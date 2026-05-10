"""Tareas Celery de billing — exportación GDPR (Fase 23B).

Tarea principal:
- export_tenant_data(job_id): exporta contacts, wa_messages, orders,
  contact_facts a CSV individuales, los empaqueta en ZIP y sube a S3/MinIO.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
import zipfile
from datetime import datetime, timezone

from src.billing import storage as s3
from src.billing.models import ExportJob
from src.celery_app import celery_app
from src.contacts.models import Contact, ContactFact
from src.db.session import get_db_session
from src.tenancy.context import bypass_tenant_filter

logger = logging.getLogger(__name__)


def _rows_to_csv(rows: list[dict]) -> bytes:
    """Serializa una lista de dicts a CSV en bytes UTF-8."""
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _export_contacts(db, tenant_id: uuid.UUID) -> bytes:
    contacts = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant_id)
        .all()
    )
    rows = [
        {
            "id": str(c.id),
            "phone_e164": c.phone_e164,
            "display_name": c.display_name or "",
            "email": c.email or "",
            "created_at": c.created_at.isoformat(),
        }
        for c in contacts
    ]
    return _rows_to_csv(rows)


def _export_contact_facts(db, tenant_id: uuid.UUID) -> bytes:
    facts = (
        db.query(ContactFact)
        .filter(ContactFact.tenant_id == tenant_id)
        .all()
    )
    rows = [
        {
            "id": str(f.id),
            "contact_id": str(f.contact_id),
            "key": f.key,
            "value_text": f.value_text or "",
            "value_type": f.value_type,
            "source": f.source,
            "confidence": str(f.confidence) if f.confidence is not None else "",
            "created_at": f.created_at.isoformat(),
        }
        for f in facts
    ]
    return _rows_to_csv(rows)


def _export_wa_messages(db, tenant_id: uuid.UUID) -> bytes:
    from src.wa.models import WaMessage
    msgs = (
        db.query(WaMessage)
        .filter(WaMessage.tenant_id == tenant_id)
        .order_by(WaMessage.created_at)
        .all()
    )
    rows = [
        {
            "id": str(m.id),
            "conversation_id": str(m.wa_conversation_id),
            "direction": m.direction,
            "text": m.text or "",
            "ack": m.ack or "",
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]
    return _rows_to_csv(rows)


def _export_orders(db, tenant_id: uuid.UUID) -> bytes:
    from src.connectors.models import Order
    orders = (
        db.query(Order)
        .filter(Order.tenant_id == tenant_id)
        .all()
    )
    rows = [
        {
            "id": str(o.id),
            "external_id": o.external_id,
            "status": o.status or "",
            "total": str(o.total) if o.total is not None else "",
            "currency": o.currency or "",
            "customer_email": o.customer_email or "",
            "created_at": o.created_at.isoformat(),
        }
        for o in orders
    ]
    return _rows_to_csv(rows)


def _build_zip(files: dict[str, bytes]) -> bytes:
    """Empaqueta un dict {filename: bytes} en un ZIP en memoria."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@celery_app.task(name="billing.export_tenant_data", bind=True, max_retries=3)
def export_tenant_data(self, job_id: str) -> None:
    """Exporta todos los datos del tenant a un ZIP y lo sube a S3/MinIO."""
    job_uuid = uuid.UUID(job_id)

    with get_db_session() as db:
        with bypass_tenant_filter():
            job = db.query(ExportJob).filter(ExportJob.id == job_uuid).first()

        if job is None:
            logger.error("export_tenant_data: job %s no encontrado", job_id)
            return

        job.status = "running"
        db.flush()

        try:
            tenant_id = job.tenant_id
            files = {
                "contacts.csv": _export_contacts(db, tenant_id),
                "contact_facts.csv": _export_contact_facts(db, tenant_id),
                "wa_messages.csv": _export_wa_messages(db, tenant_id),
                "orders.csv": _export_orders(db, tenant_id),
            }
            zip_bytes = _build_zip(files)

            s3_key = f"exports/{tenant_id}/{job_uuid}.zip"
            storage_uri = s3.upload_bytes(zip_bytes, s3_key)

            job.status = "done"
            job.storage_uri = storage_uri
            job.finished_at = datetime.now(timezone.utc)

        except Exception as exc:
            logger.exception("export_tenant_data: fallo en job %s: %s", job_id, exc)
            job.status = "error"
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            raise
