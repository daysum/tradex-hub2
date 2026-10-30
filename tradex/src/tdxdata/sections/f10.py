# -*- coding: utf-8 -*-
"""F10 个股资料语义层：`get_f10(client, symbol, section="gsjj") -> Table`。

契约见 `API设计.md` §2.7 P3：返回 `Table`，`cols` 是标准字段名，每行带 `raw`；
`section` 不认识抛 `ValueError` 并列出全部合法值。

22 个 section 的取舍理由
----------------------
只有**列义有文档依据**的才映射标准字段；其余一律只留 `raw`（宁可少映射，不许错映射）：

* `zxts` / `zxts2` / `agf10_zj`：命令回 8~13 个 ResultSet，而 `client.call()`
  只给回「最大那张表」的行、`cols` 是各 RS 的列名并集。这三条命令最大表的
  逐列语义文档没给（`zxts` 的锚点全在 RS1，最大表却是 RS2），所以**不映射**。
  `raw` 里的键就是协议层给的主表列名（并集在 `provenance.extra["cols_union"]`），并与 `mapping._TEMPLATE_COLS` 的文档清单交叉校验（`provenance.extra["template_check"]`）。
* `jysj`：实测 1 行 × 3 列**全是空值**（`[null, null, "0"]`），列义无定论 → 不映射。
* `fhrz`：与 `get_xdxr()` 同源（同一张 `ph_agf10_fhrz` 表）。产品表里另一个
  `fhrz`（`ph_tdxdatacenter_fhrz`，参数 `["2","1"]`）是**全市场最近一期**，
  没有"代码位"，做不了"按股票查"，所以这里用 `ph_agf10_fhrz`。

子码（p1）为什么逐条写死
-----------------------
`p1` 是「`00` + 子页 + 子表」两级编号 / 或英文键（`zhpf`、`ztms`、`rdzt`…），
**不是市场码**，同号在不同命令里含义完全不同（`F10与个股资料.md` §2.1 / §五）。
只能逐命令抄实测有效值 —— 下表每条都注了出处。

已知限制（诚实标注）
------------------
* `gsjj` 产品表模板是 `["00101","<代码>","0"]`，但实测 `00101` 回的是另一张
  **7 行 × 43 列**的表，而文档 §四 的 19 列列义（含「发行价 × 发行数量 =
  募集总额」这条唯一能自证的恒等式）是按 **`00201`** 的 1×19 表建立的。
  这里选 `00201` 以便列义可靠，**待实网逐条复核**。
* `ph_agf10_zxts5` 的 `p3` 必须是 JSON 数字 `0`（不是字符串 `"0"`），
  两种写法实测返回形状不同（3×4 vs 4×5）。
* 当期无数据时 `client.call()` 抛 `EmptyResult`，本模块**不吞**。
"""
from __future__ import annotations

from .. import symbols
from ..models import Table
from . import mapping

__all__ = ["SECTIONS", "get_f10"]

# ── section → (产品名, 子码) ─────────────────────────────────────────────
# 出处: API设计.md §2.7 P3 的 22 个 section；产品名 = products.json 的键
SECTIONS: dict[str, tuple[str, str | None]] = {
    "gsjj":     ("gsjj", "00201"),      # 公司概况 / 发行上市（1×19）
    "jyfx":     ("jyfx", "00101"),      # 经营分析（实网主表 = RS0 1×4：主营简述/经营范围）
    "gbgd":     ("gbgd", "00201"),      # 股东户数 / 人均持股（5~7×3）
    "zbyz":     ("zbyz", "00102"),      # 经营分析 · 指标/变动说明（13×7，p1 必须 00102）
    "rdtc":     ("rdtc", "00103"),      # 热点题材/公司资料文本块（24×3，p1 必须 00103）
    "fhrz":     ("fhrz_agf10", "00101"),   # 分红/融资统计（1×12，按代码）
    "hypm":     ("hypm", "00301"),      # 行业/板块成员（实网 25×4：N003/N004/sc/lx）
    "zxts":     ("zxts", None),         # 综合提示（8 个 RS）
    "zxts2":    ("zxts2", None),        # 综合提示 2（13 个 RS）
    "zxts3":    ("zxts3", "00501"),     # 概念题材命中（RS1 9×5）
    "zxts4":    ("zxts4", None),        # 主营构成（RS0 6×4）
    "zxts5":    ("zxts5", "00101"),     # 股东户数 / 人均持股（实网 8×4，行数随股票变）
    "zg_jby":   ("zgjby", "zhpf"),      # 基本面诊断（1×10）
    "zg_jsm":   ("zgjsm", "jdbxzxt"),   # 近 17 日相对强弱（17×7）
    "zg_scm":   ("zgscm", "ztms"),      # 市场面诊断（1×8，p1 必须 ztms）
    "zg_yq":    ("zgyq", "rdzt"),       # 题材标签 + 业绩预期（RS1 4×11）
    "zg_zs":    ("zgzs", "zhpf"),       # 综合评分卡（1×13）
    "zp":       ("zp", "00104"),        # 个股标签 / 所属概念（15×2）
    "zp_ggld":  ("zpggld", None),       # 个股亮点标签（N 行 × 3 列）
    "sl_v2":    ("sl_v2", "00201"),     # 风险等级（1×3）
    "jysj":     ("jysj", "00101"),      # 交易数据（1×3，实测全空）
    "agf10_zj": ("agf10_zj", None),     # 资金/行业定位（最大表 RS3 = 1×4 全 null）
}

# ── 产品名 → (cmd, ctl, 参数模板) ────────────────────────────────────────
# `@code@` / `@sub@` 由 SECTIONS 决定；其余位照抄实测值（含 "0"/0 的区别）。
# 出处: tdxdata/data/products.json 的 cmd/ctl/params，ctl 与域文档每条命令标注一致。
_PARAMS: dict[str, tuple[str, int, list]] = {
    # 出处: F10与个股资料.md §2.1「gsjj 实测有效 p1 = 00201 → 1×19」+ §四 列义表
    "gsjj":     ("CWServ.ph_agf10_gsjj", 0x16, ["@sub@", "@code@", "0"]),
    # 出处: §四 jyfx「p1=00101 = 经营概览」，且必须 3 位参数（少写第 3 位回 -1005）
    "jyfx":     ("CWServ.ph_agf10_jyfx", 0x16, ["@sub@", "@code@", ""]),
    # 出处: §七之二 gbgd「p1=00201、p3/p4 空、p5 = 数字 0」
    "gbgd":     ("CWServ.ph_agf10_gbgd", 0x16, ["@sub@", "@code@", "", "", 0]),
    # 出处: §四 jyfx_zbyz「p1 要用 00102，用 00101 回空表」
    "zbyz":     ("CWServ.ph_agf10_jyfx_zbyz", 0x1B, ["@sub@", "@code@"]),
    # 出处: §四 rdtc「p1 用 00103 才出数；00101 直接 -1005」
    "rdtc":     ("CWServ.ph_agf10_rdtc", 0x16, ["@sub@", "@code@", "0"]),
    # 出处: §七之二 fhrz「p1=00101（不按市场走，北证同样 PASS），p2=6 位代码」
    "fhrz_agf10": ("CWServ.ph_agf10_fhrz", 0x16, ["@sub@", "@code@"]),
    # 出处: §四 hypm「p1=00301（子页 3），p2 代码，p3 空」
    "hypm":     ("CWServ.ph_agf10_hypm", 0x16, ["@sub@", "@code@", ""]),
    # 出处: §六 zxts「只有代码，无 p1」
    "zxts":     ("CWServ.ph_agf10_zxts", 0x16, ["@code@"]),
    # 出处: §六 zxts2「只有代码」
    "zxts2":    ("CWServ.ph_agf10_zxts2", 0x17, ["@code@"]),
    # 出处: §六 zxts3「p1 = 板块/分类码 00501，p3/p4 空」
    "zxts3":    ("CWServ.ph_agf10_zxts3", 0x17, ["@sub@", "@code@", "", ""]),
    # 出处: §六 zxts4「只有代码」
    "zxts4":    ("CWServ.ph_agf10_zxts4", 0x17, ["@code@"]),
    # 出处: §六 zxts5「p1=00101，p3 是整数 0（不是字符串）」
    "zxts5":    ("CWServ.ph_agf10_zxts5", 0x17, ["@sub@", "@code@", 0]),
    # 出处: §五 zg_jby「p1 = 英文键 zhpf，p3 = "2"」
    "zgjby":    ("CWServ.ph_agf10_zg_jby", 0x18, ["@sub@", "@code@", "2"]),
    # 出处: §五 zg_jsm「p1 = 英文键 jdbxzxt，p3 = "1"，p4/p5 空」
    "zgjsm":    ("CWServ.ph_agf10_zg_jsm", 0x18, ["@sub@", "@code@", "1", "", ""]),
    # 出处: §五 zg_scm「p1 = 英文键 ztms 才出数（dyrdztms 回空）」
    "zgscm":    ("CWServ.ph_agf10_zg_scm", 0x18, ["@sub@", "@code@", ""]),
    # 出处: §五 zg_yq「p1 = 英文键 rdzt，p2 代码」
    "zgyq":     ("CWServ.ph_agf10_zg_yq", 0x17, ["@sub@", "@code@"]),
    # 出处: §五 zg_zs「p1 = 英文键 zhpf，p2 代码」
    "zgzs":     ("CWServ.ph_agf10_zg_zs", 0x17, ["@sub@", "@code@"]),
    # 出处: §七 zp「p1=00104 = 15 行标签表；00103 是 1×1 公司一句话简介」
    "zp":       ("CWServ.ph_agf10_zp", 0x14, ["@sub@", "@code@"]),
    # 出处: §七之二 zp_ggld「只有 6 位代码，无 p1」
    "zpggld":   ("CWServ.ph_agf10_zp_ggld", 0x19, ["@code@"]),
    # 出处: §七 sl_v2「p1=00201，p2 代码」
    "sl_v2":    ("CWServ.ph_agf10_sl_v2", 0x17, ["@sub@", "@code@"]),
    # 出处: §四 jysj「p1 子码、p2 代码、p3/p4 两个开关位（未定论）」
    "jysj":     ("CWServ.ph_agf10_jysj", 0x16, ["@sub@", "@code@", "0", "0"]),
    # 出处: §七 zj「只有代码」
    "agf10_zj": ("CWServ.ph_agf10_zj", 0x14, ["@code@"]),
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
                    raise ValueError("产品 %r 需要子码，但 SECTIONS 里没给")
                out.append(sub)
                continue
        out.append(slot)
    return out


def get_f10(client, symbol, section: str = "gsjj") -> Table:
    """取一只股票的 F10 某一节。

    参数
    ----
    client : Client
        只要求有 `call(cmd, ctl, params) -> Table`（离线测试用假 client 也行）。
    symbol : str | tuple
        `"600303"` / `"sh600303"` / `(1, "600303")`。
    section : str
        `gsjj`（默认）`jyfx` `gbgd` `zbyz` `rdtc` `fhrz` `hypm`
        `zxts` `zxts2` `zxts3` `zxts4` `zxts5`
        `zg_jby` `zg_jsm` `zg_scm` `zg_yq` `zg_zs`
        `zp` `zp_ggld` `sl_v2` `jysj` `agf10_zj`

    返回
    ----
    `Table`，`cols` = 标准字段（**不含 `"raw"`**），每行另带 `raw` 键（`len(row) == len(cols) + 1`）。列义见 `mapping.FIELD_MAP`；
    没映射的 section（`zxts` / `zxts2` / `jysj` / `agf10_zj`）只有 `raw`。

    异常
    ----
    ValueError
        `section` 不认识（消息里列出全部合法值）。
    EmptyResult
        当期无数据（原样上抛，不换成空表）。
    NotSupportedError
        多 ResultSet 命令的列名无法按文档还原时（例如该股返回的列数与文档
        模板不符）。**不猜列名**。

    示例
    ----
    >>> t = get_f10(c, "600303", section="gsjj")          # doctest: +SKIP
    >>> t.rows[0]["list_date"]
    '2000-12-26'
    """
    if section not in SECTIONS:
        raise ValueError("不认识的 section：%r；合法取值：%s"
                         % (section, "、".join(sorted(SECTIONS))))
    product, sub = SECTIONS[section]
    cmd, ctl, template = _PARAMS[product]

    _, code = symbols.parse(symbol)
    params = _render(template, code, sub)

    table = client.call(cmd, ctl, params)
    return mapping.apply(table, product)
