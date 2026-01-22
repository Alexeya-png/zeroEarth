# MMO TG Bot Skeleton (aiogram v3 + async SQLAlchemy)

## Quick start
1) Create venv and install deps:
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

2) Create `.env` from `.env.example` and set `BOT_TOKEN`.

3) Run:
```bash
python -m main
```

DB defaults to SQLite at `./data/bot.db`.
