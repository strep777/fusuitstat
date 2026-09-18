"""多语言配置值与服务端composed文案（C02/C24、02 文档 FR-022；R15d）。

口径（`文档/06_API接口契约.md` 多语言文本一节）：面向用户的运营文案以「值自带语言」的形式
下发——可以是所有语言共用的一句话，也可以是 `{"zh-CN": …, "en-US": …}`；**服务端不按请求
语言裁剪**，由客户端按当前语言解析。这样切换语言不需要重新取配置，也不会出现两份设置漂移。

本轮补齐的缺口：
1. 设置里的运营文案（工作时段、售后参照、跨境说明、运费说明、节假日文案）只有中文一份，
   英文站只能显示中文——现在这些字段接受按语言给出的值，并且**未知语言写进来会被拒绝**；
2. `brand.profile` 之前**没有任何字段校验**（不在 `MODELS` 里），写入形状不受约束；
3. 服务端自己拼的对外文案（首页 hero、委托步骤）写死中文，现在同时给出两种语言，
   且数值仍来自同一份后台配置。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.platform.errors import AppError
from tests.conftest import admin_session, create_staff

PUBLIC_ORIGIN = "http://127.0.0.1:5173"


def _patch_setting(db: Session, key: str, value: object) -> None:
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    row = get_setting(db, key)
    assert row is not None
    merged = {**(row.value or {}), **value} if isinstance(value, dict) else value
    update_setting(
        db,
        key=key,
        value=merged,
        version=row.version,
        actor_id=None,
        reason="多语言配置回归",
    )
    db.commit()


# ------------------------------------------------------------------ 解析规则
def test_localized_text_resolution_and_fallback() -> None:
    from app.modules.content.service import localized_text

    # 纯字符串：所有语言共用（老配置天然兼容）
    assert localized_text("一句话") == "一句话"
    assert localized_text("一句话", "en-US") == "一句话"
    # 按语言给出：命中目标语言
    value = {"zh-CN": "中文", "en-US": "English"}
    assert localized_text(value, "zh-CN") == "中文"
    assert localized_text(value, "en-US") == "English"
    # 缺目标语言：回退简中（宁可显示中文，也不显示空）
    assert localized_text({"zh-CN": "只有中文"}, "en-US") == "只有中文"
    # 都没有：空串，不编造文案
    assert localized_text({}, "en-US") == ""
    assert localized_text(None, "en-US") == ""


# ------------------------------------------------------------------ 设置校验
def test_brand_profile_accepts_text_or_localized_map(db: Session) -> None:
    from app.modules.content.repository import get_setting

    _patch_setting(
        db,
        "brand.profile",
        {
            "service_hours": {
                "zh-CN": "周一至周五 10:00–19:00（Asia/Shanghai）",
                "en-US": "Mon–Fri 10:00–19:00 (Asia/Shanghai)",
            }
        },
    )
    row = get_setting(db, "brand.profile")
    assert row is not None
    assert row.value["service_hours"]["en-US"] == "Mon–Fri 10:00–19:00 (Asia/Shanghai)"

    # 纯字符串同样可写（不升级配置也能继续用）
    _patch_setting(db, "brand.profile", {"service_hours": "周一至周五"})
    refreshed = get_setting(db, "brand.profile")
    assert refreshed is not None and refreshed.value["service_hours"] == "周一至周五"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        # 未支持的语言：写进来会被静默失效，必须拒绝
        ("brand.profile", {"service_hours": {"fr-FR": "Lun–Ven"}}),
        # 空文案：等于把英文站渲染成空白
        ("brand.profile", {"service_hours": {"zh-CN": ""}}),
        # 值必须是字符串
        ("brand.profile", {"service_hours": {"zh-CN": 10}}),
        # 未知字段
        ("brand.profile", {"service_hours_en": "Mon–Fri"}),
        ("commerce.policy", {"returns_reference": {"ja-JP": "規則"}}),
        ("commerce.policy", {"shipping_template_note": {"en-US": ""}}),
        ("support.calendar", {"holiday_note": {"en-US": 2026}}),
    ],
)
def test_localized_settings_reject_unknown_locale_and_bad_shapes(
    db: Session, key: str, value: dict
) -> None:
    with pytest.raises(AppError) as error:
        _patch_setting(db, key, value)
    assert error.value.code == "VALIDATION_ERROR"


def test_seeded_defaults_carry_both_languages(db: Session) -> None:
    """默认值本身就要给出两种语言，否则英文站上线时页脚是中文。"""
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    profile = get_setting(db, "brand.profile")
    policy = get_setting(db, "commerce.policy")
    assert profile is not None and policy is not None
    for field, row in (
        ("service_hours", profile.value),
        ("returns_reference", policy.value),
        ("cross_border_note", policy.value),
        ("shipping_template_note", policy.value),
    ):
        value = row[field]
        assert isinstance(value, dict), f"{field} 应同时给出两种语言"
        assert value["zh-CN"].strip() and value["en-US"].strip(), f"{field} 缺少语言"
        # 英文值不应还是中文（漏译检测：出现汉字即视为未翻译）
        assert not any("\u4e00" <= char <= "\u9fff" for char in value["en-US"]), field


# ------------------------------------------------------------------ 对外载荷
def test_public_settings_and_home_expose_localized_values(db: Session, client: TestClient) -> None:
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    db.commit()

    settings = client.get("/api/v1/content/settings")
    assert settings.status_code == 200, settings.text
    profile = settings.json()["brand.profile"]
    assert isinstance(profile["service_hours"], dict)
    assert profile["service_hours"]["en-US"]

    home = client.get("/api/v1/site/home")
    assert home.status_code == 200, home.text
    body = home.json()
    assert isinstance(body["hero"]["tagline"], dict)
    assert body["hero"]["tagline"]["en-US"]
    assert isinstance(body["hero"]["primary_action"]["label"], dict)
    assert body["hero"]["primary_action"]["to"] == "/commission/guide"
    steps = body["commission_steps"]
    assert len(steps) == 4
    for step in steps:
        assert isinstance(step["title"], dict) and isinstance(step["description"], dict)
        assert step["title"]["en-US"] and step["description"]["en-US"]


def test_composed_copy_uses_configured_numbers_in_both_languages(
    db: Session, client: TestClient
) -> None:
    """两种语言的文案里，数值都来自同一份后台配置（不存在两套业务数值）。"""
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    _patch_setting(db, "commission.policy", {"quote_valid_days": 9, "prepayment_percent": 40})

    body = client.get("/api/v1/site/home").json()
    steps = {step["step"]: step for step in body["commission_steps"]}
    assert "9 天" in steps["02"]["description"]["zh-CN"]
    assert "9 days" in steps["02"]["description"]["en-US"]
    assert "40%" in steps["03"]["description"]["zh-CN"]
    assert "40%" in steps["03"]["description"]["en-US"]


def test_cross_border_note_is_passed_through_untouched(db: Session, client: TestClient) -> None:
    """跨境说明按语言下发，服务端不做字符串化（否则前端只能拿到 `{'zh-CN': …}` 文本）。"""
    from app.modules.commerce import service as commerce_service

    _patch_setting(
        db,
        "commerce.policy",
        {
            "cross_border": {
                "enabled": True,
                "duty_percent": 10,
                "note": {"zh-CN": "以清关为准", "en-US": "Subject to customs clearance"},
            }
        },
    )
    policy = commerce_service.commerce_policy(db)
    note = policy["cross_border"]["note"]
    assert note == {"zh-CN": "以清关为准", "en-US": "Subject to customs clearance"}

    estimate = commerce_service.cross_border_estimate(policy, goods_cents=10000, region_code="US-CA")
    assert estimate["cross_border"] is True
    assert estimate["duty_note"] == note


def test_admin_settings_view_is_readable_by_operator(db: Session, client: TestClient) -> None:
    """后台设置列表要能看到多语言值（否则运营没法核对英文文案）。"""
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    staff, secret = create_staff(db, login_name="content-ops", roles=["admin"])
    db.commit()
    csrf = admin_session(db, client, staff, secret)
    listed = client.get(
        "/api/v1/admin/settings",
        headers={"X-CSRF-Token": csrf["csrf_token"], "Origin": PUBLIC_ORIGIN},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"] if isinstance(listed.json(), dict) else listed.json()
    brand = next(item for item in items if item["key"] == "brand.profile")
    assert isinstance(brand["value"]["service_hours"], dict)


# ------------------------------------------------------------------ 其它读路径不得字符串化
def test_ratio_note_passes_through_untouched(db: Session) -> None:
    """积分兑换说明按语言下发：字符串化会让英文站显示成 `{'zh-CN': …}`。"""
    from app.modules.loyalty import service as loyalty_service

    note = {"zh-CN": "每 1 元需要 100 星绒币", "en-US": "100 points per CNY 1"}
    _patch_setting(
        db,
        "loyalty.rules",
        {
            "redemption_enabled": True,
            "redemption_points_per_yuan": 100,
            "redemption_min_points": 50,
            "redemption_max_ratio_percent": 20,
            "redemption_ratio_note": note,
        },
    )
    policy = loyalty_service.redemption_policy(db)
    assert policy["ratio_note"] == note
    # 报价接口还要求兑换开关打开（否则整条路径是拒绝路径，与文案解析无关）
    _patch_setting(db, "platform.feature_flags", {"points_redemption": True})
    quote = loyalty_service.redemption_quote(db, user_id="u-1", amount_cents=10000, points=0)
    assert quote["ratio_note"] == note


def test_after_sale_policy_note_is_published_and_typed(db: Session) -> None:
    """售后条件说明由运营配置给出：DTO 必须接受「按语言给出的值」。"""
    from app.modules.commerce import service as commerce_service
    from app.modules.contracts import AfterSaleDto

    note = {"zh-CN": "定制商品不支持无理由退货", "en-US": "Custom items are not returnable without cause"}
    _patch_setting(db, "commerce.policy", {"after_sale": {"note": note}})
    policy = commerce_service.commerce_policy(db)["after_sale"]
    assert policy["note"] == note

    dto = AfterSaleDto(
        id="as-1",
        order_id="o-1",
        type="refund_only",
        status="OPEN",
        reason="尺寸不合适",
        policy_note=note,
    )
    assert dto.policy_note == note
    # 纯字符串同样兼容（老配置不需要迁移）
    assert AfterSaleDto(
        id="as-2", order_id="o-1", type="refund_only", status="OPEN", reason="x", policy_note="一句话"
    ).policy_note == "一句话"
