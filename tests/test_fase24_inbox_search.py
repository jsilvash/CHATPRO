"""Tests Fase 24C — Búsqueda full-text de mensajes en inbox.

Cubre:
- Búsqueda retorna mensajes que contienen el término.
- Contexto (±2) se incluye correctamente.
- Filtro por conversation_id funciona.
- Filtro por fecha funciona.
- q vacío o ausente → 400.
- Aislamiento: búsqueda de tenant A no retorna mensajes de tenant B.
- Sin resultados → lista vacía, no error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, date

import pytest

from src.auth.tokens import create_access_token
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test",
        waha_session_name=f"session-{uuid.uuid4().hex[:8]}",
        phone="5491100000002",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(db, tenant_id: uuid.UUID, wn: WaNumber, phone: str = "5491111111111") -> WaConversation:
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
    text: str,
    direction: str = "in",
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


def test_busqueda_retorna_match(client, db, tenant_a):
    """Búsqueda retorna mensajes que contienen el término buscado."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)
    _make_message(db, tenant.id, wn, conv, "quiero comprar zapatos rojos")
    _make_message(db, tenant.id, wn, conv, "gracias por tu respuesta")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        "/v1/inbox/search?q=zapatos",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    texts = [r["message"]["text"] for r in data["results"]]
    assert any("zapatos" in t for t in texts)


def test_busqueda_sin_resultados_lista_vacia(client, db, tenant_a):
    """Búsqueda sin matches devuelve lista vacía, no error."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)
    _make_message(db, tenant.id, wn, conv, "hola como estas")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        "/v1/inbox/search?q=xyzimpossible",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


def test_busqueda_contexto_incluye_mensajes_cercanos(client, db, tenant_a):
    """El campo context incluye hasta 2 mensajes antes y 2 después del match."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)

    base_time = datetime.now(UTC)
    msgs = []
    texts = ["primer mensaje", "segundo mensaje", "BUSCAR ESTO", "cuarto mensaje", "quinto mensaje"]
    for i, text in enumerate(texts):
        t = base_time + timedelta(seconds=i)
        msgs.append(_make_message(db, tenant.id, wn, conv, text, created_at=t))
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        "/v1/inbox/search?q=buscar",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1

    result = data["results"][0]
    assert "buscar" in result["message"]["text"].lower() or "BUSCAR" in result["message"]["text"]
    context_texts = [c["text"] for c in result["context"]]
    # Debe haber contexto alrededor.
    assert len(result["context"]) > 0
    assert len(result["context"]) <= 4  # máximo ±2


def test_busqueda_filtro_conversation_id(client, db, tenant_a):
    """Filtro por conversation_id restringe resultados a esa conversación."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv1 = _make_conversation(db, tenant.id, wn, "5491100001111")
    conv2 = _make_conversation(db, tenant.id, wn, "5491100002222")
    _make_message(db, tenant.id, wn, conv1, "producto especial disponible")
    _make_message(db, tenant.id, wn, conv2, "producto especial disponible")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        f"/v1/inbox/search?q=especial&conversation_id={conv1.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for r in data["results"]:
        assert r["message"]["wa_conversation_id"] == str(conv1.id)


def test_busqueda_filtro_fecha(client, db, tenant_a):
    """Filtro por date_from / date_to excluye mensajes fuera del rango."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn)

    old_time = datetime.now(UTC) - timedelta(days=60)
    _make_message(db, tenant.id, wn, conv, "consulta antigua sobre precios", created_at=old_time)
    _make_message(db, tenant.id, wn, conv, "consulta reciente sobre precios")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    today = date.today()
    date_from = (today - timedelta(days=7)).isoformat()
    resp = client.get(
        f"/v1/inbox/search?q=precios&date_from={date_from}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


def test_busqueda_q_vacio_retorna_400(client, db, tenant_a):
    """q vacío devuelve 400."""
    tenant, owner = tenant_a
    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        "/v1/inbox/search?q=",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_busqueda_sin_q_retorna_400(client, db, tenant_a):
    """Sin parámetro q devuelve 400."""
    tenant, owner = tenant_a
    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        "/v1/inbox/search",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_busqueda_aislamiento_tenant_b_no_ve_tenant_a(db, tenant_a, tenant_b):
    """Búsqueda de tenant B no retorna mensajes de tenant A."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.db.session import get_db

    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b

    wn_a = _make_wa_number(db, tenant_a_obj.id)
    conv_a = _make_conversation(db, tenant_a_obj.id, wn_a)
    _make_message(db, tenant_a_obj.id, wn_a, conv_a, "producto secreto de tenant A")

    wn_b = _make_wa_number(db, tenant_b_obj.id)
    conv_b = _make_conversation(db, tenant_b_obj.id, wn_b)
    _make_message(db, tenant_b_obj.id, wn_b, conv_b, "otro mensaje de tenant B")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        token_b = create_access_token(owner_b.id, tenant_b_obj.id, owner_b.role)
        with TestClient(app) as c:
            resp = c.get(
                "/v1/inbox/search?q=secreto",
                headers={"Authorization": f"Bearer {token_b}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0  # Tenant B no ve mensajes de tenant A.
    finally:
        app.dependency_overrides.pop(get_db, None)
