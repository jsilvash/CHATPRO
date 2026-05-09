"""ACK monotonicity — 16 transiciones + timestamps.

Tabla de verdad cubierta exhaustivamente:

| current\\incoming | sent | delivered | read | failed |
|-------------------|------|-----------|------|--------|
| sent              | sent (no-op)    | delivered (apply) | read (apply) | failed (apply) |
| delivered         | delivered (no-op) | delivered (no-op) | read (apply) | delivered (REJECT) |
| read              | read (no-op)    | read (no-op)        | read (no-op)    | read (REJECT)      |
| failed            | failed (no-op)  | failed (no-op)      | failed (no-op)  | failed (no-op)     |
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.messaging.ack import apply_ack_update, update_message_ack

# ────────────────────────────────────────────────────────────
# Las 16 transiciones desde estado válido (sent/delivered/read/failed).
# ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,incoming,expected",
    [
        # Desde sent
        ("sent", "sent", "sent"),
        ("sent", "delivered", "delivered"),
        ("sent", "read", "read"),
        ("sent", "failed", "failed"),
        # Desde delivered
        ("delivered", "sent", "delivered"),
        ("delivered", "delivered", "delivered"),
        ("delivered", "read", "read"),
        ("delivered", "failed", "delivered"),
        # Desde read
        ("read", "sent", "read"),
        ("read", "delivered", "read"),
        ("read", "read", "read"),
        ("read", "failed", "read"),
        # Desde failed (terminal)
        ("failed", "sent", "failed"),
        ("failed", "delivered", "failed"),
        ("failed", "read", "failed"),
        ("failed", "failed", "failed"),
    ],
)
def test_apply_ack_update_16_transitions(current, incoming, expected):
    assert apply_ack_update(current, incoming) == expected


def test_apply_ack_update_initial_state():
    """Desde "" (sin ACK) puede ir a cualquier estado."""
    assert apply_ack_update("", "sent") == "sent"
    assert apply_ack_update("", "delivered") == "delivered"
    assert apply_ack_update("", "read") == "read"
    assert apply_ack_update("", "failed") == "failed"


def test_apply_ack_update_rejects_unknown():
    assert apply_ack_update("sent", "garbage") == "sent"
    assert apply_ack_update("", "") == ""


# ────────────────────────────────────────────────────────────
# update_message_ack — sets timestamp y devuelve True/False.
# ────────────────────────────────────────────────────────────


class _FakeMessage:
    def __init__(self):
        self.ack = ""
        self.sent_at = None
        self.delivered_at = None
        self.read_at = None
        self.failed_at = None


def test_update_message_ack_advances_and_sets_timestamp():
    m = _FakeMessage()

    assert update_message_ack(m, "sent") is True
    assert m.ack == "sent" and m.sent_at is not None
    sent_ts = m.sent_at

    # No-op: same state
    assert update_message_ack(m, "sent") is False
    assert m.sent_at == sent_ts

    # Advance to delivered
    assert update_message_ack(m, "delivered") is True
    assert m.ack == "delivered" and m.delivered_at is not None

    # Cannot go back
    assert update_message_ack(m, "sent") is False
    assert m.ack == "delivered"

    # Advance to read
    assert update_message_ack(m, "read") is True
    assert m.ack == "read" and m.read_at is not None

    # read is absorbing
    assert update_message_ack(m, "failed") is False
    assert m.ack == "read"


def test_update_message_ack_failed_terminal():
    m = _FakeMessage()
    m.ack = "sent"
    m.sent_at = datetime.now(UTC)

    assert update_message_ack(m, "failed") is True
    assert m.ack == "failed" and m.failed_at is not None

    # Una vez failed, terminal
    assert update_message_ack(m, "delivered") is False
    assert update_message_ack(m, "read") is False
    assert update_message_ack(m, "sent") is False
    assert m.ack == "failed"


def test_update_message_ack_failed_blocked_after_delivered():
    """Si ya fue entregado, un failed tardío se descarta (out-of-order)."""
    m = _FakeMessage()
    m.ack = "delivered"
    m.delivered_at = datetime.now(UTC)

    assert update_message_ack(m, "failed") is False
    assert m.ack == "delivered"
    assert m.failed_at is None
