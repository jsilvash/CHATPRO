"""Helpers OAuth para Meta Graph API (Login with Facebook).

Fase B0a. HTTP puro — sin dependencias de DB. El router
`src/api/routes/meta_oauth.py` combina estos helpers con `settings_json`.

Flujo:
    code          → exchange_code_for_short()  → short user token (~1h)
    short token   → exchange_short_for_long()  → long user token (~60d)
    long token    → list_pages()               → FB Pages + IG Business Accounts
                                                  con Page Tokens sin expiración
                                                  (mientras viva el long user token)
    long token    → debug_token()              → metadata: expires_at, is_valid,
                                                  scopes

Referencias:
    https://developers.facebook.com/docs/facebook-login/guides/access-tokens
    https://developers.facebook.com/docs/pages/access-tokens
"""
from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"
OAUTH_DIALOG = "https://www.facebook.com/v19.0/dialog/oauth"

# Permisos que pedimos en el dialog OAuth. `instagram_manage_messages` requiere
# App Review para publishing outbound en cuentas que no son testers.
OAUTH_SCOPES = [
    "pages_messaging",
    "pages_show_list",
    "instagram_basic",
    "instagram_manage_messages",
    "business_management",
]


def build_login_dialog_url(state: str) -> Optional[str]:
    """URL que el usuario abre en el browser para autorizar la app.

    Meta muestra el diálogo de consentimiento y luego redirige a
    `meta_oauth_redirect_uri` con `?code=...&state=...`.

    Returns None si falta configuración — el caller debe responder 503.
    """
    s = get_settings()
    if not s.meta_app_id or not s.meta_oauth_redirect_uri:
        return None
    params = {
        "client_id": s.meta_app_id,
        "redirect_uri": s.meta_oauth_redirect_uri,
        "state": state,
        "scope": ",".join(OAUTH_SCOPES),
        "response_type": "code",
    }
    return f"{OAUTH_DIALOG}?{urlencode(params)}"


def exchange_code_for_short(code: str) -> Optional[str]:
    """Canjea el `code` del callback por un User Access Token corto (~1 h).

    `GET /oauth/access_token` — no requiere el App Secret en params cuando
    la app se autentica server-side via client_secret.
    """
    s = get_settings()
    if not s.meta_app_id or not s.meta_app_secret or not s.meta_oauth_redirect_uri:
        logger.error("exchange_code_for_short: configuración incompleta")
        return None
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": s.meta_app_id,
                "client_secret": s.meta_app_secret,
                "redirect_uri": s.meta_oauth_redirect_uri,
                "code": code,
            },
            timeout=15.0,
        )
        if r.status_code != 200:
            logger.error(
                "exchange_code_for_short FAILED status=%s body=%s",
                r.status_code, r.text[:300],
            )
            return None
        return (r.json() or {}).get("access_token")
    except Exception as exc:
        logger.exception("exchange_code_for_short EXCEPTION: %s", exc)
        return None


def exchange_short_for_long(short_token: str) -> Optional[dict]:
    """Canjea un User Token corto por uno largo (~60 días).

    Returns:
        {"access_token": str, "expires_at": int unix} si OK, None si falla.
    """
    s = get_settings()
    if not s.meta_app_id or not s.meta_app_secret:
        logger.error("exchange_short_for_long: app_id/app_secret faltan")
        return None
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": s.meta_app_id,
                "client_secret": s.meta_app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=15.0,
        )
        if r.status_code != 200:
            logger.error(
                "exchange_short_for_long FAILED status=%s body=%s",
                r.status_code, r.text[:300],
            )
            return None
        data = r.json() or {}
        access_token = data.get("access_token")
        if not access_token:
            return None
        expires_in = int(data.get("expires_in") or 0)
        return {
            "access_token": access_token,
            "expires_at": int(time.time()) + expires_in if expires_in else 0,
        }
    except Exception as exc:
        logger.exception("exchange_short_for_long EXCEPTION: %s", exc)
        return None


def debug_token(input_token: str) -> Optional[dict]:
    """Valida un token vía `/debug_token`.

    Returns la sección `data` de la respuesta (con campos `expires_at`,
    `is_valid`, `scopes`, `user_id`, `app_id`). None si la llamada falla.
    """
    s = get_settings()
    if not s.meta_app_id or not s.meta_app_secret:
        return None
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/debug_token",
            params={
                "input_token": input_token,
                "access_token": f"{s.meta_app_id}|{s.meta_app_secret}",
            },
            timeout=10.0,
        )
        if r.status_code != 200:
            logger.warning(
                "debug_token FAILED status=%s body=%s",
                r.status_code, r.text[:300],
            )
            return None
        return (r.json() or {}).get("data")
    except Exception as exc:
        logger.warning("debug_token EXCEPTION: %s", exc)
        return None


def list_pages(user_token_long: str) -> list[dict]:
    """`GET /me/accounts` — páginas administradas con sus Page Tokens.

    El Page Token que viene aquí, obtenido desde un User Token largo, es
    "non-expiring": sólo muere si el user revoca permisos o el long token
    muere.
    """
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "access_token": user_token_long,
                "fields": (
                    "id,name,access_token,picture{url},"
                    "instagram_business_account{id,username,profile_picture_url}"
                ),
                "limit": 100,
            },
            timeout=15.0,
        )
        if r.status_code != 200:
            logger.error(
                "list_pages FAILED status=%s body=%s",
                r.status_code, r.text[:300],
            )
            return []
        return (r.json() or {}).get("data") or []
    except Exception as exc:
        logger.exception("list_pages EXCEPTION: %s", exc)
        return []


def build_meta_pages_payload(pages: list[dict]) -> dict:
    """Convierte `/me/accounts` al formato `tenant.settings_json.meta_pages`.

    Una FB Page con IG Business vinculada se indexa dos veces: por FB Page ID
    (platform=facebook) y por IGSID (platform=instagram), ambos con el mismo
    Page Token — Graph API acepta el token de la página para ambos canales.

    Formato conservado idéntico al que hoy consume `src/messaging/meta.py` y
    `src/api/routes/cases.py::case_send_meta_message` — no hay regresión.
    """
    out: dict[str, dict] = {}
    for p in pages:
        page_id = str(p.get("id") or "").strip()
        token = (p.get("access_token") or "").strip()
        if not page_id or not token:
            continue
        # Fase B0g: picture_url persistido para mostrar avatar de la
        # página en el inbox. URLs Graph CDN duran ~30d; refresh via
        # POST /admin/integrations/meta/refresh-pages-metadata o
        # re-OAuth.
        fb_pic = ((p.get("picture") or {}).get("data") or {}).get("url") or ""
        out[page_id] = {
            "platform": "facebook",
            "access_token": token,
            "name": (p.get("name") or "").strip(),
            "picture_url": fb_pic.strip(),
        }
        ig = p.get("instagram_business_account") or {}
        igsid = str(ig.get("id") or "").strip()
        if igsid:
            out[igsid] = {
                "platform": "instagram",
                "access_token": token,
                "username": (ig.get("username") or "").strip(),
                "linked_fb_page_id": page_id,
                "picture_url": (ig.get("profile_picture_url") or "").strip(),
            }
    return out


SUBSCRIBE_FIELDS = ["messages", "messaging_postbacks"]


def subscribe_page_to_app(page_id: str, page_token: str) -> dict:
    """Suscribe la app al webhook de la FB Page para recibir DMs entrantes.

    `POST /{page-id}/subscribed_apps?subscribed_fields=messages,messaging_postbacks`
    Idempotente — Meta acepta repetir y consolida los fields. Sin esta
    llamada las FB Pages no entregan eventos `messages` al webhook,
    aunque el page token tenga el scope `pages_messaging`.

    Returns dict con `ok`, `status` (HTTP) y `body` (recortado).
    """
    if not page_id or not page_token:
        return {"ok": False, "status": 0, "body": "missing page_id or token"}
    try:
        r = httpx.post(
            f"{GRAPH_BASE}/{page_id}/subscribed_apps",
            params={
                "subscribed_fields": ",".join(SUBSCRIBE_FIELDS),
                "access_token": page_token,
            },
            timeout=15.0,
        )
        body = (r.text or "")[:400]
        if r.status_code != 200:
            logger.warning(
                "subscribe_page_to_app FAILED page=%s status=%s body=%s",
                page_id, r.status_code, body,
            )
            return {"ok": False, "status": r.status_code, "body": body}
        return {"ok": True, "status": 200, "body": body}
    except Exception as exc:
        logger.exception("subscribe_page_to_app EXCEPTION page=%s: %s", page_id, exc)
        return {"ok": False, "status": 0, "body": str(exc)[:200]}


def summarize_pages_for_status(meta_pages: dict) -> list[dict]:
    """Lista pública de páginas para el endpoint /status — sin tokens."""
    out = []
    for pid, conf in (meta_pages or {}).items():
        if not isinstance(conf, dict):
            continue
        out.append({
            "id": pid,
            "platform": conf.get("platform", ""),
            "name": conf.get("name", ""),
            "username": conf.get("username", ""),
            "club_id": conf.get("club_id", "") or "",
        })
    return out


def merge_club_assignments(prev_pages: dict, new_pages: dict) -> dict:
    """Preserva `club_id` (asignación manual del admin) al re-OAuth.

    Cuando el admin reconecta Meta, `build_meta_pages_payload` arma un
    `meta_pages` fresco desde Graph API que NO incluye `club_id` (Graph no
    sabe nada de sedes — se asigna manualmente en backoffice). Sin este
    helper, las asignaciones se perderían en cada reconexión.

    Páginas nuevas (no estaban antes) quedan con `club_id=""`. El admin
    debe asignarlas explícitamente desde la UI.
    """
    if not isinstance(prev_pages, dict) or not isinstance(new_pages, dict):
        return new_pages
    for pid, new_conf in new_pages.items():
        if not isinstance(new_conf, dict):
            continue
        prev_conf = prev_pages.get(pid) or {}
        if isinstance(prev_conf, dict) and prev_conf.get("club_id"):
            new_conf["club_id"] = prev_conf["club_id"]
    return new_pages
