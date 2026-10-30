# -*- coding: utf-8 -*-
r"""通达信行情封装（走手机版 7615 的 protobuf 命令，零第三方依赖）。

为什么不用 PC 7709 / 第三方库
-----------------------------
2026-10-25 复核结论：**7615 通道自己就能出 K 线和分时**，不需要 PC 端 7709：

| 需求 | 7615 命令 | ctl | 实测规模 |
|---|---|---|---|
| K 线（5/15/30/60 分、日、周） | `HQServ.PBFXT` | 0x0E | 12 列 × 120 行（单页上限 416） |
| **复权 K 线**（前/后复权） | `HQServ.PBFXT` + `field8` | 0x0E | 同上；`field8` = 复权开关（见下） |
| 分时（当日 240 点） | `HQServ.PBZST` | 0x0E | 6 列 × 240 行 |
| 市场行情列表（可翻页/按市场筛） | `HQServ.PBMultiHQ` | 0x12 | 至多 PageSize 行 × N 列（**参数已解构**，见下） |
| 五档盘口（按代码） | ❌ 无 | — | 协议返回列里**没有任何 bid/ask 字段** |
| 逐笔成交 | **`HQServ.PBTickEx`** | 0x11 | 7 列 × 每页 100 行（**2026-10-27 打通**，实装在 `hq_extra.ticks()`） |
| **实时涨跌家数**（全市场） | **`HQServ.PBSdstat`** | 0x11 | 5 个家数字段 + 逐分钟序列（**2026-10-28 打通**，`market_breadth()`） |
| **分时点位抽样**（逐分钟数组） | **`HQServ.PBSample`** | 0x11 | 240 点上限 × 本帧 N 点（**2026-10-28 打通**，`intraday_samples()`） |

> PC 7709 通道本轮做过自研尝试（帧构造已逐字节对齐、握手/zlib 解码均正常），
> 但 5 台主站对 K 线/五档/分时/逐笔一律返回空体（`ret=800` 无数据），
> 判定为「需进一步抓包」，**不作为本程序的依赖**。见 `reports/7709自研探测记录.md`。

PBFXT 参数位（2026-10-24 订正 + 2026-10-25 复权补正）
---------------------------------------------------
    field1 = 嵌套 {1:"UTF-8", 4:0, 5:0}     固定头
    field2 = 6 位代码（字符串）
    field3 = 市场  0=深 1=沪 2=北
    field4 = 周期  0=5分 1=15分 2=30分 3=60分 4=日 5=周 7=1分
    field6 = 翻页偏移（条）
    field7 = 本次条数（≤416）
    field8 = **复权开关**  1=不复权 2=后复权 3=前复权 4=另一口径（语义待定）

复权实测（2026-10-23 收盘，日线，见 `reports/P8调研_复权与分页.md` §二）
--------------------------------------------------------------------
    600519  field8=1 → 最新 1335.00（基准）
            field8=2 → 最新 9311.23（×6.9747，**后复权**：以上市首日为锚，最新价被上调）
            field8=3 → 最新 1335.00（×1.0000，**前复权**：以最新价为锚，最新价不变）
                       历史 20250228 那根 1413.76 vs 原始 1421.14（×0.9948）
            field8=4 → 最新 10993.53（×8.5353）  语义待定，本库不映射到 `Adjust`
    000001  field8=2 → ×322.97（1991 年上市，累计送转巨大，量级合理）
            field8=3 → 最新 ×1.0000
    周线同结论 → **与周期无关**

⚠️ 前复权的调整方向**不是恒定的**：`600519` 历史段被下调（×0.9948），
而 `000001` 历史段被上调（9.05 → 9.25，×1.0221）。说明服务端按自己的口径处理
（与配股/增发/送转的方式有关），**不是"原始价减累计分红"**。见同报告 §2.4。

PBMultiHQ 参数位（2026-10-25 解构）
----------------------------------
    field1    = 嵌套 {5:0}                   格式声明
    field2×N  = **要返回哪些列的名字**（`NOW`/`CLOSE`/`NAME`…，**不是代码！**）
    field3    = 空字符串                      未用
    field4    = **市场/品种码**  0=科创 1=沪B 2=创业 3=深B 4,5=债券 6=全部A股 7=B股
    field5    = 14                           分类/周期（未定论）
    field6    = **偏移量（翻页，单位=行；窗口 [offset, offset+page_size)）**
    field7    = **PageSize**（返回**至多** ps 行；>80 封顶 80。
                部分「(列集合, ps)」组合服务端会**稳定回 0 行**，规律未定论
                —— 本库不设硬下限，改为 0 行就翻倍重试，详见 `get_market_page` docstring）
    field8    = 1                            开关位（未定论）
    field9    = 1                            开关位（未定论）
    field12   = 1                            开关位（未定论）
    field13   = 0                            未用

（**没有 field10 / field11** —— 早期按文档推演时多写过两个，已用字节级比对纠正：
本库构造的参数与内置模板 74 字节**完全一致**，有离线用例守着。）

⚠️ `field4` **不是 setcode**（0=深/1=沪那个口径），是品种/板块分类码，别混用。
⚠️ 参数里**没有任何代码字段** → **不能按代码取**；返回列里**没有 bid/ask** → **没有五档**。

PBZST 参数位
------------
    field1 = 嵌套 {1:"UTF8", 5:0}   固定头
    field2 = 6 位代码
    field3 = 市场
    field4/5/6 = 0

PBSdstat 参数位（2026-10-28 解构，ctl 0x11）
------------------------------------------
    field1 = 嵌套 {5:0}                固定头 → `0a022800`
    field2 = **视图码**  4=汇总家数（回 1 条记录）/ 3=逐分钟序列（回 N 行）

    ⚠️ 参数里**没有代码位** —— 这条统计天生是全市场口径，不能按代码取。

PBSample 参数位（2026-10-28 解构，ctl 0x11）
------------------------------------------
    field1 = 嵌套 {5:0}                固定头 → `0a022800`
    field2 = 1                         （开关位，实测恒 1）
    field3 = 6 位代码
    field4 = 市场
    field7 = 1                         （**语义未定**：发 1 却回 139 点，不是行数上限）

    响应位（`PBSample` 与 `PBSampleMore` 的单条布局**同构**）：
    field3=代码 / field4=市场 / field5=**全天点数上限**(240，More 是 60) /
    field6=**昨收**(与 PBCombHQ 的 CLOSE 一致) / field7=**本帧点数**(恒等于 len(field8)/4) /
    field8=打包 **float32** 点位数组

用法
----
    from tdx_hq_pb import get_bars, get_minutes, get_quotes

    rows = get_bars("600519", period="day", count=120)   # [{Data,Open,High,Low,Close,Volume,...}]
    mins = get_minutes("600519")                          # [{Minute,Now,Average,NowVol,...}]

    breadth = market_breadth("summary")      # {"shape":"summary","fields":{1:...,2:...}}
    pts = intraday_samples("999999", market=1)   # [{"code","points":[...]}]
"""
from __future__ import annotations

import os
import struct
import sys

from . import pb as _pb                                        # noqa: E402
from . import pb_api as tdx_pb_api                                       # noqa: E402

# 周期名 → PBFXT field4
PERIOD = {
    "1m": 7, "5m": 0, "15m": 1, "30m": 2, "60m": 3,
    "day": 4, "week": 5, "month": 6, "season": 10,
}

# 服务端实测无数据的周期 —— 单独标出来，别让调用方以为是自己参数写错
PERIOD_EMPTY = {"year": 9}      # 实测 count=10 / 30 均返回 0 行
_PERIOD_CN = {v: k for k, v in PERIOD.items()}

MAX_ROWS = 416          # PBFXT 单页上限（实测）

# PBFXT field8 = 复权开关（2026-10-25 实测，见模块头）。**注意不是 Adjust 枚举值**。
ADJUST_NONE = 1         # 不复权（原始价）
ADJUST_HFQ = 2          # 后复权（以上市首日为锚，最新价被上调）
ADJUST_QFQ = 3          # 前复权（以最新价为锚，最新价不变）
ADJUST_ALT = 4          # 另一口径，语义待定 —— 本库不映射到 Adjust

# PBMultiHQ field4 = 市场/品种码（2026-10-25 实测）
MARKET_CODES = {
    # ══════════════════════════════════════════════════════════════════════
    # `PBMultiHQ` 的 `field4`（市场域）语义 —— 阶段 3 按实网证据重标。
    #
    # 老表把 `0` 标成「科创板」、`2` 标成「创业板」，于是看起来 `market=0` 只覆盖
    # 科创板；**实际它覆盖沪市全部股票（含 60/68）**。一个错标注会让整条结论歪掉，
    # 所以这里逐条给出证据口径；没复验过的**如实标注"沿用旧标注"**。
    # 唯一事实源在 `tdxdata/_core.py:MARKET_DOMAINS`（本表与之保持一致）。
    # ══════════════════════════════════════════════════════════════════════
    0: "沪A（沪市全部股票，含 60xxxx/68xxxx）",   # 修错标。2026-10-27 实网翻页取全：
                                                # 代码段含 60/68 两种，远不止科创板
    1: "沪B（900xxx）",
    2: "深A（深市全部股票，含 00xxxx/30xxxx）",   # 修错标。同日实网：含 00/30 两种
    3: "深B（200xxx/201xxx）",                    # 沿用旧标注，未逐个复验
    4: "债券/回购",                                # 沿用旧标注，未逐个复验
    5: "债券（另一类）",                           # 沿用旧标注，未逐个复验
    6: "全部A股（沪A ∪ 深A ∪ 北交所）",            # 2026-10-27 实网能力断言：
                                                # set(6) == set(0) | set(2) | set(12)
                                                # —— 注意**不是** 0|1|2（6 里没有 B 股）
    7: "B股",                                      # 沿用旧标注，未逐个复验
    8: "科创板（68 段；与 0 **不等**）",           # 修错标。8 是 0 的真子集：只含 68xxxx，
                                                # 而 0 同时含 60xxxx → "同0"是错的
    9: "ETF/基金",                                # 补登记。2026-10-27 实网有数据
    10: "全品种榜",                               # 补登记。同日实网有数据
    11: "指数",                                   # 补登记。同日实网有数据
    12: "北交所（920xxx/430xxx/83xxxx/87xxxx）",   # 补登记。同日实网：354 只全在 920 段
    13: "配债",                                   # 补登记。同日实网有数据
    14: "创业板（30xxxx）",                       # 补登记。同日实网有数据
    # 2026-10-26 手机抓包实测：六类板块清单都是 field4=10xxx 的「板块域」，
    # 行数与手机端逐个点数完全一致（30 / 128 / 345 / 269 / 158 / 32）。
    # 早先按 14 去试拿到的是创业板 —— 14 是股票口径，不是板块。
    10001: "板块-行业二级",
    10002: "板块-行业三级",
    10004: "板块-概念",
    10005: "板块-风格",
    10006: "板块-地区",
    10008: "板块-行业一级",
}
#: **2026-10-26 抓包原样的 3 列** —— 保留它**只为逐字节对照**（抓包形态不许漂）。
#: 不要拿它当默认值用：默认已改成 `MULTIHQ_DEFAULT_COLS`（5 列，含量额）。
MULTIHQ_COLS_CAPTURE = ("NOW", "CLOSE", "NAME")

#: `PBMultiHQ` 的**默认列**（名字叫 DEFAULT 就等于"默认实际拿到的"）。
#:
#: 2026-10-27 由 3 列改成 5 列（**行为变更：默认请求体变大**）：
#: 原来只要 `NOW/CLOSE/NAME`，服务端会自动补 `Code/Setcode`（还有 `XSFLAG`/`MISCFLAG`），
#: 于是 `Quote.vol`/`Quote.amount` 永远拿不到 —— 而"成交额成交量"恰恰是用户最常问的。
#: 两个字段每行约多 24 字节（80 行/页 ≈ 1.9 KB），而这一页本来就要 ~2.2 秒：
#: **字节是噪声，延迟才是成本**，所以默认就给全。
#: 显式传 `cols=` 会**完全覆盖**这份默认值。
MULTIHQ_DEFAULT_COLS = MULTIHQ_COLS_CAPTURE + ("AMOUNT", "VOL")

# PBMultiHQ 的 page_size 边界（2026-10-25 实测 + 两轮复核修正）
#
# ✅ 已定论：`offset ≥ 0` 时 **返回行数 = page_size**（"page_size − 1" 是 `offset = −1`
#    那条老模板路径的产物，不是 PageSize 的规律）；`page_size > 80` 封顶 80 行。
#
# ⚠️ **未定论：部分「(列集合, page_size)」组合服务端会稳定回 0 行，规律不是数量关系。**
#    第一轮我方测出「ps ≤ 20 全空」就写成固定下限 21 —— 错；第二轮复核 + 我方复测确认：
#      · 1 列 `[NOW]`：ps=22 → 0 行，ps=26 → 26 行          → 阈值在 (22, 26]
#      · 3 列 默认：  ps=20 → 0 行，ps=21 → 21 行            → 阈值在 (20, 21]
#      · 4 列 含 VAR0404：ps=18 → **0 行**（要放大才行）
#      · 4 列 含 BELONGHY：ps=18 → **18 行**（同 ps 直接出）
#    最后两行是**关键反例**：同样 4 列、同样 ps=18，只换列名结果就不同 →
#    ① **不是「列数 × ps」的单调函数**（4×18=72 cells 回 0，而 1×26=26 cells 正常）；
#    ② **跟具体列名有关**（`VAR0404`/`FLAG` 这类列似乎更"贵"）；
#    ③ **是确定性行为**：同参数重复请求结果一致（复核重复 3 次 = `[0,0,0]`），不是偶发空包，
#       所以**不能靠"再试一次"解决**，只能放大 ps 绕过。
#    结论：**不猜公式、不设硬下限**，0 行就把 page_size 翻倍重试，把"到底要多大"
#    交给服务端回答。这样 1 列 + ps=21 不会再被误放行成"提前退出"。
MAX_PAGE_ROWS = 80
MIN_PAGE_ROWS = 2            # 纯 sanity 下限：< 2 行的请求没有意义
_PAGE_GROW_ATTEMPTS = 5      # 0 行时翻倍重试的次数上限（2→4→8→16→32…，受 MAX_PAGE_ROWS 约束）


# ── protobuf 构造小工具（只用到 varint 和 length-delimited 两种）─────────────
def _varint(v):
    """varint 编码。负数按 int64 二补码编码（protobuf 标准做法）。

    ⚠️ 必须处理负数：`-1 >> 7` 在 Python 里永远是 `-1`，
    直接 `while` 会**死循环**。服务端下发的 `field6` 默认值就是 `-1`，
    所以这个坑是真会被踩到的。
    """
    v = int(v)
    if v < 0:
        v &= 0xFFFFFFFFFFFFFFFF          # int64 二补码
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field, wire):
    return _varint((field << 3) | wire)


def _vfield(field, value):
    """varint 字段。"""
    return _tag(field, 0) + _varint(int(value))


def _bfield(field, payload):
    """length-delimited 字段。"""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return _tag(field, 2) + _varint(len(payload)) + payload


def _market_of(code):
    """6 位代码 → 市场码（0=深 1=沪 2=北）。

    ⚠️ **不要再在这里写映射表！** 这个函数原来自带一份老规则（`5/6→沪`、`83/87/88/92→北`），
    和 `symbols` 里那份各维护各的 —— 结果是 `Client.get_bars("900901")` 的
    `symbols.parse` 已经算出 market=1，`_args_pbfxt` 却又把它改回 0，报文里带错市场码，
    服务端回空，用户看到的是「K线无数据」（2026-10-26 实测）。
    现在统一委托给 `symbols.market_by_code`，与服务端 `Setcode` 真值一致（309/309）。
    """
    from ..symbols import market_by_code
    return market_by_code(str(code)[:6])


# ── 参数构造 ────────────────────────────────────────────────────────────────
def _args_pbfxt(code, period, count, offset, adjust=ADJUST_NONE, market=None):
    # field1 = 嵌套 { 1:"UTF-8"(len), 4:""(len 空), 5:0(varint) }
    inner = _bfield(1, "UTF-8") + _bfield(4, b"") + _vfield(5, 0)
    out = _bfield(1, inner)
    out += _bfield(2, str(code)[:6])
    # field3 = 市场码。显式给了就用（`999999` 这类指数无法靠前缀推断），否则按代码推断
    out += _vfield(3, _market_of(code) if market is None else int(market))
    out += _vfield(4, period)
    out += _vfield(5, 0)
    out += _vfield(6, offset)
    out += _vfield(7, count)
    out += _vfield(8, adjust)     # 复权开关：1=不复权 2=后复权 3=前复权（2026-10-25 实测）
    out += _vfield(9, 0)
    out += _vfield(11, 0)
    out += _vfield(12, 0)
    return out


def _args_multihq(cols, market, offset, page_size):
    """`PBMultiHQ` 参数（**自己构造，不用固定 base64**）。

    字段顺序沿用实测抓到的原包顺序：1 / 2×N / 3 / 4 / 5 / 6 / 7 / 8 / 9 / 12 / 13
    （**没有 10、11**）。字节级与内置模板 74 字节一致这件事有离线用例守着
    （`tests/test_p8_adjust_page.py`）。
    """
    out = _bfield(1, _vfield(5, 0))                    # {5:0} 格式声明
    for name in cols:                                  # field2×N = 要哪几列
        out += _bfield(2, str(name))
    out += _bfield(3, b"")                             # 空字符串，未用
    out += _vfield(4, market)                          # 市场/品种码（**不是 setcode**）
    out += _vfield(5, 14)                              # 分类/周期（未定论，沿用实测值）
    out += _vfield(6, offset)                          # 偏移（翻页）
    out += _vfield(7, page_size)                       # PageSize（返回**至多** ps 行）
    out += _vfield(8, 1)
    out += _vfield(9, 1)
    out += _vfield(12, 1)
    out += _vfield(13, 0)
    return out


def _args_pbzst(code, market=None):
    # field1 = 嵌套 { 1:"UTF8"(len), 5:0 }
    out = _bfield(1, _bfield(1, "UTF8") + _vfield(5, 0))
    out += _bfield(2, str(code)[:6])
    out += _vfield(3, _market_of(code) if market is None else int(market))
    out += _vfield(4, 0)
    out += _vfield(5, 0)
    out += _vfield(6, 0)
    return out


# ── 对外接口 ────────────────────────────────────────────────────────────────
def get_bars(code, period="day", count=120, offset=0, adjust=ADJUST_NONE, market=None):
    """取 K 线，返回 `(列名, 行)`；失败返回 `(None, [])`。

    `period`: `1m/5m/15m/30m/60m/day/week/month/season`（`year` 服务端无数据）。
    `count` 上限 416/页；要更多用 `offset` 翻页（**实测 offset 有效**，返回更早的数据）。
    `adjust`: `PBFXT` 的 `field8` —— `1`=不复权 / `2`=后复权 / `3`=前复权 / `4`=待定口径。
    **服务端算好给，不是本地算**（2026-10-25 实测，日线周线同结论）。
    `market`: `field3` 市场码。`None` 时按代码前缀推断；**指数等推断不出来的品种必须显式给**
    （`999999` 上证指数 = market `1`，2026-10-26 手机端确认）。

    ⚠️ **服务端限制：`count` 小于 10 会直接回空**（实测 1/3/5 全空，10 起正常）。
    本函数因此总是至少按 10 条请求，再在本地截到 `count`。

    支持品种（2026-10-26 实网**逐只**实测，见 README「支持品种」表）：

    | 品种 | 代码段 | K 线 |
    |---|---|---|
    | 沪深 A 股 | 600/601/603/605/688/000/001/002/003/300/301 | ✅ |
    | 北交所 | 920/430/830/870 | ✅（920207 实测正常） |
    | 深 B / 沪 B | 200/201/900 | ✅（沪 B 需 market=1，市场码已按服务端 `Setcode` 修正） |
    | 深转债 | 123/127/128 | ✅（123283 实测 184.10 元） |
    | 沪转债 | 110/111/113/118 | **✅**（113054=121.375 / 110077=196.999 / 111012=533.557 / 118013=170.390） |
    | 基金/ETF | 510/159/524 | 未系统验证 |

    ⚠️ **别再按"语法上不存在"或"某只死票"去判无数据**（D6 教训，2026-10-27）：
    偶发"某只代码 0 行"要先怀疑**帧形态**（见 `datacenter.unwrap_frames`），
    不是业务限制。判定"无数据"之前至少换一组**更多列**重取一次 ——
    列多 payload 大 → 走 zlib 帧，与明文帧是**两条不同的解析路径**，能互相验证。
    实例：`113711` 曾被我们两次判成"无数据/已摘牌"，实际一直有 2 根真实成交，
    只是响应是明文 protobuf 帧，老的 `unwrap()` 认不出。
    """
    p = PERIOD.get(period, period)
    if not isinstance(p, int):
        raise ValueError("period 不支持：%r（可选 %s）" % (period, "/".join(PERIOD)))
    want = max(1, min(int(count), MAX_ROWS))
    fetch = max(want, 10)                     # 见上：<10 服务端回空
    args = _args_pbfxt(code, p, fetch, offset, adjust, market=market)
    res = tdx_pb_api.call_pb("HQServ.PBFXT", ctl=0x0E, args=args)
    if not res:
        return None, []
    cols, rows = res
    # 服务端按时间升序返回；取「最近 want 条」
    return cols, rows[-want:] if want < len(rows) else rows


def get_market_page(market=6, offset=0, page_size=50, cols=None):
    """市场行情**一页**（`PBMultiHQ`），返回 `(列名, 行)`；失败返回 `(None, [])`。

    2026-10-25 结论：`PBMultiHQ` = **「按涨幅排序、可按市场码过滤、可翻页的市场行情列表」**。
    - `market`：`field4`，`0` 科创 / `1` 沪B / `2` 创业 / `3` 深B / `4,5` 债券 / **`6` 全部 A 股** / `7` B 股
    - `offset`：`field6`，**单位=行，窗口 `[offset, offset+page_size)`**。
      实测 `offset=0` 与 `offset=50` 的代码**零重叠**。
    - `page_size`：`field7`，返回**至多** `ps` 行；`ps > 80` 封顶 80。
      ✅ **「0 行规律」已破案（D6，2026-10-27）**：不是服务端规则，是**客户端解析缺陷**。
      服务端按 payload 大小决定压不压缩 body：payload 大的帧是 zlib（首字节 `0x3B`），
      小的帧是**明文 protobuf**（`0x0B`）—— 而老的 `unwrap()` 只认 JSON 头和 zlib 魔数，
      明文帧一律切不出来 → 被当成传输失败 → 0 行（还要白等 8s × 4 轮）。
      补上 `unwrap_frames()` 兜底后 8/8 组合满页、不再翻倍；`VAR0404` 与 `BELONGHY`
      的差异也只是 payload 大小跨过了压缩门槛，跟字段语义无关。
      **翻倍重试仍保留，但已降级为真·0 行的兜底**（如 `offset` 超出范围）。
      2026-10-25 记的那批"确定性 0 行"组合（1 列 ps=22 / 3 列 ps=20 / 6 列 ps=10 /
      4 列 `VAR0404` ps=18）现在实测都**直接满页**。
    - `cols`：`field2`，要返回哪些**列名**（不是代码！）；`Code`/`Setcode` 由服务端自动补。
      **`cols=None` 时用 `MULTIHQ_DEFAULT_COLS` = `NOW/CLOSE/NAME/AMOUNT/VOL`（5 列，含成交量额）**；
      显式传 `cols=` 会**完全覆盖**这份默认（传 `MULTIHQ_COLS_CAPTURE` 可回到 2026-10-26 抓包的 3 列形态）。
      实测（2026-10-27，market=6 ps=50）：3 列请求体 **40 B** / 5 列 **53 B**，
      两者**都是 1 次请求、1.75 s、满页 50 行** —— 多要两列不会触发「0 行翻倍重试」。

    ⚠️ 参数里**没有代码字段**，所以**不能按代码取**（`field2` 是列名）。
    ⚠️ 返回列里**没有 bid/ask** → 协议层没有五档。

    限流：PB 路径唯一的咽喉是 `datacenter._send`，**闸门已经在那一层统一过**
    （2026-10-26 D2 起；本函数不再自己 `pacer.wait()` —— 否则会双重等待变 4.4 秒）。
    所以翻倍重试的每一次尝试都会各自过一次闸门。
    """
    m = int(market)
    if m not in MARKET_CODES:
        raise ValueError("market 不认识：%r（可用 %s）"
                         % (market, " / ".join("%d=%s" % kv for kv in sorted(MARKET_CODES.items()))))
    off = int(offset)
    if off < 0:
        raise ValueError("offset 必须 ≥ 0（单位=行；收到 %r）" % (offset,))
    ps = int(page_size)
    if ps < MIN_PAGE_ROWS:
        raise ValueError("page_size 必须 ≥ %d（收到 %r）" % (MIN_PAGE_ROWS, page_size))
    if ps > MAX_PAGE_ROWS:
        ps = MAX_PAGE_ROWS          # 实测 >80 也只给 80 行，本地截住以免调用方误判
    use_cols = list(MULTIHQ_DEFAULT_COLS if cols is None else cols)
    if not use_cols:
        raise ValueError("cols 不能为空（至少给一列，例如 ['NOW','CLOSE','NAME']）")

    for _ in range(_PAGE_GROW_ATTEMPTS):
        # 闸门在 dc._send 里统一过（每次尝试各过一次），此处不再重复 wait
        args = _args_multihq(use_cols, m, off, ps)
        res = tdx_pb_api.call_pb("HQServ.PBMultiHQ", ctl=0x12, args=args)
        if res and res[1]:
            return res
        if ps >= MAX_PAGE_ROWS:
            break                          # 已经顶到 80，再放大也没用
        # 0 行的主因（明文帧解析）已经修掉，这里只兜真·0 行（如 offset 越界）：
        # 还空就翻倍再试一次，宁可多发一次请求也别把"取不到"说成"没有数据"
        ps = min(ps * 2, MAX_PAGE_ROWS)
    return None, []


def get_minutes(code, market=None):
    """取当日分时（240 点），返回 `(列名, 行)`；失败返回 `(None, [])`。

    `market`: 同 `get_bars`，`None` 时按代码前缀推断。
    """
    args = _args_pbzst(code, market=market)
    res = tdx_pb_api.call_pb("HQServ.PBZST", ctl=0x0E, args=args)
    if not res:
        return None, []
    return res


def get_quotes(codes=None):
    """**按代码取五档：走不通，调用方请改用 `get_market_page()`。**

    2026-10-25 解构 `PBMultiHQ` 参数后的确切结论（两条各自独立成立）：
    1. **协议层没有五档字段** —— 返回列只有 `Code/Setcode/Name/CLOSE/NOW/BELONGHY/
       FLAG/XSFLAG/MISCFLAG/VAR0404`，**没有任何 bid/ask**。不是参数没调对。
    2. **参数里没有代码位** —— `field2` 是"要返回哪几列"的**列名**，不是代码。

    所以本函数**不实现**（上游 `Client.get_quotes` 抛 `NotSupportedError`）。
    要市场行情请用 `get_market_page(market=6, offset=..., page_size=...)`。
    """
    res = tdx_pb_api.call_pb("HQServ.PBMultiHQ", ctl=0x12)
    if not res:
        return None, []
    return res


def get_ticks(code, market, *, anchor="0", count=100):
    """逐笔成交（`HQServ.PBTickEx`，ctl=0x11）—— **2026-10-27 已打通**。

    实现在 `hq_extra.ticks()`（本模块只管 K 线/分时/代码表，逐笔的表格解析在那边）。
    这里留同名入口，方便按「命令名 → 实现」找代码。

    ⚠️ 早前这里抛 `NotImplementedError` 并写「7615 无等价命令」，根因是**命令名记错**：
    真名是 `PBTickEx`（`PBEXTick` 是 App 命令目录里另一个命令），见 `07` §16。
    """
    from . import hq_extra

    return hq_extra.ticks(code, market, anchor=anchor, count=count)


# ══════════════════════════════════════════════════════════════════════════
# 市场涨跌家数（`PBSdstat`）/ 分时点位抽样（`PBSample`）
#
# 2026-10-28 由抓包逆向后实装（证据：`reports/eval3/findings/M6-pb-undecoded.md`）。
# 两条命令**都不是**「列名 + 字符串行」的表结构，`pb.parse_table()` 认不出来，
# 所以必须走本节的专用解码器。
# ══════════════════════════════════════════════════════════════════════════

#: `PBSdstat` 的 ctl（抓包实测：ctl 0x11，msgid 170/166/55/56）。
SDSTAT_CTL = 0x11
#: `PBSample` 的 ctl（抓包实测：ctl 0x11，msgid 168）。
SAMPLE_CTL = 0x11

#: `PBSdstat` 的 `field2` = **视图码**（不是行数！行数在响应的 `field2` 里）。
#:
#: * `summary`(4) → 回**一条**记录（`field7`），含涨/跌/平/涨停/跌停家数；
#: * `minute`(3)  → 回**N 行**（重复 `field6`），N = 当日已走过的分钟数。
#:
#: 证据：`f2=4` 的 131 帧全是 421B 单记录；`f2=3` 的 125 帧全是 N 行
#: （121@11:30 / 139@13:18 / 242@15:00），见 M6 报告 §1。
SDSTAT_VIEWS = {"summary": 4, "minute": 3}

_WIRE_VARINT, _WIRE_64, _WIRE_LEN, _WIRE_32 = 0, 1, 2, 5


def _read_fields(buf: bytes) -> list:
    """**严格解一层** protobuf → `[(field, wire, value, off, end)]`。

    为什么不用 `pb.decode_message()`
    --------------------------------
    那个函数会把「看起来不像文本的 length 段」**递归**当子消息解。`PBSdstat`
    形状 B 每行有个 40 字节的**打包 uint32 数组**（10 个 `uint32`），递归解会
    把它解成一串假字段（尤其零填充段），于是"解出来了"其实是幻觉。
    本函数只解一层、wire type 原样保留，解释权交给调用方 —— 这是"不猜"的地基。

    `wire==2` 时 `value` 是 `bytes`；`wire==0` 是 `int`；`wire==1/5` 是原始字节。
    """
    out = []
    pos, n = 0, len(buf)
    while pos < n:
        try:
            key, pos = _pb.read_varint(buf, pos)
        except ValueError:
            break
        field, wire = key >> 3, key & 7
        if field == 0:                       # 0 号字段非法 → 到此为止（别硬啃零填充）
            break
        start = pos
        try:
            if wire == _WIRE_VARINT:
                val, pos = _pb.read_varint(buf, pos)
                out.append((field, wire, val, start, pos))
            elif wire == _WIRE_64:
                if pos + 8 > n:
                    break
                out.append((field, wire, buf[pos:pos + 8], start, pos + 8))
                pos += 8
            elif wire == _WIRE_LEN:
                ln, p2 = _pb.read_varint(buf, pos)
                if p2 + ln > n:
                    break
                out.append((field, wire, buf[p2:p2 + ln], start, p2 + ln))
                pos = p2 + ln
            elif wire == _WIRE_32:
                if pos + 4 > n:
                    break
                out.append((field, wire, buf[pos:pos + 4], start, pos + 4))
                pos += 4
            else:                            # group 等：本协议不用，直接停
                break
        except ValueError:
            break
    return out


def _vint(fields, no):
    for f, w, v, _o, _e in fields:
        if f == no and w == _WIRE_VARINT:
            return int(v)
    return None


def _blob(fields, no):
    for f, w, v, _o, _e in fields:
        if f == no and w == _WIRE_LEN:
            return v
    return None


def _text(fields, no):
    raw = _blob(fields, no)
    return None if raw is None else raw.decode("utf-8", "replace")


def _f32(fields, no):
    for f, w, v, _o, _e in fields:
        if f == no and w == _WIRE_32:
            # float32 转 double 会带出 551.4000244140625 这种噪声，统一 round 到 3 位
            # （TDX 报价就是 3 位小数）
            return round(struct.unpack("<f", v)[0], 3)
    return None


def _u32_list(raw: bytes) -> list:
    """打包的 `uint32` 数组 → list[int]（**定长 4 字节**，不是 float32）。"""
    return [struct.unpack("<I", raw[i:i + 4])[0] for i in range(0, len(raw) - 3, 4)]


# ── PBSdstat ─────────────────────────────────────────────────────────────

#: 形状 A 记录里前 5 个 varint 的**协议字段号** → 公开列名。
#: 定标依据（三命令同刻互证，M6 报告 §1.2）：`f1+f2+f3 ≡ 5572`（27 帧无一例外）
#: 且 `PBHQInfo.f8/f5/f7`、`PBZST(880005)` 的 `Now/Average` 在同刻给出同一组数。
SDSTAT_COLS = {
    1: "up_count",
    2: "down_count",
    3: "flat_count",
    4: "limit_up_count",
    5: "limit_down_count",
}


def decode_market_breadth(body: bytes) -> dict:
    """`PBSdstat` 回包 → 协议级 dict（**列名映射在 client 层做**）。

    返回::

        {"shape": "summary", "declared_count": 1,
         "fields": {1: 1637, 2: 3775, 3: 160, 4: 35, 5: 15}, "off": 5}

        {"shape": "minute", "declared_count": 139,
         "rows": [{"f2": 10157.5801, "u32": [10 个 int]}, ...]}

    ⚠️ `summary` 的 `fields` 里**只有服务端真的发出来的字段**（缺的键不补 0）：
    protobuf 里"没发"和"发了 0"在线上等价，本库无法区分，所以缺就是不写 ——
    下游拿到 `None` 才知道是"不知道"，而不是"零家上涨"。
    """
    top = _read_fields(body)
    declared = _vint(top, 2)

    rec = _blob(top, 7)
    if rec is not None:                                     # 形状 A：单记录
        sub = _read_fields(rec)
        fields = {}
        for f, w, v, _o, _e in sub:
            if w == _WIRE_VARINT and f in SDSTAT_COLS:
                fields[f] = int(v)
        off = next((o for f, w, _v, o, _e in top if f == 7), None)
        return {"shape": "summary", "declared_count": declared,
                "fields": fields, "off": off}

    rows = []                                               # 形状 B：N 行
    for f, w, val, off, _e in top:
        if f != 6 or w != _WIRE_LEN:
            continue
        sub = _read_fields(val)
        arr = _blob(sub, 1)
        rows.append({
            "f2": _f32(sub, 2),
            "u32": _u32_list(arr) if arr is not None else None,
            "off": off,
        })
    return {"shape": "minute", "declared_count": declared, "rows": rows}


def _args_pbsdstat(view_code: int) -> bytes:
    """`PBSdstat` 参数：`field1{5:0}` + `field2=视图码`。

    实测字节（view=4）：`0a0228001004`。
    """
    return _bfield(1, _vfield(5, 0)) + _vfield(2, int(view_code))


def market_breadth(view: str = "summary", *, tries: int = 4):
    """取 `PBSdstat` 原始回包并解码；网络失败返回 `None`（不抛，由 client 决定）。"""
    code = SDSTAT_VIEWS.get(str(view).lower())
    if code is None:
        raise ValueError("view 只支持 %s（收到 %r）"
                         % ("/".join(sorted(SDSTAT_VIEWS)), view))
    body = _call_peeled("HQServ.PBSdstat", SDSTAT_CTL, _args_pbsdstat(code),
                        tries=tries)
    if body is None:
        return None
    return decode_market_breadth(body)


# ── PBSample ─────────────────────────────────────────────────────────────

def _args_pbsample(code: str, market, count) -> bytes:
    """`PBSample` 参数：`field1{5:0}` + `field2=1` + `field3=代码` + `field4=市场`
    + `field7=点数`。

    实测字节（999999 / market=1 / count=1）：
    `0a022800 1001 1a06393939393939 2001 3801`。

    ⚠️ `field7` 的语义**未定**：抓包里发 `1`，服务端却回了 139 点。
    所以它**不是**"要几点"的上限 —— `count` 只是原样透传（默认发 1，与抓包一致），
    要限制行数请在本地截尾（`client.get_intraday_samples` 就是这么做的，且写在
    docstring 里，不假装是服务端行为）。
    """
    args = _bfield(1, _vfield(5, 0)) + _vfield(2, 1) + _bfield(3, code.encode("utf-8"))
    if market is not None:
        args += _vfield(4, int(market))
    args += _vfield(7, 1 if count is None else int(count))
    return args


def _decode_one_sample(fields, off=0) -> dict:
    """一条 `PBSample`/`PBSampleMore` 记录（两者**同构**，只是外层包法与点位上限不同）。"""
    arr = _blob(fields, 8)
    pts = [round(struct.unpack("<f", arr[i:i + 4])[0], 3)
           for i in range(0, len(arr) - 3, 4)] if arr is not None else None
    return {
        "code": _text(fields, 3),
        "market": _vint(fields, 4),
        # field5 = **全天点数上限**（实测 240；`PBSampleMore` 是 60）。
        # ⚠️ 它不是"本帧点数"，也不是"字段数" —— 别按 `parse_samples` 的旧注释理解。
        "total_points": _vint(fields, 5),
        # field6 = 昨收（与 `PBCombHQ` 的 CLOSE 一致，已锚定：3658.0256 vs 3658.03）
        "pre_close": _f32(fields, 6),
        # field7 = 本帧点数，实测**恒等于** len(points)（25/25 帧）→ 逐点，不是抽样
        "n_points": _vint(fields, 7),
        "points": pts,
        "off": off,
    }


def decode_intraday_samples(body: bytes) -> list:
    """`PBSample`（单条，字段在顶层）/ `PBSampleMore`（重复 `field1`）都能解。

    两种形状的**单条布局完全一样**（3=代码 / 4=市场 / 5=全天点数 / 6=昨收 /
    7=本帧点数 / 8=打包 float32），所以一个函数吃完，不按命令名分叉。
    """
    top = _read_fields(body)
    if _text(top, 3):                                       # 单条：代码在顶层
        return [_decode_one_sample(top)]
    out = []
    for f, w, val, off, _e in top:                          # 批量：重复 field1
        if f == 1 and w == _WIRE_LEN:
            sub = _read_fields(val)
            if _text(sub, 3):
                out.append(_decode_one_sample(sub, off))
    return out


def intraday_samples(code: str, market=None, count=None, *, tries: int = 4):
    """取一只标的的分时点位；网络失败返回 `None`。"""
    body = _call_peeled("HQServ.PBSample", SAMPLE_CTL,
                        _args_pbsample(code, market, count), tries=tries)
    if body is None:
        return None
    return decode_intraday_samples(body)


def _call_peeled(cmd: str, ctl: int, args: bytes, *, tries: int = 4):
    """发一条 PB 命令并**剥壳**（`{"Length"}` / zlib / base64 可套娃）。

    复用 `pb_api._raw_send`（它已经有重试/超时/限流闸门），本函数只多做一步 `peel`。
    `hq_extra` 是**函数内导入**：`hq_extra` 模块级会 import 本模块，顶层导入会成环。
    """
    from . import hq_extra as _x

    blob = tdx_pb_api._raw_send(ctl, cmd, args, tries=tries)
    if not blob:
        return None
    return _x.peel(blob)


if __name__ == "__main__":                                   # 自检
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    for per in ("day", "week", "60m"):
        cols, rows = get_bars(code, per, 3)
        print("K线 %-4s → %s 行%s" % (per, len(rows), "" if not rows else
                                    "  末条 %s O=%s C=%s" % (
                                        rows[-1].get("Data"), rows[-1].get("Open"),
                                        rows[-1].get("Close"))))
    cols, rows = get_minutes(code)
    print("分时 → %d 行" % len(rows), rows[0] if rows else "")
    cols, rows = get_quotes()
    print("批量快照 → %d 行" % len(rows))
