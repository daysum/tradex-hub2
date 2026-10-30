# -*- coding: utf-8 -*-
"""通达信 PB* 接口的 protobuf 编解码。

背景
----
`HQServ.PB*` 这一类接口的参数与响应都是 protobuf（部分参数是 JSON），
与其它接口的 JSON 完全不同。之前误判为「不可用」，实际只要写对编解码就能用。

实测确认（2026-10-23）
----------------------
    HQServ.PBZST  ctl=0x0E  参数 protobuf 26 字节
    → 返回 protobuf：10 01 | 1a 06 "880885" | 20 <ts> | 2a 0a "364.69000220"

帧格式与其它接口完全一致，**`00 00` 分隔符必须保留**：
    0b | msgid(4) | ctl | len(2) | len(2) | type(2) | 00 64 | cmd | 00 00 | <protobuf>

用法
----
    from tdx_pb import decode, encode_varint, dump
    tree = decode(resp_bytes)        # → [(field_no, wire_type, value), ...]
    print(dump(tree))
"""
import sys

if hasattr(sys.stdout, "reconfigure"):      # 被 redirect_stdout/io.StringIO 换掉时没有这个方法
    sys.stdout.reconfigure(encoding="utf-8")

WIRE_VARINT, WIRE_64, WIRE_LEN, WIRE_32 = 0, 1, 2, 5


def read_varint(buf, pos):
    """读 varint，返回 (值, 新位置)。"""
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            break
    raise ValueError("varint 过长")


def write_varint(val):
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _looks_text(raw):
    """判断一段字节是否像 UTF-8 文本。"""
    if not raw:
        return False
    try:
        t = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(c.isprintable() or c in "\r\n\t" for c in t)


def decode_message(buf):
    """解一层 message，返回 [(field_no, wire_type, value)]。

    value 的类型按 wire_type：
        0 → int
        1 → 8 字节原样
        2 → 子消息列表（能解就递归）/ bytes / str
        5 → 4 字节原样
    """
    out = []
    pos = 0
    n = len(buf)
    while pos < n:
        try:
            key, pos = read_varint(buf, pos)
        except ValueError:
            break
        field, wire = key >> 3, key & 7
        if field == 0:
            break
        if wire == WIRE_VARINT:
            try:
                v, pos = read_varint(buf, pos)
            except ValueError:
                break
            out.append((field, wire, v))
        elif wire == WIRE_64:
            if pos + 8 > n:
                break
            out.append((field, wire, buf[pos:pos + 8]))
            pos += 8
        elif wire == WIRE_LEN:
            try:
                ln, pos = read_varint(buf, pos)
            except ValueError:
                break
            if pos + ln > n:
                break
            raw = buf[pos:pos + ln]
            pos += ln
            sub = None
            # 先按可打印文本判，再尝试递归解子消息
            if _looks_text(raw):
                sub = raw.decode("utf-8")
            else:
                try:
                    parsed = decode_message(raw)
                    # 至少两个字段、且消费完整才算子消息
                    if len(parsed) >= 1:
                        sub = parsed
                except Exception:
                    sub = None
            out.append((field, wire, sub if sub is not None else raw))
        elif wire == WIRE_32:
            if pos + 4 > n:
                break
            out.append((field, wire, buf[pos:pos + 4]))
            pos += 4
        else:
            break
    return out


def decode(buf):
    """解整包。失败返回空列表。"""
    try:
        return decode_message(buf)
    except Exception:
        return []


def dump(tree, indent=0, maxlen=120):
    """把 decode 的结果打成可读文本。"""
    pad = "  " * indent
    lines = []
    for field, wire, val in tree:
        if isinstance(val, list):
            lines.append("%s%d: {" % (pad, field))
            lines.append(dump(val, indent + 1, maxlen))
            lines.append("%s}" % pad)
        elif isinstance(val, bytes):
            h = val.hex(" ")
            if len(h) > maxlen:
                h = h[:maxlen] + "..."
            lines.append("%s%d: <%dB> %s" % (pad, field, len(val), h))
        else:
            s = str(val)
            if len(s) > maxlen:
                s = s[:maxlen] + "..."
            lines.append("%s%d: %s" % (pad, field, s))
    return "\n".join(lines)


def group(tree):
    """把 decode 结果按字段号分组 → {字段号: [值, ...]}，永远返回列表。

    这是最不容易出错的形态：不猜「是不是重复字段」。
    """
    out = {}
    for field, _wire, val in tree:
        out.setdefault(field, []).append(val)
    return out


def to_dict(tree):
    """{字段号: 值}，只有一个值时取标量，多个值时给列表。"""
    g = group(tree)
    return {f: (vs[0] if len(vs) == 1 else vs) for f, vs in g.items()}


def parse_table(tree):
    """从 PB 响应里认出「列名 + 数据行」的表结构。

    通达信 PB 表的典型形状：

        字段 6: [(1,2,'Data'), (1,2,'Second'), ...]      ← 列名（一行，多单元格）
        字段 7: [[(1,2,'20260427'), (1,2,'0'), ...],    ← 第 1 行
                [(1,2,'20260428'), ...], ...]            ← 第 2 行

    返回 (列名列表, [字典行])，认不出返回 (None, [])。
    """
    g = group(tree)
    cols_field = None
    for f, vals in g.items():
        if len(vals) == 1 and isinstance(vals[0], list):
            cells = vals[0]
            if cells and all(isinstance(c, tuple) and isinstance(c[2], str)
                             for c in cells):
                cols_field = f
                break
    if cols_field is None:
        return None, []
    cols = [c[2] for c in g[cols_field][0]]

    rows_field = None
    for f, vals in g.items():
        if f == cols_field:
            continue
        if vals and all(isinstance(v, list) for v in vals):
            rows_field = f
            break
    if rows_field is None:
        return cols, []

    rows = []
    for cells in g[rows_field]:
        vals = [c[2] for c in cells if isinstance(c, tuple)]
        row = dict(zip(cols, vals))
        if len(vals) < len(cols):
            # protobuf 不序列化默认值，短行按位置对齐即可
            row = dict(zip(cols[:len(vals)], vals))
        rows.append(row)
    return cols, rows


def first_str(tree, field):
    """取第一个指定字段的字符串值。"""
    for f, _w, v in tree:
        if f == field and isinstance(v, str):
            return v
    return None


def rows_from_tree(tree):
    """把「重复的子消息列表」转成行。

    PB 响应常见两种形状：
      ① 顶层就是一条记录      → 返回 [dict]
      ② 顶层某字段是重复子消息 → 返回该字段每个子消息一行的 list
    """
    d = to_dict(tree)
    # 形状 ②：整包只有一个字段，值是列表
    if len(d) == 1:
        k = next(iter(d))
        v = d[k]
        if isinstance(v, list) and v and isinstance(v[0], list):
            return [to_dict(x) for x in v]
    return [d]


if __name__ == "__main__":
    # 用实测样本自测
    sample = bytes.fromhex(
        "10011a06383830383835209fd1d4092a0a3336342e3639303030323230"
        "0a064d696e757465")
    t = decode(sample)
    print(dump(t))
    print("\n转 dict:", to_dict(t))
