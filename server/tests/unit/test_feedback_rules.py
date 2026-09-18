"""TC-F01 ~ TC-F03：反馈处理、C14 分级奖励、每日上限与申诉。

奖励金额与时间边界都用真实服务代码断言：奖励通过 ``loyalty.ports.recompute_award``
入账，自然日边界按 Asia/Shanghai 计算。
"""

from __future__ import annotations

import contextlib
import pathlib
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa

from app.modules.content.models import SiteSetting
from app.modules.feedback import repository as feedback_repo
from app.modules.feedback import service as feedback_service
from app.modules.feedback.models import Feedback, FeedbackMessage, FeedbackReview
from app.modules.identity.models import User
from app.modules.loyalty import service as loyalty_service
from app.modules.loyalty.models import PointAward, PointLedger
from app.modules.notifications.models import Notification
from app.platform import db as platform_db
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import SHANGHAI, new_id, to_ms
from app.platform.security import encrypt_pii, mask_phone, normalize_phone, phone_lookup

TEST_ORIGIN = "http://127.0.0.1:5173"
SERVER_VAR = pathlib.Path(__file__).resolve().parents[2] / "var"

_MODEL_MODULES = (
    "app.platform.reliability",
    "app.modules.identity.models",
    "app.modules.media.models",
    "app.modules.notifications.models",
    "app.modules.content.models",
    "app.modules.catalog.models",
    "app.modules.loyalty.models",
    "app.modules.feedback.models",
)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """独立测试库：只新建库文件，不新建目录（沙箱限制见交付说明）。"""
    import importlib

    SERVER_VAR.mkdir(parents=True, exist_ok=True)
    database = SERVER_VAR / f"test-feedback-{uuid.uuid4().hex}.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_DIR", str(SERVER_VAR))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database.as_posix()}")
    monkeypatch.setenv("PUBLIC_ORIGIN", TEST_ORIGIN)
    monkeypatch.setenv("SQLITE_SYNCHRONOUS", "NORMAL")
    get_settings.cache_clear()
    platform_db.reset_engine()
    for name in _MODEL_MODULES:
        importlib.import_module(name)
    platform_db.init_database()
    try:
        yield get_settings()
    finally:
        platform_db.reset_engine()
        get_settings.cache_clear()
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(PermissionError):
                pathlib.Path(f"{database}{suffix}").unlink(missing_ok=True)


@pytest.fixture()
def db(env: Any) -> Iterator[Any]:
    session = platform_db.get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def shanghai_ms(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    """Asia/Shanghai 本地时间 → UTC 毫秒（用于自然日边界断言）。"""
    return to_ms(datetime(year, month, day, hour, minute, tzinfo=SHANGHAI))


def make_user(session: Any, *, phone: str = "13800000002", nickname: str = "反馈星友") -> str:
    normalized = normalize_phone(phone)
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(normalized),
        phone_lookup=phone_lookup(normalized),
        phone_masked=mask_phone(normalized),
        nickname=nickname,
    )
    session.add(user)
    session.flush()
    return user.id


def submit(
    session: Any,
    *,
    user_id: str,
    title: str,
    body: str = "这个页面的按钮点了没有反应，重复三次都一样，希望修复。",
    page_path: str = "/shop/demo",
    category: str = "bug",
    now: int | None = None,
) -> Feedback:
    return feedback_service.create_feedback(
        session,
        user_id=user_id,
        category=category,
        title=title,
        body=body,
        page_path=page_path,
        now=now,
    )


def review(
    session: Any,
    *,
    feedback_id: str,
    reviewer_id: str = "stf_reviewer",
    result: str = "RESOLVED",
    reason: str = "已确认问题并修复，符合奖励标准。",
    reward_level: str | None = None,
    impact: str | None = None,
    criteria: str | None = "可复现的缺陷，判据为操作步骤与截图一致。",
    duplicate_of: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    return feedback_service.review_feedback(
        session,
        feedback_id=feedback_id,
        reviewer_id=reviewer_id,
        result=result,
        reason=reason,
        reward_level=reward_level,
        impact=impact,
        criteria=criteria,
        duplicate_of=duplicate_of,
        now=now,
    )


def ledger_rows(session: Any, user_id: str) -> list[PointLedger]:
    return list(
        session.scalars(
            sa.select(PointLedger).where(PointLedger.user_id == user_id).order_by(PointLedger.created_at)
        )
    )


# ------------------------------------------------------------------ 输入清洗
def test_page_path_drops_sensitive_query() -> None:
    """page_path 只保留本站相对路径，query 含敏感键时整段丢弃。"""
    assert feedback_service.sanitize_page_path("/shop/demo?token=abc&page=2") == "/shop/demo"
    assert feedback_service.sanitize_page_path("/shop/demo?page=2&keyword=龙") == "/shop/demo?page=2&keyword=%E9%BE%99"
    assert feedback_service.sanitize_page_path("/account/orders") == "/account/orders"
    assert feedback_service.sanitize_page_path(None) is None
    for bad in ("https://evil.example/x", "//evil.example/x", "/\\evil", "/x?email=a@b.com"):
        if bad == "/x?email=a@b.com":
            assert feedback_service.sanitize_page_path(bad) == "/x"
            continue
        with pytest.raises(AppError) as excinfo:
            feedback_service.sanitize_page_path(bad)
        assert excinfo.value.code == "VALIDATION_ERROR"


# ------------------------------------------------------------------ TC-F01
def test_tc_f01_valid_negative_feedback_is_rewarded(db: Any) -> None:
    """有效但负面的反馈可以获奖；审核记录影响与判据。"""
    user_id = make_user(db)
    row = submit(
        db,
        user_id=user_id,
        title="结算页优惠券无法使用",
        body="优惠券输入后提示无效，体验很差，非常失望，但这是真实缺陷，请修复。",
    )
    db.commit()
    assert row.status == "SUBMITTED"
    assert row.reward_status == "PENDING_REVIEW", "提交时不能给出确定积分"

    result = review(db, feedback_id=row.id, reward_level="normal")
    db.commit()
    assert result["reward_status"] == "GRANTED"
    assert result["reward_points"] == 20
    assert result["granted_delta"] == 20
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20
    rows = ledger_rows(db, user_id)
    assert [entry.delta for entry in rows] == [20]
    assert "订单" not in rows[0].description and "优惠券" not in rows[0].description, "流水不得泄露反馈正文"
    review_row = db.scalars(
        sa.select(FeedbackReview).where(FeedbackReview.feedback_id == row.id)
    ).one()
    assert review_row.criteria and review_row.reviewer_id == "stf_reviewer"
    notifications = list(
        db.scalars(sa.select(Notification).where(Notification.user_id == user_id))
    )
    assert any(item.category == "feedback" for item in notifications)


def test_tc_f01_low_value_reports_are_not_rewarded(db: Any) -> None:
    """无价值刷屏不奖励：重复内容被拒，无判据的处理结论标为 NOT_ELIGIBLE。"""
    user_id = make_user(db)
    first = submit(db, user_id=user_id, title="按钮没反应")
    db.commit()
    with pytest.raises(AppError) as excinfo:
        submit(db, user_id=user_id, title="按钮没反应")
    assert excinfo.value.code == "FEEDBACK_DUPLICATE"

    result = review(db, feedback_id=first.id, reward_level=None)
    db.commit()
    assert result["reward_status"] == "NOT_ELIGIBLE"
    assert result["reward_points"] == 0
    assert ledger_rows(db, user_id) == []
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 0


def test_tc_f01_duplicate_and_reject_need_readable_reason(db: Any) -> None:
    """DUPLICATE / REJECTED 必须给用户可读理由；重复反馈关联匿名摘要。"""
    user_id = make_user(db)
    original = submit(db, user_id=user_id, title="搜索结果为空", page_path="/search")
    other = make_user(db, phone="13800000003", nickname="另一位星友")
    duplicate = submit(db, user_id=other, title="搜索结果为空", page_path="/search")
    db.commit()

    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=duplicate.id, result="DUPLICATE", reason="重复", duplicate_of=original.id)
    assert excinfo.value.code == "VALIDATION_ERROR"
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=duplicate.id, result="DUPLICATE", reason="这条反馈与其他反馈重复，请查看原反馈进度。")
    assert excinfo.value.code == "VALIDATION_ERROR"  # 缺 duplicate_of

    result = review(
        db,
        feedback_id=duplicate.id,
        result="DUPLICATE",
        reason="该问题已由更早的反馈记录，后续进展请看原反馈。",
        duplicate_of=original.id,
    )
    db.commit()
    assert result["status"] == "DUPLICATE"
    refreshed = db.get(Feedback, duplicate.id)
    assert refreshed.duplicate_summary and original.id in refreshed.duplicate_summary
    assert user_id not in (refreshed.duplicate_summary or ""), "匿名摘要不得公开原提交者"

    # 否定式结论同样需要理由
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=first_pending(db, other), result="REJECTED", reason="不奖")
    assert excinfo.value.code == "VALIDATION_ERROR"


def first_pending(db: Any, user_id: str) -> str:
    row = submit(db, user_id=user_id, title="无法上传头像", page_path="/account/profile")
    db.commit()
    return row.id


def test_tc_f01_reward_cannot_demand_removing_negative_review(db: Any) -> None:
    """不能以删除差评、给好评或只保留正面内容作为奖励条件。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="物流信息不更新")
    db.commit()
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=row.id, reward_level="minor", reason="删除差评后即可获得奖励。")
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_tc_f01_appeal_has_reason_and_status(db: Any) -> None:
    """申诉要有理由与状态，同一反馈的未结申诉只保留一条。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="收藏夹丢失")
    db.commit()
    with pytest.raises(AppError) as excinfo:
        feedback_service.create_appeal(db, user_id=user_id, feedback_id=row.id, reason="不服")
    assert excinfo.value.code == "VALIDATION_ERROR"

    appeal = feedback_service.create_appeal(
        db, user_id=user_id, feedback_id=row.id, reason="补充了新的复现视频，请复核奖励结论。"
    )
    db.commit()
    assert appeal.status == "SUBMITTED"
    payload = feedback_service.serialize_appeal(appeal)
    assert payload["reason"].startswith("补充了新的复现视频")
    again = feedback_service.create_appeal(
        db, user_id=user_id, feedback_id=row.id, reason="再次提交同样的申诉理由，用于验证幂等。"
    )
    assert again.id == appeal.id
    detail = feedback_service.get_feedback_detail(db, user_id=user_id, feedback_id=row.id)
    assert detail["appeal_endpoint"].endswith(f"/feedback/{row.id}/appeals")


def test_tc_f01_self_review_forbidden(db: Any) -> None:
    """审核人不能审核自己提交的反馈；运营登记的关联账号同样被拒。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="页面错别字")
    db.commit()
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=row.id, reviewer_id=user_id, reward_level="minor")
    assert excinfo.value.code == "FORBIDDEN"

    db.add(
        SiteSetting(
            id=new_id("content"),
            key="feedback.reviewer_related_user_ids",
            value=[user_id],
            group="feedback",
        )
    )
    db.commit()
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=row.id, reviewer_id="stf_related", reward_level="minor")
    assert excinfo.value.code == "FORBIDDEN"


def test_tc_f01_internal_notes_never_exposed(db: Any) -> None:
    """内部备注与用户可见回复严格区分。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="发票申请失败")
    db.add(
        FeedbackMessage(
            id=new_id("message"),
            feedback_id=row.id,
            author_type="staff",
            author_id="stf_reviewer",
            body="内部备注：怀疑是渠道配置问题，先不要承诺时间。",
            visibility="internal",
        )
    )
    db.add(
        FeedbackMessage(
            id=new_id("message"),
            feedback_id=row.id,
            author_type="staff",
            author_id="stf_reviewer",
            body="已收到，我们会在两个工作日内回复。",
            visibility="user",
        )
    )
    db.commit()
    detail = feedback_service.get_feedback_detail(db, user_id=user_id, feedback_id=row.id)
    bodies = [message["body"] for message in detail["messages"]]
    assert bodies == ["已收到，我们会在两个工作日内回复。"]
    assert "内部备注" not in str(detail)


def test_tc_f01_withdraw_keeps_granted_points(db: Any) -> None:
    """撤回反馈不自动删除已发积分，确认作弊才通过复核冲正。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="优惠券过期时间显示错误")
    db.commit()
    review(db, feedback_id=row.id, reward_level="normal")
    db.commit()
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20

    feedback_service.withdraw_feedback(db, user_id=user_id, feedback_id=row.id, reason="自己已解决")
    db.commit()
    refreshed = db.get(Feedback, row.id)
    assert refreshed.status == "CLOSED"
    assert refreshed.reward_status == "GRANTED"
    assert refreshed.reward_points == 20
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20


def test_tc_f01_submit_rate_limits(db: Any) -> None:
    """每天 10 条、每小时 3 条：超出抛 FEEDBACK_LIMIT_EXCEEDED。"""
    user_id = make_user(db)
    moment = shanghai_ms(2024, 3, 1, 10, 5)
    for index in range(3):
        submit(db, user_id=user_id, title=f"第 {index} 个问题", now=moment + index * 1_000)
    db.commit()
    with pytest.raises(AppError) as excinfo:
        submit(db, user_id=user_id, title="第 4 个问题", now=moment + 4_000)
    assert excinfo.value.code == "FEEDBACK_LIMIT_EXCEEDED"
    # 换一个小时窗口即可继续提交
    later = submit(db, user_id=user_id, title="换小时后的反馈", now=moment + 3_600_000)
    db.commit()
    assert later.status == "SUBMITTED"


# ------------------------------------------------------------------ TC-F02
def test_tc_f02_duplicate_approval_grants_once(db: Any) -> None:
    """同一来源重复批准只产生一次奖励（UNIQUE(source_type,source_id) + 条件 UPDATE）。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="支付回调延迟")
    db.commit()
    first = review(db, feedback_id=row.id, reward_level="normal")
    db.commit()
    second = review(db, feedback_id=row.id, reward_level="normal", reviewer_id="stf_reviewer_2")
    db.commit()
    assert first["granted_delta"] == 20
    assert second["granted_delta"] == 0
    assert len(ledger_rows(db, user_id)) == 1
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20
    awards = list(db.scalars(sa.select(PointAward).where(PointAward.source_id == row.id)))
    assert len(awards) == 1


def test_tc_f02_concurrent_approval_grants_once(db: Any) -> None:
    """两个审核人并发批准：只有一个奖励来源与一条入账流水。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="页面在 Safari 下错位")
    db.commit()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(reviewer: str) -> None:
        def run() -> None:
            session = platform_db.get_session_factory()()
            try:
                session.execute(sa.text("BEGIN IMMEDIATE"))
                review(session, feedback_id=row.id, reward_level="normal", reviewer_id=reviewer)
                session.commit()
            finally:
                session.close()

        try:
            barrier.wait(timeout=10)
            platform_db.retry_on_busy(run, attempts=4)
        except BaseException as exc:  # noqa: BLE001 - 并发异常需要被断言发现
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("stf_a", "stf_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"并发审核不应报错：{errors}"
    db.expire_all()
    assert len(ledger_rows(db, user_id)) == 1
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20
    assert len(list(db.scalars(sa.select(PointAward).where(PointAward.source_id == row.id)))) == 1


def test_tc_f02_level_upgrade_only_pays_difference(db: Any) -> None:
    """补充关键新证据可升级同一反馈的奖励，只补差额。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="导出文件缺少页眉")
    db.commit()
    minor = review(db, feedback_id=row.id, reward_level="minor")
    db.commit()
    assert minor["reward_points"] == 5

    upgrade = review(
        db,
        feedback_id=row.id,
        reward_level="normal",
        reason="用户补充了完整复现步骤，升级为有效可复现缺陷。",
        reviewer_id="stf_lead",
    )
    db.commit()
    assert upgrade["reward_level"] == "normal"
    assert upgrade["reward_points"] == 20
    assert upgrade["granted_delta"] == 15, "升级只补差额"
    assert [entry.delta for entry in ledger_rows(db, user_id)] == [5, 15]
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20


def test_tc_f02_major_requires_impact_and_criteria(db: Any) -> None:
    """major 等级必须记录影响与判据。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="订单金额显示错误导致误付")
    db.commit()
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=row.id, reward_level="major", impact=None)
    assert excinfo.value.code == "VALIDATION_ERROR"

    result = review(
        db,
        feedback_id=row.id,
        reward_level="major",
        impact="影响全部在线支付用户，可能造成多付款与退款工单。",
    )
    db.commit()
    assert result["reward_points"] == 50


def test_tc_f02_revoke_reward_appends_reversal(db: Any) -> None:
    """确认作弊走复核冲正：追加反向流水，原流水保留。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="重复领取活动奖励")
    db.commit()
    review(db, feedback_id=row.id, reward_level="normal")
    db.commit()
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 20

    result = feedback_service.revoke_reward(
        db, feedback_id=row.id, reviewer_id="stf_lead", reason="复核确认同一问题重复领取，需冲正。"
    )
    db.commit()
    assert result["reward_status"] == "REVOKED"
    assert result["granted_delta"] == -20
    rows = ledger_rows(db, user_id)
    assert [entry.entry_type for entry in rows] == ["AWARD", "REVERSAL"]
    assert [entry.delta for entry in rows] == [20, -20]
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 0


# ------------------------------------------------------------------ TC-F03
def test_tc_f03_daily_limit_uses_shanghai_day(db: Any) -> None:
    """每人每天最多奖励 1 条，边界按 Asia/Shanghai 自然日。"""
    user_id = make_user(db)
    jan1_2330 = shanghai_ms(2024, 1, 1, 23, 30)
    jan1_2359 = shanghai_ms(2024, 1, 1, 23, 59)
    jan2_0030 = shanghai_ms(2024, 1, 2, 0, 30)

    first = submit(db, user_id=user_id, title="问题 A", now=jan1_2330)
    db.commit()
    granted = review(db, feedback_id=first.id, reward_level="normal", now=jan1_2330)
    db.commit()
    assert granted["reward_status"] == "GRANTED"

    second = submit(db, user_id=user_id, title="问题 B", now=jan1_2359)
    db.commit()
    blocked = review(db, feedback_id=second.id, reward_level="normal", now=jan1_2359)
    db.commit()
    assert blocked["reward_status"] == "NOT_ELIGIBLE"
    assert blocked["reward_points"] == 0
    assert "今日奖励名额已用完" in (blocked["reward_note"] or "")

    # 同一反馈当天升级不额外占用名额
    upgraded = review(db, feedback_id=first.id, reward_level="major", impact="影响面扩大", now=jan1_2359)
    db.commit()
    assert upgraded["reward_status"] == "GRANTED"
    assert upgraded["reward_points"] == 50

    # 跨到上海自然日的次日：名额恢复
    third = submit(db, user_id=user_id, title="问题 C", now=jan2_0030)
    db.commit()
    next_day = review(db, feedback_id=third.id, reward_level="minor", now=jan2_0030)
    db.commit()
    assert next_day["reward_status"] == "GRANTED"
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 55


def test_tc_f03_no_monthly_cap(db: Any) -> None:
    """每月无上限：1 月内连续 8 天各奖励 50 分（合计 400 > 旧草案 300 分上限）全部发放。"""
    user_id = make_user(db)
    balance = 0
    for day in range(1, 9):
        moment = shanghai_ms(2024, 1, day, 12, 0)
        row = submit(db, user_id=user_id, title=f"一月第 {day} 天的建议", now=moment)
        db.commit()
        result = review(
            db,
            feedback_id=row.id,
            reward_level="major",
            impact="影响所有使用该流程的用户。",
            now=moment,
        )
        db.commit()
        assert result["reward_status"] == "GRANTED", f"第 {day} 天不应被月度上限拦截"
        balance += 50
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == balance == 400


def test_tc_f03_budget_exhausted_still_allows_submission(db: Any) -> None:
    """预算耗尽仍可正常提交反馈，只是本次不再获奖，并在响应与详情里说明原因。"""
    user_id = make_user(db)
    moment = shanghai_ms(2024, 2, 5, 9, 0)
    first = submit(db, user_id=user_id, title="问题甲", now=moment)
    db.commit()
    review(db, feedback_id=first.id, reward_level="minor", now=moment)
    db.commit()

    later = shanghai_ms(2024, 2, 5, 10, 30)  # 同一天但换一个小时窗口
    second = submit(db, user_id=user_id, title="问题乙", now=later)
    db.commit()
    assert second.status == "SUBMITTED", "达到奖励上限不影响继续提交"

    result = review(db, feedback_id=second.id, reward_level="normal", now=later)
    db.commit()
    assert result["reward_status"] == "NOT_ELIGIBLE"
    detail = feedback_service.get_feedback_detail(db, user_id=user_id, feedback_id=second.id)
    assert "今日奖励名额已用完" in (detail["reward_note"] or "")
    assert loyalty_service.account_view(db, user_id=user_id)["posted_balance"] == 5


def test_review_state_machine_and_repo_helpers(db: Any) -> None:
    """状态机限制非法流转；复审版本单调递增。"""
    user_id = make_user(db)
    row = submit(db, user_id=user_id, title="页面加载很慢")
    db.commit()
    assert feedback_repo.latest_review(db, feedback_id=row.id) is None
    with pytest.raises(AppError) as excinfo:
        review(db, feedback_id=row.id, result="CLOSED", reason="未分诊就直接关闭不符合状态机。")
    assert excinfo.value.code == "STATE_CONFLICT"
    review(db, feedback_id=row.id, result="TRIAGED", reason="已分诊，等待排期。", now=shanghai_ms(2024, 4, 1, 9))
    db.commit()
    review(db, feedback_id=row.id, result="RESOLVED", reason="已修复并验证。", now=shanghai_ms(2024, 4, 1, 11))
    db.commit()
    review(db, feedback_id=row.id, result="CLOSED", reason="用户已确认。", now=shanghai_ms(2024, 4, 1, 12))
    db.commit()
    latest = feedback_repo.latest_review(db, feedback_id=row.id)
    assert latest is not None and latest.revision == 3
    assert db.get(Feedback, row.id).status == "CLOSED"
