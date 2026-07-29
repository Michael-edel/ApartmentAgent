from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ApartmentAgent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://apartment:apartment@db:5432/apartment"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True
    telegram_notify_min_score: int = 85
    check_interval_minutes: int = 120

    search_enabled: bool = True
    search_interval_minutes: int = 15
    search_queries_per_run: int = 8
    # The example .env uses a readable comma-separated value.  NoDecode keeps
    # pydantic-settings from trying to parse that value as JSON before the
    # validator below normalizes it.
    search_providers: Annotated[list[str], NoDecode] = ["krisha_direct", "brave", "bing_rss"]
    brave_search_api_key: str = ""
    search_freshness: str = "pw"

    krisha_search_pages: int = 3
    krisha_search_timeout_seconds: float = 20.0
    krisha_search_delay_seconds: float = 2.0
    auto_import_enabled: bool = True
    auto_import_delay_seconds: float = 1.5

    ai_enabled: bool = True
    ai_remote_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ai_timeout_seconds: float = 20.0

    max_price_kzt: int = 30_000_000
    min_area_m2: float = 55.0
    max_area_m2: float = 70.0
    min_score_to_notify: int = 85

    @field_validator("search_providers", mode="before")
    @classmethod
    def parse_search_providers(cls, value: object) -> list[str]:
        if isinstance(value, str):
            requested = [item.strip().lower() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            requested = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            requested = []

        # Прямой поиск Krisha является обязательным основным источником.
        # Старый локальный .env с SEARCH_PROVIDERS=bing_rss больше не отключает его.
        allowed = {"krisha_direct", "brave", "bing_rss"}
        providers = ["krisha_direct"]
        for provider in requested or ["brave", "bing_rss"]:
            if provider in allowed and provider not in providers:
                providers.append(provider)
        return providers


@lru_cache
def get_settings() -> Settings:
    return Settings()
