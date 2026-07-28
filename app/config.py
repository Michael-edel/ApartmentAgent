from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ApartmentAgent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://apartment:apartment@db:5432/apartment"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    check_interval_minutes: int = 120

    search_enabled: bool = True
    search_interval_minutes: int = 15

    max_price_kzt: int = 30_000_000
    min_area_m2: float = 55.0
    max_area_m2: float = 70.0
    min_score_to_notify: int = 85


@lru_cache
def get_settings() -> Settings:
    return Settings()
