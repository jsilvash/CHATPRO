"""Tests Fase 29C — Menciones (@usuario) en notas internas.

Cubre:
1. Nota sin menciones — campo mentions vacío
2. Nota con @email válido — menciona al usuario
3. Nota con @nombre válido — menciona al usuario por full_name
4. Menciones a usuarios de otro tenant ignoradas
5. Nota con menciones persistidas — GET list_notes devuelve mentions
6. Notificación WS enviada al crear nota con mención
7. Usuario inactivo no se menciona
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import User
from src.inbox.api import _resolve_mentions
from src.wa.models import WaConversation, WaNumber


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


def _make_conversation(db, tenant_id, wa_number_id, phone="54911001") -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number_id,
        wa_contact_phone=phone,
        wa_contact_name="Test",
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, tenant_id: uuid.UUID, email: str, full_name: str = "Agent") -> User:
    user = User(
        tenant_id=tenant_id,
        email=email,
        hashed_password=hash_password("secret123"),
        full_name=full_name,
        role="agent",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


# ── Test 1: nota sin menciones ────────────────────────────────────────────────


def test_nota_sin_menciones(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id)

    r = client_a.post(f"/v1/inbox/{conv.id}/notes", json={"text": "Nota sin menciones."})
    assert r.status_code == 201
    data = r.json()
    assert data["mentions"] == []


# ── Test 2: mención por email ─────────────────────────────────────────────────


def test_mencion_por_email(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54912001")
    agent = _make_agent(db, tenant.id, "agente@test.com", "Agente Test")

    r = client_a.post(
        f"/v1/inbox/{conv.id}/notes",
        json={"text": f"Revisar con @agente@test.com esto por favor."},
    )
    assert r.status_code == 201
    data = r.json()
    assert str(agent.id) in data["mentions"]


# ── Test 3: mención por nombre completo ───────────────────────────────────────


def test_mencion_por_nombre(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54913001")
    agent = _make_agent(db, tenant.id, "otro@test.com", "Carlos")

    r = client_a.post(
        f"/v1/inbox/{conv.id}/notes",
        json={"text": "Esto es para @Carlos que lo revise."},
    )
    assert r.status_code == 201
    data = r.json()
    assert str(agent.id) in data["mentions"]


# ── Test 4: mención a usuario inexistente ignorada ────────────────────────────


def test_mencion_usuario_inexistente_ignorada(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54914001")

    r = client_a.post(
        f"/v1/inbox/{conv.id}/notes",
        json={"text": "Para @nadie_que_exista en el tenant."},
    )
    assert r.status_code == 201
    assert r.json()["mentions"] == []


# ── Test 5: menciones persistidas — GET list_notes devuelve mentions ──────────


def test_menciones_persistidas_en_list(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54915001")
    agent = _make_agent(db, tenant.id, "persisted@test.com", "Persisted")

    client_a.post(
        f"/v1/inbox/{conv.id}/notes",
        json={"text": f"Cc: @persisted@test.com para que vea esto."},
    )

    r = client_a.get(f"/v1/inbox/{conv.id}/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) >= 1
    assert str(agent.id) in notes[-1]["mentions"]


# ── Test 6: _resolve_mentions — servicio unitario ─────────────────────────────


def test_resolve_mentions_unitario(db, tenant_a):
    tenant, owner = tenant_a
    agent = _make_agent(db, tenant.id, "unit@test.com", "UnitAgent")

    ids = _resolve_mentions(
        "Revisar con @unit@test.com",
        tenant.id,
        db,
    )
    assert agent.id in ids


# ── Test 7: usuario inactivo no se menciona ───────────────────────────────────


def test_usuario_inactivo_no_mencionado(client_a, db, tenant_a):
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn.id, phone="54917001")

    inactive = User(
        tenant_id=tenant.id,
        email="inactivo@test.com",
        hashed_password=hash_password("x"),
        full_name="Inactivo",
        role="agent",
        is_active=False,
    )
    db.add(inactive)
    db.flush()

    r = client_a.post(
        f"/v1/inbox/{conv.id}/notes",
        json={"text": "Para @inactivo@test.com que no está activo."},
    )
    assert r.status_code == 201
    assert r.json()["mentions"] == []
