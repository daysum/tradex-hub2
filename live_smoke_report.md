# tradex-hub 全量真实网络冒烟报告

生成时间：2026-09-20 00:13:17

> 离线单测的响应是 mock 的，不走 TLS 握手与真实节流，
> 因此本报告是防封改造后唯一的有效端到端证据。

判定口径：`OK` 取到数据 ｜ `EMPTY` 无异常但结果空 ｜ `FAIL` 抛异常（**回归**）
｜ `ENV` 缺本地数据/API Key ｜ `UPSTREAM` 上游节点或第三方库自身问题
｜ `AKSHARE` akshare 自身传输层被拒 ｜ `SKIP` 参数不足
｜ 单项失败会重试 1 次（上游偶发 503/连接重置很常见）

## 〇、数据源主机连通性

〇、数据源主机连通性: 共 14 项 | 取到数据 12 | 失败 1 | 连通但异常 1

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| FAIL | 东财 push2 | - | 0.13 | ConnectionError: Failed to perform, curl: (56) Connection closed abruptly. See https://curl.se/libcurl/c/li |
| OK | 东财 datacenter | - | 0.16 | HTTP 200 len=2285 |
| OK | 东财 emweb(F10) | - | 0.37 | HTTP 200 len=1718 |
| OK | 东财 push2delay | - | 0.22 | HTTP 200 len=191 |
| OK | 东财 push2ex | - | 0.14 | HTTP 200 len=613 |
| OK | 东财 push2his | - | 0.16 | HTTP 200 len=132 |
| OK | 东财 slist | - | 0.29 | HTTP 200 len=349830 |
| OK | 同花顺 10jqka | - | 0.24 | HTTP 200 len=47295 |
| OK | 同花顺 hexin | - | 0.08 | HTTP 200 len=3993 |
| WARN | 巨潮 cninfo | - | 0.16 | HTTP 500 len=32579 |
| OK | 新浪 sina | - | 0.22 | HTTP 200 len=4303 |
| OK | 腾讯 qt.gtimg | - | 0.12 | HTTP 200 len=552 |
| OK | 财联社 cls | - | 0.12 | HTTP 200 len=39 |
| OK | 问财 openapi | - | 0.08 | HTTP 401 len=75 |

## 一、数据源层

一、数据源层: 共 102 项 | 取到数据 88 | 环境依赖 4 | 上游问题 4 | akshare传输 6

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| UPSTREAM | company_info | akshare | 1.24 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— 同上（akshare 自身传输层） |
| UPSTREAM | company_info | cninfo | 13.52 | KeyError: 'count'（已重试 1 次） —— cninfo 备源转调 akshare.stock_profile_cninfo（打 webapi.cninfo.com.cn），akshare 内部 KeyError 'count'，不经本项目传输层 |
| UPSTREAM | fund_hold | akshare_fund_hold | 1.00 | RuntimeError: akshare 的 stock_report_fund_hold 列已错位（上游字段顺序改版 + akshare 按位置硬编码列名），hold 已改由 fetch_fund_hold_direct 直取东财；此处不再作为兜底，以免返回错标数据（已重试 1 次） —— 该源已按设计停用（akshare 列错位），改由 em_zlsj_direct 直取东财 |
| UPSTREAM | industry_comparison | em_push2 | 11.84 | RuntimeError: 东财返回空数据（已重试 1 次） —— push2.eastmoney.com 节点故障：对该主机所有指纹（含不伪装）均 'Connection closed abruptly'，命令行 curl 亦 HTTP 000；同域 push2delay/push2his/push2ex/datacenter/slist 全部 200 |
| OK | adjusted_kline | eltdx | 0.72 | DataFrame(5, 7) |
| OK | all_a_shares | eltdx | 1.13 | DataFrame(5574, 3) |
| OK | auction_data | eltdx | 1.09 | DataFrame(1, 6) |
| OK | baidu_economic_calendar | akshare_baidu_economic | 1.74 | DataFrame(81, 8) |
| OK | baidu_economic_calendar | akshare_report_time | 0.34 | DataFrame(6, 7) |
| OK | baidu_trade_notify | akshare_baidu_notify | 0.33 | DataFrame(4, 12) |
| OK | baidu_trade_notify | akshare_tfp | 0.88 | DataFrame(913, 9) |
| OK | call_auction | eltdx | 0.01 | AuctionSeries(exchange='sh', market_id=1, code='600519', trading_date=None, mode_or_select |
| OK | capital_changes | eltdx | 0.01 | DataFrame(45, 4) |
| OK | category_quotes | eltdx | 0.58 | DataFrame(5, 9) |
| OK | category_quotes | tencent | 24.93 | DataFrame(5, 9) |
| OK | cb_data | astock_signals | 0.19 | dict(5) keys=['source', 'data_type', 'symbol', 'days'] |
| OK | cninfo_announcement | cninfo_direct | 0.66 | DataFrame(5, 6) |
| OK | company_news | eltdx | 0.27 | DataFrame(20, 7) |
| OK | concept_attribution | em_push2delay | 36.86 | dict(6) keys=['code', 'name', 'concepts', 'industries'] |
| OK | dividend_financing | eltdx | 0.26 | DataFrame(56, 11) |
| OK | dragon_tiger | em_datacenter | 4.19 | dict(7) keys=['symbol', 'source', 'trade_date', 'look_back_days'] |
| OK | dragon_tiger | akshare | 1.81 | dict(7) keys=['symbol', 'source', 'trade_date', 'look_back_days'] |
| OK | etf_data | astock_signals | 0.01 | dict(6) keys=['source', 'data_type', 'symbol', 'period'] |
| OK | f10_extra | eltdx | 0.36 | DataFrame(1, 2) |
| OK | f10_profile | eltdx | 0.74 | dict(4) keys=['profile_resp', 'topics_resp', 'diag_resp', 'code'] |
| OK | finance_batch | eltdx | 0.01 | DataFrame(1, 8) |
| OK | finance_report | eltdx | 0.39 | DataFrame(102, 71) |
| AKSHARE | financial_stmt | akshare | 1.39 | TypeError: 'NoneType' object is not subscriptable（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | financial_stmt | sina | 1.06 | DataFrame(103, 83) |
| OK | full_kline | eltdx | 0.25 | DataFrame(6008, 7) |
| OK | fund_flow | em_push2 | 1.73 | dict(6) keys=['symbol', 'source', 'date', 'realtime'] |
| OK | fund_flow | akshare | 1.01 | dict(6) keys=['symbol', 'source', 'date', 'realtime'] |
| OK | fund_hold | em_zlsj_direct | 14.10 | DataFrame(5311, 10) |
| OK | futures_news | akshare_futures_news | 0.26 | DataFrame(10, 2) |
| OK | global_market_quote | tencent_http | 0.12 | DataFrame(17, 11) |
| OK | historical_kline | eltdx | 0.02 | DataFrame(5, 7) |
| AKSHARE | historical_kline | akshare | 1.36 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | hot_money | ths_editorial | 0.15 | dict(4) keys=['data', 'error', 'count', 'source'] |
| AKSHARE | hot_rank | akshare_hot_rank | 1.56 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | hot_rank | ths_hot | 0.18 | DataFrame(100, 8) |
| OK | hot_search | akshare_hot_search | 0.35 | DataFrame(12, 3) |
| OK | hot_search | ths_hot | 0.13 | DataFrame(100, 8) |
| OK | hot_stocks | akshare | 0.17 | DataFrame(78, 16) |
| OK | index_daily_amount | akshare | 0.29 | list[5] |
| OK | index_news_sentiment | legu_activity | 0.35 | DataFrame(12, 2) |
| OK | index_news_sentiment | ths_distribution | 0.20 | DataFrame(11, 3) |
| AKSHARE | industry_comparison | akshare | 1.24 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | industry_comparison | ths_flow | 0.32 | dict(4) keys=['source', 'date', 'code', 'industries'] |
| AKSHARE | industry_data | akshare | 12.41 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | industry_data | ths | 0.18 | DataFrame(90, 2) |
| OK | industry_quotes | em_push2 | 6.81 | DataFrame(100, 6) |
| OK | limit_up_board | em_push2_clist | 6.86 | dict(4) keys=['type', 'data', 'count', 'source'] |
| ENV | local_kline | tdx_local | 0.00 | DataFrame(0, 0) —— 需本机通达信 vipdoc 目录（tdxdir） |
| ENV | local_minute | tdx_local | 0.00 | DataFrame(0, 0) —— 需本机通达信 vipdoc 目录（tdxdir） |
| OK | lockup_expiry | em_datacenter | 1.24 | dict(5) keys=['code', 'trade_date', 'history', 'upcoming'] |
| OK | macro_data | akshare | 0.18 | DataFrame(82, 9) |
| OK | market_breadth | em_push2ex | 1.04 | DataFrame(1, 5) |
| OK | market_overview | akshare | 2.11 | DataFrame(562, 11) |
| OK | market_overview | tencent_http | 0.11 | DataFrame(7, 6) |
| OK | minute_aux | eltdx | 0.02 | DataFrame(240, 5) |
| OK | minute_data | eltdx | 0.02 | DataFrame(240, 4) |
| AKSHARE | minute_data | akshare | 1.26 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | minute_history | eltdx | 0.02 | DataFrame(240, 4) |
| OK | news_data | em_news_direct | 0.46 | DataFrame(30, 6) |
| OK | news_data | akshare | 0.15 | DataFrame(10, 6) |
| OK | northbound | ths_hsgt | 0.19 | dict(6) keys=['source', 'date', 'realtime', 'history'] |
| OK | northbound | akshare | 1.40 | DataFrame(2756, 13) |
| OK | northbound_holding | eltdx | 0.25 | DataFrame(8, 6) |
| OK | opening_match | eltdx | 0.07 | DataFrame(1, 8) |
| OK | opening_match_history | eltdx | 0.08 | DataFrame(1, 8) |
| OK | profit_forecast | akshare | 2.35 | dict(10) keys=['symbol', 'source', 'summary', 'forecasts'] |
| OK | profit_forecast | tencent_http | 0.27 | dict(6) keys=['symbol', 'source', 'price', 'pe_ttm'] |
| OK | realtime_quote | eltdx | 0.04 | DataFrame(1, 13) |
| OK | realtime_quote | akshare | 27.70 | DataFrame(5564, 14) |
| OK | realtime_quote | tencent_http | 0.15 | DataFrame(1, 13) |
| OK | security_codes | eltdx | 1.24 | DataFrame(52594, 4) |
| OK | shortline_indicators | eltdx | 1.79 | DataFrame(1, 17) |
| OK | sina_finance_news | sina_direct | 0.36 | DataFrame(5, 4) |
| OK | special_limits | eltdx | 0.01 | DataFrame(1, 3) |
| OK | special_limits_scan | eltdx | 11.81 | DataFrame(781, 3) |
| OK | stock_boards | em_slist | 0.45 | DataFrame(28, 4) |
| OK | stock_profile | eltdx | 0.59 | DataFrame(1, 11) |
| OK | stock_topics | eltdx | 0.49 | DataFrame(20, 4) |
| OK | telegraph_news | cls_telegraph | 0.30 | DataFrame(5, 4) |
| OK | ths_eps_forecast | ths | 0.30 | DataFrame(3, 6) |
| OK | ths_hot_list | ths | 0.20 | DataFrame(100, 8) |
| OK | ths_hot_reason | ths | 0.36 | DataFrame(78, 13) |
| OK | ths_limit_up_minute | ths | 0.32 | dict(9) keys=['total', 'name', 'header', 'point_list'] |
| OK | ths_limit_up_pool | ths | 0.51 | DataFrame(77, 12) |
| OK | ths_market_breadth | ths | 0.53 | dict(5) keys=['up', 'down', 'flat', 'limit_up'] |
| OK | ths_turnover_minute | ths | 0.31 | dict(9) keys=['total', 'name', 'header', 'point_list'] |
| OK | ths_up_down_distribution | ths | 0.35 | DataFrame(11, 3) |
| OK | tick_data | eltdx | 0.01 | TradePage(exchange='sh', market_id=1, code='600519', start=0, request_count=5, ticks=(Trad |
| OK | today_ticks | eltdx | 0.01 | DataFrame(5, 8) |
| OK | topic_stocks | eltdx | 0.76 | DataFrame(20, 8) |
| OK | trading_day | eltdx | 0.01 | DataFrame(1, 3) |
| OK | valuation | akshare | 0.24 | DataFrame(366, 2) |
| OK | valuation | eltdx | 0.33 | DataFrame(1, 2) |
| ENV | wencai_news | iwencai_openapi | 0.00 | DataFrame(0, 0) —— 需环境变量 IWENCAI_API_KEY |
| ENV | wencai_query | pywencai | 0.00 | DataFrame(0, 0) —— 需 pywencai 依赖 + 网络 |
| OK | xueqiu_hot | akshare_xueqiu_hot | 12.61 | DataFrame(5645, 4) |
| OK | xueqiu_hot | ths_hot | 0.42 | DataFrame(100, 8) |

## 二、MCP 工具层

二、MCP 工具层: 共 129 项 | 取到数据 117 | 跳过 12

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| OK | analyze_industry_comparison | - | 6.39 | {"error": true, "message": "无法获取公司信息: All sources for 'company_info' failed: akshare: Conn |
| OK | analyze_market_overview | - | 4.45 | {"market_indices": {"success": true, "data": {"shanghai": {"name": "上证指数", "price": 3911.8 |
| OK | analyze_stock_comprehensive | - | 6.38 | {"symbol": "600519", "realtime_quote": {"success": true, "data": {"code": "600519", "name" |
| OK | analyze_technical | - | 0.04 | {"symbol": "600519", "data_points": 30, "last_date": "2026-09-18", "last_close": 1257.12,  |
| SKIP | calculate_atr | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| SKIP | calculate_boll | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_factor_score | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stock_codes', 'factor_data'] |
| SKIP | calculate_kdj | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| SKIP | calculate_ma_ema | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_macd | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_performance | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['equity_curve'] |
| SKIP | calculate_rsi | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| OK | eltdx_get_adjusted_kline | - | 0.02 | {"status": "success", "data": {"code": "600519", "period": "day", "adjust": "qfq", "latenc |
| OK | eltdx_get_auction | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 12.4, "point_count": 124, " |
| OK | eltdx_get_auction_data | - | 0.15 | {"status": "success", "data": {"code": "sh600519", "latency_ms": 143.8, "open_price": 1262 |
| OK | eltdx_get_buy_sell_strength | - | 0.05 | {"status": "success", "data": {"code": "600519", "latency_ms": 25.5, "point_count": 240, " |
| OK | eltdx_get_capital_changes | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 14.5, "count": 45, "changes |
| OK | eltdx_get_category_quotes | - | 0.02 | {"status": "success", "data": {"category": "沪深a股", "sort_by": "涨幅", "latency_ms": 17.1, "c |
| OK | eltdx_get_company_news | - | 0.29 | {"status": "success", "data": {"code": "600519", "latency_ms": 285.0, "columns": ["T004",  |
| OK | eltdx_get_depth | - | 0.94 | {"status": "success", "data": {"code": "600519", "latency_ms": 934.4, "last_price": 1257.1 |
| OK | eltdx_get_dividend_financing | - | 0.25 | {"status": "success", "data": {"code": "600519", "latency_ms": 240.8, "columns": ["rq", "T |
| OK | eltdx_get_f10 | - | 0.77 | {"status": "success", "data": {"code": "600519", "latency_ms": 770.2, "profile": {"T035":  |
| OK | eltdx_get_f10_extra | - | 0.37 | {"status": "success", "data": {"entry": "valuation", "code": "600519", "latency_ms": 366.3 |
| OK | eltdx_get_finance_batch | - | 0.02 | {"status": "success", "data": {"latency_ms": 13.9, "count": 1, "finances": [{"code": "sh60 |
| OK | eltdx_get_finance_report | - | 0.49 | {"status": "success", "data": {"code": "600519", "report_type": "zcfzb", "latency_ms": 474 |
| OK | eltdx_get_full_kline | - | 0.81 | {"status": "success", "data": {"code": "600519", "period": "day", "latency_ms": 272.4, "ba |
| OK | eltdx_get_kline | - | 0.03 | {"status": "success", "data": {"code": "600519", "period": "day", "latency_ms": 28.2, "bar |
| OK | eltdx_get_minute_history | - | 0.06 | {"status": "success", "data": {"code": "600519", "trading_date": "20260918", "latency_ms": |
| OK | eltdx_get_minutes | - | 0.06 | {"status": "success", "data": {"code": "600519", "latency_ms": 27.5, "point_count": 240, " |
| OK | eltdx_get_northbound_holding | - | 0.26 | {"status": "success", "data": {"code": "600519", "latency_ms": 252.3, "columns": ["N001",  |
| OK | eltdx_get_opening_match | - | 0.07 | {"status": "success", "data": {"code": "600519", "latency_ms": 69.8, "time": "09:25", "ope |
| OK | eltdx_get_opening_match_history | - | 0.08 | {"status": "success", "data": {"code": "600519", "trading_date": "20260918", "latency_ms": |
| OK | eltdx_get_security_codes | - | 1.27 | {"status": "success", "data": {"market": "all", "category": "沪深a股", "latency_ms": 1239.7,  |
| OK | eltdx_get_shortline_indicators | - | 0.65 | {"status": "success", "data": {"latency_ms": 650.6, "count": 1, "indicators": [{"代码": "sh6 |
| OK | eltdx_get_special_limits | - | 0.02 | {"status": "success", "data": {"latency_ms": 14.2, "count": 1, "limits": [{"code": "sz0000 |
| OK | eltdx_get_special_limits_scan | - | 12.02 | {"status": "error", "error": "special limits scan query failed: Exclusive source 'eltdx' f |
| OK | eltdx_get_stock_profile | - | 0.74 | {"status": "success", "data": {"latency_ms": 732.1, "count": 1, "profiles": [{"code": "sh6 |
| OK | eltdx_get_stock_topics | - | 0.54 | {"status": "success", "data": {"code": "600519", "latency_ms": 534.7, "count": 20, "topics |
| OK | eltdx_get_ticks | - | 0.11 | {"status": "success", "data": {"code": "600519", "date": "20260918", "latency_ms": 104.9,  |
| OK | eltdx_get_today_ticks | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 15.2, "tick_count": 5, "tic |
| OK | eltdx_get_topic_stocks | - | 0.81 | {"status": "success", "data": {"seed_code": "600519", "topic_name": "", "latency_ms": 794. |
| OK | eltdx_get_trading_day | - | 0.02 | {"status": "success", "data": {"server_date": "2026-09-18", "server_datetime": "2026-09-20 |
| OK | eltdx_stream_health | - | 0.00 | {"status": "success", "data": {"started": true, "subscribed_count": 0, "push_buffer": {}}} |
| SKIP | generate_trading_signal | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| OK | get_analyst_rating | - | 6.15 | {"error": true, "message": "获取分析师评级失败 (600519): All sources for 'valuation' failed: akshar |
| OK | get_balance_sheet | - | 0.00 | [{"报告期":"2026中报","货币资金(亿)":535.19,"应收账款(亿)":0.01,"预付款项(亿)":0.16,"存货(亿)":613.17,"流动资产合计(亿)" |
| OK | get_board_sentiment | - | 12.01 | {"error": true, "message": "获取打板情绪数据失败: Exclusive source 'em_push2_clist' for 'limit_up_bo |
| OK | get_bond_yield_curve | - | 0.25 | {"error": true, "message": "获取国债收益率失败: All sources for 'macro_data' failed: akshare: Value |
| OK | get_cache_stats | - | 0.00 | {"size": 4, "max_size": 5000, "hits": 0, "misses": 8, "hit_rate": 0.0, "file_size": 15, "f |
| OK | get_cash_flow_statement | - | 0.00 | [{"报告期":"2026二季度","经营活动现金流入(亿)":530.65,"经营活动现金流出(亿)":92.84,"经营活动现金流净额(亿)":437.81,"投资活动现金流入 |
| OK | get_cb_realtime_data | - | 2.08 | {"source": "AKShare (东财 bond_zh_cov)", "data_type": "cb_realtime", "timestamp": "2026-09-2 |
| OK | get_cb_value_analysis_data | - | 0.16 | {"source": "AKShare (东财)", "data_type": "cb_value_analysis", "symbol": "600519", "days": 5 |
| OK | get_company_announcements | - | 0.70 | [{"标题":"贵州茅台2026年半年度报告","发布日期":"2026-08-15","公告类型":"","PDF链接":"http:\/\/static.cninfo.com. |
| OK | get_company_info | - | 18.38 | {"error": true, "message": "获取公司信息失败 (600519): All sources for 'realtime_quote' failed: el |
| OK | get_company_profile | - | 0.00 | {"error": true, "message": "获取公司主营构成失败 (600519): All sources for 'company_info' failed: ", |
| OK | get_competitors | - | 6.45 | {"error": true, "message": "获取竞争对手列表失败 (600519, 白酒): All sources for 'industry_data' faile |
| OK | get_concept_attribution | - | 12.01 | {"error": true, "message": "获取概念归属失败: All sources for 'concept_attribution' failed: em_pus |
| OK | get_concept_list | - | 0.00 | {"error": true, "message": "获取概念板块列表失败: 所有数据源均不可用", "tool": "get_concept_list"} |
| OK | get_data_source_dashboard | - | 7.34 | {"timestamp": "2026-09-20T00:12:12", "summary": {"total_tools": 129, "data_types": 78, "so |
| OK | get_data_source_health | - | 0.00 | {"status": "ok", "count": 102, "data_sources": [{"name": "historical_kline:eltdx", "score" |
| OK | get_dividend_data | - | 0.00 | [{"公告日期":"2026-06-22T00:00:00.000","送股":0,"转增":0,"派息":280.242,"进度":"实施","除权除息日":"2026-06-2 |
| OK | get_dragon_tiger | - | 3.32 | [{"序号":1,"代码":"000002","名称":"万科A","上榜日":"2026-09-18T00:00:00.000","解读":"2家机构买入，成功率43.55%", |
| OK | get_dragon_tiger_signal | - | 0.00 | {"error": true, "message": "获取龙虎榜数据失败: All sources for 'dragon_tiger' failed: em_datacente |
| OK | get_etf_kline_data | - | 0.00 | {"source": "AKShare (东财)", "data_type": "etf_kline", "symbol": "600519", "period": "day",  |
| OK | get_etf_realtime_data | - | 12.02 | {"error": true, "message": "获取ETF实时行情失败: All sources for 'etf_data' failed: astock_signals |
| OK | get_factor_catalog | - | 0.00 | {"success": true, "categories": {"value": "价值类因子", "growth": "成长类因子", "quality": "质量类因子",  |
| OK | get_financial_calendar | - | 0.41 | [{"日期":"2026-09-18T00:00:00.000","时间":"01:00","地区":"美国","事件":"美国9月17日10年期TIPS竞拍-高收益率(%)"," |
| OK | get_financial_indicators | - | 0.00 | [{"日期":"1998-12-31T00:00:00.000","摊薄每股收益(元)":null,"扣除非经常性损益后的每股收益(元)":null,"每股净资产_调整前(元)": |
| OK | get_financial_line_item | - | 0.00 | [{"报告期":"2026二季度","营业收入(亿)":367.94},{"报告期":"2026一季度","营业收入(亿)":539.09},{"报告期":"2025四季度","营 |
| OK | get_fund_flow_signal | - | 5.08 | {"symbol": "600519", "source": "东财 push2 (直连)", "date": "2026-09-18", "realtime": [{"time" |
| OK | get_fund_hold | - | 0.00 | {"error": true, "message": "获取基金持仓数据失败: All sources for 'fund_hold' failed: em_zlsj_direct |
| OK | get_futures_news | - | 0.00 | {"error": true, "message": "获取期货新闻失败: All sources for 'futures_news' failed: akshare_futur |
| OK | get_fx_rate | - | 0.31 | [{"货币对":"USD\/CNY"},{"货币对":"EUR\/CNY"},{"货币对":"100JPY\/CNY"},{"货币对":"HKD\/CNY"},{"货币对":"GB |
| OK | get_global_market_quote | - | 0.16 | [] |
| OK | get_growth_rates | - | 0.00 | [{"日期":"1998-12-31T00:00:00.000","主营业务收入增长率(%)":null,"净利润增长率(%)":null,"净资产增长率(%)":null,"总资 |
| OK | get_historical_price | - | 0.02 | [{"日期":"2026-04-28 15:00:00+08:00","开盘":1402.0,"最高":1409.39,"最低":1400.11,"收盘":1405.0,"成交量" |
| OK | get_hot_keywords | - | 0.16 | {"error": true, "message": "获取热门关键词失败: All sources for 'hot_rank' failed: akshare_hot_rank |
| OK | get_hot_rank | - | 0.48 | [{"当前排名":1,"代码":"SH601091","股票名称":"C沈鼓","最新价":57.77,"涨跌额":102.680398,"涨跌幅":177.74},{"当前排名" |
| OK | get_hot_search | - | 0.26 | [{"排名":1,"代码":"601091","名称":"沈鼓集团","人气值":"332421.0","涨幅%":177.7404,"排名变化":0,"概念标签":["煤化工概念 |
| OK | get_hot_stocks | - | 0.27 | {"date": "20260918", "stocks": [{"code": "603230", "name": "内蒙新华", "change_pct": 10.041, " |
| OK | get_income_statement | - | 0.00 | [{"报告期":"2026二季度","营业总收入(亿)":375.75,"营业收入(亿)":367.94,"营业成本(亿)":39.53,"销售费用(亿)":16.01,"管理费用 |
| OK | get_index_volume_compare | - | 2.89 | {"sh000001": {"name": "上证指数", "series": [{"date": "2026-09-14", "volume_hand": 458888916.0 |
| OK | get_industry_comparison_signal | - | 6.99 | {"source": "THS stock_fund_flow_industry(即时)", "date": "20260918", "code": "600519", "indu |
| OK | get_industry_list | - | 0.00 | {"error": true, "message": "获取行业板块列表失败: 所有数据源均不可用", "tool": "get_industry_list"} |
| OK | get_industry_pe | - | 0.00 | {"error": true, "message": "获取行业历史行情失败 (白酒): All sources for 'industry_data' failed: ", "t |
| OK | get_industry_stocks | - | 0.00 | {"error": true, "message": "获取行业成分股失败 (白酒): All sources for 'industry_data' failed: ", "to |
| OK | get_insider_trading | - | 2.35 | [] |
| OK | get_institutional_holdings | - | 0.01 | [{"截止日期":"2026-06-30T00:00:00.000","公告日期":"2026-08-15T00:00:00.000","编号":1,"股东名称":"中国贵州茅台酒 |
| OK | get_intraday_data | - | 0.05 | {"code": "600519", "point_count": 240, "points": [{"time": "09:31", "price": 1259.18, "avg |
| OK | get_limit_up_board | - | 5.59 | {"type": "zt", "data": [], "count": 0, "source": "eastmoney_clist"} |
| OK | get_limit_up_down | - | 0.20 | [{"序号":1,"代码":"001376","名称":"百通能源","涨跌幅":9.991312027,"最新价":12.66,"成交额":147380124,"流通市值":27 |
| OK | get_limit_up_insight | - | 0.16 | {"data": [], "error": null, "count": 0, "source": "同花顺 limit_up_detail"} |
| OK | get_local_kline | - | 0.00 | {"error": true, "message": "本地无 600519 日线数据（通达信目录: 未找到，检查是否下载过该股数据）", "tool": "get_local_k |
| OK | get_local_minute | - | 0.00 | {"error": true, "message": "本地无 600519 day分钟线数据（通达信未下载分钟线）", "tool": "get_local_minute"} |
| OK | get_lockup_expiry | - | 0.91 | {"code": "600519", "trade_date": "20260918", "history": [], "upcoming": [], "upcoming_erro |
| OK | get_macro_cpi | - | 0.00 | [{"月份":"2026年08月份","全国-当月":100.8,"全国-同比增长":0.8,"全国-环比增长":0.4,"全国-累计":100.9,"城市-当月":100.8," |
| OK | get_macro_gdp | - | 0.00 | [{"季度":"2026年第1-2季度","国内生产总值-绝对值":695704.0,"国内生产总值-同比增长":4.7,"第一产业-绝对值":31521.8,"第一产业-同比增长 |
| OK | get_macro_money_supply | - | 0.00 | [{"月份":"2026年08月份","货币和准货币(M2)-数量(亿元)":3568083.6000000001,"货币和准货币(M2)-同比增长":7.5,"货币和准货币(M2 |
| OK | get_macro_pmi | - | 0.00 | [{"月份":"2026年08月份","制造业-指数":49.8,"制造业-同比增长":0.8097166,"非制造业-指数":49.0,"非制造业-同比增长":-2.584493 |
| OK | get_margin_trading | - | 1.06 | {"error": true, "message": "融资融券数据为空 (600519)", "tool": "get_margin_trading"} |
| OK | get_market_capitalization | - | 0.04 | [{"代码":"600519","最新价":1257.12,"涨跌幅":-0.7782285435,"成交量":24890,"成交额":3135849216.0}] |
| OK | get_market_overview | - | 2.40 | [{"代码":"sh000001","名称":"上证指数","最新价":3911.8714,"涨跌额":36.267,"涨跌幅":0.936,"昨收":3875.6044,"今开" |
| OK | get_market_sentiment | - | 0.29 | [{"item":"上涨","value":3937.0},{"item":"涨停","value":79.0},{"item":"真实涨停","value":73.0},{"it |
| OK | get_money_flow | - | 1.88 | [{"时间":"2026-09-18 13:21","主力净流入":-104081053.0,"小单净流入":-152325.0,"中单净流入":104233383.0,"大单净流 |
| OK | get_north_bound_flow | - | 0.18 | [{"状态":"已停更","说明":"北向资金数值连续多日完全一致，疑似已停更\/冻结。沪深港通自 2024-08-19 起停止披露北向实时净买入，该数值仅供参考，不应作为交易依据 |
| OK | get_northbound_flow_signal | - | 0.33 | {"source": "同花顺 hsgtApi", "date": "2026-09-18", "realtime": {"data_points": [{"time": "14: |
| OK | get_per_share_data | - | 0.00 | [{"日期":"1998-12-31T00:00:00.000","摊薄每股收益(元)":null,"扣除非经常性损益后的每股收益(元)":null,"每股净资产_调整前(元)": |
| OK | get_profit_forecast | - | 2.10 | {"symbol": "600519", "source": "同花顺 analyst consensus", "summary": "截至2026-09-18，6个月以内共有 4 |
| OK | get_realtime_quote | - | 0.04 | [{"代码":"600519","最新价":1257.12,"昨收":1266.98,"今开":1262.99,"最高":1265.88,"最低":1256.1,"涨跌额":-9. |
| OK | get_screening_conditions | - | 0.00 | {"success": true, "categories": {"fundamental": "基本面条件", "technical": "技术面条件", "capital":  |
| OK | get_sector_fund_flow | - | 0.18 | [{"板块":"玻璃行业","涨跌幅":-0.663094639,"涨跌额":-0.1178947368,"总成交额":26401819337,"总成交量":1003647974, |
| OK | get_segments_revenue | - | 0.00 | [{"股票代码":"600519","报告日期":"2026-06-30T00:00:00.000","分类类型":"按产品分类","主营构成":"茅台酒","主营收入":7772 |
| OK | get_stock_list | - | 12.11 | {"error": true, "message": "获取股票列表失败: All sources for 'realtime_quote' failed: eltdx: Runt |
| OK | get_stock_news | - | 0.55 | [{"新闻标题":"贵州茅台(<em>600519<\/em>.SH)：2<em>0<\/em>2<em>6<\/em>年中报净利润为44<em>5<\/em>.<em>1<\/e |
| OK | get_technical_indicator | - | 0.00 | {"error": true, "message": "不支持的指标 'MACD'。可选: ['close_50_sma', 'close_200_sma', 'close_10_ |
| OK | get_telegraph_news | - | 0.39 | [{"标题":"尼泊尔泥石流灾害遇难人数升至1436人","内容":"【尼泊尔泥石流灾害遇难人数升至1436人】财联社9月19日电，据尼泊尔警方公布的最新数据，截至当地时间9月19 |
| OK | get_valuation_metrics | - | 1.41 | {"error": true, "message": "获取估值指标失败 (600519): '<' not supported between instances of 'dat |
| OK | get_wencai_news | - | 0.00 | [] |
| OK | get_wencai_query | - | 0.01 | [] |
| OK | get_xueqiu_hot | - | 0.31 | [{"排名":1,"代码":"601091","名称":"沈鼓集团","人气值":"332421.0","涨幅%":177.7404,"排名变化":0,"概念标签":["煤化工概念 |
| OK | health_check | - | 0.00 | {"status": "degraded", "issues": ["4 个数据源不健康"], "akshare": {"available": true, "version":  |
| OK | list_all_tools | - | 0.00 | {"status": "ok", "total": 129, "tools": ["analyze_industry_comparison", "analyze_market_ov |
| OK | list_performance_metrics | - | 0.00 | {"success": true, "categories": {"return": "收益类指标", "risk": "风险类指标", "risk_adjusted": "风险调 |
| OK | list_technical_indicators | - | 0.00 | [{"name": "close_50_sma", "description": "50-day Simple Moving Average (均线)"}, {"name": "c |
| SKIP | scan_stocks_for_signals | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stocks_data'] |
| SKIP | screen_stocks | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stocks_data', 'conditions'] |
| OK | search_news | - | 0.45 | [{"新闻标题":"贵州茅台(<em>600519<\/em>.SH)：2<em>0<\/em>2<em>6<\/em>年中报净利润为44<em>5<\/em>.<em>1<\/e |
| OK | search_stock | - | 0.00 | [{"code":"600519","name":"贵州茅台"}] |
| SKIP | validate_signal_quality | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes', 'signal_idx'] |

## 结论

**无失败项**：所有可调用接口均未抛异常。
