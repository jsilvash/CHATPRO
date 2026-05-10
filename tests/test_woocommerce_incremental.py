"""Tests Fase 6: sync incremental, webhooks, embeddings, búsqueda híbrida, aislamiento."""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.woocommerce.connector import (
    WooCommerceConnector,
    _product_text,
    _to_decimal,
)

# ------------------------------------------------------------------ fixtures

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
CONFIG_ID = uuid.uuid4()
WEBHOOK_SECRET = "s3cr3t-wh-test"

_CREDS = {
    "site_url": "https://shop.example.com",
    "consumer_key": "ck_test",
    "consumer_secret": "cs_test",
}


def _mock_cfg(tenant_id=TENANT_A, secret=WEBHOOK_SECRET) -> MagicMock:
    cfg = MagicMock()
    cfg.tenant_id = tenant_id
    cfg.webhook_secret = secret
    cfg.encrypted_credentials = json.dumps(_CREDS).encode()
    cfg.last_incremental_sync_at = None
    cfg.last_full_sync_at = None
    return cfg


def _mock_session(tenant_id=TENANT_A) -> MagicMock:
    session = MagicMock()
    session.get.return_value = _mock_cfg(tenant_id)
    return session


def _make_connector(session=None, voyage_key="") -> WooCommerceConnector:
    if session is None:
        session = _mock_session()
    return WooCommerceConnector(TENANT_A, CONFIG_ID, session, voyage_key)


def _wc_product(pid: int = 1, name: str = "Producto Test") -> dict:
    return {
        "id": pid,
        "name": name,
        "sku": f"SKU-{pid:04d}",
        "short_description": "Descripción corta",
        "description": "Descripción larga del producto de prueba",
        "regular_price": "9990",
        "sale_price": "7990",
        "stock_status": "instock",
        "stock_quantity": 50,
        "permalink": f"https://shop.example.com/producto-{pid}",
        "categories": [{"id": 1, "name": "Ropa"}],
        "attributes": [{"name": "Color", "options": ["Rojo", "Azul"]}],
        "images": [],
        "variations": [],
    }


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return base64.b64encode(
        hmac.digest(secret.encode(), payload, hashlib.sha256)
    ).decode()


def _mock_http_response(data, status=200, total_pages=1) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.headers = {"X-WP-TotalPages": str(total_pages)}
    return resp


# ================================================================ sync_incremental

class TestSyncIncremental:
    def test_usa_parametro_modified_after(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session)
        since = datetime(2026, 5, 1, tzinfo=timezone.utc)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([_wc_product(1)])

            connector.sync_incremental(since)

        call_params = client_ctx.get.call_args_list[0][1]["params"]
        assert call_params["modified_after"] == since.isoformat()

    def test_pagina_hasta_total_pages(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session)
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)

        resp1 = _mock_http_response([_wc_product(1)], total_pages=2)
        resp2 = _mock_http_response([_wc_product(2)], total_pages=2)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.side_effect = [resp1, resp2]

            result = connector.sync_incremental(since)

        assert result.items_processed == 2
        assert client_ctx.get.call_count == 2

    def test_crea_producto_nuevo(self):
        session = _mock_session()
        session.scalar.return_value = None  # producto no existe
        connector = _make_connector(session)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([_wc_product(99)])

            result = connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert result.items_created == 1
        assert result.items_updated == 0
        session.add.assert_called_once()

    def test_actualiza_producto_existente(self):
        session = _mock_session()
        session.scalar.return_value = MagicMock()  # producto existe
        connector = _make_connector(session)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([_wc_product(1)])

            result = connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert result.items_updated == 1
        assert result.items_created == 0
        session.add.assert_not_called()

    def test_registra_error_http(self):
        session = _mock_session()
        connector = _make_connector(session)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([], status=401)

            result = connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert len(result.errors) == 1
        assert "401" in result.errors[0]

    def test_actualiza_last_incremental_sync_at(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session)
        cfg_mock = session.get.return_value

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([])

            connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert cfg_mock.last_incremental_sync_at is not None

    def test_cursor_en_resultado(self):
        session = _mock_session()
        connector = _make_connector(session)

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([])

            result = connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert result.cursor is not None

    def test_embeddings_generados_al_upsert(self):
        """Al hacer upsert, el conector llama a Voyage AI si hay voyage_api_key."""
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient, patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 1024]
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([_wc_product(7)])

            connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        mock_embed.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.embedding == [0.1] * 1024

    def test_sin_voyage_key_no_genera_embedding(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session, voyage_key="")  # sin key

        with patch(
            "src.connectors.woocommerce.connector.httpx.Client"
        ) as MockClient, patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_http_response([_wc_product(8)])

            connector.sync_incremental(datetime(2026, 5, 1, tzinfo=timezone.utc))

        mock_embed.assert_not_called()


# ================================================================ verify_webhook

class TestVerifyWebhook:
    def test_firma_valida(self):
        connector = _make_connector()
        payload = b'{"id":1,"name":"Test"}'
        sig = _sign(payload)
        result = connector.verify_webhook(payload, {"X-WC-Webhook-Signature": sig})
        assert result.valid is True
        assert result.reason is None

    def test_firma_invalida(self):
        connector = _make_connector()
        payload = b'{"id":1,"name":"Test"}'
        result = connector.verify_webhook(payload, {"X-WC-Webhook-Signature": "bad-sig"})
        assert result.valid is False
        assert result.reason is not None

    def test_cabecera_ausente(self):
        connector = _make_connector()
        result = connector.verify_webhook(b'{"id":1}', {})
        assert result.valid is False
        assert "Falta" in result.reason

    def test_cabecera_minusculas_aceptada(self):
        connector = _make_connector()
        payload = b'{"id":2}'
        sig = _sign(payload)
        result = connector.verify_webhook(payload, {"x-wc-webhook-signature": sig})
        assert result.valid is True

    def test_payload_alterado_rechazado(self):
        connector = _make_connector()
        original = b'{"id":1,"price":"100"}'
        sig = _sign(original)
        tampered = b'{"id":1,"price":"0"}'
        result = connector.verify_webhook(tampered, {"X-WC-Webhook-Signature": sig})
        assert result.valid is False

    def test_secreto_distinto_rechazado(self):
        connector = _make_connector()
        payload = b'{"id":1}'
        sig = _sign(payload, secret="otro-secreto")
        result = connector.verify_webhook(payload, {"X-WC-Webhook-Signature": sig})
        assert result.valid is False


# ================================================================ webhook_handler

class TestWebhookHandler:
    def test_product_created_inserta(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session)

        connector.webhook_handler(
            _wc_product(pid=99, name="Nuevo Producto"),
            {"X-WC-Webhook-Topic": "product.created"},
        )

        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.name == "Nuevo Producto"
        assert str(added.tenant_id) == str(TENANT_A)

    def test_product_updated_actualiza(self):
        session = _mock_session()
        existing = MagicMock()
        session.scalar.return_value = existing
        connector = _make_connector(session)

        connector.webhook_handler(
            _wc_product(pid=10, name="Actualizado"),
            {"X-WC-Webhook-Topic": "product.updated"},
        )

        assert existing.name == "Actualizado"
        session.add.assert_not_called()

    def test_product_restored_actualiza(self):
        session = _mock_session()
        existing = MagicMock()
        existing.deleted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.scalar.return_value = existing
        connector = _make_connector(session)

        connector.webhook_handler(
            _wc_product(pid=5),
            {"X-WC-Webhook-Topic": "product.restored"},
        )

        assert existing.deleted_at is None

    def test_product_deleted_soft_delete(self):
        session = _mock_session()
        connector = _make_connector(session)

        connector.webhook_handler(
            {"id": 42},
            {"X-WC-Webhook-Topic": "product.deleted"},
        )

        session.execute.assert_called_once()

    def test_topic_desconocido_es_noop(self):
        session = _mock_session()
        connector = _make_connector(session)

        connector.webhook_handler(
            {"id": 1},
            {"X-WC-Webhook-Topic": "coupon.created"},
        )

        session.add.assert_not_called()
        session.execute.assert_not_called()

    def test_cabeceras_minusculas(self):
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session)

        connector.webhook_handler(
            _wc_product(pid=3),
            {"x-wc-webhook-topic": "product.created"},
        )

        session.add.assert_called_once()

    def test_webhook_handler_embebe_producto(self):
        """El handler debe generar embedding si hay voyage_api_key."""
        session = _mock_session()
        session.scalar.return_value = None
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.return_value = [[0.5] * 1024]
            connector.webhook_handler(
                _wc_product(pid=20, name="Polera"),
                {"X-WC-Webhook-Topic": "product.created"},
            )

        mock_embed.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.embedding == [0.5] * 1024


# ================================================================ búsqueda híbrida

class TestBusquedaHibrida:
    def _fts_row(self, pid: int, name: str, score: float = 0.5) -> MagicMock:
        row = MagicMock()
        row.id = str(uuid.uuid4())
        row.name = name
        row.description_short = "Desc corta"
        row.url = f"https://shop.example.com/{pid}"
        row.sku = f"SKU-{pid}"
        row.price_regular = 9990
        row.stock_status = "in_stock"
        row.score = score
        return row

    def test_resultados_fts_retornados(self):
        session = _mock_session()
        row = self._fts_row(1, "Camiseta Roja", 0.8)
        session.execute.return_value.fetchall.return_value = [row]
        connector = _make_connector(session, voyage_key="")  # sin vector

        results = connector.search("camiseta roja", top_k=5)

        assert len(results) == 1
        assert results[0].title == "Camiseta Roja"

    def test_tenant_id_en_query_fts(self):
        """El prefiltro tenant_id DEBE estar presente en la consulta FTS."""
        session = _mock_session()
        session.execute.return_value.fetchall.return_value = []
        connector = _make_connector(session, voyage_key="")

        connector.search("zapatos")

        params = session.execute.call_args[0][1]
        assert str(params["tenant_id"]) == str(TENANT_A)

    def test_tenant_id_en_query_vector(self):
        """El prefiltro tenant_id DEBE estar en la consulta vectorial."""
        session = _mock_session()
        # primera llamada FTS vacía, segunda llamada vector
        session.execute.return_value.fetchall.side_effect = [[], []]
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.return_value = [[0.0] * 1024]
            connector.search("ropa")

        # segunda llamada es la vectorial
        all_calls = session.execute.call_args_list
        assert len(all_calls) == 2
        vec_params = all_calls[1][0][1]
        assert str(vec_params["tenant_id"]) == str(TENANT_A)

    def test_aislamiento_entre_tenants(self):
        """Búsqueda de Tenant A no puede ver resultados de Tenant B."""
        session_a = _mock_session(TENANT_A)
        session_b = _mock_session(TENANT_B)
        session_a.execute.return_value.fetchall.return_value = []
        session_b.execute.return_value.fetchall.return_value = []

        connector_a = WooCommerceConnector(TENANT_A, CONFIG_ID, session_a, "")
        connector_b = WooCommerceConnector(TENANT_B, CONFIG_ID, session_b, "")

        connector_a.search("zapatos")
        connector_b.search("zapatos")

        params_a = session_a.execute.call_args[0][1]
        params_b = session_b.execute.call_args[0][1]
        assert str(params_a["tenant_id"]) == str(TENANT_A)
        assert str(params_b["tenant_id"]) == str(TENANT_B)
        assert str(params_a["tenant_id"]) != str(params_b["tenant_id"])

    def test_hibrido_combina_fts_y_vector(self):
        session = _mock_session()
        fts_row = self._fts_row(1, "Camiseta Roja", 0.9)
        vec_row = self._fts_row(2, "Polera Azul", 0.7)
        session.execute.return_value.fetchall.side_effect = [
            [fts_row],
            [vec_row],
        ]
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.return_value = [[0.1] * 1024]
            results = connector.search("ropa", top_k=10)

        ids = {r.id for r in results}
        assert fts_row.id in ids
        assert vec_row.id in ids

    def test_score_hibrido_suma_fts_y_vector(self):
        """Un producto presente en ambas búsquedas tiene score combinado."""
        session = _mock_session()
        shared_id = str(uuid.uuid4())

        fts_row = self._fts_row(1, "Producto Compartido", 1.0)
        fts_row.id = shared_id

        vec_row = self._fts_row(1, "Producto Compartido", 0.9)
        vec_row.id = shared_id

        session.execute.return_value.fetchall.side_effect = [[fts_row], [vec_row]]
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.return_value = [[0.2] * 1024]
            results = connector.search("producto", top_k=5)

        # debe aparecer una sola vez con score FTS+vector
        matching = [r for r in results if r.id == shared_id]
        assert len(matching) == 1
        # score = fts(1.0 * 0.4) + vec(0.9 * 0.6) = 0.4 + 0.54 = 0.94
        assert matching[0].score == pytest.approx(0.94, abs=0.01)

    def test_falla_vector_retorna_solo_fts(self):
        """Si Voyage AI falla, la búsqueda FTS sigue funcionando."""
        session = _mock_session()
        fts_row = self._fts_row(1, "Producto FTS")
        session.execute.return_value.fetchall.return_value = [fts_row]
        connector = _make_connector(session, voyage_key="vk-test")

        with patch(
            "src.connectors.woocommerce.connector.embed_texts"
        ) as mock_embed:
            mock_embed.side_effect = Exception("Voyage API caído")
            results = connector.search("producto")

        assert len(results) == 1
        assert results[0].title == "Producto FTS"

    def test_top_k_limita_resultados(self):
        session = _mock_session()
        rows = [self._fts_row(i, f"Producto {i}", float(i) / 10) for i in range(20)]
        session.execute.return_value.fetchall.return_value = rows
        connector = _make_connector(session, voyage_key="")

        results = connector.search("producto", top_k=5)

        assert len(results) <= 5


# ================================================================ helpers puros

class TestHelpers:
    def test_product_text_incluye_campos_principales(self):
        p = {
            "name": "Camiseta Polo",
            "short_description": "Algodón 100%",
            "description": "Muy cómoda",
            "sku": "CAM-001",
            "categories": [{"name": "Ropa"}],
            "attributes": [{"name": "Color", "options": ["Rojo", "Azul"]}],
        }
        txt = _product_text(p)
        assert "Camiseta Polo" in txt
        assert "CAM-001" in txt
        assert "Ropa" in txt
        assert "Color" in txt
        assert "Rojo" in txt

    def test_product_text_sin_atributos(self):
        p = {"name": "Zapato", "sku": "ZAP-01"}
        assert "Zapato" in _product_text(p)

    def test_to_decimal_string_numerico(self):
        assert _to_decimal("9990") == 9990.0
        assert _to_decimal("9990.50") == 9990.50

    def test_to_decimal_vacio_es_none(self):
        assert _to_decimal("") is None
        assert _to_decimal(None) is None

    def test_to_decimal_no_numerico_es_none(self):
        assert _to_decimal("precio") is None

    def test_to_decimal_entero(self):
        assert _to_decimal(100) == 100.0
