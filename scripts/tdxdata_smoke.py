"""tdxdata 集成冒烟测试（Phase 0）。

为什么单独一个脚本：
  tdxdata 走通达信 7615，服务端单命令节流约 2.2 秒且**按来源 IP 限流**。
  把它塞进 `scripts/live_smoke.py` 的全量跑会让默认冒烟从几分钟变成几十分钟，
  也会与 eltdx/东财等源争抢出口 IP 配额。因此单独提供、默认只跑少量调用。

用法：
    python scripts/tdxdata_smoke.py                 # 默认：约 6~8 次调用，约 15~25 秒
    python scripts/tdxdata_smoke.py --no-probe      # 只查版本/产品目录，不联网
    python scripts/tdxdata_smoke.py --full          # 遍历 421 个产品（约 15~20 分钟）
    python scripts/tdxdata_smoke.py --full --domain 行情 --limit 20
    python scripts/tdxdata_smoke.py --boards        # 额外跑板块/成分股（较慢）

判定：
    OK     取到数据
    EMPTY  未抛异常但结果为空（可能是当期无数据/参数为样例值）
    FAIL   抛异常
    SKIP   未启用/参数不足
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 让脚本在仓库任意位置执行都能 import 到 tradex
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tradex" / "src"))

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 老 Python/非 TTY 忽略
    pass

from tradex.data_sources import tdxdata_fetchers as tdf  # noqa: E402


def _fmt_detail(value) -> str:
    """把探针 detail 压成单行短文本。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if isinstance(v, (list, tuple)):
                v = str(v)
            parts.append(f"{k}={v}")
        return ", ".join(parts)
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _run_check(results: list[dict], name: str, fn, empty_ok: bool = False) -> None:
    t0 = time.time()
    try:
        value = fn()
        elapsed = round((time.time() - t0) * 1000, 1)
        if hasattr(value, "shape"):
            rows, cols = value.shape
            detail = f"shape=({rows},{cols})"
            status = "OK" if rows > 0 else ("EMPTY" if empty_ok else "EMPTY")
        elif isinstance(value, (list, tuple)):
            detail = f"len={len(value)}"
            status = "OK" if len(value) > 0 else "EMPTY"
        elif isinstance(value, dict):
            detail = _fmt_detail(value)
            status = "OK" if value else "EMPTY"
        else:
            detail = _fmt_detail(value)
            status = "OK"
        results.append({"name": name, "status": status, "detail": detail, "elapsed_ms": elapsed})
    except Exception as exc:  # noqa: BLE001
        elapsed = round((time.time() - t0) * 1000, 1)
        results.append(
            {
                "name": name,
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": elapsed,
            }
        )


def _print_table(results: list[dict]) -> None:
    print("\n| 项 | 状态 | 详情 | 耗时(ms) |")
    print("|---|---|---|---:|")
    for r in results:
        detail = (r.get("detail") or "").replace("|", "\\|")
        print(f"| {r['name']} | {r['status']} | {detail} | {r.get('elapsed_ms', '')} |")


def _summary(results: list[dict]) -> int:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(
        "\n汇总："
        + " ｜ ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    return 1 if counts.get("FAIL") else 0


def _run_default(results: list[dict]) -> None:
    """默认快速冒烟：覆盖行情/报价/涨跌家数/产品。"""
    ping = tdf.tdx_ping(probe=True)
    if not ping.get("enabled"):
        results.append(
            {
                "name": "tdxdata enabled",
                "status": "SKIP",
                "detail": "TDXDATA_ENABLED=false",
                "elapsed_ms": 0,
            }
        )
        return
    for c in ping.get("checks", []):
        results.append(
            {
                "name": f"ping.{c['name']}",
                "status": c.get("status", "FAIL"),
                "detail": _fmt_detail(c.get("detail") or c.get("error")),
                "elapsed_ms": c.get("elapsed_ms", ""),
            }
        )

    _run_check(results, "get_breadth 880005", lambda: tdf.call("get_breadth", "880005", count=5))
    _run_check(results, "get_market_stat updown", lambda: tdf.call("get_market_stat", "updown"))
    _run_check(results, "get_price_limits 600519", lambda: tdf.call("get_price_limits", "600519"))


def _run_boards(results: list[dict]) -> None:
    """板块/成分股（get_boards 约 20s，按需开启）。"""
    _run_check(results, "get_boards concept", lambda: tdf.call("get_boards", "concept"))
    _run_check(results, "get_board_members 880564", lambda: tdf.call("get_board_members", "880564"))


def _run_full(results: list[dict], domain: str = "", limit: int = 0) -> None:
    """遍历数据中心产品（默认参数）。慢：约 2.2s/产品。"""
    client = tdf.get_client()
    products = client.products(domain=domain or None)
    if limit and limit > 0:
        products = products[:limit]
    total = len(products)
    print(f"开始遍历 {total} 个产品（domain={domain or '全部'}）……")
    ok = empty = fail = 0
    t_start = time.time()
    for i, p in enumerate(products, 1):
        t0 = time.time()
        try:
            ds = tdf.call("get", p.name)
            rows, cols = ds.table.shape
            status = "OK" if rows > 0 else "EMPTY"
            detail = f"({rows},{cols})"
            ok += 1 if rows > 0 else 0
            empty += 1 if rows == 0 else 0
        except Exception as exc:  # noqa: BLE001
            status = "FAIL"
            detail = f"{type(exc).__name__}: {exc}"
            fail += 1
        elapsed = round((time.time() - t0) * 1000, 1)
        results.append({"name": f"product.{p.name}", "status": status, "detail": detail, "elapsed_ms": elapsed})
        if i % 10 == 0 or i == total:
            print(
                f"  进度 {i}/{total}  已用 {time.time() - t_start:.0f}s  "
                f"OK={ok} EMPTY={empty} FAIL={fail}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="tdxdata 集成冒烟测试")
    parser.add_argument("--no-probe", action="store_true", help="只查版本/产品目录，不联网")
    parser.add_argument("--full", action="store_true", help="遍历全部数据中心产品（很慢）")
    parser.add_argument("--domain", default="", help="--full 时按域过滤，如 行情/财报/宏观")
    parser.add_argument("--limit", type=int, default=0, help="--full 时最多跑多少个产品")
    parser.add_argument("--boards", action="store_true", help="额外跑板块/成分股（约 30s）")
    args = parser.parse_args()

    if not tdf.is_enabled():
        print("tdxdata 未启用：TDXDATA_ENABLED=false")
        return 0

    results: list[dict] = []
    t0 = time.time()
    if args.no_probe:
        ping = tdf.tdx_ping(probe=False)
        results.append(
            {
                "name": "static",
                "status": "OK" if ping.get("products") else "FAIL",
                "detail": f"version={ping.get('version')} products={ping.get('products')} domains={ping.get('domains')}",
                "elapsed_ms": ping.get("elapsed_ms", 0),
            }
        )
    else:
        _run_default(results)
        if args.boards:
            _run_boards(results)
        if args.full:
            _run_full(results, domain=args.domain, limit=args.limit)

    _print_table(results)
    print(f"\n总耗时 {time.time() - t0:.1f}s")
    return _summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
