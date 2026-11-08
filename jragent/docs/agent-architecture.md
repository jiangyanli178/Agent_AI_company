# 量化策略研究工作室 · Agent 调度与功能详解

> MiMo-OPC 三层架构 · 执行层 11 个专职 Agent · 编排层 COO · 中枢层 Context Hub + 9 道闸门
> 全部留痕 · 可回滚

---

## 目录

1. [架构总览](#1-架构总览)
2. [Agent 职责详解](#2-agent-职责详解)
3. [COO 调度流程](#3-coo-调度流程)
4. [9 道人工闸门](#4-9-道人工闸门)
5. [风控审计域](#5-风控审计域)
6. [数据源降级链](#6-数据源降级链)
7. [中枢与留痕机制](#7-中枢与留痕机制)
8. [多股组合回测](#8-多股组合回测)
9. [前端 API 与支付闸门](#9-前端-api-与支付闸门)

---

## 1. 架构总览

系统采用 **MiMo-OPC 三层架构**，三条铁律贯穿始终：**单一事实来源、不可逆动作过闸、全部留痕可回滚**。

| 层级 | 组成 | 职责 |
|---|---|---|
| **执行层** | 11 个专职 Agent | 各司其职，互不直接调用，全部由 COO 调度 |
| **编排层** | COO Agent | 全流程编排：报价→建单→收款→采集→翻译→回测→审计→报告→合规→交付，管理双循环与升级机制 |
| **中枢层** | Context Hub + 9 道闸门 | 9 个卷宗单一事实来源 + 人工闸门把控不可逆动作 + 07-log.md 全量留痕 |

### 五条合规红线

| 红线 | 要求 | 说明 |
|---|---|---|
| 红线 1 | **不代客理财** | Agent 永不碰资金与交易 |
| 红线 2 | **不荐股** | 仅提供研究服务 |
| 红线 3 | **必带风险揭示** | 免责声明 + 最大回撤揭示 |
| 红线 4 | **业绩不造假** | 只用真实公开数据，降级须披露 |
| 红线 5 | **可复现** | 快照 + 规格 JSON + 重跑脚本入 09-backtest/ |

---

## 2. Agent 职责详解

所有 Agent 继承 `Agent` 基类，持有 ContextHub 引用，全部动作通过 `_log()` 留痕至 07-log.md。

### 2.1 商业前置链（报价 → 建单 → 收款）

#### 💰 报价官（Quoter）

- **职责**：定价合规校验，报价必须落在定价带内，定价表入 01-market.md
- **核心方法**：`quote(product, amount) -> dict`
- **输出**：product / amount / currency:"CNY" / terms（注明交付物为研究服务、不构成投资建议、3 个工作日）

| 产品 | 定价带（¥） |
|---|---|
| 标准策略回测报告 | 300 ~ 1000 |
| 策略体检 | 500（固定） |
| 模拟盘跟踪订阅 | 99 ~ 299 |

#### 📋 订单员（OrderClerk）

- **职责**：创建订单记录，状态标记「已建单」
- **核心方法**：`create_order(order_id, quote, client) -> dict`
- **输出**：order_id / client / product / amount / status

#### 🧾 收银员（Cashier）

- **职责**：仅生成收款账单，实际收款由老板执行/确认（闸门⑥）。**Agent 永不碰资金**
- **核心方法**：`generate_invoice(order) -> dict`
- **输出**：invoice_no:"INV-{order_id}" / amount / pay_link / note

---

### 2.2 研究执行链（数据 → 策略 → 回测 → 审计 → 报告）

#### 📊 数据收集师（DataCollector）

- **职责**：国内直连真实行情采集，四级降级链保障数据可得性；快照落盘可复现
- **核心方法**：`collect(symbol, period="2y") -> DataSnapshot`
- **输出**：snapshot_id / dates / closes / source / pool_definition / meta
- **降级链**：AKShare（东财 push2）→ BaoStock → yfinance → 合成数据。每一级失败自动降级，降级原因写入日志与快照 meta，报告「回测假设」章加合成数据警告（红线 4）

#### 🧠 策略翻译官（StrategyTranslator）

- **职责**：自然语言策略想法 → 结构化策略规格（StrategySpec），含完整解读与微调指引
- **核心方法**：`translate(idea) -> StrategySpec`
- **输出**：kind / fast / slow / stop_loss / position + interpretation 字典

| 能力 | 说明 |
|---|---|
| 规则识别 | 含「突破」→ breakout；含「均线/金叉/死叉/交叉」→ ma_cross |
| 参数解析 | 支持 `10/40`、`10x40`、`10日和40日` 等写法；fast≥slow 自动对调；缺省 10/40 |
| 止损解析 | `止损 X%` 正则；缺省 5% |
| interpretation | clarity（明确/默认计数）/ items（原文线索→解读→确定性）/ params（参数→取值→来源→如何微调）/ assumptions（满仓、成本 0.1%/边、t+1、只做多）/ to_confirm（待确认项） |

#### ⚙️ 量化工程师（QuantEngineer）

- **职责**：回测引擎调用 + 可复现资产生成（规格 JSON + 重跑脚本）
- **核心方法**：
  - `build_and_run(spec, snap, order_id, asset_prefix) -> BacktestResult`（单股）
  - `build_and_run_portfolio(spec, snaps, order_id) -> BacktestResult`（多股组合）
- **产出**：`09-backtest/<order_id>/strategy_spec.json` + `reproduce.py`（红线 5）；多股每股独立子目录

#### 🔍 执行风险审计员（ExecutionRiskAuditor）

- **审计域**：**未来函数（数据泄漏）+ 参数敏感性**
- **核心方法**：`audit(spec, snap, result) -> dict` / `audit_portfolio(...)`
- **输出**：agent / scope / verdict / checks / data / conclusion_md（完整详细结论）

#### 🔍 模型风险审计员（ModelRiskAuditor）

- **审计域**：**过拟合（样本内外）+ 幸存者偏差**
- **核心方法**：`audit(spec, snap, result) -> dict` / `audit_portfolio(...)`
- **输出**：agent / scope / verdict / checks / data / conclusion_md（完整详细结论）

#### 📝 报告设计（ReportDesigner）

- **职责**：回测结果 + 审计结论 → 9 章标准报告（含策略解读与微调指引板块）
- **核心方法**：`render(result, audit, out_dir, order_id) -> Path` / `render_portfolio(...)`
- **DoD 9 章**：摘要 / 策略解读与微调指引 / 策略规格 / 回测假设 / 回测结果 / 风险揭示 / 风控审计 / 可复现说明 / 免责声明与风险揭示

#### ✅ 评审 Agent（Reviewer）

- **职责**：报告质量评审，DoD 章节齐全性 + 指标完整性 + 审计否决拦截
- **核心方法**：`review(report_text, result, audit) -> (bool, list[str])`
- **判定**：9 章各有 `## 标题` / max_drawdown 非缺失 / audit ≠ 否决

#### ⚖️ 合规官（ComplianceOfficer）

- **职责**：合规终检，金融广告禁词扫描 + 免责声明/风险揭示齐全性，**一票否决**
- **核心方法**：`check(report_text) -> dict`
- **输出**：passed / violations / has_disclaimer / has_drawdown_risk
- **禁词 13 个**：保本 / 稳赚 / 收益保证 / 必涨 / 零风险 / 荐股 / 代客理财 / 代客下单 / 包赚 / 翻倍 / 内部消息 等

### 2.3 Agent 间调用关系

Agent 之间**互不直接调用**，全部由 COO 调度，通过共享数据结构传递：

```
DataCollector → DataSnapshot → QuantEngineer / 两审计员
StrategyTranslator → StrategySpec → QuantEngineer / 两审计员
QuantEngineer → BacktestResult → 两审计员 / ReportDesigner / Reviewer
两审计员 → 各自 audit dict → COO 用 merge_risk_audits 聚合 → ReportDesigner / Reviewer
ReportDesigner → report.md 文本 → Reviewer → ComplianceOfficer
Quoter → quote → OrderClerk → order → Cashier → invoice
```

---

## 3. COO 调度流程

COO 持有全部 10 个下属 Agent 实例（translator / collector / engineer / exec_auditor / model_auditor / designer / reviewer / compliance / quoter / order_clerk / cashier），按固定顺序调度。

### 3.1 run_first_order 九步闭环（单股）

| 步骤 | 动作 | 说明 |
|---|---|---|
| 1 | 开工留痕 | `COO.log("开工")`，记录订单参数 |
| 2 | 报价 → **闸门⑤** | `Quoter.quote` 校验定价带（300~1000 ¥）→ `GateKeeper.checkpoint(5)` 价格与条款人工审批 |
| 3 | 建单 + 收款单 → **闸门⑥** | `OrderClerk.create_order` → `Cashier.generate_invoice` → `GateKeeper.checkpoint(6)` 真实收付款 |
| 4 | 多股分支判断 | `parse_symbols(symbol)`：单股→步骤 5；多股→ `_run_multi_research` |
| 5 | 数据采集 + 策略翻译 | `DataCollector.collect`（四级降级链）→ DataSnapshot；`StrategyTranslator.translate` → StrategySpec |
| 6 | 回测 + 风控审计（≤3 轮） | `QuantEngineer.build_and_run` → 两审计员 → `merge_risk_audits`；否决→打回量化工程师；3 轮仍否决→ `AuditRejected` 升级老板 |
| 7 | 报告成稿 + 评审循环（≤3 轮） | `ReportDesigner.render` → `Reviewer.review`；不过→旧稿 rename 版本留痕后重跑；3 轮不过→升级老板 |
| 8 | 合规终检（一票否决） | `ComplianceOfficer.check`：禁词扫描 + 免责声明 + 风险揭示 |
| 9 | QC + 交付回执 + 入账 | metrics 五指标 + 09-backtest 资产齐全 → `_book_order` 幂等入账 → receipt → 05-pipeline.md |

### 3.2 循环与升级机制

| 机制 | 最大轮数 | 打回对象 | 超限处理 |
|---|---|---|---|
| 风控审计循环 | 3 轮 | 量化工程师（定向修改） | `AuditRejected` 升级老板，报告不出稿 |
| 报告评审循环 | 3 轮 | 报告设计（重写缺陷章节） | 断言失败升级老板 |
| 合规终检 | 1 次 | — | **一票否决**，不出稿 |

### 3.3 闸门调用点汇总

代码自动调用点仅 3 处：**闸门⑤**（报价审批）、**闸门⑥**（收款确认）、**闸门⑧**（周报决策）。其余闸门（①②③④⑦⑨）由老板在业务流程外手动使用。

---

## 4. 9 道人工闸门

不可逆动作必须过闸。GateKeeper 支持 `auto`（演示自动通过）/ `manual`（老板交互 y/n 拍板）两种模式，均写 07-log.md 留痕。

| # | 事项 | 说明 |
|---|---|---|
| ① | 做什么生意 | 立案方向审批 |
| ② | 定名 + 合规红项 | 品牌命名、合规红线审批 |
| ③ | 卖什么 / 承诺范围 | 产品定义 + 免责声明审批 |
| ④ | 对外公开发布 | 对外发布审批 |
| ⑤ | 价格与条款 | 报价审批 · run_first_order 自动调用 |
| ⑥ | 真实收付款 | 资金动作审批 · Agent 永不碰资金 |
| ⑦ | 投诉 / 退款 / 舆情 | 危机处理审批 |
| ⑧ | 周报决策 / 催款 | weekly_report 自动调用 |
| ⑨ | 下一步战略 | 战略方向审批 |

**GateKeeper.checkpoint(gate_no, subject, material) -> bool**：True=放行，False=打回。manual 模式需老板输入 `y/n`；auto 模式直接通过并标注「放行（演示模式自动通过）」。

---

## 5. 风控审计域

风控审计拆分为两个独立 Agent，各自产出**完整而详细的结论**（conclusion_md），由 `merge_risk_audits()` 聚合。

### 5.1 执行风险审计域（ExecutionRiskAuditor）

| 检查项 | 判定标准 | 实测指标 |
|---|---|---|
| **未来函数（数据泄漏）** `红线` | 成交至少滞后信号 1 根K线（execution_lag ≥ 1）；组合版附加「日期交集对齐、不外推」 | `execution_lag` / 对齐方式 |
| **参数敏感性** | 夏普极差 ≤ 1.5 **且** 正夏普参数组合占比 ≥ 60% | `sensitivity_spread` / `sensitivity_rows`（fast±3 × slow±5 共 9 组网格） |

### 5.2 模型风险审计域（ModelRiskAuditor）

| 检查项 | 判定标准 | 实测指标 |
|---|---|---|
| **过拟合（样本内外）** | 前 70% IS / 后 30% OOS：oos_sharpe ≥ 0.3 × is_sharpe **且** oos_total_return > -15% | `is_sharpe` / `oos_sharpe` / `oos_return` |
| **幸存者偏差** | 数据快照必须记录 `pool_definition`（股票池口径）非空 | `pool_definition` |

### 5.3 综合判定口径（`_audit_verdict`）

| 判定 | 条件 | COO 后续动作 |
|---|---|---|
| **通过** | 四项全部通过 | 继续报告成稿 |
| **有条件通过** | 恰好 1 项失败 **且** 失败项非「未来函数」 | 继续报告成稿，附条件说明 |
| **否决** | 未来函数失败，或 ≥2 项失败 | 打回量化工程师，≤3 轮，超限升级老板 |

### 5.4 审计产出结构

```python
# 单域审计 dict
{
  "agent": "执行风险审计员" | "模型风险审计员",
  "scope": "未来函数 / 参数敏感性" | "过拟合 / 幸存者偏差",
  "verdict": "通过" | "有条件通过" | "否决",
  "checks": [{"item", "passed", "detail"}, ...],
  "data": {"execution_lag", "sensitivity_spread", ...},
  "conclusion_md": "完整详细结论（判定标准/实测/判定/风险含义/微调建议）"
}

# merge_risk_audits 聚合结构
{
  "verdict": "通过" | "有条件通过" | "否决",   # 四项重判
  "checks": [...],           # 固定顺序：未来函数→过拟合→参数敏感性→幸存者偏差
  "is_sharpe", "oos_sharpe", "sensitivity_spread",
  "execution": {...},        # 执行风险完整结论
  "model": {...},            # 模型风险完整结论
  "per_stock": {...}         # 组合版附加分股明细
}
```

---

## 6. 数据源降级链

DataCollector.collect 按优先级尝试四级数据源，确保国内网络环境免代理直连可用：

| 优先级 | 数据源 | 类型 | 说明 |
|---|---|---|---|
| 1 | **AKShare**（东方财富 push2） | 国内直连 | `stock_zh_a_hist` 日线前复权，时间窗 n_days×1.6 冗余后截尾 |
| 2 | **BaoStock**（证券宝） | 国内直连备用 | `query_history_k_data_plus` 前复权，sh./sz. 前缀映射，应对东财限流 |
| 3 | **yfinance**（Yahoo Finance） | 海外备用 | auto_adjust=True，需海外网络 |
| 4 | **合成数据**（synthetic_ohlcv） | 兜底·必须披露 | 种子化 GBM + 三段趋势注入，降级原因写入 log 与快照 meta，报告加警告（红线 4） |

### DataSnapshot 快照结构

```python
@dataclass
class DataSnapshot:
    snapshot_id: str      # SNAP-{sha1(symbol+首日+末日+末收)[:8]}
    symbol: str
    dates: list[str]
    closes: list[float]
    source: str           # "akshare:600519（真实公开数据·东方财富源·国内直连）"
    pool_definition: str  # 股票池口径（幸存者偏差检查用）
    meta: dict
```

快照落盘 `09-backtest/_snapshots/SNAP-*.csv + .meta.json`，配合 reproduce.py 可复现（红线 5）。

---

## 7. 中枢与留痕机制

Context Hub 是**单一事实来源**：9 个卷宗 + 回测资产库 + 订单台账 + 全量日志。所有 Agent 只读写 Context Hub，全部动作留痕可回滚。

### 7.1 卷宗清单

| 卷宗 | 内容 |
|---|---|
| `00-charter.md` | 公司宪章：定位、客户、价值主张、合规红线 |
| `01-market.md` | 调研：目标人群、竞品、定价带（PRICE_TABLE 来源） |
| `02-brand.md` | 命名、VI、语调、金融广告禁词表 |
| `03-product.md` | 报告模板、回测规范、验收标准（DoD 9 章定义） |
| `04-ops.md` | SOP、话术库、FAQ、权限表、免责声明全文 |
| `05-pipeline.md` | 内容台账、线索台账、**订单台账**（receipt 留痕） |
| `06-finance.md` | 票据、收支、毛利、现金流（_book_order 入账留痕） |
| `07-log.md` | **全量动作日志**（所有 Agent + 闸门留痕） |
| `08-review.md` | 周报、复盘、模拟盘跟踪记录、决策记录（versioned） |
| `09-backtest/` | 回测资产库：快照 CSV / strategy_spec.json / reproduce.py |
| `orders.json` | 订单台账（_book_order 幂等维护） |
| `payments.json` | 支付记录（webapp 维护：pay_id / paid_at / success / status） |

### 7.2 关键机制

- **版本留痕**：`write(name, content, versioned=True)` 旧稿 rename 为 `*.v{n}.md` 顺延
- **回滚机制**：`mark_stale(name)` → 07-log.md 记 `[STALE]` + 文件 rename 为 `*.stale.*`
- **幂等入账**：`_book_order` 先剔除同 order_id 旧记录再 append，绝不重复入账
- **日志格式**：`## 时间 | Agent | 动作` + 输入/输出/工具调用/截图索引

---

## 8. 多股组合回测

当标的为多只股票时，COO 转入 `_run_multi_research` 分支：**每股独立闭环 + 等权组合闭环**，产出 N+1 份报告。

### 8.1 分支流程

1. **策略翻译一次**（全标的共用同一 StrategySpec）
2. **每股独立闭环**（N 份个股报告）：collect → build_and_run → 风控审计 ≤3 轮 → 报告成稿 → 评审 ≤3 轮 → 合规终检；每股独立子目录 `09-backtest/<order_id>/<safe>/`
3. **等权组合闭环**（1 份组合报告）：日期交集对齐（≥60 天）→ 等权按日再平衡 → `run_portfolio_backtest` → 组合审计（含 per_stock 明细）→ portfolio_report.md
4. **组合 QC + 入账**：每股 strategy_spec.json + reproduce.py 齐备 + 组合 portfolio_strategy_spec.json + reproduce_portfolio.py + 五指标齐全

### 8.2 组合回测口径

- **日期对齐**：日期交集保序，不足 `MIN_ALIGNED_DAYS=60` 抛 ValueError，绝不外推缺失日
- **等权再平衡**：组合日收益 = 各股策略日收益等权平均（按日再平衡简化口径）
- **指标携带**：`per_stock`（对齐窗口口径分股明细）、`weighting`、`aligned_days`

---

## 9. 前端 API 与支付闸门

纯标准库 HTTP 服务（ThreadingHTTPServer，127.0.0.1:8000），6 视图前端 + 模拟微信扫码支付闸门。

### 9.1 API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/state` | GET | 订单台账 + 支付记录 + 下一订单号 |
| `/api/checkout` | POST | 生成收款单（PAY-xxx），落库 payments.json |
| `/api/pay_confirm` | POST | 确认扫码支付：记录 paid_at / success / status + 闸门⑥留痕 |
| `/api/run` | POST | **支付闸门**：paid && success 才开始回测（先付款后出结果） |
| `/api/report` | GET | 读取指定订单/股票的报告 Markdown |
| `/api/chart` | GET | 返回回测图表 PNG |
| `/api/log` | GET | 读取 07-log.md 全量日志 |
| `/api/weekly` | POST | 生成周报（闸门⑧） |
| `/api/check_scope` | POST | 越界请求拦截（代客理财/荐股/收益承诺） |

### 9.2 支付闸门流程

1. **生成收款单**：`POST /api/checkout` → `{pay_id, order_id(预分配), amount, created_at, status:"待支付", paid:false}` → payments.json
2. **模拟扫码支付**：`POST /api/pay_confirm` → 记录 `paid_at`(支付时间) / `success`(是否成功) / `status`(支付成功|支付失败) + 闸门⑥留痕
3. **支付校验 → 开始履约**：`POST /api/run {pay_id}` → 校验 `paid && success`（否则 402 拒绝）→ 执行 `run_first_order` → 回填 `fulfilled_order_id` + `status:"已履约"`

支付信息全部落库 `context_hub/payments.json`：每笔含 **pay_id / order_id / created_at / paid / paid_at / success / status / fulfilled_order_id**，闸门⑥同步写入 07-log.md 留痕。

### 9.3 前端 6 视图

| 视图 | 路由 | 功能 |
|---|---|---|
| 工作台 | `#/work` | KPI 统计卡 + 订单台账 + 支付记录 |
| 下单 | `#/order` | 订单信息 → 收款单 → 模拟扫码 → 开始履约 |
| 履约进度 | `#/progress` | 8 阶段实时进度动画 |
| 研究报告 | `#/report` | 报告 tab 切换（个股/组合/审计详报），Markdown 渲染 + 图表 |
| 合规工具 | `#/compliance` | 越界检测 + 周报生成 |
| 留痕审计 | `#/log` | 07-log.md 全量日志实时查看 |

---

> 量化策略研究工作室 · MiMo-OPC 三层架构 · Agent 调度与功能详解 · 生成于 2026-11-07
