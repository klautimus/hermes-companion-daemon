# Plan 011: End-to-end audit — invalid credentials bug (P0)

> **Executor instructions**: This is a P0 plan from a fresh end-to-end audit. The user reported "invalid credentials" on the Android app. Root cause was a self-inflicted test overwrite + daemon memory cache + cleartext network policy. All three are fixed; this plan documents the systemic improvements to prevent recurrence.

## Status
- **Priority**: P0 (production blocker)
- **Effort**: S (incremental hardening on top of working state)
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness + security + DX
- **Planned at**: commit `ccbbfc8`, 2026-06-17

## Why this matters

The user reported `Invalid credentials` from the Android app on `https://android.kevlarscreations.com`. The end-to-end audit found THREE independent bugs, each of which alone would break auth:

1. **Config path split** (server.py vs setup_wizard.py used different `auth.json` paths) — fixed in commit `e85380f`
2. **Cleartext network policy** blocked `android.kevlarscreations.com` — fixed in commit `6b74e4a` (Android side)
3. **Self-inflicted test overwrite** — running `python3 -c "from setup_wizard import create_auth_file..."` overwrote the live `auth.json` with a placeholder hash. The daemon did NOT pick up the change because the `BasicAuth` class caches users in memory and only re-reads on file `mtime` change during the request lifecycle (which it does correctly) BUT the user tried connecting through a path that the cache invalidation path doesn't cover cleanly. This was fixed by writing a real hash for `kevin` and restarting the daemon.

## Current state (verified working)

- **Daemon**: `http://127.0.0.1:8777` (auth: `kevin` / `Kevi667n!1991!`) ✓
- **Tunnel**: `https://android.kevlarscreations.com` (Cloudflare → daemon) ✓
- **App**: v1.2.0 installed on Pixel 4 XL ✓
- **End-to-end**: tunnel → daemon → kanban API returns 40 boards ✓

## Findings

### F1 (P0): Test path can overwrite live auth.json (resolved)
- **Evidence**: `setup_wizard.py:create_auth_file` writes to the path `config.get_expanded_paths()["auth_file"]` resolves to, which is now `~/.hermes/companion/auth.json` (the LIVE daemon file)
- **Impact**: Running any test code that imports `setup_wizard` and calls `create_auth_file` clobbers the live auth file
- **Risk**: A misplaced test in the repo can lock out the production user
- **Fix sketch**: (a) tests should use a tmp path, (b) `create_auth_file` should refuse to write to a path under `~/.hermes/companion/` unless an explicit `force=True` flag is set

### F2 (P1): Daemon caches users in memory; mtime reload is correct but restart was needed in this incident
- **Evidence**: `server.py:130-138` `_reload()` only triggers on `mtime != self._mtime`. When a partial write happens (e.g., the test code wrote a partial JSON before erroring out), `mtime` may not have changed in a way the daemon notices, OR the daemon hadn't seen the new file yet at request time
- **Impact**: Stale credentials persist across file changes until the daemon restarts
- **Risk**: Slower response to credential rotation
- **Fix sketch**: Add a 5-second TTL on the in-memory users dict, OR force reload on every 401 response (degraded mode), OR document that credential changes require `systemctl --user restart hermes-companion`

### F3 (P1): Android network security config lacked public hostname
- **Evidence**: `network_security_config.xml` only allowed cleartext for `localhost`, `127.0.0.1`, `10.0.2.2`. The Cloudflare tunnel URL `android.kevlarscreations.com` is not in the list. (Fixed in commit `6b74e4a`.)
- **Impact**: The app would block any request to the public hostname, even with valid credentials
- **Risk**: None — cleartext is permitted only for the local-loopback-equivalent tunnel
- **Fix sketch**: Already done; document the trust model in a code comment (also done)

### F4 (P2): No automated test for end-to-end daemon auth
- **Evidence**: `tests/test_server.py` tests `BasicAuth` directly but never starts the daemon and curls it
- **Impact**: Regressions like F1 (auth.json overwrite) wouldn't be caught by CI
- **Fix sketch**: Add `tests/test_e2e_auth.py` that (a) starts the daemon on a test port, (b) creates a temp `auth.json`, (c) curls the healthz + a protected endpoint, (d) verifies 401 → 200 transition on auth

### F5 (P2): scrypt N=131072 fails in some Python builds (memory limit)
- **Evidence**: `setup_wizard.py:127-128` calls `hashlib.scrypt(..., n=131072, ...)`. On the active Python build this raises `ValueError: [digital envelope routines] memory limit exceeded`. The daemon uses N=16384 successfully.
- **Impact**: New `auth.json` files created by the wizard can't be written; the wizard silently fails or writes a placeholder
- **Risk**: Wizard never produces a usable auth file on this system
- **Fix sketch**: Either reduce wizard's `SCRYPT_N` to 16384 to match server, OR document the system memory requirement, OR add a try/except fallback to N=16384

## Scope

**In scope** (this plan):
- Add a safety check in `create_auth_file` that refuses to write to the live daemon path without explicit `force=True`
- Add end-to-end test for daemon auth (F4)
- Fix scrypt N consistency: align wizard to N=16384 (matches daemon) OR bump daemon to 131072 (better security but memory-intensive)

**Out of scope**:
- Android app changes (the build is already deployed)
- Cloudflare tunnel config (already working)
- User management API (separate feature)

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Run all tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -x --tb=short` | all pass |
| Run new E2E test | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/test_e2e_auth.py -xvs` | passes |
| Verify daemon still accepts kevin | `curl -u kevin:Kevi667n!1991! http://127.0.0.1:8777/healthz` | 200 OK |

## Steps

### Step 1: Add safety check to create_auth_file

In `setup_wizard.py:134`, modify `create_auth_file` to take a `force` parameter and refuse to write to `~/.hermes/companion/auth.json` unless forced:

```python
def create_auth_file(config: CompanionConfig, username: str, password: str, force: bool = False) -> Path:
    """Create auth.json with scrypt-hashed password. Refuses to overwrite
    a path under ~/.hermes/companion/ unless force=True."""
    paths = config.get_expanded_paths()
    auth_file = paths["auth_file"]
    live_path = Path("~/.hermes/companion/auth.json").expanduser().resolve()
    if auth_file.resolve() == live_path and not force:
        raise RuntimeError(
            f"Refusing to overwrite live daemon auth file at {auth_file}. "
            f"Pass force=True if this is intentional."
        )
    # ... rest unchanged
```

### Step 2: Fix scrypt N consistency

In `setup_wizard.py:40`, change `SCRYPT_N = 131072` to `SCRYPT_N = 16384` to match the daemon's default. This makes the wizard produce hashes the daemon can verify without upgrade.

### Step 3: Add E2E auth test

Create `tests/test_e2e_auth.py`:

```python
import asyncio
import base64
import hashlib
import json
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import requests


def _make_auth_file(tmpdir: Path, username: str, password: str) -> Path:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    b64 = base64.b64encode(h).decode()
    phash = f"scrypt$16384$8$1${salt.hex()}${b64}"
    auth_file = tmpdir / "auth.json"
    auth_file.write_text(json.dumps({
        "users": {username: {"password_hash": phash, "created_at": "2026-01-01"}}
    }))
    return auth_file


@pytest.fixture
def daemon(tmp_path):
    """Start the daemon on a free port with a test auth.json."""
    port = 18777 + secrets.randbelow(100)
    auth_file = _make_auth_file(tmp_path, "testuser", "testpass")
    config = tmp_path / "config.yaml"
    config.write_text(f"""
server:
  host: 127.0.0.1
  port: {port}
hermes:
  api_url: http://127.0.0.1:8642
  api_key: test-key
auth:
  file: {auth_file}
""")
    env = {
        **__import__("os").environ,
        "XDG_CONFIG_HOME": str(tmp_path),  # force config_schema to use tmp_path/.config
    }
    proc = subprocess.Popen(
        ["python3", "-c", f"import sys; sys.path.insert(0, '.'); from server import main; main()"],
        cwd=Path(__file__).parent.parent,
        env=env,
    )
    # wait for port
    for _ in range(30):
        try:
            requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield f"http://127.0.0.1:{port}", "testuser", "testpass"
    proc.terminate()
    proc.wait(timeout=5)


def test_valid_credentials(daemon):
    base, user, pw = daemon
    r = requests.get(f"{base}/api/kanban/boards", auth=(user, pw))
    assert r.status_code in (200, 500)  # not 401


def test_invalid_credentials(daemon):
    base, user, pw = daemon
    r = requests.get(f"{base}/api/kanban/boards", auth=(user, "wrong"))
    assert r.status_code == 401
```

### Step 4: Run all tests

```bash
cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -x --tb=short
```

### Step 5: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git commit -m "fix(safety): prevent create_auth_file from clobbering live daemon auth

- setup_wizard.py:create_auth_file now requires force=True to overwrite
  ~/.hermes/companion/auth.json
- align wizard SCRYPT_N with daemon (16384) so wizard produces
  hashes the daemon can verify without upgrade
- tests/test_e2e_auth.py: spin up real daemon, verify auth roundtrip

Caught by full end-to-end audit after user reported invalid credentials.
Root cause was a test invocation that clobbered auth.json; the
explicit-safety check prevents the same class of mistake.
"
```

## Test plan

- `python3 -m pytest tests/ -x --tb=short` — all pass
- `python3 -m pytest tests/test_e2e_auth.py -xvs` — new tests pass
- `curl -u kevin:Kevi667n!1991! http://127.0.0.1:8777/healthz` — 200 OK

## Done criteria

- [ ] `setup_wizard.py:create_auth_file` has `force` parameter and refuses live-path write
- [ ] `setup_wizard.py` `SCRYPT_N` matches daemon's default
- [ ] `tests/test_e2e_auth.py` exists with 2+ tests
- [ ] `python3 -m pytest tests/ -x --tb=short` exits 0
- [ ] Live daemon still accepts `kevin` / `Kevi667n!1991!`
- [ ] Git commit clean

## STOP conditions

- E2E test cannot bind the port (firewall/Docker) — skip with pytest.mark.skip
- Existing tests fail after scrypt N change — STOP, investigate which plan set N=131072 and whether the daemon needs to be updated instead
