"""
体检报告生成器 —— AI 聊完之后，现场产出一份能打开、能下载、能发人的网页。

为什么不是「固定页面 + 改参数」：
用户要的是「我描述一句，你给我一个网页」。固定页面只能改数字，
改不了标题、模块顺序、说明文字和结论 —— 那些恰好是"这是你的策略"的部分。

所以这里让 AI 决定「页面长什么样」（标题、要哪几块、每块写什么），
系统负责把**真实回测数据**填进去，最终生成一份自包含 HTML：
- 图表是后端直接算出来的 SVG，不依赖任何 JS 库，下载到本地断网也能看
- 不执行 AI 写的任何脚本 —— AI 只填文字和模块选择，结构和样式由模板控制
- 生成后落在 reports/ 目录，给一个公开链接（类似分享页），也能下载

安全边界：AI 提供的所有文本都经过 html.escape，
不存在把 <script> 塞进报告的可能。
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

try:
    from .backtest import Bar, run_backtest
except ImportError:
    from backtest import Bar, run_backtest

# 交互图表是可选能力：拿不到就降级回静态 SVG，绝不能让整份报告失败
try:
    from . import report_charts
except ImportError:
    try:
        import report_charts          # type: ignore
    except Exception:
        report_charts = None          # type: ignore


# ---------- 颜色：和前端 CSS 变量保持一致 ----------
C_UP = "#c0362c"        # 涨（A股习惯：红涨）
C_DOWN = "#1a7f4b"      # 跌
C_MAIN = "#534ab7"      # 策略净值
C_FAST = "#e8a33d"      # 快线
C_SLOW = "#7c4dbc"      # 慢线
C_BENCH = "#a8a89e"     # 买入持有
C_TEXT = "#2a2a26"
C_TEXT2 = "#5f5f58"
C_TEXT3 = "#8f8f86"
C_BORDER = "#e6e6df"
C_BG = "#fbfbf9"


def esc(s: Any) -> str:
    return html.escape(str(s or ""))


def _fmt_pct(v: Optional[float], digits: int = 2, sign: bool = True) -> str:
    if v is None:
        return "—"
    x = v * 100
    return (f"{x:+.{digits}f}%" if sign else f"{x:.{digits}f}%")


def _nice_range(values: Sequence[float]) -> tuple:
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    return lo - pad, hi + pad


def _downsample(seq: Sequence[Any], n: int) -> List[Any]:
    if not seq:
        return []
    if len(seq) <= n:
        return list(seq)
    step = (len(seq) - 1) / (n - 1)
    out = []
    last = -1
    for i in range(n):
        idx = int(round(i * step))
        if idx != last:
            out.append(seq[idx])
            last = idx
    return out


# ============================================================
# 图一：K 线 + 均线 + 买卖点
# ============================================================
def candle_svg(bars: List[Bar], ma_fast: List[Optional[float]],
               ma_slow: List[Optional[float]],
               markers: List[Dict[str, Any]],
               width: int = 920, height: int = 380, bars_shown: int = 170) -> str:
    if not bars:
        return ""
    n = len(bars)
    start = max(0, n - bars_shown)
    idxs = list(range(start, n))

    ml, mr, mt, mb = 54, 14, 16, 42
    pw, ph = width - ml - mr, height - mt - mb

    highs = [bars[i].high for i in idxs]
    lows = [bars[i].low for i in idxs]
    for arr in (ma_fast, ma_slow):
        for i in idxs:
            if i < len(arr) and arr[i] is not None:
                highs.append(arr[i])
                lows.append(arr[i])
    y_lo, y_hi = _nice_range(highs + lows)

    def x_of(k: int) -> float:
        return ml + (k + 0.5) * pw / len(idxs)

    def y_of(v: float) -> float:
        return mt + (y_hi - v) / (y_hi - y_lo) * ph

    cw = max(1.6, min(6.0, pw / len(idxs) * 0.62))

    wick, body_up, body_dn = [], [], []
    for k, i in enumerate(idxs):
        b = bars[i]
        x = x_of(k)
        up = b.close >= b.open
        wick.append(f"M{x:.1f},{y_of(b.high):.1f}L{x:.1f},{y_of(b.low):.1f}")
        y1, y2 = y_of(max(b.open, b.close)), y_of(min(b.open, b.close))
        h = max(0.8, y2 - y1)
        rect = f"M{x - cw / 2:.1f},{y1:.1f}h{cw:.1f}v{h:.1f}h{-cw:.1f}Z"
        (body_up if up else body_dn).append(rect)

    def ma_path(arr: List[Optional[float]]) -> str:
        seg, pen = [], False
        for k, i in enumerate(idxs):
            v = arr[i] if i < len(arr) else None
            if v is None:
                pen = False
                continue
            seg.append(f"{'L' if pen else 'M'}{x_of(k):.1f},{y_of(v):.1f}")
            pen = True
        return "".join(seg)

    # y 轴刻度
    ticks = []
    for t in range(5):
        v = y_lo + (y_hi - y_lo) * t / 4
        y = y_of(v)
        ticks.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" stroke="{C_BORDER}" stroke-width="0.7"/>'
            f'<text x="{ml - 7}" y="{y + 3.5:.1f}" font-size="10" fill="{C_TEXT3}" text-anchor="end">{v:.0f}</text>'
        )

    # 买卖点
    mk = []
    date_to_k = {bars[i].trade_date: k for k, i in enumerate(idxs)}
    for m in markers or []:
        k = date_to_k.get(m.get("date"))
        if k is None:
            continue
        x, y = x_of(k), y_of(float(m.get("price") or 0))
        if m.get("type") == "buy":
            mk.append(f'<path d="M{x:.1f},{y - 9:.1f}l5.5,9.5h-11Z" fill="{C_UP}" stroke="#fff" stroke-width="1"/>')
        else:
            mk.append(f'<path d="M{x:.1f},{y + 9:.1f}l5.5,-9.5h-11Z" fill="{C_DOWN}" stroke="#fff" stroke-width="1"/>')

    # x 轴日期
    xlab = []
    for t in range(5):
        k = int((len(idxs) - 1) * t / 4)
        i = idxs[k]
        xlab.append(
            f'<text x="{x_of(k):.0f}" y="{height - 16}" font-size="10" fill="{C_TEXT3}" '
            f'text-anchor="middle">{esc(bars[i].trade_date[2:])}</text>'
        )

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <rect width="{width}" height="{height}" fill="#fff"/>
  {''.join(ticks)}
  <path d="{' '.join(wick)}" stroke="{C_TEXT3}" stroke-width="0.9" fill="none"/>
  <path d="{' '.join(body_up)}" fill="{C_UP}"/>
  <path d="{' '.join(body_dn)}" fill="{C_DOWN}"/>
  <path d="{ma_path(ma_fast)}" stroke="{C_FAST}" stroke-width="2.2" fill="none"/>
  <path d="{ma_path(ma_slow)}" stroke="{C_SLOW}" stroke-width="2.6" fill="none"/>
  {''.join(mk)}
  {''.join(xlab)}
  <g font-size="11">
    <rect x="{ml + 8}" y="{mt + 6}" width="150" height="44" rx="6" fill="#fff" stroke="{C_BORDER}"/>
    <line x1="{ml + 18}" y1="{mt + 21}" x2="{ml + 38}" y2="{mt + 21}" stroke="{C_FAST}" stroke-width="2.2"/>
    <text x="{ml + 44}" y="{mt + 25}" fill="{C_TEXT2}">快线</text>
    <line x1="{ml + 18}" y1="{mt + 38}" x2="{ml + 38}" y2="{mt + 38}" stroke="{C_SLOW}" stroke-width="2.6"/>
    <text x="{ml + 44}" y="{mt + 42}" fill="{C_TEXT2}">慢线</text>
    <path d="M{ml + 100},{mt + 16}l4.5,8h-9Z" fill="{C_UP}"/>
    <text x="{ml + 109}" y="{mt + 25}" fill="{C_TEXT2}">买</text>
    <path d="M{ml + 100},{mt + 42}l4.5,-8h-9Z" fill="{C_DOWN}"/>
    <text x="{ml + 109}" y="{mt + 42}" fill="{C_TEXT2}">卖</text>
  </g>
</svg>'''


# ============================================================
# 图二：净值曲线（策略 vs 买入持有）
# ============================================================
def equity_svg(dates: List[str], equity: List[float], bench: List[float],
               width: int = 920, height: int = 260, points: int = 240) -> str:
    if not equity:
        return ""
    eq = _downsample(equity, points)
    bn = _downsample(bench, points) if bench else []
    ds = _downsample(dates, points)

    ml, mr, mt, mb = 54, 14, 16, 34
    pw, ph = width - ml - mr, height - mt - mb
    vals = list(eq) + list(bn)
    y_lo, y_hi = _nice_range(vals)

    def y_of(v):
        return mt + (y_hi - v) / (y_hi - y_lo) * ph

    def poly(seq):
        n = len(seq)
        return " ".join(
            f"{ml + i * pw / (n - 1):.1f},{y_of(v):.1f}" for i, v in enumerate(seq)
        )

    base_y = y_of(1.0)
    ticks = []
    for t in range(5):
        v = y_lo + (y_hi - y_lo) * t / 4
        y = y_of(v)
        ticks.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" stroke="{C_BORDER}" stroke-width="0.7"/>'
            f'<text x="{ml - 7}" y="{y + 3.5:.1f}" font-size="10" fill="{C_TEXT3}" text-anchor="end">{v:.2f}</text>'
        )

    xlab = []
    n = len(ds)
    for t in range(5):
        k = int((n - 1) * t / 4)
        xlab.append(
            f'<text x="{ml + k * pw / (n - 1):.0f}" y="{height - 12}" font-size="10" '
            f'fill="{C_TEXT3}" text-anchor="middle">{esc(str(ds[k])[2:])}</text>'
        )

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <rect width="{width}" height="{height}" fill="#fff"/>
  {''.join(ticks)}
  <line x1="{ml}" y1="{base_y:.1f}" x2="{ml + pw}" y2="{base_y:.1f}" stroke="{C_TEXT3}" stroke-width="0.8" stroke-dasharray="3 3"/>
  {f'<polyline points="{poly(bn)}" fill="none" stroke="{C_BENCH}" stroke-width="1.6" stroke-dasharray="5 3"/>' if bn else ''}
  <polyline points="{poly(eq)}" fill="none" stroke="{C_MAIN}" stroke-width="2.2"/>
  {''.join(xlab)}
</svg>'''


# ============================================================
# 图三：回撤（面积，单口径）
# ============================================================
def drawdown_svg(dates: List[str], dd: List[float],
                 width: int = 920, height: int = 130, points: int = 240,
                 color: str = "", caption: str = "") -> str:
    if not dd:
        return ""
    color = color or C_DOWN
    vals = _downsample(dd, points)
    ds = _downsample(dates, points)
    ml, mr, mt, mb = 54, 14, 12, 26
    pw, ph = width - ml - mr, height - mt - mb
    worst = max(vals) or 0.0001
    hi = max(worst * 1.15, 0.02)

    def y_of(v):
        return mt + (v / hi) * ph

    n = len(vals)
    pts = [f"{ml},{mt}"]
    pts += [f"{ml + i * pw / (n - 1):.1f},{y_of(v):.1f}" for i, v in enumerate(vals)]
    pts.append(f"{ml + pw},{mt}")

    ticks = []
    for t in range(3):
        v = hi * t / 2
        y = y_of(v)
        ticks.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" stroke="{C_BORDER}" stroke-width="0.7"/>'
            f'<text x="{ml - 7}" y="{y + 3.5:.1f}" font-size="10" fill="{C_TEXT3}" text-anchor="end">-{v * 100:.0f}%</text>'
        )

    xlab = []
    for t in range(5):
        k = int((n - 1) * t / 4)
        xlab.append(
            f'<text x="{ml + k * pw / (n - 1):.0f}" y="{height - 8}" font-size="10" '
            f'fill="{C_TEXT3}" text-anchor="middle">{esc(str(ds[k])[2:])}</text>'
        )

    cap = ""
    if caption:
        cap = (f'<text x="{ml}" y="{mt + 12}" font-size="11" fill="{C_TEXT3}">'
               f'{esc(caption)}</text>')

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <rect width="{width}" height="{height}" fill="#fff"/>
  {''.join(ticks)}
  <polygon points="{' '.join(pts)}" fill="{color}" fill-opacity="0.14"/>
  <polyline points="{' '.join(pts[1:-1])}" fill="none" stroke="{color}" stroke-width="1.4"/>
  {cap}
  {''.join(xlab)}
</svg>'''


# ============================================================
# 图三·b：三个口径画在同一张图上（这才是「两个分母」的正确画法）
# ============================================================
def drawdown_compare_svg(dates: List[str], series: List[Dict[str, Any]],
                         width: int = 920, height: int = 260, points: int = 240) -> str:
    """
    series = [{"label": "峰值口径", "values": [...], "color": "#c0362c"}, ...]
    多条回撤曲线共用同一个 y 轴（都是 0 ~ 最差），这样高低关系才是真的。
    """
    series = [s for s in series if s.get("values")]
    if not series:
        return ""

    ml, mr, mt, mb = 56, 150, 16, 30
    pw, ph = width - ml - mr, height - mt - mb
    n = len(_downsample(series[0]["values"], points))

    worst = max(max(_downsample(s["values"], points)) for s in series) or 0.0001
    hi = max(worst * 1.15, 0.02)

    def y_of(v):
        return mt + (v / hi) * ph

    grids = []
    for t in range(4):
        v = hi * t / 3
        y = y_of(v)
        grids.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" '
            f'stroke="{C_BORDER}" stroke-width="0.7"/>'
            f'<text x="{ml - 8}" y="{y + 3.5:.1f}" font-size="10" fill="{C_TEXT3}" '
            f'text-anchor="end">-{v * 100:.0f}%</text>'
        )

    lines, legend = [], []
    for k, s in enumerate(series):
        vals = _downsample(s["values"], points)
        color = s.get("color") or C_DOWN
        pts = " ".join(f"{ml + i * pw / (n - 1):.1f},{y_of(v):.1f}"
                       for i, v in enumerate(vals))
        lines.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round"/>'
        )
        # 图例放右侧，不和曲线打架
        ly = mt + 18 + k * 26
        lines.append(
            f'<line x1="{ml + pw + 16}" y1="{ly - 4}" x2="{ml + pw + 40}" y2="{ly - 4}" '
            f'stroke="{color}" stroke-width="2.4"/>'
            f'<text x="{ml + pw + 46}" y="{ly}" font-size="11.5" fill="{C_TEXT2}">'
            f'{esc(s.get("label") or "")}</text>'
        )
        legend.append((s.get("label"), max(vals)))

    ds = _downsample(dates, points)
    xlab = []
    for t in range(5):
        k = int((n - 1) * t / 4)
        xlab.append(
            f'<text x="{ml + k * pw / (n - 1):.0f}" y="{height - 9}" font-size="10" '
            f'fill="{C_TEXT3}" text-anchor="middle">{esc(str(ds[k])[2:])}</text>'
        )

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <rect width="{width}" height="{height}" fill="#fff"/>
  {''.join(grids)}
  {''.join(lines)}
  {''.join(xlab)}
</svg>'''


# ============================================================
# HTML 组装
# ============================================================
SECTION_TITLES = {
    "kline": "在哪买、在哪卖",
    "equity": "复利：赚的钱继续投进去",
    "simple": "单利：每笔收益直接加在本金上",
    "drawdown": "回撤：从最高点跌下来多少",
    "metrics": "全套指标",
    "trades": "逐笔交易",
    "hysteresis": "回滞带对比",
}

# 模板能力清单：AI 回答用户前必须照这个说，不许再说「模板做不到」。
# 每个模块写清「画几张图、每张图的横纵轴是什么」，这样它描述页面时才准确。
SECTION_CAPS: Dict[str, str] = {
    "kline": "1 张 K 线图：日 K 蜡烛 + 快线 + 慢线 + 买卖点三角箭头，可滚轮缩放（截图里是最近 170 个交易日）",
    "equity": "1 张净值图：策略净值（紫实线）+ 买入持有（灰虚线），共用 y 轴，跌破 1.0 即亏钱",
    "simple": "1 张净值图：单利口径（收益不滚存）+ 买入持有，同样共用 y 轴",
    "drawdown": ("3 张回撤图：① 三口径叠加折线图（峰值/本金/单利同框，共用 y 轴，带图例和各自最深值）"
                 "② 复利·峰值面积图 ③ 复利·本金面积图；另附四口径对照表"
                 "（复利/单利 × 峰值/本金），并解释为什么峰值口径的数字更大"),
    "metrics": "指标卡三组：复利（收益/年化/回撤峰值/回撤本金）、单利（收益/年化/回撤峰值/回撤本金）、对比与质量（买入持有/超额/夏普/胜率/交易次数/持仓占比）",
    "trades": "1 张逐笔交易表：买入日、卖出日、买入价、卖出价、单笔收益（最多 40 笔）",
    "hysteresis": "1 张回滞对比表：0/0.5/1/2/3/5% 各自的收益、回撤、交易次数",
}


def describe_sections(sections: List[str],
                      result: Dict[str, Any],
                      hysteresis: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """
    返回「这份报告实际渲染了什么」，给 AI 看。

    为什么需要它：AI 之前会对用户说「报告模板只能画一根回撤线」——
    它没撒谎，它只是不知道模板长什么样。把渲染结果回喂给它，它才能准确描述自己的产出。
    """
    r = result or {}
    out: List[Dict[str, str]] = []
    for s in sections:
        cap = SECTION_CAPS.get(s)
        if not cap:
            continue
        if s == "hysteresis" and not hysteresis:
            continue          # 非均线策略 / 没开回滞，实际不会渲染
        item = {"section": s, "title": SECTION_TITLES.get(s, s), "content": cap}
        if s == "drawdown":
            item["numbers"] = (
                f"复利·峰值分母 -{(r.get('max_drawdown') or 0) * 100:.2f}%，"
                f"复利·本金分母 -{(r.get('max_drawdown_principal') or 0) * 100:.2f}%，"
                f"复利·亏损深度 -{(r.get('max_drawdown_loss') or 0) * 100:.2f}%"
            )
        out.append(item)
    return out


def _metric_cards(r: Dict[str, Any]) -> str:
    def card(label, value, cls="", hint=""):
        return (f'<div class="m"><div class="m-l">{esc(label)}'
                f'{f"<span class=tip title=\"{esc(hint)}\">?</span>" if hint else ""}</div>'
                f'<div class="m-v {cls}">{value}</div></div>')

    def pct(v, sign=True):
        return _fmt_pct(v, sign=sign)

    rows = [
        ("复利口径 · 赚的钱滚进本金继续投", [
            card("复利收益", pct(r.get("total_return")), "up" if (r.get("total_return") or 0) > 0 else "down"),
            card("复利年化", pct(r.get("annual_return"))),
            card("最大回撤（峰值分母）", f"-{(r.get('max_drawdown') or 0) * 100:.2f}%", "down",
                 "（峰值−当前）÷峰值。从历史最高净值跌下来的幅度，行业标准"),
            card("最大回撤（本金分母）", f"-{(r.get('max_drawdown_principal') or 0) * 100:.2f}%", "down",
                 "同一波跌幅 ÷ 本金 1.0。分母更小，所以一定比峰值分母大"),
            card("最大亏损（相对本金）", f"-{(r.get('max_drawdown_loss') or 0) * 100:.2f}%", "down",
                 "最惨时比本金少了多少。只算亏到本金以下的部分"),
        ]),
        ("单利口径 · 每笔赚的钱直接加在本金上，不再滚入", [
            card("单利收益", pct(r.get("simple_return"))),
            card("单利年化", pct(r.get("simple_annual_return"))),
            card("最大回撤（峰值分母）",
                 f"-{(r.get('simple_max_drawdown_peak') or 0) * 100:.2f}%", "down",
                 "以单利净值自己的历史高点为分母"),
            card("最大回撤（本金分母）",
                 f"-{(r.get('simple_max_drawdown_principal') or 0) * 100:.2f}%",
                 "down", "同一波跌幅 ÷ 本金 1.0"),
            card("最大亏损（相对本金）",
                 f"-{(r.get('simple_max_drawdown_loss') or 0) * 100:.2f}%", "down",
                 "最惨时比本金少了多少"),
        ]),
        ("对比与质量", [
            card("买入持有", pct(r.get("benchmark_return"))),
            card("超额收益", pct(r.get("excess_return"))),
            card("夏普比率", f"{(r.get('sharpe') or 0):.2f}"),
            card("胜率", f"{(r.get('win_rate') or 0) * 100:.1f}%"),
            card("交易次数", f"{r.get('trade_count') or 0}"),
            card("持仓时间占比", f"{(r.get('exposure') or 0) * 100:.1f}%"),
        ]),
    ]
    out = []
    for title, cards in rows:
        out.append(f'<div class="m-grp"><div class="m-t">{esc(title)}</div>'
                   f'<div class="m-row">{"".join(cards)}</div></div>')
    return "".join(out)


def _trades_table(trades: List[Dict[str, Any]], limit: int = 40) -> str:
    if not trades:
        return '<p class="muted">这次回测没有成交。</p>'
    rows = []
    for i, t in enumerate(trades[:limit], 1):
        pnl = t.get("pnl_pct")
        cls = "up" if (pnl or 0) > 0 else "down"
        rows.append(
            f'<tr><td>{i}</td><td>{esc(t.get("entry_date"))}</td>'
            f'<td>{esc(t.get("exit_date"))}</td>'
            f'<td>{float(t.get("entry_price") or 0):.2f}</td>'
            f'<td>{float(t.get("exit_price") or 0):.2f}</td>'
            f'<td class="{cls}">{_fmt_pct(pnl) if pnl is not None else "—"}</td></tr>'
        )
    more = ""
    if len(trades) > limit:
        more = f'<p class="muted">只显示前 {limit} 笔，共 {len(trades)} 笔。完整明细可在下载的脚本里跑出来。</p>'
    return f'''<table class="tbl">
<thead><tr><th>#</th><th>买入日</th><th>卖出日</th><th>买入价</th><th>卖出价</th><th>收益</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>{more}'''


def _hyst_table(h: Optional[Dict[str, Any]]) -> str:
    if not h or not h.get("rows"):
        return ""
    rows = []
    best = h.get("best_hysteresis")
    for r in h["rows"]:
        if r.get("error"):
            continue
        cls = ' class="best"' if r["hysteresis"] == best else ""
        label = "关闭" if r["hysteresis"] == 0 else f'{r["hysteresis"]}%'
        rows.append(
            f'<tr{cls}><td>{label}</td>'
            f'<td class="{"up" if r["total_return_pct"] > 0 else "down"}">{r["total_return_pct"]}%</td>'
            f'<td class="down">-{r["max_drawdown_pct"]}%</td>'
            f'<td>{r["sharpe"]}</td><td>{r["trade_count"]}</td></tr>'
        )
    return f'''<table class="tbl">
<thead><tr><th>回滞</th><th>收益</th><th>回撤</th><th>夏普</th><th>交易</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="muted">{esc(h.get("conclusion"))} {esc(h.get("note"))}</p>'''


def _volumes(bars: List[Bar]) -> List[float]:
    """成交量序列，给 AI 写的自定义图表用"""
    return [round(getattr(b, "volume", 0) or 0, 0) for b in (bars or [])]


def build_html(spec: Dict[str, Any], result: Dict[str, Any],
               bars: List[Bar], hysteresis: Optional[Dict[str, Any]] = None,
               symbol_name: str = "") -> str:
    title = spec.get("title") or "策略体检报告"
    subtitle = spec.get("subtitle") or ""
    sections: List[str] = spec.get("sections") or ["kline", "equity", "metrics"]
    # AI 自己写的补充区块：[{title, html}]。模板只提供骨架，
    # 它想加什么图/表/结论都可以自己写 HTML 塞进来。
    custom_sections: List[Dict[str, Any]] = spec.get("custom_sections") or []
    highlights: List[Dict[str, str]] = spec.get("highlights") or []
    conclusion = spec.get("conclusion") or ""

    r = result
    sym = r.get("symbol_code") or ""
    params = r.get("params") or {}

    # 什么时候要把 echarts（1MB）内联进来：
    #   1) 有 kline 模块（要交互 K 线）
    #   2) AI 的自定义区块里用到了 echarts
    # 第二条很容易漏：只选了 metrics + 自定义图的报告，
    # 库没内联，AI 的 echarts.init 直接 ReferenceError，图区一片空白。
    _uses_echarts = any("echarts" in ((cs or {}).get("html") or "")
                        for cs in (custom_sections or []) if isinstance(cs, dict))
    need_chart = (("kline" in sections) or _uses_echarts) \
        and report_charts is not None and report_charts.available()
    chart_lib = report_charts.chart_script_wrapper() if need_chart else ""

    # 有自定义区块时，把真实数据挂到 window.__REPORT_DATA__，
    # AI 写的脚本就能直接拿来画图，不用它自己编数据（编了就是假的）。
    report_data = ""
    if custom_sections:
        try:
            import json as _json
            blob = {
                "symbol": r.get("symbol_code"),
                "strategy": r.get("strategy_name"),
                "params": r.get("params") or {},
                "filters": r.get("filters") or [],
                "range": r.get("range"),
                "dates": r.get("dates") or [],
                "ohlc": [list(x) for x in (r.get("ohlc") or [])],
                "volume": _volumes(bars),
                "equity": r.get("equity") or [],
                "benchmark": r.get("benchmark_curve") or [],
                "simple": r.get("simple_curve") or [],
                "drawdown": r.get("drawdown_curve") or [],
                "trades": r.get("trades") or [],
                "metrics": {k: r.get(k) for k in (
                    "total_return", "annual_return", "max_drawdown", "max_drawdown_principal",
                    "simple_return", "simple_annual_return", "simple_max_drawdown_peak",
                    "sharpe", "win_rate", "trade_count", "benchmark_return", "excess_return")},
            }
            body_json = _json.dumps(blob, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
            report_data = f"<script>window.__REPORT_DATA__ = {body_json};</script>"
        except Exception:
            report_data = ""

    # 参数人话
    if r.get("strategy_type") == "ma_cross":
        p_txt = f'快线 {params.get("fast")} 天 / 慢线 {params.get("slow")} 天'
        if float(params.get("hysteresis") or 0) > 0:
            p_txt += f' / 回滞 {params["hysteresis"]}%'
    else:
        p_txt = ", ".join(f"{k}={v}" for k, v in params.items())

    # 过滤条件必须写在最显眼的位置：不然用户分不清图上是"裸策略"还是"优化版"
    filters_desc = (r.get("filters_desc") or "").strip()
    filter_html = ""
    if filters_desc:
        filter_html = (
            f'<div class="filter-badge">已叠加过滤条件：{esc(filters_desc)}</div>'
        )

    # AI 自己写的补充区块。模板只提供骨架和真实数据，
    # 它想加什么图表/表格都可以直接写 HTML（能引 echarts，数据在 __REPORT_DATA__）
    custom_html = ""
    for cs in (custom_sections or [])[:6]:
        if not isinstance(cs, dict):
            continue
        _t = (cs.get("title") or "").strip()
        _h = (cs.get("html") or "").strip()
        if not _h:
            continue
        _h = _h[:30000]          # 单个区块上限，防止 AI 写飞了
        custom_html += (f'<section class="card"><h2>{esc(_t or "补充分析")}</h2>'
                        f'{_h}</section>')

    blocks = []
    for s in sections:
        if s == "kline":
            # 优先用 ECharts 交互图（和 /strategies 页面同一套 dataZoom）；
            # 找不到 echarts 文件时自动降级成原来的静态 SVG，不让报告开天窗。
            if report_charts is not None and report_charts.available() and bars:
                chart_html = report_charts.kline_block(
                    bars, r.get("ma_fast") or [], r.get("ma_slow") or [],
                    r.get("markers") or [], symbol=sym)
                sub_txt = (f"全部 {len(bars)} 个交易日都在图里，<b>默认就显示全部</b>。"
                           "滚轮缩放、按住左键左右拖、鼠标停住看当天开高低收。")
            else:
                chart_html = candle_svg(bars, r.get("ma_fast") or [],
                                        r.get("ma_slow") or [], r.get("markers") or [])
                sub_txt = (f"红色三角是买入，绿色倒三角是卖出。"
                           f"这里显示最近 {min(170, len(bars))} 个交易日。")
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["kline"])}</h2>
  <p class="sub">{sub_txt}</p>
  {chart_html}
</section>''')
        elif s == "equity":
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["equity"])}</h2>
  <p class="sub">紫线是用策略，灰虚线是一直拿着不动。跌破 1.0 就是亏钱。</p>
  {equity_svg(r.get("dates") or [], r.get("equity") or [], r.get("benchmark_curve") or [])}
</section>''')
        elif s == "simple":
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["simple"])}</h2>
  <p class="sub">
    同样的交易，但赚到的钱不再滚进去。
    <b>空仓时这条线是横的</b>——不滚存也不计息，钱躺着不动；
    持仓时才跟着涨跌。所以它看起来是台阶状，这是对的，不是画错了。
  </p>
  {equity_svg(r.get("dates") or [], r.get("simple_curve") or [], r.get("benchmark_curve") or [])}
</section>''')
        elif s == "drawdown":
            # 回撤是「一个策略、多个分母」的典型场景，只画一根线等于什么都没解释。
            #
            # 核心：**分子是同一个**，就是「峰值 − 当前」这一波跌掉了多少。
            # 区别只在除以谁：除以峰值（>1）得到行业标准的最大回撤；
            # 除以本金（=1）分母更小，所以数字更大。
            # 第三种「亏损深度」换了个分子（本金 − 当前），回答「我到底亏钱了没」。
            dd_peak = r.get("drawdown_curve") or []
            dd_prin = r.get("principal_drawdown_curve") or []
            dd_simp_peak = r.get("simple_peak_drawdown_curve") or []
            dates = r.get("dates") or []

            peak_max = (r.get("max_drawdown") or 0) * 100
            prin_max = (r.get("max_drawdown_principal") or 0) * 100
            loss_max = (r.get("max_drawdown_loss") or 0) * 100
            simp_peak_max = (r.get("simple_max_drawdown_peak") or 0) * 100
            simp_prin_max = (r.get("simple_max_drawdown_principal") or 0) * 100
            simp_loss_max = (r.get("simple_max_drawdown_loss") or 0) * 100

            # 举「最深那次回撤」的真实数字来算，不能用全期 max/min
            eq = r.get("equity") or []
            if dd_peak and eq and len(dd_peak) == len(eq):
                i_worst = max(range(len(dd_peak)), key=lambda k: dd_peak[k])
                top = max(eq[:i_worst + 1])
                bottom = eq[i_worst]
            else:
                top, bottom = (max(eq), min(eq)) if eq else (1.0, 1.0)
            drop = top - bottom

            compare = drawdown_compare_svg(dates, [
                {"label": f"本金分母 · 最深 {prin_max:.2f}%", "values": dd_prin, "color": C_FAST},
                {"label": f"峰值分母 · 最深 {peak_max:.2f}%", "values": dd_peak, "color": C_DOWN},
                {"label": f"单利·峰值 · 最深 {simp_peak_max:.2f}%", "values": dd_simp_peak, "color": C_MAIN},
            ])
            area_peak = drawdown_svg(dates, dd_peak, height=120, color=C_DOWN,
                                     caption="分母 = 历史最高净值")
            area_prin = drawdown_svg(dates, dd_prin, height=120, color=C_FAST,
                                     caption="分母 = 初始本金 1.0")

            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["drawdown"])}</h2>
  <p class="sub">
    跌下去的钱是同一笔，<b>换个分母就是另一个百分数</b>。下表 2 × 3 = 六个数，
    都来自同一段行情，回答的是六个不同的问题。
  </p>
  {compare}
  <table class="tbl" style="margin-top:14px">
    <thead><tr>
      <th></th><th>峰值分母</th><th>本金分母</th><th>亏损深度</th>
    </tr></thead>
    <tbody>
      <tr>
        <td><b>复利</b><br><span class="tiny">赚的钱滚进本金</span></td>
        <td class="down">-{peak_max:.2f}%</td>
        <td class="down">-{prin_max:.2f}%</td>
        <td class="down">-{loss_max:.2f}%</td>
      </tr>
      <tr>
        <td><b>单利</b><br><span class="tiny">每笔赚的钱不滚存</span></td>
        <td class="down">-{simp_peak_max:.2f}%</td>
        <td class="down">-{simp_prin_max:.2f}%</td>
        <td class="down">-{simp_loss_max:.2f}%</td>
      </tr>
    </tbody>
  </table>
  <p class="sub" style="margin-top:8px">
    <b>峰值分母</b>＝(峰值−当前)÷峰值　<b>本金分母</b>＝(峰值−当前)÷本金 1.0　
    <b>亏损深度</b>＝本金−当前（跌破本金才有值）
  </p>
  <div class="dd-pair">
    <div><div class="dd-cap">复利·峰值分母（＝行业标准的最大回撤）</div>{area_peak}</div>
    <div><div class="dd-cap">复利·本金分母（同样跌幅 ÷ 1.0）</div>{area_prin}</div>
  </div>
  <div class="note-box">
    <b>为什么「本金分母」一定 ≥「峰值分母」？</b><br>
    因为<b>分子是同一个</b>：净值从 <b>{top:.2f}</b> 跌到 <b>{bottom:.2f}</b>，这波跌掉了 <b>{drop:.2f}</b>。<br>
    · 除以峰值 {top:.2f}：{drop:.2f} ÷ {top:.2f} = <b>{(drop / top * 100) if top else 0:.2f}%</b><br>
    · 除以本金 1.00：{drop:.2f} ÷ 1.00 = <b>{drop * 100:.2f}%</b><br>
    分母 1.00 比 {top:.2f} 小，所以除出来的数更大 —— 这就是「本金分母回撤更大」的全部原因。<br>
    <br>
    <b>「亏损深度」为什么反而最小？</b><br>
    因为它换了分子：算的是「本金 1.00 − 当前 {bottom:.2f} = {1 - bottom:.2f}」，
    只算<b>亏到本金以下</b>的那部分，不包含「先赚到 {top:.2f} 又还回去」的钱。
    它回答的是「我现在到底亏钱了没」，不是「最高点回落了多少」。
  </div>
</section>''')
        elif s == "metrics":
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["metrics"])}</h2>
  {_metric_cards(r)}
</section>''')
        elif s == "trades":
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["trades"])}</h2>
  {_trades_table(r.get("trades") or [])}
</section>''')
        elif s == "hysteresis" and hysteresis:
            blocks.append(f'''<section class="card">
  <h2>{esc(SECTION_TITLES["hysteresis"])}</h2>
  <p class="sub">回滞 = 快线要超出慢线多少才算真交叉。参数不是越大越好，得看数据。</p>
  {_hyst_table(hysteresis)}
</section>''')

    hl_html = ""
    if highlights:
        items = "".join(
            f'<div class="hl"><div class="hl-l">{esc(x.get("label"))}</div>'
            f'<div>{esc(x.get("text"))}</div></div>'
            for x in highlights
        )
        hl_html = f'<section class="card highlights">{items}</section>'

    concl_html = ""
    if conclusion:
        para = "".join(f"<p>{esc(p)}</p>" for p in str(conclusion).split("\n") if p.strip())
        concl_html = f'<section class="card"><h2>结论</h2><div class="concl">{para}</div></section>'

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    name = f"{sym} {symbol_name}".strip()

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
  :root {{
    --text: {C_TEXT}; --text2: {C_TEXT2}; --text3: {C_TEXT3};
    --border: {C_BORDER}; --bg: {C_BG}; --primary: {C_MAIN};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px 20px 60px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 14px; line-height: 1.65;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  .kicker {{ font-size: 11px; letter-spacing: 1.2px; color: var(--text3); text-transform: uppercase; }}
  h1 {{ font-size: 25px; margin: 6px 0 8px; letter-spacing: -0.3px; }}
  .meta {{ font-size: 12.5px; color: var(--text2); }}
  .meta span + span::before {{ content: "·"; margin: 0 7px; color: var(--text3); }}
  .card {{
    background: #fff; border: 1px solid var(--border); border-radius: 12px;
    padding: 18px 20px; margin-top: 14px;
  }}
  h2 {{ font-size: 15px; margin: 0 0 4px; font-weight: 600; }}
  .sub {{ font-size: 12px; color: var(--text3); margin: 0 0 12px; }}
  .muted {{ font-size: 12px; color: var(--text3); margin: 8px 0 0; }}
  .highlights {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }}
  .hl-l {{ font-size: 11px; color: var(--text3); margin-bottom: 3px; }}
  .m-grp {{ margin-bottom: 14px; }}
  .m-grp:last-child {{ margin-bottom: 0; }}
  .m-t {{ font-size: 11.5px; color: var(--text3); margin-bottom: 6px; }}
  .m-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }}
  .m {{ background: var(--bg); border-radius: 8px; padding: 9px 11px; }}
  .m-l {{ font-size: 11px; color: var(--text3); }}
  .m-v {{ font-size: 17px; font-weight: 600; margin-top: 2px; letter-spacing: -0.2px; }}
  .up {{ color: {C_UP}; }}
  .down {{ color: {C_DOWN}; }}
  .tip {{
    display: inline-block; width: 12px; height: 12px; line-height: 11px; text-align: center;
    border-radius: 50%; border: 1px solid var(--text3); color: var(--text3);
    font-size: 9px; margin-left: 3px; cursor: help;
  }}
  .tbl {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  .tbl th {{
    text-align: right; font-weight: 500; color: var(--text3); font-size: 11px;
    padding: 5px 7px; border-bottom: 1px solid var(--border);
  }}
  .tbl th:first-child, .tbl td:first-child {{ text-align: left; }}
  .tbl td {{ padding: 6px 7px; text-align: right; border-bottom: 1px solid #f1f1ec; }}
  .tbl tr.best {{ background: #f2f1fb; }}
  /* 两个回撤口径并排；窄屏自动叠成上下 */
  .dd-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; }}
  .dd-cap {{ font-size: 11.5px; color: var(--text3); margin-bottom: 4px; }}
  @media (max-width: 760px) {{ .dd-pair {{ grid-template-columns: 1fr; }} }}
  /* 口径解释框：这类「为什么数字是这样」的说明必须显眼，
     藏在正文小字里等于没说 —— 用户看不懂就会怀疑数据算错了 */
  .note-box {{
    margin-top: 14px; padding: 12px 14px; border-radius: 8px;
    background: #fbfbfa; border: 1px solid var(--border);
    font-size: 12.5px; line-height: 1.85; color: var(--text2);
  }}
  .note-box b {{ color: var(--text); }}
  .note-box .tiny {{ display: inline-block; margin-top: 4px; color: var(--text3); font-size: 11.5px; }}
  .concl p {{ margin: 0 0 8px; }}
  .concl p:last-child {{ margin-bottom: 0; }}
  /* 有过滤条件时在标题下面挂一条，避免"图是裸策略、字是优化版"的歧义 */
  .filter-badge {{
    display: inline-block; margin: 2px 0 10px;
    background: #eeecfa; color: #4b43a8;
    border: 1px solid #d9d5f5; border-radius: 7px;
    padding: 5px 11px; font-size: 12.5px; line-height: 1.5;
  }}
  footer {{
    margin-top: 26px; padding-top: 14px; border-top: 1px solid var(--border);
    font-size: 11.5px; color: var(--text3); line-height: 1.8;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .card {{ break-inside: avoid; border-color: #ddd; }}
  }}
</style>
</head>
<body>
{chart_lib}
{report_data}
<div class="wrap">
  <div class="kicker">全域策略研究所 · 策略体检报告</div>
  <h1>{esc(title)}</h1>
  {filter_html}
  <div class="meta">
    {f"<span>{esc(name)}</span>" if name else ""}
    <span>{esc(p_txt)}</span>
    <span>{esc(r.get("range") or "")}</span>
    <span>{esc(r.get("bar_count") or 0)} 个交易日</span>
  </div>
  {f'<p class="meta" style="margin-top:8px">{esc(subtitle)}</p>' if subtitle else ""}

  {hl_html}
  {''.join(blocks)}
  {custom_html}
  {concl_html}

  <footer>
    生成时间：{now}　·　数据截至 {esc(r.get("data_cutoff") or "")}<br>
    数据为前复权日线，信号 T 日产生、T+1 成交，不含未来函数；涨停日不可买入；已扣双边费用。<br>
    历史表现不代表未来，回测收益通常高于实盘。本报告为技术工具输出，不构成投资建议。
  </footer>
</div>
</body>
</html>'''


def safe_filename(s: str, fallback: str = "report") -> str:
    """文件名只留安全字符，避免路径穿越"""
    s = re.sub(r"[^\w\u4e00-\u9fa5.\-]+", "_", (s or "").strip())
    return (s[:60] or fallback)
