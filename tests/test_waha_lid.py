"""LID Fix A: ``send_text`` debe levantar ``WahaAPIError(-2)`` cuando el LID no resuelve.

Bug producción FitnessIA 2026-04-23: WhatsApp reinterpretó un ``<LID>@lid``
como número internacional ficticio (+1413…) y entregó el mensaje a un
desconocido. Mejor fallar visible que enviar al lugar equivocado.
"""

from __future__ import annotations

import pytest

from src.messaging import waha_client
from src.messaging.waha_client import WahaAPIError


@pytest.fixture(autouse=True)
def _clear_caches():
    waha_client._clear_lid_cache()
    yield
    waha_client._clear_lid_cache()


def test_resolve_target_lid_unresolved_aborts(monkeypatch):
    """Si el número parece LID y resolve_lid_to_pn devuelve None, raise -2."""
    monkeypatch.setattr(waha_client, "resolve_lid_to_pn", lambda *a, **kw: None)
    with pytest.raises(WahaAPIError) as excinfo:
        waha_client._resolve_target("default", "148726328881285")  # 15 dígitos = LID
    assert excinfo.value.status == -2
    assert "LID" in excinfo.value.message or "número real" in excinfo.value.message


def test_resolve_target_lid_resolved_uses_pn(monkeypatch):
    """Si el LID resuelve, se usa el PN como chatId."""
    monkeypatch.setattr(
        waha_client, "resolve_lid_to_pn", lambda *a, **kw: "56941131946"
    )
    chat_id = waha_client._resolve_target("default", "148726328881285")
    assert chat_id == "56941131946@c.us"


def test_resolve_target_pn_passes_through():
    """Si el número es PN normal (< 14 dígitos), no se intenta resolver LID."""
    chat_id = waha_client._resolve_target("default", "56941131946")
    assert chat_id == "56941131946@c.us"


def test_send_text_lid_unresolved_propagates_minus_two(monkeypatch):
    """``waha_client.send_text`` propaga el ``WahaAPIError(-2)`` al caller."""
    monkeypatch.setenv("WAHA_API_URL", "http://fake")
    monkeypatch.setenv("WAHA_API_KEY", "fake")
    # Forzar refresh del cache de settings
    from src.config import get_settings
    get_settings.cache_clear()

    monkeypatch.setattr(waha_client, "resolve_lid_to_pn", lambda *a, **kw: None)
    with pytest.raises(WahaAPIError) as excinfo:
        waha_client.send_text("default", "+148726328881285", "hola")
    assert excinfo.value.status == -2

    get_settings.cache_clear()


def test_resolve_lid_to_pn_uses_cache(monkeypatch):
    """Layer 1: cache hit no toca HTTP."""
    waha_client._LID_TO_PN_CACHE["999@lid"] = "56941131946"

    def _fail(*a, **kw):
        raise AssertionError("No debe llamar a _request si hay cache")

    monkeypatch.setattr(waha_client, "_request", _fail)
    assert waha_client.resolve_lid_to_pn("default", "999@lid") == "56941131946"


def test_resolve_lid_to_pn_layer1_endpoint(monkeypatch):
    """Layer 2: endpoint /contacts/lid-pn devuelve PN."""
    calls: list[tuple] = []

    def _fake_request(method, path, **kw):
        calls.append((method, path, kw.get("params")))
        if path.endswith("/contacts/lid-pn"):
            return {"PN": "56941131946@c.us"}
        raise WahaAPIError(404, "not found")

    monkeypatch.setattr(waha_client, "_request", _fake_request)
    pn = waha_client.resolve_lid_to_pn("default", "148726328881285@lid")
    assert pn == "56941131946"


def test_resolve_lid_to_pn_returns_none_when_all_layers_fail(monkeypatch):
    """Las 3 capas fallan → None (caller debe abortar el envío)."""
    def _fake_request(method, path, **kw):
        raise WahaAPIError(404, "not found")

    monkeypatch.setattr(waha_client, "_request", _fake_request)
    pn = waha_client.resolve_lid_to_pn("default", "148726328881285@lid")
    assert pn is None
