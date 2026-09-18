"""支付渠道适配器的验签、查单、退款与对账核对（R17）。

依据：
- `文档/08_商城订单支付与退款.md`：成功只认**已验签通知或服务端主动查单**；超时用**同一商户单号**
  查单；退款重试使用同一退款单号；
- `AGENTS.md` 第 5 条与 `文档/06`：支付原始报文不进业务记录，渠道凭据缺失必须 fail closed；
- `文档/功能确认清单-已确认.md` C04：信用卡（Stripe）为已确认渠道，**当前无商户号**。

本轮交付的边界（诚实声明）：
1. **可在本地验证**：Stripe 的签名算法与容忍窗口、事件解析与状态映射、金额/币种核对、
   未配置凭据时的 fail closed、四个渠道「未启用即拒绝」的门禁；
2. **必须真实凭据才能验证（R30 BLOCKED）**：微信平台证书链、支付宝 RSA2 公钥证书模式、
   PayPal 证书下载与 OAuth2 令牌、Stripe 真实下单/查单/退款/对账——本文件对这些只验证
   **拒绝路径**（缺凭据、伪造签名、过期时间戳、篡改报文），不伪造通过路径。
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.adapters.base import GatewayNotConfigured
from app.adapters.payments import get_gateway, reset_gateways
from app.adapters.payments.base import (
    PAYMENT_RESULT_CLOSED,
    PAYMENT_RESULT_NOT_FOUND,
    PAYMENT_RESULT_PENDING,
    PAYMENT_RESULT_SUCCEEDED,
    REFUND_RESULT_SUCCEEDED,
)
from app.adapters.payments.stripe import StripeGateway, expected_signature, parse_signature_header
from app.platform.config import Settings
from app.platform.errors import AppError
from app.platform.ids import now_ms

WEBHOOK_SECRET = "whsec_test_0123456789abcdef"
SECRET_KEY = "sk_test_0123456789abcdef"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "public_origin": "http://127.0.0.1:5173",
        "stripe_secret_key": SECRET_KEY,
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_account_id": "acct_test_1",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _signed(event: dict, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> tuple[dict, bytes]:
    moment = timestamp if timestamp is not None else now_ms() // 1000
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode(), f"{moment}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={moment},v1={signature}"}, body


def _succeeded_event(**overrides: object) -> dict:
    intent: dict[str, object] = {
        "id": "pi_3Test",
        "object": "payment_intent",
        "status": "succeeded",
        "amount": 19900,
        "amount_received": 19900,
        "currency": "cny",
        "created": 1_769_000_000,
        "account": "acct_test_1",
        "metadata": {"merchant_order_no": "SO20260101001"},
    }
    intent.update(overrides)
    return {
        "id": "evt_1",
        "object": "event",
        "type": "payment_intent.succeeded",
        "data": {"object": intent},
    }


# ------------------------------------------------------------------ 签名解析与算法
def test_signature_header_parsing() -> None:
    assert parse_signature_header("t=123,v1=abc") == (123, ["abc"])
    # 多个 v1（密钥轮换期）都要保留；v0 是旧协议，不参与比较。
    timestamp, signatures = parse_signature_header("t=5,v1=aa,v0=bb,v1=cc")
    assert timestamp == 5 and signatures == ["aa", "cc"]
    assert parse_signature_header(None) == (None, [])
    assert parse_signature_header("v1=aa") == (None, ["aa"])
    assert parse_signature_header("t=not-a-number,v1=aa") == (None, ["aa"])


def test_expected_signature_matches_official_recipe() -> None:
    """待签名字符串是 ``{t}.{payload}``，算法 HMAC-SHA256（官方文档口径）。"""
    body = b'{"id":"evt_1"}'
    manual = hmac.new(WEBHOOK_SECRET.encode(), b"1700000000." + body, hashlib.sha256).hexdigest()
    assert expected_signature(WEBHOOK_SECRET, 1_700_000_000, body) == manual


# ------------------------------------------------------------------ 回调验签
def test_verified_success_event_produces_payment() -> None:
    gateway = StripeGateway(_settings())
    headers, body = _signed(_succeeded_event())
    verdict = gateway.verify_notification_detailed(headers, body)
    assert verdict.accepted is True and verdict.reason == "verified"
    payment = verdict.payment
    assert payment is not None
    assert payment.provider == "stripe"
    assert payment.merchant_order_no == "SO20260101001"
    assert payment.amount_cents == 19900
    assert payment.currency == "CNY"
    assert payment.provider_transaction_id == "pi_3Test"
    assert payment.event_id == "evt_1"
    assert payment.account_ref == "acct_test_1"


def test_tampered_body_and_wrong_secret_are_rejected() -> None:
    gateway = StripeGateway(_settings())
    headers, body = _signed(_succeeded_event())
    tampered = body.replace(b"19900", b"29900")
    assert gateway.verify_notification_detailed(headers, tampered).reason == "signature_mismatch"
    # 换一把密钥（模拟伪造者）：同样拒绝
    other, other_body = _signed(_succeeded_event(), secret="whsec_attacker")
    assert gateway.verify_notification_detailed(other, other_body).reason == "signature_mismatch"
    assert gateway.verify_notification(other, other_body) is None


def test_replay_window_and_missing_signature_are_rejected() -> None:
    gateway = StripeGateway(_settings())
    stale = now_ms() // 1000 - 3600
    headers, body = _signed(_succeeded_event(), timestamp=stale)
    assert gateway.verify_notification_detailed(headers, body).reason == "timestamp_out_of_window"
    fresh, fresh_body = _signed(_succeeded_event())
    assert gateway.verify_notification_detailed({}, fresh_body).reason == "signature_missing"
    assert gateway.verify_notification_detailed({"Stripe-Signature": "t=1"}, fresh_body).reason == "signature_missing"
    assert fresh


def test_non_success_and_incomplete_events_do_not_credit() -> None:
    gateway = StripeGateway(_settings())
    failed = _succeeded_event()
    failed["type"] = "payment_intent.payment_failed"
    headers, body = _signed(failed)
    verdict = gateway.verify_notification_detailed(headers, body)
    # 验签通过但不是成功收款：接受事件、不给付款结论（服务层不入账）。
    assert verdict.accepted is True and verdict.payment is None
    assert verdict.reason == "not_a_success_payment"

    ignored = _succeeded_event()
    ignored["type"] = "charge.refunded"
    headers, body = _signed(ignored)
    assert gateway.verify_notification_detailed(headers, body).reason == "ignored_event_type"

    # 缺商户单号：拒绝（没法与本地应付项核对，交人工/对账处理）
    headers, body = _signed(_succeeded_event(metadata={}))
    assert gateway.verify_notification_detailed(headers, body).reason == "merchant_order_no_missing"

    # 金额非法：拒绝
    headers, body = _signed(_succeeded_event(amount_received="19900"))
    assert gateway.verify_notification_detailed(headers, body).reason == "amount_malformed"

    headers, body = _signed(_succeeded_event(currency=""))
    assert gateway.verify_notification_detailed(headers, body).reason == "amount_malformed"


def test_malformed_payload_is_rejected() -> None:
    gateway = StripeGateway(_settings())
    moment = now_ms() // 1000
    body = b"{not-json"
    signature = hmac.new(WEBHOOK_SECRET.encode(), f"{moment}.".encode() + body, hashlib.sha256).hexdigest()
    verdict = gateway.verify_notification_detailed(
        {"Stripe-Signature": f"t={moment},v1={signature}"}, body
    )
    assert verdict.accepted is False and verdict.reason == "payload_malformed"


# ------------------------------------------------------------------ 凭据与门禁
def test_missing_credentials_fail_closed() -> None:
    gateway = StripeGateway(_settings(stripe_webhook_secret=""))
    headers, body = _signed(_succeeded_event())
    # 未配置签名密钥：连验签都不做，直接拒绝（不猜、不放行）。
    assert gateway.verify_notification_detailed(headers, body).reason == "webhook_secret_missing"

    bare = StripeGateway(_settings(stripe_secret_key=""))
    with pytest.raises(GatewayNotConfigured):
        bare.query("SO20260101001")
    with pytest.raises(GatewayNotConfigured):
        bare.create("SO20260101001", 19900, "CNY", "web", "测试商品", None)
    with pytest.raises(GatewayNotConfigured):
        bare.download_bill("2026-01-05")


def test_disabled_provider_is_refused() -> None:
    """未在 PAYMENT_PROVIDERS 里启用的渠道一律拒绝，不存在绕过开关的入口。"""
    reset_gateways()
    settings = _settings(payment_providers="mock")
    assert "stripe" not in settings.enabled_payment_providers
    with pytest.raises(AppError) as error:
        get_gateway("stripe")
    assert error.value.code == "PAYMENT_PROVIDER_DISABLED"
    reset_gateways()


def test_production_requires_channel_credentials() -> None:
    """生产环境不允许「渠道已启用但没有凭据」的中间态（与短信侧同一口径）。"""
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "public_origin": "https://starium.cn",
        "session_secret": "x" * 40,
        "csrf_secret": "y" * 40,
        "otp_secret": "z" * 40,
        "phone_lookup_key": "p" * 40,
        "pii_encryption_key": "q" * 40,
        "cookie_secure": True,
        "sms_adapter": "aliyun",
        "aliyun_sms_access_key_id": "ak",
        "aliyun_sms_access_key_secret": "sk",
        "sms_template_code": "SMS_1",
        "storage_backend": "s3",
        "smtp_host": "mail.test",
        "payment_providers": "stripe",
    }
    with pytest.raises(ValueError) as error:
        Settings(**base)  # type: ignore[arg-type]
    message = str(error.value)
    assert "STRIPE_SECRET_KEY" in message and "STRIPE_WEBHOOK_SECRET" in message

    # 凭据齐全后放行（其余条件都满足）
    base.update(
        {
            "stripe_secret_key": SECRET_KEY,
            "stripe_webhook_secret": WEBHOOK_SECRET,
            "stripe_account_id": "acct_live_1",
        }
    )
    settings = Settings(**base)  # type: ignore[arg-type]
    assert settings.enabled_payment_providers == ["stripe"]


def test_other_channels_need_their_own_credentials_in_production() -> None:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "public_origin": "https://starium.cn",
        "session_secret": "x" * 40,
        "csrf_secret": "y" * 40,
        "otp_secret": "z" * 40,
        "phone_lookup_key": "p" * 40,
        "pii_encryption_key": "q" * 40,
        "cookie_secure": True,
        "sms_adapter": "aliyun",
        "aliyun_sms_access_key_id": "ak",
        "aliyun_sms_access_key_secret": "sk",
        "sms_template_code": "SMS_1",
        "storage_backend": "s3",
        "smtp_host": "mail.test",
        "payment_providers": "wechat,alipay,paypal",
    }
    with pytest.raises(ValueError) as error:
        Settings(**base)  # type: ignore[arg-type]
    message = str(error.value)
    assert "WECHAT_MERCHANT_ID" in message
    assert "ALIPAY_APP_ID" in message
    assert "PAYPAL_CLIENT_ID" in message


# ------------------------------------------------------------------ 状态映射（不联网）
def test_status_mapping_is_explicit() -> None:
    gateway = StripeGateway(_settings())
    assert gateway._payment_status("succeeded") == PAYMENT_RESULT_SUCCEEDED
    assert gateway._payment_status("canceled") == PAYMENT_RESULT_CLOSED
    for state in ("requires_payment_method", "requires_confirmation", "requires_action", "processing"):
        assert gateway._payment_status(state) == PAYMENT_RESULT_PENDING
    # 未知状态不得当成成功或待支付：报 UNKNOWN，由服务层继续查单。
    assert gateway._payment_status("something_new") not in (PAYMENT_RESULT_SUCCEEDED, PAYMENT_RESULT_PENDING)


def test_bill_round_trip_and_mismatched_day() -> None:
    gateway = StripeGateway(_settings())
    raw = json.dumps(
        {
            "bill_date": "2026-01-05",
            "data": [
                {
                    "merchant_order_no": "SO20260101001",
                    "transaction_id": "txn_1",
                    "kind": "payment",
                    "amount_cents": 19900,
                    "currency": "CNY",
                    "refund_no": None,
                    "fee_cents": 120,
                    "occurred_at": 1_767_000_000_000,
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    rows = gateway.parse_bill("2026-01-05", raw)
    assert rows[0]["merchant_order_no"] == "SO20260101001" and rows[0]["fee_cents"] == 120
    from app.adapters.base import GatewayError

    with pytest.raises(GatewayError):
        gateway.parse_bill("2026-01-06", raw)
    with pytest.raises(GatewayError):
        gateway.parse_bill("2026-01-05", b"not-json")


def test_balance_transaction_rows_map_to_bill_fields() -> None:
    gateway = StripeGateway(_settings())
    payment = gateway._bill_row(
        {
            "id": "txn_1",
            "amount": 19900,
            "fee": 120,
            "currency": "cny",
            "created": 1_767_000_000,
            "source": {"id": "ch_1", "metadata": {"merchant_order_no": "SO1"}},
        }
    )
    assert payment["kind"] == "payment" and payment["amount_cents"] == 19900
    assert payment["fee_cents"] == 120 and payment["currency"] == "CNY"
    refund = gateway._bill_row(
        {
            "id": "txn_2",
            "amount": -5000,
            "fee": 0,
            "currency": "cny",
            "created": 1_767_000_100,
            "source": {"id": "re_1", "metadata": {"merchant_order_no": "SO1", "refund_no": "RF1"}},
        }
    )
    assert refund["kind"] == "refund" and refund["amount_cents"] == 5000 and refund["refund_no"] == "RF1"


def test_refund_status_mapping() -> None:
    gateway = StripeGateway(_settings())
    from app.adapters.payments.base import (
        REFUND_RESULT_FAILED,
        REFUND_RESULT_PROCESSING,
        REFUND_RESULT_UNKNOWN,
    )

    assert gateway._refund_state({"status": "succeeded", "amount": 100}, refund_no="RF1")["status"] == (
        REFUND_RESULT_SUCCEEDED
    )
    assert gateway._refund_state({"status": "pending", "amount": 100}, refund_no="RF1")["status"] == (
        REFUND_RESULT_PROCESSING
    )
    assert gateway._refund_state({"status": "failed", "amount": 100}, refund_no="RF1")["status"] == (
        REFUND_RESULT_FAILED
    )
    assert gateway._refund_state({"status": "weird", "amount": 100}, refund_no="RF1")["status"] == (
        REFUND_RESULT_UNKNOWN
    )


def test_query_without_search_support_reports_not_found() -> None:
    """Search 不可用（4xx）时必须报 NOT_FOUND，不能猜成功。"""
    from app.adapters.base import GatewayError

    gateway = StripeGateway(_settings())

    def _boom(*_args: object, **_kwargs: object) -> dict:
        raise GatewayError("stripe 拒绝请求：resource_missing", retryable=False)

    gateway._request = _boom  # type: ignore[method-assign]
    assert gateway.query("SO20260101001")["status"] == PAYMENT_RESULT_NOT_FOUND


def test_search_query_escapes_quotes() -> None:
    gateway = StripeGateway(_settings())
    assert gateway._escape_search("SO'1") == "SO\\'1"


def test_configuration_report_never_returns_secrets() -> None:
    gateway = StripeGateway(_settings())
    report = gateway.describe_configuration()
    assert report["secret_key_configured"] is True and report["webhook_secret_configured"] is True
    serialized = json.dumps(report, ensure_ascii=False)
    assert SECRET_KEY not in serialized and WEBHOOK_SECRET not in serialized
    assert report["notification_url"].endswith("/payment-notifications/stripe")


def test_ack_shapes() -> None:
    gateway = StripeGateway(_settings())
    assert gateway.ack_success() == {"received": True}
    assert gateway.ack_failure("signature_mismatch") == {"received": False, "reason": "signature_mismatch"}


# ------------------------------------------------------------------ 其它三个渠道
def _rsa_pair() -> tuple[str, str]:
    """生成一对 RSA2 密钥（仅测试用）：公钥交给 adapter，私钥用来造合法/非法签名。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _alipay_signature(private_pem: str, params: dict[str, str]) -> str:
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    from app.adapters.payments.alipay import _canonical

    private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    message = _canonical({**params, "sign_type": "RSA2"}).encode("utf-8")
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
    return base64.b64encode(signature).decode()


def test_alipay_rsa2_verification_is_verified_locally() -> None:
    """支付宝 RSA2 验签可在本地完整验证（纯密码学，不需要商户号）。"""
    from datetime import datetime
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo

    from app.adapters.payments.alipay import AlipayGateway

    private_pem, public_pem = _rsa_pair()
    gateway = AlipayGateway(_settings(alipay_public_key=public_pem))
    # notify_time 必须在重放窗口内（1 小时），否则会被时间戳检查拒绝。
    notify_time = datetime.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "app_id": "2021000000000000",
        "out_trade_no": "SO20260101001",
        "trade_no": "2026010522001400000000000001",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "199.00",
        "notify_time": notify_time,
        "notify_id": "notify-1",
    }
    signed = {**params, "sign": _alipay_signature(private_pem, params), "sign_type": "RSA2"}
    verdict = gateway.verify_notification_detailed({}, urlencode(signed).encode("utf-8"))
    assert verdict.accepted is True and verdict.reason == "verified"
    payment = verdict.payment
    assert payment is not None
    assert payment.provider == "alipay"
    assert payment.merchant_order_no == "SO20260101001"
    assert payment.amount_cents == 19900
    assert payment.currency == "CNY"

    # 篡改金额 → 验签失败（值参与签名）
    tampered = {**signed, "total_amount": "1.00"}
    verdict = gateway.verify_notification_detailed({}, urlencode(tampered).encode("utf-8"))
    assert verdict.accepted is False and verdict.reason == "signature_mismatch"

    # 换成别人的私钥签名 → 失败
    other_private, _ = _rsa_pair()
    forged = {**params, "sign": _alipay_signature(other_private, params), "sign_type": "RSA2"}
    assert gateway.verify_notification_detailed({}, urlencode(forged).encode("utf-8")).reason == (
        "signature_mismatch"
    )

    # 非成功状态：验签通过但不产生付款结论
    pending_params = {**params, "trade_status": "WAIT_BUYER_PAY"}
    pending = {**pending_params, "sign": _alipay_signature(private_pem, pending_params), "sign_type": "RSA2"}
    verdict = gateway.verify_notification_detailed({}, urlencode(pending).encode("utf-8"))
    assert verdict.accepted is True and verdict.payment is None
    assert verdict.reason == "not_a_success_payment"

    # 缺 sign / 非 RSA2 一律拒绝
    assert gateway.verify_notification_detailed({}, urlencode(params).encode("utf-8")).reason == (
        "signature_missing"
    )
    md5 = {**params, "sign": "x", "sign_type": "MD5"}
    assert gateway.verify_notification_detailed({}, urlencode(md5).encode("utf-8")).reason == (
        "unsupported_sign_type"
    )


def test_alipay_without_public_key_fails_closed() -> None:
    from app.adapters.payments.alipay import AlipayGateway

    gateway = AlipayGateway(_settings(alipay_public_key=""))
    with pytest.raises(GatewayNotConfigured):
        gateway.verify_notification_detailed({}, b"out_trade_no=SO1&sign=x&sign_type=RSA2")


def test_wechat_rejects_incomplete_headers_and_missing_certificate() -> None:
    from app.adapters.payments.wechat import WechatPayGateway

    # 未配置平台证书/APIv3 密钥：连验签材料都没有 → fail closed（抛错而不是放行）。
    unconfigured = WechatPayGateway(_settings())
    with pytest.raises(GatewayNotConfigured):
        unconfigured.verify_notification_detailed({}, b'{"id":"evt"}')

    # 配了 APIv3 密钥但缺签名头：给出稳定原因（不猜、不放行）。
    gateway = WechatPayGateway(_settings(wechat_api_v3_key="k" * 32, wechat_merchant_id="1900000001"))
    assert gateway.verify_notification_detailed({}, b'{"id":"evt"}').reason == "headers_incomplete"
    # 有签名头但没有平台证书：同样 fail closed。
    with pytest.raises(GatewayNotConfigured):
        gateway.verify_notification_detailed(
            {
                "Wechatpay-Timestamp": str(now_ms() // 1000),
                "Wechatpay-Nonce": "n1",
                "Wechatpay-Signature": "forged",
                "Wechatpay-Serial": "UNKNOWN-SERIAL",
            },
            b'{"id":"evt"}',
        )


def test_configuration_errors_are_not_reported_as_business_answers() -> None:
    """**R17 复核发现的跨渠道缺陷**：凭据缺失被 `except GatewayError` 吞成业务结论。

    `GatewayNotConfigured` 是 `GatewayError` 的子类且 ``retryable=False``，因此所有
    「非可重试 → 返回 NOT_FOUND / FAILED / PROCESSING / SUCCEEDED」的分支都会把
    **配置错误**答成**业务答案**：查单变成"渠道没有这笔单"，退款永远停在"处理中"，
    微信关单甚至返回 SUCCEEDED（会被读成可能已支付）。现在各渠道必须在映射之前
    显式重新抛出配置错误。
    """
    from app.adapters.payments.alipay import AlipayGateway
    from app.adapters.payments.paypal import PaypalGateway
    from app.adapters.payments.stripe import StripeGateway
    from app.adapters.payments.wechat import WechatPayGateway

    settings = _settings()
    gateways = [
        WechatPayGateway(settings),
        AlipayGateway(settings),
        PaypalGateway(settings),
        StripeGateway(_settings(stripe_secret_key="")),
    ]
    for gateway in gateways:
        with pytest.raises(GatewayNotConfigured):
            gateway.query("SO20260101001")
        with pytest.raises(GatewayNotConfigured):
            gateway.refund("SO20260101001", "RF1", 100, "测试")
        with pytest.raises(GatewayNotConfigured):
            gateway.query_refund("RF1")


def test_paypal_rejects_incomplete_headers_and_foreign_certificate_url() -> None:
    from app.adapters.payments.paypal import PaypalGateway

    gateway = PaypalGateway(_settings(paypal_client_id="client", paypal_webhook_id="WH-1"))
    assert gateway.verify_notification_detailed({}, b'{"id":"WH-1"}').reason == "headers_incomplete"
    # 证书地址必须落在 PayPal 官方域名：伪造主机直接拒绝（防 SSRF / 伪造证书）。
    verdict = gateway.verify_notification_detailed(
        {
            "PAYPAL-TRANSMISSION-ID": "t1",
            "PAYPAL-TRANSMISSION-TIME": "2026-01-05T00:00:00Z",
            "PAYPAL-TRANSMISSION-SIG": "Zm9yZ2Vk",
            "PAYPAL-CERT-URL": "https://evil.example/cert.pem",
            "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        },
        b'{"id":"WH-1"}',
    )
    assert verdict.accepted is False and verdict.payment is None
    assert verdict.reason in ("certificate_unavailable", "signature_mismatch", "timestamp_out_of_window")


def test_real_channels_need_credentials_for_trading() -> None:
    """三个真实渠道的交易类方法在缺凭据时同样 fail closed（不静默降到沙箱）。"""
    from app.adapters.payments.alipay import AlipayGateway
    from app.adapters.payments.paypal import PaypalGateway
    from app.adapters.payments.wechat import WechatPayGateway

    settings = _settings()
    for gateway in (WechatPayGateway(settings), AlipayGateway(settings), PaypalGateway(settings)):
        with pytest.raises(GatewayNotConfigured):
            gateway.create("SO20260101001", 19900, "CNY", "native", "测试商品", None)
        with pytest.raises(GatewayNotConfigured):
            gateway.download_bill("2026-01-05")
