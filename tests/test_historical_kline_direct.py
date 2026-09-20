"""历史 K 线直连实现测试（v3.3.27）。

背景：`historical_kline` 备源 `akshare` 恒失败。akshare 的 `stock_zh_a_hist`
用裸 requests 打 `push2his.eastmoney.com/api/qt/stock/kline/get`（被拒连），
且**即便打通**它还有第二个问题：用「按位置赋列名」把逗号串映射成中文列名，
一旦上游调整 `fields2` 顺序或字段缺失，列名会整体错位（v3.3.16 fund_hold 同类事故）。

直连实现两层：
  1. 东财 push2his（走 em_get，享节流/重试/镜像/熔断）—— 原生复权与振幅/涨跌幅
  2. 腾讯 `proxy.finance.qq.com` —— 东财日K路由故障时兜底（实测可用）

字段语义已用 **eltdx 主源逐字段交叉验证**（2026-09-18 600519）：
  东财/eltdx：开盘 1262.99 收盘 1257.12 最高 1265.88 最低 1256.10 量 24891 额 3135849216
  腾讯 bar：  [1]1262.99 [2]1257.12 [3]1265.88 [4]1256.10 [5]24891 [8]313584.91(万元)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from astock_signals import antiban_http as ah
from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client

KLINE_COLUMNS = [
    "日期", "股票代码", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]


def em_kline_row(date="2026-09-18", o=1262.99, c=1257.12, h=1265.88, low=1256.10,
                 vol=24891, amt=3135849216, amp=0.77, pct=-0.78, chg=-9.86, turn=0.20):
    return f"{date},{o},{c},{h},{low},{vol},{amt},{amp},{pct},{chg},{turn},0"


def em_payload(rows: list[str]):
    return {"rc": 0, "data": {"code": "600519", "klines": rows}}


def tencent_bar(date="2026-09-18", o=1262.99, c=1257.12, h=1265.88, low=1256.10,
                vol=24891, turn=0.20, amt_wan=313584.91):
    """腾讯 bar：[日期,开,收,高,低,量(手),{},换手率%,成交额(万元)]"""
    return [date, str(o), str(c), str(h), str(low), str(vol), {}, str(turn), str(amt_wan)]


def tencent_payload(sym="sh600519", key="qfqday", bars=None):
    return {"code": 0, "data": {sym: {key: bars or [tencent_bar()]}}}


def fake_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


@pytest.fixture
def patch_em_get(monkeypatch):
    state = {"resp": None, "calls": []}

    def fake_get(url, **kwargs):
        state["calls"].append((url, kwargs))
        outcome = state["resp"]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(em_client, "em_get", fake_get)
    return state


@pytest.fixture
def patch_tencent(monkeypatch):
    """替换 antiban_http.get_bytes（腾讯传输层底层）。"""
    state = {"payload": None, "calls": []}

    def fake_get_bytes(session_provider, throttle, url, **kwargs):
        state["calls"].append((url, kwargs))
        if state["payload"] is None:
            return None
        return json.dumps(state["payload"]).encode("utf-8")

    monkeypatch.setattr(ah, "get_bytes", fake_get_bytes)
    return state


# ════════════════════════════════════════════════════════════════
# 第 1 层：东财 push2his
# ════════════════════════════════════════════════════════════════

class TestEastmoneyLayer:
    def test_parses_klines_into_akshare_columns(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        df = akf.fetch_historical_kline(symbol="600519", period="daily")

        assert list(df.columns) == KLINE_COLUMNS, "列名与顺序必须与 akshare 一致"
        assert len(df) == 1
        assert df.iloc[0]["开盘"] == pytest.approx(1262.99)
        assert df.iloc[0]["收盘"] == pytest.approx(1257.12)
        assert df.iloc[0]["成交额"] == pytest.approx(3135849216)
        assert df.iloc[0]["涨跌幅"] == pytest.approx(-0.78)
        assert df.iloc[0]["股票代码"] == "600519"

    def test_prefers_eastmoney_when_available(self, patch_em_get, patch_tencent):
        """东财可用时不应走腾讯（东财原生给振幅/涨跌幅，无需自算）。"""
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        akf.fetch_historical_kline(symbol="600519")

        assert patch_tencent["calls"] == [], "东财成功时不该请求腾讯"

    def test_request_params_use_em_entry(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        akf.fetch_historical_kline(symbol="600519", period="weekly", adjust="hfq",
                                   start_date="20260101", end_date="20260918")

        url, kwargs = patch_em_get["calls"][0]
        assert url == "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        p = kwargs["params"]
        assert p["klt"] == "102", "weekly → 102"
        assert p["fqt"] == "2", "hfq → 2"
        assert p["secid"] == "1.600519"
        assert p["beg"] == "20260101" and p["end"] == "20260918"

    def test_shanghai_market_code(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        akf.fetch_historical_kline(symbol="000001")

        assert patch_em_get["calls"][0][1]["params"]["secid"] == "0.000001"

    def test_malformed_rows_skipped(self, patch_em_get, patch_tencent):
        """字段数不足 11 的行应丢弃，避免「少一列 → 整体错位」。"""
        patch_em_get["resp"] = fake_resp(em_payload([
            em_kline_row(date="2026-09-17"),
            "2026-09-18,1262.99,1257.12",          # 只有 3 个字段
        ]))

        df = akf.fetch_historical_kline(symbol="600519")

        assert len(df) == 1
        assert df.iloc[0]["日期"].isoformat() == "2026-09-17"

    def test_empty_klines_falls_back_to_tencent(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = fake_resp(em_payload([]))
        patch_tencent["payload"] = tencent_payload()

        df = akf.fetch_historical_kline(symbol="600519")

        assert len(df) == 1
        assert len(patch_tencent["calls"]) == 1

    def test_exhausted_falls_back_to_tencent(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = None
        patch_tencent["payload"] = tencent_payload()

        df = akf.fetch_historical_kline(symbol="600519")

        assert len(df) == 1


# ════════════════════════════════════════════════════════════════
# 第 2 层：腾讯兜底
# ════════════════════════════════════════════════════════════════

class TestTencentLayer:
    def _only_tencent(self, patch_em_get, patch_tencent, payload):
        patch_em_get["resp"] = None
        patch_tencent["payload"] = payload
        return akf.fetch_historical_kline(symbol="600519", period="daily",
                                          start_date="20260916", end_date="20260918")

    def test_parses_and_uses_akshare_columns(self, patch_em_get, patch_tencent):
        df = self._only_tencent(patch_em_get, patch_tencent, tencent_payload())

        assert list(df.columns) == KLINE_COLUMNS
        assert df.iloc[0]["开盘"] == pytest.approx(1262.99)
        assert df.iloc[0]["收盘"] == pytest.approx(1257.12)
        assert df.iloc[0]["成交量"] == pytest.approx(24891)
        assert df.iloc[0]["换手率"] == pytest.approx(0.20)

    def test_amount_converted_from_wan_to_yuan(self, patch_em_get, patch_tencent):
        """腾讯给的是**万元**，必须换算成元（与东财/eltdx 口径一致）。"""
        df = self._only_tencent(patch_em_get, patch_tencent,
                                tencent_payload(bars=[tencent_bar(amt_wan=313584.91)]))

        assert df.iloc[0]["成交额"] == pytest.approx(3135849100, rel=1e-6)

    def test_computed_change_and_amplitude(self, patch_em_get, patch_tencent):
        """腾讯不提供振幅/涨跌幅/涨跌额，应按**前一根收盘**计算。"""
        bars = [
            tencent_bar(date="2026-09-17", o=1257.98, c=1266.98, h=1267.60, low=1254.0),
            tencent_bar(date="2026-09-18", o=1262.99, c=1257.12, h=1265.88, low=1256.10),
        ]
        df = self._only_tencent(patch_em_get, patch_tencent,
                                tencent_payload(bars=bars))

        row = df[df["日期"].astype(str) == "2026-09-18"].iloc[0]
        assert row["涨跌额"] == pytest.approx(1257.12 - 1266.98)
        assert row["涨跌幅"] == pytest.approx((1257.12 - 1266.98) / 1266.98 * 100)
        assert row["振幅"] == pytest.approx((1265.88 - 1256.10) / 1266.98 * 100)

    def test_bars_sorted_ascending(self, patch_em_get, patch_tencent):
        """腾讯可能乱序返回，必须按日期升序后再算前收。"""
        bars = [
            tencent_bar(date="2026-09-18", c=1257.12),
            tencent_bar(date="2026-09-17", c=1266.98),
        ]
        df = self._only_tencent(patch_em_get, patch_tencent,
                                tencent_payload(bars=bars))

        assert list(df["日期"].astype(str)) == ["2026-09-17", "2026-09-18"]

    def test_date_range_filter(self, patch_em_get, patch_tencent):
        bars = [tencent_bar(date=f"2026-09-{d:02d}") for d in (10, 15, 18, 20)]
        patch_em_get["resp"] = None
        patch_tencent["payload"] = tencent_payload(bars=bars)

        df = akf.fetch_historical_kline(symbol="600519", start_date="20260915",
                                        end_date="20260918")

        assert [str(d) for d in df["日期"]] == ["2026-09-15", "2026-09-18"]

    @pytest.mark.parametrize("period,key", [
        ("daily", "qfqday"), ("weekly", "qfqweek"), ("monthly", "qfqmonth"),
    ])
    def test_period_keys(self, period, key, patch_em_get, patch_tencent):
        patch_em_get["resp"] = None
        patch_tencent["payload"] = tencent_payload(key=key)

        df = akf.fetch_historical_kline(symbol="600519", period=period,
                                        start_date="20260901", end_date="20260918")

        assert len(df) == 1

    @pytest.mark.parametrize("symbol,expected", [
        ("600519", "sh600519"),
        ("sh600519", "sh600519"),
        ("000001", "sz000001"),
        ("sz000001", "sz000001"),
        ("300750", "sz300750"),
        ("688981", "sh688981"),
    ])
    def test_tencent_symbol_normalisation(self, symbol, expected):
        assert akf._to_tencent_sym(symbol) == expected

    def test_tencent_param_format(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = None
        # weekly + hfq → 响应键为 hfqweek
        patch_tencent["payload"] = tencent_payload(key="hfqweek")

        akf.fetch_historical_kline(symbol="600519", period="weekly", adjust="hfq",
                                   start_date="20260101", end_date="20260918")

        url, kwargs = patch_tencent["calls"][0]
        assert "proxy.finance.qq.com" in url
        assert kwargs["params"]["param"] == "sh600519,week,2026-01-01,2026-09-18,640,hfq"

    def test_empty_bars_falls_through_to_error(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = None
        patch_tencent["payload"] = {"code": 0, "data": {"sh600519": {"qfqday": []}}}

        with pytest.raises(RuntimeError, match="全部源失败"):
            akf.fetch_historical_kline(symbol="600519")


# ════════════════════════════════════════════════════════════════
# 失败路径与守卫
# ════════════════════════════════════════════════════════════════

class TestFailuresAndGuards:
    def test_requires_symbol(self):
        with pytest.raises(RuntimeError, match="symbol/code is required"):
            akf.fetch_historical_kline()

    def test_both_layers_fail_lists_both_reasons(self, patch_em_get, patch_tencent):
        patch_em_get["resp"] = None
        patch_tencent["payload"] = None

        with pytest.raises(RuntimeError) as ei:
            akf.fetch_historical_kline(symbol="600519")

        msg = str(ei.value)
        assert "东财" in msg and "腾讯" in msg, "错误信息应列出每一层的失败原因"

    def test_does_not_call_akshare(self, patch_em_get, patch_tencent, monkeypatch):
        """不得再调用 akshare 的 stock_zh_a_hist（裸 requests + 位置赋列名）。"""
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        fake_ak = MagicMock()
        fake_ak.stock_zh_a_hist = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_zh_a_hist"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_historical_kline(symbol="600519")

        fake_ak.stock_zh_a_hist.assert_not_called()

    def test_period_normalised_before_dispatch(self, patch_em_get, patch_tencent):
        """day/week/month 别名需先归一化成 daily/weekly/monthly（v3.3.14 约定）。"""
        patch_em_get["resp"] = fake_resp(em_payload([em_kline_row()]))

        akf.fetch_historical_kline(symbol="600519", period="week")

        assert patch_em_get["calls"][0][1]["params"]["klt"] == "102"
