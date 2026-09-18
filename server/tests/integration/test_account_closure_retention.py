"""注销后的清理与依法隔离保留回归（R22b / C22 / 10 文档「隐私和保留草案」）。

依据：
- `10_手机号登录安全与合规.md` 第 24 行：注销区分**停止服务、撤销会话、删除非必要资料、
  隔离依法保留交易记录**；不能以任意理由永久拒绝注销；
- 同文件「隐私和保留草案」：草稿 30 天、订单/付款/退款/合同与发票在账户注销后**隔离保留**；
- `功能确认清单-已确认.md` C22：最小化收集；区分履约/交易法定留存与可删除资料。

锁定点：
1. 未到期的注销账号不清理（页面承诺的「清理计划完成时间」之前不动数据）；
2. 到期后清理**可删除资料**：地址、购物车、未提交草稿、未被引用的私有附件、站内通知，
   以及手机号密文与查找值（改成不可逆墓碑）；
3. **依法隔离保留**：订单、发票、物流、合同/授权留证、积分账本、已提交询价与报价一行不减；
4. 每个账号一行 `closure_executions`，重复扫描幂等（不重复删除、不重复计数）；
5. 清理后原手机号无法再登录到该账号（不能靠旧手机号恢复已注销账号）。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.commerce.models import Cart, Order
from app.modules.commissions.models import Inquiry
from app.modules.identity import repository as identity_repo
from app.modules.identity import service as identity_service
from app.modules.identity.models import Address, ClosureExecution, Consent, User
from app.modules.media.models import MediaAsset
from app.modules.notifications.models import Notification
from app.platform.ids import new_id, now_ms
from app.platform.security import decrypt_pii, normalize_phone, phone_lookup
from tests.conftest import create_address, create_user, issue_login

DAY_MS = 24 * 3600 * 1000


def _closed_user(db: Session, *, closed_days_ago: int, phone: str = "13800006666") -> User:
    user = create_user(db, nickname="注销客户", phone=phone)
    user.status = "closed"
    user.closed_at = now_ms() - closed_days_ago * DAY_MS
    user.closure_requested_at = user.closed_at
    db.commit()
    return user


def _seed_closable_data(db: Session, *, user: User) -> dict[str, str]:
    """准备可删除资料 + 依法保留的交易记录各一份。"""
    address = create_address(db, user_id=user.id)
    cart = Cart(id=new_id("cart"), user_id=user.id)
    db.add(cart)
    db.flush()
    draft = Inquiry(
        id=new_id("inquiry"),
        user_id=user.id,
        status="DRAFT",
        series_id="series_x",
        character_name="草稿角色",
        species="狼",
        suit_type="full",
        requirements="草稿内容",
        design_asset_ids=[],
        rights_declaration=1,
    )
    submitted = Inquiry(
        id=new_id("inquiry"),
        user_id=user.id,
        status="SUBMITTED",
        series_id="series_x",
        character_name="已提交角色",
        species="狼",
        suit_type="full",
        requirements="已提交内容",
        design_asset_ids=[],
        rights_declaration=1,
        submitted_at=now_ms(),
    )
    order = Order(
        order_no=f"SO{new_id('n')[:10]}",
        user_id=user.id,
        kind="stock_goods",
        status="ACTIVE",
        currency="CNY",
        goods_cents=12000,
        discount_cents=0,
        points_discount_cents=0,
        points_redeemed=0,
        points_restored=0,
        shipping_cents=0,
        total_cents=12000,
    )
    asset = MediaAsset(
        id=new_id("asset"),
        owner_id=user.id,
        purpose="design",
        visibility="private",
        object_key=f"design/{new_id('k')[:8]}.png",
        mime="image/png",
        scan_status="ready",
    )
    consent = Consent(
        id=new_id("consent"),
        user_id=user.id,
        purpose="terms",
        document_version="shop-1",
        content_hash="hash",
        resource_type="order",
        resource_id=order.id,
    )
    notification = Notification(
        id=new_id("notification"),
        user_id=user.id,
        event_type="order.paid",
        template_version="v1",
        category="transaction",
        title="订单已支付",
        body="正文",
    )
    db.add_all([draft, submitted, order, asset, consent, notification])
    db.commit()
    return {
        "address": address.id,
        "draft": draft.id,
        "submitted": submitted.id,
        "order": order.id,
        "asset": asset.id,
        "consent": consent.id,
    }


def _exists(db: Session, model: type, row_id: str) -> bool:
    db.expire_all()
    return db.get(model, row_id) is not None


def test_closure_cleanup_waits_for_the_window(client: TestClient, db: Session) -> None:
    user = _closed_user(db, closed_days_ago=1)
    seeded = _seed_closable_data(db, user=user)

    result = identity_service.process_due_closures(db)
    db.commit()
    assert result["processed"] == 0, "未到清理窗口不得动数据"
    assert _exists(db, Address, seeded["address"])
    assert db.scalar(sa.select(sa.func.count()).select_from(ClosureExecution)) == 0


def test_closure_cleanup_purges_deletable_and_keeps_legal_records(
    client: TestClient, db: Session
) -> None:
    user = _closed_user(db, closed_days_ago=31)
    seeded = _seed_closable_data(db, user=user)
    original_phone = decrypt_pii(user.phone_ciphertext)
    assert original_phone == normalize_phone("13800006666")

    result = identity_service.process_due_closures(db)
    db.commit()
    assert result["processed"] == 1
    purged = result["executions"][0]["purged"]
    retained = result["executions"][0]["retained"]

    # 可删除资料：地址、购物车、草稿、私有附件、站内通知、手机号身份
    assert purged["addresses"] == 1 and not _exists(db, Address, seeded["address"])
    assert not _exists(db, Inquiry, seeded["draft"])
    assert purged["notifications"] == 1
    assert purged["private_assets"] == 1
    db.expire_all()
    asset = db.get(MediaAsset, seeded["asset"])
    assert asset is not None and asset.status == "deleted"
    assert purged["phone_identity"] == 1

    # 手机号不可再解密，查找值改成墓碑：旧手机号登录会创建**新账号**（手机号无法反向找到旧账号）
    db.expire_all()
    refreshed = db.get(User, user.id)
    assert refreshed is not None
    assert refreshed.phone_ciphertext == ""
    assert refreshed.nickname == "已注销用户" and refreshed.avatar_asset_id is None
    assert refreshed.phone_lookup != phone_lookup(normalize_phone("13800006666"))
    assert identity_repo.get_user_by_phone_lookup(
        db, phone_lookup(normalize_phone("13800006666"))
    ) is None

    # 依法隔离保留：订单、发票/物流等关联、合同与授权留证、已提交询价一行不减
    assert _exists(db, Order, seeded["order"])
    assert _exists(db, Inquiry, seeded["submitted"])
    assert _exists(db, Consent, seeded["consent"])
    assert retained["orders"] == 1
    assert retained["inquiries"] == 1
    assert retained["consents"] == 1

    # 执行记录：可回答「删了什么、保留了什么」
    execution = db.scalars(
        sa.select(ClosureExecution).where(ClosureExecution.user_id == user.id)
    ).one()
    assert execution.purged is not None and execution.purged["addresses"] == 1
    assert execution.retained is not None and execution.retained["orders"] == 1


def test_closure_cleanup_is_idempotent(client: TestClient, db: Session) -> None:
    user = _closed_user(db, closed_days_ago=45, phone="13800005555")
    seeded = _seed_closable_data(db, user=user)

    first = identity_service.process_due_closures(db)
    db.commit()
    assert first["processed"] == 1
    second = identity_service.process_due_closures(db)
    db.commit()
    assert second["processed"] == 0, "同一账号不得被重复清理"
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ClosureExecution)
            .where(ClosureExecution.user_id == user.id)
        )
        == 1
    )
    assert _exists(db, Order, seeded["order"])


def test_closed_account_cannot_log_in_again(client: TestClient, db: Session) -> None:
    """注销后立即停止服务：已撤销会话读不到数据，也不能用旧手机号直接拿回账号。"""
    user = create_user(db, nickname="待注销", phone="13800004444")
    csrf = issue_login(db, client, user)
    client.headers["X-CSRF-Token"] = csrf
    db.commit()
    assert client.get("/api/v1/me").status_code == 200

    from app.modules.identity.schemas import ClosureRequest  # noqa: F401  (仅说明接口形状)

    result = identity_service.request_closure(
        db, user=user, reason="不再使用", acknowledged=True
    )
    db.commit()
    assert result["status"] == "REQUESTED"
    assert result["scheduled_for"], "必须给出可核对的清理计划时间"

    # 会话已撤销：原 cookie 不能再读本人数据
    assert client.get("/api/v1/me").status_code in (401, 403)

    # 到期清理后，旧手机号查找不到该账号（登录会创建新账号而不是恢复已注销账号）
    user.closed_at = now_ms() - 31 * DAY_MS
    db.commit()
    identity_service.process_due_closures(db)
    db.commit()
    assert (
        identity_repo.get_user_by_phone_lookup(db, phone_lookup(normalize_phone("13800004444")))
        is None
    )


def test_cleanup_handler_reports_closure_count(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """worker 的定期清理必须把注销清理纳入，并在结果里如实报告数量。"""
    from app.platform.reliability import OutboxEvent
    from app.worker import _cleanup

    user = _closed_user(db, closed_days_ago=40, phone="13800003333")
    _seed_closable_data(db, user=user)
    db.commit()

    event = OutboxEvent(
        id=new_id("event"),
        event_id=new_id("event"),
        event_type="ops.cleanup",
        aggregate_id="scheduler",
        aggregate_version=1,
        payload={},
        available_at=now_ms(),
    )
    result = _cleanup(db, event)
    db.commit()
    assert "closures=1" in result
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ClosureExecution)
            .where(ClosureExecution.user_id == user.id)
        )
        == 1
    )


def test_stale_drafts_are_purged_by_retention_window(db: Session) -> None:
    """未提交草稿超过保留窗口被清理；新草稿与已提交询价保留（10 文档「草稿 30 天」）。"""
    from app.platform.reliability import OutboxEvent
    from app.worker import _cleanup

    user = create_user(db, nickname="草稿客户", phone="13800002222")
    stale = Inquiry(
        id=new_id("inquiry"),
        user_id=user.id,
        status="DRAFT",
        series_id="series_x",
        character_name="过期草稿",
        species="狼",
        suit_type="full",
        requirements="很久以前的草稿",
        design_asset_ids=[],
        rights_declaration=0,
        updated_at=now_ms() - 45 * DAY_MS,
    )
    fresh = Inquiry(
        id=new_id("inquiry"),
        user_id=user.id,
        status="DRAFT",
        series_id="series_x",
        character_name="新草稿",
        species="狼",
        suit_type="full",
        requirements="刚写的草稿",
        design_asset_ids=[],
        rights_declaration=0,
        updated_at=now_ms(),
    )
    submitted = Inquiry(
        id=new_id("inquiry"),
        user_id=user.id,
        status="SUBMITTED",
        series_id="series_x",
        character_name="已提交",
        species="狼",
        suit_type="full",
        requirements="已提交的询价",
        design_asset_ids=[],
        rights_declaration=1,
        submitted_at=now_ms() - 45 * DAY_MS,
        updated_at=now_ms() - 45 * DAY_MS,
    )
    db.add_all([stale, fresh, submitted])
    db.commit()
    stale_id, fresh_id, submitted_id = stale.id, fresh.id, submitted.id

    event = OutboxEvent(
        id=new_id("event"),
        event_id=new_id("event"),
        event_type="ops.cleanup",
        aggregate_id="scheduler",
        aggregate_version=1,
        payload={},
        available_at=now_ms(),
    )
    result = _cleanup(db, event)
    db.commit()
    assert "drafts=1" in result
    assert not _exists(db, Inquiry, stale_id)
    assert _exists(db, Inquiry, fresh_id)
    assert _exists(db, Inquiry, submitted_id), "已提交的询价按保留期处理，不随草稿清理"
