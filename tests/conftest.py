"""Fixtures compartidas para todos los tests de ChatPro.

Estrategia de base de datos:
- Se usa PostgreSQL real (chatpro_test), no SQLite, para garantizar
  compatibilidad con JSONB, UUID y otros tipos específicos de Postgres.
- La DB se limpia en cada sesión de test con DROP/CREATE de tablas.
- Cada test recibe sesión con rollback automático → aislamiento total.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.v1.tenants import TenantCreate
from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.db.base import Base
from src.db.models import Tenant, User
from src.db.session import get_db
import src.agent.models  # noqa: F401 — registrar modelos agente en Base.metadata
import src.connectors.models  # noqa: F401 — registrar modelos conectores en Base.metadata
import src.contacts.models  # noqa: F401 — registrar modelos contactos en Base.metadata
import src.inbox.models  # noqa: F401 — registrar modelos inbox en Base.metadata
import src.wa.models  # noqa: F401 — registrar modelos WA en Base.metadata
from src.main import app

_TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://chatpro:chatpro@db:5432/chatpro_test",
)

_engine = create_engine(_TEST_DB_URL, pool_pre_ping=True)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Crea el schema fresco para toda la sesión de tests."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture
def db():
    """Sesión de BD con rollback tras cada test (aislamiento)."""
    connection = _engine.connect()
    transaction = connection.begin()
    session = _TestSession(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db):
    """TestClient de FastAPI con override de la sesión de BD."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────
# Helpers para crear tenants y usuarios de test
# ────────────────────────────────────────────────────────────


def _create_tenant_and_owner(db, slug: str, email: str, password: str = "secret123") -> tuple[Tenant, User]:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    db.flush()

    owner = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=hash_password(password),
        full_name=f"Owner {slug}",
        role="owner",
    )
    db.add(owner)
    db.flush()
    return tenant, owner


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenant_a(db):
    tenant, owner = _create_tenant_and_owner(db, "tenant-a", "owner@tenant-a.com")
    return tenant, owner


@pytest.fixture
def tenant_b(db):
    tenant, owner = _create_tenant_and_owner(db, "tenant-b", "owner@tenant-b.com")
    return tenant, owner


@pytest.fixture
def client_a(client, tenant_a, db):
    """Cliente HTTP autenticado como owner del Tenant A."""
    _tenant, owner = tenant_a
    client.headers.update(_auth_header(owner))
    return client


@pytest.fixture
def client_b(db):
    """Cliente HTTP autenticado como owner del Tenant B (instancia separada)."""
    _tenant, owner = _create_tenant_and_owner(db, "tenant-b-client", "owner@tenant-b-client.com")

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers.update(_auth_header(owner))
        yield c
    app.dependency_overrides.clear()
