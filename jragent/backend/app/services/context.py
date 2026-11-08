# -*- coding: utf-8 -*-
"""ToolContext 的 MySQL 实现：把 agent_tools 的工具接到真实数据库上"""
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from ..db import engine
from . import kb as kb_service
from .agent_tools import ToolContext
from .backtest import Bar


class MySQLToolContext(ToolContext):

    def search_strategies(self, category=None, difficulty=None, keyword=None, limit=3):
        where, params = ["is_public=1"], {}
        if category:
            where.append("category=:c"); params["c"] = category
        if difficulty:
            where.append("difficulty=:d"); params["d"] = difficulty
        if keyword:
            where.append("(name LIKE :k OR summary LIKE :k OR tags LIKE :k)")
            params["k"] = f"%{keyword}%"
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT code, name, category, summary, difficulty FROM strategies "
                f"WHERE {' AND '.join(where)} ORDER BY difficulty, use_count DESC LIMIT :n"
            ), {**params, "n": min(5, max(1, limit))}).mappings().all()
        return [dict(r) for r in rows]

    def get_strategy(self, code: str) -> Optional[Dict[str, Any]]:
        with engine.connect() as conn:
            r = conn.execute(text(
                "SELECT * FROM strategies WHERE code=:c AND is_public=1"
            ), {"c": code}).mappings().first()
        if not r:
            return None
        d = dict(r)
        # filters 也必须解析：公共库里有「RSI + ADX>20.5」这种带条件的策略，
        # 不解析就会丢掉条件，下游跑出来的是裸策略。
        for k in ("param_schema", "default_params", "filters"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    d[k] = None
        return d

    def get_bars(self, symbol: str, years: int) -> List[Bar]:
        years = min(5, max(1, years))
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
                "FROM daily_bars WHERE symbol_code=:c "
                "AND trade_date >= DATE_SUB((SELECT MAX(trade_date) FROM daily_bars "
                "                           WHERE symbol_code=:c), INTERVAL :y YEAR) "
                "ORDER BY trade_date"
            ), {"c": symbol, "y": years}).all()
        return [Bar(str(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in rows]

    def list_symbols(self, keyword=None, industry=None, limit=10):
        where, params = ["active=1"], {}
        if keyword:
            where.append("(code LIKE :k OR name LIKE :k)"); params["k"] = f"%{keyword}%"
        if industry:
            where.append("industry=:i"); params["i"] = industry
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT code, name, industry FROM symbols "
                f"WHERE {' AND '.join(where)} ORDER BY code LIMIT :n"
            ), {**params, "n": min(20, max(1, limit))}).mappings().all()
        return [dict(r) for r in rows]

    def search_kb(self, query: str):
        return kb_service.search(query, limit=3)


# 全局单例，工具循环直接拿来用
context = MySQLToolContext()
