# -*- coding: utf-8 -*-
r"""自包含握手模板：把抓到的握手字节内嵌成 base64，脱离 assets 目录依赖。

背景
----
7615 建连必须发一段**真实抓包的握手字节流**（CONNECT + LOGIN + ACL 三帧）。
服务端会校验 ACL/nonce，自己拼裸 CONNECT 帧会被直接断开（回 0 字节）。

原先这段模板存在 `assets/cw_template.bin`（27 KB 抓包原件），由 `tdx_hq.load_handshake()`
截取前 N 字节（握手区）。为了让新工作区只靠 .py 文件就能跑，这里把
**握手区字节**直接内嵌（base64），不再依赖任何二进制资产。

用法
----
    from tdx_handshake import HANDSHAKE
    sock.sendall(HANDSHAKE)
"""
from __future__ import annotations

import base64

# 握手区字节（CONNECT 0x26AC + LOGIN 0x26AD + ACL），来源：2026-10-20 真机抓包模板
_B64 = (
    "C2kAAAAAHgAeAKwmAGTSBAAAAAAAAAAAAAAAAMCoAGgAAAAAAAAlABsHAAALbAAAAABSAFIArSYAZGdQaG9uZQAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAADAqABoAAAAAAAAR1Bob25lX1N0YW5kYXJkACUAGwcAANIEAUtDSG5xMFJ5b240NzVNUkUAAAALbwAAAA+9Ab0B"
    "SyYAZEFDTDpjaGVja3VzZXIAAElYLFNQRUM9MjgzNCxTVFJVQ1Q9QUNMOmNoZWNrdXNlcg0KMSxMb2dpblR5cGV8MixNb2JpbGVN"
    "YWNoaW5lSW5mb3wzLFBUWVBFfDQsUFRPS0VOfDUsT0lEfDYsU2Vzc2lvbnw3LENIQU5ORUx8OCxJbnB1dFpIfDksSW5wdXRaSExC"
    "fDEwLElucHV0UVNJRHwxMSxEQ2hhbm5lbHwNCjk5fDQ7MTkyLjE2OC4wLjEwNDtSZWRtaTs7Ozs7MTQ7MTE3LjE0Ny40NS4yNDMs"
    "MDs7OztHUGhvbmVfU3RhbmRhcmQ7Ny4yNzsyMDI2LTA4LTA2IDE1OjE1IDIuMDIuNzY7QGVjMDp3b2lWc2UzR28vKzdBei9Pa1h1"
    "elFWTWk3SllBekM3bUVCV2dEcTRLVzVLcEc0UEtHME5VdmRtL005alVRU2xHO2Y0ZjY1MmRiZmY0OGFhMTA7Ozs7MTA1NDM7UmVk"
    "bWlfMjIxMjJSSzkzQ18zNF8xNDs7Ozt8M3wtMXw5ZjA2NGRlY2Q2NmFlYjFiMTkwMmViNDZfMjAxMTgxOTEyOTg3MTA3NDk3NXx8"
    "MXx8MHw5OTl8NXwNCgA="
)

HANDSHAKE = base64.b64decode(_B64)


def get():
    """返回握手字节流（每次给新的一份，调用方可以放心改）。"""
    return bytes(HANDSHAKE)


if __name__ == "__main__":
    print("握手字节 %d 字节，前 16B: %s" % (len(HANDSHAKE), HANDSHAKE[:16].hex()))
