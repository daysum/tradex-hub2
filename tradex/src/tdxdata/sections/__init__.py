# -*- coding: utf-8 -*-
"""语义层：把 421 个数据产品的**原始列**归一到标准字段名。

为什么单独一层
--------------
`client.get("fin", ...)` 回来的列是 `N001/N002…`，含义见 `docs/VERIFICATION.md` 的「未定字段」清单。
这一层负责「原始列 → 标准字段」，于是调用方拿到的是
`{"report_date": …, "total_assets": …, "raw": {...}}` 而不是一堆 `N017`。

模块划分
--------
| 模块 | 内容 | `Client` 方法 |
|---|---|---|
| `mapping.py` | 列名映射表 + `apply()` | — |
| `finance.py` | 资产负债表 / 利润表 / 现金流量表… | `get_finance` |
| `f10.py` | F10 个股资料各节 | `get_f10` |
| `xdxr.py` | 分红融资 | `get_xdxr` |
| `boards.py` | 板块列表 / 成分股 | `get_boards` `get_board_members` |
| `flow.py` | 资金流向 / 市场概览 | `get_capital_flow` `get_market_stat` |

约定（见 `API设计.md` §2.7）
--------------------------
* 每个模块暴露 `get_xxx(client, ...) -> Table`，**第一个参数是 `Client`**，
  这样不用反向 import `client`，不会形成循环依赖。
* 返回的 `Table.cols` 是标准字段名，每行带 `raw`（原始列一个不丢）。
* 拿不准含义的列**不给标准名**，只留在 `raw` 里 —— 宁可少映射，不许错映射。
* 没有通道的能力抛 `NotSupportedError`，当期无数据抛 `EmptyResult`，
  **绝不用空 `Table` 冒充"没数据"**。
"""
