"""Tests Fase 27-D: Webhook events para notas e historial.

Cubre:
- note.created se emite al crear una nota (mock emit_event).
- note.created NO se emite si la creación falla (texto vacío → 422).
- conversation.status_changed se emite en _auto_escalate_if_needed.
- payload de note.created contiene los campos correctos.
- Aislamiento: emit_event recibe el tenant_id correcto.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.wa.models import WaConversation, WaMessage, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="WH Events",
        waha_session_name=f"sess_{uuid.uuid4().hex[:8]}",
        phone="56900000003",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_agent(db, tenant_id: uuid.UUID) -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@wh.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Webhook",
        role="agent",
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_conversation(
    db,
    tenant_id: uuid.UUID,
    wa_number: WaNumber,
    status: str = "bot",
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name="Webhook Contact",
        status=status,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_message(db, tenant_id: uuid.UUID, conv: WaConversation, wn: WaNumber) -> WaMessage:
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="in",
        text="hola",
        ack="sent",
        raw_payload={},
        llm_metadata={},
    )
    db.add(msg)
    db.flush()
    return msg


# ── Tests note.created ────────────────────────────────────────────────────────


class TestNoteCreatedEvent:
    def test_note_created_se_emite_al_crear_nota(self, client, db, tenant_a):
        """emit_event('note.created') se llama cuando se crea una nota."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))

        with patch("src.public_api.dispatcher.emit_event") as mock_emit:
            mock_emit.return_value = 0
            resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "nota de prueba"})
            assert resp.status_code == 201
            mock_emit.assert_called_once()

    def test_note_created_payload_correcto(self, client, db, tenant_a):
        """El payload de note.created contiene todos los campos requeridos."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))

        captured_calls = []

        def capture_emit(tid, event, payload, db_session):
            captured_calls.append((tid, event, payload))
            return 0

        with patch("src.public_api.dispatcher.emit_event", side_effect=capture_emit):
            resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "nota con payload"})
            assert resp.status_code == 201

        note_calls = [(t, e, p) for t, e, p in captured_calls if e == "note.created"]
        assert len(note_calls) == 1
        _tid, _event, payload = note_calls[0]
        assert payload["event"] == "note.created"
        assert payload["conversation_id"] == str(conv.id)
        assert "note_id" in payload
        assert "user_id" in payload
        assert "text_preview" in payload
        assert payload["text_preview"] == "nota con payload"
        assert "tenant_id" in payload

    def test_note_created_tenant_id_correcto(self, client, db, tenant_a):
        """emit_event recibe el tenant_id correcto del tenant que crea la nota."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))

        captured_calls = []

        def capture_emit(tid, event, payload, db_session):
            captured_calls.append((tid, event, payload))
            return 0

        with patch("src.public_api.dispatcher.emit_event", side_effect=capture_emit):
            resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": "nota tenant"})
            assert resp.status_code == 201

        note_calls = [(t, e, p) for t, e, p in captured_calls if e == "note.created"]
        assert len(note_calls) == 1
        tid, _event, payload = note_calls[0]
        assert str(tid) == str(tenant.id)
        assert payload["tenant_id"] == str(tenant.id)

    def test_note_created_no_emite_si_texto_vacio(self, client, db, tenant_a):
        """emit_event NO se llama si la nota falla por texto vacío."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))

        with patch("src.public_api.dispatcher.emit_event") as mock_emit:
            resp = client.post(f"/v1/inbox/{conv.id}/notes", json={"text": ""})
            assert resp.status_code == 422
            mock_emit.assert_not_called()

    def test_status_changed_se_emite_en_auto_escalate(self, client, db, tenant_a):
        """conversation.status_changed se emite cuando _auto_escalate_if_needed escala."""
        from src.agent.service import _auto_escalate_if_needed

        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn, status="bot")

        captured_calls = []

        def capture_emit(tid, event, payload, db_session):
            captured_calls.append((tid, event, payload))
            return 0

        with patch("src.public_api.dispatcher.emit_event", side_effect=capture_emit):
            _auto_escalate_if_needed(db, conv, "max_tool_calls")
            db.flush()

        status_calls = [(t, e, p) for t, e, p in captured_calls if e == "conversation.status_changed"]
        assert len(status_calls) >= 1
        _tid, _event, payload = status_calls[0]
        assert payload["new_status"] == "waiting_agent"
        assert payload["conversation_id"] == str(conv.id)
