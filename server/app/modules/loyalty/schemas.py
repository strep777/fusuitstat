"""积分模块的 HTTP 输入输出 DTO。

响应形状与 06 文档一致：``{posted_balance,reserved_points,available_points,pending_points,rules_version}``；
流水含 delta、业务中文描述、来源摘要、发生时间、调整关联与申诉入口。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from app.modules.shared.schemas import Dto, Page, StrictDto

PointAppealStatus = Literal["SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED"]


class PointAccountResponse(Dto):
    """积分账户视图。可用 = max(0, 账面 - 冻结)；账面可为负（退款扣回）。"""

    posted_balance: int
    reserved_points: int
    available_points: int
    pending_points: int
    rules_version: str


class PointRedemptionQuote(Dto):
    """结算积分抵扣报价（C15，默认关闭）。

    所有数值都由服务端按运营配置裁定：``applied_cents`` 是真正抵扣的金额，
    ``applied_points`` 是为这笔抵扣实际冻结/消费的积分（不含取整零头）。
    """

    enabled: bool = True
    points_name: str
    amount_cents: int
    available_points: int
    max_points: int
    max_cents: int
    requested_points: int
    applied_points: int
    applied_cents: int
    capped: bool = False
    rounded_points: int = 0
    min_points: int
    points_per_yuan: int
    max_ratio_percent: int
    expiry_days: int = 0
    restore_on_refund: bool = True
    ratio_note: str = ""


class PointLedgerSource(Dto):
    """来源摘要：只给类型、编号与业务标签，不含他人反馈正文。"""

    type: str
    id: str
    summary: str


class PointLedgerAppeal(Dto):
    """明细行上的申诉入口与当前状态。"""

    available: bool
    endpoint: str
    status: str | None = None


class PointLedgerItem(Dto):
    id: str
    delta: int
    entry_type: str
    description: str
    source: PointLedgerSource
    source_revision: int
    rule_version: str
    reversal_of: str | None = None
    adjustment_id: str | None = None
    occurred_at: str | None = None
    appeal: PointLedgerAppeal


class PointLedgerPage(Page[PointLedgerItem]):
    """流水明细分页。``next_cursor`` 为下一页偏移量的不透明字符串。"""


class PointAppealCreate(StrictDto):
    ledger_id: Annotated[str, Field(min_length=4, max_length=48)]
    reason: Annotated[str, Field(min_length=10, max_length=1000)]


class PointAppealResponse(Dto):
    id: str
    ledger_id: str
    status: PointAppealStatus
    reason: str
    resolution: str | None = None
    created_at: str | None = None


class PointAppealPage(Page[PointAppealResponse]):
    """本人申诉分页：``next_cursor`` 为下一页偏移量的字符串形式。"""


class PointAdjustmentCreate(StrictDto):
    """后台调整申请输入（路由在 ``app/api/admin_routes.py``，权限码 ``points.adjust``）。"""

    user_id: Annotated[str, Field(min_length=4, max_length=48)]
    delta_points: Annotated[int, Field(ge=-100_000, le=100_000)]
    reason: Annotated[str, Field(min_length=5, max_length=300)]
    source_type: Annotated[str | None, Field(max_length=20)] = None
    source_id: Annotated[str | None, Field(max_length=48)] = None
    evidence: dict[str, Any] | None = None

    @field_validator("delta_points")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("调整分值不能为 0")
        return value


class PointAdjustmentReject(StrictDto):
    reason: Annotated[str, Field(min_length=5, max_length=300)]


class PointAdjustmentResponse(Dto):
    id: str
    user_id: str
    delta_points: int
    reason: str
    status: str
    requested_by: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    ledger_id: str | None = None
    posted_balance: int | None = None
    created_at: str | None = None


__all__ = [
    "PointAccountResponse",
    "PointAdjustmentCreate",
    "PointAdjustmentReject",
    "PointAdjustmentResponse",
    "PointAppealCreate",
    "PointAppealPage",
    "PointAppealResponse",
    "PointLedgerAppeal",
    "PointLedgerItem",
    "PointLedgerPage",
    "PointLedgerSource",
    "PointRedemptionQuote",
]
