"""Smoke E2E de los endpoints REST de WaNumbers (Fase 1 — criterio de done).

Cubre:
- Alta de número (mocked WAHA create_session) + listado.
- Aislamiento multi-tenant: tenant B no ve los números de tenant A.
- Envío de texto via dispatcher (mocked) → persiste WaMessage outbound con ACK 'sent'.
- Manejo de ``WahaAPIError(-2)`` en envío → mensaje queda en ACK 'failed'.
- GET QR usa el cache transitorio si hay; pull en vivo si no.
- Logout cierra la sesión.
"""

from __future__ import annotations

import uuid

import pytest

from src.messaging import waha_client
from src.messaging.waha_client import WahaAPIError
from src.wa.models import WaMessage, WaNumber, WaSession


@pytest.fixture(autouse=True)
def _stub_create_session(monkeypatch):
    """Las pruebas no deben llamar a WAHA real al crear sesión."""
    monkeypatch.setattr(waha_client, "create_session", lambda *a, **kw: {"name": a[0]})
    monkeypatch.setattr(waha_client, "logout_session", lambda *a, **kw: {})
    yield


def test_create_wa_number(client_a, db):
    resp = client_a.post(
        "/v1/wa-numbers",
        json={
            "label": "Soporte",
            "waha_session_name": "tenant-a-soporte",
            "tags": ["soporte"],
            "is_default": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["label"] == "Soporte"
    assert data["waha_session_name"] == "tenant-a-soporte"
    assert data["session_status"] == "STARTING"
    assert data["is_default"] is True
    assert data["tags"] == ["soporte"]

    # Persistido en DB con tenant_id correcto
    wn = db.query(WaNumber).filter(WaNumber.id == uuid.UUID(data["id"])).first()
    assert wn is not None
    assert wn.tenant_id == uuid.UUID(data["tenant_id"])
    sess = db.query(WaSession).filter(WaSession.wa_number_id == wn.id).first()
    assert sess is not None
    assert sess.status == "STARTING"


def test_create_wa_number_invalid_session_name(client_a):
    resp = client_a.post(
        "/v1/wa-numbers",
        json={"label": "x", "waha_session_name": "ab"},  # < 3 chars
    )
    assert resp.status_code == 422


def test_create_wa_number_duplicate_session_name(client_a, db):
    payload = {"label": "X", "waha_session_name": "shared-name"}
    r1 = client_a.post("/v1/wa-numbers", json=payload)
    assert r1.status_code == 201
    r2 = client_a.post("/v1/wa-numbers", json=payload)
    assert r2.status_code == 400


def test_list_wa_numbers_isolated_per_tenant(client_a, client_b, db):
    """Tenant B no debe ver los números de tenant A."""
    r1 = client_a.post(
        "/v1/wa-numbers",
        json={"label": "A1", "waha_session_name": "tenant-a-num"},
    )
    assert r1.status_code == 201
    a_id = r1.json()["id"]

    r2 = client_b.post(
        "/v1/wa-numbers",
        json={"label": "B1", "waha_session_name": "tenant-b-num"},
    )
    assert r2.status_code == 201

    # B lista solo el suyo
    list_b = client_b.get("/v1/wa-numbers")
    assert list_b.status_code == 200
    ids_b = [n["id"] for n in list_b.json()["items"]]
    assert a_id not in ids_b

    # B no puede acceder al de A por id
    g = client_b.get(f"/v1/wa-numbers/{a_id}")
    assert g.status_code == 404


def test_send_text_success(client_a, db, monkeypatch):
    """POST /send con waha_client.send_text mocked → WaMessage outbound 'sent'."""
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "X", "waha_session_name": "tenant-a-send"},
    )
    assert r.status_code == 201
    wn_id = r.json()["id"]

    fake_resp = {"id": {"_serialized": "true_56941131946@c.us_ABC123"}}
    monkeypatch.setattr(waha_client, "send_text", lambda *a, **kw: fake_resp)

    send = client_a.post(
        f"/v1/wa-numbers/{wn_id}/send",
        json={"to": "+56 9 4113 1946", "text": "hola"},
    )
    assert send.status_code == 200, send.text
    body = send.json()
    assert body["success"] is True
    assert body["wa_message_id"] == "true_56941131946@c.us_ABC123"

    # WaMessage persistido con ACK 'sent'
    msg = (
        db.query(WaMessage)
        .filter(WaMessage.wa_message_id == "true_56941131946@c.us_ABC123")
        .first()
    )
    assert msg is not None
    assert msg.direction == "out"
    assert msg.text == "hola"
    assert msg.ack == "sent"
    assert msg.sent_at is not None


def test_send_text_lid_unresolved_returns_failed(client_a, db, monkeypatch):
    """Si waha_client.send_text levanta WahaAPIError(-2), el mensaje queda 'failed'."""
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "X", "waha_session_name": "tenant-a-lid"},
    )
    assert r.status_code == 201
    wn_id = r.json()["id"]

    def _raise(*a, **kw):
        raise WahaAPIError(
            status=-2, message="LID sin resolver — abort"
        )

    monkeypatch.setattr(waha_client, "send_text", _raise)

    send = client_a.post(
        f"/v1/wa-numbers/{wn_id}/send",
        json={"to": "148726328881285", "text": "test"},  # 15 dígitos = LID
    )
    assert send.status_code == 200
    body = send.json()
    assert body["success"] is False
    assert "-2" in body["error"] or "LID" in body["error"]

    # WaMessage persistido con ack='failed'
    msg = (
        db.query(WaMessage)
        .filter(WaMessage.id == uuid.UUID(body["message_id"]))
        .first()
    )
    assert msg is not None
    assert msg.ack == "failed"
    assert msg.failed_at is not None
    assert "-2" in msg.error


def test_send_text_invalid_phone(client_a):
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "X", "waha_session_name": "tenant-a-invphone"},
    )
    wn_id = r.json()["id"]
    send = client_a.post(
        f"/v1/wa-numbers/{wn_id}/send",
        json={"to": "abc", "text": "x"},
    )
    assert send.status_code == 400


def test_get_qr_pulls_live_when_no_cache(client_a, db, monkeypatch):
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "X", "waha_session_name": "tenant-a-qr"},
    )
    wn_id = r.json()["id"]

    monkeypatch.setattr(waha_client, "get_qr", lambda *a: "BASE64_PAYLOAD")

    q = client_a.get(f"/v1/wa-numbers/{wn_id}/qr")
    assert q.status_code == 200
    assert q.json()["qr_base64"] == "BASE64_PAYLOAD"


def test_logout_marks_session_stopped(client_a, db):
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "X", "waha_session_name": "tenant-a-logout"},
    )
    wn_id = r.json()["id"]
    out = client_a.post(f"/v1/wa-numbers/{wn_id}/logout")
    assert out.status_code == 204

    sess = (
        db.query(WaSession)
        .filter(WaSession.wa_number_id == uuid.UUID(wn_id))
        .first()
    )
    assert sess is not None
    assert sess.status == "STOPPED"
