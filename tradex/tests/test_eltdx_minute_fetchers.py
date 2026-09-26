"""
eltdx fetcher 层参数透传与语义测试（v3.3.30）。

覆盖本次新增/改动：
  - fetch_historical_kline：分钟周期透传、'1m' 语义（回归守卫）、未识别周期先抛错
  - fetch_adjusted_kline：分钟复权 + all_pages/max_pages/page_size + 跨页升序重排
  - fetch_full_kline：adjust/page_size/max_pages + 跨页升序重排
  - fetch_security_codes：kind 品种分派（stock/a_share/etf/index/st/suspended）
  - fetch_recent_minute：空结果不抛错（不污染源健康评分）

全部用假 client，不触网。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest


def _bar(dt: str, close: float = 1.0):
    """构造一个 KlineBar 替身。"""
    return SimpleNamespace(
        time=datetime.fromisoformat(dt),
        open=1.0, high=2.0, low=0.5, close=close,
        volume_lots=100.0, amount=1000.0,
    )


def _point(label: str, price: float = 10.0):
    """构造一个 IntradayPoint 替身。"""
    return SimpleNamespace(time_label=label, time=None, price=price, avg_price=10.1, volume=5.0)


def _sec(full_code: str, name: str, category: str, board: str = "main"):
    """构造一个 SecurityCode 替身。"""
    return SimpleNamespace(
        full_code=full_code, code=full_code[2:], exchange=full_code[:2],
        name=name, category=category, board=board,
    )


class _Call:
    """记录一次调用的 (args, kwargs) 并返回预设结果。"""

    def __init__(self, result=None):
        self.result = result
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    @property
    def last_kwargs(self) -> dict:
        return self.calls[-1][1]


class _RaisingCall:
    """被调用即抛错（用于断言「不该走到这里」）。"""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.called = False

    def __call__(self, *args, **kwargs):
        self.called = True
        raise self.exc


def _install(monkeypatch, *, bars_get=None, minutes_history=None, minutes_recent=None,
             codes=None):
    """装配假 eltdx client 并注入 eltdx_fetchers._get_client。"""
    from tradex.data_sources import eltdx_fetchers as ef

    codes_ns = SimpleNamespace(
        all_markets=lambda: [],
        all=lambda market: [],
        stocks=SimpleNamespace(),
    )
    # 用可调用对象填充 codes 命名空间（按需覆盖）
    codes_ns = SimpleNamespace(**(codes or {}))

    client = SimpleNamespace(
        bars=SimpleNamespace(get=bars_get or _Call(SimpleNamespace(bars=()))),
        minutes=SimpleNamespace(
            history=minutes_history or _Call(SimpleNamespace(points=())),
            recent=minutes_recent or _Call(SimpleNamespace(points=())),
        ),
        codes=codes_ns,
    )
    monkeypatch.setattr(ef, "_get_client", lambda: client)
    return ef, client


# ─────────────────────────────────────────────
# fetch_historical_kline
# ─────────────────────────────────────────────

class TestHistoricalKline:
    def test_minute_period_passed_through_with_time_precision(self, monkeypatch):
        """5m 必须直接打到 eltdx（此前被拒绝 → 降级 akshare 返回日线）。"""
        get = _Call(SimpleNamespace(bars=(
            _bar("2026-09-24 14:40"), _bar("2026-09-24 14:45"),
        )))
        ef, _ = _install(monkeypatch, bars_get=get)

        df = ef.fetch_historical_kline(code="920185", period="5m", count=2)

        assert get.calls[-1][1]["period"] == "5m"
        assert get.calls[-1][1]["count"] == 2
        assert get.calls[-1][0][0] == "bj920185"          # 代码归一化
        assert list(df["日期"]) == ["2026-09-24 14:40", "2026-09-24 14:45"]
        # 分钟粒度必须保留时分（不能被截成日期）
        assert all(":" in d for d in df["日期"])

    def test_1m_is_one_minute_regression_guard(self, monkeypatch):
        """P0 回归守卫：'1m' 打到 eltdx 时必须是 1m，不能变成 month。"""
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 14:58"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        ef.fetch_historical_kline(code="920185", period="1m", count=1)

        assert get.calls[-1][1]["period"] == "1m"
        assert get.calls[-1][1]["period"] != "month"

    def test_unknown_period_raises_before_touching_client(self, monkeypatch):
        """未识别周期必须在调用 client 之前抛错（不给上层任何静默回落的窗口）。"""
        get = _RaisingCall(AssertionError("client 不应被调用"))
        ef, _ = _install(monkeypatch, bars_get=get)

        with pytest.raises(ValueError):
            ef.fetch_historical_kline(code="920185", period="badxyz", count=5)
        assert get.called is False

    def test_day_period_still_works(self, monkeypatch):
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 15:00"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        df = ef.fetch_historical_kline(code="920185", period="daily", count=1)

        assert get.calls[-1][1]["period"] == "day"
        assert len(df) == 1


# ─────────────────────────────────────────────
# fetch_adjusted_kline
# ─────────────────────────────────────────────

class TestAdjustedKline:
    def test_minute_adjusted_supported(self, monkeypatch):
        """分钟复权此前是硬失败（独占源 + 周期被拒），现在必须可用。"""
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 14:50"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        df = ef.fetch_adjusted_kline(code="920185", period="5m", adjust="qfq", count=1)

        kw = get.calls[-1][1]
        assert kw["period"] == "5m"
        assert kw["adjust"] == "qfq"
        assert kw["all_pages"] is False
        assert kw["max_pages"] is None          # 单页模式不应传页上限
        assert df["日期"].iloc[0] == "2026-09-24 14:50"

    def test_all_pages_sorts_ascending(self, monkeypatch):
        """跨页合并结果必须按时间升序重排（eltdx 分页序为「最近页→更早页」）。"""
        get = _Call(SimpleNamespace(bars=(
            _bar("2026-09-24 15:00"), _bar("2026-09-23 15:00"), _bar("2026-09-22 15:00"),
        )))
        ef, _ = _install(monkeypatch, bars_get=get)

        df = ef.fetch_adjusted_kline(
            code="920185", period="day", adjust="hfq",
            all_pages=True, max_pages=60, page_size=400,
        )

        kw = get.calls[-1][1]
        assert kw["all_pages"] is True
        assert kw["max_pages"] == 60
        assert kw["page_size"] == 400
        assert list(df["日期"]) == sorted(df["日期"])

    def test_anchor_date_forwarded_when_given(self, monkeypatch):
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 15:00"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        ef.fetch_adjusted_kline(code="920185", period="day", adjust="fixed_qfq",
                                anchor_date="2024-06-03")

        assert get.calls[-1][1]["anchor_date"] == "2024-06-03"


# ─────────────────────────────────────────────
# fetch_full_kline
# ─────────────────────────────────────────────

class TestFullKline:
    def test_minute_full_kline_sorts_and_forwards_paging(self, monkeypatch):
        get = _Call(SimpleNamespace(bars=(
            _bar("2026-09-24 15:00"), _bar("2026-05-15 09:31"),
        )))
        ef, _ = _install(monkeypatch, bars_get=get)

        df = ef.fetch_full_kline(code="920185", period="1m", adjust="qfq",
                                 max_pages=80, page_size=800)

        kw = get.calls[-1][1]
        assert kw["period"] == "1m"             # 与 historical_kline 口径统一
        assert kw["adjust"] == "qfq"
        assert kw["all_pages"] is True
        assert kw["max_pages"] == 80
        assert kw["page_size"] == 800
        assert list(df["日期"]) == ["2026-05-15 09:31", "2026-09-24 15:00"]

    def test_empty_adjust_becomes_none(self, monkeypatch):
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 15:00"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        ef.fetch_full_kline(code="920185", period="day")

        assert get.calls[-1][1]["adjust"] is None

    def test_default_max_pages_is_200(self, monkeypatch):
        """默认值由 30 放宽到 SDK 默认 200（1 分钟全量约需 28 页）。"""
        get = _Call(SimpleNamespace(bars=(_bar("2026-09-24 15:00"),)))
        ef, _ = _install(monkeypatch, bars_get=get)

        ef.fetch_full_kline(code="920185", period="day")

        assert get.calls[-1][1]["max_pages"] == 200


# ─────────────────────────────────────────────
# fetch_security_codes —— kind 品种分派
# ─────────────────────────────────────────────

class TestSecurityCodesKind:
    def test_kind_stock_market_all_merges_three_markets(self, monkeypatch):
        calls = {}

        def _stocks(market):
            calls.setdefault("markets", []).append(market)
            return [_sec(f"{market}600000", f"{market}-A", "a_share")]

        ef, _ = _install(monkeypatch, codes={"stocks": _stocks})

        df = ef.fetch_security_codes(market="all", kind="stock")

        assert calls["markets"] == ["sh", "sz", "bj"]
        assert len(df) == 3
        assert list(df.columns) == ["代码", "名称", "类别", "板块"]

    def test_kind_stock_single_market(self, monkeypatch):
        def _stocks(market):
            assert market == "bj"
            return [_sec("bj920000", "安徽凤凰", "a_share", "bse_listed_stock")]

        ef, _ = _install(monkeypatch, codes={"stocks": _stocks})

        df = ef.fetch_security_codes(market="bj", kind="stock")

        assert len(df) == 1
        assert df["代码"].iloc[0] == "bj920000"
        assert df["板块"].iloc[0] == "bse_listed_stock"

    def test_kind_suspended_returns_code_only(self, monkeypatch):
        def _suspended(market):
            assert market is None            # 'all' 走 SDK 的 None 语义
            return ["sh600001", "sz000002"]

        ef, _ = _install(monkeypatch, codes={"latest_suspended": _suspended})

        df = ef.fetch_security_codes(market="all", kind="suspended")

        assert list(df.columns) == ["代码"]
        assert list(df["代码"]) == ["sh600001", "sz000002"]

    def test_kind_etf_and_index_and_st_dispatch(self, monkeypatch):
        ef, _ = _install(monkeypatch, codes={
            "etfs": lambda m: [_sec("sh510300", "沪深300ETF", "etf")],
            "indices": lambda m: [_sec("sh000001", "上证指数", "index")],
            "latest_st": lambda m: [_sec("sz000004", "ST国华", "a_share")],
        })

        assert ef.fetch_security_codes(market="sh", kind="etf")["类别"].iloc[0] == "etf"
        assert ef.fetch_security_codes(market="sh", kind="index")["代码"].iloc[0] == "sh000001"
        assert ef.fetch_security_codes(market="all", kind="st")["名称"].iloc[0] == "ST国华"

    def test_invalid_kind_raises(self, monkeypatch):
        ef, _ = _install(monkeypatch, codes={"stocks": lambda m: []})

        with pytest.raises(ValueError):
            ef.fetch_security_codes(market="bj", kind="not_a_kind")

    def test_no_kind_falls_back_to_all_categories(self, monkeypatch):
        """kind 为空时保持旧行为（全部品种），向后兼容。"""
        ef, _ = _install(monkeypatch, codes={
            "all_markets": lambda: [
                _sec("bj899050", "北证50", "index"),
                _sec("bj920000", "安徽凤凰", "a_share"),
            ],
        })

        df = ef.fetch_security_codes(market="all")

        assert len(df) == 2
        assert set(df["类别"]) == {"index", "a_share"}


# ─────────────────────────────────────────────
# 分时三件套
# ─────────────────────────────────────────────

class TestMinuteFetchers:
    def test_recent_minute_empty_does_not_raise(self, monkeypatch):
        """独占源：空结果不能抛错，否则会被记为源失败、污染健康评分。"""
        recent = _Call(SimpleNamespace(points=()))
        ef, _ = _install(monkeypatch, minutes_recent=recent)

        df = ef.fetch_recent_minute(code="920185")

        assert df.empty
        assert list(df.columns) == ["时间", "价格", "均价", "成交量"]
        assert recent.calls[-1][0][0] == "bj920185"

    def test_recent_minute_returns_points(self, monkeypatch):
        recent = _Call(SimpleNamespace(points=(_point("09:31"), _point("09:32"))))
        ef, _ = _install(monkeypatch, minutes_recent=recent)

        df = ef.fetch_recent_minute(code="920185", trading_date="2026-09-18")

        assert list(df["时间"]) == ["09:31", "09:32"]
        # 日期归一化为纯数字交给 SDK
        assert recent.calls[-1][0][1] == "20260918"

    def test_minute_history_time_label_fallback(self, monkeypatch):
        """time_label 缺失时回落到 time（与 recent 共用行构造）。"""
        pt = SimpleNamespace(time_label=None, time="09:31:00", price=1.0,
                             avg_price=1.0, volume=1.0)
        hist = _Call(SimpleNamespace(points=(pt,)))
        ef, _ = _install(monkeypatch, minutes_history=hist)

        df = ef.fetch_minute_history(code="920185", trading_date="2026-09-18")

        assert df["时间"].iloc[0] == "09:31:00"

    def test_minute_history_empty_still_raises(self, monkeypatch):
        """指定日历史分时空 → 仍按「源失败」处理（与 recent 的策略有意不同）。"""
        hist = _Call(SimpleNamespace(points=()))
        ef, _ = _install(monkeypatch, minutes_history=hist)

        with pytest.raises(RuntimeError):
            ef.fetch_minute_history(code="920185", trading_date="2026-09-18")


# ─────────────────────────────────────────────
# page_size 护栏
# （v3.3.30 回归守卫：live_smoke 把 page_size 传成 5 → 误报成代码回归）
# ─────────────────────────────────────────────

class TestPageSizeClamp:
    """背景：all_pages 全量拉取时 page_size 越小往返越多，过小还会让**总页数**
    撞穿 max_pages 直接失败。
    实测 fetch_full_kline(page_size=5) → 日线 6012 根需 1203 页 ≫ max_pages=200
        → RuntimeError: bars.get reached max_pages before the server returned an empty page
    该现象曾在 live_smoke 中被误判为「代码回归」，实为夹具把 page_size 传成了 5。
    护栏只影响分页效率，**不改变最终返回的数据集合**。
    """

    @pytest.mark.parametrize("raw,expected", [
        (5, 100), (50, 100), (99, 100),           # 过小 → 抬到下限
        (100, 100), (500, 500), (800, 800),       # 区间内 → 原样透传
        (1000, 800),                              # 超上限 → 压到 800
        (0, 800), (-1, 800),                      # 非法 → SDK 默认
        (None, 800), ("", 800), ("abc", 800),
        ("800", 800),
    ])
    def test_clamp_table(self, raw, expected):
        from tradex.data_sources import eltdx_fetchers as ef

        assert ef._clamp_page_size(raw) == expected

    def test_full_kline_clamps_small_page_size(self, monkeypatch):
        """回归守卫：page_size=5 必须被抬到 100 才交给 SDK。"""
        bars_get = _Call(SimpleNamespace(bars=(_bar("2026-09-01T15:00:00"),)))
        ef, _ = _install(monkeypatch, bars_get=bars_get)

        ef.fetch_full_kline(code="600519", period="day", page_size=5)

        assert bars_get.last_kwargs["page_size"] == 100
        assert bars_get.last_kwargs["all_pages"] is True

    def test_adjusted_kline_clamps_small_page_size(self, monkeypatch):
        """同一护栏在复权全量接口上同样生效。"""
        bars_get = _Call(SimpleNamespace(bars=(_bar("2026-09-01T15:00:00"),)))
        ef, _ = _install(monkeypatch, bars_get=bars_get)

        ef.fetch_adjusted_kline(code="600519", period="day", page_size=1, all_pages=True)

        assert bars_get.last_kwargs["page_size"] == 100

    def test_default_page_size_unchanged(self, monkeypatch):
        """不传 page_size 时保持 SDK 默认 800 —— 护栏不得改变默认行为。"""
        bars_get = _Call(SimpleNamespace(bars=(_bar("2026-09-01T15:00:00"),)))
        ef, _ = _install(monkeypatch, bars_get=bars_get)

        ef.fetch_full_kline(code="600519", period="day")

        assert bars_get.last_kwargs["page_size"] == 800
