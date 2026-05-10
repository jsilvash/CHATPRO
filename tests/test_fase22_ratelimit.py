"""Tests Fase 22B: Rate limiting por (tenant_id, wa_contact_phone).

Cobertura:
- is_rate_limited: primera llamada no limita, N-ésima tampoco, N+1 sí.
- Ventana deslizante: mensajes fuera de la ventana no se cuentan.
- Aislamiento: distintos tenants/phones son contadores independientes.
- Integración en service.respond: agent mudo cuando rate-limited, no mudo cuando no.
- clear_for_testing: limpieza del store in-memory.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.agent.rate_limiter import clear_for_testing, is_rate_limited


@pytest.fixture(autouse=True)
def limpiar_store():
    """Limpia el store in-memory antes de cada test."""
    clear_for_testing()
    yield
    clear_for_testing()


# ── is_rate_limited in-memory ────────────────────────────────────────────────


def test_primera_llamada_no_limita():
    tid = uuid.uuid4()
    assert is_rate_limited(tid, "56999111000", limit=3, window_s=60) is False


def test_dentro_del_limite_no_limita():
    tid = uuid.uuid4()
    for _ in range(3):
        resultado = is_rate_limited(tid, "56999111001", limit=3, window_s=60)
    assert resultado is False


def test_superar_limite_retorna_true():
    tid = uuid.uuid4()
    phone = "56999111002"
    for _ in range(3):
        is_rate_limited(tid, phone, limit=3, window_s=60)
    # La 4ª llamada supera el límite
    assert is_rate_limited(tid, phone, limit=3, window_s=60) is True


def test_limite_exacto_no_limita_al_n():
    """El N-ésimo mensaje (igual al límite) aún no bloquea; el N+1 sí."""
    tid = uuid.uuid4()
    phone = "56999111003"
    for i in range(5):
        resultado = is_rate_limited(tid, phone, limit=5, window_s=60)
    # 5to mensaje = resultado False (igual al límite, aún permitido)
    assert resultado is False
    # 6to mensaje supera el límite
    assert is_rate_limited(tid, phone, limit=5, window_s=60) is True


def test_ventana_expirada_permite_de_nuevo():
    """Mensajes fuera de la ventana temporal no se cuentan."""
    tid = uuid.uuid4()
    phone = "56999111004"
    # Llenamos el límite con timestamps artificialmente viejos
    from src.agent.rate_limiter import _STORE, _STORE_LOCK
    import collections
    old_time = time.time() - 120  # 2 minutos atrás
    with _STORE_LOCK:
        _STORE[(str(tid), phone)] = collections.deque([old_time, old_time, old_time])

    # Ahora debería poder enviar (los 3 anteriores expiraron con ventana de 60s)
    assert is_rate_limited(tid, phone, limit=3, window_s=60) is False


def test_aislamiento_por_tenant():
    """Dos tenants distintos con el mismo phone tienen contadores independientes."""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    phone = "56999111005"

    for _ in range(5):
        is_rate_limited(tid_a, phone, limit=5, window_s=60)
    # Tenant A lleno
    assert is_rate_limited(tid_a, phone, limit=5, window_s=60) is True
    # Tenant B no tiene mensajes
    assert is_rate_limited(tid_b, phone, limit=5, window_s=60) is False


def test_aislamiento_por_phone():
    """Mismo tenant, phones distintos, contadores independientes."""
    tid = uuid.uuid4()
    for _ in range(5):
        is_rate_limited(tid, "56999111006", limit=5, window_s=60)
    # Phone A lleno
    assert is_rate_limited(tid, "56999111006", limit=5, window_s=60) is True
    # Phone B intacto
    assert is_rate_limited(tid, "56999111007", limit=5, window_s=60) is False


def test_clear_for_testing():
    """clear_for_testing() limpia todos los contadores."""
    tid = uuid.uuid4()
    phone = "56999111008"
    for _ in range(10):
        is_rate_limited(tid, phone, limit=5, window_s=60)
    # Estaba limitado
    assert is_rate_limited(tid, phone, limit=5, window_s=60) is True
    clear_for_testing()
    # Después de limpiar, ya no está limitado
    assert is_rate_limited(tid, phone, limit=5, window_s=60) is False


# ── Integración con service.respond ──────────────────────────────────────────


def _make_conv(tenant_id, phone="56999777000"):
    conv = MagicMock()
    conv.tenant_id = tenant_id
    conv.wa_contact_phone = phone
    conv.status = "bot"
    conv.id = uuid.uuid4()
    conv.wa_number_id = uuid.uuid4()
    return conv


def test_respond_silencioso_cuando_rate_limited():
    """service.respond retorna sin llamar al agente si la clave supera el límite."""
    from src.agent.service import respond

    tid = uuid.uuid4()
    phone = "56999888000"
    conv = _make_conv(tid, phone)
    inbound = MagicMock()

    # Llenar el límite en el store
    for _ in range(10):
        is_rate_limited(tid, phone, limit=10, window_s=60)
    # La llamada 11 activará el límite dentro de respond

    db_mock = MagicMock()

    with (
        patch("src.agent.service.get_settings") as mock_settings,
        patch("src.agent.service.is_rate_limited", return_value=True) as mock_rl,
        patch("src.agent.service._load_persona") as mock_persona,
    ):
        mock_settings.return_value.rate_limit_messages = 10
        mock_settings.return_value.rate_limit_window_seconds = 60
        mock_settings.return_value.redis_url = ""

        respond(db_mock, conv, inbound)

        mock_rl.assert_called_once()
        mock_persona.assert_not_called()


def test_respond_llama_agente_cuando_no_rate_limited():
    """service.respond llama al agente si no hay rate limit."""
    from src.agent.service import respond

    tid = uuid.uuid4()
    phone = "56999888001"
    conv = _make_conv(tid, phone)
    inbound = MagicMock()
    db_mock = MagicMock()

    with (
        patch("src.agent.service.get_settings") as mock_settings,
        patch("src.agent.service.is_rate_limited", return_value=False),
        patch("src.agent.service._load_persona", return_value=None) as mock_persona,
    ):
        mock_settings.return_value.rate_limit_messages = 10
        mock_settings.return_value.rate_limit_window_seconds = 60
        mock_settings.return_value.redis_url = ""

        respond(db_mock, conv, inbound)

        # _load_persona se llama porque el rate limit no bloqueó
        mock_persona.assert_called_once()
