# -*- coding: utf-8 -*-
"""因子库：技术指标零件箱，供用户浏览与 AI 引用"""
from fastapi import APIRouter
from sqlalchemy import text

from ..db import engine
from ..exceptions import BizError, ok

router = APIRouter(prefix="/factors", tags=["因子库"])


@router.get("/categories")
def categories():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT category, COUNT(*) AS count FROM factors "
            "WHERE active=1 GROUP BY category ORDER BY MIN(sort_no)"
        )).mappings().all()
    return ok([dict(r) for r in rows])


@router.get("")
def list_factors(keyword: str = "", category: str = "", limit: int = 200):
    where, params = ["active=1"], {}
    if category:
        where.append("category=:c"); params["c"] = category
    if keyword:
        where.append("(name LIKE :k OR summary LIKE :k OR code LIKE :k OR usage_note LIKE :k)")
        params["k"] = f"%{keyword}%"
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT id, code, name, category, summary, formula, usage_note, params_hint "
            f"FROM factors WHERE {' AND '.join(where)} "
            f"ORDER BY sort_no LIMIT :n"
        ), {**params, "n": min(500, max(1, limit))}).mappings().all()
    return ok({"total": len(rows), "items": [dict(r) for r in rows]})


@router.get("/{code_or_id}/demo")
def demo(code_or_id: str, symbol: str = "600519", days: int = 160):
    """
    因子可视化：用真实行情算出这个因子的曲线。
    用户点开 RSI 看到的就是真 RSI，和回测里用的是同一套公式。
    """
    from ..services.context import context
    from ..services.factor_demo import build_demo

    with engine.connect() as conn:
        if code_or_id.isdigit():
            f = conn.execute(text("SELECT code, name FROM factors WHERE id=:i"),
                             {"i": int(code_or_id)}).mappings().first()
        else:
            f = conn.execute(text("SELECT code, name FROM factors WHERE code=:c"),
                             {"c": code_or_id}).mappings().first()
    if not f:
        raise BizError(40412, "因子不存在")

    bars = context.get_bars(symbol, 1)          # 近一年，画出来不挤
    if len(bars) < 60:
        raise BizError(40406, "行情数据不足，无法演示")

    bars = bars[-min(500, max(60, days)):]
    payload = build_demo(f["code"], bars, f["name"])
    payload["symbol"] = symbol
    return ok(payload)


@router.get("/{code_or_id}/export")
def export_factor(code_or_id: str, symbol: str = "600519", days: int = 250):
    """把因子的逐日取值导成 CSV，用户可以自己核对算法"""
    from fastapi import Response
    from urllib.parse import quote
    from ..services.context import context
    from ..services.factor_demo import build_demo
    from ..services.data_export import multi_series_csv

    with engine.connect() as conn:
        if code_or_id.isdigit():
            f = conn.execute(text("SELECT code, name FROM factors WHERE id=:i"),
                             {"i": int(code_or_id)}).mappings().first()
        else:
            f = conn.execute(text("SELECT code, name FROM factors WHERE code=:c"),
                             {"c": code_or_id}).mappings().first()
    if not f:
        raise BizError(40412, "因子不存在")

    bars = context.get_bars(symbol, 2)
    if len(bars) < 60:
        raise BizError(40406, "行情数据不足")
    bars = bars[-min(500, max(60, days)):]

    payload = build_demo(f["code"], bars, f["name"])
    text_csv = multi_series_csv(payload["dates"], payload["series"])

    fname = f"{f['code']}_{f['name']}_{symbol}.csv"
    return Response(
        content=text_csv,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename={f['code']}_{symbol}.csv; filename*=UTF-8''{quote(fname)}",
            "X-Row-Count": str(len(bars)),
        },
    )


@router.get("/{code_or_id}")
def detail(code_or_id: str):
    with engine.connect() as conn:
        if code_or_id.isdigit():
            r = conn.execute(text("SELECT * FROM factors WHERE id=:i"),
                             {"i": int(code_or_id)}).mappings().first()
        else:
            r = conn.execute(text("SELECT * FROM factors WHERE code=:c"),
                             {"c": code_or_id}).mappings().first()
    if not r:
        raise BizError(40412, "因子不存在")
    return ok(dict(r))
