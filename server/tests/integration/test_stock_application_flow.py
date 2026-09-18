"""掉落审批购买优先级、独占库存与「服务先申请再报价」回归（R20）。

依据：
- `功能确认清单-已确认.md` C28：掉落商品需申请后人工审批，审批通过才可购买；
  审批后生成**审批通过列表**作为购买优先级；**服务先申请再报价**；
- `08_商城订单支付与退款.md`：独占角色只能绑定一个销售 SKU，库存固定为 1；
  同一独占角色不能售出两次（FR-004）；
- `文档/16_需求追踪矩阵.md` 的 FR-004 / FR-011。

本轮把「优先级列表」落成运营可查的排序与筛选（已审批在前、名次小者优先），
把「服务先申请再报价」落成 申请 → 询价（唯一关联）→ 报价 → 接受建单（申请收口）的链路，
并补上「独占角色可售数量不能调成大于 1」的库存约束。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import StockApplication
from app.modules.commerce import repository as commerce_repo
from app.platform.errors import AppError
from app.platform.ids import now_ms
from tests.conftest import admin_session, create_product, create_sku, create_staff, create_user
from tests.integration.test_commission_flow import (
    accept_payload,
    login,
    make_asset,
    make_series,
    publish_quote,
)

SERVICE_REASON = "需要定制一套兽装，先申请再报价。"


def _application(
    db: Session, *, user_id: str, product_id: str, kind: str = "service", status: str = "SUBMITTED"
) -> StockApplication:
    row = StockApplication(
        user_id=user_id,
        product_id=product_id,
        kind=kind,
        status=status,
        reason=SERVICE_REASON,
    )
    db.add(row)
    db.commit()
    return row


def _approve(db: Session, application_id: str, *, rank: int | None = 1, days: int = 7) -> None:
    catalog_service.decide_application(
        db,
        application_id=application_id,
        decision="APPROVED",
        actor_id="stf_catalog",
        reason="资料齐全，同意进入报价。",
        priority_rank=rank,
        purchase_window_days=days,
    )
    db.commit()


def _service_payload(user_id: str, series_id: str, asset_id: str, **overrides: object) -> dict:
    payload = {
        "character_name": "申请流程角色",
        "species": "狼",
        "series_id": series_id,
        "suit_type": "full",
        "design_asset_ids": [asset_id],
        "requirements": "需要可拆卸尾巴与磁吸耳",
        "budget_min_cents": 500_000,
        "budget_max_cents": 900_000,
        "delivery_region": "中国大陆 / 上海",
        "contact_preference": "qq",
        "rights_declaration": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def service_product(db: Session):
    product = create_product(db, slug="service-suit", title="定制服务", kind="service")
    db.commit()
    return product


# ------------------------------------------------------------------ 服务先申请再报价
def test_inquiry_requires_approved_service_application(
    client: TestClient, db: Session, service_product
) -> None:
    owner = login(db, "申请流程客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)
    application = _application(db, user_id=owner.user_id, product_id=service_product.id)

    # 未审批：不能发起询价
    pending = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=application.id),
        headers=owner.headers(),
    )
    assert pending.status_code == 409, pending.text
    assert pending.json()["error"]["code"] == "STATE_CONFLICT"

    # 通过审批后：询价带上申请编号，并在详情里可见
    _approve(db, application.id)
    created = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=application.id),
        headers=owner.headers(),
    )
    assert created.status_code == 201, created.text
    inquiry_id = created.json()["id"]
    detail = client.get(f"/api/v1/inquiries/{inquiry_id}", headers=owner.headers())
    assert detail.status_code == 200, detail.text
    assert detail.json()["application_id"] == application.id

    # 同一份审批不能用于第二份询价
    again = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=application.id),
        headers=owner.headers(),
    )
    assert again.status_code == 409, again.text
    assert "已用于另一份询价" in again.json()["error"]["message"]


def test_application_must_be_own_service_and_within_window(
    client: TestClient, db: Session, service_product
) -> None:
    owner = login(db, "申请归属客户")
    other = login(db, "另一位客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)

    theirs = _application(db, user_id=other.user_id, product_id=service_product.id)
    _approve(db, theirs.id)
    foreign = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=theirs.id),
        headers=owner.headers(),
    )
    assert foreign.status_code == 404, foreign.text

    drop = _application(db, user_id=owner.user_id, product_id=service_product.id, kind="drop")
    _approve(db, drop.id)
    wrong_kind = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=drop.id),
        headers=owner.headers(),
    )
    assert wrong_kind.status_code == 422, wrong_kind.text
    assert wrong_kind.json()["error"]["fields"]["application_id"] == "申请类型不匹配"

    expired = _application(db, user_id=owner.user_id, product_id=service_product.id)
    _approve(db, expired.id)
    db.execute(
        sa.update(StockApplication)
        .where(StockApplication.id == expired.id)
        .values(purchase_expires_at=now_ms() - 1)
    )
    db.commit()
    late = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=expired.id),
        headers=owner.headers(),
    )
    assert late.status_code == 409, late.text
    assert "购买窗口已过期" in late.json()["error"]["message"]

    # 不带申请编号的普通询价不受影响
    plain = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset),
        headers=owner.headers(),
    )
    assert plain.status_code == 201, plain.text
    assert plain.json()["application_id"] is None


def test_accepting_quote_converts_the_application(
    client: TestClient, db: Session, service_product
) -> None:
    owner = login(db, "服务报价客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)
    application = _application(db, user_id=owner.user_id, product_id=service_product.id)
    _approve(db, application.id)
    application_id = application.id

    created = client.post(
        "/api/v1/inquiries",
        json=_service_payload(owner.user_id, series, asset, application_id=application_id),
        headers=owner.headers(),
    )
    assert created.status_code == 201, created.text
    inquiry_id = created.json()["id"]
    client.post(f"/api/v1/inquiries/{inquiry_id}/submit", headers=owner.headers())

    quote = publish_quote(db, inquiry_id=inquiry_id)
    accepted = client.post(
        f"/api/v1/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers()
    )
    assert accepted.status_code == 201, accepted.text
    order_id = accepted.json()["order_id"]

    db.expire_all()
    row = catalog_repo.get_application(db, application_id)
    assert row is not None
    assert row.status == "CONVERTED", "接受报价建单后，服务申请必须收口为已转订单"
    assert row.converted_order_id == order_id
    assert commerce_repo.get_order(db, order_id=order_id) is not None


# ------------------------------------------------------------------ 购买优先级列表
def test_admin_priority_list_orders_approved_first(db: Session, service_product, client: TestClient) -> None:
    first = create_user(db, nickname="优先级甲", phone="13800003333")
    second = create_user(db, nickname="优先级乙", phone="13800004444")
    third = create_user(db, nickname="优先级丙", phone="13800005555")
    db.commit()

    rank_two = _application(db, user_id=second.id, product_id=service_product.id)
    rank_one = _application(db, user_id=first.id, product_id=service_product.id)
    waiting = _application(db, user_id=third.id, product_id=service_product.id)
    _approve(db, rank_two.id, rank=2)
    _approve(db, rank_one.id, rank=1)

    rows, total = catalog_repo.list_applications_admin(
        db, order_by_priority=True, limit=10
    )
    assert total == 3
    assert [row.id for row in rows] == [rank_one.id, rank_two.id, waiting.id]

    # kind 过滤：drop 申请不在服务队列里
    drop = _application(db, user_id=third.id, product_id=service_product.id, kind="drop")
    db.commit()
    service_rows, service_total = catalog_repo.list_applications_admin(
        db, kind="service", order_by_priority=True, limit=10
    )
    assert service_total == 3
    assert drop.id not in [row.id for row in service_rows]

    # HTTP 层同样可用（运营在 /admin 商品页读这张购买优先级表）
    staff, secret = create_staff(db, login_name="catalog-ops", roles=["content_editor"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    headers = {"X-CSRF-Token": csrf["csrf_token"], "Origin": "http://127.0.0.1:5173"}
    response = client.get(
        "/api/v1/admin/stock-applications",
        params={"kind": "service", "order_by_priority": "true"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [rank_one.id, rank_two.id, waiting.id]
    assert body["items"][0]["priority_rank"] == 1


# ------------------------------------------------------------------ 独占库存
def test_exclusive_character_stock_cannot_exceed_one(db: Session) -> None:
    product = create_product(db, slug="exclusive-stock", title="独占角色", kind="exclusive_character")
    sku = create_sku(db, product_id=product.id, price_cents=880000, stock_on_hand=1, max_per_order=1)
    db.commit()

    with pytest.raises(AppError) as error:
        catalog_service.adjust_stock(
            db, sku_id=sku.id, delta=1, reason="补货", actor_id="stf_catalog"
        )
    assert error.value.code == "STATE_CONFLICT"
    assert error.value.fields["delta"] == "独占角色库存不可超过 1"
    db.rollback()

    db.expire_all()
    refreshed = catalog_repo.get_sku(db, sku.id)
    assert refreshed is not None and refreshed.stock_on_hand == 1

    # 下架（降到 0）与复售（回到 1）仍然允许
    catalog_service.adjust_stock(db, sku_id=sku.id, delta=-1, reason="下架", actor_id="stf_catalog")
    db.commit()
    db.expire_all()
    assert catalog_repo.get_sku(db, sku.id).stock_on_hand == 0  # type: ignore[union-attr]
    catalog_service.adjust_stock(db, sku_id=sku.id, delta=1, reason="复售", actor_id="stf_catalog")
    db.commit()
    db.expire_all()
    assert catalog_repo.get_sku(db, sku.id).stock_on_hand == 1  # type: ignore[union-attr]


def test_exclusive_character_second_sku_is_rejected(db: Session, client: TestClient) -> None:
    """08 文档：独占角色只能绑定一个销售 SKU。"""
    product = create_product(db, slug="exclusive-single", title="独占角色二", kind="exclusive_character")
    create_sku(db, product_id=product.id, price_cents=500000, stock_on_hand=1, max_per_order=1)
    db.commit()

    staff, secret = create_staff(db, login_name="catalog-ops-2", roles=["content_editor"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    headers = {"X-CSRF-Token": csrf["csrf_token"], "Origin": "http://127.0.0.1:5173"}
    response = client.post(
        f"/api/v1/admin/products/{product.id}/skus",
        json={
            "sku_code": "EXCL-2",
            "label": "第二份",
            "price_cents": 500000,
            "currency": "CNY",
            "stock_on_hand": 1,
            "max_per_order": 1,
        },
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert "只能绑定一个销售 SKU" in response.json()["error"]["message"]
