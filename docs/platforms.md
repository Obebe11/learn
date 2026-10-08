# Где можно учиться: среды и подключение

Система разделена на два слоя, поэтому работает практически с любым агентом:

| Слой | Что это | Зависит от среды? |
|---|---|---|
| **Методика** | `skills/` — открытый формат `SKILL.md` ([Agent Skills](https://agentskills.io/home)): `teach`, `study-plan`, `daily-lesson`, `visualize` | нет — один и тот же текст |
| **Память** | `scripts/learn.py` + каталог данных `~/learning` (JSON/Markdown) | нет — чистый Python 3.9+, без зависимостей |
| **Инструменты UI** | pi-расширения `quiz`, `ask-user-question`, `md-log`, визуальные субагенты | да — это необязательные «апгрейды»; без них скиллы переходят на текстовые аналоги |

Прогресс общий для всех сред, если каталог данных синхронизирован через git (`learn sync`, см. [data-and-sync.md](data-and-sync.md)) или лежит на одном сервере с MCP/API ([remote-server.md](remote-server.md)). То есть можно начать план в Telegram, а продолжить на ноутбуке в Claude Code.

## Сравнение

| Среда | С телефона | Ежедневный пуш | Как подключить скиллы | Чем хороша |
|---|---|---|---|---|
| **Hermes + Telegram** ⭐ | бот в Telegram, голос, фото | cron `--no-agent` (0 токенов) | `install.sh` / `skills.external_dirs` | лучший сценарий «в кармане»; [гайд](hermes-telegram.md) |
| **Claude Code** (CLI/десктоп/web) | приложение Claude: облачные сессии, Remote Control | Routines / `/schedule` в облаке, либо `integrations/generic/notify.sh` | `.claude/skills` (в репозитории уже есть) или плагин | сильная модель, поиск в вебе, работа с файлами |
| **Codex** (CLI / ChatGPT) | Codex в приложении ChatGPT | системный cron + `notify.sh` | `.agents/skills` (в репозитории уже есть) | удобно, если уже платите за ChatGPT |
| **OpenCode** | `opencode web` по Tailscale в браузере телефона | системный cron + `notify.sh` | читает `.claude/skills` и `.agents/skills` | открытый, любые модели |
| **OpenClaw** | Telegram / WhatsApp / Discord | свои cron-средства или `notify.sh` | каталог скиллов OpenClaw | если уже стоит у вас |
| **pi** (оригинал) | SSH + tmux (Termius, Blink) | `notify.sh` | репозиторий = `.pi` | максимум удобства на ПК: всплывающие `quiz`, схемы, `md-log` → Obsidian |
| **Свой MCP/API-сервер на VPS** | любой MCP-клиент (Claude, Codex, Cursor…), curl, «Команды» iOS | `integrations/generic/notify.sh` или cron `learn nudge` на сервере | скиллы отдаёт сам сервер (`learn_skill`), ставить ничего не надо | один источник правды для всех устройств; [гайд](remote-server.md) |
| **Любой чат** (ChatGPT, Claude.ai, Gemini) | приложение | напоминание в календаре | вставить [prompts/universal-tutor.md](../prompts/universal-tutor.md) | ничего не ставить; прогресс — «карточка» вручную |

Telegram-боты с rich-сообщениями (Bot API 10.1+) показывают LaTeX, таблицы, чек-листы и сворачиваемые подсказки — см. [раздел 5 гайда по Hermes](hermes-telegram.md#5-rich-сообщения-формулы-таблицы-чек-листы-подсказки). Режим переключается `learn config chat_format plain|rich`.

⭐ — рекомендую для ежедневных уроков с телефона. Для глубоких сессий за компьютером — Claude Code или pi.

## Что проверено, а что нет

Я читал документацию Hermes (скиллы, cron, Telegram) и запускал `learn` и скрипты установки, но **не** запускал сами агенты. Для остальных сред опираюсь на публичные описания; перед настройкой сверьтесь с актуальной документацией:

- **Hermes** — скиллы `SKILL.md`, `skills.external_dirs`, cron `--no-agent --script`, доставка `--deliver telegram`, `/skill-name` — из официальной документации.
- **OpenCode** — официальная страница [Skills](https://opencode.ai/docs/skills/): ищет `.claude/skills/<name>/SKILL.md` и `.agents/skills/<name>/SKILL.md`.
- **Claude Code** — [мобильный доступ](https://code.claude.com/docs/en/mobile) и [Remote Control](https://code.claude.com/docs/en/remote-control): сессия идёт на вашей машине, телефон — окно в неё (машина должна быть включена); облачные сессии работают без вашего компьютера. Синтаксис плагинов/маркетплейса (`/plugin marketplace add`, `/plugin install`) сверьте командой `/plugin`.
- **Codex** — по публичным описаниям: интерфейс Codex в мобильном ChatGPT подключается к вашему компьютеру или удалённой машине. Путь `.agents/skills` указан в сторонних источниках — проверьте в документации Codex.
- **OpenClaw** — шлюз для Telegram/WhatsApp. Прочитайте его актуальные security-advisories перед развёртыванием: агент с доступом к shell, доступный из мессенджера, надо закрывать списком разрешённых пользователей.

Общее правило безопасности: любой чат-бот с доступом к терминалу — ограничьте списком разрешённых пользователей, а репозиторий с вашим прогрессом держите **приватным**.

## Подключение по средам

В репозитории лежат симлинки `.claude/skills → ../skills` и `.agents/skills → ../skills`: если открыть клон в Claude Code / Codex / OpenCode, скиллы подхватятся автоматически (на Windows симлинки могут не работать — тогда скопируйте папки `skills/*`).

### Общее для всех: команда `learn`

```bash
git clone https://github.com/obebe11/learn ~/learn
ln -sf ~/learn/scripts/learn.py ~/.local/bin/learn      # убедитесь, что ~/.local/bin в PATH
learn init --language ru --timezone Europe/Moscow
```

Если `learn` не в PATH, скиллы подсказывают агенту вызывать `python3 ~/learn/scripts/learn.py`.

### Claude Code

- **Скиллы глобально:** `cp -R ~/learn/skills/* ~/.claude/skills/` (или откройте проект `~/learn` — подхватятся из `.claude/skills`).
- **Плагином:** в репозитории есть `.claude-plugin/` — `/plugin marketplace add obebe11/learn`, затем `/plugin install learn@learn`. Скиллы получат префикс плагина (например `/learn:daily-lesson`).
- **С телефона:** `claude remote-control` на компьютере → сессия в приложении Claude; либо облачные сессии на claude.ai/code (репозиторий с данными подключите как второй источник, прогресс сохраняйте через `learn sync` / коммит).
- **Ежедневный запуск:** расписание Claude Code (Routines) с промптом «Run /daily-lesson», либо просто напоминание `notify.sh`.

### Codex, OpenCode, OpenClaw

Положите (или оставьте симлинками) папки из `skills/` в каталог скиллов агента (`.agents/skills/` для Codex/OpenCode; у OpenClaw — его каталог скиллов). Запросы те же: «составь план изучения …», «daily lesson».

OpenCode с телефона: `opencode web` на домашнем сервере и доступ **только** через VPN/Tailscale с паролем; не открывайте порт в интернет.

### pi (оригинальная установка)

Репозиторий и есть каталог `.pi`: `git clone https://github.com/obebe11/learn .pi` в корне вашего учебного проекта. Расширения (`quiz`, `ask-user-question`, `md-log`, `visual-tools`) и агенты (`researcher`, `svg-maker`, `mermaid-maker`) работают как раньше; новые скиллы `study-plan` и `daily-lesson` используют те же инструменты, когда они есть. Подробности в корневом [README](../README.md).

### Напоминания без агента (для любой среды)

`integrations/generic/notify.sh` вызывает `learn nudge` и отправляет текст в Telegram (Bot API) или в push-приложение [ntfy](https://ntfy.sh). Молчит, если делать нечего:

```cron
0 9 * * 1-6  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... /home/me/learn/integrations/generic/notify.sh
```

### Только чат, без установки

Откройте [prompts/universal-tutor.md](../prompts/universal-tutor.md), вставьте как инструкции проекта (Claude.ai Project, ChatGPT Project, Gemini Gem). В конце занятия бот выдаёт «карточку прогресса» — сохраните её и вставьте в начале следующего занятия. Без CLI нет автоматического расписания повторений: вместо этого бот сам планирует их по карточке.
