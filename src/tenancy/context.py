"""Contexto de tenant por request/tarea.

Cada request HTTP establece el tenant_id en este contexto antes de acceder
a la BD. Todas las queries operativas DEBEN filtrar por tenant_id; este
módulo provee la fuente de verdad del tenant activo.

Uso típico en dependencias FastAPI:
    tenant_id = get_current_tenant_id()
    users = db.query(User).filter(User.tenant_id == tenant_id).all()

Para webhooks y hooks de startup que necesitan trabajar sin tenant_scope:
    with bypass_tenant_filter():
        wa_num = db.query(WaNumber).filter(...).first()
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_tenant_id_var: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)
_bypass_var: ContextVar[bool] = ContextVar("bypass_tenant_filter", default=False)


@contextmanager
def tenant_scope(tenant_id: uuid.UUID):
    """Establece el tenant activo para el contexto de la tarea/request."""
    token = _tenant_id_var.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _tenant_id_var.reset(token)


@contextmanager
def bypass_tenant_filter():
    """Permite queries sin filtro de tenant.

    Solo para: webhook handlers (antes de resolver tenant), startup hooks,
    lookups globales (slug único, email global). Nunca en hot path de negocio.
    """
    token = _bypass_var.set(True)
    try:
        yield
    finally:
        _bypass_var.reset(token)


def get_current_tenant_id() -> uuid.UUID:
    """Devuelve el tenant_id activo. Lanza RuntimeError si no hay contexto."""
    tid = _tenant_id_var.get()
    if tid is None and not _bypass_var.get():
        raise RuntimeError(
            "No hay tenant_id en contexto. "
            "Usar tenant_scope() en el request handler o bypass_tenant_filter() si es intencional."
        )
    return tid  # type: ignore[return-value]


def get_tenant_id_optional() -> uuid.UUID | None:
    """Devuelve el tenant_id activo o None si no hay contexto (sin raise)."""
    return _tenant_id_var.get()
