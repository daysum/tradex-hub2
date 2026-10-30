# -*- coding: utf-8 -*-
"""时效判定：把"这个数是什么时候的、是不是最终值"写成**可测试的代码**。

为什么需要这个模块
------------------
本项目唯一被实证造成过**错误结论**的缺陷就是时效：同一交易日内，14:44 盘中取
两市成交额 13,130.88 亿，收盘后是 14,609.58 亿（+11.3%），"60 日最低"的结论直接反转。
根因不是算错，而是**拿盘中的数当收盘数**。

所以规则必须落成代码（可注入假时钟、可断言边界），而不是散落在各处的注释里。

两个必须守住的口径
------------------
1. **判定一律用「传入的服务器时刻」，不许在判定里调 `datetime.now()`。**
   假时钟能注入，才谈得上"边界可断言"。
2. **拿不到就降级并标注，绝不冒充 `FINAL`。** 不知道上一交易日时降级成
   `PROVISIONAL`/`DELAYED`（较保守的那个），并在 `note` 里写明原因。
   `meta.status=FINAL` 才有资格被下游当成"可以拿去做结论"。

⚠️ **`FINAL` 只表示"这个口径定稿了"，不表示"今天全市场成交都在里面"。**
「收盘口径的日线」与「含盘后固定价格交易的真实全市场成交额」是**两个口径**：
日线通道给不了含盘后的量（`Bar.raw` 的 12 个键里没有任何字段能区分盘后成交，
逐笔的 `InOutFlag=5` 才标得出来）。要那个口径只能自己走逐笔聚合。

它会落进哪几个字段（形状是契约，别改）
--------------------------------------
判定结果本身进 `enums.Status`（四态）；**依据**进返回容器的 `provenance`
（ARCH-002：旧 `meta` dict 公开面已移除，出处是一等字段 `Provenance`）：

```
provenance.status       = "final" | "provisional" | "pending" | "delayed"
provenance.as_of        = 数据时刻（末根 bar 的日期/时间，或报价的 server_time）
provenance.extra["provisional"] = bool（K 线容器专有：末根是否盘中未定稿）
provenance.data_date    = 数据日（Dataset.get() 专有；从首行 N001 推，推不出为 None）
provenance.clock        = ClockSource.SERVER | HYBRID | LOCAL   ← 类型锁，不是自由字符串
provenance.notes        = ("local-clock-fallback", ...) 等说明性文字（**不承载唯一信息**）
provenance.fetched_at   = 取数时刻（aware datetime，UTC+8）
provenance.elapsed      = 本次调用耗时（秒，缓存命中≈0）
```

本模块**纯标准库、纯函数、零网络**（探针由 `Client` 注入，可换成 `FakeClock`）。
"""
from __future__ import annotations

from datetime import date, datetime, time as _time, timedelta
from typing import Callable, NamedTuple, Optional, Protocol, runtime_checkable

from .enums import Status

try:                                    # ARCH-002：Provenance 可用即接受
    from ._provenance import Provenance
except ImportError:                     # pragma: no cover（环形导入防护）
    Provenance = None

__all__ = [
    "CLOSE_CUTOFF", "FINAL_CUTOFF_HHMM", "MIN_BAR_CUTOFF",
    "NowInfo", "Clock", "FakeClock", "ServerClock",
    "parse_hhmmss", "parse_yyyymmdd", "parse_hhmm",
    "is_intraday_period", "period_minutes", "bar_period_end",
    "judge_bar", "judge_intraday", "judge_quote", "judge_market_stat",
    "judge_product", "judge_unregistered", "probe_or_none",
    "PRODUCT_EVIDENCE", "UNREGISTERED_NOTE",
]

#: **未登记时效口径**时给用户的解释（`status_note` / `timeliness()["note"]`）。
#: 措辞必须点明"我们**没有**判定它，只是按非定稿保守处理" —— 否则用户会
#: 误读成"库认为它是盘中值"，那是另一种假结论。
UNREGISTERED_NOTE = ("该命令在 421 产品台账里没有时效声明，无法判定，"
                     "按**非定稿**保守处理（不是「我们判定它是盘中值」）")


# ══════════════════════════════════════════════════════════════════════════
# 一、三个时刻常量
# ══════════════════════════════════════════════════════════════════════════
# 当日日线（vol/amount/OHLC）的 FINAL 判定时刻。
# 依据（2026-10-27 收盘后四通道互证，Lead 已亲验 E1）：
#   · 内容层面 15:00 就定稿：日线 vol == 分时 240 点合计
#     （实测 688111 差 0.47 手、600519 差 0.14 手，纯取整）；
#     盘后固定价格成交（逐笔 InOutFlag=5，15:05–15:29）确实存在但**不进日线**
#     （占日线量 0.013%–0.119%，逐票都在日线之外）。
#   · 但服务端写当日快照最晚到 15:33:57（40 只 HqTime 样本，创业板那批），
#     所以不能卡 15:00、也不能卡 15:30:00 —— 撞写入窗口。
#   · 取 15:34 之后 + 缓冲 = 15:35。方向性理由：晚 35 分钟成本确定，
#     早宣布 FINAL 的成本是结论反转+返工（本项目已发生过一次）。
CLOSE_CUTOFF = _time(15, 35)

#: 给人看的同一件事（`Client.timeliness()` 的 `close_cutoff` 字段用它）。
FINAL_CUTOFF_HHMM = "15:35"

#: 分钟周期的收盘边界 —— 15:00 之后当日所有分钟周期都已走完。
#:
#: 更细的判定不靠这个常量：**某根分钟 bar 是否走完**由
#: `bar_period_end(bar)`（= `bar.time`，服务端给的本来就是**周期结束时刻**：
#: 实测 5m 首根 09:35、末根 15:00；60m 是 10:30/11:30/14:00/15:00）与服务器时刻比。
#: 这个常量只用在"整天的分钟数据算不算定稿"这种粗判定上。
MIN_BAR_CUTOFF = _time(15, 0)

#: 周期名 → 分钟数（只用于把 `bar.time` 换算成周期长度做边界检查）。
_PERIOD_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60,
}

#: 日线及以上的周期（这些周期"当根"按 `CLOSE_CUTOFF` 判）。
_DAYLIKE = ("day", "week", "month", "season")

#: 已登记产品的**证据型**补充说明：写进 `status_note` / `timeliness()["note"]`，
#: 让"为什么这么判"能被人复核，而不是只给一个枚举值让人信。
PRODUCT_EVIDENCE = {
    "ztqd": ("涨停清单（ztqd）的 00102 口径当日就有行"
             "（实测 2026-10-27 当日 41 行；00101/00102 是不同清单口径、日期是独立参数位），"
             "所以盘后仍只给到上一交易日 = **真的滞后**，不是「还没生成」"),
}


# ══════════════════════════════════════════════════════════════════════════
# 二、解析（解析不了就 None，**绝不猜**）
# ══════════════════════════════════════════════════════════════════════════
def parse_hhmmss(s) -> Optional[_time]:
    """`"153003"` / `94258` / `"09:42:58"` → `time`；解析不了返回 `None`。

    ⚠️ 服务端的 `HqTime` **前导 0 会被吃掉**（上午 09:42:58 给的是 `"94258"`，
    00:05:09 给 `"509"`）—— 纯数字且 ≤ 6 位就左补 0，这与 `client._stamp` 同口径。
    长度 > 6、或含非数字、或数值越界 → `None`（不猜、不抛，交给调用方降级）。
    """
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        hh, mm, ss = (int(p) for p in parts)
    else:
        if not text.isascii() or not text.isdigit() or len(text) > 6:
            return None
        p = text.zfill(6)
        hh, mm, ss = int(p[:2]), int(p[2:4]), int(p[4:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return _time(hh, mm, ss)


def parse_hhmm(s) -> Optional[_time]:
    """`"15:00"` / `"1500"` / `"150000"` → `time`（秒=0）；解析不了返回 `None`。"""
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            hh, mm = int(parts[0]), int(parts[1])
        elif len(parts) == 3:
            got = parse_hhmmss(text)
            return None if got is None else got
        else:
            return None
    else:
        if not text.isascii() or not text.isdigit() or len(text) not in (4, 6):
            return None
        hh, mm = int(text[:2]), int(text[2:4])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return _time(hh, mm)


def parse_yyyymmdd(s) -> Optional[date]:
    """`"20261027"` / `20261027` / `"2026-10-27"` → `date`；解析不了返回 `None`。

    ⚠️ **不许拿今天/0 顶替**：本项目红线是"unknown ≠ 0"。解析不出来就 `None`，
    由调用方降级（`Dataset.data_date` 也按这条走）。
    """
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    text = str(s).strip()
    if not text:
        return None
    try:
        if "-" in text:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        if "/" in text:
            return datetime.strptime(text[:10], "%Y/%m/%d").date()
        if not text.isascii() or not text.isdigit() or len(text) != 8:
            return None
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════
# 三、周期工具
# ══════════════════════════════════════════════════════════════════════════
def is_intraday_period(period) -> bool:
    """是不是分钟周期（`1m/5m/15m/30m/60m`）。日线及以上返回 `False`。"""
    return str(period or "").strip().lower() in _PERIOD_MINUTES


def period_minutes(period) -> Optional[int]:
    """分钟周期的长度（几分钟）；不是分钟周期返回 `None`。"""
    return _PERIOD_MINUTES.get(str(period or "").strip().lower())


def bar_period_end(bar) -> Optional[_time]:
    """该根所属周期的**结束时刻**（本地钟面）；判不出来返回 `None`。

    服务端的 `Second` 换算出来的 `Bar.time` 就是**周期结束时刻**（实测：
    5m 首根 `09:35`、末根 `15:00`；60m 是 `10:30 / 11:30 / 14:00 / 15:00`），
    所以这里直接用 `bar.time`。日线及以上没有 `time`（空串）→ `None`。

    ⚠️ 保守加固：如果哪天服务端改成给"周期开始时刻"，`bar.time` 会**早**一个周期。
    所以这里在拿到标签后**再加一个周期长度**取上界，并封顶在 `MIN_BAR_CUTOFF`
    —— 方向永远是"宁可晚宣布 FINAL"，不会因为标签口径变化而提前宣布定稿。
    """
    label = parse_hhmm(getattr(bar, "time", "") or "")
    if label is None:
        return None
    minutes = period_minutes(getattr(bar, "period", ""))
    if not minutes:
        return label
    total = label.hour * 60 + label.minute + minutes
    cap = MIN_BAR_CUTOFF.hour * 60 + MIN_BAR_CUTOFF.minute
    total = min(total, cap)
    return _time(total // 60, total % 60)


# ══════════════════════════════════════════════════════════════════════════
# 四、时刻来源（可注入假时钟）
# ══════════════════════════════════════════════════════════════════════════
class NowInfo(NamedTuple):
    """一次"现在几点"的结果。

    `clock` 的三个合法取值：

    | 值 | 含义 | 可信 |
    |---|---|---|
    | `"server"` | 纯服务端时刻（`ServerClock`，保留兼容） | ✅ |
    | `"hybrid"` | **服务器日期 + 本机时分**，且已通过"行情已收盘"交叉校验 | ✅ |
    | `"local"` | 本机时钟兜底（探针失败 / 用户关掉探针 / 日期对不上） | ❌ |

    `market_closed` 是**第三个事实源**：指数 `999999` 的 `HqTime` 是否已到收盘
    （`>= 15:00`）。它是 `True/False/None`：
      · `True` —— 市场自己的最后成交时刻已过收盘；
      · `False` —— **本机说已收盘、行情说没到** → 自相矛盾，一律保守处理；
      · `None` —— 不知道（`FakeClock` / 纯 `datetime` 走这条），**不额外收紧**。

    `note` 非空时**必须**让下游看到（它是"这个时刻不可全信"的唯一线索）。
    """
    at: datetime
    clock: str = "server"
    note: str = ""
    market_closed: Optional[bool] = None


#: `clock` 里算"可信"的取值（`judge_*` 用它决定要不要降级）。
TRUSTED_CLOCKS = ("server", "hybrid")


@runtime_checkable
class Clock(Protocol):
    """时钟协议：只要一个 `now() -> NowInfo`。"""

    def now(self) -> NowInfo:
        ...


class FakeClock:
    """固定时刻的假时钟（单测用）。

    只做一件事：把"现在"钉死，让 `15:34:59 / 15:35:00` 这种边界能被断言。
    """

    def __init__(self, at: datetime, *, clock: str = "server", note: str = ""):
        if not isinstance(at, datetime):
            raise TypeError("FakeClock 需要 datetime（收到 %r）" % (type(at).__name__,))
        self._at = at
        self._clock = clock
        self._note = note

    def now(self) -> NowInfo:
        return NowInfo(self._at, self._clock, self._note)

    # -- 方便写用例 --------------------------------------------------------
    def set(self, at: datetime) -> "FakeClock":
        """改时刻（同一个假时钟可以演一遍"盘中 → 收盘"）。"""
        self._at = at
        return self

    def __repr__(self) -> str:
        return "<FakeClock %s %s>" % (self._at.isoformat(sep=" "), self._clock)


def _local_now() -> datetime:
    """本机时钟（只在**兜底**时用，且一定会被标注 `clock="local"`）。"""
    return datetime.now()


def probe_or_none(fn: Callable[[], object]):
    """跑一次**可选**探针；任何失败都返回 `None`。

    ⚠️ 这里的 `except BaseException` 是**刻意**的，理由不是偷懒：
    时刻/交易日都属于**元信息**，元信息取不到必须降级，绝不能让一次取数因为
    "问不到几点"而失败。而 `tests/golden_runner.py` 的 `FrameMissing` **刻意继承
    `BaseException`**（为了让 `pb_api._raw_send` 的 `except Exception` 吞不掉它），
    回放时探针必然撞上它 —— 用 `except Exception` 反而会退化成"白等 4 轮 × 8 秒"。
    `KeyboardInterrupt / SystemExit / GeneratorExit` 照常上抛。
    """
    try:
        return fn()
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:                                   # noqa: BLE001
        return None


class ServerClock:
    """生产用时钟：**服务器时刻优先**，拿不到才退回本机时钟并标注。

    不重复取
    --------
    `probe()` 每次都是一次真实网络往返（默认走一条便宜的行情查询），
    逐次取会把取数速度砍半。所以这里做**锚点 + 单调增量**：
    拿到一次服务器时刻后，用 `time.monotonic()` 的增量往前推，
    锚点超过 `ttl` 秒（默认 1800）才重新取一次。

    这不是"编时间"：锚点是服务端的，漂移量是同一台机器上的单调时钟增量
    （秒级），而本模块只拿它做"跨没跨过 15:35"这种**分钟级**判断。
    真的跨了一整天（`|增量| > 12h`）时直接作废锚点重取，避免跨日错位。

    探针失败后 `fail_ttl` 秒内**不再重试**（避免每次取数都白等一轮超时）。
    """

    def __init__(self, probe: Callable[[], Optional[datetime]] | None = None,
                 *, ttl: float = 1800.0, fail_ttl: float = 60.0,
                 mono: Callable[[], float] | None = None,
                 local: Callable[[], datetime] | None = None,
                 note: str = ""):
        self._probe = probe
        self._ttl = float(ttl)
        self._fail_ttl = float(fail_ttl)
        self._mono = mono or _mono_now
        self._local = local or _local_now
        self._note = note                      # 例如"未初始化的 Client"这类附加说明
        self._anchor: Optional[datetime] = None
        self._anchor_mono = 0.0
        self._retry_after = 0.0

    # -- 主入口 ------------------------------------------------------------
    def now(self) -> NowInfo:
        mono = self._mono()
        if self._anchor is not None and (mono - self._anchor_mono) < self._ttl:
            return NowInfo(self._anchor + timedelta(seconds=mono - self._anchor_mono),
                           "server", self._note)
        if self._probe is None or mono < self._retry_after:
            return self._fallback("无可用探针" if self._probe is None else "探针刚失败，冷却中")
        try:
            got = self._probe()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                    # noqa: BLE001  见 probe_or_none
            # 探针的异常要**连类型一起记下来**：`note` 是排查唯一线索
            self._anchor = None
            self._retry_after = mono + self._fail_ttl
            return self._fallback("探针抛 %s: %s" % (type(exc).__name__, exc))
        if not isinstance(got, datetime):
            self._anchor = None
            self._retry_after = mono + self._fail_ttl
            return self._fallback("探针没拿到服务器时刻（返回 %r）"
                                  % (type(got).__name__,))
        if abs((got - self._local()).total_seconds()) > 12 * 3600:
            # 服务端与本机相差超过 12 小时 → 大概率是锚点跨日/时区错，作废后仍按本次结果用
            self._retry_after = mono + self._fail_ttl
        self._anchor = got
        self._anchor_mono = mono
        return NowInfo(got, "server", self._note)

    def _fallback(self, why: str) -> NowInfo:
        note = "local-clock-fallback: %s" % why
        if self._note:
            note = "%s; %s" % (note, self._note)
        return NowInfo(self._local(), "local", note)

    # -- 可观测性 ----------------------------------------------------------
    @property
    def anchored(self) -> bool:
        """当前是否持有服务端锚点（排查用）。"""
        return self._anchor is not None


class HybridClock:
    """**双源交叉校验时钟**：日期用服务器、时分用本机、再用行情做交叉校验。

    为什么必须这么做（这条缺陷是实测抓到的，不是设想的）
    --------------------------------------------------
    7615 的 `HqTime` **不是"现在几点"，而是"最后成交时刻"**：收盘后它冻结不动。
    实测 2026-10-27：`999999` 的 `server_time` 在 16:37 与 17:29 两次取样都是
    `15:30:03`（50 分钟不前进）；最晚的 `000001` 也只到 `15:33:06`。
    而"当日日线定稿"的门槛是 `CLOSE_CUTOFF = 15:35` —— 于是
    **`server_now` 永远跨不过 15:35，当日日线整晚恒为 `PROVISIONAL`**，
    `FINAL` 那一支在现实中不可达。方向安全，但**不可用**：
    `status` 存在的意义就是"收盘后能确认这是最终值"，结果它永远说"可能还会变"。

    但也不能拿本机时钟顶替服务器时钟（那会破红线：本机漂了就误判 FINAL）。
    所以拆开用 —— 每个事实只问**最可信的那个源**：

    | 要问的事 | 问谁 | 理由 |
    |---|---|---|
    | **今天是哪一天** | 服务器 `HqDate` | 服务器自己的交易日，不受本机时钟影响 |
    | **现在几点** | 本机时分 | `HqTime` 会冻结，不能当时钟用 |
    | **市场收盘了吗** | 服务器 `HqTime >= 15:00` | 指数不可能在收盘前给出"最后成交时刻 ≥ 15:00" |

    于是 `at = HqDate + 本机 time-of-day`，并且**必须**通过两道校验：

    * (A) 时间条件：`at >= CLOSE_CUTOFF`；
    * (B) 行情条件：`market_closed`（`HqTime >= 15:00`）；
    * 前置条件：**本机日期 == 服务器交易日**，否则两源都不能用 → `clock="local"`。

    (B) 存在的主要理由是**防本机时钟偏快**：本机说 16:10、指数 `HqTime` 却只有
    14:35，那说明本机钟不准，此时**不许**判 FINAL，并把"自相矛盾"写进 `note`。

    `HqTime` 会冻结这件事同时意味着它**不能单独当 `now`**：停牌/无成交的票
    可能停在很早的时刻，拿它判断"现在几点"会把它们永远判成 `PROVISIONAL`。
    当"市场是否已收盘"的**证据**却非常可靠 —— 这是它唯一该被用的地方。
    """

    def __init__(self, probe: Callable[[], object] | None = None,
                 *, ttl: float = 1800.0, fail_ttl: float = 60.0,
                 mono: Callable[[], float] | None = None,
                 local: Callable[[], datetime] | None = None,
                 cutoff: _time = CLOSE_CUTOFF,
                 close_mark: _time = MIN_BAR_CUTOFF,
                 note: str = ""):
        self._probe = probe
        self._ttl = float(ttl)
        self._fail_ttl = float(fail_ttl)
        self._mono = mono or _mono_now
        self._local = local or _local_now
        self._cutoff = cutoff
        self._close_mark = close_mark
        self._note = note
        #: 缓存的探针结果 `(服务器交易日, 最后成交时刻, 单调戳)`
        self._anchor: Optional[tuple[date, _time, float]] = None
        self._retry_after = 0.0
        self._why = ""                 # 上一次降级的原因（写进 `note`）

    # -- 主入口 ------------------------------------------------------------
    def now(self) -> NowInfo:
        """返回交叉校验过的 `NowInfo`；任何一环不成立就降级成 `local` + `note`。"""
        mono = self._mono()
        self._why = ""
        got = self._probe_pair(mono)
        if got is None:
            return self._fallback(self._why or "拿不到服务器交易日")
        server_date, last, _stamp = got

        local_now = self._local()
        if local_now.date() != server_date:
            # 日期对不上 → 两个源都不能用（本机可能错日，也可能服务器数据是隔夜的）
            return self._fallback(
                "本机日期 %s ≠ 服务器交易日 %s，两源不敢混用"
                % (local_now.date().isoformat(), server_date.isoformat()))

        at = datetime.combine(server_date, local_now.time())
        market_closed = last >= self._close_mark
        note = ""
        if at.time() >= self._cutoff and not market_closed:
            # (A) 成立、(B) 不成立 → 自相矛盾：本机钟偏快或行情卡住，一律保守
            note = ("时钟与行情自相矛盾，按保守处理：本机时分 %s 已过收盘 %s，"
                    "但指数最后成交时刻只有 %s（未到 %s）"
                    % (at.strftime("%H:%M:%S"), _hhmm(self._cutoff),
                       last.strftime("%H:%M:%S"), _hhmm(self._close_mark)))
            if self._note:
                note = "%s; %s" % (note, self._note)
            return NowInfo(at, "hybrid", note, False)
        if self._note:
            note = self._note
        return NowInfo(at, "hybrid", note, market_closed)

    def _probe_pair(self, mono: float):
        """取/复用 `(服务器交易日, 最后成交时刻)`；拿不到 `None`（原因写进 `_why`）。"""
        if self._anchor is not None and (mono - self._anchor[2]) < self._ttl:
            return self._anchor
        if self._probe is None:
            self._why = "没有可用探针（未注入 / 用户关了 clock_probe）"
            return None
        if mono < self._retry_after:
            self._why = "探针刚失败，冷却中"
            return None
        try:
            raw = self._probe()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                        # noqa: BLE001
            # 异常要**连类型一起记下来**：`note` 是排查唯一线索
            self._anchor = None
            self._retry_after = mono + self._fail_ttl
            self._why = "探针抛 %s: %s" % (type(exc).__name__, exc)
            return None
        pair = _as_date_time_pair(raw)
        if pair is None:
            self._anchor = None
            self._retry_after = mono + self._fail_ttl
            self._why = ("探针没拿到 (服务器交易日, 最后成交时刻)（返回 %r）"
                         % (raw,))
            return None
        self._anchor = (pair[0], pair[1], mono)
        return self._anchor

    def _fallback(self, why: str) -> NowInfo:
        note = "local-clock-fallback: %s" % why
        if self._note:
            note = "%s; %s" % (note, self._note)
        return NowInfo(self._local(), "local", note, None)

    # -- 可观测性 ----------------------------------------------------------
    @property
    def anchored(self) -> bool:
        """当前是否持有服务器日期锚点（排查用）。"""
        return self._anchor is not None


def _hhmm(t: _time) -> str:
    return "%02d:%02d" % (t.hour, t.minute)


def _as_date_time_pair(raw) -> Optional[tuple[date, _time]]:
    """探针返回值归一成 `(date, time)`：接受 `(d, t)` 元组 / `dict` / `datetime`。"""
    if isinstance(raw, datetime):
        return raw.date(), raw.time()
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        d = raw[0] if isinstance(raw[0], date) else parse_yyyymmdd(raw[0])
        t = raw[1] if isinstance(raw[1], _time) else parse_hhmmss(raw[1])
        if d is not None and t is not None:
            return d, t
        return None
    if isinstance(raw, dict):
        d = parse_yyyymmdd(raw.get("HqDate") or raw.get("date"))
        t = parse_hhmmss(raw.get("HqTime") or raw.get("time"))
        if d is not None and t is not None:
            return d, t
    return None


def _mono_now() -> float:
    import time as _t
    return _t.monotonic()


# ══════════════════════════════════════════════════════════════════════════
# 五、判定规则
# ══════════════════════════════════════════════════════════════════════════
def _bar_day(bar) -> Optional[date]:
    return parse_yyyymmdd(getattr(bar, "date", ""))


def _now_and_trust(server_now, clock_trusted: bool):
    """`server_now` **只**接受 `NowInfo` 或 `Provenance`（DEF-019 类型锁）。

    为什么要收 `NowInfo`：时钟来源（`server`/`hybrid`/`local`）是**判定的一部分**，
    不该由调用方"记得另传一个 flag"—— 忘了传就会把本机时钟当服务器时刻，
    正好踩本库红线。所以这里直接从 `NowInfo.clock` 推出可信度：
    `clock="local"` 时**自动**降级为不信任，调用方没有任何机会"忘记"。

    ARCH-002（DEF-019 终局）：**bare `datetime` 不再被接受** —— 裸 datetime
    携带不了"可信度"这个信息，旧版按 `clock_trusted=True` 默认放行是洞不是兼容。
    同理，字符串当场 `TypeError`（原来也是炸，口径没变）。`Provenance` 也收：
    它的 `clock` 是 `ClockSource` 枚举，可信度由类型保证。
    """
    if isinstance(server_now, NowInfo):
        trusted = (server_now.clock in TRUSTED_CLOCKS) and bool(clock_trusted)
        return server_now.at, trusted
    if Provenance is not None and isinstance(server_now, Provenance):
        trusted = (server_now.clock.value in TRUSTED_CLOCKS) and bool(clock_trusted)
        return server_now.fetched_at, trusted
    raise TypeError(
        "DEF-019：server_now 必须是 NowInfo 或 Provenance（收到 %r）——"
        "bare datetime 分不清服务器时刻与本机时刻，已被类型锁拒绝"
        % (type(server_now).__name__,))


def _market_conflict(server_now) -> bool:
    """行情与时钟**自相矛盾**吗（`market_closed is False` 才算）。

    只认**显式**的 `False`：`None`（FakeClock / 裸 datetime）表示"没有这个证据"，
    绝不能当成"行情没收盘"去收紧 —— 那会把已有的 396 条用例全部改成 PROVISIONAL。
    """
    return getattr(server_now, "market_closed", None) is False


def judge_bar(bar, *, server_now,
              prev_trade_date: Optional[date] = None,
              clock_trusted: bool = True) -> Status:
    """**一根 bar** 的时效。

    规则（顺序即优先级）：
      1. `bar.date` 解析不出来 → `PROVISIONAL`（不知道就别声称定稿）；
      2. 历史日期（`bar.date != 服务器当前日`）→ `FINAL`；
         ⚠️ 这一条**顺带把周末/休市处理掉了**：周六的 `server_now.date()` 是周六，
         末根 bar 是周五 → 进"历史"分支 → `FINAL`，不会被误判成"当根"。
         **所以本库不为判交易日引入节假日表。**
      3. 当日 + 日线及以上周期 + `server_now.time() < CLOSE_CUTOFF` → `PROVISIONAL`；
      4. 当日 + 分钟周期 + 该根周期未走完（`server_now.time() < bar_period_end`）
         → `PROVISIONAL`；
      5. 其余 → `FINAL`。

    `clock_trusted=False`（服务器时刻没取到、只能用本机时钟时）
    ----------------------------------------------------------
    **当日**的根一律降级成 `PROVISIONAL`：时钟本身不可信，就不能证明"今天这根
    已经收盘定稿了"。方向只能是更保守 —— 宁可晚宣布 FINAL，不可早宣布。
    （历史根不受影响：日子早于今天这件事，任何合理的钟都成立。）

    `prev_trade_date` 只作为"当日"分支的旁证（拿不到不影响判定），
    **本函数内部绝不调 `datetime.now()`**。

    `server_now` 传 `datetime` 或 `NowInfo` 都行；传 `NowInfo` 时**自动**按
    `clock` 字段决定可不可信（见 `_now_and_trust`）。
    """
    conflict = _market_conflict(server_now)      # 必须在转 datetime 之前取
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    day = _bar_day(bar)
    if day is None:
        return Status.PROVISIONAL                     # 日子都读不出来：不许声称定稿
    today = server_now.date()
    if day > today:
        # 根的日期**比"今天"还晚** → 至少有一个源是错的（时钟落后了，或者数据串了）。
        # 此时绝不能因为"day != today"就走进历史根那一支判 FINAL ——
        # 那正是"本机钟慢了一天，于是把今天的根当成昨天的根宣布定稿"的形态。
        return Status.PROVISIONAL
    if day != today:
        return Status.FINAL                           # 历史根恒 FINAL（含周末/休市）
    if not clock_trusted:
        return Status.PROVISIONAL                     # 时钟不可信 → 当日根不许定稿
    now_t = server_now.time()
    period = str(getattr(bar, "period", "") or "").strip().lower()
    if is_intraday_period(period):
        end = bar_period_end(bar)
        if end is not None and now_t < end:
            return Status.PROVISIONAL
        if end is None and now_t < MIN_BAR_CUTOFF:
            # 分钟根读不出 `time`：退回"整天是否走完"这一粗判据（保守方向）
            return Status.PROVISIONAL
        return Status.FINAL
    if period in _DAYLIKE or not period:
        if now_t < CLOSE_CUTOFF:
            return Status.PROVISIONAL
        if conflict:
            # 时间说"收盘了"、行情说"没到" → 两者不能同时为真，保守处理
            return Status.PROVISIONAL
        return Status.FINAL
    # 未登记的周期：保守
    return Status.PROVISIONAL


def judge_intraday(server_now: datetime, *, cutoff: _time = CLOSE_CUTOFF,
                   clock_trusted: bool = True, _conflict: bool = False) -> Status:
    """**当日连续序列**（分时 / 逐笔 / 报价快照）的时效。

    `cutoff` 之前一律 `PROVISIONAL` —— 这些数据本身就是"还在长"的。
    逐笔用默认的 `CLOSE_CUTOFF`（盘后固定价格成交到 15:29 还在进来，别提前说定稿）；
    分时可以用 `MIN_BAR_CUTOFF`（它的内容 15:00 就满了）。

    `clock_trusted=False` → 一律 `PROVISIONAL`（时钟不可信，就没有"已收盘"这回事）。
    `_conflict` 是内部参数：`judge_quote` 已经在类型锁转换前取过矛盾标记，
    摊平传进来，避免对裸 datetime 再调 `_market_conflict`（那会二次判型）。
    """
    if _conflict:
        conflict = True
    else:
        conflict = _market_conflict(server_now)
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    if not clock_trusted:
        return Status.PROVISIONAL
    if server_now.time() < cutoff:
        return Status.PROVISIONAL
    return Status.PROVISIONAL if conflict else Status.FINAL


def judge_quote(server_now: datetime, data_time: Optional[datetime] = None,
                *, clock_trusted: bool = True):
    """**一条报价快照**的时效：有 `server_time` 就按它判，没有就按"现在"判。

    ⚠️ 报价行**自带** `HqDate`+`HqTime`（服务端时刻），所以只要 `data_time` 有值，
    即使本机时钟不可信也能判：拿 `data_time` 和 `server_now` 的日子比即可。
    """
    conflict = _market_conflict(server_now)      # 必须在转 datetime 之前取
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    if data_time is None:
        return (Status.PROVISIONAL if not clock_trusted
                else Status.PROVISIONAL if server_now.time() < CLOSE_CUTOFF
                else Status.PROVISIONAL if conflict else Status.FINAL)
    if data_time.date() != server_now.date():
        return Status.FINAL
    return (Status.PROVISIONAL if not clock_trusted
            else Status.PROVISIONAL if server_now.time() < CLOSE_CUTOFF
            else Status.PROVISIONAL if conflict else Status.FINAL)


def judge_market_stat(kind: str, data_date, *, server_now: datetime,
                      prev_trade_date: Optional[date] = None,
                      clock_trusted: bool = True) -> Status:
    """**市场统计类**（`get_market_stat`）的时效。

    `kind="industry"` 是**按日 ×2 计数 / 按行业 ×2 计数**里最慢的那条：
    实测它的数据日是 `T-1`（行业统计当天出不来）。
      · 数据日 < 上一交易日 → `DELAYED`
      · 数据日 == 上一交易日 → `DELAYED`（就是 T-1 口径）
      · 数据日 == 当日 → 走 `CLOSE_CUTOFF` 判
      · 数据日读不出来 → `PROVISIONAL`/`DELAYED`（**不许**当 FINAL）
    拿不到上一交易日、或时钟不可信时，都落在较保守的那一档。
    """
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    d = parse_yyyymmdd(data_date)
    name = str(kind or "").strip().lower()
    today = server_now.date()
    if d is None:
        return Status.PROVISIONAL if name in ("updown", "sentiment", "all") else Status.DELAYED
    if d == today:
        if not clock_trusted:
            return Status.PROVISIONAL
        return Status.PROVISIONAL if server_now.time() < CLOSE_CUTOFF else Status.FINAL
    # 走到这里：数据日不是今天。市场统计这几条通道（updown / industry / sentiment）
    # 全是**收盘口径**，非当日即滞后：
    #   · `industry` 的 T-1 是这条通道的常态（行业统计当天出不来）；
    #   · 更早的数据日是**真的落后**；
    #   · `prev_trade_date` 拿不到时同样落在这一档（较保守的那一档）。
    # 三种情况结论相同，所以这里不再分支 —— 但**保留** `prev_trade_date` 入参：
    # 以后若要按"落后几天"分级（`delayed_1` / `delayed_n`），落点就在这里。
    # 绝不因为"不确定"就退回 FINAL。
    return Status.DELAYED


def judge_product(name: str, data_date, *, server_now,
                  prev_trade_date: Optional[date] = None,
                  clock_trusted: bool = True) -> Status:
    """**数据中心产品**的时效（按产品名登记，没登记的走 `judge_unregistered`）。

    已登记：
      · `lrmx` / `north`（两融 / 北向）—— **T+1**：数据日 == 上一交易日是常态 → `DELAYED`；
        数据日 == 当日且已过 `CLOSE_CUTOFF` → `FINAL`。
      · `ztqd`（涨停清单）—— 当日的清单要在收盘后才生成：盘中查 → `PENDING`
        （"今天的还没生成"，不是异常、不是空）。
    """
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    key = str(name or "").strip().lower()
    d = parse_yyyymmdd(data_date)
    today = server_now.date()
    now_t = server_now.time()

    if key in ("lrmx", "north"):
        if d is None:
            return Status.DELAYED                   # T+1 产品读不出数据日 → 不敢说定稿
        if d >= today:
            if not clock_trusted:
                return Status.PROVISIONAL           # 时钟不可信 → 不许说 FINAL
            return Status.PROVISIONAL if now_t < CLOSE_CUTOFF else Status.FINAL
        return Status.DELAYED                       # 昨天及更早 = T+1 常态

    if key in ("ztqd",):
        if d is None:
            return Status.PENDING                   # 今天那份还没生成
        if d >= today:
            if not clock_trusted:
                return Status.PENDING
            return Status.PENDING if now_t < CLOSE_CUTOFF else Status.FINAL
        return Status.PENDING if now_t < CLOSE_CUTOFF else Status.DELAYED

    return judge_unregistered(server_now=server_now if isinstance(server_now, NowInfo)
                              else NowInfo(server_now))


def judge_unregistered(*, server_now, clock_trusted: bool = True) -> Status:
    """**没登记时效口径**的产品/命令 → `PROVISIONAL`。

    这不是"猜它是最终值"，而是最保守的非定稿口径：下游看到 `PROVISIONAL`
    会知道"这个数我还没给它定性"，绝不会拿它当收盘定稿去下结论。
    （本库红线是 unknown ≠ 0，所以这里也不许出现"默认 FINAL"。）
    """
    server_now, clock_trusted = _now_and_trust(server_now, clock_trusted)
    return Status.PROVISIONAL          # 与时钟可不可信无关：没登记就永远是 PROVISIONAL
