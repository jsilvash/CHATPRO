"""Tests Fase 23B — Export de datos del tenant (GDPR).

Cubre:
- POST /v1/tenants/me/export → crea job con status queued.
- GET /v1/tenants/me/export/{job_id}/status → devuelve estado.
- Estado queued / done / error.
- GET /v1/tenants/me/export/{job_id}/download → URL firmada (solo si status=done).
- Download 400 si status != done.
- Aislamiento: tenant B no puede ver ni descargar export de tenant A.
- Tarea Celery mock: exporta datos y sube a S3 mock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.billing.models import ExportJob
from src.billing.tasks import export_tenant_data, _build_zip, _rows_to_csv


# ── Helpers ──────────────────────────────────────────────────────────────────


def _token(owner):
    from src.auth.tokens import create_access_token
    return create_access_token(owner.id, owner.tenant_id, owner.role)


def _auth(owner):
    return {"Authorization": f"Bearer {_token(owner)}"}


# ── Tests de endpoints ────────────────────────────────────────────────────────


def test_crear_export_job_devuelve_202(client_a, tenant_a, db):
    """POST /v1/tenants/me/export crea job con status queued."""
    tenant, owner = tenant_a
    with patch("src.billing.api.export_tenant_data") as mock_task:
        mock_task.delay = MagicMock()
        resp = client_a.post("/v1/tenants/me/export")

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["tenant_id"] == str(tenant.id)
    assert data["storage_uri"] is None
    assert data["error"] is None


def test_status_queued(client_a, tenant_a, db):
    """GET status devuelve queued para un job recién creado."""
    tenant, owner = tenant_a
    job = ExportJob(tenant_id=tenant.id, status="queued")
    db.add(job)
    db.flush()

    resp = client_a.get(f"/v1/tenants/me/export/{job.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["finished_at"] is None


def test_status_done(client_a, tenant_a, db):
    """GET status devuelve done con finished_at cuando el job terminó."""
    tenant, owner = tenant_a
    now = datetime.now(timezone.utc)
    job = ExportJob(
        tenant_id=tenant.id,
        status="done",
        storage_uri=f"s3://chatpro-exports/exports/{tenant.id}/test.zip",
        finished_at=now,
    )
    db.add(job)
    db.flush()

    resp = client_a.get(f"/v1/tenants/me/export/{job.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["finished_at"] is not None


def test_status_error(client_a, tenant_a, db):
    """GET status devuelve error con el mensaje de error."""
    tenant, owner = tenant_a
    job = ExportJob(
        tenant_id=tenant.id,
        status="error",
        error="conexión S3 fallida",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    resp = client_a.get(f"/v1/tenants/me/export/{job.id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert "S3" in data["error"]


def test_download_400_si_no_done(client_a, tenant_a, db):
    """GET download devuelve 400 si el job no está en estado done."""
    tenant, owner = tenant_a
    job = ExportJob(tenant_id=tenant.id, status="queued")
    db.add(job)
    db.flush()

    resp = client_a.get(f"/v1/tenants/me/export/{job.id}/download")
    assert resp.status_code == 400


def test_download_url_si_done(client_a, tenant_a, db):
    """GET download devuelve URL firmada cuando el job está done."""
    tenant, owner = tenant_a
    job = ExportJob(
        tenant_id=tenant.id,
        status="done",
        storage_uri=f"s3://chatpro-exports/exports/{tenant.id}/{uuid.uuid4()}.zip",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    with patch("src.billing.api.s3_storage") as mock_s3:
        mock_s3.generate_presigned_url.return_value = "https://example.com/signed-url"
        resp = client_a.get(f"/v1/tenants/me/export/{job.id}/download")

    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://example.com/signed-url"


def test_download_404_sin_storage_uri(client_a, tenant_a, db):
    """GET download devuelve 404 si storage_uri está vacío."""
    tenant, owner = tenant_a
    job = ExportJob(
        tenant_id=tenant.id,
        status="done",
        storage_uri=None,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    resp = client_a.get(f"/v1/tenants/me/export/{job.id}/download")
    assert resp.status_code == 404


def test_aislamiento_status(client_a, client_b, tenant_a, tenant_b, db):
    """Tenant B no puede ver el status del export de Tenant A."""
    tenant_a_obj, owner_a = tenant_a
    job = ExportJob(tenant_id=tenant_a_obj.id, status="queued")
    db.add(job)
    db.flush()

    resp = client_b.get(f"/v1/tenants/me/export/{job.id}/status")
    assert resp.status_code == 404


def test_aislamiento_download(client_a, client_b, tenant_a, tenant_b, db):
    """Tenant B no puede descargar el export de Tenant A."""
    tenant_a_obj, owner_a = tenant_a
    job = ExportJob(
        tenant_id=tenant_a_obj.id,
        status="done",
        storage_uri="s3://chatpro-exports/exports/test.zip",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    resp = client_b.get(f"/v1/tenants/me/export/{job.id}/download")
    assert resp.status_code == 404


# ── Tests de tarea Celery (mock S3) ──────────────────────────────────────────


def test_celery_task_mock_completa(db, tenant_a):
    """La tarea Celery exporta datos y sube a S3 mock."""
    tenant, owner = tenant_a
    job = ExportJob(tenant_id=tenant.id, status="queued")
    db.add(job)
    db.commit()

    with patch("src.billing.tasks.s3") as mock_s3:
        mock_s3.upload_bytes.return_value = f"s3://chatpro-exports/exports/{tenant.id}/{job.id}.zip"
        # .run() llama la función directamente sin broker Celery.
        export_tenant_data.run(str(job.id))

    db.refresh(job)
    assert job.status == "done"
    assert job.storage_uri is not None
    assert job.finished_at is not None
    mock_s3.upload_bytes.assert_called_once()


def test_celery_task_mock_error_s3(db, tenant_a):
    """Si S3 falla, el job queda en status error."""
    tenant, owner = tenant_a
    job = ExportJob(tenant_id=tenant.id, status="queued")
    db.add(job)
    db.commit()

    with patch("src.billing.tasks.s3") as mock_s3:
        mock_s3.upload_bytes.side_effect = RuntimeError("S3 timeout")
        with pytest.raises(RuntimeError):
            export_tenant_data.run(str(job.id))

    db.refresh(job)
    assert job.status == "error"
    assert "S3 timeout" in job.error


# ── Tests unitarios de helpers ────────────────────────────────────────────────


def test_rows_to_csv_vacio():
    result = _rows_to_csv([])
    assert result == b""


def test_rows_to_csv_con_datos():
    rows = [{"a": 1, "b": "hola"}, {"a": 2, "b": "mundo"}]
    csv_bytes = _rows_to_csv(rows)
    assert b"a,b" in csv_bytes
    assert b"hola" in csv_bytes


def test_build_zip_genera_zip():
    import zipfile, io
    files = {"test.csv": b"id,name\n1,foo\n"}
    result = _build_zip(files)
    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        names = zf.namelist()
    assert "test.csv" in names
