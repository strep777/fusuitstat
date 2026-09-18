"""反馈模块的 HTTP 输入输出 DTO。

提交响应固定为 ``{id,status:"SUBMITTED",reward_status:"PENDING_REVIEW"}``：
提交时不能给出确定积分（06/09 文档）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.modules.shared.schemas import Dto, Page, StrictDto

FeedbackCategory = Literal["bug", "experience", "suggestion", "content_error", "after_sale", "other"]
FeedbackStatus = Literal[
    "SUBMITTED", "TRIAGED", "NEEDS_INFO", "IN_PROGRESS", "RESOLVED", "CLOSED", "DUPLICATE", "REJECTED"
]
RewardStatus = Literal["PENDING_REVIEW", "APPROVED", "GRANTED", "NOT_ELIGIBLE", "REVOKED"]
RewardLevel = Literal["minor", "normal", "major"]
MessageVisibility = Literal["user", "internal"]
AppealStatus = Literal["SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED"]


class FeedbackCreate(StrictDto):
    """反馈输入：title 1–100 字、body 10–5000 字、附件最多 3 张。"""

    category: FeedbackCategory
    title: Annotated[str, Field(min_length=1, max_length=100)]
    body: Annotated[str, Field(min_length=10, max_length=5000)]
    page_path: Annotated[str | None, Field(max_length=200)] = None
    steps: Annotated[str | None, Field(max_length=2000)] = None
    asset_ids: Annotated[list[str], Field(max_length=3)] = []


class FeedbackCreatedResponse(Dto):
    """提交响应：只有编号、处理状态与奖励状态。"""

    id: str
    status: FeedbackStatus
    reward_status: RewardStatus


class FeedbackSla(Dto):
    """首响时效（C30：反馈 7 个工作日首响）。与询价侧同一套计算口径与字段名。"""

    sla_workdays: int = 0
    response_due_at: str | None = None
    first_responded_at: str | None = None
    response_overdue: bool = False


class FeedbackSummaryItem(Dto):
    id: str
    category: str
    title: str
    page_path: str | None = None
    status: str
    reward_status: str
    reward_level: str | None = None
    reward_points: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    sla_workdays: int = 0
    response_due_at: str | None = None
    first_responded_at: str | None = None
    response_overdue: bool = False


class FeedbackPage(Page[FeedbackSummaryItem]):
    """本人反馈列表分页。``next_cursor`` 为下一页偏移量的字符串形式。"""


class FeedbackMessageItem(Dto):
    id: str
    author_type: str
    body: str
    visibility: MessageVisibility
    created_at: str | None = None


class FeedbackDetailResponse(Dto):
    """反馈详情。``messages`` 只含用户可见回复，内部备注不出现在用户接口。"""

    id: str
    category: str
    title: str
    body: str
    page_path: str | None = None
    steps: str | None = None
    status: str
    duplicate_of: str | None = None
    duplicate_summary: str | None = None
    reward_status: str
    reward_level: str | None = None
    reward_points: int = 0
    reward_reason: str | None = None
    reward_note: str | None = None
    resolution_note: str | None = None
    review_revision: int = 0
    asset_ids: list[str] = []
    messages: list[FeedbackMessageItem] = []
    can_appeal: bool = True
    appeal_endpoint: str
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    sla_workdays: int = 0
    response_due_at: str | None = None
    first_responded_at: str | None = None
    response_overdue: bool = False


class FeedbackMessageCreate(StrictDto):
    body: Annotated[str, Field(min_length=1, max_length=2000)]


class FeedbackAppealCreate(StrictDto):
    reason: Annotated[str, Field(min_length=10, max_length=1000)]


class FeedbackAppealResponse(Dto):
    id: str
    feedback_id: str
    status: AppealStatus
    reason: str
    resolution: str | None = None
    created_at: str | None = None


class FeedbackAppealResolve(StrictDto):
    """申诉复核输入（权限码 ``feedback.review``，路由在 admin_routes.py）。"""

    status: Literal["UNDER_REVIEW", "ACCEPTED", "REJECTED"]
    resolution: Annotated[str, Field(min_length=5, max_length=300)]


class FeedbackReviewCreate(StrictDto):
    """后台审核输入（权限码 ``feedback.review``，路由在 admin_routes.py）。

    - ``reason`` 为用户可读理由；DUPLICATE/REJECTED 必须填写；
    - ``reward_level`` 决定 C14 分级奖励（minor=5 / normal=20 / major=50）；
    - ``impact`` / ``criteria`` 记录影响与判据，major 等级必填。
    """

    result: FeedbackStatus
    reason: Annotated[str, Field(min_length=2, max_length=500)]
    reward_level: RewardLevel | None = None
    impact: Annotated[str | None, Field(max_length=300)] = None
    criteria: Annotated[str | None, Field(max_length=300)] = None
    duplicate_of: Annotated[str | None, Field(max_length=48)] = None
    resolution_note: Annotated[str | None, Field(max_length=500)] = None


class FeedbackWithdrawRequest(StrictDto):
    reason: Annotated[str | None, Field(max_length=300)] = None


class FeedbackRewardRevoke(StrictDto):
    """奖励冲正输入（确认作弊后复核追加冲正，09 文档）。"""

    reason: Annotated[str, Field(min_length=5, max_length=300)]


__all__ = [
    "AppealStatus",
    "FeedbackAppealCreate",
    "FeedbackAppealResolve",
    "FeedbackAppealResponse",
    "FeedbackCategory",
    "FeedbackCreate",
    "FeedbackCreatedResponse",
    "FeedbackDetailResponse",
    "FeedbackMessageCreate",
    "FeedbackMessageItem",
    "FeedbackPage",
    "FeedbackReviewCreate",
    "FeedbackRewardRevoke",
    "FeedbackSla",
    "FeedbackStatus",
    "FeedbackSummaryItem",
    "FeedbackWithdrawRequest",
    "MessageVisibility",
    "RewardLevel",
    "RewardStatus",
]
