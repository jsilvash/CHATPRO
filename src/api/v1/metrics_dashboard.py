"""Endpoint de dashboard de métricas operativas del tenant (Fase 28A).

GET /v1/metrics/dashboard — resumen ejecutivo en tiempo real.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id
from src.wa.models import WaConversation, WaMessage

router = APIRouter(tags=["metrics"])


class TenantMetricsDashboard(BaseModel):
    conversations_total: int
    conversations_active: int
    conversations_bot: int
    conversations_closed_today: int
    messages_in_today: int
    messages_out_today: int
    agents_online: int
    unassigned_waiting: int


@router.get("/metrics/dashboard", response_model=TenantMetricsDashboard)
def get_metrics_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TenantMetricsDashboard:
    """Resumen ejecutivo de métricas operativas del tenant en tiempo real."""
    tenant_id = get_current_tenant_id()

    today = date.today()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    today_end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)

    with bypass_tenant_filter():
        conversations_total = (
            db.query(func.count(WaConversation.id))
            .filter(WaConversation.tenant_id == tenant_id)
            .scalar()
        ) or 0

        conversations_active = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.status.in_(["agent", "waiting_agent"]),
            )
            .scalar()
        ) or 0

        conversations_bot = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.status == "bot",
            )
            .scalar()
        ) or 0

        conversations_closed_today = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.resolved_at >= today_start,
                WaConversation.resolved_at <= today_end,
            )
            .scalar()
        ) or 0

        messages_in_today = (
            db.query(func.count(WaMessage.id))
            .filter(
                WaMessage.tenant_id == tenant_id,
                WaMessage.direction == "in",
                WaMessage.created_at >= today_start,
                WaMessage.created_at <= today_end,
            )
            .scalar()
        ) or 0

        messages_out_today = (
            db.query(func.count(WaMessage.id))
            .filter(
                WaMessage.tenant_id == tenant_id,
                WaMessage.direction == "out",
                WaMessage.created_at >= today_start,
                WaMessage.created_at <= today_end,
            )
            .scalar()
        ) or 0

        agents_online = (
            db.query(func.count(User.id))
            .filter(
                User.tenant_id == tenant_id,
                User.role.in_(["agent", "admin"]),
                User.is_active.is_(True),
            )
            .scalar()
        ) or 0

        unassigned_waiting = (
            db.query(func.count(WaConversation.id))
            .filter(
                WaConversation.tenant_id == tenant_id,
                WaConversation.status == "waiting_agent",
                WaConversation.assigned_user_id.is_(None),
            )
            .scalar()
        ) or 0

    return TenantMetricsDashboard(
        conversations_total=conversations_total,
        conversations_active=conversations_active,
        conversations_bot=conversations_bot,
        conversations_closed_today=conversations_closed_today,
        messages_in_today=messages_in_today,
        messages_out_today=messages_out_today,
        agents_online=agents_online,
        unassigned_waiting=unassigned_waiting,
    )
