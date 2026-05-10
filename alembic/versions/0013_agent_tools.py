"""Fase 7: tablas wa_conversations, wa_messages (stubs) + tool_invocations.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wa_conversations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_wa_conversations_tenant", "wa_conversations", ["tenant_id"])

    op.create_table(
        "wa_messages",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_wa_messages_conv", "wa_messages", ["conversation_id"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("wa_messages.id"),
            nullable=True,
        ),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("input_json", JSONB, nullable=False),
        sa.Column("output_json", JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cost_cents", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_tool_invocations_tenant_conv",
        "tool_invocations",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_tool_invocations_tenant_tool",
        "tool_invocations",
        ["tenant_id", "tool_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("tool_invocations")
    op.drop_table("wa_messages")
    op.drop_table("wa_conversations")
