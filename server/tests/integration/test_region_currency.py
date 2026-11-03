"""区域与币种（R15d / C02、C24）。

依据：
- `功能确认清单-已确认.md` C02：**中国大陆 / 人民币 / 简体中文** 与 **美国 / 美元 / 英文（美）**
  双区并行；C24：首发简中 + 英文（美）；
- `02_软件需求规格说明书.md` FR-022：**币种不隐式换算**；未上线语言不输出假入口；
- `08_商城订单支付与退款.md`：金额一律整数分 + 显式币种，客户端不能决定金额。

本轮补齐的缺口：此前"双区"只有 SKU 上的 `currency` 字段与展示层格式化——
**没有任何服务端规则说"哪个区域用哪种币种"**，也没有拒绝"用美元区地址买人民币定价商品"。
因此"按汇率折算"在实现上是空白，而不是被明确拒绝。现在：

1. `commerce_policy.regions` 把区域与币种绑起来（运营可配，含双语标签，**不含汇率**）；
2. 结算时按**收货地区**判定区域，SKU 币种与区域币种不一致 → `CURRENCY_NOT_SUPPORTED`（422），
   文案明确写"本站不按汇率折算"；
3. 预览载荷返回 `zone`（区域/币种/标签/是否跨境），页面据此说明结算币种。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commerce import service as commerce_service
from app.platform.errors import AppError
from tests.conftest import create_address, create_product, create_sku, create_user, issue_login


def _policy() -> dict:
    return dict(commerce_service.DEFAULT_POLICY)


# ------------------------------------------------------------------ 区域判定
@pytest.mark.parametrize(
    ("region_code", "expected"),
    [
        ("CN-31", "CN"),
        ("cn-11", "CN"),
        ("CN", "CN"),
        # 未填写地区：按站点默认区（中国大陆）处理
        ("", "CN"),
        (None, "CN"),
        ("US-CA", "US"),
        ("US", "US"),
        # 首发只登记 CN/US 两区：其他地区按海外区处理（跨境），不是"未支持"
        ("JP-13", "US"),
        ("GB-LND", "US"),
    ],
)
def test_zone_for_region(region_code: str | None, expected: str) -> None:
    assert commerce_service.zone_for_region(region_code) == expected


def test_zone_currency_comes_from_configuration() -> None:
    policy = _policy()
    assert commerce_service.zone_currency(policy, zone="CN") == "CNY"
    assert commerce_service.zone_currency(policy, zone="US") == "USD"

    # 运营配置改区域币种：服务端读配置，不在代码里写死
    policy["regions"] = {"CN": {"currency": "CNY"}, "US": {"currency": "CNY"}}
    assert commerce_service.zone_currency(policy, zone="US") == "CNY"

    # 未登记的区域：回落到站点政策币种，而不是随便猜一个
    policy["regions"] = {}
    assert commerce_service.zone_currency(policy, zone="US") == "CNY"


def test_zone_payload_marks_cross_border_and_label() -> None:
    payload = commerce_service.zone_payload(_policy(), zone="US")
    assert payload["zone"] == "US"
    assert payload["currency"] == "USD"
    assert payload["cross_border"] is True
    assert payload["label"]  # 双语标签（LocalizedText），页面对外显示

    domestic = commerce_service.zone_payload(_policy(), zone="CN")
    assert domestic["cross_border"] is False and domestic["currency"] == "CNY"


# ------------------------------------------------------------------ 结算拒绝折算
def _user_with_sku(db: Session, client: TestClient, *, currency: str) -> dict:
    user = create_user(db, nickname=f"区域客户{currency}", phone=f"1380000{len(currency)}{abs(hash(currency)) % 1000:03d}")
    address = create_address(db, user_id=user.id, region_code="CN-31")
    product = create_product(db, slug=f"zone-{currency.lower()}", title=f"区域示例 {currency}")
    sku = create_sku(db, product_id=product.id, price_cents=10000, stock_on_hand=5, currency=currency)
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()
    return {"user": user, "address": address, "sku": sku}


def test_domestic_checkout_reports_zone_and_currency(db: Session, client: TestClient) -> None:
    fixture = _user_with_sku(db, client, currency="CNY")
    preview = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": fixture["sku"].id, "quantity": 1}], "address_id": fixture["address"].id},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["currency"] == "CNY"
    assert body["zone"]["zone"] == "CN" and body["zone"]["currency"] == "CNY"
    assert body["zone"]["cross_border"] is False


def test_overseas_address_cannot_buy_domestically_priced_sku(
    db: Session, client: TestClient
) -> None:
    """**币种不隐式换算**：美国区地址买人民币定价商品 → 明确拒绝，不按汇率折算。"""
    fixture = _user_with_sku(db, client, currency="CNY")
    overseas = create_address(db, user_id=fixture["user"].id, region_code="US-CA", city="Los Angeles")
    db.commit()

    preview = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": fixture["sku"].id, "quantity": 1}], "address_id": overseas.id},
    )
    assert preview.status_code == 422, preview.text
    error = preview.json()["error"]
    assert error["code"] == "CURRENCY_NOT_SUPPORTED"
    assert "不按汇率折算" in error["message"]
    assert error["fields"]["currency"] == "USD"


def test_usd_priced_sku_is_blocked_for_domestic_address(db: Session, client: TestClient) -> None:
    fixture = _user_with_sku(db, client, currency="USD")
    preview = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": fixture["sku"].id, "quantity": 1}], "address_id": fixture["address"].id},
    )
    assert preview.status_code == 422, preview.text
    assert preview.json()["error"]["code"] == "CURRENCY_NOT_SUPPORTED"


def test_usd_priced_sku_settles_in_usd_for_overseas_address(
    db: Session, client: TestClient
) -> None:
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    row = get_setting(db, "commerce.policy")
    assert row is not None
    regions = {**row.value["regions"], "US": {"currency": "USD", "shipping": {
        "currency": "USD", "flat_cents": 2500, "free_threshold_cents": 0,
        "remote_region_surcharge_cents": 0, "max_cents": 0,
    }}}
    update_setting(db, key="commerce.policy", value={"regions": regions},
                   version=row.version, actor_id=None, reason="测试独立美元运费")
    fixture = _user_with_sku(db, client, currency="USD")
    overseas = create_address(db, user_id=fixture["user"].id, region_code="US-CA", city="Los Angeles")
    db.commit()
    preview = client.post(
        "/api/v1/checkout/preview",
        json={"items": [{"sku_id": fixture["sku"].id, "quantity": 1}], "address_id": overseas.id},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["currency"] == "USD"
    assert body["zone"]["zone"] == "US" and body["zone"]["currency"] == "USD"
    assert body["zone"]["cross_border"] is True
    # 金额仍是整数分，且没有任何"换算后的"字段
    assert isinstance(body["total_cents"], int)
    assert body["goods_cents"] == 10000
    assert body["shipping_cents"] == 2500


def test_overseas_checkout_without_shipping_template_is_disabled(db: Session, client: TestClient) -> None:
    fixture = _user_with_sku(db, client, currency="USD")
    overseas = create_address(db, user_id=fixture["user"].id, region_code="US-CA")
    db.commit()
    response = client.post("/api/v1/checkout/preview", json={
        "items": [{"sku_id": fixture["sku"].id, "quantity": 1}], "address_id": overseas.id,
    })
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


# ------------------------------------------------------------------ 配置校验
def test_regions_block_is_validated(db: Session) -> None:
    from app.modules.content.repository import get_setting
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    row = get_setting(db, "commerce.policy")
    assert row is not None
    current = dict(row.value or {})
    assert current["regions"]["US"]["currency"] == "USD"

    def _patch(value: dict) -> None:
        update_setting(
            db,
            key="commerce.policy",
            value={**current, "regions": value},
            version=row.version,
            actor_id=None,
            reason="区域币种回归",
        )
        db.commit()

    _patch({"CN": {"currency": "CNY"}, "US": {"currency": "USD", "label": {"en-US": "United States"}}})
    refreshed = get_setting(db, "commerce.policy")
    assert refreshed is not None and refreshed.value["regions"]["US"]["currency"] == "USD"

    # 币种只能是 CNY/USD（写别的等于给自己挖一个无法结算的区域）
    with pytest.raises(AppError) as error:
        _patch({"US": {"currency": "EUR"}})
    assert error.value.code == "VALIDATION_ERROR"
    # 缺 currency 也必须拒绝
    with pytest.raises(AppError):
        _patch({"US": {"label": "United States"}})
    db.rollback()
    assert get_setting(db, "commerce.policy").value["regions"]["US"]["currency"] == "USD"


def test_public_settings_expose_regions_without_rates(db: Session, client: TestClient) -> None:
    """公开配置给出区域与币种，**不包含任何汇率字段**。"""
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    db.commit()
    settings = client.get("/api/v1/content/settings")
    assert settings.status_code == 200, settings.text
    regions = settings.json()["commerce.policy"]["regions"]
    assert set(regions) >= {"CN", "US"}
    assert regions["US"]["currency"] == "USD"
    # 公开配置里只有"区域 → 币种"，**没有**任何汇率字段（不隐式换算）
    assert "rate" not in str(regions).lower() and "汇率" not in str(regions)


# 区域运费是独立金额，不是汇率换算；测试值只用于用例。
def test_region_shipping_and_duty_use_separate_templates() -> None:
    policy = _policy()
    policy["regions"] = {
        "CN": {"currency": "CNY", "shipping": {"mode": "flat", "flat_cents": 1200,
            "free_threshold_cents": 0, "remote_region_codes": [], "remote_region_surcharge_cents": 0, "max_cents": 0}},
        "US": {"currency": "USD", "shipping": {"mode": "flat", "flat_cents": 2500,
            "free_threshold_cents": 100000, "remote_region_codes": ["US-AK"], "remote_region_surcharge_cents": 700, "max_cents": 0},
            "cross_border": {"enabled": True, "duty_percent": 8, "note": {"en-US": "Test estimate"}}},
    }
    assert commerce_service.compute_shipping_cents(policy, goods_cents=10000, region_code="CN-31") == 1200
    assert commerce_service.compute_shipping_cents(policy, goods_cents=10000, region_code="US-CA") == 2500
    assert commerce_service.compute_shipping_cents(policy, goods_cents=10000, region_code="US-AK") == 3200
    assert commerce_service.compute_shipping_cents(policy, goods_cents=100000, region_code="US-CA") == 0
    estimate = commerce_service.cross_border_estimate(policy, goods_cents=10000, region_code="US-CA")
    assert estimate["duty_estimate_cents"] == 800
    assert estimate["duty_note"] == {"en-US": "Test estimate"}


def test_region_shipping_requires_explicit_values_and_matching_currency() -> None:
    from pydantic import ValidationError

    from app.modules.content.policies import RegionZone
    with pytest.raises(ValidationError):
        RegionZone.model_validate({"currency": "USD", "shipping": {}})
    shipping = {"currency": "USD", "flat_cents": 2500, "free_threshold_cents": 0,
                "remote_region_surcharge_cents": 0, "max_cents": 0}
    zone = RegionZone.model_validate({"currency": "USD", "shipping": shipping})
    assert zone.shipping is not None and zone.shipping.flat_cents == 2500
    with pytest.raises(ValidationError):
        RegionZone.model_validate({"currency": "CNY", "shipping": shipping})
