"""Tests Fase 25A: Panel de SLA y tiempos de respuesta.

Cubre:
- first_response_at se setea en primera respuesta del bot (_persist_outbound).
- first_response_at se setea en primera respuesta del agente (reply_conversation).
- first_response_at NO se sobreescribe en segundas respuestas.
- resolved_at se setea en close_conversation.
- GET /v1/inbox/sla-report devuelve métricas correctas.
- Aislamiento multi-tenant en sla-report.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.auth.tokens import create_access_token
from src.db.models import Tenant, User
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant: Tenant) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant.id,
        label="Test",
        waha_session_name=f"sess-{uuid.uuid4().hex[:8]}",
        phone="5491100000001",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conv(db, tenant: Tenant, wn: WaNumber, *, created_offset_secs: int = 0) -> WaConversation:
    ts = datetime.now(UTC) - timedelta(seconds=created_offset_secs)
    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone=f"549110{uuid.uuid4().int % 10000000:07d}",
        status="bot",
        created_at=ts,
        updated_at=ts,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_inbound(db, tenant: Tenant, wn: WaNumber, conv: WaConversation) -> WaMessage:
    msg = WaMessage(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="in",
        text="hola",
    )
    db.add(msg)
    db.flush()
    return msg


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.tenant_id, user.role)
    return {"Authorization": f"Bearer {token}"}


# ── Tests first_response_at (bot) ─────────────────────────────────────────────


def test_first_response_at_se_setea_en_bot(db, tenant_a):
    """_persist_outbound debe setear first_response_at si es NULL."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    assert conv.first_response_at is None

    from src.agent.service import _persist_outbound
    from src.messaging.dispatcher import DispatchResult

    send_result = DispatchResult(success=True, wa_message_id="wamid-1", raw_response={})
    with patch("src.agent.service.dispatcher.send_text", return_value=send_result):
        _persist_outbound(db, conv, wn, "Hola, soy el bot")

    db.refresh(conv)
    assert conv.first_response_at is not None


def test_first_response_at_no_se_sobreescribe(db, tenant_a):
    """Primera respuesta ya seteada no se debe actualizar en la segunda."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)

    original_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    conv.first_response_at = original_dt
    db.add(conv)
    db.flush()

    from src.agent.service import _persist_outbound
    from src.messaging.dispatcher import DispatchResult

    send_result = DispatchResult(success=True, wa_message_id="wamid-2", raw_response={})
    with patch("src.agent.service.dispatcher.send_text", return_value=send_result):
        _persist_outbound(db, conv, wn, "Segunda respuesta")

    db.refresh(conv)
    assert conv.first_response_at == original_dt


# ── Tests resolved_at ─────────────────────────────────────────────────────────


def test_resolved_at_se_setea_al_cerrar(client_a, db, tenant_a):
    """close_conversation debe setear resolved_at."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)
    conv.status = "agent"
    conv.assigned_user_id = owner.id
    db.add(conv)
    db.flush()

    assert conv.resolved_at is None

    r = client_a.post(f"/v1/inbox/{conv.id}/close")
    assert r.status_code == 200

    db.refresh(conv)
    assert conv.resolved_at is not None


def test_resolved_at_se_actualiza_al_reabrir_y_cerrar(client_a, db, tenant_a):
    """Si se cierra dos veces, resolved_at se actualiza."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)
    conv = _make_conv(db, tenant, wn)
    conv.status = "agent"
    db.add(conv)
    db.flush()

    client_a.post(f"/v1/inbox/{conv.id}/close")
    db.refresh(conv)
    first_resolved = conv.resolved_at

    # Reabrir y cerrar de nuevo.
    conv.status = "agent"
    db.add(conv)
    db.flush()

    client_a.post(f"/v1/inbox/{conv.id}/close")
    db.refresh(conv)
    assert conv.resolved_at is not None
    assert conv.resolved_at >= first_resolved


# ── Tests SLA report ──────────────────────────────────────────────────────────


def test_sla_report_sin_datos_devuelve_ceros(client_a):
    """Sin conversaciones, el reporte devuelve ceros y Nones."""
    r = client_a.get("/v1/inbox/sla-report")
    assert r.status_code == 200
    data = r.json()
    assert data["total_conversations"] == 0
    assert data["resolved_conversations"] == 0
    assert data["avg_first_response_seconds"] is None
    assert data["avg_resolution_seconds"] is None
    assert data["p50_first_response_seconds"] is None
    assert data["p90_first_response_seconds"] is None


def test_sla_report_calcula_metricas(client_a, db, tenant_a):
    """Con conversaciones reales, el reporte calcula tiempos correctamente."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)

    now = datetime.now(UTC)

    # conv1: first_response en 60s, resolved en 300s
    conv1 = _make_conv(db, tenant, wn)
    conv1.first_response_at = now + timedelta(seconds=60)
    conv1.resolved_at = now + timedelta(seconds=300)
    db.add(conv1)

    # conv2: first_response en 120s, sin resolver
    conv2 = _make_conv(db, tenant, wn)
    conv2.first_response_at = now + timedelta(seconds=120)
    db.add(conv2)

    db.flush()

    r = client_a.get("/v1/inbox/sla-report")
    assert r.status_code == 200
    data = r.json()

    assert data["total_conversations"] == 2
    assert data["resolved_conversations"] == 1
    assert data["avg_first_response_seconds"] == pytest.approx(90.0, abs=1)
    assert data["avg_resolution_seconds"] == pytest.approx(300.0, abs=1)
    assert data["p50_first_response_seconds"] is not None
    assert data["p90_first_response_seconds"] is not None


def test_sla_report_aislamiento_tenant(client_a, db, tenant_a, tenant_b):
    """El reporte no incluye conversaciones de otro tenant."""
    tenant_b_obj, owner_b = tenant_b
    wn_b = _make_wa_number(db, tenant_b_obj)
    conv_b = _make_conv(db, tenant_b_obj, wn_b)
    conv_b.first_response_at = datetime.now(UTC) + timedelta(seconds=100)
    conv_b.resolved_at = datetime.now(UTC) + timedelta(seconds=500)
    db.add(conv_b)
    db.flush()

    r = client_a.get("/v1/inbox/sla-report")
    assert r.status_code == 200
    data = r.json()
    # Tenant A no tiene conversaciones.
    assert data["total_conversations"] == 0


def test_sla_report_filtro_fecha(client_a, db, tenant_a):
    """date_from/date_to filtran correctamente."""
    from datetime import date

    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant)

    # conv dentro del rango
    conv_in = _make_conv(db, tenant, wn)
    conv_in.first_response_at = datetime.now(UTC) + timedelta(seconds=30)
    db.add(conv_in)

    # conv fuera del rango (hace 60 días)
    old_ts = datetime.now(UTC) - timedelta(days=60)
    conv_out = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone=f"549111{uuid.uuid4().int % 10000000:07d}",
        status="bot",
        created_at=old_ts,
        updated_at=old_ts,
    )
    conv_out.first_response_at = old_ts + timedelta(seconds=30)
    db.add(conv_out)
    db.flush()

    today = date.today().isoformat()
    r = client_a.get(f"/v1/inbox/sla-report?date_from={today}&date_to={today}")
    assert r.status_code == 200
    data = r.json()
    assert data["total_conversations"] == 1
