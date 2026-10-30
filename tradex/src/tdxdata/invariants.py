# -*- coding: utf-8 -*-
r"""恒等式运行时闸门（批次 E，报告 §6.5）—— **纯函数检查器，零网络零依赖**。

定位与取舍（先读这段）
----------------------
这些检查器回答一个问题："**这批数据自己跟自己打架了吗？**"。六条恒等式全部
来自实测锚定（见各检查器的口径注释），任何一条被打破都意味着：解码错位、
单位错配、或服务端口径变更 —— 每一种都比"数不好看"严重得多。

**闸门不自动开，这是性能取舍**：检查器在取数管道里自动跑会让每次取数多付
O(n) 遍历与误报打断的风险（快照 5577 行 × 每行 3 项比对），而恒等式破坏是
**小概率事件**。所以定位是"**用户显式启用 + 测试全量调用**"：

    from tdxdata import invariants
    bars = c.get_bars("600519", count=300)
    problems = invariants.validate(bars)      # 空 list = 通过
    if problems:
        ...    # 每条 violation 带「哪一条恒等式、第几个元素、是什么值」

分发规则（`validate`）
----------------------
按容器类型派发到能查的检查器组合（方案甲，models.py 零改动）：

| 容器 | 检查器 |
|---|---|
| `Bars` / `Ticks`（有 OHLC 的序列） | `check_ohlc` + `check_amount_vol_close` |
| `Quotes` | `check_ohlc` + `check_pre_close` |
| `Minutes` | `check_minute_points`（points 存在时） |
| `Table`（广度表） | provenance.extra 有 `series` 痕迹才 `check_breadth_total`，否则跳过并如实说明 —— Table 无类型级标记，**能查才查** |

`check_minute_volume_sum` 需要当日 Bar 作对照（独立检查器，不进默认组合）；
`check_pre_close` 单个 `Quote` 也能独立调用。

每条 violation 的形态
---------------------
`Violation(invariant, index, detail)`：`invariant` 是恒等式名（机器可读），
`index` 是元素位置（容器级 violation 为 `None`），`detail` 是含**具体值**的
人读文案。红例文案必须能定位到"哪个元素、哪两个数打架"。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Violation", "check_ohlc", "check_amount_vol_close", "check_breadth_total",
    "check_minute_points", "check_minute_volume_sum", "check_pre_close",
    "validate",
]

#: `amount ≈ vol×100×close` 的相对误差宽限（与 tests/test_hq_extra.py:235 同宽）：
#: 同刻不同字段的服务端取舍（NOW 是最新价、AMOUNT 是累计额）在 5% 内视为同口径。
_AMOUNT_TOL = 0.05


@dataclass(frozen=True)
class Violation:
    """一条恒等式破坏：哪条、第几个元素、具体什么值打架。"""
    invariant: str
    index: int | None
    detail: str

    def __str__(self) -> str:                                # pragma: no cover
        return "[%s]%s %s" % (self.invariant,
                              " #%d" % self.index if self.index is not None else "",
                              self.detail)


def _num(v):
    """可参与算术的数（None/非数值 → None：**跳过不猜**）。

    服务端回包里数字常以**字符串**形态躺在 `raw`（`"20.50"`/`"21164"`），
    所以宽松收数字字符串 —— 但**不猜**："N/A"/空串/非数字一律 None。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ══════════════════════════════════════════════════════════════════════════
# 一、OHLC 包络
# ══════════════════════════════════════════════════════════════════════════
def check_ohlc(items) -> list[Violation]:
    """`low ≤ open,close ≤ high`（`high/low` 也互为包络：low ≤ high）。

    口径：Bar 与 Quote 共用（Quote 的 open/high/low 缺列时默认 0.0 —— 那是
    "没给"不是"是 0"，所以 **None/0 一起跳过不猜**；`pre_close`/`price` 的
    包络同理只在四价全为正数时判）。DEF-011 红线：None = unknown，不是 0。
    """
    out: list[Violation] = []
    for i, it in enumerate(items or []):
        o, h, l, c = (_num(getattr(it, "open", None)),
                      _num(getattr(it, "high", None)),
                      _num(getattr(it, "low", None)),
                      _num(getattr(it, "close", None)) or _num(getattr(it, "price", None)))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue                       # 缺列/未定价 → 跳过不猜（unknown ≠ 0）
        if l > h:
            out.append(Violation("ohlc", i,
                                 "low(%s) > high(%s)：包络倒挂" % (l, h)))
            continue
        for name, v in (("open", o), ("close", c)):
            if not (l <= v <= h):
                out.append(Violation(
                    "ohlc", i,
                    "%s(%s) 超出 [low(%s), high(%s)] 包络" % (name, v, l, h)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 二、量额价自洽
# ══════════════════════════════════════════════════════════════════════════
def check_amount_vol_close(items, *, tol: float = _AMOUNT_TOL) -> list[Violation]:
    """`amount ≈ vol×100×close`（相对误差 ≤5%，与 test_hq_extra.py:235 同宽）。

    口径（实测 2026-10-27，688252）：`vol` 手 ×100 = 股，×`close`(NOW) ≈
    `AMOUNT`（累计成交额）—— 差 <5% 是"NOW 是此刻价、AMOUNT 是全天累计"的
    同刻不同字段取舍。日线用 `close`（收盘价口径更准），分时行用 `price`。
    `vol`/`amount` 任一 None → 跳过（unknown ≠ 0 红线）。
    """
    out: list[Violation] = []
    for i, it in enumerate(items or []):
        vol = _num(getattr(it, "vol", None))
        amount = _num(getattr(it, "amount", None))
        close = _num(getattr(it, "close", None)) or _num(getattr(it, "price", None))
        if vol is None or amount is None or close is None or close <= 0:
            continue                       # 缺任一 → 跳过不猜
        expect = vol * 100.0 * close
        if amount <= 0 or expect <= 0:
            continue                       # 0 量/0 额是合法状态（停牌），不是恒等式破坏
        rel = abs(expect - amount) / amount
        if rel > tol:
            out.append(Violation(
                "amount_vol_close", i,
                "vol(%s)×100×close(%s)≈%s 与 amount(%s) 相对误差 %.1f%% 超过 %.0f%%"
                % (vol, close, round(expect, 2), amount, rel * 100, tol * 100)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 三、涨跌家数守恒
# ══════════════════════════════════════════════════════════════════════════
def check_breadth_total(items, total=None) -> list[Violation]:
    """`up + down + flat ≡ total`（**当日**证券总数；5572 不是跨日真理）。

    口径（2026-10-23 抓包 27 帧）：`field1/2/3` = 涨/跌/平，三者之和恒等于
    **当日有价证券总数**（那一天是 5572）—— 这个常数**每天会变**（新股上市/
    退市），所以**不许硬编码**：
      · `total` 显式给出 → 逐行严格判 `≡ total`；
      · `total=None` → **自校模式**：以首行 `up+down+flat` 为当日基准，
        其余行必须与之相等（行内自洽 + 跨行一致，不引入任何外来常数）。
    零行 → 通过（没东西可查）。
    """
    out: list[Violation] = []
    rows = list(items or [])
    if not rows:
        return out
    base = None
    for i, r in enumerate(rows):
        up = _num(r.get("up_count") if hasattr(r, "get")
                  else getattr(r, "up_count", None))
        down = _num(r.get("down_count") if hasattr(r, "get")
                    else getattr(r, "down_count", None))
        flat = _num(r.get("flat_count") if hasattr(r, "get")
                    else getattr(r, "flat_count", None))
        if up is None or down is None or flat is None:
            continue                       # 缺家数 → 跳过不猜
        s = up + down + flat
        want = total if total is not None else base
        if want is None:
            base = s                       # 自校基准 = 第一行（或首行缺数后的第一行）
            continue
        if s != want:
            out.append(Violation(
                "breadth_total", i,
                "up(%s)+down(%s)+flat(%s)=%s ≢ total(%s)" % (
                    up, down, flat, s, want)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 四、分时点位守恒
# ══════════════════════════════════════════════════════════════════════════
def check_minute_points(minutes, n_points_field=None) -> list[Violation]:
    """`n_points(field7) ≡ len(points)`（test_pb_sample.py 实证 25/25 帧恒等）。

    口径：`PBSample` 回包里 `field7`（服务端声明的本帧点数）与 `field8` 数组
    实际长度**逐帧相等** —— 这就是"逐点、非等距抽样"的判据。恒等式破坏 =
    解码错位或服务端契约变更。

    `n_points_field`：预留的对照值来源钩子（默认 None = 用 rows 里每行的
    `n_points` 字段；外部行形态不同时可传键名/属性名，当前实现只在每行
    自身的 `n_points` 上判 —— 声明值与点数组在同一行，跨行对照没有意义）。
    `points` 缺失（`None`）→ 跳过该行（本地 `count` 截尾后 `n_points` 仍是
    服务端声明值 —— 截尾不改声明，所以截尾行也是合法绿例）。
    """
    out: list[Violation] = []
    rows = list(minutes or [])
    for i, r in enumerate(rows):
        n_declared = _num((r.get("n_points") if hasattr(r, "get")
                           else getattr(r, "n_points", None)))
        points = (r.get("points") if hasattr(r, "get")
                  else getattr(r, "points", None))
        if n_declared is None or points is None:
            continue                       # 缺声明或缺数组 → 跳过不猜
        if int(n_declared) != len(points):
            out.append(Violation(
                "minute_points", i,
                "n_points(field7)=%s ≢ len(points)=%d：逐点恒等破坏"
                % (int(n_declared), len(points))))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 五、分时量合计 ≈ 当日 Bar
# ══════════════════════════════════════════════════════════════════════════
def check_minute_volume_sum(minutes, bar, *, tol: float = _AMOUNT_TOL) -> list[Violation]:
    """240 点 `vol` 合计 ≈ 当日 `Bar.vol`；`amount` 合计 ≈ 当日 `Bar.amount`。

    口径（_dev/smoke.py 实测）：2026-10-27 起 `MinutePoint.vol` 与 `Bar.vol`
    **同为「手」**（Bar 归一化后），所以 240 点 vol 合计 = 当日日线 vol；
    amount 同理（元对元）。相对误差 ≤5%（与 §二 同宽：盘中取分时时当日
    Bar 可能还在走，且分时与日线的字段快照时刻不同）。

    `bar` 是当日日线 Bar（`get_bars(code, period="day", count=1)[-1]`）。
    分时点缺 vol/amount 或 Bar 缺 → 整体跳过（unknown ≠ 0）。
    """
    out: list[Violation] = []
    pts = list(minutes or [])
    if not pts or bar is None:
        return out
    bar_vol = _num(getattr(bar, "vol", None))
    bar_amount = _num(getattr(bar, "amount", None))
    vols = [_num(getattr(p, "vol", None)) for p in pts]
    amounts = [_num(getattr(p, "amount", None)) for p in pts]
    if all(v is None for v in vols) and all(a is None for a in amounts):
        return out                        # 两边都没有 → 没东西可查
    if bar_vol is not None and all(v is not None for v in vols):
        s = sum(vols)
        if bar_vol > 0:
            rel = abs(s - bar_vol) / bar_vol
            if rel > tol:
                out.append(Violation(
                    "minute_volume_sum", None,
                    "%d 点 vol 合计 %s ≁ 当日 Bar.vol %s（相对误差 %.1f%% > %.0f%%）"
                    % (len(pts), round(s, 2), bar_vol, rel * 100, tol * 100)))
    if bar_amount is not None and all(a is not None for a in amounts):
        s = sum(amounts)
        if bar_amount > 0:
            rel = abs(s - bar_amount) / bar_amount
            if rel > tol:
                out.append(Violation(
                    "minute_volume_sum", None,
                    "%d 点 amount 合计 %s ≁ 当日 Bar.amount %s（相对误差 %.1f%%）"
                    % (len(pts), round(s, 2), bar_amount, rel * 100)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 六、昨收跨通道一致
# ══════════════════════════════════════════════════════════════════════════
def check_pre_close(quote, other=None, *, tol: float = 0.001) -> list[Violation]:
    """`pre_close` ↔ 同码同刻的另一通道昨收一致（相对误差 ≤0.1%）。

    口径（README 实证）：`PBSample` 的 `pre_close(field6) = 3658.0256` 与同抓包
    `PBCombHQ(999999).CLOSE = 3658.03` 两位小数一致 —— 同一道昨收事实的两条
    通道。`other` 缺省时查 `quote.raw["CLOSE"]`（PBMultiHQ 行形态）；
    两者都没有 → 跳过（没有对照就不判）。
    """
    out: list[Violation] = []
    pre = _num(getattr(quote, "pre_close", None))
    if pre is None or pre <= 0:
        return out                        # 没有昨收 → 没东西可查
    if other is None:
        raw = getattr(quote, "raw", None) or {}
        other = _num(raw.get("CLOSE"))
    oc = _num(other)
    if oc is None or oc <= 0:
        return out                        # 对照缺失 → 不猜
    rel = abs(pre - oc) / oc
    if rel > tol:
        out.append(Violation(
            "pre_close", None,
            "pre_close(%s) 与对照昨收(%s) 相对误差 %.2f%% 超过 %.1f%%"
            % (pre, oc, rel * 100, tol * 100)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 运行时入口：validate（方案甲，models.py 零改动）
# ══════════════════════════════════════════════════════════════════════════
def validate(container, *, total=None, n_points_field=None) -> list[Violation]:
    """按容器类型分发到能查的检查器组合（**显式调用**，管道不自动跑）。

    分发表（能查才查）：
      · `Bars`/`Ticks`（有 OHLC 的序列）→ `check_ohlc` + `check_amount_vol_close`
      · `Quotes` → `check_ohlc` + `check_pre_close`
      · `Minutes` → `check_minute_points`（行内有 n_points/points 时）
      · `Table` → provenance.extra 有 `series` 痕迹才 `check_breadth_total`，
        否则跳过（Table 无类型级标记，如实说明）
      · 其它/空容器 → `[]`

    `total` 透传给 `check_breadth_total`（当日证券总数；不给 = 自校模式）。
    返回 violations 列表，空 = 通过。
    """
    out: list[Violation] = []
    name = type(container).__name__
    rows = list(container or [])

    if name in ("Bars", "Ticks"):
        out += check_ohlc(rows)
        out += check_amount_vol_close(rows)
    elif name == "Quotes":
        out += check_ohlc(rows)
        for i, q in enumerate(rows):
            out += [Violation(v.invariant, i, v.detail) for v in check_pre_close(q)]
    elif name == "Minutes":
        out += check_minute_points(rows)
    elif name == "Table":
        prov = getattr(container, "provenance", None)
        extra = getattr(prov, "extra", None) or {}
        if "series" in extra:
            out += check_breadth_total(rows, total=total)
        # 没有 series 痕迹的 Table：不查（能查才查），不是漏查 —— 调用方想查
        # 广度恒等式请显式调 check_breadth_total 并给 total。
    return out
