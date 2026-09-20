"""指数日K数据源测试（v3.3.22）。

背景：`index_daily_amount` 的三层降级里，备源1 原用 akshare 的
`stock_zh_index_daily_tx` —— 它**按年份逐年翻页**（上证指数从 1990 年起 →
37 次请求 ≈ 21 秒），在 SmartRouter 的 12s 超时下必然失败，
于是该备源被误判为不可用。

实测 2026-09-20：push2his 的日K路由全挂（10/10 失败），
而 akshare 的 tx/em 两条备源也走 akshare（裸 requests，被东财拒连），
导致本函数**三层降级全失败**。

修法：备源1 改为本项目直连腾讯 `proxy.finance.qq.com`（**单次请求 0.12s**）。
本文件覆盖该直连实现与降级顺序。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import http_fetchers as hf


# ════════════════════════════════════════════════════════════════
# 腾讯指数日K直连
# ════════════════════════════════════════════════════════════════

def _kline_payload(sym: str, bars: list[list]) -> bytes:
    return json.dumps({"code": 0, "msg": "", "data": {sym: {"day": bars}}}).encode("utf-8")


def _bar(date: str, vol: float) -> list:
    """腾讯原始数组：[日期, 开, 收, 高, 低, 成交量, {}, ...]"""
    return [date, "2900.0", "2910.0", "2920.0", "2890.0", str(vol), {}, "0.9", "1.0", "0.0", "0.0"]


class TestTencentIndexDaily:
    def test_parses_bars(self, monkeypatch):
        bars = [_bar("2026-09-17", 4.5e8), _bar("2026-09-18", 4.8e8)]
        monkeypatch.setattr(hf, "get_bytes",
                            lambda *a, **k: _kline_payload("sh000001", bars))

        out = hf.fetch_index_daily_tencent("sh000001", days=5)

        assert len(out) == 2
        assert out[-1]["date"] == "2026-09-18"
        assert out[-1]["volume"] == 4.8e8
        # 腾讯该接口的成交额单位未确认，宁可为空也不给可能错标的数据
        assert out[-1]["amount"] is None

    def test_days_limits_to_tail(self, monkeypatch):
        bars = [_bar(f"2026-09-{d:02d}", 1e8) for d in range(1, 21)]
        monkeypatch.setattr(hf, "get_bytes",
                            lambda *a, **k: _kline_payload("sh000001", bars))

        out = hf.fetch_index_daily_tencent("sh000001", days=3)

        assert len(out) == 3
        assert out[-1]["date"] == "2026-09-20"

    @pytest.mark.parametrize("given,expected", [
        ("sh000001", "sh000001"),
        ("000001", "sh000001"),
        ("1.000001", "sh000001"),
        ("sz399001", "sz399001"),
        ("399001", "sz399001"),
        ("0.399001", "sz399001"),
    ])
    def test_symbol_normalisation(self, given, expected):
        assert hf._to_tencent_index_sym(given) == expected

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setattr(hf, "get_bytes",
                            lambda *a, **k: json.dumps(
                                {"code": 0, "data": {"sh000001": {"day": []}}}).encode())

        with pytest.raises(RuntimeError, match="empty"):
            hf.fetch_index_daily_tencent("sh000001")

    def test_raises_when_all_requests_fail(self, monkeypatch):
        monkeypatch.setattr(hf, "get_bytes", lambda *a, **k: None)

        with pytest.raises(RuntimeError, match="empty"):
            hf.fetch_index_daily_tencent("sh000001")

    def test_uses_hardened_tencent_transport(self, monkeypatch):
        """必须经 get_bytes（带指纹+节流+重试），而不是裸 requests。"""
        captured = {}

        def fake_get_bytes(session_provider, throttle, url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _kline_payload("sh000001", [_bar("2026-09-18", 1e8)])

        monkeypatch.setattr(hf, "get_bytes", fake_get_bytes)

        hf.fetch_index_daily_tencent("sh000001")

        assert "proxy.finance.qq.com" in captured["url"]
        assert captured["params"]["param"].startswith("sh000001,day,")

    def test_falls_back_to_previous_year(self, monkeypatch):
        """当年无数据（如年初）时应回退上一年，而不是直接失败。"""
        calls = []

        def fake_get_bytes(session_provider, throttle, url, **kwargs):
            calls.append(kwargs["params"]["param"])
            if len(calls) == 1:
                return _kline_payload("sh000001", [])          # 当年为空
            return _kline_payload("sh000001", [_bar("2025-12-31", 1e8)])

        monkeypatch.setattr(hf, "get_bytes", fake_get_bytes)

        out = hf.fetch_index_daily_tencent("sh000001", days=1)

        assert len(out) == 1
        assert out[0]["date"] == "2025-12-31"
        assert len(calls) == 2


# ════════════════════════════════════════════════════════════════
# 降级顺序
# ════════════════════════════════════════════════════════════════

class TestIndexDailyFallback:
    def test_prefers_push2his_when_available(self, monkeypatch):
        """主源正常时应用主源（它含成交额），不得跳到备源。"""
        called = []

        monkeypatch.setattr(akf, "_fetch_index_daily_from_push2his",
                            lambda sym, days: called.append("push2his") or [
                                {"date": "2026-09-18", "volume": 1.0, "amount": 2.0}])
        monkeypatch.setattr(hf, "fetch_index_daily_tencent",
                            lambda *a, **k: called.append("tx") or [])

        out = akf.fetch_index_daily_amount(symbol="sh000001", days=1)

        assert called == ["push2his"]
        assert out[0]["amount"] == 2.0

    def test_falls_back_to_direct_tencent_when_push2his_fails(self, monkeypatch):
        """真实故障场景：push2his 日K路由全挂 → 备源1 直连腾讯应顶上。"""
        def boom(sym, days):
            raise RuntimeError("push2his request failed for sh000001")

        monkeypatch.setattr(akf, "_fetch_index_daily_from_push2his", boom)
        monkeypatch.setattr(hf, "fetch_index_daily_tencent",
                            lambda sym, days=None: [
                                {"date": "2026-09-18", "volume": 4.8e8, "amount": None}])

        out = akf.fetch_index_daily_amount(symbol="sh000001", days=1)

        assert len(out) == 1
        assert out[0]["volume"] == 4.8e8

    def test_does_not_call_slow_akshare_tx(self, monkeypatch):
        """回归守卫：不得再调用 akshare 的按年翻页版本。

        它 37 次请求 ≈ 21s，超过路由 12s 超时 → 备源被误判为不可用。
        """
        monkeypatch.setattr(akf, "_fetch_index_daily_from_push2his",
                            lambda sym, days: (_ for _ in ()).throw(RuntimeError("down")))
        monkeypatch.setattr(hf, "fetch_index_daily_tencent",
                            lambda sym, days=None: [{"date": "d", "volume": 1.0, "amount": None}])

        fake_ak = MagicMock()
        fake_ak.stock_zh_index_daily_tx = MagicMock(
            side_effect=AssertionError("不应调用慢速的 akshare 版本"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_index_daily_amount(symbol="sh000001", days=1)

        fake_ak.stock_zh_index_daily_tx.assert_not_called()

    def test_raises_when_all_layers_fail(self, monkeypatch):
        monkeypatch.setattr(akf, "_fetch_index_daily_from_push2his",
                            lambda sym, days: (_ for _ in ()).throw(RuntimeError("p")))
        monkeypatch.setattr(hf, "fetch_index_daily_tencent",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("t")))
        fake_ak = MagicMock()
        fake_ak.stock_zh_index_daily_em.side_effect = RuntimeError("e")
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        with pytest.raises(RuntimeError, match="failed all"):
            akf.fetch_index_daily_amount(symbol="sh000001")
