"""Tools invocables por el agente Claude para el conector WooCommerce.

Cada función recibe el contexto (tenant_id, config_id) y retorna un dict
que se envía como ``tool_result`` al agente.
"""

import uuid

from src.connectors.models import ConnectorConfig, Product
from src.db.session import get_db_session


def buscar_productos(
    query: str,
    max_results: int = 5,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
) -> dict:
    """Busca productos en el catálogo local por texto libre."""
    max_results = max(1, min(max_results, 20))

    with get_db_session() as db:
        products = (
            db.query(Product)
            .filter(
                Product.tenant_id == tenant_id,
                Product.connector_config_id == config_id,
                Product.deleted_at.is_(None),
                Product.name.ilike(f"%{query}%")
                | Product.description_short.ilike(f"%{query}%")
                | Product.sku.ilike(f"%{query}%"),
            )
            .limit(max_results)
            .all()
        )

        if not products:
            return {"resultados": [], "mensaje": "No se encontraron productos."}

        return {
            "resultados": [
                {
                    "id": str(p.id),
                    "external_id": p.external_id,
                    "nombre": p.name,
                    "sku": p.sku,
                    "precio": float(p.price_sale or p.price_regular or 0),
                    "moneda": p.currency or "USD",
                    "stock": p.stock_quantity,
                    "stock_status": p.stock_status,
                    "url": p.url,
                    "descripcion": p.description_short or "",
                }
                for p in products
            ]
        }


def consultar_stock_y_precio(
    sku: str | None = None,
    product_id: str | None = None,
    *,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
) -> dict:
    """Retorna stock, precio y link de un producto por SKU o ID externo."""
    if not sku and not product_id:
        return {"error": "Se requiere sku o product_id"}

    with get_db_session() as db:
        q = db.query(Product).filter(
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
    contact_email: str | None = None,
    contact_phone: str | None = None,
) -> dict:
    """Retorna historial de órdenes del contacto.

    En Fase 5 la tabla ``orders`` no existe aún — se devuelve un mensaje
    orientativo. Fase 6 implementará la tabla y el lookup por email/teléfono.
    """
    return {
        "mensaje": (
            "El historial de pedidos estará disponible en la próxima actualización del sistema. "
            "Para consultar el estado de un pedido, pedí el número de orden al cliente."
        )
    }
