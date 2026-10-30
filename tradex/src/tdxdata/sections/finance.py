# -*- coding: utf-8 -*-
"""财报语义层：`get_finance(client, symbol, kind="zcfz") -> Table`。

契约见 `API设计.md` §2.7 P3：返回 `Table`，`cols` 是**标准字段名**，
每行带 `raw`（原始列一个不丢）；`kind` 不认识抛 `ValueError` 并列出全部合法值。

单位口径（写死在字段名里，别再自己换算）
--------------------------------------
| 后缀 | 含义 |
|---|---|
| `_yuan` | 元 |
| `_pct` | 百分数（服务端已是百分数，**不要再 ×100**） |
| `_vol` | **股**（`gsjj.issue_vol` 这类发行数量，实测单位是股） |
| `_wan_yuan` | 万元（`per_capita_hold_amt_wan_yuan`） |

为什么不走 `client.get(产品名, code=...)`
---------------------------------------
产品表里 421 个产品的 `params` 是**模板**，写死的往往是样例代码（如
`["00101","600303"]`），占位符不一定存在。所以这里按模板**按位置**替换：
`@code@` → 目标 6 位代码，`@sub@` → 本 `kind` 的子码，其余位原样照抄。

子码（p1）为什么逐条写死
-----------------------
F10 的 `p1` 是「`00` + 子页 + 子表」的两级编号，**不是市场码**，同一个码在不同
命令里含义完全不同（`F10与个股资料.md` §2.1）。所以只能逐命令抄实测有效值。

已知限制（诚实标注）
------------------
* `kind="cwfxxq"` 对应 `ph_agf10_cw_cwfxxq`，该命令回 **5 个 ResultSet**
  （1×10 / 19×29 / 4×46 / 45×7 / 4×5）。`client.call()` 只保留「最大那张表」
  的行、而 `cols` 是各 RS 的**列名并集**，两张文档记录的「最大表」还互相矛盾
  （`实测样本总表` #3 记 45×7，域文档记 RS1 19×29），列名无从还原。
  因此本 kind 目前会抛 `NotSupportedError`（**不猜列名**），等实网核定后再补映射。
* `kind="zcfz"` 是**资产负债表本体**（`ph_agf10_cw_zcfzb`，23 期 × 81 列），
  不是 `ph_agf10_cw_zcfz`（那是 1×9 的主要指标快照，产品名 `zcfz_all`）。
* 当期无数据时 `client.call()` 抛 `EmptyResult`，本模块**不吞**它。
"""
from __future__ import annotations

from .. import symbols
from ..models import Table
from . import mapping

__all__ = ["KINDS", "get_finance"]

# ── kind → (产品名, 子码) ────────────────────────────────────────────────
# 出处: API设计.md §2.7 P3（9 个 kind）；产品名 = tdxdata/data/products.json 的键
KINDS: dict[str, tuple[str, str | None]] = {
    "zcfz":   ("fin", None),        # 资产负债表全表  ph_agf10_cw_zcfzb（23×81）
    "lyb":    ("lyb", "00101"),     # 利润表全表      ph_agf10_cw_lyb（24×38）
    "xjllb":  ("xjllb", "00101"),   # 现金流量表全表  ph_agf10_cw_xjllb（23×64）
    "zyzb":   ("zyzb", None),       # 主要财务指标    ph_agf10_cw_zyzb（11×67）
    "cwbg":   ("cwbg", "00101"),    # 财报公告列表    ph_agf10_cw_cwbg（14×3）
    "cwfxxq": ("cwfxxq", None),     # 财务分析详情    ph_agf10_cw_cwfxxq（5 个 RS）
    "tjcj":   ("tjcj", None),       # 最新报告期探测  ph_agf10_cw_tjcj（1×2）
    "zysy":   ("zysy", None),       # 财务首页抬头    ph_agf10_cw_sy_v2（1×2）
    "zyzbxq": ("zyzbxq", None),     # 主要指标对比    ph_agf10_cw_zyzbxqy_sy（4 个 RS）
}

# ── 产品名 → (cmd, ctl, 参数模板) ─────────────────────────────────────────
# `@code@` = 6 位股票代码位；`@sub@` = 子码位（由 KINDS 决定）；其余位照抄实测值。
# 出处: tdxdata/data/products.json（cmd/ctl/params 三列原样），
#       ctl 与 `F10与个股资料.md` §三 每条命令标注的 ctl 逐条核对一致。
_PARAMS: dict[str, tuple[str, int, list]] = {
    # 出处: products.json「fin」= ["600303"]；域文档 §三 cw_zcfzb「只有 1 个参数，没有 p1」
    "fin":    ("CWServ.ph_agf10_cw_zcfzb", 0x1A, ["@code@"]),
    # 出处: products.json「lyb」= ["00101","600303"]；域文档 §三「p1=00101、p2=代码」
    "lyb":    ("CWServ.ph_agf10_cw_lyb", 0x18, ["@sub@", "@code@"]),
    # 出处: products.json「xjllb」= ["00101","600303"]；域文档 §三 同结构
    "xjllb":  ("CWServ.ph_agf10_cw_xjllb", 0x1A, ["@sub@", "@code@"]),
    # 出处: products.json「zyzb」= ["920091"]；域文档 §三「没有 p1 子码，只传 6 位代码」
    "zyzb":   ("CWServ.ph_agf10_cw_zyzb", 0x19, ["@code@"]),
    # 出处: products.json「cwbg」= ["00101","600303","0"]；p3 实测无作用
    "cwbg":   ("CWServ.ph_agf10_cw_cwbg", 0x19, ["@sub@", "@code@", "0"]),
    # 出处: products.json「cwfxxq」= ["600303"]；域文档 §三「只有代码，无 p1」
    "cwfxxq": ("CWServ.ph_agf10_cw_cwfxxq", 0x1B, ["@code@"]),
    # 出处: products.json「tjcj」= ["600303"]；域文档 §三「只有代码」
    "tjcj":   ("CWServ.ph_agf10_cw_tjcj", 0x19, ["@code@"]),
    # 出处: products.json「zysy」= ["920091"]；域文档 §三 cw_sy_v2「只有代码」
    "zysy":   ("CWServ.ph_agf10_cw_sy_v2", 0x1A, ["@code@"]),
    # 出处: products.json「zyzbxq」= ["600519"]；域文档 §三「只有代码，无 p1」
    "zyzbxq": ("CWServ.ph_agf10_cw_zyzbxqy_sy", 0x1F, ["@code@"]),
}


def _render(template: list, code: str, sub: str | None) -> list:
    """按位置替换模板里的 `@code@` / `@sub@`，其余元素原样保留。"""
    out = []
    for slot in template:
        if isinstance(slot, str):
            if slot == "@code@":
                out.append(code)
                continue
            if slot == "@sub@":
                if sub is None:
                    raise ValueError("产品 %r 需要子码，但 KINDS 里没给")
                out.append(sub)
                continue
        out.append(slot)
    return out


def get_finance(client, symbol, kind: str = "zcfz") -> Table:
    """取一只股票的财报数据。

    参数
    ----
    client : Client
        只要能提供 `call(cmd, ctl, params) -> Table` 即可（离线测试用假 client 也行）。
    symbol : str | tuple
        `"600519"` / `"sh600519"` / `(1, "600519")` 都行。
    kind : str
        `zcfz`（资产负债表，默认）/ `lyb` / `xjllb` / `zyzb` / `cwbg` /
        `cwfxxq` / `tjcj` / `zysy` / `zyzbxq`。

    返回
    ----
    `Table`，`cols` = 标准字段（**不含 `"raw"`**）；每行另带 `raw` 键（原始列全在里面），故 `len(row) == len(cols) + 1`。
    标准字段随报表而变，见 `tdxdata/sections/mapping.py::FIELD_MAP`。

    异常
    ----
    ValueError
        `kind` 不认识（消息里列出全部合法值）。
    EmptyResult
        命令可用但当期无数据（**原样上抛，不换成空表**）。
    NotSupportedError
        命令可发但列名无法还原（当前 `kind="cwfxxq"`，见模块 docstring）。
    ParamError / ServerError / RateLimited / ConnectionFailed
        协议层原样上抛。

    示例
    ----
    >>> t = get_finance(c, "600303", kind="zcfz")        # doctest: +SKIP
    >>> t.cols[:3]
    ['report_period', 'report_date', 'monetary_fund_yuan']
    >>> t.rows[0]["total_assets_yuan"]
    3104851614.01
    """
    if kind not in KINDS:
        raise ValueError("不认识的 kind：%r；合法取值：%s"
                         % (kind, "、".join(sorted(KINDS))))
    product, sub = KINDS[kind]
    cmd, ctl, template = _PARAMS[product]

    _, code = symbols.parse(symbol)          # 顺带校验代码合法性（非法抛 ValueError）
    params = _render(template, code, sub)

    table = client.call(cmd, ctl, params)     # 限速闸门在 client 里；EmptyResult 原样上抛
    return mapping.apply(table, product)
