"""Phase 3: calculate_risk_metrics 离线测试（纯计算，不联网）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from tradex.server import mcp


def _call(args: dict) -> dict:
    res = asyncio.run(mcp.call_tool("calculate_risk_metrics", args))
    text = res[0][0].text if isinstance(res, tuple) else res[0].text
    return json.loads(text)


def _last(series):
    for value in reversed(series):
        if value is not None:
            return value
    return None


CLOSES = [
    10.0, 10.5, 10.2, 10.8, 11.0, 10.7, 11.2, 11.5, 11.1, 11.8,
    12.0, 11.6, 12.2, 12.5, 12.1, 12.8, 13.0, 12.6, 13.2, 13.5,
    13.1, 13.8,
]
BENCH = [
    100.0, 101.0, 100.5, 102.0, 102.5, 101.8, 103.0, 103.5, 102.8, 104.0,
    104.5, 103.8, 105.0, 105.5, 104.8, 106.0, 106.5, 105.8, 107.0, 107.5,
    106.8, 108.0,
]


def test_risk_metrics_latest_matches_tdxdata_indicator():
    from tdxdata import indicator as ind

    data = _call(
        {"closes": CLOSES, "benchmark_closes": BENCH, "period": 5, "risk_free_rate": 0.02}
    )
    assert data["success"] is True
    assert data["data_points"] == len(CLOSES)
    latest = data["latest"]
    assert latest["annualized_volatility_pct"] == pytest.approx(_last(ind.hv(CLOSES, 5)))
    assert latest["momentum_pct"] == pytest.approx(_last(ind.momentum(CLOSES, 5)))
    assert latest["sharpe"] == pytest.approx(_last(ind.sharpe(CLOSES, 5, rf=0.02)))
    assert latest["max_drawdown_pct"] == pytest.approx(_last(ind.max_drawdown(CLOSES, 5)))
    assert latest["beta"] == pytest.approx(_last(ind.beta(CLOSES, BENCH, 5)))
    assert "series" not in data


def test_risk_metrics_include_series():
    data = _call({"closes": CLOSES, "period": 5, "include_series": True})
    assert data["success"] is True
    series = data["series"]
    assert len(series["annualized_volatility_pct"]) == len(CLOSES)
    assert len(series["momentum_pct"]) == len(CLOSES)
    assert len(series["sharpe"]) == len(CLOSES)
    assert len(series["max_drawdown_pct"]) == len(CLOSES)
    assert "beta" not in series  # 未给基准


def test_risk_metrics_insufficient_data():
    data = _call({"closes": CLOSES[:5], "period": 5})
    assert data["error"] is True
    assert "数据不足" in data["message"]


def test_risk_metrics_period_too_small():
    data = _call({"closes": CLOSES, "period": 1})
    assert data["error"] is True
    assert "period" in data["message"]


def test_risk_metrics_beta_length_mismatch():
    data = _call({"closes": CLOSES, "benchmark_closes": BENCH[:-1], "period": 5})
    assert data["error"] is True
    assert "等长" in data["message"]


def test_risk_metrics_empty_closes():
    data = _call({"closes": []})
    assert data["error"] is True
