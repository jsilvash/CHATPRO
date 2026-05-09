"""Generación de embeddings para catálogo de productos.

Provider primario:  Voyage AI voyage-3 (1024 dims) via VOYAGE_API_KEY.
Fallback:           OpenAI text-embedding-3-small (truncado a 1024) via OPENAI_API_KEY.
"""

import logging
import os

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def generate_embedding(text: str) -> list[float]:
    """Genera embedding de texto (1024 dims).

    Requiere VOYAGE_API_KEY o OPENAI_API_KEY en el entorno.
    En tests se mockea esta función directamente.
    """
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    if voyage_key:
        return _embed_voyage(text, voyage_key)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return _embed_openai(text, openai_key)

    raise RuntimeError(
        "Sin proveedor de embeddings. Definí VOYAGE_API_KEY o OPENAI_API_KEY."
    )


def _embed_voyage(text: str, api_key: str) -> list[float]:
    import voyageai

    client = voyageai.Client(api_key=api_key)
    result = client.embed([text], model="voyage-3")
    return result.embeddings[0]


def _embed_openai(text: str, api_key: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.embeddings.create(input=text, model="text-embedding-3-small")
    vec = resp.data[0].embedding
    # text-embedding-3-small devuelve 1536 dims; truncamos a EMBEDDING_DIM
    return vec[:EMBEDDING_DIM]
