"""Cliente Anthropic con prompt caching.

El system prompt se marca con ``cache_control: ephemeral`` (TTL 5 min).
Los mensajes de turno NO se cachean — cambian en cada llamada.
"""

from __future__ import annotations

import logging

import anthropic

from src.config import get_settings

logger = logging.getLogger(__name__)

# Precios en USD por millón de tokens (aproximados, actualizar según Anthropic pricing).
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.0,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "cache_write": 1.00,
        "cache_read": 0.08,
        "output": 4.0,
    },
    "claude-opus-4-7": {
        "input": 15.0,
        "cache_write": 18.75,
        "cache_read": 1.50,
        "output": 75.0,
    },
}
_DEFAULT_PRICING = _MODEL_PRICING["claude-sonnet-4-6"]


def _compute_cost(model_id: str, usage: anthropic.types.Usage) -> float:
    pricing = _MODEL_PRICING.get(model_id, _DEFAULT_PRICING)
    per_m = 1_000_000.0

    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    regular_input = max(0, usage.input_tokens - cache_read - cache_write)

    cost = (
        regular_input * pricing["input"] / per_m
        + cache_read * pricing["cache_read"] / per_m
        + cache_write * pricing["cache_write"] / per_m
        + usage.output_tokens * pricing["output"] / per_m
    )
    return round(cost, 8)


def call_claude(
    system_prompt: str,
    messages: list[dict],
    model_id: str = "claude-sonnet-4-6",
    max_tokens: int = 1024,
) -> tuple[str, dict]:
    """Llama a la API Anthropic y devuelve (texto_respuesta, llm_metadata).

    llm_metadata shape:
        {"model", "input_tokens", "output_tokens",
         "cache_read_input_tokens", "cost_usd"}
    """
    response, metadata = call_claude_messages(
        system_prompt, messages, model_id=model_id, max_tokens=max_tokens
    )
    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    return text, metadata


def call_claude_messages(
    system_prompt: str,
    messages: list[dict],
    model_id: str = "claude-sonnet-4-6",
    max_tokens: int = 1024,
    tools: list[dict] | None = None,
) -> tuple[anthropic.types.Message, dict]:
    """Variante que retorna el ``Message`` crudo + metadata.

    Necesario para el loop de tool_use: el caller necesita ``stop_reason`` y
    ``content`` (incluyendo bloques ``tool_use``). El cómputo de costo es el
    mismo que ``call_claude``.
    """
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    kwargs: dict = {
        "model": model_id,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    response = client.messages.create(**kwargs)

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = _compute_cost(model_id, usage)

    metadata = {
        "model": model_id,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": cache_read,
        "cost_usd": cost,
    }

    logger.debug(
        "llm call: model=%s in=%d out=%d cache_read=%d cost=%.6f stop=%s",
        model_id,
        usage.input_tokens,
        usage.output_tokens,
        cache_read,
        cost,
        getattr(response, "stop_reason", "?"),
    )

    return response, metadata
