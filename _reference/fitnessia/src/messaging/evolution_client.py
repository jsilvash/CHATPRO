"""Cliente HTTP para Evolution API (WhatsApp QR self-hosted).

Envoltura thin sobre la REST API de Evolution v2.x. Usada desde:

- `src/api/routes/wa_qr_onboarding.py` (Fase 5) para crear/conectar instancias
  y devolver el QR a la UI admin.
- `src/messaging/dispatcher.py` (Fase 4) para rutear envíos a números con
  `connection_type == "qr"`.

Convenciones de error distintas al resto de `src/messaging/`:

- 4xx → `EvolutionAPIError` (el admin UI necesita ver el detalle del fallo;
  swallow-to-None pierde información accionable).
- 5xx → retry con backoff exponencial hasta `settings.evolution_max_retries`;
  tras agotar, `EvolutionAPIError`.
- Errores de red / timeout → `EvolutionAPIError(status=-1, ...)`.
"""

import logging
import threading
import time
from typing import Any

import httpx

from src.config import get_settings
from src.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

# Eventos que Evolution envía al webhook global por default al crear una
# instancia. SYNEX necesita al menos estos cuatro para el pipeline del plan.
DEFAULT_EVENTS = [
    "MESSAGES_UPSERT",
    "MESSAGES_UPDATE",
    "CONNECTION_UPDATE",
    "QRCODE_UPDATED",
    "SEND_MESSAGE",
]


class EvolutionAPIError(Exception):
    """Error devuelto por Evolution API o por la capa de transporte.

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
    url = (get_settings().evolution_api_url or "").rstrip("/")
    if not url:
        raise EvolutionAPIError(-1, "EVOLUTION_API_URL no está configurado")
    return url


def _headers() -> dict[str, str]:
    key = get_settings().evolution_api_key or ""
    if not key:
        raise EvolutionAPIError(-1, "EVOLUTION_API_KEY no está configurado")
    return {"apikey": key, "Content-Type": "application/json"}


def _sleep_for_retry(attempt: int) -> None:
    # 0.5s, 1s, 2s, 4s… cap en 5s.
    time.sleep(min(0.5 * (2 ** attempt), 5.0))


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _short_body(body: Any) -> str:
    if body is None:
        return ""
    s = str(body)
    return s[:200] + ("…" if len(s) > 200 else "")


def _extract_error_message(body: Any) -> str | None:
    """Extrae el mensaje humano desde los shapes comunes de Evolution.

    Evolution v2.x mezcla dos formatos:
    - `{"status": 404, "error": "Not Found", "response": {"message": [...]}}`
    - `{"message": "..."}` (middleware de auth o validación)
    """
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("message"), str):
        return body["message"]
    response = body.get("response")
    if isinstance(response, dict):
        msg = response.get("message")
        if isinstance(msg, list) and msg:
            return "; ".join(str(x) for x in msg)
        if isinstance(msg, str):
            return msg
    if "error" in body:
        return str(body["error"])
    return None


def _request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> Any:
    """Ejecuta una llamada contra Evolution API con retry de 5xx.

    Levanta `EvolutionAPIError` en cualquier falla no recuperable (4xx, 5xx
    tras agotar retries, errores de red). Devuelve el JSON parseado en 2xx.
    """
    url = f"{_base_url()}{path}"
    headers = _headers()
    settings = get_settings()
    timeout = settings.evolution_timeout_seconds
    retries = settings.evolution_max_retries

    last_network_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            resp = httpx.request(
                method, url, json=json, headers=headers, timeout=timeout
            )
        except httpx.RequestError as e:
            last_network_error = e
            logger.warning(
                "Evolution %s %s network error (intento %d/%d): %s",
                method, path, attempt + 1, retries + 1, e,
            )
            if attempt < retries:
                _sleep_for_retry(attempt)
                continue
            raise EvolutionAPIError(-1, f"Network error: {e}", url=url) from e

        status = resp.status_code
        body = _safe_json(resp)

        if 200 <= status < 300:
            return body

        if 500 <= status < 600 and attempt < retries:
            logger.warning(
                "Evolution %s %s → %d (intento %d/%d): %s",
                method, path, status, attempt + 1, retries + 1, _short_body(body),
            )
            _sleep_for_retry(attempt)
            continue

        # 4xx, o 5xx con retries agotados.
        msg = _extract_error_message(body) or (resp.text or "")[:500] or "sin detalle"
        logger.error("Evolution %s %s → %d: %s", method, path, status, msg)
        raise EvolutionAPIError(status, msg, body=body, url=url)

    # Unreachable en teoría; el loop siempre retorna o raisea.
    raise EvolutionAPIError(
        -1, "Retries agotados sin respuesta", url=url
    ) from last_network_error


# ────────────────────────────────────────────────────────────
# Gestión de instancias
# ────────────────────────────────────────────────────────────


def create_instance(
    instance_name: str,
    *,
    webhook_url: str | None = None,
    events: list[str] | None = None,
    integration: str = "WHATSAPP-BAILEYS",
) -> dict:
    """Crea una instancia nueva.

    El response incluye `instance` (con `instanceName`, `instanceId`, `status`),
    `hash` (apikey per-instancia que Evolution genera) y la config de webhook.
    """
    payload: dict[str, Any] = {
        "instanceName": instance_name,
        "qrcode": True,
        "integration": integration,
    }
    if webhook_url:
        payload["webhook"] = {
            "url": webhook_url,
            "byEvents": False,
            "base64": True,
            "events": events or DEFAULT_EVENTS,
        }
    return _request("POST", "/instance/create", json=payload)


def connect_instance(instance_name: str) -> dict:
    """Devuelve el QR para vincular la instancia.

    El response contiene `pairingCode`, `code` (QR como string) y `base64`
    (imagen PNG para renderizar en la UI). El QR expira según Evolution
    (típicamente 45-60s); el cliente debe re-llamar para refrescar.
    """
    return _request("GET", f"/instance/connect/{instance_name}")


def fetch_instance_status(instance_name: str | None = None) -> list[dict]:
    """Devuelve la lista de instancias. Si `instance_name` está, filtra a esa."""
    path = "/instance/fetchInstances"
    if instance_name:
        path += f"?instanceName={instance_name}"
    data = _request("GET", path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def logout_instance(instance_name: str) -> dict:
    """Cierra la sesión (el QR pasa a inválido). La instancia sobrevive."""
    return _request("DELETE", f"/instance/logout/{instance_name}")


def delete_instance(instance_name: str) -> dict:
    """Elimina completamente la instancia (no recuperable)."""
    return _request("DELETE", f"/instance/delete/{instance_name}")


# ────────────────────────────────────────────────────────────
# Envío de mensajes
# ────────────────────────────────────────────────────────────


# Heurística LID: WhatsApp LIDs son identificadores locales típicamente de
# ≥14 dígitos (ej: 148726328881285). Los phone numbers reales con country
# code son 10-13 dígitos (máx: Brasil móvil 55XX9XXXXXXXX = 13). Si un
# `to` llega con ≥14 dígitos, casi seguro es un LID que quedó persistido
# en el modelo Client porque `resolve_lid_to_pn` falló en el webhook.
_LID_MIN_DIGITS = 14


def _resolve_target_number(instance_name: str, number: str) -> str:
    """Convierte un número en el string que Evolution debe recibir en `number`.

    Para phones normales (<14 dígitos) → devuelve el mismo número.
    Para LIDs (≥14 dígitos) → intenta `resolve_lid_to_pn` y retorna el PN.

    Si el LID no se resuelve, **raisea** `EvolutionAPIError(-1)`. Evolution
    v2.2.3 rechaza tanto `<lid>@s.whatsapp.net` como `<lid>@lid` con `400
    exists: false` (verificado en prod 2026-04-22: Ana María, La Araucana
    Talca), así que enviar el LID en cualquier forma es inútil y solo
    genera ruido en logs. Fallar temprano le da al caller la chance de
    mostrar un error accionable en la UI.
    """
    if not number.isdigit() or len(number) < _LID_MIN_DIGITS:
        return number
    lid_jid = f"{number}@lid"
    pn = resolve_lid_to_pn(instance_name, lid_jid)
    if pn:
        logger.info("send: LID %s → PN %s resuelto", number, pn)
        return pn
    raise EvolutionAPIError(
        -1,
        f"LID {number} no resoluble a phone number real. Evolution no expone "
        f"el PN del contacto — pedile al cliente que envíe un mensaje nuevo "
        f"para re-sincronizar, o contactalo por otro canal.",
    )


def send_text(instance_name: str, to: str, text: str) -> dict:
    """Envía texto plano. `to` se normaliza a formato internacional (56XXXXXXXXX).

    Si el número es un LID intenta resolverlo a PN real. Si no puede,
    levanta `EvolutionAPIError` antes de hacer HTTP (Evolution v2.2.3 no
    acepta LIDs como destino en ningún formato).
    """
    number = normalize_phone(to)
    if not number:
        raise EvolutionAPIError(-1, f"Número inválido: {to!r}")
    target = _resolve_target_number(instance_name, number)
    payload = {"number": target, "text": text}
    return _request("POST", f"/message/sendText/{instance_name}", json=payload)


def send_media(
    instance_name: str,
    to: str,
    media_url: str,
    *,
    caption: str = "",
    mediatype: str = "image",
    file_name: str | None = None,
) -> dict:
    """Envía media por URL. `mediatype`: 'image' | 'video' | 'document' | 'audio'."""
    number = normalize_phone(to)
    if not number:
        raise EvolutionAPIError(-1, f"Número inválido: {to!r}")
    target = _resolve_target_number(instance_name, number)
    payload: dict[str, Any] = {
        "number": target,
        "mediatype": mediatype,
        "media": media_url,
        "caption": caption,
    }
    if file_name:
        payload["fileName"] = file_name
    return _request("POST", f"/message/sendMedia/{instance_name}", json=payload)


def send_media_bytes(
    instance_name: str,
    to: str,
    *,
    file_bytes: bytes,
    mime: str,
    mediatype: str = "image",
    caption: str = "",
    file_name: str | None = None,
) -> dict:
    """Envía media desde bytes crudos (base64 inline).

    Evolution acepta en el campo `media` tanto una URL como un string
    base64 (con o sin prefijo `data:`). Para outbound del agente — donde
    el archivo llega multipart desde el browser y no está hosteado en
    ningún CDN — es el path más simple. `mediatype`: image/video/document/audio.
    """
    import base64

    number = normalize_phone(to)
    if not number:
        raise EvolutionAPIError(-1, f"Número inválido: {to!r}")
    if not file_bytes:
        raise EvolutionAPIError(-1, "file_bytes vacío")
    target = _resolve_target_number(instance_name, number)
    b64 = base64.b64encode(file_bytes).decode("ascii")
    payload: dict[str, Any] = {
        "number": target,
        "mediatype": mediatype,
        "media": b64,
        "mimetype": mime or "application/octet-stream",
        "caption": caption,
    }
    if file_name:
        payload["fileName"] = file_name
    return _request("POST", f"/message/sendMedia/{instance_name}", json=payload)


def mark_as_read(
    instance_name: str,
    *,
    remote_jid: str,
    message_id: str,
    from_me: bool = False,
) -> dict:
    """Marca un mensaje inbound como leído (double check azul)."""
    payload = {
        "readMessages": [
            {"remoteJid": remote_jid, "fromMe": from_me, "id": message_id}
        ]
    }
    return _request("POST", f"/chat/markMessageAsRead/{instance_name}", json=payload)


def get_media_bytes(
    instance_name: str, raw_message_payload: dict
) -> tuple[bytes, str, str] | None:
    """Descarga y decodifica un media inbound recibido via QR.

    Evolution empaqueta el media cifrado con `mediaKey` en el propio evento
    `messages.upsert`. El `.url` del `imageMessage`/`audioMessage`/etc. NO
    es descargable directo (viene encriptado). Hay que pasarle el payload
    completo (con `key`, `message` y todos los campos de mediaKey/iv) al
    endpoint `/chat/getBase64FromMediaMessage/{instance}`, que decripta y
    devuelve `base64`.

    Args:
        instance_name: nombre de la instancia Evolution.
        raw_message_payload: el dict `data` crudo del webhook
            `messages.upsert` (debe incluir `key`, `message`, `messageType`).

    Returns:
        `(bytes, mime, filename)` si se pudo decodificar; `None` si falló
        (Evolution timeout, instancia desconectada, payload corrupto…).
        El caller debe degradar la UI a un link genérico sin rompr.
    """
    import base64

    if not isinstance(raw_message_payload, dict) or not instance_name:
        return None
    try:
        resp = _request(
            "POST",
            f"/chat/getBase64FromMediaMessage/{instance_name}",
            json={"message": raw_message_payload, "convertToMp4": False},
        )
    except EvolutionAPIError as e:
        logger.warning("get_media_bytes: Evolution rechazó descarga: %s", e)
        return None
    if not isinstance(resp, dict):
        return None
    b64 = resp.get("base64") or ""
    mime = (resp.get("mimetype") or resp.get("mediatype") or "").strip()
    filename = (resp.get("fileName") or resp.get("filename") or "").strip()
    if not b64 or not isinstance(b64, str):
        return None
    try:
        data = base64.b64decode(b64)
    except Exception as e:
        logger.warning("get_media_bytes: base64 inválido: %s", e)
        return None
    # Defaults razonables por si Evolution no pobló mimetype.
    if not mime:
        mime = "application/octet-stream"
    return data, mime, filename


def send_presence(
    instance_name: str,
    to: str,
    *,
    presence: str = "composing",
    delay_ms: int = 1200,
) -> dict:
    """Envía un evento de presencia (typing/recording) al contacto.

    `presence`:
      - `composing` → "escribiendo…"
      - `recording` → "grabando audio…"
      - `paused`    → limpia el indicador
      - `available` / `unavailable` → online/offline del propio QR

    Evolution v2.2.3: endpoint `POST /chat/sendPresence/{instance}`. El campo
    `delay` es obligatorio (schema valida `required: ["delay"]`) y define por
    cuántos ms el contacto verá el indicador antes de que Evolution lo limpie
    automáticamente. Repetimos el envío con cada keystroke del agente, así
    ~1200ms es suficiente buffer entre pulsaciones.
    """
    number = normalize_phone(to)
    if not number:
        raise EvolutionAPIError(-1, f"Número inválido: {to!r}")
    payload: dict[str, Any] = {
        "number": number,
        "presence": presence,
        "delay": max(0, int(delay_ms)),
    }
    return _request("POST", f"/chat/sendPresence/{instance_name}", json=payload)


# ────────────────────────────────────────────────────────────
# Resolución LID → PN (WhatsApp LID addressing workaround)
# ────────────────────────────────────────────────────────────

# Cache in-memory del mapping LID → PN. Un LID en WhatsApp siempre apunta al
# mismo PN (es un identificador estable por cuenta), así que cachearlo de por
# vida del proceso es seguro. Evita re-golpear `/chat/findChats` y previene
# que un mismo contacto genere conversación duplicada si un mensaje posterior
# llega antes de que el retry termine.
_LID_TO_PN_CACHE: dict[str, str] = {}
_LID_CACHE_LOCK = threading.Lock()

# Retry inline al resolver un LID por primera vez. Evolution v2.2.3 sincroniza
# los contacts `@lid` y `@s.whatsapp.net` con un desfase de hasta ~5s después
# del primer mensaje. QA detectó (2026-04-22) que la resolución fallaba en el
# primer intento pero funcionaba 8s después → duplicaba la conversación. Con
# 2 retries de 2.5s cubrimos ese desfase sin bloquear demasiado el webhook.
_LID_RESOLVE_RETRIES = 2
_LID_RESOLVE_BACKOFF = 2.5


def _clear_lid_cache() -> None:
    """Solo para tests."""
    with _LID_CACHE_LOCK:
        _LID_TO_PN_CACHE.clear()


def _collect_pn_matches(chats: list, predicate) -> list[str]:
    """Itera `chats` y devuelve los phones `@s.whatsapp.net` que cumplen `predicate(chat)`."""
    out: list[str] = []
    for c in chats:
        if not isinstance(c, dict):
            continue
        jid = c.get("remoteJid", "") or ""
        if not jid.endswith("@s.whatsapp.net"):
            continue
        if not predicate(c):
            continue
        phone = jid.split("@", 1)[0]
        if phone.isdigit() and len(phone) >= 7:
            out.append(phone)
    return out


def _resolve_lid_to_pn_once(instance_name: str, lid_jid: str) -> str | None:
    """Una pasada de resolución (sin retry ni cache). Retornar None es esperado
    en el primer mensaje mientras Evolution sincroniza contacts.

    Estrategia:
    1. Match por `profilePicUrl` (más específico) — dos contactos @lid y
       @s.whatsapp.net comparten la misma foto si corresponden al mismo usuario.
    2. Fallback por `pushName` — si el LID no tiene foto (caso común), Evolution
       suele propagar `pushName` en ambos contacts. Menos específico: si 2
       personas comparten nombre no podemos desambiguar y retornamos None.
    """
    try:
        chats = _request(
            "POST", f"/chat/findChats/{instance_name}", json={}
        )
    except EvolutionAPIError as e:
        logger.warning("resolve_lid_to_pn: findChats falló: %s", e)
        return None
    if not isinstance(chats, list):
        return None

    lid_contact: dict | None = None
    for c in chats:
        if isinstance(c, dict) and c.get("remoteJid") == lid_jid:
            lid_contact = c
            break
    if not lid_contact:
        return None

    lid_pic = (lid_contact.get("profilePicUrl") or "").strip()
    if lid_pic:
        matches = _collect_pn_matches(
            chats, lambda c: (c.get("profilePicUrl") or "").strip() == lid_pic
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "resolve_lid_to_pn: múltiples contactos comparten profilePicUrl "
                "con %s: %s — no se puede desambiguar",
                lid_jid, matches,
            )
            return None

    lid_name = (lid_contact.get("pushName") or lid_contact.get("name") or "").strip()
    if lid_name:
        matches = _collect_pn_matches(
            chats,
            lambda c: (
                (c.get("pushName") or c.get("name") or "").strip() == lid_name
            ),
        )
        if len(matches) == 1:
            logger.info(
                "resolve_lid_to_pn: %s resuelto por pushName=%r (sin foto)",
                lid_jid, lid_name,
            )
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "resolve_lid_to_pn: múltiples contactos con pushName=%r "
                "matchean con %s: %s — no se puede desambiguar",
                lid_name, lid_jid, matches,
            )
    return None


def resolve_lid_to_pn(instance_name: str, lid_jid: str) -> str | None:
    """Resuelve un `@lid` al phone number (`@s.whatsapp.net`) real.

    Evolution v2.2.3 entrega algunos mensajes con `remoteJid=XXX@lid`
    (WhatsApp LID addressing mode), y el webhook NO propaga el PN. Tampoco
    lo expone `whatsappNumbers` ni `findContacts` directo.

    Truco: Evolution sí guarda un contact con el LID y **otro contact
    separado** con el PN `@s.whatsapp.net` del mismo usuario, y ambos
    comparten la misma `profilePicUrl`. Usamos ese hash visual como
    huella para matchear.

    Cachea el resultado in-memory para que contactos con tráfico continuo no
    golpeen `findChats` en cada mensaje. Reintenta con backoff si el primer
    intento falla — cubre el desfase de sincronización entre el webhook del
    primer mensaje y la llegada del contact `@s.whatsapp.net` a la DB de
    Evolution.

    Retorna el PN en formato puro (solo dígitos) o `None` si no se pudo
    resolver (contact sin foto, foto compartida con otro, instancia
    offline, etc.). El caller debe tener fallback.
    """
    lid_jid = (lid_jid or "").strip()
    if "@lid" not in lid_jid or not instance_name:
        return None

    with _LID_CACHE_LOCK:
        cached = _LID_TO_PN_CACHE.get(lid_jid)
    if cached:
        return cached

    for attempt in range(_LID_RESOLVE_RETRIES + 1):
        result = _resolve_lid_to_pn_once(instance_name, lid_jid)
        if result:
            with _LID_CACHE_LOCK:
                _LID_TO_PN_CACHE[lid_jid] = result
            if attempt > 0:
                logger.info(
                    "resolve_lid_to_pn: %s → %s resuelto en intento %d",
                    lid_jid, result, attempt + 1,
                )
            return result
        if attempt < _LID_RESOLVE_RETRIES:
            time.sleep(_LID_RESOLVE_BACKOFF)

    logger.info(
        "resolve_lid_to_pn: sin contact @s.whatsapp.net con la foto de %s "
        "tras %d intentos",
        lid_jid, _LID_RESOLVE_RETRIES + 1,
    )
    return None
