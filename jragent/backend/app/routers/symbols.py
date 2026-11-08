# -*- coding: utf-8 -*-
"""行情：标的列表、日线、数据截止日期"""
from fastapi import APIRouter
from sqlalchemy import text

from ..db import engine
from ..exceptions import BizError, ok

router = APIRouter(prefix="/symbols", tags=["行情"])


@router.get("/meta/cutoff")
def cutoff():
    """
    数据截止日期。前端每个结果页都要展示，
    否则用户会误以为能回测到当下。
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT MAX(trade_date) AS latest, COUNT(DISTINCT symbol_code) AS symbols, "
            "COUNT(*) AS bars FROM daily_bars"
        )).mappings().first()
    return ok({
        "latest_trade_date": str(row["latest"]) if row["latest"] else None,
        "symbol_count": row["symbols"],
        "bar_count": row["bars"],
        "note": "数据截至最近一个已收盘交易日，T 日数据于收盘后次日更新",
    })


@router.get("")
def list_symbols(keyword: str = "", industry: str = "", limit: int = 50):
    limit = min(100, max(1, limit))
    where, params = ["active=1"], {}
    if keyword:
        where.append("(code LIKE :k OR name LIKE :k)"); params["k"] = f"%{keyword}%"
    if industry:
        where.append("industry=:i"); params["i"] = industry
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT code, name, market, industry FROM symbols "
            f"WHERE {' AND '.join(where)} ORDER BY code LIMIT :n"
        ), {**params, "n": limit}).mappings().all()
    return ok([dict(r) for r in rows])


@router.get("/{code}/export")
def export_bars(code: str, years: int = 3):
    """
    下载该标的的日线原始数据（CSV）。
    浏览器直接下载，服务器不留文件 —— 用户的数据本来就该能拿走。
    """
    from fastapi import Response
    from ..services.data_export import bars_csv
    from ..services.backtest import Bar

    years = min(10, max(1, years))
    with engine.connect() as conn:
        sym = conn.execute(text(
            "SELECT code, name FROM symbols WHERE code=:c"), {"c": code}).mappings().first()
        if not sym:
            raise BizError(40405, "标的不在池内")
        rows = conn.execute(text(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_bars WHERE symbol_code=:c "
            "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM daily_bars "
            "                           WHERE symbol_code=:c), INTERVAL :y YEAR) "
            "ORDER BY trade_date"
        ), {"c": code, "y": years}).all()

    if not rows:
        raise BizError(40406, "该标的暂无行情数据")

    bars = [Bar(str(r[0]), float(r[1]), float(r[2]), float(r[3]),
                float(r[4]), float(r[5])) for r in rows]
    text_csv = bars_csv(bars, code, sym["name"])

    from urllib.parse import quote
    fname = f"{code}_{sym['name']}_日线_{rows[0][0]}至{rows[-1][0]}.csv"
    return Response(
        content=text_csv,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename={code}_{years}y.csv; filename*=UTF-8''{quote(fname)}",
            "X-Row-Count": str(len(rows)),
        },
    )

@router.get("/{code}/bars")
def bars(code: str, years: int = 3):
    """取日线，按日期升序返回，前端画图与后端回测共用"""
    years = min(5, max(1, years))
    with engine.connect() as conn:
        sym = conn.execute(text(
            "SELECT code, name FROM symbols WHERE code=:c"), {"c": code}).mappings().first()
        if not sym:
            raise BizError(40405, "标的不在池内")
        rows = conn.execute(text(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_bars WHERE symbol_code=:c "
            "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM daily_bars "
            "                           WHERE symbol_code=:c), INTERVAL :y YEAR) "
            "ORDER BY trade_date"
        ), {"c": code, "y": years}).all()
    if not rows:
        raise BizError(40406, "该标的暂无行情数据")
    return ok({
        "code": code, "name": sym["name"], "years": years,
        "count": len(rows),
        "range": f"{rows[0][0]} ~ {rows[-1][0]}",
        "bars": [{"date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
                  "low": float(r[3]), "close": float(r[4]), "volume": int(r[5])}
                 for r in rows],
    })
