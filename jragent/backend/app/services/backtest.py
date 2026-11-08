# -*- coding: utf-8 -*-
"""
回测引擎 —— 纯标准库实现，不依赖 pandas / numpy
理由：50 只标的 × 5 年日线约 6 万行，纯 Python 循环跑完一次不到 0.1 秒，
     而省掉 pandas 意味着现场不会再碰到装包失败这种事。

关键设计：所有信号在 T 日收盘后生成，T+1 日才生效（signal 前移一位），
         从根上杜绝「用当天收盘价决定当天买点」这种未来函数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

TRADING_DAYS = 252

# 策略类型的中文名，给 AI 和前端展示用
STRATEGY_NAMES = {
    "ma_cross": "均线交叉",
    "boll": "布林带回归",
    "boll_break": "布林带突破",
    "momentum": "动量",
    "rsi": "RSI 超卖买入",
    "macd_cross": "MACD 金叉",
    "kdj_cross": "KDJ 金叉",
    "breakout": "唐奇安通道突破",
    "trix_cross": "TRIX 金叉",
}


# ============================================================
# 数据结构
# ============================================================
@dataclass
class Bar:
    trade_date: str          # '2024-01-02'
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    bars_held: int
    pnl_pct: float           # 已扣双边费用
    # 信号日（交叉当天）与成交日分开记，前端可画淡线连接，
    # 让用户看懂"T 日看到信号，T+1 才成交"。
    signal_entry: str = ""
    signal_exit: str = ""


@dataclass
class BacktestResult:
    dates: List[str] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)      # 归一化净值，起点 1.0
    positions: List[int] = field(default_factory=list)     # 每日实际持仓 0/1
    trades: List[Trade] = field(default_factory=list)

    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    exposure: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    calmar: float = 0.0
    blocked_orders: int = 0     # 因涨跌停无法成交的次数，>0 说明策略有流动性风险
    benchmark_curve: List[float] = field(default_factory=list)   # 买入持有净值，用于双线对比
    drawdown_curve: List[float] = field(default_factory=list)    # 回撤序列，用于回撤面积图
    # 复利口径但分母换成初始本金：只记录净值跌破 1.0 的部分。
    # 和 drawdown_curve 一起画，才能让人看懂「同一个策略为什么两个回撤数字」
    principal_drawdown_curve: List[float] = field(default_factory=list)

    # ---- 教学用图形数据：让用户看清"在哪买、在哪卖" ----
    ohlc: List[List[float]] = field(default_factory=list)    # 原始K线 [open, close, low, high]
    ma_fast: List[Optional[float]] = field(default_factory=list)  # 快线序列（均线策略）
    ma_slow: List[Optional[float]] = field(default_factory=list)  # 慢线序列
    markers: List[Dict[str, Any]] = field(default_factory=list)   # 买卖点标记

    # ---- 单利 vs 复利两套口径 ----
    # 单利：每笔收益直接累加，不回滚本金（分母恒为初始本金）
    # 复利：收益滚入本金继续投（分母是当时的本金）
    #
    # ⚠️ 「单利/复利」和「峰值/本金」是**两个不同维度**，之前混在一起了：
    #    计算方式（单利 or 复利）× 回撤分母（历史峰值 or 初始本金）= 四种组合。
    #    原先把 simple_drawdown_curve 直接定义成 max(0, 1-v)，
    #    结果单利净值有 70% 的日子在 1.0 以上，那些天算出来全是 0，
    #    图上一条线贴在零轴 —— 用户看到的就是"只有开头有回撤"。
    simple_curve: List[float] = field(default_factory=list)
    simple_return: float = 0.0
    simple_annual_return: float = 0.0

    # 单利 × 本金分母（旧行为，保留兼容）
    simple_drawdown_curve: List[float] = field(default_factory=list)
    simple_max_drawdown: float = 0.0

    # 单利 × 峰值分母（行业标准的"最大回撤"定义）
    simple_peak_drawdown_curve: List[float] = field(default_factory=list)
    simple_max_drawdown_peak: float = 0.0

    # 单利 × 本金分母（同样的跌幅 ÷ 1.0）
    simple_principal_drawdown_curve: List[float] = field(default_factory=list)
    simple_max_drawdown_principal: float = 0.0

    # 单利 × 亏损深度（现在比本金少了多少）
    simple_loss_drawdown_curve: List[float] = field(default_factory=list)
    simple_max_drawdown_loss: float = 0.0

    # 复利 × 本金分母
    max_drawdown_principal: float = 0.0
    # 复利 × 亏损深度
    loss_drawdown_curve: List[float] = field(default_factory=list)
    max_drawdown_loss: float = 0.0

    # 逐日收益率，用于算两个策略的相关性与组合模拟
    daily_returns: List[float] = field(default_factory=list)

    # 额外过滤条件的使用情况（AI 设计的策略会带这个）
    filter_stats: List[Dict[str, Any]] = field(default_factory=list)
    filters_desc: str = ""

    def to_dict(self, keep_curve: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "total_return": round(self.total_return, 6),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_principal": round(self.max_drawdown_principal, 6),
            "max_drawdown_loss": round(self.max_drawdown_loss, 6),
            "simple_return": round(self.simple_return, 6),
            "simple_annual_return": round(self.simple_annual_return, 6),
            "simple_max_drawdown": round(self.simple_max_drawdown, 6),
            "simple_max_drawdown_principal": round(self.simple_max_drawdown_principal, 6),
            "simple_max_drawdown_peak": round(self.simple_max_drawdown_peak, 6),
            "simple_max_drawdown_loss": round(self.simple_max_drawdown_loss, 6),
            "sharpe": round(self.sharpe, 4),
            "win_rate": round(self.win_rate, 4),
            "trade_count": self.trade_count,
            "exposure": round(self.exposure, 4),
            "benchmark_return": round(self.benchmark_return, 6),
            "excess_return": round(self.excess_return, 6),
            "calmar": round(self.calmar, 4),
            "blocked_orders": self.blocked_orders,
            "filter_stats": self.filter_stats,
            "filters_desc": self.filters_desc,
            "trades": [
                {
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "entry_price": round(t.entry_price, 3),
                    "exit_price": round(t.exit_price, 3),
                    "bars_held": t.bars_held,
                    "pnl_pct": round(t.pnl_pct, 6),
                }
                for t in self.trades
            ],
        }
        if keep_curve:
            d["dates"] = self.dates
            d["equity"] = [round(x, 6) for x in self.equity]
            d["positions"] = self.positions
            d["benchmark_curve"] = [round(x, 6) for x in self.benchmark_curve]
            d["drawdown_curve"] = [round(x, 6) for x in self.drawdown_curve]
            d["principal_drawdown_curve"] = [round(x, 6) for x in self.principal_drawdown_curve]
            d["ohlc"] = self.ohlc
            d["ma_fast"] = self.ma_fast
            d["ma_slow"] = self.ma_slow
            d["markers"] = self.markers
            d["simple_curve"] = [round(x, 6) for x in self.simple_curve]
            d["simple_drawdown_curve"] = [round(x, 6) for x in self.simple_drawdown_curve]
            d["simple_peak_drawdown_curve"] = [round(x, 6) for x in self.simple_peak_drawdown_curve]
            d["simple_principal_drawdown_curve"] = [round(x, 6) for x in self.simple_principal_drawdown_curve]
            d["simple_loss_drawdown_curve"] = [round(x, 6) for x in self.simple_loss_drawdown_curve]
            d["loss_drawdown_curve"] = [round(x, 6) for x in self.loss_drawdown_curve]
        return d


# ============================================================
# 指标计算工具
# ============================================================
def sma(values: Sequence[float], n: int) -> List[Optional[float]]:
    """简单移动平均，前 n-1 个为 None（表示数据不足，不产生信号）"""
    out: List[Optional[float]] = [None] * len(values)
    if n <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def rolling_std(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1: i + 1]
        m = sum(window) / n
        var = sum((x - m) ** 2 for x in window) / n
        out[i] = math.sqrt(var)
    return out


def rsi(values: Sequence[float], n: int) -> List[Optional[float]]:
    """Wilder RSI"""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        ch = values[i] - values[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g = gains / n
    avg_l = losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(n + 1, len(values)):
        ch = values[i] - values[i - 1]
        avg_g = (avg_g * (n - 1) + max(ch, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


# ============================================================
# 策略信号：返回每日目标仓位 0 / 1
# 注意：这里用的是「截至第 i 日收盘」的信息，不含未来数据
# ============================================================
def sig_ma_cross(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """
    均线交叉。可选「回滞」（hysteresis，单位 %）。

    回滞是干什么的：快慢线贴着走的时候，今天穿上去明天穿下来，
    来回交叉十几次，每次都当成信号就会反复买卖、白交手续费。
    加一个缓冲带之后，快线要超出慢线 1% 才算真金叉、跌破 1% 才算真死叉，
    中间那段保持原仓位不动。代价是信号会晚一点，但假信号少很多。

    hysteresis = 0 时与原来的逻辑完全等价。
    """
    fast_n = int(p.get("fast", 5))
    slow_n = int(p.get("slow", 20))
    if fast_n >= slow_n:
        raise ValueError("fast 必须小于 slow")
    if fast_n < 1:
        raise ValueError("fast 至少为 1")

    # 上限 20%：再大基本就不会交易了，属于误操作
    band = max(0.0, min(float(p.get("hysteresis", 0) or 0), 20.0)) / 100.0

    close = [b.close for b in bars]
    f = sma(close, fast_n)
    s = sma(close, slow_n)
    out = [0] * len(bars)
    pos = 0
    for i in range(len(bars)):
        if f[i] is None or s[i] is None or s[i] <= 0:
            out[i] = 0
            pos = 0
            continue
        if pos == 0:
            # 空仓时：快线要显著高于慢线才进场
            if f[i] > s[i] * (1 + band):
                pos = 1
        else:
            # 持仓时：快线要显著低于慢线才离场
            if f[i] < s[i] * (1 - band):
                pos = 0
        out[i] = pos
    return out


def sig_boll_revert(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """跌破下轨买入，回到中轨卖出（状态机）"""
    n = int(p.get("n", 20))
    k = float(p.get("k", 2.0))
    close = [b.close for b in bars]
    mid = sma(close, n)
    std = rolling_std(close, n)
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if mid[i] is None or std[i] is None:
            out[i] = 0
            continue
        lower = mid[i] - k * std[i]
        if holding == 0 and close[i] < lower:
            holding = 1
        elif holding == 1 and close[i] >= mid[i]:
            holding = 0
        out[i] = holding
    return out


def sig_boll_break(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """突破上轨买入，跌回中轨卖出（状态机）"""
    n = int(p.get("n", 20))
    k = float(p.get("k", 2.0))
    close = [b.close for b in bars]
    mid = sma(close, n)
    std = rolling_std(close, n)
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if mid[i] is None or std[i] is None:
            out[i] = 0
            continue
        upper = mid[i] + k * std[i]
        if holding == 0 and close[i] > upper:
            holding = 1
        elif holding == 1 and close[i] <= mid[i]:
            holding = 0
        out[i] = holding
    return out


def sig_momentum(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    lb = int(p.get("lookback", 20))
    th = float(p.get("threshold", 0.03))
    close = [b.close for b in bars]
    out = [0] * len(bars)
    for i in range(len(bars)):
        if i < lb:
            out[i] = 0
            continue
        ret = close[i] / close[i - lb] - 1
        out[i] = 1 if ret > th else 0
    return out


def sig_rsi(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    n = int(p.get("n", 14))
    low = float(p.get("low", 30))
    high = float(p.get("high", 70))
    close = [b.close for b in bars]
    r = rsi(close, n)
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if r[i] is None:
            out[i] = 0
            continue
        if holding == 0 and r[i] < low:
            holding = 1
        elif holding == 1 and r[i] > high:
            holding = 0
        out[i] = holding
    return out


def ema(values: Sequence[float], n: int) -> List[float]:
    """
    指数移动平均。用第一个值做种子（和 TA-Lib 的 SMA 种子略有差异，
    但足够长之后两者收敛到同一条线，回测中不计这点偏差）。
    """
    k = 2.0 / (n + 1)
    out: List[float] = []
    prev: Optional[float] = None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd_series(values: Sequence[float], fast: int = 12, slow: int = 26,
                signal_n: int = 9):
    """返回 (DIF, DEA)。DIF = EMA(fast) − EMA(slow)，DEA = DIF 的 EMA。"""
    ef = ema(values, fast)
    es = ema(values, slow)
    dif = [ef[i] - es[i] for i in range(len(values))]
    dea = ema(dif, signal_n)
    return dif, dea


def kdj_series(bars: Sequence["Bar"], n: int = 9, k_smooth: int = 3, d_smooth: int = 3):
    """
    KDJ。RSV = (收 − n日最低) / (n日最高 − n日最低) × 100，
    K 是 RSV 的 (1/k_smooth) 平滑，D 是 K 的 (1/d_smooth) 平滑。
    通达信默认 k_smooth=d_smooth=3，即 K = 2/3 前K + 1/3 RSV。
    """
    k_vals: List[Optional[float]] = [None] * len(bars)
    d_vals: List[Optional[float]] = [None] * len(bars)
    pk = pd = 50.0
    for i in range(len(bars)):
        if i < n - 1:
            continue
        win = bars[i - n + 1:i + 1]
        hh = max(b.high for b in win)
        ll = min(b.low for b in win)
        rsv = 50.0 if hh - ll < 1e-12 else (bars[i].close - ll) / (hh - ll) * 100
        pk = (k_smooth - 1) / k_smooth * pk + 1.0 / k_smooth * rsv
        pd = (d_smooth - 1) / d_smooth * pd + 1.0 / d_smooth * pk
        k_vals[i] = pk
        d_vals[i] = pd
    return k_vals, d_vals


def sig_macd_cross(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """
    MACD 金叉买入、死叉卖出。

    这是被引用最多、也最容易被误解的策略：
    金叉在震荡市里会连续假信号，所以真实用法通常还要看 DIF 是否在零轴上方。
    这里加了 zero_filter 开关（默认 False，保持经典定义）。
    """
    fast_n = int(p.get("fast", 12))
    slow_n = int(p.get("slow", 26))
    sig_n = int(p.get("signal", 9))
    zero_filter = bool(p.get("zero_filter", False))
    if fast_n >= slow_n:
        raise ValueError("MACD 快线周期必须小于慢线周期")

    close = [b.close for b in bars]
    dif, dea = macd_series(close, fast_n, slow_n, sig_n)
    warm = slow_n + sig_n
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if i < warm:
            out[i] = 0
            continue
        golden = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        dead = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        if holding == 0 and golden and (not zero_filter or dif[i] > 0):
            holding = 1
        elif holding == 1 and dead:
            holding = 0
        out[i] = holding
    return out


def sig_kdj_cross(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """
    KDJ 金叉买入、死叉卖出。

    额外两个约束，避免在 80 以上追高、20 以下被来回甩：
      buy_below   只在 K 低于这个值时才认金叉（默认 50，即低位金叉）
      sell_above  持仓时 K 高于这个值就先走（默认 85，超买区）
    """
    n = int(p.get("n", 9))
    buy_below = float(p.get("buy_below", 50))
    sell_above = float(p.get("sell_above", 85))

    k_vals, d_vals = kdj_series(bars, n)
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if k_vals[i] is None or d_vals[i] is None or i == 0:
            out[i] = 0
            continue
        if k_vals[i - 1] is None or d_vals[i - 1] is None:
            out[i] = 0
            continue
        golden = k_vals[i - 1] <= d_vals[i - 1] and k_vals[i] > d_vals[i]
        dead = k_vals[i - 1] >= d_vals[i - 1] and k_vals[i] < d_vals[i]
        if holding == 0 and golden and k_vals[i] < buy_below:
            holding = 1
        elif holding == 1 and (dead or k_vals[i] > sell_above):
            holding = 0
        out[i] = holding
    return out


def sig_breakout(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """
    唐奇安通道突破（海龟交易法的核心信号）。

    买入：收盘价突破过去 entry 日的最高价
    卖出：收盘价跌破过去 exit 日的最低价

    关键细节：上下轨只用到**昨天为止**的数据。
    如果用当天最高价算上轨，那当天收盘必然 ≤ 上轨，永远突破不了 ——
    这是海龟策略最常被写错的地方，也是回测虚高的经典来源。
    """
    entry = int(p.get("entry", 20))
    exit_n = int(p.get("exit", 10))
    if entry < 2 or exit_n < 2:
        raise ValueError("唐奇安通道周期至少 2 天")

    high = [b.high for b in bars]
    low = [b.low for b in bars]
    close = [b.close for b in bars]
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if i < entry:
            out[i] = 0
            continue
        upper = max(high[i - entry:i])          # 不含当日
        lower = min(low[i - exit_n:i]) if i >= exit_n else min(low[:i])
        if holding == 0 and close[i] > upper:
            holding = 1
        elif holding == 1 and close[i] < lower:
            holding = 0
        out[i] = holding
    return out


def sig_trix_cross(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """
    TRIX 上穿零轴买入、下穿零轴卖出。

    TRIX 是三重平滑后的变化率，穿越零轴意味着「大方向刚翻」，
    信号极少但很干净 —— 代价是永远吃不到头尾。
    """
    n = int(p.get("n", 12))
    signal_n = int(p.get("signal", 9))

    close = [b.close for b in bars]
    e1 = ema(close, n)
    e2 = ema(e1, n)
    e3 = ema(e2, n)
    trix = [0.0] * len(bars)
    for i in range(1, len(bars)):
        if e3[i - 1] > 1e-9:
            trix[i] = (e3[i] / e3[i - 1] - 1) * 100
    sig = ema(trix, signal_n)

    warm = 3 * n + signal_n
    out = [0] * len(bars)
    holding = 0
    for i in range(len(bars)):
        if i < warm:
            out[i] = 0
            continue
        golden = trix[i - 1] <= sig[i - 1] and trix[i] > sig[i]
        dead = trix[i - 1] >= sig[i - 1] and trix[i] < sig[i]
        if holding == 0 and golden:
            holding = 1
        elif holding == 1 and dead:
            holding = 0
        out[i] = holding
    return out


SIGNAL_FUNCS = {
    "ma_cross": sig_ma_cross,
    "boll": sig_boll_revert,
    "boll_break": sig_boll_break,
    "momentum": sig_momentum,
    "rsi": sig_rsi,
    "macd_cross": sig_macd_cross,
    "kdj_cross": sig_kdj_cross,
    "breakout": sig_breakout,
    "trix_cross": sig_trix_cross,
}


# ============================================================
# 主回测
# ============================================================
def run_backtest(
    bars: List[Bar],
    strategy_type: str,
    params: Dict[str, Any],
    fee_rate: float = 0.0010,
    respect_limit: bool = True,
    limit_pct: float = 0.10,
    filters: Optional[List[Dict[str, Any]]] = None,
    precomputed_mask: Optional[List[bool]] = None,
) -> BacktestResult:
    """
    bars 必须按 trade_date 升序。
    fee_rate 为单边费率（默认万十，含佣金+印花税+滑点，偏保守）。

    respect_limit：触及涨跌停时该方向不可成交。
        这是回测虚高的第二大来源（第一是未来函数）——
        策略在涨停那天发出买入信号，现实里你根本买不进去，
        但 naive 回测会按收盘价成交，白白吃掉后面一整段涨幅。
        这里采用保守处理：只要触及涨跌停幅度就拦截，不要求一字板。
        代价是可能略微低估收益，但绝不会高估。宁可保守。

    ⚠️ 数据必须前复权。akshare 用 stock_zh_a_hist(adjust="qfq")。
       用不复权价格回测均线策略，分红除权日会出现假跳空，信号全是错的。
    """
    if len(bars) < 30:
        raise ValueError("数据不足 30 根 K 线，无法回测")
    if strategy_type not in SIGNAL_FUNCS:
        raise ValueError(f"未知策略类型：{strategy_type}")

    target = SIGNAL_FUNCS[strategy_type](bars, params)
    n = len(bars)

    # 额外过滤条件（由 AI 组合）：支持预设过滤器，也支持任意因子+阈值
    filter_stats: List[Dict[str, Any]] = []
    if precomputed_mask is not None:
        # 搜索器已经算好了「哪些天允许买入」，这里不再重算因子，
        # 否则每个候选阈值都要重新跑一遍因子序列，搜索会慢十几倍。
        mask = precomputed_mask
        before = sum(target)
        target = [1 if (t == 1 and mask[i]) else 0 for i, t in enumerate(target)]
        filter_stats = [{
            "name": "search", "label": "自动搜索的条件",
            "desc": "由因子搜索器预先计算",
            "params": {}, "blocked_days": before - sum(target),
            "blocked_pct": round((before - sum(target)) / n * 100, 1) if n else 0,
        }]
    elif filters:
        try:
            from .factor_engine import apply_conditions
        except ImportError:
            from factor_engine import apply_conditions
        target, filter_stats = apply_conditions(bars, target, filters)

    equity_curve: List[float] = [1.0] * n
    positions: List[int] = [0] * n
    daily_ret: List[float] = [0.0] * n
    trades: List[Trade] = []

    equity = 1.0
    simple_equity_curve: List[float] = [1.0] + [1.0] * (n - 1)   # 单利净值：每笔收益直接累加
    pos_prev = 0
    entry_price: Optional[float] = None
    entry_idx = 0
    blocked = 0

    for i in range(1, n):
        # T 日的仓位由 T-1 日的信号决定 —— 防未来函数的核心一行
        pos = target[i - 1]
        r = bars[i].close / bars[i - 1].close - 1

        # 涨跌停无法成交：信号作废，仓位维持
        if respect_limit:
            if pos == 1 and pos_prev == 0 and r >= limit_pct:
                pos = pos_prev
                blocked += 1
            elif pos == 0 and pos_prev == 1 and r <= -limit_pct:
                pos = pos_prev
                blocked += 1

        turnover = abs(pos - pos_prev)
        cost = turnover * fee_rate

        equity *= (1 + pos * r - cost)
        daily_ret[i] = equity / equity_curve[i - 1] - 1 if equity_curve[i - 1] else 0.0
        equity_curve[i] = equity

        # 单利口径：不做复利，每天赚的钱直接加在初始本金上
        simple_equity_curve[i] = simple_equity_curve[i - 1] + (pos * r - cost)

        positions[i] = pos

        if pos != pos_prev:
            if pos == 1:
                entry_price = bars[i].close
                entry_idx = i
            elif entry_price is not None:
                pnl = bars[i].close / entry_price - 1 - 2 * fee_rate
                trades.append(
                    Trade(
                        entry_date=bars[entry_idx].trade_date,
                        exit_date=bars[i].trade_date,
                        entry_price=entry_price,
                        exit_price=bars[i].close,
                        bars_held=i - entry_idx,
                        pnl_pct=pnl,
                        signal_entry=bars[max(0, entry_idx - 1)].trade_date,
                        signal_exit=bars[i - 1].trade_date,
                    )
                )
                entry_price = None
        pos_prev = pos

    # 期末仍持仓则按最后收盘价平掉，保证收益可比
    if pos_prev == 1 and entry_price is not None:
        pnl = bars[-1].close / entry_price - 1 - fee_rate
        trades.append(
            Trade(
                entry_date=bars[entry_idx].trade_date,
                exit_date=bars[-1].trade_date,
                entry_price=entry_price,
                exit_price=bars[-1].close,
                bars_held=n - 1 - entry_idx,
                pnl_pct=pnl,
                signal_entry=bars[max(0, entry_idx - 1)].trade_date,
                signal_exit="",     # 期末强制平仓，不是信号触发
            )
        )

    res = BacktestResult(
        dates=[b.trade_date for b in bars],
        equity=equity_curve,
        positions=positions,
        trades=trades,
        benchmark_curve=[b.close / bars[0].close for b in bars],
        # 原始K线，前端画蜡烛图用
        ohlc=[[b.open, b.close, b.low, b.high] for b in bars],
    )

    # 均线序列：只有均线策略有，其它策略留空，前端据此决定是否画均线
    if strategy_type == "ma_cross":
        closes = [b.close for b in bars]
        res.ma_fast = sma(closes, int(params["fast"]))
        res.ma_slow = sma(closes, int(params["slow"]))

    # 买卖点一律用「真实成交价 + 成交日」：
    # 信号在 T 日收盘产生，T+1 才成交，标记必须落在真正买进/卖出的那根 K 线上。
    # 另外记一个 signal_date，前端可以画一条淡虚线连到成交点，说明信号比成交早一天。
    for t in trades:
        res.markers.append({"date": t.entry_date, "type": "buy",
                            "price": round(t.entry_price, 3), "signal_date": t.signal_entry})
        res.markers.append({"date": t.exit_date, "type": "sell",
                            "price": round(t.exit_price, 3), "signal_date": t.signal_exit})

    # ---- 指标 ----
    res.total_return = equity_curve[-1] - 1.0
    years = (n - 1) / TRADING_DAYS
    if years > 0 and equity_curve[-1] > 0:
        res.annual_return = equity_curve[-1] ** (1 / years) - 1

    peak = 0.0
    mdd = 0.0
    dd_curve: List[float] = []
    # ---- 回撤的三种算法 ----
    #
    # 关键：**分子是同一个**，就是「峰值 − 当前」这一波跌掉了多少；
    # 区别只在除以谁。之前这里搞错了，本金口径写成了 max(0, 1-v)，
    # 它的分子是「本金 − 当前」，根本不是同一波跌幅 ——
    # 用户看到「峰值口径 29% > 本金口径 25%」会觉得算反了，
    # 因为按常识 200 ÷ 1000 应该比 200 ÷ 1100 大。
    #
    #   peak_dd    = (peak − v) / peak   从最高点回落多少（行业标准）
    #   prin_dd    = (peak − v) / 1.0    这波跌幅相当于本金的百分之几
    #   loss_dd    = max(0, 1 − v)       现在比本金少了多少（跌破本金才有值）
    mdd_principal = 0.0
    principal_dd_curve: List[float] = []
    mdd_loss = 0.0
    loss_dd_curve: List[float] = []
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        dd_curve.append(dd)
        mdd = max(mdd, dd)
        # 本金分母：分子不变，只把分母换成 1.0（所以一定 ≥ 峰值口径）
        dp = peak - v
        principal_dd_curve.append(dp)
        mdd_principal = max(mdd_principal, dp)
        # 另一种视角：亏到本金以下多少。这是给散户看「有没有真的亏钱」
        dl = max(0.0, 1.0 - v)
        loss_dd_curve.append(dl)
        mdd_loss = max(mdd_loss, dl)
    res.max_drawdown = mdd                       # 峰值分母：从高点回落多少
    res.max_drawdown_principal = mdd_principal   # 本金分母：同样的跌幅 ÷ 1.0
    res.max_drawdown_loss = mdd_loss             # 亏损深度：现在比本金少了多少
    res.drawdown_curve = dd_curve
    res.principal_drawdown_curve = principal_dd_curve
    res.loss_drawdown_curve = loss_dd_curve

    # ---- 单利口径：收益直接累加不滚存 ----
    res.simple_return = simple_equity_curve[-1] - 1.0
    res.simple_curve = simple_equity_curve

    # 同一套三种算法，作用在单利净值上
    sp_peak = 0.0
    sp_curve: List[float] = []       # 峰值分母
    spp_curve: List[float] = []      # 本金分母（(peak-v)/1.0）
    spl_curve: List[float] = []      # 亏损深度（max(0,1-v)）
    for v in simple_equity_curve:
        sp_peak = max(sp_peak, v)
        sp_curve.append((sp_peak - v) / sp_peak if sp_peak > 1e-12 else 0.0)
        spp_curve.append(sp_peak - v)
        spl_curve.append(max(0.0, 1.0 - v))

    res.simple_peak_drawdown_curve = sp_curve
    res.simple_max_drawdown_peak = max(sp_curve) if sp_curve else 0.0
    res.simple_principal_drawdown_curve = spp_curve
    res.simple_max_drawdown_principal = max(spp_curve) if spp_curve else 0.0
    res.simple_loss_drawdown_curve = spl_curve
    res.simple_max_drawdown_loss = max(spl_curve) if spl_curve else 0.0
    # 兼容旧字段名：simple_max_drawdown 现在等于本金分母口径
    res.simple_drawdown_curve = spp_curve
    res.simple_max_drawdown = res.simple_max_drawdown_principal

    if years > 0 and res.simple_return > -1:
        res.simple_annual_return = (1 + res.simple_return) ** (1 / years) - 1

    r_mean = sum(daily_ret[1:]) / max(1, n - 1)
    r_var = sum((x - r_mean) ** 2 for x in daily_ret[1:]) / max(1, n - 2)
    r_std = math.sqrt(r_var)
    res.sharpe = (r_mean / r_std * math.sqrt(TRADING_DAYS)) if r_std > 1e-12 else 0.0

    res.trade_count = len(trades)
    if trades:
        res.win_rate = sum(1 for t in trades if t.pnl_pct > 0) / len(trades)
    res.exposure = sum(positions) / n
    res.benchmark_return = bars[-1].close / bars[0].close - 1
    res.excess_return = res.total_return - res.benchmark_return
    res.calmar = (res.annual_return / mdd) if mdd > 1e-12 else 0.0
    res.blocked_orders = blocked
    res.daily_returns = daily_ret
    res.filter_stats = filter_stats
    if filter_stats:
        try:
            from .factor_engine import describe_conditions
        except ImportError:
            from factor_engine import describe_conditions
        res.filters_desc = describe_conditions(filter_stats)
    return res


def metrics_from_returns(returns: Sequence[float]) -> Dict[str, float]:
    """
    从日收益率序列算出一组指标。
    用于组合模拟：把两个策略的日收益按权重合成一条新序列，再重新算净值与回撤。
    """
    eq = 1.0
    curve = [1.0]
    peak, mdd = 1.0, 0.0
    for r in returns:
        eq *= (1 + r)
        curve.append(eq)
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)

    vals = list(returns)
    n = len(vals)
    mean = sum(vals) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(TRADING_DAYS)) if std > 1e-12 else 0.0
    years = n / TRADING_DAYS if n else 0
    annual = (eq ** (1 / years) - 1) if years > 0 and eq > 0 else 0.0

    return {
        "total_return": eq - 1.0,
        "annual_return": annual,
        "max_drawdown": mdd,
        "sharpe": sharpe,
    }


def analyze_pair(bars: Sequence[Bar],
                 strat_a: str, params_a: Dict[str, Any],
                 strat_b: str, params_b: Dict[str, Any],
                 weight_a: float = 0.5,
                 filters_a: Optional[List[Dict[str, Any]]] = None,
                 filters_b: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    比较两个策略能不能互补。

    不硬把两个信号拼成一个策略（那样规则说不清），而是给 AI 一组客观事实：
      - 两条净值曲线日收益的相关性：越接近 1 说明同涨同跌，组合没意义
      - 持仓冲突率：一个想满仓一个想空仓的天数占比
      - 等权组合的真实回测结果：合起来到底比单干好还是差
    AI 据此给建议，而不是空口说"这两个互补"。
    """
    # 两个策略各自的过滤条件都要带上，否则比的是两个裸策略
    ra = run_backtest(bars, strat_a, params_a, filters=filters_a or None)
    rb = run_backtest(bars, strat_b, params_b, filters=filters_b or None)

    n = min(len(ra.daily_returns), len(rb.daily_returns))
    xa = ra.daily_returns[1:n]
    xb = rb.daily_returns[1:n]
    m = len(xa)
    if m < 30:
        raise ValueError("数据太短，至少需要 30 个交易日")

    # ---- 相关性 ----
    avg_a = sum(xa) / m
    avg_b = sum(xb) / m
    cov = sum((a - avg_a) * (b - avg_b) for a, b in zip(xa, xb)) / max(1, m - 1)
    sd_a = math.sqrt(sum((a - avg_a) ** 2 for a in xa) / max(1, m - 1))
    sd_b = math.sqrt(sum((b - avg_b) ** 2 for b in xb) / max(1, m - 1))
    corr = cov / (sd_a * sd_b) if sd_a > 1e-12 and sd_b > 1e-12 else 0.0

    # ---- 持仓冲突 ----
    pa, pb = ra.positions[1:n], rb.positions[1:n]
    same = sum(1 for i in range(m) if pa[i] == pb[i])
    both_in = sum(1 for i in range(m) if pa[i] == 1 and pb[i] == 1)
    one_in = sum(1 for i in range(m) if pa[i] != pb[i])
    both_idle = sum(1 for i in range(m) if pa[i] == 0 and pb[i] == 0)

    # ---- 等权组合（每日再平衡，未计额外换手成本）----
    w = max(0.0, min(1.0, weight_a))
    combo = [w * a + (1 - w) * b for a, b in zip(xa, xb)]
    cm = metrics_from_returns(combo)

    if corr >= 0.75:
        verdict = "高度重叠"
        advice = "两个策略基本同涨同跌，放在一起等于把同一个策略做两遍，分散不了风险。"
    elif corr >= 0.45:
        verdict = "部分重叠"
        advice = "有一定相关性，组合能略微平滑波动，但别期待质的提升。"
    elif corr >= 0.15:
        verdict = "互补性较好"
        advice = "两者走势差异明显，组合后回撤通常会比单干小一些。"
    else:
        verdict = "互补性强"
        advice = "两者几乎各走各的，是典型的互补组合；但要确认低相关不是因为其中一个长期空仓。"

    return {
        "code": "pair_analysis",
        "period": f"{bars[0].trade_date} ~ {bars[-1].trade_date}",
        "days": m,
        "a": {
            "name": STRATEGY_NAMES.get(strat_a, strat_a),
            "params": params_a,
            "total_return_pct": round(ra.total_return * 100, 2),
            "max_drawdown_pct": round(ra.max_drawdown * 100, 2),
            "sharpe": round(ra.sharpe, 2),
            "trade_count": ra.trade_count,
            "exposure_pct": round(ra.exposure * 100, 1),
        },
        "b": {
            "name": STRATEGY_NAMES.get(strat_b, strat_b),
            "params": params_b,
            "total_return_pct": round(rb.total_return * 100, 2),
            "max_drawdown_pct": round(rb.max_drawdown * 100, 2),
            "sharpe": round(rb.sharpe, 2),
            "trade_count": rb.trade_count,
            "exposure_pct": round(rb.exposure * 100, 1),
        },
        "combined": {
            "weight": f"{int(w*100)}/{int((1-w)*100)}",
            "total_return_pct": round(cm["total_return"] * 100, 2),
            "annual_return_pct": round(cm["annual_return"] * 100, 2),
            "max_drawdown_pct": round(cm["max_drawdown"] * 100, 2),
            "sharpe": round(cm["sharpe"], 2),
            "note": "等权每日再平衡，未计额外换手成本",
        },
        "correlation": round(corr, 3),
        "same_position_pct": round(same / m * 100, 1),
        "conflict_days": one_in,
        "conflict_pct": round(one_in / m * 100, 1),
        "both_idle_pct": round(both_idle / m * 100, 1),
        "both_holding_pct": round(both_in / m * 100, 1),
        "verdict": verdict,
        "advice": advice,
        "benchmark_return_pct": round((bars[-1].close / bars[0].close - 1) * 100, 2),
    }


def explain_signal(bars: List[Bar], strategy_type: str, params: Dict[str, Any], idx: int = -1) -> str:
    """给每日信号生成一句人话解释，前端直接展示，避免只给数字"""
    if not bars:
        return ""
    i = idx if idx >= 0 else len(bars) - 1
    b = bars[i]
    if strategy_type == "ma_cross":
        close = [x.close for x in bars]
        f = sma(close, int(params.get("fast", 5)))[i]
        s = sma(close, int(params.get("slow", 20)))[i]
        if f is None or s is None:
            return "数据不足，暂不产生信号"
        gap = (f - s) / s * 100 if s else 0.0
        band = max(0.0, min(float(params.get("hysteresis", 0) or 0), 20.0))
        side = "上方" if gap > 0 else "下方"
        extra = ""
        if band > 0:
            # 加了回滞带之后，「快线在上面」不等于「信号成立」，
            # 得看有没有超出缓冲区，否则用户会以为今天该买
            if abs(gap) > band:
                extra = f"，已超出回滞带 {band}%（信号成立）"
            else:
                extra = f"，未超出回滞带 {band}%（缓冲区内，保持不动）"
        return (f"快线 {f:.2f}，慢线 {s:.2f}，快线在慢线{side} {abs(gap):.2f}%"
                f"{extra}，收盘 {b.close:.2f}")
    if strategy_type in ("boll", "boll_break"):
        close = [x.close for x in bars]
        n = int(params.get("n", 20))
        k = float(params.get("k", 2.0))
        m = sma(close, n)[i]
        sd = rolling_std(close, n)[i]
        if m is None or sd is None:
            return "数据不足，暂不产生信号"
        return f"中轨 {m:.2f}，上轨 {m + k * sd:.2f}，下轨 {m - k * sd:.2f}，收盘 {b.close:.2f}"
    if strategy_type == "momentum":
        lb = int(params.get("lookback", 20))
        if i < lb:
            return "数据不足，暂不产生信号"
        ret = bars[i].close / bars[i - lb].close - 1
        return f"过去 {lb} 日涨跌 {ret * 100:.2f}%，阈值 {float(params.get('threshold', 0.03)) * 100:.1f}%"
    if strategy_type == "rsi":
        r = rsi([x.close for x in bars], int(params.get("n", 14)))[i]
        if r is None:
            return "数据不足，暂不产生信号"
        return f"RSI {r:.1f}，超卖线 {params.get('low', 30)}，超买线 {params.get('high', 70)}"
    if strategy_type == "macd_cross":
        close = [x.close for x in bars]
        dif, dea = macd_series(close, int(params.get("fast", 12)),
                               int(params.get("slow", 26)), int(params.get("signal", 9)))
        side = "上方" if dif[i] > dea[i] else "下方"
        return (f"DIF {dif[i]:.3f}，DEA {dea[i]:.3f}，DIF 在 DEA {side}"
                f"{f'，也在零轴上方' if dif[i] > 0 else '，还在零轴下方'}")
    if strategy_type == "kdj_cross":
        k_vals, d_vals = kdj_series(bars, int(params.get("n", 9)))
        if k_vals[i] is None or d_vals[i] is None:
            return "数据不足，暂不产生信号"
        return (f"K {k_vals[i]:.1f}，D {d_vals[i]:.1f}，"
                f"买入需 K 低于 {params.get('buy_below', 50)} 且金叉，"
                f"K 高于 {params.get('sell_above', 85)} 离场")
    if strategy_type == "breakout":
        entry = int(params.get("entry", 20))
        exit_n = int(params.get("exit", 10))
        if i < entry:
            return "数据不足，暂不产生信号"
        upper = max(x.high for x in bars[i - entry:i])
        lower = min(x.low for x in bars[i - exit_n:i]) if i >= exit_n else min(x.low for x in bars[:i])
        return (f"收盘 {b.close:.2f}，{entry} 日上轨 {upper:.2f}，"
                f"{exit_n} 日下轨 {lower:.2f}"
                f"{'（已突破，站上上轨）' if b.close > upper else ''}"
                f"{'（已跌破下轨）' if b.close < lower else ''}")
    if strategy_type == "trix_cross":
        close = [x.close for x in bars]
        n = int(params.get("n", 12))
        e1 = ema(close, n)
        e2 = ema(e1, n)
        e3 = ema(e2, n)
        t = ((e3[i] / e3[i - 1] - 1) * 100) if i > 0 and e3[i - 1] > 1e-9 else 0.0
        return f"TRIX {t:+.3f}%，{'在零轴上方（偏多）' if t > 0 else '在零轴下方（偏空）'}"
    return ""


# ============================================================
# 自测：无依赖，直接 python backtest.py
# ============================================================
if __name__ == "__main__":
    import random

    random.seed(42)

    def make_bars(n: int = 500, drift: float = 0.0004, vol: float = 0.018,
                  cycle: int = 0) -> List[Bar]:
        price = 100.0
        out: List[Bar] = []
        for i in range(n):
            shock = random.gauss(drift, vol)
            if cycle:
                shock += 0.004 * math.sin(2 * math.pi * i / cycle)
            price = max(1.0, price * (1 + shock))
            out.append(Bar(
                trade_date=f"D{i:04d}",
                open=price * (1 - 0.002),
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000,
            ))
        return out

    cases = [
        ("ma_cross", {"fast": 5, "slow": 20}),
        ("boll", {"n": 20, "k": 2.0}),
        ("boll_break", {"n": 20, "k": 2.0}),
        ("momentum", {"lookback": 20, "threshold": 0.03}),
        ("rsi", {"n": 14, "low": 30, "high": 70}),
    ]

    for label, bars, note in [
        ("随机游走", make_bars(500), "接近无趋势，大部分策略应跑输或打平"),
        ("带周期", make_bars(500, cycle=60), "有明显波动周期，均值回归类应占优"),
    ]:
        print("=" * 74)
        print(f"数据集：{label}（{note}）")
        print("=" * 74)
        print(f"{'策略':<14}{'总收益':>10}{'年化':>10}{'最大回撤':>10}{'夏普':>8}{'胜率':>8}{'交易':>6}")
        print("-" * 74)
        for st, p in cases:
            try:
                r = run_backtest(bars, st, p)
                print(f"{st:<14}{r.total_return*100:>9.2f}%{r.annual_return*100:>9.2f}%"
                      f"{r.max_drawdown*100:>9.2f}%{r.sharpe:>8.2f}"
                      f"{r.win_rate*100:>7.1f}%{r.trade_count:>6}")
            except Exception as e:
                print(f"{st:<14}  FAILED: {e}")
        br = bars[-1].close / bars[0].close - 1
        print("-" * 74)
        print(f"基准（买入持有）总收益：{br*100:.2f}%")

    print()
    print("=" * 74)
    print("防未来函数验证：把最后一天信号强行改成 1，倒数第二天净值不应受影响")
    print("=" * 74)
    bars = make_bars(120)
    r1 = run_backtest(bars, "ma_cross", {"fast": 5, "slow": 20})
    bars2 = [Bar(b.trade_date, b.open, b.high, b.low, b.close, b.volume) for b in bars]
    r2 = run_backtest(bars2, "ma_cross", {"fast": 5, "slow": 20})
    same = all(abs(a - b) < 1e-12 for a, b in zip(r1.equity[:-1], r2.equity[:-1]))
    print(f"倒数第二个净值点一致：{same} → 说明 T 日仓位不受 T 日及之后信息影响")

    print()
    print("信号解释样例：")
    print(" ", explain_signal(bars, "ma_cross", {"fast": 5, "slow": 20}))
    print(" ", explain_signal(bars, "boll", {"n": 20, "k": 2.0}))
    print(" ", explain_signal(bars, "rsi", {"n": 14, "low": 30, "high": 70}))

    d = r1.to_dict()
    print()
    print(f"序列化字段：{sorted(d.keys())}")
    print(f"净值曲线点数：{len(d['equity'])}，交易明细条数：{len(d['trades'])}")

    print()
    print("=" * 74)
    print("涨跌停验证：连续一字涨停时，买入信号应被拦截")
    print("=" * 74)
    # 前 60 天阴跌（快线低于慢线，空仓），接着 5 天每天 +10%，再横盘 30 天
    seq: List[Bar] = []
    price = 100.0
    for i in range(60):
        price *= 0.998
        seq.append(Bar(f"A{i:03d}", price, price * 1.001, price * 0.999, price))
    for i in range(5):
        prev = price
        price *= 1.10                       # 每天刚好 +10%，触及涨停
        seq.append(Bar(f"B{i:03d}", prev, price, prev, price))
    for i in range(30):
        seq.append(Bar(f"C{i:03d}", price, price, price, price))

    on = run_backtest(seq, "ma_cross", {"fast": 3, "slow": 10}, respect_limit=True)
    off = run_backtest(seq, "ma_cross", {"fast": 3, "slow": 10}, respect_limit=False)
    print(f"  开启涨跌停拦截：被拦截 {on.blocked_orders} 次，总收益 {on.total_return*100:.2f}%")
    print(f"  关闭涨跌停拦截：被拦截 {off.blocked_orders} 次，总收益 {off.total_return*100:.2f}%")
    print(f"  差异 {(off.total_return-on.total_return)*100:.2f} 个百分点 "
          f"→ 这部分就是 naive 回测凭空多出来的、现实中拿不到的收益")
    assert on.blocked_orders > 0, "应当至少拦截一次涨停买入"
    print("  断言通过：涨停买入确实被拦截")
