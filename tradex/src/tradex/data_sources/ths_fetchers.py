"""同花顺数据源 —— 一致预期 / 热点归因 / 涨停揭秘 / 热榜（零鉴权）。

借鉴 a-stock-data 的同花顺实现，均为零鉴权直连接口。
同花顺是不封 IP 的低风险源，东财被封时的备选。

v3.3.20：原先模块级 `requests.Session()` 裸请求（零指纹、零节流）已收敛到
`astock_signals.antiban_http.get_client("ths")` —— 指纹伪装 + 独立节流器 +
指数退避重试。同花顺风控较松，间隔取 0.25s，不牺牲吞吐。
"""
from __future__ import annotations

import logging
from datetime import datetime
from io import StringIO

import pandas as pd

from astock_signals.antiban_http import get_client, json_of

logger = logging.getLogger("tradex.ths")


def _get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 10, **kw):
    """经统一防封层请求同花顺接口（指纹伪装 + 节流 + 退避重试）。

    Raises:
        RuntimeError: 请求重试耗尽（与原 requests 抛 ConnectionError 语义一致）。
    """
    r = get_client("ths").get(url, params=params, headers=headers, timeout=timeout, **kw)
    if r is None:
        raise RuntimeError(f"同花顺请求重试耗尽: {url}")
    return r


def _json(r, **kw):
    """同花顺接口服务端多不声明 charset，内容实为 GBK，故统一带 gbk 兜底。"""
    return json_of(r, fallback_encoding="gbk")


def fetch_ths_eps_forecast(code: str, **kwargs) -> pd.DataFrame:
    """同花顺机构一致预期 EPS（直连 basic.10jqka.com.cn，解析 HTML 表格）。

    Returns:
        DataFrame: 年度 / 预测机构数 / 最小值 / 均值 / 最大值（均值=一致预期 EPS）
    """
    code = str(code).split(".")[0].split("_")[0]  # 纯 6 位，带前缀会 404
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    r = _get(url, headers={"Referer": "https://basic.10jqka.com.cn/"}, timeout=15)
    html = r.content.decode("gbk", errors="replace")
    dfs = pd.read_html(StringIO(html))
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any("每股收益" in c or "均值" in c for c in cols):
            return df
    return dfs[0] if dfs else pd.DataFrame()


def fetch_ths_hot_reason(date: str | None = None, **kwargs) -> pd.DataFrame:
    """同花顺当日强势股 + 题材归因（reason 人工运营标签，核心字段）。

    实测 73ms 拿到 ~125 只 + 完整字段。
    Returns columns: 名称 / 代码 / 题材归因 / 涨幅% / 换手率% / 成交额 / 大单净量 ...
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    url = (f"http://zx.10jqka.com.cn/event/api/getharden/"
           f"date/{date}/orderby/date/orderway/desc/charset/GBK/")
    r = _get(url, timeout=10)
    data = _json(r)
    if data.get("errocode", 0) != 0:
        raise RuntimeError(f"同花顺热点错误: {data.get('errormsg', '')}")
    rows = data.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    rename = {
        "name": "名称", "code": "代码", "reason": "题材归因",
        "close": "收盘价", "zhangdie": "涨跌额", "zhangfu": "涨幅%",
        "huanshou": "换手率%", "chengjiaoe": "成交额",
        "chengjiaoliang": "成交量", "ddejingliang": "大单净量", "market": "市场",
    }
    return df.rename(columns=rename)


def fetch_ths_limit_up_pool(date: str, **kwargs) -> pd.DataFrame:
    """同花顺涨停揭秘：涨停原因 + 封板质量。date=YYYYMMDD。

    Returns columns: 代码 / 名称 / 涨停原因 / 板型 / 封板成功率 / 炸板次数 / 封单额 / 连板 / 首封时间
    """
    url = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    params = {
        "page": 1, "limit": 200,
        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
        "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": date,
    }
    r = _get(url, params=params, timeout=10)
    info = (_json(r).get("data") or {}).get("info", [])
    rows = []
    for it in info:
        ft = it.get("first_limit_up_time")
        rows.append({
            "代码": it.get("code"), "名称": it.get("name"),
            "价格": it.get("latest"), "涨幅%": it.get("change_rate"),
            "涨停原因": it.get("reason_type", ""), "板型": it.get("limit_up_type", ""),
            "封板成功率": it.get("limit_up_suc_rate"), "炸板次数": it.get("open_num") or 0,
            "封单额": it.get("order_amount"), "连板": it.get("high_days", ""),
            "首封时间": datetime.fromtimestamp(int(ft)).strftime("%H:%M:%S") if ft else "",
            "是否回封": it.get("is_again_limit"),
        })
    return pd.DataFrame(rows)


def fetch_ths_hot_list(period: str = "hour", **kwargs) -> pd.DataFrame:
    """同花顺热榜：人气值 + 概念标签 + 排名变化。period: hour/day。

    Returns columns: 排名 / 代码 / 名称 / 人气值 / 涨幅% / 排名变化 / 概念标签 / 热度标签
    """
    r = _get("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
             params={"stock_type": "a", "type": period, "list_type": "normal"}, timeout=10)
    lst = (_json(r).get("data") or {}).get("stock_list") or []
    rows = []
    for it in lst:
        tag = it.get("tag") or {}
        rows.append({
            "排名": it.get("order"), "代码": it.get("code"), "名称": it.get("name"),
            "人气值": it.get("rate"), "涨幅%": it.get("rise_and_fall"),
            "排名变化": it.get("hot_rank_chg"),
            "概念标签": tag.get("concept_tag") or [],
            "热度标签": tag.get("popularity_tag", ""),
        })
    return pd.DataFrame(rows)


# ── 市场盘口分析（同花顺 eq.10jqka.com.cn 盘面分析页，零鉴权直连）──
# 三个接口：涨跌分布 / 涨跌趋势分时 / 市场成交额分时。
# 关键：limit_up_minute 接口的 header 返回「准确全市场涨跌家数/涨跌停（含北交所）」，
# 比东财 getTopicZDFenBu（仅沪深A股、涨停口径含新股虚增）更准确。

_THS_REFERER = "https://eq.10jqka.com.cn/"


def fetch_ths_up_down_distribution(**kwargs) -> pd.DataFrame:
    """同花顺涨跌分布（11 档区间，含北交所）。

    Returns columns: 区间 / 家数 / 方向（跌/平/涨）
    """
    url = "https://dq.10jqka.com.cn/fuyao/up_down_distribution/distribution/v2/realtime"
    r = _get(url, headers={"Referer": _THS_REFERER}, timeout=15)
    r.raise_for_status()
    table = (_json(r).get("data") or {}).get("table", [])
    n = len(table)
    rows = []
    for i, it in enumerate(table):
        key = str(it.get("key", ""))
        val = int(it.get("value", 0) or 0)
        # key 有重复（">10%" 跌/涨各一次），靠顺序判方向：前半跌、中间平、后半涨
        if i < n // 2:
            direction = "跌"
        elif i == n // 2:
            direction = "平"
        else:
            direction = "涨"
        rows.append({"区间": key, "家数": val, "方向": direction})
    return pd.DataFrame(rows)


def fetch_ths_market_chart(chart_key: str, **kwargs) -> dict:
    """同花顺市场分析图表数据（盘面分析页的涨跌趋势/成交额分时）。

    chart_key: limit_up_minute（涨跌趋势）/ turnover_minute（市场成交额）/ market_score_day（评分）。

    Returns dict: {name, header:[{name,key,val}], point_key_list, point_list, ...}
    """
    url = f"https://dq.10jqka.com.cn/fuyao/market_analysis_api/chart/v1/get_chart_data?chart_key={chart_key}"
    r = _get(url, headers={"Referer": _THS_REFERER}, timeout=15)
    r.raise_for_status()
    return (_json(r).get("data") or {}).get("charts") or {}


def fetch_ths_limit_up_minute(**kwargs) -> dict:
    """涨跌趋势分时（含 header 准确全市场涨跌家数/涨跌停，含北交所）。"""
    return fetch_ths_market_chart("limit_up_minute")


def fetch_ths_turnover_minute(**kwargs) -> dict:
    """市场成交额分时（当日 vs 昨日同时刻）。"""
    return fetch_ths_market_chart("turnover_minute")


def fetch_ths_market_breadth(**kwargs) -> dict:
    """准确全市场涨跌家数/涨跌停/平盘（含北交所）。

    涨/跌/涨停/跌停从 limit_up_minute 接口的 header 提取；平盘数从涨跌分布接口 "0" 档拿。
    返回 {"up","down","flat","limit_up","limit_down"}。
    实测：上涨2121/下跌3292/涨停80/跌停5（与通达信/同花顺全市场口径一致）。
    """
    data = fetch_ths_market_chart("limit_up_minute")
    header = data.get("header") or []
    out = {}
    for h in header:
        out[str(h.get("key"))] = h.get("val")
    flat = 0
    try:
        dist = fetch_ths_up_down_distribution()
        zero = dist[dist["方向"] == "平"]
        flat = int(zero["家数"].sum()) if not zero.empty else 0
    except Exception:  # noqa: BLE001
        flat = 0
    return {
        "up": int(out.get("rise", 0) or 0),
        "down": int(out.get("fall", 0) or 0),
        "flat": flat,
        "limit_up": int(out.get("limit_up", 0) or 0),
        "limit_down": int(out.get("limit_down", 0) or 0),
    }


# ══════════════════════════════════════════════════════════════════════════
# 板块历史行情（同花顺 d.10jqka.com.cn，v3.3.29）
#
# 为什么加这个：东财的板块日K路由（`push2his/api/qt/stock/kline/get`，
# `secid=90.BKxxxx`）整条链路故障 —— 实测所有 push2his* 主机连接被拒，
# 而 `push2delay` 虽认得出板块（返回 `name`）但 `klines` 为空。
# 同花顺的板块K线**免鉴权可用**，故作为板块历史行情的来源。
#
# 接口：`d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js`（最近 ~140 根）
#       `d.10jqka.com.cn/v6/line/bk_{code}/{seg}/{year}.js`（某年全量）
# 返回 JSONP：`quotebridge_v6_line_bk_xxx_01_last({"year":{...},"name":"白酒","data":"..."})`
#   data 每行 `日期,开,高,低,收,成交量,成交额,...`（**同花顺顺序：开高低收**）
# 板块代码：行业 881xxx（如 白酒=881273、半导体=881121），概念 3xxxxx。
# ══════════════════════════════════════════════════════════════════════════
_THS_BOARD_LINE_URL = "http://d.10jqka.com.cn/v6/line/bk_{code}/{seg}/{span}.js"
# 周期 → 同花顺分段码（实测：01=日K、11=周K、21=月K；31=分时）
_THS_BOARD_SEG = {
    "日k": "01", "周k": "11", "月k": "21",
    "daily": "01", "weekly": "11", "monthly": "21", "day": "01",
}

# 板块「名称 → 同花顺代码」映射缓存（板块名单变动很慢）
_THS_BOARD_MAP_CACHE: dict[str, dict[str, str]] = {}


def _ths_board_name_map(kind: str = "industry") -> dict[str, str]:
    """板块「名称 → 同花顺代码」映射（行业 / 概念），带进程内缓存。

    来源用 akshare 的同花顺板块列表函数（`stock_board_industry_name_ths` /
    `stock_board_concept_name_ths`）—— 它们实测稳定可用（90 / 375 行，
    列 `name`/`code`），且项目已在 `board_industry_name_ths` 端点使用同一来源。
    """
    cached = _THS_BOARD_MAP_CACHE.get(kind)
    if cached:
        return cached
    import akshare as ak

    fn = (ak.stock_board_concept_name_ths if kind == "concept"
          else ak.stock_board_industry_name_ths)
    df = fn()
    if df is None or df.empty:
        raise RuntimeError(f"同花顺 {kind} 板块列表为空")
    mapping = {
        str(r["name"]).strip(): str(r["code"]).strip()
        for _, r in df.iterrows()
        if str(r.get("name", "")).strip()
    }
    if not mapping:
        raise RuntimeError(f"同花顺 {kind} 板块列表解析为空")
    _THS_BOARD_MAP_CACHE[kind] = mapping
    return mapping


def _ths_board_code(name_or_code: str, kind: str = "industry") -> str:
    """板块名 → 同花顺代码；已是纯数字代码则原样返回。"""
    s = str(name_or_code or "").strip()
    if s.isdigit() and len(s) >= 6:
        return s
    if not s:
        raise RuntimeError("板块名称/代码不能为空")
    code = _ths_board_name_map(kind).get(s)
    if not code:
        raise RuntimeError(f"同花顺板块列表中未找到 {s!r}")
    return code


def _parse_ths_board_line(text: str) -> tuple[list[list[str]], str]:
    """解析同花顺板块K线 JSONP，返回 (数据行列表, 板块名)。"""
    import json as _json

    lp, rp = text.find("("), text.rfind(")")
    if lp < 0 or rp <= lp:
        raise RuntimeError("同花顺板块K线响应格式异常（无 JSONP 包裹）")
    payload = _json.loads(text[lp + 1: rp])
    raw = payload.get("data") or ""
    rows = [r.split(",") for r in raw.split(";") if r]
    return rows, str(payload.get("name") or "")


def fetch_ths_board_hist(
    symbol: str = "",
    code: str = "",
    period: str = "日k",
    start_date: str = "",
    end_date: str = "",
    kind: str = "industry",
    **kwargs,
) -> pd.DataFrame:
    """同花顺板块历史行情（日K/周K/月K，免鉴权）。

    Args:
        symbol / code: 板块名称（如 "白酒"）或同花顺板块代码（如 "881273"）
        period: 日k / 周k / 月k
        start_date / end_date: YYYYMMDD 或 YYYY-MM-DD
        kind: industry（行业，881xxx）/ concept（概念，3xxxxx）

    Returns:
        DataFrame，列与东财 `stock_board_industry_hist_em` 对齐：
        日期 / 开盘 / 收盘 / 最高 / 最低 / 涨跌幅 / 涨跌额 / 成交量 / 成交额 / 振幅 / 换手率

    说明：同花顺板块K线**不提供换手率**，该列恒为空；
    涨跌幅/涨跌额/振幅按**前一根收盘**计算（首行无前收 → 空）。
    """
    seg = _THS_BOARD_SEG.get(str(period or "日k").strip().lower())
    if seg is None:
        raise ValueError(f"同花顺板块K线不支持的周期: {period!r}（支持 日k/周k/月k）")

    board = _ths_board_code(symbol or code, kind=kind)
    client = get_client("ths")

    bars: dict[str, list[str]] = {}
    year_map: dict[str, int] = {}

    def _fetch(span: str) -> None:
        url = _THS_BOARD_LINE_URL.format(code=board, seg=seg, span=span)
        r = client.get(url, headers={"Referer": "http://q.10jqka.com.cn/"}, timeout=12)
        if r is None:
            raise RuntimeError(f"同花顺板块K线请求重试耗尽: {url}")
        if getattr(r, "status_code", None) != 200:
            raise RuntimeError(f"同花顺板块K线 HTTP {r.status_code}: {url}")
        rows, _name = _parse_ths_board_line(r.content.decode("gbk", errors="replace"))
        for row in rows:
            if row and row[0]:
                bars[row[0]] = row

    _fetch("last")   # 最近 ~140 根（含 year 表，但 _parse 只取 data）
    if not bars:
        raise RuntimeError(f"同花顺板块K线返回空（板块 {board}）")

    # 起始日期早于 last.js 覆盖范围 → 再按年取（同花顺支持 .../{year}.js）
    beg = _norm_ymd(start_date)
    if beg:
        earliest = min(bars)
        if beg < earliest:
            import time as _t
            y0 = int(beg[:4])
            y1 = int((_norm_ymd(end_date) or _t.strftime("%Y%m%d"))[:4])
            for y in range(y0, min(y1, y0 + 30) + 1):
                try:
                    _fetch(str(y))
                except Exception as exc:  # noqa: BLE001 - 个别年份缺数据不影响整体
                    logger.debug("同花顺板块K线 %s 年 %s 取数失败: %s", board, y, exc)

    rows = [bars[d] for d in sorted(bars)]
    recs = []
    for row in rows:
        if len(row) < 7:
            continue
        try:
            recs.append({
                "日期": row[0],
                "开盘": float(row[1]),
                "最高": float(row[2]),
                "最低": float(row[3]),
                "收盘": float(row[4]),
                "成交量": float(row[5]) if row[5] else None,
                "成交额": float(row[6]) if row[6] else None,
            })
        except (TypeError, ValueError):
            continue
    if not recs:
        raise RuntimeError(f"同花顺板块K线解析为空（板块 {board}）")

    df = pd.DataFrame(recs)
    prev = df["收盘"].shift(1)
    df["涨跌额"] = df["收盘"] - prev
    df["涨跌幅"] = df["涨跌额"] / prev * 100
    df["振幅"] = (df["最高"] - df["最低"]) / prev * 100
    df["换手率"] = None            # 同花顺板块K线不提供

    if beg:
        df = df[df["日期"] >= beg]
    end = _norm_ymd(end_date)
    if end:
        df = df[df["日期"] <= end]
    if df.empty:
        raise RuntimeError(f"同花顺板块K线在指定区间内为空（板块 {board}）")

    df["日期"] = pd.to_datetime(df["日期"], format="%Y%m%d", errors="coerce").dt.date
    cols = ["日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额",
            "成交量", "成交额", "振幅", "换手率"]
    return df[cols].reset_index(drop=True)


def _norm_ymd(s: str) -> str:
    """YYYYMMDD / YYYY-MM-DD → YYYYMMDD；空串原样返回。"""
    d = str(s or "").strip().replace("-", "")
    return d if len(d) == 8 and d.isdigit() else ""
