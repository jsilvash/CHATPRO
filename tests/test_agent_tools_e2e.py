"""
Tests Fase 7 — Tools del agente Claude.

Cubre:
- Loop tool_use → end_turn con tool real (buscar_productos)
- Cap MAX_TOOL_CALLS → escalación
- Cap MAX_COST_CENTS → escalación
- Tool consultar_stock_y_precio (hit + miss)
- Tool historial_pedidos_contacto
- Tool escalar_a_humano
- Anti-hallucination: precio grounded vs. alucinado
- Logging de tool_invocations (session.add llamado)
- collect_tools incluye escalar_a_humano siempre
- Tool timeout → status='timeout'
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.agent.service import (
    AgentTurnResult,
    _check_anti_hallucination,
    _normalize_price,
    collect_tools,
    run_agent_turn,
)
from src.connectors.base import SearchResult, ToolSchema
from src.connectors.woocommerce.tools import (
    buscar_productos,
    consultar_stock_y_precio,
    historial_pedidos_contacto,
)

# ------------------------------------------------------------------ fixtures

TENANT_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
CONTACT_ID = uuid.uuid4()

_PRODUCT_SEARCH_RESULT = SearchResult(
    id="42",
    title="Zapatilla Running Pro X",
    snippet="Zapatilla de alto rendimiento para running.",
    url="https://shop.example.com/zapatilla-running-pro-x",
    score=0.95,
    metadata={"sku": "ZRP-001", "price": "89.99", "stock_status": "in_stock"},
)


def _make_connector(search_results=None):
    """Crea un connector mock con ToolSchema reales (no MagicMock) para que
    tool_registry funcione correctamente en run_agent_turn."""
    connector = MagicMock()
    connector.tenant_id = TENANT_ID
    connector.config_id = uuid.uuid4()
    connector.expose_tools.return_value = [
        ToolSchema(
            name="buscar_productos",
            description="Busca productos.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            callable_ref="src.connectors.woocommerce.tools:buscar_productos",
        ),
        ToolSchema(
            name="consultar_stock_y_precio",
            description="Consulta stock.",
            input_schema={"type": "object", "properties": {"sku": {"type": "string"}}},
            callable_ref="src.connectors.woocommerce.tools:consultar_stock_y_precio",
        ),
        ToolSchema(
            name="historial_pedidos_contacto",
            description="Lista pedidos.",
            input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
            callable_ref="src.connectors.woocommerce.tools:historial_pedidos_contacto",
        ),
    ]
    connector.search.return_value = search_results or [_PRODUCT_SEARCH_RESULT]
    return connector


def _tool_use_block(tool_id, name, input_dict):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_dict
    return block


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_usage(input_tokens=100, output_tokens=50):
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    return usage


def _make_resp(stop_reason, content, input_tokens=100, output_tokens=50):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    resp.usage = _make_usage(input_tokens, output_tokens)
    return resp


def _mock_session():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = MagicMock()
    return session


# ================================================================== E2E: buscar_productos → respuesta grounded

class TestE2EBuscarProductos:
    def test_contacto_pregunta_precio_respuesta_grounded(self):
        """
        Flujo completo: usuario pregunta → Claude llama buscar_productos
        → respuesta incluye precio y link del tool_result → sin hallucination flag.

        El executor llama la función real buscar_productos() que llama a
        connector.search() (mockeado). Verifica grounding de precios.
        """
        # connector.search() devuelve el producto con precio 89.99
        connector = _make_connector()
        session = _mock_session()

        tool_id = "tu_001"
        search_tool_block = _tool_use_block(tool_id, "buscar_productos", {"query": "zapatilla running"})

        # Respuesta grounded: el precio 89.99 viene del tool_result
        final_text = (
            "Tenemos la Zapatilla Running Pro X a $89.99. "
            "Podés comprarla en https://shop.example.com/zapatilla-running-pro-x"
        )

        resp_tool_use = _make_resp("tool_use", [search_tool_block])
        resp_end_turn = _make_resp("end_turn", [_text_block(final_text)])

        with patch("src.agent.service.get_client") as mock_gc:
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.side_effect = [resp_tool_use, resp_end_turn]

            result = run_agent_turn(
                messages=[{"role": "user", "content": "¿Cuánto cuesta la zapatilla running?"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                contact_id=CONTACT_ID,
                connector=connector,
                session=session,
            )

        assert result.stop_reason == "ok"
        assert "89.99" in result.response_text
        assert result.tool_calls_count == 1
        assert result.total_cost_cents > 0
        assert not result.hallucination_flag, (
            f"Anti-hallucination flag inesperado. tool_results={result.tool_results}"
        )
        # Tool invocation debe haberse loggeado
        session.add.assert_called_once()
        session.flush.assert_called_once()

    def test_collect_tools_incluye_escalar_siempre(self):
        connector = _make_connector()
        tools = collect_tools(connector)
        names = [t.name for t in tools]
        assert "escalar_a_humano" in names
        assert "buscar_productos" in names

    def test_collect_tools_sin_connector(self):
        tools = collect_tools(None)
        assert len(tools) == 1
        assert tools[0].name == "escalar_a_humano"


# ================================================================== Cap tool_calls

class TestCapToolCalls:
    def test_max_tool_calls_escala(self):
        """Cuando se alcanzan MAX_TOOL_CALLS, el agente escala sin llamar más al LLM."""
        connector = _make_connector()

        # Cada llamada LLM retorna tool_use para disparar el cap
        def always_tool_use(*args, **kwargs):
            block = _tool_use_block("tu_x", "buscar_productos", {"query": "test"})
            return _make_resp("tool_use", [block])

        # Patch en src.agent.service (donde está importado) no en tool_executor
        with patch("src.agent.service.get_client") as mock_gc, \
             patch("src.agent.service.execute_tool",
                   return_value=({"resultados": []}, "ok", 5)):
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.side_effect = always_tool_use

            result = run_agent_turn(
                messages=[{"role": "user", "content": "busca todo"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
                max_tool_calls=3,
            )

        assert result.stop_reason == "max_tool_calls"
        assert result.tool_calls_count == 3

    def test_max_cost_escala(self):
        """Cuando el costo acumulado supera el límite, el agente escala."""
        connector = _make_connector()

        def expensive_tool_use(*args, **kwargs):
            block = _tool_use_block("tu_x", "buscar_productos", {"query": "test"})
            # 10M input tokens → costo gigante
            return _make_resp("tool_use", [block], input_tokens=10_000_000, output_tokens=0)

        with patch("src.agent.service.get_client") as mock_gc, \
             patch("src.agent.service.execute_tool",
                   return_value=({"resultados": []}, "ok", 5)):
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.side_effect = expensive_tool_use

            result = run_agent_turn(
                messages=[{"role": "user", "content": "busca todo"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
                max_cost_cents=0.001,  # cap mínimo
            )

        assert result.stop_reason == "max_cost"
        assert result.total_cost_cents > 0.001


# ================================================================== escalar_a_humano

class TestEscalarAHumano:
    def test_escalar_retorna_stop_reason_escalated(self):
        connector = _make_connector()
        escalar_block = _tool_use_block("tu_esc", "escalar_a_humano", {"motivo": "cliente enojado"})
        resp_tool_use = _make_resp("tool_use", [escalar_block])

        with patch("src.agent.service.get_client") as mock_gc:
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.return_value = resp_tool_use

            result = run_agent_turn(
                messages=[{"role": "user", "content": "quiero hablar con una persona"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
            )

        assert result.stop_reason == "escalated"
        assert result.tool_calls_count == 1


# ================================================================== consultar_stock_y_precio (unit)

class TestConsultarStockYPrecio:
    def _make_product_mock(self):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "Remera Básica"
        p.sku = "REM-001"
        p.price_regular = Decimal("29.99")
        p.price_sale = None
        p.stock_quantity = 15
        p.stock_status = "in_stock"
        p.url = "https://shop.example.com/remera-basica"
        return p

    def test_busca_por_sku_hit(self):
        connector = MagicMock()
        connector.tenant_id = TENANT_ID
        connector.config_id = uuid.uuid4()

        product = self._make_product_mock()
        session = MagicMock()
        session.scalar.return_value = product

        result = consultar_stock_y_precio(
            sku="REM-001",
            connector=connector,
            session=session,
        )

        assert result["sku"] == "REM-001"
        assert result["precio"] == "29.99"
        assert result["stock_status"] == "in_stock"
        assert result["url"] == "https://shop.example.com/remera-basica"

    def test_busca_por_sku_miss(self):
        connector = MagicMock()
        connector.tenant_id = TENANT_ID
        connector.config_id = uuid.uuid4()

        session = MagicMock()
        session.scalar.return_value = None

        result = consultar_stock_y_precio(
            sku="NO-EXISTE",
            connector=connector,
            session=session,
        )

        assert "error" in result
        assert "NO-EXISTE" in result["error"]

    def test_sin_sku_ni_id_retorna_error(self):
        connector = MagicMock()
        result = consultar_stock_y_precio(connector=connector, session=MagicMock())
        assert "error" in result

    def test_busca_por_product_id(self):
        connector = MagicMock()
        connector.tenant_id = TENANT_ID
        connector.config_id = uuid.uuid4()

        product = self._make_product_mock()
        session = MagicMock()
        session.scalar.return_value = product

        result = consultar_stock_y_precio(
            product_id=42,
            connector=connector,
            session=session,
        )
        assert result["nombre"] == "Remera Básica"


# ================================================================== buscar_productos (unit)

class TestBuscarProductos:
    def test_retorna_campos_requeridos(self):
        connector = MagicMock()
        connector.search.return_value = [_PRODUCT_SEARCH_RESULT]

        results = buscar_productos(query="zapatilla", connector=connector)

        assert len(results) == 1
        r = results[0]
        assert r["nombre"] == "Zapatilla Running Pro X"
        assert r["precio"] == "89.99"
        assert r["stock_status"] == "in_stock"
        assert r["url"] == "https://shop.example.com/zapatilla-running-pro-x"

    def test_max_results_respetado(self):
        connector = MagicMock()
        connector.search.return_value = [_PRODUCT_SEARCH_RESULT] * 3

        buscar_productos(query="test", max_results=2, connector=connector)
        connector.search.assert_called_once_with("test", top_k=2)

    def test_sin_resultados(self):
        connector = MagicMock()
        connector.search.return_value = []
        results = buscar_productos(query="xyzzy_inexistente", connector=connector)
        assert results == []


# ================================================================== historial_pedidos_contacto (unit)

class TestHistorialPedidosContacto:
    def test_sin_contacto_retorna_error(self):
        connector = MagicMock()
        result = historial_pedidos_contacto(connector=connector)
        assert isinstance(result, list)
        assert "error" in result[0]

    def test_con_email_llama_wc_api(self):
        import respx
        import httpx as real_httpx

        connector = MagicMock()
        connector._get_creds.return_value = {
            "site_url": "https://shop.example.com",
            "consumer_key": "ck_test",
            "consumer_secret": "cs_test",
        }

        orders_payload = [
            {
                "id": 101,
                "status": "completed",
                "total": "89.99",
                "currency": "USD",
                "date_created": "2026-05-01T10:00:00",
                "line_items": [
                    {"name": "Zapatilla Running Pro X", "quantity": 1, "total": "89.99"}
                ],
            }
        ]

        with respx.mock(base_url="https://shop.example.com") as mock_api:
            mock_api.get("/wc/v3/orders").mock(
                return_value=real_httpx.Response(200, json=orders_payload)
            )
            result = historial_pedidos_contacto(
                contact_email="cliente@example.com",
                connector=connector,
            )

        assert len(result) == 1
        assert result[0]["id"] == 101
        assert result[0]["estado"] == "completed"
        assert result[0]["items"][0]["nombre"] == "Zapatilla Running Pro X"

    def test_api_error_retorna_error(self):
        import respx
        import httpx as real_httpx

        connector = MagicMock()
        connector._get_creds.return_value = {
            "site_url": "https://shop.example.com",
            "consumer_key": "ck_test",
            "consumer_secret": "cs_test",
        }

        with respx.mock(base_url="https://shop.example.com") as mock_api:
            mock_api.get("/wc/v3/orders").mock(
                return_value=real_httpx.Response(401, json={"message": "Unauthorized"})
            )
            result = historial_pedidos_contacto(
                contact_phone="+5491155555555",
                connector=connector,
            )

        assert "error" in result[0]
        assert "401" in result[0]["error"]


# ================================================================== Anti-hallucination

class TestAntiHallucination:
    def test_precio_grounded_no_flag(self):
        tool_results = [{"nombre": "Zapatilla", "precio": "89.99", "stock_status": "in_stock"}]
        response = "La Zapatilla cuesta $89.99. ¡Disponible!"
        assert not _check_anti_hallucination(response, tool_results)

    def test_precio_alucinado_flag_activado(self):
        tool_results = [{"nombre": "Zapatilla", "precio": "89.99"}]
        response = "La Zapatilla cuesta $150.00 y está disponible."
        assert _check_anti_hallucination(response, tool_results)

    def test_sin_tool_results_y_precio_en_respuesta_flag(self):
        assert _check_anti_hallucination("El producto cuesta $99.99", [])

    def test_sin_precio_en_respuesta_no_flag(self):
        assert not _check_anti_hallucination("El producto está disponible.", [])

    def test_sin_tool_results_sin_precio_no_flag(self):
        assert not _check_anti_hallucination("Hola, ¿en qué te puedo ayudar?", [])

    def test_precio_en_lista_tool_results_no_flag(self):
        tool_results = [
            {"resultados": [{"precio": "45.00"}, {"precio": "120.00"}]}
        ]
        response = "Tenés opciones desde $45.00 hasta $120.00"
        assert not _check_anti_hallucination(response, tool_results)

    def test_normalize_price_europeo(self):
        assert _normalize_price("1.500,99") == "1500.99"

    def test_normalize_price_simple(self):
        assert _normalize_price("89.99") == "89.99"

    def test_normalize_price_con_coma(self):
        assert _normalize_price("89,99") == "89.99"


# ================================================================== tool_invocations logging

class TestToolInvocationsLogging:
    def test_tool_invocation_se_persiste(self):
        """Verifica que session.add sea llamado con un ToolInvocation por cada tool call."""
        connector = _make_connector()
        session = _mock_session()

        tool_id = "tu_log"
        tool_block = _tool_use_block(tool_id, "buscar_productos", {"query": "remera"})
        resp_tool = _make_resp("tool_use", [tool_block])
        resp_end = _make_resp("end_turn", [_text_block("Tenemos la Remera Básica a $29.99")])

        # Patch en src.agent.service (donde execute_tool está importado)
        with patch("src.agent.service.get_client") as mock_gc, \
             patch("src.agent.service.execute_tool",
                   return_value=([{"nombre": "Remera", "precio": "29.99"}], "ok", 42)):
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.side_effect = [resp_tool, resp_end]

            result = run_agent_turn(
                messages=[{"role": "user", "content": "¿Tenés remeras?"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=session,
            )

        assert result.stop_reason == "ok"
        session.add.assert_called_once()
        # Verificar que el objeto añadido es ToolInvocation
        from src.agent.models import ToolInvocation
        added = session.add.call_args[0][0]
        assert isinstance(added, ToolInvocation)
        assert added.tool_name == "buscar_productos"
        assert added.status == "ok"
        assert added.latency_ms == 42

    def test_sin_session_no_falla(self):
        """Sin session, run_agent_turn no lanza excepción al intentar loggear."""
        connector = _make_connector()
        tool_block = _tool_use_block("tu_ns", "buscar_productos", {"query": "test"})
        resp_tool = _make_resp("tool_use", [tool_block])
        resp_end = _make_resp("end_turn", [_text_block("Resultado sin precio")])

        with patch("src.agent.service.get_client") as mock_gc, \
             patch("src.agent.service.execute_tool",
                   return_value=([], "ok", 5)):
            mock_client = MagicMock()
            mock_gc.return_value = mock_client
            mock_client.messages.create.side_effect = [resp_tool, resp_end]

            result = run_agent_turn(
                messages=[{"role": "user", "content": "test"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
            )

        assert result.stop_reason == "ok"


# ================================================================== tool_executor timeout

class TestToolExecutorTimeout:
    def test_timeout_retorna_status_timeout(self):
        import time
        from src.agent.tool_executor import execute_tool

        def slow_tool(**_):
            time.sleep(5)
            return {"ok": True}

        with patch("src.agent.tool_executor.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.slow_func = slow_tool
            mock_import.return_value = mock_module

            result, status, latency_ms = execute_tool(
                tool_name="slow_tool",
                tool_input={},
                callable_ref="fake.module:slow_func",
                timeout_s=0.1,
            )

        assert status == "timeout"
        assert result["error"] == "timeout"
        assert latency_ms >= 100
