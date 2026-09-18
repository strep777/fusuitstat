"""注销后的清理执行记录（C22 / 10 文档「隐私和保留草案」）。

`closure_executions` 一行一个账号：记录「哪些可删除资料已清理、哪些交易记录依法隔离保留」
以及执行时间。唯一索引保证清理幂等——重复扫描同一个注销账号不会重复删除或重复计数。

新增表，不影响既有数据；downgrade 直接删表。
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "closure_executions" in inspector.get_table_names():
        return
    op.create_table(
        "closure_executions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("executed_at", sa.BigInteger(), nullable=False),
        sa.Column("purged", sa.JSON()),
        sa.Column("retained", sa.JSON()),
        sa.Column("note", sa.String(200)),
    )
    op.create_index(
        "ix_closure_executions_user_id", "closure_executions", ["user_id"], unique=True
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "closure_executions" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("closure_executions")}
    if "ix_closure_executions_user_id" in indexes:
        op.drop_index("ix_closure_executions_user_id", table_name="closure_executions")
    op.drop_table("closure_executions")
