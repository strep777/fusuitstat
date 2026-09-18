"""审计缺陷的持续回归，使用真实数据库/路由及明确沙盒通道。"""
import re

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.platform.errors import AppError


@pytest.mark.parametrize("provider", ["mock", "sandbox", " SANDBOX ", "wechat,Mock", "paypal, sandbox"])
def test_production_rejects_all_sandbox_aliases(provider):
    from app.platform.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", public_origin="https://starium.cn",
                 session_secret="x"*40, csrf_secret="y"*40, otp_secret="z"*40,
                 phone_lookup_key="p"*40, pii_encryption_key="q"*40, cookie_secure=True,
                 sms_adapter="aliyun", payment_providers=provider, storage_backend="s3", smtp_host="mail.test")


@pytest.mark.parametrize(("key", "patch"), [
    ("commission.policy", {"prepayment_percent": "invalid"}),
    ("commission.policy", {"prepayment_percent": 101}),
    ("commission.policy", {"quote_valid_days": 0}),
    ("commission.policy", {"slot_hold_minutes": -1}),
    ("commission.policy", {"free_revision_count": True}),
    ("loyalty.rules", {"denominator_yuan": 0}),
    ("loyalty.rules", {"shipping_excluded": "false"}),
    ("platform.feature_flags", {"coupons": "false"}),
    ("commerce.policy", {"shipping": {"flat_cents": -1}}),
    ("commerce.policy", {"coupons": {"X": {"type": "percent", "value": 101}}}),
])
def test_invalid_setting_does_not_mutate_row(db, key, patch):
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings, update_setting
    ensure_default_settings(db)
    row = get_setting(db, key)
    before, version = row.value, row.version
    with pytest.raises(AppError) as error:
        update_setting(db, key=key, value=patch, version=version, actor_id=None, reason="回归")
    assert error.value.code == "VALIDATION_ERROR"
    assert row.value == before and row.version == version


def test_payment_toggle_preserves_existing_queries(customer, payment_db, commerce, sandbox_channel, payment_settings):
    from app.modules.content.models import SiteSetting
    sandbox_channel("sandbox")
    _, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    denied = customer.create_attempt(installments[0].id, idempotency_key="disabled")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FEATURE_DISABLED"
    flag = SiteSetting(key="platform.feature_flags", group="platform", label="测试", value={"online_payment": True})
    payment_db.add(flag)
    payment_db.commit()
    response = customer.create_attempt(installments[0].id, idempotency_key="enabled")
    assert response.status_code == 201, response.text
    attempt_id = response.json()["payment_attempt_id"]
    flag.value = {"online_payment": False}
    payment_db.commit()
    # 已创建交易仍可查单，关闭开关仅禁止新建。
    response = customer.client.get(f"/api/v1/payment-attempts/{attempt_id}", headers=customer.headers())
    assert response.status_code == 200, response.text


def test_missing_email_is_retained_and_verified_recipient_is_used(db, monkeypatch):
    from types import SimpleNamespace

    from app.modules.identity.email import confirm_verification, start_verification
    from app.modules.notifications.models import Delivery, Notification
    from app.modules.notifications.ports import NotificationRequest, notify, resume_missing_recipient
    from app.modules.notifications.service import send_delivery
    from app.platform.security import decrypt_pii
    from tests.conftest import create_user
    user = create_user(db)
    request = NotificationRequest(event_id="email-regression", event_type="quote.published", user_id=user.id,
                                  title="报价", body="请查看报价", payload={"email": "untrusted@example.test"})
    notify(db, request)
    db.flush()
    delivery = db.scalar(sa.select(Delivery))
    assert delivery.status == "FAILED" and delivery.last_error == "RECIPIENT_UNVERIFIED"
    assert decrypt_pii(delivery.target_ciphertext) == ""
    sent = []
    gateway = SimpleNamespace(send=lambda **kwargs: sent.append(kwargs) or SimpleNamespace(message_id="mock", provider="sandbox"))
    monkeypatch.setattr("app.modules.identity.email.get_gateway", lambda: gateway)
    start_verification(db, user_id=user.id, email="verified@example.test")
    code = re.search(r"\d{6}", sent[0]["body"]).group()
    assert not confirm_verification(db, user_id=user.id, code="999999" if code != "999999" else "000000")
    assert confirm_verification(db, user_id=user.id, code=code)
    assert not confirm_verification(db, user_id=user.id, code=code)
    assert resume_missing_recipient(db, user.id) == 1
    db.refresh(delivery)
    assert decrypt_pii(delivery.target_ciphertext) == "verified@example.test"
    monkeypatch.setattr("app.adapters.mail.get_gateway", lambda: gateway)
    send_delivery(db, delivery)
    assert delivery.status == "SENT" and sent[-1]["to"] == "verified@example.test"
    assert notify(db, request) == 0
    assert db.scalar(sa.select(sa.func.count()).select_from(Notification)) == 1


def test_maker_scope_read_write_list_and_reassignment(db, client):
    from app.modules.commissions.models import CommissionProject
    from tests.conftest import admin_session, create_staff
    from tests.integration.test_commission_flow import (
        accept_payload,
        create_submitted_inquiry,
        login,
        make_asset,
        make_series,
        publish_quote,
    )
    owner = login(db, "权限回归客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)
    inquiry = create_submitted_inquiry(client, db, owner, series, asset)
    quote = publish_quote(db, inquiry_id=inquiry)
    response = client.post(f"/api/v1/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers())
    project_id = response.json()["project_id"]
    staff, secret = create_staff(db, login_name="scope-maker", roles=["maker"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    headers = {"X-CSRF-Token": csrf["csrf_token"], "Origin": "http://127.0.0.1:5173"}
    url = f"/api/v1/admin/commission-projects/{project_id}"
    assert client.get(url).status_code == 404
    assert client.get(url+"/change-requests").status_code == 404
    assert client.get("/api/v1/admin/commission-projects", params={"maker_id": "another"}).json()["total"] == 0
    project = db.get(CommissionProject, project_id)
    assert client.patch(url, json={"version": project.version, "maker_id": staff.id}, headers=headers).status_code == 404
    project.maker_id = staff.id
    db.commit()
    assert client.get(url).status_code == 200
    assert client.get("/api/v1/admin/commission-projects", params={"maker_id": "another"}).json()["total"] == 1
    assert client.patch(url, json={"version": project.version, "maker_id": "another"}, headers=headers).status_code == 403
