"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """应用迁移。SQLite 的列变更必须用 batch_alter_table（见 env.py）。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回退迁移。破坏性迁移前必须先做恢复演练（14 文档）。"""
    ${downgrades if downgrades else "pass"}
