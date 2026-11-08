"""合规模块：金融广告禁词扫描 + 免责声明强制注入（图四 9.3 金融合规红线）。"""

from __future__ import annotations

import re

# 金融广告禁词（02-brand.md 禁用词表）
BANNED_WORDS = [
    "保本", "稳赚", "稳赚不赔", "收益保证", "保证收益", "必涨", "零风险",
    "荐股", "代客理财", "代客下单", "包赚", "翻倍", "内部消息",
]

# 每份报告必带的声明（红线 3）
DISCLAIMERS = [
    "本报告不构成投资建议。",
    "历史回测不代表未来表现。",
]


def scan(text: str) -> list[str]:
    """返回命中的禁词列表（空 = 通过）。"""
    return [w for w in BANNED_WORDS if w in text]


def ensure_disclaimers(text: str, max_drawdown: float | None = None) -> str:
    """补全免责声明与风险揭示（红线 3：报告必带三项）。"""
    parts = []
    for d in DISCLAIMERS:
        if d not in text:
            parts.append(d)
    if max_drawdown is not None:
        risk = f"风险揭示：策略历史最大回撤为 {max_drawdown:.2%}，请充分评估自身风险承受能力。"
        if "最大回撤" not in text:
            parts.append(risk)
    if parts:
        text = text.rstrip() + "\n\n## 免责声明与风险揭示\n\n" + "\n".join(f"- {p}" for p in parts) + "\n"
    return text


def check_report(text: str) -> dict:
    """合规检查单：禁词 + 免责声明 + 风险揭示。"""
    violations = scan(text)
    has_disclaimer = all(d in text for d in DISCLAIMERS)
    has_drawdown_risk = "最大回撤" in text
    passed = not violations and has_disclaimer and has_drawdown_risk
    return {
        "passed": passed,
        "violations": violations,
        "has_disclaimer": has_disclaimer,
        "has_drawdown_risk": has_drawdown_risk,
    }


def reject_out_of_scope(message: str) -> bool:
    """越界拦截官：识别代客理财/荐股/收益承诺类请求（阶段 5）。"""
    patterns = [r"代客(理财|下单|操作)", r"帮我(买卖|炒股|下单)", r"荐.{0,4}(股|票|币)",
                r"推荐.*(股票|币|合约)", r"保证(收益|赚钱|盈利)", r"包赚"]
    return any(re.search(p, message) for p in patterns)
