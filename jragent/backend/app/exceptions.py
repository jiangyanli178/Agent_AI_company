# -*- coding: utf-8 -*-
"""
统一异常与响应格式。

约定：
  成功  {"code": 0, "data": ...}
  失败  {"code": 业务码, "msg": "人话", "data": {...}}

积分不足统一 HTTP 402，前端 axios 拦截器据此弹充值框。
"""
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from .services.credits import InsufficientCredits, CreditError, AccountNotFound


def ok(data: Any = None) -> dict:
    return {"code": 0, "data": data}


def fail(code: int, msg: str, data: Optional[dict] = None) -> dict:
    return {"code": code, "msg": msg, "data": data or {}}


class BizError(Exception):
    """
    业务异常，路由层直接 raise BizError(40410, '...') 即可。

    HTTP 状态码默认按业务码自动推断：
        40001 → 400，40100 → 401，40201 → 402，40410 → 404
    需要覆盖时显式传 status_code。
    """

    def __init__(self, code: int, msg: str, data: Optional[dict] = None,
                 status_code: Optional[int] = None):
        if status_code is None:
            inferred = code // 100
            status_code = inferred if 100 <= inferred < 600 else 400
        self.status_code = status_code
        self.code = code
        self.msg = msg
        self.data = data or {}
        super().__init__(msg)


async def insufficient_handler(request: Request, exc: InsufficientCredits):
    """积分不足 → HTTP 402，前端统一拦截"""
    return JSONResponse(
        status_code=402,
        content=fail(40201, f"积分不足：本次需要 {exc.need} 积分，当前余额 {exc.have}",
                     {"need": exc.need, "have": exc.have}),
    )


async def credit_error_handler(request: Request, exc: CreditError):
    return JSONResponse(status_code=400, content=fail(40010, str(exc)))


async def account_not_found_handler(request: Request, exc: AccountNotFound):
    return JSONResponse(status_code=404, content=fail(40401, str(exc)))


async def biz_error_handler(request: Request, exc: BizError):
    return JSONResponse(status_code=exc.status_code,
                        content=fail(exc.code, exc.msg, exc.data))


async def global_error_handler(request: Request, exc: Exception):
    # 不把内部堆栈暴露给用户，但要能在日志里查到
    import logging
    logging.getLogger("app").exception("未处理异常 %s %s", request.method, request.url)
    return JSONResponse(status_code=500, content=fail(50000, "服务器开小差了，请稍后再试"))


HANDLERS = [
    (InsufficientCredits, insufficient_handler),
    (AccountNotFound, account_not_found_handler),
    (CreditError, credit_error_handler),
    (BizError, biz_error_handler),
    (Exception, global_error_handler),
]
