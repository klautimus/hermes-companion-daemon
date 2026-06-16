# Plan 007: Sanitize subprocess error messages — don't leak daemon internals to clients

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (changes error response shape, not success path)
- **Depends on**: none
- **Category**: bug (security - information disclosure)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

The daemon's kanban handlers return raw subprocess stderr to API clients via `err or "..."` patterns. This leaks internal information:

- File paths (`/opt/hermes-companion/server/auth.json`)
- Binary locations (`/usr/bin/hermes`)
- Permission issues (`Permission denied: /var/log/hermes.log`)
- Internal command structure (`hermes kanban --board telos task show t_abc123`)

**Adversary use case**: An attacker who can reach the API can probe the daemon's internal state. A `board=../../../../etc/passwd` (if it ever gets through validation) might surface path info via the error. Even legitimate clients see internal noise that should be logged server-side only.

## Current state (verified 2026-06-16 by Atlas)

**File**: `~/.hermes/companion/server.py` — 13+ handler functions return `err or "..."` to the client. Examples:

- Line 366: `return web.json_response({"error": err or "..."}, ...)`
- Line 392: same pattern
- Line 407: same
- Line 428: same
- Line 458: same
- Line 505: same
- Line 529: same
- Line 540: same
- Line 560: same
- Line 583: same
- Line 606: same
- Line 614: same
- Line 635: same
- Line 650: same

The `err` variable is `r.stderr` from a `subprocess.run` call (the `_kanban()` wrapper at line 282+).

**Example leak** (hypothetical):
```bash
# Bad request
curl -X POST http://127.0.0.1:8777/api/kanban/boards/rename \
  -u "kevin:..." \
  -d '{"board":"../../etc/passwd"}'
# Returns: {"error": "hermes: error: invalid board name '../../etc/passwd'\nFile: /opt/hermes-companion/server/boards/../../etc/passwd\nPermission denied: /opt/hermes-companion/server/etc/passwd"}
```

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Run all tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -3` | 0 failures |
| Run kanban tests | `python3 -m pytest tests/test_server.py tests/test_integration.py -v 2>&1 | tail -10` | passes (note: test_server.py is at root, may need to run directly) |

## Scope

**In scope**:
- `~/.hermes/companion/server.py` — replace each `err or "..."` pattern with a sanitized error message; log the full err server-side at WARNING/ERROR level
- `~/.hermes/companion/tests/test_server.py` — add a test that verifies the error response doesn't contain substrings like `/opt/`, `/usr/`, or the hermes binary path

**Out of scope** (do NOT touch):
- The success response shape — only errors change
- The `_kanban()` wrapper function — orthogonal
- The subprocess invocation itself

## Git workflow

- Daemon repo branch: `advisor/007-sanitize-subprocess-errors`
- Single commit is fine (mechanical change)
- Message style: imperative, scoped (`fix(security): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Add a helper function for sanitized error responses

**Edit** `~/.hermes/companion/server.py` — add near the top of the file (after imports, before handlers):

```python
def _sanitized_error_response(err: str, fallback: str = "Operation failed", status: int = 500) -> web.Response:
    """Return a JSON error response that does NOT leak internal paths/binary locations.

    The full err is logged server-side at ERROR level. The client only sees a
    generic message plus a short request_id they can use when reporting bugs.
    """
    import uuid
    request_id = uuid.uuid4().hex[:8]
    logger.error("kanban error [request_id=%s]: %s", request_id, err)
    return web.json_response(
        {
            "error": fallback,
            "request_id": request_id,
        },
        status=status,
    )
```

The `request_id` is the key: the client sees a short hex string, can include it in bug reports, and the operator can grep server logs for that ID to find the full internal error.

### Step 2: Replace each `err or "..."` pattern

Find all occurrences:
```bash
cd /home/kevin/.hermes/companion
grep -n "err or " server.py
```

For each occurrence, replace:
```python
# Before
return web.json_response({"error": err or "..."}, status=500)
# After
return _sanitized_error_response(err, fallback="Kanban operation failed", status=500)
```

The `fallback` parameter should be specific to the operation (e.g., "Failed to create board", "Failed to assign task"). Walk through each handler and pick a sensible message.

**Verify**: `cd /home/kevin/.hermes/companion && grep -n "err or " server.py` returns no matches.

### Step 3: Add a test

**Edit** `~/.hermes/companion/tests/test_server.py` (or create a new test file if test_server.py isn't easy to add to):

```python
@pytest.mark.asyncio
async def test_kanban_error_does_not_leak_paths(monkeypatch):
    """Verify that when _kanban() returns stderr containing internal paths,
    the HTTP response body does NOT include those paths."""
    # Mock _kanban to return a leak
    monkeypatch.setattr(
        "server._kanban",
        lambda *a, **kw: (1, "", "/opt/hermes-companion/server/auth.json: Permission denied"),
    )
    # Make a request that triggers the handler
    # ... (use aiohttp test client to make the actual request)
    response_body = ...  # actual request result
    # The leak should NOT appear in the response
    assert "/opt/hermes-companion" not in response_body["error"]
    assert "auth.json" not in response_body["error"]
    # The request_id should be present
    assert "request_id" in response_body
    assert len(response_body["request_id"]) == 8
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_server.py -v 2>&1 | tail -10
```

### Step 4: Run full test suite

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 0 failures
```

## Test plan

- New test catches internal-path leaks
- Existing tests should still pass (the response shape is "more generic error" but the success path is unchanged)
- Manual: with the daemon up, trigger an error (e.g., invalid board name) and confirm the response body doesn't include `/opt/` or `/usr/` substrings

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] `grep -n "err or " server.py` returns 0 matches
- [ ] `grep -n "_sanitized_error_response" server.py` returns at least 14 matches (1 declaration + 13+ call sites)
- [ ] The new leak test passes
- [ ] No files outside the in-scope list are modified
- [ ] `git status` is clean in daemon repo

## STOP conditions

Stop and report back (do not improvise) if:
- The new test is hard to write because test_server.py has a different import pattern — open the file and follow the existing pattern, or create a new test file in `tests/`
- Replacing the error response breaks a downstream test that asserts on the old error message — update the test to assert on the new shape (`{"error": "...", "request_id": "..."}`)
- More than 5 call sites don't fit the simple replacement pattern (some may need different fallback messages) — handle them case-by-case

## Maintenance notes

- The `request_id` is the bridge between client and operator. If a client reports "operation failed with request_id abc12345", the operator greps server logs for that ID to find the full error.
- The generic `fallback` messages should be specific enough that the client knows what went wrong at a high level (e.g., "Failed to create board" vs. "Operation failed").
- Future improvement: include a category field (`{"error": "...", "category": "INVALID_INPUT", "request_id": "..."}`) for client-side error handling. Out of scope for this plan.
