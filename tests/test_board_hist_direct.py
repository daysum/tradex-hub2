"""板块历史行情直连实现测试（v3.3.29）。

背景：`board_industry_hist_em`（行业历史行情）此前**两个源都不覆盖** ——
主源 akshare 打 `7.push2his.eastmoney.com/api/qt/stock/kline/get`（`secid=90.BKxxxx`），
而东财板块日K路由**整条链路故障**（所有 push2his* 主机连接被拒，
`push2delay` 认得出板块但 `klines` 为空）；备源 ths 明确不支持。

实测**同花顺板块K线免鉴权可用**：
  `d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js`   → 最近 ~140 根
  `d.10jqka.com.cn/v6/line/bk_{code}/{seg}/{year}.js` → 某年全量
  JSONP 载荷 data 每行 `日期,开,高,低,收,成交量,成交额,...`（同花顺顺序：开高低收）
  板块代码：行业 881xxx（白酒=881273、半导体=881121）

本文件覆盖：JSONP 解析、周期映射、日期区间、按年补数、降级顺序与两条守卫。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradex.data_sources import akshare_fetchers as akf
from tradex.data_sources import em_client
from tradex.data_sources import ths_fetchers as ths

EXPECTED_COLUMNS = [
    "日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额",
    "成交量", "成交额", "振幅", "换手率",
]


def board_js(name="白酒", rows=None, year=None):
    """构造同花顺板块K线 JSONP 响应文本。rows: [(日期,开,高,低,收,量,额), ...]"""
    if rows is None:
        rows = [
            ("20260916", 1927.455, 1929.821, 1904.358, 1929.734, 98616573, 6618488400.0),
            ("20260917", 1922.996, 1926.691, 1907.624, 1918.550, 87969797, 5275110400.0),
            ("20260918", 1918.386, 1939.587, 1915.038, 1932.928, 101807554, 6724935100.0),
        ]
    data = ";".join(",".join(str(x) for x in r) + ",,," for r in rows)
    payload = {"year": year or {"2026": len(rows)}, "total": len(rows),
               "num": len(rows), "name": name, "data": data}
    return f"quotebridge_v6_line_bk_xxx_01_last({json.dumps(payload, ensure_ascii=False)})"


def fake_resp(text):
    r = MagicMock()
    r.status_code = 200
    r.content = text.encode("gbk", errors="replace")
    r.text = text
    return r


@pytest.fixture(autouse=True)
def clear_caches():
    ths._THS_BOARD_MAP_CACHE.clear()
    akf._BOARD_MAP_CACHE.clear()
    yield
    ths._THS_BOARD_MAP_CACHE.clear()
    akf._BOARD_MAP_CACHE.clear()


@pytest.fixture
def ths_client(monkeypatch):
    """替换 ths_fetchers 的具名客户端，按 URL 分派响应。"""
    state = {"by_span": {}, "calls": []}

    def fake_get(url, **kwargs):
        state["calls"].append(url)
        for span, outcome in state["by_span"].items():
            if url.endswith(f"/{span}.js"):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return None

    monkeypatch.setattr(ths, "get_client", lambda name: MagicMock(get=fake_get))
    return state


@pytest.fixture
def ths_map(monkeypatch):
    """预置「名称 → 同花顺代码」映射（避免依赖 akshare 的板块列表）。"""
    ths._THS_BOARD_MAP_CACHE["industry"] = {"白酒": "881273", "半导体": "881121"}
    return ths._THS_BOARD_MAP_CACHE["industry"]


# ════════════════════════════════════════════════════════════════
# 同花顺板块K线
# ════════════════════════════════════════════════════════════════

class TestThsBoardHist:
    def test_columns_match_akshare(self, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js())

        df = ths.fetch_ths_board_hist(symbol="白酒", period="日k")

        assert list(df.columns) == EXPECTED_COLUMNS
        assert len(df) == 3

    def test_parses_open_high_low_close_order(self, ths_client, ths_map):
        """同花顺顺序是 **开,高,低,收**（不是开,收,高,低）—— 必须按此解析。"""
        ths_client["by_span"]["last"] = fake_resp(board_js(rows=[
            ("20260918", 1918.386, 1939.587, 1915.038, 1932.928, 101807554, 6724935100.0),
        ]))

        row = ths.fetch_ths_board_hist(symbol="白酒", period="日k").iloc[0]

        assert row["开盘"] == pytest.approx(1918.386)
        assert row["最高"] == pytest.approx(1939.587)
        assert row["最低"] == pytest.approx(1915.038)
        assert row["收盘"] == pytest.approx(1932.928)

    def test_computes_change_and_amplitude_from_prev_close(self, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js())

        df = ths.fetch_ths_board_hist(symbol="白酒", period="日k")

        # 首行无前收 → 空
        assert pd.isna(df.iloc[0]["涨跌幅"])
        row = df.iloc[-1]
        prev_close = 1918.550
        assert row["涨跌额"] == pytest.approx(1932.928 - prev_close)
        assert row["涨跌幅"] == pytest.approx((1932.928 - prev_close) / prev_close * 100)
        assert row["振幅"] == pytest.approx((1939.587 - 1915.038) / prev_close * 100)

    def test_turnover_always_empty(self, ths_client, ths_map):
        """同花顺板块K线不提供换手率 → 该列恒为空（不是 0）。"""
        ths_client["by_span"]["last"] = fake_resp(board_js())

        df = ths.fetch_ths_board_hist(symbol="白酒", period="日k")

        assert df["换手率"].isna().all()

    @pytest.mark.parametrize("period,seg", [
        ("日k", "01"), ("周k", "11"), ("月k", "21"),
        ("daily", "01"), ("weekly", "11"), ("monthly", "21"),
    ])
    def test_period_segment_mapping(self, period, seg, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js())

        ths.fetch_ths_board_hist(symbol="白酒", period=period)

        assert f"/{seg}/last.js" in ths_client["calls"][0]

    def test_unsupported_period_raises(self, ths_client, ths_map):
        with pytest.raises(ValueError, match="不支持的周期"):
            ths.fetch_ths_board_hist(symbol="白酒", period="5分钟")

    def test_code_passthrough(self, ths_client, ths_map):
        """传纯数字代码时不应再查名称映射。"""
        ths_client["by_span"]["last"] = fake_resp(board_js())

        ths.fetch_ths_board_hist(symbol="881273", period="日k")

        assert "/bk_881273/" in ths_client["calls"][0]

    def test_unknown_name_raises(self, ths_client, ths_map):
        with pytest.raises(RuntimeError, match="未找到"):
            ths.fetch_ths_board_hist(symbol="不存在的板块", period="日k")

    def test_date_range_filter(self, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js(rows=[
            ("20260901", 1, 2, 0.5, 1.5, 1, 1),
            ("20260910", 1, 2, 0.5, 1.5, 1, 1),
            ("20260918", 1, 2, 0.5, 1.5, 1, 1),
        ]))

        df = ths.fetch_ths_board_hist(symbol="白酒", period="日k",
                                      start_date="20260905", end_date="20260915")

        assert [str(d) for d in df["日期"]] == ["2026-09-10"]

    def test_fetches_year_files_when_range_is_earlier(self, ths_client, ths_map):
        """start_date 早于 last.js 覆盖范围 → 应按年补取。"""
        ths_client["by_span"]["last"] = fake_resp(board_js(rows=[
            ("20260918", 1, 2, 0.5, 1.5, 1, 1),
        ]))
        ths_client["by_span"]["2026"] = fake_resp(board_js(rows=[
            ("20260601", 1, 2, 0.5, 1.5, 1, 1),
            ("20260918", 1, 2, 0.5, 1.5, 1, 1),
        ]))

        df = ths.fetch_ths_board_hist(symbol="白酒", period="日k",
                                      start_date="20260601", end_date="20260918")

        assert "/2026.js" in " ".join(ths_client["calls"])
        assert len(df) == 2, "应按日期去重合并"
        assert str(df.iloc[0]["日期"]) == "2026-06-01"

    def test_empty_data_raises(self, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js(rows=[]))

        with pytest.raises(RuntimeError, match="返回空"):
            ths.fetch_ths_board_hist(symbol="白酒", period="日k")

    def test_exhausted_raises(self, ths_client, ths_map):
        ths_client["by_span"]["last"] = None   # fake_get 返回 None

        with pytest.raises(RuntimeError, match="重试耗尽"):
            ths.fetch_ths_board_hist(symbol="白酒", period="日k")

    def test_goes_through_named_client(self, ths_client, ths_map):
        """必须经统一防封层（get_client("ths")），不能裸请求。"""
        ths_client["by_span"]["last"] = fake_resp(board_js())

        ths.fetch_ths_board_hist(symbol="白酒", period="日k")

        assert "d.10jqka.com.cn" in ths_client["calls"][0]


# ════════════════════════════════════════════════════════════════
# 降级编排
# ════════════════════════════════════════════════════════════════

class TestBoardHistFallback:
    def _ths_ok(self, monkeypatch, ths_client, ths_map):
        ths_client["by_span"]["last"] = fake_resp(board_js())

    def test_ths_preferred_so_eastmoney_not_called(self, monkeypatch, ths_client, ths_map):
        """同花顺优先：成功时不应再去碰东财（避免昂贵的 BK 解析）。"""
        self._ths_ok(monkeypatch, ths_client, ths_map)
        called = []
        monkeypatch.setattr(em_client, "em_get", lambda *a, **k: called.append(1) or None)

        df = akf.fetch_industry_data(endpoint="board_industry_hist_em",
                                     industry="白酒", period="日k")

        assert len(df) == 3
        assert called == [], "同花顺成功时不该请求东财"

    def test_falls_back_to_eastmoney(self, monkeypatch, ths_map):
        """同花顺失败 → 应降级到东财（走 em_get）。"""
        monkeypatch.setattr(ths, "get_client",
                            lambda name: MagicMock(get=lambda *a, **k: None))
        # 东财层：先给板块列表（解析 BK 代码），再给 kline
        def fake_get(url, **kwargs):
            p = kwargs.get("params") or {}
            if p.get("fs") == "m:90+t:2":
                r = MagicMock(); r.status_code = 200
                r.json.return_value = {"data": {"total": 1, "diff": [{"f12": "BK0896", "f14": "白酒"}]}}
                return r
            r = MagicMock(); r.status_code = 200
            r.json.return_value = {"data": {"klines": [
                "2026-09-18,1918.386,1932.928,1939.587,1915.038,101807554,6724935100,1.28,0.75,14.38,None"
            ]}}
            return r

        monkeypatch.setattr(em_client, "em_get", fake_get)

        df = akf.fetch_industry_data(endpoint="board_industry_hist_em",
                                     industry="白酒", period="日k")

        assert list(df.columns) == EXPECTED_COLUMNS
        assert df.iloc[0]["收盘"] == pytest.approx(1932.928)

    def test_both_fail_lists_reasons(self, monkeypatch, ths_map):
        monkeypatch.setattr(ths, "get_client",
                            lambda name: MagicMock(get=lambda *a, **k: None))
        monkeypatch.setattr(em_client, "em_get", lambda *a, **k: None)

        with pytest.raises(RuntimeError) as ei:
            akf.fetch_industry_data(endpoint="board_industry_hist_em",
                                    industry="白酒", period="日k")

        msg = str(ei.value)
        assert "同花顺" in msg and "东财" in msg


# ════════════════════════════════════════════════════════════════
# 回归守卫
# ════════════════════════════════════════════════════════════════

class TestNoAkshare:
    def test_does_not_call_akshare_board_hist(self, monkeypatch, ths_client, ths_map):
        """不得再调用 akshare 的 stock_board_industry_hist_em（打故障的 push2his）。"""
        ths_client["by_span"]["last"] = fake_resp(board_js())

        fake_ak = MagicMock()
        fake_ak.stock_board_industry_hist_em = MagicMock(
            side_effect=AssertionError("不应再调用 akshare stock_board_industry_hist_em"))
        monkeypatch.setattr(akf, "_ak", lambda: fake_ak)

        akf.fetch_industry_data(endpoint="board_industry_hist_em",
                                industry="白酒", period="日k")

        fake_ak.stock_board_industry_hist_em.assert_not_called()
