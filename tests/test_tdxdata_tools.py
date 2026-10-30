"""Phase 1: tdxdata MCP 工具离线测试（不联网）。

通过 FastMCP 的 call_tool 调用真实注册的工具，monkeypatch 掉
tdxdata_fetchers.call / 产品目录 / 列义集合，验证：
  - 产品目录过滤与截断
  - 产品查询的 mapped/raw/params 覆盖/未知产品
  - 各专用工具的参数校验与输出结构
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tradex.server import mcp
from tradex.tools import tdxdata_data as td

# ── 假对象 ──────────────────────────────────────────────────────────────


class FakeProduct:
    def __init__(self, name, domain="行情", cmd="CWServ.x", ctl=1, params=None):
        self.name = name
        self.domain = domain
        self.cmd = cmd
        self.ctl = ctl
        self.params = params if params is not None else ["001"]


class FakeTable:
    def __init__(self, cols=None, rows=None, domain="测试"):
        self._cols = cols or ["a", "b"]
        self._rows = rows if rows is not None else [{"a": 1, "b": 2}]
        self._domain = domain

    @property
    def cols(self):
        return list(self._cols)

    def to_dict(self):
        return {
            "cols": list(self._cols),
            "rows": [dict(r) for r in self._rows],
            "cmd": "CWServ.test",
            "domain": self._domain,
            "provenance": {"as_of": None, "status": "provisional"},
        }


class FakeDataset:
    def __init__(self, name, params, table):
        self.name = name
        self.params = params
        self.table = table

    def to_dict(self):
        return {
            "name": self.name,
            "params": self.params,
            "table": self.table.to_dict(),
        }

    @property
    def data_date(self):
        return None


def _call(name: str, args: dict) -> dict:
    res = asyncio.run(mcp.call_tool(name, args))
    text = res[0][0].text if isinstance(res, tuple) else res[0].text
    return json.loads(text)


@pytest.fixture(autouse=True)
def _reset_tdxdata_tool_state(monkeypatch):
    """每个用例重置目录/列义缓存并默认启用。"""
    monkeypatch.setattr(td.tdf, "is_enabled", lambda: True)
    monkeypatch.setattr(td, "_CATALOG", None)
    monkeypatch.setattr(td, "_MAPPED_NAMES", None)
    yield
    td._CATALOG = None
    td._MAPPED_NAMES = None


def _install_catalog(monkeypatch, *products):
    catalog = {p.name: p for p in products}
    monkeypatch.setattr(td, "_CATALOG", catalog)
    monkeypatch.setattr(td, "_MAPPED_NAMES", set())
    return catalog


# ── 目录 ────────────────────────────────────────────────────────────────


def test_list_products_filters_and_limits(monkeypatch):
    _install_catalog(
        monkeypatch,
        FakeProduct("holiday", domain="宏观"),
        FakeProduct("lhb", domain="龙虎榜与两融"),
        FakeProduct("xjjj", domain="新股与再融资"),
    )
    data = _call("tdx_list_products", {"domain": "宏观"})
    assert data["total"] == 1
    assert data["products"][0]["name"] == "holiday"
    assert data["products"][0]["mapped"] is False
    assert data["returned"] == 1


def test_list_products_keyword_and_truncate(monkeypatch):
    _install_catalog(
        monkeypatch,
        *[FakeProduct(f"p{i}", domain="行情") for i in range(10)],
    )
    data = _call("tdx_list_products", {"keyword": "p", "limit": 3})
    assert data["total"] == 10
    assert data["returned"] == 3
    assert data["truncated"] is True


# ── 产品查询 ────────────────────────────────────────────────────────────


def test_get_product_mapped_truncates(monkeypatch):
    _install_catalog(monkeypatch, FakeProduct("holiday", domain="宏观"))
    monkeypatch.setattr(td, "_MAPPED_NAMES", {"holiday"})
    rows = [{"date": f"2026-01-0{i}"} for i in range(1, 6)]

    def fake_call(method, *args, **kwargs):
        assert method == "get"
        assert kwargs["mapped"] is True
        return FakeDataset("holiday", ["001", "1"], FakeTable(rows=rows))

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_product", {"name": "holiday", "max_rows": 2})
    assert data["name"] == "holiday"
    assert data["total_rows"] == 5
    assert data["returned_rows"] == 2
    assert data["truncated"] is True
    assert data["mapped_effective"] is True
    assert "FIELD_MAP" in data["column_note"]


def test_get_product_raw_fallback(monkeypatch):
    _install_catalog(monkeypatch, FakeProduct("lhb", domain="龙虎榜与两融"))
    monkeypatch.setattr(td, "_MAPPED_NAMES", set())

    def fake_call(method, *args, **kwargs):
        return FakeDataset("lhb", ["00101"], FakeTable(rows=[{"N001": "x"}]))

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_product", {"name": "lhb"})
    assert data["mapped_requested"] is True
    assert data["mapped_effective"] is False
    assert "未登记" in data["column_note"]


def test_get_product_mapped_but_raw_cols_falls_back(monkeypatch):
    """COL_GUARD 形状不匹配时上游可能只给 raw 而不抛错，工具须如实降级。"""
    _install_catalog(monkeypatch, FakeProduct("holiday", domain="宏观"))
    monkeypatch.setattr(td, "_MAPPED_NAMES", {"holiday"})

    def fake_call(method, *args, **kwargs):
        return FakeDataset(
            "holiday", ["001"], FakeTable(cols=["N001", "N002"], rows=[{"N001": "x"}])
        )

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_product", {"name": "holiday"})
    assert data["mapped_requested"] is True
    assert data["mapped_effective"] is False
    assert "COL_GUARD" in data["column_note"]


def test_get_product_params_override(monkeypatch):
    _install_catalog(monkeypatch, FakeProduct("lhb", domain="龙虎榜与两融"))
    monkeypatch.setattr(td, "_MAPPED_NAMES", set())
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        seen["kwargs"] = kwargs
        return FakeTable(rows=[{"N001": "2026-10-30"}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call(
        "tdx_get_product",
        {"name": "lhb", "params": '["00101","1","2026","10",""]'},
    )
    assert seen["method"] == "call"
    assert seen["args"][0] == "CWServ.x"
    assert seen["args"][2] == ["00101", "1", "2026", "10", ""]
    assert data["params_overridden"] is True
    assert data["returned_rows"] == 1


def test_get_product_unknown_suggests(monkeypatch):
    _install_catalog(monkeypatch, FakeProduct("holiday"), FakeProduct("north"))
    data = _call("tdx_get_product", {"name": "holida"})
    assert data["error"] is True
    assert "holiday" in data["message"]


def test_get_product_bad_params(monkeypatch):
    _install_catalog(monkeypatch, FakeProduct("holiday"))
    data = _call("tdx_get_product", {"name": "holiday", "params": "{bad"})
    assert data["error"] is True
    assert "JSON" in data["message"]


# ── 专用工具 ────────────────────────────────────────────────────────────


def test_market_breadth_summary(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_market_breadth"
        return FakeTable(cols=["up_count"], rows=[{"up_count": 100}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_market_breadth", {"view": "summary"})
    assert data["returned_rows"] == 1
    assert data["view"] == "summary"


def test_market_breadth_bad_view(monkeypatch):
    data = _call("tdx_get_market_breadth", {"view": "bad"})
    assert data["error"] is True


def test_breadth_history_rejects_unknown(monkeypatch):
    data = _call("tdx_get_breadth_history", {"code": "880903"})
    assert data["error"] is True
    assert "880005" in data["message"]


def test_breadth_history_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_breadth"
        assert args[0] == "880005"
        return FakeTable(cols=["up_count"], rows=[{"up_count": 1}] * 3)

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_breadth_history", {"code": "sh880005", "count": 5})
    assert data["code"] == "880005"
    assert data["total_rows"] == 3


def test_intraday_samples_limit(monkeypatch):
    symbols = ",".join(["600519"] * 21)
    data = _call("tdx_get_intraday_samples", {"symbols": symbols})
    assert data["error"] is True
    assert "最多 20" in data["message"]


def test_intraday_samples_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_intraday_samples"
        assert args[0] == ["999999", "399001"]
        return FakeTable(cols=["points"], rows=[{"points": [1, 2]}, {"points": [3]}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call(
        "tdx_get_intraday_samples", {"symbols": "999999,399001", "count": 5}
    )
    assert data["symbols"] == ["999999", "399001"]
    assert data["returned_rows"] == 2


def test_boards_index_unsupported():
    data = _call("tdx_get_boards", {"kind": "index"})
    assert data["error"] is True


def test_boards_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_boards"
        assert args[0] == "concept"
        return FakeTable(cols=["board_code"], rows=[{"board_code": "880564"}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_boards", {"kind": "concept"})
    assert data["kind"] == "concept"
    assert data["returned_rows"] == 1


def test_board_members_requires_board(monkeypatch):
    data = _call("tdx_get_board_members", {"board": "  "})
    assert data["error"] is True


def test_board_members_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_board_members"
        assert args[0] == "880564"
        return FakeTable(cols=["code"], rows=[{"code": "000860"}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_board_members", {"board": "880564"})
    assert data["returned_rows"] == 1


def test_market_stat_all_unsupported():
    data = _call("tdx_get_market_stat", {"kind": "all"})
    assert data["error"] is True


def test_market_stat_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_market_stat"
        return FakeTable(cols=["stat_date"], rows=[{"stat_date": "2026-10-30"}])

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_market_stat", {"kind": "updown"})
    assert data["kind"] == "updown"
    assert data["returned_rows"] == 1


def test_price_limits_ok(monkeypatch):
    def fake_call(method, *args, **kwargs):
        assert method == "get_price_limits"
        assert args[0] == "600519"
        return (1531.26, 1252.85)

    monkeypatch.setattr(td.tdf, "call", fake_call)
    data = _call("tdx_get_price_limits", {"symbol": "600519"})
    assert data["limit_up"] == 1531.26
    assert data["limit_down"] == 1252.85


def test_status_disabled(monkeypatch):
    monkeypatch.setattr(td.tdf, "is_enabled", lambda: False)
    data = _call("tdx_status", {})
    assert data["enabled"] is False


def test_enabled_tools_error_when_disabled(monkeypatch):
    monkeypatch.setattr(td.tdf, "is_enabled", lambda: False)
    data = _call("tdx_list_products", {})
    assert data["error"] is True
    assert "TDXDATA_ENABLED" in data["message"]
