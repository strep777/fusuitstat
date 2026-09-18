"""商务模块用例：购物车、结算、下单、取消、履约、售后与发票。

金额与库存不变量（08 文档，唯一规则源）：
1. 应付金额由服务端依据当前商品、促销与配送范围计算；客户端只提交 SKU、数量、
   地址 ID 与优惠选择，不得提交单价、金额或积分；
2. 结算预览返回一次性 ``checkout_token``；创建订单在**同一事务**内重新校验价格与
   库存，不一致返回 ``PRICE_CHANGED`` / ``STOCK_NOT_ENOUGH``；
3. 到期进入 CLOSING：先关闭或查询渠道，确认没有在途成功付款后才取消并释放预留；
   支付成功与释放用条件更新竞争，不允许同时发生；
4. 售后退款不自动补库存；只有回仓检验合格或独占角色经授权与运营审核可复售后，
   才调用 ``catalog.service.restock``；
5. 收款成功后的库存核销由支付模块的事件消费者调用 ``on_installment_paid``。
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog import ports as catalog_ports
from app.modules.commerce import repository as repo
from app.modules.commerce.models import (
    INSTALLMENT_KINDS,
    AddressSnapshot,
    AfterSale,
    AfterSaleMessage,
    CartItem,
    CheckoutSession,
    Installment,
    Invoice,
    Order,
    OrderAddressChange,
    OrderItem,
    Shipment,
)
from app.modules.content.ports import feature_flags, require_feature, setting_value
from app.modules.contracts import CreatedOrder, OrderTotals
from app.modules.identity import ports as identity_ports
from app.platform import audit
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import add_minutes_ms, iso, now_ms, random_token
from app.platform.reliability import digest, enqueue
from app.platform.security import decrypt_pii, encrypt_pii, hash_token

ORDER_OPERATION = "commerce.create_order"
ORDER_NO_PREFIX = "SO"
DEFAULT_CHECKOUT_TTL_MINUTES = 30
IDEMPOTENCY_TTL_HOURS = 24

logger = logging.getLogger("starium.commerce")

# 在售商品下单后订单有效期（分钟）；与结算令牌同一时限，避免“令牌已过期但订单还挂着”。
ORDER_TTL_MINUTES = DEFAULT_CHECKOUT_TTL_MINUTES

DEFAULT_POLICY: dict[str, Any] = {
    "currency": "CNY",
    "default_terms_version": "shop-1",
    "shipping": {
        "mode": "flat",
        "flat_cents": 1800,
        "free_threshold_cents": 30000,
        "remote_region_surcharge_cents": 1000,
        "remote_region_codes": ["CN-65", "CN-54", "CN-63", "CN-15"],
        "max_cents": 20000,
    },
    "coupons": {},
    "after_sale": {
        "window_days": 15,
        "types": ["refund_only", "return_refund", "repair", "exchange"],
        "note": "定制商品的特殊退货条件不消除质量瑕疵责任。",
    },
    "invoice": {
        "types": ["personal", "business"],
        "note": "抬头与税号仅用于开票，按最小化原则加密保存。",
    },
}

MAX_TOTAL_CENTS = 100_000_000_00  # 单笔订单上限，防超大金额（08 文档）


# ------------------------------------------------------------------ 政策与金额
def commerce_policy(session: Session) -> dict[str, Any]:
    """读取运营可配置的商务政策（C10/C16/C17），未配置时用文档默认值。"""
    stored = setting_value(session, "commerce.policy", {}) or {}
    policy = {**DEFAULT_POLICY, **stored}
    shipping = {**DEFAULT_POLICY["shipping"], **(stored.get("shipping") or {})}
    coupons = {**DEFAULT_POLICY["coupons"], **(stored.get("coupons") or {})}
    policy["shipping"] = shipping
    policy["coupons"] = coupons
    policy["coupons_enabled"] = feature_flags(session)["coupons"]
    policy["after_sale"] = {**DEFAULT_POLICY["after_sale"], **(stored.get("after_sale") or {})}
    policy["invoice"] = {**DEFAULT_POLICY["invoice"], **(stored.get("invoice") or {})}
    return policy


def compute_shipping_cents(
    policy: dict[str, Any], *, goods_cents: int, region_code: str
) -> int:
    """按配送范围与模板计算运费；只使用整数分。"""
    shipping = policy["shipping"]
    if goods_cents <= 0:
        return 0
    mode = str(shipping.get("mode", "flat"))
    flat = int(shipping.get("flat_cents", 0))
    threshold = int(shipping.get("free_threshold_cents", 0) or 0)
    remote_codes = {str(code) for code in shipping.get("remote_region_codes", [])}
    surcharge = int(shipping.get("remote_region_surcharge_cents", 0) or 0)
    max_cents = int(shipping.get("max_cents", 0) or 0)

    if mode == "free":
        return 0
    # 重量模板待运营配置（C17）；未配置时按件数无关的固定运费处理，
    # 因此 weight 与 flat 目前取值相同，等模板落库后再分流。
    amount = flat
    if threshold and goods_cents >= threshold and mode != "weight":
        return 0
    if region_code and region_code in remote_codes:
        amount += surcharge
    if max_cents:
        amount = min(amount, max_cents)
    return max(0, amount)


def coupon_discount_cents(
    policy: dict[str, Any], *, coupon_code: str | None, goods_cents: int
) -> tuple[int, str | None]:
    """计算优惠券折扣。未知券码按校验失败处理，不静默忽略。"""
    if not coupon_code or not policy.get("coupons_enabled", False):
        return 0, None
    code = coupon_code.strip().upper()
    coupon = policy["coupons"].get(code)
    if not isinstance(coupon, dict):
        raise AppError(
            "VALIDATION_ERROR", "优惠券不可用，请重新选择。", fields={"coupon_code": "无效券码"}
        )
    min_goods = int(coupon.get("min_goods_cents", 0) or 0)
    if goods_cents < min_goods:
        raise AppError(
            "VALIDATION_ERROR",
            f"该优惠券需满 {min_goods / 100:.2f} 元可用。",
            fields={"coupon_code": "未达门槛"},
        )
    kind = str(coupon.get("type", "percent"))
    if kind == "percent":
        discount = goods_cents * int(coupon.get("value", 0)) // 100
    elif kind == "amount":
        discount = int(coupon.get("value", 0))
    elif kind == "free_shipping":
        discount = 0
    else:
        raise AppError("VALIDATION_ERROR", "优惠券配置无效，请联系客服。")
    cap = int(coupon.get("max_discount_cents", 0) or 0)
    if cap:
        discount = min(discount, cap)
    return max(0, min(discount, goods_cents)), code


def free_shipping_coupon(policy: dict[str, Any], *, coupon_code: str | None) -> bool:
    if not coupon_code or not policy.get("coupons_enabled", False):
        return False
    coupon = policy["coupons"].get(coupon_code.strip().upper())
    return isinstance(coupon, dict) and str(coupon.get("type")) == "free_shipping"


def allocate_discount(subtotals: list[int], discount_cents: int) -> list[int]:
    """按行小计比例分摊优惠，最大余数法保证分摊之和恰好等于优惠总额。"""
    total = sum(subtotals)
    if discount_cents <= 0 or total <= 0:
        return [0 for _ in subtotals]
    if discount_cents >= total:
        return list(subtotals)
    shares = [divmod(subtotal * discount_cents, total) for subtotal in subtotals]
    floors = [whole for whole, _ in shares]
    remainder = discount_cents - sum(floors)
    order = sorted(
        range(len(subtotals)), key=lambda index: (shares[index][1], subtotals[index]), reverse=True
    )
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def _validate_amounts(goods_cents: int, discount_cents: int, shipping_cents: int) -> int:
    if goods_cents < 0 or discount_cents < 0 or shipping_cents < 0:
        raise AppError("VALIDATION_ERROR", "金额不能为负。")
    if discount_cents > goods_cents:
        raise AppError("STATE_CONFLICT", "优惠金额超过商品金额，请联系客服核对。")
    total = goods_cents - discount_cents + shipping_cents
    if total > MAX_TOTAL_CENTS:
        raise AppError("VALIDATION_ERROR", "单笔订单金额超出上限，请联系客服。")
    return total


# ------------------------------------------------------------------ 行计算
def _line_for_sku(session: Session, sku_id: str, quantity: int) -> dict[str, Any]:
    """按当前目录重算一行的权威价格与可售性。"""
    if quantity <= 0:
        raise AppError("VALIDATION_ERROR", "数量必须大于 0。", fields={"quantity": "必须大于 0"})
    sku = catalog_ports.get_sku(session, sku_id)
    if sku is None or not sku.is_active:
        raise AppError("STOCK_NOT_ENOUGH", "该规格当前不可售。", fields={"sku_id": "不可售"})
    product = catalog_ports.get_product(session, sku.product_id)
    if product is None or product.status != "published":
        raise AppError("STOCK_NOT_ENOUGH", "该商品当前不可售。", fields={"sku_id": "已下架"})
    if quantity > sku.max_per_order:
        raise AppError(
            "VALIDATION_ERROR",
            f"单个规格每单最多 {sku.max_per_order} 件。",
            fields={"quantity": f"最多 {sku.max_per_order} 件"},
        )
    available = max(0, sku.stock_on_hand - sku.reserved)
    if available < quantity:
        raise AppError(
            "STOCK_NOT_ENOUGH",
            "库存不足，请调整数量。",
            fields={"sku_id": f"仅剩 {available} 件"},
        )
    return {
        "sku_id": sku.id,
        "product_id": product.id,
        "product_kind": product.kind,
        "product_slug": product.slug,
        "product_title": product.title,
        "label": sku.label,
        "description_snapshot": f"{product.title} · {sku.label}",
        "sku_code": sku.sku_code,
        "attributes": sku.attributes or {},
        "unit_price_cents": int(sku.price_cents),
        "currency": sku.currency,
        "quantity": quantity,
        "weight_grams": int(sku.weight_grams),
        "requires_approval": bool(product.requires_approval),
        "shipping_template": product.shipping_template,
        "terms_version": product.terms_version,
        "available": available,
        "cover_asset_id": sku.cover_asset_id,
    }


def _aggregate_lines(lines: list[dict[str, Any]]) -> tuple[int, str]:
    if not lines:
        raise AppError("VALIDATION_ERROR", "请至少选择一件商品。", fields={"items": "不能为空"})
    currencies = {str(line["currency"]) for line in lines}
    if len(currencies) > 1:
        raise AppError("VALIDATION_ERROR", "一次结算只能包含同一币种的商品。")
    goods = sum(int(line["unit_price_cents"]) * int(line["quantity"]) for line in lines)
    return goods, currencies.pop()


def _apply_pricing(
    session: Session,
    *,
    lines: list[dict[str, Any]],
    region_code: str,
    coupon_code: str | None,
) -> dict[str, Any]:
    policy = commerce_policy(session)
    goods_cents, currency = _aggregate_lines(lines)
    if coupon_code:
        require_feature(session, "coupons")
    discount_cents, applied_code = coupon_discount_cents(
        policy, coupon_code=coupon_code, goods_cents=goods_cents
    )
    shipping_cents = compute_shipping_cents(policy, goods_cents=goods_cents, region_code=region_code)
    if free_shipping_coupon(policy, coupon_code=coupon_code):
        shipping_cents = 0
    total_cents = _validate_amounts(goods_cents, discount_cents, shipping_cents)
    return {
        "lines": lines,
        "goods_cents": goods_cents,
        "discount_cents": discount_cents,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents,
        "currency": currency,
        "coupon_code": applied_code,
        "policy": policy,
    }


# ------------------------------------------------------------------ 购物车
def cart_payload(session: Session, *, user_id: str) -> dict[str, Any]:
    """购物车只展示当前目录价格，不保存价格快照。"""
    cart = repo.get_cart(session, user_id=user_id)
    items: list[dict[str, Any]] = []
    if cart is not None:
        for row in repo.list_cart_items(session, cart_id=cart.id):
            sku = catalog_ports.get_sku(session, row.sku_id)
            product = catalog_ports.get_product(session, sku.product_id) if sku else None
            available = max(0, sku.stock_on_hand - sku.reserved) if sku else 0
            sellable = bool(
                sku and sku.is_active and product is not None and product.status == "published"
            )
            items.append(
                {
                    "sku_id": row.sku_id,
                    "quantity": row.quantity,
                    "sku_code": sku.sku_code if sku else None,
                    "label": sku.label if sku else None,
                    "product_id": product.id if product else None,
                    "product_slug": product.slug if product else None,
                    "product_title": product.title if product else None,
                    "product_kind": product.kind if product else None,
                    "unit_price_cents": int(sku.price_cents) if sku else 0,
                    "currency": sku.currency if sku else None,
                    "max_per_order": int(sku.max_per_order) if sku else 0,
                    "available": available,
                    "sellable": sellable,
                    "requires_approval": bool(product.requires_approval) if product else False,
                    "cover_asset_id": sku.cover_asset_id if sku else None,
                }
            )
    goods_cents = sum(int(item["unit_price_cents"]) * int(item["quantity"]) for item in items)
    return {
        "id": cart.id if cart else None,
        "items": items,
        "item_count": sum(int(item["quantity"]) for item in items),
        "goods_cents": goods_cents,
        "currency": items[0]["currency"] if items else None,
    }


def set_cart_item(session: Session, *, user_id: str, sku_id: str, quantity: int) -> dict[str, Any]:
    """PUT 语义：绝对数量。``quantity<=0`` 视为移除该项。"""
    sku = catalog_ports.get_sku(session, sku_id)
    cart = repo.get_or_create_cart(session, user_id=user_id)
    existing = repo.get_cart_item(session, cart_id=cart.id, sku_id=sku_id)
    if quantity <= 0:
        if existing is not None:
            session.delete(existing)
            session.flush()
        return cart_payload(session, user_id=user_id)
    if sku is None or not sku.is_active:
        raise AppError("STOCK_NOT_ENOUGH", "该规格当前不可售。", fields={"sku_id": "不可售"})
    if quantity > sku.max_per_order:
        raise AppError(
            "VALIDATION_ERROR",
            f"单个规格每单最多 {sku.max_per_order} 件。",
            fields={"quantity": f"最多 {sku.max_per_order} 件"},
        )
    if existing is None:
        session.add(CartItem(cart_id=cart.id, sku_id=sku_id, quantity=quantity))
    else:
        existing.quantity = quantity
        existing.updated_at = now_ms()
    session.flush()
    return cart_payload(session, user_id=user_id)


def remove_cart_item(session: Session, *, user_id: str, sku_id: str) -> dict[str, Any]:
    cart = repo.get_cart(session, user_id=user_id)
    if cart is not None:
        existing = repo.get_cart_item(session, cart_id=cart.id, sku_id=sku_id)
        if existing is not None:
            session.delete(existing)
            session.flush()
    return cart_payload(session, user_id=user_id)


# ------------------------------------------------------------------ 结算预览
def preview_checkout(
    session: Session,
    *,
    user_id: str,
    items: list[dict[str, Any]],
    address_id: str,
    coupon_code: str | None = None,
    terms_version: str | None = None,
) -> dict[str, Any]:
    """重算价格与运费，登记一次性结算令牌。客户端不得提交单价、金额或积分。"""
    address = _require_owned_address(session, user_id=user_id, address_id=address_id)
    merged: dict[str, int] = {}
    for item in items:
        sku_id = str(item.get("sku_id") or "")
        quantity = int(item.get("quantity") or 0)
        if not sku_id:
            raise AppError("VALIDATION_ERROR", "缺少 SKU。", fields={"items.sku_id": "必填"})
        merged[sku_id] = merged.get(sku_id, 0) + quantity

    lines = [_line_for_sku(session, sku_id, quantity) for sku_id, quantity in merged.items()]
    priced = _apply_pricing(
        session, lines=lines, region_code=address.region_code, coupon_code=coupon_code
    )
    for line in lines:
        if line["product_kind"] == "exclusive_character":
            _require_character_available(session, product_id=str(line["product_id"]))

    token = random_token(32)
    session_row = CheckoutSession(
        user_id=user_id,
        token_hash=hash_token(token),
        lines=lines,
        address_id=address_id,
        coupon_code=priced["coupon_code"],
        goods_cents=priced["goods_cents"],
        discount_cents=priced["discount_cents"],
        shipping_cents=priced["shipping_cents"],
        total_cents=priced["total_cents"],
        currency=priced["currency"],
        terms_version=_resolve_terms_version(lines, terms_version),
        expires_at=add_minutes_ms(_checkout_ttl_minutes()),
    )
    session.add(session_row)
    session.flush()
    return {
        "checkout_token": token,
        "expires_at": iso(session_row.expires_at),
        "lines": [_checkout_line(line) for line in lines],
        "goods_cents": priced["goods_cents"],
        "discount_cents": priced["discount_cents"],
        "shipping_cents": priced["shipping_cents"],
        "total_cents": priced["total_cents"],
        "currency": priced["currency"],
        "address_id": address_id,
    }


def _checkout_line(line: dict[str, Any]) -> dict[str, Any]:
    subtotal = int(line["unit_price_cents"]) * int(line["quantity"])
    return {
        "sku_id": line["sku_id"],
        "product_id": line["product_id"],
        "product_slug": line["product_slug"],
        "product_title": line["product_title"],
        "label": line["label"],
        "attributes": line["attributes"],
        "quantity": line["quantity"],
        "unit_price_cents": line["unit_price_cents"],
        "subtotal_cents": subtotal,
        "currency": line["currency"],
        "weight_grams": line["weight_grams"],
        "requires_approval": line["requires_approval"],
        "available": line["available"],
    }


def _resolve_terms_version(lines: list[dict[str, Any]], requested: str | None) -> str:
    versions = {str(line["terms_version"]) for line in lines}
    if requested:
        if versions and requested not in versions:
            raise AppError(
                "PRICE_CHANGED",
                "购买条款版本已更新，请阅读最新条款后再次确认。",
                fields={"terms_version": "与商品当前条款不一致"},
            )
        return requested
    return sorted(versions)[0] if versions else "shop-1"


def _checkout_ttl_minutes() -> int:
    from app.platform.config import get_settings

    return max(1, int(get_settings().installment_payment_ttl_minutes))


def _require_owned_address(session: Session, *, user_id: str, address_id: str) -> Any:
    address = identity_ports.get_address(session, address_id=address_id, user_id=user_id)
    if address is None:
        raise AppError(
            "VALIDATION_ERROR",
            "收货地址不可用，请重新选择。",
            fields={"address_id": "地址不存在或不属于本人"},
        )
    return address


def _require_character_available(session: Session, *, product_id: str) -> Any:
    character = catalog_ports.get_character_by_product(session, product_id)
    if character is None:
        raise AppError("STATE_CONFLICT", "该角色信息不完整，请联系客服。")
    if character.public_status != "available":
        raise AppError("STOCK_NOT_ENOUGH", "该角色已被占用或已领养。", fields={"sku_id": "不可售"})
    return character


# ------------------------------------------------------------------ 下单
def create_order(
    session: Session,
    *,
    user_id: str,
    checkout_token: str,
    address_id: str,
    terms_version: str,
    accepted: bool,
    idempotency_key: str,
) -> CreatedOrder:
    """现货下单：幂等登记 → 地址 → 令牌一次性消费 → 重算价格 → 预留 → 建单。

    以上步骤在同一事务内完成；任一步失败都不留下半成品订单。
    """
    if not accepted:
        raise AppError(
            "VALIDATION_ERROR", "请先阅读并同意购买条款。", fields={"accepted": "必须勾选"}
        )
    request_hash = digest(
        {
            "operation": ORDER_OPERATION,
            "checkout_token_hash": hash_token(checkout_token or ""),
            "address_id": address_id,
            "terms_version": terms_version,
        }
    )
    replay = _register_idempotency(
        session,
        actor_id=user_id,
        operation=ORDER_OPERATION,
        key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return _created_order_from_snapshot(session, user_id=user_id, snapshot=replay)

    _require_owned_address(session, user_id=user_id, address_id=address_id)
    checkout = repo.get_checkout_session_by_hash(session, token_hash=hash_token(checkout_token or ""))
    if checkout is None or checkout.user_id != user_id:
        raise AppError("CHECKOUT_TOKEN_INVALID")
    if checkout.consumed_at is not None:
        raise AppError("CHECKOUT_TOKEN_INVALID", "结算信息已使用，请重新结算。")
    if checkout.expires_at <= now_ms():
        raise AppError("CHECKOUT_TOKEN_INVALID", "结算信息已过期，请重新结算。")
    if checkout.address_id != address_id:
        raise AppError(
            "PRICE_CHANGED", "收货地址已变更，请重新结算确认运费。", fields={"address_id": "已变更"}
        )
    if checkout.terms_version != terms_version:
        raise AppError(
            "PRICE_CHANGED",
            "购买条款版本已更新，请阅读最新条款后再次确认。",
            fields={"terms_version": "版本不一致"},
        )

    lines = [dict(line) for line in (checkout.lines or [])]
    if not lines:
        raise AppError("CHECKOUT_TOKEN_INVALID", "结算信息不完整，请重新结算。")
    address = _require_owned_address(session, user_id=user_id, address_id=address_id)

    # 重新校验价格与库存：预览之后目录可能已变化。
    fresh_lines = [
        _line_for_sku(session, str(line["sku_id"]), int(line["quantity"])) for line in lines
    ]
    priced = _apply_pricing(
        session,
        lines=fresh_lines,
        region_code=address.region_code,
        coupon_code=checkout.coupon_code,
    )
    if (
        priced["goods_cents"] != checkout.goods_cents
        or priced["discount_cents"] != checkout.discount_cents
        or priced["shipping_cents"] != checkout.shipping_cents
    ):
        raise AppError(
            "PRICE_CHANGED",
            "价格或运费已变化，请确认最新金额后重新下单。",
            fields={"total_cents": str(priced["total_cents"])},
        )
    total_cents = _validate_amounts(
        priced["goods_cents"], priced["discount_cents"], priced["shipping_cents"]
    )

    order = Order(
        order_no=_next_order_no(session),
        user_id=user_id,
        kind=_order_kind(fresh_lines),
        status="PENDING_PAYMENT",
        currency=priced["currency"],
        goods_cents=priced["goods_cents"],
        discount_cents=priced["discount_cents"],
        shipping_cents=priced["shipping_cents"],
        total_cents=total_cents,
        checkout_session_id=checkout.id,
        idempotency_key=idempotency_key,
        expires_at=add_minutes_ms(ORDER_TTL_MINUTES),
        terms_version=terms_version,
        terms_accepted_at=now_ms(),
        terms_snapshot={"terms_version": terms_version, "accepted": True, "accepted_at": iso(now_ms())},
        cart_snapshot={"coupon_code": priced["coupon_code"], "lines": [_checkout_line(line) for line in fresh_lines]},
        policy_snapshot={
            "shipping": priced["policy"]["shipping"],
            "after_sale": priced["policy"]["after_sale"],
            "invoice": priced["policy"]["invoice"],
            "currency": priced["currency"],
        },
        snapshot={"region_code": address.region_code, "province": address.province, "city": address.city},
    )
    session.add(order)
    session.flush()

    snapshot = AddressSnapshot(
        order_id=order.id,
        source_address_id=address.id,
        recipient_ciphertext=address.recipient_ciphertext,
        phone_ciphertext=address.phone_ciphertext,
        address_ciphertext=address.address_ciphertext,
        region_code=address.region_code,
        province=address.province,
        city=address.city,
        postal_code=address.postal_code,
    )
    session.add(snapshot)
    session.flush()
    order.address_snapshot_id = snapshot.id

    subtotals = [int(line["unit_price_cents"]) * int(line["quantity"]) for line in fresh_lines]
    allocations = allocate_discount(subtotals, priced["discount_cents"])
    items: list[OrderItem] = []
    for index, line in enumerate(fresh_lines):
        item = OrderItem(
            order_id=order.id,
            sku_id=line["sku_id"],
            product_id=line["product_id"],
            description_snapshot=line["description_snapshot"],
            sku_snapshot={
                "sku_code": line["sku_code"],
                "label": line["label"],
                "attributes": line["attributes"],
                "product_slug": line["product_slug"],
                "product_title": line["product_title"],
                "product_kind": line["product_kind"],
                "shipping_template": line["shipping_template"],
                "unit_price_cents": line["unit_price_cents"],
                "currency": line["currency"],
                "terms_version": line["terms_version"],
            },
            quantity=int(line["quantity"]),
            unit_price_cents=int(line["unit_price_cents"]),
            discount_allocated_cents=int(allocations[index]),
            cash_goods_cents=int(subtotals[index]) - int(allocations[index]),
            weight_grams=int(line["weight_grams"]),
            requires_approval=1 if line["requires_approval"] else 0,
        )
        session.add(item)
        items.append(item)
    session.flush()

    _enforce_approval_requirements(session, user_id=user_id, lines=fresh_lines, items=items, order_id=order.id)

    for line, item in zip(fresh_lines, items, strict=True):
        catalog_ports.reserve(
            session,
            sku_id=str(line["sku_id"]),
            quantity=int(line["quantity"]),
            order_id=order.id,
            order_item_id=item.id,
            expires_at=order.expires_at,
            reason="order_created",
        )
        if line["product_kind"] == "exclusive_character":
            character = _require_character_available(session, product_id=str(line["product_id"]))
            catalog_ports.reserve_exclusive_character(
                session, character_id=character.id, order_id=order.id
            )

    installment = Installment(
        order_id=order.id,
        kind="full",
        sequence=1,
        label="全款",
        amount_cents=total_cents,
        currency=priced["currency"],
        status="DUE",
        due_at=order.expires_at,
    )
    session.add(installment)
    session.flush()

    _consume_checkout_session(session, checkout=checkout, order_id=order.id)
    _clear_cart_items(session, user_id=user_id, sku_ids=[str(line["sku_id"]) for line in fresh_lines])
    _complete_idempotency(
        session,
        actor_id=user_id,
        operation=ORDER_OPERATION,
        key=idempotency_key,
        resource_id=order.id,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="order.create",
        resource_type="order",
        resource_id=order.id,
        after={
            "order_no": order.order_no,
            "total_cents": total_cents,
            "currency": priced["currency"],
            "kind": order.kind,
        },
    )
    enqueue(
        session,
        event_type="order.created",
        aggregate_id=order.id,
        payload={"order_no": order.order_no, "user_id": user_id, "total_cents": total_cents},
    )
    return CreatedOrder(
        order_id=order.id,
        order_no=order.order_no,
        status=order.status,
        installment_id=installment.id,
        totals=OrderTotals(
            goods_cents=order.goods_cents,
            discount_cents=order.discount_cents,
            shipping_cents=order.shipping_cents,
            total_cents=order.total_cents,
            currency=order.currency,
        ),
        expires_at=order.expires_at,
    )


def create_commission_order(
    session: Session,
    *,
    user_id: str,
    quote_id: str,
    quote_version: int,
    total_cents: int,
    prepayment_cents: int,
    currency: str,
    terms_version: str,
    inquiry_id: str,
    slot_reservation_id: str | None,
    idempotency_key: str,
) -> CreatedOrder:
    """接受报价后建立委托订单：预付款 + 尾款两个应付项，一笔报价只能一个订单。"""
    if not accepted_terms(terms_version):
        raise AppError("VALIDATION_ERROR", "缺少购买条款版本。", fields={"terms_version": "必填"})
    if total_cents <= 0 or prepayment_cents < 0 or prepayment_cents > total_cents:
        raise AppError("VALIDATION_ERROR", "委托金额或首期金额不合法。")
    _validate_amounts(total_cents, 0, 0)

    request_hash = digest(
        {
            "operation": "commerce.create_commission_order",
            "quote_id": quote_id,
            "quote_version": quote_version,
            "total_cents": total_cents,
            "prepayment_cents": prepayment_cents,
            "currency": currency,
            "terms_version": terms_version,
        }
    )
    replay = _register_idempotency(
        session,
        actor_id=user_id,
        operation="commerce.create_commission_order",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return _created_order_from_snapshot(session, user_id=user_id, snapshot=replay)

    existing = repo.get_order_by_quote(session, quote_id=quote_id)
    if existing is not None:
        if existing.user_id != user_id:
            raise AppError("RESOURCE_NOT_FOUND")
        return _created_order_from_existing(session, order=existing)

    order = Order(
        order_no=_next_order_no(session),
        user_id=user_id,
        kind="commission",
        status="PENDING_PAYMENT",
        currency=currency,
        goods_cents=total_cents,
        discount_cents=0,
        shipping_cents=0,
        total_cents=total_cents,
        quote_id=quote_id,
        inquiry_id=inquiry_id,
        quote_version=quote_version,
        slot_reservation_id=slot_reservation_id,
        idempotency_key=idempotency_key,
        terms_version=terms_version,
        terms_accepted_at=now_ms(),
        terms_snapshot={"terms_version": terms_version, "accepted": True, "accepted_at": iso(now_ms())},
        snapshot={"quote_id": quote_id, "quote_version": quote_version, "inquiry_id": inquiry_id},
        policy_snapshot={"currency": currency, "kind": "commission"},
        expires_at=add_minutes_ms(ORDER_TTL_MINUTES),
    )
    session.add(order)
    session.flush()

    session.add(
        OrderItem(
            order_id=order.id,
            sku_id=None,
            description_snapshot="定制委托（按已确认报价版本）",
            sku_snapshot={"quote_id": quote_id, "quote_version": quote_version},
            quantity=1,
            unit_price_cents=total_cents,
            discount_allocated_cents=0,
            cash_goods_cents=total_cents,
        )
    )
    plans: list[tuple[str, str, int]] = []
    if prepayment_cents > 0:
        plans.append(("prepayment", "预付款", prepayment_cents))
    balance = total_cents - prepayment_cents
    if balance > 0:
        plans.append(("balance", "尾款", balance))
    if not plans:
        plans.append(("full", "全款", total_cents))
    first: Installment | None = None
    for sequence, (kind, label, amount) in enumerate(plans, start=1):
        row = Installment(
            order_id=order.id,
            kind=kind,
            sequence=sequence,
            label=label,
            amount_cents=amount,
            currency=currency,
            status="DUE",
            due_at=order.expires_at if kind == "prepayment" else None,
        )
        session.add(row)
        if first is None:
            first = row
    session.flush()
    _complete_idempotency(
        session,
        actor_id=user_id,
        operation="commerce.create_commission_order",
        key=idempotency_key,
        resource_id=order.id,
    )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="order.create_commission",
        resource_type="order",
        resource_id=order.id,
        after={"order_no": order.order_no, "quote_id": quote_id, "total_cents": total_cents},
    )
    enqueue(
        session,
        event_type="order.created",
        aggregate_id=order.id,
        payload={"order_no": order.order_no, "user_id": user_id, "kind": "commission"},
    )
    return _created_order_from_existing(session, order=order, installment_id=first.id if first else None)


def accepted_terms(terms_version: str | None) -> bool:
    return bool(terms_version and terms_version.strip())


def _order_kind(lines: list[dict[str, Any]]) -> str:
    """首发一个订单只有一种履约类型（08 文档）。"""
    kinds = {str(line["product_kind"]) for line in lines}
    if "exclusive_character" in kinds:
        return "exclusive_character"
    if "service" in kinds:
        return "service"
    return "stock_goods"


def _next_order_no(session: Session) -> str:
    from datetime import datetime

    from app.platform.ids import SHANGHAI

    day = datetime.fromtimestamp(now_ms() / 1000, tz=SHANGHAI).strftime("%Y%m%d")
    for _ in range(8):
        candidate = f"{ORDER_NO_PREFIX}{day}{random_token(5).replace('-', '').replace('_', '')[:6].upper()}"
        if repo.get_order_by_no(session, order_no=candidate) is None:
            return candidate
    raise AppError("STATE_CONFLICT", "订单号生成冲突，请重试。")


def _consume_checkout_session(session: Session, *, checkout: CheckoutSession, order_id: str) -> None:
    """一次性消费：条件更新，两个并发请求只有一个能建单。"""
    result = affected_rows(session.execute(
        sa.update(CheckoutSession)
        .where(CheckoutSession.id == checkout.id, CheckoutSession.consumed_at.is_(None))
        .values(consumed_at=now_ms(), consumed_order_id=order_id)
    ))
    if not result:
        raise AppError("CHECKOUT_TOKEN_INVALID", "结算信息已使用，请重新结算。")


def _clear_cart_items(session: Session, *, user_id: str, sku_ids: list[str]) -> None:
    cart = repo.get_cart(session, user_id=user_id)
    if cart is None:
        return
    for sku_id in sku_ids:
        row = repo.get_cart_item(session, cart_id=cart.id, sku_id=sku_id)
        if row is not None:
            session.delete(row)


def _enforce_approval_requirements(
    session: Session,
    *,
    user_id: str,
    lines: list[dict[str, Any]],
    items: list[OrderItem],
    order_id: str,
) -> None:
    """C28：需申请商品必须先通过人工审批，下单后审批记录转 CONVERTED。"""
    for line, item in zip(lines, items, strict=True):
        if not line["requires_approval"]:
            continue
        application = catalog_ports.approved_application_for(
            session, user_id=user_id, product_id=str(line["product_id"])
        )
        if application is None:
            raise AppError("STATE_CONFLICT", "该商品需要先提交申请并通过人工审批。")
        item.application_id = application.id
        catalog_ports.mark_application_converted(
            session, application_id=application.id, order_id=order_id
        )


# ------------------------------------------------------------------ 幂等
def _register_idempotency(
    session: Session,
    *,
    actor_id: str,
    operation: str,
    key: str,
    request_hash: str,
) -> dict[str, Any] | None:
    """幂等登记：同键同请求返回既有结果，同键不同请求 409。"""
    if not key or not key.strip():
        raise AppError("VALIDATION_ERROR", "缺少幂等键。", fields={"Idempotency-Key": "必填"})
    from app.platform.reliability import IdempotencyKey

    existing = session.scalars(
        sa.select(IdempotencyKey).where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    ).one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AppError(
                "IDEMPOTENCY_CONFLICT", "相同的请求编号已用于不同的请求内容。"
            )
        if existing.status == "COMPLETED" and existing.response_snapshot:
            return dict(existing.response_snapshot)
        raise AppError("STATE_CONFLICT", "该请求正在处理中，请稍后查询结果。")
    session.add(
        IdempotencyKey(
            actor_id=actor_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            status="IN_PROGRESS",
            expires_at=add_minutes_ms(IDEMPOTENCY_TTL_HOURS * 60),
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        # 并发同键：必须回滚到外层事务重试，不能静默返回不确定结果。
        session.rollback()
        raise AppError("STATE_CONFLICT", "该请求正在处理中，请稍后查询结果。") from exc
    return None


def _complete_idempotency(
    session: Session, *, actor_id: str, operation: str, key: str, resource_id: str
) -> None:
    from app.platform.reliability import IdempotencyKey

    row = session.scalars(
        sa.select(IdempotencyKey).where(
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    ).one_or_none()
    if row is None:
        return
    installment = repo.list_installments(session, order_id=resource_id)
    order = repo.get_order(session, order_id=resource_id)
    row.resource_id = resource_id
    row.status = "COMPLETED"
    row.response_snapshot = {
        "order_id": resource_id,
        "order_no": order.order_no if order else None,
        "status": order.status if order else None,
        "installment_id": installment[0].id if installment else None,
        "totals": {
            "goods_cents": order.goods_cents if order else 0,
            "discount_cents": order.discount_cents if order else 0,
            "shipping_cents": order.shipping_cents if order else 0,
            "total_cents": order.total_cents if order else 0,
            "currency": order.currency if order else "CNY",
        },
        "expires_at": order.expires_at if order else None,
    }
    session.flush()


def _created_order_from_snapshot(
    session: Session, *, user_id: str, snapshot: dict[str, Any]
) -> CreatedOrder:
    order_id = str(snapshot.get("order_id") or "")
    order = repo.get_order(session, order_id=order_id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return _created_order_from_existing(
        session, order=order, installment_id=snapshot.get("installment_id")
    )


def _created_order_from_existing(
    session: Session, *, order: Order, installment_id: str | None = None
) -> CreatedOrder:
    if installment_id is None:
        installments = repo.list_installments(session, order_id=order.id)
        installment_id = installments[0].id if installments else None
    return CreatedOrder(
        order_id=order.id,
        order_no=order.order_no,
        status=order.status,
        installment_id=installment_id,
        totals=OrderTotals(
            goods_cents=order.goods_cents,
            discount_cents=order.discount_cents,
            shipping_cents=order.shipping_cents,
            total_cents=order.total_cents,
            currency=order.currency,
        ),
        expires_at=order.expires_at,
    )


# ------------------------------------------------------------------ 支付模块桥接
def _load_payments() -> Any | None:
    """加载支付模块端口；未交付时返回 ``None``，由调用方降级处理。

    支付模块由另一位工程师交付。此处必须能在完全不存在的环境下导入失败而不炸掉
    商务流程，便于隔离测试；集成时若端口缺失会按 ``PROVIDER_UNAVAILABLE`` 暴露。
    """
    try:
        from app.modules.payments import ports as payments_ports
    except ImportError:
        return None
    if not hasattr(payments_ports, "mark_order_closing"):
        return None
    return payments_ports


def _payment_summary(session: Session, *, order_id: str) -> dict[str, Any]:
    """实收摘要；支付模块未就绪时按“无在途收款”处理，并标注来源。"""
    module = _load_payments()
    if module is None:
        return {
            "paid_cents": 0,
            "refunded_applied_cents": 0,
            "allocations": [],
            "provider_available": False,
        }
    summary = dict(module.order_paid_summary(session, order_id=order_id) or {})
    summary.setdefault("paid_cents", 0)
    summary.setdefault("refunded_applied_cents", 0)
    summary.setdefault("allocations", [])
    summary["provider_available"] = True
    return summary


def _in_flight_installments(session: Session, *, order_id: str) -> list[Installment]:
    """在途应付项：已经收款或正在/曾经发起渠道收款的项。"""
    return [
        row
        for row in repo.list_installments(session, order_id=order_id)
        if row.status in ("PAID", "PAYING", "CLOSING")
    ]


# ------------------------------------------------------------------ 取消与关单
def cancel_order(
    session: Session, *, user_id: str, order_id: str, reason: str = "客户取消"
) -> dict[str, Any]:
    """客户取消待付款订单。

    顺序（08 文档第 4 条）：进入关闭核对（``closing_at`` + 应付项 ``CLOSING``）→ 关闭或
    查询渠道 → 确认没有在途成功付款 → 条件更新取消订单 → 释放预留在同事务内完成。

    订单履约状态只在 ``PENDING_PAYMENT``/``ACTIVE`` 与 ``CANCELLED`` 之间条件更新，
    因此支付成功（把应付项推进为 ``PAID``）与释放预留竞争时只有一个能成功。
    """
    order = repo.get_order(session, order_id=order_id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if order.status not in ("PENDING_PAYMENT", "ACTIVE"):
        raise AppError("STATE_CONFLICT", "当前订单状态不支持取消，请联系客服。")

    moment = now_ms()
    # 第一步：记录“正在关闭核对”。条件更新同时防止并发取消重复执行。
    marked = affected_rows(session.execute(
        sa.update(Order)
        .where(
            Order.id == order.id,
            Order.status == order.status,
            Order.version == order.version,
            Order.closing_at.is_(None),
        )
        .values(
            closing_at=moment,
            closing_reason=reason[:120],
            version=Order.version + 1,
            updated_at=moment,
        )
    ))
    if not marked:
        raise AppError("STATE_CONFLICT", "订单状态已变化，请刷新后重试。")
    session.expire(order)

    module = _load_payments()
    if module is not None:
        module.mark_order_closing(session, order_id=order_id, reason=reason)

    summary = _payment_summary(session, order_id=order_id)
    paid_cents = int(summary.get("paid_cents") or 0)
    in_flight = _in_flight_installments(session, order_id=order_id)
    if paid_cents > 0 or in_flight:
        # 已有真实收款或在途收款：不能取消与释放，交给支付/对账流程处理。
        raise AppError(
            "PAYMENT_PENDING",
            "该订单已有收款记录，请等待支付结果确认后联系客服处理。",
        )

    # 第二步：关闭渠道在途尝试并把应付项收口为 CLOSED（此时已确认没有成功收款）。
    payable = repo.list_payable_installments(session, order_id=order_id)
    closed_installments: list[str] = []
    for installment in payable:
        if module is not None:
            module.close_payment_attempts_for_installment(
                session, installment_id=installment.id, reason=reason
            )
        # 关闭失败（渠道不可用，或渠道回答已支付但查单未确认）会留下活动尝试。
        # 此时必须 fail closed：继续取消会与可能到来的成功收款竞争，而库存已被释放。
        if module is not None and module.unresolved_attempt_count(
            session, order_id=order_id
        ) > 0:
            raise AppError(
                "PAYMENT_UNKNOWN",
                "渠道仍有未关闭的支付尝试，无法确认没有在途付款；"
                "请稍后重试或由客服先查单确认。",
            )
        updated = affected_rows(session.execute(
            sa.update(Installment)
            .where(
                Installment.id == installment.id,
                Installment.status.in_(("DUE", "PAYING", "CLOSING")),
            )
            .values(status="CLOSED", closed_at=now_ms(), reason=reason[:200], updated_at=now_ms())
        ))
        if updated:
            closed_installments.append(installment.id)
        elif repo.get_installment(session, installment_id=installment.id) is not None:
            # 状态已被并发推进（典型情况：收款成功已置 PAID）→ 放弃关单。
            raise AppError("PAYMENT_PENDING", "订单状态已变化，请刷新后查询最新状态。")

    # 第三步：条件更新取消订单，然后释放仍为 ACTIVE 的预留。
    cancelled = affected_rows(session.execute(
        sa.update(Order)
        .where(Order.id == order_id, Order.status.in_(("PENDING_PAYMENT", "ACTIVE")))
        .values(
            status="CANCELLED",
            cancelled_at=now_ms(),
            cancel_reason=reason[:200],
            closing_reason=reason[:120],
            version=Order.version + 1,
            updated_at=now_ms(),
        )
    ))
    if not cancelled:
        raise AppError("PAYMENT_PENDING", "订单状态已变化，请刷新后查询最新状态。")
    session.expire_all()

    released = _release_reservations_by_order(session, order_id=order_id)
    refreshed = repo.get_order(session, order_id=order_id)
    if refreshed is None:  # pragma: no cover - 刚更新过的订单不可能消失
        raise AppError("RESOURCE_NOT_FOUND")
    result = _finalize_cancellation(session, order=refreshed, reason=reason, released=released)
    result["closed_installments"] = closed_installments
    return result


def _finalize_cancellation(
    session: Session, *, order: Order, reason: str, released: list[str]
) -> dict[str, Any]:
    _release_character(session, order=order, reason=reason)
    audit.record(
        session,
        actor_type="customer",
        actor_id=order.user_id,
        action="order.cancel",
        resource_type="order",
        resource_id=order.id,
        after={"status": order.status, "released_reservations": released},
        reason=reason,
    )
    enqueue(
        session,
        event_type="order.closing_due",
        aggregate_id=order.id,
        payload={"order_no": order.order_no, "status": order.status, "reason": reason},
    )
    return {"order_id": order.id, "status": order.status, "released_reservations": released}


def expire_due_orders(session: Session, *, now: int | None = None, limit: int = 20) -> int:
    """扫描已到期但仍待付款的订单，逐个交给取消流程处理；返回处理数量。

    与 08 文档第 4 条一致：到期进入 CLOSING → 先关闭或查询渠道 → 确认没有在途成功
    付款后才取消并释放预留。单个订单失败（例如已有在途收款需要人工核对）不中断整批，
    只记录日志并跳过；重复扫描同一订单不会重复释放库存或重复写事件。
    """
    moment = now if now is not None else now_ms()
    rows = list(
        session.scalars(
            sa.select(Order)
            .where(
                Order.status == "PENDING_PAYMENT",
                Order.expires_at.is_not(None),
                Order.expires_at <= moment,
            )
            .order_by(Order.expires_at, Order.id)
            .limit(max(1, min(limit, 200)))
        )
    )
    processed = 0
    for order in rows:
        try:
            # 每个订单独立事务：一个订单失败不能污染其余订单的处理结果。
            cancel_order(session, user_id=order.user_id, order_id=order.id, reason="超时未付款自动关闭")
            session.commit()
            processed += 1
        except AppError as exc:
            session.rollback()
            logger.warning(
                "到期订单关闭失败：order_id=%s code=%s", order.id, exc.code
            )
        except Exception:  # noqa: BLE001 - 批量任务必须继续处理其余订单
            session.rollback()
            logger.exception("到期订单关闭异常：order_id=%s", order.id)
    return processed


def _release_reservations(session: Session, order: Order) -> list[str]:
    """释放订单全部 ACTIVE 预留；已 CONSUMED 的预留不会被释放。"""
    return _release_reservations_by_order(session, order_id=order.id)


def _release_reservations_by_order(session: Session, *, order_id: str) -> list[str]:
    """释放指定订单仍为 ACTIVE 的预留；已 CONSUMED 的预留不会被释放。"""
    released: list[str] = []
    for reservation in catalog_ports.active_reservations_for_order(session, order_id=order_id):
        catalog_ports.release(session, reservation_id=reservation.id, reason="order_cancelled")
        released.append(reservation.id)
    return released


def _release_character(session: Session, *, order: Order, reason: str) -> None:
    if order.kind != "exclusive_character":
        return
    for item in repo.list_items(session, order_id=order.id):
        if item.product_id is None:
            continue
        character = catalog_ports.get_character_by_product(session, item.product_id)
        if character is not None and character.public_status == "reserved":
            catalog_ports.release_character(session, character_id=character.id, reason=reason)


# ------------------------------------------------------------------ 收款成功后的核销
def on_installment_paid(session: Session, *, order_id: str, installment_id: str) -> dict[str, Any]:
    """支付模块事件消费者入口：收款成功后核销库存与应付项。

    幂等：重复调用不会重复核销、不会重复扣减库存；订单已取消时只记录迟到收款，
    不重新扣已经释放（或已售出）的库存。
    """
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    installment = repo.get_installment(session, installment_id=installment_id)
    if installment is None or installment.order_id != order_id:
        raise AppError("RESOURCE_NOT_FOUND")

    result: dict[str, Any] = {
        "order_id": order_id,
        "installment_id": installment_id,
        "consumed_reservations": [],
        "adopted_character_id": None,
        "order_status": order.status,
        "installment_state": installment.status,
        "late_payment": False,
        "already_paid": False,
    }

    paid_now = affected_rows(session.execute(
        sa.update(Installment)
        .where(
            Installment.id == installment_id,
            Installment.status.in_(("DUE", "PAYING", "CLOSING")),
        )
        .values(status="PAID", paid_at=now_ms(), version=Installment.version + 1)
    ))
    if not paid_now:
        result["already_paid"] = installment.status == "PAID"
        session.expire(installment)
        result["installment_state"] = installment.status
        result["order_status"] = order.status
        result["has_open_after_sales"] = repo.has_open_after_sales(session, order_id=order_id)
        # 迟到收款：订单已取消并释放预留后钱才到账。此时应付项已被 cancel_order
        # 置为 CLOSED，条件 UPDATE 不会命中，因此必须在 already_paid 之外单独识别，
        # 记为迟到收款待人工核对，不能静默当作「已处理」（08 文档）。
        if order.status == "CANCELLED" and installment.status == "CLOSED":
            result["late_payment"] = True
            audit.record(
                session,
                actor_type="system",
                action="order.late_payment_recorded",
                resource_type="order",
                resource_id=order.id,
                after={"installment_id": installment_id, "status": order.status},
                reason="订单已取消并释放预留后收到成功收款，需人工核对或原路退款",
            )
        return result
    session.expire(installment)

    if order.status in ("CANCELLED",):
        # 付款晚于已确认关闭与释放：记迟到收款，不重新扣库存（08 文档）。
        result["late_payment"] = True
        audit.record(
            session,
            actor_type="system",
            action="order.late_payment_recorded",
            resource_type="order",
            resource_id=order.id,
            after={"installment_id": installment_id, "status": order.status},
            reason="订单已取消后收到成功收款，需人工核对或退款",
        )
        result["order_status"] = order.status
        result["has_open_after_sales"] = repo.has_open_after_sales(session, order_id=order_id)
        return result

    consumed: list[str] = []
    for reservation in catalog_ports.active_reservations_for_order(session, order_id=order_id):
        catalog_ports.consume(session, reservation_id=reservation.id, reason="payment_confirmed")
        consumed.append(reservation.id)
    result["consumed_reservations"] = consumed
    result["adopted_character_id"] = _adopt_character(session, order=order)

    order.status = _next_paid_status(session, order=order)
    order.paid_at = order.paid_at or now_ms()
    order.updated_at = now_ms()
    order.version += 1
    session.flush()
    result["order_status"] = order.status
    result["installment_state"] = "PAID"
    result["has_open_after_sales"] = repo.has_open_after_sales(session, order_id=order_id)
    audit.record(
        session,
        actor_type="system",
        action="order.installment_paid",
        resource_type="order",
        resource_id=order.id,
        after={
            "installment_id": installment_id,
            "status": order.status,
            "consumed_reservations": consumed,
        },
    )
    enqueue(
        session,
        event_type="order.paid",
        aggregate_id=order.id,
        payload={"order_no": order.order_no, "installment_id": installment_id, "status": order.status},
    )
    enqueue(
        session,
        event_type="points.award_due",
        aggregate_id=order.id,
        payload={"order_id": order.id, "user_id": order.user_id, "reason": "installment_paid"},
    )
    return result


def _next_paid_status(session: Session, *, order: Order) -> str:
    """全部应付项 PAID 后订单转 ACTIVE/FULFILLING。"""
    installments = repo.list_installments(session, order_id=order.id)
    if installments and all(row.status == "PAID" for row in installments):
        return "FULFILLING" if order.kind == "commission" else "ACTIVE"
    if order.status == "PENDING_PAYMENT":
        return "ACTIVE"
    return order.status


def _adopt_character(session: Session, *, order: Order) -> str | None:
    if order.kind != "exclusive_character":
        return None
    for item in repo.list_items(session, order_id=order.id):
        if item.product_id is None:
            continue
        character = catalog_ports.get_character_by_product(session, item.product_id)
        if character is None:
            continue
        catalog_ports.mark_character_adopted(session, character_id=character.id, order_id=order.id)
        return str(character.id)
    return None


# ------------------------------------------------------------------ 退款成功
def on_refund_succeeded(
    session: Session, *, order_id: str, refund_id: str, goods_cents: int, shipping_cents: int
) -> dict[str, Any]:
    """退款成功：只更新汇总与 outbox，不自动补库存（08 文档第 5 条）。"""
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if goods_cents < 0 or shipping_cents < 0:
        raise AppError("VALIDATION_ERROR", "退款金额不能为负。")

    from app.platform.reliability import ConsumedEvent

    consumer_name = "commerce.refund_succeeded"
    event_id = f"{refund_id}:{goods_cents}:{shipping_cents}"
    already = session.get(ConsumedEvent, (consumer_name, event_id))
    if already is not None:
        return {
            "order_id": order.id,
            "refund_id": refund_id,
            "duplicate": True,
            "refunded_cents": order.refunded_cents,
            "refunded_goods_cents": order.refunded_goods_cents,
            "refunded_shipping_cents": order.refunded_shipping_cents,
            "restocked_quantity": 0,
        }

    updated = affected_rows(session.execute(
        sa.update(Order)
        .where(Order.id == order.id)
        .values(
            refunded_cents=Order.refunded_cents + goods_cents + shipping_cents,
            refunded_goods_cents=Order.refunded_goods_cents + goods_cents,
            refunded_shipping_cents=Order.refunded_shipping_cents + shipping_cents,
            version=Order.version + 1,
            updated_at=now_ms(),
        )
    ))
    if not updated:
        raise AppError("RESOURCE_NOT_FOUND")
    session.add(ConsumedEvent(consumer_name=consumer_name, event_id=event_id))
    session.expire(order)
    order = repo.get_order(session, order_id=order_id)
    assert order is not None

    # 售后记录同步退款结果；退款本身不等于回库。
    for after_sale in repo.list_after_sales_for_order(session, order_id=order.id):
        if after_sale.status in ("OPEN", "NEEDS_INFO", "APPROVED", "RETURNING"):
            after_sale.refund_id = after_sale.refund_id or refund_id
            after_sale.refunded_cents = after_sale.refunded_cents + goods_cents + shipping_cents
            after_sale.status = "RESOLVED"
            after_sale.resolved_at = now_ms()
            after_sale.version += 1
            after_sale.updated_at = now_ms()

    audit.record(
        session,
        actor_type="system",
        action="order.refund_succeeded",
        resource_type="order",
        resource_id=order.id,
        after={
            "refund_id": refund_id,
            "goods_cents": goods_cents,
            "shipping_cents": shipping_cents,
            "refunded_cents": order.refunded_cents,
            "restocked": False,
        },
        reason="退款成功；库存是否回补由检验结论与运营审核决定",
    )
    enqueue(
        session,
        event_type="refund.succeeded",
        aggregate_id=order.id,
        payload={"refund_id": refund_id, "goods_cents": goods_cents, "shipping_cents": shipping_cents},
    )
    # 积分按“最新净额”重算，不信任旧事件携带的金额。
    enqueue(
        session,
        event_type="points.award_due",
        aggregate_id=order.id,
        payload={"order_id": order.id, "user_id": order.user_id, "refund_id": refund_id},
    )
    return {
        "order_id": order.id,
        "refund_id": refund_id,
        "duplicate": False,
        "refunded_cents": order.refunded_cents,
        "refunded_goods_cents": order.refunded_goods_cents,
        "refunded_shipping_cents": order.refunded_shipping_cents,
        "restocked_quantity": 0,
    }


# ------------------------------------------------------------------ 加项应付
def create_change_installment(
    session: Session,
    *,
    order_id: str,
    amount_cents: int,
    label: str,
    change_request_id: str,
) -> str:
    """为已确认的变更请求建立加项应付项；同一变更只建一次。"""
    if amount_cents <= 0:
        raise AppError("VALIDATION_ERROR", "加项金额必须大于 0。")
    _validate_amounts(amount_cents, 0, 0)
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if order.status in ("CANCELLED", "COMPLETED"):
        raise AppError("STATE_CONFLICT", "订单已结束，不能再增加应付项。")
    existing = repo.get_installment_by_change_request(session, change_request_id=change_request_id)
    if existing is not None:
        if existing.order_id != order_id:
            raise AppError("STATE_CONFLICT", "该变更请求已关联其他订单。")
        return existing.id
    if not label.strip():
        raise AppError("VALIDATION_ERROR", "请填写加项说明。", fields={"label": "必填"})
    installments = repo.list_installments(session, order_id=order_id)
    row = Installment(
        order_id=order_id,
        kind="change",
        sequence=len(installments) + 1,
        label=label.strip()[:80],
        amount_cents=amount_cents,
        currency=order.currency,
        status="DUE",
        change_request_id=change_request_id,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="system",
        action="order.change_installment_created",
        resource_type="order",
        resource_id=order_id,
        after={"installment_id": row.id, "amount_cents": amount_cents, "label": row.label},
        reason="客户已接受的变更请求",
    )
    return row.id


def assert_installment_kind(kind: str) -> str:
    if kind not in INSTALLMENT_KINDS:
        raise AppError("VALIDATION_ERROR", fields={"kind": "非法应付项类型"})
    return kind


# ------------------------------------------------------------------ 积分权威快照
def order_reward_snapshot(session: Session, order_id: str) -> dict[str, Any]:
    """积分模块使用的权威快照（不信任事件携带的金额）。

    ``eligible_net_cents = max(0, 商品实际现金分摊 - 商品已成功退款分摊)``，运费不计奖。
    """
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    summary = _payment_summary(session, order_id=order_id)
    paid_total = max(0, int(summary.get("paid_cents") or 0))
    refunded_total = max(0, int(summary.get("refunded_applied_cents") or 0))

    goods_total = max(0, order.goods_cents)
    shipping_total = max(0, order.shipping_cents)
    paid_goods_cents = min(paid_total, goods_total)
    paid_shipping_cents = min(max(0, paid_total - paid_goods_cents), shipping_total)
    paid_goods_cents = min(paid_goods_cents, max(0, paid_total - paid_shipping_cents))

    refunded_goods_cents = min(max(0, order.refunded_goods_cents), paid_goods_cents)
    refunded_shipping_cents = min(
        max(0, order.refunded_shipping_cents), max(0, refunded_total - refunded_goods_cents)
    )
    return {
        "order_id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "currency": order.currency,
        "goods_cents": goods_total,
        "shipping_cents": shipping_total,
        "discount_cents": max(0, order.discount_cents),
        "total_cents": max(0, order.total_cents),
        "paid_goods_cents": paid_goods_cents,
        "paid_shipping_cents": paid_shipping_cents,
        "refunded_goods_cents": refunded_goods_cents,
        "refunded_shipping_cents": refunded_shipping_cents,
        "eligible_net_cents": max(0, paid_goods_cents - refunded_goods_cents),
        "completed_at": iso(order.completed_at),
        "has_open_after_sales": has_open_after_sales(session, order_id=order.id),
    }


def has_open_after_sales(session: Session, *, order_id: str) -> bool:
    return repo.has_open_after_sales(session, order_id=order_id)


# ------------------------------------------------------------------ 收货与物流
def confirm_receipt(session: Session, *, user_id: str, order_id: str) -> dict[str, Any]:
    order = repo.get_order(session, order_id=order_id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    result = session.execute(
        sa.update(Order)
        .where(Order.id == order.id, Order.status == "SHIPPED")
        .values(status="COMPLETED", completed_at=now_ms(), version=Order.version + 1, updated_at=now_ms())
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "只有已发货的订单可以确认收货。", fields={"status": order.status})
    session.expire(order)
    order = repo.get_order(session, order_id=order_id)
    assert order is not None
    for shipment in repo.list_shipments(session, order_id=order.id, kind="outbound"):
        if shipment.delivered_at is None:
            shipment.delivered_at = now_ms()
            shipment.updated_at = now_ms()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="order.confirm_receipt",
        resource_type="order",
        resource_id=order.id,
        after={"status": order.status},
    )
    enqueue(
        session,
        event_type="points.award_due",
        aggregate_id=order.id,
        payload={"order_id": order.id, "user_id": user_id, "reason": "order_completed"},
    )
    return {"order_id": order.id, "status": order.status, "completed_at": iso(order.completed_at)}


def create_shipment(
    session: Session,
    *,
    order_id: str,
    carrier: str,
    tracking_no: str,
    actor_id: str | None,
    kind: str = "outbound",
    verified_source: str = "staff",
    note: str | None = None,
) -> Shipment:
    """运营发货。重复发货被拒（同一承运商 + 同一物流号只允许一条）。"""
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    carrier = (carrier or "").strip()
    tracking_no = (tracking_no or "").strip()
    if not carrier or not tracking_no:
        raise AppError("VALIDATION_ERROR", "请填写承运商与物流单号。")
    if repo.get_shipment_by_tracking(
        session, order_id=order_id, carrier=carrier, tracking_no=tracking_no
    ) is not None:
        raise AppError("STATE_CONFLICT", "该物流单号已登记，请勿重复发货。")
    if kind == "outbound":
        if order.status not in ("ACTIVE", "FULFILLING"):
            raise AppError("STATE_CONFLICT", "当前订单状态不允许发货。", fields={"status": order.status})
        existing = repo.list_shipments(session, order_id=order_id, kind="outbound")
        if existing:
            raise AppError("STATE_CONFLICT", "该订单已发货，如需补充物流请更新原记录。")

    shipment = Shipment(
        order_id=order_id,
        kind=kind,
        carrier=carrier[:60],
        tracking_no=tracking_no[:80],
        shipped_at=now_ms() if kind == "outbound" else None,
        verified_source=verified_source,
        note=(note or "")[:200] or None,
        created_by=actor_id,
    )
    session.add(shipment)
    session.flush()

    if kind == "outbound":
        session.execute(
            sa.update(Order)
            .where(Order.id == order_id, Order.status.in_(("ACTIVE", "FULFILLING")))
            .values(
                status="SHIPPED",
                shipped_at=shipment.shipped_at,
                fulfilled_at=now_ms(),
                version=Order.version + 1,
                updated_at=now_ms(),
            )
        )
        enqueue(
            session,
            event_type="order.shipped",
            aggregate_id=order_id,
            payload={"order_id": order_id, "carrier": carrier, "tracking_no": tracking_no},
        )
    audit.record(
        session,
        actor_type="staff" if verified_source == "staff" else "customer",
        actor_id=actor_id,
        action="order.shipment_created",
        resource_type="shipment",
        resource_id=shipment.id,
        after={"order_id": order_id, "carrier": carrier, "kind": kind},
        reason="物流单号仅作履约证据，不作为身份凭证",
    )
    return shipment


# ------------------------------------------------------------------ 地址快照与履约变更
def change_delivery_address(
    session: Session,
    *,
    order_id: str,
    actor_type: str,
    actor_id: str | None,
    recipient: str,
    phone: str,
    detail: str,
    province: str,
    city: str,
    postal_code: str | None,
    reason: str,
) -> dict[str, Any]:
    """运营/客服改履约地址：只追加变更记录，不覆盖原始快照（08 文档）。"""
    order = repo.get_order(session, order_id=order_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if order.status in ("CANCELLED", "COMPLETED"):
        raise AppError("STATE_CONFLICT", "订单已结束，不能修改收货地址。")
    snapshot = repo.get_address_snapshot(session, snapshot_id=order.address_snapshot_id or "")
    if snapshot is None:
        snapshot = repo.latest_address_snapshot(session, order_id=order_id)
    if snapshot is None:
        raise AppError("STATE_CONFLICT", "订单缺少地址快照，请联系技术负责人核对。")
    if not reason.strip():
        raise AppError("VALIDATION_ERROR", "请填写变更原因。", fields={"reason": "必填"})

    previous_digest = digest(
        {
            "recipient": decrypt_pii(snapshot.recipient_ciphertext),
            "phone": decrypt_pii(snapshot.phone_ciphertext),
            "address": decrypt_pii(snapshot.address_ciphertext),
            "province": snapshot.province,
            "city": snapshot.city,
            "postal_code": snapshot.postal_code,
        }
    )
    change = OrderAddressChange(
        order_id=order_id,
        snapshot_id=snapshot.id,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason.strip()[:200],
        previous_digest=previous_digest,
        new_recipient_ciphertext=encrypt_pii(recipient),
        new_phone_ciphertext=encrypt_pii(phone),
        new_address_ciphertext=encrypt_pii(detail),
        province=province,
        city=city,
        postal_code=postal_code,
    )
    session.add(change)
    session.flush()
    audit.record(
        session,
        actor_type=actor_type if actor_type in ("staff", "customer", "system") else "system",
        actor_id=actor_id,
        action="order.delivery_address_changed",
        resource_type="order",
        resource_id=order_id,
        before={"address_digest": previous_digest},
        after={"change_id": change.id, "province": province, "city": city},
        reason=reason,
    )
    return {
        "order_id": order_id,
        "change_id": change.id,
        "snapshot_id": snapshot.id,
        "snapshot_preserved": True,
        "changed_at": iso(change.created_at),
    }


def effective_delivery_address(session: Session, *, order_id: str) -> dict[str, Any] | None:
    """当前生效的履约地址：原始快照 + 最后一次变更。"""
    snapshot = repo.latest_address_snapshot(session, order_id=order_id)
    if snapshot is None:
        return None
    changes = repo.list_address_changes(session, order_id=order_id)
    payload: dict[str, Any] = {
        "snapshot_id": snapshot.id,
        "recipient": decrypt_pii(snapshot.recipient_ciphertext),
        "phone_masked": _mask(decrypt_pii(snapshot.phone_ciphertext)),
        "detail": decrypt_pii(snapshot.address_ciphertext),
        "province": snapshot.province,
        "city": snapshot.city,
        "postal_code": snapshot.postal_code,
        "region_code": snapshot.region_code,
        "snapshot_created_at": iso(snapshot.created_at),
        "changed": bool(changes),
        "last_change_reason": changes[-1].reason if changes else None,
    }
    if changes:
        latest = changes[-1]
        payload.update(
            {
                "recipient": decrypt_pii(latest.new_recipient_ciphertext),
                "phone_masked": _mask(decrypt_pii(latest.new_phone_ciphertext)),
                "detail": decrypt_pii(latest.new_address_ciphertext),
                "province": latest.province,
                "city": latest.city,
                "postal_code": latest.postal_code,
                "change_count": len(changes),
                "changed_at": iso(latest.created_at),
            }
        )
    return payload


def _mask(phone: str) -> str:
    from app.platform.security import mask_phone

    return mask_phone(phone)


# ------------------------------------------------------------------ 售后
def create_after_sale(
    session: Session,
    *,
    user_id: str,
    order_id: str,
    type_: str,
    reason: str,
    order_item_id: str | None = None,
    requested_amount_cents: int = 0,
) -> AfterSale:
    order = repo.get_order(session, order_id=order_id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if order.status in ("PENDING_PAYMENT", "CANCELLED"):
        raise AppError("STATE_CONFLICT", "订单尚未付款或已取消，不能申请售后。")
    policy = commerce_policy(session)["after_sale"]
    allowed = tuple(policy.get("types", ("refund_only",)))
    if type_ not in allowed:
        raise AppError("VALIDATION_ERROR", "暂不支持该售后类型。", fields={"type": "不支持"})
    if not reason.strip():
        raise AppError("VALIDATION_ERROR", "请说明售后原因。", fields={"reason": "必填"})
    if repo.open_after_sale_for_order(session, order_id=order_id) is not None:
        raise AppError("STATE_CONFLICT", "该订单已有处理中的售后，请等待处理结论。")
    window_days = int(policy.get("window_days", 15) or 0)
    if window_days:
        start = order.completed_at or order.shipped_at or order.paid_at or order.created_at
        if start and now_ms() - start > window_days * 86_400_000:
            raise AppError(
                "STATE_CONFLICT", f"已超过 {window_days} 天售后申请期限，请联系客服说明情况。"
            )
    if order_item_id is not None and repo.get_order_item(
        session, order_item_id=order_item_id, order_id=order_id
    ) is None:
        raise AppError("RESOURCE_NOT_FOUND")

    refundable = _refundable_cents(session, order=order)
    amount = int(requested_amount_cents or 0)
    if amount < 0:
        raise AppError("VALIDATION_ERROR", "退款金额不能为负。")
    if amount > refundable:
        raise AppError(
            "REFUND_LIMIT_EXCEEDED",
            "申请金额超过当前可退金额，请核对后重试。",
            fields={"requested_amount_cents": f"最多 {refundable}"},
        )
    row = AfterSale(
        order_id=order_id,
        user_id=user_id,
        type=type_,
        status="OPEN",
        reason=reason.strip()[:1000],
        order_item_id=order_item_id,
        requested_amount_cents=amount,
        currency=order.currency,
    )
    session.add(row)
    session.flush()
    session.add(
        AfterSaleMessage(
            after_sale_id=row.id,
            author_type="customer",
            author_id=user_id,
            body=reason.strip()[:2000],
            visibility="customer",
        )
    )
    row.message_count += 1
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="after_sale.create",
        resource_type="after_sale",
        resource_id=row.id,
        after={"order_id": order_id, "type": type_, "requested_amount_cents": amount},
    )
    return row


def _refundable_cents(session: Session, *, order: Order) -> int:
    summary = _payment_summary(session, order_id=order.id)
    paid = max(0, int(summary.get("paid_cents") or 0)) or min(order.total_cents, order.paid_cents)
    refunded = max(0, int(summary.get("refunded_applied_cents") or 0))
    return max(0, paid - refunded)


def add_after_sale_message(
    session: Session, *, user_id: str, after_sale_id: str, body: str
) -> AfterSale:
    row = repo.get_after_sale(session, after_sale_id=after_sale_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status in ("RESOLVED", "REJECTED"):
        raise AppError("STATE_CONFLICT", "该售后已结束，如有疑问请联系客服。")
    if not body.strip():
        raise AppError("VALIDATION_ERROR", "请填写内容。", fields={"body": "必填"})
    session.add(
        AfterSaleMessage(
            after_sale_id=row.id,
            author_type="customer",
            author_id=user_id,
            body=body.strip()[:2000],
            visibility="customer",
        )
    )
    row.message_count += 1
    if row.status == "NEEDS_INFO":
        row.status = "OPEN"
    row.version += 1
    row.updated_at = now_ms()
    session.flush()
    return row


def submit_return_shipment(
    session: Session,
    *,
    user_id: str,
    after_sale_id: str,
    carrier: str,
    tracking_no: str,
) -> dict[str, Any]:
    """客户填写退货物流：只记录退货在途，不触发回库。"""
    row = repo.get_after_sale(session, after_sale_id=after_sale_id, user_id=user_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.status not in ("APPROVED", "RETURNING"):
        raise AppError("STATE_CONFLICT", "售后尚未批准退货，请等待处理结论。")
    carrier = (carrier or "").strip()
    tracking_no = (tracking_no or "").strip()
    if not carrier or not tracking_no:
        raise AppError("VALIDATION_ERROR", "请填写承运商与退货物流单号。")
    if row.return_tracking_no and row.return_tracking_no == tracking_no:
        return {
            "after_sale_id": row.id,
            "status": row.status,
            "shipment_id": row.return_shipment_id,
            "duplicate": True,
        }
    if row.return_shipment_id:
        raise AppError("STATE_CONFLICT", "退货物流已登记，如需修改请联系客服。")

    shipment = Shipment(
        order_id=row.order_id,
        kind="return",
        carrier=carrier[:60],
        tracking_no=tracking_no[:80],
        verified_source="customer",
        created_by=user_id,
    )
    session.add(shipment)
    session.flush()
    row.return_carrier = carrier[:60]
    row.return_tracking_no = tracking_no[:80]
    row.return_shipment_id = shipment.id
    row.status = "RETURNING"
    row.version += 1
    row.updated_at = now_ms()
    session.add(
        AfterSaleMessage(
            after_sale_id=row.id,
            author_type="customer",
            author_id=user_id,
            body=f"已寄回：{carrier} {tracking_no}",
            visibility="customer",
        )
    )
    row.message_count += 1
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="after_sale.return_shipment",
        resource_type="after_sale",
        resource_id=row.id,
        after={"carrier": carrier, "shipment_id": shipment.id},
        reason="退货在途；回库需检验合格",
    )
    return {"after_sale_id": row.id, "status": row.status, "shipment_id": shipment.id, "duplicate": False}


def restock_after_return(
    session: Session, *, after_sale_id: str, actor_id: str | None, reason: str
) -> dict[str, Any]:
    """回仓检验合格后补库存。退款本身从不触发本函数。"""
    row = repo.get_after_sale(session, after_sale_id=after_sale_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.restocked:
        raise AppError("STATE_CONFLICT", "该售后库存已回补，请勿重复操作。")
    if row.status not in ("RETURNING", "APPROVED", "RESOLVED"):
        raise AppError("STATE_CONFLICT", "售后状态不允许回补库存。")
    if not reason.strip():
        raise AppError("VALIDATION_ERROR", "请填写检验或授权依据。", fields={"reason": "必填"})
    restored = 0
    order = repo.get_order(session, order_id=row.order_id)
    for item in repo.list_items(session, order_id=row.order_id):
        if row.order_item_id and item.id != row.order_item_id:
            continue
        if item.sku_id is None:
            continue
        catalog_ports.restock(
            session,
            sku_id=item.sku_id,
            quantity=item.quantity,
            reason=reason,
            source_id=row.id,
        )
        restored += item.quantity
    row.restocked = 1
    row.decision_note = reason[:400]
    row.version += 1
    row.updated_at = now_ms()
    if order is not None and order.kind == "exclusive_character":
        for item in repo.list_items(session, order_id=order.id):
            if item.product_id is None:
                continue
            character = catalog_ports.get_character_by_product(session, item.product_id)
            if character is not None and character.public_status == "adopted":
                catalog_ports.release_character(
                    session, character_id=character.id, reason=f"经审核可复售：{reason}"
                )
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="after_sale.restock",
        resource_type="after_sale",
        resource_id=row.id,
        after={"restored_quantity": restored, "order_id": row.order_id},
        reason=reason,
    )
    return {"after_sale_id": row.id, "restored_quantity": restored, "restocked": True}


def decide_after_sale(
    session: Session,
    *,
    after_sale_id: str,
    decision: str,
    actor_id: str | None,
    note: str,
) -> AfterSale:
    """客服/运营处理售后（批准、补件或拒绝）。售后状态不等于退款渠道结果。"""
    row = repo.get_after_sale(session, after_sale_id=after_sale_id)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if decision not in ("APPROVED", "NEEDS_INFO", "REJECTED", "RESOLVED"):
        raise AppError("VALIDATION_ERROR", fields={"decision": "非法决定"})
    if row.status in ("RESOLVED", "REJECTED") and decision != "RESOLVED":
        raise AppError("STATE_CONFLICT", "该售后已结束。")
    if not note.strip():
        raise AppError("VALIDATION_ERROR", "请填写处理说明。", fields={"note": "必填"})
    row.status = decision
    row.decision_note = note.strip()[:400]
    row.decided_at = now_ms()
    if decision == "RESOLVED":
        row.resolved_at = now_ms()
    row.version += 1
    row.updated_at = now_ms()
    session.add(
        AfterSaleMessage(
            after_sale_id=row.id,
            author_type="staff",
            author_id=actor_id,
            body=note.strip()[:2000],
            visibility="customer",
        )
    )
    row.message_count += 1
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="after_sale.decide",
        resource_type="after_sale",
        resource_id=row.id,
        after={"status": decision},
        reason=note,
    )
    return row


# ------------------------------------------------------------------ 发票
def request_invoice(
    session: Session,
    *,
    user_id: str,
    order_id: str,
    type_: str,
    title: str,
    tax_no: str | None = None,
    email: str | None = None,
    note: str | None = None,
) -> Invoice:
    """开票申请：抬头与税号加密保存，金额与实际交易匹配。"""
    order = repo.get_order(session, order_id=order_id, user_id=user_id)
    if order is None:
        raise AppError("RESOURCE_NOT_FOUND")
    policy = commerce_policy(session)["invoice"]
    if type_ not in tuple(policy.get("types", ("personal", "business"))):
        raise AppError("VALIDATION_ERROR", "暂不支持该发票类型。", fields={"type": "不支持"})
    if order.status in ("PENDING_PAYMENT", "CANCELLED"):
        raise AppError("STATE_CONFLICT", "订单付款后才能申请开票。")
    if not title.strip():
        raise AppError("VALIDATION_ERROR", "请填写发票抬头。", fields={"title": "必填"})
    if type_ == "business" and not (tax_no or "").strip():
        raise AppError("VALIDATION_ERROR", "企业抬头必须填写税号。", fields={"tax_no": "必填"})
    if repo.list_invoices(session, order_id=order_id):
        raise AppError("STATE_CONFLICT", "该订单已提交开票申请，请勿重复提交。")

    amount = min(order.total_cents, max(0, _payment_summary(session, order_id=order_id).get("paid_cents") or order.paid_cents))
    payload: dict[str, str] = {"title": title.strip()[:120], "type": type_}
    if tax_no:
        payload["tax_no"] = tax_no.strip()[:40]
    if email:
        payload["email"] = email.strip()[:120]
    row = Invoice(
        order_id=order_id,
        user_id=user_id,
        type=type_,
        status="PENDING",
        amount_cents=amount,
        currency=order.currency,
        encrypted_title_data=encrypt_pii(_json_dumps(payload)),
        note=(note or "")[:200] or None,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="invoice.request",
        resource_type="invoice",
        resource_id=row.id,
        after={"order_id": order_id, "type": type_, "amount_cents": amount},
    )
    return row


def invoice_title_data(invoice: Invoice) -> dict[str, Any]:
    """读取解密后的开票数据（只对本人与有权限的运营可见）。"""
    import json

    try:
        parsed = json.loads(decrypt_pii(invoice.encrypted_title_data))
    except AppError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# ------------------------------------------------------------------ 订单读模型
def order_summary_payload(session: Session, order: Order) -> dict[str, Any]:
    items = repo.list_items(session, order_id=order.id)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "kind": order.kind,
        "status": order.status,
        "currency": order.currency,
        "goods_cents": order.goods_cents,
        "discount_cents": order.discount_cents,
        "shipping_cents": order.shipping_cents,
        "total_cents": order.total_cents,
        "paid_cents": order.paid_cents,
        "refunded_cents": order.refunded_cents,
        "item_count": sum(item.quantity for item in items),
        "title": items[0].description_snapshot if items else "",
        "created_at": iso(order.created_at),
        "expires_at": iso(order.expires_at),
        "awaiting_payment": order.status == "PENDING_PAYMENT",
    }


def order_detail_payload(session: Session, order: Order) -> dict[str, Any]:
    items = repo.list_items(session, order_id=order.id)
    installments = repo.list_installments(session, order_id=order.id)
    summary = _payment_summary(session, order_id=order.id)
    address = effective_delivery_address(session, order_id=order.id)
    invoices = [
        {
            "id": row.id,
            "type": row.type,
            "status": row.status,
            "amount_cents": row.amount_cents,
            "currency": row.currency,
            "invoice_reference": row.invoice_reference,
            "requested_at": iso(row.requested_at),
            "issued_at": iso(row.issued_at),
            "title": invoice_title_data(row).get("title"),
        }
        for row in repo.list_invoices(session, order_id=order.id)
    ]
    return {
        "id": order.id,
        "order_no": order.order_no,
        "kind": order.kind,
        "status": order.status,
        "currency": order.currency,
        "goods_cents": order.goods_cents,
        "discount_cents": order.discount_cents,
        "shipping_cents": order.shipping_cents,
        "total_cents": order.total_cents,
        "paid_cents": summary["paid_cents"],
        "refunded_applied_cents": summary["refunded_applied_cents"],
        "refunded_cents": order.refunded_cents,
        "terms_version": order.terms_version,
        "terms_accepted_at": iso(order.terms_accepted_at),
        "created_at": iso(order.created_at),
        "expires_at": iso(order.expires_at),
        "paid_at": iso(order.paid_at),
        "shipped_at": iso(order.shipped_at),
        "completed_at": iso(order.completed_at),
        "cancelled_at": iso(order.cancelled_at),
        "cancel_reason": order.cancel_reason,
        "quote_id": order.quote_id,
        "items": [
            {
                "id": item.id,
                "sku_id": item.sku_id,
                "description_snapshot": item.description_snapshot,
                "sku_snapshot": item.sku_snapshot or {},
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "discount_allocated_cents": item.discount_allocated_cents,
                "cash_goods_cents": item.cash_goods_cents,
                "refunded_goods_cents": item.refunded_goods_cents,
            }
            for item in items
        ],
        "installments": [
            {
                "id": row.id,
                "kind": row.kind,
                "sequence": row.sequence,
                "label": row.label,
                "amount_cents": row.amount_cents,
                "currency": row.currency,
                "status": row.status,
                "due_at": iso(row.due_at),
                "paid_at": iso(row.paid_at),
            }
            for row in installments
        ],
        "payments": {
            "paid_cents": summary["paid_cents"],
            "refunded_applied_cents": summary["refunded_applied_cents"],
            "allocations": summary["allocations"],
            "source": "payments" if summary.get("provider_available") else "unavailable",
        },
        "shipments": [
            {
                "id": row.id,
                "kind": row.kind,
                "carrier": row.carrier,
                "tracking_no": row.tracking_no,
                "shipped_at": iso(row.shipped_at),
                "delivered_at": iso(row.delivered_at),
                "verified_source": row.verified_source,
            }
            for row in repo.list_shipments(session, order_id=order.id)
        ],
        "address": address,
        "address_changes": [
            {
                "id": row.id,
                "actor_type": row.actor_type,
                "reason": row.reason,
                "created_at": iso(row.created_at),
                "province": row.province,
                "city": row.city,
            }
            for row in repo.list_address_changes(session, order_id=order.id)
        ],
        "invoices": invoices,
        "after_sales": [
            {
                "id": row.id,
                "order_id": row.order_id,
                "order_no": order.order_no,
                "type": row.type,
                "status": row.status,
                "reason": row.reason,
                "currency": row.currency,
                "requested_amount_cents": row.requested_amount_cents,
                "refunded_cents": row.refunded_cents,
                "restocked": bool(row.restocked),
                "return_carrier": row.return_carrier,
                "return_tracking_no": row.return_tracking_no,
                "created_at": iso(row.created_at),
            }
            for row in repo.list_after_sales_for_order(session, order_id=order.id)
        ],
        "snapshot": order.snapshot or {},
        "policy_snapshot": order.policy_snapshot or {},
    }


def after_sale_payload(session: Session, row: AfterSale) -> dict[str, Any]:
    order = repo.get_order(session, order_id=row.order_id)
    return {
        "id": row.id,
        "order_id": row.order_id,
        "order_no": order.order_no if order else None,
        "type": row.type,
        "status": row.status,
        "reason": row.reason,
        "requested_amount_cents": row.requested_amount_cents,
        "currency": row.currency,
        "refunded_cents": row.refunded_cents,
        "restocked": bool(row.restocked),
        "return_carrier": row.return_carrier,
        "return_tracking_no": row.return_tracking_no,
        "decision_note": row.decision_note,
        "created_at": iso(row.created_at),
        "decided_at": iso(row.decided_at),
        "resolved_at": iso(row.resolved_at),
        "messages": [
            {
                "id": message.id,
                "author_type": message.author_type,
                "body": message.body,
                "created_at": iso(message.created_at),
            }
            for message in repo.list_after_sale_messages(session, after_sale_id=row.id)
            if message.visibility == "customer"
        ],
        "policy": commerce_policy(session)["after_sale"],
    }


__all__ = [
    "after_sale_payload",
    "allocate_discount",
    "cancel_order",
    "cart_payload",
    "change_delivery_address",
    "commerce_policy",
    "compute_shipping_cents",
    "confirm_receipt",
    "coupon_discount_cents",
    "create_after_sale",
    "create_change_installment",
    "create_commission_order",
    "create_order",
    "create_shipment",
    "decide_after_sale",
    "effective_delivery_address",
    "expire_due_orders",
    "has_open_after_sales",
    "invoice_title_data",
    "on_installment_paid",
    "on_refund_succeeded",
    "order_detail_payload",
    "order_reward_snapshot",
    "order_summary_payload",
    "preview_checkout",
    "remove_cart_item",
    "request_invoice",
    "restock_after_return",
    "set_cart_item",
    "submit_return_shipment",
]
