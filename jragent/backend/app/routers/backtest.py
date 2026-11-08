# -*- coding: utf-8 -*-
"""回测：扣分 → 执行 → 存结果 → 返回"""
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..services.backtest import Bar, run_backtest, explain_signal
from ..services.credits import CreditsService, PRICE, InsufficientCredits

router = APIRouter(prefix="/backtest", tags=["回测"])


class BacktestIn(BaseModel):
    strategy_id: int
    symbol_code: str = Field(..., description="6 位代码，如 600519")
    years: int = Field(3, ge=1, le=5)
    params: dict = Field(default_factory=dict, description="覆盖默认参数")


def _load_bars(code: str, years: int) -> list[Bar]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_bars WHERE symbol_code=:c "
            "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM daily_bars "
            "                           WHERE symbol_code=:c), INTERVAL :y YEAR) "
            "ORDER BY trade_date"
        ), {"c": code, "y": years}).all()
    return [Bar(str(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows]


@router.post("")
def create_backtest(body: BacktestIn, user: dict = Depends(get_current_user)):
    uid = user["id"]
    svc = CreditsService(engine)

    with engine.connect() as conn:
        strat = conn.execute(text(
            "SELECT * FROM strategies WHERE id=:i AND is_public=1"
        ), {"i": body.strategy_id}).mappings().first()
    if not strat:
        raise BizError(40404, "策略不存在")

    params = dict(json.loads(strat["default_params"])
                  if isinstance(strat["default_params"], str) else strat["default_params"])
    params.update(body.params or {})

    # 公共策略可能自带过滤条件（比如「RSI + ADX>20.5」）。
    # 不读这一列的话，用户点进去跑出来的是裸策略，和描述对不上。
    filters = strat["filters"]
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except Exception:
            filters = None
    filters = filters or None

    bars = _load_bars(body.symbol_code, body.years)
    if len(bars) < 30:
        raise BizError(40005, f"{body.symbol_code} 数据不足 30 根 K 线，无法回测")

    job_no = "BT" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4].upper()
    idem = f"bt:{job_no}"

    # 回测默认免费：本地计算成本≈0，收费只会把新手挡在门外。
    # 收费项留给 AI 解读、参数优化、观察列表日租。
    if settings.BACKTEST_FREE:
        cost, free_left = 0, -1          # -1 表示不限次
    else:
        with engine.connect() as conn:
            used_today = conn.execute(text(
                "SELECT COUNT(*) FROM backtest_jobs "
                "WHERE user_id=:u AND created_at > CURDATE()"
            ), {"u": uid}).scalar()
        free_left = max(0, settings.FREE_BACKTEST_PER_DAY - used_today)
        cost = 0 if free_left > 0 else PRICE["BACKTEST"]

    # ---- 先扣费（免费时跳过），失败退 ----
    with engine.begin() as conn:
        if cost > 0:
            svc.consume(conn, uid, cost, "BACKTEST", job_no, idem=idem,
                        remark=f"回测 {strat['name']} × {body.symbol_code}")
        conn.execute(text(
            "INSERT INTO backtest_jobs (job_no, user_id, strategy_id, symbol_code, "
            " start_date, end_date, params, points_cost, status, share_token) "
            "VALUES (:j, :u, :sid, :sym, :sd, :ed, :p, :c, 'RUNNING', :st)"
        ), {"j": job_no, "u": uid, "sid": strat["id"], "sym": body.symbol_code,
            "sd": bars[0].trade_date, "ed": bars[-1].trade_date,
            "p": json.dumps(params, ensure_ascii=False), "c": cost,
            "st": uuid.uuid4().hex[:16]})

    try:
        result = run_backtest(bars, strat["strategy_type"], params, filters=filters)
        payload = result.to_dict()
        payload.update({
            "strategy_name": strat["name"],
            "strategy_code": strat["code"],
            "strategy_type": strat["strategy_type"],
            "symbol_code": body.symbol_code,
            "params": params,                 # 前端画图与标题要用真实参数
            "filters": filters or [],
            "filters_desc": result.filters_desc,
            "data_cutoff": bars[-1].trade_date,
            "bar_count": len(bars),
            "latest_signal_explain": explain_signal(bars, strat["strategy_type"], params),
        })
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE backtest_jobs SET status='DONE', result_json=:r, finished_at=NOW() "
                "WHERE job_no=:j"
            ), {"r": json.dumps(payload, ensure_ascii=False), "j": job_no})
            conn.execute(text("UPDATE strategies SET use_count=use_count+1 WHERE id=:i"),
                         {"i": strat["id"]})
    except Exception as e:
        # 回测失败必须退积分，用户不该为我们的错误付费（免费单无需退）
        with engine.begin() as conn:
            if cost > 0:
                svc.refund(conn, uid, cost, source_idem=idem, remark="回测失败退回")
            conn.execute(text(
                "UPDATE backtest_jobs SET status='FAILED', error_msg=:e WHERE job_no=:j"
            ), {"e": str(e)[:300], "j": job_no})
        raise BizError(50001, f"回测执行失败，积分已退回：{e}")

    with engine.connect() as conn:
        balance = svc.get_balance(conn, uid)

    return ok({
        "job_no": job_no,
        "points_cost": cost,
        "is_free": cost == 0,
        # -1 表示不限次（全免费模式），不要减
        "free_left": (free_left - 1) if (cost == 0 and free_left > 0) else free_left,
        "balance": balance,
        "result": payload,
    })


@router.get("/jobs")
def jobs(page: int = 1, size: int = 10, user: dict = Depends(get_current_user)):
    page, size = max(1, page), min(50, max(1, size))
    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM backtest_jobs WHERE user_id=:u"), {"u": user["id"]}).scalar()
        rows = conn.execute(text(
            "SELECT j.job_no, j.symbol_code, j.status, j.points_cost, j.created_at, "
            "       s.name AS strategy_name "
            "FROM backtest_jobs j JOIN strategies s ON s.id=j.strategy_id "
            "WHERE j.user_id=:u ORDER BY j.id DESC LIMIT :n OFFSET :o"
        ), {"u": user["id"], "n": size, "o": (page - 1) * size}).mappings().all()
    return ok({"total": total,
               "items": [{**dict(r), "created_at": str(r["created_at"])} for r in rows]})


@router.get("/jobs/{job_no}/export")
def export_script(job_no: str, user: dict = Depends(get_current_user)):
    """
    把这次回测导出成可独立运行的 Python 脚本。
    用户下载后 pip install akshare pandas 就能自己跑，不被平台锁住。
    """
    from fastapi.responses import PlainTextResponse
    from ..services.exporter import build_script

    with engine.connect() as conn:
        job = conn.execute(text(
            "SELECT j.*, s.name AS strategy_name, s.strategy_type, s.code AS strategy_code "
            "FROM backtest_jobs j JOIN strategies s ON s.id=j.strategy_id "
            "WHERE j.job_no=:j AND j.user_id=:u"
        ), {"j": job_no, "u": user["id"]}).mappings().first()
    if not job:
        raise BizError(40407, "回测记录不存在")

    params = job["params"]
    if isinstance(params, str):
        params = json.loads(params)

    script = build_script({
        "strategy_type": job["strategy_type"],
        "strategy_name": job["strategy_name"],
        "params": params,
        "symbol_code": job["symbol_code"],
        "years": 3,
        "filters": [],
    })

    filename = f"{job['symbol_code']}_{job['strategy_code']}_{job_no}.py"
    return PlainTextResponse(
        script, media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/{job_no}/export-zip")
def export_zip(job_no: str, user: dict = Depends(get_current_user)):
    """
    把这次回测的净值曲线、逐笔交易、口径说明打成 zip 下载。
    全部在内存里拼，不落盘。
    """
    from fastapi import Response
    from urllib.parse import quote
    from ..services.data_export import backtest_zip

    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT job_no, symbol_code, params, result_json, strategy_id "
            "FROM backtest_jobs WHERE job_no=:j AND user_id=:u"
        ), {"j": job_no, "u": user["id"]}).mappings().first()
        if not r:
            raise BizError(40407, "回测记录不存在")
        strat = conn.execute(text(
            "SELECT name FROM strategies WHERE id=:i"), {"i": r["strategy_id"]}).first()

    result = r["result_json"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    if not result:
        raise BizError(40012, "这次回测没有保存明细数据（可能是失败的记录）")

    params = r["params"]
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}

    blob = backtest_zip({
        "result": {
            **result,
            "strategy_name": (strat[0] if strat else ""),
            "symbol_code": r["symbol_code"],
            "params": params,
        },
    })

    fname = f"回测_{r['symbol_code']}_{job_no}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f"attachment; filename={job_no}.zip; filename*=UTF-8''{quote(fname)}",
        },
    )


@router.get("/jobs/{job_no}")
def job_detail(job_no: str, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT * FROM backtest_jobs WHERE job_no=:j AND user_id=:u"
        ), {"j": job_no, "u": user["id"]}).mappings().first()
    if not r:
        raise BizError(40407, "回测记录不存在")
    d = dict(r)
    if isinstance(d.get("result_json"), str):
        d["result_json"] = json.loads(d["result_json"])
    d["created_at"] = str(d["created_at"])
    d["finished_at"] = str(d["finished_at"]) if d["finished_at"] else None
    return ok(d)
