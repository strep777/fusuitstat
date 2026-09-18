"""支付适配器公共契约。

``PaymentGateway`` 是渠道无关的最小能力面（08 文档）：create/query/close/refund/
query_refund/download_bill/verify_notification，另加账单解析、对账账户标识与
回调应答格式。供应商原始报文不进入业务记录、不返回给用户（06 文档）。

关键安全约定：

1. **成功只认验签通知或服务端查单**：``verify_notification`` 只在签名/证书/时间戳/
   解密全部通过时才返回 ``VerifiedPayment``，其余情况返回 ``None`` 并给出结构化
   ``NotificationVerdict`` 供入口记安全事件。适配器绝不「为了联调」伪造成功。
2. **未配置凭据 fail closed**：抛 ``app.adapters.base.GatewayNotConfigured``，
   绝不静默降级到沙箱（生产环境 ``PAYMENT_PROVIDERS`` 也不允许包含 mock）。
3. **保留验签所需原始字节**：``verify_notification`` 收到的是原始 ``bytes``，
   摘要与原文留存由 ``app.modules.payments.notifications`` 落盘，适配器只报告
   ``raw_digest``。
4. **业务核对分层**：适配器负责密码学与结构核对（并把渠道侧的商户/应用标识放进
   ``VerifiedPayment.account_ref``）；商户/币种/金额/订单号的业务核对在支付服务层
   的写事务内完成，未通过者隔离告警而不是丢弃（08 文档、TC-P04）。

签名算法与证书模式（微信 APIv3 平台证书、支付宝 RSA2/公钥与证书模式、PayPal
Webhook 证书校验）必须以商户当前官方文档为准；本文件中的参数名与流程需在开通
渠道时复核。
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.modules.contracts import VerifiedPayment

#: 场景（06 文档：scene 可支持 h5/jsapi，action 内容取决于真实已开通渠道）。
PAYMENT_SCENES = ("native", "h5", "jsapi", "web", "app")

ACTION_QR = "qr"
ACTION_REDIRECT = "redirect"
ACTION_NONE = "none"

#: 渠道无关的收款结论。
PAYMENT_RESULT_SUCCEEDED = "SUCCEEDED"
PAYMENT_RESULT_PENDING = "PENDING"
PAYMENT_RESULT_CLOSED = "CLOSED"
PAYMENT_RESULT_FAILED = "FAILED"
PAYMENT_RESULT_NOT_FOUND = "NOT_FOUND"
PAYMENT_RESULT_UNKNOWN = "UNKNOWN"

#: 退款结论（REQUESTED/APPROVED 由本地状态机决定，不由渠道返回）。
REFUND_RESULT_SUCCEEDED = "SUCCEEDED"
REFUND_RESULT_PROCESSING = "PROCESSING"
REFUND_RESULT_UNKNOWN = "UNKNOWN"
REFUND_RESULT_FAILED = "FAILED"
REFUND_RESULT_NOT_FOUND = "NOT_FOUND"

#: 部分渠道要求回调应答是纯文本（支付宝要求 body 为 ``success``）。
#: 应答字典带这个键时，HTTP 层输出纯文本而不是 JSON。
ACK_TEXT_KEY = "_text"

#: 渠道账单差异核对用的手续费列名（不同渠道账单可用时间不同，由 adapter 解析）。
BILL_ROW_FIELDS = (
    "merchant_order_no",
    "transaction_id",
    "kind",
    "amount_cents",
    "currency",
    "refund_no",
    "fee_cents",
    "occurred_at",
)


@dataclass(frozen=True)
class NotificationVerdict:
    """一次回调的验签结论。

    ``accepted=False`` 时 ``reason`` 是稳定的机器可读原因（不含任何秘密），
    入口据此记录安全事件；``accepted=True`` 且 ``payment is None`` 表示验签通过
    但不是成功收款通知（例如交易关闭），不予入账。
    """

    accepted: bool
    reason: str
    payment: VerifiedPayment | None = None
    event_id: str | None = None
    event_type: str = "payment.succeeded"


@dataclass(frozen=True)
class BillRow:
    """账单行。仅保留核对必要字段，不保留持卡人/账户隐私字段。"""

    merchant_order_no: str
    transaction_id: str
    kind: str
    amount_cents: int
    currency: str = "CNY"
    refund_no: str | None = None
    fee_cents: int = 0
    occurred_at: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "merchant_order_no": self.merchant_order_no,
            "transaction_id": self.transaction_id,
            "kind": self.kind,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "refund_no": self.refund_no,
            "fee_cents": self.fee_cents,
            "occurred_at": self.occurred_at,
        }


class PaymentGateway(Protocol):
    """渠道能力协议。``name`` 与 ``provider`` 取值一致（wechat/alipay/paypal/sandbox）。"""

    name: str

    def create(
        self,
        merchant_order_no: str,
        amount_cents: int,
        currency: str,
        scene: str,
        subject: str,
        return_url: str | None,
    ) -> dict[str, Any]:
        """用固定商户单号下单。

        返回 ``{"status": PENDING|UNKNOWN|..., "action": {"type": qr|redirect|none,
        "url": ...}, "provider_transaction_id": str|None, "expires_at": int|None,
        "raw_digest": str}``。渠道超时抛 ``GatewayError``，由服务层记 UNKNOWN 并
        用**同一商户单号**查单。
        """
        ...

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        """服务端主动查单（唯一被认可的成功来源之一）。"""
        ...

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        """关闭渠道订单；已支付的单不能被关闭，返回 SUCCEEDED 让服务层复核。"""
        ...

    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        """按原渠道原路退款，重试使用同一 ``refund_no``。"""
        ...

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        """查退款结果；UNKNOWN 必须继续查单。"""
        ...

    def download_bill(self, bill_date: str) -> bytes:
        """下载账单原始文件（``bill_date`` 为 ``YYYY-MM-DD``，渠道本地时区）。"""
        ...

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        """解析账单为统一行（见 ``BILL_ROW_FIELDS``）。"""
        ...

    def verify_notification(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> VerifiedPayment | None:
        """渠道回调验签 + 解密；不通过返回 ``None``。"""
        ...

    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        """带原因的验签结论（入口用它记录安全事件）。"""
        ...

    def expected_account_ref(self) -> str | None:
        """本商户在该渠道的账户标识；未配置返回 ``None``（此时跳过该项核对）。"""
        ...

    def ack_success(self) -> dict[str, Any]:
        """按渠道协议的成功应答体。"""
        ...

    def ack_failure(self, reason: str) -> dict[str, Any]:
        """按渠道协议的失败应答体（不回显渠道秘密）。"""
        ...


# ------------------------------------------------------------------ 公共工具
def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """大小写不敏感地读取请求头（渠道头名大小写不统一）。"""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def payload_digest(raw_body: bytes) -> str:
    """回调原始字节的 SHA-256；库里只存摘要，原文按摘要落盘。"""
    return hashlib.sha256(raw_body).hexdigest()


def business_event_key(*parts: str) -> str:
    """渠道没有事件号时用规范化业务键去重（provider+交易号+商户单号+金额）。"""
    normalized = "|".join(part.strip() for part in parts if part is not None)
    return f"biz-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:48]}"


def sign_hmac(secret: bytes, message: bytes) -> str:
    """HMAC-SHA256 十六进制签名（沙箱通道与本地签名校验共用）。"""
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_hmac(secret: bytes, message: bytes, signature: str | None) -> bool:
    """常量时间比较 HMAC 签名。"""
    if not signature:
        return False
    return hmac.compare_digest(sign_hmac(secret, message), signature.strip().lower())


def cents_from_decimal_text(text: str) -> int | None:
    """把渠道的十进制金额字符串转成整数分；超出 2 位小数视为非法（拒绝舍入）。"""
    from decimal import Decimal, InvalidOperation

    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None
    if value < 0:
        return None
    cents = value * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


def decimal_text_from_cents(amount_cents: int) -> str:
    """整数分转渠道要求的两位小数字符串（不用 float）。"""
    return f"{amount_cents // 100}.{amount_cents % 100:02d}"


def bill_day_bounds(bill_date: str) -> tuple[int, int]:
    """账单日 ``YYYY-MM-DD`` 的 UTC 毫秒边界 ``[start, end)``。

    渠道账单按渠道结算时区切日；首发渠道的账单日按 ``Asia/Shanghai`` 自然日
    处理，其他渠道若按 UTC 或美西时间切日需在开通时改为 adapter 配置
    （08 文档：不同渠道账单可用时间不同，不硬编码为次日零点必可下载）。
    """
    from datetime import datetime, timedelta

    from app.platform.ids import SHANGHAI, UTC

    try:
        local = datetime.strptime(bill_date, "%Y-%m-%d").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise ValueError(f"非法账单日：{bill_date}") from exc
    start = local.astimezone(UTC)
    end = (local + timedelta(days=1)).astimezone(UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def bill_date_of(moment_ms: int) -> str:
    """毫秒时间戳所属的账单日（``Asia/Shanghai``）。"""
    from datetime import datetime

    from app.platform.ids import SHANGHAI

    return datetime.fromtimestamp(moment_ms / 1000, tz=SHANGHAI).strftime("%Y-%m-%d")


__all__ = [
    "ACK_TEXT_KEY",
    "ACTION_NONE",
    "ACTION_QR",
    "ACTION_REDIRECT",
    "BILL_ROW_FIELDS",
    "BillRow",
    "NotificationVerdict",
    "PAYMENT_RESULT_CLOSED",
    "PAYMENT_RESULT_FAILED",
    "PAYMENT_RESULT_NOT_FOUND",
    "PAYMENT_RESULT_PENDING",
    "PAYMENT_RESULT_SUCCEEDED",
    "PAYMENT_RESULT_UNKNOWN",
    "PAYMENT_SCENES",
    "PaymentGateway",
    "REFUND_RESULT_FAILED",
    "REFUND_RESULT_NOT_FOUND",
    "REFUND_RESULT_PROCESSING",
    "REFUND_RESULT_SUCCEEDED",
    "REFUND_RESULT_UNKNOWN",
    "VerifiedPayment",
    "bill_date_of",
    "bill_day_bounds",
    "business_event_key",
    "cents_from_decimal_text",
    "decimal_text_from_cents",
    "header_value",
    "payload_digest",
    "sign_hmac",
    "verify_hmac",
]
