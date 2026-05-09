"""fase 6: tablas product_embeddings (pgvector) + orders

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Habilita la extensión pgvector (idempotente — no falla si ya existe).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── product_embeddings ────────────────────────────────────────────────────
    op.create_table(
        "product_embeddings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_embeddings_tenant_product",
        "product_embeddings",
        ["tenant_id", "product_id"],
    )
    # Índice HNSW para búsqueda coseno eficiente
    op.execute(
        "CREATE INDEX ix_product_embeddings_hnsw ON product_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # ── orders ────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("connector_config_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", JSONB(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connector_config_id"], ["connector_configs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_config_id",
            "external_id",
            name="uq_orders_tenant_config_external",
        ),
    )
    op.create_index(
        "ix_orders_tenant_contact",
        "orders",
        ["tenant_id", "contact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_tenant_contact", table_name="orders")
    op.drop_table("orders")

    op.execute("DROP INDEX IF EXISTS ix_product_embeddings_hnsw")
    op.drop_index(
        "ix_product_embeddings_tenant_product", table_name="product_embeddings"
    )
    op.drop_table("product_embeddings")
