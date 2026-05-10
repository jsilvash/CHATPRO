import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.auth.tokens import decode_token
from src.db.models import User
from src.db.session import get_db
from src.tenancy.context import _tenant_id_var, bypass_tenant_filter

_bearer = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Valida el JWT, establece tenant_scope y devuelve el usuario activo."""
    try:
        payload = decode_token(credentials.credentials, "access")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    user_id = uuid.UUID(payload["sub"])
    tenant_id = uuid.UUID(payload["tid"])

    # Establece el tenant scope para este request (ContextVar task-local en asyncio;
    # copiado al thread pool por anyio cuando el endpoint es def).
    _tenant_id_var.set(tenant_id)

    with bypass_tenant_filter():
        user = (
            db.query(User)
            .filter(User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True))
            .first()
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
        )
    return user


def require_role(*roles: str):
    """Dependencia de autorización por rol. Uso: Depends(require_role('owner','admin'))."""

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Se requiere rol: {' o '.join(roles)}",
            )
        return current_user

    return _check
