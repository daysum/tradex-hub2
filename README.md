# tradex-hub

<p align="center">
  <strong>AI金融智能决策中台</strong><br/>
  AKShare 封装 · eltdx 通达信协议 · astock_signals 信号模块 · 量化计算引擎 · SmartRouter 全量路由 · 本地 MCP Server · 129 个工具
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python"/>
  <img src="https://img.shields.io/badge/MCP-1.0-green.svg" alt="MCP"/>
  <img src="https://img.shields.io/badge/License-Apache--2.0-yellow.svg" alt="License"/>
  <img src="https://img.shields.io/badge/Data-A股-red.svg" alt="Data Scope"/>
  <img src="https://img.shields.io/badge/Tools-129-orange.svg" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/Version-3.3.18-blue.svg" alt="Version"/>
</p>

---

## 项目定位

为 AI Agent（WorkBuddy / Claude Code / Cursor）提供 **A 股金融数据 + 量化计算 + 决策支持的 MCP 接口**。

**三层能力模型**（129 个工具）：
- **L1 数据获取层**：通过 SmartRouter 统一获取数据（行情/财务/估值/行业/新闻/宏观/涨停板/龙虎榜/五档盘口/逐笔/分时/题材/短线指标等），其中 eltdx 通达信协议 31 个工具
- **L2 计算引擎层**（8个工具）：技术指标计算6个、绩效指标计算2个
- **L3 决策支持层**（16个工具）：交易信号生成3个、多因子分析2个、条件选股2个、系统诊断5个、综合分析3个、技术分析引擎1个

**数据源架构（v3.3.18）**：
- **data_sources 数据源层**：78 个数据类型，102 个数据源注册（37 种源），按封禁风险分三梯队
- **SmartRouter 全量覆盖**：L1 工具通过 `SmartRouter.route()` 统一获取数据，自动健康评分/降级/故障隔离
- **eltdx 3.2.2**：行情类第一主源，31 个工具覆盖五档盘口/集合竞价/逐笔/F10/分时/K线/全景档案/短线指标/题材/分类行情等；新增常驻连接管理器 `eltdx_stream.py`（游标增量轮询实现准实时五档盘口）。3.x 为 Rust 重写内核（native wheel，cp310-abi3）
- **数据源梯队**：第一梯队（eltdx/腾讯/本地 vipdoc，不封 IP）+ 第二梯队（同花顺/新浪/巨潮/财联社，低风险）+ 第三梯队（东财 push2/push2ex/slist，仅独有数据 + 限流防封）
- **数据源看板**：`python -m tradex.dashboard`（端口 8765），可视化查看数据源健康/路由/工具分布；MCP 工具 `get_data_source_dashboard` 可在 Agent 对话中查询

**v3.3.9 更新**：全局直连（import 时清代理环境变量，国内数据源不走代理）+ 新增数据源（同花顺 4 接口 / 东财 slist 板块归属 / 东财限流防封 / 实时涨跌家数 / 行业涨幅 / 通达信本地数据）+ 2 个本地数据 MCP 工具，工具数 127→129。

---

## 项目架构

<p align="center">
  <img src="assets/architecture.svg" alt="tradex-hub 架构图" width="100%"/>
</p>

> **数据源优先级**：第一梯队（eltdx/腾讯/本地，不封 IP）优先用，第二梯队（同花顺/新浪/巨潮）低风险，第三梯队（东财）仅用于独有数据 + 限流防封（间隔 ≥1s + 随机抖动）。

## MCP 工具清单（129 个）

### 1. 公司信息（4 个）— `company_info`

| 工具名 | 功能 |
|--------|------|
| `search_stock` | 搜索A股股票，支持名称或代码模糊匹配 |
| `get_company_info` | 公司基本信息：行业、市值、股本、上市日期 |
| `get_company_profile` | 主营业务构成与业务描述 |
| `get_competitors` | 同行业公司列表（竞争对手/可比公司） |

### 2. 行情数据（5 个）— `price_data`

| 工具名 | 功能 |
|--------|------|
| `get_realtime_quote` | 实时行情：最新价/涨跌幅/量/换手率/PE/PB |
| `get_historical_price` | 历史K线（日/周/月，前复权/后复权/不复权） |
| `get_intraday_data` | 分时数据（当日分时走势/分钟K线） |
| `get_market_capitalization` | 总市值与流通市值 |
| `get_stock_list` | A股全列表，支持按市值筛选 |

### 3. 财务报表（8 个）— `financial_stmt`

| 工具名 | 功能 |
|--------|------|
| `get_income_statement` | 利润表（按季度，默认8期） |
| `get_balance_sheet` | 资产负债表（按季度，默认8期） |
| `get_cash_flow_statement` | 现金流量表（按季度，默认8期） |
| `get_financial_line_item` | 从三表中提取特定科目时间序列（如"营业总收入"） |
| `get_financial_indicators` | ROE/毛利率/净利率/资产负债率等多维度指标 |
| `get_growth_rates` | 营收增长率/净利润增长率等成长性指标 |
| `get_per_share_data` | 每股指标：EPS / BPS / CFPS |
| `get_segments_revenue` | 主营构成：按产品/地区分拆营收与毛利率 |

### 4. 估值分析（4 个）— `valuation`

| 工具名 | 功能 |
|--------|------|
| `get_valuation_metrics` | PE/PB/PS 历史时间序列（默认100交易日） |
| `get_dividend_data` | 历史分红派息：每股派息/除权日/登记日 |
| `get_institutional_holdings` | 十大流通股东/机构持股变动 |
| `get_analyst_rating` | 分析师评级/目标价/预测EPS |

### 5. 行业板块（5 个）— `industry`

| 工具名 | 功能 |
|--------|------|
| `get_industry_list` | 行业板块列表（涨跌幅/领涨股） |
| `get_industry_stocks` | 指定行业所有成分股 |
| `get_concept_list` | 概念板块列表（华为/ChatGPT/芯片等） |
| `get_sector_fund_flow` | 板块资金流向排名（行业/概念/地域，今日/5日/10日） |
| `get_industry_pe` | 行业板块历史行情（可用于行业PE估值趋势） |

### 6. 市场总览（5 个）— `market`

| 工具名 | 功能 |
|--------|------|
| `get_market_overview` | 主要指数实时快照（上证/深证/创业板/科创50/沪深300） |
| `get_money_flow` | 个股资金流向：主力/超大单/大单/中单/小单 |
| `get_north_bound_flow` | 北向资金净流入（沪股通+深股通） |
| `get_limit_up_down` | 当日涨停/跌停股票池（封单额/连板天数） |
| `get_dragon_tiger` | 龙虎榜：机构与游资买卖席位 |

### 7. 新闻公告（4 个）— `news_events`

| 工具名 | 功能 |
|--------|------|
| `get_stock_news` | 个股相关新闻资讯 |
| `get_financial_calendar` | 财报披露时间表 |
| `get_company_announcements` | 上市公司公告 |
| `search_news` | 按关键词搜索新闻（可限定个股范围） |

### 8. 宏观衍生（8 个）— `macro_fx`

| 工具名 | 功能 |
|--------|------|
| `get_macro_gdp` | 中国GDP（季度，含三次产业） |
| `get_macro_cpi` | CPI消费者价格指数（月度，同比/环比） |
| `get_macro_pmi` | PMI采购经理指数（制造业/非制造业/分项） |
| `get_macro_money_supply` | M0/M1/M2 货币供应量（月度，同比增速） |
| `get_fx_rate` | 外汇汇率（美元/欧元/英镑/日元/港币兑人民币） |
| `get_bond_yield_curve` | 国债收益率曲线（1/3/5/7/10/30年） |
| `get_margin_trading` | 融资融券余额（市场汇总/个股） |
| `get_insider_trading` | 股东/高管增减持（内部交易） |

### 9. A股信号+品种（17 个）— `signal_data`

| 工具名 | 功能 | 数据源 |
|--------|------|--------|
| `get_hot_stocks` | 涨停股票+人工标注的主题归因 | 同花顺 editorial |
| `get_lockup_expiry` | 限售解禁日历（历史+未来90天） | 东方财富 datacenter |
| `get_concept_attribution` | 概念/行业/地域板块归属 | 东方财富 / 百度 |
| `get_profit_forecast` | 分析师一致预期EPS + Forward PE/PEG | 同花顺 |
| `get_technical_indicator` | 13种技术指标（MACD/RSI/布林带/ATR等） | AKShare + stockstats |
| `list_technical_indicators` | 列出所有支持的技术指标及说明 | — |
| `get_northbound_flow_signal` | 北向资金流向（沪深股通） | 同花顺 hsgtApi |
| `get_fund_flow_signal` | 个股资金流向（主力/大中小单） | 东财 push2 |
| `get_dragon_tiger_signal` | 龙虎榜席位明细+机构动向 | 东财 datacenter |
| `get_industry_comparison_signal` | 行业横向对比排名 | 东财 push2 |
| `get_etf_realtime_data` 🆕 | ETF实时行情（IOPV/折价率/换手率） | AKShare fund_etf_spot_em |
| `get_etf_kline_data` 🆕 | ETF历史K线（日/周/月，支持复权） | AKShare fund_etf_hist_em |
| `get_cb_realtime_data` 🆕 | 可转债实时行情（溢价率/转股价/评级） | AKShare bond_zh_cov |
| `get_cb_value_analysis_data` 🆕 | 可转债价值分析（溢价率历史曲线） | AKShare bond_zh_cov_value_analysis |
| `get_limit_up_board` 🆕 v3.0.0 | 涨停板/炸板/跌停股票池（封单额/连板天数） | 东财 push2 clist |
| `get_board_sentiment` 🆕 v3.0.0 | 打板情绪速算（涨停/炸板/跌停情绪指标） | 本地计算 |
| `get_limit_up_insight` 🆕 v3.0.0 | 涨停揭秘（题材归因/封单强度/资金流向） | 同花顺 limit_up_detail |

### 10. eltdx 通达信协议（31 个）— `eltdx_data` 🆕 v3.3.9 扩充

**盘口/推送**（S 级，交易看板底座）：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_depth` | 五档盘口快照（买1-5/卖1-5/内外盘） |
| `eltdx_stream_health` | 常驻连接管理器健康状态 |

**集合竞价/逐笔**：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_auction` | 集合竞价序列（9:15-9:25） |
| `eltdx_get_ticks` | 历史逐笔成交 |
| `eltdx_get_today_ticks` | 当日逐笔成交 |
| `eltdx_get_opening_match` | 当日 9:25 开盘撮合 |
| `eltdx_get_opening_match_history` | 历史开盘撮合 |
| `eltdx_get_auction_data` | 竞价汇总（开盘价/量/额/涨跌） |

**分时**：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_minutes` | 当日分时 |
| `eltdx_get_minute_history` | 历史分时（盘后复盘） |
| `eltdx_get_buy_sell_strength` | 买卖强度（日内强弱） |

**K 线**：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_kline` | K线（日/周/月/分钟） |
| `eltdx_get_full_kline` | 全量K线（回测） |
| `eltdx_get_adjusted_kline` | 复权K线（qfq/hfq） |

**F10/基本面**：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_f10` | F10资料（概况/题材/诊断） |
| `eltdx_get_f10_extra` | F10通用入口（估值/排名/治理等） |
| `eltdx_get_finance_report` | 财务报表（资产负债/利润/现金流） |
| `eltdx_get_dividend_financing` | 分红融资历史 |
| `eltdx_get_company_news` | 公司资讯/研报 |
| `eltdx_get_northbound_holding` | 沪深股通持股 |
| `eltdx_get_finance_batch` | 批量财务字段 |
| `eltdx_get_stock_profile` | 全景档案（行情+财务一表） |
| `eltdx_get_shortline_indicators` | 21 项短线打板指标 |

**题材/分类**：
| 工具名 | 功能 |
|--------|------|
| `eltdx_get_stock_topics` | 个股关联题材 |
| `eltdx_get_topic_stocks` | 题材成分股排名 |
| `eltdx_get_security_codes` | 全市场证券代码表 |
| `eltdx_get_category_quotes` | 分类行情（涨幅榜/成交额榜） |
| `eltdx_get_trading_day` | 交易日判定 |
| `eltdx_get_special_limits` | 特殊品种涨跌停参考价 |
| `eltdx_get_special_limits_scan` | 扫描全市场特殊涨跌停 |
| `eltdx_get_capital_changes` | 股本变动历史 |

### 11. 技术指标计算（6 个）— `technical_indicators` 🆕 V2.5.0

纯函数实现，输入价格数组，输出与 Excel/通达信一致的指标值。

| 工具名 | 功能 | 算法 |
|--------|------|------|
| `calculate_ma_ema` | MA/EMA 均线计算 | SMA前n-1个为null；EMA首值用SMA初始化 |
| `calculate_macd` | MACD 指标计算 | DIF=EMA(fast)-EMA(slow)；DEA=EMA(DIF)；MACD柱=2*(DIF-DEA) |
| `calculate_kdj` | KDJ 随机指标 | RSV=(C-LLV)/(HHV-LLV)*100；K=2/3前K+1/3 RSV；D=2/3前D+1/3 K；J=3K-2D |
| `calculate_rsi` | RSI 相对强弱指数 | Wilder 平滑法；RSI=100-100/(1+平均涨幅/平均跌幅) |
| `calculate_boll` | BOLL 布林带 | 中轨=SMA；上下轨=中轨±k*std；带宽+ Percent B |
| `calculate_atr` | ATR 平均真实波幅 | TR=max(H-L,\|H-前C\|,\|L-前C\|)；ATR=EMA(TR) |

### 12. 绩效指标计算（2 个）— `performance_metrics` 🆕 V2.5.0

| 工具名 | 功能 |
|--------|------|
| `calculate_performance` | 完整绩效报告（21项指标）：收益/风险/风险调整收益/交易质量/费用统计/基准对比 |
| `list_performance_metrics` | 列出所有支持的绩效指标及计算公式 |

### 13. 信号生成（3 个）— `signal_generation` 🆕 V2.5.0

| 工具名 | 功能 |
|--------|------|
| `generate_trading_signal` | 单票交易信号生成（5级信号+评分+多指标组合） |
| `scan_stocks_for_signals` | 批量扫描股票信号（按评分排序） |
| `validate_signal_quality` | 信号质量验证（前瞻收益分析） |

### 14. 因子分析（2 个）— `factor_analysis` 🆕 V2.5.0

| 工具名 | 功能 |
|--------|------|
| `calculate_factor_score` | 多因子综合评分（5类22因子，Z-Score 标准化） |
| `get_factor_catalog` | 获取因子库清单（估值/盈利/成长/动量/质量 5 大类） |

### 15. 条件选股（2 个）— `stock_screening` 🆕 V2.5.0

| 工具名 | 功能 |
|--------|------|
| `screen_stocks` | 条件选股扫描（5类30+条件，AND 组合） |
| `get_screening_conditions` | 获取支持的选股条件清单 |

### 16. 系统诊断（5 个）— `diagnostics` 🆕 v3.0.0

系统自省与运维诊断工具，不依赖外部数据源。

| 工具名 | 功能 |
|--------|------|
| `get_data_source_health` | 数据源健康检查（各数据源成功率/延迟/封禁状态） |
| `list_all_tools` | 列出所有已注册的 MCP 工具及模块归属 |
| `get_cache_stats` | 缓存统计（命中率/容量/TTL 过期情况） |
| `health_check` | 系统整体健康检查（模块状态/数据源/缓存综合诊断） |
| `get_data_source_dashboard` 🆕 v3.1.0 | 数据源看板（25 类型 34 源的健康/路由/工具分布，供 Agent 对话查询） |

### 17. 综合分析（3 个）— `composite_analysis` 🆕 v3.0.0

组合调用 L1 数据 + L2 计算结果，输出多维度综合分析报告。

| 工具名 | 功能 |
|--------|------|
| `analyze_stock_comprehensive` | 个股综合分析（基本面+技术面+资金面+估值综合评分） |
| `analyze_industry_comparison` | 行业横向对比分析（多维度排名+景气度评估） |
| `analyze_market_overview` | 市场总览分析（指数/资金/情绪/板块轮动综合研判） |

### 18. 技术分析引擎（1 个）— `analysis_engine` 🆕 v3.0.0

| 工具名 | 功能 |
|--------|------|
| `analyze_technical` | 技术分析引擎（多指标组合分析，输出趋势/支撑压力/买卖信号综合研判） |

### 19. 本地数据（2 个）— `local_data` 🆕 v3.3.9

读通达信本地 `vipdoc` 二进制文件（离线，不封 IP），**仅用于回测和历史分析，非盘中实时**。本地数据读取能力源自开源项目 [mootdx](https://github.com/mootdx/mootdx)。

| 工具名 | 功能 |
|--------|------|
| `get_local_kline` | 通达信本地日线（离线，最新到上一交易日收盘） |
| `get_local_minute` | 通达信本地分钟线（离线，需通达信已下载分钟线） |

---

## 安装

### 1. 克隆项目

```bash
git clone https://github.com/mysum/tradex-hub2.git
cd tradex-hub2
```

### 2. 创建独立 venv（推荐）

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows
```

### 3. 安装 astock_signals 独立包（v1.1.0）

astock_signals 已从 tradex-hub 独立成包，需先安装：

```bash
pip install -e astock_signals/
```

> 或从 GitHub 克隆：`git clone https://github.com/wolfjkd/astock_signals.git && pip install -e astock_signals/`

### 4. 安装 tradex

```bash
cd tradex
pip install hatchling editables
pip install --no-build-isolation -e .
```

### 5. 安装运行时依赖

```bash
pip install "akshare>=1.18.94" mcp pandas pydantic "eltdx>=3.2.2"
```

---

## HTTP 远程模式（v3.3.12+）

tradex **原生支持 HTTP 传输**（基于 mcp SDK `FastMCP`，无需 supergateway 等外部转发进程），
单端口同时提供三种端点：

```bash
python -m tradex --http                 # HTTP gateway（默认监听 0.0.0.0:8000）
python -m tradex --http --allowed-hosts 47.102.x.x,example.com   # 对外部署：Host 白名单
python -m tradex --http --allowed-hosts '*'    # 仅可信内网：关闭 Host 校验（不推荐公网）
```

> [!important] 网络边界（铁律）
> 本网关监听与代理**无关**：数据源流量（AKShare/eltdx/东财等）**永远直连**，import 时已清代理环境变量；
> 本机 Clash 代理（127.0.0.1:7897）**只用于 GitHub push/clone**，禁止用作服务/网关地址。
> 网关默认监听 `0.0.0.0`（云/容器形态），远程可访问性由 `--allowed-hosts` 白名单控制。

| 端点 | 传输 | 说明 |
|------|------|------|
| `POST /mcp` | **Streamable HTTP** | MCP 2025 新标准传输，Dify / LangChain / Claude 远程客户端首选 |
| `GET /sse` + `POST /messages/` | legacy SSE | 旧客户端兼容（MCP 官方已标 deprecated，保留过渡） |
| `GET /health` | HTTP | 健康检查：`{"status":"ok","service":"tradex-mcp","version":"3.3.18","tools":129}`，轻量探活不触发数据源网络 |

**Dify / LangChain 配置示例**（Streamable HTTP）：

```
URL: http://<服务器IP或域名>:8000/mcp
类型: streamable-http（或 SSE 旧端点用 http://<服务器IP或域名>:8000/sse）
```

> 运行 HTTP 模式需安装 Web 依赖：`pip install "tradex[http]"`（uvicorn/starlette/sse-starlette，均已在 requirements 声明）。

> [!注意] Host 校验（DNS rebinding 防护）
> 默认**仅允许本机回环**访问（伪造 Host 返回 421）。监听默认 `0.0.0.0`，对外/跨机访问时用 `--allowed-hosts` 或环境变量 `MCP_ALLOWED_HOSTS` 放行目标 Host/IP（逗号分隔）；`*` 表示关闭校验全放行——**仅限可信内网**。`MCP_HOST`/`MCP_PORT` 可作为 `--host`/`--port` 的默认值。

---

## 配置到 AI Agent

编辑 MCP 配置文件（如 `~/.trae-cn/mcp.json` 或对应 AI Agent 的配置文件）：

```json
{
  "mcpServers": {
    "tradex": {
      "command": "/path/to/venv/Scripts/python.exe",
      "args": ["-m", "tradex"],
      "env": {}
    }
  }
}
```

本地开发用 **stdio**（如上，`python -m tradex` 默认）；远程/容器/多客户端场景用
**HTTP 模式**（`python -m tradex --http`，见上节）。

保存后重启 AI Agent，连接器页面 `tradex` 应显示绿色。

---

## 验证

重启后在对话里测试：

```
查中国能建（601868）K线
```

AI 会调用 `mcp__tradex__eltdx_get_kline`，返回 100 根日 K 线。

---

## 已知限制

1. **eltdx F10 字段为通达信内部编码**（`T007`/`T008` 等）：财报/分红/资讯/北向 4 个接口返回原始编码字段，作为 akshare 中文源的降级补充源，字段中文映射留待后续
2. **免费行情站不主动推送**：eltdx `drain_pushes` 实测 0 帧，「推送」实为 refresh_stream 游标增量轮询（`eltdx_stream.py` 已封装）
3. **eltdx F10 延迟较高**（~2 秒）：走 7615 HTTP 网关，但数据独有（题材归因 AKShare 没有）
4. **SmartRouter 全量覆盖**：L1 工具通过 `SmartRouter.route()` 统一获取数据，自动健康评分/降级/故障隔离，74 数据类型 85 源（28 种）
5. **Wind 等独立 MCP 不在本项目里**：通过独立 MCP Server 或 AI Agent 的 connector 系统接入

---

## 版本历史

| 版本 | 日期 | 内容 |
|------|------|------|
| v3.3.29 | 2026-09-20 | **修复 `board_industry_hist_em`（行业历史行情）—— `industry_data` 7 个 endpoint 至此全部有可用源，B 组清零**。东财板块日K路由整条链路故障（所有 `push2his*` 连接被拒；`push2delay` 认得出板块但 `klines` 为空），akshare 主源 + ths 备源都不覆盖 → **换数据来源**：实测**同花顺板块K线免鉴权可用**（`d.10jqka.com.cn/v6/line/bk_{code}/{seg}/last.js`，`01`=日K/`11`=周K/`21`=月K，行业代码 `881xxx`）。实现放在 `ths_fetchers`（10jqka 传输归它管）。降级顺序刻意「**同花顺优先 → 东财兜底**」：东财放前面会先跑昂贵的 BK 解析再失败（首调 **24s** 超时），同花顺 **1.5s**。新增 22 条测试，测试 770 passed |
| v3.3.28 | 2026-09-20 | **修复 `board_industry_cons_em`（行业板块成分股）**，`get_industry_stocks` / `get_competitors` 从恒失败变为正常。此前该 endpoint **两个源都不覆盖**（akshare 打 `29.push2` 被拒连 + ths 明确不支持）→ 改本项目直连（`push2delay` 的 clist 经 `em_get` 走镜像可取到）。排查中揪出 **akshare 原版两个隐藏缺陷**：① **只搜行业板块、漏了概念板块** —— `白酒`/`新能源车` 只存在于概念板块，而调用方 docstring 明确把它们列为合法输入；② **板块列表只取第一页** —— 服务端把 `pz` 硬顶在 100，而行业 496 / 概念 504 个，靠后的名字被误判为不存在。本实现两类板块都检索 + 翻页 + **可断点续取的缓存**（首次 ~10s → 之后 ~1.1s）；字段改**按名映射**（akshare 按位置依赖响应 key 顺序 + `reset_index` 插入列）。路由超时单独放宽 40s（与 `fund_hold` 做法一致）。新增 16 条测试，测试 748 passed |
| v3.3.27 | 2026-09-20 | **修复 `historical_kline` 备源**，改为本项目直连两层降级：① 东财 `push2his` kline（走 `em_get`，原生复权/振幅）；② **腾讯 `proxy.finance.qq.com` 兜底**。**为什么必须加第②层**：实测东财日K路由**整体不可用** —— `push2his` 连接被拒（10/10），`push2delay` 该路径 `rc=0` 但 `klines` 为空（只服务分时）；只把 akshare 换成直连东财**仍取不到数**，腾讯兜底才是今天就能工作的关键。字段语义已用主源 `eltdx` **逐字段交叉验证**（腾讯 bar `[1]`开`[2]`收`[3]`高`[4]`低`[5]`量`[7]`换手率`[8]`成交额(**万元**)，与 eltdx 完全吻合）；振幅/涨跌幅/涨跌额腾讯不提供，按前收自算。另显式校验 kline 字段个数，避免「少一列→整体错位」（v3.3.16 同类事故）。输出列与 akshare 完全一致。新增 27 条测试，测试 732 passed |
| v3.3.26 | 2026-09-20 | **修复 `industry_comparison` 备源，并揪出一处被掩盖的数据错误**。① 传输：akshare 打 `push2/api/qt/clist/get`（正是统一入口已覆盖+已配镜像的路径），裸 requests 被拒连、经 `em_get` 200 → 改本项目直连。② **数据正确性（更严重）**：旧代码读 akshare 的 `板块`/`涨跌幅`/`领涨股票` 三列，但**这三个列名在 akshare 输出里都不存在**（真名 `名称`/`今日涨跌幅`/`今日主力净流入最大股`）—— 即便传输没坏，返回的也是 `name="None"`、`change_pct=0.0`、`leader="None"` 的垃圾数据；传输一直失败反而掩盖了它。直连版按字段名取（f14/f3/f204）。③ 顺带补齐真实涨跌家数（旧版因字段集缺 f104/f105 而写死 0），与 em_push2 源结构对齐。新增 15 条测试（含「输出不得退化成 'None'/0.0」守卫），测试 705 passed |
| v3.3.25 | 2026-09-20 | **修复 `minute_data` 备源（东财分时）**，根因两层：① akshare 打 `70.push2.eastmoney.com`（数字前缀子域，**无镜像**）；② 用 `/details/sse` **SSE 长连接** —— 非流式 GET 必超时，且即便流式读到首个事件就 break，**`resp.close()` 仍要排空剩余响应体，实测独占 8.6s**。改为打 `push2.eastmoney.com`（有镜像）的 **`/details/get` 非流式变体**（实测 0.28s，载荷一致），稳态 1.25s（流式版 10.6s，**快 8 倍**）。排查中先实现了流式版，定位瓶颈后**撤掉整套流式代码**改用 `em_get`。另修正 secid 归一化：显式前缀优先（`sh000001` 是上证指数须 market=1，akshare 原版有误）。新增 19 条测试，测试 690 passed |
| v3.3.24 | 2026-09-20 | **修复 `hot_rank` 主源（东财人气榜）**。根因**不是「源挂了」**：akshare 的 `stock_hot_rank_em`/`stock_hot_up_em` 分两步 —— 先裸 requests POST `emappdata` 取榜单（**可达**），再裸 requests GET `push2/api/qt/ulist.np/get` 取行情（**被拒连**）；而同一 URL 经 `em_get` 可正常取到（走 push2delay 镜像）。改为**本项目直连**（新增 `em_post` 统一 POST 入口，把 `em_get` 编排抽成共用 `_em_request`，使 POST 也享有节流/重试/镜像/熔断）。**按代码 join 两个列表而非按位置对齐** —— 两者是独立请求，条数/顺序可能不一致，按位置拼会让代码与名称静默错位（v3.3.16 fund_hold 同类事故）。其余 5 个 endpoint 仍走 akshare。实测主源恢复 100×6、`up` 100×7，路由重新命中主源。新增 26 条测试，测试 671 passed |
| v3.3.23 | 2026-09-20 | **东财请求失败熔断**（熔断器模式）：某个 **(主机, 路径)** 主机级失败（连接失败/5xx）后进入冷却（默认 60s），期间立即返回 `None` 让调用方走备源，不再白跑满 `max_retries` 次退避。**按「主机+路径」而非仅主机**是关键的 —— 实测 `push2his` 是「`kline/get` 挂、`fflow/daykline` 活」，按主机熔断会误伤健康路径；`403`/`429`/`rc=102` 属按 IP 风控故**不熔断**；有可用镜像时走镜像静默期不额外熔断；冷却到期自动重探。新增 `reset_dead_hosts()`/`get_dead_host_status()`/`reset_host_health()` 与 `EM_DEAD_HOST_COOLDOWN`。实测 `index_daily_amount` 从 6.18s → **0.18s（快 34 倍）**。新增 17 条测试 + conftest 全局状态隔离 fixture，测试 645 passed |
| v3.3.22 | 2026-09-20 | **修复 `index_daily_amount` 三层降级全失败 + 撤回一处错误镜像**。主源 `push2his` 日K路由挂掉（实测 10/10 失败）后两条备源也失效：备源1 原用 akshare `stock_zh_index_daily_tx`，它**按年份逐年翻页**（上证指数从 1990 年起 → 37 次请求 ≈ **21 秒**），超过 SmartRouter 的 12s 超时 → 被误判为不可用；改为本项目直连腾讯 `proxy.finance.qq.com`（**单次请求 0.12s**，新增 `fetch_index_daily_tencent`）。同时**撤回 v3.3.21 引入过程中的错误配置**：曾把 `push2his` 映到 `push2delay`，实测载荷不等价（`kline/get` 日K 返回 `rc=0` 但 klines 为空；日线资金流 5 条 → 1 条，**静默降级**），已撤回，镜像表只保留载荷验证过等价的 `push2`；并加「镜像不服务该路径时不进静默期」保护，避免把健康主源一起跳过。新增 16 条测试 + 1 个守卫域名，测试 628 passed |
| v3.3.21 | 2026-09-20 | **东财主机镜像降级**：东财节点故障是**按主机（甚至按路由）**发生的 —— 实测 `push2.eastmoney.com/api/qt/clist/get` 返回 `curl: (56) Connection closed abruptly`（连命令行 curl 都 HTTP 000），而同主机 `fflow/kline` 正常、镜像 `push2delay` 全路由正常；此前无降级导致 6 处 push2 调用点（`em_push2`/`em_push2_fund_flow`/`limit_up_board`/`akshare_fetchers`/`astock_signals_fetchers`/`em_client.slist`）全线失效。在 `em_get` 统一入口加主机镜像：**只在连接层失败或 5xx 时切换**，`403`/`429`/`rc=102` 是按 IP 风控、换主机无效（实测 `slist` 在两台主机上都是 `rc=102`）故**不切**；镜像成功后进静默期（默认 300s）避免每次白等 3 次退避（实测首次 6.38s → 二次 1.15s）。新增 `reset_host_mirrors()`/`get_mirror_status()` 与 3 个环境变量；新增 17 条测试，测试 601 passed。建立 `docs/known-issues.md` 记录待修正数据源 |
| v3.3.20 | 2026-09-18 | **第二梯队数据源防限流收敛**：同花顺/财联社/巨潮/新浪/问财此前仍是裸请求，其中三处问题明显 —— `ths_fetchers` 模块级 `requests.Session()` 裸打 9 个接口（零指纹零节流）、`akshare_fetchers.fetch_profit_forecast`（registry priority=1）用**残缺的伪造 Mac UA**（无 Chrome/Safari 版本号，服务端识别伪造客户端的典型特征）、巨潮两处 POST **完全没有指纹伪装**。新增 `ClientGroup`（按源分组：各自独立节流器+会话+指纹+重试，东财 1.0s/同花顺 0.25s/财联社·巨潮 0.30s/新浪 0.25s/问财 0.50s）与具名注册表 `get_client(...)`；新增 `NO_IMPERSONATE` 模式（巨潮 WAF 封禁常见浏览器指纹，`chrome120/124` 实测 403）、`json_of()` GBK 兜底、200+空体静默限流识别；AST 静态守卫从 2 个域名扩到 8 个。新增 57 条测试，测试 584 passed |
| v3.3.19 | 2026-09-18 | **东财/腾讯请求统一收敛为单一防封入口**：改造前存在四类绕过节流的路径 —— `anti_ban_client` 用裸 `requests`（无 TLS 指纹）、`em_client` 与 `anti_ban_client` **各持一把节流锁**（同时取数实际速率翻倍）、7 处自建 curl_cffi 会话（其中 `concept._get_region_boards()` 是分页 while 循环**无任何间隔**）、腾讯走 `urllib` + `User-Agent: Mozilla/5.0` 裸奔（批量拉全市场约 68 个连续请求无间隔）。新增 `astock_signals/antiban_http.py` 统一层（8 版本指纹池 + UA 严格匹配 + 预定制节流器 + 指数退避 + 限流换指纹 + 东财 `rc` 语义识别：`rc=102` 限流重试 / `rc=100` 标的不存在立即返回）；`curl_cffi` 显式声明为依赖。新增 56 条测试，测试 527 passed |
| v3.3.18 | 2026-09-18 | **根因修复 MCP 频繁断连**：eltdx 3.x Rust 内核（pyo3 native）panic 抛 `PanicException`（继承 `BaseException`，不被 `except Exception` 捕获）穿透 `route()`/`_safe_call` 杀穿事件循环 → server 进程干净退出 → WorkBuddy 报 `-32000` 且永不重连。触发：v3.3.2 的 8 线程池并发 × v3.3.13 的 eltdx Rust 内核单例。三层防御（不降级 eltdx）：L1 `route()` 加 `except BaseException` 按源降级（KI/SE/CancelledError 放行）；L2 `_NativePanicShield` 互斥锁序列化所有 native 调用 + `BaseException→RuntimeError`（`_get_client()` 返代理，40+ 调用点零改动）；L2b `eltdx_stream` 第二裸 client 同包盾；L3 `_safe_call` BaseException 双保险。新增 14 条回归测试，测试 471 passed |
| v3.3.17 | 2026-09-15 | 依赖声明与健壮性修复：`mcp>=1.0.0,<2` 锁上限（mcp 2.x 移除 v1 `FastMCP` API，15 个工具模块在用，新环境解析到 2.x 直接 ImportError，pyproject+requirements 两处同修）；`WS_PORT` 环境变量非数字改容错回退默认（原在 import 期 ValueError 崩服务）；requirements.txt 补齐 `requests`/`stockstats`/`python-dotenv` 3 个运行时依赖；降级链 10 处静默吞错补 `logger.debug` 留痕（`get_market_capitalization` Tier1/2 + `news_events` 8 源，多源全挂时可排查各路死因）；pytest class-scoped fixture 迁模块级（pytest 10 兼容，消 2 条弃用警告）；删除 V2.5.0 时代无引用死脚本 `verify_v250.py`。测试 457 passed，MCP stdio 握手 129 工具验证通过 |
| v3.3.16 | 2026-09-15 | 修复机构持仓数据正确性与超时：`fetch_fund_hold_data` 因 akshare 按位置映射列而上游行序漂移 → **整列语义全错**（行数正常故长期漏检，1.18.91/1.18.94 同样错位、非升级引入），改为新增 `fetch_fund_hold_direct` **直取东财 + 按上游字段名映射**并加「代码列必须全 6 位数字」错位守卫，列名沿用 akshare 原 9 名（只追加 `持股占流通股比`）、`fund_hold` 升为双源（`em_zlsj_direct` 主 + akshare 备）；`get_fund_hold()` 默认 `基金持仓` 因全量翻页 11 页 ≈17s 超路由 12s 上限而**无参调用必挂**，新增 `_FUND_HOLD_ROUTE_TIMEOUT=40.0` 单独放宽；退役已永久失效的 `index_news_sentiment` 旧主源（chinascope 返回 HTML 非 JSON），`legu_activity` 提为主源 + 补同花顺 `ths_distribution` 跨上游备源。数据源 101→102（去重 36→37），新增 7 项回归测试，测试 457 passed |
| v3.3.15 | 2026-09-14 | 修复静默数据丢失与降级链伪装：`fetch_fund_hold_data` 季度末非法日期（默认调用恒空表）、8 个 fetch_fn 静默吞异常改 raise（SmartRouter 可降级并计健康度）、`fetch_index_news_sentiment` SSL 兜底注入点错位（urllib 层改 requests 层）、`industry_comparison` 双源同属东财 push2 族实际同生共死（补同花顺 `ths_flow` 跨上游兜底）；为 6 个单源类型补非主源上游独立备源，数据源 94→101（去重 31→36），测试 450 passed |
| v3.3.14 | 2026-09-14 | 修复备源降级链与代理误路由：akshare 备源新增 `period` 归一化（`day→daily`，修复传 eltdx 风格周期时降级必断的 `KeyError`）；`tradex/__init__.py` 补设 `NO_PROXY=*`（原仅 pop 环境变量，`requests` 会回退读注册表代理导致国内数据源走 Clash）；`_bar_sort_key` 收窄异常捕获并告警（原静默 `return 0.0` 污染回测首行）。为 `company_info`/`financial_stmt`/`valuation`/`industry_data` 四类补非东财独立备源（巨潮/新浪/eltdx/同花顺），数据源 90→94；新增 20 项回归测试，测试 418 passed |
| v3.3.13 | 2026-09-14 | 上游依赖升级：eltdx 2.0.2→3.2.2（major，Rust 重写内核 native wheel；`bars.all`→`bars.get(all_pages=True)`、`helpers.adjusted_kline`→`bars.get(adjust=)` 两处迁移）+ akshare 1.18.91→1.18.94；修复 `fetch_full_kline` 跨页乱序（回测数据 bug，6389 根 K 线重排为严格升序）；修复 `start_dashboard.bat` 解释器探测错误与端口未传递，新增 `--check` 依赖检查模式；测试 398 passed |
| v3.3.12 | 2026-09-08 | HTTP 网关原生支持（无需 supergateway）：`python -m tradex --http` 单端口提供 Streamable HTTP(/mcp 新标准, Dify/LangChain 新版) + legacy SSE(/sse 兼容) + /health 健康检查；修正 docker-compose healthcheck(原探测 /mcp 在 SSE 下必 404)；README 文档化 HTTP 模式 |
| v3.3.11 | 2026-09-07 | P2 技术债全清：指标算法单一实现收敛、EMA 种子对齐通达信、K线缓冲保留、eltdx period 归一化、代理清理改连接级、腾讯前缀防重、版本比较语义化、市场代码识别鲁棒、server lifespan 公共配置、删死代码；测试 390 passed |
| v3.3.10 | 2026-09-07 | 双源合一(astock_signals 并入本仓 src/ 为主源,独立仓退役) + P0/P1 修复：Sortino 下行波动率算法修正、可转债/沪市债券交易所判定修正、金融主营构成 symbol 前缀修正、逐笔方向字段修正(eltdx side, 原误读 buy_or_sell 全标 sell)、SmartRouter 故障源半开探测自愈、东财限流加锁、SSL 替换加锁、注册幂等加固 + 仓库卫生(删根 src 空壳/cn-financial-mcp 僵尸/串仓测试, pytest 合跑修复) |
| v3.3.9 | 2026-08-18 | 全局直连(import去代理) + 同花顺4接口/东财slist板块归属/东财限流防封/实时涨跌家数/行业涨幅/通达信本地数据 + 本地数据MCP工具2个(get_local_kline/get_local_minute)，工具数 127→129 |
| v3.3.8 | 2026-08-14 | eltdx 2.0 第二梯队 B 级接入：分类行情(涨幅榜/成交额榜)、交易日判定、历史开盘撮合、股本变动、特殊涨跌停扫描、F10通用入口(估值/题材/总评/盈利预测/排名/治理/增减持/主营/公告/新闻)，工具数 121→127 |
| v3.3.7 | 2026-08-14 | eltdx 2.0 第一梯队 S+A 级接入：常驻连接管理器(eltdx_stream.py，游标增量轮询实现准实时五档盘口)+五档盘口+证券代码表+历史分时+买卖强度+逐笔+开盘撮合+全量K线+复权K线+全景档案+21项短线指标+批量财务+特殊涨跌停+F10财报分红资讯北向+个股题材+题材成分股+竞价汇总，工具数 101→121，数据类型 40→64 |
| v3.3.6 | 2026-08-14 | 上游依赖升级：eltdx 1.2.0→2.0.2（major breaking，get_quote→helpers.full_quotes 迁移）+ akshare 1.18.81→1.18.91；盘后量能成交额修复（push2his 直连，f57=成交额）；工具数断言 100→101 |
| v3.3.5 | 2026-08-14 | 盘后复盘「量能对比」成交额数据源修复：fetch_index_daily_amount 主源改 push2his.eastmoney.com 直连（含成交额），腾讯降级备源 |
| v3.3.4 | 2026-08-13 | 盘后复盘「量能对比」数据源：新增 get_index_volume_compare 工具（上证/深证/创业板近 N 日成交额序列） |
| v3.3.3 | 2026-08-13 | 撤销逐笔盘后数据方案（eltdx 逐笔收盘后无窗口、1/5分钟线占盘过大），fetch_tick_data 回退纯实时，盘后复盘提示词 v3.6 去逐笔要求 |
| v3.3.2 | 2026-08-13 | 盘后复盘「多个数据缺失」根因修复 5 类：指数代码解析错乱（symbol.py 白名单）、慢源挂起卡死 MCP（SmartRouter timeout 12s）、北向资金停更检测、逐笔盘后回退 TickStore、自动化配置漂移 |
| v3.3.1 | 2026-08-03 | 修复 4 个问题：get_money_flow 代理问题（4只股票全通）、get_financial_calendar date过滤失效（各源独立过滤）、search_news 稳定性增强（个股新闻降级全市场源）、get_sector_fund_flow 字段解析（新浪备源7字段）；P0修复：get_realtime_quote 外围行情代理失败、get_company_announcements 公告过滤 |
| v3.3.0 | 2026-08-03 | 新增 9 个新闻资讯数据源（百度经济日历/交易提醒/热搜、期货新闻、新浪财经、东财人气榜、雪球热度、机构持仓、指数情绪）+ 同花顺问财可选集成，新增 9 个 MCP 工具（get_market_sentiment/get_futures_news/get_hot_rank/get_hot_keywords/get_xueqiu_hot/get_fund_hold/get_hot_search/get_wencai_query/get_wencai_news），工具数 90→99，数据类型 27→38 |
| v3.1.4 | 2026-08-02 | 修复 P1/P2 遗留：eltdx realtime_quote 改用 get_quote()（QuoteSnapshot 完整字段含涨跌幅/内外盘）；装饰器死代码修复（list_all_tools/health_check 改用 mcp 实例，不再误报 degraded）；源名标识修正（etf_data/cb_data akshare→astock_signals）；_client_lock 改用 threading.Lock；architecture.md 文档修正；ETF 列名重复 warning 修复 |
| v3.1.3 | 2026-08-02 | 修复 P0 bug：SmartRouter 参数名不匹配导致 eltdx 主源永远失败降级 akshare（行情类 fetcher 统一兼容 symbol/code）；修复 eltdx KlineBar 字段映射（date→time, volume→volume_lots），新增 17 个参数归一化回归测试 |
| v3.1.2 | 2026-08-02 | 删除 v2.x 遗留 `src/` 目录（data_manager/market_analyzer，依赖已删的 eltdx_provider），清理 diagnostics.py 的 `src.astock_signals` fallback 死代码 |
| v3.1.1 | 2026-08-02 | 修复 pytest warning：注册 `network` marker（根 + tradex pyproject.toml），消除 tradex/tests 4 个 PytestUnknownMarkWarning |
| v3.1.0 | 2026-08-02 | 项目改名 tradex-hub（包名 cn_financial_mcp→tradex），astock_signals 独立成包 v1.1.0，SmartRouter 全量覆盖 25 数据类型 34 源，data_sources 数据源层接入，新增数据源看板（`python -m tradex.dashboard` 端口 8765 + MCP 工具 `get_data_source_dashboard`），工具数 88→89 |
| v3.0.0 | 2026-08-01 | 架构大重构:版本号单一事实来源、僵尸模块激活、em_client 合并、signal_data 拆分、MCP_HOST 安全加固 |
| v2.5.1 | 2026-08-01 | eltdx 新增 K 线数据接口（KlineBar/KlineData + get_kline()）；.coverage 加入 .gitignore |
| v2.5.0 | 2026-07-26 | 智能决策中台升级：新增15个量化计算工具（技术指标6/绩效2/信号3/因子2/选股2），工具数65→80，从数据中台升级为智能决策中台 |
| v2.4.0 | 2026-07-23 | 修复6个核心接口（东财风控封禁），新增em_client防封客户端，ETF/可转债改为延迟导入，工具数61→65 |
| v2.3.2 | 2026-06-29 | 清理 workbuddy 遗留路径，新增涨停板分析模块 |
| v2.3.1 | 2026-06-24 | 文档修复与版本管理优化（4处不一致修正） |
| v2.3.0 | 2026-06-24 | 新增 ETF/可转债/智能路由/Tick存储/WebSocket 5 个模块，4 个新 MCP 工具（ETF实时+K线/可转债实时+价值分析），61 工具就绪；router 扩展至 17 命令 |
| v2.2.0 | 2026-06-23 | 新增 astock_signals 4 个模块（北向资金/个股资金流/龙虎榜/行业对比），4 个新 MCP 工具，57 工具就绪 |
| v2.1.0 | 2026-06-22 | 新增 A 股信号数据模块：涨停归因/解禁日历/概念归属/一致预期/技术指标，6 个新 MCP 工具，53 工具就绪 |
| v2.0.1 | 2026-06-17 | 集成 eltdx 5 个工具；修复 pyproject.toml hatchling 配置；47 工具全跑通 |
| v2.0.0 | 2026-06-04 | eltdx 通达信协议集成（原 `eltdx_provider.py`） |
| v1.0.0 | 2026-06-02 | 全市场综合分析引擎 |
| v0.1.0 | 2026-06-01 | 项目初始化；集成 tradex |

---

## 许可

Apache-2.0 License

---

## 感谢
原作者
**郭良勇 (wolfjkd)** — A股T0日内交易员
