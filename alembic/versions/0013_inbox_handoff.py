"""fase 8: assigned_user_id en wa_conversations

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wa_conversations",
        sa.Column("assigned_user_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_wa_conversations_assigned_user",
        "wa_conversations",
        "users",
        ["assigned_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_wa_conversations_tenant_assigned",
        "wa_conversations",
        ["tenant_id", "assigned_user_id"],
        postgresql_where=sa.text("assigned_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wa_conversations_tenant_assigned", table_name="wa_conversations"
    )
    op.drop_constraint(
        "fk_wa_conversations_assigned_user",
        "wa_conversations",
        type_="foreignkey",
    )
    op.drop_column("wa_conversations", "assigned_user_id")
