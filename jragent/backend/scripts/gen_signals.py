# -*- coding: utf-8 -*-
"""
每日信号生成：为观察列表中的策略生成最新信号与解释。

设计原则：与回测共用同一个 run_backtest()，
保证"回测看到的逻辑"和"每日给出的信号"是同一套，不会两套口径。

用法：
    python scripts/gen_signals.py              # 全部 RUNNING 部署
    python scripts/gen_signals.py --code 600519
"""
import argparse
import os

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.backtest import Bar, run_backtest, explain_signal  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="只处理某个标的")
    args = ap.parse_args()

    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "quant_shop"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()

    where = "d.status='RUNNING'"
    params = []
    if args.code:
        where += " AND d.symbol_code=%s"
        params.append(args.code)

    cur.execute(f"""
        SELECT d.id, d.symbol_code, d.params, s.strategy_type, s.name
        FROM deployments d JOIN strategies s ON s.id=d.strategy_id
        WHERE {where}""", params)
    deps = cur.fetchall()
    print(f"待生成信号的部署：{len(deps)} 个")

    for d in deps:
        try:
            cur.execute("""
                SELECT trade_date, open_price, high_price, low_price, close_price, volume
                FROM daily_bars WHERE symbol_code=%s ORDER BY trade_date DESC LIMIT 300""",
                (d["symbol_code"],))
            rows = cur.fetchall()
            if len(rows) < 30:
                print(f"  {d['symbol_code']} 数据不足，跳过")
                continue
            bars = [Bar(str(r["trade_date"]), float(r["open_price"]), float(r["high_price"]),
                        float(r["low_price"]), float(r["close_price"]), float(r["volume"]))
                    for r in reversed(rows)]

            import json
            params_obj = json.loads(d["params"]) if isinstance(d["params"], str) else d["params"]
            r = run_backtest(bars, d["strategy_type"], params_obj)

            last_pos = r.positions[-1]
            prev_pos = r.positions[-2] if len(r.positions) > 1 else 0
            action = "BUY" if last_pos > prev_pos else ("SELL" if last_pos < prev_pos else "HOLD")
            reason = explain_signal(bars, d["strategy_type"], params_obj)
            latest = bars[-1].trade_date

            # 唯一键 (deployment_id, trade_date)，重复跑不会产生重复信号
            cur.execute("""
                INSERT INTO signals (deployment_id, trade_date, symbol_code, action,
                                     ref_price, position, reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE action=VALUES(action),
                    ref_price=VALUES(ref_price), position=VALUES(position), reason=VALUES(reason)
            """, (d["id"], latest, d["symbol_code"], action,
                  bars[-1].close, last_pos, reason))
            conn.commit()
            print(f"  {d['name']} × {d['symbol_code']} → {action} @ {latest}（{reason}）")
        except Exception as e:
            conn.rollback()
            print(f"  {d['symbol_code']} 失败：{e}")

    conn.close()
    print("完成")


if __name__ == "__main__":
    main()
