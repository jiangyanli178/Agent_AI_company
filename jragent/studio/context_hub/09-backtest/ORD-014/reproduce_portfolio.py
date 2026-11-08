"""凭各股数据快照 + 本脚本可复现组合报告全部指标（红线 5）。"""
from opc_quant_studio.backtest import StrategySpec, run_portfolio_backtest
from opc_quant_studio.data_manager import load_csv

snaps = [load_csv(p) for p in ['SNAPSHOT_1.csv', 'SNAPSHOT_2.csv']]  # 按各股快照文件补充
spec = StrategySpec.from_dict(__import__('json').load(open('portfolio_strategy_spec.json')))
print(run_portfolio_backtest(spec, snaps).metrics)
