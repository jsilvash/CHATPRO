"""Lookup de ``WaNumber`` para webhooks entrantes.

El webhook viene en ``POST /webhook/waha/{wa_number_id}`` — el ``wa_number_id``
en la URL es nuestra fuente primaria de routing. Esta función la valida
contra DB usando ``bypass_tenant_filter()`` (estamos antes de tener tenant
en contexto) y devuelve el ``WaNumber`` activo.

Cache 5 min por ``wa_number_id`` para evitar 1 query por mensaje entrante.
Los binding cambian raramente (solo cuando admin reconfigura un número).

Se conserva ``get_wa_number_by_session_name()`` por compatibilidad con
``ensure_waha_webhooks`` y casos donde solo se conoce la session.
``get_tenant_by_phone_number_id()`` (Cloud API) **eliminado** — el Hub
solo soporta WAHA.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaNumber

logger = logging.getLogger(__name__)

# Cache: {key: (wa_number_id, cached_at)}
_id_cache: dict[uuid.UUID, tuple[uuid.UUID, float]] = {}
_session_cache: dict[str, tuple[uuid.UUID, float]] = {}
_CACHE_TTL = 300  # 5 minutos


def get_wa_number_by_id(db: Session, wa_number_id: uuid.UUID) -> WaNumber | None:
    """Devuelve el ``WaNumber`` activo por id. Bypass tenant filter."""
    if not wa_number_id:
        return None
    now = time.time()

    cached = _id_cache.get(wa_number_id)
    if cached:
        cid, cached_at = cached
        if now - cached_at < _CACHE_TTL:
            with bypass_tenant_filter():
                wn = db.get(WaNumber, cid)
            if wn and wn.active:
                return wn
            _id_cache.pop(wa_number_id, None)

    with bypass_tenant_filter():
        wn = db.get(WaNumber, wa_number_id)

    if wn and wn.active:
        _id_cache[wa_number_id] = (wn.id, now)
        return wn

    logger.warning("No WaNumber activo para id=%s", wa_number_id)
    return None


def get_wa_number_by_session_name(db: Session, session_name: str) -> WaNumber | None:
    """Devuelve el ``WaNumber`` activo por nombre de sesión WAHA."""
    if not session_name:
        return None
    now = time.time()

    cached = _session_cache.get(session_name)
    if cached:
        wa_id, cached_at = cached
        if now - cached_at < _CACHE_TTL:
            with bypass_tenant_filter():
                wn = db.get(WaNumber, wa_id)
            if wn and wn.active:
                return wn
            _session_cache.pop(session_name, None)

    with bypass_tenant_filter():
        wn = (
            db.query(WaNumber)
            .filter(
                WaNumber.waha_session_name == session_name,
                WaNumber.active.is_(True),
            )
            .first()
        )

    if wn:
        _session_cache[session_name] = (wn.id, now)
        return wn

    logger.warning("No WaNumber para session_name=%s", session_name)
    return None


def invalidate_cache(wa_number_id: uuid.UUID | None = None) -> None:
    """Invalida caches (todo o por id)."""
    if wa_number_id is not None:
        _id_cache.pop(wa_number_id, None)
    else:
        _id_cache.clear()
        _session_cache.clear()
