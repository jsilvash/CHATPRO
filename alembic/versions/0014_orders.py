"""Fase 6: crear tabla orders para órdenes sincronizadas vía webhook WooCommerce.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_config_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("connector_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.Text, nullable=True),
        sa.Column("customer_email", sa.Text, nullable=True),
        sa.Column("raw", pg.JSONB, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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

    op.create_unique_constraint(
        "uq_orders_tenant_config_external",
        "orders",
        ["tenant_id", "connector_config_id", "external_id"],
    )
    op.create_index("ix_orders_tenant_config", "orders", ["tenant_id", "connector_config_id"])
    op.create_index(
        "ix_orders_tenant_id",
        "orders",
        ["tenant_id"],
    )
    op.create_index(
        "ix_orders_customer_email",
        "orders",
        ["tenant_id", "customer_email"],
        postgresql_where=sa.text("customer_email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("orders")
