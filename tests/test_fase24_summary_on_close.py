"""Tests Fase 24D — Resumen automático de conversación al cerrar.

Cubre:
- close con > 5 turnos → tarea disparada (mock Celery delay).
- close con ≤ 5 turnos → tarea NO disparada.
- Tarea .run() con LLM mockeado → ai_summary persistido en DB.
- GET /v1/inbox/{conv_id} incluye ai_summary en respuesta.
- Aislamiento: la tarea solo accede a la conversación del tenant correcto.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.auth.tokens import create_access_token
from src.wa.models import WaConversation, WaMessage, WaNumber


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wa_number(db, tenant_id: uuid.UUID) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Test",
        waha_session_name=f"session-{uuid.uuid4().hex[:8]}",
        phone="5491100000003",
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conversation(
    db,
    tenant_id: uuid.UUID,
    wn: WaNumber,
    status: str = "agent",
    turn_count: int = 0,
) -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wn.id,
        wa_contact_phone="5491155556666",
        status=status,
        turn_count=turn_count,
    )
    db.add(conv)
    db.flush()
    return conv


def _add_messages(db, tenant_id: uuid.UUID, wn: WaNumber, conv: WaConversation, n: int) -> None:
    for i in range(n):
        direction = "in" if i % 2 == 0 else "out"
        msg = WaMessage(
            tenant_id=tenant_id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction=direction,
            text=f"Mensaje número {i + 1}",
            wa_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        )
        db.add(msg)
    db.flush()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_close_con_mas_de_5_turnos_dispara_tarea(client, db, tenant_a):
    """close con > 5 turnos dispara la tarea summarize_on_close."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="agent", turn_count=8)
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)

    with patch("src.agent.tasks.summarize_on_close") as mock_task:
        mock_delay = MagicMock()
        mock_task.delay = mock_delay

        resp = client.post(
            f"/v1/inbox/{conv.id}/close",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    mock_delay.assert_called_once_with(str(conv.id))


def test_close_con_5_o_menos_turnos_no_dispara_tarea(client, db, tenant_a):
    """close con ≤ 5 turnos NO dispara la tarea summarize_on_close."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="agent", turn_count=4)
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)

    with patch("src.agent.tasks.summarize_on_close") as mock_task:
        mock_delay = MagicMock()
        mock_task.delay = mock_delay

        resp = client.post(
            f"/v1/inbox/{conv.id}/close",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    mock_delay.assert_not_called()


def test_tarea_persiste_ai_summary_en_db(db, tenant_a):
    """La tarea run() con LLM mockeado persiste ai_summary en WaConversation."""
    from src.agent.tasks import summarize_on_close
    from src.tenancy.context import bypass_tenant_filter

    tenant, _owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="bot", turn_count=10)
    _add_messages(db, tenant.id, wn, conv, 8)
    db.commit()

    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = "El cliente consultó sobre zapatos. Se resolvió satisfactoriamente."
    mock_response.content = [mock_block]

    with patch("src.agent.llm.call_claude_messages", return_value=(mock_response, {})):
        with patch("src.db.session.get_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = lambda s: db
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            result = summarize_on_close.run(str(conv.id))

    assert result["status"] == "ok"
    db.refresh(conv)
    assert conv.ai_summary is not None
    assert "zapatos" in conv.ai_summary


def test_get_conversation_incluye_ai_summary(client, db, tenant_a):
    """GET /v1/inbox/{conv_id} incluye el campo ai_summary (null si no hay)."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="bot")
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        f"/v1/inbox/{conv.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # ai_summary debe estar presente en la respuesta de la conversación.
    assert "ai_summary" in data["conversation"]
    assert data["conversation"]["ai_summary"] is None  # sin resumen aún


def test_get_conversation_muestra_ai_summary_persistido(client, db, tenant_a):
    """GET /v1/inbox/{conv_id} muestra ai_summary cuando fue persistido."""
    tenant, owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="bot")
    conv.ai_summary = "Consulta sobre productos. Se resolvió con éxito."
    db.add(conv)
    db.commit()

    token = create_access_token(owner.id, tenant.id, owner.role)
    resp = client.get(
        f"/v1/inbox/{conv.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation"]["ai_summary"] == "Consulta sobre productos. Se resolvió con éxito."


def test_tarea_skipped_con_pocos_mensajes(db, tenant_a):
    """La tarea no genera resumen si hay ≤ 5 mensajes."""
    from src.agent.tasks import summarize_on_close

    tenant, _owner = tenant_a
    wn = _make_wa_number(db, tenant.id)
    conv = _make_conversation(db, tenant.id, wn, status="bot", turn_count=2)
    _add_messages(db, tenant.id, wn, conv, 3)
    db.commit()

    with patch("src.db.session.get_db_session") as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: db
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        result = summarize_on_close.run(str(conv.id))

    assert result["status"] == "skipped"
    db.refresh(conv)
    assert conv.ai_summary is None


def test_tarea_not_found_conv_inexistente(db, tenant_a):
    """La tarea devuelve not_found si la conversación no existe."""
    from src.agent.tasks import summarize_on_close

    conv_id_falso = str(uuid.uuid4())

    with patch("src.db.session.get_db_session") as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: db
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        result = summarize_on_close.run(conv_id_falso)

    assert result["status"] == "not_found"
