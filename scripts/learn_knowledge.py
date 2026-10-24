"""Knowledge layer for learn.py: propositions, reasoning chains and practice outcomes.

Stdlib only. Imported by learn.py; holds no CLI code and no I/O except the
LearnHarness reader/writer helpers, which only touch the paths they are given.

The model follows LearnHarness's graph (format 1) so the two systems converge:

  proposition  one self-contained true statement, with a short display name
  chain        premises that JOINTLY imply a conclusion; two arguments for the
               same conclusion are two chains, never one pooled premise set

and ties every idea to our study plan instead of a chat: a proposition's
sources are the lessons it was taught in. Understanding is never stored as a
level — it is derived on read from the evidence the plan already keeps
(lesson scores, card lapses, practice outcomes).

$LEARN_HOME/knowledge.json:
  {"version": 1, "next_prop": 4, "next_chain": 2,
   "props":  [{"id": "P001", "name": "...", "statement": "...", "aliases": [], "kind": "truth"|"derived"|null,
               "created": "YYYY-MM-DD", "sources": [{"plan": "py", "lesson": "L03"} | {"lh": "chat_x"}],
               "lh_id": "n_..."?, "draft": true?}],
   "chains": [{"id": "A001", "premises": ["P001", "P002"], "conclusion": "P003", "created": "...",
               "sources": [...], "lh_id": "arg_..."?}],
   "practice": [{"date": "...", "targets": ["P003"], "mode": "recall", "outcome": "solved", "rung": 0,
                 "miss": null, "note": "..."}]}
"""

import json
import re
from pathlib import Path

VERSION = 1
LH_FORMAT = 1  # the LearnHarness graph format this module reads and writes
KINDS = ("truth", "derived")
MODES = ("recall", "discrimination", "composition")  # prerequisite order: the cheapest undemonstrated one comes first
OUTCOMES = ("solved", "hinted", "missed", "surrendered")
MISSES = ("cue", "discrimination", "execution", "careless", "misconception")
HINTED_RUNG = 3  # from R3 on, the retrieval itself was handed over


class KnowledgeError(Exception):
    pass


def empty():
    return {"version": VERSION, "next_prop": 1, "next_chain": 1, "props": [], "chains": [], "practice": []}


def normalize(k):
    """Fill defaults of an older or partial file; refuse a newer format instead of guessing."""
    if k.get("version", VERSION) > VERSION:
        raise KnowledgeError(f"knowledge.json is format {k['version']}; this learn understands {VERSION} — update learn")
    base = empty()
    base.update(k)
    return base


def squash(text):
    return " ".join(str(text).split())


def same_statement(a, b):
    return squash(a).casefold() == squash(b).casefold()


def by_id(k):
    return {p["id"]: p for p in k["props"]}


def add_source(item, src):
    if src and src not in item["sources"]:
        item["sources"].append(src)


def add_prop(k, today, name, statement, aliases=(), kind=None, source=None, draft=False):
    """New proposition, or the existing one with the same statement (then only the source is linked).
    Returns (prop, created)."""
    statement, name = squash(statement), squash(name or statement)
    if not statement:
        raise KnowledgeError("a proposition needs a non-empty statement")
    if kind not in (None, *KINDS):
        raise KnowledgeError(f"kind must be one of {', '.join(KINDS)}")
    for p in k["props"]:
        if not p.get("retired") and (same_statement(p["statement"], statement)
                                     or any(same_statement(a, statement) for a in p["aliases"])):
            add_source(p, source)
            for a in aliases:
                add_alias(p, a)
            return p, False
    p = {"id": f"P{k['next_prop']:03d}", "name": name[:120], "statement": statement,
         "aliases": [], "kind": kind, "created": today, "sources": []}
    for a in aliases:
        add_alias(p, a)
    if draft:
        p["draft"] = True
    add_source(p, source)
    k["next_prop"] += 1
    k["props"].append(p)
    return p, True


def add_alias(p, text):
    text = squash(text)
    if text and not same_statement(text, p["statement"]) and not any(same_statement(text, a) for a in p["aliases"]):
        p["aliases"].append(text)


def edit_prop(p, name=None, statement=None, alias=None, kind=None):
    if statement is not None:
        statement = squash(statement)
        if not statement:
            raise KnowledgeError("statement must not be empty")
        if not same_statement(statement, p["statement"]):
            old = p["statement"]
            p["statement"] = statement
            p["aliases"] = [a for a in p["aliases"] if not same_statement(a, statement)]
            add_alias(p, old)  # the earlier phrasing stays findable
    if name is not None:
        if not squash(name):
            raise KnowledgeError("name must not be empty")
        p["name"] = squash(name)[:120]
    if alias is not None:
        add_alias(p, alias)
    if kind is not None:
        if kind not in KINDS:
            raise KnowledgeError(f"kind must be one of {', '.join(KINDS)}")
        p["kind"] = kind
    p.pop("draft", None)


def parse_ids(text):
    ids = [x for x in re.split(r"[,\s]+", text or "") if x]
    if not ids:
        raise KnowledgeError("no proposition ids given")
    return ids


def require_props(k, ids):
    known = by_id(k)
    missing = [i for i in ids if i not in known]
    if missing:
        raise KnowledgeError(f"unknown proposition(s): {', '.join(missing)}")
    return [known[i] for i in ids]


def chain_key(premises, conclusion):
    return (tuple(sorted(premises)), conclusion)


def add_chain(k, today, premises, conclusion, source=None):
    """New chain, or the same argument already stored (then only the source is linked). Returns (chain, created)."""
    premises = list(dict.fromkeys(premises))
    if not premises:
        raise KnowledgeError("a chain needs at least one premise")
    if conclusion in premises:
        raise KnowledgeError("a chain's conclusion cannot be one of its own premises")
    require_props(k, [*premises, conclusion])
    key = chain_key(premises, conclusion)
    for c in k["chains"]:
        if chain_key(c["premises"], c["conclusion"]) == key:
            add_source(c, source)
            return c, False
    c = {"id": f"A{k['next_chain']:03d}", "premises": premises, "conclusion": conclusion,
         "created": today, "sources": []}
    add_source(c, source)
    k["next_chain"] += 1
    k["chains"].append(c)
    return c, True


def chain_text(k, c, names=True):
    props = by_id(k)
    label = (lambda i: f"{i} {props[i]['name']}" if names and i in props else i)
    return " ∧ ".join(label(i) for i in c["premises"]) + " ⟹ " + label(c["conclusion"])


def lessons_of_prop(p):
    return [(s["plan"], s["lesson"]) for s in p["sources"] if "plan" in s and "lesson" in s]


def log_practice(k, today, targets, mode, outcome, rung=None, miss=None, note=None):
    require_props(k, targets)
    if mode not in MODES:
        raise KnowledgeError(f"mode must be one of {', '.join(MODES)}")
    if outcome not in OUTCOMES:
        raise KnowledgeError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if rung is not None and not 0 <= rung <= 5:
        raise KnowledgeError("rung is 0..5 (the highest hint given: R0 redirect … R5 worked step)")
    if outcome == "solved" and rung is not None and rung >= HINTED_RUNG:
        raise KnowledgeError(f"a solve that needed R{rung} is 'hinted': from R{HINTED_RUNG} on the idea itself was handed over")
    if miss is not None and miss not in MISSES:
        raise KnowledgeError(f"miss must be one of {', '.join(MISSES)}")
    if miss is not None and outcome == "solved":
        raise KnowledgeError("a solved problem has no miss type")
    ev = {"date": today, "targets": list(dict.fromkeys(targets)), "mode": mode, "outcome": outcome}
    if rung is not None:
        ev["rung"] = rung
    if miss:
        ev["miss"] = miss
    if note:
        ev["note"] = squash(note)
    k["practice"].append(ev)
    return ev


# ----------------------------------------------------------------------------
# evidence and practice selection (derived on read, never stored)
# ----------------------------------------------------------------------------

def practice_history(k, pid):
    return [e for e in k["practice"] if pid in e["targets"]]


def next_mode(k, pid):
    """The cheapest mode this proposition hasn't been solved in yet (recall before discrimination before
    composition); composition only for a conclusion that has a chain to run."""
    solved = {e["mode"] for e in practice_history(k, pid) if e["outcome"] == "solved"}
    has_chain = any(c["conclusion"] == pid for c in k["chains"])
    for m in MODES:
        if m == "composition" and not has_chain:
            continue
        if m not in solved:
            return m
    return "composition" if has_chain else "discrimination"


def evidence(k, p, plans, today_ord):
    """What the plan knows about one proposition, plus a weakness score for practice selection.

    plans: {slug: (plan, prog)}. today_ord: date.toordinal() of today.
    """
    from datetime import date
    lessons, cards, review_flags, last_seen = [], [], 0, None
    for slug, lid in lessons_of_prop(p):
        if slug not in plans:
            continue
        _, prog = plans[slug]
        rec = prog["lessons"].get(lid, {})
        lessons.append({"plan": slug, "lesson": lid, "status": rec.get("status", "pending"), "score": rec.get("score")})
        if rec.get("date"):
            last_seen = max(last_seen or rec["date"], rec["date"])
    for slug, (_, prog) in plans.items():
        for c in prog["cards"]:
            if p["id"] in c.get("facts", []) and not c.get("retired"):
                cards.append({"card": f"{slug}/{c['id']}", "lapses": c.get("lapses", 0), "due": c["due"]})
        for e in prog["events"]:
            if e["type"] == "review" and e.get("result") == "fail" and (e.get("confidence") or 0) >= 80 \
                    and any(f"{slug}/{c['id']}" == e["id"] and p["id"] in c.get("facts", []) for c in prog["cards"]):
                review_flags += 1
    hist = practice_history(k, p["id"])
    last = hist[-1] if hist else None
    if last:
        last_seen = max(last_seen or last["date"], last["date"])
    w = 0.0
    reasons = []
    if last and last["outcome"] in ("missed", "surrendered"):
        w += 3
        reasons.append(f"last practice {last['outcome']}" + (f" ({last['miss']})" if last.get("miss") else ""))
    elif last and last["outcome"] == "hinted":
        w += 1.5
        reasons.append("last practice needed hints")
    elif last and last["outcome"] == "solved":
        w -= 1
    for l in lessons:
        if l["status"] == "retry":
            w += 2
            reasons.append(f"{l['lesson']} needs another pass")
        if l["score"] is not None and l["score"] < 1:
            w += (1 - l["score"]) * 2
    lapses = sum(c["lapses"] for c in cards)
    if lapses:
        w += min(lapses, 3)
        reasons.append(f"cards forgotten {lapses}×")
    if review_flags:
        w += review_flags
        reasons.append("confident miss in review")
    served = sum(1 for c in k["chains"] if p["id"] in c["premises"])
    w += min(served * 0.3, 1.5)
    if served >= 2:
        reasons.append(f"premise of {served} chains")
    idle = (today_ord - date.fromisoformat(last_seen).toordinal()) if last_seen else 30
    w += min(max(idle, 0), 30) / 15
    if not hist:
        reasons.append("never practised")
    return {"id": p["id"], "name": p["name"], "statement": p["statement"], "lessons": lessons, "cards": cards,
            "practice": {"count": len(hist), "last": last}, "served": served, "weakness": round(w, 2),
            "reasons": reasons}


def pick(k, plans, today_ord, count=3, retest_date=None):
    """Practice targets: propositions taught in done/retry lessons, weakest first, interleaved across lessons.
    Misses logged on retest_date (today) come first as retests — in a fresh surface."""
    pool = []
    for p in k["props"]:
        if p.get("retired"):
            continue
        ev = evidence(k, p, plans, today_ord)
        if not any(l["status"] in ("done", "retry") for l in ev["lessons"]):
            continue
        last = ev["practice"]["last"]
        ev["retest"] = bool(retest_date and last and last["date"] == retest_date and last["outcome"] != "solved")
        ev["mode"] = last["mode"] if ev["retest"] else next_mode(k, p["id"])
        ev["chains"] = [chain_text(k, c) for c in k["chains"] if c["conclusion"] == p["id"]]
        pool.append(ev)
    pool.sort(key=lambda e: (not e["retest"], -e["weakness"], e["id"]))
    chosen, used_lessons = [], set()
    for e in pool:  # first pass: one target per lesson, so consecutive problems switch topic
        home = (e["lessons"][0]["plan"], e["lessons"][0]["lesson"]) if e["lessons"] else None
        if len(chosen) < count and (e["retest"] or home not in used_lessons):
            chosen.append(e)
            used_lessons.add(home)
    for e in pool:
        if len(chosen) >= count:
            break
        if e not in chosen:
            chosen.append(e)
    return chosen, len(pool)


# ----------------------------------------------------------------------------
# LearnHarness graph (format 1): read their files, write ours in their shape
# ----------------------------------------------------------------------------

def lh_graph_dir(path):
    """Accept the LearnHarness data dir (containing graph/) or the graph dir itself."""
    p = Path(path).expanduser()
    if (p / "graph" / "nodes.jsonl").exists():
        return p / "graph"
    if (p / "nodes.jsonl").exists():
        return p
    raise KnowledgeError(f"no LearnHarness graph in {p} (expected graph/nodes.jsonl or nodes.jsonl)")


def read_jsonl(path, problems):
    out = []
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"{path.name}:{n}: not JSON ({e.msg})")
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            problems.append(f"{path.name}:{n}: not an object")
    return out


def read_lh(path):
    """Merged LearnHarness propositions and chains, following its own reading rules: the first line of an id
    defines name/statement/date; later statements and aliases become aliases; provenance unions in order;
    chains merge by sorted premises + conclusion."""
    g = lh_graph_dir(path)
    problems = []
    fmt = json.loads((g / "format.json").read_text(encoding="utf-8")).get("version", 1) if (g / "format.json").exists() else 1
    if fmt > LH_FORMAT:
        raise KnowledgeError(f"LearnHarness graph format {fmt} is newer than {LH_FORMAT}; not guessing")
    nodes = {}
    for rec in read_jsonl(g / "nodes.jsonl", problems):
        nid, stmt = rec.get("id"), squash(rec.get("statement", ""))
        if not nid:
            problems.append("a node line has no id")
            continue
        prov = [pp.get("chatId") for pp in rec.get("provenance") or [] if isinstance(pp, dict) and pp.get("chatId")]
        if nid not in nodes:
            if not stmt:
                problems.append(f"node {nid} has no statement")
                continue
            nodes[nid] = {"id": nid, "name": squash(rec.get("name") or stmt), "statement": stmt,
                          "aliases": [], "created": str(rec.get("createdAt", ""))[:10], "chats": []}
        n = nodes[nid]
        extra = ([stmt] if stmt and not same_statement(stmt, n["statement"]) else []) + list(rec.get("aliases") or [])
        for a in extra:
            if squash(a) and not any(same_statement(a, x) for x in n["aliases"]):
                n["aliases"].append(squash(a))
        for c in prov:
            if c not in n["chats"]:
                n["chats"].append(c)
    chains, seen = [], {}
    for rec in read_jsonl(g / "chains.jsonl", problems):
        prem, concl = rec.get("premises") or [], rec.get("conclusion")
        if not rec.get("id") or not prem or not concl:
            problems.append(f"chain {rec.get('id') or '?'} is incomplete")
            continue
        missing = [i for i in [*prem, concl] if i not in nodes]
        if missing:
            problems.append(f"chain {rec['id']} references missing node(s) {', '.join(missing)} — skipped")
            continue
        key = chain_key(prem, concl)
        chats = [pp.get("chatId") for pp in rec.get("provenance") or [] if isinstance(pp, dict) and pp.get("chatId")]
        if key in seen:
            for c in chats:
                if c not in seen[key]["chats"]:
                    seen[key]["chats"].append(c)
            continue
        seen[key] = {"id": rec["id"], "premises": list(prem), "conclusion": concl, "chats": list(dict.fromkeys(chats))}
        chains.append(seen[key])
    sources = {}
    if (g / "sources.json").exists():
        try:
            raw = json.loads((g / "sources.json").read_text(encoding="utf-8"))
            sources = {sid: v.get("filePath", "") for sid, v in raw.items() if isinstance(v, dict)}
        except (json.JSONDecodeError, AttributeError):
            problems.append("sources.json is unreadable — sources kept as ids")
    return list(nodes.values()), chains, sources, problems


def lh_to_source(chat, sources):
    """Their provenance id back to our source: lessons we exported come home as lessons."""
    path = sources.get(chat, "")
    m = re.fullmatch(r"learn:([^/]+)/([^/]+)", path)
    return {"plan": m.group(1), "lesson": m.group(2)} if m else {"lh": chat}


def import_lh(k, today, path, link=None):
    """Merge a LearnHarness graph into k. Idempotent: a node already imported (same lh_id) or with the same
    statement is updated, never duplicated. link = optional {"plan", "lesson"} source for every node."""
    nodes, chains, sources, problems = read_lh(path)
    stats = {"props_new": 0, "props_merged": 0, "chains_new": 0, "chains_merged": 0}
    lh_map = {}
    for n in nodes:
        p = next((x for x in k["props"] if x.get("lh_id") == n["id"]), None)
        if p is None:
            p, created = add_prop(k, n["created"] or today, n["name"], n["statement"], n["aliases"])
            if created:
                p["lh_id"] = n["id"]
            else:
                p.setdefault("lh_id", n["id"])
            stats["props_new" if created else "props_merged"] += 1
        else:
            for a in [n["statement"], *n["aliases"]]:
                add_alias(p, a)
            stats["props_merged"] += 1
        for c in n["chats"]:
            add_source(p, lh_to_source(c, sources))
        add_source(p, link)
        lh_map[n["id"]] = p["id"]
    for c in chains:
        ch, created = add_chain(k, today, [lh_map[i] for i in c["premises"]], lh_map[c["conclusion"]])
        ch.setdefault("lh_id", c["id"])
        for chat in c["chats"]:
            add_source(ch, lh_to_source(chat, sources))
        add_source(ch, link)
        stats["chains_new" if created else "chains_merged"] += 1
    return stats, problems


def lh_source_id(src):
    if "lh" in src:
        return src["lh"]
    if "plan" in src:
        return f"src_learn_{src['plan']}_{src['lesson']}"
    return None


def to_lh(k, props, now_iso):
    """Our propositions/chains as LearnHarness format-1 records: (node lines, chain lines, sources.json).
    Lessons become notes sources (src_learn_<plan>_<lesson>), so their app shows where an idea was taught."""
    keep = {p["id"] for p in props}
    nid = {p["id"]: p.get("lh_id") or f"n_learn_{p['id'].lower()}" for p in props}
    sources = {}

    def prov(item):
        out = []
        for s in item["sources"]:
            sid = lh_source_id(s)
            if sid and {"chatId": sid, "messageId": ""} not in out:
                out.append({"chatId": sid, "messageId": ""})
                if "plan" in s:
                    sources[sid] = {"id": sid, "filePath": f"learn:{s['plan']}/{s['lesson']}", "importedAt": now_iso}
        return out

    nodes = [{"id": nid[p["id"]], "name": p["name"], "statement": p["statement"], "aliases": list(p["aliases"]),
              "createdAt": f"{p['created']}T00:00:00.000Z", "provenance": prov(p)} for p in props]
    chains = [{"id": c.get("lh_id") or f"arg_learn_{c['id'].lower()}", "premises": [nid[i] for i in c["premises"]],
               "conclusion": nid[c["conclusion"]], "createdAt": f"{c['created']}T00:00:00.000Z", "provenance": prov(c)}
              for c in k["chains"] if c["conclusion"] in keep and all(i in keep for i in c["premises"])]
    return nodes, chains, sources


def write_lh(out_dir, nodes, chains, sources):
    g = Path(out_dir).expanduser() / "graph"
    if (g / "nodes.jsonl").exists():
        raise KnowledgeError(f"{g} already holds a LearnHarness graph — refusing to overwrite it; choose an empty --out")
    g.mkdir(parents=True, exist_ok=True)
    (g / "nodes.jsonl").write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in nodes), encoding="utf-8")
    (g / "chains.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chains), encoding="utf-8")
    (g / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (g / "format.json").write_text(json.dumps({"version": LH_FORMAT}) + "\n", encoding="utf-8")
    return g


def to_markdown(k, props, lesson_titles):
    """A notes file: readable in Obsidian, and importable by LearnHarness's notes import."""
    groups = {}
    for p in props:
        for key in lessons_of_prop(p) or [(None, None)]:
            groups.setdefault(key, []).append(p)
            break
    out = ["# Knowledge", ""]
    keep = {p["id"] for p in props}
    for key in sorted(groups, key=lambda x: (x[0] is None, x)):
        slug, lid = key
        out.append(f"## {slug}/{lid} · {lesson_titles.get(key, '')}".rstrip(" ·") if slug else "## Unlinked")
        out.append("")
        for p in groups[key]:
            head = "" if p["name"] == p["statement"] else f"**{p['name']}** — "
            out.append(f"- {head}{p['statement']}" + (" *(draft)*" if p.get("draft") else ""))
        out.append("")
    ch = [c for c in k["chains"] if c["conclusion"] in keep and all(i in keep for i in c["premises"])]
    if ch:
        names = by_id(k)
        out += ["## Reasoning chains", "", "Premises in a line are used together.", ""]
        out += [f"- " + " ∧ ".join(names[i]["name"] for i in c["premises"]) + " ⟹ " + names[c["conclusion"]]["name"]
                for c in ch]
        out.append("")
    return "\n".join(out)
