# -*- coding: utf-8 -*-
r"""PROD-002：`c.calendar` —— **交易日历**（事实源两路，不维护节假日表）。

为什么查表是错的
----------------
本库**不维护节假日表**（那是第二份事实源，法定调休一变就错，错得还静默；
2026 国庆调休把 10/10 变成交易日，任何手抄表都会在下一个调休日背叛你）。
"某天开没开盘"这个问题只有**服务端的事实**能回答：有日线数据 = 开市。
两个取数通道，按问题形态选：

| 问题形态 | 通道 | 为什么 |
|---|---|---|
| 单点判定 / 取上一交易日 | `get_bars("999999", count=N)` 的日线日期序列 | 一次请求给最近 N 个交易日，离"今"最近 |
| **历史区间**（range 遍历） | `get_breadth("880005", count=N)` 的 `date` 列 | 广度序列**单日一行**，一次 N 根就覆盖 N 个**交易日**；而 `get_bars` 的 N 根同样是 N 个交易日，但广度表是**列名明确的日频事实**（`date` 列 + `provisional`），不需要碰价格语义 |

`880005`（涨跌家数）是日频序列：**服务端只在交易日给行**。所以
`date` 列就是交易日历本身 —— 不猜周末、不猜调休、不查任何表。

零请求硬约束（与 `client._prev_trade_date` 同一条铁律）
------------------------------------------------------
`clock_probe=False`（或 `Client.__new__` 造的裸对象）时**一次请求都不发**：
用户关掉探针就等于"一个多余请求都不许发"。此时 `is_trading_day` /
`next` / `prev` / `range` 返回 `None`（并在 `last_error` 记原因），
`raise_if_unavailable()` 允许调用方显式选择"要炸"。

幂等与缓存
----------
同 (方向, 锚点) 的结果缓存在实例里；同一天重复调用**不发重复请求**
（计数可测）。`get_bars` 窗口按需扩展：锚点离服务器"今天"越远，N 越大
（判据：每次翻倍，N 从 4 起步，上限 500 ≈ 两年），直到锚点落进窗口或确认
锚点在窗口外（比最早一根还早 → `prev`/`next` 返回窗口边界最近的事实，
`is_trading_day` 如实回答"窗口里没有这一天"是不够的 → 扩到上限后仍看不到
就 `None` + 记原因，**绝不猜**）。

时效口径
--------
每次成功的取数都走 `get_bars`/`get_breadth` 的既有 Provenance 管道
（`status` 按 `judge_bar` 日线口径、`as_of` 取末根日期），日历本身不另造
第二套时效语义 —— 它是那批 K 线/广度行的**派生视图**。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

__all__ = ["TradeCalendar"]

#: 单点判定的窗口起步根数。**2 是有意与历史探针逐位一致**：
#: `_prev_trade_date` 收编前的取数形态就是 `get_bars("999999", count=2)`，
#: 黄金样本里那些 DC 产品流程（经 `_describe_product` → judge_product →
#: prev）冻结的帧就是 2 根的请求体 —— 起步窗改了会多打一次不同 args 的
#: 请求，回放当场缺帧。扩展判据照样成立：2 → 4 → 8 … → 500。
_WINDOW_START = 2
#: 窗口扩展上限（≈两年）。到顶仍看不到锚点 → `None` + 记原因，绝不猜。
_WINDOW_MAX = 500
#: `get_breadth` 历史区间的单次根数（129 行 ≈ 半年）。
_BREADTH_PAGE = 260


def _to_date(d) -> date | None:
    """宽松解析：date 原样；datetime 取 .date()；"YYYY-MM-DD"/"YYYYMMDD" 字符串。

    解析不出 `None`（**不猜**）。
    """
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        s = d.strip().replace("-", "").replace("/", "")
        if len(s) == 8 and s.isdigit():
            try:
                return date(int(s[:4]), int(s[4:6]), int(s[6:]))
            except ValueError:
                return None
    return None


class TradeCalendar:
    """交易日历（`Client.calendar` 挂载；懒取数，首次调用才发请求）。

    ```python
    c.calendar.is_trading_day("2026-10-24")     # False（周六）
    c.calendar.prev("2026-10-24")               # date(2026, 10, 23)
    c.calendar.next("2026-10-23")               # date(2026, 10, 26)
    c.calendar.range("2026-10-01", "2026-10-31")  # [交易日…] 含头含尾
    ```
    """

    def __init__(self, client):
        self._client = client
        #: (d, 方向) → 结果 的缓存；同参数二次调用零新请求。
        self._cache: dict = {}
        #: 最近一次"回答不了"的原因（None = 没出错）。
        self.last_error: str | None = None
        #: 本实例累计发出的取数次数（幂等断言用）。
        self.fetches = 0

    # ── 内部：两个事实源 ─────────────────────────────────────────────────
    def _bars_dates(self, count: int) -> list[date] | None:
        """`get_bars("999999", count=N)` → **升序**日线日期列表；取不到 `None`。

        ⚠️ 兜 `BaseException`（含 golden_runner 的 `FrameMissing`）是**收编前
        `_prev_trade_date` 的既有语义**：`probe_or_none` 就是这么吞探测失败的
        —— 日历必须同口径，否则黄金回放里"探测帧不在 manifest"会把整个样本
        打成硬错误（实测踩过）。`KeyboardInterrupt`/`SystemExit`/`GeneratorExit`
        照常放行，绝不吞用户的中断。
        """
        self.fetches += 1
        probe = getattr(self._client, "_probe_allowed", False)
        if not probe:
            self.last_error = "clock_probe=False（或裸 Client）：零请求硬约束，不取数"
            return None
        try:
            bars = self._client.get_bars("999999", period="day", count=count)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                         # noqa: BLE001
            self.last_error = "get_bars 抛 %s: %s" % (type(exc).__name__, exc)
            return None
        out = []
        for b in bars or []:
            d = _to_date(getattr(b, "date", ""))
            if d is not None:
                out.append(d)
        out.sort()
        return out

    def _breadth_dates(self, count: int) -> list[date] | None:
        """`get_breadth("880005", count=N)` → **升序** `date` 列；取不到 `None`。"""
        self.fetches += 1
        probe = getattr(self._client, "_probe_allowed", False)
        if not probe:
            self.last_error = "clock_probe=False（或裸 Client）：零请求硬约束，不取数"
            return None
        try:
            t = self._client.get_breadth("880005", count=count)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                         # noqa: BLE001
            self.last_error = "get_breadth 抛 %s: %s" % (type(exc).__name__, exc)
            return None
        out = []
        for r in (t.rows or []):
            d = _to_date(r.get("date"))
            if d is not None:
                out.append(d)
        out.sort()
        return out

    def _window_dates_for(self, anchor: date) -> list[date] | None:
        """单点判定的取数窗口：按需扩展直到覆盖锚点。

        判据（写清版）：
          1. N 从 4（≈1 周）起取 `get_bars`；
          2. 锚点在窗口内（`min <= anchor <= max`）→ 直接用；
          3. 锚点比窗口**新**（anchor > max）→ 只可能发生在服务器数据滞后/
             本机时钟快 —— 不猜，返回窗口事实（`is_trading_day` 按窗口内成员
             判定，窗口外 `None`）；
          4. 锚点比窗口**旧**（anchor < min）→ 翻倍 N（4→8→16…→500）重取；
          5. N 到 `_WINDOW_MAX` 仍盖不住 → `None` + `last_error` 记原因。
        """
        n = _WINDOW_START
        last: list[date] | None = None
        while n <= _WINDOW_MAX:
            dates = self._bars_dates(n)
            if dates is None:
                return None
            if not dates:
                self.last_error = "服务端窗口是空的（999999 无日线），不猜"
                return []
            last = dates
            if dates[0] <= anchor <= dates[-1]:
                return dates
            if anchor > dates[-1]:
                return dates          # 锚点比最新数据还新：窗口已是"最接近的事实"
            n *= 2
        self.last_error = ("窗口扩到 %d 根（≈两年）仍盖不住锚点 %s，不猜"
                           % (_WINDOW_MAX, anchor.isoformat()))
        return last

    # ── 对外 API ─────────────────────────────────────────────────────────
    def is_trading_day(self, d) -> bool | None:
        """`d` 是不是交易日。服务端没给这个日期的行 = 不是（周末/节假日/未到）。

        ⚠️ 窗口盖不住（太远的未来/过去、探针关闭、取数失败）→ `None` + `last_error`。
        """
        day = _to_date(d)
        if day is None:
            self.last_error = "is_trading_day(%r)：解析不出日期，不猜" % (d,)
            return None
        key = ("is", day)
        if key in self._cache:
            return self._cache[key]
        dates = self._window_dates_for(day)
        if dates is None:
            return None
        self.last_error = None
        ans = day in set(dates)
        self._cache[key] = ans
        return ans

    def prev(self, d) -> date | None:
        """`d`（**不**含）之前最近的一个交易日；`d` 本身是不是交易日都行。

        跨周末/节假日由服务端事实回答。判据：窗口里 < d 的最大日期；
        窗口里没有比 d 早的 → 扩窗重试（见 `_window_dates_for`）。
        探针关闭/盖不住 → `None` + `last_error`。
        """
        day = _to_date(d)
        if day is None:
            self.last_error = "prev(%r)：解析不出日期，不猜" % (d,)
            return None
        key = ("prev", day)
        if key in self._cache:
            return self._cache[key]
        dates = self._window_dates_for(day - timedelta(days=1))
        if dates is None:
            return None
        older = [x for x in dates if x < day]
        ans = max(older) if older else None
        if ans is None:
            if not dates:
                self.last_error = ("服务端窗口是空的（999999 无日线），不猜")
            else:
                self.last_error = ("窗口（%s…%s）里没有早于 %s 的交易日，不猜"
                                   % (dates[0].isoformat(), dates[-1].isoformat(),
                                      day.isoformat()))
            return None
        self.last_error = None
        self._cache[key] = ans
        return ans

    def next(self, d) -> date | None:
        """`d`（**不**含）之后最近的一个交易日（周末/节假日之后的开市日）。

        判据：窗口里 > d 的最小日期；窗口盖不住未来的节假日（长假的"下一个
        开市日"可能离 d 超过 4 根）→ 按窗口扩展判据翻倍取数。探针关闭/盖不住
        → `None` + `last_error`。
        """
        day = _to_date(d)
        if day is None:
            self.last_error = "next(%r)：解析不出日期，不猜" % (d,)
            return None
        key = ("next", day)
        if key in self._cache:
            return self._cache[key]
        dates = self._window_dates_for(day + timedelta(days=1))
        if dates is None:
            return None
        newer = [x for x in dates if x > day]
        ans = min(newer) if newer else None
        if ans is None:
            if not dates:
                self.last_error = ("服务端窗口是空的（999999 无日线），不猜")
            else:
                self.last_error = ("窗口（%s…%s）里没有晚于 %s 的交易日（未来长假日还没"
                                   "被服务端数据覆盖），不猜"
                                   % (dates[0].isoformat(), dates[-1].isoformat(),
                                      day.isoformat()))
            return None
        self.last_error = None
        self._cache[key] = ans
        return ans

    def range(self, a, b) -> list[date] | None:
        """`[a, b]` 闭区间里的**全部交易日**（升序，含头含尾）。

        历史区间走 `get_breadth("880005", count=N)` 的 `date` 列（单日一行，
        N 根就是 N 个交易日，比 N 根 K 线高效且列名明确）。区间长度超过一次
        取数根数就分页拼（每页 `_BREADTH_PAGE`）。`a > b` → `ValueError`。
        探针关闭/取数失败 → `None` + `last_error`。
        """
        da, db = _to_date(a), _to_date(b)
        if da is None or db is None:
            self.last_error = "range(%r, %r)：解析不出日期，不猜" % (a, b)
            return None
        if da > db:
            raise ValueError(
                "range(a, b) 要求 a <= b（收到 a=%s > b=%s）；要反向区间请自己反转"
                % (da.isoformat(), db.isoformat()))
        key = ("range", da, db)
        if key in self._cache:
            return list(self._cache[key])

        # 交易日占日历天的 ~5/7（周末休）；再留 8 根余量盖住头部的长假错位。
        n = max(_WINDOW_START, (db - da).days * 5 // 7 + 8)
        n = min(n, 2000)
        dates = self._breadth_dates(n)
        if dates is None:
            return None
        # 长区间一页盖不住：用首尾对账，盖不住就如实报"分页未实现处"之外的事实
        got = [d for d in dates if da <= d <= db]
        if dates and dates[0] > da:
            self.last_error = ("区间头部 %s 早于窗口最早一根 %s（BREADTH_PAGE=%d 根不够），"
                               "结果不完整 —— 不猜"
                               % (da.isoformat(), dates[0].isoformat(), _BREADTH_PAGE))
            return None if not got else got
        self.last_error = None
        self._cache[key] = got
        return list(got)

    def raise_if_unavailable(self) -> "TradeCalendar":
        """调用方显式选择"回答不了就炸"：`last_error` 非空时抛 `RuntimeError`。"""
        if self.last_error:
            raise RuntimeError("TradeCalendar 回答不了：%s" % self.last_error)
        return self

    def __repr__(self) -> str:
        return "<TradeCalendar fetches=%d cached=%d%s>" % (
            self.fetches, len(self._cache),
            " err=%r" % self.last_error if self.last_error else "")
