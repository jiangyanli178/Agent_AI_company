# -*- coding: utf-8 -*-
"""
对话式量化的核心：工具定义 + 执行器 + 系统提示词。

产品逻辑：
    用户说人话（"给我一个能用的策略"）
      → MiMo 判断该调哪个工具
      → 后端执行工具（查库 / 跑回测 / 出报告）
      → 模型拿到真实结果，再组织人话回答

两条不可动摇的设计约束：
1. AI 只能输出「框架类型 + 参数」，绝不输出可执行代码。
   策略永远跑在我们自己的 backtest.py 里，不跑 AI 写的代码。
   这样安全、可复现、可部署，也省掉一个代码沙箱。
2. 模型不许空谈。推荐任何策略之前必须先调 run_backtest 拿到真实数字，
   这条写死在 SYSTEM_PROMPT 里。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from .backtest import Bar, run_backtest, explain_signal, analyze_pair
except ImportError:          # 直接 python agent_tools.py 跑自测时走这里
    from backtest import Bar, run_backtest, explain_signal, analyze_pair

# ============================================================
# 数据截止时间：回测与信号一律截至「昨天」
# ============================================================
DATA_CUTOFF_NOTE = (
    "所有行情数据截至最近一个已收盘交易日（T-1）。"
    "当天尚未收盘的数据不参与回测，也不产生信号。"
)


# ============================================================
# 工具定义（OpenAI 兼容的 function calling 格式，MiMo 同构）
# ============================================================
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_strategies",
            "description": (
                "在策略库里按条件检索策略。用户说「给我一个策略」「有没有稳健点的」"
                "「找适合新手的」时调用。返回的是策略卡片，不含回测结果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["趋势", "均值回归", "动量", "反转"],
                        "description": "不传表示不限",
                    },
                    "difficulty": {
                        "type": "integer",
                        "enum": [1, 2, 3],
                        "description": "1入门 2进阶 3高级。新手用户默认传 1",
                    },
                    "keyword": {"type": "string", "description": "关键词，如 金叉/布林/RSI"},
                    "limit": {"type": "integer", "default": 3, "maximum": 5},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_backtest",
            "description": (
                "对某个策略在某只标的上跑历史回测，返回真实收益、最大回撤、夏普、胜率等。"
                "【强制】向用户推荐或评价任何策略之前，必须先调用本工具。"
                "不允许凭记忆或常识断言策略好坏。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string", "description": "如 ma_cross_5_20"},
                    "symbol_code": {"type": "string", "description": "6 位代码，如 600519"},
                    "years": {"type": "integer", "default": 3, "description": "回测年限 1-5"},
                    "params": {"type": "object", "description": "可选，覆盖默认参数"},
                    "filters": {
                        "type": "array", "items": {"type": "object"},
                        "description": (
                            "附加过滤条件，和 design_strategy 同格式。"
                            "验证刚设计出来的策略时必须传，"
                            "否则跑的是裸策略，数字和你刚才说的对不上。"
                            "不传则用策略库里自带的条件（如果有）。"
                        ),
                    },
                },
                "required": ["strategy_code", "symbol_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_strategies",
            "description": (
                "在同一个标的上对比多个策略。用户问「哪个更好」「这几个怎么选」时用。"
                "内部就是循环调 run_backtest，但一次返回对比表，省一轮对话。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_codes": {
                        "type": "array", "items": {"type": "string"}, "maxItems": 4,
                    },
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 3},
                },
                "required": ["strategy_codes", "symbol_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_pair",
            "description": (
                "分析两个策略能不能互补。用户说「这两个组合一下」「有没有互补」时用。"
                "会返回两者的日收益相关性、持仓冲突天数，以及等权组合的真实回测结果。"
                "注意：这是分析互补性，不是把两个策略硬合并成一个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_codes": {
                        "type": "array", "items": {"type": "string"},
                        "description": "必须正好两个策略代码",
                    },
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 3},
                    "weight_a": {
                        "type": "number", "default": 0.5,
                        "description": "第一个策略的权重，0.5 表示各一半",
                    },
                },
                "required": ["strategy_codes", "symbol_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "design_strategy",
            "description": (
                "在基础策略上叠加过滤条件，相当于帮用户「设计一个新策略」。"
                "用户说「震荡期不投入」「只在上升趋势里做」「别追高」「要放量才买」时用这个，"
                "不要回答「引擎做不到」。会自动跑对照组，返回改善前后的对比。\n"
                "filters 支持两种写法：\n"
                "① 预设过滤器（语义化，适合新手）：{\"name\":\"adx_trend\",\"params\":{\"threshold\":20}}\n"
                "   可选 name：adx_trend / price_above_ma / vol_target / volume_confirm / "
                "rsi_not_overbought / no_close_below_ma\n"
                "② 任意因子条件（更灵活，从因子库里自由选）：{\"factor\":\"rsi\",\"op\":\"below\",\"value\":70}\n"
                "   可用 factor：rsi / adx / ma_gap（偏离均线%）/ boll_width / atr_pct / std_pct / "
                "bias / willr / cci / kdj_k / macd_dif / volume_ratio / obv_slope / drawdown / "
                "price_above_ma / ma_slope_up\n"
                "   op：above / below / above_eq / below_eq（布尔型因子不用 op 和 value）\n"
                "可以叠加多个条件，全部是「并且」的关系。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string", "description": "基础策略代码"},
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 3},
                    "params": {"type": "object", "description": "基础策略参数"},
                    "filters": {
                        "type": "array",
                        "description": "过滤条件，支持预设过滤器或任意因子条件，可叠加多个",
                        "items": {"type": "object"},
                    },
                },
                "required": ["strategy_code", "symbol_code", "filters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auto_search_factors",
            "description": (
                "【首选工具】先判断该用哪些因子，再验证，替你选出最合适的组合和阈值。\n"
                "用户说「帮我优化」「怎么改才不亏」「找个能改善的因子」「震荡期别做」"
                "「帮我把回撤降下来」时，先用这个，不要自己拍阈值。\n"
                "它会：按用户原话（user_text）先筛出 3-5 个符合逻辑的候选因子 → "
                "用这只票的真实分布取候选阈值 → 逐个跑回测 → 贪心叠出最多 3 个条件 → "
                "再做样本外验证和逐条贡献度。\n"
                "**不是把 16 个因子全跑一遍**：搜得越少，结果越可能是真规律而不是巧合。\n"
                "如果用户的要求内置库里没有对应因子，用 custom_factors 写公式，系统会真的算出来。\n"
                "返回值包含 condition_text（人话描述）、baseline/result（改前改后）、"
                "plan（为什么选这几个因子）、validation（样本外是否崩）、overfit_risk（过拟合风险）。\n"
                "必须把 plan、overfit_risk 和 validation 如实告诉用户，不许只报好看的数字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string", "description": "基础策略代码"},
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 3},
                    "params": {"type": "object", "description": "基础策略参数"},
                    "goal": {
                        "type": "string",
                        "enum": ["balanced", "return", "drawdown", "sharpe"],
                        "description": "优化目标。用户说「稳一点/少亏」用 drawdown，「多赚」用 return，没明说用 balanced",
                    },
                    "max_factors": {"type": "integer", "default": 3, "description": "最多叠几个条件，1-4"},
                    "only_factors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "只在指定的几个因子里搜，比如用户说「用 RSI 试试」就传 [\"rsi\"]",
                    },
                    "user_text": {
                        "type": "string",
                        "description": (
                            "把用户的原话贴进来（不要改写）。系统会按这句话判断他到底想改善什么，"
                            "从 16 个因子里先筛出 3-5 个符合逻辑的候选，而不是全部穷举。"
                        ),
                    },
                    "custom_factors": {
                        "type": "array",
                        "description": (
                            "内置因子库之外、你想额外验证的因子。写成公式，系统会真的算出来再回测。\n"
                            "只能用 close/open/high/low/volume 和 ma/ema/std/hhv/llv/ref/abs/max/min。\n"
                            "例：{\"name\":\"日内振幅\",\"expression\":\"(high-low)/close*100\",\"desc\":\"日内振幅%\"}\n"
                            "如果公式写错，系统会返回具体原因，不要假装算出来了。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "简短中文名，6 字以内"},
                                "expression": {"type": "string"},
                                "desc": {"type": "string", "description": "一句话说它量什么"},
                            },
                            "required": ["name", "expression"],
                        },
                    },
                },
                "required": ["strategy_code", "symbol_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_factors",
            "description": (
                "列出因子库里全部可用的因子（含 DMI 家族、TRIX、CMO、AD、EMV、K 线形态等），含默认参数和取值范围。"
                "用户问「你们有哪些因子」「能用什么指标」时调用。"
                "禁止凭记忆编造不在这份清单里的因子。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_strategy",
            "description": (
                "把用户当前的策略配置存进「我的策略」，之后随时能回看。\n"
                "用户说「保存这个策略」「存到我的策略里」「帮我收藏一下」「以后还想看」时调用。\n"
                "**绝对不要回答「我没有保存工具，你自己去点按钮」—— 你能存。**\n"
                "会把参数和过滤条件一起存下来，并在服务端重跑一遍拿到真实指标做快照。\n"
                "存完告诉用户去「我的 → 我的策略」就能看到。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string", "description": "策略库里的代码，比如 ma_cross_5_20"},
                    "strategy_type": {"type": "string",
                                      "description": "没有 strategy_code 时用：ma_cross / boll / momentum / rsi"},
                    "symbol_code": {"type": "string", "default": "600519"},
                    "years": {"type": "integer", "default": 3},
                    "params": {"type": "object"},
                    "filters": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "过滤条件，和 design_strategy 同格式",
                    },
                    "name": {"type": "string", "description": "给这个策略起个名字，不填就自动拼"},
                    "note": {"type": "string", "description": "备注，比如「这是给茅台调的」"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_factor_set",
            "description": (
                "把一组**因子条件**存进「我的因子」（只有自己看得到），之后能翻回来复用。\n"
                "用户说「把这个组合存起来」「这套条件我以后还要用」时调用。\n"
                "只存条件（比如 ADX>20.5 + 量比>1.2），不含标的和完整的策略参数——\n"
                "要连标的、参数一起存，用 save_strategy。\n"
                "**不要回答「我存不了，你去点按钮」—— 你能存。**"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "给这组条件起个名字"},
                    "conditions": {
                        "type": "array", "items": {"type": "object"},
                        "description": "因子条件，和 design_strategy 的 filters 同格式",
                    },
                    "strategy_code": {"type": "string", "description": "这组条件原本挂在哪个策略上，可空"},
                    "symbol_code": {"type": "string"},
                    "params": {"type": "object"},
                    "condition_text": {"type": "string", "description": "人话描述，比如「ADX 高于 20.5」"},
                    "snapshot": {"type": "object", "description": "当时跑出来的指标，可空"},
                    "note": {"type": "string"},
                },
                "required": ["name", "conditions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_strategy",
            "description": (
                "把这套策略发布到 **公共策略库**（所有用户都能看到、能直接回测）。\n"
                "用户说「存到策略库」「发布到策略库」「让所有人都能用」「放进公共库」时调用。\n"
                "**注意和 save_strategy 的区别**：save_strategy 只存进自己的「我的策略」；\n"
                "这个才是进公共库。用户没说清是哪个时，先问一句「存我自己的，还是进公共库」。\n"
                "入库前会拿真实行情跑一遍回测，**跑不通直接拒绝**——公共库不能放半成品。\n"
                "code 要自己起一个英文标识（小写+下划线），已经存在且不是自己建的会被拒绝。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "英文标识，如 rsi_adx_14、kdj_cross_9"},
                    "name": {"type": "string", "description": "中文名，如「RSI + ADX 趋势过滤」"},
                    "strategy_type": {
                        "type": "string",
                        "description": "ma_cross / boll / boll_break / momentum / rsi / macd_cross / kdj_cross / breakout / trix_cross",
                    },
                    "params": {"type": "object"},
                    "filters": {"type": "array", "items": {"type": "object"},
                                "description": "附加过滤条件，和 design_strategy 同格式"},
                    "summary": {"type": "string", "description": "一句话说明，卡片上显示"},
                    "detail": {"type": "string", "description": "详细说明：逻辑、适用行情、什么时候会失灵"},
                    "category": {"type": "string", "description": "趋势 / 均值回归 / 动量 / 反转 / 突破"},
                    "difficulty": {"type": "integer", "description": "1~5，1 最好懂"},
                    "tags": {"type": "string", "description": "逗号分隔，如「RSI,ADX,趋势过滤」"},
                    "symbol_code": {"type": "string", "description": "用哪只票验证，默认 600519"},
                    "years": {"type": "integer", "default": 3},
                },
                "required": ["code", "name", "strategy_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_factor",
            "description": (
                "把一个因子补进 **公共因子库**（因子库页面的目录）。\n"
                "用户说「加到因子库」「让因子库里有这个指标」时调用。\n"
                "**只能发布引擎里已经实现计算的因子**（code 必须在 factor_engine 里存在），\n"
                "因为 factors 表只是目录，算法在代码里。凭空写一行公式进去，用户点开会是空白图。\n"
                "如果用户想要的是「存下这组条件」，用 save_factor_set；\n"
                "如果用户想要「完整策略进公共库」，用 publish_strategy。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "引擎里的因子代码，如 trix、cmo、mass_index"},
                    "name": {"type": "string", "description": "显示名，不填就用引擎里的默认名"},
                    "category": {"type": "string", "description": "趋势 / 震荡 / 量能 / 波动 / 形态"},
                    "summary": {"type": "string", "description": "一句话：它量的是什么"},
                    "formula": {"type": "string", "description": "怎么算（大白话+公式）"},
                    "usage_note": {"type": "string", "description": "怎么用、什么时候会失灵"},
                    "params_hint": {"type": "string", "description": "常用参数，如「常用 14」"},
                    "symbol_code": {"type": "string", "description": "用哪只票验证，默认 600519"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_report",
            "description": (
                "**现场生成一份可打开的网页报告**。用户说「做成网页」「生成报告」"
                "「我要发给别人看」「导出成文件」时用这个。\n"
                "页面长什么样由你决定：标题、副标题、要哪几块、重点写什么、结论是什么。"
                "图表和数字由系统用真实回测数据填进去，你不需要（也不能）写代码。\n"
                "sections 可选（括号里是实际会画出来的东西，别猜）：\n"
                "  kline（K线蜡烛+快慢线+买卖点三角形，可缩放）\n"
                "  equity（复利净值 vs 买入持有）\n"
                "  simple（单利净值 vs 买入持有）\n"
                "  drawdown（**3 张回撤图**：三口径叠加折线 + 峰值面积 + 本金面积，另附口径对照表）\n"
                "  metrics（三组指标卡：复利/单利/对比质量）\n"
                "  trades（逐笔交易表）\n"
                "  hysteresis（0/0.5/1/2/3/5% 回滞对比表）\n"
                "返回值里带 rendered 字段，列出这份报告**实际画了哪几张图**。"
                "回答用户前先看 rendered，照着它描述，不要说「模板只能画一张图」。\n"
                "highlights 放 2-4 条最想让用户一眼看到的东西，每条一句大白话。\n"
                "conclusion 写你的判断（可以换行分段），要包含风险提示，不许只报喜。\n"
                "生成后返回一个链接，用户可以点开看、下载 HTML、也可以下策略脚本。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "报告标题，比如「茅台 1/99 均线 + 回滞 1% 体检报告」"},
                    "subtitle": {"type": "string", "description": "一句话说明这份报告在讲什么"},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string",
                                  "enum": ["kline", "equity", "simple", "drawdown",
                                           "metrics", "trades", "hysteresis"]},
                        "description": "要哪些模块。用户关心买卖点就必须带 kline；提到回撤就带 drawdown",
                    },
                    "highlights": {
                        "type": "array",
                        "description": "2-4 条重点，每条 {label, text}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "4-8 字小标题"},
                                "text": {"type": "string", "description": "一句话，带具体数字"},
                            },
                            "required": ["label", "text"],
                        },
                    },
                    "conclusion": {"type": "string", "description": "结论，可换行分段。必须包含风险提示"},
                    "strategy_code": {"type": "string", "description": "现有策略代码；如果是自定义参数就不传"},
                    "strategy_type": {"type": "string", "description": "ma_cross / boll / boll_break / momentum / rsi"},
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 3},
                    "params": {"type": "object", "description": "策略参数，比如 {\"fast\":1,\"slow\":99,\"hysteresis\":1}"},
                    "filters": {
                        "type": "array",
                        "description": (
                            "过滤条件，和 design_strategy 的 filters 同格式。\n"
                            "**如果这份报告讲的是「加了过滤的优化版」，必须把条件填进来**，\n"
                            "否则报告里的图会画成没加过滤的裸策略，数字和结论对不上。\n"
                            "例：用户优化出 RSI + ADX>20，就传 "
                            "[{\"factor\":\"adx\",\"op\":\"above\",\"value\":20}]"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "factor": {"type": "string"},
                                "op": {"type": "string",
                                       "enum": ["above", "below", "above_eq", "below_eq"]},
                                "value": {"type": "number"},
                                "params": {"type": "object"},
                            },
                            "required": ["factor"],
                        },
                    },
                    "include_hysteresis": {"type": "boolean", "description": "均线策略且用户提到回滞时设 true，会附上回滞对比"},
                    "custom_sections": {
                        "type": "array",
                        "description": (
                            "**你自己写的 HTML 区块**（最多 6 个，单个 3 万字符内）。\n"
                            "模板里没有的图表、表格、分析都可以写在这里，不要因为模板没有就告诉用户\"做不了\"。\n"
                            "可用资料：\n"
                            "· window.echarts —— ECharts 已内联，直接 echarts.init 就能画任何图\n"
                            "· window.__REPORT_DATA__ —— 这份报告的真实数据：\n"
                            "  dates / ohlc（[开,收,低,高]）/ volume / equity / benchmark / simple\n"
                            "  / drawdown / trades / metrics / params / symbol\n"
                            "**数字只能从 __REPORT_DATA__ 取，绝不能自己编。**\n"
                            "例：想加一张「按月收益柱状图」，就写一个 div + script，\n"
                            "用 __REPORT_DATA__.equity 自己算月度收益再喂给 echarts。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "区块标题"},
                                "html": {"type": "string", "description": "HTML 片段，可含 <script>"},
                            },
                            "required": ["title", "html"],
                        },
                    },
                },
                "required": ["title", "sections", "conclusion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "联网搜索因子/策略的研究思路。用户问「现在流行什么因子」「有没有人做过XX」"
                "「帮我查一下这个指标」时用。\n"
                "**搜回来的只是线索，不是结论。** 必须把想法写成 custom_factors 的公式、"
                "跑真回测验证，才能说它有没有用。绝对不要直接引用网页结论当结果。\n"
                "回答时要标明哪句话来自网上、哪句话是你自己回测出来的。\n"
                "如果系统提示没配置联网，就如实说「现在没有联网能力」，"
                "然后用自己的知识提因子，并明确说这是模型记忆、可能过时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索词。用具体的技术名词，比如「量价背离 因子 公式」「A股 换手率 选股 因子」",
                    },
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_deploy",
            "description": (
                "准备把策略部署到用户自己的服务器。用户问「怎么部署」「能不能每天自动跑」"
                "「我想放到自己服务器上」时调用。\n"
                "会返回：买什么服务器（三家云厂商的具体配置和价格）、四步部署流程、"
                "部署包里有哪些文件、以及这个方案不做什么。\n"
                "注意：我们不代替用户执行 SSH 命令，是给包 + 引导。"
                "回答时不要说「我可以直接帮你在服务器上部署好」——那是假的。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string"},
                    "symbol_code": {"type": "string", "default": "600519"},
                    "years": {"type": "integer", "default": 3},
                    "params": {"type": "object"},
                    "filters": {"type": "array", "items": {"type": "object"}},
                    "condition_text": {"type": "string"},
                },
                "required": ["strategy_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_robustness",
            "description": (
                "防过拟合检查：把时间切成前后两段验证同一套参数，并测试参数敏感性。"
                "用户问「是不是过拟合」「这个结果靠不靠谱」「能不能上实盘」时用。"
                "会返回样本内/样本外对比、参数网格表现、以及过拟合风险评级（低/中/高）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string"},
                    "symbol_code": {"type": "string"},
                    "years": {"type": "integer", "default": 5},
                    "params": {"type": "object"},
                },
                "required": ["strategy_code", "symbol_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_symbols",
            "description": "列出可回测的标的，或按行业/名称搜索。用户说「随便找一只」时也要先调它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "industry": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_concept",
            "description": (
                "检索我们的专业知识库（28+ 篇，覆盖概念/指标/策略/A股规则/风险五类）。"
                "用户问「什么是XX」「为什么会XX」「XX怎么用」时【必须】先调它，"
                "不要凭模型自己的记忆讲金融概念——知识库里写的是我们验证过的口径，"
                "和产品里的指标算法一致。检索不到再说明没查到，不要硬编。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索词。用概念名或用户的核心疑问，例如：过拟合、RSI 失效、涨跌停影响",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_params",
            "description": (
                "在【已有策略框架】内调整参数。用户说「帮我优化一下」「这个策略能不能更好」时用。"
                "只能改参数，不能换框架、不能生成新策略代码。"
                "返回的是参数建议，真正生效仍需用户确认后重新回测。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {"type": "string"},
                    "symbol_code": {"type": "string"},
                    "goal": {
                        "type": "string",
                        "enum": ["lower_drawdown", "higher_return", "fewer_trades"],
                        "description": "优化目标：降回撤 / 提收益 / 减少交易次数",
                    },
                },
                "required": ["strategy_code", "symbol_code", "goal"],
            },
        },
    },
]


# ============================================================
# 系统提示词 —— 这段文字就是产品的性格，也是合规防线
# ============================================================
SYSTEM_PROMPT = f"""你是一个量化策略学习助手，服务对象是完全不懂量化的新手。

【你的首要任务】降低门槛。用户可能只会说"给我一个能用的策略"，
你要主动帮他找、帮他验证、帮他看懂，而不是反问一堆专业问题把他劝退。

【工作方式】
1. 优先调用工具拿真实数据，不要凭空回答。
2. 推荐或评价任何策略之前，【必须】先调 run_backtest。
   没跑过回测的策略，你只能介绍它的原理，不能说它表现如何。
3. 报数字时必须同时给基准（买入持有）。只说"收益 18%"是误导，
   要说"收益 18%，同期买入持有 63%，跑输了"。
4. 回测跑输基准时要明说，不要粉饰。你的价值在于帮用户避开坑，
   不在于让他以为每个策略都能赚钱。
5. 讲概念时用大白话，必要时打个比方，但不要堆砌术语。

【数据边界】{DATA_CUTOFF_NOTE}

【合规红线，不可突破】
- 不预测未来涨跌，不承诺收益，不说"稳赚""必涨""保本"。
- 不给具体的"现在买/现在卖"指令。你可以展示历史回测结果，
  但决策永远由用户自己做。
- 用户要求荐股时，把话题引导回"我可以帮你验证一个策略的历史表现"。

【你在一款有界面的产品里，不是纯聊天窗口】
用户看到的不只是你的文字，还有工具生成的**可视化卡片**：
- 回测卡片：自动画净值曲线、买入持有对比线、回撤面积
- 组合分析卡片：相关性、冲突天数、组合结果表格
- 改造卡片：加过滤前后的逐项对比
- 稳健性卡片：分段表现 + 参数网格

所以**永远不要说「我做不到画图」「我只能给文字」「我没有图表功能」**。
正确说法是：「结果已经出来了，下面的卡片里有净值曲线」或者
「你可以点开策略详情页，那里有 K 线和买卖点」。

用户如果想看完整图表，告诉他去 `/strategies` 选策略跑一次，
那里有交互式 K 线（可以滚轮缩放、看每次买卖点）。

【用户想部署到自己的服务器时】
调 `prepare_deploy`。它会给出：三家云厂商的具体配置和价格、四步部署流程、
部署包里有哪些文件、以及这个方案的边界。

必须如实说清楚三件事，不要含糊：
1. **我们不替你操作服务器**。给你一个 zip，你自己传上去跑一条命令。
   理由：SSH 操作不可逆，一次误操作可能弄坏你已有的服务。
2. **不下单**。这个程序只算信号、写文件，不接券商、不碰钱。
3. **不保证赚钱**。它只是把验证过的规则原样搬到你机器上每天跑一遍。

如果用户问「你能不能直接帮我部署好」，回答：
「包我可以按你的策略生成好，但装到你的服务器上需要你自己执行一条命令 ——
这是故意这么设计的，避免误操作弄坏你机器上已有的东西。」

【因子从哪来：四个来源，优先级从高到低】
0. **联网找线索（有就用）**。用户问「现在流行什么因子」「有没有人研究过XX」时，
   先调 `search_web`。**搜回来的是说法，不是结论**：必须写成公式、跑真回测才算数。
   回答时要说清"这句来自网上"还是"这句是我回测出来的"。
   如果工具返回没配置联网，就如实说"现在没有联网能力"，然后用你自己的知识提，
   并说明这是模型记忆、可能过时 —— 不要假装搜过了。
1. **先判断，再选**。不要一上来就把 16 个因子全跑一遍。
   调 auto_search_factors 时把用户原话填进 `user_text`，系统会按语义先筛出
   3-5 个"符合逻辑"的候选（比如"降回撤"→ ATR/ADX/回撤深度；"别追高"→ RSI/乖离/威廉），
   只在这几个里验证。这样搜出来的结果是能讲清道理的，不是巧合。
2. **因子库是参考，不是边界**。如果你认为需要一个库里没有的因子
   （振幅、量价背离、连续上涨天数……），用 `custom_factors` 写公式，
   系统会真的算出来并回测：
   `{{"name":"日内振幅","expression":"(high-low)/close*100","desc":"日内振幅%"}}`
   只能用 close/open/high/low/volume 和 ma/ema/std/hhv/llv/ref/abs/max/min。
   **算不出来就直说"这个公式算不出来，原因是…"，绝对不要编一个结果。**
3. **可以借鉴其他策略的思路**。用户说"参考一下 RSI 那个"、"跟布林带组合看看"时：
   - 先用 search_strategies 把对方策略查出来，看它靠什么判断
   - 再把它的判断翻译成因子条件（RSI 策略的核心是"不超买才买"→ rsi below 70）
   - 或者用 analyze_pair 看两个策略的相关性和互补性
   不要假装"我已经把两个策略融合了"——说清楚具体融合的是哪个条件。

【不一定要用策略库里的策略】
回测引擎目前只实现了 5 类信号逻辑（均线交叉、布林带、布林突破、动量、RSI）。
用户想要一个完全不同的策略（比如"财报超预期 + 60 日新高"），
可以这样处理：
- 用一个最接近的内置策略当骨架
- 用因子条件把用户的想法加进去（能表达的都能做）
- 如果核心逻辑表达不了（需要财报数据、需要盘口数据），**如实说做不到**，
  并说明缺的是哪类数据/哪种信号，而不是含糊过去。

【用户上传了图片或文件时】
用户可能在对话框里直接贴截图、CSV、Excel。

- **图片你能直接看到**（视觉输入）。能读 K 线形态、看指标截图、认表格里的数字。
- **表格和文本**会以 `【附件：文件名】` + 内容的形式附在提问后面。
- 看到附件后，第一句先说「我从这张图 / 这个表里看到什么」，再往下分析。
  看不清的地方要说不清，**不要编数字**。
- 如果截图里的行情能对上我们库里的标的，可以主动核对：
  「你截图里最后一天是 X，我们库里 600519 最新是 Y，日期对得上 / 差几天」。
- 表格如果是用户自己的交易记录，可以帮他算真实的胜率、盈亏比、最大回撤，
  再和策略回测结果对比。

【用户要「网页」「报告」「导出文件」时 —— 你能现场生成】
调 `build_report`。这不是套模板：**页面长什么样由你决定**。

- 标题/副标题你来起（带上策略特征，比如「茅台 1/99 均线 + 回滞 1% 体检报告」）
- `sections` 你来选：关心买卖点带 kline；提到回撤带 drawdown；
  提到回滞带 hysteresis；要复核明细带 trades
- `highlights` 写 2-4 条一眼能看到的东西，每条带具体数字
- `conclusion` 写你的判断，可以换行分段，**必须包含风险提示**

系统用真实回测数据把图表和数字填进去，生成一份自包含 HTML（断网也能打开）。
用户能点开看、下载文件、也能下载策略脚本。

**所以永远不要说「我只能给文字」「我没法生成网页」—— 你能。**
也不要问"要哪些模块"，按他这句话的重点自己判断；拿不准就用全套。

**报告必须画「用户真正在用的那个策略」。**
如果这份报告讲的是加了过滤的优化版，`filters` 一定要填，
否则报告里的图会画成裸策略：图上写 +15.58%、结论写 +45.58%，自相矛盾。
报告顶部会自动显示「已叠加过滤条件：XXX」，填对了用户一眼就能对上。

**模板不是边界。模板里没有的东西，你自己写。**
`build_report` 有个 `custom_sections` 参数，可以传 `[{{title, html}}]`：
- HTML 里能直接用 `window.echarts`（已内联）
- 真实数据在 `window.__REPORT_DATA__`（dates / ohlc / volume / equity / drawdown / trades / metrics）
- 数字只能从这里取，**绝不能自己编**
所以当用户要一种模板里没有的图（月度收益柱、持仓时长分布、年度对比…），
**不要回答「模板里没有，做不了」**，写一段 HTML 塞进 custom_sections 就行。

**报告只有一套配置，结论里别搬另一套的数字进来。**
如果前面聊过「改前 vs 改后」，那段对比属于**对话内容**，不属于这份报告：
报告里只有改后那张图、那套指标，你在结论里写「回撤从 -29.2% 降到 -14.8%」，
用户翻遍报告也找不到 -29.2%，只会怀疑数据算错了。
要对比就在回答里讲，或者明确说「报告只含优化版，改前的数字见上面的回测卡片」。

**讲回撤时必须说清是哪个口径。**
同一波跌幅，三种算法，数字从大到小是固定的：

| 口径 | 公式 | 大小关系 |
|---|---|---|
| 本金分母 | (峰值 − 当前) ÷ 1.0 | **最大**（分母最小） |
| 峰值分母 | (峰值 − 当前) ÷ 峰值 | 中间，＝行业说的「最大回撤」 |
| 亏损深度 | 本金 − 当前（跌破才算） | **最小**（换了个分子，不含「赚到又还回去」） |

**千万别把关系说反**：本金分母一定 ≥ 峰值分母。
净值从 1.10 跌到 0.90，跌掉 0.20：
0.20 ÷ 1.00 = 20%，而 0.20 ÷ 1.10 = 18.18% —— 分母越小商越大。

只写「最大回撤 14.8%」时，默认指**复利·峰值分母**；要报别的必须写全口径。

【用户说「保存 / 存起来」时 —— 你能存，而且有四个不同的地方】

| 用户的话 | 用哪个工具 | 存到哪 |
|---|---|---|
| 「存到我的策略」「帮我收藏」 | `save_strategy` | 我的策略（只有自己看得到） |
| 「保存这套因子」「这组条件存起来」 | `save_factor_set` | 我的因子（只有条件，不带标的） |
| 「存到策略库」「发布到策略库」「让所有人都能用」 | `publish_strategy` | **公共策略库**（所有人可见） |
| 「加到因子库」「因子库里加一个」 | `publish_factor` | **公共因子库**（所有人可见） |

**绝对不要回答「我没有保存工具，你自己去点按钮」—— 那是错的，四个都能存。**

用户的说法经常很含糊，按这个规则判断：
- 只说「保存」没说去哪 → 默认 `save_strategy`（存自己的最安全）
- 明确说了「库」但没说「我的」→ 那就是公共库，用 `publish_*`
- 拿不准就问一句「存你自己的，还是放进公共库给大家用」

`publish_strategy` 是**先验证再入库**：服务端会拿真实行情跑一遍，
跑不通直接拒绝并告诉你原因。所以调用时把 `params` 和 `filters` 传全，
否则验证的是错的配置，入库的也是错的。
`publish_factor` 只能发布**引擎里已实现计算**的因子（trix、cmo、mass_index 这些），
不能凭空写一个公式进表 —— 写进去用户点开是空白图，那样是害人。

存完都要告诉用户具体去哪个页面看。

【报告模板里到底有什么 —— 照这个说，别凭印象】
- `kline`：**1 张可交互的 K 线图**（ECharts）—— 全部交易日都在里面、
  默认显示全量，滚轮缩放、按住左键拖动、悬停看当天开高低收、底部滑条可拖。
  和 /strategies 页面是同一套交互，**不要再跟用户说「报告里的图不能缩放」**
- `equity`：1 张复利净值图（策略 vs 买入持有）
- `simple`：1 张单利净值图（策略 vs 买入持有）
- `drawdown`：**3 张图** —— ① 三口径叠加折线（峰值/本金/单利同框，带图例和各自最深值）
  ② 峰值口径面积图 ③ 本金口径面积图，另附一张口径对照表
- `metrics`：三组指标卡（复利 / 单利 / 对比与质量）
- `trades`：逐笔交易表
- `hysteresis`：0/0.5/1/2/3/5% 回滞对比表

**「模板只能画一根回撤线」这句话已经是错的，绝对不许再说。**
`drawdown` 模块现在就是三张图。工具返回后先看 `rendered` 字段，它列了实际画出来的每张图；
用户追问「回撤有几个图」时，直接数 `rendered` 里的条目回答。

如果用户要的东西模板里真的没有（比如要他上传的自己的数据），
说清缺什么、能不能用现有模块凑，别用"模板做不到"一句挡回去。

【用户想「改参数看结果」时 —— 直接帮他改，不用让他去别处点】
用户说「回滞改成 3% 呢」「慢线换成 60 呢」「快线改 1 天试试」，
直接用 `auto_search_factors` 或 `run_backtest` 带上新参数跑一遍，
把结果和新的 K 线、买卖点、指标讲给他听。**不要让他自己去某个页面点。**

跑完如果他想留个能看的，再用 `build_report` 生成一份网页。

【用户想「看到图表」时】
这个产品本来就有完整的可视化，你不需要写代码，也不要回答"我只能给文字"：
- 每次回测，结果卡片里自带净值曲线和指标
- 想看图看得全（K 线买卖点、复利单利两套净值、回撤面积），就调 `build_report`
- 报告卡片上可以直接「在这里预览」，也能下载 HTML 存到本地

所以正确回答是：「我把这几块都放进报告里了，下面点开就能看」，
而不是"我没法画图"。也不要建议用户自己用 Excel 画、或者装什么软件。

【优化策略有三个方向，不止调参】
1. **调参** → suggest_params：同一个框架内找更合适的数字
2. **加因子过滤** → design_strategy：从因子库的数值因子和布尔因子里选，
   设阈值组合成条件。因子清单见该工具的说明。
3. **防过拟合** → check_robustness：分前后两段验证 + 参数敏感性网格，
   返回风险评级。用户问「靠不靠谱」「能不能上实盘」时用。

用户说「帮我优化」时，不要只想到调参。先判断他的诉求属于哪一类：
- "亏太多/想减少交易" → 加因子过滤
- "这个结果可信吗/会不会是过拟合" → 稳健性检查
- "参数还能不能调整" → 调参
也可以三个都做，按「调参 → 加因子 → 验稳健性」的顺序，一步步给结论。

【策略修改的边界】
**绝对不要说「引擎做不到」。** 用户提的改进想法，先想办法用因子条件表达出来。

正确的做法顺序（很重要，决定回答质量）：

**第一步：先让机器去找，别自己拍数字。**
用户说「优化一下」「怎么才能少亏」「用什么因子好」时，第一件事是调
`auto_search_factors`。它会真的遍历 16 个因子、用这只票的真实分布取阈值、
跑几百次回测，选出最有效的组合。你不需要自己猜 rsi=70 这种通用值。

**第二步：只有当用户点名要某个因子时，才手工构造条件。**
用户说「用 RSI 试试」「必须留 ADX」→ 调 auto_search_factors 并传 only_factors，
或者用 design_strategy 手工指定。这时可以参考的含义映射：
- "震荡期不投入"   → adx 高于某个值（具体多少让搜索器定，不是 20）
- "只在上升趋势做" → price_above_ma，n=60
- "别追高"         → rsi 低于某个值
- "要放量"         → volume_ratio 高于某个值
- "波动太大不做"   → atr_pct 低于某个值

**第三步：必须主动报告过拟合风险。**
auto_search_factors 会返回 overfit_risk（低/中/高）和 validation（样本外表现）。
- 如果 risk 是「高」或 validation.degraded 为 true，**必须**直说：
  "这组条件在样本外明显变差，大概率是拟合出来的，不建议直接用"
- 即使风险是「低」，也要提一句"这是这段历史上的最优解，换标的可能失效"
- **禁止只报改善后的漂亮数字而不提风险** —— 那是欺骗用户

如果搜索器给出的条件和用户直觉相反（比如用户要"震荡期不投入"，
但搜索结果显示"ADX 低时买入更好"），**如实说明这个反直觉的发现**，
并解释可能的原因（如这只票在震荡市里更适用均值回归），不要强行圆成用户想听的话。

所有条件只限制买入，不影响卖出。真映射不了（要新增我们没算过的指标）才如实说明，
并给出最接近的替代方案。

仍然不允许：写可执行代码、承诺"独家策略"、预测未来涨跌。

【风险提示】
每次给出回测结果后，用一句话提醒：
历史表现不代表未来，回测收益通常高于实盘。
如果结果里有 blocked_orders（涨跌停无法成交次数），要解释这意味着什么。

【用户引用策略或因子时】
用户可能用【策略：名称】【因子：名称】的形式点名要某些东西，比如：
「【策略：5/20 均线金叉】【因子：RSI】帮我组合这些」
遇到这种：
1. 先用 search_strategies 确认这些策略的真实信息，不要凭空猜参数。
2. 因子只是概念（如 RSI、布林带、量比），现有引擎不直接支持任意组合。
   要诚实说明：「因子组合需要新的策略逻辑，目前引擎只支持这几种内置策略」，
   然后把话题引导到能用现有框架做到的事情上：
   - 调整参数（suggest_params）
   - 对比几个策略在同一只股票上的表现（compare_strategies）
   - 在某个内置策略上做单因子过滤（比如"只在 RSI 不高时买入"这种需要开发，不能假装能做）
3. 不要说"我已经帮你组合好了"——除非真的调用了工具并拿到结果。

【用户想「组合两个策略」时】
这不是让你把两套规则硬拼成一个策略（那样冲突怎么处理没有标准答案）。
正确做法是调 analyze_pair，拿到三组事实后再分析：
- 日收益相关性：>0.75 基本同涨同跌，组合没意义；<0.3 才算真互补
- 持仓冲突天数：两个策略一个想买一个想卖的占比
- 等权组合的真实回测：合起来到底比单干好还是差
然后告诉用户：
1. 它们是不是真的互补（用数字说话，别空谈）
2. 如果互补，等权组合的实际表现如何
3. 如果不互补，说明原因，并建议换成哪类策略搭配更合理
4. 提醒：组合不是简单相加，还涉及仓位分配和交易成本翻倍
**永远不要凭空说"这两个策略互补"或"组合后收益会提升"——必须有 analyze_pair 的数据支撑。**

【专业性问题必须查知识库】
我们的知识库有 28 篇专业内容，覆盖：过拟合、未来函数、幸存者偏差、样本外测试、交易成本、
仓位管理、RSI 失效、布林带用法、ATR 止损、ADX 判断趋势、量比换手率、
趋势/回归策略适用环境、组合相关性、涨跌停影响、T+1、回撤三种口径、夏普局限、胜率陷阱、参数敏感性。
用户问这类问题时：
1. 先调 explain_concept 检索，不要凭自己的记忆讲——知识库里的口径和我们产品指标一致
2. 回答时可以说「我们的知识库里是这样写的」，让用户知道这不是随口说的
3. 检索不到就直接说没查到，不要编一个看起来专业的答案

【计费提示】
如果用户问价格，问答按实际消耗扣积分，回测现在免费，都有明确上限，
不存在无上限扣费。
"""


# ============================================================
# 工具执行结果
# ============================================================
@dataclass
class ToolResult:
    ok: bool
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    # 前端要渲染的卡片类型，模型不需要看这个字段
    ui_card: Optional[str] = None

    def for_model(self) -> str:
        """塞回给模型的内容：只给数据，不给 UI 信息"""
        if self.ok:
            return json.dumps(self.payload, ensure_ascii=False)
        return json.dumps({"error": self.error}, ensure_ascii=False)


# ============================================================
# 执行器
# ============================================================
class ToolExecutor:
    """
    把模型发来的 tool_call 翻译成真实的数据库查询和回测。
    ctx 里放数据库连接等依赖，方便测试时替换成内存实现。
    """

    def __init__(self, ctx: "ToolContext"):
        self.ctx = ctx
        # 由路由层在每次调用前塞进来：生成报告要记归属
        self.user_id: Optional[int] = None

    def execute(self, name: str, args: Dict[str, Any],
                user_id: Optional[int] = None) -> ToolResult:
        if user_id is not None:
            self.user_id = user_id
        handler: Optional[Callable[[Dict[str, Any]], ToolResult]] = {
            "search_strategies": self._search_strategies,
            "run_backtest": self._run_backtest,
            "compare_strategies": self._compare,
            "analyze_pair": self._analyze_pair,
            "design_strategy": self._design_strategy,
            "auto_search_factors": self._auto_search_factors,
            "list_factors": self._list_factors,
            "prepare_deploy": self._prepare_deploy,
            "search_web": self._search_web,
            "build_report": self._build_report,
            "save_strategy": self._save_strategy,
            "save_factor_set": self._save_factor_set,
            "publish_strategy": self._publish_strategy,
            "publish_factor": self._publish_factor,
            "check_robustness": self._check_robustness,
            "list_symbols": self._list_symbols,
            "explain_concept": self._explain,
            "suggest_params": self._suggest_params,
        }.get(name)
        if handler is None:
            return ToolResult(ok=False, error=f"未知工具 {name}")
        try:
            return handler(args or {})
        except Exception as e:                      # 工具失败也要让模型知道，别静默
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")

    # ---------------- 各工具实现 ----------------
    def _search_strategies(self, a: Dict[str, Any]) -> ToolResult:
        rows = self.ctx.search_strategies(
            category=a.get("category"),
            difficulty=a.get("difficulty"),
            keyword=a.get("keyword"),
            limit=int(a.get("limit", 3)),
        )
        if not rows:
            return ToolResult(ok=True, payload={
                "strategies": [],
                "hint": "没有匹配的策略，请放宽条件或换分类再试",
            })
        return ToolResult(
            ok=True,
            ui_card="strategy_list",
            payload={
                "strategies": [
                    {k: r[k] for k in ("code", "name", "category", "summary", "difficulty")}
                    for r in rows
                ],
                "next_step": "选一个调 run_backtest 验证历史表现后再推荐给用户",
            },
        )

    def _run_backtest(self, a: Dict[str, Any]) -> ToolResult:
        code = a["strategy_code"]
        symbol = a["symbol_code"]
        strat = self.ctx.get_strategy(code)
        if not strat:
            return ToolResult(ok=False, error=f"策略 {code} 不存在，先 search_strategies")
        bars = self.ctx.get_bars(symbol, int(a.get("years", 3)))
        if len(bars) < 30:
            return ToolResult(ok=False, error=f"{symbol} 数据不足或不在标的池内")

        params = dict(strat["default_params"])
        params.update(a.get("params") or {})

        # 过滤条件有两个来源，优先级：调用方传的 > 策略库里自带的。
        # 这里之前完全没传 filters —— 用户刚调好的「RSI + ADX>20.5」，
        # 一调 run_backtest 验证就变回裸 RSI，卡片上的数字和刚才说的对不上。
        filters = a.get("filters")
        if not filters:
            filters = strat.get("filters") or None
        r = run_backtest(bars, strat["strategy_type"], params, filters=filters or None)

        # 抽样净值曲线塞进卡片，前端能直接画出图，
        # 这样用户在对话里就"看得到曲线"，不用跳页面。
        def sample(seq, n=60, is_num=True):
            if not seq:
                return []
            step = max(1, len(seq) // n)
            idx = list(range(0, len(seq), step))
            if idx[-1] != len(seq) - 1:
                idx.append(len(seq) - 1)
            # 日期是字符串，不能 round —— 之前这里直接 round，
            # 导致回测工具整体报 "type str doesn't define __round__"
            return [round(seq[i], 4) if is_num else seq[i] for i in idx]

        sample_dates = sample(r.dates, 60, is_num=False)

        return ToolResult(
            ok=True,
            ui_card="backtest",
            payload={
                "strategy": strat["name"],
                "strategy_code": strat.get("code") or code,
                "strategy_type": strat["strategy_type"],
                "symbol": symbol,
                "symbol_code": symbol,
                "params": params,
                "range": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
                "data_cutoff": DATA_CUTOFF_NOTE,
                "params": params,
                "total_return_pct": round(r.total_return * 100, 2),
                "annual_return_pct": round(r.annual_return * 100, 2),
                "benchmark_return_pct": round(r.benchmark_return * 100, 2),
                "excess_return_pct": round(r.excess_return * 100, 2),
                "beat_benchmark": r.total_return > r.benchmark_return,
                "max_drawdown_pct": round(r.max_drawdown * 100, 2),
                "max_drawdown_principal_pct": round(r.max_drawdown_principal * 100, 2),
                "filters": filters or [],
                "filters_desc": r.filters_desc,
                "sharpe": round(r.sharpe, 2),
                "win_rate_pct": round(r.win_rate * 100, 1),
                "trade_count": r.trade_count,
                "exposure_pct": round(r.exposure * 100, 1),
                "blocked_orders": r.blocked_orders,
                "latest_signal_explain": explain_signal(
                    bars, strat["strategy_type"], params),
                # 给前端画小图用
                "sample_dates": sample_dates,
                "equity_sample": sample(r.equity, 60),
                "benchmark_sample": sample(r.benchmark_curve, 60),
                "drawdown_sample": sample(r.drawdown_curve, 60),
            },
        )

    def _compare(self, a: Dict[str, Any]) -> ToolResult:
        items = []
        for code in a["strategy_codes"][:4]:
            r = self._run_backtest({
                "strategy_code": code,
                "symbol_code": a["symbol_code"],
                "years": a.get("years", 3),
            })
            if r.ok:
                p = r.payload
                items.append({k: p[k] for k in (
                    "strategy", "total_return_pct", "benchmark_return_pct",
                    "max_drawdown_pct", "sharpe", "win_rate_pct", "trade_count")})
            else:
                items.append({"strategy": code, "error": r.error})
        return ToolResult(ok=True, ui_card="compare", payload={"comparison": items})

    def _analyze_pair(self, a: Dict[str, Any]) -> ToolResult:
        codes = [c for c in (a.get("strategy_codes") or []) if c]
        if len(codes) != 2:
            return ToolResult(ok=False, error="analysis_pair 需要正好两个策略代码")

        s1 = self.ctx.get_strategy(codes[0])
        s2 = self.ctx.get_strategy(codes[1])
        if not s1 or not s2:
            missing = codes[0] if not s1 else codes[1]
            return ToolResult(ok=False, error=f"没找到策略 {missing}，请先用 search_strategies 查准确的 code")

        symbol = a["symbol_code"]
        years = int(a.get("years", 3))
        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 30:
            return ToolResult(ok=False, error=f"{symbol} 数据不足，无法分析")

        def merge(s):
            p = dict(s.get("default_params") or {})
            return p

        p1, p2 = merge(s1), merge(s2)
        p1.update(a.get("params_a") or {})
        p2.update(a.get("params_b") or {})

        res = analyze_pair(bars, s1["strategy_type"], p1,
                           s2["strategy_type"], p2,
                           float(a.get("weight_a", 0.5)),
                           filters_a=(a.get("filters_a") or s1.get("filters") or None),
                           filters_b=(a.get("filters_b") or s2.get("filters") or None))
        res["symbol_code"] = symbol
        res["strategy_codes"] = codes
        res["a"]["code"] = codes[0]
        res["b"]["code"] = codes[1]

        return ToolResult(ok=True, ui_card="pair_analysis", payload=res)

    def _design_strategy(self, a: Dict[str, Any]) -> ToolResult:
        """
        基础策略 + 过滤条件 = 一个新设计的策略。
        同时跑对照组（不加过滤），让用户看到「改了什么、好了多少」。
        """
        code = a["strategy_code"]
        strat = self.ctx.get_strategy(code)
        if not strat:
            return ToolResult(ok=False, error=f"没找到策略 {code}，请先用 search_strategies 查准确 code")

        symbol = a["symbol_code"]
        years = int(a.get("years", 3))
        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 60:
            return ToolResult(ok=False, error=f"{symbol} 数据不足，无法设计策略")

        params = dict(strat.get("default_params") or {})
        params.update(a.get("params") or {})

        filters = a.get("filters") or []
        if not filters:
            return ToolResult(ok=False, error="至少需要一个过滤条件")

        base = run_backtest(bars, strat["strategy_type"], params,
                            filters=(strat.get("filters") or None))
        try:
            tuned = run_backtest(bars, strat["strategy_type"], params, filters=filters)
        except Exception as e:
            return ToolResult(ok=False, error=f"叠加过滤条件时出错：{e}")

        def pack(r):
            return {
                "total_return_pct": round(r.total_return * 100, 2),
                "annual_return_pct": round(r.annual_return * 100, 2),
                "max_drawdown_pct": round(r.max_drawdown * 100, 2),
                "max_drawdown_principal_pct": round(r.max_drawdown_principal * 100, 2),
                "filters": filters or [],
                "filters_desc": r.filters_desc,
                "sharpe": round(r.sharpe, 2),
                "win_rate_pct": round(r.win_rate * 100, 1),
                "trade_count": r.trade_count,
                "exposure_pct": round(r.exposure * 100, 1),
            }

        b, t = pack(base), pack(tuned)
        payload = {
            "code": "design_result",
            "symbol_code": symbol,
            "strategy": strat["name"],
            "params": params,
            "period": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
            "benchmark_return_pct": round((bars[-1].close / bars[0].close - 1) * 100, 2),
            "base": b,
            "designed": t,
            "filters": tuned.filter_stats,
            "filters_desc": tuned.filters_desc,
            "delta": {
                "return_pct": round(t["total_return_pct"] - b["total_return_pct"], 2),
                "drawdown_pct": round(t["max_drawdown_pct"] - b["max_drawdown_pct"], 2),
                "trades": t["trade_count"] - b["trade_count"],
                "exposure_pct": round(t["exposure_pct"] - b["exposure_pct"], 1),
            },
            "note": "过滤条件只限制买入，卖出不受影响，所以不会出现该止损却卖不掉的情况",
        }
        return ToolResult(ok=True, ui_card="design_result", payload=payload)

    def _auto_search_factors(self, a: Dict[str, Any]) -> ToolResult:
        """
        自动在因子库里穷举搜索，选出与用户目标最匹配的因子组合。
        这才是「AI 自己去找因子」，而不是从提示词里背几个固定映射。
        """
        try:
            from .factor_search import search_factors
        except ImportError:
            from factor_search import search_factors

        code = a["strategy_code"]
        strat = self.ctx.get_strategy(code)
        if not strat:
            return ToolResult(ok=False, error=f"没找到策略 {code}，请先用 search_strategies 查准确 code")

        symbol = a["symbol_code"]
        years = int(a.get("years", 3))
        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 120:
            return ToolResult(ok=False, error=f"{symbol} 数据不足，至少需要 120 根 K 线")

        params = dict(strat.get("default_params") or {})
        params.update(a.get("params") or {})

        goal = a.get("goal") or "balanced"
        if goal not in ("balanced", "return", "drawdown", "sharpe"):
            goal = "balanced"
        max_factors = max(1, min(int(a.get("max_factors", 3)), 4))
        only = a.get("only_factors") or None

        try:
            r = search_factors(bars, strat["strategy_type"], params,
                               goal=goal, max_factors=max_factors,
                               only_factors=only,
                               custom_factors=a.get("custom_factors") or None,
                               user_text=a.get("user_text") or "")
        except Exception as e:
            return ToolResult(ok=False, error=f"因子搜索失败：{e}")

        payload = {
            "code": "factor_search",
            "strategy": strat["name"],
            "strategy_code": code,
            "strategy_type": strat["strategy_type"],
            "symbol_code": symbol,
            "params": params,
            "goal": goal,
            "goal_cn": {"balanced": "收益与回撤平衡", "return": "收益优先",
                        "drawdown": "回撤优先", "sharpe": "夏普优先"}.get(goal, goal),
            "period": f"{r['sample']['start']} ~ {r['sample']['end']}",
            "bars": r["sample"]["bars"],
            "baseline": r["baseline"],
            "result": r["result"],
            "improvement": r["improvement"],
            "conditions": r["conditions"],
            "condition_text": r["condition_text"],
            "steps": r["steps"],
            "validation": r["validation"],
            "contributions": r["contributions"],
            "overfit_risk": r["overfit_risk"],
            "searched_factors": r["searched_factors"],
            "plan": r.get("plan"),
            "custom_factors": r.get("custom_factors"),
            "note": r["note"],
        }
        return ToolResult(ok=True, ui_card="factor_search", payload=payload)

    def _list_factors(self, a: Dict[str, Any]) -> ToolResult:
        """把因子库全貌给 AI，避免它凭记忆编不存在的因子"""
        try:
            from .factor_engine import list_capabilities
        except ImportError:
            from factor_engine import list_capabilities
        caps = list_capabilities()
        return ToolResult(ok=True, ui_card="factor_list", payload={
            "code": "factor_list",
            **caps,
            "count": len(caps.get("numeric_factors", [])) + len(caps.get("boolean_factors", [])),
        })

    def _save_strategy(self, a: Dict[str, Any]) -> ToolResult:
        """
        把当前策略配置存进「我的策略」。

        用户说「保存这个策略」「存到我的策略里」「帮我收藏一下」时调用。
        **不要回答"我没有这个工具、你自己去点按钮"** —— 能存，就在这里存。

        存的不是一次回测记录的引用，而是当场重跑一遍后的指标快照，
        这样即便回测记录被清理，用户收藏的数据还在。
        """
        import time as _t
        import uuid as _uuid
        try:
            from sqlalchemy import text as _text
            from ..db import engine as _engine
        except ImportError:
            from sqlalchemy import text as _text
            from app.db import engine as _engine

        if not self.user_id:
            return ToolResult(ok=False, error="没有识别到用户，无法保存")

        code = (a.get("strategy_code") or "").strip()
        strat = self.ctx.get_strategy(code) if code else None
        stype = (strat or {}).get("strategy_type") or a.get("strategy_type") or "ma_cross"
        symbol = (a.get("symbol_code") or "600519").strip()
        years = max(1, min(int(a.get("years") or 3), 10))
        params = dict((strat or {}).get("default_params") or {})
        params.update(a.get("params") or {})
        filters = a.get("filters") or []

        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 30:
            return ToolResult(ok=False, error=f"{symbol} 的行情数据不足，存不了")

        try:
            r = run_backtest(bars, stype, params, filters=filters or None)
        except Exception as e:
            return ToolResult(ok=False, error=f"回测跑不通，没保存：{e}")

        snapshot = {
            "total_return": round(r.total_return, 6),
            "annual_return": round(r.annual_return, 6),
            "max_drawdown": round(r.max_drawdown, 6),
            "max_drawdown_principal": round(r.max_drawdown_principal, 6),
            "max_drawdown_loss": round(r.max_drawdown_loss, 6),
            "simple_return": round(r.simple_return, 6),
            "simple_annual_return": round(r.simple_annual_return, 6),
            "simple_max_drawdown": round(r.simple_max_drawdown, 6),
            "simple_max_drawdown_peak": round(r.simple_max_drawdown_peak, 6),
            "simple_max_drawdown_principal": round(r.simple_max_drawdown_principal, 6),
            "simple_max_drawdown_loss": round(r.simple_max_drawdown_loss, 6),
            "sharpe": round(r.sharpe, 4),
            "win_rate": round(r.win_rate, 4),
            "trade_count": r.trade_count,
            "benchmark_return": round(r.benchmark_return, 6),
            "excess_return": round(r.excess_return, 6),
            "strategy_name": (strat or {}).get("name") or stype,
            "strategy_type": stype,
            "symbol_code": symbol,
            "params": params,
            "filters": filters,
            "filters_desc": r.filters_desc,
            "data_cutoff": bars[-1].trade_date,
            "range": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
        }

        name = (a.get("name") or "").strip()
        if not name:
            name = f"{symbol} {snapshot['strategy_name']}"
            if r.filters_desc:
                # 只取条件主体，别把整句（含括号参数）塞进名字
                name += f" + {r.filters_desc.split('（')[0][:24]}"
        name = name[:80]

        job_no = "M" + _t.strftime("%Y%m%d%H%M%S") + _uuid.uuid4().hex[:4].upper()

        try:
            with _engine.begin() as conn:
                sid = conn.execute(_text(
                    "SELECT id FROM strategies WHERE strategy_type=:t LIMIT 1"
                ), {"t": stype}).scalar()
                if not sid:
                    return ToolResult(ok=False, error="策略库里没有可挂靠的基础策略")
                conn.execute(_text(
                    "INSERT INTO saved_strategies (user_id, name, strategy_id, symbol_code, "
                    " params, job_no, snapshot, note) "
                    "VALUES (:u,:n,:sid,:sym,:p,:j,:sn,:note)"
                ), {
                    "u": self.user_id, "n": name, "sid": sid, "sym": symbol,
                    "p": json.dumps(params, ensure_ascii=False),
                    "j": job_no,
                    "sn": json.dumps(snapshot, ensure_ascii=False),
                    "note": (a.get("note") or "")[:500],
                })
                row_id = conn.execute(_text(
                    "SELECT id FROM saved_strategies WHERE job_no=:j"
                ), {"j": job_no}).scalar()
        except Exception as e:
            return ToolResult(ok=False, error=f"写库失败：{e}")

        return ToolResult(ok=True, ui_card="saved_strategy", payload={
            "code": "saved_strategy",
            "id": row_id,
            "name": name,
            "symbol_code": symbol,
            "strategy_name": snapshot["strategy_name"],
            "filters_desc": r.filters_desc,
            "total_return_pct": round(r.total_return * 100, 2),
            "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            "trade_count": r.trade_count,
            "page": "/saved",
            "hint": "已存进「我的策略」，右上角「我的 → 我的策略」能随时打开",
        })

    def _save_factor_set(self, a: Dict[str, Any]) -> ToolResult:
        """
        把一组因子条件存进「我的因子」（saved_factor_sets）。

        为什么不和 save_strategy 合并：
        这里存的是**条件**（ADX>20.5 + 量比>1.2），可以套到任何策略上；
        save_strategy 存的是**完整配置**（标的 + 参数 + 条件），直接能跑。
        两者对应产品里两个不同的页面，混在一起用户会找不到。
        """
        try:
            from sqlalchemy import text as _text
            from ..db import engine as _engine
        except ImportError:
            from sqlalchemy import text as _text
            from app.db import engine as _engine

        if not self.user_id:
            return ToolResult(ok=False, error="没有识别到用户，无法保存")

        name = (a.get("name") or "").strip()
        conditions = a.get("conditions") or []
        if not name:
            return ToolResult(ok=False, error="缺少 name（给这组条件起个名字）")
        if not conditions:
            return ToolResult(ok=False, error="至少要有一个因子条件才能保存")

        # 校验每个条件都能算出数，存进去的东西必须真能用
        try:
            from .factor_engine import apply_conditions
        except ImportError:
            from factor_engine import apply_conditions
        try:
            bars = self.ctx.get_bars(a.get("symbol_code") or "600519", 2)
            _, stats = apply_conditions(bars, [1] * len(bars), conditions)
            cond_text = (a.get("condition_text") or "").strip() or "；".join(
                x.get("desc", "") for x in stats if x.get("desc"))
        except Exception as e:
            return ToolResult(ok=False, error=f"这组条件用不了：{e}")

        with _engine.begin() as conn:
            old = conn.execute(_text(
                "SELECT id FROM saved_factor_sets WHERE user_id=:u AND name=:n"
            ), {"u": self.user_id, "n": name[:80]}).mappings().first()
            payload = {
                "sc": (a.get("strategy_code") or "")[:48],
                "sym": (a.get("symbol_code") or "")[:10],
                "p": json.dumps(a.get("params") or {}, ensure_ascii=False),
                "c": json.dumps(conditions, ensure_ascii=False),
                "ct": cond_text[:300],
                "sn": json.dumps(a.get("snapshot") or {}, ensure_ascii=False),
                "note": (a.get("note") or "")[:500],
            }
            if old:
                conn.execute(_text(
                    "UPDATE saved_factor_sets SET strategy_code=:sc, symbol_code=:sym, "
                    "params=:p, conditions=:c, condition_text=:ct, snapshot=:sn, note=:note "
                    "WHERE id=:i"
                ), {**payload, "i": old["id"]})
                fid, action = old["id"], "updated"
            else:
                conn.execute(_text(
                    "INSERT INTO saved_factor_sets (user_id, name, strategy_code, symbol_code, "
                    " params, conditions, condition_text, snapshot, note) "
                    "VALUES (:u,:n,:sc,:sym,:p,:c,:ct,:sn,:note)"
                ), {**payload, "u": self.user_id, "n": name[:80]})
                fid = conn.execute(_text(
                    "SELECT id FROM saved_factor_sets WHERE user_id=:u AND name=:n"
                ), {"u": self.user_id, "n": name[:80]}).scalar()
                action = "created"

        return ToolResult(ok=True, ui_card="saved", payload={
            "kind": "factor_set",
            "id": fid,
            "name": name,
            "action": action,
            "condition_text": cond_text,
            "conditions": conditions,
            "page": "/my-factors",
            "hint": "已存进「我的因子」，右上角「我的 → 我的因子」能翻回来",
        })

    # ============================================================
    # 发布到公共库
    # ============================================================
    def _publish_to_library(self, a: Dict[str, Any]) -> ToolResult:
        kind = (a.get("kind") or "strategy").strip().lower()
        if kind.startswith("f") or kind == "因子":
            return self._publish_factor(a)
        return self._publish_strategy(a)

    def _publish_strategy(self, a: Dict[str, Any]) -> ToolResult:
        """
        把一套策略配置发布到**公共策略库**（所有用户都能看到并回测）。

        和 save_strategy 的区别，必须分清：
          save_strategy  → 「我的策略」，只有自己看得到，不需要校验
          publish_strategy → 公共策略库，别人也会照着跑，所以**必须真跑通**才能入库

        入库前会：
          1. 校验 strategy_type 是引擎支持的类型
          2. 用 validate_params 把参数清洗到合法范围
          3. 拿真实行情跑一遍回测 —— 跑不通直接拒绝，绝不写半成品进公共库
        """
        import re as _re
        try:
            from sqlalchemy import text as _text
            from ..db import engine as _engine
        except ImportError:
            from sqlalchemy import text as _text
            from app.db import engine as _engine
        try:
            from .lab import validate_params, spec_for
            from .backtest import SIGNAL_FUNCS, STRATEGY_NAMES
        except ImportError:
            from lab import validate_params, spec_for
            from backtest import SIGNAL_FUNCS, STRATEGY_NAMES

        if not self.user_id:
            return ToolResult(ok=False, error="没有识别到用户，无法发布")

        code = (a.get("code") or "").strip().lower()
        name = (a.get("name") or "").strip()
        stype = (a.get("strategy_type") or "").strip()
        if not code:
            return ToolResult(ok=False, error="缺少 code（策略的英文标识，比如 rsi_adx_14）")
        if not _re.fullmatch(r"[a-z0-9_]{3,48}", code):
            return ToolResult(ok=False, error="code 只能用 3~48 位小写字母、数字、下划线")
        if not name:
            return ToolResult(ok=False, error="缺少 name（给用户看的中文名）")
        if stype not in SIGNAL_FUNCS:
            return ToolResult(ok=False, error=(
                f"策略库不支持 {stype} 这种类型。当前支持："
                + "、".join(f"{k}（{v}）" for k, v in STRATEGY_NAMES.items())
            ))

        try:
            params = validate_params(stype, a.get("params") or {})
        except Exception as e:
            return ToolResult(ok=False, error=f"参数不合法：{e}")

        schema = {}
        for item in spec_for(stype)["params"]:
            d = {"type": item["type"], "label": item["label"]}
            for k in ("min", "max"):
                if k in item:
                    d[k] = item[k]
            if item["key"] in params:
                d["default"] = params[item["key"]]
            schema[item["key"]] = d

        filters = a.get("filters") or []
        symbol = (a.get("symbol_code") or "600519").strip()
        years = max(1, min(int(a.get("years") or 3), 10))

        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 30:
            return ToolResult(ok=False, error=f"{symbol} 行情不足 30 根 K 线，无法验证")
        try:
            r = run_backtest(bars, stype, params, filters=filters or None)
        except Exception as e:
            return ToolResult(ok=False, error=f"这套配置跑不通，不能进公共库：{e}")

        # 公共库是所有人共用的，覆盖别人写的东西必须拦住
        with _engine.begin() as conn:
            old = conn.execute(_text(
                "SELECT id, owner_user_id, name FROM strategies WHERE code=:c"
            ), {"c": code}).mappings().first()
            if old:
                if old["owner_user_id"] != self.user_id:
                    return ToolResult(ok=False, error=(
                        f"策略库里已经有 code 为 {code} 的策略（{old['name']}），"
                        f"那是内置或别人建的。换个 code，比如 {code}_v2"
                    ))
                conn.execute(_text(
                    "UPDATE strategies SET name=:n, category=:cat, strategy_type=:t, "
                    "summary=:su, detail=:de, param_schema=:ps, default_params=:dp, "
                    "filters=:f, difficulty=:df, tags=:tg WHERE id=:i"
                ), {
                    "n": name, "cat": (a.get("category") or "趋势")[:24],
                    "t": stype, "su": (a.get("summary") or "")[:200],
                    "de": (a.get("detail") or "")[:4000],
                    "ps": json.dumps(schema, ensure_ascii=False),
                    "dp": json.dumps(params, ensure_ascii=False),
                    "f": json.dumps(filters, ensure_ascii=False) if filters else None,
                    "df": max(1, min(int(a.get("difficulty") or 2), 5)),
                    "tg": (a.get("tags") or "")[:120], "i": old["id"],
                })
                sid, action = old["id"], "updated"
            else:
                conn.execute(_text(
                    "INSERT INTO strategies (code, name, category, strategy_type, summary, "
                    " detail, param_schema, default_params, filters, difficulty, tags, "
                    " owner_user_id, is_public, use_count) "
                    "VALUES (:c,:n,:cat,:t,:su,:de,:ps,:dp,:f,:df,:tg,:u,1,0)"
                ), {
                    "c": code, "n": name, "cat": (a.get("category") or "趋势")[:24],
                    "t": stype, "su": (a.get("summary") or "")[:200],
                    "de": (a.get("detail") or "")[:4000],
                    "ps": json.dumps(schema, ensure_ascii=False),
                    "dp": json.dumps(params, ensure_ascii=False),
                    "f": json.dumps(filters, ensure_ascii=False) if filters else None,
                    "df": max(1, min(int(a.get("difficulty") or 2), 5)),
                    "tg": (a.get("tags") or "")[:120], "u": self.user_id,
                })
                sid = conn.execute(_text(
                    "SELECT id FROM strategies WHERE code=:c"), {"c": code}).scalar()
                action = "created"

        return ToolResult(ok=True, ui_card="published", payload={
            "kind": "strategy",
            "code": code,
            "id": sid,
            "name": name,
            "action": action,
            "page": "/strategies",
            "strategy_name": STRATEGY_NAMES.get(stype, stype),
            "filters_desc": r.filters_desc,
            # 入库前真跑出来的数字，用户可以拿去和详情页对照
            "verified_on": symbol,
            "total_return_pct": round(r.total_return * 100, 2),
            "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            "trade_count": r.trade_count,
            "hint": f"已发布到公共策略库，策略库页面能看到（验证标的 {symbol}，"
                    f"收益 {round(r.total_return*100,2)}%）",
        })

    def _publish_factor(self, a: Dict[str, Any]) -> ToolResult:
        """
        把因子补进**公共因子库**的目录。

        只能发布引擎里**已经实现**的计算 ——
        factors 表只是目录，真正的算法在 factor_engine / public_indicators 里。
        凭空写一行公式进表里，前端点开会画不出任何东西，那才是真正的坑。
        所以：code 必须在 NUMERIC/BOOLEAN 里能找到，并且真算一遍有足够有效值。
        """
        import re as _re
        try:
            from sqlalchemy import text as _text
            from ..db import engine as _engine
        except ImportError:
            from sqlalchemy import text as _text
            from app.db import engine as _engine
        try:
            from . import factor_engine as fe
        except ImportError:
            import factor_engine as fe

        if not self.user_id:
            return ToolResult(ok=False, error="没有识别到用户，无法发布")

        code = (a.get("code") or "").strip().lower()
        name = (a.get("name") or "").strip()
        if not code:
            return ToolResult(ok=False, error=(
                "缺少 code。只能发布引擎里已实现计算的因子，"
                "数值因子：" + "、".join(sorted(fe.NUMERIC)[:12]) + " 等；"
                "布尔因子：" + "、".join(sorted(fe.BOOLEAN)[:6]) + " 等"
            ))
        if not _re.fullmatch(r"[a-z0-9_]{2,40}", code):
            return ToolResult(ok=False, error="code 只能用 2~40 位小写字母、数字、下划线")

        spec = fe.NUMERIC.get(code) or fe.BOOLEAN.get(code)
        if not spec:
            return ToolResult(ok=False, error=(
                f"引擎里没有叫 {code} 的因子计算，不能写进因子库 —— "
                f"写进去用户点开会是空白图。可选：{'、'.join(sorted(fe.NUMERIC)[:15])}…"
            ))

        # 真算一遍，确认能出数
        try:
            bars = self.ctx.get_bars(a.get("symbol_code") or "600519", 2)
            series = spec["fn"](bars, dict(spec.get("defaults") or {}))
            valid = sum(1 for x in series if x is not None)
        except Exception as e:
            return ToolResult(ok=False, error=f"这个因子算不出数，不能入库：{e}")
        if valid < 50:
            return ToolResult(ok=False, error=f"只算出 {valid} 个有效点，数据太少，不能入库")

        if not name:
            name = spec["label"]

        with _engine.begin() as conn:
            old = conn.execute(_text("SELECT id FROM factors WHERE code=:c"),
                               {"c": code}).mappings().first()
            payload = {
                "code": code, "name": name[:40],
                "cat": (a.get("category") or "其他")[:20],
                "su": (a.get("summary") or spec["label"])[:200],
                "fm": (a.get("formula") or "")[:300],
                "un": (a.get("usage_note") or "")[:300],
                "ph": (a.get("params_hint")
                       or "、".join(f"{k}={v}" for k, v in (spec.get("defaults") or {}).items()))[:120],
            }
            if old:
                conn.execute(_text(
                    "UPDATE factors SET name=:name, category=:cat, summary=:su, "
                    "formula=:fm, usage_note=:un, params_hint=:ph, active=1 WHERE id=:i"
                ), {**payload, "i": old["id"]})
                fid, action = old["id"], "updated"
            else:
                mx = conn.execute(_text("SELECT COALESCE(MAX(sort_no),0) FROM factors")).scalar() or 0
                conn.execute(_text(
                    "INSERT INTO factors (code, name, category, summary, formula, "
                    " usage_note, params_hint, sort_no, active) "
                    "VALUES (:code,:name,:cat,:su,:fm,:un,:ph,:so,1)"
                ), {**payload, "so": int(mx) + 1})
                fid = conn.execute(_text("SELECT id FROM factors WHERE code=:c"),
                                   {"c": code}).scalar()
                action = "created"

        return ToolResult(ok=True, ui_card="published", payload={
            "kind": "factor",
            "code": code,
            "id": fid,
            "name": name,
            "action": action,
            "page": "/factors",
            "valid_points": valid,
            "hint": f"已进公共因子库，因子库页面能看到（实测 {valid} 个有效点）",
        })

    def _build_report(self, a: Dict[str, Any]) -> ToolResult:
        """
        现场生成一份可打开的网页报告。
        AI 决定「长什么样」（标题/模块/重点/结论），系统填真实数据。
        """
        try:
            from ..routers.reports import create_report, BuildIn
        except ImportError:
            from app.routers.reports import create_report, BuildIn

        if not self.user_id:
            return ToolResult(ok=False, error="没有识别到用户，无法生成报告")

        code = a.get("strategy_code") or ""
        strat = self.ctx.get_strategy(code) if code else None
        if code and not strat:
            return ToolResult(ok=False, error=f"没找到策略 {code}，先 search_strategies 确认")

        params = dict((strat or {}).get("default_params") or {})
        params.update(a.get("params") or {})

        try:
            r = create_report(self.user_id, BuildIn(
                title=(a.get("title") or "策略体检报告")[:120],
                subtitle=(a.get("subtitle") or "")[:200],
                sections=a.get("sections") or ["kline", "equity", "metrics"],
                highlights=a.get("highlights") or [],
                conclusion=(a.get("conclusion") or "")[:3000],
                strategy_type=(strat or {}).get("strategy_type") or a.get("strategy_type") or "ma_cross",
                symbol_code=a.get("symbol_code") or "600519",
                years=int(a.get("years", 3)),
                params=params,
                include_hysteresis=bool(a.get("include_hysteresis")),
                # 过滤条件透传给回测，否则报告画的是裸策略
                filters=a.get("filters") or [],
                # AI 自己写的补充区块，模板只提供骨架
                custom_sections=a.get("custom_sections") or [],
            ))
        except Exception as e:
            return ToolResult(ok=False, error=f"生成报告失败：{e}")

        return ToolResult(ok=True, ui_card="report", payload={
            "code": "report",
            "token": r["token"],
            "title": r["title"],
            "url": r["url"],
            "api_url": r["api_url"],
            "size_kb": r["size_kb"],
            "sections": r["sections"],
            # 这份报告实际画了哪些图、每张图是什么 —— 回答用户前先看这里，
            # 不要凭印象说"模板只能画一根线"
            "rendered": r.get("rendered") or [],
            "symbol_code": r["symbol_code"],
            "has_hysteresis": r["has_hysteresis"],
            "download_url": f"/api/reports/{r['token']}?download=1",
            "script_url": f"/api/reports/{r['token']}/script",
            "hint": r["hint"],
        })

    def _search_web(self, a: Dict[str, Any]) -> ToolResult:
        """
        联网找因子/策略线索。
        注意：这里只返回「网上的说法」，不判断真假。
        AI 必须把它写成公式并回测验证，不能直接采信网页结论。
        """
        try:
            from .web_search import search
        except ImportError:
            from web_search import search

        q = (a.get("query") or "").strip()
        if not q:
            return ToolResult(ok=False, error="搜索词不能为空")

        r = search(q, int(a.get("max_results", 5)))
        if not r.get("ok"):
            return ToolResult(
                ok=False,
                error=f"联网搜索没成功：{r.get('error')}"
                      + (f"；{r.get('hint')}" if r.get("hint") else "")
                      + "。请如实告诉用户『现在没联网能力』，用你自己的知识提因子，并说明是模型记忆不是实时来源。",
            )

        if not r.get("results"):
            return ToolResult(ok=False, error=f"搜「{q}」没有结果，换个关键词试试")

        return ToolResult(ok=True, ui_card="web_results", payload={
            "code": "web_results",
            **r,
            "disclaimer": "以下是网上的说法，未经核实。必须写成公式跑回测验证后才能采信。",
        })

    def _prepare_deploy(self, a: Dict[str, Any]) -> ToolResult:
        """
        生成部署引导 + 包清单。
        这里只「准备」，不代用户执行远程命令 —— 理由见 deploy_pack 的注释。
        """
        try:
            from .deploy_pack import PLATFORMS, STEPS, build_package
        except ImportError:
            from deploy_pack import PLATFORMS, STEPS, build_package

        code = a.get("strategy_code") or ""
        strat = self.ctx.get_strategy(code) if code else None
        if code and not strat:
            return ToolResult(ok=False, error=f"没找到策略 {code}")

        symbol = a.get("symbol_code") or "600519"
        years = int(a.get("years", 3))
        params = dict((strat or {}).get("default_params") or {})
        params.update(a.get("params") or {})

        payload = {
            "strategy_code": (strat or {}).get("code", code),
            "strategy_type": (strat or {}).get("strategy_type", "ma_cross"),
            "strategy_name": (strat or {}).get("name", code or "自定义策略"),
            "symbol_code": symbol,
            "years": years,
            "params": params,
            "filters": a.get("filters") or [],
            "condition_text": a.get("condition_text") or "",
        }

        try:
            blob = build_package(payload)
        except Exception as e:
            return ToolResult(ok=False, error=f"生成部署包失败：{e}")

        import io
        import zipfile
        files = []
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                files.append({"path": info.filename.split("/", 1)[-1],
                              "bytes": info.file_size})

        return ToolResult(ok=True, ui_card="deploy_guide", payload={
            "code": "deploy_guide",
            "strategy": payload["strategy_name"],
            "symbol_code": symbol,
            "platforms": PLATFORMS,
            "steps": STEPS,
            "files": files,
            "size_kb": round(len(blob) / 1024, 1),
            "download_path": "/deploy",
            "download_hint": "在「部署上线」页或回测结果页点「下载部署包」",
            "limits": [
                "不自动下单：需要券商接口权限和风控，刻意不碰钱",
                "不代执行 SSH：误操作不可逆，改为分步引导",
                "不保证盈利：只是把验证过的规则搬到服务器每天执行",
            ],
            "template_note": "所有策略共用一套模板，换策略只改参数，部署逻辑不变",
        })

    def _check_robustness(self, a: Dict[str, Any]) -> ToolResult:
        """防过拟合检查：分段验证 + 参数敏感性"""
        try:
            from .robustness import full_check
        except ImportError:
            from robustness import full_check

        code = a["strategy_code"]
        strat = self.ctx.get_strategy(code)
        if not strat:
            return ToolResult(ok=False, error=f"没找到策略 {code}")

        symbol = a["symbol_code"]
        years = int(a.get("years", 5))
        bars = self.ctx.get_bars(symbol, years)
        if len(bars) < 200:
            return ToolResult(ok=False, error=f"{symbol} 数据不足 200 天，无法做分段验证")

        params = dict(strat.get("default_params") or {})
        params.update(a.get("params") or {})

        try:
            res = full_check(bars, strat["strategy_type"], params)
        except Exception as e:
            return ToolResult(ok=False, error=f"检查失败：{e}")

        res.update({
            "code": "robustness",
            "strategy": strat["name"],
            "symbol_code": symbol,
            "params": params,
            "bars": len(bars),
            "period": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
        })
        return ToolResult(ok=True, ui_card="robustness", payload=res)

    def _list_symbols(self, a: Dict[str, Any]) -> ToolResult:
        rows = self.ctx.list_symbols(
            keyword=a.get("keyword"), industry=a.get("industry"),
            limit=int(a.get("limit", 10)))
        return ToolResult(ok=True, ui_card="symbol_list", payload={"symbols": rows})

    def _explain(self, a: Dict[str, Any]) -> ToolResult:
        docs = self.ctx.search_kb(a["query"])
        if not docs:
            return ToolResult(ok=True, payload={
                "docs": [], "hint": "知识库没有这个词条，请明确告诉用户你不知道，不要编"})
        return ToolResult(ok=True, payload={"docs": docs})

    def _suggest_params(self, a: Dict[str, Any]) -> ToolResult:
        """
        在已有框架内做小范围参数扫描，返回最优的几组。
        注意：这是「参数扫描」不是「AI 写策略」，全程不生成代码。
        """
        strat = self.ctx.get_strategy(a["strategy_code"])
        if not strat:
            return ToolResult(ok=False, error="策略不存在")
        bars = self.ctx.get_bars(a["symbol_code"], 3)
        goal = a["goal"]
        grid = self.ctx.param_grid(strat)

        scored = []
        for p in grid:
            try:
                r = run_backtest(bars, strat["strategy_type"], p)
            except Exception:
                continue
            if goal == "lower_drawdown":
                score = -r.max_drawdown
            elif goal == "higher_return":
                score = r.annual_return
            else:
                score = -r.trade_count
            scored.append((score, p, r))

        if not scored:
            return ToolResult(ok=False, error="参数扫描没有有效结果")
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[:3]
        return ToolResult(ok=True, ui_card="param_suggest", payload={
            "goal": goal,
            "framework_unchanged": strat["strategy_type"],
            "suggestions": [
                {
                    "params": p,
                    "annual_return_pct": round(r.annual_return * 100, 2),
                    "max_drawdown_pct": round(r.max_drawdown * 100, 2),
                    "trade_count": r.trade_count,
                    "benchmark_return_pct": round(r.benchmark_return * 100, 2),
                } for _s, p, r in best
            ],
            "warning": (
                "这是同一段历史上的参数最优解，属于典型的过拟合高风险操作。"
                "必须提醒用户：换一段区间或换一只标的，结果很可能完全不同。"
            ),
        })


# ============================================================
# 上下文接口：线上接 MySQL，测试接内存
# ============================================================
class ToolContext:
    """子类化或替换这些方法即可接入真实数据库"""

    def search_strategies(self, category=None, difficulty=None, keyword=None, limit=3):
        raise NotImplementedError

    def get_strategy(self, code: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_bars(self, symbol: str, years: int) -> List[Bar]:
        raise NotImplementedError

    def list_symbols(self, keyword=None, industry=None, limit=10):
        raise NotImplementedError

    def search_kb(self, query: str):
        raise NotImplementedError

    def param_grid(self, strat: Dict[str, Any]) -> List[Dict[str, Any]]:
        """根据 param_schema 生成小规模参数网格，避免扫出上千种组合"""
        schema = strat.get("param_schema") or {}
        base = dict(strat["default_params"])
        grid = [base]
        for key, spec in schema.items():
            if spec.get("type") != "int":
                continue
            lo, hi = spec["min"], spec["max"]
            cur = base[key]
            candidates = sorted({max(lo, cur // 2), cur, min(hi, cur * 2)})
            for v in candidates:
                if v != cur:
                    np = dict(base)
                    np[key] = v
                    grid.append(np)
        # 去重 + 限量，控制算力
        uniq, seen = [], set()
        for p in grid:
            k = json.dumps(p, sort_keys=True)
            if k not in seen:
                seen.add(k)
                uniq.append(p)
        return uniq[:12]


# ============================================================
# 自测：不依赖真实模型，直接验证工具执行链路
# ============================================================
if __name__ == "__main__":
    import math
    import random

    random.seed(7)

    STRATS = [
        {"code": "ma_cross_5_20", "name": "5/20 均线金叉", "category": "趋势",
         "summary": "短均线上穿长均线买入", "difficulty": 1,
         "strategy_type": "ma_cross",
         "default_params": {"fast": 5, "slow": 20},
         "param_schema": {"fast": {"type": "int", "min": 2, "max": 60},
                          "slow": {"type": "int", "min": 5, "max": 250}}},
        {"code": "boll_revert_20", "name": "布林带下轨买入", "category": "均值回归",
         "summary": "跌破下轨买入，回中轨卖出", "difficulty": 2,
         "strategy_type": "boll", "default_params": {"n": 20, "k": 2.0},
         "param_schema": {"n": {"type": "int", "min": 10, "max": 120}}},
        {"code": "mom_20", "name": "20 日动量", "category": "动量",
         "summary": "过去 20 日涨幅超阈值就持有", "difficulty": 2,
         "strategy_type": "momentum",
         "default_params": {"lookback": 20, "threshold": 0.03},
         "param_schema": {"lookback": {"type": "int", "min": 5, "max": 120}}},
    ]
    KB = [{"title": "什么是金叉和死叉",
           "content": "金叉指短期均线上穿长期均线，通常视为买入信号……"}]

    def fake_bars(symbol: str, years: int) -> List[Bar]:
        # 同一标的同一区间必须返回同一份数据，否则对比策略时基准会对不上
        key = (symbol, years)
        if key in fake_bars._cache:
            return fake_bars._cache[key]
        n = 240 * years
        rng = random.Random(hash(key) & 0xFFFF)
        price, out = 100.0, []
        for i in range(n):
            price = max(1.0, price * (1 + rng.gauss(0.0003, 0.018)))
            out.append(Bar(f"D{i:04d}", price, price * 1.01, price * 0.99, price))
        fake_bars._cache[key] = out
        return out

    fake_bars._cache = {}

    class MemCtx(ToolContext):
        def search_strategies(self, category=None, difficulty=None, keyword=None, limit=3):
            rows = [s for s in STRATS
                    if (not category or s["category"] == category)
                    and (not difficulty or s["difficulty"] == difficulty)
                    and (not keyword or keyword in s["name"] + s["summary"])]
            return rows[:limit]

        def get_strategy(self, code):
            return next((s for s in STRATS if s["code"] == code), None)

        def get_bars(self, symbol, years):
            return fake_bars(symbol, years)

        def list_symbols(self, keyword=None, industry=None, limit=10):
            return [{"code": "600519", "name": "贵州茅台"},
                    {"code": "000858", "name": "五粮液"}][:limit]

        def search_kb(self, query):
            return KB if "金叉" in query or "死叉" in query else []

    ex = ToolExecutor(MemCtx())

    def show(title, name, args):
        r = ex.execute(name, args)
        print(f"\n【{title}】{name}({json.dumps(args, ensure_ascii=False)})")
        print(f"  ok={r.ok}  ui_card={r.ui_card}")
        text = r.for_model()
        print(f"  回给模型：{text[:420]}{'…' if len(text) > 420 else ''}")
        return r

    print("=" * 78)
    print("对话链路模拟：用户说「给我一个能用的策略」")
    print("=" * 78)
    show("第 1 步：模型先找策略", "search_strategies", {"difficulty": 1, "limit": 3})
    show("第 2 步：模型选一只标的", "list_symbols", {"limit": 2})
    bt = show("第 3 步：模型跑回测验证", "run_backtest",
              {"strategy_code": "ma_cross_5_20", "symbol_code": "600519", "years": 3})

    print()
    print("=" * 78)
    print("其它工具")
    print("=" * 78)
    show("概念解释", "explain_concept", {"query": "什么是金叉"})
    show("多策略对比", "compare_strategies",
         {"strategy_codes": ["ma_cross_5_20", "mom_20"], "symbol_code": "600519", "years": 2})
    show("参数优化（只在已有框架内）", "suggest_params",
         {"strategy_code": "ma_cross_5_20", "symbol_code": "600519", "goal": "lower_drawdown"})
    show("错误处理：策略不存在", "run_backtest",
         {"strategy_code": "not_exist", "symbol_code": "600519"})
    show("知识库没有该词条", "explain_concept", {"query": "量子波动速读"})

    print()
    print("=" * 78)
    print(f"注册工具数：{len(TOOLS)}")
    for t in TOOLS:
        f = t["function"]
        req = f["parameters"].get("required", [])
        print(f"  - {f['name']:<20} 必填参数 {req}")
    print(f"\n系统提示词长度：{len(SYSTEM_PROMPT)} 字符")
