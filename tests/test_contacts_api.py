"""Tests de la API REST de contactos y hechos (Fase 4)."""

import uuid

import pytest

from src.contacts.models import Contact, ContactFact
from src.tenancy.context import tenant_scope


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _create_contact(db, tenant_id, phone="56900000001"):
    contact = Contact(
        tenant_id=tenant_id,
        phone_e164=phone,
        display_name="Contacto Test",
    )
    db.add(contact)
    db.flush()
    return contact


# ────────────────────────────────────────────────────────────
# GET /v1/contacts
# ────────────────────────────────────────────────────────────


def test_list_contacts_empty(client_a):
    resp = client_a.get("/v1/contacts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_contacts_returns_own_tenant(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        _create_contact(db, tenant.id, "56900000002")

    resp = client_a.get("/v1/contacts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["phone_e164"] == "56900000002"


def test_list_contacts_isolation(client_a, client_b, db, tenant_a):
    """Tenant A no puede ver contactos de Tenant B (client_b usa su propio tenant)."""
    tenant_a_obj, _ = tenant_a

    with tenant_scope(tenant_a_obj.id):
        _create_contact(db, tenant_a_obj.id, "56900000010")

    # client_a ve su propio contacto
    resp_a = client_a.get("/v1/contacts")
    phones_a = [c["phone_e164"] for c in resp_a.json()]
    assert "56900000010" in phones_a

    # client_b (tenant distinto) NO ve el contacto de tenant_a
    resp_b = client_b.get("/v1/contacts")
    phones_b = [c["phone_e164"] for c in resp_b.json()]
    assert "56900000010" not in phones_b


# ────────────────────────────────────────────────────────────
# GET /v1/contacts/{id}
# ────────────────────────────────────────────────────────────


def test_get_contact_found(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000020")

    resp = client_a.get(f"/v1/contacts/{contact.id}")
    assert resp.status_code == 200
    assert resp.json()["phone_e164"] == "56900000020"


def test_get_contact_cross_tenant_404(client_a, db, tenant_b):
    tenant_b_obj, _ = tenant_b
    with tenant_scope(tenant_b_obj.id):
        contact = _create_contact(db, tenant_b_obj.id, "56900000021")

    resp = client_a.get(f"/v1/contacts/{contact.id}")
    assert resp.status_code == 404


def test_get_contact_not_found(client_a):
    resp = client_a.get(f"/v1/contacts/{uuid.uuid4()}")
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────
# PATCH /v1/contacts/{id}
# ────────────────────────────────────────────────────────────


def test_patch_contact_updates_fields(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000030")

    resp = client_a.patch(
        f"/v1/contacts/{contact.id}",
        json={"display_name": "Nuevo Nombre", "email": "nuevo@test.cl"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Nuevo Nombre"
    assert data["email"] == "nuevo@test.cl"


def test_patch_contact_partial_update(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000031")

    resp = client_a.patch(
        f"/v1/contacts/{contact.id}",
        json={"opt_in_marketing": True},
    )
    assert resp.status_code == 200
    assert resp.json()["opt_in_marketing"] is True


def test_patch_contact_cross_tenant_404(client_a, db, tenant_b):
    tenant_b_obj, _ = tenant_b
    with tenant_scope(tenant_b_obj.id):
        contact = _create_contact(db, tenant_b_obj.id, "56900000032")

    resp = client_a.patch(
        f"/v1/contacts/{contact.id}",
        json={"display_name": "Hack"},
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────
# GET /v1/contacts/{id}/facts
# ────────────────────────────────────────────────────────────


def test_list_facts_empty(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000040")

    resp = client_a.get(f"/v1/contacts/{contact.id}/facts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_facts_returns_all(client_a, db, tenant_a):
    from decimal import Decimal
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000041")
        db.add(ContactFact(
            tenant_id=tenant.id,
            contact_id=contact.id,
            key="dieta",
            value_text="vegano",
            value_type="text",
            source="extracted",
            confidence=Decimal("0.90"),
        ))
        db.flush()

    resp = client_a.get(f"/v1/contacts/{contact.id}/facts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["key"] == "dieta"
    assert data[0]["source"] == "extracted"


# ────────────────────────────────────────────────────────────
# PUT /v1/contacts/{id}/facts/{key}
# ────────────────────────────────────────────────────────────


def test_put_fact_creates_new(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000050")

    resp = client_a.put(
        f"/v1/contacts/{contact.id}/facts/talla",
        json={"value_text": "M", "value_type": "text"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "talla"
    assert data["value_text"] == "M"
    assert data["source"] == "manual"
    assert data["confidence"] is None


def test_put_fact_overwrites_existing(client_a, db, tenant_a):
    from decimal import Decimal
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000051")
        db.add(ContactFact(
            tenant_id=tenant.id,
            contact_id=contact.id,
            key="talla",
            value_text="S",
            value_type="text",
            source="extracted",
            confidence=Decimal("0.80"),
        ))
        db.flush()

    resp = client_a.put(
        f"/v1/contacts/{contact.id}/facts/talla",
        json={"value_text": "L", "value_type": "text"},
    )
    assert resp.status_code == 200
    assert resp.json()["value_text"] == "L"
    assert resp.json()["source"] == "manual"


def test_put_fact_key_normalized_lowercase(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000052")

    resp = client_a.put(
        f"/v1/contacts/{contact.id}/facts/TALLA_PREFERIDA",
        json={"value_text": "XL"},
    )
    assert resp.status_code == 200
    assert resp.json()["key"] == "talla_preferida"


def test_put_fact_cross_tenant_404(client_a, db, tenant_b):
    tenant_b_obj, _ = tenant_b
    with tenant_scope(tenant_b_obj.id):
        contact = _create_contact(db, tenant_b_obj.id, "56900000053")

    resp = client_a.put(
        f"/v1/contacts/{contact.id}/facts/hack",
        json={"value_text": "valor"},
    )
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────
# DELETE /v1/contacts/{id}/facts/{key}
# ────────────────────────────────────────────────────────────


def test_delete_fact(client_a, db, tenant_a):
    from decimal import Decimal
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000060")
        db.add(ContactFact(
            tenant_id=tenant.id,
            contact_id=contact.id,
            key="dieta",
            value_text="vegano",
            value_type="text",
            source="manual",
        ))
        db.flush()

    resp = client_a.delete(f"/v1/contacts/{contact.id}/facts/dieta")
    assert resp.status_code == 204

    resp2 = client_a.get(f"/v1/contacts/{contact.id}/facts")
    assert resp2.json() == []


def test_delete_fact_not_found(client_a, db, tenant_a):
    tenant, _ = tenant_a
    with tenant_scope(tenant.id):
        contact = _create_contact(db, tenant.id, "56900000061")

    resp = client_a.delete(f"/v1/contacts/{contact.id}/facts/no_existe")
    assert resp.status_code == 404
