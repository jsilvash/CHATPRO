"""Lookup de tenant / WhatsAppNumber para webhooks entrantes.

Soporta dos shapes de lookup:

- Cloud API: el webhook de Meta incluye `phone_number_id` en el payload.
  Rutea al tenant buscando en `settings_json.whatsapp.phone_number_id`.
- Evolution (QR): el webhook de Evolution incluye `instance` (nombre de
  instancia). Rutea buscando `WhatsAppNumber.evolution_instance_name`,
  del cual sale `tenant_id`.

Ambos usan cache en memoria con TTL de 5 minutos — los binding cambian
rara vez (solo cuando admin reconfigura un número), así que el staleness
es tolerable y nos ahorra un query por mensaje entrante.
"""

import json
import logging
import time

from sqlalchemy.orm import Session

from src.db.models import Tenant, WhatsAppNumber

logger = logging.getLogger(__name__)

# Cache simple en memoria: {phone_number_id: (tenant_id, timestamp)}
_cache: dict[str, tuple[int, float]] = {}
# Cache separada para lookup por Evolution instance_name → wa_number_id.
_instance_cache: dict[str, tuple[int, float]] = {}
_CACHE_TTL = 300  # 5 minutos


def get_tenant_by_phone_number_id(db: Session, phone_number_id: str) -> Tenant | None:
    """Busca qué tenant tiene configurado este phone_number_id en su WhatsApp settings."""
    now = time.time()

    # Revisar cache
    if phone_number_id in _cache:
        tenant_id, cached_at = _cache[phone_number_id]
        if now - cached_at < _CACHE_TTL:
            return db.query(Tenant).get(tenant_id)

    # Buscar en todos los tenants activos (bypass tenant filter ya que es lookup global)
    from src.db.tenant_context import bypass_tenant_filter
    with bypass_tenant_filter():
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()

    for t in tenants:
        settings = json.loads(t.settings_json or "{}")
        wa = settings.get("whatsapp", {})
        if wa.get("enabled") and wa.get("phone_number_id") == phone_number_id:
            _cache[phone_number_id] = (t.id, now)
            return t

    logger.warning("No tenant found for phone_number_id=%s", phone_number_id)
    return None


def get_wa_number_by_instance_name(
    db: Session, instance_name: str
) -> WhatsAppNumber | None:
    """Busca un `WhatsAppNumber` por `evolution_instance_name`.

    Usa bypass del tenant filter: el webhook global no tiene tenant_scope
    aún — justamente lo resolvemos a partir de este lookup. La columna
    `evolution_instance_name` tiene índice único, así que la query es O(1).
    """
    if not instance_name:
        return None
    now = time.time()

    from src.db.tenant_context import bypass_tenant_filter

    cached = _instance_cache.get(instance_name)
    if cached:
        wa_id, cached_at = cached
        if now - cached_at < _CACHE_TTL:
            with bypass_tenant_filter():
                wn = db.query(WhatsAppNumber).get(wa_id)
            if wn and wn.active:
                return wn
            # Inválido o desactivado: limpiar cache y refrescar abajo.
            _instance_cache.pop(instance_name, None)

    with bypass_tenant_filter():
        wn = db.query(WhatsAppNumber).filter(
            WhatsAppNumber.evolution_instance_name == instance_name,
            WhatsAppNumber.active == True,  # noqa: E712
        ).first()

    if wn:
        _instance_cache[instance_name] = (wn.id, now)
        return wn

    logger.warning("No WhatsAppNumber for instance_name=%s", instance_name)
    return None


def invalidate_cache(phone_number_id: str | None = None) -> None:
    """Invalida la cache (completa o por phone_number_id específico)."""
    if phone_number_id:
        _cache.pop(phone_number_id, None)
    else:
        _cache.clear()


def invalidate_instance_cache(instance_name: str | None = None) -> None:
    """Invalida la cache de Evolution (completa o por instance_name)."""
    if instance_name:
        _instance_cache.pop(instance_name, None)
    else:
        _instance_cache.clear()
