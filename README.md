# learn

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

Система долгосрочного обучения: **учебные планы → по паре уроков в день → интервальные повторения → прогресс**. Работает в Telegram через [Hermes Agent](https://github.com/NousResearch/hermes-agent), в Claude Code, Codex, OpenCode, pi и в любом обычном чате. Учиться можно с телефона.

Основана на системе из видео [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU) (методика `teach` — оттуда). Всё остальное — переносимое ядро: скиллы + маленький CLI для прогресса.

## Как это выглядит

```
09:00  📚 Время учиться!
       Сегодня: 2 урока + 3 повторения · серия 5 🔥
       • L07 · Циклы for
       • L08 · Списки
       Напиши /daily-lesson, чтобы начать.

Вы:    /daily-lesson
Бот:   🔁 Разминка (без подсказок): что делает `return`?  … 
       ▶ Урок L07 … небольшие шаги, проверка после каждой идеи … мини-тест A–D
       ✅ L07 готов. ████░░░░░░ 7/40 (18%) · по графику · финиш ≈ 1 дек
```

## Что внутри

| | |
|---|---|
| `skills/study-plan` | **Новое.** Составляет план от цели: диагностика уровня, поиск программы в интернете, измеримые результаты, модули, майлстоуны, прогноз даты окончания. Ведёт прогресс, недельные разборы, пересмотр плана |
| `skills/daily-lesson` | **Новое.** Ежедневное занятие: разминка (повторения по памяти) → 1–2 урока → тест → объяснение своими словами → запись прогресса. Режим для мессенджеров: короткие сообщения, квизы A–D; с включёнными Telegram rich-сообщениями — LaTeX, таблицы, чек-листы, подсказки в `<details>`, карточки со спойлерами |
| `skills/teach` | Методика: безусловные истины сначала; «как я мог бы это открыть сам?»; probe → plan → teach. Теперь с запасными вариантами без pi-инструментов |
| `skills/visualize` | Минимальные верные схемы (с запасными вариантами для чатов) |
| `scripts/learn.py` | **Новое.** CLI прогресса без зависимостей: планы, уроки, карточки повторения (коробки Лейтнера), серия, прогноз, напоминание, `sync` через git |
| `integrations/hermes/` | **Новое.** Установка под Hermes + cron-напоминание без LLM (0 токенов) |
| `integrations/generic/notify.sh` | **Новое.** Напоминание в Telegram/ntfy из обычного cron для любой среды |
| `prompts/universal-tutor.md` | **Новое.** Один промпт для любого чата без установки (прогресс — «карточкой») |
| `extensions/`, `agents/` | Оригинал для pi: `quiz`, `ask-user-question`, `md-log` (→ Obsidian), `visual-tools`, субагенты |
| `docs/` | Руководства (по-русски) |

## Быстрый старт

### Вариант 1 — Hermes + Telegram (рекомендую для телефона)

```bash
git clone https://github.com/obebe11/learn ~/learn
~/learn/integrations/hermes/install.sh --language ru --timezone Europe/Moscow
hermes cron create "0 9 * * 1-6" --no-agent --script learn-nudge.sh --deliver telegram --name learn-nudge
```

Затем напишите боту: «Хочу выучить … по два урока в день». Подробно: **[docs/hermes-telegram.md](docs/hermes-telegram.md)**.

### Вариант 2 — Claude Code / Codex / OpenCode

```bash
git clone https://github.com/obebe11/learn ~/learn && cd ~/learn
ln -sf ~/learn/scripts/learn.py ~/.local/bin/learn && learn init --language ru
```

Откройте папку в агенте — скиллы подхватятся из `.claude/skills` / `.agents/skills` (симлинки в репозитории). Для Claude Code есть и плагин (`/plugin marketplace add obebe11/learn`). С телефона — приложение Claude (Remote Control / облачные сессии) или Codex в ChatGPT. Подробно: **[docs/platforms.md](docs/platforms.md)**.

### Вариант 3 — pi (как в оригинале)

```bash
git clone https://github.com/obebe11/learn .pi     # из корня вашего учебного проекта
```

Остальное (расширения, субагенты, Obsidian) — как раньше, см. раздел «pi» ниже.

### Вариант 4 — просто чат

Вставьте [prompts/universal-tutor.md](prompts/universal-tutor.md) в инструкции проекта ChatGPT / Claude.ai / Gemini.

## Где живёт прогресс

В `~/learning` (не в этом репозитории): `plans/<slug>/plan.json`, `progress.json` и читаемый `PLAN.md` с чекбоксами. Чтобы учиться с разных устройств, сделайте этот каталог **приватным** git-репозиторием и вызывайте `learn sync` (скиллы делают это сами в начале и конце занятия). См. [docs/data-and-sync.md](docs/data-and-sync.md).

## Команда `learn`

```text
learn init --language ru --timezone Europe/Moscow    # один раз (--chat-format rich для Telegram rich-сообщений)
learn config chat_format rich|plain                  # формат ответов в мессенджере
learn create <slug> --file plan.json                 # создать/обновить план (--update)
learn today                                          # уроки на сегодня + повторения
learn done L07 --score 0.8 --note "путает X и Y"     # записать урок (<70% → повторить)
learn card add --lesson L07 --stdin                  # карточки: [{"q": "...", "a": "..."}]
learn review grade <plan>/C012 pass|fail             # результат повторения
learn status | week | plans                          # прогресс, темп, прогноз окончания
learn pause|resume|archive <slug>
learn nudge                                          # текст напоминания (пусто = нечего слать)
learn sync                                           # git: commit + pull --rebase + push
```

Все команды понимают `--json`. Тесты: `python3 scripts/test_learn.py`.

## Принципы плана

Подробно с источниками и оговорками: [docs/learning-science.md](docs/learning-science.md). Коротко: цели → доказательства → уроки (backward design); каждый день начинается с вспоминания, а не перечитывания; повторения через 1/3/7/14/30/60/120 дней; отставание сдвигает дату окончания, а не удваивает уроки; подробно планируем только ближайшие ~6–8 недель.

## Что проверено

`learn.py`, `install.sh`, `notify.sh` и пример плана из скилла запущены и покрыты тестами. Сами агенты (Hermes, Claude Code и др.) в этой работе не запускались: форматы и команды Hermes взяты из его документации, остальные среды описаны по публичным источникам — см. раздел «Что проверено, а что нет» в [docs/platforms.md](docs/platforms.md).

---

## pi (оригинальная установка)

Этот репозиторий **и есть** каталог `.pi`. Из корня учебного проекта:

```bash
git clone https://github.com/obebe11/learn .pi
```

Требуется:

- [pi](https://github.com/earendil-works/pi)
- Реализация субагентов (для `researcher` и визуальных мейкеров). Рекомендуется [pi-interactive-subagents](https://github.com/amosblomqvist/pi-interactive-subagents) (только tmux). Без неё главная сессия сама преподаёт — теряются проверка фактов и сгенерированные схемы. В `agents/researcher.md` указан `safe_bash` — он специфичен для этого расширения.
- `ask-user-question` — используйте версию из репозитория: всплывающие окна разных расширений сериализуются через общую блокировку, которая работает только при одной реализации.

Расширения: `ask-user-question` (вопросы окном), `quiz` (оцениваемые вопросы с мгновенной обратной связью), `md-log` (зеркалирование сессии в markdown для Obsidian), `visual-tools` (инструменты для визуальных субагентов). Методика `teach` написана под одного ученика (автора видео) — отредактируйте под себя.
