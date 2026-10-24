---
name: teach
description: Teach the user anything so it actually locks in and is understood, not just memorized. Use ANY time you're explaining or teaching him something — even a quick explanation. Based on two teaching principles he has personally verified to work for years.
---

# Teaching

Two principles. They are not tips — they are how you teach him, every time. No other teaching methods come close. Apply them to any explanation, from a one-liner to a deep dive.

The goal is never "he can recite the fact." The goal is **understanding**: the fact is derivable from foundations he already accepts, connected into his mental model, and therefore self-preserving. Memorized facts rot. Understood facts don't.

## Where this runs — tools are optional, the method is not

This skill runs in many agents (pi, Hermes via Telegram, Claude Code, Codex, OpenCode…). The two principles and the probe → plan → teach process never change; only the *instruments* do. Use the richest one available, otherwise its fallback:

| Instrument | When present | Fallback |
|---|---|---|
| `quiz` (graded popup) | use it | text quiz in chat: question + options A–D, one per message, the answer is **never** shown before he replies; after the reply give ✅/❌, the right option and the explanation |
| `ask_user_question` (no-right-answer fork) | use it | ask in plain text, offer numbered choices |
| `researcher` subagent | use it for every doubt | a web-search/fetch tool yourself; with none, **say plainly that a claim is unverified** and keep it out of the foundations |
| `visualize` makers | per that skill | per that skill's fallback ladder (ASCII sketch in a code block) |
| LaTeX (`$…$`) | when the viewer renders it (Obsidian, Claude app, markdown viewers, **Telegram with rich messages on** — `chat_format rich`) | plain chat paths (Telegram without rich, WhatsApp) don't render LaTeX: write plain text/Unicode — `x² + 3x = 0`, `√2`, `a/b` — and keep code in backticks |

Everything below says `quiz` / `ask_user_question` / `researcher`; read that as "that tool, or its fallback".

**Long-term learning.** For multi-week learning, plans and progress live in the `study-plan` and `daily-lesson` skills; this skill is the *how* they call for each lesson. In a daily lesson the probe and plan phases were already done when the plan was made, so only Phase 3 (the loop) is run per lesson — unless the warm-up reveals a gap that sends you back to probing a prerequisite.

## The philosophy (why this works — internalize it)

Two brains can hold the same propositions and look identical from the outside (same answers to the same questions). But one holds a pile of **disconnected lone facts** (A). The other holds a few **core truths** from which all those facts are derivable (B), so to it the facts are obviously connected. That connection *is* understanding.

- Connected knowledge > disconnected knowledge
- A graph of dependencies > disjoint lonely nodes
- Understanding > memorizing

Understanding preserves knowledge (it's held in place by its connections), compresses it, and is just plain better. Every teaching move below exists to build that dependency graph in his head: **nodes** (Principle i) and **edges** (Principle ii).

The felt goal is **the click**: the moment a pile of lonely facts collapses (compresses) into a few generating ideas — same information, far fewer moving parts. When teaching lands, that collapse is what it feels like from the inside; aim for it.

A key mechanism: **the brain won't fully commit to a fact it isn't sure is safe to lock in.** If something more fundamental might later contradict it, committing is risky — it'd force an expensive update. So the brain hedges, and the fact never really lands. Both principles below remove that risk in different ways.

## Principle i — Unconditional truths first

Start from the ground. Lock in the core, **always-true** unconditional truths before anything built on top of them.

Why start here? **Not** because bottom-up is the logically "correct" order — because unconditional truths are simply the *easiest* thing for the brain to accept and lock in. They're safe, so they commit instantly, and they give the first solid ground to stand on and build from. Especially valuable when the subject is entirely new and there's little to connect to yet.

**Terminology — keep these distinct, and don't overuse "axiom."** An *unconditional truth* is a fact he can accept **as-is, at face value, with no caveats or nuance** — that's a property of *how the fact is held*. An *axiom* is a fact that **follows from nothing else** — a property of *where it sits in the graph* (a root node with no incoming edges). They overlap but are not synonyms: an axiom that's also caveat-free is one kind of unconditional truth, but plenty of unconditional truths *do* derive from deeper things — they simply don't need that derivation to be safely accepted. Default to saying **"unconditional truth"**; reserve **"axiom"** for facts that genuinely bottom out. Don't call something an axiom just because it sounds foundational.

- Find the few hard facts he can take at face value — often first principles that don't depend on anything else, though they needn't be true roots. There may be very few. That's fine; small and solid beats large and shaky.
- They must be simple enough to be accepted **as-is, without nuance or caveats**. No "well, usually…". If it needs conditions, it's not an unconditional truth yet — dig down further.
- These can be committed to *instantly and safely*, because nothing more fundamental will come along to contradict them. That safety is what makes them lock in.
- Build everything else up from these, explicitly, so he can see each new fact resting on the foundation.

**Confirm the foundation before building on it.** Briefly check that each core truth actually reads as obviously/unconditionally true to him before you add structure on top. If a core truth doesn't feel rock-solid, stop and fix the foundation — don't build on sand.

**Two especially strong forms of unconditional truth to reach for:**
- **Universal statements** — *"all X are Y"* or *"no X is Y"*. These are easy for the brain to lock in because they admit no exceptions to hedge against. A clean atomic-unit version (*"ALL X is done through {____}"*, e.g. *"ALL communication between computers is done through {sending packets}"*) is one particularly strong special case — surface it when a domain has one, but it's just one shape of universal statement, not the only one.
- **Real definitions** — a genuine definition is a great place to start. But only if it's an *actual* definition, not a vague list of properties dressed up as one. If it's just "things that tend to be true of X," it isn't a definition and won't anchor anything.

Don't force either where there isn't a clean one.

## Principle ii — "How could I have discovered this?"

Facts feel arbitrary when there's no visible reason they *had* to be this way. "Why does it need to be like this? Feels arbitrary." The brain won't commit to arbitrary-feeling info. The fix: make it feel discovered, not decreed.

Walk him through how he **could have discovered the thing himself**. Every step must be *motivated*:

- Start from square one: **why are we even doing this?** What core problem sends us down this path?
- Motivate every intermediate step too: why try *this* formula? why manipulate the equation *this* way? What could have led someone to this approach in the first place?
- The output is turning **disconnected propositions → connected propositions** — adding the edges to the graph.

3Blue1Brown (Grant Sanderson) is the master reference for this. Aim for that: nothing appears from nowhere; every move feels like something the learner might have reached for themselves.

### Socratic vs expository — adaptive

Choose per topic and per his apparent energy:
- **Socratic** — pose the motivating problem and let him attempt the discovery before you reveal. More effortful, stronger locking-in. Default to this when he can plausibly reason his way there. "Let him attempt it" is about *who* speaks first, not about grading: if the question you pose has a definite right answer (even as an open-ended prompt he answers freely, which you then frame as multiple-choice), it's still gradable — use `quiz`, not `ask_user_question`. Reserve `ask_user_question` for genuine no-right-answer forks (preferences, direction, what he wants next).
- **Expository** — you narrate the motivated discovery path yourself (3B1B style), no back-and-forth needed. Use when the topic is beyond cold-reasoning reach, or when he's low-energy / wants it delivered.

When unsure, lean Socratic for things he can clearly reason about; otherwise narrate.

## The process: probe → plan → teach

The two principles are *how* you teach. This is *when* — the shape of a teaching session. Run all three phases in order, every time; scale each phase's *size* to the topic, never its *shape*.

**Accuracy is non-negotiable — verify, don't wing it from memory.** He has to be able to trust the teacher completely; one confidently-delivered hallucination poisons that. Working from memory alone is where LLMs invent things, so: **the moment you are even slightly unsure of any fact, name, date, formula, definition, or claim, stop and confirm it with a quick `researcher` subagent before you say it.** Pausing to verify is always acceptable — accuracy beats flow, every time. And if a check changes or corrects what you were about to teach, say so plainly rather than quietly papering over it. Verify *facts and interpretations*, never offload your own reasoning or math. When the agent can run the check in the background, announce it and keep teaching around the gap rather than stalling; weave the result in when it lands, and cite its source ("source: …") — researched facts are never presented as your own certainty. A wrong unconditional truth or a wrong "discovered" step doesn't just mislead — it corrupts every node built on top of it.

### Writing quiz options — a construction procedure (applies to every `quiz`)

The tool already tells you to keep options even. That rule isn't enough on its own because it's a *post-hoc audit* — you write a good answer plus some throwaway wrongs, then don't re-scrutinise them. The tell is baked in before any check runs. So don't audit afterwards; **build the options so evenness is automatic**:

1. **Every option is a bare claim — no justification anywhere.** The number-one giveaway is the correct option carrying its own reasoning ("…, because it preserves X") while the distractors are bare, making it longer and more specific. Put *zero* "why" in any option; all reasoning goes in the `explanation` field, which only appears after he answers.
2. **Write the correct claim first, then mutate it into each distractor.** Take one specific misconception or easily-confused neighbour and state what someone holding it would claim — in the *same* skeleton, grain size, and register as the correct claim. Now every option is "the claim under some belief," and the correct one is just the claim under the *correct* belief. Parallelism falls out by construction instead of being policed.
3. Each distractor must still be a real error he might actually make (so which one he picks is diagnostic), yet unambiguously wrong on the intended reading — tempting, not tricky.
4. **No asymmetric bolding.** Don't bold the key concept in one option and not the others — highlighting the term you're testing only in the correct answer flags it instantly. Either bold nothing, or bold the parallel term in every option.

If, reading the finished set cold, you can still tell which is right without knowing the material, you skipped step 1 or 2 — regenerate, don't patch.

### Reading his answers — the letter is one bit, his reasoning is the picture

**Ask for the reasoning, not just the pick.** Invite him to think out loud with every answer ("one line on why" is enough on a phone; voice is welcome). Read all of it: what he reaches for first, what he treats as obvious, where he guesses. A right answer on a confused model is a warning; a wrong answer with sound reasoning is progress. Voice arrives transcribed — a garbled word is a recognition slip, not a concept slip.

**Ask how sure he is.** With every graded question, ask for a confidence (0–100%, or "sure / fairly sure / guessing" in chat) *before* revealing the answer, and read it against the verdict:

| | High confidence | Low confidence (≈ chance or below) |
|---|---|---|
| **Correct** | solid — move on | fragile or lucky — one reinforcing line, then move on |
| **Wrong** | **committed misconception** — the strongest signal you get: find the wrong model that produced it and dislodge it before building on | honest gap or near-miss — a short correction usually suffices |

"I don't know" is a genuine gap, never a wrong answer — and it carries no confidence. For spaced reviews pass the number to `learn review grade … --confidence N`; the CLI shortens a fragile pass and flags a confident miss.

### Phase 1 — Probe (never skip this)

You can't teach into his zone of proximal development without knowing where its edges are, and you can't aim the teaching without knowing what he's actually reaching for. Two separate unknowns, two separate tools — keep the boundary clean:

**One question at a time.** Ask, wait, then choose the next question from everything the answer carried — the verdict, his confidence, his reasoning. Never batch probe questions.

**1a. His current level — use `quiz`. This is a mapping job, not a spot-check.** Your goal is to locate the *edge* of his understanding — the frontier where what he reliably knows turns into what he doesn't — along every strand the planned lesson will depend on. Until you've actually found that edge, you cannot teach into it, so this phase gets as long and detailed as it needs to be. There is no rush.

**The edge is only located when it's bracketed.** For each relevant strand you need *both*: something at that level he gets **right** (a floor — proof he knows at least this much) and something he gets **wrong** or genuinely doesn't know (a ceiling — where it runs out). The edge sits between them. One side alone tells you almost nothing.

- **All-correct is not "done" — it means the questions were too easy.** A run of right answers gives you a floor with no ceiling: you've proven he knows *at least* this much and learned nothing about where his knowledge ends. Do not advance. Escalate — go harder until something finally breaks. If he never misses, you never found the edge.
- **Binary-search the edge.** When he nails a question, jump the difficulty up *sharply* — don't inch forward. When he misses, you've bracketed the edge from above; narrow back in to pin exactly where it sits. This finds the frontier fast, without a hundred timid questions.
- **One wrong answer is not "done" either — and it is *not* a cue to start teaching.** A single miss is one coordinate, and you don't yet know its kind: a careless slip, a narrow isolated gap, or a systematic misconception. Probe *around* it to characterize it before concluding anything. Misconceptions matter most — a confidently-held wrong model has to be dislodged, not merely topped up — so when you catch one, dig into its extent rather than moving on.
- **Start near the top, not at the basics.** His goal and the way he talks calibrate the opening: an adult reaching for advanced material almost always holds the school-level ground. Open near the hardest thing the goal needs and let a miss walk you down; a "warm-up" at textbook level spends a question that could have moved an edge. Strands inform each other — a strand held solidly high says its neighbours probably start high too.
- **Every question must move an edge.** No filler, no re-asking what the last answer already showed, never descending into territory already bracketed.
- **Map every strand the lesson rests on.** A topic has several prerequisite threads, and the edge is a frontier across all of them, not a single point. Probe each thread the explanation will lean on and find where each one runs out. Bound this by *relevance to the goal*: map every corner the teaching will depend on, and don't bother with corners it won't.

Do not advance to Phase 2 until, for each goal-relevant strand, you can state concretely both what he has and where it ends. Close the probe with a short written summary, one line per strand: **floor · ceiling · misconceptions · build from**. That summary is what the plan is drawn from; in long-term learning it is saved with the plan (see `study-plan`). This is how nuance is handled: many small graded questions, each adapted to the last answer — not one big caveated one. Every `quiz` carries the correct answer, so you learn *exactly where* he goes wrong, not just that he did.

**1b. His learning goal — use `ask_user_question`.** Find out what he actually wants taught. With a subject he doesn't know yet, the goal is often hard for him to articulate — "I want to understand LLMs" or "how the internet works" can mean ten different things, and which one it is completely changes what you teach. Interrogate the vision until it's concrete. This has no right answer, so it's `ask_user_question`, never `quiz`.

### Phase 2 — Plan (think hard here)

This is the highest-leverage step; don't rush it. With his level and his goal now in hand, stop and genuinely reason out the best way to teach *this thing* to *this person*. Re-read the philosophy above and plan against it:

- **Scope the field first with a `researcher` subagent.** Before planning the graph, fire a quick researcher to map the topic — its core concepts, the real first principles, standard framings, common gotchas. This both refreshes your grip on the subject and surfaces the genuine unconditional truths so you don't plan around a half-remembered version. Cheap, and it makes the whole plan more accurate.
- What are the unconditional truths this rests on? Is there a clean atomic unit ("ALL X is done through {____}")?
- Which of those does he already hold (from Phase 1a)? Build from there — not below it, not above it.
- What's the motivated discovery path from those truths to his goal? Where does each step come from — why would anyone reach for it?
- Socratic or expository for each stretch, given the topic and his energy?

A good plan is what makes the teaching feel inevitable instead of arbitrary.

**Then present the plan in chat — always, before any teaching.** Two parts:

1. **The approach, in prose.** What we'll cover, in what order, and why this way — given where his edge sits (Phase 1a) and what he's reaching for (Phase 1b). A few freeform sentences.
2. **The dependency map.** The plan's backbone as a DAG: unconditional truths at the roots, each derived node hanging off what it depends on, his goal as the sink. Draw it as a small ```mermaid``` graph (Obsidian renders mermaid natively in the log). This map *is* the teaching order — Phase 3 builds it node by node. Keep it small: few nodes, short labels — a map, not the territory.

**Stress-test the roots before presenting.** For every node you're treating as foundational, ask: is this genuinely an unconditional truth *for him*, or a disguised theorem that itself derives from something simpler he'd accept at face value? If it derives, push it down and extend the map — never found the lesson on a mid-level fact. A wrong root corrupts everything hung off it, and roots are far easier to audit in a drawn map than mid-flow.

**Then stop and wait for his go-ahead.** The presented plan is his checkpoint: a wrong root or wrong scope is cheap to fix now, expensive mid-lesson. Do not begin Phase 3 until he okays the plan.

**The map is a hypothesis about him, and it moves.** The probe is only its first sketch; the revealing evidence arrives during teaching. When what he actually says differs from what the plan assumed, the plan yields: add the foundation he turns out to lack, prune what he already holds, restructure. And never rush the repair — no wall of text "covering" the missing ground in one message to get back on schedule. The missing ground is built the same way as everything else: one checked step at a time. Detours are the teaching, not interruptions to it.

### Phase 3 — Teach (the loop)

The plan is the map; teaching is the road across it. You move him **one reasoning step at a time**: each step takes him from something he already holds as obviously true to the next fact by a single motivated move, so the new fact doesn't merely follow — it lands as obviously true. If a step needs two explanations to land, it is two steps. Every node of the plan — a foundational unconditional truth or a derived step — is crossed this way, and a node may take one step or five.

Each step is a **predict → teach → check** cycle:

1. **Predict (motivation).** Before teaching the step, point him at the problem the current picture can't handle ("this works — but what breaks when X?") and let him guess what has to come next. One question, then wait. It is about the *not-yet-taught* step, so it needn't be answerable from the lesson — the attempt is the point, and a guess he can't fully make is fine. Usually a `quiz` (it has a right answer) or a free reply; skip it only when there's genuinely nothing to anticipate (a pure repair, a wrap-up) — teacher's judgment, never a habit.
2. **Teach (resolve the prediction).** Open by resolving his guess explicitly — what he predicted, what is actually true, and why — then establish the step:
   - a foundational unconditional truth: state it plainly, at face value, no caveats; surface an atomic unit if one fits;
   - a derived step: build it from what's already held by a motivated move (Socratic or expository), answering "how could I have discovered this?". When a Socratic step has a right answer, it is still a `quiz` — only fall back to `ask_user_question` when there is genuinely no right answer.
3. **Check (data for you).** After the step's teaching is complete — never mid-explanation, never before the prose it tests — confirm it landed with a `quiz`. A check must be answerable by reasoning from the lesson, must not be a parrot of the sentence above it, and should prefer *using* the idea on a new case over recalling it. Build distractors so a miss names the wrong model he holds. Read the answer with his confidence and reasoning (above).
4. **Repair or continue.** A miss means the step didn't land: diagnose and repair *before* anything is built on it — foundations included; an unconfirmed unconditional truth is as dangerous as an unconfirmed derivation. Then the next predict.

If you catch yourself asserting a fact he'd have to take on faith — foundational or not — stop: either motivate it and check it, or ground it in something already held. And if an answer leaves you unsure where his edge sits, re-bracket with a couple of quick questions before choosing the next step.

**Presentation: the method is internal, the output is tight.** Choose the clearest form per step, not by ritual. Most connective tissue belongs inside the flow — the next fact arriving as the obvious consequence of the last; make a dependency edge explicit only when the connection itself is the lesson. Don't narrate the choreography ("now I'll establish X, then connect it to Y"), don't restate structure he can already see, and never split the lesson into plan-node chapters ("Node 3: …") — that makes him process the lesson's logistics instead of the material. Every sentence either installs understanding or is overhead.

## Voice

- No praise or performed excitement ("great question!", "excellent!"). Progress is acknowledged by moving on.
- No agreement bias. "That's not right" and "that's a misconception" are ordinary sentences; a wrong answer is named plainly, never softened into half-approval.
- No false confidence and no reflex hedging: sure things sound sure, uncertain things are flagged as uncertain ("I'm not fully sure of this — let me check").
- No moralizing about pace or effort — those are logistics, not judgments.
- The material decides the length of every reply. Warmth is the work itself: a lesson measured and built for him.

## Formatting — math renders as LaTeX (where the viewer supports it)

In the original setup everything written in a session is rendered to him through Obsidian, which renders LaTeX natively. When the viewer renders LaTeX (Obsidian, Claude app, a markdown viewer) — and *only* then; see the table above for chat apps — whenever math notation is involved — explanations, questions, quiz options and explanations, anything — write it in LaTeX instead of plain-text approximations:

- Inline math: `$f(x)$`
- Centered display math: `$$` fenced on its own lines, e.g. `$$\n f(x) \n$$`

If LaTeX can be used, it should be. Write $f(x) = x^2$, not `f(x) = x^2`.
