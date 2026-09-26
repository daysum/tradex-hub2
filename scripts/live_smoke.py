"""全量真实网络冒烟测试 —— 逐个直打每一个已注册数据源。

为什么需要这个脚本：
  离线单测（`pytest -m "not network"`）里的响应全是 mock 的，
  **根本不走 TLS 握手、不走真实节流**。因此防封改造一旦让某个源被 WAF 拦截
  （v3.3.20 巨潮 403 就是如此），单测会全绿而线上恒返回空。
  真实网络验证无法被 mock 测试替代。

用法：
    python scripts/live_smoke.py                 # 全部数据源 + 全部 MCP 工具
    python scripts/live_smoke.py --sources-only  # 只测数据源层
    python scripts/live_smoke.py --tools-only    # 只测 MCP 工具层
    python scripts/live_smoke.py --out report.md # 输出 Markdown 报告

判定口径：
    OK    取到非空结果
    EMPTY 未抛异常但结果为空（可能是参数不适配、非交易日、上游确实无数据）
    FAIL  抛异常
    SKIP  参数不足，无法构造调用（例如需要行业名/指数代码等特定参数）
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import io
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# 让脚本在仓库任意位置执行都能 import 到 src
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tradex" / "src"))

# 冒烟测试期间屏蔽业务日志噪音（只看结果）
logging.disable(logging.WARNING)


# ---------------------------------------------------------------------------
# 通用参数池：按 fetcher / tool 声明的形参名精确匹配后再传入
# ---------------------------------------------------------------------------
PARAM_POOL: dict[str, Any] = {
    # 个股
    "symbol": "600519",
    "code": "600519",
    "stock_code": "600519",
    "keyword": "茅台",
    "name": "贵州茅台",
    # 日期
    "date": "20260918",
    "curr_date": "2026-09-18",
    "trade_date": "20260918",
    "start_date": "20260901",
    "end_date": "20260918",
    # 周期 / 复权
    "period": "day",
    "adjust": "qfq",
    # 分类
    "category": "沪深a股",
    "industry": "白酒",
    "direction": "涨停",
    "endpoint": "",
    # 数量
    "limit": 5,
    "count": 5,
    # ⚠️ 刻意**不放** page_size：它是「每页条数」，语义敏感，不同接口的值域差别极大。
    # v3.3.30 踩坑：fetch_full_kline 新增 page_size 形参后被本池命中（=5），
    # 于是每页只取 5 根、6012 根要翻 1203 页，直接撞穿 max_pages=200 报
    # 「reached max_pages」—— 看起来像代码回归，其实是夹具把参数传坏了。
    # 需要它的类型（cninfo_announcement）在 OVERRIDES 里单独给。
    "num_results": 5,
    "top_n": 5,
    "days": 5,
    "n": 5,
}

# 这些形参若被传入空串会走错分支（如 endpoint="" 无法分派），一律不传
_SKIP_IF_EMPTY = {"endpoint"}

# ---------------------------------------------------------------------------
# 按数据类型的参数覆盖。
#
# 通用参数池只够覆盖"以个股代码为主参"的多数类型；下面这些类型的语义不同，
# 必须单独给参，否则失败的是**测试脚本**而不是被测代码：
#   - dragon_tiger  : trade_date 要 YYYY-MM-DD，不是 YYYYMMDD
#   - fund_hold     : symbol 是**机构类型**（基金持仓/社保持仓…），不是股票代码
#   - futures_news  : symbol 是品种名（全部/…）
#   - hot_search    : symbol 是市场（A股/…）
#   - xueqiu_hot    : symbol 是榜单类型（最热门/…）
#   - f10_extra     : 形参名是 entry（不是 endpoint）
#   - index_daily_amount : 要指数代码（000001=上证指数），不是个股代码
#   - minute_history / tick_data / opening_match_history : 形参名是 trading_date
# ---------------------------------------------------------------------------
OVERRIDES: dict[str, dict[str, Any]] = {
    "dragon_tiger": {"trade_date": "2026-09-18",
                     # 600519 是巨无霸、基本不上龙虎榜；用当日确实在榜的代码做夹具
                     "code": "002528"},
    "fund_hold": {"symbol": "基金持仓", "date": ""},   # 空日期 → 自动取上一季末
    "futures_news": {"symbol": "全部"},
    "hot_search": {"symbol": "A股"},
    "xueqiu_hot": {"symbol": "最热门"},
    "f10_extra": {"entry": "valuation"},
    "index_daily_amount": {"symbol": "000001", "code": "000001"},
    "minute_history": {"trading_date": "20260918"},
    # v3.3.30 新增：近期历史分时（0x0feb）。覆盖范围是服务端「近期窗口」，
    # 传固定历史日期可能落在窗口外 → 留空让 SDK 取当前日期。
    "recent_minute": {"trading_date": ""},
    "tick_data": {"trading_date": "20260918"},
    "opening_match_history": {"trading_date": "20260918"},
    # akshare 的 baidu 估值接口必须给 indicator，空串会 KeyError: 'Result'
    "valuation": {"indicator": "总市值"},
    # 巨潮公告：page_size 是真正的「每页条数」，5 条即可。
    # v3.3.30 把 page_size 移出通用参数池（避免污染全量 K 线接口），这里显式补回。
    "cninfo_announcement": {"page_size": 5},
    # 全量 K 线：显式锁死 page_size = SDK 默认 800。
    # 传小页（如通用池曾经的 5）会让 6000+ 根日线要翻上千页，撞穿 max_pages 而报错。
    "full_kline": {"page_size": 800},
    "adjusted_kline": {"page_size": 800},
}

# ---------------------------------------------------------------------------
# 数据源主机健康探测。
#
# 冒烟失败时最难判断的是「自己改坏了」还是「上游挂了」。逐主机打一发即可分辨：
# 若某主机的**全部**数据源同时失败，而兄弟主机正常，就是上游节点问题。
# 实测案例（2026-09-19）：push2.eastmoney.com 对所有指纹（含不伪装）都
# "Connection closed abruptly"，连命令行 curl 都是 HTTP 000；而同域的
# push2delay / push2his / push2ex / datacenter / slist 全部 200 → 该节点故障，
# 与防封改造无关。
# ---------------------------------------------------------------------------
HOST_PROBES: list[tuple[str, str]] = [
    ("东财 push2", "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fs=m:0+t:6&fields=f12,f14"),
    ("东财 push2delay", "https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fs=m:0+t:6&fields=f12,f14"),
    ("东财 push2his", "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519&fields1=f1&fields2=f51&klt=101&fqt=1&end=20500101&lmt=2"),
    ("东财 push2ex", "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=2&sort=fbt%3Aasc&date=20260918"),
    ("东财 datacenter", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=2"),
    ("东财 slist", "https://slist.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&fields=f12,f14&secid=1.600519"),
    ("东财 emweb(F10)", "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index?type=web&code=600519"),
    ("同花顺 10jqka", "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal"),
    ("同花顺 hexin", "https://data.hexin.cn/market/hsgtApi/method/dayChart/"),
    ("腾讯 qt.gtimg", "https://qt.gtimg.cn/q=sh600519"),
    ("财联社 cls", "https://www.cls.cn/v1/roll/get_roll_list?app=CailianpressWeb&rn=2&sv=8.4.6"),
    ("巨潮 cninfo", "http://www.cninfo.com.cn/new/information/topSearch/query"),
    ("新浪 sina", "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=2"),
    ("问财 openapi", "https://openapi.iwencai.com/"),
]


def check_hosts() -> list[Row]:
    """逐主机探活。只判断"能不能连上/拿到 HTTP 状态"，不判断业务语义。"""
    import os

    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    from curl_cffi import requests as cr

    rows: list[Row] = []
    for name, url in HOST_PROBES:
        t0 = time.time()
        try:
            r = cr.get(url, timeout=12, impersonate="chrome120")
            dt = time.time() - t0
            # 500/401 属"主机活着但请求方式/鉴权不对"，仍算连通
            status = "OK" if r.status_code < 500 else "WARN"
            rows.append(Row("host", name, "", status, dt,
                            f"HTTP {r.status_code} len={len(r.content)}"))
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            rows.append(Row("host", name, "", "FAIL", dt,
                            f"{type(e).__name__}: {str(e)[:90]}"))
    return rows

# 依赖本地环境（本地通达信数据目录 / 第三方 API Key）的类型。
# 这些在本机注定拿不到数据，归为 ENV 而不是 FAIL，避免污染结论。
ENV_DEPENDENT: dict[str, str] = {
    "local_kline": "需本机通达信 vipdoc 目录（tdxdir）",
    "local_minute": "需本机通达信 vipdoc 目录（tdxdir）",
    "wencai_news": "需环境变量 IWENCAI_API_KEY",
    "wencai_query": "需 pywencai 依赖 + 网络",
}

# 已确认由上游 / 第三方库自身导致、与本项目无关的项（人工逐个复核过）。
# 键优先取 "data_type/source"，回退到 "data_type"。
KNOWN_UPSTREAM: dict[str, str] = {
    "industry_comparison/em_push2":
        "push2.eastmoney.com 节点故障：对该主机所有指纹（含不伪装）均 "
        "'Connection closed abruptly'，命令行 curl 亦 HTTP 000；"
        "同域 push2delay/push2his/push2ex/datacenter/slist 全部 200",
    "company_info/cninfo":
        "cninfo 备源转调 akshare.stock_profile_cninfo（打 webapi.cninfo.com.cn），"
        "akshare 内部 KeyError 'count'，不经本项目传输层",
    "company_info/akshare":
        "同上（akshare 自身传输层）",
    "financial_stmt/akshare":
        "akshare 内部打 emweb.securities.eastmoney.com，该域名 DNS 已不解析",
    "fund_hold/akshare_fund_hold":
        "该源已按设计停用（akshare 列错位），改由 em_zlsj_direct 直取东财",
}

# akshare 自身传输层被上游拒连（akshare 用裸 requests，不走本项目统一防封层）。
# 这是"统一防封层覆盖不到第三方库"的结构性问题，非本次改造引入。
AKSHARE_TRANSPORT: set[str] = {
    "historical_kline/akshare", "industry_data/akshare",
    "minute_data/akshare", "industry_comparison/akshare",
    "financial_stmt/akshare", "hot_rank/akshare_hot_rank",
}


# ---------------------------------------------------------------------------
# 工具层的补充参数。
#
# 130 个工具里有 22 个无法用通用参数池调用，分两类：
#   1. 网络类但形参特殊（entry / codes / trading_date / item / indicator / query）
#      → 补下面这些即可覆盖
#   2. 纯计算类（calculate_* / screen_stocks / generate_trading_signal 等）
#      需要传入行情数组作为**输入数据**，不发起网络请求
#      → 由离线单测覆盖（test_technical_indicators_calc / test_factor_analysis /
#        test_stock_screening / test_signal_generation），本脚本不重复造数据，
#        避免合成数据形状猜错反而产生假失败
# ---------------------------------------------------------------------------
TOOL_PARAM_POOL: dict[str, Any] = {
    "entry": "valuation",
    "codes": "600519",
    "trading_date": "20260918",
    "item": "营业收入",
    "indicator": "MACD",
    "query": "贵州茅台",
}

# 纯计算类工具（需输入数组，不联网）——报告中显式标注，避免被当成"未测试"
COMPUTE_ONLY_TOOLS: set[str] = {
    "calculate_atr", "calculate_boll", "calculate_factor_score", "calculate_kdj",
    "calculate_ma_ema", "calculate_macd", "calculate_performance", "calculate_rsi",
    "generate_trading_signal", "validate_signal_quality", "screen_stocks",
    "scan_stocks_for_signals",
}

# ---------------------------------------------------------------------------
# 多 endpoint 数据类型的 endpoint 扫描。
#
# ⚠️ 为什么需要：**「类型级 OK」会骗人**。实测 2026-09-20 发现
# `industry_data / ths` 在报告里是 `OK (90,2)`，但那只是**默认 endpoint**
# （板块列表）的结果；同一个类型的另外两个 endpoint（行业成分股 / 行业历史行情）
# **两个源都不覆盖**，导致 `get_industry_stocks` / `get_industry_pe` /
# `get_competitors` 三个工具全在报「All sources failed」。
#
# 只按类型跑一遍默认 endpoint，会把这个缺口完全遮住。
# 所以：**凡 fetcher 带 `endpoint` 形参的类型，都要逐 endpoint 各测一遍。**
# ---------------------------------------------------------------------------
ENDPOINT_SWEEP: dict[str, list[str]] = {
    "industry_data": [
        "board_industry_name_em",
        "board_industry_name_ths",
        "board_concept_name_em",
        "board_concept_name_ths",
        "board_industry_cons_em",     # ← 曾无可用源
        "board_industry_hist_em",     # ← 曾无可用源
        "sector_fund_flow_rank",
    ],
}

# endpoint 级补充参数（按 "data_type:endpoint" 取）
ENDPOINT_PARAMS: dict[str, dict[str, Any]] = {
    "industry_data:board_industry_cons_em": {"industry": "白酒"},
    "industry_data:board_industry_hist_em": {"industry": "白酒", "period": "日k"},
}


def build_kwargs(fn: Any, data_type: str = "",
                 pool: dict[str, Any] = PARAM_POOL,
                 endpoint: str = "") -> dict[str, Any]:
    """只挑 fetcher/tool 自己声明过的形参，避免传入无关参数改变其行为。

    endpoint 非空时，一并叠加该 endpoint 的专属参数（ENDPOINT_PARAMS）。
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    merged = dict(pool)
    merged.update(TOOL_PARAM_POOL)
    merged.update(OVERRIDES.get(data_type, {}))
    if endpoint:
        merged["endpoint"] = endpoint
        merged.update(ENDPOINT_PARAMS.get(f"{data_type}:{endpoint}", {}))
    out: dict[str, Any] = {}
    for k, v in merged.items():
        if k not in params:
            continue
        if k in _SKIP_IF_EMPTY and not v:
            continue
        out[k] = v
    return out


def missing_required(fn: Any, kw: dict[str, Any]) -> list[str]:
    """列出没有默认值、又没被参数池覆盖的必填形参。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return []
    miss = []
    for name, p in params.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is p.empty and name not in kw:
            miss.append(name)
    return miss


def is_empty(result: Any) -> bool:
    """判断结果是否为空（DataFrame / list / dict / str 各有口径）。"""
    if result is None:
        return True
    # pandas DataFrame / Series
    empty = getattr(result, "empty", None)
    if isinstance(empty, bool):
        return empty
    if isinstance(result, (list, tuple, set, dict)):
        return len(result) == 0
    if isinstance(result, str):
        return not result.strip()
    return False


def describe(result: Any, limit: int = 90) -> str:
    """给结果一个人类可读的摘要。"""
    if result is None:
        return "None"
    if isinstance(result, (list, tuple)):
        return f"{type(result).__name__}[{len(result)}]"
    if isinstance(result, dict):
        keys = list(result)[:4]
        return f"dict({len(result)}) keys={keys}"
    shape = getattr(result, "shape", None)
    if shape is not None:
        return f"{type(result).__name__}{tuple(shape)}"
    return str(result)[:limit].replace("\n", " ")


class Row:
    __slots__ = ("kind", "name", "source", "status", "latency", "detail")

    def __init__(self, kind: str, name: str, source: str = "",
                 status: str = "", latency: float = 0.0, detail: str = "") -> None:
        self.kind, self.name, self.source = kind, name, source
        self.status, self.latency, self.detail = status, latency, detail


# ---------------------------------------------------------------------------
# 1. 数据源层：逐个直打每个已注册 fetcher
# ---------------------------------------------------------------------------
# 单项失败后的重试次数。上游偶发 503 / 连接重置很常见（实测 eltdx 会返回瞬时 503），
# 不重试就会把抖动记成"回归"，浪费大量人工复核。
# 重试仍失败才记录，并在 detail 里标注重试次数。
RETRY_ON_FAIL = 1


def _call_with_retry(fn: Any, kw: dict[str, Any]) -> tuple[Any, float, int]:
    """调用并返回 (结果, 耗时, 重试次数)。重试只针对抛异常的情况。"""
    attempts = RETRY_ON_FAIL + 1
    last_exc: Exception | None = None
    t0 = time.time()
    for attempt in range(attempts):
        try:
            if inspect.iscoroutinefunction(fn):
                return asyncio.run(fn(**kw)), time.time() - t0, attempt
            return fn(**kw), time.time() - t0, attempt
        except Exception as e:  # noqa: BLE001 - 冒烟要吞掉所有异常并记录
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(1.0)
    raise last_exc  # type: ignore[misc]


def _accepts_endpoint(fn: Any) -> bool:
    """fetcher 是否声明了 endpoint 形参（决定要不要做 endpoint 扫描）。"""
    try:
        return "endpoint" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def run_sources(only: set[str] | None = None) -> list[Row]:
    from tradex.data_sources.registry import register_all_sources
    from astock_signals.smart_router import get_router

    register_all_sources()
    router = get_router()

    rows: list[Row] = []
    for data_type in sorted(router._sources):
        if only and data_type not in only:
            continue
        sweep = ENDPOINT_SWEEP.get(data_type)
        for (src_name, fn, _prio, _excl) in router._sources[data_type]:
            # 多 endpoint 类型：逐 endpoint 各测一遍（否则默认 endpoint 的 OK
            # 会遮住其它 endpoint 无可用源的问题）
            combos: list[str | None] = sweep if (sweep and _accepts_endpoint(fn)) else [None]
            for ep in combos:
                label = f"{data_type}[{ep}]" if ep else data_type
                kw = build_kwargs(fn, data_type, endpoint=ep or "")
                miss = missing_required(fn, kw)
                if miss:
                    rows.append(Row("source", label, src_name, "SKIP", 0.0,
                                    f"缺少必填参数 {miss}"))
                    continue

                t0 = time.time()
                try:
                    result, dt, retries = _call_with_retry(fn, kw)
                    status = "EMPTY" if is_empty(result) else "OK"
                    detail = describe(result)
                    if retries:
                        detail += f"（重试 {retries} 次后成功）"
                    # 环境依赖型：本机注定拿不到，标注原因便于区分
                    if data_type in ENV_DEPENDENT and status == "EMPTY":
                        status = "ENV"
                        detail = f"{detail} —— {ENV_DEPENDENT[data_type]}"
                    rows.append(Row("source", label, src_name, status, dt, detail))
                except Exception as e:  # noqa: BLE001 - 冒烟要吞掉所有异常并记录
                    dt = time.time() - t0
                    msg = f"{type(e).__name__}: {e}"[:200].replace("\n", " ")
                    if RETRY_ON_FAIL:
                        msg += f"（已重试 {RETRY_ON_FAIL} 次）"
                    key = f"{data_type}/{src_name}"
                    if data_type in ENV_DEPENDENT:
                        rows.append(Row("source", label, src_name, "ENV", dt,
                                        f"{msg} —— {ENV_DEPENDENT[data_type]}"))
                    elif key in AKSHARE_TRANSPORT:
                        rows.append(Row("source", label, src_name, "AKSHARE", dt,
                                        f"{msg} —— akshare 自身传输层被上游拒连"
                                        f"（不走本项目统一防封层）"))
                    elif key in KNOWN_UPSTREAM or data_type in KNOWN_UPSTREAM:
                        reason = KNOWN_UPSTREAM.get(key) or KNOWN_UPSTREAM[data_type]
                        rows.append(Row("source", label, src_name, "UPSTREAM", dt,
                                        f"{msg} —— {reason}"))
                    elif "不支持" in msg:
                        # 源**明确声明**不支持该 endpoint（覆盖缺口，非代码回归）。
                        # 单看这一行不算问题 —— 关键要看有没有**别的源**兜住，
                        # 见 endpoints_without_source()。
                        rows.append(Row("source", label, src_name, "UNSUPPORTED", dt, msg))
                    else:
                        rows.append(Row("source", label, src_name, "FAIL", dt, msg))
    return rows


def endpoints_without_source(rows: list[Row]) -> list[str]:
    """找出「所有源都拿不到数据」的 (类型[ endpoint ]) 组合。

    ⚠️ 这是多 endpoint 类型的**关键结论**：
    单看某一行 `OK` 会漏掉「该 endpoint 其实没有任何源可用」的情况
    （实测 `industry_data / ths` 默认 endpoint OK，但行业成分股/历史行情
    两个源都不覆盖 → 相关工具全部失败）。
    """
    agg: dict[str, set[str]] = {}
    for r in rows:
        if r.kind != "source":
            continue
        agg.setdefault(r.name, set()).add(r.status)
    return sorted(
        f"{name}  [{', '.join(sorted(st))}]"
        for name, st in agg.items()
        if "OK" not in st
    )


# ---------------------------------------------------------------------------
# 2. MCP 工具层：调用每个已注册工具的底层函数
# ---------------------------------------------------------------------------
def run_tools(only: set[str] | None = None) -> list[Row]:
    import tradex.server as srv

    # 注意：mcp.list_tools() 返回的是 Tool 描述对象（只有 name/inputSchema），
    # **不带底层函数**；要拿可调用体必须走 _tool_manager._tools[name].fn。
    registered = srv.mcp._tool_manager._tools
    rows: list[Row] = []

    for name in sorted(registered):
        if only and name not in only:
            continue
        fn = getattr(registered[name], "fn", None)
        if fn is None:
            rows.append(Row("tool", name, "", "SKIP", 0.0, "工具无底层 fn 属性"))
            continue

        kw = build_kwargs(fn, name)
        miss = missing_required(fn, kw)
        if miss:
            # 纯计算类：需要输入数组，不联网，由离线单测覆盖
            if name in COMPUTE_ONLY_TOOLS:
                rows.append(Row("tool", name, "", "SKIP", 0.0,
                                f"纯计算类，需输入行情数组（不联网）：{miss}"))
            else:
                rows.append(Row("tool", name, "", "SKIP", 0.0,
                                f"缺少必填参数 {miss}"))
            continue

        t0 = time.time()
        try:
            result, dt, retries = _call_with_retry(fn, kw)
            status = "EMPTY" if is_empty(result) else "OK"
            detail = describe(result)
            if retries:
                detail += f"（重试 {retries} 次后成功）"
            # 工具层约定返回 JSON 字符串；顺带校验合法性 + 识别 no_data
            if isinstance(result, str) and result.strip().startswith(("{", "[")):
                try:
                    payload = json.loads(result)
                except Exception:  # noqa: BLE001
                    status = "FAIL"
                    detail = f"返回非法 JSON: {result[:120]}"
                else:
                    # 工具层的统一约定：没取到数据返回 {"status": "no_data", ...}。
                    # 它是一条合法 JSON，但**不是 OK** —— 之前会被误报成 OK。
                    if isinstance(payload, dict) and payload.get("status") == "no_data":
                        status = "EMPTY"
            rows.append(Row("tool", name, "", status, dt, detail))
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            msg = f"{type(e).__name__}: {e}"[:200].replace("\n", " ")
            if RETRY_ON_FAIL:
                msg += f"（已重试 {RETRY_ON_FAIL} 次）"
            rows.append(Row("tool", name, "", "FAIL", dt, msg))
    return rows


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
ICON = {"OK": "OK  ", "EMPTY": "EMPTY", "FAIL": "FAIL", "SKIP": "SKIP",
        "ENV": "ENV ", "UPSTREAM": "UPSTREAM", "AKSHARE": "AKSHARE", "WARN": "WARN",
        "UNSUPPORTED": "UNSUPPORTED"}

# 只有 FAIL 算回归；ENV（缺本地数据/Key）/ UPSTREAM（上游节点或第三方库自身问题）不算
REGRESSION_STATUSES = {"FAIL"}

_SUMMARY_LABELS = (("EMPTY", "空结果"), ("FAIL", "失败"),
                   ("ENV", "环境依赖"), ("UPSTREAM", "上游问题"),
                   ("AKSHARE", "akshare传输"), ("WARN", "连通但异常"),
                   ("UNSUPPORTED", "源不支持该endpoint"), ("SKIP", "跳过"))


def summarize(rows: list[Row], title: str) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    parts = [f"共 {len(rows)} 项", f"取到数据 {counts.get('OK', 0)}"]
    for key, label in _SUMMARY_LABELS:
        if counts.get(key):
            parts.append(f"{label} {counts[key]}")
    return f"{title}: " + " | ".join(parts)


def render_markdown(host_rows: list[Row], source_rows: list[Row],
                    tool_rows: list[Row]) -> str:
    lines: list[str] = ["# tradex-hub 全量真实网络冒烟报告", ""]
    lines.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("> 离线单测的响应是 mock 的，不走 TLS 握手与真实节流，")
    lines.append("> 因此本报告是防封改造后唯一的有效端到端证据。")
    lines.append("")
    lines.append("判定口径：`OK` 取到数据 ｜ `EMPTY` 无异常但结果空 ｜ `FAIL` 抛异常（**回归**）")
    lines.append("｜ `ENV` 缺本地数据/API Key ｜ `UPSTREAM` 上游节点或第三方库自身问题")
    lines.append("｜ `AKSHARE` akshare 自身传输层被拒 ｜ `SKIP` 参数不足")
    lines.append("｜ `UNSUPPORTED` 源明确不支持该 endpoint（覆盖缺口，非回归）")
    lines.append(f"｜ 单项失败会重试 {RETRY_ON_FAIL} 次（上游偶发 503/连接重置很常见）")
    lines.append("")

    sections = (("〇、数据源主机连通性", host_rows),
                ("一、数据源层", source_rows),
                ("二、MCP 工具层", tool_rows))
    for title, rows in sections:
        if not rows:
            continue
        lines += [f"## {title}", "", summarize(rows, title), ""]
        lines += ["| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |",
                  "|------|------|------|------|------|"]
        for r in sorted(rows, key=lambda x: (x.status != "FAIL", x.status != "EMPTY",
                                             x.status != "UPSTREAM", x.name)):
            detail = r.detail.replace("|", "\\|")[:200]
            lines.append(f"| {r.status} | {r.name} | {r.source or '-'} | "
                         f"{r.latency:.2f} | {detail} |")
        lines.append("")

    fails = [r for r in source_rows + tool_rows if r.status == "FAIL"]
    lines += ["## 结论", ""]

    gaps = endpoints_without_source(source_rows)
    if gaps:
        lines += ["### ⚠️ 无可用源的 (类型 / endpoint) 组合", "",
                  "**所有源都拿不到数据**的组合 —— 这是多 endpoint 类型最容易漏掉的问题：",
                  "单看某一行 `OK` 会以为该类型没问题，其实个别 endpoint 完全没有源覆盖。", ""]
        for g in gaps:
            lines.append(f"- `{g}`")
        lines.append("")

    if fails:
        lines.append(f"**发现 {len(fails)} 项失败（需人工确认是否回归）：**")
        lines.append("")
        for r in fails:
            lines.append(f"- **{r.name}**" + (f" (`{r.source}`)" if r.source else "")
                         + f" — {r.detail}")
    elif not gaps:
        lines.append("**无失败项**：所有可调用接口均未抛异常。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources-only", action="store_true")
    ap.add_argument("--tools-only", action="store_true")
    ap.add_argument("--types", default="",
                    help="只测这些数据类型（逗号分隔），如 fund_hold,dragon_tiger")
    ap.add_argument("--tools", default="",
                    help="只测这些工具名（逗号分隔）")
    ap.add_argument("--out", default="", help="Markdown 报告输出路径")
    ap.add_argument("--skip-hosts", action="store_true", help="跳过主机连通性探测")
    args = ap.parse_args()

    only_types = {t.strip() for t in args.types.split(",") if t.strip()} or None
    only_tools = {t.strip() for t in args.tools.split(",") if t.strip()} or None

    host_rows: list[Row] = []
    source_rows: list[Row] = []
    tool_rows: list[Row] = []

    if not args.skip_hosts:
        print(">>> 探测数据源主机连通性...", flush=True)
        host_rows = check_hosts()
        print("    " + summarize(host_rows, "主机连通性"), flush=True)

    if not args.tools_only:
        print(">>> 测试数据源层（逐个直打每个已注册 fetcher）...", flush=True)
        source_rows = run_sources(only_types)
        print("    " + summarize(source_rows, "数据源层"), flush=True)

    if not args.sources_only:
        print(">>> 测试 MCP 工具层（调用每个工具的底层函数）...", flush=True)
        tool_rows = run_tools(only_tools)
        print("    " + summarize(tool_rows, "MCP 工具层"), flush=True)

    # 控制台明细：打印所有非 OK 项
    for title, rows in (("主机连通性", host_rows), ("数据源层", source_rows),
                        ("MCP 工具层", tool_rows)):
        if not rows:
            continue
        print(f"\n===== {title}：非 OK 明细 =====")
        for r in sorted(rows, key=lambda x: (x.status, x.name)):
            if r.status == "OK":
                continue
            src = f" [{r.source}]" if r.source else ""
            print(f"  {ICON.get(r.status, r.status):9s} {r.name}{src}  "
                  f"{r.latency:5.2f}s  {r.detail}")

    gaps = endpoints_without_source(source_rows)
    if gaps:
        print("\n===== ⚠️ 无可用源的 (类型/endpoint) 组合 =====")
        for g in gaps:
            print(f"  {g}")

    if args.out:
        out = Path(args.out)
        out.write_text(render_markdown(host_rows, source_rows, tool_rows),
                       encoding="utf-8")
        print(f"\n报告已写入：{out}")

    # 退出码：只有 FAIL（真正的回归）才非 0，方便接入 CI
    return 1 if any(r.status in REGRESSION_STATUSES
                    for r in source_rows + tool_rows) else 0


if __name__ == "__main__":
    sys.exit(main())
