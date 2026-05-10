"""API REST para templates de respuesta rápida (canned responses, Fase 25C).

Endpoints:
- POST   /v1/canned-responses          — crear template
- GET    /v1/canned-responses          — listar (paginado)
- GET    /v1/canned-responses/{id}     — detalle
- PATCH  /v1/canned-responses/{id}     — actualizar
- DELETE /v1/canned-responses/{id}     — eliminar
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.inbox.models import CannedResponse
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id

router = APIRouter(prefix="/canned-responses", tags=["canned-responses"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class CannedResponseCreate(BaseModel):
    shortcode: str
    text: str


class CannedResponseUpdate(BaseModel):
    shortcode: str | None = None
    text: str | None = None


class CannedResponseOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    shortcode: str
    text: str
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CannedResponseListOut(BaseModel):
    items: list[CannedResponseOut]
    total: int
    page: int
    page_size: int


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_canned(
    canned_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> CannedResponse:
    with bypass_tenant_filter():
        cr = (
            db.query(CannedResponse)
            .filter(
                CannedResponse.id == canned_id,
                CannedResponse.tenant_id == tenant_id,
            )
            .first()
        )
    if cr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template no encontrado",
        )
    return cr


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", response_model=CannedResponseOut, status_code=status.HTTP_201_CREATED)
def create_canned(
    body: CannedResponseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CannedResponseOut:
    """Crea un nuevo template de respuesta rápida."""
    tenant_id = get_current_tenant_id()

    shortcode = (body.shortcode or "").strip()
    if not shortcode:
        raise HTTPException(status_code=422, detail="shortcode no puede estar vacío")
    if len(shortcode) > 64:
        raise HTTPException(status_code=422, detail="shortcode demasiado largo (máx 64 chars)")
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text no puede estar vacío")

    with bypass_tenant_filter():
        existing = (
            db.query(CannedResponse)
            .filter(
                CannedResponse.tenant_id == tenant_id,
                CannedResponse.shortcode == shortcode,
            )
            .first()
        )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un template con shortcode '{shortcode}'",
        )

    cr = CannedResponse(
        tenant_id=tenant_id,
        shortcode=shortcode,
        text=body.text.strip(),
        created_by_user_id=current_user.id,
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return CannedResponseOut.model_validate(cr)


@router.get("", response_model=CannedResponseListOut)
def list_canned(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CannedResponseListOut:
    """Lista templates de respuesta rápida del tenant (paginado)."""
    tenant_id = get_current_tenant_id()
    offset = (page - 1) * page_size

    with bypass_tenant_filter():
        q = db.query(CannedResponse).filter(
            CannedResponse.tenant_id == tenant_id
        ).order_by(CannedResponse.shortcode)

        total = q.count()
        items = q.offset(offset).limit(page_size).all()

    return CannedResponseListOut(
        items=[CannedResponseOut.model_validate(cr) for cr in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{canned_id}", response_model=CannedResponseOut)
def get_canned(
    canned_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CannedResponseOut:
    """Detalle de un template por ID."""
    tenant_id = get_current_tenant_id()
    cr = _get_canned(canned_id, tenant_id, db)
    return CannedResponseOut.model_validate(cr)


@router.patch("/{canned_id}", response_model=CannedResponseOut)
def update_canned(
    canned_id: uuid.UUID,
    body: CannedResponseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CannedResponseOut:
    """Actualiza shortcode y/o text de un template."""
    tenant_id = get_current_tenant_id()
    cr = _get_canned(canned_id, tenant_id, db)

    if body.shortcode is not None:
        shortcode = body.shortcode.strip()
        if not shortcode:
            raise HTTPException(status_code=422, detail="shortcode no puede estar vacío")
        if len(shortcode) > 64:
            raise HTTPException(status_code=422, detail="shortcode demasiado largo (máx 64 chars)")
        if shortcode != cr.shortcode:
            with bypass_tenant_filter():
                dup = (
                    db.query(CannedResponse)
                    .filter(
                        CannedResponse.tenant_id == tenant_id,
                        CannedResponse.shortcode == shortcode,
                        CannedResponse.id != canned_id,
                    )
                    .first()
                )
            if dup:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Ya existe un template con shortcode '{shortcode}'",
                )
        cr.shortcode = shortcode

    if body.text is not None:
        if not body.text.strip():
            raise HTTPException(status_code=422, detail="text no puede estar vacío")
        cr.text = body.text.strip()

    cr.updated_at = datetime.now(UTC)
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return CannedResponseOut.model_validate(cr)


@router.delete("/{canned_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_canned(
    canned_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Elimina un template. 404 si no existe."""
    tenant_id = get_current_tenant_id()
    cr = _get_canned(canned_id, tenant_id, db)
    db.delete(cr)
    db.commit()
