"""询价分派责任人（03 文档：/admin/quotes 支持分派、补件、版本报价、拒绝）。"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 使用实时 metadata；全新库已有该列，旧库在此增量补齐。
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "assignee_id" not in columns:
        op.add_column("inquiries", sa.Column("assignee_id", sa.String(48)))
        op.create_index("ix_inquiries_assignee_id", "inquiries", ["assignee_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("inquiries")}
    if "ix_inquiries_assignee_id" in indexes:
        op.drop_index("ix_inquiries_assignee_id", table_name="inquiries")
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "assignee_id" in columns:
        op.drop_column("inquiries", "assignee_id")
