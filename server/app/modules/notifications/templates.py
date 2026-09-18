"""通知模板与渲染。

模板按版本管理，事件只携带业务数据；标题与正文在此集中定义，
避免各模块各写一套文案（C27：邮件必选，交易短信按需）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TEMPLATE_VERSION = "v1"

# 站点内相对路径，通知点击后跳转（不含任何令牌）。
ACCOUNT_QUOTES = "/account/quotes"
ACCOUNT_ORDERS = "/account/orders"
ACCOUNT_POINTS = "/account/points"
ACCOUNT_FEEDBACK = "/account/feedback"
ACCOUNT_COMMISSIONS = "/account/commissions"
ACCOUNT_AFTER_SALES = "/account/after-sales"
ACCOUNT_NOTIFICATIONS = "/account/notifications"


@dataclass(frozen=True)
class Template:
    version: str
    category: str  # transaction | production | loyalty | feedback | security | marketing
    subject: str
    body: str
    link_path: str | None = None
    channels: tuple[str, ...] = ("email",)
    # 交易短信按需；短信只用于必须即时触达的事件
    sms_body: str | None = None


def _money(cents: int | None, currency: str = "CNY") -> str:
    if cents is None:
        return "—"
    symbol = "¥" if currency == "CNY" else "$"
    return f"{symbol}{cents / 100:,.2f}"


def _when(value: Any) -> str:
    return str(value) if value else "—"


def render(event_type: str, payload: dict[str, Any]) -> Template:
    """按事件类型渲染模板。未知事件使用通用回退，不丢通知。"""
    builders = _BUILDERS.get(event_type)
    if builders is None:
        return Template(
            version=TEMPLATE_VERSION,
            category="transaction",
            subject=f"弈星纪元：{payload.get('title', '业务进展通知')}",
            body=str(payload.get("body", "你的业务记录有新的进展，请登录账户查看。")),
            link_path=payload.get("link_path"),
        )
    return builders(payload)


def _sms_challenge(payload: dict[str, Any]) -> Template:
    code = payload.get("code", "")
    minutes = int(payload.get("ttl_seconds", 300)) // 60
    return Template(
        version=TEMPLATE_VERSION,
        category="security",
        subject="【弈星纪元】登录验证码",
        body=(
            f"你的验证码是 {code}，{minutes} 分钟内有效。\n"
            "请勿向任何人透露该验证码；工作人员不会向你索取验证码。\n"
            "如果这不是你本人的操作，请忽略本短信并考虑修改账号安全设置。"
        ),
        channels=("sms",),
        sms_body=f"【弈星纪元】验证码 {code}，{minutes}分钟内有效，请勿泄露。",
    )


def _quote_published(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject=f"报价已发布（版本 {payload.get('quote_version', 1)}）",
        body=(
            f"你的委托询价已收到第 {payload.get('quote_version', 1)} 版报价。\n"
            f"报价总额：{_money(payload.get('total_cents'), payload.get('currency', 'CNY'))}\n"
            f"预付款：{_money(payload.get('prepayment_cents'), payload.get('currency', 'CNY'))}\n"
            f"有效期至：{_when(payload.get('expires_at'))}\n"
            "请在有效期内登录账户查看明细并确认。报价过期后需要重新评估。"
        ),
        link_path=f"{ACCOUNT_QUOTES}/{payload.get('quote_id', '')}",
    )


def _quote_needs_info(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject="委托询价需要补充资料",
        body=(
            "工作室在评估你的委托时发现资料不足，请补充后再提交。\n"
            f"说明：{payload.get('reason', '请查看询价详情中的补件说明。')}\n"
            "资料不足期间不会发布可支付报价。"
        ),
        link_path=f"{ACCOUNT_QUOTES}/{payload.get('inquiry_id', '')}",
    )


def _order_created(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject=f"订单 {payload.get('order_no', '')} 已创建，等待付款",
        body=(
            f"订单号：{payload.get('order_no', '')}\n"
            f"应付金额：{_money(payload.get('amount_cents'), payload.get('currency', 'CNY'))}\n"
            f"付款截止：{_when(payload.get('expires_at'))}\n"
            "完成付款前库存或名额仅临时保留；超时后订单进入关闭核对。"
        ),
        link_path=f"{ACCOUNT_ORDERS}/{payload.get('order_id', '')}",
    )


def _payment_succeeded(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject=f"收到款项：{_money(payload.get('amount_cents'), payload.get('currency', 'CNY'))}",
        body=(
            f"订单号：{payload.get('order_no', '')}\n"
            f"本次到账：{_money(payload.get('amount_cents'), payload.get('currency', 'CNY'))}\n"
            f"累计实收：{_money(payload.get('paid_cents'), payload.get('currency', 'CNY'))}\n"
            "该记录以支付渠道已验签通知或服务端查单结果为准。"
        ),
        link_path=f"{ACCOUNT_ORDERS}/{payload.get('order_id', '')}",
    )


def _payment_failed(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject="付款未完成",
        body=(
            "本次支付未成功，订单仍可继续付款。\n"
            f"订单号：{payload.get('order_no', '')}\n"
            f"说明：{payload.get('reason', '渠道返回失败。')}\n"
            "如已扣款但未到账，请勿重复支付，可在订单页发起查单。"
        ),
        link_path=f"{ACCOUNT_ORDERS}/{payload.get('order_id', '')}",
    )


def _order_shipped(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="production",
        subject=f"订单 {payload.get('order_no', '')} 已发货",
        body=(
            f"承运方：{payload.get('carrier', '—')}\n"
            f"物流单号：{payload.get('tracking_no', '—')}\n"
            "收到货后请在订单页确认收货，以便处理售后与星绒币发放。"
        ),
        link_path=f"{ACCOUNT_ORDERS}/{payload.get('order_id', '')}",
    )


def _stage_ready(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="production",
        subject=f"制作阶段待确认：{payload.get('stage_name', '')}",
        body=(
            f"当前阶段：{payload.get('stage_name', '')}（版本 {payload.get('revision', 1)}）\n"
            f"说明：{payload.get('description', '—')}\n"
            "请在订单页确认该阶段或提出修改；逾期未回复的延期会记录在案。"
        ),
        link_path=f"{ACCOUNT_COMMISSIONS}/{payload.get('project_id', '')}",
    )


def _refund_succeeded(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject=f"退款成功：{_money(payload.get('amount_cents'), payload.get('currency', 'CNY'))}",
        body=(
            f"订单号：{payload.get('order_no', '')}\n"
            f"退款金额：{_money(payload.get('amount_cents'), payload.get('currency', 'CNY'))}\n"
            f"退款单号：{payload.get('refund_no', '')}\n"
            "退款按原支付渠道退回，到账时间以渠道为准。"
        ),
        link_path=f"{ACCOUNT_AFTER_SALES}/{payload.get('after_sale_id', '')}",
    )


def _points_posted(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="loyalty",
        subject=f"星绒币到账：{payload.get('points', 0)}",
        body=(
            f"本次入账：{payload.get('points', 0)} 星绒币\n"
            f"来源：{payload.get('source_label', '购买奖励')}\n"
            f"当前可用：{payload.get('available_points', 0)}\n"
            "星绒币可用于兑换礼品与订单抵扣，规则以积分规则页为准。"
        ),
        link_path=ACCOUNT_POINTS,
    )


def _feedback_rewarded(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="feedback",
        subject="反馈处理结果与奖励",
        body=(
            f"反馈编号：{payload.get('feedback_id', '')}\n"
            f"结论：{payload.get('result', '已受理')}\n"
            f"奖励：{payload.get('points', 0)} 星绒币\n"
            f"理由：{payload.get('reason', '—')}\n"
            "如对结论有异议，可在反馈详情中提出申诉。"
        ),
        link_path=f"{ACCOUNT_FEEDBACK}/{payload.get('feedback_id', '')}",
    )


def _security_alert(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="security",
        subject=payload.get("title", "账号安全提醒"),
        body=(
            f"{payload.get('body', '')}\n"
            f"时间：{_when(payload.get('occurred_at'))}\n"
            "如果这不是你本人的操作，请立即通过客服渠道联系我们。"
        ),
    )


def _restock_available(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject=f"到货提醒：{payload.get('product_title', '')}",
        body=(
            f"你关注的商品已补货。\n商品：{payload.get('product_title', '')}\n"
            f"SKU：{payload.get('sku_label', '')}\n"
            "库存与价格以下单时的服务端校验结果为准。"
        ),
        link_path=f"/shop/{payload.get('product_slug', '')}",
    )


def _application_approved(payload: dict[str, Any]) -> Template:
    return Template(
        version=TEMPLATE_VERSION,
        category="transaction",
        subject="掉落申请已通过，可购买",
        body=(
            f"申请编号：{payload.get('application_id', '')}\n"
            f"商品：{payload.get('product_title', '')}\n"
            f"购买截止：{_when(payload.get('expires_at'))}\n"
            "审批通过只代表获得购买资格，未在规定时间内付款将释放资格。"
        ),
        link_path=f"/shop/{payload.get('product_slug', '')}",
    )


_BUILDERS = {
    "sms.challenge": _sms_challenge,
    "quote.published": _quote_published,
    "quote.needs_info": _quote_needs_info,
    "order.created": _order_created,
    "payment.succeeded": _payment_succeeded,
    "payment.failed": _payment_failed,
    "order.shipped": _order_shipped,
    "production.stage_ready": _stage_ready,
    "refund.succeeded": _refund_succeeded,
    "points.posted": _points_posted,
    "feedback.rewarded": _feedback_rewarded,
    "security.alert": _security_alert,
    "catalog.restock": _restock_available,
    "schedule.application_approved": _application_approved,
}

SUPPORTED_EVENT_TYPES = tuple(_BUILDERS)
