"""CRUD de Office Hours — horarios de atención del bot (Fase 29A).

Endpoints:
- GET    /v1/office-hours                  — listar todos los registros del tenant
- POST   /v1/office-hours                  — crear registro
- GET    /v1/office-hours/{id}             — obtener uno
- PUT    /v1/office-hours/{id}             — actualizar
- DELETE /v1/office-hours/{id}             — eliminar
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.office_hours.models import OfficeHours
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id

router = APIRouter(prefix="/office-hours", tags=["office-hours"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class OfficeHoursCreate(BaseModel):
    wa_number_id: uuid.UUID | None = None
    day_of_week: int
    hour_start: int
    hour_end: int
    is_active: bool = True
    out_of_hours_message: str = ""

    @field_validator("day_of_week")
    @classmethod
    def validate_day(cls, v: int) -> int:
        if not 0 <= v <= 6:
            raise ValueError("day_of_week debe estar entre 0 (lunes) y 6 (domingo)")
        return v

    @field_validator("hour_start", "hour_end")
    @classmethod
    def validate_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("hora debe estar entre 0 y 23")
        return v


class OfficeHoursUpdate(BaseModel):
    wa_number_id: uuid.UUID | None = None
    day_of_week: int | None = None
    hour_start: int | None = None
    hour_end: int | None = None
    is_active: bool | None = None
    out_of_hours_message: str | None = None


class OfficeHoursOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    wa_number_id: uuid.UUID | None
    day_of_week: int
    hour_start: int
    hour_end: int
    is_active: bool
    out_of_hours_message: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_record(
    office_hours_id: uuid.UUID, tenant_id: uuid.UUID, db: Session
) -> OfficeHours:
    with bypass_tenant_filter():
        rec = (
            db.query(OfficeHours)
            .filter(
                OfficeHours.id == office_hours_id,
                OfficeHours.tenant_id == tenant_id,
            )
            .first()
        )
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Office hours no encontrado",
        )
    return rec


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_model=list[OfficeHoursOut])
def list_office_hours(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OfficeHours]:
    """Lista todos los horarios de atención del tenant."""
    tenant_id = get_current_tenant_id()
    with bypass_tenant_filter():
        records = (
            db.query(OfficeHours)
            .filter(OfficeHours.tenant_id == tenant_id)
            .order_by(OfficeHours.day_of_week, OfficeHours.hour_start)
            .all()
        )
    return records


@router.post("", response_model=OfficeHoursOut, status_code=201)
def create_office_hours(
    body: OfficeHoursCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfficeHours:
    """Crea un nuevo registro de horario de atención."""
    tenant_id = get_current_tenant_id()
    record = OfficeHours(
        tenant_id=tenant_id,
        wa_number_id=body.wa_number_id,
        day_of_week=body.day_of_week,
        hour_start=body.hour_start,
        hour_end=body.hour_end,
        is_active=body.is_active,
        out_of_hours_message=body.out_of_hours_message or "",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{office_hours_id}", response_model=OfficeHoursOut)
def get_office_hours(
    office_hours_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfficeHours:
    """Obtiene un registro de horario por ID."""
    tenant_id = get_current_tenant_id()
    return _get_record(office_hours_id, tenant_id, db)


@router.put("/{office_hours_id}", response_model=OfficeHoursOut)
def update_office_hours(
    office_hours_id: uuid.UUID,
    body: OfficeHoursUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OfficeHours:
    """Actualiza un registro de horario."""
    tenant_id = get_current_tenant_id()
    record = _get_record(office_hours_id, tenant_id, db)

    if body.day_of_week is not None:
        if not 0 <= body.day_of_week <= 6:
            raise HTTPException(status_code=422, detail="day_of_week debe estar entre 0 y 6")
        record.day_of_week = body.day_of_week
    if body.hour_start is not None:
        if not 0 <= body.hour_start <= 23:
            raise HTTPException(status_code=422, detail="hour_start debe estar entre 0 y 23")
        record.hour_start = body.hour_start
    if body.hour_end is not None:
        if not 0 <= body.hour_end <= 23:
            raise HTTPException(status_code=422, detail="hour_end debe estar entre 0 y 23")
        record.hour_end = body.hour_end
    if body.is_active is not None:
        record.is_active = body.is_active
    if body.out_of_hours_message is not None:
        record.out_of_hours_message = body.out_of_hours_message
    if body.wa_number_id is not None:
        record.wa_number_id = body.wa_number_id

    db.commit()
    db.refresh(record)
    return record


@router.delete("/{office_hours_id}", status_code=204)
def delete_office_hours(
    office_hours_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Elimina un registro de horario."""
    tenant_id = get_current_tenant_id()
    record = _get_record(office_hours_id, tenant_id, db)
    db.delete(record)
    db.commit()
