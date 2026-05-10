"""Fase 23C: soporte multi-idioma en personas.

Agrega locale_secondary (ARRAY de texto) y auto_detect_locale (Boolean) a
la tabla personas para que el bot pueda detectar y responder en el idioma
del usuario.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column(
            "locale_secondary",
            ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        "personas",
        sa.Column(
            "auto_detect_locale",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("personas", "auto_detect_locale")
    op.drop_column("personas", "locale_secondary")
