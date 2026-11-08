# -*- coding: utf-8 -*-
"""
合规审查 Agent（风控）。

它不只是演示道具：金融产品最容易出事的就是 AI 张口乱承诺收益。
所以每份 AI 报告发出前都要过这一关。

演示脚本里"故意失败一次"也用它：
命中禁词 → 打回并标注位置 → 重写 → 通过，agent_runs 里留下两条记录。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import agent_log

log = logging.getLogger("app.critic")

# ---------------- 一票否决级：出现即打回 ----------------
BANNED_WORDS = [
    "稳赚", "保本", "必涨", "必赚", "躺赚", "包赚", "无风险",
    "内幕", "消息股", "庄家", "拉升在即",
    "现在买入", "现在卖出", "满仓", "清仓", "梭哈",
    "包你赚", "亏损包赔", "收益保证", " guaranteed",
]

# ---------------- 需要改写级：给更稳妥的说法 ----------------
REWRITE_RULES = [
    ("这个策略能赚钱", "这段历史区间里该策略有正收益，但不代表未来"),
    ("推荐买入", "历史回测显示……是否采用由你判断"),
    ("收益很高", "需要同时看最大回撤"),
    ("没有风险", "任何策略都有回撤风险"),
]

# ---------------- 每份报告必须附带 ----------------
REQUIRED_FOOTER = (
    "\n\n---\n"
    "历史表现不代表未来，回测收益通常高于实盘。\n"
    "本内容为技术工具输出，不构成投资建议。"
)


@dataclass
class Violation:
    word: str
    position: int
    context: str


@dataclass
class ReviewResult:
    passed: bool
    text: str
    violations: List[Violation] = field(default_factory=list)
    rounds: int = 0
    degraded: bool = False          # True 表示降级为模板输出


def scan(text: str) -> List[Violation]:
    """扫出所有一票否决级禁词，附位置与上下文，便于打回时说明"""
    found: List[Violation] = []
    if not text:
        return found
    for w in BANNED_WORDS:
        for m in re.finditer(re.escape(w.strip()), text):
            start = max(0, m.start() - 12)
            end = min(len(text), m.end() + 12)
            found.append(Violation(
                word=w.strip(), position=m.start(),
                context=text[start:end].replace("\n", " "),
            ))
    return found


def review(
    text: str,
    rewrite_fn: Optional[Callable[[str, List[Violation]], str]] = None,
    max_rounds: int = 3,
    biz_id: str = "",
) -> ReviewResult:
    """
    合规终审。
    rewrite_fn: 可选，接收(原文, 违规列表)返回重写文本，通常是一次模型调用。
                不传则只做检测不重写。
    """
    current = text or ""
    for round_no in range(1, max_rounds + 1):
        violations = scan(current)
        if not violations:
            final = current if REQUIRED_FOOTER.strip() in current else current + REQUIRED_FOOTER
            agent_log.record(
                "risk", "compliance_review", "SUCCESS", biz_id=biz_id,
                retry_count=round_no - 1,
                output_digest=f"第 {round_no} 轮通过，无违规词",
            )
            return ReviewResult(passed=True, text=final, rounds=round_no)

        words = "、".join(sorted({v.word for v in violations}))
        log.info("合规打回（第 %d 轮）：%s", round_no, words)
        agent_log.record(
            "risk", "compliance_review", "RETRY", biz_id=biz_id,
            retry_count=round_no - 1,
            output_digest=f"第 {round_no} 轮打回，违规词：{words}",
            error_msg=f"命中禁词 {len(violations)} 处：{words}",
        )

        if rewrite_fn is None:
            return ReviewResult(passed=False, text=current,
                                violations=violations, rounds=round_no)
        try:
            current = rewrite_fn(current, violations)
        except Exception as e:
            log.warning("合规重写失败：%s", e)
            break

    # 多轮仍不过 → 降级，绝不让违规内容出去
    agent_log.record(
        "risk", "compliance_review", "DEGRADED", biz_id=biz_id,
        output_digest="多次重写仍违规，降级为模板报告",
    )
    return ReviewResult(
        passed=False,
        text=_fallback_text(),
        violations=scan(current),
        rounds=max_rounds,
        degraded=True,
    )


def _fallback_text() -> str:
    """降级模板：不含任何评价性表述，只给事实"""
    return (
        "这次分析我没能给出足够稳妥的表述，所以只把客观结果留给你：\n"
        "回测结果中的收益、最大回撤、交易次数均为历史数据统计。\n"
        "你可以据此自行判断该策略是否值得进一步研究。"
        + REQUIRED_FOOTER
    )


def build_rewrite_prompt(original: str, violations: List[Violation]) -> str:
    """给模型的重写指令，指名道姓告诉它哪里违规"""
    detail = "\n".join(f"- 「{v.word}」出现在：……{v.context}……" for v in violations[:5])
    return (
        "下面这段策略分析里含有不合规的表述，请改写。\n\n"
        "【违规位置】\n" + detail + "\n\n"
        "【改写要求】\n"
        "1. 删除一切收益承诺与买卖指令\n"
        "2. 保留所有真实数字，不许改数字\n"
        "3. 如果策略跑输基准，必须明说，不许淡化\n"
        "4. 用大白话，面向完全不懂量化的新手\n\n"
        "【原文】\n" + original
    )
