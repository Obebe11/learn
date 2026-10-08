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
    tracks (interleaving) — retries first, then junctions (the payoff of
    several skills), then the track with the fewest picks today, then the
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
    for e in prog["events"]:
        if e["type"] == "lesson" and e["date"] == today_iso and e["id"] in units:
            today_count[units[e["id"]]] += 1

    sat = satisfied_ids(plan, prog)
    chosen = []
    while len(chosen) < quota:
        cands = [l for l in cands_all if l not in chosen and all(r in sat for r in reqs[l["id"]])
                 and (not track or units[l["id"]] == track)]
        if not cands:
            break
        pick = min(cands, key=lambda l: (lesson_status(prog, l["id"]) != "retry",
                                         l.get("kind") != "junction",
                                         today_count[units[l["id"]]],
                                         last[units[l["id"]]],
                                         order[l["id"]]))
        chosen.append(pick)
        sat.add(pick["id"])
        today_count[units[pick["id"]]] += 1
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
# SVG
# ----------------------------------------------------------------------------

def render_svg(title, plan, prog, model, done=None, total=None):
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
    unit_idx = {t["id"]: i for i, t in enumerate(model["tracks"])}
    index = {n["id"]: i for i, n in enumerate(model["nodes"])}
    layers = defaultdict(list)
    for n in model["nodes"]:
        layers[depth[n["id"]]].append(n["id"])
    for k in layers:
        layers[k].sort(key=lambda i: (unit_idx[nodes[i]["unit"]], index[i]))

    dx, dy, r = 100, 86, 18
    width = max(len(v) for v in layers.values()) * dx + 80
    width = max(width, 420)
    pos = {}
    for k in sorted(layers):
        ids = layers[k]
        if k > 0:  # barycenter ordering keeps edges short
            ids.sort(key=lambda i: (sum(pos[p][0] for p in preds[i] if p in pos) / max(1, len([p for p in preds[i] if p in pos]))
                                    if preds[i] else unit_idx[nodes[i]["unit"]] * dx, index[i]))
        for j, i in enumerate(ids):
            pos[i] = (width / 2 + (j - (len(ids) - 1) / 2) * dx, 70 + k * dy)
    height = 70 + (max(layers) + 1) * dy + 16 + 28 * (1 + (len(model["tracks"]) + 1) // 2)

    def color(unit):
        return TRACK_COLORS[unit_idx[unit] % len(TRACK_COLORS)]

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="Helvetica, Arial, sans-serif">',
           f"<title>{xml.escape(title)}</title>",
           '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#90a4ae"/></marker></defs>',
           f'<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="16" y="26" font-size="15" font-weight="bold" fill="#263238">{xml.escape(title)}'
           + (f" — {done}/{total}" if done is not None else "") + "</text>"]
    import math
    for a, b in model["edges"]:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        span = depth[b] - depth[a]
        if span > 1:
            # long edge: bend it sideways, to whichever side clears the nodes in the layers it skips
            def clearance(sign):
                worst = 1e9
                for k in range(depth[a] + 1, depth[b]):
                    t = (k - depth[a]) / span
                    bx = x1 + (x2 - x1) * t + sign * 55 * math.sin(math.pi * t) * 0.75
                    for i in layers[k]:
                        worst = min(worst, abs(pos[i][0] - bx))
                return worst
            sign = 1 if clearance(1) >= clearance(-1) else -1
            off = sign * 55
            end_y = y2 - (r + 8)
            out.append(f'<path d="M{x1:.1f},{y1 + r + 2:.1f} C{x1 + off:.1f},{y1 + dy * 0.45:.1f} '
                       f'{x2 + off:.1f},{end_y - dy * 0.45:.1f} {x2:.1f},{end_y:.1f}" fill="none" '
                       f'stroke="#90a4ae" stroke-width="1.5" marker-end="url(#ar)"/>')
            continue
        dxl, dyl = x2 - x1, y2 - y1
        dist = (dxl * dxl + dyl * dyl) ** 0.5 or 1
        ox, oy = dxl / dist * (r + 3), dyl / dist * (r + 3)
        out.append(f'<line x1="{x1 + ox:.1f}" y1="{y1 + oy:.1f}" x2="{x2 - ox:.1f}" y2="{y2 - oy:.1f}" '
                   f'stroke="#90a4ae" stroke-width="1.5" marker-end="url(#ar)"/>')
    for nid, n in nodes.items():
        x, y = pos[nid]
        fill, ring = STATUS_FILL[n["status"]], color(n["unit"])
        if n["kind"] == "junction":
            rr = r + 5
            shape = (f'<polygon points="{x:.1f},{y - rr:.1f} {x + rr:.1f},{y:.1f} {x:.1f},{y + rr:.1f} {x - rr:.1f},{y:.1f}" '
                     f'fill="{fill}" stroke="{ring}" stroke-width="3"/>')
        else:
            shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{ring}" stroke-width="3"/>'
        weight = "bold" if n["status"] in ("available", "retry") else "normal"
        out.append(shape)
        out.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="11" font-weight="{weight}" text-anchor="middle" fill="#263238">'
                   f'{xml.escape(n["short"])}</text>')
        out.append(f'<text x="{x:.1f}" y="{y + r + 15:.1f}" font-size="10" text-anchor="middle" fill="#455a64">'
                   f'{xml.escape(_short(n["label"], 16))}</text>')
    ly = height - 28 * (1 + (len(model["tracks"]) + 1) // 2) + 6
    lx = 16
    for s, label in (("done", "done"), ("available", "ready"), ("retry", "retry"), ("locked", "locked")):
        out.append(f'<circle cx="{lx + 6}" cy="{ly}" r="6" fill="{STATUS_FILL[s]}" stroke="#455a64"/>'
                   f'<text x="{lx + 18}" y="{ly + 4}" font-size="11" fill="#455a64">{label}</text>')
        lx += 84
    out.append(f'<polygon points="{lx + 6},{ly - 7} {lx + 13},{ly} {lx + 6},{ly + 7} {lx - 1},{ly}" fill="#fff" stroke="#455a64"/>'
               f'<text x="{lx + 20}" y="{ly + 4}" font-size="11" fill="#455a64">junction</text>')
    for i, t in enumerate(model["tracks"]):
        cx, cy = 16 + (i % 2) * 210, ly + 26 + (i // 2) * 24
        out.append(f'<circle cx="{cx + 6}" cy="{cy}" r="6" fill="#fff" stroke="{color(t["id"])}" stroke-width="3"/>'
                   f'<text x="{cx + 18}" y="{cy + 4}" font-size="11" fill="#263238">{xml.escape(_short(t["title"], 24))}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_to_png(svg_path, png_path):
    """Convert with whichever converter is installed; raise RuntimeError if none works."""
    svg_path, png_path = str(svg_path), str(png_path)
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
            return exe
        tried.append(exe)
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
