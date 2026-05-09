"""Integración con Google Calendar API via Service Account.

Flujo:
1. Crear Service Account en Google Cloud Console
2. Descargar JSON de credenciales
3. Compartir el Google Calendar del vendedor con el email de la Service Account
4. Pegar el JSON (o su path) en la configuración de la plataforma
5. Las demos se crean/cancelan automáticamente en el calendario compartido

No requiere OAuth del usuario — la Service Account actúa en nombre propio
sobre calendarios a los que fue invitada con permisos de edición.
"""

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Cache del servicio para no reconstruirlo en cada request
_service_cache = None
_calendar_id_cache = None


def _get_service(credentials_json: str):
    """Construye el servicio de Google Calendar a partir del JSON de Service Account."""
    global _service_cache

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        creds_dict = json.loads(credentials_json)
    except (json.JSONDecodeError, TypeError):
        logger.error("Google Calendar: credenciales JSON inválidas")
        return None

    try:
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        _service_cache = service
        return service
    except Exception as e:
        logger.error("Google Calendar: error construyendo servicio: %s", e)
        return None


def _get_config():
    """Lee las credenciales de Google Calendar desde la DB (PlatformSettings)."""
    try:
        from src.db.session import SessionLocal
        from src.db.models import PlatformSettings

        db = SessionLocal()
        try:
            creds_row = db.query(PlatformSettings).filter(
                PlatformSettings.key == "google_calendar_credentials"
            ).first()
            cal_id_row = db.query(PlatformSettings).filter(
                PlatformSettings.key == "google_calendar_id"
            ).first()

            creds = creds_row.value if creds_row else ""
            cal_id = cal_id_row.value if cal_id_row else "primary"
            return creds, cal_id
        finally:
            db.close()
    except Exception as e:
        logger.error("Google Calendar: error leyendo config: %s", e)
        return "", "primary"


def is_configured() -> bool:
    """Retorna True si Google Calendar está configurado."""
    creds, _ = _get_config()
    return bool(creds and len(creds) > 10)


def create_event(
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    description: str = "",
    location: str = "",
    attendee_email: str = "",
    attendee_name: str = "",
    event_id: Optional[str] = None,
) -> Optional[str]:
    """Crea un evento en Google Calendar. Retorna el event ID o None si falla."""
    creds, calendar_id = _get_config()
    if not creds:
        logger.debug("Google Calendar no configurado, saltando create_event")
        return None

    service = _get_service(creds)
    if not service:
        return None

    event_body = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": dtstart.isoformat(),
            "timeZone": "America/Santiago",
        },
        "end": {
            "dateTime": dtend.isoformat(),
            "timeZone": "America/Santiago",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
                {"method": "popup", "minutes": 1440},  # 24h
                {"method": "email", "minutes": 1440},
            ],
        },
    }

    if location:
        event_body["location"] = location

    if attendee_email:
        event_body["attendees"] = [
            {"email": attendee_email, "displayName": attendee_name or attendee_email},
        ]
        event_body["guestsCanModify"] = False

    if event_id:
        event_body["id"] = event_id

    try:
        event = service.events().insert(
            calendarId=calendar_id or "primary",
            body=event_body,
            sendUpdates="all",  # Envía invitación al attendee
        ).execute()

        gcal_event_id = event.get("id", "")
        gcal_link = event.get("htmlLink", "")
        logger.info(
            "Google Calendar: evento creado id=%s link=%s",
            gcal_event_id, gcal_link,
        )
        return gcal_event_id

    except Exception as e:
        logger.error("Google Calendar: error creando evento: %s", e)
        return None


def cancel_event(gcal_event_id: str) -> bool:
    """Cancela (elimina) un evento de Google Calendar."""
    if not gcal_event_id:
        return False

    creds, calendar_id = _get_config()
    if not creds:
        return False

    service = _get_service(creds)
    if not service:
        return False

    try:
        service.events().delete(
            calendarId=calendar_id or "primary",
            eventId=gcal_event_id,
            sendUpdates="all",  # Notifica al attendee
        ).execute()
        logger.info("Google Calendar: evento %s cancelado", gcal_event_id)
        return True
    except Exception as e:
        logger.error("Google Calendar: error cancelando evento %s: %s", gcal_event_id, e)
        return False


def update_event(
    gcal_event_id: str,
    summary: Optional[str] = None,
    dtstart: Optional[datetime] = None,
    dtend: Optional[datetime] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """Actualiza un evento existente en Google Calendar."""
    if not gcal_event_id:
        return False

    creds, calendar_id = _get_config()
    if not creds:
        return False

    service = _get_service(creds)
    if not service:
        return False

    try:
        # Obtener evento actual
        event = service.events().get(
            calendarId=calendar_id or "primary",
            eventId=gcal_event_id,
        ).execute()

        if summary:
            event["summary"] = summary
        if description:
            event["description"] = description
        if dtstart:
            event["start"] = {"dateTime": dtstart.isoformat(), "timeZone": "America/Santiago"}
        if dtend:
            event["end"] = {"dateTime": dtend.isoformat(), "timeZone": "America/Santiago"}
        if status == "cancelled":
            event["status"] = "cancelled"

        service.events().update(
            calendarId=calendar_id or "primary",
            eventId=gcal_event_id,
            body=event,
            sendUpdates="all",
        ).execute()

        logger.info("Google Calendar: evento %s actualizado", gcal_event_id)
        return True

    except Exception as e:
        logger.error("Google Calendar: error actualizando evento %s: %s", gcal_event_id, e)
        return False
