# -*- coding: utf-8 -*-
"""
鉴权：bcrypt 密码哈希 + JWT。

直接用 bcrypt 库而不是 passlib —— passlib 1.7.4 与新版 bcrypt 有兼容告警，
hackathon 现场少一个坑是一个。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from .config import settings


# ---------------- 密码 ----------------
def hash_password(plain: str) -> str:
    if len(plain) < 6:
        raise ValueError("密码至少 6 位")
    # bcrypt 只取前 72 字节，超长直接截断会造成"两个密码都能登录"的错觉，先拦截
    if len(plain.encode("utf-8")) > 72:
        raise ValueError("密码过长")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------- JWT ----------------
def create_token(user_id: int, phone: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "phone": phone,
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """校验失败一律返回 None，由上层决定怎么拒绝，不在这里抛异常"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
