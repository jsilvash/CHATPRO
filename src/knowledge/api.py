"""API REST /v1/knowledge — gestión de la base de conocimiento (Fase 9).

Endpoints:
- POST   /v1/knowledge/upload  — sube PDF o URL; inicia ingestión síncrona.
- GET    /v1/knowledge          — lista documentos del tenant (con filtros).
- DELETE /v1/knowledge/{id}     — elimina documento y sus chunks.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.knowledge.ingestor import delete_document, ingest_pdf, ingest_url
from src.knowledge.models import KbDocument

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class KbDocumentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    wa_number_id: uuid.UUID | None
    title: str
    source_type: str
    source_uri: str | None
    status: str
    error: str | None

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=KbDocumentOut, status_code=status.HTTP_201_CREATED)
def upload_document(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    title: Annotated[str, Form()],
    wa_number_id: Annotated[str | None, Form()] = None,
    url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Sube un documento PDF o URL a la base de conocimiento.

    Exactamente uno de ``file`` o ``url`` debe estar presente.
    La ingestión (chunking + embeddings) ocurre de forma síncrona.
    """
    if file is None and url is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Se requiere 'file' (PDF) o 'url'.",
        )
    if file is not None and url is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Solo se acepta 'file' o 'url', no ambos.",
        )

    parsed_number_id: uuid.UUID | None = None
    if wa_number_id:
        try:
            parsed_number_id = uuid.UUID(wa_number_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="wa_number_id inválido.",
            )

    if file is not None:
        content_type = file.content_type or ""
        if "pdf" not in content_type and not file.filename.endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Solo se aceptan archivos PDF.",
            )
        file_bytes = file.file.read()
        doc = ingest_pdf(
            file_bytes=file_bytes,
            title=title,
            tenant_id=current_user.tenant_id,
            wa_number_id=parsed_number_id,
            db=db,
        )
    else:
        doc = ingest_url(
            url=url,
            title=title,
            tenant_id=current_user.tenant_id,
            wa_number_id=parsed_number_id,
            db=db,
        )

    return doc


@router.get("", response_model=list[KbDocumentOut])
def list_documents(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    wa_number_id: str | None = None,
    status_filter: str | None = None,
):
    """Lista los documentos de la KB del tenant autenticado."""
    q = db.query(KbDocument).filter(KbDocument.tenant_id == current_user.tenant_id)

    if wa_number_id:
        try:
            q = q.filter(KbDocument.wa_number_id == uuid.UUID(wa_number_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="wa_number_id inválido.")

    if status_filter:
        q = q.filter(KbDocument.status == status_filter)

    return q.order_by(KbDocument.created_at.desc()).all()


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_doc(
    doc_id: uuid.UUID,
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Elimina un documento y todos sus chunks."""
    found = delete_document(doc_id, current_user.tenant_id, db=db)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado.")
