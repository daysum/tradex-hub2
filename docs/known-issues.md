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

**当前状态快照**（2026-09-20，102 个已注册源）：
**`FAIL` = 0** ｜ `AKSHARE` = 2 ｜ `UPSTREAM` = 3 ｜ `ENV` = 4 ｜ 其余 93 个取到数据
（`AKSHARE` 2 项 = `industry_data` 的 akshare 行；`live_smoke.py` 的 endpoint 扫描「无可用源组合」**清单已清零**。）

> `AKSHARE` 残留的 2 项（`industry_data` 的 `board_industry_name_em` /
> `board_concept_name_em`）**只是该源的冗余条目** —— 这两个 endpoint 已由 ths 备源
> 覆盖；加上 v3.3.28（成分股）与 v3.3.29（历史行情）后，
> `industry_data` 的 **7 个 endpoint 全部有可用源**。

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
| `wencai_news` | `iwencai_openapi` | 环境变量 `IWENCAI_API_KEY` |
| `wencai_query` | `pywencai` | `pywencai` 依赖 + 网络 |

---

## F. 已修复（保留记录，避免重复排查）

| 日期 | 项 | 修复 |
|------|----|------|
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

## 复检方法

```bash
# 全量（主机 + 102 个源 + 129 个工具，约 7 分钟）
python scripts/live_smoke.py --out live_smoke_report.md

# 只复检本清单涉及的类型（A/B/C/D 组）
python scripts/live_smoke.py --sources-only --skip-hosts \
  --types historical_kline,minute_data,industry_data,industry_comparison,hot_rank,financial_stmt,company_info,fund_hold,local_kline,local_minute,wencai_news,wencai_query

# 单独确认某个主机是否已恢复
python -c "from curl_cffi import requests as r; print(r.get('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fs=m:0+t:6&fields=f12', timeout=10, impersonate='chrome120').status_code)"
```

> 单项失败会自动重试 1 次；上游偶发 503 / 连接重置很常见，不要据此判定代码有问题。
> 判断「上游挂了」还是「自己改坏了」：先看主机连通性层，
> 若某主机的**全部**源同时失败、而兄弟主机正常 → 上游节点问题。

## 更新记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-09-20 | v3.3.21 | 首次建立。基于全量真实网络冒烟（102 源）整理 |
