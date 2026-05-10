"""Cliente HTTP para WAHA Plus (único transporte WhatsApp del Hub).

Portado de FitnessIA (`_reference/fitnessia/src/messaging/waha_client.py`)
con las simplificaciones del Hub:
- ``evolution_instance_name`` → ``waha_session_name`` (rename ya hecho).
- Eliminadas las ramas QR-Evolution y Cloud API: aquí solo WAHA.
- Eliminado ``_preload_lid_pn_cache`` con queries al modelo legacy
  ``WhatsAppNumber``; se reescribirá en Fase 1+ si hace falta usando
  ``WaConversation`` del Hub.

Convenciones de error:
- 4xx → ``WahaAPIError`` inmediatamente.
- 5xx → retry con backoff exponencial; tras agotar, ``WahaAPIError``.
- Red/timeout → ``WahaAPIError(status=-1, ...)``.
- LID sin resolver en envío → ``WahaAPIError(status=-2, ...)`` — **ABORT**,
  nunca enviar a ``@lid`` como chatId (ver ``_resolve_target``).
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

    ``status=-1`` → error local (config ausente, timeout, fallo DNS).
    ``status=-2`` → LID sin resolver, **ABORT** del envío.
    Cualquier otro valor entero es un HTTP status code real de WAHA.
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


# Para tests: número máximo de retries 5xx. Se sobreescribe en pyproject vía Settings.
_MAX_RETRIES = 3
_TIMEOUT_S = 30.0


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
    """Llamada contra WAHA con retry de 5xx y network errors."""
    url = f"{_base_url()}{path}"
    headers = _headers()

    last_network_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = httpx.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=_TIMEOUT_S,
            )
        except httpx.RequestError as e:
            last_network_error = e
            logger.warning(
                "WAHA %s %s network error (intento %d/%d): %s",
                method, path, attempt + 1, _MAX_RETRIES + 1, e,
            )
            if attempt < _MAX_RETRIES:
                _sleep_for_retry(attempt)
                continue
            raise WahaAPIError(-1, f"Network error: {e}", url=url) from e

        status = resp.status_code
        body = _safe_json(resp)

        if 200 <= status < 300:
            return body

        if 500 <= status < 600 and attempt < _MAX_RETRIES:
            logger.warning(
                "WAHA %s %s → %d (intento %d/%d): %s",
                method, path, status, attempt + 1, _MAX_RETRIES + 1, _short_body(body),
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

# Heurística LID: IDs ≥ 14 dígitos son LIDs (legacy SYNEX, ver §0 plan).
_LID_MIN_DIGITS = 14


def _to_chat_id(number: str) -> str:
    """Convierte un número normalizado (solo dígitos) al chatId WAHA."""
    return f"{number}@c.us"


def _resolve_target(session_name: str, number: str) -> str:
    """Devuelve el chatId WAHA para ``number``, resolviendo LID si hace falta.

    Si el número parece LID (≥ 14 dígitos) intenta resolverlo al PN real vía
    ``resolve_lid_to_pn``. Si la resolución falla, levanta ``WahaAPIError(-2)``
    — **NUNCA** se hace fallback a ``@lid`` como chatId.

    En producción (FitnessIA, 2026-04-23) WhatsApp reinterpretó un LID como
    un número internacional ficticio (+1413…) y entregó el mensaje a un
    desconocido. Mejor fallar visible que enviar al lugar equivocado.
    """
    if not number.isdigit() or len(number) < _LID_MIN_DIGITS:
        return _to_chat_id(number)

    lid_jid = f"{number}@lid"
    pn = resolve_lid_to_pn(session_name, lid_jid)
    if pn:
        logger.info("WAHA send: LID %s → PN %s resuelto", number, pn)
        return _to_chat_id(pn)

    logger.error(
        "WAHA send: LID %s sin resolver — ABORT para evitar envío a "
        "número ficticio. Pedir al contacto que envíe otro mensaje.",
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
    """Envía texto plano. ``to`` puede ser PN, LID o con formato — se normaliza."""
    number = normalize_phone(to)
    if not number:
        raise WahaAPIError(-1, f"Número inválido: {to!r}")
    chat_id = _resolve_target(session_name, number)
    payload = {"session": session_name, "chatId": chat_id, "text": text}
    return _request("POST", "/api/sendText", json=payload) or {}


def mark_as_read(
    session_name: str,
    *,
    chat_id: str,
    message_id: str,
) -> dict:
    """Envía read receipt al sender (doble check azul) vía ``POST /api/sendSeen``.

    Endpoint global (``session`` va en el body, no en el path). El batch
    ``/chats/{chatId}/messages/read`` mantiene el store de WAHA pero NO
    emite el receipt al sender en NOWEB.
    """
    return (
        _request(
            "POST",
            "/api/sendSeen",
            json={
                "session": session_name,
                "chatId": chat_id,
                "messageId": message_id,
            },
        )
        or {}
    )


def send_typing(
    session_name: str,
    chat_id: str,
    *,
    typing: bool = True,
) -> dict:
    """Indica "escribiendo…" al contacto. ``typing=False`` lo limpia."""
    action = "startTyping" if typing else "stopTyping"
    return (
        _request(
            "POST",
            f"/api/{action}",
            json={"session": session_name, "chatId": chat_id},
        )
        or {}
    )


# ────────────────────────────────────────────────────────────
# Gestión de sesiones
# ────────────────────────────────────────────────────────────


def get_session_status(session_name: str) -> dict:
    """Devuelve el status actual de la sesión WAHA."""
    return _request("GET", f"/api/sessions/{session_name}") or {}


def request_pairing_code(session_name: str, phone_number: str) -> str:
    """Pide pairing code de 8 chars (alternativa al QR)."""
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


def create_session(session_name: str, *, config: dict | None = None) -> dict:
    """Crea una sesión WAHA con configuración opcional."""
    payload: dict[str, Any] = {"name": session_name}
    if config:
        payload["config"] = config
    payload["start"] = True
    return _request("POST", "/api/sessions", json=payload) or {}


def start_session(session_name: str) -> dict:
    """Inicia una sesión detenida."""
    return _request("POST", f"/api/sessions/{session_name}/start") or {}


def stop_session(session_name: str) -> dict:
    """Detiene una sesión activa."""
    return _request("POST", f"/api/sessions/{session_name}/stop") or {}


def logout_session(session_name: str) -> dict:
    """Cierra sesión y limpia credenciales (chip queda desvinculado)."""
    return _request("POST", f"/api/sessions/{session_name}/logout") or {}


def list_sessions() -> list[dict]:
    """Lista todas las sesiones activas en WAHA."""
    data = _request("GET", "/api/sessions")
    if isinstance(data, list):
        return data
    return []


def get_qr(session_name: str) -> str:
    """Pide el QR como base64 (sin prefijo data:URL).

    WAHA expone ``GET /api/{session}/auth/qr?format=image``. Devuelve ``""``
    si la sesión ya está ``WORKING`` (no hay QR para mostrar).
    """
    try:
        resp = httpx.get(
            f"{_base_url()}/api/{session_name}/auth/qr",
            params={"format": "image"},
            headers=_headers(),
            timeout=_TIMEOUT_S,
        )
    except httpx.RequestError as e:
        raise WahaAPIError(-1, f"Network error: {e}") from e
    if resp.status_code == 422:
        return ""
    if resp.status_code != 200:
        raise WahaAPIError(resp.status_code, "no se pudo obtener QR", body=resp.text)
    import base64
    return base64.b64encode(resp.content).decode("ascii")


# ────────────────────────────────────────────────────────────
# Resolución LID → PN (endpoint nativo WAHA)
# ────────────────────────────────────────────────────────────

# Cache in-memory: el LID es estable, cachearlo de por vida del proceso es seguro.
_LID_TO_PN_CACHE: dict[str, str] = {}
_LID_CACHE_LOCK = threading.Lock()


def _clear_lid_cache() -> None:
    """Solo para tests."""
    with _LID_CACHE_LOCK:
        _LID_TO_PN_CACHE.clear()


def resolve_lid_to_pn(session_name: str, lid_jid: str) -> str | None:
    """Resuelve un ``@lid`` al phone number real, en 4 capas + cache + lock.

    Orden de costo creciente:
    1. Cache in-memory (O(1)).
    2. ``GET /contacts/lid-pn?lid=<jid>`` — endpoint dedicado.
    3. ``GET /contacts/{lid_jid}`` — el store puede indexar por LID.
    4. Escaneo de ``GET /contacts`` buscando ``contact.lid == lid_jid``.

    Devuelve el phone solo dígitos (ej. ``"56941131946"``) o ``None`` si el
    LID no está en la DB local de WAHA todavía.
    """
    lid_jid = (lid_jid or "").strip()
    if "@lid" not in lid_jid or not session_name:
        return None

    with _LID_CACHE_LOCK:
        cached = _LID_TO_PN_CACHE.get(lid_jid)
    if cached:
        return cached

    # Capa 1: endpoint dedicado.
    try:
        resp = _request(
            "GET",
            f"/api/{session_name}/contacts/lid-pn",
            params={"lid": lid_jid},
        )
        pn_raw = ""
        if isinstance(resp, dict):
            pn_raw = (
                resp.get("PN")
                or resp.get("phoneNumber")
                or resp.get("pn")
                or ""
            )
        elif isinstance(resp, str):
            pn_raw = resp
        pn = pn_raw.split("@")[0].strip() if pn_raw else ""
        if pn and pn.isdigit() and 7 <= len(pn) < _LID_MIN_DIGITS:
            with _LID_CACHE_LOCK:
                _LID_TO_PN_CACHE[lid_jid] = pn
            logger.info("WAHA resolve_lid_to_pn (lid-pn): %s → %s", lid_jid, pn)
            return pn
    except WahaAPIError as e:
        logger.debug("resolve_lid_to_pn lid-pn endpoint: %s → %s", lid_jid, e)

    # Capa 2: contacto por LID jid directo.
    try:
        resp2 = _request("GET", f"/api/{session_name}/contacts/{lid_jid}")
        if isinstance(resp2, dict):
            cid = resp2.get("id") or resp2.get("jid") or ""
            if "@c.us" in cid or "@s.whatsapp.net" in cid:
                pn2 = cid.split("@")[0].strip()
            else:
                pn2 = (resp2.get("pn") or resp2.get("number") or "").split("@")[0].strip()
            if pn2 and pn2.isdigit() and 7 <= len(pn2) < _LID_MIN_DIGITS:
                with _LID_CACHE_LOCK:
                    _LID_TO_PN_CACHE[lid_jid] = pn2
                logger.info(
                    "WAHA resolve_lid_to_pn (contacts/{lid}): %s → %s", lid_jid, pn2
                )
                return pn2
    except WahaAPIError as e2:
        logger.debug("resolve_lid_to_pn contacts fallback: %s → %s", lid_jid, e2)

    # Capa 3: escanear lista completa de contactos.
    try:
        contacts = _request("GET", f"/api/{session_name}/contacts")
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                clid = contact.get("lid") or ""
                if clid != lid_jid:
                    continue
                cid = contact.get("id") or ""
                if "@c.us" in cid or "@s.whatsapp.net" in cid:
                    pn3 = cid.split("@")[0].strip()
                    if pn3 and pn3.isdigit() and 7 <= len(pn3) < _LID_MIN_DIGITS:
                        with _LID_CACHE_LOCK:
                            _LID_TO_PN_CACHE[lid_jid] = pn3
                        logger.info(
                            "WAHA resolve_lid_to_pn (contacts scan): %s → %s",
                            lid_jid, pn3,
                        )
                        return pn3
    except WahaAPIError as e3:
        logger.debug("resolve_lid_to_pn contacts scan: %s → %s", lid_jid, e3)

    logger.info("WAHA resolve_lid_to_pn: sin PN para %s", lid_jid)
    return None


# ────────────────────────────────────────────────────────────
# ensure_waha_webhooks — reaplica config en cada arranque
# ────────────────────────────────────────────────────────────


def ensure_waha_webhooks() -> None:
    """Reaplica los webhooks en todas las sesiones WAHA activas al arranque.

    WAHA persiste credenciales pero NO la configuración de webhooks. Esta
    función los reapliquea en cada restart del backend para que nunca queden
    sesiones sin webhook.

    Idempotente: una segunda llamada sobreescribe con los mismos valores.

    Eventos obligatorios: ``message``, ``message.any``, ``message.ack``,
    ``session.status``. Borrar ``message.ack`` deja la fase ACK inerte.
    """
    settings = get_settings()

    if not settings.waha_api_url:
        logger.debug("ensure_waha_webhooks: WAHA_API_URL no configurado — omitiendo")
        return

    base_url = (settings.public_base_url or "").rstrip("/")
    if not base_url:
        logger.warning("ensure_waha_webhooks: public_base_url vacío — omitiendo")
        return

    # Import lazy para evitar ciclo de import.
    from src.db.session import get_session_factory
    from src.tenancy.context import bypass_tenant_filter
    from src.wa.models import WaNumber

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        with bypass_tenant_filter():
            wa_numbers = (
                db.query(WaNumber)
                .filter(
                    WaNumber.active.is_(True),
                    WaNumber.waha_session_name.isnot(None),
                )
                .all()
            )
        sessions = [(n.id, n.waha_session_name) for n in wa_numbers if n.waha_session_name]
    finally:
        db.close()

    if not sessions:
        logger.debug("ensure_waha_webhooks: sin sesiones WAHA activas en DB")
        return

    webhook_token = (settings.waha_webhook_token or "").strip()
    webhook_entry: dict[str, Any] = {
        # Cada wa_number tiene su propia URL de webhook (multi-tenant routing).
        "events": ["message", "message.any", "message.ack", "session.status"],
    }
    if webhook_token:
        webhook_entry["customHeaders"] = [
            {"name": "X-WAHA-Token", "value": webhook_token}
        ]

    for wa_number_id, session_name in sessions:
        webhook_url = f"{base_url}/webhook/waha/{wa_number_id}"
        entry = dict(webhook_entry, url=webhook_url)
        try:
            _request(
                "PUT",
                f"/api/sessions/{session_name}",
                json={
                    "config": {
                        "noweb": {"store": {"enabled": True, "fullSync": True}},
                        "webhooks": [entry],
                    }
                },
            )
            logger.info(
                "ensure_waha_webhooks: %s → webhooks restaurados (url=%s)",
                session_name, webhook_url,
            )
        except WahaAPIError as e:
            logger.warning(
                "ensure_waha_webhooks: %s → error %s (WAHA puede estar caído)",
                session_name, e,
            )
