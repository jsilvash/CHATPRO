"""Endpoint público para webhooks de WooCommerce (Fase 6).

POST /webhooks/woo/{tenant_id}/{config_id}

No requiere autenticación JWT — la autenticación se hace via HMAC
X-WC-Webhook-Signature con el ``webhook_secret`` del ConnectorConfig.
"""

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

router = APIRouter(tags=["woo-webhooks"])


@router.post(
    "/webhooks/woo/{tenant_id}/{config_id}",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def woo_webhook(
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Recibe eventos de WooCommerce, verifica HMAC y despacha al handler."""
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    with bypass_tenant_filter():
        config = db.get(ConnectorConfig, config_id)

    if config is None or config.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config no encontrada")

    connector = WooCommerceConnector(tenant_id, config_id, db=db)

    verification = connector.verify_webhook(body, headers)
    if not verification.valid:
        logger.warning(
            "woo_webhook: firma inválida para tenant=%s config=%s reason=%s",
            tenant_id,
            config_id,
            verification.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Firma inválida: {verification.reason}",
        )

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload no es JSON válido",
        )

    try:
        connector.webhook_handler(payload, headers)
    except Exception as exc:
        logger.exception(
            "woo_webhook: error en handler para tenant=%s config=%s topic=%s",
            tenant_id,
            config_id,
            headers.get("x-wc-webhook-topic", "?"),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return {"received": True}
