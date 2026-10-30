# -*- coding: utf-8 -*-
"""连接会话：建连 + 握手 + 拼帧 + 收发 + 结果解析（库内私有）。

与"直接调 `datacenter.call`"的区别
----------------------------------
`datacenter.call()` 只负责把字节发出去、把回包收回来。
**体形态**（要不要包 `{"Params":…}`）和**结果判定**
（PASS / EMPTY_RS / EMPTY / ERR_-1002 …）必须由这一层统一决定，
否则每个调用点都要重复判断 —— 这正是历史上出错最多的地方。

体形态规则（`spec/11 §〇`，2026-10-24 定论）
-------------------------------------------
1. dict 里已带 `CallName` 或 `Params` 键 → **完整请求体，直接发**，不再包信封
2. `HQServ.*` → 裸 JSON（**不包 Params**）
3. 其余（`CWServ.*` / `JJSServ.*` / `DataAggregation.*`）→ 包 `{"Params": obj}`
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

from . import datacenter as dc
from .._logging import logger

__all__ = ["Session", "body_of", "parse", "HOSTS", "PORT"]
PORT = 7615
HOSTS = ["121.37.180.65", "124.223.177.105", "60.204.149.166", "121.5.164.43"]

# 空闲多久后主动重连（实测：空闲 60s 仍可用，180s 被服务端重置 → 取 90s）
IDLE_RECONNECT = 90.0


# ── 体形态 ────────────────────────────────────────────────────────────────
def body_of(cmd: str, params) -> bytes:
    """按命令前缀/参数形态决定请求体字节。"""
    obj = json.loads(params) if isinstance(params, str) else params
    if isinstance(obj, dict) and ("CallName" in obj or "Params" in obj):
        return json.dumps(obj, ensure_ascii=False).encode()
    if cmd.startswith("HQServ"):
        return json.dumps(obj, ensure_ascii=False).encode()
    return json.dumps({"Params": obj}, ensure_ascii=False).encode()


# ── 结果解析 ──────────────────────────────────────────────────────────────
def parse(payload: bytes):
    """解回包，返回 `(verdict, n_rows, n_cols, cols, rows, primary)`。

    `verdict` 取值：
        `PASS`          有列有行
        `EMPTY_RS`      列结构在、但 0 行（当期无数据）
        `EMPTY`         空包（通道不通 / 命令无响应）
        `ERR_-1002`     参数错       `ERR_-1005` 服务端执行失败
        `ERR_-1000`     命令不存在   `RAW` 非 JSON（需按 protobuf 处理）
        `BADJSON`       是合法 JSON、但**不是 `ResultSets` 协议形态**
                        （顶层是数组/标量、`ResultSets` 不是列表、行不是列表…）

    ⚠️ **本函数永不抛异常**，所有异常形态都必须落到上面 7 个 verdict 之一。
    历史 bug（2026-10）：
    * 顶层是 JSON 数组 → 直接 `.get("ResultSets")` → 裸
      `AttributeError: 'list' object has no attribute 'get'`，穿透到用户
      （`errors.py` 的异常体系接不住），文案还和"标的写错了"毫不相干；
    * `{"ResultSets": "…"}` → 遍历字符串 → `'str' object has no attribute 'get'`。

    `rows` 与 `primary` 的关系（**必须成对使用**）
    ------------------------------------------------
    * `rows`    —— 主表（按单元格数选出的那张）的原始行，每行是**值列表**；
    * `cols`    —— 所有 ResultSet 列名的**并集**（`spec/06 §2.5.7` 的"列数"口径）；
    * `primary` —— **主表自己的列名**，长度才和 `rows` 每一行对得上。

    并集通常比主表宽（例：`ph_agf10_cw_zcfzb` 是 82 vs 81），所以
    **`zip(cols, row)` 会把整行错一位**（静默错列，最坏的一类 bug）。
    要按名取值请一律用 `primary`；`cols` 只用来报"这张表有多少列"。
    """
    if not payload:
        return "EMPTY", 0, 0, [], None, []
    try:
        j = json.loads(payload.decode("gbk", "replace"))
    except Exception:                                        # noqa: BLE001
        return "RAW", 0, 0, [], None, []

    if not isinstance(j, dict):
        # 合法 JSON 但顶层不是对象。空数组按"这一段没数据"，其余算协议形态坏。
        if isinstance(j, list) and not j:
            return "EMPTY_RS", 0, 0, [], None, []
        return "BADJSON", 0, 0, [], None, []

    if j.get("ErrorCode") not in (0, None):
        return "ERR_%s" % j.get("ErrorCode"), 0, 0, [], j.get("ErrorInfo"), []

    rss = j.get("ResultSets") or []
    if not isinstance(rss, list):
        return "BADJSON", 0, 0, [], None, []
    if not all(isinstance(rs, dict) for rs in rss):
        return "BADJSON", 0, 0, [], None, []
    best = None
    for rs in rss:
        names = dc._colnames(rs.get("ColName")) or dc._colnames(rs.get("ColDes")) or []
        rows = [x for x in (rs.get("Content") or []) if isinstance(x, list)]
        key = (len(rows) * max(len(names), 1), len(rows), len(names))
        if best is None or key > best[0]:
            best = (key, rows, names)
    if not best:
        return "EMPTY_RS", 0, 0, [], None, []

    _, rows, names = best
    union = []                       # 列数按 spec/06 §2.5.7 的「并集」口径
    for rs in rss:
        for n in (dc._colnames(rs.get("ColName")) or []) + (dc._colnames(rs.get("ColDes")) or []):
            if n not in union:
                union.append(n)
    cols = union or names
    return ("PASS" if rows else "EMPTY_RS"), len(rows), len(cols), cols, rows, names


def _complete(payload: bytes) -> bool:
    """载荷是否已完整（能整体 JSON 解析）。空包/半截都算未完成。"""
    if not payload:
        return False
    s = payload.strip()
    if s[:1] not in (b"{", b"["):
        return False
    try:
        json.loads(payload.decode("gbk", "replace"))
        return True
    except Exception:                                        # noqa: BLE001
        return False


# ── 会话 ──────────────────────────────────────────────────────────────────
class Session:
    """一条 7615 长连接（自动握手、自动换服务器重连）。

    `pin`（keyword-only，**默认 False = 可轮换**）
    --------------------------------------------------------
    `pin=True` 表示这条连接**钉死**在 `host` 上：建连失败不轮换、被踢也不换机
    （`switch_host()` 退化成"同机重连"）。

    ⚠️ 它曾经是给 `Client(concurrency>1)` 的 lane 用的（"第 i 条 lane 钉
    HOSTS[i]"）。并发特性已于 2026-10 删除，但 `pin` 作为 **Session 层能力**
    保留 —— 手工排查、抓包复现时"钉住某台服务器"同样有用。

    ⚠️ 默认 `False` 时**每一行代码路径都和改造前一致**（照样跳过冷却中的服务器、
    照样轮换、照样在被踢时 `note_limited()`）。
    """

    def __init__(self, host: str | None = None, timeout: float = 15.0, *,
                 pin: bool = False):
        self.host = host or HOSTS[0]
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._idx = 0
        self._last_io = 0.0            # 上次收发时刻（用于空闲判断）
        self._lock = threading.RLock()  # 一条 socket 不能被两个线程同时写
        self.idle_reconnect = IDLE_RECONNECT
        #: 是否钉死在本机（见类文档）。`False` = 旧行为。
        self.pin = bool(pin)

    # -- 生命周期 --------------------------------------------------------
    def connect(self, tries: int = 2) -> "Session":
        """建连 + 握手。任一台失败自动换下一台（跳过冷却中的服务器）。

        `tries`：每台服务器最多试几次（握手期被踢很常见，重试通常就好）。
        """
        with self._lock:                      # RLock：可从 _ask_locked 里重入
            return self._connect_locked(tries)

    def _connect_locked(self, tries: int = 2) -> "Session":
        from .pacer import get_pacer

        pacer = get_pacer()
        last: Exception | None = None
        if self.pin:
            # 钉死的连接：**只**连这一台。偷偷换机 = "第 i 条 lane 钉 HOSTS[i]"
            # 这句话失效，per-host 槽位就和实际流量对不上了。
            order = [self.host]
        else:
            order = list(HOSTS)
            # 从当前 host 开始，按顺序轮；跳过冷却中的
            if self.host in order:
                i = order.index(self.host)
                order = order[i:] + order[:i]

        for host in order:
            if not self.pin and not pacer.ready(host):
                continue
            for attempt in range(tries):
                try:
                    self.sock = socket.create_connection((host, PORT),
                                                         timeout=self.timeout)
                    self.sock.settimeout(self.timeout)
                    self.sock.sendall(dc.fresh_handshake(0x7000))
                    time.sleep(1.3)             # 少了这句 → 服务端直接 10054
                    _n, dead = self._drain()
                    if dead or _n <= 0:
                        # DEF-B4-01：握手回包**从不校验内容** —— 0 字节回包
                        # （timeout 分支 got=0, dead=False）与正常回包一样被
                        # 当成"建连成功"，上层不知道连接从未真正建好，表现为
                        # 下一条命令必然失败再重连（实测多建一条连接）。
                        # 修复：0 字节 / 对端关闭 → 视同握手失败，走既有重连
                        # /换机路径。只做"非空"这一最小校验，不动模板内容
                        # （指纹/票据外置是 ENG-009 的范围）。
                        raise ConnectionResetError(10054, "握手回包为空（0 字节/对端关闭）")
                    self.host = host
                    self._last_io = time.time()
                    return self
                except OSError as e:            # 含 socket.timeout / 10054 / 10053
                    last = e
                    self._kill()
                    limited = isinstance(e, (ConnectionResetError,
                                             ConnectionAbortedError)) \
                        or getattr(e, "winerror", None) in (10053, 10054)
                    if limited:
                        # DEF-030（B5）：per-host 槽位已删，Pacer 只剩全局
                        # 槽位；pin 与非 pin 的退避反馈等价（都是推全局）。
                        pacer.note_limited()
                        pacer.block(host)
                        break                   # 这台被踢了，直接换下一台
                    time.sleep(0.6 * (attempt + 1))
        raise ConnectionError("7615 建连失败（全部服务器不可用）：%s" % last)

    def _kill(self) -> None:
        """不抛异常地丢弃当前 socket。"""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError as exc:
                logger.debug("socket close in _kill failed (%s)",
                             type(exc).__name__, exc_info=True)
            self.sock = None

    def _drain(self, quiet: float = 0.5) -> tuple[int, bool]:
        """排空握手回包：读到「安静 quiet 秒」为止（回包可能分多段）。

        返回 `(收到字节数, 是否被重置)`。
        **被重置必须让上层知道** —— 吞掉会导致"以为连上了、其实连接已死"，
        表现为下一条命令必然失败再重连（实测会多建一条连接）。
        """
        assert self.sock is not None
        self.sock.settimeout(quiet)
        got, dead = 0, False
        try:
            while True:
                b = self.sock.recv(65536)
                if not b:
                    dead = True
                    break
                got += len(b)
        except socket.timeout:
            # 「安静 quiet 秒」就是排空的**正常终止条件**（不是错误）——
            # 收窄保留：这里打 debug 反而制造每帧噪声。
            pass
        except OSError:
            dead = True
        finally:
            if self.sock is not None:
                try:
                    self.sock.settimeout(self.timeout)
                except OSError as exc:
                    logger.debug("restore timeout failed (%s)",
                                 type(exc).__name__, exc_info=True)
        return got, dead

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError as exc:
                logger.debug("socket close failed (%s)",
                             type(exc).__name__, exc_info=True)
            self.sock = None

    @property
    def alive(self) -> bool:
        return self.sock is not None

    # -- 收发 ------------------------------------------------------------
    def _frame(self, ctl: int, cmd: str, args: bytes) -> bytes:
        body = cmd.encode() + b"\x00\x00" + args
        return (struct.pack("<BIBHHH", 0x0B, 0x7000, ctl,
                            len(args), len(args), 0x264B)
                + b"\x00\x64" + body)

    def ask(self, ctl: int, cmd: str, args: bytes,
            idle: float = 4.0, tail: float = 0.35) -> bytes:
        with self._lock:                    # 串行化：同一 socket 不能并发写
            return self._ask_locked(ctl, cmd, args, idle, tail)

    def ask_blobs(self, ctl: int, cmd: str, args: bytes,
                  idle: float = 4.0, tail: float = 0.35) -> list[bytes]:
        """ARCH-001 新接缝：与 `ask` 完全同一路径，但**返回包体列表**（不 join）。

        为什么加这个而不是改 `ask` 的返回值
        ----------------------------------
        `ask -> bytes` 的形状被既有判据钉着（`client.call` 的 `parse(raw)`、
        golden_runner 的 ask 拦截点、test_session_offline_b4 全部 ask 断言），
        而 `client.py` 本步禁碰。所以拍板：**`ask` 原样保留（拍平口径）**，
        `ask_blobs` 是新接缝 —— 收发逻辑只此一份（`_ask_locked` 里多留一份
        未拍平的包列表），两条出口共享，杜绝"两份读循环漂移"。
        `Transport` 契约（`transport.SessionTransport`）走的就是这条。
        """
        with self._lock:
            _joined, blobs = self._ask_locked(ctl, cmd, args, idle, tail,
                                              keep_blobs=True)
            return list(blobs)

    def _ask_locked(self, ctl: int, cmd: str, args: bytes,
                    idle: float, tail: float, *, keep_blobs: bool = False):
        """发一条命令，读回载荷（已 unwrap，未解析 JSON）。

        读取策略（两段式，为吞吐而设计）
        --------------------------------
        1. **主段**：一直读到「载荷能完整解析」为止（不再无脑等 `idle` 秒空闲）
        2. **尾段**：拿到完整载荷后再多等 `tail` 秒，收走可能追加的后续包

        旧实现是「读到安静 `idle` 秒」，一条命令固定多花 ~4 秒 ——
        100 条就要多等 6 分钟，纯浪费。命令总数不变时响应都是秒级，主段通常 <0.3s 就齐了。

        ARCH-001：`keep_blobs=True` 时返回 `(joined, blobs_list)` ——
        `ask_blobs` 用来拿**未拍平**的包列表；默认返回 `joined`（旧行为）。
        """
        # 空闲太久就先重连（服务端会静默重置空闲连接：实测 60s 可用、180s 已死）
        if self.sock is not None and self.idle_reconnect > 0:
            if time.time() - self._last_io > self.idle_reconnect:
                self.reconnect()

        if self.sock is None:
            self.connect()
        try:
            self.sock.sendall(self._frame(ctl, cmd, args))
        except OSError:
            # 连接是"半死"的（服务端已重置但本地还不知道）→ 同机重连后重发一次
            self.reconnect()
            self.sock.sendall(self._frame(ctl, cmd, args))

        blobs: list[bytes] = []
        deadline = time.time() + idle
        got_complete = False

        # ① 主段：读到能解析为止
        while time.time() < deadline:
            try:
                b = self.sock.recv(262144)
            except socket.timeout:
                break
            if not b:
                break
            blobs.append(b)
            if _complete(b"".join(dc.unwrap(b"".join(blobs)))):
                got_complete = True
                break

        # ② 尾段：已完整则再收一小会儿（防多包分段）
        if got_complete and tail > 0:
            self.sock.settimeout(tail)
            t_end = time.time() + tail
            try:
                while time.time() < t_end:
                    b = self.sock.recv(262144)
                    if not b:
                        break
                    blobs.append(b)
            except socket.timeout:
                # 流式读「读到安静」的正常终止条件（不是错误）——
                # 收窄保留：打 debug 会制造每帧噪声。
                pass
            finally:
                try:
                    self.sock.settimeout(self.timeout)
                except OSError as exc:
                    logger.debug("restore timeout failed (%s)",
                                 type(exc).__name__, exc_info=True)

        self._last_io = time.time()
        unwrapped: list[bytes] = dc.unwrap(b"".join(blobs)) if blobs else []
        joined = b"".join(unwrapped)
        if keep_blobs:
            return joined, unwrapped       # ARCH-001：包边界保留给 ask_blobs
        return joined

    def reconnect(self, tries: int = 2) -> "Session":
        """**同机**重连（不换服务器）。

        适用场景：空闲超时被重置、半死连接。
        与限流无关，所以不进入服务器冷却表 —— 换机反而会丢掉一个好节点。
        """
        with self._lock:
            return self._reconnect_locked(tries)

    def _reconnect_locked(self, tries: int = 2) -> "Session":
        host = self.host
        self._kill()
        for attempt in range(tries):
            try:
                self.sock = socket.create_connection((host, PORT), timeout=self.timeout)
                self.sock.settimeout(self.timeout)
                self.sock.sendall(dc.fresh_handshake(0x7000))
                time.sleep(1.3)
                _n, dead = self._drain()
                if dead:
                    raise ConnectionResetError(10054, "握手期被重置")
                self.host = host
                self._last_io = time.time()
                return self
            except OSError:
                self._kill()
                time.sleep(0.5 * (attempt + 1))
        if self.pin:
            # 钉死的连接**不许**退化成换机：换机就破坏了 lane 的主机绑定，
            # 而且会让 per-host 槽位记在错的机器上。连不上就如实报。
            raise ConnectionError("7615 同机重连失败（pin 住 %s，不换机）" % host)
        # 同机实在连不上，退化为换机
        return self.switch_host()

    def switch_host(self, cooldown: float | None = None) -> None:
        """断开并换一台**不在冷却中**的服务器重连。全都在冷却则等最短路。

        `pin=True` 的连接不换机：退化成"同机重连"（见类文档）。
        """
        with self._lock:
            return self._switch_host_locked(cooldown)

    def _switch_host_locked(self, cooldown: float | None = None) -> None:
        from .pacer import get_pacer

        pacer = get_pacer()
        bad = self.host
        self._kill()
        if cooldown:
            pacer.block(bad, cooldown)

        if self.pin:
            # 钉死：同机重连。**不要**改成 switch_host() —— 会无限递归。
            return self._reconnect_locked()

        for _ in range(len(HOSTS)):
            self._idx += 1
            nxt = HOSTS[self._idx % len(HOSTS)]
            if nxt != bad and pacer.ready(nxt):
                self.host = nxt
                return self.connect()
        # 全在冷却：睡到最早解冻
        time.sleep(min(8.0, pacer.gap))
        self.host = HOSTS[(self._idx + 1) % len(HOSTS)]
        return self.connect()

    def __enter__(self) -> "Session":
        return self.connect() if not self.alive else self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
