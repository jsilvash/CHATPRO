"""fase 8: tabla handoff_events

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "handoff_events",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wa_conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["wa_conversation_id"], ["wa_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_handoff_events_tenant_id", "handoff_events", ["tenant_id"]
    )
    op.create_index(
        "ix_handoff_events_wa_conversation_id",
        "handoff_events",
        ["wa_conversation_id"],
    )
    op.create_index(
        "ix_handoff_events_agent_user_id",
        "handoff_events",
        ["agent_user_id"],
    )
    op.create_index(
        "ix_handoff_events_tenant_conv",
        "handoff_events",
        ["tenant_id", "wa_conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_handoff_events_tenant_conv", table_name="handoff_events")
    op.drop_index("ix_handoff_events_agent_user_id", table_name="handoff_events")
    op.drop_index(
        "ix_handoff_events_wa_conversation_id", table_name="handoff_events"
    )
    op.drop_index("ix_handoff_events_tenant_id", table_name="handoff_events")
    op.drop_table("handoff_events")
