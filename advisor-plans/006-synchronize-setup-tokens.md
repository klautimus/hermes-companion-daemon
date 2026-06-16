# Plan 006: Synchronize `_SETUP_TOKENS` to prevent concurrent-redeem race

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (adds lock, no behavior change for single-request path)
- **Depends on**: none
- **Category**: bug (concurrency)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

`_SETUP_TOKENS: dict[str, dict]` is a module-level global mutated by `register_setup_token()` (wizard path), `_load_setup_tokens_from_disk()` (startup), and `handle_setup_redeem()` (HTTP handler). aiohttp handles requests concurrently, so two simultaneous `POST /api/setup/redeem` calls with the same token can both:

1. `entry = _SETUP_TOKENS.get(token)` — both see the entry
2. `del _SETUP_TOKENS[token]` — only the first delete succeeds
3. Both proceed to return the credentials

The current code uses `entry = _SETUP_TOKENS.pop(token, None)` at the handler (line 689), which IS atomic in CPython's GIL sense. **However**, the check-then-pop pattern across lines 684-689 has a TOCTOU window between the expiry check and the pop:

```python
# handle_setup_redeem (server.py:684-699)
if token not in _SETUP_TOKENS:
    return web.json_response({"error": "Invalid token"}, status=404)
entry = _SETUP_TOKENS[token]
if time.monotonic() > entry["expires_at"]:
    return web.json_response({"error": "Token expired"}, status=410)
# ← TOCTOU window: another request can pop here
del _SETUP_TOKENS[token]
```

The two requests both see `token in _SETUP_TOKENS` as True, both see `expires_at` as valid, both then `del` the entry. The second `del` is a no-op (Python doesn't raise on missing key here) but the first request has already returned credentials, AND the second request also returns credentials. The single-use guarantee is broken.

## Current state (verified 2026-06-16 by Atlas)

**File**: `~/.hermes/companion/server.py:56, 68-95, 669-699`

```python
# Line 56
_SETUP_TOKENS: dict[str, dict] = {}

# Lines 669-699 (handle_setup_redeem)
async def handle_setup_redeem(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    token = data.get("token", "")
    if not token or not isinstance(token, str):
        return web.json_response({"error": "Missing token"}, status=400)

    entry = _SETUP_TOKENS.get(token)
    if entry is None:
        return web.json_response({"error": "Invalid token"}, status=404)

    if time.monotonic() > entry["expires_at"]:
        del _SETUP_TOKENS[token]
        return web.json_response({"error": "Token expired"}, status=410)

    username = entry["username"]
    password = entry["password"]
    board = entry.get("board", "default")
    del _SETUP_TOKENS[token]    # <-- non-atomic with the check above
    return web.json_response({
        "username": username,
        "password": password,
        "host": _config["server"]["host"],
        "port": _config["server"]["port"],
        "board": board,
    })
```

The fix is to use `dict.pop()` atomically (which IS atomic in CPython) and combine the check + pop into one operation. Plus add an `asyncio.Lock` for explicit safety.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Run all tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -3` | 0 failures |
| Run specific test (if exists) | `python3 -m pytest tests/test_setup_token.py -v 2>&1 | tail -20` | passes |

## Scope

**In scope**:
- `~/.hermes/companion/server.py` — add `_setup_tokens_lock: asyncio.Lock = asyncio.Lock()` at module level; update `handle_setup_redeem` to use atomic pop inside the lock; update `_load_setup_tokens_from_disk` and `register_setup_token` to acquire the lock when mutating
- `~/.hermes/companion/tests/test_setup_token.py` — add a new test for concurrent redeem (use `asyncio.gather` with two concurrent requests)

**Out of scope** (do NOT touch):
- The `register_setup_token` wizard code in `setup_wizard.py` — that's a separate process that writes the token file; the daemon only reads from the file in `_load_setup_tokens_from_disk`
- The token storage file format (`setup_token.json`) — orthogonal
- The `BasicAuth` middleware — separate concern

## Git workflow

- Daemon repo branch: `advisor/006-synchronize-setup-tokens`
- Single commit is fine
- Message style: imperative, scoped (`fix(concurrency): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Add the lock

**Edit** `~/.hermes/companion/server.py` — add right after `_SETUP_TOKENS: dict[str, dict] = {}` (line 56):

```python
_SETUP_TOKENS: dict[str, dict] = {}
_setup_tokens_lock: asyncio.Lock = asyncio.Lock()
```

Add the import at the top of the file if not already present: `import asyncio`.

### Step 2: Update `register_setup_token` to use the lock

**Edit** `~/.hermes/companion/server.py` (lines 60-66):

```python
async def register_setup_token(token: str, username: str, password: str, board: str = "default", ttl_seconds: int = 300):
    async with _setup_tokens_lock:
        _SETUP_TOKENS[token] = {
            "username": username,
            "password": password,
            "board": board,
            "expires_at": time.monotonic() + ttl_seconds,
        }
```

Note: this changes the function from sync to async. All callers must `await` it.

**Find callers**: `grep -rn "register_setup_token" --include="*.py" .`

Update each caller to `await` it. The wizard path (in `setup_wizard.py:243`) calls `register_setup_token_wizard` (different function), not `register_setup_token` — verify with grep.

### Step 3: Update `handle_setup_redeem` to be atomic

**Edit** `~/.hermes/companion/server.py` (lines 669-699):

Replace the entire handler body with:

```python
async def handle_setup_redeem(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    token = data.get("token", "")
    if not token or not isinstance(token, str):
        return web.json_response({"error": "Missing token"}, status=400)

    # Atomic check + pop inside the lock. This guarantees single-use
    # semantics even with concurrent requests: the pop happens at most once
    # for a given token, and the second concurrent caller sees the entry
    # already gone.
    async with _setup_tokens_lock:
        entry = _SETUP_TOKENS.pop(token, None)

    if entry is None:
        return web.json_response({"error": "Invalid token"}, status=404)

    if time.monotonic() > entry["expires_at"]:
        return web.json_response({"error": "Token expired"}, status=410)

    return web.json_response({
        "username": entry["username"],
        "password": entry["password"],
        "host": _config["server"]["host"],
        "port": _config["server"]["port"],
        "board": entry.get("board", "default"),
    })
```

The key change: `entry = _SETUP_TOKENS.pop(token, None)` happens INSIDE the lock, atomically removing the entry regardless of its validity. If `entry` is None, the token was already used. If non-None, the lock guarantees no other request can see this entry.

### Step 4: Update `_load_setup_tokens_from_disk` to use the lock

**Edit** `~/.hermes/companion/server.py` (lines 68-95):

The function currently mutates `_SETUP_TOKENS` without a lock. It only runs at startup (before the server starts accepting requests), so the race window is small but exists if the function is called later. Wrap the mutation in the lock:

```python
def _load_setup_tokens_from_disk():
    """Load setup tokens from a file written by the setup wizard, then delete the file."""
    try:
        config_path = Path(_config["auth"]["file_path"]).parent
        token_file = config_path / "setup_token.json"
        if not token_file.exists():
            return
        raw = json.loads(token_file.read_text())
        # Build the new entries first
        new_entries = {}
        now = time.monotonic()
        for entry in raw.get("tokens", []):
            try:
                created = datetime.fromisoformat(entry["created_at"]).timestamp()
                age = time.time() - created
                if age > 300:
                    continue
                expires_at = now + (300 - age)
                new_entries[entry["token"]] = {
                    "username": entry["username"],
                    "password": entry["password"],
                    "board": entry.get("board", "default"),
                    "expires_at": expires_at,
                }
            except Exception as e:
                logger.warning("Skipping malformed token entry: %s", e)
        # Atomic swap
        # (asyncio.Lock doesn't work in sync functions; we accept the small race here
        # because this function is only called at startup before the server listens)
        _SETUP_TOKENS.update(new_entries)
        token_file.unlink()
    except Exception as e:
        logger.warning("Failed to load setup_token.json: %s", e)
```

Note: `_load_setup_tokens_from_disk` is a sync function and `asyncio.Lock` doesn't work in sync context. Since this only runs at startup (before the server starts accepting requests), the race window is acceptable. Document it with a comment.

### Step 5: Add a concurrent-redeem test

**Edit** `~/.hermes/companion/tests/test_setup_token.py`:

Add a new test that exercises the concurrent-redeem race:

```python
import asyncio

@pytest.mark.asyncio
async def test_concurrent_redeem_only_one_succeeds():
    """Two concurrent /api/setup/redeem with the same token should
    result in exactly one 200 response and one 404."""
    from server import _SETUP_TOKENS, handle_setup_redeem, register_setup_token

    token = "test-token-concurrent"
    await register_setup_token(token, "alice", "secret", "default", 300)
    # Use aiohttp test client
    app = await create_app()
    async with TestClient(app) as client:
        # Fire two requests simultaneously
        async def redeem():
            return await client.post("/api/setup/redeem", json={"token": token})

        r1, r2 = await asyncio.gather(redeem(), redeem())

    # One should be 200, the other should be 404
    statuses = sorted([r1.status, r2.status])
    assert statuses == [200, 404], f"Expected [200, 404], got {statuses}"
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_setup_token.py -v 2>&1 | tail -10
# Expected: all tests pass, including the new concurrent test
```

### Step 6: Run the full test suite

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 0 failures
```

## Test plan

- The new concurrent-redeem test catches the race
- All existing tests should still pass
- The atomic pop inside the lock guarantees single-use semantics

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] `python3 -m pytest tests/test_setup_token.py -v 2>&1 | grep -c "PASSED"` shows the new concurrent test passes
- [ ] `grep -n "_setup_tokens_lock" server.py` returns at least 3 matches (declaration + 2+ uses)
- [ ] `grep -n "_SETUP_TOKENS.pop" server.py` returns at least 1 match
- [ ] `grep -n "asyncio.Lock" server.py` returns at least 1 match
- [ ] No files outside the in-scope list are modified
- [ ] `git status` is clean in daemon repo

## STOP conditions

Stop and report back (do not improvise) if:
- The new concurrent test fails inconsistently (suggests a deeper race; investigate further)
- `asyncio.Lock()` cannot be created at module-load time (it can; tested in CPython)
- The `register_setup_token` callers are not obvious from grep — open the call sites and update them

## Maintenance notes

- The asyncio.Lock is created at module load. This is safe because asyncio.Lock doesn't actually create the underlying Future until first acquired.
- If the daemon later moves to multi-process (gunicorn workers), the in-process lock won't be enough — would need a Redis-backed distributed lock. Document this as a future work item.
- The atomic pop is the primary defense; the lock is a belt-and-suspenders measure for clarity.
