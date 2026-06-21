from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    planfact_api_key: str = ""
    planfact_base_url: str = "https://api.planfact.io/api/v1"
    # TTL in-memory кэша PlanFact-клиента (LRU). Покрывает /projects,
    # /operationcategories и bulk /operations (тяжёлый — за месяц целиком).
    # Drill-down list_operations явно идёт мимо кэша (use_cache=False),
    # т.к. у него меняются offset/limit/category_ids на каждый клик.
    # Закрытые месяцы дополнительно лежат в cache_history (S3.5) — там
    # инвалидация ручная через админку «Переоткрыть».
    cache_ttl: int = 3600
    port: int = 8000

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

    # --- Токен-брокер sa (платформенный сервис авторизации) ---
    # Когда задан sa_token_broker_url, Dodo IS access-токен берём у sa
    # (GET {url}?sub=<sub>, заголовок X-Admin-Token), а не из таблицы
    # public.dodois_credentials. sa тихо рефрешит токен по offline_access.
    # dodois_sub_map — соответствие нашего dodois_credentials_name → sub
    # аккаунта в sa (JSON в .env). Если брокер не настроен или имя не
    # в карте — фолбэк на legacy-чтение dodois_credentials (старый VPS).
    sa_token_broker_url: str = ""
    sa_internal_token: str = ""
    dodois_sub_map: dict[str, str] = {}
    # База sa для SSO: pnl форвардит sa-куку в GET {sa_base_url}/me и
    # /entitlements (внутренний адрес в общей docker-сети). Если пусто — SSO
    # выключен (работает только локальный логин).
    sa_base_url: str = ""
    # Внешний URL OAuth-входа sa (кнопка «Войти через Dodo IS» и редирект при
    # первом входе) + внешний базовый URL pnl для return_to.
    sa_login_url: str = ""        # напр. https://sa.dodotool.ru/dodois/login
    public_base_url: str = ""     # напр. https://pnl.dodotool.ru


settings = Settings()
