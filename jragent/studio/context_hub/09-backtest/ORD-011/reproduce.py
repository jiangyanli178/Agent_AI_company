"""凭数据快照 + 本脚本可复现报告全部指标（红线 5）。"""
from opc_quant_studio.backtest import StrategySpec, run_backtest
from opc_quant_studio.data_manager import load_csv

snap = load_csv('SNAPSHOT.csv')
spec = StrategySpec.from_dict(__import__('json').load(open('strategy_spec.json')))
print(run_backtest(spec, snap).metrics)
