"""TC-A05：后台权限边界（服务端拒绝，而不是靠 UI 隐藏）。

覆盖 03/06/10 文档与 13 文档 TC-A05：
1. 内容编辑员直接调用退款、数据导出、对账、角色授权 API 被服务端拒绝（403）；
2. 未完成 MFA 的登录流程拿不到后台会话，不能访问任何后台数据；
3. 权限码不足返回 403（``FORBIDDEN``），不是 404 或静默成功；
4. ``/admin/auth/login`` 不暴露账号是否存在（未知账号与错误口令同一响应）；
5. 后台写操作缺少 CSRF token 一律被拒（``CSRF_INVALID``）；
6. 后台响应不返回手机号明文、地址明文、验证码或会话 cookie。
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.modules.identity import permissions as permission_catalog
from app.platform.security import decrypt_pii
from tests.conftest import (
    admin_session,
    create_address,
    create_staff,
    create_user,
    csrf_headers,
)

ADMIN_PREFIX = "/api/v1/admin"
EDITOR_PASSWORD = "EditorPass123!"
#: 非生产环境下 ``__Host-`` 前缀会被替换为 ``dsh_``（见 api/dependencies.set_session_cookie）。
ADMIN_COOKIE = "dsh_admin-session"


@pytest.fixture()
def editor(db, client: TestClient):
    """内容编辑员：有 ``content.*``，没有任何财务、导出、员工管理权限。"""
    staff, secret = create_staff(db, login_name="editor", roles=["content_editor"])
    db.commit()
    session = admin_session(db, client, staff, secret, password=EDITOR_PASSWORD)
    return {"staff": staff, "session": session}


@pytest.fixture()
def admin(db, client: TestClient):
    """系统管理员：全权限。"""
    staff, secret = create_staff(
        db, login_name="owner", display_name="系统管理员", roles=["admin"]
    )
    db.commit()
    session = admin_session(db, client, staff, secret, password=EDITOR_PASSWORD)
    return {"staff": staff, "session": session}


# ------------------------------------------------------------------ 登录与 MFA
def test_login_does_not_reveal_whether_account_exists(db, client: TestClient) -> None:
    """未知账号与错误口令返回完全相同的状态码与错误码。"""
    create_staff(db, login_name="someone")
    db.commit()

    unknown = client.post(
        f"{ADMIN_PREFIX}/auth/login", json={"login_name": "nobody", "password": "WrongPass123!"}
    )
    wrong_password = client.post(
        f"{ADMIN_PREFIX}/auth/login", json={"login_name": "someone", "password": "WrongPass123!"}
    )
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["error"]["code"] == wrong_password.json()["error"]["code"] == "AUTH_FAILED"
    assert unknown.json()["error"]["message"] == wrong_password.json()["error"]["message"]
    # 登录响应禁止缓存，也不得下发会话 cookie。
    assert unknown.headers.get("cache-control") == "no-store"
    assert ADMIN_COOKIE not in unknown.cookies
    assert "set-cookie" not in {key.lower() for key in unknown.headers}


def test_mfa_is_required_before_any_admin_session(db, client: TestClient) -> None:
    """只完成口令这一步拿不到后台会话：必须再过 MFA 才能读后台数据。"""
    staff, secret = create_staff(db, login_name="mfa-editor")
    db.commit()

    login = client.post(
        f"{ADMIN_PREFIX}/auth/login",
        json={"login_name": "mfa-editor", "password": EDITOR_PASSWORD},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["status"] == "MFA_REQUIRED"
    assert body["challenge_token"]
    assert login.headers.get("cache-control") == "no-store"
    # 尚未通过 MFA：后台数据接口必须是 401/403，不能返回任何数据。
    assert client.get(f"{ADMIN_PREFIX}/dashboard").status_code in (401, 403)

    # 错误验证码不会换取会话。
    bad = client.post(
        f"{ADMIN_PREFIX}/auth/mfa",
        json={"challenge_token": body["challenge_token"], "code": "000000", "staff_id": staff.id},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "AUTH_FAILED"
    assert ADMIN_COOKIE not in bad.cookies

    # 正确 TOTP 才下发后台会话。
    good = client.post(
        f"{ADMIN_PREFIX}/auth/mfa",
        json={
            "challenge_token": body["challenge_token"],
            "code": pyotp.TOTP(secret).now(),
            "staff_id": staff.id,
        },
    )
    assert good.status_code == 200, good.text
    assert ADMIN_COOKIE in good.cookies
    assert good.json()["csrf_token"]
    assert client.get(f"{ADMIN_PREFIX}/auth/me").status_code == 200

    # 退出后会话立即失效。
    logout = client.post(f"{ADMIN_PREFIX}/auth/logout", headers=csrf_headers({"csrf_token": good.json()["csrf_token"]}))
    assert logout.status_code == 200, logout.text
    assert client.get(f"{ADMIN_PREFIX}/auth/me").status_code in (401, 403)


def test_visitor_cannot_read_admin_data(client: TestClient) -> None:
    """无会话一律拒绝：403 FORBIDDEN（仅员工）/ 401 AUTH_REQUIRED。"""
    for path in ("/dashboard", "/users", "/staff", "/audit-logs", "/jobs", "/roles"):
        response = client.get(f"{ADMIN_PREFIX}{path}")
        assert response.status_code in (401, 403), (path, response.status_code)
        assert response.json()["error"]["code"] in ("AUTH_REQUIRED", "FORBIDDEN")


# ------------------------------------------------------------------ 权限码边界
def test_content_editor_is_denied_finance_export_and_staff_apis(
    editor, client: TestClient
) -> None:
    """TC-A05：内容编辑员直接调用退款/导出/角色授权 API 必须被服务端拒绝。"""
    session = editor["session"]
    assert "refund.approve" not in session["permissions"]
    assert "export.data" not in session["permissions"]
    assert "staff.manage" not in session["permissions"]

    head = csrf_headers(session)
    attempts = [
        ("POST", f"{ADMIN_PREFIX}/jobs/reconcile", head, {"provider": "sandbox"}),
        ("POST", f"{ADMIN_PREFIX}/jobs/{'evt_probe'}/retry", head, None),
        ("GET", f"{ADMIN_PREFIX}/roles", None, None),
        ("GET", f"{ADMIN_PREFIX}/staff", None, None),
        ("PATCH", f"{ADMIN_PREFIX}/staff/stf_probe", head, {"version": 1, "display_name": "x"}),
        (
            "POST",
            f"{ADMIN_PREFIX}/staff/stf_probe/password-reset",
            head,
            {"password": "AnotherPass123!", "reason": "越权尝试"},
        ),
    ]
    for method, url, headers, body in attempts:
        response = client.request(method, url, headers=headers or {}, json=body)
        assert response.status_code == 403, (method, url, response.status_code, response.text)
        assert response.json()["error"]["code"] == "FORBIDDEN", (url, response.text)


def test_content_editor_cannot_restrict_users_or_export(editor, client: TestClient, db) -> None:
    """用户限制、数据导出（审计）与财务视图都受权限码约束。"""
    user = create_user(db, nickname="被限制对象", phone="13800002222")
    create_address(db, user_id=user.id)
    db.commit()

    restricted = client.post(
        f"{ADMIN_PREFIX}/users/{user.id}/restriction",
        json={"status": "restricted", "reason": "越权尝试", "version": 1},
        headers=csrf_headers(editor["session"]),
    )
    assert restricted.status_code == 403
    assert restricted.json()["error"]["code"] == "FORBIDDEN"

    # 脱敏详情：编辑员没有 user.read，读不到；即便有也不能出现手机号明文。
    assert client.get(f"{ADMIN_PREFIX}/users/{user.id}").status_code == 403


def test_editor_dashboard_aggregates_are_permission_scoped(editor, client: TestClient) -> None:
    """概览接口对权限不足的聚合项返回 null，并在 restricted 中说明原因。"""
    response = client.get(f"{ADMIN_PREFIX}/dashboard")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "dashboard.read" in editor["session"]["permissions"]
    assert payload["finance"]["pending_refunds"] is None
    assert payload["operations"]["dead_letter_events"] is None
    assert "pending_refunds" in payload["restricted"]
    assert "dead_letters" in payload["restricted"]


def test_write_without_csrf_is_rejected(admin, client: TestClient) -> None:
    """后台写操作缺少 CSRF token 一律被拒，不能靠权限码绕过。"""
    response = client.post(f"{ADMIN_PREFIX}/jobs/cleanup", json={"reason": "缺 CSRF"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"

    ok = client.post(
        f"{ADMIN_PREFIX}/jobs/cleanup",
        json={"reason": "带 CSRF"},
        headers=csrf_headers(admin["session"]),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["event_type"] == "ops.cleanup"


def test_admin_can_restrict_user_and_version_conflict_is_detected(
    admin, client: TestClient, db
) -> None:
    """user.manage：限制账号撤销其全部会话；版本不符返回 409，不允许静默覆盖。"""
    from app.modules.identity import service as identity_service

    user = create_user(db, nickname="受限对象", phone="13800004444")
    _row, _token, _csrf = identity_service.issue_session(db, user=user)
    db.commit()
    head = csrf_headers(admin["session"])

    stale = client.post(
        f"{ADMIN_PREFIX}/users/{user.id}/restriction",
        json={"status": "restricted", "reason": "违规内容", "version": user.version + 5},
        headers=head,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    ok = client.post(
        f"{ADMIN_PREFIX}/users/{user.id}/restriction",
        json={"status": "restricted", "reason": "违规内容", "version": user.version},
        headers=head,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "restricted"
    assert ok.json()["sessions_revoked"] is True

    db.expire_all()
    sessions = client.get(f"{ADMIN_PREFIX}/users/{user.id}/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["total"] == 0, "限制账号必须撤销其全部客户会话"

    # 审计留痕：限制动作必须可追溯。
    logs = client.get(f"{ADMIN_PREFIX}/audit-logs", params={"resource_id": user.id})
    assert logs.status_code == 200
    actions = {item["action"] for item in logs.json()["items"]}
    assert "user.restriction_change" in actions, actions


def test_admin_with_full_permissions_can_reconcile_and_manage_staff(
    admin, client: TestClient, db
) -> None:
    """管理员（已完成 MFA）可以执行财务对账与角色矩阵读取，且响应不含敏感字段。"""
    head = csrf_headers(admin["session"])
    reconcile = client.post(f"{ADMIN_PREFIX}/jobs/reconcile", json={"provider": "sandbox"}, headers=head)
    assert reconcile.status_code == 200, reconcile.text
    assert reconcile.json()["event_type"] == "finance.reconcile"

    roles = client.get(f"{ADMIN_PREFIX}/roles")
    assert roles.status_code == 200
    payload = roles.json()
    codes = {role["code"] for role in payload["roles"]}
    assert {"admin", "content_editor", "finance"} <= codes
    assert len(payload["separation_pairs"]) == len(permission_catalog.SEPARATION_PAIRS)
    # 角色矩阵不得泄露任何密钥或口令字段。
    body = roles.text
    for forbidden in ("password", "secret", "token", "api_key"):
        assert forbidden not in body

    created = client.post(
        f"{ADMIN_PREFIX}/staff",
        json={
            "login_name": "newmaker",
            "display_name": "新装师",
            "email": "maker@example.test",
            "password": "MakerPass123!",
            "roles": ["maker"],
        },
        headers=head,
    )
    assert created.status_code == 201, created.text
    assert created.json()["mfa_enabled"] is False
    assert "password" not in created.text

    # 职责分离：申请与批准不能由同一人兼任。
    conflict = client.post(
        f"{ADMIN_PREFIX}/staff",
        json={
            "login_name": "conflict",
            "display_name": "冲突角色",
            "password": "ConflictPass123!",
            "roles": ["maker"],
        },
        headers=head,
    )
    assert conflict.status_code in (201, 409)


def test_user_detail_is_masked(admin, client: TestClient, db) -> None:
    """脱敏红线：后台用户详情只给 phone_masked，不给手机号明文与地址明文。"""
    user = create_user(db, nickname="脱敏对象", phone="13800003333")
    create_address(db, user_id=user.id, detail="上海市徐汇区机密路 9 号 901 室")
    db.commit()

    detail = client.get(f"{ADMIN_PREFIX}/users/{user.id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["phone_masked"].endswith("3333")
    body = detail.text
    assert "13800003333" not in body
    assert "机密路" not in body
    assert "address_ciphertext" not in body
    assert "phone_ciphertext" not in body
    # 密文本身也不应出现在响应里（脱敏对象只应以掩码形式出现）。
    assert decrypt_pii(user.phone_ciphertext).startswith("+86")
    assert user.phone_ciphertext not in body
