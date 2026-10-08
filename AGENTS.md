# AGENTS.md

Personal long-term learning system. Portable core: **skills** (Agent Skills `SKILL.md`) + a stdlib-only Python **progress CLI**. Original pi extensions/agents are optional upgrades.

## Layout

- `skills/teach` — the teaching method (unconditional truths first; "how could I have discovered this?"; probe → plan → teach).
- `skills/study-plan` — build/revise a plan from a goal (backward design) and track progress.
- `skills/daily-lesson` — run one session: spaced-review warm-up → 1–2 lessons → wrap-up. Includes chat/Telegram mode.
- `skills/visualize` — minimal diagrams, with fallbacks for chat apps.
- `scripts/learn_graph.py` — knowledge-graph logic (tracks, junctions, day picking, text/md/mermaid/svg renderers), imported by `learn.py`.
- `scripts/learn.py` — state CLI (`learn today | done | card add | review grade | status | nudge | sync …`). Tests: `python3 scripts/test_learn.py`.
- `integrations/hermes` — installer + cron reminder for Hermes Agent/Telegram. `integrations/generic/notify.sh` — reminders for any setup.
- `extensions/`, `agents/` — pi-only extras (`quiz`, `ask-user-question`, `md-log`, visual makers, `researcher`).
- `docs/` (Russian) — knowledge graph, platforms, Hermes+Telegram guide, learning-science notes, sync.

## When asked to learn/teach something

1. No plan yet or "I want to learn X" → follow `skills/study-plan`.
2. Plan exists and learner wants to study / continue / answered a reminder → follow `skills/daily-lesson`.
3. Explaining anything → follow `skills/teach`.

User state lives in `$LEARN_HOME` (default `~/learning`), never in this repo. Run `learn` (or `python3 scripts/learn.py`) for every read/write of progress; don't hand-edit `progress.json`.

## Conventions for changes

- `learn.py` stays stdlib-only, Python ≥ 3.9; add a test for every behaviour change.
- Skills stay agent-neutral: tool names like `quiz`/`researcher` are optional, always give a text fallback. Keep frontmatter to `name` + `description`.
- Learner-facing docs are in Russian; skills (agent instructions) are in English.
