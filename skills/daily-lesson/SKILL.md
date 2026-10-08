---
name: daily-lesson
description: Run today's learning session from the learner's study plan — spaced-repetition warm-up, one or two lessons taught with the `teach` method, a graded check, and progress recording via the `learn` CLI. Use when the learner says "daily lesson", "/daily-lesson", "let's study", "today's lesson", "what's next", answers a daily reminder, or any time a study plan exists and they want to continue learning. Designed for short mobile chat sessions (Telegram) as well as terminals.
---

# Daily lesson

One session = **warm-up reviews → 1–2 lessons → wrap-up**, about 40–50 minutes at the default pace, and it must be fine to do on a phone, in pieces, hours apart. The plan decides *what*; `teach` decides *how*; the `learn` CLI is the memory — nothing counts until it is recorded.

## 0. Start

1. `learn sync` (ignore "not a git repo"; if it reports a conflict, tell the learner and continue read-only).
2. Read `<data dir>/profile.md` and `config.json` → lesson language, pace preferences. Speak that language.
3. `learn today` (add `--plan <slug>` if several plans and the learner named one). It lists today's lessons, any warm-up reviews, streak and pace.
   - No plan yet → hand over to `study-plan`.
   - Rest day / quota done → only offer reviews; offer one bonus lesson (`learn next`) *only* if asked.
4. One-line opener: streak, today's plan (e.g. "2 lessons + 4 reviews, ≈ 45 min"). If they've been away 4+ days: no guilt, no catch-up speech — "welcome back, we start light".
5. If a lesson was started earlier but never recorded (it won't appear as done), start it again with a 2-sentence recap.

## 1. Warm-up: retrieval before anything new (≈ 5 min)

For each due card from `learn today` (cap ~6; pull more only if asked):

1. Ask the question **closed-book** — never show the answer first. One card per message.
2. Judge the reply: lenient on wording, strict on substance. Partially right counts as `fail` unless the missing part is minor.
3. Reveal the stored answer in one line, add a one-sentence re-explanation if wrong, then record: `learn review grade <plan>/<card> pass|fail`.

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
2. `learn status` → report in 2 lines: progress bar, pace, finish date. Celebrate real things (a hard idea that clicked, a streak) — truthfully.
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
- **No LaTeX, no Mermaid** in messaging apps (not rendered): write math in plain text/Unicode (`x² + 3x = 0`, `√2`, `a/b`), keep code in backticks or fenced blocks. Diagrams: a tiny ASCII/Unicode sketch in a code block (≤ 7 nodes), or an image if the environment can send one (see `visualize`).
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
