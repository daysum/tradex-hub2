"""
tdxdata 数据中心 MCP 工具（Phase 1）。

本模块把 vendored tdxdata（通达信 7615）的**独有能力**暴露给 AI Agent：
  - 421 个数据中心产品的目录与通用访问
  - 实时/历史涨跌家数
  - 多代码当日分时点位
  - 六类板块清单 / 概念板块成分股
  - 市场统计 / 个股涨跌停价

设计约束（与项目现有风格一致）：
  - 所有 tdxdata 调用经 `data_sources.tdxdata_fetchers.call()`：单例 + 全局锁
    （tdxdata Client 状态非线程安全，且上游 2.2s 进程级节流）。
  - 工具为 async，但底层调用用 `asyncio.to_thread` 放到线程，避免阻塞 MCP 事件循环。
  - 所有返回带 `source="tdxdata"`、`latency_ms`、`provenance`；
    超过 `max_rows` 时截断并标 `truncated=true`。
  - 未登记列义的产品返回原始列，并显式提示，不猜列义。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from ..config import config
from ..data_sources import tdxdata_fetchers as tdf
from ..utils.formatter import dict_to_json, error_response
from ..utils.symbol import normalize_symbol
from . import tdxdata_product_notes as notes

logger = logging.getLogger("tradex.tdxdata")

# ── 合法取值（与上游 tdxdata 对齐）──────────────────────────────────────
_BOARD_KINDS = frozenset({
    "all", "industry", "concept", "area", "style", "index",
    "industry1", "industry2", "industry3",
})
_MARKET_STAT_KINDS = frozenset({"updown", "industry", "sentiment"})
_BREADTH_SERIES = {
    "880005": "涨跌家数（down_count / up_count / flat_count / total）",
    "880006": "停板家数（limit_up_count / limit_down_count）",
    "880866": "近期新低指标点位（value）",
    "880641": "历史新低指标点位（value）",
}
_MAX_SYMBOLS_INTRADAY = 20

# 目录/列义缓存（静态，不联网）
_CATALOG: dict[str, Any] | None = None
_MAPPED_NAMES: set[str] | None = None


class TdxTimeout(RuntimeError):
    """tdxdata 工具层超时（区别于上游自身的 Timeout 异常）。"""


# ══════════════════════════════════════════════════════════════════════
# 基础设施
# ══════════════════════════════════════════════════════════════════════

def _require_enabled(tool: str) -> str | None:
    """未启用时返回 error JSON；启用返回 None。"""
    if tdf.is_enabled():
        return None
    return error_response(
        "tdxdata 未启用（TDXDATA_ENABLED=false），无法使用该工具", tool
    )


async def _run(fn: Callable[..., Any], *args, timeout: float | None = None, **kwargs) -> Any:
    """在线程中执行阻塞的 tdxdata 调用，并施加工具层超时。"""
    limit = float(timeout if timeout is not None else config.TDXDATA_ROUTE_TIMEOUT)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs), timeout=limit
        )
    except asyncio.TimeoutError as exc:  # 3.11+ 即 builtins.TimeoutError
        raise TdxTimeout(
            f"tdxdata 调用超时（>{limit:.0f}s）。上游 2.2s 节流 + 多源排队时属正常，"
            "可减少 count/品种数或稍后重试"
        ) from exc


def _catalog_sync() -> dict[str, Any]:
    """加载 421 个产品目录（静态 JSON，不联网），进程内缓存。"""
    global _CATALOG
    if _CATALOG is None:
        client = tdf.get_client()
        _CATALOG = {p.name: p for p in client.products()}
    return _CATALOG


def _mapped_names_sync() -> set[str]:
    """已登记标准列义的产品名集合（静态，不联网）。"""
    global _MAPPED_NAMES
    if _MAPPED_NAMES is None:
        try:
            from tdxdata.sections.mapping import FIELD_MAP

            _MAPPED_NAMES = set(FIELD_MAP)
        except Exception as exc:  # noqa: BLE001
            logger.debug("load tdxdata FIELD_MAP failed: %s", exc)
            _MAPPED_NAMES = set()
    return _MAPPED_NAMES


def _clamp(value: int, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if n <= 0:
        n = default
    return max(low, min(high, n))


def _max_rows(value: int) -> int:
    return _clamp(value, 1, 1000, int(config.TDXDATA_MAX_ROWS))


def _params_hint(params: Any) -> str:
    text = json.dumps(params, ensure_ascii=False) if params is not None else ""
    hints: list[str] = []
    if "@code@" in text or "#code#" in text or "@code#" in text:
        hints.append("code=6位代码")
    if "@date@" in text or "#date#" in text or "@date#" in text:
        hints.append("date=YYYY-MM-DD")
    if not hints:
        hints.append("默认参数（如需覆盖请传 params）")
    return "；".join(hints)


def _looks_raw_cols(cols: list | tuple | None) -> bool:
    """cols 是否全为服务端原始列（N001/T003/f4/c2…）。"""
    if not cols:
        return False
    raw_re = re.compile(r"^(N\d+|T\d+|f\d+|c\d+)$")
    return all(raw_re.match(str(c)) for c in cols)


def _column_note(mapped: bool, mapping_error: str = "") -> str:
    if mapping_error:
        return (
            "映射失败，已回退为服务端原始列（N001...）：" + mapping_error
        )
    if mapped:
        return "已按 tdxdata FIELD_MAP 映射为标准字段；原始列保留在每行 raw。"
    return (
        "该产品未登记标准列义（或本次未启用映射），cols 为服务端原始列（N001...）；"
        "原始值完整保留。请参考 docs/tdxdata-products.md 或上游 docs/VERIFICATION.md。"
    )


def _table_payload(
    table: Any,
    *,
    tool: str,
    started: float,
    max_rows: int,
    extra: dict | None = None,
) -> str:
    data = table.to_dict()
    rows = data.get("rows") or []
    total = len(rows)
    data["rows"] = rows[:max_rows]
    payload = {
        **data,
        "source": "tdxdata",
        "tool": tool,
        "latency_ms": round((time.time() - started) * 1000, 1),
        "total_rows": total,
        "returned_rows": len(data["rows"]),
        "truncated": total > max_rows,
    }
    if extra:
        payload.update(extra)
    return dict_to_json(payload)


def _dataset_payload(
    ds: Any,
    *,
    tool: str,
    started: float,
    max_rows: int,
    extra: dict | None = None,
) -> str:
    data = ds.to_dict()
    table = data.get("table") or {}
    rows = table.get("rows") or []
    total = len(rows)
    table["rows"] = rows[:max_rows]
    data_date = None
    try:
        data_date = str(ds.data_date) if ds.data_date else None
    except Exception:  # noqa: BLE001
        data_date = None
    payload = {
        **data,
        "source": "tdxdata",
        "tool": tool,
        "latency_ms": round((time.time() - started) * 1000, 1),
        "total_rows": total,
        "returned_rows": len(table["rows"]),
        "truncated": total > max_rows,
        "data_date": data_date,
    }
    if extra:
        payload.update(extra)
    return dict_to_json(payload)


def _err(tool: str, exc: Exception) -> str:
    return error_response(f"{type(exc).__name__}: {exc}", tool)


# ══════════════════════════════════════════════════════════════════════
# 工具注册
# ══════════════════════════════════════════════════════════════════════

def register(mcp: FastMCP):
    """注册 tdxdata 数据中心工具。"""

    @mcp.tool()
    async def tdx_list_products(
        domain: str = "", keyword: str = "", limit: int = 50
    ) -> str:
        """
        列出 tdxdata 的 421 个数据中心产品（**不联网**，静态目录）。

        tdxdata 的产品覆盖：龙虎榜/两融、沪深港通、涨停与题材、财报、F10、
        大宗交易、新股与再融资、股东与持股、估值、资金与筹码、市场统计、
        宏观、基金与 ETF、资讯与 AI 等 17 个域。

        Args:
            domain: 按域过滤，如 "行情" / "财报" / "龙虎榜与两融" / "宏观"。
                    留空返回全部域。
            keyword: 在 产品名/域/中文说明 中模糊搜索，如 "龙虎" / "解禁" / "估值"。
            limit: 最多返回多少条（默认 50，最大 200）。

        Returns:
            JSON：total / returned / truncated / domains / products。
            每个产品含 name / domain / cmd / ctl / params / params_hint /
            mapped（是否有标准列义）/ description / tip。
        """
        if (msg := _require_enabled("tdx_list_products")) is not None:
            return msg
        started = time.time()
        try:
            catalog = await _run(_catalog_sync, timeout=15)
            mapped = _mapped_names_sync()
            domain = (domain or "").strip()
            kw = (keyword or "").strip().lower()
            items: list[dict] = []
            for name, p in sorted(catalog.items(), key=lambda kv: (kv[1].domain, kv[0])):
                if domain and p.domain != domain:
                    continue
                desc = notes.note_of(name) or f"{p.domain}数据产品"
                tip = notes.tip_of(name)
                hay = f"{name} {p.domain} {desc} {tip}".lower()
                if kw and kw not in hay:
                    continue
                items.append(
                    {
                        "name": name,
                        "domain": p.domain,
                        "cmd": p.cmd,
                        "ctl": p.ctl,
                        "params": p.params,
                        "params_hint": _params_hint(p.params),
                        "mapped": name in mapped,
                        "description": desc,
                        "tip": tip,
                    }
                )
            limit = _clamp(limit, 1, 200, 50)
            return dict_to_json(
                {
                    "source": "tdxdata",
                    "tool": "tdx_list_products",
                    "latency_ms": round((time.time() - started) * 1000, 1),
                    "total": len(items),
                    "returned": len(items[:limit]),
                    "truncated": len(items) > limit,
                    "domains": sorted({p.domain for p in catalog.values()}),
                    "products": items[:limit],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_list_products failed")
            return _err("tdx_list_products", exc)

    @mcp.tool()
    async def tdx_get_product(
        name: str,
        params: str = "",
        mapped: bool = True,
        max_rows: int = 0,
        allow_empty: bool = True,
    ) -> str:
        """
        按产品名读取 tdxdata 数据中心产品（421 个产品之一）。

        **使用前先调用 `tdx_list_products` 查产品名/参数模板**。常用产品：
        holiday（节假日）、lhb（龙虎榜）、north（北向）、xjjj（限售解禁）、
        dzjy（大宗交易）、ztzb（涨停统计）、jgcg（机构持股）、zjlx（资金流）、
        gzzc_gg（估值排行）、fin/lyb/xjllb（财报）、jjhq（基金行情）。

        Args:
            name: 产品名（严格匹配，如 "holiday" / "north" / "xjjj"）。
            params: 可选，JSON 数组/对象，用于覆盖产品默认参数。
                    如 "[\"600519\"]" / "[\"00101\",\"600519\"]"。
                    留空则使用产品模板默认参数（多数为最新/汇总视图）。
            mapped: 是否对已登记列义的产品输出标准字段（默认 True）。
                    未登记列义的产品会自动回退为原始列，并在 column_note 说明。
            max_rows: 最大返回行数（0=配置默认 TDXDATA_MAX_ROWS，最大 1000）。
            allow_empty: True（默认）时“有列无行”返回空表；False 时抛 EmptyResult。

        Returns:
            JSON：name / params / domain / cols / rows / provenance /
            total_rows / returned_rows / truncated / data_date / column_note。
        """
        if (msg := _require_enabled("tdx_get_product")) is not None:
            return msg
        started = time.time()
        try:
            catalog = await _run(_catalog_sync, timeout=15)
            key = (name or "").strip()
            spec = catalog.get(key)
            if spec is None:
                # 大小写/前缀容错
                lower_map = {k.lower(): k for k in catalog}
                key = lower_map.get(key.lower(), key)
                spec = catalog.get(key)
            if spec is None:
                import difflib

                suggestions = difflib.get_close_matches(
                    (name or "").strip(), list(catalog), n=5, cutoff=0.4
                )
                return error_response(
                    f"没有产品 {name!r}。可用 tdx_list_products 查看 421 个产品；"
                    f"相近产品：{suggestions or '无'}",
                    "tdx_get_product",
                )

            params_obj = None
            params_text = (params or "").strip()
            if params_text:
                try:
                    params_obj = json.loads(params_text)
                except json.JSONDecodeError as exc:
                    return error_response(
                        f"params 必须是合法 JSON 数组/对象（收到 {params!r}）：{exc}",
                        "tdx_get_product",
                    )
                if not isinstance(params_obj, (list, dict)):
                    return error_response(
                        "params 必须是 JSON 数组或对象，例如 [\"600519\"]",
                        "tdx_get_product",
                    )

            mapped_requested = bool(mapped)
            effective_mapped = mapped_requested and key in _mapped_names_sync()
            mapping_error = ""
            row_limit = _max_rows(max_rows)
            timeout = float(config.TDXDATA_ROUTE_TIMEOUT)

            if params_obj is not None:
                table = await _run(
                    tdf.call,
                    "call",
                    spec.cmd,
                    spec.ctl,
                    params_obj,
                    allow_empty=allow_empty,
                    timeout=timeout,
                )
                if effective_mapped:
                    try:
                        from tdxdata.sections.mapping import apply as apply_mapping

                        table = apply_mapping(table, key)
                    except Exception as exc:  # noqa: BLE001
                        mapping_error = f"{type(exc).__name__}: {exc}"
                        effective_mapped = False
                    if effective_mapped and _looks_raw_cols(getattr(table, "cols", None)):
                        # COL_GUARD 形状不匹配时上游可能只给 raw 而不抛错
                        mapping_error = "上游返回原始列（可能 COL_GUARD 形状不匹配）"
                        effective_mapped = False
                extra = {
                    "name": key,
                    "domain": getattr(spec, "domain", ""),
                    "params_used": params_obj,
                    "params_default": spec.params,
                    "params_overridden": True,
                    "mapped_requested": mapped_requested,
                    "mapped_effective": effective_mapped,
                    "description": notes.note_of(key) or f"{spec.domain}数据产品",
                    "tip": notes.tip_of(key),
                    "column_note": _column_note(effective_mapped, mapping_error),
                }
                return _table_payload(
                    table,
                    tool="tdx_get_product",
                    started=started,
                    max_rows=row_limit,
                    extra=extra,
                )

            ds = None
            if effective_mapped:
                try:
                    ds = await _run(
                        tdf.call,
                        "get",
                        key,
                        allow_empty=allow_empty,
                        mapped=True,
                        timeout=timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    # 映射层（FIELD_MAP / COL_GUARD）失败时回退原始列，
                    # 不让“列义登记不全”把整个产品查询打挂；网络/超时不在此列。
                    mapping_excs: tuple = (ValueError,)
                    try:
                        from tdxdata.errors import NotSupportedError as _TdxNotSupported

                        mapping_excs = (ValueError, _TdxNotSupported)
                    except Exception:  # noqa: BLE001
                        pass
                    if isinstance(exc, mapping_excs):
                        mapping_error = f"{type(exc).__name__}: {exc}"
                        effective_mapped = False
                    else:
                        raise
            if ds is None:
                ds = await _run(
                    tdf.call,
                    "get",
                    key,
                    allow_empty=allow_empty,
                    mapped=False,
                    timeout=timeout,
                )
            if effective_mapped and _looks_raw_cols(
                getattr(getattr(ds, "table", None), "cols", None)
            ):
                # COL_GUARD 形状不匹配时上游可能只给 raw 而不抛错
                mapping_error = "上游返回原始列（可能 COL_GUARD 形状不匹配）"
                effective_mapped = False
            extra = {
                "name": key,
                "domain": getattr(spec, "domain", ""),
                "params_used": getattr(ds, "params", spec.params),
                "params_default": spec.params,
                "params_overridden": False,
                "mapped_requested": mapped_requested,
                "mapped_effective": effective_mapped,
                "description": notes.note_of(key) or f"{spec.domain}数据产品",
                "tip": notes.tip_of(key),
                "column_note": _column_note(effective_mapped, mapping_error),
            }
            return _dataset_payload(
                ds,
                tool="tdx_get_product",
                started=started,
                max_rows=row_limit,
                extra=extra,
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_product")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_product(%s) failed", name)
            return _err("tdx_get_product", exc)

    @mcp.tool()
    async def tdx_get_market_breadth(
        view: str = "summary", max_rows: int = 0
    ) -> str:
        """
        获取全市场**实时**涨跌家数（tdxdata HQServ.PBSdstat）。

        Args:
            view: "summary"（默认，1 行 5 列：涨/跌/平/涨停/跌停）或
                  "minute"（逐分钟序列；部分 uint32 列语义未定，只进 raw）。
            max_rows: 最大返回行数（0=默认 300）。

        Returns:
            JSON：cols/rows/provenance + source/latency_ms/note。
        """
        if (msg := _require_enabled("tdx_get_market_breadth")) is not None:
            return msg
        started = time.time()
        view = (view or "summary").strip().lower()
        if view not in ("summary", "minute"):
            return error_response(
                f"view 只接受 'summary' / 'minute'（收到 {view!r}）",
                "tdx_get_market_breadth",
            )
        try:
            table = await _run(
                tdf.call, "get_market_breadth", view, timeout=30
            )
            return _table_payload(
                table,
                tool="tdx_get_market_breadth",
                started=started,
                max_rows=_max_rows(max_rows if max_rows else 300),
                extra={
                    "view": view,
                    "note": (
                        "实时快照；回包无日期/时间字段，provenance.as_of 恒为 None。"
                        "与 get_market_stat('updown') 的历史 30 日口径不是一回事。"
                        + (
                            " minute 视图的部分 uint32 语义未定，只保留在 raw。"
                            if view == "minute"
                            else ""
                        )
                    ),
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_market_breadth")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_market_breadth failed")
            return _err("tdx_get_market_breadth", exc)

    @mcp.tool()
    async def tdx_get_breadth_history(
        code: str = "880005", count: int = 60
    ) -> str:
        """
        获取历史广度序列（涨跌家数 / 停板家数 / 新低点位）。

        仅支持 tdxdata 已实证的 4 个代码：
          880005 涨跌家数；880006 停板家数；880866 近期新低；880641 历史新低。
        其他 880/881 代码请勿按前缀猜测 —— 它们可能是**价格**板块指数。

        Args:
            code: 上述 4 个代码之一，可带 sh/sz/bj 前缀。
            count: 返回最近多少个交易日（默认 60，最大 500）。

        Returns:
            JSON：命名后的 cols/rows + provenance + series_note。
        """
        if (msg := _require_enabled("tdx_get_breadth_history")) is not None:
            return msg
        started = time.time()
        raw = (code or "880005").strip()
        for pre in ("sh", "sz", "bj"):
            if raw.lower().startswith(pre) and len(raw) > len(pre):
                raw = raw[len(pre):].strip()
                break
        if raw not in _BREADTH_SERIES:
            return error_response(
                f"get_breadth 只支持 {sorted(_BREADTH_SERIES)}（收到 {code!r}）；"
                "其他代码可能是价格板块指数，请用 tdx_get_product 或 eltdx K 线",
                "tdx_get_breadth_history",
            )
        count = _clamp(count, 1, 500, 60)
        try:
            table = await _run(
                tdf.call, "get_breadth", raw, count=count, timeout=30
            )
            return _table_payload(
                table,
                tool="tdx_get_breadth_history",
                started=started,
                max_rows=count,
                extra={
                    "code": raw,
                    "series_note": _BREADTH_SERIES[raw],
                    "note": (
                        "只支持日线；未登记代码上游会拒绝，不要按 880 前缀猜含义。"
                        "880005 的四项之和 ≠ total，差额为停牌/无成交。"
                    ),
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_breadth_history")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_breadth_history failed")
            return _err("tdx_get_breadth_history", exc)

    @mcp.tool()
    async def tdx_get_intraday_samples(
        symbols: str,
        count: int = 0,
        max_rows: int = 0,
    ) -> str:
        """
        获取多代码**当日分时点位**（tdxdata HQServ.PBSample）。

        ⚠️ 实验性：上游仅对指数 999999 做过实证；个股语义未复核。
        ⚠️ 一只一次调用（约 2.2s/只），建议一次不超过 5~10 只。

        Args:
            symbols: 逗号/空格分隔的代码，如 "999999,399001"；
                     也接受 JSON 数组字符串 '["999999","399001"]'。
            count: 每只保留最近多少个点（0=全部，最大 240）。
            max_rows: 最多返回多少只（0=默认 20）。

        Returns:
            JSON：每只一行，含 code/market/total_points/pre_close/n_points/points。
        """
        if (msg := _require_enabled("tdx_get_intraday_samples")) is not None:
            return msg
        started = time.time()
        text = (symbols or "").strip()
        if not text:
            return error_response("symbols 不能为空", "tdx_get_intraday_samples")
        try:
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("JSON 必须是数组")
                wanted = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                wanted = [x for x in re.split(r"[,，\s]+", text) if x]
        except Exception as exc:  # noqa: BLE001
            return error_response(
                f"symbols 解析失败（{symbols!r}）：{exc}", "tdx_get_intraday_samples"
            )
        if not wanted:
            return error_response("symbols 解析后为空", "tdx_get_intraday_samples")
        if len(wanted) > _MAX_SYMBOLS_INTRADAY:
            return error_response(
                f"一次最多 {_MAX_SYMBOLS_INTRADAY} 只（收到 {len(wanted)} 只）；"
                "上游一只一次调用、2.2s 节流",
                "tdx_get_intraday_samples",
            )
        count_val = _clamp(count, 1, 240, 240) if count else None
        try:
            table = await _run(
                tdf.call,
                "get_intraday_samples",
                wanted,
                count=count_val,
                timeout=90,
            )
            return _table_payload(
                table,
                tool="tdx_get_intraday_samples",
                started=started,
                max_rows=_max_rows(max_rows if max_rows else 20),
                extra={
                    "symbols": wanted,
                    "count": count_val,
                    "note": (
                        "实验性：上游仅指数 999999 实证；个股语义未复核。"
                        "points 是逐分钟点位数组（n_points == len(points) 已实证）。"
                        "一只一次调用，耗时随标的数线性增加。"
                    ),
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_intraday_samples")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_intraday_samples failed")
            return _err("tdx_get_intraday_samples", exc)

    @mcp.tool()
    async def tdx_get_boards(kind: str = "all", max_rows: int = 0) -> str:
        """
        获取板块清单（行业一/二/三级、概念、风格、地区）。

        Args:
            kind: "all"（industry ∪ concept，较慢约 24s）/ "industry" /
                  "concept" / "area" / "style" / "industry1" / "industry2" /
                  "industry3"。tdxdata **不支持 A 股板块指数清单**（"index"）。
            max_rows: 最大返回行数（0=默认 300）。

        Returns:
            JSON：board_code/board_name/board_type/change_pct + provenance。
        """
        if (msg := _require_enabled("tdx_get_boards")) is not None:
            return msg
        started = time.time()
        kind = (kind or "all").strip().lower()
        if kind not in _BOARD_KINDS:
            return error_response(
                f"kind 只接受 {sorted(_BOARD_KINDS)}（收到 {kind!r}）",
                "tdx_get_boards",
            )
        if kind == "index":
            return error_response(
                "tdxdata 不支持 A 股板块指数清单（上游 NotSupportedError）；"
                "板块指数 K 线请用 tdx_get_product / eltdx K 线",
                "tdx_get_boards",
            )
        try:
            table = await _run(tdf.call, "get_boards", kind, timeout=60)
            board_notes = {
                "all": "all = industry ∪ concept（2 次调用，约 24s），不含 style/area；"
                       "要风格/地区请单独传 kind。",
                "industry": "行业板块清单（约 128 行，含 881xxx 指数码与一级行业归属）。",
                "concept": "概念板块清单（约 269 行全量）。",
                "area": "地区板块清单（约 32 行）。",
                "style": "风格板块清单（约 158 行）。",
                "industry1": "行业一级（约 30 行）。",
                "industry2": "行业二级（约 128 行）。",
                "industry3": "行业三级（约 345 行）。",
            }
            return _table_payload(
                table,
                tool="tdx_get_boards",
                started=started,
                max_rows=_max_rows(max_rows if max_rows else 300),
                extra={
                    "kind": kind,
                    "note": board_notes.get(kind, "")
                    + "change_pct 由 NOW/CLOSE 计算；industry 支路用 industry2 同码回填，"
                    "回填不到为 None。",
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_boards")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_boards failed")
            return _err("tdx_get_boards", exc)

    @mcp.tool()
    async def tdx_get_board_members(board: str, max_rows: int = 0) -> str:
        """
        获取**概念板块**成分股（含入选理由/纳入日期）。

        ⚠️ tdxdata 只有概念板块有成分股通道；行业板块（881xxx）会抛
        NotSupportedError。行业成分股请继续用现有 get_industry_stocks。

        Args:
            board: 板块代码（如 "880564"）或板块名（如 "白酒概念"）。
            max_rows: 最大返回行数（0=默认 500）。

        Returns:
            JSON：code/name/market/price/change_pct/reason/list_date + provenance。
        """
        if (msg := _require_enabled("tdx_get_board_members")) is not None:
            return msg
        started = time.time()
        board = (board or "").strip()
        if not board:
            return error_response(
                "board 不能为空（板块代码如 880564 或板块名）",
                "tdx_get_board_members",
            )
        try:
            table = await _run(tdf.call, "get_board_members", board, timeout=60)
            return _table_payload(
                table,
                tool="tdx_get_board_members",
                started=started,
                max_rows=_max_rows(max_rows if max_rows else 500),
                extra={
                    "board": board,
                    "note": (
                        "仅概念板块支持成分股；行业板块（881xxx）上游会抛 "
                        "NotSupportedError。板块名会先反查 880xxx 代码（多一次调用）。"
                    ),
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_board_members")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_board_members failed")
            return _err("tdx_get_board_members", exc)

    @mcp.tool()
    async def tdx_get_market_stat(
        kind: str = "updown", max_rows: int = 0
    ) -> str:
        """
        获取市场统计（涨跌停家数历史 / 行业涨跌 / 市场情绪）。

        Args:
            kind: "updown"（近 30 个交易日涨跌停家数，默认）/
                  "industry"（行业维度涨跌 + 涨跌家数）/
                  "sentiment"（市场热度值 + 中文判语）。
                  "all" 上游不支持（三类列结构互斥）。
            max_rows: 最大返回行数（0=默认 200）。

        Returns:
            JSON：对应统计表的 cols/rows + provenance。
        """
        if (msg := _require_enabled("tdx_get_market_stat")) is not None:
            return msg
        started = time.time()
        kind = (kind or "updown").strip().lower()
        if kind not in _MARKET_STAT_KINDS:
            return error_response(
                f"kind 只接受 {sorted(_MARKET_STAT_KINDS)}（收到 {kind!r}）；"
                "'all' 上游不支持（三类列结构互斥）",
                "tdx_get_market_stat",
            )
        try:
            # industry 支路是 31 次独立调用（约 68s）；超时必须放宽，
            # 否则会在上游正常取数途中被工具层掐断。
            table = await _run(tdf.call, "get_market_stat", kind, timeout=120)
            return _table_payload(
                table,
                tool="tdx_get_market_stat",
                started=started,
                max_rows=_max_rows(max_rows if max_rows else 200),
                extra={
                    "kind": kind,
                    "note": {
                        "updown": "近 30 个交易日涨跌停家数；含北交所口径。",
                        "industry": "行业维度涨跌 + 涨跌家数；上游约 31 次调用、耗时约 60~70s。",
                        "sentiment": "市场热度值（0~100）+ 中文判语。",
                    }.get(kind, ""),
                },
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_market_stat")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_market_stat failed")
            return _err("tdx_get_market_stat", exc)

    @mcp.tool()
    async def tdx_get_price_limits(
        symbol: str, prev_close: float = 0.0, is_st: bool = False
    ) -> str:
        """
        获取个股涨停价/跌停价（按主板 ±10%、双创 ±20%、北交所 ±30% 等规则）。

        Args:
            symbol: 6 位代码（可带 sh/sz/bj 前缀），如 "600519"。
            prev_close: 昨收价；<=0 时由上游先取 10 根日线推算（多一次请求）。
            is_st: 是否 ST（影响 ±5% 规则）。

        Returns:
            JSON：symbol / prev_close / is_st / limit_up / limit_down。
        """
        if (msg := _require_enabled("tdx_get_price_limits")) is not None:
            return msg
        started = time.time()
        try:
            sym = normalize_symbol(symbol)
            pc = float(prev_close) if prev_close and float(prev_close) > 0 else None
            result = await _run(
                tdf.call,
                "get_price_limits",
                sym,
                prev_close=pc,
                is_st=bool(is_st),
                timeout=30,
            )
            limit_up, limit_down = result
            return dict_to_json(
                {
                    "source": "tdxdata",
                    "tool": "tdx_get_price_limits",
                    "symbol": sym,
                    "prev_close": pc,
                    "is_st": bool(is_st),
                    "limit_up": limit_up,
                    "limit_down": limit_down,
                    "latency_ms": round((time.time() - started) * 1000, 1),
                    "note": (
                        "未给 prev_close 时上游会先取 10 根日线推算昨收（多一次请求）。"
                    ),
                }
            )
        except TdxTimeout as exc:
            return error_response(str(exc), "tdx_get_price_limits")
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_get_price_limits failed")
            return _err("tdx_get_price_limits", exc)

    @mcp.tool()
    async def tdx_status() -> str:
        """
        返回 tdxdata 集成状态（不联网）：版本/产品数/域数/能力项/节流状态/最近调用。

        用于排查“tdxdata 是否启用、当前限流间隔、上次调用了什么”。
        """
        payload: dict[str, Any] = {
            "source": "tdxdata",
            "tool": "tdx_status",
            "enabled": tdf.is_enabled(),
            "config": {
                "TDXDATA_GAP": config.TDXDATA_GAP,
                "TDXDATA_CONNECT_TIMEOUT": config.TDXDATA_CONNECT_TIMEOUT,
                "TDXDATA_ROUTE_TIMEOUT": config.TDXDATA_ROUTE_TIMEOUT,
                "TDXDATA_MAX_ROWS": config.TDXDATA_MAX_ROWS,
            },
        }
        if not tdf.is_enabled():
            payload["message"] = "TDXDATA_ENABLED=false，tdxdata 未启用"
            return dict_to_json(payload)
        try:
            ping = tdf.tdx_ping(probe=False)
            payload.update(
                {
                    "version": ping.get("version"),
                    "products": ping.get("products"),
                    "domains": ping.get("domains"),
                }
            )
            client = tdf.get_client()
            payload["rate_stats"] = client.rate_stats()
            payload["last_call"] = client.last_call
            try:
                caps = client.capabilities(available_only=True)
                payload["capabilities_available"] = len(caps.rows)
            except Exception as exc:  # noqa: BLE001
                payload["capabilities_error"] = f"{type(exc).__name__}: {exc}"
            return dict_to_json(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tdx_status failed")
            return _err("tdx_status", exc)
