# -*- coding: utf-8 -*-
r"""非价格序列登记表：**哪些 880/881 代码的 OHLC 不是价格**。

为什么需要这张表
----------------
`get_bars()` 的返回值叫 `Bar`，字段叫 `open/high/low/close` —— 这对价格序列是对的。
但通达信的 **880xxx / 881xxx** 里混着两类完全不同的东西：

  · **板块指数**（880903 水产品、880952、881007 油气开采…）—— OHLC **是价格**（点）；
  · **统计/广度序列**（880005 涨跌家数、880006 停板家数、880866、880641…）——
    OHLC 是**四个家数**或**指标点位**，"收盘价 1,317.00"这种读法会得出荒谬结论
    （"涨跌家数收盘 1317" —— 那是上涨家数 1317 家）。

同一条 `PBFXT` 命令、同一个解码路径，**返回形状一模一样**，所以库没法从数据本身
分辨"这根是价格还是家数"。唯一可靠的办法：把**已实证**的代码登记下来，
命中就**拒绝**并指路 —— 用"拒绝"换"正确"。

⚠️ **不许按 `880`/`881` 前缀一律拒绝**：880903 / 880952 / 881007 等是**真价格序列**的
板块指数，必须继续能用 `get_bars()` 取。前缀启发式会把它们一起误杀，
这正是本库反复踩过的"启发式代替事实"的坑。

⚠️ 本表**只登记已实证的 4 个代码**。没实证的一律不猜：未登记代码走 `get_bars()`
（当价格用）或 `call()` / `_engine` 原始通道，由调用方自己承担字段含义。
`get_breadth()` 对未登记代码**直接抛错**，绝不按位置猜含义。

`fields` 的语义
---------------
`{Bar 字段名: 标准字段名}`。取值时**只搬名字**，不做任何换算和猜测；
没出现在 `fields` 里的 `Bar` 字段就是"该序列没有定义的量"，会被记进
`Table.provenance.extra["unavailable"]`（例如 880006 只定义了 open/close，high/low 无意义）。

字段含义的实证来源见各条 `note`。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SeriesSpec", "NON_PRICE_SERIES",
    "get_spec", "is_non_price", "as_markdown_table", "render_redirect",
    "unavailable_fields",
]


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """一个非价格序列的登记项。"""

    code: str
    name: str
    #: `{Bar 字段名(open/high/low/close): 标准字段名}`，**插入顺序即输出列顺序**。
    fields: dict[str, str]
    #: 实证说明 / 已知坑（会原样进 `Table.provenance.notes` 与 README 表格）。
    note: str


#: 已实证的非价格序列（**只有这 4 个**，多一个都要先有实证）。
NON_PRICE_SERIES: dict[str, SeriesSpec] = {
    "880005": SeriesSpec(
        code="880005",
        name="涨跌家数",
        fields={
            "open": "down_count",     # 下跌家数
            "high": "total",          # 全市场总数
            "low": "flat_count",      # 平盘家数
            "close": "up_count",      # 上涨家数
        },
        note="四项之和不等于 total，差额为停牌/无成交（实测每日差 5–7 家）；"
             "⚠️ `raw` 里另有 `up`/`down` 两列，取值与 up_count/down_count 不同"
             "（实测同一根 435/2163 vs 606/4922），含义未实证，勿混用",
    ),
    "880006": SeriesSpec(
        code="880006",
        name="停板家数",
        fields={
            "close": "limit_up_count",    # 涨停家数
            "open": "limit_down_count",   # 跌停家数
        },
        note="其余字段无定义（high/low 不是「最高/最低停板家数」，只是占位）",
    ),
    "880866": SeriesSpec(
        code="880866",
        name="近期新低",
        fields={"close": "value"},
        note="带小数的**指标点位**，不是家数；open/high/low 无定义",
    ),
    "880641": SeriesSpec(
        code="880641",
        name="历史新低",
        fields={"close": "value"},
        note="同 880866：指标点位，不是家数；open/high/low 无定义",
    ),
}

#: `Bar` 里"价格四件套"的顺序（生成 `unavailable` 用）。
_PRICE_FIELDS = ("open", "high", "low", "close")

#: 标准字段名 → 计量单位（`render_redirect` 用它生成"是…而非价格"那半句）。
_UNITS = {
    "down_count": "家数",
    "up_count": "家数",
    "flat_count": "家数",
    "total": "家数",
    "limit_up_count": "家数",
    "limit_down_count": "家数",
    "value": "指标点位",
}

_COUNT_WORDS = {2: "两个", 3: "三个", 4: "四个"}


def _norm(code) -> str:
    """代码归一化：去空白、去 `sh`/`sz`/`bj` 前缀，只留本体。

    `symbols.parse()` 已经归一过一次；这里再兜一次是因为 `get_breadth()` 与
    `is_non_price()` 都是公开入口，可能被直接喂 `"sh880005"`。
    """
    s = str(code if code is not None else "").strip()
    low = s.lower()
    for pre in ("sh", "sz", "bj"):
        if low.startswith(pre) and len(low) > len(pre):
            return s[len(pre):].strip()
    return s


def get_spec(code) -> SeriesSpec | None:
    """按代码取登记项；**未登记返回 `None`**（不是抛错 —— 调用方要能"问一下"）。"""
    return NON_PRICE_SERIES.get(_norm(code))


def is_non_price(code) -> bool:
    """这个代码的 OHLC 是不是**非价格**（是 → `get_bars()` 应该拒绝）。"""
    return _norm(code) in NON_PRICE_SERIES


def _payload_phrase(spec: SeriesSpec) -> str:
    """生成"其 OHLC 是四个家数"这半句（对任意 spec 都成立，不写死）。"""
    units = {_UNITS.get(std, "数值") for std in spec.fields.values()}
    unit = units.pop() if len(units) == 1 else "指标值"
    n = len(spec.fields)
    if n <= 1:
        return unit
    return "%s%s" % (_COUNT_WORDS.get(n, "%d 个" % n), unit)


def _bar_fields_label(spec: SeriesSpec) -> str:
    """生成"其 **OHLC**"那半句：四个都映射了就叫 OHLC，否则逐个列出来。"""
    used = [f for f in _PRICE_FIELDS if f in spec.fields]
    if len(used) == len(_PRICE_FIELDS):
        return "OHLC"
    return "、".join(used)


def render_redirect(spec: SeriesSpec, *, attempted: str = "get_bars") -> str:
    """统一的**指路文案**（`get_bars` 拒绝时 / `get_breadth` 报错时都用它）。

    对任意 `spec` 都生成正确句子（不写死代码、不写死字段数）：

        880005 是「涨跌家数」指标，其 OHLC 是四个家数而非价格。
        价格序列请用 get_bars；广度序列请用 get_breadth("880005")。

    对 880866（只有 `close`）会生成：

        880866 是「近期新低」指标，其 close 是指标点位而非价格。
        价格序列请用 get_bars；广度序列请用 get_breadth("880866")。

    `attempted` 只影响**第三句**（"你刚才走的是谁"），前两句永远一样 ——
    因为"这个代码不是价格"这个事实与调用入口无关。
    """
    if not isinstance(spec, SeriesSpec):
        raise TypeError("render_redirect 需要 SeriesSpec（收到 %r）"
                        % (type(spec).__name__,))
    lines = [
        "%s 是「%s」指标，其 %s 是%s而非价格。" % (
            spec.code, spec.name, _bar_fields_label(spec), _payload_phrase(spec)),
        "价格序列请用 get_bars；广度序列请用 get_breadth(\"%s\")。" % spec.code,
    ]
    if attempted != "get_bars":
        lines.append("（刚才走的是 %s —— 它把非价格序列当价格返回，才需要这道拒绝。）"
                     % attempted)
    return "\n".join(lines)


def as_markdown_table() -> str:
    """把登记表渲染成 markdown —— **README 那一节与这里逐字同源**。

    `tests/test_series_kinds.py::test_readme_table_is_generated_from_registry`
    会拿 README 的标记块内容与这里比字符串相等，所以**任何人手改 README 那张表都会红**。
    """
    head = ("| 代码 | 名称 | 字段映射（`Bar` 字段 → 标准名） | 说明 |\n"
            "|---|---|---|---|")
    rows = []
    for code in sorted(NON_PRICE_SERIES):
        spec = NON_PRICE_SERIES[code]
        mapped = "、".join("`%s`→`%s`" % (bf, std) for bf, std in spec.fields.items())
        rows.append("| `%s` | %s | %s | %s |" % (code, spec.name, mapped, spec.note))
    return "\n".join([head] + rows)


def unavailable_fields(spec: SeriesSpec) -> list[str]:
    """该序列**没有定义**的 `Bar` 字段（按 open/high/low/close 顺序）。

    这些字段在 `Bar` 上有值（服务端总得填点什么），但**语义未定义** ——
    所以 `get_breadth()` 不把它们当列，只列进 `provenance.extra["unavailable"]`。
    """
    return [f for f in _PRICE_FIELDS if f not in spec.fields]
