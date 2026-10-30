"""Phase 0: tdxdata_fetchers 客户端管理守卫（离线，不联网）。"""

from __future__ import annotations

import pytest

from tradex.config import config
from tradex.data_sources import tdxdata_fetchers as tdf


@pytest.fixture(autouse=True)
def _reset_client():
    """每个用例前后关闭/清空全局客户端，避免状态串扰。"""
    tdf._shutdown()
    yield
    tdf._shutdown()


def test_enabled_by_default():
    assert tdf.is_enabled() is True


def test_disabled_by_env(monkeypatch):
    monkeypatch.setattr(config, "TDXDATA_ENABLED", False)
    assert tdf.is_enabled() is False
    with pytest.raises(RuntimeError, match="disabled"):
        tdf.get_client()
    ping = tdf.tdx_ping(probe=False)
    assert ping["enabled"] is False
    assert ping["checks"] == []


def test_client_singleton():
    c1 = tdf.get_client()
    c2 = tdf.get_client()
    assert c1 is c2
    assert len(c1.products()) == 421


def test_ping_static_no_network():
    ping = tdf.tdx_ping(probe=False)
    assert ping["enabled"] is True
    assert ping["version"] == "0.3.0"
    assert ping["products"] == 421
    assert ping["domains"] == 17
    assert ping["checks"] == []


def test_shutdown_idempotent_and_recreatable():
    c1 = tdf.get_client()
    assert tdf._client is c1
    tdf._shutdown()
    assert tdf._client is None
    tdf._shutdown()  # 幂等
    c2 = tdf.get_client()
    assert c2 is not None
    assert c2 is not c1


# ══════════════════════════════════════════════════════════════════════
# Phase 2: SmartRouter fetch_fn
# ══════════════════════════════════════════════════════════════════════

class FakeBar:
    def __init__(
        self, date, time="", open_=10.0, high=11.0, low=9.0, close=10.5,
        vol=1000.0, amount=100000.0, code="600519",
    ):
        self.date = date
        self.time = time
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.vol = vol
        self.amount = amount
        self.code = code


class FakeMinute:
    def __init__(self, time, price, avg_price, vol):
        self.time = time
        self.price = price
        self.avg_price = avg_price
        self.vol = vol


class FakeBreadthTable:
    def __init__(self, row):
        self.rows = [row]


class FakeDataTable:
    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows


class FakeTick:
    def __init__(self, time, price, vol):
        self.time = time
        self.price = price
        self.vol = vol


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("daily", "day"), ("day", "day"), ("weekly", "week"), ("week", "week"),
        ("monthly", "month"), ("month", "month"), ("m", "month"),
        ("quarterly", "season"), ("quarter", "season"), ("season", "season"),
        ("1m", "1m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m"), ("60m", "60m"),
    ],
)
def test_period_aliases(monkeypatch, alias, expected):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["period"] = kwargs["period"]
        return [FakeBar("20261030")]

    monkeypatch.setattr(tdf, "call", fake_call)
    tdf.fetch_historical_kline(symbol="600519", period=alias, count=1)
    assert seen["method"] == "get_bars"
    assert seen["period"] == expected


@pytest.mark.parametrize("bad", ["year", "yearly", "3m", "120m", "foo", ""])
def test_unknown_period_raises(monkeypatch, bad):
    called = []
    monkeypatch.setattr(tdf, "call", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError):
        tdf.fetch_historical_kline(symbol="600519", period=bad)
    assert called == []


@pytest.mark.parametrize(
    "alias,expected",
    [("qfq", "QFQ"), ("hfq", "HFQ"), ("", "NONE"), ("none", "NONE"), ("bfq", "NONE")],
)
def test_adjust_aliases(monkeypatch, alias, expected):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["adjust"] = kwargs["adjust"]
        return [FakeBar("20261030")]

    monkeypatch.setattr(tdf, "call", fake_call)
    tdf.fetch_historical_kline(symbol="600519", adjust=alias, count=1)
    assert seen["adjust"] == expected


def test_unknown_adjust_raises(monkeypatch):
    called = []
    monkeypatch.setattr(tdf, "call", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError):
        tdf.fetch_historical_kline(symbol="600519", adjust="bad")
    assert called == []


def test_historical_kline_schema_and_derived(monkeypatch):
    bars = [
        FakeBar("20261001", close=10.0, high=10.5, low=9.5),
        FakeBar("20261002", close=11.0, high=11.5, low=10.5),
        FakeBar("20261003", close=12.0, high=12.5, low=11.5),
        FakeBar("20261004", close=13.0, high=13.5, low=12.5),
    ]
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["count"] = kwargs["count"]
        return bars

    monkeypatch.setattr(tdf, "call", fake_call)
    df = tdf.fetch_historical_kline(symbol="600519", period="daily", count=3)
    assert seen["count"] == 4  # count+1 用于首行前收
    assert df.columns.tolist() == tdf._KLINE_COLUMNS
    assert len(df) == 3
    first = df.iloc[0]
    assert first["日期"] == "2026-10-02"
    assert first["股票代码"] == "600519"
    assert first["涨跌额"] == pytest.approx(1.0)
    assert first["涨跌幅"] == pytest.approx(10.0)
    assert first["振幅"] == pytest.approx((11.5 - 10.5) / 10.0 * 100.0)
    assert first["换手率"] is None


def test_historical_kline_date_filter(monkeypatch):
    bars = [
        FakeBar("20261001", close=10.0),
        FakeBar("20261002", close=11.0),
        FakeBar("20261003", close=12.0),
    ]
    monkeypatch.setattr(tdf, "call", lambda *a, **k: bars)
    df = tdf.fetch_historical_kline(
        symbol="600519", period="daily", start_date="2026-10-02", end_date="20261003"
    )
    assert df["日期"].tolist() == ["2026-10-02", "2026-10-03"]
    assert df.iloc[0]["涨跌额"] == pytest.approx(1.0)  # 前收来自被过滤的 10-01


def test_historical_kline_count_is_lower_bound_with_start_date(monkeypatch):
    """日期范围优先：live_smoke 通用池 count=5 不得把区间截成最近 5 根。"""
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen.update(kwargs)
        return [FakeBar("20261030")]

    monkeypatch.setattr(tdf, "call", fake_call)
    tdf.fetch_historical_kline(
        symbol="600519", period="daily", start_date="20260901", count=5
    )
    assert seen["count"] > 5


def test_minute_data_schema(monkeypatch):
    points = [
        FakeMinute("09:30", 10.0, 10.0, 100),
        FakeMinute("09:31", 10.1, 10.05, 200),
    ]
    monkeypatch.setattr(tdf, "call", lambda *a, **k: points)
    df = tdf.fetch_minute_data(symbol="600519")
    assert df.columns.tolist() == ["时间", "价格", "均价", "成交量"]
    assert df.iloc[1].tolist() == ["09:31", 10.1, 10.05, 200.0]


def test_market_breadth_schema(monkeypatch):
    table = FakeBreadthTable(
        {
            "up_count": 100, "down_count": 200, "flat_count": 3,
            "limit_up_count": 10, "limit_down_count": 5,
        }
    )
    monkeypatch.setattr(tdf, "call", lambda *a, **k: table)
    df = tdf.fetch_market_breadth()
    assert df.columns.tolist() == ["上涨", "下跌", "平盘", "涨停", "跌停"]
    assert df.iloc[0].tolist() == [100, 200, 3, 10, 5]


def test_market_breadth_missing_field_raises(monkeypatch):
    table = FakeBreadthTable({"up_count": 100, "down_count": 200})
    monkeypatch.setattr(tdf, "call", lambda *a, **k: table)
    with pytest.raises(RuntimeError, match="缺字段"):
        tdf.fetch_market_breadth()


# ── Phase 3 ──────────────────────────────────────────────────────────────


def test_finance_report_maps_kind(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        seen["kind"] = kwargs["kind"]
        return FakeDataTable(["report_period", "revenue_yuan"], [{"report_period": "2026Q3", "revenue_yuan": 1.0}])

    monkeypatch.setattr(tdf, "call", fake_call)
    df = tdf.fetch_finance_report(code="600519", report_type="lrb")
    assert seen["method"] == "get_finance"
    assert seen["args"][0] == "600519"
    assert seen["kind"] == "lyb"
    assert df.columns.tolist() == ["report_period", "revenue_yuan"]
    assert len(df) == 1


@pytest.mark.parametrize(
    "report_type,kind",
    [("zcfzb", "zcfz"), ("zcfz", "zcfz"), ("lrb", "lyb"), ("xjllb", "xjllb"), ("cash", "xjllb")],
)
def test_finance_report_aliases(monkeypatch, report_type, kind):
    seen = {}
    monkeypatch.setattr(
        tdf, "call",
        lambda method, *a, **k: seen.update(k) or FakeDataTable(["a"], [{"a": 1}]),
    )
    tdf.fetch_finance_report(code="600519", report_type=report_type)
    assert seen["kind"] == kind


def test_finance_report_unknown_type_raises(monkeypatch):
    called = []
    monkeypatch.setattr(tdf, "call", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError):
        tdf.fetch_finance_report(code="600519", report_type="bad")
    assert called == []


def test_dividend_financing_not_registered():
    """tdxdata get_xdxr 是 IPO/融资累计统计，不是分红事件表，故不注册为 dividend_financing 备源。"""
    from tradex.data_sources import get_router, register_all_sources

    register_all_sources()
    entries = {
        (r["data_type"], r["source_name"])
        for r in get_router().get_registry_report()
    }
    assert ("dividend_financing", "tdxdata") not in entries


def test_today_ticks_schema_and_cap(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["count"] = kwargs["count"]
        return [
            FakeTick("09:30:00", 10.0, 2.0),
            FakeTick("09:30:01", 10.1, 3.0),
        ]

    monkeypatch.setattr(tdf, "call", fake_call)
    df = tdf.fetch_today_ticks(code="600519", count=5000)
    assert seen["method"] == "get_ticks"
    assert seen["count"] == 1000  # 上限
    assert df.columns.tolist() == [
        "时间", "价格", "成交量", "成交额", "方向",
        "是否开盘撮合", "是否竞价快照", "竞价匹配量",
    ]
    assert df.iloc[0].tolist()[:5] == ["09:30:00", 10.0, 2.0, 2000.0, "unknown"]
    assert not df.iloc[0]["是否开盘撮合"]


def test_backup_registration_present():
    from tradex.data_sources import get_router, register_all_sources

    register_all_sources()
    entries = {
        (r["data_type"], r["source_name"], r["priority"])
        for r in get_router().get_registry_report()
    }
    assert ("market_breadth", "tdxdata", 100) in entries
    assert ("historical_kline", "tdxdata", 300) in entries
    assert ("minute_data", "tdxdata", 300) in entries
    assert ("finance_report", "tdxdata", 100) in entries
    assert ("today_ticks", "tdxdata", 100) in entries


def test_router_falls_back_to_tdxdata(monkeypatch):
    from astock_signals.smart_router import SmartRouter

    def fail(**kwargs):
        raise RuntimeError("primary down")

    bars = [FakeBar("20261030", close=10.0), FakeBar("20261031", close=11.0)]
    monkeypatch.setattr(tdf, "call", lambda *a, **k: bars)

    router = SmartRouter()
    router.register("historical_kline", "primary", fail, priority=1)
    router.register("historical_kline", "tdxdata", tdf.fetch_historical_kline, priority=300)
    df, src = router.route(
        "historical_kline", symbol="600519", period="daily", count=1,
        adjust="qfq", timeout=10,
    )
    assert src == "tdxdata"
    assert df.columns.tolist() == tdf._KLINE_COLUMNS
    assert len(df) == 1
