"""fase 3: ai_summary + turn_count en wa_conversations

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wa_conversations",
        sa.Column("ai_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "wa_conversations",
        sa.Column(
            "turn_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("wa_conversations", "turn_count")
    op.drop_column("wa_conversations", "ai_summary")
