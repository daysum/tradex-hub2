"""
tdxdata 数据源封装（Phase 0 基础设施 + Phase 2/3 备源 fetch_fn）。

tdxdata 是 vendored 于 ``tradex/src/tdxdata`` 的通达信手机版 7615 客户端：
  - 零第三方依赖（纯标准库）
  - 421 个数据中心产品 + K 线/分时/逐笔/报价/板块/涨跌家数
  - 单命令服务端节流约 2.2 秒，**限流按来源 IP 计，禁止并发/高频调用**

本模块提供：
  1. 单例 + 全局锁管理 ``tdxdata.Client``（tdxdata 自身状态非线程安全）；
  2. 进程退出时关闭长连接；
  3. ``tdx_ping()`` 探针（供冒烟脚本/诊断工具使用）；
  4. SmartRouter 备源 fetch_fn：历史K线/分时/涨跌家数/财报/当日逐笔。

⚠️ 不要在工具层直接 ``import tdxdata``；统一经本模块的
``get_client()`` / ``call()``，以保证单例、串行与可观测。
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from ..config import config

logger = logging.getLogger("tradex.tdxdata")

# 客户端单例与锁
_client: Any | None = None
_client_lock = threading.Lock()
_call_lock = threading.RLock()  # tdxdata Client 状态非线程安全，所有调用串行
_atexit_registered = False


def is_enabled() -> bool:
    """tdxdata 是否启用（TDXDATA_ENABLED）。"""
    return bool(config.TDXDATA_ENABLED)


def _import_tdxdata():
    """懒加载 vendored tdxdata，返回模块对象。"""
    try:
        import tdxdata  # noqa: PLC0415 - 懒加载，避免未使用时的 import 成本
    except ImportError as exc:  # pragma: no cover - 打包缺失时才会触发
        raise RuntimeError(
            "tdxdata 未安装/未随包打包：请确认 tradex/src/tdxdata 存在，"
            "且 wheel 包含 tdxdata/data/products.json"
        ) from exc
    return tdxdata


def get_client():
    """获取（懒创建）全局 tdxdata.Client 单例。

    只创建对象，不建立 TCP 连接；tdxdata 在第一次取数时自动连接。

    Raises:
        RuntimeError: TDXDATA_ENABLED=false 或 vendored 包缺失。
    """
    global _client, _atexit_registered
    if not is_enabled():
        raise RuntimeError("tdxdata disabled by TDXDATA_ENABLED=false")
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        tdxdata = _import_tdxdata()
        client = tdxdata.Client(
            timeout=float(config.TDXDATA_CONNECT_TIMEOUT),
            gap=float(config.TDXDATA_GAP),
        )
        _client = client
        if not _atexit_registered:
            atexit.register(_shutdown)
            _atexit_registered = True
        logger.info(
            "tdxdata client created (version=%s, gap=%.1fs, timeout=%.0fs)",
            getattr(tdxdata, "__version__", "?"),
            float(config.TDXDATA_GAP),
            float(config.TDXDATA_CONNECT_TIMEOUT),
        )
        return client


def call(method: str, *args, **kwargs) -> Any:
    """串行调用 tdxdata.Client 的公开方法。

    所有 tdxdata 调用必须经过这里：Client 的 ``_last_call`` / ``_cal`` /
    ``_clock`` 是裸可变状态，且其 pacer 是进程级单例；用 RLock 串行化最简单可靠。

    Args:
        method: Client 方法名，如 ``"get_bars"`` / ``"get_product"``。
        *args, **kwargs: 透传参数。

    Returns:
        方法返回值。
    """
    client = get_client()
    fn: Callable[..., Any] = getattr(client, method)
    with _call_lock:
        return fn(*args, **kwargs)


def _shutdown() -> None:
    """关闭 tdxdata 长连接（幂等，供 server lifespan / atexit 调用）。"""
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        try:
            with _call_lock:
                client.close()
            logger.info("tdxdata client closed")
        except Exception as exc:  # noqa: BLE001
            logger.debug("tdxdata client close failed: %s", exc)


def tdx_ping(probe: bool = True) -> dict:
    """tdxdata 集成探针。

    Args:
        probe: True 时做 4 个轻量真实调用（bars/quotes/breadth/product）；
               False 时只返回版本/产品目录等静态信息，不联网。

    Returns:
        dict：包含 enabled/version/products/domains/checks/elapsed_ms。
        任一真实调用失败不会抛出，而是记录到 ``checks`` 中，便于冒烟脚本分级。
    """
    started = time.time()
    result: dict[str, Any] = {
        "enabled": is_enabled(),
        "version": "",
        "products": 0,
        "domains": 0,
        "checks": [],
        "elapsed_ms": 0.0,
    }
    if not is_enabled():
        result["elapsed_ms"] = round((time.time() - started) * 1000, 1)
        return result

    try:
        tdxdata = _import_tdxdata()
        result["version"] = getattr(tdxdata, "__version__", "")
        client = get_client()
        if not probe:
            products = client.products()
            result["products"] = len(products)
            result["domains"] = len(client.domains())
            result["elapsed_ms"] = round((time.time() - started) * 1000, 1)
            return result
    except Exception as exc:  # noqa: BLE001
        result["checks"].append(
            {"name": "init", "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        )
        result["elapsed_ms"] = round((time.time() - started) * 1000, 1)
        return result

    def _check(name: str, fn: Callable[[], Any], describe: Callable[[Any], Any]):
        t0 = time.time()
        try:
            value = fn()
            result["checks"].append(
                {
                    "name": name,
                    "status": "OK",
                    "detail": describe(value),
                    "elapsed_ms": round((time.time() - t0) * 1000, 1),
                }
            )
        except Exception as exc:  # noqa: BLE001
            result["checks"].append(
                {
                    "name": name,
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": round((time.time() - t0) * 1000, 1),
                }
            )

    _check(
        "bars",
        lambda: call("get_bars", "600519", period="day", count=1),
        lambda bars: {
            "rows": len(bars),
            "last_date": bars[-1].date if bars else None,
            "last_close": bars[-1].close if bars else None,
        },
    )
    _check(
        "quotes",
        lambda: call("get_quotes", ["600519"]),
        lambda quotes: {
            "rows": len(quotes),
            "code": quotes[0].code if quotes else None,
            "name": quotes[0].name if quotes else None,
            "price": quotes[0].price if quotes else None,
        },
    )
    _check(
        "market_breadth",
        lambda: call("get_market_breadth", "summary"),
        lambda table: {
            "shape": list(table.shape),
            "row": dict(table.rows[0]) if len(table.rows) else None,
        },
    )
    _check(
        "product_holiday",
        lambda: call("get", "holiday"),
        lambda ds: {"shape": list(ds.table.shape), "name": ds.name},
    )

    result["elapsed_ms"] = round((time.time() - started) * 1000, 1)
    return result


# ══════════════════════════════════════════════════════════════════════
# Phase 2：SmartRouter fetch_fn（备源）
# ══════════════════════════════════════════════════════════════════════

# 周期别名 → tdxdata 协议周期。
# ⚠️ tdxdata 的季线字符串是 "season"（不是 "quarter"）；年线/3 分钟无数据，
# 必须显式拒绝，禁止默认值兜底（v3.3.30 H 组教训）。
_PERIOD_ALIASES: dict[str, str] = {
    "daily": "day", "day": "day", "d": "day",
    "weekly": "week", "week": "week", "w": "week",
    "monthly": "month", "month": "month", "m": "month", "1mo": "month",
    "quarterly": "season", "quarter": "season", "season": "season",
    "q": "season", "1q": "season",
    "1m": "1m", "1min": "1m", "min1": "1m",
    "5m": "5m", "5min": "5m", "min5": "5m",
    "15m": "15m", "15min": "15m", "min15": "15m",
    "30m": "30m", "30min": "30m", "min30": "30m",
    "60m": "60m", "60min": "60m", "min60": "60m",
}
_MINUTE_PERIODS = frozenset({"1m", "5m", "15m", "30m", "60m"})
_ADJUST_ALIASES: dict[str, str] = {
    "qfq": "QFQ", "hfq": "HFQ",
    "": "NONE", "none": "NONE", "bfq": "NONE", "no": "NONE",
}
_KLINE_COLUMNS = [
    "日期", "股票代码", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]
# 未给 count/start_date 时的默认请求根数（与工具层 max_rows=500 对齐）
_COUNT_DEFAULTS = {
    "day": 500, "week": 260, "month": 120, "season": 60,
    "1m": 240, "5m": 240, "15m": 240, "30m": 240, "60m": 240,
}
# 单次请求硬上限：防止 start_date 很早时一次翻几十页（2.2s/页）
_COUNT_CAPS = {
    "day": 1500, "week": 1000, "month": 500, "season": 200,
    "1m": 800, "5m": 1000, "15m": 1000, "30m": 1000, "60m": 1000,
}
# 每个日历日的理论根数（用于 start_date → count 估算）
_BARS_PER_DAY = {
    "day": 1.6, "week": 1 / 4.375, "month": 1 / 30.4, "season": 1 / 91.25,
    "1m": 240.0, "5m": 48.0, "15m": 16.0, "30m": 8.0, "60m": 4.0,
}


def _normalize_period(period: Any) -> str:
    """周期别名 → tdxdata 协议周期；未知/无数据周期**直接抛错**。"""
    key = str(period if period is not None else "daily").strip().lower()
    if key in _PERIOD_ALIASES:
        return _PERIOD_ALIASES[key]
    raise ValueError(
        f"tdxdata K 线不支持周期 {period!r}；支持 day/week/month/season/"
        "1m/5m/15m/30m/60m（年线、3 分钟无数据）"
    )


def _normalize_adjust(adjust: Any) -> str:
    """复权别名 → tdxdata Adjust 名；未知**直接抛错**。

    默认空串 = 不复权：与 eltdx 主源（不认 adjust 参数）在“未显式传复权”时保持一致；
    `get_historical_price` 等调用方会显式传 qfq/hfq。
    """
    key = str(adjust if adjust is not None else "").strip().lower()
    if key in _ADJUST_ALIASES:
        return _ADJUST_ALIASES[key]
    raise ValueError(
        f"tdxdata K 线不支持复权 {adjust!r}；支持 qfq/hfq/''（不复权）"
    )


def _ymd(value: Any) -> str:
    """YYYYMMDD / YYYY-MM-DD → YYYYMMDD；空串返回空串；非法格式抛错。"""
    s = str(value or "").strip().replace("-", "").replace("/", "")
    if not s:
        return ""
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD（收到 {value!r}）")
    return s


def _resolve_count(period: str, start_date: str, count: Any) -> int:
    """决定向 tdxdata 请求多少根 K 线。

    - 有 start_date：按日历天估算，且 **count 只是下限**（日期范围优先）；
      否则 live_smoke 的通用参数池 count=5 会把 9 月的区间截成最近 5 根。
    - 无 start_date、count>0：请求 count+1（多 1 根用于首行涨跌幅/振幅的前收）
    - 都没有：用 _COUNT_DEFAULTS（与工具层输出上限对齐）
    """
    try:
        c = int(count or 0)
    except (TypeError, ValueError):
        c = 0
    cap = _COUNT_CAPS[period]
    start = _ymd(start_date)
    if start:
        try:
            start_dt = datetime.strptime(start, "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError(f"start_date 非法：{start_date!r}") from exc
        days = max(1, (datetime.now().date() - start_dt).days)
        est = int(days * _BARS_PER_DAY[period]) + 10
        wanted = max(c + 1, est) if c > 0 else est
        return max(2, min(wanted, cap))
    if c > 0:
        return max(2, min(c + 1, cap))
    return _COUNT_DEFAULTS[period]


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_bar_date(bar: Any, period: str) -> str:
    raw = str(getattr(bar, "date", "") or "")
    if len(raw) == 8 and raw.isdigit():
        text = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    else:
        text = raw
    if period in _MINUTE_PERIODS:
        t = str(getattr(bar, "time", "") or "")
        if t:
            text = f"{text} {t}" if text else t
    return text


def _bar_row(bar: Any, period: str, prev_close: float | None) -> dict:
    """tdxdata Bar → 中文列名行（与 akshare 12 列口径对齐）。

    换手率 tdxdata 不提供 → None（不填 0）；
    振幅/涨跌幅/涨跌额用**上一根收盘**计算，首根无前收 → None。
    """
    open_ = _to_float(getattr(bar, "open", None))
    close = _to_float(getattr(bar, "close", None))
    high = _to_float(getattr(bar, "high", None))
    low = _to_float(getattr(bar, "low", None))
    amplitude = (
        (high - low) / prev_close * 100.0
        if high is not None and low is not None and prev_close and prev_close > 0
        else None
    )
    change = (
        close - prev_close
        if close is not None and prev_close is not None
        else None
    )
    change_pct = (
        change / prev_close * 100.0
        if change is not None and prev_close and prev_close != 0
        else None
    )
    return {
        "日期": _format_bar_date(bar, period),
        "股票代码": str(getattr(bar, "code", "") or ""),
        "开盘": open_,
        "收盘": close,
        "最高": high,
        "最低": low,
        "成交量": _to_float(getattr(bar, "vol", None)),
        "成交额": _to_float(getattr(bar, "amount", None)),
        "振幅": round(amplitude, 4) if amplitude is not None else None,
        "涨跌幅": round(change_pct, 4) if change_pct is not None else None,
        "涨跌额": round(change, 4) if change is not None else None,
        "换手率": None,  # tdxdata 7615 不提供换手率；不填 0
    }


def fetch_historical_kline(
    symbol: str = "",
    code: str = "",
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    adjust: str = "",
    count: int = 0,
    **kwargs,
) -> pd.DataFrame:
    """历史 K 线（tdxdata 7615 备源，priority=300）。

    兼容 SmartRouter 的 symbol/code 参数名；输出 12 列与 akshare 口径一致：
      日期 / 股票代码 / 开盘 / 收盘 / 最高 / 最低 /
      成交量 / 成交额 / 振幅 / 涨跌幅 / 涨跌额 / 换手率

    ⚠️ 换手率恒为 None（上游不提供）；振幅/涨跌幅/涨跌额由上一根收盘自算，
    首根（无前收）为 None。周期/复权未识别一律抛 ValueError，不回落默认值。
    """
    sym = (symbol or code or "").strip()
    if not sym:
        raise RuntimeError("historical_kline(tdxdata): symbol/code is required")
    p = _normalize_period(period)
    adj = _normalize_adjust(adjust)
    req_count = _resolve_count(p, start_date, count)

    bars = call("get_bars", sym, period=p, count=req_count, adjust=adj)
    if not bars:
        raise RuntimeError(
            f"historical_kline(tdxdata): 无 K 线数据（{sym}, period={p}, "
            f"count={req_count}）"
        )

    start = _ymd(start_date)
    end = _ymd(end_date)
    ordered = sorted(
        bars, key=lambda b: (str(getattr(b, "date", "") or ""), str(getattr(b, "time", "") or ""))
    )
    rows: list[dict] = []
    prev_close: float | None = None
    for bar in ordered:
        row = _bar_row(bar, p, prev_close)
        key = str(getattr(bar, "date", "") or "")
        if (not start or key >= start) and (not end or key <= end):
            rows.append(row)
        prev_close = _to_float(getattr(bar, "close", None))

    if not rows:
        raise RuntimeError(
            f"historical_kline(tdxdata): 日期范围内无数据"
            f"（{sym}, {start_date or '...'}~{end_date or '...'}）"
        )

    df = pd.DataFrame(rows, columns=_KLINE_COLUMNS)
    # count 语义：无日期范围时只返回最近 count 根（与 eltdx 一致）
    if count and not start and not end:
        c = int(count)
        if c > 0 and len(df) > c:
            df = df.tail(c).reset_index(drop=True)
    return df


def fetch_minute_data(symbol: str = "", code: str = "", **kwargs) -> pd.DataFrame:
    """当日分时（tdxdata 7615 备源，priority=300）。

    输出列与 eltdx 源一致：时间 / 价格 / 均价 / 成交量。
    tdxdata 只支持当日，无日期参数。
    """
    sym = (symbol or code or "").strip()
    if not sym:
        raise RuntimeError("minute_data(tdxdata): symbol/code is required")
    points = call("get_minutes", sym)
    if not points:
        raise RuntimeError(f"minute_data(tdxdata): 无分时数据（{sym}）")
    rows = [
        {
            "时间": str(getattr(p, "time", "") or ""),
            "价格": _to_float(getattr(p, "price", None)),
            "均价": _to_float(getattr(p, "avg_price", None)),
            "成交量": _to_float(getattr(p, "vol", None)),
        }
        for p in points
    ]
    return pd.DataFrame(rows, columns=["时间", "价格", "均价", "成交量"])


def fetch_market_breadth(**kwargs) -> pd.DataFrame:
    """全市场实时涨跌家数（tdxdata 7615 备源，priority=100）。

    输出列与东财 push2ex 源一致：上涨 / 下跌 / 平盘 / 涨停 / 跌停。
    """
    table = call("get_market_breadth", "summary")
    rows = getattr(table, "rows", None) or []
    if not rows:
        raise RuntimeError("market_breadth(tdxdata): 返回空表")
    row = rows[0]
    keys = ("up_count", "down_count", "flat_count", "limit_up_count", "limit_down_count")
    missing = [k for k in keys if row.get(k) is None]
    if missing:
        raise RuntimeError(f"market_breadth(tdxdata): 缺字段 {missing}")
    return pd.DataFrame(
        [
            {
                "上涨": int(row["up_count"]),
                "下跌": int(row["down_count"]),
                "平盘": int(row["flat_count"]),
                "涨停": int(row["limit_up_count"]),
                "跌停": int(row["limit_down_count"]),
            }
        ]
    )


# ── Phase 3：F10/财报/分红/当日逐笔备源 ────────────────────────────────

_FINANCE_KIND_MAP: dict[str, str] = {
    # 通达信 eltdx 的 report_type → tdxdata get_finance kind
    "zcfzb": "zcfz", "zcfz": "zcfz", "balance": "zcfz",
    "lrb": "lyb", "lyb": "lyb", "income": "lyb",
    "xjllb": "xjllb", "cash": "xjllb", "cashflow": "xjllb",
}


def _table_to_frame(table: Any, *, what: str) -> pd.DataFrame:
    """tdxdata Table → DataFrame（cols 与行严格对齐，丢弃 raw sidecar）。"""
    rows = getattr(table, "rows", None) or []
    if not rows:
        raise RuntimeError(f"{what}: 返回空表")
    cols = list(getattr(table, "cols", None) or [])
    data = [dict(r) for r in rows]
    return pd.DataFrame(data, columns=cols) if cols else pd.DataFrame(data)


def fetch_finance_report(
    code: str = "", symbol: str = "", report_type: str = "zcfzb", **kwargs
) -> pd.DataFrame:
    """财务报表（tdxdata 备源，用于 eltdx `finance_report` 的兜底）。

    `report_type`: zcfzb/lrb/xjllb（兼容 zcfz/lyb/balance/income/cash 别名）。
    返回 tdxdata `get_finance` 的标准英文字段表；未知类型抛 ValueError。
    """
    sym = (code or symbol or "").strip()
    if not sym:
        raise RuntimeError("finance_report(tdxdata): code/symbol is required")
    key = str(report_type or "zcfzb").strip().lower()
    kind = _FINANCE_KIND_MAP.get(key)
    if kind is None:
        raise ValueError(
            f"tdxdata finance_report 不支持 report_type={report_type!r}；"
            "支持 zcfzb/lrb/xjllb"
        )
    table = call("get_finance", sym, kind=kind)
    return _table_to_frame(table, what=f"finance_report(tdxdata, {sym}, {kind})")


# tdxdata 逐笔单页 100 笔、约 2.2s/页；上限 1000 笔 ≈ 10 页 ≈ 22s，
# 与工具层 30s 路由超时匹配。超过 1000 的 count 会被截断（工具层会标注来源）。
_TDX_TICKS_MAX = 1000


def fetch_today_ticks(
    code: str = "", symbol: str = "", count: int = 2000, **kwargs
) -> pd.DataFrame:
    """当日逐笔（tdxdata 备源，用于 eltdx `today_ticks` 的兜底）。

    ⚠️ tdxdata 逐笔**无买卖方向**（方向固定 "unknown"）、无开盘撮合标记；
    `成交额` 由 price × vol(手) × 100 推算。只支持当日，最多 1000 笔。
    输出列与 eltdx `_tick_to_row` 对齐（缺失字段为 None/unknown）。
    """
    sym = (code or symbol or "").strip()
    if not sym:
        raise RuntimeError("today_ticks(tdxdata): code/symbol is required")
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 2000
    n = max(1, min(n, _TDX_TICKS_MAX))
    ticks = call("get_ticks", sym, count=n)
    if not ticks:
        raise RuntimeError(f"today_ticks(tdxdata): 无当日逐笔（{sym}）")
    rows = []
    for t in ticks:
        price = _to_float(getattr(t, "price", None))
        vol = _to_float(getattr(t, "vol", None))
        amount = (
            price * vol * 100.0
            if price is not None and vol is not None
            else None
        )
        rows.append(
            {
                "时间": str(getattr(t, "time", "") or ""),
                "价格": price,
                "成交量": vol,
                "成交额": amount,
                "方向": "unknown",
                "是否开盘撮合": False,
                "是否竞价快照": None,
                "竞价匹配量": None,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "时间", "价格", "成交量", "成交额", "方向",
            "是否开盘撮合", "是否竞价快照", "竞价匹配量",
        ],
    )


__all__ = [
    "is_enabled", "get_client", "call", "tdx_ping", "_shutdown",
    "fetch_historical_kline", "fetch_minute_data", "fetch_market_breadth",
    "fetch_finance_report", "fetch_today_ticks",
]
