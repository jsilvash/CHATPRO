"""fase 24C: body_tsv en wa_messages para búsqueda full-text

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE wa_messages
        ADD COLUMN IF NOT EXISTS body_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(text, ''))) STORED
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wa_messages_body_tsv ON wa_messages USING GIN (body_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wa_messages_body_tsv")
    op.execute("ALTER TABLE wa_messages DROP COLUMN IF EXISTS body_tsv")
