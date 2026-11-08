# -*- coding: utf-8 -*-
"""知识库检索：MySQL ngram 全文索引 + 关键词兜底"""
from typing import Any, Dict, List

from sqlalchemy import text

from ..db import engine


def search(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """
    中文全文检索。ngram_token_size=2，对"金叉""回撤"这类两字词正好合适。
    全文索引查不到时退回 LIKE，避免短词被漏掉。
    """
    q = (query or "").strip()
    if not q:
        return []

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, category, title, content, related_strategy_code, "
            "       MATCH(title, content) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score "
            "FROM kb_documents "
            "WHERE active=1 AND MATCH(title, content) AGAINST (:q IN NATURAL LANGUAGE MODE) "
            "ORDER BY score DESC LIMIT :n"
        ), {"q": q, "n": limit}).mappings().all()

        if not rows:
            # 兜底：全文索引对极短词或纯标点查询可能直接返回空
            rows = conn.execute(text(
                "SELECT id, category, title, content, related_strategy_code, 0 AS score "
                "FROM kb_documents "
                "WHERE active=1 AND (title LIKE :k OR content LIKE :k OR keywords LIKE :k) "
                "ORDER BY sort_no LIMIT :n"
            ), {"k": f"%{q}%", "n": limit}).mappings().all()

    return [
        {
            "id": r["id"],
            "category": r["category"],
            "title": r["title"],
            "content": r["content"],
            "related_strategy_code": r["related_strategy_code"],
        }
        for r in rows
    ]


def list_by_category(category: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        if category:
            rows = conn.execute(text(
                "SELECT id, category, title, sort_no FROM kb_documents "
                "WHERE active=1 AND category=:c ORDER BY sort_no LIMIT :n"
            ), {"c": category, "n": limit}).mappings().all()
        else:
            rows = conn.execute(text(
                "SELECT id, category, title, sort_no FROM kb_documents "
                "WHERE active=1 ORDER BY category, sort_no LIMIT :n"
            ), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]
