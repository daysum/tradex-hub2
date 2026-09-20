"""死主机冷却（失败熔断）测试 —— v3.3.23。

问题：某个 (主机, 路径) 挂掉后，每次调用都要先白跑满 max_retries 次退避
才轮到备源。实测 `push2his` 日K路由全挂时，`index_daily_amount` 每次
都要先白等 3 次退避（1s+2s）＋ 节流 ≈ **6.5 秒**，而真正的备源只需 0.25s。

设计要点（本文件逐条验证）：
  1. 按 **(主机, 路径)** 熔断，而非仅主机 —— 实测 push2his 是
     「`kline/get` 挂了、`fflow/daykline/get` 还活着」，
     按主机熔断会误伤同主机上健康的路径
  2. 只在**主机级失败**（连接失败 / 5xx）时熔断；
     403 / 429 / rc=102 是按 IP 风控，主机本身没问题 → 不熔断
  3. 有镜像可切时优先走镜像，不额外熔断主源
  4. 冷却期内**不发起任何请求**（这才是省时间的意义）
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from astock_signals import anti_ban_client as abc
from astock_signals import antiban_http as ah
from astock_signals.anti_ban_client import _em_last_call

PUSH2 = "https://push2.eastmoney.com/api/qt/clist/get"
PUSH2_OTHER = "https://push2.eastmoney.com/api/qt/ulist.np/get"
PUSH2HIS_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PUSH2HIS_FFLOW = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"


@pytest.fixture(autouse=True)
def clean_state():
    original_interval = abc._EM_MIN_INTERVAL
    abc.reset_host_health()
    _em_last_call[0] = 0.0
    abc.set_min_interval(0.0)
    yield
    abc.set_min_interval(original_interval)
    abc.reset_host_health()
    _em_last_call[0] = 0.0


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(ah.time, "sleep", lambda *_: None)


def resp(status: int = 200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {"rc": 0}
    r.content = b'{"rc":0}'
    return r


def failing_session(exc=None):
    session = MagicMock()
    session.get.side_effect = exc or ConnectionError("Connection closed abruptly")
    return session


def urls_called(session) -> list[str]:
    return [c.args[0] for c in session.get.call_args_list]


# ════════════════════════════════════════════════════════════════
# 熔断触发与短路
# ════════════════════════════════════════════════════════════════

class TestBreakerTrip:
    def test_connection_failure_marks_host_path_dead(self, monkeypatch):
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2HIS_KLINE) is None

        status = abc.get_dead_host_status()
        assert any("push2his.eastmoney.com/api/qt/stock/kline/get" in k
                   for k in status), f"应熔断该 (主机,路径)，实际 {status}"

    def test_second_call_short_circuits_without_any_request(self, monkeypatch):
        """冷却期内必须**一个请求都不发** —— 这才是省下 6.5s 的意义。"""
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)          # 第一次：真实失败，触发熔断
        session.get.reset_mock()

        assert abc.em_get(PUSH2HIS_KLINE) is None
        assert session.get.call_count == 0, "冷却期内不应发起任何请求"

    def test_5xx_also_trips_breaker(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(503)
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        assert abc.get_dead_host_status()

    def test_success_clears_dead_mark(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 0})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        # 手动种一个熔断标记，成功请求应把它清掉
        abc._mark_host_dead("push2his.eastmoney.com", "/api/qt/stock/kline/get")
        assert abc.get_dead_host_status()
        abc.reset_dead_hosts()

        abc.em_get(PUSH2HIS_KLINE)
        assert abc.get_dead_host_status() == {}


# ════════════════════════════════════════════════════════════════
# 按 (主机, 路径) 而非按主机 —— 最关键的一条
# ════════════════════════════════════════════════════════════════

class TestBreakerScopeIsHostPlusPath:
    def test_sibling_path_on_same_host_is_not_affected(self, monkeypatch):
        """真实场景：push2his 的 `kline/get` 挂了，但 `fflow/daykline` 活着。

        若按**主机**熔断，健康路径会被一起跳过 —— 故障面反而扩大。
        """
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            # 注意用完整路径匹配：`fflow/daykline/get` 里也含 "kline/get" 子串，
            # 用子串判断会把健康路径一起弄挂（本用例最初就踩了这个坑）
            if "/api/qt/stock/kline/get" in url:
                raise ConnectionError("kline route down")
            return resp(200, {"rc": 0, "data": {"klines": ["ok"]}})

        session = MagicMock()
        session.get.side_effect = fake_get
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2HIS_KLINE) is None       # 日K挂 → 熔断该路径
        assert abc.get_dead_host_status()

        # 同主机另一条路径仍应正常工作
        got = abc.em_get(PUSH2HIS_FFLOW)
        assert got is not None, "同主机上健康的路径不应被熔断误伤"
        assert any("fflow" in u for u in calls)

    def test_different_path_on_same_host_is_probed(self, monkeypatch):
        """同主机不同路径各自独立计数。"""
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)                 # 熔断 /api/qt/clist/get
        session.get.reset_mock()

        abc.em_get(PUSH2_OTHER)           # 另一条路径仍应真实发起请求
        assert session.get.call_count > 0
        assert any("ulist.np" in u for u in urls_called(session))


# ════════════════════════════════════════════════════════════════
# 不该熔断的情况
# ════════════════════════════════════════════════════════════════

class TestBreakerNotTripped:
    def test_rc_102_does_not_trip(self, monkeypatch):
        """rc=102 是按 IP 限流，主机本身没问题 → 不熔断。"""
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 102})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        assert abc.get_dead_host_status() == {}

    def test_http_403_does_not_trip(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(403)
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        assert abc.get_dead_host_status() == {}

    def test_rc_100_does_not_trip(self, monkeypatch):
        """rc=100 是标的不存在，属正常业务结果。"""
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 100})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        assert abc.get_dead_host_status() == {}

    def test_normal_response_does_not_trip(self, monkeypatch):
        session = MagicMock()
        session.get.return_value = resp(200, {"rc": 0})
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        assert abc.get_dead_host_status() == {}

    def test_mirror_served_host_is_not_breaker_tripped(self, monkeypatch):
        """有镜像可切且镜像可用 → 走镜像静默期，不额外熔断主源。"""
        session = MagicMock()

        def fake_get(url, **kwargs):
            if "push2delay" in url:
                return resp(200, {"rc": 0, "data": {"diff": []}})
            raise ConnectionError("push2 down")

        session.get.side_effect = fake_get
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        assert abc.em_get(PUSH2) is not None
        assert abc.get_mirror_status(), "应进入镜像静默期"
        assert abc.get_dead_host_status() == {}, "走镜像时不应额外熔断主源"


# ════════════════════════════════════════════════════════════════
# 冷却期管理与开关
# ════════════════════════════════════════════════════════════════

class TestBreakerCooldownManagement:
    def test_reset_dead_hosts_clears(self, monkeypatch):
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)
        assert abc.get_dead_host_status()

        abc.reset_dead_hosts()

        assert abc.get_dead_host_status() == {}
        session.get.reset_mock()
        abc.em_get(PUSH2HIS_KLINE)
        assert session.get.call_count > 0, "清空冷却后应重新真实请求"

    def test_reset_host_health_clears_both(self, monkeypatch):
        """运维用的一次性复位：镜像静默期 + 死主机冷却都要清。"""
        session = MagicMock()

        def fake_get(url, **kwargs):
            if "push2delay" in url:
                return resp(200, {"rc": 0})
            raise ConnectionError("down")

        session.get.side_effect = fake_get
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2)                 # 触发镜像静默期
        abc.em_get(PUSH2HIS_KLINE)        # 触发死主机冷却（无镜像）
        assert abc.get_mirror_status()
        assert abc.get_dead_host_status()

        abc.reset_host_health()

        assert abc.get_mirror_status() == {}
        assert abc.get_dead_host_status() == {}

    def test_expired_cooldown_reprobes(self, monkeypatch):
        """冷却到期后应重新真实请求（不能永久熔断）。"""
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)
        # 把冷却截止时间拨到过去，模拟到期
        abc._dead_until[("push2his.eastmoney.com", "/api/qt/stock/kline/get")] = time.time() - 1

        session.get.reset_mock()
        abc.em_get(PUSH2HIS_KLINE)

        assert session.get.call_count > 0, "冷却到期后应重新探测"

    def test_cooldown_can_be_disabled(self, monkeypatch):
        """EM_DEAD_HOST_COOLDOWN=0 → 关闭熔断，保持每次真实重试。"""
        monkeypatch.setattr(abc, "_EM_DEAD_HOST_COOLDOWN", 0.0)
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)
        assert abc.get_dead_host_status() == {}

        session.get.reset_mock()
        abc.em_get(PUSH2HIS_KLINE)
        assert session.get.call_count > 0, "关闭熔断后每次都应真实请求"

    def test_status_reports_remaining_seconds(self, monkeypatch):
        session = failing_session()
        monkeypatch.setattr(abc, "_ensure_session", lambda: session)

        abc.em_get(PUSH2HIS_KLINE)

        status = abc.get_dead_host_status()
        assert len(status) == 1
        remaining = next(iter(status.values()))
        assert 0 < remaining <= abc._EM_DEAD_HOST_COOLDOWN

    def test_host_and_path_helper(self):
        assert abc._host_and_path(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001"
        ) == ("push2his.eastmoney.com", "/api/qt/stock/kline/get")
        assert abc._host_and_path("https://push2.eastmoney.com") == (
            "push2.eastmoney.com", "/")
        assert abc._host_and_path("not-a-url") == (None, "")
