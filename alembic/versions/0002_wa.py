"""fase 1: tablas WhatsApp (wa_numbers, wa_sessions, wa_conversations, wa_messages)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── wa_numbers ───────────────────────────────────────────
    op.create_table(
        "wa_numbers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("waha_session_name", sa.Text(), nullable=False),
        sa.Column(
            "waha_node_id",
            sa.Text(),
            nullable=False,
            server_default="default",
        ),
        sa.Column("phone", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tags",
            ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_wa_numbers_tenant_id", "wa_numbers", ["tenant_id"])
    op.create_unique_constraint(
        "uq_wa_numbers_session_name", "wa_numbers", ["waha_session_name"]
    )

    # ─── wa_sessions ──────────────────────────────────────────
    op.create_table(
        "wa_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_number_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="STARTING"
        ),
        sa.Column(
            "last_status_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("qr_data_b64", sa.Text(), nullable=False, server_default=""),
        sa.Column("pairing_code", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_wa_sessions_tenant_id", "wa_sessions", ["tenant_id"])

    # ─── wa_conversations ─────────────────────────────────────
    op.create_table(
        "wa_conversations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_number_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wa_contact_phone", sa.Text(), nullable=False),
        sa.Column(
            "wa_contact_name", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="bot"),
        sa.Column(
            "last_message_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_wa_conversations_tenant_id", "wa_conversations", ["tenant_id"]
    )
    op.create_index(
        "ix_wa_conversations_wa_number_id", "wa_conversations", ["wa_number_id"]
    )
    op.create_index(
        "ix_wa_conversations_wa_contact_phone",
        "wa_conversations",
        ["wa_contact_phone"],
    )
    op.create_unique_constraint(
        "uq_wa_conversations_number_phone",
        "wa_conversations",
        ["wa_number_id", "wa_contact_phone"],
    )

    # ─── wa_messages ──────────────────────────────────────────
    op.create_table(
        "wa_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_number_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("mediatype", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "wa_message_id", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("ack", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "raw_payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_wa_messages_tenant_id", "wa_messages", ["tenant_id"])
    op.create_index(
        "ix_wa_messages_wa_number_id", "wa_messages", ["wa_number_id"]
    )
    op.create_index(
        "ix_wa_messages_wa_conversation_id",
        "wa_messages",
        ["wa_conversation_id"],
    )
    op.create_index(
        "ix_wa_messages_wa_message_id", "wa_messages", ["wa_message_id"]
    )


def downgrade() -> None:
    op.drop_table("wa_messages")
    op.drop_table("wa_conversations")
    op.drop_table("wa_sessions")
    op.drop_table("wa_numbers")
