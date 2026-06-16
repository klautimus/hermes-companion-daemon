# Plan 002: Auth hardening — constant-time compare, brute-force lockout, scrypt N bump

> **Executor instructions**: Read Plan 001 first. This plan modifies the canonical `server.py` and `setup_wizard.py` that Plan 001 consolidates. Run all verification commands. If Plan 001 is not yet DONE, STOP.
>
> **Drift check (run first)**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- server.py setup_wizard.py
> ```

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 001
- **Category**: security
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

The daemon's HTTP Basic Auth has three exploitable weaknesses: (1) hash comparison is not constant-time (`server.py:96` — `==`), allowing timing-based byte-by-byte brute force of scrypt hashes; (2) the comparison returns early if the username is unknown (`server.py:84-85`), enabling user enumeration via timing; (3) there is no brute-force protection — every failed auth attempt takes scrypt ~100ms (which protects the hash) but allows unlimited attempts, and there is no lockout. Additionally, the scrypt work factor is N=16384, below OWASP's 2023 minimum of N=131072 (8x weaker offline brute-force resistance). On successful auth, the user's hash should be checked and re-hashed with stronger params (transparent upgrade). All three fixes together close the offline-crack window and slow online attacks.

## Current state

**File**: `server.py` (root, 572 LOC after Plan 001)

`server.py:55-109` — `BasicAuth` class:
```python
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
            return False                                # <-- SEC-09: early return, timing leak
        phash = user.get("password_hash", "")
        if not phash.startswith("scrypt$"):
            return phash == password                    # <-- SEC: not constant-time
        try:
            _, n, r, p, salt_hex, expected = phash.split("$", 5)
            n, r, p = int(n), int(r), int(p)
            salt_bytes = bytes.fromhex(salt_hex)
            hash_bytes = hashlib.scrypt(
                password.encode(), salt=salt_bytes, n=n, r=r, p=p, dklen=32,
            )
            return base64.b64encode(hash_bytes).decode() == expected  # <-- SEC-05: not constant-time
        except Exception:
            return False
```

**File**: `setup_wizard.py` (root, 333 LOC after Plan 001)

`setup_wizard.py:40` — `SCRYPT_N = 16384`

`setup_wizard.py:130-148` — `create_auth_file` builds the hash string with these parameters. The `auth.json` format is `{"users": {"<username>": {"password_hash": "scrypt$16384$8$1$<salt_hex>$<expected_b64>"}}}`.

**Live auth.json**: `/home/kevin/.hermes/companion/auth.json` (gitignored) uses the current params.

**Repo conventions** (from Plan 001 recon):
- Auth middleware: `BasicAuth` class + `web.middleware` decorator
- Test pattern: `tests/test_first_run.py` (pytest, async)
- Constants defined at module top: `MAX_UPLOAD_SIZE`, `SCRYPT_N`, etc.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| Test new auth behavior | `cd /home/kevin/.hermes/companion && python -m pytest tests/test_auth_hardening.py -xvs` | new tests pass |
| Verify constant-time | `cd /home/kevin/.hermes/companion && python -c "import hmac; print('hmac available')"` | prints `hmac available` |
| Smoke import | `cd /home/kevin/.hermes/companion && python -c "import server; print('ok')"` | prints `ok` |

## Scope

**In scope**:
- `server.py` — modify `BasicAuth.check()` to use `hmac.compare_digest`; add brute-force tracking; keep API stable
- `setup_wizard.py` — bump `SCRYPT_N` constant; add transparent hash upgrade in `BasicAuth.check()`
- `tests/test_auth_hardening.py` — create new test file
- Existing `tests/test_first_run.py` — add timing-compare test (small, doesn't need server)

**Out of scope**:
- `auth.json` — never commit changes; in-place upgrade happens at runtime
- `config.py`, `config_schema.py` — Plan 007 covers config changes
- Android app — separate plan (004)
- Network rate limiting at infrastructure layer (Cloudflare, nginx) — separate concern

## Git workflow

- Branch: `advisor/002-auth-hardening` (continue from `advisor/001-daemon-consolidate` if branched from it; otherwise branch from current master after Plan 001 merges)
- Commit style: `fix(auth):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Add `hmac` import

In `server.py:11-22`, add `import hmac` to the standard library imports.

**Verify**: `python -c "import server; print('ok')"` exits 0.

### Step 2: Add brute-force tracking state

In `server.py`, inside `BasicAuth.__init__`, add:
```python
self._failures: dict = {}  # key: (username, client_ip) -> (count, locked_until_monotonic)
self._max_failures: int = 5
self._lockout_seconds: int = 60
```

### Step 3: Add lockout check at top of `check()`

In `server.py`, at the very top of `BasicAuth.check()`, before `_reload`:
```python
client_ip = request.remote or "unknown"
key = (username_placeholder, client_ip)  # username known later; use raw header
lockout = self._failures.get(("__pending__", client_ip))
if lockout and time.monotonic() < lockout[1]:
    return False
```

Note: at this point we haven't parsed the auth header. Move lockout check AFTER parsing username, so key is `(username, client_ip)`.

**Better approach**: parse auth header first, then check lockout:
```python
async def check(self, request):
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
    if fail and time.monotonic() < fail[1]:
        return False

    # ... continue with existing user lookup ...
```

### Step 4: Replace `==` comparisons with `hmac.compare_digest`

In `server.py`, replace:
- `return phash == password` → `return hmac.compare_digest(phash, password)`
- `return base64.b64encode(hash_bytes).decode() == expected` → `return hmac.compare_digest(base64.b64encode(hash_bytes).decode(), expected)`

### Step 5: Remove early-return on unknown user (or equalize timing)

Currently `server.py:84-85`:
```python
user = self._users.get(username)
if not user:
    return False
```

To equalize timing, perform a dummy scrypt compute when the user is not found:
```python
user = self._users.get(username)
if not user:
    # Equalize timing: do a dummy scrypt compute against a fixed salt
    dummy_salt = b"\x00" * 16
    try:
        hashlib.scrypt(password.encode(), salt=dummy_salt, n=16384, r=8, p=1, dklen=32)
    except Exception:
        pass
    # Now also do a constant-time compare against a dummy hash
    hmac.compare_digest(password, b"\x00" * 32.decode("latin-1") if False else "")
    return False
```

**Simpler approach** (preferred): just do the compare_digest against an empty string. The dummy scrypt alone is enough to equalize timing for the "valid vs invalid username" case. The empty compare adds negligible cost.

```python
user = self._users.get(username)
if not user:
    # Equalize timing: do a dummy scrypt compute so timing doesn't reveal username existence
    try:
        hashlib.scrypt(password.encode(), salt=b"\x00" * 16, n=16384, r=8, p=1, dklen=32)
    except Exception:
        pass
    return False
```

### Step 6: Track failures and trigger lockout

After `return False` paths, increment the failure counter. Refactor `check()` to call a helper `_record_failure(key)` on any failure path and `_clear_failures(key)` on success.

```python
def _record_failure(self, key):
    count, until = self._failures.get(key, (0, 0.0))
    count += 1
    if count >= self._max_failures:
        until = time.monotonic() + self._lockout_seconds
    self._failures[key] = (count, until)
```

### Step 7: On successful auth, attempt transparent hash upgrade

If the existing hash uses `N=16384`, re-hash with new N=131072 and update `auth.json`:

```python
# After successful compare:
if phash.startswith("scrypt$"):
    _, n_str, r_str, p_str, salt_hex, _ = phash.split("$", 5)
    if int(n_str) < 131072:
        # Transparent upgrade
        new_salt = os.urandom(16)
        new_hash = hashlib.scrypt(
            password.encode(), salt=new_salt, n=131072, r=8, p=1, dklen=32,
        )
        new_phash = f"scrypt$131072$8$1${new_salt.hex()}${base64.b64encode(new_hash).decode()}"
        try:
            raw = json.loads(self._file.read_text())
            raw["users"][username]["password_hash"] = new_phash
            self._file.write_text(json.dumps(raw, indent=2))
            self._mtime = 0  # force reload
        except Exception as e:
            logger.warning("Failed to upgrade hash for %s: %s", username, e)
```

**Risk**: This writes to `auth.json` on every successful login for any user with old params. That's a write per session. Make it idempotent by only writing if `mtime` or `password_hash` changed.

### Step 8: Bump `SCRYPT_N` in `setup_wizard.py:40`

Change `SCRYPT_N = 16384` to `SCRYPT_N = 131072`.

**Verify**: `grep "SCRYPT_N" setup_wizard.py` shows `SCRYPT_N = 131072`.

### Step 9: Run all tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -30
```

### Step 10: Write new tests in `tests/test_auth_hardening.py`

Create `tests/test_auth_hardening.py` with:
- `test_unknown_user_equal_timing` — assert that `_check_with_user("nonexistent", "any")` and `_check_with_user("admin", "wrong")` take similar time (within 50% margin). Use `time.monotonic()`.
- `test_lockout_after_n_failures` — fail auth 5 times, expect 6th attempt to return False even with correct password.
- `test_lockout_clears_after_timeout` — set `lockout_seconds=0.1`, fail 5 times, sleep 0.2, expect 6th to succeed.
- `test_constant_time_compare_used` — monkey-patch `hmac.compare_digest`, assert it was called.
- `test_hash_upgrade_on_login` — set up `auth.json` with N=16384 hash, call `check()` with correct password, verify file rewritten with N=131072.

Pattern: follow `tests/test_setup_wizard.py:1-50` (pytest fixtures, async test patterns).

### Step 11: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(auth): harden BasicAuth with constant-time compare, brute-force lockout, scrypt N=131072

- Replace == with hmac.compare_digest for hash and plaintext fallback
- Equalize timing for unknown users (dummy scrypt compute)
- Add per-(username, IP) failure tracking with 5-failure/60s lockout
- Bump SCRYPT_N from 16384 to 131072 (OWASP 2023 minimum)
- Transparent hash upgrade on next successful login for legacy N=16384 hashes
- Add tests/test_auth_hardening.py covering all four behaviors

CVE-class: timing-attack resistance, online brute-force resistance
EOF
)"
```

## Test plan

- Existing tests still pass: `python -m pytest -xvs`
- New `tests/test_auth_hardening.py` covers: timing equality, lockout, timeout reset, constant-time compare, hash upgrade
- Verification: `python -m pytest tests/test_auth_hardening.py -v` — all 5 new tests pass

## Done criteria

- [ ] `python -m pytest -xvs` exits 0
- [ ] `python -m pytest tests/test_auth_hardening.py -v` — 5 new tests pass
- [ ] `grep -n "SCRYPT_N" setup_wizard.py` shows `131072`
- [ ] `grep -n "compare_digest" server.py` shows 2+ matches
- [ ] `grep -n "_failures" server.py` shows 3+ matches (init, get, set)
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 002 row updated to `DONE`

## STOP conditions

- Plan 001 is not DONE — STOP, this plan depends on consolidated code.
- Drift check (Step 1 of Plan 002) reveals changes — STOP, the plan is stale.
- Existing tests fail AFTER changes (Step 9) — STOP, surface regression.
- New auth test reveals `hmac.compare_digest` not behaving as expected — STOP, re-design.
- `auth.json` write test fails — STOP, may indicate file lock or perm issue. Don't write to live auth.json in tests; use a tmp file.

## Maintenance notes

- `SCRYPT_N=131072` makes scrypt compute ~8x slower (~800ms per auth). For high-traffic servers, consider caching successful auth per (IP, user, hash) for a few seconds.
- The 5-failure/60s lockout is in-memory only. After daemon restart, lockout state resets. For persistent lockout, write to `auth.json.lockout` or similar.
- Hash upgrade writes to `auth.json` on every successful login until all users have N=131072. After rollout, this self-clears. Consider logging "upgraded user X to N=131072" so admin can track.
- Future: if you add a refresh-token flow, the in-memory lockout should be per-refresh-token not per-password.
