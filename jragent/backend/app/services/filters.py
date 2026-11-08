# -*- coding: utf-8 -*-
"""
信号过滤器：让 AI 能「设计策略」，而不只是调参数。

设计原则（很重要）：
1. 所有过滤器只限制【买入】，不影响【卖出】。
   这样永远不会出现"该止损却卖不掉"的灾难。
2. 过滤器都是预定义好的、可验证的计算，AI 只能选和配阈值，
   不能写代码。安全、可复现、可解释。
3. 每个过滤器都能用一句人话解释清楚它在干什么。

这样 AI 就可以说："我给你加了个 ADX 过滤，震荡期不投入"，
并且真的跑出结果，而不是嘴上说说。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .backtest import Bar, sma, rolling_std, rsi


# ============================================================
# 各过滤器的「允许买入」序列计算
# ============================================================
def _adx_trend(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """ADX 高于阈值才允许买入：震荡市不参与"""
    n = int(p.get("n", 14))
    thr = float(p.get("threshold", 20))

    plus, minus, trs = [], [], []
    for i, b in enumerate(bars):
        if i == 0:
            plus.append(0.0); minus.append(0.0); trs.append(b.high - b.low); continue
        up = b.high - bars[i - 1].high
        dn = bars[i - 1].low - b.low
        plus.append(up if (up > dn and up > 0) else 0.0)
        minus.append(dn if (dn > up and dn > 0) else 0.0)
        pc = bars[i - 1].close
        trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))

    def wilder(vals):
        out: List[Optional[float]] = []
        for i in range(len(vals)):
            if i < n - 1:
                out.append(None)
            elif i == n - 1:
                out.append(sum(vals[:n]))
            else:
                out.append((out[-1] or 0) - (out[-1] or 0) / n + vals[i])
        return out

    st, sp, sm = wilder(trs), wilder(plus), wilder(minus)
    dxs: List[Optional[float]] = []
    for i in range(len(bars)):
        if not st[i] or st[i] < 1e-9 or sp[i] is None or sm[i] is None:
            dxs.append(None); continue
        pdi = 100 * sp[i] / st[i]
        mdi = 100 * sm[i] / st[i]
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 1e-9 else 0.0)

    adx: List[Optional[float]] = []
    for i in range(len(dxs)):
        if i < 2 * n - 2 or dxs[i] is None:
            adx.append(None); continue
        win = [x for x in dxs[i - n + 1:i + 1] if x is not None]
        adx.append(sum(win) / len(win) if win else None)

    # 数据不足时先不拦，避免前 30 天完全不能买
    return [True if v is None else v >= thr for v in adx]


def _price_above_ma(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """收盘价站上 N 日均线才允许买入：只在上升趋势里做"""
    n = int(p.get("n", 60))
    closes = [b.close for b in bars]
    m = sma(closes, n)
    return [True if mm is None else c >= mm for c, mm in zip(closes, m)]


def _vol_target(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """近期波动率高于阈值时不买入：太颠的时候先躲开"""
    n = int(p.get("n", 20))
    thr = float(p.get("max_pct", 4.0))     # 日波动率上限 %
    closes = [b.close for b in bars]
    sd = rolling_std(closes, n)
    return [True if s is None or c <= 0 else (s / c * 100) <= thr
            for s, c in zip(sd, closes)]


def _volume_confirm(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """成交量达到近期均量的倍数才买入：放量才算真突破"""
    n = int(p.get("n", 5))
    mult = float(p.get("mult", 1.0))
    vols = [b.volume for b in bars]
    m = sma(vols, n)
    return [True if mm is None or mm <= 0 else v >= mm * mult
            for v, mm in zip(vols, m)]


def _rsi_not_overbought(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """RSI 没超买才买入：不追高"""
    n = int(p.get("n", 14))
    thr = float(p.get("max", 70))
    closes = [b.close for b in bars]
    r = rsi(closes, n)
    return [True if v is None else v <= thr for v in r]


def _no_close_below_ma(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """价格没跌破 N 日均线太多才买：躲开急跌"""
    n = int(p.get("n", 20))
    gap = float(p.get("max_gap_pct", 5.0))
    closes = [b.close for b in bars]
    m = sma(closes, n)
    return [True if mm is None or mm <= 0 else (mm - c) / mm * 100 <= gap
            for c, mm in zip(closes, m)]


# 名字 → (计算函数, 人话描述, 默认参数)
FILTERS: Dict[str, Dict[str, Any]] = {
    "adx_trend": {
        "fn": _adx_trend,
        "label": "ADX 趋势过滤",
        "desc": "ADX 高于阈值才买入，震荡期空仓不参与",
        "defaults": {"n": 14, "threshold": 20},
    },
    "price_above_ma": {
        "fn": _price_above_ma,
        "label": "长期均线过滤",
        "desc": "收盘价站上 N 日均线才买入，只在上升趋势中做",
        "defaults": {"n": 60},
    },
    "vol_target": {
        "fn": _vol_target,
        "label": "波动率过滤",
        "desc": "近期日波动率超过阈值时不买入，躲开剧烈震荡",
        "defaults": {"n": 20, "max_pct": 4.0},
    },
    "volume_confirm": {
        "fn": _volume_confirm,
        "label": "放量确认",
        "desc": "成交量达到近期均量的倍数才买入，过滤假突破",
        "defaults": {"n": 5, "mult": 1.0},
    },
    "rsi_not_overbought": {
        "fn": _rsi_not_overbought,
        "label": "RSI 不追高",
        "desc": "RSI 低于阈值才买入，避免在超买区接盘",
        "defaults": {"n": 14, "max": 70},
    },
    "no_close_below_ma": {
        "fn": _no_close_below_ma,
        "label": "急跌回避",
        "desc": "价格跌破均线超过一定幅度时不买入，避开下跌中继",
        "defaults": {"n": 20, "max_gap_pct": 5.0},
    },
}


def apply_filters(bars: List[Bar], target: List[int],
                  filters: List[Dict[str, Any]]) -> (List[int], List[Dict[str, Any]]):
    """
    把过滤器作用到买入信号上。
    只改「0 → 保持 0」或「1 → 0」，绝不把卖出改成买入。
    返回 (新信号, 每个过滤器的拦截统计)
    """
    if not filters:
        return target, []

    n = len(bars)
    allowed = [True] * n
    stats: List[Dict[str, Any]] = []

    for f in filters:
        name = f.get("name") or f.get("code")
        spec = FILTERS.get(name)
        if not spec:
            continue
        params = {**spec["defaults"], **(f.get("params") or {})}
        try:
            ok_seq = spec["fn"](bars, params)
        except Exception:
            continue

        blocked = sum(1 for i in range(n) if allowed[i] and not ok_seq[i])
        stats.append({
            "name": name,
            "label": spec["label"],
            "desc": spec["desc"],
            "params": params,
            "blocked_days": blocked,
            "blocked_pct": round(blocked / n * 100, 1) if n else 0,
        })
        allowed = [a and b for a, b in zip(allowed, ok_seq)]

    out = list(target)
    for i in range(n):
        if out[i] == 1 and not allowed[i]:
            out[i] = 0        # 只是不买，不影响卖出
    return out, stats


def describe_filters(filters: List[Dict[str, Any]]) -> str:
    """把过滤器列表翻译成一句人话，给 AI 和前端展示"""
    if not filters:
        return "无额外过滤条件"
    parts = []
    for f in filters:
        spec = FILTERS.get(f.get("name") or f.get("code"))
        if spec:
            parts.append(spec["desc"])
    return "；".join(parts) if parts else "无额外过滤条件"
