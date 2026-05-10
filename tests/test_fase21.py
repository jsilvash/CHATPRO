"""Tests de Fase 21.

Cubre:
A. Webhooks salientes para eventos de conectores:
   - WooCommerce order.created → delivery "connector.order_created" para tenant suscripto.
   - WooCommerce order.updated → delivery "connector.order_updated".
   - WooCommerce product.created/updated → delivery "connector.product_updated".
   - Tenant B no recibe deliveries de eventos del tenant A.

B. Panel de métricas de conectores:
   - GET /v1/connector-configs/{id}/stats → products_count, orders_count, status.
   - Tenant B no puede leer stats de tenant A.

C. Anti-hallucination — logging completo:
   - Precio alucinado → run_agent_turn loggea warning con conversation_id.
   - Precio grounded → no se loggea el warning de anti-hallucination.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product
from src.connectors.woocommerce.connector import WooCommerceConnector
from src.connectors.shopify.connector import ShopifyConnector
from src.db.models import Tenant
from src.public_api.models import WebhookDelivery, WebhookOut


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_connector_def(db: Session, name: str = "woocommerce") -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == name).first()
    if existing:
        return existing
    defn = ConnectorDef(id=uuid.uuid4(), name=name, kind="ecommerce", version="1.0.0")
    db.add(defn)
    db.flush()
    return defn


def _make_config(db: Session, tenant: Tenant, name: str = "woocommerce") -> ConnectorConfig:
    defn = _make_connector_def(db, name)
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
        webhook_secret="secreto",
        encrypted_credentials=blob,
    )
    db.add(config)
    db.flush()
    return config


def _make_webhook_out(db: Session, tenant: Tenant, events: list[str]) -> WebhookOut:
    wh = WebhookOut(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        url="https://receptor.example.com/hooks",
        secret="wh_secret_test",
        events=events,
        enabled=True,
        consecutive_failures=0,
    )
    db.add(wh)
    db.flush()
    return wh


def _make_product(db: Session, tenant: Tenant, config: ConnectorConfig, **kwargs) -> Product:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": tenant.id,
        "connector_config_id": config.id,
        "external_id": str(uuid.uuid4().int)[:6],
        "name": "Producto Test",
        "sku": f"SKU-{uuid.uuid4().hex[:4]}",
    }
    defaults.update(kwargs)
    p = Product(**defaults)
    db.add(p)
    db.flush()
    return p


def _make_order(db: Session, tenant: Tenant, config: ConnectorConfig, **kwargs) -> Order:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": tenant.id,
        "connector_config_id": config.id,
        "external_id": str(uuid.uuid4().int)[:6],
        "status": "processing",
        "total": 100.0,
        "currency": "USD",
    }
    defaults.update(kwargs)
    o = Order(**defaults)
    db.add(o)
    db.flush()
    return o


_FAKE_ORDER_PAYLOAD = {
    "id": 555,
    "status": "processing",
    "total": "45.00",
    "currency": "USD",
    "billing": {"email": "cliente@ejemplo.com", "phone": "+54911234567"},
    "date_created": "2026-05-10T12:00:00",
}

_FAKE_PRODUCT_PAYLOAD = {
    "id": 99,
    "sku": "REMERA-L",
    "name": "Remera Test",
    "short_description": "Descripción corta",
    "description": "Descripción larga",
    "regular_price": "25.00",
    "sale_price": "20.00",
    "stock_quantity": 50,
    "stock_status": "in_stock",
    "permalink": "https://tienda.com/remera",
    "images": [],
    "categories": [],
    "attributes": [],
    "variations": [],
}


# ══════════════════════════════════════════════════════════════════════════════
# A. Webhooks salientes — WooCommerce
# ══════════════════════════════════════════════════════════════════════════════


class TestWebhooksSalientesWoo:
    def test_order_created_dispara_delivery(self, db: Session, tenant_a):
        """order.created → WebhookDelivery con event='connector.order_created'."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)
        _make_webhook_out(db, tenant, ["connector.order_created"])

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        connector.webhook_handler(_FAKE_ORDER_PAYLOAD, {"x-wc-webhook-topic": "order.created"})

        delivery = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.order_created",
            )
            .first()
        )
        assert delivery is not None
        assert delivery.status == "pending"

    def test_order_updated_dispara_delivery(self, db: Session, tenant_a):
        """order.updated → WebhookDelivery con event='connector.order_updated'."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)
        _make_webhook_out(db, tenant, ["connector.order_updated"])

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        connector.webhook_handler(_FAKE_ORDER_PAYLOAD, {"x-wc-webhook-topic": "order.updated"})

        delivery = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.order_updated",
            )
            .first()
        )
        assert delivery is not None
        assert delivery.status == "pending"

    def test_product_updated_dispara_delivery(self, db: Session, tenant_a):
        """product.created/updated → WebhookDelivery con event='connector.product_updated'."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)
        _make_webhook_out(db, tenant, ["connector.product_updated"])

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        with patch.object(connector, "_enqueue_embed"):
            connector.webhook_handler(_FAKE_PRODUCT_PAYLOAD, {"x-wc-webhook-topic": "product.updated"})

        delivery = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.product_updated",
            )
            .first()
        )
        assert delivery is not None

    def test_order_created_sin_webhook_suscripto_no_crea_delivery(self, db: Session, tenant_a):
        """Si no hay WebhookOut suscripto, no se crea delivery."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)
        # Crear webhook suscripto a un evento DIFERENTE
        _make_webhook_out(db, tenant, ["message.received"])

        connector = WooCommerceConnector(tenant.id, config.id, db=db)
        connector.webhook_handler(_FAKE_ORDER_PAYLOAD, {"x-wc-webhook-topic": "order.created"})

        count = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.order_created",
            )
            .count()
        )
        assert count == 0

    def test_aislamiento_tenant_b_no_recibe_delivery_de_tenant_a(self, db: Session, tenant_a, tenant_b):
        """Tenant B con webhook suscripto no recibe delivery de eventos de tenant A."""
        tenant_a_obj, _ = tenant_a
        tenant_b_obj, _ = tenant_b
        config_a = _make_config(db, tenant_a_obj)
        # Webhook del tenant B
        _make_webhook_out(db, tenant_b_obj, ["connector.order_created"])

        connector = WooCommerceConnector(tenant_a_obj.id, config_a.id, db=db)
        connector.webhook_handler(_FAKE_ORDER_PAYLOAD, {"x-wc-webhook-topic": "order.created"})

        # Tenant B no debe tener deliveries
        count_b = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant_b_obj.id,
                WebhookDelivery.event == "connector.order_created",
            )
            .count()
        )
        assert count_b == 0


# ══════════════════════════════════════════════════════════════════════════════
# A. Webhooks salientes — Shopify
# ══════════════════════════════════════════════════════════════════════════════


class TestWebhooksSalientesShopify:
    def _make_shopify_config(self, db: Session, tenant: Tenant) -> ConnectorConfig:
        defn = _make_connector_def(db, "shopify")
        from src.connectors.crypto import encrypt_credentials
        blob = encrypt_credentials({
            "shop_url": "https://tienda.myshopify.com",
            "access_token": "tok_test",
        })
        config = ConnectorConfig(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            connector_def_id=defn.id,
            display_name="Shopify Test",
            status="connected",
            webhook_secret="secreto",
            encrypted_credentials=blob,
        )
        db.add(config)
        db.flush()
        return config

    def test_orders_create_dispara_delivery(self, db: Session, tenant_a):
        """Shopify orders/create → delivery 'connector.order_created'."""
        tenant, _ = tenant_a
        config = self._make_shopify_config(db, tenant)
        _make_webhook_out(db, tenant, ["connector.order_created"])

        connector = ShopifyConnector(tenant.id, config.id, db=db)
        shopify_order = {
            "id": "shopify_123",
            "financial_status": "paid",
            "total_price": "99.00",
            "currency": "ARS",
            "billing_address": {"phone": "+5491122334455"},
            "email": "buyer@example.com",
            "created_at": "2026-05-10T12:00:00-03:00",
            "line_items": [],
        }
        connector.webhook_handler(shopify_order, {"x-shopify-topic": "orders/create"})

        delivery = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.order_created",
            )
            .first()
        )
        assert delivery is not None
        assert delivery.status == "pending"

    def test_orders_updated_dispara_delivery(self, db: Session, tenant_a):
        """Shopify orders/updated → delivery 'connector.order_updated'."""
        tenant, _ = tenant_a
        config = self._make_shopify_config(db, tenant)
        _make_webhook_out(db, tenant, ["connector.order_updated"])

        connector = ShopifyConnector(tenant.id, config.id, db=db)
        shopify_order = {
            "id": "shopify_456",
            "financial_status": "paid",
            "total_price": "55.00",
            "currency": "ARS",
            "billing_address": {},
            "email": "buyer2@example.com",
            "created_at": "2026-05-10T13:00:00-03:00",
            "line_items": [],
        }
        connector.webhook_handler(shopify_order, {"x-shopify-topic": "orders/updated"})

        delivery = (
            db.query(WebhookDelivery)
            .filter(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "connector.order_updated",
            )
            .first()
        )
        assert delivery is not None


# ══════════════════════════════════════════════════════════════════════════════
# B. Panel de métricas — GET /v1/connector-configs/{id}/stats
# ══════════════════════════════════════════════════════════════════════════════


class TestConnectorStats:
    def test_stats_retorna_conteos_correctos(self, client_a, tenant_a, db: Session):
        """GET /stats devuelve products_count y orders_count reales."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)

        for i in range(3):
            _make_product(db, tenant, config, external_id=str(i + 1000))
        for i in range(2):
            _make_order(db, tenant, config, external_id=str(i + 2000))

        resp = client_a.get(f"/v1/connector-configs/{config.id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["products_count"] == 3
        assert data["orders_count"] == 2
        assert data["status"] == "connected"
        assert "last_full_sync_at" in data
        assert "last_incremental_sync_at" in data
        assert "last_error" in data

    def test_stats_cero_cuando_no_hay_productos_ni_ordenes(self, client_a, tenant_a, db: Session):
        """Stats con productos y órdenes vacíos retorna 0 en ambos campos."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)

        resp = client_a.get(f"/v1/connector-configs/{config.id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["products_count"] == 0
        assert data["orders_count"] == 0

    def test_stats_excluye_productos_borrados(self, client_a, tenant_a, db: Session):
        """products_count no cuenta los products con deleted_at != None."""
        from datetime import datetime, timezone
        tenant, _ = tenant_a
        config = _make_config(db, tenant)

        # 2 activos + 1 borrado
        _make_product(db, tenant, config, external_id="1001")
        _make_product(db, tenant, config, external_id="1002")
        deleted = _make_product(db, tenant, config, external_id="1003")
        deleted.deleted_at = datetime.now(timezone.utc)
        db.flush()

        resp = client_a.get(f"/v1/connector-configs/{config.id}/stats")
        assert resp.status_code == 200
        assert resp.json()["products_count"] == 2

    def test_stats_config_no_encontrada_retorna_404(self, client_a, db: Session):
        """Config inexistente → 404."""
        resp = client_a.get(f"/v1/connector-configs/{uuid.uuid4()}/stats")
        assert resp.status_code == 404

    def test_aislamiento_tenant_b_no_puede_leer_stats_de_tenant_a(
        self, client_a, client_b, tenant_a, db: Session
    ):
        """Tenant B recibe 404 al intentar leer stats de la config de tenant A."""
        tenant, _ = tenant_a
        config = _make_config(db, tenant)

        resp = client_b.get(f"/v1/connector-configs/{config.id}/stats")
        assert resp.status_code == 404

    def test_stats_refleja_last_full_sync_at(self, client_a, tenant_a, db: Session):
        """last_full_sync_at refleja el valor real de la config."""
        from datetime import datetime, timezone
        tenant, _ = tenant_a
        config = _make_config(db, tenant)
        sync_time = datetime(2026, 5, 10, 10, 0, 0, tzinfo=timezone.utc)
        config.last_full_sync_at = sync_time
        db.flush()

        resp = client_a.get(f"/v1/connector-configs/{config.id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_full_sync_at"] is not None
        assert "2026-05-10" in data["last_full_sync_at"]


# ══════════════════════════════════════════════════════════════════════════════
# C. Anti-hallucination — logging completo en run_agent_turn
# ══════════════════════════════════════════════════════════════════════════════

CONV_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def _make_llm_resp(stop_reason: str, content: list, cost_usd: float = 0.0):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    return resp, {"cost_usd": cost_usd}


def _text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


class TestAntiHallucinationLogging:
    def test_precio_alucinado_loggea_warning_con_conversation_id(self, caplog):
        """Precio en respuesta no grounded → warning con conversation_id en el log."""
        from src.agent.service import run_agent_turn

        resp_end, meta_end = _make_llm_resp(
            "end_turn",
            [_text_block("El producto cuesta $999.99, ¡oferta única!")],
        )

        with caplog.at_level(logging.WARNING, logger="src.agent.service"), \
             patch("src.agent.service.call_claude_messages", return_value=(resp_end, meta_end)):
            result = run_agent_turn(
                messages=[{"role": "user", "content": "¿cuánto cuesta?"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=None,
                session=None,
            )

        assert result.hallucination_flag is True
        # Debe haber un warning con el conversation_id
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(str(CONV_ID) in msg for msg in warning_msgs), (
            f"conversation_id {CONV_ID} no encontrado en warnings: {warning_msgs}"
        )

    def test_precio_grounded_no_loggea_warning_anti_hallucination(self, caplog):
        """Precio en respuesta que coincide con tool_result → no loggea warning de anti-hallucination."""
        from src.agent.service import run_agent_turn
        from src.connectors.base import ToolSchema

        connector = MagicMock()
        connector.tenant_id = TENANT_ID
        connector.expose_tools.return_value = [
            ToolSchema(
                name="buscar_productos",
                description="Busca.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                callable_ref="src.connectors.woocommerce.tools:buscar_productos",
            ),
        ]

        tool_id = "tu_001"
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = tool_id
        tool_block.name = "buscar_productos"
        tool_block.input = {"query": "zapatilla"}

        resp_tool, meta_tool = _make_llm_resp("tool_use", [tool_block])
        resp_end, meta_end = _make_llm_resp(
            "end_turn",
            [_text_block("La zapatilla cuesta $89.99 y tiene stock.")],
        )
        tool_result = {"resultados": [{"nombre": "Zapatilla", "precio": 89.99}]}

        with caplog.at_level(logging.WARNING, logger="src.agent.service"), \
             patch("src.agent.service.call_claude_messages",
                   side_effect=[(resp_tool, meta_tool), (resp_end, meta_end)]), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=(tool_result, "ok", 10)):
            result = run_agent_turn(
                messages=[{"role": "user", "content": "¿cuánto cuesta la zapatilla?"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
            )

        assert result.hallucination_flag is False
        # No debe haber warning de anti-hallucination con el CONV_ID
        anti_hall_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "anti-hallucination" in r.message
        ]
        assert not any(str(CONV_ID) in msg for msg in anti_hall_warnings)

    def test_respuesta_sin_precios_no_es_alucinacion(self, caplog):
        """Respuesta sin precios → hallucination_flag=False sin warnings."""
        from src.agent.service import run_agent_turn

        resp_end, meta_end = _make_llm_resp(
            "end_turn",
            [_text_block("Hola, ¿en qué te puedo ayudar hoy?")],
        )

        with caplog.at_level(logging.WARNING, logger="src.agent.service"), \
             patch("src.agent.service.call_claude_messages", return_value=(resp_end, meta_end)):
            result = run_agent_turn(
                messages=[{"role": "user", "content": "hola"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=None,
                session=None,
            )

        assert result.hallucination_flag is False
        anti_hall_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "anti-hallucination" in r.message
        ]
        assert len(anti_hall_warnings) == 0
