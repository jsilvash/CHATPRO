"""Función de agregación nocturna de métricas diarias (Fase 11).

Diseñada para correr como tarea Celery beat a medianoche.
Puede llamarse directamente en tests o scripts de backfill.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.billing.models import UsageMetric
from src.db.models import Tenant
from src.knowledge.models import KbChunk
from src.wa.models import WaConversation, WaMessage


def aggregate_daily_metrics(db: Session, target_date: date) -> int:
    """Agrega métricas del ``target_date`` para todos los tenants activos.

    Hace UPSERT en usage_metrics acumulando las columnas que puede derivar
    de las tablas fuente. No toca llm_cost_cents ni llm_*_tokens
    (esos se acumulan en tiempo real desde el agente).

    Returns:
        Número de tenants procesados.
    """
    tenants: list[Tenant] = db.query(Tenant).filter(Tenant.is_active.is_(True)).all()
    count = 0
    for tenant in tenants:
        _upsert_tenant_day(db, tenant.id, target_date)
        count += 1
    db.commit()
    return count


def _upsert_tenant_day(
    db: Session,
    tenant_id: uuid.UUID,
    target_date: date,
) -> None:
    """Calcula y hace upsert de las métricas del tenant para la fecha dada."""
    msgs_in = _count_messages(db, tenant_id, target_date, "in")
    msgs_out = _count_messages(db, tenant_id, target_date, "out")
    conversations_active = _count_active_conversations(db, tenant_id, target_date)
    storage_bytes = _calc_storage_bytes(db, tenant_id)

    stmt = pg_insert(UsageMetric).values(
        tenant_id=tenant_id,
        metric_date=target_date,
        messages_in=msgs_in,
        messages_out=msgs_out,
        conversations_active=conversations_active,
        storage_bytes=storage_bytes,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "metric_date"],
        set_={
            "messages_in": stmt.excluded.messages_in,
            "messages_out": stmt.excluded.messages_out,
            "conversations_active": stmt.excluded.conversations_active,
            "storage_bytes": stmt.excluded.storage_bytes,
        },
    )
    db.execute(stmt)


def _count_messages(
    db: Session, tenant_id: uuid.UUID, target_date: date, direction: str
) -> int:
    result = db.execute(
        sa.select(sa.func.count()).select_from(WaMessage).where(
            WaMessage.tenant_id == tenant_id,
            WaMessage.direction == direction,
            sa.func.date(WaMessage.created_at) == target_date,
        )
    ).scalar()
    return result or 0


def _count_active_conversations(
    db: Session, tenant_id: uuid.UUID, target_date: date
) -> int:
    """Conversaciones que tuvieron al menos un mensaje en la fecha."""
    subq = (
        sa.select(WaMessage.wa_conversation_id)
        .where(
            WaMessage.tenant_id == tenant_id,
            sa.func.date(WaMessage.created_at) == target_date,
        )
        .distinct()
        .subquery()
    )
    result = db.execute(
        sa.select(sa.func.count()).select_from(subq)
    ).scalar()
    return result or 0


def _calc_storage_bytes(db: Session, tenant_id: uuid.UUID) -> int:
    """Suma len(content) de todos los kb_chunks del tenant (snapshot actual)."""
    result = db.execute(
        sa.select(sa.func.coalesce(sa.func.sum(sa.func.length(KbChunk.content)), 0)).where(
            KbChunk.tenant_id == tenant_id
        )
    ).scalar()
    return result or 0
