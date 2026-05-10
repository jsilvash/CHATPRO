"""Dispatcher de webhooks salientes (Fase 10).

Flujo de vida de una entrega:
  pending → intento HTTP → success
                         → failed (próximo reintento en RETRY_DELAYS[attempts])
                         → dead (si attempts >= MAX_ATTEMPTS)

Reintentos (índice = número de intentos fallidos previos):
  [0] 1 min, [1] 5 min, [2] 30 min, [3] 2 h, [4] 12 h, [5] 24 h → dead

Firma del payload: X-ChatPro-Signature: sha256=<hmac-hex>
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from src.public_api.models import WebhookDelivery, WebhookOut

logger = logging.getLogger(__name__)

# Demoras de reintento en segundos: 1m, 5m, 30m, 2h, 12h, 24h
RETRY_DELAYS: list[int] = [60, 300, 1800, 7200, 43200, 86400]
MAX_ATTEMPTS = len(RETRY_DELAYS)

# Timeout HTTP por intento
_HTTP_TIMEOUT_S = 15


# ── Emisión de eventos ────────────────────────────────────────────────────────


def emit_event(
    tenant_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
    db: Session,
) -> int:
    """Crea registros WebhookDelivery pendientes para cada webhook suscrito al evento.

    Args:
        tenant_id: Tenant que emite el evento.
        event: Nombre del evento (ej: 'message.received').
        payload: Cuerpo JSON que se enviará al endpoint destino.
        db: Sesión de BD activa.

    Returns:
        Número de entregas creadas.
    """
    webhooks: list[WebhookOut] = (
        db.query(WebhookOut)
        .filter(
            WebhookOut.tenant_id == tenant_id,
            WebhookOut.enabled.is_(True),
            WebhookOut.events.contains([event]),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    count = 0
    for wh in webhooks:
        delivery = WebhookDelivery(
            tenant_id=tenant_id,
            webhook_id=wh.id,
            event=event,
            payload=payload,
            status="pending",
            attempts=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        count += 1

    if count:
        db.flush()

    return count


# ── Entrega individual ────────────────────────────────────────────────────────


def _sign_payload(secret: str, body_bytes: bytes) -> str:
    """Calcula la firma HMAC-SHA256 del payload para el header X-ChatPro-Signature."""
    sig = hmac.new(secret.encode(), body_bytes, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def deliver_webhook(
    delivery: WebhookDelivery,
    webhook: WebhookOut,
    db: Session,
    http_client: httpx.Client | None = None,
) -> bool:
    """Intenta entregar una WebhookDelivery.

    Actualiza ``delivery.status``, ``delivery.attempts``,
    ``delivery.next_attempt_at`` y los contadores del webhook padre.

    Args:
        delivery: Registro de entrega a intentar.
        webhook: Webhook destino (ya cargado para evitar lazy-load).
        db: Sesión de BD activa.
        http_client: Cliente HTTP opcional (para inyectar mock en tests).

    Returns:
        ``True`` si la entrega fue exitosa.
    """
    body: dict[str, Any] = {
        "event": delivery.event,
        "delivery_id": str(delivery.id),
        "tenant_id": str(delivery.tenant_id),
        "data": delivery.payload,
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode()
    signature = _sign_payload(webhook.secret, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-ChatPro-Signature": signature,
        "X-ChatPro-Event": delivery.event,
        "User-Agent": "ChatPro-Webhooks/1.0",
    }

    now = datetime.now(timezone.utc)
    delivery.attempts += 1
    response_status: int | None = None
    response_body: str | None = None
    success = False

    try:
        client = http_client or httpx.Client(timeout=_HTTP_TIMEOUT_S)
        resp = client.post(webhook.url, content=body_bytes, headers=headers)
        response_status = resp.status_code
        response_body = resp.text[:500]
        success = 200 <= resp.status_code < 300
    except Exception as exc:
        response_body = str(exc)[:500]
        logger.warning(
            "Webhook delivery %s a %s falló: %s", delivery.id, webhook.url, exc
        )

    delivery.last_response_status = response_status
    delivery.last_response_body = response_body

    if success:
        delivery.status = "success"
        delivery.next_attempt_at = None
        webhook.last_success_at = now
        webhook.consecutive_failures = 0
    else:
        webhook.last_failure_at = now
        webhook.consecutive_failures = (webhook.consecutive_failures or 0) + 1

        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = "dead"
            delivery.next_attempt_at = None
            logger.error(
                "Webhook delivery %s declarado dead tras %d intentos",
                delivery.id,
                delivery.attempts,
            )
        else:
            delivery.status = "failed"
            delay_s = RETRY_DELAYS[delivery.attempts - 1]
            delivery.next_attempt_at = now + timedelta(seconds=delay_s)

    db.flush()
    return success


# ── Procesamiento en lote ─────────────────────────────────────────────────────


def process_due_deliveries(
    db: Session,
    http_client: httpx.Client | None = None,
    batch_size: int = 100,
) -> int:
    """Procesa todas las entregas pendientes cuyo ``next_attempt_at`` ya venció.

    Diseñado para correr periódicamente via Celery beat o desde tests.

    Returns:
        Número de entregas procesadas.
    """
    now = datetime.now(timezone.utc)

    deliveries: list[WebhookDelivery] = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status.in_(["pending", "failed"]),
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(batch_size)
        .all()
    )

    if not deliveries:
        return 0

    # Pre-cargar webhooks asociados para evitar N+1
    webhook_ids = {d.webhook_id for d in deliveries}
    webhooks_map: dict[uuid.UUID, WebhookOut] = {
        wh.id: wh
        for wh in db.query(WebhookOut).filter(WebhookOut.id.in_(webhook_ids)).all()
    }

    processed = 0
    for delivery in deliveries:
        wh = webhooks_map.get(delivery.webhook_id)
        if wh is None:
            logger.warning("Webhook %s no encontrado para delivery %s", delivery.webhook_id, delivery.id)
            continue
        deliver_webhook(delivery, wh, db, http_client=http_client)
        processed += 1

    return processed
