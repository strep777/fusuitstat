"""短信通道与手机号区号适配回归（R16）。

依据：
- `10_手机号登录安全与合规.md`：手机号规范化为 E.164、验证码只存 HMAC、生产不得使用沙盒；
- `功能确认清单-已确认.md` C03/C05：短信通道随云厂商切换（腾讯 / 阿里），C02/C24：首发中国大陆 + 美国；
- `14_商业部署与运维手册.md`：开发环境使用沙盒通道。

本文件不连接任何真实服务商：签名与请求形状用**替身 HTTP 客户端**断言，
真实通道的业务验收仍属 BLOCKED（R30）。
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import urllib.parse
from typing import Any

import pytest

from app.adapters.base import GatewayError, GatewayNotConfigured
from app.adapters.sms import AliyunSmsGateway, TencentSmsGateway, build_gateway, get_gateway
from app.platform.config import Settings
from app.platform.errors import AppError
from app.platform.security import normalize_phone


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "public_origin": "http://127.0.0.1:5173",
        "sms_sign_name": "弈星纪元",
        "sms_template_code": "SMS_123456",
        "aliyun_sms_access_key_id": "LTAI-test-id",
        "aliyun_sms_access_key_secret": "test-secret",
        "tencent_sms_secret_id": "AKIDtest",
        "tencent_sms_secret_key": "test-key",
        "tencent_sms_sdk_app_id": "1400000000",
        "tencent_sms_template_id": "1234567",
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    """替身 HTTP 客户端：记录请求，返回脚本化响应。"""

    instances: list[FakeClient] = []

    def __init__(self, payload: dict[str, Any], status: int = 200, *, non_json: bool = False) -> None:
        self.payload = payload
        self.status = status
        self.non_json = non_json
        self.calls: list[dict[str, Any]] = []
        FakeClient.instances.append(self)

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.non_json:
            class _Bad(FakeResponse):
                def json(self) -> dict[str, Any]:
                    raise ValueError("not json")

            return _Bad(self.payload, self.status)
        return FakeResponse(self.payload, self.status)


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch):
    """把 ``httpx.Client`` 换成本文件的替身，避免任何真实网络调用。"""

    def _install(payload: dict[str, Any], status: int = 200, *, non_json: bool = False) -> FakeClient:
        FakeClient.instances = []
        client = FakeClient(payload, status, non_json=non_json)
        monkeypatch.setattr("app.adapters.sms.httpx.Client", lambda **_kwargs: client)
        return client

    return _install


# ------------------------------------------------------------------ 阿里云
def test_aliyun_signature_is_reproducible_and_covers_business_params(fake_http):
    client = fake_http({"Code": "OK", "RequestId": "req-1", "BizId": "biz-1"})
    gateway = AliyunSmsGateway(_settings())

    result = gateway.send_code(to="+8613800000000", code="246810", purpose="login")

    assert result.provider == "aliyun" and result.message_id == "req-1"
    call = client.calls[0]
    assert call["url"] == "https://dysmsapi.aliyuncs.com/"
    payload = call["data"]
    assert payload["Action"] == "SendSms"
    assert payload["PhoneNumbers"] == "8613800000000"  # 不带 `+`
    assert payload["SignName"] == "弈星纪元"
    assert payload["TemplateCode"] == "SMS_123456"
    assert json.loads(payload["TemplateParam"]) == {"code": "246810"}
    # 按参数名排序并用 RFC3986 编码后 HMAC-SHA1（AccessKeySecret + "&" 为密钥）
    signed = {key: value for key, value in payload.items() if key != "Signature"}
    canonical = "&".join(
        f"{urllib.parse.quote(str(key), safe='~')}={urllib.parse.quote(str(signed[key]), safe='~')}"
        for key in sorted(signed)
    )
    string_to_sign = "POST&%2F&" + urllib.parse.quote(canonical, safe="~")
    expected = base64.b64encode(
        hmac.new(b"test-secret&", string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    assert payload["Signature"] == expected
    assert payload["SignatureMethod"] == "HMAC-SHA1"
    assert payload["Timestamp"].endswith("Z")


def test_aliyun_provider_error_becomes_gateway_error(fake_http):
    fake_http({"Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "触发流控"})
    with pytest.raises(GatewayError) as error:
        AliyunSmsGateway(_settings()).send_code(to="+8613800000000", code="111111", purpose="login")
    assert "isv.BUSINESS_LIMIT_CONTROL" in str(error.value)


def test_aliyun_requires_credentials_and_notice_template(fake_http):
    fake_http({"Code": "OK"})
    with pytest.raises(GatewayNotConfigured):
        AliyunSmsGateway(_settings(aliyun_sms_access_key_id="")).send_code(
            to="+8613800000000", code="111111", purpose="login"
        )
    with pytest.raises(GatewayNotConfigured):
        AliyunSmsGateway(_settings(sms_notice_template_code="")).send_text(
            to="+8613800000000", text="订单已发货"
        )


# ------------------------------------------------------------------ 腾讯云
def test_tencent_tc3_signature_and_request_shape(fake_http):
    client = fake_http(
        {"Response": {"RequestId": "req-2", "SendStatusSet": [{"Code": "Ok", "SerialNo": "sn-1"}]}}
    )
    gateway = TencentSmsGateway(_settings())

    result = gateway.send_code(to="+8613800000000", code="135790", purpose="login")

    assert result.provider == "tencent" and result.message_id == "sn-1"
    call = client.calls[0]
    assert call["url"] == "https://sms.tencentcloudapi.com/"
    headers = call["headers"]
    assert headers["X-TC-Action"] == "SendSms"
    assert headers["X-TC-Version"] == "2021-01-11"
    assert headers["X-TC-Region"] == "ap-guangzhou"
    assert headers["Host"] == "sms.tencentcloudapi.com"
    body = json.loads(call["content"])
    assert body["PhoneNumberSet"] == ["+8613800000000"]
    assert body["SmsSdkAppId"] == "1400000000"
    assert body["TemplateId"] == "1234567"
    assert body["TemplateParamSet"][0] == "135790"
    assert body["TemplateParamSet"][1].isdigit(), "第二个参数是有效期（分钟）"

    # 按腾讯云签名 v3 独立复算：规范化请求 → 待签名字符串 → 派生密钥 → 签名
    timestamp = int(headers["X-TC-Timestamp"])
    canonical_headers = "content-type:application/json; charset=utf-8\nhost:sms.tencentcloudapi.com\n"
    hashed_payload = hashlib.sha256(call["content"].encode()).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, "content-type;host", hashed_payload]
    )
    date = dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).strftime("%Y-%m-%d")
    scope = f"{date}/sms/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    secret_date = _sign(b"TC3test-key", date)
    secret_service = _sign(secret_date, "sms")
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    assert headers["Authorization"] == (
        f"TC3-HMAC-SHA256 Credential=AKIDtest/{scope}, "
        f"SignedHeaders=content-type;host, Signature={signature}"
    )


def test_tencent_provider_error_and_per_number_failure(fake_http):
    fake_http({"Response": {"Error": {"Code": "FailedOperation.PhoneNumberInBlacklist"}}})
    with pytest.raises(GatewayError) as error:
        TencentSmsGateway(_settings()).send_code(to="+8613800000000", code="111111", purpose="login")
    assert "PhoneNumberInBlacklist" in str(error.value)

    fake_http({"Response": {"SendStatusSet": [{"Code": "LimitExceeded.PhoneNumberDailyLimit"}]}})
    with pytest.raises(GatewayError) as per_number:
        TencentSmsGateway(_settings()).send_code(to="+8613800000000", code="111111", purpose="login")
    assert "PhoneNumberDailyLimit" in str(per_number.value)


def test_tencent_requires_credentials(fake_http):
    fake_http({"Response": {}})
    with pytest.raises(GatewayNotConfigured):
        TencentSmsGateway(_settings(tencent_sms_secret_id="")).send_code(
            to="+8613800000000", code="111111", purpose="login"
        )


# ------------------------------------------------------------------ 通道选择与沙盒边界
def test_build_gateway_never_silently_falls_back_to_sandbox():
    assert isinstance(build_gateway("aliyun", _settings()), AliyunSmsGateway)
    assert isinstance(build_gateway("tencent", _settings()), TencentSmsGateway)
    assert build_gateway("mock", _settings()).name == "sandbox"
    with pytest.raises(GatewayNotConfigured):
        build_gateway("stripe", _settings())


def test_development_uses_sandbox_and_production_rejects_mock(monkeypatch: pytest.MonkeyPatch):
    import app.adapters.sms as sms_module

    sms_module.reset_gateway()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SMS_ADAPTER", "aliyun")
    from app.platform.config import get_settings

    get_settings.cache_clear()
    try:
        assert get_gateway().name == "sandbox", "开发环境统一走沙盒通道"
    finally:
        sms_module.reset_gateway()
        get_settings.cache_clear()

    # 生产环境使用 mock 或缺少所选通道的凭据都会被配置校验直接拒绝
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            app_env="production",
            public_origin="https://starium.cn",
            session_secret="x" * 40,
            csrf_secret="y" * 40,
            otp_secret="z" * 40,
            phone_lookup_key="p" * 40,
            pii_encryption_key="q" * 40,
            cookie_secure=True,
            sms_adapter="mock",
            payment_providers="wechat",
            storage_backend="s3",
            smtp_host="mail.test",
        )
    with pytest.raises(ValueError) as missing:
        Settings(
            _env_file=None,
            app_env="production",
            public_origin="https://starium.cn",
            session_secret="x" * 40,
            csrf_secret="y" * 40,
            otp_secret="z" * 40,
            phone_lookup_key="p" * 40,
            pii_encryption_key="q" * 40,
            cookie_secure=True,
            sms_adapter="tencent",
            payment_providers="wechat",
            storage_backend="s3",
            smtp_host="mail.test",
        )
    assert "TENCENT_SMS" in str(missing.value)


# ------------------------------------------------------------------ 中美区号适配
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13800000000", "+8613800000000"),
        ("+86 138 0000 0000", "+8613800000000"),
        ("8613800000000", "+8613800000000"),
        ("(415) 555-2671", "+14155552671"),
        ("+1 415 555 2671", "+14155552671"),
        ("1 415 555 2671", "+14155552671"),
        ("14155552671", "+14155552671"),
        ("4155552671", "+14155552671"),
    ],
)
def test_phone_normalization_for_cn_and_us(raw: str, expected: str):
    assert normalize_phone(raw) == expected


def test_us_number_with_country_code_is_not_mistaken_for_a_cn_number():
    """回归：``14155552671`` 之前会被当成中国大陆号码（+8614155552671）。"""
    assert normalize_phone("14155552671") == "+14155552671"


@pytest.mark.parametrize(
    "raw",
    [
        "12800000000",  # 中国大陆手机号第二位不能是 2
        "1380000000",  # 位数不足
        "0555123456",  # NANP 区号首位不能是 0
        "415555267",  # NANP 位数不足
        "5551234567",  # NANP 局号首位不能是 1（555-123-4567 不是合法号码）
        "+86138000000000000",  # 超出 E.164 长度
    ],
)
def test_invalid_numbers_are_rejected(raw: str):
    with pytest.raises(AppError) as error:
        normalize_phone(raw)
    assert error.value.code == "VALIDATION_ERROR"


def test_region_allowlist_blocks_unsupported_countries(monkeypatch: pytest.MonkeyPatch):
    from app.platform.config import get_settings

    monkeypatch.setenv("SMS_REGIONS", "CN")
    get_settings.cache_clear()
    try:
        assert normalize_phone("13800000000") == "+8613800000000"
        with pytest.raises(AppError) as error:
            normalize_phone("+14155552671")
        assert error.value.fields["phone"] == "不支持的地区：US"
    finally:
        monkeypatch.delenv("SMS_REGIONS", raising=False)
        get_settings.cache_clear()
