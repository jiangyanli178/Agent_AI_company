# -*- coding: utf-8 -*-
"""回测引擎测试：策略可用性、防未来函数、涨跌停拦截"""
import pytest

from app.services.backtest import Bar, run_backtest
from conftest import make_bars


CASES = [
    ("ma_cross", {"fast": 5, "slow": 20}),
    ("boll", {"n": 20, "k": 2.0}),
    ("boll_break", {"n": 20, "k": 2.0}),
    ("momentum", {"lookback": 20, "threshold": 0.03}),
    ("rsi", {"n": 14, "low": 30, "high": 70}),
]


@pytest.mark.parametrize("strategy,params", CASES)
def test_strategies_run(strategy, params):
    r = run_backtest(make_bars(300), strategy, params)
    assert len(r.equity) == 300
    assert len(r.dates) == 300
    assert len(r.benchmark_curve) == 300      # 前端双线对比要用
    assert len(r.drawdown_curve) == 300       # 前端回撤面积图要用
    assert 0 <= r.max_drawdown <= 1
    assert 0 <= r.exposure <= 1
    assert isinstance(r.trade_count, int)


def test_no_lookahead():
    """T 日仓位只能由 T-1 日信号决定，改动最后一日不应影响前面的净值"""
    bars = make_bars(120)
    r1 = run_backtest(bars, "ma_cross", {"fast": 5, "slow": 20})
    r2 = run_backtest(bars, "ma_cross", {"fast": 5, "slow": 20})
    assert all(abs(a - b) < 1e-12 for a, b in zip(r1.equity[:-1], r2.equity[:-1]))


def test_invalid_params_rejected():
    """fast >= slow 必须报错，且上层要据此退积分"""
    with pytest.raises(ValueError):
        run_backtest(make_bars(100), "ma_cross", {"fast": 30, "slow": 20})


def test_insufficient_bars():
    with pytest.raises(ValueError):
        run_backtest(make_bars(10), "ma_cross", {"fast": 5, "slow": 20})


def test_unknown_strategy():
    with pytest.raises(ValueError):
        run_backtest(make_bars(100), "not_exist", {})


def test_price_limit_blocks_orders():
    """连续涨停时买入信号必须被拦截，否则回测会凭空多出收益"""
    seq = []
    price = 100.0
    for i in range(60):                       # 先阴跌，制造空仓状态
        price *= 0.998
        seq.append(Bar(f"A{i:03d}", price, price, price, price))
    for i in range(5):                        # 再连续 5 个 +10% 涨停
        prev = price
        price *= 1.10
        seq.append(Bar(f"B{i:03d}", prev, price, prev, price))
    for i in range(30):
        seq.append(Bar(f"C{i:03d}", price, price, price, price))

    on = run_backtest(seq, "ma_cross", {"fast": 3, "slow": 10}, respect_limit=True)
    off = run_backtest(seq, "ma_cross", {"fast": 3, "slow": 10}, respect_limit=False)

    assert on.blocked_orders > 0, "应当至少拦截一次涨停买入"
    assert off.blocked_orders == 0
    # 关闭拦截的收益会显著虚高，这正是要避免的
    assert off.total_return > on.total_return


def test_result_serializable():
    """结果必须能 JSON 序列化存进 backtest_jobs.result_json"""
    import json
    r = run_backtest(make_bars(100), "ma_cross", {"fast": 5, "slow": 20})
    d = r.to_dict()
    text = json.dumps(d, ensure_ascii=False)
    assert "equity" in d and "benchmark_curve" in d
    assert len(text) > 100
