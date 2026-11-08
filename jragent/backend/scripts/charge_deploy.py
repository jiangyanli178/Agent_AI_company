# -*- coding: utf-8 -*-
"""
部署日租扣费：每个 RUNNING 观察项，每天扣一次。

之前这块是空的：只在创建时扣了一次，之后永远不扣，
导致 days_left 的展示是误导，收入模型也不成立。

规则：
- 幂等键 deploy:day:{dep_id}:{YYYY-MM-DD}，同一天重复跑不会重复扣
- 余额不足 → 自动暂停该观察项（PAUSED），并留 agent_runs 事故记录
- 返回扣费明细，便于对账

用法：
    python scripts/charge_deploy.py
    python scripts/charge_deploy.py --dry-run     # 只看不扣
"""
import argparse
import os
from datetime import date

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine                       # noqa: E402
from app.services.credits import CreditsService, PRICE  # noqa: E402
from app.services import agent_log              # noqa: E402


def run(dry_run: bool = False) -> dict:
    svc = CreditsService(engine)
    today = date.today().isoformat()
    per_day = PRICE["DEPLOY_DAY"]

    with engine.connect() as conn:
        from sqlalchemy import text
        rows = conn.execute(text(
            "SELECT d.id, d.user_id, d.symbol_code, s.name AS strategy_name "
            "FROM deployments d JOIN strategies s ON s.id=d.strategy_id "
            "WHERE d.status='RUNNING'"
        )).mappings().all()

    print(f"今日 {today}，待扣费观察项 {len(rows)} 个，单价 {per_day} 积分/天")
    charged, paused, skipped = 0, 0, 0

    for d in rows:
        dep_id, uid = d["id"], d["user_id"]
        idem = f"deploy:day:{dep_id}:{today}"
        label = f"#{dep_id} {d['strategy_name']}×{d['symbol_code']}"

        if dry_run:
            print(f"  [dry-run] {label} 将扣 {per_day} 积分")
            continue

        try:
            with engine.begin() as conn:
                entry = svc.consume(
                    conn, uid, per_day, "DEPLOY_DAY", str(dep_id),
                    idem=idem, remark=f"观察日租 {today} {label}")
            if entry.duplicated:
                skipped += 1
                print(f"  {label} 今日已扣过，跳过")
            else:
                charged += 1
                print(f"  {label} 扣 {per_day}，余额 {entry.balance_after}")
        except Exception as e:
            # 余额不足或其它异常：自动暂停，不硬扣、不报错中断
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE deployments SET status='PAUSED' WHERE id=:i"), {"i": dep_id})
            paused += 1
            agent_log.record(
                "finance", "deploy_daily_charge", "DEGRADED", biz_id=str(dep_id),
                output_digest=f"{label} 因扣费失败自动暂停",
                error_msg=str(e),
            )
            print(f"  {label} 扣费失败（{e}），已自动暂停")

    summary = {"date": today, "charged": charged, "paused": paused,
               "skipped": skipped, "total": len(rows)}
    print(f"完成：扣费 {charged}，暂停 {paused}，跳过 {skipped}")
    agent_log.record("finance", "deploy_daily_charge", "SUCCESS",
                     output_digest=str(summary))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.dry_run)
