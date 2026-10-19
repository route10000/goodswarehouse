# Публикация GoodsWarehouse на GitHub и выпуск 1.0.0

Инструкция для владельца проекта. Все команды — для Windows, PowerShell,
запускаются из корня репозитория:

```powershell
cd "C:\Users\Админ\Desktop\Shipping of Chinese Goods"
```

## 1. Проверка перед публикацией

### 1.1. Тесты и линтер

```powershell
$env:DEBUG="true"
.venv\Scripts\pytest -q
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\python manage.py makemigrations --check --dry-run
```

Все команды должны пройти без ошибок. Последняя должна вывести `No changes detected`.

### 1.2. Рабочая копия чистая

```powershell
git status
```

Ожидается `nothing to commit, working tree clean`. Если есть изменения —
закоммитьте их или отложите (`git stash`).

### 1.3. Секреты и локальные файлы не попали в git

```powershell
git ls-files .env db.sqlite3 media .venv staticfiles
```

Команда не должна ничего вывести. Если какой-то файл показан, уберите его
из индекса (сам файл на диске останется) и закоммитьте:

```powershell
git rm --cached -r .env db.sqlite3 media .venv staticfiles
git commit -m "Stop tracking local files"
```

Поиск токенов и ключей в отслеживаемых файлах (кроме `.env.example`):

```powershell
git grep -nE "TELEGRAM_BOT_TOKEN=|SECRET_KEY=" -- ':!*.example'
```

Совпадения допустимы, только если значение пустое или это заглушка в документации
(например, `SECRET_KEY=...` в `docs/DEPLOY.md`). Если в выводе настоящий токен бота
или ключ — остановитесь: удалите его из файла, **смените** токен у @BotFather
и ключ на сервере. Секрет, который уже был в каком-то коммите, остаётся в истории,
поэтому смена обязательна.

## 2. Пустой репозиторий на github.com

1. Войдите на https://github.com, нажмите **New repository** («+» вверху справа).
2. Repository name: `goodswarehouse`.
3. Видимость:
   - **Private** — код видите только вы и приглашённые люди. Подходит для рабочей
     системы компании. Рекомендуется по умолчанию.
   - **Public** — код видят все. Подходит, если проект показывается как портфолио
     или открытый продукт. Перед этим ещё раз выполните пункт 1.3.
4. **Не** отмечайте «Add a README file», «Add .gitignore», «Choose a license».
   Репозиторий должен быть пустым, иначе первый `git push` будет отклонён
   из-за расхождения историй.
5. Нажмите **Create repository**.

## 3. Подключение и отправка `main`

Замените `OWNER` на ваш логин GitHub (и в ссылке внизу `CHANGELOG.md`).

### Вариант А: HTTPS и персональный токен

1. GitHub → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token. Доступ — только к репозиторию
   `goodswarehouse`, права *Contents: Read and write* (для защиты ветки ещё
   *Administration: Read and write*). Срок действия — ограниченный.
2. Отправьте код:

```powershell
git remote add origin https://github.com/route10000/goodswarehouse.git
git push -u origin main
```

Логин — ваш логин GitHub, пароль — **токен** (не пароль от аккаунта).
Git Credential Manager его запомнит.

### Вариант Б: GitHub CLI

```powershell
winget install --id GitHub.cli
gh auth login
```

Выберите GitHub.com → HTTPS → вход через браузер. Затем:

```powershell
git remote add origin https://github.com/route10000/goodswarehouse.git
git push -u origin main
```

Проверка:

```powershell
git remote -v
git log origin/main --oneline -1
```

## 4. Ветка `feature/3d-scene`

Экспериментальная ветка с 3D-сценой: не проверена и в 1.0 не входит.
Рекомендация — **не отправлять** её. Команда выше отправляет только `main`.

Если хотите хранить её на GitHub как черновик:

```powershell
git push -u origin feature/3d-scene
gh pr create --draft --base main --head feature/3d-scene --title "3D-сцена (черновик)" --body "Экспериментальная ветка, не для слияния."
```

Черновой pull request нельзя слить, пока его не пометят как готовый.

## 5. Тег `v1.0.0`

Тег ставится на коммит, который уже отправлен в `main`:

```powershell
git checkout main
git pull
git tag -a v1.0.0 -m "GoodsWarehouse 1.0.0"
git push origin v1.0.0
```

Проверка: `git show v1.0.0 --stat` показывает автора тега и сообщение.

## 6. Релиз на GitHub

Текст релиза — раздел `## [1.0.0]` из `CHANGELOG.md`.

### Через сайт

1. Репозиторий → **Releases** → **Draft a new release**.
2. Choose a tag: `v1.0.0`.
3. Release title: `GoodsWarehouse 1.0.0`.
4. В описание вставьте содержимое раздела `[1.0.0]` из `CHANGELOG.md` (без строки заголовка).
5. Оставьте отмеченным **Set as the latest release** и нажмите **Publish release**.

### Через GitHub CLI

Вырежьте раздел версии в отдельный файл и создайте релиз:

```powershell
$text = Get-Content CHANGELOG.md -Raw -Encoding utf8
$notes = ($text -split "(?m)^## \[")[1] -replace "^[^\r\n]*\r?\n", ""
$notes = ($notes -split "(?m)^\[1\.0\.0\]:")[0].Trim()
Set-Content -Path release-notes.md -Value $notes -Encoding utf8
notepad release-notes.md
gh release create v1.0.0 --title "GoodsWarehouse 1.0.0" --notes-file release-notes.md
Remove-Item release-notes.md
```

В блокноте проверьте текст, сохраните и закройте его перед `gh release create`.

## 7. После релиза

### Тесты на GitHub

Вкладка **Actions** → workflow **tests**: последний запуск по `main` должен быть
зелёным. Он запускается при каждом push и pull request в `main` и выполняет
ruff, проверку миграций и pytest.

### Защита ветки `main`

Settings → **Branches** → **Add branch ruleset** (или **Add rule**) для `main`:

- **Require a pull request before merging** — изменения только через pull request;
- **Require status checks to pass** → выберите проверку `tests`;
- **Block force pushes** и запрет удаления ветки.

Если работаете один и пушите прямо в `main`, достаточно двух последних пунктов.

## 8. Лицензия — решение владельца

Без файла лицензии чужим людям нельзя использовать код без вашего разрешения,
даже в публичном репозитории. Варианты:

- **MIT** — любой может использовать и менять код, сохраняя упоминание автора.
  Подходит для открытого проекта.
- **Проприетарная** — «Все права защищены». Подходит для рабочей системы компании.

Как добавить: репозиторий → **Add file → Create new file** → имя `LICENSE`.

- MIT: появится кнопка **Choose a license template** → MIT License → укажите год
  и имя → **Review and submit** → закоммитьте в `main`.
- Проприетарная: впишите текст сами, например
  «© 2026 <владелец>. Все права защищены. Использование, копирование и
  распространение без письменного разрешения владельца запрещены.»

Затем выполните `git pull`, чтобы файл появился локально.

## 9. Следующие версии

1. Номер: исправления — `1.0.1`, новые возможности — `1.1.0`,
   несовместимые изменения — `2.0.0`.
2. Обновите номер в `config/__init__.py` (`__version__`), `pyproject.toml`
   (`version`) и строку «Версия» в `README.md`.
3. В `CHANGELOG.md` добавьте сверху раздел `## [1.1.0] — ГГГГ-ММ-ДД` и ссылку внизу файла.
4. Проверки из пункта 1, коммит, `git push`.
5. Тег и релиз, как в пунктах 5–6, с новым номером:

```powershell
git tag -a v1.1.0 -m "GoodsWarehouse 1.1.0"
git push origin v1.1.0
gh release create v1.1.0 --title "GoodsWarehouse 1.1.0" --notes-file release-notes.md
```
