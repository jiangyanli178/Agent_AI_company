"""Context Hub：公司大脑（中枢层）。

单一事实来源：所有 Agent 读写这里的卷宗，禁止各自存一套版本。
- 卷宗 00-09 见图四 0.3 节清单
- 07-log.md 全量动作日志（时间 / Agent / 输入摘要 / 输出 / 工具调用 / 截图索引）
- 版本留痕：重要输出自动留 v1/v2/v3，可标记 stale 回滚重跑
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# 卷宗清单（文件名 -> 说明）
VOLUMES = {
    "00-charter.md": "公司宪章：定位、客户、价值主张、合规红线",
    "01-market.md": "调研：目标人群、竞品、定价带",
    "02-brand.md": "命名、VI、语调、金融广告禁词表",
    "03-product.md": "报告模板、回测规范、验收标准（DoD）",
    "04-ops.md": "SOP、话术库、FAQ、权限表、免责声明全文",
    "05-pipeline.md": "内容台账、线索台账、订单台账",
    "06-finance.md": "票据、收支、毛利、现金流",
    "07-log.md": "全量动作日志",
    "08-review.md": "周报、复盘、模拟盘跟踪记录、决策记录",
}


class ContextHub:
    """文件型 Context Hub：卷宗 + 日志 + 回测资产库（09-backtest/）。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "09-backtest").mkdir(exist_ok=True)  # 回测资产库
        for name in VOLUMES:
            path = self.root / name
            if not path.exists():
                path.write_text(f"# {name}\n\n> {VOLUMES[name]}\n\n", encoding="utf-8")

    # ---- 卷宗读写 ----
    def write(self, name: str, content: str, versioned: bool = False) -> Path:
        """写入卷宗；versioned=True 时保留历史版本（v1/v2...）。"""
        path = self.root / name
        if versioned and path.exists() and path.stat().st_size > 0:
            stem, suffix = path.stem, path.suffix
            v = 1
            while (self.root / f"{stem}.v{v}{suffix}").exists():
                v += 1
            path.rename(self.root / f"{stem}.v{v}{suffix}")
        path.write_text(content, encoding="utf-8")
        return path

    def append(self, name: str, content: str) -> Path:
        path = self.root / name
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return path

    def read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        return (self.root / name).exists()

    # ---- 留痕 ----
    def log(self, agent: str, action: str, input_summary: str,
            output_summary: str, tools: list[str] | None = None,
            screenshots: list[str] | None = None) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"## {ts} | {agent} | {action}\n"
            f"- 输入：{input_summary}\n"
            f"- 输出：{output_summary}\n"
            f"- 工具调用：{', '.join(tools or []) or '无'}\n"
            f"- 截图索引：{', '.join(screenshots or []) or '无'}\n\n"
        )
        self.append("07-log.md", entry)

    def mark_stale(self, name: str) -> None:
        """回滚机制：把已产出物标记为 stale，需重跑。"""
        path = self.root / name
        if path.exists():
            self.append("07-log.md", f"- [STALE] {name} 已标记失效，需从失败节点重跑\n")
            path.rename(path.with_name(path.stem + ".stale" + path.suffix))

    # ---- 回测资产库（09-backtest/，可复现红线）----
    def save_backtest_asset(self, order_id: str, filename: str, content: str | bytes) -> Path:
        d = self.root / "09-backtest" / order_id
        d.mkdir(exist_ok=True)
        path = d / filename
        path.parent.mkdir(parents=True, exist_ok=True)  # filename 可带子目录（多股票每股独立留痕）
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def save_json(self, name: str, data: dict) -> Path:
        return self.write(name, json.dumps(data, ensure_ascii=False, indent=2))
