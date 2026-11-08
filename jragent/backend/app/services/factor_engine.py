# -*- coding: utf-8 -*-
"""
通用因子过滤引擎。

和 filters.py 的区别：
- filters.py 是 6 个语义化的固定过滤器（给新手用，"震荡期不投入"）
- 这个引擎是**任意因子 + 任意阈值**，AI 可以从因子库里自由组合

统一语法（AI 只需要学这一套）：
    {"factor": "rsi", "op": "below", "value": 70}
    {"factor": "adx", "op": "above", "value": 20}
    {"factor": "volume_ratio", "op": "above", "value": 1.5}
    {"factor": "price_above_ma", "params": {"n": 60}}      # 布尔型，不需要 op/value

硬约束：所有条件只限制【买入】，绝不影响【卖出】。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .backtest import Bar, sma, rolling_std, rsi
from .factor_demo import (
    ema, atr_series, adx_series, macd_series, kdj_series, rsi_series,
)


# ============================================================
# 因子取值：返回与 bars 等长的数值序列
# ============================================================
def _closes(bars): return [b.close for b in bars]
def _vols(bars): return [b.volume for b in bars]


def _f_ma_gap(bars: List[Bar], p: Dict[str, Any]):
    """收盘价相对 N 日均线的乖离（%），正数表示在均线上方"""
    n = int(p.get("n", 20))
    m = sma(_closes(bars), n)
    return [None if mm is None or mm <= 0 else (c - mm) / mm * 100
            for c, mm in zip(_closes(bars), m)]


def _f_boll_width(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 20))
    mid = sma(_closes(bars), n)
    sd = rolling_std(_closes(bars), n)
    return [None if m is None or s is None or m <= 0 else (4 * s / m) * 100
            for m, s in zip(mid, sd)]


def _f_atr_pct(bars: List[Bar], p: Dict[str, Any]):
    """ATR 占价格的比例（%），跨股票可比"""
    n = int(p.get("n", 14))
    a = atr_series(bars, n)
    return [None if v is None or b.close <= 0 else v / b.close * 100
            for v, b in zip(a, bars)]


def _f_std_pct(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 20))
    sd = rolling_std(_closes(bars), n)
    return [None if s is None or c <= 0 else s / c * 100
            for s, c in zip(sd, _closes(bars))]


def _f_bias(bars: List[Bar], p: Dict[str, Any]):
    return _f_ma_gap(bars, p)


def _f_willr(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 14))
    out: List[Optional[float]] = []
    for i in range(len(bars)):
        if i < n - 1:
            out.append(None); continue
        win = bars[i - n + 1:i + 1]
        hi = max(x.high for x in win); lo = min(x.low for x in win)
        out.append(None if hi - lo < 1e-9 else (hi - bars[i].close) / (hi - lo) * -100)
    return out


def _f_cci(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 14))
    tp = [(b.high + b.low + b.close) / 3 for b in bars]
    ma_tp = sma(tp, n)
    out: List[Optional[float]] = []
    for i in range(len(bars)):
        if i < n - 1 or ma_tp[i] is None:
            out.append(None); continue
        win = tp[i - n + 1:i + 1]
        md = sum(abs(x - (ma_tp[i] or 0)) for x in win) / len(win)
        out.append(0.0 if md < 1e-9 else round((tp[i] - (ma_tp[i] or 0)) / (0.015 * md), 2))
    return out


def _f_kdj_k(bars: List[Bar], p: Dict[str, Any]):
    k, _, _ = kdj_series(bars, int(p.get("n", 9)))
    return k


def _f_macd_dif(bars: List[Bar], p: Dict[str, Any]):
    dif, _, _ = macd_series(_closes(bars))
    return dif


def _f_volume_ratio(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 5))
    m = sma(_vols(bars), n)
    return [None if mm is None or mm <= 0 else v / mm for v, mm in zip(_vols(bars), m)]


def _f_obv_slope(bars: List[Bar], p: Dict[str, Any]):
    """OBV 的 N 日变化率（%），用来判断资金是在流入还是流出"""
    n = int(p.get("n", 20))
    obv = [0.0]
    for i in range(1, len(bars)):
        prev = obv[-1]
        if bars[i].close > bars[i - 1].close:
            obv.append(prev + bars[i].volume)
        elif bars[i].close < bars[i - 1].close:
            obv.append(prev - bars[i].volume)
        else:
            obv.append(prev)
    out: List[Optional[float]] = []
    base = max(1.0, max(abs(v) for v in obv) or 1.0)
    for i in range(len(obv)):
        if i < n:
            out.append(None); continue
        prev = obv[i - n]
        out.append(None if abs(prev) < 1e-9 else (obv[i] - prev) / base * 100)
    return out


def _f_drawdown(bars: List[Bar], p: Dict[str, Any]):
    peak = 0.0
    out = []
    for b in bars:
        peak = max(peak, b.close)
        out.append(None if peak <= 0 else (b.close - peak) / peak * 100)
    return out


# ---- 布尔型因子（直接给出"能不能买"）----
def _b_price_above_ma(bars: List[Bar], p: Dict[str, Any]):
    n = int(p.get("n", 60))
    m = sma(_closes(bars), n)
    return [True if mm is None else c >= mm for c, mm in zip(_closes(bars), m)]


def _b_ma_slope(bars: List[Bar], p: Dict[str, Any]):
    """N 日均线本身在向上走"""
    n = int(p.get("n", 20))
    m = sma(_closes(bars), n)
    out = []
    for i in range(len(m)):
        if i < 1 or m[i] is None or m[i - 1] is None:
            out.append(True)
        else:
            out.append(m[i] >= m[i - 1])
    return out



def _param_text(spec: dict, p: dict) -> str:
    """
    只显示真正改过的参数。

    之前直接把整个 dict 拼进去，会得到「ADX 趋势强度 高于 20.5（参数 {'n': 14}）」
    这种又长又没信息量的描述 —— n=14 本来就是默认值，写出来只会干扰阅读。
    """
    defaults = spec.get("defaults") or {}
    diff = {k: v for k, v in (p or {}).items() if defaults.get(k) != v}
    if not diff:
        return ""
    return "，".join(f"{k}={v}" for k, v in diff.items())


NUMERIC: Dict[str, Dict[str, Any]] = {
    "rsi":        {"fn": lambda b, p: rsi_series(_closes(b), int(p.get("n", 14))),
                   "label": "RSI", "defaults": {"n": 14}, "range": [0, 100]},
    "adx":        {"fn": lambda b, p: adx_series(b, int(p.get("n", 14))),
                   "label": "ADX 趋势强度", "defaults": {"n": 14}, "range": [0, 100]},
    "ma_gap":     {"fn": _f_ma_gap, "label": "价格偏离均线", "defaults": {"n": 20}},
    "boll_width": {"fn": _f_boll_width, "label": "布林带宽度", "defaults": {"n": 20}},
    "atr_pct":    {"fn": _f_atr_pct, "label": "ATR 波动占比", "defaults": {"n": 14}},
    "std_pct":    {"fn": _f_std_pct, "label": "标准差占比", "defaults": {"n": 20}},
    "bias":       {"fn": _f_bias, "label": "乖离率", "defaults": {"n": 6}},
    "willr":      {"fn": _f_willr, "label": "威廉指标", "defaults": {"n": 14}, "range": [-100, 0]},
    "cci":        {"fn": _f_cci, "label": "CCI", "defaults": {"n": 14}},
    "kdj_k":      {"fn": _f_kdj_k, "label": "KDJ 的 K 值", "defaults": {"n": 9}, "range": [0, 100]},
    "macd_dif":   {"fn": _f_macd_dif, "label": "MACD DIF", "defaults": {}},
    "volume_ratio": {"fn": _f_volume_ratio, "label": "量比", "defaults": {"n": 5}},
    "obv_slope":  {"fn": _f_obv_slope, "label": "OBV 变化率", "defaults": {"n": 20}},
    "drawdown":   {"fn": _f_drawdown, "label": "当前回撤", "defaults": {}},
}

BOOLEAN: Dict[str, Dict[str, Any]] = {
    "price_above_ma": {"fn": _b_price_above_ma, "label": "价格站上均线", "defaults": {"n": 60}},
    "ma_slope_up":    {"fn": _b_ma_slope, "label": "均线向上", "defaults": {"n": 20}},
}

OPS = {
    "above": lambda v, t: v > t,
    "below": lambda v, t: v < t,
    "above_eq": lambda v, t: v >= t,
    "below_eq": lambda v, t: v <= t,
}


def list_capabilities() -> Dict[str, Any]:
    """给 AI 看的「你能用哪些因子」清单"""
    return {
        "numeric_factors": [
            {"code": k, "label": v["label"], "defaults": v["defaults"],
             **({"range": v["range"]} if "range" in v else {})}
            for k, v in NUMERIC.items()
        ],
        "boolean_factors": [
            {"code": k, "label": v["label"], "defaults": v["defaults"]}
            for k, v in BOOLEAN.items()
        ],
        "ops": list(OPS.keys()),
        "syntax": [
            '{"factor":"rsi","op":"below","value":70}',
            '{"factor":"adx","op":"above","value":20,"params":{"n":14}}',
            '{"factor":"price_above_ma","params":{"n":60}}',
        ],
    }


def apply_conditions(bars: List[Bar], target: List[int],
                     conditions: List[Dict[str, Any]]):
    """
    把任意因子条件作用到买入信号上。
    返回 (新信号, 每个条件的拦截统计)

    兼容两种写法：
      1. {"name": "adx_trend", "params": {...}}   → 交给 filters.py 的预设
      2. {"factor": "rsi", "op": "below", "value": 70} → 本引擎
    """
    if not conditions:
        return target, []

    n = len(bars)
    allowed = [True] * n
    stats: List[Dict[str, Any]] = []

    # 先分离出预设过滤器，交给 filters.py
    presets = [c for c in conditions if c.get("name") and not c.get("factor")]
    if presets:
        try:
            from .filters import apply_filters
        except ImportError:
            from filters import apply_filters
        target, preset_stats = apply_filters(bars, target, presets)
        stats.extend(preset_stats)
        # 预设已经改过 target，这里只需要处理因子条件
        # 为避免重复过滤，预设那部分从 allowed 里排除

    for c in conditions:
        if c.get("name") and not c.get("factor"):
            continue
        code = c.get("factor")
        params = dict(c.get("params") or {})

        if code in BOOLEAN:
            spec = BOOLEAN[code]
            p = {**spec["defaults"], **params}
            try:
                seq = spec["fn"](bars, p)
            except Exception:
                continue
            blocked = sum(1 for i in range(n) if allowed[i] and not seq[i])
            stats.append({
                "name": code, "label": spec["label"],
                "desc": (f"{spec['label']}（{_param_text(spec, p)}）"
                         if _param_text(spec, p) else spec["label"]),
                "params": p, "blocked_days": blocked,
                "blocked_pct": round(blocked / n * 100, 1) if n else 0,
            })
            allowed = [a and b for a, b in zip(allowed, seq)]
            continue

        if code not in NUMERIC:
            continue
        spec = NUMERIC[code]
        p = {**spec["defaults"], **params}
        op = c.get("op") or "below"
        cmp = OPS.get(op)
        if cmp is None or c.get("value") is None:
            continue
        try:
            seq = spec["fn"](bars, p)
        except Exception:
            continue

        def ok_at(i):
            v = seq[i]
            return True if v is None else cmp(v, c["value"])

        oks = [ok_at(i) for i in range(n)]
        blocked = sum(1 for i in range(n) if allowed[i] and not oks[i])
        op_cn = {"above": "高于", "below": "低于",
                 "above_eq": "不低于", "below_eq": "不高于"}.get(op, op)
        stats.append({
            "name": code, "label": spec["label"],
            "desc": (f"{spec['label']} {op_cn} {c['value']}"
                     + (f"（{_param_text(spec, p)}）" if _param_text(spec, p) else "")),
            "params": p, "op": op, "value": c["value"],
            "blocked_days": blocked,
            "blocked_pct": round(blocked / n * 100, 1) if n else 0,
        })
        allowed = [a and b for a, b in zip(allowed, oks)]

    out = list(target)
    for i in range(n):
        if out[i] == 1 and not allowed[i]:
            out[i] = 0       # 只限制买入
    return out, stats


def describe_conditions(stats: List[Dict[str, Any]]) -> str:
    if not stats:
        return "无额外过滤条件"
    return "；".join(s["desc"] for s in stats)


# ============================================================
# 扩展因子注册
# 放在文件末尾，避免和 extended_factors 形成循环导入：
# extended_factors 需要 factor_demo.rsi_series，而那边又依赖 backtest。
# 挂进来之后，因子搜索、回测过滤、因子可视化三处会同时多出这些因子。
# ============================================================
def _load_extended() -> int:
    """
    挂载扩展因子包。分两批：
      extended_factors  第一批（ROC/MFI/PSY/Z 分数…）
      public_indicators 第二批（DMI 家族/TRIX/CMO/AD/EMV/梅斯线/K 线形态…）

    每个包单独 try —— 一个包坏了只丢那一批，主引擎和其它包照常工作。
    """
    import sys as _sys
    total = 0
    for mod_name in ("extended_factors", "public_indicators"):
        try:
            mod = __import__(f"{__package__}.{mod_name}", fromlist=["register_into"])
        except Exception:
            try:
                mod = __import__(mod_name, fromlist=["register_into"])
            except Exception:
                continue
        try:
            total += mod.register_into(_sys.modules[__name__])
        except Exception:
            continue
    return total


try:
    EXTENDED_COUNT = _load_extended()
except Exception:            # 扩展因子坏了不能拖垮主引擎
    EXTENDED_COUNT = 0
