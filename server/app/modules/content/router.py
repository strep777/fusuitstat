"""内容模块 HTTP 路由：公开读取、后台编辑与发布。

公开读取只返回 published；后台动作绑定权限码并写审计（FR-001–003、FR-016）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import (
    ContextDep,
    CsrfDep,
    CustomerIdDep,
    PrincipalDep,
    enforce_write_guard,
    require_permission,
)
from app.modules.content import repository as repo
from app.modules.content import service as svc
from app.modules.content.models import CONTENT_KINDS
from app.modules.content.schemas import (
    ContentCreateInput,
    ContentEntryDetail,
    ContentEntrySummary,
    ContentPage,
    ContentUpdateInput,
    DisplayAuthorizationItem,
    DisplayAuthorizationPage,
    DisplayRetractInput,
    DisplayRetractResponse,
    PaginationQuery,
    PublishInput,
    SeriesSpecInput,
    SettingUpdateInput,
    SlugQuery,
)
from app.modules.media.ports import public_media_payload
from app.platform.config import get_settings
from app.platform.errors import AppError
from app.platform.ids import iso

public_router = APIRouter(tags=["content"])
admin_router = APIRouter(tags=["admin-content"])


def _with_cover(session: Any, entry: Any, *, include_body: bool = False) -> dict[str, Any]:
    payload = svc.entry_payload(entry, include_body=include_body)
    payload["cover"] = public_media_payload(session, entry.cover_asset_id) if entry.cover_asset_id else None
    if include_body:
        payload["gallery"] = _gallery(session, entry)
    return payload


def _gallery(session: Any, entry: Any) -> list[dict[str, Any]]:
    from app.modules.media.ports import assets_for

    images: list[dict[str, Any]] = []
    for asset in assets_for(session, resource_type="content_entry", resource_id=entry.id):
        media = public_media_payload(session, asset.id)
        if media:
            images.append(media)
    return images


def _page(items: list[dict[str, Any]], total: int, page: int, limit: int) -> ContentPage:
    return ContentPage(
        items=[ContentEntrySummary(**item) for item in items],
        total=total,
        page=page,
        limit=limit,
        has_more=page * limit < total,
        next_cursor=None,
    )


# ------------------------------------------------------------------ 公开读取
@public_router.get("/content/settings", response_model=dict)
def get_public_settings(ctx: ContextDep) -> dict[str, Any]:
    """公开站点配置：品牌信息、社交链接、规则与功能开关（不含任何密钥）。"""
    return svc.public_settings(ctx.session)


@public_router.get("/content/settings/features", response_model=dict)
def get_feature_flags(ctx: ContextDep) -> dict[str, bool]:
    return svc.feature_flags(ctx.session)


@public_router.get("/content/work/facets", response_model=dict)
def get_work_facets(ctx: ContextDep) -> dict[str, Any]:
    """作品筛选维度。筛选条件可刷新恢复（category/series/species/q/page）。"""
    return {
        "work_types": [{"value": value, "count": count} for value, count in repo.work_type_facets(ctx.session)],
        "species": [{"value": value, "count": count} for value, count in repo.species_facets(ctx.session)],
        "series": [
            {
                "value": spec.code,
                "count": 0,
                "label": entry.title,
            }
            for spec, entry in repo.list_series(ctx.session, active_only=True)
        ],
    }


@public_router.get("/content/series", response_model=dict)
def list_series_public(ctx: ContextDep) -> dict[str, Any]:
    items = []
    for spec, entry in repo.list_series(ctx.session, active_only=True):
        items.append(
            {
                **svc.series_payload(spec, entry),
                "cover": public_media_payload(ctx.session, entry.cover_asset_id)
                if entry.cover_asset_id
                else None,
            }
        )
    return {"items": items, "total": len(items), "has_more": False, "next_cursor": None}


@public_router.get("/content/series/{slug}", response_model=dict)
def get_series_public(slug: str, ctx: ContextDep) -> dict[str, Any]:
    entry = repo.get_published(ctx.session, kind="series", slug=slug)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    spec = repo.get_series_for_entry(ctx.session, entry.id)
    payload = svc.entry_payload(entry, include_body=True)
    payload["cover"] = (
        public_media_payload(ctx.session, entry.cover_asset_id) if entry.cover_asset_id else None
    )
    payload["gallery"] = _gallery(ctx.session, entry)
    payload["spec"] = (
        svc.series_payload(spec, entry) if spec is not None else None
    )
    faqs, _total = repo.list_published(ctx.session, kind="faq", limit=8)
    payload["faqs"] = [_with_cover(ctx.session, row) for row in faqs]
    return payload


@public_router.get("/content/{kind}", response_model=ContentPage)
def list_content(
    kind: str,
    ctx: ContextDep,
    pagination: Annotated[PaginationQuery, Query()] = PaginationQuery(),
    species: str | None = None,
    work_type: str | None = None,
    series: str | None = None,
    q: str | None = None,
    featured: bool = False,
) -> ContentPage:
    _validate_kind(kind)
    rows, total = repo.list_published(
        ctx.session,
        kind=kind,
        species=species,
        work_type=work_type,
        series=series,
        query=q,
        featured_only=featured,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return _page(
        [_with_cover(ctx.session, row) for row in rows], total, pagination.page, pagination.limit
    )


@public_router.get("/content/{kind}/{slug}", response_model=ContentEntryDetail)
def get_content(kind: str, slug: str, ctx: ContextDep) -> ContentEntryDetail:
    _validate_kind(kind)
    entry = repo.get_published(ctx.session, kind=kind, slug=slug)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.entry_payload(entry, include_body=True)
    payload["cover"] = (
        public_media_payload(ctx.session, entry.cover_asset_id) if entry.cover_asset_id else None
    )
    payload["gallery"] = _gallery(ctx.session, entry)
    return ContentEntryDetail(**payload)


def _validate_kind(kind: str) -> None:
    if kind not in CONTENT_KINDS:
        raise AppError("RESOURCE_NOT_FOUND")


# ------------------------------------------------------------------ 公开展示授权（C18）
@public_router.get("/me/display-authorizations", response_model=DisplayAuthorizationPage)
def list_my_display_authorizations(ctx: ContextDep, user_id: CustomerIdDep) -> DisplayAuthorizationPage:
    """我的公开展示授权：作品与本人公开素材的当前展示状态与撤回入口。"""
    items = [DisplayAuthorizationItem(**item) for item in svc.list_display_authorizations(ctx.session, user_id=user_id)]
    return DisplayAuthorizationPage(items=items, total=len(items))


@public_router.post("/me/display-authorizations/retract", response_model=DisplayRetractResponse)
def retract_my_display_authorization(
    payload: DisplayRetractInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    user_id: CustomerIdDep,
    request: Request,
) -> DisplayRetractResponse:
    """撤回公开展示授权：立即生效（公开页面与直链不再展示），并登记缓存失效任务。"""
    enforce_write_guard(request)
    result = svc.retract_display_authorization(
        ctx.session,
        user_id=user_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        reason=payload.reason,
    )
    ctx.session.commit()
    return DisplayRetractResponse(**result)


# ------------------------------------------------------------------ 站点聚合
@public_router.get("/site/home", response_model=dict)
def home_aggregate(ctx: ContextDep) -> dict[str, Any]:
    """首页聚合。

    信息顺序固定（11 文档页面布局基线）：导航与接单状态 → 代表作品/品牌短句 →
    系列 → 委托步骤 → 商品/角色 → 公告 → 联系与经营信息。一次请求渲染首屏以下
    全部区块，首屏内容不为等待图片或全部请求而被遮罩阻挡。
    """
    session = ctx.session
    works, work_total = repo.list_published(session, kind="work", limit=6, featured_only=True)
    if not works:
        works, work_total = repo.list_published(session, kind="work", limit=6)
    news, news_total = repo.list_published(session, kind="news", limit=4)
    series_rows = repo.list_series(session, active_only=True)
    faq_rows, _faq_total = repo.list_published(session, kind="faq", limit=6)

    from app.modules.catalog import ports as catalog_ports
    
    products, product_total = catalog_ports.list_products(session, limit=4)
    product_items: list[dict[str, Any]] = []
    for product in products:
        item = catalog_ports.product_payload(session, product, include_skus=False)
        skus = catalog_ports.list_skus(session, product_id=product.id, active_only=True)
        prices = [sku.price_cents for sku in skus]
        item["price_from_cents"] = min(prices) if prices else None
        item["currency"] = skus[0].currency if skus else "CNY"
        item.pop("skus", None)
        product_items.append(item)

    try:
        from app.modules.commissions import ports as commission_ports

        schedule_payload: dict[str, Any] | None = commission_ports.public_schedule_summary(session)
    except Exception:  # noqa: BLE001 - 档期数据缺失不阻塞首页渲染
        schedule_payload = None

    from app.modules.media.ports import public_media_payload as media_payload

    brand = svc.setting_value(session, "brand.profile", {}) or {}
    return {
        # 面向用户的文案同时给出简中与美式英文（C02/C24）：客户端按当前语言解析，
        # 服务端不按请求语言裁剪，切换语言不需要重新取配置。
        "hero": {
            "brand_name": brand.get("brand_name", "弈星纪元兽装工作室"),
            "tagline": {"zh-CN": "绒里有星，装配成纪", "en-US": "Stars in the fur, eras in the making"},
            "statement": {
                "zh-CN": "从设定到实装，一步一步做成可以穿在身上的角色。",
                "en-US": "From reference sheet to finished suit, one step at a time — a character you can wear.",
            },
            "primary_action": {
                "label": {"zh-CN": "查看委托指南", "en-US": "Read the commission guide"},
                "to": "/commission/guide",
            },
            "secondary_action": {
                "label": {"zh-CN": "浏览作品集", "en-US": "Browse the works"},
                "to": "/works",
            },
        },
        "schedule": schedule_payload,
        "featured_works": {
            "items": [_with_cover(session, row) for row in works],
            "total": work_total,
            "more": "/works",
        },
        "series": [
            {
                **svc.series_payload(spec, entry),
                "cover": media_payload(session, entry.cover_asset_id) if entry.cover_asset_id else None,
            }
            for spec, entry in series_rows[:4]
        ],
        "commission_steps": svc.commission_steps(
            svc.commission_policy(session), svc.loyalty_policy(session)
        ),
        "products": {"items": product_items, "total": product_total, "more": "/shop"},
        "news": {
            "items": [_with_cover(session, row) for row in news],
            "total": news_total,
            "more": "/news",
        },
        "faq": [_with_cover(session, row, include_body=True) for row in faq_rows],
        "settings": svc.public_settings(session),
        "features": svc.feature_flags(session),
    }


@public_router.get("/site/sitemap", response_model=dict)
def sitemap(ctx: ContextDep, request: Request) -> dict[str, Any]:
    """sitemap 只含已发布公共 URL，不含用户中心、付款、后台与未公开报价。"""
    settings = get_settings()
    origin = (request.headers.get("origin") or settings.public_origin).rstrip("/")
    return {"urls": svc.sitemap_urls(ctx.session, origin=origin)}


# ------------------------------------------------------------------ 后台
@admin_router.get("/content", response_model=ContentPage, dependencies=[Depends(require_permission("content.read"))])
def admin_list_content(
    ctx: ContextDep,
    kind: str | None = None,
    status: str | None = None,
    locale: str | None = None,
    q: str | None = None,
    pagination: Annotated[PaginationQuery, Query()] = PaginationQuery(),
) -> ContentPage:
    rows, total = repo.list_admin(
        ctx.session,
        kind=kind,
        status=status,
        locale=locale,
        query=q,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    items = []
    for row in rows:
        payload = svc.entry_payload(row, include_body=False)
        payload["status"] = row.status
        payload["version"] = row.version
        payload["revision"] = row.revision
        items.append(ContentEntrySummary(**payload))
    return ContentPage(
        items=items,
        total=total,
        page=pagination.page,
        limit=pagination.limit,
        has_more=pagination.page * pagination.limit < total,
        next_cursor=None,
    )


@admin_router.get(
    "/content/{entry_id}", response_model=dict, dependencies=[Depends(require_permission("content.read"))]
)
def admin_get_content(entry_id: str, ctx: ContextDep) -> dict[str, Any]:
    entry = repo.get_entry(ctx.session, entry_id)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    payload = svc.entry_payload(entry, include_body=True)
    payload.update(
        {
            "status": entry.status,
            "version": entry.version,
            "revision": entry.revision,
            "publish_at": iso(entry.publish_at),
            "unpublish_at": iso(entry.unpublish_at),
            "sanitized_body": entry.sanitized_body,
            "display_authorized": bool(entry.display_authorized),
            "client_user_id": entry.client_user_id,
            "revisions": [
                {
                    "revision": row.revision,
                    "title": row.title,
                    "editor_label": row.editor_label,
                    "change_note": row.change_note,
                    "published_at": iso(row.published_at),
                    "created_at": iso(row.created_at),
                }
                for row in repo.list_revisions(ctx.session, entry_id=entry.id)
            ],
        }
    )
    return payload


@admin_router.post(
    "/content",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_permission("content.write"))],
)
def admin_create_content(
    payload: ContentCreateInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    entry = svc.create_entry(
        ctx.session,
        kind=payload.kind,
        slug=payload.slug,
        locale=payload.locale,
        title=payload.title,
        summary=payload.summary,
        body=payload.body_html,
        actor_id=principal.id,
        tags=payload.tags,
        cover_asset_id=payload.cover_asset_id,
        seo_title=payload.seo_title,
        seo_description=payload.seo_description,
        species=payload.species,
        work_type=payload.work_type,
        craft_notes=payload.craft_notes,
        designer_credit=payload.designer_credit,
        maker_credit=payload.maker_credit,
        photographer_credit=payload.photographer_credit,
        display_authorized=1 if payload.display_authorized else 0,
        client_display_name=payload.client_display_name,
        client_user_id=payload.client_user_id,
        sort_order=payload.sort_order,
        is_featured=1 if payload.is_featured else 0,
    )
    return {"id": entry.id, "slug": entry.slug, "status": entry.status, "version": entry.version}


@admin_router.patch(
    "/content/{entry_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("content.write"))],
)
def admin_update_content(
    entry_id: str,
    payload: ContentUpdateInput,
    ctx: ContextDep,
    _csrf: CsrfDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    entry = svc.update_entry(
        ctx.session,
        entry_id=entry_id,
        version=payload.version,
        actor_id=principal.id,
        body=payload.body_html,
        change_note=payload.change_note,
        **{
            name: getattr(payload, name)
            for name in (
                "title",
                "summary",
                "locale",
                "cover_asset_id",
                "tags",
                "seo_title",
                "seo_description",
                "sort_order",
                "species",
                "work_type",
                "craft_notes",
                "designer_credit",
                "maker_credit",
                "photographer_credit",
                "client_display_name",
                "client_user_id",
            )
            if getattr(payload, name) is not None
        },
        **(
            {"display_authorized": 1 if payload.display_authorized else 0}
            if payload.display_authorized is not None
            else {}
        ),
        **({"is_featured": 1 if payload.is_featured else 0} if payload.is_featured is not None else {}),
    )
    return {"id": entry.id, "status": entry.status, "version": entry.version, "revision": entry.revision}


@admin_router.post(
    "/content/{entry_id}/publish",
    response_model=dict,
    dependencies=[Depends(require_permission("content.publish"))],
)
def admin_publish_content(
    entry_id: str, payload: PublishInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    entry = svc.publish_entry(
        ctx.session,
        entry_id=entry_id,
        version=payload.version,
        actor_id=principal.id,
        change_note=payload.change_note,
    )
    return {"id": entry.id, "status": entry.status, "revision": entry.revision, "route": svc.public_route_for(entry)}


@admin_router.post(
    "/content/{entry_id}/archive",
    response_model=dict,
    dependencies=[Depends(require_permission("content.publish"))],
)
def admin_archive_content(
    entry_id: str, payload: PublishInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    entry = svc.archive_entry(
        ctx.session,
        entry_id=entry_id,
        version=payload.version,
        actor_id=principal.id,
        reason=payload.change_note or "运营下架",
    )
    return {"id": entry.id, "status": entry.status}


@admin_router.post(
    "/series-specs",
    response_model=dict,
    dependencies=[Depends(require_permission("content.write"))],
)
def admin_upsert_series_spec(
    payload: SeriesSpecInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    spec = svc.upsert_series_spec(
        ctx.session, payload=payload.model_dump(exclude_none=True), actor_id=principal.id
    )
    return {"id": spec.id, "code": spec.code, "version": spec.version}


@admin_router.get(
    "/settings", response_model=list, dependencies=[Depends(require_permission("settings.manage"))]
)
def admin_list_settings(ctx: ContextDep, group: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "key": row.key,
            "group": row.group,
            "label": row.label,
            "description": row.description,
            "is_public": bool(row.is_public),
            "value": row.value,
            "version": row.version,
            "updated_at": iso(row.updated_at),
        }
        for row in repo.list_settings(ctx.session, group=group)
    ]


@admin_router.patch(
    "/settings/{key}", response_model=dict, dependencies=[Depends(require_permission("settings.manage"))]
)
def admin_update_setting(
    key: str, payload: SettingUpdateInput, ctx: ContextDep, _csrf: CsrfDep, principal: PrincipalDep
) -> dict[str, Any]:
    row = svc.update_setting(
        ctx.session,
        key=key,
        value=payload.value,
        version=payload.version,
        actor_id=principal.id,
        reason=payload.reason,
    )
    return {"key": row.key, "version": row.version, "value": row.value}


@admin_router.get(
    "/prebuild-jobs", response_model=list, dependencies=[Depends(require_permission("ops.read"))]
)
def admin_prebuild_jobs(ctx: ContextDep, limit: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "trigger": row.trigger,
            "status": row.status,
            "routes": row.routes or [],
            "error": row.error,
            "created_at": iso(row.created_at),
            "finished_at": iso(row.finished_at),
        }
        for row in repo.list_prebuild_jobs(ctx.session, limit=limit)
    ]


@public_router.get("/content/slug-available", response_model=dict)
def slug_available(slug: Annotated[str, Query(min_length=1, max_length=120)], kind: str, ctx: ContextDep) -> dict[str, Any]:
    """后台编辑辅助：slug 是否可用。公开只读、不泄露草稿内容。"""
    _ = SlugQuery(slug=slug)
    _validate_kind(kind)
    existing = repo.get_published(ctx.session, kind=kind, slug=slug)
    taken = existing is not None
    if not taken:
        rows, _total = repo.list_admin(ctx.session, kind=kind, query=slug, limit=5)
        taken = any(row.slug == slug for row in rows)
    return {"slug": slug, "available": not taken}


__all__ = ["public_router", "admin_router"]
