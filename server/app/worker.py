"""任务 worker：消费 outbox、投递通知、积分入账、关闭到期订单与对账。

约束（04/14 文档）：任务 worker 独立服务；多个 worker 意外启动时以数据库
claim 条件保证一条任务只有一个有效租约，消费者仍必须幂等；失败有指数退避、
最大次数与死信。
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from app.platform.config import Settings, get_settings
from app.platform.db import affected_rows, get_session_factory, integrity_report
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms
from app.platform.reliability import ConsumedEvent, OutboxEvent

logger = logging.getLogger("starium.worker")

_stop = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _stop
    logger.info("收到信号 %s，准备停止 worker", signum)
    _stop = True


@dataclass
class WorkerRun:
    claimed: int = 0
    done: int = 0
    failed: int = 0
    dead: int = 0
    skipped: int = 0


def claim_events(session: Any, *, worker_id: str, limit: int, settings: Settings) -> list[OutboxEvent]:
    """原子领取到期事件。条件更新保证一条任务只有一个有效租约。"""
    moment = now_ms()
    lease_until = moment + settings.outbox_lease_seconds * 1000
    candidates = list(
        session.scalars(
            sa.select(OutboxEvent)
            .where(
                OutboxEvent.status.in_(("PENDING", "CLAIMED")),
                OutboxEvent.available_at <= moment,
                sa.or_(OutboxEvent.claimed_until.is_(None), OutboxEvent.claimed_until < moment),
            )
            .order_by(OutboxEvent.available_at)
            .limit(limit)
        )
    )
    claimed: list[OutboxEvent] = []
    for event in candidates:
        result = session.execute(
            sa.update(OutboxEvent)
            .where(
                OutboxEvent.id == event.id,
                sa.or_(OutboxEvent.claimed_until.is_(None), OutboxEvent.claimed_until < moment),
            )
            .values(status="CLAIMED", claimed_until=lease_until, claimed_by=worker_id)
        )
        if affected_rows(result):
            claimed.append(event)
    session.flush()
    return claimed


def _already_consumed(session: Any, *, consumer: str, event_id: str) -> bool:
    return (
        session.get(ConsumedEvent, {"consumer_name": consumer, "event_id": event_id}) is not None
    )


def _mark_consumed(session: Any, *, consumer: str, event_id: str) -> None:
    if not _already_consumed(session, consumer=consumer, event_id=event_id):
        session.add(ConsumedEvent(consumer_name=consumer, event_id=event_id))


def handle_event(session: Any, event: OutboxEvent) -> str:
    """分发事件。返回处理结果描述；未知事件只记录最低必要元数据。

    消费用「消费者名 + event_id」去重（与业务效果同事务），实现至少一次投递、
    业务效果一次；重复投递返回 ``duplicate``，不重复发积分或重复扣库存。
    """
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        logger.warning("未知事件类型 %s（aggregate=%s）", event.event_type, event.aggregate_id)
        return "unknown-event"

    consumer = f"worker:{event.event_type}"
    if _already_consumed(session, consumer=consumer, event_id=event.event_id):
        return "duplicate"
    result = handler(session, event)
    _mark_consumed(session, consumer=consumer, event_id=event.event_id)
    return result


def _dispatch_notification(session: Any, event: OutboxEvent) -> str:
    """投递待发送的通知。失败不回滚已成功的支付。"""
    from app.modules.notifications import service as notification_service

    rows = notification_service.due_deliveries(session, limit=20)
    for row in rows:
        notification_service.send_delivery(session, row)
    return f"delivered:{len(rows)}"


def _prebuild_content(session: Any, event: OutboxEvent) -> str:
    """实际生成公开内容产物，失败由 outbox 重试。"""
    from app.ops.prebuild import run_prebuild

    result = run_prebuild(session, job_id=(event.payload or {}).get("job_id"), trigger="content-event")
    if result["status"] != "DONE":
        raise RuntimeError("PREBUILD_FAILED")
    return "prebuild-done"


def _close_due_orders(session: Any, event: OutboxEvent) -> str:
    """关闭到期的未付款订单：先查渠道，确认无在途付款后才释放预留。

    通过商务模块端口调用（04 文档：跨模块只用公开端口）。
    """
    from app.modules.commerce import ports as commerce_ports

    closed = commerce_ports.expire_due_orders(session, limit=20)
    return f"closed:{closed}"


def _post_due_points(session: Any, event: OutboxEvent) -> str:
    """把到期且无未决售后的积分奖励入账。"""
    from app.modules.loyalty import service as loyalty_service

    posted = loyalty_service.post_due_awards(session, limit=50)
    return f"points_posted:{posted}"


def _remind_stage(session: Any, event: OutboxEvent) -> str:
    """制作阶段待确认提醒：登记通知，等待下次投递。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.service import enqueue_notification

    payload = event.payload or {}
    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"stage-{event.aggregate_id}-{payload.get('revision', 1)}",
            event_type="production.stage_ready",
            user_id=payload.get("user_id"),
            title="制作阶段待确认",
            body="",
            link_path=f"/account/commissions/{payload.get('project_id', '')}",
            category="production",
            channels=("email",),
            email_target=payload.get("email"),
            payload=payload,
        ),
    )
    return "stage-notified"


def _cleanup(session: Any, event: OutboxEvent) -> str:
    """定期清理：过期验证码、无引用附件、过期限流窗口、失败投递重试、到期注销清理。"""
    from app.modules.identity import repository as identity_repo
    from app.modules.identity import service as identity_service
    from app.modules.media import service as media_service
    from app.modules.notifications import service as notification_service
    from app.platform import rate_limit

    purged = identity_repo.purge_expired_challenges(session)
    orphans = media_service.cleanup_orphans(session, limit=50)
    rate_limit.cleanup(session, limit=200)
    retried = notification_service.retry_failed(session, limit=20)
    # 未提交草稿的保留窗口（10 文档「草稿 30 天」草案值，C22 定稿后调整配置即可）。
    from app.modules.commissions import ports as commission_ports
    from app.platform.config import get_settings

    retention_days = max(1, int(getattr(get_settings(), "retention_draft_days", 30)))
    stale_drafts = commission_ports.purge_stale_drafts(
        session, before_ms=now_ms() - retention_days * 24 * 3600 * 1000, limit=50
    )
    # 注销到期：执行可删除资料清理（C22）；幂等，重复运行不会重复删除或重复计数。
    closures = identity_service.process_due_closures(session, limit=20)
    return (
        f"cleanup:otp={purged},media={orphans},deliveries={retried},"
        f"drafts={stale_drafts},closures={closures['processed']}"
    )


def _support_sla(session: Any, event: OutboxEvent) -> str:
    """C30 首响时效巡检：对逾期未响应的询价与反馈各登记一次提醒（同一条只提醒一次）。"""
    from app.modules.commissions import ports as commission_ports
    from app.modules.feedback import service as feedback_service

    inquiries = commission_ports.remind_overdue_inquiries(session, limit=50)
    feedback = feedback_service.remind_overdue_feedback(session, limit=50)
    return (
        f"sla:inquiries_overdue={inquiries['overdue']},inquiries_reminded={inquiries['reminded']},"
        f"feedback_overdue={feedback['overdue']},feedback_reminded={feedback['reminded']}"
    )


def _reconcile(session: Any, event: OutboxEvent) -> str:
    from app.adapters.payments.base import bill_date_of
    from app.modules.payments import service as payment_service

    payload = event.payload or {}
    result = payment_service.run_reconciliation(
        session,
        provider=str(payload.get("provider") or "sandbox"),
        bill_date=str(payload.get("bill_date") or bill_date_of(now_ms())),
        # 周期对账没有登录员工；操作人以固定系统标识留痕，不伪造员工 ID。
        actor_id="system:reconcile",
    )
    return f"reconciliation:{result.get('status', 'unknown')}"


_HANDLERS = {
    "notification.dispatch": _dispatch_notification,
    "content.prebuild_requested": _prebuild_content,
    "order.closing_due": _close_due_orders,
    "order.created": _dispatch_notification,
    "payment.succeeded": _dispatch_notification,
    "payment.verified": _dispatch_notification,
    "points.award_due": _post_due_points,
    "production.stage_ready": _remind_stage,
    "schedule.slot_expiring": _close_due_orders,
    "refund.succeeded": _dispatch_notification,
    "quote.published": _dispatch_notification,
    "quote.accepted": _dispatch_notification,
    "feedback.reviewed": _dispatch_notification,
    "settings.feature_flags_changed": lambda session, event: "acknowledged",
    "ops.cleanup": _cleanup,
    "support.sla_check": _support_sla,
    "finance.reconcile": _reconcile,
}


def run_once(*, worker_id: str | None = None, limit: int = 20) -> WorkerRun:
    """运行一轮。返回本轮统计，便于测试与运维观察。"""
    settings = get_settings()
    identity = worker_id or f"worker-{new_id('job')[:12]}"
    run = WorkerRun()
    session = get_session_factory()()
    try:
        events = claim_events(session, worker_id=identity, limit=limit, settings=settings)
        run.claimed = len(events)
        for event in events:
            try:
                handle_event(session, event)
                event.status = "DONE"
                event.processed_at = now_ms()
                event.claimed_until = None
                session.commit()
                run.done += 1
            except AppError as exc:
                session.rollback()
                _fail_event(session, event.id, str(exc), settings)
                run.failed += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其他任务
                session.rollback()
                logger.exception("处理事件失败：%s", event.event_type)
                _fail_event(session, event.id, f"{type(exc).__name__}: {exc}", settings)
                run.failed += 1
        if not events:
            run.skipped = 1
    finally:
        session.close()
    return run


def _fail_event(session: Any, event_id: str, message: str, settings: Settings) -> None:
    """指数退避重试；超过最大次数进入死信。"""
    session.rollback()
    event = session.get(OutboxEvent, event_id)
    if event is None:
        return
    event.attempts += 1
    event.last_error = message[:400]
    event.claimed_until = None
    if event.attempts >= settings.outbox_max_attempts:
        event.status = "DEAD"
        logger.error("事件 %s 进入死信：%s", event.event_type, message)
    else:
        event.status = "PENDING"
        backoff_seconds = min(300, 2 ** min(event.attempts, 8))
        event.available_at = now_ms() + backoff_seconds * 1000
    session.commit()


def run_worker(*, once: bool = False, max_iterations: int = 0) -> None:
    """worker 主循环。设置信号处理后优雅停止。"""
    settings = get_settings()
    worker_id = f"worker-{new_id('job')[:12]}"
    logger.info("worker 启动：%s（env=%s）", worker_id, settings.app_env)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    iterations = 0
    # 定期任务：清理、对账与首响时效巡检由 worker 自己排期，避免依赖外部 cron。
    schedule = {
        "ops.cleanup": 3600,
        "support.sla_check": 3600,
        "finance.reconcile": 86_400,
        "notification.dispatch": 60,
    }
    last_run = {key: 0 for key in schedule}

    while not _stop:
        run = run_once(worker_id=worker_id)
        iterations += 1
        if run.claimed:
            logger.info(
                "本轮：领取 %s，完成 %s，失败 %s", run.claimed, run.done, run.failed
            )
        _enqueue_scheduled(schedule, last_run)
        if once or (max_iterations and iterations >= max_iterations):
            break
        time.sleep(settings.outbox_poll_seconds)

    logger.info("worker 停止（处理 %s 轮）", iterations)


def _enqueue_scheduled(schedule: dict[str, int], last_run: dict[str, int]) -> None:
    """把到期的周期任务写入 outbox。"""
    moment = now_ms()
    session = get_session_factory()()
    try:
        for event_type, interval in schedule.items():
            previous = last_run.get(event_type, 0)
            if moment - previous < interval * 1000:
                continue
            session.add(
                OutboxEvent(
                    id=new_id("event"),
                    event_id=new_id("event"),
                    event_type=event_type,
                    aggregate_id="scheduler",
                    aggregate_version=1,
                    payload={"scheduled_at": iso(moment)},
                    available_at=moment,
                )
            )
            last_run[event_type] = moment
        session.commit()
    finally:
        session.close()


def worker_health() -> dict[str, Any]:
    """worker 健康与其他运维数据，用于 ``/admin/jobs`` 与告警。"""
    session = get_session_factory()()
    try:
        rows = session.execute(
            sa.select(OutboxEvent.status, sa.func.count()).group_by(OutboxEvent.status)
        ).all()
        stats = {str(status): int(count) for status, count in rows}
        oldest = session.scalar(
            sa.select(sa.func.min(OutboxEvent.available_at)).where(OutboxEvent.status == "PENDING")
        )
        dead = list(
            session.scalars(
                sa.select(OutboxEvent).where(OutboxEvent.status == "DEAD").limit(20)
            )
        )
        return {
            "outbox": stats,
            "oldest_pending_at": iso(oldest),
            "dead_letters": [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "aggregate_id": event.aggregate_id,
                    "attempts": event.attempts,
                    "last_error": event.last_error,
                    "created_at": iso(event.created_at),
                }
                for event in dead
            ],
            "database": integrity_report(),
        }
    finally:
        session.close()


def retry_dead_letter(event_id: str) -> bool:
    """人工重试死信。必须幂等（消费者去重保证业务效果一次）。"""
    session = get_session_factory()()
    try:
        event = session.get(OutboxEvent, event_id)
        if event is None or event.status != "DEAD":
            return False
        event.status = "PENDING"
        event.attempts = 0
        event.available_at = now_ms()
        event.claimed_until = None
        session.commit()
        return True
    finally:
        session.close()
