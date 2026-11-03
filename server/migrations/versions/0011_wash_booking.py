"""洗毛预约字段（FR-025 / C31）。

`stock_applications` 增加：

- ``preferred_date``：期望到店/收件日期，ISO ``YYYY-MM-DD``；仅 cleaning 申请使用；
- ``return_method``：``pickup``（到店取回）或 ``ship``（寄回）；仅 cleaning 申请使用。

预约本身不收款、不锁库存；审批通过后才进入商城下单。历史掉落/服务申请两列为 NULL。
SQLite 通过 batch 模式重建表以同时加入 CHECK 约束。
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("stock_applications")}
    if "preferred_date" in columns and "return_method" in columns:
        return
    with op.batch_alter_table("stock_applications", recreate="always") as batch:
        if "preferred_date" not in columns:
            batch.add_column(sa.Column("preferred_date", sa.String(10)))
        if "return_method" not in columns:
            batch.add_column(sa.Column("return_method", sa.String(10)))
        batch.create_check_constraint(
            "ck_application_return_method",
            "return_method IS NULL OR return_method IN ('pickup','ship')",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("stock_applications")}
    if "preferred_date" not in columns and "return_method" not in columns:
        return
    with op.batch_alter_table("stock_applications", recreate="always") as batch:
        batch.drop_constraint("ck_application_return_method", type_="check")
        if "preferred_date" in columns:
            batch.drop_column("preferred_date")
        if "return_method" in columns:
            batch.drop_column("return_method")
