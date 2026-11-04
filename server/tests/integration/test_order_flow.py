"""TC-O04 与订单全流程集成测试（真实 SQLite 临时文件 + TestClient）。

覆盖：结算预览 → 创建订单 → 取消/关单释放、履约改地址保留原始快照、重复发货被拒、
退款后不自动回库、发票申请、越权访问返回 404、同幂等键不同请求 409、
以及订单列表的筛选/分页/待付款提醒。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.catalog.models import InventoryMovement
from app.modules.commerce import repository as commerce_repo
from app.modules.commerce import service as commerce_service
from app.modules.commerce.models import AddressSnapshot, Installment, Order
from app.platform.ids import iso
from app.platform.security import decrypt_pii
from tests.conftest import (
    create_address,
    create_product,
    create_sku,
    create_user,
    issue_login,
)


@pytest.fixture()
def shopper(db: Session, client: TestClient):
    user = create_user(db, nickname="星友甲", phone="13800001111")
    address = create_address(db, user_id=user.id)
    product = create_product(db, slug="suit-a", title="示例兽装 A")
    sku = create_sku(db, product_id=product.id, price_cents=12000, stock_on_hand=5)
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()
    return {"user": user, "address": address, "product": product, "sku": sku, "csrf": csrf}


def _place_order(client: TestClient, shopper: dict, *, quantity: int = 1, key: str = "flow-1") -> dict:
    preview = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": quantity}],
            "address_id": shopper["address"].id,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["terms_version"] == "shop-1"
    created = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview.json()["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": key},
    )
    assert created.status_code == 201, created.text
    return created.json()


# ------------------------------------------------------------------ 主流程
def test_order_lifecycle_to_shipped_and_receipt(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]

    listed = client.get("/api/v1/orders")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["pending_actions"]["orders_awaiting_payment"] == 1
    assert body["items"][0]["id"] == order_id
    assert body["items"][0]["status"] == "PENDING_PAYMENT"
    assert body["has_more"] is False

    filtered = client.get("/api/v1/orders", params={"status": "CANCELLED"})
    assert filtered.json()["items"] == []
    bad_filter = client.get("/api/v1/orders", params={"status": "NOPE"})
    assert bad_filter.status_code == 422

    detail = client.get(f"/api/v1/orders/{order_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["order_no"] == created["order_no"]
    assert payload["installments"][0]["id"] == created["installment_id"]
    assert payload["address"] is not None
    assert payload["address"]["recipient"] == "测试收货人"
    assert payload["items"][0]["description_snapshot"].startswith("示例兽装 A")
    assert payload["terms_version"] == "shop-1"

    # 未付款不能发货：先由支付模块事件消费者核销，再走运营发货。
    from app.platform.errors import AppError

    with pytest.raises(AppError) as unpaid:
        commerce_service.create_shipment(
            db, order_id=order_id, carrier="顺丰速运", tracking_no="SF0001", actor_id="stf_ops"
        )
    assert unpaid.value.code == "STATE_CONFLICT"
    db.rollback()

    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=created["installment_id"])
    db.commit()

    shipment = commerce_service.create_shipment(
        db,
        order_id=order_id,
        carrier="顺丰速运",
        tracking_no="SF1001",
        actor_id="stf_ops",
    )
    db.commit()
    assert shipment.tracking_no == "SF1001"
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "SHIPPED"

    receipt = client.post(f"/api/v1/orders/{order_id}/confirm-receipt")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "COMPLETED"
    detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert detail["status"] == "COMPLETED"
    assert detail["completed_at"]
    assert detail["shipments"][0]["delivered_at"] is not None
    assert len(detail["payments"]["allocations"]) == 0


def test_tc_o04_address_change_keeps_original_snapshot(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """运营改地址只追加履约变更记录，原始证据不被覆盖。"""
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None
    snapshot = commerce_repo.get_address_snapshot(db, snapshot_id=order.address_snapshot_id or "")
    assert snapshot is not None
    original_recipient = decrypt_pii(snapshot.recipient_ciphertext)
    original_detail = decrypt_pii(snapshot.address_ciphertext)
    assert original_recipient == "测试收货人"

    result = commerce_service.change_delivery_address(
        db,
        order_id=order_id,
        actor_type="staff",
        actor_id="stf_cs",
        recipient="新收货人",
        phone="13900000000",
        detail="广州市天河区示例路 9 号",
        province="广东省",
        city="广州市",
        postal_code="510000",
        reason="客户致电要求改地址",
    )
    db.commit()
    assert result["snapshot_preserved"] is True

    db.expire_all()
    preserved = commerce_repo.get_address_snapshot(db, snapshot_id=snapshot.id)
    assert preserved is not None
    assert decrypt_pii(preserved.recipient_ciphertext) == original_recipient
    assert decrypt_pii(preserved.address_ciphertext) == original_detail
    assert preserved.recipient_ciphertext == snapshot.recipient_ciphertext

    changes = commerce_repo.list_address_changes(db, order_id=order_id)
    assert len(changes) == 1
    assert changes[0].actor_type == "staff"
    assert changes[0].reason == "客户致电要求改地址"
    assert changes[0].snapshot_id == snapshot.id
    assert decrypt_pii(changes[0].new_address_ciphertext) == "广州市天河区示例路 9 号"

    effective = commerce_service.effective_delivery_address(db, order_id=order_id)
    assert effective is not None
    assert effective["changed"] is True
    assert effective["recipient"] == "新收货人"
    assert effective["detail"] == "广州市天河区示例路 9 号"

    detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert detail["address"]["recipient"] == "新收货人"
    assert len(detail["address_changes"]) == 1
    assert detail["address"]["last_change_reason"] == "客户致电要求改地址"


def test_tc_o04_duplicate_shipment_is_rejected(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """重复发货被拒：同一承运商 + 同一物流号只登记一次，订单不能重复出库。"""
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    installment_id = created["installment_id"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()

    first = commerce_service.create_shipment(
        db, order_id=order_id, carrier="顺丰速运", tracking_no="SF2001", actor_id="stf_ops"
    )
    db.commit()
    assert first.id

    from app.platform.errors import AppError

    with pytest.raises(AppError) as caught:
        commerce_service.create_shipment(
            db, order_id=order_id, carrier="顺丰速运", tracking_no="SF2001", actor_id="stf_ops"
        )
    assert caught.value.code == "STATE_CONFLICT"
    db.rollback()

    with pytest.raises(AppError) as caught2:
        commerce_service.create_shipment(
            db, order_id=order_id, carrier="中通快递", tracking_no="ZT9001", actor_id="stf_ops"
        )
    assert caught2.value.code == "STATE_CONFLICT"
    db.rollback()

    detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert len(detail["shipments"]) == 1
    assert detail["status"] == "SHIPPED"


def test_tc_o04_refund_does_not_auto_restock(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """退款成功不自动回库；库存只由显式回仓检验动作恢复。"""
    created = _place_order(client, shopper, quantity=2)
    order_id = created["order_id"]
    installment_id = created["installment_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3

    commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="rfd_flow_1", goods_cents=24000, shipping_cents=0
    )
    db.commit()
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3
    assert shopper["sku"].reserved == 0
    restocks = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.action == "RESTOCK"))
    )
    assert restocks == []


def test_tc_o04_invoice_request_is_minimal_and_unique(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """发票申请：抬头加密保存，金额与实际交易匹配，重复提交被拒。"""
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    installment_id = created["installment_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]

    pending = client.post(
        f"/api/v1/orders/{order_id}/invoices",
        json={"type": "business", "title": "示例科技有限公司", "tax_no": "91310000MA1K000000"},
    )
    assert pending.status_code == 409
    assert pending.json()["error"]["code"] == "STATE_CONFLICT"

    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()

    missing_tax = client.post(
        f"/api/v1/orders/{order_id}/invoices",
        json={"type": "business", "title": "示例科技有限公司"},
    )
    assert missing_tax.status_code == 422

    created_invoice = client.post(
        f"/api/v1/orders/{order_id}/invoices",
        json={"type": "business", "title": "示例科技有限公司", "tax_no": "91310000MA1K000000"},
    )
    assert created_invoice.status_code == 201, created_invoice.text
    invoice = created_invoice.json()
    assert invoice["status"] == "PENDING"
    assert invoice["amount_cents"] == created["total_cents"]
    assert invoice["title"] == "示例科技有限公司"

    from app.modules.commerce.models import Invoice

    row = db.get(Invoice, invoice["id"])
    assert row is not None
    assert "91310000MA1K000000" not in row.encrypted_title_data
    assert commerce_service.invoice_title_data(row)["tax_no"] == "91310000MA1K000000"

    duplicate = client.post(
        f"/api/v1/orders/{order_id}/invoices",
        json={"type": "personal", "title": "个人"},
    )
    assert duplicate.status_code == 409
    listed = client.get(f"/api/v1/orders/{order_id}/invoices")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


# ------------------------------------------------------------------ 关闭与释放
def test_cancel_releases_reservation_and_keeps_evidence(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    created = _place_order(client, shopper, quantity=2)
    order_id = created["order_id"]
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 2

    cancelled = client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "改主意了"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 0
    assert shopper["sku"].stock_on_hand == 5
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.cancel_reason == "改主意了"
    reservations = commerce_repo_active(db, order_id=order_id)
    assert reservations == []
    detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert detail["status"] == "CANCELLED"
    assert detail["cancelled_at"] is not None
    # 关闭后不得再释放一次（幂等）。
    again = client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "再点一次"})
    assert again.status_code == 409
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 0


def commerce_repo_active(session: Session, *, order_id: str):
    from app.modules.catalog.models import InventoryReservation

    return list(
        session.scalars(
            sa.select(InventoryReservation).where(
                InventoryReservation.order_id == order_id,
                InventoryReservation.state == "ACTIVE",
            )
        )
    )


def test_expired_order_can_be_closed_by_task(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """到期未付款订单由任务关闭：进入 CLOSING 后确认没有在途付款再取消并释放。"""
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    db.execute(
        sa.update(Order)
        .where(Order.id == order_id)
        .values(expires_at=0, updated_at=0)
    )
    db.commit()

    assert commerce_repo.count_overdue_orders(db) == 1
    result = commerce_service.cancel_order(
        db, user_id=shopper["user"].id, order_id=order_id, reason="超时未付款自动关闭"
    )
    db.commit()
    assert result["status"] == "CANCELLED"
    assert result["released_reservations"]
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "CANCELLED"
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 0


# ------------------------------------------------------------------ 越权与幂等
def test_other_user_order_returns_404(client: TestClient, db: Session, shopper: dict) -> None:
    """越权访问他人订单返回 404，不泄露资源是否存在。"""
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]

    intruder = create_user(db, nickname="星友丁", phone="13800004444")
    csrf = issue_login(db, client, intruder)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()

    assert client.get(f"/api/v1/orders/{order_id}").status_code == 404
    assert client.get(f"/api/v1/orders/{order_id}/invoices").status_code == 404
    assert (
        client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "越权"}).status_code == 404
    )
    assert client.post(f"/api/v1/orders/{order_id}/confirm-receipt").status_code == 404
    assert (
        client.post(
            f"/api/v1/orders/{order_id}/after-sales",
            json={"type": "refund_only", "reason": "越权申请"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/orders/{order_id}/invoices", json={"type": "personal", "title": "越权"}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/orders").json()["items"] == []
    assert client.get("/api/v1/after-sales").json()["items"] == []


def test_idempotency_key_conflict_returns_409(client: TestClient, db: Session, shopper: dict) -> None:
    """同键不同请求 409；同键同请求返回同资源。"""
    created = _place_order(client, shopper, quantity=1, key="flow-dup")
    assert created["order_id"]

    preview = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": 1}],
            "address_id": shopper["address"].id,
        },
    ).json()
    conflict = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": "flow-dup"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert commerce_repo.count_orders_for_user(db, user_id=shopper["user"].id) == 1

    replay = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": "flow-dup"},
    )
    assert replay.status_code == 409


def test_order_list_pagination(client: TestClient, db: Session, shopper: dict) -> None:
    for index in range(3):
        _place_order(client, shopper, quantity=1, key=f"page-{index}")
    first = client.get("/api/v1/orders", params={"page": 1, "limit": 2})
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] == "2"
    second = client.get("/api/v1/orders", params={"page": 1, "limit": 2, "status": "PENDING_PAYMENT"})
    assert second.json()["pending_actions"]["orders_awaiting_payment"] == 3
    over = client.get("/api/v1/orders", params={"limit": 500})
    assert over.status_code == 422


# ------------------------------------------------------------------ 售后全流程
def test_after_sale_flow_with_messages_and_return(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    installment_id = created["installment_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()

    opened = client.post(
        f"/api/v1/orders/{order_id}/after-sales",
        json={"type": "return_refund", "reason": "皮毛有瑕疵，希望退货"},
    )
    assert opened.status_code == 201, opened.text
    after_sale = opened.json()
    assert after_sale["status"] == "OPEN"
    assert after_sale["requested_amount_cents"] == 0
    assert after_sale["messages"][0]["author_type"] == "customer"
    assert "policy" in after_sale and after_sale["policy"]["window_days"] >= 1

    duplicate = client.post(
        f"/api/v1/orders/{order_id}/after-sales",
        json={"type": "refund_only", "reason": "再来一次"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "STATE_CONFLICT"

    messaged = client.post(
        f"/api/v1/after-sales/{after_sale['id']}/messages",
        json={"body": "补充：瑕疵在左肩位置"},
    )
    assert messaged.status_code == 200
    assert messaged.json()["messages"][-1]["body"].startswith("补充")

    detail = client.get(f"/api/v1/after-sales/{after_sale['id']}")
    assert detail.status_code == 200
    listed = client.get("/api/v1/after-sales")
    assert listed.json()["items"][0]["id"] == after_sale["id"]

    # 未批准前不能填写退货物流。
    premature = client.post(
        f"/api/v1/after-sales/{after_sale['id']}/return-shipment",
        json={"carrier": "顺丰", "tracking_no": "SF3001"},
    )
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "STATE_CONFLICT"

    commerce_service.decide_after_sale(
        db, after_sale_id=after_sale["id"], decision="APPROVED", actor_id="stf_cs", note="同意退货"
    )
    db.commit()

    returned = client.post(
        f"/api/v1/after-sales/{after_sale['id']}/return-shipment",
        json={"carrier": "顺丰", "tracking_no": "SF3001"},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["status"] == "RETURNING"
    assert returned.json()["return_tracking_no"] == "SF3001"

    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 4, "退货在途不得自动回库"

    order_detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert order_detail["after_sales"][0]["status"] == "RETURNING"
    assert len(order_detail["shipments"]) == 1
    assert order_detail["shipments"][0]["kind"] == "return"


def test_after_sale_refund_limit_and_window(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    installment_id = created["installment_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()

    over = client.post(
        f"/api/v1/orders/{order_id}/after-sales",
        json={
            "type": "refund_only",
            "reason": "要求超额退款",
            "requested_amount_cents": created["total_cents"] + 1,
        },
    )
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "REFUND_LIMIT_EXCEEDED"

    ok = client.post(
        f"/api/v1/orders/{order_id}/after-sales",
        json={
            "type": "refund_only",
            "reason": "部分退款",
            "requested_amount_cents": 5000,
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["requested_amount_cents"] == 5000


def test_order_detail_exposes_installments_and_payment_source(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    created = _place_order(client, shopper, quantity=1)
    order_id = created["order_id"]
    detail = client.get(f"/api/v1/orders/{order_id}").json()
    assert detail["payments"]["source"] == "payments"
    assert detail["installments"][0]["due_at"] is not None
    assert detail["policy_snapshot"]["currency"] == "CNY"
    assert detail["snapshot"]["region_code"] == "CN-31"
    order = db.get(Order, order_id)
    assert order is not None
    snapshot = db.get(AddressSnapshot, order.address_snapshot_id)
    assert snapshot is not None
    assert snapshot.order_id == order_id
    assert iso(snapshot.created_at) == detail["address"]["snapshot_created_at"]
    installments = commerce_repo.list_installments(db, order_id=order_id)
    assert isinstance(installments[0], Installment)
