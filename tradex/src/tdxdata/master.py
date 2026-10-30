# -*- coding: utf-8 -*-
r"""PROD-001：`c.master` —— **证券主数据**（快照聚合，不冒充完整主表）。

为什么它不是"完整证券主表"
--------------------------
完整证券主表命令**不存在**（报告 §3.2：7615 手机版协议没有"给我全部证券"的
接口）。`PBMultiHQ` 是**按涨幅排序**的行情列表，盘中排序会漂移，翻页接缝
可能漏票 —— `get_universe()` 的边界警告原样适用于本模块：

    "只能保证**不重复**，不能保证**不遗漏**：`PBMultiHQ` 是按涨幅排序的列表，
    盘中排序会漂移，翻页接缝不可能严格对齐 —— 某只票在一页之间从第 50 名跳到
    第 40 名，就会被跳过。这也意味着**不是完整证券代码表**，只是
    "当前可枚举到的行情列表"。"（client.get_universe docstring）

所以本模块的 `snapshot()` 产出的是「**当前可枚举的行情快照**」，docstring
与 Provenance notes 里**都如实声明**这个边界 —— 把它当完整主表用的人会在
notes 里读到警告（读不读是他的事，库里不许装作不知道）。

resolve 的红线：**绝不猜市场**
----------------------------
输入 6 位码在快照里查唯一市场号。快照是"服务端此刻给的事实"，查不到就
`None`（默认）或 raise（`missing="raise"`）—— **绝不退回 `symbols.market_by_code`
的前缀推断**：前缀推断是"猜"，`000700` 与 `00700` 这类前缀碰撞正是它
静默命中错市场的方式（00700 会按前缀命中 0=深，而它是港股；本库主战场
A 股里同样存在 000680 这类两义码，靠猜迟早出错数据）。快照里命中几条
（同名码跨市场）→ 同样 `None`/raise，**唯一**才回。

测试锚点
--------
`resolve` 的市场号正确性由 `tests/fixtures/市场码真值样本.json` 的 309 条
服务端真值全量断言（离线桩路径：假 universe 喂样本周表 → resolve 逐条查
→ 100% 命中）。这套真值同时校验了 snapshot 聚合链（`Quote.market` ←
PBMultiHQ `Setcode`）没在途中被改写。
"""
from __future__ import annotations

from typing import Any

from . import symbols
from .models import Table

__all__ = ["SecMaster"]

#: 快照覆盖的市场码（get_universe 的口径：6=全部A股；0/1/2/7 是分市场子集）。
#: 全部A股(6) 已含深(0)/沪(1)两市的可枚举行；北交所(2) 不在 PBMultiHQ market=6
#: 里（实测）—— 它不是本快照的覆盖对象，resolve 对北交所码因此如实 None。
_SNAPSHOT_MARKETS = (6,)


class SecMaster:
    """证券主数据（`Client.master` 挂载；懒取数，首次调用才发请求）。

    ```python
    t = c.master.snapshot()                     # 全市场快照 Table
    c.master.resolve("000700")                  # (0, "000700") 或 None
    c.master.search("茅台")                     # 名称子串匹配 → Table
    ```
    """

    def __init__(self, client, *, max_pages: int = 20, page_size: int = 50):
        self._client = client
        self._max_pages = int(max_pages)
        self._page_size = int(page_size)
        self._snap: Table | None = None
        self._by_code: dict[str, list[tuple[int, str]]] | None = None
        #: 本实例累计发起的快照取数次数（幂等断言用）。
        self.fetches = 0
        #: 最近一次"回答不了"的原因（None = 没出错）。
        self.last_error: str | None = None

    # ── 快照聚合 ─────────────────────────────────────────────────────────
    def snapshot(self, *, refresh: bool = False) -> Table:
        """**当前可枚举的行情快照**（全市场聚合）。

        列：`market`（服务端 Setcode）/ `code`（6 位）/ `name` /
        `symbol`（`symbols.symbol_of` 复用：1→sh / 0→sz / 2→bj 前缀）。

        ⚠️ **边界声明（原样来自 get_universe）**：这是「当前可枚举的行情列表」
        **不是完整证券主表** —— PBMultiHQ 按涨幅排序会漂移，页间接缝可能漏票；
        北交所(market=2)不在 PBMultiHQ market=6 的枚举范围里，本快照不含它。
        Provenance notes 里有同一句警告（走 get_universe 既有管道，不造第二套
        时效语义）。

        进程级缓存：同一实例重复调用**零新请求**（`fetches` 计数可断言）；
        `refresh=True` 强制重取（仍然不破幂等语义 —— 是你显式要求的）。
        """
        if self._snap is not None and not refresh:
            return self._snap
        self.fetches += 1
        rows: list[dict] = []
        prov = None
        try:
            for mkt in _SNAPSHOT_MARKETS:
                uni = self._client.get_universe(market=mkt,
                                                max_pages=self._max_pages,
                                                page_size=self._page_size)
                for q in (uni or []):
                    rows.append({
                        "market": int(q.market),
                        "code": str(q.code),
                        "name": str(q.name or ""),
                        "symbol": symbols.symbol_of(int(q.market), str(q.code)),
                    })
                p = getattr(uni, "provenance", None)
                if p is not None:
                    prov = p
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                         # noqa: BLE001
            # ⚠️ 兜 BaseException 与 calendar 同口径：golden_runner 的
            # `FrameMissing` 继承 BaseException，回放路径里快照不被触发，
            # 但真机取数失败也该记原因而不是炸调用链。
            self.last_error = "get_universe 抛 %s: %s" % (type(exc).__name__, exc)
            raise
        table = Table(cols=["market", "code", "name", "symbol"], rows=rows,
                      cmd="tdxdata.master.snapshot", domain="证券主数据")
        if prov is not None:
            # 出处整份继承自那批翻页枚举（它就是由它们算出来的，独立性零）
            table.inherit_provenance(uni)
            table.provenance = table.provenance.with_notes(
                "证券主数据快照：聚合自 get_universe（PBMultiHQ 翻页枚举）。"
                "**不是完整证券主表** —— 按涨幅排序盘中漂移、页间接缝可能漏票、"
                "北交所(market=2)不在枚举范围；resolve 查不到就如实 None，绝不按前缀猜市场")
        self._snap = table
        self._by_code = None          # 索引随快照失效，下次 resolve 重建
        return table

    # ── 索引 ─────────────────────────────────────────────────────────────
    def _index(self) -> dict[str, list[tuple[int, str]]]:
        """code → [(market, code)] 的倒排（同码跨市场会有多条）。"""
        if self._by_code is None:
            idx: dict[str, list[tuple[int, str]]] = {}
            for r in (self.snapshot().rows or []):
                idx.setdefault(str(r["code"]), []).append((int(r["market"]),
                                                           str(r["code"])))
            self._by_code = idx
        return self._by_code

    # ── 对外 API ─────────────────────────────────────────────────────────
    def resolve(self, code, *, missing: str = "none"):
        """6 位码 → `(market, code)` 唯一市场号；**查不到绝不猜**。

        判据（写清版）：
          · 快照里该码**恰好一条** → `(market, code)`；
          · 快照里**零条**（未枚举到/北交所/港股/不存在）→ 按 `missing`：
            `"none"`（默认）回 `None`；`"raise"` 抛 `KeyError`（点名快照边界）；
          · 快照里**多条**（同码跨市场，理论上 PBMultiHQ 不会给，但真给就是不
            唯一事实）→ 按 `missing` 同上处理，**绝不取第一条**。

        ⚠️ **绝不退回 `symbols.market_by_code` 前缀推断** —— 那是"猜"：
        `000700`/`00700` 前缀碰撞静默命中错市场是实测踩过的坑
        （000680 两义码：市场 1 = 科创综指、市场 0 = 山推股份）。
        快照没有 = 回答不了，不是"猜一个"。

        `missing="raise"` 时抛的 `KeyError` 消息里带快照边界声明与
        `last_error`（若快照取数本身失败）。
        """
        from . import errors as _errors

        raw = str(code or "").strip().lower()
        for suf in (".sh", ".sz", ".bj", ".ss"):
            if raw.endswith(suf):
                raw = raw[: -len(suf)]
        if raw[:2] in symbols._PREFIX:
            raw = raw[2:]
        # ⚠️ 位数先于 clean 截断校验：clean 是内部宽容版（s[:6] 会截断），
        # resolve 的输入校验必须拒绝 5 位（00700 港股形态）与 >6 位 ——
        # 静默截断正是"00700 → 000700 前缀碰撞"的入口。
        if not raw.isdigit():
            raise ValueError("resolve(%r)：需要 6 位数字代码（可带 sh/sz/bj 前缀）"
                             % (code,))
        if len(raw) != 6:
            raise ValueError(
                "resolve(%r)：需要 6 位数字代码（收到 %d 位）。"
                "5 位码可能是港股（本库主战场 A 股，不支持）；"
                "7 位以上是抄写错误。**不猜、不截断、不按前缀推市场**"
                % (code, len(raw)))
        code_s = raw
        entries = self._index().get(code_s) or []
        if len(entries) == 1:
            self.last_error = None
            return entries[0]
        if len(entries) > 1:
            self.last_error = ("resolve(%s)：快照里命中 %d 个市场 %s —— 不唯一，"
                               "绝不取第一条" % (code_s, len(entries), entries))
        else:
            self.last_error = ("resolve(%s)：快照里没有这个代码（未枚举到/北交所/"
                               "港股/不存在）。快照边界：PBMultiHQ 翻页枚举，"
                               "**不是完整证券主表**，绝不按前缀猜市场"
                               % code_s)
        if missing == "raise":
            raise KeyError(self.last_error)
        if missing != "none":
            raise _errors.ParamError(
                "resolve 的 missing 只接受 'none' / 'raise'（收到 %r）" % (missing,))
        return None

    def search(self, name_like: str) -> Table:
        """快照内**名称子串匹配**（大小写不敏感）；空结果回**空 Table 不抛**。

        出入参都是快照的列（market/code/name/symbol）；出处继承快照。
        """
        needle = str(name_like or "").strip().lower()
        snap = self.snapshot()
        if not needle:
            rows = list(snap.rows or [])
        else:
            rows = [r for r in (snap.rows or [])
                    if needle in str(r.get("name", "")).lower()]
        out = Table(cols=list(snap.cols), rows=rows,
                    cmd="tdxdata.master.search", domain="证券主数据")
        out.inherit_provenance(snap)
        out.provenance = out.provenance.with_notes(
            "search(%r)：快照内名称子串匹配（大小写不敏感），%d 命中"
            % (name_like, len(rows)))
        return out

    def __repr__(self) -> str:
        n = len(self._snap.rows) if self._snap is not None else None
        return "<SecMaster fetches=%d snapshot_rows=%s%s>" % (
            self.fetches, n, " err=%r" % self.last_error if self.last_error else "")
