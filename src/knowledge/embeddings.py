"""Cliente de embeddings con Voyage AI voyage-3 (1024 dims).

En tests se mockea ``get_embeddings`` para evitar llamadas reales a la API.
"""

from __future__ import annotations

import logging

import voyageai

from src.config import get_settings

logger = logging.getLogger(__name__)

_MODEL = "voyage-3"
_BATCH_SIZE = 128  # máximo de textos por llamada


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Devuelve embeddings de 1024 dims para cada texto.

    Procesa en lotes de ``_BATCH_SIZE`` para respetar límites de la API.
    """
    if not texts:
        return []

    settings = get_settings()
    client = voyageai.Client(api_key=settings.voyage_api_key)

    results: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        response = client.embed(batch, model=_MODEL, input_type="document")
        results.extend(response.embeddings)

    return results


def get_query_embedding(query: str) -> list[float]:
    """Embedding de una query (input_type='query' para retrieval)."""
    settings = get_settings()
    client = voyageai.Client(api_key=settings.voyage_api_key)
    response = client.embed([query], model=_MODEL, input_type="query")
    return response.embeddings[0]
