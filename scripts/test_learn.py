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

    def test_speed_is_average_lessons_per_day_over_the_needed_average(self):
        sp = lambda day: self.js("status", day=day)["plans"][0]["pace"]["speed"]  # noqa: E731
        self.assertIsNone(sp("2026-10-04"))  # not started yet
        self.assertIsNone(sp("2026-10-05"))  # first morning: no day to average over
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-05")
        s = sp("2026-10-05")  # today counts once a lesson is done today
        self.assertEqual((s["lessons_per_day"], s["needed_per_day"], s["basis"]), (1.0, 1.43, "pace"))  # 2 x 5/7
        self.assertEqual(s["speed"], 0.7)
        self.run_cmd("done", "L02", "--score", "1", day="2026-10-06")
        self.run_cmd("done", "L03", "--score", "1", day="2026-10-06")
        s = sp("2026-10-07")  # 3 lessons over Mon+Tue; Wednesday morning not counted yet
        self.assertEqual((s["days"], s["lessons_per_day"], s["speed"]), (2, 1.5, 1.05))
        self.assertIn("speed 1.05×", self.run_cmd("status", day="2026-10-07"))

    def test_speed_with_a_deadline_uses_the_deadline(self):
        plan = json.loads(json.dumps(PLAN))
        plan["deadline"] = "2026-10-16"  # 6 lessons over 12 days: 0.5 a day needed
        f = os.path.join(self.tmp.name, "p.json")
        Path(f).write_text(json.dumps(plan))
        self.run_cmd("create", "py", "--file", f, "--update")
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-05")
        s = self.js("status", day="2026-10-06")["plans"][0]["pace"]["speed"]
        self.assertEqual((s["needed_per_day"], s["basis"], s["lessons_per_day"], s["speed"]), (0.5, "deadline", 1.0, 2.0))

    def test_today_speed_handles_no_data_and_refreshes_after_a_lesson(self):
        for day in ("2026-10-04", "2026-10-05"):
            with self.subTest(day=day):
                self.assertIsNone(self.js("today", day=day)["plans"][0]["speed"])
                self.assertIn("speed — · not enough data yet", self.run_cmd("today"))
        self.run_cmd("done", "L01", "--score", "1")
        progress = Path(self.home) / "plans/py/progress.json"
        before = progress.read_bytes()
        for day in ("2026-10-05", "2026-10-06", "2026-10-10"):
            with self.subTest(day=day):
                today = self.js("today", day=day)["plans"][0]
                expected = self.js("status")["plans"][0]["pace"]["speed"]
                self.assertEqual(today["speed"], expected)
                self.assertIn(f"speed {expected['speed']:.2f}×", self.run_cmd("today"))
        self.assertIn("speed 0.70× · 1.00 lessons/day, needed 1.43",
                      self.run_cmd("today", day="2026-10-06"))
        self.assertEqual(progress.read_bytes(), before)  # previews do not write progress

    def test_today_reports_zero_speed_for_retries_and_skips(self):
        self.run_cmd("done", "L01", "--score", "0.2")
        self.run_cmd("skip", "L02")
        sp = self.js("today", day="2026-10-06")["plans"][0]["speed"]
        self.assertEqual((sp["speed"], sp["lessons_per_day"]), (0.0, 0.0))
        self.assertIn("speed 0.00× · 0.00 lessons/day, needed 1.43", self.run_cmd("today"))

    def test_today_keeps_speed_when_the_daily_quota_is_done(self):
        self.run_cmd("done", "L01", "--score", "1")
        self.run_cmd("done", "L02", "--score", "1")
        p = self.js("today")["plans"][0]
        self.assertEqual((p["quota"], p["lessons"], p["speed"]["speed"]), (0, [], 1.4))
        self.assertIn("speed 1.40× · 2.00 lessons/day, needed 1.43", self.run_cmd("today"))

    def test_today_speed_is_per_plan_and_uses_its_deadline(self):
        plan = dict(PLAN, deadline="2026-10-16")  # 6 lessons / 12 days = 0.5 needed per day
        self.run_cmd("create", "deadline", "--stdin", stdin=json.dumps(plan))
        self.run_cmd("done", "L01", "--plan", "py", "--score", "1")
        for lid in ("L01", "L02"):
            self.run_cmd("done", lid, "--plan", "deadline", "--score", "1")
        plans = {p["slug"]: p for p in self.js("today", day="2026-10-06")["plans"]}
        self.assertEqual(plans["py"]["speed"]["speed"], 0.7)
        sp = plans["deadline"]["speed"]
        self.assertEqual((sp["lessons_per_day"], sp["needed_per_day"], sp["speed"], sp["basis"]),
                         (2.0, 0.5, 4.0, "deadline"))
        self.assertIn("speed 4.00× · 2.00 lessons/day, needed 0.50 (by the deadline)",
                      self.run_cmd("today", "--plan", "deadline"))
        self.assertEqual(self.js("today", "--plan", "deadline")["plans"][0]["speed"], sp)

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
    def test_sm2_cycle_and_failure(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "What is a function?", "--a", "Reusable block")
        due = self.js("review", "due", day="2026-10-06")
        self.assertEqual(len(due), 1)
        self.run_cmd("review", "grade", "py/C001", "pass", day="2026-10-06")
        self.assertEqual(self.js("review", "due", "--all")[0]["due"], "2026-10-07")
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-07")
        self.assertEqual(self.js("review", "due", day="2026-10-12"), [])
        self.assertEqual(len(self.js("review", "due", day="2026-10-13")), 1)
        self.run_cmd("review", "grade", "C001", "fail", "--quality", "0", day="2026-10-13")
        card = self.js("review", "due", "--all")[0]
        self.assertEqual((card["lapses"], card["due"], card["srs"]["successes"]), (1, "2026-10-14", 0))
        self.assertEqual(card["srs"]["ease"], 2.5)

    def test_repeated_grade_is_not_another_recall(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("review", "grade", "C001", "fail", day="2026-10-06")
        before = (Path(self.home) / "plans/py/progress.json").read_text()
        self.assertIn("already graded today", self.run_cmd("review", "grade", "C001", "pass"))
        self.assertEqual((Path(self.home) / "plans/py/progress.json").read_text(), before)

    def test_early_and_inconsistent_grades_leave_state_unchanged(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        before = (Path(self.home) / "plans/py/progress.json").read_text()
        self.assertIn("not due", self.run_cmd("review", "grade", "C001", "pass", expect=1))
        self.assertIn("requires pass", self.run_cmd("review", "grade", "C001", "fail", "--quality", "5", expect=1))
        self.assertEqual((Path(self.home) / "plans/py/progress.json").read_text(), before)

    def test_quality_changes_future_intervals_and_is_logged(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", day="2026-10-06")
        self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", day="2026-10-07")
        self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", day="2026-10-13")
        card = self.js("review", "due", "--all")[0]
        self.assertEqual((card["srs"]["interval"], card["srs"]["ease"], card["due"]), (17, 2.8, "2026-10-30"))
        prog = json.loads((Path(self.home) / "plans/py/progress.json").read_text())
        self.assertEqual((prog["events"][-1]["quality"], prog["events"][-1]["scheduler"]), (5, "sm2"))

    def test_confidence_caps_fragile_passes_and_flags_confident_misses(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q2", "--a", "a2")
        out = self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", "--confidence", "30", day="2026-10-06")
        self.assertIn("q3", out)
        self.assertIn("fragile", out)
        out = self.run_cmd("review", "grade", "C002", "fail", "--confidence", "90", day="2026-10-06")
        self.assertIn("confident miss", out)
        prog = json.loads((Path(self.home) / "plans/py/progress.json").read_text())
        self.assertEqual([(e["quality"], e["confidence"]) for e in prog["events"] if e["type"] == "review"],
                         [(3, 30), (1, 90)])
        before = (Path(self.home) / "plans/py/progress.json").read_text()
        self.assertIn("0..100", self.run_cmd("review", "grade", "C001", "pass", "--confidence", "120",
                                             day="2026-10-07", expect=1))
        self.assertEqual((Path(self.home) / "plans/py/progress.json").read_text(), before)

    def test_confident_pass_keeps_its_quality(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.assertIn("q5", self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", "--confidence", "95",
                                         day="2026-10-06"))

    def test_overdue_review_uses_actual_answer_date(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-20")
        self.assertEqual(self.js("review", "due", "--all")[0]["due"], "2026-10-21")
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-26")
        self.assertEqual(self.js("review", "due", "--all")[0]["due"], "2026-11-01")

    def test_legacy_card_migrates_on_due_review_without_resetting_history(self):
        st = learn.Store(type("Args", (), {"home": self.home})())
        plan, prog = st.load("py")
        prog["cards"] = [{"id": "C001", "lesson": "L01", "q": "old", "a": "answer", "box": 3,
                          "due": "2026-10-12", "last": "2026-10-05", "reps": 3, "lapses": 1,
                          "created": "2026-09-20"}]
        prog["events"] = [{"date": day, "type": "review", "id": "py/C001", "result": result}
                          for day, result in [("2026-09-30", "fail"), ("2026-10-02", "pass"), ("2026-10-05", "pass")]]
        st.save("py", plan, prog)
        before = (Path(self.home) / "plans/py/progress.json").read_text()
        self.assertEqual(self.js("review", "due", "--all")[0]["due"], "2026-10-12")
        self.assertEqual((Path(self.home) / "plans/py/progress.json").read_text(), before)
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-12")
        card = self.js("review", "due", "--all")[0]
        self.assertEqual((card["reps"], card["lapses"], card["srs"]["successes"]), (4, 1, 3))
        self.assertEqual((card["srs"]["interval"], card["due"]), (18, "2026-10-30"))

    def test_batch_cards_and_today_lists_due(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin",
                     stdin=json.dumps([{"q": "q1", "a": "a1"}, {"q": "q2", "a": "a2"}]))
        d = self.js("today", day="2026-10-06")
        self.assertEqual(d["reviews_due_total"], 2)

    def test_bad_card_rejected(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin", stdin='[{"q":"only q"}]', expect=1)
        self.run_cmd("card", "add", "--lesson", "L99", "--q", "q", "--a", "a", expect=1)


class TestSm2Formula(unittest.TestCase):
    def test_reference_good_sequence_uses_ceil_and_previous_ease(self):
        state = {"ease": 2.5, "interval": 1, "successes": 0}
        intervals = []
        for _ in range(5):
            state = learn.schedule_review(state, 4)
            intervals.append(state["interval"])
        self.assertEqual(intervals, [1, 6, 15, 38, 95])
        self.assertEqual(state["ease"], 2.5)

    def test_hard_ease_floor_and_failure_reset(self):
        hard = learn.schedule_review({"ease": 2.5, "interval": 6, "successes": 2}, 3)
        self.assertEqual((hard["ease"], hard["interval"]), (2.36, 15))
        state = {"ease": 1.35, "interval": 10, "successes": 4}
        state = learn.schedule_review(state, 3)
        self.assertEqual((state["ease"], state["interval"]), (1.3, 14))
        for quality in range(3):
            failed = learn.schedule_review(state, quality)
            self.assertEqual((failed["ease"], failed["interval"], failed["successes"]), (1.3, 1, 0))
            self.assertEqual(learn.schedule_review(failed, 4)["interval"], 1)

    def test_interval_ceiling_and_legacy_box_fallback(self):
        self.assertEqual(learn.schedule_review({"ease": 3, "interval": 30000, "successes": 10}, 5)["interval"],
                         learn.MAX_REVIEW_INTERVAL)
        self.assertEqual(learn.review_state({"box": 4, "due": "2026-10-20"}),
                         {"algorithm": "sm2", "ease": 2.5, "interval": 14, "successes": 3})


class TestWarmup(Base):
    def setUp(self):
        super().setUp()
        self.run_cmd("config", "daily_reviews", "6")  # a fixed budget; auto is tested in TestReviewLoad

    def add_cards(self, lesson, count):
        self.run_cmd("card", "add", "--lesson", lesson, "--stdin",
                     stdin=json.dumps([{"q": f"{lesson}-{i}", "a": "a"} for i in range(count)]))

    def test_preview_is_read_only_and_topics_interleave(self):
        self.add_cards("L01", 4)
        self.add_cards("L02", 2)
        self.add_cards("L03", 2)
        path = Path(self.home) / "plans/py/progress.json"
        before = path.read_text()
        preview = self.js("today", day="2026-10-06")
        self.assertEqual([c["lesson"] for c in preview["reviews"]], ["L01", "L02", "L03", "L01", "L02", "L03"])
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.js("today")["reviews"], preview["reviews"])
        self.assertEqual(self.js("today", "--max-reviews", "0")["reviews"], [])
        self.assertEqual(path.read_text(), before)

    def test_issued_questions_survive_restart_and_do_not_refill_after_lesson(self):
        self.add_cards("L01", 8)
        issued = [self.js("review", "next", day="2026-10-06")["card"]["id"] for _ in range(6)]
        self.assertEqual(len(set(issued)), 6)
        self.assertIsNone(self.js("review", "next")["card"])
        self.run_cmd("done", "L01", "--score", "1")
        today = self.js("today")
        self.assertEqual((today["reviews"], today["reviews_today"], today["reviews_due_total"]), ([], 6, 8))
        cards = self.js("review", "due")
        self.assertTrue(all(c["reps"] == 0 and c["due"] == "2026-10-06" for c in cards))
        next_day = self.js("review", "next", day="2026-10-07")["card"]
        self.assertEqual(next_day["id"], "C007")  # unasked backlog, not yesterday's first question

    def test_budget_counts_legacy_grades_and_is_shared_across_plan_filters(self):
        self.add_cards("L01", 3)
        self.run_cmd("create", "other", "--file", self.plan_file)
        self.run_cmd("card", "add", "--plan", "other", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("review", "grade", "py/C001", "pass", day="2026-10-06")
        self.assertEqual(self.js("review", "next", "--plan", "other", "--max-reviews", "2")["card"]["plan"], "other")
        self.assertIsNone(self.js("review", "next", "--plan", "py", "--max-reviews", "2")["card"])
        self.assertEqual(self.js("review", "next", "--plan", "py", "--max-reviews", "3")["card"]["id"], "C002")

    def test_future_retired_and_presented_cards_are_not_issued(self):
        self.add_cards("L01", 2)
        self.assertIsNone(self.js("review", "next")["card"])
        self.run_cmd("card", "retire", "C001")
        card = self.js("review", "next", day="2026-10-06")["card"]
        self.assertEqual(card["id"], "C002")
        self.assertIsNone(self.js("review", "next")["card"])
        self.run_cmd("review", "grade", "C002", "pass")  # a pending answer is still gradable
        self.assertEqual(self.js("review", "due", "--all")[0]["reps"], 1)
        self.assertIn("retired", self.run_cmd("review", "grade", "C001", "pass", expect=1))
        self.assertIn("between 0 and 50", self.run_cmd("review", "next", "--max-reviews", "-1", expect=1))

    def test_presentation_is_not_activity_and_nudge_respects_budget(self):
        self.add_cards("L01", 8)
        for _ in range(6):
            self.js("review", "next", day="2026-10-10")  # rest day
        self.assertEqual(self.js("week")["plans"][0]["reviews"], 0)
        self.assertEqual(self.js("today")["streak"], 0)
        self.assertEqual(self.run_cmd("nudge"), "")


class TestReviewLoad(Base):
    def add_cards(self, lesson, count):
        self.run_cmd("card", "add", "--lesson", lesson, "--stdin",
                     stdin=json.dumps([{"q": f"{lesson}-{i}", "a": "a"} for i in range(count)]))

    def test_auto_budget_follows_the_days_load_and_stays_put_while_grading(self):
        self.add_cards("L01", 9)
        self.assertEqual(self.run_cmd("config", "daily_reviews").strip(), "auto")
        today = self.js("today", day="2026-10-06")
        self.assertEqual((today["reviews_budget"], today["reviews_load"], len(today["reviews"])), (9, 9, 9))
        for _ in range(4):
            card = self.js("review", "next")["card"]
            self.run_cmd("review", "grade", f"py/{card['id']}", "pass")
        today = self.js("today")
        self.assertEqual((today["reviews_budget"], today["reviews_today"], len(today["reviews"])), (9, 4, 5))
        self.assertNotIn("review backlog", self.run_cmd("today"))

    def test_auto_budget_has_a_floor_and_a_ceiling_and_warns_about_backlog(self):
        self.add_cards("L01", 2)
        self.assertEqual(self.js("today", day="2026-10-06")["reviews_budget"], 6)
        self.add_cards("L02", 40)
        today = self.js("today", day="2026-10-07")
        self.assertEqual((today["reviews_budget"], today["reviews_load"], len(today["reviews"])), (30, 42, 30))
        self.assertIn("review backlog: 42 due today, daily budget 30", self.run_cmd("today"))
        self.assertIn("review backlog", self.run_cmd("status"))
        self.assertEqual(self.js("status")["reviews"], {"budget": 30, "load": 42})
        self.assertNotIn("review backlog", self.run_cmd("today", "--max-reviews", "0"))

    def test_config_budget_overrides_auto_and_is_validated(self):
        self.add_cards("L01", 9)
        self.run_cmd("config", "daily_reviews", "4")
        self.assertEqual(self.js("today", day="2026-10-06")["reviews_budget"], 4)
        self.assertEqual(json.loads((Path(self.home) / "config.json").read_text())["daily_reviews"], 4)
        self.assertEqual(self.js("today", "--max-reviews", "7")["reviews_budget"], 7)
        for bad in ("51", "-1", "lots"):
            self.assertIn("between 0 and 50", self.run_cmd("config", "daily_reviews", bad, expect=1))
        self.run_cmd("config", "daily_reviews", "auto")
        self.assertEqual(self.js("today")["reviews_budget"], 9)

    def test_nudge_counts_the_auto_budget(self):
        self.add_cards("L01", 8)
        self.assertIn("8 повторений", self.run_cmd("nudge", day="2026-10-10"))  # rest day: reviews only

    def test_confident_overdue_recall_earns_a_longer_interval(self):
        self.assertEqual(learn.schedule_review({"ease": 2.5, "interval": 6, "successes": 2}, 4, delay=10)["interval"], 28)
        self.assertEqual(learn.schedule_review({"ease": 2.5, "interval": 6, "successes": 2}, 5, delay=10)["interval"], 40)
        self.assertEqual(learn.schedule_review({"ease": 2.5, "interval": 6, "successes": 2}, 3, delay=10)["interval"], 15)
        self.assertEqual(learn.schedule_review({"ease": 2.5, "interval": 1, "successes": 1}, 5, delay=10)["interval"], 6)
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-06")
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-07")  # next due 10-13
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-23")  # 10 days late, q4
        card = self.js("review", "due", "--all")[0]
        self.assertEqual((card["srs"]["interval"], card["due"]), (28, "2026-11-20"))
        prog = json.loads((Path(self.home) / "plans/py/progress.json").read_text())
        self.assertEqual(prog["events"][-1]["delay"], 10)


class TestLessonNotes(Base):
    def test_retry_shows_last_score_and_note(self):
        self.run_cmd("done", "L01", "--score", "0.3", "--note", "confuses return with print")
        today = self.run_cmd("today", day="2026-10-06")
        self.assertIn("last time: last score 30% (2026-10-05) · note (2026-10-05): confuses return with print", today)
        lesson = self.js("today")["plans"][0]["lessons"][0]
        self.assertEqual((lesson["id"], lesson["score"], lesson["note"]), ("L01", 0.3, "confuses return with print"))

    def test_note_checkpoints_an_unfinished_lesson_without_completing_it(self):
        self.assertIn("noted py/L01 (pending)", self.run_cmd("note", "L01", "--text", "stopped after step 3"))
        self.assertEqual(self.js("status")["plans"][0]["done"], 0)
        self.assertEqual(self.js("today")["plans"][0]["quota"], 2)  # not a lesson event
        self.assertEqual(self.js("today")["streak"], 0)  # not activity either
        self.assertIn("note (2026-10-05): stopped after step 3", self.run_cmd("today"))
        self.run_cmd("note", "L01", "--text", "   ", expect=1)
        self.run_cmd("note", "L99", "--text", "x", expect=1)

    def test_repeated_failures_escalate(self):
        self.run_cmd("done", "L01", "--score", "0.2")
        self.assertNotIn("failed 2 times", self.run_cmd("today", day="2026-10-06"))
        self.run_cmd("done", "L01", "--score", "0.4")
        self.assertIn("failed 2 times — don't re-teach it the same way", self.run_cmd("today", day="2026-10-07"))


class TestLeechesAndEdits(Base):
    def make_leech(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a")
        for day in ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
            out = self.run_cmd("review", "grade", "C001", "fail", day=day)
        return out

    def test_leech_is_flagged_after_repeated_lapses(self):
        self.assertIn("⚠ leech: forgotten 4 times", self.make_leech())
        os.environ["LEARN_TODAY"] = "2026-10-12"
        self.assertIn("⚠ leech (4 lapses)", self.run_cmd("today"))
        card = self.js("review", "next")["card"]
        self.assertTrue(card["leech"])
        self.assertIn("1 leech(es)", self.run_cmd("status"))
        self.assertEqual(self.js("week")["plans"][0]["leeches"], 1)

    def test_edit_rewrites_a_card_and_keeps_its_schedule(self):
        self.make_leech()
        before = self.js("review", "due", "--all")[0]
        self.assertIn("schedule unchanged", self.run_cmd("card", "edit", "py/C001", "--q", "Why X?", "--a", "Because Y"))
        after = self.js("review", "due", "--all")[0]
        self.assertEqual((after["q"], after["a"], after["edited"]), ("Why X?", "Because Y", "2026-10-09"))
        self.assertEqual({k: after[k] for k in ("due", "srs", "lapses", "reps")},
                         {k: before[k] for k in ("due", "srs", "lapses", "reps")})
        self.run_cmd("card", "edit", "C001", expect=1)
        self.run_cmd("card", "edit", "C001", "--q", " ", expect=1)
        self.run_cmd("card", "edit", "C009", "--q", "x", expect=1)


class TestWeekRetention(Base):
    def test_week_reports_long_term_recall_quality_and_new_cards(self):
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin", stdin=json.dumps([{"q": "q1", "a": "a"}, {"q": "q2", "a": "a"}]))
        for day in ("2026-10-06", "2026-10-07"):
            self.run_cmd("review", "grade", "C001", "pass", day=day)
        week = self.js("week")["plans"][0]
        self.assertEqual((week["mature_reviews"], week["new_cards"], week["avg_quality"]), (0, 2, 4.0))
        self.assertIn("no mature cards reviewed yet", self.run_cmd("week"))
        self.run_cmd("review", "grade", "C001", "pass", day="2026-10-13")
        self.run_cmd("review", "grade", "C001", "pass", "--quality", "5", day="2026-11-03")  # 21-day gap
        week = self.js("week")["plans"][0]
        self.assertEqual((week["mature_reviews"], week["mature_passed"], week["avg_quality"], week["new_cards"]),
                         (1, 1, 5.0, 0))
        self.assertIn("long-term recall (≥ 21d gap): 1/1 (100%)", self.run_cmd("week"))


class TestRollingWave(Base):
    def test_status_asks_to_detail_the_next_stage_near_the_end(self):
        self.assertNotIn("time to detail the next stage", self.run_cmd("status"))
        Path(self.plan_file).write_text(json.dumps(dict(PLAN, later=["Stage 2: files"])))
        self.run_cmd("create", "py", "--file", self.plan_file, "--update")
        # 6 lessons left, two weeks at 2/day x 5 days = 20: already within the window
        self.assertIn("only 6 detailed lesson(s) left and `later` has 1 stage(s)", self.run_cmd("status"))
        self.assertTrue(self.js("status")["plans"][0]["extend_soon"])


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

    def test_only_one_junction_is_prioritised_per_day(self):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["pace"]["lessons_per_day"] = 3
        plan["units"][2]["lessons"].append(lesson("J2", "Second project", kind="junction", requires=["J1", "A2"]))
        Path(self.gfile).write_text(json.dumps(plan))
        self.run_cmd("create", "ai", "--file", self.gfile, "--update")
        for lid in ("P1", "P2", "A1", "A2"):
            self.run_cmd("done", lid, "--score", "1", day="2026-10-06")
        day = "2026-10-07"
        self.assertEqual(self.today_ids(day=day)[:1], ["J1"])  # first junction jumps the queue
        self.run_cmd("done", "J1", "--score", "1", day=day)
        nxt = self.today_ids(day=day)
        self.assertEqual(nxt[0], "P3")  # J2 is unlocked but waits: one project a day is enough
        self.assertIn("J2", nxt)  # still offered once other tracks had their turn

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

    def test_svg_lanes_today_ring_and_language(self):
        import xml.etree.ElementTree as ET
        out = os.path.join(self.tmp.name, "g.svg")
        self.run_cmd("done", "P1", "--score", "1", day="2026-10-05")
        self.run_cmd("config", "language", "en")
        self.run_cmd("graph", "--format", "svg", "-o", out, day="2026-10-06")
        root = ET.parse(out).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        text = " ".join(t.text or "" for t in root.iter(ns + "text"))
        self.assertIn("1/6 lessons done", text)
        self.assertIn("Today: ", text)  # the day's picks are listed and ringed
        self.assertTrue(any(c.get("stroke") == "#f9a825" for c in root.iter(ns + "circle")))
        for title in ("Python", "AI basics", "Projects"):  # one lane per track, with its own progress
            self.assertIn(title, text)
        self.assertIn("1/3", text)
        tips = [t.text for t in root.iter(ns + "title")]
        self.assertIn("P1 — Functions (done)", tips)
        self.assertIn("J1 — Chatbot script (locked)", tips)
        self.assertEqual(root.get("width"), root.get("viewBox").split()[2])  # svg: natural size
        self.assertIn("Speed 0.70×", text)  # 1 lesson in 1 day / (2 x 5/7) needed
        self.run_cmd("config", "language", "ru")
        self.run_cmd("graph", "--format", "svg", "-o", out, day="2026-10-06")
        self.assertIn("Скорость 0,70×", Path(out).read_text())
        self.assertIn("уроков пройдено", Path(out).read_text())
        self.assertIn("Сегодня: ", Path(out).read_text())

    def test_svg_every_branch_flows_into_the_goal(self):
        import xml.etree.ElementTree as ET
        import learn_graph as G
        ns = "{http://www.w3.org/2000/svg}"
        plan = json.loads(json.dumps(GRAPH_PLAN))
        model = G.build_model(plan, {"lessons": {}, "events": []})
        root = ET.fromstring(G.render_svg("t", plan, {"lessons": {}, "events": []}, model, lang="ru"))
        tips = [t.text for t in root.iter(ns + "title")]
        self.assertIn("Цель: Build an AI script", tips)
        self.assertIn("0%", [t.text for t in root.iter(ns + "text")])
        # sinks are P3 and J1 (P1, P2, A1, A2 all lead somewhere): one path each, ending at the goal's top
        gx = float(root.get("viewBox").split()[2]) / 2
        ends = [p.get("d").split()[-1] for p in root.iter(ns + "path") if p.get("fill") == "none"]
        self.assertEqual(sum(1 for e in ends if float(e.split(",")[0]) == round(gx, 1)), 2)

    def test_svg_lanes_never_share_a_cell_and_keep_prerequisites_above(self):
        import learn_graph as G
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"][2]["lessons"].append(lesson("J2", "Second project", kind="junction", requires=["P2", "A2"]))
        prog = {"lessons": {}, "events": []}
        model = G.build_model(plan, prog)
        cols, col, row, _ = G._layout(model)
        self.assertEqual(cols[1], "PR")  # the junction lane sits between the tracks it joins
        cells = [(col[n], row[n]) for n in col]
        self.assertEqual(len(cells), len(set(cells)))
        for a, b in model["edges"]:
            self.assertLess(row[a], row[b])

    def test_png_scale_is_checked(self):
        self.assertIn("--scale", self.run_cmd("graph", "--format", "png", "--scale", "9", expect=1))

    @unittest.skipUnless(__import__("shutil").which("rsvg-convert"), "needs rsvg-convert")
    def test_png_is_rendered_at_the_requested_scale(self):
        widths = []
        for scale in ("1", "3"):
            out = os.path.join(self.tmp.name, f"g{scale}.png")
            self.run_cmd("graph", "--format", "png", "--scale", scale, "-o", out)
            widths.append(int.from_bytes(Path(out).read_bytes()[16:20], "big"))
        self.assertAlmostEqual(widths[1], 3 * widths[0], delta=3)

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

    def run_png(self, *argv, converter=None, exe="fake"):
        """`learn graph --format png` with a fake converter: (stdout, stderr, svg root, converter calls)."""
        import xml.etree.ElementTree as ET
        import learn_graph as G
        calls, real = [], G.svg_to_png

        def fake(svg, png):
            calls.append(svg)
            Path(png).write_bytes(converter(svg) if converter else b"\x89PNG fake")
            return exe
        G.svg_to_png = fake
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = learn.main(["--home", self.home, "graph", "--format", "png", *argv])
        finally:
            G.svg_to_png = real
        self.assertEqual(code, 0, err.getvalue())
        svg = Path(out.getvalue().strip()).with_suffix(".svg")
        return out.getvalue(), err.getvalue(), ET.parse(svg).getroot(), calls

    def big_plan(self, tracks=21, per=12):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"] = [{"id": f"T{u}", "title": f"Track number {u}", "lessons": [
            lesson(f"T{u}L{i}", f"Topic {i} of track {u}", **({"requires": [f"T{u - 1}L3"]} if u and not i else {}))
            for i in range(per)]} for u in range(tracks)]
        Path(self.gfile).write_text(json.dumps(plan))
        self.run_cmd("create", "ai", "--file", self.gfile, "--update")

    def assert_phone_sized(self, root):
        ns = "{http://www.w3.org/2000/svg}"
        px = float(root.get("width")) / float(root.get("viewBox").split()[2])
        self.assertLessEqual(float(root.get("width")), 1400)
        smallest = min(float(t.get("font-size")) for t in root.iter(ns + "text"))
        self.assertGreaterEqual(smallest * px, 14)

    def test_png_of_a_wide_plan_keeps_every_lesson_at_phone_size(self):
        self.big_plan()
        _, err, root, calls = self.run_png()
        self.assertIn("png: fake", err)  # the converter is named
        self.assert_phone_sized(root)
        tips = [t.text for t in root.iter("{http://www.w3.org/2000/svg}title")]
        for u in range(21):
            for i in range(12):
                self.assertTrue(any(t.startswith(f"T{u}L{i} — ") for t in tips), (u, i))
        self.assertNotIn("one point per track", err)
        _, _, full, _ = self.run_png("--full")  # same graph at its natural density
        self.assertEqual(root.get("viewBox"), full.get("viewBox"))
        self.assertEqual(full.get("width"), full.get("viewBox").split()[2])
        self.assertTrue(any(t.text.startswith("T5L1 ") for t in full.iter("{http://www.w3.org/2000/svg}title")))

    def test_compact_map_packs_parallel_roots_and_keeps_every_prerequisite_above(self):
        import learn_graph as G
        # Anonymous stress case: many simultaneous roots, diamonds, cross-track dependencies and long ids.
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"] = [{"id": f"T{u}", "title": f"Track {u}", "lessons": [
            lesson(f"T{u}L{i}", f"Topic {i}", **({"kind": "junction", "requires": [f"T{u}L{i - 1}", f"T{u - 1}L{i - 1}"]}
                   if u and i else {})) for i in range(5)]} for u in range(21)]
        model = G.build_model(plan, {"lessons": {}, "events": []})
        snapshot = json.dumps(model)
        cols, col, row, _ = G._compact_layout(model)
        self.assertLessEqual(len(cols), 8)
        self.assertEqual(len({(col[n], row[n]) for n in col}), 105)
        for a, b in model["edges"]:
            self.assertLess(row[a], row[b], (a, b))
        self.assertEqual(json.dumps(model), snapshot)
        self.assertEqual(G._compact_layout(model), G._compact_layout(model))

    def test_compact_map_keeps_progress_speed_today_and_explicit_units_view(self):
        self.big_plan(tracks=6)
        self.run_cmd("done", "T0L0", "--score", "1", day="2026-10-05")
        os.environ["LEARN_TODAY"] = "2026-10-06"
        _, _, root, _ = self.run_png()
        ns = "{http://www.w3.org/2000/svg}"
        texts = " ".join(t.text or "" for t in root.iter(ns + "text"))
        self.assertIn("1/72", texts)
        self.assertIn("1%", texts)
        self.assertIn("Скорость", texts)
        self.assertIn("Сегодня:", texts)
        self.assertTrue(any(c.get("stroke") == "#f9a825" for c in root.iter(ns + "circle")))
        _, _, units, _ = self.run_png("--level", "units")
        tips = [t.text for t in units.iter(ns + "title")]
        self.assertIn("T0 — Track number 0 (готово)", tips)
        self.assertFalse(any(t.startswith("T0L0 — ") for t in tips))

    def test_flowing_map_compacts_each_level_and_places_a_join_between_its_parents(self):
        import learn_graph as G
        plan = {"units": [{"id": f"T{u}", "title": f"Track {u}", "lessons": [
            lesson(f"T{u}L{i}", "Example topic") for i in range(length)]}
            for u, length in enumerate([8, 2, 6, 3, 1, 4])], "graph": True}
        plan["units"].append({"id": "J", "title": "Join", "lessons": [
            lesson("J0", "Combine skills", kind="junction", requires=["T0L7", "T2L5"])]})
        model = G.build_model(plan, {"lessons": {}, "events": []})
        _, x, row, _ = G._compact_layout(model)
        for r in set(row.values()):
            xs = sorted(x[n] for n in x if row[n] == r)
            # An ended branch leaves no empty slot between the remaining lessons.
            for a, b in zip(xs, xs[1:]):
                self.assertAlmostEqual(b - a, 1)
        trunk = [x[f"T0L{i}"] for i in range(8)]
        self.assertGreater(max(trunk) - min(trunk), 0.5)
        self.assertAlmostEqual(x["J0"], (x["T0L7"] + x["T2L5"]) / 2)
        # Parallel chains retain their left-to-right order while they bend.
        for i in range(6):
            self.assertLess(x[f"T0L{i}"], x[f"T2L{i}"])

    def test_flowing_edges_are_curved_clear_other_nodes_and_do_not_extend_early_leaves(self):
        import re
        import xml.etree.ElementTree as ET
        import learn_graph as G
        plan = {"units": [{"id": f"T{u}", "title": f"Track {u}", "lessons": [
            lesson(f"T{u}L{i}", "Example topic") for i in range(length)]}
            for u, length in enumerate([8, 2, 6, 3, 1, 4])], "graph": True}
        plan["units"].append({"id": "J", "title": "Join", "lessons": [
            lesson("J0", "Combine skills", kind="junction", requires=["T0L0", "T2L5"])]})
        plan["units"][5]["lessons"][1]["requires"] = ["T0L0"]  # a wide move past other root captions
        model = G.build_model(plan, {"lessons": {}, "events": []})
        root = ET.fromstring(G.render_svg("Example", plan, {}, model))
        ns = "{http://www.w3.org/2000/svg}"
        shapes, captions = {}, {}
        for group in root.iter(ns + "g"):
            title = group.findtext(ns + "title") or ""
            if " — " not in title:
                continue
            nid = title.split(" — ")[0]
            shape = next(s for s in group if s.get("stroke-width") == "3")
            if shape.tag == ns + "circle":
                shapes[nid] = (float(shape.get("cx")), float(shape.get("cy")), float(shape.get("r")))
            else:
                points = [tuple(map(float, p.split(","))) for p in shape.get("points").split()]
                x, y = sum(p[0] for p in points) / 4, sum(p[1] for p in points) / 4
                shapes[nid] = (x, y, max(p[0] for p in points) - x)
            label = list(group.iter(ns + "text"))[-1]
            w = G._text_w(label.text or "", float(label.get("font-size")), label.get("font-weight") == "bold")
            captions[nid] = (float(label.get("x")) - w / 2, float(label.get("y")) - 9, w, 12)
        paths = [p for p in root.iter(ns + "path") if p.get("marker-end")]
        _, _, row, _ = G._compact_layout(model)
        self.assertEqual(len(paths), len(model["edges"]) + sum(r == max(row.values()) for r in row.values()))
        for path, (a, b) in zip(paths, model["edges"]):
            d = path.get("d")
            self.assertIn("C", d)
            self.assertNotIn("L", d)
            points = [tuple(map(float, p.split(","))) for p in re.findall(r"-?\d+\.\d+,-?\d+\.\d+", d)]
            for k in range(1, len(points), 3):
                p0, p1, p2, p3 = points[k - 1:k + 3]
                self.assertTrue(p0[1] <= p1[1] <= p2[1] <= p3[1])
                for step in range(21):
                    t = step / 20
                    x, y = [sum(w * p[axis] for w, p in zip(
                        ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3), (p0, p1, p2, p3))) for axis in (0, 1)]
                    for n, (cx, cy, r) in shapes.items():
                        if n not in (a, b):
                            self.assertGreater((x - cx)**2 + (y - cy)**2, r*r, (a, b, n))
                    for n, (lx, ly, w, h) in captions.items():
                        if n not in (a, b):
                            self.assertFalse(lx - 1 < x < lx + w + 1 and ly - 1 < y < ly + h + 1, (a, b, n))

    def test_node_fill_is_status_outline_is_track_and_convergence_is_a_diamond(self):
        import xml.etree.ElementTree as ET
        import learn_graph as G
        statuses = ["done", "available", "retry", "locked", "skipped"]
        model = {"level": "lessons", "tracks": [
            {"id": f"T{i}", "title": f"Track {i}", "done": 2 if st == "done" else 0, "total": 2}
            for i, st in enumerate(statuses)], "nodes": [
            {"id": f"{shape}{i}", "short": f"{shape}{i}", "label": "Topic", "unit": f"T{i}",
             "status": st, "kind": "lesson"} for i, st in enumerate(statuses) for shape in "CJ"],
            "edges": [(source, f"J{i}") for i in range(5) for source in ("C0", "C1")]}
        root = ET.fromstring(G.render_svg("Example", {"goal": "Practice"}, {}, model))
        ns = "{http://www.w3.org/2000/svg}"
        for i, st in enumerate(statuses):
            for prefix, tag in (("C", "circle"), ("J", "polygon")):
                group = next(g for g in root.iter(ns + "g")
                             if (g.findtext(ns + "title") or "").startswith(f"{prefix}{i} — "))
                shape = next(s for s in group if s.tag == ns + tag and s.get("stroke-width") == "3")
                self.assertEqual(shape.get("fill"), G.STATUS_FILL[st])
                self.assertEqual(shape.get("stroke"), G.FAINT if st == "skipped" else G.TRACK_COLORS[i])
        # Every compact label and the track key fit inside the canvas, including long lesson ids.
        width = float(root.get("viewBox").split()[2])
        for t in root.iter(ns + "text"):
            w = G._text_w(t.text or "", float(t.get("font-size")), t.get("font-weight") == "bold")
            x = float(t.get("x")) - {"start": 0, "middle": w / 2, "end": w}[t.get("text-anchor")]
            self.assertTrue(-1 <= x and x + w <= width + 1, t.text)

    def test_png_of_one_track_shows_its_lessons_readably(self):
        self.big_plan()
        _, err, root, _ = self.run_png("--track", "T3")
        self.assertNotIn("too wide", err)
        self.assert_phone_sized(root)
        tips = [t.text for t in root.iter("{http://www.w3.org/2000/svg}title")]
        self.assertTrue(any(t.startswith("T3L11 ") for t in tips))
        self.assertFalse(any(t.startswith("T4L") for t in tips))
        self.assertIn("Track number 3", self.run_cmd("graph", "--track", "T3"))
        self.assertIn("no such track: ZZ", self.run_cmd("graph", "--track", "ZZ", expect=1))

    def test_small_plan_png_keeps_its_lanes_at_phone_size(self):
        _, err, root, _ = self.run_png()
        self.assertNotIn("too wide", err)
        self.assert_phone_sized(root)
        self.assertIn("Python", " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text")))

    def test_png_is_cached_until_the_map_changes(self):
        self.run_png()
        _, err, _, calls = self.run_png()
        self.assertEqual(calls, [])
        self.assertIn("cached", err)
        self.run_cmd("done", "P2", "--score", "1")
        _, err, _, calls = self.run_png()
        self.assertEqual(len(calls), 1)

    def test_heavy_png_is_rendered_smaller(self):
        def heavy(svg):  # pretend anything wider than 1100 px is over the 1 MB budget
            width = float(Path(svg).read_text().split('width="', 1)[1].split('"', 1)[0])
            return b"x" * (2_000_000 if width > 1100 else 1000)
        _, err, root, calls = self.run_png(converter=heavy)
        self.assertEqual(len(calls), 2)
        self.assertLessEqual(float(root.get("width")), 1100)

    def test_heavy_wide_png_stops_shrinking_while_labels_are_readable(self):
        self.big_plan()
        _, _, root, calls = self.run_png(converter=lambda _svg: b"x" * 2_000_000)
        self.assertLessEqual(len(calls), 3)
        self.assert_phone_sized(root)
        tips = [t.text for t in root.iter("{http://www.w3.org/2000/svg}title")]
        self.assertTrue(any(t.startswith("T20L11 — ") for t in tips))

    def test_svg_to_png_takes_the_first_installed_converter(self):
        import shutil
        import learn_graph as G
        real_which, real_run, used = shutil.which, G.subprocess.run, []

        def run(cmd, **_):
            used.append(cmd[0])
            Path(cmd[-1] if cmd[0] != "rsvg-convert" else cmd[2]).write_bytes(b"png")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        png = os.path.join(self.tmp.name, "g.png")
        try:
            G.subprocess.run = run
            shutil.which = lambda exe: exe if exe in ("magick", "convert") else None
            self.assertEqual(G.svg_to_png("g.svg", png), "magick")
            shutil.which = lambda exe: exe if exe in ("rsvg-convert", "convert") else None
            self.assertEqual(G.svg_to_png("g.svg", png), "rsvg-convert")
        finally:
            shutil.which, G.subprocess.run = real_which, real_run
        self.assertEqual(used, ["magick", "rsvg-convert"])
        self.assertEqual(Path(png).read_bytes(), b"png")

    def test_png_from_a_slower_converter_suggests_rsvg(self):
        import learn_graph as G
        _, err, _, _ = self.run_png(exe="convert")
        self.assertIn("png: convert", err)
        self.assertIn("librsvg2-bin", err)
        Path(self.home, "plans", "ai", "graph.svg").unlink()  # not the cache: render again
        _, err, _, _ = self.run_png(exe=G.BEST_PNG)
        self.assertIn("png: rsvg-convert", err)
        self.assertNotIn("librsvg2-bin", err)

    def test_a_circle_of_tracks_is_broken_where_fewest_arrows_point_back(self):
        import learn_graph as G
        # A needs B and D, B needs A, D needs B: starting at B costs one arrow back (A→B), at A two
        model = {"level": "units", "tracks": [], "nodes": [{"id": i, "unit": i, "kind": "lesson"} for i in "ABD"],
                 "edges": [("B", "A"), ("D", "A"), ("A", "B"), ("B", "D")]}
        _, _, row, _ = G._layout(model)
        self.assertEqual([(a, b) for a, b in model["edges"] if row[b] <= row[a]], [("A", "B")])

    def test_units_map_spreads_points_over_the_columns(self):
        import math
        import learn_graph as G
        # 21 tracks, mostly chained one or two per level: wrapping each level from column 0 piled them up on the left
        ids = [f"T{i}" for i in range(21)]
        edges = [(ids[i - 1], ids[i]) for i in range(1, 15)] + [(ids[i - 7], ids[i]) for i in range(15, 21)]
        model = {"level": "units", "tracks": [], "nodes": [{"id": i, "unit": i, "kind": "lesson"} for i in ids],
                 "edges": edges}
        cols, col, row, _ = G._layout(model)
        loads = [sum(1 for n in col if col[n] == c) for c in cols]
        self.assertLessEqual(max(loads), math.ceil(len(ids) / len(cols)))
        self.assertEqual(len({(col[n], row[n]) for n in col}), len(ids))
        for a, b in edges:
            self.assertLess(row[a], row[b])

    def test_tracks_needing_each_other_in_a_circle_warn_and_still_draw(self):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"][0]["lessons"].append(lesson("P4", "Bot polish", kind="junction", requires=["J1", "P3"]))
        Path(self.gfile).write_text(json.dumps(plan))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(learn.main(["--home", self.home, "create", "ai", "--file", self.gfile, "--update"]), 0)
        self.assertIn("tracks need each other in a circle (PY → PR → PY", err.getvalue())
        _, _, root, _ = self.run_png("--level", "units")
        ns = "{http://www.w3.org/2000/svg}"
        self.assertTrue(any(p.get("stroke-dasharray") == "5 3" for p in root.iter(ns + "path")))  # the arrow back up
        gx = float(root.get("viewBox").split()[2]) / 2
        ends = [p.get("d").split()[-1] for p in root.iter(ns + "path") if p.get("fill") == "none"]
        self.assertTrue(any(float(e.split(",")[0]) == round(gx, 1) for e in ends))  # something still reaches the goal

    def test_long_chains_need_no_recursion(self):
        import learn_graph as G
        n = 3000
        reqs = {f"L{i}": [f"L{i - 1}"] if i else [] for i in range(n)}
        self.assertIsNone(G.find_cycle(reqs))
        reqs["L0"] = [f"L{n - 1}"]
        self.assertEqual(len(G.find_cycle(reqs)), n + 1)
        model = {"level": "lessons", "tracks": [{"id": "U", "title": "U", "done": 0, "total": n, "ready": []}],
                 "nodes": [{"id": f"L{i}", "unit": "U", "kind": "lesson"} for i in range(n)],
                 "edges": [(f"L{i - 1}", f"L{i}") for i in range(1, n)]}
        _, _, row, _ = G._layout(model)
        self.assertEqual(row[f"L{n - 1}"], n - 1)
        cyc = dict(model, level="units", edges=model["edges"] + [(f"L{n - 1}", "L0")])
        self.assertEqual(len(G._layout(cyc)[2]), n)

    def test_one_track_header_goal_and_ids_are_never_cut(self):
        import learn_graph as G
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["title"] = "Учебная программа · длинное название · полный курс с упражнениями, облаком и собеседованиями"
        plan["goal"] = ("Первая часть: Python и основы ML, первые упражнения. "
                        "Вторая часть: облако, сервисы, портфолио и собеседования")
        plan["units"][0]["lessons"] = [lesson(f"PY{i:02d}", f"Python, тема {i}: генераторы") for i in range(1, 28)]
        plan["units"][2]["lessons"][0]["requires"] = ["PY05", "A2"]
        Path(self.gfile).write_text(json.dumps(plan))
        self.run_cmd("create", "ai", "--file", self.gfile, "--update")
        for lid in ("PY01", "PY02", "PY03"):
            self.run_cmd("done", lid, "--score", "1")
        _, _, root, _ = self.run_png("--track", "PY")
        self.assert_phone_sized(root)
        ns = "{http://www.w3.org/2000/svg}"
        width = float(root.get("viewBox").split()[2])
        texts = list(root.iter(ns + "text"))
        self.assertFalse([t.text for t in texts if "…" in (t.text or "")])
        joined = " ".join(t.text or "" for t in texts)
        for words in ("собеседованиями", "портфолио и собеседования", "PY27"):
            self.assertIn(words, joined)
        for t in texts:  # every line fits the picture
            w = G._text_w(t.text or "", float(t.get("font-size")), t.get("font-weight") == "bold")
            x = float(t.get("x")) - {"start": 0, "middle": w / 2, "end": w}[t.get("text-anchor")]
            self.assertTrue(0 <= x and x + w <= width + 1, t.text)
        circles = {(c.get("cx"), c.get("cy")): float(c.get("r")) for c in root.iter(ns + "circle")}
        for t in texts:  # ids sit inside their circle
            if (t.text or "").startswith("PY") and t.get("text-anchor") == "middle":
                r = max(r for (cx, cy), r in circles.items() if cx == t.get("x"))
                self.assertLessEqual(G._text_w(t.text, float(t.get("font-size")), True), 2 * r - 6, t.text)

    def test_long_track_collapses(self):
        plan = json.loads(json.dumps(GRAPH_PLAN))
        plan["units"][0]["lessons"] = [lesson(f"P{i}", f"L{i}") for i in range(1, 21)]
        plan["units"][2]["lessons"][0]["requires"] = ["P5", "A2"]
        Path(self.gfile).write_text(json.dumps(plan))
        self.run_cmd("create", "ai", "--file", self.gfile, "--update")
        line = [l for l in self.run_cmd("graph").splitlines() if "P1" in l and "…" in l][0]
        self.assertIn("… +", line)
        self.assertLess(len(line), 80)


def lh_fixture(root, fmt=1):
    """A tiny LearnHarness data dir: a repeated node id, a repeated chain and one with a missing endpoint."""
    g = Path(root) / "graph"
    g.mkdir(parents=True)
    lines = [
        {"id": "n_a", "name": "One fact per row", "statement": "Each row is one fact.", "aliases": ["A row is a fact"],
         "createdAt": "2026-09-30T10:00:00.000Z", "provenance": [{"chatId": "chat_1", "messageId": "m1"}]},
        {"id": "n_b", "name": "Primary key", "statement": "Rows are referred to only by values, so tables need keys.",
         "aliases": [], "createdAt": "2026-09-30T10:00:00.000Z", "provenance": [{"chatId": "chat_1", "messageId": ""}]},
        {"id": "n_a", "name": "ignored later name", "statement": "Every row states exactly one fact.", "aliases": [],
         "createdAt": "2026-10-01T10:00:00.000Z", "provenance": [{"chatId": "chat_2", "messageId": ""}]},
    ]
    (g / "nodes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines) + "not json\n")
    chains = [
        {"id": "arg_1", "premises": ["n_a"], "conclusion": "n_b", "createdAt": "2026-09-30T10:00:00.000Z",
         "provenance": [{"chatId": "chat_1", "messageId": ""}]},
        {"id": "arg_2", "premises": ["n_a"], "conclusion": "n_b", "createdAt": "2026-10-01T10:00:00.000Z",
         "provenance": [{"chatId": "chat_2", "messageId": ""}]},
        {"id": "arg_3", "premises": ["n_gone"], "conclusion": "n_b", "createdAt": "2026-10-01T10:00:00.000Z", "provenance": []},
    ]
    (g / "chains.jsonl").write_text("".join(json.dumps(x) + "\n" for x in chains))
    (g / "format.json").write_text(json.dumps({"version": fmt}))
    return root


class TestKnowledge(Base):
    def knowledge(self):
        return json.loads((Path(self.home) / "knowledge.json").read_text())

    def add(self, lesson, *items):
        return self.run_cmd("fact", "add", "--lesson", lesson, "--stdin", stdin=json.dumps(list(items)))

    def test_propositions_dedupe_by_statement_and_keep_old_wording_as_alias(self):
        self.add("L01", {"name": "Function", "statement": "A function names a  recipe once.", "kind": "truth"})
        out = self.add("L02", {"statement": "a function names a recipe once."})
        self.assertIn("already known", out)
        p = self.knowledge()["props"][0]
        self.assertEqual(p["sources"], [{"plan": "py", "lesson": "L01"}, {"plan": "py", "lesson": "L02"}])
        self.run_cmd("fact", "edit", "P001", "--statement", "A function gives a reusable recipe one name.")
        p = self.knowledge()["props"][0]
        self.assertEqual((p["statement"], p["aliases"]),
                         ("A function gives a reusable recipe one name.", ["A function names a recipe once."]))
        self.assertIn("already known", self.add("L03", {"statement": "A function names a recipe once."}))
        self.assertIn("L99", self.run_cmd("fact", "add", "--lesson", "L99", "--statement", "x", expect=1))
        self.assertIn("kind must be", self.add_err({"statement": "s", "kind": "axiom"}))

    def add_err(self, item):
        return self.run_cmd("fact", "add", "--lesson", "L01", "--stdin", stdin=json.dumps([item]), expect=1)

    def test_chains_merge_by_premise_set_and_reject_bad_references(self):
        self.add("L01", {"statement": "a"}, {"statement": "b"}, {"statement": "c"})
        self.assertIn("+ A001", self.run_cmd("chain", "add", "--premises", "P001,P002", "--conclusion", "P003"))
        self.assertIn("already known", self.run_cmd("chain", "add", "--premises", "P002 P001", "--conclusion", "P003",
                                                    "--lesson", "L02"))
        self.assertEqual(len(self.knowledge()["chains"]), 1)
        self.assertIn("own premises", self.run_cmd("chain", "add", "--premises", "P001,P003", "--conclusion", "P003", expect=1))
        self.assertIn("unknown proposition", self.run_cmd("chain", "add", "--premises", "P009", "--conclusion", "P003",
                                                          expect=1))
        self.assertIn("P001 a ∧ P002 b ⟹ P003 c", self.run_cmd("chain", "list", "--fact", "P002"))

    def test_cards_link_to_propositions_and_lapses_show_as_evidence(self):
        self.add("L01", {"statement": "a"})
        self.run_cmd("done", "L01", "--score", "0.75")
        self.run_cmd("card", "add", "--lesson", "L01", "--stdin", stdin=json.dumps([{"q": "q", "a": "a", "facts": ["P001"]}]))
        self.assertIn("unknown proposition", self.run_cmd("card", "add", "--lesson", "L01", "--q", "q", "--a", "a",
                                                          "--facts", "P404", expect=1))
        self.run_cmd("review", "grade", "C001", "fail", "--confidence", "90", day="2026-10-06")
        row = self.js("fact", "list")[0]
        self.assertEqual((row["cards"][0]["lapses"], row["lessons"][0]["score"]), (1, 0.75))
        self.assertIn("confident miss in review", row["reasons"])
        self.run_cmd("card", "edit", "C001", "--facts", "P001")

    def test_seed_migrates_key_ideas_of_finished_lessons_once(self):
        plan = json.loads(json.dumps(PLAN))
        plan["units"][0]["lessons"][0]["nodes"] = ["Repeated code means repeated bugs", "A function names a recipe once"]
        plan["units"][0]["lessons"][1]["nodes"] = ["not taught yet"]
        self.run_cmd("create", "py", "--update", "--stdin", stdin=json.dumps(plan))
        self.run_cmd("done", "L01")
        self.assertIn("seeded 2 draft", self.run_cmd("fact", "seed"))
        self.assertIn("nothing to seed", self.run_cmd("fact", "seed"))
        self.assertEqual(len(self.js("fact", "list", "--drafts")), 2)
        self.run_cmd("fact", "edit", "P001", "--statement", "Copy-pasted code must be fixed in every copy.")
        self.assertEqual([r["id"] for r in self.js("fact", "list", "--drafts")], ["P002"])
        self.assertIn("seeded 1", self.run_cmd("fact", "seed", "--all"))

    def test_practice_picks_weakest_interleaved_and_retests_todays_misses_first(self):
        self.add("L01", {"statement": "a"}, {"statement": "b"})
        self.add("L02", {"statement": "c"})
        self.add("L03", {"statement": "not taught"})
        self.run_cmd("done", "L01", "--score", "1")
        self.run_cmd("done", "L02", "--score", "0.5", "--accept")
        picked = self.js("practice", "pick", "-n", "2")
        self.assertEqual(picked["pool"], 3)
        self.assertEqual([t["id"] for t in picked["targets"]], ["P003", "P001"])  # weakest first, one per lesson
        self.assertEqual({t["mode"] for t in picked["targets"]}, {"recall"})
        self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall", "--outcome", "missed", "--miss", "cue")
        first = self.js("practice", "pick", "-n", "1")["targets"][0]
        self.assertEqual((first["id"], first["retest"]), ("P001", True))
        self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall", "--outcome", "solved", "--rung", "1")
        self.run_cmd("chain", "add", "--premises", "P002", "--conclusion", "P001")
        modes = {t["id"]: t["mode"] for t in self.js("practice", "pick", "-n", "3")["targets"]}
        self.assertEqual(modes["P001"], "discrimination")
        self.run_cmd("practice", "log", "--targets", "P001", "--mode", "discrimination", "--outcome", "solved")
        modes = {t["id"]: t["mode"] for t in self.js("practice", "pick", "-n", "3")["targets"]}
        self.assertEqual(modes["P001"], "composition")

    def test_practice_log_rejects_contradictions(self):
        self.add("L01", {"statement": "a"})
        self.assertIn("hinted", self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall",
                                             "--outcome", "solved", "--rung", "3", expect=1))
        self.assertIn("no miss type", self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall",
                                                   "--outcome", "solved", "--miss", "cue", expect=1))
        self.assertIn("0..5", self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall",
                                           "--outcome", "hinted", "--rung", "7", expect=1))
        self.assertEqual(self.knowledge()["practice"], [])

    def test_probe_is_saved_and_its_misconceptions_reach_today(self):
        strands = [{"strand": "functions", "floor": "calls built-ins", "ceiling": "cannot define one",
                    "misconceptions": ["return prints the value"], "build_from": "repeated code"}]
        self.run_cmd("probe", "set", "--stdin", stdin=json.dumps(strands))
        self.assertIn("⚠ return prints the value", self.run_cmd("probe", "show"))
        self.add("L01", {"statement": "a function names a recipe once"})
        today = self.run_cmd("today")
        self.assertIn("watch for (probe): return prints the value", today)
        self.assertIn("◆ P001 a function names a recipe once", today)
        plan = self.js("today")["plans"][0]
        self.assertEqual(plan["watch_for"], ["return prints the value"])
        self.assertEqual(plan["lessons"][0]["propositions"][0]["id"], "P001")
        self.assertIn("floor", self.run_cmd("probe", "set", "--stdin", stdin='[{"strand": "x"}]', expect=1))

    def test_history_and_week_include_recall_confidence_and_practice(self):
        self.add("L01", {"name": "Function", "statement": "a"})
        self.run_cmd("done", "L01", "--score", "0.8", "--note", "mixes up return and print")
        self.run_cmd("card", "add", "--lesson", "L01", "--q", "What does return do?", "--a", "hands back a value")
        self.run_cmd("review", "grade", "C001", "pass", "--confidence", "30", day="2026-10-06")
        self.run_cmd("practice", "log", "--targets", "P001", "--mode", "recall", "--outcome", "missed",
                     "--miss", "misconception")
        hist = self.run_cmd("history")
        self.assertIn("py/L01 lesson ✓ 80% · Lesson 1 — mixes up return and print", hist)
        self.assertIn("py/C001 recall ✓ q3 @30%", hist)
        self.assertIn("practice recall missed (misconception) · Function", hist)
        self.assertEqual([r["type"] for r in self.js("history", "--lesson", "L01")], ["lesson", "review", "practice"])
        self.assertIn("⚠ misconceptions: 1", self.run_cmd("week"))

    def test_learnharness_import_follows_their_merge_rules_and_is_idempotent(self):
        src = lh_fixture(os.path.join(self.tmp.name, "lh"))
        out = self.run_cmd("knowledge", "import", "--learnharness", src)
        self.assertIn("2 new / 0 merged propositions, 1 new / 0 merged chains", out)
        self.assertIn("missing node(s) n_gone", out)
        self.assertIn("not JSON", out)
        k = self.knowledge()
        a = k["props"][0]
        self.assertEqual((a["name"], a["lh_id"]), ("One fact per row", "n_a"))
        self.assertEqual(a["aliases"], ["A row is a fact", "Every row states exactly one fact."])
        self.assertEqual(a["sources"], [{"lh": "chat_1"}, {"lh": "chat_2"}])
        self.assertEqual(k["chains"][0]["sources"], [{"lh": "chat_1"}, {"lh": "chat_2"}])
        self.run_cmd("knowledge", "import", "--learnharness", os.path.join(src, "graph"), "--lesson", "L02")
        k = self.knowledge()
        self.assertEqual((len(k["props"]), len(k["chains"])), (2, 1))
        self.assertIn({"plan": "py", "lesson": "L02"}, k["props"][1]["sources"])
        newer = lh_fixture(os.path.join(self.tmp.name, "lh2"), fmt=2)
        self.assertIn("newer", self.run_cmd("knowledge", "import", "--learnharness", newer, expect=1))
        self.assertIn("no LearnHarness graph", self.run_cmd("knowledge", "import", "--learnharness", self.tmp.name, expect=1))

    def test_export_in_their_format_round_trips_lesson_links(self):
        self.add("L01", {"name": "Function", "statement": "a"}, {"statement": "b"})
        self.run_cmd("chain", "add", "--premises", "P001", "--conclusion", "P002", "--lesson", "L01")
        dump = self.js("knowledge", "export", "--format", "json")
        self.assertEqual(dump["nodes"][0]["provenance"], [{"chatId": "src_learn_py_L01", "messageId": ""}])
        self.assertEqual(dump["sources"]["src_learn_py_L01"]["filePath"], "learn:py/L01")
        self.assertEqual(dump["chains"][0]["premises"], ["n_learn_p001"])
        out = os.path.join(self.tmp.name, "out")
        self.run_cmd("knowledge", "export", "--format", "learnharness", "--out", out)
        self.assertIn("refusing to overwrite", self.run_cmd("knowledge", "export", "--format", "learnharness",
                                                            "--out", out, expect=1))
        other = os.path.join(self.tmp.name, "other")
        self.run_cmd("--home", other, "init")
        self.run_cmd("--home", other, "knowledge", "import", "--learnharness", out)
        k = json.loads((Path(other) / "knowledge.json").read_text())
        self.assertEqual(k["props"][0]["sources"], [{"plan": "py", "lesson": "L01"}])
        self.assertEqual(k["props"][0]["lh_id"], "n_learn_p001")
        md = self.run_cmd("knowledge", "export")
        self.assertIn("## py/L01 · Lesson 1", md)
        self.assertIn("- **Function** — a", md)
        self.assertIn("- Function ⟹ b", md)

    def test_newer_knowledge_file_is_refused(self):
        (Path(self.home) / "knowledge.json").write_text(json.dumps({"version": 9}))
        self.assertIn("format 9", self.run_cmd("fact", "list", expect=1))


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

    def test_validate_warns_about_python_check_outside_py_track(self):
        self.run_cmd("done", "L01", "--score", "1", day="2026-10-08")
        plan_path = Path(self.home) / "plans" / "py" / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["units"][0]["id"] = "MATH"
        plan["units"][0]["lessons"][0]["check"] = "3 задачи руками + те же в Python-списках"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            learn.main(["--home", self.home, "validate", "--plan", "py"])
        self.assertIn("requires Python", err.getvalue())

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


class TestRemoteFoundations(Base):
    """Things the MCP/API server relies on: safe slugs, shared-home locking, profile and plan access."""

    def test_slug_cannot_escape_plans_dir(self):
        for bad in ("../evil", "a/b", ".hidden", "a\\b", "x" * 65):
            msg = self.run_cmd("create", bad, "--file", self.plan_file, expect=1)
            self.assertIn("invalid plan slug", msg)
            msg = self.run_cmd("done", "L01", "--plan", bad, expect=1)
            self.assertIn("invalid plan slug", msg)
        self.assertFalse((Path(self.home) / "evil").exists())
        self.assertEqual(sorted(p.name for p in (Path(self.home) / "plans").iterdir()), ["py"])

    def test_unicode_slug_is_fine(self):
        self.run_cmd("create", "питон", "--file", self.plan_file)
        self.assertIn("питон", self.run_cmd("plans"))

    def test_profile_show_and_replace(self):
        self.assertIn("Learner profile", self.run_cmd("profile"))
        self.assertIn("profile updated", self.run_cmd("profile", "--stdin", stdin="# Я\n- люблю примеры"))
        self.assertEqual(self.run_cmd("profile").strip(), "# Я\n- люблю примеры")
        self.run_cmd("profile", "--stdin", stdin="  \n", expect=1)
        self.assertIn("люблю", self.run_cmd("profile"))

    def test_show_plan_json_roundtrips_into_update(self):
        shown = self.run_cmd("show")
        self.assertEqual(json.loads(shown)["title"], "Python basics")
        self.run_cmd("done", "L01", "--score", "1")
        self.run_cmd("create", "py", "--stdin", "--update", stdin=shown)
        self.assertEqual(self.js("status")["plans"][0]["done"], 1)  # progress survived
        self.assertIn("- [x] **L01**", self.run_cmd("show", "--md"))

    def test_parallel_processes_do_not_lose_updates(self):
        # Each child writes slowly, so without the home lock all eight would read the same
        # next_card and clobber each other.
        driver = ("import sys, time\n"
                  f"sys.path.insert(0, {str(Path(learn.__file__).parent)!r})\n"
                  "import learn\n"
                  "orig = learn.write_text\n"
                  "learn.write_text = lambda p, t: (time.sleep(0.05), orig(p, t))[1]\n"
                  "sys.exit(learn.main(sys.argv[1:]))\n")
        cmd = [sys.executable, "-c", driver, "--home", self.home, "card", "add", "--lesson", "L01", "--stdin"]
        procs = [subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(8)]
        for i, p in enumerate(procs):  # feed everyone first, so they really do run at the same time
            p.stdin.write(json.dumps([{"q": f"q{i}", "a": f"a{i}"}]))
            p.stdin.close()
        for p in procs:
            err = p.stderr.read()
            self.assertEqual(p.wait(timeout=60), 0, err)
            p.stdout.close()
            p.stderr.close()
        cards = json.loads((Path(self.home) / "plans/py/progress.json").read_text())["cards"]
        self.assertEqual(len(cards), 8)
        self.assertEqual(len({c["id"] for c in cards}), 8)


if __name__ == "__main__":
    unittest.main(verbosity=1)
