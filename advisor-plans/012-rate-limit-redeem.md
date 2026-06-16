# Plan 012: Add rate limiting to /api/setup/redeem + per-username lockout

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py tests/test_auth_hardening.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (could lock out legitimate users under NAT/shared IP; mitigated by per-username tracking)
- **Depends on**: 006 (synchronized _SETUP_TOKENS)
- **Category**: bug (security)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

- `/api/setup/redeem` has no rate limiting (only relies on 256-bit token strength)
- Existing lockout (`BasicAuth._failures`) is keyed by `(username, IP)` — IP rotation bypasses it
- Attacker with multiple IPs can attempt 5 logins per IP × 5 IPs = 25 attempts before lockout

## Steps

### Step 1: Add per-IP rate limiting to handle_setup_redeem

**Edit** `~/.hermes/companion/server.py`:

Add at module level (near `_SETUP_TOKENS`):
```python
_SETUP_REDEEM_FAILURES: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)
_SETUP_REDEEM_LOCKOUT_THRESHOLD = 10
_SETUP_REDEEM_LOCKOUT_DURATION = 60  # seconds
```

Update `handle_setup_redeem` (the modified version from Plan 006) to check rate limit BEFORE the token check:

```python
async def handle_setup_redeem(request: web.Request) -> web.Response:
    client_ip = request.remote or "unknown"

    # Check rate limit
    async with _setup_tokens_lock:
        fail_entry = _SETUP_REDEEM_FAILURES.get(client_ip, (0, 0.0))
        count, locked_until = fail_entry
        if locked_until > time.time():
            return web.json_response(
                {"error": "Too many attempts", "retry_after": int(locked_until - time.time())},
                status=429,
            )

    # ... existing token validation
    # On failure (token not found OR expired):
    async with _setup_tokens_lock:
        count, _ = _SETUP_REDEEM_FAILURES.get(client_ip, (0, 0.0))
        count += 1
        if count >= _SETUP_REDEEM_LOCKOUT_THRESHOLD:
            _SETUP_REDEEM_FAILURES[client_ip] = (count, time.time() + _SETUP_REDEEM_LOCKOUT_DURATION)
        else:
            _SETUP_REDEEM_FAILURES[client_ip] = (count, 0.0)
    return web.json_response({"error": "..."}, status=...)
```

(On success, reset the counter: `_SETUP_REDEEM_FAILURES[client_ip] = (0, 0.0)`.)

### Step 2: Add per-username lockout (defense in depth)

In `BasicAuth`, add a secondary per-username failure counter:
```python
_user_failures: dict[str, tuple[int, float]] = {}
_USER_LOCKOUT_THRESHOLD = 5
_USER_LOCKOUT_DURATION = 300  # 5 min

# In check(), after the per-(user,IP) check:
async def _user_lockout_check(self, username: str) -> bool:
    entry = _user_failures.get(username, (0, 0.0))
    count, locked_until = entry
    if locked_until > time.time():
        return False  # locked
    return True

# After successful auth, reset:
_user_failures[username] = (0, 0.0)

# After failed auth, increment:
count, _ = _user_failures.get(username, (0, 0.0))
count += 1
if count >= _USER_LOCKOUT_THRESHOLD:
    _user_failures[username] = (count, time.time() + _USER_LOCKOUT_DURATION)
else:
    _user_failures[username] = (count, 0.0)
```

### Step 3: Add tests

**Edit** `~/.hermes/companion/tests/test_auth_hardening.py`:

```python
@pytest.mark.asyncio
async def test_setup_redeem_rate_limit():
    """After 10 failed redeems from same IP, the 11th gets 429."""
    from server import handle_setup_redeem, _SETUP_REDEEM_FAILURES
    _SETUP_REDEEM_FAILURES.clear()  # reset state

    for i in range(10):
        # Make a request with a bad token
        request = make_request({"token": f"bad-{i}"})
        response = await handle_setup_redeem(request)
        assert response.status in (404, 410)

    # 11th request should be 429
    request = make_request({"token": "bad-11"})
    response = await handle_setup_redeem(request)
    assert response.status == 429
```

### Step 4: Verify

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_auth_hardening.py -v 2>&1 | tail -10
```

## Done criteria

- [ ] `python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] The new rate-limit test passes
- [ ] `git status` is clean (commit)
