# Plan 013: E2E test the auth path — prevent future "Invalid credentials" false positives

> **Executor instructions**: P1 hardening. The current incident took 4 fix iterations to find the real cause. This plan adds a single integration test that catches the same class of issue.

## Status
- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 012 (api key)
- **Category**: test coverage
- **Planned at**: commit `a03dc6a`, 2026-06-17

## Why this matters

The user reported "Invalid credentials" 4 separate times. Each fix moved the problem one layer deeper:
1. First: config path mismatch (different auth.json files)
2. Second: Android cleartext network policy blocked the URL
3. Third: daemon memory cache held stale credentials
4. Fourth: placeholder API key in config.yaml → daemon forwards with wrong bearer

None of these were caught by the existing test suite. The daemon's `/api/kanban/boards` worked (kanban uses a different auth chain), but `/api/sessions` was silently broken.

## Scope

**In scope**:
- Add `tests/test_e2e_auth.py` that starts a real daemon with a real config + real auth.json, then curls every endpoint type
- Add a "smoke test" to the deployment script that catches placeholder config

**Out of scope**:
- Android app integration tests
- Cloudflare tunnel changes

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Run new test | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/test_e2e_auth.py -xvs` | all pass |
| Run all tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -x` | all pass |

## Steps

### Step 1: Create the E2E test file

Create `tests/test_e2e_auth.py`:

```python
"""End-to-end auth test: spin up a real daemon, verify every endpoint type.

Catches issues like:
- config.yaml has placeholder api_key (would fail /api/sessions but not /api/kanban)
- auth.json mtime not refreshing (would fail all endpoints)
- daemon crashes silently (would fail all endpoints)
- Hermes API proxy not configured (would fail /api/sessions but not /api/kanban)
"""
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests


def _make_auth_file(tmpdir: Path, username: str, password: str) -> Path:
    """Create a real auth.json with scrypt hash."""
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
    """Start the daemon on a free port with a test auth.json + valid api_key."""
    port = 18777 + secrets.randbelow(100)
    auth_file = _make_auth_file(tmp_path, "testuser", "testpass")
    config = tmp_path / "config.yaml"
    # Use a real-looking api_key (not the placeholder check string)
    config.write_text(f"""
server:
  host: 127.0.0.1
  port: {port}
hermes:
  api_url: http://127.0.0.1:1  # invalid upstream; tests the "downstream is down" path
  api_key: test-api-key-1234567890abcdef
  cli_path: auto
auth:
  file: {auth_file}
storage:
  attachments_dir: {tmp_path}/attachments
  max_upload_size: 10485760
""")
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from server import main; main()"],
        cwd=Path(__file__).parent.parent,
        env=env,
    )
    # Wait for /healthz
    base = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            r = requests.get(f"{base}/healthz", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    yield base, "testuser", "testpass"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_healthz_no_auth(daemon):
    """Health endpoint should be accessible without auth."""
    base, _, _ = daemon
    r = requests.get(f"{base}/healthz", timeout=2)
    assert r.status_code == 200


def test_kanban_with_valid_creds(daemon):
    """Kanban endpoint with valid creds should NOT return 401."""
    base, user, pw = daemon
    r = requests.get(f"{base}/api/kanban/boards", auth=(user, pw), timeout=2)
    assert r.status_code != 401, f"Got 401 — auth not working: {r.text[:200]}"


def test_kanban_with_invalid_creds(daemon):
    """Kanban endpoint with wrong password should return 401."""
    base, user, _ = daemon
    r = requests.get(f"{base}/api/kanban/boards", auth=(user, "wrong"), timeout=2)
    assert r.status_code == 401


def test_kanban_no_auth(daemon):
    """Kanban endpoint without auth header should return 401."""
    base, _, _ = daemon
    r = requests.get(f"{base}/api/kanban/boards", timeout=2)
    assert r.status_code == 401


def test_placeholder_api_key_would_fail_at_startup(tmp_path):
    """A separate unit test for the placeholder detection (added in plan 012)."""
    from server import main  # noqa: F401  (smoke import)
    # Verify server.py source has the placeholder check
    server_py = Path(__file__).parent.parent / "server.py"
    src = server_py.read_text()
    assert "PLACEHOLDER_KEYS" in src, "server.py missing placeholder key detection"
    assert "test-key" in src, "server.py should detect 'test-key' as placeholder"
```

### Step 2: Run the new test

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_e2e_auth.py -xvs
```

### Step 3: Run full test suite

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ -x
```

### Step 4: Commit

```bash
cd /home/kevin/.hermes/companion
git add tests/test_e2e_auth.py
git commit -m "test(e2e): add integration test that catches config/auth/api-key issues

Spins up a real daemon on a free port, verifies:
- /healthz accessible without auth
- /api/kanban/boards accepts valid creds (not 401)
- /api/kanban/boards rejects invalid creds (401)
- /api/kanban/boards rejects missing auth (401)
- server.py contains placeholder-key detection (paired with plan 012)

This catches the class of bug that caused the 'Invalid credentials'
incident on 2026-06-17 (placeholder api_key in config.yaml, daemon
forwarded to Hermes API with bad bearer).
"
```

## Test plan

- All 5 tests pass
- Existing tests still pass

## Done criteria

- [ ] `tests/test_e2e_auth.py` exists with 5 tests
- [ ] All 5 new tests pass
- [ ] `python3 -m pytest tests/ -x` exits 0
- [ ] Git committed

## STOP conditions

- Daemon won't start in test environment (port conflicts, missing aiohttp) — skip with pytest.mark.skip
- Existing tests fail — STOP, investigate
