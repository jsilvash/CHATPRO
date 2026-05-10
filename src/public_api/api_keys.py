"""API REST /v1/api-keys — gestión de API keys por tenant (Fase 10).

Endpoints:
- POST   /v1/api-keys           — crea una nueva API key (devuelve token RAW una sola vez).
- GET    /v1/api-keys           — lista las API keys del tenant (sin token raw).
- GET    /v1/api-keys/{id}      — detalle de una API key.
- DELETE /v1/api-keys/{id}      — revoca (soft-delete) una API key.
- GET    /v1/api-keys/me        — info de la key que autentica la request actual.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.db.models import User
from src.db.session import get_db
from src.public_api.dependencies import get_api_key
from src.public_api.models import ApiKey

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

_SCOPES_VALIDOS = {"read", "write", "admin"}


# ── Schemas ──────────────────────────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = ["read"]


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    """Respuesta de creación — incluye el token raw (se muestra UNA sola vez)."""
    token: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _generate_token() -> tuple[str, str, str]:
    """Devuelve (token_raw, prefix_8, hashed_sha256)."""
    raw = secrets.token_hex(32)          # 64 chars hex
    prefix = raw[:8]
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, prefix, hashed


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def crear_api_key(
    body: ApiKeyCreate,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("owner", "admin")),
):
    """Crea una API key nueva para el tenant.

    El ``token`` del cuerpo de respuesta es el **único momento** en que
    se devuelve el valor crudo. Guardarlo de inmediato.
    """
    scopes_invalidos = set(body.scopes) - _SCOPES_VALIDOS
    if scopes_invalidos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Scopes inválidos: {sorted(scopes_invalidos)}. Válidos: {sorted(_SCOPES_VALIDOS)}",
        )
    if not body.scopes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Se requiere al menos un scope.",
        )

    raw, prefix, hashed = _generate_token()

    key = ApiKey(
        tenant_id=current_user.tenant_id,
        name=body.name,
        hashed_key=hashed,
        prefix=prefix,
        scopes=body.scopes,
        created_by_user_id=current_user.id,
    )
    db.add(key)
    db.flush()

    return ApiKeyCreated(
        id=key.id,
        tenant_id=key.tenant_id,
        name=key.name,
        prefix=key.prefix,
        scopes=key.scopes,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        created_by_user_id=key.created_by_user_id,
        created_at=key.created_at,
        token=raw,
    )


@router.get("", response_model=list[ApiKeyOut])
def listar_api_keys(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    incluir_revocadas: bool = False,
):
    """Lista las API keys del tenant autenticado."""
    q = db.query(ApiKey).filter(ApiKey.tenant_id == current_user.tenant_id)
    if not incluir_revocadas:
        q = q.filter(ApiKey.revoked_at.is_(None))
    return q.order_by(ApiKey.created_at.desc()).all()


@router.get("/me", response_model=ApiKeyOut)
def api_key_actual(
    key: ApiKey = Depends(get_api_key("")),
):
    """Devuelve la información de la API key que autentica la request actual."""
    return key


@router.get("/{key_id}", response_model=ApiKeyOut)
def obtener_api_key(
    key_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Obtiene el detalle de una API key del tenant."""
    key = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.tenant_id == current_user.tenant_id)
        .first()
    )
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key no encontrada.")
    return key


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revocar_api_key(
    key_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("owner", "admin")),
):
    """Revoca una API key (soft-delete). Las requests autenticadas con ella
    fallarán con 401 a partir de este momento."""
    key = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.tenant_id == current_user.tenant_id)
        .first()
    )
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key no encontrada.")
    if key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La API key ya está revocada.",
        )
    key.revoked_at = datetime.now(timezone.utc)
    db.flush()
