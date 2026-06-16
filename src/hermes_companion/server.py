#!/usr/bin/env python3
"""Companion Daemon — HTTP shim for Hermes API + Kanban CLI on port 8777.

Provides:
  - HTTP Basic auth (credentials from auth.json)
  - Hermes API session passthrough (/api/sessions/*)
  - Kanban CLI wrapper (/api/kanban/*)
  - Health endpoint (/healthz)
"""

import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

from aiohttp import web, ClientSession, ClientTimeout

from .config import load_config, detect_hermes_binary, validate_config

# ── Config ──────────────────────────────────────────────────
_config = load_config()

HOST = _config["server"]["host"]
PORT = _config["server"]["port"]
HERMES_API = _config["hermes"]["api_url"]
API_KEY = _config["hermes"]["api_key"]
HERMES_BIN = detect_hermes_binary(_config["hermes"]["binary_path"])
AUTH_FILE = Path(_config["auth"]["file_path"])
ATTACHMENTS_DIR = Path(_config["attachments"]["dir"])

# Validate on import
_errors = validate_config(_config)
if _errors:
    for e in _errors:
        print(f"[FATAL] {e}", file=sys.stderr)
    sys.exit(1)

# Require API key
if not API_KEY:
    print("[FATAL] API_SERVER_KEY / HERMES_API_KEY not set", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [companion] %(levelname)s %(message)s")
logger = logging.getLogger("companion")

STARTED_AT = time.monotonic()


# ── Auth ────────────────────────────────────────────────────
class BasicAuth:
    def __init__(self, auth_file: Path):
        self._file = auth_file
        self._users: dict = {}
        self._mtime: float = 0.0
        self._reload()

    def _reload(self):
        try:
            if self._file.exists():
                mtime = self._file.stat().st_mtime
                if mtime != self._mtime:
                    raw = json.loads(self._file.read_text())
                    self._users = raw.get("users", {})
                    self._mtime = mtime
        except Exception as e:
            logger.error("Failed to load auth.json: %s", e)

    async def check(self, request: web.Request) -> bool:
        self._reload()
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            return False
        user = self._users.get(username)
        if not user:
            return False
        phash = user.get("password_hash", "")
        if not phash.startswith("scrypt$"):
            return phash == password
        try:
            _, n, r, p, salt_hex, expected = phash.split("$", 5)
            n, r, p = int(n), int(r), int(p)
            salt_bytes = bytes.fromhex(salt_hex)
            hash_bytes = hashlib.scrypt(
                password.encode(), salt=salt_bytes, n=n, r=r, p=p, dklen=32,
            )
            return base64.b64encode(hash_bytes).decode() == expected
        except Exception:
            return False

    @web.middleware
    async def middleware(self, request, handler):
        if request.path in ("/healthz", "/health"):
            return await handler(request)
        if not await self.check(request):
            return web.json_response(
                {"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials"}},
                status=401,
            )
        return await handler(request)


# ── Hermes API Proxy ─────────────────────────────────────────
class HermesProxy:
    _session: ClientSession | None = None

    @classmethod
    async def get_session(cls) -> ClientSession:
        if cls._session is None:
            cls._session = ClientSession(timeout=ClientTimeout(total=300, connect=10))
        return cls._session

    @classmethod
    async def forward(cls, request: web.Request, path: str) -> web.Response:
        session = await cls.get_session()
        url = f"{HERMES_API}{path}"
        if request.query_string:
            url += f"?{request.query_string}"
        headers = dict(request.headers)
        headers.pop("Host", None)
        headers.pop("Authorization", None)
        headers["Authorization"] = f"Bearer {API_KEY}"
        body = await request.read()
        try:
            upstream = await session.request(
                request.method, url, headers=headers, data=body or None,
            )
            data = await upstream.read()
            ct = upstream.headers.get("Content-Type", "application/json")
            if ";" in ct:
                ct = ct.split(";")[0].strip()
            return web.Response(body=data, status=upstream.status, content_type=ct)
        except Exception as e:
            logger.error("Hermes API error: %s", e)
            return web.json_response(
                {"error": {"code": "HERMES_DOWN", "message": "Hermes API unreachable"}},
                status=503,
            )


# ── Kanban CLI Wrapper ───────────────────────────────────────
def _kanban(args: list[str], board: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    cmd = [HERMES_BIN, "kanban"]
    cmd.extend(args)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "HERMES_KANBAN_BOARD": board or ""},
        )
        return (r.returncode, r.stdout.strip(), r.stderr.strip())
    except subprocess.TimeoutExpired:
        return (-1, "", "subprocess timed out")
    except FileNotFoundError:
        return (-1, "", f"hermes binary not found at {HERMES_BIN}")


# ── Route Handlers ───────────────────────────────────────────
async def handle_healthz(request: web.Request) -> web.Response:
    uptime = round(time.monotonic() - STARTED_AT)
    # Check Hermes API reachability
    ok = False
    try:
        session = await HermesProxy.get_session()
        async with session.get(f"{HERMES_API}/health", timeout=ClientTimeout(total=5)) as resp:
            ok = resp.status == 200
    except Exception:
        pass
    return web.json_response({
        "status": "ok" if ok else "degraded",
        "uptime": uptime,
        "hermes_api_reachable": ok,
    })


# Session passthrough
async def handle_sessions_list(request: web.Request) -> web.Response:
    return await HermesProxy.forward(request, "/api/sessions")


async def handle_session_create(request: web.Request) -> web.Response:
    resp = await HermesProxy.forward(request, "/api/sessions")
    # Normalize: Hermes returns {"session": {...}} on 201
    if resp.status == 201 and resp.content_type == "application/json":
        try:
            raw = resp.body
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            if "session" in data:
                data["data"] = [data.pop("session")]
            return web.json_response(data, status=201)
        except Exception:
            pass
    return resp


async def handle_session_detail(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    return await HermesProxy.forward(request, f"/api/sessions/{sid}")


async def handle_session_messages(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    return await HermesProxy.forward(request, f"/api/sessions/{sid}/messages")


# Kanban handlers
async def handle_kanban_boards(request: web.Request) -> web.Response:
    code, out, err = _kanban(["boards", "list", "--json"])
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to list boards"}},
            status=500,
        )
    try:
        return web.json_response(json.loads(out))
    except json.JSONDecodeError:
        return web.json_response({"boards": []})


async def handle_kanban_tasks_list(request: web.Request) -> web.Response:
    board = request.query.get("board", "")
    if not board:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "?board= required"}},
            status=422,
        )
    args = ["list", "--json"]
    status_filter = request.query.get("status")
    if status_filter:
        args.extend(["--status", status_filter])
    assignee = request.query.get("assignee")
    if assignee:
        args.extend(["--assignee", assignee])
    code, out, err = _kanban(args, board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to list tasks"}},
            status=500,
        )
    try:
        return web.json_response(json.loads(out))
    except json.JSONDecodeError:
        return web.json_response([])


async def handle_kanban_task_show(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, out, err = _kanban(["show", "--json", task_id], board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": err or "Task not found"}},
            status=404,
        )
    try:
        return web.json_response(json.loads(out))
    except json.JSONDecodeError:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": "parse error"}}, status=500,
        )


async def handle_kanban_task_complete(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    args = ["complete", task_id]
    if body.get("result"):
        args.extend(["--result", body["result"]])
    if body.get("summary"):
        args.extend(["--summary", body["summary"]])
    code, _, err = _kanban(args, board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to complete task"}},
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "done"})


async def handle_kanban_task_comment(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    body = await request.json()
    if not body.get("text"):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "text required"}},
            status=422,
        )
    author = request.query.get("author", body.get("author", "user"))
    code, _, err = _kanban(["comment", task_id, body["text"], "--author", author], board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to comment"}},
            status=500,
        )
    return web.json_response({"ok": True})


# ── App Setup ────────────────────────────────────────────────
async def create_app() -> web.Application:
    auth = BasicAuth(AUTH_FILE)
    app = web.Application(middlewares=[auth.middleware])

    # Health
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/health", handle_healthz)

    # Session passthrough
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_post("/api/sessions", handle_session_create)
    app.router.add_get("/api/sessions/{session_id}", handle_session_detail)
    app.router.add_get("/api/sessions/{session_id}/messages", handle_session_messages)

    # Kanban
    app.router.add_get("/api/kanban/boards", handle_kanban_boards)
    app.router.add_get("/api/kanban/tasks", handle_kanban_tasks_list)
    app.router.add_get("/api/kanban/tasks/{task_id}", handle_kanban_task_show)
    app.router.add_post("/api/kanban/tasks/{task_id}/complete", handle_kanban_task_complete)
    app.router.add_post("/api/kanban/tasks/{task_id}/comment", handle_kanban_task_comment)

    return app


def check_first_run() -> bool:
    """Check if config.yaml exists. If not, print first-run message and return True.

    Returns True if this is a first run (config missing), False if config exists.
    """
    from .config import find_config_path

    config_path = find_config_path()
    if config_path is None:
        print()
        print("=" * 60)
        print("  First run detected!")
        print("=" * 60)
        print()
        print("  No config.yaml found. Run the setup wizard to configure:")
        print()
        print("    hermes-companion setup")
        print()
        print("  For non-interactive setup:")
        print()
        print("    hermes-companion setup --non-interactive")
        print()
        return True
    return False


def main():
    if check_first_run():
        sys.exit(2)

    app = create_app()
    logger.info("Companion daemon starting on %s:%d", HOST, PORT)
    logger.info("Using Hermes binary: %s", HERMES_BIN)
    logger.info("Auth file: %s", AUTH_FILE)
    web.run_app(app, host=HOST, port=PORT, print=logger.info)


if __name__ == "__main__":
    main()
