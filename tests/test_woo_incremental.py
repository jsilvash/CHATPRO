"""Tests de Fase 6: Sync incremental WooCommerce + embeddings de productos.

Cobertura:
- verify_webhook(): firma HMAC válida, inválida, header ausente, base64 corrupto.
- webhook_handler(): product.created/updated/deleted, order.created/updated, topic desconocido.
- sync_incremental(): llama /wc/v3/products?modified_after, upserta productos, actualiza last_incremental_sync_at.
- embed_product task: genera y persiste embedding; skip si sin API key; skip si producto no existe.
- search() híbrida: keyword fallback sin embedding; combina semántico+keyword con RRF.
- Endpoint POST /webhooks/woo/{tenant_id}/{config_id}: 200 OK, 401 firma inválida, 404 config no encontrada.
- Aislamiento: tenant B no puede recibir eventos de tenant A.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product
from src.connectors.woocommerce.connector import WooCommerceConnector, _rrf_merge
from src.connectors.woocommerce.tasks import _build_product_text
from src.db.models import Tenant


# ── Helpers compartidos ───────────────────────────────────────────────────────


def _make_connector_def(db: Session) -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == "woocommerce").first()
    if existing:
        return existing
    defn = ConnectorDef(id=uuid.uuid4(), name="woocommerce", kind="ecommerce", version="1.0.0")
    db.add(defn)
    db.flush()
    return defn


def _make_config(db: Session, tenant: Tenant, secret: str = "secreto_test") -> ConnectorConfig:
    defn = _make_connector_def(db)
    from src.connectors.crypto import encrypt_credentials
    blob = encrypt_credentials({
        "site_url": "https://tienda.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
    })
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Tienda Test",
        status="connected",
        webhook_secret=secret,
        encrypted_credentials=blob,
    )
    db.add(config)
    db.flush()
    return config


def _make_product(db: Session, tenant: Tenant, config: ConnectorConfig, **kwargs) -> Product:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": tenant.id,
        "connector_config_id": config.id,
        "external_id": str(uuid.uuid4().int)[:8],
        "name": "Producto Test",
        "sku": "TEST-001",
    }
    defaults.update(kwargs)
    p = Product(**defaults)
    db.add(p)
    db.flush()
    return p


def _sign_payload(payload: bytes, secret: str) -> str:
    """Genera X-WC-Webhook-Signature válida para un payload dado."""
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return b64encode(digest).decode()


_FAKE_PRODUCT = {
    "id": 99,
    "sku": "REMERA-L",
    "name": "Remera de Algodón L",
    "short_description": "Remera cómoda",
    "description": "<p>Descripción larga</p>",
    "regular_price": "25.00",
    "sale_price": "20.00",
    "stock_quantity": 50,
    "stock_status": "in_stock",
    "permalink": "https://tienda.com/remera-l",
    "images": [],
    "categories": [{"name": "Ropa"}],
    "attributes": [],
    "variations": [],
}

_FAKE_ORDER = {
    "id": 777,
    "status": "processing",
    "total": "45.00",
    "currency": "USD",
    "billing": {"email": "cliente@ejemplo.com"},
    "line_items": [{"product_id": 99, "quantity": 2}],
}


# ── Tests verify_webhook ──────────────────────────────────────────────────────


def test_verify_webhook_firma_valida(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="mi_secreto")
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    payload = b'{"id": 1}'
    sig = _sign_payload(payload, "mi_secreto")
    result = connector.verify_webhook(payload, {"x-wc-webhook-signature": sig})

    assert result.valid is True


def test_verify_webhook_firma_invalida(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="mi_secreto")
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    payload = b'{"id": 1}'
    result = connector.verify_webhook(payload, {"x-wc-webhook-signature": "firma_falsa_AAAA=="})

    assert result.valid is False
    assert "inválida" in result.reason


def test_verify_webhook_header_ausente(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    result = connector.verify_webhook(b'{}', {})

    assert result.valid is False
    assert "ausente" in result.reason


def test_verify_webhook_base64_corrupto(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    result = connector.verify_webhook(b'{}', {"x-wc-webhook-signature": "!!!no_es_base64!!!"})

    assert result.valid is False


# ── Tests webhook_handler — productos ─────────────────────────────────────────


def test_webhook_handler_product_created(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch.object(connector, "_enqueue_embed"):
        connector.webhook_handler(
            _FAKE_PRODUCT,
            {"x-wc-webhook-topic": "product.created"},
        )

    product = (
        db.query(Product)
        .filter(
            Product.tenant_id == tenant.id,
            Product.external_id == "99",
        )
        .first()
    )
    assert product is not None
    assert product.name == "Remera de Algodón L"
    assert product.sku == "REMERA-L"


def test_webhook_handler_product_updated_actualiza_producto(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    existing = _make_product(
        db, tenant, config, external_id="99", name="Nombre Viejo", sku="REMERA-L"
    )
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch.object(connector, "_enqueue_embed"):
        connector.webhook_handler(
            _FAKE_PRODUCT,
            {"x-wc-webhook-topic": "product.updated"},
        )

    db.refresh(existing)
    assert existing.name == "Remera de Algodón L"
    assert float(existing.price_regular) == 25.0


def test_webhook_handler_product_deleted_soft_delete(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    product = _make_product(db, tenant, config, external_id="99")
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    connector.webhook_handler({"id": 99}, {"x-wc-webhook-topic": "product.deleted"})

    db.refresh(product)
    assert product.deleted_at is not None


def test_webhook_handler_product_deleted_no_existente_no_falla(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    # No debe lanzar excepción aunque el producto no exista
    connector.webhook_handler({"id": 9999}, {"x-wc-webhook-topic": "product.deleted"})


def test_webhook_handler_product_encola_embed(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch.object(connector, "_enqueue_embed") as mock_enqueue:
        connector.webhook_handler(
            _FAKE_PRODUCT,
            {"x-wc-webhook-topic": "product.created"},
        )
        mock_enqueue.assert_called_once()


# ── Tests webhook_handler — órdenes ──────────────────────────────────────────


def test_webhook_handler_order_created(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    connector.webhook_handler(_FAKE_ORDER, {"x-wc-webhook-topic": "order.created"})

    order = (
        db.query(Order)
        .filter(Order.tenant_id == tenant.id, Order.external_id == "777")
        .first()
    )
    assert order is not None
    assert order.status == "processing"
    assert order.customer_email == "cliente@ejemplo.com"
    assert float(order.total) == 45.0


def test_webhook_handler_order_updated(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)

    # Crear orden preexistente
    order = Order(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="777",
        status="pending",
    )
    db.add(order)
    db.flush()

    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    updated_order = {**_FAKE_ORDER, "status": "completed"}
    connector.webhook_handler(updated_order, {"x-wc-webhook-topic": "order.updated"})

    db.refresh(order)
    assert order.status == "completed"


def test_webhook_handler_topic_desconocido_no_falla(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    # No debe lanzar excepción
    connector.webhook_handler({}, {"x-wc-webhook-topic": "coupon.created"})


# ── Tests sync_incremental ────────────────────────────────────────────────────


def _build_woo_resp(products: list, total_pages: int = 1):
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = products
    mock.headers = {"X-WP-TotalPages": str(total_pages)}
    mock.raise_for_status = MagicMock()
    return mock


def test_sync_incremental_crea_productos(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_resp([_FAKE_PRODUCT]),
            _build_woo_resp([]),
        ]
        mock_cls.return_value = mock_client

        result = connector.sync_incremental(since)

    assert result.items_created == 1
    assert result.errors == []
    assert config.last_incremental_sync_at is not None


def test_sync_incremental_usa_modified_after(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    since = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)

    calls_params = []

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        def capture_get(url, params=None, **kwargs):
            calls_params.append(params or {})
            return _build_woo_resp([])

        mock_client.get.side_effect = capture_get
        mock_cls.return_value = mock_client

        connector.sync_incremental(since)

    assert calls_params, "No se llamó a client.get"
    assert calls_params[0].get("modified_after") == "2026-03-15T10:30:00"


def test_sync_incremental_actualiza_producto_existente(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    existing = _make_product(db, tenant, config, external_id="99", name="Viejo")
    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_resp([_FAKE_PRODUCT]),
            _build_woo_resp([]),
        ]
        mock_cls.return_value = mock_client

        result = connector.sync_incremental(datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result.items_updated == 1
    db.refresh(existing)
    assert existing.name == "Remera de Algodón L"


# ── Tests embed_product task ──────────────────────────────────────────────────


def test_embed_product_genera_y_persiste_embedding():
    fake_product = MagicMock()
    fake_product.name = "Remera Test"
    fake_product.description_short = "Algodón"
    fake_product.description_long = ""
    fake_product.sku = "T001"
    fake_product.categories = []
    fake_product.attributes = {}

    mock_db = MagicMock()
    mock_db.get.return_value = fake_product

    fake_vector = [0.1] * 1024

    # Los módulos son importados a nivel de módulo en tasks.py → patchear ahí mismo.
    with patch("src.connectors.woocommerce.tasks.embed_texts", return_value=[fake_vector]) as mock_embed, \
         patch("src.connectors.woocommerce.tasks.get_db_session") as mock_ctx, \
         patch("src.connectors.woocommerce.tasks.get_settings") as mock_settings:

        mock_settings.return_value.voyage_api_key = "vk_test_key"
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from src.connectors.woocommerce.tasks import embed_product
        result = embed_product.run(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["ok"] is True
    mock_embed.assert_called_once()
    assert fake_product.embedding == fake_vector
    mock_db.commit.assert_called_once()


def test_embed_product_skip_sin_api_key():
    with patch("src.connectors.woocommerce.tasks.get_settings") as mock_settings:
        mock_settings.return_value.voyage_api_key = ""

        from src.connectors.woocommerce.tasks import embed_product
        result = embed_product.run(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["ok"] is False
    assert result["reason"] == "sin_api_key"


def test_embed_product_skip_producto_no_existe():
    mock_db = MagicMock()
    mock_db.get.return_value = None

    with patch("src.connectors.woocommerce.tasks.get_settings") as mock_settings, \
         patch("src.connectors.woocommerce.tasks.get_db_session") as mock_ctx:

        mock_settings.return_value.voyage_api_key = "vk_test"
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from src.connectors.woocommerce.tasks import embed_product
        result = embed_product.run(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))

    assert result["ok"] is False
    assert result["reason"] == "producto_no_encontrado"


def test_build_product_text_construye_texto(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    product = _make_product(
        db, tenant, config,
        name="Zapatilla",
        description_short="Cómoda",
        description_long="Muy cómoda",
        sku="ZAP-001",
        categories=["Calzado"],
    )

    text_out = _build_product_text(product)

    assert "Zapatilla" in text_out
    assert "Cómoda" in text_out
    assert "ZAP-001" in text_out
    assert "Calzado" in text_out


# ── Tests search() híbrida ────────────────────────────────────────────────────


def test_search_keyword_sin_embedding(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    _make_product(db, tenant, config, name="Remera Azul", description_short="Remera de algodón")
    _make_product(db, tenant, config, name="Pantalón Negro")

    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch("src.connectors.woocommerce.connector.get_settings") as mock_settings:
        mock_settings.return_value.voyage_api_key = ""
        results = connector.search("Remera")

    assert len(results) >= 1
    titles = [r.title for r in results]
    assert any("Remera" in t for t in titles)


def test_search_semantica_con_embedding(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    product = _make_product(
        db, tenant, config,
        name="Remera Azul",
        description_short="Algodón",
        embedding=[0.9] + [0.0] * 1023,
    )
    _make_product(db, tenant, config, name="Pantalón", embedding=[0.1] + [0.0] * 1023)

    fake_query_vector = [0.9] + [0.0] * 1023

    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch("src.connectors.woocommerce.connector.get_settings") as mock_settings, \
         patch("src.connectors.woocommerce.connector.embed_texts", return_value=[fake_query_vector]) as _mock_embed:
        mock_settings.return_value.voyage_api_key = "vk_test"
        results = connector.search("tela suave", top_k=5)

    assert len(results) >= 1
    assert any(r.id == str(product.id) for r in results)


def test_search_excluye_productos_eliminados(db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant)
    _make_product(db, tenant, config, name="Remera Borrada",
                  deleted_at=datetime.now(timezone.utc))
    _make_product(db, tenant, config, name="Remera Activa")

    connector = WooCommerceConnector(tenant.id, config.id, db=db)

    with patch("src.connectors.woocommerce.connector.get_settings") as mock_settings:
        mock_settings.return_value.voyage_api_key = ""
        results = connector.search("Remera")

    titles = [r.title for r in results]
    assert "Remera Borrada" not in titles
    assert "Remera Activa" in titles


def test_rrf_merge_combina_listas():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()

    keyword = [(a, 1.0), (b, 1.0)]
    semantic = [(b, 0.95), (c, 0.80)]

    merged = _rrf_merge(keyword, semantic, top_k=3)
    ids = [pid for pid, _ in merged]

    assert b in ids  # b aparece en ambas listas → score más alto


def test_rrf_merge_top_k(db: Session):
    items = [(uuid.uuid4(), float(i)) for i in range(10)]
    merged = _rrf_merge(items, [], top_k=3)
    assert len(merged) == 3


# ── Tests endpoint webhook HTTP ───────────────────────────────────────────────


def _make_woo_request(client, tenant_id, config_id, payload: dict, secret: str, topic: str = "product.updated"):
    body = json.dumps(payload).encode()
    sig = _sign_payload(body, secret)
    return client.post(
        f"/webhooks/woo/{tenant_id}/{config_id}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": sig,
            "X-WC-Webhook-Topic": topic,
        },
    )


def test_endpoint_webhook_200_ok(client_a, db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="secreto_a")

    with patch("src.connectors.woocommerce.webhook.WooCommerceConnector") as MockConnector:
        mock_conn = MagicMock()
        mock_conn.verify_webhook.return_value = MagicMock(valid=True)
        mock_conn.webhook_handler.return_value = None
        MockConnector.return_value = mock_conn

        body = json.dumps({"id": 42}).encode()
        sig = _sign_payload(body, "secreto_a")

        r = client_a.post(
            f"/webhooks/woo/{tenant.id}/{config.id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": "product.updated",
            },
        )

    assert r.status_code == 200
    assert r.json()["received"] is True


def test_endpoint_webhook_401_firma_invalida(client_a, db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="secreto_real")

    body = json.dumps({"id": 42}).encode()
    r = client_a.post(
        f"/webhooks/woo/{tenant.id}/{config.id}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": "ZmlybWFfZmFsc2E=",
            "X-WC-Webhook-Topic": "product.updated",
        },
    )

    assert r.status_code == 401


def test_endpoint_webhook_404_config_no_existe(client_a, db: Session, tenant_a):
    tenant, _ = tenant_a
    body = json.dumps({"id": 1}).encode()
    fake_config_id = uuid.uuid4()

    r = client_a.post(
        f"/webhooks/woo/{tenant.id}/{fake_config_id}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": "ZmlybWFfZmFsc2E=",
        },
    )

    assert r.status_code == 404


def test_endpoint_webhook_product_created_end_to_end(client_a, db: Session, tenant_a):
    """Test E2E: el endpoint persiste el producto sin mock del conector."""
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="secreto_e2e")

    payload = {**_FAKE_PRODUCT, "id": 200}
    body = json.dumps(payload).encode()
    sig = _sign_payload(body, "secreto_e2e")

    with patch("src.connectors.woocommerce.connector.WooCommerceConnector._enqueue_embed"):
        r = client_a.post(
            f"/webhooks/woo/{tenant.id}/{config.id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": "product.created",
            },
        )

    assert r.status_code == 200

    product = (
        db.query(Product)
        .filter(Product.tenant_id == tenant.id, Product.external_id == "200")
        .first()
    )
    assert product is not None
    assert product.name == "Remera de Algodón L"


# ── Tests aislamiento ─────────────────────────────────────────────────────────


def test_aislamiento_webhook_tenant_b_no_puede_usar_config_de_a(client_a, client_b, db: Session, tenant_a):
    tenant, _ = tenant_a
    config = _make_config(db, tenant, secret="secreto_a")

    body = json.dumps({"id": 1}).encode()
    sig = _sign_payload(body, "secreto_a")

    # Tenant B intenta enviar webhook a config de tenant A con un tenant_id distinto
    fake_tenant_b_id = uuid.uuid4()
    r = client_b.post(
        f"/webhooks/woo/{fake_tenant_b_id}/{config.id}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": sig,
            "X-WC-Webhook-Topic": "product.created",
        },
    )

    assert r.status_code == 404


def test_aislamiento_search_tenant_b_no_ve_productos_de_a(db: Session, tenant_a, tenant_b):
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    config_a = _make_config(db, tenant_a_obj)
    _make_product(db, tenant_a_obj, config_a, name="Remera Secreta de A")

    config_b = _make_config(db, tenant_b_obj)
    connector_b = WooCommerceConnector(tenant_b_obj.id, config_b.id, db=db)

    with patch("src.connectors.woocommerce.connector.get_settings") as mock_settings:
        mock_settings.return_value.voyage_api_key = ""
        results = connector_b.search("Remera")

    titles = [r.title for r in results]
    assert "Remera Secreta de A" not in titles


def test_aislamiento_orders_tenant_b_no_ve_ordenes_de_a(db: Session, tenant_a, tenant_b):
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    config_a = _make_config(db, tenant_a_obj)
    connector_a = WooCommerceConnector(tenant_a_obj.id, config_a.id, db=db)
    connector_a.webhook_handler(_FAKE_ORDER, {"x-wc-webhook-topic": "order.created"})

    ordenes_b = (
        db.query(Order)
        .filter(Order.tenant_id == tenant_b_obj.id)
        .count()
    )
    assert ordenes_b == 0
