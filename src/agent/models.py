"""Modelos SQLAlchemy para Fase 7: agent tools + observabilidad."""

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from src.connectors.models import Base


class WaConversation(Base):
    """Stub mínimo — se amplía en Fase 8 (inbox + handoff)."""

    __tablename__ = "wa_conversations"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False)
    created_at = Column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (Index("ix_wa_conversations_tenant", "tenant_id"),)


class WaMessage(Base):
    """Stub mínimo — se amplía en Fase 8."""

    __tablename__ = "wa_messages"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False)
    conversation_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("wa_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (Index("ix_wa_messages_conv", "conversation_id"),)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False)
    conversation_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("wa_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("wa_messages.id"),
        nullable=True,
    )
    tool_name = Column(Text, nullable=False)
    input_json = Column(JSONB, nullable=False)
    output_json = Column(JSONB, nullable=True)
    status = Column(Text, nullable=False)  # 'ok'|'error'|'timeout'
    error = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    cost_cents = Column(Numeric(8, 4), nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        Index(
            "ix_tool_invocations_tenant_conv",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_tool_invocations_tenant_tool",
            "tenant_id",
            "tool_name",
            "created_at",
        ),
    )
