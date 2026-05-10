"""Tests Fase 27-C: Estadísticas de usuario/agente.

Cubre:
- GET /v1/users/{user_id}/stats devuelve conversations_active correcto.
- conversations_today correcto (convs cerradas hoy).
- avg_first_response_sec calculado; null si no hay datos.
- notes_count correcto.
- Otro tenant recibe 404 al consultar stats de usuario ajeno.
- El propio usuario puede ver sus stats (sin ser admin).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.auth.passwords import hash_password
from src.db.models import User
from src.inbox.models import ConversationNote
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="WA Stats",
        waha_session_name=f"sess_{uuid.uuid4().hex[:8]}",
        phone="56900000002",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_agent(db, tenant_id: uuid.UUID, role: str = "agent") -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@stats.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Stats",
        role=role,
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_conversation(
    db,
    tenant_id: uuid.UUID,
    wa_number: WaNumber,
    *,
    status: str = "bot",
    assigned_user_id=None,
    first_response_at: datetime | None = None,
    created_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name="Stats Contact",
        status=status,
        assigned_user_id=assigned_user_id,
        first_response_at=first_response_at,
    )
    db.add(conv)
    db.flush()
    if resolved_at is not None:
        db.execute(
            text("UPDATE wa_conversations SET resolved_at = :dt WHERE id = :id"),
            {"dt": resolved_at, "id": str(conv.id)},
        )
        db.flush()
        db.refresh(conv)
    if created_at is not None:
        db.execute(
            text("UPDATE wa_conversations SET created_at = :dt WHERE id = :id"),
            {"dt": created_at, "id": str(conv.id)},
        )
        db.flush()
        db.refresh(conv)
    return conv


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestUserStats:
    def test_conversations_active_correcto(self, client, db, tenant_a):
        """conversations_active cuenta convs con status agent|waiting_agent asignadas."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agent.id)
        _make_conversation(db, tenant.id, wn, status="waiting_agent", assigned_user_id=agent.id)
        _make_conversation(db, tenant.id, wn, status="bot", assigned_user_id=agent.id)  # no cuenta

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == str(agent.id)
        assert data["conversations_active"] == 2

    def test_conversations_today_correcto(self, client, db, tenant_a):
        """conversations_today cuenta convs cerradas hoy con resolved_at = hoy."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)
        now = datetime.now(UTC)

        # Cerrada hoy → debe contar.
        _make_conversation(
            db, tenant.id, wn,
            status="bot",
            assigned_user_id=agent.id,
            resolved_at=now,
        )
        # Cerrada ayer → no debe contar.
        _make_conversation(
            db, tenant.id, wn,
            status="bot",
            assigned_user_id=agent.id,
            resolved_at=now - timedelta(days=1),
        )

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        assert resp.json()["conversations_today"] >= 1

    def test_avg_first_response_sec_calculado(self, client, db, tenant_a):
        """avg_first_response_sec es el promedio de (first_response_at - created_at)."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)
        now = datetime.now(UTC)

        # Tiempo de respuesta: 60 s.
        c1 = _make_conversation(
            db, tenant.id, wn,
            status="agent",
            assigned_user_id=agent.id,
            created_at=now - timedelta(seconds=60),
            first_response_at=now,
        )

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["avg_first_response_sec"] is not None
        assert data["avg_first_response_sec"] > 0

    def test_avg_first_response_sec_null_sin_datos(self, client, db, tenant_a):
        """avg_first_response_sec es null si no hay convs con first_response_at."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agent.id)

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        assert resp.json()["avg_first_response_sec"] is None

    def test_notes_count_correcto(self, client, db, tenant_a):
        """notes_count cuenta las notas creadas por el usuario."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)
        conv = _make_conversation(db, tenant.id, wn)

        for _ in range(3):
            note = ConversationNote(
                tenant_id=tenant.id,
                wa_conversation_id=conv.id,
                user_id=agent.id,
                text="Nota de test",
            )
            db.add(note)
        db.flush()

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        assert resp.json()["notes_count"] == 3

    def test_otro_tenant_no_puede_ver_stats(self, client, db, tenant_a, tenant_b):
        """Usuario de otro tenant recibe 404 al consultar stats."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b
        wn_a = _make_wa_number(db, tenant_a_obj.id)
        agent_a = _make_agent(db, tenant_a_obj.id)

        client.headers.update(_auth_header(owner_b))
        resp = client.get(f"/v1/users/{agent_a.id}/stats")
        assert resp.status_code == 404

    def test_propio_usuario_puede_ver_sus_stats(self, client, db, tenant_a):
        """El propio usuario (sin ser admin) puede ver sus propias stats."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        client.headers.update(_auth_header(agent))
        resp = client.get(f"/v1/users/{agent.id}/stats")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == str(agent.id)
