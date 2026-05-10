"""fase 25C: tabla canned_responses para templates de respuesta rápida

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canned_responses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shortcode", sa.String(64), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_canned_responses_tenant_shortcode",
        "canned_responses",
        ["tenant_id", "shortcode"],
    )
    op.create_unique_constraint(
        "uq_canned_responses_tenant_shortcode",
        "canned_responses",
        ["tenant_id", "shortcode"],
    )


def downgrade() -> None:
    op.drop_table("canned_responses")
