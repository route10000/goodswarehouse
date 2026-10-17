# Деплой GoodsWarehouse на VPS (Ubuntu 24.04)

Пример: домен `gw.example.ru`, пользователь `gw`, код в `/srv/gw/app`. Подставьте свои значения.

## 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-dev build-essential git \
    postgresql-16 libpq-dev nginx certbot python3-certbot-nginx
sudo timedatectl set-timezone Asia/Vladivostok
sudo adduser --system --group --home /srv/gw gw
sudo usermod -aG gw www-data   # nginx должен видеть сокет gunicorn
```

## 2. PostgreSQL 16

```bash
sudo -u postgres psql <<'SQL'
CREATE USER gw WITH PASSWORD 'сложный-пароль';
CREATE DATABASE gw OWNER gw ENCODING 'UTF8';
SQL
```

## 3. Код и окружение

```bash
sudo -u gw git clone <адрес-репозитория> /srv/gw/app
cd /srv/gw/app
sudo -u gw python3.12 -m venv .venv
sudo -u gw .venv/bin/pip install -r requirements.txt
```

Файл `/srv/gw/app/.env` (права `600`, владелец `gw`), все ключи из `.env.example`:

```ini
SECRET_KEY=длинная-случайная-строка
DEBUG=false
ALLOWED_HOSTS=gw.example.ru
DATABASE_URL=postgres://gw:сложный-пароль@localhost:5432/gw
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_BOT_USERNAME=gw_bot
WAREHOUSE_ADDRESS=г. Владивосток, ул. ...
WAREHOUSE_HOURS=Пн–Сб 9:00–18:00
WAREHOUSE_PHONE=+79140000000
FREE_STORAGE_DAYS=7
CALL_AFTER_DAYS=3
WAREHOUSE_BLOCK_CAPACITY=400
WAREHOUSE_CITY=Владивосток
```

Ключ: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.
Без `TELEGRAM_BOT_TOKEN` отправщик работает в dry-run, а бот не запустится.

Настройки читаются только из переменных окружения, поэтому для ручных команд
загружайте `.env` в shell:

```bash
cd /srv/gw/app
sudo -u gw bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py migrate'
sudo -u gw bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py collectstatic --noinput'
sudo -u gw bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py createsuperuser'
```

Статика собирается в `/srv/gw/app/staticfiles` (`STATIC_ROOT`).
Сотрудников заводите в админке `/admin/` и добавляйте в группы «Менеджер» или «Склад».
`seed_demo` на боевом сервере не запускайте.

Удобный алиас: `alias gwm='sudo -u gw bash -c "cd /srv/gw/app && set -a && . ./.env && set +a && .venv/bin/python manage.py \"\$@\"" --'`, затем `gwm migrate`.

## 4. Gunicorn: `gw-web.service`

`gunicorn` уже есть в `requirements.txt`.

`/etc/systemd/system/gw-web.service`:

```ini
[Unit]
Description=GoodsWarehouse web (gunicorn)
After=network.target postgresql.service

[Service]
User=gw
Group=gw
WorkingDirectory=/srv/gw/app
EnvironmentFile=/srv/gw/app/.env
RuntimeDirectory=gw
UMask=0007
ExecStart=/srv/gw/app/.venv/bin/gunicorn config.wsgi:application \
    --bind unix:/run/gw/gunicorn.sock --workers 3 --timeout 60
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 5. Nginx

`/etc/nginx/sites-available/gw`:

```nginx
server {
    listen 80;
    server_name gw.example.ru;
    client_max_body_size 5m;

    location /static/ {
        alias /srv/gw/app/staticfiles/;
        expires 30d;
    }

    location / {
        proxy_pass http://unix:/run/gw/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/gw /etc/nginx/sites-enabled/gw
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 6. HTTPS

```bash
sudo certbot --nginx -d gw.example.ru
```

Certbot сам добавит редирект на HTTPS и таймер продления (`systemctl list-timers | grep certbot`).

## 7. Бот: `gw-bot.service`

`/etc/systemd/system/gw-bot.service`:

```ini
[Unit]
Description=GoodsWarehouse Telegram bot (long polling)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=gw
Group=gw
WorkingDirectory=/srv/gw/app
EnvironmentFile=/srv/gw/app/.env
ExecStart=/srv/gw/app/.venv/bin/python manage.py run_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 8. Отправщик уведомлений: `gw-notify.service`

`/etc/systemd/system/gw-notify.service`:

```ini
[Unit]
Description=GoodsWarehouse notification sender
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=gw
Group=gw
WorkingDirectory=/srv/gw/app
EnvironmentFile=/srv/gw/app/.env
ExecStart=/srv/gw/app/.venv/bin/python manage.py send_notifications --loop --interval 10
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 9. Таймеры

Ордер на день в 08:30 — `/etc/systemd/system/gw-order.service`:

```ini
[Unit]
Description=GoodsWarehouse: build daily call order

[Service]
Type=oneshot
User=gw
Group=gw
WorkingDirectory=/srv/gw/app
EnvironmentFile=/srv/gw/app/.env
ExecStart=/srv/gw/app/.venv/bin/python manage.py build_daily_order
```

`/etc/systemd/system/gw-order.timer`:

```ini
[Unit]
Description=GoodsWarehouse: daily order at 08:30

[Timer]
OnCalendar=*-*-* 08:30:00 Asia/Vladivostok
Persistent=true

[Install]
WantedBy=timers.target
```

Напоминания в 09:00 — `/etc/systemd/system/gw-reminders.service`:

```ini
[Unit]
Description=GoodsWarehouse: queue reminders

[Service]
Type=oneshot
User=gw
Group=gw
WorkingDirectory=/srv/gw/app
EnvironmentFile=/srv/gw/app/.env
ExecStart=/srv/gw/app/.venv/bin/python manage.py daily_reminders
```

`/etc/systemd/system/gw-reminders.timer`:

```ini
[Unit]
Description=GoodsWarehouse: reminders at 09:00

[Timer]
OnCalendar=*-*-* 09:00:00 Asia/Vladivostok
Persistent=true

[Install]
WantedBy=timers.target
```

Включить всё:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gw-web gw-bot gw-notify gw-order.timer gw-reminders.timer
systemctl list-timers 'gw-*'
```

## 10. Логи

```bash
journalctl -u gw-web -f
journalctl -u gw-bot -n 200
journalctl -u gw-notify --since today
journalctl -u gw-order -u gw-reminders --since yesterday
sudo tail -f /var/log/nginx/error.log
```

## 11. Обновление

```bash
cd /srv/gw/app
sudo -u gw git pull
sudo -u gw .venv/bin/pip install -r requirements.txt
sudo -u gw bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py migrate'
sudo -u gw bash -c 'set -a; . ./.env; set +a; .venv/bin/python manage.py collectstatic --noinput'
sudo systemctl restart gw-web gw-bot gw-notify
```

## 12. Резервные копии

```bash
sudo mkdir -p /srv/gw/backups && sudo chown gw:gw /srv/gw/backups
```

`sudo -u gw crontab -e`:

```cron
# каждый день в 03:00: дамп базы, хранить 14 дней
0 3 * * * pg_dump -Fc postgres://gw:сложный-пароль@localhost:5432/gw > /srv/gw/backups/gw-$(date +\%F).dump && find /srv/gw/backups -name 'gw-*.dump' -mtime +14 -delete
```

Пароль удобнее держать в `/srv/gw/.pgpass` (`localhost:5432:gw:gw:пароль`, права `600`)
и писать `pg_dump -Fc -h localhost -U gw gw`. Восстановление:
`pg_restore --clean -h localhost -U gw -d gw /srv/gw/backups/gw-ГГГГ-ММ-ДД.dump`.
Копируйте дампы и на другой сервер.
