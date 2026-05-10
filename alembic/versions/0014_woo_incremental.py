"""Fase 6: tabla orders + columna search_tsv en products.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_config_id", UUID(as_uuid=True),
            sa.ForeignKey("connector_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column(
            "contact_id", UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text, nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.Text, nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_tenant_contact", "orders", ["tenant_id", "contact_id"])
    op.create_unique_constraint(
        "uq_orders_tenant_config_external",
        "orders",
        ["tenant_id", "connector_config_id", "external_id"],
    )

    # Columna tsvector generada para búsqueda BM25 sobre productos.
    # No se usa GENERATED ALWAYS AS dentro de Alembic porque la sintaxis varía;
    # se ejecuta DDL directo igual que en 0009_knowledge.py.
    op.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS search_tsv tsvector "
        "GENERATED ALWAYS AS ("
        "  to_tsvector('spanish',"
        "    coalesce(name,'')||' '||"
        "    coalesce(description_short,'')||' '||"
        "    coalesce(description_long,'')"
        "  )"
        ") STORED"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_search_tsv "
        "ON products USING gin(search_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_search_tsv")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS search_tsv")
    op.drop_table("orders")
