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

from sqlalchemy.orm import Session

__all__ = [
    "assert_owned_ready",
    "assets_for",
    "link_asset",
    "linked_asset_ids",
    "public_media_payload",
]


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
