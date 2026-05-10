"""Tests Fase 20 — historial_pedidos_contacto real.

Cobertura:
- _upsert_order() de WooCommerce persiste customer_phone y placed_at.
- _upsert_order() de Shopify persiste customer_phone y placed_at.
- historial_pedidos_contacto retorna pedidos por email.
- historial_pedidos_contacto retorna pedidos por teléfono.
- historial_pedidos_contacto resuelve contact desde contact_id (email + phone).
- Sin contacto → lista vacía + mensaje.
- Sin pedidos para el contacto → lista vacía + mensaje.
- Orden cronológico: placed_at DESC, luego created_at DESC.
- Respeta límite (limit).
- Soft-delete: pedidos con deleted_at no se incluyen.
- Aislamiento: tenant B no ve pedidos de tenant A.
- Shopify expose_tools incluye historial_pedidos_contacto.
- tool_runner pasa contact_id en extra_kwargs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, ConnectorDef, Order
from src.connectors.woocommerce.tools import historial_pedidos_contacto
from src.contacts.models import Contact
from src.db.models import Tenant

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant(db: Session, slug: str) -> Tenant:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    db.flush()
    return tenant


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
    blob = encrypt_credentials({"site_url": "https://x.com", "consumer_key": "ck", "consumer_secret": "cs"})
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Test Store",
        status="connected",
        webhook_secret="s",
        encrypted_credentials=blob,
    )
    db.add(config)
    db.flush()
    return config


def _make_contact(db: Session, tenant: Tenant, phone: str, email: str | None = None) -> Contact:
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        phone_e164=phone,
        email=email,
    )
    db.add(contact)
    db.flush()
    return contact


def _make_order(
    db: Session,
    tenant: Tenant,
    config: ConnectorConfig,
    *,
    external_id: str,
    status: str = "processing",
    total: float = 99.90,
    customer_email: str | None = None,
    customer_phone: str | None = None,
    placed_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Order:
    order = Order(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id=external_id,
        status=status,
        total=total,
        currency="CLP",
        customer_email=customer_email,
        customer_phone=customer_phone,
        placed_at=placed_at,
        deleted_at=deleted_at,
    )
    db.add(order)
    db.flush()
    return order


# ── _upsert_order: WooCommerce ────────────────────────────────────────────────


class TestWooUpsertOrderNuevosCampos:
    def test_persiste_phone_y_placed_at(self, db):
        tenant = _make_tenant(db, "woo-upsert-slug")
        config = _make_config(db, tenant)

        from src.connectors.woocommerce.connector import WooCommerceConnector
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        raw = {
            "id": 42,
            "status": "processing",
            "total": "59.99",
            "currency": "USD",
            "date_created": "2026-05-10T12:00:00",
            "billing": {
                "email": "cliente@ejemplo.com",
                "phone": "+56912345678",
            },
        }
        order = connector._upsert_order(raw)

        assert order.customer_email == "cliente@ejemplo.com"
        assert order.customer_phone == "+56912345678"
        assert order.placed_at is not None
        assert order.placed_at.year == 2026

    def test_placed_at_None_si_no_hay_fecha(self, db):
        tenant = _make_tenant(db, "woo-nodate-slug")
        config = _make_config(db, tenant)

        from src.connectors.woocommerce.connector import WooCommerceConnector
        connector = WooCommerceConnector(tenant.id, config.id, db=db)

        raw = {"id": 99, "status": "pending", "billing": {}}
        order = connector._upsert_order(raw)

        assert order.placed_at is None
        assert order.customer_phone is None


# ── _upsert_order: Shopify ────────────────────────────────────────────────────


class TestShopifyUpsertOrderNuevosCampos:
    def test_persiste_phone_y_placed_at(self, db):
        tenant = _make_tenant(db, "shopify-upsert-slug")
        config = _make_config(db, tenant, "shopify")

        from src.connectors.shopify.connector import ShopifyConnector
        connector = ShopifyConnector(tenant.id, config.id, db=db)

        raw = {
            "id": 77,
            "email": "user@shop.com",
            "financial_status": "paid",
            "current_total_price": "199.00",
            "currency": "MXN",
            "created_at": "2026-04-20T09:30:00Z",
            "billing_address": {"phone": "+525512345678"},
        }
        order = connector._upsert_order(raw)

        assert order.customer_email == "user@shop.com"
        assert order.customer_phone == "+525512345678"
        assert order.placed_at is not None
        assert order.placed_at.year == 2026


# ── historial_pedidos_contacto: WooCommerce tool real ────────────────────────


class TestHistorialPedidosContactoReal:
    def test_retorna_pedidos_por_email(self, db):
        tenant = _make_tenant(db, "hist-email-slug")
        config = _make_config(db, tenant)
        _make_order(db, tenant, config, external_id="1001", customer_email="juan@x.cl")
        _make_order(db, tenant, config, external_id="1002", customer_email="juan@x.cl", status="completed")

        result = historial_pedidos_contacto(
            2,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="juan@x.cl",
            db=db,
        )

        assert "pedidos" in result
        assert len(result["pedidos"]) == 2
        numeros = {p["numero_orden"] for p in result["pedidos"]}
        assert "1001" in numeros
        assert "1002" in numeros

    def test_retorna_pedidos_por_telefono(self, db):
        tenant = _make_tenant(db, "hist-phone-slug")
        config = _make_config(db, tenant)
        _make_order(db, tenant, config, external_id="2001", customer_phone="56911222333")

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_phone="56911222333",
            db=db,
        )

        assert len(result["pedidos"]) == 1
        assert result["pedidos"][0]["numero_orden"] == "2001"

    def test_resuelve_contacto_desde_contact_id_por_email(self, db):
        tenant = _make_tenant(db, "hist-cid-email-slug")
        config = _make_config(db, tenant)
        contact = _make_contact(db, tenant, phone="56900000001", email="pedro@shop.cl")
        _make_order(db, tenant, config, external_id="3001", customer_email="pedro@shop.cl")

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_id=contact.id,
            db=db,
        )

        assert len(result["pedidos"]) == 1
        assert result["pedidos"][0]["numero_orden"] == "3001"

    def test_resuelve_contacto_desde_contact_id_por_phone(self, db):
        tenant = _make_tenant(db, "hist-cid-phone-slug")
        config = _make_config(db, tenant)
        contact = _make_contact(db, tenant, phone="56988776655")
        _make_order(db, tenant, config, external_id="4001", customer_phone="56988776655")

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_id=contact.id,
            db=db,
        )

        assert len(result["pedidos"]) == 1
        assert result["pedidos"][0]["numero_orden"] == "4001"

    def test_sin_identificador_retorna_vacio(self, db):
        tenant = _make_tenant(db, "hist-none-slug")
        config = _make_config(db, tenant)

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            db=db,
        )

        assert result["pedidos"] == []
        assert "mensaje" in result

    def test_sin_pedidos_retorna_vacio_con_mensaje(self, db):
        tenant = _make_tenant(db, "hist-empty-slug")
        config = _make_config(db, tenant)

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="sinpedidos@x.cl",
            db=db,
        )

        assert result["pedidos"] == []
        assert "mensaje" in result

    def test_orden_cronologico_placed_at_desc(self, db):
        tenant = _make_tenant(db, "hist-order-slug")
        config = _make_config(db, tenant)
        t1 = datetime(2026, 1, 1, tzinfo=UTC)
        t2 = datetime(2026, 3, 1, tzinfo=UTC)
        t3 = datetime(2026, 5, 1, tzinfo=UTC)
        _make_order(db, tenant, config, external_id="A", customer_email="c@c.cl", placed_at=t1)
        _make_order(db, tenant, config, external_id="B", customer_email="c@c.cl", placed_at=t3)
        _make_order(db, tenant, config, external_id="C", customer_email="c@c.cl", placed_at=t2)

        result = historial_pedidos_contacto(
            10,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="c@c.cl",
            db=db,
        )

        numeros = [p["numero_orden"] for p in result["pedidos"]]
        assert numeros == ["B", "C", "A"]

    def test_respeta_limit(self, db):
        tenant = _make_tenant(db, "hist-limit-slug")
        config = _make_config(db, tenant)
        for i in range(10):
            _make_order(db, tenant, config, external_id=str(i), customer_email="lim@x.cl")

        result = historial_pedidos_contacto(
            3,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="lim@x.cl",
            db=db,
        )

        assert len(result["pedidos"]) == 3

    def test_no_incluye_soft_deleted(self, db):
        tenant = _make_tenant(db, "hist-deleted-slug")
        config = _make_config(db, tenant)
        _make_order(db, tenant, config, external_id="DEL1", customer_email="d@d.cl",
                    deleted_at=datetime(2026, 4, 1, tzinfo=UTC))
        _make_order(db, tenant, config, external_id="OK1", customer_email="d@d.cl")

        result = historial_pedidos_contacto(
            10,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="d@d.cl",
            db=db,
        )

        numeros = [p["numero_orden"] for p in result["pedidos"]]
        assert "DEL1" not in numeros
        assert "OK1" in numeros

    def test_estructura_pedido(self, db):
        tenant = _make_tenant(db, "hist-struct-slug")
        config = _make_config(db, tenant)
        placed = datetime(2026, 5, 10, 14, 30, tzinfo=UTC)
        _make_order(
            db, tenant, config,
            external_id="5001",
            customer_email="struct@x.cl",
            status="completed",
            total=150.50,
            placed_at=placed,
        )

        result = historial_pedidos_contacto(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="struct@x.cl",
            db=db,
        )

        pedido = result["pedidos"][0]
        assert pedido["numero_orden"] == "5001"
        assert pedido["estado"] == "completed"
        assert pedido["total"] == 150.50
        assert pedido["moneda"] == "CLP"
        assert pedido["fecha"] is not None


# ── Aislamiento multi-tenant ──────────────────────────────────────────────────


class TestHistorialAislamiento:
    def test_tenant_a_no_ve_pedidos_de_tenant_b(self, db):
        ta = _make_tenant(db, "hist-iso-a")
        tb = _make_tenant(db, "hist-iso-b")
        ca = _make_config(db, ta)
        cb = _make_config(db, tb)

        _make_order(db, ta, ca, external_id="A-001", customer_email="shared@x.cl")
        _make_order(db, tb, cb, external_id="B-001", customer_email="shared@x.cl")

        result_a = historial_pedidos_contacto(
            10,
            tenant_id=ta.id,
            config_id=ca.id,
            contact_email="shared@x.cl",
            db=db,
        )
        result_b = historial_pedidos_contacto(
            10,
            tenant_id=tb.id,
            config_id=cb.id,
            contact_email="shared@x.cl",
            db=db,
        )

        numeros_a = {p["numero_orden"] for p in result_a["pedidos"]}
        numeros_b = {p["numero_orden"] for p in result_b["pedidos"]}
        assert numeros_a == {"A-001"}
        assert numeros_b == {"B-001"}


# ── Shopify expose_tools incluye historial_pedidos_contacto ──────────────────


class TestShopifyExposeToolsHistorial:
    def test_expose_tools_incluye_historial(self, db):
        tenant = _make_tenant(db, "shopify-expose-slug")
        config = _make_config(db, tenant, "shopify")

        from src.connectors.shopify.connector import ShopifyConnector
        connector = ShopifyConnector(tenant.id, config.id, db=db)
        tools = connector.expose_tools()
        names = [t.name for t in tools]
        assert "historial_pedidos_contacto" in names

    def test_shopify_historial_retorna_pedidos(self, db):
        from src.connectors.shopify.tools import historial_pedidos_contacto as shopify_hist
        tenant = _make_tenant(db, "shopify-hist-slug")
        config = _make_config(db, tenant, "shopify")
        _make_order(db, tenant, config, external_id="S-001", customer_email="s@s.cl")

        result = shopify_hist(
            5,
            tenant_id=tenant.id,
            config_id=config.id,
            contact_email="s@s.cl",
            db=db,
        )

        assert len(result["pedidos"]) == 1
        assert result["pedidos"][0]["numero_orden"] == "S-001"


# ── tool_runner: contact_id en extra_kwargs ───────────────────────────────────


class TestToolRunnerContactId:
    def test_extra_kwargs_incluye_contact_id(self, db):
        from unittest.mock import MagicMock, patch

        tenant = _make_tenant(db, "tr-contact-id-slug")
        contact_id = uuid.uuid4()

        conversation = MagicMock()
        conversation.tenant_id = tenant.id
        conversation.contact_id = contact_id

        with patch("src.agent.tool_runner.bypass_tenant_filter") as mock_bypass, \
             patch.object(db, "query") as mock_query:
            mock_bypass.return_value.__enter__ = lambda s: s
            mock_bypass.return_value.__exit__ = MagicMock(return_value=False)
            mock_query.return_value.join.return_value.filter.return_value.all.return_value = []

            from src.agent.tool_runner import collect_tools_for_conversation
            _, index = collect_tools_for_conversation(db, conversation)

        for name, resolved in index.items():
            if not resolved.is_builtin:
                assert "contact_id" in resolved.extra_kwargs
                assert resolved.extra_kwargs["contact_id"] == contact_id
