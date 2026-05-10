"""Tests Fase 25B: Etiquetas en conversaciones.

Cubre:
- POST /v1/inbox/{conv_id}/tags → 201 tag nueva, 200 si ya existe (idempotente).
- DELETE /v1/inbox/{conv_id}/tags/{tag} → 204.
- GET /v1/inbox?tag=xxx filtra por etiqueta.
- GET /v1/inbox/{conv_id} incluye campo tags en la respuesta.
- Aislamiento multi-tenant.
"""

from __future__ import annotations

import uuid

import pytest

from src.auth.tokens import create_access_token
from src.db.models import Tenant, User
from src.wa.models import WaConversation, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant: Tenant) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant.id,
        label="Test",
        waha_session_name=f"sess-{uuid.uuid4().hex[:8]}",
        phone="5491100000002",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conv(db, tenant: Tenant, wn: WaNumber) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone=f"549110{uuid.uuid4().int % 10000000:07d}",
        status="agent",
    )
    db.add(conv)
    db.flush()
    return conv


# ── Tests POST /tags ──────────────────────────────────────────────────────────


def test_add_tag_crea_tag(client_a, db, tenant_a):
    """POST /v1/inbox/{conv_id}/tags crea la tag y devuelve 201."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    r = client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "urgente"})
    assert r.status_code == 201
    data = r.json()
    assert data["tag"] == "urgente"


def test_add_tag_idempotente(client_a, db, tenant_a):
    """POST con la misma tag devuelve 200 (no duplica)."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "vip"})
    r = client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "vip"})
    assert r.status_code == 200

    # Verificar que sólo hay una tag "vip".
    r2 = client_a.get(f"/v1/inbox/{conv.id}")
    assert r2.status_code == 200
    assert r2.json()["conversation"]["tags"].count("vip") == 1


def test_add_tag_normaliza_a_minusculas(client_a, db, tenant_a):
    """Las tags se normalizan a minúsculas."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    r = client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "Urgente"})
    assert r.status_code == 201
    assert r.json()["tag"] == "urgente"


def test_add_tag_vacia_devuelve_422(client_a, db, tenant_a):
    """Tag vacía devuelve 422."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    r = client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": ""})
    assert r.status_code == 422


# ── Tests DELETE /tags/{tag} ──────────────────────────────────────────────────


def test_delete_tag_existente(client_a, db, tenant_a):
    """DELETE tag existente devuelve 204."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "eliminar"})
    r = client_a.delete(f"/v1/inbox/{conv.id}/tags/eliminar")
    assert r.status_code == 204

    r2 = client_a.get(f"/v1/inbox/{conv.id}")
    assert "eliminar" not in r2.json()["conversation"]["tags"]


def test_delete_tag_inexistente_204(client_a, db, tenant_a):
    """DELETE tag que no existe también devuelve 204 (idempotente)."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    r = client_a.delete(f"/v1/inbox/{conv.id}/tags/inexistente")
    assert r.status_code == 204


# ── Tests GET /inbox con filtro tag ──────────────────────────────────────────


def test_list_inbox_filtra_por_tag(client_a, db, tenant_a):
    """GET /v1/inbox?tag=urgente devuelve solo convs con esa tag."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)

    conv1 = _make_conv(db, tenant, wn)
    conv2 = _make_conv(db, tenant, wn)
    conv3 = _make_conv(db, tenant, wn)

    client_a.post(f"/v1/inbox/{conv1.id}/tags", json={"tag": "urgente"})
    client_a.post(f"/v1/inbox/{conv2.id}/tags", json={"tag": "urgente"})
    # conv3 sin tag

    r = client_a.get("/v1/inbox?tag=urgente")
    assert r.status_code == 200
    data = r.json()
    ids = [item["id"] for item in data["items"]]
    assert str(conv1.id) in ids
    assert str(conv2.id) in ids
    assert str(conv3.id) not in ids


def test_list_inbox_sin_filtro_incluye_todas(client_a, db, tenant_a):
    """Sin filtro tag, el listado incluye todas las conversaciones."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv1 = _make_conv(db, tenant, wn)
    conv2 = _make_conv(db, tenant, wn)

    client_a.post(f"/v1/inbox/{conv1.id}/tags", json={"tag": "solo-conv1"})

    r = client_a.get("/v1/inbox")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert str(conv1.id) in ids
    assert str(conv2.id) in ids


# ── Tests GET /inbox/{conv_id} incluye tags ──────────────────────────────────


def test_get_conversation_incluye_tags(client_a, db, tenant_a):
    """GET detalle conversación incluye campo tags con las etiquetas activas."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "vip"})
    client_a.post(f"/v1/inbox/{conv.id}/tags", json={"tag": "soporte"})

    r = client_a.get(f"/v1/inbox/{conv.id}")
    assert r.status_code == 200
    tags = r.json()["conversation"]["tags"]
    assert "vip" in tags
    assert "soporte" in tags


def test_get_conversation_tags_vacias(client_a, db, tenant_a):
    """Sin etiquetas, el campo tags es lista vacía."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    r = client_a.get(f"/v1/inbox/{conv.id}")
    assert r.status_code == 200
    assert r.json()["conversation"]["tags"] == []


# ── Tests aislamiento ────────────────────────────────────────────────────────


def test_tags_aislamiento_tenant(db, tenant_a, tenant_b):
    """Tenant B no puede añadir tags a conversaciones del Tenant A."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    wn_a = _make_wa_number(db, tenant_a_obj)
    conv_a = _make_conv(db, tenant_a_obj, wn_a)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    token_b = create_access_token(owner_b.id, owner_b.tenant_id, owner_b.role)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token_b}"})
        r = c.post(f"/v1/inbox/{conv_a.id}/tags", json={"tag": "hack"})
        assert r.status_code == 404
    app.dependency_overrides.clear()
