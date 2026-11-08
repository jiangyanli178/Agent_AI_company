"""
因子自动搜索 —— 让 AI 不必自己猜「该用哪个因子、阈值设多少」。

原来 AI 只能从提示词里硬背几种映射（震荡期→ADX>20），
但阈值 20 是我们拍的，不是这只票这段历史算出来的。

这里的做法：
  1. 把 14 个数值因子在这只票上的真实分布算出来，取分位点当候选阈值
     （每个因子在自己的合理区间里试探，不去猜 rsi=70 这种通用值）
  2. 逐个因子 × 阈值 × 方向跑真实回测，按用户目标打分
  3. 贪心累加：每轮挑当前最优的一个，最多叠 3 个
  4. 硬约束：过滤后交易数不能太少、拦截天数不能过多 —— 防过拟合

搜索结果是「这段历史上的事实」，不是「未来的保证」，
所以返回值里始终带上 baseline 和样本量，让 AI 有话说得清楚。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from .backtest import Bar, run_backtest
    from .factor_engine import NUMERIC, BOOLEAN, OPS
except ImportError:                     # 直接跑自测时走这里
    from backtest import Bar, run_backtest
    from factor_engine import NUMERIC, BOOLEAN, OPS


# 保守的上限，保证搜索不会把整段时间都过滤掉
MAX_BLOCK_PCT = 50.0        # 最多拦掉 50% 的交易日
MIN_TRADE_KEEP = 0.25       # 过滤后交易数至少保留 25%
MIN_TRADE_ABS = 2           # 且至少 2 笔，否则统计上没意义
MIN_EXPOSURE_KEEP = 0.6     # 持仓时间不能萎缩到基线的 60% 以下
                            # 0.4 太松：会让搜索器选出「一年只做 20 天」的极端解，
                            # 那不是策略变好，是它根本没参与市场。
MAX_OVERLAP = 0.9           # 新因子与已选因子掩码重合度超过 90% 就不加
MIN_GAIN = 0.5              # 评分提升小于这个就不加了，避免加无用的条件
OUT_SAMPLE_RATIO = 0.25     # 最后 25% 留作样本外验证


def _fmt_condition(code: str, op: str, val: Any,
                   labels: Optional[Dict[str, str]] = None) -> str:
    """把条件翻译成人话，特别处理回撤这种带正负号的"""
    op_cn = {"above": "高于", "below": "低于",
             "above_eq": "不低于", "below_eq": "不高于"}.get(op, "符合")
    if labels and code in labels:
        return f"{labels[code]} {'高于' if op == 'above' else '低于'} {val}"
    if code in NUMERIC:
        label = NUMERIC[code]["label"]
        if code == "drawdown" and op == "below" and isinstance(val, (int, float)):
            return f"回撤深于 {abs(val)}%"
        if code == "drawdown" and op == "above" and isinstance(val, (int, float)):
            return f"回撤浅于 {abs(val)}%"
        return f"{label} {op_cn} {val}"
    if code in BOOLEAN:
        return BOOLEAN[code]["label"]
    return code


def _percentile(sorted_vals: List[float], q: float) -> float:
    """线性插值分位数，不引 numpy"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _candidate_thresholds(series: List[Optional[float]]) -> List[float]:
    """
    用因子自己的分布取候选阈值。
    取 20/30/40/50/60/70/80 分位 —— 太大或太小的都会被拦截比例约束筛掉。
    """
    vals = sorted(v for v in series if v is not None)
    if len(vals) < 60:
        return []
    out = []
    for q in (20, 30, 40, 50, 60, 70, 80):
        v = _percentile(vals, q)
        # 价格类因子保留两位、比率类保留四位足够
        out.append(round(v, 4 if abs(v) < 10 else 2))
    return sorted(set(out))


def _score(res, goal: str) -> float:
    """
    统一的评分口径。都用「百分点」为单位，便于横向比较。
    balanced 对回撤罚得比收益重 —— 用户对亏钱的痛感本来就高于赚钱的快感。
    """
    ret = res.total_return * 100
    mdd = res.max_drawdown * 100
    if goal == "return":
        return ret
    if goal == "drawdown":
        return -mdd
    if goal == "sharpe":
        return res.sharpe * 10
    return ret - 1.5 * mdd


def _labels_of(conditions: List[Dict[str, Any]]) -> List[str]:
    """把条件翻译成给用户看的中文短语"""
    return [_fmt_condition(c.get("factor"), c.get("op"), c.get("value"))
            for c in conditions]


def _summarize(res) -> Dict[str, Any]:
    return {
        "total_return_pct": round(res.total_return * 100, 2),
        "annual_return_pct": round(res.annual_return * 100, 2),
        "max_drawdown_pct": round(res.max_drawdown * 100, 2),
        "sharpe": round(res.sharpe, 2),
        "win_rate_pct": round(res.win_rate * 100, 1),
        "trade_count": res.trade_count,
        "exposure_pct": round(res.exposure * 100, 1),
    }


def search_factors(
    bars: List[Bar],
    strategy_type: str,
    base_params: Dict[str, Any],
    goal: str = "balanced",
    max_factors: int = 3,
    only_factors: Optional[List[str]] = None,
    fixed_conditions: Optional[List[Dict[str, Any]]] = None,
    custom_factors: Optional[List[Dict[str, str]]] = None,
    user_text: str = "",
) -> Dict[str, Any]:
    """
    在当前策略上自动搜索最优因子组合。

    only_factors: 限定只在这些因子里搜（用户说「用 RSI 试试」时传）
    fixed_conditions: 已经确定要保留的条件（用户说「ADX 必须留着」时传）
    custom_factors: [{"name": "振幅", "expression": "(high-low)/close*100"}]，
                    AI 提出的内置库之外的因子，这里会真的算出来并参与搜索
    user_text: 用户原话，用来推断他到底想改善什么（决定候选范围）

    搜索范围不是「全部因子」，而是先按语义筛出 3-5 个候选再试。
    理由见 factor_planner 顶部注释：穷举出来的结果没有逻辑支撑，也更容易是巧合。
    """
    n = len(bars)
    if n < 60:
        raise ValueError("数据太少，至少需要 60 根 K 线")

    # ---- 先做「该试哪些因子」的判断 ----
    try:
        from .factor_planner import plan_candidates
    except ImportError:
        from factor_planner import plan_candidates

    plan = plan_candidates(
        goal=goal,
        user_text=user_text,
        strategy_type=strategy_type,
        custom_factors=[c.get("name") for c in (custom_factors or [])],
        max_candidates=5 if not only_factors else len(only_factors),
    )

    if only_factors:
        allowed_set = set(only_factors)
    else:
        allowed_set = {c["code"] for c in plan["candidates"]}

    base = run_backtest(bars, strategy_type, base_params)
    base_score = _score(base, goal)

    # ---- 预先算好候选因子序列，避免每个阈值重复计算 ----
    series_cache: Dict[str, List[Optional[float]]] = {}
    for code, spec in NUMERIC.items():
        if allowed_set and code not in allowed_set:
            continue
        try:
            series_cache[code] = spec["fn"](bars, dict(spec["defaults"]))
        except Exception:
            continue
    bool_cache: Dict[str, List[bool]] = {}
    for code, spec in BOOLEAN.items():
        if allowed_set and code not in allowed_set:
            continue
        try:
            bool_cache[code] = spec["fn"](bars, dict(spec["defaults"]))
        except Exception:
            continue

    # ---- AI 提出的自定义因子：这里才真正算出来 ----
    custom_ok: List[Dict[str, Any]] = []
    custom_failed: List[Dict[str, Any]] = []
    custom_labels: Dict[str, str] = {}
    for cf in (custom_factors or []):
        nm = (cf.get("name") or "").strip()[:20] or "custom"
        # AI 起的名字可能和内置因子撞（比如它写个 "ADX"），
        # 撞了就把内置的覆盖掉、结果对不上，所以统一加前缀
        if nm in NUMERIC or nm in BOOLEAN:
            nm = "自定义_" + nm
        try:
            from .custom_factor import evaluate
        except ImportError:
            from custom_factor import evaluate
        try:
            series_cache[nm] = evaluate(cf.get("expression") or "", bars)
            custom_ok.append({
                "name": nm,
                "expression": cf.get("expression"),
                "desc": cf.get("desc") or "",
            })
            # 不去改全局 NUMERIC（会跨请求污染），
            # 只在本地的 custom_labels 里登记，供 label_for 使用
            custom_labels[nm] = cf.get("desc") or nm
        except Exception as e:
            custom_failed.append({
                "name": nm,
                "expression": cf.get("expression"),
                "reason": str(e)[:120],
            })

    # ---- 已有条件先叠加，形成初始掩码 ----
    allowed = [True] * n
    chosen: List[Dict[str, Any]] = list(fixed_conditions or [])
    chosen_desc: List[str] = _labels_of(chosen)

    def mask_of(cond: Dict[str, Any]) -> Optional[List[bool]]:
        code = cond.get("factor")
        if code in NUMERIC:
            seq = series_cache.get(code)
            if seq is None:
                return None
            cmp = OPS.get(cond.get("op") or "below")
            if cmp is None:
                return None
            val = cond.get("value")
            return [True if v is None else cmp(v, val) for v in seq]
        if code in BOOLEAN:
            seq = bool_cache.get(code)
            return list(seq) if seq is not None else None
        return None

    for c in chosen:
        m = mask_of(c)
        if m:
            allowed = [a and b for a, b in zip(allowed, m)]

    def run_with(mask: List[bool]):
        return run_backtest(bars, strategy_type, base_params,
                            precomputed_mask=mask)

    # 固定条件的基线（用于算增量）
    current = run_with(allowed) if chosen else base
    current_score = _score(current, goal)

    used: set = {c.get("factor") for c in chosen}
    rounds: List[Dict[str, Any]] = []

    for _ in range(max(0, max_factors)):
        best: Optional[Dict[str, Any]] = None

        # ---- 数值因子 ----
        for code, seq in series_cache.items():
            if code in used:
                continue
            cands = _candidate_thresholds(seq)
            for op in ("above", "below"):
                cmp = OPS[op]
                for val in cands:
                    cond = {"factor": code, "op": op, "value": val}
                    m = [True if v is None else cmp(v, val) for v in seq]
                    merged = [a and b for a, b in zip(allowed, m)]
                    blocked = sum(1 for a, b in zip(allowed, merged) if a and not b)
                    block_pct = blocked / n * 100
                    if block_pct < 3 or block_pct > MAX_BLOCK_PCT:
                        continue
                    try:
                        res = run_with(merged)
                    except Exception:
                        continue
                    # 两个硬约束：交易数不能太少、持仓时间不能萎缩太多。
                    # 只看收益的话，搜索器一定会选出「几乎不交易」的极端解 ——
                    # 那不是策略变好了，是它根本不参与市场。
                    if res.trade_count < max(MIN_TRADE_ABS,
                                             int(base.trade_count * MIN_TRADE_KEEP)):
                        continue
                    if res.exposure < base.exposure * MIN_EXPOSURE_KEEP:
                        continue
                    sc = _score(res, goal)
                    gain = sc - current_score
                    if gain < MIN_GAIN:
                        continue
                    if best is None or gain > best["gain"]:
                        best = {"cond": cond, "mask": merged, "res": res,
                                "gain": gain, "block_pct": block_pct,
                                "label": _fmt_condition(code, op, val, custom_labels)}

        # ---- 布尔因子（没有阈值，只有开/关）----
        for code, seq in bool_cache.items():
            if code in used:
                continue
            merged = [a and b for a, b in zip(allowed, seq)]
            blocked = sum(1 for a, b in zip(allowed, merged) if a and not b)
            block_pct = blocked / n * 100
            if block_pct < 3 or block_pct > MAX_BLOCK_PCT:
                continue
            try:
                res = run_with(merged)
            except Exception:
                continue
            if res.trade_count < max(MIN_TRADE_ABS,
                                     int(base.trade_count * MIN_TRADE_KEEP)):
                continue
            if res.exposure < base.exposure * MIN_EXPOSURE_KEEP:
                continue
            sc = _score(res, goal)
            gain = sc - current_score
            if gain < MIN_GAIN:
                continue
            if best is None or gain > best["gain"]:
                best = {"cond": {"factor": code}, "mask": merged, "res": res,
                        "gain": gain, "block_pct": block_pct,
                        "label": BOOLEAN[code]["label"]}

        if best is None:
            break

        # 冗余不用单独判：block_pct 下限（3%）已经保证新条件必须有额外拦截，
        # 几乎不拦的条件算不出提升，会自然被 MIN_GAIN 筛掉。
        chosen.append(best["cond"])
        chosen_desc.append(best["label"])
        used.add(best["cond"].get("factor"))
        allowed = best["mask"]
        current = best["res"]
        current_score = _score(current, goal)
        rounds.append({
            "step": len(rounds) + 1,
            "factor": best["cond"].get("factor"),
            "label": best["label"],
            "condition": best["cond"],
            "blocked_pct": round(best["block_pct"], 1),
            "gain": round(best["gain"], 2),
            "result": _summarize(best["res"]),
        })

    after = _summarize(current) if rounds else _summarize(base)

    # ---- 样本外验证 ----
    # 搜索用的是全样本，必然能挑到「历史上最好看」的一组，这是拟合。
    # 所以最后 25% 单独再跑一遍：要求不是「样本外也变好」，只看「有没有崩掉」。
    validation: Optional[Dict[str, Any]] = None
    if rounds:
        split = int(n * (1 - OUT_SAMPLE_RATIO))
        warm = 60                      # 给均线等指标留预热期
        out_bars = bars[max(0, split - warm):]
        if len(out_bars) >= 60:
            try:
                o_base = _summarize(run_backtest(out_bars, strategy_type, base_params))
                o_new = _summarize(run_backtest(out_bars, strategy_type, base_params,
                                                filters=chosen))
                degraded = (o_new["total_return_pct"] < o_base["total_return_pct"] - 5
                            or o_new["max_drawdown_pct"] > o_base["max_drawdown_pct"] + 8)
                validation = {
                    "range": [out_bars[0].trade_date, out_bars[-1].trade_date],
                    "baseline": o_base,
                    "with_conditions": o_new,
                    "degraded": degraded,
                    "verdict": ("样本外明显变差，这套条件很可能是拟合出来的"
                                if degraded else "样本外没有明显变差，条件相对可靠"),
                }
            except Exception as e:
                validation = {"error": str(e)[:120]}

    # ---- 每个条件的边际贡献（去掉它还剩多少）----
    contributions: List[Dict[str, Any]] = []
    for i, c in enumerate(chosen):
        sub = [x for j, x in enumerate(chosen) if j != i]
        try:
            r = (run_backtest(bars, strategy_type, base_params, filters=sub)
                 if sub else base)
            s = _summarize(r)
            contributions.append({
                "label": chosen_desc[i] if i < len(chosen_desc) else c.get("factor"),
                "without_it": s,
                "delta_return_pp": round(after["total_return_pct"] - s["total_return_pct"], 2),
                "delta_drawdown_pp": round(s["max_drawdown_pct"] - after["max_drawdown_pct"], 2),
            })
        except Exception:
            continue

    # 风险评级：只靠样本外暴跌 + 某个条件贡献过大这两条判断，宁可保守
    risk = "低"
    if validation and validation.get("degraded"):
        risk = "高"
    elif contributions and max((c["delta_return_pp"] for c in contributions), default=0) > 15:
        risk = "中"
    if len(rounds) >= 3 and risk == "低":
        risk = "中"      # 叠了三个条件，自由度已经吃掉不少

    return {
        "strategy_type": strategy_type,
        "params": base_params,
        "goal": goal,
        "sample": {
            "bars": n,
            "start": bars[0].trade_date,
            "end": bars[-1].trade_date,
        },
        "baseline": _summarize(base),
        "result": after,
        "improvement": {
            "return_pp": round(after["total_return_pct"] - base.total_return * 100, 2),
            "drawdown_pp": round(after["max_drawdown_pct"] - base.max_drawdown * 100, 2),
            "trades": after["trade_count"] - base.trade_count,
        },
        "conditions": chosen,
        "condition_text": "；".join(chosen_desc) if chosen_desc else "未加任何过滤",
        "steps": rounds,
        "validation": validation,
        "contributions": contributions,
        "overfit_risk": risk,
        "searched_factors": len(series_cache) + len(bool_cache),
        "plan": plan,
        "custom_factors": {"accepted": custom_ok, "rejected": custom_failed},
        "note": (
            f"在 {n} 根 K 线上穷举了 {len(series_cache) + len(bool_cache)} 个因子，"
            "挑出来的必然是这段历史上最好看的一组 —— 这本身就是过拟合的温床。"
            "请重点看 verification：样本外是否还成立、每个条件去掉后是否立刻崩掉。"
        ),
    }
