"""Modelos del agente IA (Fase 2).

Tablas:
- ``personas``       — configuración de persona/locale/tono por tenant.
- ``usage_metrics``  — acumulado diario de tokens y costo por tenant.
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class Persona(Base, TimestampMixin):
    """Configuración de personalidad del bot para un tenant."""

    __tablename__ = "personas"

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
    system_prompt: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )
    # Hint de tono (incluido en system prompt): "formal" | "amigable" | "neutral"
    tone: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="amigable"
    )
    # Locale BCP-47: "es-CL", "es-MX", "en-US"
    locale: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="es-CL"
    )
    # Zona horaria IANA: "America/Santiago"
    timezone: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="America/Santiago"
    )
    # Mensaje cuando la consulta llega fuera de horario comercial.
    out_of_hours_message: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )
    # Horario comercial. Formato:
    # {"tz": "America/Santiago", "days": {"mon-fri": ["09:00", "18:00"]}}
    # Si vacío ({}) → siempre disponible.
    business_hours_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # Modelo Claude a usar. Default: claude-sonnet-4-6
    model_id: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="claude-sonnet-4-6"
    )


class ToolInvocation(Base, TimestampMixin):
    """Registro de cada invocación de tool por el agente (Fase 7).

    Sirve para debug, métricas y facturación. Se persiste antes de continuar
    el loop, así que un crash a mitad de turno deja rastro.
    """

    __tablename__ = "tool_invocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wa_conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Mensaje inbound que originó este turno (puede ser NULL en escenarios edge).
    wa_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tool_use_id: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    input: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    output: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # success | error | timeout | unknown_tool
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="success")
    error: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    latency_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")

    __table_args__ = (
        sa.Index("ix_tool_invocations_tenant_conv", "tenant_id", "wa_conversation_id"),
    )


class UsageMetric(Base):
    """Acumulado diario de uso LLM por tenant."""

    __tablename__ = "usage_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day: Mapped[date] = mapped_column(sa.Date, nullable=False)
    msgs_in: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default="0"
    )
    msgs_out: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default="0"
    )
    tokens_in: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default="0"
    )
    tokens_out: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default="0"
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        sa.Numeric(12, 6), nullable=False, server_default="0"
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "day", name="uq_usage_metrics_tenant_day"
        ),
    )
