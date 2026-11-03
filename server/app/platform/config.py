"""应用配置。生产启动拒绝不安全组合（fail closed）。

前端 ``VITE_*`` 只允许公开配置；本文件中的任何密钥都不得写入静态构建包。
"""

from __future__ import annotations

import contextlib
import json
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
    # Stripe（C04 信用卡）：密钥不留空即视为已配置；缺任一凭据时适配器 fail closed。
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    #: 平台账户标识（``acct_...``）：用于核对渠道侧账户，未配置则跳过该项核对。
    stripe_account_id: str = ""
    #: 固定 API 版本，避免 Stripe 默认版本漂移导致回调结构变化。
    stripe_api_version: str = "2024-06-20"
    installment_payment_ttl_minutes: int = 30

    # ---- 电子签（C07：合同全部电子签，网站内通过邮件发送合同链接完成签署）----
    #: ``disabled`` = 未配置（不发送、不假装已签）；``sandbox`` = 本地联调通道（生产禁止）；
    #: 其余为真实厂商名，必须同时给出该厂商的凭据，否则拒绝启动。
    esign_provider: str = "disabled"
    esign_api_base: str = ""
    esign_app_id: str = ""
    esign_app_secret: str = ""
    #: 回调验签密钥（由电子签平台为回调地址签发）。
    esign_webhook_secret: str = ""
    #: 签署链接有效期（小时）：超时未签完的合同按过期处理，避免无限期挂起。
    esign_link_ttl_hours: int = 72

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

    # ---- 防爬虫、并发与请求体（19 文档）----
    security_guard_enabled: bool = True
    #: 测试默认关闭全局限额与 UA 拦截，避免误伤功能测试；安全用例显式打开。
    security_guard_test_mode: bool = False
    max_concurrent_requests: int = 128
    concurrency_queue_ms: int = 80
    max_json_body_bytes: int = 1024 * 1024
    max_multipart_body_bytes: int = 32 * 1024 * 1024
    rate_limit_global_per_minute: int = 300
    rate_limit_read_per_minute: int = 240
    rate_limit_search_per_minute: int = 90
    rate_limit_write_per_minute: int = 30
    rate_limit_admin_per_minute: int = 60
    crawler_block_enabled: bool = False
    crawler_blocklist: str = ""
    admin_ip_allowlist: str = ""
    public_cache_seconds: int = 15

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
            problems.extend(self._payment_provider_problems())
            problems.extend(self._esign_provider_problems())
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

    def _payment_provider_problems(self) -> list[str]:
        """生产环境所选支付渠道必须具备该渠道自己的凭据，缺一条都不放行。

        与短信侧同一口径：**不允许「配置了渠道但没有凭据」的中间态**，
        否则上线后表现为"入口存在但一点就失败"，比明确关闭更糟。
        """
        problems: list[str] = []
        enabled = set(self._provider_list())
        if "stripe" in enabled:
            if not self.stripe_secret_key.strip():
                problems.append("PAYMENT_PROVIDERS 含 stripe 时需要 STRIPE_SECRET_KEY")
            if not self.stripe_webhook_secret.strip():
                problems.append("PAYMENT_PROVIDERS 含 stripe 时需要 STRIPE_WEBHOOK_SECRET")
            if not self.stripe_account_id.strip():
                problems.append("PAYMENT_PROVIDERS 含 stripe 时需要 STRIPE_ACCOUNT_ID（用于核对渠道账户）")
        if "wechat" in enabled and not (self.wechat_merchant_id and self.wechat_api_v3_key):
            problems.append("PAYMENT_PROVIDERS 含 wechat 时需要 WECHAT_MERCHANT_ID/WECHAT_API_V3_KEY")
        if "alipay" in enabled and not (self.alipay_app_id and self.alipay_public_key):
            problems.append("PAYMENT_PROVIDERS 含 alipay 时需要 ALIPAY_APP_ID/ALIPAY_PUBLIC_KEY")
        if "paypal" in enabled and not (self.paypal_client_id and self.paypal_webhook_id):
            problems.append("PAYMENT_PROVIDERS 含 paypal 时需要 PAYPAL_CLIENT_ID/PAYPAL_WEBHOOK_ID")
        return problems

    def _esign_provider_problems(self) -> list[str]:
        """电子签渠道（C07）：商业上线必须是真的电子签服务，不能是"未配置"或沙箱。"""
        problems: list[str] = []
        name = self.esign_provider.strip().lower()
        if name in ("", "disabled"):
            problems.append(
                "ESIGN_PROVIDER 未配置：C07 要求合同全部电子签，未接入电子签渠道不允许商业上线"
            )
            return problems
        if name in SANDBOX_PROVIDERS:
            problems.append("ESIGN_PROVIDER 不允许使用沙箱/模拟通道（签署状态必须来自真实平台）")
            return problems
        if not self.esign_app_id or not self.esign_app_secret:
            problems.append(f"ESIGN_PROVIDER={name} 需要 ESIGN_APP_ID/ESIGN_APP_SECRET")
        if not self.esign_webhook_secret:
            problems.append(f"ESIGN_PROVIDER={name} 需要 ESIGN_WEBHOOK_SECRET（回调验签）")
        if not self.esign_api_base.startswith("https://"):
            problems.append(f"ESIGN_PROVIDER={name} 需要 HTTPS 的 ESIGN_API_BASE")
        return problems

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

    @property
    def crawler_block_active(self) -> bool:
        """生产强制开启；测试/开发只在显式配置时开启。"""
        return self.crawler_block_enabled or self.is_production

    @property
    def allowed_hosts(self) -> list[str]:
        """生产 Host 校验：主域、www 与 public_origin 的主机。"""
        hosts = {"starium.cn", "www.starium.cn"}
        for origin in (self.public_origin, f"https://{self.brand_domain}"):
            try:
                host = origin.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
                if host:
                    hosts.add(host)
            except IndexError:
                continue
        return sorted(hosts)

    @property
    def admin_networks(self) -> list[str]:
        return [item.strip() for item in self.admin_ip_allowlist.split(",") if item.strip()]

    def secret(self, name: str) -> str:
        """读取密钥；开发环境缺失时生成并在 ``data_dir`` 内持久化。

        只对 ``development`` 生效：重启后仍能解密本机演示库里的 PII/MFA 密文。
        ``test`` 保持进程内随机值；生产/预发布必须显式配置，缺失即失败。
        """
        value = getattr(self, name)
        if value:
            return str(value)
        if self.is_production:
            raise RuntimeError(f"{name.upper()} 未配置")
        if self.app_env == "development":
            return self._dev_secret(name)
        return _DEV_SECRETS.setdefault(name, secrets.token_hex(32))

    def _dev_secret(self, name: str) -> str:
        """本地开发密钥文件：仅本机、不入库、不写日志。"""
        path = self.data_dir / ".dev-secrets.json"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get(name), str) and data[name]:
                    return data[name]
            else:
                data = {}
        except (OSError, ValueError):
            # 文件损坏或不可读时回退到进程内值，并尝试覆盖重建。
            data = {}
        data[name] = secrets.token_hex(32)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            with contextlib.suppress(OSError):
                tmp.chmod(0o600)
            tmp.replace(path)
        except OSError:
            _DEV_SECRETS.setdefault(name, data[name])
        return data[name]


_DEV_SECRETS: dict[str, str] = {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试辅助：清空配置缓存。"""
    get_settings.cache_clear()
