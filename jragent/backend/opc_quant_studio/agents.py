"""执行层：专职 Agent 团队。

每个 Agent 只做自己的事、只读写 Context Hub，全部动作留痕。
（MiMo 能力标注见图四各表；本实现为可离线运行的确定性引擎，LLM 接口可替换）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .backtest import (COST_RATE, BacktestResult, StrategySpec, execution_risk_audit,
                       execution_risk_audit_portfolio, model_risk_audit,
                       model_risk_audit_portfolio, run_backtest, run_portfolio_backtest)
from .compliance import check_report
from .context_hub import ContextHub
from .data_manager import DataSnapshot, fetch_baostock, fetch_cn, fetch_public, save_snapshot, synthetic_ohlcv
from .report import DOD, render_portfolio_report, render_report


class Agent:
    name = "Agent"

    def __init__(self, hub: ContextHub):
        self.hub = hub

    def _log(self, action: str, input_summary: str, output_summary: str,
             tools: list[str] | None = None) -> None:
        self.hub.log(self.name, action, input_summary, output_summary, tools)


# ---------------- 阶段 4 履约产线 ----------------
class DataCollector(Agent):
    """数据收集师：老板给股票代码，收集该股最近 1-2 年公开行情（Tool Use + 数据清洗）。

    红线 4：只用真实公开数据。真实数据不可得时明确降级为合成演示数据，
    并在快照 meta 与报告中如实披露（业绩不造假）。
    """

    name = "数据收集师"

    PERIOD_DAYS = {"1y": 252, "2y": 504}

    def collect(self, symbol: str, period: str = "2y") -> DataSnapshot:
        n_days = self.PERIOD_DAYS.get(period, 504)
        # 数据源优先级：AKShare/东财（国内直连）→ BaoStock（国内直连备用）→ yfinance → 合成演示数据（降级须披露）
        errors = []
        snap = None
        for fetcher, tool in ((fetch_cn, "data.fetch_cn(akshare·东财·国内直连)"),
                              (fetch_baostock, "data.fetch_baostock(国内直连备用)"),
                              (fetch_public, "data.fetch_public(yfinance)")):
            try:
                snap = fetcher(symbol, n_days)
                self._log("收集行情", f"{symbol} 近 {period}",
                          f"真实公开数据 {snap.snapshot_id}（{len(snap.closes)} 根K线，{snap.source}）",
                          tools=[tool, "hub.save_snapshot"])
                break
            except Exception as e:
                errors.append(f"{fetcher.__name__}: {e}")
        if snap is None:  # 真实数据不可得 → 明确降级并披露
            snap = synthetic_ohlcv(symbol=symbol, n_days=n_days)
            self._log("收集行情", f"{symbol} 近 {period}",
                      f"真实数据不可得（{'; '.join(errors)}）→ 降级为合成演示数据 {snap.snapshot_id}",
                      tools=["data.synthetic_fallback", "hub.save_snapshot"])
        save_snapshot(snap, self.hub)
        return snap


class StrategyTranslator(Agent):
    """策略翻译官：自然语言策略想法 → 结构化策略规格 JSON（复杂推理 + 1M 上下文）。"""

    name = "策略翻译官"

    def translate(self, idea: str) -> StrategySpec:
        """自然语言 → 结构化规格。

        用户描述多为不清晰的逻辑，翻译时同步生成 interpretation（逐条解读 /
        参数来源 / 默认假设 / 待确认点），供报告「策略解读与微调指引」章节
        展示，便于用户对照微调和修改。
        """
        items: list[dict] = []      # 逐条解读对照：原文线索 → 翻译结果 → 确定性
        params: list[dict] = []     # 参数微调表：取值 + 来源 + 如何微调
        to_confirm: list[str] = []  # 不确定点：需用户确认或可微调处

        # 1. 规则类型：原文未说明时按默认假设处理并标注
        if re.search(r"突破", idea):
            kind, kind_src = "breakout", "用户明确（原文含「突破」）"
        elif re.search(r"均线|金叉|死叉|交叉", idea):
            kind, kind_src = "ma_cross", "用户明确（原文含均线/交叉表述）"
        else:
            kind, kind_src = "ma_cross", "默认假设（原文未说明规则类型）"
            to_confirm.append("规则类型未明确，暂按「均线交叉」理解；若您想做突破跟踪或其他规则，请在微调时说明。")
        items.append({
            "原文线索": "规则类型相关表述",
            "解读": "突破跟踪" if kind == "breakout" else "均线交叉（金叉买入/死叉卖出）",
            "确定性": kind_src,
            "说明": "决定开仓/平仓的触发方式",
        })

        # 2. 快/慢线周期：支持「10/40」「10x40」「10日和40日」等写法
        m = re.search(r"(\d+)\s*[/／,，xX×]\s*(\d+)", idea) \
            or re.search(r"(\d+)\s*[日天]\s*[和、与跟]?\s*(\d+)\s*[日天]", idea)
        if m:
            fast, slow = int(m.group(1)), int(m.group(2))
            fast_slow_src = f"用户明确（原文「{m.group(0)}」）"
            if fast >= slow:
                fast, slow = slow, fast
                to_confirm.append("原文周期大小顺序与「快线/慢线」相反，已自动对调，请确认是否符合本意。")
        else:
            fast, slow = 10, 40
            fast_slow_src = "默认假设（原文未给出周期）"
            to_confirm.append("均线/窗口周期未给出，暂用 10/40（快线/慢线），可按需微调。")
        items.append({
            "原文线索": m.group(0) if m else "（未提及周期）",
            "解读": f"快线 {fast} 日 / 慢线 {slow} 日",
            "确定性": fast_slow_src,
            "说明": "决定开平仓信号的计算窗口",
        })

        # 3. 止损
        m2 = re.search(r"止损\s*(\d+(?:\.\d+)?)\s*%", idea)
        if m2:
            stop = float(m2.group(1)) / 100
            stop_src = f"用户明确（原文「{m2.group(0)}」）"
        else:
            stop = 0.05
            stop_src = "默认假设（原文未给出止损）"
            to_confirm.append("止损比例未给出，暂用 5%，可按风险承受度微调。")
        items.append({
            "原文线索": m2.group(0) if m2 else "（未提及止损）",
            "解读": f"持仓浮亏达 {stop:.0%} 即强制离场",
            "确定性": stop_src,
            "说明": "控制单笔最大亏损的兜底规则",
        })

        # 4. 原文未提及、由翻译补全的部分：如实标注为默认假设
        items.append({"原文线索": "（未提及仓位）", "解读": "满仓（100%）一次性进出",
                      "确定性": "默认假设", "说明": "原文未描述资金管理/加减仓"})
        items.append({"原文线索": "（未提及做空/杠杆）", "解读": "只做多，离场即空仓等待",
                      "确定性": "默认假设", "说明": "原文未描述做空、杠杆与再入场规则"})

        if kind == "breakout":
            entry_rule = f"收盘价突破近 {slow} 日最高价时看多"
            exit_rule = f"收盘价跌破近 {slow} 日最低价（或止损 {stop:.0%}）离场"
        else:
            entry_rule = f"MA{fast} 上穿 MA{slow} 金叉看多"
            exit_rule = f"MA{fast} 下穿 MA{slow} 死叉离场（或止损 {stop:.0%}）"

        n_explicit = sum(1 for i in items if i["确定性"].startswith("用户明确"))
        clarity = (f"共 {len(items)} 项要点中 {n_explicit} 项由您的描述明确、"
                   f"{len(items) - n_explicit} 项为默认假设（下方逐项列出，可对照微调）")

        interpretation = {
            "clarity": clarity,
            "items": items,
            "params": [
                {"参数": "规则类型", "取值": "突破跟踪" if kind == "breakout" else "均线交叉",
                 "来源": kind_src, "如何微调": "在想法中写明「突破」或「均线交叉/金叉」"},
                {"参数": "快线周期", "取值": str(fast), "来源": fast_slow_src,
                 "如何微调": "在想法中以「快线/慢线」形式给出，如 5/20"},
                {"参数": "慢线周期", "取值": str(slow), "来源": fast_slow_src,
                 "如何微调": "同上，与快线周期一起给出"},
                {"参数": "止损", "取值": f"{stop:.0%}", "来源": stop_src,
                 "如何微调": "在想法中写「止损 X%」"},
                {"参数": "仓位", "取值": "100%（满仓进出）", "来源": "默认假设（原文未提仓位）",
                 "如何微调": "如需分批/半仓请说明仓位规则（当前引擎仅支持固定仓位）"},
            ],
            "assumptions": [
                "仓位：默认满仓（100%）一次性进出，未做加减仓与资金管理。",
                f"交易成本：手续费与滑点合计 {COST_RATE:.2%}/边（引擎统一口径）。",
                "执行滞后：t 日收盘信号 t+1 日成交（≥1 根K线，杜绝未来函数）。",
                "只做多：触发离场信号即空仓等待，不做空、不加杠杆。",
                "数据口径：标的近 1-2 年公开日线（来源见「回测假设」章）。",
            ],
            "to_confirm": to_confirm,
        }

        spec = StrategySpec(
            name=f"策略研究-{idea[:12]}", kind=kind, fast=fast, slow=slow,
            stop_loss=stop, entry_rule=entry_rule, exit_rule=exit_rule, raw_idea=idea,
            interpretation=interpretation,
        )
        self._log("策略翻译", idea,
                  f"规格：{kind} {fast}/{slow} 止损{stop:.0%}；{clarity}；"
                  f"待确认 {len(to_confirm)} 处（详见报告「策略解读与微调指引」）",
                  tools=["llm.translate"])
        return spec


class QuantEngineer(Agent):
    """量化工程师：写回测代码、跑回测、出指标（Coding + Tool Use）。"""

    name = "量化工程师"

    def build_and_run(self, spec: StrategySpec, snap: DataSnapshot,
                      order_id: str, asset_prefix: str = "") -> BacktestResult:
        """asset_prefix：多股票订单下每股资产存于独立子目录（如 600519/）。"""
        result = run_backtest(spec, snap)
        # 回测资产留痕（红线 5：可复现）——规格 + 可重跑脚本入 09-backtest/
        p = f"{asset_prefix}/" if asset_prefix else ""
        self.hub.save_backtest_asset(order_id, f"{p}strategy_spec.json",
                                     json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
        self.hub.save_backtest_asset(order_id, f"{p}reproduce.py", self._repro_script())
        self._log("跑回测", f"spec={spec.name} snap={snap.snapshot_id}",
                  f"metrics={result.metrics}", tools=["backtest.run", "hub.save_asset"])
        return result

    def build_and_run_portfolio(self, spec: StrategySpec, snaps: list,
                                order_id: str) -> BacktestResult:
        """同一策略 × 多只股票：等权组合回测（日期交集对齐）。"""
        result = run_portfolio_backtest(spec, snaps)
        self.hub.save_backtest_asset(order_id, "portfolio_strategy_spec.json",
                                     json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
        self.hub.save_backtest_asset(order_id, "reproduce_portfolio.py",
                                     self._repro_portfolio_script())
        self._log("跑组合回测", f"spec={spec.name} snaps={[s.snapshot_id for s in snaps]}",
                  f"组合 metrics={ {k: v for k, v in result.metrics.items() if k != 'per_stock'} }",
                  tools=["backtest.run_portfolio", "hub.save_asset"])
        return result

    @staticmethod
    def _repro_script() -> str:
        return (
            '"""凭数据快照 + 本脚本可复现报告全部指标（红线 5）。"""\n'
            "from opc_quant_studio.backtest import StrategySpec, run_backtest\n"
            "from opc_quant_studio.data_manager import load_csv\n\n"
            "snap = load_csv('SNAPSHOT.csv')\n"
            "spec = StrategySpec.from_dict(__import__('json').load(open('strategy_spec.json')))\n"
            "print(run_backtest(spec, snap).metrics)\n"
        )

    @staticmethod
    def _repro_portfolio_script() -> str:
        return (
            '"""凭各股数据快照 + 本脚本可复现组合报告全部指标（红线 5）。"""\n'
            "from opc_quant_studio.backtest import StrategySpec, run_portfolio_backtest\n"
            "from opc_quant_studio.data_manager import load_csv\n\n"
            "snaps = [load_csv(p) for p in ['SNAPSHOT_1.csv', 'SNAPSHOT_2.csv']]  # 按各股快照文件补充\n"
            "spec = StrategySpec.from_dict(__import__('json').load(open('portfolio_strategy_spec.json')))\n"
            "print(run_portfolio_backtest(spec, snaps).metrics)\n"
        )


class ExecutionRiskAuditor(Agent):
    """执行风险审计员：未来函数 / 参数敏感性（Coding + 复杂推理）。

    独立产出完整详细结论（判定标准 / 实测数据 / 判定 / 风险含义 / 微调建议），
    与模型风险审计员分工，由 COO 聚合为统一风控审计结论。
    """

    name = "执行风险审计员"

    def audit(self, spec: StrategySpec, snap: DataSnapshot,
              result: BacktestResult) -> dict:
        report = execution_risk_audit(spec, snap, result)
        self._log("执行风险审计（未来函数/参数敏感性）", spec.name,
                  f"结论={report['verdict']}，不通过项="
                  f"{[c['item'] for c in report['checks'] if not c['passed']] or '无'}",
                  tools=["backtest.execution_risk_audit"])
        return report

    def audit_portfolio(self, spec: StrategySpec, snaps: list,
                        result: BacktestResult) -> dict:
        report = execution_risk_audit_portfolio(spec, snaps, result)
        self._log("组合执行风险审计（未来函数/参数敏感性）",
                  f"{spec.name} × {len(snaps)} 标的",
                  f"结论={report['verdict']}，不通过项="
                  f"{[c['item'] for c in report['checks'] if not c['passed']] or '无'}",
                  tools=["backtest.execution_risk_audit_portfolio"])
        return report


class ModelRiskAuditor(Agent):
    """模型风险审计员：过拟合（样本内外）/ 幸存者偏差（Coding + 复杂推理）。

    独立产出完整详细结论（判定标准 / 实测数据 / 判定 / 风险含义 / 微调建议）。
    """

    name = "模型风险审计员"

    def audit(self, spec: StrategySpec, snap: DataSnapshot,
              result: BacktestResult) -> dict:
        report = model_risk_audit(spec, snap, result)
        self._log("模型风险审计（过拟合/幸存者偏差）", spec.name,
                  f"结论={report['verdict']}，不通过项="
                  f"{[c['item'] for c in report['checks'] if not c['passed']] or '无'}",
                  tools=["backtest.model_risk_audit"])
        return report

    def audit_portfolio(self, spec: StrategySpec, snaps: list,
                        result: BacktestResult) -> dict:
        report = model_risk_audit_portfolio(spec, snaps, result)
        self._log("组合模型风险审计（过拟合/幸存者偏差）",
                  f"{spec.name} × {len(snaps)} 标的",
                  f"结论={report['verdict']}，不通过项="
                  f"{[c['item'] for c in report['checks'] if not c['passed']] or '无'}",
                  tools=["backtest.model_risk_audit_portfolio"])
        return report


class ReportDesigner(Agent):
    """报告设计：图表 + 排版成报告（设计 + 多模态）。"""

    name = "报告设计"

    def render(self, result: BacktestResult, audit: dict, out_dir: Path,
               order_id: str, draft_drop_section: str | None = None) -> Path:
        path = render_report(result, audit, out_dir, order_id, draft_drop_section)
        self._log("报告成稿", order_id, f"输出 {path}", tools=["report.render"])
        return path

    def render_portfolio(self, result: BacktestResult, audit: dict,
                         stock_reports: list, out_dir: Path, order_id: str) -> Path:
        path = render_portfolio_report(result, audit, stock_reports, out_dir, order_id)
        self._log("组合报告成稿", order_id, f"输出 {path}", tools=["report.render_portfolio"])
        return path


class Reviewer(Agent):
    """评审 Agent：对照 DoD 与合规要求挑刺，否决权 ≤3 轮（Critique Loop）。"""

    name = "评审Agent"

    def review(self, report_text: str, result: BacktestResult,
               audit: dict) -> tuple[bool, list[str]]:
        issues = []
        for section in DOD:
            if f"## {section}" not in report_text:
                issues.append(f"缺少章节「{section}」")
        if result.metrics.get("max_drawdown") is None:
            issues.append("缺少最大回撤指标")
        if audit["verdict"] == "否决":
            issues.append("风控审计否决，禁止出报告")
        passed = not issues
        self._log("评审", result.spec.get("name", ""),
                  f"{'通过' if passed else '打回'}，issues={issues or '无'}")
        return passed, issues


class ComplianceOfficer(Agent):
    """合规官：禁词扫描 + 免责声明核对（网络安全意识，一票否决）。"""

    name = "合规官"

    def check(self, report_text: str) -> dict:
        report = check_report(report_text)
        self._log("合规检查", "报告全文",
                  f"{'通过' if report['passed'] else '不通过'}，禁词命中={report['violations'] or '无'}")
        return report


# ---------------- 阶段 4 交易动作（Agent 永不碰资金） ----------------
class Quoter(Agent):
    name = "报价官"

    FIXED_PRICE = 10  # 报价金额统一固定为 10 元
    PRICE_TABLE = {"标准策略回测报告": (10, 10), "策略体检": (10, 10), "模拟盘跟踪订阅": (10, 10)}

    def quote(self, product: str = "标准策略回测报告", amount: int = 10) -> dict:
        amount = self.FIXED_PRICE  # 固定值，忽略入参
        q = {"product": product, "amount": amount, "currency": "CNY",
             "terms": "交付物=策略研究报告（研究服务），不构成投资建议；交付周期 3 个工作日"}
        self._log("出报价", product, str(q), tools=["pricing.table"])
        return q


class OrderClerk(Agent):
    name = "订单员"

    def create_order(self, order_id: str, quote: dict, client: str) -> dict:
        order = {"order_id": order_id, "client": client, **quote, "status": "已建单"}
        self._log("建订单", order_id, str(order))
        return order


class Cashier(Agent):
    """收银员：只生成账单与收款链接——资金动作必须老板执行/确认（闸门⑥）。"""

    name = "收银员"

    def generate_invoice(self, order: dict) -> dict:
        invoice = {"invoice_no": f"INV-{order['order_id']}", "amount": order["amount"],
                   "pay_link": f"https://pay.example.com/{order['order_id']}",
                   "note": "Agent 仅生成账单，实际收款由老板执行/确认"}
        self._log("生成收款单", order["order_id"], str(invoice), tools=["billing.generate"])
        return invoice
