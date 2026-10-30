# -*- coding: utf-8 -*-
"""涨跌停价 —— **纯本地计算，零依赖、零网络**。

契约来源：`API设计.md` §2.7「P2 涨跌停 `tdxdata/limits.py`」（唯一契约）。

为什么要单独一个模块：涨跌停取决于**板块**（10%/20%/30%）与 **ST**（主板 5%），
而且**必须用十进制 + ROUND_HALF_UP** 取整到分。用内置 `round()` 会错在两点：
  · Python 3 的 `round()` 是**银行家舍入**（`round(0.5) == 0`），交易所是四舍五入；
  · 浮点二进制误差会让"恰好落在半分"的价格被舍到另一边。
例：`prev_close=10.55`、主板 → 涨停 `10.55*1.1 = 11.605`，
正确价 **11.61**，而 `round(11.605, 2)` 在浮点下给出 **11.6**（漏了一分钱）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__all__ = ["board_of", "ratio_of", "price_limits", "tick_of"]

_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")
_ONE = Decimal("1")

# 板块前缀表（契约 §2.7 + 2026-10-26 手机通达信实测修正）
_MAIN_PREFIXES = ("60", "00")
_CHINEXT_PREFIXES = ("30",)
_STAR_PREFIXES = ("68",)
_BSE_PREFIXES = ("43", "83", "87", "88", "92")
_FUND_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")
# 可转债 / 可交换债（按 3 位前缀）：沪 110/111/113/118/132，深 120/123/127/128
_BOND_PREFIXES = ("110", "111", "113", "118", "120", "123", "127", "128", "132")
# B 股：沪 900、深 200
_BSHARE_PREFIXES = ("900", "200")

# 各板块的涨跌幅比例（非 ST）
# bond=0.20：沪深可转债现行 ±20%（2026-10-26 手机通达信 113054 涨停 144.461 实测确认）
_RATIO = {
    "main": 0.10,
    "chinext": 0.20,
    "star": 0.20,
    "bse": 0.30,
    "fund": 0.10,
    "bond": 0.20,
    "bshare": 0.10,
    "unknown": 0.10,
}
_ST_RATIO = 0.05          # 只对主板生效

# 最小价格变动单位：3 位小数的品种按 0.001 取整，其余 0.01
# （2026-10-26 实测：510300 涨停 4.596、900901 涨停 0.626，用 0.01 会各差 0.004）
_TICK = {
    "main": _CENT,
    "chinext": _CENT,
    "star": _CENT,
    "bse": _CENT,
    "unknown": _CENT,
    "fund": _MILLI,
    "bond": _MILLI,
    "bshare": _MILLI,
}


def _norm_code(code) -> str:
    """校验并规范化代码：必须是 6 位 ASCII 数字，否则 `ValueError`。"""
    s = str(code).strip()
    if len(s) != 6 or not (s.isascii() and s.isdigit()):
        raise ValueError("code 必须是 6 位数字（如 '600519'），收到 %r" % (code,))
    return s


def board_of(code) -> str:
    """按 6 位代码前缀判定板块。

    返回 `"main"`（主板 60/00）、`"chinext"`（创业板 30）、`"star"`（科创板 68）、
    `"bse"`（北交所 43/83/87/88/92）、`"fund"`（基金 15/16/50/51/52/56/58）、
    `"bond"`（可转债/可交换债 110/111/113/118/120/123/127/128/132）、
    `"bshare"`（B 股 900/200）、其余 `"unknown"`。代码非 6 位数字 → `ValueError`。
    """
    s = _norm_code(code)
    head, head3 = s[:2], s[:3]
    if head3 in _BOND_PREFIXES:
        return "bond"
    if head3 in _BSHARE_PREFIXES:
        return "bshare"
    if head in _MAIN_PREFIXES:
        return "main"
    if head in _CHINEXT_PREFIXES:
        return "chinext"
    if head in _STAR_PREFIXES:
        return "star"
    if head in _BSE_PREFIXES:
        return "bse"
    if head in _FUND_PREFIXES:
        return "fund"
    return "unknown"


def tick_of(code) -> float:
    """最小价格变动单位：基金 / 转债 / B 股 `0.001`，其余 `0.01`。"""
    return float(_TICK[board_of(code)])


def ratio_of(code, is_st: bool = False) -> float:
    """涨跌幅比例：主板 / 基金 / B 股 / 未知 `0.10`，创业板 / 科创板 / 转债 `0.20`，北交所 `0.30`。

    `is_st=True` **只在主板生效**（→ `0.05`）；创业板/科创板 ST 仍 `0.20`，
    北交所 ST 仍 `0.30`（契约 §2.7：「ST 只在主板生效」）。
    """
    board = board_of(code)
    if is_st and board == "main":
        return _ST_RATIO
    return _RATIO[board]


def price_limits(prev_close, code, is_st: bool = False, is_new: bool = False):
    """涨跌停价 `(limit_up, limit_down)`，单位元。

    取整口径（契约硬性要求）：`Decimal(str(prev_close)) * Decimal("1"±ratio)`，
    再按该品种的**最小变动单位** `quantize(ROUND_HALF_UP)` —— 股票 2 位小数，
    基金 / 转债 / B 股 3 位小数（见 `tick_of`）。**绝不用内置 `round()`**。

    参数
      · `prev_close` —— 上一交易日收盘价（元），必须 `> 0`，否则 `ValueError`。
      · `code`       —— 6 位代码，非 6 位数字 → `ValueError`。
      · `is_st`      —— ST 股（**只在主板生效** 5%，见 `ratio_of`）。
      · `is_new`     —— 新股上市首日：无固定幅度，**抛 `NotImplementedError`**
        （契约 §2.7 允许；请不要用涨停价近似首日）。

    异常顺序：先校验 `code`（形状），再 `is_new`（能力未实现），最后 `prev_close`。

    实测基准（2026-10-26，手机通达信 / 东方财富一致）：
      · 600519 昨收 1335.00 → 1468.50 / 1201.50
      · 510300 昨收 4.178 → 4.596 / 3.760（3 位小数）
      · 900901 昨收 0.569 → 0.626 / 0.512（3 位小数）
      · 113054 昨收 120.384 → 144.461 / 96.307（转债 ±20%，3 位小数）
    """
    tick = _TICK[board_of(code)]          # 先校验 code 形状（board_of 内部会校验）
    if is_new:
        raise NotImplementedError(
            "新股上市首日（is_new=True）无固定涨跌幅，未实现；"
            "请改用交易所公布的首日有效申报价格区间")
    try:
        pc = Decimal(str(prev_close))
    except (InvalidOperation, ValueError):
        raise ValueError("prev_close 必须是数字，收到 %r" % (prev_close,)) from None
    if not pc.is_finite():
        raise ValueError("prev_close 必须是有限数字，收到 %r" % (prev_close,))
    if pc <= 0:
        raise ValueError("prev_close 必须 > 0，收到 %r" % (prev_close,))

    ratio = Decimal(str(ratio_of(code, is_st=is_st)))
    up = (pc * (_ONE + ratio)).quantize(tick, rounding=ROUND_HALF_UP)
    down = (pc * (_ONE - ratio)).quantize(tick, rounding=ROUND_HALF_UP)
    return (float(up), float(down))
