"""客服工作日时效与逾期提醒回归（R23 / C30）。

依据：
- `功能确认清单-已确认.md` C30：人工客服处理时效为**询价 3 个工作日、反馈 7 个工作日**首响；
  节假日另说明；
- `10_手机号登录安全与合规.md` / `文档/03`：客服工作时段与节假日由配置决定，不能写死；
- N13（节假日客服时效的具体说明文案）**待补** → 本轮只实现「日历可配 + 逾期可算 + 提醒可发」，
  不编造节假日话术，文案字段留空由运营填写。

锁定点：
1. 工作日计算：跳过周末与节假日、把调休上班日算作工作日、起始日当天不计入；
2. 节假日/调休配置经严格校验（日期格式、重复、同一天既是节假日又是调休会被拒）；
3. 询价详情返回承诺工作日数、首响截止时间与是否逾期；工作人员首次响应（发布报价/要求补件/拒单）
   写入首响时间，之后改版报价不覆盖；
4. 逾期提醒按天去重：同一询价同一天只提醒一次，重复扫描不刷屏；未逾期不提醒；
5. 提醒由 worker 的 `support.sla_check` 周期任务执行。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commissions import repository as commission_repo
from app.modules.commissions import service as commission_service
from app.modules.commissions.models import Inquiry
from app.modules.content import service as content_service
from app.modules.content.models import SiteSetting
from app.modules.notifications.models import Notification
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms
from tests.integration.test_commission_flow import login, make_asset, make_series, publish_quote

SHANGHAI = timezone(timedelta(hours=8))


def _ms(year: int, month: int, day: int, hour: int = 10) -> int:
    """上海时区的某个时刻（毫秒）。"""
    return int(datetime(year, month, day, hour, tzinfo=SHANGHAI).timestamp() * 1000)


def _set_calendar(db: Session, **value: object) -> None:
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    row = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "support.calendar"))
    assert row is not None
    row.value = {**(row.value or {}), **value}
    db.commit()


# ------------------------------------------------------------------ 工作日计算
def test_workday_math_skips_weekends_and_holidays(db: Session) -> None:
    """3 个工作日：周五提交 → 周三截止（跳过周末）；节假日顺延。"""
    _set_calendar(db, holidays=[], extra_workdays=[], workdays=[1, 2, 3, 4, 5])
    calendar = content_service.support_calendar(db)

    # 2026-09-18 是周五；+3 个工作日 = 9/21(一)、9/22(二)、9/23(三)
    friday = _ms(2026, 9, 18)
    assert content_service.add_workdays(friday, 3, calendar=calendar) == _ms(2026, 9, 23)

    # 把 9/21 设为节假日：顺延到 9/24（周四）
    _set_calendar(db, holidays=["2026-09-21"])
    calendar = content_service.support_calendar(db)
    assert content_service.add_workdays(friday, 3, calendar=calendar) == _ms(2026, 9, 24)

    # 把周六 9/19 设为调休上班日：9/21 缺勤后仍是 9/23
    _set_calendar(db, holidays=[], extra_workdays=["2026-09-19"])
    calendar = content_service.support_calendar(db)
    assert content_service.add_workdays(friday, 3, calendar=calendar) == _ms(2026, 9, 22)

    # 起始日当天不计入：同一时刻再加 0 个工作日就是它自己
    assert content_service.add_workdays(friday, 0, calendar=calendar) == friday


def test_calendar_setting_validation(db: Session) -> None:
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    db.commit()
    row = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "support.calendar"))
    assert row is not None
    version = int(row.version)

    for patch in (
        {"holidays": ["2026-13-01"]},
        {"holidays": ["2026-10-01", "2026-10-01"]},
        {"workdays": []},
        {"workdays": [1, 1]},
        {"holidays": ["2026-10-01"], "extra_workdays": ["2026-10-01"]},
        {"workdays": [9]},
    ):
        with pytest.raises(AppError) as error:
            update_setting(
                db,
                key="support.calendar",
                value=patch,
                version=version,
                actor_id=None,
                reason="回归",
            )
        assert error.value.code == "VALIDATION_ERROR"
        db.expire_all()

    # 合法配置可以保存
    saved = update_setting(
        db,
        key="support.calendar",
        value={"holidays": ["2026-10-01"], "extra_workdays": ["2026-10-11"]},
        version=version,
        actor_id=None,
        reason="国庆与调休",
    )
    db.commit()
    assert saved.value["holidays"] == ["2026-10-01"]


# ------------------------------------------------------------------ 首响时效与逾期
def _submitted_inquiry(db: Session, *, submitted_at: int) -> Inquiry:
    actor = login(db, "时效客户")
    inquiry = Inquiry(
        id=new_id("inquiry"),
        user_id=actor.user_id,
        status="SUBMITTED",
        series_id=make_series(db),
        character_name="时效角色",
        species="狼",
        suit_type="full",
        requirements="需要可拆尾巴",
        design_asset_ids=[make_asset(db, actor.user_id)],
        rights_declaration=1,
        current_revision=1,
        submitted_at=submitted_at,
    )
    db.add(inquiry)
    db.commit()
    return inquiry


def test_inquiry_detail_reports_sla_and_staff_first_response(client: TestClient, db: Session) -> None:
    _set_calendar(db, holidays=[], extra_workdays=[], workdays=[1, 2, 3, 4, 5])
    inquiry = _submitted_inquiry(db, submitted_at=_ms(2026, 9, 18))

    detail = commission_service.inquiry_detail_dto(db, inquiry)
    assert detail.sla_workdays == 3
    assert detail.response_due_at and detail.response_due_at.startswith("2026-09-23")
    assert detail.first_responded_at is None
    # 截止时间还没到（用固定 now 判定）
    sla = commission_service.inquiry_sla(db, inquiry, now=_ms(2026, 9, 22))
    assert sla["response_overdue"] is False
    sla = commission_service.inquiry_sla(db, inquiry, now=_ms(2026, 9, 24))
    assert sla["response_overdue"] is True

    # 发布报价即首响：写入时间，之后改版报价不覆盖
    quote = publish_quote(db, inquiry_id=inquiry.id)
    db.commit()
    db.expire_all()
    refreshed = commission_repo.get_inquiry(db, inquiry_id=inquiry.id, user_id=inquiry.user_id)
    assert refreshed is not None and refreshed.first_responded_at is not None
    first = refreshed.first_responded_at
    publish_quote(db, inquiry_id=inquiry.id)
    db.commit()
    db.expire_all()
    refreshed = commission_repo.get_inquiry(db, inquiry_id=inquiry.id, user_id=inquiry.user_id)
    assert refreshed is not None and refreshed.first_responded_at == first
    assert refreshed.status == "QUOTED"
    assert quote.quote_version >= 1

    # 客户侧详情也能看到承诺与首响（SLA 字段随详情返回）
    listed = commission_service.inquiry_detail_dto(db, refreshed)
    assert listed.response_due_at is not None and listed.first_responded_at is not None
    assert listed.response_overdue is False


def test_admin_workbench_row_shows_overdue(client: TestClient, db: Session) -> None:
    """运营工作台每行都带首响截止与逾期标记（C30 逾期可见）。"""
    from tests.conftest import admin_session, create_staff

    _set_calendar(db, holidays=[], extra_workdays=[], workdays=[1, 2, 3, 4, 5])
    inquiry = _submitted_inquiry(db, submitted_at=now_ms() - 10 * 24 * 3600 * 1000)
    staff, secret = create_staff(db, login_name="support-ops", roles=["support"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    headers = {"X-CSRF-Token": csrf["csrf_token"], "Origin": "http://127.0.0.1:5173"}

    response = client.get("/api/v1/admin/quotes", headers=headers)
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["items"] if item["id"] == inquiry.id)
    assert row["sla_workdays"] == 3
    assert row["response_due_at"] and row["first_responded_at"] is None
    assert row["response_overdue"] is True
    assert row["submitted_at"] is not None


def test_overdue_reminder_is_once_per_day(db: Session) -> None:
    _set_calendar(db, holidays=[], extra_workdays=[], workdays=[1, 2, 3, 4, 5])
    moment = _ms(2026, 9, 24, 12)
    overdue = _submitted_inquiry(db, submitted_at=_ms(2026, 9, 18))
    # 同一天刚提交的询价：3 个工作日的截止时间还没到，不算逾期
    fresh = _submitted_inquiry(db, submitted_at=moment - 3600 * 1000)

    first = commission_service.remind_overdue_inquiries(db, now=moment)
    db.commit()
    assert first["overdue"] == 1 and first["reminded"] == 1
    assert overdue.id in first["inquiry_ids"] and fresh.id not in first["inquiry_ids"]

    # 同一天再扫：不重复提醒（通知按 (user_id, event_id) 去重）
    second = commission_service.remind_overdue_inquiries(db, now=moment + 3600 * 1000)
    db.commit()
    assert second["overdue"] == 1 and second["reminded"] == 0

    # 跨天仍未处理：**不重复提醒**（同一询价只提醒一次，逾期状态在后台列表可见）
    third = commission_service.remind_overdue_inquiries(db, now=moment + 24 * 3600 * 1000)
    db.commit()
    assert third["overdue"] == 1 and third["reminded"] == 0

    rows = list(
        db.scalars(
            sa.select(Notification).where(Notification.event_type == "support.sla_overdue")
        )
    )
    assert len(rows) == 1, "同一询价只提醒一次，避免每天刷屏"
    assert all(row.user_id is None and row.link_path == "/admin/quotes" for row in rows)
    assert all("首响已逾期" in row.title for row in rows)

    # 已响应的询价不再提醒
    publish_quote(db, inquiry_id=overdue.id)
    db.commit()
    later = commission_service.remind_overdue_inquiries(db, now=moment + 2 * 24 * 3600 * 1000)
    assert overdue.id not in later["inquiry_ids"]


def test_worker_sla_check_task_reports_counts(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.reliability import OutboxEvent
    from app.worker import _HANDLERS

    handler = _HANDLERS["support.sla_check"]
    _set_calendar(db, holidays=[], extra_workdays=[], workdays=[1, 2, 3, 4, 5])
    _submitted_inquiry(db, submitted_at=now_ms() - 10 * 24 * 3600 * 1000)

    event = OutboxEvent(
        id=new_id("event"),
        event_id=new_id("event"),
        event_type="support.sla_check",
        aggregate_id="scheduler",
        aggregate_version=1,
        payload={},
        available_at=now_ms(),
    )
    result = handler(db, event)
    db.commit()
    assert result.startswith("sla:overdue=1"), result
