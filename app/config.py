from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    planfact_api_key: str = ""
    planfact_base_url: str = "https://api.planfact.io/api/v1"
    database_path: str = "./data/pnl.db"
    cache_ttl: int = 300
    port: int = 8000
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # --- Dodo IS API ---
    # Токен получаем либо вручную (для локальной разработки) — кладём в .env,
    # либо позже — из Postgres соседнего сервиса (на VPS).
    dodo_is_access_token: str = ""
    dodo_is_base_url: str = "https://api.dodois.io/dodopizza/ru"
    dodo_is_auth_url: str = "https://api.dodois.io/auth"


settings = Settings()
