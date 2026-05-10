"""Dispatcher unificado de envíos WhatsApp (Hub: solo WAHA).

Punto único al que cualquier caller llama para enviar mensajes salientes.
Resuelve el ``WaNumber`` del tenant según ``tag`` (o default), llama al
cliente WAHA y devuelve un ``DispatchResult`` con el ``wa_message_id``
para que el caller persista en ``WaMessage``.

Diferencias con el dispatcher legacy de FitnessIA:
- Sin discriminador ``connection_type``: aquí solo WAHA.
- Sin ``purpose`` enum: routing por ``tag`` libre + ``is_default``.
- Sin ``_tenant_config_for_cloud_api``: Cloud API descartado en el Hub.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.messaging import waha_client
from src.messaging.lid_resolver import rehydrate_conversation_phone
from src.messaging.waha_client import WahaAPIError
from src.tenancy.context import bypass_tenant_filter
from src.utils.phone import normalize_phone
from src.wa.models import WaConversation, WaNumber

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Resultado de un envío vía dispatcher."""

    success: bool
    wa_message_id: str = ""
    wa_number_id: uuid.UUID | None = None
    error: str = ""
    error_status: int = 0  # 0 = sin error; -2 = LID no resuelto; etc.
    raw_response: dict = field(default_factory=dict)


class DispatcherError(Exception):
    """Error irrecuperable del dispatcher (ej. tenant sin WaNumber activo)."""


# ────────────────────────────────────────────────────────────
# Resolución del WaNumber a usar
# ────────────────────────────────────────────────────────────


def resolve_wa_number(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    tag: str | None = None,
) -> WaNumber | None:
    """Elige qué ``WaNumber`` usar para este envío.

    Cadena de fallback (primera que matchea gana):
    1. ``tag`` solicitado + ``is_default=True``.
    2. ``tag`` solicitado (cualquiera).
    3. ``is_default=True`` (sin filtrar tag).
    4. Primer activo.

    Devuelve ``None`` si el tenant no tiene ningún ``WaNumber`` activo.
    """
    with bypass_tenant_filter():
        numbers = (
            db.query(WaNumber)
            .filter(WaNumber.tenant_id == tenant_id, WaNumber.active.is_(True))
            .all()
        )

    if not numbers:
        return None

    if tag:
        for wn in numbers:
            if tag in (wn.tags or []) and wn.is_default:
                return wn
        for wn in numbers:
            if tag in (wn.tags or []):
                return wn

    for wn in numbers:
        if wn.is_default:
            return wn

    return numbers[0]


def _resolve_target_number(
    db: Session,
    tenant_id: uuid.UUID,
    wa_number: WaNumber | None,
    tag: str | None,
) -> WaNumber:
    """Devuelve el ``WaNumber`` explícito o lo resuelve; raise si no hay ninguno."""
    if wa_number is not None:
        return wa_number
    resolved = resolve_wa_number(db, tenant_id, tag=tag)
    if resolved is None:
        raise DispatcherError(
            f"tenant {tenant_id} no tiene WaNumber activo (tag={tag!r})"
        )
    return resolved


# ────────────────────────────────────────────────────────────
# Envíos
# ────────────────────────────────────────────────────────────


def send_text(
    db: Session,
    tenant_id: uuid.UUID,
    to_phone: str,
    text: str,
    *,
    wa_number: WaNumber | None = None,
    tag: str | None = None,
    conversation: WaConversation | None = None,
) -> DispatchResult:
    """Envía texto plano. Si ``conversation`` se pasa, intenta rehidratar LID→PN."""
    if not text:
        return DispatchResult(success=False, error="text vacío", error_status=-1)

    normalized = normalize_phone(to_phone)
    if not normalized:
        return DispatchResult(
            success=False, error=f"teléfono inválido: {to_phone!r}", error_status=-1
        )

    try:
        wn = _resolve_target_number(db, tenant_id, wa_number, tag)
    except DispatcherError as e:
        return DispatchResult(success=False, error=str(e), error_status=-1)

    if not wn.waha_session_name:
        return DispatchResult(
            success=False,
            wa_number_id=wn.id,
            error="WaNumber sin waha_session_name",
            error_status=-1,
        )

    # Rehidratación LID→PN si pasamos la conversación.
    if conversation is not None:
        phone_to_send = rehydrate_conversation_phone(db, conversation, wn)
    else:
        phone_to_send = normalized

    try:
        resp = waha_client.send_text(wn.waha_session_name, phone_to_send, text)
    except WahaAPIError as e:
        logger.warning(
            "dispatcher send_text falló: session=%s status=%d msg=%s",
            wn.waha_session_name, e.status, e.message,
        )
        return DispatchResult(
            success=False,
            wa_number_id=wn.id,
            error=f"[{e.status}] {e.message}",
            error_status=e.status,
        )

    return DispatchResult(
        success=True,
        wa_message_id=_extract_wa_message_id(resp),
        wa_number_id=wn.id,
        raw_response=resp if isinstance(resp, dict) else {},
    )


def _extract_wa_message_id(resp: dict | None) -> str:
    """Extrae el id Baileys-style del payload de respuesta de WAHA."""
    if not isinstance(resp, dict):
        return ""
    # WAHA devuelve {"id":{"_serialized":"true_<jid>_<hash>"}, ...} o {"_data": {"id": {"_serialized": "..."}}}
    for path in (("id", "_serialized"), ("_data", "id", "_serialized")):
        node: object = resp
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            return node
    rid = resp.get("id")
    if isinstance(rid, str):
        return rid
    return ""
