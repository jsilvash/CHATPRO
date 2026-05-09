"""Integracion con SendGrid para email transaccional.

## Wrap HTML — dos modos

Históricamente `send_email` siempre envolvía el `html_body` con un template
básico (`_wrap_html`) que inyectaba estilos inline y un footer hardcoded
"Nutre y Entrena — Tu gimnasio, tu espacio.". Eso funcionaba mientras los
correos eran fragmentos HTML simples escritos a mano.

Con el editor visual de emails (Unlayer en el backoffice) ahora el usuario
puede producir **documentos HTML completos** con su propio `<!DOCTYPE>`,
`<html>`, `<head>`, estilos, fuentes y footer. Wrappear ese HTML generaría
un documento inválido (html dentro de html) y además pisaría el diseño del
usuario con nuestro footer hardcoded.

Por eso `send_email` ahora detecta si el `html_body` ya es un documento
completo (arranca con `<!DOCTYPE` o `<html`) y en ese caso **saltea el
wrap**. Los fragmentos legacy (que empiezan con `<h2>...`, `<p>...`, etc.)
siguen pasando por `_wrap_html` como siempre.

## Branding configurable

El footer y el color base del wrap ya no están hardcoded: si el caller
pasa `tenant_config` con una sección `branding`, `_wrap_html` lee de ahí
los valores (con fallback a los strings por defecto). Esto permite que la
sección Admin → Branding del backoffice cambie el footer/color/nombre sin
tocar código.
"""

import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


_DEFAULT_BRANDING = {
    "company_name": "Nutre y Entrena",
    "footer_text": "Tu gimnasio, tu espacio.",
    "unsubscribe_text": "Si no deseas recibir estos correos, puedes desuscribirte aqui.",
    "primary_color": "#2c3e50",
    # Color secundario para acentos (links, separadores, borde del footer).
    # Si no se configura, cae al primary_color para no romper el look.
    "secondary_color": "#e74c3c",
}


def _get_email_credentials(tenant_config: dict | None = None) -> tuple[str, str, str, str]:
    """Obtiene credenciales email: primero del tenant, luego del .env.

    Returns:
        (api_key, from_email, from_name, reply_to)
        reply_to puede ser vacío → no se agrega header Reply-To al correo.
    """
    settings = get_settings()
    if tenant_config:
        em = tenant_config.get("email", {})
        if em.get("enabled") and em.get("sendgrid_api_key"):
            return (
                em["sendgrid_api_key"],
                em.get("from_email", settings.email_from),
                em.get("from_name", settings.email_from_name),
                em.get("reply_to", settings.email_reply_to),
            )
    return settings.sendgrid_api_key, settings.email_from, settings.email_from_name, settings.email_reply_to


def _is_full_html_document(html_body: str) -> bool:
    """Devuelve True si el body ya es un documento HTML completo.

    Detectamos con prefijo (case-insensitive, ignorando whitespace inicial).
    Los exports de Unlayer siempre empiezan con `<!DOCTYPE html>`. Algunos
    editores legacy pueden mandar `<html>` directo — ambos casos cuentan
    como "completo" y deben saltar `_wrap_html`.
    """
    if not html_body:
        return False
    stripped = html_body.lstrip()
    head = stripped[:15].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    tenant_config: dict | None = None,
    *,
    attachments: list[dict] | None = None,
    client_id: int | None = None,
    tenant_id: int | None = None,
    skip_opt_out_check: bool = False,
    case_id: int | None = None,
) -> str | None:
    """Envia un email via SendGrid.

    Si `tenant_config` contiene una sección `branding`, esos valores se
    usan para personalizar el wrap HTML (cuando aplica). Si el `html_body`
    ya es un documento HTML completo (exportado por el editor visual),
    se envía tal cual sin wrappear.

    Args:
        client_id: ID del cliente (para generar link de desuscripción y
            verificar opt-out). Si es None, no se genera link ni se
            verifica opt-out (ej: correos de prueba admin).
        tenant_id: ID del tenant (requerido junto con client_id).
        skip_opt_out_check: Si True, envía aunque el cliente esté opt-out
            (ej: correos transaccionales obligatorios, no marketing).

    Returns:
        message_id (puede ser string vacío si SendGrid no lo devolvió) o
        None si el envío falló.
    """
    api_key, from_email, from_name, reply_to = _get_email_credentials(tenant_config)
    if not api_key:
        logger.warning("SendGrid not configured, skipping send to %s", to_email)
        return None

    if not to_email or "@" not in to_email:
        logger.warning("Invalid email: %s", to_email)
        return None

    # ── Verificar opt-out ──────────────────────────────────────────────
    # Si el socio se desuscribió, no enviar (a menos que sea obligatorio).
    if client_id and tenant_id and not skip_opt_out_check:
        try:
            from src.db.session import SessionLocal
            from src.db.models import Client
            with SessionLocal() as db:
                client = (
                    db.query(Client.email_opted_out)
                    .filter(Client.id == client_id, Client.tenant_id == tenant_id)
                    .first()
                )
                if client and client.email_opted_out:
                    logger.info(
                        "Skipping email to %s (client_id=%s): opted out",
                        to_email, client_id,
                    )
                    return None
        except Exception as e:
            # Si falla el check, enviar de todas formas — mejor enviar
            # un email de más que silenciar un error de DB.
            logger.warning("Opt-out check failed for client %s: %s", client_id, e)

    # ── Link de desuscripción ──────────────────────────────────────────
    unsubscribe_url = ""
    if client_id and tenant_id:
        try:
            from src.api.routes.unsubscribe import generate_unsubscribe_url
            base_url = (tenant_config or {}).get("base_url", "")
            unsubscribe_url = generate_unsubscribe_url(client_id, tenant_id, base_url)
        except Exception as e:
            logger.warning("Failed to generate unsubscribe URL: %s", e)

    # Decidir si wrappear. Si el usuario diseñó el correo en el editor
    # visual, el export ya trae `<!DOCTYPE html>` y metemos nuestro wrap
    # encima sólo rompería el diseño.
    if _is_full_html_document(html_body):
        content_html = html_body
    else:
        branding = (tenant_config or {}).get("branding") or {}
        content_html = _wrap_html(html_body, branding, unsubscribe_url=unsubscribe_url)

    payload = {
        "personalizations": [
            {
                "to": [{"email": to_email, "name": to_name}],
                "subject": subject,
            }
        ],
        "from": {
            "email": from_email,
            "name": from_name,
        },
        "content": [
            {
                "type": "text/html",
                "value": content_html,
            }
        ],
    }

    # Reply-To: si hay case_id y el tenant tiene dominio de reply configurado,
    # generamos una dirección con token firmado para capturar la respuesta en
    # el CRM omnicanal (`/webhook/email`). Si no, cae al reply_to estático.
    dynamic_reply = None
    if case_id and tenant_id:
        try:
            from src.messaging.email_inbound import build_reply_address
            dynamic_reply = build_reply_address(case_id, tenant_id)
        except Exception as e:
            logger.warning("build_reply_address failed for case=%s: %s", case_id, e)
    if dynamic_reply:
        payload["reply_to"] = {"email": dynamic_reply}
    elif reply_to:
        payload["reply_to"] = {"email": reply_to}

    if attachments:
        payload["attachments"] = attachments

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(SENDGRID_API_URL, json=payload, headers=headers, timeout=15.0)
        resp.raise_for_status()
        message_id = resp.headers.get("X-Message-Id", "")
        logger.info("Email sent to %s: subject='%s', id=%s", to_email, subject, message_id)
        return message_id
    except httpx.HTTPStatusError as e:
        logger.error("SendGrid API error: %s - %s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.error("Email send failed: %s", e)
        return None


def _wrap_html(body: str, branding: dict | None = None, *, unsubscribe_url: str = "") -> str:
    """Envuelve un fragmento HTML con un template básico + branding editable.

    `branding` se mergea sobre `_DEFAULT_BRANDING`. Campos soportados:
      - company_name:    nombre que aparece en el footer
      - footer_text:     tagline debajo del nombre
      - unsubscribe_text:línea de opt-out
      - primary_color:   color de headings (h2)
      - secondary_color: color de acentos (links, borde del footer).
                         Si viene vacío, cae al primary_color.

    `unsubscribe_url`: si no está vacío, el texto de opt-out se convierte
    en un link clickeable. Si está vacío, queda como texto plano (fallback).

    Si `branding` es None o vacío, usa los defaults (comportamiento
    histórico idéntico al de antes del refactor).
    """
    b = {**_DEFAULT_BRANDING, **(branding or {})}
    # Escapado defensivo mínimo: evitamos que un string con comilla o `<`
    # rompa el atributo style. No es XSS porque el branding viene de
    # settings del propio tenant autenticado, pero un typo no debería
    # reventar el email.
    def _esc(s: str) -> str:
        return (str(s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    company = _esc(b["company_name"])
    footer  = _esc(b["footer_text"])
    color   = _esc(b["primary_color"])
    # Fallback: si el tenant borró el secundario o lo dejó vacío, reusamos
    # el primario en vez de romper el CSS con un string vacío.
    accent  = _esc(b.get("secondary_color") or b["primary_color"])

    unsub_text = _esc(b["unsubscribe_text"])
    if unsubscribe_url:
        unsub = f'<a href="{unsubscribe_url}" style="color: #bbb; text-decoration: underline;">{unsub_text}</a>'
    else:
        unsub = unsub_text

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Helvetica Neue', Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            h2 {{
                color: {color};
            }}
            a {{
                color: {accent};
            }}
            ul {{
                padding-left: 20px;
            }}
            li {{
                margin-bottom: 8px;
            }}
            .footer {{
                margin-top: 30px;
                padding-top: 15px;
                border-top: 2px solid {accent};
                font-size: 12px;
                color: #999;
            }}
        </style>
    </head>
    <body>
        {body}
        <div class="footer">
            <p>{company} — {footer}</p>
            <p style="font-size:10px; color:#bbb; margin-top:8px;">{unsub}</p>
        </div>
    </body>
    </html>
    """
