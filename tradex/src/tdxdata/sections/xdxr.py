# -*- coding: utf-8 -*-
"""分红融资语义层：`get_xdxr(client, symbol) -> Table`。

⚠️ 先读这条（诚实声明）
======================
**7615 通道没有独立的「除权除息」命令。** 本库搜遍 449 条实测命令与 421 个产品，
没有一条返回「每股送转 / 每股派息 / 除权除息日 / 除权除息价」那种标准 xdxr 结构。
所以 `get_xdxr()` 返回的是**分红融资口径**（`CWServ.ph_agf10_fhrz` 的
「6 组 次数 + 金额」统计表），**不是**逐次的除权除息事件表。
字段含义一律以 `tdxdata/sections/mapping.py::FIELD_MAP["fhrz_agf10"]` 为准；
不要把它当成 pytdx 的 `get_xdxr_info` 用。

为什么选 `ph_agf10_fhrz` 而不是 `ph_tdxdatacenter_fhrz`
-----------------------------------------------------
| 命令 | 产品名 | 参数 | 口径 |
|---|---|---|---|
| `CWServ.ph_agf10_fhrz` | `fhrz_agf10` | `["00101", "<6位代码>"]` | **单只股票**的分红/融资累计统计（1 行 × 12 列） |
| `CWServ.ph_tdxdatacenter_fhrz` | `fhrz` | `["2", "1"]` | **全市场最近一期**分红融资榜（没有代码位） |

`get_xdxr(symbol)` 是**按代码**取数，而后者根本没有"代码位"参数，做不了这件事
—— 所以只能用前者。出处：实机验证 `ph_agf10_fhrz`
（`p1=00101` 对沪股与北证都 PASS，说明这条的 p1 不按市场走）+ 域文档 §2.1。

已知限制
-------
* 6 组「次数 + 金额」里**只有第 3 组确认是首发（IPO）**（证据：`600303` 的
  `N006 = 352000000` 与 `gsjj` 的 `N011`「募集资金总额」跨命令逐位相同）。
  其余 5 组（送股/转增/派现/配股/增发/可转债 的哪一类）文档明确标「未定论」，
  因此**不映射**，只留在 `raw` 里（`F10与个股资料.md` §八 #9）。
* 金额列是 4 位小数字符串（`480187293.0000`）；没有次数的组金额一定是 `null`。
"""
from __future__ import annotations

from .. import symbols
from ..models import Table
from . import mapping

__all__ = ["get_xdxr"]

# 出处: products.json「fhrz_agf10」= {"cmd": "CWServ.ph_agf10_fhrz", "ctl": 22,
#       "params": ["00101", "600303"]}；域文档 §七之二 同参数形态。
CMD = "CWServ.ph_agf10_fhrz"
CTL = 0x16
SUB = "00101"


def get_xdxr(client, symbol) -> Table:
    """取一只股票的分红融资统计（**不是**除权除息事件表，见模块 docstring）。

    参数
    ----
    client : Client
        只要求有 `call(cmd, ctl, params) -> Table`。
    symbol : str | tuple
        `"600303"` / `"sh600303"` / `(1, "600303")`。

    返回
    ----
    `Table`，`cols == ["ipo_count", "ipo_proceeds_yuan"]`（不含 `"raw"`，原始 12 列在每行的 `row["raw"]` 里）——
    其余 10 列口径未定论，只留在 `raw` 里（键名与服务端一致：`N001`…`N012`）。

    异常
    ----
    EmptyResult
        当期无数据（原样上抛，不换成空表）。
    NotSupportedError / ParamError / ServerError
        协议层原样上抛。
    """
    _, code = symbols.parse(symbol)
    table = client.call(CMD, CTL, [SUB, code])
    # 与 get_f10(symbol, "fhrz") 同源同表，复用同一份映射，避免两处漂移。
    return mapping.apply(table, "fhrz_agf10")
