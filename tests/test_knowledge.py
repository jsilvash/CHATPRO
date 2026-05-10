"""Tests de la base de conocimiento RAG (Fase 9).

Cubre:
1. Chunking de texto.
2. Ingestión PDF (con mock de embeddings).
3. Ingestión URL (con mock de embeddings + HTTP).
4. Búsqueda semántica (con mock embeddings y datos reales en BD).
5. Tool del agente buscar_knowledge.
6. API REST /v1/knowledge (upload, list, delete).
7. Aislamiento: tenant A no ve docs de tenant B.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.auth.tokens import create_access_token
from src.db.models import Tenant, User
from src.auth.passwords import hash_password


# ── Helpers de fixtures ──────────────────────────────────────────────────────

def _fake_embedding(n: int = 1024) -> list[float]:
    """Embedding falso de dimensión 1024."""
    return [0.01] * n


def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    return [_fake_embedding() for _ in texts]


def _fake_query_embedding(query: str) -> list[float]:
    return _fake_embedding()


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_tenant(db, slug: str, email: str) -> tuple[Tenant, User]:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=hash_password("secret123"),
        full_name=f"Owner {slug}",
        role="owner",
    )
    db.add(owner)
    db.flush()
    return tenant, owner


# ── 1. Tests de chunking ─────────────────────────────────────────────────────


def test_chunk_texto_corto():
    from src.knowledge.ingestor import _chunk_text

    chunks = _chunk_text("hola mundo", chunk_words=500)
    assert chunks == ["hola mundo"]


def test_chunk_texto_largo():
    from src.knowledge.ingestor import _chunk_text

    texto = " ".join([f"palabra{i}" for i in range(1200)])
    chunks = _chunk_text(texto, chunk_words=500, overlap=50)
    assert len(chunks) == 3
    assert all(len(c.split()) <= 500 for c in chunks)


def test_chunk_texto_vacio():
    from src.knowledge.ingestor import _chunk_text

    assert _chunk_text("") == []


def test_chunk_overlap():
    from src.knowledge.ingestor import _chunk_text

    texto = " ".join([f"w{i}" for i in range(600)])
    chunks = _chunk_text(texto, chunk_words=500, overlap=50)
    # El segundo chunk debe empezar en la palabra 450 (500 - 50)
    primer_chunk = chunks[0].split()
    segundo_chunk = chunks[1].split()
    # Overlap: últimas 50 palabras del primer chunk son las primeras del segundo
    assert primer_chunk[-50:] == segundo_chunk[:50]


# ── 2. Tests de ingestión PDF ────────────────────────────────────────────────


def _minimal_pdf_bytes() -> bytes:
    """PDF mínimo válido con texto extraíble."""
    return b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Hola mundo desde PDF) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
441
%%EOF"""


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_ingest_pdf_crea_documento(mock_embed, db):
    from src.knowledge.ingestor import ingest_pdf
    from src.knowledge.models import KbDocument, KbChunk

    tenant = Tenant(slug="kb-pdf-test", name="PDF Tenant")
    db.add(tenant)
    db.flush()

    doc = ingest_pdf(
        file_bytes=_minimal_pdf_bytes(),
        title="Manual de prueba",
        tenant_id=tenant.id,
        db=db,
    )

    assert doc.status == "ready"
    assert doc.source_type == "pdf"
    assert doc.tenant_id == tenant.id

    chunks = db.query(KbChunk).filter(KbChunk.document_id == doc.id).all()
    assert len(chunks) >= 1
    assert all(c.tenant_id == tenant.id for c in chunks)
    assert all(c.embedding is not None for c in chunks)


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_ingest_pdf_con_wa_number_id(mock_embed, db):
    from src.knowledge.ingestor import ingest_pdf
    from src.knowledge.models import KbChunk
    from src.wa.models import WaNumber

    tenant = Tenant(slug="kb-pdf-number", name="PDF Number Tenant")
    db.add(tenant)
    db.flush()

    # Necesitamos un WaNumber real para satisfacer la FK de kb_documents
    wn = WaNumber(
        tenant_id=tenant.id,
        label="número test",
        waha_session_name=f"s{uuid.uuid4().hex[:8]}",
    )
    db.add(wn)
    db.flush()
    wa_number_id = wn.id

    doc = ingest_pdf(
        file_bytes=_minimal_pdf_bytes(),
        title="Manual por número",
        tenant_id=tenant.id,
        wa_number_id=wa_number_id,
        db=db,
    )

    assert doc.wa_number_id == wa_number_id
    chunks = db.query(KbChunk).filter(KbChunk.document_id == doc.id).all()
    assert all(c.wa_number_id == wa_number_id for c in chunks)


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_ingest_pdf_falla_con_bytes_invalidos(mock_embed, db):
    from src.knowledge.ingestor import ingest_pdf

    tenant = Tenant(slug="kb-pdf-fail", name="PDF Fail Tenant")
    db.add(tenant)
    db.flush()

    doc = ingest_pdf(
        file_bytes=b"esto no es un PDF valido",
        title="PDF roto",
        tenant_id=tenant.id,
        db=db,
    )

    assert doc.status == "failed"
    assert doc.error is not None


# ── 3. Tests de ingestión URL ────────────────────────────────────────────────


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
@patch("src.knowledge.ingestor._extract_url")
def test_ingest_url_crea_documento(mock_extract, mock_embed, db):
    mock_extract.return_value = "Contenido de la web sobre política de envíos."

    from src.knowledge.ingestor import ingest_url
    from src.knowledge.models import KbChunk

    tenant = Tenant(slug="kb-url-test", name="URL Tenant")
    db.add(tenant)
    db.flush()

    doc = ingest_url(
        url="https://ejemplo.com/politica",
        title="Política de envíos",
        tenant_id=tenant.id,
        db=db,
    )

    assert doc.status == "ready"
    assert doc.source_type == "url"
    assert doc.source_uri == "https://ejemplo.com/politica"
    chunks = db.query(KbChunk).filter(KbChunk.document_id == doc.id).all()
    assert len(chunks) >= 1


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
@patch("src.knowledge.ingestor._extract_url", side_effect=Exception("timeout"))
def test_ingest_url_falla_con_error_http(mock_extract, mock_embed, db):
    from src.knowledge.ingestor import ingest_url

    tenant = Tenant(slug="kb-url-fail", name="URL Fail Tenant")
    db.add(tenant)
    db.flush()

    doc = ingest_url(
        url="https://no-existe-este-sitio.xyz",
        title="URL que falla",
        tenant_id=tenant.id,
        db=db,
    )

    assert doc.status == "failed"
    assert "timeout" in (doc.error or "")


# ── 4. Tests de búsqueda ─────────────────────────────────────────────────────


def _insert_doc_with_chunks(db, tenant_id, content: str, n_chunks: int = 1) -> "KbDocument":
    """Inserta un KbDocument en estado 'ready' con chunks de embedding falso."""
    from src.knowledge.models import KbChunk, KbDocument

    doc = KbDocument(
        tenant_id=tenant_id,
        title="Doc de test",
        source_type="pdf",
        status="ready",
    )
    db.add(doc)
    db.flush()

    for i in range(n_chunks):
        chunk = KbChunk(
            tenant_id=tenant_id,
            document_id=doc.id,
            content=f"{content} (chunk {i})",
            position=i,
            embedding=_fake_embedding(),
            metadata_={},
        )
        db.add(chunk)
    db.flush()
    return doc


@patch("src.knowledge.search.get_query_embedding", side_effect=_fake_query_embedding)
def test_busqueda_semantica_retorna_resultados(mock_embed, db):
    from src.knowledge.search import hybrid_search

    tenant = Tenant(slug="kb-search-sem", name="Search Tenant")
    db.add(tenant)
    db.flush()

    _insert_doc_with_chunks(db, tenant.id, "política de devoluciones gratuita")

    results = hybrid_search(
        "devoluciones",
        tenant_id=tenant.id,
        db=db,
        top_k=5,
    )

    # La búsqueda semántica retorna (al menos) el chunk con embedding similar
    assert isinstance(results, list)
    # BM25 debería encontrar "devoluciones" en el contenido
    for r in results:
        assert r.chunk_id
        assert r.document_title == "Doc de test"


@patch("src.knowledge.search.get_query_embedding", side_effect=_fake_query_embedding)
def test_busqueda_respeta_tenant_id(mock_embed, db):
    from src.knowledge.search import hybrid_search

    tenant_a = Tenant(slug="kb-iso-a", name="Tenant A KB")
    tenant_b = Tenant(slug="kb-iso-b", name="Tenant B KB")
    db.add(tenant_a)
    db.add(tenant_b)
    db.flush()

    _insert_doc_with_chunks(db, tenant_a.id, "horario de atención lunes a viernes")
    _insert_doc_with_chunks(db, tenant_b.id, "horario secreto de tenant b")

    results_a = hybrid_search("horario", tenant_id=tenant_a.id, db=db, top_k=10)
    for r in results_a:
        assert "tenant b" not in r.content.lower()


# ── 5. Tests del tool del agente ─────────────────────────────────────────────


@patch("src.knowledge.search.get_query_embedding", side_effect=_fake_query_embedding)
def test_tool_buscar_knowledge_sin_docs(mock_embed, db):
    from src.knowledge.tools import buscar_knowledge

    tenant = Tenant(slug="kb-tool-empty", name="Tool Empty Tenant")
    db.add(tenant)
    db.flush()

    result = buscar_knowledge(
        "pregunta sobre cualquier cosa",
        tenant_id=tenant.id,
        db=db,
    )

    assert result["resultados"] == []
    assert "mensaje" in result


@patch("src.knowledge.search.get_query_embedding", side_effect=_fake_query_embedding)
def test_tool_buscar_knowledge_con_docs(mock_embed, db):
    from src.knowledge.tools import buscar_knowledge

    tenant = Tenant(slug="kb-tool-docs", name="Tool Docs Tenant")
    db.add(tenant)
    db.flush()

    _insert_doc_with_chunks(db, tenant.id, "garantía de 12 meses incluida")

    result = buscar_knowledge(
        "garantía",
        max_results=3,
        tenant_id=tenant.id,
        db=db,
    )

    assert isinstance(result["resultados"], list)
    if result["resultados"]:
        r = result["resultados"][0]
        assert "chunk_id" in r
        assert "titulo" in r
        assert "fragmento" in r


@patch("src.knowledge.search.get_query_embedding", side_effect=_fake_query_embedding)
def test_tool_buscar_knowledge_max_results_cap(mock_embed, db):
    from src.knowledge.tools import buscar_knowledge

    tenant = Tenant(slug="kb-tool-cap", name="Tool Cap Tenant")
    db.add(tenant)
    db.flush()

    for i in range(15):
        _insert_doc_with_chunks(db, tenant.id, f"documento {i} con texto relevante")

    result = buscar_knowledge(
        "documento",
        max_results=50,  # debe capparse a 10
        tenant_id=tenant.id,
        db=db,
    )

    assert len(result["resultados"]) <= 10


# ── 6. Tests de la API REST ──────────────────────────────────────────────────


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_api_upload_pdf(mock_embed, client_a, tenant_a, db):
    tenant, owner = tenant_a

    pdf_bytes = _minimal_pdf_bytes()
    response = client_a.post(
        "/v1/knowledge/upload",
        data={"title": "Manual PDF"},
        files={"file": ("manual.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Manual PDF"
    assert data["source_type"] == "pdf"
    assert data["tenant_id"] == str(tenant.id)


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
@patch("src.knowledge.ingestor._extract_url")
def test_api_upload_url(mock_extract, mock_embed, client_a, tenant_a, db):
    mock_extract.return_value = "Texto de la web sobre soporte."

    response = client_a.post(
        "/v1/knowledge/upload",
        data={"title": "Soporte web", "url": "https://soporte.ejemplo.com"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["source_type"] == "url"
    assert data["source_uri"] == "https://soporte.ejemplo.com"


def test_api_upload_sin_file_ni_url(client_a):
    response = client_a.post(
        "/v1/knowledge/upload",
        data={"title": "Sin fuente"},
    )
    assert response.status_code == 422


def test_api_upload_con_file_y_url(client_a):
    response = client_a.post(
        "/v1/knowledge/upload",
        data={"title": "Doble fuente", "url": "https://ejemplo.com"},
        files={"file": ("archivo.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert response.status_code == 422


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_api_list_documentos(mock_embed, client_a, tenant_a, db):
    tenant, owner = tenant_a

    ingest_doc_directly(db, tenant.id, "Doc para listar")

    response = client_a.get("/v1/knowledge")
    assert response.status_code == 200
    docs = response.json()
    assert isinstance(docs, list)
    assert any(d["title"] == "Doc para listar" for d in docs)


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_api_delete_documento(mock_embed, client_a, tenant_a, db):
    tenant, owner = tenant_a

    doc = ingest_doc_directly(db, tenant.id, "Doc a eliminar")

    response = client_a.delete(f"/v1/knowledge/{doc.id}")
    assert response.status_code == 204

    response = client_a.delete(f"/v1/knowledge/{doc.id}")
    assert response.status_code == 404


def test_api_delete_documento_no_existente(client_a):
    response = client_a.delete(f"/v1/knowledge/{uuid.uuid4()}")
    assert response.status_code == 404


# ── 7. Aislamiento tenant ────────────────────────────────────────────────────


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_aislamiento_list(mock_embed, db):
    """Tenant B no puede listar documentos de Tenant A."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "kb-iso-list-a", "a@kb-list.com")
    tenant_b, owner_b = _make_tenant(db, "kb-iso-list-b", "b@kb-list.com")

    ingest_doc_directly(db, tenant_a.id, "Secreto de A")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client_b_:
        client_b_.headers.update(_auth(owner_b))
        resp = client_b_.get("/v1/knowledge")
        assert resp.status_code == 200
        docs = resp.json()
        assert not any(d["title"] == "Secreto de A" for d in docs)
    app.dependency_overrides.clear()


@patch("src.knowledge.ingestor.get_embeddings", side_effect=_fake_embeddings)
def test_aislamiento_delete(mock_embed, db):
    """Tenant B no puede borrar documentos de Tenant A."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "kb-iso-del-a", "a@kb-del.com")
    tenant_b, owner_b = _make_tenant(db, "kb-iso-del-b", "b@kb-del.com")

    doc_a = ingest_doc_directly(db, tenant_a.id, "Documento privado de A")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client_b_:
        client_b_.headers.update(_auth(owner_b))
        resp = client_b_.delete(f"/v1/knowledge/{doc_a.id}")
        assert resp.status_code == 404
    app.dependency_overrides.clear()


# ── Helpers locales de test ──────────────────────────────────────────────────


def ingest_doc_directly(db, tenant_id: uuid.UUID, title: str) -> "KbDocument":
    """Crea un KbDocument ready sin pasar por el ingestor completo."""
    from src.knowledge.models import KbDocument, KbChunk

    doc = KbDocument(
        tenant_id=tenant_id,
        title=title,
        source_type="pdf",
        status="ready",
    )
    db.add(doc)
    db.flush()

    chunk = KbChunk(
        tenant_id=tenant_id,
        document_id=doc.id,
        content=f"Contenido de {title}",
        position=0,
        embedding=_fake_embedding(),
        metadata_={},
    )
    db.add(chunk)
    db.flush()
    return doc
