"""整页刷新后的 CSRF 自愈回归（浏览器用例发现的 P1）。

缺陷：会话只保存 ``csrf_secret_hash``，而 ``GET /session/csrf`` 过去**无论是否已登录**
都只发匿名 token。前端整页刷新后内存里的 token 丢失，重新取到的是匿名 token，
于是已登录的第一次写操作被 ``verify_csrf`` 判为不匹配 → 403 ``CSRF_INVALID``。
表现是「登录后刷新页面，询价草稿第一次自动保存失败」「加购/下单第一次失败」。

修法：`/session/csrf` 在已登录时轮换并返回绑定该会话的 token；前端在仍被
``CSRF_INVALID`` 拒绝时清缓存、重取一次并重试一次（写请求带 `Idempotency-Key`，
重试不会产生第二次业务效果）。
"""
from __future__ import annotations

BASE = "/api/v1"
ORIGIN = "http://127.0.0.1:5173"


def _headers(token: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def _csrf_from(client) -> str:
    response = client.get(f"{BASE}/session/csrf", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_csrf_endpoint_returns_session_bound_token_when_logged_in(db, client):
    """已登录时 `/session/csrf` 必须返回可用于写操作的 token，而不是匿名 token。"""
    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="刷新自愈用户", phone="13800000301")
    db.flush()
    issue_login(db, client, user)

    # 模拟整页刷新：丢开登录时下发的内存 token，重新向服务端要一个。
    refreshed = _csrf_from(client)
    response = client.patch(
        f"{BASE}/me",
        json={"nickname": "刷新后改名", "version": 1},
        headers=_headers(refreshed),
    )
    assert response.status_code == 200, response.text
    assert response.json()["nickname"] == "刷新后改名"


def test_visitor_gets_anonymous_token_and_it_is_not_session_bound(db, client):
    """未登录仍发匿名 token；登录后拿旧匿名 token 写操作必须被拒。"""
    anonymous = _csrf_from(client)
    # 匿名 token 形式为 `nonce.expires.hmac`（会话 token 是随机密钥，不含点）。
    assert anonymous.count(".") == 2

    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="匿名对照", phone="13800000302")
    db.flush()
    issue_login(db, client, user)

    rejected = client.patch(
        f"{BASE}/me",
        json={"nickname": "匿名 token 写入", "version": 1},
        headers=_headers(anonymous),
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error"]["code"] == "CSRF_INVALID"


def test_csrf_reissue_is_idempotent_and_multitab_safe(db, client):
    """重复获取必须返回同一个 token：并发写请求与多标签页不能互相作废。

    这是「刷新后重新获取」方案的前提——如果每次获取都轮换密钥，同一页面并发的两个写请求
    会各取一次、后者让前者的 token 立刻失效，表现为随机的 ``CSRF_INVALID``。
    """
    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="多标签用户", phone="13800000303")
    db.flush()
    login_token = issue_login(db, client, user)

    first = _csrf_from(client)
    second = _csrf_from(client)
    assert first == second, "同一个会话重复获取 CSRF token 必须稳定"

    # 登录响应下发的 token 与刷新后重新获取的 token 都必须可用（多标签页同时在线）。
    for index, (label, token) in enumerate((("登录下发", login_token), ("刷新重取", first)), start=1):
        response = client.patch(
            f"{BASE}/me", json={"nickname": label, "version": index}, headers=_headers(token)
        )
        assert response.status_code == 200, f"{label} token 应可用：{response.text}"
        assert response.json()["nickname"] == label

    # 撤销会话后两种 token 同时失效（注销即失去写权限）。
    logout = client.post(f"{BASE}/auth/logout", headers=_headers(first))
    assert logout.status_code in (200, 204), logout.text
    after_logout = client.patch(
        f"{BASE}/me", json={"nickname": "注销后", "version": 3}, headers=_headers(first)
    )
    assert after_logout.status_code in (401, 403), after_logout.text


def test_admin_session_csrf_stays_valid(db, client):
    """员工后台的 CSRF 不受客户侧改动影响：登录下发的 token 仍然可用。"""
    from tests.conftest import admin_session, create_staff

    staff, secret = create_staff(db, login_name="csrf-admin", roles=["admin"])
    db.commit()
    session_info = admin_session(db, client, staff, secret)

    response = client.get(
        f"{BASE}/admin/auth/me",
        headers={"Origin": ORIGIN, "X-CSRF-Token": session_info["csrf_token"]},
    )
    assert response.status_code == 200, response.text

    # 客户侧取匿名 token 不应影响员工会话：后台写操作仍用员工 token 且成功。
    _csrf_from(client)
    still_valid = client.post(
        f"{BASE}/admin/content",
        json={
            "kind": "news",
            "slug": "csrf-admin-regression",
            "title": "CSRF 后台回归",
            "summary": "验证员工 token 未被客户侧轮换影响",
            "body_html": "<p>正文</p>",
        },
        headers={"Origin": ORIGIN, "X-CSRF-Token": session_info["csrf_token"]},
    )
    assert still_valid.status_code in (200, 201), still_valid.text
