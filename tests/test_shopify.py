"""Tests de Fase 12: ShopifyConnector (configure/test_connection/sync_full/tools/aislamiento).

Cobertura:
- configure(): persiste credenciales cifradas AES-GCM, valida campos requeridos.
- test_connection(): status 'connected' si Shopify responde 200, 'error' si no.
- sync_full(): parsea páginas de Shopify (mock HTTP), crea/actualiza productos en BD.
- sync_full con paginación cursor (Link header).
- tools del agente: buscar_productos, consultar_stock_y_precio.
- expose_tools(): retorna ToolSchemas con callable_ref correcto.
- search(): búsqueda full-text sobre productos en BD.
- Aislamiento cross-tenant: tenant B no ve productos de tenant A.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from src.auth.tokens import create_access_token
from src.connectors.crypto import decrypt_credentials, encrypt_credentials
from src.connectors.models import ConnectorConfig, ConnectorDef, Product
from src.connectors.shopify.connector import (
    ShopifyConnector,
    _extract_page_info,
    _map_shopify_product,
    _parse_next_link,
)
from src.connectors.shopify.tools import buscar_productos, consultar_stock_y_precio
from src.db.models import Tenant, User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_shopify_def(db: Session) -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == "shopify").first()
    if existing:
        return existing
    defn = ConnectorDef(
        id=uuid.uuid4(), name="shopify", kind="ecommerce", version="1.0.0"
    )
    db.add(defn)
    db.flush()
    return defn


def _make_config(db: Session, tenant: Tenant, defn: ConnectorDef) -> ConnectorConfig:
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Mi Tienda Shopify",
        status="pending",
    )
    db.add(config)
    db.flush()
    return config


def _make_connector(db: Session, tenant: Tenant, config: ConnectorConfig) -> ShopifyConnector:
    return ShopifyConnector(tenant_id=tenant.id, config_id=config.id, db=db)


_CREDS = {
    "shop_url": "https://mitienda.myshopify.com",
    "access_token": "shpat_abc123",
}

_FAKE_PRODUCT_SHOPIFY = {
    "id": 99,
    "title": "Remera Shopify",
    "handle": "remera-shopify",
    "body_html": "<p>Remera de algodón orgánico</p>",
    "tags": "ropa,verano",
    "variants": [
        {
            "id": 101,
            "sku": "SHOP-SHIRT-M",
            "price": "29.99",
            "compare_at_price": "39.99",
            "inventory_quantity": 50,
            "inventory_policy": "deny",
        }
    ],
    "images": [{"src": "https://cdn.shopify.com/img.jpg"}],
    "options": [{"name": "Talle", "values": ["S", "M", "L"]}],
}

_FAKE_PRODUCT_ON_SALE = {
    "id": 100,
    "title": "Producto en oferta",
    "handle": "producto-oferta",
    "body_html": "",
    "tags": "",
    "variants": [
        {
            "id": 102,
            "sku": "OFERTA-01",
            "price": "19.99",
            "compare_at_price": "39.99",
            "inventory_quantity": 5,
            "inventory_policy": "deny",
        }
    ],
    "images": [],
    "options": [],
}

_FAKE_PRODUCT_NO_STOCK = {
    "id": 101,
    "title": "Producto sin stock",
    "handle": "sin-stock",
    "body_html": "",
    "tags": "",
    "variants": [
        {
            "id": 103,
            "sku": "NO-STOCK",
            "price": "9.99",
            "compare_at_price": None,
            "inventory_quantity": 0,
            "inventory_policy": "deny",
        }
    ],
    "images": [],
    "options": [],
}


# ── Tests de configure ────────────────────────────────────────────────────────


def test_configure_persiste_credenciales(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    connector.configure(_CREDS)

    db.refresh(config)
    assert config.encrypted_credentials is not None
    assert config.status == "pending"
    assert config.webhook_secret != ""

    creds_back = decrypt_credentials(bytes(config.encrypted_credentials))
    assert creds_back["shop_url"] == _CREDS["shop_url"]
    assert creds_back["access_token"] == _CREDS["access_token"]


def test_configure_falla_sin_campos_requeridos(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    with pytest.raises(ValueError, match="Faltan campos requeridos"):
        connector.configure({"shop_url": "https://tienda.myshopify.com"})

    with pytest.raises(ValueError, match="Faltan campos requeridos"):
        connector.configure({"access_token": "tok"})


def test_configure_falla_con_url_invalida(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    with pytest.raises(ValueError, match="shop_url debe comenzar"):
        connector.configure({"shop_url": "mitienda.myshopify.com", "access_token": "tok"})


def test_configure_genera_webhook_secret_una_sola_vez(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    connector.configure(_CREDS)
    db.refresh(config)
    secret_original = config.webhook_secret

    connector.configure({**_CREDS, "access_token": "otro_token"})
    db.refresh(config)
    assert config.webhook_secret == secret_original  # no cambia si ya existe


# ── Tests de test_connection ──────────────────────────────────────────────────


def test_test_connection_ok(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = mock_resp

        result = connector.test_connection()

    assert result is True
    db.refresh(config)
    assert config.status == "connected"
    assert config.last_error is None


def test_test_connection_error_http(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = mock_resp

        result = connector.test_connection()

    assert result is False
    db.refresh(config)
    assert config.status == "error"
    assert "401" in config.last_error


def test_test_connection_excepcion_red(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__ = MagicMock(
            side_effect=httpx.ConnectError("timeout")
        )

        result = connector.test_connection()

    assert result is False
    db.refresh(config)
    assert config.status == "error"


# ── Tests de sync_full ────────────────────────────────────────────────────────


def _mock_shopify_response(products: list, next_link: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"products": products}
    link_value = f'<https://tienda.myshopify.com/admin/api/2024-01/products.json?page_info=abc123>; rel="next"' if next_link else ""
    resp.headers = {"Link": link_value}
    resp.raise_for_status = MagicMock()
    return resp


def test_sync_full_crea_productos(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    resp_pag1 = _mock_shopify_response([_FAKE_PRODUCT_SHOPIFY])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp_pag1

        result = connector.sync_full()

    assert result.items_processed == 1
    assert result.items_created == 1
    assert result.items_updated == 0
    assert result.errors == []

    products = db.query(Product).filter(
        Product.tenant_id == tenant.id,
        Product.connector_config_id == config.id,
    ).all()
    assert len(products) == 1
    p = products[0]
    assert p.external_id == "99"
    assert p.name == "Remera Shopify"
    assert p.sku == "SHOP-SHIRT-M"
    assert float(p.price_regular) == 39.99  # compare_at_price > price → regular
    assert float(p.price_sale) == 29.99


def test_sync_full_actualiza_producto_existente(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    resp = _mock_shopify_response([_FAKE_PRODUCT_SHOPIFY])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp

        connector.sync_full()

    producto_modificado = {**_FAKE_PRODUCT_SHOPIFY, "title": "Remera Shopify V2"}
    resp2 = _mock_shopify_response([producto_modificado])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp2

        result2 = connector.sync_full()

    assert result2.items_created == 0
    assert result2.items_updated == 1

    products = db.query(Product).filter(
        Product.tenant_id == tenant.id,
        Product.connector_config_id == config.id,
    ).all()
    assert len(products) == 1
    assert products[0].name == "Remera Shopify V2"


def test_sync_full_mapea_stock_out_of_stock(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    resp = _mock_shopify_response([_FAKE_PRODUCT_NO_STOCK])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp

        connector.sync_full()

    p = db.query(Product).filter(
        Product.tenant_id == tenant.id,
        Product.external_id == "101",
    ).first()
    assert p.stock_status == "out_of_stock"
    assert p.stock_quantity == 0
    # Sin compare_at_price → price_regular = price, price_sale = None
    assert float(p.price_regular) == 9.99
    assert p.price_sale is None


def test_sync_full_multiples_paginas(db, tenant_a):
    """Verifica que sync_full sigue leyendo mientras hay Link: next."""
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    producto2 = {**_FAKE_PRODUCT_ON_SALE, "id": 200, "handle": "producto-2"}

    resp_pag1 = MagicMock()
    resp_pag1.status_code = 200
    resp_pag1.json.return_value = {"products": [_FAKE_PRODUCT_SHOPIFY]}
    resp_pag1.headers = {
        "Link": '<https://tienda.myshopify.com/admin/api/2024-01/products.json?page_info=cursor_p2>; rel="next"'
    }
    resp_pag1.raise_for_status = MagicMock()

    resp_pag2 = MagicMock()
    resp_pag2.status_code = 200
    resp_pag2.json.return_value = {"products": [producto2]}
    resp_pag2.headers = {"Link": ""}
    resp_pag2.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.side_effect = [resp_pag1, resp_pag2]

        result = connector.sync_full()

    assert result.items_processed == 2
    assert result.items_created == 2
    assert result.errors == []


def test_sync_full_actualiza_config_ok(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    resp = _mock_shopify_response([_FAKE_PRODUCT_SHOPIFY])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp

        connector.sync_full()

    db.refresh(config)
    assert config.status == "connected"
    assert config.last_full_sync_at is not None
    assert config.last_error is None


def test_sync_full_error_http_registra_en_config(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)
    connector.configure(_CREDS)

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        result = connector.sync_full()

    assert len(result.errors) > 0
    db.refresh(config)
    assert config.status == "error"


# ── Tests de helpers de paginación ────────────────────────────────────────────


def test_parse_next_link_extrae_url():
    link = '<https://tienda.myshopify.com/products.json?page_info=abc>; rel="next"'
    assert _parse_next_link(link) == "https://tienda.myshopify.com/products.json?page_info=abc"


def test_parse_next_link_sin_next_retorna_none():
    link = '<https://tienda.myshopify.com/products.json?page_info=abc>; rel="previous"'
    assert _parse_next_link(link) is None


def test_parse_next_link_header_vacio():
    assert _parse_next_link("") is None


def test_extract_page_info():
    url = "https://tienda.myshopify.com/products.json?limit=250&page_info=cursor123"
    assert _extract_page_info(url) == "cursor123"


def test_extract_page_info_sin_param():
    assert _extract_page_info("https://tienda.myshopify.com/products.json") is None


# ── Tests de mapeo de productos ───────────────────────────────────────────────


def test_map_shopify_product_precio_en_oferta():
    tenant_id = uuid.uuid4()
    config_id = uuid.uuid4()
    result = _map_shopify_product(_FAKE_PRODUCT_ON_SALE, tenant_id, config_id, "https://tienda.myshopify.com")
    assert float(result["price_regular"]) == 39.99
    assert float(result["price_sale"]) == 19.99


def test_map_shopify_product_sin_oferta():
    tenant_id = uuid.uuid4()
    config_id = uuid.uuid4()
    raw = {**_FAKE_PRODUCT_SHOPIFY, "variants": [{
        "sku": "X", "price": "10.00", "compare_at_price": None,
        "inventory_quantity": 10, "inventory_policy": "deny"
    }]}
    result = _map_shopify_product(raw, tenant_id, config_id, "https://tienda.myshopify.com")
    assert float(result["price_regular"]) == 10.0
    assert result["price_sale"] is None


def test_map_shopify_product_url_usa_shop_url():
    tenant_id = uuid.uuid4()
    config_id = uuid.uuid4()
    result = _map_shopify_product(_FAKE_PRODUCT_SHOPIFY, tenant_id, config_id, "https://mitienda.myshopify.com")
    assert result["url"] == "https://mitienda.myshopify.com/products/remera-shopify"


def test_map_shopify_product_backorder():
    tenant_id = uuid.uuid4()
    config_id = uuid.uuid4()
    raw = {**_FAKE_PRODUCT_NO_STOCK, "variants": [{
        "sku": "BO", "price": "5.00", "compare_at_price": None,
        "inventory_quantity": 0, "inventory_policy": "continue"
    }]}
    result = _map_shopify_product(raw, tenant_id, config_id)
    assert result["stock_status"] == "on_backorder"


# ── Tests de tools del agente ─────────────────────────────────────────────────


def _seed_product(db: Session, tenant: Tenant, config: ConnectorConfig) -> Product:
    p = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="99",
        sku="SHOP-SHIRT-M",
        name="Remera Shopify",
        description_short="Remera de algodón orgánico",
        price_regular=39.99,
        price_sale=29.99,
        currency="USD",
        stock_quantity=50,
        stock_status="in_stock",
        url="https://mitienda.myshopify.com/products/remera-shopify",
    )
    db.add(p)
    db.flush()
    return p


def test_tool_buscar_productos_encuentra(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    _seed_product(db, tenant, config)

    result = buscar_productos(
        query="Remera",
        max_results=5,
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert len(result["resultados"]) == 1
    assert result["resultados"][0]["nombre"] == "Remera Shopify"
    assert result["resultados"][0]["sku"] == "SHOP-SHIRT-M"
    assert result["resultados"][0]["precio"] == 29.99  # usa price_sale


def test_tool_buscar_productos_sin_resultados(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)

    result = buscar_productos(
        query="ProductoInexistente99",
        max_results=5,
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert result["resultados"] == []
    assert "No se encontraron" in result["mensaje"]


def test_tool_consultar_stock_por_sku(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    _seed_product(db, tenant, config)

    result = consultar_stock_y_precio(
        sku="SHOP-SHIRT-M",
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert result["nombre"] == "Remera Shopify"
    assert result["stock_cantidad"] == 50
    assert result["stock_status"] == "in_stock"
    assert result["precio_regular"] == 39.99
    assert result["precio_oferta"] == 29.99


def test_tool_consultar_stock_por_product_id(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    _seed_product(db, tenant, config)

    result = consultar_stock_y_precio(
        product_id="99",
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert result["nombre"] == "Remera Shopify"


def test_tool_consultar_stock_no_encontrado(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)

    result = consultar_stock_y_precio(
        sku="NOEXISTE",
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert "error" in result


def test_tool_consultar_sin_sku_ni_id(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)

    result = consultar_stock_y_precio(
        tenant_id=tenant.id,
        config_id=config.id,
        db=db,
    )
    assert "error" in result


# ── Tests de expose_tools ─────────────────────────────────────────────────────


def test_expose_tools_retorna_esquemas(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    tools = connector.expose_tools()
    assert len(tools) == 2

    nombres = {t.name for t in tools}
    assert "shopify_buscar_productos" in nombres
    assert "shopify_consultar_stock_y_precio" in nombres

    for tool in tools:
        assert tool.callable_ref.startswith("connectors.shopify.tools:")
        assert "type" in tool.input_schema


# ── Tests de search ───────────────────────────────────────────────────────────


def test_search_retorna_resultados(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    _seed_product(db, tenant, config)
    connector = _make_connector(db, tenant, config)

    results = connector.search("Remera")
    assert len(results) == 1
    assert results[0].title == "Remera Shopify"
    assert results[0].score == 1.0


def test_search_sin_resultados(db, tenant_a):
    tenant, _ = tenant_a
    defn = _make_shopify_def(db)
    config = _make_config(db, tenant, defn)
    connector = _make_connector(db, tenant, config)

    results = connector.search("ProductoQueNoExiste")
    assert results == []


# ── Tests de aislamiento cross-tenant ─────────────────────────────────────────


def test_aislamiento_sync_full(db, tenant_a, tenant_b):
    """Tenant B no puede ver productos del sync de tenant A."""
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    defn = _make_shopify_def(db)
    config_a = _make_config(db, tenant_a_obj, defn)
    config_b = _make_config(db, tenant_b_obj, defn)

    connector_a = _make_connector(db, tenant_a_obj, config_a)
    connector_a.configure(_CREDS)

    resp = _mock_shopify_response([_FAKE_PRODUCT_SHOPIFY])

    with patch("httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = lambda s: mock_ctx
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = resp

        connector_a.sync_full()

    # Tenant B no debe ver productos de A
    products_b = db.query(Product).filter(
        Product.tenant_id == tenant_b_obj.id,
    ).all()
    assert len(products_b) == 0

    # Connector de B con search no retorna nada
    connector_b = _make_connector(db, tenant_b_obj, config_b)
    assert connector_b.search("Remera") == []


def test_aislamiento_tool_buscar(db, tenant_a, tenant_b):
    """buscar_productos de tenant B no ve productos de tenant A."""
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    defn = _make_shopify_def(db)
    config_a = _make_config(db, tenant_a_obj, defn)
    config_b = _make_config(db, tenant_b_obj, defn)

    _seed_product(db, tenant_a_obj, config_a)

    result = buscar_productos(
        query="Remera",
        max_results=10,
        tenant_id=tenant_b_obj.id,
        config_id=config_b.id,
        db=db,
    )
    assert result["resultados"] == []


def test_aislamiento_configure_tenant_incorrecto(db, tenant_a, tenant_b):
    """ShopifyConnector lanza error si config no pertenece al tenant."""
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    defn = _make_shopify_def(db)
    config_a = _make_config(db, tenant_a_obj, defn)
    connector_a = ShopifyConnector(tenant_id=tenant_a_obj.id, config_id=config_a.id, db=db)
    connector_a.configure(_CREDS)

    # Tenant B intenta cargar credenciales de config de tenant A
    connector_impostor = ShopifyConnector(
        tenant_id=tenant_b_obj.id,
        config_id=config_a.id,
        db=db,
    )
    with pytest.raises(RuntimeError, match="tenant_id no coincide"):
        connector_impostor._load_credentials()


def test_aislamiento_consultar_stock(db, tenant_a, tenant_b):
    """consultar_stock_y_precio de tenant B no retorna producto de tenant A."""
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    defn = _make_shopify_def(db)
    config_a = _make_config(db, tenant_a_obj, defn)
    config_b = _make_config(db, tenant_b_obj, defn)

    _seed_product(db, tenant_a_obj, config_a)

    result = consultar_stock_y_precio(
        sku="SHOP-SHIRT-M",
        tenant_id=tenant_b_obj.id,
        config_id=config_b.id,
        db=db,
    )
    assert "error" in result
