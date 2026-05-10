"""Endpoint FastAPI para webhooks entrantes de WooCommerce (Fase 6).

POST /webhooks/woo/{tenant_id}/{config_id}

Flujo:
  1. Leer body crudo (necesario para verificar firma HMAC).
  2. Verificar X-WC-Webhook-Signature con el secret almacenado en ConnectorConfig.
  3. Despachar a WooCommerceConnector.webhook_handler(payload, headers).
  4. Retornar 200 OK.

WooCommerce espera 200 en ≤5 s; el handler es síncrono y rápido
(escribe en BD + encola embedding, no llama al API de Woo).
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig
from src.connectors.woocommerce.connector import WooCommerceConnector
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks-woo"])


@router.post(
    "/webhooks/woo/{tenant_id}/{config_id}",
    status_code=status.HTTP_200_OK,
)
async def recibir_webhook_woo(
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Recibe y procesa un webhook de WooCommerce."""
    payload_bytes = await request.body()
    headers = dict(request.headers)

    with bypass_tenant_filter():
        config = db.get(ConnectorConfig, config_id)

    if config is None or config.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config no encontrada")

    connector = WooCommerceConnector(tenant_id, config_id, db=db)
    verification = connector.verify_webhook(payload_bytes, headers)

    if not verification.valid:
        logger.warning(
            "Webhook Woo rechazado — tenant=%s config=%s motivo=%s",
            tenant_id, config_id, verification.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Firma inválida: {verification.reason}",
        )

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload no es JSON válido",
        )

    try:
        connector.webhook_handler(payload, headers)
    except Exception:
        logger.exception(
            "Error en webhook_handler — tenant=%s config=%s topic=%s",
            tenant_id, config_id, headers.get("x-wc-webhook-topic", "?"),
        )
        # Retornamos 200 para que Woo no reintente; el error quedó en logs.

    return {"ok": True}
