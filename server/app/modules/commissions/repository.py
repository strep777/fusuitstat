"""委托模块查询。

按 ID 查询客户可见对象时一律同时带 ``user_id`` 过滤（逐对象归属检查，06 文档）；
查不到由 service 抛 ``RESOURCE_NOT_FOUND``，不泄露对象是否存在。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.commissions.models import (
    ChangeRequest,
    CommissionProject,
    Inquiry,
    InquiryRevision,
    Measurement,
    ProductionStage,
    Quote,
    QuoteItem,
    ScheduleApplication,
    ScheduleRound,
    SlotReservation,
)
from app.platform.ids import now_ms


# ------------------------------------------------------------------ 询价
def get_inquiry(session: Session, *, inquiry_id: str, user_id: str) -> Inquiry | None:
    return session.scalars(
        sa.select(Inquiry).where(Inquiry.id == inquiry_id, Inquiry.user_id == user_id)
    ).one_or_none()


def overdue_inquiries(session: Session, *, before_ms: int, limit: int = 50) -> list[Inquiry]:
    """已提交、首响截止已过、且工作人员尚未响应的询价（C30 逾期提醒）。"""
    return list(
        session.scalars(
            sa.select(Inquiry)
            .where(
                Inquiry.status.in_(("SUBMITTED", "NEEDS_INFO")),
                Inquiry.submitted_at.is_not(None),
                Inquiry.submitted_at <= before_ms,
                Inquiry.first_responded_at.is_(None),
            )
            .order_by(Inquiry.submitted_at)
            .limit(max(1, min(limit, 200)))
        )
    )


def get_inquiry_for_staff(session: Session, inquiry_id: str) -> Inquiry | None:
    """员工路径按权限码访问，不做本人过滤。"""
    return session.get(Inquiry, inquiry_id)


def list_inquiries(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Inquiry]:
    stmt = sa.select(Inquiry).where(Inquiry.user_id == user_id)
    if status:
        stmt = stmt.where(Inquiry.status == status)
    if statuses:
        stmt = stmt.where(Inquiry.status.in_(statuses))
    return list(
        session.scalars(
            stmt.order_by(Inquiry.created_at.desc()).limit(min(limit, 100)).offset(offset)
        )
    )


def count_inquiries(session: Session, *, user_id: str, status: str | None = None) -> int:
    stmt = (
        sa.select(sa.func.count())
        .select_from(Inquiry)
        .where(Inquiry.user_id == user_id)
    )
    if status:
        stmt = stmt.where(Inquiry.status == status)
    return int(session.scalar(stmt) or 0)


def list_inquiries_admin(
    session: Session,
    *,
    status: str | None = None,
    assignee_id: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Inquiry], int]:
    """员工询价列表。``query`` 只按角色名做包含匹配，不检索联系方式等私密字段。"""
    conditions = []
    if status:
        conditions.append(Inquiry.status == status)
    if assignee_id:
        conditions.append(Inquiry.assignee_id == assignee_id)
    if query:
        conditions.append(Inquiry.character_name.ilike(f"%{query.strip()}%"))
    stmt = sa.select(Inquiry).where(*conditions)
    total = int(
        session.scalar(sa.select(sa.func.count()).select_from(Inquiry).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            stmt.order_by(Inquiry.created_at.desc()).limit(min(limit, 100)).offset(offset)
        )
    )
    return rows, total


def count_open_inquiries_for_user(session: Session, *, user_id: str) -> int:
    """注销阻碍提示用：仍在处理中的询价数量。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Inquiry)
            .where(
                Inquiry.user_id == user_id,
                Inquiry.status.in_(("DRAFT", "SUBMITTED", "NEEDS_INFO", "QUOTED")),
            )
        )
        or 0
    )


def count_inquiries_needs_info(session: Session, *, user_id: str) -> int:
    """账户总览「待补件」计数：工作室已要求补充资料的询价（07 文档 NEEDS_INFO）。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Inquiry)
            .where(Inquiry.user_id == user_id, Inquiry.status == "NEEDS_INFO")
        )
        or 0
    )


def latest_revision(session: Session, *, inquiry_id: str) -> InquiryRevision | None:
    return session.scalars(
        sa.select(InquiryRevision)
        .where(InquiryRevision.inquiry_id == inquiry_id)
        .order_by(InquiryRevision.revision.desc())
        .limit(1)
    ).one_or_none()


def max_revision(session: Session, *, inquiry_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(InquiryRevision.revision), 0)).where(
                InquiryRevision.inquiry_id == inquiry_id
            )
        )
        or 0
    )


def list_revisions(session: Session, *, inquiry_id: str, limit: int = 20) -> list[InquiryRevision]:
    return list(
        session.scalars(
            sa.select(InquiryRevision)
            .where(InquiryRevision.inquiry_id == inquiry_id)
            .order_by(InquiryRevision.revision.desc())
            .limit(min(limit, 50))
        )
    )


# ------------------------------------------------------------------ 报价
def get_quote(session: Session, quote_id: str) -> Quote | None:
    return session.get(Quote, quote_id)


def get_quote_for_user(session: Session, *, quote_id: str, user_id: str) -> Quote | None:
    return session.scalars(
        sa.select(Quote)
        .join(Inquiry, Inquiry.id == Quote.inquiry_id)
        .where(Quote.id == quote_id, Inquiry.user_id == user_id)
    ).one_or_none()


def get_quote_for_inquiry(session: Session, *, inquiry_id: str, quote_id: str) -> Quote | None:
    return session.scalars(
        sa.select(Quote).where(Quote.id == quote_id, Quote.inquiry_id == inquiry_id)
    ).one_or_none()


def list_quotes_for_inquiry(session: Session, *, inquiry_id: str, limit: int = 20) -> list[Quote]:
    return list(
        session.scalars(
            sa.select(Quote)
            .where(Quote.inquiry_id == inquiry_id)
            .order_by(Quote.quote_version.desc())
            .limit(min(limit, 50))
        )
    )


def current_published_quote(session: Session, *, inquiry_id: str) -> Quote | None:
    return session.scalars(
        sa.select(Quote)
        .where(Quote.inquiry_id == inquiry_id, Quote.status == "PUBLISHED")
        .order_by(Quote.quote_version.desc())
        .limit(1)
    ).one_or_none()


def latest_quote(session: Session, *, inquiry_id: str) -> Quote | None:
    """该询价最新的一版报价（不论状态），用于后台工作台展示。"""
    return session.scalars(
        sa.select(Quote)
        .where(Quote.inquiry_id == inquiry_id)
        .order_by(Quote.quote_version.desc())
        .limit(1)
    ).one_or_none()


def max_quote_version(session: Session, *, inquiry_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(Quote.quote_version), 0)).where(
                Quote.inquiry_id == inquiry_id
            )
        )
        or 0
    )


def list_quote_items(session: Session, *, quote_id: str) -> list[QuoteItem]:
    return list(
        session.scalars(
            sa.select(QuoteItem).where(QuoteItem.quote_id == quote_id).order_by(QuoteItem.position)
        )
    )


def expired_published_quotes(session: Session, *, moment: int, limit: int = 50) -> list[Quote]:
    return list(
        session.scalars(
            sa.select(Quote)
            .where(Quote.status == "PUBLISHED", Quote.expires_at <= moment)
            .order_by(Quote.expires_at)
            .limit(min(limit, 200))
        )
    )


def count_quotes_awaiting_decision(session: Session, *, user_id: str, moment: int | None = None) -> int:
    """待客户决定的报价数量（账户待办提示）。"""
    now = moment if moment is not None else now_ms()
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Quote)
            .join(Inquiry, Inquiry.id == Quote.inquiry_id)
            .where(
                Inquiry.user_id == user_id,
                Inquiry.status == "QUOTED",
                Quote.status == "PUBLISHED",
                Quote.expires_at > now,
            )
        )
        or 0
    )


# ------------------------------------------------------------------ 档期与申请
def get_round(session: Session, round_id: str) -> ScheduleRound | None:
    return session.get(ScheduleRound, round_id)


def list_rounds(
    session: Session,
    *,
    statuses: tuple[str, ...] = ("OPEN",),
    limit: int = 20,
    offset: int = 0,
) -> list[ScheduleRound]:
    return list(
        session.scalars(
            sa.select(ScheduleRound)
            .where(ScheduleRound.status.in_(statuses))
            .order_by(ScheduleRound.start_at)
            .limit(min(limit, 100))
            .offset(offset)
        )
    )


def count_rounds(session: Session, *, statuses: tuple[str, ...] = ("OPEN",)) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count()).select_from(ScheduleRound).where(ScheduleRound.status.in_(statuses))
        )
        or 0
    )


def list_rounds_admin(
    session: Session,
    *,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ScheduleRound], int]:
    """运营档期列表：不受公开的 ``OPEN`` 过滤限制。"""
    conditions = []
    if status:
        conditions.append(ScheduleRound.status == status)
    stmt = sa.select(ScheduleRound).where(*conditions)
    total = int(
        session.scalar(sa.select(sa.func.count()).select_from(ScheduleRound).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            stmt.order_by(ScheduleRound.start_at.desc()).limit(min(limit, 100)).offset(offset)
        )
    )
    return rows, total


def get_application(session: Session, *, application_id: str, user_id: str) -> ScheduleApplication | None:
    return session.scalars(
        sa.select(ScheduleApplication).where(
            ScheduleApplication.id == application_id, ScheduleApplication.user_id == user_id
        )
    ).one_or_none()


def find_application(
    session: Session, *, round_id: str, user_id: str, inquiry_id: str
) -> ScheduleApplication | None:
    return session.scalars(
        sa.select(ScheduleApplication).where(
            ScheduleApplication.round_id == round_id,
            ScheduleApplication.user_id == user_id,
            ScheduleApplication.inquiry_id == inquiry_id,
        )
    ).one_or_none()


def list_applications(
    session: Session, *, round_id: str, user_id: str, limit: int = 20
) -> list[ScheduleApplication]:
    return list(
        session.scalars(
            sa.select(ScheduleApplication)
            .where(ScheduleApplication.round_id == round_id, ScheduleApplication.user_id == user_id)
            .order_by(ScheduleApplication.created_at)
            .limit(min(limit, 50))
        )
    )


def count_applications(session: Session, *, round_id: str, user_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(ScheduleApplication)
            .where(ScheduleApplication.round_id == round_id, ScheduleApplication.user_id == user_id)
        )
        or 0
    )


def max_rank_sequence(session: Session, *, round_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ScheduleApplication.rank_sequence), 0)).where(
                ScheduleApplication.round_id == round_id
            )
        )
        or 0
    )


def selected_applications_for_inquiry(session: Session, *, inquiry_id: str) -> list[ScheduleApplication]:
    """该询价已通过筛选的档期申请（接受报价时校验名额资格）。"""
    return list(
        session.scalars(
            sa.select(ScheduleApplication)
            .where(
                ScheduleApplication.inquiry_id == inquiry_id,
                ScheduleApplication.status.in_(("SELECTED", "SHORTLISTED")),
            )
            .order_by(ScheduleApplication.created_at)
        )
    )


def list_applications_for_round(
    session: Session, *, round_id: str, statuses: tuple[str, ...] | None = None, limit: int = 100
) -> list[ScheduleApplication]:
    stmt = sa.select(ScheduleApplication).where(ScheduleApplication.round_id == round_id)
    if statuses:
        stmt = stmt.where(ScheduleApplication.status.in_(statuses))
    return list(
        session.scalars(
            stmt.order_by(ScheduleApplication.rank_sequence, ScheduleApplication.created_at).limit(
                min(limit, 200)
            )
        )
    )


def list_applications_admin(
    session: Session,
    *,
    round_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ScheduleApplication], int]:
    """运营侧申请列表：可按档期与状态过滤，跨档期分页。"""
    conditions = []
    if round_id:
        conditions.append(ScheduleApplication.round_id == round_id)
    if status:
        conditions.append(ScheduleApplication.status == status)
    stmt = sa.select(ScheduleApplication).where(*conditions)
    total = int(
        session.scalar(
            sa.select(sa.func.count()).select_from(ScheduleApplication).where(*conditions)
        )
        or 0
    )
    rows = list(
        session.scalars(
            stmt.order_by(
                ScheduleApplication.round_id, ScheduleApplication.rank_sequence,
                ScheduleApplication.created_at,
            )
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, total


# ------------------------------------------------------------------ 名额预留
def get_reservation(session: Session, reservation_id: str) -> SlotReservation | None:
    return session.get(SlotReservation, reservation_id)


def get_reservation_for_order(session: Session, *, order_id: str) -> SlotReservation | None:
    return session.scalars(
        sa.select(SlotReservation).where(SlotReservation.order_id == order_id)
    ).one_or_none()


def get_reservation_for_user(
    session: Session, *, reservation_id: str, user_id: str
) -> SlotReservation | None:
    return session.scalars(
        sa.select(SlotReservation).where(
            SlotReservation.id == reservation_id, SlotReservation.user_id == user_id
        )
    ).one_or_none()


def due_reservations(session: Session, *, moment: int, limit: int = 50) -> list[SlotReservation]:
    return list(
        session.scalars(
            sa.select(SlotReservation)
            .where(SlotReservation.state == "RESERVED", SlotReservation.expires_at <= moment)
            .order_by(SlotReservation.expires_at)
            .limit(min(limit, 200))
        )
    )


# ------------------------------------------------------------------ 制作单
def get_project(session: Session, project_id: str) -> CommissionProject | None:
    return session.get(CommissionProject, project_id)


def get_project_for_user(session: Session, *, project_id: str, user_id: str) -> CommissionProject | None:
    return session.scalars(
        sa.select(CommissionProject).where(
            CommissionProject.id == project_id, CommissionProject.user_id == user_id
        )
    ).one_or_none()


def get_project_for_maker(
    session: Session, *, project_id: str, maker_id: str
) -> CommissionProject | None:
    """分配装师路径：只返回分配给该员工的制作单。"""
    return session.scalars(
        sa.select(CommissionProject).where(
            CommissionProject.id == project_id, CommissionProject.maker_id == maker_id
        )
    ).one_or_none()


def get_project_for_order(session: Session, *, order_id: str) -> CommissionProject | None:
    return session.scalars(
        sa.select(CommissionProject).where(CommissionProject.order_id == order_id)
    ).one_or_none()


def list_projects_for_user(
    session: Session, *, user_id: str, status: str | None = None, limit: int = 20, offset: int = 0
) -> list[CommissionProject]:
    stmt = sa.select(CommissionProject).where(CommissionProject.user_id == user_id)
    if status:
        stmt = stmt.where(CommissionProject.status == status)
    return list(
        session.scalars(
            stmt.order_by(CommissionProject.created_at.desc()).limit(min(limit, 100)).offset(offset)
        )
    )


def count_projects_for_user(session: Session, *, user_id: str, status: str | None = None) -> int:
    stmt = (
        sa.select(sa.func.count())
        .select_from(CommissionProject)
        .where(CommissionProject.user_id == user_id)
    )
    if status:
        stmt = stmt.where(CommissionProject.status == status)
    return int(session.scalar(stmt) or 0)


def list_projects_admin(
    session: Session,
    *,
    status: str | None = None,
    maker_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[CommissionProject], int]:
    """运营/装师制作单列表。

    ``maker_id`` 是数据范围约束而不是普通筛选：装师只能看见分配给自己的单，
    由路由层强制写入 principal.id（BUG-01）。
    """
    conditions = []
    if status:
        conditions.append(CommissionProject.status == status)
    if maker_id:
        conditions.append(CommissionProject.maker_id == maker_id)
    stmt = sa.select(CommissionProject).where(*conditions)
    total = int(
        session.scalar(
            sa.select(sa.func.count()).select_from(CommissionProject).where(*conditions)
        )
        or 0
    )
    rows = list(
        session.scalars(
            stmt.order_by(CommissionProject.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, total


def active_project_for_inquiry(session: Session, *, inquiry_id: str) -> CommissionProject | None:
    return session.scalars(
        sa.select(CommissionProject)
        .where(CommissionProject.inquiry_id == inquiry_id)
        .order_by(CommissionProject.created_at.desc())
        .limit(1)
    ).one_or_none()


# ------------------------------------------------------------------ 量体
def list_measurements(session: Session, *, project_id: str, limit: int = 20) -> list[Measurement]:
    return list(
        session.scalars(
            sa.select(Measurement)
            .where(Measurement.project_id == project_id)
            .order_by(Measurement.version.desc())
            .limit(min(limit, 50))
        )
    )


def max_measurement_version(session: Session, *, project_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(Measurement.version), 0)).where(
                Measurement.project_id == project_id
            )
        )
        or 0
    )


def get_measurement(session: Session, *, measurement_id: str, project_id: str) -> Measurement | None:
    return session.scalars(
        sa.select(Measurement).where(
            Measurement.id == measurement_id, Measurement.project_id == project_id
        )
    ).one_or_none()


# ------------------------------------------------------------------ 制作阶段
def get_stage(session: Session, stage_id: str) -> ProductionStage | None:
    return session.get(ProductionStage, stage_id)


def get_stage_for_user(session: Session, *, stage_id: str, user_id: str) -> ProductionStage | None:
    """客户路径：阶段必须属于本人制作单。"""
    return session.scalars(
        sa.select(ProductionStage)
        .join(CommissionProject, CommissionProject.id == ProductionStage.project_id)
        .where(ProductionStage.id == stage_id, CommissionProject.user_id == user_id)
    ).one_or_none()


def get_stage_for_maker(session: Session, *, stage_id: str, maker_id: str) -> ProductionStage | None:
    return session.scalars(
        sa.select(ProductionStage)
        .join(CommissionProject, CommissionProject.id == ProductionStage.project_id)
        .where(ProductionStage.id == stage_id, CommissionProject.maker_id == maker_id)
    ).one_or_none()


def list_stages(session: Session, *, project_id: str, limit: int = 50) -> list[ProductionStage]:
    return list(
        session.scalars(
            sa.select(ProductionStage)
            .where(ProductionStage.project_id == project_id)
            .order_by(ProductionStage.created_at, ProductionStage.stage_code)
            .limit(min(limit, 200))
        )
    )


def latest_stage_for_code(
    session: Session, *, project_id: str, stage_code: str
) -> ProductionStage | None:
    return session.scalars(
        sa.select(ProductionStage)
        .where(ProductionStage.project_id == project_id, ProductionStage.stage_code == stage_code)
        .order_by(ProductionStage.revision.desc())
        .limit(1)
    ).one_or_none()


def current_stage(session: Session, *, project_id: str) -> ProductionStage | None:
    return session.scalars(
        sa.select(ProductionStage)
        .where(ProductionStage.project_id == project_id)
        .order_by(ProductionStage.created_at.desc(), ProductionStage.revision.desc())
        .limit(1)
    ).one_or_none()


def count_stages_awaiting_confirmation(session: Session, *, user_id: str) -> int:
    """待客户确认的制作阶段数量（账户待办提示）。"""
    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(ProductionStage)
            .join(CommissionProject, CommissionProject.id == ProductionStage.project_id)
            .where(
                CommissionProject.user_id == user_id,
                CommissionProject.status.not_in(("COMPLETED", "CANCELLED")),
                ProductionStage.needs_customer_confirmation == 1,
                ProductionStage.confirmed_revision.is_(None),
            )
        )
        or 0
    )


# ------------------------------------------------------------------ 变更单
def get_change(
    session: Session, *, change_request_id: str, project_id: str
) -> ChangeRequest | None:
    return session.scalars(
        sa.select(ChangeRequest).where(
            ChangeRequest.id == change_request_id, ChangeRequest.project_id == project_id
        )
    ).one_or_none()


def get_change_any_project(session: Session, *, change_request_id: str) -> ChangeRequest | None:
    """按变更单 ID 读取（不限定项目）。调用方必须再校验项目归属。"""
    return session.get(ChangeRequest, change_request_id)


def list_changes(session: Session, *, project_id: str, limit: int = 20) -> list[ChangeRequest]:
    return list(
        session.scalars(
            sa.select(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
            .order_by(ChangeRequest.version.desc())
            .limit(min(limit, 50))
        )
    )


def max_change_version(session: Session, *, project_id: str) -> int:
    return int(
        session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ChangeRequest.version), 0)).where(
                ChangeRequest.project_id == project_id
            )
        )
        or 0
    )


def open_change_requests(session: Session, *, project_id: str) -> list[ChangeRequest]:
    return list(
        session.scalars(
            sa.select(ChangeRequest)
            .where(ChangeRequest.project_id == project_id, ChangeRequest.status == "SUBMITTED")
            .order_by(ChangeRequest.version)
        )
    )
