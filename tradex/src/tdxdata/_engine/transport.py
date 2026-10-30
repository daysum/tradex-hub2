# -*- coding: utf-8 -*-
r"""ARCH-001：Transport 传输协议（报告 §6.4 步骤 1）。

为什么要把「传输」显式化成接口
------------------------------
改造前库里有**两条**互不相通的传输路径，各自的收发与错误口径各写一份：

=================  ================================  ==============================
路径               实现                              回包形态
=================  ================================  ==============================
数据中心 JSON      `Session.ask`                     `bytes`（多包 `b"".join` **拍平**）
PB / K 线          `datacenter._send`                `list[bytes]`（已 `unwrap`）
=================  ================================  ==============================

「多包被 join 拍平」意味着 JSON 路径一旦出现真正的多包响应（历史样本
`n_blobs` 恒为 1，多包路径从未真跑），包边界信息就丢了。本模块统一成
**一个协议、一种返回形态**：

* `Transport.call(cmd, ctl, body, *, idle) -> list[bytes]` —— **返回包列表**，
  绝不在传输层 join；
* 异常契约：**只允许**抛 `errors.ConnectionFailed`（含子类 `Timeout` /
  `RateLimited`）与 `errors.ProtocolError` 四类（批次 A 已把错误归一化下沉，
  这里把契约显式化到接口层）。裸 `OSError` / `socket.timeout` 一律不许冒泡
  —— 用户的 `except tdxdata.TdxError` 必须接得住。

三条实现（适配器模式，库内既有实现原样包进来，不重写收发逻辑）：

* `SessionTransport` —— 数据中心 JSON 通路（包 `Session.ask_blobs`）
* `PBTransport` —— PB / K 线通路（包 `datacenter._send`）
* `ReplayTransport` —— 离线回放（实现放在测试侧 `tests/transport_replay.py`，
  库内不背回放职责）

实例化进度（_CONN 过渡第一步）
------------------------------
`datacenter._CONN` 仍是模块级单例（dict 形状未变，既有注入缝全部保留），
但已可显式构造 `PBChannel` 实例并 `dc._send(..., conn=ch)` 指定通道 ——
与 `client.py` 联动的大改（每实例一条通道）后置，这里只留缝。
"""
from __future__ import annotations

import socket
import typing

from .. import errors

__all__ = ["Transport", "SessionTransport", "PBTransport"]


# ── 协议 ─────────────────────────────────────────────────────────────────
@typing.runtime_checkable
class Transport(typing.Protocol):
    """传输协议：发一帧、收齐回包，**返回包体列表**。

    契约
    ----
    1. 返回 `list[bytes]`：每个元素是一个已 `unwrap` 的包体。**不许 join** ——
       包边界是调用方（分帧/解析层）的输入，拍平了就再也分不回去。
    2. `cmd` 是 ASCII 命令名（如 `"HQServ.PBFXT"` / `"CWServ.ph_..."`）；
       `ctl` 是命令字；`body` 是**已成形**的请求体字节（信封/Params 包装
       由更上层决定，传输层不做体形态判断）。
    3. `idle`：首包等待秒数（各路径的稳态实测值不同，JSON 4.0 / PB 2.0）。
    4. 异常**只允许**四类：`errors.ConnectionFailed`、`errors.Timeout`、
       `errors.RateLimited`、`errors.ProtocolError`。空回包 = 连接被掐
       （诊断实测 10053），按 `ConnectionFailed` 上抛，**绝不返回 `[]`**
       —— 协议里"无数据"是"合法但 0 行的表"，两者混在一起调用方分不清。
    """

    def call(self, cmd: str, ctl: int, body: bytes, *,
             idle: float = 4.0) -> list[bytes]:
        ...


# ── 异常归一化（契约的实现侧）────────────────────────────────────────────
_WINERROR_LIMITED = (10053, 10054)


def _normalize(exc: BaseException) -> errors.TdxError | None:
    """把 socket 域异常归一化成传输层四类；**非传输类返回 `None`（原样冒泡）**。

    归类依据（与 `datacenter._send` / `client` 的限流判据同口径）：
      * `socket.timeout` / `TimeoutError`           → `errors.Timeout`
      * `ConnectionResetError` / `ConnectionAbortedError`
        （winerror 10053/10054，被服务端踢）        → `errors.RateLimited`
      * 其余 `ConnectionError` / `OSError`          → `errors.ConnectionFailed`
      * 已是四类之一                                 → 原样返回
    """
    if isinstance(exc, (errors.ConnectionFailed, errors.ProtocolError)):
        return exc                       # ConnectionFailed/Timeout/RateLimited 都在内
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return errors.Timeout("传输超时：%s: %s" % (type(exc).__name__, exc))
    limited = isinstance(exc, (ConnectionResetError, ConnectionAbortedError)) \
        or getattr(exc, "winerror", None) in _WINERROR_LIMITED
    if limited:
        return errors.RateLimited("连接被服务端重置/中止（疑似限流）：%s: %s"
                                  % (type(exc).__name__, exc))
    if isinstance(exc, (ConnectionError, OSError)):
        return errors.ConnectionFailed("传输失败：%s: %s"
                                       % (type(exc).__name__, exc))
    return None


# ── 适配器 1：数据中心 JSON 通路 ─────────────────────────────────────────
class SessionTransport:
    """把 `Session` 包成 `Transport`（数据中心 JSON 通路）。

    为什么是**包装**而不是把 `Session` 改名
    --------------------------------------
    `Session` 的建连/握手/换机/钉机语义有大量离线判据钉着（test_session_offline_b4
    等），且 `client.py`（本步禁碰）仍直接调 `Session.ask`。所以拍板：
    **Session 保持原样，`ask_blobs` 是新接缝**（返回包列表，不再拍平），
    本类只做「包列表 + 异常归一化」两件事。
    """

    def __init__(self, session):
        self._session = session

    def call(self, cmd: str, ctl: int, body: bytes, *,
             idle: float = 4.0) -> list[bytes]:
        try:
            blobs = self._session.ask_blobs(ctl, cmd, body, idle=idle)
        except (errors.ConnectionFailed, errors.ProtocolError):
            raise
        except BaseException as exc:                          # noqa: BLE001
            norm = _normalize(exc)
            if norm is None:
                raise                     # 非传输类异常不许被传输层吞掉重分类
            raise norm from exc
        if not blobs:
            # 空回包 = 连接被掐（诊断实测 10053），不是"没有数据"。
            # ⚠️ 与 `Session.ask` 返回 `b""` 的历史口径不同：历史口径下
            # `client.call` 靠 `parse(b"")` 的 EMPTY verdict 走重试/NotSupported；
            # Transport 契约层必须如实上抛（client 侧接线改造时由适配层消化）。
            raise errors.ConnectionFailed(
                "%s 回包为空（连接疑似被服务端中止）" % cmd)
        return list(blobs)


# ── 适配器 2：PB / K 线通路 ──────────────────────────────────────────────
class PBTransport:
    """把 `datacenter._send` 包成 `Transport`（PB / K 线通路）。

    `_send` 本来就返回 `list[bytes]` 且已把错误归一化（DEF-006），这里
    只补齐「帧拼装」这半件事（`_raw_send`/`hq_extra._call` 各拼过一份）。
    """

    def __init__(self, host: str | None = None, *, conn=None, wait: float = 15.0):
        self.host = host
        #: 显式指定 `PBChannel` 实例（_CONN 实例化过渡缝；`None` = 默认模块级通道）
        self.conn = conn
        self.wait = wait

    def call(self, cmd: str, ctl: int, body: bytes, *,
             idle: float = 2.0) -> list[bytes]:
        from . import datacenter as dc            # 函数内导入：避免 import 环

        if isinstance(cmd, str):
            cmd = cmd.encode("ascii")
        frame = dc.build_frame(0x7000, ctl, dc.TYPE_QUERY, cmd, body)
        return dc._send(frame, host=self.host, wait=self.wait, idle=idle,
                        conn=self.conn)


# ── 帧工具（回放侧也要用，放库内共享一份事实）────────────────────────────
def frame_ctl_cmd_args(frame: bytes) -> tuple[int, str, bytes]:
    """从 7615 帧字节取 `(ctl, cmd, args)`（与 golden_runner.parse_wire_frame 同口径）。

    帧布局：`0x0B | msgid(LE32) | ctl | len(H) len(H) typ(H) | 0x00 0x64 |
    <cmd ascii> 00 00 <args>`
    """
    if len(frame) < 16:
        raise ValueError("帧太短（%d 字节），不是 7615 帧" % len(frame))
    ctl = frame[5]
    body = frame[14:]
    cut = body.find(b"\x00\x00")
    if cut < 0:
        return ctl, "", b""
    return ctl, body[:cut].decode("ascii", "replace"), body[cut + 2:]
