"""
tdxdata 深水区工具（Phase 3，v3.5.0）。

只接 tdxdata **已有标准字段/语义层**的接口，避免对 raw 产品猜列义：
  - `tdx_get_f10`：22 个 F10 section（gsjj/jyfx/gbgd/zbyz/rdtc/fhrz/...）
  - `tdx_get_finance`：9 类财务报表（zcfz/lyb/xjllb/zyzb/cwbg/...）
  - `tdx_get_capital_flow_rank`：全市场主力净流入榜（固定前 20）

⚠️ `dividend_financing` / 龙虎榜 / 涨停池 / 解禁 等 raw 产品仍走
`tdx_get_product` 通用入口 —— 在拿到逐列证据前不硬映射。
"""

from __future__ import annotations

import json
import time

from mcp.server.fastmcp import FastMCP

from ..data_sources import tdxdata_fetchers as tdf
from ..utils.formatter import dict_to_json, error_response
from ..utils.symbol import normalize_symbol
from .tdxdata_data import _err, _max_rows, _require_enabled, _run, _table_payload


def _sections() -> list[str]:
    from tdxdata.sections.f10 import SECTIONS

    return sorted(SECTIONS)


def _finance_kinds() -> list[str]:
    from tdxdata.sections.finance import KINDS

    return sorted(KINDS)


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(value) -> list:
    """上游 JSON 字符串 → list；非法/空值返回 []（不编数据）。"""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def register(mcp: FastMCP):
    """注册 tdxdata 深水区工具。"""

    @mcp.tool()
    async def tdx_get_f10(
        symbol: str, section: str = "gsjj", max_rows: int = 0
    ) -> str:
        """
        获取 tdxdata 结构化 F10 资料（22 个 section）。

        常用 section：
          - gsjj  公司概况/发行上市
          - jyfx  经营分析（主营简述/经营范围）
          - gbgd  股东户数/人均持股
          - zbyz  经营分析·指标/变动说明
          - rdtc  热点题材/公司资料文本块
          - fhrz  IPO/融资累计统计（**不是逐次分红/除权事件表**）
          - zg_jby / zg_scm / zg_zs  基本面/市场面/综合评分卡
          - zp / zp_ggld  个股标签/亮点
          - sl_v2 风险等级

        Args:
            symbol: 6 位股票代码（可带 sh/sz/bj 前缀）。
            section: F10 section 名；完整列表见返回的 available_sections。
            max_rows: 最大返回行数（0=配置默认，最大 1000）。

        Returns:
            JSON：cols/rows/provenance + source/latency_ms/available_sections/note。
        """
        if (msg := _require_enabled("tdx_get_f10")) is not None:
            return msg
        started = time.time()
        try:
            sections = _sections()
            section = (section or "gsjj").strip()
            if section not in sections:
                return error_response(
                    f"section 只接受 {sections}（收到 {section!r}）",
                    "tdx_get_f10",
                )
            sym = normalize_symbol(symbol)
            table = await _run(
                tdf.call, "get_f10", sym, section=section, timeout=30
            )
            return _table_payload(
                table,
                tool="tdx_get_f10",
                started=started,
                max_rows=_max_rows(max_rows),
                extra={
                    "symbol": sym,
                    "section": section,
                    "available_sections": sections,
                    "note": (
                        "标准字段 + 每行 raw；部分 section 只登记了 raw。"
                        "fhrz 是 IPO/融资累计统计（ipo_count/ipo_proceeds_yuan），"
                        "不是分红/除权事件表；逐次分红请用 get_dividend_data / "
                        "eltdx_get_dividend_financing。"
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _err("tdx_get_f10", exc)

    @mcp.tool()
    async def tdx_get_finance(
        symbol: str, kind: str = "zcfz", max_rows: int = 0
    ) -> str:
        """
        获取 tdxdata 结构化财务报表（9 类）。

        kind 取值：zcfz（资产负债表全表）/ lyb（利润表全表）/ xjllb（现金流量表全表）/
        zyzb（主要财务指标）/ cwbg（财报公告列表）/ cwfxxq（财务分析详情，上游暂不支持）/
        tjcj（最新报告期探测）/ zysy（财务首页抬头）/ zybzxq（主要指标对比）。

        字段为单位后缀的标准英文名（`_yuan` 元 / `_pct` 百分数 / `_vol` 股）；
        原始列在每行 raw 里。

        Args:
            symbol: 6 位股票代码（可带前缀）。
            kind: 报表类型，默认 zcfz。
            max_rows: 最大返回行数（0=配置默认，最大 1000）。

        Returns:
            JSON：cols/rows/provenance + source/latency_ms/available_kinds/note。
        """
        if (msg := _require_enabled("tdx_get_finance")) is not None:
            return msg
        started = time.time()
        try:
            kinds = _finance_kinds()
            kind = (kind or "zcfz").strip()
            if kind not in kinds:
                return error_response(
                    f"kind 只接受 {kinds}（收到 {kind!r}）",
                    "tdx_get_finance",
                )
            sym = normalize_symbol(symbol)
            table = await _run(
                tdf.call, "get_finance", sym, kind=kind, timeout=30
            )
            return _table_payload(
                table,
                tool="tdx_get_finance",
                started=started,
                max_rows=_max_rows(max_rows),
                extra={
                    "symbol": sym,
                    "kind": kind,
                    "available_kinds": kinds,
                    "note": (
                        "标准英文字段，单位后缀 _yuan/_pct/_vol；原始列在每行 raw。"
                        "cwfxxq 上游返回多 ResultSet 且列义未定，会抛 NotSupportedError。"
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _err("tdx_get_finance", exc)

    @mcp.tool()
    async def tdx_get_capital_flow_rank(top_n: int = 20) -> str:
        """
        获取全市场**主力净流入榜**（tdxdata ph_zt_zjlx_cxlclr）。

        ⚠️ 上游固定返回前 20 名；不支持按单代码查询（名单命令没有代码位）。
        单代码资金流请继续用现有 `get_money_flow`。

        标准字段：code/name/market/price/net_inflow(元)/net_inflow_pct/
        change_pct(%)/net_inflow_continued(元)/net_inflow_days/change_pct_period(%)/date。
        其中 price / net_inflow_pct 本通道恒为 null（上游不给，不填 0）。

        Args:
            top_n: 返回前多少名（默认 20，最大 20；上游固定 20）。

        Returns:
            JSON：cols/rows/provenance + source/latency_ms/note。
        """
        if (msg := _require_enabled("tdx_get_capital_flow_rank")) is not None:
            return msg
        started = time.time()
        try:
            n = int(top_n or 20)
        except (TypeError, ValueError):
            n = 20
        n = max(1, min(n, 20))
        try:
            table = await _run(tdf.call, "get_capital_flow", None, timeout=30)
            return _table_payload(
                table,
                tool="tdx_get_capital_flow_rank",
                started=started,
                max_rows=n,
                extra={
                    "top_n": n,
                    "note": (
                        "全市场净流入榜（上游固定前 20）；不支持单代码。"
                        "net_inflow/net_inflow_continued 单位为元；change_pct/"
                        "change_pct_period 为百分数；price/net_inflow_pct 恒 null。"
                        "单代码资金流请用 get_money_flow。"
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _err("tdx_get_capital_flow_rank", exc)

    @mcp.tool()
    async def tdx_get_lhb_review(date: str = "", max_rows: int = 0) -> str:
        """
        获取 tdxdata 龙虎榜复盘序列（`lhfp`，近 60 个交易日）。

        **列义映射（有证据才映射）**：
          - `N001` → `date`：日期（强证据）
          - `N002` → `listed_count`：上榜股票数（与东财龙虎榜按日去重股票数
            连续 5 个交易日完全一致）
          - `N003` → `net_amount_yuan`：TDX 复盘口径净额（元）。已确认与
            `N002` 同属「全部」汇总行；但**与东财 `BILLBOARD_NET_AMT` 汇总不完全一致**
            （如 2026-10-29 TDX -12.52 亿 vs 东财 -9.76 亿），只建议看方向/量级；
            需要东财精确口径请用 `get_dragon_tiger`。
          - `N004` / `N005` / `N006` **不映射**：三者满足
            `N002 = N004 + N005 + N006`，但语义未证实（不猜），原值保留在 `raw`。

        返回行同时带 `raw`（N004–N006 原值）。

        Args:
            date: 可选，结束日期 "YYYY-MM-DD"（返回该日往前 60 个交易日）；
                  留空使用上游默认样例日期。
            max_rows: 最大返回行数（0=默认 60，最大 120）。

        Returns:
            JSON：rows（date/listed_count/net_amount_yuan/raw）+ mapped_fields/
            unmapped_fields/note/provenance。
        """
        if (msg := _require_enabled("tdx_get_lhb_review")) is not None:
            return msg
        started = time.time()
        try:
            kwargs = {"date": date} if date else {}
            ds = await _run(tdf.call, "get", "lhfp", timeout=30, **kwargs)
            table = getattr(ds, "table", None)
            rows_in = list(getattr(table, "rows", None) or [])
            if not rows_in:
                return error_response(
                    "lhfp 返回空表（tdxdata 当期无数据）", "tdx_get_lhb_review"
                )
            limit = min(120, _max_rows(max_rows if max_rows else 60))
            mapped = []
            for row in rows_in:
                mapped.append(
                    {
                        "date": row.get("N001"),
                        "listed_count": _int_or_none(row.get("N002")),
                        "net_amount_yuan": _float_or_none(row.get("N003")),
                        "raw": {
                            "N004": row.get("N004"),
                            "N005": row.get("N005"),
                            "N006": row.get("N006"),
                        },
                    }
                )
            prov = None
            if table is not None and getattr(table, "provenance", None) is not None:
                try:
                    prov = table.provenance.to_dict()
                except Exception:  # noqa: BLE001
                    prov = None
            return dict_to_json(
                {
                    "source": "tdxdata",
                    "tool": "tdx_get_lhb_review",
                    "product": "lhfp",
                    "date": date or None,
                    "latency_ms": round((time.time() - started) * 1000, 1),
                    "total_rows": len(mapped),
                    "returned_rows": len(mapped[:limit]),
                    "truncated": len(mapped) > limit,
                    "rows": mapped[:limit],
                    "mapped_fields": {
                        "N001": "date",
                        "N002": "listed_count（与东财按日去重股票数 5 日逐日一致）",
                        "N003": "net_amount_yuan（TDX 复盘口径，与东财汇总不完全一致）",
                    },
                    "unmapped_fields": {
                        "N004": "未证实；与 N005/N006 满足 N002=N004+N005+N006",
                        "N005": "未证实；原值保留",
                        "N006": "未证实；原值保留",
                    },
                    "provenance": prov,
                    "note": (
                        "只映射有交叉证据的列。N003 为 TDX 口径净额，"
                        "不代表东财 BILLBOARD_NET_AMT；精确东财口径请用 get_dragon_tiger。"
                        "N004-N006 语义未证实，不猜列义。"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _err("tdx_get_lhb_review", exc)

    @mcp.tool()
    async def tdx_get_lhb_stocks(
        date: str = "", view: str = "list", max_rows: int = 0
    ) -> str:
        """
        获取龙虎榜个股明细（tdxdata `lhzy` / `lhbsj`）。

        `view`:
          - `"list"`（默认）→ `lhbsj`：全量个股行（同一股票可能因不同异动原因
            出现多行；含价格/涨幅/净买额/成交额/上游标签）。
          - `"detail"` → `lhzy`：上游排序前 20，含净买占比、TDX 异动码、
            标签 JSON、概念 JSON。

        **列义（已用东财逐股交叉验证）**：
          - 通用：code/name/market（0=深 1=沪 2=北）、change_pct(%)
          - list：price（元）、net_buy_amount_yuan（龙虎榜净买入额，元）、
            amount_yuan（成交额，元）、tag（上游标签，如 "3日"/"同城"，可为空）
          - detail：amount_yuan、net_buy_amount_yuan、net_buy_ratio_pct
            （= net_buy/amount×100，已逐股验算）、reason_code（TDX 异动码，
            **未翻译**）、date、tags（JSON list）、concepts（JSON list）
          - `N011`/`N012`（detail）为上游空值/UI 字段，保留在 raw。

        Args:
            date: 可选交易日 "YYYY-MM-DD"；留空=上游最新交易日。
            view: "list" | "detail"。
            max_rows: 最大返回行数（0=默认 list 200 / detail 20，最大 500）。

        Returns:
            JSON：rows + mapped_fields/unmapped_fields/note/provenance。
        """
        if (msg := _require_enabled("tdx_get_lhb_stocks")) is not None:
            return msg
        started = time.time()
        view = (view or "list").strip().lower()
        if view not in ("list", "detail"):
            return error_response(
                f"view 只接受 'list' / 'detail'（收到 {view!r}）",
                "tdx_get_lhb_stocks",
            )
        try:
            if view == "list":
                params = ["00103", date or "", 0, 0, "", 1, 365]
                table = await _run(
                    tdf.call,
                    "call",
                    "DataAggregation.lhbsj",
                    0x17,
                    params,
                    timeout=45,
                )
            else:
                params = ["00101", "-1", date or "", "6", "1", "1", ""]
                table = await _run(
                    tdf.call,
                    "call",
                    "CWServ.ph_zt_lhb_sy_zy",
                    0x18,
                    params,
                    timeout=45,
                )

            rows_in = list(getattr(table, "rows", None) or [])
            if not rows_in:
                return error_response(
                    f"龙虎榜个股明细返回空表（view={view}, date={date or 'latest'}）",
                    "tdx_get_lhb_stocks",
                )
            default_rows = 200 if view == "list" else 20
            limit = min(500, _max_rows(max_rows if max_rows else default_rows))

            mapped: list[dict] = []
            if view == "list":
                for row in rows_in:
                    mapped.append(
                        {
                            "name": row.get("N001"),
                            "code": row.get("N002"),
                            "market": _int_or_none(row.get("N003")),
                            "price": _float_or_none(row.get("N004")),
                            "change_pct": _float_or_none(row.get("N005")),
                            "net_buy_amount_yuan": _float_or_none(row.get("N006")),
                            "amount_yuan": _float_or_none(row.get("N007")),
                            "tag": row.get("N008") or "",
                        }
                    )
                mapped_fields = {
                    "N001": "name",
                    "N002": "code",
                    "N003": "market（0=深 1=沪 2=北，已用沪深北样本核对）",
                    "N004": "price（元）",
                    "N005": "change_pct（%）",
                    "N006": "net_buy_amount_yuan（与东财 BILLBOARD_NET_AMT 逐股一致）",
                    "N007": "amount_yuan（与东财 ACCUM_AMOUNT 逐股一致）",
                    "N008": "tag（上游标签，可为空）",
                }
                unmapped_fields = {}
                note = (
                    "lhbsj 全量个股行；同一股票可能因不同异动原因出现多行，"
                    "tag 是上游标签（如 3日/同城）。字段已用东财逐股交叉验证。"
                )
            else:
                for row in rows_in:
                    mapped.append(
                        {
                            "code": row.get("N001"),
                            "market": _int_or_none(row.get("N002")),
                            "name": row.get("N003"),
                            "change_pct": _float_or_none(row.get("N004")),
                            "amount_yuan": _float_or_none(row.get("N005")),
                            "net_buy_amount_yuan": _float_or_none(row.get("N006")),
                            "net_buy_ratio_pct": _float_or_none(row.get("N007")),
                            "reason_code": row.get("N008"),
                            "date": row.get("N009"),
                            "tags": _json_list(row.get("N010")),
                            "concepts": _json_list(row.get("N013")),
                            "raw": {"N011": row.get("N011"), "N012": row.get("N012")},
                        }
                    )
                mapped_fields = {
                    "N001": "code",
                    "N002": "market（0=深 1=沪 2=北）",
                    "N003": "name",
                    "N004": "change_pct（%）",
                    "N005": "amount_yuan（与东财 ACCUM_AMOUNT 逐股一致）",
                    "N006": "net_buy_amount_yuan（与东财 BILLBOARD_NET_AMT 逐股一致）",
                    "N007": "net_buy_ratio_pct（= N006/N005×100，已逐股验算）",
                    "N008": "reason_code（TDX 异动码，未翻译）",
                    "N009": "date",
                    "N010": "tags（JSON list）",
                    "N013": "concepts（JSON list）",
                }
                unmapped_fields = {
                    "N011": "上游空值",
                    "N012": "上游 UI 字段（clickEvent1），保留 raw",
                }
                note = (
                    "lhzy 上游排序前 20；amount/net_buy 为对应「主异动」口径。"
                    "N008 是 TDX 内部异动码，未翻译成中文；"
                    "需要东财原因文本请用 get_dragon_tiger。"
                )

            prov = None
            if getattr(table, "provenance", None) is not None:
                try:
                    prov = table.provenance.to_dict()
                except Exception:  # noqa: BLE001
                    prov = None
            return dict_to_json(
                {
                    "source": "tdxdata",
                    "tool": "tdx_get_lhb_stocks",
                    "view": view,
                    "date": date or None,
                    "latency_ms": round((time.time() - started) * 1000, 1),
                    "total_rows": len(mapped),
                    "returned_rows": len(mapped[:limit]),
                    "truncated": len(mapped) > limit,
                    "rows": mapped[:limit],
                    "mapped_fields": mapped_fields,
                    "unmapped_fields": unmapped_fields,
                    "provenance": prov,
                    "note": note,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _err("tdx_get_lhb_stocks", exc)
