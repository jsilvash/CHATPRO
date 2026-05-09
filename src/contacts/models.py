"""Modelos de contactos y hechos persistentes (Fase 4).

Tablas:
- ``contacts``      — datos base del contacto (teléfono, nombre, email, etc.).
- ``contact_facts`` — hechos clave/valor extraídos o ingresados manualmente.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class Contact(Base, TimestampMixin):
    """Contacto externo normalizado por tenant + teléfono."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Teléfono normalizado (solo dígitos, sin +).
    phone_e164: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    email: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    locale: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    opt_in_marketing: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="false"
    )

    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_contacts_tenant_phone"),
        sa.Index("ix_contacts_tenant_email", "tenant_id", "email", postgresql_where=sa.text("email IS NOT NULL")),
    )


class ContactFact(Base, TimestampMixin):
    """Hecho persistente sobre un contacto.

    Un hecho es un par clave/valor tipado extraído automáticamente por Haiku
    o ingresado manualmente desde el inbox.  El campo ``confidence`` indica
    la certeza de la extracción automática (0.00-1.00); es NULL si ``source``
    es ``'manual'``.

    Invariante: UNIQUE(tenant_id, contact_id, key) — un hecho por clave.
    Si se extrae un nuevo valor con mayor confidence, reemplaza al anterior.
    """

    __tablename__ = "contact_facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 'text' | 'number' | 'date' | 'bool' | 'json'
    value_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="text"
    )
    # 'manual' | 'extracted' | 'imported' | 'connector'
    source: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="extracted"
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(3, 2), nullable=True
    )
    # Mensaje del que se extrajo el hecho (solo para source='extracted').
    extracted_from_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "contact_id", "key", name="uq_contact_facts_tenant_contact_key"),
        sa.Index("ix_contact_facts_tenant_contact", "tenant_id", "contact_id"),
    )
