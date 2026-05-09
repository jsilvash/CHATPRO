"""fase 5: tablas connector_defs + connector_configs + products

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── connector_defs ────────────────────────────────────────────────────────
    op.create_table(
        "connector_defs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_connector_defs_name"),
    )

    # ── connector_configs ─────────────────────────────────────────────────────
    op.create_table(
        "connector_configs",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("connector_def_id", UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_incremental_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connector_def_id"], ["connector_defs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_configs_tenant", "connector_configs", ["tenant_id"])
    op.create_index(
        "ix_connector_configs_tenant_def",
        "connector_configs",
        ["tenant_id", "connector_def_id"],
    )

    # ── products ──────────────────────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("connector_config_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description_short", sa.Text(), nullable=True),
        sa.Column("description_long", sa.Text(), nullable=True),
        sa.Column("price_regular", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_sale", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("stock_status", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("images", JSONB(), nullable=True),
        sa.Column("categories", JSONB(), nullable=True),
        sa.Column("attributes", JSONB(), nullable=True),
        sa.Column("variations", JSONB(), nullable=True),
        sa.Column("raw", JSONB(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connector_config_id"], ["connector_configs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "connector_config_id", "external_id",
            name="uq_products_tenant_config_external",
        ),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index(
        "ix_products_tenant_sku", "products", ["tenant_id", "sku"],
        postgresql_where=sa.text("sku IS NOT NULL"),
    )
    op.create_index(
        "ix_products_tenant_config", "products", ["tenant_id", "connector_config_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_products_tenant_config", table_name="products")
    op.drop_index("ix_products_tenant_sku", table_name="products")
    op.drop_index("ix_products_tenant_id", table_name="products")
    op.drop_table("products")

    op.drop_index("ix_connector_configs_tenant_def", table_name="connector_configs")
    op.drop_index("ix_connector_configs_tenant", table_name="connector_configs")
    op.drop_table("connector_configs")

    op.drop_table("connector_defs")
