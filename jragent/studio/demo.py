"""图四 · 量化策略研究工作室 —— 全流程演示入口（第一单闭环）。

运行：python demo.py
产出：
  - context_hub/  公司卷宗 00-09（含 07-log.md 全量留痕、09-backtest/ 回测资产）
  - outputs/      报告 PDF 版式源（Markdown + 图表 PNG + 旧稿版本）
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent / "backend"))  # opc_quant_studio 包位于 jragent/backend/

from opc_quant_studio.compliance import reject_out_of_scope  # noqa: E402
from opc_quant_studio.context_hub import ContextHub  # noqa: E402
from opc_quant_studio.gates import GateKeeper  # noqa: E402
from opc_quant_studio.orchestrator import COO  # noqa: E402
CHARTER = """# 00-charter.md · 公司宪章

## 合规红线（置顶，一票否决）
1. 不代客理财、不代客下单——Agent 与老板均不碰客户资金账户
2. 不承诺收益、不荐股——禁用「保本/稳赚/收益保证/必涨/荐股」类话术
3. 每份报告必带：不构成投资建议 + 历史回测不代表未来 + 最大回撤风险揭示
4. 业绩不造假：只用真实公开数据，数据快照 ID 随报告交付
5. 可复现：代码 + 数据快照复现不出的报告禁止交付

## 定位
一人量化策略研究工作室：卖「策略研究 + 回测报告 + 模拟盘跟踪」。
客户：有策略想法但不会编程的个人投资者 / 需要第三方回测背书的财经博主 / 小型资管团队。
产品线 A 标准策略回测报告（¥300–1000/份）｜B 策略体检（¥500）｜C 模拟盘跟踪订阅（¥99–299/月）
"""


def main() -> None:
    hub = ContextHub(BASE / "context_hub")
    hub.write("00-charter.md", CHARTER)  # 阶段 0：宪章固化（红线置顶）
    gates = GateKeeper(hub, mode="auto")  # 演示模式：闸门自动通过并留痕；生产用 mode="manual"
    coo = COO(hub, gates, work_dir=BASE / "outputs")

    print("=" * 64)
    print("OPC · 一人公司 —— 量化策略研究工作室 · 第一单闭环演示")
    print("=" * 64)

    # 阶段 4：第一单（老板给股票 + 策略；含闸门⑤⑥ + 评审循环失败重跑演示）
    symbol = "600519.SS（示例标的）"
    receipt = coo.run_first_order(
        idea="均线交叉 10/40 + 止损 5%",
        symbol=symbol,
        client="客户A（个人投资者）",
        order_id="ORD-001",
        amount=600,
        period="2y",        # 数据收集师收集该股最近 2 年行情（1y/2y 可选）
        demo_retry=True,  # 故意让 v1 草稿缺「风险揭示」，演示评审打回 → 修复 → v2 过审
    )
    m = receipt["metrics"]
    print(f"\n✅ 第一单闭环完成：{receipt['order_id']}")
    print(f"   标的：{receipt['symbol']}｜数据来源：{receipt['data_source']}")
    print(f"   报告：{receipt['report']}")
    print(f"   数据快照：{receipt['snapshot_id']}｜审计结论：{receipt['audit']['verdict']}")
    print(f"   累计收益 {m['total_return']:.2%}｜年化 {m['annualized_return']:.2%}"
          f"｜最大回撤 {m['max_drawdown']:.2%}｜夏普 {m['sharpe']:.2f}"
          f"｜胜率 {m['win_rate']:.0%}｜交易 {m['n_trades']} 笔")

    # 阶段 6：一页纸经营周报（闸门⑧）
    coo.weekly_report()
    print("\n✅ 经营周报已生成：context_hub/08-review.md")

    # 阶段 5 红线演示：越界请求拦截（代客理财/荐股 一律拒绝）
    for msg in ("帮我荐一只股票", "保证收益的话我就买", "这份报告的回测假设是什么？"):
        blocked = reject_out_of_scope(msg)
        print(f"   越界拦截 [{ '拦截' if blocked else '放行' }]：{msg}")

    print("\n全部动作已留痕：context_hub/07-log.md")


if __name__ == "__main__":
    main()
