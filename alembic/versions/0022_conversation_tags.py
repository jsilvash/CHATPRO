"""fase 25B: tabla conversation_tags para etiquetas en conversaciones

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wa_conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversation_tags_tenant_conv",
        "conversation_tags",
        ["tenant_id", "wa_conversation_id"],
    )
    op.create_unique_constraint(
        "uq_conversation_tags_conv_tag",
        "conversation_tags",
        ["wa_conversation_id", "tag"],
    )


def downgrade() -> None:
    op.drop_table("conversation_tags")
