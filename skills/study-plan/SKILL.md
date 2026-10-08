---
name: study-plan
description: Create, review and adjust a long-term study plan (outcomes, units, daily lessons, milestones) and track progress with the `learn` CLI. Use when the learner wants to learn a subject over weeks or months, asks for a learning plan / curriculum / roadmap, wants to see progress ("how am I doing", "what's my progress"), or wants to change the pace, pause, or revise a plan. Works from any agent, including phone chat (Telegram).
---

# Study plan

Turns "I want to learn X" into a plan the learner can follow at a couple of lessons a day for months, and keeps score honestly. The *teaching* of each lesson is done by the `daily-lesson` skill (which uses `teach`); this skill designs and maintains the plan.

## Tooling: the `learn` CLI

All state lives in plain files managed by `learn` (stdlib Python; data dir `$LEARN_HOME`, default `~/learning`). Never hand-edit `progress.json`. If `learn` isn't on PATH, run `python3 <this repo>/scripts/learn.py` instead (clone is conventionally `~/learn`).

```
learn init --language ru --timezone Europe/Moscow     # once
learn plans | status | week
learn create <slug> --file plan.json  [--update]      # validates, renders PLAN.md
learn pause|resume|archive <slug>
learn sync                                           # git-sync the data dir across devices
```

At the start: `learn sync` (ignore "not a git repo"), `learn plans`, and read `<data dir>/profile.md`. Reply in the language set in `config.json`/profile.

## Chat-mode etiquette (phone / Telegram / WhatsApp)

If the learner is on a messaging gateway: one question per message, short messages (≈ under 1,000 characters), no tables, no LaTeX or Mermaid (they usually don't render — use plain text/Unicode), offer numbered choices instead of open forms. Never dump the whole JSON plan into chat; show the outline.

## Process

### 1. Intake — make the goal concrete (ask one thing at a time)

You need five answers. Don't proceed on a vague goal ("learn AI", "get better at math"): interrogate until it names something the learner will be able to **do**.

1. **Outcome** — what should you be able to *do* at the end, and why does it matter to you? (A project built, an exam passed, a conversation held.)
2. **Deadline** — fixed date, or open-ended?
3. **Time budget** — lessons per day (default **2**), which days (default Mon–Sat, one rest day), minutes per lesson (default 25). Be realistic: a plan that survives a bad week beats an ambitious one.
4. **Starting point** — ask about prior exposure, then run the diagnostic (step 2).
5. **Constraints** — language of materials, books/courses already owned, where they'll study (phone? commute?), what they hate.

### 2. Diagnostic — find where the learner actually is

Run a short placement (4–8 questions, graded) following `teach` Phase 1a: bracket the edge — something they get right *and* something they miss — then stop. Record the result in `level` (one honest sentence) and in `profile.md`. Starting too low is boring, too high is demoralising; this is what prevents both.

### 3. Scope the field (don't plan from memory)

Look it up: standard syllabi, prerequisites, the real first principles, common gotchas, and **one primary resource per outcome** (a book chapter, a course, docs). Use web search or the `researcher` subagent if the environment has them. Only put a resource in the plan if you found it; never invent titles or URLs. If you have no search tool, say the resource list is unverified.

### 4. Backward design — outcomes → evidence → lessons

Design from the end. (Evidence for each rule: `docs/learning-science.md`.)

- **3–7 outcomes**, each starting with an observable verb (*explain, implement, debug, translate, derive, compare…*) at mixed levels from recall to apply/create. "Understand chapter 4" is not an outcome; "Explain why X and solve 3 problems of type Y without notes" is.
- **Evidence first.** For each unit decide what proves the outcome — a built artifact, a problem set, a spoken explanation — and write the **pass bar in advance** ("2 consecutive attempts without notes", "≥ 80 %"). That is the unit's `milestone`.
- **Lessons**: one objective each, sized to `minutes`, ordered by dependency (foundations → derived). Each lesson needs:
  - `objective` — a sentence starting with the verb.
  - `check` — how mastery is verified, using the *same verb* (if the objective says "explain", the check requires an explanation, not recognising the term).
  - `nodes` — 2–4 key ideas for the teacher: the unconditional truths and discovery steps this lesson builds (per `teach`).
- **Make retrieval and spacing structural.** The tracker schedules spaced reviews of every lesson's cards automatically, so you don't plan re-reading. Do add: a cumulative `review` lesson roughly every 5th lesson, and a `milestone` lesson ending each unit. From the second unit on, mix earlier material into practice (interleaving) rather than finishing one topic forever before the next.
- **First lesson = quick win.** Something small the learner can finish and feel progress on in one sitting.
- **Rolling-wave planning.** Detail at most ~6–8 weeks of lessons (heuristic: ≤ 60 lessons). For longer goals put later stages in `later` as one-line outlines and extend with `learn create --update` when the learner approaches the end of the detailed part.
- **Keep it small.** Few outcomes, one main resource each, no resource-hoarding. Cut anything that doesn't serve an outcome.

Before saving, self-check: every outcome has a lesson and a check; every lesson maps to an outcome; the pass bars are concrete; total lessons ÷ (lessons/day × days/week) is the number of weeks you'll quote — and it matches the deadline, if there is one. If not, cut scope, don't raise the pace.

### 5. Present the plan and wait for a go-ahead

In chat, show: the goal in one line; outcomes; units with lesson counts and milestones; pace and **projected finish date**; what a typical day looks like (≈ 5 min warm-up reviews + 2 lessons). Ask what to change. A wrong scope is cheap to fix now and expensive in week three. Don't create the plan until they approve.

### 6. Save it

Write the JSON to a temp file (or pipe with `--stdin`), then `learn create <slug> --file plan.json`. Fix any validation errors it lists. Show `learn status`. Then set up the daily nudge for their environment — see `docs/platforms.md` (Hermes: a no-LLM cron job; Claude Code: a scheduled routine; elsewhere: a calendar reminder). `learn sync` at the end.

### Plan JSON

```json
{
  "title": "Python for automation",
  "goal": "Write scripts that automate my weekly reports",
  "level": "Knows variables and loops; has not written functions or used files.",
  "deadline": "2027-01-31",
  "pace": {"lessons_per_day": 2, "days": ["mon","tue","wed","thu","fri","sat"], "minutes_per_lesson": 25, "pass_score": 0.7},
  "start_date": "2026-10-12",
  "outcomes": [
    {"id": "O1", "text": "Write and test functions that transform data", "bloom": "apply"},
    {"id": "O2", "text": "Read and write CSV/JSON files", "bloom": "apply"}
  ],
  "units": [
    {
      "id": "U1", "title": "Functions", "outcomes": ["O1"],
      "milestone": {"title": "Refactor a script into 3 tested functions", "pass_bar": "Passes my own tests; done without notes"},
      "lessons": [
        {"id": "L01", "title": "What a function is for", "kind": "lesson", "minutes": 20,
         "objective": "Explain what problem functions solve by rewriting repeated code as one function",
         "nodes": ["Repeated code means repeated bugs", "A function names a recipe once"],
         "check": "Given 3 copy-pasted lines, student extracts a function and says why it is better",
         "outcomes": ["O1"]}
      ]
    }
  ],
  "later": ["Stage 2: APIs and scheduling", "Stage 3: a capstone report generator"],
  "resources": [{"title": "Python docs tutorial", "url": "https://docs.python.org/3/tutorial/", "note": "primary reference for O1–O2"}]
}
```

Rules enforced by `learn create`: unique `L..`/`U..`/`O..` ids, `title`+`goal`, every lesson has `objective` and `check`, referenced outcomes exist. `kind` ∈ `lesson | review | milestone`. Omit `pace`/`start_date` to use defaults (today).

## Progress and review rhythm

- **"How am I doing?"** → `learn status` (and `PLAN.md`): bar, pace, projected finish, reviews due. Report plainly, lead with what's going well.
- **Weekly** (Sunday, or the rest day): `learn week`. Ask two questions: what felt solid, what keeps slipping? Adjust the *practice type or resource* for what's slipping — not just the schedule. Add or change lessons by editing the plan (see Revising).
- **Behind schedule**: `learn` already shows `behind N`. Do **not** schedule catch-up doubles — cramming defeats spacing. The projected finish date simply moves; tell the learner that's fine. If they've been behind for 2+ weeks, offer: lighten to 1 lesson/day, pause (`learn pause`), or cut a unit.
- **Failed lessons** come back automatically (`retry`) — the next session re-teaches the gaps first.
- **Monthly**: re-read outcomes; drop what no longer matters.
- **Finished**: `learn archive <slug>`, a short retrospective (what worked, what to learn next), and offer a follow-up plan.
- **Revising**: edit `<data dir>/plans/<slug>/plan.json` (lessons, order, pace), then `learn validate --plan <slug> && learn render --plan <slug>` (or `learn create <slug> --file new.json --update`). Progress is keyed by lesson id, so finished lessons stay finished — keep ids stable.
