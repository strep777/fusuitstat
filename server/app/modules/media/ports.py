"""媒体模块对外端口。

其他模块**不得**导入本模块的 ORM 模型或 repository（04 文档）；
素材归属、可引用性与公开载荷一律通过这里的函数获取。

约束：
- 只返回可引用的素材（``scan_status == "ready"`` 且未被隔离），未完成扫描的编号直接拒绝；
- 公开载荷只给相对路径与派生尺寸，不含私密签名 URL；
- 私密附件的下载仍走 ``/media/{id}/access`` 的逐对象鉴权与短期签名。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

__all__ = [
    "assert_owned_ready",
    "assets_for",
    "link_asset",
    "linked_asset_ids",
    "public_assets_for_owner",
    "public_media_payload",
    "purge_owned_unreferenced",
    "retract_public_display",
]


def public_assets_for_owner(session: Session, *, owner_id: str) -> list[dict[str, Any]]:
    """本人上传且当前被设为公开的素材（C18：客户可在账户里看到并撤回公开授权）。"""
    from app.modules.media.models import MediaAsset

    rows = session.scalars(
        sa.select(MediaAsset).where(
            MediaAsset.owner_id == owner_id,
            MediaAsset.status == "active",
            MediaAsset.visibility == "public",
            MediaAsset.is_public_ok == 1,
        )
    )
    return [
        {
            "id": row.id,
            "filename": row.original_filename or row.object_key,
            "mime": row.mime,
            "created_at": row.created_at,
            "updated_at": row.updated_at or row.created_at,
        }
        for row in rows
    ]


def purge_owned_unreferenced(session: Session, *, owner_id: str, limit: int = 200) -> int:
    """注销清理：删除本人**未被任何业务记录引用**且非公开的附件。

    被订单/委托/制作单引用过的附件属于交易与履约证据，保留；公开素材（已进入公开页面
    或静态产物）不在注销清理范围内，由撤回展示授权单独处理。
    """
    from app.modules.media.models import MediaAsset, MediaLink

    rows = list(
        session.scalars(
            sa.select(MediaAsset)
            .outerjoin(MediaLink, MediaLink.asset_id == MediaAsset.id)
            .where(
                MediaAsset.owner_id == owner_id,
                MediaAsset.status == "active",
                MediaAsset.visibility == "private",
                MediaLink.id.is_(None),
            )
            .limit(limit)
        )
    )
    for asset in rows:
        asset.status = "deleted"
    session.flush()
    return len(rows)


def retract_public_display(session: Session, *, asset_id: str, owner_id: str) -> bool:
    """撤回本人素材的公开展示：立即取消公开可见性。

    返回 False 表示素材不存在或不属于该用户（调用方按 404 处理，不泄露存在性）。
    已撤回过再调用同样返回 True（幂等），因此客户重复点击不会报错。
    """
    from app.modules.media.models import MediaAsset
    from app.platform.ids import now_ms

    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.owner_id != owner_id:
        return False
    asset.is_public_ok = 0
    asset.visibility = "private"
    asset.updated_at = now_ms() if asset.updated_at else asset.updated_at
    session.flush()
    return True


def public_media_payload(session: Session, asset_id: str) -> dict[str, Any] | None:
    """公开素材载荷（相对路径，可安全进入静态产物）。"""
    from app.modules.media.service import public_media_payload as read

    return read(session, asset_id)


def assets_for(
    session: Session,
    *,
    resource_type: str,
    resource_id: str,
    role: str | None = None,
) -> list[Any]:
    """某业务对象已登记的素材行（只给编号与归属，不下发签名直链）。"""
    from app.modules.media.service import assets_for as read

    return read(session, resource_type=resource_type, resource_id=resource_id, role=role)


def linked_asset_ids(
    session: Session, *, resource_type: str, resource_id: str, role: str | None = None
) -> list[str]:
    """某业务对象已登记的素材编号，按登记顺序返回。

    供需要「只取编号」的模块使用，避免它们为了一个 join 去导入媒体模块的 ORM。
    """
    from app.modules.media.service import assets_for as read

    rows = read(session, resource_type=resource_type, resource_id=resource_id, role=role)
    return [row.id for row in rows]


def link_asset(
    session: Session,
    *,
    asset_id: str,
    resource_type: str,
    resource_id: str,
    role: str = "attachment",
) -> Any:
    """登记素材引用。未完成扫描或安全解码的素材一律拒绝（``UPLOAD_NOT_READY``）。"""
    from app.modules.media.service import link_asset as write

    return write(
        session,
        asset_id=asset_id,
        resource_type=resource_type,
        resource_id=resource_id,
        role=role,
    )


def assert_owned_ready(
    session: Session, *, asset_ids: list[str], owner_id: str, purpose: str
) -> list[Any]:
    """校验一批素材属于本人、用途匹配且已完成扫描，返回素材行。

    与 ``assert_owned_ready_design`` 的差别只在 ``purpose``：反馈附件用 ``feedback``，
    设定图用 ``design``；两者都不允许引用他人或未处理完成的素材。
    """
    from app.modules.media.service import assert_owned_ready as check

    return check(session, asset_ids=asset_ids, owner_id=owner_id, purpose=purpose)
