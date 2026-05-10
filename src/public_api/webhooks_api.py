"""API REST /v1/webhooks — gestión de webhooks salientes por tenant (Fase 10).

Endpoints:
- POST   /v1/webhooks           — registra un nuevo webhook.
- GET    /v1/webhooks           — lista los webhooks del tenant.
- GET    /v1/webhooks/{id}      — detalle de un webhook.
- PUT    /v1/webhooks/{id}      — actualiza url/events/enabled/secret.
- DELETE /v1/webhooks/{id}      — elimina el webhook y sus entregas.
- GET    /v1/webhooks/{id}/deliveries — historial de entregas recientes.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.db.models import User
from src.db.session import get_db
from src.public_api.models import WebhookDelivery, WebhookOut

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_EVENTOS_VALIDOS = {
    "message.received",
    "message.sent",
    "conversation.escalated",
    "conversation.closed",
}


# ── Schemas ──────────────────────────────────────────────────────────────────


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: list[str]
    enabled: bool = True
    secret: str | None = None  # auto-generado si es None


class WebhookUpdate(BaseModel):
    url: HttpUrl | None = None
    events: list[str] | None = None
    enabled: bool | None = None
    secret: str | None = None


class WebhookOut_(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    url: str
    events: list[str]
    enabled: bool
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookCreated(WebhookOut_):
    """En creación se expone el secreto generado (única vez si fue auto-generado)."""
    secret: str


class DeliveryOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    webhook_id: uuid.UUID
    event: str
    status: str
    attempts: int
    last_response_status: int | None
    last_response_body: str | None
    next_attempt_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _validate_events(events: list[str]) -> None:
    invalidos = set(events) - _EVENTOS_VALIDOS
    if invalidos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Eventos inválidos: {sorted(invalidos)}. Válidos: {sorted(_EVENTOS_VALIDOS)}",
        )
    if not events:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Se requiere al menos un evento.",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
def crear_webhook(
    body: WebhookCreate,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("owner", "admin")),
):
    """Registra un webhook saliente para el tenant.

    Si no se provee ``secret``, se genera uno automáticamente y se devuelve
    en la respuesta (única vez). Guardarlo para verificar las firmas HMAC.
    """
    _validate_events(body.events)

    secret = body.secret or secrets.token_hex(32)
    url_str = str(body.url)

    webhook = WebhookOut(
        tenant_id=current_user.tenant_id,
        url=url_str,
        secret=secret,
        events=body.events,
        enabled=body.enabled,
    )
    db.add(webhook)
    db.flush()

    return WebhookCreated(
        id=webhook.id,
        tenant_id=webhook.tenant_id,
        url=webhook.url,
        secret=secret,
        events=webhook.events,
        enabled=webhook.enabled,
        consecutive_failures=webhook.consecutive_failures,
        last_success_at=webhook.last_success_at,
        last_failure_at=webhook.last_failure_at,
        created_at=webhook.created_at,
    )


@router.get("", response_model=list[WebhookOut_])
def listar_webhooks(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista los webhooks del tenant autenticado."""
    return (
        db.query(WebhookOut)
        .filter(WebhookOut.tenant_id == current_user.tenant_id)
        .order_by(WebhookOut.created_at.desc())
        .all()
    )


@router.get("/{webhook_id}", response_model=WebhookOut_)
def obtener_webhook(
    webhook_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Obtiene el detalle de un webhook del tenant."""
    wh = (
        db.query(WebhookOut)
        .filter(
            WebhookOut.id == webhook_id,
            WebhookOut.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook no encontrado.")
    return wh


@router.put("/{webhook_id}", response_model=WebhookOut_)
def actualizar_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("owner", "admin")),
):
    """Actualiza url, eventos, estado o secreto de un webhook."""
    wh = (
        db.query(WebhookOut)
        .filter(
            WebhookOut.id == webhook_id,
            WebhookOut.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook no encontrado.")

    if body.url is not None:
        wh.url = str(body.url)
    if body.events is not None:
        _validate_events(body.events)
        wh.events = body.events
    if body.enabled is not None:
        wh.enabled = body.enabled
        if body.enabled:
            wh.consecutive_failures = 0
    if body.secret is not None:
        wh.secret = body.secret

    db.flush()
    return wh


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_webhook(
    webhook_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("owner", "admin")),
):
    """Elimina un webhook y todas sus entregas en cascada."""
    wh = (
        db.query(WebhookOut)
        .filter(
            WebhookOut.id == webhook_id,
            WebhookOut.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook no encontrado.")
    db.delete(wh)
    db.flush()


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
def listar_entregas(
    webhook_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = 50,
):
    """Lista las últimas entregas de un webhook (máx 200)."""
    wh = (
        db.query(WebhookOut)
        .filter(
            WebhookOut.id == webhook_id,
            WebhookOut.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not wh:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook no encontrado.")

    limit = min(limit, 200)
    return (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.tenant_id == current_user.tenant_id,
        )
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .all()
    )
