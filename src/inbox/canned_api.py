"""API REST para templates de respuesta rápida (canned responses).

Endpoints:
- POST   /v1/canned-responses                    — crear template
- GET    /v1/canned-responses                    — listar (paginado)
- GET    /v1/canned-responses/search             — buscar por shortcode/text (Fase 26D)
- GET    /v1/canned-responses/{id}               — detalle
- GET    /v1/canned-responses/{id}/render        — renderizar con variables (Fase 26D)
- PATCH  /v1/canned-responses/{id}               — actualizar
- DELETE /v1/canned-responses/{id}               — eliminar

Variables en templates: {{nombre}}, {{producto}}, etc.
Solo letras a-z, A-Z, 0-9 y guion bajo dentro de {{ }}.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.inbox.models import CannedResponse
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id

# Regex: {{variable_name}} donde variable_name solo letras/dígitos/guion_bajo.
_VAR_PATTERN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
# Detecta variables mal formadas: {{ }} con contenido no válido.
_INVALID_VAR_PATTERN = re.compile(r"\{\{[^}]*\}\}")

router = APIRouter(prefix="/canned-responses", tags=["canned-responses"])


def _validate_variables(text: str) -> None:
    """Lanza 422 si el texto tiene variables mal formadas en {{ }}."""
    all_double_braces = _INVALID_VAR_PATTERN.findall(text)
    valid_vars = _VAR_PATTERN.findall(text)
    # Una variable bien formada tiene la forma {{nombre_valido}}.
    # Si hay {{ }} que no matchean el patrón válido, es un error.
    for match in _INVALID_VAR_PATTERN.finditer(text):
        inner = match.group(0)[2:-2]  # quitar {{ }}
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", inner):
            raise HTTPException(
                status_code=422,
                detail=f"Variable mal formada: '{match.group(0)}'. "
                       "Solo se permiten letras, dígitos y guion bajo, comenzando con letra o guion bajo.",
            )


def _extract_variables(text: str) -> list[str]:
    """Devuelve la lista de nombres de variables en el template."""
    return _VAR_PATTERN.findall(text)


def _render_template(text: str, variables: dict[str, str]) -> str:
    """Reemplaza {{variable}} con el valor correspondiente del dict."""
    def replacer(m: re.Match) -> str:
        name = m.group(1)
        return variables.get(name, m.group(0))
    return _VAR_PATTERN.sub(replacer, text)


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
    variables: list[str] = []
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_vars(cls, cr: CannedResponse) -> "CannedResponseOut":
        obj = cls.model_validate(cr)
        obj.variables = _extract_variables(cr.text)
        return obj


class RenderOut(BaseModel):
    id: uuid.UUID
    shortcode: str
    original_text: str
    rendered_text: str
    variables_used: dict[str, str]
    variables_missing: list[str]


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
    _validate_variables(body.text.strip())

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
    return CannedResponseOut.from_orm_with_vars(cr)


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
        items=[CannedResponseOut.from_orm_with_vars(cr) for cr in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=CannedResponseListOut)
def search_canned(
    q: str = Query(..., description="Texto a buscar en shortcode o text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CannedResponseListOut:
    """Busca templates por shortcode o texto (búsqueda case-insensitive)."""
    tenant_id = get_current_tenant_id()
    q_clean = (q or "").strip()
    if not q_clean:
        raise HTTPException(status_code=422, detail="El parámetro 'q' es obligatorio")

    like = f"%{q_clean}%"
    offset = (page - 1) * page_size

    with bypass_tenant_filter():
        base_q = (
            db.query(CannedResponse)
            .filter(
                CannedResponse.tenant_id == tenant_id,
                or_(
                    CannedResponse.shortcode.ilike(like),
                    CannedResponse.text.ilike(like),
                ),
            )
            .order_by(CannedResponse.shortcode)
        )
        total = base_q.count()
        items = base_q.offset(offset).limit(page_size).all()

    return CannedResponseListOut(
        items=[CannedResponseOut.from_orm_with_vars(cr) for cr in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{canned_id}/render", response_model=RenderOut)
def render_canned(
    canned_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RenderOut:
    """Renderiza un template reemplazando {{variable}} con los query params.

    Ejemplo: GET /v1/canned-responses/{id}/render?nombre=Juan&producto=Zapatillas
    Variables no provistas quedan sin reemplazar en el texto renderizado.
    """
    tenant_id = get_current_tenant_id()
    cr = _get_canned(canned_id, tenant_id, db)

    # Query params como variables (excluir params internos de FastAPI si los hubiera).
    vars_provided: dict[str, str] = dict(request.query_params)

    declared_vars = _extract_variables(cr.text)
    variables_used = {k: v for k, v in vars_provided.items() if k in declared_vars}
    variables_missing = [v for v in declared_vars if v not in vars_provided]

    rendered = _render_template(cr.text, vars_provided)

    return RenderOut(
        id=cr.id,
        shortcode=cr.shortcode,
        original_text=cr.text,
        rendered_text=rendered,
        variables_used=variables_used,
        variables_missing=variables_missing,
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
    return CannedResponseOut.from_orm_with_vars(cr)


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
        _validate_variables(body.text.strip())
        cr.text = body.text.strip()

    cr.updated_at = datetime.now(UTC)
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return CannedResponseOut.from_orm_with_vars(cr)


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
