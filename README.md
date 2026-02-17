# ZeroEarth — Telegram бот MMO RPG (aiogram v3 + async SQLAlchemy)

ZeroEarth — Telegram-бот с модульной архитектурой (aiogram v3) и асинхронным доступом к БД (SQLAlchemy 2 + asyncpg/aiosqlite). В проекте есть игровые модули (персонажи, склад, экипировка, рейды, рынок, тир, апгрейды оружия) и вспомогательные утилиты (сидинг оружия/патронов, импорт кастомных emoji, редактор лута рейдов, WebApp API).

## Возможности

- Меню и навигация через inline-кнопки и команды бота.
- Персонажи: создание/выбор, просмотр характеристик.
- Склад (инвентарь) персонажа.
- Экипировка: слоты оружия/предметов, управление боезапасом.
- Тир: симуляция серий выстрелов.
- Рейды: фоновой «тикер» (переходы, поиск, бои, выход), события и уведомления.
- Рынок за игровую валюту (монеты).
- Рынок оружия за Telegram Stars (валюта `XTR`): выставление/покупка, инвойсы, pre-checkout, выдача.
- WebApp (Mini App): статические страницы (stash/market) + HTTP API (aiohttp) с проверкой Telegram initData.

## Технологии

- Python 3.11+ (рекомендуется).
- aiogram v3.
- SQLAlchemy 2 (async).
- SQLite (dev) через aiosqlite.
- PostgreSQL (prod) через asyncpg (в т.ч. Supabase/pooler).
- (опционально) Node.js + pnpm для фронтенда (в репозитории присутствует `package.json`).

## Структура проекта

- `main.py` — точка входа.
- `core/` — приложение, конфиг, логирование, middleware, механики боя/стрельбы.
- `db/` — базовые сущности, сессии, репозитории.
- `modules/` — игровые модули (роутеры, сервисы, клавиатуры, состояния FSM).
- `modules/webapp_api/server.py` — aiohttp-сервер для WebApp (страницы + API).
- `webapp/` — статические HTML/CSS/JS страницы Mini App.
- `scripts/` — утилиты (сидинг, импорт emoji, пинг БД, генерация/ресайз изображений).
- `tools/` — отдельные инструменты (например, GUI редактор лута рейдов).

## Быстрый старт (бот)

1) Создать виртуальное окружение и установить зависимости:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt
```

2) Создать `.env` и задать минимум:

- `BOT_TOKEN` — токен бота.
- `DB_URL` — строка подключения (можно не указывать: по умолчанию SQLite).

Пример безопасного шаблона (значения заменить):

```env
BOT_TOKEN=123456:REPLACE_ME
LOG_LEVEL=INFO

# SQLite для локального запуска
DB_URL=sqlite+aiosqlite:///./data/bot.db

# PostgreSQL (пример). Для Supabase/pooler часто требуется statement_cache_size=0
# DB_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME?ssl=require&statement_cache_size=0

# Mini App
TG_BOT_USERNAME=your_bot_username
TG_WEBAPP_NAME=zeroearth

# WebApp API (aiohttp)
WEBAPP_DIR=webapp
WEBAPP_PAGE=stash.html
```

3) Запуск:

```bash
python -m main
```

После запуска бот выставляет команды `/start`, `/menu`, `/character`, `/stash`, `/market`, `/quests`.

## База данных

Проект использует SQLAlchemy async-сессии и много прямого SQL в сервисах.

SQLite:
- По умолчанию бот пишет в `./data/bot.db`.
- Автосоздание таблиц через `Base.metadata.create_all` включено только для SQLite.
- В текущем состоянии SQLAlchemy-модели описывают минимум (`users`), а большинство игровых таблиц ожидаются со стороны PostgreSQL-схемы.

PostgreSQL (рекомендуется для полного функционала):
- Указать `DB_URL=postgresql+asyncpg://...`.
- При использовании PgBouncer/Supabase pooler часто нужно отключить кеш prepared statements: `statement_cache_size=0`.

Минимально ожидаемые таблицы (по запросам в коде):

- `users`
- `characters`
- `equipment`
- `character_inventory`
- `character_ammo_loadout`
- `character_health`
- `character_faction_profile`
- `items`
- `item_equipment_stats`
- `weapons`
- `weapon_mods`
- `weapon_uniques`
- `calibers`
- `ammo_types`
- `raids`
- `raid_locations`
- `raid_points`
- `raid_visited_points`
- `raid_point_presence`
- `raid_point_itemtype_weights`
- `raid_point_loot`
- `raid_inventory`
- `raid_fights`
- `raid_fight_participants`
- `raid_logs`
- `market_listings`
- `stars_weapon_listings`
- `stars_orders`
- `stars_ledger_entries`
- `stars_user_balance`

Важно: `schema.sql` и `data.sql` в репозитории сейчас пустые. Схему PostgreSQL нужно хранить/поддерживать отдельно (например, migrations) или загрузить в БД до запуска.

## WebApp (Mini App)

В проекте есть два слоя:

1) Статические страницы в `webapp/` (например, `stash.html`, `market.html`).
2) aiohttp сервер `modules/webapp_api/server.py`, который:
   - отдаёт страницы,
   - отдаёт API (`/api/me/stash`, `/api/market/listings` и т.д.),
   - валидирует Telegram initData (`X-Tg-InitData`/`initData`), чтобы определить `tg_id`.

Запуск WebApp API:

```bash
python modules/webapp_api/server.py --host 127.0.0.1 --port 3001
```

Переменные окружения для WebApp API:

- `DB_URL` (или `DATABASE_URL/DB_DSN/...`) — обязательно.
- `WEBAPP_DIR` — путь к директории со статикой (по умолчанию `webapp`).
- `WEBAPP_PAGE` — страница по умолчанию (по умолчанию `stash.html`).
- `ENV_FILE` — путь к env-файлу, если нужно грузить не `.env`.

Чтобы Mini App открывался из Telegram, домен WebApp должен быть добавлен в BotFather (и/или в настройках Mini App). Для локальной разработки обычно используют tunnel (например, ngrok) и указывают публичный URL домена.

## Рынок оружия за Telegram Stars

Модуль `modules/stars_weapon_market/` использует Telegram Stars:

- `currency = "XTR"`.
- `provider_token` в `send_invoice` пустой (`""`) — для Stars это ожидаемая конфигурация.
- Обработчики:
  - `@router.pre_checkout_query()` — валидация заказа.
  - `@router.message(F.successful_payment)` — финализация и выдача.

Для корректной работы требуется рабочая PostgreSQL-схема (таблицы `stars_*`) и доступ бота к платежам Stars.

## Полезные утилиты

- `scripts/seed_weapons.py` — сидинг калибров/патронов/оружия в БД.
- `scripts/create_c.py` — создание персонажа по `--tg-id` (полезно для тестов).
- `scripts/db_ping.py` — проверка подключения к PostgreSQL (требует `psycopg2`/`psycopg`).
- `scripts/import_custom_emoji_pack.py` — импорт PNG как кастомные emoji в набор (нужны `requests` и `Pillow`).
- `tools/raid_loot_editor.py` — GUI редактор лута рейдов (tkinter) для PostgreSQL.

Пример запуска сидера оружия (DB_URL берётся из `.env`):

```bash
python scripts/seed_weapons.py
```

## Добавление нового модуля (кратко)

1) Создать пакет в `modules/<name>/` с `router.py`.
2) Подключить роутер в `core/app.py` через `dp.include_router(...)`.
3) Если нужен доступ к БД — использовать `db_session: AsyncSession` из middleware `DbSessionMiddleware`.

## Безопасность

- Не коммить `.env` с токенами и паролями.
- Хранить секреты в переменных окружения/секрет-менеджере.

## Q&A (для уточнения требований)

**Q1:** Где хранится актуальная PostgreSQL-схема (migrations/дамп), и нужно ли добавить её в репозиторий (например, Alembic)?

**Q2:** «Веб приложение» должно открываться через Mini App (`startapp`) или через прямой URL (`WEBAPP_PUBLIC_URL`) — какой вариант считать основным?

**Q3:** Нужен ли Docker/compose для развёртывания (бот + PostgreSQL + WebApp API), или достаточно инструкций для systemd/PM2?
