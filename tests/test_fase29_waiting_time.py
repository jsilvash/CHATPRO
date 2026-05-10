"""Tests Fase 29B — Waiting time en conversaciones en espera.

Cubre:
1. waiting_since se setea al escalar a waiting_agent
2. waiting_since se limpia al tomar la conversación (status → agent)
3. waiting_minutes calculado en ConversationSummary
4. GET /v1/inbox?sort=waiting_time ordena por waiting_since ASC
5. GET /v1/inbox/overdue devuelve convs que superan el umbral
6. Aislamiento multi-tenant en /overdue
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import User
from src.inbox.models import HandoffEvent
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


def _make_conversation(db, tenant_id, wa_number_id, phone="549110001", status="bot") -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number_id,
        wa_contact_phone=phone,
        wa_contact_name="Test",
        status=status,
    )
    db.add(conv)
    db.flush()
    return conv


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


# ── Test 1: waiting_since se setea al escalar ─────────────────────────────────


def test_waiting_since_se_setea_en_waiting_agent(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54911111", status="waiting_agent")

    # Setear waiting_since manualmente (como lo haría _auto_escalate_if_needed)
    now = datetime.now(UTC)
    conv.waiting_since = now
    db.flush()

    r = client_a.get(f"/v1/inbox/{conv.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["conversation"]["waiting_since"] is not None
    assert data["conversation"]["waiting_minutes"] is not None
    assert data["conversation"]["waiting_minutes"] >= 0


# ── Test 2: waiting_since se limpia al tomar (status → agent) ────────────────


def test_waiting_since_se_limpia_en_agent(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54912222", status="waiting_agent")
    conv.waiting_since = datetime.now(UTC) - timedelta(minutes=15)
    db.flush()

    r = client_a.post(f"/v1/inbox/{conv.id}/take")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "agent"
    assert data["waiting_since"] is None
    assert data["waiting_minutes"] is None


# ── Test 3: waiting_minutes calculado correctamente ───────────────────────────


def test_waiting_minutes_calculado(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54913333", status="waiting_agent")
    conv.waiting_since = datetime.now(UTC) - timedelta(minutes=45)
    db.flush()

    r = client_a.get(f"/v1/inbox/{conv.id}")
    assert r.status_code == 200
    wm = r.json()["conversation"]["waiting_minutes"]
    assert wm is not None
    assert 44 <= wm <= 46  # tolerancia de 1 minuto


# ── Test 4: sort=waiting_time ordena por waiting_since ASC ───────────────────


def test_sort_waiting_time(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)

    conv_later = _make_conversation(db, tenant.id, wn.id, phone="54921111", status="waiting_agent")
    conv_later.waiting_since = now - timedelta(minutes=5)
    db.flush()

    conv_earlier = _make_conversation(db, tenant.id, wn.id, phone="54922222", status="waiting_agent")
    conv_earlier.waiting_since = now - timedelta(minutes=30)
    db.flush()

    r = client_a.get("/v1/inbox?status=waiting_agent&sort=waiting_time")
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [item["id"] for item in items]
    assert ids.index(str(conv_earlier.id)) < ids.index(str(conv_later.id))


# ── Test 5: GET /v1/inbox/overdue devuelve convs vencidas ────────────────────


def test_overdue_devuelve_convs_vencidas(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)

    # Conv vencida: waiting_since hace 60 minutos.
    conv_overdue = _make_conversation(db, tenant.id, wn.id, phone="54931111", status="waiting_agent")
    conv_overdue.waiting_since = now - timedelta(minutes=60)
    db.flush()

    # Conv reciente: waiting_since hace 5 minutos (no vencida con threshold=30).
    conv_fresh = _make_conversation(db, tenant.id, wn.id, phone="54932222", status="waiting_agent")
    conv_fresh.waiting_since = now - timedelta(minutes=5)
    db.flush()

    r = client_a.get("/v1/inbox/overdue?threshold_minutes=30")
    assert r.status_code == 200
    data = r.json()
    ids = [item["id"] for item in data["items"]]
    assert str(conv_overdue.id) in ids
    assert str(conv_fresh.id) not in ids


# ── Test 6: /overdue no devuelve convs en status != waiting_agent ─────────────


def test_overdue_solo_waiting_agent(db, tenant_a, client_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)

    conv_bot = _make_conversation(db, tenant.id, wn.id, phone="54941111", status="bot")
    # aunque tuviera waiting_since, no es waiting_agent
    conv_bot.waiting_since = now - timedelta(hours=2)
    db.flush()

    r = client_a.get("/v1/inbox/overdue?threshold_minutes=30")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert str(conv_bot.id) not in ids


# ── Test 7: aislamiento multi-tenant en /overdue ─────────────────────────────


def test_overdue_aislamiento_tenant(db, tenant_a, client_a, client_b):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    now = datetime.now(UTC)
    conv_a = _make_conversation(db, tenant.id, wn.id, phone="54951111", status="waiting_agent")
    conv_a.waiting_since = now - timedelta(hours=2)
    db.flush()

    r_b = client_b.get("/v1/inbox/overdue?threshold_minutes=30")
    assert r_b.status_code == 200
    ids_b = [item["id"] for item in r_b.json()["items"]]
    assert str(conv_a.id) not in ids_b
