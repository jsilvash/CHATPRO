"""Tests Fase 28-D: GET /v1/contacts/search — búsqueda de contactos.

Cubre:
- Búsqueda por teléfono (ILIKE).
- Búsqueda por nombre (case-insensitive).
- conversations_count correcto.
- Aislamiento tenant.
- limit respetado.
- Sin resultados devuelve lista vacía (no error).
"""

from __future__ import annotations

import uuid

import pytest

from src.contacts.models import Contact
from src.tenancy.context import bypass_tenant_filter
from src.wa.models import WaConversation, WaNumber
from tests.conftest import _auth_header, _create_tenant_and_owner

ENDPOINT = "/v1/contacts/search"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_contact(
    db,
    tenant_id: uuid.UUID,
    phone_e164: str,
    display_name: str | None = None,
) -> Contact:
    contact = Contact(
        tenant_id=tenant_id,
        phone_e164=phone_e164,
        display_name=display_name,
    )
    db.add(contact)
    db.flush()
    return contact


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
    contact: Contact | None = None,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone=f"569{uuid.uuid4().int % 100_000_000:08d}",
        wa_contact_name="Test",
        status="bot",
        contact_id=contact.id if contact else None,
    )
    db.add(conv)
    db.flush()
    return conv


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestContactSearch:
    def test_busqueda_por_telefono(self, client, db, tenant_a):
        """Busca contactos por phone_e164 con ILIKE."""
        tenant, owner = tenant_a
        _make_contact(db, tenant.id, phone_e164="56912345678", display_name="Contacto A")
        _make_contact(db, tenant.id, phone_e164="56987654321", display_name="Contacto B")
        _make_contact(db, tenant.id, phone_e164="34600000001", display_name="Contacto C")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "5691234"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        assert data[0]["phone_e164"] == "56912345678"

    def test_busqueda_por_nombre_case_insensitive(self, client, db, tenant_a):
        """Busca contactos por display_name sin importar mayúsculas."""
        tenant, owner = tenant_a
        _make_contact(db, tenant.id, phone_e164="56900000001", display_name="Juan Pérez")
        _make_contact(db, tenant.id, phone_e164="56900000002", display_name="María García")
        _make_contact(db, tenant.id, phone_e164="56900000003", display_name="Pedro López")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "JUAN"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        assert data[0]["display_name"] == "Juan Pérez"

    def test_conversations_count_correcto(self, client, db, tenant_a):
        """conversations_count refleja el número real de convs vinculadas al contacto."""
        tenant, owner = tenant_a
        wn = _make_wa_number(db, tenant.id)
        contact = _make_contact(db, tenant.id, phone_e164="56911111111", display_name="Con Convs")

        # 3 conversaciones vinculadas a este contacto
        for _ in range(3):
            _make_conversation(db, tenant.id, wn, contact=contact)

        # Contacto sin convs
        _make_contact(db, tenant.id, phone_e164="56922222222", display_name="Sin Convs")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "5691"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        assert data[0]["conversations_count"] == 3

    def test_conversations_count_cero_si_sin_convs(self, client, db, tenant_a):
        """Un contacto sin conversaciones devuelve conversations_count=0."""
        tenant, owner = tenant_a
        _make_contact(db, tenant.id, phone_e164="56933333333", display_name="Sin Convs")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "5693333"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        assert data[0]["conversations_count"] == 0

    def test_aislamiento_tenant(self, client, db, tenant_a, tenant_b):
        """Contactos de otro tenant no aparecen en los resultados."""
        tenant_a_obj, owner_a = tenant_a
        tenant_b_obj, _ = tenant_b

        # Contacto en tenant A
        _make_contact(db, tenant_a_obj.id, phone_e164="56944444444", display_name="Ana Tenant A")
        # Contacto en tenant B (mismos datos, no debe aparecer desde A)
        _make_contact(db, tenant_b_obj.id, phone_e164="56944444444", display_name="Ana Tenant B")

        client.headers.update(_auth_header(owner_a))
        resp = client.get(ENDPOINT, params={"q": "Ana"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        assert data[0]["display_name"] == "Ana Tenant A"

    def test_limit_respetado(self, client, db, tenant_a):
        """El parámetro limit restringe el número de resultados."""
        tenant, owner = tenant_a
        for i in range(10):
            _make_contact(db, tenant.id, phone_e164=f"5690000000{i}", display_name=f"Contacto {i}")

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "5690", "limit": 3})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 3

    def test_sin_resultados_devuelve_lista_vacia(self, client, db, tenant_a):
        """Búsqueda sin coincidencias devuelve lista vacía, sin error."""
        _, owner = tenant_a

        client.headers.update(_auth_header(owner))
        resp = client.get(ENDPOINT, params={"q": "nuncaexistira99999"})
        assert resp.status_code == 200
        assert resp.json() == []
