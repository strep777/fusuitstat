"""支付模块用例：发起收款、确认收款、关闭尝试、退款与对账。

事务不变量（05/08 文档，全部在本模块的写事务内实现）：

1. **创建支付**：先落库唯一商户单号再向渠道下单；渠道超时记 ``UNKNOWN`` 并用
   **同一单号**查单，禁止立即换新单号再下单。同一应付项默认一个活动支付尝试。
2. **确认收款**：去重事件（``UNIQUE(provider,event_id)`` 屏障）+ 收款 + 分配 +
   应付状态 + 库存/名额核销（commerce 端口）+ outbox **一并提交**。
3. **成功不可回退**：成功状态只能由已验签且核对完整的通知或服务端主动查单确认；
   后到的创建响应不得把 ``SUCCEEDED`` 覆盖为 ``PENDING``。
4. **可退额度**：退款申请在同一事务内用条件 ``UPDATE`` 原子预留；
   ``有效预留 + 已成功退款 ≤ 原收款``；明确失败且渠道证明未退才释放。
5. **多付**：两个渠道都真实收款时，事务内只分配应付余额，剩余进入
   ``OVERPAYMENT`` 并触发退款流程；多付款退款不冲减订单正常实收。

对外只通过 ``ports.py`` 暴露命令；订单与应付项经 ``repository_bridge.py`` 只读访问，
商户/库存状态推进经 commerce 端口完成。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.payments import get_gateway
from app.adapters.payments.base import (
    ACTION_NONE,
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_FAILED,
    PAYMENT_RESULT_NOT_FOUND,
    PAYMENT_RESULT_PENDING,
    PAYMENT_RESULT_SUCCEEDED,
    PAYMENT_SCENES,
    REFUND_RESULT_FAILED,
    REFUND_RESULT_NOT_FOUND,
    REFUND_RESULT_PROCESSING,
    REFUND_RESULT_SUCCEEDED,
    REFUND_RESULT_UNKNOWN,
    bill_day_bounds,
    payload_digest,
)
from app.modules.contracts import PaymentIntent, RefundOutcome, VerifiedPayment
from app.modules.payments import repository as repo
from app.modules.payments import repository_bridge as bridge
from app.modules.payments.models import (
    ACTIVE_ATTEMPT_STATUSES,
    DIFFERENCE_RESOLUTIONS,
    PaymentAllocation,
    PaymentAttempt,
    Refund,
)
from app.platform import audit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import add_minutes_ms, iso, new_id, now_ms
from app.platform.reliability import IdempotencyKey, digest, enqueue

#: 同一支付尝试两次渠道查单之间的最小间隔（「受策略约束的查单」，防止轮询打爆渠道）。
QUERY_MIN_INTERVAL_MS = 3_000
#: 幂等登记保留期：HTTP 响应缓存过期不会取消业务防重能力（08 文档）。
IDEMPOTENCY_TTL_MS = 7 * 86_400_000
#: 已取消/已终结的订单状态：这时再收到的真实收款只能记多付并退款。
CLOSED_ORDER_STATUSES = ("CANCELLED",)
#: 应付项终态：关闭后到达的收款不得再核销库存/名额。
CLOSED_INSTALLMENT_STATES = ("CLOSED",)
#: 可发起支付的应付项状态。
PAYABLE_INSTALLMENT_STATES = ("DUE", "PAYING")
#: 回调原始字节留存目录（相对 ``Settings.data_dir``）。
PAYMENT_EVIDENCE_DIR = "payment_events"
#: 账单原文留存目录。
BILL_EVIDENCE_DIR = "reconciliation"

RefundStatusLiteral = Literal["PROCESSING", "UNKNOWN", "SUCCEEDED", "FAILED"]

_REFUND_OUTCOME_STATUS: dict[str, RefundStatusLiteral] = {
    "REQUESTED": "PROCESSING",
    "APPROVED": "PROCESSING",
    "PROCESSING": "PROCESSING",
    "UNKNOWN": "UNKNOWN",
    "SUCCEEDED": "SUCCEEDED",
    "FAILED": "FAILED",
    "REJECTED": "FAILED",
}


# ------------------------------------------------------------------ 通用工具
@dataclass(frozen=True)
class _AllocationPlan:
    """一次已核实收款的分配计划。"""

    classification: str  # APPLIED | OVERPAYMENT | QUARANTINED
    applied_cents: int
    overpayment_cents: int
    reason: str


@dataclass(frozen=True)
class IdempotentCall:
    row: IdempotencyKey | None
    replay: dict[str, Any] | None


def _provider_code(provider: str) -> str:
    return "".join(ch for ch in provider.upper() if ch.isalnum())[:4] or "PAY"


def new_merchant_order_no(provider: str) -> str:
    """唯一商户单号：渠道唯一约束 + 本地唯一索引双保险，下单前落库。"""
    return f"ST{_provider_code(provider)}{now_ms()}{secrets.token_hex(4).upper()}"


def new_refund_no(provider: str) -> str:
    """唯一退款号：重试必须复用同一退款号。"""
    return f"RF{_provider_code(provider)}{now_ms()}{secrets.token_hex(4).upper()}"


def payload_relative_path(payload_digest_value: str) -> str:
    """回调原始字节的留存路径（相对 ``data_dir``）：``storage/payment_events/<digest>.payload``。"""
    return f"storage/{PAYMENT_EVIDENCE_DIR}/{payload_digest_value}.payload"


def persist_raw_payload(raw_body: bytes) -> tuple[str, str]:
    """把验签所需原始字节写盘，返回 ``(digest, 相对路径)``。

    库里只存摘要与路径（08 文档：不存明文隐私全文，但要能复核验签）。
    """
    settings = get_settings()
    value = payload_digest(raw_body)
    target = settings.data_dir / "storage" / PAYMENT_EVIDENCE_DIR / f"{value}.payload"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(raw_body)
    return value, payload_relative_path(value)


def _gateway(provider: str) -> Any:
    try:
        return get_gateway(provider)
    except GatewayNotConfigured as exc:
        raise AppError("PROVIDER_UNAVAILABLE", f"支付方式「{provider}」尚未配置完成。") from exc


def _action_of(attempt: PaymentAttempt) -> dict[str, Any]:
    action = dict(attempt.action or {})
    action.setdefault("type", ACTION_NONE)
    action.setdefault("url", "")
    return action


def _intent_of(attempt: PaymentAttempt) -> PaymentIntent:
    return PaymentIntent(
        attempt_id=attempt.id,
        provider=attempt.provider,
        merchant_order_no=attempt.merchant_order_no,
        expected_cents=attempt.expected_cents,
        currency=attempt.currency,
        status=attempt.status,
        action=_action_of(attempt),
        expires_at=attempt.action_expires_at,
    )


def _alert_staff(session: Session, *, title: str, body: str, payload: dict[str, Any]) -> None:
    """异常资金必须有人处理：登记站内/邮件告警（真实投递由 worker 完成）。"""
    from app.modules.notifications.ports import NotificationRequest, notify

    notify(
        session,
        NotificationRequest(
            event_id=new_id("notification"),
            event_type="security.alert",
            user_id=None,
            title=title,
            body=body,
            category="security",
            channels=(),
            payload=payload,
        ),
    )


def _insert_event_barrier(
    session: Session,
    *,
    provider: str,
    event_key: str,
    verified: VerifiedPayment,
    payload_path: str,
    attempt: PaymentAttempt | None,
    event_type: str = "payment.succeeded",
    verified_flag: bool = True,
    rejection_reason: str | None = None,
) -> Any | None:
    """事件去重屏障：``UNIQUE(provider,event_id)`` 冲突返回 ``None``（重复通知）。

    用 SAVEPOINT 包住插入，冲突只回滚这一条，不影响同一事务内的其他工作。
    """
    try:
        with session.begin_nested():
            event = repo.create_event(
                session,
                provider=provider,
                event_id=event_key,
                event_type=event_type,
                payload_digest=verified.raw_digest,
                payload_path=payload_path,
                verified=verified_flag,
                attempt=attempt,
                merchant_order_no=verified.merchant_order_no or None,
                amount_cents=verified.amount_cents,
                currency=verified.currency,
                provider_transaction_id=verified.provider_transaction_id,
                processing_result="PENDING",
                rejection_reason=rejection_reason,
            )
            session.flush()
    except IntegrityError:
        return None
    return event


# ------------------------------------------------------------------ 幂等
def begin_idempotent(
    session: Session,
    *,
    actor_id: str,
    operation: str,
    key: str,
    request_hash: str,
) -> IdempotentCall:
    """幂等登记：同主体+动作+键唯一；同键不同请求 409；同键重试返回同资源。"""
    if not key or not key.strip():
        raise AppError("VALIDATION_ERROR", "缺少 Idempotency-Key。", fields={"Idempotency-Key": "必填"})
    key = key.strip()[:120]
    stmt = sa.select(IdempotencyKey).where(
        IdempotencyKey.actor_id == actor_id,
        IdempotencyKey.operation == operation,
        IdempotencyKey.key == key,
    )
    row = session.scalars(stmt).one_or_none()
    if row is None:
        candidate = IdempotencyKey(
            id=new_id("job"),
            actor_id=actor_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            status="IN_PROGRESS",
            expires_at=now_ms() + IDEMPOTENCY_TTL_MS,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
        except IntegrityError:
            row = session.scalars(stmt).one_or_none()
            if row is None:
                raise AppError("STATE_CONFLICT", "相同请求正在处理中，请稍后查询结果。") from None
        else:
            return IdempotentCall(row=candidate, replay=None)
    if row.request_hash != request_hash:
        raise AppError("IDEMPOTENCY_CONFLICT")
    if row.status == "COMPLETED":
        return IdempotentCall(row=row, replay=dict(row.response_snapshot or {}))
    raise AppError("STATE_CONFLICT", "相同请求正在处理中，请稍后查询结果。")


def complete_idempotent(
    session: Session, call: IdempotentCall, *, resource_id: str, snapshot: dict[str, Any]
) -> None:
    if call.row is None:
        return
    call.row.status = "COMPLETED"
    call.row.resource_id = resource_id
    call.row.response_snapshot = snapshot
    session.flush()


# ------------------------------------------------------------------ 创建支付
def create_intent(
    session: Session, *, installment_id: str, provider: str, scene: str, actor_id: str
) -> PaymentIntent:
    """为应付项创建（或复用）支付尝试。

    客户端只提交渠道与场景；金额由服务端按应付项余额裁定，商户单号服务端生成。
    """
    from app.modules.content.ports import require_feature

    require_feature(session, "online_payment")
    settings = get_settings()
    provider = (provider or "").strip().lower()
    if scene not in PAYMENT_SCENES:
        raise AppError("VALIDATION_ERROR", "不支持的支付场景。", fields={"scene": "非法场景"})
    gateway = _gateway(provider)

    installment = bridge.load_installment(session, installment_id)
    if installment is None or not installment.get("id"):
        raise AppError("RESOURCE_NOT_FOUND")
    order = bridge.load_order(session, installment["order_id"])
    if order is None or order.get("user_id") != actor_id:
        # 逐资源归属检查：不泄露他人订单是否存在。
        raise AppError("RESOURCE_NOT_FOUND")
    if order.get("status") in CLOSED_ORDER_STATUSES:
        raise AppError("STATE_CONFLICT", "订单已取消，无法再发起支付。")
    state = str(installment.get("state") or "")
    if state == "PAID":
        raise AppError("STATE_CONFLICT", "该应付项已结清。")
    if state not in PAYABLE_INSTALLMENT_STATES:
        raise AppError("STATE_CONFLICT", f"该应付项当前状态（{state}）不允许发起支付。")

    applied = repo.applied_sum(session, installment_id=installment_id)
    remaining = int(installment["amount_cents"]) - applied
    if remaining <= 0:
        raise AppError("STATE_CONFLICT", "该应付项已无待付金额。")

    active = repo.list_active_attempts_for_installment(session, installment_id)
    same_provider = [item for item in active if item.provider == provider]
    if same_provider:
        # 同一应付项同渠道复用同一商户单号，绝不换新单号再下单。
        attempt = same_provider[0]
        if attempt.status == "UNKNOWN":
            attempt = query_attempt(session, attempt, force=True)
        return _intent_of(attempt)
    if active:
        # 切换渠道前关闭旧单并核对渠道状态。
        close_payment_attempts_for_installment(
            session, installment_id=installment_id, reason="switch_provider"
        )

    attempt = repo.create_attempt(
        session,
        order_id=str(installment["order_id"]),
        installment_id=installment_id,
        user_id=actor_id,
        provider=provider,
        scene=scene,
        merchant_order_no=new_merchant_order_no(provider),
        expected_cents=remaining,
        currency=str(installment.get("currency") or "CNY"),
        action_expires_at=add_minutes_ms(settings.installment_payment_ttl_minutes),
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=actor_id,
        action="payment.attempt_created",
        resource_type="payment_attempt",
        resource_id=attempt.id,
        after={
            "provider": provider,
            "scene": scene,
            "expected_cents": remaining,
            "merchant_order_no": attempt.merchant_order_no,
        },
    )
    return _submit_to_gateway(session, attempt, gateway, scene, subject=f"订单支付 {attempt.order_id}")


def _submit_to_gateway(
    session: Session, attempt: PaymentAttempt, gateway: Any, scene: str, *, subject: str
) -> PaymentIntent:
    """用固定商户单号下单，并把渠道结果按「不降级」规则写回。"""
    settings = get_settings()
    return_url = settings.payment_return_url or None
    try:
        result = gateway.create(
            attempt.merchant_order_no,
            attempt.expected_cents,
            attempt.currency,
            scene,
            subject,
            return_url,
        )
    except GatewayError as exc:
        # 超时/网络错误：结果未知，禁止立即换单号重下，用同一单号查单。
        status = "UNKNOWN" if exc.retryable else "FAILED"
        _apply_create_response(
            session, attempt, status=status, action=None, failure_reason=str(exc)[:200] or None
        )
        if status == "UNKNOWN":
            enqueue(
                session,
                event_type="payment.unknown",
                aggregate_id=attempt.id,
                payload={
                    "merchant_order_no": attempt.merchant_order_no,
                    "provider": attempt.provider,
                    "reason": "create_timeout",
                },
            )
            _alert_staff(
                session,
                title="支付下单结果未知",
                body=f"商户单号 {attempt.merchant_order_no} 下单超时，需用同一单号查单并跟进。",
                payload={"attempt_id": attempt.id, "merchant_order_no": attempt.merchant_order_no},
            )
        return _intent_of(attempt)

    raw_status = str(result.get("status") or PAYMENT_RESULT_PENDING)
    action = result.get("action") or {"type": ACTION_NONE, "url": ""}
    status = PAYMENT_RESULT_PENDING
    _apply_create_response(
        session,
        attempt,
        status=status,
        action=dict(action),
        action_expires_at=result.get("expires_at") or attempt.action_expires_at,
    )
    if raw_status == PAYMENT_RESULT_SUCCEEDED:
        # 渠道应答声称成功不足以入账：成功只认已验签通知或服务端主动查单。
        attempt = query_attempt(session, attempt, force=True)
    return _intent_of(attempt)


def _apply_create_response(
    session: Session,
    attempt: PaymentAttempt,
    *,
    status: str,
    action: dict[str, Any] | None,
    action_expires_at: int | None = None,
    failure_reason: str | None = None,
) -> None:
    """条件更新：只有仍处于 ``CREATED`` 才能写回下单结果。

    若回调先到并已把尝试写成 ``SUCCEEDED``，后到的创建响应不得把它降级为
    ``PENDING``/``UNKNOWN``（08 文档「回调先到而创建支付响应后到」）。
    """
    values: dict[str, Any] = {
        "status": status,
        "updated_at": now_ms(),
        "version": PaymentAttempt.version + 1,
    }
    if action is not None:
        values["action"] = action
    if action_expires_at is not None:
        values["action_expires_at"] = action_expires_at
    if failure_reason is not None:
        values["failure_reason"] = failure_reason
    session.execute(
        sa.update(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id, PaymentAttempt.status == "CREATED")
        .values(**values)
    )
    session.flush()
    session.expire(attempt)


# ------------------------------------------------------------------ 查单
def query_attempt(session: Session, attempt: PaymentAttempt, *, force: bool = False) -> PaymentAttempt:
    """服务端主动查单（唯一被认可的成功来源之一）。

    - 只在活动状态查单，``SUCCEEDED``/``CLOSED``/``FAILED`` 不再查；
    - 受最小间隔约束，避免前端轮询把渠道打爆；
    - 渠道明确回答未成功时才降级；成功记录永不改回失败。
    """
    if attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        return attempt
    if (
        not force
        and attempt.last_queried_at is not None
        and now_ms() - attempt.last_queried_at < QUERY_MIN_INTERVAL_MS
    ):
        return attempt
    gateway = _gateway(attempt.provider)
    try:
        result = gateway.query(attempt.merchant_order_no)
    except GatewayError as exc:
        audit.record(
            session,
            actor_type="system",
            action="payment.query_failed",
            resource_type="payment_attempt",
            resource_id=attempt.id,
            reason=str(exc)[:200],
        )
        return attempt
    attempt.last_queried_at = now_ms()
    attempt.query_attempts += 1
    session.flush()

    status = str(result.get("status") or "")
    if status == PAYMENT_RESULT_SUCCEEDED:
        transaction_id = str(result.get("provider_transaction_id") or "")
        verified = VerifiedPayment(
            provider=attempt.provider,
            provider_transaction_id=transaction_id,
            merchant_order_no=attempt.merchant_order_no,
            amount_cents=int(result.get("amount_cents") or attempt.expected_cents),
            currency=str(result.get("currency") or attempt.currency),
            paid_at=result.get("paid_at"),
            raw_digest=str(result.get("raw_digest") or payload_digest(f"query:{attempt.id}:{status}".encode())),
            event_id=result.get("event_id")
            or f"query-{attempt.provider}-{transaction_id or attempt.merchant_order_no}",
        )
        apply_verified_payment(session, verified)
        session.expire(attempt)
        return attempt
    if status in (PAYMENT_RESULT_CLOSED, PAYMENT_RESULT_FAILED, PAYMENT_RESULT_NOT_FOUND) and attempt.status in (
        "CREATED",
        "PENDING",
        "UNKNOWN",
    ):
        # 渠道明确回答未成功才降级；成功记录永不改回失败。
        attempt.status = "CLOSED" if status != PAYMENT_RESULT_FAILED else "FAILED"
        attempt.closed_at = now_ms()
        attempt.closed_reason = f"query:{status}"
        attempt.updated_at = now_ms()
        session.flush()
    return attempt


# ------------------------------------------------------------------ 确认收款
def apply_verified_payment(session: Session, verified: VerifiedPayment) -> dict[str, Any]:
    """落实一笔已验签收款：去重 + 收款 + 分配 + 应付状态 + 库存/名额 + outbox。

    调用方（回调入口或任务）负责提交事务；收款成功但本地事务失败时不给渠道成功应答，
    等渠道重试或用同一单号查单，不会重复加金额。
    """
    provider = (verified.provider or "").strip().lower()
    event_key = verified.event_id or f"biz-{digest_sample(provider, verified)}"
    payload_path = payload_relative_path(verified.raw_digest)

    existing = repo.get_event(session, provider=provider, event_id=event_key)
    if existing is not None and existing.processing_result != "PENDING":
        # 通知已处理：返回原成功结果，不再加金额、扣库存、发积分。
        return {
            "status": existing.processing_result,
            "duplicate": True,
            "attempt_id": existing.payment_attempt_id,
            "order_id": None,
            "applied_cents": 0,
            "overpayment_cents": 0,
        }

    attempt = repo.get_attempt_by_merchant_order_no(session, verified.merchant_order_no)
    event = _insert_event_barrier(
        session,
        provider=provider,
        event_key=event_key,
        verified=verified,
        payload_path=payload_path,
        attempt=attempt,
    )
    if event is None:
        return {
            "status": "DUPLICATE",
            "duplicate": True,
            "attempt_id": attempt.id if attempt else None,
            "order_id": None,
            "applied_cents": 0,
            "overpayment_cents": 0,
        }

    if attempt is None:
        # 未登记商户单号：真实收款不能丢，记事件 + 安全告警，绝不发货。
        event.processing_result = "UNKNOWN_ORDER"
        event.rejection_reason = "merchant_order_no_not_found"
        session.flush()
        audit.record(
            session,
            actor_type="provider",
            action="payment.notification_unknown_order",
            resource_type="payment_event",
            resource_id=event.id,
            reason="商户单号未在本地登记",
            after={
                "merchant_order_no": verified.merchant_order_no,
                "amount_cents": verified.amount_cents,
                "provider_transaction_id": verified.provider_transaction_id,
            },
        )
        _alert_staff(
            session,
            title="收到未登记商户单号的支付通知",
            body=f"{provider} 通知的商户单号 {verified.merchant_order_no} 本地不存在，需人工核对。",
            payload={"event_id": event_key, "amount_cents": verified.amount_cents},
        )
        enqueue(
            session,
            event_type="payment.unknown",
            aggregate_id=event.id,
            payload={"reason": "unknown_merchant_order_no", "provider": provider},
        )
        return {
            "status": "UNKNOWN_ORDER",
            "duplicate": False,
            "attempt_id": None,
            "order_id": None,
            "applied_cents": 0,
            "overpayment_cents": 0,
        }

    plan = _plan_allocation(session, attempt, verified)
    if plan.classification == "QUARANTINED":
        repo.create_allocation(
            session,
            payment_id=attempt.id,
            order_id=attempt.order_id,
            installment_id=attempt.installment_id,
            amount_cents=verified.amount_cents,
            currency=verified.currency,
            classification="QUARANTINED",
            note=plan.reason[:200],
        )
        event.processing_result = "QUARANTINED"
        event.rejection_reason = plan.reason[:200]
        session.flush()
        audit.record(
            session,
            actor_type="provider",
            action="payment.quarantined",
            resource_type="payment_attempt",
            resource_id=attempt.id,
            reason=plan.reason,
            after={
                "amount_cents": verified.amount_cents,
                "currency": verified.currency,
                "provider_transaction_id": verified.provider_transaction_id,
            },
        )
        _alert_staff(
            session,
            title="收款需人工核对（已隔离）",
            body=f"{provider} 收款与本地支付尝试不匹配（{plan.reason}），已隔离待处置，未发货。",
            payload={"attempt_id": attempt.id, "reason": plan.reason},
        )
        enqueue(
            session,
            event_type="payment.unknown",
            aggregate_id=attempt.id,
            payload={"reason": plan.reason, "attempt_id": attempt.id},
        )
        return {
            "status": "QUARANTINED",
            "classification": "QUARANTINED",
            "duplicate": False,
            "attempt_id": attempt.id,
            "order_id": attempt.order_id,
            "applied_cents": 0,
            "overpayment_cents": 0,
            "reason": plan.reason,
        }

    _mark_attempt_succeeded(session, attempt, verified)
    applied_allocation: PaymentAllocation | None = None
    overpayment_allocation: PaymentAllocation | None = None
    if plan.applied_cents:
        applied_allocation = repo.create_allocation(
            session,
            payment_id=attempt.id,
            order_id=attempt.order_id,
            installment_id=attempt.installment_id,
            amount_cents=plan.applied_cents,
            currency=verified.currency,
            classification="APPLIED",
            note=f"confirmed:{verified.provider_transaction_id}"[:200],
        )
    if plan.overpayment_cents:
        overpayment_allocation = repo.create_allocation(
            session,
            payment_id=attempt.id,
            order_id=attempt.order_id,
            installment_id=attempt.installment_id,
            amount_cents=plan.overpayment_cents,
            currency=verified.currency,
            classification="OVERPAYMENT",
            note=f"overpayment:{verified.provider_transaction_id}"[:200],
        )
    event.processing_result = "OVERPAYMENT" if not plan.applied_cents else "APPLIED"
    session.flush()

    commerce_result: dict[str, Any] | None = None
    if applied_allocation is not None:
        _close_other_active_attempts(session, attempt)
        commerce_result = bridge.on_installment_paid(
            session, order_id=attempt.order_id, installment_id=attempt.installment_id
        )

    enqueue(
        session,
        event_type="payment.verified",
        aggregate_id=attempt.id,
        event_id=f"evt_{digest({'payment': event_key})[:32]}",
        payload={
            "attempt_id": attempt.id,
            "order_id": attempt.order_id,
            "installment_id": attempt.installment_id,
            "provider": provider,
            "provider_transaction_id": verified.provider_transaction_id,
            "applied_cents": plan.applied_cents,
            "overpayment_cents": plan.overpayment_cents,
        },
    )
    if overpayment_allocation is not None:
        _enqueue_overpayment_refund(session, attempt, overpayment_allocation, verified)
    audit.record(
        session,
        actor_type="provider",
        action="payment.verified",
        resource_type="payment_attempt",
        resource_id=attempt.id,
        after={
            "applied_cents": plan.applied_cents,
            "overpayment_cents": plan.overpayment_cents,
            "provider_transaction_id": verified.provider_transaction_id,
        },
    )
    return {
        "status": "APPLIED" if plan.applied_cents else "OVERPAYMENT",
        "classification": plan.classification,
        "duplicate": False,
        "attempt_id": attempt.id,
        "order_id": attempt.order_id,
        "installment_id": attempt.installment_id,
        "applied_cents": plan.applied_cents,
        "overpayment_cents": plan.overpayment_cents,
        "allocation_id": applied_allocation.id if applied_allocation else None,
        "commerce": commerce_result,
    }


def digest_sample(provider: str, verified: VerifiedPayment) -> str:
    """无事件号时的规范化业务键（provider+渠道交易号+商户单号+金额）。"""
    payload = (
        f"{provider}|{verified.provider_transaction_id}|{verified.merchant_order_no}|"
        f"{verified.amount_cents}|{verified.currency}"
    )
    return digest(payload)[:48]


def _plan_allocation(
    session: Session, attempt: PaymentAttempt, verified: VerifiedPayment
) -> _AllocationPlan:
    """业务核对与分配计划（隔离 / 全多付 / 部分应用）。"""
    if attempt.provider != (verified.provider or "").strip().lower():
        return _AllocationPlan("QUARANTINED", 0, 0, "provider_mismatch")
    if (verified.currency or "").upper() != (attempt.currency or "").upper():
        return _AllocationPlan("QUARANTINED", 0, 0, "currency_mismatch")
    if verified.amount_cents != attempt.expected_cents:
        return _AllocationPlan("QUARANTINED", 0, 0, "amount_mismatch")
    other = repo.get_attempt_by_provider_transaction(
        session,
        provider=attempt.provider,
        provider_transaction_id=verified.provider_transaction_id,
        exclude_id=attempt.id,
    )
    if other is not None:
        # 同一渠道交易号已挂在别的尝试上：重复收款，人工核对后再处理。
        return _AllocationPlan("QUARANTINED", 0, 0, "duplicate_provider_transaction")
    expected_ref = _expected_account_ref(attempt.provider)
    if expected_ref and verified.account_ref and verified.account_ref != expected_ref:
        return _AllocationPlan("QUARANTINED", 0, 0, "account_mismatch")
    if not verified.provider_transaction_id:
        return _AllocationPlan("QUARANTINED", 0, 0, "missing_provider_transaction_id")

    order = bridge.load_order(session, attempt.order_id)
    installment = bridge.load_installment(session, attempt.installment_id)
    if order is None or installment is None:
        return _AllocationPlan("QUARANTINED", 0, 0, "order_reference_missing")
    if (
        order.get("status") in CLOSED_ORDER_STATUSES
        or str(installment.get("state") or "") in CLOSED_INSTALLMENT_STATES
    ):
        # 付款晚于已确认关闭及释放：记真实收款为多付/异常待处置，不重新扣库存。
        return _AllocationPlan("OVERPAYMENT", 0, verified.amount_cents, "installment_closed")
    applied = repo.applied_sum(session, installment_id=attempt.installment_id)
    remaining = int(installment.get("amount_cents") or 0) - applied
    if remaining <= 0:
        return _AllocationPlan("OVERPAYMENT", 0, verified.amount_cents, "no_remaining_balance")
    applied_part = min(remaining, verified.amount_cents)
    return _AllocationPlan(
        "APPLIED", applied_part, verified.amount_cents - applied_part, "applied"
    )


def _expected_account_ref(provider: str) -> str | None:
    try:
        return get_gateway(provider).expected_account_ref()
    except (AppError, GatewayNotConfigured):
        return None


def _mark_attempt_succeeded(session: Session, attempt: PaymentAttempt, verified: VerifiedPayment) -> None:
    """写成功状态。已有成功记录时不覆盖原渠道交易号（成功不可回退、不可改写）。"""
    try:
        with session.begin_nested():
            session.execute(
                sa.update(PaymentAttempt)
                .where(PaymentAttempt.id == attempt.id, PaymentAttempt.status != "SUCCEEDED")
                .values(
                    status="SUCCEEDED",
                    provider_transaction_id=verified.provider_transaction_id,
                    verified_at=verified.paid_at or now_ms(),
                    failure_reason=None,
                    updated_at=now_ms(),
                    version=PaymentAttempt.version + 1,
                )
            )
            session.flush()
    except IntegrityError as exc:
        raise AppError("STATE_CONFLICT", "渠道交易号已用于其他支付尝试，需人工核对。") from exc
    session.expire(attempt)


def _close_other_active_attempts(session: Session, attempt: PaymentAttempt) -> None:
    """已收满该应付项后，其他渠道的活动尝试不再需要（避免用户重复支付）。"""
    session.execute(
        sa.update(PaymentAttempt)
        .where(
            PaymentAttempt.installment_id == attempt.installment_id,
            PaymentAttempt.id != attempt.id,
            PaymentAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
        )
        .values(
            status="CLOSED",
            closed_at=now_ms(),
            closed_reason="installment_paid",
            updated_at=now_ms(),
            version=PaymentAttempt.version + 1,
        )
    )
    session.flush()


def _enqueue_overpayment_refund(
    session: Session,
    attempt: PaymentAttempt,
    allocation: PaymentAllocation,
    verified: VerifiedPayment,
) -> None:
    """多付款必须走退款流程，不能留在账上充当订单实收。"""
    enqueue(
        session,
        event_type="refund.requested",
        aggregate_id=allocation.id,
        payload={
            "reason": "overpayment",
            "allocation_id": allocation.id,
            "payment_id": attempt.id,
            "order_id": attempt.order_id,
            "amount_cents": allocation.amount_cents,
            "provider": attempt.provider,
            "provider_transaction_id": verified.provider_transaction_id,
        },
    )
    _alert_staff(
        session,
        title="出现多付款，需发起退款",
        body=(
            f"订单 {attempt.order_id} 收到超出应付余额的收款 "
            f"{allocation.amount_cents} 分，已记为 OVERPAYMENT，请按原渠道退款。"
        ),
        payload={"allocation_id": allocation.id, "order_id": attempt.order_id},
    )


# ------------------------------------------------------------------ 关闭与汇总
def close_payment_attempts_for_installment(
    session: Session, *, installment_id: str, reason: str
) -> int:
    """关闭该应付项的活动支付尝试，返回真正关闭的数量。

    ``UNKNOWN`` 必须先查单：查不到成功才允许关闭；渠道不可用时保留活动状态并登记
    待处理事件（不能假装已关闭）。
    """
    closed = 0
    for attempt in repo.list_active_attempts_for_installment(session, installment_id):
        try:
            gateway = _gateway(attempt.provider)
        except AppError:
            continue
        if attempt.status == "UNKNOWN":
            attempt = query_attempt(session, attempt, force=True)
            if attempt.status == "SUCCEEDED":
                continue
        try:
            result = gateway.close(attempt.merchant_order_no)
        except GatewayError as exc:
            audit.record(
                session,
                actor_type="system",
                action="payment.close_failed",
                resource_type="payment_attempt",
                resource_id=attempt.id,
                reason=str(exc)[:200],
            )
            enqueue(
                session,
                event_type="payment.unknown",
                aggregate_id=attempt.id,
                payload={"reason": "close_failed", "merchant_order_no": attempt.merchant_order_no},
            )
            continue
        if str(result.get("status") or "") == PAYMENT_RESULT_SUCCEEDED:
            # 渠道回答「已支付」：不能当关闭，改为查单核对真实结果。
            query_attempt(session, attempt, force=True)
            continue
        session.execute(
            sa.update(PaymentAttempt)
            .where(
                PaymentAttempt.id == attempt.id,
                PaymentAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
            .values(
                status="CLOSED",
                closed_at=now_ms(),
                closed_reason=reason[:200],
                updated_at=now_ms(),
                version=PaymentAttempt.version + 1,
            )
        )
        session.flush()
        session.expire(attempt)
        closed += 1
    return closed


def mark_order_closing(session: Session, *, order_id: str, reason: str) -> None:
    """订单进入关闭流程：先关闭或查询渠道，再决定能否取消（08 文档库存预留第 4 条）。"""
    closed = 0
    for installment in bridge.load_installments_for_order(session, order_id):
        closed += close_payment_attempts_for_installment(
            session, installment_id=str(installment["id"]), reason=reason
        )
    audit.record(
        session,
        actor_type="system",
        action="payment.order_closing",
        resource_type="order",
        resource_id=order_id,
        reason=reason[:200],
        after={"closed_attempts": closed},
    )


def order_paid_summary(session: Session, *, order_id: str) -> dict[str, Any]:
    """订单实收汇总。``paid_cents`` 只计 APPLIED 分配；多付款不计入。"""
    allocations = repo.list_allocations_for_order(session, order_id)
    return {
        "paid_cents": repo.applied_sum(session, order_id=order_id),
        "refunded_applied_cents": repo.refunded_applied_sum(session, order_id=order_id),
        "allocations": [
            {
                "id": row.id,
                "payment_id": row.payment_id,
                "installment_id": row.installment_id,
                "amount_cents": row.amount_cents,
                "currency": row.currency,
                "classification": row.classification,
                "refunded_cents": row.refunded_cents,
                "reserved_refund_cents": row.reserved_refund_cents,
                "refundable_cents": max(
                    0, row.amount_cents - row.refunded_cents - row.reserved_refund_cents
                ),
                "created_at": iso(row.created_at),
            }
            for row in allocations
        ],
    }


# ------------------------------------------------------------------ 退款
def create_refund_request(
    session: Session,
    *,
    payment_id: str,
    allocation_id: str,
    amount_cents: int,
    items: list[dict[str, Any]],
    reason: str,
    actor_id: str | None,
    kind: str | None = None,
) -> Refund:
    """登记退款申请（状态 ``REQUESTED``）。审批与执行分开授权，执行在 ``request_refund``。

    ``items`` 是退款分摊（商品行/运费/多付/优惠），其和必须等于退款金额：
    「部分退款分配到商品行、运费及已用优惠；不能超过各行剩余实付分摊」。
    """
    allocation = repo.get_allocation(session, allocation_id)
    if allocation is None or allocation.payment_id != payment_id:
        raise AppError("RESOURCE_NOT_FOUND")
    if allocation.classification == "QUARANTINED":
        raise AppError("STATE_CONFLICT", "隔离中的收款需人工核对后才能发起退款。")
    if amount_cents <= 0:
        raise AppError("VALIDATION_ERROR", "退款金额必须大于 0。", fields={"amount_cents": "必须大于 0"})
    if amount_cents > allocation.amount_cents:
        raise AppError("REFUND_LIMIT_EXCEEDED", "退款金额超过该笔收款金额。")
    attempt = repo.get_attempt(session, payment_id)
    if attempt is None:
        raise AppError("RESOURCE_NOT_FOUND")
    resolved_kind = kind or ("overpayment" if allocation.classification == "OVERPAYMENT" else "normal")
    refund = repo.create_refund(
        session,
        refund_no=new_refund_no(attempt.provider),
        order_id=allocation.order_id,
        payment_id=payment_id,
        allocation_id=allocation_id,
        installment_id=allocation.installment_id,
        user_id=attempt.user_id,
        provider=attempt.provider,
        amount_cents=amount_cents,
        currency=allocation.currency,
        reason=reason,
        kind=resolved_kind,
        requested_by=actor_id,
    )
    for item in items:
        item_kind = str(item.get("kind") or "goods")
        repo.create_refund_item(
            session,
            refund_id=refund.id,
            kind=item_kind,
            amount_cents=int(item.get("amount_cents") or 0),
            order_item_id=item.get("order_item_id"),
            description=item.get("description"),
        )
    total = repo.refund_item_total(session, refund.id)
    if total != amount_cents:
        raise AppError(
            "VALIDATION_ERROR",
            "退款分摊之和必须等于退款金额。",
            fields={"items": f"分摊合计 {total} 分，退款金额 {amount_cents} 分"},
        )
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="refund.requested",
        resource_type="refund",
        resource_id=refund.id,
        reason=reason[:200],
        after={
            "amount_cents": amount_cents,
            "kind": resolved_kind,
            "payment_id": payment_id,
            "allocation_id": allocation_id,
        },
    )
    return refund


def approve_refund(session: Session, *, refund_id: str, actor_id: str, reason: str) -> Refund:
    """审批退款。审批人与申请人必须是不同人员（审批与执行分开授权）。"""
    refund = repo.get_refund(session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if refund.status != "REQUESTED":
        raise AppError("STATE_CONFLICT", f"退款当前状态（{refund.status}）不允许审批。")
    if refund.requested_by and refund.requested_by == actor_id:
        raise AppError("FORBIDDEN", "退款审批不能由申请人本人完成。")
    refund.status = "APPROVED"
    refund.approved_by = actor_id
    refund.approved_at = now_ms()
    refund.updated_at = now_ms()
    refund.version += 1
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="refund.approved",
        resource_type="refund",
        resource_id=refund.id,
        reason=reason[:200],
    )
    return refund


def reject_refund(session: Session, *, refund_id: str, actor_id: str, reason: str) -> Refund:
    """驳回退款申请（此时尚未占用可退额度，无需释放）。"""
    refund = repo.get_refund(session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if refund.status not in ("REQUESTED", "APPROVED"):
        raise AppError("STATE_CONFLICT", f"退款当前状态（{refund.status}）不允许驳回。")
    refund.status = "REJECTED"
    refund.resolved_at = now_ms()
    refund.failure_reason = reason[:200]
    refund.updated_at = now_ms()
    refund.version += 1
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="refund.rejected",
        resource_type="refund",
        resource_id=refund.id,
        reason=reason[:200],
    )
    return refund


def _refund_outcome(refund: Refund) -> RefundOutcome:
    return RefundOutcome(
        refund_no=refund.refund_no,
        provider_refund_id=refund.provider_refund_id,
        amount_cents=refund.amount_cents,
        status=_REFUND_OUTCOME_STATUS.get(refund.status, "UNKNOWN"),
    )


def request_refund(
    session: Session, *, refund_id: str, actor_id: str, idempotency_key: str
) -> RefundOutcome:
    """执行退款：原子预留可退额度 → 按原渠道退款 → 写回渠道结果。

    同一退款号重试使用同一幂等键返回同资源；并发退同一笔款时只有一个能把条件
    ``UPDATE`` 的 ``rowcount`` 拿到 1，另一个得到 ``REFUND_LIMIT_EXCEEDED``。
    """
    refund = repo.get_refund(session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if refund.status == "SUCCEEDED":
        # 已成功的退款是终态：重复请求返回原结果，不再动账。
        return _refund_outcome(refund)
    if refund.status == "REJECTED":
        raise AppError("STATE_CONFLICT", "该退款已被驳回，不能执行。")

    call = begin_idempotent(
        session,
        actor_id=actor_id,
        operation="refund.request",
        key=idempotency_key,
        request_hash=digest({"refund_id": refund_id, "amount_cents": refund.amount_cents}),
    )
    if call.replay is not None:
        snapshot = call.replay
        return RefundOutcome(
            refund_no=str(snapshot["refund_no"]),
            provider_refund_id=snapshot.get("provider_refund_id"),
            amount_cents=int(snapshot["amount_cents"]),
            status=_REFUND_OUTCOME_STATUS.get(str(snapshot.get("status")), "UNKNOWN"),
        )

    if refund.status in ("PROCESSING", "UNKNOWN"):
        # 重试使用同一退款号：先查证渠道结果，不重复发起。
        _sync_refund_from_channel(session, refund)
        complete_idempotent(
            session,
            call,
            resource_id=refund.id,
            snapshot=_refund_snapshot(refund),
        )
        return _refund_outcome(refund)
    if refund.status != "APPROVED":
        raise AppError("STATE_CONFLICT", "退款需先通过审批才能执行。")

    allocation = repo.get_allocation(session, refund.allocation_id)
    if allocation is None:
        raise AppError("STATE_CONFLICT", "退款对应的收款分配不存在，需人工核对。")
    if allocation.classification == "QUARANTINED":
        raise AppError("STATE_CONFLICT", "隔离中的收款需人工核对后才能退款。")
    if repo.refund_item_total(session, refund.id) != refund.amount_cents:
        raise AppError("VALIDATION_ERROR", "退款分摊之和必须等于退款金额。")
    if not repo.reserve_refund_quota(
        session, allocation_id=allocation.id, amount_cents=refund.amount_cents
    ):
        raise AppError(
            "REFUND_LIMIT_EXCEEDED",
            "可退金额不足（可能已有其他退款在途或已完成），请核对后重试。",
        )
    refund.status = "PROCESSING"
    refund.reserved_at = now_ms()
    refund.requested_by = refund.requested_by or actor_id
    refund.updated_at = now_ms()
    refund.version += 1
    session.flush()

    attempt = repo.get_attempt(session, refund.payment_id)
    gateway = _gateway(refund.provider)
    try:
        result = gateway.refund(
            attempt.merchant_order_no if attempt else refund.payment_id,
            refund.refund_no,
            refund.amount_cents,
            refund.reason,
        )
    except GatewayError as exc:
        if exc.retryable:
            # 结果未知：预留继续占用，等查单；失败或未知都不能直接说钱已退。
            refund.status = "UNKNOWN"
            refund.failure_reason = str(exc)[:200]
            session.flush()
            enqueue(
                session,
                event_type="payment.unknown",
                aggregate_id=refund.id,
                payload={"reason": "refund_unknown", "refund_no": refund.refund_no},
            )
            complete_idempotent(session, call, resource_id=refund.id, snapshot=_refund_snapshot(refund))
            return _refund_outcome(refund)
        # 渠道明确拒绝：释放预留并记失败。
        apply_refund_outcome(
            session,
            refund_id=refund.id,
            status=REFUND_RESULT_FAILED,
            provider_refund_id=None,
            amount_cents=refund.amount_cents,
        )
        refund.failure_reason = str(exc)[:200]
        session.flush()
        complete_idempotent(session, call, resource_id=refund.id, snapshot=_refund_snapshot(refund))
        return _refund_outcome(refund)

    apply_refund_outcome(
        session,
        refund_id=refund.id,
        status=str(result.get("status") or REFUND_RESULT_UNKNOWN),
        provider_refund_id=result.get("provider_refund_id"),
        amount_cents=int(result.get("amount_cents") or refund.amount_cents),
    )
    session.expire(refund)
    complete_idempotent(session, call, resource_id=refund.id, snapshot=_refund_snapshot(refund))
    return _refund_outcome(refund)


def _refund_snapshot(refund: Refund) -> dict[str, Any]:
    return {
        "refund_no": refund.refund_no,
        "status": refund.status,
        "amount_cents": refund.amount_cents,
        "provider_refund_id": refund.provider_refund_id,
    }


def _sync_refund_from_channel(session: Session, refund: Refund) -> None:
    """UNKNOWN/PROCESSING 的退款继续查单（UNKNOWN 必须查单，不能直接改判）。"""
    gateway = _gateway(refund.provider)
    try:
        result = gateway.query_refund(refund.refund_no)
    except GatewayError as exc:
        audit.record(
            session,
            actor_type="system",
            action="refund.query_failed",
            resource_type="refund",
            resource_id=refund.id,
            reason=str(exc)[:200],
        )
        return
    status = str(result.get("status") or "")
    if status in (REFUND_RESULT_SUCCEEDED, REFUND_RESULT_FAILED, REFUND_RESULT_PROCESSING):
        apply_refund_outcome(
            session,
            refund_id=refund.id,
            status=status,
            provider_refund_id=result.get("provider_refund_id"),
            amount_cents=int(result.get("amount_cents") or refund.amount_cents),
        )
        session.expire(refund)
    elif status == REFUND_RESULT_NOT_FOUND:
        audit.record(
            session,
            actor_type="system",
            action="refund.query_not_found",
            resource_type="refund",
            resource_id=refund.id,
            reason="渠道尚未返回该退款单",
        )


def apply_refund_outcome(
    session: Session,
    *,
    refund_id: str,
    status: str,
    provider_refund_id: str | None,
    amount_cents: int,
) -> dict[str, Any]:
    """写回渠道退款结果（幂等）。

    ``SUCCEEDED``：预留转已退款 + outbox ``refund.succeeded`` + commerce 端口调整；
    ``FAILED``：渠道证明未退才释放预留；``PROCESSING``/``UNKNOWN``：保留预留继续查单。
    """
    if status not in (REFUND_RESULT_SUCCEEDED, REFUND_RESULT_FAILED, REFUND_RESULT_PROCESSING, REFUND_RESULT_UNKNOWN):
        raise AppError("VALIDATION_ERROR", "非法的退款结果状态。", fields={"status": "非法状态"})
    refund = repo.get_refund(session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if amount_cents != refund.amount_cents:
        raise AppError("STATE_CONFLICT", "渠道退款金额与退款单不一致，需人工核对。")
    if refund.status == "SUCCEEDED":
        # 退款通知重复：只一笔退款效果。
        return {
            "refund_id": refund.id,
            "refund_no": refund.refund_no,
            "status": "SUCCEEDED",
            "duplicate": True,
            "amount_cents": refund.amount_cents,
            "refunded_applied_cents": repo.refunded_applied_sum(session, order_id=refund.order_id),
        }

    if status == REFUND_RESULT_SUCCEEDED:
        settled = repo.settle_refund_quota(
            session, allocation_id=refund.allocation_id, amount_cents=refund.amount_cents
        )
        if not settled:
            raise AppError("STATE_CONFLICT", "退款预留不足，需人工核对后再确认退款成功。")
        refund.status = "SUCCEEDED"
        refund.provider_refund_id = provider_refund_id or refund.provider_refund_id
        refund.succeeded_at = now_ms()
        refund.resolved_at = now_ms()
        refund.failure_reason = None
        refund.updated_at = now_ms()
        refund.version += 1
        session.flush()
        sums = repo.refund_item_sums(session, refund.id)
        enqueue(
            session,
            event_type="refund.succeeded",
            aggregate_id=refund.id,
            event_id=f"evt_{digest({'refund': refund.refund_no})[:32]}",
            payload={
                "refund_id": refund.id,
                "refund_no": refund.refund_no,
                "order_id": refund.order_id,
                "amount_cents": refund.amount_cents,
                "goods_cents": sums["goods"],
                "shipping_cents": sums["shipping"],
                "overpayment_cents": sums["overpayment"],
                "provider": refund.provider,
            },
        )
        commerce_result = bridge.on_refund_succeeded(
            session,
            order_id=refund.order_id,
            refund_id=refund.id,
            goods_cents=sums["goods"],
            shipping_cents=sums["shipping"],
            # 按行分摊（08 文档：部分退款分配到商品行、运费及已用优惠）——
            # 商务模块据此更新每一行的已退款金额，不再只有订单级汇总。
            items=[
                {
                    "kind": row.kind,
                    "order_item_id": row.order_item_id,
                    "amount_cents": row.amount_cents,
                }
                for row in repo.list_refund_items(session, refund.id)
            ],
        )
        audit.record(
            session,
            actor_type="system",
            action="refund.succeeded",
            resource_type="refund",
            resource_id=refund.id,
            after={
                "amount_cents": refund.amount_cents,
                "provider_refund_id": refund.provider_refund_id,
                "item_sums": sums,
            },
        )
        return {
            "refund_id": refund.id,
            "refund_no": refund.refund_no,
            "status": "SUCCEEDED",
            "duplicate": False,
            "amount_cents": refund.amount_cents,
            "goods_cents": sums["goods"],
            "shipping_cents": sums["shipping"],
            "refunded_applied_cents": repo.refunded_applied_sum(session, order_id=refund.order_id),
            "commerce": commerce_result,
        }

    if status == REFUND_RESULT_FAILED:
        # 明确失败且渠道证明未退：释放预留，额度回到可退池。
        released = repo.release_refund_quota(
            session, allocation_id=refund.allocation_id, amount_cents=refund.amount_cents
        )
        refund.status = "FAILED"
        refund.resolved_at = now_ms()
        refund.failure_reason = refund.failure_reason or "渠道明确退款失败"
        refund.updated_at = now_ms()
        refund.version += 1
        session.flush()
        audit.record(
            session,
            actor_type="system",
            action="refund.failed",
            resource_type="refund",
            resource_id=refund.id,
            reason=refund.failure_reason,
            after={"released_reservation_cents": refund.amount_cents if released else 0},
        )
        return {
            "refund_id": refund.id,
            "refund_no": refund.refund_no,
            "status": "FAILED",
            "duplicate": False,
            "amount_cents": refund.amount_cents,
            "released_reservation_cents": refund.amount_cents if released else 0,
        }

    refund.status = status
    refund.provider_refund_id = provider_refund_id or refund.provider_refund_id
    refund.updated_at = now_ms()
    refund.version += 1
    session.flush()
    audit.record(
        session,
        actor_type="system",
        action="refund.pending_result",
        resource_type="refund",
        resource_id=refund.id,
        reason=status,
    )
    return {
        "refund_id": refund.id,
        "refund_no": refund.refund_no,
        "status": status,
        "duplicate": False,
        "amount_cents": refund.amount_cents,
    }


# ------------------------------------------------------------------ 通知入口留证
def record_rejected_notification(
    session: Session,
    *,
    provider: str,
    raw_digest: str,
    payload_path: str,
    event_key: str | None,
    reason: str,
    amount_cents: int | None = None,
) -> None:
    """记录未通过验签的通知：不入账，但保留原始字节摘要与安全事件。"""
    key = event_key or f"reject-{raw_digest[:48]}"
    verified = VerifiedPayment(
        provider=provider,
        provider_transaction_id="",
        merchant_order_no="",
        amount_cents=amount_cents or 0,
        currency="",
        paid_at=None,
        raw_digest=raw_digest,
        event_id=key,
    )
    event = _insert_event_barrier(
        session,
        provider=provider,
        event_key=key,
        verified=verified,
        payload_path=payload_path,
        attempt=None,
        verified_flag=False,
        rejection_reason=reason[:200],
    )
    if event is not None:
        event.processing_result = "REJECTED"
        session.flush()
    audit.record(
        session,
        actor_type="provider",
        action="payment.notification_rejected",
        resource_type="payment_event",
        resource_id=event.id if event is not None else None,
        reason=reason[:200],
        after={"provider": provider, "payload_digest": raw_digest},
    )


def record_ignored_notification(
    session: Session,
    *,
    provider: str,
    raw_digest: str,
    payload_path: str,
    event_key: str | None,
    event_type: str,
) -> None:
    """验签通过但不是成功收款（例如交易关闭）：留证但不入账。"""
    key = event_key or f"ignored-{raw_digest[:48]}"
    verified = VerifiedPayment(
        provider=provider,
        provider_transaction_id="",
        merchant_order_no="",
        amount_cents=0,
        currency="",
        paid_at=None,
        raw_digest=raw_digest,
        event_id=key,
    )
    event = _insert_event_barrier(
        session,
        provider=provider,
        event_key=key,
        verified=verified,
        payload_path=payload_path,
        attempt=None,
        event_type=event_type,
        rejection_reason="not_a_success_payment",
    )
    if event is not None:
        event.processing_result = "IGNORED"
        session.flush()


# ------------------------------------------------------------------ 对账
def run_reconciliation(
    session: Session, *, provider: str, bill_date: str, actor_id: str
) -> dict[str, Any]:
    """拉取账单并与本地收款/退款逐笔比较，保留重新对账历史。

    差异类型：渠道有本地无、本地成功渠道无、金额不符、重复支付、退款不符、手续费不符。
    **不提供批量「标记一致」**：每条差异都要人工处置并写明理由。
    """
    provider = (provider or "").strip().lower()
    gateway = _gateway(provider)
    revision = repo.latest_reconciliation_revision(session, provider=provider, bill_date=bill_date) + 1
    run = repo.create_reconciliation_run(
        session, provider=provider, bill_date=bill_date, revision=revision, actor_id=actor_id
    )
    try:
        raw = gateway.download_bill(bill_date)
        rows = gateway.parse_bill(bill_date, raw)
    except (GatewayError, GatewayNotConfigured):
        run.status = "FAILED"
        run.failure_reason = "渠道账单暂不可用"
        run.finished_at = now_ms()
        session.flush()
        raise AppError(
            "PROVIDER_UNAVAILABLE", "渠道账单暂不可用，请稍后重试或等待账单生成。"
        ) from None
    path, file_hash = _store_bill_file(
        provider=provider, bill_date=bill_date, revision=revision, raw=raw
    )
    run.bill_payload_path = path
    run.bill_file_hash = file_hash

    start, end = bill_day_bounds(bill_date)
    local_attempts = repo.list_succeeded_attempts_in_range(
        session, provider=provider, start_ms=start, end_ms=end
    )
    local_refunds = repo.list_succeeded_refunds_in_range(
        session, provider=provider, start_ms=start, end_ms=end
    )
    payment_rows = [row for row in rows if str(row.get("kind")) == "payment"]
    refund_rows = [row for row in rows if str(row.get("kind")) == "refund"]
    channel_by_order = {str(row.get("merchant_order_no")): row for row in payment_rows}
    local_by_order = {row.merchant_order_no: row for row in local_attempts}
    local_refunds_by_no = {row.refund_no: row for row in local_refunds}
    channel_refunds_by_no = {str(row.get("refund_no")): row for row in refund_rows}

    created: list[Any] = []
    seen_transactions: dict[str, int] = {}
    for row in payment_rows:
        order_no = str(row.get("merchant_order_no") or "")
        transaction_id = str(row.get("transaction_id") or "")
        amount = int(row.get("amount_cents") or 0)
        seen_transactions[transaction_id] = seen_transactions.get(transaction_id, 0) + 1
        local = local_by_order.get(order_no)
        if local is None:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="CHANNEL_ONLY",
                    merchant_order_no=order_no,
                    transaction_id=transaction_id,
                    channel_amount_cents=amount,
                    fee_cents=int(row.get("fee_cents") or 0),
                    evidence={"bill_date": bill_date, "row": row},
                )
            )
            continue
        local_amount = sum(item.amount_cents for item in repo.list_allocations_for_payment(session, local.id))
        if local.provider_transaction_id and transaction_id and local.provider_transaction_id != transaction_id:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="DUPLICATE_PAYMENT",
                    merchant_order_no=order_no,
                    transaction_id=transaction_id,
                    local_amount_cents=local_amount,
                    channel_amount_cents=amount,
                    evidence={"local_transaction_id": local.provider_transaction_id},
                )
            )
        elif local_amount != amount:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="AMOUNT_MISMATCH",
                    merchant_order_no=order_no,
                    transaction_id=transaction_id,
                    channel_amount_cents=amount,
                    local_amount_cents=local_amount,
                    evidence={"bill_date": bill_date},
                )
            )
        fee = int(row.get("fee_cents") or 0)
        if fee < 0 or fee > amount:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="FEE_MISMATCH",
                    merchant_order_no=order_no,
                    transaction_id=transaction_id,
                    channel_amount_cents=amount,
                    fee_cents=fee,
                    evidence={"reason": "账单手续费异常", "bill_date": bill_date},
                )
            )
    for transaction_id, count in seen_transactions.items():
        if count > 1 and transaction_id:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="DUPLICATE_PAYMENT",
                    transaction_id=transaction_id,
                    evidence={"channel_row_count": count},
                )
            )
    for order_no, local in local_by_order.items():
        if order_no not in channel_by_order:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="LOCAL_ONLY",
                    merchant_order_no=order_no,
                    transaction_id=local.provider_transaction_id,
                    local_amount_cents=sum(
                        item.amount_cents
                        for item in repo.list_allocations_for_payment(session, local.id)
                    ),
                    evidence={"bill_date": bill_date},
                )
            )
    for row in refund_rows:
        refund_no = str(row.get("refund_no") or "")
        amount = int(row.get("amount_cents") or 0)
        local_refund = local_refunds_by_no.get(refund_no)
        if local_refund is None or local_refund.amount_cents != amount:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="REFUND_MISMATCH",
                    refund_no=refund_no,
                    transaction_id=str(row.get("transaction_id") or ""),
                    merchant_order_no=str(row.get("merchant_order_no") or ""),
                    channel_amount_cents=amount,
                    local_amount_cents=local_refund.amount_cents if local_refund else None,
                    evidence={"bill_date": bill_date},
                )
            )
    for refund_no, local_refund in local_refunds_by_no.items():
        if refund_no not in channel_refunds_by_no:
            created.append(
                repo.create_difference(
                    session,
                    run=run,
                    difference_type="REFUND_MISMATCH",
                    refund_no=refund_no,
                    local_amount_cents=local_refund.amount_cents,
                    channel_amount_cents=None,
                    evidence={"reason": "本地成功退款未出现在账单", "bill_date": bill_date},
                )
            )

    run.channel_total_cents = sum(int(row.get("amount_cents") or 0) for row in payment_rows)
    run.local_total_cents = sum(
        sum(item.amount_cents for item in repo.list_allocations_for_payment(session, row.id))
        for row in local_attempts
    )
    run.channel_refund_cents = sum(int(row.get("amount_cents") or 0) for row in refund_rows)
    run.local_refund_cents = sum(row.amount_cents for row in local_refunds)
    run.difference_count = len(created)
    run.status = "COMPLETED"
    run.finished_at = now_ms()
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="reconciliation.run",
        resource_type="reconciliation_run",
        resource_id=run.id,
        after={
            "provider": provider,
            "bill_date": bill_date,
            "revision": revision,
            "difference_count": run.difference_count,
        },
    )
    return {
        "run_id": run.id,
        "provider": provider,
        "bill_date": bill_date,
        "revision": revision,
        "status": run.status,
        "difference_count": run.difference_count,
        "channel_total_cents": run.channel_total_cents,
        "local_total_cents": run.local_total_cents,
        "bill_file_hash": run.bill_file_hash,
        "differences": [
            {
                "id": row.id,
                "difference_type": row.difference_type,
                "status": row.status,
                "merchant_order_no": row.merchant_order_no,
                "transaction_id": row.transaction_id,
                "refund_no": row.refund_no,
                "channel_amount_cents": row.channel_amount_cents,
                "local_amount_cents": row.local_amount_cents,
            }
            for row in created
        ],
    }


def _store_bill_file(*, provider: str, bill_date: str, revision: int, raw: bytes) -> tuple[str, str]:
    """账单原文留存（对账证据），返回 ``(相对路径, sha256)``。"""
    settings = get_settings()
    relative = f"storage/{BILL_EVIDENCE_DIR}/{provider}/{bill_date}-r{revision}.bill"
    target = settings.data_dir / "storage" / BILL_EVIDENCE_DIR / provider / f"{bill_date}-r{revision}.bill"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return relative, payload_digest(raw)


def resolve_difference(
    session: Session, *, difference_id: str, resolution: str, actor_id: str, reason: str
) -> dict[str, Any]:
    """逐条处置对账差异。必须写明理由；不存在批量标记入口。"""
    if resolution not in DIFFERENCE_RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", "非法的差异处置方式。", fields={"resolution": "非法取值"})
    if not reason or len(reason.strip()) < 4:
        raise AppError("VALIDATION_ERROR", "关闭差异必须写明理由。", fields={"reason": "必填"})
    row = repo.get_difference(session, difference_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status == "RESOLVED":
        raise AppError("STATE_CONFLICT", "该差异已关闭，不能重复处置。")
    row.status = "RESOLVED"
    row.resolution = resolution
    row.resolution_reason = reason[:400]
    row.resolved_by = actor_id
    row.resolved_at = now_ms()
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="reconciliation.difference_resolved",
        resource_type="reconciliation_difference",
        resource_id=row.id,
        reason=reason[:200],
        after={"resolution": resolution, "difference_type": row.difference_type},
    )
    return {
        "difference_id": row.id,
        "status": row.status,
        "resolution": row.resolution,
        "resolved_at": iso(row.resolved_at),
    }


__all__ = [
    "IDEMPOTENCY_TTL_MS",
    "PAYMENT_EVIDENCE_DIR",
    "QUERY_MIN_INTERVAL_MS",
    "apply_refund_outcome",
    "apply_verified_payment",
    "approve_refund",
    "begin_idempotent",
    "close_payment_attempts_for_installment",
    "complete_idempotent",
    "create_intent",
    "create_refund_request",
    "mark_order_closing",
    "new_merchant_order_no",
    "new_refund_no",
    "order_paid_summary",
    "payload_relative_path",
    "persist_raw_payload",
    "query_attempt",
    "record_ignored_notification",
    "record_rejected_notification",
    "reject_refund",
    "request_refund",
    "resolve_difference",
    "run_reconciliation",
]
