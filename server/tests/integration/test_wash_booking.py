"""洗毛预约、道具/周边分类过滤（FR-025 / C31–C33）。

覆盖：

- `/wash` 预约要求登录，期望日期为北京时间今日 +2..+60 天；
- cleaning 申请必须绑定 service_kind=cleaning 的商品，且不占用库存；
- 预约字段写入响应与后台列表，审批后才可使用购买窗口；
- props/merch 分类过滤与 facets 枚举。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.catalog import repository as catalog_repo
from app.modules.catalog.models import Product, Sku
from tests.conftest import (
    BASE_ORIGIN,
    admin_session,
    create_product,
    create_sku,
    create_staff,
    create_user,
    issue_login,
)

TZ = ZoneInfo("Asia/Shanghai")


def _login(db: Session, client: TestClient, nickname: str = "洗毛客户"):
    user = create_user(db, nickname=nickname, phone="13800007777")
    db.commit()
    csrf = issue_login(db, client, user)
    return user, {"Origin": BASE_ORIGIN, "X-CSRF-Token": csrf}


@pytest.fixture()
def wash_product(db: Session) -> Product:
    product = create_product(
        db, slug="wash-full", title="全装洗毛", kind="service", requires_approval=True
    )
    product.category = "service"
    product.service_kind = "cleaning"
    db.flush()
    create_sku(
        db,
        product_id=product.id,
        price_cents=39800,
        stock_on_hand=3,
        sku_code="WASH-TEST-FULL",
        label="全装套餐",
    )
    db.commit()
    return product


def _date(days: int) -> str:
    return (datetime.now(TZ).date() + timedelta(days=days)).isoformat()


def _payload(product: Product, **overrides: object) -> dict:
    payload = {
        "product_id": product.id,
        "kind": "cleaning",
        "reason": "全装需要拆洗与毛面复形，预约周末到店。",
        "preferred_date": _date(7),
        "return_method": "pickup",
    }
    payload.update(overrides)
    return payload


def test_wash_booking_requires_login(client: TestClient, wash_product: Product) -> None:
    response = client.post("/api/v1/stock-applications", json=_payload(wash_product))
    assert response.status_code == 401, response.text


def test_wash_booking_date_window_and_return_method(
    client: TestClient, db: Session, wash_product: Product
) -> None:
    _user, headers = _login(db, client)

    too_soon = client.post(
        "/api/v1/stock-applications", json=_payload(wash_product, preferred_date=_date(1)), headers=headers
    )
    assert too_soon.status_code == 422, too_soon.text
    assert too_soon.json()["error"]["code"] == "BOOKING_DATE_INVALID"

    too_far = client.post(
        "/api/v1/stock-applications", json=_payload(wash_product, preferred_date=_date(61)), headers=headers
    )
    assert too_far.status_code == 422, too_far.text
    assert too_far.json()["error"]["code"] == "BOOKING_DATE_INVALID"

    malformed = client.post(
        "/api/v1/stock-applications",
        json=_payload(wash_product, preferred_date="2026-02-31"),
        headers=headers,
    )
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["error"]["code"] == "BOOKING_DATE_INVALID"

    no_method = client.post(
        "/api/v1/stock-applications", json=_payload(wash_product, return_method=None), headers=headers
    )
    assert no_method.status_code == 422, no_method.text
    assert no_method.json()["error"]["code"] == "VALIDATION_ERROR"
    assert no_method.json()["error"]["fields"]["return_method"]


def test_wash_booking_records_fields_without_reserving_stock(
    client: TestClient, db: Session, wash_product: Product
) -> None:
    _user, headers = _login(db, client)
    wanted = _date(2)
    created = client.post(
        "/api/v1/stock-applications",
        json=_payload(wash_product, preferred_date=wanted, return_method="ship"),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "cleaning"
    assert body["status"] == "SUBMITTED"
    assert body["preferred_date"] == wanted
    assert body["return_method"] == "ship"
    assert body["purchase_expires_at"] is None

    sku = db.query(Sku).filter(Sku.sku_code == "WASH-TEST-FULL").one()
    assert (sku.stock_on_hand, sku.reserved) == (3, 0), "预约不得占用库存"

    duplicate = client.post(
        "/api/v1/stock-applications", json=_payload(wash_product), headers=headers
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "STATE_CONFLICT"

    mine = client.get("/api/v1/stock-applications", headers=headers)
    assert mine.status_code == 200, mine.text
    assert mine.json()[0]["preferred_date"] == wanted


def test_wash_fields_rejected_for_other_application_kinds(
    client: TestClient, db: Session, wash_product: Product
) -> None:
    _user, headers = _login(db, client, nickname="维修客户")
    response = client.post(
        "/api/v1/stock-applications",
        json=_payload(wash_product, kind="repair"),
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cleaning_kind_requires_cleaning_service_product(
    client: TestClient, db: Session
) -> None:
    product = create_product(db, slug="repair-service", title="维修服务", kind="service")
    product.service_kind = "repair"
    db.commit()
    _user, headers = _login(db, client, nickname="错误分类客户")
    response = client.post(
        "/api/v1/stock-applications", json=_payload(product), headers=headers
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["fields"]["product_id"]


def test_admin_list_exposes_booking_fields(
    client: TestClient, db: Session, wash_product: Product
) -> None:
    _user, headers = _login(db, client, nickname="后台可见客户")
    wanted = _date(10)
    created = client.post(
        "/api/v1/stock-applications",
        json=_payload(wash_product, preferred_date=wanted, return_method="pickup"),
        headers=headers,
    )
    assert created.status_code == 201, created.text

    staff, secret = create_staff(db, login_name="wash-ops", roles=["content_editor"])
    db.commit()
    session = admin_session(db, client, staff, secret)
    response = client.get(
        "/api/v1/admin/stock-applications",
        params={"kind": "cleaning"},
        headers={"X-CSRF-Token": session["csrf_token"], "Origin": BASE_ORIGIN},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["preferred_date"] == wanted
    assert item["return_method"] == "pickup"


def test_props_and_merch_category_filters(client: TestClient, db: Session) -> None:
    for slug, category, title in (
        ("prop-horn", "props", "发光角饰"),
        ("merch-badge", "merch", "星绒徽章"),
        ("stock-tail", "accessory", "尾巴配件"),
    ):
        product = create_product(db, slug=slug, title=title, kind="stock_goods")
        product.category = category
        create_sku(db, product_id=product.id, price_cents=10000)
    service = create_product(db, slug="care-service", title="保养服务", kind="service")
    service.category = "service"
    service.service_kind = "cleaning"
    create_sku(db, product_id=service.id, price_cents=16800)
    character = create_product(db, slug="adopt-character", title="角色汤角色", kind="exclusive_character")
    character.category = "character"
    create_sku(db, product_id=character.id, price_cents=2400000)
    db.commit()

    props = client.get("/api/v1/products", params={"category": "props"})
    assert props.status_code == 200, props.text
    assert [item["slug"] for item in props.json()["items"]] == ["prop-horn"]

    merch = client.get("/api/v1/products", params={"category": "merch"})
    assert merch.status_code == 200, merch.text
    assert [item["slug"] for item in merch.json()["items"]] == ["merch-badge"]

    merged = client.get(
        "/api/v1/products",
        params={"exclude_kinds": "service,exclusive_character"},
    )
    assert merged.status_code == 200, merged.text
    assert {item["slug"] for item in merged.json()["items"]} == {"prop-horn", "merch-badge", "stock-tail"}

    facets = client.get("/api/v1/products/facets")
    assert facets.status_code == 200, facets.text
    facet_values = [item["value"] for item in facets.json()["categories"]]
    assert {"props", "merch"}.issubset(set(facet_values))

    empty = client.get("/api/v1/products", params={"category": "not-a-category"})
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == 0

    # 目录仓储同样支持分类过滤，后台与专题页共用同一查询。
    rows, total = catalog_repo.list_products(db, category="props", limit=10, offset=0)
    assert total == 1 and rows[0].slug == "prop-horn"

    merged_rows, merged_total = catalog_repo.list_products(
        db,
        exclude_kinds=["service", "exclusive_character"],
        limit=10,
        offset=0,
    )
    assert merged_total == 3
    assert {row.slug for row in merged_rows} == {"prop-horn", "merch-badge", "stock-tail"}
