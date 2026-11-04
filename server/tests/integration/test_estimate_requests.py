"""公开参考估价（FR-026）：无需登录、联系方式加密、后台只改状态。"""
from __future__ import annotations

import sqlalchemy as sa

from app.dev_seed import seed_demo
from app.modules.commissions.models import EstimateRequest
from app.platform.security import decrypt_pii
from tests.conftest import BASE_ORIGIN, admin_session, create_staff


def _payload(**overrides):
    data = {
        "name": "估价测试",
        "contact": "buyer@example.com",
        "contact_type": "email",
        "series_code": "FULL",
        "style": "standard",
        "options": ["tail"],
        "character_name": "星绒测试角色",
        "species": "龙",
        "note": "希望展会前完成",
        "consent": True,
        "website": "",
    }
    data.update(overrides)
    return data


def test_public_estimate_options_and_submit(db, client):
    seed_demo(db)
    db.commit()
    options = client.get("/api/v1/estimates/options")
    assert options.status_code == 200, options.text
    body = options.json()
    assert body["range_high_percent"] == 125
    codes = {item["code"] for item in body["series"]}
    assert "FULL" in codes
    assert next(item for item in body["styles"] if item["value"] == "standard")["factor"] == 100
    assert next(item for item in body["options"] if item["value"] == "tail")["price_cents"] == 120000

    response = client.post("/api/v1/estimates", json=_payload(), headers={"Origin": BASE_ORIGIN})
    assert response.status_code == 201, response.text
    created = response.json()
    # FULL 起价 1,800,000 + 尾巴 120,000 = 1,920,000；上限 125%。
    assert created["estimate_low_cents"] == 1920000
    assert created["estimate_high_cents"] == 2400000
    assert created["currency"] == "CNY"
    assert created["response_workdays"] == 3

    row = db.scalar(sa.select(EstimateRequest).where(EstimateRequest.id == created["id"]))
    assert row is not None
    assert "buyer@example.com" not in row.contact_ciphertext
    assert decrypt_pii(row.contact_ciphertext) == "buyer@example.com"
    assert row.contact_hint == "b***@example.com"


def test_public_estimate_validation_and_honeypot(db, client):
    seed_demo(db)
    db.commit()
    bad_contact = client.post(
        "/api/v1/estimates",
        json=_payload(contact="not-an-email"),
        headers={"Origin": BASE_ORIGIN},
    )
    assert bad_contact.status_code == 422
    assert bad_contact.json()["error"]["fields"]["contact"]

    no_consent = client.post(
        "/api/v1/estimates",
        json=_payload(consent=False),
        headers={"Origin": BASE_ORIGIN},
    )
    assert no_consent.status_code == 422
    assert no_consent.json()["error"]["fields"]["consent"]

    before = db.scalar(sa.select(sa.func.count()).select_from(EstimateRequest))
    honeypot = client.post(
        "/api/v1/estimates",
        json=_payload(website="https://spam.example"),
        headers={"Origin": BASE_ORIGIN},
    )
    assert honeypot.status_code == 201
    after = db.scalar(sa.select(sa.func.count()).select_from(EstimateRequest))
    assert after == before


def test_public_estimate_rate_limit(db, client):
    seed_demo(db)
    db.commit()
    for index in range(5):
        response = client.post(
            "/api/v1/estimates",
            json=_payload(contact=f"buyer{index}@example.com"),
            headers={"Origin": BASE_ORIGIN},
        )
        assert response.status_code == 201, response.text
    limited = client.post(
        "/api/v1/estimates",
        json=_payload(contact="buyer9@example.com"),
        headers={"Origin": BASE_ORIGIN},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_admin_estimate_list_and_status(db, client):
    seed_demo(db)
    db.commit()
    created = client.post(
        "/api/v1/estimates", json=_payload(), headers={"Origin": BASE_ORIGIN}
    )
    assert created.status_code == 201, created.text

    staff, secret = create_staff(db, login_name="estimate-admin", roles=["admin"])
    db.commit()

    listing = client.get("/api/v1/admin/estimates?status=NEW")
    # 无会话时后台接口不能泄露联系方式。
    assert listing.status_code in (401, 403)

    session = admin_session(db, client, staff, secret)
    headers = {"Origin": BASE_ORIGIN, "X-CSRF-Token": session["csrf_token"]}

    listing = client.get("/api/v1/admin/estimates?status=NEW", headers=headers)
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["contact"] == "buyer@example.com"
    assert items[0]["series_title"]

    updated = client.patch(
        f"/api/v1/admin/estimates/{created.json()['id']}",
        json={"status": "CONTACTED", "note": "已邮件回访"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "CONTACTED"

    closed = client.patch(
        f"/api/v1/admin/estimates/{created.json()['id']}",
        json={"status": "CLOSED", "note": "已成交转询价"},
        headers=headers,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"



