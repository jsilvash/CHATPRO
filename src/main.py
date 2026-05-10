import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.router import router as v1_router
from src.auth.tokens import decode_token
from src.billing.middleware import audit_middleware
from src.config import get_settings
from src.connectors.shopify.webhook import router as shopify_webhook_router
from src.connectors.woocommerce.webhook import router as woo_webhook_router
from src.messaging.webhook import router as waha_webhook_router
from src.tenancy.context import _tenant_id_var

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Reaplica webhooks WAHA en cada arranque (WAHA no los persiste).
    try:
        from src.messaging.waha_client import ensure_waha_webhooks
        ensure_waha_webhooks()
    except Exception as e:
        logger.warning("ensure_waha_webhooks falló en startup: %s", e)
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ChatPro API",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def tenant_context_middleware(request: Request, call_next):
        """Setea ``_tenant_id_var`` en el contexto asyncio del request.

        FastAPI corre cada dependencia y el endpoint (sync) en su propio
        ``run_in_threadpool``, lo que crea un Context aislado por llamada y
        rompe la propagación de ``ContextVar.set()`` hecha desde una
        dependencia hacia el endpoint. El workaround es setear el var en el
        contexto asyncio del request, que SÍ se copia a cada threadpool worker.

        La validación 401 sigue corriendo en ``get_current_user``; este middleware
        solo "preconfigura" el tenant cuando hay un Bearer válido.
        """
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            try:
                payload = decode_token(token, "access")
                tid = uuid.UUID(payload.get("tid", ""))
                _tenant_id_var.set(tid)
            except Exception:
                # Token inválido/expirado: get_current_user devolverá 401.
                pass
        return await call_next(request)

    app.middleware("http")(audit_middleware)

    app.include_router(v1_router)
    app.include_router(waha_webhook_router)
    app.include_router(woo_webhook_router)
    app.include_router(shopify_webhook_router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
