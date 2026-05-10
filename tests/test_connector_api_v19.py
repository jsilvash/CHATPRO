"""Tests de Fase 19: Connector API REST — nuevos endpoints y mejoras.

Cobertura:
- PATCH /v1/connector-configs/{id}: actualizar display_name; habilitar/deshabilitar.
- Credenciales genéricas: configure con formato {"credentials": {...}} para Woo y Shopify.
- connector_name en ConnectorConfigOut: listar y obtener incluyen nombre del conector.
- GET /v1/connector-configs/{id}/webhook-info: URL y secret correctos; 400 sin configurar.
- POST /v1/connector-configs/{id}/sync-incremental: llama sync_incremental; 400 sin sync previo.
- GET /v1/connector-configs/{id}/orders: lista órdenes; filtros status y email.
- Aislamiento en todos los nuevos endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.auth.tokens import create_access_token
from src.connectors.crypto import encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product
from src.db.models import Tenant, User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_connector_def(db: Session, name: str = "woocommerce") -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == name).first()
    if existing:
        return existing
    defn = ConnectorDef(
        id=uuid.uuid4(), name=name, kind="ecommerce", version="1.0.0"
    )
    db.add(defn)
    db.flush()
    return defn


def _make_config(
    db: Session,
    tenant: Tenant,
    name: str = "woocommerce",
    status: str = "connected",
    with_creds: bool = True,
    webhook_secret: str = "secreto_test",
) -> ConnectorConfig:
    defn = _make_connector_def(db, name)
    blob = None
    if with_creds:
        if name == "shopify":
            blob = encrypt_credentials({"shop_url": "https://shop.myshopify.com", "access_token": "shpat_xxx"})
        else:
            blob = encrypt_credentials({"site_url": "https://tienda.com", "consumer_key": "ck", "consumer_secret": "cs"})

    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Tienda Test",
        status=status,
        webhook_secret=webhook_secret,
        encrypted_credentials=blob,
    )
    db.add(config)
    db.flush()
    return config


def _make_order(
    db: Session,
    tenant: Tenant,
    config: ConnectorConfig,
    **kwargs,
) -> Order:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": tenant.id,
        "connector_config_id": config.id,
        "external_id": str(uuid.uuid4().int)[:8],
        "status": "completed",
        "total": 99.99,
        "currency": "CLP",
        "customer_email": "cliente@ejemplo.com",
    }
    defaults.update(kwargs)
    order = Order(**defaults)
    db.add(order)
    db.flush()
    return order


# ── PATCH /v1/connector-configs/{id} ─────────────────────────────────────────


def test_patch_actualiza_display_name(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)

    r = client_a.patch(f"/v1/connector-configs/{config.id}", json={"display_name": "Nueva Tienda"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Nueva Tienda"

    db.refresh(config)
    assert config.display_name == "Nueva Tienda"


def test_patch_deshabilita_conector(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, status="connected")

    r = client_a.patch(f"/v1/connector-configs/{config.id}", json={"status": "disabled"})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_patch_reactiva_conector(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, status="disabled")

    r = client_a.patch(f"/v1/connector-configs/{config.id}", json={"status": "connected"})
    assert r.status_code == 200
    assert r.json()["status"] == "connected"


def test_patch_status_invalido_retorna_400(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)

    r = client_a.patch(f"/v1/connector-configs/{config.id}", json={"status": "error"})
    assert r.status_code == 400


def test_patch_sin_campos_no_modifica(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)
    nombre_original = config.display_name

    r = client_a.patch(f"/v1/connector-configs/{config.id}", json={})
    assert r.status_code == 200
    assert r.json()["display_name"] == nombre_original


def test_patch_aislamiento_tenant_b_no_puede_modificar(db: Session, tenant_a, client_a, client_b):
    config = _make_config(db, tenant_a)

    r = client_b.patch(f"/v1/connector-configs/{config.id}", json={"display_name": "Hack"})
    assert r.status_code == 404


# ── connector_name en ConnectorConfigOut ─────────────────────────────────────


def test_crear_config_incluye_connector_name(client_a):
    r = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce",
        "display_name": "Mi Tienda Woo",
    })
    assert r.status_code == 201
    assert r.json()["connector_name"] == "woocommerce"


def test_listar_configs_incluye_connector_name(db: Session, tenant_a, client_a):
    _make_config(db, tenant_a, name="woocommerce")

    r = client_a.get("/v1/connector-configs")
    assert r.status_code == 200
    assert len(r.json()) >= 1
    for item in r.json():
        assert item["connector_name"] is not None


def test_obtener_config_incluye_connector_name(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="woocommerce")

    r = client_a.get(f"/v1/connector-configs/{config.id}")
    assert r.status_code == 200
    assert r.json()["connector_name"] == "woocommerce"


# ── Credenciales genéricas (Shopify via API) ──────────────────────────────────


def test_configure_shopify_via_api(db: Session, tenant_a, client_a):
    """El endpoint /configure acepta credenciales de cualquier conector."""
    config = _make_config(db, tenant_a, name="shopify", with_creds=False, status="pending")

    r = client_a.post(f"/v1/connector-configs/{config.id}/configure", json={
        "credentials": {
            "shop_url": "https://mi-tienda.myshopify.com",
            "access_token": "shpat_test",
        }
    })
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert r.json()["connector_name"] == "shopify"


def test_configure_shopify_campos_faltantes_retorna_400(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="shopify", with_creds=False, status="pending")

    r = client_a.post(f"/v1/connector-configs/{config.id}/configure", json={
        "credentials": {"shop_url": "https://tienda.myshopify.com"}  # falta access_token
    })
    assert r.status_code == 400


def test_configure_woocommerce_via_formato_generico(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="woocommerce", with_creds=False, status="pending")

    r = client_a.post(f"/v1/connector-configs/{config.id}/configure", json={
        "credentials": {
            "site_url": "https://tienda.com",
            "consumer_key": "ck_abc",
            "consumer_secret": "cs_xyz",
        }
    })
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


# ── GET /v1/connector-configs/{id}/webhook-info ───────────────────────────────


def test_webhook_info_woocommerce(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="woocommerce", webhook_secret="secreto_woo")

    r = client_a.get(f"/v1/connector-configs/{config.id}/webhook-info")
    assert r.status_code == 200
    data = r.json()
    assert data["webhook_secret"] == "secreto_woo"
    assert data["connector_name"] == "woocommerce"
    assert "/webhooks/woo/" in data["webhook_url"]
    assert str(config.id) in data["webhook_url"]
    assert str(tenant_a.id) in data["webhook_url"]


def test_webhook_info_shopify(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="shopify", webhook_secret="secreto_shopify")

    r = client_a.get(f"/v1/connector-configs/{config.id}/webhook-info")
    assert r.status_code == 200
    data = r.json()
    assert "/webhooks/shopify/" in data["webhook_url"]
    assert data["connector_name"] == "shopify"


def test_webhook_info_sin_secret_retorna_400(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, with_creds=False, webhook_secret="")

    r = client_a.get(f"/v1/connector-configs/{config.id}/webhook-info")
    assert r.status_code == 400


def test_webhook_info_aislamiento(db: Session, tenant_a, client_a, client_b):
    config = _make_config(db, tenant_a)

    r = client_b.get(f"/v1/connector-configs/{config.id}/webhook-info")
    assert r.status_code == 404


# ── POST /v1/connector-configs/{id}/sync-incremental ─────────────────────────


def test_sync_incremental_requiere_fecha_previa(db: Session, tenant_a, client_a):
    """Sin last_full_sync_at y sin since param, debe devolver 400."""
    config = _make_config(db, tenant_a, status="connected")
    # Asegurarse de que no haya last_full_sync_at
    config.last_full_sync_at = None
    db.flush()

    r = client_a.post(f"/v1/connector-configs/{config.id}/sync-incremental")
    assert r.status_code == 400


def _mock_woo_empty_response() -> MagicMock:
    """Mock para respuesta vacía de WooCommerce (lista vacía de productos)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    resp.headers = {"X-WP-TotalPages": "1"}
    resp.raise_for_status = MagicMock()
    return resp


def test_sync_incremental_con_since_param(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="woocommerce", status="connected")

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_woo_empty_response()
        mock_cls.return_value = mock_client

        r = client_a.post(
            f"/v1/connector-configs/{config.id}/sync-incremental",
            params={"since": "2026-01-01T00:00:00Z"},
        )

    assert r.status_code == 200
    data = r.json()
    assert "items_processed" in data
    assert "errors" in data


def test_sync_incremental_usa_last_full_sync_at(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, name="woocommerce", status="connected")
    config.last_full_sync_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db.flush()

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_woo_empty_response()
        mock_cls.return_value = mock_client

        r = client_a.post(f"/v1/connector-configs/{config.id}/sync-incremental")

    assert r.status_code == 200


def test_sync_incremental_estado_no_conectado_retorna_400(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a, status="pending")

    r = client_a.post(f"/v1/connector-configs/{config.id}/sync-incremental")
    assert r.status_code == 400


def test_sync_incremental_aislamiento(db: Session, tenant_a, client_a, client_b):
    config = _make_config(db, tenant_a, name="woocommerce", status="connected")
    config.last_full_sync_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db.flush()

    r = client_b.post(f"/v1/connector-configs/{config.id}/sync-incremental")
    assert r.status_code in (400, 404)


# ── GET /v1/connector-configs/{id}/orders ────────────────────────────────────


def test_listar_ordenes_vacio(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)

    r = client_a.get(f"/v1/connector-configs/{config.id}/orders")
    assert r.status_code == 200
    assert r.json() == []


def test_listar_ordenes_retorna_ordenes(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)
    _make_order(db, tenant_a, config, status="completed", customer_email="a@ejemplo.com")
    _make_order(db, tenant_a, config, status="pending", customer_email="b@ejemplo.com")

    r = client_a.get(f"/v1/connector-configs/{config.id}/orders")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_listar_ordenes_filtro_status(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)
    _make_order(db, tenant_a, config, status="completed")
    _make_order(db, tenant_a, config, status="pending")

    r = client_a.get(f"/v1/connector-configs/{config.id}/orders", params={"status": "completed"})
    assert r.status_code == 200
    orders = r.json()
    assert len(orders) == 1
    assert orders[0]["status"] == "completed"


def test_listar_ordenes_filtro_email(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)
    _make_order(db, tenant_a, config, customer_email="juan@tienda.com")
    _make_order(db, tenant_a, config, customer_email="otro@tienda.com")

    r = client_a.get(f"/v1/connector-configs/{config.id}/orders", params={"customer_email": "juan"})
    assert r.status_code == 200
    orders = r.json()
    assert len(orders) == 1
    assert "juan" in orders[0]["customer_email"]


def test_listar_ordenes_paginacion(db: Session, tenant_a, client_a):
    config = _make_config(db, tenant_a)
    for i in range(5):
        _make_order(db, tenant_a, config, external_id=str(i))

    r = client_a.get(f"/v1/connector-configs/{config.id}/orders", params={"limit": 2, "skip": 0})
    assert r.status_code == 200
    assert len(r.json()) == 2

    r2 = client_a.get(f"/v1/connector-configs/{config.id}/orders", params={"limit": 2, "skip": 2})
    assert r2.status_code == 200
    assert len(r2.json()) == 2


def test_listar_ordenes_aislamiento(db: Session, tenant_a, client_a, client_b):
    config = _make_config(db, tenant_a)
    _make_order(db, tenant_a, config)

    r = client_b.get(f"/v1/connector-configs/{config.id}/orders")
    assert r.status_code == 404


def test_listar_ordenes_no_cruza_configs(db: Session, tenant_a, client_a):
    """Órdenes de config_B no aparecen en endpoint de config_A."""
    config_a = _make_config(db, tenant_a)
    config_b = _make_config(db, tenant_a, name="woocommerce")
    config_b.display_name = "Tienda B"
    db.flush()
    _make_order(db, tenant_a, config_b, customer_email="solo_en_b@test.com")

    r = client_a.get(f"/v1/connector-configs/{config_a.id}/orders")
    assert r.status_code == 200
    emails = [o["customer_email"] for o in r.json()]
    assert "solo_en_b@test.com" not in emails
