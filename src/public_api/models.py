"""Modelos SQLAlchemy para la API pública y webhooks salientes (Fase 10).

Tablas:
- ``api_keys``          — claves de API por tenant con scopes y SHA-256 hash.
- ``webhooks_out``      — endpoints destino configurados por tenant.
- ``webhook_deliveries`` — entregas individuales con estado y reintentos.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    """Clave de API por tenant.

    El token crudo solo se muestra en el momento de creación (no se persiste).
    Se almacena únicamente el SHA-256 del token y los primeros 8 caracteres
    (``prefix``) para identificación visual.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # SHA-256 hex del token crudo; el token solo se muestra al crear.
    hashed_key: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    # Primeros 8 chars del token para identificación visual: "ck_a1b2c3d4..."
    prefix: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class WebhookOut(Base, TimestampMixin):
    """Endpoint destino configurado por un tenant para recibir eventos salientes."""

    __tablename__ = "webhooks_out"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Secreto para firmar el payload con HMAC-SHA256 (X-ChatPro-Signature).
    secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Eventos suscritos: 'message.received' | 'message.sent' | 'conversation.escalated' | ...
    events: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="true"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )

    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan"
    )


class WebhookDelivery(Base):
    """Entrega individual de un evento a un webhook_out.

    El ciclo de vida es: pending → (success | failed → retry → ... → dead).
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("webhooks_out.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 'pending' | 'success' | 'failed' | 'dead'
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    last_response_status: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    last_response_body: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    webhook: Mapped["WebhookOut"] = relationship(
        "WebhookOut", back_populates="deliveries"
    )

    __table_args__ = (
        sa.Index(
            "ix_webhook_deliveries_status_next",
            "tenant_id",
            "status",
            "next_attempt_at",
        ),
    )
