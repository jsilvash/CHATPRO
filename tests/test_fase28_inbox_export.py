"""Tests Fase 28-C: GET /v1/inbox/export — exportación CSV de conversaciones.

Cubre:
- Respuesta tiene Content-Type text/csv.
- Filas corresponden a las convs del tenant.
- Filtros funcionan en export (assigned_user_id, search).
- Aislamiento tenant: otro tenant no ve las convs.
- Columnas correctas en el CSV.
- Export vacío devuelve solo cabecera (no error).
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

from src.auth.passwords import hash_password
from src.db.models import User
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner

ENDPOINT = "/v1/inbox/export"

EXPECTED_COLUMNS = [
    "id", "wa_contact_name", "wa_contact_phone", "status",
    "assigned_user_id", "created_at", "last_message_at",
    "resolved_at", "tags", "notes_count",
]


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
    wa_contact_name: str = "Test Contacto",
    wa_contact_phone: str | None = None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=wa_contact_phone or f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name=wa_contact_name,
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
        full_name="Agente Export",
        role="agent",
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


def _parse_csv(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestInboxExport:
    def test_content_type_es_text_csv(self, client, db, tenant_a):
        """La respuesta tiene Content-Type text/csv."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_filas_corresponden_a_convs_del_tenant(self, client, db, tenant_a):
        """El CSV contiene exactamente las convs del tenant."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        c1 = _make_conversation(db, tenant.id, wn, status="bot")
        c2 = _make_conversation(db, tenant.id, wn, status="agent")
        c3 = _make_conversation(db, tenant.id, wn, status="waiting_agent")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        rows = _parse_csv(resp.text)
        assert len(rows) == 3
        ids_in_csv = {r["id"] for r in rows}
        assert str(c1.id) in ids_in_csv
        assert str(c2.id) in ids_in_csv
        assert str(c3.id) in ids_in_csv

    def test_filtro_assigned_user_id(self, client, db, tenant_a):
        """El filtro assigned_user_id funciona en export."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        agent = _make_agent(db, tenant.id)

        conv_asignada = _make_conversation(db, tenant.id, wn, status="agent", assigned_user_id=agent.id)
        conv_no_asignada = _make_conversation(db, tenant.id, wn, status="bot")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"assigned_user_id": str(agent.id)})
        assert resp.status_code == 200

        rows = _parse_csv(resp.text)
        assert len(rows) == 1
        assert rows[0]["id"] == str(conv_asignada.id)

    def test_filtro_search_funciona(self, client, db, tenant_a):
        """El filtro search (ILIKE en nombre/teléfono) funciona en export."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)

        _make_conversation(db, tenant.id, wn, wa_contact_name="Juan Pérez")
        _make_conversation(db, tenant.id, wn, wa_contact_name="María García")
        _make_conversation(db, tenant.id, wn, wa_contact_name="Pedro López")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"search": "juan"})
        assert resp.status_code == 200

        rows = _parse_csv(resp.text)
        assert len(rows) == 1
        assert rows[0]["wa_contact_name"] == "Juan Pérez"

    def test_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        """Otro tenant no ve las convs del tenant A."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, owner_b = tenant_b

        wn_a = _make_wa_number(db, tenant_a_obj.id)
        wn_b = _make_wa_number(db, tenant_b_obj.id)

        for _ in range(3):
            _make_conversation(db, tenant_a_obj.id, wn_a)
        for _ in range(5):
            _make_conversation(db, tenant_b_obj.id, wn_b)

        client.headers.update(_auth_header(owner_a))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        rows = _parse_csv(resp.text)
        assert len(rows) == 3

    def test_columnas_correctas(self, client, db, tenant_a):
        """El CSV contiene exactamente las columnas requeridas."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        reader = csv.DictReader(io.StringIO(resp.text))
        assert reader.fieldnames is not None
        for col in EXPECTED_COLUMNS:
            assert col in reader.fieldnames, f"Columna '{col}' faltante"

    def test_export_vacio_devuelve_solo_cabecera(self, client, db, tenant_a):
        """Tenant sin convs devuelve CSV solo con cabecera, sin error."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200

        rows = _parse_csv(resp.text)
        assert len(rows) == 0

        # La cabecera sí existe
        reader = csv.DictReader(io.StringIO(resp.text))
        assert reader.fieldnames is not None
        assert len(reader.fieldnames) == len(EXPECTED_COLUMNS)
