# AGENTS.md

Personal long-term learning system. Portable core: **skills** (Agent Skills `SKILL.md`) + a stdlib-only Python **progress CLI**. Original pi extensions/agents are optional upgrades.

## Layout

- `skills/teach` — the teaching method (unconditional truths first; "how could I have discovered this?"; probe → plan → teach).
- `skills/study-plan` — build/revise a plan from a goal (backward design) and track progress.
- `skills/daily-lesson` — run one session: spaced-review warm-up → 1–2 lessons → wrap-up. Includes chat/Telegram mode.
- `skills/visualize` — minimal diagrams, with fallbacks for chat apps.
- `scripts/learn_graph.py` — knowledge-graph logic (tracks, junctions, day picking, text/md/mermaid/svg renderers), imported by `learn.py`.
- `scripts/learn.py` — state CLI (`learn today | done | card add | review grade | status | nudge | sync …`). Tests: `python3 scripts/test_learn.py`.
- `scripts/learn_server.py` — MCP (Streamable HTTP + stdio) and REST server on top of `learn.py`; every tool is a validated wrapper that runs the CLI. Tests: `python3 scripts/test_learn_server.py`.
- `integrations/hermes` — installer + cron reminder for Hermes Agent/Telegram. `integrations/generic/notify.sh` — reminders for any setup. `integrations/server` — systemd unit + Caddyfile for `learn_server.py`.
- `extensions/`, `agents/` — pi-only extras (`quiz`, `ask-user-question`, `md-log`, visual makers, `researcher`).
- `docs/` (Russian) — knowledge graph, platforms, Hermes+Telegram guide, remote MCP/API server on a VPS, learning-science notes, sync.

## When asked to learn/teach something

1. No plan yet or "I want to learn X" → follow `skills/study-plan`.
2. Plan exists and learner wants to study / continue / answered a reminder → follow `skills/daily-lesson`.
3. Explaining anything → follow `skills/teach`.

User state lives in `$LEARN_HOME` (default `~/learning`), never in this repo. Run `learn` (or `python3 scripts/learn.py`) for every read/write of progress; don't hand-edit `progress.json`.

If this session has `learn_*` MCP tools instead of a shell (a remote `learn_server.py`), they are the same commands: `learn <cmd>` → `learn_<cmd>`, skip `learn sync`, and call `learn_skill` to get the skill text.

## Conventions for changes

- `learn.py` and `learn_server.py` stay stdlib-only, Python ≥ 3.9; add a test for every behaviour change.
- New `learn` command or option → expose it in `learn_server.py` (or consciously don't: `sync`, `init`) and extend `test_every_tool_passes_the_cli_parser_with_all_of_its_arguments`. The server is a wrapper: rules live in `learn.py`, never duplicated in the server.
- Anything that reads stdin must do it before the home lock is taken (see `main()`): a stalled client must not block every other writer.
- Skills stay agent-neutral: tool names like `quiz`/`researcher` are optional, always give a text fallback. Keep frontmatter to `name` + `description`.
- Learner-facing docs are in Russian; skills (agent instructions) are in English.
