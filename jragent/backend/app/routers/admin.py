# -*- coding: utf-8 -*-
"""经营看板：营收、agent 绩效、事故、账目自查"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..db import engine
from ..deps import require_admin
from ..exceptions import ok
from ..services import agent_log

router = APIRouter(prefix="/admin", tags=["经营看板"])


@router.get("/dashboard")
def dashboard(user: dict = Depends(require_admin)):
    with engine.connect() as conn:
        rev = conn.execute(text(
            "SELECT COUNT(*) AS orders, COALESCE(SUM(price_cny),0) AS gmv, "
            "       COUNT(DISTINCT user_id) AS payers "
            "FROM recharge_orders WHERE status='PAID'"
        )).mappings().first()
        users = conn.execute(text("SELECT COUNT(*) AS c FROM users")).scalar()
        consumed = conn.execute(text(
            "SELECT COALESCE(SUM(-amount),0) AS pts FROM credit_transactions WHERE change_type='CONSUME'"
        )).scalar()
        tokens = conn.execute(text(
            "SELECT COALESCE(SUM(tokens_in),0) AS ti, COALESCE(SUM(tokens_out),0) AS tout "
            "FROM ai_messages"
        )).mappings().first()
        # 账目自查：余额必须等于流水求和
        chk = conn.execute(text(
            "SELECT (SELECT COALESCE(SUM(balance),0) FROM credit_accounts) AS bal, "
            "       (SELECT COALESCE(SUM(amount),0) FROM credit_transactions) AS tx"
        )).mappings().first()
        outstanding = conn.execute(text(
            "SELECT COALESCE(SUM(balance),0) AS b FROM credit_accounts")).scalar()

    token_cost = round(float(tokens["ti"] or 0) / 1e6 * 2.0
                       + float(tokens["tout"] or 0) / 1e6 * 8.0, 4)
    gmv = float(rev["gmv"] or 0)
    outstanding = int(outstanding or 0)
    return ok({
        "gmv_cny": gmv,
        "orders": rev["orders"],
        "paying_users": rev["payers"],
        "total_users": users,
        "points_consumed": consumed,
        "tokens_in": tokens["ti"],
        "tokens_out": tokens["tout"],
        "token_cost_cny": token_cost,
        "outstanding_points": outstanding,
        "outstanding_cny": round(outstanding * 0.10, 2),
        "ledger": {
            "balance_sum": chk["bal"],
            "tx_sum": chk["tx"],
            "consistent": chk["bal"] == chk["tx"],
        },
        "gross_margin_pct": round((gmv - token_cost) / gmv * 100, 1) if gmv else None,
    })


@router.get("/agent-kpi")
def agent_kpi(user: dict = Depends(require_admin)):
    return ok(agent_log.kpi())


@router.get("/incidents")
def incidents(limit: int = 20, user: dict = Depends(require_admin)):
    return ok(agent_log.incidents(limit))


# ============================================================
# 老板工作台：确认收款 + 看流水
# ============================================================

@router.get("/orders")
def orders(status: str = "", limit: int = 50, user: dict = Depends(require_admin)):
    """充值订单。默认列出待核销的，老板确认钱到账后才入积分。"""
    where, params = [], {"n": min(200, max(1, limit))}
    if status:
        where.append("o.status=:s"); params["s"] = status
    cond = ("WHERE " + " AND ".join(where)) if where else ""

    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT o.order_no, o.price_cny, o.points, o.status, o.pay_channel, "
            f"       o.verify_note, o.created_at, o.paid_at, u.phone, u.id AS user_id "
            f"FROM recharge_orders o JOIN users u ON u.id=o.user_id "
            f"{cond} ORDER BY o.id DESC LIMIT :n"
        ), params).mappings().all()
        pending = conn.execute(text(
            "SELECT COUNT(*) FROM recharge_orders WHERE status='PENDING'")).scalar()

    def mask(p):
        return p[:3] + "****" + p[-4:] if p and len(p) == 11 else p

    return ok({
        "pending_count": pending,
        "items": [{
            **{k: v for k, v in dict(r).items() if k != "phone"},
            "phone": mask(r["phone"]),
            "price_cny": float(r["price_cny"]),
            "created_at": str(r["created_at"]),
            "paid_at": str(r["paid_at"]) if r["paid_at"] else None,
        } for r in rows],
    })


@router.post("/orders/{order_no}/verify")
def verify_order(order_no: str, user: dict = Depends(require_admin)):
    """
    老板确认收到钱，手动核销入账。
    幂等：重复点不会重复加积分。
    """
    from ..services.credits import CreditsService

    svc = CreditsService(engine)
    with engine.begin() as conn:
        o = conn.execute(text(
            "SELECT * FROM recharge_orders WHERE order_no=:n"
        ), {"n": order_no}).mappings().first()
        if not o:
            raise BizError(40403, "订单不存在")
        if o["status"] == "PAID":
            return ok({"order_no": order_no, "already": True,
                       "balance": svc.get_balance(conn, o["user_id"])})

        entry = svc.recharge(conn, o["user_id"], o["points"], o["order_no"],
                             remark=f"老板确认收款 {o['price_cny']} 元")
        conn.execute(text(
            "UPDATE recharge_orders SET status='PAID', paid_at=NOW(), "
            "verify_note=:n WHERE order_no=:no"
        ), {"n": f"管理员 {user.get('phone','')} 确认", "no": order_no})
        agent_log.record("finance", "manual_verify", "SUCCESS", biz_id=order_no,
                         output_digest=f"确认 {order_no}，{o['price_cny']} 元 / {o['points']} 积分")
    return ok({"order_no": order_no, "verified": True, "balance": entry.balance_after})


@router.get("/ledger")
def ledger(limit: int = 100, change_type: str = "", user: dict = Depends(require_admin)):
    """全站积分流水，按用户聚合展示"""
    where, params = [], {"n": min(500, max(1, limit))}
    if change_type:
        where.append("t.change_type=:ct"); params["ct"] = change_type
    cond = ("WHERE " + " AND ".join(where)) if where else ""

    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT t.id, t.change_type, t.amount, t.balance_after, t.biz_type, "
            f"       t.remark, t.created_at, u.phone "
            f"FROM credit_transactions t JOIN users u ON u.id=t.user_id "
            f"{cond} ORDER BY t.id DESC LIMIT :n"
        ), params).mappings().all()

        # 按类型汇总
        summary = conn.execute(text(
            "SELECT change_type, COUNT(*) AS cnt, SUM(amount) AS total "
            "FROM credit_transactions GROUP BY change_type"
        )).mappings().all()

    def mask(p):
        return p[:3] + "****" + p[-4:] if p and len(p) == 11 else p

    return ok({
        "summary": [{"type": r["change_type"], "count": r["cnt"],
                     "total": int(r["total"] or 0)} for r in summary],
        "items": [{
            **{k: v for k, v in dict(r).items() if k != "phone"},
            "phone": mask(r["phone"]),
            "created_at": str(r["created_at"]),
        } for r in rows],
    })
