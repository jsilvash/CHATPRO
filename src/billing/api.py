"""Endpoints REST de billing, métricas, cuotas y audit_log (Fase 11).

Rutas:
- GET  /v1/metrics            — serie diaria del período indicado (owner)
- GET  /v1/metrics/summary    — totales del mes en curso (owner)
- GET  /v1/metrics/stream     — SSE de métricas en tiempo real (Fase 23D)
- GET  /v1/quotas             — cuotas del tenant (owner)
- PUT  /v1/quotas             — actualizar cuotas (superadmin only)
- GET  /v1/audit-log          — log paginado, 50 por página (owner)
- GET  /v1/audit-log/export   — descarga CSV completa (owner)
- POST /v1/tenants/me/export                       — crear job de exportación GDPR (Fase 23B)
- GET  /v1/tenants/me/export/{job_id}/status       — estado del job
- GET  /v1/tenants/me/export/{job_id}/download     — URL firmada al ZIP
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.billing.models import AuditLog, ExportJob, Quota, UsageMetric
from src.db.session import get_db
from src.tenancy.context import get_current_tenant_id

router = APIRouter(tags=["billing"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class UsageMetricOut(BaseModel):
    metric_date: date
    messages_in: int
    messages_out: int
    conversations_active: int
    llm_input_tokens: int
    llm_output_tokens: int
    llm_cost_cents: float
    storage_bytes: int
    api_requests: int

    model_config = {"from_attributes": True}


class MetricsSummaryOut(BaseModel):
    period_start: date
    period_end: date
    messages_in: int
    messages_out: int
    conversations_active: int
    llm_input_tokens: int
    llm_output_tokens: int
    llm_cost_cents: float
    storage_bytes: int
    api_requests: int


class QuotaOut(BaseModel):
    tenant_id: uuid.UUID
    max_messages_per_month: int | None
    max_conversations_active: int | None
    max_llm_cost_cents_per_month: int | None
    max_storage_bytes: int | None
    max_api_requests_per_day: int | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuotaUpdate(BaseModel):
    max_messages_per_month: int | None = None
    max_conversations_active: int | None = None
    max_llm_cost_cents_per_month: int | None = None
    max_storage_bytes: int | None = None
    max_api_requests_per_day: int | None = None


class AuditLogOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_api_key_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: uuid.UUID | None
    ip: str | None
    user_agent: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    pages: int


# ── Endpoints métricas ────────────────────────────────────────────────────────


@router.get("/metrics", response_model=list[UsageMetricOut])
def list_metrics(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Serie diaria de métricas para el rango [from, to] del tenant autenticado."""
    tenant_id = get_current_tenant_id()
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="'to' debe ser >= 'from'")

    rows = (
        db.query(UsageMetric)
        .filter(
            UsageMetric.tenant_id == tenant_id,
            UsageMetric.metric_date >= from_date,
            UsageMetric.metric_date <= to_date,
        )
        .order_by(UsageMetric.metric_date)
        .all()
    )
    return rows


@router.get("/metrics/summary", response_model=MetricsSummaryOut)
def metrics_summary(
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Totales del mes en curso para el tenant autenticado."""
    tenant_id = get_current_tenant_id()
    today = date.today()
    period_start = today.replace(day=1)

    def _sum(field):
        return sa.func.coalesce(sa.func.sum(field), 0)

    row = db.execute(
        sa.select(
            _sum(UsageMetric.messages_in),
            _sum(UsageMetric.messages_out),
            _sum(UsageMetric.conversations_active),
            _sum(UsageMetric.llm_input_tokens),
            _sum(UsageMetric.llm_output_tokens),
            _sum(UsageMetric.llm_cost_cents),
            _sum(UsageMetric.storage_bytes),
            _sum(UsageMetric.api_requests),
        ).where(
            UsageMetric.tenant_id == tenant_id,
            UsageMetric.metric_date >= period_start,
            UsageMetric.metric_date <= today,
        )
    ).first()

    return MetricsSummaryOut(
        period_start=period_start,
        period_end=today,
        messages_in=row[0],
        messages_out=row[1],
        conversations_active=row[2],
        llm_input_tokens=row[3],
        llm_output_tokens=row[4],
        llm_cost_cents=float(row[5]),
        storage_bytes=row[6],
        api_requests=row[7],
    )


# ── Endpoints cuotas ──────────────────────────────────────────────────────────


@router.get("/quotas", response_model=QuotaOut)
def get_quotas(
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Devuelve las cuotas del tenant autenticado. Si no existen, devuelve caps a null."""
    tenant_id = get_current_tenant_id()
    quota = db.query(Quota).filter(Quota.tenant_id == tenant_id).first()
    if quota is None:
        # Retornar estructura vacía (sin cuotas configuradas = sin límites)
        return QuotaOut(
            tenant_id=tenant_id,
            max_messages_per_month=None,
            max_conversations_active=None,
            max_llm_cost_cents_per_month=None,
            max_storage_bytes=None,
            max_api_requests_per_day=None,
            updated_at=datetime.now(timezone.utc),
        )
    return quota


@router.put("/quotas", response_model=QuotaOut)
def update_quotas(
    body: QuotaUpdate,
    current_user=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Actualiza las cuotas del tenant. Solo accesible por superadmin de plataforma."""
    tenant_id = get_current_tenant_id()
    quota = db.query(Quota).filter(Quota.tenant_id == tenant_id).first()
    if quota is None:
        quota = Quota(tenant_id=tenant_id)
        db.add(quota)

    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(quota, field, val)

    quota.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(quota)
    return quota


# ── Endpoints audit log ───────────────────────────────────────────────────────

_PAGE_SIZE = 50


@router.get("/audit-log", response_model=AuditLogPage)
def list_audit_log(
    page: int = Query(default=1, ge=1),
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Devuelve el audit log del tenant paginado (50 registros/página)."""
    tenant_id = get_current_tenant_id()
    base_q = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
    total = base_q.count()
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    if page > pages and total > 0:
        raise HTTPException(status_code=404, detail="Página fuera de rango")

    items = (
        base_q.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * _PAGE_SIZE)
        .limit(_PAGE_SIZE)
        .all()
    )
    return AuditLogPage(items=items, total=total, page=page, pages=pages)


@router.get("/audit-log/export")
def export_audit_log(
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Descarga el audit log completo del tenant como CSV (StreamingResponse)."""
    tenant_id = get_current_tenant_id()

    def _generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "created_at", "action", "target_type", "target_id",
            "actor_user_id", "actor_api_key_id", "ip", "user_agent",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate()

        rows = (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.created_at.desc())
            .yield_per(200)
        )
        for row in rows:
            writer.writerow([
                str(row.id),
                row.created_at.isoformat() if row.created_at else "",
                row.action,
                row.target_type or "",
                str(row.target_id) if row.target_id else "",
                str(row.actor_user_id) if row.actor_user_id else "",
                str(row.actor_api_key_id) if row.actor_api_key_id else "",
                row.ip or "",
                row.user_agent or "",
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate()

    filename = f"audit-log-{tenant_id}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Export GDPR (Fase 23B) ────────────────────────────────────────────────────


class ExportJobOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    error: str | None
    storage_uri: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class ExportStatusOut(BaseModel):
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: str | None

    model_config = {"from_attributes": True}


class DownloadOut(BaseModel):
    url: str


@router.post(
    "/tenants/me/export",
    response_model=ExportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_export_job(
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
) -> ExportJob:
    """Crea un job de exportación de datos del tenant (GDPR) y lo encola."""
    tenant_id = get_current_tenant_id()
    job = ExportJob(tenant_id=tenant_id, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    # Encolar tarea Celery de forma lazy para no bloquear la respuesta.
    try:
        from src.billing.tasks import export_tenant_data
        export_tenant_data.delay(str(job.id))
    except Exception:
        pass

    return job


@router.get("/tenants/me/export/{job_id}/status", response_model=ExportStatusOut)
def get_export_status(
    job_id: uuid.UUID,
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
) -> ExportJob:
    """Devuelve el estado del job de exportación."""
    tenant_id = get_current_tenant_id()
    job = (
        db.query(ExportJob)
        .filter(ExportJob.id == job_id, ExportJob.tenant_id == tenant_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job no encontrado")
    return job


@router.get("/tenants/me/export/{job_id}/download", response_model=DownloadOut)
def get_export_download(
    job_id: uuid.UUID,
    current_user=Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
) -> DownloadOut:
    """Devuelve una URL firmada (TTL 15 min) al ZIP del export."""
    tenant_id = get_current_tenant_id()
    job = (
        db.query(ExportJob)
        .filter(ExportJob.id == job_id, ExportJob.tenant_id == tenant_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job no encontrado")

    if job.status != "done":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El job no está listo (status={job.status})",
        )

    if not job.storage_uri:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El archivo de exportación no está disponible",
        )

    # Extraer key de la URI s3://{bucket}/{key}
    from src.billing import storage as s3_storage
    key = job.storage_uri.split("/", 3)[-1]
    url = s3_storage.generate_presigned_url(key, ttl_seconds=900)

    return DownloadOut(url=url)


# ── Métricas SSE (Fase 23D) ────────────────────────────────────────────────────


def _resolve_sse_user(
    request_token: str,
    authorization: str,
    db: Session,
) -> tuple[uuid.UUID, str]:
    """Resuelve el tenant_id para SSE desde header Bearer o query param token.

    Devuelve (tenant_id, role). Lanza HTTPException 401 si no hay token válido.
    """
    from src.auth.tokens import decode_token
    from src.db.models import User
    from src.tenancy.context import bypass_tenant_filter

    raw_token = ""
    if authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif request_token:
        raw_token = request_token

    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    try:
        payload = decode_token(raw_token, "access")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    user_id = uuid.UUID(payload["sub"])
    tenant_id = uuid.UUID(payload["tid"])

    from src.tenancy.context import _tenant_id_var
    _tenant_id_var.set(tenant_id)

    with bypass_tenant_filter():
        user = (
            db.query(User)
            .filter(User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True))
            .first()
        )
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")

    return tenant_id, user.role


@router.get("/metrics/stream")
async def metrics_stream(
    request: "Request",
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Server-Sent Events con métricas del tenant actualizadas periódicamente.

    Emite el primer evento inmediatamente al conectar y luego cada
    METRICS_STREAM_INTERVAL_S segundos. Auth via Bearer header o ?token=<jwt>.
    Si el cliente desconecta (CancelledError) cierra silenciosamente.
    """
    import asyncio
    import json as json_lib

    from src.config import get_settings

    # Auth flexible: header Authorization o query param ?token=
    auth_header = request.headers.get("authorization", "")
    tenant_id, _role = _resolve_sse_user(token, auth_header, db)

    settings = get_settings()
    interval = settings.metrics_stream_interval_s
    today = datetime.now(timezone.utc).date()

    def _build_metrics_payload() -> dict:
        row = (
            db.query(UsageMetric)
            .filter(
                UsageMetric.tenant_id == tenant_id,
                UsageMetric.metric_date == today,
            )
            .first()
        )
        from src.wa.models import WaConversation
        conv_active = (
            db.query(sa.func.count())
            .select_from(WaConversation)
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.status.in_(["bot", "waiting_agent", "agent"]),
            )
            .scalar()
        ) or 0

        return {
            "messages_in_today": row.messages_in if row else 0,
            "messages_out_today": row.messages_out if row else 0,
            "conversations_active": conv_active,
            "llm_cost_cents_today": float(row.llm_cost_cents) if row else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _event_generator():
        try:
            while True:
                payload = _build_metrics_payload()
                yield f"data: {json_lib.dumps(payload)}\n\n"
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
