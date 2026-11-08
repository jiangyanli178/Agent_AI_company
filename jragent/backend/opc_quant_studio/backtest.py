"""回测引擎 + 风控审计（核心产线）。

- run_backtest：事件循环式回测，支持均线交叉/突破两类规则 + 止损
  **执行滞后 ≥ 1 根K线**（t 日信号 t+1 日成交）——未来函数在引擎层杜绝
- 风控审计拆为两个专职审计域（各自产出完整详细结论 conclusion_md）：
  · 执行风险审计（未来函数 / 参数敏感性）：execution_risk_audit[_portfolio]
  · 模型风险审计（过拟合 / 幸存者偏差）：model_risk_audit[_portfolio]
  merge_risk_audits 聚合两份结论；risk_audit / portfolio_risk_audit 为兼容入口
- run_portfolio_backtest：多股票组合回测（日期对齐 + 等权、按日再平衡简化口径），
  同一策略作用于多只股票，每股独立回测后合成组合净值，metrics 携带 per_stock 明细
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from types import SimpleNamespace

from .data_manager import DataSnapshot

TRADING_DAYS = 252
COST_RATE = 0.001  # 手续费 + 滑点（单边）


# ---------------- 策略规格（策略翻译官的结构化输出） ----------------
@dataclass
class StrategySpec:
    name: str
    kind: str = "ma_cross"          # ma_cross | breakout
    fast: int = 10
    slow: int = 30
    stop_loss: float = 0.05
    position: float = 1.0
    entry_rule: str = ""
    exit_rule: str = ""
    raw_idea: str = ""
    # 策略解读：用户描述多为不清晰的自然语言，翻译时记录逐条解读/假设/待确认点，
    # 供报告「策略解读与微调指引」章节展示，便于用户微调修改
    interpretation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------- 回测结果 ----------------
@dataclass
class BacktestResult:
    spec: dict
    metrics: dict
    equity_curve: list[float]
    dates: list[str]
    trades: list[dict]
    execution_lag: int = 1
    snapshot_id: str = ""
    data_source: str = ""
    cost_rate: float = COST_RATE

    def summary(self) -> dict:
        return {
            "spec": self.spec,
            "metrics": self.metrics,
            "n_trades": len(self.trades),
            "execution_lag": self.execution_lag,
            "snapshot_id": self.snapshot_id,
            "cost_rate": self.cost_rate,
        }


# ---------------- 引擎 ----------------
def _rolling_mean(vals: list[float], w: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    acc = 0.0
    for i, v in enumerate(vals):
        acc += v
        if i >= w:
            acc -= vals[i - w]
        if i >= w - 1:
            out[i] = acc / w
    return out


def _signal(spec: StrategySpec, closes: list[float]) -> list[int | None]:
    """t 日收盘后可得的信号。1=看多，0=空仓。"""
    n = len(closes)
    if spec.kind == "breakout":
        sig: list[int | None] = [None] * n
        for i in range(spec.slow, n):
            window = closes[i - spec.slow:i]
            sig[i] = 1 if closes[i] > max(window) else 0
        return sig
    ma_f = _rolling_mean(closes, spec.fast)
    ma_s = _rolling_mean(closes, spec.slow)
    sig = [None] * n
    for i in range(n):
        if ma_f[i] is not None and ma_s[i] is not None:
            sig[i] = 1 if ma_f[i] > ma_s[i] else 0
    return sig


def run_backtest(spec: StrategySpec, snap: DataSnapshot) -> BacktestResult:
    closes, dates = snap.closes, snap.dates
    n = len(closes)
    sig = _signal(spec, closes)

    equity = 1.0
    curve = [equity]
    pos = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    traded_fraction = 0.0  # 仓位变动累计（换手口径，按权益比例计）

    for i in range(1, n):
        ret = closes[i] / closes[i - 1] - 1
        equity *= 1 + pos * ret

        # 止损：跌破入场价 ×(1-止损线) 即离场（按收盘近似）
        if pos > 0 and closes[i] <= entry_price * (1 - spec.stop_loss):
            pnl = closes[i] / entry_price - 1 - 2 * COST_RATE
            trades.append({"entry": entry_price, "exit": closes[i], "pnl": pnl, "reason": "stop_loss"})
            traded_fraction += pos
            equity *= 1 - COST_RATE
            pos = 0.0

        # 信号在 i-1 日收盘可得，i 日执行（execution_lag = 1，杜绝未来函数）
        s = sig[i - 1]
        if s is not None:
            want = spec.position if s == 1 else 0.0
            if want != pos:
                traded_fraction += abs(want - pos)
                equity *= 1 - COST_RATE * abs(want - pos)  # 简化成本口径：按仓位变动比例扣费
                if want > 0 and pos == 0:
                    entry_price = closes[i] * (1 + COST_RATE)
                if want == 0 and pos > 0:
                    pnl = closes[i] / entry_price - 1 - 2 * COST_RATE
                    trades.append({"entry": entry_price, "exit": closes[i], "pnl": pnl, "reason": "signal"})
                pos = want
        curve.append(equity)

    total_return = equity - 1
    n_days = n - 1
    annualized = (1 + total_return) ** (TRADING_DAYS / n_days) - 1 if n_days > 0 else 0.0

    # 最大回撤
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)

    # 日收益序列 → 夏普
    daily_rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    mean = sum(daily_rets) / len(daily_rets)
    var = sum((r - mean) ** 2 for r in daily_rets) / max(len(daily_rets) - 1, 1)
    std = var ** 0.5
    sharpe = (mean / std * TRADING_DAYS ** 0.5) if std > 0 else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    years = n_days / TRADING_DAYS
    turnover = traded_fraction / years if years > 0 else 0.0  # 年化换手（仓位变动比例）

    metrics = {
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 3),
        "win_rate": round(win_rate, 3),
        "n_trades": len(trades),
        "turnover": round(turnover, 2),
    }
    return BacktestResult(
        spec=spec.to_dict(), metrics=metrics, equity_curve=curve,
        dates=dates, trades=trades, snapshot_id=snap.snapshot_id,
        data_source=snap.source,
    )


# ---------------- 风控审计（两个专职审计域，各出完整详细结论） ----------------
#
# 按 MiMo-OPC「专职 Agent」拆分：
#   - 执行风险审计（未来函数 + 参数敏感性）→ ExecutionRiskAuditor
#   - 模型风险审计（过拟合 + 幸存者偏差）→ ModelRiskAuditor
# 每份审计产出 checks + data + conclusion_md（完整详细结论：判定标准 / 实测数据 /
# 风险含义 / 微调建议）。merge_risk_audits 聚合为下游兼容结构（verdict/checks/
# is_sharpe/oos_sharpe/sensitivity_spread），report.py 与 webapp 前端依赖这些键。


def _audit_verdict(checks: list[dict]) -> str:
    """判定口径：全部通过=通过；单项失败（且非未来函数）=有条件通过；其余=否决。"""
    failed = [c for c in checks if not c["passed"]]
    if not failed:
        return "通过"
    return "有条件通过" if len(failed) == 1 and failed[0]["item"] != "未来函数" else "否决"


def _sensitivity_grid(spec: StrategySpec, run_fn) -> list[dict]:
    """快慢线网格扰动（fast±3 × slow±5，剔除非法组合），返回逐组夏普。"""
    rows = []
    for df in (-3, 0, 3):
        for ds in (-5, 0, 5):
            f, s = spec.fast + df, spec.slow + ds
            if f < 2 or s <= f:
                continue
            sub = StrategySpec(**{**spec.to_dict(), "fast": f, "slow": s})
            rows.append({"fast": f, "slow": s, "sharpe": run_fn(sub).metrics["sharpe"]})
    return rows


def _sensitivity_passed(rows: list[dict]) -> tuple[bool, float]:
    spread = (max(r["sharpe"] for r in rows) - min(r["sharpe"] for r in rows)) if rows else 0.0
    ok = spread <= 1.5 and sum(1 for r in rows if r["sharpe"] > 0) >= len(rows) * 0.6
    return ok, spread


def _future_md(lag: int, passed: bool, multi: bool) -> str:
    align = ("多标的一律按日期交集对齐（不外推任何缺失日），组合与个股信号同样错开一日执行。"
             if multi else "")
    return (
        "#### 1. 未来函数（数据泄漏）\n\n"
        f"- **判定标准**：成交至少滞后信号 1 根K线——t 日收盘后得到的信号最早 t+1 日成交，"
        f"指标计算只使用截至当日已知的数据。{align}\n"
        f"- **实测**：回测引擎执行滞后 = {lag} 根K线（事件循环中 `sig[i-1]` 于第 i 日成交），"
        "信号与成交严格错开。\n"
        f"- **判定**：{'✅ 通过' if passed else '❌ 不通过'}。\n"
        "- **风险含义**：若按当日收盘价成交当日信号，等于让资金「预知」当日走势，"
        "回测收益被系统性虚增且实盘不可复制。本项是审计红线：不通过即否决。\n"
    )


def _sensitivity_md(spec: StrategySpec, rows: list[dict], spread: float,
                    passed: bool, multi: bool) -> str:
    best = max(rows, key=lambda r: r["sharpe"]) if rows else None
    worst = min(rows, key=lambda r: r["sharpe"]) if rows else None
    n_pos = sum(1 for r in rows if r["sharpe"] > 0)
    ratio = n_pos / len(rows) if rows else 0.0
    grid_lines = "\n".join(
        f"  - 快线 {r['fast']} / 慢线 {r['slow']} → 夏普 {r['sharpe']}" for r in rows)
    best_txt = f"快线 {best['fast']}/慢线 {best['slow']} 夏普 {best['sharpe']}" if best else "无"
    worst_txt = f"快线 {worst['fast']}/慢线 {worst['slow']} 夏普 {worst['sharpe']}" if worst else "无"
    scope = "组合" if multi else ""
    return (
        "#### 2. 参数敏感性\n\n"
        f"- **判定标准**：在快线 {spec.fast}±3、慢线 {spec.slow}±5 的网格上逐一重跑回测，"
        f"{scope}夏普极差 ≤ 1.5，且正夏普参数组合占比 ≥ 60%。\n"
        f"- **实测**（{len(rows)} 组有效参数）：最优 {best_txt}；最差 {worst_txt}；"
        f"夏普极差 **{spread:.2f}**；正夏普 {n_pos}/{len(rows)}（{ratio:.0%}）。\n"
        f"- **逐组结果**：\n{grid_lines}\n"
        f"- **判定**：{'✅ 通过' if passed else '❌ 不通过'}（极差 ≤ 1.5 且正夏普占比 ≥ 60%）。\n"
        "- **风险含义**：极差过大说明收益集中在单一参数点（「参数悬崖」），参数轻微漂移策略即失效，"
        "也提示参数是为历史数据量身挑选的（数据挖掘偏差），真实前向表现大概率衰减。\n"
        "- **微调建议**：优先在上表表现稳定的参数邻域内微调，不要追逐网格内的单点最优；"
        "若极差过大，可简化规则、拉长回测样本或改用更稳健的参数区间后重新审计。\n"
    )


def _overfit_md(s_is: float, s_oos: float, oos_ret: float,
                is_days: int, oos_days: int, passed: bool, multi: bool) -> str:
    ratio = f"{s_oos / s_is:.0%}" if s_is else "不适用（样本内夏普为 0）"
    scope = "组合" if multi else ""
    return (
        "#### 1. 过拟合（样本内外对比）\n\n"
        f"- **判定标准**：按时间切分样本内（前 70%，{is_days} 个交易日）与样本外"
        f"（后 30%，{oos_days} 个交易日），同一参数分别回测；样本外夏普 ≥ 样本内夏普的 30%，"
        "且样本外总收益 > -15%。\n"
        f"- **实测**：{scope}样本内夏普 **{s_is}**，样本外夏普 **{s_oos}**"
        f"（样本外/样本内 = {ratio}）；样本外总收益 {oos_ret:.2%}。\n"
        f"- **判定**：{'✅ 通过' if passed else '❌ 不通过'}。\n"
        "- **风险含义**：样本外表现大幅衰减（甚至转亏）说明规则更可能拟合了历史噪声而非可重复的市场规律，"
        "实盘沿用该参数的期望收益应显著低于回测值。\n"
        "- **微调建议**：简化规则与参数数量、扩大回测样本、做滚动样本外验证（Walk-Forward），"
        "并以样本外表现而非全样本表现为准选择参数。\n"
    )


def _survivor_md(pool_text: str, passed: bool, multi: bool) -> str:
    scope = "全部标的" if multi else "标的"
    return (
        "#### 2. 幸存者偏差\n\n"
        f"- **判定标准**：数据快照必须记录股票池口径（选样范围与纳入/剔除规则），"
        f"{scope}的回测对象可追溯到选样规则。\n"
        f"- **实测**：股票池口径 = {pool_text}。\n"
        f"- **判定**：{'✅ 通过' if passed else '❌ 不通过'}。\n"
        "- **风险含义**：若股票池只保留存活至今的标的（退市、暴雷、长期停牌股被剔除），"
        "历史收益会被系统性高估，回测结果无法代表当时的可选范围。\n"
        "- **微调建议**：使用含退市标的的全历史成分数据，或明确记录指数/股票池的历史成分口径后重新审计。\n"
    )


def _exec_summary(verdict: str, lag_ok: bool, sens_ok: bool) -> str:
    parts = []
    if lag_ok and sens_ok:
        parts.append("两项检查均通过：执行口径无未来函数，且收益对参数扰动不敏感，属可继续推进的稳健形态。")
    if not lag_ok:
        parts.append("未来函数不通过属红线问题：当前回测收益不可信，"
                     "必须把成交严格推迟到信号次日（或更晚）后重新回测。")
    if not sens_ok:
        parts.append("参数敏感性不通过：收益过度依赖个别参数点，"
                     "建议在稳定参数邻域微调，或简化规则/扩大样本后重新审计。")
    return f"**综合判断（{verdict}）**：\n" + "\n".join(f"- {p}" for p in parts)


def _model_summary(verdict: str, overfit_ok: bool, pool_ok: bool) -> str:
    parts = []
    if overfit_ok and pool_ok:
        parts.append("两项检查均通过：样本外表现与样本内可比，且选样口径可追溯，"
                     "未见明显过拟合与幸存者偏差。")
    if not overfit_ok:
        parts.append("过拟合风险偏高：样本外表现不满足留存标准，回测收益对历史区间敏感，"
                     "实盘预期应大幅下调，并优先做滚动样本外验证。")
    if not pool_ok:
        parts.append("股票池口径缺失：无法排除幸存者偏差，收益可能被系统性高估，"
                     "补齐选样口径后需重新审计。")
    return f"**综合判断（{verdict}）**：\n" + "\n".join(f"- {p}" for p in parts)


# ---- 执行风险审计域（未来函数 + 参数敏感性） ----
def execution_risk_audit(spec: StrategySpec, snap: DataSnapshot,
                         result: BacktestResult) -> dict:
    """执行风险审计（单股）：未来函数 + 参数敏感性，产出完整详细结论。"""
    lag_ok = result.execution_lag >= 1
    c1 = {"item": "未来函数", "passed": lag_ok,
          "detail": f"执行滞后={result.execution_lag}（t 日信号 t+1 日成交）"}

    rows = _sensitivity_grid(spec, lambda sub: run_backtest(sub, snap))
    sens_ok, spread = _sensitivity_passed(rows)
    c2 = {"item": "参数敏感性", "passed": sens_ok,
          "detail": f"网格扰动夏普极差={spread:.2f}（{len(rows)} 组）"}

    checks = [c1, c2]
    verdict = _audit_verdict(checks)
    conclusion_md = (
        f"**审计范围**：未来函数（数据泄漏）与参数敏感性。\n\n"
        f"**审计对象**：策略「{spec.name}」（{spec.kind} {spec.fast}/{spec.slow}）"
        f"× 数据快照 {snap.snapshot_id}。\n\n"
        + _future_md(result.execution_lag, lag_ok, False) + "\n"
        + _sensitivity_md(spec, rows, spread, sens_ok, False) + "\n"
        + _exec_summary(verdict, lag_ok, sens_ok)
    )
    return {"agent": "执行风险审计员", "scope": "未来函数 / 参数敏感性",
            "verdict": verdict, "checks": checks,
            "data": {"execution_lag": result.execution_lag,
                     "sensitivity_rows": rows,
                     "sensitivity_spread": round(spread, 3)},
            "conclusion_md": conclusion_md}


def execution_risk_audit_portfolio(spec: StrategySpec, snaps: list,
                                   result: BacktestResult) -> dict:
    """执行风险审计（组合级）：未来函数 + 参数敏感性，产出完整详细结论。"""
    lag_ok = result.execution_lag >= 1
    c1 = {"item": "未来函数", "passed": lag_ok,
          "detail": f"执行滞后={result.execution_lag}（t 日信号 t+1 日成交），"
                    "多标的按日期交集对齐，无缺失日外推"}

    rows = _sensitivity_grid(spec, lambda sub: run_portfolio_backtest(sub, snaps))
    sens_ok, spread = _sensitivity_passed(rows)
    c2 = {"item": "参数敏感性", "passed": sens_ok,
          "detail": f"组合网格扰动夏普极差={spread:.2f}（{len(rows)} 组）"}

    checks = [c1, c2]
    verdict = _audit_verdict(checks)
    conclusion_md = (
        f"**审计范围**：未来函数（数据泄漏）与参数敏感性（组合级）。\n\n"
        f"**审计对象**：策略「{spec.name}」（{spec.kind} {spec.fast}/{spec.slow}）"
        f"× {len(snaps)} 只标的组合（快照 {result.snapshot_id}）。\n\n"
        + _future_md(result.execution_lag, lag_ok, True) + "\n"
        + _sensitivity_md(spec, rows, spread, sens_ok, True) + "\n"
        + _exec_summary(verdict, lag_ok, sens_ok)
    )
    return {"agent": "执行风险审计员", "scope": "未来函数 / 参数敏感性",
            "verdict": verdict, "checks": checks,
            "data": {"execution_lag": result.execution_lag,
                     "sensitivity_rows": rows,
                     "sensitivity_spread": round(spread, 3)},
            "conclusion_md": conclusion_md}


# ---- 模型风险审计域（过拟合 + 幸存者偏差） ----
def model_risk_audit(spec: StrategySpec, snap: DataSnapshot,
                     result: BacktestResult) -> dict:
    """模型风险审计（单股）：过拟合 + 幸存者偏差，产出完整详细结论。"""
    # 1. 过拟合：样本内(前 70%) vs 样本外(后 30%) 夏普对比
    n = len(snap.closes)
    cut = int(n * 0.7)
    is_snap = SimpleNamespace(dates=snap.dates[:cut], closes=snap.closes[:cut],
                              snapshot_id=snap.snapshot_id + "-IS", source=snap.source)
    oos_snap = SimpleNamespace(dates=snap.dates[cut:], closes=snap.closes[cut:],
                               snapshot_id=snap.snapshot_id + "-OOS", source=snap.source)
    is_res = run_backtest(spec, is_snap)  # type: ignore[arg-type]
    oos_res = run_backtest(spec, oos_snap)  # type: ignore[arg-type]
    s_is, s_oos = is_res.metrics["sharpe"], oos_res.metrics["sharpe"]
    oos_ret = oos_res.metrics["total_return"]
    # 样本外夏普不低于样本内的 30%（且不深亏）→ 通过
    overfit_ok = (s_oos >= 0.3 * s_is) and oos_ret > -0.15
    c1 = {"item": "过拟合（样本内外）", "passed": overfit_ok,
          "detail": f"样本内夏普={s_is}，样本外夏普={s_oos}"}

    # 2. 幸存者偏差：数据快照必须带股票池口径
    pool_text = snap.pool_definition
    pool_ok = bool(pool_text)
    c2 = {"item": "幸存者偏差", "passed": pool_ok,
          "detail": f"股票池口径：{pool_text}"}

    checks = [c1, c2]
    verdict = _audit_verdict(checks)
    conclusion_md = (
        f"**审计范围**：过拟合（样本内外对比）与幸存者偏差。\n\n"
        f"**审计对象**：策略「{spec.name}」（{spec.kind} {spec.fast}/{spec.slow}）"
        f"× 数据快照 {snap.snapshot_id}。\n\n"
        + _overfit_md(s_is, s_oos, oos_ret, cut, n - cut, overfit_ok, False) + "\n"
        + _survivor_md(pool_text or "（未记录）", pool_ok, False) + "\n"
        + _model_summary(verdict, overfit_ok, pool_ok)
    )
    return {"agent": "模型风险审计员", "scope": "过拟合 / 幸存者偏差",
            "verdict": verdict, "checks": checks,
            "data": {"is_sharpe": s_is, "oos_sharpe": s_oos,
                     "oos_return": round(oos_ret, 4),
                     "is_days": cut, "oos_days": n - cut,
                     "pool_definition": pool_text},
            "conclusion_md": conclusion_md}


def model_risk_audit_portfolio(spec: StrategySpec, snaps: list,
                               result: BacktestResult) -> dict:
    """模型风险审计（组合级）：过拟合 + 幸存者偏差，产出完整详细结论。"""
    # 1. 过拟合：对齐窗口前 70% vs 后 30%
    dates, aligned = _align_snaps(snaps)
    n = len(dates)
    cut = int(n * 0.7)
    is_snaps, oos_snaps = [], []
    for s in snaps:
        key = getattr(s, "symbol", "") or s.snapshot_id
        closes = aligned[key]
        is_snaps.append(SimpleNamespace(
            dates=dates[:cut], closes=closes[:cut],
            snapshot_id=getattr(s, "snapshot_id", key) + "-IS",
            source=getattr(s, "source", ""), symbol=key,
            pool_definition=getattr(s, "pool_definition", "")))
        oos_snaps.append(SimpleNamespace(
            dates=dates[cut:], closes=closes[cut:],
            snapshot_id=getattr(s, "snapshot_id", key) + "-OOS",
            source=getattr(s, "source", ""), symbol=key,
            pool_definition=getattr(s, "pool_definition", "")))
    try:
        is_res = run_portfolio_backtest(spec, is_snaps)   # type: ignore[arg-type]
        oos_res = run_portfolio_backtest(spec, oos_snaps)  # type: ignore[arg-type]
    except ValueError as e:  # 样本内/外段对齐天数不足等
        s_is = s_oos = 0.0
        oos_ret = 0.0
        overfit_ok = False
        c1 = {"item": "过拟合（样本内外）", "passed": False,
              "detail": f"无法完成样本内外对比：{e}"}
    else:
        s_is, s_oos = is_res.metrics["sharpe"], oos_res.metrics["sharpe"]
        oos_ret = oos_res.metrics["total_return"]
        overfit_ok = (s_oos >= 0.3 * s_is) and oos_ret > -0.15
        c1 = {"item": "过拟合（样本内外）", "passed": overfit_ok,
              "detail": f"组合样本内夏普={s_is}，组合样本外夏普={s_oos}"}

    # 2. 幸存者偏差：全部标的快照都必须带股票池口径
    missing = [getattr(s, "symbol", s.snapshot_id) for s in snaps
               if not getattr(s, "pool_definition", "")]
    pool_ok = not missing
    pool_text = "全部标的股票池口径齐备" if pool_ok else f"缺失股票池口径：{missing}"
    c2 = {"item": "幸存者偏差", "passed": pool_ok, "detail": pool_text}

    checks = [c1, c2]
    verdict = _audit_verdict(checks)
    conclusion_md = (
        f"**审计范围**：过拟合（样本内外对比）与幸存者偏差（组合级）。\n\n"
        f"**审计对象**：策略「{spec.name}」（{spec.kind} {spec.fast}/{spec.slow}）"
        f"× {len(snaps)} 只标的组合（快照 {result.snapshot_id}）。\n\n"
        + _overfit_md(s_is, s_oos, oos_ret, cut, n - cut, overfit_ok, True) + "\n"
        + _survivor_md(pool_text, pool_ok, True) + "\n"
        + _model_summary(verdict, overfit_ok, pool_ok)
    )
    return {"agent": "模型风险审计员", "scope": "过拟合 / 幸存者偏差",
            "verdict": verdict, "checks": checks,
            "data": {"is_sharpe": s_is, "oos_sharpe": s_oos,
                     "oos_return": round(oos_ret, 4),
                     "is_days": cut, "oos_days": n - cut,
                     "pool_definition": pool_text},
            "conclusion_md": conclusion_md}


# ---- 聚合（兼容下游 report.py / webapp 的键结构） ----
def merge_risk_audits(exec_report: dict, model_report: dict,
                      per_stock: dict | None = None) -> dict:
    """两份审计聚合：判定口径不变，各自完整详细结论随 execution/model 键保留。"""
    by_item = {c["item"]: c for c in exec_report["checks"] + model_report["checks"]}
    order = ["未来函数", "过拟合（样本内外）", "参数敏感性", "幸存者偏差"]
    checks = [by_item[k] for k in order if k in by_item]
    out = {
        "verdict": _audit_verdict(checks),
        "checks": checks,
        "is_sharpe": model_report["data"].get("is_sharpe", 0.0),
        "oos_sharpe": model_report["data"].get("oos_sharpe", 0.0),
        "sensitivity_spread": exec_report["data"].get("sensitivity_spread", 0.0),
        "execution": exec_report,
        "model": model_report,
    }
    if per_stock is not None:
        out["per_stock"] = per_stock
    return out


def risk_audit(spec: StrategySpec, snap: DataSnapshot,
               result: BacktestResult) -> dict:
    """兼容聚合入口 = 执行风险审计 + 模型风险审计（单股）。"""
    return merge_risk_audits(execution_risk_audit(spec, snap, result),
                             model_risk_audit(spec, snap, result))


# ---------------- 组合回测（同一策略 × 多只股票） ----------------
MIN_ALIGNED_DAYS = 60  # 对齐后公共交易日下限，不足则拒绝回测


def _align_snaps(snaps: list) -> tuple[list[str], dict[str, list[float]]]:
    """多序列按日期交集对齐（保留时间顺序），返回公共日期与各标的对齐后收盘序列。"""
    if len(snaps) < 2:
        raise ValueError("组合回测至少需要 2 只股票")
    common = set(snaps[0].dates)
    for s in snaps[1:]:
        common &= set(s.dates)
    dates = [d for d in snaps[0].dates if d in common]
    if len(dates) < MIN_ALIGNED_DAYS:
        raise ValueError(
            f"多标的对齐后公共交易日仅 {len(dates)} 天（<{MIN_ALIGNED_DAYS}），"
            "请检查各股票数据区间是否重叠"
        )
    aligned: dict[str, list[float]] = {}
    for s in snaps:
        by_date = dict(zip(s.dates, s.closes))
        key = getattr(s, "symbol", "") or s.snapshot_id
        aligned[key] = [by_date[d] for d in dates]
    return dates, aligned


def _metrics_from_curve(curve: list[float], trades: list[dict],
                        traded_fraction: float) -> dict:
    """由净值曲线 + 成交记录计算通用指标（组合与个股共用口径）。"""
    total_return = curve[-1] - 1
    n_days = len(curve) - 1
    annualized = (1 + total_return) ** (TRADING_DAYS / n_days) - 1 if n_days > 0 else 0.0

    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)

    daily_rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    mean = sum(daily_rets) / len(daily_rets) if daily_rets else 0.0
    var = sum((r - mean) ** 2 for r in daily_rets) / max(len(daily_rets) - 1, 1)
    std = var ** 0.5
    sharpe = (mean / std * TRADING_DAYS ** 0.5) if std > 0 else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    years = n_days / TRADING_DAYS
    turnover = traded_fraction / years if years > 0 else 0.0
    return {
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 3),
        "win_rate": round(win_rate, 3),
        "n_trades": len(trades),
        "turnover": round(turnover, 2),
    }


def run_portfolio_backtest(spec: StrategySpec, snaps: list) -> BacktestResult:
    """同一策略作用于多只股票的组合回测。

    口径（报告须披露）：
    - 各股票按**日期交集**对齐（不使用任何缺失日的外推，杜绝未来函数）；
    - 每股独立跑 run_backtest（含各自止损/成本/执行滞后），组合日收益 =
      各股策略日收益**等权平均**（隐含按日再平衡的简化口径）；
    - per_stock 明细在**对齐窗口**内计算，保证与组合指标可比。
    """
    dates, aligned = _align_snaps(snaps)

    per_stock: dict[str, dict] = {}
    daily_rets_by_sym: dict[str, list[float]] = {}
    all_trades: list[dict] = []
    for s in snaps:
        key = getattr(s, "symbol", "") or s.snapshot_id
        closes = aligned[key]
        sub = SimpleNamespace(  # 对齐后的子快照（duck-typing DataSnapshot）
            dates=dates, closes=closes,
            snapshot_id=getattr(s, "snapshot_id", key),
            source=getattr(s, "source", ""),
        )
        res = run_backtest(spec, sub)  # type: ignore[arg-type]
        per_stock[key] = {**res.metrics, "snapshot_id": sub.snapshot_id,
                          "data_source": sub.source}
        curve = res.equity_curve
        daily_rets_by_sym[key] = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
        for t in res.trades:
            all_trades.append({**t, "symbol": key})

    # 组合净值：等权平均日收益 → 累乘
    n = len(dates)
    port_curve = [1.0]
    for i in range(1, n):
        r = sum(daily_rets_by_sym[k][i - 1] for k in daily_rets_by_sym) / len(snaps)
        port_curve.append(port_curve[-1] * (1 + r))

    port_traded = sum(
        v["turnover"] for v in per_stock.values()
    ) / len(snaps)  # 等权口径：组合换手取各股换手均值（年化）
    metrics = _metrics_from_curve(port_curve, all_trades, 0.0)
    metrics["turnover"] = round(port_traded, 2) if port_traded else metrics["turnover"]
    metrics["per_stock"] = per_stock
    metrics["weighting"] = "等权（按日再平衡简化口径）"
    metrics["aligned_days"] = n

    return BacktestResult(
        spec=spec.to_dict(), metrics=metrics, equity_curve=port_curve,
        dates=dates, trades=all_trades,
        snapshot_id=",".join(getattr(s, "snapshot_id", "") for s in snaps),
        data_source=",".join(sorted({getattr(s, "source", "") for s in snaps})),
    )


def portfolio_risk_audit(spec: StrategySpec, snaps: list,
                         result: BacktestResult) -> dict:
    """兼容聚合入口 = 执行风险审计 + 模型风险审计（组合级，与单股同口径）。"""
    return merge_risk_audits(execution_risk_audit_portfolio(spec, snaps, result),
                             model_risk_audit_portfolio(spec, snaps, result),
                             per_stock=result.metrics.get("per_stock", {}))
