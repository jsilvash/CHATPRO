import uuid
from datetime import UTC, date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.auth.passwords import hash_password
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id
from src.wa.models import WaConversation

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""
    role: str = "agent"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("owner", "admin", "agent"):
            raise ValueError("Rol inválido. Opciones: owner, admin, agent")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        return v


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("owner", "admin", "agent"):
            raise ValueError("Rol inválido. Opciones: owner, admin, agent")
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int


# ── Agentes disponibles — schemas (Fase 26A) ─────────────────────────────────


class AvailableAgentOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    conv_count: int


class AvailableAgentsResponse(BaseModel):
    items: list[AvailableAgentOut]
    total: int


# ── Stats de usuario — schemas (Fase 27C) ─────────────────────────────────────


class UserStatsOut(BaseModel):
    user_id: uuid.UUID
    conversations_active: int
    conversations_today: int
    avg_first_response_sec: float | None
    notes_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=UserListResponse)
def list_users(
    q: str | None = Query(None, description="Buscar por nombre o email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    query = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True))

    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            (User.email.ilike(like)) | (User.full_name.ilike(like))
        )

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return UserListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    existing = (
        db.query(User).filter(User.tenant_id == tenant_id, User.email == payload.email).first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="El email ya está en uso en este tenant")

    user = User(
        tenant_id=tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/available-agents", response_model=AvailableAgentsResponse)
def list_available_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AvailableAgentsResponse:
    """Lista agentes activos del tenant con su carga de conversaciones activas.

    "Disponible" = role en (agent, admin) y is_active=True.
    conv_count = conversaciones con status agent o waiting_agent asignadas al agente.
    Ordenado por conv_count ASC (el de menos carga primero).
    """
    tenant_id = get_current_tenant_id()

    agents = (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id,
            User.role.in_(["agent", "admin"]),
            User.is_active.is_(True),
        )
        .all()
    )

    if not agents:
        return AvailableAgentsResponse(items=[], total=0)

    agent_ids = [a.id for a in agents]
    conv_counts: dict[uuid.UUID, int] = dict(
        db.query(
            WaConversation.assigned_user_id,
            func.count(WaConversation.id),
        )
        .filter(
            WaConversation.tenant_id == tenant_id,
            WaConversation.assigned_user_id.in_(agent_ids),
            WaConversation.status.in_(["agent", "waiting_agent"]),
        )
        .group_by(WaConversation.assigned_user_id)
        .all()
    )

    items = sorted(
        [
            AvailableAgentOut(
                id=a.id,
                email=a.email,
                full_name=a.full_name,
                role=a.role,
                conv_count=conv_counts.get(a.id, 0),
            )
            for a in agents
        ],
        key=lambda x: (x.conv_count, str(x.id)),
    )
    return AvailableAgentsResponse(items=items, total=len(items))


@router.get("/{user_id}/stats", response_model=UserStatsOut)
def get_user_stats(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserStatsOut:
    """Estadísticas de un agente/usuario del tenant.

    Accesible por: administradores/owners O el propio usuario.
    El user_id debe pertenecer al mismo tenant; si no, 404.
    """
    tenant_id = get_current_tenant_id()

    # Comprobar que el usuario pertenece al tenant.
    with bypass_tenant_filter():
        target = db.query(User).filter(
            User.id == user_id,
            User.tenant_id == tenant_id,
        ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Solo admin/owner o el propio usuario puede ver las stats.
    if current_user.role not in ("admin", "owner") and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Sin permisos para ver estas estadísticas")

    with bypass_tenant_filter():
        # Conversaciones activas asignadas al agente.
        conversations_active = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.assigned_user_id == user_id,
                WaConversation.status.in_(["agent", "waiting_agent"]),
            )
            .scalar()
        ) or 0

        # Conversaciones cerradas hoy asignadas al agente (resolved_at = hoy).
        today = date.today()
        today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        today_end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)
        conversations_today = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.assigned_user_id == user_id,
                WaConversation.status == "bot",
                WaConversation.resolved_at >= today_start,
                WaConversation.resolved_at <= today_end,
            )
            .scalar()
        ) or 0

        # Promedio de tiempo de primera respuesta en convs del agente.
        convs_with_response = (
            db.query(WaConversation)
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.assigned_user_id == user_id,
                WaConversation.first_response_at.isnot(None),
            )
            .all()
        )
        avg_first_response_sec: float | None = None
        if convs_with_response:
            deltas = [
                (c.first_response_at - c.created_at).total_seconds()
                for c in convs_with_response
                if c.first_response_at is not None and (c.first_response_at - c.created_at).total_seconds() >= 0
            ]
            if deltas:
                avg_first_response_sec = round(sum(deltas) / len(deltas), 2)

        # Notas creadas por este usuario.
        from src.inbox.models import ConversationNote
        notes_count = (
            db.query(func.count(ConversationNote.id))
            .filter(
                ConversationNote.tenant_id == tenant_id,
                ConversationNote.user_id == user_id,
            )
            .scalar()
        ) or 0

    return UserStatsOut(
        user_id=user_id,
        conversations_active=conversations_active,
        conversations_today=conversations_today,
        avg_first_response_sec=avg_first_response_sec,
        notes_count=notes_count,
    )


# ── Métricas de agente con histórico (Fase 29D) ──────────────────────────────


class AgentMetricsOut(BaseModel):
    user_id: uuid.UUID
    date_from: date
    date_to: date
    conversations_handled: int
    avg_first_response_sec: float | None
    avg_resolution_sec: float | None
    messages_sent: int
    notes_created: int
    busiest_hour: int | None  # Hora (0-23) con más actividad saliente, o None si sin datos.


@router.get("/{user_id}/metrics", response_model=AgentMetricsOut)
def get_user_metrics(
    user_id: uuid.UUID,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentMetricsOut:
    """Métricas históricas de un agente para un período.

    Accesible por admin/owner o el propio usuario.
    404 si el user_id no pertenece al tenant.
    """
    from datetime import timedelta

    from src.inbox.models import ConversationNote
    from src.wa.models import WaMessage

    tenant_id = get_current_tenant_id()

    with bypass_tenant_filter():
        target = db.query(User).filter(
            User.id == user_id,
            User.tenant_id == tenant_id,
        ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if current_user.role not in ("admin", "owner") and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Sin permisos para ver estas métricas")

    today = date.today()
    df = date_from or (today - timedelta(days=30))
    dt = date_to or today
    df_dt = datetime(df.year, df.month, df.day, tzinfo=timezone.utc)
    dt_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)

    with bypass_tenant_filter():
        # Conversaciones manejadas: asignadas al usuario y resueltas en el período.
        handled_convs = (
            db.query(WaConversation)
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.assigned_user_id == user_id,
                WaConversation.resolved_at.isnot(None),
                WaConversation.resolved_at >= df_dt,
                WaConversation.resolved_at <= dt_dt,
            )
            .all()
        )

        conversations_handled = len(handled_convs)

        # Tiempos de respuesta y resolución.
        first_resp_secs: list[float] = []
        res_secs: list[float] = []
        for c in handled_convs:
            if c.first_response_at is not None:
                delta = (c.first_response_at - c.created_at).total_seconds()
                if delta >= 0:
                    first_resp_secs.append(delta)
            if c.resolved_at is not None:
                delta = (c.resolved_at - c.created_at).total_seconds()
                if delta >= 0:
                    res_secs.append(delta)

        avg_first = round(sum(first_resp_secs) / len(first_resp_secs), 2) if first_resp_secs else None
        avg_res = round(sum(res_secs) / len(res_secs), 2) if res_secs else None

        # Mensajes salientes en conversaciones asignadas al agente.
        conv_ids = [c.id for c in handled_convs]
        outbound_msgs = []
        if conv_ids:
            outbound_msgs = (
                db.query(WaMessage)
                .filter(
                    WaMessage.tenant_id == tenant_id,
                    WaMessage.wa_conversation_id.in_(conv_ids),
                    WaMessage.direction == "out",
                    WaMessage.created_at >= df_dt,
                    WaMessage.created_at <= dt_dt,
                )
                .all()
            )
        messages_sent = len(outbound_msgs)

        # Hora más ocupada (por mensajes salientes).
        busiest_hour: int | None = None
        if outbound_msgs:
            hour_counts: dict[int, int] = {}
            for msg in outbound_msgs:
                h = msg.created_at.hour if msg.created_at else 0
                hour_counts[h] = hour_counts.get(h, 0) + 1
            busiest_hour = max(hour_counts, key=lambda k: hour_counts[k])

        # Notas creadas por el agente en el período.
        notes_created = (
            db.query(func.count(ConversationNote.id))
            .filter(
                ConversationNote.tenant_id == tenant_id,
                ConversationNote.user_id == user_id,
                ConversationNote.created_at >= df_dt,
                ConversationNote.created_at <= dt_dt,
            )
            .scalar()
        ) or 0

    return AgentMetricsOut(
        user_id=user_id,
        date_from=df,
        date_to=dt,
        conversations_handled=conversations_handled,
        avg_first_response_sec=avg_first,
        avg_resolution_sec=avg_res,
        messages_sent=messages_sent,
        notes_created=notes_created,
        busiest_hour=busiest_hour,
    )


# ── Deactivate user (Fase 28B) ────────────────────────────────────────────────
# Declarado ANTES de /{user_id} (GET) para evitar conflictos de routing.


class DeactivateUserOut(BaseModel):
    deactivated_user_id: uuid.UUID
    reassigned_conversations: int
    new_assignee_id: uuid.UUID | None


@router.patch("/{user_id}/deactivate", response_model=DeactivateUserOut)
def deactivate_user(
    user_id: uuid.UUID,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
) -> DeactivateUserOut:
    """Desactiva un usuario y reasigna sus conversaciones activas.

    - Solo admin/owner puede ejecutar este endpoint.
    - No permite desactivarse a sí mismo (422).
    - Reasigna las convs activas al agente con menos carga; si no hay, quedan sin asignar.
    - user_id debe pertenecer al mismo tenant, sino 404.
    """
    tenant_id = get_current_tenant_id()

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No puedes desactivarte a ti mismo",
        )

    with bypass_tenant_filter():
        target = db.query(User).filter(
            User.id == user_id,
            User.tenant_id == tenant_id,
        ).first()

    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    target.is_active = False

    # Buscar el agente con menos carga (excluye al usuario que se desactiva).
    from src.agent.service import _find_agent_with_least_load

    # Primero commit del is_active para que el lookup excluya al usuario desactivado.
    db.flush()

    new_assignee_id = _find_agent_with_least_load(db, tenant_id)

    # Reasignar conversaciones activas del usuario desactivado.
    with bypass_tenant_filter():
        active_convs = (
            db.query(WaConversation)
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.assigned_user_id == user_id,
                WaConversation.status.in_(["agent", "waiting_agent"]),
            )
            .all()
        )

    for conv in active_convs:
        conv.assigned_user_id = new_assignee_id

    db.commit()

    return DeactivateUserOut(
        deactivated_user_id=user_id,
        reassigned_conversations=len(active_convs),
        new_assignee_id=new_assignee_id,
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    user = (
        db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()

    # Agentes solo pueden modificarse a sí mismos
    if current_user.role == "agent" and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sin permisos para modificar otros usuarios")

    user = (
        db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None and current_user.role in ("owner", "admin"):
        user.role = payload.role
    if payload.is_active is not None and current_user.role in ("owner", "admin"):
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return user
