# -*- coding: utf-8 -*-
"""我的策略：收藏跑过的结果，随时回看"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok

router = APIRouter(prefix="/saved", tags=["我的策略"])


class SaveIn(BaseModel):
    job_no: str
    name: str = Field("", max_length=80)
    note: str = Field("", max_length=500)


class UpdateIn(BaseModel):
    name: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=500)


@router.post("")
def save(body: SaveIn, user: dict = Depends(get_current_user)):
    """把一次回测结果存进我的策略。同一次回测重复保存只更新名字和备注。"""
    uid = user["id"]
    with engine.begin() as conn:
        job = conn.execute(text(
            "SELECT * FROM backtest_jobs WHERE job_no=:j AND user_id=:u AND status='DONE'"
        ), {"j": body.job_no, "u": uid}).mappings().first()
        if not job:
            raise BizError(40407, "回测记录不存在或未完成")

        result = json.loads(job["result_json"]) if isinstance(job["result_json"], str) else job["result_json"]
        # 只留指标快照，不存整套曲线，省空间
        snapshot = {k: result.get(k) for k in (
            "total_return", "annual_return", "max_drawdown", "max_drawdown_principal",
            "max_drawdown_loss",
            "simple_return", "simple_annual_return", "simple_max_drawdown",
            "simple_max_drawdown_peak", "simple_max_drawdown_principal",
            "simple_max_drawdown_loss",
            "sharpe", "win_rate", "trade_count", "benchmark_return", "excess_return",
            "blocked_orders", "strategy_name", "symbol_code", "params", "data_cutoff",
        ) if k in result}

        name = body.name.strip() or f"{job['symbol_code']} · {result.get('strategy_name','')}"
        params = job["params"] if isinstance(job["params"], str) else json.dumps(job["params"], ensure_ascii=False)

        conn.execute(text(
            "INSERT INTO saved_strategies (user_id, name, strategy_id, symbol_code, params, "
            " job_no, snapshot, note) VALUES (:u,:n,:sid,:sym,:p,:j,:sn,:note) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), note=VALUES(note), "
            " snapshot=VALUES(snapshot)"
        ), {"u": uid, "n": name[:80], "sid": job["strategy_id"], "sym": job["symbol_code"],
            "p": params, "j": body.job_no, "sn": json.dumps(snapshot, ensure_ascii=False),
            "note": body.note[:500]})

        row = conn.execute(text(
            "SELECT id FROM saved_strategies WHERE user_id=:u AND job_no=:j"
        ), {"u": uid, "j": body.job_no}).scalar()

    return ok({"id": row, "name": name, "duplicated": False})


@router.get("")
def list_saved(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT s.id, s.name, s.symbol_code, s.job_no, s.note, s.snapshot, s.created_at, "
            "       st.name AS strategy_name, st.code AS strategy_code "
            "FROM saved_strategies s JOIN strategies st ON st.id=s.strategy_id "
            "WHERE s.user_id=:u ORDER BY s.created_at DESC"
        ), {"u": user["id"]}).mappings().all()

    items = []
    for r in rows:
        d = dict(r)
        snap = d.pop("snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        d["created_at"] = str(d["created_at"])
        d["snapshot"] = snap
        items.append(d)
    return ok({"total": len(items), "items": items})


@router.get("/{sid}")
def detail(sid: int, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT s.*, st.name AS strategy_name, st.code AS strategy_code, "
            "       st.strategy_type, st.default_params, st.param_schema "
            "FROM saved_strategies s JOIN strategies st ON st.id=s.strategy_id "
            "WHERE s.id=:i AND s.user_id=:u"
        ), {"i": sid, "u": user["id"]}).mappings().first()
    if not r:
        raise BizError(40411, "策略不存在")
    d = dict(r)
    for k in ("snapshot", "params", "default_params", "param_schema"):
        if isinstance(d.get(k), str):
            try: d[k] = json.loads(d[k])
            except Exception: pass
    d["created_at"] = str(d["created_at"])
    return ok(d)


@router.patch("/{sid}")
def update(sid: int, body: UpdateIn, user: dict = Depends(get_current_user)):
    sets, params = [], {"i": sid, "u": user["id"]}
    if body.name is not None:
        sets.append("name=:n"); params["n"] = body.name.strip()[:80]
    if body.note is not None:
        sets.append("note=:note"); params["note"] = body.note.strip()[:500]
    if not sets:
        raise BizError(40008, "没有要修改的内容")
    with engine.begin() as conn:
        cur = conn.execute(text(
            f"UPDATE saved_strategies SET {','.join(sets)} WHERE id=:i AND user_id=:u"
        ), params)
        if cur.rowcount == 0:
            raise BizError(40411, "策略不存在")
    return ok({"id": sid, "updated": True})


@router.delete("/{sid}")
def remove(sid: int, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        cur = conn.execute(text(
            "DELETE FROM saved_strategies WHERE id=:i AND user_id=:u"
        ), {"i": sid, "u": user["id"]})
        if cur.rowcount == 0:
            raise BizError(40411, "策略不存在")
    return ok({"id": sid, "deleted": True})
