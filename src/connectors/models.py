"""Modelos SQLAlchemy para conectores y catálogo de productos (Fase 5-6).

Tablas:
- ``connector_defs``     — definición de cada tipo de conector (woocommerce, shopify…).
- ``connector_configs``  — instancia de un conector configurada por un tenant.
- ``products``           — catálogo sincronizado desde el proveedor.
- ``product_embeddings`` — embeddings vectoriales de productos (Fase 6).
- ``orders``             — órdenes/pedidos sincronizados (Fase 6).
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class ConnectorDef(Base):
    """Definición global de un tipo de conector (no multi-tenant).

    Registra qué conectores existen en el sistema y si están habilitados.
    Se puebla al arranque desde el registro de código (src/connectors/registry.py).
    """

    __tablename__ = "connector_defs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default="true")


class ConnectorConfig(Base, TimestampMixin):
    """Instancia de un conector configurada por un tenant.

    ``encrypted_credentials`` contiene el blob AES-GCM generado por
    ``src/connectors/crypto.encrypt_credentials``.
    """

    __tablename__ = "connector_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_def_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("connector_defs.id"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    encrypted_credentials: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    webhook_secret: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    # 'pending' | 'connected' | 'error' | 'disabled'
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending")
    last_full_sync_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_incremental_sync_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    __table_args__ = (
        sa.Index("ix_connector_configs_tenant", "tenant_id"),
        sa.Index("ix_connector_configs_tenant_def", "tenant_id", "connector_def_id"),
    )


class Product(Base, TimestampMixin):
    """Producto sincronizado desde el proveedor e-commerce del tenant.

    ``external_id`` es el ID en el sistema del proveedor (Woo product ID, etc.).
    ``deleted_at`` implementa soft-delete: los productos borrados en el proveedor
    se marcan aquí en lugar de eliminarse, para mantener coherencia con órdenes
    ya referenciadas.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description_short: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    description_long: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    price_regular: Mapped[float | None] = mapped_column(sa.Numeric(12, 2), nullable=True)
    price_sale: Mapped[float | None] = mapped_column(sa.Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    stock_quantity: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # 'in_stock' | 'out_of_stock' | 'on_backorder'
    stock_status: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    images: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    categories: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    variations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deleted_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "connector_config_id", "external_id",
            name="uq_products_tenant_config_external",
        ),
        sa.Index("ix_products_tenant_sku", "tenant_id", "sku",
                 postgresql_where=sa.text("sku IS NOT NULL")),
        sa.Index("ix_products_tenant_config", "tenant_id", "connector_config_id"),
    )


class ProductEmbedding(Base):
    """Embedding vectorial de un producto para búsqueda semántica (pgvector).

    El texto embebido es una concatenación de nombre, descripción y atributos.
    Se regenera cuando el producto cambia via webhook o sync_incremental.
    """

    __tablename__ = "product_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list] = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.Index("ix_product_embeddings_tenant_product", "tenant_id", "product_id"),
    )


class Order(Base, TimestampMixin):
    """Orden/pedido sincronizado desde el proveedor e-commerce del tenant.

    Se puebla via webhooks (order.created / order.updated) y puede usarse para
    resolver historial de compras de un contacto.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # 'pending'|'processing'|'completed'|'cancelled'|etc.
    status: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    total: Mapped[float | None] = mapped_column(sa.Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "connector_config_id", "external_id",
            name="uq_orders_tenant_config_external",
        ),
        sa.Index("ix_orders_tenant_contact", "tenant_id", "contact_id"),
    )
