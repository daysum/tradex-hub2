# -*- coding: utf-8 -*-
"""异常层次。

设计原则：**让调用方能区分"没数据"和"取不到"**。
这是本项目最容易踩的坑 —— 把两者混成一个 None/空列表，上层会误判成"今天没数据"。
"""
from __future__ import annotations

__all__ = [
    "TdxError", "ConnectionFailed", "Timeout", "ProtocolError",
    "ParamError", "ServerError", "NotSupportedError", "RateLimited",
    "EmptyResult", "NoData", "OutOfScope", "AmbiguousSymbol",
]


class TdxError(Exception):
    """本库所有异常的基类。"""


# ── 传输层 ────────────────────────────────────────────────────────────────
class ConnectionFailed(TdxError):
    """连不上（全部服务器都失败）。"""


class Timeout(ConnectionFailed):
    """读写超时。"""


class RateLimited(ConnectionFailed):
    """被限流/被踢（10053/10054）。调用方应退避后换服务器重试。"""


class ProtocolError(TdxError):
    """帧头非法、zlib 解压失败、响应截断等 —— 协议层异常，通常需要抓包排查。"""


# ── 语义层 ────────────────────────────────────────────────────────────────
class ParamError(TdxError):
    """服务端返回 -1002：参数不对。"""


class ServerError(TdxError):
    """服务端返回 -1005 / -1009：命令执行失败。"""


class NotSupportedError(TdxError):
    """这条命令/能力**根本不可用**（-1000 协议不存在，或本库未实现）。

    典型场景：按代码取五档
    （协议无 bid/ask 字段，且 `PBMultiHQ` 参数里无代码位 —— 2026-10-25 解构确认）。
    **不要**用 `EmptyResult` 代替它 —— 否则调用方会以为是"当期无数据"。
    """


class EmptyResult(TdxError):
    """命令可用，但**这一次**没数据（`EMPTY_RS 0×N` / `EMPTY 0×0`）。

    区分三态见 `docs/VERIFICATION.md`：
      · `EMPTY_RS 0×N`（有列无行）= 参数对、当期无数据 → 本异常
      · `EMPTY 0×0`（无列无行）= 通道没通 → `NotSupportedError`
      · `ERR_-1002` = 参数错 → `ParamError`
    """


class NoData(EmptyResult):
    """`EmptyResult` 的别名，语义更直白。"""


# ── 标的范围（阶段 3 新增）────────────────────────────────────────────────
class OutOfScope(TdxError):
    """**这个品种不在本数据源的覆盖范围**（不是"接口做不了这件事"）。

    与 `NotSupportedError` 的分工（两者都让人"取不到"，但**下一步动作完全不同**）：

    | 异常 | 意思 | 用户该做什么 |
    |---|---|---|
    | `NotSupportedError` | 通道/命令/能力**做不了这件事**（协议无字段、本库未实现） | 换接口 |
    | `OutOfScope` | 这只标的**根本不在本数据源的证券表里** | 换数据源，或确认代码写错 |

    两个已实证的现场：
      * 港股 `00700` —— 5 位数字。若"左补零成 6 位"就会**静默命中另一只 A 股**
        （`00700 → 000700 模塑科技`，`get_bars` 返回的 K 线与 `000700` 逐条相同），
        所以宁可拒绝也不许补零。
      * 股指期货 `IF2612` / 国债期货 `T00` —— 形态上认得出，但**本数据源的证券表里没有**。
        ⚠️ 措辞纪律：**不许**说成"服务端不支持这个市场码"。已实证 `PBFXT` 的 `field3`
        对已知代码是**宽松的**（`600519` 在 `field3=1/99/30` 三种下返回**逐位相同**的 K 线），
        所以取不到的正确归因是"**代码不在证券表里**"，不是"市场码不支持"。
    """


class AmbiguousSymbol(TdxError):
    """**两义代码**，且调用方要求严格（`Client(strict_symbols=True)`）。

    默认**不抛**这个异常，只 `warnings.warn` —— 因为"两义"并不等于"错"，
    `000001` 确实是平安银行（深市）；易错的是把它当成上证指数（那是 `999999`）。
    所以默认照旧解析 + 提醒；要求确定性的调用方（批处理、入库）再开严格模式。
    """
