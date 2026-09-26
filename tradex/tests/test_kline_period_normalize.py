"""
K 线周期归一化回归测试（v3.3.30）。

背景 —— 本次修复的两个 P0「静默错数据」缺陷：

① `historical_kline` 请求 `period="5m"` 时：
   eltdx 源 `_normalize_period` 抛 ValueError → SmartRouter 降级 akshare →
   东财 `_EM_KLT.get(period, "101")` 落默认值 101=**日K** →
   调用方拿到 1498 根日线，**全程无任何告警**。

② `period="1m"` 被别名表映射为 **month** → 请求「1 分钟」拿到月线；
   而 `full_kline` 不做归一化，`1m` 在那里是真 1 分钟 ——
   **同一参数名在两个工具里语义相反**。

本文件锁死修复后的口径：分钟周期必须被识别、'1m' 必须是「1 分钟」、
未识别周期**必须抛错**（不允许任何形式的静默回落）。
"""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────
# eltdx 主源：_normalize_period
# ─────────────────────────────────────────────

class TestEltdxPeriodNormalize:
    """eltdx 源周期归一化（`data_sources/eltdx_fetchers._normalize_period`）。"""

    @pytest.mark.parametrize("raw,expected", [
        ("1m", "1m"), ("1min", "1m"), ("minute", "1m"),
        ("5m", "5m"), ("5min", "5m"),
        ("15m", "15m"), ("15min", "15m"),
        ("30m", "30m"), ("30min", "30m"),
        ("60m", "60m"), ("60min", "60m"), ("1h", "60m"),
    ])
    def test_minute_periods_supported(self, raw, expected):
        """分钟周期必须被识别（此前全部被拒绝 → 静默降级日线）。"""
        from tradex.data_sources.eltdx_fetchers import _normalize_period

        assert _normalize_period(raw) == expected

    def test_1m_means_one_minute_not_month(self):
        """P0 回归守卫：'1m' 必须表示 1 分钟，不再是月线。"""
        from tradex.data_sources.eltdx_fetchers import _normalize_period

        assert _normalize_period("1m") == "1m"
        assert _normalize_period("1m") != "month"

    @pytest.mark.parametrize("raw,expected", [
        ("day", "day"), ("daily", "day"), ("d", "day"), ("1d", "day"),
        ("week", "week"), ("weekly", "week"), ("w", "week"), ("1w", "week"),
        ("month", "month"), ("monthly", "month"), ("m", "month"), ("1mo", "month"),
        ("quarter", "quarter"), ("q", "quarter"), ("1q", "quarter"),
        ("year", "year"), ("y", "year"), ("1y", "year"),
    ])
    def test_daily_and_above_periods(self, raw, expected):
        """日线及以上周期（含本次新增的 quarter/year）与旧别名兼容。"""
        from tradex.data_sources.eltdx_fetchers import _normalize_period

        assert _normalize_period(raw) == expected

    @pytest.mark.parametrize("raw", ["foo", "3m", "2h", "min", "1minute", "", None, "dailyy"])
    def test_unknown_period_raises(self, raw):
        """未识别周期必须抛 ValueError —— 绝不返回一个「看起来像 K 线」的默认周期。"""
        from tradex.data_sources.eltdx_fetchers import _normalize_period

        with pytest.raises(ValueError):
            _normalize_period(raw)

    def test_is_minute_period(self):
        from tradex.data_sources.eltdx_fetchers import _is_minute_period

        assert all(_is_minute_period(p) for p in ("1m", "5m", "15m", "30m", "60m", "1h"))
        assert not any(_is_minute_period(p) for p in ("day", "week", "month", "quarter", "year"))


# ─────────────────────────────────────────────
# 备源：_AK_PERIOD_ALIASES / _norm_klt
# ─────────────────────────────────────────────

class TestAksharePeriodNormalize:
    """备源周期别名（`data_sources/akshare_fetchers`）。"""

    @pytest.mark.parametrize("raw,expected", [
        ("day", "daily"), ("daily", "daily"), ("d", "daily"),
        ("week", "weekly"), ("w", "weekly"),
        ("month", "monthly"), ("1mo", "monthly"), ("m", "monthly"),
    ])
    def test_daily_and_above_aliases(self, raw, expected):
        from tradex.data_sources.akshare_fetchers import _normalize_ak_period

        assert _normalize_ak_period(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("1m", "1m"), ("1min", "1m"), ("minute", "1m"),
        ("5m", "5m"), ("15m", "15m"), ("30m", "30m"),
        ("60m", "60m"), ("1h", "60m"),
    ])
    def test_minute_aliases(self, raw, expected):
        from tradex.data_sources.akshare_fetchers import _normalize_ak_period

        assert _normalize_ak_period(raw) == expected

    def test_1m_not_monthly(self):
        """P0 回归守卫：备源 '1m' 也从 monthly 改为 1 分钟。"""
        from tradex.data_sources.akshare_fetchers import _normalize_ak_period

        assert _normalize_ak_period("1m") == "1m"
        assert _normalize_ak_period("1m") != "monthly"


class TestNormKlt:
    """东财 klt 映射（`_norm_klt`）。"""

    @pytest.mark.parametrize("raw,expected", [
        ("1m", "1"), ("5m", "5"), ("15m", "15"), ("30m", "30"), ("60m", "60"),
        ("1h", "60"), ("1mo", "103"),
        ("day", "101"), ("daily", "101"), ("d", "101"),
        ("week", "102"), ("weekly", "102"),
        ("month", "103"), ("monthly", "103"),
    ])
    def test_mapping(self, raw, expected):
        from tradex.data_sources.akshare_fetchers import _norm_klt

        assert _norm_klt(raw) == expected

    @pytest.mark.parametrize("raw", ["foo", "3m", "", None, "badxyz"])
    def test_unknown_period_raises_instead_of_daily_fallback(self, raw):
        """P0 回归守卫：未识别周期必须抛错。

        此前实现是 `_EM_KLT.get(period, "101")` —— 任何未知周期都静默变成日 K，
        正是「请求 5 分钟拿到 1498 根日线」的直接来源。若有人改回默认值，
        本用例会失败。
        """
        from tradex.data_sources.akshare_fetchers import _norm_klt

        with pytest.raises(ValueError):
            _norm_klt(raw)

    def test_minute_klt_set_matches_mapping(self):
        """分钟 klt 集合必须与 _EM_KLT 的分钟项一致（防止漏配）。"""
        from tradex.data_sources.akshare_fetchers import _EM_KLT, _EM_MINUTE_KLTS

        derived = {v for k, v in _EM_KLT.items() if k.endswith("m") and k != "1mo"}
        assert derived == set(_EM_MINUTE_KLTS)


class TestTencentRejectsMinute:
    """腾讯 kline 源必须显式拒绝分钟周期（否则 per 参数会被默默当成 day）。"""

    @pytest.mark.parametrize("period", ["1m", "5m", "15m", "30m", "60m", "1h"])
    def test_minute_raises(self, period):
        from tradex.data_sources.akshare_fetchers import _fetch_kline_from_tencent

        with pytest.raises(ValueError):
            _fetch_kline_from_tencent("sh600519", period, "", "", "qfq")

    @pytest.mark.parametrize("period", ["foo", "3m"])
    def test_unknown_raises(self, period):
        from tradex.data_sources.akshare_fetchers import _fetch_kline_from_tencent

        with pytest.raises(ValueError):
            _fetch_kline_from_tencent("sh600519", period, "", "", "qfq")
