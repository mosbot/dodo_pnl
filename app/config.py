from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    planfact_api_key: str = ""
    planfact_base_url: str = "https://api.planfact.io/api/v1"
    # SQLite-путь — legacy single-tenant хранилище. После S2.x уйдёт.
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

    # --- S1+: Postgres / auth ---
    # Async-подключение к Postgres. Формат:
    #   postgresql+asyncpg://user:pass@host:port/db
    # На проде указывает на тот же Postgres, что у соседского сервиса; наша
    # схема — pnl_service.* (см. app/db.py). Read-доступ на public.dodois_credentials.
    database_url: str = ""

    # Секрет для подписи cookie-сессий и CSRF-токенов. На проде — 64 hex-байта,
    # генерируется один раз при первом деплое и не меняется (иначе все сессии
    # инвалидируются). В .env.example — пустая строка-плейсхолдер.
    secret_key: str = ""

    # Имя схемы для наших таблиц. Менять не надо.
    db_schema: str = "pnl_service"


settings = Settings()
