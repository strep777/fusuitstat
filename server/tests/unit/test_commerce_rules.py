"""TC-O01 / TC-O02 / TC-O03 的商务规则单元测试（真实 SQLite 临时文件）。

覆盖：金额与优惠分摊规则、负数量、失效地址、下架 SKU、条件 UPDATE 防超卖、
独占角色唯一占用、关单释放与收款核销的竞争语义，以及积分净额与回库规则。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import InventoryMovement, InventoryReservation
from app.modules.commerce import ports as commerce_ports
from app.modules.commerce import repository as commerce_repo
from app.modules.commerce import service as commerce_service
from app.modules.commerce.models import Installment, Order, OrderItem
from app.platform.db import get_session_factory
from app.platform.errors import AppError
from app.platform.ids import new_id
from app.platform.security import hash_token
from tests.conftest import (
    create_address,
    create_character,
    create_product,
    create_sku,
    create_user,
    issue_login,
)


@pytest.fixture()
def shopper(db: Session, client: TestClient):
    """一个已登录客户 + 一条默认地址 + 一个可售 SKU（12000 分、库存 5）。"""
    from app.modules.content.models import SiteSetting
    db.add(SiteSetting(key="platform.feature_flags", group="platform", label="测试开关", value={"coupons": True}))
    db.add(SiteSetting(key="commerce.policy", group="commerce", label="测试券", value={"coupons": {
        "NEIXING10": {"type": "percent", "value": 10, "min_goods_cents": 10000, "max_discount_cents": 5000}
    }}))
    db.flush()
    user = create_user(db, nickname="星友甲", phone="13800001111")
    address = create_address(db, user_id=user.id)
    product = create_product(db, slug="suit-a", title="示例兽装 A")
    sku = create_sku(db, product_id=product.id, price_cents=12000, stock_on_hand=5)
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    return {"user": user, "address": address, "product": product, "sku": sku, "csrf": csrf}


def _preview(client: TestClient, shopper: dict, *, sku_id: str, quantity: int = 1, **extra):
    body = {"items": [{"sku_id": sku_id, "quantity": quantity}], "address_id": shopper["address"].id}
    body.update(extra)
    return client.post("/api/v1/checkout/preview", json=body)


def _create_order(client: TestClient, shopper: dict, *, checkout_token: str, key: str = "key-1"):
    return client.post(
        "/api/v1/orders",
        json={
            "checkout_token": checkout_token,
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": key},
    )


# ------------------------------------------------------------------ TC-O01
def test_tc_o01_server_recalculates_price_and_ignores_client_amounts(
    client: TestClient, db: Session, shopper: dict
) -> None:
    """客户端伪造单价/总额/积分字段必须被拒绝，绝不能影响订单金额。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=2)
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    # 服务端依据当前目录重算：12000 × 2 = 24000 分，未满免邮门槛加 1800 分运费。
    assert payload["lines"][0]["unit_price_cents"] == 12000
    assert payload["goods_cents"] == 24000
    assert payload["shipping_cents"] == 1800
    assert payload["total_cents"] == 25800
    assert payload["total_cents"] == (
        payload["goods_cents"] - payload["discount_cents"] + payload["shipping_cents"]
    )
    assert payload["currency"] == "CNY"

    forged = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": payload["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
            "unit_price_cents": 1,
            "total_cents": 1,
            "points": 99999,
        },
        headers={"Idempotency-Key": "forged-1"},
    )
    assert forged.status_code == 422
    assert forged.json()["error"]["code"] == "VALIDATION_ERROR"

    created = _create_order(client, shopper, checkout_token=payload["checkout_token"], key="k-ok")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "PENDING_PAYMENT"
    assert body["total_cents"] == 25800
    order = commerce_repo.get_order(db, order_id=body["order_id"])
    assert order is not None
    assert order.total_cents == 25800
    assert order.goods_cents == 24000
    assert order.shipping_cents == 1800
    assert order.discount_cents == 0
    items = commerce_repo.list_items(db, order_id=order.id)
    assert sum(item.unit_price_cents * item.quantity for item in items) == order.goods_cents
    assert sum(item.cash_goods_cents for item in items) == order.goods_cents - order.discount_cents
    installments = commerce_repo.list_installments(db, order_id=order.id)
    assert len(installments) == 1
    assert installments[0].amount_cents == order.total_cents
    assert installments[0].kind == "full"
    assert installments[0].status == "DUE"
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 2


def test_tc_o01_negative_quantity_is_rejected(client: TestClient, shopper: dict) -> None:
    """负数量在 DTO 层与 service 层都必须被拒绝。"""
    response = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=-1)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    session = get_session_factory()()
    try:
        with pytest.raises(AppError) as caught:
            commerce_service.preview_checkout(
                session,
                user_id=shopper["user"].id,
                items=[{"sku_id": shopper["sku"].id, "quantity": -3}],
                address_id=shopper["address"].id,
            )
        assert caught.value.code == "VALIDATION_ERROR"
    finally:
        session.close()


def test_tc_o01_expired_or_foreign_address_is_rejected(
    client: TestClient, db: Session, shopper: dict
) -> None:
    """失效地址（不属于本人）与不存在的地址都必须被拒绝。"""
    other = create_user(db, nickname="星友乙", phone="13800002222")
    foreign = create_address(
        db, user_id=other.id, recipient="他人", detail="北京市朝阳区示例路 2 号", region_code="CN-11"
    )
    db.commit()
    response = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": shopper["sku"].id, "quantity": 1}], "address_id": foreign.id},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": shopper["sku"].id, "quantity": 1}], "address_id": "adr_not_exists"},
    )
    assert missing.status_code == 422


def test_tc_o01_unpublished_sku_is_rejected(client: TestClient, db: Session, shopper: dict) -> None:
    shopper["product"].status = "archived"
    db.commit()
    response = _preview(client, shopper, sku_id=shopper["sku"].id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STOCK_NOT_ENOUGH"


def test_tc_o01_inactive_sku_is_rejected(client: TestClient, db: Session, shopper: dict) -> None:
    shopper["sku"].is_active = 0
    db.commit()
    response = _preview(client, shopper, sku_id=shopper["sku"].id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STOCK_NOT_ENOUGH"


def test_tc_o01_price_change_between_preview_and_order(
    client: TestClient, db: Session, shopper: dict
) -> None:
    """预览后调价：必须返回 PRICE_CHANGED 并要求用户重新确认。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1)
    token = preview.json()["checkout_token"]
    shopper["sku"].price_cents = 15000
    db.commit()

    response = _create_order(client, shopper, checkout_token=token, key="price-changed")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PRICE_CHANGED"
    assert commerce_repo.count_orders_for_user(db, user_id=shopper["user"].id) == 0
    # 令牌未被消费，用户可以重新预览后再次下单。
    checkout = commerce_repo.get_checkout_session_by_hash(db, token_hash=hash_token(token))
    assert checkout is not None and checkout.consumed_at is None


def test_checkout_token_is_single_use(client: TestClient, db: Session, shopper: dict) -> None:
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1)
    token = preview.json()["checkout_token"]
    first = _create_order(client, shopper, checkout_token=token, key="single-1")
    assert first.status_code == 201
    second = _create_order(client, shopper, checkout_token=token, key="single-2")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CHECKOUT_TOKEN_INVALID"


def test_same_idempotency_key_different_request_conflicts(client: TestClient, shopper: dict) -> None:
    first_token = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
        "checkout_token"
    ]
    assert _create_order(client, shopper, checkout_token=first_token, key="dup").status_code == 201
    second_token = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
        "checkout_token"
    ]
    conflict = _create_order(client, shopper, checkout_token=second_token, key="dup")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_same_idempotency_key_same_request_replays_same_order(
    client: TestClient, shopper: dict
) -> None:
    token = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()["checkout_token"]
    first = _create_order(client, shopper, checkout_token=token, key="replay")
    assert first.status_code == 201
    replay = _create_order(client, shopper, checkout_token=token, key="replay")
    assert replay.status_code == 201
    assert replay.json()["order_id"] == first.json()["order_id"]
    assert replay.json()["order_no"] == first.json()["order_no"]


def test_requires_approval_product_needs_approved_application(
    client: TestClient, db: Session, shopper: dict
) -> None:
    """C28：需申请商品必须先通过人工审批，否则 STATE_CONFLICT。"""
    gated = create_product(
        db, slug="gated-drop", title="限定掉落", kind="stock_goods", requires_approval=True
    )
    gated_sku = create_sku(db, product_id=gated.id, price_cents=50000, stock_on_hand=1)
    db.commit()

    token = _preview(client, shopper, sku_id=gated_sku.id).json()["checkout_token"]
    blocked = _create_order(client, shopper, checkout_token=token, key="gated-1")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "STATE_CONFLICT"
    assert "审批" in blocked.json()["error"]["message"]
    # 未通过审批时不得占用库存。
    db.refresh(gated_sku)
    assert gated_sku.reserved == 0

    application = catalog_service.submit_application(
        db,
        user_id=shopper["user"].id,
        product_id=gated.id,
        sku_id=gated_sku.id,
        reason="希望购买限定掉落",
    )
    catalog_service.decide_application(
        db,
        application_id=application.id,
        decision="APPROVED",
        actor_id="stf_test",
        reason="资格核验通过",
    )
    db.commit()

    token2 = _preview(client, shopper, sku_id=gated_sku.id).json()["checkout_token"]
    ok = _create_order(client, shopper, checkout_token=token2, key="gated-2")
    assert ok.status_code == 201, ok.text
    order_id = ok.json()["order_id"]
    item = commerce_repo.list_items(db, order_id=order_id)[0]
    assert item.application_id == application.id
    db.refresh(application)
    assert application.status == "CONVERTED"
    assert application.converted_order_id == order_id


def test_promo_discount_allocation_matches_order_total(
    db: Session, client: TestClient, shopper: dict
) -> None:
    """优惠按行分摊，商品现金分摊之和必须等于订单商品净额。"""
    second = create_sku(db, product_id=shopper["product"].id, price_cents=5000, label="配件")
    db.commit()
    response = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [
                {"sku_id": shopper["sku"].id, "quantity": 1},
                {"sku_id": second.id, "quantity": 1},
            ],
            "address_id": shopper["address"].id,
            "coupon_code": "NEIXING10",
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["goods_cents"] == 17000
    assert preview["discount_cents"] == 1700

    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="promo")
    assert created.status_code == 201, created.text
    order = commerce_repo.get_order(db, order_id=created.json()["order_id"])
    assert order is not None
    items = commerce_repo.list_items(db, order_id=order.id)
    assert sum(item.discount_allocated_cents for item in items) == order.discount_cents
    assert sum(item.cash_goods_cents for item in items) == order.goods_cents - order.discount_cents
    assert order.total_cents == order.goods_cents - order.discount_cents + order.shipping_cents
    assert all(item.discount_allocated_cents >= 0 for item in items)


def test_allocate_discount_largest_remainder() -> None:
    assert commerce_service.allocate_discount([100, 200, 300], 100) == [17, 33, 50]
    assert sum(commerce_service.allocate_discount([1, 1, 1], 2)) == 2
    assert commerce_service.allocate_discount([], 0) == []


def test_discount_allocations_never_exceed_line_subtotal() -> None:
    allocations = commerce_service.allocate_discount([100, 100], 150)
    assert sum(allocations) == 150
    assert all(value <= 100 for value in allocations)


def test_unknown_coupon_is_rejected(client: TestClient, shopper: dict) -> None:
    response = _preview(client, shopper, sku_id=shopper["sku"].id, coupon_code="NOT-A-COUPON")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ TC-O02
@contextmanager
def _competing_attempt() -> Iterator[Session]:
    """把一次竞争尝试放进**独立会话 + 独立事务**：成功即提交，失败只回滚本次尝试。

    13 文档 TC-O02「50 并发购买最后一件最多一份有效占用」要求每个竞争单元各自提交。
    若在同一个会话里连续调用并在失败分支 ``rollback()``，SQLAlchemy 的 ``rollback()``
    会撤销**自上次提交以来的全部工作** —— 包括此前已成功但尚未提交的预留；于是「失败」
    反而把库存还回去、下一次又能成功，断言到的成功次数与最终真实库存都不成立。
    独立会话逐个提交既表达了真实并发语义，也让最终断言读到的就是真实库状态。
    """
    session = get_session_factory()()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def test_tc_o02_last_item_only_one_reservation(db: Session, shopper: dict) -> None:
    """最后一件：50 次原子预留只允许一个成功，库存永不为负。"""
    sku_id = shopper["sku"].id
    shopper["sku"].stock_on_hand = 1
    shopper["sku"].reserved = 0
    # 提交并结束测试会话的事务：竞争期间它不再持有 SQLite 读锁，也不会缓存旧库存。
    db.commit()
    order_id = new_id("order")
    succeeded: list[str] = []
    failures = 0
    for index in range(50):
        item_id = f"{order_id}-item-{index}"
        try:
            with _competing_attempt() as attempt:
                catalog_service.reserve(
                    attempt,
                    sku_id=sku_id,
                    quantity=1,
                    order_id=order_id,
                    order_item_id=item_id,
                )
            succeeded.append(item_id)
        except AppError as exc:
            assert exc.code == "STOCK_NOT_ENOUGH"
            failures += 1

    assert len(succeeded) == 1
    assert failures == 49
    db.expire_all()
    sku = commerce_repo_order_sku(db, sku_id)
    assert sku.reserved == 1
    assert sku.stock_on_hand == 1
    assert sku.stock_on_hand - sku.reserved == 0
    assert len(_active_reservations(db, order_id=order_id)) == 1


def commerce_repo_order_sku(session: Session, sku_id: str):
    from app.modules.catalog.models import Sku

    return session.get(Sku, sku_id)


def _active_reservations(session: Session, *, order_id: str) -> list[InventoryReservation]:
    return list(
        session.scalars(
            sa.select(InventoryReservation).where(
                InventoryReservation.order_id == order_id,
                InventoryReservation.state == "ACTIVE",
            )
        )
    )


def _reservation_ids(session: Session, *, order_id: str) -> list[str]:
    return list(
        session.scalars(
            sa.select(InventoryReservation.id).where(InventoryReservation.order_id == order_id)
        )
    )


def test_tc_o02_exclusive_character_single_occupation(db: Session, shopper: dict) -> None:
    """独占角色最多一份有效占用：条件 UPDATE 只有一个能改到 reserved。"""
    product = create_product(db, slug="exclusive-1", title="独占角色", kind="exclusive_character")
    sku = create_sku(
        db, product_id=product.id, price_cents=880000, stock_on_hand=1, max_per_order=1
    )
    character = create_character(db, product_id=product.id, slug="chr-1", name="霜龙")
    character_id = character.id  # 主键由客户端生成，此处不会触发数据库读取
    # 提交并结束测试会话的事务：竞争期间它不再持有 SQLite 读锁。
    db.commit()

    adopted = 0
    for _ in range(50):
        try:
            with _competing_attempt() as attempt:
                catalog_service.reserve_exclusive_character(
                    attempt, character_id=character_id, order_id=new_id("order")
                )
            adopted += 1
        except AppError as exc:
            assert exc.code == "STOCK_NOT_ENOUGH"

    assert adopted == 1
    db.expire_all()
    db.refresh(character)
    assert character.public_status == "reserved"
    assert sku.stock_on_hand == 1


def test_sku_stock_never_negative_under_repeated_reserve(db: Session, shopper: dict) -> None:
    sku_id = shopper["sku"].id
    shopper["sku"].stock_on_hand = 3
    db.commit()
    order_id = new_id("order")
    succeeded = 0
    for index in range(10):
        try:
            with _competing_attempt() as attempt:
                catalog_service.reserve(
                    attempt,
                    sku_id=sku_id,
                    quantity=1,
                    order_id=order_id,
                    order_item_id=f"{order_id}-{index}",
                )
            succeeded += 1
        except AppError:
            pass
    db.expire_all()
    sku = commerce_repo_order_sku(db, sku_id)
    assert succeeded == 3
    assert sku.reserved == 3
    assert sku.stock_on_hand - sku.reserved == 0
    assert sku.reserved <= sku.stock_on_hand
    assert len(_active_reservations(db, order_id=order_id)) == 3
    # 直接查真实库确认不变量（行内还有 CHECK ck_skus_reserved_le_stock 兜底）。
    assert db.scalar(sa.text("SELECT COUNT(*) FROM skus WHERE reserved > stock_on_hand")) == 0


# ------------------------------------------------------------------ TC-O03
def test_tc_o03_cancel_then_paid_cannot_consume(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """关单释放先成功：后到的收款只能记为迟到收款，不得再扣库存。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1)
    created = _create_order(client, shopper, checkout_token=preview.json()["checkout_token"], key="race-1")
    assert created.status_code == 201
    order_id = created.json()["order_id"]
    installment_id = created.json()["installment_id"]

    cancelled = client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "不想买了"})
    assert cancelled.status_code == 200, cancelled.text
    assert payment_ports["closing"], "关单前必须先关闭或查询渠道"
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "CANCELLED"
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 0
    assert len(_active_reservations(db, order_id=order_id)) == 0

    paid = commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()
    assert paid["late_payment"] is True
    assert paid["consumed_reservations"] == []
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 5
    assert shopper["sku"].reserved == 0
    movements = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.source_id == order_id))
    )
    # 以订单为 source 的流水只有 RESERVE；RELEASE 以预留 id 为 source（见下方断言）。
    assert {movement.action for movement in movements} == {"RESERVE"}
    reservation_ids = _reservation_ids(db, order_id=order_id)
    released = list(
        db.scalars(
            sa.select(InventoryMovement).where(
                InventoryMovement.source_id.in_(reservation_ids),
                InventoryMovement.action == "RELEASE",
            )
        )
    )
    assert len(reservation_ids) == 1
    assert len(released) == 1
    consumes = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.action == "CONSUME"))
    )
    assert consumes == []


def test_tc_o03_paid_then_cancel_is_blocked(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """收款核销先成功：关单必须失败，已核销的预留不会被二次释放。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=2)
    created = _create_order(client, shopper, checkout_token=preview.json()["checkout_token"], key="race-2")
    assert created.status_code == 201
    order_id = created.json()["order_id"]
    installment_id = created.json()["installment_id"]

    paid = commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()
    assert paid["consumed_reservations"]
    assert paid["order_status"] == "ACTIVE"
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "ACTIVE"
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3
    assert shopper["sku"].reserved == 0

    # 支付模块报告已有实收：关单必须失败，不能释放或取消。
    payment_ports["state"]["paid_cents"] = 25800
    blocked = client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "误点"})
    assert blocked.status_code == 409
    # 08 文档第 4 条：关单要先进入关闭核对（closing）并查渠道，确认「没有在途成功付款」
    # 才能取消；已有实收/已核销时归入支付待确认，而不是通用状态冲突。
    assert blocked.json()["error"]["code"] == "PAYMENT_PENDING"
    assert payment_ports["closing"], "关单必须先进入关闭核对并通知渠道"
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3
    assert shopper["sku"].reserved == 0
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "ACTIVE"
    # 关单失败必须整体回滚：不留下关闭核对标记，也不能释放已核销的预留。
    assert order.closing_at is None
    released = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.action == "RELEASE"))
    )
    assert released == [], "已核销的预留不得再被释放"


def test_tc_o03_pending_channel_payment_blocks_cancel(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """渠道在途（应付项 PAYING）：关单必须先核对，不能直接取消并释放。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1)
    created = _create_order(client, shopper, checkout_token=preview.json()["checkout_token"], key="race-3")
    order_id = created.json()["order_id"]
    installment_id = created.json()["installment_id"]
    db.execute(sa.update(Installment).where(Installment.id == installment_id).values(status="PAYING"))
    db.commit()

    blocked = client.post(f"/api/v1/orders/{order_id}/cancel", json={"reason": "取消"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PAYMENT_PENDING"
    assert payment_ports["closing"], "关单前必须先通知渠道关闭或查单"
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.status == "PENDING_PAYMENT"
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 1
    assert len(_active_reservations(db, order_id=order_id)) == 1


def test_on_installment_paid_is_idempotent(
    db: Session, shopper: dict, client: TestClient, payment_ports: dict
) -> None:
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1)
    created = _create_order(client, shopper, checkout_token=preview.json()["checkout_token"], key="idem-pay")
    order_id = created.json()["order_id"]
    installment_id = created.json()["installment_id"]

    first = commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()
    second = commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()

    assert first["consumed_reservations"] and not first["already_paid"]
    assert second["already_paid"] is True
    assert second["consumed_reservations"] == []
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 4
    assert shopper["sku"].reserved == 0
    consumes = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.action == "CONSUME"))
    )
    assert len(consumes) == 1


# ------------------------------------------------------------------ 到期扫描（worker 入口）
def test_expire_due_orders_closes_only_expired_orders(
    db: Session, shopper: dict, client: TestClient, payment_ports: dict
) -> None:
    """到期扫描：只关到期未付款订单，重复扫描幂等，且不重复释放库存与事件。"""
    due = _create_order(
        client,
        shopper,
        checkout_token=_preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
            "checkout_token"
        ],
        key="expire-due",
    ).json()
    fresh = _create_order(
        client,
        shopper,
        checkout_token=_preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
            "checkout_token"
        ],
        key="expire-fresh",
    ).json()
    db.execute(sa.update(Order).where(Order.id == due["order_id"]).values(expires_at=0))
    db.commit()
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 2

    processed = commerce_ports.expire_due_orders(db, limit=10)
    assert processed == 1
    db.expire_all()
    expired = commerce_repo.get_order(db, order_id=due["order_id"])
    alive = commerce_repo.get_order(db, order_id=fresh["order_id"])
    assert expired is not None and expired.status == "CANCELLED"
    assert expired.cancel_reason == "超时未付款自动关闭"
    assert expired.closing_at is not None
    assert alive is not None and alive.status == "PENDING_PAYMENT"
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 1
    assert len(_active_reservations(db, order_id=due["order_id"])) == 0
    assert len(_active_reservations(db, order_id=fresh["order_id"])) == 1
    assert payment_ports["closing"], "到期关单同样要先关闭或查询渠道"

    # 幂等：再扫一次不会重复处理，也不会重复释放。
    assert commerce_ports.expire_due_orders(db, limit=10) == 0
    db.expire_all()
    db.refresh(shopper["sku"])
    assert shopper["sku"].reserved == 1
    assert commerce_repo.count_overdue_orders(db) == 0


def test_expire_due_orders_skips_orders_with_in_flight_payment(
    db: Session, shopper: dict, client: TestClient, payment_ports: dict
) -> None:
    """到期但渠道在途：扫描不能取消，必须留给支付/对账流程，且不中断整批。"""
    pending = _create_order(
        client,
        shopper,
        checkout_token=_preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
            "checkout_token"
        ],
        key="expire-inflight",
    ).json()
    closable = _create_order(
        client,
        shopper,
        checkout_token=_preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()[
            "checkout_token"
        ],
        key="expire-closable",
    ).json()
    db.execute(
        sa.update(Installment).where(Installment.id == pending["installment_id"]).values(status="PAYING")
    )
    db.execute(
        sa.update(Order)
        .where(Order.id.in_([pending["order_id"], closable["order_id"]]))
        .values(expires_at=0)
    )
    db.commit()

    processed = commerce_ports.expire_due_orders(db, limit=10)
    assert processed == 1
    db.expire_all()
    blocked = commerce_repo.get_order(db, order_id=pending["order_id"])
    closed = commerce_repo.get_order(db, order_id=closable["order_id"])
    assert blocked is not None and blocked.status == "PENDING_PAYMENT"
    assert closed is not None and closed.status == "CANCELLED"
    assert len(_active_reservations(db, order_id=pending["order_id"])) == 1


# ------------------------------------------------------------------ 积分权威快照
def test_order_reward_snapshot_excludes_shipping(
    db: Session, shopper: dict, client: TestClient, payment_ports: dict
) -> None:
    """eligible_net_cents 只计商品现金净额，运费与未收款都不计奖。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()
    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="reward-1")
    order_id = created.json()["order_id"]

    snapshot = commerce_service.order_reward_snapshot(db, order_id)
    assert set(snapshot) >= {
        "order_id",
        "user_id",
        "status",
        "currency",
        "goods_cents",
        "shipping_cents",
        "discount_cents",
        "total_cents",
        "paid_goods_cents",
        "refunded_goods_cents",
        "eligible_net_cents",
        "completed_at",
        "has_open_after_sales",
    }
    assert snapshot["eligible_net_cents"] == 0
    assert snapshot["has_open_after_sales"] is False

    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None
    assert order.shipping_cents > 0
    # 支付模块确认全额实收后，仅商品净额计奖，运费不计。
    payment_ports["state"]["paid_cents"] = order.total_cents
    snapshot = commerce_service.order_reward_snapshot(db, order_id)
    assert snapshot["paid_goods_cents"] == order.goods_cents
    assert snapshot["eligible_net_cents"] == order.goods_cents - order.discount_cents
    assert snapshot["eligible_net_cents"] == order.total_cents - order.shipping_cents
    assert snapshot["eligible_net_cents"] < order.total_cents

    commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="rfd_test_1", goods_cents=5000, shipping_cents=0
    )
    db.commit()
    snapshot = commerce_service.order_reward_snapshot(db, order_id)
    assert snapshot["refunded_goods_cents"] == 5000
    assert snapshot["eligible_net_cents"] == order.goods_cents - order.discount_cents - 5000


def test_refund_does_not_restock_and_restock_is_explicit(
    db: Session, shopper: dict, client: TestClient, payment_ports: dict
) -> None:
    """售后退款不自动补库存；只有显式回仓检验才 RESTOCK。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=2).json()
    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="refund-1")
    order_id = created.json()["order_id"]
    installment_id = created.json()["installment_id"]
    payment_ports["state"]["paid_cents"] = created.json()["total_cents"]
    commerce_service.on_installment_paid(db, order_id=order_id, installment_id=installment_id)
    db.commit()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3

    result = commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="rfd_test_2", goods_cents=24000, shipping_cents=0
    )
    db.commit()
    assert result["restocked_quantity"] == 0
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3

    duplicate = commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="rfd_test_2", goods_cents=24000, shipping_cents=0
    )
    db.commit()
    assert duplicate["duplicate"] is True
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3

    after_sale = commerce_service.create_after_sale(
        db,
        user_id=shopper["user"].id,
        order_id=order_id,
        type_="return_refund",
        reason="尺寸不合适，希望退货",
        requested_amount_cents=24000,
    )
    db.commit()
    commerce_service.decide_after_sale(
        db, after_sale_id=after_sale.id, decision="APPROVED", actor_id="stf_1", note="同意退货"
    )
    db.commit()
    commerce_service.submit_return_shipment(
        db,
        user_id=shopper["user"].id,
        after_sale_id=after_sale.id,
        carrier="顺丰",
        tracking_no="SF123",
    )
    db.commit()
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 3, "退货在途不得自动回库"

    restored = commerce_service.restock_after_return(
        db, after_sale_id=after_sale.id, actor_id="stf_1", reason="回仓检验合格"
    )
    db.commit()
    assert restored["restored_quantity"] == 2
    db.refresh(shopper["sku"])
    assert shopper["sku"].stock_on_hand == 5

    movements = list(
        db.scalars(sa.select(InventoryMovement).where(InventoryMovement.action == "RESTOCK"))
    )
    assert len(movements) == 1


# ------------------------------------------------------------------ 应付项与委托
def test_change_installment_requires_positive_amount(
    db: Session, shopper: dict, client: TestClient
) -> None:
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()
    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="change-1")
    order_id = created.json()["order_id"]
    with pytest.raises(AppError) as caught:
        commerce_service.create_change_installment(
            db, order_id=order_id, amount_cents=0, label="加项", change_request_id="chg_1"
        )
    assert caught.value.code == "VALIDATION_ERROR"

    installment_id = commerce_service.create_change_installment(
        db, order_id=order_id, amount_cents=3000, label="加装尾翼", change_request_id="chg_1"
    )
    db.commit()
    again = commerce_service.create_change_installment(
        db, order_id=order_id, amount_cents=3000, label="加装尾翼", change_request_id="chg_1"
    )
    assert again == installment_id
    row = commerce_repo.get_installment(db, installment_id=installment_id)
    assert row is not None and row.kind == "change" and row.amount_cents == 3000


def test_installment_amount_and_kind_checks(db: Session, shopper: dict, client: TestClient) -> None:
    """应付项金额必须 > 0、类型受 CHECK 约束。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()
    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="chk-inst")
    installment_id = created.json()["installment_id"]
    for values in ({"amount_cents": 0}, {"kind": "bonus"}):
        with pytest.raises(IntegrityError):
            db.execute(sa.update(Installment).where(Installment.id == installment_id).values(**values))
            db.flush()
        db.rollback()


def test_order_amount_and_item_quantity_checks(db: Session, shopper: dict, client: TestClient) -> None:
    """金额列非负、订单行数量 > 0 由行内 CHECK 保证。"""
    preview = _preview(client, shopper, sku_id=shopper["sku"].id, quantity=1).json()
    created = _create_order(client, shopper, checkout_token=preview["checkout_token"], key="chk-amt")
    order_id = created.json()["order_id"]
    item = commerce_repo.list_items(db, order_id=order_id)[0]
    for statement in (
        sa.update(Order).where(Order.id == order_id).values(goods_cents=-1),
        sa.update(OrderItem).where(OrderItem.id == item.id).values(quantity=0),
    ):
        with pytest.raises(IntegrityError):
            db.execute(statement)
            db.flush()
        db.rollback()


def test_commission_order_splits_installments(db: Session, shopper: dict) -> None:
    """委托订单：预付款 + 尾款两个应付项，一笔报价只能一个订单。"""
    created = commerce_ports.create_commission_order(
        db,
        user_id=shopper["user"].id,
        quote_id="qte_1",
        quote_version=2,
        total_cents=500000,
        prepayment_cents=250000,
        currency="CNY",
        terms_version="commission-1",
        inquiry_id="inq_1",
        slot_reservation_id=None,
        idempotency_key="quote-1",
    )
    db.commit()
    assert created.totals.total_cents == 500000
    installments = commerce_repo.list_installments(db, order_id=created.order_id)
    assert [row.kind for row in installments] == ["prepayment", "balance"]
    assert sum(row.amount_cents for row in installments) == 500000
    assert all(row.status == "DUE" for row in installments)
    order = commerce_repo.get_order(db, order_id=created.order_id)
    assert order is not None and order.quote_id == "qte_1"

    again = commerce_ports.create_commission_order(
        db,
        user_id=shopper["user"].id,
        quote_id="qte_1",
        quote_version=2,
        total_cents=500000,
        prepayment_cents=250000,
        currency="CNY",
        terms_version="commission-1",
        inquiry_id="inq_1",
        slot_reservation_id=None,
        idempotency_key="quote-1",
    )
    assert again.order_id == created.order_id
    assert commerce_repo.get_order_by_quote(db, quote_id="qte_1").id == created.order_id  # type: ignore[union-attr]


def test_shipping_threshold_and_remote_surcharge(db: Session) -> None:
    policy = commerce_service.commerce_policy(db)
    assert commerce_service.compute_shipping_cents(policy, goods_cents=1000, region_code="CN-31") == 1800
    assert commerce_service.compute_shipping_cents(policy, goods_cents=30000, region_code="CN-31") == 0
    assert commerce_service.compute_shipping_cents(policy, goods_cents=1000, region_code="CN-65") == 2800


# ------------------------------------------------------------------ 购物车
def test_cart_put_is_absolute_quantity(client: TestClient, db: Session, shopper: dict) -> None:
    sku_id = shopper["sku"].id
    first = client.put(f"/api/v1/cart/items/{sku_id}", json={"quantity": 3})
    assert first.status_code == 200, first.text
    assert first.json()["items"][0]["quantity"] == 3
    second = client.put(f"/api/v1/cart/items/{sku_id}", json={"quantity": 1})
    assert second.json()["items"][0]["quantity"] == 1
    removed = client.delete(f"/api/v1/cart/items/{sku_id}")
    assert removed.json()["items"] == []
    over = client.put(f"/api/v1/cart/items/{sku_id}", json={"quantity": 99})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cart_requires_login(client: TestClient) -> None:
    response = client.get("/api/v1/cart")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
