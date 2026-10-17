# GoodsWarehouse v1.0 — план задач

Статусы: ☐ не начато · ◐ в работе · ☑ принято. Спека: `docs/SPEC.md`, дизайн: `docs/DESIGN.md`.

## T1. Каркас проекта — Medium — ☐
Django 5.1 проект `config` с приложениями `logistics`, `notify`, `bot`, `public`, `panel`. `requirements.txt` (django, dj-database-url, httpx, phonenumbers, openpyxl, whitenoise, gunicorn) и `requirements-dev.txt` (pytest, pytest-django, ruff). Настройки читают env из SPEC §11 (`.env.example`). Русская локаль, `TIME_ZONE=Asia/Vladivostok`. Ruff и pytest настроены в `pyproject.toml`. Статика: `static/css/tokens.css` (точно по DESIGN.md), `static/css/app.css` (базовые компоненты: btn, pill, field, table, panel, kpi), `static/vendor/htmx.min.js` 2.0.3, `static/vendor/three/` r160 (`three.module.min.js`, `CSS2DRenderer.js`). Шаблоны `templates/base.html`, `templates/public/base.html` (чёрная шапка и футер), `templates/panel/base.html` (топбар + иконочный сайдбар по DESIGN.md), заглушки `/` и `/panel/` (под логином). Smoke-тесты.
**Проверка:** `pytest`, `ruff check .`, `manage.py check`, runserver открывает `/`.

## T2. Модели и доменные сервисы — Careful — ☐ (после T1)
`logistics`: Client, Truck, Cargo (с `article`), DailyOrder, DailyOrderLine, ContactAttempt по SPEC §5. Генерация `GW-NNNN` и `R-YYYY-NNN` без гонок. Нормализация телефона +7. `services.py`: `truck_depart`, `truck_arrive`, `cargo_pickup`, `add_cargo`, `assign_to_truck`, `InvalidTransition` по SPEC §6. Модель Notification и `notify.services.enqueue_*` (без отправки). Django admin. Группы «Менеджер» и «Склад» через data-миграцию.
**Проверка:** тесты переходов (включая недопустимые), каскада, агрегации уведомлений, закрытия машины, телефона, уникальности кодов.

## T3. Уведомления и формирование ордера — Careful — ☐ (после T2)
`notify/telegram.py` (httpx, 403/429/5xx, dry-run). `notify/messages.py` (тексты по DESIGN.md). Команды `send_notifications`, `daily_reminders`. `logistics/orders.py`: `build_daily_order(date, user=None)` по SPEC §7 (включения, исключения, перенос `no_answer`/`pending`, повторное формирование не трогает отмеченные), `mark_line(line, status, user, promised_date, comment)` пишет ContactAttempt. Команда `build_daily_order`.
**Проверка:** тесты с моком httpx на все ветки, идемпотентность reminders, каждое правило ордера, повторное формирование.

## T4. Telegram-бот — Medium — ☐ (после T3)
`run_bot` (long polling), обработчики по SPEC §8 как чистые функции в `bot/handlers.py`.
**Проверка:** тесты обработчиков на фикстурах апдейтов.

## T5. Публичный сайт — Medium — ☐ (после T2, параллельно с T3)
`/`, `/schedule/`, `/track/`, `/contacts/` по SPEC §9 и DESIGN.md (чёрный hero, белые секции, тёмно-зелёные кнопки, жирный Onest). Rate limit на `/track/`. Адаптив 375px.
**Проверка:** тесты views, скриншоты 375 и 1280.

## T6. Панель: каркас, машины, грузы, клиенты, импорт — Careful — ☐ (после T3)
Логин в стиле панели. Командный поиск в топбаре (артикул, телефон, код, рейс). Машины: список, создание/редактирование, карточка со stepper, action-bar и `<dialog>` с подсчётом «получат в Telegram / попадут в ордер». Добавление груза (HTMX, поиск или создание клиента). Импорт .xlsx с предпросмотром, атомарно. Грузы с фильтрами. Клиенты: поиск, карточка, ссылка на бота. Права групп.
**Проверка:** тесты views, импорта и прав, скриншоты.

## T7. Панель: ордер на день и выдача — Medium — ☐ (после T6)
Экран ордера по DESIGN.md: счётчики-фильтры, таблица, кнопки «Позвонили» и «Не дозвонились» через HTMX, дата приезда и комментарий, печать A4 (print-CSS), выгрузка `.xlsx`, прошлые ордера только для чтения. Экран «Выдача». Счётчик «осталось» в сайдбаре.
**Проверка:** тесты отметок, экспорта и выдачи (закрытие машины), скриншоты экрана и печати.

## T8. Ops-центр: данные и панели — Careful — ☐ (после T6)
`/panel/` по DESIGN.md: KPI, `.pipeline`, `.drawer`, `.tracker`, `.timeline`, `.fleet`, плоская `.route-map` (fallback). JSON `GET /panel/api/scene/` по контракту DESIGN.md «3D-сцена · Данные», плюс `docs/SCENE_API.md` с точной схемой. В центре пока контейнер `#ops-scene` с fallback-схемой.
**Проверка:** тесты эндпоинта и агрегатов KPI, скриншоты 1440 и 375.

## T9. 3D-сцена погрузки и выгрузки — Fable — ☐ (после T8)
`static/js/ops-scene/` на three.js r160 строго по DESIGN.md «3D-сцена»: мир Уссурийск → дорога → склад Владивосток, low-poly фуры, InstancedMesh-коробки, анимации погрузки, движения и выгрузки, CSS2D-подписи, переключатель подписей, клик открывает drawer и tracker (через события, без изменения T8-шаблонов сверх точки монтирования), fallback.
**Проверка:** на демо-данных видны все состояния машин, 60 fps на 2 000 коробок, скриншоты и короткое описание проверенных сценариев.

## T10. Демо-данные и деплой — Light — ☐ (после T4, T5, T7, T9)
`seed_demo` (машины во всех статусах, ~25 клиентов, часть без Telegram, грузы с артикулами во всех статусах, ордер на сегодня с отметками). `docs/DEPLOY.md` (VPS, PostgreSQL, gunicorn, nginx, systemd `gw-web`, `gw-bot`, таймеры `send_notifications` раз в минуту, `build_daily_order` в 08:30, `daily_reminders` в 09:00). README.
**Проверка:** с нуля `migrate && seed_demo && runserver` даёт живой Ops-центр, `pytest` зелёный.

Порядок: T1 → T2 → (T3 ∥ T5) → (T4 ∥ T6) → (T7 ∥ T8) → T9 → T10.
