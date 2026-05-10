"""
Funciones invocables por el agente Claude para el conector WooCommerce.

Cada función recibe los parámetros del tool_input como kwargs posicionales,
más parámetros de contexto inyectados por el executor (connector, session, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from src.connectors.models import Product

if TYPE_CHECKING:
    from src.connectors.woocommerce.connector import WooCommerceConnector
    from sqlalchemy.orm import Session


def buscar_productos(
    query: str,
    max_results: int = 5,
    *,
    connector: WooCommerceConnector,
    **_: Any,
) -> list[dict[str, Any]]:
    """Busca productos en el catálogo usando búsqueda híbrida."""
    results = connector.search(query, top_k=max_results)
    return [
        {
            "id": r.id,
            "nombre": r.title,
            "precio": r.metadata.get("price"),
            "stock_status": r.metadata.get("stock_status"),
            "url": r.url,
            "descripcion": r.snippet,
        }
        for r in results
    ]


def consultar_stock_y_precio(
    sku: str | None = None,
    product_id: int | None = None,
    *,
    connector: WooCommerceConnector,
    session: Session,
    **_: Any,
) -> dict[str, Any]:
    """Consulta stock y precio de un producto por SKU o ID externo."""
    if not sku and not product_id:
        return {"error": "Se requiere sku o product_id"}

    q = select(Product).where(
        Product.tenant_id == connector.tenant_id,
        Product.connector_config_id == connector.config_id,
        Product.deleted_at.is_(None),
    )
    if sku:
        q = q.where(Product.sku == sku)
    else:
        q = q.where(Product.external_id == str(product_id))

    product = session.scalar(q)
    if product is None:
        identifier = f"SKU={sku}" if sku else f"ID={product_id}"
        return {"error": f"Producto no encontrado ({identifier})"}

    return {
        "id": str(product.id),
        "nombre": product.name,
        "sku": product.sku,
        "precio": str(product.price_regular) if product.price_regular is not None else None,
        "precio_oferta": str(product.price_sale) if product.price_sale is not None else None,
        "stock_cantidad": product.stock_quantity,
        "stock_status": product.stock_status,
        "url": product.url,
    }


def historial_pedidos_contacto(
    limit: int = 5,
    *,
    connector: WooCommerceConnector,
    contact_phone: str | None = None,
    contact_email: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Lista los pedidos del contacto actual buscando en WooCommerce por teléfono/email."""
    if not contact_phone and not contact_email:
        return [{"error": "Se requiere contact_phone o contact_email para buscar pedidos"}]

    creds = connector._get_creds()
    params: dict[str, Any] = {
        "per_page": max(1, min(limit, 20)),
        "orderby": "date",
        "order": "desc",
    }
    if contact_email:
        params["customer"] = contact_email
    elif contact_phone:
        params["search"] = contact_phone

    try:
        with httpx.Client(
            base_url=creds["site_url"].rstrip("/"),
            auth=(creds["consumer_key"], creds["consumer_secret"]),
            timeout=10.0,
        ) as client:
            resp = client.get("/wc/v3/orders", params=params)
            if resp.status_code != 200:
                return [{"error": f"HTTP {resp.status_code} al consultar pedidos"}]

            return [
                {
                    "id": order.get("id"),
                    "estado": order.get("status"),
                    "total": order.get("total"),
                    "moneda": order.get("currency"),
                    "fecha": order.get("date_created"),
                    "items": [
                        {
                            "nombre": li.get("name"),
                            "cantidad": li.get("quantity"),
                            "total": li.get("total"),
                        }
                        for li in (order.get("line_items") or [])
                    ],
                }
                for order in resp.json()
            ]
    except Exception as exc:
        return [{"error": str(exc)}]
