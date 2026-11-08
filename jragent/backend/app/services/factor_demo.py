# -*- coding: utf-8 -*-
"""
因子可视化：用真实行情算出因子曲线，让用户看到"这个指标到底在看什么"。

不画示意图，全部用库里的真实数据算，
这样用户点开 RSI 看到的就是真的 RSI，和回测里用的是同一个公式。
"""
from typing import Any, Dict, List, Optional

from .backtest import Bar


def sma(vals: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        out.append(round(s / n, 4) if i >= n - 1 else None)
    return out


def ema(vals: List[float], n: int) -> List[Optional[float]]:
    k = 2 / (n + 1)
    out: List[Optional[float]] = []
    prev: Optional[float] = None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(round(prev, 4))
    return out


def rolling_std(vals: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(vals)):
        if i < n - 1:
            out.append(None); continue
        win = vals[i - n + 1:i + 1]
        m = sum(win) / n
        var = sum((x - m) ** 2 for x in win) / n
        out.append(round(var ** 0.5, 4))
    return out


def rsi_series(vals: List[float], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(vals)
    if len(vals) <= n:
        return out
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(vals)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = round(100.0 if al < 1e-12 else 100 - 100 / (1 + ag / al), 2)
    return out


def atr_series(bars: List[Bar], n: int = 14) -> List[Optional[float]]:
    trs: List[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    out: List[Optional[float]] = []
    for i in range(len(trs)):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            out.append(round(sum(trs[:n]) / n, 4))
        else:
            prev = out[-1] or trs[i]
            out.append(round((prev * (n - 1) + trs[i]) / n, 4))
    return out


def adx_series(bars: List[Bar], n: int = 14) -> List[Optional[float]]:
    """标准 Wilder ADX"""
    plus_dm, minus_dm, trs = [], [], []
    for i, b in enumerate(bars):
        if i == 0:
            plus_dm.append(0.0); minus_dm.append(0.0)
            trs.append(b.high - b.low); continue
        up = b.high - bars[i - 1].high
        dn = bars[i - 1].low - b.low
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        pc = bars[i - 1].close
        trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))

    def wilder(vals: List[float]) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for i in range(len(vals)):
            if i < n - 1:
                out.append(None)
            elif i == n - 1:
                out.append(sum(vals[:n]))
            else:
                out.append((out[-1] or 0) - (out[-1] or 0) / n + vals[i])
        return out

    str_ = wilder(trs)
    sp, sm = wilder(plus_dm), wilder(minus_dm)
    dxs: List[Optional[float]] = []
    for i in range(len(bars)):
        if not str_[i] or str_[i] < 1e-9 or sp[i] is None or sm[i] is None:
            dxs.append(None); continue
        pdi = 100 * sp[i] / str_[i]
        mdi = 100 * sm[i] / str_[i]
        dxs.append(round(100 * abs(pdi - mdi) / (pdi + mdi), 2) if (pdi + mdi) > 1e-9 else 0.0)

    out: List[Optional[float]] = []
    for i in range(len(dxs)):
        if i < 2 * n - 2 or dxs[i] is None:
            out.append(None); continue
        win = [x for x in dxs[i - n + 1:i + 1] if x is not None]
        out.append(round(sum(win) / len(win), 2) if win else None)
    return out


def macd_series(vals: List[float], fast=12, slow=26, sig=9):
    ef, es = ema(vals, fast), ema(vals, slow)
    dif = [round((a or 0) - (b or 0), 4) for a, b in zip(ef, es)]
    dea = ema(dif, sig)
    hist = [round((a - (b or 0)) * 2, 4) for a, b in zip(dif, dea)]
    return dif, dea, hist


def kdj_series(bars: List[Bar], n=9):
    k_list: List[Optional[float]] = []
    d_list: List[Optional[float]] = []
    j_list: List[Optional[float]] = []
    k = d = 50.0
    for i, b in enumerate(bars):
        if i < n - 1:
            k_list.append(None); d_list.append(None); j_list.append(None); continue
        win = bars[i - n + 1:i + 1]
        hi = max(x.high for x in win)
        lo = min(x.low for x in win)
        rsv = 50.0 if hi - lo < 1e-9 else (b.close - lo) / (hi - lo) * 100
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
        k_list.append(round(k, 2)); d_list.append(round(d, 2))
        j_list.append(round(3 * k - 2 * d, 2))
    return k_list, d_list, j_list


def build_demo(code: str, bars: List[Bar], name: str) -> Dict[str, Any]:
    """
    按因子类型返回 demo 数据：
      dates / price / series（若干条线）/ bands（水平参考线）/ note
    前端直接用 ECharts 画。
    """
    closes = [b.close for b in bars]
    dates = [b.trade_date for b in bars]
    volumes = [b.volume for b in bars]

    payload: Dict[str, Any] = {
        "code": code, "name": name, "dates": dates,
        "price": [round(c, 2) for c in closes],
        "series": [], "bands": [], "note": "", "y_axis": "price",
    }

    def add(name_: str, data, color: str, kind: str = "line"):
        payload["series"].append({"name": name_, "data": data, "color": color, "kind": kind})

    if code == "ma":
        add("股价", payload["price"], "#c9c9c0")
        add("MA5", sma(closes, 5), "#e8a33d")
        add("MA20", sma(closes, 20), "#534ab7")
        payload["note"] = "两条均线跟着股价走，天数越大越平滑、越滞后"

    elif code == "ema":
        add("股价", payload["price"], "#c9c9c0")
        add("EMA12", ema(closes, 12), "#e8a33d")
        add("EMA26", ema(closes, 26), "#534ab7")
        payload["note"] = "EMA 给近期价格更大权重，所以比 MA 更贴着股价"

    elif code == "ma_cross":
        f, s = sma(closes, 5), sma(closes, 20)
        add("MA5（快线）", f, "#e8a33d")
        add("MA20（慢线）", s, "#534ab7")
        add("股价", payload["price"], "#e3e3da")
        payload["note"] = "快线上穿慢线是金叉，下穿是死叉"

    elif code == "macd":
        dif, dea, hist = macd_series(closes)
        add("DIF 快线", dif, "#e8a33d")
        add("DEA 慢线", dea, "#534ab7")
        add("柱状差", hist, "#9a9a90", "bar")
        payload["y_axis"] = "value"
        payload["note"] = "DIF 上穿 DEA 是金叉，柱子在 0 轴上下代表多空强弱"

    elif code == "adx":
        add("ADX", adx_series(bars), "#534ab7")
        payload["bands"] = [{"value": 25, "label": "25 以上算有趋势", "color": "#c0362c"},
                            {"value": 20, "label": "20 以下算震荡", "color": "#1a7f4b"}]
        payload["y_axis"] = "value"
        payload["note"] = "ADX 只管有没有趋势，不管涨跌。低于 20 时趋势策略容易被反复打脸"

    elif code == "boll":
        mid = sma(closes, 20)
        sd = rolling_std(closes, 20)
        up = [round((m or 0) + 2 * (s or 0), 2) if m is not None and s is not None else None
              for m, s in zip(mid, sd)]
        dn = [round((m or 0) - 2 * (s or 0), 2) if m is not None and s is not None else None
              for m, s in zip(mid, sd)]
        add("股价", payload["price"], "#c9c9c0")
        add("上轨", up, "#c0362c")
        add("中轨 MA20", mid, "#534ab7")
        add("下轨", dn, "#1a7f4b")
        payload["note"] = "中轨是均线，上下轨是中轨加减两倍标准差"

    elif code == "boll_width":
        mid = sma(closes, 20)
        sd = rolling_std(closes, 20)
        w = [round(4 * (s or 0) / (m or 1) * 100, 2) if m and s is not None else None
             for m, s in zip(mid, sd)]
        add("带宽 %", w, "#534ab7")
        payload["y_axis"] = "value"
        payload["note"] = "宽度收窄往往意味着要变盘，方向不确定但波动会放大"

    elif code == "atr":
        add("ATR", atr_series(bars), "#534ab7")
        payload["y_axis"] = "value"
        payload["note"] = "ATR 数值越大，说明这只股票平时一天波动越剧烈"

    elif code == "std":
        add("20 日标准差", rolling_std(closes, 20), "#534ab7")
        payload["y_axis"] = "value"
        payload["note"] = "标准差就是波动率本身，布林带用的就是它"

    elif code == "rsi":
        add("RSI(14)", rsi_series(closes, 14), "#534ab7")
        payload["bands"] = [{"value": 70, "label": "70 超买", "color": "#c0362c"},
                            {"value": 30, "label": "30 超卖", "color": "#1a7f4b"}]
        payload["y_axis"] = "value"
        payload["note"] = "越靠上说明最近涨的力量越强。超过 70 未必会跌，强趋势里会长期钝化"

    elif code == "kdj":
        k, d, j = kdj_series(bars)
        add("K", k, "#e8a33d")
        add("D", d, "#534ab7")
        add("J", j, "#c0362c")
        payload["bands"] = [{"value": 80, "label": "80", "color": "#dddddd"},
                            {"value": 20, "label": "20", "color": "#dddddd"}]
        payload["y_axis"] = "value"
        payload["note"] = "K 上穿 D 买入、下穿卖出，比 RSI 灵敏，噪音也更多"

    elif code == "cci":
        tp = [(b.high + b.low + b.close) / 3 for b in bars]
        ma_tp = sma(tp, 14)
        cci = []
        for i in range(len(bars)):
            if i < 13 or ma_tp[i] is None:
                cci.append(None); continue
            win = tp[i - 13:i + 1]
            md = sum(abs(x - (ma_tp[i] or 0)) for x in win) / len(win)
            cci.append(round((tp[i] - (ma_tp[i] or 0)) / (0.015 * md), 2) if md > 1e-9 else 0.0)
        add("CCI", cci, "#534ab7")
        payload["bands"] = [{"value": 100, "label": "+100", "color": "#c0362c"},
                            {"value": -100, "label": "-100", "color": "#1a7f4b"}]
        payload["y_axis"] = "value"
        payload["note"] = "超过 +100 算强势，低于 -100 算弱势，用来抓极端行情"

    elif code == "bias":
        for n, c in ((6, "#e8a33d"), (24, "#534ab7")):
            m = sma(closes, n)
            add(f"BIAS{n}", [round((x - (mm or 0)) / (mm or 1) * 100, 2) if mm else None
                             for x, mm in zip(closes, m)], c)
        payload["y_axis"] = "value"
        payload["note"] = "价格离均线有多远。正得太多可能回落，负得太多可能反弹"

    elif code == "willr":
        wr = []
        for i in range(len(bars)):
            if i < 13:
                wr.append(None); continue
            win = bars[i - 13:i + 1]
            hi = max(x.high for x in win); lo = min(x.low for x in win)
            wr.append(round((hi - bars[i].close) / (hi - lo) * -100, 2) if hi - lo > 1e-9 else None)
        add("W%R", wr, "#534ab7")
        payload["bands"] = [{"value": -20, "label": "-20", "color": "#c0362c"},
                            {"value": -80, "label": "-80", "color": "#1a7f4b"}]
        payload["y_axis"] = "value"
        payload["note"] = "RSI 的镜像版，-80 以下超卖，-20 以上超买"

    elif code == "volume_ratio":
        ma5 = sma(volumes, 5)
        vr = [round(v / m, 2) if m else None for v, m in zip(volumes, ma5)]
        add("量比", vr, "#534ab7", "bar")
        payload["bands"] = [{"value": 1.5, "label": "1.5 以上算放量", "color": "#c0362c"}]
        payload["y_axis"] = "value"
        payload["note"] = "大于 1 说明今天比平时活跃，突破时量比不到 1.5 多半是假突破"

    elif code == "obv":
        obv = [0.0]
        for i in range(1, len(bars)):
            prev = obv[-1]
            if bars[i].close > bars[i - 1].close:
                obv.append(prev + volumes[i])
            elif bars[i].close < bars[i - 1].close:
                obv.append(prev - volumes[i])
            else:
                obv.append(prev)
        base = obv[0] if obv[0] else 1
        add("OBV", [round(v / 1e6, 2) for v in obv], "#534ab7")
        payload["y_axis"] = "value"
        payload["note"] = "涨的日子加成加量、跌的日子减量，累加起来看资金流入流出"

    elif code == "turnover":
        add("换手率 %", [round(v / 1e5, 3) for v in volumes], "#534ab7", "bar")
        payload["y_axis"] = "value"
        payload["note"] = "这里用成交量近似展示活跃度，真实换手率还要除以流通股本"

    elif code == "amt":
        add("成交额（亿元）", [round(b.close * b.volume / 1e8, 2) for b in bars], "#534ab7", "bar")
        payload["y_axis"] = "value"
        payload["note"] = "用钱衡量活跃度，跨股票比较比成交量更准"

    elif code in ("cross_up", "cross_down"):
        f, s = sma(closes, 5), sma(closes, 20)
        add("MA5（快线）", f, "#e8a33d")
        add("MA20（慢线）", s, "#534ab7")
        add("股价", payload["price"], "#e3e3da")
        # 标出所有交叉点
        marks = []
        for i in range(1, len(bars)):
            if None in (f[i], s[i], f[i - 1], s[i - 1]):
                continue
            if f[i - 1] <= s[i - 1] and f[i] > s[i]:
                marks.append({"date": dates[i], "type": "up", "price": round((f[i] + s[i]) / 2, 2)})
            elif f[i - 1] >= s[i - 1] and f[i] < s[i]:
                marks.append({"date": dates[i], "type": "down", "price": round((f[i] + s[i]) / 2, 2)})
        payload["target"] = "up" if code == "cross_up" else "down"
        payload["marks"] = [m for m in marks if m["type"] == payload["target"]]
        payload["note"] = ("红点是快线从下往上穿（上穿）的位置" if code == "cross_up"
                           else "绿点是快线从上往下穿（下穿）的位置")

    elif code == "breakout":
        add("股价", payload["price"], "#c9c9c0")
        hi20 = []
        for i in range(len(bars)):
            if i < 20:
                hi20.append(None); continue
            hi20.append(round(max(x.high for x in bars[i - 20:i]), 2))
        add("20 日最高", hi20, "#c0362c")
        payload["note"] = "股价超过前 20 天最高点就是突破，红线上方就是新高区"

    elif code == "drawdown":
        peak = 0.0
        dd = []
        for b in bars:
            peak = max(peak, b.close)
            dd.append(round((b.close - peak) / peak * 100, 2) if peak else 0.0)
        add("回撤 %", dd, "#c0362c", "area")
        payload["y_axis"] = "value"
        payload["note"] = "从历史最高点跌下来的幅度，0 表示正在最高点"

    else:
        # 通用兜底：任何注册在 factor_engine 里的因子，都能自动画出图。
        #
        # 为什么需要它：这个函数原来是 22 个 if/elif 硬编码，新加一个因子
        # 如果忘了在这里补分支，弹窗里就只有文字、一片空白 —— 用户会以为坏了。
        # 现在只要在 NUMERIC/BOOLEAN 里注册了，这里就自动能画。
        try:
            try:
                from .factor_engine import NUMERIC, BOOLEAN
            except ImportError:
                from factor_engine import NUMERIC, BOOLEAN

            if code in NUMERIC:
                spec = NUMERIC[code]
                p = dict(spec.get("defaults") or {})
                seq = spec["fn"](bars, p)
                add("股价", payload["price"], "#c9c9c0")
                add(spec["label"],
                    [None if v is None else round(v, 4) for v in seq],
                    "#534ab7")
                rng = spec.get("range")
                if rng and len(rng) == 2:
                    payload["bands"] = [
                        {"value": rng[0], "label": str(rng[0]), "color": "#d8d8d0"},
                        {"value": rng[1], "label": str(rng[1]), "color": "#d8d8d0"},
                    ]
                ptxt = "，".join(f"{k}={v}" for k, v in p.items()) or "无参数"
                payload["y_axis"] = "factor"
                payload["note"] = f"按「{ptxt}」算出来的逐日取值；灰线是股价，用来对照"
            elif code in BOOLEAN:
                spec = BOOLEAN[code]
                p = dict(spec.get("defaults") or {})
                flags = spec["fn"](bars, p)
                add("股价", payload["price"], "#c9c9c0")
                # 布尔因子画成 0/1 折线，上扬就代表当天条件成立
                add(spec["label"], [1 if f else 0 for f in flags], "#c0362c")
                payload["y_axis"] = "factor"
                payload["note"] = (f"红线到 1 的那天满足「{spec['label']}」，"
                                   f"参数 {p}；可以对照灰线的股价看是不是在追高")
            else:
                add("股价", payload["price"], "#534ab7")
                payload["note"] = "这个因子还没有图形演示，先看价格走势作为参照"
        except Exception as e:
            add("股价", payload["price"], "#534ab7")
            payload["note"] = f"这个因子暂时画不出图：{e}"

    return payload
