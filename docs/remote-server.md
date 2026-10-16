# Учиться где угодно: MCP/API-сервер на VPS

Один сервер на VPS хранит **весь** прогресс. Телефон, ноутбук, чат-бот — все ходят в него по HTTPS и видят одно и то же состояние сразу: ничего не нужно синхронизировать и сливать. Это вариант 2 из [issue #5](https://github.com/Obebe11/learn/issues/5).

```
 Claude (телефон/веб/Code)  ─┐
 Codex, Cursor, любой MCP    ─┼─ HTTPS ─▶ Caddy ─▶ learn_server.py ─▶ learn.py ─▶ ~/learning
 curl / Shortcuts / скрипт   ─┘          (TLS)      127.0.0.1:8787     (правила)    (JSON-файлы)
```

`learn_server.py` — тонкая обёртка: каждый инструмент проверяет аргументы и запускает `learn.py`. Правила (расписание, повторения, граф) остаются в одном месте, а CLI по-прежнему работает на том же каталоге — например, cron-напоминание Hermes на этом же VPS. Одновременные записи из сервера, Hermes и вашего shell безопасны: каждая команда `learn` берёт блокировку каталога данных.

Что умеет сервер:

- **MCP** (Streamable HTTP, `POST /mcp`) — 24 инструмента, `instructions` и 4 промпта (скиллы `daily-lesson`, `study-plan`, `teach`, `visualize`);
- **REST** (`/api/tools/<имя>`) — те же инструменты для curl, скриптов и «Команд» iOS;
- **stdio** (`--stdio`) — MCP без открытого порта: клиент запускает сервер через `ssh`.

Только стандартная библиотека Python 3.9+, как и `learn.py`.

## 1. Установка на VPS

Нужны Python 3.9+ и git. Всё ниже — от обычного пользователя (root нужен только для Caddy и пакета для картинок).

```bash
git clone https://github.com/obebe11/learn ~/learn
ln -sf ~/learn/scripts/learn.py ~/.local/bin/learn
sudo apt install librsvg2-bin                            # rsvg-convert: карта прогресса картинкой (PNG)
learn init --language ru --timezone Europe/Moscow        # каталог данных ~/learning (если его ещё нет)

# секретный токен — единственный ключ к вашим данным
mkdir -p ~/.config/learn-server ~/.config/systemd/user
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/learn-server/token
chmod 600 ~/.config/learn-server/token

# запуск как служба
cp ~/learn/integrations/server/learn-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now learn-server
loginctl enable-linger "$USER"                           # не останавливать службу после выхода
curl http://127.0.0.1:8787/health                        # {"ok": true, ...}
```

Если у вас уже есть данные на другом устройстве, перенесите каталог (`rsync -a ~/learning/ vps:learning/` или `git clone` приватного репозитория из [data-and-sync.md](data-and-sync.md)) **до** первого занятия и дальше работайте только через сервер.

Сервер слушает только `127.0.0.1`. Наружу его выставляет обратный прокси с TLS — без HTTPS токен летел бы открытым текстом. С [Caddy](https://caddyserver.com/docs/install) это четыре строки (сертификат Caddy получает и продлевает сам; домен должен указывать на VPS, порты 80/443 открыты):

```
learn.example.com {
	reverse_proxy 127.0.0.1:8787
}
```

(готовый файл: [integrations/server/Caddyfile](../integrations/server/Caddyfile)). Проверка снаружи:

```bash
TOKEN=$(cat ~/.config/learn-server/token)
curl -H "Authorization: Bearer $TOKEN" "https://learn.example.com/api/tools/learn_status?raw=1"
```

## 2. Подключение клиентов

Токен можно передать двумя способами:

| Способ | Как | Когда |
|---|---|---|
| заголовок | `Authorization: Bearer <токен>` | везде, где клиент позволяет задать заголовок — **предпочтительно** |
| в адресе | `https://learn.example.com/t/<токен>/mcp` | клиент не умеет заголовки (например, форма «добавить коннектор» только с URL) |

Токен в адресе слабее: он попадает в историю клиента и в логи любых прокси на пути. Сам `learn_server.py` его в лог не пишет (в журнале только путь без токена); в Caddy не включайте директиву `log`.

### Claude Code

```bash
claude mcp add --transport http learn https://learn.example.com/mcp --header "Authorization: Bearer <токен>"
```

(точный синтаксис сверьте с `claude mcp add --help`). Дальше достаточно написать «давай урок» или «хочу выучить …»: сервер сам сообщает модели в `instructions`, что перед занятием нужно вызвать `learn_skill`. Скилл-тексты отдаются прямо с сервера — устанавливать `skills/` на устройство не нужно.

### Claude (приложение / веб), Codex, Cursor и другие MCP-клиенты

Добавьте удалённый (Streamable HTTP) MCP-сервер с адресом `https://learn.example.com/mcp` и заголовком выше, а если заголовок задать нельзя — с адресом `https://learn.example.com/t/<токен>/mcp`. Сервер **не реализует OAuth**: клиент, который требует OAuth-вход, к нему так не подключится — берите вариант с `ssh` ниже или REST.

### Без открытого порта: MCP через ssh

Если с этого устройства есть ssh на VPS, сервер можно вообще не выставлять в интернет:

```bash
claude mcp add learn -- ssh vps python3 '~/learn/scripts/learn_server.py' --stdio
```

Кавычки нужны, чтобы `~` раскрылся на VPS, а не на вашем устройстве. Токен не нужен — защищает ssh-ключ; каталог данных по умолчанию `~/learning` (другой — флагом `--home`).

### REST: curl, скрипты, «Команды» iOS, закладка

```bash
H="Authorization: Bearer $TOKEN"; API=https://learn.example.com/api/tools
curl -H "$H" "$API/learn_today?raw=1"                                   # читать: GET, ответ текстом
curl -H "$H" -d '{"lesson":"L07","score":0.8}' $API/learn_done          # писать: POST + JSON
curl -H "$H" -d '{"json":true}' $API/learn_status                       # {"ok":true,"output":"…","data":{…}}
curl -H "$H" $API                                                       # список инструментов со схемами аргументов
```

Карта прогресса картинкой — прямо в браузере телефона (PNG; `&format=svg` — векторная, `&scale=3` — ещё чётче):

```bash
curl -H "$H" -o map.png "$API/learn_graph_image?raw=1"
# закладка: https://learn.example.com/t/<токен>/api/tools/learn_graph_image?raw=1
```

GET разрешён только для инструментов, которые ничего не меняют, поэтому закладку в браузере телефона `https://learn.example.com/t/<токен>/api/tools/learn_status?raw=1` можно безопасно открывать сколько угодно раз. Коды ответов: `400` — неверные аргументы, `401` — токен, `404` — нет такого инструмента, `422` — `learn` отказал (например, «no such lesson»).

### Hermes + Telegram на том же VPS

Ничего менять не нужно: Hermes продолжает вызывать `learn` с тем же `LEARN_HOME`. Сервер лишь добавляет второй вход в те же данные.

## 3. Инструменты

Каждая команда `learn …` — это инструмент `learn_…`; `json: true` у читающих инструментов возвращает JSON вместо текста. Инструменты помечены `readOnlyHint`, чтобы клиенты не спрашивали подтверждения на чтение.

| Группа | Инструменты |
|---|---|
| Занятие | `learn_today`, `learn_next`, `learn_done`, `learn_skip`, `learn_graph` |
| Повторения | `learn_card_add`, `learn_card_retire`, `learn_review_due`, `learn_review_grade` |
| Прогресс | `learn_status`, `learn_graph_image`, `learn_plans`, `learn_week`, `learn_nudge` |
| Планы | `learn_create` (план — JSON-объект в аргументе; `update: true` сохраняет прогресс), `learn_show`, `learn_validate`, `learn_start`, `learn_set_plan_status` |
| Профиль и настройки | `learn_profile_get`, `learn_profile_set`, `learn_config_get`, `learn_config_set` |
| Методика | `learn_skill` (без имени — список; `daily-lesson`, `study-plan`, `teach`, `visualize`) |

**Карта картинкой — `learn_graph_image`.** Спросите «как мой прогресс?» или «покажи карту» — модель вызовет его (сервер сам подсказывает это в `instructions`) и получит PNG прямо в ответе инструмента (MCP-блок `image`), а копия ляжет на сервер в `~/learning/plans/<план>/graph.png`. На картинке: колонка на каждую ветку с полоской прогресса, залитый круг — пройдено, круг с подсветкой — готово сейчас, оранжевый — пересдать, бледный — закрыто, ромб — проект (точка соединения), золотое кольцо — уроки на сегодня; все ветки внизу сходятся в одну точку — цель плана с общим процентом; в шапке — общий прогресс и что учить сегодня. Аргументы: `scale` 1–4 (по умолчанию 2 — резко на телефоне; PNG ≈ 1,2 МБ на 90 уроков, `scale: 1` — примерно вдвое легче), `level: units` — одна точка на ветку, `format: svg` — векторный файл (если на сервере нет `rsvg-convert`, PNG не получится — инструмент так и скажет). Покажет ли клиент картинку вам, а не только модели, зависит от клиента; надёжный путь на телефоне — закладка из раздела REST.

Нет `learn_sync`: источник истины один, сливать нечего. Нет `learn init`: каталог создаётся один раз при установке. Каталог данных клиент выбрать не может — он задаётся при запуске сервера.

## 4. Безопасность

- **Токен** ≥ 24 символов, генерируйте `secrets.token_urlsafe(32)`; сервер не стартует со слабым. Он даёт полный доступ к учебным данным (но не к другим файлам и не к shell: инструменты — фиксированный набор команд `learn`).
- **Сменить токен:** записать новый в `~/.config/learn-server/token`, `systemctl --user restart learn-server`, обновить клиентов.
- Проверка токена — за постоянное время; запросы из браузеров (с заголовком `Origin`) отклоняются, пока вы не разрешили источник флагом `--allow-origin`; тело запроса ≤ 1 МиБ; значения аргументов проверяются по схеме и не могут превратиться в флаги или пути (slug плана без `/`, `..`, ведущих `.` и `-`).
- Если открываете сервер в интернет, добавьте лимит на неудачные попытки (fail2ban по журналу Caddy или правило файрвола) — встроенного ограничителя нет; поэтому и нужен длинный случайный токен.
- Резервная копия: каталог данных — обычные файлы. Сделайте его приватным git-репозиторием ([data-and-sync.md](data-and-sync.md)) и добавьте в cron `learn sync` (например, `0 3 * * *`): это бэкап, а не синхронизация между устройствами. Пока идёт `sync`, запросы к серверу ждут блокировки — несколько секунд.
- Обновление: `cd ~/learn && git pull && systemctl --user restart learn-server`.

## 5. Что проверено, а что нет

Проверено: 35 автотестов сервера (`python3 scripts/test_learn_server.py`: протокол, авторизация, проверка аргументов, параллельные записи, REST, stdio) и 65 тестов `learn.py`; живой сервер подключался официальным Python-клиентом `mcp` (v2.3.0) — по HTTP с токеном в URL и в заголовке, неверный токен отклонялся, по stdio; полный сценарий (план → урок → карточки → повторение) прошёл. Файл systemd-службы прошёл `systemd-analyze verify`.

Не проверено: запуск под настоящим systemd и Caddy на VPS, подключение через интерфейс приложений Claude/Codex/Cursor (в них формы коннекторов и требования к авторизации меняются — сверяйтесь с их документацией), и то, как конкретная модель ведёт занятие через `learn_skill`. Если что-то не подключается, начните с `curl https://…/health` и `journalctl --user -u learn-server`.
