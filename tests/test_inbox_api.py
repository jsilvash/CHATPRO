"""Tests de Fase 8: Inbox + handoff humano.

Cobertura:
- GET /v1/inbox: list con filtros status/wa_number_id + total correcto.
- GET /v1/inbox/{id}: detalle con mensajes, tool_invocations, handoff_events.
- POST /v1/inbox/{id}/take: asignar conversación (status=agent + HandoffEvent).
- POST /v1/inbox/{id}/reply: enviar outbound como agente, persiste WaMessage.
- POST /v1/inbox/{id}/close: devolver al bot (status=bot, cierra HandoffEvent).
- Aislamiento de tenant: un tenant no ve ni opera conv de otro.
- Bot mudo: agent.service.respond no responde si status != "bot".
- escalar_a_humano: crea HandoffEvent con motivo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agent.models import Persona
from src.agent.service import respond
from src.inbox.models import HandoffEvent
from src.messaging import dispatcher, waha_client
from src.wa.models import WaConversation, WaMessage, WaNumber

# ── Helpers de fixtures ─────────────────────────────────────────────────────


def _make_number(db, tenant_id, session_name: str) -> WaNumber:
    wn = WaNumber(
        tenant_id=tenant_id,
        label="Soporte",
        waha_session_name=session_name,
    )
    db.add(wn)
    db.flush()
    return wn


def _make_conv(db, tenant_id, wa_number_id, phone: str, status: str = "bot") -> WaConversation:
    conv = WaConversation(
        tenant_id=tenant_id,
        wa_number_id=wa_number_id,
        wa_contact_phone=phone,
        wa_contact_name="Cliente",
        status=status,
        last_message_at=datetime.now(UTC),
    )
    db.add(conv)
    db.flush()
    return conv


def _make_message(db, tenant_id, wn_id, conv_id, text: str, direction: str = "in") -> WaMessage:
    msg = WaMessage(
        tenant_id=tenant_id,
        wa_number_id=wn_id,
        wa_conversation_id=conv_id,
        direction=direction,
        text=text,
        wa_message_id=f"wa-{uuid.uuid4().hex[:8]}",
        ack="",
        raw_payload={},
        llm_metadata={},
    )
    db.add(msg)
    db.flush()
    return msg


def _make_handoff(db, tenant_id, conv_id, motivo: str = "test") -> HandoffEvent:
    ev = HandoffEvent(
        tenant_id=tenant_id,
        wa_conversation_id=conv_id,
        motivo=motivo,
        opened_at=datetime.now(UTC),
    )
    db.add(ev)
    db.flush()
    return ev


# ── Fixture compartida ──────────────────────────────────────────────────────


@pytest.fixture
def inbox_setup(db, tenant_a, client_a):
    """Tenant A con un WaNumber y tres conversaciones en distintos status."""
    tenant, owner = tenant_a
    wn = _make_number(db, tenant.id, "inbox-session-a")

    conv_bot = _make_conv(db, tenant.id, wn.id, "56900000001", status="bot")
    conv_wait = _make_conv(db, tenant.id, wn.id, "56900000002", status="waiting_agent")
    conv_agent = _make_conv(db, tenant.id, wn.id, "56900000003", status="agent")

    _make_message(db, tenant.id, wn.id, conv_wait.id, "Necesito ayuda")
    ev = _make_handoff(db, tenant.id, conv_wait.id, "solicitado_por_cliente")

    return {
        "tenant": tenant,
        "owner": owner,
        "wn": wn,
        "conv_bot": conv_bot,
        "conv_wait": conv_wait,
        "conv_agent": conv_agent,
        "handoff_event": ev,
        "client": client_a,
    }


# ── LIST ────────────────────────────────────────────────────────────────────


class TestInboxList:
    def test_lista_todas_las_conversaciones(self, inbox_setup):
        client = inbox_setup["client"]
        r = client.get("/v1/inbox")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_filtro_status_waiting_agent(self, inbox_setup):
        client = inbox_setup["client"]
        r = client.get("/v1/inbox?status=waiting_agent")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "waiting_agent"

    def test_filtro_status_bot(self, inbox_setup):
        client = inbox_setup["client"]
        r = client.get("/v1/inbox?status=bot")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_filtro_wa_number_id(self, inbox_setup, db, tenant_a):
        """Filtrar por wa_number_id devuelve solo convs de ese número."""
        tenant, _ = tenant_a
        otro_wn = _make_number(db, tenant.id, "inbox-session-otro")
        _make_conv(db, tenant.id, otro_wn.id, "56900000099")

        client = inbox_setup["client"]
        wn_id = str(inbox_setup["wn"].id)
        r = client.get(f"/v1/inbox?wa_number_id={wn_id}")
        assert r.status_code == 200
        assert r.json()["total"] == 3

    def test_sin_autenticacion_devuelve_401(self, client):
        r = client.get("/v1/inbox")
        assert r.status_code == 401


# ── GET DETALLE ─────────────────────────────────────────────────────────────


class TestInboxDetail:
    def test_detalle_incluye_mensajes_y_handoff(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_wait"].id)

        r = client.get(f"/v1/inbox/{conv_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["conversation"]["id"] == conv_id
        assert len(data["messages"]) == 1
        assert data["messages"][0]["text"] == "Necesito ayuda"
        assert len(data["handoff_events"]) == 1
        assert data["handoff_events"][0]["motivo"] == "solicitado_por_cliente"

    def test_detalle_convs_sin_mensajes(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_bot"].id)
        r = client.get(f"/v1/inbox/{conv_id}")
        assert r.status_code == 200
        assert r.json()["messages"] == []

    def test_404_conv_inexistente(self, inbox_setup):
        client = inbox_setup["client"]
        r = client.get(f"/v1/inbox/{uuid.uuid4()}")
        assert r.status_code == 404


# ── TAKE ────────────────────────────────────────────────────────────────────


class TestInboxTake:
    def test_take_waiting_agent_a_agent(self, inbox_setup, db):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_wait"].id)
        owner = inbox_setup["owner"]

        r = client.post(f"/v1/inbox/{conv_id}/take")
        assert r.status_code == 200
        assert r.json()["status"] == "agent"

        db.refresh(inbox_setup["conv_wait"])
        assert inbox_setup["conv_wait"].status == "agent"

        db.refresh(inbox_setup["handoff_event"])
        assert inbox_setup["handoff_event"].agent_user_id == owner.id

    def test_take_conv_bot_devuelve_409(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_bot"].id)
        r = client.post(f"/v1/inbox/{conv_id}/take")
        assert r.status_code == 409

    def test_take_conv_ya_agent_devuelve_409(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_agent"].id)
        r = client.post(f"/v1/inbox/{conv_id}/take")
        assert r.status_code == 409

    def test_take_crea_handoff_si_no_existe(self, inbox_setup, db, tenant_a):
        """Si no hay HandoffEvent previo, take() lo crea."""
        tenant, _ = tenant_a
        wn = inbox_setup["wn"]
        conv = _make_conv(db, tenant.id, wn.id, "56900000050", status="waiting_agent")

        client = inbox_setup["client"]
        r = client.post(f"/v1/inbox/{conv.id}/take")
        assert r.status_code == 200

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is not None
        assert ev.agent_user_id == inbox_setup["owner"].id


# ── REPLY ───────────────────────────────────────────────────────────────────


class TestInboxReply:
    def test_reply_persiste_outbound(self, inbox_setup, db):
        """reply en conv agent envía mensaje y lo persiste en WaMessage."""
        client = inbox_setup["client"]
        conv = inbox_setup["conv_agent"]
        conv_id = str(conv.id)

        fake_resp = {"id": {"_serialized": "true_jid_abc123"}}
        with patch.object(waha_client, "send_text", return_value=fake_resp):
            r = client.post(f"/v1/inbox/{conv_id}/reply", json={"text": "Hola, soy el agente."})

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["wa_message_id"] == "true_jid_abc123"

        msg = db.get(WaMessage, uuid.UUID(data["message_id"]))
        assert msg is not None
        assert msg.direction == "out"
        assert msg.text == "Hola, soy el agente."
        assert msg.ack == "sent"

    def test_reply_waiting_agent_promueve_a_agent(self, inbox_setup, db):
        """reply en conv waiting_agent la promueve a agent automáticamente."""
        client = inbox_setup["client"]
        conv = inbox_setup["conv_wait"]
        conv_id = str(conv.id)

        fake_resp = {"id": {"_serialized": "true_jid_xyz999"}}
        with patch.object(waha_client, "send_text", return_value=fake_resp):
            r = client.post(f"/v1/inbox/{conv_id}/reply", json={"text": "Enseguida te ayudo."})

        assert r.status_code == 200
        db.refresh(conv)
        assert conv.status == "agent"

    def test_reply_conv_bot_devuelve_409(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_bot"].id)
        r = client.post(f"/v1/inbox/{conv_id}/reply", json={"text": "Hola."})
        assert r.status_code == 409

    def test_reply_texto_vacio_devuelve_422(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_agent"].id)
        r = client.post(f"/v1/inbox/{conv_id}/reply", json={"text": "   "})
        assert r.status_code == 422


# ── CLOSE ───────────────────────────────────────────────────────────────────


class TestInboxClose:
    def test_close_vuelve_a_bot(self, inbox_setup, db):
        client = inbox_setup["client"]
        conv = inbox_setup["conv_agent"]
        conv_id = str(conv.id)

        r = client.post(f"/v1/inbox/{conv_id}/close")
        assert r.status_code == 200
        assert r.json()["status"] == "bot"

        db.refresh(conv)
        assert conv.status == "bot"

    def test_close_cierra_handoff_event(self, inbox_setup, db, tenant_a):
        """close() pone closed_at en el HandoffEvent abierto."""
        tenant, _ = tenant_a
        wn = inbox_setup["wn"]
        conv = _make_conv(db, tenant.id, wn.id, "56900000060", status="agent")
        ev = _make_handoff(db, tenant.id, conv.id, "apertura_manual")
        assert ev.closed_at is None

        client = inbox_setup["client"]
        r = client.post(f"/v1/inbox/{conv.id}/close")
        assert r.status_code == 200

        db.refresh(ev)
        assert ev.closed_at is not None

    def test_close_conv_ya_bot_devuelve_409(self, inbox_setup):
        client = inbox_setup["client"]
        conv_id = str(inbox_setup["conv_bot"].id)
        r = client.post(f"/v1/inbox/{conv_id}/close")
        assert r.status_code == 409


# ── AISLAMIENTO DE TENANT ───────────────────────────────────────────────────


class TestInboxIsolation:
    def test_tenant_b_no_ve_convs_de_tenant_a(self, inbox_setup, client_b, db):
        """Tenant B no puede listar convs de tenant A."""
        r = client_b.get("/v1/inbox")
        assert r.status_code == 200
        # Tenant B no tiene convs propias → total 0
        assert r.json()["total"] == 0

    def test_tenant_b_no_puede_hacer_get_de_conv_de_tenant_a(self, inbox_setup, client_b):
        conv_id = str(inbox_setup["conv_wait"].id)
        r = client_b.get(f"/v1/inbox/{conv_id}")
        assert r.status_code == 404

    def test_tenant_b_no_puede_take_conv_de_tenant_a(self, inbox_setup, client_b):
        conv_id = str(inbox_setup["conv_wait"].id)
        r = client_b.post(f"/v1/inbox/{conv_id}/take")
        assert r.status_code == 404

    def test_tenant_b_no_puede_reply_conv_de_tenant_a(self, inbox_setup, client_b):
        conv_id = str(inbox_setup["conv_agent"].id)
        r = client_b.post(f"/v1/inbox/{conv_id}/reply", json={"text": "Hola."})
        assert r.status_code == 404

    def test_tenant_b_no_puede_close_conv_de_tenant_a(self, inbox_setup, client_b):
        conv_id = str(inbox_setup["conv_agent"].id)
        r = client_b.post(f"/v1/inbox/{conv_id}/close")
        assert r.status_code == 404


# ── BOT MUDO ────────────────────────────────────────────────────────────────


class TestBotMudo:
    """Verifica que agent.service.respond no responda si status != 'bot'."""

    def _make_persona_and_wn(self, db, tenant_id, session_name):
        persona = Persona(
            tenant_id=tenant_id,
            name="Bot Test",
            system_prompt="Sos un asistente.",
            tone="amigable",
            locale="es-CL",
            timezone="America/Santiago",
            out_of_hours_message="",
            business_hours_json={},
            model_id="claude-sonnet-4-6",
        )
        db.add(persona)
        db.flush()

        wn = WaNumber(
            tenant_id=tenant_id,
            label="Bot",
            waha_session_name=session_name,
            persona_id=persona.id,
        )
        db.add(wn)
        db.flush()
        return persona, wn

    def test_bot_responde_cuando_status_es_bot(self, db, tenant_a):
        tenant, _ = tenant_a
        _, wn = self._make_persona_and_wn(db, tenant.id, "bot-mudo-ok")

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56988880001",
            status="bot",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Hola",
            wa_message_id="wa-mudo-001",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        with patch("src.agent.service.call_claude_messages") as mock_claude, \
             patch.object(dispatcher, "send_text") as mock_send:
            mock_response = MagicMock()
            mock_response.stop_reason = "end_turn"
            mock_response.content = [MagicMock(type="text", text="Hola! ¿En qué te ayudo?")]
            mock_claude.return_value = (mock_response, {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001})
            mock_send.return_value = dispatcher.DispatchResult(success=True, wa_message_id="wa-out-001")

            respond(db, conv, inbound)

        mock_send.assert_called_once()

    def test_bot_mudo_cuando_waiting_agent(self, db, tenant_a):
        tenant, _ = tenant_a
        _, wn = self._make_persona_and_wn(db, tenant.id, "bot-mudo-wait")

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56988880002",
            status="waiting_agent",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Sigo esperando...",
            wa_message_id="wa-mudo-002",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        with patch("src.agent.service.call_claude_messages") as mock_claude, \
             patch.object(dispatcher, "send_text") as mock_send:
            respond(db, conv, inbound)

        mock_claude.assert_not_called()
        mock_send.assert_not_called()

    def test_bot_mudo_cuando_agent(self, db, tenant_a):
        tenant, _ = tenant_a
        _, wn = self._make_persona_and_wn(db, tenant.id, "bot-mudo-agent")

        conv = WaConversation(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_contact_phone="56988880003",
            status="agent",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="¿Siguen ahí?",
            wa_message_id="wa-mudo-003",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        with patch("src.agent.service.call_claude_messages") as mock_claude, \
             patch.object(dispatcher, "send_text") as mock_send:
            respond(db, conv, inbound)

        mock_claude.assert_not_called()
        mock_send.assert_not_called()


# ── ESCALAR_A_HUMANO CREA HANDOFF_EVENT ─────────────────────────────────────


class TestEscalarAHumano:
    def test_escalar_crea_handoff_event(self, db, tenant_a):
        """escalar_a_humano() persiste HandoffEvent + status=waiting_agent."""
        from src.agent.tool_runner import escalar_a_humano

        tenant, _ = tenant_a
        wn = _make_number(db, tenant.id, "escalar-session-x")
        conv = _make_conv(db, tenant.id, wn.id, "56977770001", status="bot")

        resultado = escalar_a_humano(
            motivo="cliente_molesto",
            db=db,
            conversation=conv,
        )

        assert resultado["ok"] is True
        assert resultado["motivo"] == "cliente_molesto"
        assert conv.status == "waiting_agent"

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is not None
        assert ev.motivo == "cliente_molesto"
        assert ev.opened_at is not None
        assert ev.closed_at is None
        assert ev.agent_user_id is None

    def test_escalar_motivo_default(self, db, tenant_a):
        """Motivo vacío usa default 'solicitado_por_cliente'."""
        from src.agent.tool_runner import escalar_a_humano

        tenant, _ = tenant_a
        wn = _make_number(db, tenant.id, "escalar-session-y")
        conv = _make_conv(db, tenant.id, wn.id, "56977770002", status="bot")

        resultado = escalar_a_humano(db=db, conversation=conv)

        assert resultado["motivo"] == "solicitado_por_cliente"
        ev = db.query(HandoffEvent).filter(
            HandoffEvent.wa_conversation_id == conv.id
        ).first()
        assert ev.motivo == "solicitado_por_cliente"


# ── AUTO-ESCALATE DESDE respond() ───────────────────────────────────────────


class TestAutoEscalate:
    """Verifica que respond() auto-transiciona a waiting_agent en casos de cap."""

    def _make_setup(self, db, tenant_id, session_name, phone):
        from src.agent.models import Persona

        persona = Persona(
            tenant_id=tenant_id,
            name="Bot Auto",
            system_prompt="Sos un asistente.",
            tone="amigable",
            locale="es-CL",
            timezone="America/Santiago",
            out_of_hours_message="",
            business_hours_json={},
            model_id="claude-sonnet-4-6",
        )
        db.add(persona)
        db.flush()

        wn = WaNumber(
            tenant_id=tenant_id,
            label="AutoBot",
            waha_session_name=session_name,
            persona_id=persona.id,
        )
        db.add(wn)
        db.flush()

        conv = WaConversation(
            tenant_id=tenant_id,
            wa_number_id=wn.id,
            wa_contact_phone=phone,
            status="bot",
        )
        db.add(conv)
        db.flush()

        inbound = WaMessage(
            tenant_id=tenant_id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Necesito ayuda urgente",
            wa_message_id=f"wa-auto-{phone[-4:]}",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()
        return wn, conv, inbound

    def test_max_tool_calls_auto_escala_a_waiting_agent(self, db, tenant_a):
        """respond() con max_tool_calls transiciona a waiting_agent + HandoffEvent."""
        from src.agent.service import respond
        from src.messaging import dispatcher

        tenant, _ = tenant_a
        wn, conv, inbound = self._make_setup(
            db, tenant.id, "auto-esc-tools", "56955550001"
        )

        # Simulamos que Claude devuelve max_tool_calls desde _run_agent_loop.
        with patch("src.agent.service._run_agent_loop") as mock_loop, \
             patch.object(dispatcher, "send_text") as mock_send:
            mock_loop.return_value = (
                "He llegado al límite de consultas. Un agente humano te ayudará.",
                {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "model": "claude-sonnet-4-6"},
                "max_tool_calls",
                False,
            )
            mock_send.return_value = dispatcher.DispatchResult(
                success=True, wa_message_id="wa-out-auto-001"
            )
            respond(db, conv, inbound)

        db.refresh(conv)
        assert conv.status == "waiting_agent"

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is not None
        assert "max_tool_calls" in ev.motivo
        assert ev.opened_at is not None
        assert ev.closed_at is None

    def test_max_cost_auto_escala_a_waiting_agent(self, db, tenant_a):
        """respond() con max_cost transiciona a waiting_agent + HandoffEvent."""
        from src.agent.service import respond
        from src.messaging import dispatcher

        tenant, _ = tenant_a
        wn, conv, inbound = self._make_setup(
            db, tenant.id, "auto-esc-cost", "56955550002"
        )

        with patch("src.agent.service._run_agent_loop") as mock_loop, \
             patch.object(dispatcher, "send_text") as mock_send:
            mock_loop.return_value = (
                "La consulta superó el límite. Un agente humano te ayudará.",
                {"input_tokens": 50, "output_tokens": 20, "cost_usd": 0.06, "model": "claude-sonnet-4-6"},
                "max_cost",
                False,
            )
            mock_send.return_value = dispatcher.DispatchResult(
                success=True, wa_message_id="wa-out-auto-002"
            )
            respond(db, conv, inbound)

        db.refresh(conv)
        assert conv.status == "waiting_agent"

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is not None
        assert "max_cost" in ev.motivo

    def test_hard_limit_auto_escala_a_waiting_agent(self, db, tenant_a):
        """respond() con hard_limit transiciona a waiting_agent + HandoffEvent."""
        from src.agent.service import respond
        from src.messaging import dispatcher

        tenant, _ = tenant_a
        wn, conv, inbound = self._make_setup(
            db, tenant.id, "auto-esc-hard", "56955550003"
        )

        with patch("src.agent.service._run_agent_loop") as mock_loop, \
             patch.object(dispatcher, "send_text") as mock_send:
            mock_loop.return_value = (
                "Disculpá, no logro resolverlo desde acá. Te derivo a un humano.",
                {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.02, "model": "claude-sonnet-4-6"},
                "hard_limit",
                False,
            )
            mock_send.return_value = dispatcher.DispatchResult(
                success=True, wa_message_id="wa-out-auto-003"
            )
            respond(db, conv, inbound)

        db.refresh(conv)
        assert conv.status == "waiting_agent"

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is not None
        assert "hard_limit" in ev.motivo

    def test_end_turn_no_escala(self, db, tenant_a):
        """respond() con end_turn no crea HandoffEvent ni cambia status."""
        from src.agent.service import respond
        from src.messaging import dispatcher

        tenant, _ = tenant_a
        wn, conv, inbound = self._make_setup(
            db, tenant.id, "auto-esc-ok", "56955550004"
        )

        with patch("src.agent.service._run_agent_loop") as mock_loop, \
             patch.object(dispatcher, "send_text") as mock_send:
            mock_loop.return_value = (
                "Claro, te ayudo con tu consulta.",
                {"input_tokens": 10, "output_tokens": 8, "cost_usd": 0.0005, "model": "claude-sonnet-4-6"},
                "end_turn",
                False,
            )
            mock_send.return_value = dispatcher.DispatchResult(
                success=True, wa_message_id="wa-out-auto-004"
            )
            respond(db, conv, inbound)

        db.refresh(conv)
        assert conv.status == "bot"

        ev = (
            db.query(HandoffEvent)
            .filter(HandoffEvent.wa_conversation_id == conv.id)
            .first()
        )
        assert ev is None


# ── ASSIGNED_USER_ID ─────────────────────────────────────────────────────────


class TestAssignedUserId:
    """Verifica que assigned_user_id se gestiona correctamente en take/close/assign."""

    def test_take_setea_assigned_user_id(self, inbox_setup, db):
        owner = inbox_setup["owner"]
        conv = inbox_setup["conv_wait"]
        client = inbox_setup["client"]

        r = client.post(f"/v1/inbox/{conv.id}/take")
        assert r.status_code == 200
        assert r.json()["assigned_user_id"] == str(owner.id)

        db.refresh(conv)
        assert conv.assigned_user_id == owner.id

    def test_assign_endpoint_setea_assigned_user_id(self, inbox_setup, db, tenant_a):
        tenant, _ = tenant_a
        wn = inbox_setup["wn"]
        conv = _make_conv(db, tenant.id, wn.id, "56900000070", status="waiting_agent")
        _make_handoff(db, tenant.id, conv.id, "esperando")

        owner = inbox_setup["owner"]
        client = inbox_setup["client"]

        r = client.post(f"/v1/inbox/{conv.id}/assign")
        assert r.status_code == 200
        assert r.json()["assigned_user_id"] == str(owner.id)
        assert r.json()["status"] == "agent"

        db.refresh(conv)
        assert conv.assigned_user_id == owner.id

    def test_close_limpia_assigned_user_id(self, inbox_setup, db, tenant_a):
        tenant, _ = tenant_a
        wn = inbox_setup["wn"]
        conv = _make_conv(db, tenant.id, wn.id, "56900000071", status="agent")

        client = inbox_setup["client"]
        r = client.post(f"/v1/inbox/{conv.id}/close")
        assert r.status_code == 200

        db.refresh(conv)
        assert conv.assigned_user_id is None
        assert conv.status == "bot"
