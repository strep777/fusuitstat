"""反馈首响时间（C30：反馈 7 个工作日首响，逾期提醒据此判定）。

`feedbacks.first_responded_at` 记录工作人员**首次响应**的时间：首次审核结论或首条用户可见
回复时写入一次，之后不再覆盖。默认 NULL，既有反馈保持 NULL（视为尚未响应）。

新增列 + 一个用于逾期巡检的索引，不改任何既有数据；downgrade 删索引与列。
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_feedbacks_first_response"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("feedbacks")}
    if "first_responded_at" not in columns:
        op.add_column("feedbacks", sa.Column("first_responded_at", sa.BigInteger()))
    indexes = {index["name"] for index in inspector.get_indexes("feedbacks")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "feedbacks", ["status", "first_responded_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("feedbacks")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="feedbacks")
    columns = {column["name"] for column in inspector.get_columns("feedbacks")}
    if "first_responded_at" in columns:
        op.drop_column("feedbacks", "first_responded_at")
