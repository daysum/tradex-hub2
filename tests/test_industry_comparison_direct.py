"""行业对比（东财行业资金流）直连实现测试（v3.3.26）。

背景：`industry_comparison` 的 akshare 备源恒失败。根因是 akshare 的
`stock_sector_fund_flow_rank` 用裸 requests 打
`push2.eastmoney.com/api/qt/clist/get` —— 正是本项目统一入口**已覆盖且已配镜像**
的路径（实测裸 requests `RemoteDisconnected`，经 `em_get` 200 走 push2delay）。

本文件覆盖直连实现，并守住「不要退回去调 akshare」。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client


def clist_payload(items: list[dict], total: int | None = None):
    return {"rc": 0, "data": {"total": total if total is not None else len(items),
                              "diff": items}}


def board(code: str, name: str, pct: float, up: int, down: int, leader: str, net: float = 1e8):
    return {"f12": code, "f14": name, "f3": pct, "f104": up, "f105": down,
            "f204": leader, "f62": net, "f2": 100.0}


def fake_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


@pytest.fixture
def patch_em_get(monkeypatch):
    """按页返回响应；记录每次调用的 URL 与 params。"""
    state = {"pages": [], "calls": []}

    def fake_get(url, **kwargs):
        state["calls"].append((url, kwargs))
        idx = int(kwargs["params"]["pn"]) - 1
        if idx < len(state["pages"]):
            return state["pages"][idx]
        return fake_resp(clist_payload([]))

    monkeypatch.setattr(em_client, "em_get", fake_get)
    return state


# ════════════════════════════════════════════════════════════════
# 输出结构
# ════════════════════════════════════════════════════════════════

class TestOutputShape:
    def test_matches_em_push2_structure(self, patch_em_get):
        """必须与 em_push2 源返回同一 dict 结构（本函数的既定契约）。"""
        patch_em_get["pages"] = [fake_resp(clist_payload([
            board("BK0420", "航空机场", 1.05, 13, 0, "中国东航"),
            board("BK0421", "铁路公路", 0.08, 10, 24, "三羊马"),
        ]))]

        out = akf.fetch_industry_comparison(code="600519", top_n=20)

        assert set(out) == {"source", "date", "code", "industries"}
        assert out["code"] == "600519"
        assert len(out["industries"]) == 2
        first = out["industries"][0]
        assert set(first) == {"rank", "name", "change_pct", "up_count", "down_count", "leader"}
        assert first == {
            "rank": 1, "name": "航空机场", "change_pct": 1.05,
            "up_count": 13, "down_count": 0, "leader": "中国东航",
        }

    def test_rank_is_sequential(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([
            board("BK1", "A", 1.0, 1, 1, "x"),
            board("BK2", "B", 2.0, 2, 2, "y"),
            board("BK3", "C", 3.0, 3, 3, "z"),
        ]))]

        out = akf.fetch_industry_comparison(top_n=3)

        assert [x["rank"] for x in out["industries"]] == [1, 2, 3]

    def test_up_down_counts_are_real_not_hardcoded_zero(self, patch_em_get):
        """akshare 版本因字段集缺 f104/f105 而写死 0；直连版补上这两项取真实值。"""
        patch_em_get["pages"] = [fake_resp(clist_payload([
            board("BK1", "A", 1.0, 17, 42, "x"),
        ]))]

        out = akf.fetch_industry_comparison(top_n=1)

        assert out["industries"][0]["up_count"] == 17
        assert out["industries"][0]["down_count"] == 42

    def test_missing_fields_default_safely(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([{"f14": "只有名称"}]))]

        out = akf.fetch_industry_comparison(top_n=1)

        row = out["industries"][0]
        assert row["name"] == "只有名称"
        assert row["up_count"] == 0 and row["down_count"] == 0
        assert row["leader"] == ""


# ════════════════════════════════════════════════════════════════
# 请求参数与入口
# ════════════════════════════════════════════════════════════════

class TestRequestContract:
    def test_uses_push2_clist_via_em_get(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([board("BK1", "A", 1, 1, 1, "x")]))]

        akf.fetch_industry_comparison(top_n=1)

        url, kwargs = patch_em_get["calls"][0]
        assert url == "https://push2.eastmoney.com/api/qt/clist/get"
        p = kwargs["params"]
        assert p["fs"] == "m:90 t:2", "行业板块"
        assert p["fid0"] == "f62", "按主力净流入排序"
        assert p["po"] == "1", "降序"
        assert "f104" in p["fields"] and "f105" in p["fields"], "需含涨跌家数字段"

    def test_single_page_for_small_top_n(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload(
            [board(f"BK{i}", f"行业{i}", 1.0, 1, 1, "x") for i in range(100)]))]

        akf.fetch_industry_comparison(top_n=20)

        assert len(patch_em_get["calls"]) == 1, "top_n ≤ 100 只需 1 页"

    def test_pages_across_boundary(self, patch_em_get):
        """top_n=150 需要 2 页（每页 100）。"""
        patch_em_get["pages"] = [
            fake_resp(clist_payload([board(f"BK{i}", f"行业{i}", 1.0, 1, 1, "x")
                                     for i in range(100)])),
            fake_resp(clist_payload([board(f"BK{i}", f"行业{i}", 1.0, 1, 1, "x")
                                     for i in range(100, 200)])),
        ]

        out = akf.fetch_industry_comparison(top_n=150)

        assert len(patch_em_get["calls"]) == 2
        assert [c[1]["params"]["pn"] for c in patch_em_get["calls"]] == ["1", "2"]
        assert len(out["industries"]) == 150

    def test_stops_when_page_empty(self, patch_em_get):
        """后续页为空时应停止翻页，而不是继续空转。"""
        patch_em_get["pages"] = [fake_resp(clist_payload([board("BK1", "A", 1, 1, 1, "x")]))]

        out = akf.fetch_industry_comparison(top_n=500)

        assert len(patch_em_get["calls"]) == 2, "第 2 页为空即停"
        assert len(out["industries"]) == 1

    def test_top_n_limits_rows(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload(
            [board(f"BK{i}", f"行业{i}", 1.0, 1, 1, "x") for i in range(100)]))]

        out = akf.fetch_industry_comparison(top_n=7)

        assert len(out["industries"]) == 7


# ════════════════════════════════════════════════════════════════
# 失败路径
# ════════════════════════════════════════════════════════════════

class TestFailures:
    def test_request_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(em_client, "em_get", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="重试耗尽"):
            akf.fetch_industry_comparison(top_n=5)

    def test_empty_result_raises(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([]))]
        with pytest.raises(RuntimeError, match="返回空"):
            akf.fetch_industry_comparison(top_n=5)

    def test_date_defaults_to_today(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([board("BK1", "A", 1, 1, 1, "x")]))]

        out = akf.fetch_industry_comparison(top_n=1)

        assert out["date"], "未传 trade_date 时应补当天日期"

    def test_explicit_date_preserved(self, patch_em_get):
        patch_em_get["pages"] = [fake_resp(clist_payload([board("BK1", "A", 1, 1, 1, "x")]))]

        out = akf.fetch_industry_comparison(trade_date="2026-09-18", top_n=1)

        assert out["date"] == "2026-09-18"


# ════════════════════════════════════════════════════════════════
# 回归守卫
# ════════════════════════════════════════════════════════════════

class TestNoAkshare:
    def test_does_not_call_broken_akshare_function(self, patch_em_get, monkeypatch):
        """不得再调用 akshare 的 stock_sector_fund_flow_rank（裸 requests 被拒连）。"""
        patch_em_get["pages"] = [fake_resp(clist_payload([board("BK1", "A", 1, 1, 1, "x")]))]

        fake_ak = MagicMock()
        fake_ak.stock_sector_fund_flow_rank = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_sector_fund_flow_rank"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_industry_comparison(top_n=1)

        fake_ak.stock_sector_fund_flow_rank.assert_not_called()

    def test_output_is_not_degenerate_like_the_old_akshare_mapping(self, patch_em_get):
        """守住旧实现的一个**潜在数据错误**。

        旧代码读 akshare 的 `板块` / `涨跌幅` / `领涨股票` 三列，但这三个列名
        在 akshare 的实际输出里**都不存在**（真名是 `名称` / `今日涨跌幅` /
        `今日主力净流入最大股`）。也就是说即便 akshare 的传输没坏，
        它返回的也会是 `name="None"`、`change_pct=0.0`、`leader="None"` ——
        传输失败反而掩盖了这个错误。

        直连版按**字段名**取（f14/f3/f204），返回真实值。
        """
        patch_em_get["pages"] = [fake_resp(clist_payload([
            board("BK0420", "航空机场", 1.05, 13, 0, "中国东航"),
        ]))]

        row = akf.fetch_industry_comparison(top_n=1)["industries"][0]

        assert row["name"] == "航空机场"
        assert row["name"] != "None", "旧实现会因列名不存在而得到字符串 'None'"
        assert row["change_pct"] == pytest.approx(1.05)
        assert row["change_pct"] != 0, "旧实现会因列名不存在而恒为 0"
        assert row["leader"] == "中国东航"
        assert row["leader"] != "None"
