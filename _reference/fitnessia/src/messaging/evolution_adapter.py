"""Adapter de eventos Evolution API → shape interno de Cloud API.

Evolution manda eventos al webhook global (`WEBHOOK_GLOBAL_URL`) con un
payload unificado `{event, instance, data, apikey, ...}`. Este módulo
traduce ese payload al mismo shape que el webhook Cloud API ya procesa en
`_handle_incoming_message`, de modo que el pipeline downstream (IA, inbox,
handoff) no se entere del tipo de conexión.

Funciones puras: sin DB, sin HTTP, sin side-effects. Todas devuelven
`None` cuando el evento viene malformado o es de un tipo que no nos
interesa, y dejan que el caller decida qué hacer.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Evolution status flags → SYNEX WhatsAppMessage.status.
# `SERVER_ACK` = Evolution aceptó el envío al server WhatsApp.
# `DELIVERY_ACK` = el device del destinatario lo recibió.
# `READ` = el destinatario lo abrió; `PLAYED` = audio reproducido.
_STATUS_MAP = {
    "SERVER_ACK": "sent",
    "DELIVERY_ACK": "delivered",
    "READ": "read",
    "PLAYED": "read",
    "ERROR": "failed",
    "PENDING": "sent",
}

# Evolution connection states → SYNEX WhatsAppNumber.qr_connection_status.
_CONNECTION_STATE_MAP = {
    "open": "connected",
    "connecting": "connecting",
    "close": "disconnected",
}


def _strip_jid(jid: str) -> str:
    """Quita el sufijo JID (`@s.whatsapp.net`, `@g.us`) y devuelve el teléfono raw.

    Mensajes de grupos (`@g.us`) NO los queremos procesar — el webhook debe
    filtrarlos antes de llamar al adapter. Acá solo limpiamos el sufijo.
    """
    if not jid:
        return ""
    return jid.split("@", 1)[0]


def _is_group_jid(jid: str) -> bool:
    return "@g.us" in (jid or "")


def _is_lid_jid(jid: str) -> bool:
    """WhatsApp LID addressing mode: el JID NO es el teléfono real del contacto.

    En LID mode el `remoteJid` es un identificador local tipo
    `112322068623427@lid`. El número real viaja en otros campos del payload
    (senderPn, participantPn, ...). Enviar un mensaje a un `@lid` falla con
    `exists: False`, por eso necesitamos resolverlo al PN antes de persistir.
    """
    return "@lid" in (jid or "")


def _extract_sender_pn(data: dict) -> str:
    """Busca el phone number (PN) real del sender en un payload Evolution/Baileys.

    Evolution v2.2.3 no estandariza dónde viaja `senderPn` — Baileys lo
    pone en distintos lugares según la versión. Probamos los más comunes y
    devolvemos el primero que matchee. Retorna solo los dígitos (sin `@`).
    """
    if not isinstance(data, dict):
        return ""
    candidates: list[str] = []
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    # Baileys nuevos (addressing_mode=lid): senderPn/senderPhoneNumber en key.
    for k in ("senderPn", "senderPhoneNumber", "participantPn", "sender_pn"):
        v = key.get(k) or data.get(k)
        if isinstance(v, str) and v:
            candidates.append(v)
    # Algunos payloads exponen `messageContextInfo.senderPn` o lo anidan en
    # `contextInfo`. Defensivo — no asumimos estructura.
    ci = data.get("contextInfo") if isinstance(data.get("contextInfo"), dict) else {}
    for k in ("senderPn", "participantPn", "sender_pn"):
        v = ci.get(k)
        if isinstance(v, str) and v:
            candidates.append(v)
    for cand in candidates:
        phone = cand.split("@", 1)[0]
        # Validar que sea dígitos (ignorar JIDs raros).
        if phone.isdigit() and len(phone) >= 7:
            return phone
    return ""


def _extract_text(msg_payload: dict) -> str | None:
    """Texto 'plano' de un mensaje Evolution. None si no es tipo texto."""
    if not isinstance(msg_payload, dict):
        return None
    # Mensaje simple sin meta.
    if "conversation" in msg_payload and isinstance(msg_payload["conversation"], str):
        return msg_payload["conversation"]
    # Texto con metadata (reply, mentions, preview de link).
    ext = msg_payload.get("extendedTextMessage")
    if isinstance(ext, dict):
        t = ext.get("text")
        if isinstance(t, str):
            return t
    return None


def _map_message(data: dict) -> tuple[str, dict, str]:
    """Traduce `data.message` + `data.messageType` → (cloud_api_type, cloud_api_subdict, media_url).

    Shapes Cloud API que produce (replicando `_handle_incoming_message`):
      - ("text", {"body": "hola"}, "")
      - ("image", {"caption": "...", "id": "<media_url>"}, "<media_url>")
      - ("audio", {"id": "<media_url>"}, "<media_url>")
      - ("document", {"filename": "...", "id": "<media_url>"}, "<media_url>")
      - ("reaction", {"emoji": "😀"}, "")
      - ("<tipo_desconocido>", {}, "") → queda como `[tipo]` en el content

    `media_url` queda separado porque en Cloud API SYNEX guarda el `media.id`
    en `WhatsAppMessage.media_url` y en Evolution el equivalente es el `.url`
    firmado que devuelve Baileys.
    """
    msg = data.get("message") if isinstance(data.get("message"), dict) else {}
    msg_type_raw = data.get("messageType", "")

    text = _extract_text(msg)
    if text is not None:
        return "text", {"body": text}, ""

    img = msg.get("imageMessage")
    if isinstance(img, dict):
        url = img.get("url", "") or ""
        caption = img.get("caption", "") or ""
        return "image", {"caption": caption, "id": url}, url

    audio = msg.get("audioMessage")
    if isinstance(audio, dict):
        url = audio.get("url", "") or ""
        return "audio", {"id": url}, url

    doc = msg.get("documentMessage") or msg.get("documentWithCaptionMessage")
    if isinstance(doc, dict):
        # Evolution a veces anida: documentWithCaptionMessage.message.documentMessage
        inner = doc.get("message", {}).get("documentMessage") if "message" in doc else None
        d = inner if isinstance(inner, dict) else doc
        url = d.get("url", "") or ""
        filename = d.get("fileName", "") or ""
        return "document", {"filename": filename, "id": url}, url

    video = msg.get("videoMessage")
    if isinstance(video, dict):
        url = video.get("url", "") or ""
        caption = video.get("caption", "") or ""
        # Cloud API expone 'video' pero SYNEX lo trata como type desconocido →
        # que caiga al branch genérico. Devolvemos 'video' para que el logger
        # capture el tipo real.
        return "video", {"caption": caption, "id": url}, url

    reaction = msg.get("reactionMessage")
    if isinstance(reaction, dict):
        emoji = reaction.get("text", "") or ""
        return "reaction", {"emoji": emoji}, ""

    sticker = msg.get("stickerMessage")
    if isinstance(sticker, dict):
        url = sticker.get("url", "") or ""
        return "sticker", {"id": url}, url

    # Ubicación / contacto / lista / poll / etc. caen acá.
    # Preservamos el tipo raw para que el logger muestre info útil.
    fallback_type = msg_type_raw or "unknown"
    return fallback_type, {}, ""


# ────────────────────────────────────────────────────────────
# Adapters públicos (uno por event type de Evolution)
# ────────────────────────────────────────────────────────────


def adapt_messages_upsert(evt: dict) -> dict | None:
    """Convierte `messages.upsert` a formato Cloud API.

    Retorna un dict con:
        {
            "instance": "nye-1",
            "from_me": False,                 # si True, se descarta (echo)
            "is_group": False,                # si True, se descarta
            "message": { ...cloud_api shape... },
            "value":   { "contacts": [...] }, # shape Cloud API
        }

    Devuelve `None` si el evento viene vacío o es un mensaje que no debemos
    persistir (grupo, echo de nosotros mismos, key.id vacío).
    """
    if not isinstance(evt, dict):
        return None
    data = evt.get("data")
    if not isinstance(data, dict):
        return None

    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    wa_message_id = key.get("id", "") or ""
    if not wa_message_id:
        return None

    remote_jid = key.get("remoteJid", "") or ""
    if _is_group_jid(remote_jid):
        return None  # grupos fuera de scope (Fase futura)

    from_me = bool(key.get("fromMe", False))
    if from_me:
        # Es un mensaje que salió desde el QR mismo (otro device del gimnasio
        # respondiendo fuera de SYNEX). Lo ignoramos en inbound — Fase 7
        # puede decidir si lo muestra como "mensaje desde device" del agente.
        return None

    # En LID mode el remoteJid NO es el teléfono real. Evolution v2.2.3 no
    # propaga `senderPn` en el payload del webhook (verificado 2026-04-22) y
    # tampoco viaja en `message.messageContextInfo`. Propagamos el `lid_jid`
    # crudo para que el caller lo resuelva async via evolution_client —
    # meter HTTP acá rompería la pureza de esta función.
    lid_jid = ""
    if _is_lid_jid(remote_jid):
        pn = _extract_sender_pn(data)
        if pn:
            from_phone = pn
        else:
            lid_jid = remote_jid
            from_phone = _strip_jid(remote_jid)
    else:
        from_phone = _strip_jid(remote_jid)
    push_name = (data.get("pushName") or "").strip()
    msg_type, sub, _media = _map_message(data)

    cloud_message: dict[str, Any] = {
        "id": wa_message_id,
        "from": from_phone,
        "type": msg_type,
    }
    if sub:
        cloud_message[msg_type] = sub

    cloud_value: dict[str, Any] = {}
    if push_name:
        cloud_value["contacts"] = [{"profile": {"name": push_name}}]

    return {
        "instance": evt.get("instance", "") or "",
        "from_me": False,
        "is_group": False,
        "message": cloud_message,
        "value": cloud_value,
        "lid_jid": lid_jid,  # "" si no aplica; caller debe resolver vía API
        # Payload crudo del `data`, necesario para que el webhook descargue
        # media inbound via `/chat/getBase64FromMediaMessage` (Evolution
        # cifra el contenido con mediaKey; el `.url` en el adapter no es
        # descargable directo).
        "raw_data": data,
    }


def adapt_messages_update(evt: dict) -> dict | None:
    """Convierte `messages.update` → {instance, wa_message_id, status}.

    Devuelve `None` si falta keyId / status, o si el status no mapea a un
    estado conocido (p.ej. eventos de edición de mensaje).
    """
    if not isinstance(evt, dict):
        return None
    data = evt.get("data")
    if not isinstance(data, dict):
        return None

    wa_message_id = data.get("keyId", "") or data.get("key", {}).get("id", "")
    if not wa_message_id:
        return None

    raw_status = data.get("status", "")
    if not raw_status:
        return None
    mapped = _STATUS_MAP.get(raw_status.upper())
    if not mapped:
        return None

    return {
        "instance": evt.get("instance", "") or "",
        "wa_message_id": wa_message_id,
        "status": mapped,
    }


def adapt_connection_update(evt: dict) -> dict | None:
    """Convierte `connection.update` → {instance, qr_connection_status}."""
    if not isinstance(evt, dict):
        return None
    data = evt.get("data")
    if not isinstance(data, dict):
        return None
    state_raw = (data.get("state") or "").lower()
    mapped = _CONNECTION_STATE_MAP.get(state_raw)
    if not mapped:
        return None
    return {
        "instance": evt.get("instance", "") or "",
        "qr_connection_status": mapped,
    }


def adapt_presence_update(evt: dict) -> dict | None:
    """Convierte `presence.update` → {instance, phone, presence}.

    Evolution envía este evento cuando el contacto cambia su estado de
    actividad en WhatsApp (escribiendo, grabando audio, online/offline).

    Shape típico del payload Evolution:

        data: {
            id: "5511999999999@s.whatsapp.net",
            presences: {
                "5511999999999@s.whatsapp.net": { "lastKnownPresence": "composing" }
            }
        }

    Mapeo de valores:
      - `composing`   → escribiendo (ON)
      - `recording`   → grabando audio (ON)
      - `paused`      → dejó de escribir (OFF)
      - `available`   → solo online/offline, ignoramos (OFF equivalente)
      - `unavailable` → offline, ignoramos

    Solo nos interesan los 3 primeros (estado ON/OFF del indicador). El
    dispatcher traduce a `set_typing` / `clear_typing`. Para grupos
    (`@g.us`) devolvemos None — el panel no gestiona grupos.
    """
    if not isinstance(evt, dict):
        return None
    data = evt.get("data")
    if not isinstance(data, dict):
        return None

    jid = (data.get("id") or "").strip()
    if not jid or _is_group_jid(jid):
        return None

    presences = data.get("presences") if isinstance(data.get("presences"), dict) else {}
    entry = presences.get(jid) if isinstance(presences.get(jid), dict) else {}
    presence = (entry.get("lastKnownPresence") or data.get("presence") or "").strip().lower()
    if not presence:
        return None

    phone = _strip_jid(jid)
    if not phone:
        return None

    return {
        "instance": evt.get("instance", "") or "",
        "phone": phone,
        "presence": presence,
    }


def adapt_qrcode_updated(evt: dict) -> dict | None:
    """Convierte `qrcode.updated` → {instance, base64, code, pairing_code}."""
    if not isinstance(evt, dict):
        return None
    data = evt.get("data")
    if not isinstance(data, dict):
        return None
    qr = data.get("qrcode") if isinstance(data.get("qrcode"), dict) else data
    base64 = qr.get("base64", "") or ""
    code = qr.get("code", "") or ""
    pairing = qr.get("pairingCode", "") or qr.get("pairing_code", "") or ""
    if not (base64 or code or pairing):
        return None
    return {
        "instance": evt.get("instance", "") or "",
        "base64": base64,
        "code": code,
        "pairing_code": pairing,
    }
