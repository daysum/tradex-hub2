"""Tests for 东财/腾讯 请求统一收敛（v3.3.19 防限流改造的回归守卫）。

覆盖两类问题：
  1. **行为**：em_client 与 anti_ban_client 必须是同一入口（曾各持一把节流器 →
     同时取数时实际速率翻倍）；腾讯传输层必须走带指纹+节流的实现。
  2. **防回退**：静态扫描源码，确保没有模块绕过统一入口直接请求
     eastmoney.com / gtimg.cn —— 这类裸请求会让全局节流失效。
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parents[1] / "tradex" / "src"

# 允许直接请求东财的模块（统一入口本身）
EM_ALLOWED = {"antiban_http.py", "anti_ban_client.py", "em_client.py"}
# 允许直接请求腾讯的模块
TX_ALLOWED = {"antiban_http.py", "http_fetchers.py"}

_EM_HOST_RE = ("eastmoney.com",)
_TX_HOST_RE = ("gtimg.cn",)
_RAW_HTTP_FUNCS = {"get", "post", "open", "urlopen", "Session", "session"}


def _iter_py():
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _str_constants(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _raw_http_calls_with_host(tree: ast.AST, hosts: tuple[str, ...]):
    """找出「原始 HTTP 调用 + 同一语句里含目标域名」的调用点。"""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else ""
        )
        if name not in _RAW_HTTP_FUNCS:
            continue
        if any(any(h in s for h in hosts) for s in _str_constants(node)):
            hits.append((name, node.lineno))
    return hits


def _has_session_ctor_with_host(tree: ast.AST, hosts: tuple[str, ...]):
    """文件里同时出现「自建 Session」与目标域名常量 → 疑似自建会话绕过限流。"""
    text_hosts = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and any(h in n.value for h in hosts)
    ]
    if not text_hosts:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else ""
            )
            if name == "Session":
                return True
    return False


# ════════════════════════════════════════════════════════════════
# 防回退：静态守卫
# ════════════════════════════════════════════════════════════════

class TestNoBypassGuard:
    def test_no_raw_eastmoney_request_outside_entrypoint(self):
        offenders = []
        for path in _iter_py():
            if path.name in EM_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name, lineno in _raw_http_calls_with_host(tree, _EM_HOST_RE):
                offenders.append(f"{path.name}:{lineno} {name}(...)")
        assert not offenders, (
            "以下位置绕过统一入口直接请求东财（会使全局节流失效）：\n  "
            + "\n  ".join(offenders)
        )

    def test_no_raw_tencent_request_outside_http_fetchers(self):
        offenders = []
        for path in _iter_py():
            if path.name in TX_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name, lineno in _raw_http_calls_with_host(tree, _TX_HOST_RE):
                offenders.append(f"{path.name}:{lineno} {name}(...)")
        assert not offenders, (
            "以下位置绕过统一入口直接请求腾讯：\n  " + "\n  ".join(offenders)
        )

    def test_no_self_built_session_for_eastmoney(self):
        """除统一入口外，不应自建 Session 去打东财域名。"""
        offenders = []
        for path in _iter_py():
            if path.name in EM_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if _has_session_ctor_with_host(tree, _EM_HOST_RE):
                offenders.append(path.name)
        assert not offenders, (
            "以下文件自建 Session 请求东财，应改用 em_get：\n  "
            + "\n  ".join(offenders)
        )

    def test_no_self_built_session_for_tencent(self):
        offenders = []
        for path in _iter_py():
            if path.name in TX_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if _has_session_ctor_with_host(tree, _TX_HOST_RE):
                offenders.append(path.name)
        assert not offenders, (
            "以下文件自建 Session 请求腾讯，应改用 _urlopen_no_proxy：\n  "
            + "\n  ".join(offenders)
        )


# ════════════════════════════════════════════════════════════════
# 东财：单一入口
# ════════════════════════════════════════════════════════════════

class TestEastmoneySingleEntry:
    def test_em_client_delegates_to_anti_ban_client(self):
        """em_client.em_get 必须就是 anti_ban_client.em_get（同一节流器）。

        曾有两套独立实现（各持一把锁 + 一个 session），
        同时取数时实际请求速率是设计值的 2 倍。
        """
        from astock_signals.anti_ban_client import em_get as core_em_get
        from tradex.data_sources.em_client import em_get as wrapper_em_get

        assert wrapper_em_get is core_em_get

    def test_em_client_exposes_em_text(self):
        from tradex.data_sources import em_client

        assert callable(em_client.em_text)
        assert callable(em_client.em_reset_session)

    def test_fetch_stock_boards_uses_em_get(self):
        """fetch_stock_boards 必须经 em_get 取数。"""
        from tradex.data_sources import em_client

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "data": {"diff": [{"f14": "白酒", "f12": "BK0477", "f3": 1.2, "f128": "贵州茅台"}]}
        }
        with patch.object(em_client, "em_get", return_value=fake_resp) as m:
            df = em_client.fetch_stock_boards("600519")

        m.assert_called_once()
        url = m.call_args[0][0]
        assert "eastmoney.com" in url
        assert list(df.columns) == ["板块名称", "板块代码", "涨跌幅", "领涨股票"]
        assert df.iloc[0]["板块名称"] == "白酒"

    def test_fetch_stock_boards_none_response_returns_empty(self):
        """em_get 重试耗尽返回 None 时，应返回空 DataFrame 而不是崩溃。"""
        from tradex.data_sources import em_client

        with patch.object(em_client, "em_get", return_value=None):
            df = em_client.fetch_stock_boards("600519")
        assert df.empty

    def test_em_get_returns_none_when_exhausted(self):
        """em_get 全重试失败应返回 None，而不是抛异常穿透到 MCP 层。"""
        from astock_signals import anti_ban_client as abc

        session = MagicMock()
        session.get.side_effect = ConnectionError("boom")
        with patch.object(abc, "_ensure_session", return_value=session), \
             patch("astock_signals.antiban_http.time.sleep"):
            assert abc.em_get("https://push2.eastmoney.com/x", timeout=1) is None

    def test_rc_102_triggers_fingerprint_rotation(self):
        """命中 rc=102 限流应重建会话（换指纹），而不是继续用同一指纹硬打。"""
        from astock_signals import anti_ban_client as abc

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"rc": 102}
        session = MagicMock()
        session.get.return_value = resp

        with patch.object(abc, "_ensure_session", return_value=session), \
             patch.object(abc, "em_reset_session") as reset, \
             patch("astock_signals.antiban_http.time.sleep"):
            abc.em_get("https://push2.eastmoney.com/x", timeout=1)

        assert reset.call_count >= 1

    def test_em_datacenter_none_response_returns_empty(self):
        from astock_signals import anti_ban_client as abc

        with patch.object(abc, "em_get", return_value=None):
            assert abc.em_datacenter("RPT_LIFT_STAGE") == []

    def test_em_push2_none_response_returns_empty_dict(self):
        from astock_signals import anti_ban_client as abc

        with patch.object(abc, "em_get", return_value=None):
            assert abc.em_push2({}) == {}


# ════════════════════════════════════════════════════════════════
# 腾讯：传输层加固
# ════════════════════════════════════════════════════════════════

class TestTencentTransport:
    def test_urlopen_returns_readable_bytes(self):
        """保留 .read() 语义（调用方按 GBK 解码）。"""
        from tradex.data_sources import http_fetchers as hf

        with patch.object(hf, "get_bytes", return_value=b"abc"):
            resp = hf._urlopen_no_proxy("https://qt.gtimg.cn/q=sh600000", timeout=5)
        assert resp.read() == b"abc"
        assert resp.status_code == 200

    def test_urlopen_raises_when_exhausted(self):
        """重试耗尽应抛 RuntimeError（与原 urllib 抛 URLError 语义一致）。"""
        from tradex.data_sources import http_fetchers as hf

        with patch.object(hf, "get_bytes", return_value=None):
            with pytest.raises(RuntimeError):
                hf._urlopen_no_proxy("https://qt.gtimg.cn/q=sh600000", timeout=5)

    def test_urlopen_goes_through_throttled_client(self):
        """必须经 get_bytes（带节流+指纹），而不是裸 urllib。"""
        from tradex.data_sources import http_fetchers as hf

        with patch.object(hf, "get_bytes", return_value=b"x") as m:
            hf._urlopen_no_proxy("https://qt.gtimg.cn/q=sh600000", timeout=7)
        m.assert_called_once()
        _, kwargs = m.call_args
        assert kwargs["timeout"] == 7

    def test_tencent_quote_vals_parses_gbk(self):
        """腾讯 GBK 解码 + ~ 分隔字段解析（原测试的离线等价版本）。"""
        from tradex.data_sources import http_fetchers as hf

        payload = ('v_sh600000="1~贵州茅台~600000~1500.0~' + "~".join(["1"] * 50) + '"')
        with patch.object(hf, "_urlopen_no_proxy",
                          return_value=hf._TencentResponse(payload.encode("gbk"))):
            vals = hf._tencent_quote_vals("sh600000")
        assert vals[1] == "贵州茅台"
        assert vals[2] == "600000"

    def test_tencent_batch_failure_skips_batch(self):
        """批量拉取时单批失败应跳过，不中断整体。"""
        from tradex.data_sources import http_fetchers as hf

        with patch.object(hf, "_urlopen_no_proxy", side_effect=RuntimeError("boom")):
            rows = hf._tencent_batch_quotes(["600000", "600519"])
        assert rows == []

    def test_tencent_reset_session(self):
        from tradex.data_sources import http_fetchers as hf

        fake = MagicMock()
        hf._tencent_session = fake
        try:
            hf.tencent_reset_session()
            fake.close.assert_called_once()
            assert hf._tencent_session is None
        finally:
            hf._tencent_session = None
