# GoodsWarehouse

Учёт грузов из Китая: машины Уссурийск → Владивосток, грузы клиентов по артикулам,
уведомления в Telegram, ордер на обзвон, выдача со склада и Ops-центр для менеджера.
Django 5.1 + HTMX, PostgreSQL в проде, SQLite локально.

## Быстрый старт (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
$env:DEBUG="true"
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed_demo
.venv\Scripts\python manage.py runserver
```

Панель: http://127.0.0.1:8000/panel/ — логины `manager` (Менеджер) и `sklad` (Склад),
демо-пароль `demo12345` (или значение `DEMO_PASSWORD`). Пересоздать демо-данные:
`manage.py seed_demo --reset`. Админка `/admin/` — после `manage.py createsuperuser`.

## Тесты и линтер

```powershell
.venv\Scripts\pytest
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
```

## Документация

- `docs/SPEC.md` — требования, `docs/DESIGN.md` — дизайн, `docs/PLAN.md` — задачи
- `docs/DEPLOY.md` — деплой на VPS (gunicorn, nginx, systemd)
- `filemap.md` — карта файлов
- Переменные окружения — `.env.example`
