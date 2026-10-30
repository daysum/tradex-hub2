# -*- coding: utf-8 -*-
"""TTL + LRU 结果缓存（库内私有）。

为什么缓存是**可选注入**而不是默认开启
------------------------------------
7615 的取数是"慢速串行 + 服务端限流"，同一个产品同一天被重复问是很常见的浪费；
但缓存会改变 `Client` 的默认语义（"每次都真的去服务端"），而且行情类数据
一旦缓存错了比慢更糟。所以：`Client` 默认**不缓存**，需要的人显式传
`cache=TTLCache(...)`（接线由 Client 侧完成，见 `API设计.md` §2.6）。

设计
----
* `OrderedDict` 存 `key -> (expire_at, value)`，`move_to_end` 实现 LRU；
* 一把 `threading.Lock` 管全部状态（操作都是 O(1)，不值得更细的锁）；
* `clock` 可注入 —— 测试用假时钟"快进时间"，**不必真 sleep**。

注意
----
`get()` 用 `return None` 表示未命中（契约 `-> Any | None`），
所以**值为 `None` 的条目无法与未命中区分** —— 存 `None` 没有意义，别存。
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict

__all__ = ["TTLCache", "cache_key"]


class TTLCache:
    """线程安全的 TTL + LRU 缓存。

    参数
    ----
    ttl : float
        默认存活秒数（`set(..., ttl=...)` 可逐条覆盖）。
    maxsize : int
        条目上限，超出就淘汰**最久未使用**的那条。
    clock : callable
        取当前时刻的函数，默认 `time.monotonic`。测试注入假时钟即可快进。
    """

    def __init__(self, ttl: float = 300.0, maxsize: int = 2048,
                 clock=time.monotonic):
        maxsize = int(maxsize)
        if maxsize < 1:
            raise ValueError("maxsize 必须 ≥ 1（收到 %r）" % (maxsize,))
        self._lock = threading.Lock()
        self._data: "OrderedDict" = OrderedDict()
        self._ttl = float(ttl)
        self._maxsize = maxsize
        self._clock = clock
        self._hits = 0
        self._misses = 0
        self._evictions = 0          # 因超容量被淘汰的条数
        self._expired = 0            # 因过期被清掉的条数（读时发现）

    # ── 读写 ──────────────────────────────────────────────────────────────
    def get(self, key, default=None):
        """取值：命中且未过期返回 value；未命中/已过期返回 `default`。"""
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return default
            expire_at, value = item
            if expire_at <= now:                 # 过期当 miss，顺手删掉
                del self._data[key]
                self._expired += 1
                self._misses += 1
                return default
            self._data.move_to_end(key)          # 命中即刷新 LRU 位置
            self._hits += 1
            return value

    def set(self, key, value, ttl: float | None = None) -> None:
        """写值。`ttl` 不给用默认值；**有效 ttl ≤ 0 表示不缓存**（直接不存）。"""
        life = self._ttl if ttl is None else float(ttl)
        if life <= 0:
            return
        expire_at = self._clock() + life
        with self._lock:
            if key in self._data:
                self._data[key] = (expire_at, value)
                self._data.move_to_end(key)
            else:
                self._data[key] = (expire_at, value)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)   # LRU 尾部淘汰
                self._evictions += 1

    def __contains__(self, key) -> bool:
        """是否有一条**未过期**的条目。不计入 hits/misses（纯查询，别污染统计）。"""
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            return item is not None and item[0] > now

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        """清空条目（**不清零** hits/misses/evictions 计数，便于长期观测）。"""
        with self._lock:
            self._data.clear()

    def purge(self) -> int:
        """主动清掉已过期条目，返回清掉的条数（长时间不用时可调用）。"""
        now = self._clock()
        with self._lock:
            dead = [k for k, (exp, _v) in self._data.items() if exp <= now]
            for k in dead:
                del self._data[k]
            self._expired += len(dead)
            return len(dead)

    def stats(self) -> dict:
        """观测快照：命中数 / 未命中数 / 淘汰数 / 当前条目数 / ttl。"""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expired": self._expired,
                "size": len(self._data),
                "ttl": self._ttl,
                "maxsize": self._maxsize,
            }

    def __repr__(self) -> str:
        s = self.stats()
        return ("<TTLCache size=%d/%d ttl=%.1fs hits=%d misses=%d>"
                % (s["size"], s["maxsize"], s["ttl"], s["hits"], s["misses"]))


# ── 缓存键 ────────────────────────────────────────────────────────────────
def cache_key(cmd, params=None, trade_date=None) -> tuple:
    """把 `(cmd, params, trade_date)` 归一成**可哈希、与 dict 顺序无关**的键。

    ```python
    cache_key("CWServ.x", {"a": 1, "b": 2}) == cache_key("CWServ.x", {"b": 2, "a": 1})
    ```

    * dict/list 用 `json.dumps(..., sort_keys=True, default=str)` 归一，
      所以嵌套 dict 的顺序也不影响结果；
    * `params` 若是 JSON 字符串，会先尝试解析再归一（这样"字符串形态"和
      "对象形态"的同一次请求得到同一个键）；解析不了的字符串按原文入键；
    * 非 JSON 可序列化对象（如 `Decimal`）由 `default=str` 兜底，不会抛错。
    """
    return (str(cmd), _norm_params(params), _norm_scalar(trade_date))


def _norm_params(params) -> str:
    if isinstance(params, str):
        try:
            return json.dumps(json.loads(params), sort_keys=True, default=str,
                              ensure_ascii=False)
        except (ValueError, TypeError):
            return params
    return json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)


def _norm_scalar(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
