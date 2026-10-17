# GoodsWarehouse v1.0 — план задач

Статусы: ☐ не начато · ◐ в работе · ☑ принято. Спека: `docs/SPEC.md`, дизайн: `docs/DESIGN.md`.

## T1. Каркас проекта — Medium — ☐
Django 5.1 проект `config` с приложениями `logistics`, `notify`, `bot`, `public`, `panel` (пустые, с `apps.py`, `urls.py` где нужно). `requirements.txt` (django, dj-database-url, httpx, phonenumbers, openpyxl, whitenoise, gunicorn) и `requirements-dev.txt` (pytest, pytest-django, ruff). Настройки читают env из SPEC §11 (`.env.example`). Русская локаль, `TIME_ZONE=Europe/Moscow`. `pytest.ini`/`pyproject.toml` с ruff. Статика: `static/css/tokens.css` (точно по DESIGN.md), `static/css/app.css` с базовыми компонентами из DESIGN.md, `static/vendor/htmx.min.js` 2.0.3. Базовые шаблоны `templates/base.html`, `templates/public/base.html` (шапка/футер), `templates/panel/base.html` (сайдбар) — пустые страницы-заглушки `/` и `/panel/` (под логином). Smoke-тест: обе страницы отвечают 200/302.
**Проверка:** `pytest`, `ruff check .`, `python manage.py check`, runserver открывает `/`.

## T2. Модели и доменные сервисы — Careful — ☐ (после T1)
`logistics`: Client, Truck, Cargo, ContactAttempt по SPEC §5. Генерация `GW-NNNN` и `R-YYYY-NNN` без гонок (select_for_update или отдельный счётчик). Нормализация телефона +7 (`phonenumbers`, отклонять невалидные). `services.py`: `truck_depart`, `truck_arrive`, `cargo_pickup`, `add_cargo`, `assign_to_truck`, `InvalidTransition` по SPEC §6. Хук для уведомлений: сервисы вызывают `notify.services.enqueue_*`. В этой задаче сделать модель Notification и `enqueue_*`, без отправки. Django admin для всех моделей. Группы «Менеджер»/«Склад» создаются data-миграцией.
**Проверка:** тесты переходов (включая недопустимые), каскада, агрегации уведомлений по клиенту, закрытия машины, нормализации телефона, уникальности кодов.

## T3. Отправка уведомлений и список обзвона — Careful — ☐ (после T2)
`notify/telegram.py` (httpx-клиент: sendMessage, обработка 403/429/5xx, dry-run без токена). `notify/messages.py` (тексты по DESIGN.md). Команда `send_notifications` (батч pending, ретраи до 3, 403 → `failed` + обнуление chat_id). Команда `daily_reminders` по SPEC §7. `logistics/calllist.py`: `call_list(date)` строго по правилам SPEC §7.
**Проверка:** тесты с моком httpx на все ветки ошибок, идемпотентность reminders, каждое правило включения/исключения в обзвон.

## T4. Telegram-бот — Medium — ☐ (после T3)
Команда `run_bot`: long polling `getUpdates`, хранение offset. Обработчики по SPEC §8 (deep link, request_contact, меню из трёх кнопок). Логика в `bot/handlers.py` как чистые функции `update → list[ответ]`, чтобы тестировать без сети. В карточке клиента ссылка `t.me/<bot>?start=<token>` (используется в T6).
**Проверка:** тесты обработчиков на фикстурах апдейтов: привязка по токену, по номеру, неизвестный номер, «Мои грузы», «Расписание».

## T5. Публичный сайт — Medium — ☐ (после T2, параллельно с T3)
Страницы `/`, `/schedule/`, `/track/`, `/contacts/` по SPEC §9 и DESIGN.md (главная по макету: hero, 4 шага, ближайшие машины, контакты). Rate limit на `/track/`. Адаптив 375px.
**Проверка:** тесты views (track: успех, неверный код, неверные цифры, лимит), скриншоты 375 и 1280 в отчёте.

## T6. Панель: машины, грузы, клиенты, импорт — Careful — ☐ (после T3)
Логин/выход (Django auth, шаблон в стиле панели). Дашборд (4 плитки). Машины: список, создание/редактирование, карточка со stepper, action-bar и `<dialog>` подтверждения с подсчётом «получат в Telegram / уйдут в обзвон». Добавление груза с поиском/созданием клиента (HTMX). Импорт манифеста .xlsx: предпросмотр с ошибками по строкам → подтверждение, атомарно. Грузы: список с фильтрами. Клиенты: поиск, карточка (грузы, уведомления, звонки, ссылка на бота). Права групп.
**Проверка:** тесты views и импорта (валидный файл, ошибки строк, откат), права «Склад» vs «Менеджер», скриншоты.

## T7. Панель: выдача и обзвон — Medium — ☐ (после T6)
Экран «Выдача» и экран «Обзвон» по DESIGN.md. Отметки исходов через HTMX, счётчик обзвона в сайдбаре.
**Проверка:** тесты: выдача меняет статусы и закрывает машину, отметка исхода убирает клиента из списка по правилам, скриншоты.

## T8. Демо-данные и деплой — Light — ☐ (после T4, T5, T7)
Команда `seed_demo` (3 машины в разных статусах, ~25 клиентов, часть без Telegram, грузы во всех статусах, пара звонков). `docs/DEPLOY.md`: VPS, PostgreSQL, gunicorn, nginx, systemd-юниты `gw-web`, `gw-bot`, таймеры `send_notifications` (каждую минуту) и `daily_reminders` (09:00). README с быстрым стартом.
**Проверка:** с нуля `migrate && seed_demo && runserver` даёт заполненный дашборд, `pytest` зелёный.

Порядок: T1 → T2 → (T3 ∥ T5) → (T4 ∥ T6) → T7 → T8.
