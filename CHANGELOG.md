# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),

## [3.6.0] - 2026-10-30

### Added

- **龙虎榜 `lhfp` 列义映射**：新增 `tdx_get_lhb_review(date="", max_rows=60)`
  （工具 144→145）：
  - `N001` → `date`（日期）
  - `N002` → `listed_count`（上榜股票数；与东财龙虎榜按日去重股票数
    **连续 5 个交易日逐日完全一致**）
  - `N003` → `net_amount_yuan`（TDX 复盘口径净额；与东财 `BILLBOARD_NET_AMT`
    汇总不完全一致，例如 2026-10-29 TDX -12.52 亿 vs 东财 -9.76 亿，只建议看方向/量级）
  - `N004` / `N005` / `N006` **不映射**：满足 `N002 = N004 + N005 + N006`，
    但语义未证实；原值保留在每行 `raw`。
- **龙虎榜个股明细**：新增 `tdx_get_lhb_stocks(date="", view="list", max_rows=0)`
  （工具 145→146）：
  - `view="list"` → `lhbsj` 全量个股行（同一股票可因不同异动原因多行）；
  - `view="detail"` → `lhzy` 上游前 20，含净买占比、TDX 异动码、标签/概念 JSON；
  - 字段 `code/name/market(0深/1沪/2北)/price/change_pct/amount_yuan/
    net_buy_amount_yuan/net_buy_ratio_pct/tag/reason_code/date/tags/concepts`
    已用东财逐股核对（`ACCUM_AMOUNT`/`BILLBOARD_NET_AMT`/`CHANGE_RATE` 一致）。
- 新增 `tests/test_tdxdata_deep.py` 6 项龙虎榜测试（lhfp 2 + 个股明细 4）。

### Notes

- 龙虎榜 raw 产品其余列仍不猜列义；需要东财精确口径/原因文本请用 `get_dragon_tiger`。
- 全量离线 **999 passed / 7 deselected**。

## [3.5.0] - 2026-10-30

### Added

- **Phase 3 深水区：3 个结构化 tdxdata 工具**（`tools/tdxdata_deep.py`，工具 141→144）：
  - `tdx_get_f10(symbol, section="gsjj", max_rows=0)`：22 个 F10 section
    （gsjj/jyfx/gbgd/zbyz/rdtc/fhrz/zg_jby/zg_scm/zg_zs/zp/...）；
  - `tdx_get_finance(symbol, kind="zcfz", max_rows=0)`：9 类财报
    （zcfz/lyb/xjllb/zyzb/cwbg/cwfxxq/tjcj/zysy/zyzbxq），标准英文字段；
  - `tdx_get_capital_flow_rank(top_n=20)`：全市场主力净流入榜（上游固定前 20）。
- 新增 `tests/test_tdxdata_deep.py`（7 项）。

### Notes

- **只接已有标准字段/语义层的接口**；龙虎榜/涨停池/解禁等 raw 产品继续走
  `tdx_get_product`，在拿到逐列证据前不硬映射。
- `tdx_get_f10(section="fhrz")` 与 `get_xdxr` 同源：是 IPO/融资累计统计
  （`ipo_count`/`ipo_proceeds_yuan`），**不是**逐次分红/除权事件表。
- `tdx_get_capital_flow_rank` 不支持单代码（榜单命令无代码位）；单代码请用
  `get_money_flow`；`price` / `net_inflow_pct` 本通道恒 `null`。
- `tdx_get_finance(kind="cwfxxq")` 上游多 ResultSet 且列义未定，会抛
  `NotSupportedError`（不猜列名）。
- 全量离线 **993 passed / 7 deselected**。

## [3.4.1] - 2026-10-30

### Fixed

- **K 组：eltdx `historical_kline` 忽略 `start_date` / `end_date` / `adjust`（静默错数据）。**
  此前 `fetch_historical_kline` 的签名只有 `code/symbol/period/count`，日期与复权参数
  被 `**kwargs` 吞掉：
  - `get_historical_price(start_date=..., end_date=...)` 实际返回最近 100 根
    （首行可能是几个月前），日期区间完全失效；
  - `get_historical_price(adjust="qfq")` 实际拿到不复权数据；
  - `analysis_engine` / `signal_data_base` 传 `start_date` 后也只拿到最近 100 根，
    回看窗口 >100 根时指标/信号基于不足数据计算。

  修法（`data_sources/eltdx_fetchers.py`）：
  - 真正接收并过滤 `start_date` / `end_date`；
  - 按日期范围从最新一页向前分页回溯（`bars.get(start=offset)`，最多 8 页 / 6400 根）；
  - 日期范围优先于 `count`，`count` 只作为“至少取多少根”的下限
    （避免 live_smoke 通用池 `count=5` 截断区间）；
  - 真正透传 `adjust` 到 `bars.get(adjust=...)`；
  - 超出可获取历史时**显式抛错**，不静默截断。

- 新增回归测试 `tests/test_eltdx_kline_date_range.py`（6 项）：日期过滤、跨页回溯、
  `count` 下限、`adjust` 透传、超出历史报错、非法日期格式报错。

### Notes

- 实测：`get_historical_price("600519", 20261020~20261024, qfq)` 返回 4 行；
  `2022-01-10` 的 qfq 收盘 1742.99 vs 不复权 1966.00，复权参数确实生效。
- 全量离线 **986 passed / 7 deselected**。

## [3.4.0] - 2026-10-30

> Phase 0 + Phase 1 + Phase 2 + Phase 3：接入 tdxdata（通达信手机版 7615）数据中心，
> 并注册为 5 类现有数据的备源。

### Added

- **Vendored tdxdata 0.3.0**（commit `a276f57`，MIT）到 `tradex/src/tdxdata/`：
  纯标准库零依赖，含 421 个数据中心产品目录与 7615 协议引擎；`VENDORED.md`
  记录来源/commit/同步步骤；`pyproject.toml` wheel packages 已加入 `src/tdxdata`。
- **`data_sources/tdxdata_fetchers.py`**：tdxdata 客户端单例 + 全局锁 + `call()` +
  `_shutdown()` + `tdx_ping()`；`server.py` lifespan 退出时关闭长连接。
- **10 个 tdxdata MCP 工具**（`tools/tdxdata_data.py`，工具总数 130→140）：
  - `tdx_list_products` / `tdx_get_product`（421 产品目录 + 通用访问）
  - `tdx_get_market_breadth` / `tdx_get_breadth_history`（实时/历史涨跌家数）
  - `tdx_get_intraday_samples`（多代码当日分时点位，实验性）
  - `tdx_get_boards` / `tdx_get_board_members`（六类板块 / 概念成分股）
  - `tdx_get_market_stat` / `tdx_get_price_limits`
  - `tdx_status`（集成状态/节流统计）
- **`docs/tdxdata-products.md`**：57 个常用产品中文说明与参数提示。
- **Phase 2 备源**（`data_sources/tdxdata_fetchers.py` + `registry.py`）：
  - `market_breadth` 增加 tdxdata（priority=100，主源东财 push2ex）
  - `historical_kline` 增加 tdxdata（priority=300，主源 eltdx → akshare）
  - `minute_data` 增加 tdxdata（priority=300，主源 eltdx → akshare）
  - 严格周期/复权映射：`quarter` → tdxdata `season`；年线/3 分钟**显式抛错**；
    未知复权同样抛错，不回落默认值。
  - K 线输出 12 列与 akshare 口径一致；换手率恒 `null`（上游不提供），
    振幅/涨跌幅/涨跌额由上一根收盘自算，首行无前收为 `null`。
  - 相关工具路由超时放宽到 30s（`get_historical_price` / `get_intraday_data` /
    `eltdx_get_kline` / `eltdx_get_minutes` / 技术分析与技术指标内部调用）。
- **Phase 3 深化**：
  - 新增 L2 工具 `calculate_risk_metrics`：HV / 动量 / 夏普 / 最大回撤 / Beta
    （基于 vendored `tdxdata.indicator`，纯函数无网络）。工具总数 **140 → 141**。
  - `finance_report` 增加 tdxdata 备源（priority=100，标准英文字段；与 eltdx T 编码字段不同）。
  - `today_ticks` 增加 tdxdata 备源（priority=100；**无买卖方向/开盘撮合标记**，
    成交额按 price×手数×100 推算，最多 1000 笔/次）。
  - `dividend_financing` **故意不接** tdxdata：`get_xdxr` 是 IPO/融资累计统计，
    不是分红/除权事件表，接入会造成语义错配。
  - 数据源注册数 106 → **108**（新增 finance_report/today_ticks 两个 tdxdata 备源）。
- **配置**：`TDXDATA_ENABLED` / `TDXDATA_CONNECT_TIMEOUT` / `TDXDATA_GAP` /
  `TDXDATA_ROUTE_TIMEOUT` / `TDXDATA_CACHE_TTL` / `TDXDATA_MAX_ROWS`。
- **测试**：`tests/test_tdxdata_packaging.py`（6）、`tests/test_tdxdata_fetchers.py`（49）、
  `tests/test_tdxdata_tools.py`（23）、`tests/test_risk_metrics.py`（6）；
  `scripts/tdxdata_smoke.py` 真实网络冒烟；全量离线 **980 passed / 7 deselected**。

### Notes

- tdxdata 单命令约 2.2 秒匀速节流、按来源 IP 限流；这些工具定位为低频/按需查询，
  未接入实时行情主链路。
- 421 个产品中仅 52 个有标准列义；未登记产品返回原始列并在 `column_note` 提示。
- 数据源注册数 103→108（tdxdata 5 个备源条目），数据类型仍 79 个。

## [3.3.30] - 2026-09-26

本轮主题：**分钟级 K 线全线打通 + 消灭两个「静默错数据」缺陷**。

### Fixed

- **`historical_kline` 请求 `period="5m"` 时静默返回日线（P0）。**

  链路：eltdx 源 `_normalize_period` 只认 day/week/month，遇到 `5m` 抛
  `ValueError` → SmartRouter 按设计降级 akshare → 东财参数构造里写的是
  `_EM_KLT.get(period, "101")`，**未识别周期一律落默认值 `101`=日K** →
  调用方拿到 1498 根日线（首行日期 2020-07-27），**全程没有任何告警**。

  修法（两处一起改，缺一不可）：
  - `eltdx_fetchers._normalize_period` 支持分钟周期（见 Changed）；
  - `akshare_fetchers` 新增 `_norm_klt()`，**未识别周期直接抛 ValueError**，
    并把 `_EM_KLT` 补全为 `1m/5m/15m/30m/60m → 1/5/15/30/60`（东财原生支持分钟）。
  另：腾讯备源 `ifzq` 的 `per` 参数只认 day/week/month，此前同样会默默当成 day
  —— 现显式拒绝分钟周期（`_fetch_kline_from_tencent`）。

- **`period="1m"` 返回月线（P0）。**

  两个源的别名表都写着 `"1m": "month"`（akshare 侧 `"1m": "monthly"`），
  而 `fetch_full_kline` 不做归一化 → 同一参数名 `1m`
  在 `eltdx_get_kline` 是「月线」、在 `eltdx_get_full_kline` 是「1 分钟」，
  **语义相反**。现统一为「1 分钟」，全链路口径一致（见 Changed）。

- **`adjusted_kline` 分钟周期硬失败。** `fetch_adjusted_kline` 走归一化，
  分钟周期被拒；该路由是 eltdx **独占源**，无备源可降级，直接
  `RuntimeError: unsupported period: '5m'`。现放开分钟周期。
  （实测 eltdx 3.2.2 `bars.get(period="5m", adjust="qfq")` 底层完全可用。）

- **`fetch_full_kline` 的 `max_pages` 默认 30 会触发 SDK 的硬报错。**
  `bars.get(all_pages=True)` 在「达到 max_pages 仍未出现空页」时抛
  `RuntimeError: bars.get reached max_pages`（不静默截断）。1 分钟全量实测
  约 2.2 万根 ≈ 28 页，30 页对日线够、对分钟周期边界很近。默认值放宽到
  SDK 默认的 200，并暴露给工具层。

- **备源分钟 K 线的日期被截断。** `_fetch_kline_from_em` 统一做
  `pd.to_datetime(...).dt.date`，会把分钟 K 的时分抹掉。现按周期分流：
  分钟周期输出 `"YYYY-MM-DD HH:MM"`，日线及以上仍为 `date`。

- **`get_local_kline` / `get_local_minute` 返回非法 JSON（既有缺陷，本轮暴露）。**
  两处均写作 `dict_to_json(info) + "\n" + df_to_json(df)` —— 把
  `{...}`（元信息）与 `[...]`（数据行）用换行拼成一个字符串，**不是合法 JSON**。
  此前本机读不到通达信 vipdoc 时恒走 `error_response` 分支，故一直未暴露；
  本轮本机有本地数据后，`live_smoke` 才判出「返回非法 JSON」。
  现合并为单个对象：`dict_to_json({**info, "data": json.loads(df_to_json(df))})`。

- **`fetch_full_kline` / `fetch_adjusted_kline` 新增 `_clamp_page_size` 护栏。**
  冒烟验收暴露：`page_size` 是**形参名精确匹配**式传参，`live_smoke` 的通用参数池
  带着 `"page_size": 5`（本意服务巨潮公告分页），新加的 `page_size` 形参把它接住 →
  每页只取 5 根 → 6012 根需 1203 页 → 撞穿 200 页上限 →
  `RuntimeError: bars.get reached max_pages`。
  根因是「**参数名撞车 + 无下限护栏**」，故在 fetcher 层统一兜底：
  `_clamp_page_size()` 把非法值归一为 800、小于 `_PAGE_SIZE_FLOOR`(100) 的抬到 100，
  使**任何**传入路径都不会再因小页长而失败。

### Changed

- **BREAKING：`period="1m"` 语义由「月线」改为「1 分钟」**，与 eltdx 官方口径
  （`docs/methods/7709-K线周期线.md`）对齐。
  - 月线请改用 `month` / `monthly` / `1mo`；
  - 短别名 `m` 仍保留为月线（兼容旧调用，不推荐）；
  - 若外部脚本/技能此前用 `period="1m"` 取月线，升级后需改为 `1mo`。
  - 影响面已核：仓库内无 `period="1m"` 调用点。

- `_normalize_period` 现支持 `quarter`/`q`/`1q` 与 `year`/`y`/`1y`
  （eltdx 底层原生支持，此前被拒绝）。

- `_fetch_historical_kline_direct` / `fetch_historical_kline` 新增 `count` 形参，
  用于对备源分钟 K 做「最近 N 根」尾部截取（语义对齐 eltdx）。

### Added

- **分钟 K 线（`1m/5m/15m/30m/60m`）全链路支持**，覆盖：
  - `eltdx_get_kline`（单页，`count ≤ 800`，新增越界校验）
  - `eltdx_get_adjusted_kline`（复权 × 分钟，新增 `all_pages`/`max_pages`）
  - `eltdx_get_full_kline`（全量分页，新增 `adjust`/`max_pages`/`page_size`）
  - 备源同样支持分钟（东财 `klt=1/5/15/30/60`），但**窗口有限、仅近期**。

- **`eltdx_get_security_codes` 新增 `kind` 品种快捷参数**：
  `stock`（A+B 股）/ `a_share` / `etf` / `index` / `st`（ST 名单）/
  `suspended`（停牌名单）。下推到数据源层（`codes.stocks()` 等），
  比 `category` 后置过滤更省流量、更准。
  例：`market='bj', kind='stock'` → 北交所股票表（实测 350 只，
  而 `market='bj'` 不带 kind 会返回全部品种 385 条，含指数/ETF/债券）。

- **新增 `eltdx_get_recent_minutes`**（工具数 129 → **130**）。
  底层为 `client.minutes.recent`（协议 `0x0feb`），与既有
  `eltdx_get_minute_history`（`0x0fb4`，指定日）、`eltdx_get_minutes`（当日）
  构成分时三件套。**两者是不同协议、覆盖范围不同，不可互相替代。**
  新增路由 `recent_minute`（eltdx 独占源；数据源总数 102 → **103**，
  数据类型 78 → **79**）。
  设计取舍：该接口空结果**返回空 DataFrame 而非抛错** —— 它是独占源，
  抛错会被 SmartRouter 记为源失败、污染健康评分（非交易日返回空是正常的）。
  > ⚠️ **实测（2026-09-26）：该命令当前主站不返回数据。** 对沪深京三种标的、
  > 从当日前推多个交易日、`"20260918"` 与 `"2026-09-18"` 两种日期格式，
  > 一律返回 **0 点**；同标的同日期下 `minutes.today` 与 `minutes.history`
  > 均正常返回 240 点。SDK 侧 `0x0FEB` 标记为 `required_for_1_0=False`
  > （非必需命令），报文构造在 Rust native 层，SDK 层无法进一步诊断。
  > 故本工具**保留但不承诺有数据**，待上游修复后即可自动可用；
  > 详见 `docs/known-issues.md` I 组。

- K 线类工具返回体新增 `period_canonical` 字段（归一化后的周期名），
  便于调用方核对「没有发生静默换周期」。

### 验证

- `pytest -m "not network"`（tradex/tests）：**165 passed**（基线 41 + 新增 124）
- `pytest -m "not network"`（仓库根 tests/）：**729 passed**，0 failed
- 真实主站端到端（`bj920185`，2026-09-26 凌晨实测）：
  - `historical_kline period=5m` → `src=eltdx`，首行 `2026-09-23 14:55`
    （修复前：`src=akshare`，首行 `2020-07-27`，日线）
  - `historical_kline period=1m` → 分钟粒度（修复前：`2022-08-31` 月线）
  - `adjusted_kline period=5m adjust=qfq` → `src=eltdx` 正常
  - `full_kline period=1m max_pages=80` → 22560 根，升序 `2026-05-15 09:31 → 2026-09-24 15:00`
  - `full_kline period=day adjust=qfq` → 1498 根，升序 `2020-07-27 → 2026-09-24`
  - `security_codes market=bj kind=stock` → 350 条
  - `period="badxyz"` → **两个源都报错**（修复前：akshare 静默返回日线）
- **全量真实冒烟**（`python scripts/live_smoke.py --out live_smoke_report.md`，7m51s，115 项）：
  本次改动引入的 **2 个回归已全部修复** ——
  `full_kline [eltdx]` 由 `FAIL` 恢复 `OK`（`page_size` 护栏生效，此前因冒烟通用池传入
  `page_size=5` 而撞穿 `max_pages`）；`get_local_kline` / `get_local_minute` 输出合法 JSON。
  MCP 工具层 130 项**无 `FAIL`**。
  冒烟期间新出现 2 项 `FAIL`（`industry_quotes [em_push2]`、`xueqiu_hot [akshare_xueqiu_hot]`）
  经逐个复核**均为上游故障，与本次改动无关**（本次未触碰相关模块）：
  东财 `push2`/`push2delay`/`push2his` 三台主机同时拒连；雪球接口返回非 JSON（反爬），
  但 `xueqiu_hot` 备源 `ths_hot` 与工具层 `get_xueqiu_hot` 均正常。
  `recent_minute [eltdx]` 为 `EMPTY`（上游 `0x0feb` 不可用，见 known-issues I 组，属预期）。
- 同步更新「数量锁」测试：工具数 129→130、数据源 102→103、数据类型 78→79、
  以及两处 `1m` 口径断言（`tests/test_p2_cleanup_regression.py`、
  `tests/test_v3314_fixes.py`）。

## [3.3.29] - 2026-09-20

### Fixed

- **`board_industry_hist_em`（行业历史行情）恢复可用 —— `industry_data` 的 7 个 endpoint
  至此全部有可用源，`docs/known-issues.md` 的 B 组彻底清零。**

  该 endpoint 此前**两个源都不覆盖**：主源 akshare 打
  `7.push2his.eastmoney.com/api/qt/stock/kline/get`（`secid=90.BKxxxx`），
  而东财板块日K路由**整条链路故障**（实测所有 `push2his*` 主机连接被拒；
  `push2delay` 认得出板块 —— 返回 `name='酿酒概念'` —— 但 `klines` 为空）；备源 ths 明确不支持。

  **换数据来源解决**：实测**同花顺板块K线免鉴权可用**：
  ```
  d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js     → 最近 ~140 根
  d.10jqka.com.cn/v6/line/bk_{code}/{seg}/{year}.js   → 某年全量
  ```
  JSONP 载荷：`quotebridge_v6_line_bk_xxx_01_last({"year":{...},"name":"白酒","data":"..."})`，
  `data` 每行 `日期,开,高,低,收,成交量,成交额,...`（**同花顺顺序：开高低收**）。

  实测确认的细节：
  - **周期分段码**：`01`=日K、`11`=周K、`21`=月K
  - **板块代码**：行业 `881xxx`（白酒=881273、半导体=881121）；载荷里的 `name`
    字段可用于校验「代码 ↔ 名称」映射
  - 同花顺板块K线**不提供换手率**（该列恒为空，不是 0）；
    涨跌幅/涨跌额/振幅按**前一根收盘**计算

- **实现放在 `ths_fetchers.fetch_ths_board_hist`**（10jqka 域名的传输归该模块管，
  AST 静态守卫也只允许它直连 `10jqka.com.cn`），`akshare_fetchers` 委托调用。

- **降级顺序刻意是「同花顺优先 → 东财兜底」**。若把东财放前面，每次冷启动都要先做
  一遍昂贵的「板块名 → BK 代码」解析（翻 496 行业 + 504 概念）再失败 ——
  实测首调用因此要 **24s**，超过路由默认超时；同花顺则 **1.5s** 返回。
  东财层保留为兜底：待其板块日K恢复后换回顺序即可拿回**换手率**列。

### Added

- `tests/test_board_hist_direct.py`（22 条）：JSONP 解析、**开高低收顺序**、
  涨跌幅/振幅按前收计算、换手率恒空、周期分段码映射、日期区间过滤、
  **按年补数**（start_date 早于 last.js 覆盖范围）、降级顺序（同花顺优先时不碰东财）、
  两层皆失败的错误汇总，以及回归守卫（不得再调 akshare）

### 验证

- `pytest -m "not network"`：**770 passed / 7 deselected / 0 failed**（基线 748 + 新增 22）
- 真实网络：`白酒` 日k→140 行、周k→400、月k→95；`半导体` 日k→140、周k→979、月k→230；
  代码直传 `881273` 结果一致；日期区间 `20260601~20260918` → 79 行（首末日期准确）
- 端到端：`get_industry_pe(industry="白酒")` → **error=None**（修复前恒报 All sources failed）
- **`live_smoke.py` 的 endpoint 扫描：`industry_data` 的「无可用源组合」清单已清零**

## [3.3.28] - 2026-09-20

### Fixed

- **`industry_data` 的 `board_industry_cons_em`（行业板块成分股）恢复可用**，
  `get_industry_stocks` / `get_competitors` 两个工具从「恒报 All sources failed」变为正常返回。

  此前该 endpoint **两个源都不覆盖**：主源 akshare 用裸 requests 打
  `29.push2.eastmoney.com/api/qt/clist/get` 被拒连；备源 ths **明确不支持**。
  实测 `push2delay.eastmoney.com` 的 clist 可取到（经 `em_get` 走镜像）→ 改本项目直连。

  排查中还揪出 **akshare 原版的两个隐藏缺陷**（即便它的传输正常也会失败）：

  1. **只搜「行业板块」，漏了「概念板块」**。实测 `白酒` / `新能源车`
     只存在于**概念板块**（BK0896 等），而 `银行` / `半导体` 在行业板块。
     但调用方 `get_industry_stocks` 的 docstring 明确把
     「白酒 / 新能源车」列为合法输入 → 必然找不到。本实现**两类板块都检索**。
  2. **板块列表只取第一页**。服务端把 `pz` **硬顶在 100**（实测传 600/1000 仍返回 100），
     而行业板块 496 个、概念板块 504 个 → 只看第一页会把靠后的名字误判为不存在。
     本实现**翻页**，并做**可断点续取的进程内缓存**（首次约 10s，之后 ~1s）。

  另：字段映射改为**按字段名取**（`f12`/`f14`/`f2`/`f3`/`f15`…），
  akshare 原版按位置赋列名，依赖响应 key 的精确顺序 + `fetch_paginated_data`
  里 `reset_index()` 插入的那一列（32 字段 + index = 33 列），上游改序即错位。

- **`board_industry_cons_em` 路由超时单独放宽到 40s**。首次调用需翻页建
  「板块名 → BK 代码」映射（实测约 10s），逼近默认 12s 路由超时 →
  在两个调用点（`industry.py` / `company_info.py`）显式传 `timeout`，
  与既有 `fund_hold` 的 `_FUND_HOLD_ROUTE_TIMEOUT` 做法一致。

### Added

- `tests/test_board_industry_cons_direct.py`（16 条）：BK 代码直通、
  行业/概念两类板块解析、**翻页找到第 2 页的名字**、缓存命中零请求、
  **断点续取**（第二次从上次的页码继续而非重来）、字段按名映射（乱序 key 不错位）、
  成分股分页、排序与序号、失败路径，以及回归守卫（不得再调 akshare）

### 验证

- `pytest -m "not network"`：**748 passed / 7 deselected / 0 failed**（基线 732 + 新增 16）
- 真实网络：`白酒`→45 只、`银行`→42、`半导体`→186、`新能源车`→**716**（跨 8 页）、
  `BK0896` 直通；不存在的名字给出明确错误并列出已检索的两类板块规模
- 端到端：`get_industry_stocks(industry="白酒")` → **45 条 / error=None**（修复前恒失败）；
  `get_competitors(symbol="600519", industry="白酒")` → 30 条 / error=None
- 缓存效果：首次 ~10s（含镜像发现与翻页）→ 后续 **~1.1s**

## [3.3.27] - 2026-09-20

### Fixed

- **`historical_kline` 备源恢复可用**。改为**本项目直连两层降级**：

  1. **东财 push2his kline**（走 `em_get`，享节流/重试/镜像/熔断）——
     与 akshare 同源同字段，复权与振幅/涨跌幅由上游原生给出
  2. **腾讯 `proxy.finance.qq.com`**（实测可用）—— 兜底

  **为什么必须加第 2 层**：实测东财的日K路由**整体不可用** ——
  `push2his.eastmoney.com` 连接被拒（10/10 失败），
  `push2delay.eastmoney.com` 该路径返回 `rc=0` 但 **`klines` 为空**
  （它只服务分时）。所以只把 akshare 换成直连东财**仍然取不到数**，
  腾讯兜底才是让这个源今天就能工作的关键。

  **字段语义已用主源 `eltdx` 逐字段交叉验证**（2026-09-18 / 600519）：
  腾讯 bar `[1]`开 `[2]`收 `[3]`高 `[4]`低 `[5]`成交量(手) `[7]`换手率 `[8]`成交额(**万元**)
  —— 与 eltdx 的 开盘 1262.99 / 收盘 1257.12 / 最高 1265.88 / 最低 1256.10 /
  量 24890.87 / 额 3,135,849,216 元 完全吻合。腾讯**不提供**振幅/涨跌幅/涨跌额，
  按前一根收盘价自行计算；成交额由万元换算为元（与东财/eltdx 口径统一）。

  **顺带规避的第二个问题**：akshare 用「按位置赋列名」把逗号串映射成中文列名，
  一旦上游调整 `fields2` 顺序或字段缺失就会整体错位（v3.3.16 fund_hold 同类事故）。
  本实现虽同样按位置切分（逗号串没有 key 可依），但**显式校验字段个数**
  （不足 11 个的行直接丢弃），避免「少一列 → 整体错位」的静默污染。

  输出列与 akshare `stock_zh_a_hist` **完全一致**（12 列，含顺序）：
  `日期 / 股票代码 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 振幅 / 涨跌幅 / 涨跌额 / 换手率`

### Added

- `tests/test_historical_kline_direct.py`（27 条）：两层降级、列名与顺序、
  腾讯万元→元换算、按前收计算涨跌幅/振幅、乱序 bar 排序、日期区间过滤、
  周期键（day/week/month）、symbol 归一化、字段不完整行丢弃、失败原因汇总，
  以及回归守卫（不得再调 akshare `stock_zh_a_hist`）

### 顺带修复

- **`test_concurrent_calls_respect_min_interval` 偶发假失败**（flaky）。
  原测试在各线程 `acquire()` **返回之后**才打墙钟点，线程在这两步之间被 OS 抢占
  就会让记录间隔小于真实间隔（单独跑必过、全量跑偶挂，本次实测复现 1 次）。
  改为断言节流器**内部预定的时间戳**（`_last_call`）——
  预定量才是真正决定实际间隔的机制，且断言是**确定性**的。
  已验证：加固后连跑 5 次稳定通过，且**仍能抓到真回归**
  （把 interval 置 0 时断言失败）。

- **`scripts/live_smoke.py` 新增 endpoint 扫描 + 跨源聚合**。
  起因：核查「`industry_data` 的 ths 备源是否可用」时发现
  **「类型级 OK」会骗人** —— 报告里 `industry_data / ths` 是 `OK (90,2)`，
  但那只是**默认 endpoint**（板块列表）的结果；同一个类型的
  `board_industry_cons_em`（行业成分股）/ `board_industry_hist_em`（行业历史行情）
  **两个源都不覆盖**，导致 `get_industry_stocks` / `get_industry_pe` /
  `get_competitors` 三个工具全在报「All sources failed」。

  改动：
  1. `ENDPOINT_SWEEP`：凡 fetcher 带 `endpoint` 形参的类型，**逐 endpoint 各测一遍**
     （`industry_data` 7 个 endpoint 全覆盖）
  2. 新增 `UNSUPPORTED` 状态：源**明确声明**不支持该 endpoint（覆盖缺口，非代码回归），
     与 `FAIL` 区分开
  3. 新增 `endpoints_without_source()`：**跨源聚合**，列出「所有源都拿不到数据」的
     `(类型, endpoint)` 组合 —— 这才是多 endpoint 类型的关键结论。
     实测输出：
     ```
     industry_data[board_industry_cons_em]  [AKSHARE, UNSUPPORTED]
     industry_data[board_industry_hist_em]  [AKSHARE, UNSUPPORTED]
     ```
     （`sector_fund_flow_rank` 未被列出 —— 主源 akshare 经 `em_get` 可取到，正确排除）

### 验证

- `pytest -m "not network"`：**732 passed / 7 deselected / 0 failed**（基线 705 + 新增 27）
- 真实网络：`fetch_historical_kline("600519", daily, 20260910~20260918)` → **7×12**
  （修复前 FAIL），数值与 eltdx 主源逐字段吻合；日/周/月 × qfq/hfq/不复权 全通；
  稳态 0.18s（首次 5.9s 含东财尝试与熔断）
- 经 SmartRouter 仍命中主源 `eltdx`（100×7）
- 3 条旧测试（参数归一化）按**原意图**改写注入点（`ak.stock_zh_a_hist` → `em_client.em_get`），
  意图不变：验证归一化后的 symbol/period 确实到达源站

## [3.3.26] - 2026-09-20

### Fixed

- **`industry_comparison` 备源（东财行业资金流）恢复可用，并修掉一处隐藏的数据错误**。

  **① 传输层**：akshare 的 `stock_sector_fund_flow_rank` 用裸 requests 打
  `push2.eastmoney.com/api/qt/clist/get` —— 正是本项目统一入口**已覆盖且已配镜像**
  的路径（实测裸 requests `RemoteDisconnected`，经 `em_get` 200 走 push2delay）。
  改为**本项目直连**，与 em_push2 源共用同一路径与镜像。

  **② 数据正确性（更严重，被传输失败掩盖了）**：旧代码读 akshare 的
  `板块` / `涨跌幅` / `领涨股票` 三列，但**这三个列名在 akshare 的实际输出里都不存在**
  （真名是 `名称` / `今日涨跌幅` / `今日主力净流入最大股`）。
  也就是说 —— **即便 akshare 的传输没坏，它返回的也会是**
  `name="None"`、`change_pct=0.0`、`leader="None"` 这样的垃圾数据。
  传输一直失败反而让人以为「修好网络就行了」。
  直连版按**字段名**取（`f14` / `f3` / `f204`），返回真实值。

  **③ 顺带补齐**：旧实现因 akshare 字段集不含 `f104`/`f105` 而写死
  `up_count/down_count = 0`；本实现在 fields 里补上这两项，返回**真实的涨跌家数** ——
  更贴近本函数「与 em_push2 源相同的 dict 结构」的既定契约
  （em_push2 源本就是从 f104/f105 取的）。

  另：结果按主力净流入降序（`fid0=f62`），故只取够 `top_n` 的页数，
  不像 akshare 那样把全部分页拉完。

### Added

- `tests/test_industry_comparison_direct.py`（15 条）：输出结构、请求契约、
  分页边界（`top_n=150` 跨页）、失败路径，以及两条回归守卫 ——
  ① 不得再调 akshare；② **输出不得退化成旧实现的 `"None"`/`0.0`**

### 验证

- `pytest -m "not network"`：**705 passed / 7 deselected / 0 failed**（基线 690 + 新增 15）
- 真实网络：`fetch_industry_comparison(top_n=5)` → 5 个行业，含真实涨跌家数
  （航空机场 涨1.05% 13涨0跌 领涨中国东航）；`top_n=150` → 150 条（跨 2 页）
- 三个源全部可用：`em_push2` 100 条、`akshare` 10 条（修复前 FAIL）、`ths_flow` 10 条；
  经 SmartRouter 仍命中主源 `em_push2`

## [3.3.25] - 2026-09-20

### Fixed

- **`minute_data` 备源（东财分时）恢复可用**。`akshare` 源此前恒失败，根因有两层：
  1. akshare 的 `stock_intraday_em` 打 **`70.push2.eastmoney.com`** —— 带数字前缀的
     子域**没有配镜像**，节点故障时无路可退
  2. 它用的是 **`/api/qt/stock/details/sse`（SSE 长连接）**：服务端把整包塞在首个
     事件后**不主动关闭**。非流式 GET 会阻塞到超时（实测 10s 必超时）；
     即便 `stream=True` 读到首个事件就 break，**`resp.close()` 仍会去排空剩余
     响应体**（curl 为复用连接），实测 **close() 独占 8.6s**

  改为打 **`push2.eastmoney.com`**（有 push2delay 镜像）的
  **`/api/qt/stock/details/get` 非流式变体** —— 实测 200 / **0.28s**，载荷与
  SSE 首个事件一致。稳态 1.25s（流式版需 10.6s，**快 8 倍**）。

### 说明（排查过程）

排查时先按「流式读取」实现了一版（新增 `em_stream_json`），功能正确但耗时 10.6s。
定位到瓶颈在 `resp.close()` 后，**改为寻找非流式变体**，确认 `/details/get` 可用，
于是**撤掉了整套流式实现**（含 `em_stream_json` / `_read_first_sse_event` /
`_stream_once` 及配套 import）—— 用 `em_get` 更简单、更快、且复用已有的
镜像与熔断。**结论：东财同一份数据常同时提供 `/get` 与 `/sse` 两个变体，
优先用 `/get`。**

另修正一处 secid 归一化：显式市场前缀优先于首位数字推断
（`sh000001` 是上证指数，市场码须为 1；只按首位数字猜会判成深市 0 → 空明细。
akshare 原版就有这个毛病）。

### Added

- `tests/test_minute_data_direct.py`（19 条）：解析与买卖盘映射、**接口选择守卫**
  （不得用 `/sse`、不得用 `70.push2`）、secid 归一化、失败路径、
  **回归守卫**（不得再调用 akshare `stock_intraday_em`）

### 验证

- `pytest -m "not network"`：**690 passed / 7 deselected / 0 failed**（基线 671 + 新增 19）
- 真实网络：`fetch_minute_data("600519")` → **3955×4**（修复前 FAIL），
  列 `时间/成交价/手数/买卖盘性质`；`000001`→4184、`sh000001`(上证指数)→4746、
  `300750`→4840、`688981`→4913（各入参形式均正确解析 secid）
- 经 SmartRouter：仍命中主源 `eltdx`（240×4），备源 akshare 恢复可用

## [3.3.24] - 2026-09-20

### Fixed

- **`hot_rank` 主源（东财人气榜）恢复可用**。`fetch_hot_rank_data` 的 `rank` / `up`
  两个 endpoint 此前恒失败。**根因不是「源挂了」**，而是 akshare 的实现分两步：
  1. 裸 `requests` POST `emappdata.eastmoney.com/stockrank/*` 取榜单（**实测可达**）
  2. 裸 `requests` GET `push2.eastmoney.com/api/qt/ulist.np/get` 取行情（**被拒连**）

  而**同一个 URL 经 `em_get` 可正常取到**（自动走 push2delay 镜像）——
  也就是说这两个榜单本可用，只是 akshare 绕过了统一防封层。
  现改为**本项目直连**（`emappdata` 走新增的 `em_post`，行情走 `em_get`）。

  其余 5 个 endpoint（`detail` / `realtime` / `keyword` / `latest` / `relate`）
  只打 emappdata（实测可达），仍走 akshare。

### Added

- **`em_post()`**：东财统一 POST 入口。把 `em_get` 的编排逻辑抽成共用的
  `_em_request(method, ...)`，使 **POST 接口同样享有节流 / 退避重试 / 主机镜像 /
  失败熔断** —— 否则 POST 类接口会成为绕过防封层的后门
- `tests/test_hot_rank_direct.py`（21 条）、`em_post` 专项测试（4 条）

### 注意（数据对齐）

直连版**按代码 join 两个列表，而不是按位置对齐**：
榜单（emappdata）与行情（ulist）是两个独立请求，条数或顺序都可能不一致
（镜像 push2delay 对停牌股可能不返回行情）。按位置拼会让**代码与名称静默错位** ——
正是 v3.3.16 `fund_hold` 列错位的同类事故。实测某只票缺行情时，
按代码 join 仍能保证后续行不错位（有专门用例守着）。

另：上游返回的 key 顺序与 `fields` 参数顺序**并不一致**
（实测请求 `fields=f14,f3,f12,f2`，实际 key 顺序是 `f2,f3,f12,f14`）。
akshare 按位置赋列名恰好蒙对；本实现一律**按字段名取**。

### 验证

- `pytest -m "not network"`：**671 passed / 7 deselected / 0 failed**（基线 645 + 新增 26）
- 真实网络：`hot_rank` 主源 `akshare_hot_rank` → **100×6**（修复前 FAIL），
  列 `当前排名/代码/股票名称/最新价/涨跌额/涨跌幅`；
  `up` → **100×7**（含「排名较昨日变动」）；
  经 SmartRouter 路由**命中主源**（修复前会降级到备源 `ths_hot`）
- 稳态耗时 2.2s（首次 8.2s 含镜像发现，之后走镜像静默期）

## [3.3.23] - 2026-09-20

### Added

- **东财请求失败熔断**（`em_get`，熔断器模式）：某个 **(主机, 路径)** 出现
  **主机级失败**（连接失败 / 5xx）后进入冷却（默认 60s），期间**立即返回 `None`**，
  让调用方直接走备源 —— 不再白跑满 `max_retries` 次退避
  - **按「主机 + 路径」而非仅主机**（关键）：实测 `push2his` 是
    「`kline/get` 挂了、`fflow/daykline/get` 还活着」，
    按主机熔断会把同主机上**健康的路径**一起跳过，故障面反而扩大
  - `403` / `429` / `rc=102` 是按 IP 风控，主机本身没问题 → **不熔断**
  - 有镜像可切且镜像可用时走镜像静默期，不额外熔断主源
  - 请求成功（或属正常业务结果如 `rc=100`）时解除该 `(主机,路径)` 的熔断标记
  - 冷却到期自动重探（不会永久熔断）
  - 新增 `reset_dead_hosts()` / `get_dead_host_status()` / `reset_host_health()`
    与 `EM_DEAD_HOST_COOLDOWN`（默认 60，设 0 关闭）
- `tests/test_em_dead_host_breaker.py`（17 条）：熔断触发与短路、
  **按主机+路径的作用域**（含「同主机健康路径不被误伤」）、不该熔断的四种情形、
  冷却管理、开关
- `tests/conftest.py` 新增 autouse fixture：用例前后清空「镜像静默期 / 死主机冷却」。
  这两份是模块级全局状态，不隔离会跨用例污染（`push2` 的 `/x` 被多个用例复用）

### 验证

- `pytest -m "not network"`：**645 passed / 7 deselected / 0 failed**（基线 628 + 新增 17）
- 真实网络实测（`push2his` 日K路由仍处故障）：
  ```
  index_daily_amount 第1次 → 5 条  6.18s   ← 真实失败并触发熔断
  index_daily_amount 第2次 → 5 条  0.18s   ← 短路，直走备源（快 34 倍）
  index_daily_amount 第3次 → 5 条  0.22s
  熔断状态: {'push2his.eastmoney.com/api/qt/stock/kline/get': 59.3}
  ```

## [3.3.22] - 2026-09-20

### Fixed

- **`index_daily_amount` 三层降级全失败**。主源 `push2his` 的日K路由挂掉后，
  两条备源也一起失效，导致该数据类型完全不可用（实测 2026-09-20
  `push2his` 日K路由 10/10 失败）：
  - **备源1 修复**：原用 akshare 的 `stock_zh_index_daily_tx`，它**按年份逐年翻页**
    （上证指数从 1990 年起 → 37 次请求 ≈ **21 秒**），在 SmartRouter 的 12s 超时下
    必然失败 → 备源被误判为不可用。改为本项目直连腾讯
    `proxy.finance.qq.com`（**单次请求 0.12s，快约 150 倍**），新增
    `http_fetchers.fetch_index_daily_tencent()`
  - 实测修复后：`fetch_index_daily_amount(symbol="sh000001", days=5)` → 返回 5 条
- **撤回一处错误的镜像配置**（v3.3.21 引入过程中）：曾把 `push2his` 映到
  `push2delay`，但**实测载荷不等价，已撤回**：
  - `push2delay` 的 `kline/get`（日K `klt=101`）返回 `rc=0` 但 **`klines` 为空**
    （它只服务 `klt=1` 分时）
  - 日线资金流：`push2his` 返回 **5 条**，`push2delay` 只返回 **1 条** ——
    映过去会把 5 条**静默降级成 1 条**
  - 故 `_EM_HOST_MIRRORS` 只保留**载荷验证过等价**的 `push2`
- **镜像降级增加「不服务该路径」保护**：镜像返回 404 等语义无效响应时，
  **不进入静默期**并原样返回主源结果 —— 否则会把**健康的主源**也一起跳过，
  故障面反而扩大（`push2delay` 对 `push2ex` 的 `getTopicZTPool` 就是 404）

### Added

- `http_fetchers.fetch_index_daily_tencent()`：腾讯指数历史日K直连
  （单次请求、走加固传输层、支持跨年回退）。**`amount` 恒为 `None`** ——
  该接口的成交额字段单位未能可靠确认，宁可为空也不给出可能错标的数据
- `tests/test_index_daily_source.py`（16 条）：解析、symbol 归一化、
  跨年回退、降级顺序、**回归守卫（不得再调用慢速的 akshare 按年翻页版本）**
- AST 静态守卫域名从 8 个扩到 9 个（新增 `finance.qq.com`）
- `docs/known-issues.md` 同步：`index_daily_amount` 移入「已修复」；
  A 组结论更正为「`push2his` 日K路由无可用镜像，需换源」

### 验证

- `pytest -m "not network"`：**628 passed / 7 deselected / 0 failed**（基线 611 + 新增 17）
- 真实网络：`fetch_index_daily_tencent("sh000001")` → 5 条 0.25s；
  `fetch_index_daily_amount("sh000001", days=5)` → 5 条（修复前失败）
- 镜像表保持最小：`{"push2.eastmoney.com": "push2delay.eastmoney.com"}`，
  并有测试锁定「只放载荷验证过等价的主机」

## [3.3.21] - 2026-09-20

### Added

- **东财主机镜像降级**（`em_get` 统一入口）：主源主机不可用时自动切换镜像主机，
  默认 `push2.eastmoney.com` → `push2delay.eastmoney.com`
  - **只在「连接层失败」或「5xx」时切换**：`403` / `429` / `rc=102` 是**按 IP** 的风控，
    换主机没有任何用（实测 `slist` 在 `push2` 与 `push2delay` 上都返回 `rc=102`），
    硬切只会让风控更糟，故明确不切
  - **静默期**：镜像成功后进入冷却（默认 300s），期间直接走镜像，
    避免每次调用都白等一遍死主机的退避。实测首次 6.38s（含主源 3 次退避）→
    二次 1.15s，**快 5.5 倍**
  - **两边都失败时不设静默期**，否则主源恢复后仍会被跳过
  - 配套 `reset_host_mirrors()`（清空静默期，主源恢复后立即重探）与
    `get_mirror_status()`（诊断用，返回各主机剩余静默秒数）
  - 新增 3 个环境变量：`EM_HOST_MIRROR`(默认 1，设 0 关闭)、
    `EM_PUSH2_MIRROR`(默认 push2delay.eastmoney.com)、`EM_MIRROR_COOLDOWN`(默认 300)
- `tests/test_em_host_mirror.py`（17 条）：覆盖切换边界（该切/不该切）、静默期语义、
  镜像也失败时的回退行为、开关、URL 不重复替换
- **`docs/known-issues.md`**：数据源待修正清单。记录当前拿不到数据的源、
  根因与建议修法，按优先级分组，含复检命令与已修复项（避免重复排查）。
  配套脚本 `scripts/live_smoke.py` 可一键复检

### Fixed

- **push2 节点故障导致 6 处调用点全线失败**。实测 2026-09-20 `push2.eastmoney.com`
  的 `/api/qt/clist/get` 路由返回 `curl: (56) Connection closed abruptly`
  （**连命令行 curl 都是 HTTP 000**），而同主机的 `/api/qt/stock/fflow/kline/get`
  正常、镜像主机 `push2delay` 全路由正常。此前无任何降级，导致
  `industry_comparison`(em_push2) 报「东财返回空数据」，`limit_up_board`、
  `em_client.fetch_stock_boards`、`em_push2_fund_flow` 等一并失效。
  收敛在 `em_get` 后 6 处调用点**自动全部受益**，无需逐个改动
- `http_fetchers.fetch_industry_quotes` 原有的 push2→push2delay 降级循环保留
  （与统一层机制重叠但幂等，统一层会在内层先切成功，外层第二次迭代不会执行）

### 验证

- `pytest -m "not network"`：**601 passed / 7 deselected / 0 failed**（基线 584 + 新增 17）
- 真实网络验证（`push2` 主源仍处故障状态下）：
  `em_push2` → `rc=0` 5 个板块；`get_industry_comparison_json` → **100 个行业**（此前失败）；
  `em_client.fetch_stock_boards` → `(28, 4)`；`em_push2_fund_flow` → 100 条分时；
  `limit_up_board._em_clist_get` → 3000 条
- 日志可观测：`em_get: push2.eastmoney.com 不可用（连接失败），切换镜像 …（静默 300s）`

## [3.3.20] - 2026-09-18

### Changed

- **第二梯队数据源（同花顺 / 财联社 / 巨潮 / 新浪 / 问财）收敛到统一防封入口**。3.3.19 只加固了东财与腾讯，其余源仍是裸请求，其中三处问题明显：
  - `data_sources/ths_fetchers.py` 用**模块级 `requests.Session()`** 裸请求 9 个同花顺接口（零指纹、零节流）
  - `akshare_fetchers.fetch_profit_forecast`（registry `priority=1`，线上生效）用的是**残缺的伪造 Mac UA**（`Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36`，无 Chrome/Safari 版本号）—— 这正是服务端识别伪造客户端的典型特征
  - `news_fetchers.py` 巨潮两处 POST（`hisAnnouncement/query` + `topSearch/query`）**完全没有指纹伪装**
  - 另 5 处同花顺裸请求：`hot_money.py`(×2)、`northbound.py`(×2)、`limit_up_board.py`(×1)；`wencai_fetchers.py` 裸 `urllib` POST

### Added

- **`ClientGroup` 抽象**（`antiban_http.py`）：一个数据源 = 一把节流器 + 一个指纹会话 + 一套重试参数，会话惰性创建（双重检查锁），命中限流自动换指纹重建
  - **按源分设间隔**而非共用一把锁：东财 1.0s、同花顺 0.25s、财联社/巨潮 0.30s、新浪 0.25s、问财 0.50s。共用一把锁会让低风险源被高风险源的间隔拖慢
  - **指纹真轮换**：固定指纹配置下也按指纹池顺序换下一个，而不是重复用同一个硬打
- **具名客户端注册表** `get_client("ths"|"cls"|"cninfo"|"sina"|"wencai")`：进程内单例，`astock_signals` 与 `tradex` 两包共用同一实例；配套 `registered_clients()` / `reset_clients()`
- **`json_of(resp, fallback_encoding=...)`**：宽松 JSON 解析。同花顺接口服务端**不声明 charset 但内容为 GBK**，curl_cffi 默认按 utf-8 解码会抛 `UnicodeDecodeError`；两种编码都失败时抛出带 `status` 与 body 片段的 `RuntimeError`，便于定位
- **`NO_IMPERSONATE` 模式**：显式关闭指纹伪装（保留节流/重试）。指纹伪装并非对所有站点都是好事——**巨潮 cninfo.com.cn 的 WAF 恰好封禁常见浏览器指纹**（实测 `chrome120`/`chrome124` → 403，`None`/`chrome110`/`safari15_5` → 200），故 cninfo 走该模式
- **静默限流识别**：`classify_response` 新增「HTTP 200 + 空 body → `throttled`」。JSON 接口返回 200 空体是典型的静默限流表现（同花顺高频请求下实测出现），此前被当作正常响应直接返回
- **新增 5 组环境变量**：`THS_RATE_LIMIT_INTERVAL`(0.25) / `THS_MAX_RETRY`(2)、`CLS_RATE_LIMIT_INTERVAL`(0.30)、`CNINFO_RATE_LIMIT_INTERVAL`(0.30)、`SINA_RATE_LIMIT_INTERVAL`(0.25)、`WENCAI_RATE_LIMIT_INTERVAL`(0.50) / `WENCAI_MAX_RETRY`(1)，以及 `TIER2_IMPERSONATE`(chrome120)
- `tests/test_antiban_tier2.py`（57 条）：注册表/ClientGroup 行为、`json_of` GBK 兜底、静默限流识别、11 处收敛行为断言，以及**扩展版 AST 静态守卫**

### Fixed

- **巨潮公告恢复可用**：`fetch_cninfo_direct` 此前（改造中途）因统一层默认套用 Chrome 指纹而返回 403 Forbidden、公告恒为空表；改走 `NO_IMPERSONATE` 后恢复（实测 `600519` 返回 5 条公告）
- **AST 静态守卫覆盖全部数据源域名**：由东财/腾讯 2 个域名扩展到 8 个（+ 10jqka.com.cn / hexin.cn / cls.cn / cninfo.com.cn / sina.com.cn / iwencai.com）
  - 守卫算法重写：按**真实传输对象**（import 进 requests/urllib/curl_cffi/httpx/aiohttp 的绑定名 + 模块级 `Session()` 变量）判定裸调用，避免把 `dict.get("key")` 误判为 HTTP 请求，同时**不再漏掉**「host 常量与裸调用分处不同语句」的写法（原实现正是因此漏掉了 `akshare_fetchers` 的腾讯裸请求）
- 同花顺 GBK 接口不再依赖 `resp.encoding = "gbk"` 隐式行为，统一改为显式 `content.decode("gbk")` / `json_of(..., fallback_encoding="gbk")`

### 验证

- `pytest -m "not network"`：**584 passed / 7 deselected / 0 failed**（基线 527 + 新增 57 条）
- 静态守卫：8 个数据源域名下**零**绕过统一入口的裸请求
- 真实网络冒烟（10 个第二梯队接口全部返回真实数据）：同花顺热榜 100 条 / 涨停揭秘 77 条 / 涨跌分布 11 档 / 市场涨跌家数 `up=4234 down=1152 flat=167 limit_up=79` / 一致预期 EPS 3 行 / 热点归因 79 只 / 北向 370.47 亿；财联社电报 5 条、巨潮公告 5 条、新浪新闻 5 条
- MCP 工具注册数不变：**129**

## [3.3.19] - 2026-09-18

### Changed

- **东财/腾讯请求统一收敛为单一防封入口**（移植 `astock_scraper` 的 curl_cffi 指纹方案）。改造前本项目存在**四类绕过节流的请求路径**，全局限流形同虚设：
  - `astock_signals/anti_ban_client.py` 用**裸 `requests`**（无任何 TLS 指纹伪装），东财可识别为脚本流量
  - `em_client.py` 与 `anti_ban_client.py` **各持一把节流锁 + 一个 session**，同时取数时实际请求速率是设计值的 2 倍
  - `akshare_fetchers.py` / `astock_signals_fetchers.py` / `news_fetchers.py` / `concept.py` 共 7 处**自建 curl_cffi 会话**（硬编码 UA/Referer，无限流无重试），其中 `concept.py` 的 `_get_region_boards()` 是**分页 while 循环且无任何间隔**
  - `http_fetchers.py` 腾讯走 `urllib.request` + `User-Agent: Mozilla/5.0` **裸奔**，`_tencent_batch_quotes` 按 80 只/批循环拉全市场（约 68 个连续请求无间隔）

### Added

- **`astock_signals/antiban_http.py`**：统一防封 HTTP 层（指纹伪装 + 串行限流 + 指数退避重试 + 限流换指纹）
  - 指纹池 8 个 Chrome 版本（120/124/131/136/142/145/146/150），**UA 与 impersonate 版本严格匹配**（不一致会被服务端识别为伪造）；指纹不被当前 curl_cffi 支持时回退 chrome120 而非崩溃
  - `Throttle` 预定制节流器：锁内只计算并预定时间戳、`sleep` 在锁外执行，高并发下不退化为串行排队
  - **东财 rc 语义识别**：`rc=102` 判为限流（换指纹 + 退避重试），`rc=100` 判为标的不存在（立即返回，不浪费重试）——此前两者都被当异常处理
  - 指数退避 `min(2**attempt + jitter, 30)`，覆盖网络异常 / 403 / 429 / 5xx / rc=102
- **腾讯传输层加固**：`http_fetchers._urlopen_no_proxy` 由 urllib 改为 curl_cffi 会话 + 节流（默认 0.15s + 抖动）+ 退避重试，保留 `.read()` 返回语义（调用方与既有测试零改动）；新增 `tencent_reset_session()`
- **新增 2 个环境变量组**：`TENCENT_RATE_LIMIT_INTERVAL` / `TENCENT_JITTER_MIN` / `TENCENT_JITTER_MAX` / `TENCENT_MAX_RETRY`（默认 2，腾讯是兜底源，重试过多会拉长 worst case）；`EM_MAX_RETRY` 默认 1 → 3
- **`curl_cffi>=0.6.0` 显式声明**：此前仅靠 akshare 传递引入，akshare 一旦调整依赖即静默丢失并导致 `import em_client` 直接 ImportError
- `tests/test_antiban_http.py`（38 条）：指纹/UA 匹配、节流器串行与并发间隔、rc 语义、退避重试、GBK 解码
- `tests/test_antiban_integration.py`（18 条）：含 **AST 静态守卫**——扫描源码确保没有模块绕过统一入口直接请求 eastmoney.com / gtimg.cn（防回退）

### Fixed

- `em_get` 重试耗尽时**返回 None 而非抛异常**穿透到 MCP 层；为 8 处调用点补 None 保护（`em_datacenter` / `em_push2*` / `fetch_market_breadth` / `fetch_industry_quotes` / `fetch_stock_boards` / `fetch_fund_hold_direct` 等），失败路径改为明确的 RuntimeError 或空结果
- `em_client.fetch_stock_boards` 在东财请求失败时返回空 DataFrame，不再因 `None.raise_for_status()` 崩溃

### 验证

- `pytest -m "not network"`：**527 passed / 7 deselected / 0 failed**（基线 471 + 新增 56 条）
- 静态守卫：东财 / 腾讯域名下**零**绕过统一入口的裸请求（改造前 11 处）
- 核心逻辑独立验证：8 线程并发最小间隔 0.1996s（阈值 0.18s）；`rc=102` 重试 3 次且每次触发换指纹；`rc=100` 仅 1 次请求
- 真实网络冒烟：东财 push2 `rc=0` 返回贵州茅台 1257.12（0.56s）；腾讯 GBK 正确解码，两源价格一致（1257.12）
- MCP 工具注册回归：`list_tools()` 仍为 **129 个**，导入链无破坏

## [3.3.18] - 2026-09-18

### Fixed

- **根因修复 MCP 频繁断连**：eltdx 3.x Rust 内核（pyo3 native）panic 时抛出 `PanicException`（继承 `BaseException`，不被 `except Exception` 捕获），穿透 `SmartRouter.route()` 与 `_safe_call`，杀穿 anyio 事件循环导致 MCP server 进程干净退出（Windows 10 天 394 条应用崩溃 0 条 python 佐证），WorkBuddy 报 `-32000 Connection closed` 且永不重连。触发条件为 v3.3.2 的 8 线程池并发 × v3.3.13 的 eltdx Rust 内核单例（native 非线程安全间歇 panic）。

### Changed

- **三层防御（不降级 eltdx）**：
  - L1 `smart_router.route()`：新增 `except BaseException` 统一兜底按源失败降级；`KeyboardInterrupt`/`SystemExit`/`asyncio.CancelledError` 放行（不吞进程/协程取消语义）。
  - L2 `eltdx_fetchers._NativePanicShield`：全局互斥锁序列化所有 native 调用（消除并发竞争触发条件）+ `BaseException→RuntimeError`；`_get_client()` 返回代理，40+ 调用点零改动；标量属性原样返回。
  - L2b `eltdx_stream`：常驻流第二裸 client 同包盾（共享互斥锁）+ init 期兜底。
  - L3 `composite_analysis._safe_call`：`except BaseException` 双保险，`CancelledError` 放行。

### Added

- `tests/test_baseexception_shield.py`：14 条回归测试（先红后绿），覆盖 route() BaseException 穿透、native shield 序列化、标量属性、CancelledError 放行。

### 验证

- `pytest -m "not network"`: 471 passed / 7 deselected / 0 failed。
- 真实 eltdx 10 线程并发零 panic 逃逸；stdio 启动压测 5/5。
- 端到端（HTTP 网关新代码）：`analyze_stock_comprehensive(600519)`（9-17 18:17 死亡现场同款调用）1.6s 完整返回；shield 日志确认激活。

## [3.3.17] - 2026-09-15

### Fixed

- **`mcp>=1.0.0` 无上限约束（P0，新环境安装必炸）**：全部 15 个工具模块使用 mcp v1 API（`mcp.server.fastmcp.FastMCP`），mcp 2.x 已移除该路径；此前 uv/pip 在全新环境会把 mcp 解析到 2.x，`python -m tradex` 直接 ImportError（本机 venv 曾实际踩坑，靠手动 pin 1.30.0 恢复但项目文件未修）。改为 `mcp>=1.0.0,<2`，`pyproject.toml` 与 `requirements.txt` 两处同修。
- **`WS_PORT` 环境变量非数字会在 import 期崩服务**：`int(_get_env("WS_PORT", "8765"))` 绕过了 `_get_env` 的容错转换，`WS_PORT=abc` 在模块加载时直接 ValueError。改用 `_get_env("WS_PORT", 8765, int)`（非法值回退默认）；`WS_SERVER_ENABLED` 一并统一为 `_get_env` bool cast 写法。
- **requirements.txt 缺 3 个运行时依赖**：`requests`/`stockstats`/`python-dotenv`（pyproject 已声明、requirements 漏抄，`pip install -r requirements.txt` 装不齐），补齐。
- **pytest 未来兼容（PytestRemovedIn10Warning ×2）**：`test_v3314_fixes.py`/`test_v3315_fixes.py` 的 class-scoped fixture 由类内实例方法迁至模块级（`scope="class"` 语义不变），pytest 10 可继续运行。

### Changed

- **降级链吞错加日志（10 处）**：`get_market_capitalization`（Tier1/Tier2）与 `news_events`（财报披露/百度经济日历/个股新闻/财联社快讯/新浪财经/百度交易提醒/期货新闻/百度热搜）原 `except Exception: pass` 全静默——多源全挂时只报"所有数据源均不可用"，无从排查各路死因。现统一 `logger.debug` 留痕（单源失败属设计预期，debug 级不刷屏）；`news_events.py` 补 `logger` 定义。

### Removed

- 删除 `tradex/verify_v250.py`：V2.5.0 时代一次性验证脚本（无任何引用），git 历史可追溯。

## [3.3.16] - 2026-09-15

### Fixed

- **机构持仓「列错位」——整列语义全错，但行数正常（数据正确性，影响复盘「机构持仓」章节）**：akshare `stock_report_fund_hold` 用 `big_df.columns = [...]` **按位置**硬编码中文列名，而上游东财 `/dataapi/zlsj/list` 字段顺序已改版 → **每列的语义都错**。实测（直调 akshare、绕开本项目全部代码）：`股票代码` 列里是市值 `-415345718.82`、`股票简称` 列里是代码 `000680`、`持股变动数值` 列里是文本 `减仓`。这类 bug 只判「行数/非空」**必然漏检**（返回 615 行、84KB，看着完全正常）。**1.18.91 与 1.18.94 同样错位** ⇒ 属上游字段漂移 + akshare 位置映射，**非本次依赖升级引入**；此前每年 4–9 月因默认日期非法恒返回空表，所以一直没暴露。
- **`get_fund_hold()` 无参调用稳定超时（P1，默认参数必挂）**：机构持仓主源 `em_zlsj_direct` 是**全量翻页**（东财接口 `pageSize` 硬顶 500，实测传 1000/2000/5000 均只回 500），基金持仓 5311 行 = 11 页 ≈ 17s，超过路由默认单源超时 `_DEFAULT_ROUTE_TIMEOUT = 12.0` → 判超时；而 akshare 兜底已按设计拒用（不再返回错标数据）⇒ 最终报错、无数据。偏偏 **「基金持仓」正是该工具默认 `symbol`** ⇒ 无参调用必然失败。逐类型全量耗时实测：信托 1.4s/158 行、社保 1.8s/615 行、QFII 6.3s/1719 行（已接近上限）、**基金持仓 16.8s/5311 行（超限）**。
- **退役已永久失效的 `index_news_sentiment` 旧主源**（v3.3.15 遗留项）：旧主源走 `ak.index_news_sentiment_scope` → `www.chinascope.com/inews/senti/index`，该上游**已永久失效**（实测端点 301 跳转到官网首页，返回 200 + `text/html` 22,833 字节；akshare 内部 `r.json()` 必然 `JSONDecodeError`。与 SSL 无关，`-k` 绕证书照样是 HTML）。v3.3.15 只修好了它那段从未生效的 SSL 兜底，但源本身已死，留着有害：每次调用先白失败一次（浪费 HTTP 往返）、压低健康分污染看板、且为它必须保留「在锁里篡改进程级 `requests.sessions.Session.request`」这种全局副作用 hack。

### Changed

- **`fund_hold` 的 `hold` 分支改为直取东财 + 按字段名映射**：新增 `fetch_fund_hold_direct`，绕开 akshare 直取上游 datacenter，**按上游字段名映射列**（上游再改字段顺序也不会错位），并复用本项目 `em_get`（含节流 + UA + 反爬）。之所以不能在输出上补救列名——真正需要的 `SECURITY_NAME_ABBR`（股票简称）落在 akshare 标为 `"_"` 的位置，最终 `big_df[[...]]` 选择时被**丢弃**，只能绕开 akshare。
  - **列名刻意沿用 akshare 原有 9 个命名**（只把映射改对），避免静默打断按列名取值的下游报告模板；新增列**只做追加**（`持股占流通股比`）。
  - 加**列错位守卫**：`股票代码` 必须全是 6 位数字，否则**抛错** —— 宁可失败，也不把错标数据交给下游。
  - `fetch_fund_hold_data` 的 `hold` 分支改为**直接 `raise`**（不再作兜底，否则 direct 失败时它会静默返回错标数据，比直接失败更糟）；`detail` 分支保留（该 API 按 key 取，无错位）。
  - `fund_hold` 由**单源升为双源**：`em_zlsj_direct`(1) 主 + `akshare_fund_hold`(100) 备。`detail` 请求走 direct 会抛 `ValueError` → 按设计降级到 akshare，实测 77 行正常。
- **`fund_hold` 路由超时单独放宽**：新增 `_FUND_HOLD_ROUTE_TIMEOUT = 40.0`（`tools/news_events.py`），**仅该数据类型放宽**，其余仍走默认 12s，不拖慢整体降级速度。
- **`index_news_sentiment` 主备对调 + 补跨上游备源**：移除 `akshare_index_sentiment` 注册；把实测可用的乐咕乐股「赚钱效应」`legu_activity` 由备源(100) **提为主源(1)**；补同花顺涨跌分布 `ths_distribution`(100) 作跨上游备源（与乐咕上游不同，非同生共死）。⚠️ 备源必须返 `DataFrame`：工具层 `get_market_sentiment` 写的是 `df is None or df.empty`，若给 dict 会 `AttributeError`。
- 顺带修正 `tools/news_events.py` docstring 两处过时描述（指数情绪源已退役换 `legu_activity` + `ths_distribution`；机构持仓主源为 `em_zlsj_direct`）。
- 代码卫生：`akshare_fetchers.py` 删除 `fetch_index_news_sentiment`、`_ssl_patch_lock` 及仅服务于它的 `import threading`，该文件**不再有任何全局 SSL 篡改**。
- 数据源矩阵规模：**101 → 102**（新增直取主源 `em_zlsj_direct`）；数据类型 **78** 不变；去重源 **36 → 37**；MCP 工具数 **129** 不变。

### Added

- 新增 7 项回归测试（`tests/test_v3315_fixes.py` 32 → 39）：
  - `TestFundHoldDirect`：上游字段**乱序**时仍映射正确 / 列错位守卫必须抛错 / `endpoint` 与 `symbol` 契约。
  - `TestFundHoldRouteTimeout`：断言路由收到放宽后的超时且 **≥30s**、断言默认 `symbol` 恰为最慢那条（防止默认参数再落回慢源）。
  - 「退役守卫」断言：`fetch_index_news_sentiment` / `_ssl_patch_lock` / `threading` 三者必须**真删干净**（而非仅不注册）。
  - P1-1 默认日期用例改打直取版；计数断言 `101→102`、去重源 `36→37`、单源护栏移除 `fund_hold`。

### 验证

- `pytest -m "not network"`：**457 passed / 7 deselected / 0 failed**（8.5s，零回归；基线 450 + 本次新增 7）。
- 代理铁律复核：socket 层审计本地代理端口（7897/10808/9）**0 命中**，全部国内直连。
- 真实联网冒烟（`route()` 逐路口实调，打印**实际命中源**与数据规模）：

  | 数据类型 | 实际命中源 | 结果 | 说明 |
  |---|---|---|---|
  | `fund_hold`（默认 `基金持仓`） | **`em_zlsj_direct`** | **5311 行 × 10 列** | 修复前无参调用因超时必挂；现 15.8s 返回 |
  | `fund_hold`（`社保持仓`） | `em_zlsj_direct` | 615 行 | 列语义正确 |
  | `index_news_sentiment` | **`legu_activity`** | 12 行 × 2 列 | 旧主源已退役，新主源直连可用 |

- **列错位验收（不只判行数）**：`get_fund_hold(symbol="基金持仓")` 首行实测
  `300308 中际旭创 / 持有基金家数 3578 / 持股市值 272,710,423,590 / 持股变动 减仓 / 持股变动比例 -6.89`；
  `股票代码` 列 **5311/5311 全为 6 位数字、0 处错位**。
- **MCP 进程内双确认**（第三次重启后）：运行进程 `health_check` = `healthy`（工具 129 / 数据源 102 / 不健康 0 / akshare 1.18.94）；
  独立 MCP stdio 探针复跑 `基金持仓` **14.99s / 5311 行**，返回体长度（845,528 字符）与运行进程**完全一致** ⇒ 同源同码。

## [3.3.15] - 2026-09-14

### Fixed

- **`fetch_fund_hold_data` 默认日期恒为空表（P1-1，静默数据丢失）**：原实现用 `f"{year}{q*3:02d}31"` 拼上一季度末，Q2/Q3 会算出 `20260631` / `20260931` 这类**不存在的日期**，上游直接报错、又被 `except` 静默吞成空表 —— 结果是每年 4–9 月「机构持仓」不传 `date` 的默认调用恒返回空。现新增 `_prev_quarter_end()`，用 `calendar.monthrange()` 取真实月末（3月→31 / 6月→30 / 9月→30 / 12月→31）。实测修复后默认调用返回 **5311 行**。
- **8 个 fetch_fn 静默吞异常（P1-2，失败不可见、破坏降级链）**：`except Exception: return pd.DataFrame()` 会让 SmartRouter 把「上游挂了」判定为「成功返回空表」—— 既不降级到备源、又给主源计满健康度，故障被伪装成正常。下列 8 个函数改为 `raise`（上抛给 SmartRouter 降级并计健康度）：`fetch_baidu_economic_calendar`、`fetch_baidu_trade_notify`、`fetch_index_news_sentiment`、`fetch_futures_news`、`fetch_hot_search_baidu`、`fetch_hot_rank_data`、`fetch_xueqiu_hot`、`fetch_fund_hold_data`。
- **`fetch_index_news_sentiment` 的 SSL 兜底从未生效**：原实现 patch `ssl._create_default_https_context`（urllib 层），但 akshare 该接口内部走 **requests**，注入点根本不对，那段「兜底」从未起作用。现改为在 `requests.sessions.Session.request` 层注入 `verify=False`，并用锁串行化该窗口。修正后实测：SSL 已绕过（HTTP 200），但上游 `chinascope` 返回 HTML 而非 JSON —— 该上游**已永久失效**，故改用乐咕乐股作为跨上游情绪备源（见 Added）。
- **`industry_comparison` 名义双源、实际同生共死（P2-2）**：主源 `em_push2` 与备源 akshare `stock_sector_fund_flow_rank` **同属东财 push2 族**，本机实测两会同时 `RemoteDisconnected`，即主源失效时备源一起死、整类型无兜底。现补同花顺行业资金流 `ths_flow`（priority=200，跨上游）作为实际可用的一环。

### Added

- **补 7 个类型的独立备源**（一律选**非主源上游**，避免同生共死）。其中 6 类原为**单源**注册、源失效即整类型失效；另 1 类 `industry_comparison` 虽名义双源，但两源同属东财 push2 族、实际同生共死：
  - `baidu_economic_calendar` ← 百度财报披露时间表 `news_report_time_baidu`（该接口默认日期被上游硬编码为 `20251126`，故显式传当日日期）
  - `baidu_trade_notify` ← 全市场停复牌表 `stock_tfp_em`（语义精确对应，实测 911 行）
  - `hot_rank` / `hot_search` / `xueqiu_hot` ← 同花顺人气榜 `ths_fetchers.fetch_ths_hot_list`（三类型共用同一兜底源，实测 100 行）
  - `index_news_sentiment` ← 乐咕乐股「赚钱效应」`stock_market_activity_legu`（跨上游情绪源，替代已永久失效的 chinascope）
  - `industry_comparison` ← 同花顺行业资金流 `stock_fund_flow_industry`（对应上方 Fixed 第 4 条）
- 新增回归测试 `tests/test_v3315_fixes.py`（32 项），覆盖季度末边界、异常上抛、备源注册与优先级、备源适配器契约。全量离线套件：**450 passed / 7 deselected / 0 failed**（基线 418 + 本次新增 32）。

### Changed

- 数据源矩阵规模：**94 → 101**（新增 7 个注册）；数据类型 **78** 不变；去重源 31→36；MCP 工具数 **129** 不变。
- `fund_hold` / `futures_news` **刻意保持单源**（上游分别为东财 datacenter 与上海有色网 `futures_news_shmet`，akshare 无等价第二源），但其静默吞异常问题已一并修复。

### 验证

- `pytest -m "not network"`：**450 passed / 7 deselected / 0 failed**（8.3s，零回归）。
- 真实联网冒烟（`route()` 逐路口实调，打印**实际命中源**与数据规模，非只判真假）：

  | 数据类型 | 实际命中源 | 结果 | 说明 |
  |---|---|---|---|
  | `fund_hold` | `akshare_fund_hold` | 5311 行 × 9 列 | P1-1：修复前默认调用因非法日期恒返回空 |
  | `index_news_sentiment` | **`legu_activity`** | 12 行 × 2 列 | 主源 chinascope 已永久失效，实测**降级到新备源**成功 |
  | `industry_comparison` | **`ths_flow`** | 20 个行业 | 主源 + 备源本轮**同时失败**，降级链救回（P2-2 实证） |
  | `hot_rank` | **`ths_hot`** | 100 行 × 8 列 | 主源东财人气榜失败，降级成功 |
  | `hot_search` | **`ths_hot`** | 100 行 × 8 列 | 主源百度热搜失败，降级成功 |
  | `xueqiu_hot` | `akshare_xueqiu_hot` | 5642 行 | 主源本轮可用（约 8s，偏慢），备源待命 |
  | `baidu_economic_calendar` | `akshare_baidu_economic` | 71 行 × 8 列 | 主源可用，备源待命 |
  | `baidu_trade_notify` | `akshare_baidu_notify` | 6 行 × 12 列 | 主源可用，备源待命 |

- **健康度可见性已验证**（P1-2 的核心目的）：本轮 `industry_comparison:em_push2`、`industry_comparison:akshare`、`index_news_sentiment:akshare_index_sentiment`、`hot_rank:akshare_hot_rank`、`hot_search:akshare_hot_search` 均被如实记为 `fail_count=1`；修复前这些失败会被静默吞成「空表成功」，既不降级也不计健康度。
- 代理铁律复核：socket 层审计本地代理端口（7897/10808/9）**0 命中**，全部国内直连。

## [3.3.14] - 2026-09-14

### Fixed

- **akshare 备源 period 未归一化，导致降级链断裂**：SmartRouter 会把同一个 `period` 参数**原样转发**给主源（eltdx）与备源（akshare），但两源值域不同 —— eltdx 认 `day/week/month`，akshare 认 `daily/weekly/monthly`。eltdx 源内部有 `_normalize_period()` 归一化，akshare 源此前没有，于是传 `period='day'` 时主源正常、一旦降级到 akshare 就 `KeyError('day')`，降级链在该值下必然断裂。现新增 `_normalize_ak_period()` 做反向归一化（`day→daily` / `week→weekly` / `month→monthly`，并兼容 `d/1d/w/1w/m/1m`），使两源周期语义对齐。
- **`HTTP_PROXY=""` 拦不住系统代理，国内数据源被误路由**：`requests.getproxies()` 的实现是 `getproxies_environment() or getproxies_registry()`，当环境变量为空时会**回退读取 Windows 注册表代理**（Internet Settings）。此前 `tradex/__init__.py` 只 `pop` 了代理环境变量，导致 akshare 等基于 requests 的数据源仍走 Clash（`127.0.0.1:7897`），国内东财接口被代理拦截（实测 `ProxyError`）。现显式设置 `NO_PROXY=*` / `no_proxy=*`，使环境变量分支返回非空（`{'no':'*'}`）从而短路注册表读取，彻底绕开系统代理。
- **`_bar_sort_key` 静默降级污染回测首行**：原实现为 `except Exception: return 0.0`，会把取不到时间字段的 K 线 bar 静默排到**最前**（epoch 1970），既污染回测首行、又让异常被完全吞掉。现收窄异常捕获为 `(AttributeError, TypeError, ValueError, OSError)`，命中时记 `warning` 日志并返回 `float('inf')` 排到**末尾**（不丢数据、不污染首行、异常可见）。

### Added

- **4 个单源类型的独立备源**（原本这几类均为 akshare 单源且走东方财富，东财异常时无任何兜底，违反「一主一备」原则；新增备源均刻意选用**非东财**厂商）：
  - `company_info` ← 巨潮资讯 `stock_profile_cninfo`
  - `financial_stmt` ← 新浪财经 `stock_financial_report_sina`（覆盖 profit / balance / cashflow）
  - `valuation` ← eltdx F10 valuation（通达信行情服务器）
  - `industry_data` ← 同花顺 `stock_board_industry_name_ths` / `stock_board_concept_name_ths`
- 新增回归测试 `tests/test_v3314_fixes.py`（20 项），覆盖上述全部修复点。

### Changed

- 数据源矩阵规模：**90 → 94**（新增 4 个独立备源）；MCP 工具数不变（129）。

## [3.3.13] - 2026-09-14

### Changed（上游依赖升级 —— eltdx 跨大版本）

- **eltdx 2.0.2 → 3.2.2（major，Rust 重写内核）**：3.x 将 7709 协议与传输运行核心改为 Rust 强类型实现，发布物从纯 Python 包变为 `cp310-abi3` native wheel（覆盖 CPython 3.10–3.14）。上游承诺 `TdxClient`/`Client`、全部模块化业务 API、公开 dataclass、异常继承、分页、序列化与 `include_raw`、自定义 Transport 注入、Helpers/F10/MCP/CLI 均保留；tradex 实际用到的接口面（`client` 的 `bars`/`minutes`/`quotes`/`trades`/`limits`/`corporate`/`auctions`/`codes`/`session`/`f10`/`helpers`）实测无差异。
- **akshare 1.18.91 → 1.18.94（patch）**。
- `tradex/pyproject.toml` / `tradex/requirements.txt` 依赖下界同步为 `eltdx>=3.2.2`、`akshare>=1.18.94`。
- 依赖检查看板（`start_dashboard.bat` → `GET /api/dashboard`）确认 90 个数据源全部健康，两依赖均 `has_update=false`。

### Fixed

- **eltdx 3.x 移除 `client.bars.all()`**：`fetch_full_kline` 迁移至 `client.bars.get(..., all_pages=True, max_pages=...)`（上游迁移文档指定路径，签名 `get(code, period, start, count, adjust, anchor_date, kind, include_raw, all_pages, page_size, max_pages, batch_size)`）。
- **eltdx 3.x 移除 `client.helpers.adjusted_kline()`**：`fetch_adjusted_kline` 迁移至 `client.bars.get(..., adjust="qfq"/"hfq")`，复权由主站计算（2.x 时代为本地计算复权因子）。
- **`fetch_full_kline` 跨页乱序（数据 bug，直接影响回测）**：eltdx 分页顺序为「最近页 → 更早页」、各页内部升序，直接拼接使 6389 根 K 线全局乱序（首行 2023-05-31、末行 2003-03-19）。现按 `time.timestamp()` 重排为严格升序；修复后区间为 1999-11-10（浦发银行上市首日）→ 2026-09-14。
- **`start_dashboard.bat` 完全不可用（两处缺陷）**：
  1. **解释器探测错误** —— 原脚本用 `where python` 取首个命中，本机命中的是**裸解释器**（`binaries/python/versions/3.13.12`，未安装 tradex），必然报 `No module named 'tradex'`。现按「项目 `.venv` → WorkBuddy 托管 `envs/default` → PATH」顺序探测，且要求 `import tradex.dashboard,eltdx,akshare` 全部成功。
     （说明：仅探测 `import tradex` 不足 —— cwd 为项目根时本地 `tradex/` 目录会被当作 namespace package，在空环境里也能假通过。）
  2. **端口未传递** —— 原脚本只把端口用于打印 URL，而 dashboard 实际读取 `TRADEX_DASHBOARD_PORT`，自定义端口必然打开错误地址。现导出该环境变量；另新增 `--check` 模式（仅探测解释器并打印依赖版本，不启动服务）。

### 验证

- `pytest -m "not network"`：**398 passed / 7 deselected / 1 warning（8.32s）**，与升级前完全一致（零回归）。
- 真实行情 E2E：`fetch_adjusted_kline` 返回 5×7 正确 DataFrame；`fetch_full_kline` 返回 6389 行且严格升序。
- MCP 协议级探针：冷启动 0.77s，`tools/list` 仍为 **129 个工具**（升级前后一致）。
- `start_dashboard.bat --check`：正确选中 `envs/default`，输出 tradex 3.3.13 / eltdx 3.2.2 / akshare 1.18.94。

## [3.3.12] - 2026-09-08

### Added（HTTP 网关原生支持，免 supergateway）

- **`python -m tradex --http` 统一 HTTP 网关**：新增 `tradex/http_server.py`，单端口（默认 8000）同时提供三种端点：
  - `POST /mcp` — **Streamable HTTP** 主端点（MCP 2025 新标准传输，新版 Dify / LangChain / Claude 远程客户端首选），基于 mcp SDK 原生 `streamable_http_app()`，无需 supergateway 等外部转发进程；
  - `GET /sse` + `POST /messages/` — legacy SSE 兼容端点（`sse_app()`，旧客户端过渡）；
  - `GET /health` — 轻量健康检查 `{"status":"ok","service":"tradex-mcp","version":...,"tools":129}`，不触发数据源网络探测（呼应 SmartRouter 健康检测 300s 缓存经验）。
- 原 `--http`（仅 `transport="sse"`）升级为网关；stdio 默认不变（本地 connector 不受影响）。
- **Host 校验可配（DNS rebinding 防护）**：新增 `http_server.apply_transport_security()` + CLI `--allowed-hosts` / env `MCP_ALLOWED_HOSTS`。默认沿用 SDK 仅 localhost 防护（伪造 Host 返回 421）；绑 `0.0.0.0` 对外部署传白名单（自动补 localhost 三件套 + `host:*` 端口通配 + http Origin）；`*` 关闭防护全放行（仅限可信内网，启动打 warning）。`--host/--port` 未显式给出时回退读 `MCP_HOST/MCP_PORT`，env 配置真正生效。
- 双 transport 共用同一 FastMCP 实例无冲突：子 app 路由天然分离（/mcp vs /sse,/messages），父 app lifespan 嵌套执行各自 `session_manager.run()` 初始化 anyio task group。

### Fixed

- **docker-compose healthcheck 失效 bug**：原探测 `http://localhost:8000/mcp`，在 SSE transport 下 `/mcp` 必 404（urllib 对 404 抛 HTTPError）→ 容器恒 unhealthy；改为探测 `/health`。

### Docs

- README 新增「HTTP 远程模式」章节（端点表 + Dify/LangChain 配置 + `pip install "tradex[http]"`）；architecture.md 传输方式同步 stdio + HTTP 双模式。
- 版本 3.3.11 → 3.3.12（VERSION / pyproject / README badge+版本历史 / CHANGELOG 六处同步）。

### 验证

- 手工 E2E：uvicorn 起服后官方 mcp Python client 完整会话（initialize → list_tools=129 → 真实调用 health_check 返回 90 数据源健康）+ curl 验证 /mcp、/sse、/health 三端点。
- 新增 `tests/test_http_server.py`：网关路由注册（/health,/mcp,/sse,/messages 并存）+ /health 字段断言；全量 398 passed / 0 failed（root 365 + tradex/tests 33）。

## [3.3.11] - 2026-09-07

### Changed（P2 技术债全清）

- **B20 指标算法单一实现收敛**：MACD/KDJ/RSI/BOLL/ATR/MA/EMA 计算收敛到 `technical_indicators.py` 模块级函数（`_macd_values/_kdj_values/_rsi_values/_boll_values/_atr_values/_sma/_ema`），`signal_generation.py` 删除 4 组重复实现改复用同一实现，消除双文件漂移。
- **B17 EMA 种子对齐通达信**：EMA 递归首值改为 X[0]（原 SMA 种子早期值偏移），输出自首根起全有效；MACD DIF/DEA/MACD 柱、ATR 随之无前导空值（ATR 仅首根无前收为 null），数值与 pandas ewm 对照误差 0。
- **B15 K 线缓冲真正保留**：`_load_ohlcv` 与 `get_technical_indicator` 不再截断预热缓冲（look_back_days+60），MA60/BOLL 长周期指标可正常计算；对外 data_points 口径仍只报回溯窗口。
- **B16 eltdx period 归一化**：`_normalize_period`（daily/weekly/monthly → day/week/month），消除命名不符导致主源静默降级 akshare 的绕路；非法周期直接抛错可见。
- **B5 eltdx_stream 代理清理改连接级**：代理环境变量仅连接窗口临时移除、`finally` 恢复，不再永久污染进程级代理（同进程 GitHub 调用不受影响）。
- **B4 腾讯行情前缀防重**：`_tencent_quote_vals` 兼容已带前缀代码，杜绝 `szsh600000` 错误 URL。
- **B22 版本比较语义化**：`_has_update` 改数字元组比较（`1.10 > 1.9` 不再词法误判）。
- **B24 市场代码识别鲁棒**：`_market_cn/_pure_code` 按前缀+号段分类（含北交所 920），无法识别返回空串不静默 NaN。
- **B18 财务模糊列落空明确报错**：`get_growth_rates/get_per_share_data` 过滤列落空时返回 error（原静默返全表语义不符）。
- **B23 server 生命周期 + 公共配置**：FastMCP 加 lifespan 关闭钩子（退出清理 eltdx 常驻推送连接）；`__main__ --host/--port` 改公共 `settings.host/port`（原直写私有 `_host/_port`，当前 SDK 根本不读取导致 --port 不生效）。
- **B21 删死代码**：`utils/fallback.py`（call_with_fallback 全库零调用者）删除，备份至 `Claw/_archive-2026-09-07/`。
- **测试**：指标/信号测试断言同步至通达信口径；新增 `tests/test_p2_cleanup_regression.py`（B4/B5/B16/B17/B20/B22/B24 回归），全量 **390 passed / 0 failed**（+33）。

## [3.3.10] - 2026-09-07

### Changed（双源合一 + 深度体检修复）

- **astock_signals 双源合一**：独立仓 `Claw/astock_signals/` 退役，`tradex/src/astock_signals/` 成为唯一主源（补齐 v3.3.2 超时保护/停更检测等落后内容，版本 1.1.1），所有调用统一走框架内主源。
- **P0 计算修复**：`_downside_volatility` 改用标准下行偏差算法（原对负收益以自身均值为中心，Sortino 分母系统性算错）；全正收益策略 Sortino 返回 inf。
- **P1 修复**：
  - `symbol.get_exchange`：可转债 11x(沪)/12x(深)、ETF、沪B(900)、北交所(920) 交易所判定修正；
  - `get_segments_revenue`：symbol 补 `format_em_symbol` 前缀（东财 F10 需带市场标识）+ None 守卫；
  - 逐笔方向：eltdx 真实字段 `side`(buy/sell/neutral) 替代不存在的 `buy_or_sell`（原实时路径 100% 误标 sell），统一归一化 unknown；
  - SmartRouter 故障源半开探测自愈（冷却期后自动复活，防永久拉黑+防雪崩）；
  - 东财限流 `em_client` 加锁（防多线程穿透封 IP）；SSL 全局替换加锁；`register_all_sources`/`register_all_tools` 幂等加固。
- **仓库卫生**：删根 `src/` 空壳目录与 `cn-financial-mcp/` 僵尸目录；删串仓测试 `test_data_router_syntax.py`（测的是另一项目代码）；修复 pytest 合跑 ImportPathMismatchError（移除根 tests/__init__.py）；`config/mcp-servers.json` 3.1.4→3.3.9 同步。
- **测试**：全量 357 passed / 0 failed（此前 6 个失败均修复/清理）；工具数断言 127→129。

## [3.3.9] - 2026-08-18

### Added（数据源扩充 + 本地数据 + 全局直连）

- **全局直连**：`__init__.py` import 时清代理环境变量（HTTP(S)_PROXY 等），国内数据源一律直连，代理只给 git push GitHub。
- **东财限流防封**：新增 `em_client.py`，em_get 统一请求入口（间隔≥1s+随机抖动+会话复用），所有东财接口走限流，避免高频封 IP。
- **东财 slist 板块归属**：`fetch_stock_boards`（个股所属行业/概念/地域 + 龙头股，一次请求拿全）。
- **同花顺数据源**：新增 `ths_fetchers.py`，4 个零鉴权接口（一致预期 EPS / 热点归因 reason / 涨停揭秘 / 热榜）。
- **通达信本地数据**：新增 `tdx_local.py`，读本地 vipdoc .day/.lc5 二进制文件（离线不封 IP），全市场 5900+ 只日线。
- **实时涨跌家数 / 行业涨幅**：`fetch_market_breadth`（push2ex 涨跌分布）+ `fetch_industry_quotes`（push2→push2delay 降级）。

### Added（MCP 工具）

- `get_local_kline` / `get_local_minute`：读通达信本地日线/分钟线（仅回测/历史分析，非盘中实时，盘中实时用实时源）。

### Changed

- 版本三处同步至 3.3.9（VERSION / pyproject.toml / README）。
- 工具数 127→129。

### 验证

- 本地日线实测 5984 条（茅台全历史）；同花顺热点归因 64 只含题材标签；一致预期 EPS 3 年；slist 板块归属 28 板块含龙头。

## [3.3.8] - 2026-08-14

### Added（eltdx 2.0 第二梯队 B 级接入）

按老板规划接入 B 级（有等价源的高精度补充）接口，新增 **6 个 MCP 工具**（工具数 121→127）：

- `eltdx_get_category_quotes`（分类行情：A股涨幅榜/成交额榜/封单榜，实时性强于 akshare）
- `eltdx_get_trading_day`（交易日判定，服务器握手）
- `eltdx_get_opening_match_history`（历史开盘撮合，盘后复盘）
- `eltdx_get_capital_changes`（股本变动历史，复权计算基础）
- `eltdx_get_special_limits_scan`（扫描全市场特殊品种涨跌停）
- `eltdx_get_f10_extra`（F10 通用入口，覆盖估值/题材行情/总评/盈利预测/排名/治理/增减持/主营构成/公告/新闻，通达信编码字段降级源）

### Changed
- 版本三处同步至 3.3.8（VERSION / pyproject.toml / README）。

### 决策
- C 级（服务器文件/连接心跳/旧接口/底层万能口）按老板指示**不接入**。

### 验证
- 非网络全量测试 27 passed / 0 failed。
- 分类行情实测返回实时涨幅榜（北交所 29.97%、创业板 20% 等）。

## [3.3.7] - 2026-08-14

### Added（eltdx 2.0 能力大规模接入 · 第一梯队 S+A 级）

按老板规划，将 eltdx 2.0 的第一梯队（S+A 级）能力接入 tradex，共新增 **19 个 MCP 工具**（工具数 101→121）：

**S 级（推送/盘口，交易看板底座）**
- 新增 `data_sources/eltdx_stream.py`：`EltdxStreamManager` 常驻连接管理器（start/subscribe/poll 增量/snapshot 五档/unsubscribe/health/stop）。
- 关键实测结论：eltdx 2.0 常驻连接/后台读线程/心跳保活/推送帧入队已由 PooledSocketTransport 托管；免费行情站不主动推送，增量靠 refresh_stream(0x0547) 游标机制（cursor=update_time_raw）。
- 新增 `eltdx_get_depth`（五档盘口快照）、`eltdx_stream_health`（流健康状态）。

**A 级（查询类，9 个数据域）**
- `eltdx_get_security_codes`（全市场证券代码表，5552 只A股精确分类）
- `eltdx_get_minute_history`（历史分时 240 点）+ `eltdx_get_buy_sell_strength`（买卖强度）
- `eltdx_get_today_ticks`（当日逐笔）+ `eltdx_get_opening_match`（9:25 开盘撮合）
- `eltdx_get_full_kline`（全量K线）+ `eltdx_get_adjusted_kline`（复权K线）
- `eltdx_get_stock_profile`（全景档案：行情+财务一表）
- `eltdx_get_shortline_indicators`（21 项短线打板指标）
- `eltdx_get_finance_batch`（批量财务）+ `eltdx_get_special_limits`（特殊涨跌停）
- `eltdx_get_finance_report` / `eltdx_get_dividend_financing` / `eltdx_get_company_news` / `eltdx_get_northbound_holding`（F10 基本面，通达信编码字段，降级补充源）
- `eltdx_get_stock_topics`（个股题材）+ `eltdx_get_topic_stocks`（题材成分股）+ `eltdx_get_auction_data`（竞价汇总）

### Changed
- 版本三处同步至 3.3.7（VERSION / pyproject.toml / README）。

### 验证
- 非网络全量测试 27 passed / 0 failed。
- 新增工具逐一端到端实测通过（五档盘口/历史分时/逐笔/复权K线/全景档案/短线指标/批量财务/题材/竞价等均返回真实数据）。

## [3.3.6] - 2026-08-14

### Changed（上游依赖升级）

- **eltdx 1.2.0 → 2.0.2（major breaking change）**：2.0 版移除了旧式 `client.get_quote()/get_kline()/get_minute()/get_trades()` 接口，统一为模块化 API（`client.codes/quotes/bars/minutes/trades/auctions/corporate/resources`）。tradex 唯一旧式调用 `fetch_realtime_quote` 已从 `client.get_quote()` 迁移至 `client.helpers.full_quotes()`（返回 `list[QuoteSnapshot]`，字段与旧接口一致）。
- **akshare 1.18.81 → 1.18.91（patch）**：av 依赖更新至 `akshare>=1.18.91`。
- `tradex/pyproject.toml` 依赖同步为 `akshare>=1.18.91`、`eltdx>=2.0.2`；`tradex/requirements.txt` 同步。
- 版本三处同步至 3.3.6（VERSION / pyproject.toml / README）。

### 验证
- 非网络全量测试：25 passed / 2 failed（2 个失败为预存工具数断言 101≠100，与本次升级无关）。
- eltdx 迁移后无回归。

## [3.3.5] - 2026-08-14

### Fixed（盘后复盘「量能对比」成交额数据源修复）

- **修复 `get_index_volume_compare` 成交额为 null 的问题**：原 `fetch_index_daily_amount` 主源走腾讯 `stock_zh_index_daily_tx`（仅返回成交量、无成交额字段），导致盘后复盘量能柱状图拿不到「成交额(亿元)」。现主源改为**东财 push2his 历史K线直连**（字段 f57=成交额），腾讯降级为备源。
- 新增 `_fetch_index_daily_from_push2his` 辅助函数：curl_cffi 绕过系统代理直连 `push2his.eastmoney.com/api/qt/stock/kline/get`，三指数（sh000001/sz399001/sz399006）均返回真实成交额。
- 版本三处同步至 3.3.5（VERSION / pyproject.toml / README）。

### 验证
- `py_compile` 通过，无回归。
- 三指数 6 日成交额序列验证通过：上证 6401 亿、深证成指 7472 亿、创业板 3583 亿（2026-08-14）。

## [3.3.4] - 2026-08-13

### Added（盘后复动量能对比数据源）

- **新增 `get_index_volume_compare(days=6)` MCP 工具**：返回上证/深证/创业板各自近 `days` 个交易日的日成交额序列（亿元），供盘后复盘「量能对比」柱状图使用。
- 新增数据源 `fetch_index_daily_amount`（akshare `stock_zh_index_daily_tx_js`，含成交额字段），注册路由 `index_daily_amount`（akshare 主源）。

### 验证
- `py_compile` 通过，无回归。

## [3.3.3] - 2026-08-13

### Changed（撤销逐笔方案 + 提示词去逐笔要求）

- **撤销 D(P1) 逐笔盘后数据方案**: 经核实，eltdx 逐笔（trades.history）收盘后服务器关闭当日数据窗口、本地无缓存；通达信盘后数据下载仅含日线/1分钟/5分钟、不含逐笔，且 1/5 分钟线占用大量硬盘，不可行；盘中录制会占用机器资源、可能影响实盘交易，故放弃该路线。
- `eltdx_fetchers.fetch_tick_data` 回退至纯实时取数（移除默认今日 + TickStore 回退死代码，恢复 `no ticks on {date}` 语义）。
- 已删除「盘中逐笔录制」自动化任务，并停用 `scripts/record_ticks.py`（移入 `scripts/_retired/`，未永久删除）。
- **盘后复盘提示词 v3.6 去逐笔**: 模块⑤采集项改为「数据源限制，暂不采集」；模块⑧自选股复盘删除「逐笔分析(eltdx)」维度；已知问题表删除 `eltdx tick` 注释。报告不再因无逐笔而标「暂缺」。

### 验证
- `py_compile` 通过，无回归。

## [3.3.2] - 2026-08-13

### Fixed（盘后复盘「多个数据缺失」根因修复，5 类）

- **A(P0) 指数代码解析错乱**: `utils/symbol.py` 新增指数代码白名单（`sh000001`/`sz399001`/`sz399006`/`sh000300`/`sh000688`/`sh000905`/`sz399852`），`get_exchange`/`format_*`/`get_market_name`/`is_valid_a_share_code` 正确识别指数，不再误判成股票（如 `sh000001`→平安银行）。`_get_market_overview_sync` 兼容 akshare(代码/名称) 与 tencent_http(指数名称/最新点位) 两种列名，主源失败降级腾讯后不再 KeyError 被吞 → 模块一三大指数恢复。
- **C(P0) 慢源挂起卡死 MCP**: `astock_signals.smart_router.route()` 新增 `timeout`（默认 12s）线程池包裹，慢源超时即判失败并自动降级下一源；`composite_analysis._safe_call` 同步函数改 `run_in_executor + asyncio.wait_for(30s)`，事件循环不再被阻塞。彻底解决 akshare 无内部 timeout 的 HTTP 调用无限挂起问题。
- **B(P1) 北向资金停更**: `astock_signals.northbound.get_northbound_flow_json` 新增「多日数值一致」检测，冻结值标 `discontinued` 并提示「沪深港通自2024-08-19起已停止披露，仅供参考」；`get_north_bound_flow` 工具明确输出「已停更」而非喂假数。
- **D(P1) 逐笔盘后无数据**: `eltdx_fetchers.fetch_tick_data` 默认交易日为今日，live 取不到时回退 TickStore 库读取（盘中录制、盘后取库）；新增 `scripts/record_ticks.py` 盘中录制脚本，并注册「盘中逐笔录制」自动化（交易时段整点运行）。——注：该 D(P1) 方案已于 3.3.3 撤销（详见 3.3.3）。
- **E(P2) 自动化配置漂移**: 盘后复盘自动化 prompt 从 v3.5 模板改为引用 v3.6（强制串行+健康探针+重试+完整性闸门）。

## [3.3.1] - 2026-08-03

### Fixed
- **get_money_flow**: 4只股票（601868/601390/600170/603077）全部通过 curl_cffi 直连绕过系统代理，修复 ProxyError
- **get_financial_calendar date 过滤失效**: 各源独立过滤后合并，避免 stock_report_disclosure 的2022年旧数据拖垮百度经济日历
- **search_news 稳定性增强**: 个股新闻失败时自动降级到全市场源（财联社+新浪+期货+热搜），不再依赖单源
- **get_sector_fund_flow 字段解析**: 主源东财 push2 接口 curl 56 错误，加 impersonate 仍不可用；新浪备源字段从 4 个扩充到 7 个（板块/涨跌幅/涨跌额/总成交额/总成交量/公司家数/平均价格）
- **get_realtime_quote 外围行情代理失败**: P0 修复，腾讯接口强制直连绕过系统代理
- **get_company_announcements 公告过滤**: P0 修复，_resolve_org_id 兼容带 SH 前缀的代码，Python 端二次过滤
- **pyproject.toml 版本同步**: 从 3.1.4 同步到 3.3.1（与 VERSION 文件一致）


### Changed
- **astock_signals 合并**: 将独立包 astock_signals v1.1.0 源码合并到本仓库，位于 tradex/src/astock_signals/，与 tradex 包统一版本管理
- **pyproject.toml**: packages 新增 src/astock_signals，打包时一同构建

### 验证
- 33 项单元测试通过，1 项跳过（需网络访问），无回归
- 19 项功能验证全部通过（4 只股票资金流 + 4 个新闻源 + 2 个日历源 + 板块资金流备源 + 外围行情 + 公告过滤）

## [3.3.0] - 2026-08-03

### Added
- **新闻资讯数据源扩充：9 个新增数据源**
  - `baidu_economic_calendar`：百度经济数据日历（akshare）
  - `baidu_trade_notify`：百度交易提醒（停复牌/分红派息/财报发行时间，akshare）
  - `index_news_sentiment`：指数新闻情绪评分（akshare）
  - `futures_news`：期货/大宗商品新闻（上海有色网，akshare）
  - `sina_finance_news`：新浪财经新闻直连（HTTP API）
  - `hot_search`：百度股市通热搜股票排行（akshare）
  - `hot_rank`：东方财富人气榜/飙升榜/热门关键词（akshare，7 合 1 多 endpoint）
  - `xueqiu_hot`：雪球关注/讨论/交易热度排行榜（akshare，3 合 1 多 endpoint）
  - `fund_hold`：机构持仓数据（基金/QFII/社保/券商/保险/信托，akshare，2 合 1 多 endpoint）
- **同花顺问财数据源（可选依赖）**：
  - `wencai_query`：同花顺问财自然语言查询（pywencai 可选依赖 + Node.js）
  - `wencai_news`：同花顺问财新闻/公告/研报搜索（iwencai OpenAPI，需 IWENCAI_API_KEY）
- **新增 9 个 MCP 工具（工具数 90 → 99）：**
  - `get_market_sentiment`（第91个）— 指数新闻情绪
  - `get_futures_news`（第92个）— 期货/大宗商品新闻
  - `get_hot_rank`（第93个）— 东财人气榜/飙升榜
  - `get_hot_keywords`（第94个）— 个股热门关键词
  - `get_xueqiu_hot`（第95个）— 雪球关注/讨论/交易热度
  - `get_fund_hold`（第96个）— 机构持仓数据
  - `get_hot_search`（第97个）— 百度热搜股票排行
  - `get_wencai_query`（第98个）— 同花顺问财自然语言查询
  - `get_wencai_news`（第99个）— 同花顺问财新闻/公告/研报搜索
- **新增数据类型**：数据类型从 27 个增加到 38 个（+11 个）

### Changed
- **get_financial_calendar**：新增百度经济数据日历作为补充源（财报披露 + 经济数据合并）
- **search_news**：全市场搜索新增百度交易提醒 + 期货新闻 + 新浪财经 + 百度热搜
  - 搜索源顺序：财联社快讯 → 巨潮公告 → 百度交易提醒 → 期货新闻 → 新浪财经 → 百度热搜 → 财新网 → CCTV
- **data_sources/__init__.py**：新增 wencai_fetchers 子模块说明
- **data_sources/registry.py**：注册表从 27 个数据类型扩展到 38 个

### 验证
- `python -m tradex` 启动：99 个工具注册
- 9 个 akshare 数据源实战验证均可返回非空数据
- 同花顺问财数据源在 pywencai/API Key 未配置时友好降级（返回空）
- 现有 350+ 测试用例全部通过，无回归

### 升级指引
- `pip install -e . --force-reinstall --no-deps` 重新安装 tradex 包
- 如需使用同花顺问财查询工具：`pip install pywencai`（需 Node.js v16+）
- 如需使用同花顺问财新闻搜索：设置环境变量 `IWENCAI_API_KEY`
- 新工具均在 MCP 客户端重启后自动加载

## [3.2.0] - 2026-08-03

### Added
- **新闻资讯数据源增强：3 个直连数据源**
  - `em_news_direct`：东财 search-api-web JSONP 直连个股新闻，替代 akshare 间接调用
  - `cls_telegraph`：财联社 cls.cn 实时电报，获取全市场 7×24 小时财经快讯
  - `cninfo_direct`：巨潮 cninfo.com.cn 官方全量公告，证监会指定信息披露平台
- **新增 MCP 工具**：`get_telegraph_news`（第 90 个工具）— 全市场实时财经快讯
- **新增数据类型**：`telegraph_news`（实时电报）、`cninfo_announcement`（全量公告）
- **数据源矩阵扩展**：从 25 个数据类型增加到 27 个

### Changed
- **get_stock_news**：数据源从 akshare 间接调用改为 em_news_direct 直连，akshare 降为备源
- **get_company_announcements**：数据源从 akshare 间接调用改为 cninfo_direct 直连
- **search_news**：全市场搜索新增财联社快讯和巨潮公告作为搜索源，搜索源顺序：财联社快讯 → 巨潮公告 → 财新网 → CCTV

### 验证
- `python -m tradex` 启动：90 个工具注册
- `get_stock_news("600519")`：返回东财直连个股新闻
- `get_telegraph_news()`：返回财联社实时快讯
- `get_company_announcements("600519")`：返回巨潮公告
- `search_news("业绩预增")`：多源合并结果

### 升级指引
- `pip install -e . --force-reinstall --no-deps` 重新安装 tradex 包
- 新工具 `get_telegraph_news` 在 MCP 客户端重启后自动加载

## [3.1.4] - 2026-08-02

### Fixed
- **P1: eltdx realtime_quote 语义不完整**
  - 根因：原实现用 `client.bars.get(count=1)` 取 K 线最后一根作为"实时行情"，缺涨跌幅/涨跌额/昨收/内外盘/现手等实时字段
  - 修复：改用 `client.get_quote()` 获取真正的 `QuoteSnapshot`，字段完整：最新价/昨收/今开/最高/最低/涨跌额/涨跌幅/成交量(手)/成交额/内盘/外盘/现手
- **P1: 装饰器注册机制死代码导致 health_check/list_all_tools 误报**
  - 根因：`ToolRegistry` 设计了 `@register_tool` 装饰器双轨制，但无任何工具使用，`_tools` 字典永远为空
  - 影响：`list_all_tools` 返回 `{"status":"empty","total":0}`；`health_check` 永远报 "无装饰器注册工具" 状态为 `degraded`
  - 修复：`list_all_tools` 和 `health_check` 改为从 `mcp.list_tools()` 获取工具列表（v3.1.4 起）
- **P1: architecture.md 文档与代码不符**
  - 根因：文档描述 `data_sources/smart_router.py` 和 `providers/` 子目录，实际不存在
  - 修复：改为实际的 4 个 fetchers 模块结构（eltdx/akshare/http/astock_signals_fetchers.py），注明 SmartRouter 在 astock_signals 包内
- **P1: 源名标识不准**
  - 根因：`etf_data` / `cb_data` 的源名注册为 `"akshare"`，但 fetcher 实际来自 `astock_signals_fetchers`
  - 修复：源名改为 `"astock_signals"`，监控面板显示更准确
- **P2: _client_lock 线程安全**
  - 根因：`_client_lock` 是布尔值标志，多线程下有竞态条件
  - 修复：改用 `threading.Lock()` + 双重检查模式
- **P2: ETF 列名重复 warning**（astock_signals 包）
  - 根因：`ak.fund_etf_spot_em()` 偶尔返回重复列名，触发 pandas warning
  - 修复：在 rename 前加 `df.loc[:, ~df.columns.duplicated()]` 去重

### 验证
- `python -m pytest tests -q`：334 passed
- `python -m pytest tradex/tests -q`：33 passed, 1 skipped
- 端到端实测 `get_realtime_quote`：返回完整字段（涨跌额-11.16/涨跌幅-0.82/昨收/内盘/外盘/现手）
- 端到端实测 `health_check`：status 从 `degraded` 变为 `healthy`（issues=[]，tools.total=89）
- 端到端实测 `list_all_tools`：status 从 `empty` 变为 `ok`（total=89）

### 升级指引
- `pip install -e . --force-reinstall --no-deps` 重新安装 tradex 包
- astock_signals 包本地源码已修复（editable 安装直接生效），无需重新发版

## [3.1.3] - 2026-08-02

### Fixed
- **P0 bug：SmartRouter 参数名不匹配导致 eltdx 主源永远失败**
  - 根因：`SmartRouter.route(**kwargs)` 原样转发参数，但不同 fetcher 参数名不一致（eltdx 用 `code=`，akshare/http 行情类用 `symbol=`），导致工具层 `route("realtime_quote", symbol=...)` 传 `symbol=` 时，eltdx fetcher 收到 `code=""` 失败，SmartRouter 一直降级到 akshare 全量快照（14 秒 vs eltdx 200ms）
  - 修复：所有行情/信号类 fetcher 同时接受 `symbol` 和 `code`，内部归一化（`code = code or symbol` 或 `symbol = symbol or code`）
  - 影响 fetcher：`eltdx_fetchers.py`（6 函数）、`akshare_fetchers.py`（3 函数）、`http_fetchers.py`（2 函数）、`astock_signals_fetchers.py`（8 函数）
- **P0 bug：eltdx KlineBar 字段映射错误**
  - 根因：`eltdx_fetchers.py` 用 `getattr(b, "date", None)` 和 `getattr(b, "volume", None)`，但 eltdx 1.2.0 的 `KlineBar` 实际字段是 `time` 和 `volume_lots`
  - 现象：`eltdx_get_kline` 工具返回 `date="None"`、`volume=0.0`
  - 修复：字段映射改为 `time`（日期）和 `volume_lots`（成交量）

### Added
- 新增 `tests/test_fetcher_param_compat.py`：17 个参数归一化回归测试
  - 覆盖 eltdx/akshare/http/astock_signals 四类 fetcher 的 symbol/code 兼容性
  - 覆盖 KlineBar 字段映射（time/volume_lots）
  - 覆盖 SmartRouter 端到端参数路由（含降级场景）

### 验证
- `python -m pytest tests -q`：334 passed（317 旧 + 17 新）
- `python -m pytest tradex/tests -q`：33 passed, 1 skipped
- 端到端实测：`get_realtime_quote(symbol=600519)` 路由到 eltdx 主源（200ms，单股返回，成交量 55127.52）
- 端到端实测：`get_historical_price(symbol=600519)` 路由到 eltdx 主源，日期/成交量字段正确

### 升级指引
- 无需手动操作，`pip install -e . --force-reinstall --no-deps` 重新安装 tradex 包即可
- eltdx 主源现在真正生效，行情类查询性能提升约 70 倍（14s → 200ms）

## [3.1.2] - 2026-08-02

### Removed
- 删除 `src/` 目录（v2.x 遗留死代码）：
  - `src/__init__.py`：引用 `data_manager`，链式依赖已删的 `eltdx_provider`，导致 `import src` 失败
  - `src/data_manager.py`：v2.x 多数据源管理器，引用已删除的 `src/eltdx_provider`（v3.1.0 删除），功能已被 tradex 包的 `data_sources/` + SmartRouter 替代
  - `src/market_analyzer.py`：v2.0 全市场分析引擎，功能已被 tradex 包的 `analysis_engine.py` 等替代
- 版本号 3 处同步调整：`src/__init__.py` 删除后，版本号来源变为 `VERSION` 文件（单一事实来源）+ `tradex/pyproject.toml` + `tradex/src/tradex/__init__.py`（动态读 VERSION）

### Changed
- `tradex/src/tradex/tools/diagnostics.py` 的 `_get_router()`：移除 `from src.astock_signals.smart_router import get_router` 的 fallback 死代码（`src/astock_signals/` 在 v3.1.0 已删除独立成包），简化为直接 `from astock_signals.smart_router import get_router`

### 验证
- `import tradex` 正常（v3.1.2）
- `_get_router()` 返回 SmartRouter 对象（astock_signals 独立包导入正常）
- tests: 317 passed
- tradex/tests: 33 passed, 1 skipped
- 无 warning

### 升级指引
- 无需手动操作，`src/` 目录删除不影响 tradex 包运行（tradex 是独立包）

## [3.1.1] - 2026-08-02

### Fixed
- 注册 pytest `network` marker：消除 tradex/tests 中 4 个 `PytestUnknownMarkWarning`
- 根因：`tradex/pyproject.toml` 有独立 `[tool.pytest.ini_options]`，pytest 运行 tradex/tests 时 rootdir=tradex/ 读取 tradex/pyproject.toml 而非根配置
- 修复：在根 `pyproject.toml` 和 `tradex/pyproject.toml` 同时注册 `markers = ["network: ..."]`

### 升级指引
- 无需手动操作，配置文件随版本更新

## [3.1.0] - 2026-08-02

### BREAKING CHANGES
- 项目改名：仓库目录 `trader-finance-hub` → `tradex-hub`，Python 包 `cn_financial_mcp` → `tradex`，GitHub 仓库名同步
- astock_signals 独立成包：从 `tradex-hub/src/astock_signals/` 独立为 `trae_projects/astock_signals/`（pip install -e 安装）
- 所有 L1 工具走 SmartRouter：L1 数据获取工具不再直接 import akshare/eltdx，统一通过 SmartRouter.route() 选择数据源
- eltdx 升为行情类第一主源：实时行情/历史K线/分时数据，eltdx(TCP) 为主源，akshare 降为备用
- MCP 工具入口变更：`python -m cn_financial_mcp` → `python -m tradex`

### Added
- 数据源看板：MCP 工具 `get_data_source_dashboard` 返回 JSON + HTML 单页可视化（`python -m tradex.dashboard`，端口 8765）
- 数据源版本检查模块 `data_source_monitor.py`：eltdx GitHub release + akshare PyPI 版本检查（只提醒不升级）
- SmartRouter 独占源标记（exclusive=True）：集合竞价/逐笔成交/F10/涨停归因/解禁日历/涨停板 6 个独占源失败不降级
- SmartRouter `get_registry_report()` 方法：返回全量注册表供看板使用
- data_sources 数据源层：akshare_fetchers / eltdx_fetchers / http_fetchers / astock_signals_fetchers / registry，25 数据类型 34 源注册
- HTTP 防封参数环境变量化：EM_RATE_LIMIT_INTERVAL / EM_JITTER_MIN / EM_JITTER_MAX / EM_MAX_RETRY
- anti_ban_client 新增 set_jitter_range() / set_max_retry() 函数

### Changed
- akshare 升级 1.18.80 → 1.18.81
- eltdx 版本标注更新 1.0.2 → 1.2.0
- 25 个数据类型全量注册到 SmartRouter（原仅 3 个）
- 89 个 MCP 工具（原 88，+1 看板工具）
- MCP_HOST 默认值 0.0.0.0 → 127.0.0.1（安全默认）
- trader-data-router 下游适配：删除 sys.path 路径探测，直接 import astock_signals
- quantterminal/tfhub_service.py 迁移：从已删除的 eltdx_provider 改为 eltdx.TdxClient 直调

### Removed
- 删除 `src/eltdx_provider.py` 孤儿模块（v2.0.0 时代老封装，无人调用）
- 删除所有 `sys.path.insert` hack（8 个文件，改正式 import astock_signals）
- 删除 `src/astock_signals/` 旧目录（已迁移为独立包）

### 升级指引
1. `pip install -e trae_projects/astock_signals/`（安装独立包）
2. `pip install -e tradex-hub/tradex/`（重装改名后的 tradex 包）
3. 更新 MCP 配置：server 名改为 `tradex`，args 改为 `["-m", "tradex"]`
4. `pip install -U akshare==1.18.81`
5. 访问数据源看板：`python -m tradex.dashboard`

## [3.0.0] - 2026-08-01

### BREAKING CHANGES
- 版本号统一：tradex v2.5.1 → v3.0.0，astock_signals v0.4.0 → v1.0.0，新增 VERSION 文件作为单一事实来源
- 模块激活：smart_router / tick_store / ws_server 从"独立僵尸"变为"主流程组件"
- 代码重复消除：删除 utils/em_client.py，统一使用 astock_signals.anti_ban_client
- 文件拆分：signal_data.py 拆分为 5 个子模块（signal_data_base/flow/etf/cb/board）
- 安全默认值：MCP_HOST 默认值从 0.0.0.0 改为 127.0.0.1

### Added
- VERSION 文件作为版本号单一事实来源
- L2 计算引擎层：technical_indicators（6 工具）、performance_metrics（2 工具）
- L3 决策支持层：signal_generation（3）、factor_analysis（2）、stock_screening（2）、diagnostics（4）、composite_analysis（3）、analysis_engine（1）
- smart_router 接入数据源自动选择
- tick_store 接入 eltdx_get_ticks 数据落盘
- ws_server 作为可选推送服务（WS_SERVER_ENABLED 控制）
- anti_ban_client 锁内 sleep 并发修复
- tests/test_anti_ban_client.py 并发测试

### Changed
- 旧代码原地重构：data_manager.py / market_analyzer.py / eltdx_provider.py（print→logging、Type Hint、except 细化）
- architecture.md 升级到 v3.0.0，补充 L2/L3 三层架构说明
- README.md 工具数纠正为 88

### Fixed
- em_client.py 与 anti_ban_client.py 代码重复导致节流计数分裂
- anti_ban_client.em_get 锁内 sleep 高并发阻塞
- MCP_HOST 默认 0.0.0.0 公网暴露风险
- architecture.md 严重过时（v2.3.0）
- README 工具数不一致（80 vs 实际 88）

### Removed
- utils/em_client.py（与 anti_ban_client.py 重复）
- 硬编码版本号（改为从 VERSION 文件读取）

### 升级指引
- 从 v2.5.1 升级到 v3.0.0 注意事项：
  1. 如果代码 import 了 em_client，改为 import astock_signals.anti_ban_client
  2. MCP_HOST 默认改为 127.0.0.1，外网部署需显式设置 MCP_HOST=0.0.0.0
  3. signal_data.py 已拆分为 5 个子模块，但兼容入口保留，旧 import 仍可用
  4. astock_signals __version__ 从 0.4.0 升到 1.0.0

## [2.5.1] - 2026-08-01

### Added
- eltdx K线数据接口：新增 `KlineBar`/`KlineData` 数据类 + `get_kline()` 方法（通达信TCP协议直连）
- `.coverage` 加入 `.gitignore`

### Fixed
- 修复 eltdx_provider.py 缺少 K 线数据获取能力

## [2.5.0] - 2026-07-26

### Added - 智能决策中台升级（15个新工具）
本次升级将 Trader Finance Hub 从「数据中台」升级为「智能决策中台」，
新增量化计算工具组（quant_tools），提供技术指标计算、绩效分析、
信号生成、因子分析、条件选股五大能力。

#### P0：技术指标计算模块（6个工具）— `technical_indicators.py`
- `calculate_ma_ema` - MA/EMA 均线计算（纯函数，输入价格数组）
- `calculate_macd` - MACD 指标计算（DIF/DEA/MACD柱）
- `calculate_kdj` - KDJ 随机指标（K/D/J值）
- `calculate_rsi` - RSI 相对强弱指数（Wilder 平滑法）
- `calculate_boll` - BOLL 布林带（含带宽/%B）
- `calculate_atr` - ATR 平均真实波幅

#### P0：绩效指标计算模块（2个工具）— `performance_metrics.py`
- `calculate_performance` - 完整绩效报告（21项指标）
  覆盖收益/风险/风险调整收益/交易质量/费用统计/基准对比
- `list_performance_metrics` - 绩效指标清单查询

#### P1：信号生成模块（3个工具）— `signal_generation.py`
- `generate_trading_signal` - 单票信号（5级信号+评分+多指标组合）
- `scan_stocks_for_signals` - 批量扫描信号（按评分排序）
- `validate_signal_quality` - 信号前瞻收益验证

#### P2：因子分析模块（2个工具）— `factor_analysis.py`
- `calculate_factor_score` - 多因子综合评分（5类22因子，Z-Score标准化）
- `get_factor_catalog` - 因子库清单查询

#### P2：条件选股模块（2个工具）— `stock_screening.py`
- `screen_stocks` - 条件选股扫描（5类30+条件，AND组合）
- `get_screening_conditions` - 选股条件清单查询

### Changed
- `server.py`: 注册5个新工具模块，工具总数 65 → 80
- 升级定位：从「数据中台」升级为「智能决策中台」
- 架构升级：新增「计算引擎层」(L2) 和「决策支持层」(L3)

### Fixed
- 修正 README.md 工具数不一致问题（实际65个，README标61个）

## [2.4.0] - 2026-07-23

### Fixed
- 修复6个核心接口失败问题（东财风控导致的 Connection aborted）
- get_money_flow: 改用东财push2直连API，保留AKShare兜底
- get_market_overview: 改用新浪财经作为主源，东财兜底
- get_sector_fund_flow: 增加同花顺作为备用数据源
- get_technical_indicator: K线数据源增加腾讯备用
- get_north_bound_flow: 添加日期排序，确保最新数据在前
- get_margin_trading: 增加东财市场汇总接口作为主源

### Changed
- 新增 em_client.py: 从astock_signals提取东财push2防封客户端到MCP utils
- astock_signals/__init__.py: ETF/可转债模块改为延迟导入，避免akshare缺失导致整个包无法导入
- 工具总数: 61 → 65（新增3个涨停板工具）

## [2.3.2] - 2026-06-29

### Changed
- 清理 workbuddy 遗留路径，4个硬编码文件改为相对路径/项目目录
- 删除 .workbuddy/ 目录及遗留文件
- 更新 config/mcp-servers.json，移除 WorkBuddy 引用
- 新增 limit_up_board.py 涨停板分析模块

## [2.3.1] - 2026-06-24

### Fixed
- 修复文档与代码不一致的4个高优先级问题
- 修复README.md版本/工具数与代码不一致（57→61工具）
- 修复architecture.md架构文档严重过时，全面更新为当前架构
- 修复signal_data.py内部注释不一致（工具数量和编号）
- 修复astock_signals/__init__.py模块清单不全（11→14个模块）
- 修复tradex/README.md严重过时（42→61工具）
- 修复tradex/tests/test_server.py测试过时（42→61工具）
- 修复README.md数据源描述不准确（AKShare 50→56工具）

### Changed
- 文档全面更新，准确反映v2.3.0版本的实际架构
- 测试用例更新，验证61个工具的正确注册
- 新增文档修复报告（docs/documentation-fix-report.md）

## [2.3.0] - 2026-06-24

### Added
- 新增 `astock_signals/etf.py` — ETF 数据模块（实时行情/历史K线/ETF列表，AKShare fund_etf_spot_em/fund_etf_hist_em/fund_etf_category_sina）
- 新增 `astock_signals/convertible_bond.py` — 可转债数据模块（实时行情/价值分析/比价表/详情，AKShare bond_zh_cov/bond_zh_cov_value_analysis/bond_cov_comparison/bond_zh_cov_info）
- 新增 `astock_signals/smart_router.py` — 智能路由引擎（健康评分/自动降级/延迟感知/故障隔离）
- 新增 `astock_signals/tick_store.py` — Tick 数据本地存储（SQLite WAL模式/分表/去重/时间过滤）
- 新增 `astock_signals/ws_server.py` — WebSocket 实时推送服务器（行情/异动/tick推送，按代码订阅）
- 新增 4 个 MCP 工具：`get_etf_realtime_data` / `get_etf_kline_data` / `get_cb_realtime_data` / `get_cb_value_analysis_data`
- Router 新增 3 个 thin CLI 命令：`etf` / `cb` / `tickstore`（14 → 17 命令）
- 新增 69 个 pytest 测试用例，全部通过

### Changed
- astock_signals 版本 0.2.0 → 0.3.0，模块数 9 → 14（含 smart_router/tick_store/ws_server）
- tradex 版本 2.2.0 → 2.3.0，MCP 工具数 57 → 61（信号数据 10 → 14）
- signal_data.py V0.7 → V0.8，工具数 10 → 14

### Testing
- 测试套件：69 个测试用例，0 失败
- 覆盖率：新模块 80-91%（smart_router 91%、tick_store 90%、etf 81%、convertible_bond 89%）

## [2.2.0] - 2026-06-23

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

### Added
- 新增 `astock_signals/northbound.py` — 北向资金流向模块（同花顺 hsgtApi，含本地 CSV 缓存历史）
- 新增 `astock_signals/fund_flow.py` — 个股资金流向模块（东财 push2 实时 + push2his 历史 20 天）
- 新增 `astock_signals/dragon_tiger.py` — 龙虎榜席位明细模块（东财 datacenter，含机构动向）
- 新增 `astock_signals/industry.py` — 行业横向对比模块（东财 push2 行业排名）
- 新增 4 个 MCP 工具：`get_northbound_flow_signal` / `get_fund_flow_signal` / `get_dragon_tiger_signal` / `get_industry_comparison_signal`
- 所有新模块均提供 `_json` 版本返回结构化 dict，供 MCP 工具和 CLI 共用

### Changed
- `astock_signals/__init__.py` 版本升至 0.2.0，导出 9 个模块（原 5 → 现 9）
- `signal_data.py` 版本升至 V0.7，工具数从 6 增至 10
- `tradex` 版本升至 2.2.0，MCP 工具总数 53 → 57
- README 更新工具清单和版本历史

### Architecture
- 一主一备架构落地：AKShare 版 money_flow / north_bound / dragon_tiger 为主力源，astock_signals 东财直连为备用源
- 新模块复用 `anti_ban_client` 的 `em_get` / `em_datacenter` / `em_push2_fund_flow` / `em_push2his_fund_flow`，统一封控

## [2.1.0] - 2026-06-22

### Added
- 新增 `astock_signals/` 信号数据模块（5个文件），移植自 TradingAgents-astock 项目
- `anti_ban_client.py` — 东方财富 HTTP 防封限流客户端（Session 复用 + 串行限流 + 随机抖动）
- `hot_money.py` — 涨停归因接口（同花顺 editorial，含主题频次统计）
- `lockup.py` — 限售解禁日历接口（东财 datacenter RPT_LIFT_STAGE，含风险提示）
- `concept.py` — 个股概念/行业/地域板块归属（push2delay 镜像 + 地域板块反查策略）
- `indicators.py` — 13种技术指标计算（MACD/RSI/Boll/ATR/KDJ/MFI 等，stockstats 引擎）
- 新增 6 个 MCP 工具：`get_hot_stocks` / `get_lockup_expiry` / `get_concept_attribution` / `get_profit_forecast` / `get_technical_indicator` / `list_technical_indicators`
- `get_profit_forecast` 支持分析师一致预期 EPS + Forward PE + PEG + PE 消化年限

### Changed
- `server.py` 注册 signal_data 工具模块（53 工具全部就绪）
- 清理 `server.py` 中误导性的 V0.x 内部注释，改为中文功能描述

### Fixed
- `get_profit_forecast` 从 `pd.read_html`（JS 渲染 SPA 解析失败）改为正则精准匹配 `<thead>/<tbody>`

## [2.0.0] - 2026-06-01

### Added
- 集成eltdx通达信行情协议，提供独有数据源
- 新增集合竞价数据接口（开盘前竞价撮合详情）
- 新增逐笔成交数据接口（每笔成交明细）
- 新增F10资料数据接口（公司概况/热点题材/财务诊断）
- 新增开盘前分析模块（基于集合竞价数据预判热点板块）
- 新增资金流向分析模块（基于逐笔成交识别主力资金动向）
- 新增个股筛选模块（基于F10资料快速筛选投资价值）
- 更新数据源矩阵，新增eltdx独有数据源对比表
- 更新CLI命令，新增eltdx独有数据分析命令

### Changed
- 优化智能路由策略，独有数据类型（竞价/逐笔/F10）固定使用eltdx
- 更新数据源评分模型，考虑独有数据源的不可替代性
- 完善项目文档，添加eltdx独有数据源说明

### Technical Details
- eltdx集成版本：1.0.2
- eltdx许可：仅限个人学习、协议研究和非商业研究使用
- 竞价数据延迟：~114ms
- 逐笔成交延迟：~150ms
- F10资料延迟：~200ms

### Notes
- eltdx提供腾讯接口无法覆盖的独有数据类型
- 独有数据对T0日内交易有重要价值
- 保持对原有数据源（腾讯/Wind/东财）的兼容

## [1.0.0] - 2026-05-01

### Added
- 初始版本发布
- 多源MCP数据聚合平台架构
- 智能路由系统（trader-data-router）
- 本地知识库（SQLite+语义搜索）
- 定时任务系统
- 统一MCP协议层
- 支持通达信MCP、Wind MCP、东财MCP、腾讯接口
- 全市场综合分析引擎（market_analyzer.py）
- 新闻聚合引擎（4源聚合）
- 同花顺数据集成（29个THS函数）
- 分析模型：四象限、信息熵、情绪时钟
- CLI命令行工具
- 完整的项目文档和使用示例
- Apache-2.0开源许可证

### Technical Details
- Python 3.10+兼容
- MCP协议1.0支持
- 数据源覆盖：A股、宏观、行业
- 分析模型：四象限、熵共识、情绪时钟