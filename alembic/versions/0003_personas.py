"""fase 2: personas + usage_metrics + llm_metadata en wa_messages + persona_id en wa_numbers

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── personas ─────────────────────────────────────────────
    op.create_table(
        "personas",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("tone", sa.Text(), nullable=False, server_default="amigable"),
        sa.Column("locale", sa.Text(), nullable=False, server_default="es-CL"),
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default="America/Santiago",
        ),
        sa.Column(
            "out_of_hours_message", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "business_hours_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "model_id",
            sa.Text(),
            nullable=False,
            server_default="claude-sonnet-4-6",
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
    op.create_index("ix_personas_tenant_id", "personas", ["tenant_id"])

    # ─── usage_metrics ────────────────────────────────────────
    op.create_table(
        "usage_metrics",
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
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("msgs_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("msgs_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_usage_metrics_tenant_id", "usage_metrics", ["tenant_id"])
    op.create_unique_constraint(
        "uq_usage_metrics_tenant_day", "usage_metrics", ["tenant_id", "day"]
    )

    # ─── wa_messages: agregar llm_metadata ────────────────────
    op.add_column(
        "wa_messages",
        sa.Column(
            "llm_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ─── wa_numbers: agregar persona_id FK ────────────────────
    op.add_column(
        "wa_numbers",
        sa.Column("persona_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_wa_numbers_persona_id",
        "wa_numbers",
        "personas",
        ["persona_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_wa_numbers_persona_id", "wa_numbers", ["persona_id"])


def downgrade() -> None:
    op.drop_index("ix_wa_numbers_persona_id", table_name="wa_numbers")
    op.drop_constraint("fk_wa_numbers_persona_id", "wa_numbers", type_="foreignkey")
    op.drop_column("wa_numbers", "persona_id")
    op.drop_column("wa_messages", "llm_metadata")
    op.drop_table("usage_metrics")
    op.drop_table("personas")
