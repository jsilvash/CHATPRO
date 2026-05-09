"""Endpoint de webhook WooCommerce.

Ruta: POST /webhooks/woo/{tenant_id}/{config_id}
Auth: HMAC-SHA256 (X-WC-Webhook-Signature). No requiere JWT.
"""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.connectors.woocommerce.connector import WooCommerceConnector
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/woo", tags=["webhooks-woo"])


@router.post("/{tenant_id}/{config_id}", status_code=200)
async def recibir_webhook_woocommerce(
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Recibe y procesa un webhook de WooCommerce.

    WooCommerce firma el cuerpo con HMAC-SHA256 usando el ``webhook_secret``
    almacenado en ``connector_configs``. Cualquier firma inválida retorna 401.
    """
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    with bypass_tenant_filter():
        connector = WooCommerceConnector(tenant_id, config_id, db=db)
        verification = connector.verify_webhook(raw_body, headers)

    if not verification.valid:
        logger.warning(
            "Webhook Woo rechazado — tenant=%s config=%s reason=%s",
            tenant_id, config_id, verification.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Firma inválida: {verification.reason}",
        )

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload no es JSON válido",
        )

    try:
        with bypass_tenant_filter():
            connector.webhook_handler(payload, headers)
    except Exception as exc:
        logger.exception(
            "webhook_handler falló — tenant=%s config=%s", tenant_id, config_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return {"ok": True}
