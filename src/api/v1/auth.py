import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.auth.passwords import verify_password
from src.auth.tokens import create_access_token, create_refresh_token, decode_token
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import bypass_tenant_filter

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    with bypass_tenant_filter():
        user = (
            db.query(User)
            .filter(User.email == payload.email, User.is_active.is_(True))
            .first()
        )

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
        )

    access_token = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token, _jti = create_refresh_token(user.id, user.tenant_id)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token, "refresh")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    user_id = uuid.UUID(data["sub"])
    tenant_id = uuid.UUID(data["tid"])

    with bypass_tenant_filter():
        user = (
            db.query(User)
            .filter(User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True))
            .first()
        )

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")

    access_token = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token, _jti = create_refresh_token(user.id, user.tenant_id)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=204)
def logout(_current_user: User = Depends(get_current_user)):
    # Con JWTs stateless, el logout es client-side (borrar tokens).
    # Fase futura: almacenar JTI en Redis y validar en cada request.
    return None
