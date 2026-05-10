"""Endpoints REST de billing, métricas, cuotas y audit_log (Fase 11).

Rutas:
- GET  /v1/metrics            — serie diaria del período indicado (owner)
- GET  /v1/metrics/summary    — totales del mes en curso (owner)
- GET  /v1/quotas             — cuotas del tenant (owner)
- PUT  /v1/quotas             — actualizar cuotas (superadmin only)
- GET  /v1/audit-log          — log paginado, 50 por página (owner)
- GET  /v1/audit-log/export   — descarga CSV completa (owner)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.billing.models import AuditLog, Quota, UsageMetric
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
