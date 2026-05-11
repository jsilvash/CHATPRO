"""0026_office_hours — tabla de horarios de atención del bot (Fase 29A).

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "office_hours",
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
            nullable=True,
        ),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("hour_start", sa.Integer(), nullable=False),
        sa.Column("hour_end", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "out_of_hours_message",
            sa.Text(),
            nullable=False,
            server_default="",
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
    op.create_index("ix_office_hours_tenant_id", "office_hours", ["tenant_id"])
    op.create_index("ix_office_hours_wa_number_id", "office_hours", ["wa_number_id"])
    op.create_index(
        "ix_office_hours_tenant_number",
        "office_hours",
        ["tenant_id", "wa_number_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_office_hours_tenant_number", table_name="office_hours")
    op.drop_index("ix_office_hours_wa_number_id", table_name="office_hours")
    op.drop_index("ix_office_hours_tenant_id", table_name="office_hours")
    op.drop_table("office_hours")
