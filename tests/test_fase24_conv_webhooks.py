"""Tests Fase 24A — Notificaciones push outbound vía webhooks para eventos de conversación.

Cubre:
- message.received se emite al llegar webhook de WAHA.
- message.sent se emite al responder el bot (agent/service._persist_outbound).
- conversation.created se emite al crear conversación nueva.
- conversation.status_changed se emite en take y close.
- Aislamiento: emit_event solo recibe webhooks del tenant correcto.
- No se emite si el tenant no tiene webhooks configurados (no-op silencioso).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.messaging.webhook import _dispatch_message, _get_or_create_conversation
from src.public_api.dispatcher import emit_event
from src.public_api.models import WebhookDelivery, WebhookOut
from src.wa.models import WaConversation, WaMessage, WaNumber, WaSession


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test",
        waha_session_name=f"session-{uuid.uuid4().hex[:8]}",
        phone="5491100000001",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_webhook(db, tenant_id: uuid.UUID, events: list[str]) -> WebhookOut:
    wh = WebhookOut(
        tenant_id=tenant_id,
        url="http://example.com/hook",
        secret="secret123",
        events=events,
        enabled=True,
    )
    db.add(wh)
    db.flush()
    return wh


def _make_conversation(db, tenant_id: uuid.UUID, wn: WaNumber) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone="5491199990001",
        wa_contact_name="Test Contact",
        status="waiting_agent",
    )
    db.add(conv)
    db.flush()
    return conv


def _make_inbound_event(wn_id: uuid.UUID, phone: str, body: str, msg_id: str) -> dict:
    return {
        "event": "message",
        "payload": {
            "id": msg_id,
            "from": f"{phone}@c.us",
            "senderPn": f"{phone}@c.us",
            "fromMe": False,
            "body": body,
            "timestamp": 1700000000,
        },
    }


# ── Test: no-op cuando no hay webhooks configurados ──────────────────────────


def test_emit_event_sin_webhooks_es_noop(db, tenant_a):
    """emit_event no falla si el tenant no tiene webhooks configurados."""
    tenant, _owner = tenant_a
    count = emit_event(
        tenant.id,
        "message.received",
        {"conversation_id": str(uuid.uuid4()), "tenant_id": str(tenant.id)},
        db,
    )
    assert count == 0


# ── Test: message.received al llegar webhook WAHA ────────────────────────────


def test_message_received_emitido_al_llegar_webhook(client, db, tenant_a):
    """message.received se crea una WebhookDelivery al procesar mensaje entrante WAHA."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    _make_webhook(db, tenant.id, ["message.received"])
    db.commit()

    phone = "5491122223333"
    msg_id = f"true_{phone}_abc123"
    event_payload = _make_inbound_event(wn.id, phone, "hola mundo", msg_id)

    # Mockear el agente para que no llame a Claude.
    with patch("src.messaging.webhook._maybe_respond_with_agent"):
        resp = client.post(
            f"/webhook/waha/{wn.id}",
            json=event_payload,
            headers={"X-WAHA-Token": ""},
        )

    assert resp.status_code == 200

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "message.received",
        )
        .all()
    )
    assert len(deliveries) >= 1


# ── Test: conversation.created al crear conversación nueva ───────────────────


def test_conversation_created_emitido_en_nueva_conv(client, db, tenant_a):
    """conversation.created se emite cuando llega el primer mensaje de un contacto nuevo."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    _make_webhook(db, tenant.id, ["conversation.created"])
    db.commit()

    phone = "5491155556666"
    msg_id = f"true_{phone}_new"
    event_payload = _make_inbound_event(wn.id, phone, "primera vez", msg_id)

    with patch("src.messaging.webhook._maybe_respond_with_agent"):
        resp = client.post(
            f"/webhook/waha/{wn.id}",
            json=event_payload,
            headers={"X-WAHA-Token": ""},
        )

    assert resp.status_code == 200

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "conversation.created",
        )
        .all()
    )
    assert len(deliveries) >= 1


# ── Test: conversation.created NO se re-emite si la conv ya existe ───────────


def test_conversation_created_no_se_duplica(client, db, tenant_a):
    """conversation.created solo se emite una vez para un contacto dado."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    _make_webhook(db, tenant.id, ["conversation.created"])

    # Crear la conversación previamente para simular que ya existe.
    _make_conversation(db, tenant.id, wn)
    db.commit()

    phone = "5491199990001"  # mismo phone que la conv ya creada
    msg_id = f"true_{phone}_second"
    event_payload = _make_inbound_event(wn.id, phone, "segundo mensaje", msg_id)

    with patch("src.messaging.webhook._maybe_respond_with_agent"):
        client.post(
            f"/webhook/waha/{wn.id}",
            json=event_payload,
            headers={"X-WAHA-Token": ""},
        )

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "conversation.created",
        )
        .all()
    )
    # No debe haber creado ninguna delivery de conversation.created (conv ya existía).
    assert len(deliveries) == 0


# ── Test: conversation.status_changed en take ────────────────────────────────


def test_status_changed_emitido_en_take(client, db, tenant_a):
    """conversation.status_changed se emite al hacer take de una conversación."""
    from src.auth.tokens import create_access_token

    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)
    _make_webhook(db, tenant.id, ["conversation.status_changed"])
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.post(
        f"/v1/inbox/{conv.id}/take",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "conversation.status_changed",
        )
        .all()
    )
    assert len(deliveries) >= 1
    payload = deliveries[0].payload
    assert payload["new_status"] == "agent"


# ── Test: conversation.status_changed en close ───────────────────────────────


def test_status_changed_emitido_en_close(client, db, tenant_a):
    """conversation.status_changed se emite al cerrar una conversación."""
    from src.auth.tokens import create_access_token

    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)
    conv.status = "agent"
    db.add(conv)
    _make_webhook(db, tenant.id, ["conversation.status_changed"])
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.post(
        f"/v1/inbox/{conv.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "conversation.status_changed",
        )
        .all()
    )
    assert len(deliveries) >= 1
    payload = deliveries[0].payload
    assert payload["new_status"] == "bot"


# ── Test: aislamiento — tenant B no recibe eventos de tenant A ───────────────


def test_aislamiento_emit_event_solo_tenant_correcto(db, tenant_a, tenant_b):
    """emit_event solo crea deliveries para los webhooks del tenant emisor."""
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    # Webhook de tenant A.
    wh_a = _make_webhook(db, tenant_a_obj.id, ["message.received"])
    # Webhook de tenant B.
    wh_b = _make_webhook(db, tenant_b_obj.id, ["message.received"])
    db.commit()

    count = emit_event(
        tenant_a_obj.id,
        "message.received",
        {"conversation_id": str(uuid.uuid4()), "tenant_id": str(tenant_a_obj.id)},
        db,
    )
    assert count == 1  # Solo el webhook de tenant A.

    deliveries_b = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == wh_b.id)
        .all()
    )
    assert len(deliveries_b) == 0  # Tenant B no recibe nada.


# ── Test: message.sent via agent service ─────────────────────────────────────


def test_message_sent_emitido_por_agent(db, tenant_a):
    """message.sent se emite en _persist_outbound del agente."""
    from unittest.mock import patch, MagicMock
    from src.agent.service import _persist_outbound

    tenant, _owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="5491177778888",
        status="bot",
    )
    db.add(conv)
    _make_webhook(db, tenant.id, ["message.sent"])
    db.commit()

    with patch("src.messaging.dispatcher.send_text") as mock_send:
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.wa_message_id = "msg-123"
        mock_result.raw_response = {}
        mock_send.return_value = mock_result

        with patch("src.messaging.ws_manager.manager.broadcast_from_sync"):
            _persist_outbound(db, conv, wn, "Hola desde el bot")

    deliveries = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.tenant_id == tenant.id,
            WebhookDelivery.event == "message.sent",
        )
        .all()
    )
    assert len(deliveries) >= 1
    payload = deliveries[0].payload
    assert payload["source"] == "bot"
    assert payload["text"] == "Hola desde el bot"
