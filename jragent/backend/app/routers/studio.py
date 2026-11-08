# -*- coding: utf-8 -*-
"""量化研究工作室 API（原 studio/webapp.py 独立服务，已并入统一后端）。

前端页面：frontend/public/studio/index.html（同一网站内 /studio/ 路径）
API 前缀：/api/studio/*（与体检所业务 API 共用 8000 端口，路径前缀隔离）

  GET  /api/studio/state            订单台账 + 支付记录 + 下一订单号
  GET  /api/studio/report?order=&stock=   report.md（Markdown 原文）
  GET  /api/studio/chart?order=&name=&stock=  订单图表 PNG
  GET  /api/studio/log              07-log.md 留痕尾部
  POST /api/studio/checkout         生成收款单（模拟微信扫码支付）
  POST /api/studio/pay_confirm      确认扫码支付结果
  POST /api/studio/run              跑订单闭环（支付闸门：先付款后出结果）
  POST /api/studio/weekly           生成经营周报（闸门⑧）
  POST /api/studio/check_scope      越界请求拦截检测
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body
from fastapi.responses import Response

STUDIO_DIR = Path(__file__).resolve().parents[3] / "studio"
sys.path.insert(0, str(STUDIO_DIR))  # 复用 demo.py 的 CHARTER（公司宪章）
sys.path.insert(0, str(STUDIO_DIR.parent / "backend"))  # opc_quant_studio 包

from demo import CHARTER  # noqa: E402  复用公司宪章（红线置顶）
from opc_quant_studio.compliance import reject_out_of_scope  # noqa: E402
from opc_quant_studio.context_hub import ContextHub  # noqa: E402
from opc_quant_studio.gates import GateKeeper  # noqa: E402
from opc_quant_studio.orchestrator import COO, AuditRejected  # noqa: E402

router = APIRouter(prefix="/api/studio", tags=["量化研究工作室"])

_run_lock = threading.Lock()  # 同时只跑一单
_coo: Optional[COO] = None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_coo() -> COO:
    global _coo
    if _coo is None:
        hub = ContextHub(STUDIO_DIR / "context_hub")
        hub.write("00-charter.md", CHARTER)  # 阶段 0：宪章固化（幂等）
        gates = GateKeeper(hub, mode="auto")  # 闸门自动通过并留痕；生产用 mode="manual"
        _coo = COO(hub, gates, work_dir=STUDIO_DIR / "outputs")
    return _coo


def _next_order_id() -> str:
    nums = [0]
    ledger_path = STUDIO_DIR / "context_hub" / "orders.json"
    if ledger_path.exists():
        for o in json.loads(ledger_path.read_text(encoding="utf-8")):
            m = re.match(r"ORD-(\d+)", str(o.get("order_id", "")))
            if m:
                nums.append(int(m.group(1)))
    if (STUDIO_DIR / "outputs").exists():
        for p in (STUDIO_DIR / "outputs").glob("ORD-*"):
            m = re.match(r"ORD-(\d+)", p.name)
            if m:
                nums.append(int(m.group(1)))
    return f"ORD-{max(nums) + 1:03d}"


# ---------------- 模拟支付闸门（微信扫码支付模拟） ----------------
_PAY_PATH = STUDIO_DIR / "context_hub" / "payments.json"
_pay_lock = threading.Lock()


def _load_payments() -> list:
    if _PAY_PATH.exists():
        return json.loads(_PAY_PATH.read_text(encoding="utf-8"))
    return []


def _save_payments(payments: list) -> None:
    _PAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PAY_PATH.write_text(json.dumps(payments, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_pay_id() -> str:
    nums = [0]
    for p in _load_payments():
        m = re.match(r"PAY-(\d+)", str(p.get("pay_id", "")))
        if m:
            nums.append(int(m.group(1)))
    return f"PAY-{max(nums) + 1:03d}"


def _find_payment(pay_id: str) -> Optional[dict]:
    for p in _load_payments():
        if p.get("pay_id") == pay_id:
            return p
    return None


def _json_default(o):
    """numpy 标量 → Python 原生类型（json 序列化用）。"""
    return o.item() if hasattr(o, "item") else str(o)


def _json(obj, code: int = 200) -> Response:
    body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
    return Response(content=body, status_code=code,
                    media_type="application/json; charset=utf-8")


# ---------------- GET ----------------
@router.get("/state")
def get_state():
    ledger_path = STUDIO_DIR / "context_hub" / "orders.json"
    orders = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else []
    return _json({"orders": orders, "next_order_id": _next_order_id(),
                  "payments": _load_payments()})


@router.get("/report")
def get_report(order: str = "", stock: str = ""):
    order = Path(order).name
    stock = re.sub(r"[^\w.-]", "_", Path(stock).name)  # 与资产子目录同口径
    base = STUDIO_DIR / "outputs" / order
    p = base / stock / "report.md" if stock else base / "report.md"
    if not p.exists() and not stock:
        p = base / "portfolio_report.md"  # 多股票订单：主报告=组合报告
    if not p.exists():
        return _json({"error": f"{order} 报告不存在"}, 404)
    return _json({"order": order, "stock": stock, "markdown": p.read_text(encoding="utf-8")})


@router.get("/chart")
def get_chart(order: str = "", name: str = "", stock: str = ""):
    order = Path(order).name
    name = Path(name).name
    stock = re.sub(r"[^\w.-]", "_", Path(stock).name)
    base = STUDIO_DIR / "outputs" / order / stock if stock else STUDIO_DIR / "outputs" / order
    p = base / name
    if p.exists() and p.suffix.lower() == ".png":
        return Response(content=p.read_bytes(), media_type="image/png")
    return Response(content=b"not found", status_code=404,
                    media_type="text/plain; charset=utf-8")


@router.get("/log")
def get_log():
    log = STUDIO_DIR / "context_hub" / "07-log.md"
    text = log.read_text(encoding="utf-8") if log.exists() else "（暂无留痕）"
    return _json({"text": text[-5000:]})


# ---------------- POST ----------------
@router.post("/checkout")
def checkout(payload: Optional[dict] = Body(default=None)):
    data = payload or {}
    # 生成收款单（模拟微信扫码支付）：先建收款单，扫码确认支付成功后才可履约
    with _pay_lock:
        pay_id = _next_pay_id()
        amount = 10  # 报价金额统一固定为 10 元（固定值，忽略入参）
        record = {
            "pay_id": pay_id,
            "order_id": _next_order_id(),  # 预分配订单号，履约时正式入账
            "client": (data.get("client") or "客户A").strip(),
            "symbol": (data.get("symbol") or "600519.SS").strip(),
            "idea": (data.get("idea") or "均线交叉 10/40 + 止损 5%").strip(),
            "amount": amount,
            "period": (data.get("period") or "2y").strip(),
            "product": "标准策略回测报告",
            "created_at": _now(),
            "paid": False,
            "paid_at": None,
            "success": False,
            "status": "待支付",
            "fulfilled_order_id": None,
        }
        payments = _load_payments()
        payments.append(record)
        _save_payments(payments)
    # 闸门⑥留痕：收款单生成
    log_path = STUDIO_DIR / "context_hub" / "07-log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n- [{_now()}] 闸门⑥ 收款单生成：{pay_id} / 预订单 {record['order_id']} / "
                f"{record['client']} / ¥{amount} / 状态=待支付（模拟微信扫码支付）")
    return _json({"ok": True, "payment": record})


@router.post("/pay_confirm")
def pay_confirm(payload: Optional[dict] = Body(default=None)):
    data = payload or {}
    # 确认扫码支付结果：记录支付时间/是否成功/状态，闸门⑥留痕
    pay_id = (data.get("pay_id") or "").strip()
    success = bool(data.get("success"))
    with _pay_lock:
        payments = _load_payments()
        target = next((p for p in payments if p.get("pay_id") == pay_id), None)
        if target is None:
            return _json({"ok": False, "error": f"收款单 {pay_id} 不存在"}, 404)
        if target.get("paid"):
            return _json({"ok": False, "error": f"收款单 {pay_id} 已支付，请勿重复确认"}, 409)
        target["paid"] = True
        target["paid_at"] = _now()
        target["success"] = success
        target["status"] = "支付成功" if success else "支付失败"
        _save_payments(payments)
    log_path = STUDIO_DIR / "context_hub" / "07-log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n- [{target['paid_at']}] 闸门⑥ 扫码支付确认：{pay_id} / ¥{target['amount']} / "
                f"是否成功={'是' if success else '否'} / 状态={target['status']}")
    return _json({"ok": True, "payment": target})


@router.post("/run")
def run_order(payload: Optional[dict] = Body(default=None)):
    data = payload or {}
    # 支付闸门：收款单必须「支付成功」才开始回测（先付款后出结果）
    pay_id = (data.get("pay_id") or "").strip()
    payment = _find_payment(pay_id)
    if payment is None:
        return _json({"ok": False, "error": "请先生成收款单并完成扫码支付"}, 400)
    if not (payment.get("paid") and payment.get("success")):
        return _json({"ok": False, "error": f"收款单 {pay_id} 未支付成功（当前：{payment.get('status')}），支付后才能开始回测",
                      "payment": payment}, 402)
    if payment.get("fulfilled_order_id"):
        return _json({"ok": False, "error": f"收款单 {pay_id} 已履约（订单 {payment['fulfilled_order_id']}），请勿重复提交",
                      "payment": payment}, 409)
    if not _run_lock.acquire(blocking=False):
        return _json({"ok": False, "error": "已有订单正在履约中，请稍候再试"}, 429)
    try:
        receipt = _get_coo().run_first_order(
            idea=payment["idea"],
            symbol=payment["symbol"],
            client=payment["client"],
            order_id=payment["order_id"],  # 用预分配订单号，保持收款单与订单一致
            amount=payment["amount"],
            period=payment["period"],
            demo_retry=False,
        )
        # 回填履约结果到支付记录
        with _pay_lock:
            payments = _load_payments()
            target = next((p for p in payments if p.get("pay_id") == pay_id), None)
            if target is not None:
                target["fulfilled_order_id"] = receipt.get("order_id")
                target["status"] = "已履约"
                _save_payments(payments)
        return _json({"ok": True, "receipt": receipt})
    except AuditRejected as e:  # 业务结果：审计否决 → 升级老板（携带审计明细）
        return _json({"ok": False, "stage": "风控审计",
                      "error": str(e), "audit": e.audit})
    except Exception as e:  # 流程断言失败（闸门/评审/合规）如实报给前端
        return _json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
    finally:
        _run_lock.release()


@router.post("/weekly")
def weekly():
    if not _run_lock.acquire(blocking=False):
        return _json({"ok": False, "error": "履约进行中，请稍候"}, 429)
    try:
        text = _get_coo().weekly_report()
        return _json({"ok": True, "markdown": text})
    except Exception as e:
        return _json({"ok": False, "error": str(e)}, 500)
    finally:
        _run_lock.release()


@router.post("/check_scope")
def check_scope(payload: Optional[dict] = Body(default=None)):
    data = payload or {}
    msg = (data.get("message") or "").strip()
    return _json({"ok": True, "message": msg, "blocked": bool(reject_out_of_scope(msg))})
