"""
Tests Fase 7 — Tools del agente Claude.

Cubre:
- Loop tool_use → end_turn con tool real (buscar_productos)
- Cap MAX_TOOL_CALLS → escalación
- Cap MAX_COST_CENTS → escalación
- Tool consultar_stock_y_precio (hit + miss)
- Tool historial_pedidos_contacto (stub fase 7)
- Tool escalar_a_humano
- Anti-hallucination: precio grounded vs. alucinado
- Logging de tool_invocations (session.add llamado)
- collect_tools incluye escalar_a_humano siempre
- Tool timeout → status='timeout'
"""

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

# ─── fixtures comunes ─────────────────────────────────────────────────────────

TENANT_ID = uuid.uuid4()
CONFIG_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
CONTACT_ID = uuid.uuid4()


def _make_connector():
    connector = MagicMock()
    connector.tenant_id = TENANT_ID
    connector.config_id = CONFIG_ID
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


def _mock_session():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = MagicMock()
    return session


def _make_llm_resp(stop_reason, content, cost_usd=0.001):
    """Crea un par (resp, metadata) compatible con call_claude_messages."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    metadata = {"cost_usd": cost_usd, "input_tokens": 100, "output_tokens": 50}
    return resp, metadata


# ================================================================== E2E: buscar_productos

class TestE2EBuscarProductos:
    def test_contacto_pregunta_precio_respuesta_grounded(self):
        """
        Flujo completo: usuario pregunta → Claude llama buscar_productos
        → respuesta incluye precio del tool_result → sin hallucination flag.
        """
        connector = _make_connector()
        session = _mock_session()

        tool_id = "tu_001"
        search_block = _tool_use_block(tool_id, "buscar_productos", {"query": "zapatilla running"})
        final_text = "La Zapatilla Running Pro X cuesta $89.99. Ver en https://shop.example.com/zapas"

        resp_tool, meta_tool = _make_llm_resp("tool_use", [search_block])
        resp_end, meta_end = _make_llm_resp("end_turn", [_text_block(final_text)])

        tool_result = {"resultados": [{"nombre": "Zapatilla Running Pro X", "precio": 89.99, "url": "..."}]}

        with patch("src.agent.service.call_claude_messages",
                   side_effect=[(resp_tool, meta_tool), (resp_end, meta_end)]), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=(tool_result, "ok", 15)):
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
        # precio 89.99 está en tool_result → no flag
        assert not result.hallucination_flag

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


# ================================================================== Cap tool_calls / cost

class TestCapToolCalls:
    def test_max_tool_calls_escala(self):
        connector = _make_connector()

        def always_tool_use(*args, **kwargs):
            block = _tool_use_block("tu_x", "buscar_productos", {"query": "test"})
            return _make_llm_resp("tool_use", [block])

        with patch("src.agent.service.call_claude_messages", side_effect=always_tool_use), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=({"resultados": []}, "ok", 5)):
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
        connector = _make_connector()

        def expensive_call(*args, **kwargs):
            block = _tool_use_block("tu_x", "buscar_productos", {"query": "test"})
            return _make_llm_resp("tool_use", [block], cost_usd=10.0)  # 1000¢ >> límite

        with patch("src.agent.service.call_claude_messages", side_effect=expensive_call), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=({"resultados": []}, "ok", 5)):
            result = run_agent_turn(
                messages=[{"role": "user", "content": "busca todo"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=None,
                max_cost_cents=0.001,
            )

        assert result.stop_reason == "max_cost"
        assert result.total_cost_cents > 0.001


# ================================================================== escalar_a_humano

class TestEscalarAHumano:
    def test_escalar_retorna_stop_reason_escalated(self):
        connector = _make_connector()
        escalar_block = _tool_use_block("tu_esc", "escalar_a_humano", {"motivo": "cliente enojado"})
        resp_tool, meta_tool = _make_llm_resp("tool_use", [escalar_block])

        with patch("src.agent.service.call_claude_messages",
                   return_value=(resp_tool, meta_tool)):
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
    def _make_product(self):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.name = "Remera Básica"
        p.sku = "REM-001"
        p.price_regular = Decimal("29.99")
        p.price_sale = None
        p.currency = "USD"
        p.stock_quantity = 15
        p.stock_status = "in_stock"
        p.url = "https://shop.example.com/remera"
        return p

    def test_busca_por_sku_hit(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.filter.return_value.first.return_value = self._make_product()

        result = consultar_stock_y_precio(
            sku="REM-001",
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
            db=session,
        )

        assert result["sku"] == "REM-001"
        assert result["precio_regular"] == 29.99
        assert result["stock_status"] == "in_stock"

    def test_busca_por_sku_miss(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        result = consultar_stock_y_precio(
            sku="NO-EXISTE",
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
            db=session,
        )

        assert "error" in result

    def test_sin_sku_ni_id_retorna_error(self):
        result = consultar_stock_y_precio(
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
        )
        assert "error" in result

    def test_busca_por_product_id(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.filter.return_value.first.return_value = self._make_product()

        result = consultar_stock_y_precio(
            product_id="42",
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
            db=session,
        )
        assert result["nombre"] == "Remera Básica"


# ================================================================== buscar_productos (unit)

class TestBuscarProductos:
    def _make_product(self, name="Zapatilla", sku="ZRP-001", precio=89.99):
        p = MagicMock()
        p.id = uuid.uuid4()
        p.external_id = "42"
        p.name = name
        p.sku = sku
        p.price_regular = Decimal(str(precio))
        p.price_sale = None
        p.currency = "USD"
        p.stock_quantity = 10
        p.stock_status = "in_stock"
        p.url = f"https://shop.example.com/{sku.lower()}"
        p.description_short = "Descripción corta"
        return p

    def test_retorna_campos_requeridos(self):
        """buscar_productos delega en WooCommerceConnector.search() (Fase 18)."""
        product = self._make_product()
        fake_result = SearchResult(
            id=str(product.id),
            title=product.name,
            snippet=product.description_short,
            url=product.url,
            score=0.016,
            metadata={
                "sku": product.sku,
                "external_id": product.external_id,
                "price_regular": float(product.price_regular),
                "price_sale": None,
                "currency": product.currency,
                "stock_quantity": product.stock_quantity,
                "stock_status": product.stock_status,
            },
        )

        with patch("src.connectors.woocommerce.connector.WooCommerceConnector") as MockConn:
            mock_conn = MagicMock()
            mock_conn.search.return_value = [fake_result]
            MockConn.return_value = mock_conn

            result = buscar_productos(
                query="zapatilla",
                tenant_id=TENANT_ID,
                config_id=CONFIG_ID,
                db=MagicMock(),
            )

        assert "resultados" in result
        assert len(result["resultados"]) == 1
        r = result["resultados"][0]
        assert r["nombre"] == "Zapatilla"
        assert r["stock_status"] == "in_stock"

    def test_sin_resultados_retorna_mensaje(self):
        with patch("src.connectors.woocommerce.connector.WooCommerceConnector") as MockConn:
            mock_conn = MagicMock()
            mock_conn.search.return_value = []
            MockConn.return_value = mock_conn

            result = buscar_productos(
                query="xyzzy_inexistente",
                tenant_id=TENANT_ID,
                config_id=CONFIG_ID,
                db=MagicMock(),
            )

        assert "resultados" in result
        assert result["resultados"] == []
        assert "mensaje" in result


# ================================================================== historial_pedidos_contacto (unit)

class TestHistorialPedidosContacto:
    def test_retorna_stub_con_pedidos_vacio(self):
        """Fase 7: historial es stub, devuelve mensaje orientativo."""
        result = historial_pedidos_contacto(
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
            contact_email="cliente@example.com",
        )
        assert "pedidos" in result
        assert isinstance(result["pedidos"], list)
        assert "mensaje" in result

    def test_sin_contacto_igual_funciona(self):
        """El stub funciona sin parámetros de contacto."""
        result = historial_pedidos_contacto(
            tenant_id=TENANT_ID,
            config_id=CONFIG_ID,
        )
        assert "pedidos" in result


# ================================================================== Anti-hallucination

class TestAntiHallucination:
    def test_precio_grounded_no_flag(self):
        tool_results = [{"resultados": [{"nombre": "Zapatilla", "precio": 89.99}]}]
        response = "La Zapatilla cuesta $89.99. ¡Disponible!"
        assert not _check_anti_hallucination(response, tool_results)

    def test_precio_alucinado_flag_activado(self):
        tool_results = [{"resultados": [{"nombre": "Zapatilla", "precio": 89.99}]}]
        response = "La Zapatilla cuesta $150.00 y está disponible."
        assert _check_anti_hallucination(response, tool_results)

    def test_sin_tool_results_y_precio_en_respuesta_flag(self):
        assert _check_anti_hallucination("El producto cuesta $99.99", [])

    def test_sin_precio_en_respuesta_no_flag(self):
        assert not _check_anti_hallucination("El producto está disponible.", [])

    def test_sin_tool_results_sin_precio_no_flag(self):
        assert not _check_anti_hallucination("Hola, ¿en qué te puedo ayudar?", [])

    def test_precio_en_lista_resultados_no_flag(self):
        tool_results = [{"resultados": [{"precio": 45.0}, {"precio": 120.0}]}]
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

        tool_block = _tool_use_block("tu_log", "buscar_productos", {"query": "remera"})
        resp_tool, meta_tool = _make_llm_resp("tool_use", [tool_block])
        resp_end, meta_end = _make_llm_resp("end_turn", [_text_block("Tenemos la Remera a $29.99")])

        tool_result = [{"nombre": "Remera", "precio": 29.99}]

        with patch("src.agent.service.call_claude_messages",
                   side_effect=[(resp_tool, meta_tool), (resp_end, meta_end)]), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=(tool_result, "ok", 42)):
            result = run_agent_turn(
                messages=[{"role": "user", "content": "¿Tenés remeras?"}],
                conversation_id=CONV_ID,
                tenant_id=TENANT_ID,
                connector=connector,
                session=session,
            )

        assert result.stop_reason == "ok"
        session.add.assert_called_once()
        from src.agent.models import ToolInvocation
        added = session.add.call_args[0][0]
        assert isinstance(added, ToolInvocation)
        assert added.tool_name == "buscar_productos"
        assert added.status == "ok"
        assert added.latency_ms == 42

    def test_sin_session_no_falla(self):
        connector = _make_connector()
        tool_block = _tool_use_block("tu_ns", "buscar_productos", {"query": "test"})
        resp_tool, meta_tool = _make_llm_resp("tool_use", [tool_block])
        resp_end, meta_end = _make_llm_resp("end_turn", [_text_block("Resultado sin precio")])

        with patch("src.agent.service.call_claude_messages",
                   side_effect=[(resp_tool, meta_tool), (resp_end, meta_end)]), \
             patch("src.agent.service._execute_tool_fase7",
                   return_value=([], "ok", 5)):
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
