"""内容模块用例：草稿/预览/发布、版本快照、系列规格、站点设置。

规则：草稿不公开；发布产生不可变版本快照与受控预渲染任务；
下架不删除历史版本（FR-001–003）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from app.modules.identity import ports as identity_ports
from app.modules.media import ports as media_ports
from app.platform import audit
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms
from app.platform.reliability import enqueue

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: 首发语言（C02/C24）。服务端**不按请求语言裁剪**：面向用户的运营文案以「值自带语言」
#: 的形式下发（`{"zh-CN": …, "en-US": …}`），由客户端按当前语言解析——这样切换语言不需要
#: 重新取配置，也不存在两份设置漂移。服务端自己需要一句话时用 `localized_text()`。
DEFAULT_LOCALE = "zh-CN"
SUPPORTED_LOCALES = ("zh-CN", "en-US")


def localized_text(value: Any, locale: str = DEFAULT_LOCALE) -> str:
    """把「一句话或按语言给出的文案」解析成一句话。

    - 纯字符串：所有语言都用它（老配置天然兼容）；
    - 字典：取目标语言，缺失时回退到简中；都没有就返回空串（**不编造文案**）。
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for candidate in (locale, DEFAULT_LOCALE, *SUPPORTED_LOCALES):
            text = value.get(candidate)
            if isinstance(text, str) and text.strip():
                return text
    return ""


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
            # 工作时段是面向客户的运营文案：按语言分别给出（英文站需要英文说明）。
            "service_hours": {
                "zh-CN": "周一至周五 10:00–19:00（Asia/Shanghai），节假日另行说明",
                "en-US": "Mon–Fri 10:00–19:00 (Asia/Shanghai); holidays are announced separately",
            },
            "response_sla": {"inquiry_workdays": 3, "feedback_workdays": 7},
        },
    },
    {
        "key": "support.calendar",
        "group": "support",
        "label": "客服工作日与节假日",
        "description": "C30：询价 3 个工作日、反馈 7 个工作日首响；节假日与调休由这里配置，"
        "逾期提醒按该日历计算。",
        "is_public": 1,
        "value": {
            "timezone": "Asia/Shanghai",
            # 周一=1 … 周日=7；默认周一至周五为工作日。
            "workdays": [1, 2, 3, 4, 5],
            # 法定节假日（YYYY-MM-DD）：即使落在工作日也不算工作日。
            "holidays": [],
            # 调休上班日（YYYY-MM-DD）：即使落在周末也算工作日。
            "extra_workdays": [],
            # N13 待补：节假日期间的服务时效说明文案（留空表示沿用服务时段说明）。
            "holiday_note": "",
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
            "returns_reference": {
                "zh-CN": "参照淘宝 / 闲鱼平台规则执行",
                "en-US": "Follows the marketplace rules of Taobao / Xianyu",
            },
            "cross_border_note": {
                "zh-CN": "跨境订单的物流、关税与偏远地区附加费单独说明。",
                "en-US": "Shipping, duties and remote-area surcharges for cross-border orders are quoted separately.",
            },
            "invoice_enabled": True,
            "shipping_template_note": {
                "zh-CN": "运费模板由工作室配置，结算时以服务端计算为准。",
                "en-US": "Shipping rates are configured by the studio; the server-computed amount is authoritative at checkout.",
            },
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
            # C18：客户姓名只在**仍持有公开展示授权**时出现在公开载荷里。
            # 撤回授权后即使条目还没重新发布，也不能再泄露客户身份。
            "client_display_name": (
                entry.client_display_name if entry.display_authorized else None
            ),
        }
        payload["craft_notes"] = entry.craft_notes
    if include_body:
        payload["body_html"] = entry.sanitized_body
    return payload


# ------------------------------------------------------------------ 公开展示授权（C18）
def list_display_authorizations(session: Session, *, user_id: str) -> list[dict[str, Any]]:
    """我的公开展示授权：列出租给本人账号的委托作品与本人上传且被设为公开的素材。

    只列事实，不替客户做判断：``visible_now`` 表示此刻公开页面是否真的会展示它。
    """
    items: list[dict[str, Any]] = []
    for entry in repo.list_entries_for_client(session, user_id=user_id):
        published = entry.status == "published"
        authorized = bool(entry.display_authorized)
        items.append(
            {
                "resource_type": "work",
                "resource_id": entry.id,
                "title": entry.title,
                "slug": entry.slug,
                "route": public_route_for(entry),
                "client_display_name": entry.client_display_name if authorized else None,
                "display_authorized": authorized,
                "published": published,
                "visible_now": published and authorized,
                "state": "ACTIVE" if (published and authorized) else ("RETRACTED" if not authorized else "PENDING"),
                "granted_at": iso(entry.created_at),
                "updated_at": iso(entry.updated_at),
            }
        )
    for asset in media_ports.public_assets_for_owner(session, owner_id=user_id):
        items.append(
            {
                "resource_type": "media",
                "resource_id": asset["id"],
                "title": asset["filename"],
                "slug": None,
                "route": None,
                "client_display_name": None,
                "display_authorized": True,
                "published": True,
                "visible_now": True,
                "state": "ACTIVE",
                "granted_at": iso(asset["created_at"]),
                "updated_at": iso(asset["updated_at"]),
            }
        )
    return items


def retract_display_authorization(
    session: Session,
    *,
    user_id: str,
    resource_type: str,
    resource_id: str,
    reason: str,
) -> dict[str, Any]:
    """撤回公开展示授权（C18：单独可撤回）。

    撤回**立即生效**：作品立即从公开列表与直链消失、客户姓名不再出现在公开载荷里；
    素材立即取消公开。授权留证保留（`consents` 里记一条已撤回的 `public_display`），
    并登记预渲染任务让缓存与静态产物同步失效。
    """
    if resource_type == "work":
        entry = repo.get_entry(session, resource_id)
        if entry is None or entry.kind != "work" or entry.client_user_id != user_id:
            # 非本人或不存在一律 404，不透露作品是否存在。
            raise AppError("RESOURCE_NOT_FOUND")
        already = not bool(entry.display_authorized)
        if not already:
            entry.display_authorized = 0
            entry.version += 1
            entry.updated_at = now_ms()
        route: str | None = public_route_for(entry)
    elif resource_type == "media":
        if not media_ports.retract_public_display(session, asset_id=resource_id, owner_id=user_id):
            raise AppError("RESOURCE_NOT_FOUND")
        entry = None
        already = False
        route = None
    else:
        raise AppError(
            "VALIDATION_ERROR",
            "不支持的展示资源类型。",
            fields={"resource_type": "只支持 work 或 media"},
        )

    _record_display_retraction(
        session,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
    )
    job_id: str | None = None
    if entry is not None:
        job = repo.enqueue_prebuild(
            session,
            trigger="content.display_retracted",
            resource_type="content_entry",
            resource_id=entry.id,
            routes=[route or public_route_for(entry)],
            actor_id=user_id,
        )
        job_id = job.id
        enqueue(
            session,
            event_type="content.prebuild_requested",
            aggregate_id=entry.id,
            payload={"job_id": job.id, "slug": entry.slug, "kind": entry.kind, "reason": "display_retracted"},
            aggregate_version=entry.revision,
        )
    audit.record(
        session,
        actor_type="customer",
        actor_id=user_id,
        action="display_authorization.retract",
        resource_type=resource_type,
        resource_id=resource_id,
        after={"display_authorized": False, "already_retracted": already, "prebuild_job_id": job_id},
        reason=reason,
    )
    session.flush()
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "display_authorized": False,
        "already_retracted": already,
        "prebuild_job_id": job_id,
        "effective": "immediate",
    }


def _record_display_retraction(
    session: Session, *, user_id: str, resource_type: str, resource_id: str, reason: str
) -> None:
    """留证：有生效中的授权就撤回它，否则补记一条已撤回的授权记录。

    两种情况下 `consents` 里都会存在一条 purpose=``public_display`` 且带
    ``revoked_at`` 的记录，客服与审计都能回答「客户什么时候撤回的、为什么」。
    """
    revoked = identity_ports.revoke_consent(
        session,
        user_id=user_id,
        purpose="public_display",
        resource_id=resource_id,
        reason=reason,
    )
    if revoked:
        return
    identity_ports.record_consent(
        session,
        user_id=user_id,
        purpose="public_display",
        version="retraction-1",
        resource_type=resource_type,
        resource_id=resource_id,
        evidence={"retracted_at": iso(now_ms()), "channel": "customer_center"},
    )
    identity_ports.revoke_consent(
        session,
        user_id=user_id,
        purpose="public_display",
        resource_id=resource_id,
        reason=reason,
    )


def support_calendar(session: Session) -> dict[str, Any]:
    """客服工作日与节假日（C30）。缺失时给出「周一至周五、无节假日」的安全基线。

    `holiday_note` 是面向客户的文案（N13 待运营填写）：这里**不猜语言**，把原始值原样带出，
    由调用方按语言解析（需要一句话时用 `localized_text()`）。
    """
    stored = setting_value(session, "support.calendar", {}) or {}
    workdays = sorted({int(day) for day in (stored.get("workdays") or [1, 2, 3, 4, 5])})
    return {
        "timezone": str(stored.get("timezone") or "Asia/Shanghai"),
        "workdays": workdays or [1, 2, 3, 4, 5],
        "holidays": sorted({str(day) for day in (stored.get("holidays") or [])}),
        "extra_workdays": sorted({str(day) for day in (stored.get("extra_workdays") or [])}),
        "holiday_note": stored.get("holiday_note") or "",
    }


def response_sla_workdays(session: Session) -> dict[str, int]:
    """首响承诺（C30）：询价 3 个工作日、反馈 7 个工作日。数值来自后台配置。"""
    profile = setting_value(session, "brand.profile", {}) or {}
    sla = profile.get("response_sla") or {}
    return {
        "inquiry_workdays": int(sla.get("inquiry_workdays", 3)),
        "feedback_workdays": int(sla.get("feedback_workdays", 7)),
    }


def _calendar_date(moment_ms: int, timezone_name: str) -> date:
    """把毫秒时间戳换算成客服时区里的自然日。"""
    tz = ZoneInfo(timezone_name) if timezone_name in ("Asia/Shanghai", "America/Los_Angeles") else ZoneInfo("Asia/Shanghai")
    return datetime.fromtimestamp(moment_ms / 1000, tz=tz).date()


def is_workday(moment_ms: int, *, calendar: dict[str, Any]) -> bool:
    """某一天是否为客服工作日：先看调休上班日，再看节假日，最后看星期几。"""
    day = _calendar_date(moment_ms, str(calendar["timezone"]))
    iso = day.isoformat()
    if iso in set(calendar["extra_workdays"]):
        return True
    if iso in set(calendar["holidays"]):
        return False
    return day.isoweekday() in set(calendar["workdays"])


def add_workdays(moment_ms: int, workdays: int, *, calendar: dict[str, Any]) -> int:
    """在给定时刻上加 N 个客服工作日，返回毫秒时间戳。

    规则（C30）：从次日开始数，只数工作日；节假日跳过、调休上班日照常计入。
    起始日当天不计入 N（当天提交不算「已经过一个工作日」）。
    """
    tz = ZoneInfo(str(calendar["timezone"]))
    start = datetime.fromtimestamp(moment_ms / 1000, tz=tz)
    cursor = start
    remaining = max(0, int(workdays))
    while remaining > 0:
        cursor = cursor + timedelta(days=1)
        if is_workday(int(cursor.timestamp() * 1000), calendar=calendar):
            remaining -= 1
    return int(cursor.timestamp() * 1000)


def response_deadline(session: Session, *, start_ms: int, workdays: int) -> int:
    """首响截止时间 = 提交时间 + N 个客服工作日（同一时刻的秒级精度）。"""
    return add_workdays(start_ms, workdays, calendar=support_calendar(session))


def series_payload(spec: SeriesSpec, entry: ContentEntry) -> dict[str, Any]:    return {
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
        # 说明文案可能是「按语言给出的值」，原样带出（不字符串化），由客户端解析。
        "redemption_ratio_note": stored.get("redemption_ratio_note", ""),
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


def commission_steps(policy: dict[str, Any], loyalty: dict[str, Any]) -> list[dict[str, Any]]:
    """委托步骤文案由服务端生成，数值取自后台配置，避免前端硬编码业务规则。

    信息顺序对应 11 文档的首页布局基线与 07 文档的流程分段：
    询价（需求沟通）→ 报价（有期限的商业提案）→ 条款与预付款（合同留证）→ 制作与交付。

    `title` / `description` 是**面向用户的文案**，按 C02/C24 同时给出简中与美式英文；
    两种语言里的数值来自同一份后台配置，不存在两份业务数值。
    """
    quote_days = policy["quote_valid_days"]
    prepayment = policy["prepayment_percent"]
    hold_minutes = policy["slot_hold_minutes"]
    free_revisions = policy["free_revision_count"]
    release_days = loyalty["release_delay_days"]
    return [
        {
            "step": "01",
            "title": {"zh-CN": "提交询价", "en-US": "Send an inquiry"},
            "description": {
                "zh-CN": "填写角色、物种、系列与制作要求，上传 1–3 张设定图。资料不足时我们会说明需要补什么。",
                "en-US": "Describe the character, species, series and build requirements, and upload 1–3 reference images. If anything is missing we will say exactly what to add.",
            },
        },
        {
            "step": "02",
            "title": {"zh-CN": "收到报价", "en-US": "Receive a quote"},
            "description": {
                "zh-CN": (
                    f"工作室按可制造性发布有版本的报价，有效期 {quote_days} 天，"
                    "明细、包含项与例外都会列出。"
                ),
                "en-US": (
                    f"The studio issues a versioned quote based on buildability, valid for {quote_days} days, "
                    "listing line items, inclusions and exceptions."
                ),
            },
        },
        {
            "step": "03",
            "title": {"zh-CN": "确认条款并付预付款", "en-US": "Accept the terms and pay the deposit"},
            "description": {
                "zh-CN": (
                    f"确认报价后记录条款版本与同意时间，支付预付款 {prepayment}%，"
                    f"名额预留 {hold_minutes} 分钟用于付款。"
                ),
                "en-US": (
                    f"Accepting the quote records the terms version and timestamp; the deposit is {prepayment}% "
                    f"and the slot is held for {hold_minutes} minutes while you pay."
                ),
            },
        },
        {
            "step": "04",
            "title": {"zh-CN": "跟踪制作并收货", "en-US": "Follow the build and receive the suit"},
            "description": {
                "zh-CN": (
                    f"每个制作阶段可查看图片并确认，免费修改 {free_revisions} 次；"
                    f"交付后 {release_days} 天发放星绒币。"
                ),
                "en-US": (
                    f"Each production stage can be reviewed with photos and confirmed, with {free_revisions} free "
                    f"revisions; reward points are issued {release_days} days after delivery."
                ),
            },
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
