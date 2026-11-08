"""
报告接口 —— AI 生成网页的最后一公里。

流程：
  1. AI 决定「页面长什么样」：标题、要哪几块、每块写什么、结论
  2. 这里用真实回测数据把它渲染成一份自包含 HTML，存到 reports/{token}.html
  3. 给一个公开链接，用户能看、能下载、能发人

为什么存成文件而不是每次实时渲染：
报告要能「发出去」并且长期可访问，用户可能明天才打开。
文件很小（几十 KB），不占多少空间；数据库只存元信息。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..services.context import context

router = APIRouter(prefix="/reports", tags=["策略报告"])

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "reports")


class BuildIn(BaseModel):
    # AI 决定的部分
    title: str = Field(default="策略体检报告", max_length=120)
    subtitle: str = Field(default="", max_length=200)
    sections: List[str] = Field(default_factory=lambda: ["kline", "equity", "metrics"])
    highlights: List[Dict[str, str]] = Field(default_factory=list)
    conclusion: str = Field(default="", max_length=3000)

    # 跑什么
    strategy_type: str = "ma_cross"
    symbol_code: str = "600519"
    years: int = 3
    params: Dict[str, Any] = Field(default_factory=dict)
    # 过滤条件（ADX / 量比 / RSI …）。
    # 之前没有这个字段，导致「加了 ADX 过滤的优化版」在报告里变成裸策略 ——
    # 图上写 +15.58%，结论里写 +45.58%，自相矛盾。
    filters: List[Dict[str, Any]] = Field(default_factory=list)
    # AI 自己写的补充区块（HTML 片段）。模板只提供骨架，
    # 想让 AI 加自定义图表就把它写在这里，服务端会注入真实数据到 window.__REPORT_DATA__
    custom_sections: List[Dict[str, str]] = Field(default_factory=list)
    include_hysteresis: bool = False


ALLOWED_SECTIONS = {"kline", "equity", "simple", "drawdown", "metrics", "trades", "hysteresis"}


def _ensure_dir():
    os.makedirs(REPORT_DIR, exist_ok=True)


def _path(token: str) -> str:
    # token 只允许 16 位十六进制，杜绝路径穿越
    if not token.isalnum() or len(token) > 32:
        raise BizError(40410, "报告不存在")
    return os.path.join(REPORT_DIR, f"{token}.html")


def _symbol_name(code: str) -> str:
    try:
        with engine.connect() as conn:
            r = conn.execute(text("SELECT name FROM symbols WHERE code=:c"),
                             {"c": code}).first()
        return r[0] if r else ""
    except Exception:
        return ""


def create_report(user_id: int, body: BuildIn) -> Dict[str, Any]:
    """供路由和 AI 工具共用：跑回测 → 渲染 HTML → 落盘 → 记录"""
    try:
        from ..services.lab import run_custom, validate_params, hysteresis_analysis
        from ..services.report_builder import build_html, describe_sections, safe_filename
    except ImportError:
        from app.services.lab import run_custom, validate_params, hysteresis_analysis
        from app.services.report_builder import build_html, describe_sections, safe_filename

    years = max(1, min(int(body.years), 10))
    bars = context.get_bars(body.symbol_code, years)
    if len(bars) < 60:
        raise BizError(40406, "该标的行情数据不足，生成不了报告")

    try:
        params = validate_params(body.strategy_type, body.params)
        result = run_custom(bars, body.strategy_type, params, filters=body.filters or None)
    except ValueError as e:
        raise BizError(40013, str(e))

    # 模块白名单，AI 传错名字不至于让整页崩
    sections = [s for s in (body.sections or []) if s in ALLOWED_SECTIONS]
    if not sections:
        sections = ["kline", "equity", "metrics"]

    hyst = None
    if body.include_hysteresis and body.strategy_type == "ma_cross":
        try:
            hyst = hysteresis_analysis(
                bars, {"fast": params.get("fast", 5), "slow": params.get("slow", 20)})
            if "hysteresis" not in sections:
                sections.append("hysteresis")
        except Exception:
            hyst = None

    spec = {
        "title": body.title or "策略体检报告",
        "subtitle": body.subtitle,
        "sections": sections,
        # 最多 6 条，每条别太长，版面会乱
        "highlights": [
            {"label": str(x.get("label") or "")[:20],
             # text 是标准键；value 是简写，别让它静默丢掉
             "text": str(x.get("text") or x.get("value") or "")[:200]}
            for x in (body.highlights or [])[:6]
        ],
        "conclusion": body.conclusion[:3000],
        # AI 自己写的区块：模板只提供骨架，这些原样插到正文后面。
        # 之前漏了这一行，AI 传了 custom_sections 也被丢掉，图根本不出现。
        "custom_sections": [
            {"title": str(x.get("title") or "")[:60],
             "html": str(x.get("html") or "")[:30000]}
            for x in (body.custom_sections or [])[:6]
        ],
    }

    html_text = build_html(spec, result, bars, hyst, _symbol_name(body.symbol_code))

    token = uuid.uuid4().hex[:16]
    _ensure_dir()
    with open(_path(token), "w", encoding="utf-8") as f:
        f.write(html_text)

    size = len(html_text.encode("utf-8"))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO strategy_reports "
            "(token, user_id, title, symbol_code, strategy, spec, params, size_bytes) "
            "VALUES (:t, :u, :ti, :sym, :st, :sp, :p, :sz)"
        ), {
            "t": token, "u": user_id, "ti": spec["title"][:120],
            "sym": body.symbol_code, "st": body.strategy_type,
            "sp": json.dumps(spec, ensure_ascii=False),
            "p": json.dumps(params, ensure_ascii=False),
            "sz": size,
        })

    return {
        "token": token,
        "url": f"/reports/{token}",
        "api_url": f"/api/reports/{token}",
        "title": spec["title"],
        "size_kb": round(size / 1024, 1),
        "sections": sections,
        # 实际渲染清单：AI 靠这个描述自己刚生成的东西，避免"模板做不到"这种错话
        "rendered": describe_sections(sections, result, hyst),
        "has_hysteresis": bool(hyst),
        "symbol_code": body.symbol_code,
        "hint": "点开链接就能看；也可以下载 HTML 存到本地，断网也能打开",
    }


@router.post("/build")
def build(body: BuildIn, user: dict = Depends(get_current_user)):
    """生成一份报告（前端直接调这个；AI 走 build_report 工具）"""
    return ok(create_report(user["id"], body))


@router.get("")
def my_reports(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT token, title, symbol_code, strategy, size_bytes, views, created_at "
            "FROM strategy_reports WHERE user_id=:u ORDER BY id DESC LIMIT 60"
        ), {"u": user["id"]}).mappings().all()
    return ok({
        "total": len(rows),
        "items": [{
            **dict(r),
            "created_at": str(r["created_at"]),
            "url": f"/reports/{r['token']}",
        } for r in rows],
    })


@router.get("/{token}")
def view(token: str, download: int = 0):
    """
    公开访问：报告是给用户发人用的，不要求登录。
    token 是 16 位随机串，猜不到。
    """
    p = _path(token)
    if not os.path.exists(p):
        raise BizError(40410, "报告不存在或已过期")

    with open(p, "r", encoding="utf-8") as f:
        html_text = f.read()

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE strategy_reports SET views = views + 1 WHERE token=:t"), {"t": token})
            row = conn.execute(text(
                "SELECT title FROM strategy_reports WHERE token=:t"), {"t": token}).first()
        title = row[0] if row else "策略体检报告"
    except Exception:
        title = "策略体检报告"

    headers = {}
    if download:
        fname = f"{title}.html"
        headers["Content-Disposition"] = (
            f"attachment; filename=report_{token}.html; "
            f"filename*=UTF-8''{quote(fname)}"
        )

    return HTMLResponse(content=html_text, headers=headers)


@router.get("/{token}/script")
def script(token: str):
    """报告里那个「下载策略脚本」，同一份参数导出的 .py"""
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT params, strategy, symbol_code FROM strategy_reports WHERE token=:t"
        ), {"t": token}).mappings().first()
    if not r:
        raise BizError(40410, "报告不存在")

    params = r["params"]
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}

    try:
        from ..services.exporter import build_script
    except ImportError:
        from app.services.exporter import build_script

    code = build_script({
        "strategy_type": r["strategy"],
        "strategy_name": f"{r['symbol_code']} 自定义配置",
        "params": params or {},
        "symbol_code": r["symbol_code"],
        "years": 3,
        "filters": [],
    })

    return Response(
        content=code.encode("utf-8"),
        media_type="text/x-python; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename=strategy_{token}.py; "
                f"filename*=UTF-8''{quote('strategy.py')}",
        },
    )


@router.delete("/{token}")
def remove(token: str, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        r = conn.execute(text(
            "SELECT id FROM strategy_reports WHERE token=:t AND user_id=:u"
        ), {"t": token, "u": user["id"]}).first()
        if not r:
            raise BizError(40410, "报告不存在")
        conn.execute(text("DELETE FROM strategy_reports WHERE token=:t"), {"t": token})
    try:
        os.remove(_path(token))
    except Exception:
        pass
    return ok({"deleted": True})
