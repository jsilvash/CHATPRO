"""Cliente Graph API para envío de mensajes Meta (Instagram + Facebook Messenger).

Usado por el endpoint `POST /cases/{id}/send-meta-message` para respuesta
manual del agente desde el Inbox CRM.

Requiere un Page Access Token almacenado en
`tenant.settings_json.meta_pages[page_id]['access_token']`.

Referencia: https://developers.facebook.com/docs/messenger-platform/send-messages/
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"
# Instagram acepta hasta 1 000 chars; Messenger hasta 2 000.
# Usamos el límite más restrictivo por defecto; el caller puede truncar antes.
MAX_TEXT_IG = 1000
MAX_TEXT_FB = 2000


def send_meta_message(
    *,
    page_id: str,
    page_access_token: str,
    recipient_id: str,
    text: str,
    platform: str = "instagram",
) -> tuple[str | None, dict | None]:
    """Envía un mensaje de texto vía Graph API (IG o Messenger).

    Args:
        page_id:            ID de la FB Page o IG Business Account.
        page_access_token:  Page Access Token del tenant (~60 días de vida).
        recipient_id:       IGSID o PSID del usuario final (destinatario).
        text:               Texto a enviar.
        platform:           "instagram" | "facebook". Ajusta el límite de chars.

    Returns:
        Tuple ``(message_id, error)`` donde:
          - ``message_id`` (str) si el envío fue exitoso, sino ``None``.
          - ``error`` (dict) con el shape de Graph API
            ``{"code": int, "error_subcode": int, "message": str, ...}``
            cuando ``message_id`` es ``None``. ``None`` cuando éxito.

        El caller debe inspeccionar ``error`` para distinguir entre
        ventana 24h cerrada (code=10/subcode=2534022), token expirado
        (code=190), y otros — y dar mensajes legibles al operador.
    """
    if not page_id or not page_access_token or not recipient_id:
        logger.error(
            "send_meta_message: parámetros incompletos page=%s recipient=%s",
            page_id, recipient_id,
        )
        return None, {"code": -1, "message": "parámetros incompletos"}

    max_len = MAX_TEXT_IG if platform == "instagram" else MAX_TEXT_FB
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text[:max_len]},
        "messaging_type": "RESPONSE",
    }

    # /me/messages funciona tanto para FB Messenger como para IG via
    # Messenger-from-Meta cuando `page_access_token` es el Page Access Token
    # de la FB Page linkeada. Evita el error Graph API `(#3) Application does
    # not have the capability` que se produce al golpear `/{IGSID}/messages`
    # — ese path pertenece al use case "API de Instagram" standalone.
    # Docs: https://developers.facebook.com/docs/messenger-platform/send-messages
    url = f"{GRAPH_BASE}/me/messages"
    try:
        r = httpx.post(
            url,
            json=payload,
            params={"access_token": page_access_token},
            timeout=15.0,
        )
        if r.status_code == 200:
            data = r.json()
            msg_id = data.get("message_id") or ""
            logger.info(
                "meta outbound OK page=%s recipient=%s msg_id=%s",
                page_id, recipient_id, msg_id,
            )
            return (msg_id or "sent"), None
        else:
            body = r.json() if r.content else {}
            err = body.get("error") if isinstance(body, dict) else None
            if not isinstance(err, dict):
                err = {"code": r.status_code, "message": str(body)[:200]}
            logger.error(
                "meta outbound FAILED page=%s recipient=%s status=%s error=%s",
                page_id, recipient_id, r.status_code, body,
            )
            return None, err
    except httpx.TimeoutException:
        logger.error(
            "meta outbound TIMEOUT page=%s recipient=%s", page_id, recipient_id
        )
        return None, {"code": -2, "message": "timeout al llamar Graph API"}
    except Exception as exc:
        logger.exception(
            "meta outbound EXCEPTION page=%s recipient=%s: %s",
            page_id, recipient_id, exc,
        )
        return None, {"code": -3, "message": f"excepción de red: {exc}"}


def send_meta_image(
    *,
    page_id: str,
    page_access_token: str,
    recipient_id: str,
    file_bytes: bytes,
    mime: str,
    filename: str = "upload.jpg",
    platform: str = "instagram",
) -> tuple[str | None, dict | None]:
    """Envía una imagen vía Graph API en un solo POST multipart.

    Fase MEDIA 5: zero-storage. Stream directo de los bytes del operador
    a Meta — no se persiste el binario en SYNEX. Meta hostea la imagen
    en su CDN y devuelve `message_id`.

    Body multipart al endpoint `/me/messages`:
      - `recipient`: JSON string con `{"id": recipient_id}`.
      - `message`: JSON string con `{"attachment":{"type":"image","payload":{"is_reusable":false}}}`.
      - `messaging_type`: `RESPONSE`.
      - `filedata`: el binario con MIME real.
      - `access_token`: query param.

    Args:
        page_id:            ID de la FB Page o IG Business Account (logging).
        page_access_token:  Page Access Token del tenant (~60 días).
        recipient_id:       IGSID o PSID del usuario destinatario.
        file_bytes:         Bytes de la imagen.
        mime:               MIME real (image/png, image/jpeg, image/webp).
        filename:           Nombre que ve Meta (no se muestra al contacto;
                            ayuda al MIME sniffing del lado Graph).
        platform:           "instagram" | "facebook" (logging).

    Returns:
        Tuple ``(message_id, error)``. Mismo contrato que ``send_meta_message``:
        ``message_id`` cuando éxito, ``error`` (dict shape Graph API) cuando
        falla. Ver el docstring de ``send_meta_message`` para detalles.
    """
    import json as _json

    if not page_id or not page_access_token or not recipient_id:
        logger.error(
            "send_meta_image: parámetros incompletos page=%s recipient=%s",
            page_id, recipient_id,
        )
        return None, {"code": -1, "message": "parámetros incompletos"}
    if not file_bytes:
        logger.error("send_meta_image: file_bytes vacío page=%s", page_id)
        return None, {"code": -1, "message": "file_bytes vacío"}

    url = f"{GRAPH_BASE}/me/messages"
    data = {
        "recipient": _json.dumps({"id": recipient_id}),
        "message": _json.dumps({
            "attachment": {
                "type": "image",
                "payload": {"is_reusable": False},
            },
        }),
        "messaging_type": "RESPONSE",
    }
    files = {"filedata": (filename, file_bytes, mime or "application/octet-stream")}
    try:
        r = httpx.post(
            url,
            params={"access_token": page_access_token},
            data=data,
            files=files,
            timeout=30.0,
        )
        if r.status_code == 200:
            payload = r.json()
            msg_id = payload.get("message_id") or ""
            logger.info(
                "meta outbound image OK page=%s recipient=%s mime=%s size=%d msg_id=%s",
                page_id, recipient_id, mime, len(file_bytes), msg_id,
            )
            return (msg_id or "sent"), None
        else:
            body = r.json() if r.content else {}
            err = body.get("error") if isinstance(body, dict) else None
            if not isinstance(err, dict):
                err = {"code": r.status_code, "message": str(body)[:200]}
            logger.error(
                "meta outbound image FAILED page=%s recipient=%s status=%s error=%s",
                page_id, recipient_id, r.status_code, body,
            )
            return None, err
    except httpx.TimeoutException:
        logger.error("meta outbound image TIMEOUT page=%s recipient=%s", page_id, recipient_id)
        return None, {"code": -2, "message": "timeout al subir imagen a Graph API"}
    except Exception as exc:
        logger.exception(
            "meta outbound image EXCEPTION page=%s recipient=%s: %s",
            page_id, recipient_id, exc,
        )
        return None, {"code": -3, "message": f"excepción de red: {exc}"}


def fetch_meta_user_profile(
    *,
    external_id: str,
    page_access_token: str,
    channel: str = "instagram",
) -> dict | None:
    """Resuelve el perfil público de un IGSID/PSID vía Graph API.

    Usado por el webhook Meta cuando llega un DM de un contacto desconocido
    para obtener el nombre real del usuario (Meta webhook IG no envía
    `sender.name`, solo `sender.id`).

    Args:
        external_id:        IGSID (IG) o PSID (FB) del usuario.
        page_access_token:  Page Access Token del tenant.
        channel:            "instagram" o "facebook". Ajusta los `fields`.

    Returns:
        ``{"name": "...", "username": "..."}`` si el lookup fue exitoso.
        Para FB, `username` queda vacío (el endpoint no lo expone).
        ``None`` si Graph API devuelve error o hay excepción de red.

    Nota: requiere ventana de mensajería de 24 h abierta (el user le escribió
    a la página recientemente). Como llamamos esto desde el webhook inbound
    que acaba de recibir un DM, la ventana siempre está abierta.

    Docs:
    - IG: https://developers.facebook.com/docs/messenger-platform/instagram/features/user-profile
    - FB: https://developers.facebook.com/docs/messenger-platform/identity/user-profile
    """
    if not external_id or not page_access_token:
        return None
    fields = (
        "name,username,profile_pic"
        if channel == "instagram"
        else "first_name,last_name,profile_pic"
    )
    url = f"{GRAPH_BASE}/{external_id}"
    try:
        r = httpx.get(
            url,
            params={"fields": fields, "access_token": page_access_token},
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            # `profile_pic` es una URL firmada que caduca ~30 días; se refresca
            # sola en cada DM nuevo del contacto, y el frontend degrada a
            # iniciales con `onerror` si falla la carga.
            pic = (data.get("profile_pic") or "").strip()
            if channel == "instagram":
                return {
                    "name": (data.get("name") or "").strip(),
                    "username": (data.get("username") or "").strip(),
                    "profile_pic_url": pic,
                }
            # facebook: concatenamos first_name + last_name.
            fn = (data.get("first_name") or "").strip()
            ln = (data.get("last_name") or "").strip()
            return {
                "name": (fn + " " + ln).strip(),
                "username": "",
                "profile_pic_url": pic,
            }
        # 400/403 típicos: token expirado, fuera de ventana 24h, user borró
        # el chat. Degradamos silenciosamente — el caller mantiene la key
        # sintética como display.
        logger.warning(
            "meta profile lookup FAILED external=%s channel=%s status=%s body=%s",
            external_id, channel, r.status_code, r.text[:200],
        )
        return None
    except Exception as exc:
        logger.warning(
            "meta profile lookup EXCEPTION external=%s channel=%s: %s",
            external_id, channel, exc,
        )
        return None
