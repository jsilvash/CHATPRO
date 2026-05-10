"""Tests Fase 24B — Dashboard de métricas por WaNumber.

Cubre:
- 200 con campos esperados.
- Filtro por fecha funciona correctamente.
- top_contacts devuelve máximo 5.
- 404 si wa_number no pertenece al tenant.
- Aislamiento: tenant B no ve métricas de tenant A.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, date, timedelta

import pytest

from src.auth.tokens import create_access_token
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID, label: str = "Test") -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label=label,
        waha_session_name=f"session-{uuid.uuid4().hex[:8]}",
        phone="5491100000001",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(db, tenant_id: uuid.UUID, wn: WaNumber, phone: str) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone=phone,
        status="bot",
    )
    db.add(conv)
    db.flush()
    return conv


def _make_message(
    db,
    tenant_id: uuid.UUID,
    wn: WaNumber,
    conv: WaConversation,
    direction: str,
    text: str = "hola",
    created_at: datetime | None = None,
) -> WaMessage:
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction=direction,
        text=text,
        wa_message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    if created_at:
        msg.created_at = created_at
    db.add(msg)
    db.flush()
    return msg


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_metrics_200_campos_esperados(client, db, tenant_a):
    """GET /v1/wa-numbers/{id}/metrics retorna 200 con todos los campos esperados."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, "5491111111111")
    _make_message(db, tenant.id, wn, conv, "in")
    _make_message(db, tenant.id, wn, conv, "out")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        f"/v1/wa-numbers/{wn.id}/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "wa_number_id" in data
    assert "date_from" in data
    assert "date_to" in data
    assert "messages_in" in data
    assert "messages_out" in data
    assert "conversations_total" in data
    assert "conversations_active" in data
    assert "top_contacts" in data
    assert data["messages_in"] >= 1
    assert data["messages_out"] >= 1
    assert data["conversations_total"] >= 1


def test_metrics_filtro_fecha(client, db, tenant_a):
    """Filtro por date_from / date_to excluye mensajes fuera del rango."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, "5491122223333")

    # Mensaje de hace 60 días (fuera del rango por defecto de 30 días).
    old_date = datetime.now(UTC) - timedelta(days=60)
    _make_message(db, tenant.id, wn, conv, "in", created_at=old_date)

    # Mensaje de hoy.
    _make_message(db, tenant.id, wn, conv, "in")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    today = date.today()
    date_from = (today - timedelta(days=7)).isoformat()
    date_to = today.isoformat()

    resp = client.get(
        f"/v1/wa-numbers/{wn.id}/metrics?date_from={date_from}&date_to={date_to}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Solo el mensaje de hoy debe contar.
    assert data["messages_in"] == 1


def test_metrics_top_contacts_maximo_5(client, db, tenant_a):
    """top_contacts devuelve máximo 5 entradas."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)

    # Crear 7 contactos distintos con mensajes.
    for i in range(7):
        phone = f"549111100{i:04d}"
        conv = _make_conversation(db, tenant.id, wn, phone)
        for _ in range(i + 1):
            _make_message(db, tenant.id, wn, conv, "in")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        f"/v1/wa-numbers/{wn.id}/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["top_contacts"]) <= 5


def test_metrics_404_wa_number_otro_tenant(client, db, tenant_a, tenant_b):
    """404 si el wa_number pertenece a otro tenant."""
    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, _owner_b = tenant_b

    # Número del tenant B.
    wn_b = _make_wa_number(db, tenant_b_obj.id, "Número B")
    db.commit()

    token = create_access_token(owner_a.id, tenant_a_obj.id, owner_a.role)
    resp = client.get(
        f"/v1/wa-numbers/{wn_b.id}/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_metrics_aislamiento_tenant_b_no_ve_tenant_a(db, tenant_a, tenant_b):
    """Tenant B no puede ver métricas de tenant A usando su propio token."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    wn_a = _make_wa_number(db, tenant_a_obj.id, "Número A")
    conv_a = _make_conversation(db, tenant_a_obj.id, wn_a, "5491100001111")
    _make_message(db, tenant_a_obj.id, wn_a, conv_a, "in")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        token_b = create_access_token(owner_b.id, tenant_b_obj.id, owner_b.role)
        with TestClient(app) as c:
            resp = c.get(
                f"/v1/wa-numbers/{wn_a.id}/metrics",
                headers={"Authorization": f"Bearer {token_b}"},
            )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_metrics_sin_mensajes_devuelve_ceros(client, db, tenant_a):
    """Métricas con rango sin datos devuelven ceros en vez de error."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    # Rango en el pasado lejano donde no hay nada.
    resp = client.get(
        f"/v1/wa-numbers/{wn.id}/metrics?date_from=2000-01-01&date_to=2000-01-31",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages_in"] == 0
    assert data["messages_out"] == 0
    assert data["conversations_total"] == 0
    assert data["top_contacts"] == []
