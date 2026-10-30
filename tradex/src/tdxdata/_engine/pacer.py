# -*- coding: utf-8 -*-
"""限流防护（库内私有）：进程级匀速闸门 + 自适应退避 + 服务器冷却。

为什么必须是**进程级**而不是"每个 Client 各自计时"
--------------------------------------------------
7615 的限流是**服务端按来源**判定的，跟你有几个 Client 对象无关。
如果每个 Client 各算各的 `gap`，那开 3 个 Client 就等于把频率放大 3 倍 —— 必被踢。
所以闸门做成模块级单例，同进程所有 Client 共享同一个"下一次可发送时刻"。

三层防护
--------
1. **匀速闸门**（`wait()`）：全局槽位预约，保证任意两条命令间隔 ≥ `cur`
2. **自适应退避**（`note_limited()` / `note_ok()`）：
   被踢就间隔翻倍（2.2 → 4.4 → 8.8 → 12 上限）；
   连续成功 8 次后缓慢回落（×0.75），不会一直卡在 12 秒
3. **服务器冷却**（`block()` / `ready()`）：
   刚踢过我们的服务器，30 秒内不再连，避免"换个头继续撞同一堵墙"

per-host 槽位：**已删除**（DEF-030 收敛，B5 执行）
--------------------------------------------------------
阶段 5 曾有 `wait(host="x")` / `note_limited(host="x")` 的 per-host 槽位，
但其唯一使用者 `Client(concurrency>1)` 的 lane 已按实测结论删除（配额按
来源 IP 算，换服务器不换配额；详见 `Client` 模块 docstring 的实测表），
这段代码自 2026-10 起就是**无生产调用方的死路**，且带着一个已知缺陷：
`note_limited(host="A")` 之后 `wait(host="B")` 直接返回 `0.0` ——
换台机器就重置了全场退避。

**B5 拍板：方案甲（删除），理由**：
1. `grep -r "wait(host" / "note_limited(host"` 全仓确认：库内 0 个生产调用方
   （`client.py:316/320` 均为无参调用），无隐式依赖（aio.py 不触碰）；
2. 方案乙（`wait(host=…)` 也过全局槽位）是给一个没人用的 API 打补丁，
   净增代码与测试面；方案甲净减 ~25 行，缺陷连根拔掉；
3. 若将来重新引入按主机分流，届时按正确语义重写比保留带洞代码更安全。

兼容面：`stats()` 的 `hosts_tracked` 键**保留、恒为 0** —— `client.py`
的 `rate_stats()` 文档与监控解析都引用它，删键会破坏兼容
（`tests/test_no_concurrency.py` 断言的五个老键也不受影响）。
"""
from __future__ import annotations

import threading
import time

__all__ = ["Pacer", "get_pacer", "reset_pacer"]

BASE_GAP = 2.2          # 实测安全下限（低于 2 秒明显更容易被踢）
MAX_GAP = 12.0          # 退避上限
DECAY_AFTER = 8         # 连续成功多少次开始回落
COOLDOWN = 30.0         # 服务器被踢后的冷却秒数


class Pacer:
    """全局匀速闸门。线程安全。

    槽位只有一种：全局槽位 `_next_at`（per-host 槽位已按 DEF-030 删除，
    理由见模块 docstring）。退避间隔 `cur` 同样是进程级的。
    """

    def __init__(self, base_gap: float = BASE_GAP, max_gap: float = MAX_GAP):
        self._lock = threading.Lock()
        self.base = float(base_gap)
        self.max = float(max_gap)
        self.cur = float(base_gap)
        self._next_at = 0.0             # 下一次允许发送的单调时刻（全局槽位）
        self._ok_streak = 0
        self.limited_events = 0         # 累计被限流次数（供上层判断）
        self._cooldown: dict[str, float] = {}

    # ── 匀速闸门 ──────────────────────────────────────────────────────────
    def wait(self) -> float:
        """预约一个发送槽位并 sleep 到它。返回实际等待秒数。"""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_at)
            self._next_at = slot + self.cur
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return max(0.0, delay)

    # ── 反馈 ──────────────────────────────────────────────────────────────
    def note_ok(self) -> None:
        """一条命令成功：连续成功够多就把间隔慢慢收回基准。"""
        with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= DECAY_AFTER and self.cur > self.base:
                self.cur = max(self.base, self.cur * 0.75)
                self._ok_streak = 0

    def note_limited(self) -> float:
        """被限流/被踢：间隔翻倍（不超过上限），并推后全局槽位。返回新间隔。"""
        with self._lock:
            self.limited_events += 1
            self._ok_streak = 0
            self.cur = min(self.max, max(self.cur * 2.0, 1.0))
            # 把下一个槽位往后推，避免立刻再撞
            now = time.monotonic()
            self._next_at = max(self._next_at, now + self.cur)
            return self.cur

    # ── 服务器冷却 ────────────────────────────────────────────────────────
    def block(self, host: str, seconds: float = COOLDOWN) -> None:
        with self._lock:
            self._cooldown[host] = time.monotonic() + seconds

    def ready(self, host: str) -> bool:
        with self._lock:
            until = self._cooldown.get(host)
            return until is None or time.monotonic() >= until

    # ── 观测 ──────────────────────────────────────────────────────────────
    @property
    def gap(self) -> float:
        with self._lock:
            return self.cur

    def stats(self) -> dict:
        """限流状态快照。

        ⚠️ `base` / `current` / `max` / `limited_events` / `cooling_hosts`
        这五个键的**键名与含义自阶段 0 起就没变**（`tests/test_pacer.py`、
        `README` 的 `rate_stats()` 表格、`golden_runner._NoPacer` 都依赖它们）。
        `hosts_tracked` 兼容保留、**恒为 0**（per-host 槽位已按 DEF-030 删除；
        `client.py::rate_stats` 的文档与既有监控解析仍引用该键）。
        """
        with self._lock:
            return {
                "base": self.base,
                "current": round(self.cur, 2),
                "max": self.max,
                "limited_events": self.limited_events,
                "cooling_hosts": sum(1 for h, t in self._cooldown.items()
                                     if t > time.monotonic()),
                #: per-host 槽位已删（DEF-030），恒为 0 的兼容键。
                "hosts_tracked": 0,
            }


_PACER: Pacer | None = None
_PACER_LOCK = threading.Lock()


def get_pacer(base_gap: float = BASE_GAP) -> Pacer:
    """取进程级单例闸门（首次调用时用 `base_gap` 初始化）。"""
    global _PACER
    with _PACER_LOCK:
        if _PACER is None:
            _PACER = Pacer(base_gap=base_gap)
        return _PACER


def reset_pacer() -> None:
    """重置单例（测试用）。"""
    global _PACER
    with _PACER_LOCK:
        _PACER = None
