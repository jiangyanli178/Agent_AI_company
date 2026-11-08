# -*- coding: utf-8 -*-
"""
防过拟合检查：样本外测试 + 参数敏感性。

这是「优化策略」的第三个方向（前两个是调参、加因子）。
很多人调出一个漂亮的结果就以为找到了圣杯，
其实只是在历史数据里挖到了一条恰好成立的巧合。
这两个检查就是用来识别这种巧合的。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .backtest import Bar, run_backtest


def split_test(bars: List[Bar], strategy_type: str, params: Dict[str, Any],
               filters: Optional[List[Dict[str, Any]]] = None,
               ratio: float = 0.7) -> Dict[str, Any]:
    """
    把时间切成前后两段，同一套参数分别跑。
    如果两段表现差得离谱，说明这套参数只贴合了其中某一段行情。
    """
    split = int(len(bars) * ratio)
    if split < 60 or len(bars) - split < 40:
        raise ValueError("数据太短，前后两段都不够验证")

    first, second = bars[:split], bars[split:]

    def run(seg):
        r = run_backtest(seg, strategy_type, params, filters=filters)
        return {
            "period": f"{seg[0].trade_date} ~ {seg[-1].trade_date}",
            "days": len(seg),
            "total_return_pct": round(r.total_return * 100, 2),
            "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            "sharpe": round(r.sharpe, 2),
            "trade_count": r.trade_count,
            "win_rate_pct": round(r.win_rate * 100, 1),
            "benchmark_return_pct": round(r.benchmark_return * 100, 2),
        }

    a, b = run(first), run(second)
    gap = round(abs(a["total_return_pct"] - b["total_return_pct"]), 2)

    # 判断一致性
    if a["total_return_pct"] > 0 and b["total_return_pct"] > 0:
        if gap <= 15:
            verdict, advice = "稳定", "前后两段都赚钱，且差距不大，这套参数相对可靠。"
        else:
            verdict, advice = "勉强", "两段都赚钱但差距较大，说明表现很依赖行情阶段。"
    elif a["total_return_pct"] > 0 > b["total_return_pct"]:
        verdict, advice = "前好后坏", \
            "前半段赚钱、后半段亏钱——这是最典型的过拟合信号，说明参数贴合了旧行情，对新行情无效。"
    elif a["total_return_pct"] < 0 < b["total_return_pct"]:
        verdict, advice = "前坏后好", \
            "前半段亏、后半段赚。可能只是运气好碰上顺风行情，样本太少不能说明策略变好了。"
    else:
        verdict, advice = "两段都亏", "两个阶段都在亏，这套参数没有可用的证据。"

    return {
        "in_sample": a,
        "out_sample": b,
        "gap_pct": gap,
        "verdict": verdict,
        "advice": advice,
        "note": f"按时间 {int(ratio*100)}/{int((1-ratio)*100)} 切分，同一套参数分别回测",
    }


def param_sensitivity(bars: List[Bar], strategy_type: str, params: Dict[str, Any],
                      key: str, values: List[int],
                      filters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    把某个参数依次取不同值，看收益曲线是「平台」还是「尖峰」。
    尖峰几乎一定是过拟合——它说明某个具体数值恰好踩中了历史的巧合。
    """
    rows: List[Dict[str, Any]] = []
    for v in values:
        p = dict(params)
        p[key] = v
        # 均线策略要保证 fast < slow，否则跳过
        if strategy_type == "ma_cross":
            if key == "fast" and v >= p.get("slow", 999):
                continue
            if key == "slow" and v <= p.get("fast", 0):
                continue
        try:
            r = run_backtest(bars, strategy_type, p, filters=filters)
        except Exception:
            continue
        rows.append({
            "value": v,
            "total_return_pct": round(r.total_return * 100, 2),
            "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            "sharpe": round(r.sharpe, 2),
            "trade_count": r.trade_count,
        })

    if len(rows) < 3:
        return {"key": key, "rows": rows, "verdict": "样本不足",
                "advice": "可选参数值太少，无法判断稳健性。"}

    rets = [r["total_return_pct"] for r in rows]
    best = max(rows, key=lambda r: r["total_return_pct"])
    best_i = rows.index(best)
    mean = sum(rets) / len(rets)
    spread = max(rets) - min(rets)

    # 判断是否尖峰：最优点明显高于两侧邻点
    is_spike = False
    if 0 < best_i < len(rows) - 1:
        left = rets[best_i - 1]
        right = rets[best_i + 1]
        if (best["total_return_pct"] - left > 12) and (best["total_return_pct"] - right > 12):
            is_spike = True
    elif best_i == 0 or best_i == len(rows) - 1:
        # 最优点在边界，通常意味着参数还没调够，或者趋势单调
        is_spike = False

    if is_spike:
        verdict = "尖峰（高风险）"
        advice = (f"参数 {key}={best['value']} 的收益明显高于相邻取值，"
                  f"这是过拟合的典型形态。建议选相邻区域的中间值，不要用这个最优点。")
    elif spread <= 20:
        verdict = "平缓（稳健）"
        advice = f"参数在 {values[0]}~{values[-1]} 区间内收益变化不大（极差 {spread:.1f}%），说明不依赖某个精确数值。"
    else:
        verdict = "波动较大"
        advice = f"不同取值的收益差距较大（极差 {spread:.1f}%），说明策略对这个参数比较敏感，实盘表现不确定性高。"

    return {
        "key": key,
        "rows": rows,
        "best_value": best["value"],
        "best_return_pct": best["total_return_pct"],
        "mean_return_pct": round(mean, 2),
        "spread_pct": round(spread, 2),
        "verdict": verdict,
        "advice": advice,
    }


def full_check(bars: List[Bar], strategy_type: str, params: Dict[str, Any],
               filters: Optional[List[Dict[str, Any]]] = None,
               grid: Optional[Dict[str, List[int]]] = None) -> Dict[str, Any]:
    """
    一次跑完两项检查。grid 不给就用默认的参数网格（按策略类型挑）。
    """
    if grid is None:
        if strategy_type == "ma_cross":
            base = int(params.get("fast", 5))
            grid = {"fast": sorted({max(2, base - 3), base, base + 3, base + 5})}
        elif strategy_type == "rsi":
            base = int(params.get("n", 14))
            grid = {"n": sorted({max(2, base - 4), base, base + 4, base + 8})}
        else:
            base = int(params.get("n", 20))
            grid = {"n": sorted({max(2, base - 5), base, base + 5, base + 10})}

    out: Dict[str, Any] = {"split": split_test(bars, strategy_type, params, filters),
                           "sensitivity": []}
    for k, vals in grid.items():
        try:
            out["sensitivity"].append(
                param_sensitivity(bars, strategy_type, params, k, vals, filters))
        except Exception as e:
            out["sensitivity"].append({"key": k, "error": str(e)})

    # 给个总的过拟合风险评级
    verdicts = [out["split"]["verdict"]] + [s.get("verdict", "") for s in out["sensitivity"]]
    if any("尖峰" in v or "前好后坏" in v for v in verdicts):
        out["risk"] = "高"
        out["summary"] = "有明确的过拟合迹象，这套参数不建议直接使用。"
    elif all("稳定" in v or "平缓" in v for v in verdicts):
        out["risk"] = "低"
        out["summary"] = "分段表现一致、参数也不敏感，相对可靠。"
    else:
        out["risk"] = "中"
        out["summary"] = "没有明显过拟合，但也不够稳健，建议换一段行情或换只股票再验一次。"

    return out
