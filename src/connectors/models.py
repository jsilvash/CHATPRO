import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False)
    connector_id = Column(PGUUID(as_uuid=True), nullable=False)
    display_name = Column(Text, nullable=False)
    encrypted_credentials = Column(BYTEA, nullable=False)
    webhook_secret = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")
    last_full_sync_at = Column(TIMESTAMP(timezone=True))
    last_incremental_sync_at = Column(TIMESTAMP(timezone=True))
    last_error = Column(Text)
    config = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_connector_configs_tenant", "tenant_id"),
        Index("ix_connector_configs_tenant_connector", "tenant_id", "connector_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False)
    connector_config_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id = Column(Text, nullable=False)
    sku = Column(Text)
    name = Column(Text, nullable=False)
    description_short = Column(Text)
    description_long = Column(Text)
    price_regular = Column(Numeric(12, 2))
    price_sale = Column(Numeric(12, 2))
    currency = Column(Text)
    stock_quantity = Column(Integer)
    stock_status = Column(Text)
    url = Column(Text)
    images = Column(JSONB)
    categories = Column(JSONB)
    attributes = Column(JSONB)
    variations = Column(JSONB)
    raw = Column(JSONB)
    # Fase 6: columna de embedding via pgvector (migración 0012)
    embedding = Column(Vector(1024), nullable=True)
    deleted_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_config_id", "external_id"),
        Index(
            "ix_products_tenant_sku",
            "tenant_id",
            "sku",
            postgresql_where="sku IS NOT NULL",
        ),
        Index("ix_products_tenant_config", "tenant_id", "connector_config_id"),
    )
