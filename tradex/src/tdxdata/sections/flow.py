# -*- coding: utf-8 -*-
"""资金流向 / 市场概览（P5）。

契约见 `API设计.md` §二·七「P4/P5 板块 / 资金 / 市场」：

```python
get_capital_flow(client, symbols=None) -> Table   # code/name/net_inflow/net_inflow_pct/price/change_pct
get_market_stat(client, kind="all")    -> Table
```

通用约定（与 P3/P4 一致）
------------------------
* 统一返回 `Table`：`Table.cols` 是**标准字段名**，每行是
  `{标准字段: 值, "raw": {原始列: 值}}`，**原始列一个都不丢**。
  `raw` 不是标准字段，因此**不列进 `Table.cols`**，但每行都带。
* 服务端给 `""` / `"—"` 的数值位填 `None`，**不填 `0.0`**。
* 拿不准含义的列**不给标准名**，只留在 `raw` 里。
* 没有通道的 `kind` **抛 `NotSupportedError`**（消息写明为什么没有），
  **绝不返回空表**冒充"今天没数据"。

`get_capital_flow(symbols)` 落地状态
------------------------------------
| `symbols` | 状态 | 依据 |
|---|---|---|
| `None`（全市场排行） | ✅ 有通道 | `CWServ.ph_zt_zjlx_cxlclr` 榜单模式（服务器**自带中文表头**，语义最可靠） |
| 单代码 | ❌ `NotSupportedError` | 见函数 docstring（榜单命令没有代码参数；唯一"按代码"的 `HQServ.PBONEZJLX` 是 protobuf 通道，`call()` 的 JSON 路径发不出去） |
| 代码列表 | ❌ `NotSupportedError` | 同上 |

`get_market_stat(kind)` 落地状态
--------------------------------
| `kind` | 状态 | 依据（实机验证） |
|---|---|---|
| `updown` | ✅ 涨跌停家数时间序列（30 个交易日） | 数据中心剩余接口.md `ph_datacenter_zdt_zdfx` |
| `industry` | ✅ 行业维度涨跌 + **涨跌家数** | 市场统计与行业分析.md §三 `hyfx_sy` 模式 2（"本批唯一带涨跌家数的表"） |
| `sentiment` | ✅ 市场热度值 + 中文判语 | 市场统计与行业分析.md §十三 `ph_pcwebcall_hy_bkldscnx` |
| `all` | ❌ `NotSupportedError` | 三类统计**列结构互斥**（日×2 计数 / 行业×2 计数 / 单行多指标），没有单条命令能覆盖，拼统一表就是硬造 |

单位口径（**别弄错**）
--------------------
* `net_inflow` / `net_inflow_continued` —— **元**（`cxlclr` 的"主力净额/持续主力净额"，
  样本 `中国石油 152152128.00` = 1.52 亿元，量级自洽）
* `change_pct` / `change_pct_period` —— **百分数**（`1.1811024` = 1.18%，不是 0.0118）
* `heat_value` —— `hy_bkldscnx` 的 `N001`，**0~100 的分值**，不是百分数
* `limit_up_count` / `limit_down_count` / `up_count` / `down_count` —— **家数**（整数）
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import EmptyResult, NotSupportedError
from .boards import _drop_empty_cols
from ..models import Table

__all__ = ["get_capital_flow", "get_market_stat", "MARKET_STAT_KINDS",
           "CAPITAL_FLOW_HEAD"]

_DOMAIN_FLOW = "资金与筹码"
_DOMAIN_STAT = "市场统计"

# ══════════════════════════════════════════════════════════════════════════
# 命令常量（ctl 取自 `tdxdata/data/products.json`，与文档双确认）
# ══════════════════════════════════════════════════════════════════════════
# 资金流向·持续净流入榜：`[p1, 交易日, p3, p4, p5]`
#   p1 五位 = 前 3 位方向 + 后 2 位口径：001=净流出 / 002=净流入；01=主买净额 / 02=主力净额
#   出处：实机验证 `CWServ.ph_zt_zjlx_cxlclr`
_CMD_CXLCLR = "CWServ.ph_zt_zjlx_cxlclr"
_CTL_CXLCLR = 0x1A                     # products.json: zjlx_cxlclr = ctl 26
_CXLCLR_P1 = "00202"                   # 净流入榜 · 主力净额（00201 = 主买净额，见 docstring）
_CXLCLR_DATE = ""                      # 传空 = 最新交易日（实测：回 2026-10-23 这种带横线的）
_CXLCLR_P3 = 6                         # 位置3 实测 "6" 稳妥；"30" 会换成另一张表（行业成分股）
_CXLCLR_P4 = 1
_CXLCLR_P5 = 1

# 涨跌停统计·涨跌分析：`["zdtj", "<YYYY-MM-DD>", 0, ""]`
#   出处：实机验证 `ph_datacenter_zdt_zdfx`（30 行 × 3 列，样本 20261023/84/7）
#   ⚠️ 日期必须带横线，"日期不带横线会空"
_CMD_ZDT_ZDFX = "CWServ.ph_datacenter_zdt_zdfx"
_CTL_ZDT_ZDFX = 0x1F                   # products.json: zdt = ctl 31

# 行业分析（一级目录 / 一级行业下明细）：`[<模式>, "<行业码>"]`
#   出处：实机验证记录 §三
_CMD_HYFX_SY = "CWServ.ph_tdxdatacenter_hyfx_sy"
_CTL_HYFX_SY = 0x21                    # products.json: hyfx = ctl 33
_HYFX_MODE_DIR = 1
_HYFX_MODE_DETAIL = 2
_HYFX_DIR_P2 = "H01"                   # 模式 1 忽略 pos2

# 板块轮动·市场热度/牛熊：`[<视图>, "<日期>"]`
#   出处：实机验证 `ph_pcwebcall_hy_bkldscnx`
#   视图 002 才配日期，且「空 = 最新」→ 用 "" 避免自己猜交易日
_CMD_BKLDSCNX = "CWServ.ph_pcwebcall_hy_bkldscnx"
_CTL_BKLDSCNX = 0x21                   # products.json: bkld_cnx = ctl 33
_BKLDSCNX_VIEW = "002"

# 当前交易日（1 行 × 1 列）：`{"Params":[]}` 形式可出数
#   出处：实机验证 `sys_fn_jyr_v3`（N001=2026-10-23）
_CMD_TODAY = "CWServ.sys_fn_jyr_v3"
_CTL_TODAY = 0x16                      # products.json: fn_jyr_v3 = ctl 22

# ── 原始列 → 标准字段映射（每行带出处）──────────────────────────────────
# 「T00x / N00x 是跨命令复用的数值槽，同一个 T00x 在不同命令里是不同指标」
# （实机验证）→ 每张表独立，绝不共享常量。
# 形状：(服务端原始列名, 标准字段名, 类型)   类型 ∈ text / num / int

_CXLCLR_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：实机验证 `CWServ.ph_zt_zjlx_cxlclr`
    # ★ 这一列含义**不是推断** —— 直接取自服务器返回的 RS1 **中文表头**
    #   ["证券代码","市场代码","证券名称","主力净额","持续主力净额","持续天数",
    #    "最新交易日","当日涨幅","期间涨幅"]
    #   样本（00202）: ['601857','1','中国石油','152152128.00','4328653792.00','17',
    #                   '2026-10-20','1.1811024','21.00']
    ("N001", "code", "text"),                  # 证券代码
    ("N002", "market", "int"),                 # 市场代码（0=深 1=沪）
    ("N003", "name", "text"),                  # 证券名称
    ("N004", "net_inflow", "num"),             # 主力净额（元；改 _CXLCLR_P1=00201 则为主买净额）
    ("N005", "net_inflow_continued", "num"),   # 持续主力净额（元）
    ("N006", "net_inflow_days", "int"),        # 持续天数
    ("N007", "date", "text"),                  # 最新交易日 YYYY-MM-DD
    ("N008", "change_pct", "num"),             # 当日涨幅（%）
    ("N009", "change_pct_period", "num"),      # 期间涨幅（%）
)

_ZDT_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：实机验证 `ph_datacenter_zdt_zdfx`
    #   样本首行 `N001=20261023 N002=84 N003=7`
    #   校验：涨停 84 家 / 跌停 7 家，与当日市场量级自洽
    ("N001", "stat_date", "text"),             # 日期 YYYYMMDD（服务端原样，另给 date 归一版）
    ("N002", "limit_up_count", "int"),         # 涨停家数
    ("N003", "limit_down_count", "int"),       # 跌停家数
)

_HYFX_LV2_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：实机验证 §三 模式 2（`[2,"X10"]` → 8 列 × 4 行）
    #   样本: ['煤炭开采','X1001','881002','Ⅱ','20261019','1.0131','19','6']
    #   校验（文档已做）：上级 19 涨 / 6 跌 = 下级 10+9 / 5+1 ✅ → N007/N008 就是涨/跌家数
    ("N001", "industry_name", "text"),         # 行业名
    ("N002", "industry_code", "text"),         # 行业码 X1001
    ("N003", "board_code", "text"),            # 通达信行业指数代码 881xxx
    ("N004", "level", "text"),                 # 层级 Ⅱ / Ⅲ
    ("N005", "date", "text"),                  # 日期 YYYYMMDD
    ("N006", "change_pct", "num"),             # 涨跌幅（%）
    ("N007", "up_count", "int"),               # 上涨家数
    ("N008", "down_count", "int"),             # 下跌家数
)

_BKLDSCNX_MAP: tuple[tuple[str, str, str], ...] = (
    # 出处：实机验证 `ph_pcwebcall_hy_bkldscnx`
    #   样本（["002","2026-10-23"]）:
    #   ["47.81521429","市场氛围一般","因为计算市场热度值的源数据存在时滞风险…",
    #    "79.3042",null,"63.7016","35.0072","23.7667","10.5452","36.6212","85.7604","2026-10-23"]
    ("N001", "heat_value", "num"),             # 市场热度值 0~100（不是百分数）
    ("N002", "heat_label", "text"),            # 中文判语「市场氛围一般」
    ("N012", "date", "text"),                  # 数据日期 YYYY-MM-DD
    # N003 = 服务端自带的风险免责声明长文本（与业务无关）→ 只留 raw
    # N004~N011 = 8 个分项读数，**名字未破解**（缺 page_market_bull_bear.js 表头，
    #   文档明记「待确认」）→ 按红线「拿不准含义的列不许给标准名」，全部只留 raw
)

# ── 合法 kind / 标准列顺序 ──────────────────────────────────────────────
MARKET_STAT_KINDS = ("all", "updown", "industry", "sentiment")

# 资金流向表的标准列（契约要求的最低字段在前；price/net_inflow_pct 本通道拿不到 → None）
CAPITAL_FLOW_HEAD = (
    "code", "name", "market", "price",
    "net_inflow", "net_inflow_pct", "change_pct",
)
_CAPITAL_FLOW_COLS = CAPITAL_FLOW_HEAD + (
    "net_inflow_continued", "net_inflow_days", "change_pct_period", "date",
)
_STAT_COLS = {
    "updown": ("stat_date", "limit_up_count", "limit_down_count"),
    "industry": ("industry_name", "industry_code", "board_code", "level",
                 "date", "change_pct", "up_count", "down_count"),
    "sentiment": ("heat_value", "heat_label", "date"),
}

# 一级行业码（模式 2 只吃这个形状；`X1001` 这种二级码会回 0 行）
_LV1_RE = re.compile(r"^X\d{2}$")
# 逐一级行业的循环上限：防止目录异常时打出上百次请求（红线：限流按来源计数）
# 实网（2026-10-25）：模式 1 的 30 行**全部**是 ^X\d{2}$（X10..X99），
# 所以完整跑一次 industry = 1 + 30 = **31 次调用**。上限留 40 是为了"多出来的码不静默丢"。
_INDUSTRY_MAX_CODES = 40

# ── 空值口径 ─────────────────────────────────────────────────────────────
_NULL_TOKENS = frozenset(
    {"", "—", "——", "-", "--", "－", "none", "null", "nan", "n/a"}
)


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

    `"0E-7"`（`cxlclr` 实测出现过，交通银行）是合法 float（= 0.0），不要当脏数据丢。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    f = _as_num(v)
    return None if f is None else int(f)


_COERCE = {"text": _as_text, "num": _as_num, "int": _as_int}


def _norm_date(v: Any) -> str | None:
    """日期归一成 `YYYY-MM-DD`。

    「同一命令里日期格式会变」（实机验证，陷阱 6）：
    `20261023` / `2026-10-23` / `2026-10-23 00:00:00` 都要能吃。
    """
    s = _as_text(v)
    if s is None:
        return None
    s = s.split(" ")[0].split("T")[0]
    if re.match(r"^\d{8}$", s):
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s


def _shape(src: Table, spec: tuple[tuple[str, str, str], ...], *,
           const: dict[str, Any] | None = None,
           cols: tuple[str, ...] | None = None,
           cmd: str = "", domain: str = "", meta: dict[str, Any] | None = None) -> Table:
    """原始 `Table` → 标准字段 `Table`（每行带 `raw`，原始列一个不丢）。"""
    out_cols: list[str] = list(cols) if cols else []
    for field in (const or {}):
        if field not in out_cols:
            out_cols.append(field)
    for _raw, std, _t in spec:
        if std not in out_cols:
            out_cols.append(std)

    rows: list[dict[str, Any]] = []
    for src_row in src.rows:
        out: dict[str, Any] = dict(const or {})
        for raw_name, std, kind in spec:
            out[std] = _COERCE[kind](_cell(src, src_row, raw_name))
        out["raw"] = _row_dict(src, src_row)             # 红线：原始列一个都不许丢
        rows.append(out)

    # ⚠️ 同 boards._shape：重建表必须继承 `src.provenance`，否则语义层键全丢
    return Table(cols=out_cols, rows=rows, cmd=cmd,
                 domain=domain).inherit_provenance(src, **(meta or {}))


def _row_dict(src: Table, row: Any) -> dict[str, Any]:
    """把**一行**统一成 `{原始列: 值}`。

    ⚠️ 服务端真实形态是**位置列表**：`_engine/session.py` 的 `parse()` 把
    `ResultSet.Content` 里的 list 原样透传给 `Table.rows`（`rows=[[v1, v2, …], …]`），
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


def _current_trade_date(client) -> str:
    """取服务端**当前交易日**（`CWServ.sys_fn_jyr_v3`，1 行 × 1 列）。

    为什么需要它：`zdt_zdfx` 的日期位「不带横线会空」、给错交易日也静默 0 行
    （实网验证：`hy_bkldscnx` 视图 `002` 传空日期直接 `-1005`），
    而契约里的签名没有 date 参数 —— 与其瞎猜"今天"，不如问服务端要。
    取不到 → `NotSupportedError`（**不拿本机时钟冒充交易日**）。
    """
    tbl = client.call(_CMD_TODAY, _CTL_TODAY, [])
    raw = None
    if tbl.rows:
        names = list(tbl.cols) or ["N001"]
        raw = _cell(tbl, tbl.rows[0], names[0])
        if raw is None:
            raw = _cell(tbl, tbl.rows[0], "N001")
    date = _norm_date(raw)
    if not date or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise NotSupportedError(
            "取不到当前交易日：%s 返回 %r（期望 1 行 1 列日期）。"
            "没有交易日就无法构造涨跌停统计的日期参数，"
            "本库**不会用本机时钟冒充交易日**。" % (_CMD_TODAY, raw)
        )
    return date


# ══════════════════════════════════════════════════════════════════════════
# P5-1 资金流向
# ══════════════════════════════════════════════════════════════════════════
def get_capital_flow(client, symbols=None) -> Table:
    """资金流向（个股资金净额排行）。

    `symbols` 支持范围（**逐条写清，别猜**）
    --------------------------------------
    * `None` —— ✅ **全市场排行**（净流入榜前 20）。
      走 `CWServ.ph_zt_zjlx_cxlclr` 榜单模式 `["00202", "", 6, 1, 1]`，
      **1 次调用**，不需要日期（位置2 传空 = 服务端给最新交易日）。
    * 单代码（`"600519"`）/ 代码列表（`["600519", "000001"]`）——
      ❌ 抛 `NotSupportedError`。原因有三条，都是硬约束：
        1. `ph_zt_zjlx_cxlclr` 是**排行榜**命令，参数里**没有代码位**
           （`[方向+口径, 交易日, p3, p4, p5]`），无法按代码过滤；
           拿榜单前 20 行去"过滤"会静默给出错误答案（不在榜里 ≠ 没资金流向）；
        2. 全库唯一"按证券代码取资金流向"的是 `HQServ.PBONEZJLX`
           （ctl `0x12`，body 是 **Base64 编码的 protobuf 二进制**，
           `client.call()` 走的是 JSON 路径，发不出去）；
        3. `HQServ.hq_nlp_zjlx` 虽然有 `Market/Type/Page/PageSize`，同样没有代码位。
      等 PB 通道接入后这里再补。

    标准字段
    --------
    | 字段 | 含义 | 单位/口径 |
    |---|---|---|
    | `code` | 证券代码 | 6 位字符串 |
    | `name` | 证券名称 | 文本 |
    | `market` | 市场代码 | int：`0`=深 `1`=沪（服务端 `N002`） |
    | `price` | 最新价 | **恒 `None`** —— 本通道不回价格 |
    | `net_inflow` | **主力净额**（`_CXLCLR_P1="00202"`） | **元** |
    | `net_inflow_pct` | 净额占比 | **恒 `None`** —— 本通道没有占比列 |
    | `change_pct` | 当日涨幅 | **百分数**（`1.18` = 1.18%） |
    | `net_inflow_continued` | 持续主力净额 | 元 |
    | `net_inflow_days` | 持续天数 | 整数 |
    | `change_pct_period` | 期间涨幅 | 百分数 |
    | `date` | 最新交易日 | `YYYY-MM-DD` |
    | `raw` | 原始行 | 原始列一个不丢 |

    想改口径：把模块常量 `_CXLCLR_P1` 换成 `"00201"`（主买净额）或 `"00102"`
    （净流出榜）即可 —— 三者的列结构完全一样，只是 `N004` 的中文表头从
    "主力净额"变"主买净额"、数值变负。
    """
    if symbols is not None:
        raise NotSupportedError(
            "get_capital_flow(symbols=%r)：**不支持按代码查询资金流向**。"
            "全市场排行请传 symbols=None。原因："
            "(1) 唯一可用的 ph_zt_zjlx_cxlclr 是排行榜命令，参数里没有代码位，"
            "按代码'过滤'前 20 行会静默给错答案（不在榜 ≠ 没资金流向）；"
            "(2) 全库唯一按证券代码取资金流向的 HQServ.PBONEZJLX 是 protobuf 通道"
            "（ctl 0x12，body 为 Base64 二进制），client.call() 的 JSON 路径发不出去；"
            "(3) HQServ.hq_nlp_zjlx 也只有 Market/Type/Page 等位，没有代码位。"
            % (symbols,)
        )

    params: list[Any] = [
        _CXLCLR_P1, _CXLCLR_DATE, _CXLCLR_P3, _CXLCLR_P4, _CXLCLR_P5,
    ]
    raw = client.call(_CMD_CXLCLR, _CTL_CXLCLR, params)
    table = _shape(
        raw, _CXLCLR_MAP,
        cols=_CAPITAL_FLOW_COLS,
        cmd=_CMD_CXLCLR, domain=_DOMAIN_FLOW,
        meta={
            "params": params,
            "p1": _CXLCLR_P1,
            "p1_meaning": "00202 = 净流入榜·主力净额（00201 = 主买净额；001xx = 净流出榜）",
            "source_note": "ph_zt_zjlx_cxlclr 榜单模式（服务器自带中文表头 RS1）",
            "unavailable_fields": ["price", "net_inflow_pct"],
        },
    )
    # 契约要求的最低字段必须都在：price / net_inflow_pct 本通道拿不到 → None（不是 0.0）
    for row in table.rows:
        row.setdefault("price", None)
        row.setdefault("net_inflow_pct", None)
        row.setdefault("change_pct", None)
    # ⚠️ 必须用 set_cols：直接赋 cols 只改表头，行的位置口径不会跟着变
    table.set_cols(_CAPITAL_FLOW_COLS)
    return table


# ══════════════════════════════════════════════════════════════════════════
# P5-2 市场概览
# ══════════════════════════════════════════════════════════════════════════
def get_market_stat(client, kind: str = "updown") -> Table:
    """市场概览统计。

    `kind`：`updown`（**默认**）/ `industry` / `sentiment` / `all`。

    > **默认值是 `updown` 而不是 `all`**（Lead 拍板）：`all` 没有通道、必然抛错，
    > 把它当默认值等于"无参调用必失败"，是坏接口。`all` 仍然**合法**
    > （传了会拿到一条解释清楚的 `NotSupportedError`），只是不再是默认值。

    | `kind` | 状态 | 通道 | 标准字段 |
    |---|---|---|---|
    | `updown` | ✅ 近 30 个交易日涨跌停家数 | `CWServ.ph_datacenter_zdt_zdfx` | `stat_date` `limit_up_count` `limit_down_count` |
    | `industry` | ✅ 行业涨跌 + 涨跌家数 | `hyfx_sy` 模式 2（**逐一级行业循环**） | `industry_name` `industry_code` `board_code` `level` `date` `change_pct` `up_count` `down_count` |
    | `sentiment` | ✅ 市场热度值 + 中文判语 | `CWServ.ph_pcwebcall_hy_bkldscnx` 视图 `002` | `heat_value` `heat_label` `date` |
    | `all` | ❌ `NotSupportedError` | — | — |

    为什么 `all` 抛异常而不是拼一张表
    --------------------------------
    三类统计的**列结构互斥**：`updown` 是「日 × 2 个计数」、`industry` 是
    「行业 × 2 个计数 + 涨跌幅」、`sentiment` 是「单行 × 若干指标」。
    服务端也没有任何一条命令给全市场概览。把它们硬拼成一张表只能靠造
    统一列名（`metric`/`value` 之类），那属于「硬造」，与红线冲突 ——
    所以 `all` 明确抛 `NotSupportedError`，并在消息里列出三个可用 `kind`。

    成本提醒（限流按**来源**计数，见 `限流防护.md`）
    ----------------------------------------------
    * `updown` —— **2 次**调用（先 `sys_fn_jyr_v3` 取当前交易日，再查统计）。
    * `sentiment` —— **1 次**。
    * `industry` —— **1 + N 次**（N = 一级行业码个数，`hyfx_sy` 模式 2 一次只吃一个一级行业码；
      **实网确认就是 30 个一级码（X10..X99）→ 1 + 30 = 31 次**，
    默认间隔 2.2s → 约 70 秒。这是本模块最贵的一条，调用方要知道）。
      这是唯一带「上涨家数/下跌家数」的通道，所以没得选；
      当期无数据的行业会被跳过并记进 `provenance.extra["skipped_codes"]`。

    认识不到 `kind` → `ValueError`（消息里列出全部合法取值）。
    """
    k = _as_text(kind)
    k = "" if k is None else k.lower()

    if k == "all":
        raise NotSupportedError(
            "get_market_stat(kind='all')：**三类统计的列结构互斥，没有单条命令"
            "能给全市场概览**（updown=日×2 计数；industry=行业×2 计数+涨跌幅；"
            "sentiment=单行多指标），拼成一张表只能靠造列名，属硬造数据，本库不做。"
            "请用 updown / industry / sentiment 之一（**默认 updown**）。"
        )
    if k not in MARKET_STAT_KINDS:
        raise ValueError(
            "get_market_stat(kind=%r) 不认识。合法取值：%s（其中 all 目前"
            "没有通道，会抛 NotSupportedError）。"
            % (kind, " / ".join(MARKET_STAT_KINDS))
        )

    if k == "updown":
        return _market_updown(client)
    if k == "sentiment":
        return _market_sentiment(client)
    return _market_industry(client)


def _market_updown(client) -> Table:
    """涨跌停统计（`ph_datacenter_zdt_zdfx`，30 行 × 3 列）。"""
    date = _current_trade_date(client)
    params: list[Any] = ["zdtj", date, 0, ""]
    raw = client.call(_CMD_ZDT_ZDFX, _CTL_ZDT_ZDFX, params)
    table = _shape(
        raw, _ZDT_MAP,
        cols=_STAT_COLS["updown"] + ("date",),
        cmd=_CMD_ZDT_ZDFX, domain=_DOMAIN_STAT,
        meta={"params": params, "trade_date": date,
              "source_note": "ph_datacenter_zdt_zdfx（zdtj 子码，日期必须带横线）"},
    )
    for row in table.rows:
        row["date"] = _norm_date(row.get("stat_date"))
    table.set_cols(list(_STAT_COLS["updown"]) + ["date"])
    # 阶段 4：丢掉 100% 为 None 的**结构性死列**（并记账）。
    # 作用域：只有 get_boards 与 market_stat 这三类；ztqd 清单**不适用**（见 boards 注释）。
    return _drop_empty_cols(table)


def _market_sentiment(client) -> Table:
    """市场热度 / 牛熊情绪（`ph_pcwebcall_hy_bkldscnx` 视图 002，1 行 × 12 列）。"""
    # ⚠️ 实网（2026-10-25）：视图 002 + 日期留空 → ServerError -1005。
    #    文档写的"空=最新"对这个视图不成立，必须给一个真实交易日。
    date = _current_trade_date(client)
    params: list[Any] = [_BKLDSCNX_VIEW, date]
    raw = client.call(_CMD_BKLDSCNX, _CTL_BKLDSCNX, params)
    table = _shape(
        raw, _BKLDSCNX_MAP,
        cols=_STAT_COLS["sentiment"],
        cmd=_CMD_BKLDSCNX, domain=_DOMAIN_STAT,
        meta={
            "params": params,
            "trade_date": date,
            "source_note": "ph_pcwebcall_hy_bkldscnx 视图 002（市场热度/牛熊情绪）",
            "note": "日期由 sys_fn_jyr_v3 现取；传空串实测 -1005（文档『空=最新』对本视图不成立）",
            "source_calls": 2,
            "unmapped_raw_columns": ["N003（风险免责长文本）", "N004~N011（8 个分项，名字未破解）"],
        },
    )
    return _drop_empty_cols(table)


def _map_or_serial(client, fn, items):
    """把一批**彼此独立**的取数跑完，结果**按 `items` 原序**返回（**恒为串行**）。

    名字里的 "map" 只是为了保留既有接缝，**不代表并发**：2026-10 起本库
    不再提供任何形式的并发取数（实测配额按来源 IP 算，多开连接换不来多份
    配额，且旧实现带永久死锁 —— 见 `Client` 模块 docstring 的实测表）。
    `Client._map_independent` 现在是纯串行 `[fn(x) for x in items]`。

    拿不到这个入口（测试里的假 client、`Client.__new__` 裸对象）→ 本地老实串行。
    两条路径的行为现在**完全一致**：遇错即停、按原类型上抛。

    ⚠️ 结果顺序 = 输入顺序。
    """
    runner = getattr(client, "_map_independent", None)
    if callable(runner):
        return runner(fn, items)
    return [fn(x) for x in items]


def _market_industry(client) -> Table:
    """行业涨跌 + 涨跌家数。

    路径（**两次组合，为什么**）：
      1. `hyfx_sy` **模式 1**（`[1,"H01"]`）取一级行业目录 → 拿到 `X10…X99` 这 30 个
         一级行业码。模式 2 只吃**一级**码，传二级码（`X1001`）会回 0 行，
         所以必须先取目录，不能凭印象写死。
      2. 对每个一级行业码发一次 **模式 2**（`[2,"X10"]`），回该一级行业下
         各层级成员的 8 列明细（含 `change_pct`、`up_count`、`down_count`）。
    合计 **1 + N 次**调用。数据本身没法一次取全（服务端就是这么设计的），
    所以把成本写进 docstring，而不是偷偷返回一张不完整表。

    成本与顺序
    ----------
    这 30 个一级码的明细**彼此独立**，所以走 `_map_or_serial`；
    但它**恒为串行**（本库不再有并发取数 —— 实测配额按来源 IP 算，
    多开连接换不来多份配额，且旧实现带永久死锁）。
    结果一律**按 `codes` 原序**拼装；
    `skipped_codes` / `calls` / `truncated` / `params_dir` / `level1_codes`
    等 meta 一个字节都不变（黄金样本 `market_stat_industry` 会直接照出漂移）。
    """
    dir_raw = client.call(_CMD_HYFX_SY, _CTL_HYFX_SY,
                          [_HYFX_MODE_DIR, _HYFX_DIR_P2])
    codes: list[str] = []
    for row in dir_raw.rows:
        code = _as_text(_cell(dir_raw, row, "N001"))
        if code and _LV1_RE.match(code) and code not in codes:
            codes.append(code)
    if not codes:
        raise NotSupportedError(
            "%s 模式 1 没有回任何一级行业码（期望 X10/X11… 这种两位数字码），"
            "无法继续取行业明细。" % _CMD_HYFX_SY
        )

    truncated = len(codes) > _INDUSTRY_MAX_CODES
    if truncated:
        codes = codes[:_INDUSTRY_MAX_CODES]

    def _fetch_one(code: str):
        """取**一个**一级行业码的明细分表；当期无数据（`EmptyResult`）→ `None`（=跳过）。

        ⚠️ 只接住 `EmptyResult`（"列在、行 0"= 这个行业当期没数据）；
        其它异常（`ParamError`/`NotSupported`/`RateLimited`/断连）一律往外抛，
        **不许**因为改成并发就把异常吞掉或降级成"没数据"。
        """
        params: list[Any] = [_HYFX_MODE_DETAIL, code]
        try:
            raw = client.call(_CMD_HYFX_SY, _CTL_HYFX_SY, params)
        except EmptyResult:
            return None
        return _shape(
            raw, _HYFX_LV2_MAP,
            cols=_STAT_COLS["industry"],
            cmd=_CMD_HYFX_SY, domain=_DOMAIN_STAT,
            meta={"params": params, "level1_code": code},
        )

    # 30 个一级码**彼此独立** → 唯一的并发收益点（阶段 5）。
    # 恒为串行：一次只发一个请求，一个线程都不起。
    parts = _map_or_serial(client, _fetch_one, codes)

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    _meta_src = None                 # 第一份分表：拼接表的语义层 meta 从它继承
    for code, part in zip(codes, parts):
        if part is None:
            skipped.append(code)
            continue
        if _meta_src is None:
            _meta_src = part
        rows.extend(part.rows)

    # 服务端 `N005` 给的是 `20261023` 紧凑写法（实网确认）→ 统一成 `YYYY-MM-DD`
    # 与 `market_stat("updown")` 的 `date` 口径一致，调用方不用自己判格式。
    for row in rows:
        row["date"] = _norm_date(row.get("date"))

    if not rows:
        # 红线：绝不用空表冒充"没数据"。逐行业都空 = 当期无数据 → EmptyResult
        raise EmptyResult(
            "hyfx_sy 模式 2 对 %d 个一级行业码（%s）全部返回 0 行，判定为当期无数据。"
            % (len(codes), " ".join(codes[:6]) + ("…" if len(codes) > 6 else ""))
        )

    # 逐行业拼接出来的表也必须带上语义层出处：以**第一份**分表的 provenance 为准
    # （同一轮里陆续取的，`as_of`/`status` 同源；`calls` 里另有真实请求次数）
    merged = Table(
        cols=list(_STAT_COLS["industry"]),
        rows=rows,
        cmd=_CMD_HYFX_SY,
        domain=_DOMAIN_STAT,
    ).inherit_provenance(_meta_src, **{
            "params_dir": [_HYFX_MODE_DIR, _HYFX_DIR_P2],
            "level1_codes": codes,
            "calls": 1 + len(codes),
            "skipped_codes": skipped,
            "truncated": truncated,
            "source_note": "hyfx_sy 模式 1（一级目录）+ 模式 2（逐一级行业明细）",
            "note": "本命令族唯一带「上涨家数/下跌家数」的表；模式 2 只吃一级行业码",
    })
    # 继承来的 `params` / `level1_code` 描述的是**某一个**一级行业（来自第一份分表），
    # 放在拼接表上会变成「整张表只取了一个行业」的假陈述 —— 整表的参数口径看
    # `params_dir` / `level1_codes`。这两个旁证必须删掉，不许留。
    prov = merged.provenance
    if prov is not None:
        clean = {k: v for k, v in prov.extra.items()
                 if k not in ("params", "level1_code")}
        merged.provenance = prov._replace_for(extra=clean)
    return _drop_empty_cols(merged)
