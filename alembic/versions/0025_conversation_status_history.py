"""fase 26C: tabla conversation_status_history para historial de cambios de status

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_status_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wa_conversation_id", UUID(as_uuid=True), sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_status", sa.Text, nullable=False),
        sa.Column("new_status", sa.Text, nullable=False),
        sa.Column("changed_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conv_status_history_tenant_id", "conversation_status_history", ["tenant_id"])
    op.create_index("ix_conv_status_history_tenant_conv", "conversation_status_history", ["tenant_id", "wa_conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_conv_status_history_tenant_conv", table_name="conversation_status_history")
    op.drop_index("ix_conv_status_history_tenant_id", table_name="conversation_status_history")
    op.drop_table("conversation_status_history")
