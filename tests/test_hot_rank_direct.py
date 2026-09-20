"""东财人气榜直连实现测试（v3.3.24）。

背景：`hot_rank` 主源 `akshare_hot_rank` 的 `rank` / `up` 两个 endpoint 失败。
根因不是「源挂了」，而是 akshare 的实现分两步：
  1. 裸 requests POST `emappdata.eastmoney.com/stockrank/*` 取榜单（**可达**）
  2. 裸 requests GET `push2.eastmoney.com/api/qt/ulist.np/get` 取行情（**被拒连**）
而同一个 URL 经 `em_get` 可正常取到（走 push2delay 镜像）。

本文件覆盖直连实现，重点验证**按代码 join 而非按位置对齐**——
两个列表来自独立请求，条数/顺序可能不一致，按位置拼会静默错位。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client


def rank_payload(items: list[tuple[str, int]], change_field: str | None = None):
    """emappdata 榜单响应。items: [(sc, rk), ...]"""
    data = []
    for sc, rk in items:
        rec = {"sc": sc, "rk": rk}
        if change_field:
            rec[change_field] = rk * 2
        data.append(rec)
    return data


def quote_payload(items: list[tuple[str, float, float, str]]):
    """push2 ulist 行情响应。items: [(6位代码, 最新价, 涨跌幅, 名称), ...]"""
    return {"data": {"diff": [
        {"f2": price, "f3": pct, "f12": code, "f14": name}
        for code, price, pct, name in items
    ]}}


def fake_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


@pytest.fixture
def patch_em(monkeypatch):
    """返回一个可按调用顺序配置 em_post / em_get 返回值的助手。"""
    state = {"post": None, "get": None, "post_calls": [], "get_calls": []}

    def fake_post(url, **kwargs):
        state["post_calls"].append((url, kwargs))
        outcome = state["post"]
        return outcome

    def fake_get(url, **kwargs):
        state["get_calls"].append((url, kwargs))
        outcome = state["get"]
        return outcome

    monkeypatch.setattr(em_client, "em_post", fake_post)
    monkeypatch.setattr(em_client, "em_get", fake_get)
    return state


# ════════════════════════════════════════════════════════════════
# 正常路径
# ════════════════════════════════════════════════════════════════

class TestRank:
    def test_rank_columns_and_values(self, patch_em):
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1), ("SH600825", 2)])})
        patch_em["get"] = fake_resp(quote_payload([
            ("002185", 18.03, 10.01, "华天科技"),
            ("600825", 5.20, -2.00, "新华传媒"),
        ]))

        df = akf.fetch_hot_rank_data(endpoint="rank")

        assert list(df.columns) == ["当前排名", "代码", "股票名称", "最新价", "涨跌额", "涨跌幅"]
        assert len(df) == 2
        assert df.iloc[0]["当前排名"] == 1
        assert df.iloc[0]["代码"] == "SZ002185"
        assert df.iloc[0]["股票名称"] == "华天科技"
        assert df.iloc[0]["最新价"] == pytest.approx(18.03)
        assert df.iloc[0]["涨跌幅"] == pytest.approx(10.01)
        assert df.iloc[0]["涨跌额"] == pytest.approx(18.03 * 10.01 / 100)

    def test_uses_em_post_for_emappdata_and_em_get_for_quotes(self, patch_em):
        """榜单用 POST、行情用 GET，两者都必须走统一防封层。"""
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1)])})
        patch_em["get"] = fake_resp(quote_payload([("002185", 18.03, 10.01, "华天科技")]))

        akf.fetch_hot_rank_data(endpoint="rank")

        assert len(patch_em["post_calls"]) == 1
        post_url, post_kw = patch_em["post_calls"][0]
        assert "emappdata.eastmoney.com/stockrank/getAllCurrentList" in post_url
        assert post_kw["json"]["appId"] == "appId01"

        assert len(patch_em["get_calls"]) == 1
        get_url, get_kw = patch_em["get_calls"][0]
        assert "push2.eastmoney.com/api/qt/ulist.np/get" in get_url
        assert get_kw["params"]["secids"] == "0.002185"

    def test_market_prefix_mapping(self, patch_em):
        """SZ → 0.，SH → 1.。"""
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ000001", 1), ("SH600519", 2)])})
        patch_em["get"] = fake_resp(quote_payload([]))
        patch_em["get"]["data"]["diff"] = []

        # ulist 返回空 → 应抛错（此用例只验证 secids 拼装，故先补上行情）
        patch_em["get"] = fake_resp(quote_payload([
            ("000001", 12.0, 1.0, "平安银行"), ("600519", 1500.0, 1.0, "贵州茅台"),
        ]))

        akf.fetch_hot_rank_data(endpoint="rank")

        assert patch_em["get_calls"][0][1]["params"]["secids"] == "0.000001,1.600519"


class TestUp:
    def test_up_includes_change_column(self, patch_em):
        patch_em["post"] = fake_resp(
            {"data": rank_payload([("SZ301302", 1638)], change_field="hrc")})
        patch_em["get"] = fake_resp(quote_payload([("301302", 21.01, 2.24, "华如科技")]))

        df = akf.fetch_hot_rank_data(endpoint="up")

        assert list(df.columns) == [
            "排名较昨日变动", "当前排名", "代码", "股票名称", "最新价", "涨跌额", "涨跌幅"]
        assert df.iloc[0]["排名较昨日变动"] == 1638 * 2
        assert df.iloc[0]["股票名称"] == "华如科技"

    def test_up_uses_his_rc_endpoint(self, patch_em):
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ301302", 1)], change_field="hrc")})
        patch_em["get"] = fake_resp(quote_payload([("301302", 21.0, 1.0, "华如科技")]))

        akf.fetch_hot_rank_data(endpoint="up")

        assert "getAllHisRcList" in patch_em["post_calls"][0][0]


# ════════════════════════════════════════════════════════════════
# 按代码 join —— 本文件最重要的一组
# ════════════════════════════════════════════════════════════════

class TestJoinByCodeNotPosition:
    def test_order_mismatch_does_not_misalign(self, patch_em):
        """两个列表顺序不同时，代码与名称仍须正确配对。

        按位置拼会得到「SZ000002 → 华天科技」这种错配；
        按代码 join 才能保证「代码 ↔ 名称」永远对应。
        """
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1), ("SZ000002", 2)])})
        # 行情故意**倒序**返回
        patch_em["get"] = fake_resp(quote_payload([
            ("000002", 8.88, -1.00, "万科A"),
            ("002185", 18.03, 10.01, "华天科技"),
        ]))

        df = akf.fetch_hot_rank_data(endpoint="rank")

        by_code = {r["代码"]: r for _, r in df.iterrows()}
        assert by_code["SZ002185"]["股票名称"] == "华天科技"
        assert by_code["SZ002185"]["最新价"] == pytest.approx(18.03)
        assert by_code["SZ000002"]["股票名称"] == "万科A"
        assert by_code["SZ000002"]["最新价"] == pytest.approx(8.88)

    def test_rank_order_preserved(self, patch_em):
        """输出顺序以**榜单**为准（保持排名有序），不随行情返回顺序变。"""
        patch_em["post"] = fake_resp({"data": rank_payload(
            [("SZ002185", 1), ("SZ000002", 2), ("SH600519", 3)])})
        patch_em["get"] = fake_resp(quote_payload([
            ("600519", 1500.0, 1.0, "贵州茅台"),
            ("000002", 8.88, -1.0, "万科A"),
            ("002185", 18.03, 10.01, "华天科技"),
        ]))

        df = akf.fetch_hot_rank_data(endpoint="rank")

        assert list(df["当前排名"]) == [1, 2, 3]
        assert list(df["代码"]) == ["SZ002185", "SZ000002", "SH600519"]

    def test_missing_quote_keeps_row_with_code(self, patch_em):
        """行情缺某只票（停牌/镜像未返回）时，该行仍保留且代码正确，价格为空。

        关键是**不能因为少一条就把后面的行整体错位**。
        """
        patch_em["post"] = fake_resp({"data": rank_payload(
            [("SZ002185", 1), ("SH600825", 2), ("SZ002453", 3)])})
        # 故意缺中间那条（600825）
        patch_em["get"] = fake_resp(quote_payload([
            ("002185", 18.03, 10.01, "华天科技"),
            ("002453", 5.34, 10.10, "华软科技"),
        ]))

        df = akf.fetch_hot_rank_data(endpoint="rank")

        assert len(df) == 3, "缺行情不应丢行"
        row2 = df[df["代码"] == "SH600825"].iloc[0]
        assert pd.isna(row2["最新价"])
        assert pd.isna(row2["股票名称"])          # pandas 用 NaN 表示缺失
        row3 = df[df["代码"] == "SZ002453"].iloc[0]
        assert row3["股票名称"] == "华软科技", "缺失一条不应导致后续错位"
        assert row3["最新价"] == pytest.approx(5.34)

    def test_extra_quote_ignored(self, patch_em):
        """行情多返回一条（榜单里没有）时，不应多出行。"""
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1)])})
        patch_em["get"] = fake_resp(quote_payload([
            ("002185", 18.03, 10.01, "华天科技"),
            ("999999", 1.0, 0.0, "不该出现"),
        ]))

        df = akf.fetch_hot_rank_data(endpoint="rank")

        assert len(df) == 1


# ════════════════════════════════════════════════════════════════
# 失败路径
# ════════════════════════════════════════════════════════════════

class TestFailures:
    def test_emappdata_exhausted_raises(self, patch_em):
        patch_em["post"] = None
        with pytest.raises(RuntimeError, match="emappdata"):
            akf.fetch_hot_rank_data(endpoint="rank")

    def test_emappdata_empty_raises(self, patch_em):
        patch_em["post"] = fake_resp({"data": []})
        with pytest.raises(RuntimeError, match="空榜单"):
            akf.fetch_hot_rank_data(endpoint="rank")

    def test_quotes_exhausted_raises(self, patch_em):
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1)])})
        patch_em["get"] = None
        with pytest.raises(RuntimeError, match="push2 ulist"):
            akf.fetch_hot_rank_data(endpoint="rank")

    def test_quotes_empty_raises(self, patch_em):
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1)])})
        patch_em["get"] = fake_resp({"data": {"diff": []}})
        with pytest.raises(RuntimeError, match="空行情"):
            akf.fetch_hot_rank_data(endpoint="rank")

    def test_unknown_endpoint_still_raises_value_error(self, monkeypatch):
        fake_ak = MagicMock()
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)
        with pytest.raises(ValueError, match="Unknown hot_rank_data endpoint"):
            akf.fetch_hot_rank_data(endpoint="nope")


# ════════════════════════════════════════════════════════════════
# 回归守卫
# ════════════════════════════════════════════════════════════════

class TestNoAkshareForDirectEndpoints:
    @pytest.mark.parametrize("endpoint", ["rank", "up"])
    def test_does_not_call_broken_akshare_functions(self, endpoint, patch_em, monkeypatch):
        """rank / up 不得再调用 akshare 那两个裸 requests 版本。"""
        patch_em["post"] = fake_resp({"data": rank_payload([("SZ002185", 1)])})
        patch_em["get"] = fake_resp(quote_payload([("002185", 18.03, 10.01, "华天科技")]))

        fake_ak = MagicMock()
        fake_ak.stock_hot_rank_em = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_hot_rank_em"))
        fake_ak.stock_hot_up_em = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_hot_up_em"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_hot_rank_data(endpoint=endpoint)

        fake_ak.stock_hot_rank_em.assert_not_called()
        fake_ak.stock_hot_up_em.assert_not_called()

    @pytest.mark.parametrize("endpoint,ak_func", [
        ("detail", "stock_hot_rank_detail_em"),
        ("realtime", "stock_hot_rank_detail_realtime_em"),
        ("keyword", "stock_hot_keyword_em"),
        ("latest", "stock_hot_rank_latest_em"),
        ("relate", "stock_hot_rank_relate_em"),
    ])
    def test_other_endpoints_still_use_akshare(self, endpoint, ak_func, monkeypatch):
        """其余 5 个 endpoint 只打 emappdata（实测可达），保持走 akshare。"""
        fake_ak = MagicMock()
        getattr(fake_ak, ak_func).return_value = pd.DataFrame({"x": [1]})
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        df = akf.fetch_hot_rank_data(endpoint=endpoint, symbol="SZ000665")

        assert not df.empty
        getattr(fake_ak, ak_func).assert_called_once()
