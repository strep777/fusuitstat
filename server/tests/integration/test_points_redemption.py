"""积分兑换 / 订单抵扣的服务端裁定回归（R19 第一阶段）。

依据：
- `09_积分与反馈功能规格.md`：C15 支持兑换礼品、商品与订单抵扣，但兑换率、最低消费、
  抵扣比例上限、有效期与退款恢复细则（N02–N04）未定；**细则与兑换闭环完成前入口保持关闭
  （`FEATURE_DISABLED`），不能宣传未上线的兑换权益**，也不得编造业务数值；
- `08_商城订单支付与退款.md`：应付金额由服务端计算，客户端只能提交选择。

因此本文件锁定的是「机制」而不是「业务数值」：
1. 开关关闭 → 403 `FEATURE_DISABLED`；
2. 开关打开但规则未配置 → 仍然是 `FEATURE_DISABLED`（不猜兑换率），
   并且这种「开启但没规则」的组合**无法通过设置校验写入**；
3. 规则配置齐全时，抵扣上限、最低使用数量、可用余额与整数取整都由服务端裁定。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.platform.errors import AppError


def _configure(db, *, points_per_yuan: int = 100, min_points: int = 50, max_ratio: int = 20,
               enabled: bool = True, flag: bool = True, **extra: object) -> None:
    """写入兑换规则与功能开关（绕过 HTTP，直接写设置行以便测试边界组合）。"""
    from app.modules.content.models import SiteSetting
    from app.modules.content.service import ensure_default_settings

    ensure_default_settings(db)
    loyalty = {
        "redemption_enabled": enabled,
        "redemption_points_per_yuan": points_per_yuan,
        "redemption_min_points": min_points,
        "redemption_max_ratio_percent": max_ratio,
        **extra,
    }
    row = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "loyalty.rules"))
    assert row is not None
    row.value = {**(row.value or {}), **loyalty}
    flags = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "platform.feature_flags"))
    assert flags is not None
    flags.value = {**(flags.value or {}), "points_redemption": flag}
    db.commit()


def _grant(db, user_id: str, points: int) -> None:
    """直接给账户可用余额（不走奖励流程，本文件只验证兑换裁定）。"""
    from app.modules.loyalty import repository as repo

    account = repo.ensure_account(db, user_id=user_id)
    repo.add_balance(db, account_id=account.id, delta=points)
    db.commit()


def test_quote_is_disabled_when_feature_flag_is_off(customer, payment_db):
    _configure(payment_db, enabled=True, flag=True)
    _grant(payment_db, customer.user_id, 500)
    _configure(payment_db, flag=False)

    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 10_000, "points": 100}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_quote_is_disabled_when_rules_are_not_configured(customer, payment_db):
    """开启但规则为空：如实拒绝，不发明兑换率。"""
    _configure(payment_db, enabled=True, flag=True, points_per_yuan=0, min_points=0, max_ratio=0)
    _grant(payment_db, customer.user_id, 500)

    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 10_000, "points": 100}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"

    from app.modules.loyalty.service import redemption_policy

    policy = redemption_policy(payment_db)
    assert policy["rules_ready"] is False and policy["enabled"] is False
    assert policy["points_per_yuan"] == 0 and policy["min_points"] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"redemption_enabled": True, "redemption_points_per_yuan": 0,
         "redemption_min_points": 50, "redemption_max_ratio_percent": 20},
        {"redemption_enabled": True, "redemption_points_per_yuan": 100,
         "redemption_min_points": 0, "redemption_max_ratio_percent": 20},
        {"redemption_enabled": True, "redemption_points_per_yuan": 100,
         "redemption_min_points": 50, "redemption_max_ratio_percent": 0},
        {"redemption_enabled": True, "redemption_points_per_yuan": 100,
         "redemption_min_points": 50, "redemption_max_ratio_percent": 101},
        {"redemption_expiry_days": -1},
    ],
)
def test_enabling_redemption_without_complete_rules_is_rejected(db, patch):
    """不完整的兑换规则不能写进设置：不存在「已开启但无法计算」的中间态。"""
    from app.modules.content.models import SiteSetting
    from app.modules.content.service import ensure_default_settings, update_setting

    ensure_default_settings(db)
    db.commit()
    row = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "loyalty.rules"))
    assert row is not None
    before = dict(row.value or {})
    version = int(row.version)
    with pytest.raises(AppError) as error:
        update_setting(
            db, key="loyalty.rules", value=patch, version=version, actor_id=None, reason="回归"
        )
    assert error.value.code == "VALIDATION_ERROR"
    db.expire_all()
    row = db.scalar(sa.select(SiteSetting).where(SiteSetting.key == "loyalty.rules"))
    assert row is not None
    assert dict(row.value or {}) == before, "校验失败不得修改已保存的设置"
    assert int(row.version) == version


def test_quote_caps_by_ratio_available_balance_and_min_points(customer, payment_db):
    """抵扣上限 = min(可用积分, 金额 × 抵扣比例上限)；低于最低使用数量直接拒绝。"""
    _configure(payment_db, points_per_yuan=100, min_points=50, max_ratio=20)
    _grant(payment_db, customer.user_id, 5000)

    # 金额 50 元 = 5000 分，比例上限 20% → 最多抵 1000 分 = 1000 星绒币（100 分/元）。
    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 3000}
    )
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["max_points"] == 1000 and quote["max_cents"] == 1000
    assert quote["applied_points"] == 1000 and quote["applied_cents"] == 1000
    assert quote["capped"] is True
    assert quote["available_points"] == 5000

    # 可用余额低于比例上限时，以余额为准。
    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 3000}
    )
    assert response.json()["max_points"] == 1000
    from app.modules.loyalty import repository as repo

    account = repo.ensure_account(payment_db, user_id=customer.user_id)
    repo.add_balance(payment_db, account_id=account.id, delta=-4700)
    payment_db.commit()
    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 3000}
    )
    quote = response.json()
    assert quote["available_points"] == 300 and quote["max_points"] == 300
    assert quote["applied_points"] == 300 and quote["applied_cents"] == 300

    # 低于最低使用数量：拒绝，而不是悄悄按最低值扣。
    below = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 20}
    )
    assert below.status_code == 422, below.text
    assert below.json()["error"]["code"] == "VALIDATION_ERROR"
    assert below.json()["error"]["fields"]["points"] == "低于最低使用数量"

    # 提交 0 表示不使用积分：合法，抵扣为 0。
    zero = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 0}
    )
    assert zero.status_code == 200
    assert zero.json()["applied_points"] == 0 and zero.json()["applied_cents"] == 0


def test_quote_integer_math_never_over_deducts_or_exceeds_cap(customer, payment_db):
    """兑换率不能整除时：抵扣金额向下取整，实际扣的积分不超过用户提交的数量。"""
    # 300 星绒币 = 1 元 → 1 分 = 1/3 分钱。
    _configure(payment_db, points_per_yuan=300, min_points=1, max_ratio=100)
    _grant(payment_db, customer.user_id, 1000)

    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 10_000, "points": 100}
    )
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["applied_cents"] == 33, "向下取整，不能凭空多出分钱"
    assert quote["applied_points"] == 99, "只需 99 分即可抵 33 分钱，不能多扣那 1 分"
    assert quote["applied_points"] <= quote["requested_points"]
    assert quote["capped"] is False, "取整零头不算被上限截断"
    assert quote["rounded_points"] == 1, "零头单独报告，便于前端说明"

    # 抵扣上限按金额比例截断后，折算回去仍然不超过上限。
    _configure(payment_db, points_per_yuan=300, min_points=1, max_ratio=20)
    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 1001, "points": 1000}
    )
    quote = response.json()
    cap_cents = 1001 * 20 // 100
    assert quote["applied_cents"] <= cap_cents
    assert quote["max_cents"] <= cap_cents
    assert quote["applied_points"] == quote["max_points"]


def test_quote_exposes_expiry_and_refund_restore_rules(customer, payment_db):
    """有效期与退款恢复细则只在运营配置后才对外可见；未配置时明确为 0 / 默认恢复。"""
    _configure(payment_db, points_per_yuan=100, min_points=50, max_ratio=20, redemption_expiry_days=0)
    _grant(payment_db, customer.user_id, 500)
    quote = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 100}
    ).json()
    assert quote["expiry_days"] == 0, "细则未定前不启用过期制度"
    assert quote["restore_on_refund"] is True
    assert quote["points_name"] == "星绒币"
    assert quote["max_ratio_percent"] == 20
    assert quote["points_per_yuan"] == 100

    _configure(
        payment_db,
        points_per_yuan=100,
        min_points=50,
        max_ratio=20,
        redemption_expiry_days=365,
        redemption_restore_on_refund=False,
    )
    quote = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 5000, "points": 100}
    ).json()
    assert quote["expiry_days"] == 365 and quote["restore_on_refund"] is False


def test_quote_rejects_non_positive_amount(customer, payment_db):
    _configure(payment_db, points_per_yuan=100, min_points=50, max_ratio=20)
    _grant(payment_db, customer.user_id, 500)
    response = customer.client.get(
        "/api/v1/points/redemption-quote", params={"amount_cents": 0, "points": 100}
    )
    assert response.status_code == 422, response.text
