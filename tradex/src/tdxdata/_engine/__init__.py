# -*- coding: utf-8 -*-
"""协议引擎（包内私有）：7615 帧编解码、protobuf、行情命令。

   * `datacenter` —— 7615 引擎：握手、拼帧、收发、表格解析
   * `handshake`  —— 握手字节（内嵌 base64）
   * `pb`         —— protobuf 编解码 + 表格还原
   * `pb_api`     —— PB 命令入口
   * `hq_pb`      —— K 线 / 分时 / 批量快照
"""
