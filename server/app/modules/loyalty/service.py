"""积分模块用例：规则版本、奖励重算、到点入账、调整、申诉与流水查询。

不变量（05 文档“事务不变量”第 5 条）：去重记录 + ledger + account 缓存 +
``award.posted_points`` 在同一事务提交；退款与发奖锁定同一来源并**重算**目标，
不信任旧事件携带的点数。

规则（09 文档，唯一规则源是 ``app.modules.content.service.loyalty_policy``）：

- 每 ``denominator_yuan`` 元商品实际现金分摊 = 1 积分（配置默认每 5 元 1 分），运费不计奖；
  单笔不设上限——09 文档里的 2000 分上限是已被 C13 否决的旧推荐值；
- ``eligible_net_cents = max(0, 商品实际现金分摊 - 商品已成功退款分摊)``，
  ``target = floor(eligible_net_cents / (denominator_yuan * 100))``；
- 用**减法重算**而不是逐次累加：多次零碎退款不会因每次向下取整而漏扣；
- 预付款、尾款、加项的支付不分别触发完整奖励；订单付清、完成交付后
  ``release_delay_days`` 天且无未决售后，才把目标积分入账；
- 退款到达时始终重算：未入账改目标，已入账追加差额流水；取消与全额退款目标为 0；
- 规则版本在下单时保存（``award.rule_version``），规则调整不回溯重算老订单，补发走调整单；
- 反馈奖励按 C14 分级给分（minor/normal/major），不按金额折算。

不得实现充值、提现、转账、投资收益，也不得用积分替代现金退款（09 文档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.content.ports import loyalty_policy
from app.modules.contracts import AwardRequest
from app.modules.loyalty import repository as repo
from app.modules.loyalty.models import (
    ADJUSTMENT_STATUSES,
    APPEAL_STATUSES,
    LEDGER_ENTRY_TYPES,
    RULE_KINDS,
    PointAdjustment,
    PointAppeal,
    PointAward,
    PointLedger,
    PointRule,
)
from app.platform import audit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import add_minutes_ms, iso, new_id, now_ms, to_ms
from app.platform.reliability import digest, enqueue

# C14 反馈奖励等级（分值与 09 文档旧草案的 10/30/100 不同：以 C14 定稿与配置为准）。
FEEDBACK_REWARD_LEVELS = ("minor", "normal", "major")

# 反馈等级的中文判据说明，用于流水描述与通知（不含用户反馈正文）。
FEEDBACK_LEVEL_LABELS = {
    "minor": "内容纠错/具体可用建议",
    "normal": "有效可复现功能缺陷",
    "major": "经确认严重缺陷/高价值改进",
}

MAX_ADJUSTMENT_POINTS = 100_000
DAY_MS = 86_400_000

# worker 的 ``points.award_due`` 事件：积分模块自己的消费者名（与 worker 的消费去重分开）。
AWARD_DUE_EVENT = "points.award_due"
LOYALTY_CONSUMER = "loyalty.award_due"


# ------------------------------------------------------------------ 规则
def _policy(session: Session) -> dict[str, Any]:
    return loyalty_policy(session)


def _baseline_parameters(session: Session, *, kind: str) -> dict[str, Any]:
    policy = _policy(session)
    if kind == "feedback":
        return {
            "feedback_rewards": policy["feedback_rewards"],
            "feedback_daily_reward_limit": policy["feedback_daily_reward_limit"],
        }
    return {
        "denominator_yuan": policy["denominator_yuan"],
        "release_delay_days": policy["release_delay_days"],
        "shipping_excluded": policy["shipping_excluded"],
    }


def current_rule_version(session: Session, *, kind: str = "purchase") -> str:
    """当前生效规则版本。

    优先取已发布的 ``point_rules`` 版本；没有发布记录时用参数摘要作为基线版本号，
    这样后台一改规则参数，新订单就会记到新的版本上，而老订单仍保留原版本。
    """
    if kind not in RULE_KINDS:
        raise AppError("VALIDATION_ERROR", "规则类型不支持。", fields={"kind": "非法类型"})
    row = repo.latest_published_rule(session, kind=kind)
    if row is not None:
        return row.rule_version
    return f"points-{digest(_baseline_parameters(session, kind=kind))[:8]}"


def _rule_parameters(session: Session, *, kind: str, rule_version: str) -> dict[str, Any]:
    if not rule_version:
        return {}
    row = repo.get_rule(session, rule_version=rule_version)
    if row is None or row.kind != kind or row.status != "PUBLISHED":
        # 目录里没有该版本（例如基线摘要版本）：回落当前策略。
        # 需要严格“不回溯”时，运营必须先发布 point_rules 版本再改参数（09 文档）。
        return {}
    return dict(row.parameters or {})


def purchase_policy(session: Session, *, rule_version: str = "") -> dict[str, Any]:
    """购买奖励参数（按奖励来源保存的规则版本读取）。"""
    policy = _policy(session)
    params = _rule_parameters(session, kind="purchase", rule_version=rule_version)
    return {
        "points_name": str(policy["points_name"]),
        "denominator_yuan": int(params.get("denominator_yuan", policy["denominator_yuan"])),
        "release_delay_days": int(params.get("release_delay_days", policy["release_delay_days"])),
        "shipping_excluded": bool(params.get("shipping_excluded", policy["shipping_excluded"])),
    }


def feedback_policy(session: Session, *, rule_version: str = "") -> dict[str, Any]:
    """反馈奖励参数（C14 分级与每日奖励条数上限）。"""
    policy = _policy(session)
    params = _rule_parameters(session, kind="feedback", rule_version=rule_version)
    raw = params.get("feedback_rewards", policy["feedback_rewards"]) or {}
    rewards = {level: int(raw.get(level, 0)) for level in FEEDBACK_REWARD_LEVELS}
    return {
        "points_name": str(policy["points_name"]),
        "rewards": rewards,
        "daily_limit": int(params.get("feedback_daily_reward_limit", policy["feedback_daily_reward_limit"])),
        "redemption_enabled": bool(policy["redemption_enabled"]),
    }


def target_points_for_order(session: Session, *, eligible_net_cents: int, rule_version: str = "") -> int:
    """``target = floor(可奖励净额 / (denominator_yuan * 100))``，单笔无上限。"""
    denominator_yuan = int(purchase_policy(session, rule_version=rule_version)["denominator_yuan"])
    denominator_cents = denominator_yuan * 100
    if denominator_cents <= 0:
        raise AppError("INTERNAL_ERROR", "积分规则参数不合法，请联系客服。")
    return max(0, int(eligible_net_cents)) // denominator_cents


def target_points_for_feedback(session: Session, *, level: str, rule_version: str = "") -> int:
    """反馈奖励按 C14 等级给分：minor=5 / normal=20 / major=50（可通过规则版本调整）。"""
    if level not in FEEDBACK_REWARD_LEVELS:
        raise AppError("VALIDATION_ERROR", "反馈奖励等级无效。", fields={"reward_level": "非法等级"})
    return max(0, int(feedback_policy(session, rule_version=rule_version)["rewards"][level]))


def publish_rule(
    session: Session,
    *,
    kind: str,
    parameters: dict[str, Any],
    published_by: str | None = None,
    note: str | None = None,
    effective_at: int | None = None,
) -> PointRule:
    """发布新规则版本（权限码 ``settings.manage``，路由在 admin_routes.py）。

    已发布版本不可修改：同参数重复发布会返回既有版本，不同参数只能生成新版本。
    """
    if kind not in RULE_KINDS:
        raise AppError("VALIDATION_ERROR", "规则类型不支持。", fields={"kind": "非法类型"})
    latest = repo.latest_published_rule(session, kind=kind)
    if latest is not None and dict(latest.parameters or {}) == dict(parameters):
        return latest
    sequence = repo.count_rules(session, kind=kind) + 1
    rule_version = f"{kind}-v{sequence}"
    while repo.get_rule(session, rule_version=rule_version) is not None:
        sequence += 1
        rule_version = f"{kind}-v{sequence}"
    row = PointRule(
        id=new_id("rule"),
        rule_version=rule_version,
        kind=kind,
        parameters=dict(parameters),
        effective_at=effective_at if effective_at is not None else now_ms(),
        status="PUBLISHED",
        published_by=published_by,
        note=note,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="staff" if published_by else "system",
        actor_id=published_by,
        action="point_rule.publish",
        resource_type="point_rule",
        resource_id=row.rule_version,
        after={"kind": kind, "parameters": row.parameters},
        reason=note,
    )
    return row


# ------------------------------------------------------------------ 账户
def account_view(session: Session, *, user_id: str) -> dict[str, Any]:
    """账户视图（06 文档形状）。账面可为负；可用不为负；待入账来自未入账奖励。"""
    account = repo.get_account(session, user_id=user_id)
    posted = int(account.posted_balance) if account is not None else 0
    reserved = int(account.reserved_points) if account is not None else 0
    return {
        "posted_balance": posted,
        "reserved_points": reserved,
        "available_points": max(0, posted - reserved),
        "pending_points": repo.pending_points(session, user_id=user_id),
        "rules_version": current_rule_version(session),
    }


def verify_account_consistency(session: Session, *, user_id: str) -> dict[str, Any]:
    """对账辅助：账户缓存必须等于账本合计（``posted_balance = Σ delta``）。"""
    account = repo.get_account(session, user_id=user_id)
    cached = int(account.posted_balance) if account is not None else 0
    total = repo.ledger_total(session, user_id=user_id)
    return {
        "user_id": user_id,
        "posted_balance": cached,
        "ledger_total": total,
        "consistent": cached == total,
        "difference": cached - total,
    }


def ledger_view(
    session: Session, *, user_id: str, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """本人流水与总数。只按 ``user_id`` 过滤，绝不泄露他人反馈内容。"""
    rows = repo.list_ledger(session, user_id=user_id, limit=limit, offset=offset)
    states = repo.appeal_states(session, user_id=user_id, ledger_ids=[row.id for row in rows])
    items = [serialize_ledger_entry(row, appeal_status=states.get(row.id)) for row in rows]
    return items, repo.count_ledger(session, user_id=user_id)


def serialize_ledger_entry(row: PointLedger, *, appeal_status: str | None = None) -> dict[str, Any]:
    """流水明细：delta、中文描述、来源摘要、发生时间、调整关联与申诉入口。"""
    return {
        "id": row.id,
        "delta": int(row.delta),
        "entry_type": row.entry_type,
        "description": row.description,
        "source": {
            "type": row.source_type,
            "id": row.source_id,
            "summary": row.label or row.source_id,
        },
        "source_revision": int(row.source_revision),
        "rule_version": row.rule_version,
        "reversal_of": row.reversal_of,
        "adjustment_id": row.source_id if row.source_type == "adjustment" else None,
        "occurred_at": iso(row.created_at),
        "appeal": {
            "available": True,
            "endpoint": f"{get_settings().api_prefix}/points/appeals",
            "status": appeal_status,
        },
    }


def create_appeal(
    session: Session, *, user_id: str, ledger_id: str, reason: str, client_ip: str | None = None
) -> PointAppeal:
    """对本人某条流水提出申诉。同一流水的未结申诉只保留一条。"""
    entry = repo.get_ledger(session, ledger_id=ledger_id, user_id=user_id)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 10:
        raise AppError("VALIDATION_ERROR", "请填写至少 10 个字的申诉理由。", fields={"reason": "过短"})
    existing = repo.open_appeal(session, user_id=user_id, ledger_id=entry.id)
    if existing is not None:
        return existing
    row = PointAppeal(
        id=new_id("appeal"),
        user_id=user_id,
        ledger_id=entry.id,
        reason=clean_reason[:1000],
        status="SUBMITTED",
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="points.appeal_create",
        resource_type="point_appeal",
        resource_id=row.id,
        after={"ledger_id": entry.id, "delta": entry.delta, "entry_type": entry.entry_type},
        reason=clean_reason[:200],
        ip=client_ip,
    )
    _notify_staff(
        session,
        event_id=f"points-appeal-{row.id}",
        title="收到星绒币流水申诉",
        body=f"用户对流水 {entry.id}（{entry.delta} 分）提出申诉，请复核。",
        payload={"appeal_id": row.id, "ledger_id": entry.id},
    )
    return row


def serialize_appeal(row: PointAppeal) -> dict[str, Any]:
    return {
        "id": row.id,
        "ledger_id": row.ledger_id,
        "status": row.status,
        "reason": row.reason,
        "resolution": row.resolution,
        "created_at": iso(row.created_at),
    }


def appeals_view(
    session: Session, *, user_id: str, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """本人申诉列表与总数。只按 ``user_id`` 过滤，不返回他人申诉。"""
    rows = repo.list_appeals(session, user_id=user_id, limit=limit, offset=offset)
    return [serialize_appeal(row) for row in rows], repo.count_appeals(session, user_id=user_id)


def list_appeals(
    session: Session, *, user_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[PointAppeal]:
    return repo.list_appeals(session, user_id=user_id, status=status, limit=limit)


def resolve_appeal(
    session: Session,
    *,
    appeal_id: str,
    status: str,
    resolution: str,
    actor_id: str | None,
) -> PointAppeal:
    """后台复核申诉（权限码 ``points.approve``，路由在 admin_routes.py）。

    结论只记录与通知；积分纠错一律另开调整单，不能借申诉直接改余额。
    """
    if status not in APPEAL_STATUSES or status == "SUBMITTED":
        raise AppError("VALIDATION_ERROR", "申诉结论无效。", fields={"status": "非法结论"})
    row = session.get(PointAppeal, appeal_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status in ("ACCEPTED", "REJECTED"):
        raise AppError("STATE_CONFLICT", "该申诉已有结论。")
    row.status = status
    row.resolution = resolution.strip()[:300]
    row.handled_by = actor_id
    row.handled_at = now_ms()
    session.flush()
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="points.appeal_resolve",
        resource_type="point_appeal",
        resource_id=row.id,
        after={"status": row.status, "resolution": row.resolution},
        reason=row.resolution,
    )
    return row


# ------------------------------------------------------------------ 奖励重算
@dataclass(frozen=True)
class SourceTarget:
    """来源的当前权威目标（金额、退款、可入账时间与阻塞原因）。"""

    user_id: str
    target_points: int
    available_at: int
    blocked_reason: str | None
    label: str
    description: str
    detail: dict[str, Any] = field(default_factory=dict)
    # 交付时间明确时为 True：只有这时才排期延迟入账事件，避免未交付订单形成事件链。
    release_scheduled: bool = False


@dataclass(frozen=True)
class AwardPlan:
    revision: int
    user_id: str
    target_points: int
    posted_points: int
    status: str
    available_at: int
    label: str
    detail: dict[str, Any]
    blocked_reason: str | None
    delta: int
    entry_type: str | None
    changed: bool
    posted_now: bool
    schedule_release: bool

    def values(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "user_id": self.user_id,
            "target_points": self.target_points,
            "posted_points": self.posted_points,
            "status": self.status,
            "available_at": self.available_at,
            "label": self.label,
            "detail": self.detail,
            "blocked_reason": self.blocked_reason,
            "updated_at": now_ms(),
        }


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _completed_at_ms(snapshot: dict[str, Any]) -> int | None:
    """商务快照的 ``completed_at`` 是 ISO 字符串；解析失败或缺失视为未完成交付。"""
    raw = snapshot.get("completed_at")
    if not raw:
        return None
    if isinstance(raw, int):
        return raw
    try:
        return to_ms(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def eligible_net_cents(
    snapshot: dict[str, Any], *, fallback_cents: int = 0, shipping_excluded: bool = True
) -> int:
    """可奖励净额 = max(0, 商品实际现金分摊 - 商品已成功退款分摊)。

    优先使用商务端口给出的 ``eligible_net_cents``（权威、已扣成功退款、不含运费）；
    缺失时按 ``paid_goods_cents - refunded_goods_cents`` 或「实付 - 运费 - 已退款」兜底，
    仍然坚持 **运费不计奖**。
    """
    explicit = _int_or_none(snapshot.get("eligible_net_cents"))
    if explicit is not None:
        return max(0, explicit)
    goods = _int_or_none(snapshot.get("paid_goods_cents"))
    refunded_goods = _int_or_none(snapshot.get("refunded_goods_cents")) or 0
    if goods is None:
        paid = _int_or_none(snapshot.get("paid_cents")) or int(fallback_cents)
        shipping = _int_or_none(snapshot.get("paid_shipping_cents"))
        if shipping is None:
            shipping = _int_or_none(snapshot.get("shipping_cents")) or 0
        goods = paid - shipping if shipping_excluded else paid
        refunded_goods = _int_or_none(snapshot.get("refunded_cents")) or refunded_goods
    return max(0, goods - refunded_goods)


def _has_open_after_sales(snapshot: dict[str, Any]) -> bool:
    for key in ("has_open_after_sales", "has_open_after_sale", "open_after_sale"):
        if bool(snapshot.get(key)):
            return True
    return False


def _order_target(
    session: Session, request: AwardRequest, *, rule_version: str, moment: int, fallback_user_id: str
) -> SourceTarget:
    """按**当前**订单快照重算目标（不信任 outbox 里携带的金额或点数）。"""
    from app.modules.loyalty import ports as loyalty_ports

    snapshot = loyalty_ports.order_snapshot(session, request.source_id)
    params = purchase_policy(session, rule_version=rule_version)
    eligible = eligible_net_cents(
        snapshot,
        fallback_cents=int(request.eligible_amount_cents or 0),
        shipping_excluded=params["shipping_excluded"],
    )
    target = target_points_for_order(session, eligible_net_cents=eligible, rule_version=rule_version)
    order_status = str(snapshot.get("status") or "")
    if order_status == "CANCELLED":
        target = 0  # 取消订单目标为 0
    completed_at = _completed_at_ms(snapshot)
    release_scheduled = completed_at is not None
    if completed_at is not None:
        available_at = completed_at + params["release_delay_days"] * DAY_MS
    else:
        # 尚未完成交付：不排期（等交付事件的重新重算），占位时间保证不会提前发放。
        available_at = moment + params["release_delay_days"] * DAY_MS
    blocked_reason = "存在未决售后，暂缓发放。" if _has_open_after_sales(snapshot) else None
    user_id = str(snapshot.get("user_id") or fallback_user_id or request.user_id)
    points_name = params["points_name"]
    return SourceTarget(
        user_id=user_id,
        target_points=target,
        available_at=available_at,
        blocked_reason=blocked_reason,
        label=f"订单 {request.source_id}",
        description=f"购买奖励：订单 {request.source_id} 商品实付折算 {target} {points_name}",
        detail={
            "order_id": request.source_id,
            "order_status": order_status,
            "eligible_net_cents": eligible,
            "paid_goods_cents": _int_or_none(snapshot.get("paid_goods_cents")),
            "refunded_goods_cents": _int_or_none(snapshot.get("refunded_goods_cents")),
            "denominator_yuan": params["denominator_yuan"],
            "release_at": iso(available_at),
            "delivered": release_scheduled,
        },
        release_scheduled=release_scheduled,
    )


def _feedback_target(session: Session, request: AwardRequest, *, rule_version: str) -> SourceTarget:
    """反馈奖励按 C14 等级给分（不是按金额折算）。

    ``eligible_amount_cents`` 传 0：等级到分值的映射由积分模块持有，避免调用方
    用「分值 × 100」这类换算去凑金额（那会随 denominator 变化而算错）。
    """
    level = str((request.detail or {}).get("level") or "")
    params = feedback_policy(session, rule_version=rule_version)
    target = target_points_for_feedback(session, level=level, rule_version=rule_version)
    level_label = FEEDBACK_LEVEL_LABELS.get(level, level)
    available_at = int(request.available_at or now_ms())
    return SourceTarget(
        user_id=request.user_id,
        target_points=target,
        available_at=available_at,
        blocked_reason=None,
        label=f"反馈奖励（{request.source_id}）",
        description=f"有效反馈奖励：{level_label} {target} {params['points_name']}",
        detail={"feedback_id": request.source_id, "level": level, "review_revision": request.source_revision},
        release_scheduled=False,
    )


def _next_revision(award_revision: int, request_revision: int) -> int:
    """账本 ``source_revision`` 单调递增：既保留调用方的复审版本号，也保证每次重算唯一。"""
    return max(int(award_revision) + 1, int(request_revision or 1))


def _build_plan(
    *,
    award: PointAward,
    source: SourceTarget,
    request_revision: int,
    moment: int,
    allow_post: bool = True,
) -> AwardPlan:
    """按「目标 - 已入账净额」的减法重算计划（多次零碎退款不漏扣）。

    ``allow_post=False`` 时只更新目标与可用时间（供 ``post_due_awards`` 先统一重算、
    再统一入账，保证计数与「无未决售后」判断都走同一条路径）。
    """
    posted = int(award.posted_points)
    status = award.status
    target = int(source.target_points)
    delta = 0
    entry_type: str | None = None
    posted_now = False

    if target <= 0:
        if status == "POSTED" and posted != 0:
            delta = -posted  # 取消或全额退款：已入账部分全额扣回，账面可为负
            entry_type = "AWARD_CLAWBACK"
            posted = 0
        elif status == "PENDING":
            status = "CANCELLED"
    elif status == "POSTED":
        delta = target - posted
        if delta > 0:
            entry_type = "AWARD"
        elif delta < 0:
            entry_type = "AWARD_CLAWBACK"
        posted = target
    else:
        postable = allow_post and source.available_at <= moment and source.blocked_reason is None
        if postable:
            delta = target - posted
            entry_type = "AWARD" if delta else None
            posted = target
            status = "POSTED"
            posted_now = True
        else:
            status = "PENDING"

    changed = any(
        (
            target != int(award.target_points),
            posted != int(award.posted_points),
            status != award.status,
            int(source.available_at) != int(award.available_at),
            source.user_id != award.user_id,
            source.blocked_reason != award.blocked_reason,
        )
    )
    return AwardPlan(
        revision=_next_revision(award.revision, request_revision) if changed else int(award.revision),
        user_id=source.user_id,
        target_points=target,
        posted_points=posted,
        status=status,
        available_at=int(source.available_at),
        label=source.label[:120],
        detail=source.detail,
        blocked_reason=source.blocked_reason,
        delta=delta,
        entry_type=entry_type,
        changed=changed,
        posted_now=posted_now,
        schedule_release=(
            changed
            and status == "PENDING"
            and source.release_scheduled
            and int(source.available_at) > moment
        ),
    )


def recompute_award(
    session: Session, request: AwardRequest, *, post_now: bool = True
) -> dict[str, Any]:
    """按来源重算奖励目标并入账（跨模块端口，签名见 ``contracts.LoyaltyPort``）。

    - 锁定同一来源（``source_type`` + ``source_id``），读取当前已核验金额/退款快照；
    - 未入账只更新 pending 目标；已入账追加差额流水；取消/全额退款目标为 0；
    - ``posted_points``、``point_ledger`` 与账户缓存同事务提交（调用方负责 commit）；
    - ``post_now=False`` 只重算不入账，供 ``post_due_awards`` 统一入账与计数。
    """
    if request.source_type not in ("order", "feedback"):
        raise AppError(
            "VALIDATION_ERROR", "积分来源类型不支持。", fields={"source_type": "仅支持 order/feedback"}
        )
    moment = now_ms()
    rule_kind = "feedback" if request.source_type == "feedback" else "purchase"
    for _attempt in range(3):
        award = repo.get_award(
            session, source_type=request.source_type, source_id=request.source_id, refresh=True
        )
        if award is None:
            repo.insert_award(
                session,
                id=new_id("award"),
                user_id=request.user_id,
                source_type=request.source_type,
                source_id=request.source_id,
                rule_version=request.rule_version or current_rule_version(session, kind=rule_kind),
                target_points=0,
                posted_points=0,
                available_at=moment,
                status="PENDING",
                label=(request.label or "")[:120],
                detail={},
                revision=0,
                created_at=moment,
                updated_at=moment,
            )
            award = repo.get_award(
                session, source_type=request.source_type, source_id=request.source_id, refresh=True
            )
            if award is None:  # pragma: no cover - 并发下唯一键必然给出可读行
                raise AppError("STATE_CONFLICT", "奖励来源建立失败，请稍后重试。")
        source = (
            _order_target(
                session, request, rule_version=award.rule_version, moment=moment, fallback_user_id=award.user_id
            )
            if request.source_type == "order"
            else _feedback_target(session, request, rule_version=award.rule_version)
        )
        plan = _build_plan(
            award=award,
            source=source,
            request_revision=request.source_revision,
            moment=moment,
            allow_post=post_now,
        )
        if not plan.changed:
            return _award_view(session, award)  # 幂等：重复事件/重复点击不重复发奖
        claimed = repo.cas_update_award(
            session, award_id=award.id, expected_revision=award.revision, values=plan.values()
        )
        if not claimed:
            continue  # 被并发请求推进：重读后再算一次
        _apply_plan(session, award=award, plan=plan, moment=moment)
        session.refresh(award)
        return _award_view(session, award, delta=plan.delta, posted_now=plan.posted_now)
    raise AppError("STATE_CONFLICT", "积分奖励正在被其他请求更新，请稍后重试。")


def _apply_plan(session: Session, *, award: PointAward, plan: AwardPlan, moment: int) -> None:
    """写入差额流水与账户缓存，并登记通知与 outbox（与奖励状态同事务）。"""
    if plan.delta and plan.entry_type:
        account = repo.ensure_account(session, user_id=plan.user_id)
        wrote = repo.append_ledger(
            session,
            id=new_id("ledger"),
            account_id=account.id,
            user_id=plan.user_id,
            delta=plan.delta,
            entry_type=plan.entry_type,
            source_type=award.source_type,
            source_id=award.source_id,
            source_revision=plan.revision,
            rule_version=award.rule_version,
            reversal_of=None,
            description=_entry_description(award=award, plan=plan),
            label=plan.label,
            created_at=moment,
        )
        if wrote:
            repo.add_balance(session, account_id=account.id, delta=plan.delta)
    if plan.posted_now and plan.delta > 0:
        _notify_points_posted(session, award=award, points=plan.delta, moment=moment)
    if plan.schedule_release:
        _schedule_release(session, award=award, available_at=plan.available_at)


def _entry_description(*, award: PointAward, plan: AwardPlan) -> str:
    """业务中文描述。反馈来源只写匿名摘要，不写反馈正文。"""
    if plan.entry_type == "AWARD_CLAWBACK":
        if award.source_type == "order":
            return f"退款扣回：订单 {award.source_id} 目标调整为 {plan.target_points} 分"
        return f"复核冲正：{award.label or award.source_id} 目标调整为 {plan.target_points} 分"
    if award.source_type == "order":
        return f"购买奖励：订单 {award.source_id} 商品实付折算 {plan.target_points} 分"
    level = str((plan.detail or {}).get("level") or "")
    level_label = FEEDBACK_LEVEL_LABELS.get(level, "有效反馈")
    return f"反馈奖励：{level_label}，{plan.target_points} 分"


def _award_view(
    session: Session, award: PointAward, *, delta: int = 0, posted_now: bool = False
) -> dict[str, Any]:
    return {
        "award_id": award.id,
        "user_id": award.user_id,
        "source_type": award.source_type,
        "source_id": award.source_id,
        "status": award.status,
        "target_points": int(award.target_points),
        "posted_points": int(award.posted_points),
        "revision": int(award.revision),
        "rule_version": award.rule_version,
        "available_at": int(award.available_at),
        "available_at_iso": iso(award.available_at),
        "blocked_reason": award.blocked_reason,
        "delta": int(delta),
        "posted_now": posted_now,
        "account": account_view(session, user_id=award.user_id),
    }


def _notify_points_posted(session: Session, *, award: PointAward, points: int, moment: int) -> None:
    """入账通知（站内信）。渠道留空：邮箱/短信目标由通知模块按用户偏好补全。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    view = account_view(session, user_id=award.user_id)
    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"points-posted-{award.id}-{award.revision}",
            event_type="points.posted",
            user_id=award.user_id,
            title="",
            body="",
            category="loyalty",
            channels=(),
            payload={
                "points": points,
                "source_label": award.label,
                "available_points": view["available_points"],
                "occurred_at": iso(moment),
            },
        ),
    )
    enqueue(
        session,
        event_type="points.posted",
        aggregate_id=award.id,
        aggregate_version=int(award.revision),
        payload={
            "user_id": award.user_id,
            "source_type": award.source_type,
            "source_id": award.source_id,
            "points": points,
            "available_points": view["available_points"],
        },
    )


def _schedule_release(session: Session, *, award: PointAward, available_at: int) -> None:
    """为延迟入账排期：outbox 事件到点后由 worker 再次调用 ``post_due_awards``。

    只在交付时间明确时排期，避免未交付订单不断自我续期。
    """
    if repo.scheduled_award_events(session, order_id=award.source_id, available_at=available_at):
        return
    enqueue(
        session,
        event_type=AWARD_DUE_EVENT,
        aggregate_id=award.source_id,
        aggregate_version=int(award.revision),
        payload={
            "order_id": award.source_id,
            "user_id": award.user_id,
            "reason": "points_release_scheduled",
            "available_at": iso(available_at),
        },
        available_at=available_at,
    )


def recompute_due_sources(session: Session, *, now: int | None = None, limit: int = 50) -> int:
    """消费 ``points.award_due`` 事件并重算来源奖励。

    商务模块只登记事件（订单号 + 用户 + 原因），因此购买/退款奖励必须由积分模块
    自己翻译成重算；事件里没有可信金额（09 文档：不盲信旧 outbox 中的点数）。
    """
    moment = now if now is not None else now_ms()
    events = repo.unconsumed_events(
        session, event_type=AWARD_DUE_EVENT, consumer_name=LOYALTY_CONSUMER, now=moment, limit=limit
    )
    recomputed = 0
    for event in events:
        payload = dict(event.payload or {})
        order_id = str(payload.get("order_id") or event.aggregate_id or "")
        if order_id:
            request = AwardRequest(
                user_id=str(payload.get("user_id") or ""),
                source_type="order",
                source_id=order_id,
                source_revision=int(event.aggregate_version or 1),
                eligible_amount_cents=0,
                rule_version="",
                available_at=moment,
                label=f"订单 {order_id}",
                detail={"event_id": event.event_id, "reason": payload.get("reason")},
            )
            try:
                recompute_award(session, request, post_now=False)
            except AppError as exc:
                if exc.code != "PROVIDER_UNAVAILABLE":
                    raise
                # 商务模块尚未就绪：不标记已消费，下一轮再试（隔离测试也依赖该行为）。
                continue
            recomputed += 1
        repo.mark_event_consumed(session, consumer_name=LOYALTY_CONSUMER, event_id=event.event_id)
    return recomputed


def post_due_awards(session: Session, *, now: int | None = None, limit: int = 50) -> int:
    """由 worker 调用：把 ``available_at <= now`` 且无未决售后的 PENDING 奖励入账。

    先重算来源（订单奖励会重新读取快照，未交付或已取消则不发放），再入账；
    返回本轮真正入账的奖励条数。调用方负责提交事务。
    """
    moment = now if now is not None else now_ms()
    recompute_due_sources(session, now=moment, limit=limit)
    posted = 0
    for award in repo.due_awards(session, now=moment, limit=limit):
        if award.source_type == "order":
            request = AwardRequest(
                user_id=award.user_id,
                source_type="order",
                source_id=award.source_id,
                source_revision=int(award.revision) + 1,
                eligible_amount_cents=0,
                rule_version=award.rule_version,
                available_at=moment,
                label=award.label,
            )
            try:
                result = recompute_award(session, request)
            except AppError as exc:
                if exc.code != "PROVIDER_UNAVAILABLE":
                    raise
                continue
            if result.get("posted_now"):
                posted += 1
            continue
        if _post_pending_award(session, award=award, moment=moment):
            posted += 1
    return posted


def _post_pending_award(session: Session, *, award: PointAward, moment: int) -> bool:
    """入账一条已到期的 PENDING 奖励（反馈奖励没有外部快照，可直接入账）。"""
    if award.status != "PENDING" or int(award.available_at) > moment:
        return False
    plan = AwardPlan(
        revision=_next_revision(award.revision, int(award.revision) + 1),
        user_id=award.user_id,
        target_points=int(award.target_points),
        posted_points=int(award.target_points),
        status="POSTED" if int(award.target_points) > 0 else "CANCELLED",
        available_at=int(award.available_at),
        label=award.label,
        detail=dict(award.detail or {}),
        blocked_reason=None,
        delta=int(award.target_points) - int(award.posted_points),
        entry_type="AWARD" if int(award.target_points) > int(award.posted_points) else None,
        changed=True,
        posted_now=int(award.target_points) > 0,
        schedule_release=False,
    )
    claimed = repo.cas_update_award(
        session, award_id=award.id, expected_revision=award.revision, values=plan.values()
    )
    if not claimed:
        return False
    _apply_plan(session, award=award, plan=plan, moment=moment)
    session.refresh(award)
    return True


def reverse_award(
    session: Session, *, source_type: str, source_id: str, reason: str, actor_id: str | None
) -> dict[str, Any]:
    """复核冲正（确认作弊/误发）：追加反向流水，不删除原流水（09 文档）。"""
    award = repo.get_award(session, source_type=source_type, source_id=source_id, refresh=True)
    if award is None:
        raise AppError("RESOURCE_NOT_FOUND")
    posted = int(award.posted_points)
    if posted == 0:
        return _award_view(session, award)
    moment = now_ms()
    revision = _next_revision(award.revision, int(award.revision) + 1)
    claimed = repo.cas_update_award(
        session,
        award_id=award.id,
        expected_revision=award.revision,
        values={
            "revision": revision,
            "target_points": 0,
            "posted_points": 0,
            "status": "CANCELLED",
            "blocked_reason": None,
            "updated_at": moment,
        },
    )
    if not claimed:
        raise AppError("STATE_CONFLICT", "奖励状态已变化，请刷新后重试。")
    last = repo.last_ledger_for_source(session, source_type=source_type, source_id=source_id)
    account = repo.ensure_account(session, user_id=award.user_id)
    wrote = repo.append_ledger(
        session,
        id=new_id("ledger"),
        account_id=account.id,
        user_id=award.user_id,
        delta=-posted,
        entry_type="REVERSAL",
        source_type=source_type,
        source_id=source_id,
        source_revision=revision,
        rule_version=award.rule_version,
        reversal_of=last.id if last is not None else None,
        description=f"复核冲正：{reason[:80]}",
        label=award.label,
        created_at=moment,
    )
    if wrote:
        repo.add_balance(session, account_id=account.id, delta=-posted)
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="points.award_reverse",
        resource_type="point_award",
        resource_id=award.id,
        before={"posted_points": posted},
        after={"posted_points": 0, "delta": -posted},
        reason=reason[:200],
    )
    session.refresh(award)
    return _award_view(session, award, delta=-posted)


# ------------------------------------------------------------------ 后台调整（C21）
def create_adjustment(
    session: Session,
    *,
    user_id: str,
    delta_points: int,
    reason: str,
    requested_by: str | None,
    source_type: str | None = None,
    source_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> PointAdjustment:
    """建立调整申请（权限码 ``points.adjust``，路由在 admin_routes.py）。

    申请只登记意图；入账必须经 ``approve_adjustment`` 由另一名员工批准。
    """
    delta = int(delta_points)
    if delta == 0:
        raise AppError("VALIDATION_ERROR", "调整分值不能为 0。", fields={"delta_points": "不能为 0"})
    if abs(delta) > MAX_ADJUSTMENT_POINTS:
        raise AppError(
            "VALIDATION_ERROR",
            f"单笔调整不能超过 {MAX_ADJUSTMENT_POINTS} 分，请拆分或走线下审批。",
            fields={"delta_points": "超出上限"},
        )
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 5:
        raise AppError("VALIDATION_ERROR", "请填写至少 5 个字的调整理由。", fields={"reason": "过短"})
    row = PointAdjustment(
        id=new_id("adjustment"),
        user_id=user_id,
        delta_points=delta,
        reason=clean_reason[:300],
        status="PENDING",
        requested_by=requested_by,
        source_type=source_type,
        source_id=source_id,
        evidence=evidence,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="staff" if requested_by else "system",
        actor_id=requested_by,
        action="points.adjustment_create",
        resource_type="point_adjustment",
        resource_id=row.id,
        after={"user_id": user_id, "delta_points": delta, "reason": row.reason},
        reason=row.reason,
    )
    return row


def approve_adjustment(session: Session, *, adjustment_id: str, approver_id: str | None) -> dict[str, Any]:
    """批准调整并入账（权限码 ``points.approve``，路由在 admin_routes.py）。

    入账一律**追加流水**再改账户缓存，不能直接写余额；同一调整重复批准只入账一次。
    """
    row = repo.get_adjustment(session, adjustment_id=adjustment_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status == "APPLIED":
        account = repo.get_account(session, user_id=row.user_id)
        return {
            "adjustment_id": row.id,
            "status": row.status,
            "delta_points": int(row.delta_points),
            "ledger_id": row.ledger_id,
            "posted_balance": int(account.posted_balance) if account is not None else 0,
            "applied": False,
            "message": "该调整已入账，无需重复批准。",
        }
    if row.status == "REJECTED":
        raise AppError("STATE_CONFLICT", "该调整已被驳回。")
    if row.requested_by and approver_id and row.requested_by == approver_id:
        # C21：申请与批准分离，同一员工不能既申请又批准。
        raise AppError("FORBIDDEN", "调整申请与批准必须由不同员工完成。")
    moment = now_ms()
    claimed = repo.cas_update_adjustment(
        session,
        adjustment_id=row.id,
        expected_status="PENDING",
        values={
            "status": "APPROVED",
            "approved_by": approver_id,
            "approved_at": moment,
            "version": row.version + 1,
            "updated_at": moment,
        },
    )
    if not claimed:
        raise AppError("STATE_CONFLICT", "该调整已被处理，请刷新后重试。")

    account = repo.ensure_account(session, user_id=row.user_id)
    ledger_id = new_id("ledger")
    wrote = repo.append_ledger(
        session,
        id=ledger_id,
        account_id=account.id,
        user_id=row.user_id,
        delta=int(row.delta_points),
        entry_type="ADJUSTMENT",
        source_type="adjustment",
        source_id=row.id,
        source_revision=1,
        rule_version=current_rule_version(session),
        reversal_of=None,
        description=f"后台调整：{row.reason[:100]}",
        label=f"调整单 {row.id}",
        created_at=moment,
    )
    if wrote:
        repo.add_balance(session, account_id=account.id, delta=int(row.delta_points))
    else:  # pragma: no cover - 唯一键冲突只可能是并发重复批准
        ledger_id = str(row.ledger_id or ledger_id)
    row.status = "APPLIED"
    row.ledger_id = ledger_id
    row.version += 1
    row.updated_at = moment
    session.flush()
    view = account_view(session, user_id=row.user_id)
    audit.record(
        session,
        actor_type="staff" if approver_id else "system",
        actor_id=approver_id,
        action="points.adjustment_approve",
        resource_type="point_adjustment",
        resource_id=row.id,
        before={"status": "PENDING"},
        after={
            "status": row.status,
            "ledger_id": ledger_id,
            "delta_points": int(row.delta_points),
            "posted_balance": view["posted_balance"],
        },
        reason=row.reason,
    )
    if int(row.delta_points) > 0:
        _notify_staff(
            session,
            event_id=f"points-adjustment-{row.id}",
            title="星绒币调整已入账",
            body=f"{row.user_id} 的调整单 {row.id} 已入账 {row.delta_points} 分。",
            payload={"adjustment_id": row.id, "delta_points": int(row.delta_points)},
        )
    return {
        "adjustment_id": row.id,
        "status": row.status,
        "delta_points": int(row.delta_points),
        "ledger_id": ledger_id,
        "posted_balance": view["posted_balance"],
        "applied": bool(wrote),
        "message": "调整已入账。",
    }


def reject_adjustment(
    session: Session, *, adjustment_id: str, approver_id: str | None, reason: str
) -> PointAdjustment:
    """驳回调整申请（权限码 ``points.approve``）。驳回不改余额、不写流水。"""
    row = repo.get_adjustment(session, adjustment_id=adjustment_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 5:
        raise AppError("VALIDATION_ERROR", "请填写至少 5 个字的驳回理由。", fields={"reason": "过短"})
    if row.status not in ("PENDING",):
        raise AppError("STATE_CONFLICT", "该调整已被处理。")
    claimed = repo.cas_update_adjustment(
        session,
        adjustment_id=row.id,
        expected_status="PENDING",
        values={
            "status": "REJECTED",
            "approved_by": approver_id,
            "approved_at": now_ms(),
            "reject_reason": clean_reason[:300],
            "version": row.version + 1,
            "updated_at": now_ms(),
        },
    )
    if not claimed:
        raise AppError("STATE_CONFLICT", "该调整已被处理，请刷新后重试。")
    session.refresh(row)
    audit.record(
        session,
        actor_type="staff" if approver_id else "system",
        actor_id=approver_id,
        action="points.adjustment_reject",
        resource_type="point_adjustment",
        resource_id=row.id,
        after={"status": row.status},
        reason=clean_reason[:200],
    )
    return row


def list_adjustments(
    session: Session, *, user_id: str | None = None, status: str | None = None, limit: int = 50
) -> list[PointAdjustment]:
    if status is not None and status not in ADJUSTMENT_STATUSES:
        raise AppError("VALIDATION_ERROR", "调整状态无效。", fields={"status": "非法状态"})
    return repo.list_adjustments(session, user_id=user_id, status=status, limit=limit)


# ------------------------------------------------------------------ 冻结与兑换（C15，默认关闭）
def reservations_enabled(session: Session) -> bool:
    """C15 未确认时，兑换入口与接口全部关闭（09 文档：不能宣传未上线权益）。"""
    if not bool(_policy(session).get("redemption_enabled")):
        return False
    from app.modules.content.ports import feature_flags

    return bool(feature_flags(session).get("points_redemption", False))


def _points_to_cents(points: int, *, points_per_yuan: int) -> int:
    """按兑换率把积分折算成整数分：向下取整，绝不产生小数分或超出上限。"""
    if points_per_yuan <= 0:
        raise AppError("FEATURE_DISABLED", "积分兑换尚未配置兑换率，入口保持关闭。")
    return max(0, int(points)) * 100 // points_per_yuan


def _cents_to_points(cents: int, *, points_per_yuan: int) -> int:
    """把要抵扣的分数折算回**恰好够用**的积分（向上取整，避免因取整多扣用户积分）。"""
    if points_per_yuan <= 0:
        raise AppError("FEATURE_DISABLED", "积分兑换尚未配置兑换率，入口保持关闭。")
    amount = max(0, int(cents))
    return (amount * points_per_yuan + 99) // 100


def redemption_policy(session: Session) -> dict[str, Any]:
    """兑换规则视图。规则值全部来自运营配置，未配置时如实返回 0 而不是猜一个数。"""
    policy = _policy(session)
    points_per_yuan = int(policy.get("redemption_points_per_yuan", 0))
    min_points = int(policy.get("redemption_min_points", 0))
    max_ratio = int(policy.get("redemption_max_ratio_percent", 0))
    rules_ready = points_per_yuan > 0 and min_points > 0 and max_ratio > 0
    return {
        "rules_ready": rules_ready,
        "enabled": reservations_enabled(session) and rules_ready,
        "points_name": str(policy["points_name"]),
        "points_per_yuan": points_per_yuan,
        "min_points": min_points,
        "max_ratio_percent": max_ratio,
        "expiry_days": int(policy.get("redemption_expiry_days", 0)),
        "restore_on_refund": bool(policy.get("redemption_restore_on_refund", True)),
        "ratio_note": str(policy.get("redemption_ratio_note", "")),
    }


def redemption_quote(
    session: Session, *, user_id: str, amount_cents: int, points: int = 0
) -> dict[str, Any]:
    """服务端裁定：这笔金额最多能用多少积分、实际抵扣多少分。

    金额与积分都只能用整数（分 / 枚）。客户端提交的是「想用多少积分」这一选择，
    上限、比例与折算一律由服务端计算（08 文档：客户端不得决定金额）。
    """
    if int(amount_cents) <= 0:
        raise AppError("VALIDATION_ERROR", "结算金额必须大于 0。", fields={"amount_cents": "非法金额"})
    if int(points) < 0:
        raise AppError("VALIDATION_ERROR", "积分数量不能为负。", fields={"points": "非法积分数量"})
    policy = redemption_policy(session)
    if not policy["rules_ready"]:
        raise AppError(
            "FEATURE_DISABLED",
            "积分抵扣的兑换率、最低使用数量与抵扣上限尚未配置，入口保持关闭。",
        )
    if not reservations_enabled(session):
        raise AppError("FEATURE_DISABLED", "积分兑换尚未开放。")

    points_per_yuan = int(policy["points_per_yuan"])
    available = int(account_view(session, user_id=user_id)["available_points"])
    max_cents_by_ratio = int(amount_cents) * int(policy["max_ratio_percent"]) // 100
    max_points = min(available, _cents_to_points(max_cents_by_ratio, points_per_yuan=points_per_yuan))
    requested = int(points)
    if 0 < requested < int(policy["min_points"]):
        raise AppError(
            "VALIDATION_ERROR",
            f"单笔至少使用 {policy['min_points']} {policy['points_name']}。",
            fields={"points": "低于最低使用数量"},
        )
    allowed_points = min(requested, max_points)
    applied_cents = _points_to_cents(allowed_points, points_per_yuan=points_per_yuan)
    # 折算回恰好够用的积分：向下取整可能让「积分 × 兑换率」多出零头，
    # 这里只扣真正换算成金额的那部分，不吞掉用户积分。
    applied_points = _cents_to_points(applied_cents, points_per_yuan=points_per_yuan)
    return {
        "enabled": True,
        "points_name": str(policy["points_name"]),
        "amount_cents": int(amount_cents),
        "available_points": available,
        "max_points": max_points,
        "max_cents": _points_to_cents(max_points, points_per_yuan=points_per_yuan),
        "requested_points": requested,
        "applied_points": applied_points,
        "applied_cents": applied_cents,
        # capped 只表示「被上限或余额截断」，取整零头不算被截断。
        "capped": allowed_points < requested,
        "rounded_points": allowed_points - applied_points,
        "min_points": int(policy["min_points"]),
        "points_per_yuan": points_per_yuan,
        "max_ratio_percent": int(policy["max_ratio_percent"]),
        "expiry_days": int(policy["expiry_days"]),
        "restore_on_refund": bool(policy["restore_on_refund"]),
        "ratio_note": str(policy["ratio_note"]),
    }


def reserve_points(
    session: Session,
    *,
    user_id: str,
    redemption_id: str,
    points: int,
    ttl_minutes: int = 15,
    now: int | None = None,
) -> dict[str, Any]:
    """结算冻结积分。仅 C15 开启后可用；条件更新保证不会双标签页同时花同一积分。

    失败时抛错由调用方回滚整个事务（不会留下半冻结状态）。
    """
    if not reservations_enabled(session):
        raise AppError("FEATURE_DISABLED", "积分兑换尚未开放。")
    if int(points) <= 0:
        raise AppError("VALIDATION_ERROR", "冻结积分数必须大于 0。", fields={"points": "非法"})
    moment = now if now is not None else now_ms()
    existing = repo.get_reservation(session, redemption_id=redemption_id)
    if existing is not None:
        return _reservation_view(existing)
    account = repo.ensure_account(session, user_id=user_id)
    inserted = repo.insert_reservation(
        session,
        id=new_id("reservation"),
        account_id=account.id,
        redemption_id=redemption_id,
        points=int(points),
        state="HELD",
        expires_at=add_minutes_ms(ttl_minutes, moment),
        created_at=moment,
        updated_at=moment,
    )
    if not inserted:
        current = repo.get_reservation(session, redemption_id=redemption_id)
        if current is None:  # pragma: no cover - 冲突后必然可读
            raise AppError("STATE_CONFLICT", "积分冻结失败，请重试。")
        return _reservation_view(current)
    if not repo.add_reserved(session, user_id=user_id, points=int(points)):
        raise AppError("POINTS_INSUFFICIENT")
    return {
        "redemption_id": redemption_id,
        "points": int(points),
        "state": "HELD",
        "expires_at": iso(add_minutes_ms(ttl_minutes, moment)),
        "available_points": account_view(session, user_id=user_id)["available_points"],
    }


def release_reservation(session: Session, *, redemption_id: str) -> dict[str, Any]:
    """释放冻结（关闭订单）。只改冻结额度，不动账面，因此不写账本。"""
    row = repo.get_reservation(session, redemption_id=redemption_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.state != "HELD":
        return _reservation_view(row)
    claimed = repo.cas_update_reservation(
        session,
        reservation_id=row.id,
        expected_state="HELD",
        values={"state": "RELEASED", "settled_at": now_ms(), "updated_at": now_ms()},
    )
    if claimed:
        repo.release_reserved(session, account_id=row.account_id, points=int(row.points))
        session.refresh(row)
    return _reservation_view(row)


def consume_reservation(session: Session, *, redemption_id: str) -> dict[str, Any]:
    """支付确认后消费冻结：账面扣减并发 REDEMPTION 流水。"""
    row = repo.get_reservation(session, redemption_id=redemption_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.state == "CONSUMED":
        return _reservation_view(row)
    if row.state != "HELD":
        raise AppError("STATE_CONFLICT", "该冻结已释放或过期。")
    moment = now_ms()
    claimed = repo.cas_update_reservation(
        session,
        reservation_id=row.id,
        expected_state="HELD",
        values={"state": "CONSUMED", "settled_at": moment, "updated_at": moment},
    )
    if not claimed:
        raise AppError("STATE_CONFLICT", "冻结状态已变化，请刷新后重试。")
    account = repo.get_account_by_id(session, account_id=row.account_id)
    if account is None:  # pragma: no cover - 外键保证存在
        raise AppError("RESOURCE_NOT_FOUND")
    wrote = repo.append_ledger(
        session,
        id=new_id("ledger"),
        account_id=account.id,
        user_id=account.user_id,
        delta=-int(row.points),
        entry_type="REDEMPTION",
        source_type="redemption",
        source_id=row.redemption_id,
        source_revision=1,
        rule_version=current_rule_version(session),
        reversal_of=None,
        description=f"积分兑换：消费 {row.points} 分",
        label="积分兑换",
        created_at=moment,
    )
    if wrote:
        repo.release_reserved(session, account_id=account.id, points=int(row.points))
        repo.add_balance(session, account_id=account.id, delta=-int(row.points))
    session.refresh(row)
    return _reservation_view(row)


def expire_reservations(session: Session, *, now: int | None = None, limit: int = 50) -> int:
    """释放超时冻结（worker 调用）。返回释放条数。"""
    moment = now if now is not None else now_ms()
    released = 0
    for row in repo.expired_reservations(session, now=moment, limit=limit):
        claimed = repo.cas_update_reservation(
            session,
            reservation_id=row.id,
            expected_state="HELD",
            values={"state": "EXPIRED", "settled_at": moment, "updated_at": moment},
        )
        if claimed:
            repo.release_reserved(session, account_id=row.account_id, points=int(row.points))
            released += 1
    return released


def _reservation_view(row: Any) -> dict[str, Any]:
    return {
        "redemption_id": row.redemption_id,
        "points": int(row.points),
        "state": row.state,
        "expires_at": iso(row.expires_at),
    }


# ------------------------------------------------------------------ 订单抵扣（C15）
def order_redemption_id(order_id: str) -> str:
    """订单抵扣的冻结标识：一单一冻结，天然幂等（重复建单不会重复冻结）。"""
    return f"order:{order_id}"


def order_redemption_view(session: Session, *, order_id: str) -> dict[str, Any]:
    """本单的积分冻结视图；没有冻结时如实返回 ``state="NONE"`` 而不是报错。"""
    row = repo.get_reservation(session, redemption_id=order_redemption_id(order_id))
    if row is None:
        return {"redemption_id": order_redemption_id(order_id), "points": 0, "state": "NONE",
                "expires_at": None}
    return _reservation_view(row)


def reserve_order_redemption(
    session: Session, *, user_id: str, order_id: str, points: int, ttl_minutes: int = 15
) -> dict[str, Any]:
    """下单时冻结积分（与建单同一事务）。可用额度不足由条件更新拦下并回滚整单。"""
    return reserve_points(
        session,
        user_id=user_id,
        redemption_id=order_redemption_id(order_id),
        points=points,
        ttl_minutes=ttl_minutes,
    )


def release_order_redemption(session: Session, *, order_id: str) -> dict[str, Any]:
    """关闭订单时释放冻结。只改冻结额度、不动账面，因此不写账本；没有冻结时是空操作。"""
    row = repo.get_reservation(session, redemption_id=order_redemption_id(order_id))
    if row is None:
        return {"redemption_id": order_redemption_id(order_id), "points": 0, "state": "NONE",
                "expires_at": None, "released": False}
    released = row.state == "HELD"
    view = release_reservation(session, redemption_id=order_redemption_id(order_id))
    return {**view, "released": released}


def consume_order_redemption(session: Session, *, order_id: str) -> dict[str, Any]:
    """支付确认后消费冻结：账面扣减并发 REDEMPTION 流水；没有冻结时是空操作。"""
    row = repo.get_reservation(session, redemption_id=order_redemption_id(order_id))
    if row is None:
        return {"redemption_id": order_redemption_id(order_id), "points": 0, "state": "NONE",
                "expires_at": None, "consumed": False}
    consumed = row.state == "HELD"
    view = consume_reservation(session, redemption_id=order_redemption_id(order_id))
    return {**view, "consumed": consumed}


def restored_points_for_order(session: Session, *, order_id: str) -> int:
    """本单已通过退款返还的积分（REDEMPTION_RELEASE 流水之和）。"""
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(PointLedger.delta), 0)).where(
                PointLedger.source_type == "redemption",
                PointLedger.source_id == order_redemption_id(order_id),
                PointLedger.entry_type == "REDEMPTION_RELEASE",
            )
        )
        or 0
    )


def restore_order_redemption(
    session: Session, *, order_id: str, target_points: int, reason: str
) -> dict[str, Any]:
    """退款按原订单分摊返还积分。

    采用「目标值」语义：调用方给出**累计应返还**的积分数，本函数只补差额，
    因此重复投递、乱序退款、分批退款都不会重复返还（09 文档第 73 条）。
    """
    account = None
    row = repo.get_reservation(session, redemption_id=order_redemption_id(order_id))
    if row is None:
        return {"order_id": order_id, "restored_total": 0, "restored_now": 0, "state": "NONE"}
    already = restored_points_for_order(session, order_id=order_id)
    if not bool(_policy(session).get("redemption_restore_on_refund", True)):
        # 退款恢复细则（N04）由运营配置：关掉时只记录，不返还积分。
        return {
            "order_id": order_id,
            "restored_total": already,
            "restored_now": 0,
            "state": row.state,
            "skipped": "RESTORE_DISABLED",
        }
    target = max(0, min(int(target_points), int(row.points)))
    delta = target - already
    if delta <= 0:
        return {
            "order_id": order_id,
            "restored_total": already,
            "restored_now": 0,
            "state": row.state,
        }
    account = repo.get_account_by_id(session, account_id=row.account_id)
    if account is None:  # pragma: no cover - 外键保证存在
        raise AppError("RESOURCE_NOT_FOUND")
    moment = now_ms()
    revision = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(PointLedger)
            .where(
                PointLedger.source_type == "redemption",
                PointLedger.source_id == order_redemption_id(order_id),
                PointLedger.entry_type == "REDEMPTION_RELEASE",
            )
        )
        or 0
    ) + 1
    wrote = repo.append_ledger(
        session,
        id=new_id("ledger"),
        account_id=account.id,
        user_id=account.user_id,
        delta=delta,
        entry_type="REDEMPTION_RELEASE",
        source_type="redemption",
        source_id=order_redemption_id(order_id),
        source_revision=revision,
        rule_version=current_rule_version(session),
        reversal_of=None,
        description=f"退款返还：返还 {delta} 分（累计 {target} 分）",
        label="退款返还",
        created_at=moment,
    )
    if wrote:
        repo.add_balance(session, account_id=account.id, delta=delta)
    return {
        "order_id": order_id,
        "restored_total": target if wrote else already,
        "restored_now": delta if wrote else 0,
        "state": row.state,
        "reason": reason,
    }


# ------------------------------------------------------------------ 内部工具
def _notify_staff(
    session: Session, *, event_id: str, title: str, body: str, payload: dict[str, Any]
) -> None:
    """运营提醒：登记为用户无关的站内通知，由后台任务中心读取。"""
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    enqueue_notification(
        session,
        NotificationRequest(
            event_id=event_id,
            event_type="staff.alert",
            user_id=None,
            title=title,
            body=body,
            category="loyalty",
            channels=(),
            payload={"title": title, "body": body, "occurred_at": iso(now_ms()), **payload},
        ),
    )


__all__ = [
    "AWARD_DUE_EVENT",
    "FEEDBACK_LEVEL_LABELS",
    "FEEDBACK_REWARD_LEVELS",
    "LOYALTY_CONSUMER",
    "MAX_ADJUSTMENT_POINTS",
    "LEDGER_ENTRY_TYPES",
    "account_view",
    "approve_adjustment",
    "consume_reservation",
    "create_adjustment",
    "create_appeal",
    "current_rule_version",
    "eligible_net_cents",
    "expire_reservations",
    "feedback_policy",
    "ledger_view",
    "list_adjustments",
    "list_appeals",
    "post_due_awards",
    "publish_rule",
    "purchase_policy",
    "recompute_award",
    "recompute_due_sources",
    "reject_adjustment",
    "release_reservation",
    "reservations_enabled",
    "reserve_points",
    "resolve_appeal",
    "reverse_award",
    "serialize_appeal",
    "serialize_ledger_entry",
    "target_points_for_feedback",
    "target_points_for_order",
    "verify_account_consistency",
]
