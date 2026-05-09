"""Helper LID Fix C extraído de whatsapp_inbox.py para uso compartido.

Cuando una conversación de WhatsApp quedó persistida con un LID
(en vez del PN real) en `wa_contact_phone`, este helper intenta
resolverlo al PN antes de un envío outbound vía dispatcher.

Lo usan tanto los endpoints legacy `/whatsapp/conversations/*/send*`
como los endpoints Cases-aware `/cases/{id}/send-whatsapp*`.

Ver `FASE_WA_LID_FIX_C.md`.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LID_MIN_DIGITS = 14


def rehydrate_conversation_phone(db: Session, conv, wn) -> str:
    """Intenta convertir un LID persistido en `wa_contact_phone` al PN real
    antes de enviar un mensaje outbound.

    Cuando una conversación quedó con un LID en BD (porque llegó antes de
    Fase B o el payload no incluía `senderPn`), este helper:

      1. Si el phone no parece LID (< 14 dígitos) → devuelve tal cual.
      2. Si el `connection_type` del wa_number no es "waha" → devuelve tal
         cual (solo WAHA tiene endpoint nativo lid-pn).
      3. Llama a `waha_client.resolve_lid_to_pn`. Si resuelve:
         - Actualiza `conv.wa_contact_phone` con el PN.
         - Commitea.
         - Devuelve el PN (el dispatcher lo usa como destinatario).
      4. Si no resuelve (o crashea): devuelve el LID sin tocar. El
         dispatcher WAHA llamará a `_resolve_target` y Fase A levantará
         `WahaAPIError(-2)` con error legible al operador.

    Devuelve siempre un string (nunca None), defensivo contra datos malformados.
    Ver `FASE_WA_LID_FIX_C.md`.
    """
    phone = conv.wa_contact_phone or ""
    if not (phone.isdigit() and len(phone) >= _LID_MIN_DIGITS):
        return phone

    if getattr(wn, "connection_type", "") != "waha":
        return phone

    session_name = getattr(wn, "evolution_instance_name", "") or ""
    if not session_name:
        return phone

    try:
        from src.messaging import waha_client
        lid_jid = f"{phone}@lid"
        pn = waha_client.resolve_lid_to_pn(session_name, lid_jid)
    except Exception as e:
        logger.warning(
            "inbox rehydrate: resolve_lid_to_pn crasheó (conv=%s lid=%s): %s",
            conv.id, phone, e,
        )
        return phone

    if not pn:
        logger.warning(
            "inbox rehydrate: LID %s no resuelto (conv=%s) — dispatcher "
            "WAHA bloqueará el envío con error al operador (Fase A).",
            phone, conv.id,
        )
        return phone

    logger.info(
        "inbox rehydrate: conv=%s LID %s → PN %s — persistiendo.",
        conv.id, phone, pn,
    )
    conv.wa_contact_phone = pn
    db.commit()
    return pn
