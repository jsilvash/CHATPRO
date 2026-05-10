"""Cliente HTTP liviano para Voyage AI embeddings (voyage-3, 1024 dims)."""

import logging

import httpx

logger = logging.getLogger(__name__)

_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_MODEL = "voyage-3"


def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    """Devuelve una lista de embeddings (1024 dims) para cada texto recibido."""
    if not texts:
        return []
    response = httpx.post(
        _VOYAGE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": _MODEL, "input": texts, "input_type": "document"},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return [item["embedding"] for item in data["data"]]
