"""Tests for learn.py — run: python3 scripts/test_learn.py"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import learn  # noqa: E402

PLAN = {
    "title": "Python basics", "goal": "Write small scripts", "level": "beginner",
    "pace": {"lessons_per_day": 2, "days": ["mon", "tue", "wed", "thu", "fri"]},
    "start_date": "2026-10-05",  # a Monday
    "outcomes": [{"id": "O1", "text": "Write a function", "bloom": "apply"}],
    "units": [{"id": "U1", "title": "Basics", "outcomes": ["O1"],
               "milestone": {"title": "FizzBuzz", "pass_bar": "no notes"},
               "lessons": [{"id": f"L0{i}", "title": f"Lesson {i}", "objective": f"obj {i}",
                            "check": f"check {i}", "outcomes": ["O1"]} for i in range(1, 7)]}],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "learning")
        os.environ["LEARN_TODAY"] = "2026-10-05"
        self.run_cmd("init", "--language", "ru")
        self.plan_file = os.path.join(self.tmp.name, "plan.json")
        Path(self.plan_file).write_text(json.dumps(PLAN))
        self.run_cmd("create", "py", "--file", self.plan_file)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("LEARN_TODAY", None)

    def run_cmd(self, *argv, day=None, expect=0, stdin=None):
        if day:
            os.environ["LEARN_TODAY"] = day
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = learn.main(["--home", self.home, *argv])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, expect, f"{argv}: {err.getvalue()}")
        return out.getvalue() if expect == 0 else err.getvalue()

    def js(self, *argv, **kw):
        return json.loads(self.run_cmd("--json", *argv, **kw))


class TestPlan(Base):
    def test_create_renders_markdown(self):
        md = (Path(self.home) / "plans/py/PLAN.md").read_text()
        self.assertIn("# Python basics", md)
        self.assertIn("- [ ] **L01** Lesson 1", md)

    def test_duplicate_requires_update(self):
        self.run_cmd("create", "py", "--file", self.plan_file, expect=1)
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")

    def test_validation_lists_all_problems(self):
        bad = json.loads(json.dumps(PLAN))
        bad["units"][0]["lessons"][1]["id"] = "L01"
        del bad["units"][0]["lessons"][2]["check"]
        bad["pace"]["days"] = ["funday"]
        msg = self.run_cmd("create", "bad", "--stdin", stdin=json.dumps(bad), expect=1)
        for needle in ("duplicate lesson id L01", "needs a 'check'", "pace.days"):
            self.assertIn(needle, msg)

    def test_deadline_warning(self):
        late = dict(PLAN, deadline="2026-10-06")  # 6 lessons need Mon-Wed; Wed 7th is too late
        Path(self.plan_file).write_text(json.dumps(late))
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")
        self.assertTrue(self.js("status")["plans"][0]["pace"]["past_deadline"])
        self.assertIn("after deadline", self.run_cmd("status"))

    def test_update_keeps_progress(self):
        self.run_cmd("done", "L01", "--score", "0.9")
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")
        self.assertEqual(self.js("status")["plans"][0]["done"], 1)


class TestDaily(Base):
    def test_quota_and_weekend(self):
        d = self.js("today")
        self.assertEqual([l["id"] for l in d["plans"][0]["lessons"]], ["L01", "L02"])
        sat = self.js("today", day="2026-10-10")
        self.assertFalse(sat["plans"][0]["scheduled_today"])
        self.assertEqual(sat["plans"][0]["lessons"], [])

    def test_done_consumes_quota_and_retry_does_not_loop(self):
        self.run_cmd("done", "L01", "--score", "0.9")
        self.run_cmd("done", "L02", "--score", "0.3")  # below pass bar -> retry
        d = self.js("today")["plans"][0]
        self.assertEqual(d["quota"], 0)  # both attempts used today's quota
        nxt = self.js("today", day="2026-10-06")["plans"][0]["lessons"]
        self.assertEqual(nxt[0]["id"], "L02")  # retried first
        self.assertEqual(nxt[0]["status"], "retry")

    def test_accept_overrides_pass_bar(self):
        self.run_cmd("done", "L01", "--score", "0.3", "--accept")
        self.assertEqual(self.js("status")["plans"][0]["done"], 1)

    def test_behind_never_doubles_quota(self):
        d = self.js("today", day="2026-10-08")  # Thursday, nothing done since Monday
        p = d["plans"][0]
        self.assertEqual(p["behind"], 6)  # Mon-Wed * 2
        self.assertEqual(len(p["lessons"]), 2)
        self.assertIsNotNone(p["projected_finish"])

    def test_projected_finish_skips_rest_days(self):
        out = self.js("status", day="2026-10-05")["plans"][0]["pace"]
        # 6 lessons at 2/day: Mon, Tue, Wed
        self.assertEqual(out["projected_finish"], "2026-10-07")

    def test_streak_ignores_rest_days(self):
        for day, lid in (("2026-10-08", "L01"), ("2026-10-09", "L02"), ("2026-10-12", "L03")):
            self.run_cmd("done", lid, "--score", "1", day=day)
        self.assertEqual(self.js("status", day="2026-10-12")["streak"], 3)  # Sat/Sun are rest days
        # missing Tuesday 13th (scheduled) breaks it once the day is over
        self.assertEqual(self.js("status", day="2026-10-14")["streak"], 0)

    def test_pause_hides_plan(self):
        self.run_cmd("pause", "py")
        self.run_cmd("today", expect=1)
        self.assertEqual(self.run_cmd("nudge"), "")


class TestConfig(Base):
    def test_chat_format_roundtrip_and_today(self):
        self.assertEqual(self.js("today")["chat_format"], "plain")
        self.run_cmd("config", "chat_format", "rich")
        self.assertEqual(self.run_cmd("config", "chat_format").strip(), "rich")
        d = self.js("today")
        self.assertEqual((d["chat_format"], d["language"]), ("rich", "ru"))
        self.assertIn("chat_format rich", self.run_cmd("today"))

    def test_rejects_bad_values(self):
        self.run_cmd("config", "chat_format", "fancy", expect=1)
        self.run_cmd("config", "colour", "red", expect=1)

    def test_init_flag(self):
        home = os.path.join(self.tmp.name, "other")
        code = learn.main(["--home", home, "init", "--chat-format", "rich"])
        self.assertEqual(code, 0)
        self.assertEqual(json.load(open(os.path.join(home, "config.json")))["chat_format"], "rich")


class TestReviews(Base):
    def test_leitner_cycle(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "What is a function?", "--a", "Reusable block")
        due = self.js("review", "due", day="2026-10-06")
        self.assertEqual(len(due), 1)
        self.run_cmd("review", "grade", "py/C001", "pass", day="2026-10-06")  # box 2 -> +3d
        self.assertEqual(self.js("review", "due", day="2026-10-08"), [])
        self.assertEqual(len(self.js("review", "due", day="2026-10-09")), 1)
        self.run_cmd("review", "grade", "C001", "fail", day="2026-10-09")  # back to box 1 -> +1d
        card = self.js("review", "due", "--all", day="2026-10-09")[0]
        self.assertEqual((card["box"], card["lapses"], card["due"]), (1, 1, "2026-10-10"))

    def test_box_caps_at_last_interval(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        day = "2026-10-06"
        for _ in range(10):
            self.run_cmd("review", "grade", "C001", "pass", day=day)
        card = self.js("review", "due", "--all", day=day)[0]
        self.assertEqual(card["box"], len(learn.INTERVALS))

    def test_batch_cards_and_today_lists_due(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin",
                     stdin=json.dumps([{"q": "q1", "a": "a1"}, {"q": "q2", "a": "a2"}]))
        d = self.js("today", day="2026-10-06")
        self.assertEqual(d["reviews_due_total"], 2)

    def test_bad_card_rejected(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin", stdin='[{"q":"only q"}]', expect=1)
        self.run_cmd("card", "add", "--lesson", "L99", "--q", "q", "--a", "a", expect=1)


class TestNudge(Base):
    def test_russian_nudge_and_silence(self):
        msg = self.run_cmd("nudge")
        self.assertIn("2 урока", msg)
        self.assertIn("/daily-lesson", msg)
        self.run_cmd("done", "L01", "--score", "1")
        self.run_cmd("done", "L02", "--score", "1")
        self.assertEqual(self.run_cmd("nudge"), "")  # quota met, no reviews due
        self.assertEqual(self.run_cmd("nudge", day="2026-10-10"), "")  # rest day

    def test_reviews_alone_trigger_nudge_on_rest_day(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.assertIn("1 повторение", self.run_cmd("nudge", day="2026-10-10"))

    def test_english(self):
        self.assertIn("Time to learn", self.run_cmd("nudge", "--language", "en"))

    def test_markdown_nudge(self):
        md = self.run_cmd("nudge", "--markdown")
        self.assertIn("**📚 Время учиться!**", md)
        self.assertIn("- [ ] L01 · Lesson 1", md)

    def test_plurals(self):
        f = ("урок", "урока", "уроков")
        self.assertEqual([learn.ru_plural(n, f) for n in (1, 2, 5, 11, 21, 22)],
                         ["урок", "урока", "уроков", "уроков", "урок", "урока"])


def lesson(lid, title, **kw):
    return dict({"id": lid, "title": title, "objective": f"obj {lid}", "check": f"check {lid}"}, **kw)


GRAPH_PLAN = {
    "title": "AI career", "goal": "Build an AI script", "graph": True,
    "pace": {"lessons_per_day": 2, "days": ["mon", "tue", "wed", "thu", "fri"]},
    "start_date": "2026-10-05",
    "units": [
        {"id": "PY", "title": "Python", "lessons": [lesson("P1", "Functions"), lesson("P2", "Lists"), lesson("P3", "Files")]},
        {"id": "AI", "title": "AI basics", "lessons": [lesson("A1", "What is a model"), lesson("A2", "Prompts")]},
        {"id": "PR", "title": "Projects", "lessons": [
            lesson("J1", "Chatbot script", kind="junction", requires=["P2", "A2"])]},
    ],
}


class GraphBase(Base):
    def setUp(self):
        super().setUp()
        self.gfile = os.path.join(self.tmp.name, "g.json")
        Path(self.gfile).write_text(json.dumps(GRAPH_PLAN))
        self.run_cmd("create", "ai", "--file", self.gfile)
        self.run_cmd("archive", "py")  # keep a single active plan so commands need no --plan

    def today_ids(self, **kw):
        d = self.js("today", "--plan", "ai", **kw)["plans"][0]
        return [l["id"] for l in d["lessons"]]


class TestGraphValidation(GraphBase):
    def bad(self, mutate, needle):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        mutate(plan)
        msg = self.run_cmd("create", "bad", "--stdin", stdin=json.dumps(plan), expect=1)
        self.assertIn(needle, msg)

    def test_unknown_requirement(self):
        self.bad(lambda p: p["units"][2]["lessons"][0].update(requires=["P2", "ZZ"]), "unknown lesson 'ZZ'")

    def test_cycle(self):
        def m(p):
            p["units"][0]["lessons"][0]["requires"] = ["P3"]
        self.bad(m, "cycle")

    def test_requires_needs_graph_flag(self):
        self.bad(lambda p: p.pop("graph"), "no \"graph\": true")

    def test_junction_warning(self):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"][2]["lessons"][0]["requires"] = ["P1", "P2"]  # same track twice
        Path(self.gfile).write_text(json.dumps(plan))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = learn.main(["--home", self.home, "create", "ai", "--file", self.gfile, "--update"])
        self.assertEqual(code, 0)
        self.assertIn("junction J1 should combine lessons from 2+ different tracks", err.getvalue())


class TestGraphScheduling(GraphBase):
    def test_tracks_are_parallel_roots(self):
        d = self.js("graph")
        by = {n["id"]: n["status"] for n in d["nodes"]}
        self.assertEqual((by["P1"], by["A1"], by["P2"], by["J1"]), ("available", "available", "locked", "locked"))

    def test_day_interleaves_tracks(self):
        self.assertEqual(sorted(self.today_ids()), ["A1", "P1"])  # one from each track, not P1+P2

    def test_after_done_switches_track_and_unlocks(self):
        self.run_cmd("done", "P1", "--score", "1")
        self.assertEqual(self.today_ids(), ["A1"])  # quota 1 left -> the other track
        self.run_cmd("done", "A1", "--score", "1")
        self.assertEqual(self.today_ids(), [])  # quota used
        nxt = self.today_ids(day="2026-10-06")
        self.assertEqual(sorted(nxt), ["A2", "P2"])

    def test_least_recently_studied_track_goes_first(self):
        self.run_cmd("done", "P1", "--score", "1")
        self.run_cmd("done", "A1", "--score", "1", day="2026-10-06")
        # PY was studied on 10-05, AI on 10-06, so PY comes first again
        order = [l["id"] for l in self.js("next", "--plan", "ai", "-n", "2", day="2026-10-07")]
        self.assertEqual(order, ["P2", "A2"])

    def test_junction_unlocks_only_with_both_skills_and_is_prioritised(self):
        for lid in ("P1", "P2"):
            self.run_cmd("done", lid, "--score", "1")
        self.run_cmd("done", "A1", "--score", "1", day="2026-10-06")
        status = {n["id"]: n["status"] for n in self.js("graph")["nodes"]}
        self.assertEqual(status["J1"], "locked")  # A2 still missing
        self.run_cmd("done", "A2", "--score", "1", day="2026-10-07")
        status = {n["id"]: n["status"] for n in self.js("graph")["nodes"]}
        self.assertEqual(status["J1"], "available")
        self.assertEqual(self.today_ids(day="2026-10-08")[0], "J1")  # junction first
        d = self.js("today", "--plan", "ai", day="2026-10-08")["plans"][0]["lessons"][0]
        self.assertEqual(d["requires"], ["P2", "A2"])

    def test_track_filter_and_unknown_track(self):
        lessons = self.js("today", "--plan", "ai", "--track", "AI")["plans"][0]["lessons"]
        self.assertEqual([l["id"] for l in lessons], ["A1", "A2"])  # whole quota from one branch when asked
        self.run_cmd("today", "--track", "NOPE", expect=1)

    def test_locked_lesson_needs_force(self):
        self.assertIn("locked: finish P1", self.run_cmd("done", "P2", "--score", "1", expect=1))
        self.run_cmd("done", "P2", "--score", "1", "--force")

    def test_retry_blocks_dependents_and_comes_back_first(self):
        self.run_cmd("done", "P1", "--score", "0.2")  # retry
        status = {n["id"]: n["status"] for n in self.js("graph")["nodes"]}
        self.assertEqual((status["P1"], status["P2"]), ("retry", "locked"))
        self.assertEqual(self.today_ids(day="2026-10-06")[0], "P1")

    def test_skip_unlocks_dependents(self):
        self.run_cmd("skip", "P1")
        status = {n["id"]: n["status"] for n in self.js("graph")["nodes"]}
        self.assertEqual(status["P2"], "available")

    def test_status_lists_tracks_and_nudge_names_them(self):
        self.assertIn("PY Python:", self.run_cmd("status", "--plan", "ai"))
        msg = self.run_cmd("nudge", "--language", "en")
        self.assertIn("(Python)", msg)
        self.assertIn("(AI basics)", msg)


class TestSkillExamples(Base):
    """The JSON examples in skills/study-plan/SKILL.md must stay valid."""

    def test_all_plan_examples_validate(self):
        import re
        text = (Path(learn.__file__).parent.parent / "skills/study-plan/SKILL.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        self.assertGreaterEqual(len(blocks), 2)  # linear + graph example
        for i, raw in enumerate(blocks):
            out = self.run_cmd("create", f"ex{i}", "--stdin", stdin=raw)
            self.assertIn("created plan", out)


class TestMigration(Base):
    def test_linear_to_graph_warns_about_parallel_units(self):
        two = json.loads(json.dumps(PLAN))
        two["units"].append({"id": "U2", "title": "Second", "lessons": [lesson("M1", "Next topic")]})
        Path(self.plan_file).write_text(json.dumps(two))
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")  # still linear: no warning
        two["graph"] = True
        Path(self.plan_file).write_text(json.dumps(two))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = learn.main(["--home", self.home, "create", "py", "--file", self.plan_file, "--update"])
        self.assertEqual(code, 0)
        self.assertIn("now a graph", err.getvalue())
        self.assertIn("Units U2", err.getvalue())
        # keeping U2 sequential silences it
        two["units"][1]["lessons"][0]["requires"] = ["L06"]
        Path(self.plan_file).write_text(json.dumps(two))
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")


class TestGraphViews(GraphBase):
    def setUp(self):
        super().setUp()
        self.run_cmd("done", "P1", "--score", "1")

    def test_text_map(self):
        out = self.run_cmd("graph")
        self.assertIn("PY · Python", out)
        self.assertIn("✅P1 → ▶P2", out)
        self.assertIn("⭐🔒 J1", out)
        self.assertIn("needs P2 (Lists) + A2 (Prompts)", out)
        self.assertIn("Ready now:", out)

    def test_md_and_units_level(self):
        md = self.run_cmd("graph", "--format", "md")
        self.assertIn("| Python |", md)
        self.assertIn("**⭐ Junctions**", md)
        units = self.js("graph", "--level", "units")
        self.assertIn(["PY", "PR"], units["edges"])
        self.assertIn(["AI", "PR"], units["edges"])
        self.assertIn("PY ──► PR", self.run_cmd("graph", "--level", "units"))

    def test_mermaid_and_plan_md(self):
        mm = self.run_cmd("graph", "--format", "mermaid")
        self.assertTrue(mm.startswith("graph TD"))
        self.assertIn("P1 --> P2", mm)
        self.assertIn('J1{{"J1 Chatbot script"}}:::locked', mm)
        self.assertIn("A2 --> J1", mm)
        md = (Path(self.home) / "plans/ai/PLAN.md").read_text()
        self.assertIn("```mermaid", md)
        self.assertIn("⭐ **J1** Chatbot script _junction_ 🔒 — needs P2, A2", md)

    def test_svg_is_valid_xml_and_contains_nodes(self):
        import xml.etree.ElementTree as ET
        out = os.path.join(self.tmp.name, "g.svg")
        self.run_cmd("graph", "--format", "svg", "-o", out)
        root = ET.parse(out).getroot()
        text = "".join(root.itertext())
        for needle in ("P1", "A2", "J1", "Python", "AI career"):
            self.assertIn(needle, text)

    def test_png_without_converter_explains(self):
        import shutil
        real = shutil.which
        shutil.which = lambda *_a, **_k: None
        try:
            msg = self.run_cmd("graph", "--format", "png", "-o", os.path.join(self.tmp.name, "g.png"), expect=1)
        finally:
            shutil.which = real
        self.assertIn("no working SVG", msg)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "g.svg")))

    def test_long_track_collapses(self):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"][0]["lessons"] = [lesson(f"P{i}", f"L{i}") for i in range(1, 21)]
        plan["units"][2]["lessons"][0]["requires"] = ["P5", "A2"]
        Path(self.gfile).write_text(json.dumps(plan))
        self.run_cmd("create", "ai", "--file", self.gfile, "--update")
        line = [l for l in self.run_cmd("graph").splitlines() if "P1" in l and "…" in l][0]
        self.assertIn("… +", line)
        self.assertLess(len(line), 80)


class TestStartDate(Base):
    def setUp(self):
        super().setUp()
        late = dict(PLAN, start_date="2026-10-12")  # next Monday
        Path(self.plan_file).write_text(json.dumps(late))
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")

    def test_today_before_start_says_when_it_starts(self):
        out = self.run_cmd("today", day="2026-10-08")
        self.assertIn("starts 2026-10-12", out)
        self.assertNotIn("rest day", out)
        self.assertEqual(self.js("today", day="2026-10-08")["plans"][0]["state"], "not_started")

    def test_learn_start_begins_today(self):
        self.run_cmd("start", day="2026-10-08")
        d = self.js("today", day="2026-10-08")["plans"][0]
        self.assertEqual((d["state"], d["quota"]), ("active", 2))

    def test_early_lesson_re_anchors_plan(self):
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-08")
        d = self.js("today", day="2026-10-09")["plans"][0]
        self.assertEqual(d["state"], "active")
        self.assertEqual(d["starts_on"], "2026-10-08")
        # one lesson done, 2/day expected for the Thursday that passed: behind by 1, not "ahead"
        self.assertEqual((d["behind"],), (1,))

    def test_validate_warns_about_events_before_start(self):
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-08")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            learn.main(["--home", self.home, "validate", "--plan", "py"])
        self.assertIn("before start_date", err.getvalue())
        self.assertIn("learn start --date 2026-10-08", err.getvalue())

    def test_nudge_works_for_early_study(self):
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-08")
        self.assertIn("Сегодня", self.run_cmd("nudge", day="2026-10-09"))


class TestSync(Base):
    def git(self, *a, cwd=None):
        subprocess.run(["git", "-C", cwd or self.home, *a], check=True, capture_output=True)

    def test_sync_roundtrip_between_devices(self):
        remote = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", remote)
        self.run_cmd("done", "L01", "--score", "1")
        self.run_cmd("sync")
        # second device
        other = os.path.join(self.tmp.name, "other")
        subprocess.run(["git", "clone", "-q", remote, other], check=True)
        out = subprocess.run([sys.executable, str(Path(learn.__file__)), "--home", other, "--json", "status"],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out)["plans"][0]["done"], 1)

    def test_refuses_to_sync_inside_code_repo(self):
        inside = str(Path(learn.CODE_REPO) / "learning")
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = learn.main(["--home", inside, "sync"])
        self.assertEqual(code, 1)
        self.assertIn("refusing to sync", err.getvalue())
        self.assertFalse(Path(inside).exists())

    def test_not_a_repo(self):
        self.assertIn("git init", self.run_cmd("sync", expect=1))


if __name__ == "__main__":
    unittest.main(verbosity=1)
