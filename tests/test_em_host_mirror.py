"""东财主机镜像降级测试（v3.3.21）。

背景：东财的节点故障是**按主机（甚至按路由）**发生的。实测 2026-09-20：
    push2.eastmoney.com/api/qt/clist/get       → "Connection closed abruptly"
                                                 （连命令行 curl 都是 HTTP 000）
    push2.eastmoney.com/api/qt/stock/fflow/... → 200 rc=0（同主机不同路由，正常）
    push2delay.eastmoney.com（全部路由）        → 200 rc=0
主源一挂，依赖它的数据源全废，而镜像明明可用 —— 故在 em_get 统一入口加主机镜像。

本文件验证切换**边界**：什么该切、什么不该切、静默期是否生效。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from astock_signals import anti_ban_client as abc
from astock_signals import antiban_http as ah
from astock_signals.anti_ban_client import _em_last_call

PUSH2 = "https://push2.eastmoney.com/api/qt/clist/get"
PUSH2_DELAY = "https://push2delay.eastmoney.com/api/qt/clist/get"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


@pytest.fixture(autouse=True)
def clean_state():
    """每个用例前后清理镜像静默期与节流状态，并把间隔调 0 让测试快。"""
    original_interval = abc._EM_MIN_INTERVAL
    original_enabled = abc._EM_MIRROR_ENABLED
    abc.reset_host_mirrors()
    _em_last_call[0] = 0.0
    abc.set_min_interval(0.0)
    yield
    abc._EM_MIRROR_ENABLED = original_enabled
    abc.set_min_interval(original_interval)
    abc.reset_host_mirrors()
    _em_last_call[0] = 0.0


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """去掉退避 sleep，否则每个用例要等 1s+2s。"""
    monkeypatch.setattr(ah.time, "sleep", lambda *_: None)


def resp(status: int = 200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {"rc": 0}
    r.content = b'{"rc":0}'
    return r


def routed_session(rules: dict):
    """按 URL 片段分派的假 session。rules 的值可以是响应对象或异常实例。"""
    session = MagicMock()

    def fake_get(url, **kwargs):
        for frag, outcome in rules.items():
            if frag in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"未预期的 URL: {url}")

    session.get.side_effect = fake_get
    return session


def urls_called(session) -> list[str]:
    return [c.args[0] for c in session.get.call_args_list]


# ════════════════════════════════════════════════════════════════
# 该切的情况
# ════════════════════════════════════════════════════════════════

class TestMirrorTriggered:
    def test_connection_failure_triggers_mirror(self, monkeypatch):
        """主源连接层失败（本次真实故障）→ 切镜像并返回镜像响应。"""
        good = resp(200, {"rc": 0, "data": {"diff": [{"f14": "白酒"}]}})
        session = routed_session({
            "push2delay.eastmoney.com": good,
            "push2.eastmoney.com": ConnectionError("curl: (56) Connection closed abruptly"),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        result = abc.em_get(PUSH2)

        assert result is good
        assert any("push2delay" in u for u in urls_called(session))

    def test_server_error_triggers_mirror(self, monkeypatch):
        """主源 5xx（节点级故障）→ 切镜像。"""
        session = routed_session({
            "push2delay.eastmoney.com": resp(200, {"rc": 0}),
            "push2.eastmoney.com": resp(503),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        result = abc.em_get(PUSH2)

        assert result is not None
        assert result.status_code == 200
        assert any("push2delay" in u for u in urls_called(session))

    def test_em_push2_gets_data_through_mirror(self, monkeypatch):
        """端到端：em_push2 在主源故障时仍能经镜像拿到数据（而不是返回 {}）。"""
        payload = {"rc": 0, "data": {"diff": [{"f14": "银行", "f3": 1.2}]}}
        session = routed_session({
            "push2delay.eastmoney.com": resp(200, payload),
            "push2.eastmoney.com": ConnectionError("boom"),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        out = abc.em_push2({"pn": "1", "pz": "100"})

        assert out == payload


# ════════════════════════════════════════════════════════════════
# 不该切的情况
# ════════════════════════════════════════════════════════════════

class TestMirrorNotTriggered:
    def test_rc_102_throttled_does_not_trigger_mirror(self, monkeypatch):
        """rc=102 是按 IP 限流，换主机没用（实测 slist 在两台主机上都 102）。"""
        throttled = resp(200, {"rc": 102})
        session = MagicMock()
        session.get.return_value = throttled
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        result = abc.em_get(PUSH2)

        assert result is throttled
        assert all("push2delay" not in u for u in urls_called(session))

    def test_http_403_does_not_trigger_mirror(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(403)
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)

        assert all("push2delay" not in u for u in urls_called(session))

    def test_http_429_does_not_trigger_mirror(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(429)
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)

        assert all("push2delay" not in u for u in urls_called(session))

    def test_rc_100_not_found_does_not_trigger_mirror(self, monkeypatch):
        """rc=100 是标的不存在，属于正常业务结果。"""
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 100})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)

        assert all("push2delay" not in u for u in urls_called(session))

    def test_normal_response_does_not_trigger_mirror(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 0})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)

        assert session.get.call_count == 1
        assert "push2delay" not in urls_called(session)[0]

    def test_host_without_mirror_is_untouched(self, monkeypatch):
        """datacenter-web 没有镜像 → 只打一次，不产生额外请求。"""
        session = MagicMock()
        session.get.return_value = resp(200, {"result": {"data": []}})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(DATACENTER)

        assert session.get.call_count == 1
        assert urls_called(session) == [DATACENTER]


# ════════════════════════════════════════════════════════════════
# 静默期
# ════════════════════════════════════════════════════════════════

class TestMirrorCooldown:
    def _primary_dead_mirror_alive(self, monkeypatch):
        session = routed_session({
            "push2delay.eastmoney.com": resp(200, {"rc": 0}),
            "push2.eastmoney.com": ConnectionError("closed"),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)
        return session

    def test_cooldown_skips_primary_after_mirror_success(self, monkeypatch):
        """镜像成功后进入静默期：后续调用直接走镜像，不再白撞死主源
        （否则每次都要白等 max_retries 次退避 ≈ 3s）。"""
        session = self._primary_dead_mirror_alive(monkeypatch)

        abc.em_get(PUSH2)                      # 首次：主源失败 → 镜像成功 → 设静默
        assert abc.get_mirror_status(), "镜像成功后应记录静默期"

        session.get.reset_mock()
        abc.em_get(PUSH2)                      # 二次：应直接走镜像

        called = urls_called(session)
        assert called, "应发生请求"
        assert all("push2delay" in u for u in called), (
            f"静默期内不应再请求主源，实际: {called}"
        )

    def test_no_mirror_cooldown_when_mirror_also_fails(self, monkeypatch):
        """两边都挂时**不设镜像静默期**（否则主源恢复后仍会被镜像路径跳过）。

        v3.3.23 起会另设「死主机冷却」（见 TestDeadHostBreaker）——
        那是另一套状态，语义不同：它有短超时（默认 60s）且按 (主机,路径) 计。
        """
        session = routed_session({
            "push2delay.eastmoney.com": ConnectionError("mirror down"),
            "push2.eastmoney.com": ConnectionError("primary down"),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2) is None
        assert abc.get_mirror_status() == {}, "不应设置镜像静默期"

        # 清掉死主机冷却后应重新探测主源
        abc.reset_dead_hosts()
        session.get.reset_mock()
        abc.em_get(PUSH2)
        assert any("push2.eastmoney.com" in u for u in urls_called(session))

    def test_reset_host_mirrors_clears_cooldown(self, monkeypatch):
        session = self._primary_dead_mirror_alive(monkeypatch)
        abc.em_get(PUSH2)
        assert abc.get_mirror_status()

        abc.reset_host_mirrors()

        assert abc.get_mirror_status() == {}
        session.get.reset_mock()
        abc.em_get(PUSH2)
        assert any("push2.eastmoney.com" in u for u in urls_called(session)), (
            "重置后应重新探测主源"
        )

    def test_get_mirror_status_reports_remaining_seconds(self, monkeypatch):
        session = self._primary_dead_mirror_alive(monkeypatch)
        abc.em_get(PUSH2)

        status = abc.get_mirror_status()

        assert "push2.eastmoney.com" in status
        remaining = status["push2.eastmoney.com"]
        assert 0 < remaining <= abc._EM_MIRROR_COOLDOWN


# ════════════════════════════════════════════════════════════════
# 开关与 URL 处理
# ════════════════════════════════════════════════════════════════

class TestEmPostSharesOrchestration:
    """`em_post`（v3.3.24 新增）必须与 `em_get` 共用节流/重试/镜像/熔断。

    否则 POST 类接口（emappdata 人气榜）会绕过防封层，等于开了个后门。
    """

    def test_em_post_uses_session_post(self, monkeypatch):
        session = MagicMock()
        session.post.return_value = resp(200, {"data": []})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        out = abc.em_post("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                          json={"appId": "appId01"}, timeout=12)

        assert out is not None
        session.post.assert_called()
        _, kwargs = session.post.call_args
        assert kwargs["json"] == {"appId": "appId01"}
        assert kwargs["timeout"] == 12

    def test_em_post_participates_in_mirror(self, monkeypatch):
        """push2 的 POST 失败 → 应切到镜像主机（而非直接放弃）。"""
        good = resp(200, {"rc": 0})
        session = MagicMock()

        def fake_post(url, **kwargs):
            if "push2delay" in url:
                return good
            raise ConnectionError("push2 down")

        session.post.side_effect = fake_post
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        out = abc.em_post(PUSH2, json={"k": "v"})

        assert out is good
        assert abc.get_mirror_status(), "应进入镜像静默期"

    def test_em_post_participates_in_breaker(self, monkeypatch):
        """无镜像可切的 POST 主机失败 → 应触发熔断，下次短路。"""
        session = MagicMock()
        session.post.side_effect = ConnectionError("down")
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        assert abc.em_post(url, json={}) is None
        assert abc.get_dead_host_status(), "应熔断该 (主机,路径)"

        session.post.reset_mock()
        assert abc.em_post(url, json={}) is None
        assert session.post.call_count == 0, "冷却期内不应发起请求"

    def test_em_post_403_does_not_trip_breaker(self, monkeypatch):
        session = MagicMock()
        session.post.return_value = resp(403)
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_post("https://emappdata.eastmoney.com/stockrank/getAllCurrentList", json={})

        assert abc.get_dead_host_status() == {}


class TestMirrorConfig:
    def test_mirror_can_be_disabled(self, monkeypatch):
        """EM_HOST_MIRROR=0 → 完全关闭镜像（保留原有单主机行为）。"""
        abc._EM_MIRROR_ENABLED = False
        session = MagicMock()
        session.get.side_effect = ConnectionError("closed")
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2) is None
        assert all("push2delay" not in u for u in urls_called(session))

    def test_find_mirror_returns_none_for_mirror_url(self):
        """已经是镜像的 URL 不应被再次替换（防 push2delaydelay 之类）。"""
        assert abc._find_mirror(PUSH2_DELAY) is None

    def test_find_mirror_replaces_host_once(self):
        primary, mirror_url = abc._find_mirror(PUSH2)
        assert primary == "push2.eastmoney.com"
        assert mirror_url == PUSH2_DELAY

    def test_should_try_mirror_semantics(self):
        assert abc._should_try_mirror(None) is True           # 连接失败
        assert abc._should_try_mirror(resp(503)) is True      # 服务端故障
        assert abc._should_try_mirror(resp(403)) is False     # 风控（按 IP）
        assert abc._should_try_mirror(resp(200, {"rc": 102})) is False
        assert abc._should_try_mirror(resp(200, {"rc": 0})) is False

    def test_host_matching_is_not_substring_based(self):
        """按主机名精确匹配：路径里恰好含 push2 主机名时不得误判。"""
        tricky = "https://datacenter-web.eastmoney.com/api/x?u=push2.eastmoney.com"
        assert abc._find_mirror(tricky) is None


# ════════════════════════════════════════════════════════════════
# 镜像范围：只放「载荷已验证等价」的主机
# ════════════════════════════════════════════════════════════════

PUSH2HIS_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001"


class TestPush2FamilyCoverage:
    def test_push2his_is_deliberately_not_mirrored(self):
        """push2his **刻意不配镜像**。

        曾一度把 push2his 映到 push2delay，但实测载荷不等价：
          - push2his 的日K路由（klt=101）无可用镜像：push2delay 返回
            rc=0 但 **klines 为空**（它只服务 klt=1 分时）
          - 日线资金流：push2his 返回 **5 条**，push2delay 只返回 **1 条**
            → 映过去会把 5 条**悄悄变成 1 条**（静默降级数据，比失败更糟）
        """
        assert abc._find_mirror(PUSH2HIS_KLINE) is None
        assert "push2his.eastmoney.com" not in abc._EM_HOST_MIRRORS

    def test_push2his_failure_does_not_switch_host(self, monkeypatch):
        """push2his 失败时应保持失败，不得切到载荷不等价的镜像。"""
        session = MagicMock()
        session.get.side_effect = ConnectionError("Connection closed abruptly")
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2HIS_KLINE) is None
        assert all("push2delay" not in u for u in urls_called(session))

    def test_push2ex_is_deliberately_not_mirrored(self):
        """push2delay **不服务** push2ex 的 getTopicZTPool（实测 404 空体）。

        若把 push2ex 也镜像，会在其故障时切到 404，并可能把**健康的主源**
        误判为不可用。故 push2ex 刻意不配镜像。
        """
        assert abc._find_mirror(
            "https://push2ex.eastmoney.com/getTopicZTPool?dpt=wz.ztzt"
        ) is None

    def test_numbered_push2_subdomain_not_mirrored(self):
        """带数字前缀的子域（akshare 用的 70.push2 / 17.push2）刻意不配。

        项目代码没有任何一处请求这些子域（只有 akshare 自己用，而它绕过 em_get），
        且未逐路径验证载荷等价 —— 留未经验证的映射属同一类静默降级风险。
        """
        for host in ("70.push2.eastmoney.com", "17.push2.eastmoney.com"):
            assert abc._find_mirror(f"https://{host}/api/qt/clist/get") is None

    def test_push2delay_itself_has_no_mirror(self):
        """防止镜像再被镜像（push2delay → ?）。"""
        assert abc._find_mirror(PUSH2_DELAY) is None

    def test_only_verified_host_is_mapped(self):
        """镜像表必须保持最小：只放载荷验证过等价的主机。"""
        assert abc._EM_HOST_MIRRORS == {"push2.eastmoney.com": abc._EM_PUSH2_MIRROR}


class TestMirrorPathNotServed:
    """镜像不服务某路径时的行为（以 push2ex 为例做端到端验证）。"""

    def test_404_from_mirror_does_not_set_cooldown(self, monkeypatch):
        """镜像返回 404（不服务该路径）→ 不设静默期，否则会跳过健康主源。"""
        primary_resp = resp(503)          # 主源 5xx → 触发切镜像
        mirror_404 = resp(404)
        session = routed_session({
            "push2delay.eastmoney.com": mirror_404,
            "push2.eastmoney.com": primary_resp,
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        result = abc.em_get(PUSH2)

        # 原样返回主源结果，保持「镜像引入前」的行为
        assert result is primary_resp
        assert abc.get_mirror_status() == {}, "镜像不服务该路径时不得进入静默期"

    def test_primary_reprobed_after_dead_host_reset(self, monkeypatch):
        """镜像不服务该路径后，主源仍可在清掉熔断冷却后被重新探测。"""
        session = routed_session({
            "push2delay.eastmoney.com": resp(404),
            "push2.eastmoney.com": resp(503),
        })
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)
        abc.reset_dead_hosts()
        session.get.reset_mock()
        abc.em_get(PUSH2)

        assert any("push2.eastmoney.com" in u for u in urls_called(session))

    def test_mirror_response_usable_semantics(self):
        assert abc._mirror_response_usable(None) is False
        assert abc._mirror_response_usable(resp(200, {"rc": 0})) is True
        assert abc._mirror_response_usable(resp(200, {"rc": 102})) is True   # 镜像可达
        assert abc._mirror_response_usable(resp(200, {"rc": 100})) is True
        assert abc._mirror_response_usable(resp(404)) is False               # 不服务该路径
        assert abc._mirror_response_usable(resp(503)) is False
