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

import math
import os
import shutil
import subprocess
import xml.sax.saxutils as xml
from collections import defaultdict, deque
from pathlib import Path

SATISFIED = ("done", "skipped")
ICON = {"done": "✅", "skipped": "⏭", "retry": "🔁", "available": "▶", "locked": "🔒"}
STATUS_FILL = {"done": "#a5d6a7", "skipped": "#e0e0e0", "retry": "#ffcc80",
               "available": "#fff59d", "locked": "#eceff1"}
TRACK_COLORS = ["#3f51b5", "#009688", "#e91e63", "#ff9800", "#795548", "#607d8b", "#8bc34a", "#9c27b0",
                "#1565c0", "#00838f", "#c62828", "#827717", "#6a1b9a", "#ad1457", "#00695c", "#ef6c00",
                "#283593", "#558b2f", "#4e342e", "#0277bd", "#4527a0", "#880e4f", "#37474f", "#bf360c"]


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
    """Return one cycle as a list of ids (first == last), or None.

    Iterative depth-first search: a plan with a 1000-lesson chain must not hit Python's recursion limit."""
    state = {}  # 1 = on the current path, 2 = finished
    for root in reqs:
        if root in state:
            continue
        path, stack = [root], [iter(reqs.get(root, []))]
        state[root] = 1
        while stack:
            for m in stack[-1]:
                if state.get(m) == 1:
                    return path[path.index(m):] + [m]
                if m not in state:
                    state[m] = 1
                    path.append(m)
                    stack.append(iter(reqs.get(m, [])))
                    break
            else:
                state[path.pop()] = 2
                stack.pop()
    return None


def unit_requires(plan):
    """track id -> the other tracks its lessons need (the edges of the one-point-per-track map)."""
    units, out = unit_of(plan), {u["id"]: [] for u in plan["units"]}
    for lid, rs in requires_map(plan).items():
        for r in rs:
            if r in units and units[r] != units[lid] and units[r] not in out[units[lid]]:
                out[units[lid]].append(units[r])
    return out


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
    cyc = find_cycle(unit_requires(plan))
    if cyc:
        out.append(f"tracks need each other in a circle ({' → '.join(cyc)}, each needs the next): fine for lessons, "
                   "but on the one-point-per-track map some arrows point up. To avoid it, move the lesson that "
                   "closes the circle into the other track or make it a junction in a projects track")
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

def build_model(plan, prog, level="lessons", track=None):
    """Nodes, edges and per-track progress; `track` keeps one track only (its inner edges)."""
    model = _build_model(plan, prog, level)
    if track is None:
        return model
    if track not in {t["id"] for t in model["tracks"]}:
        raise KeyError(f"no such track: {track} (have: {', '.join(t['id'] for t in model['tracks'])})")
    keep = {n["id"] for n in model["nodes"] if n["unit"] == track}
    return dict(model, nodes=[n for n in model["nodes"] if n["id"] in keep],
                edges=[(a, b) for a, b in model["edges"] if a in keep and b in keep],
                tracks=[t for t in model["tracks"] if t["id"] == track], track=track)


def _build_model(plan, prog, level):
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
           "skipped": "пропущено", "junction": "соединение", "today": "сегодня", "of": "уроков пройдено",
           "ready_now": "готово сейчас", "retries": "пересдать", "today_list": "Сегодня",
           "ready_list": "Готово сейчас", "finished": "Всё пройдено 🎉", "goal": "Цель",
           "speed": "Скорость", "per_day": "{a} из {n} нужных уроков/день"},
}
FONT = "'DejaVu Sans', 'Noto Sans', 'Segoe UI', Helvetica, Arial, sans-serif"
INK, MUTED, FAINT, TODAY = "#263238", "#607d8b", "#b0bec5", "#f9a825"
COL_W, ROW_H, NODE_R, TOP = 212, 62, 17, 162
UNIT_COLS = 4  # units map: a grid this many points wide, whatever the number of tracks
PHONE_PX, PHONE_COLS, MIN_FONT = 1400, 4, 10  # wider lesson maps use a compact DAG instead of lanes
COMPACT_COLS, COMPACT_COL_W = 8, 116
GOAL_R = 30
GUTTER = 28  # lane-internal "bus" left of the nodes, for edges that pass nodes of their own lane

# The map must look the same in every SVG→PNG converter. ImageMagick's built-in renderer (what `convert`
# uses on a stock Ubuntu/Debian) ignores fill-/stroke-opacity, paint-order and markers, so the SVG uses
# none of them: translucent colours are pre-mixed with the white page, halos are a separate text drawn
# first, and arrowheads are polygons.


def _tint(color, opacity):
    """`color` at `opacity` over the white page, as one opaque hex colour."""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    rgb = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{round(255 - (255 - v) * opacity):02x}" for v in rgb)


def _arrowhead(x, y, dx, dy, color, stroke_w=1.5):
    """A filled arrowhead whose tip is just past the path end (x, y), pointing along (dx, dy)."""
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    length, half = 4.5 + 2 * stroke_w, 2 + 1.2 * stroke_w
    tx, ty = x + ux * 1.5, y + uy * 1.5
    bx, by = tx - ux * length, ty - uy * length
    return (f'<polygon class="arrow" points="{tx:.1f},{ty:.1f} {bx - uy * half:.1f},{by + ux * half:.1f} '
            f'{bx + uy * half:.1f},{by - ux * half:.1f}" fill="{color}"/>')


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


def _depths(order, preds):
    """Longest prerequisite chain above each node, without recursion (Kahn's order).

    Lessons never form a cycle (validate refuses it), but tracks may need each other both ways; then the
    stuck node with the fewest unplaced prerequisites (first in `order` on a tie) is placed as if those were not
    there — each of them becomes one arrow pointing back up, so this keeps such arrows few — and the rest follows."""
    known = set(order)
    ps = {n: (set(preds[n]) & known) - {n} for n in order}
    succ, waiting = defaultdict(list), {n: len(ps[n]) for n in order}
    for n in order:
        for p in ps[n]:
            succ[p].append(n)
    depth, queue = {}, deque(n for n in order if not waiting[n])
    while len(depth) < len(order):
        if not queue:  # a cycle: break it where the fewest arrows have to point back
            queue.append(min((n for n in order if n not in depth), key=lambda n: waiting[n]))
        n = queue.popleft()
        if n in depth:
            continue
        depth[n] = 1 + max((depth[p] for p in ps[n] if p in depth), default=-1)
        for m in succ[n]:
            waiting[m] -= 1
            if not waiting[m]:
                queue.append(m)
    return depth


def _layout(model):
    nodes = {n["id"]: n for n in model["nodes"]}
    preds = defaultdict(list)
    for a, b in model["edges"]:
        preds[b].append(a)
    depth = _depths(list(nodes), preds)
    index = {n["id"]: i for i, n in enumerate(model["nodes"])}
    if model["level"] == "units":  # no lanes: rows follow the prerequisites, wrapped UNIT_COLS wide
        ncols = min(UNIT_COLS, len(nodes))
        col, row, load, r, used, prev = {}, {}, [0] * ncols, -1, set(), None
        for n in sorted(nodes, key=lambda i: (depth[i], index[i])):
            if depth[n] != prev or len(used) == ncols:
                r, used, prev = r + 1, set(), depth[n]
            # the emptiest free column (so no column piles up), nearest to the prerequisites (short arrows)
            near = [col[p] for p in preds[n] if p in col]
            aim = sum(near) / len(near) if near else 0
            c = min((c for c in range(ncols) if c not in used), key=lambda c: (load[c], abs(c - aim), c))
            col[n], row[n] = c, r
            used.add(c)
            load[c] += 1
        return list(range(ncols)), col, row, preds
    cols = _column_order(model["tracks"], model["edges"], {i: n["unit"] for i, n in nodes.items()})
    col = {n: cols.index(nodes[n]["unit"]) for n in nodes}
    row, last = {}, defaultdict(lambda: -1)
    for n in sorted(nodes, key=lambda i: (depth[i], index[i])):  # topological; two nodes never share a cell
        row[n] = max([row[p] + 1 for p in preds[n]] + [last[col[n]] + 1, 0])
        last[col[n]] = row[n]
    return cols, col, row, preds


def _compact_layout(model):
    """Pack each dependency level separately, letting branches bend towards joins.

    Rows hold at most COMPACT_COLS lessons. Barycentric sweeps order neighbours
    and move each row's compact block towards its prerequisites and successors;
    x positions are continuous, not permanent track columns.
    """
    nodes = {n["id"]: n for n in model["nodes"]}
    preds, succ = defaultdict(list), defaultdict(list)
    for a, b in model["edges"]:
        preds[b].append(a)
        succ[a].append(b)
    depth = _depths(list(nodes), preds)
    layers = defaultdict(list)
    index = {n: i for i, n in enumerate(nodes)}
    row = {}
    for nid in sorted(nodes, key=lambda n: (depth[n], index[n])):
        r = max([row[p] + 1 for p in preds[nid]] + [depth[nid]])
        while len(layers[r]) == COMPACT_COLS:
            r += 1
        row[nid] = r
        layers[r].append(nid)
    ncols = max(map(len, layers.values()), default=1)
    col = {n: (ncols - len(ids)) / 2 + i for ids in layers.values() for i, n in enumerate(ids)}

    def pack(r, neighbours, reorder=False):
        ids = layers[r]
        aims = {n: sum(col[p] for p in neighbours[n]) / len(neighbours[n]) if neighbours[n] else col[n]
                for n in ids}
        if r and reorder:  # preserve the plan's root order, as in the reference map
            ids.sort(key=lambda n: (aims[n], col[n], index[n]))
        # A row shrinks when branches end, instead of leaving holes in fixed lanes.
        half = (len(ids) - 1) / 2
        centre = max(half, min(ncols - 1 - half, sum(aims.values()) / len(ids)))
        for i, n in enumerate(ids):
            col[n] = centre + i - half

    for r in sorted(layers):
        pack(r, preds, reorder=True)
    # Relax whole row blocks without swapping established branch order. A late
    # cross-track requirement must not make two otherwise parallel chains cross.
    for _ in range(3):
        for r in sorted(layers):
            pack(r, preds)
        for r in sorted(layers, reverse=True):
            pack(r, succ)
    for r in sorted(layers):
        pack(r, preds)
    return list(range(ncols)), col, row, preds


def _smooth_path(points):
    """Monotone vertical Beziers through routing points, without right-angle elbows."""
    x, y = points[0]
    parts = [f"M{x:.1f},{y:.1f}"]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        bend = (y2 - y1) * 0.45
        parts.append(f"C{x1:.1f},{y1 + bend:.1f} {x2:.1f},{y2 - bend:.1f} {x2:.1f},{y2:.1f}")
    return " ".join(parts)


def render_svg(title, plan, prog, model, done=None, total=None, today=None, picks=(), lang="en", scale=1, speed=None,
               max_px=None):
    """A lesson map: narrow plans have track lanes, wide plans a compact DAG.
    Rows follow the prerequisites; the goal summary sits below the graph.

    Fill = status (green/yellow/orange/grey), outline = track, diamond = convergence,
    gold ring = today's lessons. All arrows into a node meet at its top. `speed` (learn.speed_report)
    adds a badge: green at or above the needed pace, orange from 0.8, red below.
    `scale` multiplies the pixel size only (crisper PNG); `max_px` caps the pixel width (lowers the scale).
    The units level has no lanes: one point per track, laid out as a grid.
    """
    L = SVG_TEXT["ru" if str(lang).lower().startswith("ru") else "en"]
    nodes = {n["id"]: n for n in model["nodes"]}
    tracks = {t["id"]: t for t in model["tracks"]}
    tindex = {t["id"]: i for i, t in enumerate(model["tracks"])}
    compact = model["level"] == "lessons" and len(tracks) > PHONE_COLS
    cols, col, row, preds = _compact_layout(model) if compact else _layout(model)
    picks = [p for p in picks if p in nodes]
    nrows = max(row.values(), default=0) + 1
    margin = 20
    lanes = model["level"] != "units" and not compact
    col_w = COMPACT_COL_W if compact else COL_W if len(cols) > 1 or not lanes else 2 * COL_W
    width = max(len(cols) * col_w + 2 * margin, 640)
    if max_px:
        scale = min(scale, max_px / width)
    left = (width - len(cols) * col_w) / 2
    row_h = ROW_H

    def color(unit):
        return TRACK_COLORS[tindex[unit] % len(TRACK_COLORS)]

    def xy(nid):
        return (left + col[nid] * col_w + (col_w / 2 if compact else 34),
                top + layer_y[row[nid]] if compact else top + 22 + row[nid] * row_h + row_h / 2)

    def id_size(nid):
        return 10.5 if len(nodes[nid]["short"]) > 3 else 11.5

    def junction(nid):
        return nodes[nid]["kind"] == "junction" or len(preds[nid]) > 1

    def radius(nid):
        """Wide enough for the id inside it: long ids never spill over the ring."""
        base = NODE_R + 6 if junction(nid) or nodes[nid]["kind"] == "unit" else NODE_R
        inner = _text_w(nodes[nid]["short"], id_size(nid), True) / 2 + 5
        return max(base, round(inner * (1.45 if junction(nid) else 1), 1))

    layer_nodes = defaultdict(list)
    for n in nodes:
        layer_nodes[row[n]].append(n)
    layer_y, layer_r, cursor = {}, {}, 0
    if compact:
        for r in sorted(layer_nodes):
            layer_r[r] = max(radius(n) for n in layer_nodes[r])
            layer_y[r] = cursor + layer_r[r] + 8
            cursor = layer_y[r] + layer_r[r] + 34
    body_h = cursor if compact else nrows * row_h

    def esc(t):
        return xml.escape(str(t))

    def text(x, y, s, size=11, fill=INK, weight="normal", anchor="start", halo=False, extra=""):
        attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"'
        body = f'<text {attrs} fill="{fill}"{extra}>{esc(s)}</text>'
        if not halo:
            return body
        # A white outline under the letters keeps a label readable over the edges. It is its own text,
        # drawn first: `paint-order` would do the same in one element, but not every converter knows it.
        return (f'<text {attrs} fill="#ffffff" stroke="#ffffff" stroke-width="3.5" stroke-linejoin="round" '
                f'aria-hidden="true">{esc(s)}</text>' + body)

    # --- header: title, overall progress, what to do next — wrapped, never cut ---
    head = []
    done = sum(1 for n in model["nodes"] if n["status"] == "done") if done is None else done
    total = len(model["nodes"]) if total is None else total
    bar_w = width - 2 * margin
    head_w = bar_w  # room left of the speed badge
    if speed and speed.get("speed") is not None:
        def num(v, fmt):
            return format(v, fmt).replace(".", ",") if L is SVG_TEXT["ru"] else format(v, fmt)
        v = speed["speed"]
        tone = "#2e7d32" if v >= 1 else "#ef6c00" if v >= 0.8 else "#c62828"
        big = f"{L['speed']} {num(v, '.2f')}×"
        small = L["per_day"].format(a=num(speed["lessons_per_day"], ".1f"), n=num(speed["needed_per_day"], ".1f"))
        pw = max(_text_w(big, 16, True), _text_w(small, 11)) + 28
        px = width - margin - pw
        head.append(f'<g><title>{esc(big)} = {esc(small)}</title>'
                    f'<rect x="{px:.1f}" y="14" width="{pw:.1f}" height="50" rx="10" fill="{_tint(tone, 0.1)}" '
                    f'stroke="{tone}" stroke-width="1.5"/>'
                    + text(px + pw / 2, 36, big, 16, tone, "bold", "middle")
                    + text(px + pw / 2, 54, small, 11, tone, anchor="middle") + "</g>")
        head_w -= pw + 16
    tsize = 22
    while _text_w(title, tsize, True) > head_w and tsize > 18:
        tsize -= 1
    y = 38
    for k, line in enumerate(_wrap(title, tsize, head_w, 99, True)):
        y = 38 + k * round(tsize * 1.25)
        head.append(text(margin, y, line, tsize, INK, "bold"))
    pct = round(100 * done / total) if total else 0
    statuses = [n["status"] for n in model["nodes"]]
    stats = [f"{done}/{total} {L['of']} · {pct}%"]
    if model["level"] == "lessons":
        stats.append(f"{L['ready_now']}: {statuses.count('available') + statuses.count('retry')}")
        if statuses.count("retry"):
            stats.append(f"{L['retries']}: {statuses.count('retry')}")
    if today:
        stats.append(".".join(reversed(today.split("-"))) if L is SVG_TEXT["ru"] else today)
    y += 24
    for k, line in enumerate(_wrap(" · ".join(stats), 13, head_w if y < 72 else bar_w, 99)):
        y += 17 if k else 0
        head.append(text(margin, y, line, 13, MUTED))
    y = max(y + 12, 74)  # below the speed badge
    head.append(f'<rect x="{margin}" y="{y}" width="{bar_w}" height="8" rx="4" fill="#eceff1"/>')
    if total and done:
        head.append(f'<rect x="{margin}" y="{y}" width="{max(8, bar_w * done / total):.1f}" height="8" rx="4" fill="#43a047"/>')
    y += 30
    if picks:
        hd, items = L["today_list"], [nodes[p] for p in picks]
    else:
        hd, items = L["ready_list"], [n for n in model["nodes"] if n["status"] in ("available", "retry")]
    if items and model["level"] == "lessons":
        line = f"{hd}: " + " · ".join(f"{n['id']} {n['label']}" if len(items) <= 2 else n["id"] for n in items[:12])
        for k, part in enumerate(_wrap(line, 13, bar_w, 4, True)):
            y += 17 if k else 0
            head.append(text(margin, y, part, 13, INK, "bold"))
    elif total and done == total:
        head.append(text(margin, y, L["finished"], 13, "#2e7d32", "bold"))
    top = y + (TOP - 104 if lanes else TOP - 144)

    lane_bottom = top + 14 + body_h
    has_succ = {a for a, b in model["edges"] if row[b] > row[a]}
    sinks = [i for i in nodes if i not in has_succ]  # the ends of the branches (nothing below): they all lead to the goal
    goal_text = plan.get("goal") or title
    gx, gy = width / 2, lane_bottom + 46 + GOAL_R
    goal_lines = _wrap(f"{L['goal']}: {goal_text}", 13, min(width - 2 * margin, 600), 99, True)
    height = gy + GOAL_R + 26 + 17 * len(goal_lines) + 52
    legend_positions, lx, legend_shift = [], margin, 0
    for key in ["done", "available", "retry", "locked", "junction"] + (["today"] if picks else []):
        item_w = 34 + _text_w(L[key], 12)
        if lx > margin and lx + item_w > width - margin:
            lx, legend_shift = margin, legend_shift + 24
        legend_positions.append((lx, legend_shift))
        lx += item_w
    height += legend_shift
    key_cols = max(1, min(3, int(bar_w // 260)))
    if compact:
        height += ((len(tracks) + key_cols - 1) // key_cols) * 38 + 16

    W, H = width * scale, height * scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{math.ceil(W)}" height="{math.ceil(H)}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">',
           f"<title>{esc(title)}</title>"]
    out += ['<rect width="100%" height="100%" fill="#ffffff"/>'] + head

    # --- columns: tinted lane, title, mini progress bar ---
    for i, tid in enumerate(cols if lanes else ()):
        t, c = tracks[tid], color(tid)
        x0 = left + i * col_w
        out.append(f'<rect x="{x0 + 4:.1f}" y="{top - 44:.1f}" width="{col_w - 8}" height="{body_h + 58}" rx="12" '
                   f'fill="{_tint(c, 0.045)}"/>')
        out.append(f'<rect x="{x0 + 4:.1f}" y="{top - 44:.1f}" width="{col_w - 8}" height="4" rx="2" fill="{c}"/>')
        for k, line in enumerate(_wrap(t["title"], 12, col_w - 28, 2, True)):
            out.append(text(x0 + 14, top - 22 + k * 15, line, 12, c, "bold"))
        bw = col_w - 80
        out.append(f'<rect x="{x0 + 14:.1f}" y="{top + 4:.1f}" width="{bw}" height="6" rx="3" fill="{_tint(c, 0.15)}"/>')
        if t["done"]:
            out.append(f'<rect x="{x0 + 14:.1f}" y="{top + 4:.1f}" width="{max(6, bw * t["done"] / t["total"]):.1f}" '
                       f'height="6" rx="3" fill="{c}"/>')
        out.append(text(x0 + col_w - 14, top + 11, f"{t['done']}/{t['total']}", 11, MUTED, anchor="end"))

    # --- edges, under the nodes ---
    in_col = defaultdict(list)
    for nid in sorted(nodes, key=lambda i: row[i]):
        in_col[col[nid]].append(nid)

    def flow_path(a, b):
        (x1, y1), (x2, y2) = xy(a), xy(b)
        points = [(x1, y1 + radius(a) + 2)]
        for r in range(row[a] + 1, row[b]):
            if r not in layer_nodes:
                continue
            cy = top + layer_y[r]
            target = x1 + (x2 - x1) * (cy - y1) / (y2 - y1)
            boxes = [(xy(n)[0], max(radius(n) + 9, col_w / 2 - 8)) for n in layer_nodes[r]]
            # Follow the direct connection unless an intermediate lesson blocks it.
            candidates = [target, left, width - left]
            candidates += [x + side * (half + 3) for x, half in boxes for side in (-1, 1)]
            free = [x for x in candidates if margin <= x <= width - margin
                    and all(abs(x - centre) >= half + 2 for centre, half in boxes)]
            x = min(free, key=lambda x: (abs(x - target) + 0.15 * abs(x - points[-1][0]), x))
            points.extend([(x, cy - layer_r[r] - 8), (x, cy + layer_r[r] + 20)])
        points.append((x2, y2 - radius(b) - 3))
        if abs(points[1][0] - x1) >= col_w * 0.9:
            # A wide sideways move clears the source row's captions first.
            # Short connections keep the direct diagonal curve of the reference.
            points.insert(1, (x1, y1 + layer_r[row[a]] + 20))
        return _smooth_path(points)

    edges_out = []

    def edge(tag, geometry, tip, direction, color, width_=1.5, dash=""):
        """One connection: a solid (pre-mixed) stroke and an arrowhead polygon at `tip`."""
        dash = f' stroke-dasharray="{dash}"' if dash else ""
        edges_out.append(f'<{tag} class="edge" {geometry} fill="none" stroke="{color}" stroke-width="{width_}"{dash}/>'
                         + _arrowhead(*tip, *direction, color, width_))

    for a, b in model["edges"]:
        (x1, y1), (x2, y2) = xy(a), xy(b)
        mi = tindex[nodes[a]["unit"]] % len(TRACK_COLORS)
        if row[b] <= row[a]:  # tracks that need each other in a circle: this arrow goes back up, round the left
            sx, ex, bulge = x1 - radius(a) - 2, x2 - radius(b) - 3, 34
            edge("path", f'd="M{sx:.1f},{y1:.1f} C{sx - bulge:.1f},{y1:.1f} {ex - bulge:.1f},{y2:.1f} {ex:.1f},{y2:.1f}"',
                 (ex, y2), (1, 0), _tint(TRACK_COLORS[mi], 0.6), dash="5 3")
            continue
        walked = nodes[a]["status"] in SATISFIED
        same = lanes and col[a] == col[b]
        opacity = 0.9 if walked else (0.5 if not same else 1)
        width_ = 2.2 if same and in_col[col[a]].index(b) - in_col[col[a]].index(a) == 1 else 1.5
        if picks and (a in picks or b in picks):  # today's lessons: their edges stand out, the rest steps back
            opacity = 1
        elif picks:
            opacity = round(opacity * 0.55, 2)
        stroke = _tint(TRACK_COLORS[mi], opacity)
        sx, sy, ex, ey = x1, y1 + radius(a) + 2, x2, y2 - radius(b) - 3
        if same:
            seq = in_col[col[a]]
            if seq.index(b) - seq.index(a) == 1:  # neighbours in the lane: a straight line
                edge("line", f'x1="{x1:.1f}" y1="{sy:.1f}" x2="{x2:.1f}" y2="{ey:.1f}"', (x2, ey), (0, 1), stroke, width_)
            else:  # skips nodes of its own lane: down the left gutter, back in from the top
                edge("path", f'd="{_to_gutter(x1, sy)} L{x1 - GUTTER:.1f},{ey - 28:.1f} '
                     f'C{x1 - GUTTER:.1f},{ey - 6:.1f} {x2:.1f},{ey - 14:.1f} {x2:.1f},{ey:.1f}"',
                     (x2, ey), (0, 1), stroke, width_)
            continue
        if compact:
            geometry = f'd="{flow_path(a, b)}"'
        else:
            bend = min(max(28.0, (ey - sy) * 0.5), ROW_H * 1.2)
            geometry = f'd="M{sx:.1f},{sy:.1f} C{sx:.1f},{sy + bend:.1f} {ex:.1f},{ey - bend:.1f} {ex:.1f},{ey:.1f}"'
        edge("path", geometry, (ex, ey), (0, 1), stroke, width_)
    # every branch ends in the goal: down its own lane (through the left gutter when the lane goes on), then in
    goal_tip = (gx, gy - GOAL_R - 3)
    for a in sinks:
        x1, y1 = xy(a)
        walked = nodes[a]["status"] in SATISFIED
        mi = tindex[nodes[a]["unit"]] % len(TRACK_COLORS)
        stroke = _tint(TRACK_COLORS[mi], 0.9 if walked else 0.45)
        sy = y1 + radius(a) + 2
        if compact:
            # Earlier leaves end naturally. Only the bottom frontier joins the goal summary;
            # artificial full-height rails would turn the map back into straight columns.
            if row[a] == nrows - 1:
                edge("path", f'd="{_smooth_path([(x1, sy), goal_tip])}"', goal_tip, (0, 1), stroke, 1.6)
            continue
        if not lanes or in_col[col[a]][-1] == a:
            lane = f"M{x1:.1f},{sy:.1f} L{x1:.1f},{lane_bottom - 6:.1f}"
        else:
            gutter = GUTTER
            lane = _to_gutter(x1, sy)
            lane += f" L{x1 - gutter:.1f},{lane_bottom - 6:.1f}"
            x1 -= gutter
        edge("path", f'd="{lane} C{x1:.1f},{lane_bottom + 24:.1f} {gx:.1f},{gy - GOAL_R - 36:.1f} '
             f'{gx:.1f},{gy - GOAL_R - 3:.1f}"', goal_tip, (0, 1), stroke, 1.6)
    out += edges_out

    # --- nodes ---
    for nid, n in nodes.items():
        x, y = xy(nid)
        c, st = color(n["unit"]), n["status"]
        r = radius(nid)
        fill, ring, _, idc, labc, weight = {
            "done": (STATUS_FILL["done"], c, 1, INK, INK, "normal"),
            "available": (STATUS_FILL["available"], c, 1, INK, INK, "bold"),
            "retry": (STATUS_FILL["retry"], c, 1, INK, "#bf360c", "bold"),
            "locked": (STATUS_FILL["locked"], c, 1, MUTED, MUTED, "normal"),
            "skipped": (STATUS_FILL["skipped"], FAINT, 1, MUTED, MUTED, "normal"),
        }.get(st, ("#ffffff", c, 1, INK, INK, "normal"))
        g = [f'<g><title>{esc(nid)} — {esc(n["label"])} ({esc(L.get(st, st))})</title>']
        if nid in picks:
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 7}" fill="none" stroke="{TODAY}" stroke-width="3"/>')
        elif st in ("available", "retry"):
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 6}" fill="{_tint(ring, 0.16)}"/>')
        dash = ' stroke-dasharray="4 3"' if st == "skipped" else ""
        common = f'fill="{fill}" stroke="{ring}" stroke-width="3"{dash}'
        if junction(nid):
            g.append(f'<polygon points="{x:.1f},{y - r:.1f} {x + r:.1f},{y:.1f} {x:.1f},{y + r:.1f} {x - r:.1f},{y:.1f}" '
                     f'stroke-linejoin="round" {common}/>')
        else:
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" {common}/>')
        g.append(text(x, y + 4, n["short"], id_size(nid), idc, "bold", "middle"))
        if st == "retry":
            g.append(f'<circle cx="{x + r * 0.75:.1f}" cy="{y - r * 0.75:.1f}" r="6.5" fill="#ef6c00"/>'
                     + text(x + r * 0.75, y - r * 0.75 + 3.5, "!", 10, "#ffffff", "bold", "middle"))
        lab = n["label"]
        size = 10 if compact else 11.5
        lines = _wrap(lab, size, col_w - 16 if compact else col_w - 52 - r - (4 if nid in picks else 0),
                      1 if compact else 2, weight == "bold")
        ty = y + r + 14 if compact else y + 4 - (len(lines) - 1) * 7
        for k, line in enumerate(lines):
            g.append(text(x if compact else x + r + (14 if nid in picks else 10), ty + k * 14, line, size, labc,
                          weight, anchor="middle" if compact else "start", halo=True,
                          extra=' text-decoration="line-through"' if st == "skipped" else ""))
        g.append("</g>")
        out.append("".join(g))

    # --- the goal: where every branch meets ---
    finished = bool(total) and done == total
    ring = "#2e7d32" if finished else INK
    out.append(f'<g><title>{esc(L["goal"])}: {esc(goal_text)}</title>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R + 7}" fill="{_tint(ring, 0.08)}"/>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R}" fill="{"#43a047" if finished else "#ffffff"}" '
               f'stroke="{ring}" stroke-width="3.5"/>'
               f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="{GOAL_R - 6}" fill="none" stroke="{_tint(ring, 0.25)}" '
               f'stroke-width="1.5"/>'
               + text(gx, gy + 5, f"{pct}%", 14, "#ffffff" if finished else INK, "bold", "middle") + "</g>")
    for k, line in enumerate(goal_lines):
        out.append(text(gx, gy + GOAL_R + 24 + k * 17, line, 13, INK, "bold", "middle"))

    # --- legend ---
    ly = gy + GOAL_R + 24 + 17 * len(goal_lines) + 22
    swatch = [("done", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="{STATUS_FILL["done"]}" stroke="{TRACK_COLORS[0]}" stroke-width="2"/>'),
              ("available", f'<circle cx="{{x}}" cy="{ly}" r="10" fill="{_tint(TRACK_COLORS[0], 0.16)}"/>'
                            f'<circle cx="{{x}}" cy="{ly}" r="7" fill="{STATUS_FILL["available"]}" stroke="{TRACK_COLORS[0]}" stroke-width="2"/>'),
              ("retry", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="{STATUS_FILL["retry"]}" stroke="{TRACK_COLORS[0]}" stroke-width="2"/>'),
              ("locked", f'<circle cx="{{x}}" cy="{ly}" r="7" fill="{STATUS_FILL["locked"]}" stroke="{TRACK_COLORS[0]}" '
                         f'stroke-width="2"/>'),
              ("junction", f'<polygon points="{{x}},{ly - 9} {{x9}},{ly} {{x}},{ly + 9} {{x_9}},{ly}" fill="#fff" '
                           f'stroke="{MUTED}" stroke-width="2"/>')]
    if picks:
        swatch.append(("today", f'<circle cx="{{x}}" cy="{ly}" r="9" fill="none" stroke="{TODAY}" stroke-width="2.5"/>'))
    for (key, shape), (lx, shift) in zip(swatch, legend_positions):
        cx = lx + 10
        out.append(f'<g transform="translate(0,{shift})">' + shape.format(x=cx, x9=cx + 9, x_9=cx - 9)
                   + text(cx + 16, ly + 4, L[key], 12, MUTED) + '</g>')
    if compact:
        key_w = bar_w / key_cols
        for i, t in enumerate(model["tracks"]):
            x, y = margin + i % key_cols * key_w, ly + legend_shift + 32 + i // key_cols * 38
            label = f"{t['done']}/{t['total']} · {t['title']}"
            out.append(f'<g><title>{esc(label)}</title><circle cx="{x + 5:.1f}" cy="{y - 3:.1f}" '
                       f'r="4" fill="{color(t["id"])}"/>')
            for k, line in enumerate(_wrap(label, 11, key_w - 24, 2)):
                out.append(text(x + 18, y + k * 14, line, 11, color(t["id"])))
            out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


BEST_PNG = "rsvg-convert"
PNG_TIP = ("`apt install librsvg2-bin` (rsvg-convert) draws sharper text and arrows than ImageMagick, "
           "in a fraction of the time")


def svg_to_png(svg_path, png_path):
    """Convert with the first converter installed (fastest first); return its name, raise RuntimeError if none works.

    The pixel size comes from the SVG's width/height (render_svg(scale=...)), so every converter
    renders at the same resolution. The PNG is then losslessly shrunk if pngquant/optipng is installed."""
    final = Path(png_path)
    svg_path, png_path = str(svg_path), str(final.with_name(f".tmp-{os.getpid()}-{final.name}"))  # atomic: rename at the end
    attempts = [
        (BEST_PNG, [BEST_PNG, "-o", png_path, svg_path]),
        ("inkscape", ["inkscape", svg_path, "--export-type=png", f"--export-filename={png_path}"]),
        ("magick", ["magick", "-background", "white", svg_path, "-depth", "8", png_path]),  # 16-bit by default
        ("convert", ["convert", "-background", "white", svg_path, "-depth", "8", png_path]),  # 16-bit by default
    ]
    tried = []
    for exe, cmd in attempts:
        if not shutil.which(exe):
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and Path(png_path).exists() and Path(png_path).stat().st_size > 0:
            _optimize_png(png_path)
            os.replace(png_path, final)
            return exe
        tried.append(exe)
        Path(png_path).unlink(missing_ok=True)
    raise RuntimeError("no working SVG→PNG converter" + (f" (tried {', '.join(tried)})" if tried else "")
                       + ". Install one: `apt install librsvg2-bin` (rsvg-convert), or send the .svg / use --format md or text.")


def _optimize_png(path):
    """Flat-colour maps compress well; best effort, never fatal (a failed optimizer leaves the file as is)."""
    if shutil.which("pngquant"):
        subprocess.run(["pngquant", "--force", "--skip-if-larger", "--speed", "3", "--quality", "70-100",
                        "--output", path, path], capture_output=True)
    elif shutil.which("optipng"):
        subprocess.run(["optipng", "-quiet", "-o1", path], capture_output=True)


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
