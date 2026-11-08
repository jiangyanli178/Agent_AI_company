# -*- coding: utf-8 -*-
"""鉴权：注册（送 100 积分）、登录、当前用户"""
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..security import create_token, hash_password, verify_password
from ..services.credits import CreditsService

router = APIRouter(prefix="/auth", tags=["鉴权"])

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class RegisterIn(BaseModel):
    phone: str = Field(..., description="手机号")
    password: str = Field(..., min_length=6, description="密码，至少 6 位")


class LoginIn(BaseModel):
    phone: str
    password: str


def _mask(phone: str) -> str:
    return phone[:3] + "****" + phone[-4:] if len(phone) == 11 else phone


def _client_ip(request: Request) -> str:
    """
    取真实客户端 IP。
    穿透或 Nginx 之后，request.client.host 拿到的是代理地址，
    必须优先读 X-Forwarded-For，否则全站用户会被判成同一个 IP。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # 可能是 "真实IP, 代理1, 代理2"，取第一个
        return xff.split(",")[0].strip()[:45]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:45]
    return (request.client.host if request.client else "unknown")[:45]


@router.post("/register")
def register(body: RegisterIn, request: Request):
    """注册即送 100 积分，可立即体验回测与问答"""
    phone = body.phone.strip()
    if not PHONE_RE.match(phone):
        raise BizError(40001, "手机号格式不正确")

    ip = _client_ip(request)

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM users WHERE phone=:p"), {"p": phone}).first()
        if exists:
            raise BizError(40002, "该手机号已注册，请直接登录")

        # 防刷：同一 IP 24 小时内限量注册。
        # 老板手机号不受限，否则配额耗尽时连 admin 都建不了，看板直接锁死。
        is_admin = settings.ADMIN_PHONE and phone == settings.ADMIN_PHONE
        if not is_admin:
            cnt = conn.execute(text(
                "SELECT COUNT(*) FROM users WHERE register_ip=:ip "
                "AND created_at > DATE_SUB(NOW(), INTERVAL 1 DAY)"
            ), {"ip": ip}).scalar()
            if cnt >= settings.REGISTER_DAILY_LIMIT:
                raise BizError(
                    40003,
                    f"当前网络 24 小时内已注册 {cnt} 个账号，达到上限。"
                    f"请明天再试，或换个网络环境",
                )

        cur = conn.execute(text(
            "INSERT INTO users (phone, password_hash, nickname, register_ip) "
            "VALUES (:p, :h, :n, :ip)"
        ), {"p": phone, "h": hash_password(body.password),
            "n": f"用户{_mask(phone)}", "ip": ip})
        uid = cur.lastrowid

        # 开户送积分，幂等键 register:{uid}，重复调用不会重复送
        balance = CreditsService(engine).open_account(conn, uid)

    return ok({
        "user_id": uid,
        "phone": _mask(phone),
        "token": create_token(uid, phone),
        "balance": balance,
        "gift": 100,
        "is_admin": is_admin,
    })


@router.post("/login")
def login(body: LoginIn):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, phone, password_hash, status FROM users WHERE phone=:p"
        ), {"p": body.phone.strip()}).mappings().first()

    # 提示语不区分"账号不存在"和"密码错误"，避免被人枚举手机号
    if not row or not verify_password(body.password, row["password_hash"]):
        raise BizError(40102, "手机号或密码不正确")
    if row["status"] != 1:
        raise BizError(40101, "账号已被停用")

    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET last_login_at=NOW() WHERE id=:u"),
                     {"u": row["id"]})
        balance = CreditsService.get_balance(conn, row["id"])

    return ok({
        "user_id": row["id"],
        "phone": _mask(row["phone"]),
        "token": create_token(row["id"], row["phone"]),
        "balance": balance,
        "is_admin": bool(settings.ADMIN_PHONE and row["phone"] == settings.ADMIN_PHONE),
    })


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return ok(user)
