from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.router import router as v1_router
from src.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fase 1: ensure_waha_webhooks() irá aquí
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

    app.include_router(v1_router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
