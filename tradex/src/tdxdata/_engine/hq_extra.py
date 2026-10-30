# -*- coding: utf-8 -*-
"""交接包 5 条 `HQServ` 命令的封装（2026-10-27）。

来源
----
`docs/VERIFICATION.md`：参数结构、ctl、响应形状都经过实机回放验证。
本模块只做三件事：**拼请求 protobuf / 剥响应外壳 / 把响应摊成表**。

| 命令 | ctl | 本模块入口 | 上游 API |
|---|---|---|---|
| `HQServ:PBCombHQ` | `0x11` | `get_quotes` | `Client.get_quotes`（补掉 NotSupportedError） |
| `HQServ:PBHQInfo` | `0x11` | `name_of` | `Client.get_name` |
| `HQServ:PBXmlBlock` | `0x13`（响应 ctl 是 `0x11`） | `block_data` | `Client.get_block_data` |
| `HQServ:PBSampleMore` | `0x15` | `samples` | `Client.get_samples` |
| `HQServ:PBHYStat` | `0x11` | `board_stat` | `Client.get_board_stat` |

三条公共约定
------------
1. **请求体用 `Base64:<b64>` 承载**（抓包原样）。不带前缀服务端也收，但响应会变裸 protobuf；
   `peel()` 两种都吃，所以换承载方式不会坏。
2. **响应会套娃**，统一用 `peel()` 循环剥：
   `{"Length"/"AttachLog": …}` JSON 头 → zlib → base64 →（可能再来一轮）。
3. **拿不准含义的列只进 `raw`**（本仓库红线），不许硬造标准名。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

from .. import errors
from .. import symbols as _sym
from . import datacenter as dc
from . import pb_api as tdx_pb_api
from .hq_pb import _bfield, _vfield      # 同一套手工 protobuf，别再造第三份

__all__ = [
    "COMBHQ_COLS", "COMBHQ_STD", "CTL", "CMD",
    "_args_combhq", "_args_hqinfo", "_args_xmlblock", "_args_samplemore", "_args_hystat",
    "peel", "fields_raw", "strings_of",
    "parse_rowtable", "parse_name", "parse_block",
    "parse_samples", "parse_samples_more", "parse_sample_single",
    "get_quotes", "name_of", "block_data", "block_rows", "samples", "board_stat",
    "ticks", "parse_ticks", "TICKEX_COLS", "_args_tickex",
]

# ── ctl / 命令名 ──────────────────────────────────────────────────────────
#: 请求 ctl（抓包帧头 `hdr[5]`）。⚠️ `PBXmlBlock` 的**响应** ctl 是 0x11，与请求不同。
CTL = {
    "combhq": 0x11,
    "hqinfo": 0x11,
    "xmlblock": 0x13,
    "samplemore": 0x15,
    "hystat": 0x11,
    "tickex": 0x11,
}

#: 命令名。服务名与方法名之间 `:` 和 `.` 服务端都收（实测），这里统一用抓包里的 `:`。
CMD = {
    "combhq": "HQServ:PBCombHQ",
    "hqinfo": "HQServ:PBHQInfo",
    "xmlblock": "HQServ:PBXmlBlock",
    "samplemore": "HQServ:PBSampleMore",
    "hystat": "HQServ:PBHYStat",
    "tickex": "HQServ:PBTickEx",
}

#: `PBCombHQ` 请求要的 17 个列名（**抓包原样，顺序有意义**）。
#: 服务端会自己补 `Code`/`Setcode`/`Name`，所以响应回来是 22 列。
#: ⚠️ **一个字都别改** —— 192 B 请求 hex 的逐字节断言（`test_hq_extra.py`）钉的就是它。
COMBHQ_COLS = ("XSFLAG", "FLAG", "SPECTYPE", "HqDate", "HqTime", "HASTIP", "VAR0514",
               "RELAG", "DELAYMIN", "CLOSE", "NOW", "ZSSJL", "OPEN", "MGJZC",
               "WTB", "MAX", "MIN")

#: 实际取报价时多要的三列（**只给 `get_quotes` 用**，不动上面抓包原样的 17 列）。
#:
#: 为什么多要：`field2` 是**我们自己传的列名**，服务端照着回列。抓包里 App 只要了 17 列、
#: 里面没有成交量/成交额，所以 `Quote.vol`/`amount` 长期是**假 0**（不是协议不给，是我们没要）。
#: 2026-10-27 实网逐列试出来的结果：
#:
#: ========== ============================================================
#: `AMOUNT`   ✅ 认，响应多出 `AMOUNT` 列
#: `VOL`      ✅ 认，列序 `NOW / VOL / LB / AMOUNT`
#: `LB`       ✅ 认（**语义未定**，疑似量比 → 只留在 `raw`，不映射字段）
#: `VOLUME`   ❌ **不认**（列数不变）—— 所以它绝不会出现在这里
#: ========== ============================================================
COMBHQ_COLS_FULL = COMBHQ_COLS + ("VOL", "AMOUNT", "LB")

#: 组合行情的标准列映射：响应列名 → `Quote` 字段。
#: ⚠️ 只映射**含义确定**的；`MGJZC/ZSSJL/WTB/HASTIP/VAR0514/...` 一律留在 raw。
COMBHQ_STD = {
    "Code": "code",
    "Setcode": "market",
    "Name": "name",
    "NOW": "price",
    "CLOSE": "pre_close",      # 本通道 CLOSE 是**昨收**（实网 880903 CLOSE=551.40 与 get_boards 一致）
    "OPEN": "open",
    "MAX": "high",
    "MIN": "low",
    "VOL": "volume",
    "AMOUNT": "amount",
    "HqDate": "date",
    "HqTime": "time",
}


# ══════════════════════════════════════════════════════════════════════════
# 一、请求：手工构造 protobuf（字段含义见 docs/VERIFICATION.md）
# ══════════════════════════════════════════════════════════════════════════
def _args_combhq(items, cols=COMBHQ_COLS):
    """`PBCombHQ` 请求体。`items` = `[(market, code), ...]`。

    字段（01 文档 §2，逐字节验证过）：

    ====== ========= ==========================================
    field1 bytes     固定头，嵌套 `{field1:"1", field2:0, field3:"", field4:"", field5:0}`
                     = `0a013110001a0022002800`（11 B，两条抓包完全一致）
    field2 bytes×N   **要哪些列的名字**
    field3 bytes     **每个代码的市场号**，packed varint，与 field4 顺序一一对应
    field4 bytes×N   **代码**（6 字符）
    field6 varint    `0`（抓包尾部 `30 00`）
    ====== ========= ==========================================

    `field3` 是市场号有铁证：`000680` 市场 1 → 科创综指、市场 0 → 山推股份，
    同一代码两个市场是完全不同的标的。
    """
    out = _bfield(1, _bfield(1, b"1") + _vfield(2, 0)
                  + _bfield(3, b"") + _bfield(4, b"") + _vfield(5, 0))
    for c in cols:
        out += _bfield(2, c.encode("ascii"))
    out += _bfield(3, bytes(int(m) for m, _c in items))
    for _m, c in items:
        out += _bfield(4, c.encode("ascii"))
    return out + _vfield(6, 0)


def _args_hqinfo(code, market, f2=3, f9=5):
    """`PBHQInfo` 请求体（02 文档 §2）。

    ⚠️ **`field2` 必须是 varint**（`10 03`），写成 bytes（`12 01 03`）会让深市代码查不到名字
    （`399001` 直接回 15 B 空包）—— 这个坑踩过。

    `field2`（品种域）实测：`1`=指数域 / `3`=个股·通用 / `2`=空。
    推荐一律 `3`（个股/指数/板块都正确；`000001` 用 f2=1 会返回"上证指数"而不是"平安银行"）。
    `field9`：`5` 要多字段、`0` 只回最少几个（实测 176 B vs 155 B）。
    """
    return (_bfield(1, b"\x28\x00") + _vfield(2, f2) + _bfield(3, code.encode("ascii"))
            + _vfield(4, market) + _vfield(5, 0) + _vfield(6, 0) + _vfield(7, 0)
            + _vfield(8, 0) + _vfield(9, f9) + _vfield(12, 0) + _vfield(13, 0))


def _args_xmlblock(block, spec="RN,0,2", field2=999):
    """`PBXmlBlock` 请求体（03 文档 §2）。

    ====== ========= ==========================================================
    field1 bytes     固定头 `0a 02 28 00`
    field2 varint    抓包里见过 `999`（取到 103 行股票）和 `0`（取到 227 行行业），**语义未定**
    field3 bytes     **取数规格** `"类别,级别,起始,数量"`，如 `"RN,0,2"` / `"HY,1,0,3"`
    field4 **varint** `0` ——⚠️ 必须是 varint（`20 00`），写成空 bytes（`22 00`）服务端只回一个头
    field5 **varint** `0`
    field6 bytes     **块名**，如 `MStock_JCXH_ALL`（注意是 `MStock_`，不是 `MSock_`）
    ====== ========= ==========================================================
    """
    return (_bfield(1, b"\x28\x00") + _vfield(2, field2)
            + _bfield(3, spec.encode("ascii")) + _vfield(4, 0) + _vfield(5, 0)
            + _bfield(6, block.encode("ascii")))


def _args_samplemore(codes):
    """`PBSampleMore` 请求体（04 文档 §2）：重复 `field1`，每个代码一条嵌套消息。

    单条嵌套 = `0a022800 1001 1a06<code> 2001`（16 B），外层再加一层 `0a 10`。
    """
    out = b""
    for c in codes:
        out += _bfield(1, _bfield(1, b"\x28\x00") + _vfield(2, 1)
                       + _bfield(3, c.encode("ascii")) + _vfield(4, 1))
    return out


def _args_hystat(f2=6, f3=-1, f4=0, f5=14, f6=6, f7=0, f8=2):
    r"""`PBHYStat` 请求体（05 文档 §2 + 抓包 5 个变体）。

    `f3=-1` 会被编码成 uint64 二补码 `18ffffffffffffffffff01`（与抓包一致）。

    ⚠️ **`f5` 无条件写出**，`f5 == 0` 也照写 `28 00`。
    证据：2026-10-27 用 `extract_params.py` 对 `cap_gn.pcap` 逐帧解码，`PBHYStat`
    共 53 次出现、去重后**只有 5 种参数，每一种都带 `28 00`**：

    | f3 | f5 | 长度 | hex（f4/f6/f7/f8 恒为 0/6/0/2） |
    |---|---|---|---|
    | -1 | 14 | 27 B | `0a022800 1006 18ffffffffffffffffff01 2000 280e 3006 3800 4002` |
    | 0 | 0 | 18 B | `0a022800 1006 1800 2000 2800 3006 3800 4002` |
    | 3 | 0 | 18 B | `0a022800 1006 1803 2000 2800 3006 3800 4002` |
    | 4 | 0 | 18 B | `0a022800 1006 1804 2000 2800 3006 3800 4002` |
    | 5 | 0 | 18 B | `0a022800 1006 1805 2000 2800 3006 3800 4002` |

    05 文档 §2 曾把"f5 为 0 就不写这个字段"当成抓包铁证，那是**误读**：抓包里
    `f3=3/4/5/0` 那 4 条 18 B 参数都含 `28 00`，漏写会变成 16 B、与抓包不一致。
    参数曾更正过一次，记录见 `CHANGELOG.md`。

    ⚠️ `f3`/`f5` 的**语义仍未定**（见 `board_stat()` 的说明），本函数只忠实编码。
    """
    return (_bfield(1, b"\x28\x00") + _vfield(2, f2) + _vfield(3, f3)
            + _vfield(4, f4) + _vfield(5, f5)
            + _vfield(6, f6) + _vfield(7, f7) + _vfield(8, f8))


#: `PBTickEx` 响应的 7 列（**抓包原样**，`field7` 里就是这 7 个名字）。
#:
#: 语义（**只写实证的，不猜**）：
#:
#: ================== ==========================================================
#: `Second`           当日**秒数**（`40978` → 11:22:58），不是序号
#: `Now`             成交价（**字符串**小数）
#: `NowVol`          本笔成交量，单位**手**（与 `Quote.vol`/`Bar.vol` 同单位 ✅）
#: `VolInStockDiff`  与上一笔的量差（实测多数等于 `NowVol`）
#: `InOutFlag`       `0`/`1`，**语义未定 —— 不许猜买/卖**
#: `HKTTFlag`        `0`/`1` 或 `\x01` 控制字符，**语义未定**
#: `HKInOutFlag`     `0`/`1`，**语义未定**
#: ================== ==========================================================
TICKEX_COLS = ("Second", "Now", "NowVol", "VolInStockDiff",
               "InOutFlag", "HKTTFlag", "HKInOutFlag")


def _args_tickex(code, market, anchor="0", count=100):
    r"""`PBTickEx` 请求体（**抓包逐字节实证**，2026-10-27 `cap_tick_1027.pcap`）。

    ====== ============================================================
    field1 固定资产 `0a022800`（= 嵌套 `{5:0}`）
    field2 code
    field3 market（1=沪 / 0=深）
    field4 固定 `0`
    field5 **锚点**：`"0"` = 最新一页；否则 = 上一页响应的「区间起点」
    field6 每页条数（抓包是 100）
    field7 固定 `0`
    field8 固定 `1`
    ====== ============================================================

    抓包里的原始请求体（`Base64:` 信封内）解出来就是这 8 个字段，
    例如锚点 `0`：`0a022800 1206 363030353139 1801 2000 2a0130 3064 3800 4001`。

    ⚠️ **少一个字段服务端就回空包**（Lead 手搓时漏了 `field6`/`field7` 各一次都空），
    尤其 `field7=0`、`field8=1` 看着"没用"但必须写。
    """
    return (_bfield(1, _vfield(5, 0))
            + _bfield(2, str(code)[:6])
            + _vfield(3, int(market))
            + _vfield(4, 0)
            + _bfield(5, str(anchor))
            + _vfield(6, int(count))
            + _vfield(7, 0)
            + _vfield(8, 1))


# ══════════════════════════════════════════════════════════════════════════
# 二、响应：剥外壳 + 平坦字段解析
# ══════════════════════════════════════════════════════════════════════════
_B64 = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def peel(body: bytes) -> bytes:
    """把响应外壳一层层剥掉：JSON 头 → zlib → base64 →（可能再一轮）。

    实测三种外壳且会**套娃**（00-README §2.4）：

    * `{"Length": "N","AttachLog": ""}` + `base64(protobuf)`
    * `{"Length": "N","AttachLog": ""}` + `zlib(base64(protobuf))`  ← PBCombHQ 6 代码时是这层
    * `{"AttachLog": ""}` + 裸 protobuf（请求体不带 `Base64:` 前缀时）

    所以这里**循环剥到剥不动为止**（最多 4 轮），不假设层数。
    """
    for _ in range(4):
        moved = False
        if body[:1] == b"{":                       # 1) JSON 头
            k = body.find(b"}")
            if k > 0:
                body = body[k + 1:]
                moved = True
        if body[:1] == b"\x78":                    # 2) zlib
            try:
                body = zlib.decompressobj().decompress(body)
                moved = True
            except zlib.error:
                # 探测链语义：「0x78 开头但解不开」= **不是 zlib**（形似而已），
                # 继续走 base64/裸 protobuf 探测 —— 是控制流不是错误，
                # 收窄成 zlib.error 保留静默（打 debug 会每帧噪声）。
                pass
        probe = body[:120]                         # 3) base64 文本
        if probe and sum(1 for c in probe if c in _B64) > len(probe) * 0.95:
            s = bytes(c for c in body if c in _B64)
            for cut in range(4):
                t = s[:len(s) - cut] if cut else s
                try:
                    dec = base64.b64decode(t + b"=" * ((4 - len(t) % 4) % 4))
                except Exception:                  # noqa: BLE001
                    continue
                if len(dec) >= 4:
                    body = dec
                    moved = True
                    break
        if not moved:
            break
    return body


def fields_raw(buf: bytes) -> list[tuple[int, int, object]]:
    """平坦解一层 message，返回 `[(field_no, wire_type, value)]`。

    为什么不直接用 `pb.decode`：那个会**递归**把 wt=2 的载荷当子消息解，
    而这里的载荷既可能是子消息（行）也可能是字符串（单元格），
    递归判定会把 `"880903"` 这类串误判成嵌套。本函数只解一层，由调用方决定怎么下钻。
    """
    out: list[tuple[int, int, object]] = []
    j = 0
    n = len(buf)
    while j < n:
        key = buf[j]
        j += 1
        fn, wt = key >> 3, key & 7
        if wt == 0:                                # varint
            v, sh = 0, 0
            while j < n:
                c = buf[j]
                j += 1
                v |= (c & 0x7F) << sh
                if not c & 0x80:
                    break
                sh += 7
            out.append((fn, 0, v))
        elif wt == 2:                              # 长度前缀
            ln, sh = 0, 0
            while j < n:
                c = buf[j]
                j += 1
                ln |= (c & 0x7F) << sh
                if not c & 0x80:
                    break
                sh += 7
            out.append((fn, 2, buf[j:j + ln]))
            j += ln
        elif wt == 5:                              # fixed32
            out.append((fn, 5, buf[j:j + 4]))
            j += 4
        elif wt == 1:                              # fixed64
            out.append((fn, 1, buf[j:j + 8]))
            j += 8
        else:                                      # 不认识的 wiretype：放弃剩余部分
            break
    return out


def strings_of(buf: bytes) -> list[str]:
    """按长度前缀抽**可读 UTF-8 串**（保持出现顺序）。

    用于 `PBHQInfo`（02 文档 §4.1 明确建议：不要死抠字段号，抽串取第一个含中文的）。
    """
    vals: list[str] = []
    j = 0
    n = len(buf)
    while j < n:
        start = j
        ln, sh, ok = 0, 0, False
        while j < n and sh < 21:
            c = buf[j]
            j += 1
            ln |= (c & 0x7F) << sh
            if not c & 0x80:
                ok = True
                break
            sh += 7
        if not ok or ln == 0 or j + ln > n:
            j = start + 1
            continue
        chunk = buf[j:j + ln]
        try:
            sv = chunk.decode("utf-8")
        except Exception:                          # noqa: BLE001
            j = start + 1
            continue
        if sv and all(ch.isprintable() for ch in sv):
            vals.append(sv)
            j += ln
        else:
            j = start + 1
    return vals


# ══════════════════════════════════════════════════════════════════════════
# 三、各命令的响应解析
# ══════════════════════════════════════════════════════════════════════════
def parse_rowtable(body: bytes):
    """`field3`=列名块、`field4`=每行 的形状 → `(cols, rows)`。

    `PBCombHQ` 与 `PBHYStat` 实测都是这个形状（2026-10-27 逐字段 dump）：

    ```
    field1  bytes   空
    field2  varint  计数（PBCombHQ=请求数；PBHYStat 是更大的总数，语义未定）
    field3  bytes   列名块：嵌套，每个子字段一个列名
    field4  bytes×N **每一行**：嵌套，每个子字段一个单元格
    field5/6 varint 尾部计数（未定）
    ```

    ⚠️ 交接文档 §3 说「第 1 行铺在 `field1`、第 2 行起才用 `field4`」——
    **实测不成立**：本机 6 个代码时 6 行全在 `field4`，`field1` 是空的。
    所以这里只认 `field4`；`field1` 若有内容也按行收（兼容文档描述的那种抓包）。

    ⚠️ **`field3` 可能出现不止一个，且后面的可能是空的**（2026-10-28 修）：
    `cap_1023_183457.pcap` msgid=150（美股 NVDA/AMD 那条）实测 `field3` 出现 2 次 ——
    第 1 个是 8 个真列名、**第 2 个是末尾的空占位块**。旧实现 `if fn == 3: cols = _cells(val)`
    取**最后一个**，于是真列名被空块顶成 `[]`，**77 行好数据整表丢掉**。

    现行规则：
    1. **取第一个"非空列名块"**，空块（`_cells` 返回 `[]`）一律忽略；
    2. 若存在**多个非空列名块且内容不同**，不静默挑一个 —— 抛
       `errors.NotSupportedError`（静默挑一个会把整表按错列切分，比报错危险得多）；
       内容相同则视为重复、照第一个用。

    现实影响（全量 1496 帧 PBCombHQ/PBMultiHQ/PBHYStat 统计）：
    「有 2 个 `field3`」**1 次**（就是上面那个首非空 + 末尾空的帧）、
    「多个非空且内容不同」**0 次** —— 所以规则 2 是防御性护栏，不改变任何现有帧的结果。
    """
    top = fields_raw(body)
    blocks: list[list[str]] = []                   # 所有**非空**列名块，按出现顺序
    cells_by_field: dict[int, list[list[str]]] = {}
    for fn, wt, val in top:
        if wt != 2 or fn not in (1, 3, 4):
            continue
        one = _cells(val)
        if fn == 3:
            if one:                                # 空块是尾部占位，**不许**覆盖真列名
                blocks.append(one)
        elif one:
            cells_by_field.setdefault(fn, []).append(one)

    cols: list[str] = blocks[0] if blocks else []
    for other in blocks[1:]:
        if other != cols:
            raise errors.NotSupportedError(
                "parse_rowtable：出现多个非空列名块（field3）且内容不同，"
                "无法判定哪一个是表头 —— 静默挑一个会把整表按错列切分"
                "（2026-10-28 实测过：1 个末尾空块曾顶掉 77 行真数据）。"
                "第一个（%d 列）= %s …；第二个（%d 列）= %s …。请抓包核对该帧。"
                % (len(cols), cols[:6], len(other), other[:6]))

    rows = cells_by_field.get(1, []) + cells_by_field.get(4, [])
    return cols, rows


def _cells(sub: bytes) -> list[str]:
    """一个嵌套消息里的单元格 → 字符串列表（varint 也按十进制转，保证列数对齐）。

    ⚠️ `wt == 5`（fixed32）**必须先验长度**：`fields_raw` 在流被截断时会给出不足 4 字节的
    载荷，旧实现直接 `struct.unpack("<f", val)` 抛 `struct.error` 冒出解析函数、把整批中断。
    现在长度不是 4 就回 hex（**不跳过**，否则后面所有列会左移错位）。
    """
    out: list[str] = []
    for _fn, wt, val in fields_raw(sub):
        if wt == 2:
            try:
                out.append(val.decode("utf-8"))
            except Exception:                      # noqa: BLE001
                out.append(val.hex())
        elif wt == 0:
            out.append(str(val))
        elif wt == 5:
            if len(val) == 4:
                out.append("%.3f" % struct.unpack("<f", val)[0])
            else:                                  # 截断的 fixed32：给 hex，不抛、不丢格
                out.append(val.hex())
    return out


def parse_name(body: bytes, code: str) -> str | None:
    """`PBHQInfo` → 名称。

    02 文档 §4.1 的建议：**不要死抠字段号**，把整段里所有长度前缀的可读串抽出来，
    取第一个「含中文且不等于代码本身」的。实网 10/10 正确。
    ⚠️ 名称原样返回，不做 strip/规范化（`万 科Ａ` 中间有空格、`Ａ` 是全角）。
    """
    for s in strings_of(body):
        if s != code and any("\u4e00" <= ch <= "\u9fa5" for ch in s):
            return s
    return None


def parse_block(body: bytes) -> dict:
    """`PBXmlBlock` → `{block, status, field6, rows}`。

    字段（03 文档 §4 + 本机 2026-10-27 复核）：
    `field4`=块名回显、`field5`=状态位、`field6`=**某个计数（不是行数，见下）**、
    `field7`=**JSON 数组文本**。

    ⚠️ **`field5` 不是成功标志**（见过 `9` 和 `128`，两个都返回完整数据），
    有没有数据只看 **`field7` 在不在**。

    ⚠️ **`field6` 不是"行数"**（交接文档 §4 写错了）：实测同一块名换 `数量`
    参数时它是 227 / 756 / 376 / 7380，与实际返回的 3 / 10 / 5 / 100 行毫无对应关系。
    真实行数只能数 `field7` 的 JSON 数组长度。语义仍未定，故字段名就叫 `field6`。
    """
    top = fields_raw(body)
    out = {"block": None, "status": None, "field6": None, "rows": None}
    for fn, wt, val in top:
        if fn == 4 and wt == 2:
            try:
                out["block"] = val.decode("utf-8")
            except Exception:                      # noqa: BLE001
                out["block"] = val.hex()
        elif fn == 5 and wt == 0:
            out["status"] = val
        elif fn == 6 and wt == 0:
            out["field6"] = val
        elif fn == 7 and wt == 2:
            try:
                out["rows"] = json.loads(val.decode("utf-8"))
            except Exception:                      # noqa: BLE001
                out["rows"] = None
    return out


def _looks_like_col_names(cols) -> bool:
    """这堆字符串**像不像列名**（而不是一堆数值/二进制垃圾）。

    判据：非空，且 ≥60% 的列名里至少含一个字母。
    为什么必须有这条：`PBZST` 那种帧会把**列与值整体互换** —— 解出来的「列名」是
    `['899', '535.000000', '4999.000000', '192061937', '0', '0.000000']`（其实是值），
    长度对得上、看着完全正常；只查「非空 + 宽度对齐」根本抓不住，只有这条能抓住。
    """
    if not cols:
        return False
    hit = sum(1 for c in cols if any(ch.isalpha() for ch in str(c)))
    return hit * 5 >= len(cols) * 3          # hit/len >= 0.60


def parse_samples_more(body: bytes) -> list[dict]:
    """`PBSampleMore` → `[{code, count, pre_close, floats}]`。

    适用范围**只有 `PBSampleMore`**（`CMD["samplemore"]`）。识别依据：
    响应顶层**只有重复的 `field1`**（每个标的包一层），实网 257 帧 257/257 如此。

    每条（04 文档 §3）：`field3`=代码、`field5`=**声明**的字段数、`field6`=**昨收**(fixed32 float)、
    `field7`=实际个数、`field8`=**打包的 float32 数组**（`实际个数 × 4` 字节）。

    ⚠️ 数组里的浮点**列序至今没锚定、个数还会变**（`field5` 声明 60，`field7`/`field8` 实测
    9/11/13…，随盘中推进增长），所以只给 `f0..fN`，不许猜名字、也不许写死个数。
    ⚠️ float32 转成 float64 会带出 `551.4000244140625` 这种噪声，统一 round 到 3 位
    （TDX 报价就是 3 位小数）。
    ⚠️ **只认顶层 `field1`**：旧实现遍历「任何顶层子消息」，于是把 `PBDeepWarn` 的
    `field2`（里面恰好有 `field3='002990'` 和一个 `field5=14435`）错配成一条假记录
    `{code:'002990', count:14435, pre_close:None, floats:[]}` —— 2026-10-28 修。
    """
    out: list[dict] = []
    for fn, wt, val in fields_raw(body):
        if wt != 2 or fn != 1:                 # ← 只吃 PBSampleMore 的包装层
            continue
        sub = fields_raw(val)
        code = next((v.decode("utf-8") for f, w, v in sub if f == 3 and w == 2), None)
        if not code or not code[:1].isdigit():
            continue
        cnt = next((v for f, w, v in sub if f == 5 and w == 0), None)
        f6 = next((struct.unpack("<f", v)[0] for f, w, v in sub
                   if f == 6 and w == 5 and len(v) == 4), None)   # 截断的 fixed32 不解
        packed = next((v for f, w, v in sub if f == 8 and w == 2), None)
        floats = ([round(struct.unpack("<f", packed[i:i + 4])[0], 3)
                   for i in range(0, len(packed) - 3, 4)] if packed else [])
        out.append({"code": code, "count": cnt,
                    "pre_close": round(f6, 3) if f6 is not None else None,
                    "floats": floats})
    return out


def parse_sample_single(body: bytes) -> list[dict]:
    """`PBSample`（单个标的）→ `[{code, count, declared_count, pre_close, floats}]`，最多 1 条。

    与 `PBSampleMore` 的**结构差别**（实网 324 帧实测）：`PBSample` 的 `field8`（打包 float32）
    在**顶层**、且**没有 `field1` 包装层**：

    ```
    field2 varint   field3 <子消息>   field4 varint
    field5 varint   **声明的字段数**（实测 240）
    field6 fixed32  **昨收**
    field7 varint   **实际个数**（556/4 = 139）
    field8 bytes    打包 float32（`实际个数 × 4` 字节）
    ```

    ⚠️ **这个响应里没有 6 位代码** —— `field3` 是子消息，里面是一个 `field7` fixed64
    （实测 `99999` / `20285` / `20592` / `20096` / `20641` 这类 5 位序数；324 帧里另有 146 帧
    `field3` 干脆是空的），解不出任何股票代码。所以 `code` 如实记 `None`，
    **由调用方按自己请求的代码补**（不编造一个看起来像代码的东西）。
    """
    top = fields_raw(body)
    if any(f == 1 and w == 2 for f, w, _ in top):
        return []                              # 有 field1 包装层 → 那是 SampleMore，不是本形状
    packed = next((v for f, w, v in top if f == 8 and w == 2 and v), None)
    if not packed or len(packed) % 4:
        return []                              # 没有打包数组（或长度不是 4 的倍数）→ 认不出
    declared = next((v for f, w, v in top if f == 5 and w == 0), None)
    actual = next((v for f, w, v in top if f == 7 and w == 0), None)
    f6 = next((struct.unpack("<f", v)[0] for f, w, v in top
               if f == 6 and w == 5 and len(v) == 4), None)
    floats = [round(struct.unpack("<f", packed[i:i + 4])[0], 3)
              for i in range(0, len(packed) - 3, 4)]
    return [{"code": None,                  # 响应里没有代码，不编造
             "count": actual if actual is not None else declared,
             "declared_count": declared,
             "pre_close": round(f6, 3) if f6 is not None else None,
             "floats": floats}]


def parse_samples(body: bytes) -> list[dict]:
    """按**结构**分发到 `PBSampleMore` / `PBSample`；两套都认不出 → `[]`。

    分发依据（互斥，实网实测）：
      * 顶层有 `field1` 包装层、无顶层 `field8` → `PBSampleMore`（257/257 帧）
      * 顶层有 `field8` 打包数组、无 `field1`  → `PBSample`（324/324 帧）
      * 两者都没有（`PBDeepWarn` 只有 `field2`/`field3`；`PBHYStat`/`PBCombHQ` 是 rowtable 形状）
        → `[]`，**不猜**

    ⚠️ 本函数**不再**把「任何顶层子消息里恰好有 `field3` 代码 + `field5` 数字」当成一条记录 ——
    那正是 `PBDeepWarn` 被错配成假 code 的原因（2026-10-28 修）。
    """
    more = parse_samples_more(body)
    if more:
        return more
    return parse_sample_single(body)


# ══════════════════════════════════════════════════════════════════════════
# 四、收发
# ══════════════════════════════════════════════════════════════════════════
def _call(which: str, args: bytes, tries=3) -> bytes | None:
    """发一条 `HQServ:PB*` 并返回**剥壳后**的响应体；失败返回 None。

    载体是 `Base64:<b64>`（抓包原样）。`dc._send` 已经做了帧级重试与 pacer 限流，
    这里只重试"拿到了字节但切不出包"的情况。
    """
    payload = b"Base64:" + base64.b64encode(args)
    for _ in range(max(1, tries)):
        blobs = dc._send(tdx_pb_api.build(CTL[which], CMD[which], payload))
        if blobs:
            return peel(blobs[0])
    return None


def _pairs(symbols_) -> list[tuple[int, str]]:
    """把各种写法的代码列表规范成 `[(market, code), ...]`。

    接受 `"600519"` / `"sh600519"` / `("sh", "600519")` / `(1, "600519")`；
    一律**交给 `symbols.parse()`**（本仓库唯一事实源，D1 的教训：别再维护第二份市场表）。
    """
    if isinstance(symbols_, (str, bytes)):
        symbols_ = [symbols_]
    out: list[tuple[int, str]] = []
    for it in symbols_:
        if isinstance(it, (tuple, list)) and len(it) == 2:
            mk, code = it
            code = _sym.clean(str(code))
            if isinstance(mk, str) and not mk.strip().lstrip("+-").isdigit():
                mk = _sym.parse("%s%s" % (mk.strip(), code))[0]   # ("sh","600519")
            out.append((int(mk), code))
        else:
            out.append(_sym.parse(it))                           # "sh600519" / "600519"
    return out


# ══════════════════════════════════════════════════════════════════════════
# 五、对外接口
# ══════════════════════════════════════════════════════════════════════════
def get_quotes(symbols_, batch=20, *, retry_missing=True, on_missing="raise"):
    """批量报价（`PBCombHQ`）→ `(cols, rows)`；有代码彻底取不到时的行为由 `on_missing` 定。

    `batch`：每次请求放几个代码（抓包只验证到 6 个；上限未测，默认 20 保守）。

    服务端对**无效/不支持的代码是静默不返回**（不报错），所以这里：
    先成批要，缺的码**单独再要一次**。

    `on_missing`（阶段 3 下沉；**只决定抛不抛**，"缺了哪几个"由上层算）
    ------------------------------------------------------------------
    * `"raise"`（默认，行为与旧版一字不差）：仍缺就抛 `EmptyResult` 点名，
      绝不返回"少了几个代码"的表冒充完整结果；
    * `"skip"` / `"report"`：**不抛**，按请求顺序返回能拿到的那些（可能为空列表），
      并把缺失清单交给上层 `Client.get_quotes` 写进 `Quotes.provenance.extra["missing"]`。

    ⚠️ `report` 的 `warnings.warn` **不在这里发**：本层只知道"批里缺了谁"，
    知道"你一共要了哪些"的是上层。提醒只发一次，别让人一次取数收两条同义警告。
    ⚠️ `retry_missing` 与 `on_missing` **不冲突**：前者是"要不要为缺的码再补一次请求"
    （省流量 vs 求全），后者是"补完还缺怎么办"。两者正交，都保留。
    """
    from .._core import check_on_missing

    mode = check_on_missing(on_missing)
    want = _pairs(symbols_)
    if not want:
        raise errors.ParamError("get_quotes 需要至少一个代码")

    cols: list[str] = []
    got: dict[str, list[str]] = {}
    for i in range(0, len(want), max(1, batch)):
        chunk = want[i:i + batch]
        c2, r2 = _one_batch(chunk)
        cols = cols or c2
        for row in r2:
            if row:
                got[row[0]] = row

    missing = [c for _m, c in want if c not in got]
    if missing and retry_missing:
        for code in missing:                   # 逐个单发，仍无就是真没有
            mkt = next(m for m, c in want if c == code)
            c3, r3 = _one_batch([(mkt, code)])
            cols = cols or c3
            for row in r3:
                if row and row[0] == code:
                    got[code] = row

    still = [c for _m, c in want if c not in got]
    if still and mode == "raise":
        raise errors.EmptyResult(
            "PBCombHQ 取不到这些代码（服务端静默不返回，单发也没有）：%s。"
            "⚠️ 8 开头分两类、别混：**北交所证券**（43xxxx/83xxxx/87xxxx/92xxxx，`Setcode=2`）"
            "与本通道 **880xxx/881xxx 板块指数**（`Setcode=1`，如 880903 水产品、881007 油气开采）；"
            "881xxx 曾被误判成北交所（2026-10-27 已修，若你用的是旧版请注意）。"
            "老新三板码（如 430047）本通道不认；其余请核代码是否仍存续。"
            "（若缺失是预期的，用 `get_quotes(..., on_missing=\"skip\")`。）" % ", ".join(still))
    rows = [got[c] for _m, c in want if c in got]   # 按请求顺序返回，不按服务端顺序
    return cols, rows


def _one_batch(items):
    # 多要 VOL/AMOUNT/LB（见 COMBHQ_COLS_FULL）；抓包原样的 17 列仍由 COMBHQ_COLS 钉住。
    body = _call("combhq", _args_combhq(items, COMBHQ_COLS_FULL))
    if not body:
        return [], []
    cols, rows = parse_rowtable(body)
    rows = [r for r in rows if r and _looks_code(r[0])]
    return cols, rows


def _looks_code(s: str) -> bool:
    return len(s) == 6 and s.isdigit()


def name_of(symbol, market=None, *, f2=3, f9=5) -> str:
    """代码 → 名称（`PBHQInfo`）。`symbol` 可给 `'880903'` 或 `('sh','600519')`。

    市场由 `symbols.market_by_code()` 推断；显式给 `market` 可覆盖（如 `000680` 两义）。
    """
    if market is None:
        if isinstance(symbol, (tuple, list)) and len(symbol) == 2:
            market, symbol = int(symbol[0]), symbol[1]
        else:
            market = _sym.market_by_code(str(symbol))
    code = str(symbol).strip()
    body = _call("hqinfo", _args_hqinfo(code, int(market), f2=f2, f9=f9))
    if not body:
        raise errors.EmptyResult("PBHQInfo 无响应：%s（market=%s）" % (code, market))
    name = parse_name(body, code)
    if not name:
        raise errors.EmptyResult(
            "PBHQInfo 未能从响应里认出名称：%s（market=%s，响应 %d 字节）"
            % (code, market, len(body)))
    return name


def block_data(block, spec="RN,0,2", field2=999):
    """块数据（`PBXmlBlock`）→ `(block, status, rows)`；`rows` 是服务端给的 JSON 行。

    **`spec` = `"类别,级别,起始,数量"`**（03 文档只说"取数规格"，本机 2026-10-27 定出后两位）：

    ====== ====================================================
    类别   `HY`=行业 / `RN`=（股票？）已验；`GN`/`FG`/`DQ`/`ETF` 未验
    级别   行业块用 `1`，股票块用 `0`
    起始   **行偏移**：`HY,1,5,5` 从第 5 行开始（实测首行从 `881218` 变 `881418`）
    数量   **要返回几行**：`HY,1,0,3` → 3 行；`...,10` → 10 行
    ====== ====================================================

    ⚠️ **单页上限实测 100 行**（要 150 / 227 都只给 100）。要取全请用 `block_rows()`。
    ⚠️ 块名是服务端页面配置下发的，**不是通用常量**。目前实网验证过：
    `MStock_ZLJX_ADDE_R` + `HY,1,0,N`（行业块）、`MStock_JCXH_ALL` + `RN,0,0,N`（股票块）。
    没有 `field7` 就是块名/参数不对 → **抛错，不返回空表**。
    """
    body = _call("xmlblock", _args_xmlblock(block, spec, field2))
    if not body:
        raise errors.EmptyResult("PBXmlBlock 无响应：block=%s spec=%s" % (block, spec))
    got = parse_block(body)
    if got["rows"] is None:
        raise errors.EmptyResult(
            "PBXmlBlock 响应里没有 field7（块名/参数不对？）：block=%s spec=%s field2=%s，"
            "收到 %d 字节、状态位=%s、field6=%s"
            % (block, spec, field2, len(body), got["status"], got["field6"]))
    return got["block"], got["status"], got["rows"]


def block_rows(block, kind="HY", level=1, max_rows=200, field2=0, page=100):
    """按页取全一个块（`PBXmlBlock` 分页）→ `list[list[str]]`。

    `spec` 的第 3/4 位就是 `起始` / `数量`（本机实测），所以翻页是**服务端原生**的，
    不用客户端算偏移。单页上限实测 100 行，`page` 超过会被服务端截到 100。

    `max_rows` 是硬上限，防止块名给错（或列表异常长）时把 421 个产品全打一遍。
    """
    page = max(1, min(int(page), 100))
    max_rows = max(1, int(max_rows))
    out: list[list[str]] = []
    start = 0
    while len(out) < max_rows:
        want = min(page, max_rows - len(out))
        spec = "%s,%d,%d,%d" % (kind, int(level), start, want)
        _b, _s, rows = block_data(block, spec, field2)
        rows = rows or []
        if not rows:
            break                      # 到底了（或 offset 越界）
        out.extend(rows)
        if len(rows) < want:
            break                      # 最后一页
        start += len(rows)
    return out


def samples(symbols_, batch=15):
    """批量抽样行情（`PBSampleMore`）→ `[{code, count, pre_close, floats}]`。

    ⚠️ `floats` 的**列序未锚定**（04 文档 §3.1 给了锚定方法但还没做全），
    所以只给 `f0..fN` 顺序数组，**不许**当成 high/low/volume，也不许假定个数。
    只要能锚定的 `pre_close`(=field6) 已对上 `get_boards` 的 CLOSE。
    """
    want = _pairs(symbols_)
    if not want:
        raise errors.ParamError("samples 需要至少一个代码")
    out: list[dict] = []
    for i in range(0, len(want), max(1, batch)):
        codes = [c for _m, c in want[i:i + batch]]
        body = _call("samplemore", _args_samplemore(codes))
        if body:
            out.extend(parse_samples(body))
    if not out:
        raise errors.EmptyResult("PBSampleMore 一个都没返回：%s" % ", ".join(c for _m, c in want))
    return out


def board_stat(f2=6, f3=-1, f5=14):
    """板块成分统计（`PBHYStat`）→ `(cols, rows)`。

    `f5` 选**视图**，`f3` 选**板块类别**（两种视图下都生效，只是 `f5=14` 时分叉点在第 9 行；
    2026-10-27 实机扫描定出，见 `docs/VERIFICATION.md`）：

    | `f5` | 返回形态 |
    |---|---|
    | `0` | 12 行**纯板块**（无成分行） |
    | `14` | 12 行 = **板块行 + 一只成分行交替**（默认） |

    `f3` 的实测含义（下表是用 `f5=0` 扫出来的，每类 12 行、`2` 为空包）：

    | `f3` | 类别 | 样例 |
    |---|---|---|
    | `-1` / `0` / `6` / `8` | 行业（三级） | 881007 油气开采、881008 油服工程 |
    | `1` | 行业（按涨幅排序） | 881439 会展服务、881045 合成树脂 |
    | `3` | 概念 | 880903 水产品、880549 可燃冰 |
    | `4` | 特色/资金榜单 | 880812 昨日连板、880863 昨日涨停 |
    | `5` | 地区 | 880214 宁夏、880207 北京 |
    | `7` | 一级行业（大类） | 881006 石油、881417 房地产 |
    | `2` | （无数据） | 空包 |

    ⚠️ `f3` 在 `f5=14` 下**仍然生效**，只是**分叉点在后半段**：`f3=-1` 与 `f3=3` 的
    前 8 行（4 组「板块行 + 成分行」）是各 `f3` 共有的**头部榜单**，
    **从第 9 行起按类别分叉**（`f3=-1` → 880861 连续亏损 / 880712 微小盘股；
    `f3=3` → 880731 新材料 / 880522 低空经济）。同一 `f3` 连发两次结果完全一致，
    所以差异确实来自 `f3`，不是数据漂移。

    ⚠️ `Other` 列**仍不定名**：`f5=0` 时是 ±0.1 量级、`f5=14` 时板块行是大整数而成分行 `0.000`，
    量纲随 `f5` 变，原样返回，别当市值/成交额。

    本函数仍**不接板块代码**（请求里没有板块代码位）—— 要指定板块请用
    `get_board_members(board)`，别拿这里的类别列表冒充。
    """
    body = _call("hystat", _args_hystat(f2=f2, f3=f3, f5=f5))
    if not body:
        return [], []
    cols, rows = parse_rowtable(body)
    return cols, [r for r in rows if r and _looks_code(r[1] if len(r) > 1 else "")]


# ══════════════════════════════════════════════════════════════════════════
# 六、逐笔成交（PBTickEx，2026-10-27 打通）
# ══════════════════════════════════════════════════════════════════════════
def _txt(v) -> str:
    """把单元格转成字符串。**控制字符不能炸**：`HKTTFlag` 实测会回 `b"\\x01"` 或 `b""`。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        for enc in ("utf-8", "gbk"):
            try:
                return v.decode(enc)
            except UnicodeDecodeError:
                continue
        return v.decode("latin-1")
    return str(v)


def parse_ticks(body: bytes) -> dict:
    r"""解析 `PBTickEx` 响应（**纯函数，离线可测**）。

    返回::

        {"cols": [...7 个列名...],
         "rows": [{"Second": "40978", "Now": "1349.130005", ...}, ...],
         "span": "1995,2095",      # 本次返回的序号区间「起点,终点」
         "anchor": (1995, 2095),   # 上一行的整数形式；翻页用 anchor[0]
         "date": "20261027"}

    结构（实网 + 夹具双向核对）::

        field1.field4 = "1995,2095"   ← 区间，**翻页锚点就是它的起点**
        field2 = 1        field3 = code
        field4 = 20261027 field5 = 本页锚点回显
        field7 = 7 列列名
        field8 = 重复 N 次，每次 = 7 个**字符串**（不是 float！）

    ⚠️ 行里的值**全是字符串**（`"1349.130005"`、`"\\x01"`），转数字由调用方做。
    ⚠️ `Second` 是**当日秒数**（40978 → 11:22:58），不是序号、也不是 `HHMMSS`。

    ⚠️ **认不出列名就返回空，绝不拿 `TICKEX_COLS` 顶包**（2026-10-28 修）：
    旧实现写的是 `if not cols: cols = list(TICKEX_COLS)` —— 于是把**任何**没有 `field7`
    的帧都贴上 7 个正经列名，还照样 `zip` 出行来。实网反例（M6 抓包证据）：
      * `PBHQInfo` → `Now='mLO@'`、`HKTTFlag='奐譓'`（二进制垃圾被当文本输出）
      * `PBZST`    → 列与值**整体互换**（「列名」其实是值），长度还对得上
    这比返回空**危险得多**：字段名完全正常，下游无从察觉。
    现在判据是 `_looks_like_col_names`（≥60% 的列名含字母），不满足就
    `{"cols": [], "rows": [], ...}`。**返回空而不抛错**的理由见下。

    为什么选「返回空」而不是抛 `NotSupportedError`：`_collect_ticks` 的停止条件是
    `if not got or not got.get("rows"): break` —— **空行本来就是翻页的正常终止态**。
    而「一页没有成交」时的空响应很可能同样不带 `field7`；若改成抛错，就会在**正常翻页
    到底**时炸掉，而这是活代码路径（`get_ticks`）。返回空能同时覆盖「真空页」与「异形帧」，
    且不会让异常穿出到用户代码 —— 爆炸半径比修的这个 bug 更大。
    """
    raw = peel(body)
    top = fields_raw(raw)
    cols: list[str] = []
    rows: list[list[str]] = []
    span = None
    date = None
    for f, w, v in top:
        if f == 1 and isinstance(v, bytes):
            for f2, _w2, v2 in fields_raw(v):
                if f2 == 4 and isinstance(v2, bytes):
                    span = _txt(v2)
        elif f == 7 and isinstance(v, bytes):
            cols = [_txt(x[2]) for x in fields_raw(v) if x[0] == 1]
        elif f == 8 and isinstance(v, bytes):
            rows.append([_txt(x[2]) for x in fields_raw(v)])
        elif f == 4 and w == 0:
            date = str(v)
    anchor = None
    if span and "," in span:
        a, b = span.split(",", 1)
        try:
            anchor = (int(a), int(b))
        except ValueError:
            anchor = None
    if not _looks_like_col_names(cols):
        # 没有列名块 / 列名不像列名 → 一行都不给（span/date 是真解出来的，照留）
        return {"cols": [], "rows": [], "span": span, "anchor": anchor, "date": date}
    dicts = [dict(zip(cols, r)) for r in rows]
    return {"cols": cols, "rows": dicts, "span": span, "anchor": anchor, "date": date}


def ticks(code, market, anchor="0", count=100) -> dict | None:
    """取**一页**逐笔（`PBTickEx`）。返回 `parse_ticks()` 的字典；失败 `None`。

    `anchor="0"` 取最新一页；翻页把上一页的 `anchor[0]`（区间起点）传进来。
    ⚠️ 别拿行里的 `Second` 当锚点（实测那样回一整页 0），锚点是**区间起点**。
    """
    body = _call("tickex", _args_tickex(code, market, anchor=anchor, count=count))
    if not body:
        return None
    return parse_ticks(body)
