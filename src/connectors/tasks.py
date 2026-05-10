"""Helpers de embeddings de productos (Fase 6).

``embed_product_sync`` es la versión síncrona usada desde:
  - el webhook handler (llamada directa, inline)
  - tests (sesión inyectada con rollback)
  - el task Celery (wrapping simple)

Cuando el catálogo tenga miles de productos la llamada se puede mover
a una cola Celery; el código aquí sirve de núcleo de ambas vías.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


def _build_product_text(product) -> str:
    """Construye el texto que se embebe para un producto."""
    parts = [
        product.name or "",
        product.description_short or "",
        product.description_long or "",
    ]
    return " ".join(p for p in parts if p).strip()


def embed_product_sync(product_id: uuid.UUID, db=None) -> bool:
    """Calcula el embedding del producto y lo persiste en ``products.embedding``.

    Retorna True si se computó y guardó el embedding, False si el producto
    no existe o el texto está vacío.

    Si ``db`` se pasa, se reutiliza la sesión (para tests con rollback).
    Si no, se abre una sesión propia con commit al final.
    """
    from src.connectors.models import Product
    from src.db.session import get_db_session
    from src.knowledge.embeddings import get_embeddings

    def _run(session) -> bool:
        product = session.get(Product, product_id)
        if product is None:
            logger.warning("embed_product_sync: producto %s no encontrado", product_id)
            return False

        text = _build_product_text(product)
        if not text:
            logger.debug("embed_product_sync: producto %s sin texto, skip", product_id)
            return False

        try:
            embeddings = get_embeddings([text])
            product.embedding = embeddings[0]
        except Exception:
            logger.exception("embed_product_sync: error al generar embedding para %s", product_id)
            return False

        if db is None:
            session.commit()
        else:
            session.flush()
        return True

    if db is not None:
        return _run(db)
    with get_db_session() as session:
        return _run(session)


def embed_product(product_id: str) -> None:
    """Punto de entrada para Celery task (o llamada directa sin sesión)."""
    embed_product_sync(uuid.UUID(product_id))
