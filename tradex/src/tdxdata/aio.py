# -*- coding: utf-8 -*-
"""`tdxdata.aio.AsyncClient` —— 同步 `Client` 的 asyncio 薄封装。

只做一件事
----------
每个方法都是 `await self._run(self._c.方法, ...)`：参数原样转、返回值原样透传。
**不重写协议层、不加自己的重试/缓存** —— 限流、重连、结果判定全在同步层，
两套实现只会让行为出现分叉（本仓 7615 是"握手 + 串行慢速"协议，异步收益很小）。

用法
----
    import asyncio
    from tdxdata.aio import AsyncClient

    async def main():
        async with AsyncClient() as c:                 # 退出时关连接
            bars = await c.get_bars("600519", count=5)
            t = await c.get("lhb", date="2026-10-23")
            print(bars[-1].close, t.shape)

    asyncio.run(main())

注意
----
* 并发度受同步层约束：7615 的匀速闸门是**进程级**的，开 8 个任务并不会快 8 倍，
  只是把等待挪到线程池里。**多开连接没用**（2026-10-26 实测：建连过频直接吃 `10053`，
  已把 `ConnectionPool` 删掉）—— 要更快只能减少请求数，或跨进程/跨机分摊。
* 这层**不导入** `indicator` / `limits` / `sections` 等重模块 ——
  它们是 `Client` 方法体里懒 import 的，这里提前导入会连带整包启动变慢。

`max_concurrency`（本层唯一旋钮）
--------------------------------
⚠️ 同步层的 `concurrency`（多 lane / 多主机）**已于 2026-10 删除** ——
实测配额按来源 IP 算，4 台服务器换不来 4 份配额（并发 4 条 lane 头 5 秒就被重置、
之后 175 秒 0 次成功），而且它带一个永久死锁。所以 `AsyncClient` **不接受**
`concurrency=`（传了会由同步层抛 `TypeError`，明确而不是静默忽略）。

本层只剩 `max_concurrency`，它管的是**完全不同的一件事**：

| 旋钮 | 在哪一层 | 管什么 |
|---|---|---|
| `max_concurrency` | 本层 `AsyncClient` | 本事件循环里"同时有多少个 `await` 能进线程池"的上限；**不**多建连接、**不**绕过匀速闸门 |

⚠️ **本层不另起线程池**：它本身就是 async，所有阻塞都在 `asyncio.to_thread` 里；
再加一层 executor 只会让"谁在排队"变成两处，收益为 0。
⚠️ `asyncio.gather` 一批取数**不会变快**：7615 的匀速闸门是**进程级**的
（约 2.2 s 一次），任务再多也只是把等待挪到线程池里排队。
要更快只有两条路：**减少请求数**，或**跨进程/跨机分摊**。
"""
from __future__ import annotations

import asyncio

__all__ = ["AsyncClient"]

#: `iter_universe` 的"同步生成器被取完了"哨兵。
#:
#: 为什么不能直接 `await self._run(next, gen)`：`next()` 用光时抛 `StopIteration`，
#: 而在 `run_in_executor` 里 `StopIteration` **无法**穿过 future —— Python 会把它
#: 变成 `RuntimeError: StopIteration interacts badly with generators and cannot be
#: raised into a Future`（实测踩到）。所以在线程里把"到底了"翻成一个**普通值**，
#: 回到事件循环再据此收尾。
_DONE = object()


def _next_or_done(gen):
    """线程里取一条；到底了返回 `_DONE`（而不是抛 `StopIteration`）。"""
    try:
        return next(gen)
    except StopIteration:
        return _DONE


class AsyncClient:
    """把 `Client` 的常用方法包成协程（`await`）。

    参数
    ----
    client : Client | None
        注入一个已建好的同步客户端（**测试用假对象即可，不必连网**）。
        给了它就不能再给其它参数。
    **kw :
        原样转给 `Client(**kw)`（`host` / `timeout` / `gap` / `clock_probe` /
        `strict_symbols` / `cache` …）。`client` 为 `None` 时才生效。

        ⚠️ 同步层的 `concurrency=` **已删除**（按来源 IP 配额，多开连接换不来
        多份配额，且带永久死锁）。传 `concurrency=` 会由 `Client.__init__`
        抛 `TypeError` —— 本层**不做**兼容垫片，也不静默忽略。
        ⚠️ 本层**不翻译**任何同步层参数的语义：硬上限/`ValueError` 全部由
        `Client` 决定，本层不重复实现一遍（重复实现必然分叉）。

    位置参数也会原样转给 `Client`（兼容 `API设计.md` §2.7 的 `__init__(*a, **kw)` 写法）。

    并发与限流（2026-10-26 加护栏）
    ------------------------------
    `asyncio.gather(*[ac.get_bars(x) for x in 20只])` 在**没有闸门**时会把 20 个任务一起
    丢进线程池，等于"20 路并发取数" —— 而 7615 是按来源限速的，实测 `gap` 压到 0.2 秒时
    第 8 次就被踢（`10053/10054`）。

    所以默认 `max_concurrency=1`：`gather` 只是把等待挪进线程池，**不会**并发出多条网络流。
    想更快只有两个办法：**减少请求数**，或**跨进程/跨机分摊**。

    `max_concurrency>1` 只在两种情况下有意义：
      * **数据中心类**命令（`get`/`call`）：内部走 `Session` + `RLock` + pacer，排队是安全的；
      * **纯本地计算**（`get_indicators`/`get_price_limits`）：根本不碰网络。

    PB 路径（K 线 / 分时 / 市场分页）即使开大，也只是排在 `datacenter` 的连接锁和 pacer 后面。
    """

    def __init__(self, *a, client=None, max_concurrency: int = 1, **kw):
        if client is None:
            from .client import Client          # 懒 import：避免包初始化期绕圈
            client = Client(*a, **kw)
        elif a or kw:
            raise TypeError(
                "给了 client 就不要再传 Client 的参数（多余的：%r / %r）" % (a, kw))
        self._c = client
        n = int(max_concurrency)
        if n < 1:
            raise ValueError("max_concurrency 必须 ≥ 1（收到 %r）" % (max_concurrency,))
        self._max_concurrency = n
        self._sem = asyncio.Semaphore(n)        # 3.10+ 无需绑定 loop

    # ── 只读入口 ──────────────────────────────────────────────────────────
    @property
    def client(self):
        """底层同步 `Client`（想要同步语义时直接用）。"""
        return self._c

    @property
    def max_concurrency(self) -> int:
        """当前并发上限（默认 1）。"""
        return self._max_concurrency

    @property
    def last_call(self) -> dict:
        """`await` 版没有意义 —— 这是**已在内存里的**快照，直接读，不开线程。

        语义与同步层完全一致（`Client.last_call` 的返回对象就是同一个 dict）。
        阶段 3 补齐：它是 Client 的公开属性，异步壳必须也能拿到，
        否则"两套 API 能力一致"这句话在 `last_call` 上就是假的。
        """
        return self._c.last_call

    # ── 统一入口：**所有方法都必须走这里**，别再直接 asyncio.to_thread ──────
    async def _run(self, fn, *a, **kw):
        async with self._sem:                   # ★ 并发护栏
            return await asyncio.to_thread(fn, *a, **kw)

    # ── 底层 ──────────────────────────────────────────────────────────────
    async def call(self, cmd, ctl, params=None, **kw):
        """`await` 版 `Client.call`，参数/返回值完全不改。"""
        return await self._run(self._c.call, cmd, ctl, params, **kw)

    async def get(self, name, **kw):
        """`await` 版 `Client.get`（按产品名取数）。"""
        return await self._run(self._c.get, name, **kw)

    # ── 行情 ──────────────────────────────────────────────────────────────
    async def get_bars(self, symbol, *a, **kw):
        """`await` 版 `Client.get_bars`（K 线，返回 `list[Bar]`）。"""
        return await self._run(self._c.get_bars, symbol, *a, **kw)

    async def get_minutes(self, symbol, *a, **kw):
        """`await` 版 `Client.get_minutes`（当日分时）。"""
        return await self._run(self._c.get_minutes, symbol, *a, **kw)

    async def get_quotes(self, symbols_, *a, **kw):
        """`await` 版 `Client.get_quotes`（批量报价；支持 `on_missing=`）。"""
        return await self._run(self._c.get_quotes, symbols_, *a, **kw)

    async def get_ticks(self, symbol, *a, **kw):
        """`await` 版 `Client.get_ticks`（逐笔成交）。"""
        return await self._run(self._c.get_ticks, symbol, *a, **kw)

    async def get_name(self, symbol, *a, **kw):
        """`await` 版 `Client.get_name`（代码 → 名称）。"""
        return await self._run(self._c.get_name, symbol, *a, **kw)

    async def get_samples(self, symbols_, *a, **kw):
        """`await` 版 `Client.get_samples`（批量抽样快照）。"""
        return await self._run(self._c.get_samples, symbols_, *a, **kw)

    async def get_intraday_samples(self, symbols_, *a, **kw):
        """`await` 版 `Client.get_intraday_samples`（分时点位抽样，2026-10-28 新增）。"""
        return await self._run(self._c.get_intraday_samples, symbols_, *a, **kw)

    async def get_market_breadth(self, view="summary", *a, **kw):
        """`await` 版 `Client.get_market_breadth`（实时涨跌家数，2026-10-28 新增）。"""
        return await self._run(self._c.get_market_breadth, view, *a, **kw)

    async def get_breadth(self, code, *a, **kw):
        """`await` 版 `Client.get_breadth`（广度指标：880005 涨跌家数等）。"""
        return await self._run(self._c.get_breadth, code, *a, **kw)

    # ── 本地计算（派生指标 / 涨跌停）──────────────────────────────────────
    async def get_indicators(self, symbol, *a, **kw):
        """`await` 版 `Client.get_indicators`（MACD/MA/KDJ/RSI/BOLL）。"""
        return await self._run(self._c.get_indicators, symbol, *a, **kw)

    async def get_price_limits(self, symbol, *a, **kw):
        """`await` 版 `Client.get_price_limits`（涨跌停价）。"""
        return await self._run(self._c.get_price_limits, symbol, *a, **kw)

    # ── 财务 / F10 / 分红 ─────────────────────────────────────────────────
    async def get_finance(self, symbol, *a, **kw):
        """`await` 版 `Client.get_finance`（财务报表）。"""
        return await self._run(self._c.get_finance, symbol, *a, **kw)

    async def get_f10(self, symbol, *a, **kw):
        """`await` 版 `Client.get_f10`（F10 个股资料）。"""
        return await self._run(self._c.get_f10, symbol, *a, **kw)

    async def get_xdxr(self, symbol, *a, **kw):
        """`await` 版 `Client.get_xdxr`（分红融资）。"""
        return await self._run(self._c.get_xdxr, symbol, *a, **kw)

    # ── 板块 / 资金 / 市场 ───────────────────────────────────────────────
    async def get_boards(self, *a, **kw):
        """`await` 版 `Client.get_boards`（板块列表）。"""
        return await self._run(self._c.get_boards, *a, **kw)

    async def get_board_members(self, board, *a, **kw):
        """`await` 版 `Client.get_board_members`（板块成分股）。"""
        return await self._run(self._c.get_board_members, board, *a, **kw)

    async def get_capital_flow(self, symbols=None, *a, **kw):
        """`await` 版 `Client.get_capital_flow`（资金流向）。"""
        return await self._run(self._c.get_capital_flow, symbols, *a, **kw)

    async def get_market_stat(self, *a, **kw):
        """`await` 版 `Client.get_market_stat`（市场概览）。"""
        return await self._run(self._c.get_market_stat, *a, **kw)

    async def get_market_page(self, *a, **kw):
        """`await` 版 `Client.get_market_page`（市场行情一页，2026-10-25 新增）。"""
        return await self._run(self._c.get_market_page, *a, **kw)

    async def board_page(self, *a, **kw):
        """`await` 版 `Client.board_page`（板块清单分页行情）。"""
        return await self._run(self._c.board_page, *a, **kw)

    async def get_block_data(self, block, *a, **kw):
        """`await` 版 `Client.get_block_data`（块数据，单页 ≤100 行）。"""
        return await self._run(self._c.get_block_data, block, *a, **kw)

    async def get_block_rows(self, block, *a, **kw):
        """`await` 版 `Client.get_block_rows`（块数据按页取全）。"""
        return await self._run(self._c.get_block_rows, block, *a, **kw)

    async def get_board_stat(self, *a, **kw):
        """`await` 版 `Client.get_board_stat`（板块成分统计 PBHYStat）。"""
        return await self._run(self._c.get_board_stat, *a, **kw)

    async def get_universe(self, *a, **kw):
        """`await` 版 `Client.get_universe`（按市场翻页枚举，有 max_pages 上限）。

        ⚠️ 每页各过一次闸门（`gap=2.2`）；**实测 3 页 6.60 秒**（2026-10-26），
        即约 2.2 秒/页 → `max_pages=20` 约 44 秒。在事件循环里**仍然阻塞在 to_thread 的线程上**，
        不会自动并行加速。
        """
        return await self._run(self._c.get_universe, *a, **kw)

    async def iter_universe(self, *a, **kw):
        """`await` 版的 `Client.iter_universe` —— **异步生成器**，逐个 `Quote` 交出来。

        ```python
        async with AsyncClient() as c:
            async for q in c.iter_universe(market=6):
                ...
        ```
        ⚠️ 每次 `next()` 都在线程池里跑（翻一页 = 一次网络往返 ≈2.2 秒），
        所以"边取边停"在异步里同样有意义：`break` 之后就不再翻下一页。
        ⚠️ 同步层是**真阻塞**的生成器，所以**不要**在同一个事件循环里并发
        `async for` 两个 `iter_universe()` 指望它们并行 —— 它们会串行排在
        `pacer` 的进程级闸门和 `datacenter` 的连接锁后面（见类文档"并发与限流"）。
        ⚠️ 同步生成器读的是**同步客户端**（`self._c`），与 `max_concurrency` 无关：
        每页本来就被 `pacer` 的进程级闸门串起来了，这里再并发只会互相等。
        """
        gen = self._c.iter_universe(*a, **kw)
        try:
            while True:
                item = await self._run(_next_or_done, gen)
                if item is _DONE:
                    return
                yield item
        finally:
            close = getattr(gen, "close", None)
            if close is not None:
                close()          # 提前 break 时把同步生成器关掉，别把状态挂着

    # ── 语义层（阶段 1/3）─────────────────────────────────────────────────
    async def timeliness(self, *a, **kw):
        """`await` 版 `Client.timeliness`（"先问再取"：这条通道现在什么时效状态）。"""
        return await self._run(self._c.timeliness, *a, **kw)

    # ── 元信息（本地，不发网络请求）──────────────────────────────────────
    async def products(self, *a, **kw):
        """`await` 版 `Client.products`（421 个数据产品清单）。"""
        return await self._run(self._c.products, *a, **kw)

    async def domains(self, *a, **kw):
        """`await` 版 `Client.domains`（有产品的域）。"""
        return await self._run(self._c.domains, *a, **kw)

    async def capabilities(self, *a, **kw):
        """`await` 版 `Client.capabilities`（能力边界表：品种 × 有无 × 依据 × 绕法）。

        ⚠️ 这是**本地静态注册表，一个请求都不发** —— 它之所以也过 `to_thread`，
        纯粹是为了让同步/异步两套接口长得一样（`test_aio_parity` 要求方法集完全一致），
        **不是**为了并行。
        """
        return await self._run(self._c.capabilities, *a, **kw)

    async def rate_stats(self, *a, **kw):
        """`await` 版 `Client.rate_stats`（限流状态快照）。

        返回值里除 `base` / `current` / `max` / `limited_events` / `cooling_hosts`
        外，还有三个**兼容键**：`concurrency` / `concurrency_effective` / `lanes`
        —— 并发特性已删除，它们恒为 `1` / `1` / `[]`，留着只为不破坏已有解析。
        异步壳**不另加口径**，照抄同步层。
        """
        return await self._run(self._c.rate_stats, *a, **kw)

    # ── 生命周期 ──────────────────────────────────────────────────────────
    async def close(self):
        """关连接（同步层的 `close()` 放进线程池执行）。"""
        return await self._run(self._c.close)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        await self.close()
        return False

    def __repr__(self) -> str:
        return "<aio.AsyncClient of %r>" % (self._c,)
