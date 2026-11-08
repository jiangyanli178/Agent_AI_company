# -*- coding: utf-8 -*-
"""积分：余额、套餐、流水、充值与核销"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..services.credits import CreditsService

router = APIRouter(prefix="/credits", tags=["积分"])


@router.get("/account")
def account(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT balance, total_recharge, total_gift, total_consumed "
            "FROM credit_accounts WHERE user_id=:u"
        ), {"u": user["id"]}).mappings().first()
    return ok(dict(row) if row else {"balance": 0})


@router.get("/packs")
def packs():
    """充值档位，公开可读，落地页也要用"""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, code, name, price_cny, points, bonus_points, tag "
            "FROM credit_packs WHERE active=1 ORDER BY sort_no"
        )).mappings().all()
    return ok([
        {**dict(r), "price_cny": float(r["price_cny"]),
         "total_points": r["points"] + r["bonus_points"],
         "unit_price": round(float(r["price_cny"]) / (r["points"] + r["bonus_points"]), 4)}
        for r in rows
    ])


@router.get("/transactions")
def transactions(page: int = 1, size: int = 20, user: dict = Depends(get_current_user)):
    page, size = max(1, page), min(50, max(1, size))
    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM credit_transactions WHERE user_id=:u"
        ), {"u": user["id"]}).scalar()
        rows = conn.execute(text(
            "SELECT change_type, amount, balance_after, biz_type, remark, created_at "
            "FROM credit_transactions WHERE user_id=:u "
            "ORDER BY id DESC LIMIT :s OFFSET :o"
        ), {"u": user["id"], "s": size, "o": (page - 1) * size}).mappings().all()
    return ok({
        "total": total, "page": page, "size": size,
        "items": [{**dict(r), "created_at": str(r["created_at"])} for r in rows],
    })


class RechargeIn(BaseModel):
    pack_id: int


class ConfirmIn(BaseModel):
    order_no: str
    payer_suffix: str = ""


@router.post("/recharge")
def recharge(body: RechargeIn, user: dict = Depends(get_current_user)):
    """创建充值订单，返回收款码与订单号（假支付走人工核销）"""
    with engine.begin() as conn:
        pack = conn.execute(text(
            "SELECT * FROM credit_packs WHERE id=:i AND active=1"
        ), {"i": body.pack_id}).mappings().first()
        if not pack:
            raise BizError(40402, "套餐不存在")

        order_no = "R" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()
        points = pack["points"] + pack["bonus_points"]
        conn.execute(text(
            "INSERT INTO recharge_orders (order_no, user_id, pack_id, price_cny, points, pay_channel) "
            "VALUES (:no, :u, :pid, :price, :pts, :ch)"
        ), {"no": order_no, "u": user["id"], "pid": pack["id"],
            "price": pack["price_cny"], "pts": points,
            "ch": "MOCK" if settings.PAY_MODE == "mock" else "QR"})

    return ok({
        "order_no": order_no,
        "price_cny": float(pack["price_cny"]),
        "points": points,
        "pay_mode": settings.PAY_MODE,
        "qr_image": settings.MERCHANT_QR_IMAGE,
        "tip": f"请扫码支付 {pack['price_cny']} 元，备注订单号后 6 位：{order_no[-6:]}",
    })


def _settle_order(conn, order, remark: str):
    """核销入账，重复调用安全（幂等键 = 订单号）"""
    entry = CreditsService(engine).recharge(
        conn, order["user_id"], order["points"], order["order_no"], remark=remark)
    conn.execute(text(
        "UPDATE recharge_orders SET status='PAID', paid_at=NOW(), verify_note=:n "
        "WHERE order_no=:no"
    ), {"n": remark, "no": order["order_no"]})
    return entry


@router.post("/confirm")
def confirm(body: ConfirmIn, user: dict = Depends(get_current_user)):
    """用户付完款后回填，完成核销"""
    with engine.begin() as conn:
        order = conn.execute(text(
            "SELECT * FROM recharge_orders WHERE order_no=:no AND user_id=:u"
        ), {"no": body.order_no, "u": user["id"]}).mappings().first()
        if not order:
            raise BizError(40403, "订单不存在")

        if order["status"] == "PAID":
            # 幂等：重复确认只返回余额，不重复入账
            return ok({"paid": True,
                       "balance": CreditsService.get_balance(conn, user["id"]),
                       "duplicated": True})

        if settings.PAY_MODE != "mock" and not body.payer_suffix:
            raise BizError(40004, "请填写付款备注的后 6 位，便于对账")

        entry = _settle_order(conn, order, f"用户回填核销 {body.payer_suffix}")
        return ok({"paid": True, "balance": entry.balance_after, "points": order["points"]})


@router.post("/mock-pay")
def mock_pay(body: ConfirmIn, user: dict = Depends(get_current_user)):
    """纯模拟支付，仅开发用。生产环境必须删掉或加管理员校验"""
    if settings.PAY_MODE != "mock":
        raise BizError(40301, "当前未开启模拟支付")
    with engine.begin() as conn:
        order = conn.execute(text(
            "SELECT * FROM recharge_orders WHERE order_no=:no AND user_id=:u"
        ), {"no": body.order_no, "u": user["id"]}).mappings().first()
        if not order:
            raise BizError(40403, "订单不存在")
        entry = _settle_order(conn, order, "模拟支付")
        return ok({"paid": True, "balance": entry.balance_after})
