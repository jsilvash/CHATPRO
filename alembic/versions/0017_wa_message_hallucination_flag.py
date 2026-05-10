"""Fase 22D: agregar hallucination_flag a wa_messages.

Permite persistir si el validador post-respuesta detectó un precio/dato
no grounded en los tool_results para ese mensaje de salida del agente.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wa_messages",
        sa.Column(
            "hallucination_flag",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("wa_messages", "hallucination_flag")
