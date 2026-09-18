"""委托模块的 HTTP 输入输出 DTO 与稳定枚举。

枚举由后端定义，前端从 OpenAPI 生成的类型读取，禁止手抄多份（06 文档）。
输入继承 ``StrictDto``（拒绝多余字段，杜绝前端传入 user_id、金额等可信输入）；
列表统一使用 ``Page[T]``（items、next_cursor、has_more）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.modules.shared.schemas import Dto, StrictDto, VersionedInput

SuitType = Literal["full", "partial", "head", "other"]
RoundMode = Literal["application", "rush"]
MeasurementUnit = Literal["metric", "imperial"]
ChangeScope = Literal["addition", "reduction", "schedule", "material", "other"]

ShortText = Annotated[str, Field(min_length=1, max_length=60)]
IdField = Annotated[str, Field(min_length=4, max_length=48)]


# ------------------------------------------------------------------ 询价输入
class InquiryCreate(StrictDto):
    """询价表单（07 文档）。量体信息不在首次询价收集。"""

    character_name: ShortText
    species: ShortText
    series_id: IdField
    suit_type: SuitType = "full"
    design_asset_ids: list[str] = Field(default_factory=list, max_length=3)
    requirements: str = Field(default="", max_length=3000)
    budget_min_cents: int | None = Field(default=None, ge=0)
    budget_max_cents: int | None = Field(default=None, ge=0)
    target_date: str | None = Field(default=None, max_length=20)
    delivery_region: str | None = Field(default=None, max_length=40)
    contact_preference: str | None = Field(default=None, max_length=80)
    rights_declaration: bool = False
    guardian_required: bool = False
    # C28「服务先申请再报价」：引用本人已通过审批的服务申请；其他类型或未通过的申请由服务端拒绝。
    application_id: str | None = Field(default=None, max_length=48)
    # 草稿重放时前端可能带上已保存的草稿版本号；创建时忽略，编辑走 PATCH。
    version: int | None = Field(default=None, ge=1)


class InquiryPatch(VersionedInput):
    """允许版本的编辑：只允许草稿与补件阶段，终态拒绝。"""

    character_name: ShortText | None = None
    species: ShortText | None = None
    series_id: IdField | None = None
    suit_type: SuitType | None = None
    design_asset_ids: list[str] | None = Field(default=None, max_length=3)
    requirements: str | None = Field(default=None, max_length=3000)
    budget_min_cents: int | None = Field(default=None, ge=0)
    budget_max_cents: int | None = Field(default=None, ge=0)
    target_date: str | None = Field(default=None, max_length=20)
    delivery_region: str | None = Field(default=None, max_length=40)
    contact_preference: str | None = Field(default=None, max_length=80)
    rights_declaration: bool | None = None
    guardian_required: bool | None = None


class InquirySupplementInput(VersionedInput):
    """补件：产生新 ``inquiry_revisions`` 记录；报价后补件需要重新评估。"""

    requirements: str | None = Field(default=None, max_length=3000)
    design_asset_ids: list[str] | None = Field(default=None, max_length=3)
    note: str | None = Field(default=None, max_length=300)
    request_reevaluation: bool = False


# ------------------------------------------------------------------ 报价输入
class QuoteAcceptInput(StrictDto):
    """接受报价：必须显式同意条款，且只能接受自己看到的当前版本。"""

    quote_version: int = Field(ge=1)
    terms_version: str = Field(min_length=1, max_length=40)
    accepted: bool = False


# ------------------------------------------------------------------ 档期输入
class ScheduleApplicationInput(StrictDto):
    inquiry_id: IdField
    note: str | None = Field(default=None, max_length=300)


class ScheduleRoundInput(StrictDto):
    """员工创建档期（``/admin/schedule-rounds`` 调用 service）。"""

    title: str = Field(min_length=1, max_length=120)
    mode: RoundMode = "application"
    start_at: str
    end_at: str
    opens_at: str | None = None
    closes_at: str | None = None
    capacity: int = Field(default=0, ge=0, le=200)
    stage_label: str = Field(default="", max_length=60)
    timezone: str = Field(default="Asia/Shanghai", max_length=40)
    status: Literal["DRAFT", "OPEN", "CLOSED"] = "OPEN"
    note: str | None = Field(default=None, max_length=300)


# ------------------------------------------------------------------ 制作输入
class MeasurementInput(StrictDto):
    """量体资料（私密）。数值只做异常提示，不据此拒绝用户。"""

    payload: dict[str, float] = Field(min_length=1, max_length=40)
    unit_system: MeasurementUnit = "metric"
    note: str | None = Field(default=None, max_length=300)


class StageConfirmInput(StrictDto):
    """客户确认制作阶段，绑定其看到的 revision。"""

    revision: int = Field(ge=1)
    version: int | None = Field(default=None, ge=1)
    accepted: bool = True
    note: str | None = Field(default=None, max_length=300)


class StageChangeInput(StrictDto):
    """提出修改，或接受工作人员提出的变更单（重大加项生成补款应付项）。"""

    revision: int = Field(ge=1)
    version: int | None = Field(default=None, ge=1)
    change_request_id: IdField | None = None
    accept: bool = False
    scope: ChangeScope | None = None
    description: str = Field(default="", max_length=1000)
    media_asset_ids: list[str] = Field(default_factory=list, max_length=3)


class ReassessmentInput(StrictDto):
    """客户要求重新评估：``QUOTED → SUBMITTED``（07 文档状态机）。"""

    version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=300)


class ChangeDecisionInput(StrictDto):
    """客户对变更单的决定。接受会生成单独补款应付项；拒绝不改动已发生的账务。"""

    accepted: bool
    version: int | None = Field(default=None, ge=1)


class QuotePublishInput(StrictDto):
    """员工发布报价（``/admin`` 调用 service）。金额由服务端按明细重算。"""

    line_items: list[QuoteLineItemInput] = Field(min_length=1, max_length=30)
    currency: Literal["CNY", "USD"] = "CNY"
    prepayment_cents: int | None = Field(default=None, ge=0)
    expires_at: str | None = None
    # 有效期天数：与 ``expires_at`` 二选一。由服务端按当前时间换算，前端不提供绝对时间。
    valid_days: int | None = Field(default=None, ge=1, le=180)
    estimated_start: str | None = None
    estimated_delivery: str | None = None
    included_revisions: int | None = Field(default=None, ge=0)
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    terms_version: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class QuoteLineItemInput(StrictDto):
    label: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=99)
    unit_price_cents: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=200)


# ------------------------------------------------------------------ 输出
class InquiryRevisionItem(Dto):
    revision: int
    author_type: str
    change_note: str | None = None
    submitted_at: str | None = None
    created_at: str


class InquirySummary(Dto):
    id: str
    status: str
    character_name: str
    species: str
    series_id: str
    suit_type: str
    current_revision: int
    version: int
    guardian_required: bool = False
    submitted_at: str | None = None
    created_at: str
    updated_at: str


class InquiryDetail(InquirySummary):
    requirements: str
    design_asset_ids: list[str]
    budget_min_cents: int | None = None
    budget_max_cents: int | None = None
    target_date: str | None = None
    delivery_region: str | None = None
    contact_preference: str | None = None
    rights_declaration: bool
    needs_info_note: str | None = None
    reject_reason: str | None = None
    accepted_quote_id: str | None = None
    accepted_at: str | None = None
    withdrawn_at: str | None = None
    # C28：本询价引用的服务申请编号（未引用申请时为 None）。
    application_id: str | None = None
    # C30 首响时效（详情接口返回）：承诺工作日数、截止时间与是否已逾期。
    sla_workdays: int = 0
    response_due_at: str | None = None
    first_responded_at: str | None = None
    response_overdue: bool = False
    revisions: list[InquiryRevisionItem] = Field(default_factory=list)
    current_quote_version: int | None = None
    project_id: str | None = None


class QuoteLineItemOut(Dto):
    label: str
    quantity: int
    unit_price_cents: int
    amount_cents: int
    note: str | None = None


class QuoteOut(Dto):
    """报价快照。最终可支付金额以该服务端版本为准，前端估价只用于理解区间。"""

    id: str
    inquiry_id: str
    quote_version: int
    status: str
    currency: str
    line_items: list[QuoteLineItemOut]
    total_cents: int
    prepayment_cents: int
    balance_cents: int
    expires_at: str
    estimated_start: str | None = None
    estimated_delivery: str | None = None
    included_revisions: int
    exclusions: list[str]
    terms_version: str
    issuer_id: str | None = None
    note: str | None = None
    published_at: str | None = None
    accepted_at: str | None = None
    order_id: str | None = None
    is_current: bool = False
    is_expired: bool = False


class QuoteAcceptResponse(Dto):
    """接受报价结果（06 文档）。重复请求返回同一订单与同一应付项。"""

    order_id: str
    order_no: str
    status: str
    installment_id: str | None = None
    amount_cents: int
    prepayment_cents: int
    currency: str
    expires_at: str | None = None
    quote_id: str
    quote_version: int
    project_id: str
    slot_reservation_id: str | None = None


class ScheduleRoundPublic(Dto):
    """公开档期：只展示匿名余量摘要、时区、开始与截止，不含申请人任何身份信息。"""

    id: str
    title: str
    mode: str
    status: str
    stage_label: str
    timezone: str
    start_at: str | None = None
    end_at: str | None = None
    opens_at: str | None = None
    closes_at: str | None = None
    accepting: bool
    remaining_bucket: str


class ScheduleApplicationOut(Dto):
    id: str
    round_id: str
    inquiry_id: str
    status: str
    rank_sequence: int | None = None
    note: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    created_at: str


class MeasurementOut(Dto):
    id: str
    project_id: str
    version: int
    unit_system: str
    note: str | None = None
    flagged_fields: list[str] = Field(default_factory=list)
    measurements: dict[str, float] | None = None
    created_at: str


class ProductionStageOut(Dto):
    id: str
    project_id: str
    stage_code: str
    revision: int
    status: str
    title: str
    description: str
    owner_id: str | None = None
    started_at: str | None = None
    estimated_at: str | None = None
    finished_at: str | None = None
    needs_customer_confirmation: bool
    confirmed_revision: int | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    media_asset_ids: list[str] = Field(default_factory=list)
    delay_reason: str | None = None
    version: int
    created_at: str
    updated_at: str


class ChangeRequestOut(Dto):
    id: str
    project_id: str
    version: int
    scope: str
    description: str
    reason: str | None = None
    delta_cents: int
    new_total_cents: int | None = None
    currency: str
    schedule_delta_days: int
    terms_version: str
    status: str
    installment_id: str | None = None
    decided_at: str | None = None
    accepted_at: str | None = None
    created_at: str


class CommissionProjectOut(Dto):
    id: str
    order_id: str
    inquiry_id: str
    quote_id: str
    status: str
    current_stage_code: str | None = None
    currency: str
    total_cents: int
    prepayment_cents: int
    terms_version: str
    maker_id: str | None = None
    estimated_start: str | None = None
    estimated_delivery: str | None = None
    balance_due_at: str | None = None
    started_at: str | None = None
    shipped_at: str | None = None
    completed_at: str | None = None
    version: int
    created_at: str
    updated_at: str
    stages: list[ProductionStageOut] = Field(default_factory=list)
    measurements: list[MeasurementOut] = Field(default_factory=list)
    change_requests: list[ChangeRequestOut] = Field(default_factory=list)


class InquirySubmitResponse(Dto):
    """提交询价的响应（06 文档：`{inquiry_id,status,version,submitted_at}`）。

    不能直接返回 ``InquiryDetail``：那会让前端拿不到 ``inquiry_id``，
    提交成功后紧接着的详情回读会打到 ``/inquiries/undefined`` 并得到 404。
    """

    inquiry_id: str
    status: str
    version: int
    submitted_at: str | None = None


QuotePublishInput.model_rebuild()

__all__ = [
    "ChangeRequestOut",
    "CommissionProjectOut",
    "InquiryCreate",
    "InquiryDetail",
    "InquiryPatch",
    "InquiryRevisionItem",
    "InquirySubmitResponse",
    "InquirySummary",
    "InquirySupplementInput",
    "MeasurementInput",
    "MeasurementOut",
    "ProductionStageOut",
    "QuoteAcceptInput",
    "QuoteAcceptResponse",
    "QuoteLineItemInput",
    "QuoteLineItemOut",
    "QuoteOut",
    "QuotePublishInput",
    "ScheduleApplicationInput",
    "ScheduleApplicationOut",
    "ScheduleRoundInput",
    "ScheduleRoundPublic",
    "StageChangeInput",
    "StageConfirmInput",
]
