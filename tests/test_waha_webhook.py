"""Webhook WAHA ``POST /webhook/waha/{wa_number_id}``.

Cubre los 4 eventos: ``message``, ``message.any``, ``message.ack``,
``session.status``. Verifica:
- Routing por ``wa_number_id`` con ``bypass_tenant_filter`` + ``tenant_scope``.
- Persistencia de mensaje entrante con dedupe por ``wa_message_id``.
- ACK monotónico aplicado vía webhook.
- ``session.status`` actualiza ``WaSession.status`` y guarda QR si llega.
- Token ``X-WAHA-Token`` validado.
"""

from __future__ import annotations

import uuid

import pytest

from src.config import get_settings
from src.messaging import waha_client
from src.wa.models import WaConversation, WaMessage, WaNumber, WaSession


@pytest.fixture(autouse=True)
def _stub_waha(monkeypatch):
    monkeypatch.setattr(waha_client, "create_session", lambda *a, **kw: {"name": a[0]})


@pytest.fixture
def wa_number(client_a, db):
    """Crea un WaNumber para tenant A vía API y lo devuelve refrescado."""
    r = client_a.post(
        "/v1/wa-numbers",
        json={"label": "Hub", "waha_session_name": "wn-hub"},
    )
    assert r.status_code == 201
    wid = uuid.UUID(r.json()["id"])
    wn = db.get(WaNumber, wid)
    return wn


def test_webhook_message_persists_inbound(client, wa_number, db):
    """Un evento ``message`` crea WaConversation + WaMessage inbound."""
    payload = {
        "event": "message",
        "session": "wn-hub",
        "payload": {
            "id": "false_56941131946@c.us_ABC",
            "from": "56941131946@c.us",
            "fromMe": False,
            "body": "hola",
            "senderPn": "56941131946@c.us",
            "timestamp": 1715200000,
            "_data": {"notifyName": "Juan"},
        },
    }

    r = client.post(f"/webhook/waha/{wa_number.id}", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    conv = (
        db.query(WaConversation)
        .filter(WaConversation.wa_contact_phone == "56941131946")
        .first()
    )
    assert conv is not None
    assert conv.tenant_id == wa_number.tenant_id
    assert conv.wa_contact_name == "Juan"

    msg = (
        db.query(WaMessage)
        .filter(WaMessage.wa_message_id == "false_56941131946@c.us_ABC")
        .first()
    )
    assert msg is not None
    assert msg.direction == "in"
    assert msg.text == "hola"
    assert msg.tenant_id == wa_number.tenant_id


def test_webhook_message_dedupe_by_wa_message_id(client, wa_number, db):
    """Un mismo wa_message_id no se persiste dos veces."""
    payload = {
        "event": "message",
        "payload": {
            "id": "false_aaa@c.us_DUP",
            "from": "111@c.us",
            "fromMe": False,
            "body": "ping",
            "senderPn": "111@c.us",
        },
    }

    r1 = client.post(f"/webhook/waha/{wa_number.id}", json=payload)
    assert r1.status_code == 200
    assert r1.json().get("skipped") != "duplicate"

    r2 = client.post(f"/webhook/waha/{wa_number.id}", json=payload)
    assert r2.status_code == 200
    assert r2.json()["skipped"] == "duplicate"

    count = (
        db.query(WaMessage)
        .filter(WaMessage.wa_message_id == "false_aaa@c.us_DUP")
        .count()
    )
    assert count == 1


def test_webhook_message_any_skips_outbound_echo(client, wa_number, db):
    """``message.any`` con fromMe=True no debe persistir (ya lo hizo el endpoint)."""
    payload = {
        "event": "message.any",
        "payload": {
            "id": "true_56941131946@c.us_OUT",
            "from": "56941131946@c.us",
            "fromMe": True,
            "body": "respuesta del bot",
        },
    }

    r = client.post(f"/webhook/waha/{wa_number.id}", json=payload)
    assert r.status_code == 200
    assert r.json()["skipped"] == "echo_outbound"

    n = (
        db.query(WaMessage)
        .filter(WaMessage.wa_message_id == "true_56941131946@c.us_OUT")
        .count()
    )
    assert n == 0


def test_webhook_message_ack_advances_monotonically(client, wa_number, db):
    """ACK transitions: sent → delivered → read; failed posterior es ignorado."""
    # Crear un WaMessage outbound directamente
    conv = WaConversation(
        tenant_id=wa_number.tenant_id,
        wa_number_id=wa_number.id,
        wa_contact_phone="56999",
    )
    db.add(conv)
    db.flush()
    msg = WaMessage(
        tenant_id=wa_number.tenant_id,
        wa_number_id=wa_number.id,
        wa_conversation_id=conv.id,
        direction="out",
        text="hola",
        wa_message_id="true_56999@c.us_TR1",
        ack="sent",
    )
    db.add(msg)
    db.commit()

    def _ack(state):
        return client.post(
            f"/webhook/waha/{wa_number.id}",
            json={
                "event": "message.ack",
                "payload": {"id": "true_56999@c.us_TR1", "ack": state},
            },
        )

    r1 = _ack("DELIVERED")
    assert r1.status_code == 200
    db.refresh(msg)
    assert msg.ack == "delivered"

    r2 = _ack("READ")
    assert r2.status_code == 200
    db.refresh(msg)
    assert msg.ack == "read"

    # Failed después de read se ignora (read es absorbente)
    r3 = _ack("ERROR")
    assert r3.status_code == 200
    db.refresh(msg)
    assert msg.ack == "read"


def test_webhook_session_status_persists_qr(client, wa_number, db):
    """``session.status`` con QR lo guarda; cuando pasa a WORKING lo limpia."""
    qr_payload = {
        "event": "session.status",
        "payload": {"status": "SCAN_QR_CODE", "qr": {"data": "BASE64DATA"}},
    }
    r = client.post(f"/webhook/waha/{wa_number.id}", json=qr_payload)
    assert r.status_code == 200

    sess = (
        db.query(WaSession).filter(WaSession.wa_number_id == wa_number.id).first()
    )
    assert sess is not None
    assert sess.status == "SCAN_QR_CODE"
    assert sess.qr_data_b64 == "BASE64DATA"

    working_payload = {
        "event": "session.status",
        "payload": {"status": "WORKING"},
    }
    r2 = client.post(f"/webhook/waha/{wa_number.id}", json=working_payload)
    assert r2.status_code == 200
    db.refresh(sess)
    assert sess.status == "WORKING"
    assert sess.qr_data_b64 == ""


def test_webhook_unknown_wa_number_returns_200_skipped(client, db):
    """Si el wa_number_id no existe, devolvemos 200 + skipped (evitamos retry loops)."""
    fake_id = uuid.uuid4()
    r = client.post(
        f"/webhook/waha/{fake_id}",
        json={"event": "message", "payload": {}},
    )
    assert r.status_code == 200
    assert r.json()["skipped"] == "wa_number_not_found"


def test_webhook_x_waha_token_validated(client, wa_number, db, monkeypatch):
    """Con token configurado, requests sin header válido reciben 401."""
    monkeypatch.setenv("WAHA_WEBHOOK_TOKEN", "supersecret")
    get_settings.cache_clear()

    try:
        # Sin header
        r = client.post(
            f"/webhook/waha/{wa_number.id}",
            json={"event": "message", "payload": {}},
        )
        assert r.status_code == 401

        # Header incorrecto
        r2 = client.post(
            f"/webhook/waha/{wa_number.id}",
            json={"event": "message", "payload": {}},
            headers={"X-WAHA-Token": "wrong"},
        )
        assert r2.status_code == 401

        # Header correcto pasa (y skipea por payload sin id, no relevante para auth)
        r3 = client.post(
            f"/webhook/waha/{wa_number.id}",
            json={
                "event": "message",
                "payload": {
                    "id": "false_x@c.us_TKN",
                    "from": "111@c.us",
                    "senderPn": "111@c.us",
                    "fromMe": False,
                    "body": "ok",
                },
            },
            headers={"X-WAHA-Token": "supersecret"},
        )
        assert r3.status_code == 200
        assert r3.json()["ok"] is True
    finally:
        monkeypatch.delenv("WAHA_WEBHOOK_TOKEN", raising=False)
        get_settings.cache_clear()
