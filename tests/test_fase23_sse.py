"""Tests Fase 23D — Endpoint de métricas en tiempo real via SSE.

Cubre:
- GET /v1/metrics/stream sin token → 401.
- GET /v1/metrics/stream con Bearer válido → 200 text/event-stream.
- GET /v1/metrics/stream con ?token= válido → 200 text/event-stream.
- El primer evento contiene los campos esperados: messages_in_today,
  messages_out_today, conversations_active, llm_cost_cents_today, timestamp.
- Aislamiento: el stream emite datos del tenant autenticado (no de otro tenant).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest

from src.auth.tokens import create_access_token
from src.billing.models import UsageMetric


# ── Helpers ──────────────────────────────────────────────────────────────────


def _token(user) -> str:
    return create_access_token(user.id, user.tenant_id, user.role)


def _auth_header(user) -> dict:
    return {"Authorization": f"Bearer {_token(user)}"}


def _parse_first_sse_event(content: bytes) -> dict:
    """Extrae el JSON del primer evento SSE de la respuesta."""
    text = content.decode("utf-8")
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise ValueError(f"No se encontró evento SSE en: {text!r}")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_sse_sin_token_401(client):
    """Sin autenticación → 401."""
    resp = client.get("/v1/metrics/stream")
    assert resp.status_code == 401


def test_sse_con_bearer_header_200(client_a, tenant_a, db):
    """Con Authorization Bearer válido → 200 text/event-stream."""
    tenant, owner = tenant_a
    resp = client_a.get(
        "/v1/metrics/stream",
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_sse_con_query_token_200(client, tenant_a, db):
    """Con ?token=<jwt> en query param → 200 text/event-stream."""
    tenant, owner = tenant_a
    token = _token(owner)
    resp = client.get(f"/v1/metrics/stream?token={token}")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_sse_primer_evento_contiene_campos(client_a, tenant_a, db):
    """El primer evento SSE contiene los campos esperados."""
    tenant, owner = tenant_a
    resp = client_a.get(
        "/v1/metrics/stream",
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200

    event = _parse_first_sse_event(resp.content)

    assert "messages_in_today" in event
    assert "messages_out_today" in event
    assert "conversations_active" in event
    assert "llm_cost_cents_today" in event
    assert "timestamp" in event


def test_sse_campos_son_numericos(client_a, tenant_a, db):
    """Los campos numéricos del evento SSE son int o float."""
    tenant, owner = tenant_a
    resp = client_a.get(
        "/v1/metrics/stream",
        headers={"Accept": "text/event-stream"},
    )
    event = _parse_first_sse_event(resp.content)

    assert isinstance(event["messages_in_today"], int)
    assert isinstance(event["messages_out_today"], int)
    assert isinstance(event["conversations_active"], int)
    assert isinstance(event["llm_cost_cents_today"], (int, float))


def test_sse_refleja_usage_metrics_del_tenant(client_a, tenant_a, db):
    """Los valores del evento reflejan los usage_metrics del tenant autenticado."""
    tenant, owner = tenant_a
    today = date.today()

    # Insertar métricas conocidas para hoy
    metric = UsageMetric(
        tenant_id=tenant.id,
        metric_date=today,
        messages_in=42,
        messages_out=17,
        llm_cost_cents=99.5,
    )
    db.add(metric)
    db.flush()

    resp = client_a.get(
        "/v1/metrics/stream",
        headers={"Accept": "text/event-stream"},
    )
    event = _parse_first_sse_event(resp.content)

    assert event["messages_in_today"] == 42
    assert event["messages_out_today"] == 17
    assert event["llm_cost_cents_today"] == pytest.approx(99.5, abs=0.01)


def test_sse_token_invalido_401(client):
    """Token inválido en query param → 401."""
    resp = client.get("/v1/metrics/stream?token=token-invalido-xxx")
    assert resp.status_code == 401


def test_sse_aislamiento_tenant(client_a, client_b, tenant_a, tenant_b, db):
    """El stream de tenant A no expone datos de tenant B."""
    tenant_a_obj, owner_a = tenant_a
    tenant_b_obj, owner_b = tenant_b
    today = date.today()

    # Métricas de tenant B
    metric_b = UsageMetric(
        tenant_id=tenant_b_obj.id,
        metric_date=today,
        messages_in=999,
    )
    db.add(metric_b)
    db.flush()

    # Consultar stream de tenant A
    resp = client_a.get(
        "/v1/metrics/stream",
        headers={"Accept": "text/event-stream"},
    )
    event = _parse_first_sse_event(resp.content)

    # Tenant A no debe ver los 999 mensajes de tenant B
    assert event["messages_in_today"] != 999
