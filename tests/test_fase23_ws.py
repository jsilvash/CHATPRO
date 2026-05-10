"""Tests Fase 23A — WebSocket inbox en tiempo real.

Cubre:
- connect OK con token válido.
- connect sin token → cierre con código 1008.
- connect con token inválido → 1008.
- connect con token de otro tenant → 1008.
- connect a conversación inexistente → 1008.
- broadcast llega a clientes conectados (test async con mock WebSocket).
- disconnect limpia el set del ConnectionManager.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from src.auth.tokens import create_access_token
from src.messaging.ws_manager import ConnectionManager, manager
from src.wa.models import WaConversation


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID):
    from src.wa.models import WaNumber
    wn = WaNumber(
        tenant_id=tenant_id,
        display_name="Test WA",
        waha_session_name="test-session",
        phone_e164="+56911111111",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(db, tenant_id: uuid.UUID, wa_number_id: uuid.UUID) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number_id,
        wa_contact_phone="+56912345678",
        wa_contact_name="Test",
        status="bot",
    )
    db.add(conv)
    db.flush()
    return conv


# ── Tests de conexión HTTP/WS (sync) ─────────────────────────────────────────


def test_ws_connect_ok(client, tenant_a, db):
    """Conexión válida → servidor acepta sin cerrar inmediatamente."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id)
    token = create_access_token(owner.id, tenant.id, owner.role)

    with client.websocket_connect(f"/ws/inbox/{conv.id}?token={token}") as ws:
        ws.send_text("ping")
        # Si la conexión fue aceptada, send_text no lanza excepción.


def test_ws_connect_sin_token_cierra_1008(client, tenant_a, db):
    """Sin token → cierra con código 1008."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/inbox/{conv.id}") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_ws_connect_token_invalido_cierra_1008(client, tenant_a, db):
    """Token inválido → cierra con código 1008."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/inbox/{conv.id}?token=token-invalido") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_ws_connect_token_otro_tenant_cierra_1008(client, tenant_a, tenant_b, db):
    """Token de tenant B intentando acceder a conversación de tenant A → 1008."""
    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    wn = _make_wa_number(db, tenant_a_obj.id)
    conv = _make_conversation(db, tenant_a_obj.id, wn.id)

    token_b = create_access_token(owner_b.id, tenant_b_obj.id, owner_b.role)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/inbox/{conv.id}?token={token_b}") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_ws_connect_conv_inexistente_cierra_1008(client, tenant_a, db):
    """Conversación inexistente → 1008 (protege contra enumeración)."""
    tenant, owner = tenant_a
    token = create_access_token(owner.id, tenant.id, owner.role)
    conv_id_falso = uuid.uuid4()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/inbox/{conv_id_falso}?token={token}") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_ws_disconnect_limpia_el_set(client, tenant_a, db):
    """Al cerrar el WS, el entry del conversation_id se elimina del manager."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id)
    token = create_access_token(owner.id, tenant.id, owner.role)

    assert manager.client_count(conv.id) == 0

    with client.websocket_connect(f"/ws/inbox/{conv.id}?token={token}") as _ws:
        assert manager.client_count(conv.id) == 1

    assert manager.client_count(conv.id) == 0


# ── Tests async del ConnectionManager (sin HTTP) ─────────────────────────────


@pytest.mark.asyncio
async def test_manager_broadcast_llega_a_mock_ws():
    """broadcast() envía JSON a todos los WebSocket registrados."""
    m = ConnectionManager()
    conv_id = uuid.uuid4()

    mock_ws = AsyncMock(spec=WebSocket)
    await m.connect(conv_id, mock_ws)
    mock_ws.accept.assert_awaited_once()

    data = {"event": "message", "text": "hola"}
    await m.broadcast(conv_id, data)

    mock_ws.send_json.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_manager_broadcast_multiples_clientes():
    """broadcast() envía a TODOS los clientes conectados a la misma conversación."""
    m = ConnectionManager()
    conv_id = uuid.uuid4()

    ws1, ws2 = AsyncMock(spec=WebSocket), AsyncMock(spec=WebSocket)
    await m.connect(conv_id, ws1)
    await m.connect(conv_id, ws2)
    assert m.client_count(conv_id) == 2

    data = {"event": "x"}
    await m.broadcast(conv_id, data)

    ws1.send_json.assert_awaited_once_with(data)
    ws2.send_json.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_manager_broadcast_desconecta_ws_muertos():
    """Si send_json falla, el WebSocket muerto se elimina del manager."""
    m = ConnectionManager()
    conv_id = uuid.uuid4()

    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.send_json.side_effect = RuntimeError("WS cerrado")
    await m.connect(conv_id, mock_ws)

    await m.broadcast(conv_id, {"event": "test"})

    assert m.client_count(conv_id) == 0


@pytest.mark.asyncio
async def test_manager_disconnect_limpia_conversation():
    """disconnect() elimina el conversation_id del dict cuando no quedan clientes."""
    m = ConnectionManager()
    conv_id = uuid.uuid4()

    mock_ws = AsyncMock(spec=WebSocket)
    await m.connect(conv_id, mock_ws)
    assert m.client_count(conv_id) == 1

    m.disconnect(conv_id, mock_ws)
    assert m.client_count(conv_id) == 0


@pytest.mark.asyncio
async def test_manager_broadcast_conv_sin_clientes():
    """broadcast() a una conversación sin clientes no lanza excepción."""
    m = ConnectionManager()
    await m.broadcast(uuid.uuid4(), {"event": "test"})  # debe ser no-op silencioso
