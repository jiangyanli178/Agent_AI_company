# -*- coding: utf-8 -*-
"""
账目一致性自查：账户余额必须等于流水求和。

这是答辩时的一个隐藏加分项——一个会自己对账的系统。
建议每天凌晨跑一次，异常时写 agent_runs 留档。

用法：python scripts/check_ledger.py
"""
import os

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


def main():
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "quant_shop"),
        charset="utf8mb4",
    )
    cur = conn.cursor()

    # ① 总额一致性
    cur.execute("""
        SELECT (SELECT COALESCE(SUM(balance),0) FROM credit_accounts),
               (SELECT COALESCE(SUM(amount),0) FROM credit_transactions)""")
    bal, tx = cur.fetchone()

    # ② 逐用户对账，找出具体是谁对不上
    cur.execute("""
        SELECT a.user_id, a.balance, COALESCE(t.s, 0) AS tx_sum
        FROM credit_accounts a
        LEFT JOIN (SELECT user_id, SUM(amount) AS s
                   FROM credit_transactions GROUP BY user_id) t
          ON t.user_id = a.user_id
        HAVING a.balance <> tx_sum""")
    bad = cur.fetchall()

    # ③ 每个用户最后一条流水的 balance_after 是否等于当前余额
    cur.execute("""
        SELECT a.user_id, a.balance,
               (SELECT balance_after FROM credit_transactions
                WHERE user_id=a.user_id ORDER BY id DESC LIMIT 1) AS last_after
        FROM credit_accounts a
        HAVING last_after IS NOT NULL AND a.balance <> last_after""")
    bad_last = cur.fetchall()

    print("=" * 56)
    print("账目一致性自查")
    print("=" * 56)
    print(f"账户余额合计：{bal}")
    print(f"流水发生额合计：{tx}")
    print(f"总额一致：{'是' if bal == tx else '否'}")

    ok = (bal == tx) and not bad and not bad_last
    if bad:
        print(f"\n⚠️ {len(bad)} 个用户余额与流水求和不符：")
        for uid, b, s in bad[:10]:
            print(f"   用户 {uid}：余额 {b}，流水求和 {s}，差 {b - s}")
    if bad_last:
        print(f"\n⚠️ {len(bad_last)} 个用户余额与最后一条流水不符：")
        for uid, b, la in bad_last[:10]:
            print(f"   用户 {uid}：余额 {b}，最后流水余额 {la}，差 {b - la}")
    if ok:
        print("\n✅ 全部通过：总额一致、逐用户一致、与最后流水一致")

    # 结果写进 agent_runs，作为风控留痕
    cur.execute("""
        INSERT INTO agent_runs (agent_code, task_type, status, output_digest, error_msg)
        VALUES ('finance', 'ledger_check', %s, %s, %s)""",
        ("SUCCESS" if ok else "FAILED",
         f"余额 {bal}，流水 {tx}，逐用户异常 {len(bad)} 个",
         "" if ok else f"对账异常：{len(bad)} 个用户总额不符，{len(bad_last)} 个用户末笔不符"))
    conn.commit()
    conn.close()

    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
