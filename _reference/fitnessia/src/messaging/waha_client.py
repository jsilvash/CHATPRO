"""Cliente HTTP para WAHA Plus (WhatsApp QR self-hosted, alternativa a Evolution).

Motivación: Evolution API v2.2.3 rechaza ~3.6 % de contactos LATAM con LID
addressing activo (Issue #1872, sin fix en v2.3.7). WAHA expone un endpoint
nativo `/api/{session}/contacts/lid-pn` que resuelve LID → PN directamente,
eliminando el workaround heurístico de `evolution_client.resolve_lid_to_pn`.

Spike validado 2026-04-23: WAHA Core en Railway → mensaje entregado a Nutre y
Entrena (+56941131946) donde Evolution fallaba con `exists: false`.

Convenciones de error (mismo contrato que `evolution_client`):
- 4xx → `WahaAPIError` inmediatamente.
- 5xx → retry con backoff exponencial; tras agotar, `WahaAPIError`.
- Red/timeout → `WahaAPIError(status=-1, ...)`.

La sesión WAHA equivale a la "instancia" Evolution. En `WhatsAppNumber`:
  `evolution_instance_name` → nombre de sesión WAHA (reutilizamos el campo).
  `connection_type` == "waha" → este cliente se activa en el dispatcher.
"""

import logging
import threading
import time
from typing import Any

import httpx

from src.config import get_settings
from src.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


class WahaAPIError(Exception):
    """Error devuelto por WAHA o por la capa de transporte.

    `status=-1` indica error local (config ausente, timeout, fallo DNS).
    Cualquier otro valor es un HTTP status code real.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        body: Any = None,
        url: str = "",
    ) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.body = body
        self.url = url


# ────────────────────────────────────────────────────────────
# Transporte + retry
# ────────────────────────────────────────────────────────────


def _base_url() -> str:
    url = (get_settings().waha_api_url or "").rstrip("/")
    if not url:
        raise WahaAPIError(-1, "WAHA_API_URL no está configurado")
    return url


def _headers() -> dict[str, str]:
    key = get_settings().waha_api_key or ""
    if not key:
        raise WahaAPIError(-1, "WAHA_API_KEY no está configurado")
    return {"X-Api-Key": key, "Content-Type": "application/json"}


def _sleep_for_retry(attempt: int) -> None:
    # 0.5 s → 1 s → 2 s → … cap 5 s.
    time.sleep(min(0.5 * (2 ** attempt), 5.0))


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _short_body(body: Any) -> str:
    s = str(body) if body is not None else ""
    return s[:200] + ("…" if len(s) > 200 else "")


def _extract_error_message(body: Any) -> str | None:
    """Extrae mensaje legible del body de error WAHA.

    WAHA devuelve: `{"statusCode": 422, "error": "Unprocessable Entity",
    "message": "..."}`.
    """
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("message"), str):
        return body["message"]
    if "error" in body:
        return str(body["error"])
    return None


def _request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
) -> Any:
    """Ejecuta una llamada contra WAHA con retry de 5xx.

    Levanta `WahaAPIError` en cualquier falla no recuperable. Devuelve el
    JSON parseado en 2xx (puede ser dict, list, o None para 204).
    """
    url = f"{_base_url()}{path}"
    headers = _headers()
    settings = get_settings()
    timeout = settings.waha_timeout_seconds
    retries = settings.waha_max_retries

    last_network_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            resp = httpx.request(
                method, url, json=json, params=params,
                headers=headers, timeout=timeout,
            )
        except httpx.RequestError as e:
            last_network_error = e
            logger.warning(
                "WAHA %s %s network error (intento %d/%d): %s",
                method, path, attempt + 1, retries + 1, e,
            )
            if attempt < retries:
                _sleep_for_retry(attempt)
                continue
            raise WahaAPIError(-1, f"Network error: {e}", url=url) from e

        status = resp.status_code
        body = _safe_json(resp)

        if 200 <= status < 300:
            return body

        if 500 <= status < 600 and attempt < retries:
            logger.warning(
                "WAHA %s %s → %d (intento %d/%d): %s",
                method, path, status, attempt + 1, retries + 1, _short_body(body),
            )
            _sleep_for_retry(attempt)
            continue

        msg = _extract_error_message(body) or (resp.text or "")[:500] or "sin detalle"
        logger.error("WAHA %s %s → %d: %s", method, path, status, msg)
        raise WahaAPIError(status, msg, body=body, url=url)

    raise WahaAPIError(
        -1, "Retries agotados sin respuesta", url=url
    ) from last_network_error


# ────────────────────────────────────────────────────────────
# Helpers de targeting
# ────────────────────────────────────────────────────────────

# Heurística LID: igual que evolution_client — IDs ≥ 14 dígitos son LIDs.
_LID_MIN_DIGITS = 14


def _to_chat_id(number: str) -> str:
    """Convierte un número normalizado (solo dígitos) al chatId de WAHA.

    WAHA acepta `56941131946@c.us` (chatId) en todos los endpoints de envío.
    """
    return f"{number}@c.us"


def _resolve_target(session_name: str, number: str) -> str:
    """Devuelve el chatId para el número, resolviendo LIDs si es necesario.

    Si el número tiene ≥ 14 dígitos (LID), intenta resolver al PN real vía
    `resolve_lid_to_pn`. Si la resolución falla, levanta `WahaAPIError`
    con `status=-2` — NO se hace fallback a `@lid` como chatId.

    **Por qué no fallback a `@lid`:** en producción (2026-04-23, chat Martín),
    WAHA/WhatsApp reinterpretaron un `chatId=<LID>@lid` como un número
    internacional ficticio (+141368982827245, Canadá) y el mensaje fue
    entregado a un desconocido. Mejor fallar visible que enviar al lugar
    equivocado — un mensaje no enviado es recuperable, uno enviado al
    número equivocado no. Ver `FASE_WA_LID_FIX_A.md`.
    """
    if not number.isdigit() or len(number) < _LID_MIN_DIGITS:
        return _to_chat_id(number)

    lid_jid = f"{number}@lid"
    pn = resolve_lid_to_pn(session_name, lid_jid)
    if pn:
        logger.info("WAHA send: LID %s → PN %s resuelto", number, pn)
        return _to_chat_id(pn)

    # ABORT — nunca enviar a un `@lid` como chatId (ver docstring).
    logger.error(
        "WAHA send: LID %s sin resolver — ABORT para evitar envío a "
        "número ficticio. Pedir al contacto que envíe otro mensaje así "
        "WAHA puede capturar el senderPn en el webhook.",
        number,
    )
    raise WahaAPIError(
        status=-2,
        message=(
            "No se pudo determinar el número real del contacto (LID sin "
            "resolver). Pedí al contacto que envíe otro mensaje y reintentá."
        ),
    )


# ────────────────────────────────────────────────────────────
# Envío de mensajes
# ────────────────────────────────────────────────────────────


def send_text(session_name: str, to: str, text: str) -> dict:
    """Envía texto plano.

    `to` se normaliza a dígitos (56XXXXXXXXX). Si es un LID, se resuelve al
    PN real vía el endpoint nativo `/contacts/lid-pn`.
    """
    number = normalize_phone(to)
    if not number:
        raise WahaAPIError(-1, f"Número inválido: {to!r}")
    chat_id = _resolve_target(session_name, number)
    payload = {"session": session_name, "chatId": chat_id, "text": text}
    return _request("POST", "/api/sendText", json=payload) or {}


def send_media(
    session_name: str,
    to: str,
    media_url: str,
    *,
    caption: str = "",
    mediatype: str = "image",
    file_name: str | None = None,
) -> dict:
    """Envía media por URL pública.

    `mediatype`: 'image' | 'video' | 'document' | 'audio'.
    WAHA separa el endpoint según tipo: sendImage, sendVideo, sendFile, sendAudio.
    """
    number = normalize_phone(to)
    if not number:
        raise WahaAPIError(-1, f"Número inválido: {to!r}")
    chat_id = _resolve_target(session_name, number)

    endpoint, file_key = _media_endpoint_and_key(mediatype)
    payload: dict[str, Any] = {
        "session": session_name,
        "chatId": chat_id,
        file_key: {"url": media_url},
        "caption": caption,
    }
    if file_name and mediatype == "document":
        payload["filename"] = file_name
    return _request("POST", endpoint, json=payload) or {}


def send_media_bytes(
    session_name: str,
    to: str,
    *,
    file_bytes: bytes,
    mime: str,
    mediatype: str = "image",
    caption: str = "",
    file_name: str | None = None,
) -> dict:
    """Envía media desde bytes crudos (base64 inline).

    Compatible con el mismo contrato que `evolution_client.send_media_bytes`.
    """
    import base64

    number = normalize_phone(to)
    if not number:
        raise WahaAPIError(-1, f"Número inválido: {to!r}")
    if not file_bytes:
        raise WahaAPIError(-1, "file_bytes vacío")

    chat_id = _resolve_target(session_name, number)
    # WAHA Plus espera base64 PURO en `file.data` — el prefijo `data:<mime>;base64,`
    # lo decodea como bytes literales y genera archivos corruptos en el cel
    # destino (no abre la imagen). Bug detectado en QA F6: archivos llegaban
    # pero WhatsApp del receptor mostraba "no se pudo abrir".
    b64 = base64.b64encode(file_bytes).decode("ascii")

    endpoint, file_key = _media_endpoint_and_key(mediatype)
    payload: dict[str, Any] = {
        "session": session_name,
        "chatId": chat_id,
        file_key: {
            "data": b64,
            "mimetype": mime or "application/octet-stream",
            "filename": file_name or "upload",
        },
        "caption": caption,
    }
    return _request("POST", endpoint, json=payload) or {}


def _media_endpoint_and_key(mediatype: str) -> tuple[str, str]:
    """Devuelve (endpoint_path, file_field_name) para cada tipo de media."""
    mapping = {
        "image":    ("/api/sendImage", "file"),
        "video":    ("/api/sendVideo", "file"),
        "document": ("/api/sendFile",  "file"),
        "audio":    ("/api/sendAudio", "file"),
        "voice":    ("/api/sendVoice", "file"),
    }
    return mapping.get(mediatype, ("/api/sendFile", "file"))


def _chat_jid_from_waha_message_id(message_id: str | None) -> str:
    """Extrae el remoteJid embebido en un wa_message_id de WAHA/Baileys.

    Formato esperado: ``<fromMe>_<remoteJid>_<hash>``. Ej:
      - ``"false_112322068623427@lid_A51CD43..."``  → ``"112322068623427@lid"``
      - ``"true_56941131946@c.us_ABC"``              → ``"56941131946@c.us"``
      - ``"false_56912345678@s.whatsapp.net_XYZ"``   → ``"56912345678@s.whatsapp.net"``

    Devuelve ``""`` si el formato no coincide (ej. ids de Meta Cloud API
    tipo ``wamid.xxx``, ids legacy, o malformados). En esos casos el caller
    debe caer al fallback: construir el chat_id desde el phone conocido.

    Originalmente introducido en Fase E (PR #137) pensando que el 404 del
    mark-as-read era por mismatch chat_id/remoteJid. Fase F descubrió que
    el root cause real era otro (endpoint inexistente) y que WAHA indexa
    por PN, no por LID — por eso `mark_as_read` ya no usa esta función.
    Queda disponible por si algún otro endpoint sí requiere el JID literal
    del mensaje.
    """
    if not message_id or "_" not in message_id:
        return ""
    try:
        _, rest = message_id.split("_", 1)        # quita "<fromMe>_"
        jid, _hash = rest.rsplit("_", 1)          # quita "_<hash>" del final
    except ValueError:
        return ""
    # Validación: el remoteJid debe tener ``@<domain>`` (lid / c.us /
    # s.whatsapp.net / g.us). Si no, el id no es formato Baileys.
    if "@" not in jid:
        return ""
    return jid


def mark_as_read(
    session_name: str,
    *,
    chat_id: str,
    message_id: str,
    from_me: bool = False,
) -> dict:
    """Envía el read receipt (doble check azul) al sender del mensaje.

    WAHA expone ``POST /api/sendSeen`` como endpoint **global** — la
    session va en el body, NO en el path. Mismo patrón que
    ``sendText``/``startTyping``.

    Por qué NO el batch ``/chats/{chatId}/messages/read``: ese endpoint
    existe pero solo mantiene consistencia del store de WAHA (el GET de
    mensajes reporta ``ack:3, READ``) **sin emitir el receipt al sender
    en NOWEB**. El celular del contacto se queda en doble check gris
    para siempre. Ver ``FASE_WA_SEND_SEEN_H.md``.

    Probes contra WAHA prod (2026-04-24)::

        POST /api/default/chats/{pn}/messages/read  → 201 {ids:[]} (store-only)
        POST /api/sendSeen {session,chatId,messageId} → 201 ✅ (emite receipt)
        POST /api/sendSeen {chatId,messageId}         → 400 "Session name is required"

    ``chat_id`` debe ser el **PN** del contacto (``<digits>@c.us``). El
    ``message_id`` se pasa literal — WAHA matchea el id independientemente
    del addressing (LID embebido en el id de Baileys está bien).
    """
    path = "/api/sendSeen"
    body = {
        "session": session_name,
        "chatId": chat_id,
        "messageId": message_id,
    }
    return _request("POST", path, json=body) or {}


def send_typing(
    session_name: str,
    chat_id: str,
    *,
    typing: bool = True,
) -> dict:
    """Envía o detiene el indicador de escritura ("escribiendo…").

    WAHA expone ``/api/startTyping`` y ``/api/stopTyping`` como endpoints
    **globales** — la session va en el body, NO en el path. El patrón
    ``/api/{session}/startTyping`` NO existe (404 Cannot POST), al igual
    que ``sendText``/``sendImage`` y el resto de endpoints de envío.

    Probe contra WAHA prod (2026-04-24)::

        POST /api/default/startTyping                 → 404
        POST /api/startTyping {session,chatId}        → 201 {"result":true}
        POST /api/startTyping {chatId}                → 400 "Session name is required"

    ``typing=True`` → muestra "escribiendo…"; ``typing=False`` → lo limpia.
    Equivalente a ``evolution_client.send_presence`` pero global. Ver
    ``FASE_WA_TYPING_G.md``.
    """
    action = "startTyping" if typing else "stopTyping"
    path = f"/api/{action}"
    body = {"session": session_name, "chatId": chat_id}
    return _request("POST", path, json=body) or {}


def send_presence(
    session_name: str,
    to: str,
    *,
    presence: str = "composing",
    delay_ms: int = 1200,  # noqa: ARG001 — ignorado; WAHA maneja el TTL
) -> dict:
    """Alias de `send_typing` con el mismo contrato que evolution_client.

    `presence = "composing"` → typing=True. Cualquier otro valor → typing=False.
    `delay_ms` se ignora: WAHA detiene el indicador cuando llamás stopTyping.
    """
    number = normalize_phone(to)
    if not number:
        raise WahaAPIError(-1, f"Número inválido: {to!r}")
    typing = presence in ("composing", "recording")
    return send_typing(session_name, _to_chat_id(number), typing=typing)


def fetch_media_url_for_message(
    session_name: str, chat_id: str, message_id: str
) -> str | None:
    """Pide a WAHA la `mediaUrl` de un mensaje específico, on-demand.

    WAHA Plus NOWEB no incluye `mediaUrl` en el webhook payload por
    default — solo aparece cuando el endpoint REST `/api/{session}/chats/
    {chat_id}/messages` se invoca con `?downloadMedia=true`. Este helper
    hace ese GET y devuelve la URL interna de WAHA (`http://localhost:3000/
    api/files/.../<id>.<ext>`) que después podemos descargar con
    `fetch_media_bytes_by_url`.

    Hotfix de Fase MEDIA 3: persistimos `WhatsAppMessage.media_url` vacío
    durante la ingestion (zero-storage), pero el proxy on-demand necesita
    la URL real para servir bytes al frontend. Esta función la resuelve
    en el momento del request del operador.

    Args:
        session_name: nombre de sesión WAHA (ej. "default").
        chat_id:      `<phone>@c.us` o `<lid>@lid` del chat.
        message_id:   id WAHA del mensaje (`false_<jid>_<rest>` típicamente).

    Returns:
        URL string accesible con `fetch_media_bytes_by_url`, o None si
        WAHA no encuentra el mensaje o no tiene media.
    """
    if not session_name or not chat_id or not message_id:
        return None
    try:
        path = f"/api/{session_name}/chats/{chat_id}/messages"
        resp = httpx.get(
            f"{_base_url()}{path}",
            params={"limit": 100, "downloadMedia": "true"},
            headers=_headers(),
            timeout=get_settings().waha_timeout_seconds,
        )
        if resp.status_code != 200:
            logger.warning(
                "WAHA fetch_media_url: HTTP %d para chat=%s msg=%s",
                resp.status_code, chat_id, message_id,
            )
            return None
        items = resp.json() if resp.content else []
        if not isinstance(items, list):
            return None
        # WAHA expone IDs en 2 formatos según dirección:
        #   inbound:  "false_<jid>_<keyid>"  (e.g. "false_<lid>@lid_2A0F...")
        #   outbound: "true_<chatid>_<keyid>" (e.g. "true_<phone>@c.us_3EB0...")
        # Nosotros persistimos a veces el id completo y otras solo el keyid.
        # Matcheamos por igualdad o por sufijo `_<keyid>` para cubrir ambos.
        for m in items:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "")
            if mid == message_id or mid.endswith("_" + message_id):
                media = m.get("media") or {}
                if isinstance(media, dict):
                    url = media.get("url") or ""
                    if url:
                        return _rewrite_waha_internal_url(str(url))
        logger.info(
            "WAHA fetch_media_url: msg %s no encontrado en chat %s (limit=100)",
            message_id, chat_id,
        )
        return None
    except httpx.RequestError as e:
        logger.warning("WAHA fetch_media_url: network error: %s", e)
        return None


def fetch_contact_profile_picture(
    session_name: str, contact_id: str
) -> str | None:
    """Devuelve la URL de la foto de perfil de un contacto WhatsApp.

    WAHA Plus expone `GET /api/contacts/profile-picture?session=&contactId=`
    que devuelve `{"profilePictureURL": "..."}` apuntando a `pps.whatsapp.net`.
    La URL es estable durante semanas/meses; refresh ocasional cubre casos
    de cambio.

    Args:
        session_name: nombre de sesión WAHA.
        contact_id: chatId tipo `<phone>@c.us` o `<lid>@lid`.

    Returns:
        URL string, o None si el contacto no tiene foto, no existe, o WAHA
        falla. Failure-mode silencioso (no propaga, no bloquea ingestión).
    """
    if not session_name or not contact_id:
        return None
    try:
        resp = httpx.get(
            f"{_base_url()}/api/contacts/profile-picture",
            params={"session": session_name, "contactId": contact_id},
            headers=_headers(),
            timeout=get_settings().waha_timeout_seconds,
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            return None
        url = data.get("profilePictureURL") or ""
        return str(url) if url else None
    except Exception as e:
        logger.debug("WAHA fetch_contact_profile_picture failed: %s", e)
        return None


def _rewrite_waha_internal_url(url: str) -> str:
    """WAHA Plus expone mediaUrl como `http://localhost:3000/api/files/...`
    (URL interna del container WAHA). Desde nuestro container la conexión
    rompe con `Connection refused`. Sustituimos al base URL público (env
    `WAHA_API_URL`) para que sea reachable.
    """
    if not url:
        return url
    base = (get_settings().waha_api_url or "").rstrip("/")
    if not base:
        return url
    for prefix in ("http://localhost:3000", "http://127.0.0.1:3000"):
        if url.startswith(prefix):
            return base + url[len(prefix):]
    return url


def fetch_media_bytes_by_url(media_url: str) -> tuple[bytes, str, str] | None:
    """Descarga binario desde una `mediaUrl` WAHA con `X-Api-Key`.

    Variante stateless de `get_media_bytes` para el proxy on-demand de Fase
    MEDIA 3: el endpoint `/whatsapp/messages/{id}/media` solo conoce la
    `media_url` persistida en `WhatsAppMessage.media_url` (no el payload
    completo del webhook).

    Devuelve `(bytes, mime, filename)` o `None` si falla.
    """
    if not media_url:
        return None
    try:
        resp = httpx.get(media_url, headers=_headers(), timeout=get_settings().waha_timeout_seconds)
        if resp.status_code != 200:
            logger.warning("WAHA fetch_media_bytes_by_url: HTTP %d para %s", resp.status_code, media_url)
            return None
    except httpx.RequestError as e:
        logger.warning("WAHA fetch_media_bytes_by_url: network error: %s", e)
        return None

    mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    cd = resp.headers.get("content-disposition", "")
    filename = ""
    if "filename=" in cd:
        filename = cd.split("filename=")[-1].strip(' "\'')
    if not filename:
        filename = media_url.split("/")[-1].split("?")[0] or "media"
    return resp.content, mime, filename


def get_media_bytes(
    session_name: str, raw_message_payload: dict
) -> tuple[bytes, str, str] | None:
    """Descarga media inbound de WAHA.

    WAHA Plus entrega `mediaUrl` directamente descargable (no encriptado como
    Evolution). Core también lo expone en el payload del webhook. Descargamos
    vía GET estándar con la apikey en el header.

    Args:
        session_name: nombre de sesión WAHA.
        raw_message_payload: el dict `payload` crudo del webhook `message`.

    Returns:
        `(bytes, mime, filename)` o `None` si no hay media / falla la descarga.
    """
    if not isinstance(raw_message_payload, dict):
        return None
    media_url = raw_message_payload.get("mediaUrl") or ""
    return fetch_media_bytes_by_url(media_url)


# ────────────────────────────────────────────────────────────
# Gestión de sesiones (equivalente a instancias Evolution)
# ────────────────────────────────────────────────────────────


def get_session_status(session_name: str) -> dict:
    """Devuelve el status actual de la sesión WAHA."""
    return _request("GET", f"/api/sessions/{session_name}") or {}


def request_pairing_code(session_name: str, phone_number: str) -> str:
    """Pide un pairing code de 8 chars para vincular el número.

    `phone_number` debe ser solo dígitos sin `+` (ej: "56954051554").
    Devuelve el código como string (ej: "AVPD-WEGF").
    """
    resp = _request(
        "POST",
        f"/api/{session_name}/auth/request-code",
        json={"phoneNumber": phone_number},
    )
    if not isinstance(resp, dict):
        raise WahaAPIError(-1, "request_pairing_code: respuesta inesperada")
    code = resp.get("code") or ""
    if not code:
        raise WahaAPIError(-1, "request_pairing_code: sin código en respuesta")
    return code


def start_session(session_name: str) -> dict:
    """Inicia una sesión detenida."""
    return _request("POST", f"/api/sessions/{session_name}/start") or {}


def stop_session(session_name: str) -> dict:
    """Detiene una sesión activa."""
    return _request("POST", f"/api/sessions/{session_name}/stop") or {}


def logout_session(session_name: str) -> dict:
    """Cierra sesión y limpia credenciales (el chip queda desvinculado)."""
    return _request("POST", f"/api/sessions/{session_name}/logout") or {}


def list_sessions() -> list[dict]:
    """Lista todas las sesiones activas en WAHA."""
    data = _request("GET", "/api/sessions")
    if isinstance(data, list):
        return data
    return []


# ────────────────────────────────────────────────────────────
# Resolución LID → PN (endpoint nativo WAHA)
# ────────────────────────────────────────────────────────────

# Cache in-memory: LID es un identificador estable, cachearlo de por vida
# del proceso es seguro y evita queries repetidas al endpoint WAHA.
_LID_TO_PN_CACHE: dict[str, str] = {}
_LID_CACHE_LOCK = threading.Lock()


def _clear_lid_cache() -> None:
    """Solo para tests."""
    with _LID_CACHE_LOCK:
        _LID_TO_PN_CACHE.clear()


def _preload_lid_pn_cache(wa_numbers: list) -> None:
    """Pre-carga el caché LID→PN consultando cada conversación activa en WAHA.

    Workaround para el bug de WAHA donde GET /contacts/lid-pn devuelve vacío
    incluso cuando el store sí tiene el dato (campo `lid` en el contacto).
    Por cada número activo, consultamos las conversaciones abiertas, obtenemos
    su phone y llamamos GET /contacts/{phone}@c.us — si la respuesta incluye
    `lid`, registramos el mapeo inverso en `_LID_TO_PN_CACHE`.
    """
    from src.db.session import SessionLocal
    from src.db.tenant_context import bypass_tenant_filter

    if not wa_numbers:
        return

    db = SessionLocal()
    try:
        with bypass_tenant_filter():
            open_phones = db.execute(
                __import__("sqlalchemy").text(
                    "SELECT DISTINCT wa_contact_phone, "
                    "(SELECT evolution_instance_name FROM whatsapp_numbers wn "
                    " WHERE wn.id = c.wa_number_id LIMIT 1) AS session_name "
                    "FROM whatsapp_conversations c "
                    "WHERE status != 'closed' AND wa_contact_phone IS NOT NULL"
                )
            ).fetchall()
    except Exception as e:
        logger.warning("_preload_lid_pn_cache: error leyendo convs: %s", e)
        return
    finally:
        db.close()

    loaded = 0
    for row in open_phones:
        phone = row[0] or ""
        session_name = row[1] or ""
        if not phone.isdigit() or len(phone) < 7 or not session_name:
            continue
        if phone.isdigit() and len(phone) >= 14:
            continue  # ya es un LID, no tiene PN
        try:
            resp = _request("GET", f"/api/{session_name}/contacts/{phone}@c.us")
            if isinstance(resp, dict):
                lid_jid = resp.get("lid") or ""
                if "@lid" in lid_jid:
                    with _LID_CACHE_LOCK:
                        _LID_TO_PN_CACHE[lid_jid] = phone
                    loaded += 1
        except WahaAPIError:
            pass
    if loaded:
        logger.info("_preload_lid_pn_cache: %d LID→PN mapeos cargados en caché", loaded)


def ensure_waha_webhooks() -> None:
    """Aplica webhooks en todas las sesiones WAHA activas al arranque del backend.

    WAHA Plus persiste las credenciales de auth en el store (.sessions volume)
    pero NO la configuración de webhooks. Esta función los re-aplica en cada
    restart del backend para que nunca queden sesiones sin webhook.

    Idempotente: una segunda llamada sobreescribe con los mismos valores.
    """
    settings = get_settings()

    if not settings.waha_api_url:
        logger.debug("ensure_waha_webhooks: WAHA_API_URL no configurado — omitiendo")
        return

    base_url = (getattr(settings, "public_base_url", "") or "").rstrip("/")
    if not base_url:
        logger.warning("ensure_waha_webhooks: public_base_url vacío — omitiendo")
        return

    webhook_url = f"{base_url}/webhook/waha"
    webhook_token = (settings.waha_webhook_token or "").strip()

    from src.db.session import SessionLocal
    from src.db.models import WhatsAppNumber
    from src.db.tenant_context import bypass_tenant_filter

    db = SessionLocal()
    try:
        with bypass_tenant_filter():
            wa_numbers = (
                db.query(WhatsAppNumber)
                .filter(
                    WhatsAppNumber.connection_type == "waha",
                    WhatsAppNumber.active.is_(True),
                    WhatsAppNumber.evolution_instance_name.isnot(None),
                )
                .all()
            )
        session_names = [
            n.evolution_instance_name
            for n in wa_numbers
            if n.evolution_instance_name
        ]
    finally:
        db.close()

    if not session_names:
        logger.debug("ensure_waha_webhooks: sin sesiones WAHA activas en DB")
        return

    webhook_entry: dict = {
        "url": webhook_url,
        # ``message.ack`` es obligatorio para que WAHA emita los receipts de
        # delivery/read y nuestro handler (_dispatch_message_ack, Fase J) pinte
        # ✓✓ y ✓✓ azul en el Inbox. Ver FASE_WA_HOOK_ACK_SUBSCRIBE_L.md —
        # borrarlo deja la Fase J inerte en prod aunque el código esté bien.
        "events": ["message", "message.any", "message.ack", "session.status"],
    }
    if webhook_token:
        webhook_entry["customHeaders"] = [
            {"name": "X-WAHA-Token", "value": webhook_token}
        ]

    for session_name in session_names:
        try:
            _request(
                "PUT",
                f"/api/sessions/{session_name}",
                json={
                    "config": {
                        "noweb": {"store": {"enabled": True, "fullSync": True}},
                        "webhooks": [webhook_entry],
                    }
                },
            )
            logger.info(
                "ensure_waha_webhooks: sesión %s → webhooks restaurados (url=%s)",
                session_name, webhook_url,
            )
        except WahaAPIError as e:
            logger.warning(
                "ensure_waha_webhooks: sesión %s → error %s (WAHA puede estar caído)",
                session_name, e,
            )

    # Pre-cargar caché LID→PN desde los contactos activos en la BD.
    # Workaround para el bug de WAHA donde /contacts/lid-pn no hace el
    # lookup inverso aunque el store sí tiene el dato (contact.lid).
    _preload_lid_pn_cache(wa_numbers)


def resolve_lid_to_pn(session_name: str, lid_jid: str) -> str | None:
    """Resuelve un `@lid` al phone number real usando el endpoint nativo de WAHA.

    Tres intentos en orden de costo creciente:
    1. Cache in-memory (O(1))
    2. Endpoint dedicado `/contacts/lid-pn` (rápido, requiere store poblado)
    3. GET /contacts/{lid_jid} — índice por LID en el store
    4. Escaneo de lista completa de contactos buscando `lid == lid_jid`

    Args:
        session_name: nombre de sesión WAHA.
        lid_jid: LID con formato `148726328881285@lid`.

    Returns:
        Phone number en formato puro (solo dígitos, ej: "56941131946") o `None`
        si el LID no está en la DB local de WAHA todavía.
    """
    lid_jid = (lid_jid or "").strip()
    if "@lid" not in lid_jid or not session_name:
        return None

    with _LID_CACHE_LOCK:
        cached = _LID_TO_PN_CACHE.get(lid_jid)
    if cached:
        return cached

    # Intento 1: endpoint dedicado /contacts/lid-pn (requiere fullSync en el store).
    try:
        resp = _request(
            "GET",
            f"/api/{session_name}/contacts/lid-pn",
            params={"lid": lid_jid},
        )
        pn_raw = ""
        if isinstance(resp, dict):
            pn_raw = resp.get("PN") or resp.get("phoneNumber") or resp.get("pn") or ""
        elif isinstance(resp, str):
            pn_raw = resp
        pn = pn_raw.split("@")[0].strip() if pn_raw else ""
        if pn and pn.isdigit() and len(pn) >= 7:
            with _LID_CACHE_LOCK:
                _LID_TO_PN_CACHE[lid_jid] = pn
            logger.info("WAHA resolve_lid_to_pn (lid-pn): %s → %s", lid_jid, pn)
            return pn
    except WahaAPIError as e:
        logger.debug("WAHA resolve_lid_to_pn lid-pn endpoint: %s → %s", lid_jid, e)

    # Intento 2: /contacts/{lid_jid} — el store puede tener el contacto indexado
    # por LID con su id real en @c.us. También revisamos campos alternativos (pn, number).
    try:
        resp2 = _request("GET", f"/api/{session_name}/contacts/{lid_jid}")
        if isinstance(resp2, dict):
            cid = resp2.get("id") or resp2.get("jid") or ""
            # Si id contiene @c.us o @s.whatsapp.net es el PN real
            if "@c.us" in cid or "@s.whatsapp.net" in cid:
                pn2 = cid.split("@")[0].strip()
            else:
                pn2 = (resp2.get("pn") or resp2.get("number") or "").split("@")[0].strip()
            if pn2 and pn2.isdigit() and len(pn2) >= 7 and len(pn2) < 14:
                with _LID_CACHE_LOCK:
                    _LID_TO_PN_CACHE[lid_jid] = pn2
                logger.info("WAHA resolve_lid_to_pn (contacts/{lid}): %s → %s", lid_jid, pn2)
                return pn2
    except WahaAPIError as e2:
        logger.debug("WAHA resolve_lid_to_pn contacts fallback: %s → %s", lid_jid, e2)

    # Intento 3: escanear lista completa de contactos buscando el que tiene `lid == lid_jid`
    # con su id en formato @c.us. Este es el método más costoso pero cubre el caso de
    # contactos que WAHA sincronizó vía fullSync con ambos campos (id + lid).
    try:
        contacts = _request("GET", f"/api/{session_name}/contacts")
        if isinstance(contacts, list):
            for contact in contacts:
                clid = contact.get("lid") or ""
                if clid != lid_jid:
                    continue
                cid = contact.get("id") or ""
                if "@c.us" in cid or "@s.whatsapp.net" in cid:
                    pn3 = cid.split("@")[0].strip()
                    if pn3 and pn3.isdigit() and len(pn3) >= 7 and len(pn3) < 14:
                        with _LID_CACHE_LOCK:
                            _LID_TO_PN_CACHE[lid_jid] = pn3
                        logger.info(
                            "WAHA resolve_lid_to_pn (contacts scan): %s → %s", lid_jid, pn3
                        )
                        return pn3
    except WahaAPIError as e3:
        logger.debug("WAHA resolve_lid_to_pn contacts scan: %s → %s", lid_jid, e3)

    logger.info("WAHA resolve_lid_to_pn: sin PN para %s", lid_jid)
    return None
