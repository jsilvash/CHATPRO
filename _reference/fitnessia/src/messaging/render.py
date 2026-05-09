"""Helpers de renderizado de plantillas de mensajes.

`format_text` reemplaza placeholders del estilo `{variable}` con los valores
de un diccionario. Es la misma implementación que vivía duplicada en los 4
engines de retención (`onboarding.py`, `renewal.py`, `inactivity.py`,
`exclient.py`) — al consolidarla acá evitamos que se siga forkeando y la
podemos usar también desde el endpoint `POST /admin/test-email`.

Diseño deliberadamente simple:
- No usa `str.format_map` porque las plantillas vienen de usuarios y pueden
  contener llaves sueltas (ej. bloques `{ font-family: ... }` en un email
  HTML). `str.format` crashearía; `str.replace` aguanta.
- Ignora keys que no aparezcan en `params` — así una plantilla que menciona
  `{plan}` cuando el flow no lo pasa no falla, simplemente deja el
  placeholder literal (lo cual es señal obvia de que falta un valor).
- No escapa HTML. Los engines pasan HTML crudo y es responsabilidad del
  caller evitar XSS (en práctica los `params` vienen de campos del modelo
  Client, no de input de terceros, así que no es un vector real).
"""

from __future__ import annotations


def format_text(template: str, params: dict) -> str:
    """Reemplaza `{variable}` en `template` con los valores de `params`.

    >>> format_text("Hola {nombre}!", {"nombre": "Ana"})
    'Hola Ana!'
    >>> format_text("Precio: $100 {nombre}", {"nombre": "Ana"})
    'Precio: $100 Ana'
    >>> format_text("Sin variable", {"nombre": "Ana"})
    'Sin variable'
    >>> format_text('<a href="%7Blink%7D">go</a>', {"link": "https://ex.com"})
    '<a href="https://ex.com">go</a>'
    """
    if not template:
        return ""
    result = template
    for key, val in (params or {}).items():
        sval = str(val)
        result = result.replace("{" + key + "}", sval)
        # Unlayer (y otros editores visuales) URL-encodean las llaves cuando
        # un merge tag cae dentro de un atributo `href`. Reemplazamos ambas
        # variantes (mayús/minús) para que `<a href="%7Blink%7D">` funcione
        # igual que `<a href="{link}">`.
        result = result.replace("%7B" + key + "%7D", sval)
        result = result.replace("%7b" + key + "%7d", sval)
    return result
