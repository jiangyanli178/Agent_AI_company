# -*- coding: utf-8 -*-
"""
第二批公开指标 —— 全部照 TA-Lib / 通达信的标准公式实现。

第一批在 `extended_factors.py`（ROC、MFI、PSY、Aroon、Z 分数…），
这一批补的是**更经典但需要多一些计算**的那些：

  DMI 家族的 PDI / MDI / ADXR、TRIX、CMO、StochRSI、终极震荡 ULTOSC、
  力量均衡 BOP、累积分配线 AD / ADOSC、简易波动 EMV、归一化 ATR、
  梅斯线、线性回归斜率、Aroon 下线与振荡、力量指标 EFI、相对活力 RVI、
  区间震荡 DPO —— 数值 20 个
  锤子线、看涨吞没、十字星、三连阳、死叉、布林上破 —— 布尔 6 个

为什么不用字符串表达式：
表达式引擎只接受数字常量，不支持 `n=20` 这种参数，而这些指标全都要调周期。
所以写成 Python 函数，注册进 `factor_engine.NUMERIC / BOOLEAN` 后，
**因子搜索、回测过滤、因子可视化三处同时生效**。

三条硬约束（和第一批一致）：
1. 只依赖 Bar 的 OHLCV，不引入新数据源；
2. 数据不足时返回 None，**绝不 backfill**，避免未来函数；
3. 每个指标都有明确取值范围，前端要画参考线。

公式出处：
- TA-Lib 函数文档（ta-lib.org/functions/）
- 《Technical Analysis from A to Z》Steven B. Achelis
- 通达信/同花顺 常用指标公式
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    from .backtest import Bar
    from .factor_demo import ema, rsi_series, sma
except ImportError:
    from backtest import Bar
    from factor_demo import ema, rsi_series, sma

V = Optional[float]


# ============================================================
# 基础工具
# ============================================================
def _c(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


def _h(bars: Sequence[Bar]) -> List[float]:
    return [b.high for b in bars]


def _l(bars: Sequence[Bar]) -> List[float]:
    return [b.low for b in bars]


def _o(bars: Sequence[Bar]) -> List[float]:
    return [b.open for b in bars]


def _v(bars: Sequence[Bar]) -> List[float]:
    return [b.volume for b in bars]


def _wilder(vals: Sequence[float], n: int) -> List[V]:
    """
    Wilder 平滑（TA-Lib 里的 RMA）：alpha = 1/n。
    种子用前 n 个值之和，之后递推 s = s - s/n + 新值。
    返回的是「和」不是「均值」—— 要和均值比时两边都除 n，比值不变。
    """
    out: List[V] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    s = float(sum(vals[:n]))
    out[n - 1] = s
    for i in range(n, len(vals)):
        s = s - s / n + vals[i]
        out[i] = s
    return out


def _wma(vals: Sequence[float], n: int) -> List[V]:
    """加权移动平均：最近一根权重最大（权重 1..n）"""
    out: List[V] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    denom = n * (n + 1) / 2.0
    for i in range(n - 1, len(vals)):
        s = 0.0
        for k in range(n):
            s += vals[i - n + 1 + k] * (k + 1)
        out[i] = round(s / denom, 4)
    return out


def _ema_safe(vals: Sequence[float], n: int) -> List[V]:
    """把 factor_demo.ema 的返回值前面 n-1 个抹成 None，避免用不着调的数据"""
    e = ema(list(vals), n)
    return [None if i < n - 1 else e[i] for i in range(len(e))]


def _tr_series(bars: Sequence[Bar]) -> List[float]:
    """真实波幅 TR = max(H-L, |H-前收|, |L-前收|)"""
    out = [0.0] * len(bars)
    for i, b in enumerate(bars):
        if i == 0:
            out[i] = b.high - b.low
        else:
            pc = bars[i - 1].close
            out[i] = max(b.high - b.low, abs(b.high - pc), abs(b.low - pc))
    return out


def _dm_series(bars: Sequence[Bar]):
    """+DM / -DM（Wilder 原始定义，只取更大的那个方向）"""
    pdm = [0.0] * len(bars)
    ndm = [0.0] * len(bars)
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
    return pdm, ndm


def _dx_series(bars: Sequence[Bar], n: int) -> List[V]:
    """DI 与 DX，PDI / MDI / ADX / ADXR 共用这一套中间量"""
    tr = _tr_series(bars)
    pdm, ndm = _dm_series(bars)
    tr_w = _wilder(tr, n)
    pdm_w = _wilder(pdm, n)
    ndm_w = _wilder(ndm, n)
    pdi: List[V] = [None] * len(bars)
    mdi: List[V] = [None] * len(bars)
    dx: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if tr_w[i] is None or tr_w[i] < 1e-12:
            continue
        p = 100.0 * pdm_w[i] / tr_w[i]
        m = 100.0 * ndm_w[i] / tr_w[i]
        pdi[i] = round(p, 2)
        mdi[i] = round(m, 2)
        s = p + m
        dx[i] = round(100.0 * abs(p - m) / s, 2) if s > 1e-12 else 0.0
    return pdi, mdi, dx


# ============================================================
# 数值因子：趋势 / DMI 家族
# ============================================================
def pdi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """+DI 正向动向：上涨方向的力量占比，越大越偏多"""
    return _dx_series(bars, max(1, int(p.get("n", 14))))[0]


def mdi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """-DI 负向动向：下跌方向的力量占比，越大越偏空"""
    return _dx_series(bars, max(1, int(p.get("n", 14))))[1]


def adxr(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    ADXR = (今天的 ADX + n 天前的 ADX) / 2。
    比 ADX 更钝，好处是假突破少：ADX 一柱擎天时 ADXR 还没起来，
    说明趋势是刚冒出来的，不是早就走完了。
    """
    n = max(1, int(p.get("n", 14)))
    _, _, dx = _dx_series(bars, n)
    # ADX 是 DX 的 Wilder 平滑
    dx_clean = [0.0 if x is None else x for x in dx]
    adx = _wilder(dx_clean, n)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if adx[i] is None:
            continue
        avg = adx[i] / n
        if i - n >= 0 and adx[i - n] is not None:
            out[i] = round((avg + adx[i - n] / n) / 2, 2)
        else:
            out[i] = round(avg, 2)
    return out


def trix(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    TRIX：收盘价连做三次 EMA 之后的日变化率（%）。
    三重平滑把短期噪音全滤掉了，只剩大方向，代价是信号很慢。

    必须等三层 EMA 都脱离种子区（约 3n 根）才输出：
    前几根里三层 EMA 都还贴在初始值附近，做除法会得到 +169% 这种假数字。
    """
    n = max(1, int(p.get("n", 12)))
    warm = 3 * n
    c = _c(bars)
    e1 = _ema_safe(c, n)
    e2 = _ema_safe([x if x is not None else 0.0 for x in e1], 2 * n)
    e3 = _ema_safe([x if x is not None else 0.0 for x in e2], 3 * n)
    out: List[V] = [None] * len(bars)
    for i in range(warm, len(bars)):
        prev = e3[i - 1]
        if e3[i] is None or prev is None or prev < 1e-9:
            continue
        out[i] = round((e3[i] / prev - 1) * 100, 4)
    return out


def linreg_slope(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    线性回归斜率，除以均价换算成「每根 K 线涨跌百分之几」，
    这样不同价位的股票之间能横向比。
    """
    n = max(2, int(p.get("n", 14)))
    c = _c(bars)
    out: List[V] = [None] * len(bars)
    xs = list(range(n))
    xm = (n - 1) / 2.0
    denom = sum((x - xm) ** 2 for x in xs)
    for i in range(n - 1, len(bars)):
        win = c[i - n + 1:i + 1]
        ym = sum(win) / n
        if ym < 1e-12:
            continue
        num = sum((xs[k] - xm) * (win[k] - ym) for k in range(n))
        out[i] = round(num / denom / ym * 100, 4)
    return out


def aroon_down(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """Aroon 下线：n 天内最低点离今天多少天，越接近 100 越弱"""
    n = max(1, int(p.get("n", 25)))
    low = _l(bars)
    out: List[V] = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        win = low[i - n + 1:i + 1]
        idx = min(range(n), key=lambda k: win[k])   # 最低点位置（并列取最早）
        out[i] = round((n - 1 - idx) / (n - 1) * 100, 2) if n > 1 else None
    return out


def aroon_osc(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """Aroon 振荡 = 上线 − 下线，正值偏多、负值偏空"""
    n = max(2, int(p.get("n", 25)))
    high, low = _h(bars), _l(bars)
    out: List[V] = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        wh = high[i - n + 1:i + 1]
        wl = low[i - n + 1:i + 1]
        ih = max(range(n), key=lambda k: wh[k])
        il = min(range(n), key=lambda k: wl[k])
        up = ih / (n - 1) * 100
        dn = il / (n - 1) * 100
        out[i] = round(up - dn, 2)
    return out


def dpo(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    DPO 区间震荡：收盘价减去「n/2 天前那根对应的均线」，去掉趋势只剩波动。
    除以均线换算成百分比，这样不同价位的股票能横向比
    （绝对值形式在茅台上是几百、在几块钱的票上是零点几，没法放一起看）。
    """
    n = max(2, int(p.get("n", 20)))
    c = _c(bars)
    m = sma(c, n)
    shift = n // 2 + 1
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        j = i - shift
        if j < 0 or m[j] is None or m[j] < 1e-9:
            continue
        out[i] = round((c[i] / m[j] - 1) * 100, 4)
    return out


# ============================================================
# 数值因子：震荡 / 动量
# ============================================================
def cmo(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """钱德动量摆动 CMO：只用涨跌幅度，不用均价，避免 RSI 的平滑滞后"""
    n = max(1, int(p.get("n", 14)))
    c = _c(bars)
    out: List[V] = [None] * len(bars)
    for i in range(n, len(bars)):
        up = dn = 0.0
        for k in range(i - n + 1, i + 1):
            d = c[k] - c[k - 1]
            if d > 0:
                up += d
            else:
                dn += -d
        s = up + dn
        out[i] = round(100 * (up - dn) / s, 2) if s > 1e-12 else 0.0
    return out


def stochrsi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    随机 RSI：把 RSI 再做一次「n 日内相对位置」归一化。
    比 RSI 更灵敏，冲到 0/100 的次数多得多，适合抓极值不适合判断趋势。
    """
    n = max(1, int(p.get("n", 14)))
    c = _c(bars)
    r = rsi_series(c, n)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if i < 2 * n:
            continue
        win = [x for x in r[i - n + 1:i + 1] if x is not None]
        if len(win) < n:
            continue
        hi, lo = max(win), min(win)
        if hi - lo < 1e-9:
            out[i] = 50.0
        else:
            out[i] = round((r[i] - lo) / (hi - lo) * 100, 2)
    return out


def ultosc(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    终极震荡 ULTOSC：短(7)/中(14)/长(28) 三个周期的买入压力加权，
    权重 4:2:1。设计目的是跨周期确认，减少单一周期被操纵的可能。
    """
    c = _c(bars)
    n1 = int(p.get("n1", 7))
    n2 = int(p.get("n2", 14))
    n3 = int(p.get("n3", 28))
    need = max(n1, n2, n3)
    bp: List[float] = [0.0] * len(bars)
    tr: List[float] = [0.0] * len(bars)
    for i in range(1, len(bars)):
        pc = bars[i - 1].close
        bp[i] = bars[i].close - min(bars[i].low, pc)
        tr[i] = max(bars[i].high, pc) - min(bars[i].low, pc)
    out: List[V] = [None] * len(bars)
    for i in range(need, len(bars)):
        def ratio(k: int) -> float:
            b = sum(bp[i - k + 1:i + 1])
            t = sum(tr[i - k + 1:i + 1])
            return b / t if t > 1e-12 else 0.0
        a1, a2, a3 = ratio(n1), ratio(n2), ratio(n3)
        out[i] = round(100 * (4 * a1 + 2 * a2 + a3) / 7, 2)
    return out


def bop(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    力量均衡 BOP = (收-开)/(高-低)，纯粹衡量当天多空谁占上风。
    做 n 日平均后看持续性，单日值噪音很大。
    """
    n = max(1, int(p.get("n", 5)))
    raw: List[float] = []
    for b in bars:
        rng = b.high - b.low
        raw.append((b.close - b.open) / rng if rng > 1e-12 else 0.0)
    return _wma(raw, n) if n > 1 else [round(x, 4) for x in raw]


def rvi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    相对活力 RVI：收在当天区间的高位算「有活力」，低位算「没活力」。
    """
    n = max(1, int(p.get("n", 10)))
    num: List[float] = []
    den: List[float] = []
    for b in bars:
        rng = b.high - b.low
        if rng < 1e-12:
            num.append(0.0)
            den.append(0.0)
        else:
            num.append(b.close - b.open)
            den.append(rng)
    en = _ema_safe(num, n)
    ed = _ema_safe(den, n)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if en[i] is None or ed[i] is None or ed[i] < 1e-9:
            continue
        out[i] = round(en[i] / ed[i], 4)
    return out


# ============================================================
# 数值因子：量能
# ============================================================
def ad_line(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    累积/分配线 AD：收在当天高位就认为在吸筹（加分），收在低位认为在派发（减分），
    再乘成交量累加。看的是**斜率**不是绝对值。
    """
    total = 0.0
    out: List[V] = []
    for b in bars:
        rng = b.high - b.low
        mfm = 0.0 if rng < 1e-12 else ((b.close - b.low) - (b.high - b.close)) / rng
        total += mfm * b.volume
        out.append(round(total / 1e6, 4))     # 缩到百万手，数字好看一点
    return out


def adosc(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """佳庆振荡 ADOSC = EMA(AD,3) − EMA(AD,10)，AD 的短期加速度"""
    fast = max(1, int(p.get("fast", 3)))
    slow = max(2, int(p.get("slow", 10)))
    ad = [x for x in ad_line(bars, {})]
    ef = _ema_safe(ad, fast)
    es = _ema_safe(ad, slow)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if ef[i] is None or es[i] is None:
            continue
        out[i] = round(ef[i] - es[i], 4)
    return out


def emv(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    简易波动 EMV：用「中点移动 ÷ 单位成交量造成的振幅」衡量走得轻不轻。
    放量却涨不动 → EMV 接近 0，说明有大量换手在压着。

    divisor 默认 0 = 自动取 n 日均量。
    固定用 10000 是美股口径（股数），A 股 akshare 给的是「手」，
    茅台一天几万手、小票几十万手，固定值会让不同标的差好几个数量级。
    """
    n = max(1, int(p.get("n", 14)))
    div = float(p.get("d", 0) or 0)
    if div <= 0:
        vols = _v(bars)
        div = (sum(vols[-n:]) / n) if len(vols) >= n else 0.0
        div = div or 10000.0
    mid = [(b.high + b.low) / 2 for b in bars]
    raw: List[float] = [0.0] * len(bars)
    for i in range(1, len(bars)):
        rng = bars[i].high - bars[i].low
        if rng < 1e-12 or bars[i].volume <= 0:
            raw[i] = 0.0
            continue
        box = (bars[i].volume / div) / rng
        if box < 1e-12:
            raw[i] = 0.0
            continue
        # 原式得到的是「价格点」，茅台这种千元股会到几百、几块钱的票只有零点几。
        # 折算成占价格的百分比，不同标的才能放进同一个筛选器里比。
        raw[i] = (mid[i] - mid[i - 1]) / box / max(bars[i].close, 1e-9) * 100
    return _wma(raw, n)


def efi(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    力量指标 EFI：成交量 × 价格变化，再除以 n 天平均成交额做归一化。
    正的大 = 上涨有量支撑；负的大 = 下跌在放量。
    """
    n = max(1, int(p.get("n", 13)))
    out: List[V] = [None] * len(bars)
    for i in range(n, len(bars)):
        s = 0.0
        amount = 0.0
        for k in range(i - n + 1, i + 1):
            chg = bars[k].close - bars[k - 1].close
            s += chg * bars[k].volume
            amount += bars[k].close * bars[k].volume
        out[i] = round(s / amount, 4) if amount > 1e-9 else 0.0
    return out


def volume_wma(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """成交量的加权均线占比：今天的量能相对近期水平偏热还是偏冷"""
    n = max(2, int(p.get("n", 10)))
    v = _v(bars)
    w = _wma(v, n)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if w[i] is None or w[i] < 1e-9:
            continue
        out[i] = round(v[i] / w[i], 3)
    return out


# ============================================================
# 数值因子：波动
# ============================================================
def natr(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """归一化 ATR：ATR ÷ 收盘价 × 100，不同价位的票可以直接比"""
    n = max(1, int(p.get("n", 14)))
    tr = _tr_series(bars)
    w = _wilder(tr, n)
    out: List[V] = [None] * len(bars)
    for i in range(len(bars)):
        if w[i] is None or bars[i].close < 1e-9:
            continue
        out[i] = round(w[i] / n / bars[i].close * 100, 4)
    return out


def trange_pct(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """单根 K 线真实波幅占价格百分比 —— 最直白的「今天振幅多大」"""
    tr = _tr_series(bars)
    return [round(tr[i] / bars[i].close * 100, 4) if bars[i].close > 1e-9 else None
            for i in range(len(bars))]


def mass_index(bars: List[Bar], p: Dict[str, Any]) -> List[V]:
    """
    梅斯线 Mass Index：高低价差先 EMA(9)，再对结果 EMA(9)，两者相除后累加 25 天。
    >27 之后回落到 26.5 以下，历史上常被当成「反转要来了」的信号
    （这叫「凸起」，Bulkowski 统计过）。别单独用，只当预警。
    """
    n = max(2, int(p.get("n", 9)))
    m = max(2, int(p.get("m", 25)))
    rng = [b.high - b.low for b in bars]
    e1 = _ema_safe(rng, n)
    e2 = _ema_safe([x if x is not None else 0.0 for x in e1], n)
    ratio: List[float] = []
    for i in range(len(bars)):
        if e1[i] is None or e2[i] is None or e2[i] < 1e-9:
            ratio.append(0.0)
        else:
            ratio.append(e1[i] / e2[i])
    out: List[V] = [None] * len(bars)
    for i in range(m - 1, len(bars)):
        out[i] = round(sum(ratio[i - m + 1:i + 1]), 3)
    return out


# ============================================================
# 布尔因子（0/1）
# ============================================================
def b_hammer(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """锤子线：下影线 ≥ 实体 2 倍、上影线很短，说明低位有人接"""
    ratio = float(p.get("ratio", 2.0))
    out = [0] * len(bars)
    for i, b in enumerate(bars):
        body = abs(b.close - b.open)
        lower = min(b.close, b.open) - b.low
        upper = b.high - max(b.close, b.open)
        rng = b.high - b.low
        if rng < 1e-9:
            continue
        if lower >= ratio * max(body, rng * 0.05) and upper <= body * 0.6:
            out[i] = 1
    return out


def b_engulf_bull(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """看涨吞没：昨天阴线，今天阳线且实体完全包住昨天实体"""
    out = [0] * len(bars)
    for i in range(1, len(bars)):
        y, t = bars[i - 1], bars[i]
        if y.close >= y.open or t.close <= t.open:
            continue
        if t.open <= y.close and t.close >= y.open:
            out[i] = 1
    return out


def b_doji(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """十字星：实体不到全天振幅的 10%，多空僵持"""
    body_pct = float(p.get("body_pct", 0.10))
    out = [0] * len(bars)
    for i, b in enumerate(bars):
        rng = b.high - b.low
        if rng > 1e-9 and abs(b.close - b.open) <= rng * body_pct:
            out[i] = 1
    return out


def b_three_white(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """三连阳（红三兵）：连续三根阳线且收盘逐日抬高"""
    out = [0] * len(bars)
    for i in range(2, len(bars)):
        a, b, c = bars[i - 2], bars[i - 1], bars[i]
        if a.close > a.open and b.close > b.open and c.close > c.open \
                and a.close < b.close < c.close:
            out[i] = 1
    return out


def b_death_cross(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """当日死叉：快线从上方跌破慢线"""
    fast_n = max(1, int(p.get("fast", 5)))
    slow_n = max(2, int(p.get("slow", 20)))
    c = _c(bars)
    f, s = sma(c, fast_n), sma(c, slow_n)
    out = [0] * len(bars)
    for i in range(1, len(bars)):
        if None in (f[i], s[i], f[i - 1], s[i - 1]):
            continue
        if f[i - 1] >= s[i - 1] and f[i] < s[i]:
            out[i] = 1
    return out


def b_boll_upper_break(bars: List[Bar], p: Dict[str, Any]) -> List[int]:
    """收盘站上布林上轨"""
    from .factor_demo import rolling_std
    n = max(2, int(p.get("n", 20)))
    k = float(p.get("k", 2.0))
    c = _c(bars)
    mid, sd = sma(c, n), rolling_std(c, n)
    out = [0] * len(bars)
    for i in range(len(bars)):
        if mid[i] is None or sd[i] is None:
            continue
        out[i] = 1 if c[i] > mid[i] + k * sd[i] else 0
    return out


# ============================================================
# 注册表
# ============================================================
NUMERIC_FACTORS: Dict[str, Dict[str, Any]] = {
    "pdi":          {"fn": pdi, "label": "+DI 正向动向", "defaults": {"n": 14}, "range": [0, 100]},
    "mdi":          {"fn": mdi, "label": "-DI 负向动向", "defaults": {"n": 14}, "range": [0, 100]},
    "adxr":         {"fn": adxr, "label": "ADXR 趋势评级", "defaults": {"n": 14}, "range": [0, 100]},
    "trix":         {"fn": trix, "label": "TRIX 三重指数", "defaults": {"n": 12}},
    "linreg_slope": {"fn": linreg_slope, "label": "线性回归斜率", "defaults": {"n": 14}},
    "aroon_down":   {"fn": aroon_down, "label": "阿隆下线", "defaults": {"n": 25}, "range": [0, 100]},
    "aroon_osc":    {"fn": aroon_osc, "label": "阿隆振荡", "defaults": {"n": 25}, "range": [-100, 100]},
    "dpo":          {"fn": dpo, "label": "区间震荡 DPO", "defaults": {"n": 20}},
    "cmo":          {"fn": cmo, "label": "钱德动量 CMO", "defaults": {"n": 14}, "range": [-100, 100]},
    "stochrsi":     {"fn": stochrsi, "label": "随机 RSI", "defaults": {"n": 14}, "range": [0, 100]},
    "ultosc":       {"fn": ultosc, "label": "终极震荡 ULTOSC", "defaults": {"n1": 7, "n2": 14, "n3": 28}, "range": [0, 100]},
    "bop":          {"fn": bop, "label": "力量均衡 BOP", "defaults": {"n": 5}, "range": [-1, 1]},
    "rvi":          {"fn": rvi, "label": "相对活力 RVI", "defaults": {"n": 10}},
    "ad_line":      {"fn": ad_line, "label": "累积/分配线 AD", "defaults": {}},
    "adosc":        {"fn": adosc, "label": "佳庆振荡 ADOSC", "defaults": {"fast": 3, "slow": 10}},
    "emv":          {"fn": emv, "label": "简易波动 EMV", "defaults": {"n": 14, "d": 10000}},
    "efi":          {"fn": efi, "label": "力量指标 EFI", "defaults": {"n": 13}},
    "volume_wma":   {"fn": volume_wma, "label": "量能热度", "defaults": {"n": 10}},
    "natr":         {"fn": natr, "label": "归一化 ATR", "defaults": {"n": 14}},
    "trange_pct":   {"fn": trange_pct, "label": "当日真实振幅", "defaults": {}},
    "mass_index":   {"fn": mass_index, "label": "梅斯线 Mass Index", "defaults": {"n": 9, "m": 25}},
}

BOOLEAN_FACTORS: Dict[str, Dict[str, Any]] = {
    "cdl_hammer":      {"fn": b_hammer, "label": "锤子线", "defaults": {"ratio": 2.0}},
    "cdl_engulf_bull": {"fn": b_engulf_bull, "label": "看涨吞没", "defaults": {}},
    "cdl_doji":        {"fn": b_doji, "label": "十字星", "defaults": {"body_pct": 0.10}},
    "cdl_three_white": {"fn": b_three_white, "label": "三连阳（红三兵）", "defaults": {}},
    "ma_death_cross":  {"fn": b_death_cross, "label": "当日死叉", "defaults": {"fast": 5, "slow": 20}},
    "boll_upper_break": {"fn": b_boll_upper_break, "label": "站上布林上轨", "defaults": {"n": 20, "k": 2.0}},
}


def register_into(engine_module) -> int:
    """挂进 factor_engine（幂等，可重复调用）"""
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
