"""Cliente Anthropic y cómputo de costo por llamada LLM."""

from __future__ import annotations

import anthropic

# Precios en USD/M tokens → se convierten a centavos al calcular
_COST_PER_M: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    # fallback genérico
    "default": {"input": 3.0, "output": 15.0},
}

DEFAULT_MODEL = "claude-sonnet-4-6"


def get_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def compute_cost_cents(usage: anthropic.types.Usage, model: str = DEFAULT_MODEL) -> float:
    """Retorna el costo en centavos de dólar para el uso reportado."""
    costs = _COST_PER_M.get(model, _COST_PER_M["default"])
    return (
        usage.input_tokens / 1_000_000 * costs["input"]
        + usage.output_tokens / 1_000_000 * costs["output"]
    ) * 100
