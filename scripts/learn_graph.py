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

from collections import defaultdict

SATISFIED = ("done", "skipped")
ICON = {"done": "✅", "skipped": "⏭", "retry": "🔁", "available": "▶", "locked": "🔒"}
STATUS_FILL = {"done": "#a5d6a7", "skipped": "#e0e0e0", "retry": "#ffcc80",
               "available": "#fff59d", "locked": "#eceff1"}


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
