"""K 组回归：eltdx historical_kline 必须真正支持 start_date/end_date/adjust。

此前这两个参数被 **kwargs 吞掉，日期区间返回最近 100 根（静默错数据）。
本测试用假客户端模拟 bars.get(start=offset) 的「最新页 → 更早页」翻页语义。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from tradex.data_sources import eltdx_fetchers as ef


class FakeBar:
    def __init__(self, d: date):
        self.time = datetime(d.year, d.month, d.day, 15, 0)
        self.open = 10.0
        self.high = 11.0
        self.low = 9.0
        self.close = 10.5
        self.volume_lots = 1000.0
        self.amount = 105000.0


class FakeBarsApi:
    """按 eltdx bars.get(start, count) 语义模拟：start 从最新一页向更早偏移。"""

    def __init__(self, bars):
        self.bars = sorted(bars, key=lambda b: b.time)
        self.calls = []

    def get(self, code, *, period="day", start=0, count=800, adjust=None, **kwargs):
        self.calls.append(
            {"code": code, "period": period, "start": start, "count": count, "adjust": adjust}
        )
        end = len(self.bars) - start
        begin = max(0, end - count)
        return SimpleNamespace(bars=tuple(self.bars[begin:end]))


class FakeClient:
    def __init__(self, bars):
        self.bars = FakeBarsApi(bars)


def _install(monkeypatch, bars):
    client = FakeClient(bars)
    monkeypatch.setattr(ef, "_get_client", lambda: client)
    return client


def _daily_bars(start: date, days: int):
    return [FakeBar(start + timedelta(days=i)) for i in range(days)]


def test_date_range_filters_and_passes_adjust(monkeypatch):
    bars = _daily_bars(date(2026, 7, 1), 120)
    client = _install(monkeypatch, bars)

    df = ef.fetch_historical_kline(
        symbol="600519",
        period="daily",
        start_date="2026-09-15",
        end_date="20260920",
        adjust="qfq",
    )

    assert df["日期"].tolist() == [
        "2026-09-15 15:00", "2026-09-16 15:00", "2026-09-17 15:00",
        "2026-09-18 15:00", "2026-09-19 15:00", "2026-09-20 15:00",
    ]
    assert client.bars.calls[-1]["adjust"] == "qfq"
    assert client.bars.calls[-1]["period"] == "day"


def test_count_is_lower_bound_when_dates_present(monkeypatch):
    """live_smoke 通用参数池 count=5 不得把 9 月区间截成最近 5 根。"""
    bars = _daily_bars(date(2026, 7, 1), 120)
    client = _install(monkeypatch, bars)

    df = ef.fetch_historical_kline(
        symbol="600519", period="daily", start_date="20260901", count=5
    )
    assert len(df) > 5
    assert df["日期"].iloc[0].startswith("2026-09-01")
    assert client.bars.calls[-1]["count"] >= 100


def test_multi_page_history(monkeypatch):
    """older start_date 必须跨页回溯，不能只取第一页。"""
    bars = _daily_bars(date(2020, 1, 1), 2500)
    client = _install(monkeypatch, bars)

    df = ef.fetch_historical_kline(
        symbol="600519",
        period="daily",
        start_date="20220101",
        end_date="20220110",
        adjust="",
    )
    assert len(df) == 10
    assert df["日期"].iloc[0].startswith("2022-01-01")
    assert len(client.bars.calls) >= 2  # 跨页
    assert client.bars.calls[-1]["adjust"] is None


def test_count_only_preserves_old_behavior(monkeypatch):
    bars = _daily_bars(date(2026, 10, 1), 30)
    client = _install(monkeypatch, bars)

    df = ef.fetch_historical_kline(symbol="600519", period="daily", count=5)
    assert len(df) == 5
    assert client.bars.calls[-1]["count"] == 5
    assert client.bars.calls[-1]["start"] == 0


def test_date_beyond_available_history_raises(monkeypatch):
    bars = _daily_bars(date(2024, 1, 1), 200)
    _install(monkeypatch, bars)

    with pytest.raises(RuntimeError, match="超出可获取历史"):
        ef.fetch_historical_kline(
            symbol="600519", period="daily", start_date="20000101"
        )


def test_invalid_date_format_raises(monkeypatch):
    _install(monkeypatch, _daily_bars(date(2026, 1, 1), 10))
    with pytest.raises(ValueError):
        ef.fetch_historical_kline(symbol="600519", start_date="2026/1/1")
