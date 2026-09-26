"""
本地数据工具返回值必须是**单条合法 JSON**（v3.3.30 修复）。

背景：`get_local_kline` / `get_local_minute` 原实现是
    `dict_to_json(info) + "\n" + df_to_json(df)`
拼出「{...}\n[...]」两段 JSON —— **不是合法 JSON**，任何标准解析器都会失败。
此前因本机读不到通达信数据、一直走 `error_response`（合法 JSON）分支而未暴露；
2026-09-26 跑 live_smoke 时本机已能读到本地日线，才暴露为
`FAIL get_local_kline 返回非法 JSON`。
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd


def _call(mcp, name: str, **kwargs) -> str:
    """同步调用一个 async MCP 工具，返回其文本内容。"""

    async def _run():
        res = await mcp.call_tool(name, kwargs)
        for item in (res[0] if isinstance(res, tuple) else res):
            if hasattr(item, "text"):
                return item.text
        return None

    return asyncio.run(_run())


def _kline_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"date": "2026-09-24", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
         "volume": 100, "amount": 1000},
        {"date": "2026-09-25", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0,
         "volume": 200, "amount": 2000},
    ])


class TestLocalDataJson:
    def test_get_local_kline_returns_single_valid_json(self, mcp_server, monkeypatch):
        """回归守卫：返回值必须能被 json.loads 直接解析成单个对象。"""
        from tradex.tools import local_data

        monkeypatch.setattr(local_data, "detect_tdx_dir", lambda: "D:/fake/tdx")
        monkeypatch.setattr(local_data, "fetch_local_kline", lambda symbol: _kline_df())

        text = _call(mcp_server, "get_local_kline", symbol="600519", count=5)

        payload = json.loads(text)          # 旧实现会在此抛 JSONDecodeError
        assert isinstance(payload, dict)
        assert payload["rows"] == 2
        assert len(payload["data"]) == 2

    def test_get_local_minute_returns_single_valid_json(self, mcp_server, monkeypatch):
        from tradex.tools import local_data

        monkeypatch.setattr(
            local_data, "fetch_local_minute", lambda symbol, period=5: _kline_df()
        )

        text = _call(mcp_server, "get_local_minute", symbol="600519", period=5, count=5)

        payload = json.loads(text)
        assert isinstance(payload, dict)
        assert payload["rows"] == 2
        assert len(payload["data"]) == 2

    def test_empty_local_data_still_returns_valid_json(self, mcp_server, monkeypatch):
        """无数据分支（error_response）也要保持合法 JSON —— 别修好一处坏另一处。"""
        from tradex.tools import local_data

        monkeypatch.setattr(local_data, "detect_tdx_dir", lambda: None)
        monkeypatch.setattr(local_data, "fetch_local_kline", lambda symbol: pd.DataFrame())

        text = _call(mcp_server, "get_local_kline", symbol="600519", count=5)

        payload = json.loads(text)
        assert isinstance(payload, dict)
