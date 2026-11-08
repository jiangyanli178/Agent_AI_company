# 07 · 全量动作日志

> 本卷宗由系统自动累积，记录 Agent 动作、交易与回测，用于留痕与回滚。

> 导出时间：2026-11-07 00:14:05

## 一、Agent 运行日志（最近 30 条）

| 时间 | Agent | 任务 | 状态 | 重试 | 摘要 |
|---|---|---|---|---|---|
| 2026-11-06 23:30:47 | finance | ledger_check | SUCCESS | 0 | 余额 167，流水 167，逐用户异常 0 个 |
| 2026-11-06 23:17:15 | support | ai_chat | SUCCESS | 0 | 没问题，我帮你挑了 3 个最适合新手的策略，并且在**美的集团（000333）**上用过去 3 年真实数据跑了回测。先看 |
| 2026-11-06 23:17:14 | risk | compliance_review | SUCCESS | 0 | 第 1 轮通过，无违规词 |
| 2026-11-06 23:16:59 | product | tool_compare_strategies | SUCCESS | 0 | {"comparison": [{"strategy": "5/20 均线金叉", "total_return_pct" |
| 2026-11-06 23:16:56 | product | tool_list_symbols | SUCCESS | 0 | {"symbols": [{"code": "000001", "name": "平安银行", "industry":  |
| 2026-11-06 23:16:56 | product | tool_search_strategies | SUCCESS | 0 | {"strategies": [{"code": "ma_cross_5_20", "name": "5/20 均线金叉 |

## 二、交易记录

| 订单号 | 金额 | 积分 | 状态 | 核销 | 时间 |
|---|---|---|---|---|---|
| R202611062311235C18DF | ¥9.90 | 100 | PAID | 用户回填核销 5C18DF | 2026-11-06 23:11:23 |

## 三、回测记录

| 任务号 | 标的 | 状态 | 消耗积分 | 时间 |
|---|---|---|---|---|
| BT20261106231123AB7B | 600519 | DONE | 10 | 2026-11-06 23:11:23 |

## 四、回滚说明

任何节点可从本日志定位后重跑：
- Agent 失败：按  的 task_type 与 biz_id 重派
- 扣费异常：按  的 idempotency_key 定位，重试不会重复扣费
- 数据异常：按  主键重拉该标的该区间的数据