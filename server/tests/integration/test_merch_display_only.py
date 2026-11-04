"""周边只展示不售卖：前台隐藏入口，服务端仍必须拒绝交易。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commerce import service as commerce_service
from app.platform.errors import AppError
from tests.conftest import create_product, create_sku, create_user, issue_login


def _merch_product(db: Session):
    product = create_product(
        db,
        slug="merch-display-only",
        title="周边展示品（演示内容）",
        kind="stock_goods",
        status="published",
        requires_approval=False,
        terms_version="v1",
    )
    product.category = "merch"
    sku = create_sku(
        db,
        product_id=product.id,
        price_cents=3800,
        stock_on_hand=10,
        sku_code="MERCH-DISPLAY",
    )
    db.flush()
    return product, sku


def test_cart_rejects_merch(client: TestClient, db: Session) -> None:
    product, sku = _merch_product(db)
    user = create_user(db, nickname="周边访客", phone="13800009991")
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf

    response = client.put(f"/api/v1/cart/items/{sku.id}", json={"quantity": 1})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "MERCH_DISPLAY_ONLY"
    assert product.category == "merch"


def test_checkout_line_rejects_merch(db: Session) -> None:
    _, sku = _merch_product(db)
    with pytest.raises(AppError) as caught:
        commerce_service._line_for_sku(db, sku.id, 1)
    assert caught.value.code == "MERCH_DISPLAY_ONLY"


def test_non_merch_cart_still_works(client: TestClient, db: Session) -> None:
    product = create_product(
        db,
        slug="props-not-merch",
        title="道具（演示内容）",
        kind="stock_goods",
        status="published",
        requires_approval=False,
        terms_version="v1",
    )
    product.category = "props"
    sku = create_sku(
        db,
        product_id=product.id,
        price_cents=12800,
        stock_on_hand=5,
        sku_code="PROP-OK",
    )
    user = create_user(db, nickname="道具访客", phone="13800009992")
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf

    response = client.put(f"/api/v1/cart/items/{sku.id}", json={"quantity": 1})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["sellable"] is True
