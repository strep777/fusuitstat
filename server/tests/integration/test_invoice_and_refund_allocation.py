"""发票闭环与退款按行分摊回归（R21）。

依据：
- `08_商城订单支付与退款.md` 第 91 行：**部分退款分配到商品行、运费及已用优惠；不能超过
  各行剩余实付分摊**；退款不自动补库存；
- `03_站点路由与页面功能.md`：`/admin/orders` 与订单详情需要能处理开票；
- `06_API接口契约.md`：后台动作用权限码 + 审计；
- `功能确认清单-已确认.md` C16/C17：售后与发票按后台配置执行（N07 跨境首版值待补）。

本轮补齐的两个缺口：
1. **发票只申请不签发**：客户能提交开票申请，但后台没有任何签发/作废入口，状态永远停在
   `PENDING`；`invoice_enabled=false` 时接口也不拒绝（开关形同虚设）。
2. **退款只有订单级汇总**：`order_items.refunded_goods_cents` 从未被写入，08 文档要求的
   「按行分摊、不得超过各行剩余实付」没有实现。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commerce import repository as commerce_repo
from app.modules.commerce import service as commerce_service
from app.modules.commerce.models import Order, OrderItem
from app.modules.notifications.models import Notification
from app.platform.errors import AppError
from app.platform.ids import new_id, now_ms
from tests.conftest import admin_session, create_staff

PUBLIC_ORIGIN = "http://127.0.0.1:5173"


# ------------------------------------------------------------------ 夹具
def _invoice_order(db: Session, client: TestClient, shopper: dict) -> tuple[Order, dict]:
    """一个有已核实收款的订单 + 客户会话（走真实结算链路）。"""
    preview = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": shopper["sku"].id, "quantity": 2}],
            "address_id": shopper["address"].id,
        },
    )
    assert preview.status_code == 200, preview.text
    created = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview.json()["checkout_token"],
            "address_id": shopper["address"].id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": f"inv-{new_id('k')[:8]}"},
    )
    assert created.status_code == 201, created.text
    order = commerce_repo.get_order(db, order_id=created.json()["order_id"])
    assert order is not None
    # 直接标记已收款（收款核销本身由支付模块用例覆盖，这里只构造开票前置条件）
    order.paid_cents = order.total_cents
    order.status = "ACTIVE"
    order.paid_at = now_ms()
    db.commit()
    return order, created.json()


def _set_invoice_policy(db: Session, **value: object) -> None:
    """写 `commerce.policy` 的顶层开票开关/发票块。

    开关是**顶层** `invoice_enabled`（`CommercePolicy.invoice_enabled`），不是 `invoice` 块里的
    字段；走 `update_setting` 是为了让测试与后台保存走同一条校验路径。
    """
    _patch_policy(db, **value)


@pytest.fixture()
def invoicing(db: Session, client: TestClient):
    from tests.conftest import create_address, create_product, create_sku, create_user, issue_login

    user = create_user(db, nickname="开票客户", phone="13800001111")
    address = create_address(db, user_id=user.id)
    product = create_product(db, slug="invoice-suit", title="开票示例兽装")
    sku = create_sku(db, product_id=product.id, price_cents=12000, stock_on_hand=5)
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    _set_invoice_policy(db, invoice_enabled=True)
    db.commit()
    return {"user": user, "address": address, "product": product, "sku": sku, "csrf": csrf}


def _finance_staff(db: Session, client: TestClient) -> dict[str, str]:
    staff, secret = create_staff(db, login_name="finance-ops", roles=["finance"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    return {"X-CSRF-Token": csrf["csrf_token"], "Origin": PUBLIC_ORIGIN}


# ------------------------------------------------------------------ 发票
def test_invoice_request_respects_the_switch(db: Session, client: TestClient, invoicing: dict) -> None:
    order, _ = _invoice_order(db, client, invoicing)
    _set_invoice_policy(db, invoice_enabled=False)
    denied = client.post(
        f"/api/v1/orders/{order.id}/invoices",
        json={"type": "personal", "title": "张三"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FEATURE_DISABLED"

    _set_invoice_policy(db, invoice_enabled=True)
    accepted = client.post(
        f"/api/v1/orders/{order.id}/invoices",
        json={"type": "personal", "title": "张三"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["status"] == "PENDING"
    assert accepted.json()["amount_cents"] == order.total_cents
    # 同一订单同类型不能重复申请
    again = client.post(
        f"/api/v1/orders/{order.id}/invoices",
        json={"type": "personal", "title": "张三"},
    )
    assert again.status_code == 409, again.text


def test_admin_can_issue_invoice_and_customer_is_notified(
    db: Session, client: TestClient, invoicing: dict
) -> None:
    order, _ = _invoice_order(db, client, invoicing)
    requested = client.post(
        f"/api/v1/orders/{order.id}/invoices",
        json={"type": "business", "title": "示例文化有限公司", "tax_no": "91310000MA1K3XXXXX"},
    )
    assert requested.status_code == 201, requested.text
    invoice_id = requested.json()["id"]
    headers = _finance_staff(db, client)

    listed = client.get("/api/v1/admin/invoices", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["items"] if item["id"] == invoice_id)
    assert row["status"] == "PENDING" and row["order_no"] == order.order_no
    assert row["tax_no"] == "91310000MA1K3XXXXX", "有权限的运营需要看到抬头与税号"

    # 没有发票号不能签发
    missing = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/issue", json={"reference": "x"}, headers=headers
    )
    assert missing.status_code == 422, missing.text

    issued = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/issue",
        json={"reference": "INV-2026-0001", "note": "电子发票已发送到申请邮箱"},
        headers=headers,
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "ISSUED"
    assert issued.json()["invoice_reference"] == "INV-2026-0001"
    assert issued.json()["issued_at"] is not None

    # 客户收到通知（只带发票号与金额，不带税号）
    db.expire_all()
    notifications = list(
        db.scalars(
            sa.select(Notification).where(Notification.event_type == "order.invoice_issued")
        )
    )
    assert len(notifications) == 1
    assert "INV-2026-0001" in notifications[0].body
    assert "91310000MA1K3XXXXX" not in notifications[0].body

    # 同一发票不能签发两次
    twice = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/issue",
        json={"reference": "INV-2026-0002"},
        headers=headers,
    )
    assert twice.status_code == 409, twice.text
    # 已签发的发票不能作废
    void = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/void", json={"reason": "开错了"}, headers=headers
    )
    assert void.status_code == 409, void.text


def test_invoice_void_records_reason_and_requires_permission(
    db: Session, client: TestClient, invoicing: dict
) -> None:
    order, _ = _invoice_order(db, client, invoicing)
    requested = client.post(
        f"/api/v1/orders/{order.id}/invoices", json={"type": "personal", "title": "李四"}
    )
    invoice_id = requested.json()["id"]

    # 无权限：客服角色没有 invoice.manage
    support_headers = _support_staff(db, client)
    denied = client.get("/api/v1/admin/invoices", headers=support_headers)
    assert denied.status_code == 403, denied.text

    headers = _finance_staff(db, client)
    empty_reason = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/void", json={"reason": "x"}, headers=headers
    )
    assert empty_reason.status_code == 422

    voided = client.post(
        f"/api/v1/admin/invoices/{invoice_id}/void",
        json={"reason": "抬头信息无法核验，请联系客户补充"},
        headers=headers,
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "VOID"
    assert "无法核验" in voided.json()["note"]
    db.expire_all()
    row = commerce_repo.get_invoice(db, invoice_id=invoice_id)
    assert row is not None and row.status == "VOID" and row.issued_at is None


def _support_staff(db: Session, client: TestClient) -> dict[str, str]:
    staff, secret = create_staff(db, login_name="support-ops", roles=["support"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    return {"X-CSRF-Token": csrf["csrf_token"], "Origin": PUBLIC_ORIGIN}


# ------------------------------------------------------------------ 跨境可见性（C02/C24、C17）
def _patch_policy(db: Session, **blocks: object) -> None:
    """走真实的设置更新链路（含 `validate_setting`），而不是直接改行。"""
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    row = get_setting(db, "commerce.policy")
    assert row is not None
    merged = {**(row.value or {}), **blocks}
    update_setting(
        db,
        key="commerce.policy",
        value=merged,
        version=row.version,
        actor_id=None,
        reason="跨境回归测试",
    )
    db.commit()


def test_cross_border_policy_block_is_validated(db: Session) -> None:
    """`commerce.policy` 必须真的接受跨境块——否则运营配置不了预估比例。"""
    from app.modules.content.repository import get_setting

    _patch_policy(db, cross_border={"enabled": True, "duty_percent": 10, "note": "以清关为准"})
    row = get_setting(db, "commerce.policy")
    assert row is not None
    assert row.value["cross_border"] == {"enabled": True, "duty_percent": 10, "note": "以清关为准"}

    for bad in (
        {"duty_percent": 101},
        {"duty_percent": -1},
        {"enabled": "true"},
        {"duty_note": "字段名写错时必须拒绝"},
    ):
        with pytest.raises(AppError) as error:
            _patch_policy(db, cross_border=bad)
        assert error.value.code == "VALIDATION_ERROR"
    db.rollback()
    refreshed = get_setting(db, "commerce.policy")
    assert refreshed is not None and refreshed.value["cross_border"]["duty_percent"] == 10


def test_cross_border_estimate_is_honest_and_not_charged(
    db: Session, client: TestClient, invoicing: dict
) -> None:
    """境内不提示跨境；未配置比例时不给数字；配置后只给预估且**不进应付合计**。"""
    from tests.conftest import create_address

    domestic = client.post(
        "/api/v1/checkout/preview",
        json={
            "items": [{"sku_id": invoicing["sku"].id, "quantity": 2}],
            "address_id": invoicing["address"].id,
        },
    )
    assert domestic.status_code == 200, domestic.text
    assert domestic.json()["cross_border"] is False
    assert domestic.json()["duty_estimate_cents"] is None
    assert domestic.json()["estimate_available"] is False

    # 独立美元运费为测试前置配置；不得沿用人民币模板金额。
    _patch_policy(db, regions={
        "CN": {"currency": "CNY"},
        "US": {"currency": "USD", "shipping": {
            "currency": "USD", "flat_cents": 2500, "free_threshold_cents": 0,
            "remote_region_surcharge_cents": 0, "max_cents": 0,
        }},
    })
    overseas = create_address(db, user_id=invoicing["user"].id, region_code="US-CA", city="Los Angeles")
    # C02/C24：海外区以 USD 结算，因此跨境预估用例必须用**美元定价**的 SKU；
    # 用人民币定价的商品在海外区会被结算直接拒绝（见 test_region_currency.py）。
    from tests.conftest import create_sku

    usd_sku = create_sku(
        db,
        product_id=invoicing["product"].id,
        price_cents=12000,
        stock_on_hand=5,
        label="USD 规格",
        currency="USD",
    )
    db.commit()

    # 未配置比例：判定为跨境，但不给数字，只给「以清关为准」的说明
    _patch_policy(db, cross_border={"enabled": True, "duty_percent": 0})
    unconfigured = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": usd_sku.id, "quantity": 2}], "address_id": overseas.id},
    )
    assert unconfigured.status_code == 200, unconfigured.text
    body = unconfigured.json()
    assert body["cross_border"] is True
    assert body["currency"] == "USD"
    assert body["duty_estimate_cents"] is None and body["estimate_available"] is False
    assert body["duty_note"], "必须说明关税与税费以清关为准"

    # 配置 10%：按「券后且积分抵扣后的商品金额」给出预估，应付合计不变
    _patch_policy(db, cross_border={"enabled": True, "duty_percent": 10})
    configured = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": usd_sku.id, "quantity": 2}], "address_id": overseas.id},
    )
    assert configured.status_code == 200, configured.text
    priced = configured.json()
    base = priced["goods_cents"] - priced["discount_cents"] - priced["points_discount_cents"]
    assert priced["duty_estimate_cents"] == base * 10 // 100
    assert priced["estimate_available"] is True and priced["duty_percent"] == 10
    assert priced["total_cents"] == base + priced["shipping_cents"], "预估不得计入应付合计"

    # 关闭跨境块后不再给预估（比例仍在配置里）
    _patch_policy(db, cross_border={"enabled": False, "duty_percent": 10})
    disabled = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": usd_sku.id, "quantity": 2}], "address_id": overseas.id},
    )
    assert disabled.json()["cross_border"] is True
    assert disabled.json()["duty_estimate_cents"] is None


def test_cross_border_is_frozen_on_the_order(db: Session, client: TestClient, invoicing: dict) -> None:
    """下单时冻结跨境判定与预估：之后改配置不改历史订单的说法。"""
    from tests.conftest import create_address, create_sku

    # 独立美元运费为测试前置配置；不得沿用人民币模板金额。
    _patch_policy(db, regions={
        "CN": {"currency": "CNY"},
        "US": {"currency": "USD", "shipping": {
            "currency": "USD", "flat_cents": 2500, "free_threshold_cents": 0,
            "remote_region_surcharge_cents": 0, "max_cents": 0,
        }},
    })
    overseas = create_address(db, user_id=invoicing["user"].id, region_code="US-CA", city="Los Angeles")
    # 海外区以 USD 结算：跨境订单必须用美元定价的 SKU（不按汇率折算）。
    usd_sku = create_sku(
        db,
        product_id=invoicing["product"].id,
        price_cents=12000,
        stock_on_hand=5,
        label="USD 规格",
        currency="USD",
    )
    db.commit()
    _patch_policy(db, cross_border={"enabled": True, "duty_percent": 10, "note": "以清关为准"})

    preview = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": usd_sku.id, "quantity": 2}], "address_id": overseas.id},
    )
    assert preview.status_code == 200, preview.text
    created = client.post(
        "/api/v1/orders",
        json={
            "checkout_token": preview.json()["checkout_token"],
            "address_id": overseas.id,
            "terms_version": "shop-1",
            "accepted": True,
        },
        headers={"Idempotency-Key": f"cb-{new_id('k')[:8]}"},
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["order_id"]

    detail = client.get(f"/api/v1/orders/{order_id}")
    assert detail.status_code == 200, detail.text
    frozen = detail.json()
    assert frozen["cross_border"] is True
    assert frozen["duty_estimate_cents"] == preview.json()["duty_estimate_cents"]
    assert frozen["duty_note"] == "以清关为准"

    # 改配置不影响已下单的冻结值
    _patch_policy(db, cross_border={"enabled": False, "duty_percent": 0, "note": "改了配置"})
    again = client.get(f"/api/v1/orders/{order_id}")
    assert again.json()["cross_border"] is True
    assert again.json()["duty_estimate_cents"] == frozen["duty_estimate_cents"]
    assert again.json()["duty_note"] == "以清关为准"


# ------------------------------------------------------------------ 退款按行分摊
def test_refund_allocated_across_lines_proportionally() -> None:
    """按行分摊：优先记到指定行，其余按剩余实付比例；不超各行剩余，合计等于退款额。"""

    class _Line:
        def __init__(self, item_id: str, cash: int, refunded: int = 0) -> None:
            self.id = item_id
            self.cash_goods_cents = cash
            self.refunded_goods_cents = refunded

    lines = [_Line("a", 10000), _Line("b", 20000), _Line("c", 30000)]

    # 全额退款：三行各自退满
    full = commerce_service.allocate_refund_to_items(lines, 60000)
    assert full == {"a": 10000, "b": 20000, "c": 30000}

    # 部分退款 10000：按比例（1:2:3）→ 1667/3333/5000
    part = commerce_service.allocate_refund_to_items(lines, 10000)
    assert sum(part.values()) == 10000
    assert part["a"] == 1667 and part["b"] == 3333 and part["c"] == 5000

    # 指定行优先：给 b 退 5000 → b 先拿满 5000
    preferred = commerce_service.allocate_refund_to_items(lines, 5000, preferred_item_id="b")
    assert preferred["b"] == 5000 and preferred["a"] == 0 and preferred["c"] == 0

    # 指定行装不下时，剩余分给其他行，且不超过各自剩余
    preferred_big = commerce_service.allocate_refund_to_items(
        [_Line("a", 1000), _Line("b", 2000)], 2500, preferred_item_id="b"
    )
    assert preferred_big == {"a": 500, "b": 2000}

    # 已经退满的行不再分摊
    exhausted = commerce_service.allocate_refund_to_items(
        [_Line("a", 1000, refunded=1000), _Line("b", 2000)], 1500
    )
    assert exhausted == {"a": 0, "b": 1500}

    # 退款额超过总剩余：按总剩余封顶（多退的钱不由商品行承担）
    capped = commerce_service.allocate_refund_to_items([_Line("a", 1000)], 5000)
    assert capped == {"a": 1000}


def test_line_refunds_never_exceed_line_capacity(
    db: Session, client: TestClient, invoicing: dict
) -> None:
    """真实退款链路：按行写入已退款金额，合计不超过各行实付。"""
    order, _ = _invoice_order(db, client, invoicing)
    items = commerce_repo.list_items(db, order_id=order.id)
    assert items, "订单必须有商品行"
    total_cash = sum(item.cash_goods_cents for item in items)

    first = commerce_service.on_refund_succeeded(
        db, order_id=order.id, refund_id="refund-line-1", goods_cents=total_cash // 3, shipping_cents=0
    )
    db.commit()
    assert sum(first["line_allocation"].values()) == total_cash // 3
    db.expire_all()
    for item in commerce_repo.list_items(db, order_id=order.id):
        assert item.refunded_goods_cents <= item.cash_goods_cents

    # 再退一次剩余：每行恰好退满，不会超过
    remaining = total_cash - sum(
        item.refunded_goods_cents for item in commerce_repo.list_items(db, order_id=order.id)
    )
    commerce_service.on_refund_succeeded(
        db, order_id=order.id, refund_id="refund-line-2", goods_cents=remaining, shipping_cents=0
    )
    db.commit()
    db.expire_all()
    rows = commerce_repo.list_items(db, order_id=order.id)
    assert all(item.refunded_goods_cents == item.cash_goods_cents for item in rows)
    assert sum(item.refunded_goods_cents for item in rows) == total_cash

    # 重复投递同一退款事件：不重复分摊
    again = commerce_service.on_refund_succeeded(
        db, order_id=order.id, refund_id="refund-line-2", goods_cents=remaining, shipping_cents=0
    )
    db.commit()
    assert again["duplicate"] is True and again["line_allocation"] == {}
    db.expire_all()
    assert sum(item.refunded_goods_cents for item in commerce_repo.list_items(db, order_id=order.id)) == total_cash


def test_after_sale_item_refund_prefers_that_line(
    db: Session, client: TestClient, invoicing: dict
) -> None:
    """售后指向某一行时，退款优先记到那一行。"""
    order, _ = _invoice_order(db, client, invoicing)
    items = commerce_repo.list_items(db, order_id=order.id)
    target = items[0]
    # 建一条指向该行的售后记录（走服务层，避免依赖后台会话）
    commerce_service.create_after_sale(
        db,
        user_id=order.user_id,
        order_id=order.id,
        type_="refund_only",
        reason="尺码不合适，申请部分退款",
        order_item_id=target.id,
        requested_amount_cents=target.cash_goods_cents // 2,
    )
    db.commit()

    result = commerce_service.on_refund_succeeded(
        db,
        order_id=order.id,
        refund_id="refund-line-3",
        goods_cents=target.cash_goods_cents // 2,
        shipping_cents=0,
    )
    db.commit()
    assert result["line_allocation"][target.id] == target.cash_goods_cents // 2
    db.expire_all()
    refreshed = db.get(OrderItem, target.id)
    assert refreshed is not None
    assert refreshed.refunded_goods_cents == target.cash_goods_cents // 2
