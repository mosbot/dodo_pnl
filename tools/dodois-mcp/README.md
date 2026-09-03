# Dodo IS API — MCP Server

MCP-сервер для работы с Dodo IS API (https://docs.dodois.io) из Claude
(Desktop / Code / claude.ai). 28 инструментов: продажи, финансы (daily/monthly),
доставка, кухня, персонал, стопы, РКО/РС и проверки (controlling), оценки
клиентов и готовый LFL (customer-feedback), ревизии (inventory) + универсальный
`dodois_get` для любых остальных ручек.

## 1. Предварительно

- Python 3.11+
- Приложение на https://marketplace.dodois.io/manage (client_id + client_secret)
  со scopes: `accounting sales deliverystatistics productionefficiency
  stopsales incentives staffmembers:read shifts:read user.role:read offline_access`
  (+ по необходимости; РКО/РС и customer-feedback работают без отдельного scope).

## 2. Установка

```bash
cd dodois-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # заполнить DODO_CLIENT_ID / DODO_CLIENT_SECRET
python auth_setup.py         # OAuth2 PKCE: откроет браузер, сохранит tokens.json
```

`tokens.json` содержит refresh_token — сервер дальше продлевает токены сам
(offline_access). Файл НЕ коммитить.

### Режим брокера SA (рекомендуется на VPS, без auth_setup.py)

Если MCP стоит рядом с sa, токен берётся у брокера SA — refresh-токены
держит живыми сам sa (beat раз в час), переавторизация не нужна никогда.
В `.env` вместо tokens.json:

```
SA_TOKEN_BROKER_URL=http://127.0.0.1:8001/internal/dodois-token   # порт api sa на loopback
SA_INTERNAL_TOKEN=<ADMIN_API_TOKEN из ~/dodotool-sa/.env>
DODO_SUB=<sub Dodo-аккаунта, чьим токеном ходим>   # 000d3a21… = Коваль Андрей
SA_BROKER_CACHE_SEC=120                              # кэш токена (брокер срок не отдаёт)
```

При 401 от Dodo IS кэш сбрасывается и запрос повторяется со свежим токеном.
Порт брокера публикуется в `docker-compose.prod.yml` sa:
`api: ports: ["127.0.0.1:8001:8000"]` — только loopback, снаружи недоступен.

## 3. Подключение к Claude

### Claude Desktop / Claude Code (stdio)

`claude_desktop_config.json` (или `claude mcp add`):

```json
{
  "mcpServers": {
    "dodois": {
      "command": "/path/to/dodois-mcp/.venv/bin/python",
      "args": ["/path/to/dodois-mcp/server.py"]
    }
  }
}
```

### claude.ai custom connector (HTTP, на VPS)

```bash
MCP_TRANSPORT=http MCP_PORT=8788 python server.py   # streamable HTTP на /mcp
```

За реверс-прокси с TLS (Caddy):

```
mcp.example.com {
    reverse_proxy localhost:8788
}
```

В claude.ai → Settings → Connectors → Add custom connector →
`https://mcp.example.com/mcp`.

### systemd-юнит (постоянная работа на VPS)

```ini
[Unit]
Description=Dodo IS MCP
After=network.target

[Service]
WorkingDirectory=/opt/dodois-mcp
Environment=MCP_TRANSPORT=http
ExecStart=/opt/dodois-mcp/.venv/bin/python server.py
Restart=always
User=mcp

[Install]
WantedBy=multi-user.target
```

## 4. Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| DODO_CLIENT_ID / DODO_CLIENT_SECRET | приложение marketplace |
| DODO_COUNTRY | код страны, по умолчанию `ru` |
| DODO_UNITS | UUID заведений по умолчанию, через запятую |
| DODO_UNITS_ALIASES | JSON алиасов: `{"кубинка": "<uuid>"}` |
| DODO_TOKENS_FILE | путь к tokens.json |
| MCP_TRANSPORT / MCP_HOST / MCP_PORT / MCP_PATH | stdio (дефолт) или http |

## 5. Грабли Dodo IS (важно)

- Rate limit **200 rpm на токен**, сквозной по всем ручкам.
- Батчи units: максимум **30 UUID** на запрос.
- Окна дат: `finances/.../daily` — **≤10 дней**; `monthly` — ≤62 дня;
  `customer-ratings` — ≤31 дня и «сегодня» недоступно (to = вчера максимум).
- Старые месяцы отдаются МЕДЛЕННО (холодное хранилище, до ~2 мин), повторный
  запрос попадает в их кэш и быстрый.
- `orders-handover-statistics`: канал `DineIn` (без дефиса!), `Dine-in` → 400.
- `sales` в finances — без VAT.
- Полные OpenAPI-спеки: репо dodo_pnl → `docs/dodois-openapi/*.yaml`.
