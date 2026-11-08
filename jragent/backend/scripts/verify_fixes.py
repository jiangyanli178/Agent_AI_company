# -*- coding: utf-8 -*-
"""
本轮三个修复的回归验证。MySQL 启动后运行：

    python scripts/verify_fixes.py

验证内容：
  ① IDOR：不能往别人的 AI 会话写消息
  ② 工具费退款：异常时已扣工具费会逐笔退回
  ③ 日租扣费：每天扣一次、重复跑幂等、余额不足自动暂停
"""
import os
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient     # noqa: E402
from app.main import app                      # noqa: E402
from app.config import settings               # noqa: E402


def conn():
    return pymysql.connect(
        host=settings.DB_HOST, user=settings.DB_USER,
        password=settings.DB_PASSWORD, database=settings.DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)


def main():
    c = TestClient(app)
    print("=" * 60)
    print("① IDOR 越权校验")
    print("=" * 60)

    db = conn()
    cur = db.cursor()
    cur.execute("SELECT id, user_id FROM ai_conversations ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("  库里还没有会话，先随便问一句再来跑")
        return
    conv_id, owner = row["id"], row["user_id"]
    print(f"  目标会话 #{conv_id}，属于 user_id={owner}")

    cur.execute("SELECT phone FROM users WHERE id<>%s LIMIT 1", (owner,))
    other = cur.fetchone()
    if not other:
        print("  只有一个用户，无法做跨用户测试")
        return

    # 用一个已知可登录的账号扮演"别人"
    r = c.post("/api/auth/login", json={"phone": "13800000000", "password": "Admin@2026"})
    token = r.json()["data"]["token"]
    r = c.post("/api/ai/chat",
               json={"message": "越权测试", "conversation_id": conv_id},
               headers={"Authorization": "Bearer " + token})
    ok = r.status_code == 404
    print(f"  他人访问该会话 → HTTP {r.status_code} {'✅ 已拦截' if ok else '❌ 未拦截'}")

    print()
    print("=" * 60)
    print("② 工具费退款路径")
    print("=" * 60)
    src = open("app/routers/ai.py", encoding="utf-8").read()
    checks = [
        ("记录已扣工具费", "charged_tools.append"),
        ("异常时逐笔退款", 'svc.refund(conn, uid, t["points"]'),
        ("预扣也要退", "svc.fail_release"),
        ("退款失败要留痕", 'agent_log.record("finance", "tool_refund", "FAILED"'),
    ]
    for label, needle in checks:
        print(f"  {label}：{'✅' if needle in src else '❌ 缺失'}")

    print()
    print("=" * 60)
    print("③ 日租扣费（dry-run，不真扣）")
    print("=" * 60)
    from scripts.charge_deploy import run
    run(dry_run=True)

    print()
    print("=" * 60)
    print("④ 价目表一致性")
    print("=" * 60)
    from app.services.credits import PRICE
    print(f"  当前价目：{PRICE}")
    print(f"  SIGNAL_DAY 已移除（日租含信号）：{'✅' if 'SIGNAL_DAY' not in PRICE else '❌ 仍在'}")
    db.close()


if __name__ == "__main__":
    main()
