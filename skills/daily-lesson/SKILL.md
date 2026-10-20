---
name: daily-lesson
description: Run today's learning session from the learner's study plan — spaced-repetition warm-up, one or two lessons taught with the `teach` method, a graded check, and progress recording via the `learn` CLI. Use when the learner says "daily lesson", "/daily-lesson", "let's study", "today's lesson", "what's next", answers a daily reminder, or any time a study plan exists and they want to continue learning. Designed for short mobile chat sessions (Telegram) as well as terminals.
---

# Daily lesson

One session = **warm-up reviews → 1–2 lessons → wrap-up**, about 40–50 minutes at the default pace, and it must be fine to do on a phone, in pieces, hours apart. The plan decides *what*; `teach` decides *how*; the `learn` CLI is the memory — nothing counts until it is recorded.

## 0. Start

1. `learn sync` (ignore "not a git repo"; if it reports a conflict, tell the learner and continue read-only). *With `learn_*` MCP tools instead of a shell (remote server): skip this — there is one copy of the data; every `learn <cmd>` below is the tool `learn_<cmd>`, and `profile.md`/`config.json` are `learn_profile_get`/`learn_config_get`.*
2. Read `<data dir>/profile.md` and `config.json` → lesson language, pace preferences. Speak that language.
3. `learn today` (add `--plan <slug>` if several plans and the learner named one). It lists today's lessons, a read-only preview of remaining warm-up reviews, streak, pace and each plan's `speed` (average completed lessons/day divided by the needed average). Use `learn review next` to issue each actual question.
   - No plan yet → hand over to `study-plan`.
   - Rest day / quota done → only offer reviews; offer one bonus lesson (`learn next`) *only* if asked.
4. Open in two short lines before the warm-up: streak and today's plan (e.g. "2 lessons + 4 reviews, ≈ 45 min"), then each selected plan's speed with actual and needed lessons/day (e.g. "Speed 0.70× · 1.00 lessons/day, needed 1.43"). Use the returned `speed` values; 1.00× means on schedule, below 1 means slower. If `speed` is null or its ratio is null, say "Speed: not enough data yet"; do not invent a ratio. If they've been away 4+ days: no guilt, no catch-up speech — "welcome back, we start light".
5. Resume an unfinished lesson with a 2-sentence recap, using its saved note and the current conversation to continue at the unfinished step. Do not restart its opening questions or the warm-up from chat history.

### Graph plans (several tracks): jumping between topics on purpose

If `learn today` says **graph plan**, the plan is a map of parallel *tracks* (Python, AI, …) joined by ⭐ *junctions* — lessons that need several skills at once.

- Today's lessons come from **different tracks** on purpose. Teach them in the order listed; between topics give a one-line bridge ("switching gears: from Python to AI — notice how X from before shows up again"). Mixing topics is not a distraction: the repeated "what was this again?" effort is part of why it sticks. Say this once, briefly, the first time.
- **After every `learn done`, run `learn today --max-reviews 0` again**: finishing a lesson can unlock new ones (including a junction). Continue lessons; do not start another warm-up.
- If a lesson ends as **retry**, don't teach lessons that depend on it today (they're locked until it passes).
- **The learner can steer.** "Today I only want AI" → `learn today --track AI` (track = unit id as shown by `learn graph`). "What can I study?" / "show the map" → `learn graph` (chat: text in a code block; `chat_format rich`: `--format md`; if the gateway can send images and `rsvg-convert` exists: `--format png` and send the path, e.g. Hermes `MEDIA:<path>`; remote server: `learn_graph_image` returns the picture itself). Any *ready* lesson may be taken out of order: `learn done <id>`; a locked one needs its prerequisites first (`--force` only if they clearly already know them).
- **Junction lessons (⭐)** are the payoff. Start with a short recall of the prerequisite lessons it lists (use their review cards), then give a real task that needs *both* skills; grade it against its `check`. When one unlocks, say so — "you now have both skills needed for J1".
- **End of day**: show the map in one glance (`learn graph`), pointing out what just unlocked.

## 1. Warm-up: retrieval before anything new (≈ 5 min)

Use the server's daily budget (default 6 across all chats/plans), not six more cards at every lesson start:

1. Call `learn review next` for ONE question immediately before asking it. It saves the presentation separately from recall; `learn today` / `learn review due` are only previews. Never prefetch several questions. A null `card` means stop the warm-up and teach the lesson, even if a backlog remains. Increase `--max-reviews` only if the learner asks for extra reviews.
2. Ask the returned question **closed-book** — never show its stored answer first. One card per message, then wait. Where possible use a fresh equivalent example testing the same fact, with the same correct reasoning; do not change what the card tests or invent new card IDs for wording variants.
3. Judge the first unaided reply: lenient on wording, strict on substance. Assign SM-2 `quality`: 5 = complete and confident; 4 = correct with minor hesitation; 3 = correct with substantial difficulty; 2 = incorrect but recognised after explanation; 1 = incorrect, partial recognition; 0 = no recall. Do not infer difficulty from chat response time. Missing a material part or needing an answer-revealing hint means 0..2, not a pass.
4. **Record immediately, before another question or lesson**: `learn review grade <plan>/<card> pass|fail --quality <0..5>` (3..5 = pass, 0..2 = fail). Check success; only then say it was saved. `pass|fail` without quality remains compatible (4/1). The scheduler calculates each card's next due date from its own interval, successful-recall streak and ease factor; never set dates from chat history.
5. Reveal the answer in one line; if wrong, briefly repair the gap. A corrected answer after seeing the solution is practice, not another scheduled grade. Do not loop a failed question on every lesson start; its next scheduled review is tomorrow. Repeat from step 1 until `card` is null or the learner wants to move on.

An issued but unanswered question stays ungraded and keeps its due date; it is suppressed for the rest of that day and rotates back later. If the learner returns to answer the pending question, grade that card before issuing another. A new chat or `learn today` call must not reset the daily budget. Do not pull future cards or use `review due --all` to fill an empty warm-up.

Why first: pulling answers out of memory *before* re-exposure is what builds retention; it also tells you what the learner still holds before you build on it.

## 2. Lessons (for each lesson in today's list)

Use the `teach` skill's **Phase 3 loop** (motivate → establish → connect → quiz-check) over the lesson's `nodes`. Phases 1–2 (probe, plan) were done when the plan was made — don't repeat them, but if the warm-up exposes a shaky prerequisite, repair it first (a few minutes) rather than building on sand. Accuracy rules of `teach` still apply: verify anything you're not certain of (web search / `researcher`) before stating it.

**Shape (≈ 20–25 min):**

1. **Why** (1–2 messages): the problem this lesson solves; tie to the previous lesson and the learner's goal.
2. **Build** in small steps — *one idea per message*, each motivated ("how could you have discovered this?"). Socratic where the learner can plausibly reason it out; narrate otherwise. Stop to check understanding after each key idea, not only at the end.
3. **Check** (3–5 questions, graded): match the lesson's `check` and the verb in its `objective`. Include at least one question from an *earlier* lesson (interleaving). Questions need a right answer; follow `teach`'s quiz-option construction rules (bare claims, parallel wording, plausible distractors, no justification inside options).
4. **Score** = correct ÷ asked. Record immediately: `learn done <L-id> --score 0.8 --note "confused X with Y"`.
   - Below the pass bar → the CLI marks `retry`: spend a few minutes repairing the exact gap, but **don't loop all day** — tomorrow's session re-teaches it first.
   - Use `--accept` only if the learner clearly has it and the quiz was flawed.
5. **Make review cards** (2–4 per lesson), atomic and *why/how*-oriented, answerable in a sentence — not trivia: `learn card add --lesson <L-id> --stdin` with `[{"q":"…","a":"…"}]`. Cards are what keep today's lesson alive in month three.

Milestone lessons (`kind: milestone`): the learner produces the artifact or performance named in the unit's pass bar; judge it against that bar and say specifically what met it and what didn't. `review` lessons: mixed, cumulative questions across the unit, no new material.

## 3. Wrap-up (≈ 3 min)

1. **Teach-back**: "Explain today's key idea in two sentences, as if to a friend." (Producing the explanation consolidates it; correct gently.)
2. `learn status` → report in 2 lines: progress bar, pace and speed, finish date. Celebrate real things (a hard idea that clicked, a streak) — truthfully.
3. Preview tomorrow in one line, so the next start is easy.
4. `learn sync`.

## Mobile / chat mode (Telegram, WhatsApp, any messaging gateway)

Apply whenever the learner talks to you through a chat app (and by default if unsure):

- **Short messages**: aim under ~1,000 characters; split long explanations into several messages. One question per message, then **wait**.
- **Quizzes as text** — no popup tools needed:
  ```
  ❓ 2/4
  <question>
  A) …
  B) …
  C) …
  D) …
  (reply with a letter, or in your own words)
  ```
  Never reveal the answer before they reply. After the reply: ✅/❌, the right option, a 1–2 sentence explanation, then the next question. If the environment offers answer buttons (Hermes `clarify`, a `quiz`/`ask_user_question` tool), use them — but the same no-leak rule holds.
- **Check the format first.** `learn today` prints `chat_format plain|rich` (set with `learn config chat_format rich`).
  - **plain** (default): no LaTeX, no Mermaid, no tables — they show as raw symbols. Write math in plain text/Unicode (`x² + 3x = 0`, `√2`, `a/b`), keep code in backticks or fenced blocks. Diagrams: a tiny ASCII/Unicode sketch in a code block (≤ 7 nodes), or an image if the environment can send one (see `visualize`).
  - **rich** (Telegram rich messages, Bot API 10.1+; Hermes `rich_messages: true`): LaTeX (`$…$`, `$$…$$`), tables, task lists, collapsible `<details>` hints, tap-to-reveal `||spoilers||`, quotes. Read `references/telegram-rich.md` for the exact syntax and the visual patterns that replace Mermaid (still no Mermaid). Keep messages short anyway.
- **Voice**: they may answer by voice (it arrives transcribed). Treat transcription slips generously.
- **Interruptions are normal**: they may reply an hour later or run `/new`. Don't rely on chat history: everything durable is in the CLI (`done`, `card add`, `grade`). Resume from `learn today`.
- **Time-box** if they say they're short on time: warm-up + one lesson is a full day; record it honestly.
- **Don't nag.** One gentle line is enough if they stall.

## Terminal / desktop (pi, Claude Code, Codex, OpenCode)

Same flow. Math may use LaTeX (`$…$`) if the viewer renders it (Obsidian via `md-log`, Claude app). Richer tools (`quiz`, `ask_user_question`, `visualize` makers, `researcher`) are optional upgrades — use them when present, fall back to the text forms above when not.

## Principles to keep

- Retrieval over re-reading; spacing over cramming; understanding over memorising (the `teach` skill).
- Honest scores — an inflated score today is a hidden gap next month.
- Never double lessons to "catch up". Missed days move the finish date, nothing more.
- Small and finished beats long and abandoned: a 15-minute day still counts — record whatever was truly completed.
