"""Endpoints REST para gestión de Personas del agente.

- ``POST   /v1/personas``                         — crear persona.
- ``GET    /v1/personas``                         — listar personas del tenant.
- ``GET    /v1/personas/{id}``                    — detalle.
- ``PATCH  /v1/personas/{id}``                    — actualizar campos.
- ``DELETE /v1/personas/{id}``                    — eliminar.
- ``PATCH  /v1/wa-numbers/{id}/persona``          — asignar persona a número.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agent.models import Persona
from src.auth.dependencies import get_current_user, require_role
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id
from src.wa.models import WaNumber

router = APIRouter(tags=["personas"])


# ────────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────────


class PersonaCreate(BaseModel):
    name: str
    system_prompt: str = ""
    tone: str = "amigable"
    locale: str = "es-CL"
    timezone: str = "America/Santiago"
    out_of_hours_message: str = ""
    business_hours_json: dict = {}
    model_id: str = "claude-sonnet-4-6"
    locale_secondary: list[str] = []
    auto_detect_locale: bool = False


class PersonaUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    tone: str | None = None
    locale: str | None = None
    timezone: str | None = None
    out_of_hours_message: str | None = None
    business_hours_json: dict | None = None
    model_id: str | None = None
    locale_secondary: list[str] | None = None
    auto_detect_locale: bool | None = None


class PersonaResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    system_prompt: str
    tone: str
    locale: str
    timezone: str
    out_of_hours_message: str
    business_hours_json: dict
    model_id: str
    locale_secondary: list[str] = []
    auto_detect_locale: bool = False

    model_config = {"from_attributes": True}


class PersonaListResponse(BaseModel):
    items: list[PersonaResponse]
    total: int


class AssignPersonaRequest(BaseModel):
    persona_id: uuid.UUID | None = None


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _ensure_persona(db: Session, persona_id: uuid.UUID, tenant_id: uuid.UUID) -> Persona:
    p = (
        db.query(Persona)
        .filter(Persona.id == persona_id, Persona.tenant_id == tenant_id)
        .first()
    )
    if p is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    return p


def _persona_to_response(p: Persona) -> PersonaResponse:
    return PersonaResponse(
        id=p.id,
        tenant_id=p.tenant_id,
        name=p.name,
        system_prompt=p.system_prompt or "",
        tone=p.tone or "amigable",
        locale=p.locale or "es-CL",
        timezone=p.timezone or "America/Santiago",
        out_of_hours_message=p.out_of_hours_message or "",
        business_hours_json=p.business_hours_json or {},
        model_id=p.model_id or "claude-sonnet-4-6",
        locale_secondary=getattr(p, "locale_secondary", None) or [],
        auto_detect_locale=getattr(p, "auto_detect_locale", False) or False,
    )


# ────────────────────────────────────────────────────────────
# Endpoints — Personas
# ────────────────────────────────────────────────────────────


@router.post("/personas", response_model=PersonaResponse, status_code=201)
def create_persona(
    payload: PersonaCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    persona = Persona(
        tenant_id=tenant_id,
        name=payload.name,
        system_prompt=payload.system_prompt,
        tone=payload.tone,
        locale=payload.locale,
        timezone=payload.timezone,
        out_of_hours_message=payload.out_of_hours_message,
        business_hours_json=payload.business_hours_json,
        model_id=payload.model_id,
        locale_secondary=payload.locale_secondary,
        auto_detect_locale=payload.auto_detect_locale,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return _persona_to_response(persona)


@router.get("/personas", response_model=PersonaListResponse)
def list_personas(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    items = (
        db.query(Persona)
        .filter(Persona.tenant_id == tenant_id)
        .order_by(Persona.created_at.desc())
        .all()
    )
    return PersonaListResponse(
        items=[_persona_to_response(p) for p in items],
        total=len(items),
    )


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
def get_persona(
    persona_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    return _persona_to_response(_ensure_persona(db, persona_id, tenant_id))


@router.patch("/personas/{persona_id}", response_model=PersonaResponse)
def update_persona(
    persona_id: uuid.UUID,
    payload: PersonaUpdate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    persona = _ensure_persona(db, persona_id, tenant_id)

    updates: dict[str, Any] = payload.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(persona, field, value)

    db.commit()
    db.refresh(persona)
    return _persona_to_response(persona)


@router.delete("/personas/{persona_id}", status_code=204)
def delete_persona(
    persona_id: uuid.UUID,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    persona = _ensure_persona(db, persona_id, tenant_id)
    db.delete(persona)
    db.commit()
    return None


# ────────────────────────────────────────────────────────────
# Endpoints — Asignación de Persona a WaNumber
# ────────────────────────────────────────────────────────────


@router.patch("/wa-numbers/{wa_number_id}/persona", response_model=dict)
def assign_persona_to_wa_number(
    wa_number_id: uuid.UUID,
    payload: AssignPersonaRequest,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Asigna (o desasigna si persona_id=null) una Persona a un WaNumber."""
    tenant_id = get_current_tenant_id()

    with bypass_tenant_filter():
        wn = (
            db.query(WaNumber)
            .filter(WaNumber.id == wa_number_id, WaNumber.tenant_id == tenant_id)
            .first()
        )
    if wn is None:
        raise HTTPException(status_code=404, detail="Número no encontrado")

    if payload.persona_id is not None:
        persona = (
            db.query(Persona)
            .filter(Persona.id == payload.persona_id, Persona.tenant_id == tenant_id)
            .first()
        )
        if persona is None:
            raise HTTPException(status_code=404, detail="Persona no encontrada")

    wn.persona_id = payload.persona_id
    db.commit()

    return {
        "wa_number_id": str(wa_number_id),
        "persona_id": str(payload.persona_id) if payload.persona_id else None,
    }
