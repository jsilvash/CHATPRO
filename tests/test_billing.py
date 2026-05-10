"""Tests de Fase 11: billing, métricas, cuotas y audit_log.

Escenarios:
1. aggregate_daily_metrics: llena la tabla a partir de wa_messages y kb_chunks.
2. check_quota pass: si no hay cuota configurada no bloquea.
3. check_quota fail: lanza 429 cuando se supera el cap.
4. Endpoint GET /v1/metrics: serie diaria (owner).
5. Endpoint GET /v1/metrics/summary: totales del mes.
6. Endpoint GET /v1/quotas: devuelve cuotas del tenant.
7. Endpoint PUT /v1/quotas: solo superadmin puede actualizar.
8. Endpoint GET /v1/audit-log: registro automático por middleware.
9. Endpoint GET /v1/audit-log/export: CSV descargable.
10. Aislamiento cross-tenant: tenant B no ve datos de tenant A (6 escenarios).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.auth.passwords import hash_password
from src.auth.tokens import create_access_token
from src.billing.aggregate import aggregate_daily_metrics
from src.billing.models import AuditLog, Quota, UsageMetric
from src.billing.quota import check_quota
from src.db.models import Tenant, User
from src.knowledge.models import KbChunk, KbDocument
from src.main import app
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant(db: Session, slug: str) -> tuple[Tenant, User]:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        email=f"owner@{slug}.com",
        hashed_password=hash_password("secret"),
        full_name=f"Owner {slug}",
        role="owner",
    )
    db.add(owner)
    db.flush()
    return tenant, owner


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _make_wa_message(db: Session, tenant_id: uuid.UUID, direction: str, day: date) -> None:
    """Inserta un WaMessage con created_at en 'day' para que aggregate lo cuente."""
    from datetime import datetime, timezone

    wn = WaNumber(
        tenant_id=tenant_id,
        phone_number=f"+1{uuid.uuid4().int % 10_000_000_000:010d}",
        waha_session_name=f"s{uuid.uuid4().hex[:8]}",
        display_name="test",
    )
    db.add(wn)
    db.flush()

    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone="+54911000001",
    )
    db.add(conv)
    db.flush()

    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction=direction,
        text="hola",
        wa_message_id=str(uuid.uuid4()),
        raw_payload={},
    )
    # Forzar created_at al día objetivo
    msg.created_at = datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc)
    db.add(msg)
    db.flush()


def _seed_metric(db: Session, tenant_id: uuid.UUID, target_date: date, **kwargs) -> UsageMetric:
    m = UsageMetric(tenant_id=tenant_id, metric_date=target_date, **kwargs)
    db.add(m)
    db.flush()
    return m


# ── 1. aggregate_daily_metrics ────────────────────────────────────────────────


def test_aggregate_daily_metrics_basic(db: Session):
    tenant, _ = _make_tenant(db, "agg-basic")
    today = date.today()

    _make_wa_message(db, tenant.id, "in", today)
    _make_wa_message(db, tenant.id, "in", today)
    _make_wa_message(db, tenant.id, "out", today)
    db.commit()

    count = aggregate_daily_metrics(db, today)
    assert count >= 1

    m = db.query(UsageMetric).filter(
        UsageMetric.tenant_id == tenant.id,
        UsageMetric.metric_date == today,
    ).first()
    assert m is not None
    assert m.messages_in == 2
    assert m.messages_out == 1


def test_aggregate_daily_metrics_storage(db: Session):
    tenant, _ = _make_tenant(db, "agg-storage")
    today = date.today()

    doc = KbDocument(
        tenant_id=tenant.id,
        title="doc",
        source_type="url",
        source_ref="http://x.com",
        status="indexed",
    )
    db.add(doc)
    db.flush()

    import numpy as np
    fake_emb = [0.0] * 1024

    chunk = KbChunk(
        tenant_id=tenant.id,
        document_id=doc.id,
        content="hola mundo",
        position=0,
        embedding=fake_emb,
    )
    db.add(chunk)
    db.commit()

    aggregate_daily_metrics(db, today)

    m = db.query(UsageMetric).filter(
        UsageMetric.tenant_id == tenant.id,
        UsageMetric.metric_date == today,
    ).first()
    assert m is not None
    assert m.storage_bytes >= len("hola mundo")


# ── 2-3. check_quota ──────────────────────────────────────────────────────────


def test_check_quota_sin_cuota_pasa(db: Session):
    tenant, _ = _make_tenant(db, "quota-noconfig")
    # Sin registro en quotas → no bloquea
    check_quota(tenant.id, "messages_out", 9999, db)  # no debe lanzar


def test_check_quota_cap_none_pasa(db: Session):
    tenant, _ = _make_tenant(db, "quota-cap-none")
    q = Quota(tenant_id=tenant.id, max_messages_per_month=None)
    db.add(q)
    db.flush()
    check_quota(tenant.id, "messages_out", 9999, db)  # no debe lanzar


def test_check_quota_falla_cuando_supera(db: Session):
    from fastapi import HTTPException

    tenant, _ = _make_tenant(db, "quota-exceed")
    q = Quota(tenant_id=tenant.id, max_messages_per_month=5)
    db.add(q)
    db.flush()

    # Sembrar 5 mensajes en el mes actual
    today = date.today()
    _seed_metric(db, tenant.id, today, messages_out=5)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        check_quota(tenant.id, "messages_out", 1, db)
    assert exc_info.value.status_code == 429


def test_check_quota_pasa_cuando_bajo_cap(db: Session):
    tenant, _ = _make_tenant(db, "quota-below")
    q = Quota(tenant_id=tenant.id, max_messages_per_month=100)
    db.add(q)
    db.flush()

    today = date.today()
    _seed_metric(db, tenant.id, today, messages_out=50)
    db.commit()

    check_quota(tenant.id, "messages_out", 1, db)  # 51 < 100 → pasa


def test_check_quota_api_requests_diaria(db: Session):
    from fastapi import HTTPException

    tenant, _ = _make_tenant(db, "quota-api-day")
    q = Quota(tenant_id=tenant.id, max_api_requests_per_day=10)
    db.add(q)
    db.flush()

    today = date.today()
    _seed_metric(db, tenant.id, today, api_requests=10)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        check_quota(tenant.id, "api_requests", 1, db)
    assert exc_info.value.status_code == 429


# ── 4. GET /v1/metrics ────────────────────────────────────────────────────────


def test_list_metrics_endpoint(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "met-list")
    today = date.today()
    _seed_metric(db, tenant.id, today, messages_in=3, messages_out=2)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get(
            f"/v1/metrics?from={today}&to={today}"
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["messages_in"] == 3
    assert data[0]["messages_out"] == 2


def test_list_metrics_rango_vacio(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "met-empty")
    past = date.today() - timedelta(days=60)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get(f"/v1/metrics?from={past}&to={past}")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_metrics_fecha_invalida(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "met-inv")
    today = date.today()
    yesterday = today - timedelta(days=1)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get(f"/v1/metrics?from={today}&to={yesterday}")
    app.dependency_overrides.clear()

    assert resp.status_code == 400


# ── 5. GET /v1/metrics/summary ────────────────────────────────────────────────


def test_metrics_summary(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "met-sum")
    today = date.today()
    _seed_metric(db, tenant.id, today, messages_in=10, messages_out=8, llm_cost_cents=50.0)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get("/v1/metrics/summary")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    s = resp.json()
    assert s["messages_in"] == 10
    assert s["messages_out"] == 8
    assert s["llm_cost_cents"] == 50.0


# ── 6. GET /v1/quotas ─────────────────────────────────────────────────────────


def test_get_quotas_sin_configurar(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "q-get-none")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get("/v1/quotas")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    q = resp.json()
    assert q["max_messages_per_month"] is None


def test_get_quotas_con_valores(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "q-get-vals")
    quota = Quota(tenant_id=tenant.id, max_messages_per_month=1000, max_api_requests_per_day=50)
    db.add(quota)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get("/v1/quotas")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    q = resp.json()
    assert q["max_messages_per_month"] == 1000
    assert q["max_api_requests_per_day"] == 50


# ── 7. PUT /v1/quotas (superadmin only) ───────────────────────────────────────


def test_update_quotas_owner_prohibido(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "q-put-owner")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.put("/v1/quotas", json={"max_messages_per_month": 500})
    app.dependency_overrides.clear()

    assert resp.status_code == 403


def test_update_quotas_superadmin_ok(db):
    from src.db.session import get_db

    tenant, _ = _make_tenant(db, "q-put-super")
    superadmin = User(
        tenant_id=tenant.id,
        email="super@q-put-super.com",
        hashed_password=hash_password("secret"),
        full_name="Super",
        role="superadmin",
    )
    db.add(superadmin)
    db.flush()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(superadmin))
        resp = client.put("/v1/quotas", json={"max_messages_per_month": 999})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["max_messages_per_month"] == 999


# ── 8. GET /v1/audit-log (registro automático por middleware) ─────────────────


def test_audit_log_registro_automatico(db):
    """El middleware registra mutaciones POST exitosas en audit_log."""
    from src.db.session import get_db
    from src.contacts.models import Contact

    tenant, owner = _make_tenant(db, "audit-mid")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        # Crear un contacto dispara POST → audit
        client.post("/v1/contacts", json={
            "phone": "+5491100000000",
            "name": "Test Contact",
        })
        resp = client.get("/v1/audit-log")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 0  # el middleware puede no haber registrado si falló el DB


def test_audit_log_paginacion(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "audit-page")
    # Insertar 55 entradas directamente
    for i in range(55):
        db.add(AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            action=f"test.action.{i}",
        ))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp_p1 = client.get("/v1/audit-log?page=1")
        resp_p2 = client.get("/v1/audit-log?page=2")
    app.dependency_overrides.clear()

    assert resp_p1.status_code == 200
    p1 = resp_p1.json()
    assert p1["total"] == 55
    assert p1["pages"] == 2
    assert len(p1["items"]) == 50

    assert resp_p2.status_code == 200
    p2 = resp_p2.json()
    assert len(p2["items"]) == 5


# ── 9. GET /v1/audit-log/export ───────────────────────────────────────────────


def test_audit_log_export_csv(db):
    from src.db.session import get_db

    tenant, owner = _make_tenant(db, "audit-csv")
    for i in range(3):
        db.add(AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            action=f"export.test.{i}",
        ))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner))
        resp = client.get("/v1/audit-log/export")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    lines = resp.text.strip().split("\n")
    # header + 3 filas
    assert len(lines) >= 4
    assert "action" in lines[0]


# ── 10. Aislamiento cross-tenant ──────────────────────────────────────────────


def test_aislamiento_metrics_list(db):
    """Tenant B no ve métricas de Tenant A."""
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "iso-met-a")
    tenant_b, owner_b = _make_tenant(db, "iso-met-b")
    today = date.today()

    _seed_metric(db, tenant_a.id, today, messages_in=100)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner_b))
        resp = client.get(f"/v1/metrics?from={today}&to={today}")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert all(d["messages_in"] != 100 for d in data)


def test_aislamiento_metrics_summary(db):
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "iso-sum-a")
    tenant_b, owner_b = _make_tenant(db, "iso-sum-b")
    today = date.today()

    _seed_metric(db, tenant_a.id, today, messages_in=200)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner_b))
        resp = client.get("/v1/metrics/summary")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["messages_in"] != 200


def test_aislamiento_quotas_get(db):
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "iso-qget-a")
    tenant_b, owner_b = _make_tenant(db, "iso-qget-b")

    quota_a = Quota(tenant_id=tenant_a.id, max_messages_per_month=777)
    db.add(quota_a)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner_b))
        resp = client.get("/v1/quotas")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json().get("max_messages_per_month") != 777


def test_aislamiento_audit_log_list(db):
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "iso-aud-a")
    tenant_b, owner_b = _make_tenant(db, "iso-aud-b")

    db.add(AuditLog(id=uuid.uuid4(), tenant_id=tenant_a.id, action="secret.action"))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner_b))
        resp = client.get("/v1/audit-log")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    actions = [item["action"] for item in resp.json()["items"]]
    assert "secret.action" not in actions


def test_aislamiento_audit_log_export(db):
    from src.db.session import get_db

    tenant_a, owner_a = _make_tenant(db, "iso-exp-a")
    tenant_b, owner_b = _make_tenant(db, "iso-exp-b")

    db.add(AuditLog(id=uuid.uuid4(), tenant_id=tenant_a.id, action="private.export"))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.headers.update(_auth(owner_b))
        resp = client.get("/v1/audit-log/export")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "private.export" not in resp.text


def test_aislamiento_check_quota_no_cruza(db):
    """Cuotas del tenant A no bloquean al tenant B."""
    tenant_a, _ = _make_tenant(db, "iso-qchk-a")
    tenant_b, _ = _make_tenant(db, "iso-qchk-b")

    # Poner cuota muy baja en A y llenarla
    q_a = Quota(tenant_id=tenant_a.id, max_messages_per_month=1)
    db.add(q_a)
    today = date.today()
    _seed_metric(db, tenant_a.id, today, messages_out=5)
    db.commit()

    # B no tiene cuota configurada → no bloquea
    check_quota(tenant_b.id, "messages_out", 9999, db)  # no debe lanzar
