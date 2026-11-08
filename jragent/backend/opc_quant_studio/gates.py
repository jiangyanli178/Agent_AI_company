"""人工闸门（Human-in-the-Loop）：9 道闸门，贯穿 8 个阶段。

三条铁律之二：不可逆动作必须过闸——Agent 只准备材料，老板拍板。
- auto 模式：演示用，自动通过并留痕（标注"演示模式自动通过"）
- manual 模式：交互式等待老板确认
"""

from __future__ import annotations

from .context_hub import ContextHub

# 闸门总表（与图四 9.2 节一致）
GATES = {
    1: "做什么生意（立案方向）",
    2: "定名；合规红项",
    3: "卖什么、承诺范围、免责声明",
    4: "对外公开发布",
    5: "价格与条款",
    6: "真实收付款（Agent 永不碰资金与交易）",
    7: "投诉/退款/舆情/法律",
    8: "周报决策、对外催款",
    9: "下一步战略",
}


class GateKeeper:
    def __init__(self, hub: ContextHub, mode: str = "auto"):
        assert mode in ("auto", "manual")
        self.hub = hub
        self.mode = mode

    def checkpoint(self, gate_no: int, subject: str, material: str) -> bool:
        """请求过闸。返回 True=放行，False=打回。决策写入日志。"""
        title = GATES[gate_no]
        if self.mode == "manual":
            print(f"\n{'='*60}\n【闸门{gate_no}】{title}\n事项：{subject}\n材料：{material}\n{'='*60}")
            ans = input("老板拍板（y=放行 / n=打回）：").strip().lower()
            approved = ans == "y"
        else:
            approved = True
        verdict = "放行（演示模式自动通过）" if approved else "打回"
        self.hub.log(
            agent="老板（人工闸门）",
            action=f"闸门{gate_no}·{title}",
            input_summary=f"{subject}｜{material}",
            output_summary=verdict,
        )
        return approved
