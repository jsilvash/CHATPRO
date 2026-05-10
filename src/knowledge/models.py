"""Modelos SQLAlchemy para la base de conocimiento RAG (Fase 9).

Tablas:
- ``kb_documents`` — documento fuente (PDF o URL) por tenant/número.
- ``kb_chunks``    — fragmento indexado con embedding pgvector 1024d.
"""

import uuid

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin


class KbDocument(Base, TimestampMixin):
    """Documento fuente ingestado en la KB de un tenant."""

    __tablename__ = "kb_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL → accesible para todos los números del tenant
    wa_number_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_numbers.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # 'pdf' | 'url'
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 'queued' | 'processing' | 'ready' | 'failed'
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="queued")
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    chunks: Mapped[list["KbChunk"]] = relationship(
        "KbChunk", back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.Index("ix_kb_documents_tenant_number", "tenant_id", "wa_number_id"),
    )


class KbChunk(Base):
    """Fragmento de texto indexado con embedding de 1024 dimensiones."""

    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    wa_number_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata_", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    document: Mapped[KbDocument] = relationship("KbDocument", back_populates="chunks")

    __table_args__ = (
        sa.Index("ix_kb_chunks_tenant_doc", "tenant_id", "document_id"),
    )
