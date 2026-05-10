"""fase 26B: tabla conversation_notes para notas internas

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wa_conversation_id", UUID(as_uuid=True), sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversation_notes_tenant_id", "conversation_notes", ["tenant_id"])
    op.create_index("ix_conversation_notes_tenant_conv", "conversation_notes", ["tenant_id", "wa_conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_notes_tenant_conv", table_name="conversation_notes")
    op.drop_index("ix_conversation_notes_tenant_id", table_name="conversation_notes")
    op.drop_table("conversation_notes")
