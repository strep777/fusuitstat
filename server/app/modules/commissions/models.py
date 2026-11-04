"""委托模块：询价、报价、档期名额、制作单、量体、制作阶段与变更单。

拥有的数据（05 文档「询价与制作」）：inquiries、inquiry_revisions、quotes、
quote_items、schedule_rounds、schedule_applications、slot_reservations、
commission_projects、measurements、production_stages、change_requests。

不变量：
1. ``quotes`` 唯一 ``(inquiry_id, quote_version)``，``prepayment_cents <= total_cents``；
2. ``schedule_rounds`` 的 ``reserved_count + committed_count <= capacity`` 由 CHECK
   保证，写路径用条件 UPDATE 实现原子占用，不使用 ``SELECT FOR UPDATE``；
3. ``schedule_applications`` 唯一 ``(round_id, rank_sequence)``，同一轮同一询价只能
   申请一次（C11：每人每轮最多 ``MAX_APPLICATIONS_PER_ROUND`` 条）；
4. ``measurements`` 唯一 ``(project_id, version)``，正文加密存储；
5. ``production_stages`` 唯一 ``(project_id, stage_code, revision)``，客户确认绑定
   revision；``change_requests`` 唯一 ``(project_id, version)``，已接受版本不可覆盖。

订单、付款项与积分不属于本模块：这里只保存 ``order_id`` / ``installment_id`` 引用，
金额与结算状态以商务模块为准。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id, now_ms
from app.platform.orm import Base, PkMixin, TimestampMixin, VersionMixin

# ------------------------------------------------------------------ 枚举（TEXT + CHECK）
INQUIRY_STATUSES = (
    "DRAFT",
    "SUBMITTED",
    "NEEDS_INFO",
    "QUOTED",
    "ACCEPTED",
    "REJECTED",
    "EXPIRED",
    "WITHDRAWN",
)
TERMINAL_INQUIRY_STATUSES = ("ACCEPTED", "REJECTED", "WITHDRAWN")
SUIT_TYPES = ("full", "partial", "head", "other")
ESTIMATE_STATUSES = ("NEW", "CONTACTED", "CLOSED")
ESTIMATE_CONTACT_TYPES = ("email", "phone", "wechat")
ESTIMATE_STYLES = ("standard", "fitted", "special")
QUOTE_STATUSES = ("PUBLISHED", "SUPERSEDED", "ACCEPTED", "EXPIRED", "WITHDRAWN")
ROUND_MODES = ("application", "rush")
ROUND_STATUSES = ("DRAFT", "OPEN", "CLOSED")
APPLICATION_STATUSES = ("SUBMITTED", "SHORTLISTED", "SELECTED", "REJECTED", "WITHDRAWN", "EXPIRED")
SLOT_RESERVATION_STATES = ("RESERVED", "COMMITTED", "RELEASED", "EXPIRED", "CLOSING")
PROJECT_STATUSES = (
    "QUEUED",
    "MEASUREMENT_PENDING",
    "DESIGN_CONFIRMING",
    "MATERIALS_READY",
    "PRODUCING",
    "CUSTOMER_REVIEW",
    "READY_TO_SHIP",
    "SHIPPED",
    "COMPLETED",
    "CANCELLED",
)
# 制作流程基线（07 文档）：只能按顺序推进，不能跳步，更不能跳过尾款校验发货。
PROJECT_FLOW = (
    "QUEUED",
    "MEASUREMENT_PENDING",
    "DESIGN_CONFIRMING",
    "MATERIALS_READY",
    "PRODUCING",
    "CUSTOMER_REVIEW",
    "READY_TO_SHIP",
    "SHIPPED",
    "COMPLETED",
)
STAGE_STATUSES = (
    "PENDING",
    "IN_PROGRESS",
    "AWAITING_CUSTOMER",
    "CONFIRMED",
    "CHANGE_REQUESTED",
    "DELAYED",
    "DONE",
)
CHANGE_SCOPES = ("addition", "reduction", "schedule", "material", "other")
CHANGE_STATUSES = ("SUBMITTED", "ACCEPTED", "REJECTED", "WITHDRAWN", "EXPIRED")
MEASUREMENT_UNITS = ("metric", "imperial")

# C11：启用限时申请时，每人每轮最多提交 3 条申请（一人可多个角色）。
MAX_APPLICATIONS_PER_ROUND = 3

# 量体字段范围只用于异常提示，绝不据此拒绝用户（07 文档：禁止硬编码排除特殊体型）。
MEASUREMENT_SOFT_MIN_CM = 100.0
MEASUREMENT_SOFT_MAX_CM = 250.0
MAX_MEASUREMENT_FIELDS = 40


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


# ------------------------------------------------------------------ 询价
class Inquiry(PkMixin, TimestampMixin, VersionMixin, Base):
    """询价：需求沟通记录。提交后不覆盖工作人员已见版本，补件产生新 revision。"""

    __tablename__ = "inquiries"
    id_kind = "inquiry"

    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="DRAFT")
    # 员工分派：报价与补件的责任人。staff_accounts 属身份模块，这里只保存人员 ID 引用。
    assignee_id: Mapped[str | None] = mapped_column(sa.String(48), index=True)
    series_id: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    character_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    species: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="")
    suit_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="full")
    requirements: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    design_asset_ids: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    budget_min_cents: Mapped[int | None] = mapped_column(sa.Integer)
    budget_max_cents: Mapped[int | None] = mapped_column(sa.Integer)
    target_date: Mapped[str | None] = mapped_column(sa.String(20))
    delivery_region: Mapped[str | None] = mapped_column(sa.String(40))
    contact_preference: Mapped[str | None] = mapped_column(sa.String(80))
    rights_declaration: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # C19：未成年付费委托需监护人同意与额外签署的监护人条款。
    guardian_required: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # 监护人联系信息：合同签署需要监护人作为第二个签署人，缺失时拒绝发起签署而不是猜。
    guardian_name: Mapped[str | None] = mapped_column(sa.String(60))
    guardian_email: Mapped[str | None] = mapped_column(sa.String(160))
    guardian_relation: Mapped[str | None] = mapped_column(sa.String(20))
    current_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # 工作人员已读到的版本：客户其后修改只追加版本，不覆盖已见内容。
    staff_seen_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    needs_info_note: Mapped[str | None] = mapped_column(sa.String(500))
    reject_reason: Mapped[str | None] = mapped_column(sa.String(400))
    accepted_quote_id: Mapped[str | None] = mapped_column(sa.String(48))
    # C28「服务先申请再报价」：服务类申请通过后，询价带上该申请；
    # 一个申请只能用于一份询价（唯一索引），避免同一份审批被反复用来报价。
    application_id: Mapped[str | None] = mapped_column(sa.String(48))
    submitted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    # C30：工作人员**首次响应**时间（发布报价、要求补件或拒绝时写入一次）。
    first_responded_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    accepted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    rejected_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    withdrawn_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.CheckConstraint(_enum_check("status", INQUIRY_STATUSES), name="ck_inquiries_status"),
        sa.CheckConstraint(_enum_check("suit_type", SUIT_TYPES), name="ck_inquiries_suit_type"),
        sa.CheckConstraint("rights_declaration IN (0,1)", name="ck_inquiries_rights"),
        sa.CheckConstraint("guardian_required IN (0,1)", name="ck_inquiries_guardian"),
        sa.CheckConstraint(
            "budget_min_cents IS NULL OR budget_min_cents >= 0", name="ck_inquiries_budget_min"
        ),
        sa.CheckConstraint(
            "budget_max_cents IS NULL OR budget_max_cents >= 0", name="ck_inquiries_budget_max"
        ),
        sa.CheckConstraint(
            "budget_min_cents IS NULL OR budget_max_cents IS NULL "
            "OR budget_min_cents <= budget_max_cents",
            name="ck_inquiries_budget_order",
        ),
        sa.CheckConstraint("current_revision >= 0", name="ck_inquiries_revision_nonneg"),
        sa.Index("ix_inquiries_user_status", "user_id", "status", "created_at"),
        sa.Index("uq_inquiries_application", "application_id", unique=True),
    )


class InquiryRevision(PkMixin, Base):
    """询价版本快照：客户每次提交/补件追加一条，不覆盖历史。"""

    __tablename__ = "inquiry_revisions"

    inquiry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payload_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    author_id: Mapped[str | None] = mapped_column(sa.String(48))
    author_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="customer")
    change_note: Mapped[str | None] = mapped_column(sa.String(300))
    submitted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.UniqueConstraint("inquiry_id", "revision", name="uq_inquiry_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_inquiry_revision_positive"),
        sa.CheckConstraint(
            "author_type IN ('customer','staff','system')", name="ck_inquiry_revision_author"
        ),
    )


# ------------------------------------------------------------------ 报价
class Quote(PkMixin, TimestampMixin, VersionMixin, Base):
    """报价版本：有期限的商业提案。发布新版本把旧版本置为 SUPERSEDED。"""

    __tablename__ = "quotes"
    id_kind = "quote"

    inquiry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="PUBLISHED")
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    prepayment_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    balance_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    estimated_start: Mapped[int | None] = mapped_column(sa.BigInteger)
    estimated_delivery: Mapped[int | None] = mapped_column(sa.BigInteger)
    included_revisions: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    exclusions: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    issuer_id: Mapped[str | None] = mapped_column(sa.String(48))
    note: Mapped[str | None] = mapped_column(sa.String(500))
    order_id: Mapped[str | None] = mapped_column(sa.String(48))
    # 接受时写下的订单与款项快照（07 文档）：重复请求据此返回同一订单，
    # 后续改价或条款更新也不影响已接受版本的金额与同意证据。
    contract_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    published_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)
    superseded_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    accepted_at: Mapped[int | None] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.UniqueConstraint("inquiry_id", "quote_version", name="uq_quote_version"),
        sa.CheckConstraint(_enum_check("status", QUOTE_STATUSES), name="ck_quotes_status"),
        sa.CheckConstraint("quote_version >= 1", name="ck_quotes_version_positive"),
        sa.CheckConstraint("total_cents >= 0", name="ck_quotes_total_nonneg"),
        sa.CheckConstraint("prepayment_cents >= 0", name="ck_quotes_prepayment_nonneg"),
        sa.CheckConstraint("balance_cents >= 0", name="ck_quotes_balance_nonneg"),
        sa.CheckConstraint("prepayment_cents <= total_cents", name="ck_quotes_prepayment_le_total"),
        sa.CheckConstraint(
            "balance_cents = total_cents - prepayment_cents", name="ck_quotes_balance_consistent"
        ),
        sa.CheckConstraint("included_revisions >= 0", name="ck_quotes_revisions_nonneg"),
        sa.Index("ix_quotes_inquiry_status", "inquiry_id", "status"),
    )


class QuoteItem(PkMixin, Base):
    """报价明细行。总额由服务端按明细重算，前端估价只用于理解区间。"""

    __tablename__ = "quote_items"

    quote_id: Mapped[str] = mapped_column(
        sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    unit_price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=now_ms)

    __table_args__ = (
        sa.CheckConstraint("quantity >= 1", name="ck_quote_items_quantity"),
        sa.CheckConstraint("unit_price_cents >= 0", name="ck_quote_items_unit_price"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_quote_items_amount"),
        sa.CheckConstraint("amount_cents = quantity * unit_price_cents", name="ck_quote_items_total"),
    )


# ------------------------------------------------------------------ 档期与名额
class ScheduleRound(PkMixin, TimestampMixin, VersionMixin, Base):
    """档期轮次。容量占用用条件 UPDATE 维护，CHECK 保证不超卖。"""

    __tablename__ = "schedule_rounds"
    id_kind = "round"

    title: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    mode: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="application")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="OPEN")
    stage_label: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="Asia/Shanghai")
    start_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    end_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    opens_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    closes_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    capacity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    reserved_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    committed_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.CheckConstraint("start_at < end_at", name="ck_rounds_window"),
        sa.CheckConstraint(
            "opens_at IS NULL OR closes_at IS NULL OR opens_at < closes_at", name="ck_rounds_apply_window"
        ),
        sa.CheckConstraint("capacity >= 0", name="ck_rounds_capacity_nonneg"),
        sa.CheckConstraint("reserved_count >= 0", name="ck_rounds_reserved_nonneg"),
        sa.CheckConstraint("committed_count >= 0", name="ck_rounds_committed_nonneg"),
        sa.CheckConstraint(
            "reserved_count + committed_count <= capacity", name="ck_rounds_within_capacity"
        ),
        sa.CheckConstraint(_enum_check("mode", ROUND_MODES), name="ck_rounds_mode"),
        sa.CheckConstraint(_enum_check("status", ROUND_STATUSES), name="ck_rounds_status"),
        sa.Index("ix_rounds_public", "status", "start_at"),
    )


class ScheduleApplication(PkMixin, TimestampMixin, VersionMixin, Base):
    """档期申请。普通申请先人工筛选，限时申请（C11）按服务端分配序号排位。

    05 文档的 ``UNIQUE(round_id,user_id)`` 按 C11 放宽为
    ``UNIQUE(round_id,user_id,inquiry_id)``：同一轮同一询价只能申请一次，
    一人可凭不同询价最多申请 ``MAX_APPLICATIONS_PER_ROUND`` 条。
    """

    __tablename__ = "schedule_applications"
    id_kind = "application"

    round_id: Mapped[str] = mapped_column(
        sa.ForeignKey("schedule_rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inquiry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank_sequence: Mapped[int | None] = mapped_column(sa.Integer)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="SUBMITTED")
    note: Mapped[str | None] = mapped_column(sa.String(300))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(120))
    decided_by: Mapped[str | None] = mapped_column(sa.String(48))
    decided_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    decision_reason: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.UniqueConstraint("round_id", "user_id", "inquiry_id", name="uq_schedule_application"),
        sa.UniqueConstraint("round_id", "rank_sequence", name="uq_schedule_application_rank"),
        sa.CheckConstraint(_enum_check("status", APPLICATION_STATUSES), name="ck_applications_status"),
        sa.CheckConstraint(
            "rank_sequence IS NULL OR rank_sequence >= 1", name="ck_applications_rank_positive"
        ),
        sa.Index("ix_applications_round_status", "round_id", "status"),
    )


class SlotReservation(PkMixin, TimestampMixin, VersionMixin, Base):
    """名额预留：接受报价时原子占用，已付预付款才转正式占用（COMMITTED）。

    截止前未完成收款进入关闭核对（CLOSING），核对无在途付款后再释放。
    """

    __tablename__ = "slot_reservations"
    id_kind = "slot"

    round_id: Mapped[str] = mapped_column(
        sa.ForeignKey("schedule_rounds.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # 订单号由商务模块建单后在同一事务内回填；唯一约束保证一单一预留。
    order_id: Mapped[str | None] = mapped_column(sa.String(48), unique=True)
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inquiry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[str] = mapped_column(sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("schedule_applications.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="RESERVED")
    hold_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    expires_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    committed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    released_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    release_reason: Mapped[str | None] = mapped_column(sa.String(200))

    __table_args__ = (
        sa.CheckConstraint(
            _enum_check("state", SLOT_RESERVATION_STATES), name="ck_slot_reservations_state"
        ),
        sa.CheckConstraint("hold_minutes >= 0", name="ck_slot_reservations_hold"),
        sa.Index("ix_slot_reservations_due", "state", "expires_at"),
    )


# ------------------------------------------------------------------ 制作
class CommissionProject(PkMixin, TimestampMixin, VersionMixin, Base):
    """制作单。状态按 07 文档基线顺序推进，尾款未结清不能发货。"""

    __tablename__ = "commission_projects"
    id_kind = "project"

    order_id: Mapped[str] = mapped_column(sa.String(48), nullable=False, unique=True)
    inquiry_id: Mapped[str] = mapped_column(
        sa.ForeignKey("inquiries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quote_id: Mapped[str] = mapped_column(sa.ForeignKey("quotes.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # staff_accounts 属身份模块，这里只保存人员 ID 引用，不做跨模块外键。
    maker_id: Mapped[str | None] = mapped_column(sa.String(48))
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="QUEUED")
    current_stage_code: Mapped[str | None] = mapped_column(sa.String(24))
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    prepayment_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    estimated_start: Mapped[int | None] = mapped_column(sa.BigInteger)
    estimated_delivery: Mapped[int | None] = mapped_column(sa.BigInteger)
    balance_due_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    started_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    shipped_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    completed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    cancelled_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    cancel_reason: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.CheckConstraint(_enum_check("status", PROJECT_STATUSES), name="ck_projects_status"),
        sa.CheckConstraint("total_cents >= 0", name="ck_projects_total_nonneg"),
        sa.CheckConstraint("prepayment_cents >= 0", name="ck_projects_prepayment_nonneg"),
        sa.CheckConstraint("prepayment_cents <= total_cents", name="ck_projects_prepayment_le_total"),
        sa.Index("ix_projects_user_status", "user_id", "status"),
    )


class Measurement(PkMixin, TimestampMixin, Base):
    """量体资料：正文加密，按版本追加。仅本人与分配装师可读（07 文档）。"""

    __tablename__ = "measurements"
    id_kind = "measurement"

    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("commission_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    encrypted_payload: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit_system: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="metric")
    note: Mapped[str | None] = mapped_column(sa.String(300))
    flagged_fields: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    author_id: Mapped[str | None] = mapped_column(sa.String(48))
    consent_id: Mapped[str | None] = mapped_column(sa.String(48))

    __table_args__ = (
        sa.UniqueConstraint("project_id", "version", name="uq_measurement_version"),
        sa.CheckConstraint("version >= 1", name="ck_measurements_version_positive"),
        sa.CheckConstraint(_enum_check("unit_system", MEASUREMENT_UNITS), name="ck_measurements_units"),
    )


class ProductionStage(PkMixin, TimestampMixin, VersionMixin, Base):
    """制作阶段。客户确认绑定 revision：装师更新内容后 revision 递增，旧确认失效。"""

    __tablename__ = "production_stages"
    id_kind = "stage"

    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("commission_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_code: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING")
    title: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    owner_id: Mapped[str | None] = mapped_column(sa.String(48))
    started_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    estimated_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    finished_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    needs_customer_confirmation: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(sa.Integer)
    approved_by: Mapped[str | None] = mapped_column(sa.String(48))
    approved_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    media_asset_ids: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    delay_reason: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.UniqueConstraint("project_id", "stage_code", "revision", name="uq_production_stage_revision"),
        sa.CheckConstraint(_enum_check("status", STAGE_STATUSES), name="ck_stages_status"),
        sa.CheckConstraint("revision >= 1", name="ck_stages_revision_positive"),
        sa.CheckConstraint(
            "needs_customer_confirmation IN (0,1)", name="ck_stages_needs_confirmation"
        ),
        sa.CheckConstraint(
            "confirmed_revision IS NULL OR confirmed_revision >= 0", name="ck_stages_confirmed_revision"
        ),
        sa.Index("ix_stages_project", "project_id", "created_at"),
    )


class ChangeRequest(PkMixin, TimestampMixin, VersionMixin, Base):
    """变更单：重大加项明确新金额、延期与条款版本；客户接受后才生成补款应付项。

    ``UNIQUE(project_id, version)`` 与状态条件更新共同保证已接受版本不可覆盖。
    installment_id 属商务模块，这里只保存引用。
    """

    __tablename__ = "change_requests"
    id_kind = "change"

    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("commission_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    scope: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="other")
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    reason: Mapped[str | None] = mapped_column(sa.String(300))
    delta_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    new_total_cents: Mapped[int | None] = mapped_column(sa.Integer)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    schedule_delta_days: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    terms_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="SUBMITTED")
    requested_by: Mapped[str | None] = mapped_column(sa.String(48))
    requested_by_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="staff")
    decided_by: Mapped[str | None] = mapped_column(sa.String(48))
    decided_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    accepted_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    installment_id: Mapped[str | None] = mapped_column(sa.String(48))
    media_asset_ids: Mapped[list[Any] | None] = mapped_column(sa.JSON)

    __table_args__ = (
        sa.UniqueConstraint("project_id", "version", name="uq_change_request_version"),
        sa.CheckConstraint(_enum_check("status", CHANGE_STATUSES), name="ck_changes_status"),
        sa.CheckConstraint(_enum_check("scope", CHANGE_SCOPES), name="ck_changes_scope"),
        sa.CheckConstraint("version >= 1", name="ck_changes_version_positive"),
        sa.CheckConstraint("delta_cents >= 0", name="ck_changes_delta_nonneg"),
        sa.CheckConstraint(
            "new_total_cents IS NULL OR new_total_cents >= 0", name="ck_changes_new_total_nonneg"
        ),
        sa.CheckConstraint(
            "requested_by_type IN ('customer','staff','system')", name="ck_changes_requested_by"
        ),
    )


class ContractEnvelope(PkMixin, TimestampMixin, VersionMixin, Base):
    """合同电子签签署任务（C07/C19）。

    一行一次签署任务：合同快照摘要 + 签署人（含监护人）+ 平台文档号 + 签署状态。
    **签署结论只认平台**（已验签回调或服务端查单），本表不根据"点了链接"推断已签。
    """

    __tablename__ = "contract_envelopes"
    id_kind = "envelope"

    quote_id: Mapped[str] = mapped_column(
        sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inquiry_id: Mapped[str | None] = mapped_column(sa.String(48), index=True)
    order_id: Mapped[str | None] = mapped_column(sa.String(48), index=True)
    project_id: Mapped[str | None] = mapped_column(sa.String(48), index=True)
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="disabled")
    #: 平台侧文档号；未发送时为空。
    provider_document_id: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING_SEND")
    subject: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    #: 合同快照摘要：签署的到底是哪一版，可核对且不随报价改版漂移。
    snapshot_digest: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="")
    signers: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    guardian_required: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    sign_url: Mapped[str | None] = mapped_column(sa.String(500))
    expires_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    sent_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    completed_at: Mapped[int | None] = mapped_column(sa.BigInteger)
    decline_reason: Mapped[str | None] = mapped_column(sa.String(300))
    void_reason: Mapped[str | None] = mapped_column(sa.String(300))
    #: 签署完成文件的留证：相对路径 + 摘要（文件本身按私密附件策略存放）。
    document_path: Mapped[str | None] = mapped_column(sa.String(300))
    document_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    #: 已验签回调的原始摘要（原文单独落盘，库里只留摘要）。
    last_event_digest: Mapped[str | None] = mapped_column(sa.String(64))
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(sa.String(300))

    __table_args__ = (
        sa.CheckConstraint(
            _enum_check(
                "status",
                (
                    "PENDING_SEND",
                    "SENT",
                    "PARTIALLY_SIGNED",
                    "SIGNED",
                    "DECLINED",
                    "EXPIRED",
                    "VOID",
                ),
            ),
            name="ck_contract_envelopes_status",
        ),
        sa.CheckConstraint("guardian_required IN (0,1)", name="ck_contract_envelopes_guardian"),
        sa.CheckConstraint("attempts >= 0", name="ck_contract_envelopes_attempts"),
        sa.Index("ix_contract_envelopes_quote_status", "quote_id", "status"),
    )


class EstimateRequest(PkMixin, TimestampMixin, VersionMixin, Base):
    """公开参考估价请求（FR-026）。

    无需登录即可提交，因此不关联 ``users``，也不进入询价状态机。联系方式加密保存，
    只用于工作室回访；提交时必须确认隐私说明与「参考价不是最终报价」。
    """

    __tablename__ = "estimate_requests"
    id_kind = "estimate"

    name: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    contact_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    contact_ciphertext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    contact_hint: Mapped[str] = mapped_column(sa.String(80), nullable=False, default="")
    series_code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    series_title: Mapped[str] = mapped_column(sa.String(80), nullable=False, default="")
    style: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="standard")
    options: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    character_name: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="")
    species: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="")
    note: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    estimate_low_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    estimate_high_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="NEW")
    consent_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    source_ip_hash: Mapped[str | None] = mapped_column(sa.String(64))

    __table_args__ = (
        sa.CheckConstraint(
            _enum_check("status", ESTIMATE_STATUSES), name="ck_estimate_requests_status"
        ),
        sa.CheckConstraint(
            _enum_check("contact_type", ESTIMATE_CONTACT_TYPES),
            name="ck_estimate_requests_contact_type",
        ),
        sa.CheckConstraint(
            _enum_check("style", ESTIMATE_STYLES), name="ck_estimate_requests_style"
        ),
        sa.CheckConstraint(
            "estimate_low_cents >= 0 AND estimate_high_cents >= estimate_low_cents",
            name="ck_estimate_requests_range",
        ),
        sa.Index("ix_estimate_requests_status_created", "status", "created_at"),
    )


__all__ = [
    "APPLICATION_STATUSES",
    "CHANGE_SCOPES",
    "CHANGE_STATUSES",
    "ESTIMATE_CONTACT_TYPES",
    "ESTIMATE_STATUSES",
    "ESTIMATE_STYLES",
    "EstimateRequest",
    "ChangeRequest",
    "CommissionProject",
    "ContractEnvelope",
    "INQUIRY_STATUSES",
    "Inquiry",
    "InquiryRevision",
    "MAX_APPLICATIONS_PER_ROUND",
    "MAX_MEASUREMENT_FIELDS",
    "MEASUREMENT_SOFT_MAX_CM",
    "MEASUREMENT_SOFT_MIN_CM",
    "MEASUREMENT_UNITS",
    "Measurement",
    "PROJECT_FLOW",
    "PROJECT_STATUSES",
    "ProductionStage",
    "QUOTE_STATUSES",
    "Quote",
    "QuoteItem",
    "ROUND_MODES",
    "ROUND_STATUSES",
    "SLOT_RESERVATION_STATES",
    "STAGE_STATUSES",
    "SUIT_TYPES",
    "ScheduleApplication",
    "ScheduleRound",
    "SlotReservation",
    "TERMINAL_INQUIRY_STATUSES",
    "new_id",
]
