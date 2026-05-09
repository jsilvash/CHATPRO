"""Messaging a nivel plataforma — credenciales propias del equipo de ventas SaaS.

Reutiliza las funciones existentes de email.py y whatsapp.py pasando
un config dict sintético con las credenciales de plataforma (no de tenant).
"""

import logging

from src.config import get_settings
from src.messaging.email import send_email
from src.messaging.whatsapp import send_whatsapp_template, send_whatsapp_text

logger = logging.getLogger(__name__)


def _get_setting(key: str, env_val: str) -> str:
    """Lee un setting con override de DB si existe."""
    try:
        from src.db.session import SessionLocal
        from src.db.models import PlatformSettings
        db = SessionLocal()
        try:
            row = db.query(PlatformSettings).filter(PlatformSettings.key == key).first()
            if row and row.value:
                return row.value
        finally:
            db.close()
    except Exception:
        pass
    return env_val


def _get_platform_config() -> dict:
    """Construye un config dict compatible con send_email/send_whatsapp."""
    s = get_settings()
    sgk = _get_setting("platform_sendgrid_api_key", s.platform_sendgrid_api_key)
    wt = _get_setting("platform_whatsapp_token", s.platform_whatsapp_token)
    wpid = _get_setting("platform_whatsapp_phone_number_id", s.platform_whatsapp_phone_number_id)
    return {
        "email": {
            "enabled": bool(sgk),
            "sendgrid_api_key": sgk,
            "from_email": _get_setting("platform_email_from", s.platform_email_from),
            "from_name": _get_setting("platform_email_from_name", s.platform_email_from_name),
            "reply_to": _get_setting("platform_email_reply_to", s.platform_email_reply_to),
        },
        "whatsapp": {
            "enabled": bool(wt and wpid),
            "token": wt,
            "phone_number_id": wpid,
        },
        "branding": {
            "company_name": "Fitness BI",
            "footer_text": "Plataforma de Business Intelligence para gimnasios",
            "primary_color": "#2563eb",
            "secondary_color": "#1e40af",
        },
    }


def send_platform_email(
    to_email: str, to_name: str, subject: str, html_body: str,
    attachments: list[dict] | None = None,
) -> str | None:
    """Envía email con credenciales de la plataforma (no de un tenant)."""
    config = _get_platform_config()
    return send_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_body=html_body,
        tenant_config=config,
        skip_opt_out_check=True,
        attachments=attachments,
    )


def send_platform_whatsapp_template(
    phone: str, template_name: str, params: dict, language: str = "es"
) -> str | None:
    """Envía WhatsApp template con credenciales de la plataforma."""
    config = _get_platform_config()
    return send_whatsapp_template(phone, template_name, params, language, config)


def send_platform_whatsapp_text(phone: str, text: str) -> str | None:
    """Envía WhatsApp texto libre con credenciales de la plataforma."""
    config = _get_platform_config()
    return send_whatsapp_text(phone, text, config)


def notify_sales_team(subject: str, html_body: str) -> str | None:
    """Envía notificación al equipo de ventas de la plataforma."""
    s = get_settings()
    notify_email = _get_setting("platform_sales_notification_email", s.platform_sales_notification_email)
    if not notify_email:
        logger.warning("platform_sales_notification_email no configurado, saltando notificación")
        return None
    return send_platform_email(
        to_email=s.platform_sales_notification_email,
        to_name="Equipo Ventas Fitness BI",
        subject=subject,
        html_body=html_body,
    )
