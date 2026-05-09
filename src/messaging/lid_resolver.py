"""Rehidratación LID → PN antes de enviar mensajes outbound.

Cuando una conversación quedó persistida con un LID en
``WaConversation.wa_contact_phone`` (porque el contacto envió antes de
tener el sender PN, o el payload no incluía ``senderPn``), este helper
intenta resolverlo al PN real antes de un envío outbound.

Si la resolución falla, devuelve el LID sin tocar — el dispatcher WAHA
llamará a ``_resolve_target`` y levantará ``WahaAPIError(-2)`` con error
legible al operador (nunca enviar a ``@lid`` como chatId).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LID_MIN_DIGITS = 14


def rehydrate_conversation_phone(db: Session, conv, wn) -> str:
    """Devuelve el phone PN para enviar; persiste el LID→PN si lo resuelve.

    1. Si el phone no parece LID (< 14 dígitos) → devuelve tal cual.
    2. Si ``wn.waha_session_name`` no está disponible → devuelve tal cual.
    3. Llama a ``waha_client.resolve_lid_to_pn``. Si resuelve:
       - Actualiza ``conv.wa_contact_phone`` con el PN.
       - Commitea.
       - Devuelve el PN.
    4. Si no resuelve (o crashea): devuelve el LID. El dispatcher WAHA
       bloqueará el envío con ``WahaAPIError(-2)``.
    """
    phone = conv.wa_contact_phone or ""
    if not (phone.isdigit() and len(phone) >= _LID_MIN_DIGITS):
        return phone

    session_name = getattr(wn, "waha_session_name", "") or ""
    if not session_name:
        return phone

    try:
        from src.messaging import waha_client
        lid_jid = f"{phone}@lid"
        pn = waha_client.resolve_lid_to_pn(session_name, lid_jid)
    except Exception as e:
        logger.warning(
            "rehydrate: resolve_lid_to_pn crasheó (conv=%s lid=%s): %s",
            conv.id, phone, e,
        )
        return phone

    if not pn:
        logger.warning(
            "rehydrate: LID %s no resuelto (conv=%s) — dispatcher WAHA "
            "bloqueará el envío con WahaAPIError(-2).",
            phone, conv.id,
        )
        return phone

    logger.info(
        "rehydrate: conv=%s LID %s → PN %s — persistiendo.",
        conv.id, phone, pn,
    )
    conv.wa_contact_phone = pn
    db.commit()
    return pn
