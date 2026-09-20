"""
Hot money tracking — limit-up stocks with theme attribution (涨停归因).

Uses 同花顺 editorial team's curated hot stock data, which includes
human-labeled reason tags explaining WHY each stock surged (e.g. '算力租赁+AI政务').
Also provides theme frequency analysis.

TradingAgents-astock 移植模块。V0.1。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import logging

from .antiban_http import get_client, json_of

logger = logging.getLogger(__name__)

# 同花顺 getharden 接口的 Referer（与客户端会话默认 Referer 一致，显式写出便于阅读）
_THS_REFERER = "https://www.10jqka.com.cn/"


def _fetch_harden(curr_date: str) -> dict:
    """拉取同花顺当日强势股接口原始 JSON。

    v3.3.20：由裸 requests 收敛到统一防封层（指纹伪装 + 节流 + 退避重试）。
    该接口服务端不声明 charset 但内容为 GBK，故 json_of 带 gbk 兜底。

    Raises:
        RuntimeError: 请求重试耗尽。
    """
    url = (
        f"https://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{curr_date}/orderby/date/orderway/desc/charset/GBK/"
    )
    r = get_client("ths").get(url, headers={"Referer": _THS_REFERER}, timeout=10)
    if r is None:
        raise RuntimeError("同花顺 getharden 请求重试耗尽")
    return json_of(r, fallback_encoding="gbk")


def get_hot_stocks(curr_date: str = "") -> str:
    """Get hot stocks with topic attribution from 同花顺 editorial team.

    Returns stocks that hit limit-up with human-curated reason tags
    explaining WHY they surged.

    Args:
        curr_date: Date in YYYY-MM-DD format. Default "" means today.

    Returns:
        Formatted markdown text with hot stocks list and theme frequency.
    """
    if not curr_date or curr_date.strip() == "":
        curr_date = datetime.now().strftime("%Y-%m-%d")

    try:
        data = _fetch_harden(curr_date)

        if data.get("errocode", 0) != 0:
            return f"同花顺 API error: {data.get('errormsg', 'unknown')}"

        rows = data.get("data") or []
        if not rows:
            return (
                f"No hot stocks data for {curr_date} "
                f"(may be non-trading day or data not yet available)"
            )

        lines = [
            f"# Hot Stocks with Topic Attribution ({curr_date})",
            f"# Source: 同花顺 editorial (human-curated reason tags)",
            f"# Total: {len(rows)} stocks",
            "",
        ]

        all_tags: list[str] = []

        for row in rows:
            code = row.get("code", "")
            name = row.get("name", "")
            reason = row.get("reason", "")
            zhangfu = row.get("zhangfu", "")
            huanshou = row.get("huanshou", "")
            chengjiaoe = row.get("chengjiaoe", "")
            dde = row.get("ddejingliang", "")

            lines.append(
                f"{code} {name}: +{zhangfu}% "
                f"换手{huanshou}% 成交额{chengjiaoe} "
                f"大单净量{dde} | {reason}"
            )

            if reason:
                tags = [t.strip() for t in str(reason).split("+") if t.strip()]
                all_tags.extend(tags)

        if all_tags:
            cnt = Counter(all_tags)
            lines.append(f"\n## Theme Frequency (top 15)")
            for tag, n in cnt.most_common(15):
                lines.append(f"  {tag}: {n} stocks")

        return "\n".join(lines)

    except Exception as e:
        logger.error("Error fetching hot stocks for %s: %s", curr_date, e)
        return f"Error fetching hot stocks for {curr_date}: {str(e)}"


def get_hot_stocks_json(curr_date: str = "") -> dict:
    """Get hot stocks as structured JSON dict (for MCP tools).

    Returns:
        Dict with 'stocks' list and 'theme_frequency' dict.
    """
    if not curr_date or curr_date.strip() == "":
        curr_date = datetime.now().strftime("%Y-%m-%d")

    result = {"date": curr_date, "stocks": [], "theme_frequency": {}, "error": None}

    try:
        data = _fetch_harden(curr_date)

        if data.get("errocode", 0) != 0:
            result["error"] = f"同花顺 API error: {data.get('errormsg', 'unknown')}"
            return result

        rows = data.get("data") or []
        all_tags: list[str] = []

        for row in rows:
            stock = {
                "code": row.get("code", ""),
                "name": row.get("name", ""),
                "change_pct": row.get("zhangfu", ""),
                "turnover_pct": row.get("huanshou", ""),
                "volume": row.get("chengjiaoe", ""),
                "dde_net": row.get("ddejingliang", ""),
                "reason": row.get("reason", ""),
            }
            result["stocks"].append(stock)

            reason = row.get("reason", "")
            if reason:
                tags = [t.strip() for t in str(reason).split("+") if t.strip()]
                all_tags.extend(tags)

        cnt = Counter(all_tags)
        result["theme_frequency"] = dict(cnt.most_common(20))
        result["total_count"] = len(rows)

    except Exception as e:
        logger.error("Error fetching hot stocks JSON for %s: %s", curr_date, e)
        result["error"] = str(e)

    return result
