"""API REST del inbox humano.

Endpoints:
- GET    /v1/inbox/sla-report                             — reporte SLA (Fase 25A)
- GET    /v1/inbox/search                                 — búsqueda full-text (Fase 24C)
- GET    /v1/inbox                                        — listar conversaciones
- GET    /v1/inbox/{conversation_id}                      — detalle (incluye notes_count)
- POST   /v1/inbox/{conversation_id}/take                 — asignarse la conversación
- POST   /v1/inbox/{conversation_id}/reply                — enviar mensaje como agente
- POST   /v1/inbox/{conversation_id}/close                — devolver al bot
- POST   /v1/inbox/{conversation_id}/tags                 — añadir etiqueta (Fase 25B)
- DELETE /v1/inbox/{conversation_id}/tags/{tag}           — quitar etiqueta (Fase 25B)
- POST   /v1/inbox/{conversation_id}/notes                — crear nota interna (Fase 26B)
- GET    /v1/inbox/{conversation_id}/notes                — listar notas (Fase 26B)
- DELETE /v1/inbox/{conversation_id}/notes/{note_id}      — eliminar nota (Fase 26B, solo autor)
- GET    /v1/inbox/{conversation_id}/status-history       — historial de status (Fase 26C)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import column, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Session

from src.agent.models import ToolInvocation
from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.inbox.models import (
    CannedResponse,
    ConversationNote,
    ConversationStatusHistory,
    ConversationTag,
    HandoffEvent,
)
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
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    tags: list[str] = []
    notes_count: int = 0
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


class SLAReportOut(BaseModel):
    date_from: date
    date_to: date
    total_conversations: int
    resolved_conversations: int
    avg_first_response_seconds: float | None
    avg_resolution_seconds: float | None
    p50_first_response_seconds: float | None
    p90_first_response_seconds: float | None
    p50_resolution_seconds: float | None
    p90_resolution_seconds: float | None


class TagOut(BaseModel):
    id: uuid.UUID
    tag: str
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TagCreateRequest(BaseModel):
    tag: str


# ── Schemas notas internas (Fase 26B) ─────────────────────────────────────────


class NoteCreateRequest(BaseModel):
    text: str


class NoteOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    wa_conversation_id: uuid.UUID
    user_id: uuid.UUID
    text: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Schemas historial de status (Fase 26C) ────────────────────────────────────


class StatusHistoryOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    wa_conversation_id: uuid.UUID
    old_status: str
    new_status: str
    changed_by_user_id: uuid.UUID | None
    changed_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_tags(
    conv_ids: list[uuid.UUID],
    tenant_id: uuid.UUID,
    db: Session,
) -> dict[uuid.UUID, list[str]]:
    """Devuelve {conv_id: [tag, ...]} para las conversaciones dadas."""
    if not conv_ids:
        return {}
    with bypass_tenant_filter():
        rows = (
            db.query(ConversationTag)
            .filter(
                ConversationTag.tenant_id == tenant_id,
                ConversationTag.wa_conversation_id.in_(conv_ids),
            )
            .all()
        )
    result: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        result.setdefault(row.wa_conversation_id, []).append(row.tag)
    return result


def _load_notes_count(
    conv_ids: list[uuid.UUID],
    tenant_id: uuid.UUID,
    db: Session,
) -> dict[uuid.UUID, int]:
    """Devuelve {conv_id: count} de notas para las conversaciones dadas."""
    if not conv_ids:
        return {}
    from sqlalchemy import func as sql_func
    with bypass_tenant_filter():
        rows = (
            db.query(
                ConversationNote.wa_conversation_id,
                sql_func.count(ConversationNote.id),
            )
            .filter(
                ConversationNote.tenant_id == tenant_id,
                ConversationNote.wa_conversation_id.in_(conv_ids),
            )
            .group_by(ConversationNote.wa_conversation_id)
            .all()
        )
    return {cid: cnt for cid, cnt in rows}


def _conv_summary(
    conv: WaConversation,
    tags: list[str],
    notes_count: int = 0,
) -> ConversationSummary:
    return ConversationSummary(
        id=conv.id,
        tenant_id=conv.tenant_id,
        wa_number_id=conv.wa_number_id,
        wa_contact_phone=conv.wa_contact_phone,
        wa_contact_name=conv.wa_contact_name,
        status=conv.status,
        assigned_user_id=conv.assigned_user_id,
        last_message_at=conv.last_message_at,
        turn_count=conv.turn_count,
        ai_summary=conv.ai_summary,
        first_response_at=conv.first_response_at,
        resolved_at=conv.resolved_at,
        tags=sorted(tags),
        notes_count=notes_count,
        created_at=conv.created_at,
    )


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


@router.get("/sla-report", response_model=SLAReportOut)
def get_sla_report(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SLAReportOut:
    """Reporte SLA del tenant: tiempos de primera respuesta y resolución."""
    from datetime import timedelta, timezone

    tenant_id = get_current_tenant_id()
    today = date.today()
    df = date_from or (today - timedelta(days=30))
    dt = date_to or today

    df_dt = datetime(df.year, df.month, df.day, tzinfo=timezone.utc)
    dt_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)

    with bypass_tenant_filter():
        convs = (
            db.query(WaConversation)
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.created_at >= df_dt,
                WaConversation.created_at <= dt_dt,
            )
            .all()
        )

    total = len(convs)
    resolved = [c for c in convs if c.resolved_at is not None]

    def _pct(values: list[float], p: float) -> float | None:
        if not values:
            return None
        values_sorted = sorted(values)
        idx = int(len(values_sorted) * p / 100)
        idx = min(idx, len(values_sorted) - 1)
        return round(values_sorted[idx], 2)

    first_resp_secs: list[float] = []
    for c in convs:
        if c.first_response_at is not None:
            delta = (c.first_response_at - c.created_at).total_seconds()
            if delta >= 0:
                first_resp_secs.append(delta)

    res_secs: list[float] = []
    for c in resolved:
        if c.resolved_at is not None:
            delta = (c.resolved_at - c.created_at).total_seconds()
            if delta >= 0:
                res_secs.append(delta)

    avg_first = round(sum(first_resp_secs) / len(first_resp_secs), 2) if first_resp_secs else None
    avg_res = round(sum(res_secs) / len(res_secs), 2) if res_secs else None

    return SLAReportOut(
        date_from=df,
        date_to=dt,
        total_conversations=total,
        resolved_conversations=len(resolved),
        avg_first_response_seconds=avg_first,
        avg_resolution_seconds=avg_res,
        p50_first_response_seconds=_pct(first_resp_secs, 50),
        p90_first_response_seconds=_pct(first_resp_secs, 90),
        p50_resolution_seconds=_pct(res_secs, 50),
        p90_resolution_seconds=_pct(res_secs, 90),
    )


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
    tag: str | None = Query(None, description="Filtrar por etiqueta"),
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

        if tag:
            q = q.filter(
                WaConversation.id.in_(
                    db.query(ConversationTag.wa_conversation_id).filter(
                        ConversationTag.tenant_id == tenant_id,
                        ConversationTag.tag == tag,
                    )
                )
            )

        q = q.order_by(WaConversation.last_message_at.desc().nullslast())
        total = q.count()
        items = q.offset(offset).limit(limit).all()

    conv_ids = [c.id for c in items]
    tags_map = _load_tags(conv_ids, tenant_id, db)
    notes_count_map = _load_notes_count(conv_ids, tenant_id, db)
    summaries = [
        _conv_summary(c, tags_map.get(c.id, []), notes_count_map.get(c.id, 0))
        for c in items
    ]
    return ConversationListResponse(items=summaries, total=total)


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

    tags_map = _load_tags([conversation_id], tenant_id, db)
    notes_count_map = _load_notes_count([conversation_id], tenant_id, db)
    return ConversationDetail(
        conversation=_conv_summary(conv, tags_map.get(conversation_id, []), notes_count_map.get(conversation_id, 0)),
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

    old_status = conv.status
    conv.status = "agent"
    conv.assigned_user_id = current_user.id
    db.add(conv)

    # Historial de status (Fase 26C).
    _record_status_change(db, tenant_id, conversation_id, old_status, "agent", current_user.id)

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
        _record_status_change(db, tenant_id, conversation_id, "waiting_agent", "agent", current_user.id)
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

    # SLA (Fase 25A): primera respuesta del agente.
    if conv.first_response_at is None:
        conv.first_response_at = now

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

    now = datetime.now(UTC)
    old_status = conv.status
    conv.status = "bot"
    conv.assigned_user_id = None
    # SLA (Fase 25A): marcar momento de resolución.
    conv.resolved_at = now
    db.add(conv)

    # Historial de status (Fase 26C).
    _record_status_change(db, tenant_id, conversation_id, old_status, "bot", current_user.id)

    handoff = _get_open_handoff(conversation_id, tenant_id, db)
    if handoff is not None:
        handoff.closed_at = now

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

    tags_map = _load_tags([conversation_id], tenant_id, db)
    notes_count_map = _load_notes_count([conversation_id], tenant_id, db)
    return _conv_summary(conv, tags_map.get(conversation_id, []), notes_count_map.get(conversation_id, 0))


# ── Tags (Fase 25B) ───────────────────────────────────────────────────────────


@router.post("/{conversation_id}/tags", response_model=TagOut, status_code=201)
def add_tag(
    conversation_id: uuid.UUID,
    body: TagCreateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TagOut:
    """Añade una etiqueta a la conversación.

    Idempotente: si la misma tag ya existe en la conversación devuelve 200.
    Si es nueva, devuelve 201.
    """

    tenant_id = get_current_tenant_id()
    tag_value = (body.tag or "").strip().lower()
    if not tag_value:
        raise HTTPException(status_code=422, detail="tag no puede estar vacío")
    if len(tag_value) > 64:
        raise HTTPException(status_code=422, detail="tag demasiado largo (máx 64 chars)")

    _get_conversation(conversation_id, tenant_id, db)

    with bypass_tenant_filter():
        existing = (
            db.query(ConversationTag)
            .filter(
                ConversationTag.tenant_id == tenant_id,
                ConversationTag.wa_conversation_id == conversation_id,
                ConversationTag.tag == tag_value,
            )
            .first()
        )
    if existing:
        response.status_code = status.HTTP_200_OK
        return TagOut.model_validate(existing)

    ct = ConversationTag(
        tenant_id=tenant_id,
        wa_conversation_id=conversation_id,
        tag=tag_value,
        created_by_user_id=current_user.id,
    )
    db.add(ct)
    db.commit()
    db.refresh(ct)
    return TagOut.model_validate(ct)


@router.delete("/{conversation_id}/tags/{tag}", status_code=status.HTTP_204_NO_CONTENT)
def remove_tag(
    conversation_id: uuid.UUID,
    tag: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Elimina una etiqueta de la conversación. 204 aunque no exista (idempotente)."""
    tenant_id = get_current_tenant_id()
    _get_conversation(conversation_id, tenant_id, db)

    tag_value = tag.strip().lower()
    with bypass_tenant_filter():
        ct = (
            db.query(ConversationTag)
            .filter(
                ConversationTag.tenant_id == tenant_id,
                ConversationTag.wa_conversation_id == conversation_id,
                ConversationTag.tag == tag_value,
            )
            .first()
        )
    if ct:
        db.delete(ct)
        db.commit()


# ── Notas internas (Fase 26B) ─────────────────────────────────────────────────


def _record_status_change(
    db: Session,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    old_status: str,
    new_status: str,
    changed_by_user_id: uuid.UUID | None,
) -> None:
    """Registra un cambio de status en conversation_status_history."""
    entry = ConversationStatusHistory(
        tenant_id=tenant_id,
        wa_conversation_id=conversation_id,
        old_status=old_status,
        new_status=new_status,
        changed_by_user_id=changed_by_user_id,
        changed_at=datetime.now(UTC),
    )
    db.add(entry)


@router.post("/{conversation_id}/notes", response_model=NoteOut, status_code=201)
def create_note(
    conversation_id: uuid.UUID,
    body: NoteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NoteOut:
    """Crea una nota interna en la conversación."""
    tenant_id = get_current_tenant_id()
    _get_conversation(conversation_id, tenant_id, db)

    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text no puede estar vacío")

    note = ConversationNote(
        tenant_id=tenant_id,
        wa_conversation_id=conversation_id,
        user_id=current_user.id,
        text=body.text.strip(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return NoteOut.model_validate(note)


@router.get("/{conversation_id}/notes", response_model=list[NoteOut])
def list_notes(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NoteOut]:
    """Lista todas las notas internas de la conversación."""
    tenant_id = get_current_tenant_id()
    _get_conversation(conversation_id, tenant_id, db)

    with bypass_tenant_filter():
        notes = (
            db.query(ConversationNote)
            .filter(
                ConversationNote.tenant_id == tenant_id,
                ConversationNote.wa_conversation_id == conversation_id,
            )
            .order_by(ConversationNote.created_at.asc())
            .all()
        )
    return [NoteOut.model_validate(n) for n in notes]


@router.delete("/{conversation_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    conversation_id: uuid.UUID,
    note_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Elimina una nota interna. Solo el autor puede borrar su nota."""
    tenant_id = get_current_tenant_id()
    _get_conversation(conversation_id, tenant_id, db)

    with bypass_tenant_filter():
        note = (
            db.query(ConversationNote)
            .filter(
                ConversationNote.id == note_id,
                ConversationNote.tenant_id == tenant_id,
                ConversationNote.wa_conversation_id == conversation_id,
            )
            .first()
        )
    if note is None:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if note.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Solo el autor puede eliminar su nota")
    db.delete(note)
    db.commit()


# ── Historial de status (Fase 26C) ────────────────────────────────────────────


@router.get("/{conversation_id}/status-history", response_model=list[StatusHistoryOut])
def get_status_history(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StatusHistoryOut]:
    """Devuelve el historial de cambios de status de la conversación, ordenado por fecha."""
    tenant_id = get_current_tenant_id()
    _get_conversation(conversation_id, tenant_id, db)

    with bypass_tenant_filter():
        history = (
            db.query(ConversationStatusHistory)
            .filter(
                ConversationStatusHistory.tenant_id == tenant_id,
                ConversationStatusHistory.wa_conversation_id == conversation_id,
            )
            .order_by(ConversationStatusHistory.changed_at.asc())
            .all()
        )
    return [StatusHistoryOut.model_validate(h) for h in history]
