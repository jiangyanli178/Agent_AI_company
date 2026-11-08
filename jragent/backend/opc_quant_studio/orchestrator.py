"""编排层：COO Agent——拆任务、派活、收结果、卡节点、升级。

第一单闭环（阶段 4）：
  线索 → 报价 → 闸门⑤ → 订单 → 收款单 → 闸门⑥
       → 履约（翻译 → 回测 → 风控审计 → 评审循环 ≤3 轮 → 合规 → 报告）
       → 交付回执 + 订单入账
多股票下单：同一策略 × 多标的 → 每股独立报告 + 等权组合单报告。
阶段 6：一页纸经营周报（闸门⑧）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .agents import (Agent, Cashier, ComplianceOfficer, DataCollector,
                     ExecutionRiskAuditor, ModelRiskAuditor, OrderClerk,
                     QuantEngineer, Quoter, ReportDesigner, Reviewer,
                     StrategyTranslator)
from .backtest import merge_risk_audits
from .context_hub import ContextHub
from .gates import GateKeeper


class AuditRejected(Exception):
    """风控审计 3 轮否决 → 升级老板砍需求（携带审计明细供调用方/前端展示）。"""

    def __init__(self, audit: dict):
        super().__init__("风控审计 3 轮否决 → 升级老板砍需求")
        self.audit = audit


def parse_symbols(symbol: str) -> list[str]:
    """多股票代码解析：逗号/顿号/分号/空白分隔，去重保序；单只返回 [symbol]。"""
    parts = re.split(r"[,，、;；/|]+|\s+", symbol.strip())
    seen, out = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out or [symbol.strip()]


class COO(Agent):
    name = "COO"

    def __init__(self, hub: ContextHub, gates: GateKeeper, work_dir: Path):
        super().__init__(hub)
        self.gates = gates
        self.work_dir = Path(work_dir)
        self.translator = StrategyTranslator(hub)
        self.collector = DataCollector(hub)
        self.engineer = QuantEngineer(hub)
        self.exec_auditor = ExecutionRiskAuditor(hub)   # 未来函数 / 参数敏感性
        self.model_auditor = ModelRiskAuditor(hub)      # 过拟合 / 幸存者偏差
        self.designer = ReportDesigner(hub)
        self.reviewer = Reviewer(hub)
        self.compliance = ComplianceOfficer(hub)
        self.quoter = Quoter(hub)
        self.order_clerk = OrderClerk(hub)
        self.cashier = Cashier(hub)

    def _risk_audit(self, spec, snap, result) -> dict:
        """两个专职审计 Agent 分工审计后聚合：执行风险（未来函数/参数敏感性）
        + 模型风险（过拟合/幸存者偏差），各自产出完整详细结论。"""
        exec_report = self.exec_auditor.audit(spec, snap, result)
        model_report = self.model_auditor.audit(spec, snap, result)
        return merge_risk_audits(exec_report, model_report)

    def _risk_audit_portfolio(self, spec, snaps, result) -> dict:
        """组合级同口径：两个专职审计 Agent 分工审计后聚合。"""
        exec_report = self.exec_auditor.audit_portfolio(spec, snaps, result)
        model_report = self.model_auditor.audit_portfolio(spec, snaps, result)
        return merge_risk_audits(exec_report, model_report,
                                 per_stock=result.metrics.get("per_stock", {}))

    # ---------------- 阶段 4：第一单闭环 ----------------
    def run_first_order(self, idea: str, symbol: str, client: str = "客户A",
                        order_id: str = "ORD-001", amount: int = 10,
                        period: str = "2y", demo_retry: bool = True) -> dict:
        """symbol：老板给策略前先给出对应股票；period：数据收集师收集近 1-2 年行情。
        symbol 支持多只（逗号/顿号/分号/空格分隔）：每股独立报告 + 等权组合单报告。
        """
        self._log("开工", f"{symbol}｜{idea}", f"order={order_id}", tools=["coo.schedule"])

        # 1. 报价 → 闸门⑤：价格与条款
        quote = self.quoter.quote(amount=amount)
        assert self.gates.checkpoint(5, f"{quote['product']} 报价 ¥{quote['amount']}",
                                     quote["terms"]), "闸门⑤打回，流程中止"

        # 2. 建单 + 收款单 → 闸门⑥：真实收付款（Agent 永不碰资金）
        order = self.order_clerk.create_order(order_id, quote, client)
        invoice = self.cashier.generate_invoice(order)
        assert self.gates.checkpoint(6, f"收款 ¥{invoice['amount']}",
                                     invoice["note"] + "；付款由老板执行/确认"), "闸门⑥打回，流程中止"

        # 多股票下单：每股独立报告 + 等权组合单报告（单股票路径不变，见下）
        symbols = parse_symbols(symbol)
        if len(symbols) > 1:
            return self._run_multi_research(idea, symbols, period, order_id,
                                            client, order, invoice, quote)

        # 3. 履约调度：数据收集（老板给股票）→ 策略翻译 → 回测 → 风控审计（不过则定向打回 ≤3 轮）
        snap = self.collector.collect(symbol, period)
        spec = self.translator.translate(idea)

        audit = {"verdict": "否决"}
        for attempt in range(1, 4):
            result = self.engineer.build_and_run(spec, snap, order_id)
            audit = self._risk_audit(spec, snap, result)
            if audit["verdict"] != "否决":
                break
            self._log("升级", "风控审计否决",
                      f"第 {attempt} 轮未过，定向打回量化工程师（附复现步骤+期望/实际）")
        if audit["verdict"] == "否决":
            self._log("升级", "风控审计 3 轮否决", "升级老板：砍需求 / 换策略 / 换标的后重新下单")
            raise AuditRejected(audit)

        # 4. 报告成稿 + 评审循环（Critique Loop，否决权 ≤3 轮）
        out_dir = self.work_dir / order_id
        draft_drop = "风险揭示" if demo_retry else None  # 演示：v1 草稿缺一节，触发打回
        report_path = passed = None
        issues: list[str] = []
        for rnd in range(1, 4):
            report_path = self.designer.render(result, audit, out_dir, order_id,
                                               draft_drop_section=draft_drop)
            passed, issues = self.reviewer.review(report_path.read_text(encoding="utf-8"),
                                                  result, audit)
            if passed:
                break
            self._log("评审打回", f"第 {rnd} 轮",
                      f"issues={issues} → 定向修复后重跑（最多 3 轮）")
            draft_drop = None  # 修复：补齐缺失章节后重出 v2
            if report_path:
                # 旧稿留版本作废（重跑时版本号顺延，避免覆盖）
                v = 1
                while report_path.with_name(f"report.v{v}.md").exists():
                    v += 1
                report_path.rename(report_path.with_name(f"report.v{v}.md"))
        assert passed, f"评审 3 轮未过 → 升级老板：{issues}"

        # 5. 合规终检（一票否决）
        text = Path(report_path).read_text(encoding="utf-8")
        comp = self.compliance.check(text)
        assert comp["passed"], f"合规不通过：{comp['violations']}"

        # 6. 报告质检（交付物校验：指标/免责声明/可复现资产）
        asset_dir = self.hub.root / "09-backtest" / order_id
        qc_ok = all(k in result.metrics for k in
                    ("total_return", "annualized_return", "max_drawdown", "sharpe", "win_rate")) \
            and (asset_dir / "strategy_spec.json").exists() \
            and (asset_dir / "reproduce.py").exists()
        assert qc_ok, "交付物校验失败 → 自动补齐，仍缺则升级老板"

        # 7. 交付回执 + 订单入账
        order.update({"status": "已交付", "symbol": symbol,
                      "report": str(report_path), "snapshot_id": result.snapshot_id,
                      "metrics": result.metrics, "audit_verdict": audit["verdict"]})
        self._book_order(order)

        receipt = {
            "order_id": order_id, "client": client, "symbol": symbol,
            "report": str(report_path), "snapshot_id": result.snapshot_id,
            "data_source": result.data_source,
            "metrics": result.metrics, "audit": audit,
            "payment": invoice, "status": "第一单闭环完成",
        }
        self.hub.append("05-pipeline.md",
                        f"\n## 订单 {order_id}\n\n- 客户：{client}\n- 标的：{symbol}\n"
                        f"- 产品：{quote['product']}\n"
                        f"- 金额：¥{quote['amount']}\n- 报告：{report_path}\n- 状态：已交付\n")
        self._log("第一单闭环", order_id, "报价→收款单→回测→审计→评审→合规→交付回执 全部完成",
                  tools=["coo.pipeline"])
        return receipt

    # ---------------- 多股票：同一策略 × 多标的 ----------------
    def _run_multi_research(self, idea: str, symbols: list[str], period: str,
                            order_id: str, client: str, order: dict,
                            invoice: dict, quote: dict) -> dict:
        """多股票履约：每股独立闭环出报告 + 等权组合单报告（同一策略）。"""
        self._log("多股票履约", f"{len(symbols)} 标的：{('、'.join(symbols))}",
                  "每股独立报告 + 等权组合报告", tools=["coo.schedule"])
        spec = self.translator.translate(idea)
        out_dir = self.work_dir / order_id

        # 1. 每股独立闭环：数据 → 回测 → 审计（≤3 轮）→ 报告 → 评审（≤3 轮）→ 合规
        snaps: list = []
        stock_entries: list[dict] = []
        for sym in symbols:
            snap = self.collector.collect(sym, period)
            snaps.append(snap)
            safe = re.sub(r"[^\w.-]", "_", sym)  # 资产/报告子目录名（与 QC 校验口径一致）
            audit = {"verdict": "否决"}
            for attempt in range(1, 4):
                result = self.engineer.build_and_run(spec, snap, order_id, asset_prefix=safe)
                audit = self._risk_audit(spec, snap, result)
                if audit["verdict"] != "否决":
                    break
                self._log("升级", f"{sym} 风控审计否决",
                          f"第 {attempt} 轮未过，定向打回量化工程师（附复现步骤+期望/实际）")
            if audit["verdict"] == "否决":
                self._log("升级", f"{sym} 风控审计 3 轮否决",
                          "升级老板：砍需求 / 换策略 / 换标的后重新下单")
                raise AuditRejected(audit)
            report_path = self._deliver_report(result, audit, out_dir / safe,
                                               order_id, f"个股 {sym}")
            stock_entries.append({
                "symbol": sym, "report": str(report_path),
                "snapshot_id": snap.snapshot_id, "data_source": snap.source,
                "metrics": result.metrics, "audit_verdict": audit["verdict"],
            })

        # 2. 等权组合闭环：组合回测 → 组合审计（≤3 轮）→ 组合报告 → 评审（≤3 轮）→ 合规
        port_audit = {"verdict": "否决"}
        for attempt in range(1, 4):
            port_result = self.engineer.build_and_run_portfolio(spec, snaps, order_id)
            port_audit = self._risk_audit_portfolio(spec, snaps, port_result)
            if port_audit["verdict"] != "否决":
                break
            self._log("升级", "组合风控审计否决",
                      f"第 {attempt} 轮未过，定向打回量化工程师（附复现步骤+期望/实际）")
        if port_audit["verdict"] == "否决":
            self._log("升级", "组合风控审计 3 轮否决",
                      "升级老板：砍需求 / 换策略 / 换标的后重新下单")
            raise AuditRejected(port_audit)
        port_report = self._deliver_report(port_result, port_audit, out_dir, order_id,
                                           "组合报告", portfolio=stock_entries)

        # 3. 报告质检（各股资产 + 组合资产，红线 5 可复现）
        asset_dir = self.hub.root / "09-backtest" / order_id
        qc_ok = all(
            (asset_dir / re.sub(r"[^\w.-]", "_", e["symbol"]) / "strategy_spec.json").exists()
            and (asset_dir / re.sub(r"[^\w.-]", "_", e["symbol"]) / "reproduce.py").exists()
            for e in stock_entries
        ) and (asset_dir / "portfolio_strategy_spec.json").exists() \
            and (asset_dir / "reproduce_portfolio.py").exists() \
            and all(k in port_result.metrics
                    for k in ("total_return", "annualized_return", "max_drawdown", "sharpe", "win_rate"))
        assert qc_ok, "交付物校验失败 → 自动补齐，仍缺则升级老板"

        # 4. 交付回执 + 订单入账
        order.update({"status": "已交付", "symbol": "、".join(symbols), "symbols": symbols,
                      "report": str(port_report), "snapshot_id": port_result.snapshot_id,
                      "metrics": port_result.metrics, "audit_verdict": port_audit["verdict"],
                      "stocks": stock_entries})
        self._book_order(order)

        receipt = {
            "order_id": order_id, "client": client,
            "symbol": "、".join(symbols), "symbols": symbols,
            "report": str(port_report), "portfolio_report": str(port_report),
            "snapshot_id": port_result.snapshot_id, "data_source": port_result.data_source,
            "metrics": port_result.metrics, "audit": port_audit,
            "stock_reports": stock_entries,
            "payment": invoice, "status": "第一单闭环完成（多股票组合）",
        }
        self.hub.append("05-pipeline.md",
                        f"\n## 订单 {order_id}\n\n- 客户：{client}\n"
                        f"- 标的：{('、'.join(symbols))}（{len(symbols)} 只）\n"
                        f"- 产品：{quote['product']}\n- 金额：¥{quote['amount']}\n"
                        f"- 组合报告：{port_report}\n- 个股报告：{len(stock_entries)} 份\n"
                        f"- 状态：已交付\n")
        self._log("第一单闭环", order_id,
                  "多股票：每股独立报告 + 等权组合报告 全部完成", tools=["coo.pipeline"])
        return receipt

    def _deliver_report(self, result, audit, out_dir: Path, order_id: str,
                        label: str, portfolio: list | None = None) -> Path:
        """报告成稿 → 评审循环（≤3 轮，旧稿留版本）→ 合规终检。多股票产线复用。"""
        report_path = None
        passed, issues = False, []
        for rnd in range(1, 4):
            if portfolio is None:
                report_path = self.designer.render(result, audit, out_dir, order_id)
            else:
                report_path = self.designer.render_portfolio(result, audit, portfolio,
                                                             out_dir, order_id)
            passed, issues = self.reviewer.review(report_path.read_text(encoding="utf-8"),
                                                  result, audit)
            if passed:
                break
            self._log("评审打回", f"{label} 第 {rnd} 轮",
                      f"issues={issues} → 定向修复后重跑（最多 3 轮）")
            v = 1
            while report_path.with_name(f"{report_path.stem}.v{v}{report_path.suffix}").exists():
                v += 1
            report_path.rename(report_path.with_name(f"{report_path.stem}.v{v}{report_path.suffix}"))
        assert passed, f"{label} 评审 3 轮未过 → 升级老板：{issues}"
        comp = self.compliance.check(report_path.read_text(encoding="utf-8"))
        assert comp["passed"], f"{label} 合规不通过：{comp['violations']}"
        return report_path

    def _book_order(self, order: dict) -> None:
        ledger_path = self.hub.root / "orders.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else []
        ledger = [o for o in ledger if o.get("order_id") != order["order_id"]]  # 幂等：重跑不重复入账
        ledger.append(order)
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        self.hub.append("06-finance.md",
                        f"\n- {time.strftime('%Y-%m-%d')} 入账：{order['order_id']} "
                        f"¥{order['amount']}（{order['product']}）\n")

    # ---------------- 阶段 6：一页纸经营周报 ----------------
    def weekly_report(self) -> str:
        ledger_path = self.hub.root / "orders.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else []
        revenue = sum(o.get("amount", 0) for o in ledger if o.get("status") == "已交付")
        n = len(ledger)
        report = (
            f"# 经营周报（{time.strftime('%Y-%m-%d')}）\n\n"
            f"## 数字\n\n- 累计订单：{n} 单\n- 累计收入：¥{revenue}\n"
            f"- 交付产品线：标准策略回测报告\n\n"
            f"## 三条建议\n\n"
            f"1. 沉淀样例报告作为获客素材（阶段 3 钩子：免费策略体检名额）\n"
            f"2. 模拟盘跟踪订阅（产品线 C）做成第二收入曲线\n"
            f"3. 回测规范 v1 迭代：补充参数敏感性阈值的行业对照\n\n"
            f"## 下一步（闸门⑧⑨：老板决策）\n\n- [ ] 继续 / 调价 / 砍产品线\n"
        )
        self.hub.write("08-review.md", report, versioned=True)
        self.gates.checkpoint(8, "周报决策", "继续/调价/砍产品线")
        self._log("经营周报", f"{n} 单 / ¥{revenue}", "08-review.md 已生成")
        return report
