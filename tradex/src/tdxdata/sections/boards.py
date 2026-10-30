# -*- coding: utf-8 -*-
"""板块清单 / 板块成分（P4）。

契约见 `API设计.md` §二·七「P4/P5 板块 / 资金 / 市场」：

```python
get_boards(client, kind="all")   -> Table   # board_code/board_name/board_type/change_pct
get_board_members(client, board) -> Table   # code/name/market/price/change_pct
```

通用约定（与 P3/P5 一致）
------------------------
* 统一返回 `Table`：`Table.cols` 是**标准字段名**，每行是
  `{标准字段: 值, "raw": {原始列: 值}}`，**原始列一个都不丢**。
  `raw` 不是标准字段，因此**不列进 `Table.cols`**，但每行都带。
* 服务端给 `""` / `"—"` 的数值位填 `None`，**不填 `0.0`**（0 和"没有"必须能区分）。
* 拿不准含义的列**不给标准名**，只留在 `raw` 里。
* 没有通道的 `kind` **抛 `NotSupportedError`**（消息里写明为什么没有），
  **绝不返回空表**冒充"今天没数据"。

本模块用到的 3 条服务端命令（**均已 2026-10-25 实网跑通**）
----------------------------------------------------------
| 用途 | 命令 | ctl | 出处（实机验证） |
|---|---|---|---|
| 行业板块清单（128 行） | `CWServ.app_bklist_ag` | `0x16` | 数据中心剩余接口.md `app_bklist_ag` |
| 概念板块清单（**269 行全量**） | `HQServ.PBMultiHQ`（`field4=10004` 板块域） | `0x12` | 2026-10-26 手机抓包 cap4.pcap |
| 板块成分（按**板块代码**） | `CWServ.ph_tdxdatacenter_zttz_xggp` | `0x23` | 数据中心剩余接口.md `zttz_xggp` |

`kind` 落地状态（每条都有依据，见各分支注释）
--------------------------------------------
| `kind` | 状态 | 依据 |
|---|---|---|
| `industry` | ✅ 128 行行业板块清单（含 881xxx 指数码 + 一级行业归属） | `app_bklist_ag`；实网 128 行逐列复核 + 与 `hyfx_sy` 模式 2 的 881xxx 三处交叉验证 |
| `concept` | ✅ **269 行全量概念板块** | `PBMultiHQ(market=10004)` 翻页；2026-10-26 实网 269 行，与手机端逐个点数一致 |
| `all` | ⚠️ 部分（industry ∪ concept，2 次调用） | 服务端没有一条命令能一次列全 概念+行业+地域 |
| `area` | ✅ 32 行 | `PBMultiHQ(field4=10006)`；2026-10-26 实网 32 行 |
| `index` | ❌ `NotSupportedError` | 唯一带"板块指数"字样的 `qh_hqpd_bkzs` 是**期货**板块指数，`code` 是 `T006` 这类占位码，映射不到 A 股板块名 |

`change_pct` 从哪来（阶段 4 修正，**不是**硬造）
------------------------------------------------
* `PBMultiHQ` 系列（concept/style/area/industry1-3）的回包**本来就有** `CLOSE`（昨收）
  与 `NOW`（现价），所以涨跌幅是 `(now / close - 1) * 100`，来源写在
  `provenance.extra["change_pct_method"] = "PBMultiHQ.NOW/CLOSE"`。
* **`industry` 支路例外**：`app_bklist_ag` 的 9 列里确实**没有价格**
  （`N004` 全 128 行恒为 1，实网已排除，不是涨跌幅）。这一支路用**同码**
  去 `industry2`（`PBMultiHQ field4=10001`）取 `CLOSE`/`NOW` 回填
  （2026-10-27 实网 **128/128 同码**都能对上），`provenance.extra["change_pct_method"] = "industry2 回填"`。
  回填不到的单条**不许猜**：留 `None`，代码记进 `provenance.extra["change_pct_unavailable"]`。
* 分母保护：`close <= 0` / `now <= 0` / 任一为 `None` → `None`（**绝不把未知变成 0**）。

⚠️ 历史坑：这里曾经**无条件**写 `row["change_pct"] = None`，把上游已经拿到的
`CLOSE`/`NOW` 白扔了两列 —— "没有涨跌幅**列**"是真的，"没有**价格**"是假的，
当时把两件事混成了一件。

**板块名 → 成分 为什么要两次调用**
`zttz_xggp` 的 `p2` 只认**板块代码**（`880703` 这种），不认板块名；而调用方通常给名字。
所以 `get_board_members(client, "机器人概念")` 的路径是：
  1. 先用 `get_boards("concept")`（`PBMultiHQ`）取「板块名 → 880xxx 代码」；
  2. 再用 `zttz_xggp`（`p2=880xxx`）取成分。
传**代码**时只有第 2 步（1 次调用）。行业名则用 `app_bklist_ag` 反查，
但行业板块**没有"按板块代码取成分"的通道**（实网已验：`zttz_xggp` 传 `881002` 回
`EmptyResult`；`agf10_hypm` 只能输入**个股代码**取同行业，且那是 `f10.py` 的地盘），
所以查到也只会抛 `NotSupportedError`。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import EmptyResult, NotSupportedError
from ..models import Table, Provenance, ClockSource

__all__ = ["get_boards", "get_board_members", "BOARD_KINDS"]

# ── 合法 kind ────────────────────────────────────────────────────────────
BOARD_KINDS = ("all", "industry", "concept", "area", "style", "index",
                "industry1", "industry2", "industry3")

# 真有通道的 kind（其余在 `_UNSUPPORTED_KIND_REASON` 里给理由）
_SUPPORTED_KINDS = ("industry", "concept", "area", "style",
                    "industry1", "industry2", "industry3")

_DOMAIN = "板块与行业"

# ── 命令常量（ctl 取自 `tdxdata/data/products.json`，与文档双确认）──────────
# 行业一级目录：`["<模式>", "<行业码>"]`，模式 1 = 目录（行业码传什么都回同一份 30 行）
#   出处：实机验证（「模式 1：N001=一级行业码 X10 · N002=一级行业名」）
_CMD_HYFX_SY = "CWServ.ph_tdxdatacenter_hyfx_sy"
_CTL_HYFX_SY = 0x21                       # products.json: hyfx = ctl 33
# 行业板块清单：`["2"]`（p1=板块层级）
#   出处：实机验证 `app_bklist_ag`（128 行 × 9 列）＋
#         **2026-10-25 实网复跑确认**（见 _BKLIST_MAP 的逐列证据）
_CMD_BKLIST_AG = "CWServ.app_bklist_ag"
_CTL_BKLIST_AG = 0x16                     # products.json: bklist = ctl 22
_PARAMS_BKLIST_AG: list[Any] = ["2"]

# 行业一级目录（`hyfx_sy` 模式 1）**本模块已不再使用** ——
# 实网复核后行业清单改用更全的 `app_bklist_ag`（128 行，带 881xxx 指数代码与一级行业归属）。
# `hyfx_sy` 仍由 `flow.get_market_stat("industry")` 使用（模式 1 取一级码 + 模式 2 取明细）。

# 概念板块清单（热榜·板块榜，盘中每 5 分钟刷新）：`["001"]`
#   出处：实机验证 `tdxrb_block`（20 行 × 14 列，样本 机器人概念/880904）
_CMD_TDXRB_BLOCK = "CWServ.ph_tdxdatacenter_tdxrb_block"
_CTL_TDXRB_BLOCK = 0x25                   # products.json: tdxrb_block = ctl 37
_PARAMS_TDXRB_BLOCK: list[Any] = ["001"]

# 板块成分（主题投资·相关个股）：`["00901", <板块代码>, "2", <页码>, <条数>, 0, 1]`
#   出处：实机验证 `zttz_xggp`（`p5` 是条数且严格生效）
#   **2026-10-25 实网**：`p5=100` → 100 行；`p5=500` → **477 行**（880703 全部成员）
#   ⇒ `p5` 确实是条数且 500 够用；`p4` 不是页码（见 meta 注记）
_CMD_ZTTZ_XGGP = "CWServ.ph_tdxdatacenter_zttz_xggp"
_CTL_ZTTZ_XGGP = 0x23                     # products.json: zttz_xg = ctl 35
_ZTTZ_XGGP_SUB = "00901"
_ZTTZ_XGGP_P3 = "2"
_ZTTZ_XGGP_PAGE = 1
_ZTTZ_XGGP_COUNT = 500                    # 实网：500 → 477 行（该板块全部成员，未被截断）

# ── 原始列 → 标准字段映射（每行带出处）──────────────────────────────────
# 形状：(服务端原始列名, 标准字段名, 类型)  类型 ∈ text / num / int
# 「不允许跨命令套用 N00x」——同一 N00x 在不同命令里是不同指标，
# 所以下面每张表各自独立，不共享常量（实机验证 §0.2）。

_BKLIST_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：实机验证 `app_bklist_ag`；
    # **2026-10-25 实网 128 行全量复核**（值域 + 去重数 + 与 hyfx_sy 模式 2 交叉验证）：
    #   N002 全 128 行皆数值 881002..881479  → 通达信**板块指数代码**
    #       交叉验证：N002=881002(煤炭开采)/881007(油气开采)/881016(日用化工)
    #       与 hyfx_sy 模式 2 的 N003 逐一对上 ✅✅✅
    #   N003 全 128 行唯一文本 → 行业名（煤炭开采/焦炭加工/油气开采…）
    #   N001 == N005 == N008（三列同值，128 行皆成立）→ 三级行业码 X1001
    #   N006 去重 30 个（X10..X99）→ **一级行业码**；N007 去重 30 个 → 一级行业名
    #   N009 == N003；N004 全 128 行恒为 1（无量纲含义未确义）
    ("N002", "board_code", "text"),        # 881xxx 板块指数代码（与 concept 的 880xxx 同口径）
    ("N003", "board_name", "text"),        # 行业名 煤炭开采
    ("N001", "industry_code", "text"),     # 三级行业码 X1001
    ("N006", "level1_code", "text"),       # 一级行业码 X10
    ("N007", "level1_name", "text"),       # 一级行业名 煤炭
    # N004（恒 1）/ N005、N008（== N001）/ N009（== N003）→ 未确义或重复，只留 raw
)

_TDXRB_BLOCK_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：数据中心剩余接口.md `tdxrb_block`（20 行 × 14 列）；
    # **2026-10-25 实网**首行：机器人概念 / 880904 / 市场 1 / 时间「2026-10-25 20:40」
    # ⚠️ 只映射代码与名称两列：`N008`(热度) 与 `N012`(热度两档) 量纲不同且
    #    文档也只写"热度"，不敢给标准名 → 留在 raw 里。
    ("N003", "board_code", "text"),        # 通达信板块指数代码 880904
    ("N002", "board_name", "text"),        # 板块名 机器人概念
)

# PBMultiHQ 板块清单：2026-10-26 手机抓包定位（cap4.pcap）
# ─────────────────────────────────────────────────────────────
# 请求：HQServ.PBMultiHQ(ctl 0x12)，field4=10004（**板块域**）、field5=14、
#       field6=offset、field7=page_size（手机端用 44）。
# 响应：`{"AttachLog": ""}` + zlib(protobuf 表)，列顺序
#       Code / Setcode / Name / CLOSE / OPEN / MAX / MIN / NOW / LB / AMOUNT / …
# 实网：offset 0/44/88/132/176/220/264 翻完 → **272 个板块**，含
#       880903 水产品 / 880549 可燃冰 / 880564 白酒概念 / 880671 中特估。
# 这就是手机端「概念板块」列表（用户对照手机端为 269，差 3 个是涨停/连板类附属板块）。
_MULTIHQ_BOARD_MAP: tuple[tuple[str, str, str], ...] = (
    ("Code", "board_code", "text"),        # 880xxx 板块指数代码
    ("Name", "board_name", "text"),        # 板块名 水产品
    # 2026-10-27 实网复核：这两列 100% 非空（269 个概念板块逐个都有值），
    # 所以涨跌幅可以**现算**，不需要任何外部补数。
    ("NOW", "now", "num"),                 # 板块指数现价（板块点位，不是元）
    ("CLOSE", "close", "num"),             # 板块指数昨收
)
_CMD_MULTIHQ = "HQServ.PBMultiHQ"
_CTL_MULTIHQ = 0x12
_BOARD_MARKET = 10004                      # PBMultiHQ field4：概念板块域
_BOARD_PAGE = 44                           # 手机端实测页大小
_MULTIHQ_BOARD_COLS = ("Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT")

# 板块域（PBMultiHQ field4）：2026-10-26 实网扫描 10001..10008 命中全部六类，
# 行数与手机端逐个点数完全一致。
_BOARD_DOMAINS: dict[str, int] = {
    "industry1": 10008,     # 一级行业  30 个（石油/房地产/银行…）
    "industry2": 10001,     # 二级行业 128 个（油气开采/油服工程…）
    "industry3": 10002,     # 三级行业 345 个（会展服务/合成树脂…）
    "concept": 10004,       # 概念     269 个（水产品/可燃冰…）
    "style": 10005,         # 风格     158 个（昨日连板/昨日涨停/预计转亏…）
    "area": 10006,          # 地区      32 个（宁夏板块/北京板块/贵州板块…）
}

# 实网基准行数（2026-10-26 逐页翻完 + 与手机端逐个点数一致）。
# 用途只有一个：翻页中途被限流时**能发现少拿了**，并在 `provenance` 的 notes/extra 里说明。
# 板块数量会随时间增减，所以这里**只做提示、不报错**。
# 别名域（同样内容、不同域号，抓包里都出现过，不去用它们）：
#   10000 / 10009 = 行业二级（= 10001）；10010 = 行业三级（= 10002）
#   10003 / 10007 / 10011 / 10012 = 空或无意义
_BOARD_ROWS_BASELINE: dict[str, int] = {
    "industry1": 30, "industry2": 128, "industry3": 345,
    "concept": 269, "style": 158, "area": 32,
}

_ZTTZ_XGGP_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：数据中心剩余接口.md `zttz_xggp`（9 列）；**2026-10-25 实网**
    #   首行 ['2','880703','2998','603728','鸣志电器','1','5','理由全文','2024-02-27']
    #   第 2 行 ['2','880703','2998','002747','埃斯顿','0','5','理由全文','2024-02-27']
    ("N002", "board_code", "text"),        # 板块代码（与入参一致，回显可自检）
    ("N003", "theme_id", "text"),          # 主题 ID 2998
    ("N004", "code", "text"),              # 股票代码 603728
    ("N005", "name", "text"),              # 名称 鸣志电器
    ("N006", "market", "int"),             # 市场号（实网 1=沪 / 0=深）
    ("N008", "reason", "text"),            # 入选理由全文
    ("N009", "list_date", "text"),         # 入选日期 2024-02-27
    # N001(类型=2)、N007(恒为 5) 含义未确认 → 只留 raw
)

# 板块清单的**标准列顺序**
# 契约要求的最低字段在前。`now`/`close` 紧跟 `change_pct`：它们是涨跌幅的**两个输入**，
# 不一起给出来，用户就没法复核 `change_pct`（"这个数怎么算的"必须能当场验）。
_BOARD_COLS_BASE = ("board_code", "board_name", "board_type", "change_pct",
                    "now", "close")
# industry 额外给「三级行业码 + 一级行业归属」（实网确义），便于按一级行业聚合
_BOARD_COLS_INDUSTRY = _BOARD_COLS_BASE + ("industry_code", "level1_code", "level1_name")
_BOARD_COLS_CONCEPT = _BOARD_COLS_BASE
# `all` 用超集（concept 行没有行业层级 → None）
_BOARD_COLS_ALL = _BOARD_COLS_INDUSTRY

# 成分股表的**标准列顺序**（契约要求的最低字段在前；price/change_pct 本通道拿不到 → None）
_MEMBER_COLS = (
    "code", "name", "market", "price", "change_pct",
    "board_code", "theme_id", "reason", "list_date",
)

# ── 需要 `NotSupportedError` 的 kind：消息里把「为什么没有」说清 ──────────
_UNSUPPORTED_KIND_REASON = {
    "index": (
        "没有「A 股板块指数清单」通道：唯一带『板块指数』字样的 "
        "CWServ.ph_pcwebcall_qh_hqpd_bkzs 是**期货**板块指数排行，"
        "code 回的是 T006/T014 这类占位码、market=42，映射不到任何 A 股板块名"
        "（实机验证明记『code 是占位码，"
        "无法直接映射到板块名』）；其余 88xxxx 代码只作为板块代码出现在别的表里，"
        "没有『列举指数』的命令。"
    ),
}

# ── 字符串空值口径（服务端三种写法都见过）────────────────────────────────
_NULL_TOKENS = frozenset(
    {"", "—", "——", "-", "--", "－", "none", "null", "nan", "n/a"}
)

# 通达信板块指数代码：880xxx=概念/主题，881xxx=行业
_CODE_RE = re.compile(r"^\d{6}$")
_BOARD_CODE_RE = re.compile(r"^88\d{4}$")
_INDUSTRY_BOARD_RE = re.compile(r"^881\d{3}$")


# ══════════════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════════════
def _as_text(v: Any) -> str | None:
    """原始值 → 去空白文本；空值写法统一成 `None`（**不返回 `""`**）。"""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    return s


def _as_num(v: Any) -> float | None:
    """原始值 → float；`""` / `"—"` / 无法解析 → `None`（**不填 `0.0`**）。

    注意 `"0E-7"`（`ph_zt_zjlx_cxlclr` 实测出现过）是**合法** float（= 0.0），
    `float()` 能吃，不要当脏数据丢掉。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    if s.endswith("%"):                    # 少数命令的涨跌幅带百分号
        s = s[:-1].strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    f = _as_num(v)
    return None if f is None else int(f)


_COERCE = {"text": _as_text, "num": _as_num, "int": _as_int}


def _row_dict(src: Table, row: Any) -> dict[str, Any]:
    """把**一行**统一成 `{原始列: 值}`。

    ⚠️ 服务端真实形态是**位置列表**：`_engine/session.py` 的 `parse()` 把
    `ResultSet.Content` 里的 list 原样透传给 `Table.rows`，即 `rows=[[v1, v2, …], …]`，
    列名在 `Table.cols` 里、靠**下标**对齐（2026-10-25 实网确认；离线 fixture 用 dict 也支持）。
    """
    if isinstance(row, dict):
        return dict(row)
    return {name: (row[i] if i < len(row) else None)
            for i, name in enumerate(src.cols)}


def _cell(src: Table, row: Any, name: str) -> Any:
    """按原始列名取一格，兼容 list 行（真实）与 dict 行（测试）。"""
    if isinstance(row, dict):
        return row.get(name)
    try:
        i = src.cols.index(name)
    except ValueError:
        return None
    return row[i] if i < len(row) else None


def _shape(src: Table, spec: tuple[tuple[str, str, str], ...], *,
           const: dict[str, Any] | None = None,
           cmd: str = "", domain: str = _DOMAIN,
           meta: dict[str, Any] | None = None) -> Table:
    """原始 `Table` → 标准字段 `Table`（每行带 `raw`，原始列一个不丢）。

    `const` 是**常量字段**（如 `board_type`）：服务端没有这一列，
    值由调用方按"你请求的 kind"回填，不是从数据里猜出来的 —— 见各调用处的注释。
    同时在 `spec` 里声明的字段**无论服务端有没有值都会出现**，
    拿不到就是 `None`（契约：拿不到的列不要硬造，但要能区分"没有"）。

    ⚠️ **必须继承 `src.provenance`**：这张表是"重建"出来的（换标准列名 + 补 raw），
    若不带出处，上游 `client.call()` 刚盖上的 as_of / status / fetched_at /
    elapsed / 来源就全丢了 —— 这正是复核者判的那条阻断。用 `inherit_provenance`
    单点继承，别在每个调用点手工搬。`meta` 参数沿用旧名：键进
    `provenance.extra`（字符串值同时进 `notes`）。
    """
    cols: list[str] = []
    for field in (const or {}):
        cols.append(field)
    for _raw, std, _t in spec:
        if std not in cols:
            cols.append(std)

    rows: list[dict[str, Any]] = []
    for src_row in src.rows:
        out: dict[str, Any] = dict(const or {})
        for raw_name, std, kind in spec:
            out[std] = _COERCE[kind](_cell(src, src_row, raw_name))
        out["raw"] = _row_dict(src, src_row)   # 红线：原始列一个都不许丢
        rows.append(out)

    return Table(cols=cols, rows=rows, cmd=cmd,
                 domain=domain).inherit_provenance(src, **(meta or {}))


def _pct(now, close) -> float | None:
    """涨跌幅（百分数）= `(now / close - 1) * 100`；拿不准一律 `None`。

    **红线：不许把未知变成 0。** 三种情况必须返回 `None` 而不是 0：
      * 任一为 `None`（还没开盘 / 停牌 / 该行没价格）；
      * `close <= 0`（除零或脏数据，例如某些板块指数报 0）；
      * `now <= 0`（同上）。
    "0%" 是一个**断言**（"今天没涨没跌"），不是"我不知道"的占位符。
    """
    if now is None or close is None:
        return None
    try:
        n, c = float(now), float(close)
    except (TypeError, ValueError):
        return None
    if n <= 0 or c <= 0:
        return None
    return (n / c - 1.0) * 100.0


#: `change_pct` 的算法口径（写进 `provenance.extra["change_pct_method"]`，让用户知道数怎么来的）
_METHOD_MULTIHQ = "PBMultiHQ.NOW/CLOSE"
_METHOD_INDUSTRY = "industry2 回填"


def _note_change_pct(table: Table, method: str, *, note: str | None = None) -> Table:
    """把「涨跌幅怎么算的 / 哪几条没算出来」写进 `provenance`（**只加不改行**）。"""
    prov = table.provenance
    if prov is None:
        from datetime import datetime as _dt
        prov = Provenance(fetched_at=_dt.now().astimezone(), clock=ClockSource.LOCAL)
    lacking = [r.get("board_code") for r in table.rows if r.get("change_pct") is None]
    if lacking:
        prov = prov.with_extra(change_pct_method=method,
                               unavailable=["change_pct"],
                               change_pct_unavailable=lacking,
                               change_pct_note=note or (
                                   "这些板块取不到涨跌幅（源头缺 `CLOSE`/`NOW` 或值为 0）—— 留 None，"
                                   "不猜、不填 0。代码清单见 `change_pct_unavailable`。"))
    else:
        # 回填成功后必须把**上一次**记账清掉：industry 支路先在建表时记过一次
        # "全缺"，回填把它修好以后，那两条就成了假陈述（留着 = 骗人）。
        clean_extra = {k: v for k, v in prov.extra.items()
                       if k not in ("change_pct_unavailable", "change_pct_note",
                                    "unavailable")}
        prov = prov._replace_for(extra=clean_extra).with_extra(
            change_pct_method=method, unavailable=[])
    prov = prov.with_notes(
        "涨跌幅 `change_pct` 由 `(now / close - 1) * 100` 现算"
        "（口径见 `change_pct_method`）；`now`/`close` 是分子分母，**都给了**，"
        "可就地复核。任一价格缺失或 ≤0 → None（不把未知当 0）。")
    table.provenance = prov
    return table


def _drop_empty_cols(table: Table, *, protect: tuple[str, ...] = ()) -> Table:
    """**丢掉 100% 为 `None` 的列**，并把丢了哪些记进 `provenance.extra["dropped_empty_cols"]`。

    用途：不留「看起来有、实际永远空」的假列。调用方一看到 `cols` 里有 `price`
    就会以为取到了价格，用到下游才发现整列是 `None` —— 这种"假列"比缺列更坏。

    ⚠️ **作用域严格**：只用于 `get_boards(...)` 全部 kind 与
    `get_market_stat('industry'/'updown'/'sentiment')`。
    ⛔ **绝不能用在 ztqd 清单那类表上**：那里的整列 `None` 是「当日样本不完整」的
    **数据信号**（`N029`/`N025` 盘中全空 = 当日清单还没长全），丢了就等于把
    "数据不全"藏起来。那类表用 `_flag_all_null_cols`（只标记、不删）。

    `protect`：**契约列**（`cols` 里向调用方承诺过的那几个）。它们即使整列为空也
    **不删** —— 因为本库自己的规矩是「拿不到就填 `None`，而不是让列缺席，这样
    调用方能区分『没有这一列』和『这次没有值』」（见 `get_board_members`）。

    ⚠️ 但"保列"**不等于可以留着不解释**：任何整列为 `None` 的列（**包括被
    `protect` 保下来的契约列**）一律进 `provenance.extra["all_null_cols"]`，原因写进 notes。否则就是"让用户对着整列 None 猜"，
    那和假列一样坏。两个键的分工：
      * `all_null_cols`：**诊断**口径 —— 本次哪些列整列是 None（删没删都在里面）；
      * `dropped_empty_cols`：**动作**口径 —— 其中哪些被删掉了（非契约列）。

    记账口径：**只有真丢了才写 `provenance.extra["dropped_empty_cols"]`**。键缺席 = 本次一个
    非契约空列都没有（不是"没实现"）。反过来写空列表会让每个样本的 meta 键集
    都抖一下，而"没丢东西"本来就没有需要交代的。
    """
    if not table.rows:
        return table
    nulls = [c for c in table.cols if all(r.get(c) is None for r in table.rows)]
    # ★ 契约列被保护下来以后**必须交代**：不然用户对着一整列 None 猜半天，
    #   这正是本函数要防的那件事。所以"整列为空"这件事不论删没删都要记账：
    #   `all_null_cols` = 本次整列全空的列（诊断口径）
    #   `dropped_empty_cols` = 其中**被删掉**的（动作口径）
    if nulls:
        table.provenance = (table.provenance or _local_prov()).with_extra(
            all_null_cols=nulls)
    dropped = [c for c in nulls if c not in protect]
    if not dropped:
        return table
    keep = [c for c in table.cols if c not in dropped]
    for row in table.rows:                 # 行里的键也要删，否则「已删列」还能按名取到
        for c in dropped:
            try:
                del row[c]
            except (KeyError, TypeError):
                # KeyError = 行里本来就没有这个键（del 的幂等重复）；TypeError =
                # 行不是 dict（异构行）。两者都是「无东西可删」的合法形态 ——
                # 收窄保留：这是 best-effort 清理的正常路径，不是异常事件。
                pass
    table.set_cols(keep)
    table.provenance = (table.provenance or _local_prov()).with_extra(
        dropped_empty_cols=dropped).with_notes(
            "这些列本次 100% 为 None（结构性死列），已从 cols 删掉并在此记账。"
            "⚠️ 只有「该死」的空列才删；若是「当日样本不完整」造成的整列空"
            "（如 ztqd 的 N029/N025），那属于数据信号，走 all_null_cols **只标不删**。")
    return table


def _flag_all_null_cols(table: Table, *, product: str = "") -> Table:
    """**不删任何列**，只把 100% 为 `None` 的列名记进 `provenance.extra["all_null_cols"]`。

    用在数据中心产品（尤其 ztqd 涨停清单）：整列为 `None` 可能是**当日样本不完整**
    （`00102` 口径的当日清单还没长全），也可能真有死列 —— 库**分不清**，
    所以选择「保留 + 明说」，把判断权交给用户。删掉就等于替用户做了"这列没用"的决定。
    """
    if not table.rows:
        return table
    nulls = [c for c in table.cols if all(r.get(c) is None for r in table.rows)]
    if nulls:
        table.provenance = (table.provenance or _local_prov()).with_extra(
            all_null_cols=nulls).with_notes(
                "整列为 None 的列**已保留**（没有删）：对 %s 这类清单，整列空可能是"
                "「当日样本不完整」（例如 00102 口径当日清单还在长），那是**数据信号**，"
                "删列会把'数据不全'藏起来。要判死活请看多日样本，别只看今天。"
                % (product or "数据中心产品"))
    return table


def _local_prov():
    """无上游出处时的兜底凭据（如实标注 LOCAL 钟；板块/行情域）。"""
    from datetime import datetime as _dt
    return Provenance(fetched_at=_dt.now().astimezone(), clock=ClockSource.LOCAL)


def _check_kind(kind: Any) -> str:
    """`kind` 归一 + 校验；不认识 → `ValueError` 并列出全部合法取值。"""
    k = _as_text(kind)
    k = "" if k is None else k.lower()
    if k in _UNSUPPORTED_KIND_REASON:
        raise NotSupportedError(
            "get_boards(kind=%r)：%s 请改用 %s。"
            % (k, _UNSUPPORTED_KIND_REASON[k], " / ".join(_SUPPORTED_KINDS))
        )
    if k not in BOARD_KINDS:
        raise ValueError(
            "get_boards(kind=%r) 不认识。合法取值：%s（其中 %s 目前没有通道，"
            "会抛 NotSupportedError）。"
            % (kind, " / ".join(BOARD_KINDS), " / ".join(_UNSUPPORTED_KIND_REASON))
        )
    return k


# ══════════════════════════════════════════════════════════════════════════
# 取数（各通道单独一个函数，方便离线测试注入假 client）
# ══════════════════════════════════════════════════════════════════════════
def _fetch_industry_list(client, *, with_prices: bool = True) -> Table:
    """行业板块清单（128 行 × 9 列）+ `industry2` 回填涨跌幅。

    `app_bklist_ag` **1 次调用**、无日期参数；`with_prices=True` 时**另加一轮**
    `industry2`（`PBMultiHQ field4=10001`，128 行 ≈ 5 次分页）用**同码**回填
    `CLOSE`/`NOW` —— 2026-10-27 实网 128/128 同码全部对上。

    `with_prices=False` 给**内部**调用方用（`get_board_members` 只是按名字查代码，
    不需要涨跌幅）：省掉那一轮翻页，别让"查个板块代码"白等 10 秒。

    实网复核（2026-10-25）见 `_BKLIST_MAP` 的逐列证据；与 `hyfx_sy` 模式 2
    的 881xxx 代码三处交叉验证一致。
    """
    raw = client.call(_CMD_BKLIST_AG, _CTL_BKLIST_AG, list(_PARAMS_BKLIST_AG))
    table = _board_table(
        raw, _BKLIST_MAP, "industry",
        cols=_BOARD_COLS_INDUSTRY,
        cmd=_CMD_BKLIST_AG,
        method=_METHOD_INDUSTRY,
        meta={"params": list(_PARAMS_BKLIST_AG),
              "source_note": "app_bklist_ag（A 股板块清单，128 个行业，p1=层级 2）",
              "row_semantics": "N001==N005==N008 三级行业码；N003==N009 行业名；"
                               "N006/N007 一级行业码/名；N004 恒 1（未确义，留在 raw）",
              # 服务端 9 列里没有任何"类别"列 —— 这两列的值由**请求**回填，不是数据
              "const_fields": {
                  "board_type": "恒为 'industry'（这 128 行全部是行业板块；"
                                "服务端无类别列，值按请求 kind 回填，不是从数据里读的）",
                  "board_code": "取自 N002（881xxx 真板块码），不是 X1001 那种行业分类码",
              }},
    )
    if with_prices:
        _backfill_from_industry2(client, table)
    return table


def _backfill_from_industry2(client, table: Table) -> Table:
    """用**同码**在 `industry2`（`PBMultiHQ field4=10001`）里回填 `now`/`close`。

    为什么这条路是合法的：`app_bklist_ag` 的 `N002`（881xxx）与 `industry2` 板块域的
    `Code`（881xxx）是**同一套板块指数代码**，2026-10-27 实网 128/128 全部对上
    —— 是"同一个标的换个通道取价格"，不是"猜一个数填进去"。

    回填不到的单条**留 `None`**（绝不猜、绝不填 0），代码记进
    `meta["change_pct_unavailable"]`。
    """
    try:
        src = _fetch_multihq_boards(client, "industry2", _BOARD_DOMAINS["industry2"])
    except EmptyResult:
        # 服务端这次没给 industry2：industry 清单本身是好的，不该因为它整张表失败。
        # 但必须**明说**（留 None + 记账），不能装作算过了。
        for row in table.rows:
            row.setdefault("now", None)
            row.setdefault("close", None)
            row["change_pct"] = None
        return _note_change_pct(
            table, _METHOD_INDUSTRY,
            note="industry2 本次返回 0 行，全部板块的涨跌幅无法计算（留 None）。"
                 "行业清单本身是正常的，只是价格没回填上。")

    price: dict[str, tuple[Any, Any]] = {}
    for row in src.rows:
        code = row.get("board_code")
        if code:
            price[code] = (row.get("now"), row.get("close"))

    lacking: list[str] = []
    for row in table.rows:
        now, close = price.get(row.get("board_code"), (None, None))
        row["now"], row["close"] = now, close
        row["change_pct"] = _pct(now, close)
        if row["change_pct"] is None and row.get("board_code"):
            lacking.append(row["board_code"])
    return _note_change_pct(
        table, _METHOD_INDUSTRY,
        note=("这些板块在 industry2（PBMultiHQ 10001）里没有价格，"
              "所以涨跌幅留 None、不猜：%s" % "、".join(lacking)) if lacking else None)


def _fetch_multihq_boards(client, kind: str, market: int) -> Table:
    """`PBMultiHQ` 板块域清单（`field4=market`），2026-10-26 实网六类全通。

    | kind | market | 实网行数 | 与手机端 |
    |---|---|---|---|
    | `industry1` | 10008 | 30 | 30 ✅ |
    | `industry2` | 10001 | 128 | 128 ✅ |
    | `industry3` | 10002 | 345 | 345 ✅ |
    | `concept`   | 10004 | 269 | 269 ✅ |
    | `style`     | 10005 | 158 | 158 ✅ |
    | `area`      | 10006 | 32 | 32 ✅ |

    手机端自己也用 `field7=44` 分页（offset 步进 44）；响应有明文 protobuf
    和 `{"AttachLog": ""}`+zlib 两种形态，`hq_pb.get_market_page` 都会解。
    """
    from ..models import Table

    rows: list[dict[str, Any]] = []
    offset, empty = 0, 0
    while offset <= 1200 and empty < 2:         # 上限兜底，防止服务端异常时死循环
        _cols, part = client.board_page(offset=offset, page_size=_BOARD_PAGE,
                                       market=market)
        if part:
            empty = 0
            rows.extend(part)
        else:
            empty += 1
        offset += _BOARD_PAGE
    raw = Table(cols=list(_MULTIHQ_BOARD_COLS), rows=rows, cmd=_CMD_MULTIHQ)
    meta: dict[str, Any] = {
        "params": {"market": market, "offset_step": _BOARD_PAGE,
                   "page_size": _BOARD_PAGE},
        "source_note": "PBMultiHQ(ctl 0x12, field4=%d 板块域) 翻页；"
                  "2026-10-26 手机抓包 cap4.pcap 逐字节复现" % market,
        "rows_fetched": len(rows),
        "const_fields": {
            "board_type": "恒为 %r（服务端无类别列，值按请求 kind 回填）" % kind,
        },
    }
    # 翻页中途被限流会「静默少拿行」—— 拿实网基准对一下，少了就明说（不当成正常数据）
    baseline = _BOARD_ROWS_BASELINE.get(kind)
    if baseline is not None and len(rows) != baseline:
        meta["rows_baseline"] = baseline
        meta["warning"] = (
            "只取到 %d 行，2026-10-26 实网基准是 %d 行。"
            "翻页途中被限流会少拿（服务端不会报错），重试一次通常就齐。"
            % (len(rows), baseline)
        )
    return _board_table(
        raw, _MULTIHQ_BOARD_MAP, kind,
        cols=_BOARD_COLS_CONCEPT,
        cmd=_CMD_MULTIHQ,
        method=_METHOD_MULTIHQ,
        meta=meta,
    )


def _fetch_concept_list(client) -> Table:
    """概念板块清单（269 行）—— 保留旧名，等价于 `kind="concept"`。"""
    return _fetch_multihq_boards(client, "concept", _BOARD_DOMAINS["concept"])


def _fetch_industry_list_light(client) -> Table:
    """只取行业清单、**不**回填价格（内部按名字查代码时用，省一轮翻页）。"""
    return _fetch_industry_list(client, with_prices=False)


def _board_table(raw: Table, spec, board_type: str, *, cmd: str,
                 meta: dict[str, Any], cols: tuple[str, ...] = _BOARD_COLS_BASE,
                 method: str = _METHOD_MULTIHQ) -> Table:
    """板块清单的统一收尾：固定列 + **现算 `change_pct`** + 记账。

    ⚠️ 阶段 4 之前这里是 `row["change_pct"] = None` 写死两行 —— 而同一个回包里
    `CLOSE`/`NOW` 明明都有值。那不是"拿不到"，那是**把拿到的扔了**。
    """
    table = _shape(
        raw, spec,
        # 回填"我请求的是哪一类板块"；服务端没有类别列，这不是猜出来的
        const={"board_type": board_type},
        cmd=cmd, meta=meta,
    )
    for row in table.rows:
        row["change_pct"] = _pct(row.get("now"), row.get("close"))
        for field in cols:                 # 超集列补齐（如 concept 没有行业层级）
            row.setdefault(field, None)
    # ⚠️ 必须用 set_cols：直接赋 cols 只改表头，行的位置口径还停在旧列清单
    # （Const 字段在最前 → 旧清单比 cols 短），表现为「按名对、按位置错」。
    table.set_cols(cols)
    return _note_change_pct(table, method)


# ══════════════════════════════════════════════════════════════════════════
# 公开方法
# ══════════════════════════════════════════════════════════════════════════
def get_boards(client, kind: str = "all") -> Table:
    """板块清单。

    `kind`：`all` / `industry` / `concept` / `area` / `style` / `index` /
    `industry1` / `industry2` / `industry3`。

    标准字段
    --------
    | 字段 | 含义 | 单位/口径 |
    |---|---|---|
    | `board_code` | 板块代码 | **通达信板块指数代码**（两条通道同口径）：`industry` 是 `881xxx`（如 `881002`），`concept` 是 `880xxx`（如 `880904`） |
    | `board_name` | 板块名 | 文本（行业名 / 概念名） |
    | `board_type` | 板块类别 | **回填请求的 kind**（服务端没有类别列），`industry`/`concept` |
    | `change_pct` | 涨跌幅 | **百分数**（`1.18` = +1.18%），由 `(now / close - 1) * 100` 现算。`industry` 支路来自 `industry2` 同码回填；任一价格缺失或 ≤0 → `None`（**不是 0**）。口径见 `meta["change_pct_method"]` |
    | `now` / `close` | 板块指数现价 / 昨收 | `change_pct` 的**分子与分母**，都给出来以便就地复核；`industry` 支路同样来自 `industry2` 回填 |
    | `industry_code` | 三级行业码 | 仅 `industry`：`X1001` 这种（=`raw["N001"]`） |
    | `level1_code` / `level1_name` | 一级行业码/名 | 仅 `industry`：`X10` / `煤炭`（=`raw["N006"]`/`["N007"]`），便于按一级行业聚合 |
    | `raw` | 原始行 | 原始列一个不丢 |

    `kind` 落地状态（**均已实网跑通，2026-10-25**）
    --------------------------------------------
    * `industry` ✅ —— `CWServ.app_bklist_ag`（`["2"]`，ctl `0x16`），
      **128 行行业板块清单**（30 个一级行业下 128 个细分行业）。
      `board_code` = `N002`（`881002`…`881479` 板块指数代码，全 128 行数值化，
      并与 `hyfx_sy` 模式 2 的 `N003` 三处交叉验证一致）。
      9 列里**没有价格**（`N004` 全 128 行恒为 1，是别的量，实网已排除）。
      所以这一支路**多打一轮** `industry2`（`PBMultiHQ field4=10001`），
      用**同码**回填 `CLOSE`/`NOW` 再算涨跌幅（2026-10-27 实网 128/128 同码全中），
      `meta["change_pct_method"] == "industry2 回填"`。回填不到的单条留 `None`
      并记进 `meta["change_pct_unavailable"]`（不猜、不填 0）。
    * `concept` ✅ —— `HQServ.PBMultiHQ`（ctl `0x12`，`field4=10004` 板块域），
      按 `field6=offset` / `field7=44` 翻页，**2026-10-26 实网 269 行全量概念板块**
      （与手机端逐个点数一致：880903 水产品 / 880549 可燃冰 / 880564 白酒概念…）。
      回包里 `CLOSE`(昨收)/`NOW`(现价) **100% 非空** → `change_pct` 现算
      （`meta["change_pct_method"] == "PBMultiHQ.NOW/CLOSE"`）。
    * `all` ⚠️ —— `industry` ∪ `concept`（**2 次调用**）。服务端没有一条命令能
      一次列出「概念 + 行业 + 地域」全量，所以 `all` 是**两部分拼接**，
      `meta["partial"] = True`，**不含 `index`**（其余五类可用对应 kind 单独取）；
      concept 行没有行业层级 → `industry_code`/`level1_*` 为 `None`。
    * `area` ✅ / `style` ✅ / `industry1`·`industry2`·`industry3` ✅ ——
      全部走 `PBMultiHQ` 板块域（`field4` 10006/10005/10008/10001/10002），
      2026-10-26 实网 **32 / 158 / 30 / 128 / 345** 行，与手机端逐个点数一致。
    * `index` ❌ `NotSupportedError` —— 唯一的"板块指数"命令是期货板块指数，
      `code` 是 `T006` 这类占位码。

    ⚠️ 两套码制，别混（这条必须让人看得见）
    ------------------------------------
    * 本方法的 `board_code` 是**板块码**：`industry` = `881002` 这种（和 `gzzc_hyxq`
      用的 `881344` 同一套），`concept` = `880904` 这种。
    * `flow.get_market_stat("industry")` 内部用的是 `hyfx_sy` 模式 1 回的
      **`X10…X99` —— 那是行业分类码，不是板块码**（模式 2 只吃它）。
      所以不要把 `X10` 当成 `board_code` 传进 `get_board_members`，也不要拿
      `881002` 去当 `hyfx_sy` 模式 2 的入参 —— 两套码各管各的。
      `app_bklist_ag` 的 `N001` 才是 `X1001` 那种分类码，本方法把它放进
      `industry_code` 字段并保留在 `raw` 里。

    认识不到 `kind` → `ValueError`（消息里列出全部合法取值）。
    """
    k = _check_kind(kind)

    if k == "industry":
        # 只保护**基础契约列**：`industry_code`/`level1_*` 是 kind 专属扩展，
        # 整列全空时该丢（那正是"假列"），丢了记账即可。
        return _drop_empty_cols(_fetch_industry_list(client),
                                protect=_BOARD_COLS_BASE)
    if k in _BOARD_DOMAINS:            # industry1/2/3 · concept · style · area
        return _drop_empty_cols(_fetch_multihq_boards(client, k, _BOARD_DOMAINS[k]),
                                protect=_BOARD_COLS_BASE)

    # k == "all"：两条同构表拼接（都是"板块清单"形状，所以拼得起来）
    ind = _fetch_industry_list(client)
    con = _fetch_concept_list(client)
    rows: list[dict[str, Any]] = []
    for row in ind.rows:
        row["raw"]["_source"] = "app_bklist_ag"
        rows.append(row)
    for row in con.rows:
        row["raw"]["_source"] = "PBMultiHQ"
        rows.append(row)
    cols = list(_BOARD_COLS_ALL)
    for row in rows:                     # 超集补齐：概念行没有行业层级 → None
        for field in cols:
            row.setdefault(field, None)
    # 拼接表也必须带上语义层出处：以 industry 那份为准（两条通道同一时刻取的，
    # `as_of`/`status` 同源；`counts` 里另有 concept 的行数）
    merged = Table(
        cols=cols, rows=rows,
        cmd="%s + %s" % (_CMD_BKLIST_AG, _CMD_MULTIHQ),
        domain=_DOMAIN,
    ).inherit_provenance(ind, **{
            "kinds": ["industry", "concept"],
            "partial": True,
            "missing": ["index"],
            "also_available": ["industry1", "industry2", "industry3", "style", "area"],
            "note": "`all` 只拼 industry ∪ concept（2 次调用）；"
                    "其余五类（行业一/二/三级、风格、地区）请用对应 kind 单独取，"
                    "合并成一个 all 要多打 5 轮翻页，不值得默认付这个代价；"
                    "concept 行没有行业层级 → industry_code/level1_code/level1_name 为 None",
            "counts": {"industry": len(ind.rows), "concept": len(con.rows)},
            "const_fields": {"board_type": "按来源回填 'industry'/'concept'"
                                           "（服务端无类别列，不是数据里的）"},
    })
    # `change_pct` 两条支路来源不同（industry 走 industry2 回填、concept 走
    # PBMultiHQ 现算），在拼接表上必须**分开写清**，否则用户会以为整张表一个口径。
    _note_change_pct(
        merged,
        "industry 支路 = industry2 回填；concept 支路 = PBMultiHQ.NOW/CLOSE")
    return _drop_empty_cols(merged, protect=_BOARD_COLS_BASE)


def get_board_members(client, board) -> Table:
    """板块成分股。

    `board` 接受两种写法
    -------------------
    * **板块代码**：6 位数字且以 `88` 开头（`880703` 概念/主题、`880550` 题材）。
      → 直接调 `CWServ.ph_tdxdatacenter_zttz_xggp`（`p2=板块代码`），**1 次调用**。
    * **板块名**：如 `"机器人概念"`。→ **2 次调用**：
      1. `get_boards("concept")`（`PBMultiHQ` 板块域）取「板块名 → 880xxx 代码」；
      2. 再用 `zttz_xggp` 取成分。
      名称必须在**板块清单**里（`get_boards("concept")` 的 269 个概念板块）才认得，
      否则 `ValueError`（消息里说明只接受这些名字/改传代码）。

    明确拒绝的输入（都抛 `ValueError`，消息写清只接受什么）
    ------------------------------------------------------
    * 空串 / 非字符串非数字；
    * 6 位数字但**不以 `88` 开头**（那是股票代码，不是板块代码，如 `600519`）。

    明确拒绝的板块（抛 `NotSupportedError`）
    --------------------------------------
    * **行业板块（`881xxx` / 行业名）**：服务端**没有"按板块代码取成分股"的通道** ——
      `agf10_hypm` 只能输入**个股代码**取"同行业全部股票"（那是 `f10.py` 的 `hypm` 节），
      `hyfx_sy` 模式 2 只回"该一级行业下的子行业"，都不是成分股清单。
      所以查得到板块也不再往下走，直接抛（消息说明原因）。

    标准字段
    --------
    | 字段 | 含义 | 单位/口径 |
    |---|---|---|
    | `code` | 股票代码 | 6 位字符串 |
    | `name` | 名称 | 文本 |
    | `market` | 市场号 | int：`0`=深 `1`=沪 `2`=北（`zttz_xggp` 的 `N006`） |
    | `price` | 最新价 | **恒 `None`** —— 本通道不给价格 |
    | `change_pct` | 涨跌幅 | **恒 `None`** —— 本通道不给涨跌幅 |
    | `board_code` | 板块代码 | 回显（可自检入参） |
    | `theme_id` | 主题 ID | 文本 |
    | `reason` | 入选理由全文 | 文本 |
    | `list_date` | 入选日期 | `YYYY-MM-DD` |
    | `raw` | 原始行 | 原始列一个不丢 |

    ⚠️ 需要价格/涨跌幅时请拿 `code` 自行调 `client.get_bars(code, count=1)`
    （`get_quotes` 不可用：协议无五档字段且无代码位，2026-10-25 实测）；
    本方法不替服务端编造这两列。
    """
    mode, key = _resolve_board_key(client, board)

    if mode == "industry":
        raise NotSupportedError(
            "get_board_members(%r)：行业板块（%s）没有『按板块代码取成分股』的通道。"
            "唯一能拿同行业股票的是 CWServ.ph_agf10_hypm，但它只接受**个股代码**"
            "（返回该股所在行业的全部股票，一次上限 31 只），不接受板块代码；"
            "CWServ.ph_tdxdatacenter_hyfx_sy 模式 2 回的是『该一级行业下的子行业』，"
            "不是成分股。请改传概念/主题板块代码（880xxx），或用 "
            "client.get_f10(symbol, section=\"hypm\") 取个股所属行业成员。" % (board, key)
        )

    params: list[Any] = [
        _ZTTZ_XGGP_SUB, key, _ZTTZ_XGGP_P3,
        _ZTTZ_XGGP_PAGE, _ZTTZ_XGGP_COUNT, 0, 1,
    ]
    raw = client.call(_CMD_ZTTZ_XGGP, _CTL_ZTTZ_XGGP, params)
    table = _shape(
        raw, _ZTTZ_XGGP_MAP,
        cmd=_CMD_ZTTZ_XGGP,
        meta={
            "params": params,
            "board": key,
            "source_note": "zttz_xggp（主题投资·相关个股，p5 是条数）",
            "missing_fields": ["price", "change_pct"],
            "unavailable": ["price", "change_pct"],
            "why": "zttz_xggp 的 9 列是「类型/板块码/主题ID/股票代码/名称/市场/"
                   "未知(恒5)/入选理由/入选日期」，**不含价格与涨跌幅**；"
                   "要价格/涨跌幅请拿 code 自行调 client.get_bars(code, count=1)"
                   "（get_quotes 不可用：协议无五档字段且无代码位，2026-10-25 实测）",
        },
    )
    # 契约要求的最低标准字段 code/name/market/price/change_pct 必须都在。
    # price / change_pct 本通道**拿不到** → 填 None（而不是让列缺席），
    # 这样调用方能区分"没有这一列"和"这次没有值"；绝不填 0.0。
    for row in table.rows:
        row.setdefault("price", None)
        row.setdefault("change_pct", None)
    table.set_cols(_MEMBER_COLS)   # 同上：重绑行的位置口径
    return table


def _resolve_board_key(client, board) -> tuple[str, str]:
    """入参归一 → `(mode, key)`。

    * `("board_code", "880703")` —— 可直接取成分的**概念/主题**板块代码；
    * `("industry", "X10"/"881002"/"煤炭")` —— 认出来是**行业板块**，但行业没有成分通道，
      调用方据此抛 `NotSupportedError`（把"认出来了、但没通道"和"压根不认得"分开：
      后者是 `ValueError`）。

    失败一律 `ValueError`（消息里写清**只接受**哪种写法）。
    """
    if board is None:
        raise ValueError(
            "get_board_members(board=None)：board 必须是板块代码（6 位、88 开头，"
            "如 880703）或板块名（如 机器人概念）。"
        )
    s = str(board).strip()
    if not s:
        raise ValueError(
            "get_board_members(board=%r)：空串。board 必须是板块代码（如 880703）"
            "或板块名（如 机器人概念）。" % (board,)
        )

    if _CODE_RE.match(s):
        if _INDUSTRY_BOARD_RE.match(s):
            return "industry", s            # 881xxx = 行业板块指数
        if _BOARD_CODE_RE.match(s):
            return "board_code", s          # 880xxx = 概念/主题
        raise ValueError(
            "get_board_members(%r)：%r 看着是**股票代码**，不是板块代码。"
            "本方法只接受 6 位、以 88 开头的通达信板块指数代码（如 880703 概念、"
            "880550 题材），或板块名（如 机器人概念）。"
            % (board, s)
        )

    # ── 名字路径：先在概念板块清单里查，再在行业板块清单里查 ──
    con = _fetch_concept_list(client)
    for row in con.rows:
        if row.get("board_name") == s:
            return "board_code", row["board_code"]

    ind = _fetch_industry_list_light(client)     # 只要名字→代码，不必付价格那轮翻页
    for row in ind.rows:
        if row.get("board_name") == s:
            # 认出来了：这是行业板块。它的 board_code 是 881xxx（行业板块指数代码），
            # 由 get_board_members 统一抛 NotSupportedError（行业无成分通道，实网已验）。
            return "industry", row.get("board_code") or s

    names = [r.get("board_name") for r in con.rows if r.get("board_name")]
    raise ValueError(
        "get_board_members(%r)：板块清单里找不到这个板块名。"
        "只认得 `get_boards(\"concept\")`（概念板块清单）与 `get_boards(\"industry\")`"
        "（行业板块清单）里出现过的名字，共 %d 个概念 + %d 个行业；"
        "或者直接传板块代码（880xxx 概念 / 881xxx 行业）。概念板块候选名：%s"
        % (board, len(names), len(ind.rows),
           "、".join(names[:10]) + ("…" if len(names) > 10 else ""))
    )
