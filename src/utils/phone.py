"""Normalización de números de teléfono."""

import re

_NON_DIGIT_RE = re.compile(r"[^\d]")


def normalize_phone(raw: str) -> str:
    """Devuelve solo los dígitos del número, o ``""`` si no quedan suficientes.

    Acepta entradas con ``+``, espacios, guiones, paréntesis, ``@c.us``, etc.
    Si tras limpiar quedan menos de 7 dígitos lo considera inválido y devuelve ``""``.
    """
    if not raw:
        return ""
    only_digits = _NON_DIGIT_RE.sub("", raw)
    if len(only_digits) < 7:
        return ""
    return only_digits
