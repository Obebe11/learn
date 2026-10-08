# Telegram rich messages — formatting cheat-sheet for lessons

Read this when `learn today` reports `chat_format rich` (the learner's Telegram bot sends via Bot API `sendRichMessage`, e.g. Hermes with `rich_messages: true`). If it says `plain`, or you are not sure rich is on, use the plain-text forms in SKILL.md instead — rich Markdown sent through a plain path shows up as stray symbols.

You normally just write Markdown; the gateway sends it. (A rich message is exactly one of `markdown` / `html` / `blocks`; you only choose among them if you call the API yourself.)

## What renders (from the Bot API rich-message spec)

| Want | Write | Notes |
|---|---|---|
| Inline math | `$x^2 + y^2 = r^2$` | raw LaTeX, rendered natively |
| Display math | `$$\int_0^1 x^2\,dx = \tfrac13$$` on its own lines | do not escape backslashes beyond normal Markdown |
| Headings | `## Title` (levels 1–6) | use sparingly on a phone |
| Bold / italic / strike / code | `**b**` `*i*` `~~s~~` `` `c` `` | |
| Highlight a key term | `==term==` | "marked" text |
| Spoiler (tap to reveal) | `||answer||` (backslashes here only escape the table) | hidden until tapped |
| Code | fenced block with language: ```` ```python ```` | monospace; also good for ASCII sketches |
| Lists | `- item`, `1. item`, nested by indent | |
| Task list | `- [x] done` / `- [ ] todo` | progress / day plan / checklists |
| Collapsible | `<details><summary>Hint</summary>…</details>` (HTML allowed inside Markdown) | hints, worked solutions, "why" |
| Quote | `> text` | definitions, the one sentence to remember |
| Table | Markdown pipe table, ≤ 20 columns | Hermes documents native table rendering; if the learner says it looks broken, switch to bullet rows |
| Divider | `---` | |
| Subscript / superscript | `<sub>2</sub>`, `<sup>2</sup>` (HTML) or LaTeX | chemistry: `H<sub>2</sub>O` |
| Image | `![caption](https://…)` — URL must be HTTP(S) | local files go via the gateway's media mechanism (Hermes: `MEDIA:/path`) |

Not available: **Mermaid** (use the patterns below), custom fonts/colours.
Limits: 32,768 characters and 500 blocks per message (nesting ≤ 16). For lessons stay far below — **aim for ≤ ~1,200 characters per message**; a screen of text is the unit.

## Visual patterns that replace diagrams

Prefer structure that renders natively over images. Each pattern: one idea, ≤ 7 elements, arrows re-checked.

- **Dependency map** (`teach` DAG): nested list, roots first —
  ```
  - **Packets** (unconditional truth)
    - Ordering ← needs packets
    - Retransmit on loss ← needs packets
      - **Reliable stream** ← needs both
  ```
- **Process / flow**: numbered list with arrows in the text: `1. Request → 2. Lookup → 3. Response`.
- **Comparison**: a 3–4 column table (`| | A | B |`), one row per property that matters.
- **Worked example, step by step**: `$$` lines for each transformation, one-line reason between them; hide the final step in a `<details>` or `||spoiler||` when you want him to try first.
- **Hint ladder**: `<details><summary>Hint 1</summary>…</details>`, then Hint 2 — he opens only what he needs.
- **Flashcard (fast review)**: question, then the answer in a spoiler, e.g.

  ```
  **Q:** What does `return` do?

  ||Ends the function and hands a value back||
  ```

  He taps, then replies ✅ or ❌. Self-graded, so use it only when he asks for quick reviews; for real checks have him answer first and you judge.
- **The plan's knowledge map**: `learn graph --format md` gives a ready-made rich table (track · progress · ready now) plus a list of ⭐ junctions with what they need — paste it as is. For a picture: `learn graph --format png` and send the file.
- **Progress / day plan**: task list for today's steps; a table for `learn week`.
- **Key takeaway**: one `>` quote at the end of a lesson.
- **Geometry, plots, circuits** (things text can't draw): an image. Use the `visualize` makers if available, send the PNG; otherwise describe + a small table of coordinates. Collage/slideshow blocks exist for multi-image sequences if you call the API with `blocks`.

## Quizzes in rich mode

Same rules as plain mode (options are bare claims, never reveal the answer early) but you can use LaTeX in options, a table for "match the pairs", or a code block for "what does this print?". Put the explanation *after* his reply, optionally inside `<details>` if it's long.

## Pitfalls

- Don't mix: one message = one rich Markdown document. Don't wrap the whole message in a code fence.
- Dynamic text goes in as-is (it is rich text, not HTML) — don't HTML-escape formulas.
- If the gateway reports the rich path failed it falls back to plain MarkdownV2; LaTeX then shows as raw `$…$` — if you see the learner confused by dollar signs, tell them and offer `learn config chat_format plain`.
- On a timeout, do not blindly resend (delivery outcome unknown).
