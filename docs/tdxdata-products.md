# tdxdata 数据中心产品速查（Top 常用）

> 上游：tdxdata 0.3.0（vendored，421 个产品 / 17 个域）。
> 本表只列**有中文说明**的常用产品；完整目录请调用 MCP 工具 `tdx_list_products`。

## 怎么用

```python
# 1) 查产品名/参数
tdx_list_products(domain="龙虎榜与两融", keyword="龙虎")

# 2) 用默认参数取数
tdx_get_product("holiday")
tdx_get_product("north", mapped=True)

# 3) 覆盖产品模板参数（params 必须是 JSON 数组/对象）
tdx_get_product("lhb", params='["00101","1","2026","10",""]')
tdx_get_product("fin", params='["600519"]')
```

## 口径与限制

- **限流**：tdxdata 单命令约 2.2s 匀速闸门，按来源 IP 计；一只一次调用，禁止高频/并发。
- **列义**：421 个产品中只有 **52 个**登记了标准列义（`mapped=True` 可用）；其余返回
  服务端原始列（`N001...`）。工具会在 `column_note` 显式提示，不猜列义。
- **参数**：只有部分产品模板含 `@code@` / `#date#` 占位符；其余产品的模板参数是
  “样例值/固定视图”，按日期或代码查询必须用 `params` 覆盖。
- **空值**：上游给 `""` / `"—"` 的位为 `null`，不是 0；`unknown ≠ 0`。
- **行数**：工具默认最多 200 行（`max_rows` 可调，最大 1000），超出标 `truncated=true`。

## 常用产品

| 产品 | 域 | 说明 | 参数提示 | 标准列义 |
|---|---|---|---|---|
| `fhrz_agf10` | F10与个股资料 | 分红融资（按代码） | `code` 6 位代码 | ✅ |
| `gsjj` | F10与个股资料 | 公司概况（按代码） | 默认参数 | ✅ |
| `jyfx` | F10与个股资料 | 经营分析（按代码） | 默认参数 | ✅ |
| `gzzc_gg` | 估值 | 个股估值排行（PE/PB/市值等） | 默认参数 | — |
| `gzzc_hyfx_hyfxsy_yjhy` | 估值 | 行业估值分析（一级行业） | 默认参数 | — |
| `hygz` | 估值 | 行业估值 | 默认参数 | — |
| `ztqd` | 其他 | 涨停清单（含涨停原因/封单等） | 默认参数 | — |
| `zttz_xg` | 其他 | 涨停/题材相关个股 | 默认参数 | — |
| `etf_hq` | 基金与ETF | ETF/基金代码清单（**不是行情**） | 默认参数 | — |
| `etf_t0` | 基金与ETF | ETF T+0 相关数据 | 默认参数 | ✅ |
| `jj_cgtj` | 基金与ETF | 基金持仓统计 | 默认参数 | ✅ |
| `jjhq` | 基金与ETF | 基金行情（列表/排行） | 默认参数 | ✅ |
| `jjph` | 基金与ETF | 基金排行 | 默认参数 | ✅ |
| `jjpj` | 基金与ETF | 基金评级 | 默认参数 | ✅ |
| `dzjy` | 大宗交易 | 大宗交易（按日/按股） | 默认参数 | — |
| `dzjy_zlfx` | 大宗交易 | 大宗交易主力分析 | 默认参数 | — |
| `czsz` | 宏观 | 财政收支数据 | 默认参数 | ✅ |
| `holiday` | 宏观 | 节假日与休市日历（中国/全球，含各交易所休市说明） | 默认参数 | ✅ |
| `macro_gy` | 宏观 | 宏观工业数据 | 默认参数 | — |
| `macro_jq` | 宏观 | 宏观经济景气指数（月度） | 默认参数 | — |
| `macro_my` | 宏观 | 宏观贸易数据 | 默认参数 | — |
| `macro_wj` | 宏观 | 宏观物价/外汇相关数据 | 默认参数 | — |
| `macro_xf` | 宏观 | 宏观消费数据 | 默认参数 | — |
| `macro_yh` | 宏观 | 宏观银行数据 | 默认参数 | — |
| `world_cal` | 宏观 | 全球财经日历（经济事件/数据发布日程） | 默认参数 | ✅ |
| `yhhb` | 宏观 | 央行/银行货币数据（货币供应、资产负债表等） | 默认参数 | — |
| `hyfx` | 市场统计 | 行业分析（行业涨跌/涨跌家数） | 默认参数 | — |
| `queryMarketSentiment` | 市场统计 | 市场情绪（热度值/中文判语） | 默认参数 | — |
| `scfx` | 市场统计 | 市场分析（两融等） | 默认参数 | — |
| `sctj` | 市场统计 | 市场统计 | 默认参数 | — |
| `ipozcz` | 新股与再融资 | IPO 注册制相关信息 | 默认参数 | — |
| `xgipo` | 新股与再融资 | 新股 IPO 日历/发行信息 | 默认参数 | — |
| `xjjj` | 新股与再融资 | 限售解禁日历 | 默认参数 | — |
| `hsgt_cgtj` | 沪深港通 | 沪深港通持股统计 | 默认参数 | — |
| `hsgt_ss` | 沪深港通 | 沪深港通实时/汇总数据 | 默认参数 | — |
| `north` | 沪深港通 | 北向资金历史（沪深港通净流入） | 默认参数 | ✅ |
| `north_jg` | 沪深港通 | 北向资金机构持仓 | 默认参数 | — |
| `tlsq` | 涨停与题材 | 停复牌/特停信息 | 默认参数 | — |
| `ztzb` | 涨停与题材 | 涨停板统计（首板/连板/涨停原因） | 默认参数 | — |
| `gdhs` | 股东与持股 | 股东户数 | 默认参数 | — |
| `jgcg` | 股东与持股 | 机构持股（按报告期） | 默认参数 | — |
| `hq_nlp_zjlx` | 行情 | NLP 资金流数据 | 默认参数 | ✅ |
| `fin` | 财报 | 资产负债表（按代码；全表） | 默认参数 | ✅ |
| `lyb` | 财报 | 利润表（按代码；全表） | 默认参数 | ✅ |
| `xjllb` | 财报 | 现金流量表（按代码；全表） | 默认参数 | ✅ |
| `zyzb` | 财报 | 主要财务指标（按代码） | 默认参数 | ✅ |
| `zjlx` | 资金与筹码 | 资金流向统计 | 默认参数 | — |
| `zjlx_cxlclr` | 资金与筹码 | 持续净流入榜（主力净额/持续天数） | 默认参数 | — |
| `lhb` | 龙虎榜与两融 | 龙虎榜每日榜单（按日期/市场） | 默认参数 | — |
| `lhb_jyr` | 龙虎榜与两融 | 龙虎榜交易席位 | 默认参数 | — |
| `lhbsj` | 龙虎榜与两融 | 龙虎榜数据明细 | 默认参数 | — |
| `lhbss` | 龙虎榜与两融 | 龙虎榜上榜个股 | 默认参数 | — |
| `lhfp` | 龙虎榜与两融 | 龙虎榜复盘/分析（按日期） | `date` YYYY-MM-DD | — |
| `lhzj` | 龙虎榜与两融 | 龙虎榜资金统计 | 默认参数 | — |
| `lhzy` | 龙虎榜与两融 | 龙虎榜摘要 | 默认参数 | — |
| `margin` | 龙虎榜与两融 | 融资融券统计（两融余额/买入额等） | 默认参数 | — |
| `tdxrb_qh` | 龙虎榜与两融 | 期货合约榜（数据中心侧） | 默认参数 | — |

> 共 57 个常用产品有中文说明；上游完整目录 421 个产品，其中 52 个有标准列义。

## 易踩坑提示

| 产品 | 提示 |
|---|---|
| `etf_hq` | 实测返回 1729×2 的 code/market，是 ETF/基金代码清单，不是行情；要行情请用 get_quotes / get_universe(market=9)。 |
| `fhrz_agf10` | 模板含 @code@，可直接 get("fhrz_agf10", code="600519")。 |
| `fin` | 按代码查询请用 params 覆盖为 ['600519'] 这类 6 位代码；结构化财报请用 tdx_get_finance(symbol, kind="zcfz")。 |
| `gsjj` | 按代码查询请用 params 覆盖为 ['00101','600519','0']；结构化 F10 请用 tdx_get_f10(symbol, section="gsjj")。 |
| `gzzc_gg` | 这是估值排行，不是单只股票的历史 PE/PB 时间序列；个股历史估值请用现有 get_valuation_metrics。 |
| `jgcg` | 默认参数 00201/99 可能只是目录/摘要；机构持股明细需用 params 覆盖。 |
| `jyfx` | 按代码查询请用 params 覆盖；结构化 F10 请用 tdx_get_f10(symbol, section="jyfx")。 |
| `lhb` | 产品模板里带样例年月（00101/1/2026/10）；按日期查询请用 params 覆盖模板，例如 params='["00101","1","2026","10",""]'。 |
| `lhfp` | 列义已部分映射：N001=date、N002=listed_count、N003=net_amount_yuan（TDX 口径）；N004-N006 未证实。推荐直接用 tdx_get_lhb_review(date=...)。 |
| `lyb` | 按代码查询请用 params 覆盖为 ['00101','600519']；结构化财报请用 tdx_get_finance(symbol, kind="lyb")。 |
| `north` | 返回近 30 个交易日；已登记列义，mapped=True 可用。 |
| `queryMarketSentiment` | params 为 [{}]；返回市场热度/判语，不是分时行情。 |
| `xjjj` | 默认参数返回近期解禁；按日期/代码筛选需用 params 覆盖模板。 |
| `xjllb` | 按代码查询请用 params 覆盖为 ['00101','600519']；结构化财报请用 tdx_get_finance(symbol, kind="xjllb")。 |
| `zjlx_cxlclr` | 只支持全市场排行，不支持单代码；单代码资金流请用现有 get_money_flow。 |
| `zyzb` | 按代码查询请用 params 覆盖为 ['600519']；结构化财报请用 tdx_get_finance(symbol, kind="zyzb")。 |

## `lhfp` 龙虎榜复盘列义（部分映射）

推荐直接用 MCP 工具 `tdx_get_lhb_review(date="YYYY-MM-DD")`，不要自己解析原始 N 列。

| 原始列 | 标准字段 | 证据 / 口径 |
|---|---|---|
| `N001` | `date` | 日期（强证据）|
| `N002` | `listed_count` | 上榜股票数；与东财龙虎榜按日去重股票数**连续 5 个交易日完全一致**（69/67/78/73/81）|
| `N003` | `net_amount_yuan` | TDX 复盘口径净额（元）；与东财 `BILLBOARD_NET_AMT` 汇总**不完全一致**（2026-10-29 TDX -12.52 亿 vs 东财 -9.76 亿），只看方向/量级 |
| `N004`–`N006` | 不映射 | 满足 `N002 = N004 + N005 + N006`，但语义未证实；原值保留在 `raw` |

> 需要东财精确口径请用现有 `get_dragon_tiger` / `get_dragon_tiger_signal`。

## `lhzy` / `lhbsj` 龙虎榜个股明细列义

推荐直接用 `tdx_get_lhb_stocks(date="YYYY-MM-DD", view="list"|"detail")`。

| 产品 | 用途 | 关键字段（已用东财逐股核对） |
|---|---|---|
| `lhbsj`（view=list） | 全量个股行（同一股票可多行） | `name/code/market/price/change_pct/net_buy_amount_yuan/amount_yuan/tag` |
| `lhzy`（view=detail） | 上游前 20 | `code/name/market/change_pct/amount_yuan/net_buy_amount_yuan/net_buy_ratio_pct/reason_code/date/tags/concepts` |

- `market`：0=深、1=沪、2=北；
- `amount_yuan` ↔ 东财 `ACCUM_AMOUNT`；`net_buy_amount_yuan` ↔ 东财 `BILLBOARD_NET_AMT`；
- `net_buy_ratio_pct` = `net_buy/amount×100`；
- `reason_code` 是 TDX 内部异动码（未翻译）；需要中文原因用 `get_dragon_tiger`。



