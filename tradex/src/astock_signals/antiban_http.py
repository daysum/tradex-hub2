"""统一防封 HTTP 层 —— TLS 指纹伪装 + 串行限流 + 指数退避重试。

移植自 D:\\mywbaiforce\\astock_scraper（《Python + curl_cffi 模拟 Chrome TLS 指纹》方案），
针对 tradex-hub 的多线程/多数据源场景做了三处增强：

1. **预定制节流**（reservation-based）：锁内只计算并预定时间戳，sleep 在锁外执行。
   高并发时后续线程据此排队，不会因持锁 sleep 退化为串行等待。
2. **东财 rc 语义识别**：`rc=102` 是限流（应换指纹 + 长退避重试），
   `rc=100` 是标的不存在（应立即返回空，不浪费重试次数）。
   早期版本把两者都当异常，导致「查不存在的代码」白等 3 次退避。
3. **指纹轮换**：连续命中限流时切换浏览器指纹，避免同一指纹被针对性拦截；
   UA 与 impersonate 版本严格匹配（不匹配会被服务端识别为伪造）。

设计约束：
  - 所有请求**直连**（trust_env=False / 清代理），国内数据源不走代理
  - 会话复用（Keep-Alive + cookies），减少握手开销
  - 线程安全：节流与时间戳更新在锁内原子完成
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from typing import Any, Callable, Optional

from curl_cffi import requests as curl_requests

logger = logging.getLogger("tradex.antiban")

# ---------------------------------------------------------------------------
# 指纹池 —— 按会话轮换，避免长期使用同一指纹被针对性拦截
# ---------------------------------------------------------------------------
FINGERPRINTS: list[str] = [
    "chrome120",
    "chrome124",
    "chrome131",
    "chrome136",
    "chrome142",
    "chrome145",
    "chrome146",
    "chrome150",
]

# impersonate 版本 -> 对应的真实 User-Agent
# 关键：UA 必须与 impersonate 版本一致，否则服务端能检测到不一致并拒绝请求
UA_BY_FINGERPRINT: dict[str, str] = {
    "chrome120": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "chrome124": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "chrome131": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "chrome136": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "chrome142": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "chrome145": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "chrome146": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "chrome150": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
}

# 默认指纹：固定用 chrome124（不用随机），保证行为可复现；
# 需要轮换时传 rotate=True 或 impersonate="random"
DEFAULT_FINGERPRINT = "chrome124"

# 显式关闭指纹伪装（保留节流/重试，但不改 TLS/HTTP2 指纹）。
#
# 为什么需要这个开关：伪装指纹并非对所有站点都是好事 —— 部分站点的 WAF
# 恰恰**封禁**常见浏览器指纹（这类指纹被爬虫大量使用）。
# 实测 2026-09：巨潮 cninfo.com.cn 的 topSearch 接口
#   impersonate=None      -> 200
#   impersonate=chrome110 -> 200
#   impersonate=chrome120 -> 403 Forbidden（WAF 拦截页）
#   impersonate=chrome124 -> 403 Forbidden
# 故 cninfo 走 NO_IMPERSONATE：节流与重试照旧，指纹伪装关闭。
NO_IMPERSONATE = "none"

# 指纹池里所有版本 curl_cffi 都支持吗？——低版本 curl_cffi 缺少 chrome142+，
# 此时按顺序回退到第一个受支持的版本，避免启动即崩。
_KNOWN_GOOD_FALLBACK = "chrome120"


def supported_fingerprints() -> list[str]:
    """列出当前 curl_cffi 安装实际支持的浏览器指纹。"""
    try:
        from curl_cffi.requests.impersonate import BrowserType

        return [b.value for b in BrowserType]
    except Exception:  # noqa: BLE001 - 旧版本 API 差异
        return list(FINGERPRINTS)


def resolve_fingerprint(spec: Optional[str] = None) -> str:
    """把 'chrome' / 'random' / None / 具体版本 解析成可用指纹字符串。

    - None / "random" -> 从指纹池随机取一个
    - "chrome"        -> curl_cffi 自动解析到最新稳定版
    - 其他            -> 原样返回（如 "chrome124"）
    """
    if spec is None or spec == "random":
        return random.choice(FINGERPRINTS)
    return spec


def ua_for(fingerprint: str) -> str:
    """取指纹对应的真实 UA；未知指纹返回 chrome120 的 UA 兜底。"""
    return UA_BY_FINGERPRINT.get(fingerprint, UA_BY_FINGERPRINT[_KNOWN_GOOD_FALLBACK])


# ---------------------------------------------------------------------------
# 限流参数
#
# 东财风控（社区实测）：每秒 >5 次 / 单 IP 并发 ≥10 / 1 分钟 ≥200 次 /
# 5 分钟 ≥300 次 → 临时封 IP。保守取 1.0s 间隔 + 随机抖动。
# 腾讯 qt.gtimg.cn 宽松得多，但仍需节流——批量拉全市场会有数十个连续请求。
# ---------------------------------------------------------------------------
EM_MIN_INTERVAL: float = float(
    os.environ.get("EM_RATE_LIMIT_INTERVAL", os.environ.get("EM_MIN_INTERVAL", "1.0"))
)
EM_JITTER_MIN: float = float(os.environ.get("EM_JITTER_MIN", "0.1"))
EM_JITTER_MAX: float = float(os.environ.get("EM_JITTER_MAX", "0.5"))
EM_MAX_RETRY: int = int(os.environ.get("EM_MAX_RETRY", "3"))

TENCENT_MIN_INTERVAL: float = float(os.environ.get("TENCENT_RATE_LIMIT_INTERVAL", "0.15"))
TENCENT_JITTER_MIN: float = float(os.environ.get("TENCENT_JITTER_MIN", "0.05"))
TENCENT_JITTER_MAX: float = float(os.environ.get("TENCENT_JITTER_MAX", "0.15"))
# 腾讯重试默认 2 次（而非东财的 3 次）：腾讯是兜底源，链路本身已带超时，
# 重试过多会把 worst case 拉长（5s 超时 × 2 + 退避 ≈ 11s）
TENCENT_MAX_RETRY: int = int(os.environ.get("TENCENT_MAX_RETRY", "2"))

# 指数退避上限（秒）
MAX_BACKOFF = 30.0

# ---------------------------------------------------------------------------
# 第二梯队数据源（同花顺 / 财联社 / 巨潮 / 新浪 / 问财）
#
# 这些源风控远松于东财，但「裸 requests + 零节流」的写法在批量取数时
# 仍会被识别为爬虫（典型：同花顺 limit_up_pool 分页、巨潮连续 POST 查 orgId）。
# 间隔取 0.25~0.5s：足够规避风控，又不明显牺牲吞吐。
#
# 为什么按源分设间隔而不是复用东财的 1.0s？
#   把低风险源拖到 1.0s 会让「全市场热榜 + 涨停揭秘 + 涨跌分布」这类
#   串行组合慢得不可用；各自独立才能同时满足「东财不封」与「同花顺不降速」。
# ---------------------------------------------------------------------------
TIER2_FINGERPRINT: str = os.environ.get("TIER2_IMPERSONATE", "chrome120")

THS_MIN_INTERVAL: float = float(os.environ.get("THS_RATE_LIMIT_INTERVAL", "0.25"))
THS_JITTER_MIN: float = 0.05
THS_JITTER_MAX: float = 0.15
THS_MAX_RETRY: int = int(os.environ.get("THS_MAX_RETRY", "2"))

CLS_MIN_INTERVAL: float = float(os.environ.get("CLS_RATE_LIMIT_INTERVAL", "0.30"))
CNINFO_MIN_INTERVAL: float = float(os.environ.get("CNINFO_RATE_LIMIT_INTERVAL", "0.30"))
SINA_MIN_INTERVAL: float = float(os.environ.get("SINA_RATE_LIMIT_INTERVAL", "0.25"))
# 问财 OpenAPI 是带 API Key 的付费接口，失败多为配额/鉴权问题，重试无意义
WENCAI_MIN_INTERVAL: float = float(os.environ.get("WENCAI_RATE_LIMIT_INTERVAL", "0.50"))
WENCAI_MAX_RETRY: int = int(os.environ.get("WENCAI_MAX_RETRY", "1"))


# ---------------------------------------------------------------------------
# 节流器
# ---------------------------------------------------------------------------
class Throttle:
    """预定制节流器 —— 保证任意两次请求间隔 ≥ min_interval + 随机抖动。

    锁内只做「计算等待时间 + 预定时间戳」，sleep 在锁外执行：
    并发线程各自算出应等待的时长后同时等待，而不是在锁里串行排队。
    """

    def __init__(
        self,
        min_interval: float,
        jitter_min: float = 0.0,
        jitter_max: float = 0.0,
    ) -> None:
        self.min_interval = float(min_interval)
        self.jitter_min = float(jitter_min)
        self.jitter_max = float(jitter_max)
        self._last_call = [0.0]  # 列表便于外部测试直接改
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """阻塞至可以发请求，返回本次实际等待秒数。"""
        with self._lock:
            now = time.time()
            wait = self.min_interval - (now - self._last_call[0])
            if wait < 0:
                wait = 0.0
            jitter = random.uniform(self.jitter_min, self.jitter_max)
            sleep_time = wait + jitter
            # 预定本次请求时刻，后续线程据此排队
            self._last_call[0] = now + sleep_time

        if sleep_time > 0:
            time.sleep(sleep_time)
        return sleep_time

    def reset(self) -> None:
        """清空上次请求时间（测试/换 IP 后调用）。"""
        with self._lock:
            self._last_call[0] = 0.0

    def set_interval(self, seconds: float) -> None:
        with self._lock:
            self.min_interval = float(seconds)

    def set_jitter(self, min_val: float, max_val: float) -> None:
        with self._lock:
            self.jitter_min = float(min_val)
            self.jitter_max = float(max_val)


# ---------------------------------------------------------------------------
# 会话工厂
# ---------------------------------------------------------------------------
def make_session(
    impersonate: Optional[str] = None,
    referer: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Any:
    """创建带指纹伪装、直连（不走代理）的 curl_cffi 会话。

    impersonate:
      - None / "random" -> 从指纹池随机取一个
      - "chrome"        -> curl_cffi 自动解析到最新稳定版
      - 具体版本        -> 原样使用（如 "chrome124"）
      - NO_IMPERSONATE  -> **关闭**指纹伪装（个别站点 WAF 会封浏览器指纹）

    trust_env=False 绕过系统代理（Windows IE 代理设置），
    国内财经接口一律直连。
    """
    if impersonate == NO_IMPERSONATE:
        fp: Optional[str] = None
    else:
        fp = resolve_fingerprint(impersonate)

    # 不伪装指纹时仍给一个常规浏览器 UA：实测 cninfo 接受「普通 TLS + 浏览器 UA」
    headers: dict[str, str] = {
        "Accept": "*/*",
        "User-Agent": ua_for(fp) if fp else ua_for(_KNOWN_GOOD_FALLBACK),
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    if fp is None:
        session = curl_requests.Session(headers=headers)
    else:
        try:
            session = curl_requests.Session(impersonate=fp, headers=headers)
        except Exception as exc:  # noqa: BLE001 - 该指纹不被当前 curl_cffi 支持
            logger.debug("impersonate=%s unsupported (%s), fallback to %s",
                         fp, exc, _KNOWN_GOOD_FALLBACK)
            fp = _KNOWN_GOOD_FALLBACK
            headers["User-Agent"] = ua_for(fp)
            session = curl_requests.Session(impersonate=fp, headers=headers)

    try:
        session.trust_env = False
    except Exception:  # noqa: BLE001 - 老版本无此属性
        pass
    return session


# ---------------------------------------------------------------------------
# 响应语义识别（东财 rc 码）
# ---------------------------------------------------------------------------
def _body_is_empty(resp: Any) -> bool:
    """响应体是否为空（仅对真正的 bytes/str 判定，避免误判 mock 对象）。"""
    try:
        content = resp.content
    except Exception:  # noqa: BLE001
        return False
    if isinstance(content, (bytes, bytearray)):
        return not content.strip()
    if isinstance(content, str):
        return not content.strip()
    return False


def classify_response(resp: Any) -> str:
    """判定响应语义：ok / throttled / not_found / server_error / unknown。

    - HTTP 403 / 429        -> throttled（风控拦截，应换指纹 + 退避重试）
    - HTTP 5xx              -> server_error（服务端故障，可退避重试）
    - 东财 rc=102           -> throttled（限流）
    - 东财 rc=100           -> not_found（标的不存在，重试无意义）
    - HTTP 200 + 空 body    -> throttled（JSON 接口的「静默限流」表现，
                               实测同花顺在高频请求下会返回 200 + 空体）
    - HTTP 200 且非 JSON     -> ok（JSONP / 纯文本接口）
    - 其他非 200            -> unknown（重试无意义）
    """
    status = getattr(resp, "status_code", None)
    if status in (403, 429):
        return "throttled"
    if isinstance(status, int) and 500 <= status < 600:
        return "server_error"
    if status != 200:
        return "unknown"

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应（JSONP / 文本 / 空体）
        if _body_is_empty(resp):
            return "throttled"
        return "ok"

    if not isinstance(data, dict):
        return "ok"

    rc = data.get("rc")
    if rc is None:
        return "ok"
    try:
        rc_int = int(rc)
    except (TypeError, ValueError):
        return "ok"
    if rc_int == 102:
        return "throttled"
    if rc_int == 100:
        return "not_found"
    return "ok"


def is_retryable(verdict: str) -> bool:
    """只有限流与服务端故障值得重试；其余立即返回，避免无谓等待。"""
    return verdict in ("throttled", "server_error")


def backoff_seconds(attempt: int) -> float:
    """指数退避 + 抖动：第 1 次约 1s，第 2 次约 2s，第 3 次约 4s，上限 MAX_BACKOFF。"""
    return min(2 ** attempt + random.uniform(0, 1), MAX_BACKOFF)


# ---------------------------------------------------------------------------
# 带重试的请求编排
# ---------------------------------------------------------------------------
def request_with_retry(
    session_provider: Callable[[], Any],
    throttle: Throttle,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 15,
    max_retries: int = 3,
    retry_on_status: bool = True,
    on_throttled: Optional[Callable[[], None]] = None,
    **kwargs,
) -> Any:
    """节流 + 重试地发一次请求，返回响应对象。

    session_provider: 返回 session 的无参函数（便于测试注入 mock，也便于
                      限流后重建会话/换指纹）。
    on_throttled:     命中限流时回调（用于切换指纹 / 重建会话）。
    返回最后一次的响应；全部重试失败返回 None。
    """
    clean_params = None
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None}

    attempts = max(1, int(max_retries))
    last_resp = None
    last_error: Optional[Exception] = None

    for attempt in range(attempts):
        throttle.acquire()
        try:
            session = session_provider()
            merged = dict(headers) if headers else None
            if merged:
                # 逐请求 headers 覆盖会话默认值（如不同接口的 Referer）
                session.headers.update(merged)
            resp = getattr(session, method)(
                url, params=clean_params, timeout=timeout, **kwargs
            )
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            last_error = exc
            logger.debug("%s %s failed (attempt %d/%d): %s",
                         method.upper(), url, attempt + 1, attempts, exc)
            if attempt < attempts - 1:
                time.sleep(backoff_seconds(attempt))
            continue

        last_resp = resp
        verdict = classify_response(resp)

        # 只有限流 / 服务端故障值得重试；ok / not_found / unknown 立即返回
        if not is_retryable(verdict) or not retry_on_status:
            return resp

        # 限流 → 通知调用方换指纹/换会话，退避后重试
        if verdict == "throttled":
            logger.warning("%s %s throttled (attempt %d/%d)",
                           method.upper(), url, attempt + 1, attempts)
            if on_throttled is not None:
                try:
                    on_throttled()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("on_throttled callback failed: %s", exc)

        if attempt < attempts - 1:
            time.sleep(backoff_seconds(attempt))

    if last_resp is not None:
        return last_resp
    if last_error is not None:
        logger.debug("%s %s exhausted retries: %s", method.upper(), url, last_error)
    return None


def get_text(
    session_provider: Callable[[], Any],
    throttle: Throttle,
    url: str,
    *,
    encoding: str = "utf-8",
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 10,
    max_retries: int = 3,
) -> Optional[str]:
    """取文本响应并按指定编码解码（腾讯接口是 GBK）。"""
    raw = get_bytes(
        session_provider, throttle, url,
        params=params, headers=headers, timeout=timeout, max_retries=max_retries,
    )
    if raw is None:
        return None
    try:
        return raw.decode(encoding, errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("decode %s as %s failed: %s", url, encoding, exc)
        return None


def get_bytes(
    session_provider: Callable[[], Any],
    throttle: Throttle,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 10,
    max_retries: int = 3,
) -> Optional[bytes]:
    """取原始字节响应；失败或非 200 返回 None。"""
    resp = request_with_retry(
        session_provider, throttle, "get", url,
        params=params, headers=headers, timeout=timeout, max_retries=max_retries,
    )
    if resp is None:
        return None
    try:
        if getattr(resp, "status_code", None) != 200:
            return None
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.debug("read %s content failed: %s", url, exc)
        return None


def json_of(resp: Any, *, fallback_encoding: Optional[str] = None) -> Any:
    """宽松解析 JSON 响应。

    同花顺（getharden / 涨停揭秘）等接口服务端常**不声明 charset**，但内容
    实为 GBK；此时 curl_cffi 默认按 utf-8 解码会抛 UnicodeDecodeError。
    传 fallback_encoding 后先按响应声明解码，失败再按该编码重解。

    Raises:
        ValueError: resp 为 None。
        json.JSONDecodeError / UnicodeDecodeError: 两种编码都解不出来。
    """
    if resp is None:
        raise ValueError("json_of: response is None")
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - 编码猜测失败，走 fallback
        raw = getattr(resp, "content", None)
        if fallback_encoding and isinstance(raw, (bytes, bytearray)) and raw:
            try:
                return json.loads(raw.decode(fallback_encoding, errors="replace"))
            except Exception as exc2:  # noqa: BLE001 - 两种编码都失败
                exc = exc2
        status = getattr(resp, "status_code", "?")
        preview = raw[:120] if isinstance(raw, (bytes, bytearray)) else raw
        raise RuntimeError(
            f"json_of: 响应不是合法 JSON（status={status}, "
            f"body[:120]={preview!r}）"
        ) from exc


# ---------------------------------------------------------------------------
# 按数据源分组的客户端（ClientGroup）
# ---------------------------------------------------------------------------
class ClientGroup:
    """一个数据源 = 一把节流器 + 一个指纹会话 + 一套重试参数。

    为什么按源分组而不是全局共用一把锁？
      各源风控阈值差异巨大（东财 1.0s / 同花顺 0.25s / 问财 0.5s）。
      共用一把节流器会让低风险源被高风险源的间隔拖慢；各自独立才能在
      「东财不被封」的同时不牺牲其它源的吞吐。

    线程安全：会话惰性创建（双重检查锁）；命中限流时自动换指纹重建会话。
    """

    def __init__(
        self,
        name: str,
        min_interval: float,
        jitter_min: float = 0.0,
        jitter_max: float = 0.0,
        max_retries: int = 2,
        impersonate: Optional[str] = TIER2_FINGERPRINT,
        referer: Optional[str] = None,
        extra_headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> None:
        self.name = name
        self.throttle = Throttle(min_interval, jitter_min, jitter_max)
        self.max_retries = int(max_retries)
        self.timeout = float(timeout)
        self._impersonate = impersonate
        self._referer = referer
        self._extra_headers = dict(extra_headers) if extra_headers else None
        self._session: Any = None
        self._fp: Optional[str] = None
        self._lock = threading.Lock()

    # ── 会话 ──────────────────────────────────────────────────────────
    def _pick_fingerprint(self) -> Optional[str]:
        """返回本次要用的指纹；None 表示不伪装（见 NO_IMPERSONATE）。

        首次用配置指纹；被限流后按指纹池顺序换下一个（真正轮换）。
        """
        if self._impersonate == NO_IMPERSONATE:
            return None  # 该站点 WAF 封禁浏览器指纹，不伪装也不轮换
        if self._impersonate in (None, "random"):
            return random.choice(FINGERPRINTS)
        if self._fp is None:
            return self._impersonate
        try:
            idx = FINGERPRINTS.index(self._fp)
        except ValueError:  # 配置了池外指纹（如 "chrome" 别名）→ 无从轮换
            return self._impersonate
        return FINGERPRINTS[(idx + 1) % len(FINGERPRINTS)]

    def _new_session(self) -> Any:
        fp = self._pick_fingerprint()
        self._fp = fp
        # fp=None 在 make_session 里意为「随机取指纹」，故显式传 NO_IMPERSONATE
        spec = NO_IMPERSONATE if fp is None else fp
        return make_session(
            spec, referer=self._referer, extra_headers=self._extra_headers
        )

    def session(self) -> Any:
        """取当前会话（惰性创建、线程安全）。"""
        with self._lock:
            if self._session is None:
                self._session = self._new_session()
            return self._session

    def rotate_session(self) -> None:
        """换指纹重建会话（限流回调：同一指纹被盯上时换一个）。"""
        with self._lock:
            old, self._session = self._session, None
        _close_quietly(old)
        with self._lock:
            if self._session is None:
                self._session = self._new_session()

    def reset_session(self) -> None:
        """丢弃当前会话（下次请求重建）。"""
        with self._lock:
            old, self._session = self._session, None
        _close_quietly(old)

    # ── 请求 ──────────────────────────────────────────────────────────
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_on_status: bool = True,
        **kwargs,
    ) -> Any:
        """经节流 + 重试发请求；重试耗尽返回 None。"""
        return request_with_retry(
            self.session,
            self.throttle,
            method,
            url,
            params=params,
            headers=headers,
            timeout=self.timeout if timeout is None else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
            retry_on_status=retry_on_status,
            on_throttled=self.rotate_session,
            **kwargs,
        )

    def get(self, url: str, **kwargs) -> Any:
        return self.request("get", url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        return self.request("post", url, **kwargs)

    def get_text(
        self,
        url: str,
        *,
        encoding: str = "utf-8",
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[str]:
        return get_text(
            self.session, self.throttle, url,
            encoding=encoding, params=params, headers=headers,
            timeout=self.timeout if timeout is None else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
        )

    def get_bytes(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[bytes]:
        return get_bytes(
            self.session, self.throttle, url,
            params=params, headers=headers,
            timeout=self.timeout if timeout is None else timeout,
            max_retries=self.max_retries if max_retries is None else max_retries,
        )


def _close_quietly(session: Any) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception as exc:  # noqa: BLE001 - 关闭失败不该影响主流程
        logger.debug("close session failed: %s", exc)


# ---------------------------------------------------------------------------
# 具名客户端注册表 —— astock_signals 与 tradex 两包共用同一实例
# ---------------------------------------------------------------------------
_PRESETS: dict[str, dict] = {
    "ths": dict(
        min_interval=THS_MIN_INTERVAL, jitter_min=THS_JITTER_MIN,
        jitter_max=THS_JITTER_MAX, max_retries=THS_MAX_RETRY,
        impersonate=TIER2_FINGERPRINT, referer="https://www.10jqka.com.cn/",
    ),
    "cls": dict(
        min_interval=CLS_MIN_INTERVAL, jitter_min=0.05, jitter_max=0.20,
        max_retries=2, impersonate=TIER2_FINGERPRINT,
        referer="https://www.cls.cn/",
    ),
    "cninfo": dict(
        min_interval=CNINFO_MIN_INTERVAL, jitter_min=0.05, jitter_max=0.20,
        max_retries=2,
        # 巨潮 WAF 封禁常见浏览器指纹（chrome120/124 实测 403），
        # 必须关闭指纹伪装 —— 节流与重试照旧生效。
        impersonate=NO_IMPERSONATE,
        referer="http://www.cninfo.com.cn/",
    ),
    "sina": dict(
        min_interval=SINA_MIN_INTERVAL, jitter_min=0.05, jitter_max=0.15,
        max_retries=2, impersonate=TIER2_FINGERPRINT,
        referer="https://finance.sina.com.cn/",
    ),
    "wencai": dict(
        min_interval=WENCAI_MIN_INTERVAL, jitter_min=0.10, jitter_max=0.30,
        max_retries=WENCAI_MAX_RETRY, impersonate=TIER2_FINGERPRINT,
    ),
}

_CLIENTS: dict[str, ClientGroup] = {}
_CLIENTS_LOCK = threading.Lock()


def get_client(name: str) -> ClientGroup:
    """按名取用共享客户端（进程内单例）。

    Raises:
        KeyError: 名字未注册。
    """
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(name)
        if client is None:
            preset = _PRESETS.get(name)
            if preset is None:
                raise KeyError(
                    f"未注册的数据源客户端: {name!r}（可选 {sorted(_PRESETS)}）"
                )
            client = ClientGroup(name=name, **preset)
            _CLIENTS[name] = client
        return client


def registered_clients() -> list[str]:
    """已注册的客户端名（供自检 / 测试断言）。"""
    return sorted(_PRESETS)


def reset_clients() -> None:
    """关闭并清空所有客户端（换 IP / 测试隔离用）。"""
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for c in clients:
        c.reset_session()
