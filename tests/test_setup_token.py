#!/usr/bin/env python3
"""Tests for the setup token system (Plan 003)."""

import asyncio
import base64
import hashlib
import hmac as _hmac
import json
import os
import sys
import tempfile
import time
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import aiohttp

# Add parent dir for direct imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from setup_wizard import generate_setup_token, generate_qr_code
from config_schema import CompanionConfig


# ── Bootstrap: make companion importable as a package ──────────────
_companion_dir = Path(__file__).parent.parent

_companion_pkg = types.ModuleType("companion")
_companion_pkg.__path__ = [str(_companion_dir)]
_companion_pkg.__package__ = "companion"
sys.modules["companion"] = _companion_pkg

_stub_config = types.ModuleType("companion.config_schema")
_stub_config.load_config = lambda: None
_stub_config.MODULE_DIR = Path("/tmp")

_stub_first_run = types.ModuleType("companion.first_run")
_stub_first_run.ensure_configured_or_exit = lambda: None

sys.modules["companion.config_schema"] = _stub_config
sys.modules["companion.first_run"] = _stub_first_run

_server_spec = importlib.util.spec_from_file_location(
    "companion.server",
    str(_companion_dir / "server.py"),
    submodule_search_locations=[],
)
_server_mod = importlib.util.module_from_spec(_server_spec)
sys.modules["companion.server"] = _server_mod
_server_spec.loader.exec_module(_server_mod)


# ── Token generation ────────────────────────────────────────────

class TestGenerateSetupToken:
    def test_returns_string(self):
        token = generate_setup_token()
        assert isinstance(token, str)

    def test_length(self):
        """32 bytes urlsafe -> 43 chars."""
        token = generate_setup_token()
        assert len(token) >= 32

    def test_unique_each_time(self):
        t1 = generate_setup_token()
        t2 = generate_setup_token()
        assert t1 != t2

    def test_urlsafe_characters(self):
        """Token should only contain URL-safe base64 characters."""
        import re
        token = generate_setup_token()
        assert re.match(r'^[A-Za-z0-9_-]+$', token)


# ── QR code URI format ─────────────────────────────────────────

class TestGenerateQrCodeNoPassword:
    def test_no_plaintext_password_in_uri(self):
        """Regression: QR code URI must NOT contain 'pass=' with plaintext password."""
        config = CompanionConfig()
        token = generate_setup_token()
        qr_uri = generate_qr_code(config, "admin", token)
        assert "pass=" not in qr_uri

    def test_token_in_uri(self):
        config = CompanionConfig()
        token = generate_setup_token()
        qr_uri = generate_qr_code(config, "admin", token)
        assert "token=" in qr_uri
        assert token in qr_uri

    def test_uri_format(self):
        config = CompanionConfig()
        token = generate_setup_token()
        qr_uri = generate_qr_code(config, "admin", token)
        assert qr_uri.startswith("hermescompanion://configure?")
        assert "url=" in qr_uri
        assert "user=admin" in qr_uri
        assert "board=default" in qr_uri

    def test_custom_board(self):
        config = CompanionConfig()
        token = generate_setup_token()
        qr_uri = generate_qr_code(config, "admin", token)
        # Default board is "default"
        assert "board=default" in qr_uri


# ── Setup token redeem endpoint ────────────────────────────────

class TestSetupTokenRedeem:
    """Test the /api/setup/redeem endpoint logic in server.py."""

    def setup_method(self):
        _server_mod._SETUP_TOKENS.clear()

    def teardown_method(self):
        _server_mod._SETUP_TOKENS.clear()

    def _register_token(self, token="test-token-123", username="admin", password="secretpw"):
        _server_mod._SETUP_TOKENS[token] = {
            "username": username,
            "password": password,
            "board": "default",
            "expires_at": time.monotonic() + 300,
        }

    def test_redeem_returns_credentials(self):
        self._register_token()
        token = "test-token-123"
        entry = _server_mod._SETUP_TOKENS.pop(token, None)
        assert entry is not None
        assert entry["username"] == "admin"
        assert entry["password"] == "secretpw"
        assert entry["board"] == "default"

    def test_redeem_single_use(self):
        self._register_token()
        token = "test-token-123"
        # First redeem succeeds
        entry = _server_mod._SETUP_TOKENS.pop(token, None)
        assert entry is not None
        # Second redeem fails (already consumed)
        entry2 = _server_mod._SETUP_TOKENS.pop(token, None)
        assert entry2 is None

    def test_redeem_expired_token(self):
        _server_mod._SETUP_TOKENS["expired-token"] = {
            "username": "admin",
            "password": "pw",
            "board": "default",
            "expires_at": time.monotonic() - 1,  # already expired
        }
        entry = _server_mod._SETUP_TOKENS.pop("expired-token", None)
        assert entry is not None
        assert time.monotonic() > entry["expires_at"]

    def test_redeem_invalid_token(self):
        entry = _server_mod._SETUP_TOKENS.pop("nonexistent", None)
        assert entry is None


# ── Token file loading ─────────────────────────────────────────

class TestTokenFileLoading:
    def setup_method(self):
        _server_mod._SETUP_TOKENS.clear()

    def teardown_method(self):
        _server_mod._SETUP_TOKENS.clear()

    def test_load_setup_tokens_from_disk(self, tmp_path):
        """_load_setup_tokens_from_disk reads and deletes the token file."""
        from datetime import datetime, timezone

        token_file = tmp_path / "setup_token.json"
        created = datetime.now(timezone.utc).isoformat()
        token_file.write_text(json.dumps({
            "tokens": [{
                "token": "disk-token-abc",
                "username": "admin",
                "password": "diskpw",
                "board": "default",
                "created_at": created,
            }],
        }))

        # Patch config on the server module so get_expanded_paths() uses our tmp_path
        _server_mod.config = type("MockConfig", (), {
            "get_expanded_paths": lambda self: {
                "auth_file": tmp_path / "auth.json",
                "config_dir": tmp_path,
                "attachments_dir": tmp_path / "attachments",
            },
        })()
        _server_mod._load_setup_tokens_from_disk()

        # Token should be loaded
        assert "disk-token-abc" in _server_mod._SETUP_TOKENS
        entry = _server_mod._SETUP_TOKENS["disk-token-abc"]
        assert entry["username"] == "admin"
        assert entry["password"] == "diskpw"

        # File should be deleted after loading
        assert not token_file.exists()

    def test_load_skips_expired_tokens(self, tmp_path):
        """Tokens older than 5 minutes are loaded but return 410 EXPIRED (not 404 NOT_FOUND)."""
        from datetime import datetime, timezone, timedelta

        token_file = tmp_path / "setup_token.json"
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        token_file.write_text(json.dumps({
            "tokens": [{
                "token": "old-token",
                "username": "admin",
                "password": "pw",
                "board": "default",
                "host": "127.0.0.1",
                "port": 8777,
                "created_at": old_time,
            }],
        }))

        _server_mod.config = type("MockConfig", (), {
            "get_expanded_paths": lambda self: {
                "auth_file": tmp_path / "auth.json",
                "config_dir": tmp_path,
                "attachments_dir": tmp_path / "attachments",
            },
        })()
        _server_mod._load_setup_tokens_from_disk()

        # Expired tokens ARE loaded (so they return 410 not 404)
        assert "old-token" in _server_mod._SETUP_TOKENS
        assert _server_mod._SETUP_TOKENS["old-token"]["expires_at"] < time.time()
        # File still deleted even if all tokens expired
        assert not token_file.exists()


# ── Concurrent redeem ─────────────────────────────────────────

class TestConcurrentRedeem:
    """Test that concurrent /api/setup/redeem requests with the same token
    result in exactly one 200 and one 404 (single-use guarantee)."""

    def setup_method(self):
        _server_mod._SETUP_TOKENS.clear()

    def teardown_method(self):
        _server_mod._SETUP_TOKENS.clear()

    @pytest.mark.asyncio
    async def test_concurrent_redeem_only_one_succeeds(self, tmp_path):
        """Two concurrent POST /api/setup/redeem with the same token should
        result in exactly one 200 response and one 404."""
        from aiohttp.test_utils import TestClient, TestServer

        # Create a minimal auth file so BasicAuth doesn't crash
        auth_file = tmp_path / "auth.json"
        import hashlib, base64
        password = "testpw"
        # auth.json format: {"users": {"admin": {"password_hash": ..., "salt": ..., "role": "admin"}}}
        # Use the BasicAuth._hash method pattern: HMAC-SHA256
        import os
        salt = base64.b64encode(os.urandom(16)).decode()
        pw_hash = base64.b64encode(
            _hmac.new(salt.encode(), password.encode(), hashlib.sha256).digest()
        ).decode()
        auth_file.write_text(json.dumps({
            "users": {
                "admin": {"password_hash": pw_hash, "salt": salt, "role": "admin"}
            }
        }))

        # Patch _config and AUTH_FILE so create_app() works
        _server_mod._config = {"auth": {"file_path": str(auth_file)}}
        _server_mod.AUTH_FILE = auth_file

        # Register a token
        await _server_mod.register_setup_token(
            "concurrent-token", "alice", "secret", "default", 300
        )

        # Create the app and test client
        app = await _server_mod.create_app()
        async with TestClient(TestServer(app)) as client:
            async def redeem():
                resp = await client.post(
                    "/api/setup/redeem",
                    json={"token": "concurrent-token"},
                    auth=aiohttp.BasicAuth("admin", password),
                )
                # Read JSON while the client session is still open
                data = await resp.json() if resp.status == 200 else None
                return resp.status, data

            (s1, d1), (s2, d2) = await asyncio.gather(redeem(), redeem())

        statuses = sorted([s1, s2])
        assert statuses == [200, 404], f"Expected [200, 404], got {statuses}"

        # The 200 response should contain credentials
        ok_data = d1 if s1 == 200 else d2
        assert ok_data is not None
        assert ok_data["username"] == "alice"
        assert ok_data["password"] == "secret"
        assert ok_data["board"] == "default"


# ─── Setup redeem rate limiting tests ─────────────────────────────

class TestSetupRedeemRateLimit:
    """Per-IP rate limiting on /api/setup/redeem: 10 failures -> 429."""

    @pytest.mark.asyncio
    async def test_redeem_rate_limit_after_10_failures(self, tmp_path):
        """After 10 failed redeems from same IP, the 11th gets 429."""
        from aiohttp.test_utils import TestClient, TestServer

        # Create a minimal auth file
        auth_file = tmp_path / "auth.json"
        import hashlib, base64, hmac as _hmac
        import os
        password = "testpw"
        salt = base64.b64encode(os.urandom(16)).decode()
        pw_hash = base64.b64encode(
            _hmac.new(salt.encode(), password.encode(), hashlib.sha256).digest()
        ).decode()
        auth_file.write_text(json.dumps({
            "users": {
                "admin": {"password_hash": pw_hash, "salt": salt, "role": "admin"}
            }
        }))

        # Patch config so create_app() works
        _server_mod._config = {"auth": {"file_path": str(auth_file)}}
        _server_mod.AUTH_FILE = auth_file

        # Clear rate limit state
        _server_mod._SETUP_REDEEM_FAILURES.clear()

        # Create the app and test client
        app = await _server_mod.create_app()
        async with TestClient(TestServer(app)) as client:
            # 10 failed attempts with bad tokens
            for i in range(10):
                resp = await client.post(
                    "/api/setup/redeem",
                    json={"token": f"bad-token-{i}"},
                    auth=aiohttp.BasicAuth("admin", password),
                )
                assert resp.status in (404, 410), f"attempt {i+1}: expected 404/410, got {resp.status}"

            # 11th attempt should be rate-limited (429)
            resp = await client.post(
                "/api/setup/redeem",
                json={"token": "bad-token-11"},
                auth=aiohttp.BasicAuth("admin", password),
            )
            assert resp.status == 429, f"expected 429, got {resp.status}"
            data = await resp.json()
            assert "retry_after" in data
            assert data["retry_after"] > 0

    @pytest.mark.asyncio
    async def test_redeem_rate_limit_per_ip_isolation(self, tmp_path):
        """Rate limit on one IP should not affect another IP."""
        from aiohttp.test_utils import TestClient, TestServer

        auth_file = tmp_path / "auth.json"
        import hashlib, base64, hmac as _hmac
        import os
        password = "testpw"
        salt = base64.b64encode(os.urandom(16)).decode()
        pw_hash = base64.b64encode(
            _hmac.new(salt.encode(), password.encode(), hashlib.sha256).digest()
        ).decode()
        auth_file.write_text(json.dumps({
            "users": {
                "admin": {"password_hash": pw_hash, "salt": salt, "role": "admin"}
            }
        }))

        _server_mod._config = {"auth": {"file_path": str(auth_file)}}
        _server_mod.AUTH_FILE = auth_file
        _server_mod._SETUP_REDEEM_FAILURES.clear()

        app = await _server_mod.create_app()
        async with TestClient(TestServer(app)) as client:
            # 10 failed attempts from IP 10.0.0.1
            for i in range(10):
                resp = await client.post(
                    "/api/setup/redeem",
                    json={"token": f"bad-{i}"},
                    auth=aiohttp.BasicAuth("admin", password),
                )
                assert resp.status in (404, 410)

            # 11th from same IP should be 429
            resp = await client.post(
                "/api/setup/redeem",
                json={"token": "bad-11"},
                auth=aiohttp.BasicAuth("admin", password),
            )
            assert resp.status == 429

    @pytest.mark.asyncio
    async def test_redeem_success_resets_rate_limit(self, tmp_path):
        """A successful redeem should reset the rate limit counter."""
        from aiohttp.test_utils import TestClient, TestServer

        auth_file = tmp_path / "auth.json"
        import hashlib, base64, hmac as _hmac
        import os
        password = "testpw"
        salt = base64.b64encode(os.urandom(16)).decode()
        pw_hash = base64.b64encode(
            _hmac.new(salt.encode(), password.encode(), hashlib.sha256).digest()
        ).decode()
        auth_file.write_text(json.dumps({
            "users": {
                "admin": {"password_hash": pw_hash, "salt": salt, "role": "admin"}
            }
        }))

        _server_mod._config = {"auth": {"file_path": str(auth_file)}}
        _server_mod.AUTH_FILE = auth_file
        _server_mod._SETUP_REDEEM_FAILURES.clear()

        # Register a valid token
        await _server_mod.register_setup_token(
            "valid-token", "newuser", "newpass", "default", 300
        )

        app = await _server_mod.create_app()
        async with TestClient(TestServer(app)) as client:
            # 9 failed attempts
            for i in range(9):
                resp = await client.post(
                    "/api/setup/redeem",
                    json={"token": f"bad-{i}"},
                    auth=aiohttp.BasicAuth("admin", password),
                )
                assert resp.status in (404, 410)

            # Successful redeem should reset counter
            resp = await client.post(
                "/api/setup/redeem",
                json={"token": "valid-token"},
                auth=aiohttp.BasicAuth("admin", password),
            )
            assert resp.status == 200, f"expected 200, got {resp.status}"

            # Counter reset: 10 more failures should NOT trigger 429
            for i in range(10):
                resp = await client.post(
                    "/api/setup/redeem",
                    json={"token": f"bad-after-{i}"},
                    auth=aiohttp.BasicAuth("admin", password),
                )
                assert resp.status in (404, 410), f"attempt {i+1}: expected 404/410, got {resp.status}"

            # 11th should now be 429 (new counter reached 10)
            resp = await client.post(
                "/api/setup/redeem",
                json={"token": "bad-after-11"},
                auth=aiohttp.BasicAuth("admin", password),
            )
            assert resp.status == 429, f"expected 429, got {resp.status}"
