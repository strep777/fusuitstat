"""统一错误协议。

响应形状固定为 ``{"error": {"code", "message", "fields", "trace_id"}}``。
前端按 ``code`` 分支，不对 ``message`` 做逻辑判断（见 06 文档）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.platform.ids import new_id

# 稳定业务错误码（06 文档"稳定业务错误码"）。
# 状态码直接用整数常量，避免依赖 starlette 版本间的常量改名。
_S_400 = 400
_S_401 = 401
_S_403 = 403
_S_404 = 404
_S_405 = 405
_S_409 = 409
_S_413 = 413
_S_422 = 422
_S_429 = 429
_S_500 = 500
_S_503 = 503

ERROR_CODES: dict[str, int] = {
    "VALIDATION_ERROR": _S_422,
    "AUTH_REQUIRED": _S_401,
    "AUTH_FAILED": _S_401,
    "SESSION_EXPIRED": _S_401,
    "REAUTH_REQUIRED": _S_401,
    "CSRF_INVALID": _S_403,
    "FORBIDDEN": _S_403,
    "RESOURCE_NOT_FOUND": _S_404,
    "METHOD_NOT_ALLOWED": _S_405,
    "VERSION_CONFLICT": _S_409,
    "STATE_CONFLICT": _S_409,
    "IDEMPOTENCY_CONFLICT": _S_409,
    "SMS_RATE_LIMITED": _S_429,
    "RATE_LIMITED": _S_429,
    "SMS_CODE_INVALID": _S_400,
    "SMS_CODE_EXPIRED": _S_400,
    "SMS_CHALLENGE_USED": _S_400,
    "SMS_BUDGET_EXHAUSTED": _S_503,
    "UPLOAD_NOT_READY": _S_409,
    "UPLOAD_REJECTED": _S_422,
    "UPLOAD_LIMIT_EXCEEDED": _S_413,
    "PAYLOAD_TOO_LARGE": _S_413,
    "ROBOT_DENIED": _S_403,
    "SERVER_BUSY": _S_503,
    "QUOTE_EXPIRED": _S_409,
    "QUOTE_CHANGED": _S_409,
    "SCHEDULE_CLOSED": _S_409,
    "NO_CAPACITY": _S_409,
    "STOCK_NOT_ENOUGH": _S_409,
    "MERCH_DISPLAY_ONLY": _S_409,
    "PRICE_CHANGED": _S_409,
    "CHECKOUT_TOKEN_INVALID": _S_409,
    "PAYMENT_PENDING": _S_409,
    "PAYMENT_UNKNOWN": _S_409,
    "PAYMENT_PROVIDER_DISABLED": _S_503,
    "REFUND_LIMIT_EXCEEDED": _S_409,
    # C02/C24：该区域没有该币种定价 → 不按汇率折算，明确拒绝（422 让客户端按字段提示引导用户）。
    "CURRENCY_NOT_SUPPORTED": _S_422,
    "BOOKING_DATE_INVALID": _S_422,
    "POINTS_INSUFFICIENT": _S_409,
    "FEEDBACK_DUPLICATE": _S_409,
    "FEEDBACK_LIMIT_EXCEEDED": _S_429,
    "FEATURE_DISABLED": _S_403,
    "PROVIDER_UNAVAILABLE": _S_503,
    "INTERNAL_ERROR": _S_500,
}

# 面向用户的中文提示；不含 SQL、内部路径或第三方原始响应。
DEFAULT_MESSAGES: dict[str, str] = {
    "VALIDATION_ERROR": "提交的内容不符合要求，请检查后重试。",
    "AUTH_REQUIRED": "请先登录后再继续。",
    "AUTH_FAILED": "登录信息无效，请重新登录。",
    "SESSION_EXPIRED": "登录状态已过期，请重新登录。",
    "REAUTH_REQUIRED": "该操作需要重新验证身份，请重新输入验证码。",
    "CSRF_INVALID": "请求来源校验失败，请刷新页面后重试。",
    "FORBIDDEN": "当前账号没有执行该操作的权限。",
    "RESOURCE_NOT_FOUND": "请求的内容不存在或已下架。",
    "METHOD_NOT_ALLOWED": "请求方式不被支持。",
    "VERSION_CONFLICT": "内容已被他人更新，请刷新后重新提交。",
    "STATE_CONFLICT": "当前状态不允许该操作。",
    "IDEMPOTENCY_CONFLICT": "相同的请求编号已用于不同的请求内容。",
    "SMS_RATE_LIMITED": "发送过于频繁，请稍后再试。",
    "RATE_LIMITED": "操作过于频繁，请稍后再试。",
    "SMS_CODE_INVALID": "验证码不正确。",
    "SMS_CODE_EXPIRED": "验证码已过期，请重新获取。",
    "SMS_CHALLENGE_USED": "该验证码已被使用，请重新获取。",
    "SMS_BUDGET_EXHAUSTED": "短信通道暂时不可用，请稍后重试或联系客服。",
    "UPLOAD_NOT_READY": "文件尚未处理完成，请稍后重试。",
    "UPLOAD_REJECTED": "文件未通过安全检查，请更换文件。",
    "UPLOAD_LIMIT_EXCEEDED": "文件超出大小或数量限制。",
    "PAYLOAD_TOO_LARGE": "请求内容过大，请减少提交的数据后重试。",
    "ROBOT_DENIED": "该访问方式不被允许。",
    "SERVER_BUSY": "当前访问量过大，请稍后重试。",
    "BOOKING_DATE_INVALID": "期望日期需在北京时间今日 2 天后至 60 天内，请重新选择。",
    "QUOTE_EXPIRED": "报价已过期，请重新申请。",
    "QUOTE_CHANGED": "报价已更新，请查看最新版本后重新确认。",
    "SCHEDULE_CLOSED": "该档期当前未开放申请。",
    "NO_CAPACITY": "该档期名额已满。",
    "STOCK_NOT_ENOUGH": "库存不足，请调整数量。",
    "MERCH_DISPLAY_ONLY": "周边目前仅展示，暂不售卖。",
    "PRICE_CHANGED": "价格已变化，请确认最新金额。",
    "CHECKOUT_TOKEN_INVALID": "结算信息已失效，请重新结算。",
    "PAYMENT_PENDING": "支付结果确认中，请稍后查询。",
    "PAYMENT_UNKNOWN": "支付结果尚未确认，请稍后查询，不要重复支付。",
    "PAYMENT_PROVIDER_DISABLED": "该支付方式当前不可用。",
    "REFUND_LIMIT_EXCEEDED": "可退金额不足，请核对已退款金额。",
    "POINTS_INSUFFICIENT": "星绒币不足。",
    "CURRENCY_NOT_SUPPORTED": "该收货区域与本单商品的定价币种不一致，本站不按汇率折算。",
    "FEEDBACK_DUPLICATE": "该反馈已提交过，可在列表中查看进度。",
    "FEEDBACK_LIMIT_EXCEEDED": "提交过于频繁，请稍后再试。",
    "FEATURE_DISABLED": "该功能尚未开放。",
    "PROVIDER_UNAVAILABLE": "外部服务暂时不可用，请稍后重试。",
    "INTERNAL_ERROR": "服务暂时不可用，请稍后重试。",
}


class AppError(Exception):
    """带稳定错误码的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        fields: dict[str, Any] | None = None,
        http_status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"未登记的错误码：{code}")
        super().__init__(message or DEFAULT_MESSAGES.get(code, "操作失败。"))
        self.code = code
        self.message = message or DEFAULT_MESSAGES.get(code, "操作失败。")
        self.fields = fields or {}
        self.http_status = http_status or ERROR_CODES[code]
        self.headers = headers or {}


def error_response(
    code: str,
    message: str | None = None,
    *,
    fields: dict[str, Any] | None = None,
    http_status: int | None = None,
    trace_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    status_code = http_status or ERROR_CODES.get(code, 500)
    body = {
        "error": {
            "code": code,
            "message": message or DEFAULT_MESSAGES.get(code, "操作失败。"),
            "fields": fields or {},
            "trace_id": trace_id or new_id("job"),
        }
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def _trace_id(request: Request) -> str:
    trace = getattr(request.state, "trace_id", None)
    if not trace:
        trace = new_id("job")
        request.state.trace_id = trace
    return str(trace)


def install_error_handlers(app: FastAPI) -> None:
    """注册全局异常处理器，保证错误形状统一。"""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            exc.code,
            exc.message,
            fields=exc.fields,
            http_status=exc.http_status,
            trace_id=_trace_id(request),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part not in ("body", "query"))
            fields[location or "request"] = str(item.get("msg", "invalid"))
        return error_response(
            "VALIDATION_ERROR",
            fields=fields,
            trace_id=_trace_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: "AUTH_REQUIRED",
            403: "FORBIDDEN",
            404: "RESOURCE_NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "STATE_CONFLICT",
            413: "UPLOAD_LIMIT_EXCEEDED",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
        }
        code = mapping.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "STATE_CONFLICT")
        headers = dict(getattr(exc, "headers", None) or {})
        return error_response(code, http_status=exc.status_code, trace_id=_trace_id(request), headers=headers)
