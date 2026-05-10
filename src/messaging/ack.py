"""Invariante de monotonicidad para el ACK de mensajes salientes.

Estados:
    sent < delivered < read   (orden lineal de progreso)
    failed                    (terminal, sólo desde ``sent``)

Reglas:
- Una vez ``failed`` o ``read``, ningún update puede cambiar el estado
  (ambos son absorbentes en distintas ramas del flujo).
- ``failed`` solo es alcanzable desde ``sent`` (o desde ``""`` inicial):
  si el mensaje ya fue entregado o leído, ``failed`` es una llegada tardía
  desordenada de webhooks y se descarta.
- En el resto de transiciones se aplica si el rank aumenta; si baja, se
  descarta como evento desordenado / out-of-order.

Tabla de transiciones (16 desde estado válido) — implementada en
``apply_ack_update``:

| current\\incoming | sent | delivered | read | failed |
|-------------------|------|-----------|------|--------|
| sent              | sent | delivered | read | failed |
| delivered         | delivered | delivered | read | delivered |
| read              | read | read | read | read |
| failed            | failed | failed | failed | failed |
"""

from __future__ import annotations

from datetime import UTC, datetime

# Rank lineal del flujo "feliz". ``failed`` no entra en este orden — se
# evalúa por separado como caso especial (terminal sólo desde ``sent``).
_RANK = {
    "": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
}

VALID_STATES = ("sent", "delivered", "read", "failed")


def apply_ack_update(current: str, incoming: str) -> str:
    """Devuelve el nuevo estado tras intentar aplicar ``incoming`` sobre ``current``.

    Si la transición es válida devuelve ``incoming``; si no, ``current``.
    """
    if incoming not in VALID_STATES:
        return current

    # Estados terminales: una vez ahí, ningún update cambia.
    if current == "failed":
        return current
    if current == "read":
        return current

    # ``failed`` solo es alcanzable desde "" o "sent".
    if incoming == "failed":
        if current in ("", "sent"):
            return "failed"
        return current

    # Resto: comparar rank lineal.
    if _RANK[incoming] > _RANK.get(current, 0):
        return incoming
    return current


def update_message_ack(message, incoming: str) -> bool:
    """Aplica el invariante sobre un ``WaMessage`` ORM in-place.

    Setea el timestamp correspondiente (``sent_at``, ``delivered_at``,
    ``read_at``, ``failed_at``) sólo si el estado avanzó. Devuelve ``True``
    si hubo cambio efectivo, ``False`` si el update se descartó.
    """
    new_state = apply_ack_update(message.ack or "", incoming)
    if new_state == (message.ack or ""):
        return False

    message.ack = new_state
    now = datetime.now(UTC)
    if new_state == "sent" and message.sent_at is None:
        message.sent_at = now
    elif new_state == "delivered" and message.delivered_at is None:
        message.delivered_at = now
    elif new_state == "read" and message.read_at is None:
        message.read_at = now
    elif new_state == "failed" and message.failed_at is None:
        message.failed_at = now
    return True
