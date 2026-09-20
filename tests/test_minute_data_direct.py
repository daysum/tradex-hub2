"""东财分时数据直连实现测试（v3.3.25）。

背景：`minute_data` 备源 `akshare` 恒失败。根因是 akshare 的 `stock_intraday_em`
用裸 requests 打 **`70.push2.eastmoney.com/api/qt/stock/details/sse`**：
  1. `70.push2` 是带数字前缀的子域，**没有配镜像**，节点故障时无路可退
  2. `/details/sse` 是 **SSE 长连接**，服务端不主动关闭 —— 即便流式读到首个事件
     就 break，`resp.close()` 仍会去排空剩余响应体，实测 **close() 独占 8.6s**

改用 `push2.eastmoney.com`（有 push2delay 镜像）的 **`/details/get` 非流式变体**，
实测 200 / 0.28s，载荷与 SSE 首个事件一致。

本文件覆盖该实现与上面两条「不要退回去」的守卫。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client


def details_payload(details: list[str]):
    return {"rc": 0, "data": {"code": "600519", "details": details}}


def fake_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


@pytest.fixture
def patch_em_get(monkeypatch):
    """替换 em_client.em_get，记录调用参数。"""
    state = {"resp": None, "calls": []}

    def fake_get(url, **kwargs):
        state["calls"].append((url, kwargs))
        return state["resp"]

    monkeypatch.setattr(em_client, "em_get", fake_get)
    return state


# ════════════════════════════════════════════════════════════════
# 解析
# ════════════════════════════════════════════════════════════════

class TestParsing:
    def test_parses_details_into_four_columns(self, patch_em_get):
        patch_em_get["resp"] = fake_resp(details_payload([
            "09:15:06,1266.98,4,0,4",
            "09:30:01,1266.00,10,0,2",
            "09:30:05,1265.50,7,0,1",
        ]))

        df = akf.fetch_minute_data(symbol="600519")

        assert list(df.columns) == ["时间", "成交价", "手数", "买卖盘性质"]
        assert len(df) == 3
        assert df.iloc[0]["时间"] == "09:15:06"
        assert df.iloc[0]["成交价"] == pytest.approx(1266.98)
        assert df.iloc[0]["手数"] == pytest.approx(4)

    def test_side_mapping(self, patch_em_get):
        """2=买盘 1=卖盘 4=中性盘（与 akshare 一致）。"""
        patch_em_get["resp"] = fake_resp(details_payload([
            "09:30:01,1.0,1,0,2",
            "09:30:02,1.0,1,0,1",
            "09:30:03,1.0,1,0,4",
        ]))

        df = akf.fetch_minute_data(symbol="600519")

        assert list(df["买卖盘性质"]) == ["买盘", "卖盘", "中性盘"]

    def test_numeric_coercion(self, patch_em_get):
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,abc,xyz,0,4"]))

        df = akf.fetch_minute_data(symbol="600519")

        assert pd.isna(df.iloc[0]["成交价"])
        assert pd.isna(df.iloc[0]["手数"])

    def test_malformed_rows_skipped(self, patch_em_get):
        """字段数不足的行应跳过，而不是让整批失败。"""
        patch_em_get["resp"] = fake_resp(details_payload([
            "09:30:01,1.0,1,0,4",
            "too,few",
            "09:30:02,2.0,2,0,2",
        ]))

        df = akf.fetch_minute_data(symbol="600519")

        assert len(df) == 2


# ════════════════════════════════════════════════════════════════
# 接口选择守卫（本文件最重要的一组）
# ════════════════════════════════════════════════════════════════

class TestEndpointChoice:
    def test_uses_non_sse_get_variant(self, patch_em_get):
        """必须用 `/details/get`，**不能**用 `/details/sse`。

        `/sse` 是长连接：非流式 GET 会阻塞到超时；即便流式读到首个事件就 break，
        `resp.close()` 仍要排空剩余响应体，实测独占 8.6s。
        """
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        akf.fetch_minute_data(symbol="600519")

        url = patch_em_get["calls"][0][0]
        assert url.endswith("/api/qt/stock/details/get"), (
            f"应使用非流式 /details/get，实际 {url}")
        assert "/sse" not in url, "不得使用 SSE 长连接变体"

    def test_uses_mirrorable_host(self, patch_em_get):
        """必须用 `push2.eastmoney.com`（有镜像），不能用 akshare 的 `70.push2`。"""
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        akf.fetch_minute_data(symbol="600519")

        url = patch_em_get["calls"][0][0]
        assert "//push2.eastmoney.com/" in url, f"应使用可镜像的主机，实际 {url}"
        assert "70.push2" not in url, "带数字前缀的子域没有镜像，不要用"

    def test_goes_through_unified_entry(self, patch_em_get):
        """必须走 em_get（带节流/重试/镜像/熔断），不能裸请求。"""
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        akf.fetch_minute_data(symbol="600519")

        assert len(patch_em_get["calls"]) == 1
        _, kwargs = patch_em_get["calls"][0]
        assert "params" in kwargs
        assert kwargs["params"]["fields2"] == "f51,f52,f53,f54,f55"


# ════════════════════════════════════════════════════════════════
# secid 归一化
# ════════════════════════════════════════════════════════════════

class TestSecidNormalisation:
    @pytest.mark.parametrize("symbol,expected", [
        ("600519", "1.600519"),
        ("sh600519", "1.600519"),
        ("000001", "0.000001"),
        ("sz000001", "0.000001"),
        ("300750", "0.300750"),
        ("688981", "1.688981"),
    ])
    def test_market_code_inferred(self, symbol, expected, patch_em_get):
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        akf.fetch_minute_data(symbol=symbol)

        assert patch_em_get["calls"][0][1]["params"]["secid"] == expected

    def test_explicit_prefix_wins_over_first_digit(self, patch_em_get):
        """`sh000001` 是上证指数 —— 市场码必须为 1。

        只按首位数字猜会判成深市 0，得到空明细（akshare 原版就有这个毛病）。
        """
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        akf.fetch_minute_data(symbol="sh000001")

        assert patch_em_get["calls"][0][1]["params"]["secid"] == "1.000001"


# ════════════════════════════════════════════════════════════════
# 失败路径
# ════════════════════════════════════════════════════════════════

class TestFailures:
    def test_requires_symbol(self, patch_em_get):
        with pytest.raises(RuntimeError, match="symbol/code is required"):
            akf.fetch_minute_data()

    def test_request_exhausted_raises(self, patch_em_get):
        patch_em_get["resp"] = None
        with pytest.raises(RuntimeError, match="重试耗尽"):
            akf.fetch_minute_data(symbol="600519")

    def test_empty_details_raises(self, patch_em_get):
        patch_em_get["resp"] = fake_resp({"rc": 0, "data": {"code": "600519", "details": []}})
        with pytest.raises(RuntimeError, match="分时明细为空"):
            akf.fetch_minute_data(symbol="600519")

    def test_all_rows_malformed_raises(self, patch_em_get):
        patch_em_get["resp"] = fake_resp(details_payload(["bad", "worse"]))
        with pytest.raises(RuntimeError, match="解析为空"):
            akf.fetch_minute_data(symbol="600519")


# ════════════════════════════════════════════════════════════════
# 回归守卫
# ════════════════════════════════════════════════════════════════

class TestNoAkshare:
    def test_does_not_call_broken_akshare_function(self, patch_em_get, monkeypatch):
        """不得再调用 akshare 的 stock_intraday_em（裸 requests + SSE + 无镜像）。"""
        patch_em_get["resp"] = fake_resp(details_payload(["09:30:01,1.0,1,0,4"]))

        fake_ak = MagicMock()
        fake_ak.stock_intraday_em = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_intraday_em"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_minute_data(symbol="600519")

        fake_ak.stock_intraday_em.assert_not_called()
