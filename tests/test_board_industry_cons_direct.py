"""行业板块成分股直连实现测试（v3.3.28）。

背景：`board_industry_cons_em` 此前**两个源都不覆盖** ——
主源 akshare 用裸 requests 打 `29.push2.eastmoney.com/api/qt/clist/get` 被拒连；
备源 ths 明确不支持该 endpoint。导致 `get_industry_stocks` / `get_competitors`
两个工具恒报「All sources failed」。

实测 `push2delay.eastmoney.com` 的 clist 可取到（经 `em_get` 走镜像），
故改为本项目直连。

本文件覆盖三层：板块名→BK 代码解析、成分股拉取与字段映射、以及两条回归守卫。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client

# 与 akshare stock_board_industry_cons_em 一致的 16 列
EXPECTED_COLUMNS = [
    "序号", "代码", "名称", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
    "振幅", "最高", "最低", "今开", "昨收", "换手率", "市盈率-动态", "市净率",
]


def board_item(code="BK0896", name="白酒"):
    return {"f12": code, "f14": name}


def cons_item(code="601579", name="会稽山", pct=10.02):
    """成分股条目（key 顺序刻意打乱，验证「按字段名取」）。"""
    return {
        "f14": name, "f3": pct, "f2": 32.84, "f12": code, "f4": 2.99,
        "f5": 228273, "f6": 727993777.0, "f7": 9.15, "f8": 4.76, "f9": 61.2,
        "f15": 32.84, "f16": 30.11, "f17": 30.11, "f18": 29.85, "f23": 4.19,
    }


def page(items, total=None):
    return {"rc": 0, "data": {"total": total if total is not None else len(items),
                              "diff": items}}


def fake_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


@pytest.fixture(autouse=True)
def clear_board_cache():
    """清空模块级板块映射缓存 —— 否则用例之间会互相污染。"""
    akf._BOARD_MAP_CACHE.clear()
    yield
    akf._BOARD_MAP_CACHE.clear()


@pytest.fixture
def fake_em(monkeypatch):
    """按 (fs, pn) 分派的假响应。

    board_routes / cons 里放的**已经是响应对象**（fake_resp(...)），
    本 fixture 直接返回，不再二次包装。
    """
    state = {"board_routes": {}, "cons": [], "calls": []}

    def fake_get(url, **kwargs):
        p = kwargs.get("params") or {}
        state["calls"].append(p)
        fs = p.get("fs", "")
        if fs.startswith("b:"):
            idx = int(p.get("pn", 1)) - 1
            items = state["cons"]
            if idx < len(items):
                return items[idx]
            return fake_resp(page([]))
        route = state["board_routes"].get(fs)
        if route is None:
            return fake_resp(page([]))
        return route.get(int(p.get("pn", 1)), fake_resp(page([])))

    monkeypatch.setattr(em_client, "em_get", fake_get)
    return state


# ════════════════════════════════════════════════════════════════
# 板块名 → BK 代码
# ════════════════════════════════════════════════════════════════

class TestBoardCodeResolution:
    def test_bk_code_passthrough(self, fake_em):
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        assert len(df) == 1
        # 直接用了 BK 代码 → 不应去拉板块列表
        assert all(not (c.get("fs", "").startswith("m:90")) for c in fake_em["calls"])

    def test_resolves_from_industry_taxonomy(self, fake_em):
        fake_em["board_routes"] = {"m:90+t:2": {1: fake_resp(page([board_item("BK1283", "银行")]))}}
        fake_em["cons"] = [fake_resp(page([cons_item("600036", "招商银行", 1.2)]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="银行")

        assert df.iloc[0]["名称"] == "招商银行"
        assert fake_em["calls"][-1]["fs"] == "b:BK1283 f:!50"

    def test_resolves_from_concept_taxonomy(self, fake_em):
        """⚠️ 关键：`白酒` 只存在于**概念板块**。

        只搜行业板块会直接找不到（akshare 原版就有这个毛病）。
        """
        fake_em["board_routes"] = {
            "m:90+t:2": {1: fake_resp(page([board_item("BK1036", "半导体")]))},
            "m:90+t:3": {1: fake_resp(page([board_item("BK0896", "白酒")]))},
        }
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="白酒")

        assert len(df) == 1
        assert fake_em["calls"][-1]["fs"] == "b:BK0896 f:!50"

    def test_not_found_error_lists_taxonomies(self, fake_em):
        fake_em["board_routes"] = {
            "m:90+t:2": {1: fake_resp(page([board_item("BK1", "甲")]))},
            "m:90+t:3": {1: fake_resp(page([board_item("BK2", "乙")]))},
        }

        with pytest.raises(RuntimeError) as ei:
            akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="不存在")

        msg = str(ei.value)
        assert "行业板块" in msg and "概念板块" in msg

    def test_empty_symbol_raises(self, fake_em):
        with pytest.raises(RuntimeError, match="不能为空"):
            akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="")

    def test_pages_board_list_beyond_first_100(self, fake_em):
        """服务端把 pz 硬顶在 100，名字可能在第 2 页 —— 必须翻页才能找到。"""
        fake_em["board_routes"] = {
            "m:90+t:2": {
                1: fake_resp(page([board_item(f"BK{i}", f"甲{i}") for i in range(100)],
                                  total=200)),
                2: fake_resp(page([board_item("BK1283", "银行")], total=200)),
            },
        }
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="银行")

        assert len(df) == 1
        board_calls = [c for c in fake_em["calls"] if c.get("fs") == "m:90+t:2"]
        assert [c["pn"] for c in board_calls] == ["1", "2"], "应翻到第 2 页"


class TestBoardMapCache:
    def test_second_lookup_hits_cache(self, fake_em):
        """板块映射按进程缓存，第二次查询同一分类应零请求。"""
        fake_em["board_routes"] = {"m:90+t:2": {1: fake_resp(page([board_item("BK1283", "银行")]))}}
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="银行")
        before = len([c for c in fake_em["calls"] if c.get("fs", "").startswith("m:90")])
        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="银行")
        after = len([c for c in fake_em["calls"] if c.get("fs", "").startswith("m:90")])

        assert after == before, "第二次不应再请求板块列表"

    def test_resume_from_partial_cache(self, fake_em):
        """未翻完的分类应记下页数，下次接着翻（断点续取），而不是从头再来。"""
        fake_em["board_routes"] = {
            "m:90+t:2": {
                1: fake_resp(page([board_item(f"BK{i}", f"甲{i}") for i in range(100)],
                                  total=300)),
                2: fake_resp(page([board_item("BK1283", "银行")], total=300)),
            },
        }
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        # 第一次查「甲0」→ 命中第 1 页即返回（只翻了 1 页）
        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="甲0")
        assert akf._BOARD_MAP_CACHE["m:90+t:2"]["pages"] == 1

        # 第二次查「银行」→ 应从第 2 页接着翻，而不是重新请求第 1 页
        fake_em["calls"].clear()
        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="银行")

        pns = [c["pn"] for c in fake_em["calls"] if c.get("fs") == "m:90+t:2"]
        assert pns == ["2"], f"应从第 2 页续取，实际 {pns}"


# ════════════════════════════════════════════════════════════════
# 成分股拉取与字段映射
# ════════════════════════════════════════════════════════════════

class TestConstituents:
    def test_columns_match_akshare(self, fake_em):
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        assert list(df.columns) == EXPECTED_COLUMNS

    def test_field_mapping_by_name_not_position(self, fake_em):
        """**按字段名映射**：响应 key 顺序被打乱也不应错位。

        akshare 原版按位置赋列名，依赖响应 key 的精确顺序 —— 上游改序即错位。
        """
        fake_em["cons"] = [fake_resp(page([cons_item("601579", "会稽山", 10.02)]))]

        row = akf.fetch_industry_data(
            endpoint="board_industry_cons_em", industry="BK0896").iloc[0]

        assert row["代码"] == "601579"
        assert row["名称"] == "会稽山"
        assert row["最新价"] == pytest.approx(32.84)
        assert row["涨跌幅"] == pytest.approx(10.02)
        assert row["涨跌额"] == pytest.approx(2.99)
        assert row["成交量"] == pytest.approx(228273)
        assert row["成交额"] == pytest.approx(727993777.0)
        assert row["振幅"] == pytest.approx(9.15)
        assert row["最高"] == pytest.approx(32.84)
        assert row["最低"] == pytest.approx(30.11)
        assert row["今开"] == pytest.approx(30.11)
        assert row["昨收"] == pytest.approx(29.85)
        assert row["换手率"] == pytest.approx(4.76)
        assert row["市盈率-动态"] == pytest.approx(61.2)
        assert row["市净率"] == pytest.approx(4.19)

    def test_sorted_by_change_pct_desc_with_sequence(self, fake_em):
        fake_em["cons"] = [fake_resp(page([
            cons_item("1", "低", 1.0),
            cons_item("2", "高", 9.0),
            cons_item("3", "中", 5.0),
        ]))]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        assert list(df["名称"]) == ["高", "中", "低"]
        assert list(df["序号"]) == [1, 2, 3]

    def test_pages_constituents(self, fake_em):
        """成分股本身也会超一页（如 新能源车 716 只）。"""
        fake_em["cons"] = [
            fake_resp(page([cons_item(f"00000{i}", f"股{i}", 1.0) for i in range(100)],
                           total=150)),
            fake_resp(page([cons_item(f"00001{i}", f"股1{i}", 2.0) for i in range(50)],
                           total=150)),
        ]

        df = akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        assert len(df) == 150
        assert list(df["序号"]) == list(range(1, 151))

    def test_empty_constituents_raises(self, fake_em):
        fake_em["cons"] = [fake_resp(page([]))]
        with pytest.raises(RuntimeError, match="返回空成分股"):
            akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

    def test_request_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(em_client, "em_get", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="重试耗尽"):
            akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

    def test_uses_clist_via_em_get(self, fake_em):
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        cons_calls = [c for c in fake_em["calls"] if c.get("fs", "").startswith("b:")]
        assert cons_calls and cons_calls[0]["fs"] == "b:BK0896 f:!50"
        assert cons_calls[0]["fields"].count("f") >= 20


# ════════════════════════════════════════════════════════════════
# 回归守卫
# ════════════════════════════════════════════════════════════════

class TestNoAkshare:
    def test_does_not_call_akshare_cons(self, fake_em, monkeypatch):
        """不得再调用 akshare 的 stock_board_industry_cons_em。"""
        fake_em["cons"] = [fake_resp(page([cons_item()]))]

        fake_ak = MagicMock()
        fake_ak.stock_board_industry_cons_em = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_board_industry_cons_em"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_industry_data(endpoint="board_industry_cons_em", industry="BK0896")

        fake_ak.stock_board_industry_cons_em.assert_not_called()
