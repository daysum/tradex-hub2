# -*- coding: utf-8 -*-
"""技术指标 —— **纯本地计算，零依赖、零网络**。

契约来源：`API设计.md` §2.7「P1 指标计算 `tdxdata/indicator.py`」（唯一契约）。

设计取向
  · 只用标准库 `math`，**禁止 numpy/pandas**（本库要能在裸 Python 里跑）
  · **输出长度恒等于输入长度**，头部不足周期的位置填 `None` —— 不截断。
    这样指标序列可以和 `Bar` 列表**按下标一一对齐**（`client.get_indicators`
    就是靠这一点把指标拼成表格列）。
  · `None` 与 `0.0` 严格区分：`None` = "周期不够，没算"，`0.0` = "算出来是 0"。

输入
  `bars` 既可以是 `list[Bar]`（用其 `close`；`kdj` 用 `high`/`low`），
  也可以是 `list[float]`（视为收盘序列，此时 `high = low = close`）。
"""
from __future__ import annotations

import math

__all__ = ["ma", "ema", "macd", "kdj", "rsi", "boll",
           "hv", "momentum", "sharpe", "max_drawdown", "beta"]

# 年化因子：一年按 252 个交易日（通达信/国内券商默认口径）
_SQRT_252 = math.sqrt(252.0)


# ── 内部工具 ─────────────────────────────────────────────────────────────
def _period(n, name: str = "n") -> int:
    """校验周期参数：必须是正整数，`n <= 0` → `ValueError`（契约边界）。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是正整数，收到 %r" % (name, n)) from None
    if n <= 0:
        raise ValueError("%s 必须 > 0，收到 %r" % (name, n))
    return n


def _closes(bars) -> list[float]:
    """取收盘序列：`Bar.close`，数字元素直接用。"""
    out = []
    for b in bars:
        out.append(float(getattr(b, "close", b)))
    return out


def _highs_lows(bars) -> tuple[list[float], list[float]]:
    """取 (最高, 最低) 序列；元素是纯数字时两者都等于它（= 收盘）。"""
    highs, lows = [], []
    for b in bars:
        if hasattr(b, "high"):
            highs.append(float(b.high))
            lows.append(float(b.low))
        else:
            v = float(b)
            highs.append(v)
            lows.append(v)
    return highs, lows


def _ema_series(values: list[float], n: int) -> list[float]:
    """对一串 float 做 EMA，种子 = 首值，**不填 None**（通达信口径）。"""
    if not values:
        return []
    alpha = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


# ── 1. 简单移动平均 ───────────────────────────────────────────────────────
def ma(bars, n) -> list:
    """简单移动平均 `MA(close, n)`。

    公式：`ma[i] = (close[i-n+1] + … + close[i]) / n`。
    口径：前 `n-1` 个位置填 `None`（窗口不足），之后逐个算术平均。
    单位：元（与 `close` 同）。
    """
    n = _period(n)
    c = _closes(bars)
    out: list = [None] * len(c)
    total = 0.0
    for i, v in enumerate(c):
        total += v
        if i >= n:
            total -= c[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


# ── 2. 指数移动平均 ───────────────────────────────────────────────────────
def ema(bars, n) -> list:
    """指数移动平均 `EMA(close, n)`。

    公式：`alpha = 2/(n+1)`；`ema[0] = close[0]`（**种子取首根收盘**）；
    `ema[i] = alpha*close[i] + (1-alpha)*ema[i-1]`。
    口径：**不填 `None`** —— 种子在第一根就有值，全程有值（通达信口径）。
    单位：元。

    注意：因为种子是首根收盘而不是 `n` 根均值，`len(bars) < n` 时
    `ema` 仍有值（这是契约明确规定的行为，不是 bug）。
    """
    n = _period(n)
    return _ema_series(_closes(bars), n)


# ── 3. MACD ──────────────────────────────────────────────────────────────
def macd(bars, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD（通达信口径），返回 `(dif, dea, hist)` 三个**等长**序列。

    公式：
      · `dif  = ema(close, fast) - ema(close, slow)`
      · `dea  = ema(dif, signal)`
      · `hist = 2 * (dif - dea)`  ← **通达信 MACD 柱已乘 2**（与东财/同花顺对得上）
    口径：由 `ema` 派生，三者**都不填 `None`**，长度恒等于输入长度。
    单位：元（价格差，不是百分比）。
    """
    fast = _period(fast, "fast")
    slow = _period(slow, "slow")
    signal = _period(signal, "signal")
    c = _closes(bars)
    if not c:
        return [], [], []
    ef = _ema_series(c, fast)
    es = _ema_series(c, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema_series(dif, signal)
    hist = [2.0 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


# ── 4. KDJ ───────────────────────────────────────────────────────────────
def kdj(bars, n: int = 9, k: int = 3, d: int = 3):
    """KDJ（通达信口径），返回 `(k_line, d_line, j_line)` 三个**等长**序列。

    公式（`k`/`d` 是平滑分母，默认 3 → 权重 2/3）：
      · `rsv = (close - LLV(low, n)) / (HHV(high, n) - LLV(low, n)) * 100`
      · `k_line = ((k-1)*前k + rsv) / k`   # k=3 时即 `(2*前k + rsv)/3`
      · `d_line = ((d-1)*前d + k_line) / d`
      · `j_line = 3*k_line - 2*d_line`
    口径：
      · **K/D 种子取 50**，从第一根 bar 起递推（`k/d` 参数为 1 时退化成 `= rsv`）。
      · 窗口不足时 LLV/HHV **用已有部分**（`[max(0, i-n+1), i]`）计算 rsv，
        但**输出**只在窗口填满（`i >= n-1`）处给值，之前填 `None`。
      · **退化口径（`HHV == LLV`，即窗口内最高=最低）**：分母为 0 时
        `rsv = 0.0`（不做 0/0 除法，也不沿用前值）。这样全平盘会得到
        `k → 33.33 → 22.22 → …` 逐级向 0 收敛的确定值；写成契约式子
        `k=(2*50+0)/3` 即可复现。
    单位：无量纲（0~100 为主，`j` 可越界，可为负）。
    """
    n = _period(n)
    kp = _period(k, "k")
    dp = _period(d, "d")
    closes = _closes(bars)
    highs, lows = _highs_lows(bars)
    m = len(closes)
    if m == 0:
        return [], [], []

    k_line: list = [None] * m
    d_line: list = [None] * m
    j_line: list = [None] * m
    prev_k = 50.0
    prev_d = 50.0
    for i in range(m):
        lo = max(0, i - n + 1)
        hhv = max(highs[lo:i + 1])
        llv = min(lows[lo:i + 1])
        span = hhv - llv
        rsv = 0.0 if span <= 0 else (closes[i] - llv) / span * 100.0
        cur_k = ((kp - 1) * prev_k + rsv) / kp
        cur_d = ((dp - 1) * prev_d + cur_k) / dp
        prev_k, prev_d = cur_k, cur_d
        if i >= n - 1:                      # 窗口未满的位置保持 None
            k_line[i] = cur_k
            d_line[i] = cur_d
            j_line[i] = 3.0 * cur_k - 2.0 * cur_d
    return k_line, d_line, j_line


# ── 5. RSI ───────────────────────────────────────────────────────────────
def rsi(bars, n: int = 14) -> list:
    """相对强弱指标 RSI（**Wilder 递推**口径）。

    公式（`diff[i] = close[i] - close[i-1]`）：
      · `up[i] = (max(diff[i], 0) + (n-1)*up[i-1]) / n`
      · `dn[i] = (max(-diff[i], 0) + (n-1)*dn[i-1]) / n`
      · `rsi[i] = up[i] / (up[i] + dn[i]) * 100`
    口径：
      · 递推种子 `up[1] = max(diff[1], 0)`、`dn[1] = max(-diff[1], 0)`。
      · **首个 rsi 出现在下标 `n`**，之前（含下标 0）填 `None`；
        长度 1 的输入返回 `[None]`。
      · 退化：`up + dn == 0`（区间内完全平盘）时 `rsi = 50.0`（中性）。
    单位：无量纲（0~100）。
    """
    n = _period(n)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    if m == 1:
        return out

    ups: list = [None] * m
    dns: list = [None] * m
    ups[1] = max(c[1] - c[0], 0.0)
    dns[1] = max(c[0] - c[1], 0.0)
    for i in range(2, m):
        diff = c[i] - c[i - 1]
        ups[i] = (max(diff, 0.0) + (n - 1) * ups[i - 1]) / n
        dns[i] = (max(-diff, 0.0) + (n - 1) * dns[i - 1]) / n
    for i in range(n, m):
        u = ups[i]
        dn = dns[i]
        total = u + dn
        out[i] = 50.0 if total == 0 else u / total * 100.0
    return out


# ── 6. 布林带 ─────────────────────────────────────────────────────────────
def boll(bars, n: int = 20, k=2):
    """布林带 `BOLL(close, n, k)`，返回 `(upper, mid, lower)` 三个**等长**序列。

    公式：
      · `mid   = ma(close, n)`
      · `std   = 窗口内的`**样本标准差（分母 `n-1`）**（通达信 `STD` 口径，
        不是总体标准差 `n` —— 这点最容易和别家实现对不上）
      · `upper = mid + k*std`、`lower = mid - k*std`
    口径：前 `n-1` 个位置填 `None`（`mid` 为 `None`，上下轨同样 `None`）。
    `n == 1` 时样本标准差无定义（分母 0），退化为 `std = 0.0` → 上下轨 = mid。
    单位：元。
    """
    n = _period(n)
    kmul = float(k)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return [], [], []
    mid = ma(c, n)
    upper: list = [None] * m
    lower: list = [None] * m
    for i in range(m):
        mu = mid[i]
        if mu is None:
            continue
        if n == 1:
            std = 0.0
        else:
            win = c[i - n + 1:i + 1]
            var = sum((x - mu) ** 2 for x in win) / (n - 1)
            std = math.sqrt(var)
        upper[i] = mu + kmul * std
        lower[i] = mu - kmul * std
    return upper, mid, lower


# ── 阶段 C 增补的收益/窗口工具（下面 5 个指标共用）────────────────────────
def _simple_returns(c: list[float]) -> list:
    """简单收益序列：`r[i] = c[i]/c[i-1] - 1`；`r[0] = None`（没有前一根）。

    前一根收盘 `<= 0` 时该位置记 `None`（除零无定义，不伪造数值）。
    """
    out: list = [None] * len(c)
    for i in range(1, len(c)):
        prev = c[i - 1]
        out[i] = None if prev <= 0.0 else c[i] / prev - 1.0
    return out


def _log_returns(c: list[float]) -> list:
    """对数收益序列：`r[i] = ln(c[i]/c[i-1])`；`r[0] = None`；非正收盘 → `None`。"""
    out: list = [None] * len(c)
    for i in range(1, len(c)):
        prev, cur = c[i - 1], c[i]
        out[i] = math.log(cur / prev) if (prev > 0.0 and cur > 0.0) else None
    return out


def _window_mean_std(win: list):
    """窗口的 `(均值, 样本标准差)`（分母 `len(win)-1`）；含 `None` → `None`。"""
    if any(v is None for v in win):
        return None
    cnt = len(win)
    mu = sum(win) / cnt
    var = sum((v - mu) ** 2 for v in win) / (cnt - 1)
    return mu, math.sqrt(var)


# ── 7. 历史波动率 ─────────────────────────────────────────────────────────
def hv(bars, n: int = 20) -> list:
    """滚动**年化**历史波动率 `HV(close, n)`（**百分数**，`18.5` 表示 18.5%）。

    公式（对数收益，`i >= 1`）：
      · `r[i] = ln(close[i] / close[i-1])`
      · `hv[i] = std(r[i-n+1..i], **样本**标准差 / 分母 n-1) * sqrt(252) * 100`

    口径：
      · **首个有值下标是 `n`**（窗口要 `n` 个收益 = `close[0..n]` 共 `n+1` 根），
        之前（含下标 0）填 `None`。
      · 样本标准差分母 `n-1` —— 与 `boll` 的 `STD` 口径一致（**不是**总体 `n`）。
      · 年化因子 `sqrt(252)`（一年 252 个交易日）；`*100` 把小数值换成百分数。
      · `n < 2` → `ValueError`（样本标准差至少要 2 个收益）。
      · 窗口内出现 `close <= 0`（对数无定义）→ 该位置 `None`，不伪造数值。
    单位：百分数（%）。
    """
    n = _period(n)
    if n < 2:
        raise ValueError("hv 的 n 必须 >= 2（样本标准差需要至少 2 个收益），收到 %r"
                         % (n,))
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    ret = _log_returns(c)
    for i in range(n, m):
        got = _window_mean_std(ret[i - n + 1:i + 1])
        if got is None:
            continue
        out[i] = got[1] * _SQRT_252 * 100.0
    return out


# ── 8. 动量 ───────────────────────────────────────────────────────────────
def momentum(bars, n: int = 20) -> list:
    """动量 `MOM(close, n)`（**百分数**）：`(close[i] / close[i-n] - 1) * 100`。

    口径：
      · `i < n` → `None`（`i == n` 起有值）；长度不足 `n+1` 根时全 `None`。
      · `n < 1` → `ValueError`。
      · `close[i-n] <= 0` 时该位置 `None`（除零无定义）。
      · `0.0` 表示「算出来正好是 0」（横盘），与 `None` 严格区分。
    单位：百分数（%）。
    """
    n = _period(n)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    for i in range(n, m):
        base = c[i - n]
        out[i] = None if base <= 0.0 else (c[i] / base - 1.0) * 100.0
    return out


# ── 9. 夏普比率 ───────────────────────────────────────────────────────────
def sharpe(bars, n: int = 20, rf: float = 0.0) -> list:
    """滚动**年化**夏普比率 `SHARPE(close, n, rf)`（无量纲）。

    公式（简单收益，`i >= 1`）：
      · `r[i] = close[i] / close[i-1] - 1`
      · `mean = 窗口 r[i-n+1..i] 的算术均值`
      · `std  = 同一窗口的`**样本**标准差（分母 `n-1`）
      · `sharpe[i] = (mean - rf/252) / std * sqrt(252)`

    口径：
      · `rf` 是**年化**无风险利率（**小数**，`0.02` = 2%）；按 `rf/252` 折成日频。
      · **首个有值下标是 `n`**（窗口要 `n` 个收益），之前填 `None`。
      · `std == 0`（窗口内收益全等，分母 0）→ `None`（避免 inf/NaN）。
      · `n < 2` → `ValueError`。
    单位：无量纲（收益率/波动率的倍数）。
    """
    n = _period(n)
    if n < 2:
        raise ValueError("sharpe 的 n 必须 >= 2（样本标准差需要至少 2 个收益），收到 %r"
                         % (n,))
    daily_rf = float(rf) / 252.0
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    ret = _simple_returns(c)
    for i in range(n, m):
        got = _window_mean_std(ret[i - n + 1:i + 1])
        if got is None:
            continue
        mu, std = got
        if std == 0.0:
            continue                        # 零波动 → None（不返回 inf）
        out[i] = (mu - daily_rf) / std * _SQRT_252
    return out


# ── 10. 最大回撤 ──────────────────────────────────────────────────────────
def max_drawdown(bars, n: int = 20) -> list:
    """滚动窗口最大回撤 `MDD(close, n)`（**百分数**，恒 `<= 0`）。

    公式（窗口 `close[i-n+1..i]`）：
      · `mdd[i] = min over j in 窗口 of (close[j] / max(close[i-n+1..j]) - 1) * 100`
      即「窗口内每一根相对其之前（含自身）的窗口峰值」的回撤，取最小值。

    口径：
      · `i < n-1` → `None`（窗口还没填满）；窗口填满后每根都有值。
      · 全程创新高时回撤恒为 `0.0`（**不是** `None`）—— 真实算出来就是 0。
      · `n < 1` → `ValueError`；`n == 1` 时窗口只有一根、回撤恒 `0.0`。
      · 窗口内峰值 `<= 0` 时该位置 `None`（除零无定义）。
    单位：百分数（%）。
    """
    n = _period(n)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    for i in range(n - 1, m):
        lo = i - n + 1
        peak = c[lo]
        worst = 0.0 if peak > 0.0 else None
        for j in range(lo, i + 1):
            if c[j] > peak:
                peak = c[j]
            if peak <= 0.0:
                worst = None
                continue
            dd = (c[j] / peak - 1.0) * 100.0
            if worst is None or dd < worst:
                worst = dd
        out[i] = worst
    return out


# ── 11. Beta（对基准的 OLS 斜率）──────────────────────────────────────────
def beta(bars, market, n: int = 20) -> list:
    """相对基准的滚动 OLS 斜率 `BETA(close, 基准close, n)`（无量纲）。

    公式（简单收益，`i >= 1`；个股 `r`、基准 `m` 同式）：
      · `r[i] = close[i] / close[i-1] - 1`、`m[i] = 基准[i] / 基准[i-1] - 1`
      · `beta[i] = cov(r, m) / var(m)`
        = `sum((r-k̄r)*(m-k̄m)) / sum((m-k̄m)**2)`（同窗口、中心化后
        样本口径的 `n-1` 分子分母互相抵消，故等价于 OLS 斜率）
      · 不估 alpha / 无风险项，只给斜率。

    口径：
      · **要求 `len(bars) == len(market)`**（同一交易日历、按下标对齐；
        长度不等 → `ValueError`，**消息里说明「调用方负责日历对齐」**）。
      · **首个有值下标是 `n`**（窗口要 `n` 个收益），之前填 `None`。
      · `var(market) == 0`（基准窗口内收益全等，分母 0）→ `None`（不用 inf 顶替）。
      · `n < 2` → `ValueError`。

    单位：无量纲（个股涨跌幅 / 基准涨跌幅 的倍数；基准 β=1）。

    **注意**：本函数需要**第二个序列**，所以**不在** `client.get_indicators()`
    的展开列里（那条路径只有单标的 `bars`）。请直接调用
    `tdxdata.indicator.beta(个股bars, 基准bars, n)`。
    """
    n = _period(n)
    if n < 2:
        raise ValueError("beta 的 n 必须 >= 2（样本协方差需要至少 2 个收益），收到 %r"
                         % (n,))
    ca = _closes(bars)
    cm = _closes(market)
    if len(ca) != len(cm):
        raise ValueError(
            "beta 要求个股与基准**等长**（调用方负责日历对齐，本函数不猜日期）："
            "len(bars)=%d, len(market)=%d" % (len(ca), len(cm)))
    m = len(ca)
    if m == 0:
        return []
    out: list = [None] * m
    ra = _simple_returns(ca)
    rm = _simple_returns(cm)
    for i in range(n, m):
        win_a = ra[i - n + 1:i + 1]
        win_m = rm[i - n + 1:i + 1]
        if any(v is None for v in win_a) or any(v is None for v in win_m):
            continue
        ma_ = sum(win_a) / n
        mm_ = sum(win_m) / n
        cov = sum((a - ma_) * (b - mm_) for a, b in zip(win_a, win_m))
        var = sum((b - mm_) ** 2 for b in win_m)
        if var == 0.0:
            continue                        # 基准零波动 → None（不返回 inf）
        out[i] = cov / var
    return out
