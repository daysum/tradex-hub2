"""
同花顺问财数据源 fetch_fn 包装器。

通过 pywencai 库（可选依赖）或 iwencai OpenAPI 访问同花顺问财数据。
pywencai 支持自然语言查询选股、财务数据、新闻、公告、研报等。
iwencai OpenAPI 支持新闻、公告、研报的专业搜索。

设计原则：
  - pywencai 为可选依赖，未安装时抛 RuntimeError
  - iwencai OpenAPI 需要 IWENCAI_API_KEY 环境变量
  - 明确区分「成功但无结果」与「失败」：
      * 零命中（上游正常返回但没有匹配）      -> 返回空 DataFrame（正常语义）
      * 依赖缺失 / 环境变量缺失 / 网络异常 /
        上游非 200 / 响应不可解析             -> 抛 RuntimeError
  - 抛出的异常由 SmartRouter 汇总成
    "All sources for '<data_type>' failed: ..."，
    最终经工具层 error_response 变成可见的
    {"error": true, "message": ...}，而不是一个空数组。
  - 历史教训：原先一律吞成空 DataFrame，导致鉴权失败、IP 被封、配额
    超限在工具输出里都表现为 []，与「确实没有数据」无法区分。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

logger = logging.getLogger("tradex.wencai")

# iwencai OpenAPI 配置
IWENCAI_BASE_URL = os.environ.get(
    "IWENCAI_BASE_URL", "https://openapi.iwencai.com"
)
IWENCAI_API_KEY = os.environ.get("IWENCAI_API_KEY", "")


def _response_excerpt(resp: Any, limit: int = 300) -> str:
    """提取响应正文摘要，用于把上游错误文案原样带给调用方。

    网关的错误页/错误体往往不带 charset，requests 的 resp.text 可能按
    ISO-8859-1 猜错编码，故优先直接解 content 字节。
    """
    raw = getattr(resp, "content", None)
    text = ""
    if isinstance(raw, (bytes, bytearray)):
        for enc in ("utf-8", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
    else:
        text = str(getattr(resp, "text", "") or "")
    text = " ".join(text.split())
    return text[:limit] if text else "<空响应体>"


# ============================================================
# 问财 OpenAPI 网关规范（X-Claw-* 请求头）
# ============================================================
#
# 网关要求每个请求都携带这套头，缺任一项会直接 401
# "当前 Skill 版本过低，本次请求无法执行"——措辞具有误导性，
# 它跟 key 的有效性、跟 Skill 是否真的"旧"都无关，纯粹是缺头。
# 规范来源（workspace 内技能文档，已实测验证）：
#   .agents/skills/news-search/SKILL.md
#   .agents/skills/announcement-search/SKILL.md
#
# 另一个坑：请求体里的 app_id 是固定值 "AIME_SKILL"。写成别的值
# （如 "tradex"）不会报鉴权失败，而是被 Sentinel 流控挡下，返回
# HTTP 200 + status_code=4 + status_msg=FlowException + total=0。
# 现象酷似"限流"，实际只是标识不匹配，极易误判。
IWENCAI_APP_ID = os.environ.get("IWENCAI_APP_ID", "AIME_SKILL")
IWENCAI_SKILL_VERSION = os.environ.get("IWENCAI_SKILL_VERSION", "1.0.0")

# channel -> X-Claw-Skill-Id；网关按技能登记，各频道对应各自的技能标识。
IWENCAI_SKILL_IDS = {
    "news": "news-search",
    "announcement": "announcement-search",
    "report": "report-search",
}
IWENCAI_DEFAULT_SKILL_ID = "news-search"


def _claw_headers(channel: str) -> dict[str, str]:
    """构造问财网关要求的 X-Claw-* 头。

    Trace-Id 必须每次请求新生成，且为 64 字符（token_hex(32) 恰好 64 位十六进制）。
    """
    import secrets

    return {
        "X-Claw-Call-Type": "normal",
        "X-Claw-Skill-Id": IWENCAI_SKILL_IDS.get(channel, IWENCAI_DEFAULT_SKILL_ID),
        "X-Claw-Skill-Version": IWENCAI_SKILL_VERSION,
        "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none",
        "X-Claw-Trace-Id": secrets.token_hex(32),
    }


def _extract_source(item: dict[str, Any]) -> str:
    """从响应项提取来源。

    上游响应**没有**顶层 `source` 字段（原实现的 item.get("source") 恒为空串）。
    真实来源在 `extra` 里，且网关是以 **JSON 对象** 形式返回的 dict，
    例如 {'publish_source': '微信公众平台', 'real_publish_source': '昆仑策研究院', ...}。
    这里同时兼容 dict 与 dict 字面量字符串两种形态。
    """
    extra = item.get("extra")
    parsed: Any = extra
    if isinstance(extra, str) and extra:
        try:
            import ast

            parsed = ast.literal_eval(extra)
        except (ValueError, SyntaxError):
            parsed = None

    if isinstance(parsed, dict):
        for key in (
            "real_publish_source", "publish_source",
            "organization", "author", "author_name",
        ):
            value = parsed.get(key)
            if value:
                return str(value)
    elif isinstance(extra, str) and extra:
        # extra 为字符串且 literal_eval 失败（被截断）时，退化为正则抽取
        import re

        match = re.search(r"'publish_source':\s*'([^']*)'", extra)
        if match:
            return match.group(1)

    return str(item.get("data_source", "") or "")


def _extract_publish_time(item: dict[str, Any]) -> str:
    """取发布时间：优先可读的 publish_date，退化到把 unix 秒格式化。"""
    readable = item.get("publish_date")
    if readable:
        return str(readable)
    raw = item.get("publish_time")
    if raw in (None, ""):
        return ""
    try:
        from datetime import datetime

        return datetime.fromtimestamp(int(str(raw))).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(raw)


# ============================================================
# pywencai 查询 — wencai_query (pywencai 模式)
# ============================================================

def fetch_wencai_query(
    query: str = "",
    query_type: str = "stock",
    loop: bool = False,
    sort_key: str = "",
    sort_order: str = "desc",
    **kwargs,
) -> pd.DataFrame:
    """同花顺问财自然语言查询（pywencai 可选依赖）。

    通过 pywencai 库执行自然语言查询，获取选股/财务/行情等数据。

    Args:
        query: 自然语言查询语句，如 "市值大于100亿 市盈率小于30"
        query_type: 查询类型，可选 {"stock", "fund", "index"}
        loop: 是否获取所有分页数据
        sort_key: 排序字段
        sort_order: 排序方向 {"asc", "desc"}

    Returns:
        DataFrame with query results（零命中时返回空 DataFrame）

    Raises:
        RuntimeError: pywencai 未安装，或查询失败。
            注意 pywencai 0.13 内部的 while_do 会吞掉异常并返回 None，
            因此「拿到 None」必须按失败处理，不能当成空结果。
    """
    if not query:
        logger.debug("fetch_wencai_query: query is empty")
        return pd.DataFrame()

    try:
        import pywencai
    except ImportError as e:
        raise RuntimeError(
            "pywencai 未安装，无法执行问财查询。请执行: pip install pywencai"
        ) from e

    params: dict[str, Any] = {
        "query": query,
        "query_type": query_type,
        "loop": loop,
        # pywencai 默认 retry=10，且每次尝试都要重新拉起 Node.js 生成
        # hexin-v 指纹（单次约 1s+）。上游不可用时 10 次重试必然超过
        # SmartRouter 的 12s 路由超时，最终只暴露成 "timeout(12.0s)"，
        # 把真实原因（如出口 IP 被问财封禁返回 403）整个盖掉。
        # 压到 2 次，让真实失败原因能及时透出。
        "retry": kwargs.get("retry", 2),
    }
    if sort_key:
        params["sort_key"] = sort_key
        params["sort_order"] = sort_order

    try:
        df = pywencai.get(**params)
    except Exception as e:
        # pywencai 内部的 while_do 会吞掉真实异常并返回 None，随后 get()
        # 在 None 上取字段，抛出 "'NoneType' object has no attribute 'get'"。
        # 这个异常对使用者零信息量，这里翻译成可定位的失败原因。
        if isinstance(e, AttributeError) and "NoneType" in str(e):
            raise RuntimeError(
                "pywencai 未取得有效响应（内部重试全部失败）。"
                "常见原因：本机出口 IP 被同花顺问财封禁(403)、"
                "Node.js 不可用、或 pywencai 内置的 hexin-v 指纹已失效"
            ) from e
        raise RuntimeError(
            f"pywencai 查询失败: {type(e).__name__}: {e}"
        ) from e

    if df is None:
        raise RuntimeError(
            "pywencai 未返回任何数据（上游同花顺问财无有效响应）。"
            "常见原因：本机出口 IP 被问财封禁(403)、Node.js 不可用、"
            "或 pywencai 内置的 hexin-v 指纹已失效"
        )

    if df.empty:
        logger.debug("fetch_wencai_query(%s): empty", query[:50])
        return pd.DataFrame()
    return df


# ============================================================
# iwencai OpenAPI 新闻搜索 — wencai_news (iwencai 模式)
# ============================================================

def fetch_wencai_news(
    keyword: str = "",
    channel: str = "news",
    limit: int = 20,
    **kwargs,
) -> pd.DataFrame:
    """同花顺问财 OpenAPI 新闻/公告/研报搜索。

    通过 iwencai OpenAPI 搜索财经新闻、公告、研报。
    需要设置环境变量 IWENCAI_API_KEY。

    Args:
        keyword: 搜索关键词
        channel: 搜索频道，可选 {"news": 新闻, "announcement": 公告, "report": 研报}
        limit: 返回条数，默认 20

    Returns:
        DataFrame with columns: 标题, 发布时间, 来源, 摘要, 链接
        （零命中时返回空 DataFrame）

    Raises:
        RuntimeError: IWENCAI_API_KEY 未设置、请求重试耗尽、上游返回
            非 200（如 401 鉴权失败）或响应不是合法 JSON。
            上游的错误文案会原样带在异常信息里，便于定位。
    """
    if not keyword:
        logger.debug("fetch_wencai_news: keyword is empty")
        return pd.DataFrame()

    if not IWENCAI_API_KEY:
        raise RuntimeError(
            "IWENCAI_API_KEY 未设置，无法使用 iwencai OpenAPI。"
            "请在 tradex/.env 或环境变量中设置 IWENCAI_API_KEY"
        )

    try:
        import json

        from astock_signals.antiban_http import get_client

        url = f"{IWENCAI_BASE_URL}/v1/comprehensive/search"
        payload = {
            "channels": [channel],
            # 固定值，勿改。写成其它值会被 Sentinel 流控挡下（HTTP 200 +
            # status_code=4 FlowException + total=0），表现为"限流"。
            "app_id": IWENCAI_APP_ID,
            "query": keyword,
        }
        headers = {
            "Authorization": f"Bearer {IWENCAI_API_KEY}",
            "Content-Type": "application/json",
            # 缺这套头 → 401 "当前 Skill 版本过低"
            **_claw_headers(channel),
        }
        data = json.dumps(payload).encode("utf-8")

        # v3.3.20：由裸 urllib 收敛到统一防封层（指纹伪装 + 节流 + 退避重试）
        resp = get_client("wencai").post(url, data=data, headers=headers, timeout=30)
        if resp is None:
            raise RuntimeError("iwencai OpenAPI 请求重试耗尽（防封层未取得任何响应）")

        status = getattr(resp, "status_code", None)
        if status is not None and status != 200:
            raise RuntimeError(
                f"iwencai OpenAPI 返回 HTTP {status}: {_response_excerpt(resp)}"
            )

        try:
            result = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"iwencai OpenAPI 返回非 JSON 响应: {_response_excerpt(resp)}"
            ) from e

        if not isinstance(result, dict):
            raise RuntimeError(
                f"iwencai OpenAPI 返回了非预期的 JSON 结构: {_response_excerpt(resp)}"
            )

        # 业务层错误：网关用 HTTP 200 + status_code != 0 表达
        # （0=OK；4=Sentinel FlowException）。不检查的话会被当成"零命中"。
        biz_code = result.get("status_code")
        if biz_code not in (None, 0):
            raise RuntimeError(
                f"iwencai OpenAPI 业务错误 status_code={biz_code}: "
                f"{result.get('status_msg') or _response_excerpt(resp)}"
            )

        if result.get("error"):
            raise RuntimeError(
                f"iwencai OpenAPI 返回业务错误: {result['error']}"
            )

        # 解析响应：`data` 是**数组**，不是 {"items": [...]}。
        # 原实现的 result["data"]["items"] 会直接 AttributeError。
        items = result.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or []
        if not isinstance(items, list) or not items:
            logger.debug("fetch_wencai_news(%s): empty result", keyword)
            return pd.DataFrame()

        articles = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            articles.append({
                "标题": item.get("title", ""),
                "发布时间": _extract_publish_time(item),
                "来源": _extract_source(item),
                "摘要": item.get("summary", ""),
                "链接": item.get("url", ""),
            })

        if not articles:
            return pd.DataFrame()
        return pd.DataFrame(articles)

    except RuntimeError:
        raise
    except Exception as e:
        logger.warning("fetch_wencai_news(%s) failed: %s", keyword, e)
        raise RuntimeError(
            f"iwencai OpenAPI 请求失败: {type(e).__name__}: {e}"
        ) from e
