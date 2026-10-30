"""tdxdata 常用数据中心产品的人工说明（Phase 1）。

上游 421 个产品的 ``name`` 多为英文缩写（``lhb`` / ``xjjj`` / ``gzzc_gg`` …），
且只有 52 个产品在 ``sections/mapping.py::FIELD_MAP`` 登记了标准列义。
本模块只做两件事：

1. ``PRODUCT_NOTES``：给常用产品一句中文说明，供 ``tdx_list_products`` /
   ``tdx_get_product`` 返回给 Agent；
2. ``PRODUCT_TIPS``：容易踩坑的产品补充提示（参数、单位、口径）。

⚠️ 说明只写**能确认**的内容；不确定的写「原始产品，列义见上游文档」，
不猜列义、不编单位。新增条目请先核对上游 ``docs/VERIFICATION.md`` 或实测样本。
"""

from __future__ import annotations

PRODUCT_NOTES: dict[str, str] = {
    # ── 日历 / 宏观 ──
    "holiday": "节假日与休市日历（中国/全球，含各交易所休市说明）",
    "world_cal": "全球财经日历（经济事件/数据发布日程）",
    "macro_jq": "宏观经济景气指数（月度）",
    "macro_xf": "宏观消费数据",
    "macro_yh": "宏观银行数据",
    "macro_gy": "宏观工业数据",
    "macro_my": "宏观贸易数据",
    "macro_wj": "宏观物价/外汇相关数据",
    "czsz": "财政收支数据",
    "yhhb": "央行/银行货币数据（货币供应、资产负债表等）",
    # ── 龙虎榜 / 两融 ──
    "lhb": "龙虎榜每日榜单（按日期/市场）",
    "lhfp": "龙虎榜复盘/分析（按日期）",
    "lhzy": "龙虎榜摘要",
    "lhzj": "龙虎榜资金统计",
    "lhb_jyr": "龙虎榜交易席位",
    "lhbss": "龙虎榜上榜个股",
    "lhbsj": "龙虎榜数据明细",
    "margin": "融资融券统计（两融余额/买入额等）",
    # ── 沪深港通 ──
    "north": "北向资金历史（沪深港通净流入）",
    "north_jg": "北向资金机构持仓",
    "hsgt_cgtj": "沪深港通持股统计",
    "hsgt_ss": "沪深港通实时/汇总数据",
    # ── 涨停 / 题材 ──
    "ztzb": "涨停板统计（首板/连板/涨停原因）",
    "ztqd": "涨停清单（含涨停原因/封单等）",
    "tlsq": "停复牌/特停信息",
    "xjjj": "限售解禁日历",
    "zttz_xg": "涨停/题材相关个股",
    # ── 大宗交易 / 新股 ──
    "dzjy": "大宗交易（按日/按股）",
    "dzjy_zlfx": "大宗交易主力分析",
    "xgipo": "新股 IPO 日历/发行信息",
    "ipozcz": "IPO 注册制相关信息",
    # ── 股东 / 资金 / 估值 ──
    "jgcg": "机构持股（按报告期）",
    "gdhs": "股东户数",
    "zjlx": "资金流向统计",
    "zjlx_cxlclr": "持续净流入榜（主力净额/持续天数）",
    "gzzc_gg": "个股估值排行（PE/PB/市值等）",
    "hygz": "行业估值",
    "gzzc_hyfx_hyfxsy_yjhy": "行业估值分析（一级行业）",
    # ── 市场统计 ──
    "sctj": "市场统计",
    "scfx": "市场分析（两融等）",
    "hyfx": "行业分析（行业涨跌/涨跌家数）",
    "queryMarketSentiment": "市场情绪（热度值/中文判语）",
    # ── 财报 / F10 ──
    "fin": "资产负债表（按代码；全表）",
    "lyb": "利润表（按代码；全表）",
    "xjllb": "现金流量表（按代码；全表）",
    "zyzb": "主要财务指标（按代码）",
    "gsjj": "公司概况（按代码）",
    "jyfx": "经营分析（按代码）",
    "fhrz_agf10": "分红融资（按代码）",
    # ── 基金 / ETF ──
    "jjhq": "基金行情（列表/排行）",
    "jjph": "基金排行",
    "jjpj": "基金评级",
    "etf_hq": "ETF/基金代码清单（**不是行情**）",
    "etf_t0": "ETF T+0 相关数据",
    "jj_cgtj": "基金持仓统计",
    # ── 资讯 / 其他 ──
    "hq_nlp_zjlx": "NLP 资金流数据",
    "qh_hqpd_qhhy": "期货主力连续合约快照（数据中心侧）",
    "qhlhb_sy": "期货合约清单/会员持仓（数据中心侧）",
    "tdxrb_qh": "期货合约榜（数据中心侧）",
}

#: 容易踩坑的产品补充提示；没有条目就不输出。
PRODUCT_TIPS: dict[str, str] = {
    "lhb": "产品模板里带样例年月（00101/1/2026/10）；按日期查询请用 params 覆盖模板，"
           "例如 params='[\"00101\",\"1\",\"2026\",\"10\",\"\"]'。",
    "etf_hq": "实测返回 1729×2 的 code/market，是 ETF/基金代码清单，不是行情；"
              "要行情请用 get_quotes / get_universe(market=9)。",
    "north": "返回近 30 个交易日；已登记列义，mapped=True 可用。",
    "xjjj": "默认参数返回近期解禁；按日期/代码筛选需用 params 覆盖模板。",
    "jgcg": "默认参数 00201/99 可能只是目录/摘要；机构持股明细需用 params 覆盖。",
    "gzzc_gg": "这是估值排行，不是单只股票的历史 PE/PB 时间序列；"
               "个股历史估值请用现有 get_valuation_metrics。",
    "zjlx_cxlclr": "只支持全市场排行，不支持单代码；单代码资金流请用现有 get_money_flow。",
    "fin": "按代码查询请用 params 覆盖为 ['600519'] 这类 6 位代码；"
           "结构化财报请用 tdx_get_finance(symbol, kind=\"zcfz\")。",
    "lyb": "按代码查询请用 params 覆盖为 ['00101','600519']；"
           "结构化财报请用 tdx_get_finance(symbol, kind=\"lyb\")。",
    "xjllb": "按代码查询请用 params 覆盖为 ['00101','600519']；"
             "结构化财报请用 tdx_get_finance(symbol, kind=\"xjllb\")。",
    "zyzb": "按代码查询请用 params 覆盖为 ['600519']；"
            "结构化财报请用 tdx_get_finance(symbol, kind=\"zyzb\")。",
    "gsjj": "按代码查询请用 params 覆盖为 ['00101','600519','0']；"
            "结构化 F10 请用 tdx_get_f10(symbol, section=\"gsjj\")。",
    "jyfx": "按代码查询请用 params 覆盖；结构化 F10 请用 tdx_get_f10(symbol, section=\"jyfx\")。",
    "lhfp": "列义已部分映射：N001=date、N002=listed_count、N003=net_amount_yuan"
            "（TDX 口径）；N004-N006 未证实。推荐直接用 tdx_get_lhb_review(date=...)。",
    "fhrz_agf10": "模板含 @code@，可直接 get(\"fhrz_agf10\", code=\"600519\")。",
    "queryMarketSentiment": "params 为 [{}]；返回市场热度/判语，不是分时行情。",
    "qh_hqpd_qhhy": "期货专题数据；本库未封装期货行情接口，但数据中心侧可经 get() 访问。",
}


def note_of(name: str) -> str:
    """产品中文说明；未登记时返回空串（调用方自行回退到域说明）。"""
    return PRODUCT_NOTES.get(name, "")


def tip_of(name: str) -> str:
    """产品补充提示；未登记时返回空串。"""
    return PRODUCT_TIPS.get(name, "")


__all__ = ["PRODUCT_NOTES", "PRODUCT_TIPS", "note_of", "tip_of"]
