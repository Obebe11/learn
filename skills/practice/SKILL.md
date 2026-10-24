---
name: practice
description: Run a practice session — varied problems that make already-taught ideas come to mind and work without being named, with a minimal hint ladder, honest verdicts and fresh-surface retests. Use for `review` and pre-milestone lessons, for a leech or a lesson that keeps failing, and whenever the learner asks for problems, exercises or "test me on X". Not for teaching new material (that is `teach`).
---

# Practice

Teaching makes an idea understood; practice makes it **available** — coming to mind on its own, in situations that don't look like the lesson, and running correctly. A learner can hold a complete explanation and still not think of it when a problem needs it. That gap is what practice closes.

The defining condition: the idea being exercised is **not active** in his mind when the problem arrives — only features of the situation can bring it up. A lesson check asks about something named a minute ago (comprehension). A practice problem asks for it cold (retrieval). Everything below follows from that.

Practice is not an exam (you build the demand that strengthens most, not the cheapest gradable one), not a lesson (misconceptions get surgical nudges, not re-teaching), and not puzzle hunting (long problems with a hidden path train search, not recall). Its product is quiet: the idea deploying without help. Don't stage aha-moments; accumulate deployments.

## Where this runs

Same instruments and fallbacks as `teach` (text in chat, LaTeX only where it renders, short messages on a phone). Material is either the plan's knowledge — `learn practice pick` returns the targets — or whatever the learner brings: a topic, a chapter, an outside problem they can't solve (then work from the material itself; log targets only when they are recorded propositions).

`learn practice pick [-n 3]` gives propositions from finished lessons, weakest first and interleaved across lessons, each with the mode to use next and the chains it concludes. Today's misses come back first, marked **RETEST** — pose those in a fresh surface. Pick again after a few problems: the order changes as you log outcomes. `learn fact list --lesson L…` and `learn history --lesson L…` show the evidence behind a target (scores, lapsed cards, earlier misses, notes).

## The three modes

Every problem is built in one mode:

| Mode | Trains | Shape |
|---|---|---|
| **Recall** | the idea comes up from a weak or unfamiliar cue | short, one or two decision points, a situation class not used before |
| **Discrimination** | the right idea wins over a look-alike | near-twin situations where one feature decides which idea applies |
| **Composition** | a chain of ideas runs as a whole | length follows the chain; decision points at its joints; a distracting given or two |

Order within a target: recall → discrimination → composition. You can't choose between ideas you can't retrieve, or chain steps that don't come to mind — so when a later mode fails, drop back and test the earlier one. Difficulty is a separate knob inside each mode (how far the cue is from the lesson, how similar the twins are, how long the chain is). Useful twists in any mode: **reverse** (give the conclusion, ask for the grounds), **break a premise** (weaken one assumption — what fails?).

## Choosing the next problem

Before each problem ask: *what is the cheapest problem that most strengthens — or most clarifies — the weakest thing not yet shown in this session?*

- **Target**: weakest first, then the ideas many others depend on, then the longest-untouched. Use the plan, failed lessons, leeches and `note`s.
- **Mode**: the cheapest mode this target hasn't demonstrated today. What the session hasn't shown, assume not shown.
- **Interleave.** Switch targets between problems; never a block of same-idea problems — that keeps the idea active and turns retrieval into repetition.
- **Difficulty**: clean success → one notch up; success with hints → hold; miss → one notch down. Two missed problems in a row on a target → switch target for the next one. Aim for roughly 85 % success *per idea retrieved*, not per problem.

## Posing

1. **Solve it yourself first**, then try to break it: if it can be solved by a route that skips the target idea, it trains that route instead — fix or discard it. It must need nothing beyond the material.
2. **Commit the model solution before he sees anything** (keep it to yourself in chat). That keeps the verdict honest and the hints consistent.
3. **Never name the target.** The statement doesn't say which idea or which lesson it is about. If the situation makes the idea unmistakable, that's a deliberate easy recall — choose it, don't drift into it.
4. One problem per message; ask him to think out loud (a line or two of reasoning, voice is fine). Unclear wording is free to fix immediately — wording is logistics, not a hint.

## Hints — one rung per request

Revealed information can't be unrevealed, and he can always think longer. So give the least that unblocks, however much he struggles. From his reasoning, find the **first** question in his loop he can't answer (or answered wrongly) — often earlier than where he feels stuck — and respond only to that.

| Rung | Gives away | Example |
|---|---|---|
| R0 | nothing — redirect attention | "Re-read the second given." |
| R1 | the pattern's kind | "This is a sum of many small pieces." |
| R2 | a family of candidates | "Something from last week's unit applies." |
| R3 | the idea, named | retrieval is surrendered, execution remains |
| R4 | the move outlined | he fills in the details |
| R5 | the worked step | only the lesson about boundaries remains |

Grinding on the wrong idea → a verification nudge ("check the assumption behind your move"), never the correction while he can still find it. In chat a hint ladder can be `<details>` blocks when rich messages are on (see `daily-lesson/references/telegram-rich.md`). The rung a problem needed is a difficulty reading: R0–R1 was about right, R3+ was over the edge.

## Verdicts

- **Right**: confirm it and name the move that made it work — one line, no praise.
- **Wrong**: say so plainly, point at where it first broke without giving the correction, nudge once, wait. A wrong attempt is never a reason to reveal the solution; keep going until he solves it or explicitly gives up (then show the committed solution).
- **Classify each miss** (for yourself, and in the `--note`): *nothing came* · *wrong idea won* · *right idea, wrong execution* · *careless* · *misconception* (the retrieved idea itself is wrong). The first four are practice data; a misconception needs a few targeted nudges toward repairing it — and if it's deep, a repair lesson (`study-plan`), not a lecture mid-practice.
- After each problem he may ask about it — answer surgically, then wait for "next" before posing again.

## The arc

- **Open warm**: one to three easy recall problems on the weakest nearby targets.
- **Main body**: interleaved, modes by the rule above, difficulty in the band.
- **Close with retests**: every miss, every hint-heavy problem and every given-up problem gets a retest in a **fresh surface** — never the same problem again. Nothing that failed today ends the session untested.

An outside problem he brings goes through the same machinery: solve it, map which ideas any correct solution needs, then hints, verdict, retest.

## Recording

- **After every concluded problem**: `learn practice log --targets P003[,P004] --mode recall|discrimination|composition --outcome solved|hinted|missed|surrendered [--rung 0..5] [--miss cue|discrimination|execution|careless|misconception] [--note "…"]`. `--rung` is the highest hint given; a solve that needed R3+ is `hinted` (the CLI refuses to call it solved). This is what the next `practice pick` learns from.
- In a plan's `review` or `milestone` lesson: score = problems solved without R3+ hints ÷ problems posed, then `learn done <L-id> --score … --note "missed: X (wrong idea won vs Y); retested OK"`.
- A misconception found here: add it to the lesson's note, and if a card tests the idea, rewrite it (`learn card edit … --facts P…`). A spaced review card is still graded only in the warm-up (`daily-lesson`) — practice never reschedules cards.

## Never

- pose a problem you haven't solved and tried to shortcut;
- require anything beyond the material;
- name the target;
- reveal what he can still find — one rung per request, verification before correction;
- repeat a problem in the same surface;
- teach beyond a surgical line;
- soften or inflate a verdict.
