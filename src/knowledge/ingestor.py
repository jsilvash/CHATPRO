"""Ingestor de documentos para la KB (Fase 9).

Soporta:
- PDF  — via ``pypdf``
- URL  — via ``httpx`` + ``html2text``

Pipeline por documento:
1. Extraer texto plano.
2. Chunkear en fragmentos de ~500 palabras con overlap de 50.
3. Embeddings con Voyage AI voyage-3 (1024 dims).
4. Persistir KbDocument + KbChunks.
"""

from __future__ import annotations

import io
import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import html2text
import httpx
from pypdf import PdfReader
from sqlalchemy.orm import Session

from src.db.session import get_db_session
from src.knowledge.embeddings import get_embeddings
from src.knowledge.models import KbChunk, KbDocument

logger = logging.getLogger(__name__)

_CHUNK_WORDS = 500
_CHUNK_OVERLAP = 50
_REQUEST_TIMEOUT = 30.0


# ── Chunker ─────────────────────────────────────────────────────────────────


def _chunk_text(text: str, chunk_words: int = _CHUNK_WORDS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Divide ``text`` en fragmentos de ~``chunk_words`` palabras con overlap."""
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_words - overlap

    return chunks


# ── Extractores de texto ─────────────────────────────────────────────────────


def _extract_pdf(file_bytes: bytes) -> str:
    """Extrae texto de un PDF en bytes."""
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n\n".join(pages)


def _extract_url(url: str) -> str:
    """Descarga una URL y convierte el HTML a texto plano."""
    with httpx.Client(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "ChatPro-RAG/1.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.ignore_images = True
            return converter.handle(response.text)
        return response.text


# ── Pipeline principal ───────────────────────────────────────────────────────


@contextmanager
def _db_ctx(db):
    if db is not None:
        yield db
    else:
        with get_db_session() as session:
            yield session


def ingest_pdf(
    *,
    file_bytes: bytes,
    title: str,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID | None = None,
    db: Session | None = None,
) -> KbDocument:
    """Ingesta un PDF en la KB. Devuelve el KbDocument creado."""
    with _db_ctx(db) as session:
        doc = KbDocument(
            tenant_id=tenant_id,
            wa_number_id=wa_number_id,
            title=title,
            source_type="pdf",
            status="processing",
        )
        session.add(doc)
        session.flush()

        try:
            text = _extract_pdf(file_bytes)
            _index_text(text, doc=doc, session=session)
            doc.status = "ready"
        except Exception as exc:
            logger.exception("Error ingestando PDF '%s'", title)
            doc.status = "failed"
            doc.error = str(exc)[:500]

        session.flush()
        return doc


def ingest_url(
    *,
    url: str,
    title: str | None = None,
    tenant_id: uuid.UUID,
    wa_number_id: uuid.UUID | None = None,
    db: Session | None = None,
) -> KbDocument:
    """Ingesta una URL en la KB. Devuelve el KbDocument creado."""
    with _db_ctx(db) as session:
        doc = KbDocument(
            tenant_id=tenant_id,
            wa_number_id=wa_number_id,
            title=title or url,
            source_type="url",
            source_uri=url,
            status="processing",
        )
        session.add(doc)
        session.flush()

        try:
            text = _extract_url(url)
            _index_text(text, doc=doc, session=session)
            doc.status = "ready"
        except Exception as exc:
            logger.exception("Error ingestando URL '%s'", url)
            doc.status = "failed"
            doc.error = str(exc)[:500]

        session.flush()
        return doc


def _index_text(text: str, *, doc: KbDocument, session: Session) -> None:
    """Chunkea, embede y persiste los KbChunk de un documento."""
    chunks = _chunk_text(text)
    if not chunks:
        return

    embeddings = get_embeddings(chunks)

    for position, (content, embedding) in enumerate(zip(chunks, embeddings)):
        chunk = KbChunk(
            tenant_id=doc.tenant_id,
            document_id=doc.id,
            wa_number_id=doc.wa_number_id,
            content=content,
            position=position,
            embedding=embedding,
            metadata_={"doc_title": doc.title, "source_type": doc.source_type},
        )
        session.add(chunk)

    session.flush()


def delete_document(
    doc_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    db: Session | None = None,
) -> bool:
    """Elimina un documento y sus chunks. Retorna True si existía."""
    with _db_ctx(db) as session:
        doc = (
            session.query(KbDocument)
            .filter(KbDocument.id == doc_id, KbDocument.tenant_id == tenant_id)
            .first()
        )
        if doc is None:
            return False
        session.delete(doc)
        session.flush()
        return True
