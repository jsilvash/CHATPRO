"""Construcción del system prompt desde Persona + Tenant.

También expone ``is_within_business_hours`` para decidir si el bot responde
o envía el mensaje de fuera de horario.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.agent.models import Persona

if TYPE_CHECKING:
    from src.contacts.models import ContactFact

_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_IDX = {name: i for i, name in enumerate(_DAY_NAMES)}


def _parse_day_key(key: str) -> set[int]:
    """Devuelve el conjunto de weekday() indices para un key como "mon-fri" o "sat"."""
    key = key.strip().lower()
    if "-" in key:
        parts = key.split("-", 1)
        start = _DAY_IDX.get(parts[0])
        end = _DAY_IDX.get(parts[1])
        if start is None or end is None:
            return set()
        return set(range(start, end + 1))
    idx = _DAY_IDX.get(key)
    if idx is None:
        return set()
    return {idx}


def _parse_time(t: str) -> time | None:
    """Parsea "HH:MM" a ``datetime.time``. Devuelve None si inválido."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (t or "").strip())
    if not m:
        return None
    try:
        return time(int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def is_within_business_hours(persona: Persona) -> bool:
    """Devuelve True si el momento actual cae dentro del horario de la persona.

    Si ``business_hours_json`` está vacío o malformado → siempre disponible.
    """
    bh = persona.business_hours_json
    if not bh or not isinstance(bh, dict):
        return True

    days_cfg = bh.get("days")
    if not days_cfg or not isinstance(days_cfg, dict):
        return True

    tz_name = bh.get("tz") or persona.timezone or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    current_weekday = now.weekday()  # 0=lunes, 6=domingo
    current_time = now.time().replace(second=0, microsecond=0)

    for day_key, hours in days_cfg.items():
        if not isinstance(hours, list) or len(hours) < 2:
            continue
        indices = _parse_day_key(day_key)
        if current_weekday not in indices:
            continue
        start = _parse_time(hours[0])
        end = _parse_time(hours[1])
        if start is None or end is None:
            continue
        if start <= current_time < end:
            return True

    return False


def render_contact_memory(facts: "list[ContactFact]") -> str:
    """Renderiza un bloque <memoria_contacto> con los hechos del contacto.

    Devuelve string vacío si no hay hechos.  El bloque se añade al final del
    system prompt para que no altere el caché de la parte de persona.
    """
    if not facts:
        return ""

    lines = ["<memoria_contacto>", "Hechos conocidos del cliente:"]
    for fact in facts:
        source_tag = "manual" if fact.source == "manual" else f"extraído, conf {fact.confidence}"
        lines.append(f"- {fact.key}: {fact.value_text} ({source_tag})")
    lines.append("</memoria_contacto>")
    return "\n".join(lines)


def build_system_prompt(
    persona: Persona,
    contact_facts: "list[ContactFact] | None" = None,
) -> str:
    """Construye el system prompt completo desde la persona y hechos del contacto.

    La parte de persona es estable → puede cachearse con cache_control ephemeral.
    Los hechos del contacto se añaden al final (varían por conversación).
    """
    lines: list[str] = []

    if persona.system_prompt:
        lines.append(persona.system_prompt.strip())
        lines.append("")

    tone_map = {
        "formal": "Mantén un tono formal y profesional en todas tus respuestas.",
        "amigable": "Mantén un tono amigable, cercano y empático en todas tus respuestas.",
        "neutral": "Mantén un tono neutro y objetivo en todas tus respuestas.",
    }
    tone_hint = tone_map.get(persona.tone or "amigable", tone_map["amigable"])
    lines.append(tone_hint)

    if persona.locale:
        lines.append(
            f"Usa el idioma y convenciones del locale {persona.locale}. "
            "Adapta el formato de fechas, moneda y expresiones a ese locale."
        )

    if persona.timezone:
        lines.append(f"Tu zona horaria de referencia es {persona.timezone}.")

    lines.append(
        "\nResponde SIEMPRE en texto plano, sin markdown, sin asteriscos ni emojis, "
        "a menos que el usuario lo solicite explícitamente."
    )

    if contact_facts:
        memory_block = render_contact_memory(contact_facts)
        if memory_block:
            lines.append("")
            lines.append(memory_block)

    return "\n".join(lines).strip()
