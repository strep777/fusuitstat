"""合同电子签签署任务（C07：合同全部电子签；C19：未成年委托需要监护人签署）。

`contract_envelopes` 一行一次签署任务：合同快照摘要、签署人（含监护人）、平台文档号、
签署状态与留证摘要。新增表，不影响既有数据；downgrade 直接删表。

设计与既有数据的关系：

- 已有的「勾选接受条款」`consents` 记录保持不变（它留证"同意了什么条款版本"），
  本表额外留证"谁在什么时间通过哪个平台签了哪一版合同"；
- 默认 `ESIGN_PROVIDER=disabled`：**不发送、不假装已签**，签署任务以 `PENDING_SEND`
  落库并记录失败原因，等配置真实渠道后由运营重发。
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_contract_envelopes_quote_status"

STATUSES = ("PENDING_SEND", "SENT", "PARTIALLY_SIGNED", "SIGNED", "DECLINED", "EXPIRED", "VOID")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 询价的监护人联系信息：合同签署需要监护人作为第二个签署人（C19）。
    inquiry_columns = {column["name"] for column in inspector.get_columns("inquiries")}
    for name, length in (("guardian_name", 60), ("guardian_email", 160), ("guardian_relation", 20)):
        if name not in inquiry_columns:
            op.add_column("inquiries", sa.Column(name, sa.String(length)))

    if "contract_envelopes" in inspector.get_table_names():
        return
    op.create_table(
        "contract_envelopes",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "quote_id",
            sa.String(48),
            sa.ForeignKey("quotes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inquiry_id", sa.String(48)),
        sa.Column("order_id", sa.String(48)),
        sa.Column("project_id", sa.String(48)),
        sa.Column(
            "user_id",
            sa.String(48),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(30), nullable=False, server_default="disabled"),
        sa.Column("provider_document_id", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING_SEND"),
        sa.Column("subject", sa.String(200), nullable=False, server_default=""),
        sa.Column("snapshot_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("signers", sa.JSON()),
        sa.Column("guardian_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sign_url", sa.String(500)),
        sa.Column("expires_at", sa.BigInteger()),
        sa.Column("sent_at", sa.BigInteger()),
        sa.Column("completed_at", sa.BigInteger()),
        sa.Column("decline_reason", sa.String(300)),
        sa.Column("void_reason", sa.String(300)),
        sa.Column("document_path", sa.String(300)),
        sa.Column("document_sha256", sa.String(64)),
        sa.Column("last_event_digest", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.String(300)),
        sa.Column("created_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in STATUSES) + ")",
            name="ck_contract_envelopes_status",
        ),
        sa.CheckConstraint("guardian_required IN (0,1)", name="ck_contract_envelopes_guardian"),
        sa.CheckConstraint("attempts >= 0", name="ck_contract_envelopes_attempts"),
    )
    op.create_index("ix_contract_envelopes_quote_id", "contract_envelopes", ["quote_id"])
    op.create_index("ix_contract_envelopes_user_id", "contract_envelopes", ["user_id"])
    op.create_index("ix_contract_envelopes_inquiry_id", "contract_envelopes", ["inquiry_id"])
    op.create_index("ix_contract_envelopes_order_id", "contract_envelopes", ["order_id"])
    op.create_index("ix_contract_envelopes_project_id", "contract_envelopes", ["project_id"])
    op.create_index(
        "ix_contract_envelopes_provider_document_id", "contract_envelopes", ["provider_document_id"]
    )
    op.create_index(INDEX_NAME, "contract_envelopes", ["quote_id", "status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    inquiry_columns = {column["name"] for column in inspector.get_columns("inquiries")}
    for name in ("guardian_name", "guardian_email", "guardian_relation"):
        if name in inquiry_columns:
            op.drop_column("inquiries", name)
    if "contract_envelopes" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("contract_envelopes")}
    for name in (*sorted(indexes),):
        op.drop_index(name, table_name="contract_envelopes")
    op.drop_table("contract_envelopes")
