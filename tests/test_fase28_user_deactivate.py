"""Tests Fase 28-B: PATCH /v1/users/{user_id}/deactivate.

Cubre:
- deactivate pone is_active=False.
- Convs activas se reasignan al agente con menos carga.
- Sin agentes disponibles → convs quedan sin asignar.
- No puede desactivarse a sí mismo → 422.
- user_id de otro tenant → 404.
- Convs de otro tenant no se reasignan (aislamiento).
- Respuesta contiene reassigned_conversations correcto.
"""

from __future__ import annotations

import uuid

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner

ENDPOINT = "/v1/users/{user_id}/deactivate"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test WA",
        waha_session_name=f"sess_{uuid.uuid4().hex[:8]}",
        phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
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
        wa_contact_name="Contacto Test",
        status=status,
        assigned_user_id=assigned_user_id,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, tenant_id: uuid.UUID, role: str = "agent", is_active: bool = True) -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Test",
        role=role,
        is_active=is_active,
    )
    db.add(agent)
    db.flush()
    return agent


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDeactivateUser:
    def test_deactivate_pone_is_active_false(self, client, db, tenant_a):
        """PATCH deactivate pone is_active=False en el usuario objetivo."""
        tenant, owner = tenant_a
        agent = _make_agent(db, tenant.id)
        assert agent.is_active is True

        client.headers.update(_auth_header(owner))
        resp = client.patch(ENDPOINT.format(user_id=str(agent.id)))
        assert resp.status_code == 200

        db.refresh(agent)
        assert agent.is_active is False

    def test_convs_activas_se_reasignan(self, client, db, tenant_a):
        """Las convs con status agent/waiting_agent se reasignan al agente con menos carga."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        agente_a_desactivar = _make_agent(db, tenant.id)
        agente_receptor = _make_agent(db, tenant.id)

        # 2 convs activas asignadas al agente a desactivar
        c1 = _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agente_a_desactivar.id)
        c2 = _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=agente_a_desactivar.id)
        # 1 conv cerrada (no debe reasignarse)
        c3 = _make_conversation(db, tenant.id, wn, status="bot", assigned_user_id=agente_a_desactivar.id)

        client.headers.update(_auth_header(owner))
        resp = client.patch(ENDPOINT.format(user_id=str(agente_a_desactivar.id)))
        assert resp.status_code == 200

        data = resp.json()
        assert data["reassigned_conversations"] == 2
        assert data["deactivated_user_id"] == str(agente_a_desactivar.id)

        db.refresh(c1)
        db.refresh(c2)
        db.refresh(c3)
        assert c1.assigned_user_id == agente_receptor.id
        assert c2.assigned_user_id == agente_receptor.id
        assert c3.assigned_user_id == agente_a_desactivar.id  # no cambia (cerrada)

    def test_sin_agentes_disponibles_quedan_sin_asignar(self, client, db, tenant_a):
        """Si no hay agentes activos, las convs quedan assigned_user_id=None."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        # Solo hay un agente y es el que desactivamos
        agente = _make_agent(db, tenant.id)
        c1 = _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agente.id)
        c2 = _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=agente.id)

        client.headers.update(_auth_header(owner))
        resp = client.patch(ENDPOINT.format(user_id=str(agente.id)))
        assert resp.status_code == 200

        data = resp.json()
        assert data["reassigned_conversations"] == 2
        assert data["new_assignee_id"] is None

        db.refresh(c1)
        db.refresh(c2)
        assert c1.assigned_user_id is None
        assert c2.assigned_user_id is None

    def test_no_puede_desactivarse_a_si_mismo(self, client, db, tenant_a):
        """Un admin/owner no puede desactivarse a sí mismo → 422."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.patch(ENDPOINT.format(user_id=str(owner.id)))
        assert resp.status_code == 422

    def test_user_id_otro_tenant_devuelve_404(self, client, db, tenant_a, tenant_b):
        """user_id de otro tenant devuelve 404."""
        _, owner_a = tenant_a
        tenant_b_obj, _ = tenant_b
        agente_b = _make_agent(db, tenant_b_obj.id)

        client.headers.update(_auth_header(owner_a))
        resp = client.patch(ENDPOINT.format(user_id=str(agente_b.id)))
        assert resp.status_code == 404

    def test_convs_otro_tenant_no_se_reasignan(self, client, db, tenant_a, tenant_b):
        """Las convs de otro tenant no se tocan (aislamiento)."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b

        wn_a = _make_wa_number(db, tenant_a_obj.id)
        wn_b = _make_wa_number(db, tenant_b_obj.id)

        agente_a = _make_agent(db, tenant_a_obj.id)
        agente_b = _make_agent(db, tenant_b_obj.id)

        # Conv del tenant B asignada al agente B (no debe cambiar)
        conv_b = _make_conversation(
            db, tenant_b_obj.id, wn_b, status="agent", assigned_user_id=agente_b.id
        )

        # Conv del tenant A asignada al agente A (se reasigna)
        conv_a = _make_conversation(
            db, tenant_a_obj.id, wn_a, status="agent", assigned_user_id=agente_a.id
        )

        client.headers.update(_auth_header(owner_a))
        resp = client.patch(ENDPOINT.format(user_id=str(agente_a.id)))
        assert resp.status_code == 200

        db.refresh(conv_b)
        assert conv_b.assigned_user_id == agente_b.id  # intacto

    def test_respuesta_contiene_reassigned_conversations(self, client, db, tenant_a):
        """La respuesta incluye deactivated_user_id, reassigned_conversations y new_assignee_id."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agente = _make_agent(db, tenant.id)
        receptor = _make_agent(db, tenant.id)

        _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agente.id)
        _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=agente.id)
        _make_conversation(db, tenant.id, wn, status="bot", assigned_user_id=agente.id)

        client.headers.update(_auth_header(owner))
        resp = client.patch(ENDPOINT.format(user_id=str(agente.id)))
        assert resp.status_code == 200

        data = resp.json()
        assert "deactivated_user_id" in data
        assert "reassigned_conversations" in data
        assert "new_assignee_id" in data
        assert data["deactivated_user_id"] == str(agente.id)
        assert data["reassigned_conversations"] == 2
        assert data["new_assignee_id"] == str(receptor.id)
