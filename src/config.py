from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://chatpro:chatpro@localhost:5432/chatpro"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "dev-secret-key-cambiar-en-produccion"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    app_env: str = "development"
    public_base_url: str = "http://localhost:8000"

    # WAHA (Fase 1)
    waha_api_url: str = ""
    waha_api_key: str = ""
    waha_webhook_token: str = ""

    # IA (Fase 2)
    anthropic_api_key: str = ""

    # Conectores (Fase 5) — KEK de 32 bytes en hexadecimal para cifrado AES-GCM
    connector_master_key: str = ""

    # Voyage AI (Fase 9) — embeddings para RAG
    voyage_api_key: str = ""

    # Rate limiting por tenant/contacto (Fase 22)
    rate_limit_messages: int = 10
    rate_limit_window_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
