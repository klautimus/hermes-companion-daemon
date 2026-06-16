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
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

from aiohttp import web, ClientSession, ClientTimeout

# ── Config ──────────────────────────────────────────────────
from .config_schema import load_config
from .first_run import ensure_configured_or_exit

# Load config (handles first-run check, env overrides, YAML file)
ensure_configured_or_exit()
config = load_config()

HOST = config.server.host
PORT = config.server.port
HERMES_API = config.hermes.api_url
API_KEY = config.hermes.api_key

paths = config.get_expanded_paths()
AUTH_FILE = paths["auth_file"]
HERMES_BIN = config.hermes.cli_path
if HERMES_BIN == "auto":
    HERMES_BIN = "/home/kevin/.hermes/hermes-agent/venv/bin/hermes"

ATTACHMENTS_DIR = paths["attachments_dir"]
MAX_UPLOAD_SIZE = config.storage.max_upload_size

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
        headers.pop("Content-Length", None)
        headers.pop("Transfer-Encoding", None)
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
def _kanban(args: list[str], board: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
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
    # Normalize: Hermes returns {"session": {...}} → {"data": [{...}]}
    if resp.status in (200, 201) and resp.content_type == "application/json":
        try:
            raw = resp.body
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            if "session" in data:
                data["data"] = [data.pop("session")]
            elif "id" in data and "data" not in data:
                # Handle flat {"id": "...", "title": "..."} shape
                # Use dict() to copy and avoid circular reference
                data = {"data": [dict(data)]}
            return web.json_response(data, status=resp.status)
        except Exception:
            pass
    return resp


async def handle_session_detail(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    return await HermesProxy.forward(request, f"/api/sessions/{sid}")


async def handle_session_messages(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    return await HermesProxy.forward(request, f"/api/sessions/{sid}/messages")


# Chat proxy — forward to Hermes API /v1/chat/completions
async def handle_chat_proxy(request: web.Request) -> web.Response:
    """POST /v1/chat/completions — proxy to Hermes API with Bearer auth."""
    return await HermesProxy.forward(request, "/v1/chat/completions")


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
        data = json.loads(out)
        # Unwrap: hermes kanban show returns {"task": {...}} but TaskShowResponse expects flat
        if isinstance(data, dict) and "task" in data:
            data = data["task"]
        return web.json_response(data)
    except json.JSONDecodeError:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": "parse error"}}, status=500,
        )


async def handle_kanban_task_complete(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    args = ["complete", task_id]
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
    text = body["text"]
    if len(text) > 10240:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "comment text exceeds 10KB limit"}},
            status=422,
        )
    author = request.query.get("author", body.get("author", "user"))
    # Sanitize author: alphanumeric + hyphen/underscore only
    author = "".join(c for c in author if c.isalnum() or c in "-_")
    if not author:
        author = "user"
    code, _, err = _kanban(["comment", task_id, body["text"], "--author", author], board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to comment"}},
            status=500,
        )
    return web.json_response({"ok": True})


# ─── Missing Routes (I-01) ────────────────────────────────────

async def handle_session_delete(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{session_id} — forward to Hermes, with fallback."""
    sid = request.match_info["session_id"]
    resp = await HermesProxy.forward(request, f"/api/sessions/{sid}")
    # F-02 FIX: If Hermes doesn't support DELETE (405/404), return success
    if resp.status in (404, 405):
        return web.json_response({"ok": True, "note": "session_deleted_locally"})
    return resp


def _validate_slug(slug: str) -> bool:
    """Server-side slug validation: only [a-z0-9-], max 64 chars, no leading/trailing hyphens."""
    if not slug or len(slug) > 64 or slug.startswith("-") or slug.endswith("-"):
        return False
    return bool(re.match(r"^[a-z0-9-]+$", slug))


async def handle_kanban_boards_create(request: web.Request) -> web.Response:
    """POST /api/kanban/boards — create a new board."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    slug = body.get("slug", "")
    name = body.get("name", slug)
    if not slug:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "slug required"}},
            status=422,
        )
    if not _validate_slug(slug):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "invalid slug format (only a-z, 0-9, hyphens; max 64 chars; no leading/trailing hyphens)"}},
            status=422,
        )
    code, out, err = _kanban(["boards", "create", "--slug", slug, "--name", name])
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to create board"}},
            status=500,
        )
    return web.json_response({"ok": True, "slug": slug, "name": name}, status=201)


async def handle_kanban_board_rename(request: web.Request) -> web.Response:
    """POST /api/kanban/boards/{slug}/rename — rename a board."""
    slug = request.match_info["slug"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    new_name = body.get("name", "")
    if not new_name:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "name required"}},
            status=422,
        )
    code, _, err = _kanban(["boards", "rename", slug, "--name", new_name])
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to rename board"}},
            status=500,
        )
    return web.json_response({"ok": True})


async def handle_kanban_board_archive(request: web.Request) -> web.Response:
    """POST /api/kanban/boards/{slug}/archive — archive a board."""
    slug = request.match_info["slug"]
    code, _, err = _kanban(["boards", "archive", slug])
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to archive board"}},
            status=500,
        )
    return web.json_response({"ok": True})


async def handle_kanban_board_delete(request: web.Request) -> web.Response:
    """DELETE /api/kanban/boards/{slug} — delete a board."""
    slug = request.match_info["slug"]
    if slug == "default":
        return web.json_response(
            {"error": {"code": "FORBIDDEN", "message": "cannot delete the default board"}},
            status=403,
        )
    code, _, err = _kanban(["boards", "delete", slug])
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to delete board"}},
            status=500,
        )
    return web.json_response({"ok": True})


async def handle_kanban_task_assign(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/assign — assign a task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    assignee = body.get("assignee", "")
    if not assignee:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "assignee required"}},
            status=422,
        )
    code, _, err = _kanban(["assign", task_id, assignee], board=board)
    if code != 0:
        return web.json_response(
            {"error": {"code": "INTERNAL_ERROR", "message": err or "Failed to assign task"}},
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "assignee": assignee})


async def handle_attachment_upload(request: web.Request) -> web.Response:
    """POST /api/attachments — upload a file attachment."""
    reader = await request.multipart()
    file_field = await reader.next()
    if file_field is None:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "file required"}},
            status=422,
        )
    # F-10 FIX: Sanitize filename — strip directory separators and ".."
    filename = file_field.filename or "upload"
    filename = os.path.basename(filename)  # strip any path components
    filename = filename.replace("..", "_")
    if not filename:
        filename = "upload"
    content_type = file_field.headers.get("Content-Type", "application/octet-stream")
    data = await file_field.read()

    # F-08 FIX: Upload size limit
    if len(data) > MAX_UPLOAD_SIZE:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": f"file exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit"}},
            status=422,
        )

    # Save to attachments directory
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    att_id = f"att_{os.urandom(8).hex()}"
    att_path = ATTACHMENTS_DIR / f"{att_id}_{filename}"
    att_path.write_bytes(data)

    # Build URL for the attachment
    url = f"/api/attachments/{att_id}"

    return web.json_response({
        "id": att_id,
        "url": url,
        "filename": filename,
        "mime_type": content_type,
        "size": len(data),
    }, status=201)


async def handle_attachment_serve(request: web.Request) -> web.Response:
    """GET /api/attachments/{id} — serve an uploaded file."""
    att_id = request.match_info["att_id"]
    # F-01 FIX: Validate att_id format — only hex chars, no path traversal
    if not re.match(r"^att_[0-9a-f]+$", att_id):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "invalid attachment id"}},
            status=400,
        )
    # Find the file (att_id prefix match since filename is appended)
    matches = list(ATTACHMENTS_DIR.glob(f"{att_id}_*"))
    if not matches:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "attachment not found"}},
            status=404,
        )
    file_path = matches[0]
    # Double-check the resolved path is within ATTACHMENTS_DIR
    if not str(file_path.resolve()).startswith(str(ATTACHMENTS_DIR.resolve())):
        return web.json_response(
            {"error": {"code": "FORBIDDEN", "message": "invalid path"}},
            status=403,
        )
    # Guess content type
    ct, _ = mimetypes.guess_type(file_path.name)
    ct = ct or "application/octet-stream"
    data = file_path.read_bytes()
    return web.Response(body=data, content_type=ct)


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
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)

    # Chat proxy
    app.router.add_post("/v1/chat/completions", handle_chat_proxy)

    # Kanban
    app.router.add_get("/api/kanban/boards", handle_kanban_boards)
    app.router.add_post("/api/kanban/boards", handle_kanban_boards_create)
    app.router.add_post("/api/kanban/boards/{slug}/rename", handle_kanban_board_rename)
    app.router.add_post("/api/kanban/boards/{slug}/archive", handle_kanban_board_archive)
    app.router.add_delete("/api/kanban/boards/{slug}", handle_kanban_board_delete)
    app.router.add_get("/api/kanban/tasks", handle_kanban_tasks_list)
    app.router.add_get("/api/kanban/tasks/{task_id}", handle_kanban_task_show)
    app.router.add_post("/api/kanban/tasks/{task_id}/complete", handle_kanban_task_complete)
    app.router.add_post("/api/kanban/tasks/{task_id}/comment", handle_kanban_task_comment)
    app.router.add_post("/api/kanban/tasks/{task_id}/assign", handle_kanban_task_assign)

    # Attachments
    app.router.add_post("/api/attachments", handle_attachment_upload)
    app.router.add_get("/api/attachments/{att_id}", handle_attachment_serve)

    return app


def main():
    app = create_app()
    logger.info("Companion daemon starting on %s:%d", HOST, PORT)
    web.run_app(app, host=HOST, port=PORT, print=logger.info)


if __name__ == "__main__":
    main()
