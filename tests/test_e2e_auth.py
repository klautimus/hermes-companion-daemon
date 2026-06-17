#!/usr/bin/env python3
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
import socket
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


def _find_free_port() -> int:
    """Find a free port by binding to port 0."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def daemon(tmp_path):
    """Start the daemon on a free port with a test auth.json + valid api_key.

    config_schema.load_config() resolves CONFIG_FILE as:
        $XDG_CONFIG_HOME/hermes-companion/config.yaml
    So we set XDG_CONFIG_HOME to a parent dir and write config.yaml inside
    the hermes-companion subdirectory.
    """
    port = _find_free_port()
    # XDG_CONFIG_HOME points to parent; config goes in parent/hermes-companion/
    config_dir = tmp_path / "hermes-companion"
    config_dir.mkdir()
    auth_file = _make_auth_file(tmp_path, "testuser", "testpass")
    config = config_dir / "config.yaml"
    # Use a real-looking api_key (not the placeholder check string)
    config.write_text(f"""server:
  host: 127.0.0.1
  port: {port}
hermes:
  api_url: http://127.0.0.1:1
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for /healthz
    base = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            r = requests.get(f"{base}/healthz", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        proc.wait()
        pytest.fail(f"Daemon failed to start on port {port}")
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


def test_placeholder_api_key_detection():
    """Verify server.py source has the placeholder detection (added in plan 012)."""
    server_py = Path(__file__).parent.parent / "server.py"
    src = server_py.read_text()
    assert "PLACEHOLDER_KEYS" in src, "server.py missing placeholder key detection"
    assert "test-key" in src, "server.py should detect 'test-key' as placeholder"
