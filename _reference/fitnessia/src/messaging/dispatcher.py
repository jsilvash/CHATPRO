"""Dispatcher unificado de envíos WhatsApp (Cloud API + Evolution/QR).

Punto único al que cualquier caller llama para enviar mensajes. Decide el
transporte según `WhatsAppNumber.connection_type`:

- `cloud_api` → `src.messaging.whatsapp` (Meta Graph API v18.0).
- `qr`        → `src.messaging.evolution_client` (Evolution API self-hosted).

**Por qué existe** (Fase 4 del plan WhatsApp híbrido):

Sin esta capa, cada caller (NPS, automations, inbox, reactivación) tendría
que saber si el número es QR o Cloud API y manejar dos APIs distintas con
convenciones distintas (Cloud API swallow-to-None, Evolution raise con
detalle). El dispatcher esconde eso detrás de `DispatchResult`.

**Qué NO hace** (por diseño, se consolida en Fase 6):

- No persiste `WhatsAppMessage` outbound. Los callers mantienen su shape
  actual de persistencia para no romper 20 sitios en esta fase.
- No migra los callers existentes. Los que hoy llaman `whatsapp.send_*`
  directo siguen funcionando por Cloud API puro hasta Fase 6.

**Política de templates**:

Enviar un `template` por QR puede hacer que WhatsApp banee el chip (Meta
sanciona la suplantación de templates fuera de WABA). `send_template`
devuelve `DispatchResult(success=False, error=...)` en ese caso, no raise
— así los callers pueden reportar el fallo sin crashear el flujo batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.db.models import Tenant, WhatsAppNumber
from src.db.tenant_context import bypass_tenant_filter
from src.messaging import evolution_client, waha_client, whatsapp
from src.messaging.evolution_client import EvolutionAPIError
from src.messaging.waha_client import WahaAPIError
from src.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Tipos
# ────────────────────────────────────────────────────────────


@dataclass
class DispatchResult:
    """Resultado de un envío via dispatcher.

    `wa_message_id` es el id que devuelve el proveedor (Meta o Evolution).
    El caller lo usa para persistir el `WhatsAppMessage.wa_message_id` y
    para rastrear status updates cuando lleguen por webhook.
    """

    success: bool
    wa_message_id: str = ""
    connection_type: str = ""  # "cloud_api" | "qr"
    wa_number_id: int | None = None
    error: str = ""
    # Metadata de debugging — el caller puede ignorarla.
    raw_response: dict = field(default_factory=dict)


class DispatcherError(Exception):
    """Error irrecuperable del dispatcher (ej. sin wa_number disponible).

    Se usa para errores de configuración/setup que NO deberían ser
    capturados y swalloweados por los callers — indican bugs del admin
    (número no configurado, tenant sin WhatsApp). Los errores de red /
    API se devuelven como `DispatchResult(success=False)`.
    """


# ────────────────────────────────────────────────────────────
# Resolución de WhatsAppNumber
# ────────────────────────────────────────────────────────────


def resolve_wa_number(
    db: Session,
    tenant_id: int,
    *,
    purpose: str = "general",
) -> WhatsAppNumber | None:
    """Elige qué `WhatsAppNumber` del tenant usar para este envío.

    Preferencia (primera que matchea gana):

    1. `purpose` pedido + `is_default=True`.
    2. `purpose` pedido (cualquiera).
    3. `is_default=True` (sin filtrar purpose).
    4. Primero disponible.

    Devuelve `None` si el tenant no tiene ningún `WhatsAppNumber` activo
    — el caller decide si eso es error (raise) o skip (log + continuar).
    """
    with bypass_tenant_filter():
        numbers = db.query(WhatsAppNumber).filter(
            WhatsAppNumber.tenant_id == tenant_id,
            WhatsAppNumber.active == True,  # noqa: E712
        ).all()

    if not numbers:
        return None

    # 1. purpose + default.
    for wn in numbers:
        if wn.purpose == purpose and wn.is_default:
            return wn
    # 2. purpose.
    for wn in numbers:
        if wn.purpose == purpose:
            return wn
    # 3. default.
    for wn in numbers:
        if wn.is_default:
            return wn
    # 4. fallback.
    return numbers[0]


def _resolve_target(
    db: Session,
    tenant_id: int,
    wa_number: WhatsAppNumber | None,
    purpose: str,
) -> WhatsAppNumber:
    """Devuelve el wa_number explícito o lo resuelve; raise si no hay ninguno."""
    if wa_number is not None:
        return wa_number
    resolved = resolve_wa_number(db, tenant_id, purpose=purpose)
    if resolved is None:
        raise DispatcherError(
            f"tenant {tenant_id} no tiene WhatsAppNumber activo (purpose={purpose!r})"
        )
    return resolved


def _tenant_config_for_cloud_api(
    db: Session, tenant_id: int, wa_number: WhatsAppNumber
) -> dict:
    """Arma el `tenant_config` que `whatsapp.py` espera (token + phone_number_id).

    `whatsapp.py` hoy lee `tenant_config["whatsapp"]["phone_number_id"]` y
    `token`. Multi-número por tenant: el dispatcher pasa el phone_number_id
    del `WhatsAppNumber` específico (no el del settings_json global).

    El `token` sigue viniendo del settings_json del tenant — es uno solo
    por WABA. Fase 6 puede mover el token a `WhatsAppNumber.token_ref`.
    """
    import json

    with bypass_tenant_filter():
        tenant = db.query(Tenant).get(tenant_id)
    if tenant is None:
        return {}
    try:
        cfg = json.loads(tenant.settings_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    wa_cfg = cfg.get("whatsapp", {})
    return {
        "whatsapp": {
            "enabled": True,
            "token": wa_cfg.get("token", ""),
            "phone_number_id": wa_number.phone_number_id or "",
        }
    }


# ────────────────────────────────────────────────────────────
# Envíos
# ────────────────────────────────────────────────────────────


def send_text(
    db: Session,
    tenant_id: int,
    to_phone: str,
    text: str,
    *,
    wa_number: WhatsAppNumber | None = None,
    purpose: str = "general",
) -> DispatchResult:
    """Envía texto plano al contacto usando el transporte que corresponda."""
    if not text:
        return DispatchResult(success=False, error="text vacío")

    normalized = normalize_phone(to_phone)
    if not normalized:
        return DispatchResult(success=False, error=f"teléfono inválido: {to_phone!r}")

    wn = _resolve_target(db, tenant_id, wa_number, purpose)

    if wn.connection_type == "qr":
        return _send_text_qr(wn, normalized, text)
    if wn.connection_type == "waha":
        return _send_text_waha(wn, normalized, text)
    if wn.connection_type == "cloud_api":
        return _send_text_cloud(db, tenant_id, wn, normalized, text)
    return DispatchResult(
        success=False,
        wa_number_id=wn.id,
        error=f"connection_type desconocido: {wn.connection_type!r}",
    )


def send_template(
    db: Session,
    tenant_id: int,
    to_phone: str,
    template_name: str,
    params: dict,
    *,
    language: str = "es",
    wa_number: WhatsAppNumber | None = None,
    purpose: str = "general",
) -> DispatchResult:
    """Envía un mensaje de template.

    Templates solo Cloud API: Meta los registra y aprueba. Un QR enviando
    templates imita WABA y es motivo de ban. Si el wa_number resuelto es
    QR, devuelve `success=False` con error descriptivo (no raise — el
    caller puede estar iterando sobre muchos destinatarios y no queremos
    abortar el batch entero por un número mal configurado).
    """
    normalized = normalize_phone(to_phone)
    if not normalized:
        return DispatchResult(success=False, error=f"teléfono inválido: {to_phone!r}")

    wn = _resolve_target(db, tenant_id, wa_number, purpose)

    if wn.connection_type == "qr":
        logger.warning(
            "dispatcher: bloqueado template %r por QR (tenant=%d wa_number=%d). "
            "WhatsApp puede banear el chip — enviar como texto libre o usar "
            "un WhatsAppNumber cloud_api.",
            template_name, tenant_id, wn.id,
        )
        return DispatchResult(
            success=False,
            wa_number_id=wn.id,
            connection_type="qr",
            error="templates no permitidos por conexión QR (riesgo de ban)",
        )

    if wn.connection_type != "cloud_api":
        return DispatchResult(
            success=False,
            wa_number_id=wn.id,
            error=f"connection_type desconocido: {wn.connection_type!r}",
        )

    tenant_config = _tenant_config_for_cloud_api(db, tenant_id, wn)
    try:
        message_id = whatsapp.send_whatsapp_template(
            normalized, template_name, params, language=language,
            tenant_config=tenant_config,
        )
    except Exception as e:  # whatsapp.py ya swallowea internamente; defense-in-depth
        logger.error("dispatcher cloud template send crash: %s", e, exc_info=True)
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="cloud_api",
            error=f"crash: {e}",
        )
    if not message_id:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="cloud_api",
            error="Cloud API no retornó message_id (ver logs de whatsapp.py)",
        )
    return DispatchResult(
        success=True,
        wa_message_id=message_id,
        wa_number_id=wn.id,
        connection_type="cloud_api",
    )


def send_media(
    db: Session,
    tenant_id: int,
    to_phone: str,
    media_url: str,
    *,
    caption: str = "",
    mediatype: str = "image",  # image | video | document | audio
    file_name: str | None = None,
    wa_number: WhatsAppNumber | None = None,
    purpose: str = "general",
) -> DispatchResult:
    """Envía media por URL (Evolution acepta URL o base64; solo QR).

    Para enviar bytes crudos (multipart del agente) usar `send_media_bytes`
    — maneja el upload a Meta (`/{phone_id}/media`) cuando el transporte
    es Cloud API.
    """
    if not media_url:
        return DispatchResult(success=False, error="media_url vacío")

    normalized = normalize_phone(to_phone)
    if not normalized:
        return DispatchResult(success=False, error=f"teléfono inválido: {to_phone!r}")

    wn = _resolve_target(db, tenant_id, wa_number, purpose)

    if wn.connection_type == "qr":
        return _send_media_qr(
            wn, normalized, media_url, caption=caption,
            mediatype=mediatype, file_name=file_name,
        )

    if wn.connection_type == "waha":
        return _send_media_waha(
            wn, normalized, media_url, caption=caption,
            mediatype=mediatype, file_name=file_name,
        )

    if wn.connection_type == "cloud_api":
        return DispatchResult(
            success=False,
            wa_number_id=wn.id,
            connection_type="cloud_api",
            error=(
                "send_media por URL no soportado en Cloud API — Meta requiere "
                "upload previo. Usar send_media_bytes con el binario crudo."
            ),
        )

    return DispatchResult(
        success=False, wa_number_id=wn.id,
        error=f"connection_type desconocido: {wn.connection_type!r}",
    )


def send_media_bytes(
    db: Session,
    tenant_id: int,
    to_phone: str,
    *,
    file_bytes: bytes,
    mime: str,
    mediatype: str = "image",  # image | video | document | audio
    file_name: str | None = None,
    caption: str = "",
    wa_number: WhatsAppNumber | None = None,
    purpose: str = "general",
) -> DispatchResult:
    """Envía media desde bytes crudos — el path normal para outbound del agente.

    Abstrae los dos transportes:
    - QR: Evolution acepta base64 inline en `send_media_bytes`.
    - Cloud API: sube primero a `/{phone_id}/media` para obtener `media_id`,
      luego envía el mensaje con `type=image|audio|...` y `{id}` referenciando
      el upload. El `media_id` queda válido ~30 días en Meta.
    """
    if not file_bytes:
        return DispatchResult(success=False, error="file_bytes vacío")
    if not mime:
        return DispatchResult(success=False, error="mime vacío")

    normalized = normalize_phone(to_phone)
    if not normalized:
        return DispatchResult(success=False, error=f"teléfono inválido: {to_phone!r}")

    wn = _resolve_target(db, tenant_id, wa_number, purpose)

    if wn.connection_type == "qr":
        if not wn.evolution_instance_name:
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="qr",
                error="WhatsAppNumber sin evolution_instance_name",
            )
        try:
            resp = evolution_client.send_media_bytes(
                wn.evolution_instance_name, normalized,
                file_bytes=file_bytes, mime=mime, mediatype=mediatype,
                caption=caption, file_name=file_name,
            )
        except EvolutionAPIError as e:
            logger.error(
                "dispatcher QR send_media_bytes falló: instance=%s status=%d msg=%s",
                wn.evolution_instance_name, e.status, e.message,
            )
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="qr",
                error=f"[{e.status}] {e.message}",
            )
        return DispatchResult(
            success=True,
            wa_message_id=_extract_evolution_message_id(resp),
            wa_number_id=wn.id,
            connection_type="qr",
            raw_response=resp if isinstance(resp, dict) else {},
        )

    if wn.connection_type == "waha":
        if not wn.evolution_instance_name:
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="waha",
                error="WhatsAppNumber sin evolution_instance_name (session name WAHA)",
            )
        try:
            resp = waha_client.send_media_bytes(
                wn.evolution_instance_name, normalized,
                file_bytes=file_bytes, mime=mime, mediatype=mediatype,
                caption=caption, file_name=file_name,
            )
        except WahaAPIError as e:
            logger.error(
                "dispatcher WAHA send_media_bytes falló: session=%s status=%d msg=%s",
                wn.evolution_instance_name, e.status, e.message,
            )
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="waha",
                error=f"[{e.status}] {e.message}",
            )
        return DispatchResult(
            success=True,
            wa_message_id=_extract_evolution_message_id(resp),
            wa_number_id=wn.id,
            connection_type="waha",
            raw_response=resp if isinstance(resp, dict) else {},
        )

    if wn.connection_type == "cloud_api":
        tenant_config = _tenant_config_for_cloud_api(db, tenant_id, wn)
        try:
            media_id = whatsapp.upload_media(
                file_bytes, mime, file_name or "upload",
                tenant_config=tenant_config,
            )
        except Exception as e:
            logger.error("dispatcher cloud upload_media crash: %s", e, exc_info=True)
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="cloud_api",
                error=f"upload crash: {e}",
            )
        if not media_id:
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="cloud_api",
                error="upload_media no retornó media_id",
            )
        try:
            wa_id = whatsapp.send_media_message(
                normalized, media_id, mediatype,
                caption=caption, filename=file_name or "",
                tenant_config=tenant_config,
            )
        except Exception as e:
            logger.error("dispatcher cloud send_media_message crash: %s", e, exc_info=True)
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="cloud_api",
                error=f"send crash: {e}",
            )
        if not wa_id:
            return DispatchResult(
                success=False, wa_number_id=wn.id, connection_type="cloud_api",
                error="send_media_message no retornó wa_message_id",
            )
        return DispatchResult(
            success=True, wa_message_id=wa_id,
            wa_number_id=wn.id, connection_type="cloud_api",
        )

    return DispatchResult(
        success=False, wa_number_id=wn.id,
        error=f"connection_type desconocido: {wn.connection_type!r}",
    )


# ────────────────────────────────────────────────────────────
# Transport adapters (privados)
# ────────────────────────────────────────────────────────────


def _send_text_qr(
    wn: WhatsAppNumber, to_phone: str, text: str
) -> DispatchResult:
    if not wn.evolution_instance_name:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="qr",
            error="WhatsAppNumber sin evolution_instance_name — admin debe configurarlo",
        )
    try:
        resp = evolution_client.send_text(
            wn.evolution_instance_name, to_phone, text,
        )
    except EvolutionAPIError as e:
        logger.error(
            "dispatcher QR send_text falló: instance=%s status=%d msg=%s",
            wn.evolution_instance_name, e.status, e.message,
        )
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="qr",
            error=f"[{e.status}] {e.message}",
        )
    return DispatchResult(
        success=True,
        wa_message_id=_extract_evolution_message_id(resp),
        wa_number_id=wn.id,
        connection_type="qr",
        raw_response=resp if isinstance(resp, dict) else {},
    )


def _send_media_qr(
    wn: WhatsAppNumber,
    to_phone: str,
    media_url: str,
    *,
    caption: str,
    mediatype: str,
    file_name: str | None,
) -> DispatchResult:
    if not wn.evolution_instance_name:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="qr",
            error="WhatsAppNumber sin evolution_instance_name",
        )
    try:
        resp = evolution_client.send_media(
            wn.evolution_instance_name, to_phone, media_url,
            caption=caption, mediatype=mediatype, file_name=file_name,
        )
    except EvolutionAPIError as e:
        logger.error(
            "dispatcher QR send_media falló: instance=%s status=%d msg=%s",
            wn.evolution_instance_name, e.status, e.message,
        )
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="qr",
            error=f"[{e.status}] {e.message}",
        )
    return DispatchResult(
        success=True,
        wa_message_id=_extract_evolution_message_id(resp),
        wa_number_id=wn.id,
        connection_type="qr",
        raw_response=resp if isinstance(resp, dict) else {},
    )


def _send_text_waha(
    wn: WhatsAppNumber, to_phone: str, text: str
) -> DispatchResult:
    if not wn.evolution_instance_name:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="waha",
            error="WhatsAppNumber sin evolution_instance_name (session name WAHA)",
        )
    try:
        resp = waha_client.send_text(wn.evolution_instance_name, to_phone, text)
    except WahaAPIError as e:
        logger.error(
            "dispatcher WAHA send_text falló: session=%s status=%d msg=%s",
            wn.evolution_instance_name, e.status, e.message,
        )
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="waha",
            error=f"[{e.status}] {e.message}",
        )
    return DispatchResult(
        success=True,
        wa_message_id=_extract_evolution_message_id(resp),
        wa_number_id=wn.id,
        connection_type="waha",
        raw_response=resp if isinstance(resp, dict) else {},
    )


def _send_media_waha(
    wn: WhatsAppNumber,
    to_phone: str,
    media_url: str,
    *,
    caption: str,
    mediatype: str,
    file_name: str | None,
) -> DispatchResult:
    if not wn.evolution_instance_name:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="waha",
            error="WhatsAppNumber sin evolution_instance_name (session name WAHA)",
        )
    try:
        resp = waha_client.send_media(
            wn.evolution_instance_name, to_phone, media_url,
            caption=caption, mediatype=mediatype, file_name=file_name,
        )
    except WahaAPIError as e:
        logger.error(
            "dispatcher WAHA send_media falló: session=%s status=%d msg=%s",
            wn.evolution_instance_name, e.status, e.message,
        )
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="waha",
            error=f"[{e.status}] {e.message}",
        )
    return DispatchResult(
        success=True,
        wa_message_id=_extract_evolution_message_id(resp),
        wa_number_id=wn.id,
        connection_type="waha",
        raw_response=resp if isinstance(resp, dict) else {},
    )


def _send_text_cloud(
    db: Session,
    tenant_id: int,
    wn: WhatsAppNumber,
    to_phone: str,
    text: str,
) -> DispatchResult:
    tenant_config = _tenant_config_for_cloud_api(db, tenant_id, wn)
    try:
        message_id = whatsapp.send_whatsapp_text(
            to_phone, text, tenant_config=tenant_config,
        )
    except Exception as e:
        logger.error("dispatcher cloud send_text crash: %s", e, exc_info=True)
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="cloud_api",
            error=f"crash: {e}",
        )
    if not message_id:
        return DispatchResult(
            success=False, wa_number_id=wn.id, connection_type="cloud_api",
            error="Cloud API no retornó message_id",
        )
    return DispatchResult(
        success=True,
        wa_message_id=message_id,
        wa_number_id=wn.id,
        connection_type="cloud_api",
    )


def _extract_evolution_message_id(resp: object) -> str:
    """Saca el wa_message_id del response de Evolution send_text/send_media.

    Shape típico: `{"key": {"id": "ABC123"}, "messageTimestamp": ..., ...}`.
    Si viene vacío o malformado, devuelve "" (el caller lo trata como
    `success=True` porque el envío sí ocurrió, solo perdimos el tracking).
    """
    if not isinstance(resp, dict):
        return ""
    key = resp.get("key")
    if isinstance(key, dict):
        return key.get("id", "") or ""
    return resp.get("id", "") or ""
