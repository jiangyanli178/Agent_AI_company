"""
扩展因子库 —— 一批公开、经典、能用真实行情算出来的技术因子。

为什么不直接塞表达式字符串：
`custom_factor` 的表达式引擎只接受数字常量，不支持参数（n=20 这种），
而这里每个因子都要能调周期。所以用 Python 函数实现，注册进
`factor_engine.NUMERIC / BOOLEAN` 后，回测过滤、因子搜索、可视化会**同时**生效。

设计约束：
1. 只依赖 Bar 里的 OHLCV，不引入新数据源 —— 保证换台机器也能算。
2. 前 n 个点返回 None，绝不 backfill，避免未来函数。
3. 每个因子都有明确的取值范围（range），前端画参考线要用。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    from .backtest import Bar
    from .factor_demo import sma, ema, rolling_std, rsi_series
except ImportError:
    from backtest import Bar
    from factor_demo import sma, ema, rolling_std, rsi_series

V = Optional[float]


def _closes(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


# ============================================================
# 数值因子
# ============================================================
def roc(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """ROC 变动率：现在比 n 天前涨了百分之几"""
    n = max(1, int(p.get("n", 12)))
    c = _closes(bars)
    out: List[V] = []
    for i in range(len(c)):
        if i < n or c[i - n] <= 0:
            out.append(None)
        else:
            out.append((c[i] / c[i - n] - 1) * 100)
    return out


def momentum(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """动量：现在价格是 n 天前的几倍（1.0 = 没动）"""
    n = max(1, int(p.get("n", 20)))
    c = _closes(bars)
    out: List[V] = []
    for i in range(len(c)):
        out.append(None if i < n or c[i - n] <= 0 else c[i] / c[i - n])
    return out


def volatility(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    年化历史波动率（%）。
    日收益率的标准差 × sqrt(252)，是「这只票平时晃多厉害」的标准度量。
    """
    n = max(2, int(p.get("n", 20)))
    c = _closes(bars)
    rets: List[V] = [None]
    for i in range(1, len(c)):
        rets.append(c[i] / c[i - 1] - 1 if c[i - 1] > 0 else None)

    out: List[V] = []
    for i in range(len(rets)):
        if i < n:
            out.append(None)
            continue
        seg = [r for r in rets[i - n + 1:i + 1] if r is not None]
        if len(seg) < max(3, n // 2):
            out.append(None)
            continue
        mean = sum(seg) / len(seg)
        var = sum((x - mean) ** 2 for x in seg) / (len(seg) - 1)
        out.append(math.sqrt(var) * math.sqrt(252) * 100)
    return out


def mfi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    MFI 资金流量指标（0~100）：把「量」加进 RSI 的思路。
    典型价 = (高+低+收)/3，涨日的资金流入、跌日的资金流出，
    超过 80 算过热，低于 20 算超卖。
    """
    n = max(2, int(p.get("n", 14)))
    tp, flow = [], []
    for b in bars:
        typical = (b.high + b.low + b.close) / 3
        tp.append(typical)
        flow.append(typical * b.volume)

    pos: List[float] = [0.0]
    neg: List[float] = [0.0]
    for i in range(1, len(bars)):
        if tp[i] > tp[i - 1]:
            pos.append(flow[i]); neg.append(0.0)
        elif tp[i] < tp[i - 1]:
            pos.append(0.0); neg.append(flow[i])
        else:
            pos.append(0.0); neg.append(0.0)

    out: List[V] = []
    for i in range(len(bars)):
        if i < n:
            out.append(None)
            continue
        sp = sum(pos[i - n + 1:i + 1])
        sn = sum(neg[i - n + 1:i + 1])
        if sn <= 0:
            out.append(100.0 if sp > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + sp / sn))
    return out


def psy(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """心理线：最近 n 天里上涨的天数占比（%）"""
    n = max(2, int(p.get("n", 12)))
    c = _closes(bars)
    up = [0] * len(c)
    for i in range(1, len(c)):
        up[i] = 1 if c[i] > c[i - 1] else 0
    out: List[V] = []
    for i in range(len(c)):
        out.append(None if i < n else sum(up[i - n + 1:i + 1]) / n * 100)
    return out


def aroon_up(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    阿隆上线（0~100）：n 天内最高价出现在几天前。
    越接近 100 说明刚创新高（上升趋势强），接近 0 说明最高点很久以前了。
    """
    n = max(1, int(p.get("n", 25)))
    out: List[V] = []
    for i in range(len(bars)):
        if i < n:
            out.append(None)
            continue
        seg = bars[i - n:i + 1]
        hi = max(b.high for b in seg)
        # 取最近一次出现最高点的位置
        pos = max(k for k, b in enumerate(seg) if b.high == hi)
        out.append(pos / n * 100)
    return out


def zscore(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """价格 Z 分数：(现价 - n 日均价) / n 日标准差，衡量「偏离多少个标准差」"""
    n = max(2, int(p.get("n", 20)))
    c = _closes(bars)
    m = sma(c, n)
    s = rolling_std(c, n)
    out: List[V] = []
    for i in range(len(c)):
        if m[i] is None or s[i] is None or s[i] <= 1e-9:
            out.append(None)
        else:
            out.append((c[i] - m[i]) / s[i])
    return out


def vwap_dev(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    偏离成交量加权均价（%）：现价比「这段时间大家平均买入成本」高多少。
    正数说明多数人浮盈，负数说明多数人被套。
    """
    n = max(2, int(p.get("n", 20)))
    out: List[V] = []
    for i in range(len(bars)):
        if i < n:
            out.append(None)
            continue
        seg = bars[i - n + 1:i + 1]
        vol = sum(b.volume for b in seg)
        if vol <= 0:
            out.append(None)
            continue
        vwap = sum((b.high + b.low + b.close) / 3 * b.volume for b in seg) / vol
        out.append((bars[i].close / vwap - 1) * 100 if vwap > 0 else None)
    return out


def skew(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    收益偏度：分布是「偶尔暴涨」还是「偶尔暴跌」。
    正偏 = 小跌多、大涨少；负偏 = 小涨多、大跌狠（更危险）。
    """
    n = max(3, int(p.get("n", 20)))
    c = _closes(bars)
    rets: List[V] = [None]
    for i in range(1, len(c)):
        rets.append(c[i] / c[i - 1] - 1 if c[i - 1] > 0 else None)

    out: List[V] = []
    for i in range(len(rets)):
        if i < n:
            out.append(None)
            continue
        seg = [r for r in rets[i - n + 1:i + 1] if r is not None]
        if len(seg) < 5:
            out.append(None)
            continue
        mean = sum(seg) / len(seg)
        m2 = sum((x - mean) ** 2 for x in seg) / len(seg)
        if m2 <= 1e-12:
            out.append(0.0)
            continue
        m3 = sum((x - mean) ** 3 for x in seg) / len(seg)
        out.append(m3 / (m2 ** 1.5))
    return out


NUMERIC_FACTORS: Dict[str, Dict[str, Any]] = {
    "roc":        {"fn": roc, "label": "ROC 变动率", "defaults": {"n": 12}},
    "momentum":   {"fn": momentum, "label": "动量", "defaults": {"n": 20}},
    "volatility": {"fn": volatility, "label": "年化波动率", "defaults": {"n": 20}},
    "mfi":        {"fn": mfi, "label": "MFI 资金流量", "defaults": {"n": 14}, "range": [0, 100]},
    "psy":        {"fn": psy, "label": "心理线 PSY", "defaults": {"n": 12}, "range": [0, 100]},
    "aroon_up":   {"fn": aroon_up, "label": "阿隆上线", "defaults": {"n": 25}, "range": [0, 100]},
    "zscore":     {"fn": zscore, "label": "价格 Z 分数", "defaults": {"n": 20}},
    "vwap_dev":   {"fn": vwap_dev, "label": "偏离筹码均价", "defaults": {"n": 20}},
    "skew":       {"fn": skew, "label": "收益偏度", "defaults": {"n": 20}},
}


# ============================================================
# 布尔因子（只回答「今天符不符合」，不带阈值）
# ============================================================
def b_volume_up(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """放量：今天成交量是最近 n 天均量的 k 倍以上"""
    n = max(2, int(p.get("n", 20)))
    k = float(p.get("k", 1.5))
    vols = [b.volume for b in bars]
    m = sma(vols, n)
    return [bool(m[i] and m[i] > 0 and vols[i] >= m[i] * k) for i in range(len(bars))]


def b_gap_up(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """跳空高开：今天开盘价比昨收高 pct% 以上"""
    pct = float(p.get("pct", 1.0)) / 100.0
    out = [False]
    for i in range(1, len(bars)):
        out.append(bars[i].open >= bars[i - 1].close * (1 + pct))
    return out


def b_new_high(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """创 N 日新高（收盘价站上最近 n 天的最高价）"""
    n = max(2, int(p.get("n", 60)))
    out: List[bool] = []
    for i in range(len(bars)):
        if i < n:
            out.append(False)
            continue
        out.append(bars[i].close >= max(b.high for b in bars[i - n:i]))
    return out


def b_ma_golden(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """均线金叉当天：快线今天上穿慢线（只这一天为 True）"""
    fast_n = max(1, int(p.get("fast", 5)))
    slow_n = max(2, int(p.get("slow", 20)))
    c = _closes(bars)
    f, s = sma(c, fast_n), sma(c, slow_n)
    out = [False] * len(bars)
    for i in range(1, len(bars)):
        if None in (f[i], s[i], f[i - 1], s[i - 1]):
            continue
        out[i] = f[i - 1] <= s[i - 1] and f[i] > s[i]
    return out


def b_rsi_oversold(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """RSI 处于超卖区（低于阈值）"""
    try:
        from .factor_demo import rsi_series
    except ImportError:
        from factor_demo import rsi_series
    n = max(2, int(p.get("n", 14)))
    th = float(p.get("threshold", 30))
    seq = rsi_series(_closes(bars), n)
    return [bool(v is not None and v < th) for v in seq]


def b_close_above_vwap(bars: List[Bar], p: Dict[str, Any]) -> List[bool]:
    """收盘价站在筹码均价之上（多数持仓者浮盈）"""
    n = max(2, int(p.get("n", 20)))
    dev = vwap_dev(bars, {"n": n})
    return [bool(v is not None and v > 0) for v in dev]


BOOLEAN_FACTORS: Dict[str, Dict[str, Any]] = {
    "volume_up":       {"fn": b_volume_up, "label": "放量", "defaults": {"n": 20, "k": 1.5}},
    "gap_up":          {"fn": b_gap_up, "label": "跳空高开", "defaults": {"pct": 1.0}},
    "new_high":        {"fn": b_new_high, "label": "创 N 日新高", "defaults": {"n": 60}},
    "ma_golden_cross": {"fn": b_ma_golden, "label": "当日金叉", "defaults": {"fast": 5, "slow": 20}},
    "rsi_oversold":    {"fn": b_rsi_oversold, "label": "RSI 超卖", "defaults": {"n": 14, "threshold": 30}},
    "close_above_vwap": {"fn": b_close_above_vwap, "label": "站上筹码均价", "defaults": {"n": 20}},
}


def register_into(engine_module) -> int:
    """把扩展因子挂进 factor_engine（幂等，可重复调用）"""
    n = 0
    for code, spec in NUMERIC_FACTORS.items():
        if code not in engine_module.NUMERIC:
            engine_module.NUMERIC[code] = spec
            n += 1
    for code, spec in BOOLEAN_FACTORS.items():
        if code not in engine_module.BOOLEAN:
            engine_module.BOOLEAN[code] = spec
            n += 1
    return n
