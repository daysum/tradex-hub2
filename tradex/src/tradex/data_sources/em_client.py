"""东财统一请求客户端 —— 限流防封 + slist 板块归属。

**v3.3.19 起收敛为 `astock_signals.anti_ban_client` 的薄封装**。

历史问题：本项目曾有两套独立的东财防封客户端（本文件 + astock_signals.anti_ban_client），
各自持有 session 与节流器。两个模块同时取数时，实际请求速率是设计值的 2 倍，
且两套指纹/UA 配置不一致（本文件 Chrome/117 + chrome120，anti_ban_client 走裸 requests）。
现统一为单一入口：**一个会话、一把节流锁、一套指纹池**，任何 eastmoney.com 请求
都必须经过它，否则节流形同虚设。

东财风控阈值（社区实测）：
  - 每秒 >5 次 / 并发 ≥10 / 5分钟 ≥300 次 → 触发封禁

底层能力（指纹伪装 / 指数退避重试 / rc=102 限流识别 / 换指纹）见
`astock_signals/antiban_http.py`。
"""
from __future__ import annotations

import pandas as pd

from astock_signals.anti_ban_client import (
    EM_MIN_INTERVAL,
    em_get,
    em_post,
    em_reset_session,
    em_text,
)

__all__ = [
    "em_get", "em_post", "em_text", "em_reset_session",
    "EM_MIN_INTERVAL", "fetch_stock_boards",
]

_REFERER = "https://quote.eastmoney.com/"


def fetch_stock_boards(code: str, **kwargs) -> pd.DataFrame:
    """个股所属板块/概念归属（东财 slist，一次请求拿全行业/概念/地域 + 龙头股）。

    Returns:
        DataFrame columns: 板块名称 / 板块代码(BK) / 涨跌幅 / 领涨股票
    """
    code = str(code).split(".")[0].split("_")[0]  # 归一纯 6 位
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2", "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    r = em_get(
        "https://push2.eastmoney.com/api/qt/slist/get",
        params=params,
        headers={"Referer": _REFERER},
        timeout=15,
    )
    if r is None:
        return pd.DataFrame()
    r.raise_for_status()
    diff = (r.json().get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    rows = []
    for it in items:
        rows.append({
            "板块名称": it.get("f14", ""),
            "板块代码": it.get("f12", ""),
            "涨跌幅": it.get("f3", ""),
            "领涨股票": it.get("f128", ""),
        })
    return pd.DataFrame(rows)
