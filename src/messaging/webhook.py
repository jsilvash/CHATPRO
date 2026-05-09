"""Endpoint webhook WAHA: ``POST /webhook/waha/{wa_number_id}``.

WAHA envía 4 tipos de eventos a esta URL:
- ``message``      — mensaje entrante del contacto.
- ``message.any``  — incluye también los outbound (eco). Lo usamos solo para
                     idempotencia / reconciliación; no duplicamos persistencia.
- ``message.ack``  — actualización de ACK (sent/delivered/read/failed).
- ``session.status`` — cambio de status de la sesión (incluye QR, WORKING, FAILED).

Autenticación: header ``X-WAHA-Token`` debe matchear ``settings.waha_webhook_token``.
Si el token está vacío en settings (dev), se acepta cualquier request.

El path lleva el ``wa_number_id`` (UUID) de nuestra DB. Eso evita confusion
si dos tenants usan la misma session WAHA por error y permite cachear el
routing sin tocar el body. ``bypass_tenant_filter()`` se usa al lookup; tras
resolver el ``WaNumber`` entramos a ``tenant_scope()`` para todo lo demás.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.session import get_db
from src.messaging.ack import update_message_ack
from src.messaging.wa_lookup import get_wa_number_by_id
from src.tenancy.context import bypass_tenant_filter, tenant_scope
from src.wa.models import WaConversation, WaMessage, WaNumber, WaSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/waha", tags=["webhook"])


# ────────────────────────────────────────────────────────────
# Endpoint
# ────────────────────────────────────────────────────────────


@router.post("/{wa_number_id}", status_code=200)
async def receive_waha_event(
    wa_number_id: uuid.UUID,
    request: Request,
    x_waha_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Recibe un evento WAHA y lo despacha al handler correspondiente."""
    settings = get_settings()
    expected_token = (settings.waha_webhook_token or "").strip()
    if expected_token and (x_waha_token or "") != expected_token:
        logger.warning(
            "webhook waha %s: X-WAHA-Token inválido o ausente", wa_number_id
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload no es JSON object")

    event = (payload.get("event") or "").strip()
    if not event:
        raise HTTPException(status_code=400, detail="evento sin campo 'event'")

    wn = get_wa_number_by_id(db, wa_number_id)
    if wn is None:
        # No raise: WAHA reenvía si recibe error, y un 200 OK evita loops infinitos
        # cuando el wa_number fue eliminado del lado nuestro.
        logger.warning(
            "webhook waha %s: WaNumber no encontrado o inactivo (event=%s)",
            wa_number_id, event,
        )
        return {"ok": True, "skipped": "wa_number_not_found"}

    with tenant_scope(wn.tenant_id):
        if event in ("message", "message.any"):
            return _dispatch_message(db, wn, payload, is_any=event == "message.any")
        if event == "message.ack":
            return _dispatch_message_ack(db, wn, payload)
        if event == "session.status":
            return _dispatch_session_status(db, wn, payload)

    logger.info("webhook waha %s: evento ignorado %r", wa_number_id, event)
    return {"ok": True, "skipped": event}


# ────────────────────────────────────────────────────────────
# Handlers
# ────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _phone_from_jid(jid: str) -> str:
    """Devuelve solo los dígitos de un jid ``<digits>@c.us`` o ``<digits>@lid``."""
    if not jid:
        return ""
    return jid.split("@")[0].strip()


def _epoch_to_dt(ts: Any) -> datetime | None:
    """Convierte un timestamp WAHA (segundos) a ``datetime`` UTC; ``None`` si inválido."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError):
        return None


def _get_or_create_conversation(
    db: Session,
    wn: WaNumber,
    contact_phone: str,
    contact_name: str = "",
) -> WaConversation:
    """Devuelve la conversación existente o la crea (idempotente)."""
    conv = (
        db.query(WaConversation)
        .filter(
            WaConversation.wa_number_id == wn.id,
            WaConversation.wa_contact_phone == contact_phone,
        )
        .first()
    )
    if conv:
        return conv

    conv = WaConversation(
        tenant_id=wn.tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone=contact_phone,
        wa_contact_name=contact_name or "",
    )
    db.add(conv)
    db.flush()
    return conv


def _dispatch_message(
    db: Session,
    wn: WaNumber,
    event: dict,
    *,
    is_any: bool,
) -> dict:
    """Persiste un mensaje entrante (o saliente eco si ``is_any``).

    Idempotente por ``wa_message_id``: si ya existe, devuelve ok sin duplicar.
    """
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return {"ok": True, "skipped": "payload_not_dict"}

    wa_msg_id = str(payload.get("id") or "").strip()
    from_jid = str(payload.get("from") or "")
    sender_pn_jid = str(payload.get("senderPn") or "")
    from_me = bool(payload.get("fromMe"))
    body = str(payload.get("body") or "")
    media_url = str(payload.get("mediaUrl") or "")
    notify_name = str(payload.get("_data", {}).get("notifyName") or payload.get("notifyName") or "")
    timestamp = _epoch_to_dt(payload.get("timestamp"))

    # ``message.any`` cubre tanto inbound como echo del outbound; evitamos persistir
    # outbound desde el webhook (lo persiste el handler de envío vía API). Pero sí
    # actualizamos last_message_at de la conversación si existe.
    if from_me and is_any:
        return {"ok": True, "skipped": "echo_outbound"}

    # Idempotencia: si ya tenemos este wa_message_id, no duplicar.
    if wa_msg_id:
        existing = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_number_id == wn.id,
                WaMessage.wa_message_id == wa_msg_id,
            )
            .first()
        )
        if existing:
            return {"ok": True, "skipped": "duplicate", "wa_message_id": wa_msg_id}

    # Preferir senderPn (PN real) sobre `from` (puede ser @lid).
    contact_phone = _phone_from_jid(sender_pn_jid) or _phone_from_jid(from_jid)
    if not contact_phone:
        logger.warning(
            "webhook waha %s msg sin phone identificable (from=%r senderPn=%r)",
            wn.id, from_jid, sender_pn_jid,
        )
        return {"ok": True, "skipped": "no_contact_phone"}

    with bypass_tenant_filter():
        conv = _get_or_create_conversation(db, wn, contact_phone, notify_name)
        conv.last_message_at = timestamp or _utcnow()
        if not conv.wa_contact_name and notify_name:
            conv.wa_contact_name = notify_name

        msg = WaMessage(
            tenant_id=wn.tenant_id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text=body,
            media_url=media_url,
            mediatype=str(payload.get("type") or ""),
            wa_message_id=wa_msg_id,
            ack="",
            raw_payload=event,
        )
        db.add(msg)
        conv.turn_count = (conv.turn_count or 0) + 1
        db.add(conv)
        db.commit()
        db.refresh(msg)

    # Limpiar el typing del contacto ahora que envió el mensaje.
    from src.messaging.typing_state import clear_typing
    clear_typing(wn.waha_session_name, contact_phone)

    # Invocar al agente si el número tiene persona asignada.
    _maybe_respond_with_agent(db, wn, conv, msg)

    # Extraer hechos del contacto en múltiplos de 5 turnos (falla silenciosamente).
    _maybe_trigger_facts_extraction(db, conv)

    return {"ok": True, "wa_message_id": str(msg.id)}


_FACTS_EXTRACTION_EVERY_N_TURNS = 5


def _maybe_trigger_facts_extraction(db: Session, conv: WaConversation) -> None:
    """Extrae hechos del contacto cada N turnos (falla silenciosamente)."""
    turn_count = getattr(conv, "turn_count", 0) or 0
    if turn_count > 0 and turn_count % _FACTS_EXTRACTION_EVERY_N_TURNS == 0:
        from src.agent.facts_extractor import extract_contact_facts
        extract_contact_facts(db, conv)


def _maybe_respond_with_agent(
    db: Session,
    wn: WaNumber,
    conv: WaConversation,
    inbound_msg: WaMessage,
) -> None:
    """Llama al agente si el número tiene persona asignada.

    Los errores se tragan aquí para que el webhook nunca falle por el agente.
    La llamada es síncrona en Fase 2; Celery lo asincrona en Fase 11.
    """
    if getattr(wn, "persona_id", None) is None:
        return
    try:
        from src.agent import service as agent_service
        agent_service.respond(db, conv, inbound_msg)
    except Exception:
        logger.exception(
            "webhook: error invocando agent.respond conv=%s", conv.id
        )


def _dispatch_message_ack(db: Session, wn: WaNumber, event: dict) -> dict:
    """Actualiza ACK del mensaje saliente con el invariante monotónico."""
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return {"ok": True, "skipped": "payload_not_dict"}

    wa_msg_id = str(payload.get("id") or "").strip()
    if not wa_msg_id:
        return {"ok": True, "skipped": "no_wa_message_id"}

    incoming_ack = _normalize_waha_ack(payload.get("ack"))
    if not incoming_ack:
        return {"ok": True, "skipped": "ack_unknown"}

    with bypass_tenant_filter():
        msg = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_number_id == wn.id,
                WaMessage.wa_message_id == wa_msg_id,
            )
            .first()
        )
    if msg is None:
        logger.info(
            "webhook waha ack: msg %s no encontrado (eco de outbound desconocido)",
            wa_msg_id,
        )
        return {"ok": True, "skipped": "msg_not_found"}

    changed = update_message_ack(msg, incoming_ack)
    if changed:
        db.commit()
    return {"ok": True, "ack": msg.ack, "changed": changed}


def _dispatch_session_status(db: Session, wn: WaNumber, event: dict) -> dict:
    """Persiste el cambio de status de la sesión y QR si viene."""
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return {"ok": True, "skipped": "payload_not_dict"}

    new_status = str(payload.get("status") or "").upper().strip()
    if not new_status:
        return {"ok": True, "skipped": "no_status"}

    qr_b64 = ""
    qr_data = payload.get("qr") or payload.get("data") or {}
    if isinstance(qr_data, dict):
        qr_b64 = str(qr_data.get("data") or qr_data.get("base64") or "")
    elif isinstance(qr_data, str):
        qr_b64 = qr_data

    with bypass_tenant_filter():
        sess = (
            db.query(WaSession)
            .filter(WaSession.wa_number_id == wn.id)
            .first()
        )
        if sess is None:
            sess = WaSession(
                tenant_id=wn.tenant_id,
                wa_number_id=wn.id,
                status=new_status,
            )
            db.add(sess)
        sess.status = new_status
        sess.last_status_at = _utcnow()
        if new_status == "SCAN_QR_CODE" and qr_b64:
            sess.qr_data_b64 = qr_b64
        elif new_status == "WORKING":
            sess.qr_data_b64 = ""
        db.commit()

    return {"ok": True, "status": new_status}


# WAHA emite ack como string ('SENT', 'DELIVERED', 'READ', 'PLAYED', 'ERROR')
# o como int (-1 ERROR, 0 PENDING, 1 SENT, 2 DELIVERED, 3 READ, 4 PLAYED).
_WAHA_ACK_MAP = {
    "PENDING": "",
    "SENT": "sent",
    "DELIVERED": "delivered",
    "READ": "read",
    "PLAYED": "read",
    "ERROR": "failed",
}
_WAHA_ACK_INT_MAP = {-1: "failed", 0: "", 1: "sent", 2: "delivered", 3: "read", 4: "read"}


def _normalize_waha_ack(raw: Any) -> str:
    """Convierte el ``ack`` que emite WAHA a uno de nuestros estados.

    Devuelve ``""`` si el valor es desconocido o pending — el caller skipea.
    """
    if isinstance(raw, str):
        return _WAHA_ACK_MAP.get(raw.upper().strip(), "")
    if isinstance(raw, (int, float)):
        return _WAHA_ACK_INT_MAP.get(int(raw), "")
    return ""
