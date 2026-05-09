"""Estado in-memory del indicador 'escribiendo…' del contacto.

WAHA emite eventos cuando el contacto cambia presencia (composing/recording/
paused). Un store in-memory alcanza: el indicador es efímero por definición
y un restart del worker simplemente lo deja "no typing" hasta el próximo
evento.

TTL = 15s. WhatsApp normalmente envía ``paused`` al soltar el teclado, pero
no siempre llega — el TTL evita que el flag quede pegado indefinidamente.

Clave: ``(session_name, normalized_phone)``.
"""

import threading
import time

_state: dict[tuple[str, str], float] = {}
_lock = threading.Lock()

_TTL_SECONDS = 15.0


def set_typing(session_name: str, phone: str, *, ttl: float = _TTL_SECONDS) -> None:
    """Marca al contacto como 'escribiendo' por ``ttl`` segundos."""
    if not session_name or not phone:
        return
    with _lock:
        _state[(session_name, phone)] = time.time() + ttl


def clear_typing(session_name: str, phone: str) -> None:
    """Limpia el flag (contacto envió ``paused`` o el mensaje llegó)."""
    if not session_name or not phone:
        return
    with _lock:
        _state.pop((session_name, phone), None)


def is_typing(session_name: str, phone: str) -> bool:
    """``True`` si el contacto tiene typing activo no caducado.

    Limpia entradas caducadas in-place para que el dict no crezca ilimitado.
    """
    if not session_name or not phone:
        return False
    now = time.time()
    with _lock:
        exp = _state.get((session_name, phone))
        if exp is None:
            return False
        if exp <= now:
            _state.pop((session_name, phone), None)
            return False
        return True


def _clear_all() -> None:
    """Solo para tests."""
    with _lock:
        _state.clear()
