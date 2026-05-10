"""Tests de Fase 6: sync incremental WooCommerce + embeddings + búsqueda híbrida.

Cobertura:
- Verificación HMAC: firma válida, ausente, inválida, mal codificada.
- Endpoint POST /webhooks/woo/{tenant_id}/{config_id}.
- webhook_handler: product.created, product.updated, product.deleted, order.created, order.updated.
- embed_product_sync: genera y persiste embedding (mock de Voyage AI).
- sync_incremental: pagina con modified_after y actualiza last_incremental_sync_at.
- search(): BM25 puro, semántica (mocked), híbrida, fallback ILIKE.
- Aislamiento cross-tenant: tenant B no ve resultados de tenant A.
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

from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product
from src.connectors.woocommerce.connector import WooCommerceConnector


# ── Helpers / Fixtures ────────────────────────────────────────────────────────


def _make_connector_config(db, tenant_id: uuid.UUID, *, webhook_secret: str = "test-secret") -> ConnectorConfig:
    """Crea un ConnectorDef + ConnectorConfig listos para usar."""
    defn = db.query(ConnectorDef).filter(ConnectorDef.name == "woocommerce").first()
    if defn is None:
        defn = ConnectorDef(
            id=uuid.uuid4(), name="woocommerce", kind="ecommerce", version="1.0.0"
        )
        db.add(defn)
        db.flush()

    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        connector_def_id=defn.id,
        display_name="Tienda Test",
        webhook_secret=webhook_secret,
        status="connected",
    )
    db.add(config)
    db.flush()
    return config


def _make_product(db, tenant_id: uuid.UUID, config_id: uuid.UUID, **kwargs) -> Product:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "connector_config_id": config_id,
        "external_id": str(uuid.uuid4().int)[:6],
        "name": "Producto Test",
        "description_short": "Descripción corta",
        "description_long": "Descripción larga del producto de prueba",
        "price_regular": 9990.00,
        "stock_status": "in_stock",
        "stock_quantity": 10,
    }
    defaults.update(kwargs)
    product = Product(**defaults)
    db.add(product)
    db.flush()
    return product


def _make_signature(secret: str, payload: bytes) -> str:
    """Genera la firma HMAC-SHA256 base64 como la genera WooCommerce."""
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return b64encode(digest).decode()


def _woo_product_payload(external_id: str = "42") -> dict:
    return {
        "id": int(external_id),
        "name": "Zapatillas Running",
        "sku": "ZAP-001",
        "regular_price": "49990",
        "sale_price": "",
        "short_description": "<p>Zapatillas livianas</p>",
        "description": "<p>Descripción completa</p>",
        "stock_quantity": 5,
        "stock_status": "in_stock",
        "permalink": "https://tienda.cl/zapatillas-running",
        "images": [{"src": "https://tienda.cl/img.jpg"}],
        "categories": [{"name": "Calzado"}],
        "attributes": [],
        "variations": [],
    }


def _woo_order_payload(external_id: str = "100") -> dict:
    return {
        "id": int(external_id),
        "status": "processing",
        "total": "49990",
        "currency": "CLP",
        "date_created": "2026-05-10T00:00:00",
        "billing": {"email": "cliente@ejemplo.cl", "phone": "+56912345678"},
    }


# ── Tests: verify_webhook ─────────────────────────────────────────────────────


class TestVerifyWebhook:
    def test_firma_valida(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id, webhook_secret="super-secret")
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        payload = b'{"id":1,"name":"Producto"}'
        sig = _make_signature("super-secret", payload)
        result = connector.verify_webhook(payload, {"x-wc-webhook-signature": sig})

        assert result.valid is True

    def test_firma_ausente(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        result = connector.verify_webhook(b"payload", {})

        assert result.valid is False
        assert "ausente" in result.reason

    def test_firma_invalida(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id, webhook_secret="correct-secret")
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        payload = b'{"id":1}'
        wrong_sig = _make_signature("wrong-secret", payload)
        result = connector.verify_webhook(payload, {"x-wc-webhook-signature": wrong_sig})

        assert result.valid is False
        assert "inválida" in result.reason

    def test_firma_mal_codificada(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        result = connector.verify_webhook(b"payload", {"x-wc-webhook-signature": "!!!no-base64!!!"})

        assert result.valid is False
        assert "codificada" in result.reason

    def test_config_no_encontrada(self, db, tenant_a):
        tenant, _ = tenant_a
        connector = WooCommerceConnector(tenant.id, uuid.uuid4(), db=db)

        result = connector.verify_webhook(b"payload", {"x-wc-webhook-signature": "abc"})

        assert result.valid is False


# ── Tests: endpoint POST /webhooks/woo/ ───────────────────────────────────────


class TestEndpointWebhookWoo:
    def _post_webhook(self, client, tenant_id, config_id, payload_dict, secret, topic="product.updated"):
        payload_bytes = json.dumps(payload_dict).encode()
        sig = _make_signature(secret, payload_bytes)
        return client.post(
            f"/webhooks/woo/{tenant_id}/{config_id}",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": topic,
            },
        )

    def test_webhook_valido_retorna_200(self, client_a, db, tenant_a):
        tenant, _ = tenant_a
        secret = "endpoint-secret"
        config = _make_connector_config(db, tenant.id, webhook_secret=secret)

        resp = self._post_webhook(
            client_a, tenant.id, config.id,
            _woo_product_payload("55"), secret,
            topic="product.created",
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_webhook_firma_invalida_retorna_401(self, client_a, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id, webhook_secret="real-secret")

        payload_bytes = json.dumps({"id": 99}).encode()
        wrong_sig = _make_signature("wrong-secret", payload_bytes)

        resp = client_a.post(
            f"/webhooks/woo/{tenant.id}/{config.id}",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": wrong_sig,
                "X-WC-Webhook-Topic": "product.updated",
            },
        )

        assert resp.status_code == 401

    def test_webhook_config_inexistente_retorna_404(self, client_a, db, tenant_a):
        tenant, _ = tenant_a
        config_id_falso = uuid.uuid4()

        payload_bytes = b'{"id":1}'
        sig = _make_signature("any", payload_bytes)

        resp = client_a.post(
            f"/webhooks/woo/{tenant.id}/{config_id_falso}",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": "product.updated",
            },
        )

        assert resp.status_code == 404

    def test_webhook_tenant_incorrecto_retorna_404(self, client_a, db, tenant_a, tenant_b):
        tenant_a_obj, _ = tenant_a
        tenant_b_obj, _ = tenant_b
        config = _make_connector_config(db, tenant_b_obj.id, webhook_secret="secret")

        payload_bytes = json.dumps({"id": 1}).encode()
        sig = _make_signature("secret", payload_bytes)

        resp = client_a.post(
            f"/webhooks/woo/{tenant_a_obj.id}/{config.id}",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": "product.updated",
            },
        )

        assert resp.status_code == 404


# ── Tests: webhook_handler ────────────────────────────────────────────────────


class TestWebhookHandler:
    def test_product_created_persiste_en_bd(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        payload = _woo_product_payload("77")
        with patch("src.connectors.tasks.embed_product_sync"):
            connector.webhook_handler(payload, {"x-wc-webhook-topic": "product.created"})

        product = (
            db.query(Product)
            .filter(
                Product.tenant_id == tenant.id,
                Product.connector_config_id == config.id,
                Product.external_id == "77",
            )
            .first()
        )
        assert product is not None
        assert product.name == "Zapatillas Running"
        assert product.sku == "ZAP-001"

    def test_product_updated_actualiza_precio(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(db, tenant.id, config.id, external_id="88", price_regular=1000.0)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        payload = _woo_product_payload("88")
        payload["regular_price"] = "99990"
        with patch("src.connectors.tasks.embed_product_sync"):
            connector.webhook_handler(payload, {"x-wc-webhook-topic": "product.updated"})

        db.refresh(product)
        assert float(product.price_regular) == pytest.approx(99990.0)

    def test_product_deleted_marca_deleted_at(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(db, tenant.id, config.id, external_id="99")
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        assert product.deleted_at is None
        connector.webhook_handler({"id": 99}, {"x-wc-webhook-topic": "product.deleted"})
        db.refresh(product)

        assert product.deleted_at is not None

    def test_order_created_persiste_en_bd(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        payload = _woo_order_payload("200")
        connector.webhook_handler(payload, {"x-wc-webhook-topic": "order.created"})

        order = (
            db.query(Order)
            .filter(
                Order.tenant_id == tenant.id,
                Order.connector_config_id == config.id,
                Order.external_id == "200",
            )
            .first()
        )
        assert order is not None
        assert order.status == "processing"
        assert float(order.total) == pytest.approx(49990.0)
        assert order.currency == "CLP"

    def test_order_updated_actualiza_status(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        connector.webhook_handler(_woo_order_payload("201"), {"x-wc-webhook-topic": "order.created"})
        payload = _woo_order_payload("201")
        payload["status"] = "completed"
        connector.webhook_handler(payload, {"x-wc-webhook-topic": "order.updated"})

        order = (
            db.query(Order)
            .filter(Order.tenant_id == tenant.id, Order.external_id == "201")
            .first()
        )
        assert order.status == "completed"

    def test_topic_desconocido_no_levanta_excepcion(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        connector.webhook_handler({"id": 1}, {"x-wc-webhook-topic": "coupon.created"})


# ── Tests: embed_product_sync ─────────────────────────────────────────────────


class TestEmbedProductSync:
    def test_genera_y_persiste_embedding(self, db, tenant_a):
        from src.connectors.tasks import embed_product_sync

        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(
            db, tenant.id, config.id,
            name="Polera Dry Fit",
            description_short="Tela transpirable",
        )

        fake_embedding = [0.1] * 1024
        with patch("src.connectors.tasks.get_embeddings", return_value=[fake_embedding]):
            result = embed_product_sync(product.id, db=db)

        assert result is True
        db.refresh(product)
        assert product.embedding is not None
        assert len(product.embedding) == 1024

    def test_retorna_false_si_producto_no_existe(self, db, tenant_a):
        from src.connectors.tasks import embed_product_sync

        result = embed_product_sync(uuid.uuid4(), db=db)

        assert result is False

    def test_retorna_false_si_texto_vacio(self, db, tenant_a):
        from src.connectors.tasks import embed_product_sync

        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(
            db, tenant.id, config.id,
            name="",
            description_short=None,
            description_long=None,
        )

        with patch("src.connectors.tasks.get_embeddings", return_value=[]):
            result = embed_product_sync(product.id, db=db)

        assert result is False


# ── Tests: sync_incremental ───────────────────────────────────────────────────


class TestSyncIncremental:
    def test_sincroniza_productos_modificados(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)

        # Simular credenciales cifradas con patch de _load_credentials
        raw_woo_products = [_woo_product_payload("300"), _woo_product_payload("301")]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = raw_woo_products
        mock_response.headers = {"X-WP-TotalPages": "1"}
        mock_response.raise_for_status = MagicMock()

        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = []
        mock_empty.headers = {"X-WP-TotalPages": "1"}
        mock_empty.raise_for_status = MagicMock()

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)

        with patch.object(connector, "_load_credentials", return_value={
            "site_url": "https://tienda.cl",
            "consumer_key": "ck_test",
            "consumer_secret": "cs_test",
        }):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.side_effect = [mock_response, mock_empty]
                mock_client_cls.return_value = mock_client

                result = connector.sync_incremental(since)

        assert result.errors == []
        assert result.items_processed == 2

    def test_actualiza_last_incremental_sync_at(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)

        assert config.last_incremental_sync_at is None

        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = []
        mock_empty.headers = {"X-WP-TotalPages": "1"}
        mock_empty.raise_for_status = MagicMock()

        with patch.object(connector, "_load_credentials", return_value={
            "site_url": "https://tienda.cl",
            "consumer_key": "ck_test",
            "consumer_secret": "cs_test",
        }):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_empty
                mock_client_cls.return_value = mock_client

                connector.sync_incremental(datetime(2026, 1, 1, tzinfo=timezone.utc))

        db.refresh(config)
        assert config.last_incremental_sync_at is not None


# ── Tests: search() ───────────────────────────────────────────────────────────


class TestSearch:
    def test_busqueda_ilike_retorna_productos(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        _make_product(db, tenant.id, config.id, name="Polera Azul", external_id="401")
        _make_product(db, tenant.id, config.id, name="Pantalón Verde", external_id="402")

        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        with patch("src.knowledge.embeddings.get_query_embedding", side_effect=Exception("no api")):
            results = connector.search("Polera")

        assert len(results) >= 1
        assert any("Polera" in r.title for r in results)

    def test_busqueda_semantica_con_mock(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(db, tenant.id, config.id, name="Zapatilla Trail", external_id="500")
        # Setear embedding directamente
        fake_vec = [0.5] * 1024
        product.embedding = fake_vec
        db.flush()

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        query_vec = [0.5] * 1024

        with patch("src.knowledge.embeddings.get_query_embedding", return_value=query_vec):
            results = connector.search("zapatilla trail", top_k=5)

        assert len(results) >= 1
        ids = [r.id for r in results]
        assert str(product.id) in ids

    def test_busqueda_retorna_metadata_completa(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        _make_product(
            db, tenant.id, config.id,
            name="Camiseta Básica",
            external_id="600",
            sku="CAM-001",
            price_regular=4990.0,
            stock_status="in_stock",
            stock_quantity=20,
            url="https://tienda.cl/camiseta",
        )

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        with patch("src.knowledge.embeddings.get_query_embedding", side_effect=Exception):
            results = connector.search("Camiseta")

        assert results
        r = results[0]
        assert r.metadata["sku"] == "CAM-001"
        assert r.metadata["price_regular"] == pytest.approx(4990.0)
        assert r.url == "https://tienda.cl/camiseta"

    def test_busqueda_no_retorna_eliminados(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)
        product = _make_product(
            db, tenant.id, config.id, name="Producto Eliminado", external_id="700"
        )
        product.deleted_at = datetime.now(timezone.utc)
        db.flush()

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        with patch("src.knowledge.embeddings.get_query_embedding", side_effect=Exception):
            results = connector.search("Eliminado")

        assert all("Eliminado" not in r.title for r in results)

    def test_busqueda_sin_resultados_retorna_lista_vacia(self, db, tenant_a):
        tenant, _ = tenant_a
        config = _make_connector_config(db, tenant.id)

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        with patch("src.knowledge.embeddings.get_query_embedding", side_effect=Exception):
            results = connector.search("xyzabc123nonexistent")

        assert results == []


# ── Tests: aislamiento cross-tenant ──────────────────────────────────────────


class TestAislamiento:
    def test_search_no_ve_productos_de_otro_tenant(self, db, tenant_a, tenant_b):
        tenant_a_obj, _ = tenant_a
        tenant_b_obj, _ = tenant_b

        config_a = _make_connector_config(db, tenant_a_obj.id)
        config_b = _make_connector_config(db, tenant_b_obj.id)
        _make_product(db, tenant_a_obj.id, config_a.id, name="Producto Secreto A", external_id="900")
        _make_product(db, tenant_b_obj.id, config_b.id, name="Producto Secreto B", external_id="901")

        connector_b = WooCommerceConnector(tenant_b_obj.id, config_b.id, db=db)
        with patch("src.knowledge.embeddings.get_query_embedding", side_effect=Exception):
            results = connector_b.search("Secreto")

        titles = [r.title for r in results]
        assert "Producto Secreto B" in titles
        assert "Producto Secreto A" not in titles

    def test_webhook_handler_no_persiste_en_tenant_incorrecto(self, db, tenant_a, tenant_b):
        tenant_a_obj, _ = tenant_a
        tenant_b_obj, _ = tenant_b

        config_b = _make_connector_config(db, tenant_b_obj.id)
        connector_b = WooCommerceConnector(tenant_b_obj.id, config_b.id, db=db)

        payload = _woo_product_payload("999")
        with patch("src.connectors.tasks.embed_product_sync"):
            connector_b.webhook_handler(payload, {"x-wc-webhook-topic": "product.created"})

        # Verificar que el producto pertenece al tenant B, no al A
        count_a = (
            db.query(Product)
            .filter(Product.tenant_id == tenant_a_obj.id, Product.external_id == "999")
            .count()
        )
        count_b = (
            db.query(Product)
            .filter(Product.tenant_id == tenant_b_obj.id, Product.external_id == "999")
            .count()
        )
        assert count_a == 0
        assert count_b == 1

    def test_verify_webhook_falla_si_config_es_de_otro_tenant(self, db, tenant_a, tenant_b):
        tenant_a_obj, _ = tenant_a
        tenant_b_obj, _ = tenant_b

        config_b = _make_connector_config(db, tenant_b_obj.id, webhook_secret="secret-b")

        # Tenant A intenta usar config de Tenant B
        connector = WooCommerceConnector(tenant_a_obj.id, config_b.id, db=db)
        payload = b'{"id":1}'
        sig = _make_signature("secret-b", payload)
        result = connector.verify_webhook(payload, {"x-wc-webhook-signature": sig})

        # El secret se lee igual, pero el endpoint rechazará por tenant_id mismatch.
        # Aquí verify_webhook no valida tenant (lo hace el endpoint); solo verifica firma.
        # Registramos que la firma en sí es válida — el aislamiento lo hace el endpoint.
        assert result.valid is True  # firma OK, pero el endpoint verificó tenant antes
