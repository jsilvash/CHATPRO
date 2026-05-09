"""Integracion con WhatsApp Business API (Meta Cloud API)."""

import logging
from contextvars import ContextVar

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# Captura del último error de Meta para que el caller (record_message_sent)
# pueda persistirlo en `messages_sent.last_error`. Antes los errores sólo
# iban a Railway logs, perdiéndose para post-mortems.
# Se setea en cada except y se lee/limpia con `pop_last_send_error()`.
_last_send_error: ContextVar[str | None] = ContextVar(
    "wa_last_send_error", default=None
)


def pop_last_send_error() -> str | None:
    """Lee y limpia el último error de send_whatsapp_*.

    El caller debe invocarlo justo después del send. Devuelve ``None``
    si el send fue exitoso o si nunca se llamó a un send en este context.
    """
    err = _last_send_error.get()
    _last_send_error.set(None)
    return err


def _record_send_error(msg: str) -> None:
    """Helper interno — guarda el error en el ContextVar."""
    _last_send_error.set(msg[:480] if msg else None)


def _get_wa_credentials(tenant_config: dict | None = None) -> tuple[str, str]:
    """Obtiene credenciales WhatsApp: primero del tenant, luego del .env."""
    settings = get_settings()
    if tenant_config:
        wa = tenant_config.get("whatsapp", {})
        if wa.get("enabled") and wa.get("token") and wa.get("phone_number_id"):
            return wa["token"], wa["phone_number_id"]
    return settings.whatsapp_token, settings.whatsapp_phone_number_id


def send_whatsapp_template(
    phone: str,
    template_name: str,
    params: dict,
    language: str = "es",
    tenant_config: dict | None = None,
) -> str | None:
    """Envia un mensaje de template de WhatsApp.

    Returns:
        message_id del mensaje enviado, o None si fallo.
    """
    # Limpiar error previo al iniciar el envío.
    _last_send_error.set(None)
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        logger.warning("WhatsApp not configured, skipping send to %s", phone)
        _record_send_error("not_configured: missing token or phone_id")
        return None

    phone = _normalize_phone(phone)
    if not phone:
        logger.warning("Invalid phone number, skipping")
        _record_send_error("invalid_phone")
        return None

    # Construir parametros del template
    components = []
    if params:
        body_params = [
            {"type": "text", "text": str(v)} for v in params.values()
        ]
        components.append({
            "type": "body",
            "parameters": body_params,
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": components,
        },
    }

    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        message_id = data.get("messages", [{}])[0].get("id", "")
        logger.info("WhatsApp sent to %s: template=%s, id=%s", phone, template_name, message_id)
        return message_id
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        logger.error("WhatsApp API error: %s - %s", e.response.status_code, body)
        _record_send_error(f"meta_http_{e.response.status_code}: {body}")
        return None
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)
        _record_send_error(f"exception: {type(e).__name__}: {e}")
        return None


def send_whatsapp_text(phone: str, text: str, tenant_config: dict | None = None) -> str | None:
    """Envia un mensaje de texto libre por WhatsApp."""
    _last_send_error.set(None)
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        logger.warning("WhatsApp not configured, skipping")
        _record_send_error("not_configured: missing token or phone_id")
        return None

    phone = _normalize_phone(phone)
    if not phone:
        _record_send_error("invalid_phone")
        return None

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }

    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        message_id = data.get("messages", [{}])[0].get("id", "")
        logger.info("WhatsApp text sent to %s, id=%s", phone, message_id)
        return message_id
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        logger.error("WhatsApp text API error: %s - %s", e.response.status_code, body)
        _record_send_error(f"meta_http_{e.response.status_code}: {body}")
        return None
    except Exception as e:
        logger.error("WhatsApp text send failed: %s", e)
        _record_send_error(f"exception: {type(e).__name__}: {e}")
        return None


def upload_media(
    file_bytes: bytes,
    mime: str,
    filename: str,
    tenant_config: dict | None = None,
) -> str | None:
    """Sube un media al CDN de Meta y retorna el `media_id` resultante.

    Meta exige este paso previo antes de enviar cualquier media outbound:
    no se puede pasar bytes ni URL directa en `/messages`. El `media_id`
    queda válido ~30 días para re-enviar al mismo contacto o a otro.

    Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media#upload-media
    """
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        logger.warning("WhatsApp upload_media: sin credenciales")
        return None
    if not file_bytes or not mime:
        logger.warning("WhatsApp upload_media: bytes o mime vacíos")
        return None

    url = f"{WHATSAPP_API_URL}/{phone_id}/media"
    headers = {"Authorization": f"Bearer {token}"}
    files = {
        "file": (filename or "upload", file_bytes, mime),
        "type": (None, mime),
        "messaging_product": (None, "whatsapp"),
    }
    try:
        resp = httpx.post(url, headers=headers, files=files, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        media_id = data.get("id", "") or ""
        if not media_id:
            logger.warning("upload_media: Meta no retornó id: %s", data)
            return None
        logger.info("WhatsApp media uploaded: id=%s mime=%s size=%d", media_id, mime, len(file_bytes))
        return media_id
    except httpx.HTTPStatusError as e:
        logger.error("upload_media HTTP %d: %s", e.response.status_code, (e.response.text or "")[:300])
        return None
    except Exception as e:
        logger.error("upload_media falló: %s", e)
        return None


def send_media_message(
    phone: str,
    media_id: str,
    msg_type: str,
    *,
    caption: str = "",
    filename: str = "",
    tenant_config: dict | None = None,
) -> str | None:
    """Envía un mensaje media usando un `media_id` ya subido a Meta.

    `msg_type`: "image" | "audio" | "video" | "document" | "sticker".
    `caption` solo aplica a image/video/document. `filename` solo a document.

    Returns `wa_message_id` o None si falla.
    """
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        logger.warning("send_media_message: sin credenciales")
        return None
    phone = _normalize_phone(phone)
    if not phone:
        return None
    if msg_type not in ("image", "audio", "video", "document", "sticker"):
        logger.warning("send_media_message: tipo no soportado: %s", msg_type)
        return None

    media_obj: dict = {"id": media_id}
    if caption and msg_type in ("image", "video", "document"):
        media_obj["caption"] = caption
    if filename and msg_type == "document":
        media_obj["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": msg_type,
        msg_type: media_obj,
    }
    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        wa_id = data.get("messages", [{}])[0].get("id", "")
        logger.info("WhatsApp media sent: to=%s type=%s id=%s", phone, msg_type, wa_id)
        return wa_id
    except httpx.HTTPStatusError as e:
        logger.error("send_media_message HTTP %d: %s", e.response.status_code, (e.response.text or "")[:300])
        return None
    except Exception as e:
        logger.error("send_media_message falló: %s", e)
        return None


def mark_as_read(message_id: str, tenant_config: dict | None = None) -> bool:
    """Marca un mensaje como leído en WhatsApp (double blue check)."""
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("WhatsApp mark_as_read failed: %s", e)
        return False


def send_typing_indicator(message_id: str, tenant_config: dict | None = None) -> bool:
    """Muestra 'escribiendo...' al contacto por 25 segundos.

    Requiere un `message_id` inbound (Meta ancla el typing indicator a una
    respuesta en curso a un mensaje específico). El indicador se oculta
    automáticamente a los ~25 segundos o cuando enviamos el mensaje real.
    """
    token, phone_id = _get_wa_credentials(tenant_config)
    if not token or not phone_id:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("WhatsApp typing_indicator failed: %s", e)
        return False


def download_media(
    media_id: str, tenant_config: dict | None = None
) -> tuple[bytes, str, str] | None:
    """Descarga un media inbound desde Meta Graph API.

    Flujo Cloud API (documentado por Meta): `GET /{media_id}` retorna un
    JSON con `url` (firmado, expira ~5 min) + `mime_type`. Después hay que
    hacer GET a esa URL con el mismo Bearer token para bajar los bytes.

    Retorna `(bytes, mime, filename)` o `None` si falla. El filename queda
    vacío (Cloud API no lo expone en el inbound webhook; se puede deducir
    del mime cuando se renderiza).
    """
    if not media_id:
        return None
    token, _phone_id = _get_wa_credentials(tenant_config)
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        meta = httpx.get(
            f"{WHATSAPP_API_URL}/{media_id}", headers=headers, timeout=10.0
        )
        meta.raise_for_status()
        meta_json = meta.json()
        url = meta_json.get("url") or ""
        mime = (meta_json.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip()
        if not url:
            logger.warning("download_media: Meta devolvió sin url para %s", media_id)
            return None
        # La URL firmada requiere el mismo Bearer token para descargar; no
        # es un CDN público.
        bin_resp = httpx.get(url, headers=headers, timeout=30.0)
        bin_resp.raise_for_status()
        return bin_resp.content, mime, ""
    except httpx.HTTPStatusError as e:
        logger.warning(
            "download_media: Meta HTTP %s para %s: %s",
            e.response.status_code, media_id, (e.response.text or "")[:200],
        )
        return None
    except Exception as e:
        logger.warning("download_media: excepción para %s: %s", media_id, e)
        return None


def _normalize_phone(phone: str) -> str | None:
    """Normaliza teléfono chileno al formato internacional 56XXXXXXXXX.

    Alias retrocompatible. La implementación vive en `src.utils.phone`
    para que el CRM y conectores omnicanal la reutilicen sin acoplarse
    al módulo de WhatsApp.
    """
    from src.utils.phone import normalize_phone as _norm
    return _norm(phone)
