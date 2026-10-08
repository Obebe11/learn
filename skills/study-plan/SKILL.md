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
learn graph [--format text|md|mermaid|svg|png] [--level lessons|units]   # the knowledge map (graph plans)
learn start [--date YYYY-MM-DD]                       # change the plan's start date (default: today)
learn pause|resume|archive <slug>
learn sync                                           # git-sync the data dir across devices
```

At the start: `learn sync` (ignore "not a git repo"), `learn plans`, and read `<data dir>/profile.md`. Reply in the language set in `config.json`/profile.

*Remote server (`learn_*` MCP tools, no shell):* skip `learn sync`; `learn <cmd>` is the tool `learn_<cmd>`; read/write the profile with `learn_profile_get`/`learn_profile_set`; create or revise a plan by passing the plan object to `learn_create` (`update: true` to revise; `learn_show` returns the current JSON) instead of writing files.

## Chat-mode etiquette (phone / Telegram / WhatsApp)

If the learner is on a messaging gateway: one question per message, short messages (≈ under 1,000 characters), no Mermaid ever. Check `chat_format` (`learn config chat_format`): `plain` → no tables or LaTeX (they show as raw symbols; use plain text/Unicode); `rich` (Telegram rich messages) → tables, LaTeX and task lists are fine — e.g. present the plan outline as a table of units and a task list for the first week (syntax: `skills/daily-lesson/references/telegram-rich.md`). Offer numbered choices instead of open forms. Never dump the whole JSON plan into chat; show the outline.

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

### 4b. Linear plan or knowledge graph? (choose the shape)

- **Linear** (default): one skill, a strict order, one topic after another.
- **Graph** (`"graph": true`): use it when the goal needs **several skills**, when the learner wants to **jump between topics** and learn different things on the same day, or when they say "I also want to study X separately". A graph also keeps progress honest: each branch moves on its own, and the places where skills must be combined are explicit.

**How a graph plan works**

- Each **unit is a track** — a branch of knowledge (Python, AI, English…). Lessons inside a track are a chain by default (each requires the previous one). Different tracks are independent: any of them can be studied first.
- A lesson may list `requires: ["P3", "A2"]` (lesson ids) when its prerequisites are not simply "the previous lesson".
- A **junction** (`"kind": "junction"`) is a point where **several skills are needed at once** — a mini-project or integration task. It `requires` lessons from **2+ different tracks** (and possibly an earlier junction). It stays locked until all of them are done.
- Every day `learn today` picks lessons from the *ready* frontier, **round-robin across tracks** (retries first, then junctions, then the track studied longest ago) — so a day naturally mixes topics, and each lesson still moves the overall goal forward.

**Designing the graph**

1. Split the goal into **2–5 tracks** that make sense on their own and can be studied in any order. More than 5 and the learner loses the thread.
2. Within each track, order lessons by dependency (foundations first), exactly as in step 4.
3. Find the **junctions**: where does the goal need two or more skills together? Typical: "build X using A and B", "explain/teach it in English", "apply the technique to a real dataset". Rules:
   - The first junction should unlock early (after ~3–5 lessons in each parent track) so the learner feels the payoff; then roughly one junction per 5–6 lessons; the last one is the capstone.
   - `requires` lists the **specific lessons** whose skills it uses, not "the whole track". Two or three parents is typical.
   - Put junctions in their own unit (e.g. `PR` "Projects") so the map shows them as a separate row; `objective` names the combined performance ("Build a bot that … using functions and prompts"), `check` is the artifact it must produce and the bar it must meet.
4. Keep `requires` minimal: only real dependencies. Don't chain tracks together out of tidiness — that turns the graph back into a line.
5. Pace: `lessons_per_day` counts across all tracks. For real variety use 2–3 shorter lessons (20 min), not one long one.
6. Run `learn create`, read its warnings (cycles and unknown ids are errors; a "junction" that doesn't combine two tracks is a warning), then **show the map** before asking for approval: `learn graph` (text — put it in a code block in chat), `learn graph --format md` (table, for rich Telegram), `learn graph --format png` (an image to send; needs `rsvg-convert`), `--format mermaid` for Obsidian.

**Changing the graph later** (the learner decides to add a topic): add a new unit (a new track) and, when it makes sense, junction lessons that require lessons from it, then `learn create <slug> --file new.json --update`. Keep existing lesson ids stable. Converting an existing **linear** plan to a graph: set `"graph": true`; its units become parallel tracks, so add `requires` to the first lesson of any unit that must stay *after* the previous one (the CLI warns about exactly those).

Example (two tracks and a junction; lessons abbreviated):

```json
{
  "title": "AI career", "goal": "Ship a small AI tool", "graph": true,
  "pace": {"lessons_per_day": 2, "days": ["mon","tue","wed","thu","fri"], "minutes_per_lesson": 25},
  "units": [
    {"id": "PY", "title": "Python", "lessons": [
      {"id": "P1", "title": "Functions", "objective": "…", "check": "…"},
      {"id": "P2", "title": "Lists and dicts", "objective": "…", "check": "…"},
      {"id": "P3", "title": "Files and JSON", "objective": "…", "check": "…"}]},
    {"id": "AI", "title": "AI basics", "lessons": [
      {"id": "A1", "title": "What a model is", "objective": "…", "check": "…"},
      {"id": "A2", "title": "Prompting", "objective": "…", "check": "…"}]},
    {"id": "PR", "title": "Projects", "lessons": [
      {"id": "J1", "title": "A chatbot script", "kind": "junction", "requires": ["P3", "A2"],
       "objective": "Build a script that reads a file and asks a model about it", "check": "Runs on a new file; learner explains each part"}]}
  ]
}
```

### 5. Present the plan and wait for a go-ahead

In chat, show: the goal in one line; outcomes; units with lesson counts and milestones; pace and **projected finish date**; what a typical day looks like (≈ 5 min warm-up reviews + 2 lessons). For a graph plan, show the map (see 4b) and explain in two sentences how days will mix tracks. Ask what to change. A wrong scope is cheap to fix now and expensive in week three. Don't create the plan until they approve.

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

- **"How am I doing?"** → `learn status` (and `PLAN.md`): bar, pace, projected finish, reviews due; for a graph plan also per-track progress and what is ready. Report plainly, lead with what's going well. "Show me the map" → `learn graph`.
- **Weekly** (Sunday, or the rest day): `learn week`. Ask two questions: what felt solid, what keeps slipping? Adjust the *practice type or resource* for what's slipping — not just the schedule. Add or change lessons by editing the plan (see Revising).
- **Behind schedule**: `learn` already shows `behind N`. Do **not** schedule catch-up doubles — cramming defeats spacing. The projected finish date simply moves; tell the learner that's fine. If they've been behind for 2+ weeks, offer: lighten to 1 lesson/day, pause (`learn pause`), or cut a unit.
- **Failed lessons** come back automatically (`retry`) — the next session re-teaches the gaps first.
- **Monthly**: re-read outcomes; drop what no longer matters.
- **Finished**: `learn archive <slug>`, a short retrospective (what worked, what to learn next), and offer a follow-up plan.
- **Revising**: edit `<data dir>/plans/<slug>/plan.json` (lessons, order, pace), then `learn validate --plan <slug> && learn render --plan <slug>` (or `learn create <slug> --file new.json --update`). Progress is keyed by lesson id, so finished lessons stay finished — keep ids stable.
