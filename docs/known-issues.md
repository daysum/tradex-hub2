# 数据源待修正清单（Known Issues）

> 记录当前**未能取到数据**的数据源、根因与建议修法，供后续排期修正。
> 首次建立：2026-09-20（v3.3.21）
> 复检命令：`python scripts/live_smoke.py --sources-only --out live_smoke_report.md`

## 怎么读这份清单

**判定口径**（由 `scripts/live_smoke.py` 自动分级）：

| 状态 | 含义 | 是否算故障 |
|------|------|-----------|
| `OK` | 取到数据 | — |
| `FAIL` | 抛异常 | ✅ **回归，必须修** |
| `EMPTY` | 无异常但结果空 | 视情况 |
| `ENV` | 缺本地数据目录 / API Key，本机注定拿不到 | ❌ 非故障 |
| `UPSTREAM` | 上游节点故障或第三方库自身问题 | ⚠️ 需评估 |
| `AKSHARE` | akshare 自身传输层被上游拒连 | ⚠️ 需评估 |

**当前状态快照**（2026-09-26，v3.3.30 全量真实冒烟，115 个源/组合项）：
**取到数据 98** ｜ `FAIL` = 2 ｜ `EMPTY` = 1 ｜ `ENV` = 2 ｜ `UPSTREAM` = 3 ｜ `AKSHARE` = 6 ｜ `UNSUPPORTED` = 3

其中 `FAIL` 2 项**均为上游故障，非代码回归**（已逐个复核，本次改动未触碰相关模块）：
- **`industry_quotes [em_push2]`** —— 东财 `push2` / `push2delay` / `push2his`
  **三台主机同时拒连**（`Connection closed abruptly`），镜像全挂 → 无源可降级。
  注意其中 `push2delay` / `push2his` 是**在本次冒烟期间新挂的**（数小时前仍是 HTTP 200）。
- **`xueqiu_hot [akshare_xueqiu_hot]`** —— 雪球接口返回非 JSON（反爬），
  **但备源 `ths_hot` 正常**（100 行 / 0.23s），工具层 `get_xueqiu_hot` 返回数据正常。

`EMPTY` 1 项 = `recent_minute [eltdx]` —— 上游 `0x0feb` 不返回数据，**属预期**（见 I 组）。

> 口径说明：v3.3.30 报告中 `industry_data` 已按 **7 个 endpoint 展开**
> （`industry_data[board_industry_name_em]` 等），故总项数由 102 增至 115；
> `AKSHARE` 计数随之上升，**不代表 akshare 传输层变差**。

> v3.3.28（行业成分股）+ v3.3.29（行业历史行情）之后，
> `industry_data` 的 **7 个 endpoint 全部有可用源**（`industry_data` 的
> 「无可用源组合」清单中，仅剩 `ths` 明确不支持 3 个 endpoint 的 `UNSUPPORTED` 记录）。

> ⚠️ 经验：**「类型级」数字会误导**。多 endpoint 类型必须下钻到
> `(类型, endpoint)` 粒度验证 —— `industry_data` 曾出现「类型级 OK 但 2 个 endpoint
> 全源皆挂」的情况（详见 B-1）。

> ⚠️ 另一条：**「源级 OK」不等于「类型可用」**。`industry_data / ths` 曾显示
> `OK (90,2)`，但那只是默认 endpoint 的结果 —— 同类型下其它 endpoint 可能全源皆挂。
>
> ⚠️ 经验：**「类型级 OK」会低估问题**。多 endpoint 类型必须下钻到
> `(类型, endpoint)` 粒度验证 —— `industry_data` 曾出现「类型级 OK 但 2 个 endpoint
> 全源皆挂」的情况（详见 B-1）。

> ⚠️ **数量会浮动 ±1~2**：A/B 两组里的 akshare 源是**时好时坏**的
> （同一类型不同次运行可能一次 OK 一次失败，例如 `historical_kline`、
> `minute_data`）。这是「上游间歇性拒连」的典型表现，不代表代码有问题，
> 但也说明**这些源不可靠**，值得修。
>
> ⚠️ 重要：**`FAIL = 0` 不代表没有数据源不可用**。下面 A/B 两组（8 项）虽然被
> 归到 `AKSHARE`/`UPSTREAM`（因为根因在上游或第三方库，不是本项目代码写错），
> 但它们**确实拿不到数据**，仍需修正。

---

## A. `push2his` 日K路由无可用镜像（**已通过换源解决**）

**状态**：✅ 受影响的数据源已于 v3.3.22 修复（见下）。
本节保留，因为它记录了一条**必须遵守的约束**：`push2delay` 不是 `push2his` 的全功能镜像。

**原始影响**：1 个我们自己的源 + 3 个 akshare 源（后者根因见 B 组）

| 数据类型 | 源 | 现象 |
|---------|----|------|
| `index_daily_amount` | akshare（`_fetch_index_daily_from_push2his`，**我们自己直连**）| `RuntimeError: push2his request failed` / `kline empty` |
| `historical_kline` | akshare | `ConnectionError: Remote end closed connection` |
| `minute_data` | akshare | 同上 |
| `industry_data` | akshare | 同上 |

**根因**：东财 push2 节点集群正在降级。实测 2026-09-20：

| 主机 | 状态 |
|------|------|
| `push2.eastmoney.com` | ❌ 连接被拒（`clist` 路由）|
| `push2his.eastmoney.com` | ❌ 连接被拒（**日K `kline/get` 路由**，10/10 失败）|
| `70.push2.eastmoney.com` / `17.push2.eastmoney.com` | ❌ 连接被拒（akshare 用的子域）|
| `push2delay.eastmoney.com` | ✅ 可用，但**只是部分功能镜像**（见下）|

**⚠️ 约束：`push2delay` 不能当 `push2his` 的镜像。**

曾一度把 `push2his` 映到 `push2delay`，**实测载荷不等价，已撤回**：

| 路径 | push2his | push2delay | 能否镜像 |
|------|----------|------------|---------|
| `kline/get` **日K**（`klt=101`）| 连接被拒 | `rc=0` 但 **`klines: []`**（它只服务 `klt=1` 分时）| ❌ 无数据 |
| `fflow/daykline/get`（日线资金流）| `rc=0` **5 条** | `rc=0` **仅 1 条** | ❌ **静默降级**（5 条→1 条）|
| `fflow/kline/get`（`klt=1`）| `rc=0` 0 条 | `rc=0` 100 条 | — |
| `clist/get` / `ulist.np/get` / `slist/get` | ❌ | ✅ 有数据 | — |

> 教训：**验证镜像必须看载荷，不能只看 HTTP 200 / rc=0**。
> 把 `fflow/daykline` 映过去会让 5 条悄悄变成 1 条 —— **静默降级数据比直接失败更糟**。
> 因此 `_EM_HOST_MIRRORS` 只保留**载荷验证过等价**的 `push2`。

**已实施的修法（v3.3.22 / v3.3.23）**：

- `index_daily_amount` 的备源1 由 akshare 的 `stock_zh_index_daily_tx`
  （**按年份逐年翻页** → 上证指数 37 次请求 ≈ 21s，超 12s 路由超时故必然失败）
  改为本项目直连腾讯 `proxy.finance.qq.com`（**单次请求 0.12s**）。
  修复后 `fetch_index_daily_amount(symbol="sh000001", days=5)` → 返回 5 条。
- v3.3.23 加**失败熔断**：`push2his` 该路径失败后进入 60s 冷却，期间直接返回 `None`
  让调用方走备源，不再白等 3 次退避。实测 `index_daily_amount`
  **6.18s → 0.18s（快 34 倍）**。熔断按 **(主机,路径)** 计，
  因此不会误伤同主机上仍健康的 `fflow/daykline`。
- 3 个 akshare 源仍未修 → 见 B 组（根因是 akshare 绕过防封层，与镜像无关）。

**遗留**：`push2his` 恢复后主源会自动重新生效（它仍是第一优先，因为含成交额）——
熔断冷却到期会重探，无需人工干预；也可用 `reset_host_health()` 立即重探。

---

## B. akshare 自身传输层不走统一防封层 —— ✅ 已全部解决

**状态**：5 个受影响源（`hot_rank` v3.3.24、`minute_data` v3.3.25、
`industry_comparison` v3.3.26、`historical_kline` v3.3.27、`industry_data` v3.3.28/29）
**全部修复完毕**，本节闭环。保留下方 **B-1** 是因为它记录了两条重要的排查经验：
「多 endpoint 类型必须逐 endpoint 验证」与「板块名要跨分类检索 + 翻页」。

| 数据类型 | 源 | 状态 |
|---------|----|------|
| `industry_data` | akshare | ✅ 见下方 **B-1**（7 个 endpoint 全部有可用源）|

### B-1 `industry_data`：按 endpoint 分裂，2 个 endpoint 无可用源

`industry_data` 是**多 endpoint 数据类型**（7 个），主源 akshare 与备源 ths 的覆盖范围
**不一致**。实测矩阵（2026-09-20）：

| endpoint | 用途 | akshare(主源) | ths(备源) | 结论 |
|----------|------|--------------|-----------|------|
| `board_industry_name_em` | 行业板块列表 | ❌ ConnectionError | ✅ (90,2) | 备源兜住 ✓ |
| `board_industry_name_ths` | 行业板块列表(同花顺) | ✅ (90,2) | ✅ (90,2) | 双源可用 ✓ |
| `board_concept_name_em` | 概念板块列表 | ❌ ConnectionError | ✅ (375,2) | 备源兜住 ✓ |
| `board_concept_name_ths` | 概念板块列表(同花顺) | ✅ (375,2) | ✅ (375,2) | 双源可用 ✓ |
| `board_industry_cons_em` | 行业成分股 | ❌ ConnectionError | ❌ 不支持 | ✅ **v3.3.28 已修**（本项目直连）|
| `board_industry_hist_em` | 行业历史行情 | ❌ ConnectionError | ❌ 不支持 | ✅ **v3.3.29 已修**（同花顺板块K线）|
| `sector_fund_flow_rank` | 板块资金流排行 | ✅ (100,16) | ❌ 不支持 | 主源可用 ✓ |

> ✅ **7 个 endpoint 至此全部有可用源**，本节闭环。

> ⚠️ **别只看「类型级」的 OK**：冒烟报告里 `industry_data / ths` 显示 `OK (90,2)`，
> 但那只是**默认 endpoint**（板块列表）的结果。同一份报告里
> `get_industry_stocks` / `get_competitors` / `get_industry_pe` 三个工具都在报
> 「All sources for 'industry_data' failed」—— 因为它们要的是
> `board_industry_cons_em` / `board_industry_hist_em`，而这两个 endpoint
> **两个源都不覆盖**。多 endpoint 数据类型必须**逐 endpoint 验证**。

**受影响的工具**：
- ~~`get_industry_stocks`（行业成分股）、`get_competitors`（竞争对手）~~
  → **v3.3.28 已修**（改为本项目直连，实测 `白酒` 返回 45 只）
- ~~`get_industry_pe`（行业历史行情）~~ → **v3.3.29 已修**（改用同花顺板块K线）

**修复可行性（已实测）**：

- ✅ **`board_industry_cons_em` 已修（v3.3.28）**。akshare 打的是
  `29.push2.eastmoney.com/api/qt/clist/get`（数字前缀子域 + clist 路径），
  改用 `push2.eastmoney.com` + `em_get`（走 push2delay 镜像）自建。
  排查中还发现 akshare 原版**两个隐藏缺陷**（即便传输正常也会失败）：
  ① 只搜行业板块、**漏了概念板块**（`白酒`/`新能源车` 只存在于概念板块）；
  ② 板块列表**只取第一页**（服务端 `pz` 硬顶 100，而行业 496 / 概念 504 个）。
- ✅ **`board_industry_hist_em` 已修（v3.3.29）**。东财板块日K路由**整条链路故障**
  （所有 `push2his*` 主机连接被拒；`push2delay` 认得出板块但 `klines` 为空），
  故**换数据来源** —— 实测**同花顺板块K线免鉴权可用**：
  `d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js`（`01`=日K/`11`=周K/`21`=月K，
  行业代码 `881xxx`，白酒=881273）。实现放在 `ths_fetchers`。
  降级顺序为「同花顺优先 → 东财兜底」：东财放前面会先做昂贵的 BK 解析再失败
  （首调 24s 超时），同花顺 1.5s。**同花顺不提供换手率**（该列恒空）；
  东财恢复后换回顺序即可拿回该列。

**根因**：akshare 用**裸 `requests`**（无 TLS 指纹伪装、无节流、无重试），
且请求的是上面那批**已降级的 push2 节点**。本项目自己的加固请求打同一批主机
全部正常。

**决定性证据**（同一时刻、同一 URL）：

```
https://push2.eastmoney.com/api/qt/ulist.np/get
  裸 requests         -> ERR ConnectionError
  裸 curl_cffi chrome120 -> ERR Connection closed abruptly
  em_get（统一入口）    -> 200  ← 镜像降级生效
```

也就是说：**这些源本可以正常工作，只是 akshare 绕过了我们的防封层。**

**建议修法**（二选一，推荐第 1 种）：

1. **改为自己直连**（推荐）。项目里已有先例 —— `fetch_fund_hold_direct`
   就是因为 akshare 列错位而改成直取东财的。akshare 实际请求的接口都能直连：
   - `stock_zh_a_hist` → `push2his/api/qt/stock/kline/get`
   - `stock_intraday_em` → `70.push2/api/qt/stock/...`
   - `stock_sector_fund_flow_rank` → `push2/api/qt/...`
   - `stock_hot_rank_em` → `emappdata` + `push2/api/qt/ulist.np/get`

2. ~~给 akshare 的 `requests` 打全局补丁~~（**不推荐**）。
   篡改进程级 `requests.Session.request` 有全局副作用，项目历史上已因此
   删掉过一个源（`fetch_index_news_sentiment`），并有测试守着
   （`test_v3315_fixes.py::test_index_news_sentiment_source_retired`）。

**附带风险（重要）**：akshare 的流量**不经过统一防封层**，因此
**仍会消耗本机 IP 的东财配额**。一旦 akshare 把 IP 刷进风控，
本项目自己那些「节流良好」的请求会一起失败。这是一个**结构性的 IP 连坐风险**，
建议按上面第 1 种方案逐步消除。

---

## C. 中优先级：上游接口 / 域名失效

### C-1 `financial_stmt` / akshare —— 上游域名 DNS 已不解析

- **现象**：`ConnectionError: HTTPSConnectionPool(host='emweb.securities.eastmoney.com')` `NameResolutionError`
- **根因**：`emweb.securities.eastmoney.com` **域名已不解析**（`getaddrinfo failed`）；
  akshare 的财务报表接口打的就是这个域名
- **建议**：先确认东财是否更换了 F10 域名；若无，改用
  `datacenter-web.eastmoney.com/api/data/v1/get`（本项目已验证可用）自建财务报表接口
- **备注**：`financial_stmt` 主源是 akshare，备源是 sina，目前实际由 sina 兜底

### C-2 `company_info` / akshare + cninfo —— akshare 内部 KeyError

- **现象**：`KeyError: 'count'`
- **根因**：两个源都转调 akshare：
  - `company_info`(akshare) → `ak.stock_individual_info_em`
  - `company_info`(cninfo) → `ak.stock_profile_cninfo` → 打 `webapi.cninfo.com.cn`，
    返回体无 `count` 字段，akshare 内部直接 `data_json["count"]` 抛错
- **建议**：改用本项目已验证可用的 `www.cninfo.com.cn/new/information/topSearch/query`
  （走 `get_client("cninfo")`）自建公司概况，或另寻独立源

> ⚠️ **值得复核的设计问题**：`company_info` 虽然注册了两个源（akshare / cninfo），
> 但**两者都经过 akshare**，属于「传输层同源」。akshare 一挂两个一起挂，
> **不构成真正的独立备源**。这与 v3.3.14「为单源数据类型补独立备源」的目标相悖，
> 建议连同类数据一起复核。

---

## D. 按设计停用（非故障，保留记录）

| 数据类型 | 源 | 说明 |
|---------|----|------|
| `fund_hold` | `akshare_fund_hold` | akshare `stock_report_fund_hold` **列已错位**（上游字段顺序改版 + akshare 按位置硬编码列名），会返回**错标数据**。已改由 `em_zlsj_direct` 直取东财；该源故意保留抛错，避免被当作兜底返回错误数据 |

---

## E. 环境依赖（本机无法验证，非故障）

这些源需要本机环境或密钥，**换到具备条件的机器上应能正常工作**，
不应据此判断代码有问题：

| 数据类型 | 源 | 依赖 |
|---------|----|------|
| `local_kline` | `tdx_local` | 本机通达信 vipdoc 数据目录（`tdxdir`）|
| `local_minute` | `tdx_local` | 同上 |
| `wencai_query` | `pywencai` | `pywencai` + Node.js + **未被问财封禁的出口 IP**（见下）|

> ⚠️ **`wencai_query`（pywencai）本机不可用的根因是出口 IP 被封，不是代码问题。**
>
> 2026-09-22 实测：本机出口 IP `120.230.123.119` 访问 `www.iwencai.com` 被**整体拒绝** ——
> **连首页都 403**（`Nginx forbidden. request info: 120.230.123.119`），
> 业务接口 `.../customized/chart/get-robot-data` 同样 403（openresty）。
>
> 已排除：换 `https` 无效、换 Chrome UA 无效 → 与协议、与指纹均无关，是 **IP 层封禁**。
>
> 佐证：**同一台机器**访问付费网关 `openapi.iwencai.com` 完全正常（见 G 组）——
> 说明只有 `www.iwencai.com` 这个域被墙，不是整机出网故障。
>
> **结论：给 pywencai 配任何 API Key 都无效 —— pywencai 根本不使用 Key，它是爬虫库**
> （靠 PyExecJS 调 Node.js 生成 `hexin-v` 指纹）。恢复条件只有两个：
> 问财解封该 IP，或更换出口网络。注意项目铁律：**数据源流量禁止走代理**
> （见 `tradex/src/tradex/config.py` 顶部注释），所以"挂个代理"不是可接受的方案。
>
> 另：`pywencai` 内部的 `while_do` 会吞掉异常并返回 `None`，外层再抛
> `AttributeError: 'NoneType' object has no attribute 'get'`。本项目侧已做两处处理：
> 把该异常翻译成可定位提示；并把重试次数从默认 10 压到 2 —— 默认 10 次 × 每次拉起
> Node.js 必然超过 SmartRouter 的 12s 路由超时，只会暴露成无信息量的 `timeout(12.0s)`，
> 把真实原因（403）整个盖掉。

> 注：`wencai_news` 原先列在本组，2026-09-22 已修好并实测可用，见 F 组。

---

## F. 已修复（保留记录，避免重复排查）

| 日期 | 项 | 修复 |
|------|----|------|
| 2026-09-26 | `get_local_kline` / `get_local_minute` 返回**非法 JSON** | v3.3.30：两处均写作 `dict_to_json(info) + "\n" + df_to_json(df)`，把 `{...}`（元信息）与 `[...]`（数据行）用换行拼成一个字符串 → **不是合法 JSON**。此前本机读不到通达信 vipdoc 时恒走 `error_response` 分支故未暴露；本轮本机有数据后由 `live_smoke` 判出。改为单对象 `dict_to_json({**info, "data": json.loads(df_to_json(df))})`，并新增 `tests/test_local_data_json.py` 守卫 |
| 2026-09-22 | `wencai_news` / `iwencai_openapi` 恒返回空表（**四个缺陷叠加，缺一不可用**） | 全在 `data_sources/wencai_fetchers.py`：① **缺 `X-Claw-*` 网关请求头** → `401 当前 Skill 版本过低`；② `app_id` 误写 `tradex`（该字段是**固定值 `AIME_SKILL`**）→ 被 Sentinel 流控挡成 `200 + status_code=4 FlowException + total=0`，现象酷似限流；③ 按 `result["data"]["items"]` 解析，但 `data` 实为**数组** → `AttributeError`；④ 读不存在的 `item["source"]` → 「来源」列恒为空串（真实来源在 `extra` 的 dict 内）。另：把 `status_code != 0` 纳入显式抛错（否则 FlowException 会被当成"零命中"静默返回空表）；`发布时间` 改取可读的 `publish_date`（原 `publish_time` 是 unix 秒字符串）。契约与复现命令见 **G 组** |
| 2026-09-20 | `board_industry_hist_em`（行业历史行情）无可用源 | v3.3.29 **换数据来源**：东财板块日K路由整条链路故障 → 改用**同花顺板块K线**（`d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js`，免鉴权，`01`日/`11`周/`21`月）。降级顺序「同花顺优先 → 东财兜底」（东财优先会先跑昂贵的 BK 解析再失败，首调 24s → 1.5s）。`get_industry_pe` 恢复 |
| 2026-09-20 | `board_industry_cons_em`（行业成分股）无可用源 | v3.3.28 改本项目直连（`push2delay` 的 clist 经 `em_get` 走镜像）。**并修掉 akshare 原版两个隐藏缺陷**：只搜行业板块漏了概念板块（`白酒`只存在于概念）、板块列表只取第一页（`pz` 硬顶 100）。`get_industry_stocks` / `get_competitors` 恢复 |
| 2026-09-20 | `historical_kline` 备源恒失败 | v3.3.27 改本项目直连两层：① 东财 `push2his` kline（走 `em_get`）② **腾讯 `proxy.finance.qq.com` 兜底**。**注意**：东财日K路由**整体不可用**（`push2his` 连接被拒、`push2delay` 该路径 `klines` 为空），只换直连东财仍取不到数，腾讯兜底是关键。字段已用主源 eltdx 逐字段交叉验证 |
| 2026-09-20 | `industry_comparison` 备源恒失败 **+ 一处被掩盖的数据错误** | v3.3.26 改本项目直连（akshare 打的是统一入口已覆盖+已配镜像的 `push2/clist/get`）。**更严重的是**：旧代码读 akshare 的 `板块`/`涨跌幅`/`领涨股票` 三列，但这三个列名**在 akshare 输出里都不存在**（真名 `名称`/`今日涨跌幅`/`今日主力净流入最大股`）→ 即便传输没坏也只会返回 `name="None"`/`change_pct=0.0`/`leader="None"`；传输失败反而掩盖了它。直连版按字段名取，并补齐真实涨跌家数 |
| 2026-09-20 | `minute_data` 备源 `akshare` 恒失败 | v3.3.25 根因两层：akshare 打 `70.push2`（**无镜像**）+ 用 `/details/sse`（**SSE 长连接**：非流式必超时；即便流式 break，`close()` 排空响应体独占 8.6s）。改用 `push2.eastmoney.com`（有镜像）的 **`/details/get` 非流式变体** → 0.28s，稳态 1.25s（流式版 10.6s） |
| 2026-09-20 | `hot_rank` 主源 `akshare_hot_rank` 恒失败 | v3.3.24 改为**本项目直连**：akshare 分两步（emappdata POST **可达** + push2 ulist GET **被拒连**），而同一 URL 经 `em_get` 可正常取到（走镜像）。新增 `em_post` 统一 POST 入口。**按代码 join 两个列表**（防静默错位）。修复后主源恢复 100×6，路由重新命中主源 |
| 2026-09-20 | 失败源每次都要白等满重试退避（`index_daily_amount` ≈6.5s） | v3.3.23 失败熔断：按 `(主机,路径)` 冷却 60s，期间直接返回 `None` 走备源 → **6.18s → 0.18s** |
| 2026-09-20 | `index_daily_amount` 三层降级全失败 | v3.3.22 备源1 由 akshare 按年翻页版（37 请求 ≈21s，超 12s 路由超时）改为**本项目直连腾讯** `proxy.finance.qq.com`（单次请求 0.12s）|
| 2026-09-20 | `industry_comparison` / `em_push2` 报「东财返回空数据」 | v3.3.21 主机镜像降级（push2 → push2delay）|
| 2026-09-20 | `limit_up_board` / `em_client.fetch_stock_boards` / `em_push2_fund_flow` 全线失效 | 同上（收敛在 `em_get`，6 处调用点自动受益）|
| 2026-09-18 | 巨潮 `cninfo.com.cn` 403 Forbidden、公告恒为空 | v3.3.20 该源固定走 `NO_IMPERSONATE`（巨潮 WAF 封禁常见浏览器指纹）|
| 2026-09-18 | 同花顺 / 财联社 / 新浪 / 问财 / 巨潮 裸请求无节流 | v3.3.20 收敛到 `get_client(...)` 统一层 |

---

## G. 问财 OpenAPI 网关契约（**必须遵守**）

**状态**：✅ 已于 2026-09-22 修入 `wencai_news`（见 F 组）。
本节保留，因为它记录了两条**极易误判**的约束 —— **症状的措辞与真实成因完全对不上**，
不写下来每个新人都要重排一遍。

### G-1 两个症状，两个成因（都与 key 无关）

| 症状 | 真实成因 | 与之**无关**的东西 |
|------|---------|-------------------|
| `HTTP 401`<br>`当前 Skill 版本过低，本次请求无法执行。请前往[问财 SkillHub](https://www.iwencai.com/skillhub)下载最新版本后重试。` | **缺 `X-Claw-*` 请求头** | ❌ key 是否有效<br>❌ Skill 是否真的"旧"<br>❌ 换 key（无用） |
| `HTTP 200` + `status_code: 4`<br>`status_msg: com.alibaba.csp.sentinel.slots.block.flow.FlowException`<br>`total: 0` | 请求体 `app_id` **不是固定值 `AIME_SKILL`** | ❌ 限流<br>❌ 配额用尽<br>❌ 退避重试（无用） |

**隔离实验**（同一个 key，每次只动一个变量）：

| `X-Claw-*` | `app_id` | 结果 |
|-----------|----------|------|
| ✅ 带 | `AIME_SKILL` | ✅ `200` / `status_code=0` / `status_msg=OK`，正常返回数据 |
| ✅ 带 | `tradex` | ❌ `200` / `status_code=4` FlowException / `total=0` |
| ❌ 不带 | `AIME_SKILL` | ❌ `401 当前 Skill 版本过低` |

> 关键教训：**「Skill 版本过低」这句提示是有误导性的**。它跟 key、跟版本
> 都没有关系，纯粹是"请求头不全"。看到它不要往 key / 版本方向排查。

### G-2 必须携带的请求头

```
Authorization:         Bearer <IWENCAI_API_KEY>
Content-Type:          application/json
X-Claw-Call-Type:      normal | retry
X-Claw-Skill-Id:       <技能标识，见下表>
X-Claw-Skill-Version:  1.0.0
X-Claw-Plugin-Id:      none
X-Claw-Plugin-Version: none
X-Claw-Trace-Id:       <每次请求**新生成**，必须 64 字符>
```

`Trace-Id` 用 `secrets.token_hex(32)` 恰好 64 位十六进制。

`channel` → `X-Claw-Skill-Id` 映射：

| channel | `X-Claw-Skill-Id` |
|---------|-------------------|
| `news` | `news-search` |
| `announcement` | `announcement-search` |
| `report` | `report-search` |

> 实测网关**不严格校验** skill-id 与 channel 的对应关系（用 `news-search` 打
> `report` 同样返回 200），但仍应按下表填 —— 这是技能登记口径，不是自由字段。

规范原文见 workspace 技能文档（**这份文档是权威来源**）：
`.agents/skills/news-search/SKILL.md`、`.agents/skills/announcement-search/SKILL.md`、
`.agents/skills/hithink-market-query/SKILL.md`。

### G-3 响应结构的三个陷阱

`/v1/comprehensive/search` 的真实结构（实测）：

```json
{
  "status_code": 0,          // 0=OK；4=Sentinel 流控。必须检查！
  "status_msg": "OK",
  "total": 15,
  "data": [ { ... }, ... ]   // ← 是**数组**，不是 {"items": [...]}
}
```

| 陷阱 | 后果 |
|------|------|
| `data` 是**数组** | 写成 `result["data"]["items"]` 直接 `AttributeError`（本项目曾如此） |
| **没有**顶层 `source` 字段 | 写成 `item.get("source")` 恒为空串（本项目曾如此） |
| `extra` 是 **dict** | 网关以 JSON 对象返回（`{"publish_source": "微信公众平台", "real_publish_source": "昆仑策研究院", ...}`），**不是** dict 字面量字符串。按字符串处理会静默退化到无意义的 `data_source` 枚举值（如 `NEWS_VECTOR`，本项目曾如此） |

另：`publish_time` 是 **unix 秒字符串**（`"1789509600"`），可读时间在 `publish_date`
（`"2026-09-16 06:00:00"`）。取前者只会得到一坨数字。

### G-4 复现 / 回归命令

```bash
python - <<'PY'
import json, os, secrets, urllib.request
key = os.environ["IWENCAI_API_KEY"]
req = urllib.request.Request(
    "https://openapi.iwencai.com/v1/comprehensive/search",
    data=json.dumps({"channels": ["news"], "app_id": "AIME_SKILL",
                     "query": "人工智能"}).encode(),
    headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "X-Claw-Call-Type": "normal", "X-Claw-Skill-Id": "news-search",
        "X-Claw-Skill-Version": "1.0.0", "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none", "X-Claw-Trace-Id": secrets.token_hex(32),
    }, method="POST")
print(json.loads(urllib.request.urlopen(req, timeout=30).read())["status_code"])
PY
# 期望输出：0      若为 4 = app_id 不对；若 HTTP 401 = 缺 X-Claw-* 头
```

---

## H. 「静默降级 + 参数默认值」组合会产出**错数据**（✅ v3.3.30 已修复，机制保留记录）

**这一组不是「取不到数据」，而是「取到了错误的数据且不报错」—— 比源挂掉更危险。**
源挂掉会被 `live_smoke.py` 判 `FAIL` 并告警；这类缺陷**所有监控都是绿的**。

### H-1 `historical_kline` 请求分钟周期返回日线

| 项目 | 内容 |
|------|------|
| 现象 | 请求 `period="5m"`，返回 1498 根**日线**（首行 `2020-07-27`），无任何告警 |
| 触发链路 | ① 主源 `eltdx_fetchers._normalize_period` 只认 day/week/month → 抛 `ValueError`；② SmartRouter 按设计降级 akshare；③ 备源 `_fetch_kline_from_em` 参数构造为 `_EM_KLT.get(period, "101")` —— **未识别周期一律落默认值 `101`=日K** |
| 根因定位 | `akshare_fetchers.py` 的 `_EM_KLT.get(period, "101")`（默认值兜底 = 静默换周期） |
| 复现 | `historical_kline period=5m` → 观察 `src` 字段：修复前是 `akshare`、行数 ~1498 且日期是 2020 年；修复后是 `eltdx`、行数≈count 且日期带时分 |
| 修法 | 主源补齐分钟周期；备源新增 `_norm_klt()`，**未识别周期直接抛 `ValueError`**（宁可报错不给错数据），并补全分钟 `klt=1/5/15/30/60` |
| 同类点 | 腾讯备源 `ifzq` 的 `per` 参数只认 day/week/month，此前 `{"daily":"day",...}.get(period,"day")` **同样落默认值 day** —— 已改为显式拒绝分钟/未知周期 |

### H-2 `period="1m"` 返回月线（别名表口径冲突）

| 项目 | 内容 |
|------|------|
| 现象 | 请求 `period="1m"` 返回**月线**（如 `2022-08-31`） |
| 根因 | 两个源的别名表都写 `"1m": "month"`（备源 `"1m": "monthly"`）；而 `fetch_full_kline` 不归一化 → **同一参数名在两个工具里语义相反**（`eltdx_get_kline` 是月线、`eltdx_get_full_kline` 是 1 分钟） |
| 修法 | v3.3.30 **BREAKING**：`1m` 统一为「1 分钟」（对齐 eltdx 官方口径），月线改用 `month`/`monthly`/`1mo`；短别名 `m` 保留为月线以兼容旧调用 |
| 影响面 | 仓库内已 grep 确认无 `period="1m"` 调用点；外部脚本若用 `1m` 取月线需改 `1mo` |

### H-3 一般化教训（**新写源时必看**）

1. **禁止 `dict.get(key, 默认值)` 兜周期/枚举类参数。** 默认值必须落在
   「语义等价」的值上；周期、复权、市场、品种这类参数没有安全默认值 → 一律抛错。
2. **静默降级链必须校验「降级后语义是否一致」。** SmartRouter 的设计是
   「主源失败→备源」，但若备源对同一参数的**解释不同**，降级就变成了数据污染。
   新增备源时请对照主源逐参数核对值域。
3. **别名表是共享语义，改动要全库同查。** `1m` 这类短别名在两个 fetcher 里各写一份，
   极易分叉（本次就是两份都在、且都与 eltdx 官方口径不符）。
4. **回归守卫写法**：本次新增的守卫断言是
   `_norm_klt("foo")` 必须抛错、`_normalize_period("1m") == "1m"`。
   若有人改回「默认值兜底」，测试立刻红。

### H-4 新增形参会被「通用参数池」撞中（✅ v3.3.30 已加护栏）

**现象**：给 `fetch_full_kline` 新增 `page_size` 形参后，`live_smoke.py` 的
`full_kline [eltdx]` 由 `OK 6008 根 / 0.25s` 变 `FAIL reached max_pages / 6.47s`，
工具层 `eltdx_get_full_kline` 同步报错。

**根因**：`live_smoke.py` 的通用参数池（`COMMON_PARAMS`）是**按形参名精确匹配**下发的，
里面带着 `"page_size": 5`（本意是服务巨潮公告 `cninfo_direct` 的每页 5 条）。旧代码
没有 `page_size` 形参 → 该键被 `**kwargs` 吞掉、走 SDK 默认 800；新代码把它接住 →
**每页 5 根** → 6012 根需 1203 页 → 撞穿 `max_pages=200`。

**教训**：
1. `live_smoke.py` 的参数池是**跨数据类型的共享字典**，新增通用名形参
   （`count` / `size` / `limit` / `page_size` / `days` …）前，先查该名字在池中的既有取值；
   语义冲突时在该类型的 `OVERRIDES` 里显式覆盖，**不要去删共享池的值**（会连累其他类型）。
2. **fetcher 层要有下限护栏**，不能假设调用方给的页长合理。
   本次新增 `_clamp_page_size()`：非法值 → 800，`< 100` → 抬到 100。
   护栏放在 fetcher 而非仅工具层，任何传参路径（工具/路由/脚本/第三方）都受保护。
3. 这类"新参数撞旧参数池"的失败**只会在真网络冒烟时暴露**，单测全绿说明不了问题 ——
   **改了 fetcher 签名就一定要跑一次 `live_smoke.py`**。

---

## I. `minutes.recent`（0x0feb）主站不返回数据（**上游问题，暂无解**）

**结论**：`eltdx_get_recent_minutes` 对应协议命令 `0x0feb`，**当前主站对该命令一律返回空**。

实测矩阵（2026-09-26，eltdx 3.2.2，直连主站）：

| 维度 | 取值 | 结果 |
|------|------|------|
| 标的 | `sh600519` / `sz000001` / `bj920185` | 全部 **0 点** |
| 日期 | 当日、前 1/2/3 个交易日、`2026-09-18` | 全部 **0 点** |
| 日期格式 | `20260918` / `2026-09-18` | 全部 **0 点** |
| 对照组 | 同标的同日期跑 `minutes.today` / `minutes.history` | 均**正常 240 点** |

**排除项**：不是参数格式问题（两种格式都试）、不是标的范围问题（沪深京都试）、
不是交易日问题（对照组同日期有数据）。SDK 侧 `0x0FEB` 标记 `required_for_1_0=False`
（非必需命令），报文构造在 Rust native 层，Python 层无法进一步诊断。

**当前处理**：工具保留（上游修复后自动可用），但：
- 数据源层对空结果**返回空 DataFrame 而非抛错**（它是独占源，抛错会污染源健康评分）；
- `live_smoke.py` 对 `recent_minute` 单独标注，避免把「上游无数据」误判为 `FAIL`。

**已知影响**：该工具目前**不承诺有数据**。需要历史分时请用 `eltdx_get_minute_history`（指定日，正常），需要当日分时用 `eltdx_get_minutes`。

---

## J. tdxdata（Phase 1）已知限制

**定位**：tdxdata 是通达信手机版 7615 数据中心，**不是 eltdx 的替代品**。
它适合低频/按需查询，不适合高频轮询。

| 限制 | 影响 / 处理 |
|------|-------------|
| 单命令约 **2.2 秒**匀速节流，限流按来源 IP 计 | 工具可能耗时数秒到十几秒；禁止并发/高频；`tdx_status` 可看 `rate_stats` |
| 无五档盘口 | 盘口继续用 `eltdx_get_depth` |
| 逐笔无买卖方向、无笔数、仅当日 | 逐笔继续用 eltdx（有 `side`）；tdxdata 不做逐笔主源 |
| 无历史逐笔 | 只能当日 |
| 无 3 分钟/年线 | 不支持；不要用参数默认值兜底 |
| 无港股/期货/期权/外汇行情 | 这些品种的 K 线/分时/五档拿不到 |
| 421 个产品中仅 **52 个**有标准列义 | 其余返回 `N001...` 原始列；工具 `column_note` 会提示，不猜列义 |
| `get_boards("all")` 约 24 秒 | 工具超时放宽到 60s；建议按 kind 查询并缓存 |
| `get_universe` 全量约 158 秒 | **未**用于 `get_stock_list` 备源 |
| `get_intraday_samples` 个股未复核 | 标为实验性，仅指数 999999 有实证 |
| 实时涨跌家数回包无日期 | `provenance.as_of` 恒 `null`，并带 `as_of_note` |
| 与 eltdx 同属通达信生态 | 对 eltdx 独占类型不构成跨上游备源；对东财/同花顺才是独立备源 |

**Phase 2 备源字段说明**：

- `historical_kline` 的 tdxdata 备源输出 12 列与 akshare 对齐，但 **`换手率` 恒为 `null`**
  （上游不提供），**首根 `振幅/涨跌幅/涨跌额` 为 `null`**（无前收）；其余行由上一根收盘自算。
- `minute_data` 输出 `时间/价格/均价/成交量`，只支持当日。
- `market_breadth` 输出 `上涨/下跌/平盘/涨停/跌停`；回包无日期，`as_of` 恒 `null`。
- 未知周期/复权一律抛错（年线、3 分钟、`quarter` 以外的写法不会静默回落日线）。

**Phase 3 备源说明**：

- `finance_report`：tdxdata 备源返回**标准英文字段**（`report_period` / `total_assets_yuan` …），
  与 eltdx 的 T 编码字段不同；工具输出里 `source`/`note` 会标明。
- `today_ticks`：tdxdata 逐笔**无买卖方向**（`side=unknown`）、无开盘撮合标记；
  `成交额` 由 `price × 手数 × 100` 推算；**最多 1000 笔/次**（上游约 2.2s/100 笔）。
- `dividend_financing` **故意不接 tdxdata**：`get_xdxr` 是「IPO/融资累计统计」（1 行），
  不是逐次分红/除权事件表；接进来会造成语义错配。继续用 eltdx/akshare 分红源。

**Phase 3 深水区（v3.5.0）说明**：

- `tdx_get_f10` / `tdx_get_finance` 只接 tdxdata 已有标准字段的 section/kind；
  `fhrz` 是 IPO/融资累计统计，**不是**分红事件表。
- `get_finance(kind="cwfxxq")` 上游多 ResultSet、列义未定 → 抛 `NotSupportedError`，不猜列名。
- `tdx_get_capital_flow_rank` 只支持全市场排行（上游固定前 20）；单代码资金流用
  `get_money_flow`；`price` / `net_inflow_pct` 恒 `null`。
- 龙虎榜/涨停池/解禁等 raw 产品仍未接备源；继续走 `tdx_get_product`，拿到列义证据后再映射。
- `lhfp` 已做**部分**列义映射：`N001`→date、`N002`→listed_count（与东财逐日去重股票数
  连续 5 日一致）、`N003`→net_amount_yuan（TDX 口径，与东财汇总不完全一致）；
  `N004`–`N006` 语义未证实，保留 raw，不猜。
- `lhzy` / `lhbsj` 个股明细已映射：`code/name/market(0深/1沪/2北)/price/change_pct/
  amount_yuan/net_buy_amount_yuan/net_buy_ratio_pct/tag/reason_code/date/tags/concepts`；
  amount/net_buy 与东财 `ACCUM_AMOUNT`/`BILLBOARD_NET_AMT` 逐股核对一致。
  `reason_code` 是 TDX 内部异动码（未翻译）；需要中文原因用 `get_dragon_tiger`。

**复检**：`python scripts/tdxdata_smoke.py`（默认 7 项，约 30 秒）。
不要把它并入 `scripts/live_smoke.py` 的默认全量跑（421 产品会跑 15+ 分钟）。

---

## K. eltdx `historical_kline` 忽略日期范围/复权（✅ v3.4.1 已修复）

**状态**：✅ v3.4.1 修复。保留记录，避免重复排查。

- **原现象**：`get_historical_price(symbol="600519", start_date="20261020",
  end_date="20261024")` 返回最近 **100 根**日线（首行 2026-06-04），而不是指定区间；
  `adjust="qfq"` 也实际拿到不复权数据。
- **根因**：`eltdx_fetchers.fetch_historical_kline` 的签名只接
  `code/symbol/period/count`，日期与复权参数被 `**kwargs` 吞掉，`count` 默认 100。
- **影响**：日期范围查询、`analysis_engine` / `signal_data_base` 等内部调用
  （传 `start_date` 但未传 `count`）实际只拿到最近 100 根；回看窗口 >100 根时
  指标/信号会基于不足的数据计算。
- **修法（v3.4.1）**：
  - 真正接收并过滤 `start_date` / `end_date`；
  - 按日期范围从最新一页向前分页回溯（`bars.get(start=offset)`，最多 8 页 / 6400 根）；
  - 日期范围优先于 `count`，`count` 只作为下限（避免 live_smoke 通用池 `count=5` 截断区间）；
  - 真正透传 `adjust` 到 `bars.get(adjust=...)`；
  - 超出可获取历史时显式抛错，不静默截断。
- **回归测试**：`tests/test_eltdx_kline_date_range.py`（6 项）。
- **实测**：`20261020~20261024` 返回 4 行；`2022-01-10` 的 qfq 收盘 1742.99 vs
  不复权 1966.00，复权参数确实生效。

---

## 复检方法

```bash
# 全量（主机 + 103 个源 + 130 个工具，约 7 分钟）
python scripts/live_smoke.py --out live_smoke_report.md

# 只复检本清单涉及的类型（A/B/C/D/H/I 组）
python scripts/live_smoke.py --sources-only --skip-hosts \
  --types historical_kline,minute_data,industry_data,industry_comparison,hot_rank,financial_stmt,company_info,fund_hold,local_kline,local_minute,wencai_news,wencai_query,recent_minute

# 单独确认某个主机是否已恢复
python -c "from curl_cffi import requests as r; print(r.get('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fs=m:0+t:6&fields=f12', timeout=10, impersonate='chrome120').status_code)"
```

> 单项失败会自动重试 1 次；上游偶发 503 / 连接重置很常见，不要据此判定代码有问题。
> 判断「上游挂了」还是「自己改坏了」：先看主机连通性层，
> 若某主机的**全部**源同时失败、而兄弟主机正常 → 上游节点问题。

> **`wencai_news` 专项**：它返回空表时，先按 G-4 直接打 HTTP 确认是契约问题还是代码问题。
> `wencai_query` 报错时，先按 E 组确认出口 IP 是否被问财封禁（`www.iwencai.com` 首页能否打开）。

## 更新记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-10-30 | v3.6.0 | **龙虎榜列义映射**：`tdx_get_lhb_review`（lhfp）+ `tdx_get_lhb_stocks`（lhzy/lhbsj）。lhfp：N001/N002 有交叉证据，N003 标 TDX 口径，N004–N006 不猜；个股明细的 price/change/amount/net_buy/ratio/market 已逐股核对东财。工具 144→146，全量离线 999 passed |
| 2026-10-30 | v3.5.0 | **Phase 3 深水区**：新增 `tdx_get_f10`（22 section）/ `tdx_get_finance`（9 类）/ `tdx_get_capital_flow_rank`（固定前 20）；只接标准字段，raw 产品仍走 `tdx_get_product`；工具 141→144，全量离线 993 passed |
| 2026-10-30 | v3.4.1 | **K 组已修复**：eltdx `historical_kline` 真正支持 `start_date`/`end_date`/`adjust`；日期范围分页回溯、日期优先于 count、超出历史显式报错；新增 6 条回归测试，全量离线 986 passed |
| 2026-10-30 | v3.4.0 | 新增 **J 组 tdxdata 限制**（2.2s 节流、无五档/方向/历史逐笔、52/421 列义）与 **K 组既有问题**（eltdx `historical_kline` 忽略 `start_date/end_date`，返回最近 100 根，未修）；tdxdata 注册为 `market_breadth`/`historical_kline`/`minute_data`/`finance_report`/`today_ticks` 5 个备源，源级冒烟 8/8；`dividend_financing` 因语义不符故意不接 |
| 2026-09-26 | v3.3.30 | 新增 **H 组「静默降级 + 参数默认值 → 错数据」**：`5m` 返回日线、`1m` 返回月线两个 P0 缺陷（根因、复现口径、修法与泛化教训）。这类缺陷**不会**被 `live_smoke.py` 判 `FAIL`——所有监控都是绿的。另补 **H-4「新增形参被通用参数池撞中」**（`page_size` 事故）；新增 **I 组「`minutes.recent`(0x0feb) 主站不返回数据」**（上游问题，工具保留不承诺有数据） |
| 2026-09-22 | — | 新增 **G 组「问财 OpenAPI 网关契约」**（X-Claw-* 头、`app_id` 固定值、响应结构三陷阱）；`wencai_news` 四个叠加缺陷的修复入 F 组；`wencai_query` **出口 IP 被封**的结论入 E 组 |
| 2026-09-20 | v3.3.21 | 首次建立。基于全量真实网络冒烟（102 源）整理 |
