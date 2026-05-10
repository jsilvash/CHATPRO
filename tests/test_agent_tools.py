"""Tests E2E de Fase 7: agente con tools (catálogo + escalamiento).

Cobertura:
- collect_tools_for_conversation: built-in + WooCommerce solo si config "connected".
- execute_tool: dispatch ok, unknown_tool, error capturado, timeout.
- Loop tool_use en agent_service: respuesta real desde tabla products.
- Tool escalar_a_humano: conversation.status = "waiting_agent" + outbound.
- Cap max_tool_calls → respuesta de fallback.
- Persistencia en tool_invocations (input/output/latency/status).
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.agent.models import Persona, ToolInvocation
from src.agent.tool_runner import (
    collect_tools_for_conversation,
    execute_tool,
)
from src.connectors.models import ConnectorConfig, ConnectorDef, Product
from src.connectors.woocommerce.tools import buscar_productos
from src.wa.models import WaConversation, WaMessage, WaNumber

# ── Helpers de fixtures ─────────────────────────────────────────────────────


def _make_woo_def(db) -> ConnectorDef:
    existing = db.query(ConnectorDef).filter(ConnectorDef.name == "woocommerce").first()
    if existing:
        return existing
    defn = ConnectorDef(
        id=uuid.uuid4(), name="woocommerce", kind="ecommerce", version="1.0.0"
    )
    db.add(defn)
    db.flush()
    return defn


@pytest.fixture
def woo_setup(db, tenant_a):
    """Tenant A con: persona, wa_number, conversation, connector config 'connected', 2 productos."""
    tenant, _owner = tenant_a

    persona = Persona(
        tenant_id=tenant.id,
        name="Asistente",
        system_prompt="Sos asistente de ventas.",
        tone="amigable",
        locale="es-CL",
        timezone="America/Santiago",
        out_of_hours_message="Fuera de horario.",
        business_hours_json={},
        model_id="claude-sonnet-4-6",
    )
    db.add(persona)
    db.flush()

    wn = WaNumber(
        tenant_id=tenant.id,
        label="Ventas",
        waha_session_name="test-tools-session",
        persona_id=persona.id,
    )
    db.add(wn)
    db.flush()

    conv = WaConversation(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_contact_phone="56999777111",
        wa_contact_name="Cliente Test",
    )
    db.add(conv)
    db.flush()

    inbound = WaMessage(
        tenant_id=tenant.id,
        wa_number_id=wn.id,
        wa_conversation_id=conv.id,
        direction="in",
        text="¿Tenés remeras?",
        wa_message_id="wa-tools-001",
        ack="",
        raw_payload={},
        llm_metadata={},
    )
    db.add(inbound)
    db.flush()

    defn = _make_woo_def(db)
    config = ConnectorConfig(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        connector_def_id=defn.id,
        display_name="Tienda Test",
        status="connected",
    )
    db.add(config)
    db.flush()

    productos = [
        Product(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            connector_config_id=config.id,
            external_id="100",
            sku="REM-M",
            name="Remera lisa M",
            description_short="Remera 100% algodón",
            price_regular=19.99,
            price_sale=14.99,
            currency="USD",
            stock_quantity=10,
            stock_status="in_stock",
            url="https://tienda.com/rem-m",
        ),
        Product(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            connector_config_id=config.id,
            external_id="101",
            sku="REM-L",
            name="Remera lisa L",
            description_short="Remera 100% algodón",
            price_regular=19.99,
            price_sale=None,
            currency="USD",
            stock_quantity=0,
            stock_status="out_of_stock",
            url="https://tienda.com/rem-l",
        ),
    ]
    for p in productos:
        db.add(p)
    db.flush()

    return {
        "tenant": tenant,
        "persona": persona,
        "wa_number": wn,
        "conversation": conv,
        "inbound": inbound,
        "config": config,
        "productos": productos,
    }


# ── Stubs de respuestas Anthropic ──────────────────────────────────────────


def _usage_mock(input_tokens=100, output_tokens=30, cache_read=0):
    u = MagicMock()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    u.cache_read_input_tokens = cache_read
    u.cache_creation_input_tokens = 0
    return u


def _text_block(text: str):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_use_block(name: str, tool_input: dict, tool_id: str = "toolu_test_001"):
    b = MagicMock()
    b.type = "tool_use"
    b.id = tool_id
    b.name = name
    b.input = tool_input
    return b


def _resp_end_turn(text: str = "Listo."):
    r = MagicMock()
    r.content = [_text_block(text)]
    r.usage = _usage_mock()
    r.stop_reason = "end_turn"
    return r


def _resp_tool_use(blocks):
    r = MagicMock()
    r.content = blocks
    r.usage = _usage_mock()
    r.stop_reason = "tool_use"
    return r


# ── Tests del tool_runner directo ──────────────────────────────────────────


class TestCollectTools:
    def test_collect_incluye_builtin_escalar_a_humano(self, db, woo_setup):
        schemas, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        names = {s["name"] for s in schemas}
        assert "escalar_a_humano" in names
        assert "escalar_a_humano" in index
        assert index["escalar_a_humano"].is_builtin is True

    def test_collect_incluye_woocommerce_si_connected(self, db, woo_setup):
        schemas, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        names = {s["name"] for s in schemas}
        assert "buscar_productos" in names
        assert "consultar_stock_y_precio" in names
        assert "historial_pedidos_contacto" in names
        for woo_tool in ["buscar_productos", "consultar_stock_y_precio"]:
            assert index[woo_tool].is_builtin is False
            assert index[woo_tool].extra_kwargs["tenant_id"] == woo_setup["tenant"].id

    def test_collect_omite_conector_si_no_connected(self, db, woo_setup):
        woo_setup["config"].status = "pending"
        db.flush()
        schemas, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        names = {s["name"] for s in schemas}
        assert "buscar_productos" not in names
        # Built-in sigue presente
        assert "escalar_a_humano" in names


class TestExecuteTool:
    def test_dispatch_buscar_productos_real(self, db, woo_setup):
        _, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        result = execute_tool(
            tool_name="buscar_productos",
            tool_input={"query": "remera"},
            tools_index=index,
            db=db,
            conversation=woo_setup["conversation"],
        )
        assert result.status == "success"
        assert result.latency_ms >= 0
        resultados = result.output["resultados"]
        assert len(resultados) == 2
        nombres = {r["nombre"] for r in resultados}
        assert nombres == {"Remera lisa M", "Remera lisa L"}

    def test_unknown_tool(self, db, woo_setup):
        _, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        result = execute_tool(
            tool_name="tool_que_no_existe",
            tool_input={},
            tools_index=index,
            db=db,
            conversation=woo_setup["conversation"],
        )
        assert result.status == "unknown_tool"
        assert "no disponible" in result.output["error"]

    def test_error_capturado(self, db, woo_setup):
        # Forzar error inyectando un tool roto en el índice
        from src.agent.tool_runner import ResolvedTool

        def boom(**_kwargs):
            raise RuntimeError("kaboom")

        _, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        index["explota"] = ResolvedTool(
            schema={"name": "explota", "description": "x", "input_schema": {}},
            callable_func=boom,
            extra_kwargs={"tenant_id": woo_setup["tenant"].id, "config_id": woo_setup["config"].id},
            is_builtin=False,
        )
        result = execute_tool(
            tool_name="explota",
            tool_input={},
            tools_index=index,
            db=db,
            conversation=woo_setup["conversation"],
        )
        assert result.status == "error"
        assert "kaboom" in result.error

    def test_timeout(self, db, woo_setup):
        from src.agent.tool_runner import ResolvedTool

        def lento(**_kwargs):
            time.sleep(2)
            return {"ok": True}

        _, index = collect_tools_for_conversation(db, woo_setup["conversation"])
        index["lento"] = ResolvedTool(
            schema={"name": "lento", "description": "x", "input_schema": {}},
            callable_func=lento,
            extra_kwargs={},
            is_builtin=False,
        )
        result = execute_tool(
            tool_name="lento",
            tool_input={},
            tools_index=index,
            db=db,
            conversation=woo_setup["conversation"],
            timeout_s=0.2,
        )
        assert result.status == "timeout"


# ── Tests E2E del loop en agent.service.respond ────────────────────────────


class TestAgentServiceLoop:
    def test_respond_con_tool_use_devuelve_productos_reales(self, db, woo_setup):
        """Mock Claude → tool_use(buscar_productos) → tool_result real → end_turn."""
        conv = woo_setup["conversation"]
        inbound = woo_setup["inbound"]

        captured_tool_results = []

        def fake_create(**kwargs):
            msgs = kwargs.get("messages", [])
            already_called_tool = any(
                isinstance(m.get("content"), list)
                and any(b.get("type") == "tool_result" for b in m["content"] if isinstance(b, dict))
                for m in msgs
            )
            if already_called_tool:
                # Capturar contenido del tool_result para asertar
                for m in msgs:
                    if not isinstance(m.get("content"), list):
                        continue
                    for b in m["content"]:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            captured_tool_results.append(b.get("content"))
                return _resp_end_turn(
                    "Tenemos Remera lisa M a USD 14.99 — https://tienda.com/rem-m"
                )
            return _resp_tool_use(
                [_tool_use_block("buscar_productos", {"query": "remera"}, tool_id="toolu_001")]
            )

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = fake_create
            mock_send.return_value = {"id": {"_serialized": "true_56999777111_T01"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        # Outbound persistido con texto final
        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert "Remera" in outbound.text
        assert outbound.llm_metadata["end_reason"] == "end_turn"
        assert outbound.llm_metadata["input_tokens"] >= 200  # 2 llamadas a Claude

        # tool_result que vio Claude debe contener datos reales del catálogo
        assert any("rem-m" in str(r).lower() for r in captured_tool_results)

        # Registro en tool_invocations
        invs = (
            db.query(ToolInvocation)
            .filter(ToolInvocation.wa_conversation_id == conv.id)
            .all()
        )
        assert len(invs) == 1
        inv = invs[0]
        assert inv.tool_name == "buscar_productos"
        assert inv.status == "success"
        assert inv.tool_use_id == "toolu_001"
        assert inv.input == {"query": "remera"}
        assert "resultados" in inv.output

    def test_respond_escalar_a_humano_marca_conversation(self, db, woo_setup):
        """Tool escalar_a_humano → conversation.status = waiting_agent + outbound."""
        conv = woo_setup["conversation"]
        inbound = woo_setup["inbound"]

        def fake_create(**_kwargs):
            return _resp_tool_use(
                [_tool_use_block(
                    "escalar_a_humano",
                    {"motivo": "queja"},
                    tool_id="toolu_esc",
                )]
            )

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = fake_create
            mock_send.return_value = {"id": {"_serialized": "true_56999777111_ESC"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        db.refresh(conv)
        assert conv.status == "waiting_agent"

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert "humano" in outbound.text.lower()
        assert outbound.llm_metadata["end_reason"] == "escalated"

        # Registrado en tool_invocations con motivo
        inv = (
            db.query(ToolInvocation)
            .filter(ToolInvocation.tool_name == "escalar_a_humano")
            .first()
        )
        assert inv is not None
        assert inv.input == {"motivo": "queja"}
        assert inv.output["ok"] is True

    def test_respond_max_tool_calls_devuelve_fallback(self, db, woo_setup):
        """Claude pide tool_use indefinidamente → cap 5 → fallback."""
        conv = woo_setup["conversation"]
        inbound = woo_setup["inbound"]

        call_counter = {"n": 0}

        def fake_create(**_kwargs):
            call_counter["n"] += 1
            return _resp_tool_use(
                [_tool_use_block(
                    "buscar_productos",
                    {"query": f"x{call_counter['n']}"},
                    tool_id=f"toolu_{call_counter['n']}",
                )]
            )

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = fake_create
            mock_send.return_value = {"id": {"_serialized": "true_56999777111_MAX"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert outbound.llm_metadata["end_reason"] == "max_tool_calls"

        invs = (
            db.query(ToolInvocation)
            .filter(ToolInvocation.wa_conversation_id == conv.id)
            .count()
        )
        # Se loggean exactamente las 5 invocaciones admitidas (la 6ta corta antes).
        assert invs == 5

    def test_respond_sin_tools_disponibles_funciona(self, db, tenant_b):
        """Tenant sin connector configurado → loop sin tools, end_turn directo."""
        tenant, _ = tenant_b
        persona = Persona(
            tenant_id=tenant.id,
            name="Bot",
            system_prompt="Hola.",
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
            tenant_id=tenant.id,
            label="N",
            waha_session_name="t-no-tools",
            persona_id=persona.id,
        )
        db.add(wn)
        db.flush()
        conv = WaConversation(
            tenant_id=tenant.id, wa_number_id=wn.id, wa_contact_phone="56988"
        )
        db.add(conv)
        db.flush()
        inbound = WaMessage(
            tenant_id=tenant.id,
            wa_number_id=wn.id,
            wa_conversation_id=conv.id,
            direction="in",
            text="Hola",
            wa_message_id="wa-no-tools",
            ack="",
            raw_payload={},
            llm_metadata={},
        )
        db.add(inbound)
        db.flush()

        sent_kwargs = {}

        def fake_create(**kwargs):
            sent_kwargs.update(kwargs)
            return _resp_end_turn("Hola, ¿en qué te ayudo?")

        with (
            patch("src.agent.llm.anthropic.Anthropic") as mock_cls,
            patch("src.messaging.waha_client.send_text") as mock_send,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = fake_create
            mock_send.return_value = {"id": {"_serialized": "true_56988_NT"}}

            from src.agent import service as agent_service
            agent_service.respond(db, conv, inbound)

        # El built-in escalar_a_humano siempre se envía (tools no vacío)
        assert "tools" in sent_kwargs
        names = {t["name"] for t in sent_kwargs["tools"]}
        assert names == {"escalar_a_humano"}

        outbound = (
            db.query(WaMessage)
            .filter(
                WaMessage.wa_conversation_id == conv.id,
                WaMessage.direction == "out",
            )
            .first()
        )
        assert outbound is not None
        assert outbound.text == "Hola, ¿en qué te ayudo?"


# ── Test directo del tool buscar_productos con db inyectado ─────────────────


class TestBuscarProductosDB:
    def test_buscar_productos_devuelve_match_por_nombre(self, db, woo_setup):
        out = buscar_productos(
            "remera lisa M",
            tenant_id=woo_setup["tenant"].id,
            config_id=woo_setup["config"].id,
            db=db,
        )
        assert len(out["resultados"]) == 1
        assert out["resultados"][0]["sku"] == "REM-M"
        assert out["resultados"][0]["precio"] == 14.99

    def test_buscar_productos_aislamiento_por_tenant(self, db, woo_setup, tenant_b):
        tenant_b_obj, _ = tenant_b
        out = buscar_productos(
            "remera",
            tenant_id=tenant_b_obj.id,
            config_id=woo_setup["config"].id,
            db=db,
        )
        assert out["resultados"] == []
