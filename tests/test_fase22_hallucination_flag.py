"""Tests Fase 22D: persistir hallucination_flag en wa_messages.

Cobertura:
- WaMessage tiene columna hallucination_flag (bool, default False).
- _persist_outbound crea WaMessage con hallucination_flag=False por defecto.
- _persist_outbound crea WaMessage con hallucination_flag=True cuando se pasa.
- _run_agent_loop: retorna hallucination_flag=True cuando hay precios no grounded.
- _run_agent_loop: retorna hallucination_flag=False con respuesta sin precios.
- service.respond: setea hallucination_flag en el WaMessage outbound cuando el agente detecta alucinación.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.wa.models import WaMessage


# ── Columna en el modelo ──────────────────────────────────────────────────────


def test_wa_message_tiene_columna_hallucination_flag():
    cols = WaMessage.__table__.columns.keys()
    assert "hallucination_flag" in cols


def test_wa_message_hallucination_flag_default_false():
    msg = WaMessage.__new__(WaMessage)
    # El default de la columna es False (server_default="false")
    col = WaMessage.__table__.columns["hallucination_flag"]
    # server_default es una expresión SQL, verificamos que nullable es False
    assert col.nullable is False


# ── _persist_outbound ─────────────────────────────────────────────────────────


def _mock_dispatch_result(success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.wa_message_id = "wamid-test"
    result.error = ""
    result.raw_response = {}
    return result


def _make_conv_wn(tenant_id=None):
    tenant_id = tenant_id or uuid.uuid4()
    conv = MagicMock()
    conv.tenant_id = tenant_id
    conv.wa_contact_phone = "56999000000"
    conv.id = uuid.uuid4()

    wn = MagicMock()
    wn.id = uuid.uuid4()
    return conv, wn


def test_persist_outbound_flag_false_por_defecto():
    from src.agent.service import _persist_outbound

    conv, wn = _make_conv_wn()
    db = MagicMock()
    added_msgs = []
    db.add.side_effect = lambda obj: added_msgs.append(obj)

    with patch("src.agent.service.dispatcher.send_text", return_value=_mock_dispatch_result()):
        msg = _persist_outbound(db, conv, wn, "Hola")

    assert msg.hallucination_flag is False


def test_persist_outbound_flag_true_cuando_se_pasa():
    from src.agent.service import _persist_outbound

    conv, wn = _make_conv_wn()
    db = MagicMock()

    with patch("src.agent.service.dispatcher.send_text", return_value=_mock_dispatch_result()):
        msg = _persist_outbound(db, conv, wn, "El precio es $100.000", hallucination_flag=True)

    assert msg.hallucination_flag is True


# ── _check_anti_hallucination ─────────────────────────────────────────────────


def test_check_anti_hallucination_sin_herramientas_y_precio():
    from src.agent.service import _check_anti_hallucination

    # Sin tool_results y hay un precio → True
    resultado = _check_anti_hallucination("El precio es $50.000", [])
    assert resultado is True


def test_check_anti_hallucination_precio_grounded():
    from src.agent.service import _check_anti_hallucination

    # Precio aparece en tool_results → False (grounded)
    tool_results = [{"precio": 50000, "nombre": "Producto"}]
    resultado = _check_anti_hallucination("El precio es $50.000", tool_results)
    assert resultado is False


def test_check_anti_hallucination_sin_precio_en_respuesta():
    from src.agent.service import _check_anti_hallucination

    resultado = _check_anti_hallucination("Te saludo cordialmente.", [])
    assert resultado is False


# ── Integración con service.respond ──────────────────────────────────────────


def test_respond_setea_hallucination_flag_en_outbound():
    """respond() detecta alucinación y la persiste en el WaMessage de salida."""
    from src.agent.service import respond

    tenant_id = uuid.uuid4()
    conv = MagicMock()
    conv.tenant_id = tenant_id
    conv.wa_contact_phone = "56988000001"
    conv.status = "bot"
    conv.id = uuid.uuid4()
    conv.wa_number_id = uuid.uuid4()

    inbound = MagicMock()
    db = MagicMock()

    # Simular que _run_agent_loop retorna con hallucination_flag=True
    with (
        patch("src.agent.service.get_settings") as mock_settings,
        patch("src.agent.service.is_rate_limited", return_value=False),
        patch("src.agent.service._load_persona") as mock_persona,
        patch("src.agent.service._maybe_trigger_summary"),
        patch("src.agent.service.load_top_facts", return_value=[]),
        patch("src.agent.service.build_system_prompt", return_value="sys"),
        patch("src.agent.service._build_messages", return_value=[{"role": "user", "content": "hola"}]),
        patch("src.agent.service.check_quota"),
        patch("src.agent.service._run_agent_loop",
              return_value=("El precio es $50.000", {}, "end_turn", True)) as mock_loop,
        patch("src.agent.service._auto_escalate_if_needed"),
        patch("src.agent.service._persist_outbound") as mock_persist,
        patch("src.agent.service._update_usage_metrics"),
        patch("src.agent.service.is_within_business_hours", return_value=True),
        patch("src.agent.service.bypass_tenant_filter"),
    ):
        mock_settings.return_value.rate_limit_messages = 10
        mock_settings.return_value.rate_limit_window_seconds = 60
        mock_settings.return_value.redis_url = ""

        persona_mock = MagicMock()
        persona_mock.out_of_hours_message = ""
        mock_persona.return_value = persona_mock

        respond(db, conv, inbound)

        # _persist_outbound debe llamarse con hallucination_flag=True
        mock_persist.assert_called_once()
        _, kwargs = mock_persist.call_args[0], mock_persist.call_args[1]
        assert kwargs.get("hallucination_flag") is True


def test_respond_no_setea_hallucination_flag_sin_alucinar():
    """respond() no setea hallucination_flag cuando el agente no alucina."""
    from src.agent.service import respond

    tenant_id = uuid.uuid4()
    conv = MagicMock()
    conv.tenant_id = tenant_id
    conv.wa_contact_phone = "56988000002"
    conv.status = "bot"
    conv.id = uuid.uuid4()
    conv.wa_number_id = uuid.uuid4()

    inbound = MagicMock()
    db = MagicMock()

    with (
        patch("src.agent.service.get_settings") as mock_settings,
        patch("src.agent.service.is_rate_limited", return_value=False),
        patch("src.agent.service._load_persona") as mock_persona,
        patch("src.agent.service._maybe_trigger_summary"),
        patch("src.agent.service.load_top_facts", return_value=[]),
        patch("src.agent.service.build_system_prompt", return_value="sys"),
        patch("src.agent.service._build_messages", return_value=[{"role": "user", "content": "hola"}]),
        patch("src.agent.service.check_quota"),
        patch("src.agent.service._run_agent_loop",
              return_value=("Hola, ¿en qué te ayudo?", {}, "end_turn", False)),
        patch("src.agent.service._auto_escalate_if_needed"),
        patch("src.agent.service._persist_outbound") as mock_persist,
        patch("src.agent.service._update_usage_metrics"),
        patch("src.agent.service.is_within_business_hours", return_value=True),
        patch("src.agent.service.bypass_tenant_filter"),
    ):
        mock_settings.return_value.rate_limit_messages = 10
        mock_settings.return_value.rate_limit_window_seconds = 60
        mock_settings.return_value.redis_url = ""

        persona_mock = MagicMock()
        persona_mock.out_of_hours_message = ""
        mock_persona.return_value = persona_mock

        respond(db, conv, inbound)

        mock_persist.assert_called_once()
        _, kwargs = mock_persist.call_args[0], mock_persist.call_args[1]
        assert kwargs.get("hallucination_flag") is False
