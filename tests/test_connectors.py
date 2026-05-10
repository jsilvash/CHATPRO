"""Tests de Fase 5: Connector ABC + WooCommerce (configure/test_connection/sync_full).

Cobertura:
- Cifrado AES-GCM (encrypt/decrypt round-trip, blob corrupto falla).
- configure(): persiste credenciales cifradas, valida campos requeridos.
- test_connection(): status 'connected' si Woo responde 200, 'error' si no.
- sync_full(): parsea páginas de Woo, crea/actualiza productos en BD.
- API REST: CRUD de configs, configure/test/sync endpoints.
- Aislamiento: tenant B no puede ver ni mutar configs/productos de tenant A.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from src.auth.tokens import create_access_token
from src.connectors.crypto import decrypt_credentials, encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef, Product
from src.connectors.woocommerce.connector import WooCommerceConnector, _map_woo_product
from src.db.models import Tenant, User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_connector_def(db: Session) -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == "woocommerce").first()
    if existing:
        return existing
    defn = ConnectorDef(
        id=uuid.uuid4(), name="woocommerce", kind="ecommerce", version="1.0.0"
    )
    db.add(defn)
    db.flush()
    return defn


def _make_config(db: Session, tenant: Tenant, defn: ConnectorDef) -> ConnectorConfig:
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Mi Tienda Woo",
        status="pending",
    )
    db.add(config)
    db.flush()
    return config


_FAKE_PRODUCT_WOO = {
    "id": 42,
    "sku": "TSHIRT-M",
    "name": "Remera XYZ",
    "short_description": "Remera de algodón",
    "description": "<p>Descripción larga</p>",
    "regular_price": "19.99",
    "sale_price": "14.99",
    "stock_quantity": 100,
    "stock_status": "in_stock",
    "permalink": "https://tienda.com/remera-xyz",
    "images": [{"src": "https://tienda.com/img.jpg"}],
    "categories": [{"name": "Ropa"}],
    "attributes": [],
    "variations": [],
}


# ── Tests de cifrado ──────────────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip():
    creds = {"site_url": "https://tienda.com", "consumer_key": "ck_abc", "consumer_secret": "cs_xyz"}
    blob = encrypt_credentials(creds)
    assert isinstance(blob, bytes)
    assert len(blob) > 60
    result = decrypt_credentials(blob)
    assert result == creds


def test_decrypt_con_blob_corrupto_lanza():
    with pytest.raises(Exception):
        decrypt_credentials(b"blob_invalido_muy_corto")


def test_encrypt_produce_bytes_distintos_cada_vez():
    creds = {"site_url": "https://tienda.com", "consumer_key": "ck", "consumer_secret": "cs"}
    blob1 = encrypt_credentials(creds)
    blob2 = encrypt_credentials(creds)
    assert blob1 != blob2


# ── Tests de configure() ──────────────────────────────────────────────────────


def test_configure_persiste_credenciales(db: Session, tenant_a):
    tenant, _owner = tenant_a
    defn = _make_connector_def(db)
    config = _make_config(db, tenant, defn)

    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    connector.configure({
        "site_url": "https://tienda.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
    })

    # No usar db.refresh() — con _db_session inyectado no hacemos commit.
    # Los cambios están en la sesión en memoria.
    assert config.encrypted_credentials is not None
    assert len(config.encrypted_credentials) > 60
    assert config.webhook_secret


def test_configure_descifra_correctamente(db: Session, tenant_a):
    tenant, _owner = tenant_a
    defn = _make_connector_def(db)
    config = _make_config(db, tenant, defn)

    creds = {"site_url": "https://tienda.com", "consumer_key": "ck_test", "consumer_secret": "cs_test"}
    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    connector.configure(creds)

    # config.encrypted_credentials ya está actualizado en la sesión
    recovered = decrypt_credentials(bytes(config.encrypted_credentials))
    assert recovered["site_url"] == "https://tienda.com"
    assert recovered["consumer_key"] == "ck_test"


def test_configure_rechaza_campos_faltantes(db: Session, tenant_a):
    tenant, _owner = tenant_a
    defn = _make_connector_def(db)
    config = _make_config(db, tenant, defn)

    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    with pytest.raises(ValueError, match="Faltan campos requeridos"):
        connector.configure({"site_url": "https://tienda.com"})


def test_configure_rechaza_url_sin_protocolo(db: Session, tenant_a):
    tenant, _owner = tenant_a
    defn = _make_connector_def(db)
    config = _make_config(db, tenant, defn)

    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    with pytest.raises(ValueError, match="http"):
        connector.configure({
            "site_url": "tienda.com",
            "consumer_key": "ck",
            "consumer_secret": "cs",
        })


# ── Helper para crear connector configurado ───────────────────────────────────


def _setup_connector(db, tenant):
    defn = _make_connector_def(db)
    config = _make_config(db, tenant, defn)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    connector.configure({
        "site_url": "https://tienda.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
    })
    return connector, config


# ── Tests de test_connection() ────────────────────────────────────────────────


def test_test_connection_ok(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        ok = connector.test_connection()

    assert ok is True
    assert config.status == "connected"
    assert config.last_error is None


def test_test_connection_error_http(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        ok = connector.test_connection()

    assert ok is False
    assert config.status == "error"


def test_test_connection_excepcion_red(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("No se pudo conectar")
        mock_client_cls.return_value = mock_client

        ok = connector.test_connection()

    assert ok is False
    assert config.status == "error"
    assert "No se pudo conectar" in config.last_error


# ── Tests de sync_full() ──────────────────────────────────────────────────────


def _build_woo_response(products: list, total_pages: int = 1):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = products
    mock_resp.headers = {"X-WP-TotalPages": str(total_pages)}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_sync_full_crea_productos(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_response([_FAKE_PRODUCT_WOO]),
            _build_woo_response([]),
        ]
        mock_client_cls.return_value = mock_client

        result = connector.sync_full()

    assert result.items_created == 1
    assert result.items_updated == 0
    assert result.errors == []

    product = (
        db.query(Product)
        .filter(
            Product.tenant_id == tenant.id,
            Product.connector_config_id == config.id,
            Product.external_id == "42",
        )
        .first()
    )
    assert product is not None
    assert product.name == "Remera XYZ"
    assert product.sku == "TSHIRT-M"
    assert float(product.price_regular) == 19.99
    assert float(product.price_sale) == 14.99
    assert product.stock_status == "in_stock"
    assert product.stock_quantity == 100
    assert product.url == "https://tienda.com/remera-xyz"


def test_sync_full_actualiza_producto_existente(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    existing = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="42",
        name="Nombre Viejo",
        sku="TSHIRT-M",
    )
    db.add(existing)
    db.flush()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_response([_FAKE_PRODUCT_WOO]),
            _build_woo_response([]),
        ]
        mock_client_cls.return_value = mock_client

        result = connector.sync_full()

    assert result.items_created == 0
    assert result.items_updated == 1
    db.refresh(existing)
    assert existing.name == "Remera XYZ"


def test_sync_full_pagina_multiples_paginas(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    product2 = {**_FAKE_PRODUCT_WOO, "id": 43, "name": "Producto 2", "sku": "PROD-2"}

    def side_effect(url, params=None, **kwargs):
        page = (params or {}).get("page", 1)
        if page == 1:
            return _build_woo_response([_FAKE_PRODUCT_WOO], total_pages=2)
        elif page == 2:
            return _build_woo_response([product2], total_pages=2)
        return _build_woo_response([])

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = side_effect
        mock_client_cls.return_value = mock_client

        result = connector.sync_full()

    assert result.items_created == 2
    count = (
        db.query(Product)
        .filter(Product.tenant_id == tenant.id, Product.connector_config_id == config.id)
        .count()
    )
    assert count == 2


def test_sync_full_actualiza_last_full_sync_at(db: Session, tenant_a):
    tenant, _owner = tenant_a
    connector, config = _setup_connector(db, tenant)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [_build_woo_response([])]
        mock_client_cls.return_value = mock_client

        connector.sync_full()

    assert config.last_full_sync_at is not None
    assert config.status == "connected"


# ── Tests de _map_woo_product ─────────────────────────────────────────────────


def test_map_woo_product_convierte_html():
    tenant_id = uuid.uuid4()
    config_id = uuid.uuid4()
    data = _map_woo_product(_FAKE_PRODUCT_WOO, tenant_id, config_id)
    assert "<p>" not in (data["description_long"] or "")
    assert data["name"] == "Remera XYZ"
    assert data["price_regular"] == 19.99
    assert data["sku"] == "TSHIRT-M"


def test_map_woo_product_precio_vacio():
    raw = {**_FAKE_PRODUCT_WOO, "regular_price": "", "sale_price": ""}
    data = _map_woo_product(raw, uuid.uuid4(), uuid.uuid4())
    assert data["price_regular"] is None
    assert data["price_sale"] is None


# ── Tests de expose_tools() ───────────────────────────────────────────────────


def test_expose_tools_retorna_tres_tools():
    connector = WooCommerceConnector(uuid.uuid4(), uuid.uuid4())
    tools = connector.expose_tools()
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert "buscar_productos" in names
    assert "consultar_stock_y_precio" in names
    assert "historial_pedidos_contacto" in names


def test_expose_tools_input_schema_valido():
    connector = WooCommerceConnector(uuid.uuid4(), uuid.uuid4())
    for tool in connector.expose_tools():
        assert "type" in tool.input_schema
        assert tool.callable_ref.startswith("connectors.woocommerce")


# ── Tests de API REST ─────────────────────────────────────────────────────────


def test_api_listar_tipos_conector(client_a):
    r = client_a.get("/v1/connectors")
    assert r.status_code == 200
    nombres = [c["name"] for c in r.json()]
    assert "woocommerce" in nombres


def test_api_crear_config(client_a):
    r = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce",
        "display_name": "Mi Tienda",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["display_name"] == "Mi Tienda"
    assert data["status"] == "pending"


def test_api_listar_configs_vacia(client_a):
    r = client_a.get("/v1/connector-configs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_crear_y_listar_config(client_a):
    client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda 1"
    })
    r = client_a.get("/v1/connector-configs")
    assert any(c["display_name"] == "Tienda 1" for c in r.json())


def test_api_connector_inexistente_retorna_400(client_a):
    r = client_a.post("/v1/connector-configs", json={
        "connector_name": "shopify_no_existe",
        "display_name": "Shopify",
    })
    assert r.status_code == 400


def test_api_configure_valida_campos(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Mi Tienda"
    })
    config_id = r_create.json()["id"]

    r = client_a.post(f"/v1/connector-configs/{config_id}/configure", json={
        "credentials": {
            "site_url": "https://tienda.com",
            "consumer_key": "ck_abc",
            "consumer_secret": "cs_xyz",
        }
    })
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert r.json()["connector_name"] == "woocommerce"


def test_api_configure_url_invalida_retorna_400(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Mi Tienda"
    })
    config_id = r_create.json()["id"]

    r = client_a.post(f"/v1/connector-configs/{config_id}/configure", json={
        "credentials": {"site_url": "tienda.com", "consumer_key": "ck", "consumer_secret": "cs"}
    })
    assert r.status_code == 400


def test_api_test_connection_conectado(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda"
    })
    config_id = r_create.json()["id"]
    client_a.post(f"/v1/connector-configs/{config_id}/configure", json={
        "credentials": {"site_url": "https://tienda.com", "consumer_key": "ck", "consumer_secret": "cs"}
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        r = client_a.post(f"/v1/connector-configs/{config_id}/test")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["status"] == "connected"


def test_api_sync_sin_configure_retorna_400(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda"
    })
    config_id = r_create.json()["id"]

    r = client_a.post(f"/v1/connector-configs/{config_id}/sync")
    assert r.status_code == 400


def test_api_sync_full(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda"
    })
    config_id = r_create.json()["id"]
    client_a.post(f"/v1/connector-configs/{config_id}/configure", json={
        "credentials": {"site_url": "https://tienda.com", "consumer_key": "ck", "consumer_secret": "cs"}
    })

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp_ok
        mock_cls.return_value = mock_client
        client_a.post(f"/v1/connector-configs/{config_id}/test")

    mock_products_resp = MagicMock()
    mock_products_resp.status_code = 200
    mock_products_resp.json.return_value = [_FAKE_PRODUCT_WOO]
    mock_products_resp.headers = {"X-WP-TotalPages": "1"}
    mock_products_resp.raise_for_status = MagicMock()

    mock_empty_resp = MagicMock()
    mock_empty_resp.status_code = 200
    mock_empty_resp.json.return_value = []
    mock_empty_resp.headers = {"X-WP-TotalPages": "1"}
    mock_empty_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [mock_products_resp, mock_empty_resp]
        mock_cls.return_value = mock_client

        r = client_a.post(f"/v1/connector-configs/{config_id}/sync")

    assert r.status_code == 200
    data = r.json()
    assert data["items_created"] == 1
    assert data["errors"] == []


def test_api_listar_productos_vacios(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda"
    })
    config_id = r_create.json()["id"]
    r = client_a.get(f"/v1/connector-configs/{config_id}/products")
    assert r.status_code == 200
    assert r.json() == []


def test_api_eliminar_config(client_a):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda Borrar"
    })
    config_id = r_create.json()["id"]

    r_del = client_a.delete(f"/v1/connector-configs/{config_id}")
    assert r_del.status_code == 204

    r_get = client_a.get(f"/v1/connector-configs/{config_id}")
    assert r_get.status_code == 404


# ── Tests de aislamiento ──────────────────────────────────────────────────────


def test_aislamiento_tenant_b_no_ve_configs_de_a(client_a, client_b):
    client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda A"
    })
    r = client_b.get("/v1/connector-configs")
    assert r.status_code == 200
    names = [c["display_name"] for c in r.json()]
    assert "Tienda A" not in names


def test_aislamiento_tenant_b_no_obtiene_config_de_a(client_a, client_b):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda A"
    })
    config_id = r_create.json()["id"]

    r = client_b.get(f"/v1/connector-configs/{config_id}")
    assert r.status_code == 404


def test_aislamiento_tenant_b_no_configura_config_de_a(client_a, client_b):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda A"
    })
    config_id = r_create.json()["id"]

    r = client_b.post(f"/v1/connector-configs/{config_id}/configure", json={
        "credentials": {"site_url": "https://evil.com", "consumer_key": "ck", "consumer_secret": "cs"}
    })
    assert r.status_code == 404


def test_aislamiento_tenant_b_no_sincroniza_config_de_a(client_a, client_b):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda A"
    })
    config_id = r_create.json()["id"]

    r = client_b.post(f"/v1/connector-configs/{config_id}/sync")
    assert r.status_code in (400, 404)


def test_aislamiento_tenant_b_no_lista_productos_de_a(client_a, client_b):
    r_create = client_a.post("/v1/connector-configs", json={
        "connector_name": "woocommerce", "display_name": "Tienda A"
    })
    config_id = r_create.json()["id"]

    r = client_b.get(f"/v1/connector-configs/{config_id}/products")
    assert r.status_code == 404
