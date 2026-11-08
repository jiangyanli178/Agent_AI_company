"""
我的因子库 —— 把对话里聊出来的因子组合收藏起来。

为什么要单独存：因子条件是一次搜索的产物，如果不存，
用户下次想复现「上次那套 ADX+量比 的组合」就找不回来了。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok

router = APIRouter(prefix="/saved-factors", tags=["我的因子库"])


class SaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    strategy_code: str = ""
    symbol_code: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    condition_text: str = ""
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class UpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    note: Optional[str] = Field(default=None, max_length=500)


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k in ("params", "conditions", "snapshot"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                d[k] = {} if k != "conditions" else []
    d["created_at"] = str(d.get("created_at") or "")
    return d


@router.post("")
def create(body: SaveIn, user: dict = Depends(get_current_user)):
    if not body.conditions:
        raise BizError(40010, "至少要有一个因子条件才能保存")

    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT id FROM saved_factor_sets WHERE user_id=:u AND name=:n"
        ), {"u": user["id"], "n": body.name}).first()

        if exists:
            # 同名视为「覆盖更新」，不报错也不重复占位
            conn.execute(text(
                "UPDATE saved_factor_sets SET strategy_code=:sc, symbol_code=:sym, "
                "params=:p, conditions=:c, condition_text=:ct, snapshot=:sn, note=:note "
                "WHERE id=:i"
            ), {"sc": body.strategy_code, "sym": body.symbol_code,
                "p": json.dumps(body.params, ensure_ascii=False),
                "c": json.dumps(body.conditions, ensure_ascii=False),
                "ct": body.condition_text,
                "sn": json.dumps(body.snapshot, ensure_ascii=False),
                "note": body.note, "i": exists[0]})
            new_id = exists[0]
        else:
            cur = conn.execute(text(
                "INSERT INTO saved_factor_sets "
                "(user_id, name, strategy_code, symbol_code, params, conditions, "
                " condition_text, snapshot, note) "
                "VALUES (:u, :n, :sc, :sym, :p, :c, :ct, :sn, :note)"
            ), {"u": user["id"], "n": body.name, "sc": body.strategy_code,
                "sym": body.symbol_code,
                "p": json.dumps(body.params, ensure_ascii=False),
                "c": json.dumps(body.conditions, ensure_ascii=False),
                "ct": body.condition_text,
                "sn": json.dumps(body.snapshot, ensure_ascii=False),
                "note": body.note})
            new_id = cur.lastrowid

    return ok({"id": new_id, "name": body.name, "overwritten": bool(exists)})


@router.get("")
def list_all(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, strategy_code, symbol_code, params, conditions, "
            "condition_text, snapshot, note, created_at "
            "FROM saved_factor_sets WHERE user_id=:u ORDER BY created_at DESC LIMIT 100"
        ), {"u": user["id"]}).mappings().all()
    items = [_row(r) for r in rows]
    return ok({"total": len(items), "items": items})


@router.get("/{sid}")
def detail(sid: int, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM saved_factor_sets WHERE id=:i AND user_id=:u"
        ), {"i": sid, "u": user["id"]}).mappings().first()
    if not row:
        raise BizError(40410, "没有找到这套因子")
    return ok(_row(row))


@router.patch("/{sid}")
def update(sid: int, body: UpdateIn, user: dict = Depends(get_current_user)):
    sets, vals = [], {"i": sid, "u": user["id"]}
    if body.name is not None:
        sets.append("name=:n")
        vals["n"] = body.name
    if body.note is not None:
        sets.append("note=:note")
        vals["note"] = body.note
    if not sets:
        return ok({"updated": False})

    with engine.begin() as conn:
        conn.execute(text(
            f"UPDATE saved_factor_sets SET {', '.join(sets)} WHERE id=:i AND user_id=:u"
        ), vals)
    return ok({"updated": True})


@router.delete("/{sid}")
def remove(sid: int, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM saved_factor_sets WHERE id=:i AND user_id=:u"
        ), {"i": sid, "u": user["id"]})
    return ok({"deleted": True})
