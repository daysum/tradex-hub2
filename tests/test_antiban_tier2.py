"""第二梯队数据源防限流改造测试（v3.3.20）。

背景：v3.3.19 只加固了东财与腾讯。同花顺（10jqka / hexin）/ 财联社 / 巨潮 /
新浪 / 问财当时仍是裸 requests 或裸 urllib —— 其中
  - `ths_fetchers` 模块级 `requests.Session()`，零指纹零节流；
  - `akshare_fetchers.fetch_profit_forecast` 用的是**残缺的伪造 Mac UA**；
  - 巨潮两处 POST **完全没有指纹伪装**。
本文件覆盖三件事：
  1. **ClientGroup 抽象**：按源分组的节流器/会话/指纹/重试行为。
  2. **收敛行为**：每个第二梯队模块确实经统一入口取数。
  3. **防回退**：静态扫描全部数据源域名，确保没有模块绕过统一层。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from astock_signals import antiban_http as ah

SRC = Path(__file__).resolve().parents[1] / "tradex" / "src"

# 数据源域名 -> 允许直接持有裸传输调用的模块（统一层自身 + 该源的专属传输层）
DOMAIN_ALLOWLIST: dict[str, set[str]] = {
    "eastmoney.com": {"antiban_http.py", "anti_ban_client.py", "em_client.py"},
    "gtimg.cn": {"antiban_http.py", "http_fetchers.py"},
    "10jqka.com.cn": {"antiban_http.py", "ths_fetchers.py"},
    "hexin.cn": {"antiban_http.py", "northbound.py"},
    "cls.cn": {"antiban_http.py", "news_fetchers.py"},
    "cninfo.com.cn": {"antiban_http.py", "news_fetchers.py"},
    "sina.com.cn": {"antiban_http.py", "news_fetchers.py"},
    "iwencai.com": {"antiban_http.py", "wencai_fetchers.py"},
    # 腾讯指数日K（proxy.finance.qq.com），归 http_fetchers 的腾讯传输层管
    "finance.qq.com": {"antiban_http.py", "http_fetchers.py"},
}

# 传输层模块：import 这些名字的绑定名才算「裸传输对象」
_TRANSPORT_MODULES = ("requests", "urllib", "curl_cffi", "http.client", "httpx", "aiohttp")
_HTTP_METHODS = {"get", "post", "put", "delete", "head", "patch", "request", "urlopen", "open", "Session"}


# ════════════════════════════════════════════════════════════════
# 测试替身
# ════════════════════════════════════════════════════════════════

class FakeRegistry:
    """替代 `antiban_http.get_client`：记录被请求的客户端名与调用参数。"""

    def __init__(self, resp=None, *, post_resp=None):
        self.resp = resp
        self.post_resp = post_resp if post_resp is not None else resp
        self.requested: list[str] = []
        self.calls: list[tuple] = []

    def __call__(self, name: str) -> "FakeClient":
        self.requested.append(name)
        return FakeClient(self, name)

    def only_client(self) -> str:
        """断言只用了某一个源，返回该源名。"""
        assert len(set(self.requested)) == 1, f"应只用一个客户端，实际 {self.requested}"
        return self.requested[0]

    def methods(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeClient:
    def __init__(self, reg: FakeRegistry, name: str) -> None:
        self._reg = reg
        self.name = name

    def get(self, url, **kw):
        self._reg.calls.append(("get", self.name, url, kw))
        return self._reg.resp

    def post(self, url, **kw):
        self._reg.calls.append(("post", self.name, url, kw))
        return self._reg.post_resp


def json_resp(payload) -> MagicMock:
    """构造一个可被 `resp.json()` 正常解析的响应替身。"""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r.text = json.dumps(payload, ensure_ascii=False)
    return r


def patch_clients(monkeypatch, registry, *modules):
    """同时替换统一层的 get_client 与各模块已绑定的本地引用。

    项目里两种导入风格并存：模块级（news_fetchers / ths_fetchers）与
    函数级（wencai_fetchers / akshare_fetchers）。函数级导入在调用时才
    从 antiban_http 取属性，故替换 antiban_http.get_client 即可命中；
    模块级导入则需替换该模块自己的名字。
    """
    monkeypatch.setattr(ah, "get_client", registry)
    for m in modules:
        if hasattr(m, "get_client"):
            monkeypatch.setattr(m, "get_client", registry)


# ════════════════════════════════════════════════════════════════
# 1. 具名客户端注册表
# ════════════════════════════════════════════════════════════════

class TestClientRegistry:
    def test_registered_clients_cover_all_tier2_sources(self):
        assert ah.registered_clients() == ["cls", "cninfo", "sina", "ths", "wencai"]

    def test_get_client_is_process_singleton(self):
        """同一名字必须返回同一实例 —— 否则每个调用点各持一把节流器，
        实际请求速率会翻倍（v3.3.19 东财踩过的坑）。"""
        assert ah.get_client("ths") is ah.get_client("ths")

    def test_different_sources_get_different_clients(self):
        assert ah.get_client("ths") is not ah.get_client("cls")

    def test_unknown_client_name_raises(self):
        with pytest.raises(KeyError, match="未注册"):
            ah.get_client("no-such-source")

    def test_each_source_has_independent_throttle(self):
        """各源节流器必须相互独立，否则低风险源会被高风险源的间隔拖慢。"""
        ths, cls = ah.get_client("ths"), ah.get_client("cls")
        assert ths.throttle is not cls.throttle
        assert ths.throttle._last_call is not cls.throttle._last_call

    def test_tier2_intervals_are_looser_than_eastmoney(self):
        """第二梯队风控更松，间隔必须显著小于东财的 1.0s。"""
        for name in ("ths", "cls", "cninfo", "sina"):
            assert ah.get_client(name).throttle.min_interval < ah.EM_MIN_INTERVAL

    def test_reset_clients_closes_sessions(self):
        client = ah.get_client("ths")
        fake = MagicMock()
        client._session = fake
        try:
            ah.reset_clients()
            fake.close.assert_called_once()
            # 注册表已清空，重新取到的是新实例
            assert ah.get_client("ths") is not client
        finally:
            ah.reset_clients()

    def test_cninfo_disables_fingerprint_spoofing(self):
        """巨潮 WAF 封禁常见浏览器指纹（chrome120/124 实测 403），
        该源必须关闭指纹伪装，但节流与重试仍要生效。"""
        cninfo = ah.get_client("cninfo")
        assert cninfo._impersonate == ah.NO_IMPERSONATE
        assert cninfo._pick_fingerprint() is None
        assert cninfo.throttle.min_interval > 0
        assert cninfo.max_retries >= 1

    def test_other_sources_keep_fingerprint_spoofing(self):
        """其余第二梯队源指纹伪装照常（实测均返回 200）。"""
        for name in ("ths", "cls", "sina", "wencai"):
            assert ah.get_client(name)._impersonate != ah.NO_IMPERSONATE


class TestNoImpersonateSession:
    def test_make_session_without_fingerprint(self):
        s = ah.make_session(ah.NO_IMPERSONATE, referer="http://www.cninfo.com.cn/")
        try:
            # 仍有常规浏览器 UA（实测 cninfo 接受该组合）
            assert s.headers["User-Agent"] == ah.ua_for("chrome120")
            assert s.headers["Referer"] == "http://www.cninfo.com.cn/"
            assert getattr(s, "trust_env", False) is False
        finally:
            s.close()

    def test_none_still_means_random_fingerprint(self):
        """None 语义不得被改变：仍是「从池中随机取」，而非「不伪装」。"""
        assert ah.resolve_fingerprint(None) in ah.FINGERPRINTS


# ════════════════════════════════════════════════════════════════
# 2. ClientGroup 行为
# ════════════════════════════════════════════════════════════════

class TestClientGroup:
    def test_session_is_lazy_and_cached(self, monkeypatch):
        built = []

        def fake_make_session(fp, referer=None, extra_headers=None):
            built.append(fp)
            return MagicMock()

        monkeypatch.setattr(ah, "make_session", fake_make_session)
        c = ah.ClientGroup("x", min_interval=0.0)
        assert built == []          # 未取用前不建会话
        s1 = c.session()
        assert built == ["chrome120"]
        assert c.session() is s1    # 复用，不重复建
        assert len(built) == 1

    def test_throttle_serializes_within_one_client(self, monkeypatch):
        """同一源连续两次请求必须间隔 >= min_interval。"""
        monkeypatch.setattr(ah.time, "sleep", lambda *_: None)
        c = ah.ClientGroup("x", min_interval=10.0, jitter_min=0.0, jitter_max=0.0)
        c.throttle.acquire()            # 首次：无等待
        waited = c.throttle.acquire()   # 第二次：应等满 10s
        assert waited > 9.0

    def test_fingerprint_rotates_on_throttle(self, monkeypatch):
        """被限流后应换到池里的下一个指纹（而不是继续用同一个硬打）。"""
        monkeypatch.setattr(ah, "make_session", lambda fp, **kw: MagicMock())
        c = ah.ClientGroup("x", min_interval=0.0, impersonate="chrome120")
        c.session()
        assert c._fp == "chrome120"
        c.rotate_session()
        assert c._fp != "chrome120"
        assert c._fp in ah.FINGERPRINTS

    def test_rotate_session_closes_old_session(self, monkeypatch):
        monkeypatch.setattr(ah, "make_session", lambda fp, **kw: MagicMock())
        c = ah.ClientGroup("x", min_interval=0.0)
        old = c.session()
        c.rotate_session()
        old.close.assert_called_once()
        assert c.session() is not old

    def test_request_returns_none_when_retries_exhausted(self, monkeypatch):
        """重试耗尽返回 None（与 em_get 语义一致），不把异常穿透到调用方。"""
        monkeypatch.setattr(ah.time, "sleep", lambda *_: None)
        c = ah.ClientGroup("x", min_interval=0.0, max_retries=2)
        session = MagicMock()
        session.get.side_effect = ConnectionError("boom")
        monkeypatch.setattr(c, "session", lambda: session)
        assert c.get("https://example.com/x") is None
        assert session.get.call_count == 2

    def test_post_passes_data_through(self, monkeypatch):
        """巨潮/问财都是 POST 带 body，data 必须原样透传。"""
        c = ah.ClientGroup("x", min_interval=0.0)
        session = MagicMock()
        session.post.return_value = json_resp({"ok": 1})
        monkeypatch.setattr(c, "session", lambda: session)
        c.post("https://example.com/x", data=b"payload", headers={"A": "b"})
        _, kwargs = session.post.call_args
        assert kwargs["data"] == b"payload"

    def test_throttled_response_triggers_session_rotation(self, monkeypatch):
        """命中限流（rc=102）应触发换指纹重建会话。"""
        monkeypatch.setattr(ah.time, "sleep", lambda *_: None)
        monkeypatch.setattr(ah, "make_session", lambda fp, **kw: MagicMock())
        c = ah.ClientGroup("x", min_interval=0.0, max_retries=2)
        first = MagicMock()
        first.status_code = 200
        first.json.return_value = {"rc": 102}
        first.get.return_value = first  # 让 session.get(...) 返回该响应本身
        c._session = first
        with patch.object(c, "rotate_session", wraps=c.rotate_session) as rot:
            c.get("https://example.com/x")
        assert rot.call_count >= 1


# ════════════════════════════════════════════════════════════════
# 3. json_of —— GBK 兜底
# ════════════════════════════════════════════════════════════════

class TestJsonOf:
    def test_prefers_declared_encoding(self):
        r = json_resp({"a": 1})
        assert ah.json_of(r) == {"a": 1}

    def test_falls_back_to_gbk_when_utf8_fails(self):
        """同花顺接口不声明 charset，内容实为 GBK —— 必须能兜住。"""
        payload = {"name": "贵州茅台"}
        r = MagicMock()
        r.json.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        r.content = json.dumps(payload, ensure_ascii=False).encode("gbk")
        assert ah.json_of(r, fallback_encoding="gbk") == payload

    def test_none_response_raises_value_error(self):
        with pytest.raises(ValueError):
            ah.json_of(None)

    def test_undecodable_body_raises_with_context(self):
        """两种编码都解不出来时，错误信息要带 status 与 body 片段，便于定位。"""
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = ValueError("not json")
        r.content = b"<html>WAF</html>"
        with pytest.raises(RuntimeError, match="不是合法 JSON"):
            ah.json_of(r, fallback_encoding="gbk")


class TestSilentThrottleDetection:
    """JSON 接口返回 200 + 空 body 是典型的「静默限流」，必须可重试。"""

    def _empty_resp(self):
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = ValueError("Expecting value: line 1 column 1")
        r.content = b""
        return r

    def test_empty_200_body_is_throttled(self):
        assert ah.classify_response(self._empty_resp()) == "throttled"

    def test_whitespace_only_body_is_throttled(self):
        r = self._empty_resp()
        r.content = b"   \n\t "
        assert ah.classify_response(r) == "throttled"

    def test_non_empty_non_json_is_still_ok(self):
        """JSONP / HTML 等有内容的非 JSON 响应仍按 ok 处理，不误重试。"""
        r = self._empty_resp()
        r.content = b'jQuery1({"a":1})'
        assert ah.classify_response(r) == "ok"

    def test_empty_body_is_retryable(self):
        assert ah.is_retryable("throttled")


# ════════════════════════════════════════════════════════════════
# 4. 收敛行为：每个模块确实经统一入口
# ════════════════════════════════════════════════════════════════

class TestThsConvergence:
    def test_ths_fetchers_get_raises_when_exhausted(self, monkeypatch):
        from tradex.data_sources import ths_fetchers

        reg = FakeRegistry(resp=None)
        patch_clients(monkeypatch, reg, ths_fetchers)
        with pytest.raises(RuntimeError, match="重试耗尽"):
            ths_fetchers._get("https://data.10jqka.com.cn/x")

    def test_ths_fetchers_hot_list_uses_ths_client(self, monkeypatch):
        from tradex.data_sources import ths_fetchers

        payload = {"data": {"stock_list": [{
            "order": 1, "code": "600519", "name": "贵州茅台", "rate": "100",
            "rise_and_fall": "1.2", "hot_rank_chg": "0",
            "tag": {"concept_tag": ["白酒"], "popularity_tag": "热"},
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, ths_fetchers)

        df = ths_fetchers.fetch_ths_hot_list()
        assert reg.only_client() == "ths"
        assert "10jqka.com.cn" in reg.calls[0][2]
        assert list(df.columns) == ["排名", "代码", "名称", "人气值", "涨幅%", "排名变化", "概念标签", "热度标签"]
        assert df.iloc[0]["名称"] == "贵州茅台"

    def test_ths_fetchers_limit_up_pool_uses_ths_client(self, monkeypatch):
        from tradex.data_sources import ths_fetchers

        payload = {"data": {"info": [{
            "code": "600519", "name": "贵州茅台", "latest": 1500.0,
            "change_rate": 10.0, "reason_type": "白酒", "limit_up_type": "首板",
            "limit_up_suc_rate": 0.8, "open_num": 1, "order_amount": 1e8,
            "high_days": "首板", "first_limit_up_time": 1700000000,
            "is_again_limit": 0,
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, ths_fetchers)

        df = ths_fetchers.fetch_ths_limit_up_pool("20260918")
        assert reg.only_client() == "ths"
        assert df.iloc[0]["代码"] == "600519"
        assert df.iloc[0]["涨停原因"] == "白酒"

    def test_ths_fetchers_has_no_module_level_raw_session(self):
        """回归守卫：模块级裸 Session 必须已被移除。"""
        from tradex.data_sources import ths_fetchers

        assert not hasattr(ths_fetchers, "_S"), "模块级裸 requests.Session() 应已移除"
        assert not hasattr(ths_fetchers, "_UA")


class TestHotMoneyConvergence:
    def test_get_hot_stocks_json_uses_ths_client(self, monkeypatch):
        from astock_signals import hot_money

        payload = {"errocode": 0, "data": [{
            "code": "600519", "name": "贵州茅台", "reason": "白酒+消费",
            "zhangfu": "10.0", "huanshou": "5.0",
            "chengjiaoe": "1e9", "ddejingliang": "1.2",
        }]}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, hot_money)

        out = hot_money.get_hot_stocks_json("2026-09-18")
        assert reg.only_client() == "ths"
        assert "10jqka.com.cn" in reg.calls[0][2]
        assert out["total_count"] == 1
        assert out["stocks"][0]["code"] == "600519"
        assert out["error"] is None

    def test_get_hot_stocks_markdown_uses_ths_client(self, monkeypatch):
        from astock_signals import hot_money

        payload = {"errocode": 0, "data": [{
            "code": "600519", "name": "贵州茅台", "reason": "白酒",
            "zhangfu": "10.0", "huanshou": "5.0",
            "chengjiaoe": "1e9", "ddejingliang": "1.2",
        }]}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, hot_money)

        text = hot_money.get_hot_stocks("2026-09-18")
        assert reg.only_client() == "ths"
        assert "600519 贵州茅台" in text

    def test_no_raw_requests_import(self):
        from astock_signals import hot_money

        assert not hasattr(hot_money, "_requests")


class TestNorthboundConvergence:
    def test_uses_ths_client(self, monkeypatch):
        from astock_signals import northbound

        payload = {"time": ["09:30", "15:00"], "hgt": ["1.0", "2.0"], "sgt": ["3.0", "4.0"]}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, northbound)
        # 隔离本地 CSV 缓存，避免测试写用户主目录
        monkeypatch.setattr(northbound, "_save_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(northbound, "_load_history", lambda *a, **k: [])

        out = northbound.get_northbound_flow_json("2026-09-18")
        assert reg.only_client() == "ths"
        assert "hexin.cn" in reg.calls[0][2]
        assert out["realtime"]["total"] == 6.0
        assert out["realtime"]["signal"] == "bullish_inflow"

    def test_host_header_preserved(self, monkeypatch):
        """hexin.cn 走 CDN，Host 头必须保留（UA 由客户端按指纹注入）。"""
        from astock_signals import northbound

        reg = FakeRegistry(resp=json_resp({"time": [], "hgt": [], "sgt": []}))
        patch_clients(monkeypatch, reg, northbound)
        monkeypatch.setattr(northbound, "_save_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(northbound, "_load_history", lambda *a, **k: [])

        northbound.get_northbound_flow_json("2026-09-18")
        headers = reg.calls[0][3]["headers"]
        assert headers["Host"] == "data.hexin.cn"
        assert "User-Agent" not in headers

    def test_no_raw_requests_import(self):
        from astock_signals import northbound

        assert not hasattr(northbound, "_requests")


class TestLimitUpBoardConvergence:
    def test_limit_up_insight_uses_ths_client(self, monkeypatch):
        from astock_signals import limit_up_board

        payload = {"code": 0, "data": {"list": [{
            "stock_code": "600519", "stock_name": "贵州茅台", "reason": "白酒",
            "seal_success_rate": "0.9", "board_type": "首板", "seal_amount": "1e8",
            "turnover_rate": "5", "volume_ratio": "1.2", "change_rate": "10",
            "industry": "白酒",
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, limit_up_board)

        out = limit_up_board.get_limit_up_insight("600519")
        assert reg.only_client() == "ths"
        assert "10jqka.com.cn" in reg.calls[0][2]
        assert out["count"] == 1
        assert out["source"] == "同花顺 limit_up_detail"

    def test_em_clist_still_uses_em_get(self, monkeypatch):
        """改造不得影响东财路径。"""
        from astock_signals import limit_up_board

        captured = {}

        def fake_em_get(url, **kw):
            captured["url"] = url
            r = MagicMock()
            r.json.return_value = {"data": {"diff": [], "total": 0}}
            return r

        monkeypatch.setattr(limit_up_board, "em_get", fake_em_get)
        limit_up_board._em_clist_get("f12,f14")
        assert "eastmoney.com" in captured["url"]

    def test_no_raw_requests_import(self):
        from astock_signals import limit_up_board

        assert not hasattr(limit_up_board, "_requests")


class TestNewsConvergence:
    def test_cls_telegraph_uses_cls_client(self, monkeypatch):
        from tradex.data_sources import news_fetchers

        payload = {"data": {"roll_data": [{
            "title": "标题", "content": "内容", "ctime": 1700000000,
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, news_fetchers)

        df = news_fetchers.fetch_cls_telegraph(5)
        assert reg.only_client() == "cls"
        assert "cls.cn" in reg.calls[0][2]
        assert df.iloc[0]["标题"] == "标题"

    def test_cninfo_uses_cninfo_client_for_both_posts(self, monkeypatch):
        """巨潮两条 POST（orgId 解析 + 公告查询）原先都无指纹，现均须经客户端。"""
        from tradex.data_sources import news_fetchers

        payload = {"announcements": [{
            "announcementTitle": "年度报告", "announcementTime": 1700000000000,
            "announcementTypeName": "年报", "adjunctUrl": "finalpage/x.pdf",
            "secCode": "600519", "secName": "贵州茅台",
        }]}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, news_fetchers)

        df = news_fetchers.fetch_cninfo_direct(symbol="600519")
        assert reg.only_client() == "cninfo"
        assert reg.methods() == ["post", "post"]
        assert all("cninfo.com.cn" in c[2] for c in reg.calls)
        assert df.iloc[0]["标题"] == "年度报告"

    def test_sina_uses_sina_client(self, monkeypatch):
        from tradex.data_sources import news_fetchers

        payload = {"result": {"data": [{
            "title": "新闻", "ctime": "1700000000", "source": "新浪", "url": "u",
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, news_fetchers)

        df = news_fetchers.fetch_sina_finance_news(5)
        assert reg.only_client() == "sina"
        assert "sina.com.cn" in reg.calls[0][2]
        assert df.iloc[0]["新闻标题"] == "新闻"

    def test_em_news_direct_still_uses_em_get(self, monkeypatch):
        """改造不得影响东财新闻路径。"""
        from tradex.data_sources import news_fetchers

        captured = {}

        def fake_em_get(url, **kw):
            captured["url"] = url
            r = MagicMock()
            r.text = 'jQuery1({"result": {"cmsArticleWebOld": []}})'
            return r

        monkeypatch.setattr(news_fetchers, "em_get", fake_em_get)
        news_fetchers.fetch_em_news_direct(symbol="600519")
        assert "eastmoney.com" in captured["url"]

    def test_no_bare_curl_cffi_calls_left(self):
        """cls/cninfo/sina 三处裸 curl_cffi 调用必须已移除。"""
        from tradex.data_sources import news_fetchers

        assert not hasattr(news_fetchers, "curl_requests")


class TestWencaiConvergence:
    def test_wencai_news_uses_wencai_client(self, monkeypatch):
        from tradex.data_sources import wencai_fetchers

        payload = {"data": {"items": [{
            "title": "研报", "publish_time": "2026-09-18", "source": "券商",
            "summary": "摘要", "url": "u",
        }]}}
        reg = FakeRegistry(resp=json_resp(payload))
        patch_clients(monkeypatch, reg, wencai_fetchers)
        monkeypatch.setattr(wencai_fetchers, "IWENCAI_API_KEY", "test-key")

        df = wencai_fetchers.fetch_wencai_news(keyword="茅台", channel="news")
        assert reg.only_client() == "wencai"
        assert reg.methods() == ["post"]
        assert df.iloc[0]["标题"] == "研报"

    def test_wencai_still_requires_api_key(self, monkeypatch):
        from tradex.data_sources import wencai_fetchers

        monkeypatch.setattr(wencai_fetchers, "IWENCAI_API_KEY", "")
        df = wencai_fetchers.fetch_wencai_news(keyword="茅台")
        assert df.empty


class TestAkshareProfitForecastConvergence:
    def test_profit_forecast_uses_ths_client(self, monkeypatch):
        """`fetch_profit_forecast`（registry priority=1）原先用残缺的伪造 Mac UA
        直连 10jqka，且零节流；现须经统一入口。"""
        from tradex.data_sources import akshare_fetchers as akf

        html = (
            "<html><table>"
            "<thead><tr><th>年度</th><th>预测机构数</th>"
            "<th>最小值</th><th>均值</th><th>最大值</th></tr></thead>"
            "<tbody><tr><th>2026</th><td>5</td><td>1.0</td><td>2.0</td>"
            "<td>3.0</td><td>2.5</td></tr></tbody>"
            "</table></html>"
        )
        quote_payload = '"1~贵州茅台~600519~1500.0~' + "~".join(["1"] * 50) + '"'

        resp = MagicMock()
        resp.status_code = 200
        resp.content = html.encode("gbk")

        quote_resp = MagicMock()
        quote_resp.status_code = 200
        quote_resp.content = quote_payload.encode("gbk")

        class _Reg:
            def __init__(self):
                self.requested = []
                self.calls = []

            def __call__(self, name):
                self.requested.append(name)
                reg = self

                class _C:
                    def get(self, url, **kw):
                        reg.calls.append((name, url, kw))
                        return quote_resp if "gtimg.cn" in url else resp

                return _C()

        reg = _Reg()
        patch_clients(monkeypatch, reg, akf)
        monkeypatch.setattr(
            "tradex.data_sources.http_fetchers._tencent_quote_vals",
            lambda code: quote_payload.split('"')[1].split("~"),
        )

        out = akf.fetch_profit_forecast(symbol="600519")
        assert "ths" in reg.requested
        assert any("10jqka.com.cn" in c[1] for c in reg.calls)
        assert out["symbol"] == "600519"
        assert len(out["forecasts"]) >= 1

    def test_no_fake_mac_ua_left(self):
        """残缺的伪造 Mac UA 必须彻底消失。"""
        src = (SRC / "tradex" / "data_sources" / "akshare_fetchers.py").read_text(encoding="utf-8")
        assert "Macintosh; Intel Mac OS X 10_15_7" not in src
        assert "import requests as _rq" not in src


# ════════════════════════════════════════════════════════════════
# 5. 防回退：全域名静态守卫
# ════════════════════════════════════════════════════════════════

def _iter_py():
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _transport_roots(tree: ast.AST) -> set[str]:
    """收集模块内所有指向 HTTP 传输层的绑定名。

    覆盖三种写法：`import requests as _requests`、`from curl_cffi import requests`、
    `import urllib.request as _ur`，以及模块级 `_S = requests.Session()` 这类裸会话变量。
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if any(a.name == m or a.name.startswith(m + ".") for m in _TRANSPORT_MODULES):
                    roots.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(
                node.module == m or node.module.startswith(m + ".") for m in _TRANSPORT_MODULES
            ):
                for a in node.names:
                    roots.add(a.asname or a.name)
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Attribute) and fn.attr == "Session":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        roots.add(t.id)
    return roots


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _raw_transport_calls(tree: ast.AST, roots: set[str]):
    """找出直接打在传输对象上的 HTTP 调用。

    刻意排除 `get_client("ths").get(...)` 这类「先取客户端再调用」的链式写法
    （接收者是 Call，不是 Name），否则会把统一入口本身误判为绕过。
    """
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in _HTTP_METHODS:
            continue
        receiver = fn.value
        if isinstance(receiver, ast.Call):
            continue
        root = _root_name(receiver)
        if root in roots:
            hits.append((fn.attr, node.lineno, root))
    return hits


def _has_session_ctor(tree: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Session"
        for n in ast.walk(tree)
    )


class TestNoBypassGuardAllSources:
    """任何数据源域名都不允许被统一层以外的模块裸请求。"""

    @pytest.mark.parametrize("domain", sorted(DOMAIN_ALLOWLIST))
    def test_no_raw_request_bypasses_unified_layer(self, domain):
        allowed = DOMAIN_ALLOWLIST[domain]
        offenders = []
        for path in _iter_py():
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            consts = [
                n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            if not any(domain in c for c in consts):
                continue
            calls = _raw_transport_calls(tree, _transport_roots(tree))
            if calls or _has_session_ctor(tree):
                detail = ", ".join(f"{a}(...)@L{ln} via {r}" for a, ln, r in calls)
                offenders.append(f"{path.name}: {detail or '自建 Session'}")
        assert not offenders, (
            f"以下位置绕过统一入口直接请求 {domain}（会使全局节流失效）：\n  "
            + "\n  ".join(offenders)
        )

    def test_no_urllib_left_in_data_sources(self):
        """data_sources 包内不应再有裸 urllib（版本监控器属工具层，不在其列）。"""
        offenders = []
        for path in (SRC / "tradex" / "data_sources").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "import urllib" in text:
                offenders.append(path.name)
        assert not offenders, f"以下文件仍在使用 urllib：{offenders}"
