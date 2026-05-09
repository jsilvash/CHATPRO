"""Modelos WhatsApp (Fase 1).

Tablas:
- ``wa_numbers``        — un número conectado por tenant (multi-número soportado).
- ``wa_sessions``       — estado WAHA de la sesión (status + QR transitorio).
- ``wa_conversations``  — chat entre un contacto y uno de los números del tenant.
- ``wa_messages``       — mensajes individuales (in/out) con ACK y payload crudo.

Diferencias con el legacy de FitnessIA (decisiones del Hub):
- No existe ``connection_type`` — solo WAHA en el Hub, no hay discriminador.
- El campo se llama ``waha_session_name`` (antes ``evolution_instance_name``).
- ``waha_node_id`` listo para sharding (§4 plan); default ``"default"``.
- ``purpose`` enum eliminado: el tenant define ``tags TEXT[]`` libres.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class WaNumber(Base, TimestampMixin):
    """Número de WhatsApp gestionado por un tenant."""

    __tablename__ = "wa_numbers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Nombre de sesión WAHA (único global — WAHA no namespacea por tenant).
    waha_session_name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    # Nodo WAHA donde corre la sesión (sharding §4 plan). Default: "default".
    waha_node_id: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="default"
    )
    # Teléfono real del número, conocido tras conectar (puede ser "" mientras no haya QR escaneado).
    phone: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    # Tags libres para que el tenant rutee envíos según propósito (lifecycle, support, ventas...).
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default="{}"
    )
    is_default: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="false"
    )
    active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default="true"
    )


class WaSession(Base, TimestampMixin):
    """Estado de la sesión WAHA para un ``wa_number``.

    Hay 1:1 entre ``WaNumber`` y ``WaSession``. Se separa para que ``WaNumber``
    sea estable (sin churn en cada cambio de status) y este modelo absorba el
    estado transitorio (QR base64, pairing code, último status).

    Status posibles emitidos por WAHA: ``STARTING``, ``SCAN_QR_CODE``,
    ``WORKING``, ``FAILED``, ``STOPPED``.
    """

    __tablename__ = "wa_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wa_number_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="STARTING"
    )
    last_status_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # QR base64 transitorio: se setea cuando WAHA emite SCAN_QR_CODE; se limpia al WORKING.
    qr_data_b64: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    pairing_code: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )


class WaConversation(Base, TimestampMixin):
    """Conversación entre un contacto externo y uno de los números del tenant."""

    __tablename__ = "wa_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wa_number_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Phone del contacto en formato puro (solo dígitos). Puede tener un LID si todavía
    # no se resolvió al PN — el rehidratador WA-LID lo actualiza al primer envío.
    wa_contact_phone: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    wa_contact_name: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=""
    )
    # bot | waiting_agent | agent | closed (ciclo se ampliará en Fase 8).
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="bot")
    last_message_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "wa_number_id", "wa_contact_phone", name="uq_wa_conversations_number_phone"
        ),
    )


class WaMessage(Base, TimestampMixin):
    """Mensaje individual entre el contacto y el número del tenant.

    El campo ``ack`` sigue el invariante monotónico definido en
    ``src.messaging.ack``: ``sent < delivered < read``; ``failed`` terminal.
    """

    __tablename__ = "wa_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wa_number_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_numbers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wa_conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("wa_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ``in`` (entrante del contacto) | ``out`` (saliente del bot/agente).
    direction: Mapped[str] = mapped_column(sa.Text, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    media_url: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    mediatype: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    # ID que devuelve WAHA en el envío / que llega en el webhook (Baileys-style).
    wa_message_id: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default="", index=True
    )
    # ACK: sent | delivered | read | failed. ``""`` = aún no enviado (estado inicial outbound).
    ack: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    error: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    # Payload crudo del webhook (debug / replay).
    raw_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

