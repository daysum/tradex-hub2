"""
HTTP 直连数据源 fetch_fn 包装器。

纯 HTTP 抓取的数据源（不依赖 akshare），主要是腾讯行情接口。
仅本文件允许直接发起 HTTP 请求获取行情数据。

全局行情（global_market_quote）通过腾讯 qt.gtimg.cn 批量获取，
支持美股/大宗/亚太指数/外汇等外围行情。

**v3.3.19 腾讯传输层加固**：原实现用 `urllib.request` + `User-Agent: Mozilla/5.0`
裸奔——无 TLS 指纹伪装、无限流、无重试。批量拉全市场时会产生数十个无间隔连续请求
（`_tencent_batch_quotes` 按 80 只/批循环），是潜在的限流触发点。
现改为 curl_cffi 会话（浏览器指纹 + UA 版本匹配）+ 串行节流 + 指数退避重试，
并保留 `.read()` 返回语义，调用方与既有测试无需改动。
"""

from __future__ import annotations

import json
import logging
import threading

import pandas as pd

from astock_signals.antiban_http import (
    TENCENT_JITTER_MAX,
    TENCENT_JITTER_MIN,
    TENCENT_MAX_RETRY,
    TENCENT_MIN_INTERVAL,
    Throttle,
    get_bytes,
    make_session,
)

logger = logging.getLogger("tradex.http")

# ---------------------------------------------------------------------------
# 腾讯行情专用传输层（与东财节流器相互独立：两家的风控阈值不同）
# ---------------------------------------------------------------------------
_TENCENT_REFERER = "https://gu.qq.com/"

_tencent_throttle = Throttle(
    TENCENT_MIN_INTERVAL, TENCENT_JITTER_MIN, TENCENT_JITTER_MAX
)
_tencent_session = None
_tencent_lock = threading.Lock()


def _ensure_tencent_session():
    """腾讯会话（懒加载、线程安全）：指纹伪装 + 直连绕代理。"""
    global _tencent_session
    if _tencent_session is None:
        with _tencent_lock:
            if _tencent_session is None:
                _tencent_session = make_session(referer=_TENCENT_REFERER)
    return _tencent_session


def tencent_reset_session() -> None:
    """关闭并重建腾讯会话（换 IP / 连续被限流后调用）。"""
    global _tencent_session
    with _tencent_lock:
        if _tencent_session is not None:
            try:
                _tencent_session.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("tencent_reset_session close error: %s", e)
            _tencent_session = None


class _TencentResponse:
    """轻量响应对象，保留 `.read()` 语义（调用方按 GBK 解码）。

    curl_cffi 的 Response 无 `.read()`，而既有调用方与测试都按
    `resp.read().decode("gbk")` 取值，故做一层薄包装。
    """

    __slots__ = ("content", "status_code")

    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def read(self) -> bytes:
        return self.content


def _urlopen_no_proxy(url: str, timeout: int = 10) -> _TencentResponse:
    """腾讯行情接口请求：指纹伪装 + 串行节流 + 指数退避重试，直连不走代理。

    保留原函数名与签名（`.read()` 返回字节），既是调用方接口也是测试注入点。

    Raises:
        RuntimeError: 重试耗尽仍失败（原 urllib 实现抛 URLError，语义一致）。
    """
    raw = get_bytes(
        _ensure_tencent_session,
        _tencent_throttle,
        url,
        timeout=timeout,
        max_retries=TENCENT_MAX_RETRY,
    )
    if raw is None:
        raise RuntimeError(f"tencent request failed: {url}")
    return _TencentResponse(raw)


def _tencent_quote_vals(code: str) -> list:
    """从腾讯 qt.gtimg.cn 获取个股行情，返回 ~ 分隔的值列表。

    入参兼容 6 位代码与已带前缀的代码（sh600000/sz000001），
    已带前缀时不重复拼接（防 szsh600000 之类错误 URL）。
    """
    sym = code.strip().lower()
    if sym[:2] in ("sh", "sz", "bj"):
        query = sym
    else:
        prefix = "sh" if sym.startswith("6") else "sz"
        query = prefix + sym
    url = f"https://qt.gtimg.cn/q={query}"
    resp = _urlopen_no_proxy(url, timeout=5)
    raw = resp.read().decode("gbk")
    if '"' not in raw:
        raise RuntimeError("tencent quote: no data")
    return raw.split('"')[1].split("~")


# ---------------------------------------------------------------------------
# 腾讯指数日K（proxy.finance.qq.com，单次请求）
# ---------------------------------------------------------------------------
_TENCENT_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"


def _to_tencent_index_sym(symbol: str) -> str:
    """归一化成腾讯要的 sh000001 / sz399001 形式。"""
    s = str(symbol).strip().lower()
    if s[:2] in ("sh", "sz", "bj"):
        return s
    digits = s.split(".")[-1].zfill(6) if "." in s else s.zfill(6)
    # 上证系列（000xxx / 999999）走 sh，其余走 sz
    return ("sh" if digits.startswith(("000", "999")) else "sz") + digits


def fetch_index_daily_tencent(symbol: str = "", code: str = "", days: int = 20,
                              **kwargs) -> list[dict]:
    """指数历史日K量能序列（腾讯 proxy.finance.qq.com，**单次请求**）。

    为什么不用 akshare 的 `stock_zh_index_daily_tx`：它按**年份逐年翻页**
    （上证指数从 1990 年起 → 37 次请求 ≈ 21 秒），在 SmartRouter 的 12s
    超时下必然失败，于是该备源被误判为不可用。而我们只需最近若干交易日，
    **当年一次请求即可**（实测 0.12s，快约 150 倍）。

    返回 [{"date","volume","amount"}, ...]：
      - volume 取原始数组第 6 位（成交量，手）
      - **amount 恒为 None** —— 腾讯该接口的成交额字段单位未能可靠确认，
        宁可为空也不给出可能错标的数据（本项目有过列错位的教训）
    兼容 symbol/code 两种参数名。
    """
    sym = _to_tencent_index_sym(symbol or code)
    if not sym or len(sym) < 8:
        raise RuntimeError("index symbol required (e.g. sh000001)")

    # 请求当年 + 上一年：跨年时也能凑够 days 条（接口按 end 回推 640 根，通常一次足够）
    from datetime import datetime as _dt

    year = _dt.now().year
    resp = None
    for y in (year, year - 1):
        raw = get_bytes(
            _ensure_tencent_session,
            _tencent_throttle,
            _TENCENT_KLINE_URL,
            params={"param": f"{sym},day,{y}-01-01,{y + 1}-12-31,640,qfq"},
            timeout=10,
            max_retries=TENCENT_MAX_RETRY,
        )
        if raw is None:
            continue
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("tencent index kline(%s) json failed: %s", sym, exc)
            continue
        node = ((payload.get("data") or {}).get(sym) or {})
        bars = node.get("day") or node.get("qfqday") or []
        if bars:
            resp = bars
            break

    if not resp:
        raise RuntimeError(f"tencent index daily empty for {sym}")

    out = []
    for row in resp[-max(int(days), 1):]:
        try:
            out.append({
                "date": str(row[0]),
                "volume": float(row[5]) if row[5] not in (None, "") else 0.0,
                "amount": None,
            })
        except (IndexError, TypeError, ValueError):
            continue
    if not out:
        raise RuntimeError(f"tencent index daily parse failed for {sym}")
    return out


def fetch_realtime_quote_tencent(symbol: str = "", code: str = "", **kwargs):
    """实时行情（腾讯 qt.gtimg.cn）。返回单行 DataFrame，含'代码'列。

    作为 realtime_quote 的 priority=200 兜底源。
    兼容 symbol/code 两种参数名（SmartRouter 路由归一化）。
    """
    import pandas as pd
    sym = symbol or code
    if not sym:
        raise RuntimeError("stock code is required (symbol or code)")
    vals = _tencent_quote_vals(sym)
    if len(vals) < 50:
        raise RuntimeError("tencent returned insufficient data")
    return pd.DataFrame([{
        "代码": sym,
        "名称": vals[1] if len(vals) > 1 else "",
        "最新价": float(vals[3]) if vals[3] else 0,
        "涨跌幅": float(vals[32]) if len(vals) > 32 and vals[32] else 0,
        "成交量": float(vals[36]) if len(vals) > 36 and vals[36] else 0,
        "成交额": float(vals[37]) if len(vals) > 37 and vals[37] else 0,
        "最高": float(vals[33]) if len(vals) > 33 and vals[33] else 0,
        "最低": float(vals[34]) if len(vals) > 34 and vals[34] else 0,
        "今开": float(vals[5]) if len(vals) > 5 and vals[5] else 0,
        "昨收": float(vals[4]) if len(vals) > 4 and vals[4] else 0,
        "总市值": float(vals[45]) if len(vals) > 45 and vals[45] else 0,
        "流通市值": float(vals[44]) if len(vals) > 44 and vals[44] else 0,
        "市盈率": float(vals[39]) if len(vals) > 39 and vals[39] else 0,
    }])


def fetch_profit_forecast_tencent(symbol: str = "", code: str = "", **kwargs) -> dict:
    """一致预期（腾讯行情兜底）。仅返回价格/PE，无 EPS 预测数据。

    作为 profit_forecast 的 priority=100 备源（当同花顺抓取失败时）。
    兼容 symbol/code 两种参数名（SmartRouter 路由归一化）。
    """
    sym = symbol or code
    if not sym:
        raise RuntimeError("stock code is required (symbol or code)")
    vals = _tencent_quote_vals(sym)
    if len(vals) < 50:
        raise RuntimeError("tencent returned insufficient data for profit_forecast")
    price = float(vals[3]) if vals[3] else 0
    pe_ttm = float(vals[39]) if len(vals) > 39 and vals[39] else 0
    return {
        "symbol": sym,
        "source": "tencent qt.gtimg.cn (price only)",
        "price": price,
        "pe_ttm": pe_ttm,
        "forecasts": [],
        "summary": "同花顺 EPS 抓取失败，仅返回腾讯实时价格/PE",
    }


# ============================================================
# 分类行情列表（腾讯全市场批量备源）— category_quotes 降级
# ============================================================

_TENCENT_BATCH = 80   # 腾讯单次批量请求的代码数（控制 URL 长度）
_A_SHARE_PREFIX = {"6": "sh", "5": "sh", "9": "sh"}  # 其余默认 sz


def _a_share_prefix(code: str) -> str:
    """6 位 A 股代码 → 腾讯前缀（sh/sz）。688/600/601/603/605/5/9 → sh，其余 sz。"""
    if code.startswith(("6", "5", "9", "900")):
        return "sh"
    return "sz"


def _is_a_share_code(code: str) -> bool:
    """6 位代码是否为 A 股（排除指数 399/899/880、ETF 15x/51x 等）。"""
    return (
        len(code) == 6
        and (
            code.startswith(("600", "601", "603", "605", "688", "689"))
            or code.startswith(("000", "001", "002", "003", "300", "301"))
            or code.startswith(("430", "830", "831", "832", "833", "835", "836", "837", "838", "839", "870", "871", "872", "873", "889", "920"))
        )
    )


_codes_cache: dict = {"ts": 0.0, "data": []}


def _a_share_codes_for_tencent() -> list[str]:
    """返回全市场 A 股 6 位代码（备源拉全市场实时行情用）。

    优先从 eltdx 证券代码表拿（稀缺），失败时用内置常见前缀兜底（不保证全)，
    最后降级为空。惰性 import 避免与 eltdx_fetchers 循环依赖。
    代码表 60 分钟缓存（A 股代码增减低频）。
    """
    import time as _t
    now = _t.time()
    if _codes_cache["data"] and now - _codes_cache["ts"] < 3600:
        return _codes_cache["data"]
    codes: list[str] = []
    try:
        from .eltdx_fetchers import fetch_security_codes
        df = fetch_security_codes(market="all")
        for _, r in df.iterrows():
            full = str(r.get("代码", "")).lower().strip()
            code = full[2:] if len(full) == 8 and full[:2] in ("sh", "sz", "bj") else full
            if _is_a_share_code(code):
                codes.append(code)
    except Exception:  # noqa: BLE001
        codes = []
    if not codes:
        codes = ["600000", "600519", "601318", "000858", "300750", "002594"]
    _codes_cache["data"] = codes
    _codes_cache["ts"] = now
    return codes


def _tencent_batch_quotes(codes: list[str]) -> list[list]:
    """分批发拉腾讯实时行情，返回每只的 ~ 分隔字段列表（含代码/名称）。

    批量请求：q=sh600000,sz000001,... 一次最多 _TENCENT_BATCH 只。
    失败批次跳过，返回成功解析的行。
    """
    rows: list[list[str]] = []
    for i in range(0, len(codes), _TENCENT_BATCH):
        batch = codes[i:i + _TENCENT_BATCH]
        query = ",".join(_a_share_prefix(c) + c for c in batch)
        try:
            resp = _urlopen_no_proxy(f"https://qt.gtimg.cn/q={query}", timeout=8)
            raw = resp.read().decode("gbk")
            for line in raw.strip().split(";"):
                line = line.strip()
                if "=" not in line:
                    continue
                payload = line.split("=", 1)[1].strip().strip('"')
                if not payload or "~" not in payload:
                    continue
                fields = payload.split("~")
                # 丢弃停牌/无效（现价<=0）
                try:
                    px = float(fields[3]) if len(fields) > 3 and fields[3] else 0.0
                except (TypeError, ValueError):
                    px = 0.0
                if px <= 0:
                    continue
                rows.append(fields)
        except Exception:  # noqa: BLE001
            continue
    return rows


def fetch_category_quotes_tencent(category: str = "沪深a股", sort_by: str = "涨幅", count: int = 80, **kwargs):
    """分类行情列表（腾讯 qt.gtimg.cn 备源）。

    作为 category_quotes 的 priority=100 备源（eltdx 分类榜失败时降级）。
    无东财分类榜，改为「拉全市场实时行情 → 本地按 sort_by 排序」模拟榜单。
    返回与 eltdx fetch_category_quotes 相同 schema：
      代码 / 现价 / 涨跌幅(%) / 涨跌额 / 成交额(元) / 买一 / 卖一 / 涨速 / 短换手(%)
    """
    import pandas as pd
    codes = _a_share_codes_for_tencent()
    if not codes:
        raise RuntimeError("tencent category_quotes: no a-share codes")
    rows = _tencent_batch_quotes(codes)
    result = []
    for fields in rows:
        if len(fields) < 45:
            continue
        code = fields[2].split(".")[0] if "." in fields[2] else fields[2]
        px = float(fields[3]) if fields[3] else 0.0
        change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else 0.0
        change = float(fields[31]) if len(fields) > 31 and fields[31] else 0.0
        amount_wan = float(fields[37]) if len(fields) > 37 and fields[37] else 0.0
        turnover = float(fields[38]) if len(fields) > 38 and fields[38] else 0.0
        bid1 = float(fields[9]) if len(fields) > 9 and fields[9] else 0.0
        ask1 = float(fields[19]) if len(fields) > 19 and fields[19] else 0.0
        result.append({
            "代码": code,
            "现价": px,
            "涨跌幅": change_pct,
            "涨跌额": change,
            "成交额": amount_wan * 1e4,  # 万元 → 元（与 eltdx 一致/排序可比）
            "买一": bid1,
            "卖一": ask1,
            "涨速": 0.0,   # 腾讯下标不稳定，置 0
            "短换手": turnover,
        })
    if not result:
        raise RuntimeError("tencent category_quotes: empty")
    df = pd.DataFrame(result)
    # 本地排序模拟榜单：sort_by=成交额/涨幅 → 升/降
    key = "成交额" if sort_by in ("成交额", "成交") else "涨跌幅"
    ascending = False
    df = df.sort_values(key, ascending=ascending).head(count).reset_index(drop=True)
    return df


# ============================================================
# 全局行情批量获取 — global_market_quote
# ============================================================

# 腾讯接口支持的全局行情代码表
# 格式: (code, 类别, 中文名称)
# 注意：腾讯接口不支持大宗商品(hf_*)、A50期货(int_fta50)、日经(int_nikkei)、KOSPI、外汇汇率(usUSDCNH)
GLOBAL_QUOTE_CODES = [
    # 美股指数
    ("usDJI", "美股指数", "道琼斯"),
    ("usIXIC", "美股指数", "纳斯达克"),
    ("usINX", "美股指数", "标普500"),
    # 热门美股
    ("usNVDA", "热门美股", "英伟达"),
    ("usTSLA", "热门美股", "特斯拉"),
    ("usAAPL", "热门美股", "苹果"),
    ("usMSFT", "热门美股", "微软"),
    ("usAMZN", "热门美股", "亚马逊"),
    ("usGOOGL", "热门美股", "谷歌"),
    ("usMETA", "热门美股", "Meta"),
    ("usMU", "热门美股", "美光科技"),
    ("usAMAT", "热门美股", "应用材料"),
    # 亚太指数
    ("hkHSI", "亚太指数", "恒生指数"),
    ("hkHSTECH", "亚太指数", "恒生科技"),
    # 韩股龙头
    ("kr005930", "韩股", "三星电子"),
    ("kr000660", "韩股", "SK海力士"),
    # 外汇
    ("whDINIW", "外汇", "美元指数"),
]


def _tencent_global_batch() -> pd.DataFrame:
    """批量获取腾讯全局行情（单次请求拉取所有预设代码）。

    Returns:
        DataFrame with columns: 代码, 名称, 类别, 最新价, 涨跌额, 涨跌幅, 昨收, 今开, 最高, 最低, 更新时间
    """
    codes = [c[0] for c in GLOBAL_QUOTE_CODES]
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    resp = _urlopen_no_proxy(url, timeout=10)
    raw = resp.read().decode("gbk")

    rows = []
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        parts = line.split("=", 1)
        key = parts[0].strip().replace("v_", "")
        raw_val = parts[1].strip().strip('"')
        fields = raw_val.split("~")

        # 查找代码对应的类别
        category = ""
        for c, cat, _ in GLOBAL_QUOTE_CODES:
            if c == key:
                category = cat
                break

        name = fields[1] if len(fields) > 1 else key
        price = float(fields[3]) if len(fields) > 3 and fields[3] else 0.0
        last_close = float(fields[4]) if len(fields) > 4 and fields[4] else 0.0
        open_px = float(fields[5]) if len(fields) > 5 and fields[5] else 0.0
        high = float(fields[33]) if len(fields) > 33 and fields[33] else 0.0
        low = float(fields[34]) if len(fields) > 34 and fields[34] else 0.0
        change = float(fields[31]) if len(fields) > 31 and fields[31] else 0.0
        change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else 0.0
        ts = fields[30] if len(fields) > 30 else ""

        rows.append({
            "代码": key,
            "名称": name,
            "类别": category,
            "最新价": price,
            "涨跌额": change,
            "涨跌幅": change_pct,
            "昨收": last_close,
            "今开": open_px,
            "最高": high,
            "最低": low,
            "更新时间": ts,
        })

    return pd.DataFrame(rows)


def fetch_global_quote_tencent(**kwargs) -> pd.DataFrame:
    """全局行情批量获取（腾讯 qt.gtimg.cn）。

    一次性获取美股/大宗/亚太指数/热门股/外汇共 30+ 个品种的实时行情。
    作为 global_market_quote 数据类型的唯一源。

    Returns:
        DataFrame with columns: 代码, 名称, 类别, 最新价, 涨跌额, 涨跌幅, 昨收, 今开, 最高, 最低, 更新时间
    """
    try:
        df = _tencent_global_batch()
        if df is None or df.empty:
            raise RuntimeError("tencent global batch returned empty")
        return df
    except Exception as e:
        logger.warning("fetch_global_quote_tencent failed: %s", e)
        raise


def fetch_market_overview_tencent(**kwargs) -> pd.DataFrame:
    """A股主要指数行情（腾讯 qt.gtimg.cn 兜底）。

    作为 market_overview 的备用源，解决 akshare 代理失败问题。
    获取上证指数、深证成指、创业板指等主要指数行情。

    Returns:
        DataFrame with columns: 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交额, 成交量
    """
    # 腾讯A股指数代码：sh000001(上证), sz399001(深证), sz399006(创业板), sz399852(中证1000), sh000688(科创50), sh000300(沪深300), sh000905(中证500)
    index_codes = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000905", "sz399852"]
    url = "https://qt.gtimg.cn/q=" + ",".join(index_codes)
    try:
        resp = _urlopen_no_proxy(url, timeout=10)
        raw = resp.read().decode("gbk")
        rows = []
        for line in raw.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            parts = line.split("=", 1)
            raw_val = parts[1].strip().strip('"')
            fields = raw_val.split("~")
            name = fields[1] if len(fields) > 1 else ""
            price = float(fields[3]) if len(fields) > 3 and fields[3] else 0.0
            change = float(fields[31]) if len(fields) > 31 and fields[31] else 0.0
            change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else 0.0
            volume = float(fields[36]) if len(fields) > 36 and fields[36] else 0.0
            amount = float(fields[37]) if len(fields) > 37 and fields[37] else 0.0
            rows.append({
                "指数名称": name,
                "最新点位": price,
                "涨跌额": change,
                "涨跌幅": change_pct,
                "成交量(手)": volume,
                "成交额(元)": amount,
            })
        if not rows:
            raise RuntimeError("tencent index batch returned empty")
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning("fetch_market_overview_tencent failed: %s", e)
        raise


def fetch_market_breadth(**kwargs) -> pd.DataFrame:
    """全市场实时涨跌家数/涨跌停（东财 push2ex 涨跌分布，直连实时）。

    Returns:
        DataFrame columns: 上涨 / 下跌 / 平盘 / 涨停 / 跌停
    """
    from tradex.data_sources.em_client import em_get

    url = "https://push2ex.eastmoney.com/getTopicZDFenBu"
    params = {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt"}
    resp = em_get(url, params=params, timeout=15)
    if resp is None:
        raise RuntimeError("fetch_market_breadth: 东财请求重试耗尽")
    resp.raise_for_status()
    fenbu = resp.json()["data"]["fenbu"]
    up = down = flat = limit_up = limit_down = 0
    for item in fenbu:
        for k, v in item.items():
            k = int(k)
            v = int(v)
            if k > 0:
                up += v
            elif k < 0:
                down += v
            else:
                flat += v
            if k >= 10:
                limit_up += v
            if k <= -10:
                limit_down += v
    return pd.DataFrame([{"上涨": up, "下跌": down, "平盘": flat, "涨停": limit_up, "跌停": limit_down}])


def fetch_industry_quotes(**kwargs) -> pd.DataFrame:
    """行业板块实时涨幅（东财 push2 主源，失败降级 push2delay 镜像）。

    Returns:
        DataFrame columns: 板块名称 / 涨跌幅 / 领涨股票 / 领涨股票涨跌幅 / 上涨家数 / 下跌家数
    """
    from tradex.data_sources.em_client import em_get

    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
        "fid": "f3", "fs": "m:90 t:2 f:!50",
        "fields": "f12,f14,f2,f3,f104,f105,f128,f136",
    }
    # 主源 push2（实时、常封），备源 push2delay（稳定、延迟几分钟）
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
        try:
            resp = em_get(f"{host}/api/qt/clist/get", params=params, timeout=15)
            if resp is None:
                raise RuntimeError(f"{host} 请求重试耗尽")
            resp.raise_for_status()
            diff = resp.json().get("data", {}).get("diff", [])
            rows = []
            for item in diff:
                pct = item.get("f3")
                if pct is None or pct == "-":
                    continue
                rows.append({
                    "板块名称": item.get("f14", ""),
                    "涨跌幅": float(pct),
                    "领涨股票": item.get("f128", ""),
                    "领涨股票涨跌幅": float(item.get("f136", 0) or 0),
                    "上涨家数": int(item.get("f104", 0) or 0),
                    "下跌家数": int(item.get("f105", 0) or 0),
                })
            rows.sort(key=lambda x: x["涨跌幅"], reverse=True)
            return pd.DataFrame(rows)
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_industry_quotes %s failed: %s", host, e)
    raise RuntimeError("fetch_industry_quotes all sources failed")
