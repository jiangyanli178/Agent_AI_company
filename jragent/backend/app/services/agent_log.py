# -*- coding: utf-8 -*-
"""
Agent 运行日志：人事档案与事故报告的唯一数据源。

演示时的"故意失败重试"也靠这张表留证：
一条 FAILED/RETRY，一条 SUCCESS，评委能看到完整的处置过程。
"""
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

from sqlalchemy import text

from ..db import engine

log = logging.getLogger("app.agent")

# token 单价，用于估算每次 agent 运行的成本（与 credits.py 保持一致）
PRICE_IN_PER_M = 2.0
PRICE_OUT_PER_M = 8.0


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return round(tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M, 4)


def record(
    agent_code: str,
    task_type: str,
    status: str,
    *,
    biz_id: str = "",
    retry_count: int = 0,
    latency_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    output_digest: str = "",
    error_msg: str = "",
) -> None:
    """
    写一条运行日志。绝不让日志失败影响主流程，所以整体 try 住。
    status: SUCCESS / RETRY / DEGRADED / FAILED / ESCALATED
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO agent_runs (agent_code, task_type, biz_id, status, retry_count, "
                " latency_ms, tokens_in, tokens_out, cost_cny, output_digest, error_msg) "
                "VALUES (:a, :t, :b, :s, :rc, :lat, :ti, :to, :c, :od, :em)"
            ), {
                "a": agent_code, "t": task_type, "b": biz_id, "s": status,
                "rc": retry_count, "lat": latency_ms,
                "ti": tokens_in, "to": tokens_out,
                "c": estimate_cost(tokens_in, tokens_out),
                "od": (output_digest or "")[:300],
                "em": (error_msg or "")[:500],
            })
    except Exception as e:
        log.warning("agent_runs 写入失败（已忽略，不影响主流程）：%s", e)


@contextmanager
def track(agent_code: str, task_type: str, biz_id: str = ""):
    """
    计时上下文：
        with track("product", "run_backtest", job_no) as t:
            ...
        t["output_digest"] = "..."
    """
    start = time.time()
    ctx: Dict[str, Any] = {"output_digest": "", "error_msg": "", "retry_count": 0}
    try:
        yield ctx
    except Exception as e:
        record(agent_code, task_type, "FAILED", biz_id=biz_id,
               latency_ms=int((time.time() - start) * 1000),
               retry_count=ctx.get("retry_count", 0), error_msg=str(e))
        raise
    else:
        record(agent_code, task_type, "SUCCESS", biz_id=biz_id,
               latency_ms=int((time.time() - start) * 1000),
               retry_count=ctx.get("retry_count", 0),
               output_digest=ctx.get("output_digest", ""))


def incidents(limit: int = 20) -> list:
    """事故列表：FAILED 与 ESCALATED，演示材料直接从这里出"""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT agent_code, task_type, status, retry_count, latency_ms, "
            "       output_digest, error_msg, created_at "
            "FROM agent_runs WHERE status IN ('FAILED','ESCALATED','RETRY','DEGRADED') "
            "ORDER BY id DESC LIMIT :n"
        ), {"n": limit}).mappings().all()
    return [{**dict(r), "created_at": str(r["created_at"])} for r in rows]


def kpi() -> list:
    """人事档案：每个 agent 的绩效"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM v_agent_kpi")).mappings().all()
    return [dict(r) for r in rows]
