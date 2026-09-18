"""权限码与内置角色（唯一维护位置）。

后台使用显式权限码而非单一 ``is_admin``。内容发布、报价发布、退款申请、
退款批准、积分调整分别授权（SRS 人员与权限边界）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity import repository as repo
from app.modules.identity.models import Permission, Role, RolePermission

# (权限码, 名称, 分组)
PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("dashboard.read", "查看运营概览", "general"),
    ("content.read", "查看内容（含草稿）", "content"),
    ("content.write", "编辑内容草稿", "content"),
    ("content.publish", "发布与下架内容", "content"),
    ("media.manage", "管理素材授权与派生图", "content"),
    ("catalog.read", "查看商品与角色", "catalog"),
    ("catalog.write", "编辑商品、SKU 与库存", "catalog"),
    ("quote.read", "查看询价与报价", "commission"),
    ("quote.review", "分派询价与发布报价", "commission"),
    ("schedule.manage", "管理档期与名额", "commission"),
    ("production.read", "查看制作单", "commission"),
    ("production.write", "更新制作节点与变更单", "commission"),
    ("order.read", "查看订单", "commerce"),
    ("order.fulfill", "发货与关闭订单", "commerce"),
    ("payment.read", "查看收款与查单", "finance"),
    ("invoice.manage", "查看与签发发票", "finance"),
    ("refund.request", "申请退款", "finance"),
    ("refund.approve", "批准退款", "finance"),
    ("finance.reconcile", "对账与差异处置", "finance"),
    ("feedback.read", "查看反馈", "feedback"),
    ("feedback.review", "审核反馈与发放奖励", "feedback"),
    ("points.read", "查看星绒币流水", "loyalty"),
    ("points.adjust", "申请星绒币调整", "loyalty"),
    ("points.approve", "批准星绒币调整", "loyalty"),
    ("user.read", "查看脱敏用户资料", "identity"),
    ("user.manage", "限制或恢复账号", "identity"),
    ("staff.manage", "管理员工、角色与 MFA", "identity"),
    ("settings.manage", "维护经营信息与功能开关", "platform"),
    ("audit.read", "查询审计日志", "platform"),
    ("ops.read", "查看任务与失败重试", "platform"),
    ("export.data", "导出数据（含审计）", "platform"),
)

# (角色码, 名称, 说明, 权限码集合)
ROLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "admin",
        "系统管理员",
        "账号、角色、配置与异常处置；权限变更与导出均审计，不能覆盖账本",
        tuple(code for code, _name, _group in PERMISSIONS),
    ),
    (
        "content_editor",
        "内容编辑",
        "编辑、预览、发布作品和公告；无手机号明文与订单金额修改权",
        (
            "dashboard.read",
            "content.read",
            "content.write",
            "content.publish",
            "media.manage",
            "catalog.read",
            "catalog.write",
            "audit.read",
        ),
    ),
    (
        "support",
        "客服",
        "查看分配的询价和售后、回复反馈；联系方式默认脱敏",
        (
            "dashboard.read",
            "quote.read",
            "order.read",
            "feedback.read",
            "feedback.review",
            "user.read",
            "points.read",
            "content.read",
            "catalog.read",
        ),
    ),
    (
        "maker",
        "制作装师",
        "查看分配给自己的制作单并更新节点；无支付密钥与退款权",
        ("dashboard.read", "production.read", "production.write", "order.read", "content.read"),
    ),
    (
        "finance",
        "财务",
        "查询账单、审核退款、执行对账",
        (
            "dashboard.read",
            "order.read",
            "payment.read",
            "invoice.manage",
            "refund.request",
            "refund.approve",
            "finance.reconcile",
            "points.read",
            "points.adjust",
            "points.approve",
            "export.data",
            "audit.read",
        ),
    ),
)

# 职责分离：同一员工不能同时拥有这两组权限（C21：申请与批准分离，单人审批）。
SEPARATION_PAIRS: tuple[tuple[str, str], ...] = (
    ("refund.request", "refund.approve"),
    ("points.adjust", "points.approve"),
    ("content.write", "content.publish"),
)


def sync_catalog(session: Session) -> dict[str, int]:
    """幂等同步权限与角色定义。新权限只增不删。"""
    created_permissions = 0
    by_code: dict[str, Permission] = {}
    for code, name, group in PERMISSIONS:
        row = repo.get_permission_by_code(session, code)
        if row is None:
            row = Permission(code=code, name=name, group=group)
            session.add(row)
            session.flush()
            created_permissions += 1
        else:
            row.name = name
            row.group = group
        by_code[code] = row

    created_roles = 0
    for code, name, description, permission_codes in ROLES:
        role = repo.get_role_by_code(session, code)
        if role is None:
            role = Role(code=code, name=name, description=description, is_system=1)
            session.add(role)
            session.flush()
            created_roles += 1
        else:
            role.name = name
            role.description = description
        existing = {
            row.permission_id
            for row in session.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        }
        for permission_code in permission_codes:
            permission = by_code[permission_code]
            if permission.id not in existing:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.flush()
    return {"permissions_created": created_permissions, "roles_created": created_roles}


def permissions_for_roles(session: Session, role_codes: list[str]) -> list[str]:
    codes: set[str] = set()
    for code in role_codes:
        role = repo.get_role_by_code(session, code)
        if role is None:
            continue
        codes.update(
            session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            )
        )
    return sorted(codes)
