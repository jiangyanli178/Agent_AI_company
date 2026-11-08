# -*- coding: utf-8 -*-
"""观察列表：保存策略配置，每日生成模拟信号"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..services.credits import CreditsService, PRICE

router = APIRouter(prefix="/deployments", tags=["观察列表"])


class CreateIn(BaseModel):
    strategy_id: int
    symbol_code: str
    params: dict = {}


@router.post("")
def create(body: CreateIn, user: dict = Depends(get_current_user)):
    svc = CreditsService(engine)
    with engine.connect() as conn:
        strat = conn.execute(text(
            "SELECT * FROM strategies WHERE id=:i AND is_public=1"
        ), {"i": body.strategy_id}).mappings().first()
    if not strat:
        raise BizError(40404, "策略不存在")

    params = json.loads(strat["default_params"]) if isinstance(strat["default_params"], str) else strat["default_params"]
    params.update(body.params or {})

    # 先插入拿到部署 ID，再用真实 ID 扣费，方便对账追溯。
    # 两步在同一事务里：扣费失败会整体回滚，不会留下未付费的观察项。
    with engine.begin() as conn:
        cur = conn.execute(text(
            "INSERT INTO deployments (user_id, strategy_id, symbol_code, params, points_per_day) "
            "VALUES (:u, :sid, :sym, :p, :ppd)"
        ), {"u": user["id"], "sid": body.strategy_id, "sym": body.symbol_code,
            "p": json.dumps(params, ensure_ascii=False), "ppd": PRICE["DEPLOY_DAY"]})
        dep_id = cur.lastrowid

        svc.consume(conn, user["id"], PRICE["DEPLOY_DAY"], "DEPLOY_DAY", str(dep_id),
                    idem=f"deploy:create:{dep_id}",
                    remark=f"加入观察 #{dep_id} {strat['name']} × {body.symbol_code}")
        balance = svc.get_balance(conn, user["id"])

    return ok({"id": dep_id, "status": "RUNNING", "balance": balance,
               "days_left": balance // PRICE["DEPLOY_DAY"]})


@router.get("")
def list_deployments(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT d.id, d.symbol_code, d.status, d.points_per_day, d.started_at, "
            "       s.name AS strategy_name, s.code AS strategy_code "
            "FROM deployments d JOIN strategies s ON s.id=d.strategy_id "
            "WHERE d.user_id=:u AND d.status != 'STOPPED' ORDER BY d.id DESC"
        ), {"u": user["id"]}).mappings().all()
        balance = CreditsService.get_balance(conn, user["id"])
    return ok({
        "balance": balance,
        "items": [{**dict(r), "started_at": str(r["started_at"]),
                   "days_left": balance // r["points_per_day"] if r["points_per_day"] else 0}
                  for r in rows],
    })


@router.post("/{dep_id}/{action}")
def change_status(dep_id: int, action: str, user: dict = Depends(get_current_user)):
    mapping = {"pause": "PAUSED", "resume": "RUNNING", "stop": "STOPPED"}
    if action not in mapping:
        raise BizError(40007, "不支持的操作")
    with engine.begin() as conn:
        cur = conn.execute(text(
            f"UPDATE deployments SET status=:s WHERE id=:i AND user_id=:u"
        ), {"s": mapping[action], "i": dep_id, "u": user["id"]})
        if cur.rowcount == 0:
            raise BizError(40409, "观察项不存在")
    return ok({"id": dep_id, "status": mapping[action]})


@router.get("/{dep_id}/signals")
def signals(dep_id: int, days: int = 30, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT s.trade_date, s.symbol_code, s.action, s.ref_price, s.position, s.reason "
            "FROM signals s JOIN deployments d ON d.id=s.deployment_id "
            "WHERE s.deployment_id=:i AND d.user_id=:u "
            "ORDER BY s.trade_date DESC LIMIT :n"
        ), {"i": dep_id, "u": user["id"], "n": min(100, days)}).mappings().all()
    return ok([{**dict(r), "trade_date": str(r["trade_date"]),
                "ref_price": float(r["ref_price"])} for r in rows])
