"""Tareas Celery para el conector WooCommerce (Fase 6).

``embed_product`` — genera embedding semántico de un producto y lo persiste
en ``products.embedding``. Se encola desde ``webhook_handler`` y ``sync_full``
(tras crear/actualizar un producto).
"""

import logging
import uuid

from src.celery_app import celery_app
from src.config import get_settings
from src.connectors.embeddings import embed_texts
from src.connectors.models import Product
from src.db.session import get_db_session

logger = logging.getLogger(__name__)


def _build_product_text(product) -> str:
    """Construye el texto embedible desde un ORM Product."""
    parts = [
        product.name or "",
        product.description_short or "",
        product.description_long or "",
        product.sku or "",
    ]
    for cat in (product.categories or []):
        parts.append(str(cat))
    for attr_name, attr_vals in (product.attributes or {}).items():
        if isinstance(attr_vals, list):
            parts.append(f"{attr_name}: {', '.join(str(v) for v in attr_vals)}")
        else:
            parts.append(f"{attr_name}: {attr_vals}")
    return " ".join(s for s in parts if s).strip()


@celery_app.task(
    name="connectors.woocommerce.embed_product",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def embed_product(self, product_id: str, tenant_id: str, config_id: str) -> dict:
    """Genera y persiste el embedding semántico de un producto.

    Retorna ``{"ok": True}`` si tuvo éxito, ``{"ok": False, "reason": ...}`` si
    se salteó sin error (sin API key o producto no encontrado).
    """
    settings = get_settings()
    api_key = settings.voyage_api_key
    if not api_key:
        logger.warning("embed_product: VOYAGE_API_KEY no configurado, saltando.")
        return {"ok": False, "reason": "sin_api_key"}

    try:
        with get_db_session() as db:
            product = db.get(Product, uuid.UUID(product_id))
            if product is None:
                logger.warning("embed_product: producto %s no encontrado.", product_id)
                return {"ok": False, "reason": "producto_no_encontrado"}

            text = _build_product_text(product)
            if not text.strip():
                return {"ok": False, "reason": "texto_vacio"}

            vectors = embed_texts([text], api_key)
            product.embedding = vectors[0]
            db.commit()

        logger.info("embed_product: producto %s embebido OK.", product_id)
        return {"ok": True}

    except Exception as exc:
        logger.error("embed_product error para %s: %s", product_id, exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
