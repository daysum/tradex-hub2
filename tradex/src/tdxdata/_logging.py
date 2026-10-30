# -*- coding: utf-8 -*-
r"""库级日志骨架（ENG-006，批次 E）—— **默认零输出，用户显式启用才发声**。

库纪律（本模块存在的理由）
--------------------------
库**不许**自己配 handler、不许动 root logger、不许 print —— 否则会把宿主
应用的日志配置搅乱。标准做法就两行：

    logger = logging.getLogger("tdxdata")
    logger.addHandler(logging.NullHandler())   # 没人配置时：吞掉，零输出

宿主想看库在干什么，自己接：

    import logging
    logging.getLogger("tdxdata").addHandler(logging.StreamHandler())
    logging.getLogger("tdxdata").setLevel(logging.DEBUG)

事件分级
--------
- `debug`：取数管道的内部决策（握手回退、编码探测、清理失败、缓存命中、
  换机、退避、丢行丢格）—— 诊断时才需要，正常运行一个字节都不出。
- `info`：极少用（库没有"值得默认广播"的事件）。
- 本模块**不提供** warning/error 的发射点：异常路径要么抛 typed 异常
  （调用方自己 log），要么按 ENG-005 收窄静默 —— 不存在"又吞又喊"的中间态。

事件清单（ENG-006，8 类，全部 debug 级）
------------------------------------------
| 事件 | 位置 | 消息形态 |
|---|---|---|
| 握手回退 | datacenter._handshake_frame | "handshake cache miss (%s), fallback to built-in frame" |
| zlib 解不开交上层 | datacenter.unwrap | "zlib body failed to decompress, passing raw" |
| 预排空异常 | datacenter._connect_pb | "pre-drain aborted (%s)" |
| 连接清理失败 | datacenter._connect_pb / _close | "socket close failed (%s)" |
| 重试轮次 | datacenter._retry | "probe attempt %d/%d failed (%s)" |
| 统计换算失败 | datacenter 派生 | "stat ratio unavailable (%s)" |
| 编码探测（json 不合法走 protobuf） | pb_api._decode | "payload not json (%s), trying protobuf" |
| zlib 试解失败继续探测 | pb_api._decode | "zlib probe failed (%s), continuing" |
| 属性派生失败跳过 | models.to_dicts | "property %r not derivable (%s)" |
| close_pb 清理失败 | client.close | "close_pb failed (%s)" |
| 连接换机 | session 换机处（如有） | ... |
| 时钟降级 | timeliness（如有） | ... |

（表是**验收口径的登记表**：代码里实际存在的发射点以 grep
`logger.debug` 为准 —— 派单要求"定位不到的列清单说明"。换机/退避/时钟
降级/COL_GUARD 拦截四类在现状代码里没有单点可挂的位置：换机在
`client._raw_send`/`call()` 的 retry 循环里已有 typed 异常路径与
`_pacer.limited_events` 计数（不吞不喊，用户从异常与 rate_stats 可见），
时钟降级在 `timeliness.HybridClock` 已把原因写进 `NowInfo.note`（结构化
可查），COL_GUARD 拦截走 `errors` 体系 —— 这些事件**已有更严格的事实
通道**，再加 debug 日志是重复记录，故不发射。）
"""
from __future__ import annotations

import logging

#: 库级 logger。宿主未配置时 `NullHandler` 吞掉一切 —— 默认零输出。
logger = logging.getLogger("tdxdata")

# 库纪律：不配 handler、不动 root、不许 print。NullHandler 防
# "No handlers could be found" 警告并保证默认零输出。
logger.addHandler(logging.NullHandler())

__all__ = ["logger"]
