"""报告设计：净值曲线/回撤图/月度收益图 + 报告排版（Markdown 交付版）。

红线 3：报告必带「不构成投资建议 + 历史回测不代表未来 + 最大回撤风险揭示」，
由 compliance.ensure_disclaimers 强制注入后再定稿。
"""

from __future__ import annotations

from pathlib import Path

from .backtest import BacktestResult
from .compliance import ensure_disclaimers

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _charts(result: BacktestResult, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    curve = result.equity_curve
    xs = list(range(len(curve)))

    # 净值曲线
    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(xs, curve, color="#1f6feb", lw=1.2)
    ax.set_title("Equity Curve")
    ax.set_xlabel("bars")
    fig.tight_layout()
    p1 = out_dir / "equity_curve.png"
    fig.savefig(p1, dpi=110)
    plt.close(fig)

    # 回撤图
    peak, dd = curve[0], []
    for v in curve:
        peak = max(peak, v)
        dd.append(v / peak - 1)
    fig, ax = plt.subplots(figsize=(8, 2.5))
    ax.fill_between(xs, dd, 0, color="#d1242f", alpha=0.5)
    ax.set_title("Drawdown")
    fig.tight_layout()
    p2 = out_dir / "drawdown.png"
    fig.savefig(p2, dpi=110)
    plt.close(fig)

    # 月度收益（按 21 个交易日分桶近似）
    monthly = []
    for i in range(0, len(curve) - 1, 21):
        end = min(i + 21, len(curve) - 1)
        monthly.append(curve[end] / curve[i] - 1)
    fig, ax = plt.subplots(figsize=(8, 2.5))
    colors = ["#2da44e" if r >= 0 else "#d1242f" for r in monthly]
    ax.bar(range(len(monthly)), monthly, color=colors)
    ax.set_title("Monthly Returns (approx.)")
    fig.tight_layout()
    p3 = out_dir / "monthly_returns.png"
    fig.savefig(p3, dpi=110)
    plt.close(fig)

    return {"equity": p1, "drawdown": p2, "monthly": p3}


def _interpretation_md(spec: dict) -> str:
    """「策略解读与微调指引」章节：用户描述多为不清晰的逻辑，逐条说明翻译理解。"""
    interp = spec.get("interpretation") or {}
    lines = [
        "用户给出的策略为自然语言描述，部分逻辑并不清晰。本章把「您写的 → 我们理解的」"
        "逐条对照列出，并标注每项参数是**用户明确指定**还是**默认假设**，供您核对与微调。",
        "",
        f"> **您的原始描述**：{spec['raw_idea']}",
        "",
        f"**理解概况**：{interp.get('clarity', '以下按「' + spec['raw_idea'] + '」的理解展开，标注为默认假设处均可微调。')}",
        "",
        "### 逐条解读对照",
        "",
        "| 原文线索 | 翻译结果 | 确定性 | 说明 |",
        "|---|---|---|---|",
    ]
    for it in interp.get("items", []):
        lines.append(f"| {it['原文线索']} | {it['解读']} | {it['确定性']} | {it['说明']} |")
    lines += [
        "",
        "### 参数微调指引",
        "",
        "下表列出本次回测实际使用的全部参数。若您发现任何一项与本意不符，"
        "按「如何微调」列修改想法描述后重新下单即可，无需改动代码：",
        "",
        "| 参数 | 当前取值 | 来源 | 如何微调 |",
        "|---|---|---|---|",
    ]
    for p in interp.get("params", []):
        lines.append(f"| {p['参数']} | {p['取值']} | {p['来源']} | {p['如何微调']} |")
    if interp.get("assumptions"):
        lines += ["", "### 执行假设（未在描述中给出，回测按以下口径执行）", ""]
        lines += [f"- {a}" for a in interp["assumptions"]]
    lines += ["", "### 待确认的不确定处", ""]
    if interp.get("to_confirm"):
        lines += [f"- ❓ {c}" for c in interp["to_confirm"]]
    else:
        lines.append("- 无。描述中给出的参数均已按原意执行；如与本意不符，请对照上表微调后重新下单。")
    return "\n".join(lines)


def _audit_md(audit: dict, headline: str = "审计结论", h: str = "###") -> str:
    """风控审计正文：检查总表 + 两位专职审计员的完整详细结论（结论含判定标准/
    实测数据/逐项判定/风险含义/微调建议）。h：结论小节标题层级。"""
    body = ["**检查总表**", ""]
    body += [f"- {'✅' if c['passed'] else '❌'} {c['item']}：{c['detail']}"
             for c in audit["checks"]]
    body.append(f"\n{headline}：**{audit['verdict']}**")
    for key, who in (("execution", "执行风险审计员完整审计结论（未来函数 / 参数敏感性）"),
                     ("model", "模型风险审计员完整审计结论（过拟合 / 幸存者偏差）")):
        rep = audit.get(key) or {}
        if rep.get("conclusion_md"):
            body += ["", f"{h} {who}", "", rep["conclusion_md"]]
    return "\n".join(body)


def render_report(result: BacktestResult, audit: dict, out_dir: Path,
                  order_id: str, draft_drop_section: str | None = None) -> Path:
    """生成《策略研究报告》Markdown。draft_drop_section 用于演示 Critic Loop 打回。"""
    m = result.metrics
    spec = result.spec

    interpretation_md = _interpretation_md(spec)

    sections = {
        "摘要": (
            f"本报告针对策略「{spec['name']}」（原始想法：{spec['raw_idea']}）进行量化建模与历史回测研究。"
            f"回测区间共 {len(result.dates)} 个交易日，数据快照 {result.snapshot_id}，"
            f"手续费与滑点假设 {result.cost_rate:.2%}/边。本报告为研究服务交付物，不构成投资建议。"
        ),
        "策略解读与微调指引": interpretation_md,
        "策略规格": (
            f"- 规则类型：{spec['kind']}\n- 入场：{spec['entry_rule']}\n- 出场：{spec['exit_rule']}\n"
            f"- 快线/慢线：{spec['fast']}/{spec['slow']}\n- 止损：{spec['stop_loss']:.0%}\n"
            f"- 仓位：{spec['position']:.0%}\n- 执行滞后：{result.execution_lag} 根K线（t 日信号 t+1 日成交）"
        ),
        "回测假设": (
            f"数据口径：{result.data_source or '公开行情'}（快照见 09-backtest/）；手续费与滑点合计 "
            f"{result.cost_rate:.2%}/边；未考虑涨跌停与流动性冲击；年化按 252 交易日。"
            + ("\n\n> ⚠️ 数据性质：本次为**合成演示数据（非真实行情）**，仅用于流程演示，"
               "报告交付须以真实公开数据重跑。" if "synthetic" in (result.data_source or "") else "")
        ),
        "回测结果": (
            f"| 指标 | 数值 |\n|---|---|\n"
            f"| 累计收益 | {m['total_return']:.2%} |\n"
            f"| 年化收益 | {m['annualized_return']:.2%} |\n"
            f"| 最大回撤 | {m['max_drawdown']:.2%} |\n"
            f"| 夏普比率 | {m['sharpe']:.2f} |\n"
            f"| 胜率 | {m['win_rate']:.1%} |\n"
            f"| 交易次数 | {m['n_trades']} |\n"
            f"| 年化换手 | {m['turnover']:.1f} |\n"
        ),
        "风险揭示": (
            f"本策略历史最大回撤为 {m['max_drawdown']:.2%}；样本外（后 30%）夏普为 "
            f"{audit['oos_sharpe']:.2f}（样本内 {audit['is_sharpe']:.2f}）；"
            f"参数扰动夏普极差 {audit['sensitivity_spread']:.2f}。"
            "历史回测不代表未来表现，极端行情下回撤可能显著扩大。"
        ),
        "风控审计": _audit_md(audit),
        "可复现说明": (
            f"数据快照 ID：{result.snapshot_id}；策略参数 JSON 与回测代码存于 `09-backtest/{order_id}/`，"
            "凭快照 + 代码可完整复现本报告全部指标。"
        ),
    }

    if draft_drop_section:  # 演示用：v1 草稿故意缺一节，触发评审打回
        sections.pop(draft_drop_section, None)

    body = f"# 策略研究报告 · {spec['name']}\n\n" + "\n\n".join(
        f"## {title}\n\n{content}" for title, content in sections.items()
    )
    body = ensure_disclaimers(body, max_drawdown=m["max_drawdown"])

    charts = _charts(result, out_dir)
    body += (
        "\n## 附图\n\n"
        f"![净值曲线]({charts['equity'].name})\n\n"
        f"![回撤]({charts['drawdown'].name})\n\n"
        f"![月度收益]({charts['monthly'].name})\n"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text(body, encoding="utf-8")
    return path


def render_portfolio_report(result: BacktestResult, audit: dict,
                            stock_reports: list[dict], out_dir: Path,
                            order_id: str) -> Path:
    """《多股票组合研究报告》：同一策略 × 多只股票，组合单报告 + 各股对照表。

    stock_reports 每项：{"symbol", "report": Path, "metrics", "snapshot_id",
    "data_source", "audit_verdict"}——个股报告（全窗口口径）已各自单独交付，
    本报告的对照表采用**对齐窗口口径**（与组合指标可比，见回测假设）。
    """
    m = result.metrics
    spec = result.spec
    per_stock: dict[str, dict] = m.get("per_stock", {})
    symbols = [e["symbol"] for e in stock_reports]

    def _rel(p) -> str:
        try:
            return Path(p).relative_to(out_dir).as_posix()
        except ValueError:
            return Path(p).name

    # ---- 各股对照表（对齐窗口口径）----
    rows = ["| 标的 | 累计收益 | 年化收益 | 最大回撤 | 夏普 | 胜率 | 交易次数 | 年化换手 | 数据快照 | 个股报告 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    entry_by_sym = {e["symbol"]: e for e in stock_reports}
    for sym, ps in per_stock.items():
        e = entry_by_sym.get(sym, {})
        rows.append(
            f"| {sym} | {ps['total_return']:.2%} | {ps['annualized_return']:.2%} | "
            f"{ps['max_drawdown']:.2%} | {ps['sharpe']:.2f} | {ps['win_rate']:.1%} | "
            f"{ps['n_trades']} | {ps['turnover']:.1f} | {ps.get('snapshot_id', '')} | "
            f"[个股报告]({_rel(e.get('report', ''))}) |"
        )
    stock_table = "\n".join(rows)

    src_rows = ["| 标的 | 数据来源 | 个股审计结论 |", "|---|---|---|"]
    for e in stock_reports:
        src_rows.append(f"| {e['symbol']} | {e.get('data_source', '')} | {e.get('audit_verdict', '')} |")
    src_table = "\n".join(src_rows)

    sections = {
        "摘要": (
            f"本报告对策略「{spec['name']}」（原始想法：{spec['raw_idea']}）在 **{len(symbols)} 只标的**"
            f"（{('、'.join(symbols))}）上做同一策略的组合历史回测研究。组合口径：{m.get('weighting', '等权')}；"
            f"对齐后公共交易日 {m.get('aligned_days', len(result.dates))} 天，数据快照 {result.snapshot_id}。"
            f"每只标的另有独立个股报告（见「回测结果」对照表链接）。本报告为研究服务交付物，不构成投资建议。"
        ),
        "策略解读与微调指引": _interpretation_md(spec) +
            "\n\n> 本次同一策略同时作用于多只标的，策略解读对全部标的一致；"
            "各标的仅数据不同，微调参数将同步影响全部个股报告与本组合报告。",
        "策略规格": (
            f"- 规则类型：{spec['kind']}\n- 入场：{spec['entry_rule']}\n- 出场：{spec['exit_rule']}\n"
            f"- 快线/慢线：{spec['fast']}/{spec['slow']}\n- 止损：{spec['stop_loss']:.0%}\n"
            f"- 仓位：{spec['position']:.0%}（每股独立满仓进出，组合层等权）\n"
            f"- 执行滞后：{result.execution_lag} 根K线（t 日信号 t+1 日成交）\n"
            f"- 适用标的：{('、'.join(symbols))}"
        ),
        "回测假设": (
            f"**组合口径**：各标的按**日期交集**对齐（不外推缺失日，杜绝未来函数）；"
            f"每股独立执行本策略（含各自止损/开平仓），组合日收益 = 各股策略日收益等权平均"
            f"（隐含按日再平衡的简化口径，未计再平衡成本冲击）。"
            f"\n\n**成本口径**：手续费与滑点合计 {result.cost_rate:.2%}/边（每股独立计提）；"
            "未考虑涨跌停与流动性冲击；年化按 252 交易日。"
            f"\n\n**数据口径**：\n\n{src_table}"
            + ("\n\n> ⚠️ 数据性质：部分标的为**合成演示数据（非真实行情）**（见上表数据来源），"
               "仅用于流程演示，报告交付须以真实公开数据重跑。"
               if any('synthetic' in (e.get('data_source') or '') for e in stock_reports) else "")
            + f"\n\n**口径差异提示**：上表个股报告展示各股**全数据窗口**指标；本报告对照表与组合指标"
            f"采用**对齐后公共窗口**（{m.get('aligned_days', len(result.dates))} 个交易日），"
            "两处数值可能略有差异，属口径差异而非计算错误。"
        ),
        "回测结果": (
            f"### 组合指标（{m.get('weighting', '等权')}）\n\n"
            f"| 指标 | 数值 |\n|---|---|\n"
            f"| 累计收益 | {m['total_return']:.2%} |\n"
            f"| 年化收益 | {m['annualized_return']:.2%} |\n"
            f"| 最大回撤 | {m['max_drawdown']:.2%} |\n"
            f"| 夏普比率 | {m['sharpe']:.2f} |\n"
            f"| 胜率 | {m['win_rate']:.1%} |\n"
            f"| 交易次数（全组合） | {m['n_trades']} |\n"
            f"| 年化换手（各股均值） | {m['turnover']:.1f} |\n\n"
            f"### 各股对照（对齐窗口口径）\n\n{stock_table}\n\n"
            f"### 分散化说明\n\n组合最大回撤 {m['max_drawdown']:.2%}"
            + ("，优于最差个股" if per_stock and m['max_drawdown'] > min(
                ps['max_drawdown'] for ps in per_stock.values()) else "")
            + "。分散可降低单一标的特异风险，但**不能消除系统性风险**；"
            "同一策略在不同标的上表现可能显著分化，个股表现详见对照表。"
        ),
        "风险揭示": (
            f"本组合历史最大回撤为 {m['max_drawdown']:.2%}；样本外（后 30%）组合夏普为 "
            f"{audit['oos_sharpe']:.2f}（样本内 {audit['is_sharpe']:.2f}）；"
            f"参数扰动组合夏普极差 {audit['sensitivity_spread']:.2f}。"
            "历史回测不代表未来表现，极端行情下回撤可能显著扩大；"
            "多标的同策略持仓在市场系统性下跌时可能同步回撤，组合分散效果将显著减弱。"
        ),
        "风控审计": (
            "### 组合级审计\n\n" + _audit_md(audit, headline="组合审计结论", h="####")
            + "\n\n### 个股审计结论汇总\n\n" + "\n".join(
                f"- {e['symbol']}：{e.get('audit_verdict', '')}（详见[个股报告]({_rel(e.get('report', ''))})）"
                for e in stock_reports
            )
        ),
        "可复现说明": (
            f"数据快照 ID：{result.snapshot_id}（各股明细见对照表）；"
            f"策略参数 JSON、各股与组合回测代码存于 `09-backtest/{order_id}/`"
            "（每股独立子目录 + `portfolio_strategy_spec.json`/`reproduce_portfolio.py`），"
            "凭快照 + 代码可完整复现本报告全部指标。"
        ),
    }

    body = f"# 多股票组合研究报告 · {spec['name']}\n\n" + "\n\n".join(
        f"## {title}\n\n{content}" for title, content in sections.items()
    )
    body = ensure_disclaimers(body, max_drawdown=m["max_drawdown"])

    charts = _charts(result, out_dir)
    body += (
        "\n## 附图\n\n"
        f"![组合净值曲线]({charts['equity'].name})\n\n"
        f"![组合回撤]({charts['drawdown'].name})\n\n"
        f"![组合月度收益]({charts['monthly'].name})\n"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "portfolio_report.md"
    path.write_text(body, encoding="utf-8")
    return path


# 报告验收标准（DoD）：评审 Agent 逐条打勾
DOD = ["摘要", "策略解读与微调指引", "策略规格", "回测假设", "回测结果", "风险揭示",
       "风控审计", "可复现说明", "免责声明与风险揭示"]
