# Plan 003: Fix QR code password leak — replace with one-time setup token

> **Executor instructions**: Read Plans 001 and 002 first. This plan modifies `setup_wizard.py` only. Run all verification commands.
>
> **Drift check (run first)**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- setup_wizard.py
> ```

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: 001
- **Category**: security
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

`setup_wizard.py:154-168` generates a QR code encoding `hermescompanion://configure?url=...&user=...&pass=<PLAINTEXT_PASSWORD>&board=...`. This plaintext password may be:
1. Saved in browser history when user follows the URI
2. Captured in Android intent logs (`logcat`) when the deep-link fires
3. Persisted in QR scanner history (Google Lens, etc.)
4. Captured in screenshots or photos of the QR
5. Leaked via `print_connection_info` (L216-228) which prints password to stdout — also captured in setup transcripts, terminal scrollback, systemd journal

The fix: replace the password in the URI with a one-time setup token (server-generated, short-lived, single-use). The token is exchanged for credentials via a new daemon endpoint. The plaintext password is delivered through the in-person setup (typed into the mobile app manually) or never displayed at all (auto-fill on first connection).

## Current state

**File**: `setup_wizard.py` (root, 333 LOC after Plan 001)

Lines 154-168:
```python
def generate_qr_code(config: CompanionConfig, username: str, password: str) -> str:
    """Generate QR code data URI for mobile app config."""
    # hermescompanion://configure?url=...&user=...&pass=...&board=...
    server_url = f"http://{config.server.host}:{config.server.port}"
    board = "default"

    import urllib.parse
    params = {
        "url": server_url,
        "user": username,
        "pass": password,
        "board": board,
    }
    query = urllib.parse.urlencode(params)
    return f"hermescompanion://configure?{query}"
```

Lines 216-228:
```python
def print_connection_info(config: CompanionConfig, username: str, password: str) -> None:
    """Print connection info for mobile app setup."""
    server_url = f"http://{config.server.host}:{config.server.port}"
    print("\n" + "=" * 60)
    print("SETUP COMPLETE — Mobile App Configuration")
    print("=" * 60)
    print(f"\nServer URL:  {server_url}")
    print(f"Username:    {username}")
    print(f"Password:    {password}")          # <-- LEAKS PASSWORD TO STDOUT
    print(f"Board:       default")
    print("\nOpen the Hermes Companion app on your Android device")
    print("and enter these credentials.")
    print("=" * 60)
```

**Android side**: `MainActivity.kt:80-85` extracts `pass` query param from `hermescompanion://configure?...&pass=...`.

**Repo conventions** (from recon):
- Module-level helpers, no class-based state
- Token generation: `secrets.token_urlsafe(32)` (256-bit, ~43 chars)
- One-time token storage: in-memory dict in daemon, single-use, 5-minute TTL
- Auth middleware exempts `/healthz`, `/health`. New endpoint `/api/setup/redeem` should also be exempt from auth (it's the bootstrap).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| New test | `cd /home/kevin/.hermes/companion && python -m pytest tests/test_setup_token.py -v` | all new tests pass |
| QR generation smoke | `cd /home/kevin/.hermes/companion && python -c "from setup_wizard import generate_setup_token; print(generate_setup_token())"` | prints 43-char token |

## Scope

**In scope**:
- `setup_wizard.py` — add `generate_setup_token()`; replace `pass=...` with `token=...` in URI; stop printing plaintext password in `print_connection_info`
- `server.py` — add `/api/setup/redeem` endpoint that consumes a token, returns `{username, password, board, server_url, expires_at}` once
- `tests/test_setup_token.py` — create new test file
- Android side NOT in scope (separate repo, separate plan)

**Out of scope**:
- Android `MainActivity.kt:80-85` — the `pass` parser needs to change to `token`, but Android is a separate repo. **Document this in plans/003 as a follow-up that must land before this plan is fully effective.**
- TOTP or rate-limit on token endpoints — out of scope for v1
- Revoking existing tokens — single-use means consumed token can't be redeemed again
- The setup_wizard's QR PNG file `setup_qr.png` (already excludes password in URL, but rendered to file) — covered by URI change

## Git workflow

- Branch: `advisor/003-qr-token`
- Commit style: `fix(setup):`
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Add `secrets` import

In `setup_wizard.py:1-20`, add `import secrets` to the standard library imports.

**Verify**: `python -c "import setup_wizard; print('ok')"` exits 0.

### Step 2: Add `generate_setup_token()`

In `setup_wizard.py`, after `generate_password()` (around L130), add:
```python
def generate_setup_token() -> str:
    """Generate a single-use, 5-minute-TTL setup token.
    
    Returned in the QR code URI instead of the plaintext password. The mobile
    app redeems it via POST /api/setup/redeem to fetch the actual credentials.
    """
    return secrets.token_urlsafe(32)
```

### Step 3: Modify `generate_qr_code()` to take and emit token

Change signature from `(config, username, password)` to `(config, username, token)`. Inside, replace `"pass": password` with `"token": token`. Update the comment.

**Verify**: `python -c "from setup_wizard import generate_qr_code; print(generate_qr_code.__doc__)"` shows updated docstring.

### Step 4: Modify `print_connection_info()` to suppress password

Remove the `print(f"Password:    {password}")` line. Replace with:
```python
print("Password:    (transferred via secure QR token — check your mobile app)")
```

The function still accepts `password` for backward compat (it may be passed in tests) but does not print it. If `password` is no longer needed for anything else, deprecate the parameter.

**Verify**: write a small inline test
```bash
cd /home/kevin/.hermes/companion
python -c "import io, contextlib; from setup_wizard import print_connection_info; from config_schema import CompanionConfig; buf = io.StringIO(); ... "
```
(You'll need to redirect stdout — better to write a real test in Step 8.)

### Step 5: Update the call site in `run_setup_wizard()` (L316)

Change:
```python
qr_data = generate_qr_code(config, username, password)
```
to:
```python
token = generate_setup_token()
qr_data = generate_qr_code(config, username, token)
```

And keep the call to `print_connection_info(config, username, password)` so the URL + username still print (the user needs the URL to type manually if QR fails), but the password is suppressed by Step 4.

### Step 6: Add `/api/setup/redeem` endpoint in `server.py`

In `server.py`, before the auth middleware is set up, define a token store. Actually, the auth middleware exempts `/healthz` and `/health`. We need to add `/api/setup/redeem` to that list, then implement the handler.

In `server.py:100-110` (the `middleware` method), update:
```python
@web.middleware
async def middleware(self, request, handler):
    if request.path in ("/healthz", "/health", "/api/setup/redeem"):
        return await handler(request)
    ...
```

Then add a handler and a token store near the top of the file (after `STARTED_AT`):
```python
# One-time setup tokens. Each entry: {"username": str, "password": str, "board": str, "expires_at": float}
_SETUP_TOKENS: dict[str, dict] = {}

def register_setup_token(token: str, username: str, password: str, board: str = "default", ttl_seconds: int = 300):
    _SETUP_TOKENS[token] = {
        "username": username,
        "password": password,
        "board": board,
        "expires_at": time.monotonic() + ttl_seconds,
    }

async def handle_setup_redeem(request):
    body = await request.json()
    token = body.get("token", "")
    if not token or not isinstance(token, str):
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "token required"}},
            status=422,
        )
    entry = _SETUP_TOKENS.pop(token, None)  # single-use: pop removes
    if entry is None:
        return web.json_response(
            {"error": {"code": "NOT_FOUND", "message": "token invalid or already used"}},
            status=404,
        )
    if time.monotonic() > entry["expires_at"]:
        return web.json_response(
            {"error": {"code": "EXPIRED", "message": "token expired"}},
            status=410,
        )
    return web.json_response({
        "username": entry["username"],
        "password": entry["password"],
        "board": entry["board"],
    })
```

In `create_app()` (after the auth middleware registration but before route registration), add:
```python
app.router.add_post("/api/setup/redeem", handle_setup_redeem)
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python -c "import server; print('ok')"
```

### Step 7: Update `run_setup_wizard()` to register the token with the daemon

The setup wizard runs as a CLI process. The daemon is a separate process. To register the token, the wizard must:
- Write a token file to a known path (e.g., `~/.config/hermes-companion/setup_token.json`)
- The daemon reads this file on startup or watches it

Simpler approach: the wizard writes a one-line `setup_token.json` next to `auth.json`. The daemon loads it at startup. Tokens older than 5 minutes are rejected.

In `run_setup_wizard()` (around L300-330), after creating `auth_file`, add:
```python
import json
from datetime import datetime, timezone
token = generate_setup_token()
register_setup_token_wizard(token, username, password, config)
```

And define:
```python
def register_setup_token_wizard(token: str, username: str, password: str, config: CompanionConfig):
    """Write the setup token to a file the daemon will read on startup."""
    paths = config.get_expanded_paths()
    config_dir = paths["config_dir"]
    config_dir.mkdir(parents=True, exist_ok=True)
    token_file = config_dir / "setup_token.json"
    token_file.write_text(json.dumps({
        "tokens": [{
            "token": token,
            "username": username,
            "password": password,
            "board": "default",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    }, indent=2))
    token_file.chmod(0o600)
```

In `server.py`, at module load (after `load_config()` or similar), read the token file:
```python
def _load_setup_tokens_from_disk():
    config_path = Path(_config["auth"]["file_path"]).parent
    token_file = config_path / "setup_token.json"
    if not token_file.exists():
        return
    try:
        raw = json.loads(token_file.read_text())
        now = time.monotonic()
        for entry in raw.get("tokens", []):
            created = datetime.fromisoformat(entry["created_at"]).timestamp()
            age = time.time() - created
            if age > 300:
                continue
            expires_at = now + (300 - age)
            _SETUP_TOKENS[entry["token"]] = {
                "username": entry["username"],
                "password": entry["password"],
                "board": entry.get("board", "default"),
                "expires_at": expires_at,
            }
        # After loading, delete the file (tokens are single-use and ephemeral)
        token_file.unlink()
    except Exception as e:
        logger.warning("Failed to load setup_token.json: %s", e)
```

Call `_load_setup_tokens_from_disk()` at daemon startup (in `main()` after `create_app()`).

### Step 8: Write tests in `tests/test_setup_token.py`

```python
import pytest
import json
import time
from setup_wizard import generate_setup_token, generate_qr_code
from config_schema import CompanionConfig

def test_generate_setup_token_length():
    token = generate_setup_token()
    assert isinstance(token, str)
    assert len(token) >= 32  # 32 bytes urlsafe -> 43 chars

def test_generate_qr_code_no_password():
    """Regression: ensure QR code URI does not contain 'pass=' with plaintext password."""
    config = CompanionConfig()
    token = generate_setup_token()
    qr_uri = generate_qr_code(config, "admin", token)
    assert "pass=" not in qr_uri
    assert "token=" in qr_uri
    assert token in qr_uri

def test_qr_code_uri_format():
    config = CompanionConfig()
    token = generate_setup_token()
    qr_uri = generate_qr_code(config, "admin", token)
    assert qr_uri.startswith("hermescompanion://configure?")
    assert "url=" in qr_uri
    assert "user=admin" in qr_uri
    assert f"token={token}" in qr_uri
```

### Step 9: Run all tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -30
```

### Step 10: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(setup): replace plaintext password in QR code with one-time setup token

The QR code generated by setup_wizard embedded the plaintext password in
the hermescompanion://configure URI, leaking it via:
- Browser history (when URI is followed)
- Android intent logs (logcat)
- QR scanner history (Google Lens, etc.)
- Screenshots
- Setup wizard stdout (print_connection_info)

Fix:
- generate_setup_token() returns a 256-bit single-use token (5-min TTL)
- QR URI now contains token=... instead of pass=...
- New daemon endpoint POST /api/setup/redeem exchanges token for {username, password}
- Token file (setup_token.json) is written by wizard, loaded+deleted by daemon at startup
- print_connection_info no longer prints plaintext password

Android follow-up required (separate repo): MainActivity.kt must change
'pass' query param parse to 'token' and POST to /api/setup/redeem.
EOF
)"
```

## Test plan

- `tests/test_setup_token.py` — 3+ tests for token generation and QR URI format
- Existing `tests/test_setup_wizard.py` — may need updates if it asserts on `pass=` URI format
- Verification: `python -m pytest tests/test_setup_token.py -v` — all new tests pass

## Done criteria

- [ ] `python -m pytest -xvs` exits 0
- [ ] `python -m pytest tests/test_setup_token.py -v` — all new tests pass
- [ ] `grep -n "pass=" setup_wizard.py` shows no matches in the URI generation
- [ ] `grep -n "token_urlsafe" setup_wizard.py` shows `generate_setup_token` definition
- [ ] `grep -n "/api/setup/redeem" server.py` shows route registration
- [ ] `grep -n "print(f\"Password" setup_wizard.py` shows no plaintext password print
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 003 row updated to `DONE`

## STOP conditions

- Plans 001 not DONE — STOP.
- Drift check shows `setup_wizard.py` changed — STOP, re-verify.
- `secrets` import fails (Python < 3.10) — STOP, daemon requires 3.10+ but verify on the test env.
- Token file path conflict (file already exists from previous run) — STOP, design a safer mechanism.

## Maintenance notes

- Tokens are in-memory only. Daemon restart = all pending tokens lost. Users must re-run setup wizard. Document this.
- The token file is created by wizard and deleted by daemon. If the wizard creates it but the daemon never starts, the file lingers. Add a check in `run_setup_wizard()` to delete stale token files older than 5 minutes.
- 256-bit token is more than enough. 128-bit would also suffice but 32 bytes urlsafe is a nice round number.
- The Android side MUST be updated to use the token endpoint. Until it is, the QR code change breaks setup. Coordinate rollout.
- For v2: replace token with a short PIN + server-validated cert. Tokens are easier to phish than a per-device cert.
