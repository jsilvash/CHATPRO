"""Tests de WaConversation: turn_count y campos de Fase 3."""

from __future__ import annotations

import uuid

import pytest

from src.messaging import waha_client
from src.wa.models import WaConversation, WaNumber


@pytest.fixture(autouse=True)
def _stub_waha(monkeypatch):
    monkeypatch.setattr(waha_client, "create_session", lambda *a, **kw: {"name": a[0]})


@pytest.fixture
def wa_number(client_a, db):
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "Hub turnos", "waha_session_name": "wn-turnos"},
    )
    assert r.status_code == 201
    wid = uuid.UUID(r.json()["id"])
    return db.get(WaNumber, wid)


def _inbound_payload(phone: str, body: str, msg_id: str) -> dict:
    return {
        "event": "message",
        "session": "wn-turnos",
        "payload": {
            "id": msg_id,
            "from": f"{phone}@c.us",
            "fromMe": False,
            "body": body,
            "senderPn": f"{phone}@c.us",
            "timestamp": 1715200000,
        },
    }


class TestTurnCount:
    def test_turn_count_inicia_en_cero(self, db, tenant_a):
        """Una conversación nueva tiene turn_count == 0."""
        tenant, _ = tenant_a
        wn = WaNumber(
            tenant_id=tenant.id,
            label="Num cero",
            waha_session_name="test-turn-zero",
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56920000001",
        )
        db.add(conv)
        db.flush()

        assert conv.turn_count == 0

    def test_turn_count_incrementa_en_inbound(self, client, wa_number, db):
        """Cada mensaje inbound incrementa turn_count en 1."""
        phone = "56933000001"

        client.post(
            f"/webhook/waha/{wa_number.id}",
            json=_inbound_payload(phone, "Hola", f"wa-tc-001-{phone}"),
        )

        conv = (
            db.query(WaConversation)
            .filter(WaConversation.wa_contact_phone == phone)
            .first()
        )
        assert conv is not None
        assert conv.turn_count == 1

        client.post(
            f"/webhook/waha/{wa_number.id}",
            json=_inbound_payload(phone, "¿Tienen stock?", f"wa-tc-002-{phone}"),
        )
        db.refresh(conv)
        assert conv.turn_count == 2

    def test_turn_count_no_incrementa_en_echo_outbound(self, client, wa_number, db):
        """Los ecos de mensajes outbound (fromMe=True, is_any) no incrementan turn_count."""
        phone = "56933000002"

        # Inbound real
        client.post(
            f"/webhook/waha/{wa_number.id}",
            json=_inbound_payload(phone, "Hola", f"wa-tc-out-001-{phone}"),
        )

        # Echo outbound (message.any fromMe)
        echo_payload = {
            "event": "message.any",
            "session": "wn-turnos",
            "payload": {
                "id": f"true_{phone}@c.us_ECHO",
                "from": f"{phone}@c.us",
                "fromMe": True,
                "body": "Hola desde el bot",
                "senderPn": f"{phone}@c.us",
                "timestamp": 1715200001,
            },
        }
        client.post(f"/webhook/waha/{wa_number.id}", json=echo_payload)

        conv = (
            db.query(WaConversation)
            .filter(WaConversation.wa_contact_phone == phone)
            .first()
        )
        assert conv is not None
        # Solo 1 inbound real — el echo no cuenta
        assert conv.turn_count == 1

    def test_turn_count_dedupe_no_incrementa(self, client, wa_number, db):
        """Un mensaje duplicado (mismo wa_message_id) no incrementa turn_count."""
        phone = "56933000003"
        payload = _inbound_payload(phone, "Duplicado", f"wa-tc-dup-{phone}")

        client.post(f"/webhook/waha/{wa_number.id}", json=payload)
        client.post(f"/webhook/waha/{wa_number.id}", json=payload)  # duplicado

        conv = (
            db.query(WaConversation)
            .filter(WaConversation.wa_contact_phone == phone)
            .first()
        )
        assert conv is not None
        assert conv.turn_count == 1

    def test_ai_summary_inicia_null(self, db, tenant_a):
        """Una conversación nueva tiene ai_summary == None."""
        tenant, _ = tenant_a
        wn = WaNumber(
            tenant_id=tenant.id,
            label="Num null",
            waha_session_name="test-turn-null",
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56920000099",
        )
        db.add(conv)
        db.flush()

        assert conv.ai_summary is None
