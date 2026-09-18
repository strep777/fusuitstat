"""询价关联服务申请（C28：服务先申请再报价）。

`inquiries.application_id` 记录这份询价由哪一条**已通过审批**的服务申请发起；
唯一索引保证一份审批只能用于一份询价，避免同一份审批被反复消耗。

默认 NULL，因此既有询价与未开启申请的服务不受影响。
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "application_id" not in columns:
        op.add_column("inquiries", sa.Column("application_id", sa.String(48)))
    indexes = {index["name"] for index in inspector.get_indexes("inquiries")}
    if "uq_inquiries_application" not in indexes:
        op.create_index("uq_inquiries_application", "inquiries", ["application_id"], unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("inquiries")}
    if "uq_inquiries_application" in indexes:
        op.drop_index("uq_inquiries_application", table_name="inquiries")
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "application_id" in columns:
        op.drop_column("inquiries", "application_id")
