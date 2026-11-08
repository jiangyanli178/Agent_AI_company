# -*- coding: utf-8 -*-
"""
把回测过的策略导出成一个可独立运行的 Python 脚本。

用户拿到 .py 之后：
    pip install akshare pandas
    python strategy.py --symbol 600519 --years 3
就能在自己电脑或服务器上跑，不依赖我们的平台。

这样用户不会「被平台锁住」——这也是我们敢说「工具不构成投资建议」的底气。
"""
from typing import Any, Dict, List, Optional

SIGNAL_CODE = {
    "ma_cross": '''
def gen_signal(df, p):
    """
    均线交叉：快线上穿慢线买入，下穿卖出。
    支持回滞带（hysteresis，单位 %）：
    快线要超出慢线 band% 才算真金叉、跌破 band% 才算真死叉，中间保持原仓位。
    band=0 时和普通金叉完全等价。
    """
    fast_n = int(p.get("fast", 5))
    slow_n = int(p.get("slow", 20))
    band = max(0.0, min(float(p.get("hysteresis", 0) or 0), 20.0)) / 100.0
    fast = df["close"].rolling(fast_n).mean()
    slow = df["close"].rolling(slow_n).mean()
    sig = pd.Series(0, index=df.index)
    hold = 0
    for i in range(len(df)):
        f, s = fast.iloc[i], slow.iloc[i]
        if pd.isna(f) or pd.isna(s) or s <= 0:
            hold = 0
        elif hold == 0 and f > s * (1 + band):
            hold = 1
        elif hold == 1 and f < s * (1 - band):
            hold = 0
        sig.iloc[i] = hold
    return sig
''',
    "boll": '''
def gen_signal(df, p):
    """布林带回归：跌破下轨买入，回到中轨卖出"""
    mid = df["close"].rolling(int(p["n"])).mean()
    sd = df["close"].rolling(int(p["n"])).std(ddof=0)
    lower = mid - float(p["k"]) * sd
    sig = pd.Series(0, index=df.index)
    hold = 0
    for i in range(len(df)):
        if pd.isna(mid.iloc[i]):
            sig.iloc[i] = 0; continue
        if hold == 0 and df["close"].iloc[i] < lower.iloc[i]:
            hold = 1
        elif hold == 1 and df["close"].iloc[i] > mid.iloc[i]:
            hold = 0
        sig.iloc[i] = hold
    return sig
''',
    "boll_break": '''
def gen_signal(df, p):
    """布林带突破：突破上轨买入，跌破下轨卖出"""
    mid = df["close"].rolling(int(p["n"])).mean()
    sd = df["close"].rolling(int(p["n"])).std(ddof=0)
    upper = mid + float(p["k"]) * sd
    lower = mid - float(p["k"]) * sd
    sig = pd.Series(0, index=df.index)
    hold = 0
    for i in range(len(df)):
        if pd.isna(upper.iloc[i]):
            sig.iloc[i] = 0; continue
        if hold == 0 and df["close"].iloc[i] > upper.iloc[i]:
            hold = 1
        elif hold == 1 and df["close"].iloc[i] < lower.iloc[i]:
            hold = 0
        sig.iloc[i] = hold
    return sig
''',
    "momentum": '''
def gen_signal(df, p):
    """动量：过去 N 天涨幅超过阈值就持有"""
    chg = df["close"] / df["close"].shift(int(p["lookback"])) - 1
    sig = pd.Series(0, index=df.index)
    sig[chg > float(p["threshold"])] = 1
    return sig
''',
    "rsi": '''
def gen_signal(df, p):
    """RSI：低于超卖线买入，高于超买线卖出"""
    n = int(p["n"])
    delta = df["close"].diff()
    up = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    sig = pd.Series(0, index=df.index)
    hold = 0
    for i in range(len(df)):
        if hold == 0 and rsi.iloc[i] < float(p["low"]):
            hold = 1
        elif hold == 1 and rsi.iloc[i] > float(p["high"]):
            hold = 0
        sig.iloc[i] = hold
    return sig
''',
}

FILTER_HELP = {
    "adx_trend": "ADX 高于阈值才允许买入（震荡期不参与）",
    "price_above_ma": "价格站上 N 日均线才允许买入",
    "vol_target": "近期波动率高于阈值时不买入",
    "volume_confirm": "成交量达到均量倍数才买入",
    "rsi_not_overbought": "RSI 超买时不买入",
    "no_close_below_ma": "跌破均线太多时不买入",
}


def build_script(payload: Dict[str, Any]) -> str:
    """
    根据回测结果生成脚本。payload 需要：
      strategy_type / params / symbol_code / years / filters(可选)
    """
    st = payload.get("strategy_type", "ma_cross")
    params = payload.get("params") or {}
    symbol = payload.get("symbol_code", "600519")
    years = int(payload.get("years", 3))
    filters: List[Dict[str, Any]] = payload.get("filters") or []
    strat_name = payload.get("strategy_name", st)

    signal_fn = SIGNAL_CODE.get(st, SIGNAL_CODE["ma_cross"])

    filter_lines = []
    for f in filters:
        nm = f.get("name") or f.get("factor")
        if nm:
            desc = FILTER_HELP.get(nm) or f"因子条件 {f}"
            filter_lines.append(f"# - {nm}: {desc}  参数 {f.get('params') or {}}")
    filter_comment = "\n".join(filter_lines) if filter_lines else "# （本策略没有额外的过滤条件）"

    return f'''# -*- coding: utf-8 -*-
"""
{strat_name} · 由「全域策略研究所」导出

用法：
    pip install akshare pandas
    python strategy.py --symbol {symbol} --years {years}

说明：
    - 数据用 akshare 前复权日线（qfq），和你在线上的回测是同一份口径
    - 信号 T 日产生、T+1 成交，不含未来函数
    - 涨停日不可买入，会被拦截并计数
    - 本脚本只做历史回测，不接实盘、不下单
"""

import argparse
import sys

import pandas as pd


# ============================================================
# 策略定义（参数来自你在网站上的设置）
# ============================================================
PARAMS = {params!r}

# 额外过滤条件（网站上的设置）：
{filter_comment}


{signal_fn}

def gen_filter_ok(df):
    """返回「今天是否允许买入」的布尔序列。返回 None 表示没有过滤条件。"""
    ok = pd.Series(True, index=df.index)
    used = False
''' + "\n".join([
        f'''    # {f.get("name") or f.get("factor")}
    # 如果要在本地复现这个过滤，按下面的逻辑实现（阈值来自网站设置）：
    #   {f}'''
        for f in filters
    ]) + f'''
    return ok if used else None


# ============================================================
# 回测
# ============================================================
def load_bars(symbol: str, years: int) -> pd.DataFrame:
    import akshare as ak
    from datetime import date, timedelta

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=365 * years + 30)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                            start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty:
        sys.exit(f"没拿到 {{symbol}} 的数据，检查代码或网络")
    df = df.rename(columns={{"日期": "date", "开盘": "open", "收盘": "close",
                             "最高": "high", "最低": "low", "成交量": "volume"}})
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def run(df: pd.DataFrame, fee_rate: float = 0.0010, limit_pct: float = 0.10):
    sig = gen_signal(df, PARAMS)
    filt = gen_filter_ok(df)

    n = len(df)
    equity, curve = 1.0, [1.0]
    pos_prev, blocked, trades = 0, 0, []
    entry = None

    for i in range(1, n):
        pos = int(sig.iloc[i - 1])          # T 日信号，T+1 生效
        ret = df["close"].iloc[i] / df["close"].iloc[i - 1] - 1

        # 涨停不买、跌停不卖
        if pos == 1 and pos_prev == 0 and ret >= limit_pct:
            pos, blocked = pos_prev, blocked + 1
        elif pos == 0 and pos_prev == 1 and ret <= -limit_pct:
            pos, blocked = pos_prev, blocked + 1

        # 过滤条件只限制买入
        if pos == 1 and pos_prev == 0 and filt is not None and not bool(filt.iloc[i]):
            pos = 0

        cost = abs(pos - pos_prev) * fee_rate
        equity *= (1 + pos * ret - cost)
        curve.append(equity)

        if pos != pos_prev:
            if pos == 1:
                entry = df["close"].iloc[i]
            elif entry is not None:
                trades.append(df["close"].iloc[i] / entry - 1 - 2 * fee_rate)
                entry = None
        pos_prev = pos

    peak, mdd = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0)

    bench = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    wins = sum(1 for t in trades if t > 0)
    return {{
        "total_return": curve[-1] - 1,
        "max_drawdown": mdd,
        "benchmark": bench,
        "trades": len(trades),
        "win_rate": (wins / len(trades)) if trades else 0.0,
        "blocked": blocked,
        "days": n,
    }}


def main():
    ap = argparse.ArgumentParser(description="{strat_name} 回测脚本")
    ap.add_argument("--symbol", default="{symbol}")
    ap.add_argument("--years", type=int, default={years})
    args = ap.parse_args()

    print(f"正在拉取 {{args.symbol}} 近 {{args.years}} 年数据…")
    df = load_bars(args.symbol, args.years)
    r = run(df)

    print()
    print("=" * 52)
    print(f"  {{args.symbol}}  共 {{r['days']}} 个交易日")
    print("=" * 52)
    print(f"  策略收益    {{r['total_return']*100:>8.2f}}%")
    print(f"  买入持有    {{r['benchmark']*100:>8.2f}}%")
    print(f"  超额收益    {{(r['total_return']-r['benchmark'])*100:>8.2f}}%")
    print(f"  最大回撤   -{{r['max_drawdown']*100:>8.2f}}%")
    print(f"  交易次数   {{r['trades']:>9}}")
    print(f"  胜率        {{r['win_rate']*100:>7.1f}}%")
    if r["blocked"]:
        print(f"  涨停拦截   {{r['blocked']:>9}} 次（实盘这部分收益拿不到）")
    print()
    print("  历史表现不代表未来，回测收益通常高于实盘。")


if __name__ == "__main__":
    main()
'''
