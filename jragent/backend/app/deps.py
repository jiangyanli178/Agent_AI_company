# -*- coding: utf-8 -*-
"""路由依赖：当前用户、管理员校验。"""
from fastapi import Depends, Header
from sqlalchemy import text

from .config import settings
from .db import engine
from .exceptions import BizError
from .security import decode_token


def get_current_user(authorization: str = Header(default="")) -> dict:
    """
    从 Authorization: Bearer <token> 解出用户。
    未登录或 token 无效一律 401，前端据此跳登录页。
    """
    if not authorization.startswith("Bearer "):
        raise BizError(40100, "请先登录")
    payload = decode_token(authorization[7:].strip())
    if not payload:
        raise BizError(40100, "登录已过期，请重新登录")

    uid = int(payload["sub"])
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, phone, nickname, status, is_paid FROM users WHERE id=:u"
        ), {"u": uid}).mappings().first()

    if not row:
        raise BizError(40100, "账号不存在")
    if row["status"] != 1:
        raise BizError(40101, "账号已被停用")
    return dict(row)


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """看板接口用，只放行老板手机号"""
    if settings.ADMIN_PHONE and user["phone"] != settings.ADMIN_PHONE:
        raise BizError(40300, "没有权限")
    return user
