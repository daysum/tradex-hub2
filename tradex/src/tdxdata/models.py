# -*- coding: utf-8 -*-
"""返回模型：全部是 dataclass，字段名与服务中心返回的列名对齐。

设计取向（与"返回 dict"的区别）：
  · **可静态检查**：IDE 能补全，拼错字段名当场报错
  · **可序列化**：`dataclasses.asdict()` 一行转 dict / JSON
  · **可扩展**：原始行保留在 `raw` 里，标准字段不够用时不会丢信息
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, is_dataclass
from dataclasses import dataclass, field
from typing import Any

from ._provenance import Provenance, ClockSource
from .timeliness import parse_yyyymmdd

__all__ = [
    "Bar", "MinutePoint", "Table", "Row", "Dataset", "ProductInfo",
    "Quote", "Tick", "SecurityInfo",
    "Series", "Bars", "Quotes", "Ticks", "Minutes",
    "Provenance", "ClockSource",
]


@dataclass(slots=True)
class Bar:
    """一根 K 线。

    **单位（实测；`vol` 已于 2026-10-27 归一化）**
      · `open/high/low/close` —— 元
      · `vol`    —— **手**（唯一单位，见下）
      · `amount` —— 元（各品类、各周期实测一致）
      · `time`   —— 分钟线为 `HH:MM`；日线及以上为空串
      · `date`   —— `YYYYMMDD`；分钟线同一天有多根，靠 `time` 区分
      · `adjust` —— 本根用的是哪种复权口径（`NONE`/`QFQ`/`HFQ`/`FIELD8_4`）

    ⚠️ **`vol` 恒为「手」**（2026-10-27 口径变更，此前是"日线股票=股、周线=手"混着来）。
    服务端原始单位其实是**随品种和周期变**的：

      · 股票 / ETF：日线与分钟线是**股**，周/月线是**手**（= 日线/100）
      · 可转债 / 回购 / 指数 / 板块：日线本来就是**手**，周/月线是"手/100"

    `client.get_bars()` 在出口按 `_vol_to_hand()` 的实测表统一换算成手，
    **原始值原样留在 `raw["Volume"]` 里**（那里仍是服务端的原单位，直接读要小心）。
    换算不四舍五入，所以可能出现小数（如 `10588.72` 手）。
    ⚠️ **指数/板块的分钟线 `vol` 恒为 `None`**：服务端那一列是 `amount/100` 的**派生值**，
    不是成交量（实测 999999 / 880903 / 899050 的分钟线 `amount/vol` **恰好 100.000**，
    而 `amount` 与当日日线合计的比值是 1.0000 ⇒ amount 真、vol 是造的；
    999999 的 60m 若按手讲是 2.86 万亿股/小时，不可能）。本库红线：**拿不到就给 `None`**。
    该品类**日线**的 `vol` 是**真值**，照常提供。
    """
    market: int
    code: str
    date: str                 # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    vol: float | None = None  # 手（归一化后；原值见 raw["Volume"]；指数/板块分钟线为 None）
    amount: float | None = None  # 元
    # DEF-011 终局（2026-10-28 用户拍板）：`vol`/`amount` 为 `None` = **服务端没给/解析不出**
    # （unknown ≠ 0 红线）；服务端明确给 0 → 0.0。缺列事实另记在容器 `provenance.extra["missing_cols"]`，
    # `raw` 保留服务端原貌（缺键即缺列）。
    period: str = "day"
    time: str = ""            # HH:MM，仅分钟线
    raw: dict[str, Any] = field(default_factory=dict)
    adjust: str = "NONE"      # 复权口径，见 enums.Adjust / hq_pb.ADJUST_*
    #: 本根是不是**盘中未定稿**的当根值（当根日线 / 当根分钟）。
    #: 判定规则见 `tdxdata/timeliness.py`（`judge_bar`），口径写在 `enums.Status`。
    #: ⚠️ 必须留在**最后**：`slots=True` 下位置参数会变，插在中间会把既有调用错位。
    provisional: bool = False

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)

    @property
    def vol_unit(self) -> str:
        """`vol` 的单位，**恒为 `"手"`**（2026-10-27 归一化后不再随品种/周期变）。"""
        return "手"


@dataclass(slots=True)
class MinutePoint:
    """分时点（240 点/天）。

    **单位（实测）**：`price`/`avg_price` 元；`vol` —— **手**
    （与 `Bar.vol` 现在**同单位**了：2026-10-27 起 `Bar.vol` 也归一化成手，
    所以 240 点 `vol` 合计 = 当日 `Bar.vol`，不再差 100 倍）。
    """
    code: str
    time: str                 # HH:MM
    price: float
    avg_price: float
    vol: float = 0.0
    market: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


@dataclass(slots=True)
class Quote:
    """一行报价。两个来源共用：`get_quotes`（`PBCombHQ`）与 `get_market_page`/`get_universe`（`PBMultiHQ`）。

    字段单位（**都是实网对拍过的，不是猜的**）：

    ============= ============================================================
    `price`       **元**（`NOW` 列）
    `pre_close`   **元**（`CLOSE` 列；本通道 `CLOSE` 是**昨收**，不是当日收盘）
    `open/high/low` **元**（`OPEN`/`MAX`/`MIN` 列）
    `vol`         **手**（1 手 = 100 股；2026-10-27 起与 `Bar.vol` **同单位**，可直接比）
    `amount`      **元**（与 `Bar.amount` 同口径）
    `bid`/`ask`   **恒为空列表**：协议层没有五档（`PBCombHQ` 25 列里也没有 bid/ask）
    ============= ============================================================

    `vol` 单位是「手」的铁证（2026-10-27 09:25:22 集合竞价撮合后，同一交易日 600519）：
    `get_quotes` 给 `vol=210`，当日 `get_bars` 给 `vol=21000`，比值正好 **0.01**；
    而 `amount` 两边都是 `28,221,480`（比值 1.0）。自洽校验：
    `28,221,480 / 21,000 = 1343.88` = 当日收盘价 —— 若把 210 当"股"，这笔账根本算不平。

    两个来源的差别（**两条通道 `vol` 都是「手」、`amount` 都是「元」**）：
      · `get_quotes`（`PBCombHQ`）—— **按代码**取，`field4` 就是代码列表，
        并显式多要 `VOL`/`AMOUNT`/`LB` 三列，所以 `vol`/`amount` 有真值；
      · `get_market_page` / `get_universe`（`PBMultiHQ`）—— 按市场码**翻页列表**，
        默认 `cols` 就带 `AMOUNT`（所以 `amount` 有值）；`VOL` 默认没要，
        要 `vol` 就把它加进 `cols`。**这两列现在都会进 `Quote`**
        （2026-10-27 修掉的 bug：以前值躺在 `raw` 里、对外却报 `0.0`）。
      · 两个来源都是：**没给该列 → `None`**（不拿 0 冒充"没有这一列"）。

    `PBMultiHQ` 的 `VOL` 也是「手」的算术验证（2026-10-27，688252 天德钰）：
    `21164 手 × 100 × 21.18 ≈ 44,825,352`，与同一行 `AMOUNT = 43,914,168` 同口径
    （差 2% 是同刻不同字段的取舍）；若 `VOL` 是「股」，`21164 × 21.18 = 448,254`，
    与成交额差 100 倍，对不上。

    ⚠️ 历史更正（2026-10-27）：
      - 本节旧文案写"`Client.get_quotes(symbols)` 仍然抛 `NotSupportedError`" —— **已过时**，
        `PBCombHQ` 打通后 `get_quotes` 可用；
      - 旧文案写"`vol`/`amount` 默认 0" 且暗示"拿不到" —— 现在 `get_quotes` **能拿到**，
        只是缺列时给 `None`；
      - `vol`/`amount` 的**默认值已从 `0.0` 改为 `None`**：`0.0` 会被读成"成交量为零"，
        而真含义是"该来源没给这个字段"。顺带修掉 `get_market_page` 的同类 bug
        （真值在 `raw` 里、对外报 0），两条通道现在都如实传值。
    """
    market: int
    code: str
    price: float
    pre_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    #: 成交量，单位 **手**（×100 = 股）。**`None` = 该来源没给这一列**（不是"没有成交"）
    vol: float | None = None
    #: 成交额，单位 **元**。**`None` = 该来源没给这一列**（不是"没有成交"）
    amount: float | None = None
    bid: list[tuple[float, float]] = field(default_factory=list)
    ask: list[tuple[float, float]] = field(default_factory=list)
    server_time: str = ""
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


#: **声明式不可用**字段（机器可读，不靠 docstring 记性）。
#:
#: 为什么要有这个常量：光在文档里写"不提供"没人能自动检查；写成常量以后
#: `tests/test_tick_fields.py` 可以对着**真实形状的样本**断言「它确实恒为 None」，
#: 而且一旦服务端哪天开始给这个字段，那条断言会红 —— 逼我们更新声明。
#: 这就是把"死字段"变成"有检查的不可用"。
UNPROVIDED_TICK_FIELDS: dict[str, str] = {
    "bs_flag": ("服务端无可判定买卖方向的字段：实测 raw[InOutFlag] 的 0/1 与"
                "相对上一笔涨跌只有 65.9%/76.8% 一致（相关 ≠ 实证），"
                "映射错会把整批方向搞反，故不映射；原始列仍在 tick.raw 里"),
    "trade_count": ("`PBTickEx` 的**一行就是一笔**成交，协议里没有「笔数」字段。"
                    "这个字段恒为 None —— 早前填 0 是凭空造数（会被 sum() 当真）；"
                    "要数笔数请自己 `len(ticks)`"),
}


@dataclass(slots=True)
class Tick:
    """一笔**逐笔成交**（`HQServ:PBTickEx`，2026-10-27 打通）。

    **单位**（实网复核）：`price` 元；`vol` **手**（与 `Bar.vol` / `Quote.vol` /
    `MinutePoint.vol` **同单位**）；`time` 为 `HH:MM:SS`（服务端给的是**当日秒数**，
    例如 `40978` → `11:22:58`）。

    **`bs_flag` 是「声明式不可用」，不是"忘了填"**
    （机器可读的清单见模块级 `UNPROVIDED_TICK_FIELDS`）：
    服务端**没有**可判定买卖方向的字段，所以它恒为 `None`。
    早前给它填 2 是**编数据**（`bs_flag=2` 会被下游读成"卖"），已改掉：
    **拿不到就是 `None`，不填 0 冒充**（本库红线）。

    为什么保留而不是删掉：这个**概念**是真的，用户会来找它；服务端只是不给。
    删掉会让既有读 `.bs_flag` 的脚本 `AttributeError` —— 为了一个本来就拿不到的
    东西制造破坏。`None` 才是诚实的"不知道"。

    ⚠️ **`trade_count` 同为「声明式不可用」（阶段 4 判定，最终保留字段）**：
    `PBTickEx` 一行就是**一笔**成交，协议里**根本没有"笔数"字段**。
    它恒为 `None`——早前填 0 会被 `sum()` 当真、是凭空造数。
    **为什么保留而不删**：冻结的黄金样本证明既有行为本来就是 `null`（不是 0），
    所以删掉**没有任何诚实性增益**，却要为它赔一次样本重录 + 让既有读
    `t.trade_count` 的脚本报 `AttributeError`。要数笔数直接 `len(ticks)`。
    （判定依据见 `UNPROVIDED_TICK_FIELDS`；「永远为 None 的字段」由
    `tests/test_tick_fields.py` 的声明式断言管，服务端哪天开始给值该断言会红。）

    方向相关的原始列在 `raw` 里，自己看、自己判：
      * `InOutFlag`：实测 4 个取值。`0` 相对上一笔涨 65.9%、`1` 跌 76.8%
        （**只是相关**，13%~34% 对不上，所以本库不映射）；
        `5` 的 20 笔**全在 15:05–15:30 且成交价 = 收盘价** = 盘后固定价格交易；
        `2` 零星（含 15:00:01 收盘集合竞价那笔）。
      * `HKTTFlag`：`InOutFlag` 的**单字节回声**（300/300 相等，会是 `\x01` 这种控制字符）。
      * `HKInOutFlag` 实测恒 `"0"`；`VolInStockDiff` 与 `NowVol` 只 77/300 相等 → 不是重复列。
    这些**语义未定**，原样放在 `raw` 里，本库不解释也不删。
    """
    market: int
    code: str
    time: str | None = None   # HH:MM:SS（拿不到就是 None）
    price: float | None = None
    vol: float | None = None  # 手
    trade_count: int | None = None    # **声明式不可用**：见 UNPROVIDED_TICK_FIELDS
    bs_flag: int | None = None       # **声明式不可用**：见 UNPROVIDED_TICK_FIELDS
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)

    #: 逐字段说明（README 的字段表**从这里生成**，别手改 README 那一节）。
    #: 形状：(字段, 含义, 是否有值, 依据/口径)
    #: ⚠️ **不能写类型注解**：`@dataclass(slots=True)` 会把带注解的类属性当成字段
    #: 收进 `__slots__`，`Tick.FIELDS_DOC` 就变成一个 member_descriptor 了（实测踩过）。
    FIELDS_DOC = (
        ("market", "市场号", "有", "0=深 1=沪 2=北（与 `Quote`/`Tick.symbol` 同口径）"),
        ("code", "股票代码", "有", "6 位字符串"),
        ("time", "成交时刻", "有", "`HH:MM:SS`；服务端给当日秒数（如 40978 → 11:22:58）"),
        ("price", "成交价", "有", "元；与 `raw[Now]` 逐笔相等（300/300 实测）"),
        ("vol", "成交量", "有", "**手**；与 `Bar.vol`/`Quote.vol`/`MinutePoint.vol` 同单位"),
        ("trade_count", "笔数", "**不提供（声明式不可用）**",
         "原因见 `models.UNPROVIDED_TICK_FIELDS`：`PBTickEx` 一行就是一笔，"
         "协议里没有「笔数」字段；要数笔数请 `len(ticks)`"),
        ("bs_flag", "买卖方向", "**不提供（声明式不可用）**",
         "原因见 `models.UNPROVIDED_TICK_FIELDS`：服务端无可判定字段，"
         "`raw[InOutFlag]` 0/1 与涨跌只有 65.9%/76.8% 相关，映射会整批搞反"),
        ("raw", "原始行", "有",
         "服务端原列：`Second`/`Now`/`NowVol`/`VolInStockDiff`/`InOutFlag`/"
         "`HKTTFlag`/`HKInOutFlag`，本库不解释"),
    )

    @classmethod
    def as_markdown_table(cls) -> str:
        """`Tick` 字段表（markdown）——README 那一节是它的**逐字副本**。

        为什么由代码生成：字段"有没有值"是**事实**，会随实证变化（`bs_flag` 就从
        "恒 2" 变过 "恒 None"）。手写表格必然漂移，而漂移的文档比没有文档更坏。
        """
        out = ["| 字段 | 含义 | 有值？ | 依据 / 口径 |", "|---|---|---|---|"]
        for name, mean, has, why in cls.FIELDS_DOC:
            out.append("| `%s` | %s | %s | %s |" % (name, mean, has, why))
        return "\n".join(out)


@dataclass(slots=True)
class SecurityInfo:
    """证券基础信息（代码表元素）。"""
    market: int
    code: str
    name: str = ""
    volunit: float = 0.0
    decimal_point: int = 2
    pre_close: float = 0.0

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


class Series(list):
    """带 `provenance` 的 **list 子类**：`for` / `len` / 切片 / 索引 / `== [...]` 全兼容。

    ARCH-002（破坏性，无兼容期）：旧 `meta` dict 公开面已移除，出处凭据改为
    **一等字段** `provenance`（`Provenance` frozen dataclass，见
    `tdxdata/_provenance.py`）。语义访问走一等属性：
    `bars.status` / `bars.as_of` / `bars.fetched_at` / `bars.source` /
    `bars.elapsed` / `bars.clock`。

    为什么是 list 子类，而不是"新的信封类型"
    ----------------------------------------
    语义层要给每次取数挂上「这是什么时刻的、是不是最终值」。做成
    `{"data": [...], "provenance": {...}}` 这种信封会**破坏**所有既有用法：
    `len(x)`、`x[0]`、`for bar in x`、`x[-1].close`、`json.dumps(x)` 全得改。
    所以出处挂在**实例属性**上：`isinstance(bars, list)` 仍为真。

    ⚠️ **不要**给它加 `@dataclass(slots=True)`：list 子类要能挂实例属性，
    `slots` 会把这个能力拿掉。

    ⚠️ `provenance.status == FINAL` **只表示"这个口径定稿了"**，
    不表示"今天全市场成交都在里面"（日线口径不含盘后固定价格交易，
    详见 `tdxdata/timeliness.py` 模块头）。
    """

    def __init__(self, iterable=(), provenance: Provenance | None = None):
        super().__init__(iterable)
        self.provenance = provenance if isinstance(provenance, Provenance) else None

    # -- 切片/复制要**保住类型与 provenance** ------------------------------
    def __getitem__(self, key):
        """`bars[10:]` 仍返回 `Bars`（而不是退化成 `list`），并**带着同一份出处**。

        为什么必须管：切片是最常用的取子集方式（`bars[-60:]` 算 60 日均线之类）。
        若切片退化成 `list`，用户拿到的子集就**悄悄丢了 `status`/`as_of`** ——
        而"这批数是不是定稿的"恰恰是切完片之后才要判的事。
        出处是 frozen dataclass，多个容器共享同一份实例是安全的（不可变）。
        """
        got = super().__getitem__(key)
        if isinstance(key, slice):
            return type(self)(got, provenance=self.provenance)
        return got

    def copy(self):
        """`copy()` 也保住类型与 provenance（`list.copy()` 会退回普通 `list`）。"""
        return type(self)(self, provenance=self.provenance)

    # -- 运算也要保住类型与 provenance（DEF-023，2026-10-28）-----------------
    # 缺陷形态：`bars + [...]` / `bars * 2` / `sorted(bars)` 走的是 `list.__add__`
    # / `list.__mul__` / `list(iterable)`，全部退化成**裸 list** —— 与切片是同一个病：
    # 语义层信息（status/as_of/source）在一步运算后悄悄蒸发。
    def _carry_prov(self, items, *, extra_note: str | None = None):
        """把运算结果重新装回**本类型**，provenance 继承操作数。

        出处不可变，直接共享即可（零拷贝且无串号风险）。附加说明走
        `with_notes()` 造**新** Provenance（仅当需要说明时）。
        """
        prov = self.provenance
        if extra_note:
            prov = prov.with_notes(extra_note) if prov is not None else None
        return type(self)(items, provenance=prov)

    def __add__(self, other):
        """`bars + [x, y]` 仍是 `Bars` 且带出处；右侧同型 Series 时出处取左操作数。"""
        return self._carry_prov(super().__add__(other))

    def __radd__(self, other):
        """`[x] + bars`：左侧普通 list 优先，落到这里时保住本类型与出处。"""
        return self._carry_prov(list(other) + list(self))

    def __mul__(self, n):
        """`bars * 2` 仍是 `Bars` 且带出处（重复运算不改变语义凭据）。"""
        return self._carry_prov(super().__mul__(n))

    def __rmul__(self, n):
        return self.__mul__(n)

    def __iter__(self):
        """照旧逐元素迭代 —— 但要小心：`sorted(self)` 走的是 `list(self)` 收集元素，
        然后 `list.sort()` **就地排序**并返回同一个普通 list，provenance 就丢了。
        所以在这里没法救 `sorted()`；真正的修法是给 Series 加 `__sorted__` 协议
        并覆盖 `sorted` 的行为不可行（内建不可覆写），改为在本类上提供同名方法：
        `sorted()` 内建仍会退化 —— **契约上**以 `bars.sorted()` 为准（见下）。"""
        return super().__iter__()

    def sorted(self, *, key=None, reverse: bool = False):
        """`bars.sorted()`：与内建 `sorted()` 同义，但**保住类型与出处**。

        为什么不给内建 `sorted()` 打补丁：内建函数的收值路径是 `list(iterable)`
        + `list.sort()`，返回的永远是裸 `list`，且 `sorted` 不可被实例级覆写 ——
        这是 Python 语言边界，不是本类的缺陷。所以 Series 家族提供**同义方法**：
        用户的 `sorted(bars)` 语义照旧（裸 list，不丢数据只丢出处），
        要带出处请用 `bars.sorted()`。`__iter__` 保持普通逐元素迭代不受影响。
        """
        return self._carry_prov(sorted(self, key=key, reverse=reverse))

    # -- 便捷只读属性（出处没记就是 None，**不编默认值**）------------------
    @property
    def as_of(self):
        """这批数据"是什么时候的"（末根的日期/时间，或报价的 `server_time`）。"""
        return self.provenance.as_of if self.provenance is not None else None

    @property
    def fetched_at(self):
        """这批数据"是什么时候取的"（aware datetime，UTC+8）。"""
        return self.provenance.fetched_at if self.provenance is not None else None

    @property
    def status(self):
        """时效四态（`enums.Status` 的取值字符串，没判出就是 `None`）。"""
        return self.provenance.status if self.provenance is not None else None

    @property
    def source(self):
        """来源三元组 `(host, cmd, ctl)`（精简形态；时钟在 `.clock`，缓存在 `.cached`）。"""
        if self.provenance is None:
            return None
        return (self.provenance.host, self.provenance.cmd, self.provenance.ctl)

    @property
    def elapsed(self):
        """这次调用实际花了多少秒（缓存命中≈0）。"""
        return self.provenance.elapsed if self.provenance is not None else None

    @property
    def clock(self):
        """这批数据的**时刻来源**：`ClockSource` 枚举（SERVER / HYBRID / LOCAL）。

        ⚠️ 这是**类型锁的一等字段**，不是自由文本：LOCAL 就意味着 `fetched_at`
        不可全信，时效判定会自动降级成更保守的一档（见 `tdxdata/timeliness.py`）。
        """
        return self.provenance.clock if self.provenance is not None else None

    @property
    def notes(self):
        """说明性文本元组（时钟降级原因、缺列、口径说明……都在这里）。"""
        return self.provenance.notes if self.provenance is not None else ()

    def to_dict(self) -> dict[str, Any]:
        """转**可 JSON 序列化**的 dict：`{"data": [...], "provenance": {...}}`（DEF-024）。

        与直接 `json.dumps(bars)` 的区别就在 provenance：`Series` 是 list 子类，
        JSON 序列化器看不见实例属性，直接 dumps 会丢；
        走这里出处凭据（status/as_of/fetched_at/…）一并带出。
        """
        return {"data": [_jsonable(v) for v in self],
                "provenance": _jsonable(self.provenance.to_dict())
                if self.provenance is not None else None}

    def to_json(self, *, indent=None, **kw) -> str:
        """`json.dumps` 可直接吃；datetime/date ISO 化，provenance 一并带出。"""
        return _dumps(self.to_dict(), indent=indent, **kw)

    def __repr__(self) -> str:
        return "%s(%d 条, provenance=%r)" % (type(self).__name__, len(self),
                                             self.provenance)


class Bars(Series):
    """K 线序列（`get_bars` 的返回）。元素是 `Bar`。"""


class Quotes(Series):
    """报价序列（`get_quotes` / `get_market_page` / `get_universe` / `board_page`）。"""


class Ticks(Series):
    """逐笔成交序列（`get_ticks`）。元素是 `Tick`。"""


class Minutes(Series):
    """当日分时序列（`get_minutes`）。元素是 `MinutePoint`。"""


class Row(dict):
    """一行数据：**既是 dict，也能按位置取**。

    为什么不是普通 dict
    -------------------
    7615 回包给的是**按位置排列的值列表**，而使用者想的是**按列名取**。
    两边都保留才不会有人踩坑：

    ```python
    row["N004"]     # 按列名（推荐）
    row[3]          # 按位置（等价于 row[cols[3]]）—— 协议层口径
    row.get("name", "—")
    dict(row)       # 干净转普通 dict，可 json.dumps
    ```

    它是 `dict` 的子类，所以 `isinstance(row, dict)` 为真、可 `json.dumps`、
    `**row` 展开都照常可用。位置下标只额外接 `int`（越界抛 `IndexError`）。
    """
    __slots__ = ("_cols",)

    def __init__(self, cols=(), values=None, **kwargs):
        super().__init__()
        self._cols = list(cols)
        if values is not None:
            for i, name in enumerate(self._cols):
                dict.__setitem__(self, name, values[i] if i < len(values) else None)
        if kwargs:
            dict.update(self, kwargs)

    # -- 位置访问（协议层口径）--------------------------------------------
    def __getitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            try:
                name = self._cols[key]
            except IndexError:
                raise IndexError("行只有 %d 列，取不到第 %d 列" % (len(self._cols), key)) from None
            return dict.__getitem__(self, name)
        return dict.__getitem__(self, key)

    def __setitem__(self, key, value):
        if isinstance(key, int) and not isinstance(key, bool):
            try:
                key = self._cols[key]
            except IndexError:
                raise IndexError("行只有 %d 列，写不到第 %d 列" % (len(self._cols), key)) from None
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            key = self._cols[key]
        dict.__delitem__(self, key)

    def __contains__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return 0 <= key < len(self._cols)
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        if isinstance(key, int) and not isinstance(key, bool):
            if 0 <= key < len(self._cols):
                return dict.get(self, self._cols[key], default)
            return default
        return dict.get(self, key, default)

    @property
    def cols(self) -> list[str]:
        """这一行绑定的列名（与 `Table.cols` 同源）。"""
        return list(self._cols)

    def to_dict(self) -> dict:
        """转普通 dict（去掉位置下标能力，纯数据）。"""
        return dict(self)

    def __repr__(self) -> str:
        return "Row%r" % (dict(self),)


def _make_row(cols, raw):
    """把「值列表」或「dict」统一成 `Row`（位置与列名都可用）。

    已经是 `Row` 的话，**位置口径必须以所在表的 `cols` 为准**：
    列清单变了就重绑一份新的（不复用原对象 —— 同一行可能被两张表共享，
    原地改 `_cols` 会**改坏另一张表**的位置下标）。
    """
    if isinstance(raw, Row):
        if list(raw._cols) == list(cols):
            return raw
        row = Row(cols)
        dict.update(row, raw)
        return row
    if isinstance(raw, dict):
        row = Row(cols)
        dict.update(row, raw)
        return row
    return Row(cols, list(raw))


@dataclass(slots=True)
class Table:
    """一张结果表：列名 + 行（每行是一个 `Row`，即 dict，也支持位置取）。

    这是 7615 数据中心产品的**统一返回类型**。`cols` 来自协议（多为 `N001…`），
    含义见 `docs/VERIFICATION.md` 的「未定字段」清单。

    重要：**`cols` 与每一行的取值严格对齐**。服务端回多个 ResultSet 时，
    `spec/06 §2.5.7` 的"列数"口径是并集，而并集通常比主表宽 ——
    并集放在 `provenance.extra["cols_union"]`，`cols` 只放**主表自己的列名**，
    否则 `zip(cols, row)` 会把整行错一位（静默错列）。

    ARCH-002：旧 `meta` dict 公开面已移除，出处凭据是一等字段 `provenance`
    （`Provenance` frozen dataclass）。语义访问走 `table.as_of` / `table.status` /
    `table.provenance.notes` / `table.provenance.extra[...]`。
    """
    cols: list[str]
    rows: list[dict[str, Any]]
    cmd: str = ""
    domain: str = ""
    provenance: Provenance | None = None

    def __post_init__(self):
        self.cols = list(self.cols)
        self.rows = [_make_row(self.cols, r) for r in (self.rows or [])]
        if self.provenance is not None and not isinstance(self.provenance, Provenance):
            raise TypeError(
                "provenance 必须是 Provenance 实例，收到 %r —— "
                "旧 meta dict 公开面已按 ARCH-002 移除，出处一律走 Provenance"
                % (type(self.provenance).__name__,))

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)

    @property
    def shape(self) -> tuple[int, int]:
        """(行数, 列数)。"""
        return len(self.rows), len(self.cols)

    # -- 语义层便捷属性（只读，读 `provenance`）---------------------------
    @property
    def as_of(self):
        """这张表的数据时刻（判不出来就是 `None`，不编）。"""
        return self.provenance.as_of if self.provenance is not None else None

    @property
    def status(self):
        """时效四态（没登记过时效口径的表不会是 `final`）。"""
        return self.provenance.status if self.provenance is not None else None

    def to_dicts(self) -> list[dict[str, Any]]:
        """转普通 dict 列表（可直接 `json.dumps`）。"""
        return [dict(r) for r in self.rows]

    def to_json(self, *, indent=None, **kw) -> str:
        """整表转 JSON 字符串：`{cols, rows, cmd, domain, provenance}`（DEF-024）。

        `to_dicts()` 是行数据出口，但**丢出处**；走这里
        语义层（as_of/status/fetched_at/…）一并带出，datetime/date ISO 化。
        """
        return _dumps(self.to_dict(), indent=indent, **kw)

    def to_dict(self) -> dict[str, Any]:
        """整表转 dict（`to_json` 的数据形态，provenance 一并带出）。"""
        return {"cols": list(self.cols),
                "rows": self.to_dicts(),
                "cmd": self.cmd,
                "domain": self.domain,
                "provenance": (_jsonable(self.provenance.to_dict())
                               if self.provenance is not None else None)}

    def column(self, name: str) -> list[Any]:
        """取一整列。`name` 也可以是列下标（`int`）。"""
        if isinstance(name, int) and not isinstance(name, bool):
            name = self.cols[name]
        return [r.get(name) for r in self.rows]

    def row(self, index: int) -> dict[str, Any]:
        """取第 `index` 行（返回 `Row`：按列名/按位置都能读）。"""
        return self.rows[index]

    def set_cols(self, cols) -> "Table":
        """改列清单，并**重绑所有行的位置口径**（返回 `self` 便于链式）。

        ⚠️ **改列清单请一律用这个方法，别写 `t.cols = [...]`。**
        直接赋值只会改表头的 `cols`，行的 `row[0]` 还按旧列清单解释 ——
        表现为「按名取值对、按位置取值错」。本仓踩过一次（`boards.py` 收尾时
        把 3 列补成 4 列，结果 `row[0]` 读到的还是旧的第 0 列）。
        """
        self.cols = list(cols)
        self.rows = [_make_row(self.cols, r) for r in self.rows]
        return self

    def inherit_provenance(self, *sources, **extra) -> "Table":
        """**继承上游 provenance**，再叠本次的说明/旁证（就地改，返回 `self`）。

        为什么要有它（而不是在每个调用点手工搬出处）
        ------------------------------------------------------
        `sections/*` 里的 `_shape()` 会**重建**一张 `Table`（换标准列名、补 `raw`）。
        重建时若不带出处，上游 `client.call()` 刚盖上去的
        `as_of` / `status` / `fetched_at` / `elapsed` / 来源就**全丢了** ——
        这正是复核者判的那条阻断（`sections/boards.py` 的收尾重建点）。
        所以做成一个**单点**工具：所有"由 `Table` 派生出 `Table`"的地方都调它。

        出处是 frozen dataclass：继承 = 共享同一份实例（零拷贝、不可串号）；
        `**extra` 是本次的结构化旁证（如 `cols_union` / `template_check` /
        `product`），合进 `provenance.extra` —— `extra` 里值为 `None` 的键**不收**
        （缺席 = 没记）。字符串键也会进 `notes`。
        `sources` 里可以有 `Table`、任何带 `.provenance` 的对象（如 `Series`/`Bars`）、
        也可以直接给 `Provenance`；靠前的先铺、靠后的覆盖，最后 `extra` 覆盖旁证。

        用法::

            return Table(cols=…, rows=…).inherit_provenance(src, product="gsjj")
        """
        base: Provenance | None = None
        for src in sources:
            if isinstance(src, Provenance):
                base = src
                continue
            p = getattr(src, "provenance", None)     # Table / Series / Bars …
            if isinstance(p, Provenance):
                base = p
        if base is not None:
            base = base.with_extra(**extra) if extra else base
        elif extra:
            # 没有上游出处也允许先记旁证：fetched_at 用"现在"（UTC+8）、LOCAL 钟如实标注
            from datetime import datetime as _now_dt
            base = Provenance(fetched_at=_now_dt.now().astimezone(),
                              clock=ClockSource.LOCAL).with_extra(**extra)
        self.provenance = base
        return self


@dataclass(slots=True)
class Dataset:
    """一个数据产品（= 某条命令 + 一组参数）。`Table` 的带元信息版本。"""
    name: str
    table: Table
    params: Any = None

    def __len__(self) -> int:
        return len(self.table.rows)

    def __iter__(self):
        return iter(self.table.rows)

    def to_dict(self) -> dict[str, Any]:
        """`{name, params, table: {...}}`（DEF-024；meta 在 table 里一并带出）。"""
        return {"name": self.name, "params": _jsonable(self.params),
                "table": self.table.to_dict()}

    def to_json(self, *, indent=None, **kw) -> str:
        """整个数据产品转 JSON 字符串（datetime/date ISO 化，meta 完整）。"""
        return _dumps(self.to_dict(), indent=indent, **kw)

    def to_dicts(self) -> list[dict[str, Any]]:
        """行数据出口（代理 `table.to_dicts()`，注意它**不含 meta**）。"""
        return self.table.to_dicts()

    @property
    def cols(self) -> list[str]:
        return self.table.cols

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self.table.rows

    @property
    def data_date(self):
        """这张表的**数据日**（`datetime.date`）；判不出来就是 `None`。
        取值顺序：
          1. `table.provenance.data_date`（谁知道的谁写，最可靠）；
          2. 从首行 `N001` 推 —— 7615 数据中心产品的第一列惯例就是日期
             （`"20261027"` / `"2026-10-27"` 两种形态都认）。

        ⚠️ **推不出来就给 `None`**：绝不许拿 `0`、空串或"今天"顶替。
        本项目红线是 **unknown ≠ 0** —— "不知道是哪天的"和"今天的"是两件事，
        混成一个会让下游把过期数据当今日数据用（那正是本项目出过事故的形态）。
        """
        prov = self.table.provenance
        meta_date = parse_yyyymmdd(prov.data_date) if prov is not None else None
        if meta_date is not None:
            return meta_date
        rows = self.table.rows
        if not rows:
            return None
        first = rows[0]
        try:
            got = first.get("N001")
        except AttributeError:                    # 不是 dict 形态（理论上不会发生）
            return None
        return parse_yyyymmdd(got)


@dataclass(slots=True)
class ProductInfo:
    """产品元信息（`products()` 返回的元素）。"""
    name: str
    cmd: str
    ctl: int
    domain: str = ""
    params: Any = None

    @property
    def ctl_hex(self) -> str:
        return "0x%02X" % self.ctl


# ══════════════════════════════════════════════════════════════════════════
# 序列化出口（DEF-024，2026-11）
#
# 缺陷形态：`json.dumps(Bars/Table/Dataset)` 直接 `TypeError`
# （`Bar` 是 slots dataclass、`Series` 是带实例属性的 list 子类），
# 唯一出口 `Table.to_dicts()` 又**不带 meta** —— 语义层（status/as_of/source）
# 一序列化就蒸发。
#
# 方案：每个容器统一提供 `to_dict()`（dataclass→dict，meta 一并带出）与
# `to_json()`（`json.dumps` 可直接吃，datetime/date → ISO 8601）。
# 纯标准库，零第三方依赖。
# ══════════════════════════════════════════════════════════════════════════

def _jsonable(value: Any) -> Any:
    """递归把值变成 `json.dumps` 能吃的东西。

    · `datetime` / `date` → ISO 8601 字符串（带 `T` 分隔）；
    · `set` / `frozenset` / `tuple` → `list`（保持顺序；set 先 sorted 保稳定）；
    · slots dataclass → `asdict()` 再递归；
    · bytes → `latin-1` 损失最小化的字符串？不 —— 直接 `utf-8, replace`，
      解不出就给 hex，绝不编；
    · 其它（str/int/float/bool/None）原样。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        out = {k: _jsonable(v) for k, v in asdict(value).items()}
        # 只读 `@property`（如 `symbol`）不在 `asdict()` 里 —— 但它们是
        # 派生事实（symbol_of），序列化时一并带出，消费方不用重算。
        for name in dir(type(value)):
            if name.startswith("_") or name in out:
                continue
            attr = getattr(type(value), name, None)
            if isinstance(attr, property):
                try:
                    out[name] = _jsonable(getattr(value, name))
                except Exception as exc:                   # noqa: BLE001
                    # 派生失败的属性不带（丢字段事件，debug 留轨迹：
                    # 哪个类的哪个属性派生不出来，通常是半初始化对象）。
                    from ._logging import logger as _lg

                    _lg.debug("property %r of %s not derivable (%s)",
                              name, type(value).__name__,
                              type(exc).__name__, exc_info=True)
        return out
    if hasattr(value, "provenance"):
        # Series 家族（list 子类，非 dataclass）：data + provenance（ARCH-002）
        prov = value.provenance
        return {"data": [_jsonable(v) for v in value],
                "provenance": _jsonable(prov.to_dict()) if prov is not None else None}
    return repr(value)            # 兜底：绝不抛 TypeError，但也不冒充原值


def _dumps(obj: Any, *, indent=None, ensure_ascii=False, **kw) -> str:
    import json as _json
    return _json.dumps(_jsonable(obj), indent=indent,
                       ensure_ascii=ensure_ascii, **kw)


def symbol_of(market: int, code: str) -> str:
    """(market, code) → `sh600519`。"""
    p = {1: "sh", 0: "sz", 2: "bj"}.get(int(market), "")
    return p + str(code)
