# GoodsWarehouse

Stack: Python 3.12, Django 5.1, Django templates + HTMX 2.0.3 and three.js r160 (both vendored, no build step), plain CSS tokens, SQLite dev / PostgreSQL prod, pytest + ruff.

## Commands (Windows, from repo root)
- Setup: `python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt`
- Run: `.venv\Scripts\python manage.py runserver`
- Test: `.venv\Scripts\pytest`
- Lint: `.venv\Scripts\ruff check . && .venv\Scripts\ruff format --check .`
- Bot / jobs: `manage.py run_bot`, `manage.py send_notifications`, `manage.py daily_reminders`
- Demo data: `manage.py seed_demo` · Daily call order: `manage.py build_daily_order`

## Conventions
- Status changes happen ONLY through `logistics/services.py` (and `notify/services.py` for notifications). Views, bot, admin never set `.status` directly.
- Notifications are queued as `pending` inside the same transaction. Only `send_notifications` talks to Telegram, never a web request.
- Phones are stored in E.164 (`+7...`). Always normalize via the helper in `logistics`.
- All user-facing text is in Russian. Code, identifiers, and commits are in English.
- Colors, fonts, and spacing come only from `static/css/tokens.css` variables. No new hex values in templates.
- No network calls in tests: mock `httpx`.
- Update `filemap.md` when you add or move files.

## Docs
- Spec: `docs/SPEC.md` · Design: `docs/DESIGN.md` · Tasks: `docs/PLAN.md` · Navigation: `filemap.md`
