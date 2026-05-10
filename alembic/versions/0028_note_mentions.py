"""0028_note_mentions — campo mentions en conversation_notes (Fase 29C).

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_notes",
        sa.Column(
            "mentions",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_notes", "mentions")
