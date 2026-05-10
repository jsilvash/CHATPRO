"""Tests Fase 25C: Templates de respuesta rápida (canned responses).

Cubre:
- POST /v1/canned-responses → 201
- GET /v1/canned-responses → lista paginada
- GET /v1/canned-responses/{id} → 200 | 404
- PATCH /v1/canned-responses/{id} → 200 | 404
- DELETE /v1/canned-responses/{id} → 204 | 404
- Conflicto en shortcode duplicado → 409.
- Aislamiento multi-tenant.
"""

from __future__ import annotations

import uuid

import pytest

from src.auth.tokens import create_access_token


# ── Tests POST ────────────────────────────────────────────────────────────────


def test_crear_canned_response(client_a):
    """POST crea el template y devuelve 201."""
    r = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "saludo", "text": "¡Hola! ¿En qué puedo ayudarte?"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["shortcode"] == "saludo"
    assert data["text"] == "¡Hola! ¿En qué puedo ayudarte?"
    assert "id" in data


def test_crear_canned_shortcode_duplicado_409(client_a):
    """Shortcode duplicado dentro del tenant devuelve 409."""
    client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "dup", "text": "Texto original"},
    )
    r = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "dup", "text": "Otro texto"},
    )
    assert r.status_code == 409


def test_crear_canned_shortcode_vacio_422(client_a):
    """Shortcode vacío devuelve 422."""
    r = client_a.post("/v1/canned-responses", json={"shortcode": "", "text": "Hola"})
    assert r.status_code == 422


def test_crear_canned_text_vacio_422(client_a):
    """Text vacío devuelve 422."""
    r = client_a.post("/v1/canned-responses", json={"shortcode": "codigo", "text": ""})
    assert r.status_code == 422


# ── Tests GET list ────────────────────────────────────────────────────────────


def test_listar_canned_responses(client_a):
    """GET devuelve lista con paginación."""
    client_a.post("/v1/canned-responses", json={"shortcode": "aa", "text": "Texto AA"})
    client_a.post("/v1/canned-responses", json={"shortcode": "bb", "text": "Texto BB"})

    r = client_a.get("/v1/canned-responses")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    assert "items" in data
    assert "page" in data
    assert "page_size" in data


def test_listar_paginado(client_a):
    """Paginación con page_size=1."""
    for i in range(3):
        client_a.post(
            "/v1/canned-responses",
            json={"shortcode": f"pag{i}", "text": f"Texto {i}"},
        )

    r = client_a.get("/v1/canned-responses?page=1&page_size=1")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    assert data["page"] == 1
    assert data["page_size"] == 1


# ── Tests GET by ID ──────────────────────────────────────────────────────────


def test_get_canned_response_by_id(client_a):
    """GET /{id} devuelve el template."""
    r_create = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "detalle", "text": "Texto de detalle"},
    )
    canned_id = r_create.json()["id"]

    r = client_a.get(f"/v1/canned-responses/{canned_id}")
    assert r.status_code == 200
    assert r.json()["shortcode"] == "detalle"


def test_get_canned_response_404(client_a):
    """GET con ID inexistente devuelve 404."""
    r = client_a.get(f"/v1/canned-responses/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Tests PATCH ──────────────────────────────────────────────────────────────


def test_patch_canned_shortcode(client_a):
    """PATCH actualiza el shortcode."""
    r_create = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "viejo", "text": "Texto"},
    )
    canned_id = r_create.json()["id"]

    r = client_a.patch(
        f"/v1/canned-responses/{canned_id}",
        json={"shortcode": "nuevo"},
    )
    assert r.status_code == 200
    assert r.json()["shortcode"] == "nuevo"


def test_patch_canned_text(client_a):
    """PATCH actualiza el text."""
    r_create = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "patchtext", "text": "Texto original"},
    )
    canned_id = r_create.json()["id"]

    r = client_a.patch(
        f"/v1/canned-responses/{canned_id}",
        json={"text": "Texto actualizado"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Texto actualizado"


def test_patch_canned_shortcode_duplicado_409(client_a):
    """PATCH con shortcode que ya existe en otro template → 409."""
    client_a.post("/v1/canned-responses", json={"shortcode": "existe", "text": "A"})
    r_b = client_a.post("/v1/canned-responses", json={"shortcode": "pararenombrar", "text": "B"})
    id_b = r_b.json()["id"]

    r = client_a.patch(f"/v1/canned-responses/{id_b}", json={"shortcode": "existe"})
    assert r.status_code == 409


def test_patch_canned_404(client_a):
    """PATCH con ID inexistente devuelve 404."""
    r = client_a.patch(f"/v1/canned-responses/{uuid.uuid4()}", json={"text": "X"})
    assert r.status_code == 404


# ── Tests DELETE ─────────────────────────────────────────────────────────────


def test_delete_canned_response(client_a):
    """DELETE elimina el template, 204."""
    r_create = client_a.post(
        "/v1/canned-responses",
        json={"shortcode": "eliminar", "text": "Texto"},
    )
    canned_id = r_create.json()["id"]

    r = client_a.delete(f"/v1/canned-responses/{canned_id}")
    assert r.status_code == 204

    r2 = client_a.get(f"/v1/canned-responses/{canned_id}")
    assert r2.status_code == 404


def test_delete_canned_404(client_a):
    """DELETE con ID inexistente devuelve 404."""
    r = client_a.delete(f"/v1/canned-responses/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Tests aislamiento ────────────────────────────────────────────────────────


def test_canned_aislamiento_tenant(db, tenant_a, tenant_b):
    """Tenant B no puede ver ni modificar templates de Tenant A."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    def override_get_db():
        try:
            yield db
        finally:
            pass

    # Crear template como tenant A.
    token_a = create_access_token(owner_a.id, owner_a.tenant_id, owner_a.role)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token_a}"})
        r_create = c.post(
            "/v1/canned-responses",
            json={"shortcode": "privado-a", "text": "Solo tenant A"},
        )
        assert r_create.status_code == 201
        canned_id = r_create.json()["id"]

    # Intentar acceder como tenant B.
    token_b = create_access_token(owner_b.id, owner_b.tenant_id, owner_b.role)
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token_b}"})
        r_get = c.get(f"/v1/canned-responses/{canned_id}")
        assert r_get.status_code == 404

        r_del = c.delete(f"/v1/canned-responses/{canned_id}")
        assert r_del.status_code == 404

    app.dependency_overrides.clear()


def test_shortcode_duplicado_solo_dentro_del_tenant(db, tenant_a, tenant_b):
    """El mismo shortcode puede existir en tenants distintos sin conflicto."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    token_a = create_access_token(owner_a.id, owner_a.tenant_id, owner_a.role)
    token_b = create_access_token(owner_b.id, owner_b.tenant_id, owner_b.role)

    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token_a}"})
        r_a = c.post("/v1/canned-responses", json={"shortcode": "comun", "text": "Tenant A"})
        assert r_a.status_code == 201

    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token_b}"})
        r_b = c.post("/v1/canned-responses", json={"shortcode": "comun", "text": "Tenant B"})
        assert r_b.status_code == 201

    app.dependency_overrides.clear()
