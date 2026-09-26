# 分钟 K 线 / 复权 / 分页全量 / 代码表 —— 接口扩展方案

> 状态：**已实施完成（v3.3.30，2026-09-26）**。实施记录见文末 §11。
> 目标版本：v3.3.30
> 编写依据：eltdx 3.2.2 源码与官方方法文档 + 对本机真实主站的实测（2026-09-26 凌晨）
> 涉及文件：`eltdx_fetchers.py` / `akshare_fetchers.py` / `registry.py` / `tools/eltdx_data.py`
> 文档同步：`README.md` / `tradex/README.md` / `tradex-hub-接口与安装手册.md` / `docs/known-issues.md` / `CHANGELOG.md`

---

## 0. TL;DR

用户提出的 4 类能力，「已有」1 类、「半有」2 类、"真缺"1 类；**同时还查出 2 个正在静默产出错数据的 P0 缺陷**
（请求 5m 拿到日线、请求 1m 拿到月线，均无任何报错）。

本次改动分两批：

| 批次 | 内容 | 级别 |
|---|---|---|
| A | 修 2 个静默错误（周期归一化 + 备源禁止静默回落） | P0 |
| B | 放行分钟 K 线/复权、补全分页参数、代码表按品种取、新增近期分时 | P1/P2 |

预计改动 350~450 行（含测试），不涉及数据源架构调整。

---

## 1. 需求 → 现状对照

| # | 用户示例 | 现状结论 | 证据 |
|---|---|---|---|
| 1 | `bars.get(code, period="5m", count=100, adjust="qfq")` | **不支持，且静默返回错数据** | 见 §3-① |
| 2 | `bars.get(code, period="1m", all_pages=True, max_pages=80)` | 分页已有（含 `sorted()` 重排），但 `max_pages` 写死默认 30、无 `adjust`/`page_size` 入参 | 见 §3-③ |
| 3 | `minutes.history(code, "2026-09-18")` | **已有**（`eltdx_get_minute_history`，实测 240 点正常） | 见 §3-④ |
| 4 | `codes.stocks(market="bj")` | 半有：只有 `eltdx_get_security_codes(market, category)`，返回全品种后需手动过滤；无 `stocks/a_shares/etfs/indices` 语义 | 见 §3-⑤ |

---

## 2. 现状代码定位

| 位置 | 作用 | 问题 |
|---|---|---|
| `eltdx_fetchers.py:279-299` | `_PERIOD_ALIASES` + `_normalize_period` | 只认 day/week/month；`"1m": "month"` 埋雷 |
| `eltdx_fetchers.py:302-329` | `fetch_historical_kline`（→ `eltdx_get_kline`） | 走归一化 → 分钟周期直接抛错 → 降级 akshare → **返回日线** |
| `eltdx_fetchers.py:591-607` | `fetch_adjusted_kline`（→ `eltdx_get_adjusted_kline`） | 走归一化 → 分钟周期硬失败（独占源无备源） |
| `eltdx_fetchers.py:569-588` | `fetch_full_kline`（→ `eltdx_get_full_kline`） | period **不归一化**（所以 `1m` 反而真是分钟，与 #2 语义相反）；`max_pages=30` 太小 |
| `eltdx_fetchers.py:357-388` | `fetch_security_codes` | 只支持 `codes.all/all_markets`，无品种过滤 |
| `eltdx_fetchers.py:433-457` | `fetch_minute_history` | ✅ 已实现，无需动 |
| `akshare_fetchers.py:69-82` | `_AK_PERIOD_ALIASES` | 同样 `"1m": "monthly"` |
| `akshare_fetchers.py:151` | `_EM_KLT = {daily/weekly/monthly}` | 无分钟映射 |
| `akshare_fetchers.py:205` | `"klt": _EM_KLT.get(period, "101")` | **未识别周期静默取 101=日K**（P0 根因） |
| `data_sources/registry.py:99-116` | 路由注册 | 无 `recent_minute` 路由 |
| `tools/eltdx_data.py:361-406` | `eltdx_get_kline` | docstring 已宣称支持 5m/15m/30m/60m，但实际是坏的 |
| `tools/eltdx_data.py:471-511` | `eltdx_get_security_codes` | 无 `kind` 快捷参数 |
| `tools/eltdx_data.py:665-705` | `eltdx_get_full_kline` | 只暴露 `code` / `period` |
| `tools/eltdx_data.py:708-750` | `eltdx_get_adjusted_kline` | 无分页参数 |

---

## 3. 实测证据（可复现）

命令：`.venv\Scripts\python.exe <脚本>`，脚本内容见下（均已执行，临时文件已清理）。

### ① 直连 eltdx：分钟周期 + 复权 **底层完全可用**

```
[OK] bars.get 5m adjust=qfq: period_name=5m count=5 first=2026-09-24 14:40:00+08:00 adj=qfq
[OK] bars.get 1m:            period_name=1m count=3 first=2026-09-24 14:58:00+08:00
[OK] bars.get 60m:           period_name=60m count=3 first=2026-09-24 11:30:00+08:00
[OK] bars.get sh600519 15m:  count=4 first=2026-09-24 14:15:00+08:00
[OK] bars.get sz000001 15m:  count=4 first=2026-09-24 14:15:00+08:00
[OK] bars.get sz300750 15m:  count=4 first=2026-09-24 14:15:00+08:00
```

→ 结论：**不是上游能力问题，是本项目自己的周期归一化把它挡住了。**

### ② 跨页确实乱序（用户提示正确），项目已处理

```
bars.get 1m all_pages(max_pages=None): count=22560
  first=2026-09-21 13:41  last=2026-05-15 13:40  页内拼接已升序=False   ← 整体降序
bars.get 5m all_pages adjust=qfq:      count=23520
  first=2026-09-02 10:55  last=2024-09-27 13:40
```

`max_pages=3` 时按 SDK 设计抛 `RuntimeError: bars.get reached max_pages before the server
returned an empty page`（页数不够不会静默截断，这点是好的）。

`fetch_full_kline` 已 `sorted(bars, key=_bar_sort_key)`，无需再改排序逻辑。

### ③ 项目路由实测：**5m 静默返回日线（P0）**

```
SmartRouter: historical_kline via eltdx FAILED:
  ValueError: unsupported period: '5m' (supported: day/daily/d/1d, week/..., month/m/1m)

[OK] historical_kline period=5m -> src=akshare shape=(1498, 12)
      head: {'日期': datetime.date(2020, 7, 27), ...}      ← 2020 年的日线！
```

链路：eltdx 拒绝 `5m` → SmartRouter 降级 akshare → 东财 `_EM_KLT.get("5m", "101")` 落默认值
`101`=日K → **返回 1498 根日线，无任何告警**。

### ④ 项目路由实测：**1m 返回月线（P0）**

```
[OK] historical_kline period=1m -> src=eltdx shape=(50, 7)
      head: {'日期': Timestamp('2022-08-31 15:00:00+0800'), ...}   ← 月线！
```

`_PERIOD_ALIASES["1m"] = "month"`。而 `full_kline` 不做归一化，`period="1m"` 是真 1 分钟
（实测 22560 根）——**同一参数名在两个工具里语义相反**。

### ⑤ 项目路由实测：分钟复权硬失败 + 代码表返回全品种

```
[FAIL] adjusted_kline period=5m: RuntimeError: Exclusive source 'eltdx' for 'adjusted_kline'
       failed with ValueError: unsupported period: '5m'

[OK] security_codes market=bj -> shape=(385, 4)     ← 含指数/ETF
      head: {'代码': 'bj899050', '名称': '北证50', '类别': 'index', ...}
```

### ⑥ 直连 eltdx：代码表与方法可用性

```
[OK] codes.stocks('bj'):  stocks_bj=350  sample=[('bj920000','安徽凤凰','a_share'), ...]
[OK] codes.all('bj'):     all_bj=385
[OK] codes.a_shares('bj'): a_shares_bj=350
[OK] minutes.history 2026-09-18: points=240 first=09:31
[OK] minutes.recent: points=0（周六无数据，不抛错）
```

### ⑦ 备源（东财）分钟能力：**有，但有窗口限制**

```
klt=1  → "2026-09-24 09:31,1250.01,1249.99,..."   ✅ 分钟K可取
klt=5  → "2026-08-13 09:35,1338.00,1344.00,..."
klt=60 → "2026-08-13 10:30,1338.00,1349.00,..."
```

东财 `klt` 支持 `1/5/15/30/60`，`fqt=1` 即前复权 → **备源可以真正支持分钟**，不需只做兜底报错。

⚠️ 两个约束（实现时必须处理）：
1. 分钟 K 线**可用窗口有限**（实测 klt=1 覆盖最近数日、klt=5/60 覆盖约 1 个月），
   且返回顺序为「窗口内自早到晚」，必须带 `lmt` 并取**尾部** N 根。
   → 具体窗口长度与 `lmt` 语义**需在实现时用项目统一入口 `em_client.em_get` 复核**（见 §8 风险）。
2. 裸 `urllib`/`requests` 直打 `push2his` 会被拒连（实测 `RemoteDisconnected`），
   必须走项目已有的统一防封层 `em_get`（与 `docs/known-issues.md` A 组结论一致）。

---

## 4. 设计口径

### 4.1 周期命名（**对齐 eltdx 官方**，评审已确认）

| 口径 | `period` 取值 |
|---|---|
| 分钟 | `1m` / `1min` / `minute`、`5m` / `5min`、`15m` / `15min`、`30m` / `30min`、`60m` / `60min` / `1h` |
| 日 | `day` / `daily` / `d` / `1d` |
| 周 | `week` / `weekly` / `w` / `1w` |
| 月 | `month` / `monthly` / `1mo`（**`1m` 不再表示月**，`m` 保留兼容但标注 deprecated） |
| 季 / 年 | `quarter` / `q` / `1q`、`year` / `y` / `1y` |
| 扩展（可选） | `10m`、`20m`（`8/N`）、`2d`（`9/N`）、`5s`（`13/N`） |

### 4.2 双源对齐表

| 规范化周期 | eltdx `bars.get(period=)` | 东财 `klt` | 备注 |
|---|---|---|---|
| `1m` | `1m` | `1` | |
| `5m` | `5m` | `5` | |
| `15m` | `15m` | `15` | |
| `30m` | `30m` | `30` | |
| `60m` | `60m` | `60` | |
| `day` | `day` | `101` | |
| `week` | `week` | `102` | |
| `month` | `month` | `103` | |

### 4.3 破坏性变更与兼容策略

| 项 | 说明 | 处置 |
|---|---|---|
| `period="1m"` 语义 | 月线 → **1 分钟** | CHANGELOG 标 `### Changed (BREAKING)`；手册醒目提示；仓库内 grep 无调用点（已核） |
| `period="m"` | 仍为月线 | 保留，docstring 标注 deprecated → 建议 `1mo` |
| 外部 MCP 调用方 | WorkBuddy 技能/脚本若用 `1m` 取月线会变行为 | 手册 + README 显式写一条「升级注意」 |
| 未识别周期 | 现在：eltdx 抛错 + akshare 静默给日线 | 改为：**两源都显式抛错**（不再有静默数据） |

---

## 5. 逐文件改动（含 diff 草案）

### 5.1 `eltdx_fetchers.py` —— 周期归一化重写

替换 `:279-299`：

```python
# 周期口径：与 eltdx 官方一致（docs/methods/7709-K线周期线.md）
_PERIOD_ALIASES = {
    # 分钟
    "1m": "1m", "1min": "1m", "minute": "1m",
    "5m": "5m", "5min": "5m",
    "15m": "15m", "15min": "15m",
    "30m": "30m", "30min": "30m",
    "60m": "60m", "60min": "60m", "1h": "60m",
    # 日线及以上
    "day": "day", "daily": "day", "d": "day", "1d": "day",
    "week": "week", "weekly": "week", "w": "week", "1w": "week",
    # v3.3.30 BREAKING: '1m' 改指 1 分钟；月线用 month/monthly/1mo（'m' 保留兼容）
    "month": "month", "monthly": "month", "m": "month", "1mo": "month",
    "quarter": "quarter", "q": "quarter", "1q": "quarter",
    "year": "year", "y": "year", "1y": "year",
}

_MINUTE_PERIODS = frozenset({"1m", "5m", "15m", "30m", "60m"})
_SUPPORTED_HINT = (
    "1m/5m/15m/30m/60m, day, week, month, quarter, year "
    "(aliases: 1min/5min/15min/30min/60min/1h, daily/d/1d, "
    "weekly/w/1w, monthly/m/1mo, q/1q, y/1y)"
)


def _normalize_period(period: str) -> str:
    """归一化 K 线周期命名 → eltdx 期望的规范名。

    v3.3.30 起支持分钟周期（1m/5m/15m/30m/60m）与季/年线。
    ⚠️ 破坏性变更：'1m' 语义由「月」改为「1 分钟」，与 eltdx 官方口径对齐；
       月线请改用 'month' / 'monthly' / '1mo'。

    未识别周期**直接抛错**：绝不返回一个「看起来像 K 线」的默认周期。
    （历史事故：上层把 5m 打到备源，东财 klt 落默认 101 → 静默返回日线。）
    """
    key = str(period or "").strip().lower()
    normalized = _PERIOD_ALIASES.get(key)
    if normalized is None:
        raise ValueError(f"unsupported period: {period!r} (supported: {_SUPPORTED_HINT})")
    return normalized


def _is_minute_period(period: str) -> bool:
    return _normalize_period(period) in _MINUTE_PERIODS
```

### 5.2 `eltdx_fetchers.py` —— `fetch_historical_kline`（`:302-329`）

周期归一化入口不变（改完 §5.1 即自动支持分钟），但**需统一行构造**：
现在这里用的是 `getattr(b, "time", None)`（原始 datetime），而 `fetch_full_kline` /
`fetch_adjusted_kline` 用 `_bar_to_row`（`strftime("%Y-%m-%d %H:%M")`）。
分钟 K 线必须带时分，两者行为不一致会让上层解析踩坑。

```diff
-    rows = []
-    for b in bars:
-        rows.append({
-            "日期": getattr(b, "time", None),
-            "开盘": getattr(b, "open", None),
-            "最高": getattr(b, "high", None),
-            "最低": getattr(b, "low", None),
-            "收盘": getattr(b, "close", None),
-            "成交量": getattr(b, "volume_lots", None),
-            "成交额": getattr(b, "amount", None),
-        })
-    return pd.DataFrame(rows)
+    # 统一走 _bar_to_row：分钟周期需要 "YYYY-MM-DD HH:MM" 形式
+    return pd.DataFrame([_bar_to_row(b) for b in bars])
```

> 注意 `_bar_to_row` 定义在 `:535`，位于本函数之后 —— Python 运行期解析，无需前移。

### 5.3 `eltdx_fetchers.py` —— `fetch_adjusted_kline`（`:591-607`）

放开分钟周期 + 补分页/定点复权：

```diff
-def fetch_adjusted_kline(code: str = "", symbol: str = "", period: str = "day", adjust: str = "qfq", count: int = 800, **kwargs):
-    """复权 K 线（eltdx 源，v3.3.7 新增）。
-
-    通过 bars.get(adjust=...) 返回前复权(qfq)/后复权(hfq) K 线，
-    复权由主站计算（eltdx 3.x 起；2.x 时代为 helpers.adjusted_kline 本地计算）。
-    """
+def fetch_adjusted_kline(
+    code: str = "",
+    symbol: str = "",
+    period: str = "day",
+    adjust: str = "qfq",
+    count: int = 800,
+    all_pages: bool = False,
+    max_pages: Optional[int] = 200,
+    page_size: int = 800,
+    anchor_date: str = "",
+    **kwargs,
+):
+    """复权 K 线（eltdx 源，v3.3.7 新增；v3.3.30 支持分钟周期与分页）。
+
+    adjust: qfq/hfq/fixed_qfq/fixed_hfq（定点复权需 anchor_date）。
+    all_pages=True 时按 page_size 自动翻页并**按时间升序重排**。
+    """
     import pandas as pd
     client = _get_client()
     if client is None:
         raise RuntimeError("eltdx client not available")
     norm_code = _normalize_symbol_code(symbol, code)
     period = _normalize_period(period)
-    result = client.bars.get(norm_code, period=period, adjust=adjust, count=count)
+    result = client.bars.get(
+        norm_code,
+        period=period,
+        adjust=adjust,
+        anchor_date=anchor_date or None,
+        count=count,
+        all_pages=all_pages,
+        page_size=page_size,
+        max_pages=max_pages if all_pages else None,
+    )
     bars = getattr(result, "bars", None) or []
     if not bars:
         raise RuntimeError(f"no adjusted kline bars for {norm_code}")
+    if all_pages:
+        bars = sorted(bars, key=_bar_sort_key)
     return pd.DataFrame([_bar_to_row(b) for b in bars])
```

### 5.4 `eltdx_fetchers.py` —— `fetch_full_kline`（`:569-588`）

```diff
-def fetch_full_kline(code: str = "", symbol: str = "", period: str = "day", max_pages: int = 30, **kwargs):
+def fetch_full_kline(
+    code: str = "",
+    symbol: str = "",
+    period: str = "day",
+    adjust: str = "",
+    max_pages: Optional[int] = 200,
+    page_size: int = 800,
+    **kwargs,
+):
     """全量 K 线（eltdx 源，v3.3.7 新增）。
 
     自动分页拉取全量历史 K 线（bars.get(all_pages=True)），适合回测。
     与 fetch_historical_kline（单页 bars.get）互补。
 
     v3.3.13: eltdx 3.x 移除 client.bars.all()，改用 bars.get(all_pages=True, max_pages=...)。
+    v3.3.30: 新增 adjust / page_size；max_pages 默认由 30 放宽到 200（SDK 默认），
+             传 None 表示不设上限。**注意 1 分钟全量约 2.2 万根 ≈ 28 页**，
+             默认 200 页对日线足够、对分钟周期建议显式传 max_pages 控制耗时。
     """
     import pandas as pd
     client = _get_client()
     if client is None:
         raise RuntimeError("eltdx client not available")
     norm_code = _normalize_symbol_code(symbol, code)
-    result = client.bars.get(norm_code, period=period, all_pages=True, max_pages=max_pages)
+    period = _normalize_period(period)          # ← 与 historical_kline 口径统一
+    result = client.bars.get(
+        norm_code,
+        period=period,
+        adjust=adjust or None,
+        all_pages=True,
+        page_size=page_size,
+        max_pages=max_pages,
+    )
     bars = getattr(result, "bars", None) or []
     ...
```

> ⚠️ 这条 diff 会让 `full_kline` 的 `period="1m"` 从「真 1 分钟」变成「仍指 1 分钟」
> （因为新口径 1m = 分钟），行为不变；但 `period="m"` 由「报错」变为「月线」。
> 修掉了 §3-④ 的语义分裂。

### 5.5 `eltdx_fetchers.py` —— `fetch_security_codes` 增 `kind`

```python
# 代码表品种快捷方法（eltdx CodeApi）
_CODE_KIND_METHOD = {
    "stock": "stocks",          # a_share + b_share
    "a_share": "a_shares",
    "etf": "etfs",
    "index": "indices",
    "st": "latest_st",          # ST/*ST 名单
    "suspended": "latest_suspended",  # 停牌名单（返回 list[str]，非 SecurityCode）
}


def fetch_security_codes(market: str = "all", kind: str = "", **kwargs):
    """全市场证券代码表（eltdx 源，v3.3.7 新增；v3.3.30 增 kind 品种快捷）。

    kind 取值：'' (全部品种) / stock / a_share / etf / index / st / suspended。
    kind='stock' + market='bj' 等价于 client.codes.stocks('bj')（北交所股票表，实测 350 只）。
    market='all' 时对 sh/sz/bj 逐一取并合并。
    """
    ...
    if kind:
        method_name = _CODE_KIND_METHOD.get(str(kind).strip().lower())
        if method_name is None:
            raise ValueError(f"unsupported kind: {kind!r} (choices: {list(_CODE_KIND_METHOD)})")
        markets = ("sh", "sz", "bj") if market == "all" else (market,)
        items = []
        for m in markets:
            items.extend(getattr(client.codes, method_name)(m))
        if method_name == "latest_suspended":
            # 该 API 返回纯代码字符串列表，单独构造
            return pd.DataFrame({"代码": list(items)})
    else:
        items = client.codes.all_markets() if market == "all" else client.codes.all(market)
    ...
```

### 5.6 `eltdx_fetchers.py` —— 新增 `fetch_recent_minute`

```python
def fetch_recent_minute(code: str = "", symbol: str = "", trading_date: str = "", **kwargs):
    """近期历史分时（eltdx 源，v3.3.30 新增）。

    底层 client.minutes.recent(code, trading_date)（协议 0x0feb），
    与 fetch_minute_data（当日）/ fetch_minute_history（指定日 0x0fb4）构成三件套。
    trading_date 为空时由 SDK 取当前日期。

    ⚠️ 空结果**不抛错**：非交易日返回空 DataFrame，由工具层转 no_data。
       若在此抛 RuntimeError，独占源会被 SmartRouter 记为失败，污染源健康评分。
    """
    import pandas as pd
    client = _get_client()
    if client is None:
        raise RuntimeError("eltdx client not available")
    norm_code = _normalize_symbol_code(symbol, code)
    norm_date = (trading_date or "").replace("-", "").replace("/", "")
    result = client.minutes.recent(norm_code, norm_date or None)
    points = getattr(result, "points", None) or []
    return pd.DataFrame([{
        "时间": getattr(p, "time_label", None) or getattr(p, "time", None),
        "价格": getattr(p, "price", None),
        "均价": getattr(p, "avg_price", None),
        "成交量": getattr(p, "volume", None),
    } for p in points])
```

### 5.7 `akshare_fetchers.py` —— 备源分钟支持 + **禁止静默回落**（P0 关键）

```diff
 _AK_PERIOD_ALIASES = {
     "day": "daily", "daily": "daily", "d": "daily", "1d": "daily",
     "week": "weekly", "weekly": "weekly", "w": "weekly", "1w": "weekly",
-    "month": "monthly", "monthly": "monthly", "m": "monthly", "1m": "monthly",
+    "month": "monthly", "monthly": "monthly", "m": "monthly", "1mo": "monthly",
+    # v3.3.30: 分钟周期（与 eltdx 源口径对齐；'1m' 由「月」改为「分钟」）
+    "1m": "1m", "1min": "1m", "minute": "1m",
+    "5m": "5m", "5min": "5m", "15m": "15m", "15min": "15m",
+    "30m": "30m", "30min": "30m", "60m": "60m", "60min": "60m", "1h": "60m",
 }
```

```diff
-# 东财 push2his kline 的 klt / fqt 映射（与 akshare stock_zh_a_hist 一致）
-_EM_KLT = {"daily": "101", "weekly": "102", "monthly": "103"}
+# 东财 push2his kline 的 klt / fqt 映射（klt=1/5/15/30/60 为分钟，101/102/103 为日周月）
+_EM_KLT = {
+    "1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60",
+    "daily": "101", "weekly": "102", "monthly": "103",
+}
 _EM_FQT = {"qfq": "1", "hfq": "2", "": "0"}
+
+# 分钟周期需要 lmt（东财分钟K窗口有限，返回顺序为窗口内自早到晚）
+_EM_MINUTE_KLTS = frozenset({"1", "5", "15", "30", "60"})
+
+
+def _norm_klt(period: str) -> str:
+    """周期 → 东财 klt。**未识别周期必须抛错**。
+
+    历史事故（v3.3.30 修复）：`_EM_KLT.get(period, "101")` 会让任何未知周期
+    静默变成日 K —— 上层请求 5 分钟却拿到 1498 根日线，且全程无告警。
+    """
+    key = str(period or "").strip().lower()
+    klt = _EM_KLT.get(_AK_PERIOD_ALIASES.get(key, key))
+    if klt is None:
+        raise ValueError(f"akshare/东财 kline 不支持周期 {period!r}")
+    return klt
```

`_fetch_kline_from_em` 内（`:205` 附近）：

```diff
-        "klt": _EM_KLT.get(period, "101"),
+        "klt": _norm_klt(period),
         "fqt": _EM_FQT.get(adjust, "0"),
         "secid": f"{1 if code6.startswith('6') else 0}.{code6}",
         "beg": str(start_date or "0").replace("-", ""),
         "end": str(end_date or "20500101").replace("-", ""),
     }
+    if params["klt"] in _EM_MINUTE_KLTS:
+        # 分钟K窗口有限：lmt 请求足够大的条数，取返回尾部（最新）部分
+        params["lmt"] = str(max(int(count or 0), 1000))
```

`_fetch_kline_from_tencent` 与 `_fetch_historical_kline_direct` 入口：遇到分钟周期**显式拒绝**
（腾讯 ifzq 分钟接口协议不同，不在本次范围），避免用日线冒充：

```python
if _norm_klt(period) in _EM_MINUTE_KLTS:
    raise ValueError("腾讯 kline 源暂不支持分钟周期")
```

> `_fetch_kline_from_em` 必须继续走 `em_client.em_get`（统一防封层）——
> 实测裸请求打 `push2his` 会被 `RemoteDisconnected` 拒连。

### 5.8 `registry.py` —— 新路由 + 表头同步

```python
router.register("recent_minute", "eltdx", ef.fetch_recent_minute, priority=1, exclusive=True)
```

模块 docstring 的数据源矩阵表补 2 行（`recent_minute` 新增；`historical_kline` /
`full_kline` / `adjusted_kline` 备注「支持分钟周期」）。

### 5.9 `tools/eltdx_data.py` —— MCP 工具层

| 工具 | 改动 |
|---|---|
| `eltdx_get_kline` | docstring 改为真实支持 `1m/5m/15m/30m/60m/day/week/month/quarter/year`；`count` 加 1~800 校验（eltdx 单页上限），越界给明确报错而非 native 异常 |
| `eltdx_get_adjusted_kline` | 增 `all_pages: bool = False`、`max_pages: int = 200`，透传到路由 |
| `eltdx_get_full_kline` | 增 `adjust: str = ""`、`max_pages: int = 200`、`page_size: int = 800` |
| `eltdx_get_security_codes` | 增 `kind: str = ""`，与 `category` 并存（`kind` 优先）；docstring 给 `kind='stock', market='bj'` 示例 |
| **新增** `eltdx_get_recent_minutes(code, trading_date="")` | 对应 `recent_minute` 路由，返回点列（时间/价格/均价/成交量） |
| 返回体 | K 线类返回体增加 `"period_canonical"`（归一化后的周期名），便于上层核对没有发生静默换周期 |

---

## 6. 测试计划

| 文件 | 类型 | 断言要点 |
|---|---|---|
| `tests/test_eltdx_period_normalize.py` | 单元 | 别名表全覆盖；**`1m → 1m`（分钟）回归守卫**；`5m/15m/30m/60m/1h`；`1mo/m → month`；未识别周期抛 `ValueError` |
| `tests/test_akshare_kline_period.py` | 单元 | `_norm_klt` 全映射；**未识别周期抛错**（回归守卫：禁止 `_EM_KLT.get(period, "101")` 式静默回落）；分钟周期带 `lmt` |
| `tests/test_eltdx_fetchers_minute.py` | 单元（mock client） | `period`/`adjust`/`all_pages`/`max_pages` 正确透传；`all_pages=True` 时结果按时间升序；`fetch_full_kline` 排序保持 |
| `tests/test_eltdx_codes_kind.py` | 单元（mock client） | `kind` 分派到 `codes.stocks/a_shares/etfs/indices/latest_st`；`market='all'` 三市场合并；非法 `kind` 抛错 |
| `scripts/live_smoke.py` | 集成（网络） | 新增行：`historical_kline 5m`、`adjusted_kline 5m+adjust=qfq`、`full_kline 1m`、`recent_minute`、`security_codes kind=stock market=bj` |
| 端到端断言（关键） | — | **`eltdx_get_kline(period="5m")` 返回的首根 `date` 必须含非整点分钟**，且不得出现 `15:00:00` 单一时刻 → 可自动发现「静默返回日线」类回归 |

---

## 7. 文档同步清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `CHANGELOG.md` | v3.3.30 新增段 | `### Fixed`（2 个静默错误）、`### Added`（分钟周期/kind/recent_minute）、`### Changed (BREAKING)`（`1m` 语义） |
| `README.md` | `:176-204` 工具表 | 更新 K 线类工具描述（分钟周期）；`eltdx_get_security_codes` 补 `kind` |
| `tradex/README.md` | `:295` | 工具表补分钟/新增工具 |
| `tradex-hub-接口与安装手册.md` | `:1370-1490` | 逐个工具更新参数与示例；加「升级注意：`1m` 语义变更」提示框 |
| `docs/known-issues.md` | 新增 **H 组** | 记录「SmartRouter 静默降级 + 备源默认周期」这一类缺陷（根因、复现命令、修法、更新记录表追加一行） |
| `docs/architecture.md` | 路由矩阵 | 补 `recent_minute` |

---

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| `period="1m"` 语义变更 | 外部调用方（WorkBuddy 技能/脚本）取月线会拿到分钟 | CHANGELOG 标 BREAKING + 手册醒目提示；仓库内已 grep 确认无调用点 |
| 备源分钟窗口有限 | akshare 兜底时拿不到与 eltdx 等量历史 | 文档明示「备源仅近期」；主源 eltdx 才是全量路径 |
| `lmt` 语义未完全确认 | 备源分钟 K 线根数可能偏少/取舍位置不对 | 实现时先用 `em_get` 复测（见 §3-⑦ 待办），确认后再落测试 |
| eltdx 分钟全量页数多（1m ≈ 28 页/2.2 万根） | 单次调用耗时与主站压力上升 | 分钟周期不默认 `max_pages=None`；工具层 docstring 给建议值 |
| `_bar_to_row` 在 `fetch_historical_kline` 之后定义 | 静态检查误报 | 运行期解析，无问题；必要时把 `_bar_to_row`/`_bar_sort_key` 上移到 `fetch_*` 之前 |

**回滚**：改动集中在 4 个文件，`_PERIOD_ALIASES` / `_EM_KLT` / `_norm_klt` 三处为开关点；
如出现问题可单独回退 §5.7（备源）而不影响主源分钟能力。

---

## 9. 验收命令

```bash
# 1) 直连能力（应全部 OK）
.venv/Scripts/python.exe -c "
from eltdx import TdxClient
with TdxClient(timeout=8) as c:
    for p in ('1m','5m','15m','30m','60m'):
        s = c.bars.get('bj920185', period=p, count=5, adjust='qfq')
        print(p, s.period_name, s.count, s.bars[0].time, s.adjust_mode)
    print('bj stocks:', len(c.codes.stocks('bj')))
"

# 2) 路由层（不得再出现 akshare 日线冒充 5m）
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'tradex/src')
from tradex.data_sources import get_router, register_all_sources
register_all_sources(); r = get_router()
for dt, kw in [('historical_kline', dict(code='bj920185', period='5m', count=50)),
               ('adjusted_kline',   dict(code='bj920185', period='5m', adjust='qfq', count=50)),
               ('full_kline',       dict(code='bj920185', period='1m', max_pages=3)),
               ('security_codes',   dict(market='bj', kind='stock')),
               ('recent_minute',    dict(code='bj920185'))]:
    df, src = r.route(dt, **kw)
    print(dt, src, None if df is None else df.shape, (df.iloc[0].to_dict() if df is not None and not df.empty else None))
"

# 3) 回归
pytest -m "not network" -q
```

**验收硬标准**：`historical_kline period='5m'` 的结果行数应远小于 1498，且 `src=eltdx`、
首根时间为分钟粒度（如 `2026-09-24 14:40`），**不得出现 2020 年日期或 `15:00:00` 单一时刻**。

---

## 10. 工作量估算

| 批次 | 文件 | 行数 | 依赖 |
|---|---|---|---|
| A（P0） | `eltdx_fetchers.py` §5.1/5.2、`akshare_fetchers.py` §5.7 | ~90 + 测试 120 | — |
| B1（P1） | §5.3/5.4/5.5、`registry.py`、`tools/eltdx_data.py` | ~150 + 测试 120 | A |
| B2（P2） | §5.6 近期分时 + 工具 | ~60 | A |
| 文档 | §7 全部 | ~120 | A/B1 |

建议顺序：**A → B1 → 文档 → B2**；A 完成后先跑一次 `live_smoke.py` 确认没有引入新的源级失败。

---

## 11. 实施记录（v3.3.30，2026-09-26）

按 §5 的 diff 草案全部落地，`A → B1 → B2 → 测试 → 文档`。与草案的**差异**如下：

| 项 | 草案 | 实际实施 | 原因 |
|---|---|---|---|
| §5.2 | 只统一 `fetch_historical_kline` 行构造 | 额外抽出模块级 `_df_to_bars` / `_df_to_points` helper | 三个 K 线工具 + 两个分时工具的行构造原本各写一遍（5 处重复），抽取后新增返回字段只需改一处 |
| §5.7 | 备源分钟只补 `_EM_KLT` + `lmt` | 额外修掉**日期被截断**：`_fetch_kline_from_em` 原来无条件 `.dt.date`，会把分钟 K 的时分抹掉 | 实测发现：分钟 K 若走 `date` 会退化成「一天一根」，属同类静默错误 |
| §5.7 | 腾讯源「显式拒绝分钟」 | 同时拒绝**任何未知周期**（原来是 `dict.get(period,"day")` 落默认 day） | 与东财 `klt` 默认值是**同源缺陷**，一起修 |
| §5.5 | `kind` 分派 | `st`/`suspended` 走 `market=None`（SDK 自动跨市场），`suspended` 单独分支返回仅 `代码` 列 | `latest_suspended` 返回 `list[str]` 而非 `SecurityCode`，字段不同 |
| §5.6 | `fetch_recent_minute` | 与 `fetch_minute_history` 共用 `_minute_points_to_df` | 去重 |
| 新增 | — | K 线类返回体加 `period_canonical` | 让调用方可核对「没有发生静默换周期」 |
| 新增 | — | 版本号 3.3.29 → 3.3.30（`VERSION` + `tradex/pyproject.toml`） | 单一事实源 |

**连带修掉的数量锁测试**（原断言按旧值锁死）：
`tests/test_server.py`（工具 129→130）、`tests/test_http_server.py`（129→130）、
`tests/test_p2_cleanup_regression.py` 与 `tests/test_v3314_fixes.py`（`1m` 口径）、
`tests/test_v3314_fixes.py` 与 `tests/test_v3315_fixes.py`（数据源 102→103、数据类型 78→79）。

**新增测试**：`tradex/tests/test_kline_period_normalize.py`（85 条）、
`tradex/tests/test_eltdx_minute_fetchers.py`（36 条，含冒烟事故的 `page_size` 护栏守卫）、
`tradex/tests/test_local_data_json.py`（3 条，锁 `get_local_kline/min` 输出为**单个合法 JSON**），
合计 **124 条** —— 全部离线，含两条 P0 回归守卫
（`_norm_klt("foo")` 必须抛错、`_normalize_period("1m") == "1m"`）。

**最终验证**：
- `pytest -m "not network"`：tradex/tests **165 passed**、仓库 tests **729 passed**，
  合计 **894 passed / 0 failed**
- 真实主站（`bj920185`）：`5m`→`src=eltdx` 分钟粒度；`1m` 不再返回月线；
  `adjusted_kline 5m+qfq` 可用；`full_kline 1m max_pages=80` → 22560 根升序；
  `security_codes kind=stock market=bj` → 350 条；非法周期**两源都报错**
- **全量真实冒烟**（`live_smoke.py --out live_smoke_report.md`，7m51s，115 项）：
  本次改动引入的 2 个回归**已全部修复**，MCP 工具层 130 项**无 `FAIL`**；
  冒烟期间新出现的 2 项 `FAIL`（`industry_quotes`、`xueqiu_hot`）经复核**均为上游故障**。

### 11.1 冒烟阶段额外发现并修复（方案 §5 未预见）

> 这三个问题**都是跑了真实冒烟才暴露的** —— 单测全绿、代码评审也看不出。
> 印证了 §H-4 的教训：**改了 fetcher 签名就一定要跑一次 `live_smoke.py`**。

| # | 问题 | 根因 | 修法 |
|---|------|------|------|
| 1 | `full_kline [eltdx]` 由 `OK 6008 根/0.25s` 变 `FAIL reached max_pages/6.47s` | `live_smoke.py` 的**通用参数池按形参名精确匹配**下发，池中有 `page_size: 5`（本意服务巨潮公告）。旧代码无此形参 → 被 `**kwargs` 吞掉走默认 800；新代码接住 → 每页 5 根 → 6012 根需 1203 页 → 撞穿 200 页上限 | ① 通用池**移除** `page_size`，改在各类型 `OVERRIDES` 里显式给（`cninfo_announcement: 5`、`full_kline/adjusted_kline: 800`）；② fetcher 层新增 **`_clamp_page_size()`** 下限护栏（非法→800、`<100`→抬到 100），任何传参路径都受保护 |
| 2 | `get_local_kline` / `get_local_minute` 返回**非法 JSON** | 两处写作 `dict_to_json(info) + "\n" + df_to_json(df)`，把 `{...}` 与 `[...]` 用换行拼接。**既有缺陷** —— 此前本机读不到通达信数据恒走 `error_response` 分支故未暴露 | 合并为单对象 `dict_to_json({**info, "data": json.loads(df_to_json(df))})`，并补 `test_local_data_json.py` 守卫 |
| 3 | `recent_minute [eltdx]` 恒为 `EMPTY` | **上游问题**：`minutes.recent`（`0x0feb`）对所有标的/日期/格式一律返回 0 点，而 `today`/`history` 同条件正常 240 点 | 工具**保留**（上游修复后自动可用），数据源层空结果返回空 DF 而非抛错；`live_smoke` 单独标注避免误判 `FAIL`；结论记入 known-issues **I 组** |

**遗留（本次未做，非本次范围）**：
1. `tradex/README.md` 的分类计数表（「合计 89 个工具」「eltdx 通达信独有 5」）
   与 `docs/architecture.md` 的其余小节仍是 v3.1.4 时期内容，属**历史遗留的陈旧文档**，
   建议单独排期以源码为准重新生成。
2. 东财分钟 K 的**可用窗口长度**与 `lmt` 精确语义未用统一入口复核
   （见 §3-⑦ 待办）。当前实现是「给足够大的 `lmt` + 取尾部 `count` 根」，
   语义对齐主源，但对「最多能回溯多久」不作承诺。
