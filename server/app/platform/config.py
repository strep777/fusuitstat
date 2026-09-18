"""应用配置。生产启动拒绝不安全组合（fail closed）。

前端 ``VITE_*`` 只允许公开配置；本文件中的任何密钥都不得写入静态构建包。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.platform.payment_providers import SANDBOX_PROVIDERS

AdapterName = Literal["mock", "aliyun", "tencent", "wechat", "alipay", "paypal", "stripe", "unsupported"]

SERVER_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SERVER_DIR.parent

_DEV_FALLBACK_SEED = "dev-only-insecure-seed"


class Settings(BaseSettings):
    """全部运行配置。键名与 ``文档/14_商业部署与运维手册.md`` 的配置表一致。"""

    model_config = SettingsConfigDict(
        env_file=(SERVER_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 模式与站点 ----
    app_env: Literal["development", "test", "staging", "production"] = "development"
    public_origin: str = "http://127.0.0.1:5173"
    api_prefix: str = "/api/v1"
    site_name: str = "弈星纪元兽装工作室"
    legal_entity: str = "弈星纪元（上海）文化发展有限公司"
    brand_domain: str = "starium.cn"
    support_email: str = "support@starium.cn"
    default_locale: Literal["zh-CN", "en-US"] = "zh-CN"
    default_currency: Literal["CNY", "USD"] = "CNY"
    timezone_display: str = "Asia/Shanghai"

    # ---- 数据库与数据目录 ----
    data_dir: Path = SERVER_DIR / "var"
    database_url: str | None = None
    sqlite_busy_timeout_ms: int = 5000
    sqlite_synchronous: Literal["FULL", "NORMAL"] = "FULL"
    db_echo: bool = False

    # ---- 会话与密钥 ----
    session_secret: str = ""
    csrf_secret: str = ""
    otp_secret: str = ""
    phone_lookup_key: str = ""
    pii_encryption_key: str = ""
    pii_encryption_key_version: str = "v1"
    session_absolute_hours: int = 24 * 7
    session_idle_hours: int = 24
    sensitive_reauth_minutes: int = 10
    cookie_secure: bool = False

    # ---- 员工后台 ----
    admin_session_hours: int = 8
    mfa_issuer: str = "Starium Admin"

    # ---- 短信 ----
    sms_adapter: AdapterName = "mock"
    sms_sign_name: str = "弈星纪元"
    sms_template_code: str = ""
    # 通知类短信模板（非验证码）。留空表示只发验证码，通知走邮件（C27 邮件必选）。
    sms_notice_template_code: str = ""
    # 允许接收验证码的国家码（C02/C24 双区：中国大陆 + 美国）。
    sms_regions: str = "CN,US"
    # 阿里云短信（RPC 签名，C03/C05：通道随云厂商切换）
    aliyun_sms_access_key_id: str = ""
    aliyun_sms_access_key_secret: str = ""
    aliyun_sms_endpoint: str = "https://dysmsapi.aliyuncs.com/"
    # 腾讯云短信（TC3-HMAC-SHA256 签名）
    tencent_sms_secret_id: str = ""
    tencent_sms_secret_key: str = ""
    tencent_sms_sdk_app_id: str = ""
    tencent_sms_template_id: str = ""
    tencent_sms_notice_template_id: str = ""
    tencent_sms_region: str = "ap-guangzhou"
    tencent_sms_endpoint: str = "https://sms.tencentcloudapi.com/"
    sms_sender_email: str = "no-reply@starium.cn"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    otp_ttl_seconds: int = 300
    otp_resend_seconds: int = 60
    otp_max_verify_attempts: int = 5
    otp_phone_hourly_limit: int = 5
    otp_phone_daily_limit: int = 10
    otp_ip_hourly_limit: int = 20
    otp_daily_budget: int = 2000
    # 数据保留（C22 / 10 文档「隐私和保留草案」；草案值，C22 定稿后调整）
    # 注销后多久执行可删除资料清理：与注销页面展示的「清理计划完成时间」一致。
    retention_closure_days: int = 30
    # 未提交询价草稿与无引用附件的保留窗口（周期清理用）。
    retention_draft_days: int = 30

    # ---- 支付 ----
    payment_providers: str = "mock"
    payment_return_url: str = ""
    wechat_merchant_id: str = ""
    wechat_app_id: str = ""
    wechat_api_v3_key: str = ""
    wechat_platform_cert_serial: str = ""
    alipay_app_id: str = ""
    alipay_public_key: str = ""
    paypal_client_id: str = ""
    paypal_webhook_id: str = ""
    installment_payment_ttl_minutes: int = 30

    # ---- 存储 ----
    storage_backend: Literal["local", "s3"] = "local"
    storage_public_prefix: str = "public"
    storage_private_prefix: str = "private"
    storage_s3_endpoint: str = ""
    storage_s3_bucket_public: str = ""
    storage_s3_bucket_private: str = ""
    signed_url_ttl_seconds: int = 60
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_max_files: int = 3
    upload_max_edge: int = 8192
    upload_max_pixels: int = 40_000_000

    # ---- 业务可配置默认值（后台可改，见功能确认清单 C06/C08/C09/C13/C14）----
    default_prepayment_percent: int = 50
    quote_valid_days: int = 7
    slot_hold_minutes: int = 30
    balance_due_days: int = 7
    free_revision_count: int = 2
    points_per_currency_unit: int = 5
    points_release_delay_days: int = 7
    feedback_reward_minor: int = 5
    feedback_reward_normal: int = 20
    feedback_reward_major: int = 50
    feedback_daily_reward_limit: int = 1
    feedback_daily_submit_limit: int = 10
    feedback_hourly_submit_limit: int = 3

    # ---- 功能开关（默认安全值，商业门槛未落实时保持关闭）----
    feature_online_payment: bool = False
    feature_points_redemption: bool = False
    feature_coupons: bool = False
    feature_rush_rounds: bool = False
    feature_client_stories: bool = False
    feature_events: bool = False
    feature_en_locale: bool = False
    feature_marketing_email: bool = False

    # ---- 运维 ----
    outbox_poll_seconds: float = 2.0
    outbox_lease_seconds: int = 120
    outbox_max_attempts: int = 8
    worker_concurrency: int = 1
    log_level: str = "INFO"
    trusted_proxy_ips: str = "127.0.0.1"

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (SERVER_DIR / value).resolve()

    @model_validator(mode="after")
    def _enforce_environment_safety(self) -> Settings:
        if self.app_env == "production":
            problems: list[str] = []
            if self.public_origin.startswith("http://"):
                problems.append("PUBLIC_ORIGIN 必须是 https（生产环境禁止明文 HTTP）")
            if "localhost" in self.public_origin or "127.0.0.1" in self.public_origin:
                problems.append("PUBLIC_ORIGIN 不能是本地地址")
            for name in (
                "session_secret",
                "csrf_secret",
                "otp_secret",
                "phone_lookup_key",
                "pii_encryption_key",
            ):
                value = getattr(self, name)
                if len(value) < 32 or value.startswith("dev-") or value == _DEV_FALLBACK_SEED:
                    problems.append(f"{name.upper()} 缺失、过短或使用了开发默认值")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE 必须为 true")
            if self.sms_adapter == "mock":
                problems.append("SMS_ADAPTER=mock 不允许在生产启用")
            problems.extend(self._sms_provider_problems())
            if set(self._provider_list()).intersection(SANDBOX_PROVIDERS):
                problems.append("PAYMENT_PROVIDERS 不允许包含 mock 或 sandbox")
            if self.storage_backend == "local":
                problems.append("STORAGE_BACKEND=local 不允许生产（须对象存储 + CDN）")
            if not self.smtp_host:
                problems.append("SMTP_HOST 缺失（C27：邮件为必选通知通道）")
            if problems:
                raise ValueError("生产配置不安全：" + "；".join(problems))
        return self

    # ---- 派生属性 ----
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "test"

    def _provider_list(self) -> list[str]:
        return [item.strip().lower() for item in self.payment_providers.split(",") if item.strip()]

    @property
    def allowed_sms_regions(self) -> list[str]:
        """允许接收验证码的国家码（大写，去重保序）。"""
        seen: list[str] = []
        for item in self.sms_regions.split(","):
            code = item.strip().upper()
            if code and code not in seen:
                seen.append(code)
        return seen

    def _sms_provider_problems(self) -> list[str]:
        """生产环境所选短信通道必须具备该通道自己的凭据，缺一条都不放行。"""
        problems: list[str] = []
        if self.sms_adapter == "aliyun":
            if not self.aliyun_sms_access_key_id or not self.aliyun_sms_access_key_secret:
                problems.append("SMS_ADAPTER=aliyun 需要 ALIYUN_SMS_ACCESS_KEY_ID/SECRET")
            if not self.sms_template_code:
                problems.append("SMS_ADAPTER=aliyun 需要 SMS_TEMPLATE_CODE")
        elif self.sms_adapter == "tencent":
            if not self.tencent_sms_secret_id or not self.tencent_sms_secret_key:
                problems.append("SMS_ADAPTER=tencent 需要 TENCENT_SMS_SECRET_ID/SECRET_KEY")
            if not self.tencent_sms_sdk_app_id or not self.tencent_sms_template_id:
                problems.append("SMS_ADAPTER=tencent 需要 TENCENT_SMS_SDK_APP_ID/TEMPLATE_ID")
        if not self.allowed_sms_regions:
            problems.append("SMS_REGIONS 不能为空（至少允许一个区号）")
        if "mock" in self._provider_list():
            problems.append("PAYMENT_PROVIDERS 不允许包含 mock")
        return problems

    @property
    def enabled_payment_providers(self) -> list[str]:
        return self._provider_list()

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{(self.data_dir / 'starium.db').as_posix()}"

    @property
    def storage_root(self) -> Path:
        return self.data_dir / "storage"

    @property
    def trusted_proxies(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_ips.split(",") if item.strip()]

    def secret(self, name: str) -> str:
        """读取密钥；开发/测试环境缺失时生成进程内稳定值（不落盘、不写日志）。"""
        value = getattr(self, name)
        if value:
            return str(value)
        if self.is_production:
            raise RuntimeError(f"{name.upper()} 未配置")
        cache = _DEV_SECRETS.setdefault(name, secrets.token_hex(32))
        return cache


_DEV_SECRETS: dict[str, str] = {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试辅助：清空配置缓存。"""
    get_settings.cache_clear()
