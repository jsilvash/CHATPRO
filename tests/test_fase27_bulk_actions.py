"""Tests Fase 27-B: Bulk actions en inbox.

Cubre:
- POST /v1/inbox/bulk-assign actualiza múltiples convs.
- POST /v1/inbox/bulk-assign con user_id de otro tenant → 422.
- POST /v1/inbox/bulk-tag aplica etiqueta (idempotente).
- POST /v1/inbox/bulk-close cambia status + registra historial.
- IDs inexistentes no producen error (se omiten silenciosamente).
- Aislamiento multi-tenant: convs de otro tenant se ignoran.
"""

from __future__ import annotations

import uuid

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.inbox.models import ConversationStatusHistory, ConversationTag
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test WA",
        waha_session_name=f"sess_{uuid.uuid4().hex[:8]}",
        phone="56900000001",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(
    db,
    tenant_id: uuid.UUID,
    wa_number: WaNumber,
    status: str = "bot",
    assigned_user_id=None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name="Test Contacto",
        status=status,
        assigned_user_id=assigned_user_id,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, tenant_id: uuid.UUID) -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Bulk",
        role="agent",
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


# ── Tests bulk-assign ─────────────────────────────────────────────────────────


class TestBulkAssign:
    def test_bulk_assign_actualiza_multiples_convs(self, client, db, tenant_a):
        """bulk-assign asigna el agente a todas las convs válidas."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn)
        c2 = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        resp = client.post("/v1/inbox/bulk-assign", json={
            "conversation_ids": [str(c1.id), str(c2.id)],
            "user_id": str(agent.id),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 2
        assert data["errors"] == []

        db.refresh(c1)
        db.refresh(c2)
        assert c1.assigned_user_id == agent.id
        assert c2.assigned_user_id == agent.id

    def test_bulk_assign_user_de_otro_tenant_retorna_422(self, client, db, tenant_a, tenant_b):
        """bulk-assign con user_id de otro tenant → 422."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b
        wn_b = _make_wa_number(db, tenant_b_obj.id)
        agent_b = _make_agent(db, tenant_b_obj.id)

        wn_a = _make_wa_number(db, tenant_a_obj.id)
        c1 = _make_conversation(db, tenant_a_obj.id, wn_a)

        client.headers.update(_auth_header(owner_a))
        resp = client.post("/v1/inbox/bulk-assign", json={
            "conversation_ids": [str(c1.id)],
            "user_id": str(agent_b.id),
        })
        assert resp.status_code == 422

    def test_bulk_assign_ids_inexistentes_se_omiten(self, client, db, tenant_a):
        """IDs de conversaciones que no existen (o de otro tenant) se omiten sin error."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        resp = client.post("/v1/inbox/bulk-assign", json={
            "conversation_ids": [str(c1.id), str(uuid.uuid4())],
            "user_id": str(agent.id),
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1  # solo la existente

    def test_bulk_assign_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        """bulk-assign ignora conversaciones de otro tenant."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, _owner_b = tenant_b
        wn_b = _make_wa_number(db, tenant_b_obj.id)
        conv_b = _make_conversation(db, tenant_b_obj.id, wn_b)

        wn_a = _make_wa_number(db, tenant_a_obj.id)
        agent_a = _make_agent(db, tenant_a_obj.id)

        client.headers.update(_auth_header(owner_a))
        resp = client.post("/v1/inbox/bulk-assign", json={
            "conversation_ids": [str(conv_b.id)],
            "user_id": str(agent_a.id),
        })
        # No error pero updated=0 (la conv era de otro tenant)
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0
        db.refresh(conv_b)
        assert conv_b.assigned_user_id is None


# ── Tests bulk-tag ────────────────────────────────────────────────────────────


class TestBulkTag:
    def test_bulk_tag_aplica_etiqueta(self, client, db, tenant_a):
        """bulk-tag aplica la etiqueta a todas las convs del tenant."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn)
        c2 = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        resp = client.post("/v1/inbox/bulk-tag", json={
            "conversation_ids": [str(c1.id), str(c2.id)],
            "tag": "vip",
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        with bypass_tenant_filter():
            tags = (
                db.query(ConversationTag)
                .filter(
                    ConversationTag.wa_conversation_id.in_([c1.id, c2.id]),
                    ConversationTag.tag == "vip",
                )
                .count()
            )
        assert tags == 2

    def test_bulk_tag_es_idempotente(self, client, db, tenant_a):
        """bulk-tag es idempotente: aplicar la misma etiqueta dos veces no duplica."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        for _ in range(2):
            resp = client.post("/v1/inbox/bulk-tag", json={
                "conversation_ids": [str(c1.id)],
                "tag": "duplicado",
            })
            assert resp.status_code == 200

        with bypass_tenant_filter():
            count = (
                db.query(ConversationTag)
                .filter(
                    ConversationTag.wa_conversation_id == c1.id,
                    ConversationTag.tag == "duplicado",
                )
                .count()
            )
        assert count == 1


# ── Tests bulk-close ──────────────────────────────────────────────────────────


class TestBulkClose:
    def test_bulk_close_cambia_status(self, client, db, tenant_a):
        """bulk-close pone todas las convs en status=bot."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn, status="agent")
        c2 = _make_conversation(db, tenant.id, wn, status="waiting_agent")

        client.headers.update(_auth_header(owner))
        resp = client.post("/v1/inbox/bulk-close", json={
            "conversation_ids": [str(c1.id), str(c2.id)],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        db.refresh(c1)
        db.refresh(c2)
        assert c1.status == "bot"
        assert c2.status == "bot"

    def test_bulk_close_registra_historial(self, client, db, tenant_a):
        """bulk-close registra ConversationStatusHistory para cada conv cerrada."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn, status="agent")

        client.headers.update(_auth_header(owner))
        resp = client.post("/v1/inbox/bulk-close", json={
            "conversation_ids": [str(c1.id)],
        })
        assert resp.status_code == 200

        with bypass_tenant_filter():
            history = (
                db.query(ConversationStatusHistory)
                .filter(
                    ConversationStatusHistory.wa_conversation_id == c1.id,
                    ConversationStatusHistory.new_status == "bot",
                )
                .count()
            )
        assert history >= 1

    def test_bulk_close_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        """bulk-close no toca conversaciones de otro tenant."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, _owner_b = tenant_b
        wn_b = _make_wa_number(db, tenant_b_obj.id)
        conv_b = _make_conversation(db, tenant_b_obj.id, wn_b, status="agent")

        client.headers.update(_auth_header(owner_a))
        resp = client.post("/v1/inbox/bulk-close", json={
            "conversation_ids": [str(conv_b.id)],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0
        db.refresh(conv_b)
        assert conv_b.status == "agent"  # sin cambios
