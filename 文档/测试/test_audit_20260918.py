"""审计复现：从 server 执行 uv run pytest ../文档/测试/test_audit_20260918.py -p tests.conftest -v。

断言是需求预期；当前失败代表缺陷复现，不是通过验收。仅使用临时库与沙盒。
"""
import sqlalchemy as sa
import pytest
from pydantic import ValidationError


def test_payment_disabled_rejects_creation(customer, payment_db, commerce, sandbox_channel, payment_settings):
    from app.modules.content.service import feature_flags
    sandbox_channel("sandbox")
    _, installments = commerce.create_order(user_id=customer.user_id, amounts=[10000])
    payment_db.commit()
    assert feature_flags(payment_db)["online_payment"] is False
    response = customer.create_attempt(installments[0].id, idempotency_key="audit-disabled-payment")
    assert response.status_code == 403, f"disabled payment returned HTTP {response.status_code}"
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_production_rejects_sandbox_alias():
    from app.platform.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", public_origin="https://starium.cn",
                 session_secret="x" * 40, csrf_secret="y" * 40, otp_secret="z" * 40,
                 phone_lookup_key="p" * 40, pii_encryption_key="q" * 40,
                 cookie_secure=True, sms_adapter="aliyun", payment_providers="sandbox",
                 storage_backend="s3", smtp_host="smtp.example.test")


def test_business_notification_creates_email_delivery(db):
    from tests.conftest import create_user
    from app.modules.commissions.ports import notify_customer
    from app.modules.notifications.models import Delivery, Notification
    user = create_user(db)
    notify_customer(db, user_id=user.id, event_type="quote.published", title="测试报价",
                    body="审计测试", event_id="audit-email",
                    payload={"email": "audit@example.test"})
    db.flush()
    assert db.scalar(sa.select(sa.func.count()).select_from(Notification)) == 1
    assert db.scalar(sa.select(sa.func.count()).select_from(Delivery)) == 1, "notification exists but email delivery missing"


def test_invalid_policy_rejected_at_write(db):
    from app.modules.content.service import ensure_default_settings, update_setting
    from app.modules.content.repository import get_setting
    from app.platform.errors import AppError
    ensure_default_settings(db)
    row = get_setting(db, "commission.policy")
    with pytest.raises(AppError):
        update_setting(db, key=row.key, value={"prepayment_percent": "invalid"},
                       version=row.version, actor_id=None, reason="audit")


def test_disabled_coupons_do_not_apply_default_discount(db):
    from app.modules.content.service import feature_flags
    from app.modules.commerce.service import commerce_policy, coupon_discount_cents
    assert feature_flags(db)["coupons"] is False
    discount, code = coupon_discount_cents(commerce_policy(db), coupon_code="NEIXING10", goods_cents=10000)
    assert discount == 0, f"disabled coupon {code} reduced amount by {discount} cents"


def test_unassigned_maker_cannot_read_project(db, client):
    from tests.conftest import create_staff, admin_session
    from tests.integration.test_commission_flow import (
        login, make_series, make_asset, create_submitted_inquiry, publish_quote, accept_payload,
    )
    owner = login(db, "审计客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)
    inquiry = create_submitted_inquiry(client, db, owner, series, asset)
    quote = publish_quote(db, inquiry_id=inquiry)
    response = client.post(f"/api/v1/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers())
    assert response.status_code in (200, 201), response.text
    project_id = response.json()["project_id"]
    staff, secret = create_staff(db, login_name="audit-maker", roles=["maker"])
    db.commit()
    admin_session(db, client, staff, secret)
    response = client.get(f"/api/v1/admin/commission-projects/{project_id}")
    assert response.status_code in (403, 404), f"unassigned maker got HTTP {response.status_code}"
