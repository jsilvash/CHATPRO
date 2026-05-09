"""Tests de aislamiento multi-tenant (Fase 0 — criterio de done).

Regla: tenant A NO puede ver datos de tenant B bajo NINGUNA ruta.
Cubrimos 5 escenarios obligatorios: list, get-by-id, mutate, search, tenant-get.
"""

import pytest
from fastapi.testclient import TestClient

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.models import Tenant, User


def _make_user(db, tenant: Tenant, email: str, role: str = "agent") -> User:
    user = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=hash_password("secret123"),
        full_name=f"Test {email}",
        role=role,
    )
    db.add(user)
    db.flush()
    return user


# ────────────────────────────────────────────────────────────
# Escenario 1 — List isolation
# ────────────────────────────────────────────────────────────


def test_list_isolation(client_a, client_b, tenant_a, db):
    """GET /v1/users de Tenant B no debe mostrar usuarios de Tenant A."""
    tenant, _owner = tenant_a
    user_a2 = _make_user(db, tenant, "a2@tenant-a.com")

    resp = client_b.get("/v1/users")
    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()["items"]]
    assert str(user_a2.id) not in ids, "Tenant B no debe ver usuarios de Tenant A"


# ────────────────────────────────────────────────────────────
# Escenario 2 — Get-by-id isolation
# ────────────────────────────────────────────────────────────


def test_get_by_id_isolation(client_b, tenant_a, db):
    """GET /v1/users/{id} de Tenant A debe retornar 404 para Tenant B."""
    tenant, _owner = tenant_a
    user_a2 = _make_user(db, tenant, "a2b@tenant-a.com")

    resp = client_b.get(f"/v1/users/{user_a2.id}")
    assert resp.status_code == 404, "Tenant B no debe acceder a usuarios de Tenant A por ID"


# ────────────────────────────────────────────────────────────
# Escenario 3 — Mutate isolation
# ────────────────────────────────────────────────────────────


def test_mutate_isolation(client_b, tenant_a, db):
    """PATCH /v1/users/{id} de Tenant A debe retornar 404 para Tenant B."""
    tenant, _owner = tenant_a
    user_a2 = _make_user(db, tenant, "a2c@tenant-a.com")

    resp = client_b.patch(f"/v1/users/{user_a2.id}", json={"full_name": "Hacked"})
    assert resp.status_code == 404, "Tenant B no debe poder modificar usuarios de Tenant A"

    # Verificar que el nombre no cambió
    db.refresh(user_a2)
    assert user_a2.full_name != "Hacked"


# ────────────────────────────────────────────────────────────
# Escenario 4 — Search isolation
# ────────────────────────────────────────────────────────────


def test_search_isolation(client_b, tenant_a, db):
    """GET /v1/users?q=... de Tenant B no debe devolver resultados de Tenant A."""
    tenant, _owner = tenant_a
    _make_user(db, tenant, "secreto@tenant-a.com")

    resp = client_b.get("/v1/users?q=secreto")
    assert resp.status_code == 200
    items = resp.json()["items"]
    leaked = [u for u in items if "tenant-a.com" in u["email"]]
    assert not leaked, f"Fuga cross-tenant en búsqueda: {leaked}"


# ────────────────────────────────────────────────────────────
# Escenario 5 — Tenant get isolation
# ────────────────────────────────────────────────────────────


def test_tenant_get_isolation(client_b, tenant_a):
    """GET /v1/tenants/{id} de Tenant A debe retornar 404 para Tenant B."""
    tenant, _owner = tenant_a

    resp = client_b.get(f"/v1/tenants/{tenant.id}")
    assert resp.status_code == 404, "Tenant B no debe acceder a los datos de Tenant A"


# ────────────────────────────────────────────────────────────
# Escenarios positivos (sanity checks del propio tenant)
# ────────────────────────────────────────────────────────────


def test_owner_sees_own_users(client_a, tenant_a, db):
    """Owner de Tenant A puede ver sus propios usuarios."""
    tenant, owner = tenant_a
    _make_user(db, tenant, "agent1@tenant-a.com")

    resp = client_a.get("/v1/users")
    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()["items"]]
    assert str(owner.id) in ids


def test_owner_can_get_own_user(client_a, tenant_a):
    """Owner de Tenant A puede obtener su propio perfil por ID."""
    _tenant, owner = tenant_a
    resp = client_a.get(f"/v1/users/{owner.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(owner.id)


def test_get_me(client_a, tenant_a):
    """GET /v1/me retorna el perfil correcto."""
    _tenant, owner = tenant_a
    resp = client_a.get("/v1/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(owner.id)
    assert data["tenant_id"] == str(owner.tenant_id)
    assert data["role"] == "owner"


def test_get_own_tenant(client_a, tenant_a):
    """GET /v1/tenants/{id} con el propio tenant retorna 200."""
    tenant, _owner = tenant_a
    resp = client_a.get(f"/v1/tenants/{tenant.id}")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "tenant-a"


# ────────────────────────────────────────────────────────────
# Auth básica
# ────────────────────────────────────────────────────────────


def test_login(client, tenant_a):
    """Login con credenciales correctas devuelve access y refresh token."""
    resp = client.post("/v1/auth/login", json={"email": "owner@tenant-a.com", "password": "secret123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_login_wrong_password(client, tenant_a):
    """Login con contraseña incorrecta devuelve 401."""
    resp = client.post("/v1/auth/login", json={"email": "owner@tenant-a.com", "password": "wrong"})
    assert resp.status_code == 401


def test_no_token_401(client):
    """Acceso sin token devuelve 401/403."""
    resp = client.get("/v1/me")
    assert resp.status_code in (401, 403)


def test_create_tenant_self_service(client):
    """POST /v1/tenants crea un tenant sin autenticación."""
    resp = client.post("/v1/tenants", json={
        "name": "Nuevo Tenant",
        "slug": "nuevo-tenant",
        "owner_email": "owner@nuevo.com",
        "owner_password": "password123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "nuevo-tenant"


def test_duplicate_slug_rejected(client, tenant_a):
    """POST /v1/tenants con slug duplicado devuelve 400."""
    resp = client.post("/v1/tenants", json={
        "name": "Otro",
        "slug": "tenant-a",
        "owner_email": "otro@nuevo.com",
        "owner_password": "password123",
    })
    assert resp.status_code == 400
