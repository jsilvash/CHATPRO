"""Fase 22A: agregar contact_id a wa_conversations.

Permite referenciar el Contact persistido desde la conversación.
Necesario para tool_runner.collect_tools_for_conversation que accede
a conversation.contact_id para pasarlo como extra_kwarg a los conectores.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wa_conversations",
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_wa_conversations_contact_id",
        "wa_conversations",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_wa_conversations_contact_id",
        "wa_conversations",
        ["contact_id"],
        postgresql_where=sa.text("contact_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_wa_conversations_contact_id", table_name="wa_conversations")
    op.drop_constraint(
        "fk_wa_conversations_contact_id", "wa_conversations", type_="foreignkey"
    )
    op.drop_column("wa_conversations", "contact_id")
