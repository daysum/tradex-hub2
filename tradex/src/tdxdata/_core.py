# -*- coding: utf-8 -*-
"""访问层共用原语（阶段 3 新增）。

这里只放**被多处共用、且有独立语义**的小东西，不放业务逻辑：

| 名字 | 作用 |
|---|---|
| `MARKET_DOMAINS` | `PBMultiHQ` 的 `field4`（市场域）语义表 —— **唯一事实源** |
| `paginate` | 按 offset 翻页取全（`get_universe` / `iter_universe` 共用） |
| `estimate_cost` | 翻页成本估算（写进 `meta`，让人**动手前**知道要等多久） |
| `resolve_missing` | 批量取数"缺了哪几个"的清单 + `on_missing` 三态策略 |

为什么要有 `MARKET_DOMAINS`（而不是继续散在各处）
------------------------------------------------
"`field4=6` 到底是什么"这个问题，历史上答错过：老表把 `0` 标成"科创板"、
`2` 标成"创业板"，于是 `market=0` 看起来"只覆盖科创板"，实际上它覆盖**沪市全部股票**。
一个错标注会让整条调研结论歪掉（"北交所在不在 6 里"就是这么被问出来的）。
所以：**一张表、带证据、带能力断言**，别的地方一律引用它。
"""
from __future__ import annotations

import warnings as _warnings

from . import errors
from .symbols import symbol_of

__all__ = [
    "MARKET_DOMAINS", "COMPOSITE_MARKETS", "DOMAIN_PROOF",
    "paginate", "iter_pages", "estimate_cost", "resolve_missing",
    "ON_MISSING_MODES", "check_on_missing",
]

#: `PBMultiHQ` 的 `field4`（市场域）→ 语义。
#:
#: 证据口径：**2026-10-27 实网**对每个域翻页取全后比对代码段分布
#: （`get_universe(market=d)` / `get_market_page(market=d)`，逐页记录首末代码与行数）。
#: 标「沿用旧标注」的三项（3/4/5/7）**没有逐个复验**，如实标注而不是假装查过。
MARKET_DOMAINS: dict[int, str] = {
    0: "沪A（沪市全部股票，含 60xxxx/68xxxx）",       # 实证：≠「只有科创板」
    1: "沪B（900xxx）",
    2: "深A（深市全部股票，含 00xxxx/30xxxx）",       # 实证：≠「只有创业板」
    3: "深B（200xxx/201xxx）",                        # 沿用旧标注，未逐个复验
    4: "债券/回购",                                    # 沿用旧标注，未逐个复验
    5: "债券（另一类）",                               # 沿用旧标注，未逐个复验
    6: "全部A股（沪A ∪ 深A ∪ 北交所）",                # 见下方 DOMAIN_PROOF
    7: "B股",                                          # 沿用旧标注，未逐个复验
    8: "科创板（68 段；与 0 **不等**）",               # 实证：8 只含 68xxxx，是 0 的真子集
    9: "ETF/基金",                                     # 实证：有数据，旧表未登记
    10: "全品种榜",                                    # 实证：有数据，旧表未登记
    11: "指数",                                        # 实证：有数据，旧表未登记
    12: "北交所（920xxx/430xxx/83xxxx/87xxxx）",       # 实证：354 只全在 920 段
    13: "配债",                                        # 实证：有数据，旧表未登记
    14: "创业板（30xxxx）",                            # 实证：有数据，旧表未登记
}

#: **复合域**：这些 `field4` 的值是别的域拼出来的，本身不是"一段独立的代码"。
COMPOSITE_MARKETS = (6,)

#: `market=6` 到底是什么的能力断言（实测**精确成立**，不是近似、也不是 `0|1|2`）。
#:
#: ```
#: set(6) == set(0) | set(2) | set(12)        # 沪A ∪ 深A ∪ 北交所
#: # 而不是 set(0) | set(1) | set(2)：1 是沪B（900xxx），6 里**没有** B 股
#: ```
#: 推论（很实用）：
#:   * 想要"全部 A 股"就 `market=6`，**不要**自己去拼 0+2（会漏北交所 354 只）；
#:   * 想要"沪深A股、排除北交所"用 `market=0`+`market=2`；
#:   * `market=6` 全量约 **5577 只**；`page_size=80` 时需要 `max_pages>=70`。
DOMAIN_PROOF = "set(6) == set(0) | set(2) | set(12)（2026-10-27 实网逐页取全比对）"


def iter_pages(fetch_page, *, page_size, max_pages=None, key=None, stats=None):
    """逐页取全的**生成器**：每页取到就把那页的新条目逐个 `yield` 出去。

    退出条件与 `paginate` 完全一致（这里就是唯一实现，`paginate` 只是把它吃完）。
    做成生成器的意义是 `Client.iter_universe()`：全量要 70 页 / 约 158 秒，
    调用方常常**取到够用就停**（`for q in iter_universe(): if 够: break`）——
    先把 5577 只收进列表再给，等于把"随时可以不要"的等待强行付满。

    `stats` 传一个 dict 进来，函数会在**结束时**把
    `{"pages", "rows", "truncated"}` 写进去（生成器没法 `return` 值）。
    """
    if max_pages is not None:
        max_pages = int(max_pages)
        if max_pages < 1:
            raise ValueError("max_pages 必须 ≥ 1 或 None（收到 %r）" % (max_pages,))
    size = int(page_size)
    if size < 1:
        raise ValueError("page_size 必须 ≥ 1（收到 %r）" % (page_size,))
    keyf = key or (lambda x: x)
    st = {"pages": 0, "rows": 0, "truncated": False}

    seen: set = set()
    off = 0
    try:
        while True:
            if max_pages is not None and st["pages"] >= max_pages:
                st["truncated"] = True      # 还有预算外的页没取：明确标记，别装成"取全了"
                break
            try:
                page = list(fetch_page(off))
            except errors.EmptyResult:
                if not st["rows"]:
                    raise                   # 第一页就空 = 参数/列配错
                break
            st["pages"] += 1
            fresh = [x for x in page if keyf(x) not in seen]
            if not fresh:                   # 无进展保护（服务端没挪窗口）
                break
            for x in fresh:
                seen.add(keyf(x))
                st["rows"] += 1
                yield x
            off += len(page)                # offset 是**行偏移** → 按返回行数推进
            if len(page) < size:            # 不足一页 → 末页
                break
    finally:
        if stats is not None:
            stats.update(st)


def paginate(fetch_page, *, page_size, max_pages=None, key=None):
    """按 offset 翻页取全 → `(items, stats)`（`iter_pages` 吃完的版本）。

    参数
    ----
    fetch_page : callable(offset) -> list
        取一页。**到底了**请抛 `errors.EmptyResult`（服务端"无数据"与
        "通道不通"在协议层是两件事，这里只把前者当"到底"）。
    page_size : int
        期望每页行数。**本页不足一页 ⇒ 已是末页**（服务端的最后一页就是短的）。
    max_pages : int | None
        `None` = 翻到底；给了就是**硬上限**（防参数配错时无限请求）。
    key : callable(item) -> hashable
        去重键（如 `lambda q: (q.market, q.code)`）。

    返回 `stats` = `{"pages": 实际发出的页数, "rows": 行数, "truncated": 是否因上限而停}`。

    退出条件（任一满足即停，**顺序有意义**）
    ----------------------------------------
    1. **第一页**就 `EmptyResult` → **原样上抛**（参数/列数配错，绝不能静默当"到底了"）；
    2. 后续页 `EmptyResult` → 正常到底；
    3. 本页**没有带来任何新条目**（服务端没按 offset 挪窗口）→ 到底；
    4. 本页**不足 `page_size` 行** → 已是末页；
    5. 达到 `max_pages` → 停，且 `truncated=True`。

    ⚠️ 第 4 条必须排在第 5 条**之前**判：否则"最后一页正好是满页"时，
    下一条空页会被算成"因上限而截断"，把"取完了"误报成"截断了"。
    """
    stats: dict = {}
    items = list(iter_pages(fetch_page, page_size=page_size, max_pages=max_pages,
                            key=key, stats=stats))
    return items, stats


def estimate_cost(pages, gap: float = 2.2) -> float:
    """翻页成本估算（秒）：`pages × gap`。

    为什么把它放进 `meta`：`get_universe` 默认 `max_pages=20` 就是 **44 秒**，
    而"要不要全量"是调用方**动手前**就该知道的决定 —— 等 3 分钟才发现更糟。
    `gap` 是进程级匀速闸门的间隔（`pacer` 默认 2.2 秒/次，实测 3 页 6.60 秒）。
    """
    return round(max(0, int(pages)) * float(gap), 1)


#: `on_missing` 的合法取值（批量取数缺码时怎么办）。
ON_MISSING_MODES = ("raise", "skip", "report")


def check_on_missing(value) -> str:
    """校验 `on_missing`；非法值 → `ParamError`（而不是默默用默认值）。"""
    v = str(value).strip().lower()
    if v not in ON_MISSING_MODES:
        raise errors.ParamError(
            "on_missing 只接受 %s（收到 %r）"
            % (" / ".join(repr(x) for x in ON_MISSING_MODES), value))
    return v


def resolve_missing(requested, got, *, on_missing: str = "raise",
                    reason: str = "服务端未返回",
                    raise_msg=None, warn_msg=None):
    """把"请求了哪些、拿到了哪些"算成**精确的缺失清单**，并按 `on_missing` 处理。

    参数
    ----
    requested : list[tuple[int, str]]
        请求的 `(market, code)`（**保持请求顺序**，缺失清单也按它排）。
    got : 容器
        实际拿到的 `code` 集合 / 映射（`in` 判定即可）。
    on_missing : str
        `"raise"`（默认，保持既有行为）/ `"skip"` / `"report"`。
    raise_msg : str | None
        `raise` 时用的消息（由调用方拼，**既有文案不许改** —— 黄金样本钉着它）。
    warn_msg : str | None
        `report` 时的提醒文案（默认按缺失清单拼一句）。

    返回
    ----
    `list[dict]`：`[{"symbol": "sh600519", "market": 1, "code": "600519",
    "reason": "服务端未返回"}, …]`；无缺失就是 `[]`。

    三态行为
    --------
    * `raise`：有缺失 → `EmptyResult`（点名缺了哪些，绝不返回"少了几个"的表）；
    * `skip`：**不抛**，返回缺失清单交给调用方塞进 `provenance.extra["missing"]`；
    * `report`：同 `skip`，但额外 `warnings.warn` 一次（"我知道你允许缺，但我要让你看见"）。

    ⚠️ 为什么**不加** `PartialResult` 异常：它与 `on_missing="skip"` 语义直接打架 ——
    `skip` 的整个意思就是"缺了也正常返回"，再抛一个"结果不完整"的异常，
    等于逼调用方在 `try` 里再判一次"这到底算不算错"。批量缺失只走
    `Quotes.provenance.extra["missing"]`（结构化、可数、不打断流程）。
    """
    mode = check_on_missing(on_missing)
    missing = [{"symbol": symbol_of(m, c), "market": int(m), "code": str(c),
                "reason": reason}
               for m, c in requested if c not in got]
    if not missing:
        return missing          # 空清单 = "一个都没缺"，是**完备**的正面结论
    if mode == "raise":
        raise errors.EmptyResult(raise_msg or (
            "服务端没有返回这些代码：%s（按 %s 请求）"
            % (", ".join(d["code"] for d in missing), reason)))
    if mode == "report":
        _warnings.warn(warn_msg or (
            "get_quotes 有 %d 个代码服务端未返回：%s（已按 on_missing=%r 丢弃，"
            "缺失清单见 provenance.extra[\"missing\"]）"
            % (len(missing), ", ".join(d["code"] for d in missing), mode)),
            stacklevel=3)
    return missing
