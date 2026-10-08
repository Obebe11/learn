"""Tests for learn_server.py — run: python3 scripts/test_learn_server.py"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import learn  # noqa: E402
import learn_server as S  # noqa: E402
from test_learn import PLAN  # noqa: E402

TOKEN = "t" * 40
SERVER_PY = str(Path(S.__file__))


class Base(unittest.TestCase):
    """A real server on an ephemeral port, talking to a throw-away data directory."""

    allowed_origins = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "learning")
        os.environ["LEARN_TODAY"] = "2026-10-05"  # a Monday; inherited by the learn.py children
        self.assertEqual(learn.main(["--home", self.home, "init", "--language", "ru"]), 0)
        self.server = S.Server(("127.0.0.1", 0), S.Core(self.home), TOKEN, self.allowed_origins)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.rid = 0

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
        os.environ.pop("LEARN_TODAY", None)

    # --- helpers ---

    def http(self, method, path, body=None, headers=None, auth=True):
        h = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
        h.update(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, path, body=body.encode() if isinstance(body, str) else body, headers=h)
            r = conn.getresponse()
            data = r.read()
            return r.status, dict(r.getheaders()), data
        finally:
            conn.close()

    def rpc(self, method, params=None, **kw):
        self.rid += 1
        msg = {"jsonrpc": "2.0", "id": self.rid, "method": method}
        if params is not None:
            msg["params"] = params
        status, _, data = self.http("POST", "/mcp", msg, **kw)
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def tool(self, name, args=None, expect_error=False):
        res = self.rpc("tools/call", {"name": name, "arguments": args or {}})["result"]
        text = res["content"][0]["text"]
        self.assertEqual(res["isError"], expect_error, text)
        return text

    def create_plan(self, slug="py"):
        return self.tool("learn_create", {"slug": slug, "plan": PLAN})


class TestProtocol(Base):
    def test_initialize_negotiates_version(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["capabilities"])
        self.assertIn("learn_skill", r["instructions"])
        r = self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(r["protocolVersion"], S.PROTOCOL_VERSIONS[0])  # we offer our newest; the client decides

    def test_notification_gets_202_and_no_body(self):
        status, _, data = self.http("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, data), (202, b""))

    def test_ping_unknown_method_and_garbage(self):
        self.assertEqual(self.rpc("ping")["result"], {})
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        status, _, data = self.http("POST", "/mcp", "{nope")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], -32700)
        status, _, data = self.http("POST", "/mcp", {"hello": 1})
        self.assertEqual(json.loads(data)["error"]["code"], -32600)

    def test_batch(self):
        status, _, data = self.http("POST", "/mcp", [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                                    {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
        self.assertEqual([r["id"] for r in json.loads(data)], [1, 2])

    def test_get_and_delete_on_mcp_are_405(self):
        for m in ("GET", "DELETE"):
            status, headers, _ = self.http(m, "/mcp")
            self.assertEqual(status, 405)
            self.assertEqual(headers["Allow"], "POST")

    def test_tools_are_described_for_clients(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        for needed in ("learn_today", "learn_done", "learn_card_add", "learn_review_grade", "learn_create",
                       "learn_profile_get", "learn_skill"):
            self.assertIn(needed, names)
        self.assertNotIn("learn_sync", names)  # the server is the source of truth: nothing to sync
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
            self.assertFalse(t["inputSchema"]["additionalProperties"], t["name"])
            self.assertIn("readOnlyHint", t["annotations"])
            self.assertFalse(t["annotations"]["destructiveHint"])
        by = {t["name"]: t for t in tools}
        self.assertTrue(by["learn_today"]["annotations"]["readOnlyHint"])
        self.assertFalse(by["learn_done"]["annotations"]["readOnlyHint"])
        self.assertIn("json", by["learn_today"]["inputSchema"]["properties"])
        self.assertNotIn("json", by["learn_done"]["inputSchema"]["properties"]["lesson"].get("properties", {}))

    def test_every_tool_passes_the_cli_parser_with_all_of_its_arguments(self):
        """Guards the argument mapping: a wrong flag name only shows up when that tool is really run."""
        self.create_plan()
        self.tool("learn_card_add", {"lesson": "L01", "cards": [{"q": "q", "a": "a"}]})
        sample = {"plan": "py", "track": "U1", "max_reviews": 3, "count": 1, "view": "md", "level": "units", "md": True,
                  "lesson": "L03", "score": 0.5, "note": "n", "accept": True, "force": True, "card": "py/C001",
                  "result": "pass", "slug": "py", "update": True, "date": "2026-10-05", "status": "active",
                  "content": "# me", "key": "language", "value": "ru", "language": "ru", "cta": "/x", "markdown": True,
                  "all": True, "limit": 5, "name": "teach", "json": True, "cards": [{"q": "q2", "a": "a2"}]}
        parser_errors = ("unrecognized arguments", "usage:", "expected one argument", "invalid choice",
                         "arguments are required", "ambiguous option", "invalid int value", "invalid float value")
        for t in self.server.core.tools.values():
            args = {k: sample[k] for k in t.schema["properties"] if k in sample}
            if t.name == "learn_create":
                args["plan"] = PLAN
            if t.name == "learn_skip":
                args["lesson"] = "L06"
            res = self.rpc("tools/call", {"name": t.name, "arguments": args})["result"]
            text = res["content"][0]["text"]
            self.assertFalse(any(e in text for e in parser_errors), f"{t.name}: {text}")
            unmapped = set(args) ^ set(t.schema["properties"])
            self.assertFalse(unmapped, f"{t.name}: sample has no value for {unmapped}")

    def test_every_tool_runs_without_crashing_on_an_empty_home(self):
        # no plan yet: tools must answer with a readable error or text, never an internal error
        for t in self.server.core.tools.values():
            args = {"lesson": "L01", "card": "C001", "result": "pass", "slug": "x", "plan": {"title": "t"},
                    "content": "hi", "key": "language", "value": "ru", "status": "paused", "cards": [{"q": "q", "a": "a"}]}
            args = {k: v for k, v in args.items() if k in t.schema["properties"]}
            if t.name == "learn_set_plan_status":
                args["plan"] = "nope"
            res = self.rpc("tools/call", {"name": t.name, "arguments": args})
            self.assertIn("result", res, t.name)
            self.assertNotIn("internal error", json.dumps(res), t.name)


class TestSessionOverMcp(Base):
    def test_full_study_session(self):
        self.assertIn("created plan 'py'", self.create_plan())
        today = self.tool("learn_today")
        self.assertIn("L01", today)
        self.assertIn("check: check 1", today)
        data = json.loads(self.tool("learn_today", {"json": True}))
        self.assertEqual([l["id"] for l in data["plans"][0]["lessons"]], ["L01", "L02"])

        self.assertIn("L01 done", self.tool("learn_done", {"lesson": "L01", "score": 0.9, "note": "mixes up X"}))
        self.assertIn("added 2 card(s)", self.tool("learn_card_add", {
            "lesson": "L01", "cards": [{"q": "Зачем функции?", "a": "Чтобы не повторяться"}, {"q": "q2", "a": "a2"}]}))
        # a retry: below the pass bar
        self.assertIn("needs another pass", self.tool("learn_done", {"lesson": "L02", "score": 0.2}))

        status = json.loads(self.tool("learn_status", {"json": True}))
        self.assertEqual(status["plans"][0]["done"], 1)
        # the cards come due tomorrow
        os.environ["LEARN_TODAY"] = "2026-10-06"
        due = json.loads(self.tool("learn_review_due", {"json": True}))
        self.assertEqual(len(due), 2)
        self.assertIn("box 2", self.tool("learn_review_grade", {"card": f"py/{due[0]['id']}", "result": "pass"}))
        self.assertEqual(len(json.loads(self.tool("learn_review_due", {"json": True}))), 1)

        # progress is persisted by the real CLI in the data dir
        prog = json.loads((Path(self.home) / "plans/py/progress.json").read_text())
        self.assertEqual(prog["lessons"]["L01"]["status"], "done")
        self.assertEqual(prog["lessons"]["L01"]["note"], "mixes up X")

    def test_plan_revision_roundtrip(self):
        self.create_plan()
        self.tool("learn_done", {"lesson": "L01", "score": 1})
        plan = json.loads(self.tool("learn_show"))
        plan["units"][0]["lessons"].append({"id": "L07", "title": "New", "objective": "o", "check": "c"})
        self.assertIn("updated plan", self.tool("learn_create", {"slug": "py", "plan": plan, "update": True}))
        self.assertEqual(json.loads(self.tool("learn_status", {"json": True}))["plans"][0]["total"], 7)
        self.assertIn("- [x] **L01**", self.tool("learn_show", {"md": True}))
        self.tool("learn_create", {"slug": "py", "plan": plan}, expect_error=True)  # exists, no update flag

    def test_invalid_plan_reports_every_problem(self):
        bad = json.loads(json.dumps(PLAN))
        bad["units"][0]["lessons"][1]["id"] = "L01"
        bad["pace"]["days"] = ["funday"]
        msg = self.tool("learn_create", {"slug": "bad", "plan": bad}, expect_error=True)
        self.assertIn("duplicate lesson id L01", msg)
        self.assertIn("pace.days", msg)

    def test_profile_and_config(self):
        self.assertIn("Learner profile", self.tool("learn_profile_get"))
        self.tool("learn_profile_set", {"content": "# Профиль\n- учусь с телефона"})
        self.assertIn("учусь с телефона", self.tool("learn_profile_get"))
        self.assertEqual(self.tool("learn_config_get", {"key": "language"}), "ru")
        self.tool("learn_config_set", {"key": "chat_format", "value": "rich"})
        self.assertEqual(json.loads(self.tool("learn_config_get", {"json": True}))["chat_format"], "rich")
        self.tool("learn_config_set", {"key": "chat_format", "value": "html"}, expect_error=True)

    def test_set_plan_status_and_nudge(self):
        self.create_plan()
        self.assertIn("paused", self.tool("learn_set_plan_status", {"plan": "py", "status": "paused"}))
        self.assertEqual(self.tool("learn_nudge"), "(no output)")  # nothing to remind about: empty output
        self.tool("learn_set_plan_status", {"plan": "py", "status": "active"})
        self.assertIn("Сегодня", self.tool("learn_nudge"))

    def test_graph_views(self):
        self.create_plan()
        self.assertIn("Python basics", self.tool("learn_graph"))
        self.assertTrue(self.tool("learn_graph", {"view": "mermaid"}).startswith(("graph", "flowchart")))
        self.tool("learn_graph", {"view": "png"}, expect_error=True)  # not offered remotely

    def test_cli_errors_come_back_as_tool_errors(self):
        self.create_plan()
        self.assertIn("no such lesson", self.tool("learn_done", {"lesson": "ZZ"}, expect_error=True))
        self.assertIn("no such plan", self.tool("learn_status", {"plan": "ghost"}, expect_error=True))

    def test_skills_are_served_with_the_remote_adapter(self):
        listing = self.tool("learn_skill")
        for name in ("daily-lesson", "study-plan", "teach"):
            self.assertIn(name, listing)
        text = self.tool("learn_skill", {"name": "daily-lesson"})
        self.assertIn("[Remote mode]", text)
        self.assertIn("learn_card_add", text)
        self.assertIn("# Daily lesson", text)
        self.assertNotIn("\ndescription:", text)  # frontmatter is stripped
        self.tool("learn_skill", {"name": "../../etc/passwd"}, expect_error=True)
        prompts = self.rpc("prompts/list")["result"]["prompts"]
        self.assertIn("daily-lesson", {p["name"] for p in prompts})
        got = self.rpc("prompts/get", {"name": "teach"})["result"]
        self.assertIn("[Remote mode]", got["messages"][0]["content"]["text"])
        self.assertEqual(self.rpc("prompts/get", {"name": "nope"})["error"]["code"], -32602)

    def test_parallel_calls_never_lose_a_write(self):
        self.create_plan()

        def add(i):
            return self.tool("learn_card_add", {"lesson": "L01", "cards": [{"q": f"q{i}", "a": f"a{i}"}]})

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(add, range(8)))
        cards = json.loads((Path(self.home) / "plans/py/progress.json").read_text())["cards"]
        self.assertEqual(len({c["id"] for c in cards}), 8)


class TestArgumentSafety(Base):
    def test_schema_violations_are_tool_errors_the_model_can_fix(self):
        self.create_plan()
        cases = [
            ("learn_done", {}, "missing required argument 'lesson'"),
            ("learn_done", {"lesson": "L01", "score": 2}, "between"),
            ("learn_done", {"lesson": "L01", "score": "high"}, "must be number"),
            ("learn_done", {"lesson": "L01", "score": True}, "must be number"),
            ("learn_done", {"lesson": "L01", "bogus": 1}, "unknown argument 'bogus'"),
            ("learn_done", {"lesson": "L01", "force": "yes"}, "must be boolean"),
            ("learn_review_grade", {"card": "C001", "result": "maybe"}, "one of: pass, fail"),
            ("learn_card_add", {"lesson": "L01", "cards": []}, "items"),
            ("learn_card_add", {"lesson": "L01", "cards": [{"q": "only q"}]}, "cards[0].a is required"),
            ("learn_start", {"date": "tomorrow"}, "invalid format"),
        ]
        for name, args, needle in cases:
            self.assertIn(needle, self.tool(name, args, expect_error=True), (name, args))

    def test_values_cannot_smuggle_flags_or_paths(self):
        self.create_plan()
        for name, args in [("learn_done", {"lesson": "--force"}),
                           ("learn_done", {"lesson": "L 01"}),
                           ("learn_review_grade", {"card": "--plan", "result": "pass"}),
                           ("learn_status", {"plan": "../../etc"}),
                           ("learn_status", {"plan": "-h"}),
                           ("learn_create", {"slug": "../evil", "plan": PLAN}),
                           ("learn_create", {"slug": ".hidden", "plan": PLAN}),
                           ("learn_today", {"track": "-x"}),
                           ("learn_done", {"lesson": "L01\x00"})]:
            self.tool(name, args, expect_error=True)
        self.assertFalse((Path(self.tmp.name) / "evil").exists())
        self.assertFalse((Path(self.home) / "evil").exists())

    def test_option_values_starting_with_a_dash_are_data_not_flags(self):
        self.create_plan()
        self.tool("learn_done", {"lesson": "L01", "note": "--force is not a flag here", "score": 1})
        prog = json.loads((Path(self.home) / "plans/py/progress.json").read_text())
        self.assertEqual(prog["lessons"]["L01"]["note"], "--force is not a flag here")

    def test_data_dir_cannot_be_chosen_by_the_client(self):
        self.tool("learn_status", {"home": "/tmp"}, expect_error=True)


class TestRest(Base):
    def call(self, name, body=None, method="POST"):
        status, _, data = self.http(method, f"/api/tools/{name}", body)
        return status, json.loads(data)

    def test_list_and_call(self):
        status, _, data = self.http("GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertIn("learn_today", {t["name"] for t in json.loads(data)["tools"]})
        status, r = self.call("learn_create", {"slug": "py", "plan": PLAN})
        self.assertEqual(status, 200)
        self.assertTrue(r["ok"])
        status, r = self.call("learn_status", {"json": True})
        self.assertEqual(r["data"]["plans"][0]["total"], 6)
        self.assertIn("Python basics", r["output"])

    def test_errors_map_to_status_codes(self):
        self.assertEqual(self.call("learn_nope")[0], 404)
        status, r = self.call("learn_done", {"lesson": "L01"})  # no plan -> the CLI refuses
        self.assertEqual(status, 422)
        self.assertIn("error", r)
        self.assertEqual(self.call("learn_done", {"score": "x"})[0], 400)
        status, _, _ = self.http("POST", "/api/tools/learn_status", "[1]")
        self.assertEqual(status, 400)
        self.assertEqual(self.http("GET", "/api/nothing")[0], 404)

    def test_get_is_for_read_only_tools_and_coerces_the_query(self):
        self.call("learn_create", {"slug": "py", "plan": PLAN})
        status, r = self.call("learn_today", method="GET")
        self.assertEqual(status, 200)
        status, _, data = self.http("GET", "/api/tools/learn_next?count=1&raw=1")
        self.assertEqual(status, 200)
        self.assertTrue(data.decode().startswith("L01 · Lesson 1"))
        self.assertEqual(len(data.decode().split("\n\n")), 1)  # count=1 was converted to an int and honoured
        status, headers, _ = self.http("GET", "/api/tools/learn_done?lesson=L01")
        self.assertEqual((status, headers["Allow"]), (405, "POST"))
        self.assertEqual(self.call("learn_done", {"lesson": "L01"})[0], 200)
        self.assertEqual(self.http("GET", "/api/tools/learn_status?bogus=1")[0], 400)

    def test_oversized_and_chunked_bodies_are_refused(self):
        status, _, _ = self.http("POST", "/api/tools/learn_profile_set", headers={"Content-Length": str(S.MAX_BODY + 1)})
        self.assertEqual(status, 413)
        status, _, _ = self.http("POST", "/mcp", "{}", headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(status, 411)


class TestAuth(Base):
    allowed_origins = ("https://app.example",)

    def test_health_is_open_everything_else_needs_the_token(self):
        self.assertEqual(self.http("GET", "/health", auth=False)[0], 200)
        for method, path in (("GET", "/api/tools"), ("POST", "/mcp"), ("POST", "/api/tools/learn_status")):
            status, headers, _ = self.http(method, path, "{}", auth=False)
            self.assertEqual((status, headers["WWW-Authenticate"]), (401, "Bearer"), path)
        status, _, _ = self.http("GET", "/api/tools", headers={"Authorization": "Bearer " + "x" * 40})
        self.assertEqual(status, 401)
        status, _, _ = self.http("GET", "/api/tools", headers={"Authorization": TOKEN})  # scheme missing
        self.assertEqual(status, 401)

    def test_token_in_the_url_prefix_works_for_clients_without_headers(self):
        status, _, data = self.http("POST", f"/t/{TOKEN}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, auth=False)
        self.assertEqual((status, json.loads(data)["result"]), (200, {}))
        self.assertEqual(self.http("GET", f"/t/{TOKEN}/api/tools/learn_plans?raw=1", auth=False)[0], 200)
        self.assertEqual(self.http("GET", f"/t/{'z' * 40}/api/tools", auth=False)[0], 401)

    def test_browser_origins_are_refused_unless_allowed(self):
        self.assertEqual(self.http("GET", "/api/tools", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.http("GET", "/api/tools", headers={"Origin": "https://app.example"})[0], 200)
        self.assertEqual(self.http("GET", "/api/tools", auth=False, headers={"Origin": "https://app.example"})[0], 401)


class TestStartup(unittest.TestCase):
    def run_server(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if k != "LEARN_TOKEN"}
        e.update(env or {})
        return subprocess.run([sys.executable, SERVER_PY, *args], capture_output=True, text=True, env=e, timeout=30)

    def test_refuses_to_start_without_a_strong_token(self):
        for env in ({}, {"LEARN_TOKEN": "short"}):
            r = self.run_server("--port", "0", env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("LEARN_TOKEN", r.stderr)
        r = self.run_server("--port", "0", env={"LEARN_TOKEN": "has space " * 4})
        self.assertNotEqual(r.returncode, 0)

    def test_token_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "token"
            f.write_text("short\n")
            self.assertNotEqual(self.run_server("--token-file", str(f), "--port", "0").returncode, 0)
            self.assertNotEqual(self.run_server("--token-file", str(Path(d) / "missing"), "--port", "0").returncode, 0)


class TestStdio(unittest.TestCase):
    def test_mcp_over_stdio(self):
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "learning")
            self.assertEqual(learn.main(["--home", home, "init", "--language", "ru"]), 0)
            msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "learn_profile_set", "arguments": {"content": "Привет 🎓"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "learn_profile_get", "arguments": {}}}]
            inp = "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"
            r = subprocess.run([sys.executable, SERVER_PY, "--stdio", "--home", home], input=inp,
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            replies = [json.loads(line) for line in r.stdout.splitlines()]
            self.assertEqual([x.get("id") for x in replies], [1, 2, 3, None])  # the notification got no reply
            self.assertEqual(replies[3]["error"]["code"], -32700)
            self.assertEqual(replies[2]["result"]["content"][0]["text"], "Привет 🎓")


if __name__ == "__main__":
    unittest.main(verbosity=1)
