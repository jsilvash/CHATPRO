"""Tool ``buscar_knowledge`` para el agente Claude (Fase 9).

El agente invoca esta función cuando necesita información de la base de
conocimiento del tenant (manuales, FAQs, políticas, catálogos, etc.).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from src.db.session import get_db_session
from src.knowledge.search import hybrid_search


@contextmanager
def _db_ctx(db):
    if db is not None:
        yield db
    else:
        with get_db_session() as session:
            yield session


def buscar_knowledge(
    query: str,
    max_results: int = 5,
    wa_number_id: str | None = None,
    *,
    tenant_id: uuid.UUID,
    db: Session | None = None,
    **_kwargs,
) -> dict:
    """Busca en la base de conocimiento del tenant por relevancia semántica + BM25.

    Devuelve fragmentos del documento más relevante con citas (chunk_id + título).
    """
    max_results = max(1, min(max_results, 10))

    parsed_number_id: uuid.UUID | None = None
    if wa_number_id:
        try:
            parsed_number_id = uuid.UUID(wa_number_id)
        except ValueError:
            pass

    with _db_ctx(db) as session:
        results = hybrid_search(
            query,
            tenant_id=tenant_id,
            wa_number_id=parsed_number_id,
            top_k=max_results,
            db=session,
        )

    if not results:
        return {
            "resultados": [],
            "mensaje": "No se encontró información relevante en la base de conocimiento.",
        }

    return {
        "resultados": [
            {
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "titulo": r.document_title,
                "tipo": r.source_type,
                "url": r.source_uri,
                "fragmento": r.content,
                "relevancia": round(r.score, 4),
            }
            for r in results
        ]
    }
