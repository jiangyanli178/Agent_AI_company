# -*- coding: utf-8 -*-
"""分享：回测报告的公开只读链接，是最便宜的获客出口"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok

router = APIRouter(tags=["分享"])


@router.post("/backtest/jobs/{job_no}/share")
def create_share(job_no: str, user: dict = Depends(get_current_user)):
    """把一次回测设为公开，返回分享路径"""
    with engine.begin() as conn:
        r = conn.execute(text(
            "SELECT share_token FROM backtest_jobs WHERE job_no=:j AND user_id=:u"
        ), {"j": job_no, "u": user["id"]}).mappings().first()
        if not r:
            raise BizError(40407, "回测记录不存在")
        token = r["share_token"] or uuid.uuid4().hex[:16]
        conn.execute(text(
            "UPDATE backtest_jobs SET is_public=1, share_token=:t WHERE job_no=:j"
        ), {"t": token, "j": job_no})
    return ok({"share_token": token, "path": f"/s/{token}"})


@router.get("/share/{token}")
def read_share(token: str):
    """
    公开只读。注意：只返回结果本身，不返回用户手机号等任何隐私信息。
    """
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT result_json, symbol_code, start_date, end_date, created_at "
            "FROM backtest_jobs WHERE share_token=:t AND is_public=1 AND status='DONE'"
        ), {"t": token}).mappings().first()
    if not r:
        raise BizError(40408, "分享不存在或已关闭")
    import json
    payload = json.loads(r["result_json"]) if isinstance(r["result_json"], str) else r["result_json"]
    return ok({
        "result": payload,
        "symbol_code": r["symbol_code"],
        "created_at": str(r["created_at"]),
        "disclaimer": "历史表现不代表未来，本报告为技术工具输出，不构成投资建议",
    })
