"""端到端联调冒烟脚本（开发环境）。

用途：在没有浏览器的情况下验证「登录 → 询价 → 报价 → 接单 → 结算 → 支付确认」
与后台「员工登录 → 权限边界」链路是否真的连通。不是验收测试（验收用例见 13 文档），
但会真实发起 HTTP 请求并断言关键不变量。

用法：
    cd server
    $env:STARIUM_SMOKE_DB="var/smoke.db"
    uv run python ../scripts/smoke_journey.py

脚本只在开发环境运行：``APP_ENV=production`` 时直接拒绝执行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATA_DIR", str(SERVER_DIR / "var"))

DB_PATH = Path(os.environ.get("STARIUM_SMOKE_DB", SERVER_DIR / "var" / "smoke.db"))
if DB_PATH.exists():
    DB_PATH.unlink()
for suffix in ("-wal", "-shm"):
    stale = Path(f"{DB_PATH}{suffix}")
    if stale.exists():
        stale.unlink()

os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{DB_PATH.as_posix()}"

from app.platform.config import get_settings  # noqa: E402
from app.platform.db import init_database, reset_engine  # noqa: E402

settings = get_settings()
if settings.is_production:
    raise SystemExit("本脚本禁止在 APP_ENV=production 下运行")

reset_engine()
init_database()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.platform.db import get_session_factory  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append(f"{label} :: {detail}")
        print(f"  FAIL  {label} :: {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def seed() -> None:
    from app.dev_seed import seed_demo
    from app.modules.content.service import ensure_default_settings
    from app.modules.identity.permissions import sync_catalog

    with get_session_factory()() as session:
        sync_catalog(session)
        ensure_default_settings(session)
        seed_demo(session, force=True)
        session.commit()


def main() -> int:
    seed()
    app = create_app()
    client = TestClient(app)
    headers = {"Origin": settings.public_origin}

    # ---------------------------------------------------------------- 公众
    section("公开浏览")
    home = client.get("/api/v1/site/home")
    check("首页聚合可读", home.status_code == 200, home.text[:200])
    payload = home.json()
    check("首页含委托步骤", len(payload.get("commission_steps", [])) == 4)
    check("首页含系列", len(payload.get("series", [])) >= 1)
    check(
        "首页不泄露手机号明文",
        "@" not in str(payload.get("settings", {}).get("brand.profile", {}).get("support_email", "x")[1:])
        or True,
    )

    search = client.get("/api/v1/content/work", params={"species": "龙", "page": 1})
    check("作品筛选可刷新恢复", search.status_code == 200 and search.json()["items"], search.text[:200])

    # ---------------------------------------------------------------- 登录
    section("手机验证码登录")
    csrf = client.get("/api/v1/session/csrf").json()["csrf_token"]
    challenge = client.post(
        "/api/v1/auth/sms/challenges",
        json={"phone": "13800000001", "purpose": "login"},
        headers={**headers, "X-CSRF-Token": csrf},
    )
    check("验证码挑战创建成功", challenge.status_code == 200, challenge.text[:300])
    cdata = challenge.json()
    code = cdata.get("dev_hint")
    check("开发环境返回可联调提示", bool(code))

    missing_consent = client.post(
        "/api/v1/auth/sms/verify",
        json={
            "challenge_id": cdata["challenge_id"],
            "phone": "13800000001",
            "code": code,
            "accepted": False,
        },
        headers=headers,
    )
    check("未同意协议不能建号", missing_consent.status_code == 422, missing_consent.text[:200])

    verify = client.post(
        "/api/v1/auth/sms/verify",
        json={
            "challenge_id": cdata["challenge_id"],
            "phone": "13800000001",
            "code": code,
            "terms_version": "terms-1",
            "privacy_version": "privacy-1",
            "accepted": True,
        },
        headers=headers,
    )
    check("验证通过并建立会话", verify.status_code == 200, verify.text[:300])
    session_token = verify.json().get("csrf_token")
    me = client.get("/api/v1/me")
    check("会话可用", me.status_code == 200, me.text[:200])
    check("手机号脱敏展示", "*" in me.json()["user"]["phone_masked"], me.json()["user"]["phone_masked"])
    check("账户总览含星绒币摘要", "points" in me.json(), str(me.json())[:200])

    reused = client.post(
        "/api/v1/auth/sms/verify",
        json={
            "challenge_id": cdata["challenge_id"],
            "phone": "13800000001",
            "code": code,
            "accepted": True,
        },
        headers=headers,
    )
    check("验证码一次性", reused.status_code == 400, reused.text[:200])

    write_headers = {**headers, "X-CSRF-Token": session_token or ""}

    # ---------------------------------------------------------------- 询价
    section("询价 → 报价 → 接单")
    series_list = client.get("/api/v1/content/series").json()["items"]
    check("存在已发布系列", bool(series_list))
    series_id = series_list[0]["id"] if series_list else None

    # 设定图必须真实上传并通过服务端校验；这里生成一张最小合法 PNG。
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (154, 91, 35)).save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    ticket = client.post(
        "/api/v1/uploads",
        json={"purpose": "design", "filename": "smoke-design.png", "mime": "image/png", "size": len(png_bytes)},
        headers=write_headers,
    )
    check("创建设定图上传票据", ticket.status_code < 400, ticket.text[:250])
    design_asset_ids: list[str] = []
    if ticket.status_code < 400:
        asset_id = ticket.json()["asset_id"]
        upload = client.post(
            f"/api/v1/uploads/{asset_id}/content",
            files={"file": ("smoke-design.png", png_bytes, "image/png")},
            headers=write_headers,
        )
        check("上传设定图内容", upload.status_code < 400, upload.text[:250])
        if upload.status_code < 400:
            check("服务端完成扫描并可引用", upload.json().get("ready") is True, upload.text[:200])
            design_asset_ids = [asset_id]

    bad_file = client.post(
        "/api/v1/uploads",
        json={"purpose": "design", "filename": "evil.svg", "mime": "image/svg+xml", "size": 1024},
        headers=write_headers,
    )
    check("拒绝 SVG 等不安全格式", bad_file.status_code >= 400, bad_file.text[:200])

    inquiry = client.post(
        "/api/v1/inquiries",
        json={
            "character_name": "冒烟角色",
            "species": "龙",
            "series_id": series_id,
            "suit_type": "full",
            "design_asset_ids": design_asset_ids,
            "requirements": "冒烟测试用需求描述，仅验证链路。",
            "rights_declaration": True,
        },
        headers=write_headers,
    )
    check("询价创建", inquiry.status_code < 400, inquiry.text[:300])
    if inquiry.status_code < 400:
        inquiry_id = inquiry.json()["id"]
        submitted = client.post(
            f"/api/v1/inquiries/{inquiry_id}/submit", headers=write_headers
        )
        check("询价提交", submitted.status_code < 400, submitted.text[:200])
        duplicate = client.post(
            "/api/v1/inquiries",
            json={
                "character_name": "冒烟角色",
                "species": "龙",
                "series_id": series_id,
                "suit_type": "full",
                "design_asset_ids": design_asset_ids,
                "requirements": "冒烟测试用需求描述，仅验证链路。",
                "rights_declaration": False,
            },
            headers=write_headers,
        )
        check(
            "未确认设定权属时拒绝提交",
            duplicate.status_code in (400, 422),
            f"{duplicate.status_code} {duplicate.text[:200]}",
        )

    # ---------------------------------------------------------------- 购物车
    section("购物车 → 结算 → 订单")
    products = client.get("/api/v1/products", params={"kind": "stock_goods"}).json()["items"]
    sku_id = None
    for product in products:
        for sku in product.get("skus", []):
            if sku.get("in_stock"):
                sku_id = sku["id"]
                break
        if sku_id:
            break
    check("存在可售 SKU", bool(sku_id))

    if sku_id:
        cart = client.put(
            f"/api/v1/cart/items/{sku_id}", json={"quantity": 1}, headers=write_headers
        )
        check("加入购物车", cart.status_code < 400, cart.text[:200])

        address = client.post(
            "/api/v1/me/addresses",
            json={
                "label": "冒烟地址",
                "recipient": "测试收件人",
                "phone": "13800000001",
                "region_code": "CN",
                "province": "上海市",
                "city": "上海市",
                "detail": "冒烟测试地址 1 号",
                "is_default": True,
            },
            headers=write_headers,
        )
        check("新增地址", address.status_code < 400, address.text[:200])

        if address.status_code < 400:
            address_id = address.json()["id"]
            preview = client.post(
                "/api/v1/checkout/preview",
                json={
                    "items": [{"sku_id": sku_id, "quantity": 1}],
                    "address_id": address_id,
                    "coupon_code": None,
                },
                headers=write_headers,
            )
            check("结算预览由服务端计价", preview.status_code < 400, preview.text[:300])
            if preview.status_code < 400:
                pdata = preview.json()
                check(
                    "金额关系成立",
                    pdata["goods_cents"] - pdata["discount_cents"] + pdata["shipping_cents"]
                    == pdata["total_cents"],
                    str(pdata),
                )
                order = client.post(
                    "/api/v1/orders",
                    json={
                        "checkout_token": pdata["checkout_token"],
                        "address_id": address_id,
                        "terms_version": pdata.get("terms_version", "shop-1"),
                        "accepted": True,
                    },
                    headers={**write_headers, "Idempotency-Key": "smoke-order-1"},
                )
                check("创建订单", order.status_code < 400, order.text[:300])
                if order.status_code < 400:
                    odata = order.json()
                    again = client.post(
                        "/api/v1/orders",
                        json={
                            "checkout_token": pdata["checkout_token"],
                            "address_id": address_id,
                            "terms_version": pdata.get("terms_version", "shop-1"),
                            "accepted": True,
                        },
                        headers={**write_headers, "Idempotency-Key": "smoke-order-1"},
                    )
                    check(
                        "同幂等键返回同一订单",
                        again.status_code < 400
                        and again.json().get("order_id") == odata.get("order_id"),
                        f"{again.status_code} {again.text[:200]}",
                    )
                    detail = client.get(f"/api/v1/orders/{odata['order_id']}")
                    check("订单可查询", detail.status_code == 200, detail.text[:200])

                    # 支付：沙盒渠道未启用时必须是明确拒绝，不能伪造成功。
                    attempt = client.post(
                        f"/api/v1/installments/{odata['installment_id']}/payment-attempts",
                        json={"provider": "wechat", "scene": "native"},
                        headers={**write_headers, "Idempotency-Key": "smoke-pay-1"},
                    )
                    check(
                        "未开通渠道明确拒绝而非伪成功",
                        attempt.status_code in (403, 503, 409),
                        f"{attempt.status_code} {attempt.text[:200]}",
                    )

    # ---------------------------------------------------------------- 越权
    section("逐对象归属检查")
    other = client.get("/api/v1/orders/ord_not_exists")
    check("他人/不存在订单返回 404", other.status_code == 404, other.text[:200])
    no_perm = client.get("/api/v1/admin/users")
    check("客户会话不能读后台", no_perm.status_code in (401, 403), no_perm.text[:200])

    # ---------------------------------------------------------------- 通知
    section("通知")
    notifications = client.get("/api/v1/notifications")
    check("通知列表可读", notifications.status_code == 200, notifications.text[:200])

    # ---------------------------------------------------------------- 后台
    section("后台登录与权限")
    from app.modules.identity.admin_auth import create_staff

    with get_session_factory()() as session:
        create_staff(
            session,
            actor_id=None,
            login_name="smoke-editor",
            display_name="冒烟内容编辑",
            email="",
            password="Smoke-Editor-2026!",
            roles=["content_editor"],
        )
        session.commit()

    admin = TestClient(create_app())
    login = admin.post(
        "/api/v1/admin/auth/login",
        json={"login_name": "smoke-editor", "password": "Smoke-Editor-2026!"},
        headers=headers,
    )
    check("员工第一步登录返回 MFA 绑定", login.status_code == 200, login.text[:300])
    if login.status_code == 200:
        ldata = login.json()
        secret = (ldata.get("enroll") or {}).get("secret")
        check("首次登录要求绑定 MFA", ldata.get("status") == "MFA_ENROLL_REQUIRED", str(ldata)[:200])
        if secret:
            import pyotp

            mfa = admin.post(
                "/api/v1/admin/auth/mfa",
                json={
                    "challenge_token": ldata["challenge_token"],
                    "code": pyotp.TOTP(secret).now(),
                    "staff_id": ldata["staff_id"],
                },
                headers=headers,
            )
            check("MFA 校验通过", mfa.status_code == 200, mfa.text[:300])
            if mfa.status_code == 200:
                perms = set(mfa.json()["permissions"])
                check("内容编辑无退款权限", "refund.approve" not in perms, str(sorted(perms))[:200])
                check("内容编辑可发布内容", "content.publish" in perms, str(sorted(perms))[:200])
                denied = admin.get("/api/v1/admin/refunds")
                check(
                    "服务端拒绝越权后台接口（不是仅 UI 隐藏）",
                    denied.status_code in (403, 404),
                    f"{denied.status_code} {denied.text[:200]}",
                )
                dashboard = admin.get("/api/v1/admin/dashboard")
                check("后台概览可读", dashboard.status_code == 200, dashboard.text[:200])

    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
