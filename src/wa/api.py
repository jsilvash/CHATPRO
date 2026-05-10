"""Endpoints REST para gestión de números WhatsApp del tenant.

- ``POST   /v1/wa-numbers``                    — alta de número + sesión WAHA.
- ``GET    /v1/wa-numbers``                    — listado del tenant.
- ``GET    /v1/wa-numbers/{id}``               — detalle + status WAHA actual.
- ``GET    /v1/wa-numbers/{id}/qr``            — QR base64 (si aplica).
- ``POST   /v1/wa-numbers/{id}/pairing-code``  — código de 8 chars en lugar de QR.
- ``POST   /v1/wa-numbers/{id}/send``          — envía texto a un destinatario.
- ``POST   /v1/wa-numbers/{id}/logout``        — cierra sesión WAHA.
- ``GET    /v1/wa-numbers/{id}/metrics``        — métricas de mensajes/conversaciones por período.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.billing.quota import check_quota
from src.config import get_settings
from src.db.models import User
from src.db.session import get_db
from src.messaging import dispatcher, waha_client
from src.messaging.waha_client import WahaAPIError
from src.tenancy.context import get_current_tenant_id
from src.wa.models import WaConversation, WaMessage, WaNumber, WaSession

router = APIRouter(prefix="/wa-numbers", tags=["wa-numbers"])


# ────────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────────


_SESSION_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{1,62}[a-zA-Z0-9]$")


class WaNumberCreate(BaseModel):
    label: str
    waha_session_name: str
    waha_node_id: str = "default"
    tags: list[str] = []
    is_default: bool = False

    @field_validator("waha_session_name")
    @classmethod
    def validate_session_name(cls, v: str) -> str:
        if not _SESSION_NAME_RE.match(v):
            raise ValueError(
                "waha_session_name solo admite letras/dígitos/_/- (3-64 chars)"
            )
        return v


class WaNumberResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    label: str
    waha_session_name: str
    waha_node_id: str
    phone: str
    tags: list[str]
    is_default: bool
    active: bool
    created_at: datetime
    session_status: str = ""

    model_config = {"from_attributes": True}


class WaNumberListResponse(BaseModel):
    items: list[WaNumberResponse]
    total: int


class SendTextRequest(BaseModel):
    to: str
    text: str


class SendTextResponse(BaseModel):
    success: bool
    wa_message_id: str = ""
    error: str = ""
    message_id: uuid.UUID | None = None


class QrResponse(BaseModel):
    status: str
    qr_base64: str = ""


class PairingCodeRequest(BaseModel):
    phone_number: str


class PairingCodeResponse(BaseModel):
    code: str


class TopContactEntry(BaseModel):
    phone: str
    count: int


class WaNumberMetricsOut(BaseModel):
    wa_number_id: uuid.UUID
    date_from: date
    date_to: date
    messages_in: int
    messages_out: int
    conversations_total: int
    conversations_active: int
    top_contacts: list[TopContactEntry]


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _ensure_owned(db: Session, wa_number_id: uuid.UUID, tenant_id: uuid.UUID) -> WaNumber:
    wn = (
        db.query(WaNumber)
        .filter(WaNumber.id == wa_number_id, WaNumber.tenant_id == tenant_id)
        .first()
    )
    if wn is None:
        raise HTTPException(status_code=404, detail="Número no encontrado")
    return wn


def _build_response(wn: WaNumber, sess_status: str = "") -> WaNumberResponse:
    return WaNumberResponse(
        id=wn.id,
        tenant_id=wn.tenant_id,
        label=wn.label,
        waha_session_name=wn.waha_session_name,
        waha_node_id=wn.waha_node_id,
        phone=wn.phone,
        tags=list(wn.tags or []),
        is_default=wn.is_default,
        active=wn.active,
        created_at=wn.created_at,
        session_status=sess_status,
    )


def _waha_session_config_for(wa_number_id: uuid.UUID) -> dict[str, Any]:
    """Config WAHA con webhook URL y events apuntando a este wa_number."""
    settings = get_settings()
    base_url = (settings.public_base_url or "").rstrip("/")
    webhook_url = f"{base_url}/webhook/waha/{wa_number_id}"

    webhook: dict[str, Any] = {
        "url": webhook_url,
        "events": ["message", "message.any", "message.ack", "session.status"],
    }
    token = (settings.waha_webhook_token or "").strip()
    if token:
        webhook["customHeaders"] = [{"name": "X-WAHA-Token", "value": token}]

    return {
        "noweb": {"store": {"enabled": True, "fullSync": True}},
        "webhooks": [webhook],
    }


# ────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────


@router.post("", response_model=WaNumberResponse, status_code=201)
def create_wa_number(
    payload: WaNumberCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Crea un ``WaNumber`` + sesión WAHA y devuelve el detalle.

    El frontend después hace polling a ``GET /qr`` hasta que la sesión esté
    ``WORKING``. La sesión WAHA se inicia automáticamente.
    """
    tenant_id = get_current_tenant_id()

    existing = (
        db.query(WaNumber)
        .filter(WaNumber.waha_session_name == payload.waha_session_name)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="waha_session_name ya está en uso (debe ser único global)",
        )

    wn = WaNumber(
        tenant_id=tenant_id,
        label=payload.label,
        waha_session_name=payload.waha_session_name,
        waha_node_id=payload.waha_node_id or "default",
        tags=payload.tags or [],
        is_default=payload.is_default,
    )
    db.add(wn)
    db.flush()

    # Crear sesión WAHA con webhooks configurados.
    try:
        waha_client.create_session(
            payload.waha_session_name,
            config=_waha_session_config_for(wn.id),
        )
    except WahaAPIError as e:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"WAHA error: [{e.status}] {e.message}"
        )

    sess = WaSession(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        status="STARTING",
    )
    db.add(sess)
    db.commit()
    db.refresh(wn)
    return _build_response(wn, sess_status="STARTING")


@router.get("", response_model=WaNumberListResponse)
def list_wa_numbers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    items = (
        db.query(WaNumber)
        .filter(WaNumber.tenant_id == tenant_id, WaNumber.active.is_(True))
        .order_by(WaNumber.created_at.desc())
        .all()
    )
    sessions = {
        s.wa_number_id: s.status
        for s in db.query(WaSession)
        .filter(WaSession.tenant_id == tenant_id)
        .all()
    }
    return WaNumberListResponse(
        items=[_build_response(wn, sessions.get(wn.id, "")) for wn in items],
        total=len(items),
    )


@router.get("/{wa_number_id}", response_model=WaNumberResponse)
def get_wa_number(
    wa_number_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)
    sess = db.query(WaSession).filter(WaSession.wa_number_id == wn.id).first()
    return _build_response(wn, sess_status=sess.status if sess else "")


@router.get("/{wa_number_id}/qr", response_model=QrResponse)
def get_wa_number_qr(
    wa_number_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Devuelve el QR base64 actual.

    Si el último ``session.status`` traía QR ya lo tenemos persistido. Si no,
    lo pedimos a WAHA en vivo.
    """
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)
    sess = db.query(WaSession).filter(WaSession.wa_number_id == wn.id).first()

    if sess and sess.qr_data_b64 and sess.status == "SCAN_QR_CODE":
        return QrResponse(status=sess.status, qr_base64=sess.qr_data_b64)

    # Si la sesión está WORKING, no hay QR.
    if sess and sess.status == "WORKING":
        return QrResponse(status="WORKING", qr_base64="")

    # Pull en vivo a WAHA.
    try:
        b64 = waha_client.get_qr(wn.waha_session_name)
    except WahaAPIError as e:
        raise HTTPException(
            status_code=502, detail=f"WAHA error: [{e.status}] {e.message}"
        )
    return QrResponse(
        status=sess.status if sess else "STARTING",
        qr_base64=b64,
    )


@router.post(
    "/{wa_number_id}/pairing-code", response_model=PairingCodeResponse
)
def request_pairing_code(
    wa_number_id: uuid.UUID,
    payload: PairingCodeRequest,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Solicita pairing code de 8 chars en vez de QR."""
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)
    from src.utils.phone import normalize_phone
    phone = normalize_phone(payload.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="phone_number inválido")
    try:
        code = waha_client.request_pairing_code(wn.waha_session_name, phone)
    except WahaAPIError as e:
        raise HTTPException(
            status_code=502, detail=f"WAHA error: [{e.status}] {e.message}"
        )
    sess = db.query(WaSession).filter(WaSession.wa_number_id == wn.id).first()
    if sess:
        sess.pairing_code = code
        db.commit()
    return PairingCodeResponse(code=code)


@router.post("/{wa_number_id}/send", response_model=SendTextResponse)
def send_text(
    wa_number_id: uuid.UUID,
    payload: SendTextRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Envía texto desde este ``WaNumber`` al destinatario indicado.

    Persiste el ``WaMessage`` outbound con ACK ``""`` (queda pendiente);
    los webhooks ``message.ack`` lo van avanzando.
    """
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)

    check_quota(tenant_id, "messages_out", 1, db)

    # Resolver/crear conversación para este destinatario.
    from src.utils.phone import normalize_phone
    phone = normalize_phone(payload.to)
    if not phone:
        raise HTTPException(status_code=400, detail="campo 'to' inválido")

    conv = (
        db.query(WaConversation)
        .filter(
            WaConversation.tenant_id == tenant_id,
            WaConversation.wa_number_id == wn.id,
            WaConversation.wa_contact_phone == phone,
        )
        .first()
    )
    if conv is None:
        conv = WaConversation(
            tenant_id=tenant_id,
            wa_number_id=wn.id,
            wa_contact_phone=phone,
        )
        db.add(conv)
        db.flush()

    result = dispatcher.send_text(
        db, tenant_id, phone, payload.text,
        wa_number=wn, conversation=conv,
    )

    # Persistir el outbound siempre — failures incluidos (para auditoría).
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="out",
        text=payload.text,
        wa_message_id=result.wa_message_id,
        ack="sent" if result.success else "",
        error="" if result.success else result.error,
        raw_payload=result.raw_response or {},
    )
    if result.success:
        from datetime import datetime
        msg.sent_at = datetime.now(UTC)
    else:
        from datetime import datetime
        msg.failed_at = datetime.now(UTC)
        msg.ack = "failed"
    db.add(msg)
    db.commit()
    db.refresh(msg)

    if not result.success:
        return SendTextResponse(
            success=False,
            error=result.error,
            message_id=msg.id,
        )
    return SendTextResponse(
        success=True,
        wa_message_id=result.wa_message_id,
        message_id=msg.id,
    )


@router.get("/{wa_number_id}/metrics", response_model=WaNumberMetricsOut)
def get_wa_number_metrics(
    wa_number_id: uuid.UUID,
    date_from: date | None = Query(None, description="Inicio del período (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Fin del período (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WaNumberMetricsOut:
    """Devuelve métricas de mensajes y conversaciones para un WaNumber en un período."""
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)

    today = date.today()
    df = date_from or (today - timedelta(days=30))
    dt = date_to or today

    from sqlalchemy import func, and_
    from datetime import datetime, timezone

    df_dt = datetime(df.year, df.month, df.day, tzinfo=timezone.utc)
    dt_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)

    msgs_in = (
        db.query(func.count(WaMessage.id))
        .filter(
            WaMessage.wa_number_id == wn.id,
            WaMessage.tenant_id == tenant_id,
            WaMessage.direction == "in",
            WaMessage.created_at >= df_dt,
            WaMessage.created_at <= dt_dt,
        )
        .scalar()
        or 0
    )
    msgs_out = (
        db.query(func.count(WaMessage.id))
        .filter(
            WaMessage.wa_number_id == wn.id,
            WaMessage.tenant_id == tenant_id,
            WaMessage.direction == "out",
            WaMessage.created_at >= df_dt,
            WaMessage.created_at <= dt_dt,
        )
        .scalar()
        or 0
    )
    convs_total = (
        db.query(func.count(WaConversation.id))
        .filter(
            WaConversation.wa_number_id == wn.id,
            WaConversation.tenant_id == tenant_id,
            WaConversation.created_at >= df_dt,
            WaConversation.created_at <= dt_dt,
        )
        .scalar()
        or 0
    )
    convs_active = (
        db.query(func.count(WaConversation.id))
        .filter(
            WaConversation.wa_number_id == wn.id,
            WaConversation.tenant_id == tenant_id,
            WaConversation.status != "closed",
            WaConversation.created_at >= df_dt,
            WaConversation.created_at <= dt_dt,
        )
        .scalar()
        or 0
    )

    # Top 5 contactos por mensajes entrantes en el período.
    top_rows = (
        db.query(WaMessage.wa_conversation_id, func.count(WaMessage.id).label("cnt"))
        .filter(
            WaMessage.wa_number_id == wn.id,
            WaMessage.tenant_id == tenant_id,
            WaMessage.direction == "in",
            WaMessage.created_at >= df_dt,
            WaMessage.created_at <= dt_dt,
        )
        .group_by(WaMessage.wa_conversation_id)
        .order_by(func.count(WaMessage.id).desc())
        .limit(5)
        .all()
    )
    conv_ids = [r[0] for r in top_rows]
    conv_phone_map = {}
    if conv_ids:
        convs = (
            db.query(WaConversation)
            .filter(
                WaConversation.id.in_(conv_ids),
                WaConversation.tenant_id == tenant_id,
            )
            .all()
        )
        conv_phone_map = {c.id: c.wa_contact_phone for c in convs}

    top_contacts = [
        TopContactEntry(phone=conv_phone_map.get(r[0], ""), count=r[1])
        for r in top_rows
        if conv_phone_map.get(r[0])
    ]

    return WaNumberMetricsOut(
        wa_number_id=wn.id,
        date_from=df,
        date_to=dt,
        messages_in=msgs_in,
        messages_out=msgs_out,
        conversations_total=convs_total,
        conversations_active=convs_active,
        top_contacts=top_contacts,
    )


@router.post("/{wa_number_id}/logout", status_code=204)
def logout_wa_number(
    wa_number_id: uuid.UUID,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Cierra la sesión WAHA. El número queda inactivo hasta nuevo escaneo de QR."""
    tenant_id = get_current_tenant_id()
    wn = _ensure_owned(db, wa_number_id, tenant_id)
    try:
        waha_client.logout_session(wn.waha_session_name)
    except WahaAPIError:
        # No raise — el chip queda inactivo igual; el operador puede borrar manualmente.
        pass

    sess = db.query(WaSession).filter(WaSession.wa_number_id == wn.id).first()
    if sess:
        sess.status = "STOPPED"
        sess.qr_data_b64 = ""
        sess.pairing_code = ""
    db.commit()
    return None
