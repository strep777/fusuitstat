"""沙箱支付通道：开发与测试用，**绝不伪造成功**。

沙箱只模拟「渠道侧账本」：下单返回占位二维码/跳转地址，是否真的收到钱由测试显式
在渠道侧账本上标注（``mark_paid``）或由已签名回调（``simulate_notification``）声明。
它从不写本地收款表，也从不把 create/query 的返回值变成业务成功——业务成功只能由
支付服务层在验签通知或服务端查单之后写入。生产环境启用沙箱通道会被配置校验拒绝
（``PAYMENT_PROVIDERS`` 不允许包含 mock），路由 ``/payment-notifications/sandbox``
在生产返回 404。

回调签名：``HMAC-SHA256(timestamp + "." + raw_body)``，密钥由服务端 secret 派生，
仅在开发/测试环境可用；时间戳超出重放窗口一律拒绝。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Mapping
from typing import Any

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments.base import (
    ACTION_QR,
    ACTION_REDIRECT,
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_NOT_FOUND,
    PAYMENT_RESULT_PENDING,
    PAYMENT_RESULT_SUCCEEDED,
    REFUND_RESULT_NOT_FOUND,
    REFUND_RESULT_SUCCEEDED,
    NotificationVerdict,
    VerifiedPayment,
    bill_day_bounds,
    business_event_key,
    header_value,
    payload_digest,
    sign_hmac,
    verify_hmac,
)
from app.platform.config import Settings
from app.platform.ids import now_ms, random_token

#: 回调重放窗口（毫秒）。与真实渠道一样，过期重放必须拒绝。
REPLAY_WINDOW_MS = 300_000

#: 回调体缺少 status 字段时的默认判定（不是成功，因此不会入账）。
DEFAULT_NOTIFICATION_STATUS = PAYMENT_RESULT_PENDING

SIGNATURE_HEADER = "x-sandbox-signature"
TIMESTAMP_HEADER = "x-sandbox-timestamp"
EVENT_ID_HEADER = "x-sandbox-event-id"

BILL_HEADER = (
    "merchant_order_no",
    "transaction_id",
    "kind",
    "amount_cents",
    "currency",
    "refund_no",
    "fee_cents",
    "occurred_at",
)


class SandboxPaymentGateway:
    """渠道侧账本 + 已签名回调构造器。``provider`` 可换成渠道名以替代真实渠道联调。"""

    def __init__(self, settings: Settings, *, provider: str = "sandbox") -> None:
        self._settings = settings
        self.name = provider
        self._ledger: dict[str, dict[str, Any]] = {}
        self._refunds: dict[str, dict[str, Any]] = {}
        #: 观测点：创建/查单/退款调用记录，测试据此断言「同单号只下一次单」与查单策略。
        self.create_calls: list[str] = []
        self.query_calls: list[str] = []
        self.refund_calls: list[dict[str, Any]] = []
        #: 测试接缝（仅沙箱通道提供，真实渠道没有这些开关）。
        self.timeout_on_create = False
        self.on_create_hook: Callable[[str], None] | None = None
        self.refund_result_status = REFUND_RESULT_SUCCEEDED

    # ------------------------------------------------------------ 环境与签名
    def _ensure_enabled(self) -> None:
        if self._settings.app_env not in ("development", "test"):
            raise GatewayNotConfigured("sandbox")

    @property
    def shared_secret(self) -> bytes:
        """沙箱签名密钥（由服务端 secret 派生，测试用它构造合法/非法回调）。"""
        material = f"payment-sandbox::{self._settings.secret('session_secret')}"
        return sign_hmac(b"starium", material.encode("utf-8")).encode("ascii")

    def signature_for(self, timestamp_ms: int, raw_body: bytes) -> str:
        return sign_hmac(self.shared_secret, f"{timestamp_ms}.".encode("ascii") + raw_body)

    def simulate_notification(
        self,
        *,
        merchant_order_no: str,
        amount_cents: int,
        currency: str = "CNY",
        provider_transaction_id: str | None = None,
        event_id: str | None = None,
        status: str = PAYMENT_RESULT_SUCCEEDED,
        timestamp_ms: int | None = None,
        mark_paid: bool = True,
        account_ref: str | None = None,
        tamper_signature: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, str], bytes]:
        """构造**已签名**的回调（headers, raw_body）。

        ``mark_paid=True`` 同时在渠道侧账本标注该单已收款，使随后的 ``query`` 与
        账单下载返回一致结果；``tamper_signature=True`` 用于构造错签名用例。
        """
        self._ensure_enabled()
        moment = timestamp_ms if timestamp_ms is not None else now_ms()
        transaction_id = provider_transaction_id or f"SBXTXN{random_token(8)}"
        resolved_event_id = event_id or f"sandbox-evt-{random_token(8)}"
        body: dict[str, Any] = {
            "provider": self.name,
            "event_id": resolved_event_id,
            "event_type": "payment.succeeded",
            "merchant_order_no": merchant_order_no,
            "provider_transaction_id": transaction_id,
            "amount_cents": int(amount_cents),
            "currency": currency,
            "status": status,
            "paid_at": moment,
            "account_ref": account_ref if account_ref is not None else self.expected_account_ref(),
        }
        if extra:
            body.update(dict(extra))
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        signature = self.signature_for(moment, raw)
        if tamper_signature:
            signature = ("0" * len(signature)) if not signature.startswith("0") else ("f" * len(signature))
        if mark_paid and status == PAYMENT_RESULT_SUCCEEDED:
            self.mark_paid(
                merchant_order_no,
                amount_cents=int(amount_cents),
                currency=currency,
                transaction_id=transaction_id,
                paid_at=moment,
                event_id=resolved_event_id,
            )
        headers = {
            "content-type": "application/json",
            SIGNATURE_HEADER: signature,
            TIMESTAMP_HEADER: str(moment),
            EVENT_ID_HEADER: resolved_event_id,
        }
        return headers, raw

    # ------------------------------------------------------------ 渠道侧账本
    def mark_paid(
        self,
        merchant_order_no: str,
        *,
        amount_cents: int | None = None,
        currency: str = "CNY",
        transaction_id: str | None = None,
        paid_at: int | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """在渠道侧账本标注「真实已收款」；这是沙箱唯一的成功来源。"""
        entry = self._ledger.setdefault(
            merchant_order_no,
            {
                "merchant_order_no": merchant_order_no,
                "amount_cents": amount_cents or 0,
                "currency": currency,
                "state": "CREATED",
                "transaction_id": transaction_id,
                "paid_at": paid_at,
                "event_id": event_id,
                "fee_cents": 0,
            },
        )
        if amount_cents is not None:
            entry["amount_cents"] = int(amount_cents)
        entry["currency"] = currency
        entry["state"] = PAYMENT_RESULT_SUCCEEDED
        entry["transaction_id"] = transaction_id or entry["transaction_id"] or f"SBXTXN{random_token(8)}"
        entry["paid_at"] = paid_at if paid_at is not None else now_ms()
        entry["event_id"] = event_id or entry["event_id"]
        return dict(entry)

    def mark_closed(self, merchant_order_no: str) -> dict[str, Any]:
        entry = self._ledger.setdefault(
            merchant_order_no,
            {"merchant_order_no": merchant_order_no, "amount_cents": 0, "currency": "CNY", "fee_cents": 0},
        )
        if entry.get("state") != PAYMENT_RESULT_SUCCEEDED:
            entry["state"] = PAYMENT_RESULT_CLOSED
        return dict(entry)

    def record_refund(
        self,
        *,
        merchant_order_no: str,
        refund_no: str,
        amount_cents: int,
        status: str = REFUND_RESULT_SUCCEEDED,
        provider_refund_id: str | None = None,
        occurred_at: int | None = None,
    ) -> dict[str, Any]:
        """在渠道侧账本登记退款结果（用于对账与退款查询用例）。"""
        entry = {
            "refund_no": refund_no,
            "merchant_order_no": merchant_order_no,
            "amount_cents": int(amount_cents),
            "status": status,
            "provider_refund_id": provider_refund_id or f"SBXRF{random_token(8)}",
            "occurred_at": occurred_at if occurred_at is not None else now_ms(),
        }
        self._refunds[refund_no] = entry
        return dict(entry)

    def ledger_entry(self, merchant_order_no: str) -> dict[str, Any] | None:
        entry = self._ledger.get(merchant_order_no)
        return dict(entry) if entry else None

    def set_fee(self, merchant_order_no: str, fee_cents: int) -> None:
        """设置渠道账单里的手续费（仅用于手续费不符用例）。"""
        entry = self._ledger.setdefault(
            merchant_order_no,
            {"merchant_order_no": merchant_order_no, "amount_cents": 0, "currency": "CNY"},
        )
        entry["fee_cents"] = int(fee_cents)

    # ------------------------------------------------------------ 渠道能力
    def create(
        self,
        merchant_order_no: str,
        amount_cents: int,
        currency: str,
        scene: str,
        subject: str,
        return_url: str | None,
    ) -> dict[str, Any]:
        """返回占位二维码/跳转地址；不产生任何「已付款」结论。"""
        self._ensure_enabled()
        entry = self._ledger.get(merchant_order_no)
        if entry is None:
            entry = {
                "merchant_order_no": merchant_order_no,
                "amount_cents": int(amount_cents),
                "currency": currency,
                "scene": scene,
                "subject": subject[:120],
                "state": "CREATED",
                "transaction_id": None,
                "paid_at": None,
                "event_id": None,
                "fee_cents": 0,
            }
            self._ledger[merchant_order_no] = entry
            self.create_calls.append(merchant_order_no)
        # 钩子：模拟「回调先到而下单响应后到」等乱序场景。
        if self.on_create_hook is not None:
            self.on_create_hook(merchant_order_no)
        if self.timeout_on_create:
            # 渠道侧已建单但客户端超时：服务层必须记 UNKNOWN 并用同一单号查单。
            raise GatewayError("sandbox 下单超时（渠道侧已建单）", retryable=True)
        action_type = ACTION_QR if scene == "native" else ACTION_REDIRECT
        url = f"{self._settings.public_origin.rstrip('/')}/sandbox-pay/{merchant_order_no}"
        return {
            "status": PAYMENT_RESULT_PENDING,
            "action": {"type": action_type, "url": url},
            "provider_transaction_id": None,
            "expires_at": entry.get("expires_at"),
            "raw_digest": payload_digest(f"{merchant_order_no}:{amount_cents}".encode()),
            "sandbox": True,
        }

    def query(self, merchant_order_no: str) -> dict[str, Any]:
        self._ensure_enabled()
        self.query_calls.append(merchant_order_no)
        entry = self._ledger.get(merchant_order_no)
        if entry is None:
            return {"status": PAYMENT_RESULT_NOT_FOUND, "provider_transaction_id": None}
        if entry.get("state") == PAYMENT_RESULT_SUCCEEDED:
            return {
                "status": PAYMENT_RESULT_SUCCEEDED,
                "provider_transaction_id": entry["transaction_id"],
                "amount_cents": entry["amount_cents"],
                "currency": entry.get("currency", "CNY"),
                "paid_at": entry.get("paid_at"),
                "event_id": entry.get("event_id")
                or business_event_key(self.name, entry["transaction_id"] or "", merchant_order_no),
                "raw_digest": payload_digest(
                    f"query:{merchant_order_no}:{entry['transaction_id']}".encode()
                ),
            }
        return {
            "status": entry.get("state", PAYMENT_RESULT_PENDING),
            "provider_transaction_id": entry.get("transaction_id"),
        }

    def close(self, merchant_order_no: str) -> dict[str, Any]:
        self._ensure_enabled()
        entry = self._ledger.get(merchant_order_no)
        if entry is None:
            return {"status": PAYMENT_RESULT_CLOSED}
        if entry.get("state") == PAYMENT_RESULT_SUCCEEDED:
            # 已收款不能关闭；让服务层复核真实结果。
            return {"status": PAYMENT_RESULT_SUCCEEDED, "provider_transaction_id": entry["transaction_id"]}
        entry["state"] = PAYMENT_RESULT_CLOSED
        return {"status": PAYMENT_RESULT_CLOSED}

    def refund(
        self, merchant_order_no: str, refund_no: str, amount_cents: int, reason: str
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self.refund_calls.append(
            {
                "merchant_order_no": merchant_order_no,
                "refund_no": refund_no,
                "amount_cents": int(amount_cents),
                "reason": reason,
            }
        )
        entry = self._refunds.get(refund_no)
        if entry is None:
            entry = self.record_refund(
                merchant_order_no=merchant_order_no,
                refund_no=refund_no,
                amount_cents=amount_cents,
                status=self.refund_result_status,
            )
        return {
            "status": entry["status"],
            "provider_refund_id": entry["provider_refund_id"],
            "amount_cents": entry["amount_cents"],
        }

    def query_refund(self, refund_no: str) -> dict[str, Any]:
        self._ensure_enabled()
        entry = self._refunds.get(refund_no)
        if entry is None:
            return {"status": REFUND_RESULT_NOT_FOUND, "provider_refund_id": None}
        return {
            "status": entry["status"],
            "provider_refund_id": entry["provider_refund_id"],
            "amount_cents": entry["amount_cents"],
        }

    def download_bill(self, bill_date: str) -> bytes:
        """按账单日导出渠道侧账本 CSV（渠道账单的最小等价物）。"""
        self._ensure_enabled()
        start, end = bill_day_bounds(bill_date)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(BILL_HEADER)
        for entry in self._ledger.values():
            paid_at = entry.get("paid_at")
            if entry.get("state") != PAYMENT_RESULT_SUCCEEDED or paid_at is None:
                continue
            if not (start <= int(paid_at) < end):
                continue
            writer.writerow(
                [
                    entry["merchant_order_no"],
                    entry["transaction_id"],
                    "payment",
                    entry["amount_cents"],
                    entry.get("currency", "CNY"),
                    "",
                    entry.get("fee_cents", 0),
                    paid_at,
                ]
            )
        for entry in self._refunds.values():
            occurred_at = entry.get("occurred_at")
            if entry.get("status") != REFUND_RESULT_SUCCEEDED or occurred_at is None:
                continue
            if not (start <= int(occurred_at) < end):
                continue
            writer.writerow(
                [
                    entry["merchant_order_no"],
                    entry.get("provider_refund_id", ""),
                    "refund",
                    entry["amount_cents"],
                    "CNY",
                    entry["refund_no"],
                    0,
                    occurred_at,
                ]
            )
        return buffer.getvalue().encode("utf-8")

    def parse_bill(self, bill_date: str, raw: bytes) -> list[dict[str, Any]]:
        self._ensure_enabled()
        _ = bill_date
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        rows: list[dict[str, Any]] = []
        for row in reader:
            rows.append(
                {
                    "merchant_order_no": (row.get("merchant_order_no") or "").strip(),
                    "transaction_id": (row.get("transaction_id") or "").strip(),
                    "kind": (row.get("kind") or "payment").strip(),
                    "amount_cents": _int_or_zero(row.get("amount_cents")),
                    "currency": (row.get("currency") or "CNY").strip(),
                    "refund_no": (row.get("refund_no") or "").strip() or None,
                    "fee_cents": _int_or_zero(row.get("fee_cents")),
                    "occurred_at": _int_or_zero(row.get("occurred_at")) or None,
                }
            )
        return rows

    def verify_notification_detailed(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> NotificationVerdict:
        """验签 + 时间戳窗口 + 结构核对；任何一步失败都返回不接受。"""
        self._ensure_enabled()
        timestamp_raw = header_value(headers, TIMESTAMP_HEADER)
        if not timestamp_raw or not timestamp_raw.isdigit():
            return NotificationVerdict(accepted=False, reason="timestamp_missing")
        timestamp = int(timestamp_raw)
        if abs(now_ms() - timestamp) > REPLAY_WINDOW_MS:
            return NotificationVerdict(accepted=False, reason="timestamp_out_of_window")
        signature = header_value(headers, SIGNATURE_HEADER)
        if not verify_hmac(self.shared_secret, f"{timestamp}.".encode("ascii") + raw_body, signature):
            return NotificationVerdict(accepted=False, reason="signature_mismatch")
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return NotificationVerdict(accepted=False, reason="payload_malformed")
        if not isinstance(body, dict) or not body.get("merchant_order_no"):
            return NotificationVerdict(accepted=False, reason="payload_incomplete")
        amount = body.get("amount_cents")
        if not isinstance(amount, int) or amount < 0:
            return NotificationVerdict(accepted=False, reason="payload_incomplete")
        event_id = str(body.get("event_id") or header_value(headers, EVENT_ID_HEADER) or "")
        status = str(body.get("status", DEFAULT_NOTIFICATION_STATUS))
        if status != PAYMENT_RESULT_SUCCEEDED:
            return NotificationVerdict(
                accepted=True, reason="not_a_success_payment", event_id=event_id or None
            )
        payment = VerifiedPayment(
            provider=self.name,
            provider_transaction_id=str(body.get("provider_transaction_id") or ""),
            merchant_order_no=str(body["merchant_order_no"]),
            amount_cents=int(amount),
            currency=str(body.get("currency") or "CNY"),
            paid_at=int(body["paid_at"]) if body.get("paid_at") else timestamp,
            raw_digest=payload_digest(raw_body),
            event_id=event_id or business_event_key(
                self.name,
                str(body.get("provider_transaction_id") or ""),
                str(body["merchant_order_no"]),
                str(amount),
            ),
            account_ref=str(body.get("account_ref")) if body.get("account_ref") else None,
        )
        return NotificationVerdict(
            accepted=True, reason="verified", payment=payment, event_id=payment.event_id
        )

    def verify_notification(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> VerifiedPayment | None:
        return self.verify_notification_detailed(headers, raw_body).payment

    def expected_account_ref(self) -> str | None:
        return f"sandbox:{self._settings.brand_domain}"

    def ack_success(self) -> dict[str, Any]:
        return {"code": "SUCCESS", "message": "成功", "sandbox": True}

    def ack_failure(self, reason: str) -> dict[str, Any]:
        return {"code": "FAIL", "message": "验签失败", "reason": reason[:60], "sandbox": True}


def _int_or_zero(value: Any) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "BILL_HEADER",
    "DEFAULT_NOTIFICATION_STATUS",
    "EVENT_ID_HEADER",
    "REPLAY_WINDOW_MS",
    "REFUND_RESULT_SUCCEEDED",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "SandboxPaymentGateway",
]
