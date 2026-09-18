"""支付模块运营后台路由。

覆盖收款与异常单、渠道查单、退款申请与批准、日对账与差异处置。

约束（08/03 文档）：
- 成功状态只认已验签通知或服务端主动查单；后台不能手工把订单标成已付；
- 退款**申请与批准分离**（``refund.request`` / ``refund.approve``），全程留痕；
- 可退额 = 原收款 − 已成功退款 − 在途预留；申请事务内原子预留额度；
- 失败或 ``UNKNOWN`` 不能直接说钱已退；
- 对账差异**不允许批量标记一致**，每笔都要有归因、证据与人工关闭理由。
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.adapters.payments.base import bill_date_of
from app.api.dependencies import ContextDep, CsrfDep, PrincipalDep, require_permission
from app.modules.payments import repository as repo
from app.modules.payments import service as svc
from app.modules.payments.models import (
    PaymentAttempt,
    ReconciliationDifference,
    ReconciliationRun,
    Refund,
)
from app.modules.shared.schemas import StrictDto
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, now_ms

admin_router = APIRouter(tags=["admin-payments"])


# ------------------------------------------------------------------ 输入
class RefundRequestInput(StrictDto):
    """退款申请。申请只登记金额与理由，实际出款由另一名员工批准。"""

    order_id: Annotated[str, Field(min_length=4, max_length=48)]
    amount_cents: Annotated[int, Field(gt=0, le=100_000_000)]
    reason: Annotated[str, Field(min_length=2, max_length=400)]
    after_sale_id: Annotated[str | None, Field(max_length=48)] = None
    goods_cents: Annotated[int, Field(ge=0)] = 0
    shipping_cents: Annotated[int, Field(ge=0)] = 0


class RefundDecisionInput(StrictDto):
    """退款批准或拒绝。批准与执行分离，重试使用同一退款号。"""

    reason: Annotated[str, Field(min_length=2, max_length=400)]


class DifferenceResolveInput(StrictDto):
    """对账差异处置。必须给出归因与理由，不允许静默忽略。"""

    resolution: Annotated[str, Field(min_length=4, max_length=24)]
    reason: Annotated[str, Field(min_length=2, max_length=400)]


class ReconciliationExportInput(StrictDto):
    """导出对账数据：需要独立权限与审计（10 文档）。"""

    provider: Annotated[str, Field(min_length=2, max_length=20)]
    bill_date: Annotated[str, Field(min_length=10, max_length=10)]
    reason: Annotated[str, Field(min_length=2, max_length=300)]


# ------------------------------------------------------------------ 收款
@admin_router.get(
    "/payments", response_model=dict, dependencies=[Depends(require_permission("payment.read"))]
)
def list_payments(
    ctx: ContextDep,
    status: str | None = None,
    provider: str | None = None,
    order_id: Annotated[str | None, Query(max_length=48)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """收款与异常单列表。只读；不返回渠道原始报文。"""
    conditions = []
    if status:
        conditions.append(PaymentAttempt.status == status)
    if provider:
        conditions.append(PaymentAttempt.provider == provider)
    if order_id:
        conditions.append(PaymentAttempt.order_id == order_id)
    total = int(
        ctx.session.scalar(sa.select(sa.func.count()).select_from(PaymentAttempt).where(*conditions))
        or 0
    )
    rows = list(
        ctx.session.scalars(
            sa.select(PaymentAttempt)
            .where(*conditions)
            .order_by(PaymentAttempt.created_at.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    )
    return {
        "items": [_attempt_payload(ctx.session, row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.get(
    "/payments/attention",
    response_model=dict,
    dependencies=[Depends(require_permission("payment.read"))],
)
def list_payment_attention(
    ctx: ContextDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """需要人工关注的收款：``UNKNOWN`` 与仍在处理中的支付单。"""
    rows = list(
        ctx.session.scalars(
            sa.select(PaymentAttempt)
            .where(PaymentAttempt.status.in_(("UNKNOWN", "PENDING", "CREATED")))
            .order_by(PaymentAttempt.created_at.desc())
            .limit(limit)
        )
    )
    return {
        "items": [_attempt_payload(ctx.session, row) for row in rows],
        "total": len(rows),
        "page": 1,
        "limit": limit,
        "has_more": False,
        "next_cursor": None,
        "notice": "结果未确认不等于失败：请用同一商户单号查单，不要新建支付单。",
    }


@admin_router.post(
    "/payments/{attempt_id}/query",
    response_model=dict,
    dependencies=[Depends(require_permission("payment.read"))],
)
def query_payment(
    attempt_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """按原商户单号向渠道查单。查单结果决定状态，人工截图不参与入账。"""
    attempt = repo.get_attempt(ctx.session, attempt_id)
    if attempt is None:
        raise AppError("RESOURCE_NOT_FOUND")
    updated = svc.query_attempt(ctx.session, attempt, force=True)
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="payment.query",
        resource_type="payment_attempt",
        resource_id=updated.id,
        after={"status": updated.status},
    )
    return _attempt_payload(ctx.session, updated)


# ------------------------------------------------------------------ 退款
@admin_router.get(
    "/refunds", response_model=dict, dependencies=[Depends(require_permission("refund.request"))]
)
def list_refunds(
    ctx: ContextDep,
    status: str | None = None,
    order_id: Annotated[str | None, Query(max_length=48)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    conditions = []
    if status:
        conditions.append(Refund.status == status)
    if order_id:
        conditions.append(Refund.order_id == order_id)
    total = int(
        ctx.session.scalar(sa.select(sa.func.count()).select_from(Refund).where(*conditions)) or 0
    )
    rows = list(
        ctx.session.scalars(
            sa.select(Refund)
            .where(*conditions)
            .order_by(Refund.created_at.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    )
    return {
        "items": [_refund_payload(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.post(
    "/refunds",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("refund.request"))],
)
def request_refund(
    payload: RefundRequestInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """登记退款申请（状态 ``REQUESTED``）。

    申请阶段不占用可退额度；额度在 ``approve`` 后的执行步骤里原子预留，
    因此两个客服同时申请不会造成超额退款，真正出款前仍需另一名员工批准。
    """
    summary = svc.order_paid_summary(ctx.session, order_id=payload.order_id)
    allocations = [
        item
        for item in summary.get("allocations", [])
        if item.get("classification") == "APPLIED" and int(item.get("refundable_cents", 0)) > 0
    ]
    if not allocations:
        raise AppError("REFUND_LIMIT_EXCEEDED", "该订单没有可退的成功收款记录。")

    remaining_total = sum(int(item.get("refundable_cents", 0)) for item in allocations)
    if payload.amount_cents > remaining_total:
        raise AppError(
            "REFUND_LIMIT_EXCEEDED",
            f"可退金额不足：当前可退 {remaining_total} 分。",
        )

    # 多次付款时按付款原单拆分退款子项，各子项分别查证（08 文档）。
    items: list[dict[str, Any]] = []
    leftover = payload.amount_cents
    for allocation in allocations:
        if leftover <= 0:
            break
        take = min(leftover, int(allocation.get("refundable_cents", 0)))
        if take <= 0:
            continue
        if payload.shipping_cents and leftover == payload.amount_cents:
            items.append({"kind": "shipping", "amount_cents": min(payload.shipping_cents, take)})
            take -= min(payload.shipping_cents, take)
        if take > 0:
            items.append({"kind": "goods", "amount_cents": take})
        leftover -= min(leftover, int(allocation.get("refundable_cents", 0)))

    total_items = sum(int(item["amount_cents"]) for item in items)
    if total_items != payload.amount_cents:
        raise AppError(
            "REFUND_LIMIT_EXCEEDED",
            "退款分摊与金额不一致，请核对各行剩余实付分摊。",
        )

    primary = allocations[0]
    refund = svc.create_refund_request(
        ctx.session,
        payment_id=str(primary["payment_id"]),
        allocation_id=str(primary["id"]),
        amount_cents=payload.amount_cents,
        items=items,
        reason=payload.reason,
        actor_id=principal.id,
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="refund.requested",
        resource_type="refund",
        resource_id=refund.id,
        after={"order_id": payload.order_id, "amount_cents": payload.amount_cents},
        reason=payload.reason,
    )
    return _refund_payload(refund)


@admin_router.post(
    "/refunds/{refund_id}/approve",
    response_model=dict,
    dependencies=[Depends(require_permission("refund.approve"))],
)
def approve_refund(
    refund_id: str,
    payload: RefundDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """批准并执行退款：按原渠道退回，结果为 PROCESSING/UNKNOWN 时继续查单。"""
    refund = repo.get_refund(ctx.session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    svc.approve_refund(
        ctx.session, refund_id=refund_id, actor_id=principal.id or "", reason=payload.reason
    )
    outcome = svc.request_refund(
        ctx.session,
        refund_id=refund_id,
        actor_id=principal.id or "",
        idempotency_key=f"refund-{refund_id}",
    )
    refreshed = repo.get_refund(ctx.session, refund_id)
    assert refreshed is not None
    payload_out = _refund_payload(refreshed)
    payload_out["outcome"] = outcome.status
    return payload_out


@admin_router.post(
    "/refunds/{refund_id}/reject",
    response_model=dict,
    dependencies=[Depends(require_permission("refund.approve"))],
)
def reject_refund(
    refund_id: str,
    payload: RefundDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """驳回退款申请。必须说明理由。"""
    updated = svc.reject_refund(
        ctx.session, refund_id=refund_id, actor_id=principal.id or "", reason=payload.reason
    )
    return _refund_payload(updated)


@admin_router.post(
    "/refunds/{refund_id}/query",
    response_model=dict,
    dependencies=[Depends(require_permission("refund.request"))],
)
def query_refund(
    refund_id: str,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """查证退款渠道结果。``UNKNOWN`` 继续查单，不能直接说钱已退。"""
    refund = repo.get_refund(ctx.session, refund_id)
    if refund is None:
        raise AppError("RESOURCE_NOT_FOUND")
    outcome = svc.request_refund(
        ctx.session,
        refund_id=refund_id,
        actor_id=principal.id or "",
        idempotency_key=f"refund-{refund_id}",
    )
    refreshed = repo.get_refund(ctx.session, refund_id)
    assert refreshed is not None
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="refund.query",
        resource_type="refund",
        resource_id=refund_id,
        after={"status": refreshed.status, "outcome": outcome.status},
    )
    result = _refund_payload(refreshed)
    result["outcome"] = outcome.status
    result["notice"] = "PROCESSING/UNKNOWN 表示尚未确认到账，不能对外说钱已退。"
    return result


# ------------------------------------------------------------------ 对账
@admin_router.get(
    "/reconciliation-runs",
    response_model=dict,
    dependencies=[Depends(require_permission("finance.reconcile"))],
)
def list_reconciliation_runs(
    ctx: ContextDep,
    provider: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    conditions = []
    if provider:
        conditions.append(ReconciliationRun.provider == provider)
    total = int(
        ctx.session.scalar(
            sa.select(sa.func.count()).select_from(ReconciliationRun).where(*conditions)
        )
        or 0
    )
    rows = list(
        ctx.session.scalars(
            sa.select(ReconciliationRun)
            .where(*conditions)
            .order_by(ReconciliationRun.started_at.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    )
    return {
        "items": [_run_payload(ctx.session, row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "next_cursor": None,
    }


@admin_router.post(
    "/reconciliation-runs",
    response_model=dict,
    dependencies=[Depends(require_permission("finance.reconcile"))],
)
def run_reconciliation(
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    provider: Annotated[str, Query(min_length=2, max_length=20)] = "sandbox",
    bill_date: Annotated[str | None, Query(max_length=10)] = None,
) -> dict[str, Any]:
    """拉取日账单并对账。不同渠道账单可用时间不同，由 adapter 配置。"""
    resolved_bill_date = bill_date or bill_date_of(now_ms())
    return svc.run_reconciliation(
        ctx.session, provider=provider, bill_date=resolved_bill_date, actor_id=principal.require_staff()
    )


@admin_router.post(
    "/reconciliation-runs/{run_id}/attributions",
    response_model=dict,
    dependencies=[Depends(require_permission("finance.reconcile"))],
)
def attribute_difference(
    run_id: str,
    payload: DifferenceResolveInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    difference_id: Annotated[str, Query(max_length=48)] = "",
) -> dict[str, Any]:
    """差异归因：保留证据与人工理由，不批量标记一致。"""
    _ = run_id
    if not difference_id:
        raise AppError("VALIDATION_ERROR", "缺少差异编号。", fields={"difference_id": "必填"})
    row = svc.resolve_difference(
        ctx.session,
        difference_id=difference_id,
        resolution=payload.resolution,
        actor_id=principal.require_staff(),
        reason=payload.reason,
    )
    difference = repo.get_difference(ctx.session, str(row["difference_id"]))
    assert difference is not None
    return _difference_payload(difference)


@admin_router.post(
    "/reconciliation-runs/{run_id}/resolutions",
    response_model=dict,
    dependencies=[Depends(require_permission("finance.reconcile"))],
)
def resolve_difference_endpoint(
    run_id: str,
    payload: DifferenceResolveInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
    difference_id: Annotated[str, Query(max_length=48)] = "",
) -> dict[str, Any]:
    return attribute_difference(
        run_id, payload, ctx, _csrf, principal, difference_id=difference_id
    )


@admin_router.post(
    "/reconciliation-runs/export",
    response_model=dict,
    dependencies=[Depends(require_permission("export.data"))],
)
def export_reconciliation(
    payload: ReconciliationExportInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """导出对账摘要。需要独立导出权限并写审计；不含密钥与完整渠道报文。"""
    rows = list(
        ctx.session.scalars(
            sa.select(ReconciliationRun).where(
                ReconciliationRun.provider == payload.provider,
                ReconciliationRun.bill_date == payload.bill_date,
            )
        )
    )
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="reconciliation.export",
        resource_type="reconciliation_run",
        resource_id=payload.bill_date,
        after={"provider": payload.provider, "runs": len(rows)},
        reason=payload.reason,
    )
    return {
        "provider": payload.provider,
        "bill_date": payload.bill_date,
        "runs": [_run_payload(ctx.session, row) for row in rows],
        "notice": "导出仅含对账摘要与差异归因，不含密钥、证书与完整渠道报文。",
    }


# ------------------------------------------------------------------ 序列化
def _attempt_payload(session: Any, row: PaymentAttempt) -> dict[str, Any]:
    summary = svc.order_paid_summary(session, order_id=row.order_id)
    return {
        "id": row.id,
        "order_id": row.order_id,
        "installment_id": getattr(row, "installment_id", None),
        "provider": row.provider,
        "merchant_order_no": row.merchant_order_no,
        "expected_cents": int(row.expected_cents),
        "currency": row.currency,
        "status": row.status,
        "provider_transaction_id": row.provider_transaction_id,
        "expires_at": iso(getattr(row, "expires_at", None)),
        "created_at": iso(row.created_at),
        "verified_at": iso(getattr(row, "verified_at", None)),
        "order_paid_cents": int(summary.get("paid_cents", 0)),
        "order_refunded_cents": int(summary.get("refunded_applied_cents", 0)),
        "notice": "UNKNOWN/PENDING 必须查单确认，不能手工标记为已付。",
    }


def _refund_payload(row: Refund) -> dict[str, Any]:
    return {
        "id": row.id,
        "refund_no": row.refund_no,
        "order_id": row.order_id,
        "payment_id": row.payment_id,
        "amount_cents": int(row.amount_cents),
        "currency": row.currency,
        "status": row.status,
        "kind": getattr(row, "kind", "normal"),
        "reason": row.reason,
        "requested_by": row.requested_by,
        "approved_by": getattr(row, "approved_by", None),
        "provider_refund_id": row.provider_refund_id,
        "created_at": iso(row.created_at),
        "updated_at": iso(getattr(row, "updated_at", None)),
        "notice": "PROCESSING/UNKNOWN 表示尚未确认到账，不能对外说钱已退。",
    }


def _run_payload(session: Any, row: ReconciliationRun) -> dict[str, Any]:
    differences = repo.list_differences(session, run_id=row.id)
    return {
        "id": row.id,
        "provider": row.provider,
        "bill_date": row.bill_date,
        "status": row.status,
        "revision": getattr(row, "revision", None),
        "created_at": iso(row.started_at),
        "finished_at": iso(row.finished_at),
        "difference_count": len(differences),
        "open_difference_count": len([item for item in differences if item.status == "OPEN"]),
        "differences": [_difference_payload(item) for item in differences],
    }


def _difference_payload(row: ReconciliationDifference) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "provider": row.provider,
        "bill_date": row.bill_date,
        "difference_type": row.difference_type,
        "merchant_order_no": row.merchant_order_no,
        "transaction_id": row.transaction_id,
        "refund_no": row.refund_no,
        "channel_amount_cents": row.channel_amount_cents,
        "local_amount_cents": row.local_amount_cents,
        "fee_cents": row.fee_cents,
        "status": row.status,
        "resolution": row.resolution,
        "resolution_reason": row.resolution_reason,
        "created_at": iso(row.created_at),
    }


__all__ = ["admin_router"]
