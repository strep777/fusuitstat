"""内容作品的客户授权归属（C18：公开展示授权单独可撤回）。

`content_entries.client_user_id` 记录「这份作品的公开展示授权属于哪个客户账号」，
客户的「我的公开展示授权」据此列出可撤回项。未登记的旧作品保持 NULL：
展示逻辑不受影响（仍由 `display_authorized` 决定），只是客户侧看不到撤回入口，
需要运营在后台补登记。
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("content_entries")}
    if "client_user_id" not in columns:
        op.add_column("content_entries", sa.Column("client_user_id", sa.String(48)))
    indexes = {index["name"] for index in inspector.get_indexes("content_entries")}
    if "ix_content_entries_client_user_id" not in indexes:
        op.create_index(
            "ix_content_entries_client_user_id", "content_entries", ["client_user_id"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("content_entries")}
    if "ix_content_entries_client_user_id" in indexes:
        op.drop_index("ix_content_entries_client_user_id", table_name="content_entries")
    columns = {column["name"] for column in inspector.get_columns("content_entries")}
    if "client_user_id" in columns:
        op.drop_column("content_entries", "client_user_id")
