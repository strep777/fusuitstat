"""目录模块用例：商品目录、原子库存操作、角色领养、掉落申请审批。

库存不变量（08 文档）：
1. 事务内校验 SKU 可售，原子增加 reserved，条件 ``stock_on_hand - reserved >= quantity``；
2. 已核验收款分配后 ``stock_on_hand -= qty; reserved -= qty``，预留转 CONSUMED；
3. 售后退款不自动补库存，只有检验回仓或经审核的独占角色可复售才 RESTOCK。
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.catalog import repository as repo
from app.modules.catalog.models import (
    RETURN_METHODS,
    Character,
    InventoryMovement,
    InventoryReservation,
    Product,
    Sku,
    StockApplication,
)
from app.platform import audit
from app.platform.db import affected_rows
from app.platform.errors import AppError
from app.platform.ids import add_days_ms, iso, new_id, now_ms


# ------------------------------------------------------------------ 公开目录
def list_products(
    session: Session,
    *,
    kind: str | None = None,
    exclude_kinds: list[str] | None = None,
    category: str | None = None,
    query: str | None = None,
    currency: str | None = None,
    series_code: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Product], int]:
    return repo.list_products(
        session,
        kind=kind,
        exclude_kinds=exclude_kinds,
        category=category,
        query=query,
        currency=currency,
        series_code=series_code,
        limit=limit,
        offset=offset,
    )


def product_payload(session: Session, product: Product, *, include_skus: bool = True, currency: str | None = None) -> dict[str, Any]:
    from app.modules.media.ports import public_media_payload

    payload: dict[str, Any] = {
        "id": product.id,
        "slug": product.slug,
        "kind": product.kind,
        "title": product.title,
        "subtitle": product.subtitle,
        "description": product.description,
        "highlights": product.highlights or [],
        "category": product.category,
        "series_code": product.series_code,
        "requires_approval": bool(product.requires_approval),
        "terms_version": product.terms_version,
        "service_kind": product.service_kind,
        "seo_description": product.seo_description,
    }
    skus = repo.list_skus(session, product_id=product.id, active_only=True)
    if currency:
        skus = [sku for sku in skus if sku.currency == currency]
    # 混合币种不能直接取数值最小项后配另一币种；默认展示第一种币种的最低价。
    display_currency = currency or (skus[0].currency if skus else "CNY")
    priced_skus = [sku for sku in skus if sku.currency == display_currency]
    payload["currency"] = display_currency
    payload["price_from_cents"] = min((sku.price_cents for sku in priced_skus), default=None)
    if include_skus:
        payload["skus"] = [sku_payload(session, sku) for sku in skus]
    images: list[dict[str, Any]] = []
    for asset_id in repo.product_image_ids(session, product_id=product.id):
        media = public_media_payload(session, asset_id)
        if media:
            images.append(media)
    payload["images"] = images
    payload["availability"] = (
        "available"
        if any(sku.stock_on_hand - sku.reserved > 0 for sku in skus)
        else ("sold_out" if skus else "unavailable")
    )
    return payload


def sku_payload(session: Session, sku: Sku) -> dict[str, Any]:
    from app.modules.media.ports import public_media_payload

    available = max(0, sku.stock_on_hand - sku.reserved)
    cover = public_media_payload(session, sku.cover_asset_id) if sku.cover_asset_id else None
    return {
        "id": sku.id,
        "sku_code": sku.sku_code,
        "label": sku.label,
        "attributes": sku.attributes or {},
        "price_cents": sku.price_cents,
        "currency": sku.currency,
        "compare_at_cents": sku.compare_at_cents,
        "available": available,
        "in_stock": available > 0,
        "low_stock": 0 < available <= 3,
        "max_per_order": sku.max_per_order,
        "weight_grams": sku.weight_grams,
        "cover": cover,
    }


def character_payload(session: Session, character: Character) -> dict[str, Any]:
    from app.modules.media.ports import public_media_payload

    product = repo.get_product(session, character.product_id)
    images: list[dict[str, Any]] = []
    for asset_id in repo.product_image_ids(session, product_id=character.product_id):
        media = public_media_payload(session, asset_id)
        if media:
            images.append(media)
    skus = repo.list_skus(session, product_id=character.product_id, active_only=True)
    price_cents = min((sku.price_cents for sku in skus), default=None)
    return {
        "id": character.id,
        "slug": character.slug,
        "name": character.name,
        "species": character.species,
        "designer_credit": character.designer_credit,
        "story": character.story,
        "rights_summary": character.rights_summary,
        "status": character.public_status,
        "exclusive": bool(character.exclusive),
        "adoption_note": character.adoption_note,
        "price_cents": price_cents,
        "currency": skus[0].currency if skus else "CNY",
        "purchasable": character.public_status == "available"
        and any(sku.stock_on_hand - sku.reserved > 0 for sku in skus),
        "product_slug": product.slug if product else None,
        "images": images,
        "tags": character.tags or [],
    }


# ------------------------------------------------------------------ 库存原子操作
def _expire_sku(session: Session, sku: Sku) -> None:
    """过期 Sku 的 ORM 缓存，使后续读取回到数据库真实值。

    条件 UPDATE 是 Core 语句，ORM 缓存仍是旧值；若随后 ``flush()`` 把缓存里的旧
    ``reserved``/``stock_on_hand`` 写回，就会静默抵消刚刚成功的原子更新
    （同一事务内多次预留时表现为“隔一次成功一次”）。
    """
    # 已分离的实例无法过期，忽略即可（后续读取仍从数据库取数）。
    with contextlib.suppress(Exception):
        session.expire(sku)


def reserve(
    session: Session,
    *,
    sku_id: str,
    quantity: int,
    order_id: str,
    order_item_id: str,
    source_type: str = "order",
    expires_at: int | None = None,
    reason: str | None = None,
) -> InventoryReservation:
    """条件更新增加 reserved，防超卖。``stock_on_hand - reserved >= quantity``。

    并发正确性来自数据库层的条件 UPDATE（``rowcount`` 为 0 即库存不足），
    不依赖先读后判断；更新后必须刷新 ORM 缓存，避免同一事务内的后续 flush
    把旧值写回。
    """
    if quantity <= 0:
        raise AppError("VALIDATION_ERROR", "数量必须大于 0。", fields={"quantity": "必须大于 0"})
    sku = repo.get_sku(session, sku_id)
    if sku is None or not sku.is_active:
        raise AppError("STOCK_NOT_ENOUGH", "该规格当前不可售。")
    product = repo.get_product(session, sku.product_id)
    if product is None or product.status != "published":
        raise AppError("STOCK_NOT_ENOUGH", "该商品当前不可售。")
    if quantity > sku.max_per_order:
        raise AppError(
            "VALIDATION_ERROR",
            f"单个规格每单最多 {sku.max_per_order} 件。",
            fields={"quantity": f"最多 {sku.max_per_order} 件"},
        )

    # 先把待登记的预留与流水挂上，再执行条件更新：之后不再改动 Sku 的 ORM 状态，
    # flush 只会写预留行与流水行。
    result = session.execute(
        sa.update(Sku)
        .where(
            Sku.id == sku_id,
            Sku.is_active == 1,
            Sku.stock_on_hand - Sku.reserved >= quantity,
        )
        .values(reserved=Sku.reserved + quantity, version=Sku.version + 1)
    )
    if not affected_rows(result):
        raise AppError("STOCK_NOT_ENOUGH", "库存不足，请调整数量。")
    _expire_sku(session, sku)

    reservation = InventoryReservation(
        id=new_id("sku"),
        order_item_id=order_item_id,
        order_id=order_id,
        sku_id=sku_id,
        quantity=quantity,
        state="ACTIVE",
        expires_at=expires_at,
    )
    session.add(reservation)
    _movement(
        session,
        sku_id=sku_id,
        action="RESERVE",
        delta_on_hand=0,
        delta_reserved=quantity,
        source_type=source_type,
        source_id=order_id,
        reason=reason,
    )
    session.flush()
    return reservation


def release(session: Session, *, reservation_id: str, reason: str) -> None:
    """释放预留。与支付成功用条件更新竞争，不允许同时发生。"""
    reservation = session.get(InventoryReservation, reservation_id)
    if reservation is None:
        return
    if reservation.state != "ACTIVE":
        return
    sku = session.get(Sku, reservation.sku_id)
    result = session.execute(
        sa.update(Sku)
        .where(Sku.id == reservation.sku_id, Sku.reserved >= reservation.quantity)
        .values(reserved=Sku.reserved - reservation.quantity, version=Sku.version + 1)
    )
    if not affected_rows(result):
        # 预留与库存不一致属于数据异常，记录但不静默吞掉。
        raise AppError("STATE_CONFLICT", "库存预留释放失败，请联系技术负责人核对。")
    if sku is not None:
        _expire_sku(session, sku)
    reservation.state = "RELEASED"
    reservation.released_reason = reason[:120]
    _movement(
        session,
        sku_id=reservation.sku_id,
        action="RELEASE",
        delta_on_hand=0,
        delta_reserved=-reservation.quantity,
        source_type="reservation",
        source_id=reservation.id,
        reason=reason,
    )
    session.flush()


def consume(session: Session, *, reservation_id: str, reason: str | None = None) -> None:
    """已核验收款分配后扣减实物库存。"""
    reservation = session.get(InventoryReservation, reservation_id)
    if reservation is None or reservation.state != "ACTIVE":
        raise AppError("STATE_CONFLICT", "库存预留状态不允许核销。")
    sku = session.get(Sku, reservation.sku_id)
    result = session.execute(
        sa.update(Sku)
        .where(
            Sku.id == reservation.sku_id,
            Sku.reserved >= reservation.quantity,
            Sku.stock_on_hand >= reservation.quantity,
        )
        .values(
            stock_on_hand=Sku.stock_on_hand - reservation.quantity,
            reserved=Sku.reserved - reservation.quantity,
            version=Sku.version + 1,
        )
    )
    if not affected_rows(result):
        raise AppError("STATE_CONFLICT", "库存核销失败，请核对库存与预留。")
    if sku is not None:
        _expire_sku(session, sku)
    reservation.state = "CONSUMED"
    _movement(
        session,
        sku_id=reservation.sku_id,
        action="CONSUME",
        delta_on_hand=-reservation.quantity,
        delta_reserved=-reservation.quantity,
        source_type="reservation",
        source_id=reservation.id,
        reason=reason,
    )
    session.flush()


def restock(session: Session, *, sku_id: str, quantity: int, reason: str, source_id: str) -> None:
    """补库存。售后退款不自动调用本函数（08 文档第 5 条）。"""
    if quantity <= 0:
        raise AppError("VALIDATION_ERROR", "补库存数量必须大于 0。")
    sku = session.get(Sku, sku_id)
    session.execute(
        sa.update(Sku)
        .where(Sku.id == sku_id)
        .values(stock_on_hand=Sku.stock_on_hand + quantity, version=Sku.version + 1)
    )
    if sku is not None:
        _expire_sku(session, sku)
    _movement(
        session,
        sku_id=sku_id,
        action="RESTOCK",
        delta_on_hand=quantity,
        delta_reserved=0,
        source_type="manual",
        source_id=source_id,
        reason=reason,
    )
    session.flush()


def adjust_stock(
    session: Session, *, sku_id: str, delta: int, reason: str, actor_id: str | None
) -> Sku:
    """人工调整库存。必须写原因与审计。"""
    sku = repo.get_sku(session, sku_id)
    if sku is None:
        raise AppError("RESOURCE_NOT_FOUND")
    product = repo.get_product(session, sku.product_id)
    if (
        product is not None
        and product.kind == "exclusive_character"
        and sku.stock_on_hand + delta > 1
    ):
        # 08 文档：独占角色只能绑定一个销售 SKU，库存固定为 1。
        # 允许降到 0（下架）或回到 1（复售），但不允许调成大于 1 的可售数量。
        raise AppError(
            "STATE_CONFLICT",
            "独占角色的可售数量固定为 1，不能通过调整库存增加。",
            fields={"delta": "独占角色库存不可超过 1"},
        )
    if sku.stock_on_hand + delta < sku.reserved:
        raise AppError(
            "STATE_CONFLICT",
            f"调整后库存不能低于已预留数量（{sku.reserved}）。",
        )
    before = {"stock_on_hand": sku.stock_on_hand, "reserved": sku.reserved}
    sku.stock_on_hand += delta
    sku.version += 1
    sku.updated_at = now_ms()
    _movement(
        session,
        sku_id=sku_id,
        action="ADJUST",
        delta_on_hand=delta,
        delta_reserved=0,
        source_type="staff",
        source_id=actor_id or "system",
        reason=reason,
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="sku.stock_adjust",
        resource_type="sku",
        resource_id=sku_id,
        before=before,
        after={"stock_on_hand": sku.stock_on_hand, "reserved": sku.reserved},
        reason=reason,
    )
    session.flush()
    return sku


def _movement(
    session: Session,
    *,
    sku_id: str,
    action: str,
    delta_on_hand: int,
    delta_reserved: int,
    source_type: str,
    source_id: str,
    reason: str | None,
) -> None:
    existing = session.scalars(
        sa.select(InventoryMovement).where(
            InventoryMovement.source_type == source_type,
            InventoryMovement.source_id == source_id,
            InventoryMovement.sku_id == sku_id,
            InventoryMovement.action == action,
        )
    ).one_or_none()
    if existing is not None:
        return
    session.add(
        InventoryMovement(
            id=new_id("sku"),
            sku_id=sku_id,
            action=action,
            delta_on_hand=delta_on_hand,
            delta_reserved=delta_reserved,
            source_type=source_type,
            source_id=source_id,
            reason=(reason or "")[:200] or None,
        )
    )


def expire_reservations(session: Session, *, moment: int | None = None, limit: int = 50) -> int:
    """过期预留由订单关闭流程显式释放；这里只标记超时项供任务处理。"""
    now = moment if moment is not None else now_ms()
    rows = list(
        session.scalars(
            sa.select(InventoryReservation)
            .where(
                InventoryReservation.state == "ACTIVE",
                InventoryReservation.expires_at.is_not(None),
                InventoryReservation.expires_at <= now,
            )
            .limit(limit)
        )
    )
    return len(rows)


# ------------------------------------------------------------------ 角色领养
def character_available(session: Session, *, slug: str) -> Character | None:
    character = repo.get_character_by_slug(session, slug)
    if character is None or not character.display_authorized:
        return None
    return character


def reserve_exclusive_character(session: Session, *, character_id: str, order_id: str) -> None:
    """独占角色付款前先占位，防止同一角色卖出两次。"""
    result = session.execute(
        sa.update(Character)
        .where(Character.id == character_id, Character.public_status == "available")
        .values(public_status="reserved", version=Character.version + 1)
    )
    if not affected_rows(result):
        raise AppError("STOCK_NOT_ENOUGH", "该角色已被占用或已领养。")
    audit.record(
        session,
        actor_type="system",
        action="character.reserved",
        resource_type="character",
        resource_id=character_id,
        after={"order_id": order_id, "public_status": "reserved"},
    )
    session.flush()


def mark_character_adopted(session: Session, *, character_id: str, order_id: str) -> None:
    result = session.execute(
        sa.update(Character)
        .where(Character.id == character_id)
        .values(public_status="adopted", version=Character.version + 1)
    )
    if not affected_rows(result):
        raise AppError("RESOURCE_NOT_FOUND")
    audit.record(
        session,
        actor_type="system",
        action="character.adopted",
        resource_type="character",
        resource_id=character_id,
        after={"order_id": order_id, "public_status": "adopted"},
    )
    session.flush()


def release_character(session: Session, *, character_id: str, reason: str) -> None:
    """取消后是否重新上架由运营审核，不能因退款回调自动公开设定图或复售。

    本函数只把状态改为 available 供运营决定；公开可见性由 ``display_authorized``
    与运营审核共同决定。
    """
    session.execute(
        sa.update(Character)
        .where(Character.id == character_id, Character.public_status == "reserved")
        .values(public_status="available", version=Character.version + 1)
    )
    audit.record(
        session,
        actor_type="system",
        action="character.released",
        resource_type="character",
        resource_id=character_id,
        reason=reason,
    )
    session.flush()


# ------------------------------------------------------------------ 掉落申请（C28）
_BOOKING_TZ = ZoneInfo("Asia/Shanghai")


def _validate_wash_booking(
    product: Product,
    *,
    kind: str,
    preferred_date: str | None,
    return_method: str | None,
) -> None:
    """校验洗毛预约（FR-025）：仅 cleaning 可带日期/返还方式，日期为北京时间今日 +2..+60。"""
    if kind != "cleaning":
        if preferred_date is not None or return_method is not None:
            raise AppError(
                "VALIDATION_ERROR",
                fields={"preferred_date": "只有洗毛服务可以填写预约日期"},
            )
        return

    if product.service_kind != "cleaning":
        raise AppError("VALIDATION_ERROR", fields={"product_id": "该商品不是洗毛服务"})
    if preferred_date is None:
        raise AppError("BOOKING_DATE_INVALID", fields={"preferred_date": "请选择期望日期"})
    try:
        wanted = date.fromisoformat(preferred_date)
    except ValueError:
        raise AppError(
            "BOOKING_DATE_INVALID", fields={"preferred_date": "日期格式应为 YYYY-MM-DD"}
        ) from None
    offset = (wanted - datetime.now(_BOOKING_TZ).date()).days
    if offset < 2 or offset > 60:
        raise AppError(
            "BOOKING_DATE_INVALID",
            fields={"preferred_date": "期望日期需在北京时间今日 2 天后至 60 天内"},
        )
    if return_method not in RETURN_METHODS:
        raise AppError(
            "VALIDATION_ERROR",
            fields={"return_method": "返还方式需选择到店取回或寄回"},
        )


def submit_application(
    session: Session,
    *,
    user_id: str,
    product_id: str,
    sku_id: str | None,
    reason: str,
    kind: str = "drop",
    measurement_note: str | None = None,
    preferred_date: str | None = None,
    return_method: str | None = None,
) -> StockApplication:
    product = repo.get_product(session, product_id)
    if product is None or product.status != "published":
        raise AppError("RESOURCE_NOT_FOUND")
    _validate_wash_booking(
        product, kind=kind, preferred_date=preferred_date, return_method=return_method
    )
    existing = repo.pending_application(session, user_id=user_id, product_id=product_id)
    if existing is not None:
        raise AppError("STATE_CONFLICT", "该商品已有正在处理的申请，请等待审批结果。")
    application = StockApplication(
        id=new_id("application"),
        user_id=user_id,
        product_id=product_id,
        sku_id=sku_id,
        kind=kind,
        reason=reason,
        measurement_note=measurement_note,
        preferred_date=preferred_date,
        return_method=return_method,
    )
    session.add(application)
    session.flush()
    return application


def decide_application(
    session: Session,
    *,
    application_id: str,
    decision: str,
    actor_id: str | None,
    reason: str,
    priority_rank: int | None = None,
    purchase_window_days: int = 7,
) -> StockApplication:
    application = repo.get_application(session, application_id)
    if application is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if application.status in ("CONVERTED", "EXPIRED"):
        raise AppError("STATE_CONFLICT", "该申请已结束，不能再次审批。")
    if decision not in ("APPROVED", "REJECTED", "NEEDS_INFO"):
        raise AppError("VALIDATION_ERROR", fields={"decision": "非法决定"})
    before = {"status": application.status}
    application.status = decision
    application.decided_by = actor_id
    application.decided_at = now_ms()
    application.decision_reason = reason[:400]
    if decision == "APPROVED":
        application.priority_rank = priority_rank
        application.purchase_expires_at = add_days_ms(purchase_window_days)
    application.version += 1
    application.updated_at = now_ms()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="stock_application.decide",
        resource_type="stock_application",
        resource_id=application.id,
        before=before,
        after={"status": decision, "priority_rank": priority_rank},
        reason=reason,
    )
    session.flush()
    return application


def approved_application_for(
    session: Session, *, user_id: str, product_id: str
) -> StockApplication | None:
    """审批通过列表作为购买优先级（C28）。"""
    row = repo.latest_approved_application(session, user_id=user_id, product_id=product_id)
    if row is None:
        return None
    if row.purchase_expires_at and row.purchase_expires_at <= now_ms():
        row.status = "EXPIRED"
        session.flush()
        return None
    return row


def mark_application_converted(session: Session, *, application_id: str, order_id: str) -> None:
    application = repo.get_application(session, application_id)
    if application is None:
        return
    application.status = "CONVERTED"
    application.converted_order_id = order_id
    application.version += 1
    application.updated_at = now_ms()
    session.flush()


def application_payload(session: Session, application: StockApplication) -> dict[str, Any]:
    product = repo.get_product(session, application.product_id)
    return {
        "id": application.id,
        "kind": application.kind,
        "status": application.status,
        "user_id": application.user_id,
        "product_id": application.product_id,
        "product_title": product.title if product else None,
        "product_slug": product.slug if product else None,
        "reason": application.reason,
        "measurement_note": application.measurement_note,
        "preferred_date": application.preferred_date,
        "return_method": application.return_method,
        "decision_reason": application.decision_reason,
        "priority_rank": application.priority_rank,
        "purchase_expires_at": iso(application.purchase_expires_at),
        "created_at": iso(application.created_at),
        "decided_at": iso(application.decided_at),
    }


def admin_catalog_counts(session: Session) -> dict[str, int]:
    products = int(
        session.scalar(
            sa.select(sa.func.count()).select_from(Product).where(Product.status == "published")
        )
        or 0
    )
    pending = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(StockApplication)
            .where(StockApplication.status.in_(("SUBMITTED", "NEEDS_INFO")))
        )
        or 0
    )
    low_stock = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Sku)
            .where(Sku.is_active == 1, Sku.stock_on_hand - Sku.reserved <= 1)
        )
        or 0
    )
    return {"published_products": products, "pending_applications": pending, "low_stock_skus": low_stock}
