"""Modelo OfficeHours — franjas horarias en que el bot atiende (Fase 29A)."""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class OfficeHours(Base, TimestampMixin):
    """Franja horaria en que el bot atiende para un número o tenant.

    Un registro define un intervalo [hour_start, hour_end) para un día de la semana.
    Si wa_number_id es None → aplica a todos los números del tenant.
    Si hay registros específicos para el número, éstos tienen prioridad sobre los globales.
    Si no hay registros activos → sin restricción (bot siempre atiende).
    """

    __tablename__ = "office_hours"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # None → aplica a todos los números del tenant.
    wa_number_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 0=lunes, 1=martes, ..., 6=domingo (mismo criterio que Python weekday())
    day_of_week: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # Hora de inicio (0-23) — inclusive
    hour_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # Hora de fin (0-23) — exclusive (ej: 18 = hasta las 18:59)
    hour_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="true"
    )
    # Mensaje a enviar cuando se recibe un mensaje fuera de horario. "" = silencio.
    out_of_hours_message: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )

    __table_args__ = (
        sa.Index("ix_office_hours_tenant_number", "tenant_id", "wa_number_id"),
    )
