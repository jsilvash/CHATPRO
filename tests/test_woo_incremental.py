"""Tests Fase 6: webhook WooCommerce + sync_incremental + embeddings + búsqueda híbrida.

Cobertura:
- verify_webhook: firma HMAC-SHA256 válida e inválida, header ausente.
- webhook_handler product.created/updated: upsert en products + embedding.
- webhook_handler product.deleted: soft-delete en products.
- webhook_handler order.created/updated: upsert en orders.
- Endpoint POST /webhooks/woo/{tenant_id}/{config_id}: 200 ok, 401 firma inválida.
- sync_incremental: pasa modified_after, crea productos, actualiza last_incremental_sync_at.
- embed_product: genera y persiste ProductEmbedding; actualiza si ya existe.
- search(): ILIKE, top-k, no retorna eliminados, búsqueda híbrida con embeddings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.connectors.models import ConnectorConfig, ConnectorDef, Order, Product, ProductEmbedding
from src.connectors.woocommerce.connector import WooCommerceConnector
from src.db.models import Tenant

# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _setup_connector(
    db: Session, tenant: Tenant
) -> tuple[WooCommerceConnector, ConnectorConfig]:
    defn = _make_connector_def(db)
    secret = "super_secreto_test_fase6"
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Tienda Fase 6",
        status="pending",
        webhook_secret=secret,
    )
    db.add(config)
    db.flush()

    connector = WooCommerceConnector(tenant.id, config.id, db=db)
    connector.configure({
        "site_url": "https://tienda.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
    })
    # configure() puede generar un nuevo webhook_secret; restauramos el conocido
    config.webhook_secret = secret
    db.flush()
    return connector, config


def _hmac_signature(secret: str, payload: bytes) -> str:
    digest = hmac_mod.new(secret.encode(), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


_FAKE_PRODUCT = {
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

_FAKE_EMBEDDING = [0.1] * 1024


# ── Tests verify_webhook ──────────────────────────────────────────────────────


def test_verify_webhook_firma_valida(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    payload = b'{"id":42}'
    sig = _hmac_signature(config.webhook_secret, payload)

    result = connector.verify_webhook(payload, {"x-wc-webhook-signature": sig})

    assert result.valid is True


def test_verify_webhook_firma_invalida(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    result = connector.verify_webhook(
        b'{"id":42}',
        {"x-wc-webhook-signature": "ZmlybWFfbWFsYQ=="},  # base64 de "firma_mala"
    )

    assert result.valid is False
    assert result.reason == "firma inválida"


def test_verify_webhook_header_ausente(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    result = connector.verify_webhook(b'{"id":42}', {})

    assert result.valid is False
    assert "ausente" in (result.reason or "")


def test_verify_webhook_firma_mal_codificada(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    result = connector.verify_webhook(
        b'{"id":42}',
        {"x-wc-webhook-signature": "no_es_base64_!@#"},
    )

    assert result.valid is False


# ── Tests webhook_handler — products ─────────────────────────────────────────


def test_webhook_handler_product_created_upsert(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    with patch("src.connectors.embeddings.generate_embedding", return_value=_FAKE_EMBEDDING):
        connector.webhook_handler(_FAKE_PRODUCT, {"x-wc-webhook-topic": "product.created"})

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
    assert product.deleted_at is None


def test_webhook_handler_product_updated_upsert(db: Session, tenant_a):
    tenant, _ = tenant_a
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

    with patch("src.connectors.embeddings.generate_embedding", return_value=_FAKE_EMBEDDING):
        connector.webhook_handler(_FAKE_PRODUCT, {"x-wc-webhook-topic": "product.updated"})

    db.refresh(existing)
    assert existing.name == "Remera XYZ"
    assert existing.deleted_at is None


def test_webhook_handler_product_deleted_soft_delete(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    existing = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="42",
        name="Producto a borrar",
    )
    db.add(existing)
    db.flush()

    connector.webhook_handler({"id": 42}, {"x-wc-webhook-topic": "product.deleted"})

    db.refresh(existing)
    assert existing.deleted_at is not None


def test_webhook_handler_product_deleted_no_elimina_fisicamente(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    existing = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="55",
        name="Producto 55",
    )
    db.add(existing)
    db.flush()

    connector.webhook_handler({"id": 55}, {"x-wc-webhook-topic": "product.deleted"})

    # El registro sigue en BD (soft delete)
    still_there = db.get(Product, existing.id)
    assert still_there is not None
    assert still_there.deleted_at is not None


# ── Tests webhook_handler — orders ────────────────────────────────────────────


def test_webhook_handler_order_created(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    order_payload = {
        "id": 100,
        "status": "processing",
        "total": "59.99",
        "currency": "USD",
        "date_created": "2026-05-09T10:00:00",
    }
    connector.webhook_handler(order_payload, {"x-wc-webhook-topic": "order.created"})

    order = (
        db.query(Order)
        .filter(
            Order.tenant_id == tenant.id,
            Order.connector_config_id == config.id,
            Order.external_id == "100",
        )
        .first()
    )
    assert order is not None
    assert order.status == "processing"
    assert float(order.total) == pytest.approx(59.99)
    assert order.currency == "USD"


def test_webhook_handler_order_updated_upsert(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    payload_v1 = {"id": 200, "status": "pending", "total": "10.00", "currency": "CLP"}
    payload_v2 = {"id": 200, "status": "completed", "total": "10.00", "currency": "CLP"}

    connector.webhook_handler(payload_v1, {"x-wc-webhook-topic": "order.created"})
    connector.webhook_handler(payload_v2, {"x-wc-webhook-topic": "order.updated"})

    orders = (
        db.query(Order)
        .filter(
            Order.tenant_id == tenant.id,
            Order.external_id == "200",
        )
        .all()
    )
    assert len(orders) == 1  # no duplicados
    assert orders[0].status == "completed"


# ── Tests endpoint HTTP /webhooks/woo ─────────────────────────────────────────


def test_endpoint_webhook_firma_valida(client, db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    payload_bytes = json.dumps(_FAKE_PRODUCT).encode()
    sig = _hmac_signature(config.webhook_secret, payload_bytes)

    with patch("src.connectors.embeddings.generate_embedding", return_value=_FAKE_EMBEDDING):
        r = client.post(
            f"/webhooks/woo/{tenant.id}/{config.id}",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-WC-Webhook-Signature": sig,
                "X-WC-Webhook-Topic": "product.created",
            },
        )

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_endpoint_webhook_firma_invalida_retorna_401(client, db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    payload_bytes = json.dumps(_FAKE_PRODUCT).encode()

    r = client.post(
        f"/webhooks/woo/{tenant.id}/{config.id}",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": "firma_completamente_erronea==",
            "X-WC-Webhook-Topic": "product.created",
        },
    )

    assert r.status_code == 401


def test_endpoint_webhook_config_inexistente(client, db: Session, tenant_a):
    tenant, _ = tenant_a

    r = client.post(
        f"/webhooks/woo/{tenant.id}/{uuid.uuid4()}",
        content=b'{}',
        headers={
            "Content-Type": "application/json",
            "X-WC-Webhook-Signature": "cualquier_firma==",
        },
    )
    # Config no encontrada → verify falla → 401
    assert r.status_code == 401


# ── Tests sync_incremental ────────────────────────────────────────────────────


def _build_woo_response(products: list, total_pages: int = 1):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = products
    mock_resp.headers = {"X-WP-TotalPages": str(total_pages)}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_sync_incremental_pasa_modified_after(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    since = datetime(2026, 5, 1, tzinfo=UTC)

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_response([_FAKE_PRODUCT]),
            _build_woo_response([]),
        ]
        mock_cls.return_value = mock_client

        result = connector.sync_incremental(since)

    # Verifica que modified_after se pasó al API
    call_params = mock_client.get.call_args_list[0][1].get("params", {})
    assert "modified_after" in call_params
    assert result.items_created == 1
    assert result.errors == []


def test_sync_incremental_actualiza_last_incremental_sync_at(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    since = datetime(2026, 5, 1, tzinfo=UTC)

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [_build_woo_response([])]
        mock_cls.return_value = mock_client

        connector.sync_incremental(since)

    assert config.last_incremental_sync_at is not None


def test_sync_incremental_crea_productos_nuevos(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    product2 = {**_FAKE_PRODUCT, "id": 99, "name": "Producto Nuevo", "sku": "PROD-99"}
    since = datetime(2026, 5, 1, tzinfo=UTC)

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [
            _build_woo_response([product2]),
            _build_woo_response([]),
        ]
        mock_cls.return_value = mock_client

        result = connector.sync_incremental(since)

    assert result.items_created == 1
    p = (
        db.query(Product)
        .filter(Product.tenant_id == tenant.id, Product.external_id == "99")
        .first()
    )
    assert p is not None
    assert p.name == "Producto Nuevo"


# ── Tests embed_product ───────────────────────────────────────────────────────


def test_embed_product_genera_y_persiste(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    product = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="99",
        name="Zapatilla Running",
        description_short="Para running",
        sku="ZAP-99",
        categories=["Calzado"],
    )
    db.add(product)
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=_FAKE_EMBEDDING):
        connector.embed_product(product.id)

    emb = (
        db.query(ProductEmbedding)
        .filter(ProductEmbedding.product_id == product.id)
        .first()
    )
    assert emb is not None
    assert "Zapatilla Running" in emb.content
    assert len(emb.embedding) == 1024


def test_embed_product_actualiza_si_ya_existe(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    product = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="101",
        name="Producto Actualizado",
    )
    db.add(product)
    db.flush()

    # Embedding previo con vector viejo
    old_emb = ProductEmbedding(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        product_id=product.id,
        content="contenido viejo",
        embedding=[0.0] * 1024,
    )
    db.add(old_emb)
    db.flush()

    new_vector = [0.5] * 1024
    with patch("src.connectors.embeddings.generate_embedding", return_value=new_vector):
        connector.embed_product(product.id)

    db.refresh(old_emb)
    assert "Producto Actualizado" in old_emb.content
    # Solo debe haber UN embedding por producto
    count = (
        db.query(ProductEmbedding)
        .filter(ProductEmbedding.product_id == product.id)
        .count()
    )
    assert count == 1


def test_embed_product_ignora_producto_de_otro_tenant(db: Session, tenant_a, tenant_b):
    tenant_a_obj, _ = tenant_a
    tenant_b_obj, _ = tenant_b

    defn = _make_connector_def(db)
    config_b = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant_b_obj.id,
        connector_def_id=defn.id,
        display_name="Tienda B",
        status="pending",
        webhook_secret="secreto_b",
    )
    db.add(config_b)

    product_b = Product(
        id=uuid.uuid4(),
        tenant_id=tenant_b_obj.id,
        connector_config_id=config_b.id,
        external_id="b1",
        name="Producto de B",
    )
    db.add(product_b)
    db.flush()

    # Conector de A intenta embeber producto de B → no debe hacer nada
    connector_a = WooCommerceConnector(tenant_a_obj.id, config_b.id, db=db)
    with patch("src.connectors.embeddings.generate_embedding", return_value=_FAKE_EMBEDDING):
        connector_a.embed_product(product_b.id)

    count = (
        db.query(ProductEmbedding)
        .filter(ProductEmbedding.product_id == product_b.id)
        .count()
    )
    assert count == 0


# ── Tests search() ────────────────────────────────────────────────────────────


def test_search_retorna_por_nombre(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    db.add(Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="s1",
        name="Zapatilla Running Pro",
        description_short="Para maratón",
        sku="ZAP-PRO",
        price_regular=149.99,
        stock_status="in_stock",
    ))
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=[0.0] * 1024):
        results = connector.search("zapatilla")

    assert len(results) >= 1
    assert results[0].title == "Zapatilla Running Pro"
    assert results[0].score > 0


def test_search_respeta_top_k(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    for i in range(6):
        db.add(Product(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            connector_config_id=config.id,
            external_id=f"tk{i}",
            name=f"Remera Modelo {i}",
        ))
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=[0.0] * 1024):
        results = connector.search("remera", top_k=3)

    assert len(results) <= 3


def test_search_no_retorna_eliminados(db: Session, tenant_a):
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    db.add(Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="del1",
        name="Campera Eliminada",
        deleted_at=datetime.now(UTC),
    ))
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=[0.0] * 1024):
        results = connector.search("campera eliminada")

    assert all(r.title != "Campera Eliminada" for r in results)


def test_search_hibrida_incluye_resultado_vectorial(db: Session, tenant_a):
    """Un producto sin match de texto pero con embedding coincidente aparece en resultados."""
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    # Producto cuyo nombre no matchea el query por ILIKE
    product = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="vec1",
        name="XYZ-ITEM-ESPECIAL",
        description_short="Descripción genérica",
    )
    db.add(product)
    db.flush()

    # Embedding idéntico al query → cosine similarity = 1.0
    vec = [0.1] * 1024
    db.add(ProductEmbedding(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        product_id=product.id,
        content="XYZ-ITEM-ESPECIAL | Descripción genérica",
        embedding=vec,
    ))
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=vec):
        results = connector.search("algo que no matchea por texto")

    ids = [r.id for r in results]
    assert str(product.id) in ids


def test_search_score_mayor_con_texto_y_vector(db: Session, tenant_a):
    """Producto con match texto + vector tiene score mayor que solo texto o solo vector."""
    tenant, _ = tenant_a
    connector, config = _setup_connector(db, tenant)

    vec = [0.2] * 1024

    # Producto A: match texto pero sin embedding
    prod_a = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="a1",
        name="Bolso de Viaje",
    )
    # Producto B: match texto + embedding
    prod_b = Product(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_config_id=config.id,
        external_id="b1",
        name="Bolso de Viaje Deluxe",
    )
    db.add_all([prod_a, prod_b])
    db.flush()

    db.add(ProductEmbedding(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        product_id=prod_b.id,
        content="Bolso de Viaje Deluxe",
        embedding=vec,
    ))
    db.flush()

    with patch("src.connectors.embeddings.generate_embedding", return_value=vec):
        results = connector.search("bolso")

    assert len(results) >= 2
    score_b = next((r.score for r in results if r.id == str(prod_b.id)), 0.0)
    score_a = next((r.score for r in results if r.id == str(prod_a.id)), 0.0)
    assert score_b >= score_a
