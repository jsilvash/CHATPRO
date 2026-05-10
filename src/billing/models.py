"""Modelos SQLAlchemy para billing, métricas y auditoría (Fase 11)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class UsageMetric(Base):
    """Acumulado diario de métricas de uso por tenant.

    PK compuesta: (tenant_id, metric_date).
    """

    __tablename__ = "usage_metrics"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric_date: Mapped[date] = mapped_column(sa.Date, primary_key=True)

    messages_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    messages_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    conversations_active: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    llm_input_tokens: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    llm_output_tokens: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    llm_cost_cents: Mapped[float] = mapped_column(
        sa.Numeric(12, 4), nullable=False, server_default="0"
    )
    storage_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default="0")
    api_requests: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")


class Quota(Base):
    """Cuotas operativas por tenant. Todos los caps son nullable (None = sin límite)."""

    __tablename__ = "quotas"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_messages_per_month: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_conversations_active: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_llm_cost_cents_per_month: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_storage_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    max_api_requests_per_day: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )


class AuditLog(Base):
    """Registro inmutable de mutaciones del sistema para auditoría."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    __table_args__ = (
        sa.Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_audit_log_tenant_target", "tenant_id", "target_type", "target_id"),
    )


class ExportJob(Base):
    """Job de exportación de datos del tenant (GDPR — Fase 23B).

    Ciclo: queued → running → done | error.
    storage_uri apunta al ZIP en S3/MinIO cuando el job termina con éxito.
    """

    __tablename__ = "export_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # queued | running | done | error
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="queued"
    )
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.Index("ix_export_jobs_tenant", "tenant_id"),
    )
