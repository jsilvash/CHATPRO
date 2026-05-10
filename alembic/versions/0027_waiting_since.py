"""0027_waiting_since — columna waiting_since en wa_conversations (Fase 29B).

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wa_conversations",
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wa_conversations", "waiting_since")
