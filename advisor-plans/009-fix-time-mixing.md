# Plan 009: Fix setup token time-mixing bug — use `time.monotonic()` consistently

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (correctness fix for expiry math)
- **Depends on**: none
- **Category**: bug (correctness)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

`_load_setup_tokens_from_disk` mixes `time.time()` and `time.monotonic()`:
- Line 79: `created = datetime.fromisoformat(entry["created_at"]).timestamp()` — wall-clock (`time.time()`)
- Line 80: `age = time.time() - created` — wall-clock
- Line 83: `expires_at = now + (300 - age)` where `now = time.monotonic()` (line 76) — MONOTONIC
- Line 690: `time.monotonic() > entry["expires_at"]` — compares monotonic against the mixed value

**The bug**: `time.monotonic()` returns seconds since some unspecified starting point (often boot time), while `time.time()` returns seconds since the Unix epoch. These are NOT comparable.

For example:
- `now_monotonic = 12345.6` (seconds since boot)
- `created = 1718000000.5` (Unix epoch)
- `age = 0.5` (wall-clock)
- `expires_at = 12345.6 + (300 - 0.5) = 12645.1` (a value that means "seconds since boot")
- Later check: `time.monotonic() > 12645.1` — this works ONLY if the daemon has been running for less than 12645 seconds (~3.5 hours) since boot

If the daemon runs longer than that, the comparison `12345.6 < 12645.1` may be true or false depending on whether the system was rebooted. The token expiration is effectively undefined.

## Current state (verified 2026-06-16 by Atlas)

**File**: `~/.hermes/companion/server.py:70-95`

```python
def _load_setup_tokens_from_disk():
    ...
    now = time.monotonic()  # <-- monotonic
    for entry in raw.get("tokens", []):
        try:
            created = datetime.fromisoformat(entry["created_at"]).timestamp()  # <-- wall-clock
            age = time.time() - created  # <-- wall-clock
            if age > 300:
                continue
            expires_at = now + (300 - age)  # <-- monotonic + (wall-clock)
            ...
        ...
```

## Steps

### Step 1: Use `time.time()` consistently

The token file (`setup_token.json`) writes `created_at` as an ISO 8601 string (wall-clock). The daemon reads it back. Both should use `time.time()` for arithmetic.

**Edit** `~/.hermes/companion/server.py:70-95`:

```python
def _load_setup_tokens_from_disk():
    """Load setup tokens from a file written by the setup wizard, then delete the file."""
    try:
        config_path = Path(_config["auth"]["file_path"]).parent
        token_file = config_path / "setup_token.json"
        if not token_file.exists():
            return
        raw = json.loads(token_file.read_text())
        now = time.time()  # <-- use wall-clock consistently
        for entry in raw.get("tokens", []):
            try:
                created = datetime.fromisoformat(entry["created_at"]).timestamp()
                age = now - created  # <-- wall-clock - wall-clock
                if age > 300:
                    continue
                expires_at = created + 300  # <-- 5 min from creation
                _SETUP_TOKENS[entry["token"]] = {
                    "username": entry["username"],
                    "password": entry["password"],
                    "board": entry.get("board", "default"),
                    "expires_at": expires_at,
                }
            except Exception as e:
                logger.warning("Skipping malformed token entry: %s", e)
        token_file.unlink()
    except Exception as e:
        logger.warning("Failed to load setup_token.json: %s", e)
```

### Step 2: Update the expiry check to use `time.time()`

**Edit** `~/.hermes/companion/server.py:690`:

Find:
```python
if time.monotonic() > entry["expires_at"]:
    return web.json_response({"error": "Token expired"}, status=410)
```

Replace with:
```python
if time.time() > entry["expires_at"]:
    return web.json_response({"error": "Token expired"}, status=410)
```

### Step 3: Update `register_setup_token` to use `time.time()` (consistency)

**Edit** `~/.hermes/companion/server.py:60-66`:

```python
async def register_setup_token(token: str, username: str, password: str, board: str = "default", ttl_seconds: int = 300):
    async with _setup_tokens_lock:
        _SETUP_TOKENS[token] = {
            "username": username,
            "password": password,
            "board": board,
            "expires_at": time.time() + ttl_seconds,  # <-- wall-clock
        }
```

### Step 4: Verify

```bash
cd /home/kevin/.hermes/companion
grep -n "time.monotonic" server.py
# Expected: only in STARTED_AT and middleware timeout paths, NOT in _SETUP_TOKENS code
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 0 failures
```

## Done criteria

- [ ] `grep -n "time.monotonic" server.py` shows no matches in `_SETUP_TOKENS` or `register_setup_token` or `handle_setup_redeem`
- [ ] `python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] `git status` is clean (commit)

## STOP conditions

- A test asserts on the specific monotonic-based expires_at value — update the test to use wall-clock
