"""公开参考估价请求（FR-026）。

无需登录的估价请求独立于询价表：不关联用户、不进入报价状态机，只保存
联系方式密文、配置选择与参考区间，供后台回访。联系方式不落明文。
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "estimate_requests" in inspector.get_table_names():
        return
    op.create_table(
        "estimate_requests",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("contact_type", sa.String(16), nullable=False),
        sa.Column("contact_ciphertext", sa.Text(), nullable=False),
        sa.Column("contact_hint", sa.String(80), nullable=False, server_default=""),
        sa.Column("series_code", sa.String(40), nullable=False),
        sa.Column("series_title", sa.String(80), nullable=False, server_default=""),
        sa.Column("style", sa.String(16), nullable=False, server_default="standard"),
        sa.Column("options", sa.JSON()),
        sa.Column("character_name", sa.String(60), nullable=False, server_default=""),
        sa.Column("species", sa.String(60), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimate_low_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimate_high_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(16), nullable=False, server_default="NEW"),
        sa.Column("consent_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source_ip_hash", sa.String(64)),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('NEW','CONTACTED','CLOSED')", name="ck_estimate_requests_status"
        ),
        sa.CheckConstraint(
            "contact_type IN ('email','phone','wechat')",
            name="ck_estimate_requests_contact_type",
        ),
        sa.CheckConstraint(
            "style IN ('standard','fitted','special')", name="ck_estimate_requests_style"
        ),
        sa.CheckConstraint(
            "estimate_low_cents >= 0 AND estimate_high_cents >= estimate_low_cents",
            name="ck_estimate_requests_range",
        ),
    )
    op.create_index(
        "ix_estimate_requests_status_created",
        "estimate_requests",
        ["status", "created_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "estimate_requests" not in inspector.get_table_names():
        return
    op.drop_index("ix_estimate_requests_status_created", table_name="estimate_requests")
    op.drop_table("estimate_requests")
