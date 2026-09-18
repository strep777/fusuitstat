"""公开展示授权与撤回回归（R22 / C18 / FR-002）。

依据：
- `功能确认清单-已确认.md` C18：**默认公开客户身份、成品，采用单独可撤回授权**；
  N12（默认展示范围与撤回生效时限）待补，因此撤回按**即时生效**实现（最保守）；
- `02_软件需求规格说明书.md` FR-002：无公开授权的委托作品不出现；
- `文档/03_站点路由与页面功能.md`：私密/未授权内容不得通过直链访问。

锁定点：
1. 未取得授权的作品发布被拒（原有行为）；
2. **撤回授权后**：公开列表不再包含、直链 404、公开载荷不再出现客户姓名；
3. 撤回是幂等的，且留下可审计的授权撤回记录（`consents` 里 purpose=public_display 且已撤回）；
4. 撤回立即登记预渲染任务（缓存与静态产物同步失效）；
5. 非本人资源一律 404（不透露存在性）；
6. 本人上传的公开素材同样可撤回，撤回后公开载荷为空；
7. 「我的公开展示授权」如实展示当前状态（未发布=PENDING、已撤回=RETRACTED）。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.content import repository as content_repo
from app.modules.content import service as content_service
from app.modules.content.models import ContentEntry
from app.modules.identity.models import Consent
from app.modules.media.models import MediaAsset
from app.modules.media.service import public_media_payload
from app.platform.errors import AppError
from app.platform.ids import new_id
from tests.conftest import create_user, issue_login


@pytest.fixture()
def client_with_user(db: Session, client: TestClient):
    """一位已登录客户：用于持有作品授权与上传素材。"""
    user = create_user(db, nickname="授权客户", phone="13800007777")
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()
    return {"user": user, "csrf": csrf}


def _work(db: Session, *, client_user_id: str | None, authorized: bool = True,
          name: str | None = "授权客户", status: str = "published") -> ContentEntry:
    entry = ContentEntry(
        id=new_id("content"),
        kind="work",
        slug=f"work-{new_id('s')[:8]}",
        locale="zh-CN",
        title="霜龙全装",
        summary="",
        sanitized_body="<p>成品说明</p>",
        status=status,
        publish_at=1,
        display_authorized=1 if authorized else 0,
        client_display_name=name,
        client_user_id=client_user_id,
        species="龙",
    )
    db.add(entry)
    db.commit()
    return entry


def _asset(db: Session, *, owner_id: str, public: bool = True) -> MediaAsset:
    asset = MediaAsset(
        id=new_id("asset"),
        owner_id=owner_id,
        purpose="work",
        visibility="public" if public else "private",
        object_key=f"work/{new_id('k')[:8]}.png",
        mime="image/png",
        scan_status="ready",
        is_public_ok=1 if public else 0,
    )
    db.add(asset)
    db.commit()
    return asset


# ------------------------------------------------------------------ 展示门禁
def test_published_work_without_authorization_is_not_public(
    client: TestClient, db: Session, client_with_user: dict
) -> None:
    """撤回过授权的作品：列表不含、直链 404、公开载荷不再出现客户姓名。"""
    entry = _work(db, client_user_id=client_with_user["user"].id)
    listed = client.get("/api/v1/content/work")
    assert entry.slug in [item["slug"] for item in listed.json()["items"]]

    detail = client.get(f"/api/v1/content/work/{entry.slug}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["credits"]["client_display_name"] == "授权客户"

    retracted = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "work", "resource_id": entry.id, "reason": "不希望公开姓名"},
    )
    assert retracted.status_code == 200, retracted.text
    body = retracted.json()
    assert body["display_authorized"] is False
    assert body["already_retracted"] is False
    assert body["prebuild_job_id"], "撤回必须登记缓存失效任务"

    db.expire_all()
    refreshed = content_repo.get_entry(db, entry.id)
    assert refreshed is not None and refreshed.display_authorized == 0
    # 直链立即 404，且不再出现在公开列表里
    assert client.get(f"/api/v1/content/work/{entry.slug}").status_code == 404
    after = client.get("/api/v1/content/work")
    assert entry.slug not in [item["slug"] for item in after.json()["items"]]

    # 即使条目仍是 published，公开载荷也不能再带客户姓名
    payload = content_service.entry_payload(refreshed)
    assert payload["credits"]["client_display_name"] is None

    # 撤回留证：purpose=public_display 且已撤回
    consent = db.scalars(
        sa.select(Consent).where(
            Consent.user_id == client_with_user["user"].id,
            Consent.purpose == "public_display",
            Consent.resource_id == entry.id,
        )
    ).one()
    assert consent.revoked_at is not None
    assert consent.revoke_reason == "不希望公开姓名"

    # 幂等：重复撤回不报错，也不再改数据
    again = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "work", "resource_id": entry.id, "reason": "再次撤回"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["already_retracted"] is True


def test_retraction_cannot_touch_someone_elses_work(
    client: TestClient, db: Session, client_with_user: dict
) -> None:
    other = create_user(db, nickname="别人", phone="13800008888")
    db.commit()
    entry = _work(db, client_user_id=other.id)

    response = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "work", "resource_id": entry.id, "reason": "不是我的"},
    )
    assert response.status_code == 404, response.text
    db.expire_all()
    refreshed = content_repo.get_entry(db, entry.id)
    assert refreshed is not None and refreshed.display_authorized == 1

    # 未登记授权归属的作品也不允许撤回（运营需要在后台补登记）
    orphan = _work(db, client_user_id=None)
    missing = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "work", "resource_id": orphan.id, "reason": "归属未登记"},
    )
    assert missing.status_code == 404


def test_retraction_requires_reason_and_known_type(
    client: TestClient, db: Session, client_with_user: dict
) -> None:
    entry = _work(db, client_user_id=client_with_user["user"].id)
    too_short = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "work", "resource_id": entry.id, "reason": "x"},
    )
    assert too_short.status_code == 422
    bad_type = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "order", "resource_id": entry.id, "reason": "类型不对"},
    )
    assert bad_type.status_code == 422


def test_media_asset_retraction_removes_public_payload(
    client: TestClient, db: Session, client_with_user: dict
) -> None:
    asset = _asset(db, owner_id=client_with_user["user"].id)
    assert public_media_payload(db, asset.id) is not None

    response = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "media", "resource_id": asset.id, "reason": "不希望照片公开"},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    refreshed = db.get(MediaAsset, asset.id)
    assert refreshed is not None
    assert refreshed.is_public_ok == 0 and refreshed.visibility == "private"
    assert public_media_payload(db, asset.id) is None

    foreign = create_user(db, nickname="素材别人", phone="13800009999")
    db.commit()
    theirs = _asset(db, owner_id=foreign.id)
    denied = client.post(
        "/api/v1/me/display-authorizations/retract",
        json={"resource_type": "media", "resource_id": theirs.id, "reason": "不是我的素材"},
    )
    assert denied.status_code == 404


def test_my_display_authorizations_reflect_state(
    client: TestClient, db: Session, client_with_user: dict
) -> None:
    mine = _work(db, client_user_id=client_with_user["user"].id)
    pending = _work(db, client_user_id=client_with_user["user"].id, status="draft")
    retracted = _work(db, client_user_id=client_with_user["user"].id, authorized=False)
    other = create_user(db, nickname="旁人", phone="13800001010")
    db.commit()
    _work(db, client_user_id=other.id)
    asset = _asset(db, owner_id=client_with_user["user"].id)

    listed = client.get("/api/v1/me/display-authorizations")
    assert listed.status_code == 200, listed.text
    items = {item["resource_id"]: item for item in listed.json()["items"]}
    assert listed.json()["total"] == 4
    assert items[mine.id]["state"] == "ACTIVE" and items[mine.id]["visible_now"] is True
    assert items[mine.id]["client_display_name"] == "授权客户"
    assert items[pending.id]["state"] == "PENDING" and items[pending.id]["visible_now"] is False
    assert items[retracted.id]["state"] == "RETRACTED"
    assert items[retracted.id]["client_display_name"] is None
    assert items[asset.id]["resource_type"] == "media" and items[asset.id]["visible_now"] is True
    assert all(item["resource_id"] != other.id for item in listed.json()["items"])

    # 未登录不可读
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/me/display-authorizations").status_code == 401


def test_display_authorization_is_required_for_publishing(
    db: Session, client_with_user: dict
) -> None:
    """未取得授权的作品不能发布；撤回后重新发布同样被拒。"""
    unauthorized = _work(db, client_user_id=None, authorized=False, status="draft")
    with pytest.raises(AppError) as error:
        content_service.publish_entry(
            db, entry_id=unauthorized.id, version=unauthorized.version, actor_id="stf_x"
        )
    assert error.value.code == "STATE_CONFLICT"
    assert "公开展示授权" in error.value.message
    db.rollback()

    entry = _work(db, client_user_id=client_with_user["user"].id, status="draft")
    content_service.publish_entry(db, entry_id=entry.id, version=entry.version, actor_id="stf_x")
    db.commit()
    db.expire_all()
    refreshed = content_repo.get_entry(db, entry.id)
    assert refreshed is not None and refreshed.status == "published"

    content_service.retract_display_authorization(
        db,
        user_id=client_with_user["user"].id,
        resource_type="work",
        resource_id=entry.id,
        reason="客户撤回授权",
    )
    db.commit()
    db.expire_all()
    refreshed = content_repo.get_entry(db, entry.id)
    assert refreshed is not None
    # 撤回后条目状态不变（仍是 published），但展示门禁已关闭：直链与公开载荷都不再出现。
    assert refreshed.status == "published" and refreshed.display_authorized == 0
    assert content_repo.get_published(db, kind="work", slug=refreshed.slug) is None
