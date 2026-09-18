"""客户交易邮箱验证。"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 使用实时 metadata；全新库已有该表，旧库在此增量创建。
    if "customer_emails" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "customer_emails",
            sa.Column("user_id", sa.String(48), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("email_ciphertext", sa.Text),
            sa.Column("verified_at", sa.BigInteger),
            sa.Column("pending_ciphertext", sa.Text),
            sa.Column("challenge_hash", sa.String(64)),
            sa.Column("expires_at", sa.BigInteger),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("sent_at", sa.BigInteger),
        )


def downgrade() -> None:
    op.drop_table("customer_emails")
