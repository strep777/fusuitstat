"""目录模块 HTTP 路由：公开商品与角色目录、掉落申请、后台目录管理。

约束（03/08 文档）：售罄可浏览，禁止前端自行判定可售；掉落商品需申请后人工
审批，审批通过生成购买优先级列表（C28）；独占角色库存固定为 1。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import ContextDep, CsrfDep, CustomerIdDep, PrincipalDep, require_permission
from app.modules.catalog import repository as repo
from app.modules.catalog import service as svc
from app.modules.catalog.models import Character, Product, Sku
from app.modules.catalog.schemas import (
    ApplicationDecisionInput,
    ApplicationInput,
    ApplicationResponse,
    CatalogPage,
    CharacterCreateInput,
    CharacterResponse,
    ProductCreateInput,
    ProductPatchInput,
    ProductResponse,
    SkuInput,
    SkuPatchInput,
    StockAdjustInput,
)
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms

public_router = APIRouter(tags=["catalog"])
admin_router = APIRouter(tags=["admin-catalog"])


# ------------------------------------------------------------------ 公开商品
@public_router.get("/products", response_model=CatalogPage)
def list_products(
    ctx: ContextDep,
    kind: str | None = None,
    exclude_kinds: str | None = None,
    category: str | None = None,
    series_code: str | None = None,
    currency: Literal["CNY", "USD"] | None = None,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CatalogPage:
    excluded = [value.strip() for value in (exclude_kinds or "").split(",") if value.strip()]
    rows, total = svc.list_products(
        ctx.session,
        kind=kind,
        exclude_kinds=excluded or None,
        category=category,
        query=q,
        currency=currency,
        series_code=series_code,
        limit=limit,
        offset=(page - 1) * limit,
    )
    items = [ProductResponse(**svc.product_payload(ctx.session, row, include_skus=True, currency=currency)) for row in rows]
    return CatalogPage(
        items=items, total=total, page=page, limit=limit, has_more=page * limit < total, next_cursor=None
    )


@public_router.get("/products/facets", response_model=dict)
def product_facets(ctx: ContextDep) -> dict[str, Any]:
    return {
        "categories": [
            {"value": value, "count": count} for value, count in repo.product_categories(ctx.session)
        ]
    }


@public_router.get("/products/{slug}", response_model=ProductResponse)
def get_product(slug: str, ctx: ContextDep, currency: Literal["CNY", "USD"] | None = None) -> ProductResponse:
    product = repo.get_product_by_slug(ctx.session, slug)
    if product is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.product_payload(ctx.session, product, include_skus=True, currency=currency)
    # 需要审批的商品：告知当前用户是否已有审批通过记录（购买优先级）。
    if product.requires_approval and ctx.principal.id and ctx.principal.kind == "customer":
        approved = svc.approved_application_for(
            ctx.session, user_id=ctx.principal.id, product_id=product.id
        )
        payload["approval"] = (
            {
                "approved": True,
                "application_id": approved.id,
                "priority_rank": approved.priority_rank,
                "purchase_expires_at": iso(approved.purchase_expires_at),
            }
            if approved
            else {
                "approved": False,
                "application_id": None,
                "priority_rank": None,
                "purchase_expires_at": None,
            }
        )
    else:
        payload["approval"] = None
    return ProductResponse(**payload)


# ------------------------------------------------------------------ 公开角色
@public_router.get("/characters", response_model=CatalogPage)
def list_characters(
    ctx: ContextDep,
    status: str | None = None,
    species: str | None = None,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CatalogPage:
    rows, total = repo.list_characters(
        ctx.session, status=status, species=species, query=q, limit=limit, offset=(page - 1) * limit
    )
    items = [CharacterResponse(**svc.character_payload(ctx.session, row)) for row in rows]
    return CatalogPage(
        items=items, total=total, page=page, limit=limit, has_more=page * limit < total, next_cursor=None
    )


@public_router.get("/characters/facets", response_model=dict)
def character_facets(ctx: ContextDep) -> dict[str, Any]:
    return {
        "species": [
            {"value": value, "count": count} for value, count in repo.character_species_facets(ctx.session)
        ]
    }


@public_router.get("/characters/{slug}", response_model=CharacterResponse)
def get_character(slug: str, ctx: ContextDep) -> CharacterResponse:
    character = svc.character_available(ctx.session, slug=slug)
    if character is None:
        raise AppError("RESOURCE_NOT_FOUND")
    return CharacterResponse(**svc.character_payload(ctx.session, character))


# ------------------------------------------------------------------ 掉落与服务申请
@public_router.post("/stock-applications", response_model=ApplicationResponse, status_code=201)
def submit_application(
    payload: ApplicationInput, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> ApplicationResponse:
    """掉落商品与服务需申请后人工审批，审批通过才可购买（C28）。"""
    application = svc.submit_application(
        ctx.session,
        user_id=user_id,
        product_id=payload.product_id,
        sku_id=payload.sku_id,
        reason=payload.reason,
        kind=payload.kind,
        measurement_note=payload.measurement_note,
        preferred_date=payload.preferred_date,
        return_method=payload.return_method,
    )
    audit.record(
        ctx.session,
        actor_type="customer",
        actor_id=user_id,
        action="stock_application.submit",
        resource_type="stock_application",
        resource_id=application.id,
        after={
            "product_id": payload.product_id,
            "kind": payload.kind,
            "preferred_date": payload.preferred_date,
            "return_method": payload.return_method,
        },
        ip=ctx.client_ip,
    )
    return ApplicationResponse(**svc.application_payload(ctx.session, application))


@public_router.get("/stock-applications", response_model=list[ApplicationResponse])
def list_my_applications(
    ctx: ContextDep,
    user_id: CustomerIdDep,
    status: str | None = None,
    product_id: Annotated[str | None, Query(max_length=48)] = None,
    limit: int = 20,
) -> list[ApplicationResponse]:
    """本人申请列表（数组，不是分页对象；06 文档）。

    ``product_id`` 用于商品详情页只取本商品的审批状态——不筛选会让「只看最新 N 条」
    漏掉目标商品，进而误判为「未获准」。
    """
    rows = repo.list_applications_for_user(
        ctx.session, user_id=user_id, status=status, product_id=product_id, limit=limit
    )
    return [ApplicationResponse(**svc.application_payload(ctx.session, row)) for row in rows]


@public_router.get("/stock-applications/{application_id}", response_model=ApplicationResponse)
def get_my_application(
    application_id: str, ctx: ContextDep, user_id: CustomerIdDep
) -> ApplicationResponse:
    application = repo.get_application(ctx.session, application_id)
    if application is None or application.user_id != user_id:
        raise AppError("RESOURCE_NOT_FOUND")
    return ApplicationResponse(**svc.application_payload(ctx.session, application))


@public_router.post("/stock-applications/{application_id}/withdraw", response_model=dict)
def withdraw_application(
    application_id: str, ctx: ContextDep, _csrf: CsrfDep, user_id: CustomerIdDep
) -> dict[str, Any]:
    application = repo.get_application(ctx.session, application_id)
    if application is None or application.user_id != user_id:
        raise AppError("RESOURCE_NOT_FOUND")
    if application.status not in ("SUBMITTED", "NEEDS_INFO"):
        raise AppError("STATE_CONFLICT", "该申请已进入审批或已结束，不能撤回。")
    application.status = "REJECTED"
    application.decision_reason = "用户主动撤回"
    application.decided_at = now_ms()
    application.version += 1
    application.updated_at = now_ms()
    return {"id": application.id, "status": application.status}


# ------------------------------------------------------------------ 后台商品
@admin_router.get(
    "/products", response_model=CatalogPage, dependencies=[Depends(require_permission("catalog.read"))]
)
def admin_list_products(
    ctx: ContextDep,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 50,
) -> CatalogPage:
    rows, total = repo.list_products(
        ctx.session, kind=kind, published_only=(status == "published"), limit=limit
    )
    if status and status != "published":
        rows = [row for row in rows if row.status == status]
    items = []
    for row in rows:
        payload = svc.product_payload(ctx.session, row, include_skus=True)
        payload.update({"status": row.status, "version": row.version, "updated_at": iso(row.updated_at)})
        items.append(ProductResponse(**payload))
    return CatalogPage(items=items, total=total, page=1, limit=limit, has_more=False, next_cursor=None)


@admin_router.get(
    "/products/{product_id}", response_model=dict, dependencies=[Depends(require_permission("catalog.read"))]
)
def admin_get_product(product_id: str, ctx: ContextDep) -> dict[str, Any]:
    product = repo.get_product(ctx.session, product_id)
    if product is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.product_payload(ctx.session, product, include_skus=True)
    payload.update(
        {"status": product.status, "version": product.version, "updated_at": iso(product.updated_at)}
    )
    payload["inventory_movements"] = {
        sku["id"]: [
            {
                "action": row.action,
                "delta_on_hand": row.delta_on_hand,
                "delta_reserved": row.delta_reserved,
                "reason": row.reason,
                "source_type": row.source_type,
                "created_at": iso(row.created_at),
            }
            for row in repo.movements_for_sku(ctx.session, sku_id=sku["id"], limit=20)
        ]
        for sku in payload.get("skus", [])
    }
    return payload


@admin_router.post(
    "/products",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_create_product(
    payload: ProductCreateInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    existing = repo.get_product_by_slug(ctx.session, payload.slug, published_only=False)
    if existing is not None:
        raise AppError("STATE_CONFLICT", "该 slug 已被占用。")
    product = Product(
        id=new_id("product"),
        slug=payload.slug,
        kind=payload.kind,
        title=payload.title,
        subtitle=payload.subtitle,
        description=payload.description,
        highlights=payload.highlights,
        category=payload.category,
        series_code=payload.series_code,
        requires_approval=1 if payload.requires_approval else 0,
        shipping_template=payload.shipping_template,
        service_kind=payload.service_kind,
        sort_order=payload.sort_order,
        seo_description=payload.seo_description,
        terms_version=payload.terms_version,
    )
    ctx.session.add(product)
    ctx.session.flush()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="product.create",
        resource_type="product",
        resource_id=product.id,
        after={"slug": product.slug, "kind": product.kind, "status": product.status},
    )
    return {"id": product.id, "slug": product.slug, "status": product.status, "version": product.version}


@admin_router.patch(
    "/products/{product_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_update_product(
    product_id: str, payload: ProductPatchInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    product = repo.get_product(ctx.session, product_id)
    if product is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if product.version != payload.version:
        raise AppError("VERSION_CONFLICT")
    before = {
        "title": product.title,
        "status": product.status,
        "requires_approval": bool(product.requires_approval),
    }
    for name in (
        "title",
        "subtitle",
        "description",
        "highlights",
        "category",
        "series_code",
        "shipping_template",
        "service_kind",
        "sort_order",
        "seo_description",
        "terms_version",
        "kind",
    ):
        value = getattr(payload, name, None)
        if value is not None:
            setattr(product, name, value)
    if payload.requires_approval is not None:
        product.requires_approval = 1 if payload.requires_approval else 0
    if payload.status is not None:
        if payload.status == "published":
            skus = repo.list_skus(ctx.session, product_id=product.id, active_only=True)
            if not skus:
                raise AppError("STATE_CONFLICT", "上架前必须先添加至少一个 SKU。")
        product.status = payload.status
    product.version += 1
    product.updated_at = now_ms()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="product.update",
        resource_type="product",
        resource_id=product.id,
        before=before,
        after={"title": product.title, "status": product.status},
    )
    return {"id": product.id, "status": product.status, "version": product.version}


@admin_router.post(
    "/products/{product_id}/skus",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_create_sku(
    product_id: str, payload: SkuInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    product = repo.get_product(ctx.session, product_id)
    if product is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if repo.get_sku_by_code(ctx.session, payload.sku_code) is not None:
        raise AppError("STATE_CONFLICT", "该 SKU 编码已存在。")
    if product.kind == "exclusive_character" and repo.list_skus(ctx.session, product_id=product.id):
        # 独占角色只能绑定一个销售 SKU（08 文档）。
        raise AppError("STATE_CONFLICT", "独占角色只能绑定一个销售 SKU。")
    sku = Sku(
        id=new_id("sku"),
        product_id=product.id,
        sku_code=payload.sku_code,
        label=payload.label,
        attributes=payload.attributes,
        price_cents=payload.price_cents,
        currency=payload.currency,
        compare_at_cents=payload.compare_at_cents,
        stock_on_hand=1 if product.kind == "exclusive_character" else payload.stock_on_hand,
        reserved=0,
        max_per_order=1 if product.kind == "exclusive_character" else payload.max_per_order,
        weight_grams=payload.weight_grams,
        is_active=1 if payload.is_active else 0,
        cover_asset_id=payload.cover_asset_id,
        sort_order=payload.sort_order,
    )
    ctx.session.add(sku)
    ctx.session.flush()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="sku.create",
        resource_type="sku",
        resource_id=sku.id,
        after={"sku_code": sku.sku_code, "price_cents": sku.price_cents, "stock": sku.stock_on_hand},
    )
    return {"id": sku.id, "sku_code": sku.sku_code, "version": sku.version}


@admin_router.patch(
    "/skus/{sku_id}", response_model=dict, dependencies=[Depends(require_permission("catalog.write"))]
)
def admin_update_sku(
    sku_id: str, payload: SkuPatchInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    sku = repo.get_sku(ctx.session, sku_id)
    if sku is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if sku.version != payload.version:
        raise AppError("VERSION_CONFLICT")
    before = {"price_cents": sku.price_cents, "is_active": bool(sku.is_active), "label": sku.label}
    for name in (
        "label",
        "attributes",
        "price_cents",
        "compare_at_cents",
        "max_per_order",
        "weight_grams",
        "cover_asset_id",
        "sort_order",
    ):
        value = getattr(payload, name, None)
        if value is not None:
            setattr(sku, name, value)
    if payload.is_active is not None:
        sku.is_active = 1 if payload.is_active else 0
    sku.version += 1
    sku.updated_at = now_ms()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="sku.update",
        resource_type="sku",
        resource_id=sku.id,
        before=before,
        after={"price_cents": sku.price_cents, "is_active": bool(sku.is_active), "label": sku.label},
    )
    return {"id": sku.id, "version": sku.version}


@admin_router.post(
    "/skus/{sku_id}/stock",
    response_model=dict,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_adjust_stock(
    sku_id: str, payload: StockAdjustInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    """库存调整必须填写原因并写审计；调整后不得低于已预留数量。"""
    sku = svc.adjust_stock(
        ctx.session,
        sku_id=sku_id,
        delta=payload.delta,
        reason=payload.reason,
        actor_id=principal.id,
    )
    return {
        "id": sku.id,
        "stock_on_hand": sku.stock_on_hand,
        "reserved": sku.reserved,
        "available": max(0, sku.stock_on_hand - sku.reserved),
        "version": sku.version,
    }


@admin_router.get(
    "/skus/low-stock", response_model=list, dependencies=[Depends(require_permission("catalog.read"))]
)
def admin_low_stock(ctx: ContextDep, threshold: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "id": sku.id,
            "sku_code": sku.sku_code,
            "label": sku.label,
            "stock_on_hand": sku.stock_on_hand,
            "reserved": sku.reserved,
            "available": max(0, sku.stock_on_hand - sku.reserved),
        }
        for sku in repo.low_stock_skus(ctx.session, threshold=threshold)
    ]


# ------------------------------------------------------------------ 后台角色
@admin_router.post(
    "/characters",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_create_character(
    payload: CharacterCreateInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    product = repo.get_product(ctx.session, payload.product_id)
    if product is None or product.kind != "exclusive_character":
        raise AppError("VALIDATION_ERROR", "角色必须绑定 exclusive_character 类型的商品。")
    if repo.get_character_by_product(ctx.session, payload.product_id) is not None:
        raise AppError("STATE_CONFLICT", "该商品已绑定角色。")
    character = Character(
        id=new_id("character"),
        product_id=payload.product_id,
        slug=payload.slug,
        name=payload.name,
        species=payload.species,
        designer_credit=payload.designer_credit,
        story=payload.story,
        rights_summary=payload.rights_summary,
        public_status=payload.public_status,
        exclusive=1 if payload.exclusive else 0,
        adoption_note=payload.adoption_note,
        cover_asset_id=payload.cover_asset_id,
        tags=payload.tags,
        display_authorized=1 if payload.display_authorized else 0,
    )
    ctx.session.add(character)
    ctx.session.flush()
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="character.create",
        resource_type="character",
        resource_id=character.id,
        after={"slug": character.slug, "public_status": character.public_status},
    )
    return {"id": character.id, "slug": character.slug, "public_status": character.public_status}


@admin_router.post(
    "/characters/{character_id}/release",
    response_model=dict,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_release_character(
    character_id: str, payload: dict[str, Any], ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    """取消领养后是否重新上架由运营审核决定，退款回调不会自动复售。"""
    reason = str(payload.get("reason", "运营审核后重新上架"))
    svc.release_character(ctx.session, character_id=character_id, reason=reason)
    audit.record(
        ctx.session,
        actor_type="staff",
        actor_id=principal.id,
        action="character.release",
        resource_type="character",
        resource_id=character_id,
        reason=reason,
    )
    return {"id": character_id, "public_status": "available"}


# ------------------------------------------------------------------ 后台申请审批
@admin_router.get(
    "/stock-applications",
    response_model=CatalogPage,
    dependencies=[Depends(require_permission("catalog.read"))],
)
def admin_list_applications(
    ctx: ContextDep,
    status: str | None = None,
    product_id: str | None = None,
    kind: str | None = None,
    order_by_priority: bool = False,
    limit: int = 50,
) -> CatalogPage:
    """申请列表；``order_by_priority=true`` 时按 C28 的购买优先级排序（已审批在前、名次小者优先）。"""
    rows, total = repo.list_applications_admin(
        ctx.session,
        status=status,
        product_id=product_id,
        kind=kind,
        order_by_priority=order_by_priority,
        limit=limit,
    )
    items = [ApplicationResponse(**svc.application_payload(ctx.session, row)) for row in rows]
    return CatalogPage(items=items, total=total, page=1, limit=limit, has_more=False, next_cursor=None)


@admin_router.post(
    "/stock-applications/{application_id}/decide",
    response_model=ApplicationResponse,
    dependencies=[Depends(require_permission("catalog.write"))],
)
def admin_decide_application(
    application_id: str,
    payload: ApplicationDecisionInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> ApplicationResponse:
    """审批掉落/服务申请。通过后生成购买优先级列表与购买截止时间（C28）。"""
    application = svc.decide_application(
        ctx.session,
        application_id=application_id,
        decision=payload.decision,
        actor_id=principal.id,
        reason=payload.reason,
        priority_rank=payload.priority_rank,
        purchase_window_days=payload.purchase_window_days,
    )
    if payload.decision == "APPROVED":
        from app.platform.reliability import enqueue

        enqueue(
            ctx.session,
            event_type="notification.dispatch",
            aggregate_id=application.id,
            payload={"reason": "application_approved", "application_id": application.id},
            aggregate_version=application.version,
        )
    return ApplicationResponse(**svc.application_payload(ctx.session, application))


@admin_router.get(
    "/catalog/stats", response_model=dict, dependencies=[Depends(require_permission("catalog.read"))]
)
def admin_catalog_stats(ctx: ContextDep) -> dict[str, int]:
    return svc.admin_catalog_counts(ctx.session)


__all__ = ["public_router", "admin_router"]
