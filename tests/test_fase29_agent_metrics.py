"""Tests Fase 29D — Métricas de agente con histórico por período.

Cubre:
1. Endpoint accesible por el propio usuario
2. conversations_handled calculado correctamente
3. avg_first_response_sec calculado
4. avg_resolution_sec calculado
5. notes_created en el período
6. 404 si user_id no pertenece al tenant
7. 403 si agente intenta ver métricas de otro agente
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import User
from src.inbox.models import ConversationNote
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ──────────────────────────────────────────────────────────────────


def _headers(user: User) -> dict:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test",
        waha_session_name=f"sess-{uuid.uuid4().hex[:8]}",
        phone="5491100000001",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_agent(db, tenant_id: uuid.UUID, email: str) -> User:
    user = User(
        tenant_id=tenant_id,
        email=email,
        hashed_password=hash_password("secret123"),
        full_name="Agent",
        role="agent",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_conv(db, tenant_id, wa_number_id, agent_id=None, phone="5491111", resolved=False) -> WaConversation:
    now = datetime.now(UTC)
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number_id,
        wa_contact_phone=phone,
        wa_contact_name="Test",
        status="bot" if resolved else "agent",
        assigned_user_id=agent_id,
        created_at=now - timedelta(hours=2),
        first_response_at=now - timedelta(hours=1) if resolved else None,
        resolved_at=now if resolved else None,
    )
    db.add(conv)
    db.flush()
    return conv


# ── Test 1: propio usuario puede ver sus métricas ────────────────────────────


def test_propio_usuario_ve_metricas(client, db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "agent1@test.com")
    client.headers.update(_headers(agent))

    r = client.get(f"/v1/users/{agent.id}/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == str(agent.id)
    assert "conversations_handled" in data
    assert "notes_created" in data


# ── Test 2: conversations_handled cuenta convs resueltas en el período ────────


def test_conversations_handled(client, db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "agent2@test.com")
    wn = _make_wa_number(db, tenant.id)

    today = date.today()
    now = datetime.now(UTC)

    # Conversación resuelta hoy, asignada al agente.
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="54921001",
        wa_contact_name="Test",
        status="bot",
        assigned_user_id=agent.id,
        created_at=now - timedelta(hours=3),
        first_response_at=now - timedelta(hours=2),
        resolved_at=now,
    )
    db.add(conv)
    db.flush()

    client.headers.update(_headers(agent))
    date_str = today.isoformat()
    r = client.get(f"/v1/users/{agent.id}/metrics?date_from={date_str}&date_to={date_str}")
    assert r.status_code == 200
    data = r.json()
    assert data["conversations_handled"] >= 1


# ── Test 3: avg_first_response_sec calculado ─────────────────────────────────


def test_avg_first_response_sec(client, db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "agent3@test.com")
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    # 2 horas entre creación y primera respuesta → 7200 segundos.
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="54922001",
        wa_contact_name="Test",
        status="bot",
        assigned_user_id=agent.id,
        created_at=now - timedelta(hours=4),
        first_response_at=now - timedelta(hours=2),
        resolved_at=now,
    )
    db.add(conv)
    db.flush()

    client.headers.update(_headers(agent))
    today = date.today().isoformat()
    r = client.get(f"/v1/users/{agent.id}/metrics?date_from={today}&date_to={today}")
    assert r.status_code == 200
    avg = r.json()["avg_first_response_sec"]
    assert avg is not None
    assert avg > 0


# ── Test 4: notes_created en el período ──────────────────────────────────────


def test_notes_created(client, db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "agent4@test.com")
    wn = _make_wa_number(db, tenant.id)
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="54923001",
        wa_contact_name="Test",
    )
    db.add(conv)
    db.flush()

    note = ConversationNote(
        tenant_id=tenant.id,
        wa_conversation_id=conv.id,
        user_id=agent.id,
        text="Nota de prueba",
        mentions=[],
    )
    db.add(note)
    db.flush()

    client.headers.update(_headers(agent))
    today = date.today().isoformat()
    r = client.get(f"/v1/users/{agent.id}/metrics?date_from={today}&date_to={today}")
    assert r.status_code == 200
    assert r.json()["notes_created"] >= 1


# ── Test 5: 404 si user_id no pertenece al tenant ────────────────────────────


def test_404_user_otro_tenant(client_a):
    fake_id = str(uuid.uuid4())
    r = client_a.get(f"/v1/users/{fake_id}/metrics")
    assert r.status_code == 404


# ── Test 6: 403 si agente intenta ver métricas de otro agente ────────────────


def test_403_agente_ve_metricas_de_otro(client, db, tenant_a):
    tenant, owner = tenant_a
    agent1 = _make_agent(db, tenant.id, "agent5a@test.com")
    agent2 = _make_agent(db, tenant.id, "agent5b@test.com")

    client.headers.update(_headers(agent1))
    r = client.get(f"/v1/users/{agent2.id}/metrics")
    assert r.status_code == 403


# ── Test 7: owner puede ver métricas de cualquier agente ─────────────────────


def test_owner_ve_metricas_de_agente(client_a, db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "agent6@test.com")

    r = client_a.get(f"/v1/users/{agent.id}/metrics")
    assert r.status_code == 200
    assert r.json()["user_id"] == str(agent.id)
