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
To use it from any device, run `learn_server.py` on a VPS (MCP + REST over the same data).
Run `learn <command> -h` for details.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import fcntl  # POSIX only; without it commands simply run unlocked
except ImportError:
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find learn_graph.py next to the real file (symlink-safe)
import learn_graph as G  # noqa: E402

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Retained only for migrating old cards and their compatibility `box` field.
LEGACY_INTERVALS = [1, 3, 7, 14, 30, 60, 120]
MAX_REVIEW_INTERVAL = 36500  # practical 100-year ceiling, not part of SM-2
# Daily warm-up budget. "auto" = the day's review load (cards due today, plus those already
# graded today), clamped so a quiet day still warms up and a backlog never becomes a marathon.
AUTO_REVIEWS = (6, 30)
MAX_REVIEWS = 50
FRAGILE_CONFIDENCE = 50  # a correct recall the learner rated below a coin flip is fragile or lucky
CONFIDENT_MISS = 80  # a wrong recall the learner was this sure of is a misconception, not a gap
LEECH_LAPSES = 4  # a card forgotten this often needs rewriting or a repaired prerequisite, not more drilling
MATURE_DAYS = 21  # a recall after a gap this long counts towards long-term retention
KINDS = {"lesson", "review", "milestone", "junction"}
CHAT_FORMATS = ("plain", "rich")  # rich = Telegram rich messages (tables, LaTeX, task lists, details)
CONFIG_KEYS = {"language": None, "timezone": None, "chat_format": CHAT_FORMATS, "daily_reviews": None}
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


def check_slug(slug):
    """A plan slug becomes a directory name: refuse anything that could point elsewhere."""
    if (not slug or slug.startswith((".", "-")) or any(c in slug for c in "/\\\0")
            or len(slug) > 64):
        raise LearnError(f"invalid plan slug {slug!r}: use a short name without slashes or leading dots/dashes")
    return slug


@contextmanager
def home_lock(home):
    """Exclusive lock on the data directory for the duration of one command.

    Several writers can share one $LEARN_HOME (the MCP/API server, a Hermes cron
    job, you in a shell); every command is read-modify-write, so without this two
    of them could overwrite each other's progress.
    """
    fd = None
    if fcntl is not None and home.is_dir():
        fd = os.open(home, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)  # closing releases the lock


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
        d = self.plans_dir / check_slug(slug)
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


def effective_start(plan, prog):
    """start_date, or the first lesson actually done if that came earlier (you may study ahead of the plan's start)."""
    start = parse_date(plan["start_date"])
    first = min((e["date"] for e in prog["events"] if e["type"] == "lesson"), default=None)
    return min(start, parse_date(first)) if first else start


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
    start = effective_start(plan, prog)
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


def speed_report(plan, prog, today):
    """Speed = average lessons per day so far / average lessons per day the plan needs.

    Actual: done lessons over calendar days since the start (today counts once a lesson is done today, so a
    fresh morning doesn't drag the average down). Needed: with a deadline, the remaining-at-start work spread
    over start..deadline; otherwise the plan's pace (lessons_per_day x study days per week / 7).
    1.0 = exactly on schedule, below 1 = slower than needed. None until there is a day to average over.
    """
    start = effective_start(plan, prog)
    if today < start:
        return None
    done = [prog["lessons"][l["id"]] for l in lessons_of(plan) if lesson_state(prog, l["id"]) == "done"]
    days = (today - start).days + (1 if any(rec.get("date") == iso(today) for rec in done) else 0)
    if days <= 0:
        return None
    pace = plan["pace"]
    deadline = plan.get("deadline")
    c = counts(plan, prog)
    work = c["total"] - c["skipped"]
    if deadline and parse_date(deadline) >= start:
        needed = work / ((parse_date(deadline) - start).days + 1)
    else:
        needed = pace["lessons_per_day"] * len(pace["days"]) / 7
    actual = len(done) / days
    return {"lessons_per_day": round(actual, 2), "needed_per_day": round(needed, 2),
            "speed": round(actual / needed, 2) if needed else None, "days": days,
            "basis": "deadline" if deadline and parse_date(deadline) >= start else "pace"}


def format_speed(sp):
    if not sp or sp["speed"] is None:
        return "speed — · not enough data yet"
    return (f"speed {sp['speed']:.2f}× · {sp['lessons_per_day']:.2f} lessons/day, "
            f"needed {sp['needed_per_day']:.2f}"
            + (" (by the deadline)" if sp["basis"] == "deadline" else ""))


def due_cards(plan_slug, prog, today, include_future=False):
    out = []
    for c in prog["cards"]:
        if c.get("retired"):
            continue
        if include_future or parse_date(c["due"]) <= today:
            out.append(dict(c, plan=plan_slug, leech=c.get("lapses", 0) >= LEECH_LAPSES))
    out.sort(key=lambda c: (c["due"], c["id"]))
    return out


def review_state(card, events=(), ref=None):
    """SM-2 state, or a lazy migration that leaves existing due dates alone."""
    if card.get("srs", {}).get("algorithm") == "sm2":
        return dict(card["srs"])
    history = [e for e in events if e["type"] == "review" and e["id"] == ref]
    successes = 0
    for event in reversed(history):
        if event["result"] != "pass":
            break
        successes += 1
    if not history:
        successes = max(0, card.get("box", 1) - 1)
    interval = (max(1, (parse_date(card["due"]) - parse_date(card["last"])).days)
                if card.get("last") else LEGACY_INTERVALS[min(card.get("box", 1), 7) - 1])
    return {"algorithm": "sm2", "ease": 2.5, "interval": interval, "successes": successes}


def schedule_review(state, quality, delay=0):
    """SM-2 day intervals: https://super-memory.org/archive/english/ol/sm2.htm

    Use the PREVIOUS EF for this interval, then update it. On a failed recall,
    reset the sequence, retaining EF (step 6 of the original specification).
    Same-day corrective practice is handled by the tutor, not another grade.

    `delay` = days the review was overdue. Not part of SM-2: a confident recall
    (q4: half the delay, q5: all of it) after a longer gap proves a longer
    interval, as in Anki. q3 and the fixed first two steps ignore it.
    """
    ease, successes = state["ease"], state["successes"]
    if quality < 3:
        interval, successes = 1, 0
    else:
        base = state["interval"]
        if successes >= 2 and delay > 0 and quality >= 4:
            base += delay if quality == 5 else delay // 2
        interval = (1 if successes == 0 else 6 if successes == 1
                    else math.ceil(base * ease))
        successes += 1
        ease = max(1.3, round(ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 8))
    return {"algorithm": "sm2", "ease": ease, "interval": min(MAX_REVIEW_INTERVAL, interval),
            "successes": successes}


def parse_review_budget(value):
    """'auto' or a whole number 0..MAX_REVIEWS."""
    if value == "auto":
        return value
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if not 0 <= n <= MAX_REVIEWS:
        raise LearnError(f"daily reviews must be 'auto' or a number between 0 and {MAX_REVIEWS}")
    return n


def warmup_reviews(st, plans, limit=None):
    """Read-only preview of the remaining daily warm-up, shared by all chats.

    Issuing a question and grading recall are different actions. An interrupted
    question must not lead every new session, or count as a successful recall.
    Count legacy grades too, so old clients share the same daily budget.

    limit None = config `daily_reviews` (default auto). Auto follows the day's load
    over every active plan: cards still due plus cards seen today, so the budget
    stays put while grading moves cards out of the due list.
    Returns (cards to preview, cards seen today, budget, today's load).
    """
    budget = parse_review_budget(st.cfg.get("daily_reviews", "auto") if limit is None else limit)
    today = iso(st.today)
    seen = set()
    by_lesson = {}
    load = 0
    selected = {slug: (plan, prog) for slug, plan, prog in plans}
    for slug in st.slugs():
        _, prog = selected[slug] if slug in selected else st.load(slug)
        for card in prog["cards"]:
            if card.get("presented") == today or card.get("last") == today:
                seen.add((slug, card["id"]))
                group = (slug, card["lesson"])
                by_lesson[group] = by_lesson.get(group, 0) + 1
        if prog["status"] == "active" or slug in selected:
            load += sum(1 for c in due_cards(slug, prog, st.today) if (slug, c["id"]) not in seen)
    load += len(seen)
    if budget == "auto":
        budget = max(AUTO_REVIEWS[0], min(AUTO_REVIEWS[1], load))
    candidates = [c for slug, _, prog in plans for c in due_cards(slug, prog, st.today)
                  if (slug, c["id"]) not in seen]
    shown = []
    for _ in range(min(max(0, budget - len(seen)), len(candidates))):
        # Mix lessons/plans; within each topic prefer least recently seen cards
        # before using due date and ID to break ties. Unanswered cards rotate.
        card = min(candidates, key=lambda c: (by_lesson.get((c["plan"], c["lesson"]), 0),
                                             max(c.get("presented", ""), c.get("last", "")),
                                             c["due"], c["plan"], c["id"]))
        shown.append(card)
        candidates.remove(card)
        group = (card["plan"], card["lesson"])
        by_lesson[group] = by_lesson.get(group, 0) + 1
    return shown, len(seen), budget, load


def review_load_text(budget, load):
    """One warning line when today's due reviews outgrow the daily budget, else None."""
    if load <= budget:
        return None
    return (f"⚠ review backlog: {load} due today, daily budget {budget} — make at most 2 cards per lesson; "
            f"with more time, raise it: `learn config daily_reviews <n>` (≤ {MAX_REVIEWS})")


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

def normalize_plan(plan, today, warnings=None):
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
    # --- graph ---
    ids = {l["id"] for u in units for l in u.get("lessons", []) if l.get("id")}
    has_requires = any("requires" in l for u in units for l in u.get("lessons", []))
    if has_requires and not plan.get("graph"):
        errs.append("lessons use 'requires' but the plan has no \"graph\": true")
    for u in units:
        for l in u.get("lessons", []):
            if "requires" in l:
                if not isinstance(l["requires"], list):
                    errs.append(f"lesson {l.get('id')}: 'requires' must be a list of lesson ids")
                    continue
                for r in l["requires"]:
                    if r not in ids:
                        errs.append(f"lesson {l.get('id')}: requires unknown lesson '{r}'")
                    elif r == l.get("id"):
                        errs.append(f"lesson {r} requires itself")
            if l.get("kind") == "junction" and not plan.get("graph"):
                errs.append(f"lesson {l.get('id')}: kind 'junction' needs \"graph\": true")
    if plan.get("graph") and not errs:
        cyc = G.find_cycle(G.requires_map(plan))
        if cyc:
            errs.append("prerequisite cycle: " + " → ".join(cyc))
    if errs:
        raise LearnError("invalid plan:\n  - " + "\n  - ".join(errs))
    if warnings is not None:
        warnings.extend(G.graph_warnings(plan))
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
    gm = G.graph_mode(plan)
    reqs = G.requires_map(plan)
    sat = G.satisfied_ids(plan, prog)
    if gm:
        level = "lessons" if c["total"] <= 50 else "units"
        model = G.build_model(plan, prog, level)
        out += ["## Map", "", "```mermaid", G.render_mermaid(plan["title"], plan, prog, model), "```", "",
                "_Tracks are parallel branches; ⭐ junctions need several skills at once. "
                "`▶` = ready now, `🔒` = locked._", ""]
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
            mark = ""
            if gm and st in ("pending", "retry"):
                mark = " 🔒" if st == "pending" and not all(r in sat for r in reqs[l["id"]]) else " ▶"
            needs = ""
            if gm and ("requires" in l) and reqs[l["id"]]:
                needs = " — needs " + ", ".join(reqs[l["id"]])
            star = "⭐ " if l["kind"] == "junction" else ""
            out.append(f"- [{MARK[st]}] {star}**{l['id']}** {l['title']}{kind}{mark}{needs}{tail}")
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
    if args.chat_format:
        cfg["chat_format"] = args.chat_format
    cfg.setdefault("chat_format", "plain")
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
    print(f"initialised {home} (language={cfg['language']}, chat_format={cfg['chat_format']}"
          + (f", timezone={cfg['timezone']}" if cfg.get("timezone") else "") + ")")
    print("tip: make this folder a private git repo and run `learn sync` to share progress across devices")


def cmd_config(args):
    home = home_dir(args)
    if not home.exists():
        raise LearnError(f"{home} does not exist — run `learn init` first")
    cfg = load_config(home)
    if args.key is None:
        emit(args, cfg, "\n".join(f"{k} = {v}" for k, v in cfg.items()) or "(empty)")
        return
    if args.key not in CONFIG_KEYS:
        raise LearnError(f"unknown key '{args.key}' (known: {', '.join(CONFIG_KEYS)})")
    if args.value is None:
        print(cfg.get(args.key, "auto" if args.key == "daily_reviews" else ""))
        return
    allowed = CONFIG_KEYS[args.key]
    if allowed and args.value not in allowed:
        raise LearnError(f"{args.key} must be one of: {', '.join(allowed)}")
    value = parse_review_budget(args.value) if args.key == "daily_reviews" else args.value
    cfg[args.key] = value
    save_json(home / "config.json", cfg)
    print(f"{args.key} = {value}")


def cmd_profile(args):
    """Show the learner profile, or replace it from stdin (the agent keeps it up to date)."""
    home = home_dir(args)
    if not home.exists():
        raise LearnError(f"{home} does not exist — run `learn init` first")
    path = home / "profile.md"
    if args.stdin:
        text = args.stdin_text
        if not text.strip():
            raise LearnError("refusing to replace the profile with empty text")
        write_text(path, text.rstrip("\n") + "\n")
        print("profile updated")
    elif path.exists():
        print(path.read_text(encoding="utf-8").rstrip("\n"))
    else:
        raise LearnError("no profile yet — run `learn init`")


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
    check_slug(args.slug)
    raw = args.stdin_text if args.stdin else Path(args.file).read_text(encoding="utf-8")
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LearnError(f"plan is not valid JSON: {e}")
    warns = []
    plan = normalize_plan(plan, st.today, warns)
    exists = (st.plans_dir / args.slug / "plan.json").exists()
    if exists and not args.update:
        raise LearnError(f"plan '{args.slug}' exists — use --update to replace the plan and keep progress")
    prog = st.load(args.slug)[1] if exists else {"status": "active", "lessons": {}, "cards": [],
                                                  "next_card": 1, "events": []}
    ids = {l["id"] for l in lessons_of(plan)}
    orphans = sorted(set(prog["lessons"]) - ids)
    if orphans:
        print(f"warning: progress exists for lessons no longer in the plan: {', '.join(orphans)}", file=sys.stderr)
    warns += progress_warnings(plan, prog)
    if exists:
        warns += G.migration_warnings(st.load(args.slug)[0], plan)
    for w in warns:
        print(f"warning: {w}", file=sys.stderr)
    st.save(args.slug, plan, prog)
    c = counts(plan, prog)
    print(f"{'updated' if exists else 'created'} plan '{args.slug}': {c['total']} lessons "
          f"in {len(plan['units'])} units · view: {st.plans_dir / args.slug / 'PLAN.md'}")
    pr = pace_report(plan, prog, st.today)
    if pr["projected_finish"]:
        print(f"projected finish: {pr['projected_finish']}")
    if pr["past_deadline"]:
        print(f"⚠ projected finish is after the deadline ({plan['deadline']}): cut scope or add study days — don't just raise the pace", file=sys.stderr)


def progress_warnings(plan, prog):
    out = []
    early = sorted({e["date"] for e in prog["events"] if e["date"] < plan["start_date"]})
    if early:
        out.append(f"progress has events before start_date {plan['start_date']} (from {early[0]}); "
                   f"the plan is treated as started on {early[0]} — run `learn start --date {early[0]}` to make it official")
    return out


def cmd_validate(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    warns = []
    normalize_plan(plan, st.today, warns)
    for w in warns + progress_warnings(plan, prog):
        print(f"warning: {w}", file=sys.stderr)
    print(f"plan '{slug}' is valid")


def cmd_start(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    day = args.date or iso(st.today)
    try:
        parse_date(day)
    except ValueError:
        raise LearnError("--date must be YYYY-MM-DD")
    plan["start_date"] = day
    st.save(slug, plan, prog)
    pr = pace_report(plan, prog, st.today)
    print(f"plan '{slug}' starts {day}" + (f" · projected finish {pr['projected_finish']}" if pr["projected_finish"] else ""))


def cmd_render(args):
    st = Store(args)
    for slug, plan, prog in st.select(args.plan):
        st.save(slug, plan, prog)
        print(f"rendered {st.plans_dir / slug / 'PLAN.md'}")


def cmd_show(args):
    """Print the plan itself: JSON to edit and feed back to `create --update`, or the rendered PLAN.md."""
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    if args.md:
        print(render_plan_md(slug, plan, prog, st.today))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))


def plan_status_summary(st, slug, plan, prog):
    c = counts(plan, prog)
    pr = pace_report(plan, prog, st.today)
    cards = [x for x in prog["cards"] if not x.get("retired")]
    due = due_cards(slug, prog, st.today)
    nxt = pending_lessons(plan, prog)
    pace = plan["pace"]
    two_weeks = pace["lessons_per_day"] * len(pace["days"]) * 2
    return {"slug": slug, "title": plan["title"], "status": prog["status"], **c,
            "leeches": sum(1 for x in cards if x.get("lapses", 0) >= LEECH_LAPSES),
            # rolling wave: the detailed part is nearly done and `later` still holds stages to plan
            "extend_soon": bool(plan.get("later")) and 0 < c["remaining"] <= two_weeks,
            "percent": pct(c["done"], c["total"]),
            "pace": {"lessons_per_day": plan["pace"]["lessons_per_day"], "days": plan["pace"]["days"],
                     **{k: pr[k] for k in ("behind", "ahead", "projected_finish", "past_deadline")},
                     "speed": speed_report(plan, prog, st.today)},
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
                  f"  reviews due: {s['reviews_due']} of {s['cards']} cards"
                  + (f" · ⚠ {s['leeches']} leech(es) — rewrite or repair" if s["leeches"] else "")]
        sp = p["speed"]
        if sp and sp["speed"] is not None:
            lines.insert(-1, "  " + format_speed(sp))
        if nxt:
            lines.append(f"  next: {nxt['id']} {nxt['title']}")
        if s["extend_soon"]:
            lines.append(f"  ⏭ only {s['remaining']} detailed lesson(s) left and `later` has {len(plan['later'])} stage(s): "
                         "time to detail the next stage (study-plan → Revising)")
        if G.graph_mode(plan):
            s["tracks"] = G.track_summary(plan, prog)
            for t in s["tracks"]:
                ready = ", ".join(t["ready"][:3]) or "—"
                lines.append(f"  {t['id']} {t['title']}: {bar(t['done'], t['total'], 8)} {t['done']}/{t['total']} · ready: {ready}")
    _, _, budget, load = warmup_reviews(st, [p for p in plans if p[2]["status"] == "active"] or plans)
    data["reviews"] = {"budget": budget, "load": load}
    warning = review_load_text(budget, load)
    if warning:
        lines += ["", warning]
    emit(args, data, "\n".join(lines))


def day_quota(plan, prog, today):
    """(scheduled today?, lessons still to teach today)."""
    scheduled = is_scheduled(plan, today) and today >= effective_start(plan, prog)
    left = plan["pace"]["lessons_per_day"] - len(lesson_events_on(prog, today))
    return scheduled, max(0, left) if scheduled else 0


def last_attempt(prog, lid):
    """What the learner left behind last time: score, note (from `done` or a `note` checkpoint)."""
    rec = prog["lessons"].get(lid, {})
    return {k: rec[k] for k in ("score", "note", "noted") if k in rec}


def lesson_history_lines(prog, lid):
    rec = prog["lessons"].get(lid, {})
    out = []
    bits = []
    if rec.get("status") == "retry" and rec.get("score") is not None:
        bits.append(f"last score {rec['score']:.0%} ({rec.get('date', '?')})")
    if rec.get("note"):
        bits.append(f"note ({rec.get('noted', rec.get('date', '?'))}): {rec['note']}")
    if bits:
        out.append("      last time: " + " · ".join(bits))
    failed = sum(1 for e in prog["events"] if e["type"] == "lesson" and e["id"] == lid and not e.get("passed", True))
    if rec.get("status") == "retry" and failed >= 2:
        out.append(f"      ⚠ failed {failed} times — don't re-teach it the same way: repair the prerequisite it "
                   "rests on, split it, or change the practice")
    return out


def leech_mark(card):
    return f" ⚠ leech ({card['lapses']} lapses)" if card.get("leech") else ""


def cmd_today(args):
    st = Store(args)
    plans = st.select(args.plan)
    t = st.today
    data = {"date": iso(t), "weekday": DAYS[t.weekday()], "streak": streak(st),
            "language": st.cfg.get("language", "en"), "chat_format": st.cfg.get("chat_format", "plain"),
            "plans": [], "reviews": []}
    lines = [f"📅 {iso(t)} ({DAYS[t.weekday()]}) · streak {data['streak']} · "
             f"language {data['language']} · chat_format {data['chat_format']}"]
    all_due = []
    for slug, plan, prog in plans:
        lpd = plan["pace"]["lessons_per_day"]
        done_today = len(lesson_events_on(prog, t))
        start = effective_start(plan, prog)
        started = t >= start
        scheduled, quota = day_quota(plan, prog, t)
        pr = pace_report(plan, prog, t)
        sp = speed_report(plan, prog, t)
        try:
            todo = G.day_plan(plan, prog, quota, iso(t), args.track) if quota else []
        except KeyError as e:
            raise LearnError(e.args[0])
        all_due += due_cards(slug, prog, t)
        gm = G.graph_mode(plan)
        units = G.unit_of(plan)
        utitle = {u["id"]: u["title"] for u in plan["units"]}
        reqs = G.requires_map(plan)
        state = "active" if scheduled else ("not_started" if not started else "rest")
        data["plans"].append({"slug": slug, "title": plan["title"], "scheduled_today": scheduled, "state": state,
                              "starts_on": iso(start), "graph": gm,
                              "quota": quota, "done_today": done_today, "behind": pr["behind"],
                              "projected_finish": pr["projected_finish"], "speed": sp,
                              "lessons": [dict(l, status=lesson_state(prog, l["id"]),
                                               attempts=prog["lessons"].get(l["id"], {}).get("attempts", 0),
                                               **last_attempt(prog, l["id"]),
                                               track=units[l["id"]], track_title=utitle[units[l["id"]]],
                                               requires=reqs[l["id"]] if gm else [])
                                          for l in todo],
                              "pass_score": plan["pace"]["pass_score"]})
        head = f"== {slug} — {plan['title']}: "
        if scheduled:
            head += f"{done_today}/{lpd} done today"
        elif not started:
            head += f"starts {iso(start)} (run `learn start --plan {slug}` to begin today)"
        else:
            head += "rest day"
        head += " =="
        lines += ["", head, "  " + format_speed(sp)]
        if gm and scheduled:
            lines.append("  🗺 graph plan: lessons come from different tracks on purpose (interleaving). "
                         "After each `learn done`, re-run `learn today` — new lessons may unlock.")
        if pr["behind"]:
            lines.append(f"  behind by {pr['behind']} lesson(s): do NOT double up — keep the normal quota; the finish date moves"
                         + (f" (≈ {pr['projected_finish']})" if pr["projected_finish"] else ""))
        for l in todo:
            retry = " (RETRY — re-teach the gaps)" if lesson_state(prog, l["id"]) == "retry" else ""
            track = f" · track {units[l['id']]} {utitle[units[l['id']]]}" if gm else ""
            star = "⭐ " if l["kind"] == "junction" else ""
            lines += [f"  ▶ {star}{l['id']} · {l['title']} · {l['minutes']}m [{l['kind']}]{track}{retry}",
                      f"      objective: {l['objective']}",
                      f"      check: {l['check']}"]
            if gm and l["kind"] == "junction":
                lines.append("      ⭐ junction: needs several skills at once — builds on " + ", ".join(reqs[l["id"]])
                             + " (recall those first)")
            if l.get("nodes"):
                lines.append("      key ideas: " + "; ".join(l["nodes"]))
            lines += lesson_history_lines(prog, l["id"])
        if scheduled and not todo and counts(plan, prog)["remaining"] == 0:
            lines.append("  plan complete 🎉")
        elif scheduled and not todo and quota == 0:
            lines.append("  quota for today is done ✔")
        elif scheduled and not todo:
            lines.append("  nothing unlocked yet in this track filter — finish a prerequisite or pick another track"
                         if args.track else "  nothing unlocked right now — finish the lessons in progress (retry) first")
    shown, seen_today, budget, load = warmup_reviews(st, plans, args.max_reviews)
    data["reviews"] = shown
    data["reviews_due_total"] = len(all_due)
    data["reviews_today"] = seen_today
    data["reviews_remaining"] = len(shown)
    data["reviews_budget"] = budget
    data["reviews_load"] = load
    if shown:
        lines += ["", f"🔁 Warm-up preview ({len(shown)} remaining today, {len(all_due)} due, daily budget {budget}) — "
                  "use `learn review next` to issue ONE question, then `learn review grade` immediately after the reply:"]
        for c in shown:
            lines += [f"  {c['plan']}/{c['id']} (from {c['lesson']}, due {c['due']})" + leech_mark(c),
                      f"      Q: {c['q']}", f"      A: {c['a']}"]
    elif all_due and args.max_reviews == 0:
        lines += ["", "warm-up omitted for this lesson refresh"]
    elif all_due:
        lines += ["", "warm-up finished for today — continue the lessons; the backlog does not restart the warm-up"]
    elif not any(p["quota"] for p in data["plans"]):
        lines += ["", "nothing due — enjoy the rest ☕"]
    warning = review_load_text(budget, load) if args.max_reviews is None else None
    if warning:
        lines.append(warning)
    emit(args, data, "\n".join(lines))


MAX_PNG_BYTES = 1_000_000  # soft target: never shrink phone labels below 14 px


def cmd_graph(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    level, phone = args.level, args.format == "png" and not args.full
    try:
        model = G.build_model(plan, prog, level, args.track)
    except KeyError as e:
        raise LearnError(e.args[0])
    if args.track:
        t = model["tracks"][0]
        title, done, total = f"{plan['title']} · {t['title']}", t["done"], t["total"]
    else:
        c = counts(plan, prog)
        title, done, total = plan["title"], c["done"], c["total"]
    if args.json:
        print(json.dumps(model, ensure_ascii=False, indent=2))
        return
    if args.format not in ("svg", "png"):
        render = {"text": G.render_text, "md": G.render_md, "mermaid": G.render_mermaid}[args.format]
        print(render(title, plan, prog, model, done, total))
        return
    if args.scale is not None and not 1 <= args.scale <= 4:
        raise LearnError("--scale must be between 1 and 4")
    out = Path(args.out) if args.out else st.plans_dir / slug / f"graph.{args.format}"
    _, quota = day_quota(plan, prog, st.today)
    picks = [l["id"] for l in G.day_plan(plan, prog, quota, iso(st.today), args.track)] if level == "lessons" else []

    def draw(scale, max_px):
        return G.render_svg(title, plan, prog, model, done, total, today=iso(st.today), picks=picks,
                            lang=st.cfg.get("language", "en"), scale=scale, speed=speed_report(plan, prog, st.today),
                            max_px=max_px)
    if args.format == "svg":
        write_text(out, draw(1, None))
        print(out)
        return
    # png: phone size by default (2x, at most PHONE_PX wide); --scale or --full: exactly that density
    scale = args.scale if args.scale is not None else (1 if args.full else 2)
    max_px = G.PHONE_PX if phone and args.scale is None else None
    svg_path = out.with_suffix(".svg")
    while True:
        svg = draw(scale, max_px)
        if out.is_file() and svg_path.is_file() and out.stat().st_mtime >= svg_path.stat().st_mtime \
                and svg_path.read_text(encoding="utf-8") == svg:
            print("png: unchanged since the last render — cached", file=sys.stderr)
            break
        write_text(svg_path, svg)
        t0 = time.monotonic()
        try:
            exe = G.svg_to_png(svg_path, out)
        except RuntimeError as e:
            raise LearnError(f"{e}\n(SVG saved at {svg_path})")
        size = out.stat().st_size
        print(f"png: {exe}, {time.monotonic() - t0:.1f} s, {size // 1024} KB", file=sys.stderr)
        if exe != G.BEST_PNG:
            print(f"png: tip — {G.PNG_TIP}", file=sys.stderr)
        root = ET.fromstring(svg)
        actual_scale = float(root.get("width")) / float(root.get("viewBox").split()[2])
        min_scale = 14 / G.MIN_FONT
        if not max_px or size <= MAX_PNG_BYTES or actual_scale <= min_scale + 0.001:
            break
        scale = max(min_scale, actual_scale * 0.75)
    print(out)


def cmd_next(args):
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    try:
        todo = G.day_plan(plan, prog, args.n, iso(st.today), args.track)
    except KeyError as e:
        raise LearnError(e.args[0])
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
    if G.graph_mode(plan) and not args.force:
        missing = G.missing_requirements(plan, prog, lesson["id"])
        if missing:
            raise LearnError(f"{lesson['id']} is locked: finish {', '.join(missing)} first "
                             f"(or pass --force to record it anyway)")
    rec = prog["lessons"].setdefault(lesson["id"], {})
    rec["attempts"] = rec.get("attempts", 0) + 1
    passed = args.accept or args.score is None or args.score >= plan["pace"]["pass_score"]
    rec.update({"status": "done" if passed else "retry", "date": iso(st.today)})
    if args.score is not None:
        rec["score"] = args.score
    if args.note:
        rec.update({"note": args.note, "noted": iso(st.today)})
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


def cmd_note(args):
    """Checkpoint for an interrupted lesson: where it stopped and what was shaky. Not a completion."""
    st = Store(args)
    slug, plan, prog = st.select_one(args.plan)
    lesson = find_lesson(plan, args.lesson)
    text = " ".join(args.text.split())
    if not text:
        raise LearnError("--text must not be empty")
    rec = prog["lessons"].setdefault(lesson["id"], {})
    rec.update({"note": text, "noted": iso(st.today)})
    st.save(slug, plan, prog)
    print(f"noted {slug}/{lesson['id']} ({lesson_state(prog, lesson['id'])}): {text}")


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
            items = json.loads(args.stdin_text)
        except json.JSONDecodeError as e:
            raise LearnError(f"stdin is not valid JSON: {e}")
    else:
        if not (args.q and args.a):
            raise LearnError("pass --q and --a (or --stdin with a JSON list of {q, a})")
        items = [{"q": args.q, "a": args.a}]
    if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("q") and i.get("a") for i in items):
        raise LearnError("cards must be a list of objects with non-empty 'q' and 'a'")
    first_due = st.today + timedelta(days=1)
    new = []
    for i in items:
        cid = f"C{prog['next_card']:03d}"
        prog["next_card"] += 1
        card = {"id": cid, "lesson": args.lesson, "q": i["q"], "a": i["a"], "box": 1, "due": iso(first_due),
                "reps": 0, "lapses": 0, "created": iso(st.today),
                "srs": {"algorithm": "sm2", "ease": 2.5, "interval": 1, "successes": 0}}
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
    text = "\n".join(f"{c['plan']}/{c['id']} due {c['due']}\n   Q: {c['q']}\n   A: {c['a']}" for c in rows) \
        or "no reviews due"
    emit(args, rows, text)


def cmd_review_next(args):
    st = Store(args)
    plans = st.select(args.plan)
    shown, seen_today, budget, _ = warmup_reviews(st, plans, args.max_reviews)
    card = shown[0] if shown else None
    if card:
        slug = card["plan"]
        _, plan, prog = next(p for p in plans if p[0] == slug)
        stored = next(c for c in prog["cards"] if c["id"] == card["id"])
        stored["presented"] = iso(st.today)
        st.save(slug, plan, prog)
        card["presented"] = iso(st.today)
    data = {"date": iso(st.today), "card": card, "budget": budget,
            "reviews_today": seen_today + bool(card), "reviews_remaining": max(0, len(shown) - 1)}
    text = (f"{card['plan']}/{card['id']} (from {card['lesson']}, due {card['due']}){leech_mark(card)}\n"
            f"Q: {card['q']}\nA (tutor only): {card['a']}\n"
            "Ask closed-book, wait for the reply, then record `learn review grade` before another question."
            + ("\nLeech: after grading, find why it keeps slipping — rewrite it (`learn card edit`) or repair "
               "the idea it rests on." if card["leech"] else "")
            if card else "no warm-up reviews left today — continue the lessons")
    emit(args, data, text)


def cmd_review_grade(args):
    st = Store(args)
    slug, cid = split_card_ref(args.card, args.plan)
    slug, plan, prog = st.select_one(slug)
    card = next((c for c in prog["cards"] if c["id"] == cid), None)
    if not card:
        raise LearnError(f"no such card: {cid}")
    quality = args.quality if args.quality is not None else (4 if args.result == "pass" else 1)
    if (quality >= 3) != (args.result == "pass"):
        raise LearnError("quality 3..5 requires pass; quality 0..2 requires fail")
    conf = args.confidence
    if conf is not None and not 0 <= conf <= 100:
        raise LearnError("confidence is 0..100 (the learner's stated chance that the answer is right)")
    capped = args.result == "pass" and conf is not None and conf < FRAGILE_CONFIDENCE and quality > 3
    if capped:
        quality = 3  # a pass the learner didn't trust is fragile or lucky: keep the interval short
    if card.get("retired"):
        raise LearnError(f"card {cid} is retired")
    if card.get("last") == iso(st.today):
        print(f"{slug}/{cid}: already graded today — schedule unchanged (due {card['due']})")
        return
    if parse_date(card["due"]) > st.today:
        raise LearnError(f"card {cid} is not due until {card['due']} — early practice does not reschedule recall")
    delay = max(0, (st.today - parse_date(card["due"])).days)
    state = schedule_review(review_state(card, prog["events"], f"{slug}/{cid}"), quality, delay)
    ok = quality >= 3
    card["reps"] += 1
    if not ok:
        card["lapses"] += 1
    card["srs"] = state
    card["box"] = min(state["successes"] + 1, len(LEGACY_INTERVALS))  # legacy readers only
    days = min(state["interval"], (date.max - st.today).days)
    card["srs"]["interval"] = days
    card["due"] = iso(st.today + timedelta(days=days))
    card["last"] = iso(st.today)
    prog["events"].append({"date": iso(st.today), "type": "review", "id": f"{slug}/{cid}",
                           "result": args.result, "quality": quality, "scheduler": "sm2",
                           "interval": days, "ease": state["ease"], "delay": delay,
                           **({"confidence": conf} if conf is not None else {})})
    st.save(slug, plan, prog)
    leech = "" if ok or card["lapses"] < LEECH_LAPSES else \
        f"\n⚠ leech: forgotten {card['lapses']} times — rewrite it (`learn card edit`) or repair the idea it rests on"
    signal = ""
    if capped:
        signal = f"\nfragile: correct at {conf}% confidence — graded q3 so it comes back sooner; one reinforcing line, no praise"
    elif not ok and conf is not None and conf >= CONFIDENT_MISS:
        signal = (f"\n⚠ confident miss ({conf}%): likely a misconception, not a gap — find the wrong model "
                  "behind the answer and repair it; revealing the right answer is not enough")
    print(f"{slug}/{cid}: {'✓' if ok else '✗'} → SM-2 q{quality}, EF {state['ease']:g}, "
          f"next review in {days}d ({card['due']}){leech}{signal}")


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


def cmd_card_edit(args):
    """Rewrite a card's wording, keeping its schedule, history and id."""
    st = Store(args)
    slug, cid = split_card_ref(args.card, args.plan)
    slug, plan, prog = st.select_one(slug)
    card = next((c for c in prog["cards"] if c["id"] == cid), None)
    if not card:
        raise LearnError(f"no such card: {cid}")
    changes = {k: v.strip() for k, v in (("q", args.q), ("a", args.a)) if v is not None}
    if not changes or not all(changes.values()):
        raise LearnError("pass a non-empty --q and/or --a")
    card.update(changes, edited=iso(st.today))
    st.save(slug, plan, prog)
    print(f"edited {slug}/{cid} — schedule unchanged (due {card['due']})")


def mature_reviews(prog, since):
    """Review events since `since` whose card had gone MATURE_DAYS+ without a review: true retention."""
    last_seen, out = {}, []
    for e in sorted((e for e in prog["events"] if e["type"] == "review"), key=lambda e: e["date"]):
        cid = e["id"].split("/", 1)[-1]
        prev = last_seen.get(cid)
        if prev and e["date"] >= since and (parse_date(e["date"]) - parse_date(prev)).days >= MATURE_DAYS:
            out.append(e)
        last_seen[cid] = e["date"]
    return out


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
        mature = mature_reviews(prog, since)
        qualities = [e["quality"] for e in rev if e.get("quality") is not None]
        row = {"slug": slug, "lessons": len(les), "lessons_passed": sum(1 for e in les if e["passed"]),
               "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
               "reviews": len(rev), "reviews_passed": passed, "active_days": len(days),
               "avg_quality": round(sum(qualities) / len(qualities), 2) if qualities else None,
               "mature_reviews": len(mature), "mature_passed": sum(1 for e in mature if e["result"] == "pass"),
               "new_cards": sum(1 for c in prog["cards"] if c.get("created", "") >= since),
               "leeches": sum(1 for c in prog["cards"] if not c.get("retired") and c.get("lapses", 0) >= LEECH_LAPSES)}
        data["plans"].append(row)
        c = counts(plan, prog)
        lines += ["", f"{slug}: {c['done']}/{c['total']} total",
                  f"  lessons: {row['lessons_passed']}/{row['lessons']} passed"
                  + (f" · avg score {row['avg_score']:.0%}" if row["avg_score"] is not None else ""),
                  f"  reviews: {passed}/{len(rev)} recalled" + (f" ({pct(passed, len(rev))}%)" if rev else "")
                  + (f" · avg quality {row['avg_quality']:.1f}/5" if row["avg_quality"] is not None else ""),
                  f"  long-term recall (≥ {MATURE_DAYS}d gap): " + (
                      f"{row['mature_passed']}/{row['mature_reviews']} ({pct(row['mature_passed'], row['mature_reviews'])}%)"
                      if mature else "no mature cards reviewed yet"),
                  f"  new cards: {row['new_cards']}" + (f" · ⚠ leeches: {row['leeches']}" if row["leeches"] else ""),
                  f"  active days: {len(days)}/7"]
    emit(args, data, "\n".join(lines))


def cmd_nudge(args):
    """No-LLM daily reminder. Prints nothing when there is nothing to do, so a
    scheduler can treat empty output as a silent tick. --markdown emits rich
    Markdown (bold, task list) for Telegram rich messages."""
    st = Store(args)
    if not st.home.exists() or not st.active():
        return
    lang = args.language or st.cfg.get("language", "en")
    ru = lang.lower().startswith("ru")
    t = st.today
    plans = st.active()
    todo, behind = [], 0
    due_total = len(warmup_reviews(st, plans)[0])
    for slug, plan, prog in plans:
        scheduled = is_scheduled(plan, t) and t >= effective_start(plan, prog)
        if scheduled:
            quota = max(0, plan["pace"]["lessons_per_day"] - len(lesson_events_on(prog, t)))
            utitle = {u["id"]: u["title"] for u in plan["units"]}
            units = G.unit_of(plan)
            for l in G.day_plan(plan, prog, quota, iso(t)):
                todo.append((slug, dict(l, tag=utitle[units[l["id"]]] if G.graph_mode(plan) else "")))
        behind = max(behind, pace_report(plan, prog, t)["behind"])
    if not todo and not due_total:
        return
    sk = streak(st)
    last = last_activity(st)
    away = (t - last).days if last else 0
    md = args.markdown
    b = (lambda x: f"**{x}**") if md else (lambda x: x)
    def item(l):
        tag = f" ({l['tag']})" if l.get("tag") else ""
        star = "⭐ " if l.get("kind") == "junction" else ""
        return f"- [ ] {star}{l['id']} · {l['title']}{tag}" if md else f"• {star}{l['id']} · {l['title']}{tag}"
    if ru:
        head = "📚 С возвращением! Ничего не потеряно — начнём с лёгкого." if away >= 4 else "📚 Время учиться!"
        parts = []
        if todo:
            parts.append(f"{len(todo)} {ru_plural(len(todo), ('урок', 'урока', 'уроков'))}")
        if due_total:
            parts.append(f"{due_total} {ru_plural(due_total, ('повторение', 'повторения', 'повторений'))}")
        today_line = "Сегодня: " + " + ".join(parts) + (f" · серия {sk} 🔥" if sk else "")
        late = f"Отстаём на {behind} — это нормально: план сдвинется, а не навалится." if behind >= 2 else None
        cta = f"Напиши {args.cta}, чтобы начать."
    else:
        head = "📚 Welcome back! Nothing is lost — we'll start light." if away >= 4 else "📚 Time to learn!"
        parts = []
        if todo:
            parts.append(f"{len(todo)} lesson{'s' if len(todo) != 1 else ''}")
        if due_total:
            parts.append(f"{due_total} review{'s' if due_total != 1 else ''}")
        today_line = "Today: " + " + ".join(parts) + (f" · streak {sk} 🔥" if sk else "")
        late = f"Behind by {behind} — that's fine: the plan shifts instead of piling up." if behind >= 2 else None
        cta = f"Send {args.cta} to start."
    lines = [b(head), today_line, ""] if md else [head, today_line]
    lines += [item(l) for _, l in todo]
    if md and todo:
        lines.append("")
    if late:
        lines.append(f"> {late}" if md else late)
    lines.append(cta)
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
    p.add_argument("--chat-format", choices=CHAT_FORMATS, help="plain (default) or rich (Telegram rich messages: tables, LaTeX, task lists)")

    p = add("config", cmd_config, "show or set config (language, timezone, chat_format, daily_reviews)", plan=False)
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")

    p = add("profile", cmd_profile, "show the learner profile (profile.md), or replace it with --stdin", plan=False)
    p.add_argument("--stdin", action="store_true", help="replace profile.md with the text on stdin")

    add("plans", cmd_plans, "list plans with progress", plan=False)

    p = add("create", cmd_create, "create/update a plan from JSON (see skills/study-plan)", plan=False)
    p.add_argument("slug", help="short id, e.g. python-basics")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--stdin", action="store_true")
    p.add_argument("--update", action="store_true", help="replace an existing plan but keep its progress")

    add("validate", cmd_validate, "validate a plan's JSON")
    p = add("show", cmd_show, "print a plan: JSON (editable, for create --update) or --md")
    p.add_argument("--md", action="store_true", help="the rendered PLAN.md instead of JSON")
    add("render", cmd_render, "regenerate PLAN.md")
    add("status", cmd_status, "progress, pace and projected finish")

    p = add("today", cmd_today, "today's lessons + warm-up reviews (start every session here)")
    p.add_argument("--max-reviews", type=int, help="daily warm-up budget across chats and plans (0..50; default: config daily_reviews, "
                        "auto = the day's due load within 6..30)")
    p.add_argument("--track", help="graph plans: only lessons from this track (unit id) — e.g. 'today I want AI'")

    p = add("next", cmd_next, "upcoming lessons regardless of daily quota")
    p.add_argument("-n", "--count", dest="n", type=int, default=3, help="how many lessons (default 3)")
    p.add_argument("--track", help="graph plans: only this track (unit id)")

    p = add("graph", cmd_graph, "the knowledge map: tracks, junctions, what is ready/locked")
    p.add_argument("--format", choices=["text", "md", "mermaid", "svg", "png"], default="text",
                   help="text (chat code block), md (rich Markdown table), mermaid (Obsidian), svg/png (image file)")
    p.add_argument("--level", choices=["lessons", "units"], default="lessons")
    p.add_argument("-o", "--out", help="output file for svg/png (default: plans/<slug>/graph.<ext>)")
    p.add_argument("--track", help="only this track (unit id), every lesson of it — readable on a phone")
    p.add_argument("--scale", type=float, help="png: exact pixel density, 1..4 (default: fit a phone — 2x, at most 1400 px "
                                               "wide; --full: 1)")
    p.add_argument("--full", action="store_true", help="png: natural size, without the phone width/file-size limits")

    p = add("start", cmd_start, "set the plan's start date (default: today)")
    p.add_argument("--date", help="YYYY-MM-DD")

    p = add("done", cmd_done, "record a finished lesson")
    p.add_argument("lesson")
    p.add_argument("--score", type=float, help="0..1 from the end-of-lesson check")
    p.add_argument("--note", help="one line: what was hard / what to revisit")
    p.add_argument("--accept", action="store_true", help="mark done even if score is below the pass bar")
    p.add_argument("--force", action="store_true", help="graph plans: record a lesson whose prerequisites are not done")

    p = add("skip", cmd_skip, "drop a lesson from the plan's remaining work")
    p.add_argument("lesson")

    p = add("note", cmd_note, "checkpoint an unfinished lesson: where it stopped, what was shaky (shown by `today`)")
    p.add_argument("lesson")
    p.add_argument("--text", required=True, help="one line, e.g. 'stopped after step 3; mixes up X and Y'")

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
    p = card.add_parser("edit", parents=[common], help="rewrite a card's question/answer, keeping its schedule")
    p.add_argument("card", help="C007 or slug/C007")
    p.add_argument("--q")
    p.add_argument("--a")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_card_edit)

    rev = sub.add_parser("review", help="spaced reviews").add_subparsers(dest="sub", required=True, metavar="action")
    p = rev.add_parser("next", parents=[common], help="issue one due question and remember its presentation")
    p.add_argument("--plan")
    p.add_argument("--max-reviews", type=int, help="daily warm-up budget across chats and plans (0..50; default: config daily_reviews, "
                        "auto = the day's due load within 6..30)")
    p.set_defaults(fn=cmd_review_next)
    p = rev.add_parser("due", parents=[common], help="list due cards")
    p.add_argument("--plan")
    p.add_argument("--all", action="store_true", help="include not-yet-due cards")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_review_due)
    p = rev.add_parser("grade", parents=[common], help="record recall result")
    p.add_argument("card", help="C007 or slug/C007")
    p.add_argument("result", choices=["pass", "fail"])
    p.add_argument("--quality", type=int, choices=range(6), help="SM-2 recall quality 0..5 (pass defaults to 4, fail to 1)")
    p.add_argument("--confidence", type=int, help="learner's stated confidence 0..100 that the answer was right: "
                   f"a pass below {FRAGILE_CONFIDENCE} is capped at q3, a fail at {CONFIDENT_MISS}+ is flagged as a misconception")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_review_grade)

    add("week", cmd_week, "last-7-days summary", plan=False)

    p = add("nudge", cmd_nudge, "daily reminder text (empty output = nothing to do)", plan=False)
    p.add_argument("--language", help="override config language (ru/en)")
    p.add_argument("--cta", default="/daily-lesson", help="what the learner should send to start (default /daily-lesson)")
    p.add_argument("--markdown", action="store_true", help="rich Markdown output (bold, task list, quote) for Telegram rich messages")

    add("sync", cmd_sync, "git commit + pull --rebase + push the data directory", plan=False)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.home = getattr(args, "home", None)
    args.json = getattr(args, "json", False)
    # Read stdin *before* taking the lock: a client that stalls mid-pipe must not block everyone else.
    args.stdin_text = sys.stdin.read() if getattr(args, "stdin", False) else None
    try:
        with home_lock(home_dir(args)):
            args.fn(args)
    except LearnError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
