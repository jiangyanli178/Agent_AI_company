"""
因子/策略的「画像层」—— 让 AI 先想清楚该用哪个，再动手算。

为什么不直接遍历 16 个因子：
1. 穷举出来的结果没有逻辑支撑，说不清"为什么是 ADX 而不是 ATR"
2. 搜索空间越大，过拟合越容易（16 因子 × 7 阈值 × 2 方向 = 224 次回测，
   必然能挑到历史上最漂亮的一组巧合）
3. 用户提出的诉求本身就有语义，"降回撤"和"提胜率"该看的因子完全不同

所以这里给每个因子写清楚：它量的是什么东西、适合解决什么、什么时候会失灵。
搜索时先按诉求挑出 3-5 个候选，只在这几个里试，并且把「为什么选它」讲给用户听。

因子清单本身也可以扩展：AI 可以用 `custom_factor` 提一个表达式（见 custom_factor.py），
系统算出来验证通过就纳入本次搜索。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------
# 一、因子画像
# ---------------------------------------------------------------
# measures     它到底在量什么（一句人话）
# solves       能解决什么问题（用于意图匹配）
# prefer       通常哪个方向算"好"（above=越高越适合买 / below=越低越适合买）
# 不适用于      什么情况下会失灵（必须告诉用户，不能只讲好处）
# risk         用它的副作用
FACTOR_PROFILE: Dict[str, Dict[str, Any]] = {
    "adx": {
        "label": "ADX 趋势强度",
        "measures": "当前行情有没有方向，只看强度不看涨跌",
        "solves": ["震荡", "假突破", "趋势", "没方向", "横盘"],
        "prefer": "above",
        "fails_when": "趋势末端 ADX 也很高，容易追在最后一波",
        "risk": "阈值定太高会把大部分交易机会挡掉",
    },
    "atr_pct": {
        "label": "ATR 波动占比",
        "measures": "每天平均波动多少个点（按价格百分比）",
        "solves": ["回撤", "波动", "剧烈", "风险", "太颠"],
        "prefer": "below",
        "fails_when": "低波动也可能是暴风雨前的盘整，不代表安全",
        "risk": "过度压低波动容忍度会让策略几乎不交易",
    },
    "std_pct": {
        "label": "标准差占比",
        "measures": "收盘价的离散程度（和 ATR 类似，但对跳空不敏感）",
        "solves": ["回撤", "波动", "稳定"],
        "prefer": "below",
        "fails_when": "只看收盘价，忽略日内剧烈震荡",
        "risk": "与 ATR 高度重合，同时用等于重复过滤",
    },
    "rsi": {
        "label": "RSI 相对强弱",
        "measures": "近期涨跌力量对比，0-100",
        "solves": ["追高", "超买", "接盘", "买贵", "高位"],
        "prefer": "below",
        "fails_when": "强趋势里会长期钝化，70 以上还能继续涨",
        "risk": "当成卖出信号用会过早离场",
    },
    "bias": {
        "label": "乖离率",
        "measures": "价格偏离均线多少（百分比）",
        "solves": ["追高", "偏离", "涨太多", "追涨"],
        "prefer": "below",
        "fails_when": "强趋势中价格可以长期远离均线",
        "risk": "和 RSI 有重叠，二选一即可",
    },
    "ma_gap": {
        "label": "价格偏离均线",
        "measures": "当前价相对 N 日均线的位置（百分比）",
        "solves": ["追高", "跌破", "趋势确认", "接飞刀"],
        "prefer": "below",
        "fails_when": "均线本身滞后，急跌时它的信号也慢",
        "risk": "参数 N 的选择对结果影响很大",
    },
    "boll_width": {
        "label": "布林带宽度",
        "measures": "波动率的另一种表达（上下轨之间的距离）",
        "solves": ["波动", "收缩", "变盘", "回撤"],
        "prefer": "below",
        "fails_when": "带宽收缩后往往跟着大波动，压太低反而错过变盘",
        "risk": "和 ATR 功能重叠",
    },
    "willr": {
        "label": "威廉指标",
        "measures": "收盘价在近期高低区间的位置",
        "solves": ["超买", "追高", "低位"],
        "prefer": "below",
        "fails_when": "和 RSI 类似，强趋势中会钝化",
        "risk": "取值 -100~0，方向容易搞反",
    },
    "cci": {
        "label": "CCI 顺势指标",
        "measures": "价格偏离统计平均的程度",
        "solves": ["超买", "超卖", "极端", "追高"],
        "prefer": "below",
        "fails_when": "在窄幅震荡里会频繁穿越零轴，噪音大",
        "risk": "阈值不直观，用户不容易理解",
    },
    "kdj_k": {
        "label": "KDJ 的 K 值",
        "measures": "收盘价在近期区间中的相对位置",
        "solves": ["超买", "低位", "短线"],
        "prefer": "below",
        "fails_when": "参数敏感，换 N 值结论就变",
        "risk": "噪音大，单独用不可靠",
    },
    "macd_dif": {
        "label": "MACD DIF",
        "measures": "快慢均线的差值，看动能方向",
        "solves": ["动能", "趋势", "翻绿", "走弱"],
        "prefer": "above",
        "fails_when": "滞后明显，转折点通常已经走完一半",
        "risk": "和均线策略本身高度相关，用在均线策略上容易重复",
    },
    "volume_ratio": {
        "label": "量比",
        "measures": "当前成交量相对近期平均的倍数",
        "solves": ["放量", "缩量", "确认", "假突破", "没量"],
        "prefer": "above",
        "fails_when": "放量也可能是出货，尤其在利好兑现时",
        "risk": "缩量突破确实更容易失败，但放量不代表一定成功",
    },
    "obv_slope": {
        "label": "OBV 变化率",
        "measures": "资金流向的累积方向（量价配合）",
        "solves": ["资金", "量能", "背离", "走弱"],
        "prefer": "above",
        "fails_when": "A 股有大量对倒，OBV 会被污染",
        "risk": "计算依赖成交量口径，不同数据源结果不同",
    },
    "drawdown": {
        "label": "当前回撤",
        "measures": "从最近高点跌下来多少（负数，-10 表示跌了 10%）",
        "solves": ["回撤", "抄底", "跌深", "接飞刀", "低位"],
        "prefer": "below",
        "fails_when": "跌得深不代表会反弹，下跌趋势里会一路接飞刀",
        "risk": "用它等于做均值回归，与趋势策略逻辑相冲突，必须提醒用户",
    },
    "price_above_ma": {
        "label": "价格站上均线",
        "measures": "收盘价是否高于 N 日均线（是/否）",
        "solves": ["趋势", "方向", "多空", "顺势"],
        "prefer": "above",
        "fails_when": "震荡市里会反复穿越均线，信号噪音大",
        "risk": "参数 N 越大越滞后",
    },
    "ma_slope_up": {
        "label": "均线向上",
        "measures": "N 日均线当前是否比 N 天前更高（是/否）",
        "solves": ["趋势", "方向", "上行", "顺势"],
        "prefer": "above",
        "fails_when": "均线转向总是慢半拍",
        "risk": "和 price_above_ma 经常重合",
    },
}


# ---------------------------------------------------------------
# 二、诉求 → 优先看的因子
# ---------------------------------------------------------------
# 这些是"起点"而不是"结论"：搜索仍会验证效果，只是缩小范围。
INTENT_RULES: List[Dict[str, Any]] = [
    {
        "key": "drawdown",
        "label": "降低回撤 / 少亏",
        "keywords": ["回撤", "少亏", "亏损", "稳一点", "稳一些", "扛不住", "受不了", "风险"],
        "factors": ["atr_pct", "adx", "drawdown", "boll_width", "std_pct"],
        "goal": "drawdown",
        "logic": "回撤通常来自两件事：波动太大时还在持仓，以及在下跌趋势里逆势买入。",
    },
    {
        "key": "win_rate",
        "label": "提高胜率 / 少被假突破打脸",
        "keywords": ["胜率", "假突破", "被套", "打脸", "老是亏", "来回", "震荡"],
        "factors": ["adx", "volume_ratio", "ma_slope_up", "price_above_ma"],
        "goal": "sharpe",
        "logic": "假突破多发于没有趋势、也没有量能配合的时候，先确认方向再进场。",
    },
    {
        "key": "chase",
        "label": "避免追高",
        "keywords": ["追高", "买贵", "高位", "接盘", "涨太多", "追涨"],
        "factors": ["rsi", "bias", "ma_gap", "willr", "cci"],
        "goal": "balanced",
        "logic": "追高的本质是买入时价格已经显著偏离均值，用偏离类指标拦掉。",
    },
    {
        "key": "trend",
        "label": "只在趋势里做",
        "keywords": ["趋势", "顺势", "方向", "横盘不做", "震荡不"],
        "factors": ["adx", "ma_slope_up", "price_above_ma", "macd_dif"],
        "goal": "balanced",
        "logic": "趋势策略在无趋势行情里会反复止损，先判断有没有方向。",
    },
    {
        "key": "timing",
        "label": "改善买卖点",
        "keywords": ["买点", "卖点", "时机", "提前", "滞后", "早买", "晚卖"],
        "factors": ["volume_ratio", "rsi", "kdj_k", "macd_dif"],
        "goal": "balanced",
        "logic": "买卖点差通常差在量能确认和短线超买超卖上。",
    },
    {
        "key": "return",
        "label": "提高收益",
        "keywords": ["收益", "赚钱", "多赚", "提高", "更好"],
        "factors": ["adx", "volume_ratio", "atr_pct", "macd_dif"],
        "goal": "return",
        "logic": "收益来自「在对的行情里重仓对的信号」，先筛掉不利环境。",
    },
]


def detect_intents(text: str) -> List[Dict[str, Any]]:
    """从用户那句话里读出他到底想要什么"""
    t = (text or "").lower()
    hits = []
    for rule in INTENT_RULES:
        for kw in rule["keywords"]:
            if kw in t:
                hits.append(rule)
                break
    return hits


def plan_candidates(
    goal: str = "balanced",
    user_text: str = "",
    strategy_type: str = "ma_cross",
    custom_factors: Optional[List[str]] = None,
    max_candidates: int = 5,
) -> Dict[str, Any]:
    """
    给出「这次该试哪几个因子」以及理由。
    AI 拿到这个可以直接告诉用户：我为什么选这几个。
    """
    intents = detect_intents(user_text)

    # 用户明确说了目标，以他的话为准；没说就用 goal 兜底
    ordered: List[str] = []
    reasons: List[str] = []
    seen = set()

    for it in intents:
        for f in it["factors"]:
            if f not in seen:
                seen.add(f)
                ordered.append(f)
        reasons.append(it["logic"])

    if not ordered:
        # 没有明确诉求时，按 goal 给一组通用起点
        default_map = {
            "drawdown": ["atr_pct", "adx", "price_above_ma"],
            "return": ["adx", "volume_ratio", "atr_pct"],
            "sharpe": ["adx", "volume_ratio", "bias"],
            "balanced": ["adx", "volume_ratio", "atr_pct"],
        }
        ordered = default_map.get(goal, default_map["balanced"])
        reasons.append("你没有说明具体想改善什么，我先按「控制风险 + 确认信号」这组通用逻辑试。")

    # 策略自身已经隐含的方向，不要再叠一遍（重复过滤没有意义）
    redundant = {
        "ma_cross": {"ma_gap", "price_above_ma", "ma_slope_up", "macd_dif"},
        "boll": {"boll_width"},
        "boll_break": {"boll_width"},
        "rsi": {"rsi", "willr"},
        "momentum": {"ma_gap", "bias"},
    }.get(strategy_type, set())

    kept, dropped = [], []
    for f in ordered:
        if f in redundant:
            dropped.append({"factor": f, "reason": "和基础策略判断的是同一件事，叠加等于重复过滤"})
            continue
        kept.append(f)

    # 布尔因子没有阈值，逻辑更硬，放在候选末尾（先给带阈值的，能微调）
    kept = [f for f in kept if f in FACTOR_PROFILE]

    # 候选太少时补两个通用因子：只有一两条路可走容易漏掉更合适的
    if len(kept) < 3:
        for f in ("atr_pct", "volume_ratio", "rsi", "adx"):
            if len(kept) >= 3:
                break
            if f not in kept and f not in redundant:
                kept.append(f)

    candidates = kept[:max_candidates]

    # 自定义因子排在最前，因为这是用户/AI 专门提出来的
    ext = []
    for code in (custom_factors or []):
        ext.append({"code": code, "source": "custom",
                    "why": "AI 针对这个策略专门提出的因子，优先验证"})
    for f in candidates:
        p = FACTOR_PROFILE[f]
        ext.append({
            "code": f,
            "label": p["label"],
            "measures": p["measures"],
            "prefer": p["prefer"],
            "why": "；".join(r[:40] for r in reasons[:1]) or "按通用逻辑纳入",
            "caveat": p["fails_when"],
            "risk": p["risk"],
        })

    return {
        "intents": [{"key": i["key"], "label": i["label"], "logic": i["logic"]} for i in intents],
        "candidates": ext,
        "dropped": dropped,
        "reasoning": " ".join(reasons) if reasons else "",
        "note": (
            f"这次只试 {len(ext)} 个因子，不是把全部 16 个都跑一遍。"
            "搜得越少，结果越可能是真的规律，而不是这段历史的巧合。"
        ),
    }


def describe_factor(code: str) -> Dict[str, Any]:
    """单因子画像，给对方看"""
    p = FACTOR_PROFILE.get(code)
    if not p:
        return {}
    return {"code": code, **p}
