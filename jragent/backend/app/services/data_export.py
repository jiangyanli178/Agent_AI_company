"""
数据导出 —— 用户想拿走自己的数据，就给他拿走。

为什么要做：
1. 用户会想自己用 Excel 复核一遍，或者拿去喂给别的工具
2. 平台不该用"数据留在我们这儿"来锁客，那不是护城河
3. 服务器上本来就存着日线，算完直接吐给浏览器，不占额外空间

所有导出都是「流式返回、不落盘」：拼好字符串直接响应，
服务器上不会留临时文件，用户下完就走。

CSV 统一带 UTF-8 BOM：不加的话 Excel 打开中文是乱码，
这个坑几乎每个人都会踩一次。
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

BOM = "\ufeff"


def _csv_text(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return BOM + buf.getvalue()


def bars_csv(bars, symbol: str = "", name: str = "") -> str:
    """日线原始数据：日期、四价、量额、涨跌幅"""
    rows = []
    prev_close = None
    for b in bars:
        chg = ""
        if prev_close:
            chg = f"{(b.close / prev_close - 1) * 100:.2f}"
        rows.append([
            b.trade_date,
            f"{b.open:.3f}", f"{b.high:.3f}", f"{b.low:.3f}", f"{b.close:.3f}",
            int(b.volume), chg,
        ])
        prev_close = b.close
    return _csv_text(
        ["日期", "开盘", "最高", "最低", "收盘", "成交量(股)", "涨跌幅(%)"],
        rows,
    )


def equity_csv(payload: Dict[str, Any]) -> str:
    """
    回测明细：每天的净值、买入持有净值、回撤。
    用户拿这个能在 Excel 里自己画图、自己算收益。
    """
    dates = payload.get("dates") or []
    equity = payload.get("equity") or []
    bench = payload.get("benchmark_curve") or []
    dd = payload.get("drawdown_curve") or []

    rows = []
    for i, d in enumerate(dates):
        rows.append([
            d,
            f"{equity[i]:.6f}" if i < len(equity) else "",
            f"{bench[i]:.6f}" if i < len(bench) else "",
            f"{dd[i] * 100:.2f}" if i < len(dd) else "",
        ])
    return _csv_text(["日期", "策略净值", "买入持有净值", "回撤(%)"], rows)


def trades_csv(payload: Dict[str, Any]) -> str:
    """逐笔交易：在哪天买、哪天卖、赚了多少"""
    rows = []
    for i, t in enumerate(payload.get("trades") or [], 1):
        entry = t.get("entry_date") or ""
        # 兼容 dataclass 转 dict 后的两种字段名
        entry_price = t.get("entry_price")
        exit_price = t.get("exit_price")
        pnl = t.get("pnl_pct")
        rows.append([
            i, entry, t.get("exit_date") or "",
            f"{entry_price:.3f}" if isinstance(entry_price, (int, float)) else "",
            f"{exit_price:.3f}" if isinstance(exit_price, (int, float)) else "",
            t.get("bars_held") or "",
            f"{pnl * 100:.2f}" if isinstance(pnl, (int, float)) else "",
        ])
    return _csv_text(
        ["序号", "买入日期", "卖出日期", "买入价", "卖出价", "持有天数", "收益率(%)"],
        rows,
    )


def factor_csv(dates: List[str], series: List[Optional[float]],
               factor_name: str = "因子") -> str:
    """单个因子的逐日取值，方便用户自己验证我们的算法"""
    rows = []
    for i, d in enumerate(dates):
        v = series[i] if i < len(series) else None
        rows.append([d, "" if v is None else f"{v:.6f}"])
    return _csv_text(["日期", factor_name], rows)


def multi_series_csv(dates: List[str], series_list: List[Dict[str, Any]]) -> str:
    """
    多条线导成一个宽表：日期 + 每条线一列。
    因子演示通常有「股价 / 快线 / 慢线」几条，一起给用户才看得懂。
    """
    header = ["日期"] + [s.get("name") or f"列{i+1}"
                         for i, s in enumerate(series_list)]
    rows = []
    for i, d in enumerate(dates):
        row = [d]
        for s in series_list:
            data = s.get("data") or []
            v = data[i] if i < len(data) else None
            row.append("" if v is None else f"{v:.4f}")
        rows.append(row)
    return _csv_text(header, rows)


def backtest_zip(payload: Dict[str, Any]) -> bytes:
    """
    打包：净值曲线 + 逐笔交易 + 参数说明。
    只打包文本，体积很小（几百 KB），不需要落盘。
    """
    r = payload.get("result") or payload
    meta = payload.get("meta") or {}

    info_lines = [
        "全域策略研究所 · 回测导出",
        f"导出时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"策略：{r.get('strategy_name') or meta.get('strategy_name') or ''}",
        f"标的：{r.get('symbol_code') or meta.get('symbol_code') or ''}",
        f"参数：{r.get('params') or meta.get('params') or ''}",
        f"区间：{(r.get('dates') or ['', ''])[0]} ~ {(r.get('dates') or ['', ''])[-1]}",
        "",
        "指标口径：",
        "  复利收益 = 期末净值 - 1（赚的钱继续投入）",
        "  单利收益 = 每笔收益直接相加（不滚动投入）",
        "  最大回撤（峰值）= 从历史最高净值跌下来的最大幅度",
        "  最大回撤（本金）= 从初始本金算起的最大亏损幅度",
        "  夏普 = 日收益均值 / 日收益标准差 × sqrt(252)，不分单利复利",
        "",
        f"策略收益：{(r.get('total_return') or 0) * 100:.2f}%",
        f"买入持有：{(r.get('benchmark_return') or 0) * 100:.2f}%",
        f"最大回撤：{(r.get('max_drawdown') or 0) * 100:.2f}%",
        f"交易次数：{r.get('trade_count') or 0}",
        "",
        "说明：",
        "  - 数据为前复权日线，信号 T 日产生、T+1 成交，不含未来函数",
        "  - 涨停日不可买入，会被拦截并计数",
        "  - 已扣双边费用（默认单边万十，含佣金+印花税+滑点）",
        "  - 历史表现不代表未来",
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("净值曲线.csv", equity_csv(r).encode("utf-8"))
        z.writestr("逐笔交易.csv", trades_csv(r).encode("utf-8"))
        z.writestr("说明.txt", "\n".join(info_lines).encode("utf-8"))
    return buf.getvalue()
