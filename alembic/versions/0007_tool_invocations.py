"""fase 7: tabla tool_invocations

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wa_conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("wa_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_use_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("input", JSONB(), nullable=False, server_default="{}"),
        sa.Column("output", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["wa_conversation_id"], ["wa_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["wa_message_id"], ["wa_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_invocations_tenant_id", "tool_invocations", ["tenant_id"]
    )
    op.create_index(
        "ix_tool_invocations_wa_conversation_id",
        "tool_invocations",
        ["wa_conversation_id"],
    )
    op.create_index(
        "ix_tool_invocations_tenant_conv",
        "tool_invocations",
        ["tenant_id", "wa_conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_invocations_tenant_conv", table_name="tool_invocations"
    )
    op.drop_index(
        "ix_tool_invocations_wa_conversation_id", table_name="tool_invocations"
    )
    op.drop_index(
        "ix_tool_invocations_tenant_id", table_name="tool_invocations"
    )
    op.drop_table("tool_invocations")
