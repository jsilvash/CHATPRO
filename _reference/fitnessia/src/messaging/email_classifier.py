"""Clasificador de emails inbound — decide (case_type, module) por contenido.

Estrategia en capas:

1. **Override por destinatario**: si el email llegó a un alias específico
   (`bajas@gym.com`, `congelar@gym.com`), el módulo se pre-setea desde la
   configuración del tenant (`tenant.settings_json.email.aliases`). Esto
   funciona como "atajo emocional" para clientes que ya saben dónde
   escribir.

2. **Keywords de alta confianza**: regex literales sobre subject + body.
   Resuelve ~80% de los casos en español rioplatense / chileno.

3. **IA fallback** (opcional, Paso E): si keywords no matchean, llama a
   Claude para clasificar con confianza. Solo se invoca si el tenant
   tiene `settings_json.crm_omnicanal.ai_classifier = true`.

4. **Default**: `(support, soporte_general)` — un humano lo triagea.

Devuelve siempre `(case_type: str, module: str, confidence: float, source: str)`.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Keywords por módulo (case_type=support por default) ───────────────────
# El orden importa: el primer match gana. Los más específicos primero.

_KEYWORDS = [
    # (module, regex, case_type)
    ("finiquito", re.compile(
        r"\b(finiquit[oó]?|dar\s*de\s*baja|cancelar\s*(mi\s*)?(membres[ií]a|plan|cuenta|suscripci[oó]n)|"
        r"rescindir|terminaci[oó]n\s*de\s*contrato|baja\s*definitiva|"
        r"darme\s*de\s*baja|quiero\s*(la\s*)?baja)\b",
        re.IGNORECASE,
    ), "support"),
    ("congelamiento", re.compile(
        r"\b(congelar|congelamiento|pausar|suspender\s*(temporalmente|por)|"
        r"vacaciones|me\s*voy\s*de\s*viaje|pausa\s*(temporal|de)|"
        r"freezar|hold|hibern)\b",
        re.IGNORECASE,
    ), "support"),
    ("cobranza", re.compile(
        r"\b(cobr[oa]|pago\s*(rechazado|fallido|pendiente)|deuda|"
        r"factura|comprobante|saldo|d[ée]bito\s*autom[aá]tico|"
        r"no\s*me\s*cobr|cobraron\s*de\s*m[aá]s)\b",
        re.IGNORECASE,
    ), "support"),
    ("reclamo", re.compile(
        r"\b(reclamo|queja|me\s*qued[eé]|cobraron\s*mal|"
        r"problema\s*con|no\s*funciona|p[eé]simo|terrible|"
        r"defensor\s*del\s*consumidor|sernac|consumidor|"
        r"insatisfech)\b",
        re.IGNORECASE,
    ), "support"),
    ("renovacion", re.compile(
        r"\b(renovar|renovaci[oó]n|renew|vence\s*(mi|el)|"
        r"vencimiento|extender\s*(mi\s*)?(plan|membres[ií]a))\b",
        re.IGNORECASE,
    ), "lifecycle"),  # ← caso lifecycle, no support
    ("consulta", re.compile(
        r"\b(consulta|pregunt|me\s*pueden\s*decir|c[oó]mo\s*(hago|funciona|puedo)|"
        r"horarios?|sucursales?|tarifa|precio|cu[aá]nto\s*(sale|cuesta|vale))\b",
        re.IGNORECASE,
    ), "support"),
]


def classify_by_keywords(subject: str, body: str) -> Optional[tuple[str, str, float]]:
    """Clasifica por keywords. Devuelve (case_type, module, confidence) o None."""
    text = f"{subject or ''}\n{body or ''}"
    if not text.strip():
        return None
    for module, pattern, case_type in _KEYWORDS:
        if pattern.search(text):
            return (case_type, module, 0.85)
    return None


def classify_by_alias(to_address: str, alias_map: dict | None) -> Optional[tuple[str, str, float]]:
    """Override por dirección destino.

    `alias_map` viene de `tenant.settings_json.email.aliases`, formato:
        {
            "bajas@gym.com":     {"case_type": "support", "module": "finiquito"},
            "congelar@gym.com":  {"case_type": "support", "module": "congelamiento"},
            "soporte@gym.com":   {"case_type": "support", "module": "soporte_general"},
        }
    """
    if not to_address or not alias_map:
        return None
    addr = to_address.strip().lower()
    # Match exacto primero
    cfg = alias_map.get(addr)
    if not cfg:
        # Fallback: match por local-part (todo antes del @) si el dominio
        # del tenant no está en el map pero el local sí (`bajas` sin importar
        # el dominio).
        local = addr.split("@", 1)[0] if "@" in addr else addr
        cfg = alias_map.get(local)
    if not cfg:
        return None
    return (cfg.get("case_type", "support"), cfg.get("module", "soporte_general"), 0.95)


def classify_with_ai(subject: str, body: str, tenant_settings: dict | None = None) -> Optional[tuple[str, str, float]]:
    """Clasifica con Claude. Solo se invoca si el flag está ON.

    Implementación stub aquí — el Paso E completa la integración real con
    `anthropic` SDK. Por ahora retorna None (= no IA).
    """
    if not tenant_settings:
        return None
    flag = (
        tenant_settings.get("crm_omnicanal", {}).get("ai_classifier")
        or tenant_settings.get("ai_classifier")
    )
    if not flag:
        return None
    # Importar acá para no costear el import si la flag está OFF.
    try:
        from src.engine.ai_email_classifier import classify_email_with_claude
        return classify_email_with_claude(subject, body, tenant_settings)
    except ImportError:
        logger.warning("ai_classifier flag ON pero módulo AI no disponible")
        return None
    except Exception as e:
        logger.warning("AI classifier falló, usando fallback: %s", e)
        return None


def classify_email(
    subject: str,
    body: str,
    *,
    to_address: str = "",
    tenant_settings: dict | None = None,
) -> tuple[str, str, float, str]:
    """Pipeline completo. Devuelve (case_type, module, confidence, source).

    `source` indica qué capa decidió: 'alias' | 'keyword' | 'ai' | 'default'.
    Útil para debugging y para decidir si pedirle al agente que confirme.
    """
    # 1. Override por alias del destinatario
    aliases = (tenant_settings or {}).get("email", {}).get("aliases") if tenant_settings else None
    res = classify_by_alias(to_address, aliases)
    if res:
        return (*res, "alias")

    # 2. Keywords
    res = classify_by_keywords(subject, body)
    if res:
        return (*res, "keyword")

    # 3. IA
    res = classify_with_ai(subject, body, tenant_settings)
    if res:
        return (*res, "ai")

    # 4. Default
    return ("support", "soporte_general", 0.30, "default")


__all__ = [
    "classify_email",
    "classify_by_keywords",
    "classify_by_alias",
    "classify_with_ai",
]
