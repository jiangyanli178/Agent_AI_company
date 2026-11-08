"""
部署接口：给前端和 AI 共用。

原则：不在服务器上替用户执行 SSH 命令。
不是做不到，是风险不对等 —— 一次误操作可能把用户已有的服务搞坏，
而我们又看不到对方机器上到底跑了什么。所以做「生成包 + 分步引导」，
每一步用户自己确认，出问题也容易定位。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok

def _merge_filters(conn_row, body_filters):
    """
    部署时过滤条件的优先级：用户当场传的 > 策略自带的。
    不读数据库里那一列的话，公共库里「RSI+ADX」这种策略
    下载到的脚本会是裸 RSI —— 用户拿到的东西和页面上看到的不是同一个。
    """
    import json as _json
    if body_filters:
        return body_filters
    raw = conn_row.get("filters") if hasattr(conn_row, "get") else conn_row["filters"]
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except Exception:
            return None
    return raw


router = APIRouter(prefix="/deploy", tags=["部署"])


class PackIn(BaseModel):
    strategy_code: str = ""
    job_no: str = ""
    symbol_code: str = "600519"
    years: int = 3
    params: Dict[str, Any] = Field(default_factory=dict)
    filters: List[Dict[str, Any]] = Field(default_factory=list)
    condition_text: str = ""


@router.get("/guide")
def guide():
    """买服务器 + 部署的完整步骤，前端和 AI 都用这份"""
    try:
        from ..services.deploy_pack import PLATFORMS, STEPS
    except ImportError:
        from app.services.deploy_pack import PLATFORMS, STEPS
    return ok({
        "platforms": PLATFORMS,
        "steps": STEPS,
        "template_note": (
            "所有策略共用一套部署模板：Docker + 每日任务 + 一键脚本。"
            "换策略只改 strategy.py 顶部的参数和条件，部署逻辑不变。"
        ),
        "not_included": [
            "不自动下单（需要券商接口权限和风控，刻意不做）",
            "不代用户执行 SSH（误操作风险不可控，改为分步引导）",
            "不保证盈利（只把验证过的规则原样搬到服务器执行）",
        ],
    })


def _payload_from_job(job_no: str, uid: int) -> Dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT j.symbol_code, j.params, j.start_date, j.end_date, "
            "       s.code AS strategy_code, s.name AS strategy_name, s.strategy_type "
            "FROM backtest_jobs j JOIN strategies s ON s.id = j.strategy_id "
            "WHERE j.job_no = :j AND j.user_id = :u"
        ), {"j": job_no, "u": uid}).mappings().first()
    if not row:
        raise BizError(40410, "没有找到这次回测记录")

    import json
    params = row["params"]
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}

    # 用实际区间反推年数，导出脚本拉数据时不会差太多
    years = 3
    try:
        d0 = row["start_date"]
        d1 = row["end_date"]
        years = max(1, round((d1 - d0).days / 365))
    except Exception:
        pass

    return {
        "strategy_code": row["strategy_code"],
        "strategy_type": row["strategy_type"],
        "strategy_name": row["strategy_name"],
        "symbol_code": row["symbol_code"],
        "years": years,
        "params": params or {},
        "filters": [],
        "job_no": job_no,
    }


@router.post("/package")
def package(body: PackIn, user: dict = Depends(get_current_user)):
    """生成部署 zip，浏览器直接下载"""
    try:
        from ..services.deploy_pack import build_package
    except ImportError:
        from app.services.deploy_pack import build_package

    if body.job_no:
        payload = _payload_from_job(body.job_no, user["id"])
        payload["condition_text"] = body.condition_text
    else:
        if not body.strategy_code:
            raise BizError(40011, "需要 strategy_code 或 job_no")
        with engine.connect() as conn:
            s = conn.execute(text(
                "SELECT code, name, strategy_type, default_params, filters FROM strategies WHERE code=:c"
            ), {"c": body.strategy_code}).mappings().first()
        if not s:
            raise BizError(40410, f"没有找到策略 {body.strategy_code}")
        params = dict(s["default_params"] or {}) if isinstance(s["default_params"], dict) else {}
        params.update(body.params or {})
        payload = {
            "strategy_code": s["code"],
            "strategy_type": s["strategy_type"],
            "strategy_name": s["name"],
            "symbol_code": body.symbol_code,
            "years": max(1, min(int(body.years), 10)),
            "params": params,
            "filters": _merge_filters(s, body.filters),
            "condition_text": body.condition_text,
        }

    try:
        blob = build_package(payload)
    except Exception as e:
        raise BizError(50010, f"生成部署包失败：{e}")

    name = f"jragent-{payload['strategy_code']}-{payload['symbol_code']}-{datetime.now():%Y%m%d}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={name}; filename*=UTF-8''{quote(name)}",
            "X-Package-Size": str(len(blob)),
        },
    )


@router.post("/preview")
def preview(body: PackIn, user: dict = Depends(get_current_user)):
    """
    不下载，先看包里有什么、多少行 —— 给 AI 用来回答「部署要准备什么」。
    """
    try:
        from ..services.deploy_pack import build_package
    except ImportError:
        from app.services.deploy_pack import build_package

    if body.job_no:
        payload = _payload_from_job(body.job_no, user["id"])
    else:
        with engine.connect() as conn:
            s = conn.execute(text(
                "SELECT code, name, strategy_type, default_params, filters FROM strategies WHERE code=:c"
            ), {"c": body.strategy_code}).mappings().first()
        if not s:
            raise BizError(40410, f"没有找到策略 {body.strategy_code}")
        payload = {
            "strategy_code": s["code"],
            "strategy_type": s["strategy_type"],
            "strategy_name": s["name"],
            "symbol_code": body.symbol_code,
            "years": body.years,
            "params": body.params or (s["default_params"] or {}),
            "filters": _merge_filters(s, body.filters),
            "condition_text": body.condition_text,
        }

    blob = build_package(payload)

    import io
    import zipfile
    files = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            files.append({
                "path": info.filename.split("/", 1)[-1],
                "bytes": info.file_size,
                "lines": z.read(info).decode("utf-8", "ignore").count("\n") + 1,
            })

    return ok({
        "strategy": payload.get("strategy_name"),
        "symbol_code": payload.get("symbol_code"),
        "size_kb": round(len(blob) / 1024, 1),
        "files": files,
        "can_download": True,
        "download_hint": "回测结果页点「下载部署包」，或调 POST /api/deploy/package",
    })
