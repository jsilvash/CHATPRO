"""Tests Fase 25D: Notificaciones in-app para agentes (WebSocket).

Cubre:
- NotificationManager: connect/disconnect/broadcast/broadcast_from_sync.
- Broadcast en _auto_escalate_if_needed cuando bot no puede resolver.
- Endpoint /ws/notifications/{tenant_id}: auth por JWT, aislamiento de tenant.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.tokens import create_access_token
from src.db.models import Tenant, User
from src.messaging.ws_manager import NotificationManager, notification_manager
from src.wa.models import WaConversation, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant: Tenant) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant.id,
        label="Test Notif",
        waha_session_name=f"sess-notif-{uuid.uuid4().hex[:8]}",
        phone="5491100000003",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conv(db, tenant: Tenant, wn: WaNumber) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone=f"549110{uuid.uuid4().int % 10000000:07d}",
        status="bot",
    )
    db.add(conv)
    db.flush()
    return conv


# ── Tests NotificationManager ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_manager_connect_disconnect():
    """Connect/disconnect actualiza correctamente los contadores."""
    mgr = NotificationManager()
    tenant_id = uuid.uuid4()

    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await mgr.connect(tenant_id, ws1)
    assert mgr.client_count(tenant_id) == 1

    await mgr.connect(tenant_id, ws2)
    assert mgr.client_count(tenant_id) == 2

    mgr.disconnect(tenant_id, ws1)
    assert mgr.client_count(tenant_id) == 1

    mgr.disconnect(tenant_id, ws2)
    assert mgr.client_count(tenant_id) == 0


@pytest.mark.asyncio
async def test_notification_manager_broadcast_envia_a_conectados():
    """broadcast() envía el evento JSON a todos los sockets del tenant."""
    mgr = NotificationManager()
    tenant_id = uuid.uuid4()

    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await mgr.connect(tenant_id, ws1)
    await mgr.connect(tenant_id, ws2)

    event = {"event": "conversation.waiting_agent", "conversation_id": str(uuid.uuid4())}
    await mgr.broadcast(tenant_id, event)

    ws1.send_json.assert_called_once_with(event)
    ws2.send_json.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_notification_manager_broadcast_aislado_por_tenant():
    """broadcast() a tenant A no afecta sockets del tenant B."""
    mgr = NotificationManager()
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()

    ws_a = AsyncMock()
    ws_b = AsyncMock()

    await mgr.connect(tenant_a_id, ws_a)
    await mgr.connect(tenant_b_id, ws_b)

    await mgr.broadcast(tenant_a_id, {"event": "test"})

    ws_a.send_json.assert_called_once()
    ws_b.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_notification_manager_limpia_sockets_muertos():
    """broadcast() elimina sockets que lanzaron excepción."""
    mgr = NotificationManager()
    tenant_id = uuid.uuid4()

    ws_dead = AsyncMock()
    ws_dead.send_json.side_effect = RuntimeError("socket cerrado")
    ws_alive = AsyncMock()

    await mgr.connect(tenant_id, ws_dead)
    await mgr.connect(tenant_id, ws_alive)
    assert mgr.client_count(tenant_id) == 2

    await mgr.broadcast(tenant_id, {"event": "ping"})

    assert mgr.client_count(tenant_id) == 1


# ── Tests broadcast en auto-escalation ────────────────────────────────────────


def test_auto_escalate_llama_broadcast(db, tenant_a):
    """_auto_escalate_if_needed dispara broadcast de notificación."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    from src.agent.service import _auto_escalate_if_needed

    with patch("src.messaging.ws_manager.NotificationManager.broadcast_from_sync") as mock_broadcast:
        # Patch el singleton
        notification_manager.broadcast_from_sync = mock_broadcast
        _auto_escalate_if_needed(db, conv, "max_tool_calls")

    assert conv.status == "waiting_agent"
    mock_broadcast.assert_called_once()
    call_kwargs = mock_broadcast.call_args
    # Primer arg = tenant_id, segundo arg = data dict
    data = call_kwargs[0][1]
    assert data["event"] == "conversation.waiting_agent"
    assert data["conversation_id"] == str(conv.id)
    assert data["tenant_id"] == str(tenant.id)


def test_auto_escalate_no_dispara_si_ya_escalado(db, tenant_a):
    """Si el status no es bot, no se dispara broadcast."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)
    conv.status = "waiting_agent"
    db.add(conv)
    db.flush()

    from src.agent.service import _auto_escalate_if_needed

    mock_broadcast = MagicMock()
    notification_manager.broadcast_from_sync = mock_broadcast
    _auto_escalate_if_needed(db, conv, "max_tool_calls")

    mock_broadcast.assert_not_called()


# ── Tests WS endpoint /ws/notifications/{tenant_id} ──────────────────────────


def test_ws_notifications_token_invalido_cierra_1008(client_a, db, tenant_a):
    """Token inválido en /ws/notifications → cierre con código 1008."""
    from starlette.websockets import WebSocketDisconnect

    tenant, owner = tenant_a

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_a.websocket_connect(
            f"/ws/notifications/{tenant.id}?token=TOKEN_INVALIDO"
        ) as ws:
            pass

    assert exc_info.value.code == 1008


def test_ws_notifications_tenant_mismatch_cierra_1008(client_a, db, tenant_a, tenant_b):
    """Token del tenant B intentando conectarse al canal del tenant A → 1008."""
    from starlette.websockets import WebSocketDisconnect

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    token_b = create_access_token(owner_b.id, owner_b.tenant_id, owner_b.role)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client_a.websocket_connect(
            f"/ws/notifications/{tenant_a_obj.id}?token={token_b}"
        ) as ws:
            pass

    assert exc_info.value.code == 1008


def test_ws_notifications_acepta_conexion_valida(client_a, db, tenant_a):
    """Token válido con tenant correcto acepta la conexión sin cerrar con 1008."""
    from starlette.websockets import WebSocketDisconnect

    tenant, owner = tenant_a
    token = create_access_token(owner.id, owner.tenant_id, owner.role)

    try:
        with client_a.websocket_connect(
            f"/ws/notifications/{tenant.id}?token={token}"
        ) as ws:
            pass
    except WebSocketDisconnect as e:
        if e.code == 1008:
            pytest.fail("Conexión válida fue rechazada con 1008")
