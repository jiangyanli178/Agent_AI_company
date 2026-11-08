# -*- coding: utf-8 -*-
"""
生成一页纸经营周报 → hub/08-review.md

官方蓝图要求阶段 6 的唯一交付物。
数据全部来自真实数据库，没有数据就写"尚未产生"，不编造。

用法：
    python scripts/weekly_report.py           # 最近 7 天
    python scripts/weekly_report.py --days 30
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


def conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "quant_shop"),
        charset="utf8mb4",
    )


def one(cur, sql, args=None):
    cur.execute(sql, args or {})
    return cur.fetchone()


def build(days: int) -> str:
    c = conn()
    cur = c.cursor()
    w = f"DATE_SUB(NOW(), INTERVAL {int(days)} DAY)"

    orders, gmv, payers = one(cur, f"""
        SELECT COUNT(*), COALESCE(SUM(price_cny),0), COUNT(DISTINCT user_id)
        FROM recharge_orders WHERE status='PAID' AND created_at > {w}""") or (0, 0, 0)
    total_users = one(cur, "SELECT COUNT(*) FROM users")[0]
    backtests = one(cur, f"SELECT COUNT(*) FROM backtest_jobs WHERE created_at > {w}")[0]
    chats = one(cur, f"SELECT COUNT(*) FROM ai_messages WHERE created_at > {w} AND role='user'")[0]

    consume = cur.execute(f"""
        SELECT biz_type, COUNT(*), SUM(-amount) FROM credit_transactions
        WHERE change_type='CONSUME' AND created_at > {w}
        GROUP BY biz_type ORDER BY SUM(-amount) DESC""")
    consume_rows = cur.fetchall()

    ti, to = one(cur, f"""
        SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0)
        FROM ai_messages WHERE created_at > {w}""") or (0, 0)
    token_cost = float(ti or 0) / 1e6 * 2.0 + float(to or 0) / 1e6 * 8.0

    bal, tx = one(cur, """
        SELECT (SELECT COALESCE(SUM(balance),0) FROM credit_accounts),
               (SELECT COALESCE(SUM(amount),0) FROM credit_transactions)""")
    consistent = (bal == tx)

    kcur = c.cursor(pymysql.cursors.DictCursor)
    kcur.execute("SELECT * FROM v_agent_kpi")
    kpis = kcur.fetchall()

    cur.execute(f"""
        SELECT agent_code, task_type, status, retry_count, output_digest, error_msg
        FROM agent_runs WHERE status IN ('FAILED','ESCALATED','DEGRADED')
          AND created_at > {w} ORDER BY id DESC LIMIT 5""")
    incidents = cur.fetchall()

    gmv_f = float(gmv or 0)
    margin = (gmv_f - token_cost) / gmv_f * 100 if gmv_f else None
    today = datetime.now().strftime("%Y-%m-%d")

    def money(x):
        return f"¥{x:.2f}" if x is not None else "尚未产生"

    lines = [
        f"# 策略体检所 · 经营周报（{today}，近 {days} 天）",
        "",
        "> 本报告由财务 Agent 自动生成，数据全部来自业务数据库。",
        "",
        "## 一、营收",
        f"- GMV：**{money(gmv_f)}**（订单 {orders} 笔，付费用户 {payers} 人）",
        f"- 累计注册用户：{total_users} 人",
        f"- 客单价：{money(gmv_f / payers) if payers else '尚未产生'}",
        "",
        "## 二、产品使用",
        f"- 回测次数：{backtests} 次",
        f"- AI 提问：{chats} 轮",
        "",
        "### 用户在为什么付钱（积分消耗结构）",
    ]
    if consume_rows:
        lines += ["| 功能 | 次数 | 消耗积分 |", "|---|---|---|"]
        lines += [f"| {b} | {n} | {p} |" for b, n, p in consume_rows]
    else:
        lines.append("尚未产生消耗。")

    lines += [
        "",
        "## 三、成本与毛利",
        f"- token 用量：输入 {ti} / 输出 {to}",
        f"- token 成本：{money(token_cost)}",
        f"- 毛利率：**{f'{margin:.1f}%' if margin is not None else '尚未产生营收'}**",
        "",
        "## 四、AI 员工绩效",
    ]
    if kpis:
        lines += ["| Agent | 运行 | 成功率 | 平均耗时 | 成本 |", "|---|---|---|---|---|"]
        lines += [
            f"| {k['agent_code']} | {k['total_runs']} | {k['success_rate']}% | "
            f"{k['avg_latency_ms']}ms | ¥{k['total_cost_cny']} |" for k in kpis
        ]
    else:
        lines.append("暂无运行记录。")

    lines += ["", "## 五、异常与事故"]
    if incidents:
        for a, t, s, rc, od, em in incidents:
            lines.append(f"- `{s}` {a}/{t}（重试 {rc} 次）：{od or em}")
    else:
        lines.append("本周无异常。")

    lines += [
        "",
        "## 六、三个决策建议（由老板拍板，对应闸门⑧）",
        "1. （根据消耗结构，判断哪个功能值得加大投入）",
        "2. （根据毛利率，判断定价是否需要调整）",
        "3. （根据事故记录，判断哪个 Agent 需要调整提示词）",
        "",
        "## 七、账目自查",
        f"- 账户余额合计：{bal} 积分",
        f"- 流水发生额合计：{tx} 积分",
        f"- 一致性：**{'✅ 通过' if consistent else '❌ 异常，需要立即排查'}**",
        "",
    ]
    c.close()
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    content = build(args.days)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = args.out or os.path.join(os.path.dirname(root), "hub", "08-review.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"周报已生成：{out}")
    print("-" * 60)
    print(content)


if __name__ == "__main__":
    main()
