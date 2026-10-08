#!/usr/bin/env python3
"""learn-server — the `learn` CLI as an MCP server and a REST API.

One process on a VPS owns the data directory ($LEARN_HOME), so every device sees
the same progress at once and nothing ever needs merging. Stdlib only (Python 3.9+).
Every tool is a thin, validated wrapper that runs `learn.py`, so the CLI stays the
single implementation of the rules (and its per-command lock keeps the server, a
Hermes cron job and your own shell from overwriting each other).

  LEARN_TOKEN=<secret> python3 learn_server.py          # HTTP on 127.0.0.1:8787
  python3 learn_server.py --stdio                       # MCP over stdin/stdout (e.g. through ssh)

HTTP endpoints. Authenticate with `Authorization: Bearer <token>`, or — for clients that
cannot set headers — put the token in the URL: /t/<token>/mcp
  POST /mcp                  MCP, Streamable HTTP (JSON-RPC)
  GET  /api/tools            tool list with JSON schemas
  POST /api/tools/<name>     call a tool; the JSON body is its arguments
  GET  /api/tools/<name>     same for read-only tools; arguments in the query string (?raw=1 -> plain text)
  GET  /health               liveness, no auth

Serve it behind a TLS reverse proxy (Caddy, nginx): see docs/remote-server.md.
"""

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent  # resolve(): works when started through a symlink
LEARN_PY = HERE / "learn.py"
SKILLS_DIR = HERE.parent / "skills"

VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 1 << 20  # plans are small; anything bigger is a mistake or an attack
CLI_TIMEOUT = 60
MIN_TOKEN = 24

INSTRUCTIONS = """\
This server is the learner's long-term learning system (study plans, daily lessons, spaced-repetition reviews). \
All progress lives here, so any device continues where the last one stopped.

Before running a session, call `learn_skill` with the method you need and follow it: \
`daily-lesson` (study / continue / answered a reminder), `study-plan` (new goal, progress, change the plan), \
`teach` (how to explain anything). Every learner-visible fact still has to be taught well — the tools only keep the score. \
Nothing counts until it is recorded: `learn_done`, `learn_card_add`, `learn_review_grade`."""

ADAPTER = """\
[Remote mode] You are connected to a remote `learn` server, not a shell. Read the skill below with these substitutions:
- Every `learn <command>` is the MCP tool `learn_<command>` (`learn card add` -> learn_card_add, `learn review grade` -> \
learn_review_grade); flags become arguments.
- Skip `learn sync`: the server is the single source of truth, there is nothing to merge.
- `profile.md` -> learn_profile_get / learn_profile_set. `config.json` -> learn_config_get / learn_config_set.
- To create or revise a plan, pass the plan object itself to learn_create (update: true keeps progress). Read the current \
plan with learn_show, edit it, send it back. There are no local files and no temp files.
- Ignore references to files (`references/...`), image sending and `rsvg-convert`; use the text forms. Tools named \
`quiz`, `ask_user_question`, `researcher` are optional: use their text fallbacks.

"""


# ----------------------------------------------------------------------------
# argument validation (the JSON-Schema subset the tools use)
# ----------------------------------------------------------------------------

class ArgError(Exception):
    pass


def check(schema, value, name="arguments"):
    t = schema["type"]
    if t in ("integer", "number"):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and (t == "number" or isinstance(value, int))
    else:
        ok = isinstance(value, {"string": str, "boolean": bool, "array": list, "object": dict}[t])
    if not ok:
        raise ArgError(f"{name} must be {t}")
    if "enum" in schema and value not in schema["enum"]:
        raise ArgError(f"{name} must be one of: {', '.join(map(str, schema['enum']))}")
    if t == "string":
        if "\x00" in value:
            raise ArgError(f"{name} contains a NUL character")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_BODY):
            raise ArgError(f"{name} has an invalid length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ArgError(f"{name} has an invalid format")
    elif t in ("integer", "number"):
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            raise ArgError(f"{name} must be between {schema.get('minimum')} and {schema.get('maximum')}")
    elif t == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            raise ArgError(f"{name} must have {schema.get('minItems', 0)}..{schema.get('maxItems', 1000)} items")
        for i, item in enumerate(value):
            check(schema["items"], item, f"{name}[{i}]")
    elif t == "object" and "properties" in schema:
        props = schema["properties"]
        for k in schema.get("required", ()):
            if k not in value:
                raise ArgError(f"missing required argument '{k}'" if name == "arguments" else f"{name}.{k} is required")
        for k, v in value.items():
            if k not in props:
                if schema.get("additionalProperties") is False:
                    raise ArgError(f"unknown argument '{k}'" if name == "arguments" else f"{name}.{k} is not allowed")
            else:
                check(props[k], v, k if name == "arguments" else f"{name}.{k}")


# ----------------------------------------------------------------------------
# tools
# ----------------------------------------------------------------------------

@dataclass
class Outcome:
    ok: bool
    out: str = ""
    err: str = ""
    kind: str = "cli"  # "args" = the request was malformed, "cli" = learn refused it


@dataclass
class Tool:
    name: str
    description: str
    run: Callable
    props: dict = field(default_factory=dict)
    required: tuple = ()
    readonly: bool = False
    idempotent: bool = False
    structured: bool = False  # accepts the optional `json` argument (machine-readable output)

    @property
    def schema(self):
        props = dict(self.props)
        if self.structured:
            props["json"] = {"type": "boolean", "description": "return machine-readable JSON instead of text"}
        schema = {"type": "object", "properties": props, "additionalProperties": False}
        if self.required:  # an empty `required` list is invalid in older JSON Schema drafts
            schema["required"] = list(self.required)
        return schema

    @property
    def title(self):
        return self.name.replace("learn_", "learn ").replace("_", " ")

    def annotations(self):
        return {"title": self.title, "readOnlyHint": self.readonly, "destructiveHint": False,
                "idempotentHint": self.readonly or self.idempotent, "openWorldHint": False}


# Values that become positional CLI arguments may not start with "-" (argparse would read them as flags).
SLUG = r"^[^-./\\][^/\\]*$"
IDENT = r"^[^-\s]\S*$"


def s(desc, **kw):
    return {"type": "string", "description": desc, **kw}


PLAN = s("plan slug; omit when there is a single active plan", pattern=SLUG, maxLength=64)
LESSON = s("lesson id, e.g. L07", pattern=IDENT, maxLength=64)
TRACK = s("graph plans: only this track (unit id)", pattern=IDENT, maxLength=64)
CARD = s("card id C007 or plan/C007", pattern=IDENT, maxLength=140)


def cli(cmd, opts=(), flags=(), pos=(), fixed=(), stdin=None):
    """Tool runner: build argv from validated arguments and run learn.py.

    opts   -> --name=value   (the `=` form keeps values that start with "-" from being read as flags)
    flags  -> --name         (when truthy)
    pos    -> positional values, appended last
    stdin  -> callable(args) producing the text piped to learn
    """
    cmd = [cmd] if isinstance(cmd, str) else list(cmd)

    def run(core, a):
        argv = [*cmd, *fixed]
        for k in opts:
            if k in a:
                argv.append(f"--{k.replace('_', '-')}={a[k]}")
        argv += [f"--{k}" for k in flags if a.get(k)]
        if a.get("json"):
            argv.append("--json")
        argv += [str(a[k]) for k in pos if k in a]
        return core.cli(argv, stdin(a) if stdin else None)
    return run


def set_plan_status(core, a):
    verb = {"active": "resume", "paused": "pause", "archived": "archive"}[a["status"]]
    return core.cli([verb, a["plan"]])


def skill_tool(core, a):
    skills = load_skills()
    if not skills:
        return Outcome(False, err="no skills found next to the server (expected ../skills)", kind="args")
    name = a.get("name")
    if not name:
        return Outcome(True, "\n".join(f"{n}: {d}" for n, (d, _) in skills.items()))
    if name not in skills:
        return Outcome(False, err=f"unknown skill '{name}' (have: {', '.join(skills)})", kind="args")
    return Outcome(True, ADAPTER + skills[name][1])


def load_skills():
    out = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        desc, body = "", text
        if text.startswith("---\n") and "\n---\n" in text[4:]:
            head, body = text[4:].split("\n---\n", 1)
            m = re.search(r"^description:\s*(.+)$", head, re.M)
            desc = m.group(1).strip() if m else ""
        out[path.parent.name] = (desc, body.lstrip("\n"))
    return out


TOOLS = [
    Tool("learn_today",
         "Start every study session here: today's lessons (objective, check, key ideas), warm-up review cards that are due, "
         "streak, pace and language/chat format. Graph plans mix tracks on purpose; call it again after learn_done because "
         "lessons may unlock.",
         cli("today", opts=("plan", "track", "max_reviews")),
         {"plan": PLAN, "track": TRACK, "max_reviews": {"type": "integer", "minimum": 0, "maximum": 50,
                                                         "description": "cap on warm-up reviews (default 6)"}},
         readonly=True, structured=True),
    Tool("learn_status", "Progress per plan: bar, pace (behind/ahead), projected finish date, reviews due, next lesson.",
         cli("status", opts=("plan",)), {"plan": PLAN}, readonly=True, structured=True),
    Tool("learn_plans", "List all plans with status and progress.",
         cli("plans"), readonly=True, structured=True),
    Tool("learn_next", "Upcoming lessons regardless of today's quota (only when the learner asks to study ahead).",
         cli("next", opts=("plan", "count", "track")),
         {"plan": PLAN, "count": {"type": "integer", "minimum": 1, "maximum": 10}, "track": TRACK}, readonly=True, structured=True),
    Tool("learn_graph", "The knowledge map of a graph plan: tracks, junctions, what is ready / locked. "
                        "view=text for plain chat (put it in a code block), md for a table, mermaid for diagram renderers.",
         lambda core, a: core.cli(["graph", f"--format={a.get('view', 'text')}", *([f"--plan={a['plan']}"] if "plan" in a else []),
                                   f"--level={a.get('level', 'lessons')}", *(["--json"] if a.get("json") else [])]),
         {"plan": PLAN, "view": {"type": "string", "enum": ["text", "md", "mermaid"]},
          "level": {"type": "string", "enum": ["lessons", "units"]}}, readonly=True, structured=True),
    Tool("learn_week", "Last-7-days summary: lessons passed, average score, reviews recalled, active days.",
         cli("week"), readonly=True, structured=True),
    Tool("learn_show", "Print a plan: its JSON (to edit and send back via learn_create with update) or, with md, "
                       "the rendered PLAN.md checklist.",
         cli("show", opts=("plan",), flags=("md",)),
         {"plan": PLAN, "md": {"type": "boolean", "description": "rendered PLAN.md instead of JSON"}}, readonly=True),
    Tool("learn_done", "Record a finished lesson. score = correct / asked (0..1); below the plan's pass bar the lesson becomes "
                       "'retry'. Locked graph lessons are refused unless force is set. Nothing counts until it is recorded.",
         cli("done", opts=("plan", "score", "note"), flags=("accept", "force"), pos=("lesson",)),
         {"lesson": LESSON, "plan": PLAN, "score": {"type": "number", "minimum": 0, "maximum": 1},
          "note": s("one line: what was hard / what to revisit", maxLength=500),
          "accept": {"type": "boolean", "description": "mark done even below the pass bar"},
          "force": {"type": "boolean", "description": "graph plans: record a lesson whose prerequisites are not done"}},
         required=("lesson",), structured=True),
    Tool("learn_skip", "Drop a lesson from the plan's remaining work (it counts as skipped, not done).",
         cli("skip", opts=("plan",), pos=("lesson",)), {"lesson": LESSON, "plan": PLAN}, required=("lesson",)),
    Tool("learn_card_add", "Add 2-4 spaced-repetition review cards for a lesson (atomic, why/how-oriented, answerable in a sentence).",
         cli(["card", "add"], opts=("plan", "lesson"), fixed=("--stdin",), stdin=lambda a: json.dumps(a["cards"], ensure_ascii=False)),
         {"lesson": LESSON, "plan": PLAN,
          "cards": {"type": "array", "minItems": 1, "maxItems": 20,
                    "items": {"type": "object", "required": ["q", "a"], "additionalProperties": False,
                              "properties": {"q": s("question", minLength=1, maxLength=1000),
                                             "a": s("answer", minLength=1, maxLength=2000)}}}},
         required=("lesson", "cards")),
    Tool("learn_card_retire", "Stop reviewing a card.",
         cli(["card", "retire"], opts=("plan",), pos=("card",)), {"card": CARD, "plan": PLAN}, required=("card",)),
    Tool("learn_review_due", "List review cards that are due (or all, with all=true).",
         cli(["review", "due"], opts=("plan", "limit"), flags=("all",)),
         {"plan": PLAN, "all": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
         readonly=True, structured=True),
    Tool("learn_review_grade", "Record the result of a closed-book review: pass or fail. Reschedules the card.",
         cli(["review", "grade"], opts=("plan",), pos=("card", "result")),
         {"card": CARD, "result": {"type": "string", "enum": ["pass", "fail"]}, "plan": PLAN}, required=("card", "result")),
    Tool("learn_create", "Create a plan from a plan object (see the study-plan skill), or replace one with update=true "
                         "while keeping all progress. Validates and returns every problem at once.",
         cli("create", flags=("update",), pos=("slug",), fixed=("--stdin",), stdin=lambda a: json.dumps(a["plan"], ensure_ascii=False)),
         {"slug": s("short id, e.g. python-basics", pattern=SLUG, minLength=1, maxLength=64),
          "plan": {"type": "object", "description": "the plan: title, goal, pace, outcomes, units[lessons]..."},
          "update": {"type": "boolean"}}, required=("slug", "plan")),
    Tool("learn_validate", "Validate an existing plan and print warnings.",
         cli("validate", opts=("plan",)), {"plan": PLAN}, readonly=True),
    Tool("learn_start", "Set the plan's start date (default: today).",
         cli("start", opts=("plan", "date")),
         {"plan": PLAN, "date": s("YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")}, idempotent=True),
    Tool("learn_set_plan_status", "Pause, resume (active) or archive a plan.",
         set_plan_status, {"plan": {**PLAN, "minLength": 1}, "status": {"type": "string", "enum": ["active", "paused", "archived"]}},
         required=("plan", "status"), idempotent=True),
    Tool("learn_profile_get", "Read the learner profile (profile.md): preferences, strengths, what trips them up. "
                              "Read it at the start of a session.",
         cli("profile"), readonly=True),
    Tool("learn_profile_set", "Replace the learner profile with the full new text (read it first, keep what is still true).",
         cli("profile", fixed=("--stdin",), stdin=lambda a: a["content"]),
         {"content": s("complete new profile.md, Markdown", minLength=1, maxLength=65536)}, required=("content",), idempotent=True),
    Tool("learn_config_get", "Read the config (language, timezone, chat_format), or one key.",
         cli("config", pos=("key",)),
         {"key": {"type": "string", "enum": ["language", "timezone", "chat_format"]}}, readonly=True, structured=True),
    Tool("learn_config_set", "Set language (e.g. ru), timezone (IANA, e.g. Europe/Moscow) or chat_format (plain|rich).",
         cli("config", pos=("key", "value")),
         {"key": {"type": "string", "enum": ["language", "timezone", "chat_format"]},
          "value": s("new value", pattern=r"^[A-Za-z0-9_/+][A-Za-z0-9_/+.-]*$", maxLength=64)},
         required=("key", "value"), idempotent=True),
    Tool("learn_nudge", "The daily reminder text, no LLM needed. Empty output means there is nothing to remind about.",
         cli("nudge", opts=("language", "cta"), flags=("markdown",)),
         {"language": s("override the config language", pattern=r"^[A-Za-z-]{2,10}$"),
          "cta": s("what the learner should send to start", maxLength=100),
          "markdown": {"type": "boolean", "description": "rich Markdown for Telegram rich messages"}}, readonly=True),
    Tool("learn_skill", "The teaching method and workflows. Call without a name to list them; with a name to get the full "
                        "instructions: daily-lesson (run a session), study-plan (make / revise a plan), teach (explain well).",
         skill_tool, {"name": s("skill name", maxLength=64)}, readonly=True),
]


# ----------------------------------------------------------------------------
# core: tools + JSON-RPC (MCP)
# ----------------------------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


class Core:
    def __init__(self, home):
        self.home = Path(home).expanduser()
        self.tools = {t.name: t for t in TOOLS}

    def cli(self, argv, stdin=None):
        """Run learn.py against the data directory. learn.py serialises concurrent writers itself."""
        cmd = [sys.executable, str(LEARN_PY), "--home", str(self.home), *argv]
        env = {k: v for k, v in os.environ.items() if k != "LEARN_TOKEN"}  # the child has no use for the secret
        env["PYTHONUTF8"] = "1"  # emoji and Cyrillic survive a C-locale systemd unit
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}  # never inherit our own stdin
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, timeout=CLI_TIMEOUT, **feed)
        except subprocess.TimeoutExpired:
            return Outcome(False, err=f"learn timed out after {CLI_TIMEOUT}s")
        out, err = r.stdout.rstrip("\n"), r.stderr.strip()
        if r.returncode != 0:
            return Outcome(False, err=err or out or f"learn exited with status {r.returncode}")
        return Outcome(True, out, err)

    def call(self, name, args):
        tool = self.tools[name]
        try:
            if not isinstance(args, dict):
                raise ArgError("arguments must be an object")
            check(tool.schema, args)
        except ArgError as e:
            return Outcome(False, err=str(e), kind="args")
        return tool.run(self, args)

    # --- MCP ---

    def rpc(self, msg):
        """One JSON-RPC message or a batch -> response (dict / list) or None (nothing to send)."""
        if isinstance(msg, list):
            if not msg:
                return rpc_error(None, -32600, "empty batch")
            replies = [r for r in (self._rpc_one(m) for m in msg) if r is not None]
            return replies or None
        return self._rpc_one(msg)

    def _rpc_one(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return rpc_error(None, -32600, "invalid request")
        method, mid = msg.get("method"), msg.get("id")
        if method is None:  # a reply to something we never asked
            return None
        notification = "id" not in msg
        try:
            if not isinstance(method, str):
                raise RpcError(-32600, "method must be a string")
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError(-32602, "params must be an object")
            result = self._dispatch(method, params)
        except RpcError as e:
            return None if notification else rpc_error(mid, e.code, str(e))
        except Exception:  # a bug must not take the connection down or leak a traceback to the client
            traceback.print_exc(file=sys.stderr)
            return None if notification else rpc_error(mid, -32603, "internal error")
        return None if notification else {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method, params):
        if method == "initialize":
            wanted = params.get("protocolVersion")
            return {"protocolVersion": wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                    "serverInfo": {"name": "learn", "title": "Learn", "version": VERSION},
                    "instructions": INSTRUCTIONS}
        if method == "ping" or method.startswith("notifications/"):
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t.name, "title": t.title,
                               "description": t.description, "inputSchema": t.schema, "annotations": t.annotations()}
                              for t in self.tools.values()]}
        if method == "tools/call":
            name = params.get("name")
            if name not in self.tools:
                raise RpcError(-32602, f"unknown tool: {name}")
            o = self.call(name, params.get("arguments") or {})
            text = (o.out + ("\n\n" + o.err if o.err else "")) if o.ok else o.err
            return {"content": [{"type": "text", "text": text or "(no output)"}], "isError": not o.ok}
        if method == "prompts/list":
            return {"prompts": [{"name": n, "description": d} for n, (d, _) in load_skills().items()]}
        if method == "prompts/get":
            skills = load_skills()
            name = params.get("name")
            if name not in skills:
                raise RpcError(-32602, f"unknown prompt: {name}")
            return {"description": skills[name][0],
                    "messages": [{"role": "user", "content": {"type": "text", "text": ADAPTER + skills[name][1]}}]}
        raise RpcError(-32601, f"method not found: {method}")


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

def coerce_query(tool, query):
    """GET arguments arrive as strings; convert by the schema and let validation judge the rest."""
    props = tool.schema["properties"]
    args = {}
    for k, vals in query.items():
        v, t = vals[0], props.get(k, {}).get("type")
        try:
            if t == "boolean":
                v = v.lower() in ("1", "true", "yes", "on")
            elif t == "integer":
                v = int(v)
            elif t == "number":
                v = float(v)
        except ValueError:
            pass
        args[k] = v
    return args


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "learn-server/" + VERSION
    sys_version = ""
    timeout = 30  # a stalled client may not hold a thread forever

    def log_message(self, *args):  # we log ourselves, without the token that may be in the URL
        pass

    # --- plumbing ---

    def _send(self, status, body=b"", ctype=None, headers=None):
        self.status = status
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:  # the request body may be unread: don't try to reuse the connection
            self.send_header("Connection", "close")
            self.close_connection = True
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status, obj, headers=None):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", headers)

    def _authorized(self, url_token):
        supplied = url_token
        auth = self.headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            supplied = auth[7:].strip()
        return supplied is not None and hmac.compare_digest(supplied.encode("utf-8"), self.server.token.encode("utf-8"))

    def _body(self):
        """Request body, or None after having answered with an error."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._json(411, {"ok": False, "error": "send a Content-Length (chunked bodies are not supported)"})
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._json(413, {"ok": False, "error": f"body must be at most {MAX_BODY} bytes"})
            return None
        return self.rfile.read(n)

    # --- routing ---

    def _handle(self):
        start = time.monotonic()
        self.status = 0
        url = urlsplit(self.path)
        url_token, route = None, url.path
        if route.startswith("/t/"):
            _, _, url_token, rest = (route.split("/", 3) + [""])[:4]
            route = "/" + rest
        try:
            self._route(route, url_token, parse_qs(url.query))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc(file=sys.stderr)
            if not self.status:
                self._json(500, {"ok": False, "error": "internal error"})
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.client_address[0]} {self.command} {route} "
                         f"{self.status} {int((time.monotonic() - start) * 1000)}ms\n")

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _handle

    def _route(self, route, url_token, query):
        method = self.command
        if route == "/health" and method == "GET":
            return self._json(200, {"ok": True, "name": "learn", "version": VERSION})
        origin = self.headers.get("Origin")  # browsers only; MCP clients and curl send none
        if origin and "*" not in self.server.allowed_origins and origin not in self.server.allowed_origins:
            return self._json(403, {"ok": False, "error": "origin not allowed"})
        if not self._authorized(url_token):
            return self._json(401, {"ok": False, "error": "missing or wrong token"}, {"WWW-Authenticate": "Bearer"})
        core = self.server.core

        if route == "/mcp":
            if method != "POST":  # no server-initiated stream and no sessions: stateless
                return self._json(405, {"ok": False, "error": "use POST"}, {"Allow": "POST"})
            body = self._body()
            if body is None:
                return
            try:
                msg = json.loads(body.decode("utf-8"))
            except ValueError:
                return self._json(400, rpc_error(None, -32700, "parse error"))
            reply = core.rpc(msg)
            return self._send(202) if reply is None else self._json(200, reply)

        if route == "/api/tools" and method == "GET":
            return self._json(200, {"tools": [{"name": t.name, "description": t.description, "readonly": t.readonly,
                                               "inputSchema": t.schema} for t in core.tools.values()]})
        m = re.fullmatch(r"/api/tools/(\w+)", route)
        if not m:
            return self._json(404, {"ok": False, "error": "not found"})
        tool = core.tools.get(m.group(1))
        if tool is None:
            return self._json(404, {"ok": False, "error": f"unknown tool: {m.group(1)}"})
        raw = False
        if method == "GET":
            if not tool.readonly:
                return self._json(405, {"ok": False, "error": "this tool changes data: use POST"}, {"Allow": "POST"})
            raw = query.pop("raw", [""])[0] in ("1", "true")
            args = coerce_query(tool, query)
        elif method == "POST":
            body = self._body()
            if body is None:
                return
            try:
                args = json.loads(body.decode("utf-8")) if body.strip() else {}
            except ValueError:
                return self._json(400, {"ok": False, "error": "body must be a JSON object"})
        else:
            return self._json(405, {"ok": False, "error": "use GET or POST"}, {"Allow": "GET, POST"})
        o = core.call(tool.name, args)
        if not o.ok:
            return self._json(400 if o.kind == "args" else 422, {"ok": False, "error": o.err})
        if raw:
            return self._send(200, o.out.encode("utf-8"), "text/plain; charset=utf-8")
        resp = {"ok": True, "output": o.out}
        if o.err:
            resp["warnings"] = o.err
        if args.get("json"):
            try:
                resp["data"] = json.loads(o.out)
            except ValueError:
                pass
        self._json(200, resp)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, core, token, allowed_origins=()):
        super().__init__(addr, Handler)
        self.core, self.token, self.allowed_origins = core, token, set(allowed_origins)


# ----------------------------------------------------------------------------
# entry points
# ----------------------------------------------------------------------------

def serve_stdio(core):
    """MCP over stdin/stdout, one JSON message per line (the client starts us, e.g. through ssh)."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = rpc_error(None, -32700, "parse error")
        else:
            reply = core.rpc(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")  # ASCII-only: no stray line separators
            sys.stdout.flush()
    return 0


def read_token(args):
    token = os.environ.get("LEARN_TOKEN", "")
    if args.token_file:
        try:
            token = Path(args.token_file).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            sys.exit(f"cannot read --token-file: {e}")
    token = token.strip()
    if len(token) < MIN_TOKEN:
        sys.exit(f"error: set LEARN_TOKEN (or --token-file) to a secret of at least {MIN_TOKEN} characters, e.g.\n"
                 "  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'")
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
        sys.exit("error: the token may only contain letters, digits and . _ ~ - (it can appear in a URL)")
    return token


def main(argv=None):
    ap = argparse.ArgumentParser(prog="learn-server", description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", help="data directory (default: $LEARN_HOME or ~/learning)")
    ap.add_argument("--host", default=os.environ.get("LEARN_HOST", "127.0.0.1"),
                    help="bind address (default 127.0.0.1: let a TLS proxy face the internet)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEARN_PORT", "8787")))
    ap.add_argument("--token-file", help="file holding the access token (default: $LEARN_TOKEN)")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="browser Origin allowed to call the API (default: none; '*' = any). MCP clients send no Origin")
    ap.add_argument("--stdio", action="store_true", help="speak MCP on stdin/stdout instead of HTTP (no token needed)")
    args = ap.parse_args(argv)

    core = Core(args.home or os.environ.get("LEARN_HOME") or "~/learning")
    if not core.home.exists():
        print(f"warning: {core.home} does not exist yet — run `learn init --language ru --timezone ...` first", file=sys.stderr)
    if args.stdio:
        return serve_stdio(core)

    server = Server((args.host, args.port), core, read_token(args), args.allow_origin)
    host, port = server.server_address[:2]
    print(f"learn-server {VERSION}: http://{host}:{port}  data={core.home}  tools={len(core.tools)}", file=sys.stderr, flush=True)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print("warning: listening on a non-loopback address over plain HTTP — put TLS in front (docs/remote-server.md)",
              file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
