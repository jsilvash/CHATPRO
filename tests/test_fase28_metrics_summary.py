"""Tests Fase 28-A: Dashboard de métricas del tenant (resumen ejecutivo).

Cubre:
- GET /v1/metrics/dashboard devuelve valores correctos con datos de test.
- unassigned_waiting correcto (convs con status=waiting_agent y assigned_user_id=NULL).
- conversations_closed_today usa resolved_at de hoy.
- Aislamiento multi-tenant.
- Tenant sin datos devuelve ceros (no error).
- messages_in_today y messages_out_today correctos.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaMessage, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner

ENDPOINT = "/v1/metrics/dashboard"


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
    resolved_at=None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name="Test Contacto",
        status=status,
        assigned_user_id=assigned_user_id,
        resolved_at=resolved_at,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_message(
    db,
    tenant_id: uuid.UUID,
    conv: WaConversation,
    direction: str = "in",
    created_at=None,
) -> WaMessage:
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_conversation_id=conv.id,
        wa_number_id=conv.wa_number_id,
        wa_message_id=f"msg_{uuid.uuid4().hex}",
        direction=direction,
        text="Hola",
        ack="sent",
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(msg)
    db.flush()
    return msg


def _make_agent(db, tenant_id: uuid.UUID, is_active: bool = True) -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Test",
        role="agent",
        is_active=is_active,
    )
    db.add(agent)
    db.flush()
    return agent


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMetricsDashboard:
    def test_valores_correctos_con_datos(self, client, db, tenant_a):
        """El endpoint calcula correctamente todas las métricas con datos de test."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        # 1 conv bot, 1 conv agent, 1 conv waiting_agent
        _make_conversation(db, tenant.id, wn, status="bot")
        _make_conversation(db, tenant.id, wn, status="agent")
        _make_conversation(db, tenant.id, wn, status="waiting_agent")

        # Mensajes de hoy
        conv = _make_conversation(db, tenant.id, wn, status="bot")
        _make_message(db, tenant.id, conv, direction="in")
        _make_message(db, tenant.id, conv, direction="out")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        assert data["conversations_total"] >= 4
        assert data["conversations_active"] >= 2  # agent + waiting_agent
        assert data["conversations_bot"] >= 2      # bot + conv extra
        assert data["messages_in_today"] >= 1
        assert data["messages_out_today"] >= 1

    def test_unassigned_waiting_correcto(self, client, db, tenant_a):
        """unassigned_waiting cuenta solo convs waiting_agent sin asignar."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        # 2 sin asignar, 1 asignada
        _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=None)
        _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=None)
        _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=agent.id)

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        assert data["unassigned_waiting"] == 2

    def test_conversations_closed_today_usa_resolved_at(self, client, db, tenant_a):
        """conversations_closed_today cuenta convs con resolved_at = hoy."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        # resolved_at hoy
        now = datetime.now(timezone.utc)
        _make_conversation(db, tenant.id, wn, status="bot", resolved_at=now)
        _make_conversation(db, tenant.id, wn, status="bot", resolved_at=now)

        # resolved_at antiguo (no debe contar)
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        _make_conversation(db, tenant.id, wn, status="bot", resolved_at=old)

        # sin resolved_at (no debe contar)
        _make_conversation(db, tenant.id, wn, status="bot")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        assert data["conversations_closed_today"] == 2

    def test_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        """Las métricas son exclusivas del tenant autenticado."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b

        wn_a = _make_wa_number(db, tenant_a_obj.id)
        wn_b = _make_wa_number(db, tenant_b_obj.id)

        # Tenant A: 3 convs
        for _ in range(3):
            _make_conversation(db, tenant_a_obj.id, wn_a, status="waiting_agent")

        # Tenant B: 10 convs (no deben verse desde A)
        for _ in range(10):
            _make_conversation(db, tenant_b_obj.id, wn_b, status="waiting_agent")

        client.headers.update(_auth_header(owner_a))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        assert data["conversations_total"] == 3
        assert data["conversations_active"] == 3
        assert data["unassigned_waiting"] == 3

    def test_tenant_sin_datos_devuelve_ceros(self, client, db, tenant_a):
        """Tenant sin conversaciones ni mensajes devuelve ceros, no error."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        assert data["conversations_total"] == 0
        assert data["conversations_active"] == 0
        assert data["conversations_bot"] == 0
        assert data["conversations_closed_today"] == 0
        assert data["messages_in_today"] == 0
        assert data["messages_out_today"] == 0
        assert data["unassigned_waiting"] == 0

    def test_agents_online_correctos(self, client, db, tenant_a):
        """agents_online cuenta solo agentes/admins con is_active=True."""
        tenant, owner = tenant_a
        # owner ya es 'owner', no se cuenta como agent/admin en este contexto
        _make_agent(db, tenant.id, is_active=True)
        _make_agent(db, tenant.id, is_active=True)
        _make_agent(db, tenant.id, is_active=False)  # no debe contar

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        data = resp.json()
        # owner tiene role='owner', no es agent/admin → no se cuenta
        # 2 activos + 1 inactivo → solo 2 activos
        assert data["agents_online"] == 2

    def test_requiere_autenticacion(self, client):
        """Sin token devuelve 401/403."""
        resp = client.get(ENDPOINT)
        assert resp.status_code in (401, 403)
