"""API REST del inbox humano (Fase 8).

Endpoints:
- GET    /v1/inbox                               — listar conversaciones (filtros status, wa_number_id)
- GET    /v1/inbox/{conversation_id}             — detalle con historial + tool_invocations
- POST   /v1/inbox/{conversation_id}/take        — asignarse la conversación (status=agent)
- POST   /v1/inbox/{conversation_id}/reply       — enviar mensaje como agente (outbound)
- POST   /v1/inbox/{conversation_id}/close       — devolver al bot (status=bot)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import column, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Session

from src.agent.models import ToolInvocation
from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.inbox.models import HandoffEvent
from src.messaging import dispatcher
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id
from src.wa.models import WaConversation, WaMessage, WaNumber

router = APIRouter(prefix="/inbox", tags=["inbox"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class HandoffEventOut(BaseModel):
    id: uuid.UUID
    motivo: str
    agent_user_id: uuid.UUID | None
    opened_at: datetime
    closed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationSummary(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    wa_number_id: uuid.UUID
    wa_contact_phone: str
    wa_contact_name: str
    status: str
    assigned_user_id: uuid.UUID | None = None
    last_message_at: datetime | None
    turn_count: int
    ai_summary: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    text: str
    ack: str
    sent_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolInvocationOut(BaseModel):
    id: uuid.UUID
    tool_name: str
    input: dict
    output: dict
    status: str
    latency_ms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[MessageOut]
    tool_invocations: list[ToolInvocationOut]
    handoff_events: list[HandoffEventOut]


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int


class ReplyRequest(BaseModel):
    text: str


class ReplyResponse(BaseModel):
    message_id: uuid.UUID
    wa_message_id: str
    success: bool


class SearchMessageOut(BaseModel):
    id: uuid.UUID
    wa_conversation_id: uuid.UUID
    direction: str
    text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SearchResultItem(BaseModel):
    message: SearchMessageOut
    context: list[SearchMessageOut]


class SearchResponse(BaseModel):
    total: int
    results: list[SearchResultItem]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_conversation(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> WaConversation:
    with bypass_tenant_filter():
        conv = (
            db.query(WaConversation)
            .filter(
                WaConversation.id == conversation_id,
                WaConversation.tenant_id == tenant_id,
            )
            .first()
        )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversación no encontrada",
        )
    return conv


def _get_open_handoff(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> HandoffEvent | None:
    """Devuelve el HandoffEvent abierto más reciente, o None."""
    with bypass_tenant_filter():
        return (
            db.query(HandoffEvent)
            .filter(
                HandoffEvent.wa_conversation_id == conversation_id,
                HandoffEvent.tenant_id == tenant_id,
                HandoffEvent.closed_at.is_(None),
            )
            .order_by(HandoffEvent.opened_at.desc())
            .first()
        )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/search", response_model=SearchResponse)
def search_messages(
    q: str | None = Query(None, description="Texto a buscar (obligatorio)"),
    conversation_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Búsqueda full-text de mensajes en el inbox del tenant.

    Devuelve mensajes que hacen match con ``q`` más ±2 mensajes de contexto.
    """
    if not q or not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El parámetro 'q' es obligatorio y no puede estar vacío.",
        )

    tenant_id = get_current_tenant_id()

    # Referencia a la columna generada body_tsv (GIN index, no en ORM).
    body_tsv_col = column("body_tsv", TSVECTOR)
    tsq = func.plainto_tsquery("spanish", q)

    with bypass_tenant_filter():
        base_q = db.query(WaMessage).filter(
            WaMessage.tenant_id == tenant_id,
            body_tsv_col.op("@@")(tsq),
        )

        if conversation_id is not None:
            # Verificar que la conversación pertenece al tenant.
            conv_check = (
                db.query(WaConversation)
                .filter(
                    WaConversation.id == conversation_id,
                    WaConversation.tenant_id == tenant_id,
                )
                .first()
            )
            if conv_check is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversación no encontrada.",
                )
            base_q = base_q.filter(WaMessage.wa_conversation_id == conversation_id)

        if date_from is not None:
            from datetime import timezone
            df_dt = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
            base_q = base_q.filter(WaMessage.created_at >= df_dt)

        if date_to is not None:
            from datetime import timezone
            dt_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
            base_q = base_q.filter(WaMessage.created_at <= dt_dt)

        total = base_q.count()
        matched = (
            base_q.order_by(WaMessage.created_at.desc())
            .limit(limit)
            .all()
        )

        results: list[SearchResultItem] = []
        for msg in matched:
            before = (
                db.query(WaMessage)
                .filter(
                    WaMessage.wa_conversation_id == msg.wa_conversation_id,
                    WaMessage.tenant_id == tenant_id,
                    WaMessage.created_at < msg.created_at,
                )
                .order_by(WaMessage.created_at.desc())
                .limit(2)
                .all()
            )
            after = (
                db.query(WaMessage)
                .filter(
                    WaMessage.wa_conversation_id == msg.wa_conversation_id,
                    WaMessage.tenant_id == tenant_id,
                    WaMessage.created_at > msg.created_at,
                )
                .order_by(WaMessage.created_at.asc())
                .limit(2)
                .all()
            )
            context = sorted(list(reversed(before)) + after, key=lambda m: m.created_at)
            results.append(
                SearchResultItem(
                    message=SearchMessageOut.model_validate(msg),
                    context=[SearchMessageOut.model_validate(c) for c in context],
                )
            )

    return SearchResponse(total=total, results=results)


@router.get("", response_model=ConversationListResponse)
def list_inbox(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filtrar por status: waiting_agent | agent | bot | closed",
    ),
    wa_number_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationListResponse:
    """Lista conversaciones del tenant con filtros opcionales."""
    tenant_id = get_current_tenant_id()

    with bypass_tenant_filter():
        q = db.query(WaConversation).filter(
            WaConversation.tenant_id == tenant_id
        )

    if status_filter:
        q = q.filter(WaConversation.status == status_filter)

    if wa_number_id:
        q = q.filter(WaConversation.wa_number_id == wa_number_id)

    q = q.order_by(WaConversation.last_message_at.desc().nullslast())

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    return ConversationListResponse(items=items, total=total)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    """Devuelve detalle de una conversación con historial, tools y handoff events."""
    tenant_id = get_current_tenant_id()
    conv = _get_conversation(conversation_id, tenant_id, db)

    with bypass_tenant_filter():
        messages = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conversation_id,
                WaMessage.tenant_id == tenant_id,
            )
            .order_by(WaMessage.created_at.asc())
            .all()
        )

        tool_invocations = (
            db.query(ToolInvocation)
            .filter(
                ToolInvocation.wa_conversation_id == conversation_id,
                ToolInvocation.tenant_id == tenant_id,
            )
            .order_by(ToolInvocation.created_at.asc())
            .all()
        )

        handoff_events = (
            db.query(HandoffEvent)
            .filter(
                HandoffEvent.wa_conversation_id == conversation_id,
                HandoffEvent.tenant_id == tenant_id,
            )
            .order_by(HandoffEvent.opened_at.asc())
            .all()
        )

    return ConversationDetail(
        conversation=conv,
        messages=messages,
        tool_invocations=tool_invocations,
        handoff_events=handoff_events,
    )


@router.post("/{conversation_id}/take", response_model=ConversationSummary)
def take_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WaConversation:
    """Asigna la conversación al usuario actual y la pone en status=agent.

    Actualiza el HandoffEvent abierto con el agent_user_id.
    Si la conversación ya está en status=agent (asignada a otro), devuelve 409.
    """
    tenant_id = get_current_tenant_id()
    conv = _get_conversation(conversation_id, tenant_id, db)

    if conv.status == "bot":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación está en modo bot, no requiere asignación.",
        )
    if conv.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación está cerrada.",
        )
    if conv.status == "agent":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación ya tiene un agente asignado.",
        )

    conv.status = "agent"
    conv.assigned_user_id = current_user.id
    db.add(conv)

    handoff = _get_open_handoff(conversation_id, tenant_id, db)
    if handoff is None:
        handoff = HandoffEvent(
            tenant_id=tenant_id,
            wa_conversation_id=conversation_id,
            motivo="asignado_manualmente",
            opened_at=datetime.now(UTC),
        )
        db.add(handoff)
    handoff.agent_user_id = current_user.id

    db.commit()
    db.refresh(conv)

    # Webhook saliente conversation.status_changed (Fase 24A) — fire-and-forget.
    try:
        from src.public_api.dispatcher import emit_event as _emit
        _emit(
            tenant_id,
            "conversation.status_changed",
            {
                "conversation_id": str(conv.id),
                "tenant_id": str(tenant_id),
                "new_status": conv.status,
                "wa_contact_phone": conv.wa_contact_phone,
                "assigned_user_id": str(current_user.id),
            },
            db,
        )
        db.commit()
    except Exception:
        pass

    return conv


@router.post("/{conversation_id}/assign", response_model=ConversationSummary)
def assign_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WaConversation:
    """Asigna la conversación al usuario actual (status=agent).

    Equivalente a /take; usa el nombre canónico del plan (Fase 8).
    """
    return take_conversation(conversation_id, current_user=current_user, db=db)


@router.post("/{conversation_id}/reply", response_model=ReplyResponse)
def reply_conversation(
    conversation_id: uuid.UUID,
    body: ReplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReplyResponse:
    """Envía un mensaje outbound como agente humano.

    Permitido en status=agent o status=waiting_agent (para que el agente
    pueda responder sin haber hecho take). Si el status era waiting_agent,
    lo promueve automáticamente a agent y asigna al usuario actual.
    """
    tenant_id = get_current_tenant_id()
    conv = _get_conversation(conversation_id, tenant_id, db)

    if conv.status == "bot":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación está en modo bot; el agente no interviene aquí.",
        )
    if conv.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación está cerrada.",
        )
    if not body.text or not body.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El texto no puede estar vacío.",
        )

    # Si estaba en waiting_agent, lo promovemos a agent.
    if conv.status == "waiting_agent":
        conv.status = "agent"
        conv.assigned_user_id = current_user.id
        db.add(conv)
        handoff = _get_open_handoff(conversation_id, tenant_id, db)
        if handoff and handoff.agent_user_id is None:
            handoff.agent_user_id = current_user.id
        db.flush()

    with bypass_tenant_filter():
        wn = db.query(WaNumber).filter(WaNumber.id == conv.wa_number_id).first()
    if wn is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WaNumber no encontrado.",
        )

    result = dispatcher.send_text(
        db,
        tenant_id,
        conv.wa_contact_phone,
        body.text,
        wa_number=wn,
        conversation=conv,
    )

    now = datetime.now(UTC)
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="out",
        text=body.text,
        wa_message_id=result.wa_message_id,
        ack="sent" if result.success else "",
        error="" if result.success else result.error,
        raw_payload=result.raw_response or {},
        llm_metadata={},
    )
    if result.success:
        msg.sent_at = now
    else:
        msg.ack = "failed"
        msg.failed_at = now

    conv.last_message_at = now
    db.add(msg)
    db.add(conv)
    db.commit()
    db.refresh(msg)

    # Broadcast WS a clientes conectados al inbox de esta conversación (Fase 23A).
    try:
        from src.messaging.ws_manager import manager as ws_manager
        ws_manager.broadcast_from_sync(
            conv.id,
            {
                "event": "message",
                "message_id": str(msg.id),
                "direction": msg.direction,
                "text": body.text,
                "source": "agent",
                "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            },
        )
    except Exception:
        pass

    # Webhook saliente message.sent (Fase 24A) — fire-and-forget.
    try:
        from src.public_api.dispatcher import emit_event as _emit
        _emit(
            tenant_id,
            "message.sent",
            {
                "conversation_id": str(conv.id),
                "tenant_id": str(tenant_id),
                "message_id": str(msg.id),
                "wa_contact_phone": conv.wa_contact_phone,
                "text": body.text,
                "source": "agent",
            },
            db,
        )
        db.commit()
    except Exception:
        pass

    return ReplyResponse(
        message_id=msg.id,
        wa_message_id=result.wa_message_id,
        success=result.success,
    )


@router.post("/{conversation_id}/close", response_model=ConversationSummary)
def close_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WaConversation:
    """Devuelve la conversación al bot (status=bot) y cierra el HandoffEvent."""
    tenant_id = get_current_tenant_id()
    conv = _get_conversation(conversation_id, tenant_id, db)

    if conv.status == "bot":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación ya está en modo bot.",
        )

    conv.status = "bot"
    conv.assigned_user_id = None
    db.add(conv)

    handoff = _get_open_handoff(conversation_id, tenant_id, db)
    if handoff is not None:
        handoff.closed_at = datetime.now(UTC)

    db.commit()
    db.refresh(conv)

    # Webhook saliente conversation.status_changed (Fase 24A) — fire-and-forget.
    try:
        from src.public_api.dispatcher import emit_event as _emit
        _emit(
            tenant_id,
            "conversation.status_changed",
            {
                "conversation_id": str(conv.id),
                "tenant_id": str(tenant_id),
                "new_status": conv.status,
                "wa_contact_phone": conv.wa_contact_phone,
            },
            db,
        )
        db.commit()
    except Exception:
        pass

    # Tarea de resumen automático si > 5 turnos (Fase 24D) — no bloquea.
    try:
        from src.agent.tasks import summarize_on_close as _summarize
        turn_count = conv.turn_count or 0
        if turn_count > 5:
            _summarize.delay(str(conv.id))
    except Exception:
        pass

    return conv
