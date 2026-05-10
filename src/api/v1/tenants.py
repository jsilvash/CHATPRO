import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.auth.passwords import hash_password
from src.db.models import Tenant, User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter, get_current_tenant_id

router = APIRouter(prefix="/tenants", tags=["tenants"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$")


class TenantCreate(BaseModel):
    name: str
    slug: str
    owner_email: EmailStr
    owner_password: str
    owner_full_name: str = ""

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "El slug solo puede contener minúsculas, números y guiones "
                "(3-50 chars, no puede empezar/terminar con guión)"
            )
        return v

    @field_validator("owner_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        return v


class TenantResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    plan: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=TenantResponse, status_code=201)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)):
    """Alta de tenant + usuario owner. Endpoint público (self-service)."""
    with bypass_tenant_filter():
        if db.query(Tenant).filter(Tenant.slug == payload.slug).first():
            raise HTTPException(status_code=400, detail="El slug ya está en uso")

        if db.query(User).filter(User.email == payload.owner_email).first():
            raise HTTPException(status_code=400, detail="El email ya está registrado")

    tenant = Tenant(slug=payload.slug, name=payload.name)
    db.add(tenant)
    db.flush()

    owner = User(
        tenant_id=tenant.id,
        email=payload.owner_email,
        hashed_password=hash_password(payload.owner_password),
        full_name=payload.owner_full_name,
        role="owner",
    )
    db.add(owner)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/me", response_model=TenantResponse)
def get_my_tenant(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    with bypass_tenant_filter():
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    return tenant


@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Un usuario solo puede ver su propio tenant."""
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    with bypass_tenant_filter():
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    return tenant
