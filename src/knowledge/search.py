"""Búsqueda híbrida RAG: semántica (pgvector cosine) + BM25 (ts_rank).

Estrategia RRF (Reciprocal Rank Fusion):
- Semántica: cosine similarity con el embedding de la query.
- BM25: ts_rank sobre columna ``content_tsv``.
- Fusión: score_rrf = 1/(k + rank_sem) + 1/(k + rank_bm25), k=60.
- Si pgvector no está disponible se degrada a BM25 puro.

El filtro ``wa_number_id`` es opcional:
- Si se pasa → solo chunks de ese número O de documentos sin número (wa_number_id IS NULL).
- Si no se pasa → todos los chunks del tenant.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.knowledge.embeddings import get_query_embedding

logger = logging.getLogger(__name__)

_RRF_K = 60
_DEFAULT_TOP_K = 5
_SEM_CANDIDATES = 20  # candidatos semánticos antes de fusionar
_BM25_CANDIDATES = 20


@dataclass
class KnowledgeResult:
    chunk_id: str
    document_id: str
    document_title: str
    source_type: str
    source_uri: str | None
    content: str
    score: float
    position: int


def hybrid_search(
    query: str,
    *,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID | None = None,
    top_k: int = _DEFAULT_TOP_K,
    db: Session,
) -> list[KnowledgeResult]:
    """Búsqueda híbrida semántica + BM25 con RRF."""
    try:
        query_vec = get_query_embedding(query)
        sem_results = _semantic_search(
            query_vec, tenant_id=tenant_id, wa_number_id=wa_number_id,
            limit=_SEM_CANDIDATES, db=db,
        )
    except Exception:
        logger.warning("Búsqueda semántica falló, usando solo BM25", exc_info=True)
        sem_results = []

    bm25_results = _bm25_search(
        query, tenant_id=tenant_id, wa_number_id=wa_number_id,
        limit=_BM25_CANDIDATES, db=db,
    )

    return _rrf_merge(sem_results, bm25_results, top_k=top_k)


# ── Semántica ────────────────────────────────────────────────────────────────


def _build_number_filter(wa_number_id: uuid.UUID | None) -> str:
    if wa_number_id is None:
        return ""
    return "AND (c.wa_number_id = :wa_number_id OR c.wa_number_id IS NULL)"


def _semantic_search(
    query_vec: list[float],
    *,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID | None,
    limit: int,
    db: Session,
) -> list[KnowledgeResult]:
    number_filter = _build_number_filter(wa_number_id)
    sql = text(f"""
        SELECT
            c.id::text                    AS chunk_id,
            c.document_id::text           AS document_id,
            d.title                       AS document_title,
            d.source_type,
            d.source_uri,
            c.content,
            c.position,
            1 - (c.embedding <=> CAST(:query_vec AS vector)) AS score
        FROM kb_chunks c
        JOIN kb_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tenant_id
          AND d.status = 'ready'
          {number_filter}
        ORDER BY c.embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
    """)

    params: dict = {
        "query_vec": str(query_vec),
        "tenant_id": str(tenant_id),
        "limit": limit,
    }
    if wa_number_id is not None:
        params["wa_number_id"] = str(wa_number_id)

    rows = db.execute(sql, params).fetchall()
    return [
        KnowledgeResult(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            document_title=r.document_title,
            source_type=r.source_type,
            source_uri=r.source_uri,
            content=r.content,
            score=float(r.score),
            position=r.position,
        )
        for r in rows
    ]


# ── BM25 ─────────────────────────────────────────────────────────────────────


def _bm25_search(
    query: str,
    *,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID | None,
    limit: int,
    db: Session,
) -> list[KnowledgeResult]:
    number_filter = _build_number_filter(wa_number_id)
    sql = text(f"""
        SELECT
            c.id::text                                             AS chunk_id,
            c.document_id::text                                    AS document_id,
            d.title                                                AS document_title,
            d.source_type,
            d.source_uri,
            c.content,
            c.position,
            ts_rank(c.content_tsv, plainto_tsquery('spanish', :query)) AS score
        FROM kb_chunks c
        JOIN kb_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tenant_id
          AND d.status = 'ready'
          AND c.content_tsv @@ plainto_tsquery('spanish', :query)
          {number_filter}
        ORDER BY score DESC
        LIMIT :limit
    """)

    params: dict = {
        "query": query,
        "tenant_id": str(tenant_id),
        "limit": limit,
    }
    if wa_number_id is not None:
        params["wa_number_id"] = str(wa_number_id)

    rows = db.execute(sql, params).fetchall()
    return [
        KnowledgeResult(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            document_title=r.document_title,
            source_type=r.source_type,
            source_uri=r.source_uri,
            content=r.content,
            score=float(r.score),
            position=r.position,
        )
        for r in rows
    ]


# ── RRF Fusion ───────────────────────────────────────────────────────────────


def _rrf_merge(
    sem: list[KnowledgeResult],
    bm25: list[KnowledgeResult],
    top_k: int,
) -> list[KnowledgeResult]:
    scores: dict[str, float] = {}
    index: dict[str, KnowledgeResult] = {}

    for rank, item in enumerate(sem):
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        index[item.chunk_id] = item

    for rank, item in enumerate(bm25):
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        index[item.chunk_id] = item

    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)[:top_k]
    results = []
    for cid in sorted_ids:
        item = index[cid]
        item.score = scores[cid]
        results.append(item)
    return results
