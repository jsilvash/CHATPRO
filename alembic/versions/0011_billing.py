"""fase 11: tablas usage_metrics (nueva PK compuesta), quotas y audit_log

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Borrar la tabla usage_metrics anterior (Fase 2, PK UUID simple)
    op.drop_table("usage_metrics")

    # ── usage_metrics (PK compuesta tenant_id + metric_date) ─────────────────
    op.create_table(
        "usage_metrics",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("messages_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversations_active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("llm_output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("llm_cost_cents", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("storage_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("api_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "metric_date"),
    )

    # ── quotas ────────────────────────────────────────────────────────────────
    op.create_table(
        "quotas",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("max_messages_per_month", sa.Integer(), nullable=True),
        sa.Column("max_conversations_active", sa.Integer(), nullable=True),
        sa.Column("max_llm_cost_cents_per_month", sa.Integer(), nullable=True),
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column("max_api_requests_per_day", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_api_key_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("diff", JSONB(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_tenant_created",
        "audit_log",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_audit_log_tenant_target",
        "audit_log",
        ["tenant_id", "target_type", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_tenant_target", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("quotas")
    op.drop_table("usage_metrics")
    # Restaurar la tabla original (Fase 2) — sin datos
    op.create_table(
        "usage_metrics",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("msgs_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("msgs_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "day", name="uq_usage_metrics_tenant_day"),
    )
    op.create_index(
        "ix_usage_metrics_tenant_id", "usage_metrics", ["tenant_id"]
    )
