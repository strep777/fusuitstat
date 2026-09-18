"""后台设置的字段约束；验证完成前不得修改持久化对象。"""
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.platform.errors import AppError

Positive = Annotated[int, Field(strict=True, gt=0, le=1_000_000)]
Nonnegative = Annotated[int, Field(strict=True, ge=0, le=10_000_000_000)]


class Policy(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CommissionPolicy(Policy):
    prepayment_percent: Annotated[int, Field(ge=1, le=100)] = 50
    quote_valid_days: Positive = 7
    slot_hold_minutes: Positive = 30
    balance_due_days: Positive = 7
    free_revision_count: Nonnegative = 2
    terms_version: Annotated[str, Field(min_length=1, max_length=100)] = "commission-1"


class Rewards(Policy):
    minor: Nonnegative = 5
    normal: Nonnegative = 20
    major: Nonnegative = 50


class LoyaltyPolicy(Policy):
    points_name: Literal["星绒币"] = "星绒币"
    denominator_yuan: Positive = 5
    release_delay_days: Nonnegative = 7
    shipping_excluded: Literal[True] = True
    feedback_rewards: Rewards = Field(default_factory=Rewards)
    feedback_daily_reward_limit: Positive = 1
    redemption_enabled: bool = False
    redemption_ratio_note: str = ""
    # N02/N04 未确认前一律为 0：服务端不发明兑换率、上限与有效期，
    # 读不到规则时兑换入口保持关闭（09 文档：不能宣传未上线的兑换权益）。
    # ``redemption_points_per_yuan`` = 抵扣 1 元需要多少星绒币。
    redemption_points_per_yuan: Nonnegative = 0
    redemption_min_points: Nonnegative = 0
    redemption_max_ratio_percent: Annotated[int, Field(ge=0, le=100)] = 0
    # 0 = 不启用过期制度（09 文档：有效期随兑换一并启用，细则未定前不启用）。
    redemption_expiry_days: Nonnegative = 0
    redemption_restore_on_refund: bool = True

    @model_validator(mode="after")
    def redemption_rules_required(self) -> "LoyaltyPolicy":
        """启用兑换必须先把规则配全，避免出现「已开启但无法计算」的中间态。"""
        if not self.redemption_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("redemption_points_per_yuan", self.redemption_points_per_yuan),
                ("redemption_min_points", self.redemption_min_points),
                ("redemption_max_ratio_percent", self.redemption_max_ratio_percent),
            )
            if value <= 0
        ]
        if missing:
            raise ValueError(f"启用兑换前必须配置：{'、'.join(missing)}")
        return self


class FeatureFlags(Policy):
    online_payment: bool = False
    points_redemption: bool = False
    coupons: bool = False
    rush_rounds: bool = False
    client_stories: bool = False
    events: bool = False
    en_locale: bool = False
    marketing_email: bool = False


class Shipping(Policy):
    mode: Literal["flat", "free"] = "flat"
    flat_cents: Nonnegative = 1800
    free_threshold_cents: Nonnegative = 30000
    remote_region_surcharge_cents: Nonnegative = 1000
    remote_region_codes: list[str] = Field(default_factory=list)
    max_cents: Nonnegative = 20000


class Coupon(Policy):
    type: Literal["percent", "amount", "free_shipping"]
    value: Nonnegative
    min_goods_cents: Nonnegative = 0
    max_discount_cents: Nonnegative = 0
    label: str = ""

    @model_validator(mode="after")
    def valid_percent(self) -> "Coupon":
        if self.type == "percent" and self.value > 100:
            raise ValueError("折扣比例不能超过 100")
        return self


class AfterSale(Policy):
    window_days: Positive = 15
    types: list[Literal["refund_only", "return_refund", "repair", "exchange"]] = Field(default_factory=list)
    note: str = ""


class Invoice(Policy):
    types: list[Literal["personal", "business"]] = Field(default_factory=list)
    note: str = ""


class CrossBorder(Policy):
    """跨境展示与关税预估（C02/C24、C17；N07 的首版比例待运营补）。

    默认 ``enabled=False``、``duty_percent=0``：没有配置比例时服务端不预估关税，
    也不能把默认值当成已核准的税率（08/功能确认清单：以清关为准）。
    """

    enabled: bool = False
    duty_percent: Annotated[int, Field(ge=0, le=100)] = 0
    note: str = ""


class CommercePolicy(Policy):
    currency: Literal["CNY", "USD"] = "CNY"
    default_terms_version: str = "shop-1"
    shipping: Shipping = Field(default_factory=Shipping)
    coupons: dict[str, Coupon] = Field(default_factory=dict)
    after_sale: AfterSale = Field(default_factory=AfterSale)
    invoice: Invoice = Field(default_factory=Invoice)
    cross_border: CrossBorder = Field(default_factory=CrossBorder)
    returns_reference: str = ""
    cross_border_note: str = ""
    invoice_enabled: bool = True
    shipping_template_note: str = ""


class SupportCalendar(Policy):
    """客服工作日与节假日（C30）：逾期提醒与首响截止时间按它计算。"""

    timezone: Literal["Asia/Shanghai", "America/Los_Angeles"] = "Asia/Shanghai"
    # 周一=1 … 周日=7；至少要有 1 个工作日，否则「N 个工作日」永远算不出结果。
    workdays: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    holidays: list[str] = Field(default_factory=list)
    extra_workdays: list[str] = Field(default_factory=list)
    holiday_note: str = ""

    @model_validator(mode="after")
    def valid_calendar(self) -> "SupportCalendar":
        if not self.workdays:
            raise ValueError("至少要配置一个工作日")
        if len(set(self.workdays)) != len(self.workdays):
            raise ValueError("workdays 不能重复")
        for name, values in (("holidays", self.holidays), ("extra_workdays", self.extra_workdays)):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} 不能重复")
            for value in values:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{name} 必须是 YYYY-MM-DD 日期") from exc
        overlap = set(self.holidays) & set(self.extra_workdays)
        if overlap:
            raise ValueError(f"同一天不能既是节假日又是调休上班日：{sorted(overlap)[0]}")
        return self


MODELS: dict[str, type[BaseModel]] = {
    "commission.policy": CommissionPolicy,
    "loyalty.rules": LoyaltyPolicy,
    "platform.feature_flags": FeatureFlags,
    "commerce.policy": CommercePolicy,
    "support.calendar": SupportCalendar,
}


def validate_setting(key: str, value: Any) -> Any:
    model: type[BaseModel] | None = MODELS.get(key)
    if model is None:
        return value
    try:
        # 仅保存实际提供的字段，默认值仍由唯一业务规则源负责。
        return model.model_validate(value).model_dump(exclude_unset=True)
    except ValidationError as exc:
        fields = {".".join(str(part) for part in error["loc"]) or "value": error["msg"]
                  for error in exc.errors(include_input=False)}
        raise AppError("VALIDATION_ERROR", "配置值不合法，请检查标注字段。", fields=fields) from exc
