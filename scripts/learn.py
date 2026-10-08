#!/usr/bin/env python3
"""learn — progress tracker for long-term study plans.

Stdlib only (Python 3.9+). Works the same from any agent (Hermes, Claude Code,
Codex, OpenCode, pi, ...) because all state lives in plain files:

  $LEARN_HOME (default ~/learning)
    config.json              language, timezone
    profile.md               who the learner is (the agent reads/writes this)
    plans/<slug>/plan.json   the plan (outcomes, units, lessons)
    plans/<slug>/progress.json  lesson results, review cards, activity events
    plans/<slug>/PLAN.md     generated, human-readable view (checkboxes)

Typical flow:  init -> create -> (today -> teach -> done / card add / review grade)* -> sync
Run `learn <command> -h` for details.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Leitner boxes 1..7: days until the next review after a successful recall.
INTERVALS = [1, 3, 7, 14, 30, 60, 120]
KINDS = {"lesson", "review", "milestone"}
DEFAULT_PACE = {"lessons_per_day": 2, "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
                "minutes_per_lesson": 25, "pass_score": 0.7}


class LearnError(Exception):
    pass


CODE_REPO = Path(__file__).resolve().parent.parent


def inside_code_repo(home):
    """True if the data dir is inside this (public) code repository."""
    try:
        Path(home).expanduser().resolve().relative_to(CODE_REPO)
        return (CODE_REPO / ".git").exists()
    except ValueError:
        return False


# ----------------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------------

def home_dir(args):
    return Path(args.home or os.environ.get("LEARN_HOME") or "~/learning").expanduser()


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise LearnError(f"not found: {path}")
    except json.JSONDecodeError as e:
        raise LearnError(f"invalid JSON in {path}: {e}")


def write_text(path, text):
    """Atomic write: a crash (or a sync tool) never sees a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_config(home):
    return load_json(home / "config.json", {})


def today_date(cfg):
    forced = os.environ.get("LEARN_TODAY")  # tests / debugging
    if forced:
        return date.fromisoformat(forced)
    tzname = os.environ.get("LEARN_TZ") or cfg.get("timezone")
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tzname)).date()
        except Exception:
            pass
    return date.today()


def iso(d):
    return d.isoformat()


def parse_date(s):
    return date.fromisoformat(s)


class Store:
    """All plans under one home directory."""

    def __init__(self, args):
        self.home = home_dir(args)
        self.cfg = load_config(self.home)
        self.today = today_date(self.cfg)

    @property
    def plans_dir(self):
        return self.home / "plans"

    def require_home(self):
        if not self.home.exists():
            raise LearnError(f"{self.home} does not exist — run `learn init` first")

    def slugs(self):
        if not self.plans_dir.exists():
            return []
        return sorted(p.name for p in self.plans_dir.iterdir() if (p / "plan.json").exists())

    def load(self, slug):
        d = self.plans_dir / slug
        if not (d / "plan.json").exists():
            raise LearnError(f"no such plan: {slug} (have: {', '.join(self.slugs()) or 'none'})")
        plan = load_json(d / "plan.json")
        prog = load_json(d / "progress.json", {})
        for k, v in (("status", "active"), ("lessons", {}), ("cards", []), ("next_card", 1), ("events", [])):
            prog.setdefault(k, v)
        return plan, prog

    def save(self, slug, plan, prog):
        d = self.plans_dir / slug
        save_json(d / "plan.json", plan)
        save_json(d / "progress.json", prog)
        write_text(d / "PLAN.md", render_plan_md(slug, plan, prog, self.today))

    def active(self):
        out = []
        for s in self.slugs():
            plan, prog = self.load(s)
            if prog["status"] == "active":
                out.append((s, plan, prog))
        return out

    def select(self, slug=None):
        """Plans a command applies to: the named one, or all active ones."""
        self.require_home()
        if slug:
            plan, prog = self.load(slug)
            return [(slug, plan, prog)]
        plans = self.active()
        if not plans:
            raise LearnError("no active plans — create one with `learn create` (see skills/study-plan)")
        return plans

    def select_one(self, slug=None):
        plans = self.select(slug)
        if len(plans) > 1:
            raise LearnError("several active plans — pass --plan <slug>: " + ", ".join(p[0] for p in plans))
        return plans[0]


# ----------------------------------------------------------------------------
# plan helpers
# ----------------------------------------------------------------------------

def lessons_of(plan):
    return [l for u in plan["units"] for l in u["lessons"]]


def lesson_state(prog, lid):
    return prog["lessons"].get(lid, {}).get("status", "pending")


def pending_lessons(plan, prog):
    return [l for l in lessons_of(plan) if lesson_state(prog, l["id"]) in ("pending", "retry")]


def counts(plan, prog):
    ls = lessons_of(plan)
    done = sum(1 for l in ls if lesson_state(prog, l["id"]) == "done")
    skipped = sum(1 for l in ls if lesson_state(prog, l["id"]) == "skipped")
    return {"done": done, "skipped": skipped, "total": len(ls), "remaining": len(ls) - done - skipped}


def is_scheduled(plan, d):
    return DAYS[d.weekday()] in plan["pace"]["days"]


def lesson_events_on(prog, d):
    return [e for e in prog["events"] if e["type"] == "lesson" and e["date"] == iso(d)]


def pace_report(plan, prog, today):
    """Compare actual progress against the plan's pace.

    Behind-ness is measured against scheduled days *before* today so a fresh
    morning never reads as "behind". The remedy is always to move the finish
    date, never to double up lessons (cramming defeats spacing).
    """
    pace = plan["pace"]
    lpd = pace["lessons_per_day"]
    start = parse_date(plan["start_date"])
    expected = 0
    d = start
    while d < today:
        if is_scheduled(plan, d):
            expected += lpd
        d += timedelta(days=1)
    done_before = sum(1 for l in lessons_of(plan)
                      if prog["lessons"].get(l["id"], {}).get("status") == "done"
                      and prog["lessons"][l["id"]].get("date", "9999") < iso(today))
    skipped = sum(1 for l in lessons_of(plan) if lesson_state(prog, l["id"]) == "skipped")
    behind = max(0, expected - done_before - skipped)
    ahead = max(0, done_before - expected)

    remaining = counts(plan, prog)["remaining"]
    finish = None
    if remaining == 0:
        finish = None
    else:
        left = remaining
        d = max(today, start)
        for _ in range(3650):
            if is_scheduled(plan, d):
                cap = lpd
                if d == today:
                    cap = max(0, lpd - len(lesson_events_on(prog, today)))
                left -= cap
                if left <= 0:
                    finish = d
                    break
            d += timedelta(days=1)
    deadline = plan.get("deadline")
    return {"expected_before_today": expected, "behind": behind, "ahead": ahead,
            "projected_finish": iso(finish) if finish else None, "started": today >= start,
            "past_deadline": bool(finish and deadline and iso(finish) > deadline)}


def due_cards(plan_slug, prog, today, include_future=False):
    out = []
    for c in prog["cards"]:
        if c.get("retired"):
            continue
        if include_future or parse_date(c["due"]) <= today:
            out.append(dict(c, plan=plan_slug))
    out.sort(key=lambda c: (c["due"], c["id"]))
    return out


def activity_dates(store):
    dates = set()
    for s in store.slugs():
        _, prog = store.load(s)
        dates.update(e["date"] for e in prog["events"])
    return dates


def streak(store):
    """Consecutive scheduled days with activity, ending today (or yesterday if
    today is still open). Rest days neither extend nor break the streak."""
    acts = activity_dates(store)
    if not acts:
        return 0
    plans = [p for _, p, _ in store.active()]

    def required(d):
        return any(is_scheduled(p, d) for p in plans) if plans else True

    earliest = parse_date(min(acts))
    d = store.today
    if iso(d) not in acts:
        d -= timedelta(days=1)
    n = 0
    while d >= earliest:
        if iso(d) in acts:
            n += 1
        elif required(d):
            break
        d -= timedelta(days=1)
    return n


def last_activity(store):
    acts = activity_dates(store)
    return parse_date(max(acts)) if acts else None


# ----------------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------------

def normalize_plan(plan, today):
    """Validate and fill defaults. Raises LearnError listing every problem."""
    errs = []
    if not isinstance(plan, dict):
        raise LearnError("plan must be a JSON object")
    for k in ("title", "goal"):
        if not str(plan.get(k, "")).strip():
            errs.append(f"missing '{k}'")
    pace = dict(DEFAULT_PACE)
    pace.update(plan.get("pace") or {})
    if not isinstance(pace["lessons_per_day"], int) or not 1 <= pace["lessons_per_day"] <= 6:
        errs.append("pace.lessons_per_day must be an integer 1..6")
    days = [str(x).lower()[:3] for x in pace["days"]]
    if not days or any(x not in DAYS for x in days):
        errs.append(f"pace.days must be a non-empty subset of {DAYS}")
    pace["days"] = days
    if not isinstance(pace["pass_score"], (int, float)) or not 0 <= pace["pass_score"] <= 1:
        errs.append("pace.pass_score must be 0..1")
    plan["pace"] = pace
    plan.setdefault("start_date", iso(today))
    try:
        parse_date(plan["start_date"])
    except ValueError:
        errs.append("start_date must be YYYY-MM-DD")
    if not isinstance(plan.get("later", []), list):
        errs.append("'later' must be a list of strings")
    plan.setdefault("outcomes", [])
    plan.setdefault("resources", [])
    outcome_ids = set()
    for o in plan["outcomes"]:
        if not o.get("id") or not o.get("text"):
            errs.append(f"outcome needs id and text: {o}")
        elif o["id"] in outcome_ids:
            errs.append(f"duplicate outcome id {o['id']}")
        else:
            outcome_ids.add(o["id"])
    units = plan.get("units")
    if not isinstance(units, list) or not units:
        errs.append("plan needs at least one unit")
        units = []
    seen_lessons, seen_units = set(), set()
    for u in units:
        uid = u.get("id")
        if not uid or not u.get("title"):
            errs.append(f"unit needs id and title: {u.get('title') or u}")
        elif uid in seen_units:
            errs.append(f"duplicate unit id {uid}")
        seen_units.add(uid)
        for oid in u.get("outcomes", []):
            if oid not in outcome_ids:
                errs.append(f"unit {uid}: unknown outcome {oid}")
        if not u.get("lessons"):
            errs.append(f"unit {uid}: no lessons")
        for l in u.get("lessons", []):
            lid = l.get("id")
            if not lid or not l.get("title") or not l.get("objective"):
                errs.append(f"lesson needs id, title, objective: {lid or l}")
                continue
            if lid in seen_lessons:
                errs.append(f"duplicate lesson id {lid}")
            seen_lessons.add(lid)
            l.setdefault("kind", "lesson")
            l.setdefault("minutes", pace["minutes_per_lesson"])
            if l["kind"] not in KINDS:
                errs.append(f"lesson {lid}: kind must be one of {sorted(KINDS)}")
            if not l.get("check"):
                errs.append(f"lesson {lid}: needs a 'check' (how mastery is verified)")
            for oid in l.get("outcomes", []):
                if oid not in outcome_ids:
                    errs.append(f"lesson {lid}: unknown outcome {oid}")
    if errs:
        raise LearnError("invalid plan:\n  - " + "\n  - ".join(errs))
    return plan


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------

def bar(done, total, width=10):
    filled = round(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def pct(done, total):
    return round(100 * done / total) if total else 0


MARK = {"done": "x", "pending": " ", "retry": "!", "skipped": "-"}


def render_plan_md(slug, plan, prog, today):
    c = counts(plan, prog)
    pr = pace_report(plan, prog, today)
    pace = plan["pace"]
    out = [f"# {plan['title']}", "",
           f"**Goal:** {plan['goal']}", ""]
    if plan.get("level"):
        out += [f"**Starting level:** {plan['level']}", ""]
    out += [f"**Pace:** {pace['lessons_per_day']} lessons/day on {', '.join(pace['days'])} "
            f"(~{pace['minutes_per_lesson']} min each) · started {plan['start_date']}"
            + (f" · target {plan['deadline']}" if plan.get("deadline") else ""),
            f"**Progress:** {bar(c['done'], c['total'])} {c['done']}/{c['total']} ({pct(c['done'], c['total'])}%)"
            + (f" · projected finish {pr['projected_finish']}" if pr["projected_finish"] else " · complete 🎉" if c["remaining"] == 0 else ""),
            f"**Status:** {prog['status']}", ""]
    if plan["outcomes"]:
        out += ["## Outcomes", ""]
        out += [f"- **{o['id']}** {o['text']}" for o in plan["outcomes"]]
        out.append("")
    out += ["## Lessons", "", "_`[x]` done · `[ ]` pending · `[!]` retry · `[-]` skipped_", ""]
    for u in plan["units"]:
        ud = sum(1 for l in u["lessons"] if lesson_state(prog, l["id"]) == "done")
        out += [f"### {u['id']} · {u['title']} ({ud}/{len(u['lessons'])})", ""]
        for l in u["lessons"]:
            st = lesson_state(prog, l["id"])
            rec = prog["lessons"].get(l["id"], {})
            tail = ""
            if st == "done":
                tail = f" — {rec.get('date', '')}" + (f" · {round(rec['score'] * 100)}%" if rec.get("score") is not None else "")
            elif st == "retry":
                tail = f" — retry (last {round(rec.get('score', 0) * 100)}%)"
            kind = "" if l["kind"] == "lesson" else f" _{l['kind']}_"
            out.append(f"- [{MARK[st]}] **{l['id']}** {l['title']}{kind}{tail}")
            out.append(f"  - {l['objective']}")
        ms = u.get("milestone")
        if ms:
            out += ["", f"> 🏁 **Milestone:** {ms.get('title', '')} — pass bar: {ms.get('pass_bar', '')}"]
        out.append("")
    if plan.get("later"):
        out += ["## Later stages (outlined, detailed when reached)", ""]
        out += [f"- {x}" for x in plan["later"]]
        out.append("")
    if plan["resources"]:
        out += ["## Resources", ""]
        for r in plan["resources"]:
            link = f"[{r.get('title', r.get('url'))}]({r['url']})" if r.get("url") else r.get("title", "")
            out.append(f"- {link}" + (f" — {r['note']}" if r.get("note") else ""))
        out.append("")
    return "\n".join(out)


def emit(args, data, text):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(text)


def ru_plural(n, forms):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[{1: 0, 2: 1, 3: 1, 4: 1}.get(n % 10, 2)]


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------

PROFILE_TEMPLATE = """# Learner profile

_The tutor agent reads this at the start of every session and updates it as it learns about you._

- **Language for lessons:** {language}
- **Typical session length:**
- **Preferred days / time:**
- **Where I usually learn:** (phone / laptop / both)
- **Strengths / what clicks for me:**
- **What tends to trip me up:**
- **Goals (big picture):**
"""


def cmd_init(args):
    home = home_dir(args)
    home.mkdir(parents=True, exist_ok=True)
    cfg = load_json(home / "config.json", {})
    cfg.setdefault("language", args.language)
    if args.timezone:
        cfg["timezone"] = args.timezone
    save_json(home / "config.json", cfg)
    if not (home / "profile.md").exists():
        write_text(home / "profile.md", PROFILE_TEMPLATE.format(language=cfg["language"]))
    (home / "plans").mkdir(exist_ok=True)
    if not (home / ".gitignore").exists():
        write_text(home / ".gitignore", ".tmp-*\n.DS_Store\n")
    if args.git and not (home / ".git").exists():
        run_git(home, "init", "-q")
    if inside_code_repo(home):
        print(f"⚠ {home} is inside the code repository. Your progress is personal: keep it outside "
              f"(default ~/learning) or make sure it is git-ignored — never push it to a public repo.", file=sys.stderr)
    print(f"initialised {home} (language={cfg['language']}"
          + (f", timezone={cfg['timezone']}" if cfg.get("timezone") else "") + ")")
    print("tip: make this folder a private git repo and run `learn sync` to share progress across devices")


def cmd_plans(args):
    st = Store(args)
    st.require_home()
    rows = []
    for s in st.slugs():
        plan, prog = st.load(s)
        c = counts(plan, prog)
        rows.append({"slug": s, "title": plan["title"], "status": prog["status"], **c})
    text = "\n".join(f"{r['slug']:<24} {r['status']:<9} {bar(r['done'], r['total'])} {r['done']}/{r['total']}  {r['title']}"
                     for r in rows) or "no plans yet"
    emit(args, rows, text)


def cmd_create(args):
    st = Store(args)
    st.require_home()
    raw = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LearnError(f"plan is not valid JSON: {e}")
    plan = normalize_plan(plan, st.today)
    exists = (st.plans_dir / args.slug / "plan.json").exists()
    if exists and not args.update:
        raise LearnError(f"plan '{args.slug}' exists — use --update to replace the plan and keep progress")
    prog = st.load(args.slug)[1] if exists else {"status": "active", "lessons": {}, "cards": [],
                                                  "next_card": 1, "events": []}
    ids = {l["id"] for l in lessons_of(plan)}
    orphans = sorted(set(prog["lessons"]) - ids)
    if orphans:
        print(f"warning: progress exists for lessons no longer in the plan: {', '.join(orphans)}", file=sys.stderr)
    st.save(args.slug, plan, prog)
    c = counts(plan, prog)
    print(f"{'updated' if exists else 'created'} plan '{args.slug}': {c['total']} lessons "
          f"in {len(plan['units'])} units · view: {st.plans_dir / args.slug / 'PLAN.md'}")
    pr = pace_report(plan, prog, st.today)
    if pr["projected_finish"]:
        print(f"projected finish: {pr['projected_finish']}")
    if pr["past_deadline"]:
        print(f"⚠ projected finish is after the deadline ({plan['deadline']}): cut scope or add study days — don't just raise the pace", file=sys.stderr)


def cmd_validate(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    normalize_plan(plan, st.today)
    print(f"plan '{slug}' is valid")


def cmd_render(args):
    st = Store(args)
    for slug, plan, prog in st.select(args.plan):
        st.save(slug, plan, prog)
        print(f"rendered {st.plans_dir / slug / 'PLAN.md'}")


def plan_status_summary(st, slug, plan, prog):
    c = counts(plan, prog)
    pr = pace_report(plan, prog, st.today)
    cards = [x for x in prog["cards"] if not x.get("retired")]
    due = due_cards(slug, prog, st.today)
    nxt = pending_lessons(plan, prog)
    return {"slug": slug, "title": plan["title"], "status": prog["status"], **c,
            "percent": pct(c["done"], c["total"]),
            "pace": {"lessons_per_day": plan["pace"]["lessons_per_day"], "days": plan["pace"]["days"],
                     **{k: pr[k] for k in ("behind", "ahead", "projected_finish", "past_deadline")}},
            "cards": len(cards), "reviews_due": len(due),
            "next_lesson": nxt[0]["id"] if nxt else None}


def cmd_status(args):
    st = Store(args)
    st.require_home()
    if args.plan:
        plans = st.select(args.plan)
    else:
        plans = [(s, *st.load(s)) for s in st.slugs()]
        if not plans:
            raise LearnError("no plans yet")
    data = {"date": iso(st.today), "streak": streak(st), "plans": []}
    lines = [f"📊 {iso(st.today)} · streak {data['streak']}"]
    for slug, plan, prog in plans:
        s = plan_status_summary(st, slug, plan, prog)
        data["plans"].append(s)
        p = s["pace"]
        pace_txt = "on track" if not p["behind"] else f"behind {p['behind']}"
        if p["ahead"]:
            pace_txt = f"ahead {p['ahead']}"
        nxt = next((l for l in lessons_of(plan) if l["id"] == s["next_lesson"]), None)
        lines += ["", f"{slug} — {plan['title']} [{s['status']}]",
                  f"  {bar(s['done'], s['total'])} {s['done']}/{s['total']} ({s['percent']}%) · {pace_txt}"
                  + (f" · finish ≈ {p['projected_finish']}" if p["projected_finish"] else "")
                  + (" ⚠ after deadline" if p["past_deadline"] else ""),
                  f"  reviews due: {s['reviews_due']} of {s['cards']} cards"]
        if nxt:
            lines.append(f"  next: {nxt['id']} {nxt['title']}")
    emit(args, data, "\n".join(lines))


def cmd_today(args):
    st = Store(args)
    plans = st.select(args.plan)
    t = st.today
    data = {"date": iso(t), "weekday": DAYS[t.weekday()], "streak": streak(st), "plans": [], "reviews": []}
    lines = [f"📅 {iso(t)} ({DAYS[t.weekday()]}) · streak {data['streak']}"]
    all_due = []
    for slug, plan, prog in plans:
        lpd = plan["pace"]["lessons_per_day"]
        done_today = len(lesson_events_on(prog, t))
        scheduled = is_scheduled(plan, t) and t >= parse_date(plan["start_date"])
        quota = max(0, lpd - done_today) if scheduled else 0
        pr = pace_report(plan, prog, t)
        todo = pending_lessons(plan, prog)[:quota]
        all_due += due_cards(slug, prog, t)
        data["plans"].append({"slug": slug, "title": plan["title"], "scheduled_today": scheduled,
                              "quota": quota, "done_today": done_today, "behind": pr["behind"],
                              "projected_finish": pr["projected_finish"],
                              "lessons": [dict(l, status=lesson_state(prog, l["id"]),
                                               attempts=prog["lessons"].get(l["id"], {}).get("attempts", 0))
                                          for l in todo],
                              "pass_score": plan["pace"]["pass_score"]})
        head = f"== {slug} — {plan['title']}: "
        head += (f"{done_today}/{lpd} done today" if scheduled else "rest day") + " =="
        lines += ["", head]
        if pr["behind"]:
            lines.append(f"  behind by {pr['behind']} lesson(s): do NOT double up — keep the normal quota; the finish date moves"
                         + (f" (≈ {pr['projected_finish']})" if pr["projected_finish"] else ""))
        for l in todo:
            retry = " (RETRY — re-teach the gaps)" if lesson_state(prog, l["id"]) == "retry" else ""
            lines += [f"  ▶ {l['id']} · {l['title']} · {l['minutes']}m [{l['kind']}]{retry}",
                      f"      objective: {l['objective']}",
                      f"      check: {l['check']}"]
            if l.get("nodes"):
                lines.append("      key ideas: " + "; ".join(l["nodes"]))
        if scheduled and not todo and counts(plan, prog)["remaining"] == 0:
            lines.append("  plan complete 🎉")
        elif scheduled and not todo:
            lines.append("  quota for today is done ✔")
    all_due.sort(key=lambda c: (c["due"], c["plan"], c["id"]))
    shown = all_due[: args.max_reviews]
    data["reviews"] = shown
    data["reviews_due_total"] = len(all_due)
    if shown:
        lines += ["", f"🔁 Warm-up reviews ({len(shown)} of {len(all_due)} due) — ask closed-book, then `learn review grade`:"]
        for c in shown:
            lines += [f"  {c['plan']}/{c['id']} (from {c['lesson']}, box {c['box']}, due {c['due']})",
                      f"      Q: {c['q']}", f"      A: {c['a']}"]
    elif not any(p["quota"] for p in data["plans"]):
        lines += ["", "nothing due — enjoy the rest ☕"]
    emit(args, data, "\n".join(lines))


def cmd_next(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    todo = pending_lessons(plan, prog)[: args.n]
    data = [dict(l, status=lesson_state(prog, l["id"])) for l in todo]
    text = "\n".join(f"{l['id']} · {l['title']} · {l['minutes']}m\n   objective: {l['objective']}\n   check: {l['check']}"
                     for l in todo) or "plan complete 🎉"
    emit(args, data, text)


def find_lesson(plan, lid):
    for l in lessons_of(plan):
        if l["id"] == lid:
            return l
    raise LearnError(f"no such lesson: {lid}")


def cmd_done(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    lesson = find_lesson(plan, args.lesson)
    if args.score is not None and not 0 <= args.score <= 1:
        raise LearnError("--score must be between 0 and 1")
    rec = prog["lessons"].setdefault(lesson["id"], {})
    rec["attempts"] = rec.get("attempts", 0) + 1
    passed = args.accept or args.score is None or args.score >= plan["pace"]["pass_score"]
    rec.update({"status": "done" if passed else "retry", "date": iso(st.today)})
    if args.score is not None:
        rec["score"] = args.score
    if args.note:
        rec["note"] = args.note
    prog["events"].append({"date": iso(st.today), "type": "lesson", "id": lesson["id"],
                           "score": args.score, "passed": passed})
    st.save(slug, plan, prog)
    c = counts(plan, prog)
    msg = (f"✅ {lesson['id']} done" if passed else
           f"🔁 {lesson['id']} needs another pass (score {args.score:.0%} < {plan['pace']['pass_score']:.0%}) — it stays next in line")
    nxt = pending_lessons(plan, prog)
    out = {"lesson": lesson["id"], "passed": passed, "done": c["done"], "total": c["total"],
           "next_lesson": nxt[0]["id"] if nxt else None}
    text = f"{msg}\n{bar(c['done'], c['total'])} {c['done']}/{c['total']} ({pct(c['done'], c['total'])}%)"
    if nxt:
        text += f"\nnext: {nxt[0]['id']} {nxt[0]['title']}"
    else:
        text += "\nplan complete 🎉"
    emit(args, out, text)


def cmd_skip(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    lesson = find_lesson(plan, args.lesson)
    prog["lessons"].setdefault(lesson["id"], {}).update({"status": "skipped", "date": iso(st.today)})
    st.save(slug, plan, prog)
    print(f"skipped {lesson['id']}")


def cmd_set_plan_status(args):
    st = Store(args)
    st.require_home()
    plan, prog = st.load(args.plan)
    prog["status"] = args.value
    st.save(args.plan, plan, prog)
    print(f"plan '{args.plan}' is now {args.value}")


def cmd_card_add(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    find_lesson(plan, args.lesson)
    if args.stdin:
        try:
            items = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            raise LearnError(f"stdin is not valid JSON: {e}")
    else:
        if not (args.q and args.a):
            raise LearnError("pass --q and --a (or --stdin with a JSON list of {q, a})")
        items = [{"q": args.q, "a": args.a}]
    if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("q") and i.get("a") for i in items):
        raise LearnError("cards must be a list of objects with non-empty 'q' and 'a'")
    first_due = st.today + timedelta(days=INTERVALS[0])
    new = []
    for i in items:
        cid = f"C{prog['next_card']:03d}"
        prog["next_card"] += 1
        card = {"id": cid, "lesson": args.lesson, "q": i["q"], "a": i["a"], "box": 1, "due": iso(first_due),
                "reps": 0, "lapses": 0, "created": iso(st.today)}
        prog["cards"].append(card)
        new.append(cid)
    st.save(slug, plan, prog)
    print(f"added {len(new)} card(s) to {slug}: {', '.join(new)} — first review {iso(first_due)}")


def split_card_ref(ref, plan_arg):
    if "/" in ref:
        slug, cid = ref.split("/", 1)
        return slug, cid
    return plan_arg, ref


def cmd_review_due(args):
    st = Store(args)
    rows = []
    for slug, plan, prog in st.select(args.plan):
        rows += due_cards(slug, prog, st.today, include_future=args.all)
    rows.sort(key=lambda c: (c["due"], c["plan"], c["id"]))
    rows = rows[: args.limit]
    text = "\n".join(f"{c['plan']}/{c['id']} box{c['box']} due {c['due']}\n   Q: {c['q']}\n   A: {c['a']}" for c in rows) \
        or "no reviews due"
    emit(args, rows, text)


def cmd_review_grade(args):
    st = Store(args)
    slug, cid = split_card_ref(args.card, args.plan)
    slug, plan, prog = st.select_one(slug)
    card = next((c for c in prog["cards"] if c["id"] == cid), None)
    if not card:
        raise LearnError(f"no such card: {cid}")
    ok = args.result == "pass"
    card["reps"] += 1
    if ok:
        card["box"] = min(card["box"] + 1, len(INTERVALS))
    else:
        card["box"] = 1
        card["lapses"] += 1
    days = INTERVALS[card["box"] - 1]
    card["due"] = iso(st.today + timedelta(days=days))
    card["last"] = iso(st.today)
    prog["events"].append({"date": iso(st.today), "type": "review", "id": f"{slug}/{cid}", "result": args.result})
    st.save(slug, plan, prog)
    print(f"{slug}/{cid}: {'✓' if ok else '✗'} → box {card['box']}, next review in {days}d ({card['due']})")


def cmd_card_retire(args):
    st = Store(args)
    slug, cid = split_card_ref(args.card, args.plan)
    slug, plan, prog = st.select_one(slug)
    card = next((c for c in prog["cards"] if c["id"] == cid), None)
    if not card:
        raise LearnError(f"no such card: {cid}")
    card["retired"] = True
    st.save(slug, plan, prog)
    print(f"retired {slug}/{cid}")


def cmd_week(args):
    st = Store(args)
    st.require_home()
    since = iso(st.today - timedelta(days=6))
    data = {"from": since, "to": iso(st.today), "plans": []}
    lines = [f"🗓 {since} → {iso(st.today)} · streak {streak(st)}"]
    for slug in st.slugs():
        plan, prog = st.load(slug)
        ev = [e for e in prog["events"] if e["date"] >= since]
        les = [e for e in ev if e["type"] == "lesson"]
        rev = [e for e in ev if e["type"] == "review"]
        scores = [e["score"] for e in les if e.get("score") is not None]
        passed = sum(1 for e in rev if e["result"] == "pass")
        days = sorted({e["date"] for e in ev})
        row = {"slug": slug, "lessons": len(les), "lessons_passed": sum(1 for e in les if e["passed"]),
               "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
               "reviews": len(rev), "reviews_passed": passed, "active_days": len(days)}
        data["plans"].append(row)
        c = counts(plan, prog)
        lines += ["", f"{slug}: {c['done']}/{c['total']} total",
                  f"  lessons: {row['lessons_passed']}/{row['lessons']} passed"
                  + (f" · avg score {row['avg_score']:.0%}" if row["avg_score"] is not None else ""),
                  f"  reviews: {passed}/{len(rev)} recalled" + (f" ({pct(passed, len(rev))}%)" if rev else ""),
                  f"  active days: {len(days)}/7"]
    emit(args, data, "\n".join(lines))


def cmd_nudge(args):
    """No-LLM daily reminder. Prints nothing when there is nothing to do, so a
    scheduler can treat empty output as a silent tick."""
    st = Store(args)
    if not st.home.exists() or not st.active():
        return
    lang = args.language or st.cfg.get("language", "en")
    ru = lang.lower().startswith("ru")
    t = st.today
    todo, due_total, behind = [], 0, 0
    for slug, plan, prog in st.active():
        scheduled = is_scheduled(plan, t) and t >= parse_date(plan["start_date"])
        if scheduled:
            quota = max(0, plan["pace"]["lessons_per_day"] - len(lesson_events_on(prog, t)))
            todo += [(slug, l) for l in pending_lessons(plan, prog)[:quota]]
        due_total += len(due_cards(slug, prog, t))
        behind = max(behind, pace_report(plan, prog, t)["behind"])
    if not todo and not due_total:
        return
    sk = streak(st)
    last = last_activity(st)
    away = (t - last).days if last else 0
    lines = []
    if ru:
        lines.append("📚 С возвращением! Ничего не потеряно — начнём с лёгкого." if away >= 4 else "📚 Время учиться!")
        parts = []
        if todo:
            parts.append(f"{len(todo)} {ru_plural(len(todo), ('урок', 'урока', 'уроков'))}")
        if due_total:
            parts.append(f"{due_total} {ru_plural(due_total, ('повторение', 'повторения', 'повторений'))}")
        lines.append("Сегодня: " + " + ".join(parts) + (f" · серия {sk} 🔥" if sk else ""))
        lines += [f"• {l['id']} · {l['title']}" for _, l in todo]
        if behind >= 2:
            lines.append(f"Отстаём на {behind} — это нормально: план сдвинется, а не навалится.")
        lines.append(f"Напиши {args.cta}, чтобы начать.")
    else:
        lines.append("📚 Welcome back! Nothing is lost — we'll start light." if away >= 4 else "📚 Time to learn!")
        parts = []
        if todo:
            parts.append(f"{len(todo)} lesson{'s' if len(todo) != 1 else ''}")
        if due_total:
            parts.append(f"{due_total} review{'s' if due_total != 1 else ''}")
        lines.append("Today: " + " + ".join(parts) + (f" · streak {sk} 🔥" if sk else ""))
        lines += [f"• {l['id']} · {l['title']}" for _, l in todo]
        if behind >= 2:
            lines.append(f"Behind by {behind} — that's fine: the plan shifts instead of piling up.")
        lines.append(f"Send {args.cta} to start.")
    print("\n".join(lines))


def run_git(home, *cmd, check=True):
    try:
        r = subprocess.run(["git", "-C", str(home), *cmd], capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise LearnError("git is not installed")
    except subprocess.TimeoutExpired:
        raise LearnError(f"git {' '.join(cmd)} timed out")
    if check and r.returncode != 0:
        raise LearnError(f"git {' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def cmd_sync(args):
    """Commit local changes, rebase onto the remote, push. Run at the start and
    end of a session when you learn from more than one device."""
    st = Store(args)
    if inside_code_repo(st.home):
        raise LearnError(f"refusing to sync: {st.home} is inside the code repository ({CODE_REPO}), which may be public. "
                         "Move your data outside it (e.g. LEARN_HOME=~/learning) and sync that as its own PRIVATE repo.")
    st.require_home()
    if not (st.home / ".git").exists():
        raise LearnError(f"{st.home} is not a git repo. One-time setup:\n"
                         f"  cd {st.home} && git init && git remote add origin <your PRIVATE repo url>")
    run_git(st.home, "add", "-A")
    if run_git(st.home, "status", "--porcelain").stdout.strip():
        run_git(st.home, "-c", "user.name=learn", "-c", "user.email=learn@localhost",
                "commit", "-q", "-m", f"learn: progress {iso(st.today)}")
    remotes = run_git(st.home, "remote").stdout.split()
    if not remotes:
        print("committed locally (no remote configured — add one to sync across devices)")
        return
    branch = run_git(st.home, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    has_upstream = run_git(st.home, "rev-parse", "--abbrev-ref", "@{u}", check=False).returncode == 0
    if has_upstream or run_git(st.home, "ls-remote", "--exit-code", "--heads", remotes[0], branch, check=False).returncode == 0:
        r = run_git(st.home, "pull", "--rebase", "--autostash", remotes[0], branch, check=False)
        if r.returncode != 0:
            run_git(st.home, "rebase", "--abort", check=False)
            raise LearnError("sync conflict: both devices changed progress. Nothing was lost locally.\n"
                             f"Resolve in {st.home} (git status), then run `learn sync` again.\n" + r.stderr.strip())
    run_git(st.home, "push", "-q", "-u", remotes[0], branch)
    print(f"synced with {remotes[0]}/{branch}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: a value given before the subcommand must survive the subparser's own defaults.
    common.add_argument("--home", default=argparse.SUPPRESS, help="data directory (default: $LEARN_HOME or ~/learning)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")

    ap = argparse.ArgumentParser(prog="learn", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help_, plan=True, **kw):
        p = sub.add_parser(name, parents=[common], help=help_, **kw)
        if plan:
            p.add_argument("--plan", help="plan slug (default: the only active plan, or all for overview commands)")
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init, "create the data directory", plan=False)
    p.add_argument("--language", default="en", help="lesson/reminder language, e.g. ru, en")
    p.add_argument("--timezone", help="IANA timezone, e.g. Europe/Moscow")
    p.add_argument("--git", action="store_true", help="also `git init` the directory")

    add("plans", cmd_plans, "list plans with progress", plan=False)

    p = add("create", cmd_create, "create/update a plan from JSON (see skills/study-plan)", plan=False)
    p.add_argument("slug", help="short id, e.g. python-basics")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--stdin", action="store_true")
    p.add_argument("--update", action="store_true", help="replace an existing plan but keep its progress")

    add("validate", cmd_validate, "validate a plan's JSON")
    add("render", cmd_render, "regenerate PLAN.md")
    add("status", cmd_status, "progress, pace and projected finish")

    p = add("today", cmd_today, "today's lessons + warm-up reviews (start every session here)")
    p.add_argument("--max-reviews", type=int, default=6, help="cap warm-up reviews (default 6)")

    p = add("next", cmd_next, "upcoming lessons regardless of daily quota")
    p.add_argument("-n", type=int, default=3)

    p = add("done", cmd_done, "record a finished lesson")
    p.add_argument("lesson")
    p.add_argument("--score", type=float, help="0..1 from the end-of-lesson check")
    p.add_argument("--note", help="one line: what was hard / what to revisit")
    p.add_argument("--accept", action="store_true", help="mark done even if score is below the pass bar")

    p = add("skip", cmd_skip, "drop a lesson from the plan's remaining work")
    p.add_argument("lesson")

    for name in ("pause", "resume", "archive"):
        p = sub.add_parser(name, parents=[common], help=f"{name} a plan")
        p.add_argument("plan")
        p.set_defaults(fn=cmd_set_plan_status, value={"pause": "paused", "resume": "active", "archive": "archived"}[name])

    card = sub.add_parser("card", help="review cards").add_subparsers(dest="sub", required=True, metavar="action")
    p = card.add_parser("add", parents=[common], help="add review card(s) for a lesson")
    p.add_argument("--plan")
    p.add_argument("--lesson", required=True)
    p.add_argument("--q")
    p.add_argument("--a")
    p.add_argument("--stdin", action="store_true", help='read JSON list [{"q":..,"a":..}] from stdin')
    p.set_defaults(fn=cmd_card_add)
    p = card.add_parser("retire", parents=[common], help="stop reviewing a card")
    p.add_argument("card", help="C007 or slug/C007")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_card_retire)

    rev = sub.add_parser("review", help="spaced reviews").add_subparsers(dest="sub", required=True, metavar="action")
    p = rev.add_parser("due", parents=[common], help="list due cards")
    p.add_argument("--plan")
    p.add_argument("--all", action="store_true", help="include not-yet-due cards")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_review_due)
    p = rev.add_parser("grade", parents=[common], help="record recall result")
    p.add_argument("card", help="C007 or slug/C007")
    p.add_argument("result", choices=["pass", "fail"])
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_review_grade)

    add("week", cmd_week, "last-7-days summary", plan=False)

    p = add("nudge", cmd_nudge, "daily reminder text (empty output = nothing to do)", plan=False)
    p.add_argument("--language", help="override config language (ru/en)")
    p.add_argument("--cta", default="/daily-lesson", help="what the learner should send to start (default /daily-lesson)")

    add("sync", cmd_sync, "git commit + pull --rebase + push the data directory", plan=False)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.home = getattr(args, "home", None)
    args.json = getattr(args, "json", False)
    try:
        args.fn(args)
    except LearnError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
