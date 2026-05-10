import uuid
from datetime import datetime, timedelta, timezone

import jwt

from src.config import get_settings

_ALGORITHM = "HS256"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,
        "type": "access",
        "exp": _now() + timedelta(minutes=s.access_token_expire_minutes),
        "iat": _now(),
    }
    return jwt.encode(payload, s.secret_key, algorithm=_ALGORITHM)


def create_refresh_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, str]:
    """Devuelve (token, jti). El jti se almacena en Redis para revocación."""
    s = get_settings()
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "jti": jti,
        "type": "refresh",
        "exp": _now() + timedelta(days=s.refresh_token_expire_days),
        "iat": _now(),
    }
    return jwt.encode(payload, s.secret_key, algorithm=_ALGORITHM), jti


def decode_token(token: str, expected_type: str = "access") -> dict:
    """Decodifica y valida el token. Lanza ValueError en cualquier falla."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expirado")
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Token inválido: {exc}")

    if payload.get("type") != expected_type:
        raise ValueError(f"Tipo de token incorrecto: se esperaba '{expected_type}'")
    return payload
