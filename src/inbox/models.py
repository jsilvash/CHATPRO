"""Modelos del inbox humano.

Tablas:
- ``handoff_events``             — escalamientos a agente humano (Fase 8).
- ``conversation_tags``          — etiquetas por conversación (Fase 25B).
- ``canned_responses``           — templates de respuesta rápida (Fase 25C).
- ``conversation_notes``         — notas internas por conversación (Fase 26B).
- ``conversation_status_history``— historial de cambios de status (Fase 26C).
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class ConversationTag(Base):
    """Etiqueta aplicada a una conversación (Fase 25B)."""

    __tablename__ = "conversation_tags"

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
    tag: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index(
            "ix_conversation_tags_tenant_conv",
            "tenant_id",
            "wa_conversation_id",
        ),
        sa.UniqueConstraint(
            "wa_conversation_id",
            "tag",
            name="uq_conversation_tags_conv_tag",
        ),
    )


class CannedResponse(Base):
    """Template de respuesta rápida reutilizable por el equipo del tenant (Fase 25C)."""

    __tablename__ = "canned_responses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shortcode: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index(
            "ix_canned_responses_tenant_shortcode",
            "tenant_id",
            "shortcode",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "shortcode",
            name="uq_canned_responses_tenant_shortcode",
        ),
    )


class ConversationNote(Base):
    """Nota interna en una conversación, solo visible para el equipo (Fase 26B)."""

    __tablename__ = "conversation_notes"

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
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # IDs de usuarios mencionados con @email/@nombre (Fase 29C). Lista de UUIDs en JSONB.
    mentions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index(
            "ix_conversation_notes_tenant_conv",
            "tenant_id",
            "wa_conversation_id",
        ),
    )


class ConversationStatusHistory(Base):
    """Registro de cada cambio de status de una conversación (Fase 26C)."""

    __tablename__ = "conversation_status_history"

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
    )
    old_status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    new_status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index(
            "ix_conv_status_history_tenant_conv",
            "tenant_id",
            "wa_conversation_id",
        ),
    )
