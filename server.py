#!/usr/bin/env python3
"""Companion Daemon — HTTP shim for Hermes API + Kanban CLI on port 8777.

Provides:
  - HTTP Basic auth (credentials from auth.json)
  - Hermes API session passthrough (/api/sessions/*)
  - Kanban CLI wrapper (/api/kanban/*)
  - Health endpoint (/healthz)
"""

import shutil

import asyncio
import atexit
import base64
import hashlib
import hmac
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
from config_schema import load_config
from first_run import ensure_configured_or_exit

# Load config (handles first-run check, env overrides, YAML file)
ensure_configured_or_exit()
config = load_config()

# Fail fast if api_key is a placeholder
PLACEHOLDER_KEYS = {"test-key", "changeme", "your-key-here", "TODO", "REPLACE_ME", ""}
if config.hermes.api_key in PLACEHOLDER_KEYS:
    print(
        f"FATAL: hermes.api_key in config.yaml looks like a placeholder "
        f"('{config.hermes.api_key}'). Set it to the real API_SERVER_KEY "
        f"from ~/.hermes/.env, or unset the config field to auto-load from "
        f"the env var.",
        file=sys.stderr,
    )
    sys.exit(2)

HOST = config.server.host
PORT = config.server.port
HERMES_API = config.hermes.api_url
API_KEY = config.hermes.api_key

paths = config.get_expanded_paths()
AUTH_FILE = paths["auth_file"]
HERMES_BIN = config.hermes.cli_path
if HERMES_BIN == "auto":
    # shutil.which fails under systemd (minimal PATH). Check known locations as fallback.
    HERMES_BIN = (
        shutil.which("hermes")
        or os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")
        or os.path.expanduser("~/.local/bin/hermes")
        or "hermes"
    )

ATTACHMENTS_DIR = paths["attachments_dir"]
MAX_UPLOAD_SIZE = config.storage.max_upload_size

logging.basicConfig(level=logging.INFO, format="%(asctime)s [companion] %(levelname)s %(message)s")
logger = logging.getLogger("companion")

STARTED_AT = time.monotonic()

# ── Attachment→Session Mapping ────────────────────────────────
# Stores attachment IDs keyed by session_id for injection into message history.
_attachment_store: dict[str, list[str]] = {}

# ── Setup Token Store ─────────────────────────────────────────
# One-time setup tokens. Each entry: {"username": str, "password": str, "board": str, "expires_at": float}
_SETUP_TOKENS: dict[str, dict] = {}
_setup_tokens_lock: asyncio.Lock = asyncio.Lock()

# ── Setup Token Redeem Rate Limiting ──────────────────────────
_SETUP_REDEEM_FAILURES: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)
_SETUP_REDEEM_LOCKOUT_THRESHOLD = 10
_SETUP_REDEEM_LOCKOUT_DURATION = 60  # seconds


async def register_setup_token(token: str, username: str, password: str, board: str = "default", ttl_seconds: int = 300, host: str = "127.0.0.1", port: int = 8777):
    async with _setup_tokens_lock:
        _SETUP_TOKENS[token] = {
            "username": username,
            "password": password,
            "board": board,
            "host": host,
            "port": port,
            "expires_at": time.time() + ttl_seconds,
        }


def _load_setup_tokens_from_disk():
    """Load setup tokens from a file written by the setup wizard, then delete the file.

    NOTE: This is a sync function and cannot acquire _setup_tokens_lock.
    It only runs at startup before the server accepts requests, so the
    race window is acceptable. If this is ever called after the server
    is listening, a lock would be needed.
    """
    try:
        paths = config.get_expanded_paths()
        config_path = paths["config_dir"]
        token_file = config_path / "setup_token.json"
        if not token_file.exists():
            return
        raw = json.loads(token_file.read_text())
        for entry in raw.get("tokens", []):
            try:
                created = datetime.fromisoformat(entry["created_at"]).timestamp()
                expires_at = created + 300
                # Load all tokens including expired ones (expired ones will have expires_at in the past)
                # This ensures they return 410 EXPIRED instead of 404 NOT_FOUND
                _SETUP_TOKENS[entry["token"]] = {
                    "username": entry["username"],
                    "password": entry["password"],
                    "board": entry.get("board", "default"),
                    "host": entry.get("host", "127.0.0.1"),
                    "port": entry.get("port", 8777),
                    "expires_at": expires_at,
                }
            except Exception as e:
                logger.warning("Skipping malformed token entry: %s", e)
        # After loading, delete the file (tokens are single-use and ephemeral)
        token_file.unlink()
    except Exception as e:
        logger.warning("Failed to load setup_tokens.json: %s", e)


def _sanitized_error_response(err: str, code: str, fallback: str, request_id: str = None) -> dict:
    """Log full stderr server-side, return generic fallback + request_id to client.

    Prevents leaking internal paths, stack traces, or subprocess output to
    API clients while preserving full diagnostics in server logs.
    """
    logger.error("Subprocess error [%s] (request_id=%s): %s", code, request_id, err)
    msg = fallback
    if request_id:
        msg += f" (request_id: {request_id})"
    return {"error": {"code": code, "message": msg}}


# ── Auth ────────────────────────────────────────────────────
class BasicAuth:
    def __init__(self, auth_file: Path):
        self._file = auth_file
        self._users: dict = {}
        self._mtime: float = 0.0
        # Brute-force tracking: key = (username, client_ip) -> (fail_count, locked_until)
        self._failures: dict = {}
        self._max_failures: int = 5
        self._lockout_seconds: int = 60
        # Per-username lockout (defense in depth: IP rotation bypasses per-IP tracking)
        self._user_failures: dict = {}  # username -> (fail_count, locked_until)
        self._user_lockout_threshold: int = 5
        self._user_lockout_seconds: int = 300  # 5 min
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

    def _record_failure(self, key):
        count, until = self._failures.get(key, (0, 0.0))
        count += 1
        if count >= self._max_failures:
            until = time.time() + self._lockout_seconds
        self._failures[key] = (count, until)

    def _clear_failures(self, key):
        self._failures.pop(key, None)

    def _record_user_failure(self, username: str):
        count, until = self._user_failures.get(username, (0, 0.0))
        count += 1
        if count >= self._user_lockout_threshold:
            until = time.time() + self._user_lockout_seconds
        self._user_failures[username] = (count, until)

    def _clear_user_failures(self, username: str):
        self._user_failures.pop(username, None)

    async def check(self, request: web.Request) -> bool:
        self._reload()
        client_ip = request.remote or "unknown"
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            return False

        # Lockout check (per username + IP)
        key = (username, client_ip)
        fail = self._failures.get(key)
        if fail and time.time() < fail[1]:
            return False

        # Per-username lockout check (defense in depth against IP rotation)
        user_fail = self._user_failures.get(username)
        if user_fail and time.time() < user_fail[1]:
            return False

        # Note: scrypt N=16384 is used throughout for compatibility. The setup
        # wizard, daemon, and test fixtures all use the same N value. Upgrading
        # to N=131072 (OWASP 2023 minimum) would require ~128MB of memory per
        # verify call, which exceeds the system limit on this build.
        user = self._users.get(username)
        if not user:
            # Equalize timing: dummy scrypt so timing doesn't reveal username existence.
            # Uses N=16384 to match the system's memory constraint (see comment above).
            try:
                hashlib.scrypt(
                    password.encode(), salt=b"\x00" * 16, n=16384, r=8, p=1, dklen=32,
                )
            except Exception:
                pass
            self._record_failure(key)
            self._record_user_failure(username)
            return False

        phash = user.get("password_hash", "")
        password_ok = False
        if not phash.startswith("scrypt$"):
            # Plaintext fallback — constant-time compare
            password_ok = hmac.compare_digest(phash, password)
        else:
            try:
                _, n, r, p, salt_hex, expected = phash.split("$", 5)
                n, r, p = int(n), int(r), int(p)
                salt_bytes = bytes.fromhex(salt_hex)
                hash_bytes = hashlib.scrypt(
                    password.encode(), salt=salt_bytes, n=n, r=r, p=p, dklen=32,
                )
                computed = base64.b64encode(hash_bytes).decode()
                password_ok = hmac.compare_digest(computed, expected)
            except Exception:
                password_ok = False

        if not password_ok:
            self._record_failure(key)
            self._record_user_failure(username)
            return False

        # Password is correct — check if 2FA is enabled
        self._clear_failures(key)
        self._clear_user_failures(username)

        if user.get("two_factor_enabled"):
            # Generate a 2FA challenge and return it
            try:
                from email_2fa import generate_challenge, send_otp

                email = user.get("email", username)
                challenge_id = generate_challenge(email)
                send_otp(challenge_id)
            except Exception as e:
                logger.error("2FA challenge generation failed for %s: %s", username, e)
                return {"error": "2FA_SYSTEM_ERROR", "message": "Two-factor authentication is enabled but the email system failed."}
            return {"requires_2fa": True, "challenge_id": challenge_id}

        return True

    @web.middleware
    async def middleware(self, request, handler):
        if request.path in (
            "/healthz", "/health", "/api/setup/redeem", "/api/setup/register",
            "/api/auth/2fa/verify", "/api/auth/2fa/setup",
            "/api/auth/2fa/disable", "/api/auth/2fa/resend",
        ):
            return await handler(request)
        result = await self.check(request)
        if result is False:
            return web.json_response(
                {"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials"}},
                status=401,
            )
        if isinstance(result, dict) and result.get("requires_2fa"):
            return web.json_response(result, status=200)
        if isinstance(result, dict) and result.get("error"):
            return web.json_response(result, status=503)
        return await handler(request)


# ── Security Headers Middleware ───────────────────────────────
@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response: web.StreamResponse = await handler(request)
    # Skip headers for streaming/SSE responses (already set by handler)
    if not isinstance(response, web.Response):
        return response
    # Set headers only if not already set by the handler
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
    if "X-Frame-Options" not in response.headers:
        response.headers["X-Frame-Options"] = "DENY"
    if "X-Content-Type-Options" not in response.headers:
        response.headers["X-Content-Type-Options"] = "nosniff"
    if "Referrer-Policy" not in response.headers:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "Permissions-Policy" not in response.headers:
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


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


async def _forward_chat(request: web.Request, body: bytes | None) -> web.Response:
    """Forward a chat request to Hermes API with the given body.

    Like HermesProxy.forward but accepts a pre-read body so the original
    request body stream doesn't need to be re-read.
    """
    session = await HermesProxy.get_session()
    url = f"{HERMES_API}/v1/chat/completions"
    if request.query_string:
        url += f"?{request.query_string}"
    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("Authorization", None)
    headers.pop("Content-Length", None)
    headers.pop("Transfer-Encoding", None)
    headers["Authorization"] = f"Bearer {API_KEY}"
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


@atexit.register
def _close_hermes_proxy_session():
    """Close the HermesProxy session on daemon shutdown."""
    if HermesProxy._session is not None and not HermesProxy._session.closed:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(HermesProxy._session.close())
            loop.close()
        except Exception as e:
            logger.warning("Failed to close HermesProxy session: %s", e)


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
    resp = await HermesProxy.forward(request, f"/api/sessions/{sid}/messages")
    # Inject attachment URLs for sessions that have attachments
    if sid in _attachment_store and resp.status == 200:
        try:
            body_bytes = resp.body
            if body_bytes is None:
                return resp
            if isinstance(body_bytes, (bytearray, memoryview)):
                body_bytes = bytes(body_bytes)
            if not isinstance(body_bytes, bytes):
                return resp
            data = json.loads(body_bytes.decode("utf-8"))
            atts = _attachment_store.get(sid, [])
            if atts:
                # Inject attachment_url into the last user message
                if "data" in data and isinstance(data["data"], list):
                    for msg in reversed(data["data"]):
                        if msg.get("role") == "user":
                            att_id = atts[0]
                            msg["attachment_url"] = f"/api/attachments/{att_id}"
                            break
                # Also expose full attachments list at top level
                data["attachments"] = [
                    {"id": aid, "url": f"/api/attachments/{aid}"}
                    for aid in atts
                ]
            return web.json_response(data, status=resp.status)
        except Exception:
            pass
    return resp


# Chat proxy — forward to Hermes API /v1/chat/completions
async def handle_chat_proxy(request: web.Request) -> web.Response:
    """POST /v1/chat/completions — proxy to Hermes API with Bearer auth.

    If the request body includes 'attachment_ids' and 'session_id',
    stores the attachment→session mapping for later injection into
    message history. Those fields are stripped before forwarding to Hermes.
    """
    body = await request.read()
    modified_body = body
    if body:
        try:
            payload = json.loads(body)
            session_id = payload.pop("session_id", None)
            att_ids = payload.pop("attachment_ids", None)
            if session_id and att_ids:
                _attachment_store.setdefault(session_id, []).extend(att_ids)
            modified_body = json.dumps(payload).encode("utf-8")
        except (json.JSONDecodeError, ValueError):
            pass
    return await _forward_chat(request, modified_body)


# Chat streaming proxy — SSE pipe to Hermes API
async def handle_chat_stream(request: web.Request) -> web.StreamResponse:
    """POST /v1/chat/completions/stream — stream chat via SSE.

    Forwards to Hermes API with ``"stream": true`` and pipes SSE chunks
    back to the client as they arrive.  ``attachment_ids`` and
    ``session_id`` are handled identically to ``handle_chat_proxy``.
    """
    body = await request.read()
    modified_body = body
    if body:
        try:
            payload = json.loads(body)
            session_id = payload.pop("session_id", None)
            att_ids = payload.pop("attachment_ids", None)
            if session_id and att_ids:
                _attachment_store.setdefault(session_id, []).extend(att_ids)
            # Force streaming on the upstream request
            payload["stream"] = True
            modified_body = json.dumps(payload).encode("utf-8")
        except (json.JSONDecodeError, ValueError):
            pass

    session = await HermesProxy.get_session()
    url = f"{HERMES_API}/v1/chat/completions"
    if request.query_string:
        url += f"?{request.query_string}"
    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("Authorization", None)
    headers.pop("Content-Length", None)
    headers.pop("Transfer-Encoding", None)
    headers["Authorization"] = f"Bearer {API_KEY}"
    headers["Accept"] = "text/event-stream"

    resp = web.StreamResponse(status=200, reason="OK")
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    try:
        upstream = await session.request(
            request.method, url, headers=headers, data=modified_body or None,
        )
        if upstream.status != 200:
            err_body = await upstream.read()
            err_msg = err_body.decode("utf-8", errors="replace")
            err_json = json.dumps({"error": {"code": "UPSTREAM_ERROR", "message": err_msg}})
            await resp.write(f"data: {err_json}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp

        async for chunk in upstream.content:
            if chunk:
                await resp.write(chunk)
                await resp.drain()
    except Exception as e:
        logger.error("Chat stream error: %s", e)
        try:
            await resp.write(
                f"data: {json.dumps({'error': {'code': 'STREAM_ERROR', 'message': str(e)}})}\n\n".encode()
            )
        except Exception:
            pass
    finally:
        await resp.write(b"data: [DONE]\n\n")

    return resp


# Kanban handlers
async def handle_kanban_boards(request: web.Request) -> web.Response:
    code, out, err = _kanban(["boards", "list", "--json"])
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to list boards"),
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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to list tasks"),
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
             _sanitized_error_response(err, "NOT_FOUND", "Task not found"),
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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to complete task"),
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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to comment"),
            status=500,
        )
    return web.json_response({"ok": True})


# ─── Task Lifecycle (Plan 016) ─────────────────────────────────

async def handle_kanban_task_block(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/block — block a task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["block", task_id], board=board)
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to block task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "blocked"})


async def handle_kanban_task_unblock(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/unblock — unblock a task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["unblock", task_id], board=board)
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to unblock task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "ready"})


async def handle_kanban_task_archive(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/archive — archive a task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["archive", task_id], board=board)
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to archive task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "archived"})


async def handle_kanban_task_reclaim(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/reclaim — reclaim an archived task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["reclaim", task_id], board=board)
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to reclaim task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "ready"})


async def handle_kanban_task_decompose(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/{task_id}/decompose — decompose a task into subtasks."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["decompose", task_id, "--json"], board=board)
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to decompose task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "decomposed"})


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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to create board"),
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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to rename board"),
            status=500,
        )
    return web.json_response({"ok": True})


async def handle_kanban_board_archive(request: web.Request) -> web.Response:
    """POST /api/kanban/boards/{slug}/archive — archive a board."""
    slug = request.match_info["slug"]
    code, _, err = _kanban(["boards", "archive", slug])
    if code != 0:
        return web.json_response(
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to archive board"),
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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to delete board"),
            status=500,
        )
    return web.json_response({"ok": True})


async def handle_kanban_profiles(request: web.Request) -> web.Response:
    """GET /api/kanban/profiles — list available worker profiles."""
    import glob
    profiles_dir = os.path.expanduser("~/.hermes/profiles")
    profiles = []
    if os.path.isdir(profiles_dir):
        for entry in sorted(os.listdir(profiles_dir)):
            full = os.path.join(profiles_dir, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                profiles.append(entry)
    if not profiles:
        profiles = ["analyst", "ops", "research", "researcher", "writer"]
    return web.json_response(profiles)


async def handle_kanban_stats(request: web.Request) -> web.Response:
    """GET /api/kanban/stats?board=<slug> — aggregate task counts by status."""
    board = request.query.get("board", "")
    code, out, err = _kanban(["list", "--json"], board=board)
    if code != 0:
        return web.json_response(
            _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to compute stats"),
            status=500,
        )
    try:
        tasks = json.loads(out) if out else []
        if isinstance(tasks, dict) and "tasks" in tasks:
            tasks = tasks["tasks"]
        counts: dict[str, int] = {}
        for t in tasks:
            s = t.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return web.json_response({
            "total": len(tasks),
            "countsByStatus": counts,
        })
    except (json.JSONDecodeError, TypeError):
        return web.json_response({"total": 0, "countsByStatus": {}})


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
             _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to assign task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "assignee": assignee})


async def handle_kanban_task_create(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks — create a new task."""
    board = request.query.get("board", "")
    if not board:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "?board= required"}},
            status=422,
        )
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    title = body.get("title", "")
    if not title:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "title required"}},
            status=422,
        )
    task_body = body.get("body", "")
    assignee = body.get("assignee", "")
    priority = body.get("priority", 0)
    status = body.get("status", "todo")

    cmd = ["create", title, "--json"]
    if task_body:
        cmd.extend(["--body", json.dumps(task_body) if isinstance(task_body, dict) else task_body])
    if assignee:
        cmd.extend(["--assignee", assignee])
    if priority:
        cmd.extend(["--priority", str(priority)])
    if status and status != "todo":
        if status == "blocked":
            cmd.extend(["--initial-status", "blocked"])

    code, out, err = _kanban(cmd, board=board)
    if code != 0:
        return web.json_response(
            _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to create task"),
            status=500,
        )
    try:
        data = json.loads(out)
        return web.json_response(data, status=201)
    except json.JSONDecodeError:
        return web.json_response({"ok": True, "title": title}, status=201)


def _kanban_db_update(task_id: str, field: str, value, board: str = "") -> bool:
    """Direct SQLite update for fields the CLI doesn't support (title, body, priority, status for non-action transitions)."""
    import sqlite3 as _sqlite3
    db_path = os.path.expanduser("~/.hermes/kanban.db")
    if not os.path.exists(db_path):
        return False
    try:
        conn = _sqlite3.connect(db_path)
        conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, task_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


async def handle_kanban_task_edit(request: web.Request) -> web.Response:
    """PATCH /api/kanban/tasks/{task_id} — update task fields."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    # Apply assignee change via 'assign' subcommand
    assignee = body.get("assignee")
    if assignee is not None:
        code, _, err = _kanban(["assign", task_id, assignee], board=board)
        if code != 0:
            return web.json_response(
                _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to assign task"),
                status=500,
            )

    # Route status changes to the correct CLI command
    status = body.get("status")
    if status is not None:
        status_cmd_map = {
            "done":     ["complete", task_id],
            "blocked":  ["block", task_id],
            "ready":    ["unblock", task_id],
            "scheduled": ["schedule", task_id],
            "archived": ["archive", task_id],
        }
        if status in status_cmd_map:
            code, _, err = _kanban(status_cmd_map[status], board=board)
            if code != 0:
                return web.json_response(
                    _sanitized_error_response(err, "INTERNAL_ERROR", f"Failed to set status to {status}"),
                    status=500,
                )
        elif status in ("triage", "todo", "running", "review"):
            # No dedicated CLI command for these — update DB directly
            _kanban_db_update(task_id, "status", status, board)

    # Title, body, priority updates via direct DB edit (CLI 'edit' only supports --result/--summary)
    for field in ("title", "body", "priority"):
        if field in body:
            _kanban_db_update(task_id, field, body[field], board)

    # Return updated task
    code, out, err = _kanban(["show", "--json", task_id], board=board)
    if code != 0:
        return web.json_response({"ok": True, "task_id": task_id})
    try:
        data = json.loads(out)
        if isinstance(data, dict) and "task" in data:
            data = data["task"]
        return web.json_response(data)
    except json.JSONDecodeError:
        return web.json_response({"ok": True, "task_id": task_id})


async def handle_kanban_task_delete(request: web.Request) -> web.Response:
    """DELETE /api/kanban/tasks/{task_id} — archive (soft-delete) a task."""
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, _, err = _kanban(["archive", task_id], board=board)
    if code != 0:
        return web.json_response(
            _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to archive task"),
            status=500,
        )
    return web.json_response({"ok": True, "task_id": task_id, "status": "archived"})


async def handle_kanban_task_bulk(request: web.Request) -> web.Response:
    """POST /api/kanban/tasks/bulk — apply action to multiple tasks."""
    board = request.query.get("board", "")
    if not board:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "?board= required"}},
            status=422,
        )
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    task_ids = body.get("task_ids", [])
    if not task_ids:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "task_ids required"}},
            status=422,
        )
    action = body.get("action", "")
    value = body.get("value", "")
    if action not in ("set_status", "set_assignee"):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "action must be 'set_status' or 'set_assignee'"}},
            status=422,
        )

    affected = 0
    failed = []
    for task_id in task_ids:
        if action == "set_status" and value == "done":
            cmd = ["complete", task_id]
        elif action == "set_assignee":
            cmd = ["assign", task_id, value]
        else:
            cmd = []
        if cmd:
            code, _, _ = _kanban(cmd, board=board)
            if code == 0:
                affected += 1
            else:
                failed.append(task_id)

    return web.json_response({
        "ok": True,
        "affected": affected,
        "total": len(task_ids),
        "failed": failed,
    })


async def handle_kanban_link(request: web.Request) -> web.Response:
    """POST /api/kanban/links — add a parent->child dependency."""
    board = request.query.get("board", "")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    parent_id = body.get("parent_id", "")
    child_id = body.get("child_id", "")
    if not parent_id or not child_id:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "parent_id and child_id required"}},
            status=422,
        )
    code, _, err = _kanban(["link", parent_id, child_id], board=board)
    if code != 0:
        return web.json_response(
            _sanitized_error_response(err, "INTERNAL_ERROR", "Failed to create link"),
            status=500,
        )
    return web.json_response({"ok": True, "parent_id": parent_id, "child_id": child_id})


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
    # Stream read with early size enforcement
    CHUNK_SIZE = 64 * 1024  # 64 KB
    data_chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file_field.read_chunk(size=CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_SIZE:
            return web.json_response(
                {"error": {"code": "VALIDATION_ERROR", "message": f"file exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit"}},
                status=413,  # Payload Too Large
            )
        data_chunks.append(chunk)
    data = b"".join(data_chunks)

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


async def handle_attachment_serve(request: web.Request) -> web.StreamResponse:
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
    # Stream the file instead of loading it all into memory
    return web.FileResponse(file_path)


# ── Setup Token Redeem ────────────────────────────────────────
async def handle_setup_redeem(request):
    """POST /api/setup/redeem — exchange a one-time setup token for credentials."""
    client_ip = request.remote or "unknown"

    # Check rate limit before doing any work
    async with _setup_tokens_lock:
        fail_count, locked_until = _SETUP_REDEEM_FAILURES.get(client_ip, (0, 0.0))
        if locked_until > time.time():
            return web.json_response(
                {"error": "Too many attempts", "retry_after": int(locked_until - time.time())},
                status=429,
            )

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "JSON body required"}},
            status=400,
        )
    token = body.get("token", "")
    if not token or not isinstance(token, str):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "token required"}},
            status=422,
        )

    # Atomic check + pop inside the lock. This guarantees single-use
    # semantics even with concurrent requests: the pop happens at most once
    # for a given token, and the second concurrent caller sees the entry
    # already gone.
    async with _setup_tokens_lock:
        entry = _SETUP_TOKENS.pop(token, None)

    if entry is None:
        # Rate-limit tracking: increment failure counter
        async with _setup_tokens_lock:
            count, _ = _SETUP_REDEEM_FAILURES.get(client_ip, (0, 0.0))
            count += 1
            if count >= _SETUP_REDEEM_LOCKOUT_THRESHOLD:
                _SETUP_REDEEM_FAILURES[client_ip] = (count, time.time() + _SETUP_REDEEM_LOCKOUT_DURATION)
            else:
                _SETUP_REDEEM_FAILURES[client_ip] = (count, 0.0)
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "token invalid or already used"}},
            status=404,
        )
    if time.time() > entry["expires_at"]:
        # Rate-limit tracking: increment failure counter (expired = failed attempt)
        async with _setup_tokens_lock:
            count, _ = _SETUP_REDEEM_FAILURES.get(client_ip, (0, 0.0))
            count += 1
            if count >= _SETUP_REDEEM_LOCKOUT_THRESHOLD:
                _SETUP_REDEEM_FAILURES[client_ip] = (count, time.time() + _SETUP_REDEEM_LOCKOUT_DURATION)
            else:
                _SETUP_REDEEM_FAILURES[client_ip] = (count, 0.0)
        return web.json_response(
            {"error": {"code": "EXPIRED", "message": "token expired"}},
            status=410,
        )

    # Success: reset rate-limit counter
    async with _setup_tokens_lock:
        _SETUP_REDEEM_FAILURES[client_ip] = (0, 0.0)

    return web.json_response({
        "username": entry["username"],
        "password": entry["password"],
        "host": entry["host"],
        "port": entry["port"],
        "board": entry["board"],
    })


# ── Setup Handlers ───────────────────────────────────────────
async def handle_setup_register(request):
    """Create the first user account. Only works when no users exist."""
    import json as _json
    import hashlib, base64, secrets

    config = request.app["config"]
    paths = config.get_expanded_paths()
    auth_file = paths["auth_file"]

    # Read existing auth.json
    try:
        auth_data = _json.loads(auth_file.read_text())
    except (FileNotFoundError, _json.JSONDecodeError):
        auth_data = {"users": {}}

    # SECURITY: Only allow registration if no users exist
    if auth_data.get("users"):
        return web.json_response(
            {"error": {"message": "Registration is closed. Ask your administrator for credentials."}},
            status=403
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": {"message": "Invalid JSON body"}}, status=400)

    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        return web.json_response({"error": {"message": "Username and password are required"}}, status=400)
    if len(username) < 3 or len(password) < 8:
        return web.json_response({"error": {"message": "Username must be >=3 chars, password >=8 chars"}}, status=400)

    # Hash password with scrypt (same format as server.py BasicAuth)
    salt = secrets.token_bytes(16)
    hash_bytes = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    b64hash = base64.b64encode(hash_bytes).decode()
    password_hash = f"scrypt$16384$8$1${salt.hex()}${b64hash}"

    auth_data = {"users": {username: {"password_hash": password_hash, "created_at": "2026-01-01"}}}
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(_json.dumps(auth_data, indent=2))
    auth_file.chmod(0o600)

    return web.json_response({"status": "ok", "message": f"User '{username}' created"}, status=201)


# ── 2FA Handlers ──────────────────────────────────────────────

async def handle_2fa_verify(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/verify — verify OTP code, return auth result."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "JSON body required"}},
            status=400,
        )
    challenge_id = body.get("challenge_id", "")
    code = body.get("code", "")
    if not challenge_id or not code:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "challenge_id and code required"}},
            status=422,
        )

    from email_2fa import verify_otp
    if verify_otp(challenge_id, code):
        return web.json_response({"status": "ok", "authenticated": True})
    return web.json_response(
        {"error": {"code": "INVALID_OTP", "message": "Invalid or expired code"}},
        status=401,
    )


async def handle_2fa_setup(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/setup — enable 2FA for the authenticated user."""
    import json as _json

    config = request.app["config"]
    paths = config.get_expanded_paths()
    auth_file = paths["auth_file"]

    try:
        auth_data = _json.loads(auth_file.read_text())
    except (FileNotFoundError, _json.JSONDecodeError):
        auth_data = {"users": {}}

    # Get username from Basic Auth header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Basic Auth required"}},
            status=401,
        )
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except Exception:
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Invalid auth header"}},
            status=401,
        )

    user = auth_data.get("users", {}).get(username)
    if not user:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "User not found"}},
            status=404,
        )

    # Already enabled
    if user.get("two_factor_enabled"):
        return web.json_response(
            {"error": {"code": "ALREADY_ENABLED", "message": "2FA already enabled"}},
            status=409,
        )

    # Enable 2FA
    user["two_factor_enabled"] = True
    auth_file.write_text(_json.dumps(auth_data, indent=2))

    logger.info("2FA enabled for user %s", username)
    return web.json_response({"status": "ok", "message": "2FA enabled"})


async def handle_2fa_disable(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/disable — disable 2FA (requires OTP verification first)."""
    import json as _json

    config = request.app["config"]
    paths = config.get_expanded_paths()
    auth_file = paths["auth_file"]

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "JSON body required"}},
            status=400,
        )

    challenge_id = body.get("challenge_id", "")
    code = body.get("code", "")

    # Require OTP verification before disabling
    if challenge_id and code:
        from email_2fa import verify_otp
        if not verify_otp(challenge_id, code):
            return web.json_response(
                {"error": {"code": "INVALID_OTP", "message": "Invalid or expired code"}},
                status=401,
            )
    else:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "challenge_id and code required"}},
            status=422,
        )

    try:
        auth_data = _json.loads(auth_file.read_text())
    except (FileNotFoundError, _json.JSONDecodeError):
        auth_data = {"users": {}}

    # Get username from Basic Auth header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Basic Auth required"}},
            status=401,
        )
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, _, _ = decoded.partition(":")
    except Exception:
        return web.json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Invalid auth header"}},
            status=401,
        )

    user = auth_data.get("users", {}).get(username)
    if not user:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "User not found"}},
            status=404,
        )

    user["two_factor_enabled"] = False
    auth_file.write_text(_json.dumps(auth_data, indent=2))

    logger.info("2FA disabled for user %s", username)
    return web.json_response({"status": "ok", "message": "2FA disabled"})


async def handle_2fa_check(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/check — check if 2FA is required for the authenticated user.

    This endpoint is reached only when the auth middleware does NOT intercept
    (i.e., 2FA is not enabled for the user). If 2FA were enabled, the middleware
    would have returned {requires_2fa: true} before reaching this handler.
    """
    return web.json_response({"requires_2fa": False})


async def handle_2fa_resend(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/resend — resend OTP for an existing challenge."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "JSON body required"}},
            status=400,
        )
    challenge_id = body.get("challenge_id", "")
    if not challenge_id:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "challenge_id required"}},
            status=422,
        )

    from email_2fa import _pending_challenges, generate_challenge, send_otp

    challenge = _pending_challenges.get(challenge_id)
    if challenge is None:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "Challenge not found or expired"}},
            status=404,
        )

    # Check if expired
    if time.time() > challenge["expires"]:
        _pending_challenges.pop(challenge_id, None)
        return web.json_response(
            {"error": {"code": "EXPIRED", "message": "Challenge expired, start a new login"}},
            status=410,
        )

    # Resend the same code
    try:
        send_otp(challenge_id)
    except Exception as e:
        logger.error("2FA resend failed: %s", e)
        return web.json_response(
            {"error": {"code": "SEND_FAILED", "message": "Failed to resend code"}},
            status=500,
        )

    return web.json_response({"status": "ok", "message": "Code resent"})


# ── App Setup ────────────────────────────────────────────────
async def create_app() -> web.Application:
    auth = BasicAuth(AUTH_FILE)
    app = web.Application(middlewares=[auth.middleware, security_headers_middleware])
    app["config"] = config

    # Health
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/health", handle_healthz)

    # Setup routes (unauthenticated — must be registered before auth middleware check)
    app.router.add_post("/api/setup/register", handle_setup_register)
    app.router.add_post("/api/setup/redeem", handle_setup_redeem)

    # Session passthrough
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_post("/api/sessions", handle_session_create)
    app.router.add_get("/api/sessions/{session_id}", handle_session_detail)
    app.router.add_get("/api/sessions/{session_id}/messages", handle_session_messages)
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)

    # Chat proxy
    app.router.add_post("/v1/chat/completions", handle_chat_proxy)
    app.router.add_post("/v1/chat/completions/stream", handle_chat_stream)

    # Kanban
    app.router.add_get("/api/kanban/boards", handle_kanban_boards)
    app.router.add_post("/api/kanban/boards", handle_kanban_boards_create)
    app.router.add_post("/api/kanban/boards/{slug}/rename", handle_kanban_board_rename)
    app.router.add_post("/api/kanban/boards/{slug}/archive", handle_kanban_board_archive)
    app.router.add_delete("/api/kanban/boards/{slug}", handle_kanban_board_delete)
    app.router.add_get("/api/kanban/profiles", handle_kanban_profiles)
    app.router.add_get("/api/kanban/stats", handle_kanban_stats)
    app.router.add_get("/api/kanban/tasks", handle_kanban_tasks_list)
    app.router.add_get("/api/kanban/tasks/{task_id}", handle_kanban_task_show)
    app.router.add_post("/api/kanban/tasks/{task_id}/complete", handle_kanban_task_complete)
    app.router.add_post("/api/kanban/tasks/{task_id}/comment", handle_kanban_task_comment)
    app.router.add_post("/api/kanban/tasks/{task_id}/assign", handle_kanban_task_assign)

    # Kanban CRUD (Plan 015)
    app.router.add_post("/api/kanban/tasks", handle_kanban_task_create)
    app.router.add_patch("/api/kanban/tasks/{task_id}", handle_kanban_task_edit)
    app.router.add_delete("/api/kanban/tasks/{task_id}", handle_kanban_task_delete)
    app.router.add_post("/api/kanban/tasks/bulk", handle_kanban_task_bulk)
    app.router.add_post("/api/kanban/links", handle_kanban_link)

    # Kanban Task Lifecycle (Plan 016)
    app.router.add_post("/api/kanban/tasks/{task_id}/block", handle_kanban_task_block)
    app.router.add_post("/api/kanban/tasks/{task_id}/unblock", handle_kanban_task_unblock)
    app.router.add_post("/api/kanban/tasks/{task_id}/archive", handle_kanban_task_archive)
    app.router.add_post("/api/kanban/tasks/{task_id}/reclaim", handle_kanban_task_reclaim)
    app.router.add_post("/api/kanban/tasks/{task_id}/decompose", handle_kanban_task_decompose)

    # 2FA Auth (Plan 017)
    app.router.add_post("/api/auth/2fa/verify", handle_2fa_verify)
    app.router.add_post("/api/auth/2fa/setup", handle_2fa_setup)
    app.router.add_post("/api/auth/2fa/disable", handle_2fa_disable)
    app.router.add_post("/api/auth/2fa/resend", handle_2fa_resend)
    app.router.add_post("/api/auth/2fa/check", handle_2fa_check)

    # Attachments
    app.router.add_post("/api/attachments", handle_attachment_upload)
    app.router.add_get("/api/attachments/{att_id}", handle_attachment_serve)

    async def _cleanup_session(app):
        if HermesProxy._session is not None and not HermesProxy._session.closed:
            await HermesProxy._session.close()

    app.on_cleanup.append(_cleanup_session)

    return app


def main():
    _load_setup_tokens_from_disk()
    app = create_app()
    logger.info("Companion daemon starting on %s:%d", HOST, PORT)
    web.run_app(app, host=HOST, port=PORT, print=logger.info)


if __name__ == "__main__":
    main()
