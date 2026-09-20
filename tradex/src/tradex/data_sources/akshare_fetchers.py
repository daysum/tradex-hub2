"""
AKShare 数据源 fetch_fn 包装器。

所有 akshare 的数据获取函数在此注册为 SmartRouter fetch_fn。
仅本文件（及 data_sources 包内其他 fetcher 文件）允许 `import akshare`。

设计原则：
  - 每个 fetch_fn 接受 **kwargs，返回原始数据（DataFrame/dict）
  - 多 endpoint 的数据类型（如 financial_stmt）通过 endpoint 参数分派
  - fetch_fn 只负责调用 akshare API 获取原始数据，不做过滤/格式化/缓存
  - 源内的 fallback（如 spot_em → spot）在 fetch_fn 内部处理
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger("tradex.akshare")

# v3.3.15 新增：季度末日期计算。
# 旧实现用 f"{year}{q*3:02d}31" 硬编码 31 日，Q2(6月)/Q3(9月) 会算出
# 20260631 / 20260931 这类**不存在的日期**，上游直接报错，而错误又被
# except 静默吞成空表 —— 结果是每年 4–9 月「机构持仓」默认调用恒返回空。
_QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def _prev_quarter_end(today: "datetime | None" = None) -> str:
    """返回「上一季度末」的真实日期，格式 YYYYMMDD。

    以真实月末为准（6→30、9→30、12→31），不再硬编码 31 日。
    今天 2026-09-14 → 所属 Q3 → 上一季度末 = 20260630。
    """
    d = (today or datetime.now()).date()
    q = (d.month - 1) // 3 + 1          # 当前季度 1-4
    py, pq = (d.year, q - 1) if q > 1 else (d.year - 1, 4)   # 上一季度
    m = _QUARTER_END_MONTH[pq]
    last_day = calendar.monthrange(py, m)[1]                  # 真实月末，不假设 31
    return f"{py}{m:02d}{last_day:02d}"

# 待发版：`_ssl_patch_lock` 已随 `fetch_index_news_sentiment` 一并删除。
# 原因：该锁只为串行化「临时篡改进程级 requests.sessions.Session.request 的
# verify 开关」而存在 —— 这是全局副作用，锁只能缩小窗口、不能消除风险。
# 而其唯一服务对象（chinascope 指数新闻情绪）上游**已永久失效**（端点 301 跳官网首页、
# 返回 text/html，akshare 内部 r.json() 必然 JSONDecodeError），源已退役，
# 这把拿全局副作用换来的锁自然失去存在理由。


def _ak():
    """延迟导入 akshare，避免模块加载时副作用。"""
    import akshare as ak
    return ak


# ============================================================
# K 线周期归一化（v3.3.14 新增）
# ============================================================
# SmartRouter 会把同一个 period 参数**原样转发**给主源(eltdx)与备源(akshare)，
# 但两源值域不同：eltdx 认 day/week/month，akshare 认 daily/weekly/monthly。
# eltdx 源内部有 _normalize_period() 归一化，akshare 源此前没有 ——
# 于是传 'day' 时主源正常、一旦降级到 akshare 就 KeyError('day')，降级链在该值下必断。
# 这里做反向归一化，使两个源的 period 语义对齐。
_AK_PERIOD_ALIASES = {
    "day": "daily", "daily": "daily", "d": "daily", "1d": "daily",
    "week": "weekly", "weekly": "weekly", "w": "weekly", "1w": "weekly",
    "month": "monthly", "monthly": "monthly", "m": "monthly", "1m": "monthly",
}


def _normalize_ak_period(period: str) -> str:
    """把 eltdx 风格周期名(day/week/month)转成 akshare 期望的(daily/weekly/monthly)。

    已是 akshare 风格的值保持不变；未识别的值原样透传，交由 akshare 自行报错。
    """
    key = str(period or "").strip().lower()
    return _AK_PERIOD_ALIASES.get(key, period)


# ============================================================
# realtime_quote — 全量行情快照
# ============================================================

def fetch_realtime_quote(symbol: str = "", code: str = "", **kwargs):
    """A股实时行情快照（东方财富主，新浪备）。返回全量 DataFrame，含'代码'列。

    工具层负责按 symbol 过滤。
    兼容 symbol/code 两种参数名（SmartRouter 路由归一化）。
    """
    ak = _ak()
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug("stock_zh_a_spot_em failed: %s", e)
    # 源内 fallback: 新浪
    df = ak.stock_zh_a_spot()
    if df is None or df.empty:
        raise RuntimeError("akshare realtime_quote returned empty (em+sina)")
    return df


# ============================================================
# historical_kline — 历史 K 线
# ============================================================

def fetch_historical_kline(
    symbol: str = "",
    code: str = "",
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
    **kwargs,
):
    """历史 K 线（中文列名 DataFrame）。

    兼容 symbol/code 两种参数名（SmartRouter 路由归一化）。
    v3.3.14: period 先做归一化(day/week/month → daily/weekly/monthly)，
    使本备源与 eltdx 主源的周期语义对齐，避免路由原样透传时降级链断裂。

    v3.3.27：由 akshare 改为**本项目直连**，两层：
      1. 东财 push2his kline（走 `em_get`，享节流/重试/镜像/熔断）——
         与 akshare 同源同字段，复权与振幅/涨跌幅由上游原生给出
      2. **腾讯 `proxy.finance.qq.com`**（实测可用）—— 东财日K路由故障时的兜底
         （实测 2026-09-20 `push2his` 连接被拒、`push2delay` 该路径返回空 klines，
         即东财日K整体不可用；而腾讯可用）

    为什么不再用 akshare：它用裸 requests 打 `push2his`，被拒连；且**即便打通**，
    下面的字段映射问题也说明不该继续依赖它的输出（见 `_fetch_historical_kline_direct`）。

    返回列（与 akshare `stock_zh_a_hist` 完全一致）：
        日期 / 股票代码 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 振幅 / 涨跌幅 / 涨跌额 / 换手率
    """
    sym = symbol or code
    if not sym:
        raise RuntimeError("historical_kline: symbol/code is required")
    return _fetch_historical_kline_direct(
        sym, period=_normalize_ak_period(period),
        start_date=start_date, end_date=end_date, adjust=adjust,
    )


# 东财 push2his kline 的 klt / fqt 映射（与 akshare stock_zh_a_hist 一致）
_EM_KLT = {"daily": "101", "weekly": "102", "monthly": "103"}
_EM_FQT = {"qfq": "1", "hfq": "2", "": "0"}
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# f51..f61 的语义（东财 kline 返回的是逗号串，顺序由 fields2 决定）
#   f51=日期 f52=开 f53=收 f54=高 f55=低 f56=成交量(手) f57=成交额(元)
#   f58=振幅% f59=涨跌幅% f60=涨跌额 f61=换手率%
_EM_KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
# 与 akshare 完全一致的输出列顺序
_KLINE_COLUMNS = [
    "日期", "股票代码", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]
# 东财 kline 逗号串的 11 个字段对应的列名（**不含** 股票代码 ——
# 它由请求参数补出，不在响应里；别用 _KLINE_COLUMNS[:11] 代替，会错位）
_EM_KLINE_FIELD_COLUMNS = [
    "日期", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]


def _norm_date_ymd(s: str) -> str:
    """把 YYYYMMDD / YYYY-MM-DD 统一成 YYYY-MM-DD；空串原样返回。"""
    d = str(s or "").strip().replace("-", "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else ""


def _to_tencent_sym(symbol: str) -> str:
    """归一化成腾讯要的 sh600519 / sz000001 形式。"""
    s = str(symbol or "").strip().lower()
    if s[:2] in ("sh", "sz", "bj"):
        return s
    digits = s.split(".")[-1].zfill(6) if "." in s else s.zfill(6)
    if digits.startswith(("6", "9")):
        return "sh" + digits
    if digits.startswith(("4", "8")):
        return "bj" + digits
    return "sz" + digits


def _fetch_kline_from_em(sym: str, period: str, start_date: str,
                         end_date: str, adjust: str) -> pd.DataFrame:
    """东财 push2his kline（经统一防封层）。失败抛异常由上层兜底。"""
    from .em_client import em_get

    code6 = _code6(sym)
    if not code6:
        raise RuntimeError("historical_kline: symbol/code is required")
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _EM_KLINE_FIELDS2,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": _EM_KLT.get(period, "101"),
        "fqt": _EM_FQT.get(adjust, "0"),
        "secid": f"{1 if code6.startswith('6') else 0}.{code6}",
        "beg": str(start_date or "0").replace("-", ""),
        "end": str(end_date or "20500101").replace("-", ""),
    }
    resp = em_get(_EM_KLINE_URL, params=params, timeout=15)
    if resp is None:
        raise RuntimeError(f"historical_kline({code6}): 东财 kline 请求重试耗尽")
    resp.raise_for_status()
    klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError(f"historical_kline({code6}): 东财 kline 返回空")

    rows = [str(item).split(",") for item in klines]
    # 只保留字段齐全的行（f51..f61 共 11 个）
    rows = [r for r in rows if len(r) >= 11]
    if not rows:
        raise RuntimeError(f"historical_kline({code6}): 东财 kline 字段不完整")
    df = pd.DataFrame([r[:11] for r in rows], columns=_EM_KLINE_FIELD_COLUMNS)
    df["股票代码"] = code6
    df = df[_KLINE_COLUMNS]
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
    for col in _KLINE_COLUMNS[2:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _fetch_kline_from_tencent(sym: str, period: str, start_date: str,
                              end_date: str, adjust: str) -> pd.DataFrame:
    """腾讯 proxy.finance.qq.com 日/周/月 K（实测可用，走加固传输层）。

    字段布局（已用 eltdx 主源逐字段交叉验证，2026-09-18 600519）：
        [0]日期 [1]开 [2]收 [3]高 [4]低 [5]成交量(手) [6]{} [7]换手率% [8]成交额(**万元**)
    腾讯**不提供**振幅/涨跌幅/涨跌额，按前一根收盘价自行计算。
    """
    from .http_fetchers import _ensure_tencent_session, _tencent_throttle
    from astock_signals.antiban_http import get_bytes, TENCENT_MAX_RETRY

    per = {"daily": "day", "weekly": "week", "monthly": "month"}.get(period, "day")
    adj = {"qfq": "qfq", "hfq": "hfq"}.get(adjust, "")
    tsym = _to_tencent_sym(sym)
    beg = _norm_date_ymd(start_date) or "1990-01-01"
    end = _norm_date_ymd(end_date) or "2050-01-01"

    raw = get_bytes(
        _ensure_tencent_session,
        _tencent_throttle,
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
        params={"param": f"{tsym},{per},{beg},{end},640,{adj}"},
        timeout=15,
        max_retries=TENCENT_MAX_RETRY,
    )
    if raw is None:
        raise RuntimeError(f"historical_kline({sym}): 腾讯 kline 请求重试耗尽")
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    node = ((payload.get("data") or {}).get(tsym)) or {}
    bars = node.get(f"{adj}{per}") or node.get(per) or []
    if not bars:
        raise RuntimeError(f"historical_kline({sym}): 腾讯 kline 返回空")

    recs = []
    for b in bars:
        if len(b) < 9:
            continue
        try:
            recs.append({
                "日期": str(b[0]),
                "开盘": float(b[1]),
                "收盘": float(b[2]),
                "最高": float(b[3]),
                "最低": float(b[4]),
                "成交量": float(b[5]) if b[5] not in (None, "") else None,
                "换手率": float(b[7]) if b[7] not in (None, "") else None,
                # 腾讯给的是**万元**，统一换算成元（与东财/eltdx 口径一致）
                "成交额": float(b[8]) * 10000 if b[8] not in (None, "") else None,
            })
        except (TypeError, ValueError):
            continue
    if not recs:
        raise RuntimeError(f"historical_kline({sym}): 腾讯 kline 解析为空")

    df = pd.DataFrame(recs).sort_values("日期").reset_index(drop=True)
    prev_close = df["收盘"].shift(1)
    # 振幅/涨跌幅/涨跌额：腾讯不提供，按前收计算（首行无前收 → NaN）
    df["涨跌额"] = df["收盘"] - prev_close
    df["涨跌幅"] = df["涨跌额"] / prev_close * 100
    df["振幅"] = (df["最高"] - df["最低"]) / prev_close * 100
    df["股票代码"] = _code6(sym)

    if beg != "1990-01-01":
        df = df[df["日期"] >= beg]
    if end != "2050-01-01":
        df = df[df["日期"] <= end]
    if df.empty:
        raise RuntimeError(f"historical_kline({sym}): 腾讯 kline 在指定区间内为空")

    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
    return df[_KLINE_COLUMNS].reset_index(drop=True)


def _fetch_historical_kline_direct(sym: str, period: str = "daily",
                                   start_date: str = "", end_date: str = "",
                                   adjust: str = "qfq") -> pd.DataFrame:
    """历史 K 线直连：东财（原生复权/振幅）→ 腾讯（兜底）。

    ⚠️ 关于「不要退回 akshare」的说明（v3.3.27）：
    akshare 的 `stock_zh_a_hist` 有**两个**问题 ——
      ① 传输：裸 requests 打 `push2his`，被拒连（本函数第 1 层已用统一入口解决）
      ② **字段映射用的是「按位置赋列名」**（`temp_df.columns = [...]`），
         而东财 kline 返回的是逗号串，顺序**取决于 `fields2` 参数的顺序**。
         一旦上游调整字段顺序或某个字段缺失，列名会整体错位 ——
         正是本项目 v3.3.16 fund_hold 的同类事故。
    本实现同样按位置切分（逗号串没有 key 可依），但**显式校验字段个数**
    （不足 11 个的行直接丢弃），避免「少一列 → 整体错位」的静默污染。
    """
    errors = []
    for name, fn in (("东财 push2his", _fetch_kline_from_em),
                     ("腾讯", _fetch_kline_from_tencent)):
        try:
            return fn(sym, period, start_date, end_date, adjust)
        except Exception as exc:  # noqa: BLE001 - 逐层降级
            errors.append(f"{name}({exc})")
            logger.debug("historical_kline %s 失败: %s", name, exc)
    raise RuntimeError(f"historical_kline({sym}) 全部源失败: {'; '.join(errors)}")


# ============================================================
# index_daily_amount — 指数历史日K成交额（量能对比数据源）
# ============================================================

def fetch_index_daily_amount(symbol: str = "", code: str = "", days: int = 6, **kwargs):
    """指数历史日K 量能序列（供盘后复盘「量能对比」柱状图）。

    主源：东方财富 push2his（历史K线域名，直连，含 成交量+成交额）。
      返回字段 f51-f61 = [日期, 开, 收, 高, 低, 成交量(手), 成交额(元), 振幅, 涨跌幅, 涨跌额, 换手率]，
      其中 成交额 在第7位（index 6）。
    备源1：腾讯 proxy.finance.qq.com（**本项目直连，单次请求**，仅 成交量(手)，无成交额）。
    备源2：东方财富 stock_zh_index_daily_em（含 成交量+成交额；当前网络拓扑下 push2 直连常 RemoteDisconnected）。
    返回最近 days 个交易日的 [日期, 成交量(手), 成交额(元)]。
    兼容 symbol/code 两种参数名（symbol 用 sh000001 / sz399001 / sz399006 等格式）。

    v3.3.22 变更：备源1 由 akshare 的 `stock_zh_index_daily_tx` 改为本项目直连
    `http_fetchers.fetch_index_daily_tencent`。前者**按年份逐年翻页**（上证指数从
    1990 年起 → 37 次请求 ≈ 21 秒），在 SmartRouter 的 12s 超时下必然失败，
    导致该备源被误判为不可用（实测 2026-09-20 push2his 日K路由全挂时，
    本函数三层降级全失败）。直连版**单次请求 0.12s**，快约 150 倍。
    """
    ak = _ak()
    sym = symbol or code
    if not sym:
        raise RuntimeError("index symbol required (e.g. sh000001)")

    # 主源：东财 push2his 历史K线（能返回成交额(元)）
    try:
        return _fetch_index_daily_from_push2his(sym, days)
    except Exception as e_push2his:
        logger.debug("push2his index daily(%s) failed: %s", sym, e_push2his)

    # 备源1：腾讯（本项目直连，单次请求；amount 列为成交量(手)，无成交额）
    try:
        from .http_fetchers import fetch_index_daily_tencent

        return fetch_index_daily_tencent(sym, days=days)
    except Exception as e_tx:
        logger.debug("fetch_index_daily_tencent(%s) failed: %s", sym, e_tx)
        # 备源2：东方财富（含 成交量 + 成交额）
        try:
            df = ak.stock_zh_index_daily_em(symbol=sym)
            src = "em"
        except Exception as e_em:
            raise RuntimeError(
                f"index daily failed all (push2his, tx({e_tx}), em({e_em})) for {sym}"
            )

    if df is None or df.empty:
        raise RuntimeError(f"index daily empty for {sym} (src={src})")

    # 列名兼容：
    #  - 东财 stock_zh_index_daily_em：含 成交量 + 成交额 两列
    #  - 腾讯 stock_zh_index_daily_tx：仅 amount 列，且该列实为「成交量(手)」
    cols_lower = {str(c).lower(): c for c in df.columns}
    has_volume = any(k in cols_lower for k in ("volume", "vol", "成交量"))
    has_amount_name = any(k in cols_lower for k in ("amount", "amt", "成交额", "成交额(元)"))

    amt_col = None
    if has_volume and has_amount_name:
        # 东财：volume=成交量(手)，amount=成交额(元)
        vol_col = next(c for c in df.columns if str(c).lower() in ("volume", "vol", "成交量"))
        amt_col = next(c for c in df.columns if str(c).lower() in ("amount", "amt", "成交额", "成交额(元)"))
    elif has_amount_name and not has_volume:
        # 腾讯：仅 amount 列，实为成交量(手)
        vol_col = next(c for c in df.columns if str(c).lower() in ("amount", "amt", "成交额", "成交额(元)"))
    else:
        vol_col = df.columns[-1]

    df = df.tail(int(days))
    out = []
    for _, r in df.iterrows():
        vol = float(r.get(vol_col)) if vol_col and r.get(vol_col) is not None else 0.0
        amt = float(r.get(amt_col)) if amt_col and r.get(amt_col) is not None else None
        out.append({
            "date": str(r.get("date", "")),
            "volume": vol,          # 成交量(手)
            "amount": amt,         # 成交额(元)，东财源才有；腾讯源为 None
        })
    return out


def _fetch_index_daily_from_push2his(sym: str, days: int) -> list:
    """直连东财 push2his 历史K线接口取指数量能序列（含成交额）。

    返回 [{"date","volume","amount"}, ...]；成交额/成交量为 None 时表示该源未提供。

    v3.3.19：原实现自建 curl_cffi 会话（硬编码 UA/Referer，无限流无重试），
    现统一走 em_get —— 复用全局东财节流器，避免与本项目其他东财调用叠加超额。

    注：em_get 采用**函数内导入**（项目既有约定），使测试可 monkeypatch
    `em_client.em_get` 注入假响应而不触网。
    """
    from .em_client import em_get

    # symbol 格式 sh000001 / sz399001 / sz399006 → secid
    if sym.lower().startswith("sh") or sym.lower().startswith("1."):
        secid = "1." + sym[2:].zfill(6) if "." not in sym else sym
    elif sym.lower().startswith("sz") or sym.lower().startswith("0."):
        secid = "0." + sym[2:].zfill(6) if "." not in sym else sym
    else:
        # 纯数字：上证≈1.，深证≈0.
        secid = f"1.{sym.zfill(6)}" if sym.startswith(("000001", "999999")) else f"0.{sym.zfill(6)}"

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101,
        "fqt": 0,
        "end": "20500101",
        "lmt": max(int(days), 10),
    }

    resp = em_get(url, params=params, headers={"Referer": "https://quote.eastmoney.com/"},
                  timeout=10)
    if resp is None:
        raise RuntimeError(f"push2his request failed for {sym}")
    resp.raise_for_status()
    data = resp.json()
    klines = (data.get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError(f"push2his kline empty for {sym}")

    out = []
    for line in klines[-int(days):]:
        p = line.split(",")
        # f51..f61: 0日期 1开 2收 3高 4低 5成交量(手) 6成交额(元)
        vol = float(p[5]) if len(p) > 5 and p[5] not in (None, "", "-") else 0.0
        amt = float(p[6]) if len(p) > 6 and p[6] not in (None, "", "-") else None
        out.append({
            "date": p[0],
            "volume": vol,
            "amount": amt,
        })
    return out


# ============================================================
# minute_data — 分时数据
# ============================================================

def fetch_minute_data(symbol: str = "", code: str = "", **kwargs):
    """当日分时数据（东方财富分时明细）。返回原始 DataFrame。

    工具层负责列名标准化与格式化。
    兼容 symbol/code 两种参数名（SmartRouter 路由归一化）。

    v3.3.25：由 akshare 改为**本项目直连**。akshare 的 `stock_intraday_em` 用裸
    requests 打 `70.push2.eastmoney.com`（带数字前缀的子域，**无镜像可切**）
    的 `/details/sse`，该主机故障时直接失败；改用 `push2.eastmoney.com`
    （有 push2delay 镜像）的 **`/details/get` 非流式变体**（见 `_fetch_minute_data_direct`）。

    返回列：时间 / 成交价 / 手数 / 买卖盘性质（与 akshare 版本一致）。
    """
    sym = symbol or code
    if not sym:
        raise RuntimeError("minute_data: symbol/code is required")
    return _fetch_minute_data_direct(sym)


def _fetch_minute_data_direct(symbol: str) -> pd.DataFrame:
    """直连东财分时明细（走统一防封层 + 主机镜像）。

    为什么用 `push2.eastmoney.com` 而不是 akshare 的 `70.push2.eastmoney.com`：
    只有前者配了镜像，节点故障时能自动切 `push2delay`（实测 70.push2 无镜像可切）。

    ⚠️ 为什么用 `/api/qt/stock/details/get` 而不是 akshare 用的 `/details/sse`：
    `/sse` 是 **SSE 长连接**，服务端把整包塞在首个事件后**不主动关闭**，于是
      - 非流式 GET 会阻塞到超时（实测 10s 必超时）
      - 即便 `stream=True` 读到首个事件就 break，`resp.close()` 仍会去
        **排空剩余响应体**（curl 为复用连接），实测 **close() 独占 8.6s**
    而 `/details/get` 是**同一份数据的非流式变体**（实测 200 / 0.28s，载荷一致）。
    结论：**别用 SSE 变体**，用 `/get` + `em_get` 即可，快 30 倍且代码简单得多。

    Raises:
        RuntimeError: 请求重试耗尽、返回空、或解析不出明细。
    """
    # 函数级导入（项目约定：便于测试 monkeypatch em_client.em_get）
    from .em_client import em_get

    raw = str(symbol or "").strip().lower()
    code6 = _code6(raw)
    if not code6:
        raise RuntimeError("minute_data: symbol/code is required")
    # 显式前缀优先（如 sh000001 是上证指数，市场码必须为 1；
    # 只按首位数字猜会把指数判成深市 0，得到空明细）
    if raw.startswith("sh"):
        market_code = 1
    elif raw.startswith(("sz", "bj")):
        market_code = 0
    else:
        market_code = 1 if code6.startswith("6") else 0

    params = {
        "fields1": "f1,f2,f3,f4",
        "fields2": "f51,f52,f53,f54,f55",
        "mpi": "2000",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "pos": "-0",
        "secid": f"{market_code}.{code6}",
        "wbp2u": "|0|0|0|web",
    }
    resp = em_get(
        "https://push2.eastmoney.com/api/qt/stock/details/get",
        params=params,
        headers={"Referer": "https://quote.eastmoney.com/"},
        timeout=15,
    )
    if resp is None:
        raise RuntimeError(f"minute_data({code6}): 分时请求重试耗尽")
    resp.raise_for_status()

    payload = resp.json() or {}
    details = ((payload.get("data") or {}).get("details")) or []
    if not details:
        raise RuntimeError(f"minute_data({code6}): 分时明细为空")

    # details 每项形如 "09:15:06,1266.98,4,0,4"
    #   -> [时间, 成交价, 手数, -, 买卖盘性质]（与 akshare 的列语义一致）
    rows = []
    for item in details:
        parts = str(item).split(",")
        if len(parts) < 5:
            continue
        rows.append({
            "时间": parts[0],
            "成交价": pd.to_numeric(parts[1], errors="coerce"),
            "手数": pd.to_numeric(parts[2], errors="coerce"),
            "买卖盘性质": parts[4],
        })
    if not rows:
        raise RuntimeError(f"minute_data({code6}): 分时明细解析为空")

    df = pd.DataFrame(rows, columns=["时间", "成交价", "手数", "买卖盘性质"])
    df["买卖盘性质"] = df["买卖盘性质"].map({"2": "买盘", "1": "卖盘", "4": "中性盘"})
    return df


# ============================================================
# company_info — 公司信息（多 endpoint）
# ============================================================

def fetch_company_info(
    endpoint: str = "individual_info",
    symbol: str = "",
    keyword: str = "",
    industry: str = "",
    **kwargs,
):
    """公司信息（多 endpoint 分派）。

    endpoint:
      - code_name:        股票代码名称列表（搜索）
      - individual_info:  个股基本信息
      - profile:          主营业务构成
      - industry_cons:    行业成分股
    """
    ak = _ak()
    if endpoint == "code_name":
        return ak.stock_info_a_code_name()
    if endpoint == "individual_info":
        return ak.stock_individual_info_em(symbol=symbol)
    if endpoint == "profile":
        return ak.stock_zyjs_ths(symbol=symbol)
    if endpoint == "industry_cons":
        return ak.stock_board_industry_cons_em(symbol=industry)
    raise ValueError(f"Unknown company_info endpoint: {endpoint}")


# ============================================================
# financial_stmt — 财务报表（多 endpoint）
# ============================================================

def fetch_financial_stmt(
    endpoint: str = "profit",
    symbol: str = "",
    **kwargs,
):
    """财务报表（多 endpoint 分派）。

    endpoint:
      - profit:     利润表（按季度）
      - balance:    资产负债表（按报告期）
      - cashflow:   现金流量表（按季度）
      - indicator:  财务分析指标
      - segments:   主营业务构成
    """
    ak = _ak()
    if endpoint == "profit":
        return ak.stock_profit_sheet_by_quarterly_em(symbol=symbol)
    if endpoint == "balance":
        return ak.stock_balance_sheet_by_report_em(symbol=symbol)
    if endpoint == "cashflow":
        return ak.stock_cash_flow_sheet_by_quarterly_em(symbol=symbol)
    if endpoint == "indicator":
        return ak.stock_financial_analysis_indicator(symbol=symbol)
    if endpoint == "segments":
        return ak.stock_zygc_em(symbol=symbol)
    raise ValueError(f"Unknown financial_stmt endpoint: {endpoint}")


# ============================================================
# valuation — 估值与分红（多 endpoint）
# ============================================================

def fetch_valuation(
    endpoint: str = "baidu",
    symbol: str = "",
    indicator: str = "",
    **kwargs,
):
    """估值数据（多 endpoint 分派）。

    endpoint:
      - baidu:            百度股市通估值指标（PE/PB/PS/总市值）
      - dividend_detail:  分红明细（stock_history_dividend_detail）
      - dividend_cninfo:  分红明细（巨潮，stock_dividend_cninfo）
      - circulate_holder: 流通股东（stock_circulate_stock_holder）
      - rank_forecast:    分析师评级（stock_rank_forecast_cninfo）
    """
    ak = _ak()
    if endpoint == "baidu":
        return ak.stock_zh_valuation_baidu(
            symbol=symbol, indicator=indicator, period="近一年"
        )
    if endpoint == "dividend_detail":
        return ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
    if endpoint == "dividend_cninfo":
        return ak.stock_dividend_cninfo(symbol=symbol)
    if endpoint == "circulate_holder":
        return ak.stock_circulate_stock_holder(symbol=symbol)
    if endpoint == "rank_forecast":
        return ak.stock_rank_forecast_cninfo()
    raise ValueError(f"Unknown valuation endpoint: {endpoint}")


# ============================================================
# industry_data — 行业与板块（多 endpoint）
# ============================================================

# ---------------------------------------------------------------------------
# 行业板块成分股（board_industry_cons_em 直连版，v3.3.28）
#
# 背景：该 endpoint 此前**两个源都不覆盖** —— 主源 akshare 用裸 requests 打
# `29.push2.eastmoney.com/api/qt/clist/get` 被拒连；备源 ths 明确不支持。
# 导致 get_industry_stocks / get_competitors 两个工具恒失败。
#
# 实测 2026-09-20：`29.push2` ❌ / `push2.eastmoney.com` ❌ /
# **`push2delay.eastmoney.com` ✅ 200**，且经 `em_get` 可取到（走镜像）。
#
# ⚠️ 字段映射：akshare 用「按位置赋列名」，依赖**响应 key 的精确顺序** +
# `fetch_paginated_data` 里 `reset_index()` 插入的那一列（32 字段 + index = 33 列）。
# 这里改为**按字段名映射**，上游改序也不会错位。
# ---------------------------------------------------------------------------
_EM_BOARD_CONS_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_EM_BOARD_CONS_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_EM_BOARD_CONS_FIELDS = (
    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,"
    "f23,f24,f25,f22,f11,f62,f128,f136,f115,f152,f45"
)
_EM_BOARD_CONS_PAGE_SIZE = 100
_EM_BOARD_CONS_MAX_PAGES = 20      # 安全上限（约 2000 只，单行业不会超）

# 板块分类：行业板块 / 概念板块（**两类都要检索**，名字可能只在其中一类）
_BOARD_TAXONOMIES: tuple[tuple[str, str], ...] = (
    ("m:90+t:2", "行业板块"),
    ("m:90+t:3", "概念板块"),
)
# 板块「名称 → BK 代码」映射的进程内缓存（**支持断点续取**，见 _board_map_lookup）
# fs -> {"ts": 上次更新时间, "map": 名称→代码, "pages": 已取页数, "complete": 是否翻完}
_BOARD_MAP_CACHE: dict[str, dict] = {}
_BOARD_MAP_TTL = 3600.0
_BOARD_MAP_MAX_PAGES = 12          # 496 行业 / 504 概念，按 100/页 约 5~6 页

# 输出列 ← 响应字段（与 akshare stock_board_industry_cons_em 的 16 列一致）
_BOARD_CONS_COLUMNS = [
    "序号", "代码", "名称", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
    "振幅", "最高", "最低", "今开", "昨收", "换手率", "市盈率-动态", "市净率",
]
_BOARD_CONS_FIELD_MAP = {
    "代码": "f12", "名称": "f14",
    "最新价": "f2", "涨跌幅": "f3", "涨跌额": "f4",
    "成交量": "f5", "成交额": "f6", "振幅": "f7",
    "最高": "f15", "最低": "f16", "今开": "f17", "昨收": "f18",
    "换手率": "f8", "市盈率-动态": "f9", "市净率": "f23",
}
_NUMERIC_CONS_COLUMNS = [c for c in _BOARD_CONS_COLUMNS if c not in ("序号", "代码", "名称")]


def _resolve_em_board_code(symbol: str) -> str:
    """把「板块名称」解析成东财板块代码（BKxxxx）。

    已是 BK 代码则原样返回。

    ⚠️ **必须同时检索「行业板块」与「概念板块」**：
    实测 2026-09-20 —— `白酒` / `新能源车` 只存在于**概念板块**
    （BK0896 等），而 `银行` / `半导体` 在**行业板块**（BK1283 / BK1036）。
    本函数的调用方 `get_industry_stocks` 的 docstring 明确把
    「白酒 / 新能源车」列为合法输入，所以只搜行业板块会**直接找不到**
    —— akshare 原版就有这个毛病（即便它的传输正常也会失败）。
    """
    s = str(symbol or "").strip()
    if s.upper().startswith("BK") and s[2:].isdigit():
        return s.upper()
    if not s:
        raise RuntimeError("board_industry_cons_em: 板块名称/代码不能为空")

    tried: list[str] = []
    for fs, kind in _BOARD_TAXONOMIES:
        code = _board_map_lookup(fs, s)
        if code:
            return code
        st = _BOARD_MAP_CACHE.get(fs) or {}
        tried.append(f"{kind} {len(st.get('map') or {})} 个")
    raise RuntimeError(
        f"board_industry_cons_em: 未找到板块 {s!r}（已检索 {' / '.join(tried)}）"
    )


def _board_map_lookup(fs: str, name: str) -> str | None:
    """在某一类板块里查名称对应的 BK 代码；必要时继续翻页。

    ⚠️ 服务端把 `pz` **硬顶在 100**（实测传 600/1000 仍只返回 100），
    而行业板块 496 个、概念板块 504 个 —— **必须翻页**，否则只能看到前 100 个，
    「白酒」这类靠后的名字会被误判为不存在。

    翻页结果按 `fs` 缓存（**支持断点续取**）：本次没翻完就记下已取页数，
    下次接着翻；翻完则标记 complete，后续同分类查询零请求命中。
    板块名单变动很慢，TTL 默认 1 小时。
    """
    st = _BOARD_MAP_CACHE.setdefault(
        fs, {"ts": 0.0, "map": {}, "pages": 0, "complete": False})
    if time.time() - st["ts"] > _BOARD_MAP_TTL:
        st.update({"ts": 0.0, "map": {}, "pages": 0, "complete": False})

    if name in st["map"]:
        return st["map"][name]
    if st["complete"]:
        return None

    from .em_client import em_get

    page = int(st["pages"]) + 1
    while page <= _BOARD_MAP_MAX_PAGES:
        resp = em_get(_EM_BOARD_CONS_URL, params={
            "pn": str(page), "pz": "100", "po": "1", "np": "1",
            "ut": _EM_BOARD_CONS_UT, "fltt": "2", "invt": "2",
            "fs": fs, "fields": "f12,f14",
        }, timeout=15)
        if resp is None:
            raise RuntimeError(f"board_industry_cons_em: 板块列表({fs}) 请求重试耗尽")
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        diff = data.get("diff") or []
        st["pages"] = page
        if not diff:
            st["complete"] = True
            break
        for item in diff:
            nm = str(item.get("f14", "")).strip()
            if nm:
                st["map"][nm] = str(item.get("f12", ""))
        if name in st["map"]:
            break
        if len(st["map"]) >= int(data.get("total") or 0):
            st["complete"] = True
            break
        page += 1

    st["ts"] = time.time()
    return st["map"].get(name)


def _fetch_board_industry_cons_direct(symbol: str) -> pd.DataFrame:
    """直连东财行业板块成分股（走统一防封层 + 主机镜像）。

    Returns:
        DataFrame，16 列，与 akshare `stock_board_industry_cons_em` 一致：
        序号 / 代码 / 名称 / 最新价 / 涨跌幅 / 涨跌额 / 成交量 / 成交额 /
        振幅 / 最高 / 最低 / 今开 / 昨收 / 换手率 / 市盈率-动态 / 市净率
        （按涨跌幅降序，序号 1..N）

    Raises:
        RuntimeError: 板块解析失败、请求重试耗尽、或返回空。
    """
    from .em_client import em_get

    board_code = _resolve_em_board_code(symbol)

    rows: list[dict] = []
    for page in range(1, _EM_BOARD_CONS_MAX_PAGES + 1):
        resp = em_get(_EM_BOARD_CONS_URL, params={
            "pn": str(page), "pz": str(_EM_BOARD_CONS_PAGE_SIZE), "po": "1", "np": "1",
            "ut": _EM_BOARD_CONS_UT, "fltt": "2", "invt": "2",
            "fid": "f3", "fs": f"b:{board_code} f:!50",
            "fields": _EM_BOARD_CONS_FIELDS,
        }, headers={"Referer": "https://data.eastmoney.com/"}, timeout=15)
        if resp is None:
            raise RuntimeError(f"board_industry_cons_em({board_code}): 请求重试耗尽")
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        rows.extend(diff)
        total = int(data.get("total") or 0)
        if len(rows) >= total:
            break

    if not rows:
        raise RuntimeError(f"board_industry_cons_em({board_code}): 返回空成分股")

    # 按字段名取（不按位置）——上游改 key 顺序也不会错位
    recs = []
    for item in rows:
        rec = {"代码": item.get("f12"), "名称": item.get("f14")}
        for col, fld in _BOARD_CONS_FIELD_MAP.items():
            if col in ("代码", "名称"):
                continue
            rec[col] = pd.to_numeric(item.get(fld), errors="coerce")
        recs.append(rec)

    df = pd.DataFrame(recs)
    # akshare 按涨跌幅降序，序号 1..N
    df = df.sort_values("涨跌幅", ascending=False, ignore_index=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    return df[_BOARD_CONS_COLUMNS]


# ---------------------------------------------------------------------------
# 行业板块历史行情（board_industry_hist_em 直连版，v3.3.29）
#
# 背景：该 endpoint 此前**两个源都不覆盖** —— 主源 akshare 打
# `7.push2his.eastmoney.com/api/qt/stock/kline/get`（`secid=90.BKxxxx`），
# 而东财板块日K路由**整条链路故障**（实测所有 push2his* 主机连接被拒；
# `push2delay` 虽认得出板块但 `klines` 为空）；备源 ths 明确不支持。
#
# 实测**同花顺的板块K线免鉴权可用** → 作为兜底来源
# （实现放在 `ths_fetchers.fetch_ths_board_hist`，10jqka 域名的传输归它管）。
# ---------------------------------------------------------------------------
_EM_BOARD_HIST_KLT = {
    "日k": "101", "周k": "102", "月k": "103",
    "daily": "101", "weekly": "102", "monthly": "103",
}
# 与 akshare stock_board_industry_hist_em 一致的最终列顺序
_BOARD_HIST_COLUMNS = [
    "日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额",
    "成交量", "成交额", "振幅", "换手率",
]


def _fetch_board_hist_from_em(symbol: str, period: str,
                              start_date: str, end_date: str) -> pd.DataFrame:
    """东财板块日K（经统一防封层）。板块故障时抛异常由上层兜底。"""
    from .em_client import em_get

    bk = _resolve_em_board_code(symbol)
    beg = str(start_date or "").strip().replace("-", "") or "0"
    end = str(end_date or "").strip().replace("-", "") or "20500101"
    resp = em_get(_EM_KLINE_URL, params={
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _EM_KLINE_FIELDS2,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": _EM_BOARD_HIST_KLT.get(str(period or "日k").strip().lower(), "101"),
        "fqt": "1",
        "secid": f"90.{bk}",
        "beg": beg,
        "end": end,
    }, timeout=15)
    if resp is None:
        raise RuntimeError(f"board_industry_hist_em({bk}): 东财请求重试耗尽")
    resp.raise_for_status()
    klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError(f"board_industry_hist_em({bk}): 东财 kline 返回空")

    rows = [str(it).split(",") for it in klines]
    rows = [r for r in rows if len(r) >= 11]
    if not rows:
        raise RuntimeError(f"board_industry_hist_em({bk}): 东财 kline 字段不完整")
    df = pd.DataFrame([r[:11] for r in rows], columns=_EM_KLINE_FIELD_COLUMNS)
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
    for col in _EM_KLINE_FIELD_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[_BOARD_HIST_COLUMNS]


def _fetch_board_industry_hist_direct(symbol: str, period: str = "日k",
                                      start_date: str = "", end_date: str = "") -> pd.DataFrame:
    """板块历史行情：**同花顺优先** → 东财兜底。

    ⚠️ 为什么同花顺优先（而非东财优先）：
    东财的板块日K路由**整条链路故障**（所有 `push2his*` 主机连接被拒，
    `push2delay` 认得出板块但 `klines` 为空）。若把东财放前面，每次冷启动
    都要先做一遍昂贵的「板块名 → BK 代码」解析（翻 496 行业 + 504 概念）
    再失败 —— 实测首调用因此要 **24s**，超过路由默认超时。
    同花顺则 0.3s 就返回（且免鉴权、实测稳定）。
    东财层保留为兜底：待其板块日K恢复后，把顺序换回来即可拿回**换手率**列
    （同花顺板块K线不提供换手率）。

    ⚠️ 关于「不要退回 akshare」：akshare 的 `stock_board_industry_hist_em` 打
    `7.push2his`（正是故障链路），且与 `board_industry_cons_em` 同属
    「按位置赋列名」的写法。本实现东财层按**字段名**取值，
    同花顺层按同花顺的固定字段顺序（日期,开,高,低,收,量,额）解析。
    """
    errors = []
    for name, fn in (
        ("同花顺", _fetch_board_hist_from_ths),
        ("东财", _fetch_board_hist_from_em),
    ):
        try:
            return fn(symbol, period, start_date, end_date)
        except Exception as exc:  # noqa: BLE001 - 逐层降级
            errors.append(f"{name}({exc})")
            logger.debug("board_industry_hist_em %s 失败: %s", name, exc)
    raise RuntimeError(
        f"board_industry_hist_em({symbol}) 全部源失败: {'; '.join(errors)}")


def _fetch_board_hist_from_ths(symbol: str, period: str,
                               start_date: str, end_date: str) -> pd.DataFrame:
    """同花顺板块历史行情（委托给 ths_fetchers —— 10jqka 传输归它管）。"""
    from .ths_fetchers import fetch_ths_board_hist

    return fetch_ths_board_hist(symbol=symbol, period=period,
                                start_date=start_date, end_date=end_date,
                                kind="industry")


def fetch_industry_data(
    endpoint: str = "board_industry_name_em",
    industry: str = "",
    sector_type: str = "行业资金流",
    indicator: str = "今日",
    period: str = "日k",
    start_date: str = "",
    end_date: str = "",
    **kwargs,
):
    """行业板块数据（多 endpoint 分派）。

    endpoint:
      - board_industry_name_em:  行业板块列表（东方财富）
      - board_industry_name_ths: 行业板块列表（同花顺）
      - board_industry_cons_em:  行业成分股
      - board_concept_name_em:   概念板块列表（东方财富）
      - board_concept_name_ths:  概念板块列表（同花顺）
      - sector_fund_flow_rank:   板块资金流向排名
      - board_industry_hist_em:  行业板块历史行情
    """
    ak = _ak()
    if endpoint == "board_industry_name_em":
        return ak.stock_board_industry_name_em()
    if endpoint == "board_industry_name_ths":
        return ak.stock_board_industry_name_ths()
    if endpoint == "board_industry_cons_em":
        # v3.3.28：由 akshare 改为本项目直连（东财 clist + em_get 走镜像）。
        # akshare 打 `29.push2` 被拒连，且备源 ths 不支持该 endpoint
        # —— 此前两个源都不覆盖，get_industry_stocks / get_competitors 恒失败。
        return _fetch_board_industry_cons_direct(industry)
    if endpoint == "board_concept_name_em":
        return ak.stock_board_concept_name_em()
    if endpoint == "board_concept_name_ths":
        return ak.stock_board_concept_name_ths()
    if endpoint == "sector_fund_flow_rank":
        # 东财 push2 接口 —— 统一走 em_get（指纹伪装 + 全局节流 + 退避重试）
        from .em_client import em_get

        _st_map = {"行业资金流": "2", "概念资金流": "3", "地域资金流": "1"}
        _ind_map = {"今日": "0", "5日": "5", "10日": "10"}
        _fs = f"m:90+t:{_st_map.get(sector_type, '2')}"
        _url = "https://push2.eastmoney.com/api/qt/clist/get"
        _params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fltt": "2", "invt": "2",
            "fid0": "f62",
            "fs": _fs,
            "stat": "1",
            "fields": (
                "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
                "f204,f205,f124"
            ),
            "rt": "52975239",
        }
        resp = em_get(_url, params=_params,
                      headers={"Referer": "https://data.eastmoney.com/"}, timeout=15)
        if resp is None:
            raise RuntimeError("sector_fund_flow_rank: 东财请求重试耗尽")
        resp.raise_for_status()
        _data = resp.json()
        _items = _data.get("data", {}).get("diff", [])
        _rows = []
        for item in _items:
            _rows.append({
                "板块": item.get("f14", ""),
                "代码": item.get("f12", ""),
                "最新价": item.get("f2", 0),
                "涨跌幅": item.get("f3", 0),
                "主力净流入": item.get("f62", 0),
                "主力净流入-占比": item.get("f184", 0),
                "超大单净流入": item.get("f66", 0),
                "超大单净流入-占比": item.get("f69", 0),
                "大单净流入": item.get("f72", 0),
                "大单净流入-占比": item.get("f75", 0),
                "中单净流入": item.get("f78", 0),
                "中单净流入-占比": item.get("f81", 0),
                "小单净流入": item.get("f84", 0),
                "小单净流入-占比": item.get("f87", 0),
                "主力净流入排名": item.get("f204", 0),
                "涨跌股数比": item.get("f205", 0),
            })
        if not _rows:
            return pd.DataFrame()
        return pd.DataFrame(_rows)
    if endpoint == "board_industry_hist_em":
        # v3.3.29：由 akshare 改为本项目直连（东财 → **同花顺兜底**）。
        # 东财板块日K路由整条链路故障，同花顺板块K线免鉴权可用。
        return _fetch_board_industry_hist_direct(
            industry, period=period, start_date=start_date, end_date=end_date)
    raise ValueError(f"Unknown industry_data endpoint: {endpoint}")


# ============================================================
# market_overview — 指数行情
# ============================================================

def fetch_market_overview(symbol: str = "", **kwargs):
    """主要指数实时行情。返回 DataFrame（含 最新价/涨跌幅/成交量/成交额 等）。

    主源：新浪/腾讯 stock_zh_index_spot_sina（直连可达，成交额单位=元，数据正确）。
    备源：东方财富 stock_zh_index_spot_em（当前网络拓扑下 push2 直连常 RemoteDisconnected，
    失败时自动降级新浪源）。
    注意：旧版 akshare 的 stock_zh_index_spot 已改名 stock_zh_index_spot_sina，直接调用会
    AttributeError，故主源改用 _sina 后缀接口。
    """
    ak = _ak()
    if symbol:
        # 指定系列指数时走东财（带 symbol 参数过滤）
        try:
            return ak.stock_zh_index_spot_em(symbol=symbol)
        except Exception as e_em:
            logger.debug("stock_zh_index_spot_em(%s) failed: %s", symbol, e_em)
            return ak.stock_zh_index_spot_sina()
    try:
        df = ak.stock_zh_index_spot_sina()
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug("stock_zh_index_spot_sina failed: %s", e)
    # 备源：东方财富
    return ak.stock_zh_index_spot_em()


# ============================================================
# news_data — 新闻与公告（多 endpoint）
# ============================================================

def fetch_news_data(
    endpoint: str = "stock_news_em",
    symbol: str = "",
    date: str = "",
    **kwargs,
):
    """新闻数据（多 endpoint 分派）。

    endpoint:
      - stock_news_em:       个股新闻
      - stock_report_disclosure: 财报披露时间表
      - stock_notice_report: 公告
      - stock_news_main_cx:  财新网新闻
      - news_cctv:           CCTV 新闻
    """
    ak = _ak()
    if endpoint == "stock_news_em":
        return ak.stock_news_em(symbol=symbol)
    if endpoint == "stock_report_disclosure":
        # 尝试传递 period 参数（akshare 可能按报告期过滤）
        if date:
            return ak.stock_report_disclosure(period=date)
        return ak.stock_report_disclosure()
    if endpoint == "stock_notice_report":
        kw = {}
        if symbol:
            kw["symbol"] = symbol
        return ak.stock_notice_report(**kw)
    if endpoint == "stock_news_main_cx":
        return ak.stock_news_main_cx()
    if endpoint == "news_cctv":
        return ak.news_cctv(date=date)
    raise ValueError(f"Unknown news_data endpoint: {endpoint}")


# ============================================================
# macro_data — 宏观与外汇（多 endpoint）
# ============================================================

def fetch_macro_data(
    endpoint: str = "gdp",
    symbol: str = "",
    date: str = "",
    **kwargs,
):
    """宏观数据（多 endpoint 分派）。

    endpoint:
      - gdp / cpi / pmi / money_supply: 宏观指标
      - fx_spot:                        外汇汇率
      - bond_yield:                     国债收益率
      - margin_em:                      融资融券汇总
      - margin_sse:                     上交所融资融券明细
      - margin_szse:                    深交所融资融券明细
      - inner_trade:                    内部交易（股东增减持）
    """
    ak = _ak()
    if endpoint == "gdp":
        return ak.macro_china_gdp()
    if endpoint == "cpi":
        return ak.macro_china_cpi()
    if endpoint == "pmi":
        return ak.macro_china_pmi()
    if endpoint == "money_supply":
        return ak.macro_china_money_supply()
    if endpoint == "fx_spot":
        return ak.fx_spot_quote()
    if endpoint == "bond_yield":
        return ak.bond_china_yield(start_date="", end_date="")
    if endpoint == "margin_em":
        return ak.stock_margin_em()
    if endpoint == "margin_sse":
        kw: dict = {}
        if date:
            kw["date"] = date
        return ak.stock_margin_detail_sse(**kw)
    if endpoint == "margin_szse":
        kw = {}
        if date:
            kw["date"] = date
        return ak.stock_margin_detail_szse(**kw)
    if endpoint == "inner_trade":
        return ak.stock_inner_trade_xq()
    raise ValueError(f"Unknown macro_data endpoint: {endpoint}")


# ============================================================
# fund_flow — 个股资金流向（akshare 备源）
# ============================================================

def fetch_fund_flow(code: str = "", curr_date: str = "", include_history: bool = True, **kwargs) -> dict:
    """个股资金流向（直连东财 push2his API）。

    返回与 em_push2 源相同的 dict 结构（realtime/history/signal）。

    v3.3.19：统一走 em_get（指纹伪装 + 全局节流 + 退避重试），
    不再自建会话绕过限流。
    """
    from .em_client import em_get

    if not curr_date:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    result: dict = {
        "symbol": code,
        "source": "AKShare stock_individual_fund_flow",
        "date": curr_date,
        "realtime": [],
        "history": [],
        "signal": "neutral",
    }

    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": secid,
        "lmt": 20,
        "klt": 101,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }

    try:
        resp = em_get(url, params=params,
                      headers={"Referer": "https://data.eastmoney.com/"}, timeout=10)
        if resp is None:
            raise RuntimeError("资金流数据请求失败")
        resp.raise_for_status()
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            raise RuntimeError("资金流数据为空")
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                result["history"].append({
                    "date": parts[0],
                    "main_net": float(parts[1]),
                    "small": float(parts[2]),
                    "mid": float(parts[3]),
                    "large": float(parts[4]),
                    "super_large": float(parts[5]),
                })
        if result["history"]:
            last = result["history"][-1]
            if last["main_net"] > 0:
                result["signal"] = "bullish_inflow"
            elif last["main_net"] < 0:
                result["signal"] = "bearish_outflow"
        return result
    except Exception as e:
        raise RuntimeError(f"AKShare 资金流数据获取失败: {e}")


# ============================================================
# dragon_tiger — 龙虎榜（akshare 备源）
# ============================================================

def fetch_dragon_tiger(code: str = "", trade_date: str = "", look_back_days: int = 30, **kwargs):
    """龙虎榜（akshare stock_lhb_detail_em）。

    code 非空: 返回与 em_datacenter 源相同的 dict 结构（单股上榜记录）。
    code 为空: 返回原始 DataFrame（全市场龙虎榜明细）。
    """
    ak = _ak()
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days)
    df = ak.stock_lhb_detail_em(
        start_date=start_dt.strftime("%Y%m%d"),
        end_date=end_dt.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        raise RuntimeError("AKShare 龙虎榜数据为空")
    if not code:
        # 全市场龙虎榜明细：返回原始 DataFrame
        return df
    code_col = "代码" if "代码" in df.columns else df.columns[1]
    filtered = df[df[code_col].astype(str).str.zfill(6) == code]
    if filtered.empty:
        raise RuntimeError(f"AKShare 龙虎榜无 {code} 上榜记录")
    result: dict = {
        "symbol": code,
        "source": "AKShare stock_lhb_detail_em",
        "trade_date": trade_date,
        "look_back_days": look_back_days,
        "appearances": [],
        "latest_seats": {"buy": [], "sell": []},
        "institutional": None,
    }
    for _, row in filtered.iterrows():
        result["appearances"].append({
            "date": str(row.get("上榜日", "")),
            "reason": str(row.get("解读", "")),
            "net_buy_wan": round(float(row.get("龙虎榜净买额", 0) or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("换手率", 0) or 0), 2),
        })
    return result


# ============================================================
# industry_comparison — 行业对比（akshare 备源）
# ============================================================

def fetch_industry_comparison(code: str = "", trade_date: str = "", top_n: int = 20, **kwargs) -> dict:
    """行业对比（东财行业资金流排行）。返回与 em_push2 源相同的 dict 结构。

    v3.3.26：由 akshare 改为**本项目直连**。akshare 的 `stock_sector_fund_flow_rank`
    用裸 requests 打 `push2.eastmoney.com/api/qt/clist/get` —— 正是本项目
    统一入口**已覆盖且已配镜像**的路径，经 `em_get` 可正常取到（走 push2delay），
    而 akshare 绕过防封层故被拒连。
    """
    return _fetch_industry_comparison_direct(code=code, trade_date=trade_date, top_n=top_n)


# 行业资金流排行的请求参数（对齐 akshare stock_sector_fund_flow_rank 的「今日/行业资金流」）
_EM_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_EM_SECTOR_FUNDFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
# f62=主力净流入（排序键）；f104/f105=上涨/下跌家数；f204=主力净流入最大股
_EM_SECTOR_FUNDFLOW_FIELDS = (
    "f12,f14,f2,f3,f62,f184,f204,f205,f124,f104,f105"
)
_EM_SECTOR_PAGE_SIZE = 100


def _fetch_industry_comparison_direct(code: str = "", trade_date: str = "",
                                      top_n: int = 20) -> dict:
    """直连东财行业资金流排行（走统一防封层 + 主机镜像）。

    与 akshare 版本的差异（**有意为之**）：akshare 的字段集不含 f104/f105，
    故它写死 `up_count/down_count = 0`；本实现在 fields 里补上这两项，
    返回**真实的上涨/下跌家数** —— 更贴近本函数「与 em_push2 源相同的 dict 结构」
    的既定意图（em_push2 源就是从 f104/f105 取的）。

    分页策略：结果按主力净流入降序（`fid0=f62`），故只需取够 top_n 的页数，
    不像 akshare 那样把全部分页都拉完。

    Raises:
        RuntimeError: 请求重试耗尽或返回空。
    """
    # 函数级导入（项目约定：便于测试 monkeypatch em_client.em_get）
    from .em_client import em_get

    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    result: dict = {
        "source": "东财 push2 行业资金流（直连）",
        "date": trade_date,
        "code": code or None,
        "industries": [],
    }

    want = max(int(top_n or 1), 1)
    # 向上取整，避免引入 math（本模块未在顶层 import math）
    pages = max(1, (want + _EM_SECTOR_PAGE_SIZE - 1) // _EM_SECTOR_PAGE_SIZE)
    rows: list[dict] = []
    for page in range(1, pages + 1):
        params = {
            "pn": str(page),
            "pz": str(_EM_SECTOR_PAGE_SIZE),
            "po": "1",              # 降序
            "np": "1",
            "ut": _EM_SECTOR_FUNDFLOW_UT,
            "fltt": "2",
            "invt": "2",
            "fid0": "f62",          # 按主力净流入排序
            "fs": "m:90 t:2",       # 行业板块
            "stat": "1",
            "fields": _EM_SECTOR_FUNDFLOW_FIELDS,
            "rt": "52975239",
        }
        resp = em_get(_EM_CLIST_URL, params=params,
                      headers={"Referer": "https://data.eastmoney.com/"}, timeout=15)
        if resp is None:
            raise RuntimeError("industry_comparison: 东财行业资金流请求重试耗尽")
        resp.raise_for_status()
        diff = ((resp.json() or {}).get("data") or {}).get("diff") or []
        if not diff:
            break
        rows.extend(diff)
        if len(rows) >= want:
            break

    if not rows:
        raise RuntimeError("industry_comparison: 东财行业资金流返回空")

    for i, item in enumerate(rows[:want]):
        result["industries"].append({
            "rank": i + 1,
            "name": item.get("f14", ""),
            "change_pct": item.get("f3", 0),
            "up_count": item.get("f104", 0) or 0,
            "down_count": item.get("f105", 0) or 0,
            "leader": item.get("f204", ""),
        })
    return result


# ============================================================
# northbound — 北向资金（akshare 备源）
# ============================================================

def fetch_northbound(curr_date: str = "", include_history: bool = False, **kwargs):
    """北向资金（akshare stock_hsgt_hist_em）。返回 DataFrame。"""
    ak = _ak()
    df = ak.stock_hsgt_hist_em(symbol="北向资金")
    if df is None or df.empty:
        raise RuntimeError("AKShare 北向资金数据为空")
    return df


# ============================================================
# hot_stocks — 涨跌停股票池
# ============================================================

def fetch_hot_stocks(direction: str = "涨停", date: str = "", **kwargs):
    """涨跌停股票池（akshare）。

    direction: "涨停" → stock_zt_pool_em; "跌停" → stock_zt_pool_dtgc_em
    返回 DataFrame。
    """
    ak = _ak()
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    if direction == "涨停":
        return ak.stock_zt_pool_em(date=date)
    return ak.stock_zt_pool_dtgc_em(date=date)


# ============================================================
# profit_forecast — 一致预期（同花顺 HTTP 抓取）
# ============================================================

def fetch_profit_forecast(symbol: str = "", **kwargs) -> dict:
    """分析师一致预期 EPS（同花顺 basic.10jqka 抓取 + 腾讯实时价格）。

    返回包含 forecasts/price/forward_pe/peg 等字段的 dict。
    抓取逻辑从 signal_data_base.get_profit_forecast 迁移而来。
    """
    import math
    import re

    from astock_signals.antiban_http import get_client

    code = symbol
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    _headers = {"Referer": "https://basic.10jqka.com.cn/"}
    # v3.3.20：由裸 requests 收敛到统一防封层。
    # 原实现 UA 是「残缺的 Mac UA」（无 Chrome/Safari 版本号）—— 这正是
    # 服务端识别伪造客户端的典型特征，且该接口零节流。
    resp = get_client("ths").get(url, headers=_headers, timeout=15)
    if resp is None:
        raise RuntimeError(f"{symbol} 同花顺一致预期请求重试耗尽")
    html = resp.content.decode("gbk", errors="replace")

    thead_pat = re.compile(
        r"<thead[^>]*>\s*<tr>\s*<th>\s*年度\s*</th>\s*"
        r"<th>\s*预测机构数\s*</th>.*?</thead>",
        re.DOTALL,
    )
    thead_m = thead_pat.search(html)
    if not thead_m:
        raise RuntimeError(f"{symbol} 无分析师一致预期数据（找不到EPS预测表头）")

    tbody_pat = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.DOTALL)
    tbody_m = tbody_pat.search(html, thead_m.end())
    if not tbody_m:
        raise RuntimeError(f"{symbol} 无分析师一致预期数据（找不到EPS预测表体）")

    tbody_html = tbody_m.group(1)
    row_pat = re.compile(
        r"<tr[^>]*>\s*<th[^>]*>\s*(\d{4})\s*</th>\s*"
        r"<td[^>]*>\s*(\d+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.]+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.]+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.]+)\s*</td>\s*"
        r"<td[^>]*>\s*([\d.]+)\s*</td>"
        r".*?</tr>",
        re.DOTALL,
    )

    eps_by_year: dict[str, float] = {}
    forecast_rows: list[dict] = []
    for rm in row_pat.finditer(tbody_html):
        fy = rm.group(1)
        analysts = int(rm.group(2))
        eps_min = rm.group(3)
        eps_mean = float(rm.group(4))
        eps_max = rm.group(5)
        industry_avg = rm.group(6)
        entry = {
            "year": fy,
            "analysts": analysts,
            "eps_min": eps_min,
            "eps_mean": eps_mean,
            "eps_max": eps_max,
            "industry_average": industry_avg,
            "low_coverage_warning": analysts < 3,
        }
        forecast_rows.append(entry)
        if analysts > 0:
            eps_by_year[fy] = eps_mean

    if not forecast_rows:
        raise RuntimeError(f"{symbol} 无分析师一致预期数据")

    tip_pat = re.compile(r'<p[^>]*class="tip[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
    tip_m = tip_pat.search(html, max(0, thead_m.start() - 2000), thead_m.start())
    summary_text = ""
    if tip_m:
        summary_text = re.sub(r"<[^>]+>", "", tip_m.group(1)).strip()

    result: dict = {
        "symbol": symbol,
        "source": "同花顺 analyst consensus",
        "summary": summary_text,
        "forecasts": forecast_rows,
    }

    try:
        # v3.3.20：腾讯行情由裸 urllib（UA=Mozilla/5.0、零节流）收敛到
        # http_fetchers 的腾讯传输层（指纹伪装 + 独立节流器 + 退避重试）。
        # 函数级导入：保持 http_fetchers._tencent_quote_vals 可被 monkeypatch 替换。
        from .http_fetchers import _tencent_quote_vals

        vals = _tencent_quote_vals(code)
        if len(vals) >= 53:
            price = float(vals[3]) if vals[3] else 0
            pe_ttm = float(vals[39]) if vals[39] else 0
            result["price"] = price
            result["pe_ttm"] = pe_ttm
            years_sorted = sorted(eps_by_year.keys())
            if years_sorted and eps_by_year.get(years_sorted[0], 0) > 0:
                eps_cur = eps_by_year[years_sorted[0]]
                fwd_pe = round(price / eps_cur, 1)
                result["forward_pe"] = fwd_pe
                result["forward_pe_year"] = years_sorted[0]
                if len(years_sorted) >= 2 and eps_by_year.get(years_sorted[1], 0) > 0:
                    eps_next = eps_by_year[years_sorted[1]]
                    cagr = eps_next / eps_cur - 1
                    if cagr > 0:
                        peg = round(fwd_pe / (cagr * 100), 2)
                        result["peg"] = peg
                        result["eps_cagr"] = round(cagr * 100, 1)
                        if fwd_pe > 30:
                            digest = round(
                                math.log(fwd_pe / 30) / math.log(1 + cagr), 1
                            )
                            result["pe_digestion_years"] = digest
                    else:
                        result["peg"] = None
                        result["eps_cagr"] = round(cagr * 100, 1)
                        result["peg_note"] = "EPS declining, PEG not applicable"
    except Exception as e:
        result["valuation_note"] = f"Forward valuation unavailable: {e}"

    return result


# ============================================================
# baidu_economic_calendar — 百度经济数据日历
# ============================================================

def fetch_baidu_economic_calendar(date: str = "", **kwargs) -> pd.DataFrame:
    """百度股市通经济数据日历。

    调用 ak.news_economic_baidu(date=date) 获取每日经济数据发布日程。

    Args:
        date: 查询日期，格式 "YYYYMMDD"。默认为当日

    Returns:
        DataFrame with columns: 日期, 时间, 事件, 重要性, 前值, 预期, 公布值, 地区, 国家, 统计周期
    """
    ak = _ak()
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    try:
        df = ak.news_economic_baidu(date=date)
        if df is None or df.empty:
            logger.debug("fetch_baidu_economic_calendar(%s): empty", date)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_baidu_economic_calendar(%s) failed: %s", date, e)
        # v3.3.15：不吞异常。吞掉会让 SmartRouter 把失败当成功（不降级、不计健康度），
        # 调用方也分不清「当天确实无数据」与「上游挂了」。
        raise


# ============================================================
# baidu_trade_notify — 百度交易提醒（多 endpoint）
# ============================================================

def fetch_baidu_trade_notify(endpoint: str = "suspend", date: str = "", **kwargs) -> pd.DataFrame:
    """百度股市通交易提醒（多 endpoint 分派）。

    endpoint:
      - suspend:     停复牌提醒（ak.news_trade_notify_suspend_baidu）
      - dividend:    分红派息提醒（ak.news_trade_notify_dividend_baidu）
      - report_time: 财报发行时间（ak.news_report_time_baidu）

    Args:
        endpoint: 提醒类型
        date: 查询日期，格式 "YYYYMMDD"

    Returns:
        DataFrame
    """
    ak = _ak()
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    try:
        if endpoint == "suspend":
            df = ak.news_trade_notify_suspend_baidu(date=date)
        elif endpoint == "dividend":
            df = ak.news_trade_notify_dividend_baidu(date=date)
        elif endpoint == "report_time":
            df = ak.news_report_time_baidu(date=date)
        else:
            raise ValueError(f"Unknown baidu_trade_notify endpoint: {endpoint}")
        if df is None or df.empty:
            logger.debug("fetch_baidu_trade_notify(%s, %s): empty", endpoint, date)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_baidu_trade_notify(%s, %s) failed: %s", endpoint, date, e)
        # v3.3.15：不吞异常，交由 SmartRouter 降级并计健康度
        raise


# ============================================================
# index_news_sentiment — 指数新闻情绪（主源已退役，见下）
# ============================================================
# 【已退役】原 `fetch_index_news_sentiment`（走 ak.index_news_sentiment_scope →
# www.chinascope.com/inews/senti/index）**上游永久失效，函数已删除**。
#
# 实测铁证（非猜测，可复现）：
#   curl -kL "https://www.chinascope.com/inews/senti/index?period=YEAR"
#     → 301 跳转到 https://www.chinascope.com.cn/ （公司官网首页）
#     → HTTP 200 + content_type: text/html（22,833 字节的官网 HTML）
#   ⇒ 该 JSON API 端点已被数库科技撤下/搬迁；akshare 内部 r.json() 必然
#     JSONDecodeError。与 SSL 无关（-k 绕过证书照样拿到 HTML）。
#
# 为什么删而不是留着当"备胎"：它的上游是死的，留着只会有害 ——
#   ① 每次调用先白失败一次（浪费一次 HTTP 往返 + 超时窗口）；
#   ② 把该源健康分压低，污染健康度看板；
#   ③ 为了它必须保留「在锁里篡改进程级 requests.sessions.Session.request」
#      这种全局副作用 hack（见本文件顶部已删的 _ssl_patch_lock 说明），
#      用一个全局副作用去维持一个永远失败的源，收益为负。
#
# 现装配：主源 legu_activity（乐咕乐股赚钱效应，实测 12 项），
#         备源 ths_distribution（同花顺涨跌分布，实测 11 行，跨上游）。
# 详见 registry.py 的 index_news_sentiment 注册块。


# ============================================================
# futures_news — 期货新闻（上海有色网）
# ============================================================

def fetch_futures_news(symbol: str = "全部", **kwargs) -> pd.DataFrame:
    """期货新闻（上海有色网）。

    调用 ak.futures_news_shmet(symbol=symbol) 获取期货/大宗商品相关新闻。

    Args:
        symbol: 品种，默认 "全部"

    Returns:
        DataFrame with columns: 标题, 发布时间, 内容
    """
    ak = _ak()
    try:
        df = ak.futures_news_shmet(symbol=symbol)
        if df is None or df.empty:
            logger.debug("fetch_futures_news(%s): empty", symbol)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_futures_news(%s) failed: %s", symbol, e)
        # v3.3.15：不吞异常，交由 SmartRouter 降级并计健康度
        raise


# ============================================================
# hot_search_baidu — 百度热搜股票
# ============================================================

def fetch_hot_search_baidu(symbol: str = "A股", date: str = "", time: str = "今日", **kwargs) -> pd.DataFrame:
    """百度股市通热搜股票。

    调用 ak.stock_hot_search_baidu(symbol=symbol, date=date, time=time) 获取热搜排行。

    Args:
        symbol: {"全部", "A股", "港股", "美股"}
        date: 日期，格式 "YYYYMMDD"
        time: {"今日", "1小时"}

    Returns:
        DataFrame with columns: 股票代码, 股票名称, 热搜排名, 热度值等
    """
    ak = _ak()
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    try:
        df = ak.stock_hot_search_baidu(symbol=symbol, date=date, time=time)
        if df is None or df.empty:
            logger.debug("fetch_hot_search_baidu(%s): empty", symbol)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_hot_search_baidu(%s) failed: %s", symbol, e)
        # v3.3.15：不吞异常（百度该接口常间歇性返回 KeyError 'list'，吞掉会伪装成"无热搜"）
        raise


# ============================================================
# hot_rank_data — 东方财富人气榜（多 endpoint）
# ============================================================

# ============================================================
# hot_rank — 东财人气榜（直连版）
#
# v3.3.24：`rank` / `up` 两个 endpoint 由 akshare 改为**本项目直连**。
#
# 为什么要自己实现：akshare 的 `stock_hot_rank_em` / `stock_hot_up_em` 先用
# 裸 requests POST `emappdata` 取榜单，**再用裸 requests GET
# `push2.eastmoney.com/api/qt/ulist.np/get`** 取行情 —— 后者在 push2 节点
# 故障时被直接拒连（实测 2026-09-20 `RemoteDisconnected`），
# 而**同一个 URL 经 `em_get` 可正常取到**（自动走 push2delay 镜像）。
# 也就是说这两个榜单本可用，只是 akshare 绕过了防封层。
#
# 其余 5 个 endpoint（detail/realtime/keyword/latest/relate）只打 emappdata
# （实测可达），仍走 akshare。
# ============================================================
_EMAPPDATA_STOCKRANK = "https://emappdata.eastmoney.com/stockrank"
_EM_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EM_HOT_RANK_UT = "f057cbcbce2a86e2866ab8877db1d059"

# endpoint -> (emappdata 路径, 排名变动字段名；无则 None)
_HOT_RANK_DIRECT: dict[str, tuple[str, str | None]] = {
    "rank": ("getAllCurrentList", None),          # 人气榜
    "up": ("getAllHisRcList", "hrc"),             # 飙升榜（含「排名较昨日变动」）
}

# 行情字段（ulist.np 返回）：f2=最新价、f3=涨跌幅、f12=代码、f14=股票名称。
# ⚠️ 注意上游返回的 key 顺序与 `fields` 参数顺序**不一致**（实测请求
# `fields=f14,f3,f12,f2`，实际 key 顺序是 `f2,f3,f12,f14`）。
# akshare 用「按位置赋列名」恰好蒙对了；本项目有过列错位事故（v3.3.16 fund_hold），
# 故这里一律**按字段名取**，并按代码 join 两个列表。


def _fetch_hot_rank_direct(kind: str) -> pd.DataFrame:
    """直连东财人气榜 / 飙升榜（emappdata + push2 ulist.np，均走统一防封层）。

    Returns:
        DataFrame，列同 akshare 版本：
          rank → 当前排名 / 代码 / 股票名称 / 最新价 / 涨跌额 / 涨跌幅
          up   → 排名较昨日变动 / 当前排名 / 代码 / 股票名称 / 最新价 / 涨跌额 / 涨跌幅

    Raises:
        RuntimeError: 任一步请求重试耗尽或返回空。
    """
    # 函数级导入（项目约定：便于测试 monkeypatch em_client.em_get/em_post）
    from .em_client import em_get, em_post

    path, change_field = _HOT_RANK_DIRECT[kind]

    payload = {
        "appId": "appId01",
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "",
        "pageNo": 1,
        "pageSize": 100,
    }
    resp = em_post(f"{_EMAPPDATA_STOCKRANK}/{path}", json=payload, timeout=15)
    if resp is None:
        raise RuntimeError(f"hot_rank({kind}): emappdata 请求重试耗尽")
    resp.raise_for_status()
    rows = (resp.json() or {}).get("data") or []
    if not rows:
        raise RuntimeError(f"hot_rank({kind}): emappdata 返回空榜单")

    # SZ000665 -> 0.000665；SH600519 -> 1.600519
    marks = [("0." if "SZ" in str(r.get("sc", "")) else "1.") + str(r.get("sc", ""))[2:]
             for r in rows]
    params = {
        "ut": _EM_HOT_RANK_UT,
        "fltt": "2",
        "invt": "2",
        "fields": "f14,f3,f12,f2",
        "secids": ",".join(marks),
    }
    resp2 = em_get(_EM_ULIST_URL, params=params, timeout=15)
    if resp2 is None:
        raise RuntimeError(f"hot_rank({kind}): push2 ulist 请求重试耗尽")
    resp2.raise_for_status()
    diff = ((resp2.json() or {}).get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError(f"hot_rank({kind}): push2 ulist 返回空行情")

    # ⚠️ **按代码 join，不按位置对齐**。
    # 榜单（emappdata）与行情（ulist）是两个独立请求，条数或顺序都可能不一致
    # （镜像 push2delay 对停牌股可能不返回行情）。若按位置拼，代码与名称会
    # **静默错位** —— 正是本项目 v3.3.16 fund_hold 列错位的同类事故。
    quote_by_code: dict[str, dict] = {}
    for item in diff:
        c = str(item.get("f12") or "").strip()
        if c:
            quote_by_code[c] = item

    out_rows: list[dict] = []
    for r in rows:                                  # 以榜单顺序为主，保持排名有序
        sc = str(r.get("sc") or "")
        code6 = sc[2:] if len(sc) > 2 else sc       # SZ002185 -> 002185
        q = quote_by_code.get(code6, {})
        price = pd.to_numeric(q.get("f2"), errors="coerce")
        pct = pd.to_numeric(q.get("f3"), errors="coerce")
        rec = {
            "当前排名": r.get("rk"),
            "代码": sc,                              # 与 akshare 一致：用 sc（如 SZ002185）
            "股票名称": q.get("f14"),                # 缺失则为 None（不猜、不错位）
            "最新价": price,
            "涨跌额": price * pct / 100 if pd.notna(price) and pd.notna(pct) else None,
            "涨跌幅": pct,
        }
        if change_field:
            rec["排名较昨日变动"] = r.get(change_field)
        out_rows.append(rec)

    cols = ["当前排名", "代码", "股票名称", "最新价", "涨跌额", "涨跌幅"]
    if change_field:
        cols = ["排名较昨日变动", *cols]
    df = pd.DataFrame(out_rows, columns=cols)

    df["当前排名"] = pd.to_numeric(df["当前排名"], errors="coerce")
    if change_field:
        df["排名较昨日变动"] = pd.to_numeric(df["排名较昨日变动"], errors="coerce")
    return df


def fetch_hot_rank_data(endpoint: str = "rank", symbol: str = "", **kwargs) -> pd.DataFrame:
    """东方财富个股人气榜（多 endpoint 分派）。

    endpoint:
      - rank:     全市场人气榜（**本项目直连**，v3.3.24）
      - up:       飙升榜（**本项目直连**，v3.3.24）
      - detail:   个股历史趋势及粉丝特征（ak.stock_hot_rank_detail_em）
      - realtime: 个股实时变动（ak.stock_hot_rank_detail_realtime_em）
      - keyword:  个股热门关键词（ak.stock_hot_keyword_em）
      - latest:   个股最新排名（ak.stock_hot_rank_latest_em）
      - relate:   相关股票（ak.stock_hot_rank_relate_em）

    Args:
        endpoint: 榜单类型
        symbol: 带市场表示的证券代码，如 "SZ000665"

    Returns:
        DataFrame

    v3.3.24：`rank` / `up` 改走本项目直连（见 `_fetch_hot_rank_direct`）。
    不再回退 akshare —— 它同样依赖 push2 且用裸 requests，只会多花时间。
    """
    if endpoint in _HOT_RANK_DIRECT:
        return _fetch_hot_rank_direct(endpoint)

    ak = _ak()
    try:
        if endpoint == "detail":
            df = ak.stock_hot_rank_detail_em(symbol=symbol)
        elif endpoint == "realtime":
            df = ak.stock_hot_rank_detail_realtime_em(symbol=symbol)
        elif endpoint == "keyword":
            df = ak.stock_hot_keyword_em(symbol=symbol)
        elif endpoint == "latest":
            df = ak.stock_hot_rank_latest_em(symbol=symbol)
        elif endpoint == "relate":
            df = ak.stock_hot_rank_relate_em(symbol=symbol)
        else:
            raise ValueError(f"Unknown hot_rank_data endpoint: {endpoint}")
        if df is None or df.empty:
            logger.debug("fetch_hot_rank_data(%s): empty", endpoint)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_hot_rank_data(%s) failed: %s", endpoint, e)
        # v3.3.15：不吞异常。东财人气榜走 push2 族，本机会间歇性被 RST，
        # 旧实现把它伪装成"人气榜为空"，既不让位给备源也让健康度虚高。
        raise


# ============================================================
# xueqiu_hot — 雪球热度（多 endpoint）
# ============================================================

def fetch_xueqiu_hot(endpoint: str = "follow", symbol: str = "最热门", **kwargs) -> pd.DataFrame:
    """雪球沪深股市热度排行榜（多 endpoint 分派）。

    endpoint:
      - follow: 关注排行榜（ak.stock_hot_follow_xq）
      - tweet:  讨论排行榜（ak.stock_hot_tweet_xq）
      - deal:   交易排行榜（ak.stock_hot_deal_xq）

    Args:
        endpoint: 排行榜类型
        symbol: {"最热门", "沪深股市", "创业板", "科创板"}

    Returns:
        DataFrame
    """
    ak = _ak()
    try:
        if endpoint == "follow":
            df = ak.stock_hot_follow_xq(symbol=symbol)
        elif endpoint == "tweet":
            df = ak.stock_hot_tweet_xq(symbol=symbol)
        elif endpoint == "deal":
            df = ak.stock_hot_deal_xq(symbol=symbol)
        else:
            raise ValueError(f"Unknown xueqiu_hot endpoint: {endpoint}")
        if df is None or df.empty:
            logger.debug("fetch_xueqiu_hot(%s): empty", endpoint)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_xueqiu_hot(%s) failed: %s", endpoint, e)
        # v3.3.15：不吞异常，交由 SmartRouter 降级并计健康度
        raise


# ============================================================
# fund_hold_data — 机构持仓（多 endpoint）
# ============================================================

def fetch_fund_hold_data(endpoint: str = "hold", symbol: str = "基金持仓", date: str = "", **kwargs) -> pd.DataFrame:
    """机构持仓数据（仅服务 endpoint="detail"，hold 已移交直取实现）。

    ⚠️ endpoint="hold" **已故意停用**（v3.3.15 之后发现的问题）：
    本函数原先调用 `ak.stock_report_fund_hold`，但该 akshare 接口用
      `big_df.columns = ["序号","_","股票简称","_","_","持有基金家数",...]`
    **按位置**硬编码中文列名，而上游东财 `/dataapi/zlsj/list` 的字段顺序
    已经改版 → **整行列错位**。实测（直接调 akshare，绕开本项目全部代码）：
      股票代码 列拿到 HOLDCHA_VALUE = -415345718.82（一个市值，不是代码）
      股票简称 列拿到 SECURITY_CODE  = 000680（代码跑到了简称列）
      持股变动数值 列拿到 HOLDCHA     = '减仓'（文本跑进了数值列）
    且 1.18.91 与 1.18.94 **同样错位** → 属上游字段漂移 + akshare 位置映射，
    **不是本项目升级引入**。此前每年 4–9 月因默认日期非法而返回空表，所以看不出来。

    关键：真正需要的 `SECURITY_NAME_ABBR`（股票简称）落在 akshare 标为 "_" 的
    位置，最终 `big_df[[...]]` 选择时被**丢弃** → 无法在其输出上补救列名，
    只能绕开 akshare 直取上游。故 hold 改由 `fetch_fund_hold_direct` 承担。
    此处 hold 直接 raise，而**不作为备源兜底** —— 否则 direct 失败时它会
    静默返回错标数据，比直接失败更糟。

    Args:
        endpoint: 仅支持 "detail"（单只基金持仓明细，走 ak.stock_report_fund_hold_detail）
        symbol: detail 时为基金代码
        date: 财报日期 "YYYYMMDD"

    Returns:
        DataFrame
    """
    ak = _ak()
    if endpoint == "hold":
        raise RuntimeError(
            "akshare 的 stock_report_fund_hold 列已错位（上游字段顺序改版 + akshare "
            "按位置硬编码列名），hold 已改由 fetch_fund_hold_direct 直取东财；"
            "此处不再作为兜底，以免返回错标数据"
        )
    if not date:
        date = _prev_quarter_end()
    try:
        if endpoint == "detail":
            df = ak.stock_report_fund_hold_detail(symbol=symbol, date=date)
        else:
            raise ValueError(f"Unknown fund_hold_data endpoint: {endpoint}")
        if df is None or df.empty:
            logger.debug("fetch_fund_hold_data(%s, %s): empty", endpoint, symbol)
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("fetch_fund_hold_data(%s, %s) failed: %s", endpoint, symbol, e)
        # v3.3.15：不吞异常，交由 SmartRouter 降级并计健康度
        raise


# ============================================================
# 机构持仓汇总 —— 直取东财（修复 akshare 列错位）
# ============================================================
# 上游：东方财富 datacenter /dataapi/zlsj/list
# 与 akshare 的唯一区别：**按上游字段名映射中文列名**，而不是按位置。
# 上游字段顺序再变也不会错位。
_EM_ZLSJ_URL = "http://data.eastmoney.com/dataapi/zlsj/list"

# 机构类型 -> 东财 type 参数
_FUND_HOLD_SYMBOL_MAP = {
    "基金持仓": "1",
    "QFII持仓": "2",
    "社保持仓": "3",
    "券商持仓": "4",
    "保险持仓": "5",
    "信托持仓": "6",
}

# (上游字段名, 输出中文列名) —— 顺序即输出列顺序
#
# ⚠️ 列名**刻意沿用 akshare 原有的 9 个列名**（不新增/不改名），只把「哪个字段
# 填进哪个列」改对。原因：下游（如周末复盘报告模板）按列名取值，改名会静默
# 打断它们。最后额外追加一列 持股占流通股比（纯新增，不改动既有列，风险低）。
_FUND_HOLD_FIELDS = [
    ("SECURITY_CODE", "股票代码"),
    ("SECURITY_NAME_ABBR", "股票简称"),
    ("HOULD_NUM", "持有基金家数"),
    ("TOTAL_SHARES", "持股总数"),
    ("HOLD_VALUE", "持股市值"),
    ("HOLDCHA", "持股变化"),          # 增仓 / 减仓（方向）
    ("HOLDCHA_NUM", "持股变动数值"),   # 股数增减
    ("HOLDCHA_RATIO", "持股变动比例"), # 百分比
    ("FREESHARES_RATIO", "持股占流通股比"),  # 新增列（akshare 原 schema 无此列）
]

_FUND_HOLD_NUM_COLS = (
    "持有基金家数", "持股总数", "持股市值", "持股变动数值",
    "持股变动比例", "持股占流通股比",
)


def fetch_fund_hold_direct(
    endpoint: str = "hold", symbol: str = "基金持仓", date: str = "", **kwargs
) -> pd.DataFrame:
    """机构持仓汇总 —— 直取东财 datacenter，按字段名映射列（修复列错位）。

    仅支持 endpoint="hold"；其余 endpoint 抛 ValueError，交由 SmartRouter
    降级到 akshare 源（后者负责 detail）。

    Args:
        endpoint: 仅 "hold"
        symbol: {"基金持仓","QFII持仓","社保持仓","券商持仓","保险持仓","信托持仓"}
        date: 财报日期 "YYYYMMDD"，默认上一季度末

    Returns:
        DataFrame，列沿用 akshare 原有命名以保证下游不被打断：
        序号 / 股票代码 / 股票简称 / 持有基金家数 / 持股总数 / 持股市值 /
        持股变化 / 持股变动数值 / 持股变动比例，并追加 持股占流通股比。
        其中「持股变化」为方向文本（增仓/减仓），「持股变动数值」为股数，
        「持股变动比例」为百分比。
    """
    if endpoint != "hold":
        raise ValueError(
            f"fetch_fund_hold_direct 仅支持 endpoint='hold'，收到 {endpoint!r}"
            "（detail 请走 akshare 源）"
        )
    t = str(symbol or "基金持仓").strip()
    if t not in _FUND_HOLD_SYMBOL_MAP:
        raise ValueError(
            f"未知机构类型 {symbol!r}（支持: {list(_FUND_HOLD_SYMBOL_MAP)}）"
        )
    if not date:
        date = _prev_quarter_end()
    em_date = (
        f"{date[:4]}-{date[4:6]}-{date[6:]}"
        if (len(date) == 8 and date.isdigit())
        else date
    )

    # 函数内导入（项目既有约定）：测试 monkeypatch em_client.em_get 后此处生效
    from .em_client import em_get

    rows: list = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        params = {
            "date": em_date,
            "type": _FUND_HOLD_SYMBOL_MAP[t],
            "zjc": "0",
            "sortField": "HOULD_NUM",
            "sortDirec": "1",
            "pageNum": str(page),
            "pageSize": "500",
            "p": str(page),
            "pageNo": str(page),
        }
        resp = em_get(_EM_ZLSJ_URL, params=params, timeout=25)
        if resp is None:
            raise RuntimeError(
                f"东财机构持仓({t}, {date}) 第 {page} 页请求重试耗尽"
            )
        payload = resp.json()
        if not payload or not payload.get("data"):
            break
        rows.extend(payload["data"])
        try:
            total_pages = int(payload.get("pages") or 1)
        except (TypeError, ValueError):
            total_pages = 1
        page += 1
        if page > 60:  # 安全阀：最多 60 页（3 万条），防上游 pages 异常导致死循环
            logger.warning("fetch_fund_hold_direct(%s): 分页超过 60 页，提前截断", t)
            break

    if not rows:
        raise RuntimeError(f"东财机构持仓({t}, {date}) 返回空数据")

    out = pd.DataFrame(
        [{cn: r.get(en) for en, cn in _FUND_HOLD_FIELDS} for r in rows]
    )
    out.insert(0, "序号", range(1, len(out) + 1))
    for c in _FUND_HOLD_NUM_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # 护栏：列错位会立刻暴露 —— 股票代码必须是 6 位数字，否则宁可失败也不给错标数据
    codes = out["股票代码"].astype(str).str.strip()
    if not codes.str.fullmatch(r"\d{6}").all():
        bad = codes[~codes.str.fullmatch(r"\d{6}")].head(3).tolist()
        raise RuntimeError(
            f"东财机构持仓({t}) 股票代码列异常（疑似上游字段再变）：{bad}"
        )
    return out


# ============================================================
# v3.3.14 新增：单源类型的独立备源（避免东财一挂全挂）
# ============================================================
# 背景：company_info / financial_stmt / industry_data 此前均为 akshare 单源，
# 且都走东方财富。东财不可用（限流 / 反爬 / 代理误路由）时这些数据类型
# **完全没有兜底**，违反「一主一备」原则。
# 以下备源刻意选用**非东财**厂商：巨潮资讯 / 新浪财经 / 同花顺。


def _code6(code: str) -> str:
    """提取 6 位纯代码（去掉 sh/sz/bj 前缀）。"""
    c = str(code or "").strip().lower()
    return c[2:] if c.startswith(("sh", "sz", "bj")) else c


def _prefixed_code(code: str) -> str:
    """补全为 sh/sz/bj 前缀形式（新浪等接口需要）。"""
    c = str(code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if len(c) == 6 and c.isdigit():
        if c.startswith(("60", "68", "90", "11", "13")):
            return "sh" + c
        if c.startswith(("00", "30", "20")):
            return "sz" + c
        if c.startswith(("8", "43", "92")):
            return "bj" + c
    return c


def fetch_company_info_cninfo(
    endpoint: str = "individual_info",
    symbol: str = "",
    code: str = "",
    **kwargs,
) -> pd.DataFrame:
    """公司信息备源（巨潮资讯 stock_profile_cninfo，非东财）。

    v3.3.14 新增：company_info 此前只有 akshare 东财单源。
    仅覆盖 individual_info 语义；其它 endpoint 直接报错，交由路由汇总失败原因。
    """
    if endpoint != "individual_info":
        raise ValueError(
            f"cninfo 备源不支持 company_info endpoint={endpoint!r}（仅 individual_info）"
        )
    sym = _code6(code or symbol)
    if not sym:
        raise RuntimeError("stock code is required")
    return _ak().stock_profile_cninfo(symbol=sym)


_SINA_STMT_MAP = {
    "profit": "利润表",
    "balance": "资产负债表",
    "cashflow": "现金流量表",
}


def fetch_financial_stmt_sina(
    endpoint: str = "profit",
    symbol: str = "",
    code: str = "",
    **kwargs,
) -> pd.DataFrame:
    """财务报表备源（新浪财经 stock_financial_report_sina，非东财）。

    v3.3.14 新增：financial_stmt 此前只有 akshare 东财单源。
    覆盖 profit / balance / cashflow 三个主 endpoint。
    """
    if endpoint not in _SINA_STMT_MAP:
        raise ValueError(
            f"sina 备源不支持 financial_stmt endpoint={endpoint!r}"
            f"（支持: {list(_SINA_STMT_MAP)}）"
        )
    sym = _prefixed_code(code or symbol)
    if not sym:
        raise RuntimeError("stock code is required")
    return _ak().stock_financial_report_sina(stock=sym, symbol=_SINA_STMT_MAP[endpoint])


def fetch_industry_data_ths(endpoint: str = "board_industry_name_em", **kwargs):
    """行业 / 概念板块备源（同花顺，非东财）。

    v3.3.14 新增：industry_data 此前只有 akshare 东财单源。
    接受主源的 endpoint 名并映射到对应的同花顺接口。
    """
    ak = _ak()
    if endpoint in ("board_industry_name_em", "board_industry_name_ths"):
        return ak.stock_board_industry_name_ths()
    if endpoint in ("board_concept_name_em", "board_concept_name_ths"):
        return ak.stock_board_concept_name_ths()
    raise ValueError(
        f"ths 备源不支持 industry_data endpoint={endpoint!r}"
        "（支持: board_industry_name_em/ths, board_concept_name_em/ths）"
    )


# ============================================================
# v3.3.15 新增备源 —— 补「单源无兜底」的 8 个数据类型
# ============================================================
# 背景：hot_rank / hot_search / xueqiu_hot / fund_hold / futures_news /
#      baidu_economic_calendar / baidu_trade_notify / index_news_sentiment
#      原为单源注册，源失效即整类型失效；且部分源此前还把异常静默吞成空表，
#      失败完全不可见。本节补齐「非同一上游」的备源，避免与主源同生共死。
#
# 选源原则（与 P2-2 的教训一致）：
#   备源不得与主源打同一个上游域名族 —— 否则主源被限流时备源一起死。
#   东财 push2 族在本机会间歇性被 RST，故首选同花顺 / 百度 / 巨潮等独立上游。


def fetch_industry_comparison_ths(
    endpoint: str = "即时", code: str = "", symbol: str = "",
    trade_date: str = "", top_n: int = 20, **kwargs,
) -> dict:
    """行业横向对比备源（同花顺行业资金流，非东财）。

    v3.3.15 新增：industry_comparison 原先主备两源（em_push2 / akshare
    stock_sector_fund_flow_rank）**都打东财 push2 族**，主源被限流时备源一同失败，
    整类型失效。本源改走同花顺 10jqka，与主源完全解耦。

    返回与 em_push2 / akshare 源完全相同的 dict 结构，工具层无需改动。
    """
    ak = _ak()
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # 同花顺行业资金流：即时 / 3日排行 / 5日排行 / 10日排行 / 20日排行
    indicator = endpoint if endpoint in (
        "即时", "3日排行", "5日排行", "10日排行", "20日排行"
    ) else "即时"

    df = ak.stock_fund_flow_industry(symbol=indicator)
    if df is None or df.empty:
        raise RuntimeError("同花顺行业资金流数据为空")

    chg_col = next(
        (c for c in ("行业-涨跌幅", "阶段涨跌幅", "涨跌幅") if c in df.columns), None
    )

    result: dict = {
        "source": f"THS stock_fund_flow_industry({indicator})",
        "date": trade_date,
        "code": code or symbol or None,
        "industries": [],
    }
    for i, (_, row) in enumerate(df.iterrows()):
        if i >= top_n:
            break
        try:
            chg = float(row.get(chg_col, 0) or 0) if chg_col else 0.0
        except (TypeError, ValueError):
            chg = 0.0
        result["industries"].append({
            "rank": i + 1,
            "name": str(row.get("行业", "") or ""),
            "change_pct": chg,
            "up_count": 0,
            "down_count": 0,
            "leader": str(row.get("领涨股", "") or ""),
        })
    if not result["industries"]:
        raise RuntimeError("同花顺行业资金流解析后为空")
    return result


def fetch_hot_rank_ths(
    endpoint: str = "rank", symbol: str = "", period: str = "", **kwargs,
) -> pd.DataFrame:
    """热度榜备源（同花顺人气榜，非东财）。

    v3.3.15 新增，同时服务 hot_rank / hot_search / xueqiu_hot 三个类型的降级兜底。
    这三类主源分别是东财人气榜(push2，本机会间歇性 RST)、百度股市通、雪球热度，
    都属"个股热度排行"语义，同花顺人气榜可作为结构相近的降级备源。

    返回列：排名 / 代码 / 名称 / 人气值 / 涨幅% / 排名变化 / 概念标签 / 热度标签

    个股维度的 endpoint（detail/realtime/keyword/latest/relate）无法用全市场榜
    替代，遇到时直接抛错 —— 宁可如实记为失败，也不返回语义不符的数据。
    """
    from . import ths_fetchers as _ths   # 局部导入，避免模块级循环依赖

    if endpoint in ("detail", "realtime", "keyword", "latest", "relate"):
        raise ValueError(
            f"ths 备源不支持个股维度 endpoint={endpoint!r}（同花顺热榜仅提供全市场榜）"
        )
    p = period or ("day" if endpoint in ("up", "3日排行") else "hour")
    df = _ths.fetch_ths_hot_list(period=p)
    if df is None or df.empty:
        raise RuntimeError("同花顺热榜返回空")
    return df


def fetch_baidu_economic_calendar_bak(date: str = "", **kwargs) -> pd.DataFrame:
    """财报披露日历备源（百度股市通，非东财）。

    v3.3.15 新增，服务 baidu_economic_calendar 的兜底。
    主源是百度理财日历（宏观事件），本备源给的是 A股财报披露时间表，
    语义偏"财报日历"一侧，属**降级备源**（字段不同，工具层泛化输出）。
    """
    ak = _ak()
    # 注意：ak.news_report_time_baidu 的 date 默认值是**写死的过期日期**，
    # 不传就用 2025 年的旧值。这里显式补当天，避免备源返回陈旧数据。
    d = date or datetime.now().strftime("%Y%m%d")
    df = ak.news_report_time_baidu(date=d)
    if df is None or df.empty:
        raise RuntimeError(f"百度财报披露时间表返回空（date={d}）")
    return df


def fetch_market_sentiment_legu(**kwargs) -> pd.DataFrame:
    """A股市场情绪备源（乐咕乐股「赚钱效应分析」，非东财、非 chinascope）。

    v3.3.15 新增，服务 index_news_sentiment 的兜底。

    背景（实测结论）：主源 akshare index_news_sentiment_scope 的上游
    www.chinascope.com 已**不再提供 JSON 接口**，现在返回 HTML 页面
    （content-type: text/html，正文是 <!DOCTYPE html>），任何客户端都拿不到数据 ——
    属上游永久失效，非本机网络/证书问题。

    本备源给出标准 A股市场情绪口径（上涨/下跌/涨停/跌停家数、真实涨跌幅、
    活跃度等），来自 legulegu.com，与主源完全独立。
    """
    ak = _ak()
    df = ak.stock_market_activity_legu()
    if df is None or df.empty:
        raise RuntimeError("乐咕乐股市场活跃度返回空")
    return df


def fetch_baidu_trade_notify_tfp(
    endpoint: str = "suspend", symbol: str = "", date: str = "", **kwargs,
) -> pd.DataFrame:
    """交易提示/停复牌备源（akshare stock_tfp_em 全市场停复牌表，非百度）。

    v3.3.15 新增，服务 baidu_trade_notify 的兜底。
    主源是百度交易提示（停复牌口径）。本备源给出全市场停复牌明细
    （序号/代码/名称/停牌时间/停牌截止时间/停牌期限/停牌原因），语义精确对应，
    且**不需要股票代码**，与工具层"市场级调用"的方式一致。
    """
    ak = _ak()
    df = ak.stock_tfp_em()
    if df is None or df.empty:
        raise RuntimeError("全市场停复牌表返回空")
    return df

