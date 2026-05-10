"""Fase 20: agregar customer_phone y placed_at a tabla orders.

``customer_phone`` permite match por teléfono de facturación cuando el contacto
no tiene email registrado. ``placed_at`` refleja la fecha real del pedido en el
proveedor (date_created en WooCommerce, created_at en Shopify).

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("customer_phone", sa.Text, nullable=True))
    op.add_column(
        "orders",
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_orders_customer_phone",
        "orders",
        ["tenant_id", "customer_phone"],
        postgresql_where=sa.text("customer_phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders_customer_phone", table_name="orders")
    op.drop_column("orders", "placed_at")
    op.drop_column("orders", "customer_phone")
