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

    def test_plurals(self):
        f = ("урок", "урока", "уроков")
        self.assertEqual([learn.ru_plural(n, f) for n in (1, 2, 5, 11, 21, 22)],
                         ["урок", "урока", "уроков", "уроков", "урок", "урока"])


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
