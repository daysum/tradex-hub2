# -*- coding: utf-8 -*-
"""ARCH-002：**出处（Provenance）一等字段** —— meta dict 公开面的继任者。

背景与裁决
----------
ARCH-002 是**破坏性重构**（用户裁决，无兼容期）：旧 `Table.meta` /
`Series.meta` 的 **dict 公开面整体移除**。原来散落在 meta dict 里的语义键
（as_of / status / fetched_at / source / elapsed / data_date / fetch_clock /
clock_note / status_note / as_of_note …）收拢成**不可变一等字段** `provenance`。

为什么必须不可变（frozen + tuple）
--------------------------------
旧 meta 是个**可变 dict**，切片/继承/回填都靠"再 copy 一份"，任何一处忘拷贝
就是静默串号。`Provenance` 是 `frozen dataclass`：改字段唯一的入口是
`_replace_for`（dataclasses.replace 的薄壳），改完得到**新对象**，旧对象原样
保留 —— "切片与原容器不共享状态"由此从约定升级成类型保证。

时钟可信度是**类型锁**（闭 DEF-018 / DEF-019）
--------------------------------------------
* `fetched_at` 类型强制为 **aware datetime、UTC+8**（`_FROZEN_TZ`）：
  naive datetime / 字符串进构造器当场抛 `TypeError` —— "服务器时刻"与
  "本机时刻"混用却看不出来，正是 DEF-018 的根源。
* `clock` 是 `ClockSource` 枚举（SERVER / HYBRID / LOCAL），不再是自由字符串：
  bare datetime 判定不出的可信度，从此必须由 `NowInfo`/`Provenance` 显式携带
  （timeliness.judge_* 的签名同步收紧，DEF-019 关闭）。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

__all__ = ["ClockSource", "Provenance"]

#: 整库冻结的时区：**UTC+8**（服务器与交易所同域；fetched_at 只允许这个时区）
_FROZEN_TZ = timezone(timedelta(hours=8), "UTC+8")


def _utc8(dt: datetime, what: str = "fetched_at") -> datetime:
    """把 datetime 归一到 **aware UTC+8**；非 datetime 当场抛 `TypeError`。

    naive datetime 的处理：整库的钟面（服务器时刻、FakeClock、本机兜底）都按
    **UTC+8 钟面**约定（服务器与交易所同域），所以 naive 被视作"UTC+8 钟面缺省
    tzinfo"直接**挂上** `UTC+8` —— 出口恒为 aware，消费方拿到的永远是
    带时区的 datetime，DEF-018 关闭。naive 的"来源不可考"风险由 `clock`
    字段承载（LOCAL 钟如实降级），不靠时区猜。
    """
    if not isinstance(dt, datetime):
        raise TypeError("%s 必须是 datetime，收到 %r" % (what, type(dt).__name__))
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=_FROZEN_TZ)
    return dt.astimezone(_FROZEN_TZ)


class ClockSource(str, Enum):
    """时钟来源三态（`Status` 同款 str 枚举，可直接进 JSON）。"""
    SERVER = "server"      # 探针成功：时刻来自服务器
    HYBRID = "hybrid"      # 服务器日期 + 本机时分（交叉校验通过）
    LOCAL = "local"        # 本机兜底：时刻不可信为服务器口径


@dataclass(frozen=True, slots=True)
class Provenance:
    """一次取数的**出处凭据**（不可变一等字段，替代旧 meta dict 的公开面）。

    字段分工
    --------
    | 字段 | 类型 | 语义 |
    |---|---|---|
    | `fetched_at` | aware datetime（UTC+8） | 取数时刻（探针成功时=服务器时刻） |
    | `clock` | ClockSource | 时刻来源三态 |
    | `host` | str\\|None | 来源主机（None=未记录/本地派生） |
    | `cmd` | str | 来源命令（本地派生表是 `local:indicator` 这类自报名） |
    | `ctl` | int\\|None | 命令的 ctl（无则 None） |
    | `cached` | bool | 是否命中缓存 |
    | `elapsed` | float | 耗时（秒） |
    | `status` | str\\|None | 时效四态之一（"final"/"provisional"/"pending"/"delayed"） |
    | `as_of` | str\\|None | 数据时刻（末根日期等；快照通道为 None） |
    | `data_date` | str\\|None | 数据日（数据中心产品） |
    | `notes` | tuple[str, ...] | 说明性文本（status_note / as_of_note / clock_note / 缺列、回填方法、模板校验等一切"如实告知"） |
    | `extra` | dict（冻结视图） | 结构化旁证（missing_cols / requested / returned / pages / template_check …）：键是登记过的词表，值随样本可比 |

    `notes` 用 tuple、`extra` 用 `MappingProxyType`：容器浅拷贝零成本且**天然
    不可变** —— 切片/继承不再需要"逐键深拷贝"这条纪律。
    """
    fetched_at: datetime
    clock: ClockSource = ClockSource.LOCAL
    host: str | None = None
    cmd: str = ""
    ctl: int | None = None
    cached: bool = False
    elapsed: float = 0.0
    status: str | None = None
    as_of: str | None = None
    data_date: str | None = None
    notes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # DEF-018：fetched_at 出口恒为 aware UTC+8（naive 钟面按 UTC+8 挂 tz）
        object.__setattr__(self, "fetched_at", _utc8(self.fetched_at))
        # DEF-019：clock 必须是 ClockSource（裸字符串当场炸，不静默收编）
        if not isinstance(self.clock, ClockSource):
            raise TypeError(
                "DEF-019：clock 必须是 ClockSource 枚举，收到 %r —— "
                "时钟可信度是类型锁，不许用自由字符串表达" % (self.clock,))
        if isinstance(self.notes, (list, tuple)):
            object.__setattr__(self, "notes", tuple(str(n) for n in self.notes))
        elif self.notes in (None, ""):
            object.__setattr__(self, "notes", ())
        else:
            object.__setattr__(self, "notes", (str(self.notes),))
        object.__setattr__(self, "extra",
                           dataclasses.field(default_factory=dict) if self.extra is None
                           else dict(self.extra))

    # ── 唯一的"修改"入口：返回新对象（frozen 语义的显式化）────────────────
    def _replace_for(self, **kw) -> "Provenance":
        """改字段=造新实例。fetched_at/clock 在此过闸（同 __post_init__）。"""
        if "fetched_at" in kw:
            kw["fetched_at"] = _utc8(kw["fetched_at"])
        if "clock" in kw and not isinstance(kw["clock"], ClockSource):
            raise TypeError("DEF-019：clock 必须是 ClockSource 枚举，收到 %r"
                            % (kw["clock"],))
        return replace(self, **kw)

    def with_notes(self, *add) -> "Provenance":
        """追加说明（不改旧对象）。空串/None 不收。"""
        add_n = tuple(str(a) for a in add if a)
        return self._replace_for(notes=self.notes + add_n) if add_n else self

    def with_extra(self, **kw) -> "Provenance":
        """合并结构化旁证（不改旧对象）。值是 None 的键不收（缺席=没记）。"""
        clean = {k: v for k, v in kw.items() if v is not None}
        return self._replace_for(extra={**self.extra, **clean}) if clean else self

    # ── JSON 出口（to_dict / to_json 用）─────────────────────────────────
    def to_dict(self) -> dict:
        """可 JSON 化的凭据 dict（键集稳定，测试按它对形状）。"""
        return {
            "fetched_at": self.fetched_at.isoformat(sep=" ", timespec="seconds"),
            "clock": self.clock.value,
            "host": self.host,
            "cmd": self.cmd,
            "ctl": self.ctl,
            "cached": self.cached,
            "elapsed": self.elapsed,
            "status": self.status,
            "as_of": self.as_of,
            "data_date": self.data_date,
            "notes": list(self.notes),
            "extra": dict(self.extra),
        }

    def __repr__(self) -> str:
        return ("Provenance(fetched_at=%s, clock=%s, host=%r, cmd=%r, ctl=%r, "
                "cached=%r, elapsed=%r, status=%r, as_of=%r, data_date=%r, "
                "notes=%r, extra=%r)"
                % (self.fetched_at.isoformat(sep=" ", timespec="seconds"),
                   self.clock.value, self.host, self.cmd, self.ctl, self.cached,
                   self.elapsed, self.status, self.as_of, self.data_date,
                   list(self.notes), self.extra))
