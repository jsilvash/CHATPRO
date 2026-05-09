"""Estado in-memory del indicador 'escribiendo…' del contacto.

Evolution emite eventos `PRESENCE_UPDATE` con `presence=composing|recording|
paused|available` cada vez que el contacto cambia de estado en WhatsApp. No
tenemos webhook de Cloud API para esto (Meta no lo expone inbound), así que
esta funcionalidad es exclusiva del path QR.

Un store in-memory alcanza: el indicador es efímero por definición (si el
worker se reinicia, el contacto simplemente deja de verse 'escribiendo' por
unos segundos hasta el próximo evento). No vale la pena persistirlo.

TTL = 15s. WhatsApp normalmente envía `paused` al soltar el teclado, pero no
siempre llega — el TTL garantiza que el indicador no se quede pegado si el
`paused` se pierde.
"""

import threading
import time

# {(instance_name, normalized_phone): expires_at_unix_ts}
_state: dict[tuple[str, str], float] = {}
_lock = threading.Lock()

_TTL_SECONDS = 15.0


def set_typing(instance_name: str, phone: str, *, ttl: float = _TTL_SECONDS) -> None:
    """Marca al contacto como 'escribiendo' por `ttl` segundos."""
    if not instance_name or not phone:
        return
    with _lock:
        _state[(instance_name, phone)] = time.time() + ttl


def clear_typing(instance_name: str, phone: str) -> None:
    """Limpia el flag (contacto envió `paused` o el mensaje llegó)."""
    if not instance_name or not phone:
        return
    with _lock:
        _state.pop((instance_name, phone), None)


def is_typing(instance_name: str, phone: str) -> bool:
    """`True` si el contacto tiene un typing activo y no caducó.

    Caducar in-place limpia la entrada para que el dict no crezca ilimitado
    en sedes con mucho tráfico.
    """
    if not instance_name or not phone:
        return False
    now = time.time()
    with _lock:
        exp = _state.get((instance_name, phone))
        if exp is None:
            return False
        if exp <= now:
            _state.pop((instance_name, phone), None)
            return False
        return True


def _clear_all() -> None:
    """Solo para tests."""
    with _lock:
        _state.clear()
