"""支付模块 HTTP DTO。

输入 DTO 继承 ``StrictDto``（额外字段直接拒绝）：支付创建接口**不接收商户号或金额**
（06 文档「支付」），客户端只能提交渠道与场景。
"""

from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import Dto, Page, StrictDto


class PaymentAttemptCreateRequest(StrictDto):
    """``POST /installments/{id}/payment-attempts`` 输入。

    不接受 ``merchant_order_no``、``amount_cents`` 等字段：金额由服务端按应付项
    余额计算，商户单号由服务端在下单前生成并落库。
    """

    provider: str = Field(min_length=2, max_length=20)
    scene: str = Field(default="native", min_length=2, max_length=20)


class PaymentAction(Dto):
    """渠道返回的下一步动作：二维码、跳转或「无动作（处理中）」。"""

    type: str
    url: str | None = None


class PaymentAttemptResponse(Dto):
    payment_attempt_id: str
    status: str
    action: PaymentAction
    expires_at: str | None = None


class PaymentAttemptStatusResponse(Dto):
    """``GET /payment-attempts/{id}``：本地权威状态。"""

    id: str
    status: str
    order_id: str
    verified_at: str | None = None


class PaymentAttemptListItem(Dto):
    id: str
    provider: str
    scene: str
    status: str
    expected_cents: int
    currency: str
    action_type: str | None = None
    created_at: str | None = None
    verified_at: str | None = None


PaymentAttemptPage = Page[PaymentAttemptListItem]


__all__ = [
    "PaymentAction",
    "PaymentAttemptCreateRequest",
    "PaymentAttemptListItem",
    "PaymentAttemptPage",
    "PaymentAttemptResponse",
    "PaymentAttemptStatusResponse",
]
