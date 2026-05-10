"""Tests Fase 27-A: Filtros avanzados en listado de inbox.

Cubre:
- ?assigned_user_id=<uuid> filtra por agente asignado.
- ?date_from / ?date_to filtran por created_at.
- ?search=<texto> busca en wa_contact_name OR wa_contact_phone (ILIKE).
- total_pages calculado correctamente.
- Aislamiento multi-tenant en todos los filtros.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test WA",
        waha_session_name=f"sess_{uuid.uuid4().hex[:8]}",
        phone="56900000000",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(
    db,
    tenant_id: uuid.UUID,
    wa_number: WaNumber,
    *,
    status: str = "bot",
    assigned_user_id=None,
    wa_contact_phone: str | None = None,
    wa_contact_name: str | None = None,
    created_at: datetime | None = None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=wa_contact_phone or f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name=wa_contact_name or "Contacto Test",
        status=status,
        assigned_user_id=assigned_user_id,
    )
    db.add(conv)
    db.flush()
    if created_at is not None:
        # Sobreescribir created_at tras el flush (server_default lo setea en flush).
        db.execute(
            __import__("sqlalchemy").text("UPDATE wa_conversations SET created_at = :dt WHERE id = :id"),
            {"dt": created_at, "id": str(conv.id)},
        )
        db.flush()
        db.refresh(conv)
    return conv


def _make_agent(db, tenant_id: uuid.UUID) -> User:
    agent = User(
        tenant_id=tenant_id,
        email=f"agent_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password=hash_password("secret123"),
        full_name="Agente Test",
        role="agent",
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestInboxFilters:
    def test_filtro_por_assigned_user_id(self, client, db, tenant_a):
        """?assigned_user_id filtra correctamente."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        c1 = _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agent.id)
        _make_conversation(db, tenant.id, wn, status="agent")  # sin asignar

        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/inbox?assigned_user_id={agent.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == str(c1.id)
        assert data["items"][0]["assigned_user_id"] == str(agent.id)

    def test_filtro_por_date_from(self, client, db, tenant_a):
        """?date_from excluye conversaciones anteriores a la fecha."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        now = datetime.now(UTC)

        old_conv = _make_conversation(db, tenant.id, wn, created_at=now - timedelta(days=10))
        _new_conv = _make_conversation(db, tenant.id, wn, created_at=now)

        date_str = (now - timedelta(days=5)).date().isoformat()
        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/inbox?date_from={date_str}")
        assert resp.status_code == 200
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert str(old_conv.id) not in ids
        assert data["total"] >= 1

    def test_filtro_por_date_to(self, client, db, tenant_a):
        """?date_to excluye conversaciones posteriores a la fecha."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        now = datetime.now(UTC)

        old_conv = _make_conversation(db, tenant.id, wn, created_at=now - timedelta(days=10))
        new_conv = _make_conversation(db, tenant.id, wn, created_at=now)

        date_str = (now - timedelta(days=5)).date().isoformat()
        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/inbox?date_to={date_str}")
        assert resp.status_code == 200
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert str(old_conv.id) in ids
        assert str(new_conv.id) not in ids

    def test_filtro_date_from_y_date_to(self, client, db, tenant_a):
        """Combinación date_from + date_to acota el rango correctamente."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        now = datetime.now(UTC)

        _very_old = _make_conversation(db, tenant.id, wn, created_at=now - timedelta(days=20))
        in_range = _make_conversation(db, tenant.id, wn, created_at=now - timedelta(days=7))
        _very_new = _make_conversation(db, tenant.id, wn, created_at=now)

        df = (now - timedelta(days=15)).date().isoformat()
        dt = (now - timedelta(days=3)).date().isoformat()
        client.headers.update(_auth_header(owner))
        resp = client.get(f"/v1/inbox?date_from={df}&date_to={dt}")
        assert resp.status_code == 200
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert str(in_range.id) in ids

    def test_filtro_search_por_nombre(self, client, db, tenant_a):
        """?search busca por wa_contact_name (ILIKE)."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        target = _make_conversation(db, tenant.id, wn, wa_contact_name="María García Unique27")
        _make_conversation(db, tenant.id, wn, wa_contact_name="Juan Pérez")

        client.headers.update(_auth_header(owner))
        resp = client.get("/v1/inbox?search=unique27")
        assert resp.status_code == 200
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert str(target.id) in ids
        assert data["total"] >= 1

    def test_filtro_search_por_telefono(self, client, db, tenant_a):
        """?search busca por wa_contact_phone (ILIKE)."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        target = _make_conversation(db, tenant.id, wn, wa_contact_phone="56912345678")
        _make_conversation(db, tenant.id, wn, wa_contact_phone="56987654321")

        client.headers.update(_auth_header(owner))
        resp = client.get("/v1/inbox?search=12345")
        assert resp.status_code == 200
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert str(target.id) in ids

    def test_total_pages_correcto(self, client, db, tenant_a):
        """total_pages = ceil(total / page_size)."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        for _ in range(5):
            _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        resp = client.get("/v1/inbox?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        assert data["page"] == 1
        assert data["page_size"] == 2
        import math
        expected_pages = math.ceil(data["total"] / 2)
        assert data["total_pages"] == expected_pages

    def test_aislamiento_tenant_filtros(self, client, db, tenant_a, tenant_b):
        """Los filtros no cruzan datos entre tenants."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b
        wn_a = _make_wa_number(db, tenant_a_obj.id)
        wn_b = _make_wa_number(db, tenant_b_obj.id)
        agent_a = _make_agent(db, tenant_a_obj.id)

        _conv_a = _make_conversation(db, tenant_a_obj.id, wn_a, assigned_user_id=agent_a.id)
        _conv_b = _make_conversation(db, tenant_b_obj.id, wn_b, assigned_user_id=agent_a.id)

        # Tenant B no ve las convs de Tenant A aunque use el mismo agent id.
        client.headers.update(_auth_header(owner_b))
        resp = client.get(f"/v1/inbox?assigned_user_id={agent_a.id}")
        assert resp.status_code == 200
        data = resp.json()
        # Ninguna conv de A debe aparecer.
        for item in data["items"]:
            assert item["tenant_id"] == str(tenant_b_obj.id)

    def test_paginacion_segunda_pagina(self, client, db, tenant_a):
        """page=2 devuelve los items del segundo bloque."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        for _ in range(6):
            _make_conversation(db, tenant.id, wn)

        client.headers.update(_auth_header(owner))
        resp1 = client.get("/v1/inbox?page=1&page_size=3")
        resp2 = client.get("/v1/inbox?page=2&page_size=3")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        ids1 = {i["id"] for i in resp1.json()["items"]}
        ids2 = {i["id"] for i in resp2.json()["items"]}
        assert ids1.isdisjoint(ids2), "Las páginas no deben solaparse"
