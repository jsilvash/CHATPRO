"""Tools invocables por el agente Claude para el conector Shopify.

Cada función recibe el contexto (tenant_id, config_id) y retorna un dict
que se envía como ``tool_result`` al agente.

``db`` es opcional: si se pasa una sesión SQLAlchemy se reutiliza, si no
se abre una nueva. Esto permite a los tests usar una sesión con rollback.

``buscar_productos`` usa ``ShopifyConnector.search()`` — búsqueda híbrida
pgvector + keyword con RRF (igual que WooCommerce, Fase 18).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from src.connectors.models import Product
from src.db.session import get_db_session


@contextmanager
def _db_ctx(db):
    if db is not None:
        yield db
    else:
        with get_db_session() as session:
            yield session


def buscar_productos(
    query: str,
    max_results: int = 5,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    db=None,
    **_kwargs,
) -> dict:
    """Busca productos usando búsqueda híbrida semántica (pgvector + keyword + RRF)."""
    max_results = max(1, min(max_results, 20))

    from src.connectors.shopify.connector import ShopifyConnector
    connector = ShopifyConnector(tenant_id=tenant_id, config_id=config_id, db=db)
    results = connector.search(query, top_k=max_results)

    if not results:
        return {"resultados": [], "mensaje": "No se encontraron productos."}

    return {
        "resultados": [
            {
                "id": r.id,
                "external_id": r.metadata.get("external_id"),
                "nombre": r.title,
                "sku": r.metadata.get("sku"),
                "precio": r.metadata.get("price_sale") or r.metadata.get("price_regular") or 0.0,
                "moneda": r.metadata.get("currency") or "USD",
                "stock": r.metadata.get("stock_quantity"),
                "stock_status": r.metadata.get("stock_status"),
                "url": r.url,
                "descripcion": r.snippet,
            }
            for r in results
        ]
    }


def consultar_stock_y_precio(
    sku: str | None = None,
    product_id: str | None = None,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    db=None,
    **_kwargs,
) -> dict:
    """Retorna stock, precio y link de un producto Shopify por SKU o ID externo."""
    if not sku and not product_id:
        return {"error": "Se requiere sku o product_id"}

    with _db_ctx(db) as session:
        q = session.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.connector_config_id == config_id,
            Product.deleted_at.is_(None),
        )
        if sku:
            q = q.filter(Product.sku == sku)
        else:
            q = q.filter(Product.external_id == str(product_id))

        product = q.first()

    if product is None:
        return {"error": f"Producto no encontrado (sku={sku}, id={product_id})"}

    return {
        "id": str(product.id),
        "nombre": product.name,
        "sku": product.sku,
        "precio_regular": float(product.price_regular) if product.price_regular else None,
        "precio_oferta": float(product.price_sale) if product.price_sale else None,
        "moneda": product.currency or "USD",
        "stock_cantidad": product.stock_quantity,
        "stock_status": product.stock_status,
        "url": product.url,
    }


def historial_pedidos_contacto(
    limit: int = 5,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    contact_id: uuid.UUID | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    db=None,
    **_kwargs,
) -> dict:
    """Retorna los últimos pedidos del contacto buscando por email o teléfono.

    Comparte la tabla ``orders`` con WooCommerce — el conector activo determina
    desde qué config_id vienen las órdenes, pero el match es por email/teléfono.
    """
    # Reutilizar la implementación de WooCommerce (misma tabla orders).
    from src.connectors.woocommerce.tools import historial_pedidos_contacto as _impl
    return _impl(
        limit,
        tenant_id=tenant_id,
        config_id=config_id,
        contact_id=contact_id,
        contact_email=contact_email,
        contact_phone=contact_phone,
        db=db,
    )
