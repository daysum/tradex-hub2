# tdxdata 接入评估与实施计划

> 评估对象：https://github.com/liu1561089752/tdxdata
> 评估时间：2026-10-30
> 评估版本：`tdxdata 0.3.0`，commit `a276f57`（2026-10-28）
> 评估方式：源码通读 + 本机真实联网探测（只读，未改动 tradex-hub 代码）
> 结论：**建议接入，定位为「通达信 7615 第二数据中枢」——新工具 + 部分类型备源，不做全面替换。**

---

## 1. 结论摘要

### 1.1 值得做的三件事

1. **把 421 个数据中心产品做成通用 MCP 工具**（`tdx_list_products` + `tdx_get_product`）。
   这是 tdxdata 相对本项目最大的增量：零依赖、无需 Key、覆盖龙虎榜/两融/北向/财报/F10/
   大宗交易/新股/股东/估值/基金 ETF/宏观/市场统计等 17 个域。
2. **新增 tdxdata 独有的市场截面工具**：
   实时涨跌家数、历史涨跌家数序列（880005/880006/880866/880641）、
   多代码当日分时点位、六类板块清单与概念成分股、市场统计、个股涨跌停价。
3. **把 tdxdata 注册为 3 类现有数据的低优先级备源**：
   `market_breadth`、`historical_kline`、`minute_data`。
   这三类现有 schema 简单、映射风险低，且 tdxdata 与东财/同花顺是不同上游。

### 1.2 不建议做的事

| 事项 | 原因 |
|------|------|
| 用 tdxdata 替换 eltdx 作为行情主源 | tdxdata 无五档盘口；单命令 2.2s 匀速闸门；实时性不如 eltdx 7709 |
| 用 tdxdata 做逐笔主源 | 逐笔**无买卖方向**（`bs_flag` 声明式不可用）；只支持当日 |
| 用 tdxdata 做全量股票列表/代码表备源 | `get_universe(market=6)` 全量 5577 只约 **158 秒**，不可用于工具实时调用 |
| 用 tdxdata 做实时行情备源（首期） | 无五档、无 PE/PB/换手率等字段；2.2s 节流，schema 与现有 quote 不一致 |
| 首期替换 `industry_quotes` 备源 | `get_boards("all")` 实测 **24.2s**；且缺领涨股/涨跌家数列 |
| 直接暴露 `tdx_call` 给普通 Agent | 底层命令参数需人工核对，误用会得到无意义空表；仅作高级逃生口 |

### 1.3 推荐接入方式

**把 tdxdata 固定版本 vendoring 到本仓库**（`tradex/src/tdxdata/`），而不是加 git 依赖：

- tdxdata 纯标准库、零第三方依赖，vendoring 不增加安装复杂度；
- 本项目已有 `astock_signals` 并入 `src/` 的先例；
- 生产环境无需 git/网络即可安装，符合项目「零配置可运行」的部署风格；
- 上游 MIT 许可，保留 LICENSE/NOTICE 即可。

> 备选方案：`pip install tdxdata @ git+https://...@<commit>`。适合不想背 1.8MB 源码的场景，
> 但会引入 git 安装依赖与版本漂移风险。若选此方案，必须锁定 commit，并在
> `requirements.txt` / `pyproject.toml` 中写死。

---

## 2. 上游项目画像

### 2.1 基本事实

| 项 | 值 |
|---|---|
| 包名/版本 | `tdxdata` 0.3.0 |
| 许可 | MIT（Copyright (c) 2026 liu1561089752） |
| Python | >= 3.10 |
| 依赖 | **零第三方依赖**（只用 socket/struct/zlib/json） |
| 代码规模 | 72 个文件，约 15,887 行 Python，包体约 1.8MB |
| 协议 | 通达信手机版 **7615** TCP 长连接；4 台公开服务器；JSON + protobuf 两类通道 |
| 鉴权 | 无账号、无 Key |
| 返回模型 | `Bar` / `MinutePoint` / `Quote` / `Tick` / `Table` / `Dataset`，均带 `provenance`（来源/时刻/耗时/时效） |
| 同步/异步 | `Client` / `AsyncClient` |
| 缓存 | 可选 `TTLCache`，默认关闭；命中不占限流配额 |
| 测试 | 上游自述 928 passed（离线）+ 黄金样本回放 |

### 2.2 数据能力

| 能力 | 接口 | 说明 |
|---|---|---|
| 日/周/月/季 K 线 | `get_bars` | 前复权/后复权/不复权由服务端计算；单页 416 根，超出自动翻页 |
| 1/5/15/30/60 分钟线 | `get_bars` | 1 分钟线约覆盖 2 个交易日；**无 3 分钟/年线**（服务端空结果，库显式拒绝） |
| 当日分时 | `get_minutes` | 最多 240 点，含均价；盘中只给到当前时刻 |
| 逐笔成交 | `get_ticks` | 自动翻页、按时间升序；**无买卖方向、无笔数**；仅当日 |
| 批量报价 | `get_quotes` | 沪深/创业板/科创/北交所/指数/板块；含成交量、成交额；**无五档** |
| 全市场枚举 | `get_market_page` / `get_universe` / `iter_universe` | `market=6` 为全部 A 股（含北交所 354 只），全量约 5577 只/158 秒 |
| 板块 | `get_boards` / `get_board_members` | 六类：行业一/二/三级、概念、风格、地区；**仅概念板块支持成分股** |
| 技术指标 | `get_indicators` | EMA/RSI 等 11 个确定性指标（含 HV/动量/夏普/最大回撤/Beta） |
| 涨跌停价 | `get_price_limits` | 主板/双创/北交所/转债/基金/B 股规则 |
| 实时涨跌家数 | `get_market_breadth` | 涨/跌/平/涨停/跌停；含逐分钟序列 |
| 历史涨跌家数 | `get_breadth` | 880005/880006/880866/880641 等非价格序列 |
| 多代码分时点位 | `get_intraday_samples` | 一次取多代码当日分时；**个股未复核，仅指数实证**（实验性） |
| 市场统计 | `get_market_stat` | 涨跌停家数（近 30 日）/行业涨跌/情绪 |
| 资金流 | `get_capital_flow` | **只支持全市场排行**；不支持按单代码查询 |
| F10 | `get_f10` | 22 个 section（公司概况/经营分析/股东/分红/题材/诊断等） |
| 财报 | `get_finance` | 资产负债表/利润表/现金流量表/主要指标等 9 类，标准英文字段 |
| 分红融资 | `get_xdxr` | 分红融资历史 |
| 数据中心产品 | `get(name)` / `call(cmd, ctl, params)` | **421 个产品 / 17 个域 / 485 条可调用命令** |

### 2.3 关键限制（接入前必须知道）

| 限制 | 影响 |
|---|---|
| 单命令节流约 **2.2 秒**，限流按**来源 IP** 计 | 不能做高频/并发主源；单次工具调用可能秒级；并发无收益 |
| 无五档盘口 | 不能替代 `eltdx_get_depth` |
| 逐笔无方向、无笔数 | 不能替代 eltdx 逐笔的 `side` 字段 |
| 无历史逐笔 | 只能当日 |
| 无港股/期货/期权/外汇行情 | 这些品种的 K 线/分时/五档全部拿不到 |
| 行业板块无成分股 | `get_board_members` 只支持概念板块；行业成分股仍用现有东财直连 |
| 421 个产品中仅 **52 个**登记了标准列义 | 其余产品返回 `N001...` 原始列；需逐产品核对列义 |
| 421 个产品中仅 **37 个**有 `@code@`/`#date#` 占位符 | 其余产品参数固定；需按产品文档/底层 `call` 覆盖参数 |
| `get_boards("all")` 实测 24.2s | 必须单独放宽超时并加缓存；不要放在默认路由里 |
| `get_universe` 全量 158s | 不可作为 `get_stock_list` 备源 |
| 上游数据来自第三方公开服务器 | 代码 MIT，但数据合规性需自行确认（与现有数据源同一性质） |

### 2.4 本机真实探测结果（2026-10-30）

| 调用 | 结果 | 耗时 |
|---|---|---|
| `get_bars("600519", day, 5, qfq)` | 5 根，20261030 收 1392.05 | 3.45s（首调） |
| `get_quotes(["600519","000001"])` | 2 条，含名称/价格/量额 | 2.20s |
| `get("holiday")` | 58×6 | 4.50s |
| `get_market_breadth("summary")` | 1×5：涨 893/跌 4602/平 76/涨停 49/跌停 86 | 2.41s |
| `get_breadth("880005", count=5)` | 5×8 | 2.20s |
| `get_intraday_samples(["999999"])` | 1×6，240 点 | 2.20s |
| `get_boards("concept")` | 269×6 | **24.21s** |
| `get_board_members("880564")` | 49×9，含入选理由/纳入日期 | 7.59s |
| `get_market_stat("updown")` | 30×4 | 4.66s |
| `get("lhb")` / `dzjy` / `north` / `ztzb` / `xjjj` / `jgcg` / `gzzc_gg` / `zjlx` / `sctj` / `macro_jq` / `fin` / `etf_hq` | 全部 OK | 2.2~7.2s/次 |
| `get_capital_flow(["600519"])` | 明确抛 `NotSupportedError`（只支持排行） | — |

> 探测命令与结果可复现：见 `scripts/tdxdata_smoke.py`（计划新增）。

---

## 3. 与本项目现有能力的对比

### 3.1 现有基线

- MCP 工具：**130 个**（实测 `mcp.list_tools()`）
- 数据源：**79 个数据类型 / 103 个源条目**
- 数据源梯队：eltdx（通达信 7709/7615）→ AKShare/东财/同花顺/腾讯 → 本地 vipdoc
- L1 工具统一经 `SmartRouter.route()`；L2 纯计算；L3 组合分析

### 3.2 tdxdata 独有 / 明显补强的能力（建议做新工具）

| 能力 | 现有项目覆盖 | 建议 |
|------|-------------|------|
| **421 个数据中心产品** | 部分有对应工具，但大量域（大宗交易、新股、股东户数、产业链/筹码、基金 ETF 细分、宏观细分）无覆盖 | **通用产品工具 + 产品目录 + 常用产品说明** |
| **实时涨跌家数**（`get_market_breadth`） | 有 `market_breadth` 数据类型但**没有 MCP 工具**；现有 `get_market_sentiment` 口径不同 | **新工具 + 作为 `market_breadth` 备源** |
| **历史涨跌家数序列**（`get_breadth`） | 无对应工具 | **新工具** |
| **多代码当日分时点位**（`get_intraday_samples`） | 只能单代码分时 | **新工具**（标注实验性） |
| **六类板块清单**（行业一/二/三级、概念、风格、地区） | 行业/概念列表有；风格/地区无 | **新工具**（`get_boards`） |
| **概念板块成分股 + 入选理由/纳入日期** | 行业成分股有；概念成分股无（现有东财接口只给行业/概念列表） | **新工具**（`get_board_members`） |
| **个股涨跌停价** | `eltdx_get_special_limits` 只覆盖特殊品种 | **新工具**（`get_price_limits`） |
| **市场统计**（涨跌停家数近 30 日/行业涨跌/情绪） | 有涨跌停池/情绪工具，但历史口径不同 | **新工具**（`get_market_stat`） |
| **5 个风险指标**（HV/动量/夏普/最大回撤/Beta） | L2 有 6 个技术指标，无这 5 个 | **新增 L2 工具** `calculate_risk_metrics` |
| **F10 22 个 section / 财报 9 类标准字段** | 有 F10/财报工具，但 eltdx F10 返回 T 编码、akshare 财务字段不稳 | **Phase 3 评估作为备源或新工具** |

### 3.3 重叠能力（建议做备源，不做新工具）

| 数据类型 | 现有源 | tdxdata 适配度 | 建议 |
|---------|--------|---------------|------|
| `historical_kline` | eltdx → akshare/东财→腾讯 | 高（字段可映射；缺换手率/振幅首行需自算） | **Phase 2 备源，priority=300** |
| `minute_data` | eltdx → 东财 | 高（时间/价格/均价/成交量一致） | **Phase 2 备源，priority=300** |
| `market_breadth` | 东财 push2ex | 高（5 列一一对应） | **Phase 2 备源，priority=100** |
| `f10_profile` | eltdx 独占 | 中（22 section 可映射，但输出契约不同） | Phase 3 |
| `financial_stmt` | akshare/sina | 中（`get_finance` 标准字段，但中文列/单位需转换） | Phase 3 |
| `valuation` | akshare/eltdx | 低（tdxdata 是估值排行，不是个股历史 PE/PB 序列） | 不做备源 |
| `northbound` | 同花顺/akshare | 中（`get("north")` 30 日表，需映射 dict） | Phase 3 |
| `dragon_tiger` | 东财/akshare | 中（`lhb` 等产品原始列，需映射） | Phase 3 |
| `limit_up_board` | 东财 push2ex | 中（`ztzb`/`ztqd` 等产品，需映射） | Phase 3 |
| `lockup_expiry` | 东财 | 中（`xjjj` 原始列，需映射） | Phase 3 |
| `tick_data` | eltdx 独占 | 中（无方向；需 `bs=unknown`） | Phase 3 |
| `realtime_quote` | eltdx→akshare→腾讯 | 低（无五档/PE/PB；2.2s） | 不做备源 |
| `security_codes` / `all_a_shares` | eltdx 独占 | 低（全量枚举 158s） | 不做备源 |
| `industry_quotes` | 东财 push2 | 低（24s；缺领涨股/家数） | 不做备源 |
| `etf_data` / `cb_data` | akshare/astock_signals | 低（无对应实时/价值分析工具） | 不做备源 |

### 3.4 上游独立性判断

- tdxdata 与 eltdx **同属通达信生态**，但 tdxdata 走手机版 7615 通道，
  eltdx 行情走 7709、F10 走 7615 HTTP 网关。对东财/同花顺/新浪/巨潮而言，
  tdxdata 是**真正不同的上游**；对 eltdx 独家数据（五档/逐笔/竞价）则不是独立备源。
- 因此：**不要**把 tdxdata 当作 eltdx 独占类型的「跨上游兜底」；
  它的价值在于给东财/同花顺单源类型增加一个非东财、非同花顺的独立备源。

---

## 4. 接入方案

### 4.1 方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| **A. Vendoring（推荐）** | 把 `tdxdata/` 包固定版本复制到 `tradex/src/tdxdata/`，随 tradex wheel 打包 | 离线可装、版本稳定、零依赖、与 astock_signals 先例一致 | 仓库 +1.8MB / +15.9k 行；需同步上游 | **采用** |
| B. 外部 git 依赖 | `tdxdata @ git+https://...@<commit>` | 仓库干净、上游更新方便 | 生产安装依赖 git/网络；版本漂移；与项目零配置目标冲突 | 备选 |
| C. 独立 MCP Server | 单独起一个 tdxdata MCP 进程，tradex 通过 connector 调 | 故障隔离 | 多一个进程/端口；AI Agent 要配两个 connector；无法复用 SmartRouter | 不采用 |

### 4.2 Vendoring 注意事项（已实测确认）

1. **`.gitignore` 的 `data/` 规则会忽略 `tradex/src/tdxdata/data/products.json`**
   （实测 `git check-ignore` 命中 `.gitignore:48:data/`）。
   必须追加：
   ```gitignore
   !tradex/src/tdxdata/data/
   !tradex/src/tdxdata/data/**
   ```
2. `tradex/pyproject.toml` 的 `[tool.hatch.build.targets.wheel] packages` 增加
   `"src/tdxdata"`；并验证 wheel 中包含：
   - `tdxdata/data/products.json`
   - `tdxdata/_engine/catalog/page_cmd_params.json`
   - `tdxdata/_engine/catalog/pb_specs.json`
   - `tdxdata/_engine/catalog/raw_args.json`
   - `tdxdata/py.typed`
3. 保留上游 `LICENSE`（MIT）与版权声明；新增 `tradex/src/tdxdata/VENDORED.md`：
   - 上游 URL、commit `a276f57`、版本 `0.3.0`、同步日期、同步方法。
4. 上游包名保持 `tdxdata` 不变，避免改 import 路径；在本项目 `README` 注明来源与许可。
5. 如未来需要升级：用 `tools/sync_tdxdata.py`（或文档步骤）从上游指定 commit 覆盖，
   跑 `tests/test_tdxdata_*` 与 `scripts/tdxdata_smoke.py` 后再提交。

---

## 5. 目标架构

### 5.1 新增/修改文件

| 文件 | 类型 | 内容 |
|------|------|------|
| `tradex/src/tdxdata/` | 新增（vendored） | 上游 0.3.0 包 |
| `tradex/src/tdxdata/VENDORED.md` | 新增 | 来源、commit、许可、同步说明 |
| `tradex/src/tradex/data_sources/tdxdata_fetchers.py` | 新增 | tdxdata 客户端单例、fetch_fn、schema 转换 |
| `tradex/src/tradex/tools/tdxdata_data.py` | 新增 | 新 MCP 工具（约 9~11 个） |
| `tradex/src/tradex/data_sources/registry.py` | 修改 | 注册备源与新数据类型 |
| `tradex/src/tradex/server.py` | 修改 | lifespan 关闭 tdxdata 客户端 |
| `tradex/src/tradex/config.py` | 修改 | 新增 `TDXDATA_*` 配置 |
| `tradex/pyproject.toml` | 修改 | 打包 `src/tdxdata`；版本 3.4.0 |
| `.gitignore` | 修改 | 放行 vendored `data/` |
| `tests/test_tdxdata_fetchers.py` | 新增 | 离线单测 |
| `tests/test_tdxdata_tools.py` | 新增 | 离线单测 |
| `scripts/tdxdata_smoke.py` | 新增 | 真实网络冒烟（默认只跑少量） |
| `docs/plan-tdxdata-integration.md` | 新增 | 本文件 |
| `docs/tdxdata-products.md` | 新增 | 常用产品目录/列义说明（Phase 1 增量） |
| `README.md` / `tradex-hub-接口与安装手册.md` / `docs/architecture.md` / `docs/known-issues.md` / `CHANGELOG.md` / `config/mcp-servers.json` | 修改 | 工具数/数据源数/新工具/限制说明 |

### 5.2 客户端与服务封装

`tdxdata_fetchers.py` 负责：

- **单例客户端**：模块级 `_client` + `threading.RLock`；懒建连。
  - tdxdata 文档明确 `Client` 自身不加锁，多线程共用只保证结果正确、不保证状态安全；
  - 本项目用**全局锁串行化**所有 tdxdata 调用（tdxdata 的 pacer 本来就是进程级串行），
    避免 `_last_call`/`_cal`/`_clock` 竞态。
- **生命周期**：`_shutdown()` 关闭客户端；在 `server.py` lifespan 的 `finally` 调用，
  并注册 `atexit` 兜底。
- **超时**：所有调用通过 `ThreadPoolExecutor`/`asyncio.to_thread` 执行；
  连接超时 15s；普通查询 30s；`tdx_product`/`boards` 60s。
- **错误翻译**：
  - `EmptyResult` → 抛 `RuntimeError`（SmartRouter 可降级）/ 工具返回 `error_response`；
  - `NotSupportedError` → **不要**当成“空数据”静默降级；工具明确返回“不支持”及原因；
  - `Timeout`/`RateLimited`/`ConnectionFailed` → 记录健康度并抛错；
  - 未知异常 → 记录 `logger.exception`，返回可读错误。
- **数据源标识**：所有新工具输出带 `"source": "tdxdata"`、`"latency_ms"`、
  `"provenance"`（`as_of`/`status`/`fetched_at`），便于追溯。

### 5.3 新增 MCP 工具（MVP 建议 9 个 + 1 个可选）

| 工具名 | 签名（建议） | 说明 | 是否联网 |
|--------|-------------|------|---------|
| `tdx_list_products` | `(domain="", keyword="", limit=50)` | 421 个产品目录；返回 name/domain/cmd/ctl/params/mapped/description | 否 |
| `tdx_get_product` | `(name, params="", mapped=True, max_rows=200)` | 通用数据中心产品；`params` 为 JSON 数组（覆盖产品默认参数） | 是 |
| `tdx_call`（可选） | `(cmd, ctl, params="[]", max_rows=200)` | 底层命令逃生口；仅高级用户 | 是 |
| `tdx_get_market_breadth` | `(view="summary")` | 实时涨/跌/平/涨停/跌停 | 是 |
| `tdx_get_breadth_history` | `(code="880005", count=60)` | 历史涨跌家数/停板家数/新低点位 | 是 |
| `tdx_get_intraday_samples` | `(symbols, count=0)` | 多代码当日分时点位（实验性） | 是 |
| `tdx_get_boards` | `(kind="all")` | 六类板块清单（行业一/二/三级、概念、风格、地区） | 是 |
| `tdx_get_board_members` | `(board)` | 概念板块成分股（含入选理由/纳入日期） | 是 |
| `tdx_get_market_stat` | `(kind="updown")` | 涨跌停家数（近 30 日）/行业涨跌/情绪 | 是 |
| `tdx_get_price_limits` | `(symbol, prev_close=0, is_st=False)` | 个股涨停价/跌停价 | 是 |
| `tdx_status`（建议） | `()` | 能力边界/节流状态/最近调用/时效 | 否/轻量 |

> 工具总数：130 → **139~141**。README / 接口手册 / `config/mcp-servers.json` 需同步。
> 若担心工具过多，可把 `tdx_get_boards` 与 `tdx_get_board_members` 合并为
> `tdx_get_boards(kind="all", board="")`，总数降到 138~140。

**产品目录的可用性设计（关键）**：

- `tdx_list_products` 按 `domain` / `keyword` 过滤，默认最多 50 条，避免一次吐 421 条撑爆上下文；
- `tdx_get_product` 对 `name` 做精确匹配；未命中时返回相近名称建议；
- 对 52 个已登记列义的产品，默认 `mapped=True`，返回标准字段 + 每行 `raw`；
- 对未登记产品，返回原始列并显式标注
  `"column_note": "该产品未登记列义，列为服务端原始 N001...，请参考 docs/tdxdata-products.md"`；
- 新增 `docs/tdxdata-products.md`：人工整理 **Top 40 常用产品**（名称、域、用途、参数、列义、示例），
  不让 Agent 凭空猜 `N001`；
- 输出默认 `max_rows=200`，超出截断并标 `truncated: true`；
- 结果按 `utils.cache` 缓存：实时类 30s，数据中心产品 300s，目录永久缓存。

### 5.4 备源注册（MVP）

在 `data_sources/registry.py` 增加：

```python
from . import tdxdata_fetchers as tdf

# 现有类型：低优先级备源（不改变主源顺序）
router.register("market_breadth", "tdxdata", tdf.fetch_market_breadth, priority=100)
router.register("historical_kline", "tdxdata", tdf.fetch_historical_kline, priority=300)
router.register("minute_data", "tdxdata", tdf.fetch_minute_data, priority=300)

# 新数据类型：tdxdata 独占（供新工具使用）
router.register("tdx_product", "tdxdata", tdf.fetch_product, priority=1, exclusive=True)
router.register("tdx_breadth_history", "tdxdata", tdf.fetch_breadth_history, priority=1, exclusive=True)
router.register("tdx_boards", "tdxdata", tdf.fetch_boards, priority=1, exclusive=True)
router.register("tdx_board_members", "tdxdata", tdf.fetch_board_members, priority=1, exclusive=True)
router.register("tdx_market_stat", "tdxdata", tdf.fetch_market_stat, priority=1, exclusive=True)
router.register("tdx_intraday_samples", "tdxdata", tdf.fetch_intraday_samples, priority=1, exclusive=True)
router.register("tdx_price_limits", "tdxdata", tdf.fetch_price_limits, priority=1, exclusive=True)
```

说明：

- `market_breadth` 现有源只有 `em_push2ex`（priority=1），加 tdxdata 后**首次拥有真实备源**；
- `historical_kline` / `minute_data` 的 tdxdata 源放在 priority=300，只有主源/备源全失败才触发；
- 新类型全部 `exclusive=True`：tdxdata 是唯一源，失败直接报错，不静默降级；
- 所有 `route()` 调用需按类型传 `timeout`：
  - `market_breadth`/`historical_kline`/`minute_data`：30s（字段类工具改造）
  - `tdx_product`/`tdx_boards`/`tdx_board_members`：60s
- 若不想让 tdxdata 进入 SmartRouter 健康评分/线程池，可改为「tool 层直接调用服务封装」；
  但会破坏“L1 统一走 SmartRouter”的架构约定，首期不建议。

### 5.5 新增 L2 风险指标（可选，Phase 3）

tdxdata 的 `indicator.py` 提供 5 个纯函数：
`hv`、`momentum`、`sharpe`、`max_drawdown`、`beta`。
本项目 L2 已有 6 个技术指标，建议新增 **1 个 MCP 工具**：

- `calculate_risk_metrics(closes, benchmark_closes="", periods=[20,60,120], risk_free_rate=0.02)`
  - 返回年化波动率、动量、夏普、最大回撤、Beta；
  - 纯函数、无网络、无第三方依赖；
  - 与现有 `calculate_performance` 有部分重叠，但输入/输出更轻量，适合 Agent 快速调用。

---

## 6. 分阶段实施计划

### Phase 0：Vendoring 与可行性验证（1~2 天）

**任务**
1. 从上游 commit `a276f57` 复制 `tdxdata/` 到 `tradex/src/tdxdata/`；
   保留 `LICENSE`，新增 `VENDORED.md`。
2. 修改 `.gitignore`（放行 `tradex/src/tdxdata/data/**`）与
   `tradex/pyproject.toml`（packages + package data）。
3. 新增 `data_sources/tdxdata_fetchers.py`：客户端单例、锁、`_shutdown()`、
   一个 `tdx_ping()` 探针。
4. 新增 `config.py` 的 `TDXDATA_*` 配置。
5. 新增 `scripts/tdxdata_smoke.py`：默认 5 个调用（bars/quotes/breadth/product/boards），
   `--full` 才跑全部。
6. 构建 wheel，验证 package data 完整；`pip install -e .` 后 `import tdxdata` 正常。

**验收**
- `python -c "import tdxdata; print(tdxdata.__version__)"` → `0.3.0`
- `python scripts/tdxdata_smoke.py` → 5/5 OK
- 现有 130 工具与全部离线测试不受影响

### Phase 1：新工具（3~4 天）

**任务**
1. 新增 `tools/tdxdata_data.py`，注册 9~10 个工具（见 §5.3）。
2. 新增 `docs/tdxdata-products.md`（Top 40 产品说明）。
3. 工具输出统一带 `source/latency_ms/provenance/truncated`。
4. 工具层缓存：实时 30s、产品 300s、目录常驻。
5. 单测：目录过滤、产品参数校验、mapped/raw 输出、行数截断、错误翻译。
6. 更新 README/接口手册/架构文档/`config/mcp-servers.json` 的工具数。

**验收**
- `tdx_list_products` 返回 421 个产品（分页/过滤）
- `tdx_get_product("holiday")` → 58×6
- `tdx_get_product("north", mapped=True)` → 标准列
- `tdx_get_market_breadth()` → 5 个家数
- `tdx_get_breadth_history("880005", 5)` → 5 行
- `tdx_get_intraday_samples("999999")` → 点数 > 0
- `tdx_get_boards("concept")` → 269 行；`tdx_get_board_members("880564")` → 49 行
- `tdx_get_market_stat("updown")` → 30 行
- `tdx_get_price_limits("600519")` → 两个价格
- 所有新工具在 `list_all_tools` 中出现；`/health` 工具数正确

### Phase 2：备源接入（3~4 天）

**任务**
1. `tdxdata_fetchers` 实现 3 个 fetch_fn：
   - `fetch_market_breadth` → 列名 `上涨/下跌/平盘/涨停/跌停`
   - `fetch_historical_kline` → 列名与 akshare/eltdx 完全一致
   - `fetch_minute_data` → 列名 `时间/价格/均价/成交量`
2. 严格周期/复权映射表；未知值**抛错**，禁止默认值兜底（吸取 v3.3.30 H 组教训）。
3. 修改 `get_historical_price` / `get_intraday_data`，为 `route()` 传 `timeout=30`。
4. 单测：用固定样本验证 3 个 fetch_fn 的列名/单位/日期格式；主源失败时降级到 tdxdata。
5. 真实冒烟：停掉主源（monkeypatch 抛错）后走 tdxdata，验证输出可用。

**验收**
- `market_breadth` 主源失败 → tdxdata 返回 5 列，工具正常
- `historical_kline` 主源+备源失败 → tdxdata 返回列集合一致的数据；
  `换手率` 允许为 null 并在输出说明中标注
- `minute_data` 主源+备源失败 → tdxdata 返回分时点，`get_intraday_data` 正常
- 不出现“静默错数据”：未知 period/adjust 必须报错
- 现有离线测试全绿

### Phase 3：深化（5~10 天，可选）

按优先级：
1. **F10/财报备源**：把 `get_f10` / `get_finance` / `get_xdxr` 映射到现有
   `f10_profile` / `financial_stmt` / `dividend_data`。
2. **龙虎榜/北向/涨停/解禁备源**：逐产品核对列义后注册 priority=200。
3. **逐笔备源**：`tick_data` 增加 tdxdata 源，`bs=unknown` 并标注原因。
4. **ETF/基金产品工具**：从 20 个基金域产品中挑选 3~5 个做成便捷工具。
5. **风险指标 L2 工具**：`calculate_risk_metrics`。
6. **产品列义扩展**：把 `FIELD_MAP` 的 52 个产品扩展到 80~100 个（只映射有证据的列）。

---

## 7. 数据契约与防错清单

### 7.1 周期映射（严格）

| 本项目输入 | tdxdata period | 备注 |
|-----------|----------------|------|
| `daily` / `day` | `day` | |
| `weekly` / `week` | `week` | |
| `monthly` / `month` | `month` | |
| `quarterly` / `quarter` | `quarter` | |
| `1m` / `5m` / `15m` / `30m` / `60m` | 同名 | `1m` = 1 分钟，与 v3.3.30 口径一致 |
| `yearly` / `year` / `3m` | **不支持** | 必须抛 `ValueError`，不得回落日线 |

### 7.2 复权映射

| 本项目 | tdxdata `Adjust` |
|--------|------------------|
| `qfq` | `QFQ` |
| `hfq` | `HFQ` |
| `""` / `none` | `NONE` |
| 其他 | 抛 `ValueError` |

### 7.3 单位与日期

- `Bar.vol` / `Quote.vol` / `MinutePoint.vol` / `Tick.vol` **恒为「手」**；与现有 eltdx/东财口径一致。
- `amount` 单位为元。
- 日线 `date` 为 `YYYYMMDD`；分钟线另有 `time=HH:MM`。
- `Quote.pre_close` 是**昨收**（不是当日收盘）。
- 逐笔 `time` 为 `HH:MM:SS`；`bs_flag` 恒 `None`，不要映射成买卖。

### 7.4 已知缺口（必须在输出中标注）

| 字段/能力 | 处理 |
|----------|------|
| 历史 K 线 `换手率` | tdxdata 不提供 → 输出 `null`，并在工具结果 `notes` 标注 |
| 历史 K 线首行 `振幅/涨跌幅/涨跌额` | 缺前收 → `null` 或请求 `count+1` 后自算；不得填 0 |
| 逐笔 `bs_flag` | 输出 `"unknown"`，并注明“tdxdata 无方向字段” |
| 指数/板块分钟线 `vol` | tdxdata 返回 `None`（派生值不可信）→ 原样 `null` |
| 未映射产品的列 | 原始 `N001...`，标 `column_note` |
| 实时涨跌家数 `as_of` | tdxdata 回包无日期 → `as_of=None` 并带 `as_of_note` |
| `get_intraday_samples` 个股 | 上游仅指数实证 → 标 `experimental` |

### 7.5 防错红线（沿用 v3.3.30 教训）

1. **禁止 `dict.get(key, 默认值)` 兜周期/复权/枚举参数**；未知值一律抛错。
2. **备源必须与主源语义一致**；无法映射的字段宁可 `null`，不得用 0/默认值伪装。
3. **`NotSupportedError` ≠ 空数据**；不得静默降级或返回空表。
4. **空表要区分“当期无数据”与“通道不通”**；前者可返回空，后者抛错。
5. **所有 tdxdata 调用必须带 provenance**（source/as_of/status/fetched_at）。
6. **新增形参前先查 `live_smoke.py` 的通用参数池**，避免 v3.3.30 `page_size` 事故重演。

---

## 8. 测试与验收

### 8.1 离线单测（不联网）

- `tests/test_tdxdata_fetchers.py`
  - 周期/复权映射表（含未知值抛错）
  - `Bar` → 中文 DataFrame 的列名/顺序/单位
  - `MinutePoint` → 分时 DataFrame
  - `market_breadth` → 5 列
  - 空结果/不支持/超时的错误翻译
  - 客户端单例与 `_shutdown()` 幂等
- `tests/test_tdxdata_tools.py`
  - 产品目录过滤/分页
  - `tdx_get_product` 的 mapped/raw/truncated
  - 参数校验与错误 JSON
  - 行数上限与缓存键
- `tests/test_tdxdata_packaging.py`
  - `products.json` / `_engine/catalog/*.json` 可读
  - wheel 构建后包数据存在（可用 `importlib.metadata`/`Path`）

### 8.2 真实网络冒烟（独立脚本，不进 CI 默认）

`scripts/tdxdata_smoke.py`：

```bash
python scripts/tdxdata_smoke.py            # 默认 5 个调用，约 15s
python scripts/tdxdata_smoke.py --full     # 全部产品/板块，约 20+ 分钟
```

- 默认检查：bars、quotes、market_breadth、breadth_history、product("holiday")
- `--full` 按域抽样 421 个产品，输出 OK/EMPTY/FAIL 表格
- 由于 2.2s 节流，**不要**并入 `scripts/live_smoke.py` 的默认全量跑

### 8.3 验收标准（Definition of Done）

1. 现有 130 个工具行为不变；`pytest -m "not network"` 全绿。
2. 新工具全部注册，`list_all_tools` 与 `/health` 工具数一致。
3. §6 Phase 1/2 的验收用例全部通过。
4. `scripts/tdxdata_smoke.py` 默认 5/5 OK，并记录延迟。
5. README、接口手册、架构文档、known-issues、CHANGELOG、`config/mcp-servers.json` 同步更新。
6. `VERSION` 升级到 **3.4.0**（新增工具/数据源，minor）。
7. 无静默错数据：未知周期/复权/产品参数均抛错；未映射列有显式提示。

### 8.4 回滚方案

- 配置 `TDXDATA_ENABLED=false`：跳过 tdxdata 客户端初始化与所有注册；
- 或移除 `registry.py` 中的 3 个备源注册（新工具保留，不影响现有工具）；
- 无数据库/文件迁移，回滚成本低。

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **IP 限流/2.2s 节流** | 调用慢、并发差；可能影响同 IP 的 eltdx 请求 | 单客户端 + 全局锁；默认只做备源/低频工具；缓存；不并发；监控 `rate_stats()` |
| **与 eltdx 同属通达信生态** | 对 eltdx 独占类型不构成跨上游备源 | 不把 tdxdata 当 eltdx 的兜底；只给东财/同花顺单源类型做独立备源 |
| **上游版本漂移** | API/产品表变化 | Vendoring 固定 commit；`VENDORED.md` 记录；升级前跑冒烟与单测 |
| **421 产品多数列为 N001** | Agent 难以理解 | Top 40 产品说明 + `mapped` 标记 + 原始列提示；逐步扩展 FIELD_MAP |
| **`get_boards("all")` 24s** | 超时/卡顿 | 按 kind 查询、缓存、60s 超时；不放在默认路由 |
| **`get_universe` 158s** | 不可用于工具 | 不作为 `stock_list` 备源；需要全量时用现有 eltdx/akshare |
| **事件循环阻塞** | MCP 无响应 | 所有调用走 `asyncio.to_thread`/SmartRouter 线程池；工具层 `wait_for` 超时 |
| **数据合规** | 第三方数据源条款 | 与现有 AKShare/东财源同等对待；README 免责声明；不存储/再分发原始数据 |
| **打包遗漏 products.json** | 运行时 `TdxError: 产品数据缺失` | wheel 构建测试 + `tests/test_tdxdata_packaging.py` |

---

## 10. 工作量与排期

| 阶段 | 内容 | 预估 | 产出 |
|------|------|------|------|
| Phase 0 | Vendoring + 客户端封装 + 冒烟脚本 | 1~2 天 | 可 import、可连通、可打包 |
| Phase 1 | 9~10 个新工具 + 产品目录 + 文档 | 3~4 天 | 工具数 130→139~141 |
| Phase 2 | 3 类备源 + 严格映射 + 测试 | 3~4 天 | `market_breadth`/K线/分时具备 tdxdata 兜底 |
| Phase 3 | F10/财报/龙虎榜/北向/逐笔备源、L2 风险指标、列义扩展 | 5~10 天 | 完整融合 |
| **MVP（Phase 0+1+2）** | | **约 7~10 个工作日** | 可发布 v3.4.0 |
| **完整版** | | **约 15~20 个工作日** | v3.5.0 |

> 如果只想先验证价值，可做 **5 天最小版**：
> Vendoring + `tdx_list_products` + `tdx_get_product` + `tdx_get_market_breadth`
> + `market_breadth` 备源 + 冒烟脚本。

---

## 11. 需要拍板的决策

| # | 决策 | 建议 |
|---|------|------|
| 1 | Vendoring 还是外部 git 依赖？ | **Vendoring**（固定 0.3.0 / commit a276f57） |
| 2 | 是否暴露通用 `tdx_call`？ | 建议暴露，但标记「高级/需核对参数」，默认不推荐 Agent 使用 |
| 3 | MVP 备源选哪几类？ | `market_breadth` + `historical_kline` + `minute_data` |
| 4 | 是否首期做 F10/财报备源？ | 否，放 Phase 3；先做通用产品工具 |
| 5 | 工具数上限？ | MVP 控制在 **139~141**，避免工具爆炸；后续按需增加 |
| 6 | 是否把 tdxdata 产品全量开放？ | 是，但默认分页/过滤 + 行数上限；Top 40 产品提供中文说明 |
| 7 | 是否新增 L2 风险指标？ | 建议 Phase 3 加 1 个 `calculate_risk_metrics` |

---

## 12. 参考

- tdxdata 仓库：https://github.com/liu1561089752/tdxdata
- tdxdata 接口目录：仓库内 `tools/catalog/05-接口目录.md`（635 条已知命令 / 485 条可调用）
- tdxdata 字段核验：仓库内 `docs/VERIFICATION.md`
- tradex-hub 现有架构：`docs/architecture.md`
- tradex-hub 已知问题与防错经验：`docs/known-issues.md`（尤其 H 组“静默错数据”）

---

## 13. 附录：tdxdata vs TDXDataFetcher 对比（2026-10-30）

> 对比对象：https://github.com/mickey3721/TDXDataFetcher
> 对比方式：源码通读 + 离线测试 + 本机真实联网探测（只读）

### 13.1 两个项目不是同一类东西

| 维度 | **tdxdata**（liu1561089752） | **TDXDataFetcher / tdxapi**（mickey3721） |
|------|------------------------------|------------------------------------------|
| 协议 | 通达信**手机版 7615**（JSON + protobuf） | 通达信**PC 版 7709**（自研 pytdx 风格协议） |
| 定位 | “行情 + 数据中心”完整客户端 | “行情数据自研接口”，偏底层 7709 协议实现 |
| 代码规模 | 约 15,887 行 / 72 文件 / 1.8MB | 约 2,871 行 / 20 文件 / 30KB |
| 版本 | 0.3.0 | 0.1.0 |
| 最近提交 | 2026-10-28（活跃） | 2026-04-06（半年未更新） |
| 测试 | 上游自述 928 passed（含黄金回放） | 69 个离线测试（本机实测全过） |
| 许可 | MIT，仓库内 `LICENSE` 文件 | README 写 MIT，但仓库**没有 LICENSE 文件**（GitHub API `license=None`） |
| 依赖 | 零第三方依赖 | 零第三方依赖 |
| 社区 | — | 34 stars / 11 forks / 1 open issue |

**结论先说**：tdxdata 是“数据产品库”，TDXDataFetcher 是“7709 协议小客户端”。
后者在能力上更接近 eltdx 的早期雏形，而不是 tdxdata 的竞品。

### 13.2 数据接口对比

| 能力 | tdxdata | TDXDataFetcher | 评价 |
|------|---------|----------------|------|
| 实时行情 | `get_quotes`（无五档、无名称时用 `get_name`） | `get_quote` / `get_quotes`（**含五档**） | tdxapi 字段更全，但实测不可用 |
| 五档盘口 | ❌ 协议无 | ✅ `StockQuote.bid1~5/ask1~5` | tdxapi 唯一明显强项 |
| 日/周/月/季/年 K 线 | 日/周/月/季；❌年线 | 日/周/月/**季/年** | tdxapi 有年线（但实测取数失败） |
| 分钟 K 线 | 1/5/15/30/60；❌3m | 1/5/15/30/60 | 持平 |
| 复权 K 线 | ✅ qfq/hfq/不复权（服务端算） | ❌ **无 adjust 参数** | tdxdata 明显强 |
| 当日分时 | ✅ 240 点 + 均价 | ✅（默认用 1 分钟 K 线替代） | tdxdata 口径更清晰 |
| 历史分时 | ❌（只有当日） | ✅ `get_history_minute_time` | tdxapi 理论强项，实测失败 |
| 当日逐笔 | ✅ 自动翻页；❌方向/笔数 | ✅ `get_transactions`；有 `direction` 字段 | tdxapi 字段更全，实测返回空 |
| 历史逐笔 | ❌ | ✅ `get_history_transactions` | tdxapi 理论强项，实测失败 |
| 股票列表 | `get_universe`（全量 158s） | `get_security_list`（分页） | tdxapi 更轻，实测解码失败 |
| 指数行情 | 通过 `get_quotes`/`get_bars` | `get_index_quote` / `get_index_bars`（仅 SH） | tdxapi 有专用方法，但只硬编码上证 |
| 期货行情 | ❌ | `get_futures_quote`（market=6/7/8/9） | tdxapi 独有，实测不可用 |
| 除权除息 | ✅ `get_xdxr`（结构化） | `get_xdxr_info`（原始字段） | tdxdata 明显强 |
| 财务数据 | ✅ `get_finance` 9 类、标准字段 | `get_finance_info` 15 个原始字段 | tdxdata 明显强 |
| F10 | ✅ 22 个 section | ❌ 只有公司信息目录/内容（原始字节） | tdxdata 明显强 |
| 板块 | ✅ 六类 + 概念成分股 | `get_block_info*`（原始 block.dat） | tdxdata 明显强 |
| 市场统计/涨跌家数 | ✅ 实时 + 历史 + 情绪 | ❌ | tdxdata 独有 |
| 数据中心 421 产品 | ✅ | ❌ | tdxdata 独有 |
| 多代码分时点位 | ✅（实验性） | ❌ | tdxdata 独有 |
| AsyncClient | ✅ | ❌ | tdxdata 有 |
| Provenance（来源/时刻/时效） | ✅ 每个返回对象都带 | ❌ | tdxdata 明显强 |
| 限流/重连/健康 | 进程级 pacer、4 主机、自动重连 | 心跳 + 自动重连 + 测速选服务器 | tdxdata 更成熟 |

### 13.3 本机真实联网探测（2026-10-30）

**tdxdata（7615）**：全部通过（见 §2.4）。

**TDXDataFetcher（7709）**：5 台服务器中 3 台超时，2 台可握手
（`218.75.126.9`、`115.238.90.165`），但绝大多数方法取不到数据：

| 方法 | 实测结果 |
|------|----------|
| `get_quote("600519","SH")` | **返回 None**（服务端回 `num_stock=0`） |
| `get_bars("600519","SH","1d")` | **解析异常**：`unpack_from requires a buffer of at least 6 bytes ... actual buffer size is 2` |
| `get_minute_time("600519","SH")` | 同上，解析异常 |
| `get_transactions("600519","SH")` | 返回空列表 |
| `get_stock_count("SH")` | 27784（可用） |
| `get_security_list("SH",0,5)` | **UnicodeDecodeError**（响应体错位） |
| `get_xdxr_info("600519","SH")` | 返回明显错值（`date=808465921`、`add_count=758722869`） |
| `get_finance_info("600519","SH")` | 返回明显错值（`management_shares=4,096,267,321`、`eps=758,305,860`） |
| `get_index_quote("000001")` | 返回 None |

离线测试 69 个全部通过，但测试大量使用合成字节，且真实网络测试用
`if data:` 守卫 —— **空结果也会通过**，所以“69 passed”不能证明线上可用。

### 13.4 TDXDataFetcher 的代码质量问题（接入前必须知道）

1. **未知周期静默回落日线**：`get_bars` / `get_index_bars` 都是
   `period_map.get(period, 9)`，传 `"foo"` 会静默返回日线 —— 正是本项目
   v3.3.30 H 组明令禁止的“静默错数据”。
2. **无复权**：K 线请求没有 adjust 字段，无法作为 `get_historical_price(adjust="qfq")`
   的备源，否则会静默返回不复权数据。
3. **市场码处理不完整**：`_parse_market` 只认 `"SH"` 否则一律 0（SZ），
   `"BJ"` 北交所会被当成深市；`get_bars` 同样硬编码 1/0。
4. **指数方法只支持上证**：`get_index_quote` 硬编码 `SH`，`get_index_bars`
   的 period_map 缺 `1w/1m/1min`，未知周期还会回落日线。
5. **解析未经验证**：`parse_bars` 用 `/1000.0` 且 high/low 以 open 为基准，
   与 pytdx 的 `/100.0` 口径不一致；`parse_xdxr_info`/`parse_finance_info`
   实测返回垃圾值；`get_security_list` 响应体错位。
6. **无名称解析**：`StockQuote.name` 恒为空串，与现有行情工具契约不符。
7. **无 provenance**：不区分“当期无数据/通道不通/解析失败”，排障困难。
8. **无 LICENSE 文件**：README 声称 MIT，但仓库无 `LICENSE`，vendoring 有合规风险。
9. **维护停滞**：最后提交 2026-04-06，协议/服务器可能已变化；本次实测正是如此。

### 13.5 结论与建议

| 问题 | 结论 |
|------|------|
| 选哪个接入 tradex-hub？ | **选 tdxdata（7615）**，不选 TDXDataFetcher |
| TDXDataFetcher 有没有值得借鉴的？ | 有：`docs/KNOWLEDGE.md` 对 7709 协议细节（变长价格、对数成交量、K 线日期格式、五次 setup 握手）记录得较清楚，可作为 eltdx 的排障参考；但**不要直接集成其代码** |
| 它宣称的五档/期货/历史逐笔怎么办？ | 五档和历史逐笔本项目已由 **eltdx 3.2.2** 覆盖；期货不在本项目 A 股范围内。若未来要做期货，优先评估 eltdx / 专业期货源，而不是修 tdxapi |
| 是否可作为 7709 备源？ | 当前**不具备生产可用性**：核心取数方法多数失败，且存在静默错数据风险。若坚持使用，需先修复并建立真实网络回归（至少覆盖 quote/bars/minute/ticks/finance/xdxr），成本高于收益 |
| 是否可 vendor 其协议常量/请求构造？ | 不建议；协议常量可参考，但代码没有许可证文件，且与 eltdx 已有实现重复 |

**一句话**：tdxdata 是“可用的数据产品库”，TDXDataFetcher 是“未完成、已停滞的 7709 协议实验”。
前者值得接入，后者只值得读它的知识文档，不值得接入。

---

## 14. Phase 0 实施记录（2026-10-30）

已完成：

| 项 | 结果 |
|---|---|
| Vendoring | `tradex/src/tdxdata/`，44 个文件 / 1.1MB，上游 0.3.0 / commit `a276f57` |
| 许可证 | 上游 `LICENSE`（MIT）已保留；新增 `VENDORED.md`（来源/commit/同步步骤） |
| `.gitignore` | 已放行 `tradex/src/tdxdata/data/**`（原 `data/` 规则会误伤 `products.json`） |
| 打包 | `tradex/pyproject.toml` wheel packages 加入 `src/tdxdata`；构建 wheel 验证包含 `products.json` + `_engine/catalog/*.json` |
| 客户端封装 | 新增 `data_sources/tdxdata_fetchers.py`：单例 + RLock + `call()` + `_shutdown()` + `tdx_ping()` |
| 配置 | 新增 `TDXDATA_ENABLED` / `CONNECT_TIMEOUT` / `GAP` / `ROUTE_TIMEOUT` / `CACHE_TTL` / `MAX_ROWS` |
| 生命周期 | `server.py` lifespan 的 `finally` 关闭 tdxdata 客户端；`atexit` 兜底 |
| 冒烟脚本 | 新增 `scripts/tdxdata_smoke.py`（默认 7 项 ~30s；`--full` 遍历 421 产品） |
| 离线测试 | 新增 `tests/test_tdxdata_packaging.py`（6）+ `tests/test_tdxdata_fetchers.py`（5），11 passed |
| 全量回归 | `pytest -m "not network"` → **905 passed / 7 deselected** |
| MCP 工具数 | 仍为 **130**（Phase 0 不新增工具） |
| 真实冒烟 | `scripts/tdxdata_smoke.py` → **OK=7 / FAIL=0**，总耗时 32.5s |

Phase 0 未改动任何现有 L1 工具/数据源注册；tdxdata 目前是“可调用但未挂载”的状态，
Phase 1 再新增 MCP 工具，Phase 2 再接备源。

---

## 15. Phase 1 实施记录（2026-10-30）

已完成：

| 项 | 结果 |
|---|---|
| 新工具模块 | `tradex/src/tradex/tools/tdxdata_data.py`，注册 **10 个** tdxdata 工具 |
| 工具总数 | **130 → 140**（`mcp.list_tools()` 实测） |
| 产品说明 | `tradex/src/tradex/tools/tdxdata_product_notes.py`，57 个常用产品中文说明 |
| 产品速查文档 | `docs/tdxdata-products.md`（57 个产品 + 参数提示 + 易踩坑） |
| 工具清单 | `tdx_list_products` / `tdx_get_product` / `tdx_get_market_breadth` / `tdx_get_breadth_history` / `tdx_get_intraday_samples` / `tdx_get_boards` / `tdx_get_board_members` / `tdx_get_market_stat` / `tdx_get_price_limits` / `tdx_status` |
| 产品映射 | `mapped=True` 对 52 个已登记产品生效；未登记产品自动回退原始列并提示；映射失败也回退，不让列义登记不全打挂查询 |
| 输出契约 | 统一带 `source=tdxdata` / `latency_ms` / `provenance` / `total_rows` / `returned_rows` / `truncated`；行数默认上限 200 |
| 超时 | 产品/板块 60s，市场统计 120s（`industry` 支路约 31 次调用），普通查询 30s；阻塞调用经 `asyncio.to_thread` |
| 离线测试 | 新增 `tests/test_tdxdata_tools.py`（22 项），连同 Phase 0 共 **33 项** |
| 真实验证 | 10 个工具逐个通过 `mcp.call_tool` 实网调用：holiday 58×6、north 30 行（mapped）、lhb 31 行（raw）、etf_hq 1729×2、market_breadth 1×5、breadth 5×8、price_limits 1531.26/1252.85、concept boards 269 行、board members 49 行、market_stat 30×4、intraday samples 240 点 |
| 文档 | README / 接口手册 / architecture / known-issues / CHANGELOG / `config/mcp-servers.json` 已同步工具数与 tdxdata 说明 |

Phase 1 未改现有数据源的注册顺序；Phase 2 再把 tdxdata 注册为
`market_breadth` / `historical_kline` / `minute_data` 的备源。

---

## 16. Phase 2 实施记录（2026-10-30）

已完成：

| 项 | 结果 |
|---|---|
| fetch_fn | `tdxdata_fetchers.py` 新增 `fetch_historical_kline` / `fetch_minute_data` / `fetch_market_breadth` |
| 注册 | `registry.py` 追加 3 个备源：`market_breadth`(100)、`historical_kline`(300)、`minute_data`(300) |
| 数据源数 | **103 → 106** 条注册（79 个数据类型不变；38 种源） |
| 周期映射 | `daily/day`→`day`、`weekly/week`→`week`、`monthly/month/m`→`month`、`quarterly/quarter`→**`season`**、`1m/5m/15m/30m/60m`；年线/3 分钟/未知周期**抛 ValueError** |
| 复权映射 | `qfq`→QFQ、`hfq`→HFQ、`""/none/bfq`→NONE；未知**抛 ValueError** |
| K 线输出 | 12 列与 akshare 口径一致；换手率恒 `null`；振幅/涨跌幅/涨跌额用上一根收盘自算；首行无前收为 `null` |
| 分时输出 | `时间/价格/均价/成交量`，与 eltdx 源一致 |
| 涨跌家数输出 | `上涨/下跌/平盘/涨停/跌停`，与东财 push2ex 源一致 |
| 超时 | 相关工具路由统一放宽到 30s（历史K线/分时/eltdx K线/技术分析/技术指标） |
| 离线测试 | `tests/test_tdxdata_fetchers.py` 扩到 **40 项**（含周期/复权别名、日期过滤、schema、缺字段、备源注册、路由降级）；全量离线 **964 passed / 7 deselected** |
| 真实降级验证 | 用 `SmartRouter` 强制主源失败：`historical_kline`→tdxdata (2×12)、`minute_data`→tdxdata (240×4)、`market_breadth`→tdxdata (893/4602/76/49/86) |
| 真实源级冒烟 | `scripts/live_smoke.py --sources-only --skip-hosts --types historical_kline,minute_data,market_breadth` → **8/8 OK** |
| 真实数据验证 | 日K 5 根（qfq，含振幅/涨跌幅自算）、日期区间 4 行、5 分钟 5 根、季线 5 根、分时 240 点、实时涨跌家数 1×5 |

Phase 2 未改变现有主源优先级；tdxdata 仅在主源/备源失败后参与路由。
MVP 验收完成，版本已升至 **3.4.0**（`VERSION` / `tradex/pyproject.toml` / CHANGELOG）。

---

## 17. Phase 3 实施记录（2026-10-30）

已完成（Phase 3 MVP）：

| 项 | 结果 |
|---|---|
| L2 风险指标 | 新增 `calculate_risk_metrics`（HV/动量/夏普/最大回撤/Beta），基于 vendored `tdxdata.indicator`，纯函数无网络；工具总数 **140 → 141** |
| 财报备源 | `finance_report` 增加 tdxdata（priority=100）；`report_type` 映射 `zcfzb→zcfz` / `lrb→lyb` / `xjllb→xjllb`，返回标准英文字段 |
| 当日逐笔备源 | `today_ticks` 增加 tdxdata（priority=100）；输出 8 列与 eltdx 对齐；方向 `unknown`、开盘撮合 `False`、成交额 `price×手数×100` 推算、最多 1000 笔/次 |
| 分红备源 | **故意不接**：tdxdata `get_xdxr` 是 IPO/融资累计统计（1 行），不是逐次分红/除权事件表，语义错配 |
| 数据源数 | 106 → **108**（新增 finance_report/today_ticks 两个 tdxdata 备源） |
| 工具输出 | `eltdx_get_today_ticks` / `eltdx_get_finance_report` 增加 `source`/`note`，明确标注 tdxdata 备源的能力差异 |
| 超时 | `today_ticks` / `finance_report` 路由放宽到 30s |
| 离线测试 | `tests/test_tdxdata_fetchers.py` 扩到 **49 项**，新增 `tests/test_risk_metrics.py`（6 项）；全量离线 **980 passed / 7 deselected** |
| 真实降级验证 | 强制主源失败：`finance_report`→tdxdata (23×10)、`today_ticks`→tdxdata (100×8)；直接调用 `fetch_finance_report("600519","zcfzb")` 返回 23×10 标准字段 |
| 真实源级冒烟 | `live_smoke --sources-only --skip-hosts --types finance_report,today_ticks` → **4/4 OK**（eltdx + tdxdata 各 2 源） |
| 语义核验 | 实测 tdxdata `get_xdxr` 只返回 `ipo_count/ipo_proceeds_yuan`（1×2），确认**不能**当分红事件表，故不注册 |

Phase 3 仍未覆盖（后续可选）：
- 龙虎榜/北向/涨停/解禁等 raw 产品的逐列映射与备源（需要逐产品核对列义）。
- ETF/基金域便捷工具（可用 `tdx_get_product` 通用访问）。
- 421 产品列义从 52 扩展到 80~100（需上游证据，不猜列义）。
- `f10_profile` 的 tdxdata 备源（输出契约差异大；可用 `tdx_get_product("gsjj"/"jyfx")` 通用访问）。

---

## 18. K 组修复记录（v3.4.1，2026-10-30）

`docs/known-issues.md` K 组：eltdx `historical_kline` 忽略
`start_date` / `end_date` / `adjust`，导致日期区间查询返回最近 100 根、
`qfq` 请求实际拿到不复权数据。已修复：

| 项 | 结果 |
|---|---|
| 日期范围 | `fetch_historical_kline` 接收并过滤 `start_date` / `end_date` |
| 分页回溯 | 按 `bars.get(start=offset)` 从最新页向前回溯，最多 8 页 / 6400 根 |
| count 语义 | 日期范围优先；`count` 只作为下限（避免 live_smoke `count=5` 截断区间） |
| 复权 | 真正透传 `adjust` 到 `bars.get(adjust=...)` |
| 超范围 | 超出可获取历史时显式抛 `RuntimeError`，不静默截断 |
| 回归测试 | 新增 `tests/test_eltdx_kline_date_range.py`（6 项） |
| 实测 | 20261020~20261024 返回 4 行；2022-01-10 qfq 1742.99 vs 不复权 1966.00 |
| 全量离线 | **986 passed / 7 deselected** |
| 版本 | 3.4.0 → **3.4.1**（`VERSION` / `pyproject.toml` / README / 手册 / 配置） |

---

## 19. Phase 3 深水区记录（v3.5.0，2026-10-30）

按“**有标准字段才接，raw 产品不猜列义**”的原则，新增 3 个结构化工具
（`tools/tdxdata_deep.py`，工具 141 → **144**）：

| 工具 | 底层 | 说明 |
|---|---|---|
| `tdx_get_f10` | `get_f10` | 22 个 F10 section；标准字段 + 每行 raw |
| `tdx_get_finance` | `get_finance` | 9 类财报；标准英文字段（`_yuan/_pct/_vol`）|
| `tdx_get_capital_flow_rank` | `get_capital_flow(None)` | 全市场主力净流入榜（固定前 20）|

关键取舍：

- `fhrz` / `get_xdxr` 是 **IPO/融资累计统计**（`ipo_count`/`ipo_proceeds_yuan`），
  **不是**分红/除权事件表；工具内已明确标注，且不注册为 `dividend_financing` 备源。
- `get_capital_flow` 只支持全市场排行；单代码会抛 `NotSupportedError`，
  因此工具只提供 `tdx_get_capital_flow_rank`，不伪装成单代码查询。
- `get_finance(kind="cwfxxq")` 上游多 ResultSet 且列义未定，会抛
  `NotSupportedError`；不猜列名。
- 龙虎榜/涨停池/解禁等 raw 产品**仍未接**；继续走 `tdx_get_product`，
  需要逐产品拿到列义证据后再映射。

验证：

| 项 | 结果 |
|---|---|
| 单测 | `tests/test_tdxdata_deep.py` 7 项；全量离线 **993 passed / 7 deselected** |
| 真实数据 | gsjj 1×14、rdtc 24×2、zcfz 24×10、资金流榜 20×11（top_n=5 截断） |
| 工具数 | 141 → **144**；模块数 20 → 21 |
| 版本 | 3.4.1 → **3.5.0** |

---

## 20. `lhfp` 列义映射记录（v3.6.0，2026-10-30）

产品：`lhfp` → `CWServ.ph_zt_lhb_sy_lhfp`（ctl 0x1A，参数 `["00102","#date#",60,""]`），
60 个交易日 × 6 列。新增工具 `tdx_get_lhb_review`（工具 144→145）。

### 列义证据

| 原始列 | 映射 | 证据 |
|---|---|---|
| `N001` | `date` | 每行首个值为日期，与 `provenance.data_date` 一致 |
| `N002` | `listed_count` | 与东财 `RPT_DAILYBILLBOARD_DETAILSNEW` 按日去重 `SECURITY_CODE` 数**连续 5 个交易日完全一致**（69/67/78/73/81） |
| `N003` | `net_amount_yuan` | 子码 `00101` 的「全部」汇总行同时给出 `N002=69 / N003=-12.52亿 / N004=273.5亿`，说明 N003 是金额列；符号/量级与龙虎榜净额一致。但与东财 `BILLBOARD_NET_AMT` 汇总**不完全一致**（10-29 TDX -12.52亿 vs 东财 -9.76亿），故标注为 **TDX 复盘口径**，只建议看方向/量级 |
| `N004`–`N006` | **不映射** | 满足恒等式 `N002 = N004 + N005 + N006`（5 日逐日成立），但无法证实各自语义（非涨跌/净买卖/机构家数/市场分布；已逐一排除常见假设），原值保留在 `raw` |

### 工具输出

- `rows`: `date` / `listed_count` / `net_amount_yuan` / `raw{N004,N005,N006}`
- `mapped_fields` / `unmapped_fields` / `note` / `provenance`
- `date` 可选、`max_rows` 默认 60（最大 120）

### 验证

| 项 | 结果 |
|---|---|
| 单测 | `tests/test_tdxdata_deep.py` 新增 2 项（映射/截断/raw、空表报错），共 9 项 |
| 全量离线 | **995 passed / 7 deselected** |
| 真实数据 | 2026-10-29：69 / -12.52亿 / raw 67,2,0；2026-10-28：67 / +4.56亿 / raw 67,0,0 |
| 交叉验证 | N002 与东财逐日去重股票数 5 日完全一致；N003 与东财净额不完全一致（已标注） |

### 20.1 `lhzy` / `lhbsj` 龙虎榜个股明细（同版本 v3.6.0）

新增工具 `tdx_get_lhb_stocks(date="", view="list"|"detail", max_rows=0)`（工具 145→146）。

| 产品 | 命令 / 参数 | 输出 |
|---|---|---|
| `lhbsj`（list） | `DataAggregation.lhbsj`，`["00103", date, 0,0,"",1,365]` | 全量个股行；同一股票可因不同异动原因多行 |
| `lhzy`（detail） | `CWServ.ph_zt_lhb_sy_zy`，`["00101","-1", date,"6","1","1",""]` | 上游前 20，含净买占比/异动码/标签/概念 |

列义证据（东财 `RPT_DAILYBILLBOARD_DETAILSNEW` 逐股核对）：

| 列 | 映射 | 证据 |
|---|---|---|
| `lhzy.N002` / `lhbsj.N003` | market（0深/1沪/2北） | 沪深北样本逐只核对 |
| `lhzy.N004` / `lhbsj.N005` | change_pct | 000568/002747/002149/301520 与东财 `CHANGE_RATE` 一致 |
| `lhzy.N005` / `lhbsj.N007` | amount_yuan | 与东财 `ACCUM_AMOUNT` 一致（如 000568 3,116,403,500 vs 3,116,403,391） |
| `lhzy.N006` / `lhbsj.N006` | net_buy_amount_yuan | 与东财 `BILLBOARD_NET_AMT` 一致（如 460,963,000 vs 460,963,059.86） |
| `lhzy.N007` | net_buy_ratio_pct | = N006/N005×100（14.79% 逐股验算） |
| `lhbsj.N004` | price | 77.070 / 29.240 / 33.570 与东财 `CLOSE_PRICE` 一致 |
| `lhzy.N008` | reason_code | TDX 内部异动码，未翻译 |
| `lhzy.N009/N010/N013` | date/tags/concepts | 日期与 JSON list（概念含 code/name/setcode） |
| `lhzy.N011/N012` | raw | 上游空值/UI 字段 |

验证：`tests/test_tdxdata_deep.py` 共 13 项（lhfp 2 + 个股明细 4 + 其他），
全量离线 **999 passed / 7 deselected**；真实数据 `lhbsj` 92 行、`lhzy` 20 行。
