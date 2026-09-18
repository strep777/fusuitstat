"""私密素材访问只通过业务端口判断授权。"""
from sqlalchemy.orm import Session

from app.modules.commissions.ports import can_access_asset
from app.modules.identity.ports import staff_permissions


def can_read_private_asset(session: Session, *, asset_id: str, principal_id: str, is_staff: bool) -> bool:
    if is_staff:
        permissions = staff_permissions(session, principal_id)
        if "schedule.manage" in permissions:
            return True
        if "production.read" not in permissions:
            return False
    return can_access_asset(session, asset_id=asset_id, principal_id=principal_id, is_staff=is_staff)
