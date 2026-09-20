# tradex-hub 接口与安装手册

> **版本**：v3.3.29 ｜ **接口总数**：129 个 MCP 工具 ｜ **数据范围**：A 股 + 全球指数/美股/外汇 + 宏观
> **仓库**：https://github.com/wolfjkd/tradex-hub ｜ **许可**：Apache-2.0 ｜ **作者**：郭良勇 (wolfjkd)
> **文档生成**：2026-09-18，接口清单由源码 AST 自动提取（`tools/` 目录 23 个模块）

---

## 目录

- [一、项目概览](#一项目概览)
- [二、安装部署](#二安装部署)
- [三、运行与接入 AI Agent](#三运行与接入-ai-agent)
- [四、字段与数据约定](#四字段与数据约定)
- [五、接口函数总览](#五接口函数总览)
- [六、接口详细说明（129 个）](#六接口详细说明129-个)
- [七、环境变量参考](#七环境变量参考)
- [八、故障排查](#八故障排查)
- [附录 A：工具统计](#附录-a工具统计)

---

## 一、项目概览

tradex-hub 是一个面向 AI Agent（WorkBuddy / Claude Code / Cursor / Dify / LangChain）的
**A 股金融数据 + 量化计算 + 决策支持 MCP Server**，基于 AKShare、eltdx 通达信协议、
astock_signals 信号模块构建，提供 **129 个 MCP 工具**。

### 1.1 三层能力模型

| 层级 | 能力 | 工具数 | 说明 |
|------|------|--------|------|
| **L1 数据获取层** | 行情 / 财务 / 估值 / 行业 / 新闻 / 宏观 / 涨停板 / 龙虎榜 / 五档盘口 / 逐笔 / 分时 / 题材 / 短线指标 | 105 | 通过 `SmartRouter` 统一获取，自动健康评分 / 降级 / 故障隔离 |
| **L2 计算引擎层** | 技术指标计算 6 个、绩效指标计算 2 个 | 8 | 纯函数实现，结果与 Excel / 通达信一致 |
| **L3 决策支持层** | 交易信号 3、多因子 2、条件选股 2、系统诊断 5、综合分析 3、技术分析引擎 1 | 16 | 组合调用 L1+L2，输出可执行结论 |
| | | **129** | |

### 1.2 数据源架构

- **data_sources 数据源层**：78 个数据类型、102 个数据源注册（37 种源），按封禁风险分三梯队
- **SmartRouter 全量覆盖**：L1 工具通过 `SmartRouter.route()` 统一取数，自动健康评分 / 降级 / 故障隔离
- **数据源梯队**：
  - 第一梯队（不封 IP）：`eltdx` 通达信 / 腾讯 / 本地 vipdoc
  - 第二梯队（低风险）：同花顺 / 新浪 / 巨潮 / 财联社
  - 第三梯队（仅独有数据 + 限流防封）：东财 push2 / push2ex / slist，请求间隔 ≥1s + 随机抖动
- **统一防封层（v3.3.19 起）**：所有 HTTP 数据源请求收敛到 `astock_signals/antiban_http.py`
  —— TLS 指纹伪装 + **按源独立**的串行节流 + 指数退避重试 + 限流换指纹。
  按源分设间隔（东财 1.0s / 同花顺 0.25s / 财联社·巨潮 0.30s / 新浪 0.25s / 问财 0.50s），
  低风险源不会被东财的间隔拖慢；有 **AST 静态守卫测试**确保没有模块绕过该入口。
  > 例外：巨潮 `cninfo.com.cn` 的 WAF 封禁常见浏览器指纹（`chrome120`/`chrome124` → 403），
  > 该源固定关闭指纹伪装（`NO_IMPERSONATE`），仅保留节流与重试。
- **主机镜像降级（v3.3.21）**：东财节点故障是按主机发生的，`em_get` 在主源
  **连接层失败或 5xx** 时自动切镜像主机（`push2` → `push2delay`），
  并在成功后进入静默期避免重复撞死主机。按 IP 的风控（403/429/rc=102）不切主机。
- **失败熔断（v3.3.23）**：某 `(主机, 路径)` 主机级失败后冷却 60s，期间直接返回
  `None` 让调用方走备源。按 **主机+路径** 计，不误伤同主机上健康的路径。
- **`em_post()` 统一 POST 入口（v3.3.24）**：与 `em_get` 共用节流 / 重试 / 镜像 / 熔断，
  避免 POST 类接口（如 emappdata 人气榜）成为绕过防封层的后门。
- **eltdx 3.2.2**：行情类第一主源，31 个工具；3.x 为 Rust 重写内核（native wheel，cp310-abi3）
- **数据源看板**：`python -m tradex.dashboard`（端口 8765），也可通过 MCP 工具 `get_data_source_dashboard` 查询

> **网络边界铁律**：数据源流量（AKShare / eltdx / 东财等）**永远直连**，import 时已清代理环境变量；
> 本机代理仅用于 GitHub push/clone，禁止用作服务或网关地址。

### 1.3 目录结构

```
tradex-hub/
├── tradex/                      # 主包（MCP Server）
│   ├── src/tradex/
│   │   ├── server.py            # FastMCP 实例 + 工具自动发现注册
│   │   ├── __main__.py          # 入口：stdio / --http
│   │   ├── http_server.py       # HTTP 网关（/mcp + /sse + /health）
│   │   ├── config.py            # 环境变量配置中心
│   │   ├── tools/               # ★ 23 个工具模块（129 个接口）
│   │   ├── data_sources/        # 数据源层 + SmartRouter 注册表
│   │   ├── utils/               # symbol / formatter / cache 等
│   │   └── dashboard/           # 数据源看板（端口 8765）
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── .env.example
├── tests/                       # 根级集成测试
├── docs/architecture.md
├── scripts/health_check.py
├── start_dashboard.bat          # Windows 一键启动看板
└── README.md
```

---

## 二、安装部署

### 2.1 环境要求

| 项目 | 要求 |
|------|------|
| Python | **≥ 3.10**（README 标注 3.13+；`pyproject.toml` 声明 `>=3.10`，分类器覆盖 3.10–3.13）|
| 操作系统 | Windows / Linux / macOS |
| 网络 | 需可访问国内财经数据源；**建议关闭系统代理或配置直连** |
| Node.js | 仅在使用 `get_wencai_query`（同花顺问财）时需要 |

### 2.2 源码安装（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/wolfjkd/tradex-hub.git
cd tradex-hub

# 2. 创建独立 venv
python -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows
```

```bash
# 3. 安装 astock_signals 独立包（v1.1.0）
pip install -e astock_signals/
```

> 若仓库内无 `astock_signals/` 目录（v3.3.10 起已并入本仓 `tradex/src/astock_signals`），
> 可跳过此步；仅当独立包缺失时从 GitHub 补装：
> `git clone https://github.com/wolfjkd/astock_signals.git && pip install -e astock_signals/`

```bash
# 4. 安装 tradex 本体（可编辑安装）
cd tradex
pip install hatchling editables
pip install --no-build-isolation -e .

# 5. 安装运行时依赖
pip install "akshare>=1.18.94" mcp pandas pydantic "eltdx>=3.2.2"
```

> **`mcp` 版本锁定 `>=1.0.0,<2`**：mcp 2.x 移除了 `mcp.server.fastmcp.FastMCP` v1 API，
> 本项目 15+ 模块在用，新环境若解析到 2.x 会直接 `ImportError`。`pyproject.toml` 与
> `requirements.txt` 两处已同步锁定，请勿手动升级到 2.x。

### 2.3 一步安装（依赖清单法）

```bash
cd tradex
pip install -r requirements.txt
pip install --no-build-isolation -e .
```

`requirements.txt` 内容：

```
mcp>=1.0.0,<2
akshare>=1.18.94
eltdx>=3.2.2
pandas>=2.0.0
pydantic>=2.0.0
requests>=2.28.0
stockstats>=0.6.0
python-dotenv>=1.0.0
# HTTP/SSE 模式（可选）
uvicorn>=0.29.0
starlette>=0.37.0
sse-starlette>=1.6.0
```

### 2.4 可选依赖组

```bash
pip install "tradex[http]"    # HTTP 网关：uvicorn / starlette / sse-starlette
pip install "tradex[ws]"      # WebSocket 推送：websockets>=12.0
pip install "tradex[dev]"     # 开发测试：pytest / pytest-asyncio
pip install pywencai          # 仅 get_wencai_query 需要
```

### 2.5 Docker 部署

```bash
cd tradex
docker compose up -d --build
```

- 镜像基于 `python:3.12-slim`，默认以 **HTTP 模式**启动并监听 `0.0.0.0:8000`
- 健康检查探测 `http://localhost:8000/health`（间隔 30s，超时 10s，重试 3 次）
- `restart: unless-stopped` 自动重启

### 2.6 验证安装

```bash
python -m tradex --help          # 查看 CLI 参数
curl http://localhost:8000/health  # HTTP 模式：应返回 tools 计数
python -m pytest tests/ -m "not network"   # 离线回归测试
```

---

## 三、运行与接入 AI Agent

### 3.1 stdio 模式（本地开发，默认）

```bash
python -m tradex
```

编辑 AI Agent 的 MCP 配置文件（如 `~/.trae-cn/mcp.json`、WorkBuddy 的 `~/.workbuddy-ai/mcp.json`）：

```json
{
  "mcpServers": {
    "tradex": {
      "type": "stdio",
      "command": "/path/to/venv/Scripts/python.exe",
      "args": ["-m", "tradex"],
      "env": {}
    }
  }
}
```

> Windows 下 `command` 建议填 venv 内 `python.exe` 的**绝对路径**，避免解释器错位。

### 3.2 HTTP 网关模式（远程 / 容器 / 多客户端）

```bash
python -m tradex --http                                    # 默认 0.0.0.0:8000
python -m tradex --http --port 9000                        # 自定义端口
python -m tradex --http --allowed-hosts 47.102.x.x,example.com  # 对外部署：Host 白名单
python -m tradex --http --allowed-hosts '*'                # 仅可信内网：关闭 Host 校验
```

单端口同时提供三种端点：

| 端点 | 传输 | 说明 |
|------|------|------|
| `POST /mcp` | Streamable HTTP | MCP 2025 新标准，Dify / LangChain / Claude 远程客户端首选 |
| `GET /sse` + `POST /messages/` | legacy SSE | 旧客户端兼容（MCP 官方已标 deprecated）|
| `GET /health` | HTTP | `{"status":"ok","service":"tradex-mcp","version":"3.3.29","tools":129}`，轻量探活不触发数据源 |

**Dify / LangChain 配置**：URL 填 `http://<服务器IP或域名>:8000/mcp`，类型选 `streamable-http`
（旧端点则用 `http://<服务器IP或域名>:8000/sse`）。

> **Host 校验（DNS rebinding 防护）**：默认仅允许本机回环访问，伪造 Host 返回 `421`。
> 监听默认 `0.0.0.0`，对外访问需用 `--allowed-hosts` 或 `MCP_ALLOWED_HOSTS` 放行目标 IP/域名；
> `*` 表示关闭校验全放行，**仅限可信内网**。

### 3.3 WebSocket 推送（可选）

```bash
export WS_SERVER_ENABLED=true
export WS_PORT=8765
export WS_TOKEN=your-token
python -m tradex
```

WebSocket 服务运行在独立线程 + 独立事件循环，启动失败不会阻断 MCP Server。

### 3.4 数据源看板

```bash
python -m tradex.dashboard        # 端口 8765
start_dashboard.bat               # Windows 一键启动（支持 --check 依赖检查）
```

可视化查看数据源健康度 / 路由链路 / 工具分布。

### 3.5 连接验证

重启 AI Agent 后，在对话里测试：

```
查中国能建（601868）K线
```

Agent 应调用 `eltdx_get_kline` 并返回日 K 线数据；连接器页面 `tradex` 显示绿色即接入成功。

---

## 四、字段与数据约定

### 4.1 股票代码 `symbol` 格式

| 形式 | 示例 | 适用场景 |
|------|------|----------|
| 6 位纯数字 | `600519`、`000001`、`300750` | **绝大多数接口的默认形式** |
| 交易所前缀 | `sh600519`、`sz000001`、`bj830799` | 部分接口（如 `get_hot_rank`、`get_hot_keywords`）要求大写前缀：`SZ000665` |
| 指数代码 | `sh000001`（上证）、`sz399001`（深证成指）、`sz399006`（创业板指）、`sh000300`（沪深300）、`sh000688`（科创50）、`sh000905`（中证500）、`sz399852`（中证1000）| 指数类接口 |
| 全球行情代码 | `usDJI`、`usIXIC`、`usINX`、`usNVDA`、`usTSLA`、`hkHSI`、`hkHSTECH`、`kr005930`、`whDINIW` | `get_realtime_quote` / `get_global_market_quote` |

**交易所归属规则**：

| 代码段 | 市场 | 前缀 |
|--------|------|------|
| `600xxx` / `601xxx` / `603xxx` / `605xxx` | 上交所主板 | `sh` |
| `688xxx` | 上交所科创板 | `sh` |
| `000xxx` / `001xxx` / `002xxx` / `003xxx` | 深交所主板/中小板 | `sz` |
| `300xxx` / `301xxx` | 深交所创业板 | `sz` |
| `4xxxxx` / `8xxxxx` | 北交所 | `bj` |

> ⚠️ `000001` 同时是平安银行（SZ）与上证指数（SH），仅靠裸代码无法区分。
> 指数请显式使用带前缀代码 `sh000001`。

### 4.2 日期格式

接口间日期格式**不统一**，传参前请对照接口说明：

| 格式 | 示例 | 常见于 |
|------|------|--------|
| `YYYY-MM-DD` | `2026-09-18` | `signal_data_*` 系列（`date` / `trade_date` / `curr_date`）|
| `YYYYMMDD` | `20260918` | `news_events` 系列（`date`）、`get_fund_hold` |
| 空字符串 `""` | — | 表示"默认最新"（今天 / 最新一期 / 上一季度末）|

### 4.3 响应格式与错误结构

- 所有工具**返回 JSON 字符串**（`ensure_ascii=False`，中文不转义）
- 列表类数据默认 `orient="records"`，即 `[{...}, {...}]`
- 输出前会自动**丢弃全空列**、剔除内部元数据列（`SECUCODE`、`ORG_CODE`、`CURRENCY` 等）
  与 `*_QOQ` 环比冗余列，保证 LLM 友好
- 出错时返回统一错误结构：

```json
{
  "error": true,
  "message": "错误描述",
  "tool": "工具名"
}
```

> 判断调用是否成功：检查是否存在 `error` 字段为 `true`。

### 4.4 财务字段命名与单位

财务报表类接口（`get_income_statement` / `get_balance_sheet` / `get_cash_flow_statement`）
已将东财原始英文列名映射为**中文列名并折算为亿元**，无需二次换算：

| 报表 | 典型字段（单位：亿元）|
|------|------------------------|
| 利润表 | `报告期`、`营业总收入(亿)`、`营业成本(亿)`、`研发费用(亿)`、`营业利润(亿)`、`归母净利润(亿)`、`扣非归母净利润(亿)`、`基本每股收益(元)` |
| 资产负债表 | `货币资金(亿)`、`应收账款(亿)`、`存货(亿)`、`总资产(亿)`、`总负债(亿)`、`商誉(亿)`、`归母股东权益(亿)` |
| 现金流量表 | `经营活动现金流净额(亿)`、`投资活动现金流净额(亿)`、`筹资活动现金流净额(亿)`、`现金净增加额(亿)` |

- 金额单位：**亿元**（原值 ÷ 1e8，保留 2 位小数）
- 每股指标单位：**元**
- 报告期字段：`报告期`（如 `2025-12-31`）

### 4.5 周期与复权参数

| 参数 | 取值 |
|------|------|
| 周期 `period` / `freq` | `daily`（日）/ `weekly`（周）/ `monthly`（月）；eltdx 亦支持分钟级 |
| 复权 `adjust` | `qfq`（前复权，默认）/ `hfq`（后复权）/ `""`（不复权）|

> 部分接口兼容 `day` 等 eltdx 风格写法，内部会归一化为 `daily`。

### 4.6 数据源优先级（SmartRouter）

| 数据类型 | 主源 → 备源 |
|----------|-------------|
| 实时行情 | `eltdx(TCP)` → `akshare` → `tencent_http` |
| 历史 K 线 | `eltdx` → `akshare` |
| 分时数据 | `eltdx` → `akshare` |
| 集合竞价 / 逐笔 / F10 | `eltdx` **独占** |
| 财务报表 / 行业板块 / 宏观 | `akshare` |
| 个股资金流 | `em_push2` → `akshare` |
| 龙虎榜 | `em_datacenter` → `akshare` |
| 北向资金 | `ths_hsgt` → `akshare` |
| 涨停板池 | `em_push2_clist` **独占** |

> 路由自带 **12s 超时保护**、健康评分、故障源半开探测自愈；单源失败自动降级，
> 多源全挂时返回错误结构而非静默空数据。

---

## 五、接口函数总览

| # | 分类 | 源码模块 | 工具数 | 接口名 |
|---|------|----------|--------|--------|
| 1 | 公司信息 | `company_info.py` | 4 | `search_stock`、`get_company_info`、`get_company_profile`、`get_competitors` |
| 2 | 行情数据 | `price_data.py` | 5 | `get_realtime_quote`、`get_historical_price`、`get_intraday_data`、`get_market_capitalization`、`get_stock_list` |
| 3 | 财务报表 | `financial_stmt.py` | 8 | `get_income_statement`、`get_balance_sheet`、`get_cash_flow_statement`、`get_financial_line_item`、`get_financial_indicators`、`get_growth_rates`、`get_per_share_data`、`get_segments_revenue` |
| 4 | 估值分析 | `valuation.py` | 4 | `get_valuation_metrics`、`get_dividend_data`、`get_institutional_holdings`、`get_analyst_rating` |
| 5 | 行业板块 | `industry.py` | 5 | `get_industry_list`、`get_industry_stocks`、`get_concept_list`、`get_sector_fund_flow`、`get_industry_pe` |
| 6 | 市场总览 | `market.py` | 7 | `get_market_overview`、`get_index_volume_compare`、`get_money_flow`、`get_north_bound_flow`、`get_limit_up_down`、`get_dragon_tiger`、`get_global_market_quote` |
| 7 | 新闻公告 | `news_events.py` | 14 | `get_stock_news`、`get_financial_calendar`、`get_company_announcements`、`get_telegraph_news`、`search_news`、`get_market_sentiment`、`get_futures_news`、`get_hot_rank`、`get_hot_keywords`、`get_xueqiu_hot`、`get_fund_hold`、`get_hot_search`、`get_wencai_query`、`get_wencai_news` |
| 8 | 宏观衍生 | `macro_fx.py` | 8 | `get_macro_gdp`、`get_macro_cpi`、`get_macro_pmi`、`get_macro_money_supply`、`get_fx_rate`、`get_bond_yield_curve`、`get_margin_trading`、`get_insider_trading` |
| 9 | A股信号·基础 | `signal_data_base.py` | 6 | `get_hot_stocks`、`get_lockup_expiry`、`get_concept_attribution`、`get_profit_forecast`、`get_technical_indicator`、`list_technical_indicators` |
| 10 | A股信号·资金流 | `signal_data_flow.py` | 4 | `get_northbound_flow_signal`、`get_fund_flow_signal`、`get_dragon_tiger_signal`、`get_industry_comparison_signal` |
| 11 | A股信号·ETF | `signal_data_etf.py` | 2 | `get_etf_realtime_data`、`get_etf_kline_data` |
| 12 | A股信号·可转债 | `signal_data_cb.py` | 2 | `get_cb_realtime_data`、`get_cb_value_analysis_data` |
| 13 | A股信号·涨停板 | `signal_data_board.py` | 3 | `get_limit_up_board`、`get_board_sentiment`、`get_limit_up_insight` |
| 14 | eltdx 通达信协议 | `eltdx_data.py` | 31 | 见 [14 节](#14-eltdx-通达信协议31-个) |
| 15 | 技术指标计算（L2）| `technical_indicators.py` | 6 | `calculate_ma_ema`、`calculate_macd`、`calculate_kdj`、`calculate_rsi`、`calculate_boll`、`calculate_atr` |
| 16 | 绩效指标计算（L2）| `performance_metrics.py` | 2 | `calculate_performance`、`list_performance_metrics` |
| 17 | 信号生成（L3）| `signal_generation.py` | 3 | `generate_trading_signal`、`scan_stocks_for_signals`、`validate_signal_quality` |
| 18 | 因子分析（L3）| `factor_analysis.py` | 2 | `calculate_factor_score`、`get_factor_catalog` |
| 19 | 条件选股（L3）| `stock_screening.py` | 2 | `screen_stocks`、`get_screening_conditions` |
| 20 | 综合分析（L3）| `composite_analysis.py` | 3 | `analyze_stock_comprehensive`、`analyze_industry_comparison`、`analyze_market_overview` |
| 21 | 技术分析引擎（L3）| `analysis_engine.py` | 1 | `analyze_technical` |
| 22 | 系统诊断 | `diagnostics.py` | 5 | `get_data_source_health`、`list_all_tools`、`get_cache_stats`、`health_check`、`get_data_source_dashboard` |
| 23 | 本地数据 | `local_data.py` | 2 | `get_local_kline`、`get_local_minute` |
| | | | **129** | |

> 上表与第六节清单**以源码为准**自动生成。README 部分小节（如"市场总览 5 个"、
> "新闻公告 4 个"）为历史版本口径，实际工具数已随后续版本扩充。

---

## 六、接口详细说明（129 个）

> 参数表中"必填"依据函数签名是否有默认值判定；`—` 表示无默认值（必填）。

### 1. 公司信息（4 个）

> 源码模块：`tradex/src/tradex/tools/company_info.py`

#### `search_stock`

搜索A股股票，支持名称或代码模糊匹配。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keyword` | string | **是** | — | 搜索关键词，可以是股票名称（如"贵州茅台"）或代码（如"600519"）|

**返回**：匹配的股票列表 (JSON)，包含代码(code)和名称(name)字段，最多返回20条。

#### `get_company_info`

获取A股公司基本信息，包括行业、市值、股本等。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "000001"（平安银行）、"600519"（贵州茅台）|

**返回**：公司基本信息 (JSON)，包含总市值、流通市值、行业、上市日期等。

#### `get_company_profile`

获取公司主营业务构成和业务描述。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "000001"（平安银行）|

**返回**：公司主营业务构成 (JSON)，包含各业务的营收占比、毛利率等。

#### `get_competitors`

获取同行业公司列表（竞争对手/可比公司）。

先根据股票代码查找所属行业板块，然后返回该板块的所有成分股。
也可以直接传入行业名称来查询。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519"。如果同时提供了 industry 参数则忽略此参数。|
| `industry` | string | 否 | `""` | 行业板块名称，如 "白酒"、"银行"。如果为空则自动从 symbol 推断。|

**返回**：同行业公司列表 (JSON)，包含代码、名称、最新价、涨跌幅等。

### 2. 行情数据（5 个）

> 源码模块：`tradex/src/tradex/tools/price_data.py`

#### `get_realtime_quote`

获取实时行情数据。支持A股6位代码和全球行情代码。

**A股代码**：6位数字，如 "600519"（贵州茅台）、"000001"（平安银行）。

**全球行情代码**：
- 美股指数：usDJI（道琼斯）、usIXIC（纳斯达克）、usINX（标普500）
- 热门美股：usNVDA（英伟达）、usTSLA（特斯拉）、usAAPL（苹果）、usMSFT（微软）、usAMZN（亚马逊）、usGOOGL（谷歌）、usMETA（Meta）、usMU（美光科技）、usAMAT（应用材料）
- 亚太指数：hkHSI（恒生指数）、hkHSTECH（恒生科技）
- 韩股：kr005930（三星电子）、kr000660（SK海力士）
- 外汇：whDINIW（美元指数）

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 股票代码，A股为6位数字，全球行情使用前缀代码 |

**返回**：实时行情数据 (JSON)，包含最新价、涨跌幅、成交量、成交额、最高价、最低价、开盘价、昨收价、换手率、市盈率、市净率等。

#### `get_historical_price`

获取A股股票历史K线数据 (OHLCV)。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `period` | string | 否 | `"daily"` | K线周期，可选 "daily"（日线）, "weekly"（周线）, "monthly"（月线）|
| `start_date` | string | 否 | `""` | 开始日期，格式 "YYYYMMDD"，如 "20240101"。默认为空返回所有数据。|
| `end_date` | string | 否 | `""` | 结束日期，格式 "YYYYMMDD"，如 "20241231"。默认为空返回至今。|
| `adjust` | string | 否 | `"qfq"` | 复权类型，"qfq"（前复权）, "hfq"（后复权）, ""（不复权）|

**返回**：K线数据 (JSON)，包含日期、开盘价、收盘价、最高价、最低价、成交量、成交额、振幅、涨跌幅、涨跌额、换手率。

#### `get_intraday_data`

获取A股股票当日分时数据（1分钟K线）。

返回当天从开盘到当前的每分钟价格、均价、成交量数据，
可用于绘制分时图。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：分时数据 (JSON)，包含时间、价格、均价、成交量。

#### `get_market_capitalization`

获取A股股票的总市值和流通市值。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：市值数据 (JSON)，包含总市值、流通市值、最新价、成交量等关键字段。

#### `get_stock_list`

获取A股完整股票列表，附带行情摘要信息。可按市值筛选。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `min_market_cap` | number | 否 | `0` | 最低总市值过滤（单位：亿元），默认0不过滤。如传入100则只返回市值>=100亿的股票。|
| `max_results` | integer | 否 | `50` | 最大返回条数，默认50。|

**返回**：股票列表 (JSON)，包含代码、名称、最新价、涨跌幅、总市值、流通市值、成交量、成交额、市盈率、市净率等。

### 3. 财务报表（8 个）

> 源码模块：`tradex/src/tradex/tools/financial_stmt.py`

#### `get_income_statement`

获取利润表（按季度）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_quarters` | integer | 否 | `8` | 返回最近几个季度的数据，默认8个季度（2年）|

**返回**：利润表数据 (JSON)，包含营业收入、营业成本、毛利润、净利润、研发费用、销售费用、管理费用等字段。

#### `get_balance_sheet`

获取资产负债表（按季度）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_quarters` | integer | 否 | `8` | 返回最近几个季度的数据，默认8个季度 |

**返回**：资产负债表数据 (JSON)，包含总资产、总负债、股东权益、流动资产、非流动资产、存货、应收账款等。

#### `get_cash_flow_statement`

获取现金流量表（按季度）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_quarters` | integer | 否 | `8` | 返回最近几个季度的数据，默认8个季度 |

**返回**：现金流量表数据 (JSON)，包含经营活动现金流、投资活动现金流、筹资活动现金流、自由现金流等。

#### `get_financial_line_item`

从三大财务报表中提取特定财务科目的时间序列数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `item` | string | **是** | — | 要提取的科目名称，如 "营业总收入"、"净利润"、"基本每股收益"、"经营活动产生的现金流量净额"、"总资产" 等。支持模糊匹配。|
| `num_quarters` | integer | 否 | `8` | 返回最近几个季度的数据，默认8个季度 |

**返回**：该科目的时间序列数据 (JSON)，包含报告期和对应值。

#### `get_financial_indicators`

获取财务分析指标（ROE、毛利率、净利率、资产负债率等）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_periods` | integer | 否 | `8` | 返回最近几期数据，默认8期 |

**返回**：财务指标数据 (JSON)，包含盈利能力、偿债能力、运营能力、成长能力等多维度指标。

#### `get_growth_rates`

获取成长性指标（营收增长率、净利润增长率等）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_periods` | integer | 否 | `8` | 返回最近几期数据，默认8期 |

**返回**：成长性指标数据 (JSON)，包含各项增长率。

#### `get_per_share_data`

获取每股指标（每股收益EPS、每股净资产BPS、每股现金流CFPS等）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_periods` | integer | 否 | `8` | 返回最近几期数据，默认8期 |

**返回**：每股指标数据 (JSON)，包含EPS、BPS、每股经营现金流等。

#### `get_segments_revenue`

获取公司主营业务构成（按产品/地区分拆营收）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：主营构成数据 (JSON)，包含各业务板块的营收、占比、毛利率等。

### 4. 估值分析（4 个）

> 源码模块：`tradex/src/tradex/tools/valuation.py`

#### `get_valuation_metrics`

获取股票历史估值指标（PE、PB、PS等市盈率/市净率/市销率时间序列）。

可用于估值分析、历史百分位计算、可比公司估值对比等。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `num_periods` | integer | 否 | `100` | 返回最近几个交易日的数据，默认100 |

**返回**：估值指标时间序列 (JSON)，包含日期、PE、PB、PS、总市值、流通市值等。

#### `get_dividend_data`

获取股票历史分红派息详情。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：分红历史 (JSON)，包含分红年度、每股派息、除权日、股权登记日等。

#### `get_institutional_holdings`

获取机构持股/十大流通股东数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：流通股东数据 (JSON)，包含股东名称、持股数量、持股比例、变动情况等。

#### `get_analyst_rating`

获取分析师评级和盈利预测数据。

可查询特定股票的分析师评级，或获取全市场最新评级列表。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `""` | 6位股票代码，如 "600519"。为空则返回全市场最新评级。|
| `num_results` | integer | 否 | `20` | 最大返回条数，默认20 |

**返回**：分析师评级数据 (JSON)，包含评级机构、目标价、评级、预测EPS等。

### 5. 行业板块（5 个）

> 源码模块：`tradex/src/tradex/tools/industry.py`

#### `get_industry_list`

获取行业板块列表。

_无入参。_

**返回**：行业板块列表 (JSON)，包含板块名称、涨跌幅、总市值、换手率、领涨股等信息。

#### `get_industry_stocks`

获取指定行业板块的成分股列表。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `industry` | string | **是** | — | 行业板块名称，如 "白酒"、"银行"、"半导体"、"新能源车" |

**返回**：该行业所有成分股 (JSON)，包含代码、名称、最新价、涨跌幅、市盈率、市净率等。

#### `get_concept_list`

获取概念板块列表。

概念板块如：华为概念、ChatGPT、锂电池、芯片、光伏等。

_无入参。_

**返回**：概念板块列表 (JSON)，包含板块名称、涨跌幅、总市值、换手率、领涨股等信息。

#### `get_sector_fund_flow`

获取板块资金流向排名。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `sector_type` | string | 否 | `"行业资金流"` | 资金流类型，可选："行业资金流" - 行业板块资金流向 "概念资金流" - 概念板块资金流向 "地域资金流" - 地域板块资金流向 |
| `indicator` | string | 否 | `"今日"` | 时间维度，可选："今日"、"5日"、"10日"，默认 "今日" |

**返回**：板块资金流向排名 (JSON)，包含板块名称、今日主力净流入、今日超大单净流入、今日大单净流入等。

#### `get_industry_pe`

获取行业板块历史行情数据（可用于计算行业PE估值趋势）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `industry` | string | **是** | — | 行业板块名称，如 "白酒"、"银行" |
| `start_date` | string | 否 | `""` | 开始日期，格式 "YYYYMMDD"，如 "20240101" |
| `end_date` | string | 否 | `""` | 结束日期，格式 "YYYYMMDD" |

**返回**：行业板块历史行情 (JSON)，包含日期、开盘价、收盘价、最高价、最低价、成交量、成交额、振幅、涨跌幅等。

### 6. 市场总览（7 个）

> 源码模块：`tradex/src/tradex/tools/market.py`

#### `get_market_overview`

获取A股主要指数实时行情快照。

包含上证指数、深证成指、创业板指、科创50、沪深300、中证500等。

_无入参。_

**返回**：主要指数实时行情 (JSON)，包含指数名称、最新点位、涨跌幅、成交量、成交额等。

#### `get_index_volume_compare`

获取三大指数（上证/深证/创业板）近 days 个交易日的量能序列，
用于盘后复盘「量能对比」柱状图。

返回 JSON：
{
  "sh000001": {
    "name": "上证指数",
    "series": [{"date": "2026-08-13", "volume_hand": 572793677, "volume_yi_share": 5.73,
                "amount_yuan": 1164203068530, "amount_yi": 11642.03}, ...]
  },
  ...
}
- volume_hand: 成交量(手)
- volume_yi_share: 成交量(亿手)
- amount_yuan: 成交额(元)，东财源才有，腾讯源为 null
- amount_yi: 成交额(亿元)
单指数取数失败则在对应项中返回 {"series": [], "error": "..."}。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `days` | integer | 否 | `6` | 回溯的交易日数量，默认 6 个交易日 |

#### `get_money_flow`

获取个股资金流向数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：资金流向数据 (JSON)，包含日期、主力净流入、超大单净流入、大单净流入、中单净流入、小单净流入等。

#### `get_north_bound_flow`

获取北向资金（沪股通+深股通）净流入数据。

北向资金是境外投资者通过港交所买入A股的资金，是市场重要的
情绪和趋势指标。

_无入参。_

**返回**：北向资金流入时间序列 (JSON)，包含日期、沪股通净流入、深股通净流入、北向资金合计净流入等。

#### `get_limit_up_down`

获取当日涨停板或跌停板股票池。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `direction` | string | 否 | `"涨停"` | "涨停" 获取涨停板，"跌停" 获取跌停板 |

**返回**：涨停/跌停股票列表 (JSON)，包含代码、名称、涨跌幅、封单额、首次涨停/跌停时间、最后涨停/跌停时间、连板天数等。

#### `get_dragon_tiger`

获取龙虎榜数据（机构和游资活跃买卖记录）。

龙虎榜是沪深交易所公布的异动股票交易席位信息，反映机构和
大型游资的交易行为。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `num_days` | integer | 否 | `5` | 返回最近几个交易日的数据，默认5天 |

**返回**：龙虎榜数据 (JSON)，包含股票代码、名称、上榜原因、买入额、卖出额、净买入额、买方营业部等。

#### `get_global_market_quote`

获取全球市场行情快照。v3.3.1 新增，通过腾讯接口批量获取。

包含美股三大指数、热门美股（英伟达/特斯拉/苹果/微软/亚马逊/谷歌/Meta/美光/应用材料）、
亚太指数（恒生/恒生科技）、韩股龙头（三星/SK海力士）、美元指数。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `category` | string | 否 | `""` | 可选分类过滤，如 "美股指数"、"热门美股"、"亚太指数"、"韩股"、"外汇"。为空时返回全部。|

**返回**：全球市场行情 (JSON)，包含代码、名称、类别、最新价、涨跌额、涨跌幅等。

### 7. 新闻公告（14 个）

> 源码模块：`tradex/src/tradex/tools/news_events.py`

#### `get_stock_news`

获取个股相关新闻资讯。v3.2.0 起直连东财 search-api-web，更稳定。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |

**返回**：新闻列表 (JSON)，包含新闻标题、发布时间、来源、摘要、链接等。

#### `get_financial_calendar`

获取上市公司财报披露时间表和经济数据日历。v3.3.0 新增百度经济数据日历补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `date` | string | 否 | `""` | 查询日期，格式 "YYYYMMDD"。默认为空获取最新一期。|

**返回**：财报披露时间表和经济数据日历 (JSON)，包含股票代码、名称、预计披露日期、实际披露日期、报告类型、经济数据等。

#### `get_company_announcements`

获取上市公司公告。v3.2.0 起直连巨潮 cninfo.com.cn，信息更全更及时。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `""` | 6位股票代码，如 "600519"。为空则获取全市场最新公告。|
| `num_results` | integer | 否 | `30` | 最大返回条数，默认30 |

**返回**：公告列表 (JSON)，包含公告标题、发布日期、公告类型、PDF链接等。

#### `get_telegraph_news`

获取全市场实时财经快讯（财联社7×24小时电报）。v3.2.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `num_results` | integer | 否 | `20` | 最大返回条数，默认20，最大50 |

**返回**：快讯列表 (JSON)，包含时间戳、内容、类别、链接等。

#### `search_news`

按关键词搜索财经新闻资讯。

纯新闻搜索，不包含公司公告。数据源：财联社快讯 + 新浪财经 + 百度交易提醒 + 期货新闻 + 百度热搜。

如果提供了股票代码，则在该股票的新闻中搜索关键词；
否则在全市场新闻中搜索。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keyword` | string | **是** | — | 搜索关键词，如 "业绩预增"、"回购"、"增持" |
| `symbol` | string | 否 | `""` | 可选的6位股票代码，用于限定搜索范围 |
| `num_results` | integer | 否 | `20` | 最大返回条数，默认20 |

**返回**：匹配的新闻列表 (JSON)，包含标题、时间、来源、摘要等。

#### `get_market_sentiment`

获取指数新闻情绪数据，反映市场整体情绪倾向。v3.3.0 新增。

_无入参。_

**返回**：指数新闻情绪数据 (JSON)，包含指数代码、情感得分、相关新闻数量等。

#### `get_futures_news`

获取期货/大宗商品相关新闻。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `"全部"` | 品种，默认 "全部" 获取所有品种新闻 |

**返回**：期货新闻列表 (JSON)，包含标题、发布时间、内容等。

#### `get_hot_rank`

获取东方财富个股人气榜数据。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `endpoint` | string | 否 | `"rank"` | 榜单类型。可选值：- "rank": 全市场人气榜（默认）- "up": 飙升榜 - "detail": 个股历史趋势及粉丝特征（需 symbol）- "realtime": 个股实时变动（需 symbol）- "latest": 个股最新排名（需 symbol）- "relate": 相关股票（需 symbol）|
| `symbol` | string | 否 | `""` | 带市场表示的证券代码，如 "SZ000665"。部分 endpoint 必填 |

**返回**：人气榜数据 (JSON)。

#### `get_hot_keywords`

获取指定股票的东方财富热门关联关键词。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `"SZ000665"` | 带市场表示的证券代码，如 "SZ000665" |

**返回**：热门关键词列表 (JSON)。

#### `get_xueqiu_hot`

获取雪球沪深股市热度排行榜数据。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `endpoint` | string | 否 | `"follow"` | 排行榜类型。可选值：- "follow": 关注排行榜（默认）- "tweet": 讨论排行榜 - "deal": 交易排行榜 |
| `symbol` | string | 否 | `"最热门"` | {"最热门", "沪深股市", "创业板", "科创板"} |

**返回**：雪球热度数据 (JSON)。

#### `get_fund_hold`

获取机构持仓数据（基金/QFII/社保/券商/保险/信托）。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `endpoint` | string | 否 | `"hold"` | 数据类型。可选值：- "hold": 机构持仓汇总（默认），symbol 可选 {"基金持仓", "QFII持仓", "社保持仓", "券商持仓", "保险持仓", "信托持仓"} - "detail": 单只基金持仓明细，symbol 为基金代码 |
| `symbol` | string | 否 | `"基金持仓"` | 机构类型或基金代码 |
| `date` | string | 否 | `""` | 财报日期，格式 "YYYYMMDD"。默认上一季度末 |

**返回**：机构持仓数据 (JSON)。

#### `get_hot_search`

获取百度股市通热搜股票排行。v3.3.0 新增。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `"A股"` | 市场范围。可选值：{"全部", "A股", "港股", "美股"} |
| `date` | string | 否 | `""` | 日期，格式 "YYYYMMDD"。默认为当日 |
| `time` | string | 否 | `"今日"` | 时间范围。可选值：{"今日", "1小时"} |

**返回**：百度热搜股票排行 (JSON)。

#### `get_wencai_query`

同花顺问财自然语言查询（需安装 pywencai 和 Node.js）。v3.3.0 新增。

通过自然语言描述筛选条件，获取符合条件的股票/基金/指数列表。
例如："市值大于100亿，市盈率小于30，连续3年ROE大于15%"

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | **是** | — | 自然语言查询语句，如 "市值大于100亿 市盈率小于30" |
| `query_type` | string | 否 | `"stock"` | 查询类型，可选 {"stock", "fund", "index"}，默认 "stock" |
| `loop` | boolean | 否 | `False` | 是否获取所有分页数据，默认 False |

**返回**：查询结果 (JSON)，包含符合条件的标的信息。需要安装 pywencai: pip install pywencai

#### `get_wencai_news`

同花顺问财新闻/公告/研报搜索（需设置 IWENCAI_API_KEY 环境变量）。v3.3.0 新增。

通过 iwencai OpenAPI 搜索财经新闻、公司公告或研究报告。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keyword` | string | **是** | — | 搜索关键词，如 "新能源汽车"、"贵州茅台" |
| `channel` | string | 否 | `"news"` | 搜索频道，可选 {"news": 财经新闻, "announcement": 公司公告, "report": 研究报告}，默认 "news" |
| `limit` | integer | 否 | `20` | 返回条数，默认 20，最大 50 |

**返回**：搜索结果 (JSON)，包含标题、发布时间、来源、摘要、链接。需要设置环境变量 IWENCAI_API_KEY

### 8. 宏观衍生（8 个）

> 源码模块：`tradex/src/tradex/tools/macro_fx.py`

#### `get_macro_gdp`

获取中国GDP数据（季度）。

_无入参。_

**返回**：GDP数据 (JSON)，包含季度、国内生产总值、同比增长、第一/二/三产业增加值等。

#### `get_macro_cpi`

获取中国CPI消费者价格指数数据（月度）。

_无入参。_

**返回**：CPI数据 (JSON)，包含月份、全国居民消费价格指数、食品CPI、非食品CPI、同比、环比等。

#### `get_macro_pmi`

获取中国PMI采购经理指数数据（月度）。

PMI > 50 表示制造业扩张，< 50 表示收缩，是重要的经济领先指标。

_无入参。_

**返回**：PMI数据 (JSON)，包含月份、制造业PMI、非制造业PMI、新订单指数、生产指数等分项。

#### `get_macro_money_supply`

获取中国货币供应量数据（M0/M1/M2，月度）。

M2 增速是市场流动性的核心指标，直接影响股市资金面。

_无入参。_

**返回**：货币供应数据 (JSON)，包含月份、M0、M1、M2余额及同比增速等。

#### `get_fx_rate`

获取外汇汇率数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `"美元兑人民币"` | 货币对名称，可选："美元兑人民币"、"欧元兑人民币"、"英镑兑人民币"、"日元兑人民币"、"港币兑人民币" 等 |

**返回**：汇率数据 (JSON)，包含日期、买入价、卖出价、中间价等。

#### `get_bond_yield_curve`

获取中国国债收益率曲线数据。

包含不同期限（1年、3年、5年、7年、10年、30年）的国债收益率，
是无风险利率的基准，用于DCF贴现率估算。

_无入参。_

**返回**：国债收益率数据 (JSON)，包含日期、各期限收益率等。

#### `get_margin_trading`

获取融资融券数据（两融余额）。

融资余额增加表示市场加杠杆做多，是市场情绪的重要参考。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `""` | 6位股票代码，查询个股融资融券。为空则返回市场汇总。|

**返回**：融资融券数据 (JSON)，包含融资余额、融券余额、融资买入额、融资偿还额等。

#### `get_insider_trading`

获取股东/高管增减持（内部交易）数据。

大股东和高管的增减持行为是市场的重要信号。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `""` | 6位股票代码，查询个股内部交易。为空则返回全市场最新数据。|

**返回**：内部交易数据 (JSON)，包含股票代码、名称、变动人、关系、变动股数、变动金额、变动后持股数等。

### 9. A股信号 · 基础（6 个）

> 源码模块：`tradex/src/tradex/tools/signal_data_base.py`

#### `get_hot_stocks`

获取涨停股票及主题归因（同花顺 editorial 人工标注）。

返回当日涨停股票列表，含人工标注的上涨原因标签（如"算力租赁+AI政务"），
以及主题频次统计。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `date` | string | 否 | `""` | 日期 YYYY-MM-DD，空字符串默认今天。|

**返回**：涨停股票列表及主题归因 (JSON)，含股票代码/名称/涨幅/换手率/ 成交额/DDE净量/原因标签，以及主题频次 top20。

#### `get_lockup_expiry`

获取个股限售解禁日历。

包含历史解禁记录和未来待解禁安排，自动计算累计解禁占比并提示风险。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "000858"。|
| `trade_date` | string | 否 | `""` | 参考日期 YYYY-MM-DD，空字符串默认今天。|
| `forward_days` | integer | 否 | `90` | 向前查询天数，默认90天。|

**返回**：解禁日历 (JSON)，含历史记录和待解禁列表，以及风险提示。

#### `get_concept_attribution`

获取个股所属概念/行业/地域板块。

显示股票归属于哪些概念板块、行业分类和地域板块，
每个板块含当日涨跌幅。主力源：东方财富，备用源：百度股市通。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "688017"。|

**返回**：概念归属数据 (JSON)，含概念/行业/地域三个维度的板块列表。

#### `get_profit_forecast`

获取分析师一致预期EPS及Forward PE/PEG估值。

基于同花顺分析师一致预期数据，计算Forward PE、PEG、
以及PE消化年限（PEG估值框架）。

主源：同花顺 basic.10jqka 抓取；备源：腾讯实时价格/PE。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519"。|

**返回**：一致预期数据 (JSON)，含FY年份/EPS均值/预测机构数/ Forward PE/PEG/PE消化年限。

#### `get_technical_indicator`

计算个股技术指标。

支持 MACD、RSI、布林带、ATR、SMA、EMA、VWMA、MFI 等 13 种常用指标。
底层使用 stockstats 标准库计算，OHLCV 数据通过 SmartRouter 获取。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519"。|
| `indicator` | string | **是** | — | 指标名称，可选: macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma, mfi, close_50_sma, close_200_sma, close_10_ema。|
| `curr_date` | string | 否 | `""` | 参考日期 YYYY-MM-DD，空字符串默认今天。|
| `look_back_days` | integer | 否 | `30` | 回溯天数，默认30天。|

**返回**：技术指标值 (JSON)，含日期和指标值列表。

#### `list_technical_indicators`

列出所有支持的技术指标及其说明。

_无入参。_

**返回**：指标列表 (JSON)，含指标名称和中文说明。

### 10. A股信号 · 资金流（4 个）

> 源码模块：`tradex/src/tradex/tools/signal_data_flow.py`

#### `get_northbound_flow_signal`

获取北向资金流向（沪深股通）。

数据源：同花顺 hsgtApi（主源），提供实时分钟级沪股通+深股通累计净买入。
备用源：AKShare stock_hsgt_hist_em。
附带本地缓存的历史每日收盘数据（最多20个交易日）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `curr_date` | string | 否 | `""` | 日期 YYYY-MM-DD，空字符串默认今天。|
| `include_history` | boolean | 否 | `False` | 是否包含历史每日数据（最近20个交易日）。|

**返回**：北向资金数据 (JSON)，含实时数据点、收盘净流入、多空信号、历史数据。

#### `get_fund_flow_signal`

获取个股资金流向（主力/大单/中单/小单/超大单净流入）。

数据源：东财 push2（实时分钟级）+ push2his（历史日线20天）。
备用源：AKShare stock_individual_fund_flow。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519"。|
| `curr_date` | string | 否 | `""` | 日期 YYYY-MM-DD，空字符串默认今天。|
| `include_history` | boolean | 否 | `True` | 是否包含历史每日资金流（最近20个交易日）。|

**返回**：资金流向数据 (JSON)，含实时分钟级数据、历史日线、多空信号。

#### `get_dragon_tiger_signal`

获取个股龙虎榜数据（上榜记录 + 买卖席位 + 机构动向）。

数据源：东财 datacenter-web（主源，直连）。
备用源：AKShare stock_lhb_detail_em。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "000858"。|
| `trade_date` | string | 否 | `""` | 参考日期 YYYY-MM-DD，空字符串默认今天。|
| `look_back_days` | integer | 否 | `30` | 向前查询天数，默认30天。|

**返回**：龙虎榜数据 (JSON)，含上榜记录、买卖席位TOP5、机构动向。

#### `get_industry_comparison_signal`

获取行业横向对比排名（全行业涨跌幅/上涨下跌家数/领涨股）。

数据源：东财 push2 行业板块排名（主源，直连）。
备用源：AKShare stock_sector_fund_flow_rank。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | 否 | `""` | 6位股票代码（可选，用于定位所属行业）。|
| `trade_date` | string | 否 | `""` | 日期 YYYY-MM-DD，空字符串默认今天。|
| `top_n` | integer | 否 | `20` | 显示前/后N个行业，默认20。|

**返回**：行业排名数据 (JSON)，含行业名称/涨跌幅/上涨下跌家数/领涨股。

### 11. A股信号 · ETF（2 个）

> 源码模块：`tradex/src/tradex/tools/signal_data_etf.py`

#### `get_etf_realtime_data`

获取ETF实时行情（全部ETF按成交额/涨跌幅排序）。

数据源：AKShare fund_etf_spot_em（东方财富）。
提供 IOPV估值、折价率、换手率等ETF特有字段。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `top_n` | integer | 否 | `50` | 返回前N只ETF，默认50。|
| `sort_by` | string | 否 | `"成交额"` | 排序字段，默认成交额，可选涨跌幅。|

**返回**：ETF实时行情 (JSON)，含代码/名称/价格/涨跌幅/IOPV/成交额。

#### `get_etf_kline_data`

获取ETF历史K线数据。

数据源：AKShare fund_etf_hist_em（东方财富）。
支持日/周/月线，可选前复权/后复权/不复权。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | ETF代码，如 "513500"。|
| `period` | string | 否 | `"daily"` | K线周期 daily/weekly/monthly，默认daily。|
| `start_date` | string | 否 | `""` | 开始日期 YYYYMMDD，默认近1年。|
| `end_date` | string | 否 | `""` | 结束日期 YYYYMMDD，默认今天。|
| `adjust` | string | 否 | `""` | 复权方式（''不复权/'qfq'前复权/'hfq'后复权）。|

**返回**：ETF K线数据 (JSON)，含日期/开高低收/成交量/成交额/涨跌幅。

### 12. A股信号 · 可转债（2 个）

> 源码模块：`tradex/src/tradex/tools/signal_data_cb.py`

#### `get_cb_realtime_data`

获取可转债实时行情（全部可转债含转股溢价率）。

数据源：AKShare bond_zh_cov（东方财富）。
提供正股价/转股价/转股价值/转股溢价率/信用评级等可转债特有字段。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `top_n` | integer | 否 | `50` | 返回前N只可转债，默认50。|
| `sort_by` | string | 否 | `"成交额"` | 排序字段，默认成交额，可选转股溢价率。|

**返回**：可转债实时行情 (JSON)，含代码/价格/正股价/转股价/溢价率/评级。

#### `get_cb_value_analysis_data`

获取可转债价值分析（历史转股溢价率/纯债价值/纯债溢价率曲线）。

数据源：AKShare bond_zh_cov_value_analysis（东方财富）。
用于分析可转债估值区间、溢价率趋势。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 可转债代码，如 "113527"。|
| `days` | integer | 否 | `30` | 返回最近N天数据，默认30天。|

**返回**：可转债价值分析 (JSON)，含日期/收盘价/纯债价值/转股价值/溢价率。

### 13. A股信号 · 涨停板（3 个）

> 源码模块：`tradex/src/tradex/tools/signal_data_board.py`

#### `get_limit_up_board`

获取涨停板数据（东财 push2ex）。

支持四种类型：涨停池/炸板池/跌停池/昨日涨停池。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `board_type` | string | 否 | `"zt"` | 板类型，可选 zt(涨停)/zb(炸板)/dt(跌停)/prev_zt(昨日涨停)。|

**返回**：板数据 (JSON)，含股票列表及对应字段。

#### `get_board_sentiment`

获取打板情绪数据（炸板率/连板梯队/晋级率）。

综合涨停四池数据，计算市场打板情绪指标：炸板率、连板梯队、
昨日涨停晋级率、平均溢价、热门题材等。

_无入参。_

**返回**：打板情绪数据 (JSON)，含涨停数/炸板数/跌停数/炸板率/ 连板梯队/晋级率/平均溢价/热门题材。

#### `get_limit_up_insight`

获取涨停揭秘数据（同花顺）。

包含涨停原因题材、封板成功率、板型（一字/换手/T字）、封单额等。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | 否 | `""` | 股票代码（可选，不传则返回当日全部涨停揭秘）。|

**返回**：涨停揭秘数据 (JSON)，含题材原因/封板成功率/板型/封单额/换手率。

### 14. eltdx 通达信协议（31 个）

> 源码模块：`tradex/src/tradex/tools/eltdx_data.py`

#### `eltdx_get_auction`

获取股票集合竞价数据（eltdx 独有，AKShare 无此功能）。

集合竞价发生在开盘前 9:15-9:25，用于确定开盘价。
返回每 3 秒一个价格点的撮合量、未匹配量。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001"（平安银行）、"600519"（贵州茅台）|

#### `eltdx_get_ticks`

获取股票逐笔成交数据（eltdx 独有，AKShare 无此功能）。

包含每笔成交的时间、价格、数量、买卖方向。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001" |
| `trading_date` | string | **是** | — | 交易日期，格式 "20260617" 或 "2026-06-17" |
| `count` | integer | 否 | `2000` | 返回笔数（默认 2000）|

#### `eltdx_get_f10`

获取股票 F10 资料（eltdx 独有，AKShare 无此功能）。

包含公司概况、热点题材、财务诊断评分。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001"（6 位）|

#### `eltdx_get_minutes`

获取股票当日分时数据（SmartRouter 路由，优先 eltdx 数据源）。

1 分钟一根 K 线的价量数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001" |

#### `eltdx_get_kline`

获取股票 K 线数据（SmartRouter 路由，优先 eltdx 数据源）。

支持日/周/月/分钟等多种周期。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001" |
| `period` | string | 否 | `"day"` | 周期，"day" / "week" / "month" / "5m" / "15m" / "30m" / "60m" |
| `count` | integer | 否 | `100` | 返回 K 线根数（默认 100）|

#### `eltdx_get_depth`

获取股票五档盘口快照（买1-5 / 卖1-5，eltdx 常驻连接）。

通过 eltdx refresh_stream(0x0547) 拿实时五档盘口，含最新价、
涨跌、内外盘、买卖五档价量。免费行情站不主动推送，本接口为
一次性快照（调用即拉取最新）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "000001" 或 "sh600170" |

#### `eltdx_stream_health`

查询 eltdx 常驻连接管理器健康状态。

返回连接是否启动、已订阅代码数、推送缓冲快照（帧数/字节数/丢弃数）。
用于诊断交易看板推送链路的健康情况。

_无入参。_

#### `eltdx_get_security_codes`

获取全市场证券代码表（eltdx 精确分类，v3.3.7 新增）。

返回权威证券代码清单，含代码、名称、类别（a_share/etf/index/bond 等）、
板块（主板/创业板/科创板）。相比行情快照更权威：含停牌股、精确分类。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `market` | string | 否 | `"all"` | 市场，'sh' / 'sz' / 'bj' / 'all'（默认 all，沪深京三市场）|
| `category` | string | 否 | `""` | 类别过滤，如 'a_share'（A股）、'etf'、'index'，空则不过滤 |

#### `eltdx_get_minute_history`

获取股票历史分时数据（eltdx 独有，v3.3.7 新增）。

返回指定交易日的历史分时（约 240 点/日），用于盘后复盘。
这是 eltdx 独有能力（历史分时 AKShare 需付费）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `trading_date` | string | **是** | — | 交易日，格式 "20260813" 或 "2026-08-13" |

#### `eltdx_get_buy_sell_strength`

获取股票日内买卖强度数据（eltdx 独有，v3.3.7 新增）。

返回日内逐分钟的买卖盘力量对比（买盘/卖盘委比量），
用于 T0 盘口强弱判断。这是 eltdx 独有能力。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_today_ticks`

获取股票当日逐笔成交明细（eltdx 独有，v3.3.7 新增）。

返回当日逐笔成交（时间/价格/成交量/成交额/方向/是否开盘撮合）。
与历史逐笔互补，用于盘中实时逐笔监控。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `count` | integer | 否 | `1000` | 返回条数，默认 1000，上限 1800 |

#### `eltdx_get_opening_match`

获取股票当日 9:25 开盘撮合（eltdx 独有，v3.3.7 新增）。

返回开盘集合竞价形成的正式撮合那一笔（开盘价/开盘量/开盘额），
是 T0 竞价策略判断开盘强弱的核心数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_full_kline`

获取股票全量历史 K 线（eltdx，v3.3.7 新增）。

自动分页拉取全量 K 线（如日线约 6000+ 根），适合回测。
与 eltdx_get_kline（单页）互补。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `period` | string | 否 | `"day"` | 周期，"day" / "week" / "month" |

#### `eltdx_get_adjusted_kline`

获取股票复权 K 线（eltdx，v3.3.7 新增）。

返回前复权(qfq)/后复权(hfq) K 线，复权因子由 eltdx 本地计算。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `period` | string | 否 | `"day"` | 周期，"day" / "week" / "month" |
| `adjust` | string | 否 | `"qfq"` | 复权方式，"qfq"（前复权，默认）/ "hfq"（后复权）|
| `count` | integer | 否 | `800` | 返回根数，默认 800 |

#### `eltdx_get_stock_profile`

获取股票全景档案（eltdx 独有，v3.3.7 新增）。

返回「行情 + 证券信息 + 财务」一张表：最新价、涨跌幅、换手率、
总市值、流通市值、总股本、EPS、板块等。量化选股核心工具。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `codes` | string | **是** | — | 股票代码，支持逗号分隔多只，如 "600170,000001" |

#### `eltdx_get_shortline_indicators`

获取股票短线打板指标（eltdx 独有，v3.3.7 新增）。

返回 21 项短线指标：涨停状态、连板天数、涨停次数、封单流通比、
市盈率TTM、60日贝塔、自由流通市值等。短线打板分析核心。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `codes` | string | **是** | — | 股票代码，支持逗号分隔多只，如 "600170,000001" |

#### `eltdx_get_finance_batch`

获取股票批量财务字段（eltdx 独有，v3.3.7 新增）。

一次拉取多只股票的财务字段：总股本、流通股本、总资产、净利润、
每股收益、行业、上市日期等。量化选股估值利器。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `codes` | string | **是** | — | 股票代码，支持逗号分隔多只，如 "600170,000001" |

#### `eltdx_get_special_limits`

获取特殊品种涨跌停参考价（eltdx 独有，v3.3.7 新增）。

返回特殊品种（ST/新股/复牌等）的涨跌停参考价。
用于判断特殊品种的涨跌幅限制。

_无入参。_

#### `eltdx_get_finance_report`

获取通达信财务报表（eltdx F10，v3.3.7 新增）。

返回通达信原始财务报表，report_type：zcfzb=资产负债表 / lrb=利润表 /
xjllb=现金流量表。字段为通达信内部编码（T007/T008 等），
作为 akshare 中文财报的降级补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `report_type` | string | 否 | `"zcfzb"` | 报表类型，默认 zcfzb（资产负债表）|

#### `eltdx_get_dividend_financing`

获取股票分红融资历史（eltdx F10，v3.3.7 新增）。

返回分红方案历史。字段为通达信内部编码，作为 akshare 分红的降级补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_company_news`

获取公司资讯/研报（eltdx F10，v3.3.7 新增）。

返回公司研报/监管措施资讯。字段为通达信内部编码，作为 akshare 资讯的降级补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_northbound_holding`

获取沪深股通持股变化（eltdx F10，v3.3.7 新增）。

返回北向资金持股变化。字段为通达信内部编码，作为 akshare 北向的降级补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_stock_topics`

获取个股关联全部题材（eltdx helpers，v3.3.7 新增）。

返回个股关联的题材列表（题材名/关联度/入选理由），题材挖掘核心。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_topic_stocks`

获取题材成分股排名（eltdx helpers，v3.3.7 新增）。

返回某题材下的成分股（排名/涨跌幅/3日/5日/20日/60日涨跌幅）。
topic_name 为空时用种子股首个题材。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 种子股票代码，如 "600170" |
| `topic_name` | string | 否 | `""` | 题材名，如 "节能环保"，空则用首个题材 |

#### `eltdx_get_auction_data`

获取股票竞价汇总（eltdx helpers，v3.3.7 新增）。

返回开盘集合竞价汇总：开盘价/开盘量/开盘额/开盘涨跌幅/昨收。
聚合竞价序列 + 9:25 快照 + 行情。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_category_quotes`

获取分类行情列表（eltdx，v3.3.7 新增，B 级）。

返回分类行情（A股涨幅榜/成交额榜/封单榜等），实时性强于 akshare。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `category` | string | 否 | `"沪深a股"` | 分类，"沪深a股" / "a股" |
| `sort_by` | string | 否 | `"涨幅"` | 排序字段，"涨幅" / "成交额" / "现价" / "封单额" 等 |
| `count` | integer | 否 | `80` | 返回条数，默认 80 |

#### `eltdx_get_trading_day`

获取当前交易日（eltdx，v3.3.7 新增，B 级）。

通过服务器握手返回当前交易日日期，用于判断是否交易日。

_无入参。_

#### `eltdx_get_opening_match_history`

获取历史开盘撮合（eltdx，v3.3.7 新增，B 级）。

返回历史某日 9:25 开盘撮合（开盘价/量/额），盘后复盘用。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |
| `trading_date` | string | **是** | — | 交易日，格式 "20260813" 或 "2026-08-13" |

#### `eltdx_get_capital_changes`

获取股本变动历史（eltdx，v3.3.7 新增，B 级）。

返回股本变动（分红/送股/增发等）历史，复权计算基础。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | **是** | — | 股票代码，如 "600170" |

#### `eltdx_get_special_limits_scan`

扫描全市场特殊涨跌停参考价（eltdx，v3.3.7 新增，B 级）。

扫描全部特殊品种（ST/新股/复牌）的涨跌停参考价。

_无入参。_

#### `eltdx_get_f10_extra`

获取 F10 额外资料（eltdx 通用入口，v3.3.7 新增，B 级）。

覆盖 F10 B 级接口，字段为通达信内部编码（T007 等），
作为 akshare 中文源的降级补充源。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `entry` | string | **是** | — | 接口名，可选：valuation(估值)/theme_market(题材行情)/ stock_score(个股总评)/profit_forecast(盈利预测)/ ranking_detail(排名)/governance(治理)/ shareholder_change_plans(增减持)/business_composition(主营构成)/ announcements(公告)/news(新闻)/stock_info(基础信息) |
| `code` | string | **是** | — | 股票代码，如 "600170" |

### 15. 技术指标计算（L2）（6 个）

> 源码模块：`tradex/src/tradex/tools/technical_indicators.py`

#### `calculate_ma_ema`

计算 MA(简单移动平均) / EMA(指数移动平均) 均线。

与 Excel/通达信计算结果一致。MA 前 period-1 个数据点返回 null；
EMA 按通达信口径自首根（X[0] 种子）起算，全数组有效。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `closes` | list[float] | **是** | — | 收盘价数组，如 [10.5, 10.8, 11.2, ...] |
| `period` | integer | 否 | `20` | 计算周期，默认20 |
| `type` | string | 否 | `"both"` | 计算类型 - "sma" \| "ema" \| "both"(默认，同时返回SMA和EMA) |

**返回**：均线计算结果 (JSON)，包含字段：

- ma: SMA 数组（type 为 sma 或 both 时返回）
- ema: EMA 数组（type 为 ema 或 both 时返回）
- period: 周期
- data_points: 输入数据点数
- valid_points: 有效计算点数（非 null）

#### `calculate_macd`

计算 MACD 指标（DIF、DEA、MACD柱）。

算法（通达信口径，EMA 首值 X[0] 种子）：
- DIF = EMA(close, fast) - EMA(close, slow)
- DEA = EMA(DIF, signal)
- MACD柱 = 2 * (DIF - DEA)

与通达信 MACD 计算结果一致，DIF/DEA/MACD 自首根起算、三数组等长。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `fast_period` | integer | 否 | `12` | 快线周期，默认12 |
| `slow_period` | integer | 否 | `26` | 慢线周期，默认26 |
| `signal_period` | integer | 否 | `9` | 信号线周期，默认9 |

**返回**：MACD 计算结果 (JSON)，包含字段：

- dif: DIF 线数组
- dea: DEA 信号线数组
- macd: MACD 柱状图数组
- fast_period / slow_period / signal_period: 周期参数

#### `calculate_kdj`

计算 KDJ 随机指标（K、D、J 值）。

算法：
- RSV = (close - lowest_low) / (highest_high - lowest_low) * 100
- 初始 K = 50, 初始 D = 50
- K = (2/3) * 前K + (1/3) * RSV
- D = (2/3) * 前D + (1/3) * K
- J = 3 * K - 2 * D

与通达信 KDJ 计算结果一致。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `highs` | list[float] | **是** | — | 最高价数组 |
| `lows` | list[float] | **是** | — | 最低价数组 |
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `period` | integer | 否 | `9` | RSV 周期，默认9 |
| `k_period` | integer | 否 | `3` | K 平滑周期，默认3 |
| `d_period` | integer | 否 | `3` | D 平滑周期，默认3 |

**返回**：KDJ 计算结果 (JSON)，包含字段：

- k: K 值数组
- d: D 值数组
- j: J 值数组
- period / k_period / d_period: 周期参数

#### `calculate_rsi`

计算 RSI 相对强弱指数（Wilder 平滑法）。

算法：
- 计算每日涨跌幅
- 分离上涨幅度和下跌幅度
- Wilder 平滑平均：avg = (prev_avg * (period-1) + current) / period
- RSI = 100 - 100 / (1 + avg_gain / avg_loss)

与通达信 RSI 计算结果一致。前 period 个为 null。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `period` | integer | 否 | `14` | 计算周期，默认14 |

**返回**：RSI 计算结果 (JSON)，包含字段：

- rsi: RSI 值数组（前 period 个为 null）
- period: 周期
- data_points: 数据点数

#### `calculate_boll`

计算布林带（BOLL）上轨、中轨、下轨。

算法：
- 中轨 = SMA(close, period)
- 上轨 = 中轨 + k * 标准差
- 下轨 = 中轨 - k * 标准差
- 带宽 = (上轨 - 下轨) / 中轨
- %B = (收盘价 - 下轨) / (上轨 - 下轨)

与通达信 BOLL 计算结果一致。前 period-1 个为 null。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `period` | integer | 否 | `20` | 计算周期，默认20 |
| `k` | number | 否 | `2.0` | 标准差倍数，默认2.0 |

**返回**：BOLL 计算结果 (JSON)，包含字段：

- upper: 上轨数组
- middle: 中轨数组
- lower: 下轨数组
- bandwidth: 带宽数组
- percent_b: %B 数组
- period / k: 参数

#### `calculate_atr`

计算 ATR 平均真实波幅。

算法：
- TR = max(high-low, |high-pre_close|, |low-pre_close|)
- ATR = EMA(TR, period)

与通达信 ATR 计算结果一致。首根 TR 为 null（无前收盘），
ATR 自第二根起有效（EMA 首值 = 首根 TR，通达信口径）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `highs` | list[float] | **是** | — | 最高价数组 |
| `lows` | list[float] | **是** | — | 最低价数组 |
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `period` | integer | 否 | `14` | 计算周期，默认14 |

**返回**：ATR 计算结果 (JSON)，包含字段：

- atr: ATR 值数组
- tr: TR 真实波幅数组（首个为 null）
- period: 周期
- data_points: 数据点数

### 16. 绩效指标计算（L2）（2 个）

> 源码模块：`tradex/src/tradex/tools/performance_metrics.py`

#### `calculate_performance`

计算完整的策略绩效指标。

输入权益曲线和交易记录，输出涵盖收益、风险、风险调整收益、
交易质量、费用统计的完整绩效报告。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `equity_curve` | list[float] | **是** | — | 权益曲线数组（日频），如 [1000000, 1005000, 1010000, ...] |
| `trades` | list[dict] | None | 否 | `None` | 交易记录数组，每条记录可含 profit/commission/stamp_tax/slippage 字段 |
| `benchmark_curve` | list[float] | None | 否 | `None` | 基准权益曲线（可选），用于计算超额收益 |
| `risk_free_rate` | number | 否 | `0.03` | 无风险利率，默认0.03（3%）|
| `trading_days` | integer | 否 | `252` | 年交易日数，默认252 |

**返回**：完整绩效报告 (JSON)，包含，包含字段：

- return_metrics: 收益类指标
- risk_metrics: 风险类指标
- risk_adjusted_metrics: 风险调整收益指标
- trade_quality_metrics: 交易质量指标
- cost_metrics: 费用统计指标
- benchmark_metrics: 基准对比指标（如有基准）

#### `list_performance_metrics`

列出所有支持的绩效指标清单。

返回按类别分组的绩效指标清单，每个指标包含名称、描述和计算公式。
可用于让 AI Agent 了解 calculate_performance 工具的输出字段含义。

_无入参。_

**返回**：绩效指标清单 (JSON)，包含字段：

- categories: 指标类别列表
- return: 收益类
- risk: 风险类
- risk_adjusted: 风险调整收益类
- trade_quality: 交易质量类
- cost: 费用统计类
- metrics: 所有指标详情列表
- total_count: 指标总数

### 17. 信号生成（L3）（3 个）

> 源码模块：`tradex/src/tradex/tools/signal_generation.py`

#### `generate_trading_signal`

基于多指标组合生成单票交易信号。

综合 MA/MACD/KDJ/RSI/BOLL/量比 等多维度评分，
生成 strongly_buy/buy/neutral/sell/strong_sell 五级信号。

评分维度：
- 趋势(30%): MA5/MA20 金叉死叉
- 动量(25%): MACD 金叉死叉、零轴位置
- 超买超卖(20%): KDJ J值、RSI
- 量能(15%): 量比变化
- 风险(10%): 布林带位置

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `highs` | list[float] | **是** | — | 最高价数组（至少30根K线）|
| `lows` | list[float] | **是** | — | 最低价数组 |
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `volumes` | list[float] | None | 否 | `None` | 成交量数组（可选）|

**返回**：交易信号 (JSON)，包含字段：

- signal: 信号级别 strong_buy/buy/neutral/sell/strong_sell
- score: 综合评分 0-100
- reasons: 触发原因列表
- indicators: 最新技术指标值

#### `scan_stocks_for_signals`

批量扫描多只股票的交易信号。

输入多只股票的OHLCV数据，批量生成信号并按评分排序。
适用于全市场选股、自选股扫描等场景。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `stocks_data` | dict[str, dict[str, list[float]]] | **是** | — | 股票数据字典 { "000001": { "highs": [...], "lows": [...], "closes": [...], "volumes": [...] }, "600519": {...} } |
| `min_score` | number | 否 | `0` | 最低评分阈值，默认0（不筛选）|
| `signal_filter` | string | 否 | `""` | 信号过滤，逗号分隔，如 "strong_buy,buy" |

**返回**：扫描结果 (JSON)，包含字段：

- results: 股票信号列表（按评分降序）
- summary: 统计摘要（各信号级别数量）
- total_scanned: 扫描总数
- total_returned: 返回数量

#### `validate_signal_quality`

验证信号质量（前瞻收益分析）。

在指定位置的信号生成后，统计未来N日的实际收益，
用于评估信号的有效性。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `closes` | list[float] | **是** | — | 收盘价数组 |
| `signal_idx` | integer | **是** | — | 信号产生的索引位置（0-based）|
| `forward_days` | integer | 否 | `5` | 前瞻天数，默认5 |

**返回**：信号质量验证 (JSON)，包含字段：

- forward_returns: 未来1/3/5/10/20日收益
- max_gain: 期间最大涨幅
- max_loss: 期间最大跌幅
- win_rate: 上涨概率（基于日收益）
- signal_price: 信号位置价格
- end_price: 前瞻期末价格

### 18. 因子分析（L3）（2 个）

> 源码模块：`tradex/src/tradex/tools/factor_analysis.py`

#### `calculate_factor_score`

多因子综合评分。

输入多只股票的多个因子值，标准化处理后按权重加权得到综合评分。
支持5类20+因子，可自定义权重。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `stock_codes` | list[str] | **是** | — | 股票代码数组，如 ["000001", "600519", ...] |
| `factor_data` | dict[str, list[float]] | **是** | — | 因子数据字典 { "pe": [10.5, 30.2, 15.8, ...], # 与stock_codes一一对应 "pb": [1.2, 8.5, 2.3, ...], "revenue_growth": [0.15, 0.20, 0.08, ...] } |
| `weights` | dict[str, float] | None | 否 | `None` | 因子权重（可选），如 {"pe": 0.3, "pb": 0.2, "revenue_growth": 0.5} 未指定时所有因子等权 |

**返回**：多因子评分结果 (JSON)，包含字段：

- scores: 综合评分列表（按分数降序）[{code, total_score, factor_scores: {...}}, ...]
- summary: 统计摘要
- weights_used: 实际使用的权重

#### `get_factor_catalog`

获取因子库清单。

返回所有支持的因子列表，包含因子名称、所属类别、方向（正向/负向/中性）、单位。
可用于让 AI Agent 了解 calculate_factor_score 工具支持的因子。

_无入参。_

**返回**：因子库清单 (JSON)，包含字段：

- categories: 因子类别
- factors: 因子详情列表
- total_count: 因子总数

### 19. 条件选股（L3）（2 个）

> 源码模块：`tradex/src/tradex/tools/stock_screening.py`

#### `screen_stocks`

条件选股扫描。

输入股票数据和多维度条件，输出符合条件的股票列表。
支持基本面/技术面/资金面/风险面/标记 五大类条件组合筛选。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `stocks_data` | list[dict[str, Any]] | **是** | — | 股票数据数组，每个元素是一个股票的字典 [{"code": "000001", "name": "平安银行", "pe": 8.5, "pb": 0.6, "price": 12.3, ...}, ...] |
| `conditions` | list[dict[str, Any]] | **是** | — | 筛选条件数组（AND 逻辑）[ {"field": "pe", "operator": "<", "value": 20}, {"field": "pb", "operator": "<", "value": 2}, {"field": "roe", "operator": ">", "value": 15}, {"field": "is_st", "operator": "==", "value": false}, {"field": "board", "operator": "in", "value": ["main_sh", "main_sz"]} ] |
| `sort_by` | string | 否 | `""` | 排序字段（可选），如 "pe" 或 "total_score" |
| `sort_order` | string | 否 | `"desc"` | 排序方向 "asc" 或 "desc"，默认 desc |
| `limit` | integer | 否 | `50` | 返回数量上限，默认50 |

**返回**：选股结果 (JSON)，包含字段：

- results: 符合条件的股票列表
- summary: 统计摘要
- conditions_applied: 实际应用的条件

#### `get_screening_conditions`

获取支持的选股条件清单。

返回所有可选的筛选字段，包含字段名、所属类别、类型、单位和支持的运算符。
可用于让 AI Agent 了解 screen_stocks 工具支持的筛选条件。

_无入参。_

**返回**：选股条件清单 (JSON)，包含字段：

- categories: 条件类别
- conditions: 条件详情列表
- total_count: 条件总数
- examples: 使用示例

### 20. 综合分析（L3）（3 个）

> 源码模块：`tradex/src/tradex/tools/composite_analysis.py`

#### `analyze_stock_comprehensive`

个股综合分析 — 一次调用获取完整分析视图。

组合以下维度数据：
- 实时行情（价格、涨跌、成交量、市值、换手率）
- 公司信息（行业、上市日期、总股本等）
- 财务指标（ROE、净利率、毛利率、资产负债率，最近4期）
- 板块资金流向（当日行业资金流入/流出 Top5）

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位A股股票代码，如 "600519"（贵州茅台）|

**返回**：JSON 格式的综合分析结果，包含各维度数据和成功/失败状态。

#### `analyze_industry_comparison`

行业对比分析 — 个股指标 vs 同行业均值。

获取个股的行业分类，然后获取同行业所有股票的关键指标，
计算行业平均值，对比个股在行业中的排名。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位A股股票代码，如 "600519"（贵州茅台）|

**返回**：JSON 格式的行业对比分析结果。

#### `analyze_market_overview`

市场全景分析 — 大盘指数 + 板块资金 + 涨跌停统计。

一次获取市场全景数据：
- 三大指数（上证、深证、创业板）实时行情
- 行业板块资金流向 Top5
- 涨停/跌停数量统计

_无入参。_

**返回**：JSON 格式的市场全景分析结果。

### 21. 技术分析引擎（L3）（1 个）

> 源码模块：`tradex/src/tradex/tools/analysis_engine.py`

#### `analyze_technical`

5 维度技术分析引擎（均线/趋势/量价/筹码/形态）。

输入股票代码，自动获取 OHLCV 数据，输出 5 个维度的综合技术分析结果：
1. 均线系统 — MA5/10/20/60、多空排列、金叉/死叉
2. 趋势分析 — 上升/下降/震荡判断、趋势强度评分(0-100)
3. 量价关系 — 量价同步/背离、异常放量检测
4. 筹码分布 — 获利盘比例、筹码集中度、支撑压力位
5. 形态识别 — 双底/双顶、头肩顶/底等常见形态

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6 位股票代码，如 "600519"。|
| `look_back_days` | integer | 否 | `30` | 回溯交易日数，默认 30。建议 >= 60 以支持 MA60 分析。|

**返回**：5 维度技术分析结果 (JSON)，含数据概况、各维度详情及汇总信号。

### 22. 系统诊断（5 个）

> 源码模块：`tradex/src/tradex/tools/diagnostics.py`

#### `get_data_source_health`

返回各数据源的健康状态。

通过 SmartRouter 获取每个数据源的健康评分、成功率、平均延迟、
连续失败次数、最近失败时间与是否健康标记。SmartRouter 不可用时返回基础信息。

_无入参。_

**返回**：数据源健康状态 (JSON)，包含 status、count 与 data_sources 列表，每个数据源含 name、score、success_rate、avg_latency_ms、consecutive_fails、last_fail_time、is_healthy。

#### `list_all_tools`

返回所有已注册工具的清单。

从 FastMCP 实例读取所有通过 register(mcp) 注册的工具，返回工具名列表与总数。
v3.1.4 起：改为从 mcp 实例获取（替代原装饰器注册机制，避免返回空清单）。

_无入参。_

**返回**：工具清单 (JSON)，包含 status、total、tools（工具名列表）。

#### `get_cache_stats`

返回两级缓存（内存 + 文件）的统计信息。

_无入参。_

**返回**：缓存统计 (JSON)，包含 size、max_size、hits、misses、hit_rate、file_size、file_enabled。

#### `health_check`

系统健康检查。

组合数据源健康、工具清单、缓存统计三项，并增加 akshare 连通性测试，
返回整体状态（healthy / degraded / unhealthy）：
  - unhealthy: akshare 不可用（核心数据源缺失）
  - degraded:  akshare 可用但存在其他异常（SmartRouter 不可用、
               缓存/工具获取失败或存在不健康数据源）
  - healthy:   全部正常

_无入参。_

**返回**：系统健康检查结果 (JSON)，包含 status、issues、akshare、data_sources、tools、cache。

#### `get_data_source_dashboard`

获取数据源看板数据（JSON）。

返回数据源健康/版本/统计信息，包含：
- 数据源列表（25 类型 34 源）
- 每个源的健康评分/延迟/成功率/独占标记
- eltdx/akshare 版本检查结果
- 系统概览（总工具数、注册数据类型数、健康源比例）

_无入参。_

**返回**：看板数据 (JSON)，包含 timestamp、summary、sources、health、versions。summary 含 total_tools / data_types / sources / healthy_sources / unhealthy_sources；sources 为 SmartRouter 注册表；health 为健康报告；versions 为 eltdx/akshare 版本检查结果列表。

### 23. 本地数据（2 个）

> 源码模块：`tradex/src/tradex/tools/local_data.py`

#### `get_local_kline`

获取通达信本地日线数据（离线，不封 IP）。**仅用于回测和历史分析，非盘中实时。**

⚠️ 数据新鲜度：本地日线最新到「上一个交易日收盘」，盘中（9:30-15:00）不含当天
未完成的 K 线，当天数据需等收盘后通达信「盘后数据下载」才写入本地。
盘中需要实时行情请改用 get_realtime_quote / get_intraday_data。

读取本地通达信 vipdoc 目录的日线二进制文件，非网络请求，
在网络全断或东财被封时仍可用（作为历史数据兜底）。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519"（贵州茅台）、"000001"（平安银行）|
| `count` | integer | 否 | `250` | 返回最近多少条，默认 250 |

**返回**：本地日线数据 (JSON)，含 date/open/high/low/close/volume/amount。

#### `get_local_minute`

获取通达信本地分钟线数据（离线，不封 IP）。**仅用于回测和历史分析，非盘中实时。**

⚠️ 数据新鲜度：分钟线最新到「通达信最后一次下载的时间」，盘中不实时追加；
且通达信默认只下载日线，分钟线需手动下载，否则本地无数据。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `symbol` | string | **是** | — | 6位股票代码，如 "600519" |
| `period` | integer | 否 | `5` | 周期，5=5分钟线（默认），1=1分钟线 |
| `count` | integer | 否 | `200` | 返回最近多少条，默认 200 |

**返回**：本地分钟线数据 (JSON)，含 datetime/open/high/low/close/amount/volume。


---

## 七、环境变量参考

配置文件：复制 `tradex/.env.example` 为 `.env` 并按需修改（**不配置也可正常运行，全部有默认值**）。

### 7.1 数据源

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AKSHARE_TIMEOUT` | `30` | AKShare 请求超时（秒）|
| `TENCENT_TIMEOUT` | `10` | 腾讯行情接口超时（秒）|
| `ELTDX_SERVER` | `auto` | 通达信服务器地址，`auto` 自动选择最优 |

### 7.2 东财防封客户端（anti_ban_client）

> v3.3.19 起东财/腾讯请求统一收敛到 `astock_signals/antiban_http.py`
> （TLS 指纹伪装 + 串行限流 + 指数退避重试 + 限流换指纹）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EM_RATE_LIMIT_INTERVAL` | `1.0` | 请求最小间隔（秒）|
| `EM_JITTER_MIN` | `0.1` | 随机抖动下限（秒）|
| `EM_JITTER_MAX` | `0.5` | 随机抖动上限（秒）|
| `EM_MAX_RETRY` | `3` | 限流/网络异常最大重试次数（指数退避 1s、2s、4s）|

### 7.2a 东财主机镜像降级

> v3.3.21 起 `em_get` 在主源主机不可用时自动切镜像主机。
> **东财节点故障是按主机（甚至按路由）发生的** —— 实测 2026-09-20
> `push2.eastmoney.com` 的 `/api/qt/clist/get` 路由 `Connection closed abruptly`
> （连命令行 curl 都是 HTTP 000），而同主机的 `fflow/kline` 正常、
> 镜像 `push2delay` 全路由正常。此前无降级，6 处调用点全线失效。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EM_HOST_MIRROR` | `1` | 是否启用镜像降级（`0`/`false` 关闭）|
| `EM_PUSH2_MIRROR` | `push2delay.eastmoney.com` | push2 的镜像主机（数据延迟几分钟，作降级可接受）|
| `EM_MIRROR_COOLDOWN` | `300` | 镜像成功后的静默期（秒），期间直接走镜像不再白撞主源 |
| `EM_DEAD_HOST_COOLDOWN` | `60` | **失败熔断冷却（秒）**：某 `(主机,路径)` 主机级失败后期间立即返回 `None`，不再白跑满重试；设 `0` 关闭 |

**切换边界**（重要）：只在**连接层失败**或 **5xx** 时切换。
`403` / `429` / `rc=102` 是**按 IP** 的风控，换主机无效（实测 `slist` 在
`push2` 与 `push2delay` 上都返回 `rc=102`），硬切只会让风控更糟。

**失败熔断**（v3.3.23）：熔断按 **(主机, 路径)** 而非仅主机 ——
实测 `push2his` 是「`kline/get` 挂了、`fflow/daykline/get` 还活着」，
按主机熔断会误伤同主机上健康的路径。实测效果：`index_daily_amount`
从 **6.18s → 0.18s**（快 34 倍）。

诊断：`anti_ban_client.get_mirror_status()` / `get_dead_host_status()` 返回剩余静默秒数；
`reset_host_health()` 可一次性清空两者（上游恢复后立即重探）。

### 7.2b 腾讯防封客户端（qt.gtimg.cn）

> 腾讯风控远比东财宽松，但批量拉全市场会产生数十个连续请求，仍需节流。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TENCENT_RATE_LIMIT_INTERVAL` | `0.15` | 请求最小间隔（秒）|
| `TENCENT_JITTER_MIN` | `0.05` | 随机抖动下限（秒）|
| `TENCENT_JITTER_MAX` | `0.15` | 随机抖动上限（秒）|
| `TENCENT_MAX_RETRY` | `2` | 最大重试次数（腾讯是兜底源，重试过多会拉长 worst case）|

### 7.2c 第二梯队防封客户端（同花顺 / 财联社 / 巨潮 / 新浪 / 问财）

> v3.3.20 起这 5 个源统一收敛到 `get_client("ths"|"cls"|"cninfo"|"sina"|"wencai")`。
> 各源**独立节流器**，互不影响 —— 低风险源不会被东财的 1.0s 间隔拖慢。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TIER2_IMPERSONATE` | `chrome120` | 第二梯队指纹伪装版本 |
| `THS_RATE_LIMIT_INTERVAL` | `0.25` | 同花顺（`10jqka.com.cn` / `data.hexin.cn`）请求最小间隔（秒）|
| `THS_MAX_RETRY` | `2` | 同花顺最大重试次数 |
| `CLS_RATE_LIMIT_INTERVAL` | `0.30` | 财联社 `cls.cn` 请求最小间隔（秒）|
| `CNINFO_RATE_LIMIT_INTERVAL` | `0.30` | 巨潮 `cninfo.com.cn` 请求最小间隔（秒）|
| `SINA_RATE_LIMIT_INTERVAL` | `0.25` | 新浪财经 `feed.mix.sina.com.cn` 请求最小间隔（秒）|
| `WENCAI_RATE_LIMIT_INTERVAL` | `0.50` | 问财 OpenAPI 请求最小间隔（秒）|
| `WENCAI_MAX_RETRY` | `1` | 问财最大重试次数（付费接口，失败多为配额/鉴权问题，重试无意义）|

> **注意（巨潮）**：`cninfo.com.cn` 的 WAF 恰好**封禁常见浏览器指纹**
> （实测 `chrome120`/`chrome124` → `403 Forbidden`，不伪装 → `200`），
> 因此该源在代码中**固定走 `NO_IMPERSONATE`（关闭指纹伪装）**，
> 上述 `CNINFO_RATE_LIMIT_INTERVAL` 只控制节流间隔，不影响该行为。

### 7.3 智能路由

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ROUTER_HEALTH_CHECK_INTERVAL` | `60` | 健康检查间隔（秒）|
| `ROUTER_MIN_SCORE` | `20.0` | 数据源最低健康评分，低于此值不再选中 |
| `ROUTER_RECOVERY_AMOUNT` | `10.0` | 定时恢复评分的增量 |

### 7.4 MCP 服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_TRANSPORT` | `stdio` | 传输模式：`stdio` / `sse` / `http` |
| `MCP_HOST` | `0.0.0.0` | HTTP 网关监听地址（`--host` 覆盖）|
| `MCP_PORT` | `8000` | HTTP/SSE 监听端口（`--port` 覆盖）|
| `MCP_ALLOWED_HOSTS` | 空 | Host 白名单，逗号分隔；空=仅本机回环；`*`=全放行（仅可信内网）|
| `WS_SERVER_ENABLED` | `false` | 启用 WebSocket 推送服务 |
| `WS_PORT` | `8765` | WebSocket 端口 |
| `WS_TOKEN` | 空 | WebSocket 认证 token |

### 7.5 缓存与计算

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CACHE_MAX_SIZE` | `5000` | 内存缓存最大条目数，`0` 表示无限 |
| `CACHE_FILE_DIR` | `.cache` | 文件缓存目录 |
| `CACHE_FILE_ENABLED` | `true` | 是否启用文件缓存 |
| `INDICATOR_LOOKBACK_DAYS` | `400` | 技术指标计算回溯天数 |
| `CHIP_DISTRIBUTION_BINS` | `50` | 筹码分布直方图分箱数 |

### 7.6 日志

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_DIR` | `logs` | 日志文件目录 |

### 7.7 第三方可选集成

| 变量 | 说明 |
|------|------|
| `IWENCAI_API_KEY` | 同花顺问财 OpenAPI 密钥，`get_wencai_news` 必需 |

---

## 八、故障排查

### 8.1 MCP 频繁断连、报错 `-32000` 且不再重连

**症状**：Agent 端连接器显示断开，错误码 `-32000`，重启前无法恢复。

**根因**：eltdx 3.x 为 Rust 内核（pyo3 native），panic 时抛出的 `PanicException`
继承自 `BaseException`，**不被 `except Exception` 捕获**，会穿透路由层直接杀穿事件循环，
导致 server 进程干净退出。触发条件为并发调用 × native 内核单例。

**修复**：v3.3.18 已内置三层防御（不降级 eltdx）：

| 层级 | 措施 |
|------|------|
| L1 | `SmartRouter.route()` 增加 `except BaseException` 按源降级（KI / SE / CancelledError 放行）|
| L2 | `_NativePanicShield` 互斥锁序列化所有 native 调用，并将 `BaseException` 转为 `RuntimeError` |
| L2b | `eltdx_stream` 第二裸 client 施加同款盾 |
| L3 | `_safe_call` 增加 `BaseException` 双保险 |

**处置**：升级到 **v3.3.18 或更高版本**；确认版本用 `curl http://<host>:8000/health`。

### 8.2 国内数据源全部超时 / 走代理

**原因**：`requests` 会回退读取注册表代理配置，导致国内数据源误走本机代理。

**修复**：v3.3.14 起 `tradex/__init__.py` 已设置 `NO_PROXY=*`（仅 pop 环境变量不够）。
如仍异常，检查系统级代理与 `NO_PROXY` 环境变量。

### 8.3 `ImportError: cannot import name 'FastMCP'`

**原因**：环境解析到 `mcp` 2.x（已移除 v1 `FastMCP` API）。

**修复**：`pip install "mcp>=1.0.0,<2"`。

### 8.4 HTTP 模式返回 `421`

**原因**：DNS rebinding 防护拒绝非白名单 Host。

**修复**：用 `--allowed-hosts` 或 `MCP_ALLOWED_HOSTS` 放行目标 IP/域名（逗号分隔）。

### 8.5 `get_wencai_query` 调用失败

**修复**：`pip install pywencai` 并安装 Node.js；`get_wencai_news` 另需 `IWENCAI_API_KEY`。

### 8.6 已知限制

1. **eltdx F10 字段为通达信内部编码**（`T007` / `T008` 等）：财报 / 分红 / 资讯 / 北向
   4 个接口返回原始编码字段，作为 AKShare 中文源的降级补充源
2. **免费行情站不主动推送**：eltdx `drain_pushes` 实测 0 帧，"推送"实为 `refresh_stream`
   游标增量轮询（已封装在 `eltdx_stream.py`）
3. **eltdx F10 延迟较高**（约 2 秒）：走 7615 HTTP 网关，但题材归因数据为 AKShare 所无
4. **本地数据非实时**：`get_local_kline` / `get_local_minute` 读通达信本地 `vipdoc` 二进制，
   最新仅到上一交易日收盘，**仅用于回测与历史分析**
5. **Wind 等商业数据源不在本项目内**：需通过独立 MCP Server 或 Agent 的 connector 接入

### 8.7 诊断工具

| 工具 | 用途 |
|------|------|
| `health_check` | 系统整体健康检查（模块状态 / 数据源 / 缓存综合诊断）|
| `get_data_source_health` | 各数据源成功率 / 延迟 / 封禁状态 |
| `get_cache_stats` | 缓存命中率 / 容量 / TTL 过期情况 |
| `list_all_tools` | 列出所有已注册 MCP 工具及模块归属 |
| `get_data_source_dashboard` | 数据源看板数据（健康 / 路由 / 工具分布）|

---

## 附录 A：工具统计

### A.1 按能力层

| 层级 | 工具数 | 占比 |
|------|--------|------|
| L1 数据获取层 | 105 | 81.4% |
| L2 计算引擎层 | 8 | 6.2% |
| L3 决策支持层 | 16 | 12.4% |
| **合计** | **129** | **100%** |

### A.2 按源码模块

| 模块 | 工具数 | 模块 | 工具数 |
|------|--------|------|--------|
| `eltdx_data.py` | 31 | `macro_fx.py` | 8 |
| `news_events.py` | 14 | `market.py` | 7 |
| `financial_stmt.py` | 8 | `technical_indicators.py` | 6 |
| `signal_data_base.py` | 6 | `price_data.py` | 5 |
| `industry.py` | 5 | `diagnostics.py` | 5 |
| `company_info.py` | 4 | `valuation.py` | 4 |
| `signal_data_flow.py` | 4 | `signal_data_board.py` | 3 |
| `signal_generation.py` | 3 | `composite_analysis.py` | 3 |
| `signal_data_etf.py` | 2 | `signal_data_cb.py` | 2 |
| `performance_metrics.py` | 2 | `factor_analysis.py` | 2 |
| `stock_screening.py` | 2 | `local_data.py` | 2 |
| `analysis_engine.py` | 1 | | |

### A.3 快速上手示例

```python
# 通过 MCP 客户端调用（伪代码）
await call("get_realtime_quote", symbol="600519")          # 贵州茅台实时行情
await call("eltdx_get_kline", code="601868", period="daily", count=100)
await call("get_income_statement", symbol="600519")        # 利润表（8 期，单位亿元）
await call("calculate_macd", prices=[...])                 # MACD 计算
await call("generate_trading_signal", symbol="600519")     # 交易信号
await call("analyze_stock_comprehensive", symbol="600519") # 综合分析
await call("health_check")                                 # 系统诊断
```

---

## 附录 B：相关文档

| 文档 | 路径 |
|------|------|
| 项目说明 | `README.md` |
| 架构设计 | `docs/architecture.md` |
| **数据源待修正清单** | **`docs/known-issues.md`** |
| 变更日志 | `CHANGELOG.md` |
| 健康检查脚本 | `scripts/health_check.py` |
| **全量真实网络冒烟** | **`scripts/live_smoke.py`** |
| 环境变量模板 | `tradex/.env.example` |
| MCP 配置示例 | `tradex/.mcp.json`、`config/mcp-servers.json` |

---

## 附录 C：全量真实网络冒烟（`scripts/live_smoke.py`）

**为什么需要**：`pytest -m "not network"` 的响应全是 mock 的，**不走 TLS 握手、不走真实节流**。
防封改造一旦让某个源被 WAF 拦截（v3.3.20 巨潮 403 就是如此），单测会全绿而线上恒返回空。
真实网络验证无法被 mock 测试替代。

```bash
python scripts/live_smoke.py                      # 全量：主机 + 数据源 + MCP 工具
python scripts/live_smoke.py --sources-only       # 只测数据源层
python scripts/live_smoke.py --tools-only         # 只测 MCP 工具层
python scripts/live_smoke.py --types fund_hold,valuation   # 只测指定数据类型
python scripts/live_smoke.py --out report.md      # 输出 Markdown 报告
```

三层探测：

| 层 | 对象 | 说明 |
|----|------|------|
| 〇 | 数据源主机连通性 | 分辨「上游挂了」与「自己改坏了」 |
| 一 | **每个已注册 fetcher**（102 个） | 逐个直打，含所有备源（不只主源） |
| 二 | **每个 MCP 工具**（129 个） | 调用底层函数，校验返回结构 |

状态分级（**只有 `FAIL` 算回归**，退出码才非 0）：

| 状态 | 含义 |
|------|------|
| `OK` | 取到数据 |
| `EMPTY` | 无异常但结果为空 |
| `FAIL` | 抛异常 —— **回归** |
| `ENV` | 缺本地通达信目录 / API Key，本机注定拿不到 |
| `UPSTREAM` | 上游节点故障或第三方库自身问题（人工复核后登记） |
| `AKSHARE` | akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| `SKIP` | 必填参数不足，或纯计算类（需输入数组、不联网） |

单项失败会自动重试 1 次（上游偶发 503 / 连接重置很常见，不重试会把抖动误判为回归）。
