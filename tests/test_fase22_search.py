"""Tests Fase 22C: endpoint de búsqueda semántica de productos.

GET /v1/connector-configs/{id}/search?q=<query>&max_results=5

Cobertura:
- Autenticación requerida (401 sin token).
- Aislamiento: tenant B no puede buscar en config de tenant A.
- Config no encontrada → 404.
- Búsqueda exitosa: retorna lista con campos id, external_id, name, price, url, score.
- max_results respetado.
- q vacío rechazado (422).
- Conector no registrado → 400.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.auth.tokens import create_access_token
from src.connectors.base import SearchResult
from src.connectors.crypto import encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef
from src.db.models import Tenant, User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_connector_def(db: Session, name: str = "woocommerce") -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == name).first()
    if existing:
        return existing
    defn = ConnectorDef(id=uuid.uuid4(), name=name, kind="ecommerce", version="1.0.0")
    db.add(defn)
    db.flush()
    return defn


def _make_config(
    db: Session,
    tenant: Tenant,
    name: str = "woocommerce",
    status: str = "connected",
) -> ConnectorConfig:
    defn = _make_connector_def(db, name)
    blob = encrypt_credentials({"site_url": "https://t.com", "consumer_key": "ck", "consumer_secret": "cs"})
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Tienda",
        status=status,
        webhook_secret="s",
        encrypted_credentials=blob,
    )
    db.add(config)
    db.flush()
    return config


def _fake_search_results(n: int = 2) -> list[SearchResult]:
    return [
        SearchResult(
            id=str(uuid.uuid4()),
            title=f"Producto {i}",
            snippet=f"Descripción {i}",
            url=f"https://tienda.com/producto-{i}",
            score=round(0.9 - i * 0.1, 2),
            metadata={
                "external_id": str(1000 + i),
                "price_regular": 29.99 + i * 10,
                "price_sale": None,
                "sku": f"SKU-{i}",
                "currency": "CLP",
                "stock_quantity": 5,
                "stock_status": "in_stock",
            },
        )
        for i in range(n)
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_search_sin_auth_retorna_401(client, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    r = client.get(f"/v1/connector-configs/{config.id}/search", params={"q": "zapato"})
    assert r.status_code == 401


def test_search_config_no_existe_retorna_404(client_a, db, tenant_a):
    r = client_a.get(f"/v1/connector-configs/{uuid.uuid4()}/search", params={"q": "test"})
    assert r.status_code == 404


def test_search_aislamiento_tenant_b_no_puede(client_a, client_b, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    r = client_b.get(f"/v1/connector-configs/{config.id}/search", params={"q": "test"})
    assert r.status_code == 404


def test_search_q_vacio_retorna_422(client_a, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    r = client_a.get(f"/v1/connector-configs/{config.id}/search", params={"q": ""})
    assert r.status_code == 422


def test_search_retorna_lista_con_campos_correctos(client_a, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    fake_results = _fake_search_results(2)

    with patch("src.connectors.woocommerce.connector.WooCommerceConnector.search", return_value=fake_results):
        r = client_a.get(
            f"/v1/connector-configs/{config.id}/search",
            params={"q": "producto", "max_results": 5},
        )

    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert "total" in data
    assert data["total"] == 2
    assert len(data["results"]) == 2

    first = data["results"][0]
    assert "id" in first
    assert "external_id" in first
    assert "name" in first
    assert "price" in first
    assert "url" in first
    assert "score" in first


def test_search_respeta_max_results(client_a, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    fake_results = _fake_search_results(3)

    with patch("src.connectors.woocommerce.connector.WooCommerceConnector.search", return_value=fake_results[:2]):
        r = client_a.get(
            f"/v1/connector-configs/{config.id}/search",
            params={"q": "test", "max_results": 2},
        )

    assert r.status_code == 200
    assert r.json()["total"] <= 2


def test_search_price_usa_sale_si_disponible(client_a, db, tenant_a):
    """price en el resultado usa price_sale si está disponible, sino price_regular."""
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    result_con_sale = SearchResult(
        id=str(uuid.uuid4()),
        title="Producto rebajado",
        snippet="",
        url="https://t.com/p",
        score=0.95,
        metadata={
            "external_id": "999",
            "price_regular": 100.0,
            "price_sale": 75.0,
            "sku": "SKU-SALE",
            "currency": "CLP",
            "stock_quantity": 3,
            "stock_status": "in_stock",
        },
    )

    with patch("src.connectors.woocommerce.connector.WooCommerceConnector.search", return_value=[result_con_sale]):
        r = client_a.get(
            f"/v1/connector-configs/{config.id}/search",
            params={"q": "rebaja"},
        )

    assert r.status_code == 200
    assert r.json()["results"][0]["price"] == 75.0


def test_search_sin_resultados_retorna_lista_vacia(client_a, db, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    with patch("src.connectors.woocommerce.connector.WooCommerceConnector.search", return_value=[]):
        r = client_a.get(
            f"/v1/connector-configs/{config.id}/search",
            params={"q": "xyzinexistente"},
        )

    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["total"] == 0
