"""Dependencia de autenticación por API key (Fase 10).

Uso en endpoints públicos:
    @router.get("/endpoint")
    def mi_endpoint(key: ApiKey = Depends(get_api_key("read"))):
        tenant_id = key.tenant_id
        ...

El token crudo llega como Bearer en el header Authorization.
Se valida contra el SHA-256 almacenado en ``api_keys.hashed_key``.
"""

import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.billing.quota import check_quota
from src.db.session import get_db
from src.public_api.models import ApiKey
from src.tenancy.context import _tenant_id_var, bypass_tenant_filter

_bearer = HTTPBearer()


def get_api_key(scope: str = ""):
    """Factory que devuelve una dependencia FastAPI para autenticar por API key.

    Args:
        scope: Scope requerido (vacío = solo validar que la key es activa).

    Returns:
        Dependencia que extrae, valida y devuelve el ``ApiKey`` activo.
    """

    def _dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(_bearer),
        db: Session = Depends(get_db),
    ) -> ApiKey:
        raw_token = credentials.credentials
        hashed = hashlib.sha256(raw_token.encode()).hexdigest()

        with bypass_tenant_filter():
            key = (
                db.query(ApiKey)
                .filter(ApiKey.hashed_key == hashed)
                .first()
            )

        if not key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key inválida",
            )
        if key.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key revocada",
            )
        if scope and scope not in key.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scope requerido: {scope}",
            )

        key.last_used_at = datetime.now(timezone.utc)
        db.flush()

        check_quota(key.tenant_id, "api_requests", 1, db)

        # Establecer tenant scope en el ContextVar del worker actual y en el
        # request state para que el endpoint (otro worker) pueda leerlo si
        # lo necesita sin llamar a get_current_tenant_id().
        _tenant_id_var.set(key.tenant_id)
        request.state.api_key_tenant_id = key.tenant_id

        return key

    return _dependency
