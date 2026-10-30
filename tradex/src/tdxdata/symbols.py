# -*- coding: utf-8 -*-
"""标的代码归一化。

对外统一 `"sh600519"` / `"600519"` / `(Market.SH, "600519")` 三种写法都接受，
内部一律转成 `(market, 6 位代码)`。

三条**不许越过的线**（阶段 3 立，逐条都有实证）
----------------------------------------------
1. **位数不对就拒绝，绝不补零/截断**。`parse("00700")` 曾通过校验并被判成深市，
   下游 `PBFXT` 左补 0 → **静默返回「模塑科技 000700」的 K 线**
   （`close=13.58`，与 `get_bars("000700")` 逐条相同）。任何 5 位数字串都会命中
   某只 6 位 A 股（`02800→002800`、`00005→000005` 同样已实证）。
   现在一律 `errors.OutOfScope`，消息里写明去向。
2. **期货/期权/国债期货/外汇 → `OutOfScope`**，理由只能写"不在本数据源证券表内"
   （见 `errors.OutOfScope` 的措辞纪律：`field3` 对已知代码是宽松的，
   所以"取不到"的正确归因是代码不在表里，不是市场码不支持）。
3. **两义代码只在 Client 层提醒**。本模块的 `parse()` 保持**纯函数**
   （无 warning、无异常分支之外的副作用），提醒/严格模式在
   `Client._check_symbol()`；`AMBIGUOUS` 表在这里，是因为"哪些代码两义"属于代码段知识。
"""
from __future__ import annotations

import re as _re

from . import errors

__all__ = [
    "parse", "symbol_of", "market_of", "market_by_code", "clean", "text_of",
    "iter_symbols", "is_explicit", "AMBIGUOUS", "is_futures_like",
]

_PREFIX = {"sh": 1, "sz": 0, "bj": 2}
_SUFFIX = {1: "sh", 0: "sz", 2: "bj"}

# ── 代码段 → 市场（2026-10-26 修正，依据服务端真值）─────────────────────────
# **来源**：`HQServ.PBMultiHQ` 返回的 `Setcode` 列（309 条服务端真值，
# 夹具在 `tests/fixtures/市场码真值样本.json`，用例 `test_symbols_market.py` 全量比对）。
#
# 修正前的老规则是「5/6 开头=沪、83/87/88/92/43/82=北、其余=深」，
# 实测在 309 条样本上 **错 92 条（29.8%）**，错的全是：
#   900 沪B(41) / 113(21) / 118(10) / 204 沪逆回购(9) / 110(4) / 111(3) / 241~243(1 each) / 524(1)
# 后果是真机上 `get_bars("900901")` 抛 `EmptyResult`（报"K线无数据"），用户会以为这票不存在，
# 其实只要把 market 从 0 改成 1 就能取到（实测 10 行、0.569 元）。
#
# 规则：**3 位前缀优先**（一组前缀在真值里都只对应唯一 setcode），1 位兜底。
# 新增品种时**只加表项，不要改逻辑**。
_PREFIX3 = {
    # 沪市 (1)
    "110": 1, "111": 1, "113": 1, "118": 1,   # 沪转债
    "204": 1,                                   # 沪逆回购（1 开头但不在深）
    "241": 1, "242": 1, "243": 1,               # 沪债券
    "900": 1,                                   # 沪 B
    "999": 1,                                   # 沪市指数（999999 上证指数，2026-10-26 手机端确认）
    "880": 1,                                   # 板块指数（880903 水产品 / 880001）；
                                                # 2026-10-26 实测：get_bars("sh880903")=561.57，
                                                # 且抓包里 PBZSTDDE 的 {"Code":"880001","Setcode":1}
    "881": 1,                                   # 行业板块指数（881007 油气开采 / 881218）
                                                # 2026-10-27 实测：PBCombHQ `Setcode=1` → 有数据
                                                # （['881007','1','油气开采']）；`Setcode=2`（北交所）
                                                # 一律 EmptyResult，3/4/5 也全空。
                                                # ⚠️ 880xxx 与 881xxx 是**同一族通达信板块指数**，
                                                # **不是北交所证券** —— 漏了这一项时它们会掉进
                                                # `_HEAD1["8"]` 的北交所兜底，于是 get_quotes/
                                                # get_bars 都取不到（旧 bug，2026-10-27 修）。
    # 深市 (0)
    "123": 0, "127": 0, "131": 0,               # 深转债 / 深回购
    "200": 0, "201": 0,                          # 深 B
    "524": 0,                                    # ⚠️ 5 开头但服务端是深（实测）
    "399": 0,                                    # 深市指数（399001 深证成指）
    # 北交所 (2)
    "920": 2, "430": 2, "830": 2, "870": 2, "820": 2,
}
# 1 位兜底（表里没命中时用）。注意 `1` 兜底必须是 0 —— 深转债 123/127/131 都在 1 段。
# `8` 兜底是北交所证券（43xxxx/83xxxx/87xxxx/92xxxx 里的北交所票；`88xxxx` 里的
# **板块指数 880/881 已在上表单独覆盖**，别把"8 开头"一律当北交所）。
_HEAD1 = {"6": 1, "5": 1, "0": 0, "3": 0, "1": 0, "2": 0, "4": 2, "8": 2, "9": 0}


def market_by_code(code: str) -> int:
    """由 6 位代码推断市场码（3 位前缀优先，1 位兜底）。数据来源见 `_PREFIX3`。"""
    m = _PREFIX3.get(code[:3])
    if m is not None:
        return m
    return _HEAD1.get(code[0], 0)


def clean(code) -> str:
    """取出 6 位数字代码（容忍 `sh`/`sz`/`bj` 前缀、`.SH` 后缀、空格）。

    ⚠️ 这是**内部宽容版**：`s[:6]` 会截断超长串。**用户输入一律走 `parse()`**
    （它先校验位数再返回），别把本函数当成"输入校验"。
    """
    s = str(code).strip().lower()
    for suf in (".sh", ".sz", ".bj", ".ss"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if s[:2] in _PREFIX:
        s = s[2:]
    return s[:6]


def _digits_of(code) -> str:
    """去掉前后缀后的**原样**数字串 —— **不截断、不补零**（校验用）。"""
    s = str(code).strip().lower()
    for suf in (".sh", ".sz", ".bj", ".ss"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if s[:2] in _PREFIX:
        s = s[2:]
    return s


#: 期货/期权/国债期货/外汇的**可识别形态**：1~2 个字母开头 + 后面带数字
#: （或期权全码 `IO2609-C-3900` 这种带连字符的）。这些品种**不在本数据源的证券表里**。
_FUTURES_LIKE = _re.compile(r"^[A-Za-z]{1,2}[0-9][0-9A-Za-z\-]*$")


def is_futures_like(symbol) -> bool:
    """形态上像不像期货/期权/国债期货/外汇代码（`IF2612` / `IF00` / `T00` / `IO2609-C-3900`）。

    ⚠️ 先剥掉 `sh`/`sz`/`bj` 前缀再判形态：`sh600519` 表面符合"2 字母 + 数字"，
    但它显式声明了市场、是**A 股**，不该被这个判据认领。
    """
    s = str(symbol).strip()
    if s[:2].lower() in _PREFIX:
        return False
    return bool(_FUTURES_LIKE.match(s))


#: **两义代码**：同一个写法在不同口径下指向不同东西。
#:
#: 只登记**已实证会造成口径错**的四个；每条都带"该怎么写"的指路文案。
#: ⚠️ 本表**不改变** `parse()` 的返回值（它是纯函数）—— 提醒/严格模式在
#: `Client._check_symbol()`，且**只在调用方没显式指定市场时**触发。
AMBIGUOUS = {
    "000001": "平安银行（深市 000001）。⚠️ **上证指数是 999999，不是 000001**"
              " —— 写成 000001 拿到的是平安银行，这是本项目最常见的口径错。"
              "真要上证指数请写 999999 或 \"sh999999\"。",
    "000300": "深市没有这个代码（深市指数是 399xxx）。⚠️ **沪深300 是沪市指数**，"
              "请写 \"sh000300\" 或 (\"sh\", \"000300\")。",
    "000852": "深市没有这个代码（深市指数是 399xxx）。⚠️ **中证1000 是沪市指数**，"
              "请写 \"sh000852\" 或 (\"sh\", \"000852\")。",
    "000016": "深市没有这个代码（深市指数是 399xxx）。⚠️ **上证50 是沪市指数**，"
              "请写 \"sh000016\" 或 (\"sh\", \"000016\")。",
}


def is_explicit(symbol) -> bool:
    """这个写法是否**显式指定了市场**（`sh600519`，或 `("sh","600519")` / `(1,"600519")`）。

    两义提醒只在"市场是猜出来的"时候发 —— 显式写了 `sh000300` 还提醒一遍，
    等于把正确的写法也判成可疑，指路文案就白写了。
    """
    if isinstance(symbol, (tuple, list)) and len(symbol) >= 2:
        return True
    return str(symbol).strip().lower()[:2] in _PREFIX


def iter_symbols(symbols_) -> list:
    """把"单个标的 / 批量标的"拆成逐个标的（**口径与 `hq_extra._pairs` 保持一致**）。

    `"600519"` / `"sh600519"` → 一个；`("sh","600519")` 这种二元组也算**一个**；
    其它可迭代对象 → 逐个。
    """
    if isinstance(symbols_, (str, bytes)):
        return [symbols_]
    return list(symbols_)


def _raise_out_of_scope_bad_length(symbol, digits: str):
    """位数不对 → `OutOfScope`，消息必须把"为什么会取到别的标的"说清楚。"""
    if len(digits) < 6:
        why = ("⚠️ 本库**不会**左补零。历史上 `%s` 被补成 `%s` 后，"
               "`get_bars(\"%s\")` **静默返回了「模塑科技 %s」的 K 线**"
               "（`close=13.58`，与 `get_bars(\"%s\")` 逐条相同，`get_minutes` 同病）；"
               "`02800→002800`、`00005→000005` 也是同一形态。"
               "补零等于**把另一个标的的数据冒充成你要的标的**，比报错危险得多。"
               % (digits, digits.zfill(6), digits, digits.zfill(6),
                  digits.zfill(6)))
        target = "如果你要的就是 A 股 %s，请**显式写全 6 位**：`%s`。" % (
            digits.zfill(6), digits.zfill(6))
    else:
        why = ("⚠️ 本库**不会**截断。多写的位若被截掉，同样会命中**另一只**标的"
               "（与左补零是同一个坑的两面）。")
        target = "请确认代码是否多写了位。"
    raise errors.OutOfScope(
        "标的 %r 的代码是 %d 位（%s），本数据源的证券代码一律 **6 位数字**。%s"
        "如果 %r 是港股/海外标的：港股**实时报价服务端是有的**"
        "（`PBCombHQ` + `setcode=31`），但**本库尚未实现**；"
        "港股 K 线/分时目前**没有通道**，港股清单也无法枚举。%s"
        % (symbol, len(digits), digits, why, symbol, target))


def _raise_out_of_scope_futures(symbol):
    """期货/期权/国债期货/外汇 → `OutOfScope`（措辞见 `errors.OutOfScope` 的纪律）。"""
    raise errors.OutOfScope(
        "标的 %r 是**期货/期权/国债期货/外汇**形态的代码，这类品种"
        "**不在本数据源（通达信手机版 7615）的证券表内**，所以取不到。"
        "⚠️ 注意归因：这**不是**「服务端不支持这个市场码」—— 已实证 `PBFXT` 的 `field3` "
        "对已知代码是宽松的（`600519` 在 field3=1/99/30 三种下返回**逐位相同**的 K 线），"
        "取不到是因为**代码不在证券表里**。本库目前只覆盖 A 股/北交所/指数/板块/债券。"
        % (symbol,))


def parse(symbol) -> tuple[int, str]:
    """任意写法 → `(market, code)`。

    不带前缀时按**代码段**推断市场（3 位前缀表 → 1 位兜底，见 `_PREFIX3`）。
    推断规则来自服务端 `Setcode` 真值，**不要在别处重复实现**（历史教训：
    老规则把沪 B `900xxx`、沪转债 `11xxxx`、沪逆回购 `204xxx` 全判成深市，
    309 条样本里错 92 条）。

    校验（阶段 3 立，三条都是红线）
    ------------------------------
    * 代码必须是**正好 6 位**数字：位数不对 → `errors.OutOfScope`，
      **绝不补零、绝不截断**（`00700` 补零会静默命中 `000700` 模塑科技，已实证）；
    * 期货/期权/国债期货/外汇形态 → `errors.OutOfScope`（不在证券表内）；
    * 其余无法识别的垃圾输入 → `ValueError`（**别**混进 `OutOfScope`：
      `"abc"` 是调用方写错了，不是品种不在范围内）。

    >>> parse("sh600519")
    (1, '600519')
    >>> parse("600519")
    (1, '600519')
    >>> parse("000001")
    (0, '000001')
    >>> parse("920091")
    (2, '920091')
    >>> parse("900901")      # 沪 B：老规则会错判成深市
    (1, '900901')
    >>> parse("113711")      # 沪转债
    (1, '113711')
    >>> parse("123283")      # 深转债
    (0, '123283')
    >>> parse("524001")      # 5 开头但服务端是深
    (0, '524001')
    """
    if isinstance(symbol, (tuple, list)) and len(symbol) >= 2:
        m, c = symbol[0], symbol[1]
        if isinstance(m, str):
            key = m.strip().lower()
            if key not in _PREFIX:
                raise ValueError(
                    "无法识别的市场写法：%r（二元组的第一位只能是 sh / sz / bj "
                    "或 1 / 0 / 2；收到 %r）" % (symbol, m))
            market = _PREFIX[key]
        else:
            try:
                market = int(m)
            except (TypeError, ValueError):
                raise ValueError(
                    "无法识别的市场写法：%r（二元组的第一位只能是 sh / sz / bj "
                    "或 1 / 0 / 2；收到 %r）" % (symbol, m)) from None
        digits = _digits_of(c)
        # ⚠️ 与下面的字符串写法**共用同一段校验**。历史 bug：这里曾写成
        # `if digits.isdigit() and len(digits) != 6`，`and` 短路把"非数字"
        # 整个放过去了 —— `("sh", "abcdef")` / `("sh", "")` 都被当成合法标的
        # 一路发进 PBFXT 帧，最后以「K线无数据」收场（归因错到"这票没数据"）。
        if not digits:
            raise ValueError(
                "无法识别的标的：%r（代码部分是空的，需要 6 位数字代码）" % (symbol,))
        if not digits.isdigit():
            if is_futures_like(digits):
                _raise_out_of_scope_futures(symbol)
            raise ValueError(
                "无法识别的标的：%r（代码部分 %r 含非数字字符，需要 6 位数字代码）"
                % (symbol, digits))
        if len(digits) != 6:
            _raise_out_of_scope_bad_length(c, digits)
        return market, digits

    s = str(symbol).strip().lower()
    digits = _digits_of(s)
    if not digits:
        raise ValueError("无法识别的标的：%r（需要 6 位数字代码，可带 sh/sz/bj 前缀）"
                         % symbol)
    if not digits.isdigit():
        if is_futures_like(s):
            _raise_out_of_scope_futures(symbol)
        raise ValueError("无法识别的标的：%r（需要 6 位数字代码，可带 sh/sz/bj 前缀）"
                         % symbol)
    if len(digits) != 6:
        _raise_out_of_scope_bad_length(symbol, digits)
    if s[:2] in _PREFIX:
        return _PREFIX[s[:2]], digits
    return market_by_code(digits), digits


def market_of(symbol) -> int:
    return parse(symbol)[0]


def symbol_of(market: int, code: str) -> str:
    return _SUFFIX.get(int(market), "") + clean(code)


def text_of(symbol) -> str:
    """把**任意写法**渲染成人类可读字符串，**永不抛异常**（专供报错文案）。

    `"%s" % symbol` 对二元组会自爆（`("%s …" % ("sh",""))` →
    `TypeError: not all arguments converted during string formatting`），
    而报错文案恰恰是最不能抛异常的地方。用本函数代替：

    >>> text_of("600519"), text_of(("sh", "600519")), text_of(1)
    ('600519', 'sh600519', '1')

    对**字符串输入原样返回**，所以历史报错文案逐字节不变（黄金样本里
    `reject_*` 把异常文案原文冻死了，改这里不会破它们）。
    """
    if isinstance(symbol, (tuple, list)):
        if len(symbol) >= 2:
            m, c = symbol[0], symbol[1]
            try:
                market = int(m) if not isinstance(m, str) else _PREFIX.get(
                    m.strip().lower(), 0)
                return symbol_of(market, c)
            except Exception:                                # noqa: BLE001
                return str(symbol)
        return str(symbol)
    return str(symbol)
