# -*- coding: utf-8 -*-
"""策略库：列表、分类、详情"""
import json

from fastapi import APIRouter
from sqlalchemy import text

from ..db import engine
from ..exceptions import BizError, ok

router = APIRouter(prefix="/strategies", tags=["策略库"])


def _row_to_card(r) -> dict:
    d = dict(r)
    d.pop("param_schema", None)
    d.pop("default_params", None)
    d.pop("detail", None)
    return d


@router.get("/categories")
def categories():
    """分类聚合。注意：必须写在 /{strategy_id} 之前，否则会被当成 id"""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT category, COUNT(*) AS count FROM strategies "
            "WHERE is_public=1 GROUP BY category ORDER BY count DESC"
        )).mappings().all()
    return ok([dict(r) for r in rows])


@router.get("")
def list_strategies(category: str = "", difficulty: int = 0,
                    keyword: str = "", page: int = 1, size: int = 20):
    size = min(50, max(1, size))
    page = max(1, page)
    where, params = ["is_public=1"], {}
    if category:
        where.append("category=:c"); params["c"] = category
    if difficulty:
        where.append("difficulty=:d"); params["d"] = difficulty
    if keyword:
        where.append("(name LIKE :k OR summary LIKE :k OR tags LIKE :k)")
        params["k"] = f"%{keyword}%"
    cond = " AND ".join(where)

    with engine.connect() as conn:
        total = conn.execute(text(
            f"SELECT COUNT(*) FROM strategies WHERE {cond}"), params).scalar()
        rows = conn.execute(text(
            f"SELECT id, code, name, category, strategy_type, summary, difficulty, tags, use_count "
            f"FROM strategies WHERE {cond} ORDER BY difficulty, id "
            f"LIMIT :s OFFSET :o"
        ), {**params, "s": size, "o": (page - 1) * size}).mappings().all()
    return ok({"total": total, "page": page,
               "items": [_row_to_card(r) for r in rows]})


@router.get("/{strategy_id}")
def detail(strategy_id: int):
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT * FROM strategies WHERE id=:i AND is_public=1"
        ), {"i": strategy_id}).mappings().first()
    if not r:
        raise BizError(40404, "策略不存在")
    d = dict(r)
    # JSON 列在 PyMySQL 下返回字符串，统一转成对象，前端直接拿去渲染表单
    for k in ("param_schema", "default_params"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["created_at"] = str(d["created_at"])
    return ok(d)
