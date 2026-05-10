"""Enforcement de cuotas operativas (Fase 11).

check_quota() se llama en el hot path antes de la operación. Lanza HTTP 429
si el tenant ha superado el cap. Emite evento 'quota.exceeded' via dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.billing.models import Quota, UsageMetric

# Métricas soportadas y sus caps en Quota
_METRIC_CONFIG: dict[str, dict] = {
    "messages_out": {
        "quota_field": "max_messages_per_month",
        "metric_field": "messages_out",
        "period": "month",
    },
    "llm_cost_cents": {
        "quota_field": "max_llm_cost_cents_per_month",
        "metric_field": "llm_cost_cents",
        "period": "month",
    },
    "api_requests": {
        "quota_field": "max_api_requests_per_day",
        "metric_field": "api_requests",
        "period": "day",
    },
    "storage_bytes": {
        "quota_field": "max_storage_bytes",
        "metric_field": "storage_bytes",
        "period": "snapshot",
    },
}


def check_quota(
    tenant_id: uuid.UUID,
    metric: str,
    delta: float,
    db: Session,
) -> None:
    """Verifica que agregar ``delta`` al tenant no supere la cuota configurada.

    Args:
        tenant_id: Tenant a verificar.
        metric: Clave de la métrica (ej: 'messages_out', 'llm_cost_cents').
        delta: Cantidad a agregar (1 para mensajes, cents para LLM, etc.).
        db: Sesión de BD activa.

    Raises:
        HTTPException 429: Si el tenant supera su cuota.
    """
    config = _METRIC_CONFIG.get(metric)
    if config is None:
        return  # métrica desconocida → no bloquear

    quota = db.query(Quota).filter(Quota.tenant_id == tenant_id).first()
    if quota is None:
        return  # sin cuota configurada → libre

    cap = getattr(quota, config["quota_field"], None)
    if cap is None:
        return  # cap null → sin límite

    current = _get_current_usage(db, tenant_id, config)
    if current + delta > cap:
        _emit_quota_exceeded(tenant_id, metric, current, cap, delta, db)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cuota superada: {metric} ({current + delta:.0f}/{cap})",
        )


def _get_current_usage(
    db: Session,
    tenant_id: uuid.UUID,
    config: dict,
) -> float:
    period = config["period"]
    field_name = config["metric_field"]
    field = getattr(UsageMetric, field_name)

    if period == "month":
        today = date.today()
        result = db.execute(
            sa.select(sa.func.coalesce(sa.func.sum(field), 0)).where(
                UsageMetric.tenant_id == tenant_id,
                sa.extract("year", UsageMetric.metric_date) == today.year,
                sa.extract("month", UsageMetric.metric_date) == today.month,
            )
        ).scalar()
    elif period == "day":
        today = date.today()
        result = db.execute(
            sa.select(sa.func.coalesce(sa.func.sum(field), 0)).where(
                UsageMetric.tenant_id == tenant_id,
                UsageMetric.metric_date == today,
            )
        ).scalar()
    else:
        # snapshot: valor actual del día (storage_bytes se lee directo)
        today = date.today()
        result = db.execute(
            sa.select(sa.func.coalesce(sa.func.sum(field), 0)).where(
                UsageMetric.tenant_id == tenant_id,
                UsageMetric.metric_date == today,
            )
        ).scalar()

    return float(result or 0)


def _emit_quota_exceeded(
    tenant_id: uuid.UUID,
    metric: str,
    current: float,
    cap: float,
    delta: float,
    db: Session,
) -> None:
    """Emite evento 'quota.exceeded' via dispatcher (best-effort, no bloquea)."""
    try:
        from src.public_api.dispatcher import emit_event

        emit_event(
            tenant_id=tenant_id,
            event="quota.exceeded",
            payload={
                "metric": metric,
                "current": current,
                "cap": cap,
                "delta": delta,
                "exceeded_at": datetime.now(timezone.utc).isoformat(),
            },
            db=db,
        )
    except Exception:
        pass  # no bloquear el 429 si el dispatcher falla
