import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user, require_role
from src.auth.passwords import hash_password
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import get_current_tenant_id

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""
    role: str = "agent"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("owner", "admin", "agent"):
            raise ValueError("Rol inválido. Opciones: owner, admin, agent")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        return v


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("owner", "admin", "agent"):
            raise ValueError("Rol inválido. Opciones: owner, admin, agent")
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int


@router.get("", response_model=UserListResponse)
def list_users(
    q: str | None = Query(None, description="Buscar por nombre o email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    query = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True))

    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            (User.email.ilike(like)) | (User.full_name.ilike(like))
        )

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return UserListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    existing = (
        db.query(User).filter(User.tenant_id == tenant_id, User.email == payload.email).first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="El email ya está en uso en este tenant")

    user = User(
        tenant_id=tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()
    user = (
        db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenant_id = get_current_tenant_id()

    # Agentes solo pueden modificarse a sí mismos
    if current_user.role == "agent" and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sin permisos para modificar otros usuarios")

    user = (
        db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None and current_user.role in ("owner", "admin"):
        user.role = payload.role
    if payload.is_active is not None and current_user.role in ("owner", "admin"):
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return user
