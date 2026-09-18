"""询价首响时间（C30：询价 3 个工作日首响，逾期提醒据此判定）。

`inquiries.first_responded_at` 记录工作人员**首次响应**的时间（发布报价、要求补件或拒绝
时写入一次，之后不再覆盖）。默认 NULL，既有询价保持 NULL（视为尚未响应）。

新增列，不改任何既有数据；downgrade 删列。
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "first_responded_at" not in columns:
        op.add_column("inquiries", sa.Column("first_responded_at", sa.BigInteger()))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("inquiries")}
    if "first_responded_at" in columns:
        op.drop_column("inquiries", "first_responded_at")
