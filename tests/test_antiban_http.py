"""Tests for astock_signals.antiban_http —— 统一防封 HTTP 层（全部离线）。

覆盖：
  - 指纹池与 UA 版本匹配（UA 与 impersonate 不一致会被服务端识别为伪造）
  - 节流器：最小间隔、抖动范围、并发下不退化
  - 东财 rc 语义：rc=102 限流（应重试+换指纹）vs rc=100 标的不存在（应立即返回）
  - 指数退避重试：网络异常 / 403 / 429 / 5xx 重试，正常响应不重试
  - 腾讯 GBK 文本获取
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from astock_signals import antiban_http as ab
from astock_signals.antiban_http import (
    FINGERPRINTS,
    UA_BY_FINGERPRINT,
    Throttle,
    backoff_seconds,
    classify_response,
    get_bytes,
    get_text,
    is_retryable,
    make_session,
    request_with_retry,
    resolve_fingerprint,
    ua_for,
)


# ════════════════════════════════════════════════════════════════
# 指纹池与 UA 匹配
# ════════════════════════════════════════════════════════════════

class TestFingerprints:
    def test_pool_not_empty(self):
        assert len(FINGERPRINTS) >= 4

    def test_every_fingerprint_has_matching_ua(self):
        """每个指纹都必须有 UA，且 UA 里的 Chrome 版本号与指纹一致。

        UA 与 impersonate 版本不一致会被服务端识别为伪造并拒绝——
        这是 astock_scraper 项目踩过的坑。
        """
        for fp in FINGERPRINTS:
            assert fp in UA_BY_FINGERPRINT, f"{fp} 缺少 UA 映射"
            ua = UA_BY_FINGERPRINT[fp]
            version = fp.replace("chrome", "")
            assert f"Chrome/{version}.0.0.0" in ua, (
                f"{fp} 的 UA 版本不匹配: {ua}"
            )

    def test_ua_for_unknown_falls_back(self):
        """未知指纹返回兜底 UA，而不是 None（否则请求无 UA 直接被拒）。"""
        ua = ua_for("chrome999")
        assert ua and "Mozilla" in ua

    def test_resolve_random_returns_pool_member(self):
        for _ in range(20):
            assert resolve_fingerprint("random") in FINGERPRINTS

    def test_resolve_none_returns_pool_member(self):
        assert resolve_fingerprint(None) in FINGERPRINTS

    def test_resolve_explicit_passthrough(self):
        assert resolve_fingerprint("chrome131") == "chrome131"


class TestMakeSession:
    def test_session_has_matching_ua(self):
        s = make_session(impersonate="chrome124", referer="https://example.com/")
        try:
            assert s.headers["User-Agent"] == UA_BY_FINGERPRINT["chrome124"]
            assert s.headers["Referer"] == "https://example.com/"
        finally:
            s.close()

    def test_session_disables_proxy_env(self):
        """trust_env=False 保证国内数据源直连，不走系统代理。"""
        s = make_session(impersonate="chrome124")
        try:
            assert getattr(s, "trust_env", False) is False
        finally:
            s.close()

    def test_unsupported_fingerprint_falls_back(self):
        """curl_cffi 不支持的指纹应回退到 chrome120，而不是抛异常。"""
        with patch("astock_signals.antiban_http.curl_requests.Session",
                   side_effect=[ValueError("unsupported"), MagicMock()]):
            s = make_session(impersonate="chrome999")
            assert s is not None


# ════════════════════════════════════════════════════════════════
# 节流器
# ════════════════════════════════════════════════════════════════

class TestThrottle:
    def test_first_call_no_min_interval_wait(self):
        """首次调用（_last_call=0）不应等待完整 min_interval。"""
        t = Throttle(1.0, 0.0, 0.0)
        start = time.time()
        t.acquire()
        assert time.time() - start < 0.2

    def test_sequential_calls_respect_min_interval(self):
        t = Throttle(0.25, 0.0, 0.0)
        times = []
        for _ in range(4):
            t.acquire()
            times.append(time.time())
        intervals = [b - a for a, b in zip(times, times[1:])]
        assert min(intervals) >= 0.25 * 0.9, f"间隔不足: {intervals}"

    def test_concurrent_calls_respect_min_interval(self):
        """8 线程并发：预定的时间戳仍应逐次递增 min_interval（预定制节流）。

        断言刻意用节流器**内部预定的时间戳**（`_last_call`），而不是各线程
        `acquire()` 返回后的墙钟打点 —— 后者在 `acquire()` 返回到打点之间
        可能被 OS 抢占，实测机器有负载时会偶发假失败（单独跑必过、全量跑偶挂）。
        预定量才是真正决定实际间隔的机制，且该断言是确定性的。
        """
        interval = 0.2
        t = Throttle(interval, 0.0, 0.0)
        t0 = time.time()

        threads = [threading.Thread(target=t.acquire) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # 8 次预定 → 最后一次的预定时刻应 ≥ 起点 + 7*interval
        # （留 1% 余量吸收首次预定相对 t0 的微小偏移）
        assert t._last_call[0] >= t0 + 7 * interval * 0.99, (
            f"并发预定间隔不足：last={t._last_call[0] - t0:.3f}s，"
            f"期望 ≥ {7 * interval * 0.99:.3f}s"
        )

    def test_jitter_applied(self):
        t = Throttle(0.0, 0.05, 0.1)
        start = time.time()
        t.acquire()
        assert 0.04 <= time.time() - start < 0.5

    def test_set_interval_and_jitter(self):
        t = Throttle(1.0, 0.0, 0.0)
        t.set_interval(0.5)
        t.set_jitter(0.1, 0.2)
        assert t.min_interval == 0.5
        assert (t.jitter_min, t.jitter_max) == (0.1, 0.2)

    def test_reset_clears_last_call(self):
        t = Throttle(5.0, 0.0, 0.0)
        t.acquire()
        t.reset()
        start = time.time()
        t.acquire()
        assert time.time() - start < 0.3


# ════════════════════════════════════════════════════════════════
# 响应语义识别
# ════════════════════════════════════════════════════════════════

def _resp(status=200, json_data=None, json_raises=False):
    r = MagicMock()
    r.status_code = status
    if json_raises:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = json_data if json_data is not None else {}
    return r


class TestClassifyResponse:
    def test_rc_102_is_throttled(self):
        """东财 rc=102 = 限流，必须识别为可重试，而不是当作空数据。"""
        assert classify_response(_resp(200, {"rc": 102})) == "throttled"

    def test_rc_102_as_string_is_throttled(self):
        assert classify_response(_resp(200, {"rc": "102"})) == "throttled"

    def test_rc_100_is_not_found(self):
        """rc=100 = 标的不存在，重试无意义，应区别于限流。"""
        assert classify_response(_resp(200, {"rc": 100})) == "not_found"

    def test_rc_0_is_ok(self):
        assert classify_response(_resp(200, {"rc": 0, "data": {}})) == "ok"

    def test_403_and_429_are_throttled(self):
        assert classify_response(_resp(403)) == "throttled"
        assert classify_response(_resp(429)) == "throttled"

    def test_5xx_is_server_error(self):
        for code in (500, 502, 503, 504):
            assert classify_response(_resp(code)) == "server_error"

    def test_non_json_200_is_ok(self):
        """JSONP / 纯文本接口返回 200 且非 JSON —— 属正常，不应重试。"""
        assert classify_response(_resp(200, json_raises=True)) == "ok"

    def test_404_is_unknown(self):
        assert classify_response(_resp(404)) == "unknown"

    def test_only_throttled_and_server_error_retryable(self):
        assert is_retryable("throttled") and is_retryable("server_error")
        for v in ("ok", "not_found", "unknown"):
            assert not is_retryable(v)

    def test_backoff_grows_and_is_capped(self):
        assert backoff_seconds(0) < backoff_seconds(2)
        assert backoff_seconds(10) <= ab.MAX_BACKOFF


# ════════════════════════════════════════════════════════════════
# 重试编排
# ════════════════════════════════════════════════════════════════

class TestRequestWithRetry:
    def _throttle(self):
        return Throttle(0.0, 0.0, 0.0)

    def test_ok_returns_without_retry(self):
        session = MagicMock()
        session.get.return_value = _resp(200, {"rc": 0})
        resp = request_with_retry(lambda: session, self._throttle(), "get",
                                  "https://x.test", max_retries=3)
        assert resp is session.get.return_value
        assert session.get.call_count == 1

    def test_not_found_returns_without_retry(self):
        """rc=100 不应浪费重试次数。"""
        session = MagicMock()
        session.get.return_value = _resp(200, {"rc": 100})
        request_with_retry(lambda: session, self._throttle(), "get",
                           "https://x.test", max_retries=3)
        assert session.get.call_count == 1

    def test_network_exception_retries_then_returns_none(self):
        session = MagicMock()
        session.get.side_effect = ConnectionError("boom")
        with patch("astock_signals.antiban_http.time.sleep"):
            resp = request_with_retry(lambda: session, self._throttle(), "get",
                                      "https://x.test", max_retries=3)
        assert resp is None
        assert session.get.call_count == 3

    def test_recovers_after_transient_failure(self):
        session = MagicMock()
        good = _resp(200, {"rc": 0})
        session.get.side_effect = [ConnectionError("boom"), good]
        with patch("astock_signals.antiban_http.time.sleep"):
            resp = request_with_retry(lambda: session, self._throttle(), "get",
                                      "https://x.test", max_retries=3)
        assert resp is good
        assert session.get.call_count == 2

    def test_throttled_retries_and_calls_on_throttled(self):
        session = MagicMock()
        session.get.return_value = _resp(200, {"rc": 102})
        rotated = []
        with patch("astock_signals.antiban_http.time.sleep"):
            resp = request_with_retry(lambda: session, self._throttle(), "get",
                                      "https://x.test", max_retries=3,
                                      on_throttled=lambda: rotated.append(1))
        assert resp is session.get.return_value
        assert session.get.call_count == 3
        # 每次命中限流都通知调用方换指纹
        assert len(rotated) == 3

    def test_server_error_retries(self):
        session = MagicMock()
        session.get.return_value = _resp(503)
        with patch("astock_signals.antiban_http.time.sleep"):
            request_with_retry(lambda: session, self._throttle(), "get",
                               "https://x.test", max_retries=2)
        assert session.get.call_count == 2

    def test_retry_disabled_returns_immediately(self):
        session = MagicMock()
        session.get.return_value = _resp(403)
        request_with_retry(lambda: session, self._throttle(), "get",
                           "https://x.test", max_retries=3, retry_on_status=False)
        assert session.get.call_count == 1

    def test_none_params_filtered(self):
        """params 里的 None 值应被剔除，避免拼出 ?a=None。"""
        session = MagicMock()
        session.get.return_value = _resp(200, {"rc": 0})
        request_with_retry(lambda: session, self._throttle(), "get", "https://x.test",
                           params={"a": 1, "b": None}, max_retries=1)
        _, kwargs = session.get.call_args
        assert kwargs["params"] == {"a": 1}

    def test_throttle_acquired_once_per_attempt(self):
        """节流器每次尝试都调用一次（保证重试也受限流约束）。"""
        throttle = MagicMock()
        session = MagicMock()
        session.get.return_value = _resp(200, {"rc": 0})
        request_with_retry(lambda: session, throttle, "get", "https://x.test",
                           max_retries=3)
        assert throttle.acquire.call_count == 1


# ════════════════════════════════════════════════════════════════
# 字节 / 文本获取（腾讯 GBK）
# ════════════════════════════════════════════════════════════════

class TestGetBytesText:
    def _session(self, content: bytes, status=200):
        s = MagicMock()
        r = MagicMock()
        r.status_code = status
        r.content = content
        s.get.return_value = r
        return s

    def test_get_bytes_returns_content(self):
        s = self._session(b"hello")
        out = get_bytes(lambda: s, Throttle(0.0), "https://x.test", max_retries=1)
        assert out == b"hello"

    def test_get_text_decodes_gbk(self):
        """腾讯接口是 GBK 编码，解码错会中文乱码。"""
        raw = 'v_sh600000="1~贵州茅台~600000"'.encode("gbk")
        s = self._session(raw)
        out = get_text(lambda: s, Throttle(0.0), "https://x.test",
                       encoding="gbk", max_retries=1)
        assert "贵州茅台" in out

    def test_get_bytes_none_on_non_200(self):
        s = self._session(b"", status=500)
        assert get_bytes(lambda: s, Throttle(0.0), "https://x.test",
                         max_retries=1) is None

    def test_get_bytes_none_on_exception(self):
        s = MagicMock()
        s.get.side_effect = ConnectionError("boom")
        with patch("astock_signals.antiban_http.time.sleep"):
            assert get_bytes(lambda: s, Throttle(0.0), "https://x.test",
                             max_retries=2) is None
