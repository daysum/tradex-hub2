# tradex-hub 全量真实网络冒烟报告

生成时间：2026-09-26 09:33:54

> 离线单测的响应是 mock 的，不走 TLS 握手与真实节流，
> 因此本报告是防封改造后唯一的有效端到端证据。

判定口径：`OK` 取到数据 ｜ `EMPTY` 无异常但结果空 ｜ `FAIL` 抛异常（**回归**）
｜ `ENV` 缺本地数据/API Key ｜ `UPSTREAM` 上游节点或第三方库自身问题
｜ `AKSHARE` akshare 自身传输层被拒 ｜ `SKIP` 参数不足
｜ `UNSUPPORTED` 源明确不支持该 endpoint（覆盖缺口，非回归）
｜ 单项失败会重试 1 次（上游偶发 503/连接重置很常见）

## 〇、数据源主机连通性

〇、数据源主机连通性: 共 14 项 | 取到数据 10 | 失败 3 | 连通但异常 1

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| FAIL | 东财 push2 | - | 0.16 | ConnectionError: Failed to perform, curl: (56) Connection closed abruptly. See https://curl.se/libcurl/c/li |
| FAIL | 东财 push2delay | - | 0.21 | ConnectionError: Failed to perform, curl: (56) Connection closed abruptly. See https://curl.se/libcurl/c/li |
| FAIL | 东财 push2his | - | 0.14 | ConnectionError: Failed to perform, curl: (56) Connection closed abruptly. See https://curl.se/libcurl/c/li |
| OK | 东财 datacenter | - | 0.20 | HTTP 200 len=2285 |
| OK | 东财 emweb(F10) | - | 0.61 | HTTP 200 len=1718 |
| OK | 东财 push2ex | - | 0.30 | HTTP 200 len=604 |
| OK | 东财 slist | - | 0.43 | HTTP 200 len=349889 |
| OK | 同花顺 10jqka | - | 0.17 | HTTP 200 len=53304 |
| OK | 同花顺 hexin | - | 0.16 | HTTP 200 len=3993 |
| WARN | 巨潮 cninfo | - | 0.38 | HTTP 500 len=32579 |
| OK | 新浪 sina | - | 0.26 | HTTP 200 len=4275 |
| OK | 腾讯 qt.gtimg | - | 0.08 | HTTP 200 len=550 |
| OK | 财联社 cls | - | 0.20 | HTTP 200 len=39 |
| OK | 问财 openapi | - | 0.10 | HTTP 401 len=75 |

## 一、数据源层

一、数据源层: 共 115 项 | 取到数据 98 | 空结果 1 | 失败 2 | 环境依赖 2 | 上游问题 3 | akshare传输 6 | 源不支持该endpoint 3

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| FAIL | industry_quotes | em_push2 | 19.89 | RuntimeError: fetch_industry_quotes all sources failed（已重试 1 次） |
| FAIL | xueqiu_hot | akshare_xueqiu_hot | 1.84 | NetworkError: Failed to request Xueqiu hot rank endpoint: Expecting value: line 1 column 1 (char 0)（已重试 1 次） |
| EMPTY | recent_minute | eltdx | 0.02 | DataFrame(0, 4) |
| UPSTREAM | company_info | akshare | 1.51 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— 同上（akshare 自身传输层） |
| UPSTREAM | fund_hold | akshare_fund_hold | 1.00 | RuntimeError: akshare 的 stock_report_fund_hold 列已错位（上游字段顺序改版 + akshare 按位置硬编码列名），hold 已改由 fetch_fund_hold_direct 直取东财；此处不再作为兜底，以免返回错标数据（已重试 1 次） —— 该源已按设计停用（akshare 列错位），改由 em_zlsj_direct 直取东财 |
| UPSTREAM | industry_comparison | em_push2 | 14.36 | RuntimeError: 东财返回空数据（已重试 1 次） —— push2.eastmoney.com 节点故障：对该主机所有指纹（含不伪装）均 'Connection closed abruptly'，命令行 curl 亦 HTTP 000；同域 push2delay/push2his/push2ex/datacenter/slist 全部 200 |
| OK | adjusted_kline | eltdx | 0.72 | DataFrame(5, 7) |
| OK | all_a_shares | eltdx | 1.50 | DataFrame(5577, 3) |
| OK | auction_data | eltdx | 0.98 | DataFrame(1, 6) |
| OK | baidu_economic_calendar | akshare_baidu_economic | 2.00 | DataFrame(81, 8) |
| OK | baidu_economic_calendar | akshare_report_time | 0.35 | DataFrame(5, 7) |
| OK | baidu_trade_notify | akshare_baidu_notify | 0.36 | DataFrame(4, 12) |
| OK | baidu_trade_notify | akshare_tfp | 1.24 | DataFrame(918, 9) |
| OK | call_auction | eltdx | 0.01 | AuctionSeries(exchange='sh', market_id=1, code='600519', trading_date=None, mode_or_select |
| OK | capital_changes | eltdx | 0.01 | DataFrame(45, 4) |
| OK | category_quotes | eltdx | 0.86 | DataFrame(5, 9) |
| OK | category_quotes | tencent | 31.21 | DataFrame(5, 9) |
| OK | cb_data | astock_signals | 0.22 | dict(5) keys=['source', 'data_type', 'symbol', 'days'] |
| OK | cninfo_announcement | cninfo_direct | 0.66 | DataFrame(5, 6) |
| OK | company_info | cninfo | 0.36 | DataFrame(1, 26) |
| OK | company_news | eltdx | 0.30 | DataFrame(20, 7) |
| OK | concept_attribution | em_push2delay | 5.45 | dict(5) keys=['code', 'concepts', 'industries', 'regions'] |
| OK | dividend_financing | eltdx | 0.28 | DataFrame(56, 11) |
| OK | dragon_tiger | em_datacenter | 3.58 | dict(7) keys=['symbol', 'source', 'trade_date', 'look_back_days'] |
| OK | dragon_tiger | akshare | 2.05 | dict(7) keys=['symbol', 'source', 'trade_date', 'look_back_days'] |
| OK | etf_data | astock_signals | 0.00 | dict(6) keys=['source', 'data_type', 'symbol', 'period'] |
| OK | f10_extra | eltdx | 0.35 | DataFrame(1, 2) |
| OK | f10_profile | eltdx | 0.78 | dict(4) keys=['profile_resp', 'topics_resp', 'diag_resp', 'code'] |
| OK | finance_batch | eltdx | 0.01 | DataFrame(1, 8) |
| OK | finance_report | eltdx | 0.45 | DataFrame(102, 71) |
| AKSHARE | financial_stmt | akshare | 1.74 | TypeError: 'NoneType' object is not subscriptable（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | financial_stmt | sina | 1.38 | DataFrame(103, 83) |
| OK | full_kline | eltdx | 0.40 | DataFrame(6012, 7) |
| OK | fund_flow | em_push2 | 1.97 | dict(6) keys=['symbol', 'source', 'date', 'realtime'] |
| OK | fund_flow | akshare | 1.27 | dict(6) keys=['symbol', 'source', 'date', 'realtime'] |
| OK | fund_hold | em_zlsj_direct | 14.88 | DataFrame(5311, 10) |
| OK | futures_news | akshare_futures_news | 0.32 | DataFrame(100, 2) |
| OK | global_market_quote | tencent_http | 0.11 | DataFrame(17, 11) |
| OK | historical_kline | eltdx | 0.02 | DataFrame(5, 7) |
| OK | historical_kline | akshare | 5.70 | DataFrame(14, 12) |
| OK | hot_money | ths_editorial | 0.19 | dict(4) keys=['data', 'error', 'count', 'source'] |
| OK | hot_rank | akshare_hot_rank | 2.06 | DataFrame(100, 6) |
| OK | hot_rank | ths_hot | 0.30 | DataFrame(100, 8) |
| OK | hot_search | akshare_hot_search | 0.43 | DataFrame(12, 3) |
| OK | hot_search | ths_hot | 0.18 | DataFrame(100, 8) |
| OK | hot_stocks | akshare | 0.32 | DataFrame(78, 16) |
| OK | index_daily_amount | akshare | 0.18 | list[5] |
| OK | index_news_sentiment | legu_activity | 0.40 | DataFrame(12, 2) |
| OK | index_news_sentiment | ths_distribution | 0.10 | DataFrame(11, 3) |
| AKSHARE | industry_comparison | akshare | 1.00 | RuntimeError: industry_comparison: 东财行业资金流请求重试耗尽（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | industry_comparison | ths_flow | 0.38 | dict(4) keys=['source', 'date', 'code', 'industries'] |
| AKSHARE | industry_data[board_concept_name_em] | akshare | 34.39 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | industry_data[board_concept_name_em] | ths | 0.00 | DataFrame(375, 2) |
| OK | industry_data[board_concept_name_ths] | akshare | 2.91 | DataFrame(375, 2) |
| OK | industry_data[board_concept_name_ths] | ths | 0.00 | DataFrame(375, 2) |
| AKSHARE | industry_data[board_industry_cons_em] | akshare | 1.00 | RuntimeError: board_industry_cons_em: 板块列表(m:90+t:2) 请求重试耗尽（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| UNSUPPORTED | industry_data[board_industry_cons_em] | ths | 1.00 | ValueError: ths 备源不支持 industry_data endpoint='board_industry_cons_em'（支持: board_industry_name_em/ths, board_concept_name_em/ths）（已重试 1 次） |
| OK | industry_data[board_industry_hist_em] | akshare | 0.27 | DataFrame(14, 11) |
| UNSUPPORTED | industry_data[board_industry_hist_em] | ths | 1.00 | ValueError: ths 备源不支持 industry_data endpoint='board_industry_hist_em'（支持: board_industry_name_em/ths, board_concept_name_em/ths）（已重试 1 次） |
| AKSHARE | industry_data[board_industry_name_em] | akshare | 11.79 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| OK | industry_data[board_industry_name_em] | ths | 0.00 | DataFrame(90, 2) |
| OK | industry_data[board_industry_name_ths] | akshare | 0.28 | DataFrame(90, 2) |
| OK | industry_data[board_industry_name_ths] | ths | 0.00 | DataFrame(90, 2) |
| AKSHARE | industry_data[sector_fund_flow_rank] | akshare | 1.00 | RuntimeError: sector_fund_flow_rank: 东财请求重试耗尽（已重试 1 次） —— akshare 自身传输层被上游拒连（不走本项目统一防封层） |
| UNSUPPORTED | industry_data[sector_fund_flow_rank] | ths | 1.00 | ValueError: ths 备源不支持 industry_data endpoint='sector_fund_flow_rank'（支持: board_industry_name_em/ths, board_concept_name_em/ths）（已重试 1 次） |
| OK | limit_up_board | em_push2_clist | 0.00 | dict(4) keys=['type', 'data', 'count', 'source'] |
| OK | local_kline | tdx_local | 0.01 | DataFrame(1589, 7) |
| ENV | local_minute | tdx_local | 0.00 | DataFrame(0, 0) —— 需本机通达信 vipdoc 目录（tdxdir） |
| OK | lockup_expiry | em_datacenter | 1.48 | dict(5) keys=['code', 'trade_date', 'history', 'upcoming'] |
| OK | macro_data | akshare | 0.24 | DataFrame(82, 9) |
| OK | market_breadth | em_push2ex | 1.18 | DataFrame(1, 5) |
| OK | market_overview | akshare | 2.73 | DataFrame(562, 11) |
| OK | market_overview | tencent_http | 0.17 | DataFrame(7, 6) |
| OK | minute_aux | eltdx | 0.01 | DataFrame(240, 5) |
| OK | minute_data | eltdx | 0.03 | DataFrame(240, 4) |
| OK | minute_data | akshare | 0.81 | DataFrame(4331, 4) |
| OK | minute_history | eltdx | 0.02 | DataFrame(240, 4) |
| OK | news_data | em_news_direct | 1.16 | DataFrame(30, 6) |
| OK | news_data | akshare | 0.15 | DataFrame(10, 6) |
| OK | northbound | ths_hsgt | 0.37 | dict(6) keys=['source', 'date', 'realtime', 'history'] |
| OK | northbound | akshare | 1.81 | DataFrame(2760, 13) |
| OK | northbound_holding | eltdx | 0.25 | DataFrame(8, 6) |
| OK | opening_match | eltdx | 0.09 | DataFrame(1, 8) |
| OK | opening_match_history | eltdx | 0.11 | DataFrame(1, 8) |
| OK | profit_forecast | akshare | 2.02 | dict(10) keys=['symbol', 'source', 'summary', 'forecasts'] |
| OK | profit_forecast | tencent_http | 0.25 | dict(6) keys=['symbol', 'source', 'price', 'pe_ttm'] |
| OK | realtime_quote | eltdx | 0.04 | DataFrame(1, 13) |
| OK | realtime_quote | akshare | 37.82 | DataFrame(5568, 14) |
| OK | realtime_quote | tencent_http | 0.09 | DataFrame(1, 13) |
| OK | security_codes | eltdx | 1.54 | DataFrame(52163, 4) |
| OK | shortline_indicators | eltdx | 2.23 | DataFrame(1, 17) |
| OK | sina_finance_news | sina_direct | 0.34 | DataFrame(5, 4) |
| OK | special_limits | eltdx | 0.01 | DataFrame(1, 3) |
| OK | special_limits_scan | eltdx | 10.07 | DataFrame(789, 3) |
| OK | stock_boards | em_slist | 0.51 | DataFrame(28, 4) |
| OK | stock_profile | eltdx | 0.78 | DataFrame(1, 11) |
| OK | stock_topics | eltdx | 0.49 | DataFrame(19, 4) |
| OK | telegraph_news | cls_telegraph | 0.25 | DataFrame(5, 4) |
| OK | ths_eps_forecast | ths | 0.41 | DataFrame(3, 6) |
| OK | ths_hot_list | ths | 0.25 | DataFrame(100, 8) |
| OK | ths_hot_reason | ths | 0.40 | DataFrame(78, 13) |
| OK | ths_limit_up_minute | ths | 0.14 | dict(9) keys=['total', 'name', 'header', 'point_list'] |
| OK | ths_limit_up_pool | ths | 0.53 | DataFrame(77, 12) |
| OK | ths_market_breadth | ths | 0.54 | dict(5) keys=['up', 'down', 'flat', 'limit_up'] |
| OK | ths_turnover_minute | ths | 0.34 | dict(9) keys=['total', 'name', 'header', 'point_list'] |
| OK | ths_up_down_distribution | ths | 0.31 | DataFrame(11, 3) |
| OK | tick_data | eltdx | 0.01 | TradePage(exchange='sh', market_id=1, code='600519', start=0, request_count=5, ticks=(Trad |
| OK | today_ticks | eltdx | 0.01 | DataFrame(5, 8) |
| OK | topic_stocks | eltdx | 0.81 | DataFrame(20, 8) |
| OK | trading_day | eltdx | 0.01 | DataFrame(1, 3) |
| OK | valuation | akshare | 0.33 | DataFrame(365, 2) |
| OK | valuation | eltdx | 0.32 | DataFrame(1, 2) |
| OK | wencai_news | iwencai_openapi | 0.64 | DataFrame(5, 5) |
| ENV | wencai_query | pywencai | 9.36 | RuntimeError: pywencai 未取得有效响应（内部重试全部失败）。常见原因：本机出口 IP 被同花顺问财封禁(403)、Node.js 不可用、或 pywencai 内置的 hexin-v 指纹已失效（已重试 1 次） —— 需 pywencai 依赖 + 网络 |
| OK | xueqiu_hot | ths_hot | 0.23 | DataFrame(100, 8) |

## 二、MCP 工具层

二、MCP 工具层: 共 130 项 | 取到数据 117 | 空结果 1 | 跳过 12

| 状态 | 数据源/工具 | 来源 | 耗时(s) | 结果/错误 |
|------|------|------|------|------|
| EMPTY | eltdx_get_recent_minutes | - | 0.02 | {"status": "no_data", "message": "no recent minute data for 600519 on 20260918"} |
| OK | analyze_industry_comparison | - | 6.56 | {"error": true, "message": "获取行业数据失败: '板块名称'", "tool": "analyze_industry_comparison"} |
| OK | analyze_market_overview | - | 12.00 | {"market_indices": {"success": true, "data": {"shanghai": {"name": "上证指数", "price": 3888.3 |
| OK | analyze_stock_comprehensive | - | 11.40 | {"symbol": "600519", "realtime_quote": {"success": true, "data": {"code": "600519", "name" |
| OK | analyze_technical | - | 0.07 | {"symbol": "600519", "data_points": 30, "last_date": "2026-09-24", "last_close": 1237.0, " |
| SKIP | calculate_atr | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| SKIP | calculate_boll | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_factor_score | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stock_codes', 'factor_data'] |
| SKIP | calculate_kdj | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| SKIP | calculate_ma_ema | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_macd | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| SKIP | calculate_performance | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['equity_curve'] |
| SKIP | calculate_rsi | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes'] |
| OK | eltdx_get_adjusted_kline | - | 0.02 | {"status": "success", "data": {"code": "600519", "period": "day", "period_canonical": "day |
| OK | eltdx_get_auction | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 13.3, "point_count": 145, " |
| OK | eltdx_get_auction_data | - | 0.14 | {"status": "success", "data": {"code": "sh600519", "latency_ms": 134.7, "open_price": 1250 |
| OK | eltdx_get_buy_sell_strength | - | 0.05 | {"status": "success", "data": {"code": "600519", "latency_ms": 14.5, "point_count": 240, " |
| OK | eltdx_get_capital_changes | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 12.6, "count": 45, "changes |
| OK | eltdx_get_category_quotes | - | 0.02 | {"status": "success", "data": {"category": "沪深a股", "sort_by": "涨幅", "latency_ms": 18.8, "c |
| OK | eltdx_get_company_news | - | 0.29 | {"status": "success", "data": {"code": "600519", "latency_ms": 279.5, "columns": ["T004",  |
| OK | eltdx_get_depth | - | 1.09 | {"status": "success", "data": {"code": "600519", "latency_ms": 1086.0, "last_price": 1237. |
| OK | eltdx_get_dividend_financing | - | 0.26 | {"status": "success", "data": {"code": "600519", "latency_ms": 251.9, "columns": ["rq", "T |
| OK | eltdx_get_f10 | - | 0.83 | {"status": "success", "data": {"code": "600519", "latency_ms": 826.1, "profile": {"T035":  |
| OK | eltdx_get_f10_extra | - | 0.37 | {"status": "success", "data": {"entry": "valuation", "code": "600519", "latency_ms": 366.0 |
| OK | eltdx_get_finance_batch | - | 0.02 | {"status": "success", "data": {"latency_ms": 14.7, "count": 1, "finances": [{"code": "sh60 |
| OK | eltdx_get_finance_report | - | 0.45 | {"status": "success", "data": {"code": "600519", "report_type": "zcfzb", "latency_ms": 421 |
| OK | eltdx_get_full_kline | - | 2.18 | {"status": "success", "data": {"code": "600519", "period": "day", "period_canonical": "day |
| OK | eltdx_get_kline | - | 0.02 | {"status": "success", "data": {"code": "600519", "period": "day", "period_canonical": "day |
| OK | eltdx_get_minute_history | - | 0.06 | {"status": "success", "data": {"code": "600519", "trading_date": "20260918", "latency_ms": |
| OK | eltdx_get_minutes | - | 0.08 | {"status": "success", "data": {"code": "600519", "latency_ms": 22.9, "point_count": 240, " |
| OK | eltdx_get_northbound_holding | - | 0.27 | {"status": "success", "data": {"code": "600519", "latency_ms": 264.8, "columns": ["N001",  |
| OK | eltdx_get_opening_match | - | 0.09 | {"status": "success", "data": {"code": "600519", "latency_ms": 91.7, "time": "09:25", "ope |
| OK | eltdx_get_opening_match_history | - | 0.11 | {"status": "success", "data": {"code": "600519", "trading_date": "20260918", "latency_ms": |
| OK | eltdx_get_security_codes | - | 1.49 | {"status": "success", "data": {"market": "all", "category": "沪深a股", "kind": "", "latency_m |
| OK | eltdx_get_shortline_indicators | - | 0.92 | {"status": "success", "data": {"latency_ms": 914.6, "count": 1, "indicators": [{"代码": "sh6 |
| OK | eltdx_get_special_limits | - | 0.01 | {"status": "success", "data": {"latency_ms": 11.9, "count": 1, "limits": [{"code": "sz0000 |
| OK | eltdx_get_special_limits_scan | - | 10.29 | {"status": "success", "data": {"latency_ms": 10151.5, "count": 789, "limits": [{"code": "s |
| OK | eltdx_get_stock_profile | - | 0.08 | {"status": "success", "data": {"latency_ms": 72.7, "count": 1, "profiles": [{"code": "sh60 |
| OK | eltdx_get_stock_topics | - | 0.54 | {"status": "success", "data": {"code": "600519", "latency_ms": 530.1, "count": 19, "topics |
| OK | eltdx_get_ticks | - | 0.05 | {"status": "success", "data": {"code": "600519", "date": "20260918", "latency_ms": 51.1, " |
| OK | eltdx_get_today_ticks | - | 0.02 | {"status": "success", "data": {"code": "600519", "latency_ms": 13.2, "tick_count": 5, "tic |
| OK | eltdx_get_topic_stocks | - | 0.78 | {"status": "success", "data": {"seed_code": "600519", "topic_name": "", "latency_ms": 774. |
| OK | eltdx_get_trading_day | - | 0.02 | {"status": "success", "data": {"server_date": "2026-09-24", "server_datetime": "2026-09-26 |
| OK | eltdx_stream_health | - | 0.00 | {"status": "success", "data": {"started": true, "subscribed_count": 0, "push_buffer": {}}} |
| SKIP | generate_trading_signal | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['highs', 'lows', 'closes'] |
| OK | get_analyst_rating | - | 0.35 | [{"证券代码":"600519","证券简称":"贵州茅台","发布日期":"2023-08-17","研究机构简称":"南京证券","研究员名称":"宋芳","投资评级":"买 |
| OK | get_balance_sheet | - | 10.54 | [{"报告期":"2026中报","货币资金(亿)":535.19,"应收账款(亿)":0.01,"预付款项(亿)":0.16,"存货(亿)":613.17,"流动资产合计(亿)" |
| OK | get_board_sentiment | - | 0.00 | {"zt_count": 0, "zb_count": 0, "dt_count": 0, "break_rate": 0, "max_consecutive_limit_up": |
| OK | get_bond_yield_curve | - | 0.33 | {"error": true, "message": "获取国债收益率失败: All sources for 'macro_data' failed: akshare: Value |
| OK | get_cache_stats | - | 0.00 | {"size": 6, "max_size": 5000, "hits": 0, "misses": 8, "hit_rate": 0.0, "file_size": 1, "fi |
| OK | get_cash_flow_statement | - | 9.49 | [{"报告期":"2026二季度","经营活动现金流入(亿)":530.65,"经营活动现金流出(亿)":92.84,"经营活动现金流净额(亿)":437.81,"投资活动现金流入 |
| OK | get_cb_realtime_data | - | 2.53 | {"source": "AKShare (东财 bond_zh_cov)", "data_type": "cb_realtime", "timestamp": "2026-09-2 |
| OK | get_cb_value_analysis_data | - | 0.20 | {"source": "AKShare (东财)", "data_type": "cb_value_analysis", "symbol": "600519", "days": 5 |
| OK | get_company_announcements | - | 0.75 | [{"标题":"贵州茅台2026年半年度报告","发布日期":"2026-08-15","公告类型":"","PDF链接":"http:\/\/static.cninfo.com. |
| OK | get_company_info | - | 0.33 | {"贵州茅台酒股份有限公司": "Kweichow Moutai Co., Ltd."} |
| OK | get_company_profile | - | 0.53 | [{"股票代码":"600519","主营业务":"茅台酒及系列酒的生产与销售。","产品类型":"茅台酒、其他系列酒","产品名称":"茅台酒、其他系列酒","经营范围":"茅台 |
| OK | get_competitors | - | 0.00 | {"error": true, "message": "获取竞争对手列表失败 (600519, 白酒): All sources for 'industry_data' faile |
| OK | get_concept_attribution | - | 0.00 | {"code": "600519", "concepts": [], "industries": [], "regions": [], "error": "无法获取地域板块列表"} |
| OK | get_concept_list | - | 0.00 | {"error": true, "message": "获取概念板块列表失败: 所有数据源均不可用", "tool": "get_concept_list"} |
| OK | get_data_source_dashboard | - | 1.33 | {"timestamp": "2026-09-26T09:32:01", "summary": {"total_tools": 130, "data_types": 79, "so |
| OK | get_data_source_health | - | 0.00 | {"status": "ok", "count": 103, "data_sources": [{"name": "realtime_quote:eltdx", "score":  |
| OK | get_dividend_data | - | 0.45 | [{"公告日期":"2026-06-22T00:00:00.000","送股":0,"转增":0,"派息":280.242,"进度":"实施","除权除息日":"2026-06-2 |
| OK | get_dragon_tiger | - | 1.71 | [{"序号":1,"代码":"000002","名称":"万科A","上榜日":"2026-09-22T00:00:00.000","解读":"主力做T，成功率15.13%","收 |
| OK | get_dragon_tiger_signal | - | 0.00 | {"error": true, "message": "获取龙虎榜数据失败: All sources for 'dragon_tiger' failed: em_datacente |
| OK | get_etf_kline_data | - | 0.00 | {"source": "AKShare (东财)", "data_type": "etf_kline", "symbol": "600519", "period": "day",  |
| OK | get_etf_realtime_data | - | 5.68 | {"source": "AKShare (东财)", "data_type": "etf_realtime", "timestamp": "2026-09-26T09:32:04. |
| OK | get_factor_catalog | - | 0.00 | {"success": true, "categories": {"value": "价值类因子", "growth": "成长类因子", "quality": "质量类因子",  |
| OK | get_financial_calendar | - | 1.85 | [{"日期":"2026-09-18T00:00:00.000","时间":"01:00","地区":"美国","事件":"美国9月17日10年期TIPS竞拍-高收益率(%)"," |
| OK | get_financial_indicators | - | 6.05 | [{"日期":"1998-12-31T00:00:00.000","摊薄每股收益(元)":null,"扣除非经常性损益后的每股收益(元)":null,"每股净资产_调整前(元)": |
| OK | get_financial_line_item | - | 8.92 | [{"报告期":"2026二季度","营业收入(亿)":367.94},{"报告期":"2026一季度","营业收入(亿)":539.09},{"报告期":"2025四季度","营 |
| OK | get_fund_flow_signal | - | 1.98 | {"symbol": "600519", "source": "东财 push2 (直连)", "date": "2026-09-18", "realtime": [{"time" |
| OK | get_fund_hold | - | 0.00 | {"error": true, "message": "获取基金持仓数据失败: All sources for 'fund_hold' failed: em_zlsj_direct |
| OK | get_futures_news | - | 0.00 | {"error": true, "message": "获取期货新闻失败: All sources for 'futures_news' failed: akshare_futur |
| OK | get_fx_rate | - | 0.28 | [{"货币对":"USD\/CNY"},{"货币对":"EUR\/CNY"},{"货币对":"100JPY\/CNY"},{"货币对":"HKD\/CNY"},{"货币对":"GB |
| OK | get_global_market_quote | - | 0.15 | [] |
| OK | get_growth_rates | - | 6.23 | [{"日期":"1998-12-31T00:00:00.000","主营业务收入增长率(%)":null,"净利润增长率(%)":null,"净资产增长率(%)":null,"总资 |
| OK | get_historical_price | - | 0.02 | [{"日期":"2026-05-07 15:00","开盘":1375.0,"最高":1388.0,"最低":1370.01,"收盘":1371.05,"成交量":40461.47 |
| OK | get_hot_keywords | - | 0.19 | {"error": true, "message": "获取热门关键词失败: All sources for 'hot_rank' failed: akshare_hot_rank |
| OK | get_hot_rank | - | 1.51 | [{"当前排名":1,"代码":"SZ002413","股票名称":"雷科防务","最新价":8.97,"涨跌额":0.902382,"涨跌幅":10.06},{"当前排名":2, |
| OK | get_hot_search | - | 0.18 | [{"排名":1,"代码":"000592","名称":"平潭发展","人气值":"95156.0","涨幅%":10.0251,"排名变化":0,"概念标签":["福建自贸区", |
| OK | get_hot_stocks | - | 0.36 | {"date": "20260918", "stocks": [{"code": "603230", "name": "内蒙新华", "change_pct": 10.041, " |
| OK | get_income_statement | - | 5.29 | [{"报告期":"2026二季度","营业总收入(亿)":375.75,"营业收入(亿)":367.94,"营业成本(亿)":39.53,"销售费用(亿)":16.01,"管理费用 |
| OK | get_index_volume_compare | - | 6.55 | {"sh000001": {"name": "上证指数", "series": [{"date": "2026-09-18", "volume_hand": 485712507.0 |
| OK | get_industry_comparison_signal | - | 11.97 | {"source": "THS stock_fund_flow_industry(即时)", "date": "20260918", "code": "600519", "indu |
| OK | get_industry_list | - | 0.00 | {"error": true, "message": "获取行业板块列表失败: 所有数据源均不可用", "tool": "get_industry_list"} |
| OK | get_industry_pe | - | 0.00 | {"error": true, "message": "获取行业历史行情失败 (白酒): All sources for 'industry_data' failed: ", "t |
| OK | get_industry_stocks | - | 0.00 | {"error": true, "message": "获取行业成分股失败 (白酒): All sources for 'industry_data' failed: ", "to |
| OK | get_insider_trading | - | 0.44 | {"error": true, "message": "获取内部交易数据失败 (600519): All sources for 'macro_data' failed: aksh |
| OK | get_institutional_holdings | - | 1.38 | [{"截止日期":"2026-06-30T00:00:00.000","公告日期":"2026-08-15T00:00:00.000","编号":1,"股东名称":"中国贵州茅台酒 |
| OK | get_intraday_data | - | 0.07 | {"code": "600519", "point_count": 240, "points": [{"time": "09:31", "price": 1249.99, "avg |
| OK | get_limit_up_board | - | 0.00 | {"type": "zt", "data": [], "count": 0, "source": "eastmoney_clist"} |
| OK | get_limit_up_down | - | 0.36 | [{"序号":1,"代码":"000498","名称":"山东路桥","涨跌幅":9.9221792221,"最新价":5.65,"成交额":314302752,"流通市值":81 |
| OK | get_limit_up_insight | - | 0.23 | {"data": [], "error": null, "count": 0, "source": "同花顺 limit_up_detail"} |
| OK | get_local_kline | - | 0.02 | {"code": "600519", "tdx_dir": "D:\\通达信金融终端(开心果交易版)V2026", "rows": 5, "data": [{"date": "20 |
| OK | get_local_minute | - | 0.00 | {"error": true, "message": "本地无 600519 day分钟线数据（通达信未下载分钟线）", "tool": "get_local_minute"} |
| OK | get_lockup_expiry | - | 0.57 | {"code": "600519", "trade_date": "20260918", "history": [], "upcoming": [], "upcoming_erro |
| OK | get_macro_cpi | - | 0.33 | [{"月份":"2026年08月份","全国-当月":100.8,"全国-同比增长":0.8,"全国-环比增长":0.4,"全国-累计":100.9,"城市-当月":100.8," |
| OK | get_macro_gdp | - | 0.26 | [{"季度":"2026年第1-2季度","国内生产总值-绝对值":695704.0,"国内生产总值-同比增长":4.7,"第一产业-绝对值":31521.8,"第一产业-同比增长 |
| OK | get_macro_money_supply | - | 0.36 | [{"月份":"2026年08月份","货币和准货币(M2)-数量(亿元)":3568083.6000000001,"货币和准货币(M2)-同比增长":7.5,"货币和准货币(M2 |
| OK | get_macro_pmi | - | 0.24 | [{"月份":"2026年08月份","制造业-指数":49.8,"制造业-同比增长":0.8097166,"非制造业-指数":49.0,"非制造业-同比增长":-2.584493 |
| OK | get_margin_trading | - | 1.41 | {"error": true, "message": "融资融券数据为空 (600519)", "tool": "get_margin_trading"} |
| OK | get_market_capitalization | - | 0.59 | [{"代码":"600519","最新价":1237.0,"涨跌幅":-1.1380710335,"成交量":31239,"成交额":3867310848.0}] |
| OK | get_market_overview | - | 2.53 | [{"代码":"sh000001","名称":"上证指数","最新价":3888.3738,"涨跌额":-48.146,"涨跌幅":-1.223,"昨收":3936.5199,"今 |
| OK | get_market_sentiment | - | 0.38 | [{"item":"上涨","value":1084.0},{"item":"涨停","value":52.0},{"item":"真实涨停","value":45.0},{"it |
| OK | get_money_flow | - | 1.85 | [{"时间":"2026-09-24 13:21","主力净流入":-529120695.0,"小单净流入":-243286.0,"中单净流入":529363983.0,"大单净流 |
| OK | get_north_bound_flow | - | 0.38 | [{"状态":"已停更","说明":"北向资金数值连续多日完全一致，疑似已停更\/冻结。沪深港通自 2024-08-19 起停止披露北向实时净买入，该数值仅供参考，不应作为交易依据 |
| OK | get_northbound_flow_signal | - | 0.16 | {"source": "同花顺 hsgtApi", "date": "2026-09-18", "realtime": {"data_points": [{"time": "14: |
| OK | get_per_share_data | - | 6.56 | [{"日期":"1998-12-31T00:00:00.000","摊薄每股收益(元)":null,"扣除非经常性损益后的每股收益(元)":null,"每股净资产_调整前(元)": |
| OK | get_profit_forecast | - | 2.49 | {"symbol": "600519", "source": "同花顺 analyst consensus", "summary": "截至2026-09-25，6个月以内共有 4 |
| OK | get_realtime_quote | - | 0.05 | [{"代码":"600519","最新价":1237.0,"昨收":1251.24,"今开":1250.01,"最高":1256.13,"最低":1231.05,"涨跌额":-14 |
| OK | get_screening_conditions | - | 0.00 | {"success": true, "categories": {"fundamental": "基本面条件", "technical": "技术面条件", "capital":  |
| OK | get_sector_fund_flow | - | 0.18 | [{"板块":"玻璃行业","涨跌幅":-2.32386682,"涨跌额":-0.4147368421,"总成交额":17402360459,"总成交量":1008720579," |
| OK | get_segments_revenue | - | 0.30 | [{"股票代码":"600519","报告日期":"2026-06-30T00:00:00.000","分类类型":"按产品分类","主营构成":"茅台酒","主营收入":7772 |
| OK | get_stock_list | - | 12.03 | {"error": true, "message": "获取股票列表失败: All sources for 'realtime_quote' failed: eltdx: Runt |
| OK | get_stock_news | - | 0.76 | [{"新闻标题":"贵州茅台(<em>600519<\/em>.SH)：2<em>0<\/em>2<em>6<\/em>年中报净利润为44<em>5<\/em>.<em>1<\/e |
| OK | get_technical_indicator | - | 0.00 | {"error": true, "message": "不支持的指标 'MACD'。可选: ['close_50_sma', 'close_200_sma', 'close_10_ |
| OK | get_telegraph_news | - | 0.49 | [{"标题":"“全球最快高铁”CR450动车组模拟驾驶舱亮相柏林","内容":"【“全球最快高铁”CR450动车组模拟驾驶舱亮相柏林】财联社9月26日电，被称为“全球最快高铁”的 |
| OK | get_valuation_metrics | - | 3.26 | {"error": true, "message": "获取估值指标失败 (600519): '<' not supported between instances of 'dat |
| OK | get_wencai_news | - | 0.88 | [{"标题":"贵州茅台分红体量暂居A股第五","发布时间":"2026-07-03 21:06:00","来源":"茅台","摘要":"A股市场分红力度持续加码。据同花顺iFin |
| OK | get_wencai_query | - | 5.41 | {"error": true, "message": "问财查询失败: All sources for 'wencai_query' failed: pywencai: Runti |
| OK | get_xueqiu_hot | - | 0.38 | [{"排名":1,"代码":"000592","名称":"平潭发展","人气值":"95156.0","涨幅%":10.0251,"排名变化":0,"概念标签":["福建自贸区", |
| OK | health_check | - | 0.01 | {"status": "degraded", "issues": ["2 个数据源不健康"], "akshare": {"available": true, "version":  |
| OK | list_all_tools | - | 0.00 | {"status": "ok", "total": 130, "tools": ["analyze_industry_comparison", "analyze_market_ov |
| OK | list_performance_metrics | - | 0.00 | {"success": true, "categories": {"return": "收益类指标", "risk": "风险类指标", "risk_adjusted": "风险调 |
| OK | list_technical_indicators | - | 0.00 | [{"name": "close_50_sma", "description": "50-day Simple Moving Average (均线)"}, {"name": "c |
| SKIP | scan_stocks_for_signals | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stocks_data'] |
| SKIP | screen_stocks | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['stocks_data', 'conditions'] |
| OK | search_news | - | 0.75 | [{"新闻标题":"贵州茅台(<em>600519<\/em>.SH)：2<em>0<\/em>2<em>6<\/em>年中报净利润为44<em>5<\/em>.<em>1<\/e |
| OK | search_stock | - | 7.70 | {"error": true, "message": "搜索股票失败: All sources for 'company_info' failed: cninfo: ValueEr |
| SKIP | validate_signal_quality | - | 0.00 | 纯计算类，需输入行情数组（不联网）：['closes', 'signal_idx'] |

## 结论

### ⚠️ 无可用源的 (类型 / endpoint) 组合

**所有源都拿不到数据**的组合 —— 这是多 endpoint 类型最容易漏掉的问题：
单看某一行 `OK` 会以为该类型没问题，其实个别 endpoint 完全没有源覆盖。

- `industry_data[board_industry_cons_em]  [AKSHARE, UNSUPPORTED]`
- `industry_data[sector_fund_flow_rank]  [AKSHARE, UNSUPPORTED]`
- `industry_quotes  [FAIL]`
- `local_minute  [ENV]`
- `recent_minute  [EMPTY]`
- `wencai_query  [ENV]`

**发现 2 项失败（需人工确认是否回归）：**

- **industry_quotes** (`em_push2`) — RuntimeError: fetch_industry_quotes all sources failed（已重试 1 次）
- **xueqiu_hot** (`akshare_xueqiu_hot`) — NetworkError: Failed to request Xueqiu hot rank endpoint: Expecting value: line 1 column 1 (char 0)（已重试 1 次）
