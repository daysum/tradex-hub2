"""
Anti-ban rate-limited HTTP client for Eastmoney APIs.

东财系 HTTP 接口（push2 / push2his / datacenter-web / search-api / np-weblist）
有风控：每秒 >5 次 / 单 IP 并发 ≥10 / 1 分钟 ≥200 次 / 5 分钟 ≥300 次 → 临时封 IP。

所有 eastmoney.com 请求一律走此入口，四层防护：
  1. **TLS 指纹伪装**：curl_cffi 模拟真实 Chrome 的 JA3/JA4 握手特征，
     UA 与 impersonate 版本严格匹配（不匹配会被识别为伪造）
  2. **串行限流**：预定制节流，最小间隔 + 随机抖动，高并发下不退化
  3. **指数退避重试**：网络异常 / 403 / 429 / rc=102 限流 → 1s、2s、4s 退避重试
  4. **限流换指纹**：命中限流时重建会话并切换浏览器指纹

与 `tradex.data_sources.em_client` 共用同一套底层实现（`astock_signals.antiban_http`），
两者节流器互相独立（东财总间隔仍是全局一份，见下方 `_em_last_call` 说明）。

TradingAgents-astock 移植模块。V0.1。
"""

from __future__ import annotations

import os
import re
import random
import time
import logging
import threading

from .antiban_http import (
    DEFAULT_FINGERPRINT,
    EM_JITTER_MAX,
    EM_JITTER_MIN,
    EM_MAX_RETRY,
    EM_MIN_INTERVAL,
    FINGERPRINTS,
    Throttle,
    classify_response,
    make_session,
    request_with_retry,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EM_SESSION = None  # curl_cffi.Session | None
_EM_MIN_INTERVAL: float = EM_MIN_INTERVAL
_EM_JITTER_MIN: float = EM_JITTER_MIN
_EM_JITTER_MAX: float = EM_JITTER_MAX
_EM_MAX_RETRY: int = EM_MAX_RETRY
_em_last_call: list[float] = [0.0]  # mutable for inner func access
_lock = threading.Lock()  # 保护 session 初始化和限流逻辑

_EM_REFERER = "https://data.eastmoney.com/"

# 当前会话使用的指纹（命中限流时轮换）
_current_fingerprint: str = DEFAULT_FINGERPRINT

# 全局节流器：_last_call 直接复用模块级 _em_last_call，
# 保证测试与外部代码对 _em_last_call 的读写仍然生效。
_throttle = Throttle(_EM_MIN_INTERVAL, _EM_JITTER_MIN, _EM_JITTER_MAX)
_throttle._last_call = _em_last_call  # noqa: SLF001 - 有意共享同一列表

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 6 位纯数字股票代码正则（用于 SQL filter 注入防护）
_CODE_RE = re.compile(r"^\d{6}$")


# ---------------------------------------------------------------------------
# 主机镜像降级（v3.3.21）
#
# 东财同一套 API 有多个入口主机。**节点故障是按主机（甚至按路由）发生的**，
# 实测 2026-09-20：
#   push2.eastmoney.com/api/qt/clist/get       → "Connection closed abruptly"
#                                                （连命令行 curl 都是 HTTP 000）
#   push2.eastmoney.com/api/qt/stock/fflow/... → 200 rc=0（同主机、不同路由，正常）
#   push2his.eastmoney.com/api/qt/stock/kline/get → 连接被拒
#     （但同主机 /api/qt/stock/fflow/daykline/get 正常 —— 又是按路由）
#   push2delay.eastmoney.com（clist/kline/fflow/ulist/slist）→ 全部 200 rc=0
# 也就是说：主源一挂，依赖它的数据源全废，而镜像明明可用。
#
# 为什么放在 em_get 而不是各调用点：
#   全项目有多处直接打 push2 / push2his（em_push2 / em_push2_fund_flow /
#   em_push2his_fund_flow / limit_up_board._em_clist_get /
#   akshare_fetchers._fetch_index_daily_from_push2his / astock_signals_fetchers /
#   em_client.slist）。逐处写降级循环既重复又容易漏，收敛在统一入口后
#   所有调用点自动受益。
#
# ⚠️ 只在「连接层失败」或「5xx」时切换：
#   403 / 429 / rc=102 是**按 IP** 的风控，换主机没有任何用
#   （实测 slist 在 push2 与 push2delay 上都返回 rc=102），硬切只会让风控更糟。
#
# ⚠️ 镜像范围必须逐路径实测：push2delay **不服务** push2ex 的
#   `/getTopicZTPool`（返回 404 空体），故 push2ex **刻意不镜像** ——
#   否则会把健康主机也跳过。
# ---------------------------------------------------------------------------
_EM_PUSH2_MIRROR: str = os.environ.get("EM_PUSH2_MIRROR", "push2delay.eastmoney.com")

# 精确主机映射（键为主源主机名）。
#
# ⚠️ **只放「载荷已验证等价」的主机**。push2delay 并非 push2 的全功能镜像，
# 实测 2026-09-20（同一 secid）：
#   clist/get            push2delay → rc=0 有数据 ✓
#   fflow/kline/get klt=1 push2 → 100 条 / push2delay → 100 条，数值一致 ✓
#   kline/get  klt=101    push2delay → rc=0 但 **klines 为空** ✗（无日K）
#   fflow/daykline/get    push2his → **5 条** / push2delay → **仅 1 条** ✗（静默降级）
# 因此 **push2his 刻意不配镜像**：它的日K路由没有可用镜像，
# 而日线资金流映过去会把 5 条悄悄变成 1 条 —— 静默降级数据比直接失败更糟。
# 教训：验证镜像必须看**载荷**，不能只看 HTTP 200 / rc=0。
_EM_HOST_MIRRORS: dict[str, str] = {
    "push2.eastmoney.com": _EM_PUSH2_MIRROR,
}

# push2 族的**带数字前缀子域**（akshare 用的是 70.push2 / 17.push2）：
# 实测它们与 push2 主域同时降级，理论上可归一到 push2delay。
# 但**当前项目代码没有任何一处请求这些子域**（只有 akshare 自己用），
# 而 akshare 绕过 em_get，配了也不生效；且未逐路径验证载荷等价。
# 故**不配**，避免留下未经验证的映射（见上方「验证镜像必须看载荷」的教训）。
# 若将来把 akshare 源改为直连，直接写 `push2.eastmoney.com` 即可命中已有映射。

_EM_MIRROR_ENABLED: bool = os.environ.get("EM_HOST_MIRROR", "1").lower() not in (
    "0", "false", "no", "off",
)

# 主源失败后的「静默期」（秒）：期间直接走镜像，不再先撞一遍死主机。
# 没有它的话，每次调用都要白等 max_retries 次退避（默认 1s+2s ≈ 3s）。
_EM_MIRROR_COOLDOWN: float = float(os.environ.get("EM_MIRROR_COOLDOWN", "300"))

# primary_host -> 静默期截止时间戳
_mirror_cooldown_until: dict[str, float] = {}

_URL_HOST_RE = re.compile(r"^https?://([^/]+)")


def _host_of(url: str) -> str | None:
    """取出 URL 的主机名（不含端口之外的路径）。"""
    m = _URL_HOST_RE.match(url)
    return m.group(1) if m else None


def _mirror_for_host(host: str) -> str | None:
    """主机对应的镜像主机；无镜像返回 None。

    只认 `_EM_HOST_MIRRORS` 里**载荷已验证等价**的精确主机名。
    刻意不做子域通配 —— 未经逐路径验证的映射可能静默降级数据（见上方注释）。
    """
    return _EM_HOST_MIRRORS.get(host)


def _find_mirror(url: str) -> tuple[str, str] | None:
    """若 url 的主机有镜像，返回 (主源主机, 镜像URL)；否则 None。

    按**主机名**精确匹配（不再用子串匹配，避免 URL 路径里恰好含主机名时误判）。
    """
    if not _EM_MIRROR_ENABLED:
        return None
    host = _host_of(url)
    if host is None:
        return None
    mirror_host = _mirror_for_host(host)
    if mirror_host is None or mirror_host == host:
        return None
    return host, url.replace(host, mirror_host, 1)


def _should_try_mirror(resp) -> bool:
    """主源失败到「换主机可能有效」的程度才切镜像。

    - resp is None        → 连接层失败（节点不可达 / 被拒）→ 换
    - 5xx server_error    → 服务端故障 → 换
    - 403/429/rc=102      → 按 IP 风控，换主机无效 → **不换**
    """
    if resp is None:
        return True
    return classify_response(resp) == "server_error"


def _mirror_response_usable(resp) -> bool:
    """镜像响应是否「语义有效」—— 决定要不要进入静默期。

    404 等 `unknown` 说明**镜像根本不服务这个路径**（实测 push2delay 对
    push2ex 的 getTopicZTPool 返回 404 空体）。此时绝不能设静默期，
    否则会把**健康的主源**也一起跳过，故障面反而扩大。
    """
    if resp is None:
        return False
    return classify_response(resp) in ("ok", "throttled", "not_found")


def reset_host_mirrors() -> None:
    """清空镜像静默期（主源恢复后立即重新探测主源）。"""
    with _lock:
        _mirror_cooldown_until.clear()


def get_mirror_status() -> dict[str, float]:
    """当前处于静默期的主机 → 剩余秒数（供诊断 / 看板展示）。"""
    now = time.time()
    with _lock:
        return {
            host: round(until - now, 1)
            for host, until in _mirror_cooldown_until.items()
            if until > now
        }


# ---------------------------------------------------------------------------
# 「死主机」冷却（v3.3.23）—— 熔断器模式
#
# 问题：某个 (主机, 路径) 挂掉后，每次调用都要先白跑满 max_retries 次退避
# 才轮到备源。实测 push2his 的日K路由全挂时，index_daily_amount 每次
# 都要先白等 3 次退避（1s+2s）＋ 节流 ≈ **6.5 秒**，而真正的备源只需 0.25s。
#
# ⚠️ 必须按 **(主机, 路径)** 而非仅主机：
#   实测 push2his 是「`kline/get` 挂了、`fflow/daykline/get` 还活着」。
#   按主机冷却会把同主机上**健康的路径**一起跳过，故障面反而扩大。
#
# 语义与镜像降级一致：只在**主机级失败**（连接失败 / 5xx）时熔断；
# 403 / 429 / rc=102 是按 IP 风控，主机本身没问题，**不熔断**。
# ---------------------------------------------------------------------------
_EM_DEAD_HOST_COOLDOWN: float = float(os.environ.get("EM_DEAD_HOST_COOLDOWN", "60"))

# (host, path) -> 冷却截止时间戳
_dead_until: dict[tuple[str, str], float] = {}


def _host_and_path(url: str) -> tuple[str | None, str]:
    """拆出 (主机, 路径)，用于按「主机+路径」做失败熔断。"""
    host = _host_of(url)
    if host is None:
        return None, ""
    rest = url.split("://", 1)[-1]
    slash = rest.find("/")
    path = rest[slash:].split("?", 1)[0] if slash >= 0 else "/"
    return host, path


def _is_cooling(store: dict, key) -> bool:
    with _lock:
        return store.get(key, 0.0) > time.time()


def _mark_host_dead(host: str | None, path: str) -> None:
    """把 (host, path) 标记为失败冷却，期间直接返回 None 让调用方走备源。"""
    if not host or _EM_DEAD_HOST_COOLDOWN <= 0:
        return
    with _lock:
        _dead_until[(host, path)] = time.time() + _EM_DEAD_HOST_COOLDOWN


def _clear_host_dead(host: str | None, path: str) -> None:
    """请求成功（或属正常业务结果）→ 解除该 (host, path) 的冷却标记。"""
    if not host:
        return
    with _lock:
        _dead_until.pop((host, path), None)


def reset_dead_hosts() -> None:
    """清空死主机冷却（上游恢复后立即重探）。"""
    with _lock:
        _dead_until.clear()


def get_dead_host_status() -> dict[str, float]:
    """处于冷却期的 "host+path" → 剩余秒数（供诊断 / 看板展示）。"""
    now = time.time()
    with _lock:
        return {
            f"{h}{p}": round(until - now, 1)
            for (h, p), until in _dead_until.items()
            if until > now
        }


def reset_host_health() -> None:
    """一次性清空镜像静默期与死主机冷却（运维用）。"""
    reset_host_mirrors()
    reset_dead_hosts()


def _ensure_session():
    """Get or create the shared Eastmoney session (lazy init, thread-safe).

    使用 curl_cffi 会话：TLS 握手特征与真实 Chrome 一致，绕过 JA3/JA4 指纹识别；
    UA 与 impersonate 版本严格匹配。
    trust_env=False 绕过系统代理（Windows IE 代理设置），东财 API 国内直连。
    """
    global _EM_SESSION
    if _EM_SESSION is None:
        with _lock:
            if _EM_SESSION is None:  # double-check
                _EM_SESSION = make_session(
                    impersonate=_current_fingerprint,
                    referer=_EM_REFERER,
                )
    return _EM_SESSION


# ---------------------------------------------------------------------------
# Public API: rate-limited request
# ---------------------------------------------------------------------------


def _em_once(
    method: str,
    url: str,
    params: dict | None,
    headers: dict | None,
    timeout: int,
    kwargs: dict,
):
    """单次请求（含节流 + 退避重试 + 限流换指纹），不做镜像降级 / 熔断。"""
    return request_with_retry(
        _ensure_session,
        _throttle,
        method,
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        max_retries=_EM_MAX_RETRY,
        on_throttled=_rotate_fingerprint,
        **kwargs,
    )


def _em_request(
    method: str,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs,
):
    """东财统一请求编排（GET / POST 共用）。

    指纹伪装 + 节流 + 指数退避重试 + 主机镜像降级 + 失败熔断。
    细节见 `em_get` 的文档。
    """
    host, path = _host_and_path(url)
    mirror_info = _find_mirror(url)

    # 1. 镜像静默期 → 直接走镜像，不再白撞死主机
    if mirror_info is not None and _is_cooling(_mirror_cooldown_until, mirror_info[0]):
        logger.debug("em: %s 处于镜像静默期，直接使用 %s", mirror_info[0], mirror_info[1])
        return _em_once(method, mirror_info[1], params, headers, timeout, kwargs)

    # 2. 该 (主机, 路径) 处于熔断冷却期 → 立即失败，让调用方走备源
    if host and _is_cooling(_dead_until, (host, path)):
        logger.debug("em: %s%s 处于失败冷却期，跳过", host, path)
        return None

    resp = _em_once(method, url, params, headers, timeout, kwargs)

    # 3. 成功（或属正常业务结果：rc=100 标的不存在等）→ 解除熔断标记
    if not _should_try_mirror(resp):
        _clear_host_dead(host, path)
        return resp

    # 4. 主机级失败 → 先试镜像
    if mirror_info is not None:
        primary, mirror_url = mirror_info
        logger.warning("em: %s 不可用（%s），切换镜像 %s",
                       primary,
                       "连接失败" if resp is None else f"HTTP {getattr(resp, 'status_code', '?')}",
                       mirror_url)

        mirror_resp = _em_once(method, mirror_url, params, headers, timeout, kwargs)
        if mirror_resp is None:
            # 镜像也失败：不设静默期，下次仍先试主源（避免主源恢复后还被跳过）
            logger.warning("em: 镜像 %s 亦失败", mirror_url)
            _mark_host_dead(host, path)
            return None

        if not _mirror_response_usable(mirror_resp):
            # 镜像不服务该路径（如 push2delay 无 push2ex 的 getTopicZTPool）→
            # 不设静默期，原样返回主源结果；同时熔断该 (主机,路径) 免得反复白撞
            logger.warning(
                "em: 镜像 %s 不服务该路径（HTTP %s），不进入静默期",
                mirror_url, getattr(mirror_resp, "status_code", "?"),
            )
            _mark_host_dead(host, path)
            return resp

        with _lock:
            _mirror_cooldown_until[primary] = time.time() + _EM_MIRROR_COOLDOWN
        logger.info("em: 已切至镜像 %s（静默 %.0fs）", mirror_url, _EM_MIRROR_COOLDOWN)
        return mirror_resp

    # 5. 无镜像可切 → 熔断该 (主机, 路径)，下次直接失败让调用方走备源
    _mark_host_dead(host, path)
    return resp


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs,
):
    """东财统一 GET 入口：指纹伪装 + 节流 + 指数退避重试 + 主机镜像降级 + 失败熔断。

    所有 eastmoney.com 接口都应通过它请求，避免多 Agent 高频拉数据被封 IP。

    重试语义（v3.3.19）：
      - 网络异常 / HTTP 403 / 429 / 东财 rc=102 → 退避重试（1s、2s、4s），
        并重建会话轮换浏览器指纹
      - 东财 rc=100（标的不存在）→ 立即返回，不浪费重试次数
      - 正常响应 → 立即返回

    主机镜像降级（v3.3.21）：
      - 主源**连接层失败**或返回 5xx → 自动改用镜像主机重试一次
        （如 push2 → push2delay）；403/429/rc=102 属按 IP 风控，不切主机
      - 镜像成功后进入静默期（默认 300s），期间直接走镜像，
        避免每次调用都白等一遍死主机的退避

    失败熔断（v3.3.23）：
      - 某个 **(主机, 路径)** 出现主机级失败（连接失败 / 5xx）后进入冷却
        （默认 60s），期间**立即返回 None**，让调用方直接走备源 ——
        不再白跑满 max_retries 次退避（实测 push2his 日K挂时每次白等 ≈6.5s）
      - 按**主机+路径**而非仅主机：push2his 实测是「`kline/get` 挂、
        `fflow/daykline` 活」，按主机熔断会误伤健康路径
      - 403 / 429 / rc=102 是按 IP 风控，主机本身没问题 → **不熔断**

    Args:
        url: Full URL to request.
        params: Query parameters dict.
        headers: Optional per-request headers (e.g. Referer/Origin).
        timeout: Request timeout in seconds.
        **kwargs: Passed to session.get().

    Returns:
        Response object（全部重试失败、或处于冷却期时返回 None，调用方需判空）。
    """
    return _em_request("get", url, params=params, headers=headers,
                       timeout=timeout, **kwargs)


def em_post(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs,
):
    """东财统一 POST 入口（与 `em_get` 共用节流 / 重试 / 镜像 / 熔断）。

    用于 emappdata 等人气榜类 POST 接口。`json=` / `data=` 经 `**kwargs`
    透传给 `session.post()`。

    Returns:
        Response object（失败或处于冷却期时返回 None，调用方需判空）。
    """
    return _em_request("post", url, params=params, headers=headers,
                       timeout=timeout, **kwargs)


def _rotate_fingerprint() -> None:
    """命中限流：关闭当前会话并切换指纹，下次请求用新指纹重建连接。

    从完整指纹池里挑一个与当前不同的版本；池为空时保持原指纹不变。
    """
    global _current_fingerprint
    with _lock:
        candidates = [f for f in FINGERPRINTS if f != _current_fingerprint]
        if candidates:
            _current_fingerprint = random.choice(candidates)
        logger.debug("Eastmoney throttled → rotating fingerprint to %s",
                     _current_fingerprint)
    em_reset_session()


def set_min_interval(seconds: float) -> None:
    """Adjust the minimum interval between Eastmoney requests.

    Args:
        seconds: Minimum seconds between requests (default 1.0, env EM_RATE_LIMIT_INTERVAL).
    """
    global _EM_MIN_INTERVAL
    with _lock:
        _EM_MIN_INTERVAL = float(seconds)
    _throttle.set_interval(_EM_MIN_INTERVAL)


def set_jitter_range(min_val: float, max_val: float) -> None:
    """Adjust the random jitter range added to each request's wait time.

    Args:
        min_val: Minimum jitter seconds (default 0.1, env EM_JITTER_MIN).
        max_val: Maximum jitter seconds (default 0.5, env EM_JITTER_MAX).
    """
    global _EM_JITTER_MIN, _EM_JITTER_MAX
    with _lock:
        _EM_JITTER_MIN = float(min_val)
        _EM_JITTER_MAX = float(max_val)
    _throttle.set_jitter(_EM_JITTER_MIN, _EM_JITTER_MAX)


def set_max_retry(retries: int) -> None:
    """Adjust the maximum retry count for Eastmoney requests.

    Args:
        retries: Max retry attempts (default 3, env EM_MAX_RETRY).
    """
    global _EM_MAX_RETRY
    with _lock:
        _EM_MAX_RETRY = int(retries)


def em_reset_session() -> None:
    """Close and recreate the Eastmoney session (e.g. after IP change)."""
    global _EM_SESSION
    with _lock:
        if _EM_SESSION is not None:
            try:
                _EM_SESSION.close()
            except Exception as e:
                logger.debug("em_reset_session close error: %s", e)
            _EM_SESSION = None


# ---------------------------------------------------------------------------
# Input validation helper
# ---------------------------------------------------------------------------


def validate_code(code: str) -> str:
    """校验股票代码格式（纯 6 位数字），防止 SQL 注入。

    Args:
        code: 股票代码

    Returns:
        校验通过的代码

    Raises:
        ValueError: 代码格式不合法
    """
    if not _CODE_RE.match(code):
        raise ValueError(f"Invalid stock code: {code!r} (must be 6 digits)")
    return code


# ---------------------------------------------------------------------------
# Eastmoney Datacenter unified query helper
# ---------------------------------------------------------------------------


def em_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁 等共用入口。

    Args:
        report_name: Report name, e.g. "RPT_LIFT_STAGE", "RPT_DAILYBILLBOARD_DETAILSNEW".
        columns: Column selection, "ALL" for everything.
        filter_str: Filter expression, e.g. '(SECURITY_CODE="000858")'.
        page_size: Results per page (max 200).
        sort_columns: Column name to sort by.
        sort_types: Sort direction (-1=desc, 1=asc).

    Returns:
        List of dicts, each representing one row.
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    # 403 / rc=102 的退避重试与指纹轮换已由 em_get 统一处理
    r = em_get(_DATACENTER_URL, params=params, timeout=15)
    if r is None:
        logger.warning("em_datacenter: all retries exhausted for %s", report_name)
        return []

    r.raise_for_status()

    try:
        d = r.json()
    except ValueError:
        logger.error("em_datacenter: response is not valid JSON (status=%d)", r.status_code)
        return []

    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ---------------------------------------------------------------------------
# Convenience: push2 / push2his direct request helpers
# ---------------------------------------------------------------------------


def em_push2(params: dict, timeout: int = 15) -> dict:
    """Query Eastmoney push2 API (realtime market data).

    经 em_get 请求，因此自动享有指纹伪装 / 节流 / 退避重试 /
    **主机镜像降级**（v3.3.21：push2 不可用时自动切 push2delay）。

    Args:
        params: Query parameters dict.
        timeout: Request timeout.

    Returns:
        Parsed JSON dict；请求失败返回空 dict。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    r = em_get(url, params=params, timeout=timeout)
    return r.json() if r is not None else {}


def em_push2_fund_flow(secid: str, timeout: int = 10) -> dict:
    """Query Eastmoney push2 fund flow API (minute-level).

    Args:
        secid: Security ID, e.g. "1.600519" or "0.000858".
        timeout: Request timeout.

    Returns:
        Parsed JSON dict with klines data.
    """
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid,
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    r = em_get(url, params=params, timeout=timeout)
    return r.json() if r is not None else {}


def em_push2his_fund_flow(secid: str, limit: int = 20, timeout: int = 10) -> dict:
    """Query Eastmoney push2his fund flow API (daily historical).

    Args:
        secid: Security ID.
        limit: Number of trading days.
        timeout: Request timeout.

    Returns:
        Parsed JSON dict with daily klines.
    """
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": secid,
        "lmt": limit,
        "klt": 101,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    r = em_get(url, params=params, timeout=timeout)
    return r.json() if r is not None else {}


def em_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    encoding: str = "utf-8",
) -> str | None:
    """东财接口取原始文本（JSONP / 非 JSON 响应用）。

    与 em_get 共用同一节流器与重试策略，保证「东财全局串行限流」不被绕过。

    Returns:
        解码后的文本；请求失败返回 None。
    """
    resp = em_get(url, params=params, headers=headers, timeout=timeout)
    if resp is None:
        return None
    try:
        if getattr(resp, "status_code", None) != 200:
            return None
        return resp.content.decode(encoding, errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.debug("em_text decode failed: %s", e)
        return None
