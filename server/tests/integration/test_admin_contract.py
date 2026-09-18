"""运营后台契约回归：询价工作台、分派、版本报价、制作节点与相册、积分申诉列表。

背景：审计（AUDIT-20260918）之后发现运营后台前端调用的路径与载荷同后端契约不一致，
本文件把修正后的真实行为固定下来，避免再出现「前端调用不存在的接口」。
路径与权限依据 03/06/07/09 文档；对象授权依据 BUG-01 结论。
"""
from __future__ import annotations

import pytest

from app.platform.ids import iso, new_id

BASE = "/api/v1"
ADMIN_ORIGIN = "http://127.0.0.1:5173"


def _csrf(csrf: dict) -> dict[str, str]:
    return {"X-CSRF-Token": csrf["csrf_token"], "Origin": ADMIN_ORIGIN}


def _inquiry_fixture(db, client, *, accept: bool):
    """建一个已提交询价。

    ``accept=True``：接受报价并生成制作单，返回 ``(inquiry_id, quote, project_id)``；
    ``accept=False``：停在 ``QUOTED``，返回 ``(inquiry_id, quote, None)``，
    用于分派/版本报价（终态询价不允许再改派或改价）。
    """
    from tests.integration.test_commission_flow import (
        accept_payload,
        create_submitted_inquiry,
        login,
        make_asset,
        make_series,
        publish_quote,
    )

    owner = login(db, "后台契约客户")
    series = make_series(db)
    asset = make_asset(db, owner.user_id)
    inquiry_id = create_submitted_inquiry(client, db, owner, series, asset)
    quote = publish_quote(db, inquiry_id=inquiry_id)
    if not accept:
        return inquiry_id, quote, None
    accepted = client.post(
        f"{BASE}/quotes/{quote.id}/accept", json=accept_payload(quote), headers=owner.headers()
    )
    assert accepted.status_code == 201, accepted.text
    return inquiry_id, quote, accepted.json()["project_id"]


def _ops_login(db, client, *, roles=("admin",)):
    from tests.conftest import admin_session, create_staff

    staff, secret = create_staff(db, login_name=f"ops-{new_id('s')[:8]}", roles=list(roles))
    db.commit()
    return staff, admin_session(db, client, staff, secret)


def test_admin_quote_workbench_lists_inquiries_with_current_quote(db, client):
    """`/admin/quotes` 是询价工作台：行以询价为单位，并带当前报价版本与金额。"""
    inquiry_id, quote, _ = _inquiry_fixture(db, client, accept=True)
    staff, csrf = _ops_login(db, client)

    response = client.get(f"{BASE}/admin/quotes", headers=_csrf(csrf))
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["items"] if item["id"] == inquiry_id)
    assert row["inquiry_id"] == inquiry_id
    assert row["quote_id"] == quote.id
    assert row["quote_version"] == quote.quote_version
    assert row["amount_cents"] == quote.total_cents
    assert row["valid_until"] == iso(quote.expires_at)
    assert row["version"] >= 1  # 补件需要询价版本号
    assert row["assignee"] is None

    # 按角色名检索只做包含匹配，不返回他人隐私字段。
    filtered = client.get(f"{BASE}/admin/quotes", params={"q": "后台契约"}, headers=_csrf(csrf))
    assert filtered.status_code == 200
    assert all(item["id"] == inquiry_id for item in filtered.json()["items"])


def test_admin_assign_accepts_staff_login_and_audits(db, client):
    """分派接受员工登录名；无效登录名返回字段错误而不是静默留空。"""
    from app.modules.commissions.models import Inquiry
    from app.platform import audit

    inquiry_id, _quote, _project = _inquiry_fixture(db, client, accept=False)
    staff, csrf = _ops_login(db, client)
    make_staff, _secret = _ops_login_staff_only(db)

    ok = client.post(
        f"{BASE}/admin/inquiries/{inquiry_id}/assign",
        json={"assignee": make_staff.login_name, "note": "由运营分派"},
        headers=_csrf(csrf),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == inquiry_id
    db.rollback()
    assert db.get(Inquiry, inquiry_id).assignee_id == make_staff.id
    actions = {row.action for row in audit.query(db, resource_id=inquiry_id)}
    assert "inquiry.assign" in actions

    bad = client.post(
        f"{BASE}/admin/inquiries/{inquiry_id}/assign",
        json={"assignee": "no-such-login"},
        headers=_csrf(csrf),
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "assignee" in bad.json()["error"]["fields"]

    cleared = client.post(
        f"{BASE}/admin/inquiries/{inquiry_id}/assign", json={"assignee": None}, headers=_csrf(csrf)
    )
    assert cleared.status_code == 200, cleared.text
    db.rollback()
    assert db.get(Inquiry, inquiry_id).assignee_id is None


def _ops_login_staff_only(db):
    from tests.conftest import create_staff

    staff, secret = create_staff(db, login_name=f"maker-{new_id('s')[:8]}", roles=["maker"])
    db.commit()
    return staff, secret


def test_admin_publish_quote_version_from_workbench(db, client):
    """版本报价走 `/admin/inquiries/{id}/quotes`：金额按明细重算，有效期由服务端换算。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import Quote

    inquiry_id, first, _project = _inquiry_fixture(db, client, accept=False)
    staff, csrf = _ops_login(db, client)

    created = client.post(
        f"{BASE}/admin/inquiries/{inquiry_id}/quotes",
        json={
            "currency": "CNY",
            "line_items": [{"label": "整装委托", "quantity": 1, "unit_price_cents": 620000}],
            "valid_days": 21,
            "terms_version": "commission-1",
            "note": "第二版报价",
        },
        headers=_csrf(csrf),
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["total_cents"] == 620000
    assert payload["quote_version"] == first.quote_version + 1
    assert payload["status"] == "PUBLISHED"
    db.rollback()
    db.expire_all()
    reloaded = db.get(Quote, first.id)
    assert reloaded is not None and reloaded.status == "SUPERSEDED"
    versions = list(
        db.scalars(sa.select(Quote.quote_version).where(Quote.inquiry_id == inquiry_id))
    )
    assert sorted(versions) == [first.quote_version, first.quote_version + 1]


def test_admin_stage_patch_is_object_scoped(db, client):
    """制作节点 PATCH 复用对象授权：未分派装师 404，运营可推进且旧确认失效规则生效。"""
    import sqlalchemy as sa

    from app.modules.commissions.models import CommissionProject, ProductionStage

    _inquiry_id, _quote, project_id = _inquiry_fixture(db, client, accept=True)
    staff, csrf = _ops_login(db, client)

    created = client.post(
        f"{BASE}/admin/commission-projects/{project_id}/stages",
        json={
            "stage_code": "PRODUCING",
            "description": "头部已完成，进入躯干制作",
            "requires_confirmation": True,
        },
        headers=_csrf(csrf),
    )
    assert created.status_code == 200, created.text
    stage = created.json()
    assert stage["status"] == "AWAITING_CUSTOMER"

    maker, maker_secret = _ops_login_staff_only(db)
    from tests.conftest import admin_session

    maker_csrf = admin_session(db, client, maker, maker_secret)
    denied = client.patch(
        f"{BASE}/admin/production-stages/{stage['id']}",
        json={"version": stage["version"], "status": "IN_PROGRESS"},
        headers=_csrf(maker_csrf),
    )
    assert denied.status_code == 404

    project = db.get(CommissionProject, project_id)
    project.maker_id = maker.id
    db.commit()
    allowed = client.patch(
        f"{BASE}/admin/production-stages/{stage['id']}",
        json={"version": stage["version"], "status": "IN_PROGRESS", "note": "已开始躯干"},
        headers=_csrf(maker_csrf),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "IN_PROGRESS"

    db.rollback()
    reloaded = db.get(ProductionStage, stage["id"])
    assert reloaded is not None
    # 内容变化且此前待客户确认：递增 revision 并作废旧确认。
    assert reloaded.revision >= 2
    assert sorted(db.scalars(sa.select(ProductionStage.status))) == ["IN_PROGRESS"]

    stale = client.patch(
        f"{BASE}/admin/production-stages/{stage['id']}",
        json={"version": stage["version"], "status": "DONE"},
        headers=_csrf(maker_csrf),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_admin_album_link_rejects_unready_asset(db, client):
    """私密相册只接受已扫描完成的素材；未完成扫描返回 UPLOAD_NOT_READY。"""
    import sqlalchemy as sa

    from app.modules.media.models import MediaAsset, MediaLink

    _inquiry_id, _quote, project_id = _inquiry_fixture(db, client, accept=True)
    staff, csrf = _ops_login(db, client)
    owner_id = db.scalar(sa.select(MediaAsset.owner_id)) or staff.id

    pending = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose="stage",
        visibility="private",
        object_key=f"stage/{new_id('k')}",
        mime="image/png",
        scan_status="pending",
    )
    ready = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose="stage",
        visibility="private",
        object_key=f"stage/{new_id('k')}",
        mime="image/png",
        scan_status="ready",
    )
    db.add_all([pending, ready])
    db.commit()

    blocked = client.post(
        f"{BASE}/admin/commission-projects/{project_id}/album",
        json={"label": "头部进度", "asset_ids": [pending.id]},
        headers=_csrf(csrf),
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "UPLOAD_NOT_READY"

    ok = client.post(
        f"{BASE}/admin/commission-projects/{project_id}/album",
        json={"label": "头部进度", "asset_ids": [ready.id]},
        headers=_csrf(csrf),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["asset_ids"] == [ready.id]
    assert "/media/" in ok.json()["notice"]  # 只给访问路径，不给签名直链
    db.rollback()
    links = list(db.scalars(sa.select(MediaLink).where(MediaLink.resource_id == project_id)))
    assert [link.asset_id for link in links] == [ready.id]


def test_points_appeals_list_is_owner_scoped(db, client):
    """`GET /points/appeals` 只返回本人申诉；他人申诉不可见。"""
    from app.modules.loyalty import service as loyalty_service
    from app.modules.loyalty.models import PointAccount, PointAppeal, PointLedger
    from tests.conftest import create_user, issue_login

    mine = create_user(db, nickname="申诉本人", phone="13800000101")
    other = create_user(db, nickname="他人", phone="13800000102")
    db.flush()
    csrf = issue_login(db, client, mine)

    def _ledger(user_id: str) -> str:
        account = PointAccount(id=new_id("account"), user_id=user_id, posted_balance=10)
        db.add(account)
        db.flush()
        row = PointLedger(
            id=new_id("ledger"),
            account_id=account.id,
            user_id=user_id,
            delta=10,
            entry_type="AWARD",
            source_type="order",
            source_id=new_id("order"),
            description="订单奖励",
        )
        db.add(row)
        db.flush()
        return row.id

    db.add(
        PointAppeal(
            id=new_id("appeal"),
            user_id=mine.id,
            ledger_id=_ledger(mine.id),
            reason="这条奖励金额与订单不符，请复核。",
        )
    )
    db.add(
        PointAppeal(
            id=new_id("appeal"),
            user_id=other.id,
            ledger_id=_ledger(other.id),
            reason="他人的申诉内容不应被读取。",
        )
    )
    db.commit()

    response = client.get(f"{BASE}/points/appeals", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    body = response.json()
    # 列表遵循 06 文档统一形状：items / next_cursor / has_more（分页 DTO 不含 total）。
    assert len(body["items"]) == 1
    assert body["has_more"] is False
    assert "他人的申诉内容" not in response.text
    assert body["items"][0]["status"] == "SUBMITTED"

    # 未登录读不到任何申诉（清掉 cookie，模拟匿名请求）。
    token = client.cookies.get("dsh_session")
    client.cookies.clear()
    anonymized = client.get(f"{BASE}/points/appeals")
    assert anonymized.status_code in (401, 403), anonymized.text
    if token:
        client.cookies.set("dsh_session", token)

    # 服务层分页接口同样只按 user_id 过滤，且总数与页大小一致。
    items, total = loyalty_service.appeals_view(db, user_id=mine.id, limit=20, offset=0)
    assert total == 1 and len(items) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/me/sessions/revoke-others"),
        ("POST", "/inquiries"),
        ("POST", "/feedback"),
        ("PATCH", "/me"),
    ],
)
def test_write_guard_is_enforced_at_router_level(db, client, method, path):
    """写请求的来源校验在组装层统一生效：任何写端点都不能漏挂（10 文档）。

    审计发现 `/me/sessions/revoke-others` 只校验 CSRF、没有 Origin 校验；
    修好后即使带合法 CSRF，来自非受信 Origin 的写请求也必须被拒。
    """
    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="来源校验本人", phone="13800000201")
    db.flush()
    csrf = issue_login(db, client, user)
    headers = {"Origin": "https://evil.example", "X-CSRF-Token": csrf}
    response = client.request(method, f"{BASE}{path}", json={}, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_INVALID"

    # 对照：受信 Origin 下同一个请求不会被来源校验拦下（是否缺少 CSRF 由各路由自己负责）。
    trusted = client.request(
        method, f"{BASE}{path}", json={}, headers={"Origin": ADMIN_ORIGIN, "X-CSRF-Token": csrf}
    )
    assert trusted.status_code != 403 or trusted.json()["error"]["code"] != "CSRF_INVALID"


@pytest.mark.parametrize("provider", ["mock", "sandbox"])
def test_production_payment_providers_stay_rejected(provider):
    """回归锚点：沙盒别名在生产配置中始终被拒绝（BUG-03 不回归）。"""
    from pydantic import ValidationError

    from app.platform.config import Settings

    with pytest.raises(ValidationError):
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
            sms_adapter="aliyun",
            payment_providers=provider,
            storage_backend="s3",
            smtp_host="mail.test",
        )
