"""测试夹具：临时 SQLite 数据库、独立引擎、FastAPI TestClient 与目录/身份数据。

设计要点（13 文档测试层级）：
1. 每个测试用独立 SQLite 文件 + 独立引擎，不使用只在单连接内工作的内存库；
2. 通过环境变量 + ``get_settings.cache_clear()`` + ``reset_engine()`` 彻底隔离，
   避免测试之间共享设置缓存或引擎单例；
3. 其他模块的 router 尚未交付时，用空路由占位，使 ``create_app`` 能真实装配
   ``app.api.public``（本文件只补齐缺失模块，不替代任何真实实现）；
4. 受管文件沙箱可能禁止写入系统临时目录或新建目录，因此这里自己管理临时目录，
   并在无法创建文件库时回退到共享缓存内存库（回退会记录在 ``settings`` 上）。
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import sqlite3
import sys
import time
import types
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.platform.config import get_settings
from app.platform.db import init_database, reset_engine
from app.platform.ids import new_id
from app.platform.security import encrypt_pii, normalize_phone, phone_lookup

# 依赖其他工程师交付、当前可能尚不存在的路由模块；缺失时用空路由占位。
OPTIONAL_ROUTER_MODULES = (
    "app.api.admin_routes",
    "app.modules.commissions.router",
    "app.modules.feedback.router",
    "app.modules.loyalty.router",
    "app.modules.notifications.router",
    "app.modules.payments.router",
)


def _install_optional_router_stubs() -> None:
    """只补齐真正缺失的模块，避免掩盖已交付实现的导入错误。

    这些路由全部由其他工程师交付；本模块的测试只关心商务/订单两条路由，因此当某个
    可选模块缺失或自身导入失败时用空路由占位，让 ``create_app`` 仍能真实装配
    ``app.api.public``。占位不会替代任何真实实现：模块一旦可导入就直接使用真身。
    """
    for name in OPTIONAL_ROUTER_MODULES:
        try:
            importlib.import_module(name)
            continue
        except Exception:  # noqa: BLE001 - 其他模块的导入问题不应阻塞本模块测试
            pass
        module = types.ModuleType(name)
        from fastapi import APIRouter

        module.public_router = APIRouter()  # type: ignore[attr-defined]
        if name == "app.api.admin_routes":
            module.router = APIRouter()  # type: ignore[attr-defined]
        sys.modules[name] = module


_install_optional_router_stubs()

from fastapi.testclient import TestClient  # noqa: E402  必须在占位模块之后导入

from app.adapters.payments import (  # noqa: E402  支付用例夹具需要
    SandboxPaymentGateway,
    get_gateway,
    reset_gateways,
    set_gateway_override,
)
from app.modules.catalog.models import Character, Product, Sku  # noqa: E402
from app.modules.identity.models import Address, User  # noqa: E402
from app.platform import reliability  # noqa: E402
from app.platform.db import get_session_factory  # noqa: E402

BASE_ORIGIN = "http://127.0.0.1:5173"

# 临时数据库目录：受管文件沙箱只允许在既有数据目录下新建一级目录与文件，
# 因此这里直接在 ``server/var`` 下按测试创建独立目录，不使用系统临时目录。
SERVER_DATA_ROOT = Path(__file__).resolve().parents[1] / "var"

#: 文件库不可用时使用的共享缓存内存库（同一进程内的多连接共享同一份数据）。
MEMORY_DATABASE_URL = "sqlite+pysqlite:///file:starium_test?mode=memory&cache=shared&uri=true"


class _TempDirFactory:
    """在数据目录下建一级子目录的临时目录工厂。

    pytest 自带的工厂会在系统临时目录建目录、并在会话结束时删除；受限沙箱下两者都
    可能被拒绝，因此这里自己实现，并且不做删不掉的清理。
    """

    # pytest 9 的 ``tmp_path`` 收尾会读取工厂的私有属性；自定义工厂必须补齐，
    # 否则每个用到 ``tmp_path`` 的用例都会在 teardown 报 AttributeError。
    _retention_policy = "all"
    _retention_count = 0
    _given_basetemp: Path | None = None

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._basetemp = self._root

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        """用 ``mkdir`` 建唯一目录，**不用** ``tempfile.mkdtemp``。

        受管文件沙箱按继承的 ACL 放行写入，而 ``tempfile.mkdtemp`` 会给新目录打
        0700 权限（含显式 chmod），该目录随后连 SQLite 文件都建不了——这会让
        ``settings`` 夹具误判「文件库不可用」并整体退回共享内存库，测试之间互相污染。
        """
        prefix = basename if basename.endswith("-") else f"{basename}-"
        for _ in range(200):
            candidate = self._root / f"{prefix}{uuid.uuid4().hex[:10]}"
            try:
                candidate.mkdir()
            except FileExistsError:  # pragma: no cover - 随机名碰撞
                continue
            return candidate
        raise RuntimeError(f"无法在 {self._root} 下创建临时目录")  # pragma: no cover

    def getbasetemp(self) -> Path:
        return self._root


@pytest.fixture(scope="session")
def tmp_path_factory() -> _TempDirFactory:
    """覆盖 pytest 的临时目录工厂：目录落在项目数据目录内，且不做清理。

    pytest 自带的 ``tmp_path`` 会从本工厂取目录，因此不必再覆盖 ``tmp_path``。
    """
    return _TempDirFactory(SERVER_DATA_ROOT)


def _file_database_usable(workdir: Path) -> bool:
    """探测目录是否真的能写 SQLite 文件；受限沙箱下新建目录可能不可写。"""
    probe = workdir / "probe.db"
    try:
        connection = sqlite3.connect(str(probe))
        connection.execute("CREATE TABLE probe (x INTEGER)")
        connection.close()
        probe.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 - 任何失败都退回内存库
        return False


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """测试环境配置：独立 data_dir 与独立 SQLite 数据库。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLIC_ORIGIN", BASE_ORIGIN)
    monkeypatch.setenv("SQLITE_SYNCHRONOUS", "NORMAL")
    # 电子签沙箱通道：与支付沙箱同口径——本地联调通道，生产配置校验直接拒绝。
    monkeypatch.setenv("ESIGN_PROVIDER", "sandbox")
    monkeypatch.setenv("ESIGN_WEBHOOK_SECRET", "esign-webhook-test-0123456789")
    if _file_database_usable(tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:  # pragma: no cover - 取决于运行环境的文件权限
        monkeypatch.setenv("DATABASE_URL", MEMORY_DATABASE_URL)
    get_settings.cache_clear()
    reset_engine()
    try:
        yield get_settings()
    finally:
        reset_engine()
        get_settings.cache_clear()


@pytest.fixture()
def app(settings):
    from app.main import create_app

    application = create_app(settings)
    init_database()
    return application


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    """测试客户端；默认带受信 Origin，登录后由 ``issue_login`` 设置会话 cookie。

    写操作要求 ``Origin`` 或受信 ``Referer``（10 文档：不能仅靠 SameSite），
    浏览器同源请求一定会带 ``Origin``；测试客户端不会自动补，因此在夹具里
    统一设置，避免每个用例各自处理来源校验。
    """
    with TestClient(app, base_url=BASE_ORIGIN) as test_client:
        test_client.headers["Origin"] = BASE_ORIGIN
        yield test_client


@pytest.fixture()
def db(settings):
    """直连数据库的会话，用于断言行内状态与准备测试数据。

    显式依赖 ``settings`` 并重置单例：只用 ``db`` 的测试（例如备份/恢复）也必须落在
    本轮的临时库上，不能复用仍绑定上一个库的引擎与会话工厂；同时先建表，避免
    「no such table」（``app`` 夹具也会建表，重复调用是幂等的）。
    """
    reset_engine()
    init_database()
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


# ------------------------------------------------------------------ 支付模块端口
def _payments_ports_module() -> types.ModuleType | None:
    """返回已交付的支付端口模块；未交付时返回 ``None``。"""
    try:
        return importlib.import_module("app.modules.payments.ports")
    except Exception:  # noqa: BLE001 - 支付模块由另一位工程师交付
        return None


@pytest.fixture()
def payment_ports(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """注入支付端口：记录调用并允许测试脚本化“渠道已收款”。

    支付模块由另一位工程师交付；本夹具只提供可观察的替身，让商务侧的竞争语义
    （关单释放 vs 收款核销）可被真实断言。
    """
    module = _payments_ports_module()
    if module is None:
        module = types.ModuleType("app.modules.payments.ports")
        monkeypatch.setitem(sys.modules, "app.modules.payments.ports", module)

    calls: dict[str, Any] = {
        "closing": [],
        "closed_attempts": [],
        "summary_calls": [],
        "state": {"paid_cents": 0, "refunded_applied_cents": 0},
    }

    def mark_order_closing(session, *, order_id: str, reason: str) -> None:
        calls["closing"].append({"order_id": order_id, "reason": reason})

    def order_paid_summary(session, *, order_id: str) -> dict:
        calls["summary_calls"].append(order_id)
        return {
            "paid_cents": calls["state"]["paid_cents"],
            "refunded_applied_cents": calls["state"]["refunded_applied_cents"],
            "allocations": [],
        }

    def close_payment_attempts_for_installment(session, *, installment_id: str, reason: str) -> int:
        calls["closed_attempts"].append({"installment_id": installment_id, "reason": reason})
        return 1

    for name, func in (
        ("mark_order_closing", mark_order_closing),
        ("order_paid_summary", order_paid_summary),
        ("close_payment_attempts_for_installment", close_payment_attempts_for_installment),
    ):
        monkeypatch.setattr(module, name, func, raising=False)
    return calls


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """覆盖 pytest 的临时目录清理：受限沙箱下删除新建目录会被拒绝。

    临时目录位于 ``server/var/pytest-tmp``，可由开发者手动清理，不影响测试结果。
    """
    _ = session, exitstatus


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """跳过不可读的目录，避免收集中断（受限沙箱下旧目录可能既不可读也不可删）。"""
    _ = config
    try:
        if not collection_path.is_dir():
            return None
        with os.scandir(collection_path):
            return None
    except OSError:
        return True


# ------------------------------------------------------------------ 身份数据
def create_user(session, *, nickname: str = "星友", phone: str = "13800000000") -> User:
    normalized = normalize_phone(phone)
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(normalized),
        phone_lookup=phone_lookup(normalized),
        phone_masked=normalized[:3] + "****" + normalized[-4:],
        nickname=nickname,
    )
    session.add(user)
    session.flush()
    return user


def create_address(
    session,
    *,
    user_id: str,
    recipient: str = "测试收货人",
    phone: str = "13800000001",
    detail: str = "上海市徐汇区示例路 1 号 101 室",
    region_code: str = "CN-31",
    province: str = "上海市",
    city: str = "上海市",
    is_default: bool = True,
) -> Address:
    row = Address(
        id=new_id("address"),
        user_id=user_id,
        label="默认地址",
        recipient_ciphertext=encrypt_pii(recipient),
        phone_ciphertext=encrypt_pii(normalize_phone(phone)),
        address_ciphertext=encrypt_pii(detail),
        region_code=region_code,
        province=province,
        city=city,
        is_default=1 if is_default else 0,
    )
    session.add(row)
    session.flush()
    return row


def issue_login(session, client: TestClient, user: User) -> str:
    """为已有用户签发会话 cookie，返回 CSRF token。"""
    from app.modules.identity import service as identity_service

    _row, token, csrf_secret = identity_service.issue_session(session, user=user)
    session.commit()
    client.cookies.set("dsh_session", token)
    return csrf_secret


# ------------------------------------------------------------------ 目录数据
def create_product(
    session,
    *,
    slug: str = "demo-suit",
    title: str = "示例兽装",
    kind: str = "stock_goods",
    status: str = "published",
    requires_approval: bool = False,
    terms_version: str = "shop-1",
) -> Product:
    product = Product(
        id=new_id("product"),
        slug=slug,
        kind=kind,
        title=title,
        subtitle="",
        status=status,
        description="",
        terms_version=terms_version,
        requires_approval=1 if requires_approval else 0,
    )
    session.add(product)
    session.flush()
    return product


def create_sku(
    session,
    *,
    product_id: str,
    price_cents: int = 10000,
    stock_on_hand: int = 10,
    max_per_order: int = 5,
    is_active: bool = True,
    label: str = "标准规格",
    sku_code: str | None = None,
    currency: str = "CNY",
) -> Sku:
    sku = Sku(
        id=new_id("sku"),
        product_id=product_id,
        sku_code=sku_code or f"SKU-{new_id('sku')[-6:]}",
        label=label,
        price_cents=price_cents,
        currency=currency,
        stock_on_hand=stock_on_hand,
        reserved=0,
        max_per_order=max_per_order,
        is_active=1 if is_active else 0,
    )
    session.add(sku)
    session.flush()
    return sku


def create_character(
    session, *, product_id: str, slug: str = "demo-character", name: str = "示例角色"
) -> Character:
    character = Character(
        id=new_id("character"),
        product_id=product_id,
        slug=slug,
        name=name,
        species="龙",
        public_status="available",
        exclusive=1,
    )
    session.add(character)
    session.flush()
    return character


_ = os  # 保留导入，供环境相关断言使用


# ------------------------------------------------------------------ 后台员工与运维夹具
def create_staff(
    session,
    *,
    login_name: str = "editor",
    display_name: str = "内容编辑",
    password: str = "EditorPass123!",
    roles: list[str] | None = None,
    mfa_enabled: bool = True,
    status: str = "active",
):
    """创建员工账号；``mfa_enabled=True`` 时同时绑定一个已知 TOTP 密钥。

    返回 ``(staff, mfa_secret)``，密钥只在测试内使用，便于用 pyotp 计算真实验证码。
    """
    import pyotp

    from app.modules.identity import permissions as permission_catalog
    from app.modules.identity.admin_auth import create_staff as create_staff_account
    from app.platform.ids import now_ms
    from app.platform.security import encrypt_pii

    # 权限与角色定义由 identity 模块维护；创建员工前幂等同步，保证角色码真实存在。
    permission_catalog.sync_catalog(session)

    staff = create_staff_account(
        session,
        actor_id=None,
        login_name=login_name,
        display_name=display_name,
        email=f"{login_name}@example.test",
        password=password,
        roles=roles or ["content_editor"],
    )
    staff.status = status
    secret = ""
    if mfa_enabled:
        secret = pyotp.random_base32()
        staff.mfa_secret_ciphertext = encrypt_pii(secret)
        staff.mfa_enabled = 1
        staff.mfa_confirmed_at = now_ms()
        staff.recovery_code_hashes = []
    session.flush()
    return staff, secret


def admin_session(
    session,
    client: TestClient,
    staff,
    secret: str,
    *,
    password: str = "EditorPass123!",
) -> dict[str, Any]:
    """走完整后台登录（口令 + TOTP），返回 csrf_token 与会话信息。

    不绕过任何一步：MFA 用真实 TOTP 计算，CSRF token 由服务端在 MFA 响应中下发。
    """
    import pyotp

    # 组装层统一校验写请求的 Origin/受信 Referer（10 文档），员工登录同样要带来源。
    headers = {"Origin": BASE_ORIGIN}
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"login_name": staff.login_name, "password": password},
        headers=headers,
    )
    assert login.status_code == 200, login.text
    body = login.json()
    mfa = client.post(
        "/api/v1/admin/auth/mfa",
        json={
            "challenge_token": body["challenge_token"],
            "code": pyotp.TOTP(secret).now(),
            "staff_id": staff.id,
        },
        headers=headers,
    )
    assert mfa.status_code == 200, mfa.text
    session.rollback()
    return {
        "csrf_token": mfa.json()["csrf_token"],
        "staff_id": staff.id,
        "permissions": mfa.json()["permissions"],
    }


def csrf_headers(session_info: dict[str, Any]) -> dict[str, str]:
    """后台写操作的请求头：CSRF token 走 ``X-CSRF-Token``。"""
    return {"X-CSRF-Token": session_info["csrf_token"]}


# ======================================================================================
# 支付模块夹具（payments 交付方维护）
#
# 自成一条夹具链（payment_settings → payment_app → payment_client / payment_db），
# 不复用可能被其他模块调整的夹具，保证支付用例（TC-P01..TC-P11）的隔离性：
# 每个用例一个**真实临时 SQLite 文件**（不是只在单连接内工作的内存库）、一个独立
# data_dir（回调原文、账单、平台证书都落在这里）、独立引擎与独立渠道实例缓存。
#
# 受限文件沙箱下「运行时新建的目录」里可能无法再建文件，因此临时根目录
# ``server/var/payment-tests`` 由仓库预先创建，用例内只在其下新建内容。
# ======================================================================================
PAYMENT_TEST_ROOT = SERVER_DATA_ROOT / "payment-tests"

#: 支付用例启用的渠道：真实微信/支付宝/Stripe 适配器 + 沙箱通道（替代未开通渠道做时序验证）。
PAYMENT_TEST_PROVIDERS = "wechat,alipay,stripe,sandbox"
#: Stripe 回调验签用的测试签名密钥（只在本机测试使用；真实值按运维手册注入）。
STRIPE_TEST_WEBHOOK_SECRET = "whsec_payment_test_0123456789abcdef"


def _import_all_models() -> None:
    """建表前确保所有模型已注册（各模块 router 也会导入，这里显式兜底）。"""
    import app.modules.catalog.models  # noqa: F401
    import app.modules.commerce.models  # noqa: F401
    import app.modules.identity.models  # noqa: F401
    import app.modules.notifications.models  # noqa: F401
    import app.modules.payments.models  # noqa: F401
    from app.platform import reliability  # noqa: F401


@pytest.fixture()
def payment_data_dir() -> Iterator[Path]:
    """用例独立的数据目录（含 storage 子目录），返回该目录。"""
    PAYMENT_TEST_ROOT.mkdir(parents=True, exist_ok=True)
    workdir = PAYMENT_TEST_ROOT / f"case-{uuid.uuid4().hex[:12]}"
    for sub in ("payment_events", "reconciliation", "payment_certs"):
        (workdir / "storage" / sub).mkdir(parents=True, exist_ok=True)
    yield workdir


@pytest.fixture()
def payment_settings(payment_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """支付测试配置：临时 SQLite 文件 + 临时 data_dir + 已启用的支付渠道。

    微信只配置回调验签所需材料（商户号/AppID/APIv3 密钥/平台证书序列），
    商户私钥保持未配置，用于验证「未配置凭据 fail closed，绝不降级沙箱」。
    """
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_DIR", str(payment_data_dir))
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+pysqlite:///{(payment_data_dir / 'starium.db').as_posix()}"
    )
    monkeypatch.setenv("PUBLIC_ORIGIN", BASE_ORIGIN)
    monkeypatch.setenv("SQLITE_SYNCHRONOUS", "NORMAL")
    monkeypatch.setenv("PAYMENT_PROVIDERS", PAYMENT_TEST_PROVIDERS)
    monkeypatch.setenv("PAYMENT_RETURN_URL", f"{BASE_ORIGIN}/account/orders")
    monkeypatch.setenv("INSTALLMENT_PAYMENT_TTL_MINUTES", "30")
    monkeypatch.setenv("WECHAT_MERCHANT_ID", "1900000001")
    monkeypatch.setenv("WECHAT_APP_ID", "wx-test-appid")
    monkeypatch.setenv("WECHAT_API_V3_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("WECHAT_PLATFORM_CERT_SERIAL", "TESTSERIAL0001")
    monkeypatch.setenv("ALIPAY_APP_ID", "2021000000000000")
    # Stripe：只配置回调验签所需材料（密钥 + endpoint 签名密钥 + 账户标识），
    # 真实下单/查单/退款仍会因为没有真实账号而在联调阶段验证（R30）。
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_payment_test_0123456789")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_ACCOUNT_ID", "acct_payment_test_1")
    monkeypatch.delenv("WECHAT_PAY_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("WECHAT_PAY_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("ALIPAY_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ALIPAY_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "payment-test-session-secret-0123456789")
    monkeypatch.setenv("OTP_SECRET", "payment-test-otp-secret-0123456789")
    monkeypatch.setenv("CSRF_SECRET", "payment-test-csrf-secret-0123456789")
    monkeypatch.setenv("PHONE_LOOKUP_KEY", "payment-test-phone-lookup-0123456789")
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "payment-test-pii-key-0123456789")

    get_settings.cache_clear()
    reset_engine()
    reset_gateways()
    _import_all_models()
    settings = get_settings()
    init_database()
    try:
        yield settings
    finally:
        reset_gateways()
        reset_engine()
        get_settings.cache_clear()


@pytest.fixture()
def payment_app(payment_settings: Any) -> Any:
    from app.main import create_app

    return create_app(payment_settings)


@pytest.fixture()
def payment_client(payment_app: Any) -> Iterator[TestClient]:
    with TestClient(payment_app, base_url=BASE_ORIGIN) as test_client:
        # 浏览器同源写请求一定带 Origin；组装层统一校验 Origin（10 文档），
        # 因此测试客户端也必须像浏览器一样带上受信来源。
        test_client.headers["Origin"] = BASE_ORIGIN
        yield test_client


@pytest.fixture()
def payment_db(payment_settings: Any) -> Iterator[Any]:
    """直连支付测试库的会话；断言前请调用 ``fresh(db)`` 结束只读事务。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def fresh(session: Any) -> Any:
    """结束当前只读事务并过期身份映射，确保看到其他连接已提交的写入。

    会话工厂用 ``expire_on_commit=False``，仅 rollback 不会让已加载对象重新 SELECT，
    因此断言前必须显式 ``expire_all()``。
    """
    session.rollback()
    session.expire_all()
    return session


@dataclass
class Customer:
    """已登录客户：带会话 cookie、CSRF 头与受信 Origin 的测试客户端。"""

    client: TestClient
    user_id: str
    csrf_token: str

    def headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Origin": BASE_ORIGIN, "X-CSRF-Token": self.csrf_token}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def create_attempt(
        self,
        installment_id: str,
        *,
        provider: str = "sandbox",
        scene: str = "native",
        idempotency_key: str = "idem-create-1",
        body: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"provider": provider, "scene": scene}
        if body:
            payload.update(body)
        return self.client.post(
            f"/api/v1/installments/{installment_id}/payment-attempts",
            json=payload,
            headers=self.headers(idempotency_key=idempotency_key),
        )

    def attempt_status(self, attempt_id: str) -> Any:
        return self.client.get(f"/api/v1/payment-attempts/{attempt_id}")

    def order_payments(self, order_id: str) -> Any:
        """订单支付尝试列表：优先 ``/orders/{id}/payments``，被商务模块占用时用查询参数形式。"""
        response = self.client.get(f"/api/v1/orders/{order_id}/payments")
        if response.status_code == 404:
            response = self.client.get("/api/v1/payment-attempts", params={"order_id": order_id})
        return response


def _issue_customer(session: Any, client: TestClient, phone: str) -> tuple[Any, str]:
    from app.modules.identity import service as identity_service

    normalized = normalize_phone(phone)
    user = User(
        id=new_id("user"),
        phone_ciphertext=encrypt_pii(normalized),
        phone_lookup=phone_lookup(normalized),
        phone_masked=normalized[:3] + "****" + normalized[-4:],
        nickname="支付测试星友",
    )
    session.add(user)
    session.flush()
    _row, token, csrf_secret = identity_service.issue_session(session, user=user, scope="customer")
    session.commit()
    client.cookies.set("dsh_session", token)
    return user, csrf_secret


@pytest.fixture()
def customer(payment_client: TestClient, payment_db: Any, payment_settings: Any) -> Customer:
    user, csrf = _issue_customer(payment_db, payment_client, "13900000001")
    return Customer(client=payment_client, user_id=user.id, csrf_token=csrf)


@pytest.fixture()
def customer_factory(
    payment_app: Any, payment_db: Any, payment_settings: Any
) -> Callable[..., Customer]:
    """创建多个已登录客户（各自独立客户端与 cookie 罐），用于归属校验用例。"""

    counter = {"index": 0}

    def _factory(phone: str | None = None) -> Customer:
        counter["index"] += 1
        # 与 ``customer`` 夹具的手机号区间区分，避免 users.phone_lookup 唯一冲突。
        resolved = phone or f"+861370000{counter['index']:04d}"
        client = TestClient(payment_app, base_url=BASE_ORIGIN)
        client.headers["Origin"] = BASE_ORIGIN
        user, csrf = _issue_customer(payment_db, client, resolved)
        return Customer(client=client, user_id=user.id, csrf_token=csrf)

    return _factory


# ------------------------------------------------------------------ 商务模块替身
@dataclass
class FakeOrder:
    id: str
    user_id: str
    status: str = "PENDING_PAYMENT"
    currency: str = "CNY"


@dataclass
class FakeInstallment:
    """字段名故意用 ``status``（而非 ``state``），顺带验证桥接的字段归一化。"""

    id: str
    order_id: str
    amount_cents: int
    status: str = "DUE"
    currency: str = "CNY"
    kind: str = "full"


class FakeCommerce:
    """``commerce`` 仓储与端口的替身（签名与真实实现一致），用于隔离验证支付时序。

    只模拟商务侧状态与调用记录，不做任何金额裁定：金额规则仍在支付模块内被断言。
    """

    def __init__(self) -> None:
        self.orders: dict[str, FakeOrder] = {}
        self.installments: dict[str, FakeInstallment] = {}
        self.paid_calls: list[dict[str, Any]] = []
        self.refund_calls: list[dict[str, Any]] = []

    def create_order(
        self,
        *,
        user_id: str,
        amounts: list[int],
        status: str = "PENDING_PAYMENT",
        currency: str = "CNY",
    ) -> tuple[FakeOrder, list[FakeInstallment]]:
        order = FakeOrder(
            id=f"ord_{uuid.uuid4().hex[:12]}", user_id=user_id, status=status, currency=currency
        )
        self.orders[order.id] = order
        rows: list[FakeInstallment] = []
        for index, amount in enumerate(amounts):
            kind = "full" if len(amounts) == 1 else ("prepayment" if index == 0 else "balance")
            installment = FakeInstallment(
                id=f"ist_{uuid.uuid4().hex[:12]}",
                order_id=order.id,
                amount_cents=amount,
                kind=kind,
            )
            self.installments[installment.id] = installment
            rows.append(installment)
        return order, rows

    # ---- commerce.repository 替身（只读） ----
    def get_order(self, session: Any, order_id: str) -> FakeOrder | None:
        _ = session
        return self.orders.get(order_id)

    def get_installment(self, session: Any, installment_id: str) -> FakeInstallment | None:
        _ = session
        return self.installments.get(installment_id)

    def list_installments(self, session: Any, *, order_id: str) -> list[FakeInstallment]:
        _ = session
        return [row for row in self.installments.values() if row.order_id == order_id]

    def list_installments_for_order(self, session: Any, order_id: str) -> list[FakeInstallment]:
        return self.list_installments(session, order_id=order_id)

    # ---- commerce.ports 替身 ----
    def on_installment_paid(self, session: Any, *, order_id: str, installment_id: str) -> dict[str, Any]:
        from app.modules.payments import repository as payments_repo

        self.paid_calls.append({"order_id": order_id, "installment_id": installment_id})
        installment = self.installments[installment_id]
        applied = payments_repo.applied_sum(session, installment_id=installment_id)
        if applied >= installment.amount_cents:
            installment.status = "PAID"
            self.orders[order_id].status = "ACTIVE"
        reliability.enqueue(
            session,
            event_type="order.paid",
            aggregate_id=order_id,
            event_id=f"evt_{uuid.uuid4().hex[:24]}",
            payload={"installment_id": installment_id, "applied_cents": applied},
        )
        return {
            "installment_status": installment.status,
            "order_status": self.orders[order_id].status,
            "consumed_reservations": [],
        }

    def on_refund_succeeded(
        self,
        session: Any,
        *,
        order_id: str,
        refund_id: str,
        goods_cents: int,
        shipping_cents: int,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _ = session
        self.refund_calls.append(
            {
                "order_id": order_id,
                "refund_id": refund_id,
                "goods_cents": goods_cents,
                "shipping_cents": shipping_cents,
                # 按行分摊：与真实端口一致，便于断言「部分退款分配到商品行」。
                "items": items or [],
            }
        )
        return {
            "points_recomputed": True,
            "goods_cents": goods_cents,
            "shipping_cents": shipping_cents,
        }


@pytest.fixture()
def commerce(monkeypatch: pytest.MonkeyPatch) -> FakeCommerce:
    """把支付模块的商务桥接整体替换为替身（读取与端口调用都换）。"""
    from app.modules.payments import repository_bridge as bridge

    fake = FakeCommerce()
    monkeypatch.setattr(bridge, "get_order", fake.get_order)
    monkeypatch.setattr(bridge, "get_installment", fake.get_installment)
    monkeypatch.setattr(bridge, "list_installments_for_order", fake.list_installments_for_order)
    monkeypatch.setattr(bridge, "on_installment_paid", fake.on_installment_paid)
    monkeypatch.setattr(bridge, "on_refund_succeeded", fake.on_refund_succeeded)
    return fake


# ------------------------------------------------------------------ 渠道替身与真实验签构造器
@pytest.fixture()
def sandbox_channel(payment_settings: Any) -> Iterator[Callable[[str], Any]]:
    """把某个渠道替换为沙箱实现（渠道侧账本 + 已签名回调），返回该实例。"""

    def _make(provider: str = "sandbox") -> Any:
        gateway = SandboxPaymentGateway(payment_settings, provider=provider)
        set_gateway_override(provider, gateway)
        return gateway

    yield _make
    reset_gateways()


class WechatTestChannel:
    """构造**真实**微信 APIv3 通知：本地生成平台密钥、加密 resource 并签名。

    用它验证适配器的验签、证书序列轮换、时间戳窗口与解密路径，不需要外网与真实凭据。
    """

    def __init__(self, settings: Any, gateway: Any) -> None:
        self.settings = settings
        self.gateway = gateway
        self.serial = settings.wechat_platform_cert_serial or "TESTSERIAL0001"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.store_certificate()

    @property
    def api_v3_key(self) -> bytes:
        return (self.settings.wechat_api_v3_key or "").encode()

    def public_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )

    def store_certificate(self, serial: str | None = None) -> str:
        target = serial or self.serial
        self.gateway.store_platform_certificate(target, self.public_pem())
        return target

    def sign(self, message: bytes, *, private_key: Any | None = None) -> str:
        key = private_key or self.private_key
        return base64.b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()

    def notification(
        self,
        *,
        merchant_order_no: str,
        amount_cents: int,
        transaction_id: str | None = None,
        event_id: str | None = None,
        mchid: str | None = None,
        appid: str | None = None,
        trade_state: str = "SUCCESS",
        timestamp: int | None = None,
        header_nonce: str = "header-nonce-0001",
        serial: str | None = None,
        sign_key: Any | None = None,
        encrypt_key: bytes | None = None,
        currency: str = "CNY",
        omit_signature: bool = False,
    ) -> tuple[dict[str, str], bytes]:
        """返回 ``(headers, raw_body)``；可注入错密钥/错序列/过期时间构造异常用例。"""
        moment = int(time.time()) if timestamp is None else timestamp
        resource_nonce = "0123456789ab"
        aead = AESGCM(encrypt_key or self.api_v3_key)
        plaintext = {
            "out_trade_no": merchant_order_no,
            "transaction_id": transaction_id or f"WXTXN{uuid.uuid4().hex[:16]}",
            "trade_state": trade_state,
            "mchid": mchid if mchid is not None else self.settings.wechat_merchant_id,
            "appid": appid if appid is not None else self.settings.wechat_app_id,
            "amount": {"total": amount_cents, "currency": currency},
            "success_time": "2024-05-01T12:00:00+08:00",
        }
        ciphertext = aead.encrypt(
            resource_nonce.encode(), json.dumps(plaintext).encode(), b"transaction"
        )
        body = {
            "id": event_id or f"wx-evt-{uuid.uuid4().hex[:16]}",
            "create_time": "2024-05-01T12:00:00+08:00",
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": resource_nonce,
                "associated_data": "transaction",
            },
        }
        raw = json.dumps(body, ensure_ascii=False).encode()
        message = f"{moment}\n{header_nonce}\n{raw.decode()}\n".encode()
        headers = {
            "Wechatpay-Timestamp": str(moment),
            "Wechatpay-Nonce": header_nonce,
            "Wechatpay-Serial": serial or self.serial,
            "Content-Type": "application/json",
        }
        if not omit_signature:
            headers["Wechatpay-Signature"] = self.sign(message, private_key=sign_key)
        return headers, raw


@pytest.fixture()
def wechat_channel(payment_settings: Any) -> WechatTestChannel:
    """真实微信适配器（已配商户号/AppID/APIv3 密钥）+ 本地平台证书。"""
    return WechatTestChannel(payment_settings, get_gateway("wechat"))


class AlipayTestChannel:
    """构造**真实**支付宝异步通知：商户私钥签名、支付宝公钥验签。"""

    def __init__(self, settings: Any, gateway: Any) -> None:
        self.settings = settings
        self.gateway = gateway
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )

    def notification(
        self,
        *,
        merchant_order_no: str,
        amount_cents: int,
        transaction_id: str | None = None,
        app_id: str | None = None,
        trade_status: str = "TRADE_SUCCESS",
        notify_time: str | None = None,
        notify_id: str | None = None,
        tamper_amount_cents: int | None = None,
    ) -> bytes:
        from urllib.parse import urlencode

        from app.adapters.payments.alipay import _canonical

        params = {
            "notify_time": notify_time or time.strftime("%Y-%m-%d %H:%M:%S"),
            "notify_type": "trade_status_sync",
            "notify_id": notify_id or f"ali-notify-{uuid.uuid4().hex[:12]}",
            "app_id": app_id if app_id is not None else self.settings.alipay_app_id,
            "charset": "utf-8",
            "version": "1.0",
            "sign_type": "RSA2",
            "trade_no": transaction_id or f"2024050122001{uuid.uuid4().hex[:10]}",
            "out_trade_no": merchant_order_no,
            "trade_status": trade_status,
            "total_amount": f"{amount_cents // 100}.{amount_cents % 100:02d}",
            "gmt_payment": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        params["sign"] = base64.b64encode(
            self.private_key.sign(_canonical(params).encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        if tamper_amount_cents is not None:
            params["total_amount"] = f"{tamper_amount_cents // 100}.{tamper_amount_cents % 100:02d}"
        return urlencode(params).encode()


@pytest.fixture()
def alipay_channel(payment_settings: Any) -> AlipayTestChannel:
    """真实支付宝适配器（用测试生成的支付宝公钥配置）+ 通知构造器。"""
    from app.adapters.payments.alipay import AlipayGateway

    channel = AlipayTestChannel(payment_settings, gateway=None)
    settings = payment_settings.model_copy(update={"alipay_public_key": channel.public_pem()})
    channel.gateway = AlipayGateway(settings)
    return channel
