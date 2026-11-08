# -*- coding: utf-8 -*-
"""
数据库连接。
全局只建一个 engine，连接池复用，不要每个请求新建。
"""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import settings

engine: Engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,       # 取连接前先 ping，避免拿到 MySQL 已断开的死连接
    pool_recycle=3600,        # MySQL wait_timeout 默认 8 小时，1 小时回收更稳
    echo=False,
    future=True,
)


def ping() -> bool:
    """健康检查用"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def table_count() -> int:
    """确认 schema 已初始化"""
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=:db"
        ), {"db": settings.DB_NAME}).scalar() or 0
