"""Knowledge-graph logic for learn.py (stdlib only).

A plan in *graph mode* (`"graph": true`) is a DAG of lessons:

* each unit is a **track** (a branch of knowledge: Python, AI, ...);
* a lesson may list `requires` (lesson ids). Without it, a lesson requires the
  previous lesson of its unit, so a plain unit is a chain and two units are two
  parallel branches;
* a `kind: "junction"` lesson is a point where several skills are needed at
  once — it requires lessons from at least two tracks.

Plans without `graph: true` stay linear (the whole plan is one chain).

Everything here is a pure function of the plan/progress dicts.
"""

import os
import shutil
import subprocess
import xml.sax.saxutils as xml
from collections import defaultdict
from pathlib import Path

SATISFIED = ("done", "skipped")
ICON = {"done": "✅", "skipped": "⏭", "retry": "🔁", "available": "▶", "locked": "🔒"}
STATUS_FILL = {"done": "#a5d6a7", "skipped": "#e0e0e0", "retry": "#ffcc80",
               "available": "#fff59d", "locked": "#eceff1"}
TRACK_COLORS = ["#3f51b5", "#009688", "#e91e63", "#ff9800", "#795548", "#607d8b", "#8bc34a", "#9c27b0"]


def graph_mode(plan):
    return bool(plan.get("graph"))


def lessons_of(plan):
    return [l for u in plan["units"] for l in u["lessons"]]


def unit_of(plan):
    return {l["id"]: u["id"] for u in plan["units"] for l in u["lessons"]}


def order_of(plan):
    return {l["id"]: i for i, l in enumerate(lessons_of(plan))}


def requires_map(plan):
    """Effective prerequisites of every lesson."""
    reqs = {}
    if graph_mode(plan):
        for u in plan["units"]:
            prev = None
            for l in u["lessons"]:
                reqs[l["id"]] = list(l["requires"]) if "requires" in l else ([prev] if prev else [])
                prev = l["id"]
    else:
        prev = None
        for l in lessons_of(plan):
            reqs[l["id"]] = [prev] if prev else []
            prev = l["id"]
    return reqs


def find_cycle(reqs):
    """Return one cycle as a list of ids, or None."""
    state = {}

    def visit(n, path):
        state[n] = 1
        for m in reqs.get(n, []):
            if state.get(m) == 1:
                return path[path.index(m):] + [m] if m in path else [m, n, m]
            if m not in state:
                cyc = visit(m, path + [m])
                if cyc:
                    return cyc
        state[n] = 2
        return None

    for n in reqs:
        if n not in state:
            cyc = visit(n, [n])
            if cyc:
                return cyc
    return None


def graph_warnings(plan):
    """Non-fatal design problems in a graph plan."""
    out = []
    if not graph_mode(plan):
        return out
    units = unit_of(plan)
    reqs = requires_map(plan)
    for l in lessons_of(plan):
        if l.get("kind") != "junction":
            continue
        tracks = {units[r] for r in reqs[l["id"]] if r in units}
        if len(reqs[l["id"]]) < 2 or len(tracks) < 2:
            out.append(f"junction {l['id']} should combine lessons from 2+ different tracks "
                       f"(it requires {', '.join(reqs[l['id']]) or 'nothing'})")
    non_junction_multi = [l["id"] for l in lessons_of(plan)
                          if l.get("kind") != "junction" and len({units.get(r) for r in reqs[l["id"]]}) > 1]
    if non_junction_multi:
        out.append("lessons that combine several tracks should be kind 'junction': " + ", ".join(non_junction_multi))
    if len(plan["units"]) < 2:
        out.append("graph mode with a single track is just a chain — add a second track or drop 'graph'")
    return out


# ----------------------------------------------------------------------------
# status and scheduling
# ----------------------------------------------------------------------------

def lesson_status(prog, lid):
    return prog["lessons"].get(lid, {}).get("status", "pending")


def satisfied_ids(plan, prog, extra=()):
    s = {l["id"] for l in lessons_of(plan) if lesson_status(prog, l["id"]) in SATISFIED}
    s.update(extra)
    return s


def node_status(plan, prog, lid, reqs=None, sat=None):
    st = lesson_status(prog, lid)
    if st in ("done", "skipped", "retry"):
        return st
    reqs = reqs or requires_map(plan)
    sat = sat if sat is not None else satisfied_ids(plan, prog)
    return "available" if all(r in sat for r in reqs[lid]) else "locked"


def missing_requirements(plan, prog, lid):
    sat = satisfied_ids(plan, prog)
    return [r for r in requires_map(plan)[lid] if r not in sat]


def pending(plan, prog):
    return [l for l in lessons_of(plan) if lesson_status(prog, l["id"]) in ("pending", "retry")]


def day_plan(plan, prog, quota, today_iso, track=None):
    """Which lessons to teach today.

    Linear plans: the next `quota` pending lessons, in order (as before).
    Graph plans: pick from the *available* frontier, round-robin across
    tracks (interleaving) — retries first, then the day's first junction (the
    payoff of several skills; at most one gets priority per day, so projects
    don't pile up), then the track with the fewest picks today, then the
    track studied longest ago. A pick virtually unlocks its successors, so a
    quota larger than the number of tracks continues down a branch.
    """
    cands_all = pending(plan, prog)
    if track:
        valid = {u["id"] for u in plan["units"]}
        if track not in valid:
            raise KeyError(f"unknown track '{track}' (tracks: {', '.join(sorted(valid))})")
    if not graph_mode(plan):
        picks = [l for l in cands_all if not track or unit_of(plan)[l["id"]] == track]
        return picks[:quota]

    units, reqs, order = unit_of(plan), requires_map(plan), order_of(plan)
    last = defaultdict(str)
    for lid, rec in prog["lessons"].items():
        if rec.get("status") == "done" and lid in units:
            last[units[lid]] = max(last[units[lid]], rec.get("date", ""))
    today_count = defaultdict(int)
    kinds = {l["id"]: l.get("kind", "lesson") for l in lessons_of(plan)}
    junctions_today = 0
    for e in prog["events"]:
        if e["type"] == "lesson" and e["date"] == today_iso and e["id"] in units:
            today_count[units[e["id"]]] += 1
            junctions_today += kinds[e["id"]] == "junction"

    sat = satisfied_ids(plan, prog)
    chosen = []
    while len(chosen) < quota:
        cands = [l for l in cands_all if l not in chosen and all(r in sat for r in reqs[l["id"]])
                 and (not track or units[l["id"]] == track)]
        if not cands:
            break
        pick = min(cands, key=lambda l: (lesson_status(prog, l["id"]) != "retry",
                                         not (l.get("kind") == "junction" and junctions_today == 0),
                                         today_count[units[l["id"]]],
                                         last[units[l["id"]]],
                                         order[l["id"]]))
        chosen.append(pick)
        sat.add(pick["id"])
        today_count[units[pick["id"]]] += 1
        junctions_today += pick.get("kind") == "junction"
    return chosen


def track_summary(plan, prog):
    """Per-track progress and what is ready now."""
    reqs, sat = requires_map(plan), satisfied_ids(plan, prog)
    out = []
    for i, u in enumerate(plan["units"]):
        ls = u["lessons"]
        done = sum(1 for l in ls if lesson_status(prog, l["id"]) == "done")
        ready = [l["id"] for l in ls if node_status(plan, prog, l["id"], reqs, sat) in ("available", "retry")]
        out.append({"id": u["id"], "title": u["title"], "index": i, "done": done, "total": len(ls), "ready": ready})
    return out


# ----------------------------------------------------------------------------
# model for renderers
# ----------------------------------------------------------------------------

def build_model(plan, prog, level="lessons"):
    reqs, sat, units = requires_map(plan), satisfied_ids(plan, prog), unit_of(plan)
    tracks = track_summary(plan, prog)
    utitle = {u["id"]: u["title"] for u in plan["units"]}
    if level == "units":
        nodes, edges = [], set()
        for t in tracks:
            if t["done"] == t["total"]:
                st = "done"
            elif t["ready"]:
                st = "available"
            else:
                st = "locked"
            nodes.append({"id": t["id"], "short": f"{t['done']}/{t['total']}", "label": t["title"],
                          "unit": t["id"], "status": st, "kind": "unit"})
        for lid, rs in reqs.items():
            for r in rs:
                if units[r] != units[lid]:
                    edges.add((units[r], units[lid]))
        return {"level": level, "nodes": nodes, "edges": sorted(edges), "tracks": tracks}
    nodes = [{"id": l["id"], "short": l["id"], "label": l["title"], "unit": units[l["id"]],
              "unit_title": utitle[units[l["id"]]], "kind": l.get("kind", "lesson"),
              "status": node_status(plan, prog, l["id"], reqs, sat)} for l in lessons_of(plan)]
    edges = [(r, lid) for lid, rs in reqs.items() for r in rs]
    return {"level": level, "nodes": nodes, "edges": edges, "tracks": tracks}


def _short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ----------------------------------------------------------------------------
# text / markdown renderers (chat friendly)
# ----------------------------------------------------------------------------

def _token(n):
    star = "⭐" if n["kind"] == "junction" else ""
    return f"{star}{ICON[n['status']]}{n['id']}"


def _junction_needs(model):
    """(node, [required ids]) for every junction and every node that needs another track."""
    nodes = {n["id"]: n for n in model["nodes"]}
    out = []
    for lid, n in nodes.items():
        needs = [a for a, b in model["edges"] if b == lid]
        cross = any(nodes[a]["unit"] != n["unit"] for a in needs)
        if needs and (n["kind"] == "junction" or cross):
            out.append((n, needs))
    return out


def _chain_line(nodes_in_track, edge_set):
    """One track as a chain; long finished stretches are collapsed."""
    toks = []
    first_open = next((i for i, n in enumerate(nodes_in_track) if n["status"] not in SATISFIED), len(nodes_in_track))
    start = max(0, first_open - 1)
    if start == 1:  # collapsing a single node saves nothing
        start = 0
    shown = nodes_in_track[start:start + 6]
    if start > 0:
        toks.append((f"✅×{start}", None))
    for n in shown:
        toks.append((_token(n), n["id"]))
    rest = len(nodes_in_track) - start - len(shown)
    line = ""
    prev_id = nodes_in_track[start - 1]["id"] if start > 0 else None
    for tok, nid in toks:
        if line:
            sep = " → " if (nid is None or prev_id is None or (prev_id, nid) in edge_set) else " · "
            line += sep
        line += tok
        prev_id = nid if nid else prev_id
    if rest > 0:
        line += f" … +{rest}"
    return line


def render_text(title, plan, prog, model, done, total):
    legend = "✅ done · ▶ ready · 🔁 retry · 🔒 locked · ⭐ junction"
    lines = [f"🗺 {title} — {done}/{total}", legend, ""]
    nodes = {n["id"]: n for n in model["nodes"]}
    edge_set = set(map(tuple, model["edges"]))
    if model["level"] == "units":
        for t in model["tracks"]:
            lines.append(f"{ICON[nodes[t['id']]['status']]} {t['id']} · {t['title']}  {t['done']}/{t['total']}")
        for a, b in model["edges"]:
            lines.append(f"  {a} ──► {b}")
        return "\n".join(lines)
    by_unit = defaultdict(list)
    for n in model["nodes"]:
        by_unit[n["unit"]].append(n)
    for t in model["tracks"]:
        fill = round(10 * t["done"] / t["total"]) if t["total"] else 0
        lines.append(f"{t['id']} · {t['title']}  {'█' * fill}{'░' * (10 - fill)} {t['done']}/{t['total']}")
        lines.append("  " + _chain_line(by_unit[t["id"]], edge_set))
    cross = _junction_needs(model)
    if cross:
        lines += ["", "Junctions (several skills at once):"]
        for n, needs in cross:
            what = " + ".join(f"{r} ({_short(nodes[r]['label'], 22)})" for r in needs)
            lines.append(f"  ⭐{ICON[n['status']]} {n['id']} {_short(n['label'], 30)} ← needs {what}")
    ready = [n for n in model["nodes"] if n["status"] in ("available", "retry")]
    if ready:
        lines += ["", "Ready now: " + " · ".join(f"{n['id']} {_short(n['label'], 24)} [{n['unit']}]" for n in ready[:8])]
    return "\n".join(lines)


def render_md(title, plan, prog, model, done, total):
    """Rich-Markdown (Telegram rich messages / Obsidian): a table, no Mermaid."""
    pct = round(100 * done / total) if total else 0
    lines = [f"**🗺 {title}** — {done}/{total} ({pct}%)", ""]
    if model["level"] == "units":
        lines += ["| Track | Progress | State |", "|---|---|---|"]
        for t in model["tracks"]:
            n = next(x for x in model["nodes"] if x["id"] == t["id"])
            lines.append(f"| {t['title']} | {t['done']}/{t['total']} | {ICON[n['status']]} |")
        return "\n".join(lines)
    nodes = {n["id"]: n for n in model["nodes"]}
    lines += ["| Track | Progress | Ready now |", "|---|---|---|"]
    for t in model["tracks"]:
        fill = round(8 * t["done"] / t["total"]) if t["total"] else 0
        ready = ", ".join(f"{r} {_short(nodes[r]['label'], 22)}" for r in t["ready"][:3]) or "—"
        lines.append(f"| {t['title']} | {'█' * fill}{'░' * (8 - fill)} {t['done']}/{t['total']} | {ready} |")
    junc = []
    for n, needs in _junction_needs(model):
        junc.append(f"- {ICON[n['status']]} **{n['id']}** {n['label']} — needs "
                    + ", ".join(f"{r} ({nodes[r]['unit']}) {ICON[nodes[r]['status']]}" for r in needs))
    if junc:
        lines += ["", "**⭐ Junctions** (several skills at once)", *junc]
    return "\n".join(lines)


def render_mermaid(title, plan, prog, model, done=None, total=None):
    """Mermaid `graph TD` for Obsidian / GitHub (not Telegram)."""
    def esc(t):
        return " ".join(str(t).replace('"', "'").replace("[", "(").replace("]", ")").split())

    lines = ["graph TD"]
    if model["level"] == "units":
        for n in model["nodes"]:
            lines.append(f'  {n["id"]}["{esc(n["label"])}<br/>{n["short"]}"]:::{n["status"]}')
    else:
        by_unit = defaultdict(list)
        for n in model["nodes"]:
            by_unit[n["unit"]].append(n)
        for t in model["tracks"]:
            lines.append(f'  subgraph {t["id"]}["{esc(t["title"])} {t["done"]}/{t["total"]}"]')
            for n in by_unit[t["id"]]:
                label = f'{n["id"]} {esc(_short(n["label"], 28))}'
                shape = ('{{"%s"}}' % label) if n["kind"] == "junction" else ('["%s"]' % label)
                lines.append(f'    {n["id"]}{shape}:::{n["status"]}')
            lines.append("  end")
    for a, b in model["edges"]:
        lines.append(f"  {a} --> {b}")
    lines += [f"  classDef {s} fill:{c},stroke:#455a64;" for s, c in STATUS_FILL.items()]
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# SVG: one column per track, prerequisites always above what they unlock
# ----------------------------------------------------------------------------

SVG_TEXT = {
    "en": {"done": "done", "available": "ready", "retry": "retry", "locked": "locked", "skipped": "skipped",
           "junction": "junction", "today": "today", "of": "lessons done", "ready_now": "ready now",
           "retries": "to retry", "today_list": "Today", "ready_list": "Ready now", "finished": "All done 🎉",
           "goal": "Goal", "speed": "Speed", "per_day": "{a} of {n} lessons/day needed"},
    "ru": {"done": "пройдено", "available": "готово", "retry": "пересдать", "locked": "закрыто",
           "skipped": "пропущено", "junction": "проект", "today": "сегодня", "of": "уроков пройдено",
           "ready_now": "готово сейчас", "retries": "пересдать", "today_list": "Сегодня",
           "ready_list": "Готово сейчас", "finished": "Всё пройдено 🎉", "goal": "Цель",
           "speed": "Скорость", "per_day": "{a} из {n} нужных уроков/день"},
}
FONT = "'DejaVu Sans', 'Noto Sans', 'Segoe UI', Helvetica, Arial, sans-serif"
INK, MUTED, FAINT, TODAY = "#263238", "#607d8b", "#b0bec5", "#f9a825"
COL_W, ROW_H, NODE_R, TOP = 212, 62, 17, 162
GOAL_R = 30
GUTTER = 28  # lane-internal "bus" left of the nodes, for edges that pass nodes of their own lane


def _to_gutter(x, y):
    """Path start: leave a node from its bottom and swing into the lane's left gutter."""
    return f"M{x:.1f},{y:.1f} C{x:.1f},{y + 14:.1f} {x - GUTTER:.1f},{y + 6:.1f} {x - GUTTER:.1f},{y + 28:.1f}"


def _text_w(text, size, bold=False):
    """Rough rendered width, calibrated on DejaVu Sans (the widest common fallback), so wrapped labels fit."""
    def em(ch):
        if ch in " .,:;!|il'()[]ftj":
            return 0.34
        if ch.isdigit():
            return 0.6
        cyr = "\u0400" <= ch <= "\u04ff"
        if ch.isupper():
            return 0.73 if cyr else 0.68
        return 0.66 if cyr else 0.57
    return sum(em(ch) for ch in str(text)) * size * (1.1 if bold else 1)


def _wrap(text, size, width, lines=2, bold=False):
    words, out, cur = " ".join(str(text).split()).split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if _text_w(trial, size, bold) <= width or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] += " …"
    for i, line in enumerate(out):  # a single word longer than the box
        while _text_w(line, size, bold) > width and len(line) > 2:
            line = line[:-2] + "…"
        out[i] = line
    return out


def _column_order(tracks, edges, unit):
    """Order the track columns so that cross-track edges stay short (exhaustive up to 7 tracks)."""
    import itertools
    ids = [t["id"] for t in tracks]
    cross = [(unit[a], unit[b]) for a, b in edges if unit[a] != unit[b]]
    if len(ids) > 7 or not cross:
        return ids
    orig = {t: i for i, t in enumerate(ids)}

    def cost(perm):
        at = {t: i for i, t in enumerate(perm)}
        return (sum(abs(at[a] - at[b]) for a, b in cross), sum(abs(at[t] - orig[t]) for t in perm))
    return list(min(itertools.permutations(ids), key=cost))


def _layout(model):
    nodes = {n["id"]: n for n in model["nodes"]}
    preds = defaultdict(list)
    for a, b in model["edges"]:
        preds[b].append(a)
    depth = {}

    def d(n):
        if n not in depth:
            depth[n] = 0 if not preds[n] else 1 + max(d(p) for p in preds[n])
        return depth[n]

    for n in nodes:
        d(n)
    index = {n["id"]: i for i, n in enumerate(model["nodes"])}
    cols = _column_order(model["tracks"], model["edges"], {i: n["unit"] for i, n in nodes.items()})
    col = {n: cols.index(nodes[n]["unit"]) for n in nodes}
    row, last = {}, defaultdict(lambda: -1)
    for n in sorted(nodes, key=lambda i: (depth[i], index[i])):  # topological; two nodes never share a cell
        row[n] = max([row[p] + 1 for p in preds[n]] + [last[col[n]] + 1, 0])
        last[col[n]] = row[n]
    return cols, col, row, preds


def render_svg(title, plan, prog, model, done=None, total=None, today=None, picks=(), lang="en", scale=1, speed=None):
    """A swimlane map: one column per track, rows follow the prerequisites, and every
    branch flows into one final point — the plan's goal.

    Fill = status (track colour when done), outline = track, diamond = junction,
    gold ring = today's lessons. All arrows into a node meet at its top. `speed` (learn.speed_report)
    adds a badge: green at or above the needed pace, orange from 0.8, red below.
    `scale` multiplies the pixel size only (crisper PNG).
    """
    L = SVG_TEXT["ru" if str(lang).lower().startswith("ru") else "en"]
    nodes = {n["id"]: n for n in model["nodes"]}
    tracks = {t["id"]: t for t in model["tracks"]}
    tindex = {t["id"]: i for i, t in enumerate(model["tracks"])}
    cols, col, row, preds = _layout(model)
    picks = [p for p in picks if p in nodes]
    nrows = max(row.values(), default=0) + 1
    margin = 20
    width = max(len(cols) * COL_W + 2 * margin, 640)
    left = (width - len(cols) * COL_W) / 2
    body_h = nrows * ROW_H
    lane_bottom = TOP + 14 + body_h
    has_succ = {a for a, _ in model["edges"]}
    sinks = [i for i in nodes if i not in has_succ]  # the ends of the branches: they all lead to the goal
    goal_text = plan.get("goal") or title
    gx, gy = width / 2, lane_bottom + 46 + GOAL_R
    goal_lines = _wrap(f"{L['goal']}: {goal_text}", 13, min(width - 2 * margin, 520), 2, True)
    height = gy + GOAL_R + 26 + 17 * len(goal_lines) + 52

    def color(unit):
        return TRACK_COLORS[tindex[unit] % len(TRACK_COLORS)]

    def xy(nid):
        return left + col[nid] * COL_W + 34, TOP + 22 + row[nid] * ROW_H + ROW_H / 2

    def radius(nid):
        return NODE_R + 6 if nodes[nid]["kind"] in ("junction", "unit") else NODE_R

    def esc(t):
        return xml.escape(str(t))

    def text(x, y, s, size=11, fill=INK, weight="normal", anchor="start", halo=False, extra=""):
        h = ' stroke="#ffffff" stroke-width="3.5" stroke-linejoin="round" paint-order="stroke"' if halo else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                f'text-anchor="{anchor}"{h}{extra}>{esc(s)}</text>')

    W, H = width * scale, height * scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">',
           f"<title>{esc(title)}</title>", "<defs>"]
    for i, c in enumerate(TRACK_COLORS + [FAINT]):
        out.append(f'<marker id="ar{i}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5.5" markerHeight="5.5" '
                   f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
    out += ["</defs>", '<rect width="100%" height="100%" fill="#ffffff"/>']

    # --- header: title, overall progress, what to do next ---
    done = sum(1 for n in model["nodes"] if n["status"] == "done") if done is None else done
    total = len(model["nodes"]) if total is None else total
    head_w = width - 2 * margin  # room left of the speed badge
    if speed and speed.get("speed") is not None:
        def num(v, fmt):
            return format(v, fmt).replace(".", ",") if L is SVG_TEXT["ru"] else format(v, fmt)
        v = speed["speed"]
        tone = "#2e7d32" if v >= 1 else "#ef6c00" if v >= 0.8 else "#c62828"
        big = f"{L['speed']} {num(v, '.2f')}×"
        small = L["per_day"].format(a=num(speed["lessons_per_day"], ".1f"), n=num(speed["needed_per_day"], ".1f"))
        pw = max(_text_w(big, 16, True), _text_w(small, 11)) + 28
        px = width - margin - pw
        out.append(f'<g><title>{esc(big)} = {esc(small)}</title>'
                   f'<rect x="{px:.1f}" y="14" width="{pw:.1f}" height="50" rx="10" fill="{tone}" fill-opacity="0.1" '
                   f'stroke="{tone}" stroke-width="1.5"/>'
                   + text(px + pw / 2, 36, big, 16, tone, "bold", "middle")
                   + text(px + pw / 2, 54, small, 11, tone, anchor="middle") + "</g>")
        head_w -= pw + 16
    tsize = 22
    while _text_w(title, tsize, True) > head_w and tsize > 14:
        tsize -= 1
    out.append(text(margin, 38, _wrap(title, tsize, head_w, 1, True)[0], tsize, INK, "bold"))
    pct = round(100 * done / total) if total else 0
    statuses = [n["status"] for n in model["nodes"]]
    stats = [f"{done}/{total} {L['of']} · {pct}%"]
    if model["level"] == "lessons":
        stats.append(f"{L['ready_now']}: {statuses.count('available') + statuses.count('retry')}")
        if statuses.count("retry"):
            stats.append(f"{L['retries']}: {statuses.count('retry')}")
    if today:
        stats.append(".".join(reversed(today.split("-"))) if L is SVG_TEXT["ru"] else today)
    out.append(text(margin, 62, _wrap(" · ".join(stats), 13, head_w, 1)[0], 13, MUTED))
    bar_w = width - 2 * margin
    out.append(f'<rect x="{margin}" y="74" width="{bar_w}" height="8" rx="4" fill="#eceff1"/>')
    if total and done:
        out.append(f'<rect x="{margin}" y="74" width="{max(8, bar_w * done / total):.1f}" height="8" rx="4" fill="#43a047"/>')
    if picks:
        head, items = L["today_list"], [nodes[p] for p in picks]
    else:
        head, items = L["ready_list"], [n for n in model["nodes"] if n["status"] in ("available", "retry")]
    if items and model["level"] == "lessons":
        line = f"{head}: " + " · ".join(f"{n['id']} {n['label']}" if len(items) <= 2 else n["id"] for n in items[:12])
        out.append(text(margin, 104, _wrap(line, 13, bar_w, 1, True)[0], 13, INK, "bold"))
    elif total and done == total:
        out.append(text(margin, 104, L["finished"], 13, "#2e7d32", "bold"))

    # --- columns: tinted lane, title, mini progress bar ---
    for i, tid in enumerate(cols):
        t, c = tracks[tid], color(tid)
        x0 = left + i * COL_W
        out.append(f'<rect x="{x0 + 4:.1f}" y="{TOP - 44}" width="{COL_W - 8}" height="{body_h + 58}" rx="12" '
                   f'fill="{c}" fill-opacity="0.045"/>')
        out.append(f'<rect x="{x0 + 4:.1f}" y="{TOP - 44}" width="{COL_W - 8}" height="4" rx="2" fill="{c}"/>')
        for k, line in enumerate(_wrap(t["title"], 12, COL_W - 28, 2, True)):
            out.append(text(x0 + 14, TOP - 22 + k * 15, line, 12, c, "bold"))
        bw = COL_W - 80
        out.append(f'<rect x="{x0 + 14:.1f}" y="{TOP + 4}" width="{bw}" height="6" rx="3" fill="{c}" fill-opacity="0.15"/>')
        if t["done"]:
            out.append(f'<rect x="{x0 + 14:.1f}" y="{TOP + 4}" width="{max(6, bw * t["done"] / t["total"]):.1f}" '
                       f'height="6" rx="3" fill="{c}"/>')
        out.append(text(x0 + COL_W - 14, TOP + 11, f"{t['done']}/{t['total']}", 11, MUTED, anchor="end"))

    # --- edges, under the nodes ---
    in_col = defaultdict(list)
    for nid in sorted(nodes, key=lambda i: row[i]):
        in_col[col[nid]].append(nid)
    edges_out = []
    for a, b in model["edges"]:
        (x1, y1), (x2, y2) = xy(a), xy(b)
        walked = nodes[a]["status"] in SATISFIED
        same = col[a] == col[b]
        mi = tindex[nodes[a]["unit"]] % len(TRACK_COLORS) if (walked or not same) else len(TRACK_COLORS)
        stroke = TRACK_COLORS[mi] if mi < len(TRACK_COLORS) else FAINT
        opacity = 0.9 if walked else (0.5 if not same else 1)
        width_ = 2.2 if same and in_col[col[a]].index(b) - in_col[col[a]].index(a) == 1 else 1.5
        style = f'fill="none" stroke="{stroke}" stroke-width="{width_}" stroke-opacity="{opacity}" marker-end="url(#ar{mi})"'
        if same:
            seq = in_col[col[a]]
            if seq.index(b) - seq.index(a) == 1:  # neighbours in the lane: a straight line
                edges_out.append(f'<line x1="{x1:.1f}" y1="{y1 + radius(a) + 2:.1f}" x2="{x2:.1f}" '
                                 f'y2="{y2 - radius(b) - 3:.1f}" {style}/>')
            else:  # skips nodes of its own lane: down the left gutter, back in from the top
                sy, ey = y1 + radius(a) + 2, y2 - radius(b) - 3
                edges_out.append(f'<path d="{_to_gutter(x1, sy)} L{x1 - GUTTER:.1f},{ey - 28:.1f} '
                                 f'C{x1 - GUTTER:.1f},{ey - 6:.1f} {x2:.1f},{ey - 14:.1f} {x2:.1f},{ey:.1f}" {style}/>')
            continue
        sx, sy, ex, ey = x1, y1 + radius(a) + 2, x2, y2 - radius(b) - 3
        bend = min(max(28.0, (ey - sy) * 0.5), ROW_H * 1.2)
        edges_out.append(f'<path d="M{sx:.1f},{sy:.1f} C{sx:.1f},{sy + bend:.1f} {ex:.1f},{ey - bend:.1f} '
                         f'{ex:.1f},{ey:.1f}" {style}/>')
    # every branch ends in the goal: down its own lane (through the left gutter when the lane goes on), then in
    for a in sinks:
        x1, y1 = xy(a)
        walked = nodes[a]["status"] in SATISFIED
        mi = tindex[nodes[a]["unit"]] % len(TRACK_COLORS)
        style = (f'fill="none" stroke="{TRACK_COLORS[mi]}" stroke-width="1.6" stroke-opacity="{0.9 if walked else 0.45}" '
                 f'marker-end="url(#ar{mi})"')
        sy = y1 + radius(a) + 2
        if in_col[col[a]][-1] == a:
            lane = f"M{x1:.1f},{sy:.1f} L{x1:.1f},{lane_bottom - 6:.1f}"
        else:
            lane = f"{_to_gutter(x1, sy)} L{x1 - GUTTER:.1f},{lane_bottom - 6:.1f}"
            x1 -= GUTTER
        edges_out.append(f'<path d="{lane} C{x1:.1f},{lane_bottom + 24:.1f} {gx:.1f},{gy - GOAL_R - 36:.1f} '
                         f'{gx:.1f},{gy - GOAL_R - 3:.1f}" {style}/>')
    out += edges_out

    # --- nodes ---
    for nid, n in nodes.items():
        x, y = xy(nid)
        c, st = color(n["unit"]), n["status"]
        r = radius(nid)
        fill, ring, ring_op, idc, labc, weight = {
            "done": (c, c, 1, "#ffffff", INK, "normal"),
            "available": ("#ffffff", c, 1, INK, INK, "bold"),
            "retry": ("#ffe0b2", "#ef6c00", 1, INK, "#bf360c", "bold"),
            "locked": ("#f7f9fa", c, 0.35, MUTED, MUTED, "normal"),
            "skipped": ("#f5f5f5", FAINT, 1, MUTED, MUTED, "normal"),
        }.get(st, ("#ffffff", c, 1, INK, INK, "normal"))
        g = [f'<g><title>{esc(nid)} — {esc(n["label"])} ({esc(L.get(st, st))})</title>']
        if nid in picks:
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 7}" fill="none" stroke="{TODAY}" stroke-width="3"/>')
        elif st in ("available", "retry"):
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 6}" fill="{ring}" fill-opacity="0.16"/>')
        dash = ' stroke-dasharray="4 3"' if st == "skipped" else ""
        common = f'fill="{fill}" stroke="{ring}" stroke-opacity="{ring_op}" stroke-width="3"{dash}'
        if n["kind"] == "junction":
            g.append(f'<polygon points="{x:.1f},{y - r:.1f} {x + r:.1f},{y:.1f} {x:.1f},{y + r:.1f} {x - r:.1f},{y:.1f}" '
                     f'stroke-linejoin="round" {common}/>')
        else:
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" {common}/>')
        g.append(text(x, y + 4, n["short"], 10.5 if len(n["short"]) > 3 else 11.5, idc, "bold", "middle"))
        if st == "retry":
            g.append(f'<circle cx="{x + r * 0.75:.1f}" cy="{y - r * 0.75:.1f}" r="6.5" fill="#ef6c00"/>'
                     + text(x + r * 0.75, y - r * 0.75 + 3.5, "!", 10, "#ffffff", "bold", "middle"))
        lab = n["label"] if model["level"] == "lessons" else ""
        lines = _wrap(lab, 11.5, COL_W - 70, 2, weight == "bold")
        ty = y + 4 - (len(lines) - 1) * 7
        for k, line in enumerate(lines):
            g.append(text(x + r + (14 if nid in picks else 10), ty + k * 14, line, 11.5, labc,
                          weight, halo=True, extra=' text-decoration="line-through"' if st == "skipped" else ""))
        g.append("</g>")
        out.append("".join(g))

    # --- the goal: where every branch meets ---
    finished = bool(total) and done == total
    ring = "#2e7d32" if finished else INK
    out.append(f'<g><title>{esc(L["goal"])}: {esc(goal_text)}</title>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R + 7}" fill="{ring}" fill-opacity="0.08"/>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R}" fill="{"#43a047" if finished else "#ffffff"}" '
               f'stroke="{ring}" stroke-width="3.5"/>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R - 6}" fill="none" stroke="{ring}" stroke-opacity="0.25" '
               f'stroke-width="1.5"/>'
               + text(gx, gy + 5, f"{pct}%", 14, "#ffffff" if finished else INK, "bold", "middle") + "</g>")
    for k, line in enumerate(goal_lines):
        out.append(text(gx, gy + GOAL_R + 24 + k * 17, line, 13, INK, "bold", "middle"))

    # --- legend ---
    ly = gy + GOAL_R + 24 + 17 * len(goal_lines) + 22
    lx = margin
    swatch = [("done", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="{TRACK_COLORS[0]}" stroke="{TRACK_COLORS[0]}" stroke-width="2"/>'),
              ("available", f'<circle cx="{{x}}" cy="{ly}" r="10" fill="{TRACK_COLORS[0]}" fill-opacity="0.16"/>'
                            f'<circle cx="{{x}}" cy="{ly}" r="7" fill="#fff" stroke="{TRACK_COLORS[0]}" stroke-width="2"/>'),
              ("retry", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="#ffe0b2" stroke="#ef6c00" stroke-width="2"/>'),
              ("locked", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="#f7f9fa" stroke="{TRACK_COLORS[0]}" '
                         f'stroke-opacity="0.35" stroke-width="2"/>'),
              ("junction", f'<polygon points="{{x}},{ly - 9} {{x9}},{ly} {{x}},{ly + 9} {{x_9}},{ly}" fill="#fff" '
                           f'stroke="{MUTED}" stroke-width="2"/>')]
    if picks:
        swatch.append(("today", f'<circle cx="{{x}}" cy="{ly}" r="9" fill="none" stroke="{TODAY}" stroke-width="2.5"/>'))
    for key, shape in swatch:
        cx = lx + 10
        out.append(shape.format(x=cx, x9=cx + 9, x_9=cx - 9) + text(cx + 16, ly + 4, L[key], 12, MUTED))
        lx += 34 + _text_w(L[key], 12)
    out.append("</svg>")
    return "\n".join(out)


def svg_to_png(svg_path, png_path):
    """Convert with whichever converter is installed; raise RuntimeError if none works.

    The pixel size comes from the SVG's width/height (render_svg(scale=...)), so every converter
    renders at the same resolution."""
    final = Path(png_path)
    svg_path, png_path = str(svg_path), str(final.with_name(f".tmp-{os.getpid()}-{final.name}"))  # atomic: rename at the end
    attempts = [
        ("rsvg-convert", ["rsvg-convert", "-o", png_path, svg_path]),
        ("inkscape", ["inkscape", svg_path, "--export-type=png", f"--export-filename={png_path}"]),
        ("magick", ["magick", "-background", "white", svg_path, png_path]),
        ("convert", ["convert", "-background", "white", svg_path, png_path]),
    ]
    tried = []
    for exe, cmd in attempts:
        if not shutil.which(exe):
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and Path(png_path).exists() and Path(png_path).stat().st_size > 0:
            os.replace(png_path, final)
            return exe
        tried.append(exe)
        Path(png_path).unlink(missing_ok=True)
    raise RuntimeError("no working SVG→PNG converter" + (f" (tried {', '.join(tried)})" if tried else "")
                       + ". Install one: `apt install librsvg2-bin` (rsvg-convert), or send the .svg / use --format md or text.")


def migration_warnings(old_plan, new_plan):
    """Linear -> graph: units stop being sequential and become parallel tracks."""
    if graph_mode(old_plan) or not graph_mode(new_plan):
        return []
    loose = [u["id"] for u in new_plan["units"][1:]
             if u["lessons"] and "requires" not in u["lessons"][0]]
    if not loose:
        return []
    return ["this plan was linear and is now a graph: units become parallel tracks. "
            f"Units {', '.join(loose)} had followed the previous unit; their first lesson now has no prerequisite. "
            "Add `requires` to their first lesson if they must stay sequential."]
