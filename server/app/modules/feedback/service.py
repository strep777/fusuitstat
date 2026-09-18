"""反馈模块用例：提交（限流 + 去重 + 幂等）、沟通、申诉、审核与 C14 分级奖励。

规则（09 文档）：

- 分类固定；状态机受约束；DUPLICATE/REJECTED 必须给用户可读理由；
- 奖励状态独立（``PENDING_REVIEW → APPROVED → GRANTED``，另有 ``NOT_ELIGIBLE``/``REVOKED``）；
- 奖励等级 minor=5 / normal=20 / major=50 分（等级到分值的映射由积分模块按 C14 持有）；
- **每人每天最多奖励 1 条**（Asia/Shanghai 自然日边界），每月不设上限；
  达到上限仍可正常提交反馈，只是本次不再获奖；
- 有用反馈即使不能立刻修复也可奖励；不能要求用户删除差评、给好评或说正面内容，
  负面反馈不因立场被拒；
- 同一实际问题原则上奖励首个有效报告；补充关键新证据可升级同一反馈的奖励，只补差额；
- 撤回反馈不自动删除已发积分，确认作弊才通过复核追加冲正；
- 审核操作与奖励命令、outbox、通知登记在同一事务保存。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.content.ports import loyalty_policy
from app.modules.contracts import AwardRequest
from app.modules.feedback import repository as repo
from app.modules.feedback.models import (
    ALLOWED_TRANSITIONS,
    FEEDBACK_CATEGORIES,
    FEEDBACK_STATUSES,
    REASON_REQUIRED_RESULTS,
    REWARD_LEVELS,
    Feedback,
    FeedbackAppeal,
    FeedbackMessage,
)
from app.platform import audit, rate_limit
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms, shanghai_day_bounds_ms
from app.platform.reliability import digest, enqueue

# 反馈附件：最多 3 张，purpose 必须为 feedback（media 模块的 purpose 清单里已定义）。
MAX_FEEDBACK_ASSETS = 3

# query 里出现这些参数名（精确匹配）时，整段 query 丢弃（06/09 文档：不存 URL 中的敏感值）。
SENSITIVE_QUERY_KEYS = (
    "token",
    "code",
    "session",
    "sid",
    "email",
    "phone",
    "mobile",
    "tel",
    "password",
    "passwd",
    "secret",
    "key",
    "auth",
    "sign",
    "signature",
    "jwt",
    "otp",
    "captcha",
    "ticket",
    "invite",
    "verify",
    "openid",
    "state",
)

# 复合参数名（如 ``access_token``、``user_email``）用子串匹配；
# 这里只放不会被正常参数误伤的片段（例如不用 ``key``/``sign``，避免命中 keyword/design）。
SENSITIVE_QUERY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "auth",
    "session",
    "signature",
    "credential",
    "captcha",
    "verify",
    "verification",
    "email",
    "phone",
    "mobile",
    "jwt",
    "openid",
    "otp",
)

_LONG_TOKEN = re.compile(r"^[A-Za-z0-9_\-\.]{32,}$")
_EMAIL_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# 不能以“删差评/给好评”为奖励条件（09 文档：不得要求用户删除差评或只留正面内容）。
REWARD_COERCION_KEYWORDS = ("删除差评", "删差评", "给好评", "改好评", "改成正面", "撤销投诉", "删除反馈")

IDEMPOTENCY_OPERATION = "feedback.create"
IDEMPOTENCY_TTL_MS = 7 * 86_400_000


# ------------------------------------------------------------------ 输入清洗
def sanitize_page_path(value: str | None) -> str | None:
    """只接受本站相对路径，并去掉 query 中的敏感值。

    出现 ``token``/``code``/``session``/``email``/``phone`` 等参数名，或参数值形如
    邮箱、长令牌时，**整段 query 丢弃**；绝对地址直接拒绝。
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if len(raw) > 600:
        raise AppError("VALIDATION_ERROR", "来源页面地址过长。", fields={"page_path": "过长"})
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        raise AppError(
            "VALIDATION_ERROR", "来源页面只能是本站相对路径。", fields={"page_path": "必须是相对路径"}
        )
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        raise AppError(
            "VALIDATION_ERROR", "来源页面只能是本站相对路径。", fields={"page_path": "必须是相对路径"}
        )
    if "\\" in parsed.path or "://" in raw:
        raise AppError(
            "VALIDATION_ERROR", "来源页面只能是本站相对路径。", fields={"page_path": "必须是相对路径"}
        )
    query = _safe_query(parsed.query)
    result = parsed.path + (f"?{query}" if query else "")
    return result[:200]


def _safe_query(query: str) -> str:
    """敏感参数整段丢弃，其余参数原样保留。"""
    if not query:
        return ""
    kept: list[tuple[str, str]] = []
    for name, value in parse_qsl(query, keep_blank_values=True):
        lowered = name.lower()
        if lowered in SENSITIVE_QUERY_KEYS:
            return ""
        if any(marker in lowered for marker in SENSITIVE_QUERY_MARKERS):
            return ""
        if _LONG_TOKEN.match(value) or _EMAIL_LIKE.match(value):
            return ""
        kept.append((name, value))
    return urlencode(kept)


def content_fingerprint(*, category: str, title: str, page_path: str | None) -> str:
    """重复内容指纹。只辅助运营审核，不自动判定抄袭。"""
    return digest({"category": category, "title": title, "page_path": page_path})


def _assert_feedback_assets(session: Session, *, user_id: str, asset_ids: list[str]) -> list[Any]:
    """附件校验：本人所有、purpose=feedback、扫描完成且未删除。

    素材归属媒体模块，这里通过 ``media.ports`` 校验，不导入它的 ORM（04 文档）。
    """
    from app.modules.media.ports import assert_owned_ready

    if not asset_ids:
        return []
    if len(asset_ids) > MAX_FEEDBACK_ASSETS:
        raise AppError(
            "UPLOAD_LIMIT_EXCEEDED", f"反馈附件最多 {MAX_FEEDBACK_ASSETS} 张。"
        )
    return assert_owned_ready(session, asset_ids=asset_ids, owner_id=user_id, purpose="feedback")


# ------------------------------------------------------------------ 提交与查询
def create_feedback(
    session: Session,
    *,
    user_id: str,
    category: str,
    title: str,
    body: str,
    page_path: str | None = None,
    steps: str | None = None,
    asset_ids: list[str] | None = None,
    idempotency_key: str | None = None,
    client_ip: str | None = None,
    now: int | None = None,
) -> Feedback:
    """提交反馈：幂等键 → 限流 → 去重 → 附件校验 → 建单（同一事务）。"""
    moment = now if now is not None else now_ms()
    clean_title = (title or "").strip()
    clean_body = (body or "").strip()
    if not clean_title or len(clean_title) > 100:
        raise AppError("VALIDATION_ERROR", "标题需要 1–100 个字。", fields={"title": "长度不符合要求"})
    if len(clean_body) < 10 or len(clean_body) > 5000:
        raise AppError("VALIDATION_ERROR", "内容需要 10–5000 个字。", fields={"body": "长度不符合要求"})
    if category not in FEEDBACK_CATEGORIES:
        raise AppError("VALIDATION_ERROR", "反馈分类无效。", fields={"category": "非法分类"})
    clean_path = sanitize_page_path(page_path)
    clean_steps = (steps or "").strip()[:2000] or None
    assets = list(asset_ids or [])
    request_hash = digest(
        {
            "category": category,
            "title": clean_title,
            "body": clean_body,
            "page_path": clean_path,
            "steps": clean_steps,
            "asset_ids": assets,
        }
    )
    fingerprint = content_fingerprint(category=category, title=clean_title, page_path=clean_path)
    # 显式幂等键（Idempotency-Key）重放返回首次结果；未提供时用内容指纹登记，
    # 但重复提交由“去重”规则判为 FEEDBACK_DUPLICATE（09 文档要求返回该错误码）。
    explicit_key = (idempotency_key or "").strip()
    key = (explicit_key or f"auto-{fingerprint[:32]}")[:120]
    if explicit_key:
        existing = repo.get_idempotency(
            session, actor_id=user_id, operation=IDEMPOTENCY_OPERATION, key=key
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_CONFLICT")
            if existing.resource_id:
                row = repo.get_feedback(session, feedback_id=existing.resource_id, user_id=user_id)
                if row is not None:
                    return row

    # 限流：每天上限 10 条、每小时 3 条（配置项），超限抛 FEEDBACK_LIMIT_EXCEEDED。
    settings = get_settings()
    rate_limit.check(
        session,
        scope="feedback_submit_hour",
        subject=user_id,
        limit=settings.feedback_hourly_submit_limit,
        window_seconds=3600,
        code="FEEDBACK_LIMIT_EXCEEDED",
        now=moment,
    )
    rate_limit.check(
        session,
        scope="feedback_submit_day",
        subject=user_id,
        limit=settings.feedback_daily_submit_limit,
        window_seconds=86_400,
        code="FEEDBACK_LIMIT_EXCEEDED",
        now=moment,
    )

    duplicate = repo.find_duplicate(
        session, user_id=user_id, page_path=clean_path, title=clean_title
    )
    if duplicate is not None:
        raise AppError(
            "FEEDBACK_DUPLICATE",
            f"同一页面的同类反馈已提交过（编号 {duplicate.id}），可在反馈列表中查看进度。",
        )

    checked_assets = _assert_feedback_assets(session, user_id=user_id, asset_ids=assets)
    _claim_idempotency(
        session,
        actor_id=user_id,
        key=key,
        request_hash=request_hash,
        moment=moment,
    )
    row = Feedback(
        id=new_id("feedback"),
        user_id=user_id,
        category=category,
        title=clean_title[:100],
        body=clean_body[:5000],
        page_path=clean_path,
        steps=clean_steps,
        status="SUBMITTED",
        reward_status="PENDING_REVIEW",
        content_fingerprint=fingerprint,
    )
    session.add(row)
    session.flush()
    if checked_assets:
        from app.modules.media.ports import link_asset

        for asset in checked_assets:
            link_asset(
                session,
                asset_id=asset.id,
                resource_type="feedback",
                resource_id=row.id,
                role="attachment",
            )
    _complete_idempotency(session, actor_id=user_id, key=key, resource_id=row.id)
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="feedback.create",
        resource_type="feedback",
        resource_id=row.id,
        after={"category": category, "page_path": clean_path, "assets": len(checked_assets)},
        ip=client_ip,
    )
    _notify_user_received(session, row=row, moment=moment)
    _notify_staff(
        session,
        event_id=f"feedback-created-{row.id}",
        title="收到新的用户反馈",
        body=f"反馈 {row.id}（{category}）待分诊与审核。",
        payload={"feedback_id": row.id, "category": category},
    )
    enqueue(
        session,
        event_type="notification.dispatch",
        aggregate_id=row.id,
        payload={"reason": "feedback_created", "category": category},
    )
    return row


def list_feedback_page(
    session: Session,
    *,
    user_id: str,
    status: str | None = None,
    category: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    rows = repo.list_feedbacks(
        session, user_id=user_id, status=status, category=category, limit=limit, offset=offset
    )
    total = repo.count_feedbacks(session, user_id=user_id, status=status, category=category)
    return [serialize_summary(session, row) for row in rows], total


def get_feedback_detail(session: Session, *, user_id: str, feedback_id: str) -> dict[str, Any]:
    """本人反馈详情。内部备注与其他人反馈一律不可见（非本人按不存在处理）。"""
    row = repo.get_feedback(session, feedback_id=feedback_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return serialize_detail(session, row)


def add_message(
    session: Session, *, user_id: str, feedback_id: str, body: str, client_ip: str | None = None
) -> FeedbackMessage:
    """用户补充说明。始终以 ``visibility='user'`` 落库，内部备注只由员工创建。"""
    row = repo.get_feedback(session, feedback_id=feedback_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status in ("CLOSED", "REJECTED", "DUPLICATE"):
        raise AppError("STATE_CONFLICT", "该反馈已结束，如需补充请提交新的反馈。")
    clean_body = (body or "").strip()
    if not clean_body:
        raise AppError("VALIDATION_ERROR", "请填写补充内容。", fields={"body": "必填"})
    moment = now_ms()
    message = FeedbackMessage(
        id=new_id("message"),
        feedback_id=row.id,
        author_type="user",
        author_id=user_id,
        body=clean_body[:2000],
        visibility="user",
        created_at=moment,
    )
    session.add(message)
    row.last_message_at = moment
    row.updated_at = moment
    if row.status == "NEEDS_INFO":
        row.status = "IN_PROGRESS"  # 用户补充资料后回到处理中
    row.version += 1
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="feedback.message_create",
        resource_type="feedback",
        resource_id=row.id,
        after={"message_id": message.id, "status": row.status},
        ip=client_ip,
    )
    _notify_staff(
        session,
        event_id=f"feedback-message-{message.id}",
        title="用户补充了反馈内容",
        body=f"反馈 {row.id} 收到用户补充，请查看。",
        payload={"feedback_id": row.id, "message_id": message.id},
    )
    return message


def create_appeal(
    session: Session, *, user_id: str, feedback_id: str, reason: str, client_ip: str | None = None
) -> FeedbackAppeal:
    """对处理结论或奖励提出申诉。同一反馈的未结申诉只保留一条。"""
    row = repo.get_feedback(session, feedback_id=feedback_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 10:
        raise AppError("VALIDATION_ERROR", "请填写至少 10 个字的申诉理由。", fields={"reason": "过短"})
    existing = repo.open_appeal(session, feedback_id=row.id, user_id=user_id)
    if existing is not None:
        return existing
    appeal = FeedbackAppeal(
        id=new_id("appeal"),
        feedback_id=row.id,
        user_id=user_id,
        reason=clean_reason[:1000],
        status="SUBMITTED",
    )
    session.add(appeal)
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="feedback.appeal_create",
        resource_type="feedback_appeal",
        resource_id=appeal.id,
        after={"feedback_id": row.id, "status": row.status},
        reason=clean_reason[:200],
        ip=client_ip,
    )
    _notify_staff(
        session,
        event_id=f"feedback-appeal-{appeal.id}",
        title="收到反馈申诉",
        body=f"反馈 {row.id} 的结论被申诉，请复核。",
        payload={"feedback_id": row.id, "appeal_id": appeal.id},
    )
    return appeal


def withdraw_feedback(
    session: Session, *, user_id: str, feedback_id: str, reason: str | None = None
) -> Feedback:
    """用户撤回反馈。**不删除已发积分**：确认作弊才由复核追加冲正（09 文档）。"""
    row = repo.get_feedback(session, feedback_id=feedback_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status == "CLOSED":
        raise AppError("STATE_CONFLICT", "该反馈已关闭。")
    moment = now_ms()
    row.status = "CLOSED"
    row.closed_at = moment
    row.withdrawn_at = moment
    row.resolution_note = (f"用户撤回反馈：{reason.strip()}" if reason and reason.strip() else "用户撤回反馈。")[
        :500
    ]
    row.version += 1
    row.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="feedback.withdraw",
        resource_type="feedback",
        resource_id=row.id,
        after={"reward_status": row.reward_status, "reward_points": row.reward_points},
        reason=(reason or "用户撤回")[:200],
    )
    return row


# ------------------------------------------------------------------ 审核与奖励
def is_self_review(session: Session, *, feedback: Feedback, reviewer_id: str | None) -> bool:
    """审核人不能审核自己提交的反馈（防自奖）。

    目前能严格判断的是「审核主体与反馈提交者是同一个 ID」，以及运营在
    ``feedback.reviewer_related_user_ids`` 设置项里登记的关联/测试账号名单。

    TODO(运营配置)：完整的关联账号识别（员工与客户账号同一手机号前缀、测试账号标记）
    需要运营维护「员工 ↔ 客户账号」关联关系——``users`` 与 ``staff_accounts`` 的手机号
    分别使用不同密钥的 HMAC（``phone_lookup``），不可直接比较，因此这里不做猜测性判断。
    """
    if not reviewer_id:
        return False
    if feedback.user_id == reviewer_id:
        return True
    from app.modules.content.ports import setting_value

    related = setting_value(session, "feedback.reviewer_related_user_ids", []) or []
    return feedback.user_id in {str(item) for item in related}


def review_feedback(
    session: Session,
    *,
    feedback_id: str,
    reviewer_id: str | None,
    result: str,
    reason: str,
    reward_level: str | None = None,
    impact: str | None = None,
    criteria: str | None = None,
    duplicate_of: str | None = None,
    resolution_note: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """后台审核（权限码 ``feedback.review``，路由在 admin_routes.py）。

    审核记录、奖励命令、outbox 与通知登记在同一事务；同一复审版本只会入账一次。
    """
    moment = now if now is not None else now_ms()
    row = repo.get_feedback(session, feedback_id=feedback_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if is_self_review(session, feedback=row, reviewer_id=reviewer_id):
        raise AppError("FORBIDDEN", "不能审核自己提交的反馈。")
    if result not in FEEDBACK_STATUSES:
        raise AppError("VALIDATION_ERROR", "结果状态无效。", fields={"result": "非法状态"})
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 2:
        raise AppError("VALIDATION_ERROR", "请填写审核理由。", fields={"reason": "必填"})
    if result in REASON_REQUIRED_RESULTS and len(clean_reason) < 5:
        raise AppError(
            "VALIDATION_ERROR",
            "标记重复或驳回时必须填写用户可读理由。",
            fields={"reason": "理由过短"},
        )
    _assert_transition(current=row.status, target=result)
    if reward_level is not None:
        if reward_level not in REWARD_LEVELS:
            raise AppError("VALIDATION_ERROR", "奖励等级无效。", fields={"reward_level": "非法等级"})
        if result != "RESOLVED":
            raise AppError(
                "VALIDATION_ERROR",
                "只有结论为 RESOLVED 时才能发放奖励。",
                fields={"reward_level": "结论不支持奖励"},
            )
        if not (criteria or "").strip():
            raise AppError(
                "VALIDATION_ERROR", "发放奖励必须记录判定依据。", fields={"criteria": "必填"}
            )
        if reward_level == "major" and not (impact or "").strip():
            raise AppError(
                "VALIDATION_ERROR",
                "major 等级必须记录影响与判据。",
                fields={"impact": "必填"},
            )
    _assert_no_reward_coercion(clean_reason)

    duplicate_summary: str | None = None
    if result == "DUPLICATE":
        if not duplicate_of or duplicate_of == row.id:
            raise AppError(
                "VALIDATION_ERROR", "标记重复必须关联原反馈编号。", fields={"duplicate_of": "必填"}
            )
        original = repo.get_feedback(session, feedback_id=duplicate_of)
        if original is None:
            raise AppError("RESOURCE_NOT_FOUND", "关联的原反馈不存在。")
        # 只登记原反馈编号与匿名摘要，不公开原提交者。
        duplicate_summary = f"已有同类反馈（{original.id}，当前状态 {original.status}）"

    # 并发审核断言：条件 UPDATE 只有一个审核人能推进复审代数。
    revision = int(row.review_revision) + 1
    claimed = repo.cas_claim_review(
        session,
        feedback_id=row.id,
        expected_revision=row.review_revision,
        values={"review_revision": revision, "version": row.version + 1, "updated_at": moment},
    )
    if not claimed:
        return _concurrent_review_result(session, row=row)

    reward_status, decided_level, reward_note = _decide_reward(
        session, row=row, result=result, reward_level=reward_level, moment=moment
    )
    award: dict[str, Any] | None = None
    if decided_level is not None:
        award = _grant_reward(session, row=row, level=decided_level, revision=revision, moment=moment)
        reward_status = "GRANTED" if award.get("status") == "POSTED" else "APPROVED"

    review_points = int(award.get("target_points") or 0) if award else 0
    inserted = repo.insert_review(
        session,
        id=new_id("review"),
        feedback_id=row.id,
        revision=revision,
        result=result,
        reason=clean_reason[:500],
        reward_level=decided_level,
        reward_points=review_points,
        reward_status=reward_status,
        impact=(impact or None),
        criteria=(criteria or None),
        reviewer_id=reviewer_id,
        created_at=moment,
    )
    if not inserted:  # pragma: no cover - 复审代数已由 CAS 独占
        return _concurrent_review_result(session, row=row)

    row.status = result
    # C30 首响：审核结论是工作人员第一次对用户可见的响应，只记一次。
    _mark_first_response(row, moment)
    if resolution_note or result in ("RESOLVED", "CLOSED", "REJECTED", "DUPLICATE"):
        row.resolution_note = (resolution_note or clean_reason)[:500]
    row.reward_status = reward_status
    if duplicate_summary is not None:
        row.duplicate_of = duplicate_of
        row.duplicate_summary = duplicate_summary
    if result == "CLOSED":
        row.closed_at = moment
    if award is not None:
        row.reward_level = decided_level
        row.reward_award_id = str(award.get("award_id") or "")
        row.reward_points = review_points
        row.reward_granted_at = moment
        row.reward_reason = clean_reason[:300]
    elif reward_status == "NOT_ELIGIBLE":
        row.reward_level = None
        row.reward_reason = (reward_note or clean_reason)[:300]
    row.updated_at = moment
    session.flush()

    audit.record(
        session,
        actor_type="staff" if reviewer_id else "system",
        actor_id=reviewer_id,
        action="feedback.review",
        resource_type="feedback",
        resource_id=row.id,
        after={
            "revision": revision,
            "result": result,
            "reward_level": decided_level,
            "reward_status": reward_status,
            "reward_points": review_points,
            "duplicate_of": row.duplicate_of,
        },
        reason=clean_reason[:200],
    )
    enqueue(
        session,
        event_type="feedback.reviewed",
        aggregate_id=row.id,
        aggregate_version=revision,
        payload={
            "feedback_id": row.id,
            "result": result,
            "reward_status": reward_status,
            "reward_level": decided_level,
            "reward_points": review_points,
        },
    )
    _notify_user_reviewed(
        session, row=row, result=result, points=review_points, reason=reward_note or clean_reason
    )
    return {
        "feedback_id": row.id,
        "status": row.status,
        "review_revision": revision,
        "reward_status": row.reward_status,
        "reward_level": row.reward_level,
        "reward_points": row.reward_points,
        "reward_note": reward_note,
        "granted_delta": int((award or {}).get("delta") or 0),
        "duplicate_review": False,
        "message": reward_note or "审核结论已记录。",
    }


def revoke_reward(
    session: Session, *, feedback_id: str, reviewer_id: str | None, reason: str, now: int | None = None
) -> dict[str, Any]:
    """复核冲正（确认作弊/不符合条件）。追加反向流水，原流水保留。"""
    moment = now if now is not None else now_ms()
    row = repo.get_feedback(session, feedback_id=feedback_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if is_self_review(session, feedback=row, reviewer_id=reviewer_id):
        raise AppError("FORBIDDEN", "不能复核自己提交的反馈。")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 5:
        raise AppError("VALIDATION_ERROR", "请填写至少 5 个字的冲正理由。", fields={"reason": "过短"})
    if row.reward_status not in ("GRANTED", "APPROVED"):
        raise AppError("STATE_CONFLICT", "该反馈没有可冲正的奖励。")
    revision = int(row.review_revision) + 1
    claimed = repo.cas_claim_review(
        session,
        feedback_id=row.id,
        expected_revision=row.review_revision,
        values={"review_revision": revision, "version": row.version + 1, "updated_at": moment},
    )
    if not claimed:
        return _concurrent_review_result(session, row=row)
    from app.modules.loyalty import ports as loyalty_ports

    reversed_award = loyalty_ports.reverse_award(
        session, source_type="feedback", source_id=row.id, reason=clean_reason, actor_id=reviewer_id
    )
    repo.insert_review(
        session,
        id=new_id("review"),
        feedback_id=row.id,
        revision=revision,
        result=row.status,
        reason=clean_reason[:500],
        reward_level=row.reward_level,
        reward_points=0,
        reward_status="REVOKED",
        impact=None,
        criteria=None,
        reviewer_id=reviewer_id,
        created_at=moment,
    )
    row.reward_status = "REVOKED"
    row.reward_points = 0
    row.reward_reason = clean_reason[:300]
    row.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="staff" if reviewer_id else "system",
        actor_id=reviewer_id,
        action="feedback.reward_revoke",
        resource_type="feedback",
        resource_id=row.id,
        after={
            "revision": revision,
            "reward_status": "REVOKED",
            "reward_delta": int(reversed_award.get("delta") or 0),
        },
        reason=clean_reason[:200],
    )
    _notify_user_reviewed(session, row=row, result=row.status, points=0, reason=clean_reason)
    return {
        "feedback_id": row.id,
        "status": row.status,
        "review_revision": revision,
        "reward_status": row.reward_status,
        "reward_points": 0,
        "granted_delta": int(reversed_award.get("delta") or 0),
        "message": "奖励已冲正。",
    }


def resolve_appeal(
    session: Session,
    *,
    appeal_id: str,
    status: str,
    resolution: str,
    actor_id: str | None,
    now: int | None = None,
) -> FeedbackAppeal:
    """后台复核申诉（权限码 ``feedback.review``，路由在 admin_routes.py）。"""
    if status not in ("UNDER_REVIEW", "ACCEPTED", "REJECTED"):
        raise AppError("VALIDATION_ERROR", "申诉结论无效。", fields={"status": "非法结论"})
    row = repo.get_appeal(session, appeal_id=appeal_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status in ("ACCEPTED", "REJECTED"):
        raise AppError("STATE_CONFLICT", "该申诉已有结论。")
    moment = now if now is not None else now_ms()
    row.status = status
    row.resolution = resolution.strip()[:300]
    row.handled_by = actor_id
    row.handled_at = moment
    row.updated_at = moment
    session.flush()
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="feedback.appeal_resolve",
        resource_type="feedback_appeal",
        resource_id=row.id,
        after={"status": status, "resolution": row.resolution},
        reason=row.resolution,
    )
    return row


def _decide_reward(
    session: Session, *, row: Feedback, result: str, reward_level: str | None, moment: int
) -> tuple[str, str | None, str | None]:
    """决定奖励状态：每日上限按 Asia/Shanghai 自然日，每月不设上限。"""
    if reward_level is None:
        if result in ("RESOLVED", "CLOSED", *REASON_REQUIRED_RESULTS):
            # 已给出结论但未达到奖励标准：明确说清，不留一个永远悬着的 PENDING_REVIEW。
            return "NOT_ELIGIBLE", None, "该反馈未达到奖励标准（缺少可核实证据或实用价值不足）。"
        # 仍在分诊/补充资料阶段：奖励结论尚未作出。
        return "PENDING_REVIEW", None, None
    policy = loyalty_policy(session)
    daily_limit = max(1, int(policy["feedback_daily_reward_limit"]))
    start_ms, end_ms = shanghai_day_bounds_ms(moment)
    rewarded = repo.rewarded_feedback_ids(
        session, user_id=row.user_id, start_ms=start_ms, end_ms=end_ms
    )
    # 同一条反馈的升级奖励不额外占用当天的奖励条数；每月不设上限。
    if row.id in rewarded or len(rewarded) < daily_limit:
        return "APPROVED", reward_level, None
    note = (
        f"该账号今日奖励名额已用完（每日最多 {daily_limit} 条，按 Asia/Shanghai 自然日计算）；"
        "本次仅处理问题、不再发放积分，不影响继续提交反馈。"
    )
    return "NOT_ELIGIBLE", None, note


def _grant_reward(
    session: Session, *, row: Feedback, level: str, revision: int, moment: int
) -> dict[str, Any]:
    """调用积分模块的命令入账。

    ``eligible_amount_cents`` 固定为 0：反馈奖励是 C14 的**分级规则**，不是按金额折算，
    等级到分值的映射由积分模块按规则版本持有（minor=5 / normal=20 / major=50）。
    """
    from app.modules.loyalty import ports as loyalty_ports

    request = AwardRequest(
        user_id=row.user_id,
        source_type="feedback",
        source_id=row.id,
        source_revision=revision,
        eligible_amount_cents=0,
        rule_version="",
        available_at=moment,
        label=f"反馈奖励（{row.id}）",
        detail={"level": level, "review_revision": revision},
    )
    return loyalty_ports.recompute_award(session, request)


def _concurrent_review_result(session: Session, *, row: Feedback) -> dict[str, Any]:
    """并发审核：后来者只读取既有结论，不重复发奖。"""
    return {
        "feedback_id": row.id,
        "status": row.status,
        "review_revision": int(row.review_revision),
        "reward_status": row.reward_status,
        "reward_level": row.reward_level,
        "reward_points": int(row.reward_points),
        "reward_note": row.reward_reason,
        "granted_delta": 0,
        "duplicate_review": True,
        "message": "该反馈已被其他审核人处理，本次不重复发奖。",
    }


def _assert_transition(*, current: str, target: str) -> None:
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, ()):
        raise AppError(
            "STATE_CONFLICT",
            f"反馈状态不能从 {current} 变更为 {target}。",
            fields={"result": "非法流转"},
        )


def _assert_no_reward_coercion(reason: str) -> None:
    """不能以删除差评、给好评或改成正面内容作为奖励条件（09 文档）。"""
    for keyword in REWARD_COERCION_KEYWORDS:
        if keyword in reason:
            raise AppError(
                "VALIDATION_ERROR",
                "奖励不能以删除差评、给好评或只保留正面内容为条件。",
                fields={"reason": "不得以评价立场作为奖励条件"},
            )


def _categories() -> tuple[str, ...]:  # pragma: no cover - 供运营脚本读取分类清单
    return FEEDBACK_CATEGORIES


# ------------------------------------------------------------------ 序列化
def serialize_summary(session: Session, row: Feedback) -> dict[str, Any]:
    return {
        "id": row.id,
        "category": row.category,
        "title": row.title,
        "page_path": row.page_path,
        "status": row.status,
        "reward_status": row.reward_status,
        "reward_level": row.reward_level,
        "reward_points": int(row.reward_points),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        **feedback_sla(session, row),
    }


def serialize_message(row: FeedbackMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "author_type": row.author_type,
        "body": row.body,
        "visibility": row.visibility,
        "created_at": iso(row.created_at),
    }


def serialize_appeal(row: FeedbackAppeal) -> dict[str, Any]:
    return {
        "id": row.id,
        "feedback_id": row.feedback_id,
        "status": row.status,
        "reason": row.reason,
        "resolution": row.resolution,
        "created_at": iso(row.created_at),
    }


def serialize_detail(session: Session, row: Feedback) -> dict[str, Any]:
    """详情：只返回 ``visibility='user'`` 的回复；内部备注永不出现。"""
    from app.modules.media.ports import assets_for

    messages = repo.list_messages(session, feedback_id=row.id, visibility="user")
    assets = assets_for(session, resource_type="feedback", resource_id=row.id, role="attachment")
    return {
        "id": row.id,
        "category": row.category,
        "title": row.title,
        "body": row.body,
        "page_path": row.page_path,
        "steps": row.steps,
        "status": row.status,
        "duplicate_of": row.duplicate_of,
        "duplicate_summary": row.duplicate_summary,
        "reward_status": row.reward_status,
        "reward_level": row.reward_level,
        "reward_points": int(row.reward_points),
        "reward_reason": row.reward_reason,
        "reward_note": _reward_note(row),
        "resolution_note": row.resolution_note,
        "review_revision": int(row.review_revision),
        "asset_ids": [asset.id for asset in assets],
        "messages": [serialize_message(message) for message in messages],
        "can_appeal": True,
        "appeal_endpoint": f"{get_settings().api_prefix}/feedback/{row.id}/appeals",
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "closed_at": iso(row.closed_at),
        # C30：反馈承诺 7 个工作日首响；与询价侧同一套日历与计算口径。
        **feedback_sla(session, row),
    }


# ------------------------------------------------------------------ 首响时效（C30）
def feedback_sla(session: Session, row: Feedback, *, now: int | None = None) -> dict[str, Any]:
    """反馈首响时效：承诺工作日数、首响截止时间与是否已逾期。

    截止时间按**客服日历**（工作日 + 节假日 + 调休）计算；已响应则报告实际响应时间。
    已结束（CLOSED/REJECTED/DUPLICATE/撤回）的反馈不再算逾期：它们已经有结论，
    继续提醒没有意义，也会让后台列表长期飘红。
    """
    from app.modules.content import ports as content_ports

    moment = now if now is not None else now_ms()
    workdays = int(content_ports.response_sla_workdays(session)["feedback_workdays"])
    start = row.created_at
    deadline = content_ports.response_deadline(session, start_ms=start, workdays=workdays)
    responded = row.first_responded_at
    settled = row.status in ("CLOSED", "REJECTED", "DUPLICATE") or row.withdrawn_at is not None
    return {
        "sla_workdays": workdays,
        "response_due_at": iso(deadline),
        "first_responded_at": iso(responded),
        "response_overdue": responded is None and not settled and deadline < moment,
    }


def _mark_first_response(row: Feedback, moment: int) -> None:
    """首次工作人员响应只写一次；后续审核或回复不覆盖（首响时效以第一次为准）。"""
    if row.first_responded_at is None:
        row.first_responded_at = moment


def mark_first_response(row: Feedback, moment: int | None = None) -> None:
    """供后台路由调用：把「用户可见的响应」写进首响时间（内部备注不算）。"""
    _mark_first_response(row, moment if moment is not None else now_ms())


def remind_overdue_feedback(session: Session, *, now: int | None = None, limit: int = 50) -> dict[str, Any]:
    """C30 逾期提醒：超过首响承诺仍未响应的反馈，给工作人员登记一次提醒。

    去重靠通知模块的 `(user_id, event_id)` 唯一约束——同一个反馈只提醒一次
    （event_id 含首响截止日）。返回本轮新增提醒数与仍在逾期的数量。
    """
    from app.modules.content import ports as content_ports
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    moment = now if now is not None else now_ms()
    workdays = int(content_ports.response_sla_workdays(session)["feedback_workdays"])
    calendar = content_ports.support_calendar(session)
    reminded = 0
    overdue_ids: list[str] = []
    for row in repo.overdue_feedback(session, before_ms=moment, workdays=workdays, limit=limit):
        deadline = content_ports.add_workdays(row.created_at, workdays, calendar=calendar)
        if deadline >= moment:
            continue
        overdue_ids.append(row.id)
        due_date = iso(deadline) or ""
        created = enqueue_notification(
            session,
            NotificationRequest(
                # 同一反馈只提醒一次（event_id 含首响截止日，跨天也不会重复提醒）。
                event_id=f"feedback-sla-overdue-{row.id}-{due_date[:10]}",
                event_type="support.sla_overdue",
                user_id=None,
                title="反馈首响已逾期",
                body=(
                    f"反馈 {row.id}（{row.title}）承诺 {workdays} 个工作日首响，"
                    f"截止 {due_date}，至今没有工作人员响应。"
                ),
                link_path="/admin/feedback",
                category="support",
                channels=(),
                payload={"feedback_id": row.id, "response_due_at": due_date},
            ),
        )
        reminded += created
    return {"overdue": len(overdue_ids), "reminded": reminded, "feedback_ids": overdue_ids}


def _reward_note(row: Feedback) -> str | None:
    """给用户看的奖励说明（不能承诺积分；上限与未获奖都要说清原因）。"""
    if row.reward_status == "NOT_ELIGIBLE":
        return row.reward_reason or "该反馈未达到奖励标准。"
    if row.reward_status == "GRANTED":
        return f"该反馈已发放 {int(row.reward_points)} 星绒币。"
    if row.reward_status == "APPROVED":
        return "奖励已通过审核，等待发放。"
    if row.reward_status == "REVOKED":
        return "奖励已冲正：复核确认该反馈不符合奖励条件。"
    return "奖励审核中：提交成功不代表一定获得积分。"


# ------------------------------------------------------------------ 内部：幂等与通知
def _claim_idempotency(
    session: Session, *, actor_id: str, key: str, request_hash: str, moment: int
) -> None:
    """先占幂等键（IN_PROGRESS），再建单；同键不同内容 409。"""
    inserted = repo.insert_idempotency(
        session,
        id=new_id("job"),
        actor_id=actor_id,
        operation=IDEMPOTENCY_OPERATION,
        key=key,
        request_hash=request_hash,
        resource_id=None,
        status="IN_PROGRESS",
        response_snapshot=None,
        created_at=moment,
        expires_at=moment + IDEMPOTENCY_TTL_MS,
    )
    if inserted:
        return
    existing = repo.get_idempotency(
        session, actor_id=actor_id, operation=IDEMPOTENCY_OPERATION, key=key
    )
    if existing is None:  # pragma: no cover - 冲突后必然可读
        raise AppError("STATE_CONFLICT", "提交冲突，请稍后重试。")
    if existing.request_hash != request_hash:
        raise AppError("IDEMPOTENCY_CONFLICT")
    if existing.resource_id:
        raise AppError("STATE_CONFLICT", "该请求已受理，请刷新后查看反馈列表。")
    raise AppError("STATE_CONFLICT", "相同请求正在处理中，请稍后重试。")


def _complete_idempotency(session: Session, *, actor_id: str, key: str, resource_id: str) -> None:
    from app.platform.reliability import IdempotencyKey

    session.execute(
        sa.update(IdempotencyKey)
        .where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == IDEMPOTENCY_OPERATION,
            IdempotencyKey.key == key,
        )
        .values(
            resource_id=resource_id,
            status="COMPLETED",
            response_snapshot={"id": resource_id},
        )
    )


def _notify_user_received(session: Session, *, row: Feedback, moment: int) -> None:
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"feedback-received-{row.id}",
            event_type="feedback.received",
            user_id=row.user_id,
            title="反馈已提交，等待处理",
            body=(
                f"反馈编号：{row.id}\n"
                "我们已收到你的反馈，处理进展会在反馈详情中更新。\n"
                "是否获得星绒币奖励由人工审核决定，提交成功不代表一定获奖。"
            ),
            link_path=f"/account/feedback/{row.id}",
            category="feedback",
            channels=(),
            payload={"feedback_id": row.id, "occurred_at": iso(moment)},
        ),
    )


def _notify_user_reviewed(
    session: Session, *, row: Feedback, result: str, points: int, reason: str
) -> None:
    from app.modules.notifications.ports import NotificationRequest
    from app.modules.notifications.ports import notify as enqueue_notification

    enqueue_notification(
        session,
        NotificationRequest(
            event_id=f"feedback-reviewed-{row.id}-{row.review_revision}",
            event_type="feedback.rewarded",
            user_id=row.user_id,
            title="",
            body="",
            link_path=f"/account/feedback/{row.id}",
            category="feedback",
            channels=(),
            payload={
                "feedback_id": row.id,
                "result": result,
                "points": points,
                "reason": reason,
                "occurred_at": iso(now_ms()),
            },
        ),
    )


def _notify_staff(
    session: Session, *, event_id: str, title: str, body: str, payload: dict[str, Any]
) -> None:
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
            category="feedback",
            channels=(),
            payload={"title": title, "body": body, "occurred_at": iso(now_ms()), **payload},
        ),
    )


__all__ = [
    "IDEMPOTENCY_OPERATION",
    "MAX_FEEDBACK_ASSETS",
    "REWARD_COERCION_KEYWORDS",
    "SENSITIVE_QUERY_KEYS",
    "add_message",
    "content_fingerprint",
    "create_appeal",
    "create_feedback",
    "get_feedback_detail",
    "is_self_review",
    "list_feedback_page",
    "resolve_appeal",
    "review_feedback",
    "revoke_reward",
    "sanitize_page_path",
    "serialize_appeal",
    "serialize_detail",
    "serialize_message",
    "serialize_summary",
    "withdraw_feedback",
]
