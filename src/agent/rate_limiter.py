"""Rate limiter por (tenant_id, wa_contact_phone) para el agente.

Implementa sliding window: cuenta mensajes en los últimos WINDOW_SECONDS.

- Sin Redis: ventana in-memory con collections.deque, TTL implícito.
- Con Redis (REDIS_URL configurado): ZADD + ZREMRANGEBYSCORE + ZCARD atómico.

Si el límite se supera, `is_rate_limited()` retorna True y loggea WARNING.
El caller (service.respond) debe retornar silenciosamente sin llamar al agente.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

# Almacenamiento in-memory: (tenant_id_str, phone) → deque de timestamps float (unix)
_STORE: dict[tuple[str, str], deque[float]] = {}
_STORE_LOCK = Lock()


def _key(tenant_id: Any, phone: str) -> tuple[str, str]:
    return (str(tenant_id), phone)


# ── In-memory sliding window ─────────────────────────────────────────────────


def _check_inMemory(tenant_id: Any, phone: str, limit: int, window_s: int) -> bool:
    """Registra timestamp actual y retorna True si se superó el límite."""
    k = _key(tenant_id, phone)
    now = time.time()
    cutoff = now - window_s

    with _STORE_LOCK:
        dq = _STORE.get(k)
        if dq is None:
            dq = deque()
            _STORE[k] = dq

        # Purgar entradas fuera de la ventana
        while dq and dq[0] < cutoff:
            dq.popleft()

        count_before = len(dq)
        dq.append(now)

        if count_before >= limit:
            logger.warning(
                "rate_limit: superado | tenant_id=%s phone=%s count=%d limit=%d",
                tenant_id, phone, count_before + 1, limit,
            )
            return True
        return False


# ── Redis sliding window (cuando REDIS_URL esté configurado) ──────────────────


def _check_redis(redis_url: str, tenant_id: Any, phone: str, limit: int, window_s: int) -> bool:
    """Usa ZADD + ZREMRANGEBYSCORE + ZCARD en Redis para sliding window distribuida."""
    try:
        import redis as _redis  # type: ignore[import]
        client = _redis.from_url(redis_url, decode_responses=True)

        key = f"rl:{tenant_id}:{phone}"
        now = time.time()
        cutoff = now - window_s

        pipe = client.pipeline()
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_s * 2)
        results = pipe.execute()

        count = results[2]  # ZCARD result
        if count > limit:
            logger.warning(
                "rate_limit(redis): superado | tenant_id=%s phone=%s count=%d limit=%d",
                tenant_id, phone, count, limit,
            )
            return True
        return False
    except Exception as exc:
        logger.warning("rate_limit(redis): fallo, usando in-memory fallback: %s", exc)
        return _check_inMemory(tenant_id, phone, limit, window_s)


# ── Interfaz pública ─────────────────────────────────────────────────────────


def is_rate_limited(tenant_id: Any, phone: str, limit: int, window_s: int, redis_url: str = "") -> bool:
    """Retorna True si el (tenant, phone) superó el límite en la ventana deslizante."""
    if redis_url:
        return _check_redis(redis_url, tenant_id, phone, limit, window_s)
    return _check_inMemory(tenant_id, phone, limit, window_s)


def clear_for_testing(tenant_id: Any = None, phone: str = "") -> None:
    """Limpia el store in-memory. Solo para tests."""
    with _STORE_LOCK:
        if tenant_id is None:
            _STORE.clear()
        else:
            _STORE.pop(_key(tenant_id, phone), None)
