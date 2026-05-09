"""Middleware de auditoría: registra toda mutación HTTP en audit_log (Fase 11).

Estrategia:
- Intercepta POST/PUT/PATCH/DELETE en el ciclo de respuesta (después de 2xx).
- El actor se extrae del JWT Bearer (user_id) o del estado del request (api_key_id).
- target_type y target_id se infieren del path usando convenciones REST.
- diff vacío por defecto (datos sensibles no entran en el log).
"""

from __future__ import annotations

import re
import uuid
from typing import Callable

from fastapi import Request, Response
from sqlalchemy.orm import Session

from src.auth.tokens import decode_token
from src.billing.models import AuditLog
from src.db.session import get_db as _get_db_iter

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Segmento UUID en paths REST
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

# Mapeo de prefijo de path → target_type
_PATH_TYPE_MAP = [
    (r"/v1/wa-numbers", "wa_number"),
    (r"/v1/contacts", "contact"),
    (r"/v1/connectors", "connector"),
    (r"/v1/inbox", "handoff"),
    (r"/v1/knowledge", "kb_document"),
    (r"/v1/api-keys", "api_key"),
    (r"/v1/webhooks", "webhook_out"),
    (r"/v1/quotas", "quota"),
    (r"/v1/users", "user"),
    (r"/v1/tenants", "tenant"),
    (r"/v1/personas", "persona"),
]


def _infer_target(path: str) -> tuple[str | None, uuid.UUID | None]:
    target_type: str | None = None
    for prefix, ttype in _PATH_TYPE_MAP:
        if path.startswith(prefix):
            target_type = ttype
            break

    uuids = _UUID_RE.findall(path)
    target_id: uuid.UUID | None = uuid.UUID(uuids[-1]) if uuids else None
    return target_type, target_id


def _build_action(method: str, path: str) -> str:
    """Convierte método+path en acción legible: ej. 'POST /v1/contacts' → 'contact.create'."""
    target_type, target_id = _infer_target(path)
    base = target_type or "resource"

    if method == "POST":
        verb = "create"
    elif method == "PUT" or method == "PATCH":
        verb = "update"
    elif method == "DELETE":
        verb = "delete"
    else:
        verb = method.lower()

    return f"{base}.{verb}"


async def audit_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware ASGI que escribe en audit_log tras mutaciones exitosas."""
    response: Response = await call_next(request)

    method = request.method
    if method not in _MUTATING_METHODS:
        return response
    if response.status_code >= 400:
        return response

    path = request.url.path

    # Extraer actor desde JWT Bearer
    actor_user_id: uuid.UUID | None = None
    actor_api_key_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            payload = decode_token(token, "access")
            actor_user_id = uuid.UUID(payload["sub"])
            tenant_id = uuid.UUID(payload["tid"])
        except Exception:
            pass

    # Si no hay user_id, puede ser una api_key auth (state set en dependency)
    if actor_user_id is None:
        api_key_tenant = getattr(getattr(request, "state", None), "api_key_tenant_id", None)
        if api_key_tenant is not None:
            tenant_id = api_key_tenant

    target_type, target_id = _infer_target(path)
    action = _build_action(method, path)
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        db: Session = next(_get_db_iter())
        try:
            entry = AuditLog(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                actor_api_key_id=actor_api_key_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                diff=None,
                ip=ip,
                user_agent=user_agent,
            )
            db.add(entry)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # audit nunca bloquea el request

    return response
