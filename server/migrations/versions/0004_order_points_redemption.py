"""积分抵扣字段（C15：结算冻结 → 支付消费 → 关闭释放 → 退款按原订单分摊返还）。

订单与结算会话各加一列组：
- ``orders.points_redeemed`` / ``points_discount_cents``：本单冻结（后消费）的积分与它抵掉的钱；
- ``orders.points_restored``：已通过退款返还的积分（幂等基准，避免重复返还）；
- ``checkout_sessions.redeem_points`` / ``points_discount_cents``：预览时服务端裁定结果，
  建单时用它做一致性核对（与 ``coupon_code`` 同样的处理方式）。

同时必须更新金额公式约束：08 文档的 ``total = goods - discount + shipping``
在启用积分抵扣后是 ``total = goods - discount - points_discount + shipping``。
SQLite 不能直接改 CHECK 约束，因此用 Alembic 的 batch 模式重建 ``orders`` 表
（空表或已有数据都能安全复制）。默认全为 0，因此既有订单的公式与取值不受影响。
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_OLD_FORMULA = "total_cents = goods_cents - discount_cents + shipping_cents"
_NEW_FORMULA = (
    "total_cents = goods_cents - discount_cents - points_discount_cents + shipping_cents"
)

_COLUMNS: tuple[tuple[str, tuple[tuple[str, sa.types.TypeEngine], ...]], ...] = (
    (
        "orders",
        (
            ("points_redeemed", sa.Integer()),
            ("points_discount_cents", sa.Integer()),
            ("points_restored", sa.Integer()),
        ),
    ),
    (
        "checkout_sessions",
        (
            ("redeem_points", sa.Integer()),
            ("points_discount_cents", sa.Integer()),
        ),
    ),
)


def _check_constraints(table: str) -> dict[str, str]:
    rows = sa.inspect(op.get_bind()).get_check_constraints(table)
    return {str(row["name"]): str(row.get("sqltext") or "") for row in rows}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 使用实时 metadata；全新库已有这些列，旧库在此增量补齐。
    for table, columns in _COLUMNS:
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, column_type in columns:
            if name not in existing:
                op.add_column(
                    table,
                    sa.Column(name, column_type, nullable=False, server_default="0"),
                )

    constraints = _check_constraints("orders")
    if _OLD_FORMULA in constraints.values():
        with op.batch_alter_table("orders", recreate="always") as batch:
            batch.drop_constraint("ck_orders_total_formula", type_="check")
            batch.create_check_constraint("ck_orders_total_formula", _NEW_FORMULA)
            if "ck_orders_points_amounts" not in constraints:
                batch.create_check_constraint(
                    "ck_orders_points_amounts",
                    "points_discount_cents >= 0 AND points_redeemed >= 0 AND points_restored >= 0",
                )
            if "ck_orders_points_le_goods" not in constraints:
                batch.create_check_constraint(
                    "ck_orders_points_le_goods",
                    "points_discount_cents <= goods_cents - discount_cents",
                )


def downgrade() -> None:
    # 先撤掉积分抵扣公式，再删除列：否则删列时旧约束仍要求新列存在。
    constraints = _check_constraints("orders")
    if _NEW_FORMULA in constraints.values():
        with op.batch_alter_table("orders", recreate="always") as batch:
            batch.drop_constraint("ck_orders_total_formula", type_="check")
            batch.create_check_constraint("ck_orders_total_formula", _OLD_FORMULA)
            for name in ("ck_orders_points_amounts", "ck_orders_points_le_goods"):
                if name in constraints:
                    batch.drop_constraint(name, type_="check")

    inspector = sa.inspect(op.get_bind())
    for table, columns in _COLUMNS:
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, _column_type in columns:
            if name in existing:
                op.drop_column(table, name)
