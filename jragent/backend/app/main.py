# -*- coding: utf-8 -*-
"""
FastAPI 入口。

启动：
    cd backend
    uvicorn app.main:app --reload --port 8000

文档：
    http://127.0.0.1:8000/docs
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __init__ as _pkg  # noqa: F401  确保包被初始化
from .config import settings
from .db import ping, table_count
from .exceptions import HANDLERS, ok
from .logging_config import setup_logging

setup_logging()

app = FastAPI(
    title="策略体检所 StrategyClinic",
    description="你说人话，AI 帮你找策略、跑回测、把结果翻译成人话。",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for exc, handler in HANDLERS:
    app.add_exception_handler(exc, handler)


# ---------------- 业务路由 ----------------
from .routers import (
    admin, ai, auth, backtest, credits, deploy, deployments, factors,
    reports, saved, saved_factors, share, strategies, symbols,
)

for r in (auth, credits, strategies, symbols, backtest, ai, admin, share,
          deployments, saved, factors, saved_factors, deploy, reports):
    app.include_router(r.router, prefix="/api")

# 量化研究工作室 API（原 studio/webapp.py 独立服务已并入，挂 /api/studio 前缀）
from .routers import studio  # noqa: E402
app.include_router(studio.router)

# 后续按需追加：
# from .routers import deployments, ai, kb, share, admin
# for r in (deployments, ai, kb, share, admin):
#     app.include_router(r.router, prefix="/api")


@app.get("/api/health")
def health():
    """健康检查：确认服务与数据库都活着"""
    return ok({
        "service": "strategy-clinic",
        "db_connected": ping(),
        "db_tables": table_count() if ping() else 0,
        "model": settings.MIMO_MODEL,
        "mimo_key_configured": bool(settings.MIMO_API_KEY),
        "pay_mode": settings.PAY_MODE,
        "tz": settings.TZ,
    })


@app.get("/")
def root():
    return ok({"name": "策略体检所 StrategyClinic", "docs": "/docs"})
