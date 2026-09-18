"""订单积分抵扣闭环回归（R19b）。

依据：
- `09_积分与反馈功能规格.md` 第 73 条：**结算冻结积分 → 支付确认消费 → 关闭订单释放**；
  现金退款与积分返还按原订单分摊；先用条件更新扣可用额度，禁止双标签页同时花同一积分；
  兑换细则（N02–N04）未定前入口保持关闭；
- `08_商城订单支付与退款.md`：应付金额由服务端计算，客户端只能提交 SKU、数量、地址与选择。

本文件锁定的是机制与不变量，业务数值全部来自测试里显式写入的运营配置：
1. 开启后：预览给出抵扣金额 → 建单在同一事务里冻结积分 → 订单应付金额同步减少；
2. 支付确认：冻结转为真实扣减并写 REDEMPTION 流水（重复调用不重复扣）；
3. 关闭订单（取消 / 超时）：释放冻结，账面不变、不写流水；
4. 退款：按原订单分摊返还积分（目标值语义，重复投递不重复返还）；
5. 条件更新保证两笔订单不能花掉同一份积分；
6. 未开启或未配置规则时拒绝，未使用积分时结算流程完全不受影响。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commerce import repository as commerce_repo
from app.modules.commerce import service as commerce_service
from app.modules.commerce.models import Order
from app.modules.loyalty.models import PointLedger
from app.platform.errors import AppError
from app.platform.ids import now_ms
from tests.conftest import create_address, create_product, create_sku, create_user, issue_login


def _configure(db: Session, *, points_per_yuan: int = 100, min_points: int = 1,
               max_ratio: int = 100, enabled: bool = True, flag: bool = True,
               restore_on_refund: bool = True) -> None:
    from app.modules.content.models import SiteSetting
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    loyalty = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "loyalty.rules"))
    assert loyalty is not None
    loyalty.value = {
        **(loyalty.value or {}),
        "redemption_enabled": enabled,
        "redemption_points_per_yuan": points_per_yuan,
        "redemption_min_points": min_points,
        "redemption_max_ratio_percent": max_ratio,
        "redemption_restore_on_refund": restore_on_refund,
    }
    flags = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "platform.feature_flags"))
    assert flags is not None
    flags.value = {**(flags.value or {}), "points_redemption": flag}
    db.commit()


def _grant(db: Session, user_id: str, points: int) -> None:
    from app.modules.loyalty import repository as repo

    account = repo.ensure_account(db, user_id=user_id)
    repo.add_balance(db, account_id=account.id, delta=points)
    db.commit()


def _account(db: Session, user_id: str) -> dict[str, int]:
    from app.modules.loyalty.ports import account_view

    return account_view(db, user_id=user_id)


@pytest.fixture()
def shopper(db: Session, client: TestClient):
    """一位有地址、有积分余额的客户；商品 120 元（12000 分）。"""
    user = create_user(db, nickname="积分星友", phone="13800002222")
    address = create_address(db, user_id=user.id)
    product = create_product(db, slug="suit-points", title="积分示例兽装")
    sku = create_sku(db, product_id=product.id, price_cents=12000, stock_on_hand=5)
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()
    return {"user": user, "address": address, "product": product, "sku": sku, "csrf": csrf}


def _preview(client: TestClient, shopper: dict, *, redeem_points: int = 0, quantity: int = 1) -> dict:
    response = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": quantity}],
            "address_id": shopper["address"].id,
            "redeem_points": redeem_points,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create(client: TestClient, shopper: dict, preview: dict, *, key: str,
            redeem_points: int = 0) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
            "redeem_points": redeem_points,
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _redemption_entries(db: Session, order_id: str, entry_type: str) -> list[PointLedger]:
    db.expire_all()
    return list(
        db.scalars(
            sa.select(PointLedger).where(
                PointLedger.source_type == "redemption",
                PointLedger.source_id == f"order:{order_id}",
                PointLedger.entry_type == entry_type,
            )
        )
    )


# ------------------------------------------------------------------ 主流程
def test_preview_and_create_freeze_points_and_reduce_total(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 5000)

    preview = _preview(client, shopper, redeem_points=1500)
    assert preview["points_discount_cents"] == 1500
    assert preview["points_redeemed"] == 1500
    assert preview["total_cents"] == preview["goods_cents"] - 1500 + preview["shipping_cents"]
    assert preview["points_quote"]["max_points"] == 5000
    assert preview["points_quote"]["points_name"] == "星绒币"

    created = _create(client, shopper, preview, key="points-1", redeem_points=1500)
    assert created["points_discount_cents"] == 1500
    assert created["points_redeemed"] == 1500

    db.expire_all()
    order = commerce_repo.get_order(db, order_id=created["order_id"])
    assert order is not None
    assert order.points_redeemed == 1500 and order.points_discount_cents == 1500
    assert order.total_cents == order.goods_cents - 1500 + order.shipping_cents
    installments = commerce_repo.list_installments(db, order_id=order.id)
    assert installments[0].amount_cents == order.total_cents, "应付项必须等于抵扣后的金额"

    account = _account(db, shopper["user"].id)
    assert account["posted_balance"] == 5000 and account["reserved_points"] == 1500
    assert account["available_points"] == 3500

    # 订单详情对本人展示积分抵扣与已返还
    detail = client.get(f"/api/v1/orders/{order.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["points_discount_cents"] == 1500
    assert detail.json()["points_redeemed"] == 1500
    assert detail.json()["points_restored"] == 0

    # 未使用积分的订单完全不受影响
    plain = _preview(client, shopper, redeem_points=0)
    assert plain["points_discount_cents"] == 0 and plain["points_quote"] is None
    assert plain["total_cents"] == 12000 + plain["shipping_cents"]


def test_redemption_requires_feature_and_complete_rules(
    client: TestClient, db: Session, shopper: dict
) -> None:
    _grant(db, shopper["user"].id, 5000)
    _configure(db, flag=False)
    denied = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": 1}],
            "address_id": shopper["address"].id,
            "redeem_points": 100,
        },
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FEATURE_DISABLED"

    _configure(db, flag=True, points_per_yuan=0, min_points=0, max_ratio=0)
    not_configured = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": 1}],
            "address_id": shopper["address"].id,
            "redeem_points": 100,
        },
    )
    assert not_configured.status_code == 403
    assert not_configured.json()["error"]["code"] == "FEATURE_DISABLED"

    # 未使用积分的结算在关闭状态下照常可用
    normal = _preview(client, shopper, redeem_points=0)
    assert normal["points_discount_cents"] == 0


def test_quote_change_between_preview_and_create_is_rejected(
    client: TestClient, db: Session, shopper: dict
) -> None:
    """预览后余额被花掉：建单必须让用户重新确认，而不是默默少抵。"""
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 2000)
    preview = _preview(client, shopper, redeem_points=2000)
    assert preview["points_discount_cents"] == 2000

    from app.modules.loyalty import repository as repo

    account = repo.ensure_account(db, user_id=shopper["user"].id)
    repo.add_balance(db, account_id=account.id, delta=-2000)
    db.commit()

    response = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
            "redeem_points": 2000,
        },
        headers={"Idempotency-Key": "points-changed"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PRICE_CHANGED"
    db.expire_all()
    assert (
        commerce_repo.order_by_idempotency_key(db, user_id=shopper["user"].id, key="points-changed")
        is None
    ), "被拒绝的请求不得留下订单"


def test_two_orders_cannot_spend_the_same_points(db: Session, shopper: dict) -> None:
    """条件更新保证两笔冻结不能超出可用额度（双标签页场景）。"""
    from app.modules.loyalty import ports as loyalty_ports

    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 1000)

    first = loyalty_ports.reserve_order_redemption(
        db, user_id=shopper["user"].id, order_id="order-a", points=800, ttl_minutes=15
    )
    db.commit()
    assert first["points"] == 800 and first["state"] == "HELD"

    with pytest.raises(AppError) as error:
        loyalty_ports.reserve_order_redemption(
            db, user_id=shopper["user"].id, order_id="order-b", points=800, ttl_minutes=15
        )
    assert error.value.code == "POINTS_INSUFFICIENT"
    db.rollback()
    assert loyalty_ports.order_redemption_view(db, order_id="order-b")["state"] == "NONE"
    assert _account(db, shopper["user"].id)["reserved_points"] == 800


def test_payment_consumes_frozen_points_once(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-pay", redeem_points=1000)
    order_id = created["order_id"]

    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(
        db, order_id=order_id, installment_id=created["installment_id"]
    )
    db.commit()

    account = _account(db, shopper["user"].id)
    assert account["posted_balance"] == 2000, "支付确认后账面真正扣减"
    assert account["reserved_points"] == 0, "冻结必须被结清"
    entries = _redemption_entries(db, order_id, "REDEMPTION")
    assert len(entries) == 1 and entries[0].delta == -1000

    # 重复投递同一事件：不重复扣减、不重复写流水
    commerce_service.on_installment_paid(
        db, order_id=order_id, installment_id=created["installment_id"]
    )
    db.commit()
    assert _account(db, shopper["user"].id)["posted_balance"] == 2000
    assert len(_redemption_entries(db, order_id, "REDEMPTION")) == 1


def test_cancel_releases_frozen_points_without_ledger_entry(
    client: TestClient, db: Session, shopper: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-cancel", redeem_points=1000)
    assert _account(db, shopper["user"].id)["reserved_points"] == 1000

    cancelled = client.post(
        f"/api/v1/orders/{created['order_id']}/cancel", json={"reason": "不想要了"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    account = _account(db, shopper["user"].id)
    assert account["posted_balance"] == 3000 and account["reserved_points"] == 0
    assert account["available_points"] == 3000
    assert _redemption_entries(db, created["order_id"], "REDEMPTION") == []
    assert _redemption_entries(db, created["order_id"], "REDEMPTION_RELEASE") == []


def test_expired_order_releases_frozen_points(
    client: TestClient, db: Session, shopper: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-expire", redeem_points=1000)

    db.execute(
        sa.update(Order)
        .where(Order.id == created["order_id"])
        .values(expires_at=now_ms() - 1)
    )
    db.commit()
    processed = commerce_service.expire_due_orders(db)
    db.commit()
    assert processed == 1
    account = _account(db, shopper["user"].id)
    assert account["reserved_points"] == 0 and account["available_points"] == 3000


def test_full_refund_restores_points_once(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-refund", redeem_points=1000)
    order_id = created["order_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(
        db, order_id=order_id, installment_id=created["installment_id"]
    )
    db.commit()
    assert _account(db, shopper["user"].id)["posted_balance"] == 2000

    result = commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-1",
        goods_cents=created["total_cents"], shipping_cents=0,
    )
    db.commit()
    assert result["refunded_cents"] == created["total_cents"]
    account = _account(db, shopper["user"].id)
    assert account["posted_balance"] == 3000, "全额退款后积分全部返还"
    entries = _redemption_entries(db, order_id, "REDEMPTION_RELEASE")
    assert len(entries) == 1 and entries[0].delta == 1000
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.points_restored == 1000

    # 重复投递同一退款事件：不重复返还
    again = commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-1",
        goods_cents=created["total_cents"], shipping_cents=0,
    )
    db.commit()
    assert again["duplicate"] is True
    assert _account(db, shopper["user"].id)["posted_balance"] == 3000
    assert len(_redemption_entries(db, order_id, "REDEMPTION_RELEASE")) == 1

    # 新的退款事件但金额已超付：返还上限仍是本单用掉的积分
    overshoot = commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-2",
        goods_cents=created["total_cents"], shipping_cents=0,
    )
    db.commit()
    assert overshoot["order_id"] == order_id
    assert _account(db, shopper["user"].id)["posted_balance"] == 3000


def test_partial_refund_restores_proportionally(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-partial", redeem_points=1000)
    order_id = created["order_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(
        db, order_id=order_id, installment_id=created["installment_id"]
    )
    db.commit()

    half = created["total_cents"] // 2
    commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-half", goods_cents=half, shipping_cents=0
    )
    db.commit()
    expected = 1000 * half // created["total_cents"]
    account = _account(db, shopper["user"].id)
    assert account["posted_balance"] == 2000 + expected
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.points_restored == expected

    # 剩余部分退款：累计返还到本单用掉的积分，不超过
    commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-rest",
        goods_cents=created["total_cents"] - half, shipping_cents=0,
    )
    db.commit()
    assert _account(db, shopper["user"].id)["posted_balance"] == 3000


def test_restore_can_be_disabled_by_rules(
    client: TestClient, db: Session, shopper: dict, payment_ports: dict
) -> None:
    """退款恢复细则由运营配置；关掉时不返还，但订单与流水保持不变。"""
    _configure(db, points_per_yuan=100, min_points=1, max_ratio=100, restore_on_refund=False)
    _grant(db, shopper["user"].id, 3000)
    preview = _preview(client, shopper, redeem_points=1000)
    created = _create(client, shopper, preview, key="points-no-restore", redeem_points=1000)
    order_id = created["order_id"]
    payment_ports["state"]["paid_cents"] = created["total_cents"]
    commerce_service.on_installment_paid(
        db, order_id=order_id, installment_id=created["installment_id"]
    )
    db.commit()

    commerce_service.on_refund_succeeded(
        db, order_id=order_id, refund_id="refund-nr",
        goods_cents=created["total_cents"], shipping_cents=0,
    )
    db.commit()
    assert _account(db, shopper["user"].id)["posted_balance"] == 2000
    assert _redemption_entries(db, order_id, "REDEMPTION_RELEASE") == []
    db.expire_all()
    order = commerce_repo.get_order(db, order_id=order_id)
    assert order is not None and order.points_restored == 0
