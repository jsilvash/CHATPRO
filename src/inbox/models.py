"""Modelos del inbox humano (Fase 8).

Tabla:
- ``handoff_events`` — registra cada escalamiento a agente humano con motivo,
  agente asignado y timestamps de apertura/cierre.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class HandoffEvent(Base, TimestampMixin):
    """Un escalamiento de conversación desde el bot hacia un agente humano."""

    __tablename__ = "handoff_events"

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
    # Motivo del escalamiento (informado por el bot o por la API).
    motivo: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )
    # Usuario humano que tomó la conversación (null hasta que alguien ejecute /take).
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Momento en que la conversación entró en waiting_agent.
    opened_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    # Momento en que la conversación fue cerrada/devuelta al bot (null si sigue abierta).
    closed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.Index(
            "ix_handoff_events_tenant_conv",
            "tenant_id",
            "wa_conversation_id",
        ),
    )
