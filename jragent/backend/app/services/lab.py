"""
自定义策略的执行层（内部服务，不直接对前端暴露）。

用途：用户经常是「快线 1、慢线 99、回滞 1%」这种现场拼出来的组合，
数据库里没有对应 strategy_id，走不了 /api/backtest。
这里直接拿 strategy_type + params 跑，返回结构和策略详情页一致。

目前主要被报告生成（routers/reports.py）复用：
跑一次 → 拿到完整结果 → 渲染成 HTML。
原来的 /lab 页面已经去掉，用户改成让 AI 直接生成报告了。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from .backtest import Bar, run_backtest, explain_signal, SIGNAL_FUNCS
except ImportError:
    from backtest import Bar, run_backtest, explain_signal, SIGNAL_FUNCS


# 策略类型 → 参数的中文名、类型、范围。前端据此生成控件，
# 以后加新策略只要在这里补一行，页面自动多出对应控件。
PARAM_SPEC: Dict[str, Dict[str, Any]] = {
    "ma_cross": {
        "label": "均线交叉",
        "params": [
            {"key": "fast", "label": "快线天数", "type": "int", "default": 5, "min": 1, "max": 120, "step": 1},
            {"key": "slow", "label": "慢线天数", "type": "int", "default": 20, "min": 2, "max": 250, "step": 1},
            {"key": "hysteresis", "label": "回滞带", "type": "select", "default": 0,
             "unit": "%", "options": [0, 0.5, 1, 2, 3, 5],
             "hint": "快线要超出慢线这个比例才算真交叉，中间保持不动。调大能少被假突破打脸，但信号会晚一点"},
        ],
    },
    "boll": {
        "label": "布林带回归",
        "params": [
            {"key": "n", "label": "周期", "type": "int", "default": 20, "min": 5, "max": 120, "step": 1},
            {"key": "k", "label": "标准差倍数", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1},
        ],
    },
    "boll_break": {
        "label": "布林带突破",
        "params": [
            {"key": "n", "label": "周期", "type": "int", "default": 20, "min": 5, "max": 120, "step": 1},
            {"key": "k", "label": "标准差倍数", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1},
        ],
    },
    "momentum": {
        "label": "动量",
        "params": [
            {"key": "lookback", "label": "回看天数", "type": "int", "default": 20, "min": 5, "max": 120, "step": 1},
            {"key": "threshold", "label": "触发涨幅", "type": "float", "default": 0.03, "min": 0.005, "max": 0.3, "step": 0.005},
        ],
    },
    "rsi": {
        "label": "RSI 超卖",
        "params": [
            {"key": "n", "label": "周期", "type": "int", "default": 14, "min": 5, "max": 60, "step": 1},
            {"key": "low", "label": "超卖线", "type": "int", "default": 30, "min": 10, "max": 45, "step": 1},
            {"key": "high", "label": "超买线", "type": "int", "default": 70, "min": 55, "max": 90, "step": 1},
        ],
    },
    "macd_cross": {
        "label": "MACD 金叉",
        "params": [
            {"key": "fast", "label": "快线周期", "type": "int", "default": 12, "min": 2, "max": 60, "step": 1},
            {"key": "slow", "label": "慢线周期", "type": "int", "default": 26, "min": 5, "max": 120, "step": 1},
            {"key": "signal", "label": "信号线周期", "type": "int", "default": 9, "min": 2, "max": 30, "step": 1},
            {"key": "zero_filter", "label": "只在零轴上方买", "type": "bool", "default": False,
             "hint": "打开后只认 DIF 在零轴上方的金叉，能滤掉下跌趋势里的反弹假信号，代价是错过底部第一波"},
        ],
    },
    "kdj_cross": {
        "label": "KDJ 金叉",
        "params": [
            {"key": "n", "label": "RSV 周期", "type": "int", "default": 9, "min": 3, "max": 60, "step": 1},
            {"key": "buy_below", "label": "只在 K 低于此值买入", "type": "int", "default": 50, "min": 10, "max": 90, "step": 1},
            {"key": "sell_above", "label": "K 高于此值离场", "type": "int", "default": 85, "min": 50, "max": 99, "step": 1},
        ],
    },
    "breakout": {
        "label": "唐奇安通道突破",
        "params": [
            {"key": "entry", "label": "突破几日新高买入", "type": "int", "default": 20, "min": 5, "max": 120, "step": 1},
            {"key": "exit", "label": "跌破几日新低卖出", "type": "int", "default": 10, "min": 3, "max": 120, "step": 1},
        ],
    },
    "trix_cross": {
        "label": "TRIX 金叉",
        "params": [
            {"key": "n", "label": "平滑周期", "type": "int", "default": 12, "min": 3, "max": 40, "step": 1},
            {"key": "signal", "label": "信号线周期", "type": "int", "default": 9, "min": 2, "max": 30, "step": 1},
        ],
    },
}


def spec_for(strategy_type: str) -> Dict[str, Any]:
    return PARAM_SPEC.get(strategy_type) or {"label": strategy_type, "params": []}


def validate_params(strategy_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    把参数收拾干净：类型转对、范围夹住。
    用户从 URL 里传进来的都是字符串，不转会用不了。
    """
    spec = spec_for(strategy_type)
    out: Dict[str, Any] = {}
    for item in spec["params"]:
        k = item["key"]
        # 先把默认值铺上：用户只说了一个参数时，其余参数不能是空的，
        # 否则存进策略库的策略会缺参数，详情页控件一片空白。
        if "default" in item:
            out[k] = item["default"]
        if k not in params or params[k] in (None, ""):
            continue
        raw = params[k]
        if item["type"] == "int":
            v = int(float(raw))
        elif item["type"] == "float":
            v = float(raw)
        elif item["type"] == "bool":
            # URL 里传进来的是 "true"/"1"/"on"，直接 bool("false") 会得到 True
            if isinstance(raw, str):
                v = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                v = bool(raw)
        else:
            v = raw
        # bool 本来就在 0/1 之间，夹范围会把它变成数字
        if item["type"] != "bool":
            if "min" in item:
                v = max(item["min"], v)
            if "max" in item:
                v = min(item["max"], v)
        out[k] = v

    if strategy_type == "ma_cross":
        f = int(out.get("fast", 5))
        s = int(out.get("slow", 20))
        if f >= s:
            raise ValueError(f"快线天数（{f}）必须小于慢线天数（{s}）")
    if strategy_type == "macd_cross":
        f = int(out.get("fast", 12))
        s = int(out.get("slow", 26))
        if f >= s:
            raise ValueError(f"MACD 快线周期（{f}）必须小于慢线周期（{s}）")
    return out


def run_custom(bars: List[Bar], strategy_type: str, params: Dict[str, Any],
               filters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """跑一次自定义配置，返回和策略详情页同构的结果"""
    if strategy_type not in SIGNAL_FUNCS:
        raise ValueError(f"不支持的策略类型：{strategy_type}")

    r = run_backtest(bars, strategy_type, params, filters=filters)
    d = r.to_dict()
    d.update({
        "strategy_name": spec_for(strategy_type)["label"],
        "strategy_type": strategy_type,
        "params": params,
        "data_cutoff": bars[-1].trade_date,
        "bar_count": len(bars),
        "range": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
        "latest_signal_explain": explain_signal(bars, strategy_type, params),
        "filters_desc": r.filters_desc,
    })
    return d


def hysteresis_analysis(bars: List[Bar], params: Dict[str, Any],
                        values: Optional[List[float]] = None) -> Dict[str, Any]:
    """
    回滞带对比：同一套均线参数，回滞取 0 / 0.5 / 1 / 2 / 3 / 5 各跑一遍。

    这是用户最关心的"这个参数到底该设多少"，
    光给一个数字没用，得把每个取值的代价和收益都摆出来。
    """
    vals = values or [0, 0.5, 1, 2, 3, 5]
    base_params = {k: v for k, v in params.items() if k != "hysteresis"}

    rows = []
    best = None
    for v in vals:
        p = {**base_params, "hysteresis": float(v)}
        try:
            r = run_backtest(bars, "ma_cross", p)
        except Exception as e:
            rows.append({"hysteresis": v, "error": str(e)[:80]})
            continue
        row = {
            "hysteresis": v,
            "total_return_pct": round(r.total_return * 100, 2),
            "annual_return_pct": round(r.annual_return * 100, 2),
            "max_drawdown_pct": round(r.max_drawdown * 100, 2),
            "sharpe": round(r.sharpe, 2),
            "win_rate_pct": round(r.win_rate * 100, 1),
            "trade_count": r.trade_count,
            "exposure_pct": round(r.exposure * 100, 1),
        }
        rows.append(row)
        # 用「收益 - 回撤」做粗排，只作为参考，不替他决定
        score = row["total_return_pct"] - row["max_drawdown_pct"]
        if best is None or score > best["score"]:
            best = {"score": score, **row}

    base = rows[0] if rows else {}
    if base and best and base.get("trade_count") is not None:
        cut = base["trade_count"] - best["trade_count"]
        conclusion = (
            f"回滞从 0 调到 {best['hysteresis']}%，交易次数从 {base['trade_count']} 降到 "
            f"{best['trade_count']}（少了 {cut} 次），收益 {base['total_return_pct']}% → "
            f"{best['total_return_pct']}%，回撤 {base['max_drawdown_pct']}% → {best['max_drawdown_pct']}%。"
        )
        if cut <= 0:
            conclusion += " 但这次回滞没能减少交易 —— 说明你的均线差距本来就没在临界区反复摩擦。"
    else:
        conclusion = "数据不足，无法比较。"

    return {
        "rows": rows,
        "best_hysteresis": best["hysteresis"] if best else None,
        "conclusion": conclusion,
        "note": (
            "选哪一档取决于你想少折腾还是想早一点进场。"
            "回滞越大，信号越晚、交易越少、手续费越省，但可能错过急涨的开头。"
        ),
    }
