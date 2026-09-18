"""内容模块用例：草稿/预览/发布、版本快照、系列规格、站点设置。

规则：草稿不公开；发布产生不可变版本快照与受控预渲染任务；
下架不删除历史版本（FR-001–003）。
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.content import repository as repo
from app.modules.content.models import (
    CONTENT_KINDS,
    ContentEntry,
    ContentRevision,
    SeriesSpec,
    SiteSetting,
)
from app.modules.content.sanitize import excerpt, sanitize_html
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms
from app.platform.reliability import enqueue

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not SLUG_RE.match(value):
        raise AppError(
            "VALIDATION_ERROR",
            "slug 只能使用小写字母、数字与连字符。",
            fields={"slug": "格式不正确"},
        )
    return value


def create_entry(
    session: Session,
    *,
    kind: str,
    slug: str,
    locale: str,
    title: str,
    summary: str,
    body: str,
    actor_id: str | None,
    **extra: Any,
) -> ContentEntry:
    if kind not in CONTENT_KINDS:
        raise AppError("VALIDATION_ERROR", f"未知内容类型：{kind}", fields={"kind": kind})
    entry = ContentEntry(
        id=new_id("content"),
        kind=kind,
        slug=_validate_slug(slug),
        locale=locale,
        title=title,
        summary=summary or excerpt(body, 200),
        sanitized_body=sanitize_html(body),
        status="draft",
        created_by=actor_id,
        **extra,
    )
    session.add(entry)
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="content.create",
        resource_type="content_entry",
        resource_id=entry.id,
        after={"kind": kind, "slug": entry.slug, "status": entry.status},
    )
    return entry


EDITABLE_FIELDS = (
    "title",
    "summary",
    "locale",
    "cover_asset_id",
    "tags",
    "seo_title",
    "seo_description",
    "sort_order",
    "is_featured",
    "species",
    "work_type",
    "craft_notes",
    "designer_credit",
    "maker_credit",
    "photographer_credit",
    "display_authorized",
    "client_display_name",
    "publish_at",
    "unpublish_at",
)


def update_entry(
    session: Session,
    *,
    entry_id: str,
    version: int,
    actor_id: str | None,
    body: str | None = None,
    change_note: str | None = None,
    **fields: Any,
) -> ContentEntry:
    entry = repo.get_entry(session, entry_id)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if entry.version != version:
        raise AppError("VERSION_CONFLICT")
    before = {"title": entry.title, "status": entry.status, "revision": entry.revision}
    for name in EDITABLE_FIELDS:
        if name in fields and fields[name] is not None:
            setattr(entry, name, fields[name])
    if body is not None:
        entry.sanitized_body = sanitize_html(body)
        if not entry.summary:
            entry.summary = excerpt(body, 200)
    entry.version += 1
    entry.updated_at = now_ms()

    # 已发布内容再次编辑产生新版本快照；草稿阶段只保留最新内容。
    if entry.status == "published":
        entry.revision += 1
        _snapshot(session, entry=entry, actor_id=actor_id, change_note=change_note, published=True)
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="content.update",
        resource_type="content_entry",
        resource_id=entry.id,
        before=before,
        after={"title": entry.title, "status": entry.status, "revision": entry.revision},
        reason=change_note,
    )
    return entry


def _snapshot(
    session: Session,
    *,
    entry: ContentEntry,
    actor_id: str | None,
    change_note: str | None,
    published: bool,
) -> ContentRevision:
    revision = ContentRevision(
        id=new_id("content"),
        entry_id=entry.id,
        revision=entry.revision,
        title=entry.title,
        body_snapshot=entry.sanitized_body,
        editor_id=actor_id,
        change_note=change_note,
        published_at=now_ms() if published else None,
    )
    session.add(revision)
    return revision


def publish_entry(
    session: Session, *, entry_id: str, version: int, actor_id: str | None, change_note: str | None = None
) -> ContentEntry:
    """发布：写版本快照、切换状态、登记预渲染任务（同一事务）。"""
    entry = repo.get_entry(session, entry_id)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if entry.version != version:
        raise AppError("VERSION_CONFLICT")
    if entry.kind == "work" and not entry.display_authorized:
        # 无公开授权的委托作品不出现（FR-002）。
        raise AppError(
            "STATE_CONFLICT",
            "该作品尚未取得公开展示授权，不能发布。请先在客户授权中登记。",
        )
    before = {"status": entry.status, "revision": entry.revision}
    if entry.status != "published":
        entry.revision += 1
    entry.status = "published"
    entry.publish_at = entry.publish_at or now_ms()
    entry.unpublish_at = None
    entry.version += 1
    entry.updated_at = now_ms()
    _snapshot(session, entry=entry, actor_id=actor_id, change_note=change_note, published=True)

    job = repo.enqueue_prebuild(
        session,
        trigger="content.publish",
        resource_type="content_entry",
        resource_id=entry.id,
        routes=[public_route_for(entry)],
        actor_id=actor_id,
    )
    enqueue(
        session,
        event_type="content.prebuild_requested",
        aggregate_id=entry.id,
        payload={"job_id": job.id, "slug": entry.slug, "kind": entry.kind},
        aggregate_version=entry.revision,
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="content.publish",
        resource_type="content_entry",
        resource_id=entry.id,
        before=before,
        after={"status": entry.status, "revision": entry.revision},
        reason=change_note,
    )
    session.flush()
    return entry


def archive_entry(
    session: Session, *, entry_id: str, version: int, actor_id: str | None, reason: str
) -> ContentEntry:
    entry = repo.get_entry(session, entry_id)
    if entry is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if entry.version != version:
        raise AppError("VERSION_CONFLICT")
    before = {"status": entry.status}
    entry.status = "archived"
    entry.unpublish_at = now_ms()
    entry.version += 1
    entry.updated_at = now_ms()
    repo.enqueue_prebuild(
        session,
        trigger="content.archive",
        resource_type="content_entry",
        resource_id=entry.id,
        routes=[public_route_for(entry)],
        actor_id=actor_id,
    )
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="content.archive",
        resource_type="content_entry",
        resource_id=entry.id,
        before=before,
        after={"status": entry.status},
        reason=reason,
    )
    session.flush()
    return entry


def upsert_series_spec(session: Session, *, payload: dict[str, Any], actor_id: str | None) -> SeriesSpec:
    entry = repo.get_entry(session, payload["entry_id"])
    if entry is None or entry.kind != "series":
        raise AppError("VALIDATION_ERROR", "系列规格必须绑定 series 类型的内容条目。")
    existing = repo.get_series_for_entry(session, entry.id)
    if existing is not None:
        if payload.get("version") is not None and payload["version"] != existing.version:
            raise AppError("VERSION_CONFLICT")
        for name in (
            "base_price_cents",
            "currency",
            "price_includes",
            "price_excludes",
            "lead_time_note",
            "spec_schema",
            "display_order",
            "is_active",
        ):
            if name in payload and payload[name] is not None:
                setattr(existing, name, payload[name])
        existing.version += 1
        existing.updated_at = now_ms()
        session.flush()
        return existing

    spec = SeriesSpec(
        id=new_id("content"),
        entry_id=entry.id,
        code=payload["code"],
        base_price_cents=payload.get("base_price_cents", 0),
        currency=payload.get("currency", "CNY"),
        price_includes=payload.get("price_includes"),
        price_excludes=payload.get("price_excludes"),
        lead_time_note=payload.get("lead_time_note"),
        spec_schema=payload.get("spec_schema"),
        display_order=payload.get("display_order", 0),
        is_active=1 if payload.get("is_active", True) else 0,
    )
    session.add(spec)
    session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="series.upsert",
        resource_type="series_spec",
        resource_id=spec.id,
        after={"code": spec.code, "base_price_cents": spec.base_price_cents},
    )
    return spec


# ------------------------------------------------------------------ 站点设置
DEFAULT_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "brand.profile",
        "group": "brand",
        "label": "品牌与经营信息",
        "description": "品牌名、经营主体、域名、客服邮箱与工作时段；用于页脚与合同抬头。",
        "is_public": 1,
        "value": {
            "brand_name": "弈星纪元兽装工作室",
            "legal_entity": "弈星纪元（上海）文化发展有限公司",
            "domain": "starium.cn",
            "support_email": "support@starium.cn",
            "service_hours": "周一至周五 10:00–19:00（Asia/Shanghai），节假日另行说明",
            "response_sla": {"inquiry_workdays": 3, "feedback_workdays": 7},
        },
    },
    {
        "key": "brand.social_links",
        "group": "brand",
        "label": "社交渠道",
        "description": "C20：社交链接留在配置文件中，仅允许安全协议外链。",
        "is_public": 1,
        "value": [
            {"code": "douyin", "label": "抖音", "url": ""},
            {"code": "xiaohongshu", "label": "小红书", "url": ""},
            {"code": "bilibili", "label": "B站", "url": ""},
            {"code": "qq_group", "label": "QQ 群", "url": ""},
            {"code": "wechat", "label": "微信", "url": ""},
            {"code": "x", "label": "X（Twitter）", "url": ""},
        ],
    },
    {
        "key": "commission.policy",
        "group": "commission",
        "label": "委托规则默认值",
        "description": "C06/C08/C09：预付款比例、报价有效期、名额预留时长、尾款期限、免费修改次数。",
        "is_public": 1,
        "value": {
            "prepayment_percent": 50,
            "quote_valid_days": 7,
            "slot_hold_minutes": 30,
            "balance_due_days": 7,
            "free_revision_count": 2,
            "terms_version": "commission-1",
        },
    },
    {
        "key": "loyalty.rules",
        "group": "loyalty",
        "label": "星绒币规则",
        "description": "C13/C14/C15：汇率、发放延迟、反馈奖励分级与兑换参数。",
        "is_public": 1,
        "value": {
            "points_name": "星绒币",
            "denominator_yuan": 5,
            "release_delay_days": 7,
            "shipping_excluded": True,
            "feedback_rewards": {"minor": 5, "normal": 20, "major": 50},
            "feedback_daily_reward_limit": 1,
            "redemption_enabled": False,
            "redemption_ratio_note": "兑换率与上限在启用前定义（N02/N04）",
            # 规则值全部为 0 = 未配置：启用兑换前必须由运营填好，服务端不预置业务数值。
            "redemption_points_per_yuan": 0,
            "redemption_min_points": 0,
            "redemption_max_ratio_percent": 0,
            "redemption_expiry_days": 0,
            "redemption_restore_on_refund": True,
        },
    },
    {
        "key": "commerce.policy",
        "group": "commerce",
        "label": "商城与售后规则",
        "description": "C10/C16/C17：退换货、跨境、运费与开票说明。",
        "is_public": 1,
        "value": {
            "returns_reference": "参照淘宝 / 闲鱼平台规则执行",
            "cross_border_note": "跨境订单的物流、关税与偏远地区附加费单独说明。",
            "invoice_enabled": True,
            "shipping_template_note": "运费模板由工作室配置，结算时以服务端计算为准。",
        },
    },
    {
        "key": "platform.feature_flags",
        "group": "platform",
        "label": "功能开关",
        "description": "商业门槛未落实时保持关闭；每次变更需审计。",
        "is_public": 1,
        "value": {
            "online_payment": False,
            "points_redemption": False,
            "coupons": False,
            "rush_rounds": False,
            "client_stories": False,
            "events": False,
            "en_locale": False,
            "marketing_email": False,
        },
    },
)


def ensure_default_settings(session: Session) -> int:
    """幂等写入默认设置。已存在的键不覆盖，保证运营改动不被重置。"""
    created = 0
    for item in DEFAULT_SETTINGS:
        if repo.get_setting(session, item["key"]) is None:
            session.add(
                SiteSetting(
                    id=new_id("job"),
                    key=item["key"],
                    group=item["group"],
                    label=item["label"],
                    description=item["description"],
                    is_public=item["is_public"],
                    value=item["value"],
                )
            )
            created += 1
    session.flush()
    return created


def update_setting(
    session: Session, *, key: str, value: Any, version: int, actor_id: str | None, reason: str | None
) -> SiteSetting:
    row = repo.get_setting(session, key)
    if row is None:
        raise AppError("RESOURCE_NOT_FOUND")
    if row.version != version:
        raise AppError("VERSION_CONFLICT")
    from app.modules.content.policies import validate_setting

    before = row.value or {}
    merged = {**before, **value} if isinstance(value, dict) and isinstance(before, dict) else value
    merged = validate_setting(key, merged)
    row.value = merged
    row.version += 1
    row.updated_by = actor_id
    row.updated_at = now_ms()
    # 功能开关变化必须留痕，且不能让新交易绕过账本。
    feature_keys = {"online_payment", "points_redemption", "coupons", "rush_rounds", "marketing_email"}
    changed_features = {
        name: merged.get(name) for name in feature_keys
        if key == "platform.feature_flags" and before.get(name) != merged.get(name)
    }
    audit.record(
        session,
        actor_type="staff",
        actor_id=actor_id,
        action="settings.update",
        resource_type="site_setting",
        resource_id=row.key,
        before=before,
        after=merged,
        reason=reason,
    )
    if changed_features:
        enqueue(
            session,
            event_type="settings.feature_flags_changed",
            aggregate_id=row.key,
            payload={"changes": changed_features},
            aggregate_version=row.version,
        )
    session.flush()
    return row


# ------------------------------------------------------------------ 序列化
def public_route_for(entry: ContentEntry) -> str:
    if entry.kind == "work":
        return f"/works/{entry.slug}"
    if entry.kind == "news":
        return f"/news/{entry.slug}"
    if entry.kind == "series":
        return f"/fursuits/{entry.slug}"
    if entry.kind == "legal":
        return f"/legal/{entry.slug}"
    if entry.kind == "story":
        return f"/stories/{entry.slug}"
    if entry.kind == "designer":
        return f"/designers/{entry.slug}"
    if entry.kind == "event":
        return f"/events/{entry.slug}"
    return f"/pages/{entry.slug}"


def entry_payload(entry: ContentEntry, *, include_body: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": entry.id,
        "kind": entry.kind,
        "slug": entry.slug,
        "locale": entry.locale,
        "title": entry.title,
        "summary": entry.summary,
        "route": public_route_for(entry),
        "cover_asset_id": entry.cover_asset_id,
        "tags": entry.tags or [],
        "species": entry.species,
        "work_type": entry.work_type,
        "published_at": iso(entry.publish_at),
        "updated_at": iso(entry.updated_at),
        "is_featured": bool(entry.is_featured),
        "seo": {
            "title": entry.seo_title or entry.title,
            "description": entry.seo_description or entry.summary,
            "canonical": public_route_for(entry),
        },
    }
    if entry.kind == "work":
        payload["credits"] = {
            "designer": entry.designer_credit,
            "maker": entry.maker_credit,
            "photographer": entry.photographer_credit,
            "client_display_name": entry.client_display_name,
        }
        payload["craft_notes"] = entry.craft_notes
    if include_body:
        payload["body_html"] = entry.sanitized_body
    return payload


def series_payload(spec: SeriesSpec, entry: ContentEntry) -> dict[str, Any]:
    return {
        **entry_payload(entry),
        "code": spec.code,
        "base_price_cents": spec.base_price_cents,
        "currency": spec.currency,
        "price_includes": spec.price_includes or [],
        "price_excludes": spec.price_excludes or [],
        "lead_time_note": spec.lead_time_note,
        "spec_schema": spec.spec_schema or {},
        "display_order": spec.display_order,
        "is_active": bool(spec.is_active),
    }


def public_settings(session: Session) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in repo.list_settings(session, public_only=True):
        result[row.key] = row.value
    return result


def setting_value(session: Session, key: str, default: Any = None) -> Any:
    row = repo.get_setting(session, key)
    if row is None:
        return default
    value = row.value or {}
    return value if value else default


def commission_policy(session: Session) -> dict[str, Any]:
    """委托规则：后台配置优先，缺失时回落到配置基线（C06/C08/C09）。"""
    from app.platform.config import get_settings

    settings = get_settings()
    stored = setting_value(session, "commission.policy", {}) or {}
    return {
        "prepayment_percent": int(stored.get("prepayment_percent", settings.default_prepayment_percent)),
        "quote_valid_days": int(stored.get("quote_valid_days", settings.quote_valid_days)),
        "slot_hold_minutes": int(stored.get("slot_hold_minutes", settings.slot_hold_minutes)),
        "balance_due_days": int(stored.get("balance_due_days", settings.balance_due_days)),
        "free_revision_count": int(stored.get("free_revision_count", settings.free_revision_count)),
        "terms_version": str(stored.get("terms_version", "commission-1")),
    }


def loyalty_policy(session: Session) -> dict[str, Any]:
    from app.platform.config import get_settings

    settings = get_settings()
    stored = setting_value(session, "loyalty.rules", {}) or {}
    return {
        "points_name": stored.get("points_name", "星绒币"),
        "denominator_yuan": int(stored.get("denominator_yuan", settings.points_per_currency_unit)),
        "release_delay_days": int(stored.get("release_delay_days", settings.points_release_delay_days)),
        "shipping_excluded": bool(stored.get("shipping_excluded", True)),
        "feedback_rewards": stored.get(
            "feedback_rewards",
            {
                "minor": settings.feedback_reward_minor,
                "normal": settings.feedback_reward_normal,
                "major": settings.feedback_reward_major,
            },
        ),
        "feedback_daily_reward_limit": int(
            stored.get("feedback_daily_reward_limit", settings.feedback_daily_reward_limit)
        ),
        "redemption_enabled": bool(stored.get("redemption_enabled", False)),
        # 兑换规则（N02/N04）：未配置时如实返回 0 / 默认值，由积分模块决定是否可兑换。
        # 这里必须逐项转发——漏掉任何一项都会让「已配置」在服务端看起来仍是未配置。
        "redemption_ratio_note": str(stored.get("redemption_ratio_note", "")),
        "redemption_points_per_yuan": int(stored.get("redemption_points_per_yuan", 0)),
        "redemption_min_points": int(stored.get("redemption_min_points", 0)),
        "redemption_max_ratio_percent": int(stored.get("redemption_max_ratio_percent", 0)),
        "redemption_expiry_days": int(stored.get("redemption_expiry_days", 0)),
        "redemption_restore_on_refund": bool(stored.get("redemption_restore_on_refund", True)),
    }


def feature_flags(session: Session) -> dict[str, bool]:
    from app.platform.config import get_settings

    settings = get_settings()
    stored = setting_value(session, "platform.feature_flags", {}) or {}
    defaults = {
        "online_payment": settings.feature_online_payment,
        "points_redemption": settings.feature_points_redemption,
        "coupons": settings.feature_coupons,
        "rush_rounds": settings.feature_rush_rounds,
        "client_stories": settings.feature_client_stories,
        "events": settings.feature_events,
        "en_locale": settings.feature_en_locale,
        "marketing_email": settings.feature_marketing_email,
    }
    return {name: bool(stored.get(name, default)) for name, default in defaults.items()}


def require_feature(session: Session, name: str) -> None:
    """未启用的功能入口隐藏且后端拒绝（13 文档）。"""
    if not feature_flags(session).get(name, False):
        raise AppError("FEATURE_DISABLED", "该功能尚未开放。")


def commission_steps(policy: dict[str, Any], loyalty: dict[str, Any]) -> list[dict[str, str]]:
    """委托步骤文案由服务端生成，数值取自后台配置，避免前端硬编码业务规则。

    信息顺序对应 11 文档的首页布局基线与 07 文档的流程分段：
    询价（需求沟通）→ 报价（有期限的商业提案）→ 条款与预付款（合同留证）→ 制作与交付。
    """
    return [
        {
            "step": "01",
            "title": "提交询价",
            "description": "填写角色、物种、系列与制作要求，上传 1–3 张设定图。资料不足时我们会说明需要补什么。",
        },
        {
            "step": "02",
            "title": "收到报价",
            "description": (
                f"工作室按可制造性发布有版本的报价，有效期 {policy['quote_valid_days']} 天，"
                "明细、包含项与例外都会列出。"
            ),
        },
        {
            "step": "03",
            "title": "确认条款并付预付款",
            "description": (
                f"确认报价后记录条款版本与同意时间，支付预付款 {policy['prepayment_percent']}%，"
                f"名额预留 {policy['slot_hold_minutes']} 分钟用于付款。"
            ),
        },
        {
            "step": "04",
            "title": "跟踪制作并收货",
            "description": (
                f"每个制作阶段可查看图片并确认，免费修改 {policy['free_revision_count']} 次；"
                f"交付后 {loyalty['release_delay_days']} 天发放星绒币。"
            ),
        },
    ]


def sitemap_urls(session: Session, *, origin: str) -> list[dict[str, str]]:
    """sitemap 只含已发布公共 URL，不含用户中心、付款、后台与未公开报价。"""
    static_paths = [
        "/",
        "/about",
        "/contact",
        "/news",
        "/works",
        "/fursuits",
        "/commission/guide",
        "/commission/schedules",
        "/commission/contract",
        "/characters",
        "/shop",
        "/faq",
        "/care",
        "/legal/terms",
        "/legal/privacy",
        "/legal/returns",
        "/legal/points",
    ]
    entries = [
        {"loc": f"{origin}{path}", "changefreq": "weekly", "priority": "0.7"}
        for path in static_paths
    ]
    for entry in repo.list_sitemap_entries(session):
        entries.append(
            {
                "loc": f"{origin}{public_route_for(entry)}",
                "lastmod": (iso(entry.updated_at) or "")[:10],
                "changefreq": "monthly",
                "priority": "0.6",
            }
        )
    return entries


def admin_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        sa.select(ContentEntry.kind, sa.func.count()).group_by(ContentEntry.kind)
    ).all()
    return {str(kind): int(count) for kind, count in rows}
