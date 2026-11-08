# -*- coding: utf-8 -*-
"""
交互式 K 线 —— 把 ECharts 内联进报告，实现和 /strategies 页面一样的手感。

为什么不用原来的手绘 SVG：
SVG 是一张静态画，缩放拖动要么写一堆坐标换算，要么在浏览器里重绘整棵树。
统计过一遍，自己实现滚轮缩放 + 拖动 + 十字光标 + tooltip，至少要 300 行 JS，
而且很难做到和策略库一样顺滑。策略库本来就是 ECharts 的 dataZoom，
**用同一套库，行为才真的一致**。

代价：ECharts 压缩后约 1MB，内联后报告从 60KB 涨到 1MB 左右。
换来的是：断网能打开（不依赖 CDN）+ 交互和策略库完全一致。
报告是存档/分享用的，一次下载 1MB 可以接受。

如果部署机器上没有 frontend/node_modules（比如只跑了后端），
`available()` 返回 False，调用方自动降级回 SVG，不会报错。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

# 报告模板的配色（和 report_builder 保持一致：A 股红涨绿跌）
C_UP = "#c0362c"        # 涨
C_DOWN = "#1a7f4b"      # 跌
C_MAIN = "#534ab7"      # 策略主色
C_FAST = "#e8a33d"      # 快线
C_TEXT3 = "#8f8f86"
C_BORDER = "#e6e6df"

_LIB_CACHE: Optional[str] = None
_LIB_TRIED = False


def _lib_paths() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    # backend/app/services -> 项目根
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return [
        os.path.join(root, "frontend", "node_modules", "echarts", "dist", "echarts.min.js"),
        os.path.join(root, "node_modules", "echarts", "dist", "echarts.min.js"),
    ]


def echarts_lib() -> str:
    """读一次 ECharts 源码，之后走缓存（1MB 文件不能每份报告都读）"""
    global _LIB_CACHE, _LIB_TRIED
    if _LIB_TRIED:
        return _LIB_CACHE or ""
    _LIB_TRIED = True
    for p in _lib_paths():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    _LIB_CACHE = f.read()
                return _LIB_CACHE
            except Exception:
                continue
    _LIB_CACHE = ""
    return ""


def available() -> bool:
    return bool(echarts_lib())


def _js_json(obj: Any) -> str:
    """
    把 Python 对象变成能安全塞进 <script> 的 JSON。

    `</script>` 出现在字符串里会提前闭合标签；`\u2028/\u2029` 在旧 JS 引擎里是换行符。
    这两处必须转义，否则报告会出现"图没了、后面全白"的诡异现象。
    """
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("</", "<\\/")
             .replace("\u2028", "\\u2028")
             .replace("\u2029", "\\u2029"))


def kline_block(bars: Sequence[Any],
                ma_fast: Sequence[Optional[float]],
                ma_slow: Sequence[Optional[float]],
                markers: Sequence[Dict[str, Any]],
                height: int = 460,
                symbol: str = "") -> str:
    """
    生成 <div> + <script> 片段。

    默认 dataZoom 是 start=0 / end=100，也就是**一进来就是全部数据**。
    用户明确要求「无论怎样都必须展示全部时间」，
    所以不做"默认只看最近 170 天"那种取巧 —— 想看细节自己滚轮推近。

    底部那根 slider 也能拖，双击滑块可以跳回全量视图。
    """
    if not bars:
        return "<p class='sub'>没有 K 线数据。</p>"

    dates = [b.trade_date for b in bars]
    # ECharts candlestick 的数组顺序是 [开, 收, 低, 高]，别写成 OHLC
    ohlc = [[round(b.open, 3), round(b.close, 3), round(b.low, 3), round(b.high, 3)]
            for b in bars]
    vols = [round(getattr(b, "volume", 0) or 0, 0) for b in bars]

    def _series(vals: Sequence[Optional[float]]) -> List[Optional[float]]:
        if not vals or len(vals) != len(dates):
            return []
        out = [round(v, 3) if v is not None else None for v in vals]
        # 全是 None 等于没有这条线（RSI/KDJ 策略本来就没有均线），
        # 返回空数组让下面直接跳过，别在图例里留个空条目
        return out if any(x is not None for x in out) else []

    # 买卖点要按日期对齐到 x 轴（category 轴放 [date, price] 会被当字符串）
    # 用日期字符串而不是索引：category 轴上 [日期, 价格] 是 ECharts 的稳妥写法，
    # 索引在某些版本会被当成数值坐标，点就飘到左边去了
    known = set(dates)
    buys: List[List[Any]] = []
    sells: List[List[Any]] = []
    for m in markers or []:
        d = m.get("date")
        if d not in known:
            continue
        pt = [d, round(float(m.get("price") or 0), 3)]
        (buys if m.get("type") == "buy" else sells).append(pt)

    # 只有真有数据才画，图例也要跟着变
    ma_fast_s = _series(ma_fast)
    ma_slow_s = _series(ma_slow)
    legend = ["K线"]
    if ma_fast_s:
        legend.append("MA快")
    if ma_slow_s:
        legend.append("MA慢")
    if buys:
        legend.append("买入")
    if sells:
        legend.append("卖出")

    ma_js = ""
    if ma_fast_s:
        ma_js += (f"{{ name: 'MA快', type: 'line', data: D.maF, symbol: 'none', "
                  f"lineStyle: {{ width: 1, color: '{C_FAST}' }}, connectNulls: false }},")
    if ma_slow_s:
        ma_js += (f"{{ name: 'MA慢', type: 'line', data: D.maS, symbol: 'none', "
                  f"lineStyle: {{ width: 1, color: '{C_MAIN}' }}, connectNulls: false }},")
    sell_js = ""
    if sells:
        sell_js = (f"{{ name: '卖出', type: 'scatter', data: D.sells, symbol: 'triangle', "
                   f"symbolRotate: 180, symbolSize: 11, "
                   f"itemStyle: {{ color: '{C_DOWN}' }}, z: 10, symbolOffset: [0, -12] }},")

    payload = {
        "dates": dates,
        "ohlc": ohlc,
        "vol": vols,
        "maF": ma_fast_s,
        "maS": ma_slow_s,
        "buys": buys,
        "sells": sells,
    }

    return f'''<div id="kline-box" style="width:100%;height:{height}px"></div>
<div class="kline-tip">
  滚轮缩放 · 按住左键左右拖 · 鼠标停住看当天开高低收 · 底部滑条也能拖（双击回到全量）
</div>
<script>
(function () {{
  var D = {_js_json(payload)};
  var el = document.getElementById('kline-box');
  if (!el || typeof echarts === 'undefined') return;
  var chart = echarts.init(el, null, {{ renderer: 'canvas' }});
  chart.setOption({{
    animation: false,
    backgroundColor: 'transparent',
    textStyle: {{ fontSize: 11, color: '{C_TEXT3}' }},
    legend: {{
      top: 0, left: 0, itemWidth: 12, itemHeight: 8,
      textStyle: {{ fontSize: 11, color: '{C_TEXT3}' }},
      data: {_js_json(legend)}
    }},
    tooltip: {{
      trigger: 'axis',
      axisPointer: {{ type: 'cross', crossStyle: {{ color: '{C_TEXT3}' }} }},
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '{C_BORDER}',
      borderWidth: 1,
      textStyle: {{ fontSize: 12, color: '#2a2a26' }},
      formatter: function (ps) {{
        if (!ps || !ps.length) return '';
        var i = ps[0].dataIndex;
        var o = D.ohlc[i] || [];
        var chg = (o[0] ? ((o[1] - o[0]) / o[0] * 100) : 0);
        var cls = o[1] >= o[0] ? '{C_UP}' : '{C_DOWN}';
        var lines = [
          '<b>' + D.dates[i] + '</b>',
          '开 ' + o[0] + '　收 ' + o[1] + '　低 ' + o[2] + '　高 ' + o[3],
          '涨跌 <span style="color:' + cls + '">' + chg.toFixed(2) + '%</span>',
          '量 ' + (D.vol[i] || 0).toLocaleString()
        ];
        if (D.buys.some(function (p) {{ return p[0] === i; }})) lines.push('<span style="color:{C_UP}">▲ 买入点</span>');
        if (D.sells.some(function (p) {{ return p[0] === i; }})) lines.push('<span style="color:{C_DOWN}">▼ 卖出点</span>');
        return lines.join('<br>');
      }}
    }},
    axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }},
    grid: [
      {{ left: 58, right: 18, top: 34, height: '56%' }},
      {{ left: 58, right: 18, top: '74%', height: '14%' }}
    ],
    xAxis: [
      {{
        type: 'category', data: D.dates, gridIndex: 0, boundaryGap: true,
        axisLine: {{ lineStyle: {{ color: '{C_BORDER}' }} }},
        axisTick: {{ show: false }},
        splitLine: {{ show: false }},
        axisLabel: {{ show: false }}
      }},
      {{
        type: 'category', data: D.dates, gridIndex: 1, boundaryGap: true,
        axisLine: {{ lineStyle: {{ color: '{C_BORDER}' }} }},
        axisTick: {{ show: false }},
        splitLine: {{ show: false }},
        axisLabel: {{ fontSize: 10, color: '{C_TEXT3}', hideOverlap: true }}
      }}
    ],
    yAxis: [
      {{
        scale: true, gridIndex: 0, position: 'left',
        splitLine: {{ lineStyle: {{ color: '{C_BORDER}', type: 'dashed' }} }},
        axisLine: {{ show: false }}, axisTick: {{ show: false }},
        axisLabel: {{ fontSize: 10, color: '{C_TEXT3}' }}
      }},
      {{
        scale: true, gridIndex: 1, splitNumber: 2,
        splitLine: {{ show: false }},
        axisLine: {{ show: false }}, axisTick: {{ show: false }},
        axisLabel: {{ fontSize: 9, color: '{C_TEXT3}' }}
      }}
    ],
    // start:0 / end:100 = 打开就是全部时间，不做任何截断
    dataZoom: [
      {{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true }},
      {{ type: 'slider', xAxisIndex: [0, 1], start: 0, end: 100, bottom: 6, height: 18,
         borderColor: '{C_BORDER}', fillerColor: 'rgba(83,74,183,0.12)',
         handleStyle: {{ color: '{C_MAIN}' }},
         textStyle: {{ fontSize: 9, color: '{C_TEXT3}' }} }}
    ],
    series: [
      {{
        name: 'K线', type: 'candlestick', data: D.ohlc,
        itemStyle: {{ color: '{C_UP}', color0: '{C_DOWN}', borderColor: '{C_UP}', borderColor0: '{C_DOWN}' }}
      }},
      {ma_js}
      {{
        name: '买入', type: 'scatter', data: D.buys, symbol: 'triangle', symbolSize: 11,
        itemStyle: {{ color: '{C_UP}' }}, z: 10,
        symbolOffset: [0, 12]
      }},
      {sell_js}
      {{
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: D.vol,
        itemStyle: {{ color: 'rgba(83,74,183,0.35)' }}
      }}
    ]
  }});
  window.addEventListener('resize', function () {{ chart.resize(); }});
  // 双击滑条回到全量视图，方便用户"看完全部"这个动作一键完成
  el.addEventListener('dblclick', function () {{
    chart.dispatchAction({{ type: 'dataZoom', start: 0, end: 100 }});
  }});
}})();
</script>'''


def chart_script_wrapper() -> str:
    """把 ECharts 源码包进 <script>，只在该报告需要交互图时嵌入"""
    lib = echarts_lib()
    if not lib:
        return ""
    return f"<script>{lib}</script>"
