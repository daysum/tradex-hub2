"""Phase 3 深水区：tdx_get_f10 / tdx_get_finance / tdx_get_capital_flow_rank 离线测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from tradex.server import mcp
from tradex.tools import tdxdata_deep as deep


class FakeTable:
    def __init__(self, cols=None, rows=None):
        self.cols = cols or ["a"]
        self.rows = rows if rows is not None else [{"a": 1}]

    def to_dict(self):
        return {
            "cols": list(self.cols),
            "rows": [dict(r) for r in self.rows],
            "cmd": "CWServ.test",
            "domain": "测试",
            "provenance": {"as_of": None, "status": "provisional"},
        }


class FakeDataset:
    def __init__(self, table):
        self.table = table

    def to_dict(self):
        return {"name": "lhfp", "params": None, "table": self.table.to_dict()}


def _call(name: str, args: dict) -> dict:
    res = asyncio.run(mcp.call_tool(name, args))
    text = res[0][0].text if isinstance(res, tuple) else res[0].text
    return json.loads(text)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(deep.tdf, "is_enabled", lambda: True)


def test_f10_valid_section(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        seen["section"] = kwargs["section"]
        return FakeTable(["share_class"], [{"share_class": "A股"}])

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_f10", {"symbol": "sh600519", "section": "gsjj"})
    assert seen["method"] == "get_f10"
    assert seen["args"][0] == "600519"
    assert seen["section"] == "gsjj"
    assert data["returned_rows"] == 1
    assert "available_sections" in data
    assert "gsjj" in data["available_sections"]


def test_f10_invalid_section():
    data = _call("tdx_get_f10", {"symbol": "600519", "section": "nope"})
    assert data["error"] is True
    assert "gsjj" in data["message"]


def test_finance_valid_kind(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        seen["kind"] = kwargs["kind"]
        return FakeTable(["total_assets_yuan"], [{"total_assets_yuan": 1.0}])

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_finance", {"symbol": "600519", "kind": "zcfz"})
    assert seen["method"] == "get_finance"
    assert seen["args"][0] == "600519"
    assert seen["kind"] == "zcfz"
    assert data["returned_rows"] == 1
    assert "zcfz" in data["available_kinds"]


def test_finance_invalid_kind():
    data = _call("tdx_get_finance", {"symbol": "600519", "kind": "nope"})
    assert data["error"] is True
    assert "zcfz" in data["message"]


def test_capital_flow_rank_truncates(monkeypatch):
    rows = [{"code": f"{i:06d}", "net_inflow": i} for i in range(20)]

    def fake_call(method, *args, **kwargs):
        assert method == "get_capital_flow"
        assert args[0] is None
        return FakeTable(["code", "net_inflow"], rows)

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_capital_flow_rank", {"top_n": 5})
    assert data["returned_rows"] == 5
    assert data["total_rows"] == 20
    assert data["truncated"] is True
    assert data["top_n"] == 5
    assert "固定前 20" in data["note"]


def test_capital_flow_rank_caps_at_20(monkeypatch):
    rows = [{"code": f"{i:06d}"} for i in range(20)]
    monkeypatch.setattr(
        deep.tdf, "call", lambda *a, **k: FakeTable(["code"], rows)
    )
    data = _call("tdx_get_capital_flow_rank", {"top_n": 999})
    assert data["top_n"] == 20
    assert data["returned_rows"] == 20


def test_tools_registered():
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {
        "tdx_get_f10",
        "tdx_get_finance",
        "tdx_get_capital_flow_rank",
        "tdx_get_lhb_review",
    } <= tools


def test_lhb_review_maps_known_fields(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        seen["date"] = kwargs.get("date")
        rows = [
            {"N001": "2026-10-29", "N002": "69", "N003": "-1251883100.00",
             "N004": "67", "N005": "2", "N006": "0"},
            {"N001": "2026-10-28", "N002": "67", "N003": "456196000.00",
             "N004": "67", "N005": "0", "N006": "0"},
        ]
        return FakeDataset(FakeTable(["N001", "N002", "N003", "N004", "N005", "N006"], rows))

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_lhb_review", {"date": "2026-10-29", "max_rows": 1})
    assert seen["method"] == "get"
    assert seen["args"] == ("lhfp",)
    assert seen["date"] == "2026-10-29"
    assert data["returned_rows"] == 1
    assert data["total_rows"] == 2
    assert data["truncated"] is True
    row = data["rows"][0]
    assert row["date"] == "2026-10-29"
    assert row["listed_count"] == 69
    assert row["net_amount_yuan"] == pytest.approx(-1251883100.0)
    assert row["raw"] == {"N004": "67", "N005": "2", "N006": "0"}
    assert data["mapped_fields"]["N002"].startswith("listed_count")
    assert "N004" in data["unmapped_fields"]
    assert "TDX" in data["note"]


def test_lhb_review_empty_returns_error(monkeypatch):
    monkeypatch.setattr(
        deep.tdf, "call", lambda *a, **k: FakeDataset(FakeTable(["N001"], []))
    )
    data = _call("tdx_get_lhb_review", {})
    assert data["error"] is True


def test_lhb_stocks_list_mapping(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["method"] = method
        seen["args"] = args
        rows = [
            {"N001": "泸州老窖", "N002": "000568", "N003": "0", "N004": "77.070",
             "N005": "10.0057094", "N006": "460963060.0000",
             "N007": "3116403391.0000", "N008": None},
            {"N001": "金安国纪", "N002": "002636", "N003": "0", "N004": "63.680",
             "N005": "10.0017274", "N006": "277936189.0000",
             "N007": "10199257404.0000", "N008": "3日"},
        ]
        return FakeTable(["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"], rows)

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_lhb_stocks", {"date": "2026-10-30", "view": "list"})
    assert seen["method"] == "call"
    assert seen["args"][0] == "DataAggregation.lhbsj"
    assert seen["args"][2][1] == "2026-10-30"
    row = data["rows"][0]
    assert row["name"] == "泸州老窖"
    assert row["code"] == "000568"
    assert row["market"] == 0
    assert row["price"] == pytest.approx(77.07)
    assert row["change_pct"] == pytest.approx(10.0057094)
    assert row["net_buy_amount_yuan"] == pytest.approx(460963060.0)
    assert row["amount_yuan"] == pytest.approx(3116403391.0)
    assert data["rows"][1]["tag"] == "3日"


def test_lhb_stocks_detail_mapping(monkeypatch):
    seen = {}

    def fake_call(method, *args, **kwargs):
        seen["args"] = args
        rows = [
            {"N001": "000568", "N002": "0", "N003": "泸州老窖", "N004": "10.01",
             "N005": "3116403500.00", "N006": "460963000.00", "N007": "14.791506",
             "N008": "070001", "N009": "2026-10-30", "N010": "[]",
             "N011": None, "N012": "clickEvent1",
             "N013": "[{\"code\":\"880564\",\"name\":\"白酒概念\",\"setcode\":\"1\"}]"},
        ]
        return FakeTable(list(rows[0]), rows)

    monkeypatch.setattr(deep.tdf, "call", fake_call)
    data = _call("tdx_get_lhb_stocks", {"view": "detail", "max_rows": 1})
    assert seen["args"][0] == "CWServ.ph_zt_lhb_sy_zy"
    assert seen["args"][2][2] == ""  # date 留空
    row = data["rows"][0]
    assert row["code"] == "000568"
    assert row["market"] == 0
    assert row["name"] == "泸州老窖"
    assert row["change_pct"] == pytest.approx(10.01)
    assert row["amount_yuan"] == pytest.approx(3116403500.0)
    assert row["net_buy_amount_yuan"] == pytest.approx(460963000.0)
    assert row["net_buy_ratio_pct"] == pytest.approx(14.791506)
    assert row["reason_code"] == "070001"
    assert row["date"] == "2026-10-30"
    assert row["tags"] == []
    assert row["concepts"] == [{"code": "880564", "name": "白酒概念", "setcode": "1"}]
    assert row["raw"] == {"N011": None, "N012": "clickEvent1"}


def test_lhb_stocks_invalid_view():
    data = _call("tdx_get_lhb_stocks", {"view": "bad"})
    assert data["error"] is True


def test_lhb_stocks_empty(monkeypatch):
    monkeypatch.setattr(
        deep.tdf, "call", lambda *a, **k: FakeTable(["N001"], [])
    )
    data = _call("tdx_get_lhb_stocks", {})
    assert data["error"] is True
