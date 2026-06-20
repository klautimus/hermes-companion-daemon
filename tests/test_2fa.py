#!/usr/bin/env python3
"""Tests for email 2FA backend (Plan 017).

Covers: OTP generation, challenge lifecycle (create/verify/expire),
Gmail API send mocking, 2FA enable/disable, and the /api/auth/2fa/*
endpoint contracts.
"""

import asyncio
import base64
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# ── Bootstrap: make companion importable (same pattern as test_auth_hardening) ──

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
assert _server_spec is not None, "Failed to load server.py spec"
_server_mod = importlib.util.module_from_spec(_server_spec)
sys.modules["companion.server"] = _server_mod
_server_spec.loader.exec_module(_server_mod)

# Now import the modules under test
BasicAuth = _server_mod.BasicAuth

# Add companion dir to path so `import email_2fa` works once it exists
if str(_companion_dir) not in sys.path:
    sys.path.insert(0, str(_companion_dir))


def _get_email_2fa():
    """Lazily import email_2fa — raises ImportError if not yet created."""
    import email_2fa
    return email_2fa


def _get_2fa_handler(name):
    """Lazily import a 2FA handler from server.py."""
    import importlib
    _server_spec = importlib.util.spec_from_file_location(
        "companion.server",
        str(_companion_dir / "server.py"),
        submodule_search_locations=[],
    )
    _mod = importlib.util.module_from_spec(_server_spec)
    _server_spec.loader.exec_module(_mod)
    return {
        "verify": _mod.handle_2fa_verify,
        "setup": _mod.handle_2fa_setup,
        "disable": _mod.handle_2fa_disable,
        "resend": _mod.handle_2fa_resend,
    }[name]


# ── Helpers ────────────────────────────────────────────────────────


def _make_request(auth_header=None, remote="127.0.0.1", path="/api/test"):
    req = MagicMock()
    if auth_header is not None:
        req.headers = {"Authorization": auth_header}
    else:
        req.headers = {}
    req.remote = remote
    req.path = path
    return req


def _basic_auth(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"


def _scrypt_hash(password, n=16384, r=8, p=1):
    import secrets as _secrets
    salt = _secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b64 = base64.b64encode(h).decode()
    return f"scrypt${n}${r}${p}${salt.hex()}${b64}"


@pytest.fixture
def auth_file(tmp_path):
    """Create a temporary auth.json and return its path."""
    return tmp_path / "auth.json"


@pytest.fixture
def basic_auth(auth_file):
    """Create a BasicAuth instance with one test user (no 2FA)."""
    phash = _scrypt_hash("testpass123", n=16384)
    auth_data = {
        "users": {
            "admin": {
                "password_hash": phash,
                "created_at": "2026-01-01",
            }
        }
    }
    auth_file.write_text(json.dumps(auth_data))
    return BasicAuth(auth_file)


# ─── OTP Challenge Tests ─────────────────────────────────────────

class TestOTPChallengeGeneration:
    """Test the email_2fa module's challenge generation."""

    def test_import_email_2fa(self):
        """email_2fa module should be importable."""
        email_2fa = _get_email_2fa()
        assert hasattr(email_2fa, "generate_challenge")
        assert hasattr(email_2fa, "verify_otp")
        assert hasattr(email_2fa, "send_otp")

    def test_generate_challenge_returns_id(self):
        email_2fa = _get_email_2fa()
        challenge_id = email_2fa.generate_challenge("test@example.com")
        assert isinstance(challenge_id, str)
        assert len(challenge_id) > 0

    def test_generate_challenge_unique_each_time(self):
        email_2fa = _get_email_2fa()
        c1 = email_2fa.generate_challenge("test@example.com")
        c2 = email_2fa.generate_challenge("test@example.com")
        assert c1 != c2

    def test_verify_otp_correct_code(self):
        email_2fa = _get_email_2fa()
        challenge_id = email_2fa.generate_challenge("test@example.com")
        challenge = email_2fa._pending_challenges.get(challenge_id)
        assert challenge is not None
        code = challenge["code"]
        assert email_2fa.verify_otp(challenge_id, code) is True

    def test_verify_otp_wrong_code(self):
        email_2fa = _get_email_2fa()
        challenge_id = email_2fa.generate_challenge("test@example.com")
        assert email_2fa.verify_otp(challenge_id, "000000") is False

    def test_verify_otp_expired_challenge(self):
        email_2fa = _get_email_2fa()
        challenge_id = email_2fa.generate_challenge("test@example.com")
        email_2fa._pending_challenges[challenge_id]["expires"] = time.time() - 1
        challenge = email_2fa._pending_challenges[challenge_id]
        assert email_2fa.verify_otp(challenge_id, challenge["code"]) is False

    def test_verify_otp_consumes_challenge(self):
        """After successful verify, challenge should be deleted (single-use)."""
        email_2fa = _get_email_2fa()
        challenge_id = email_2fa.generate_challenge("test@example.com")
        challenge = email_2fa._pending_challenges[challenge_id]
        email_2fa.verify_otp(challenge_id, challenge["code"])
        assert challenge_id not in email_2fa._pending_challenges

    def test_verify_otp_unknown_challenge(self):
        email_2fa = _get_email_2fa()
        assert email_2fa.verify_otp("nonexistent", "123456") is False

    def test_otp_is_6_digits(self):
        email_2fa = _get_email_2fa()
        for _ in range(20):
            challenge_id = email_2fa.generate_challenge("test@example.com")
            code = email_2fa._pending_challenges[challenge_id]["code"]
            assert len(code) == 6, f"code '{code}' is not 6 digits"
            assert code.isdigit(), f"code '{code}' contains non-digit chars"


# ─── Gmail API Send Tests ────────────────────────────────────────

class TestGmailOTPSend:
    """Test that send_otp calls the Gmail API correctly."""

    @pytest.mark.asyncio
    async def test_send_otp_calls_gmail_api(self, tmp_path):
        """send_otp should build and send a Gmail message with the OTP code."""
        email_2fa = _get_email_2fa()

        # Create a fake token file
        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "fake-token",
            "refresh_token": "fake-refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake-client-id",
            "client_secret": "fake-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            "type": "authorized_user",
        }))

        challenge_id = email_2fa.generate_challenge("kevin@example.com")
        code = email_2fa._pending_challenges[challenge_id]["code"]

        mock_service = MagicMock()
        mock_send = mock_service.users().messages().send
        mock_send.return_value.execute.return_value = {"id": "msg123"}

        with patch("email_2fa._load_gmail_service", return_value=mock_service):
            with patch("email_2fa.TOKEN_FILE", token_file):
                email_2fa.send_otp(challenge_id)

        # Verify Gmail API was called
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["userId"] == "me"
        # The raw message should contain the OTP code
        import base64 as _b64
        raw_msg = _b64.urlsafe_b64decode(call_kwargs["body"]["raw"].encode()).decode()
        assert code in raw_msg
        assert "kevin@example.com" in raw_msg

    @pytest.mark.asyncio
    async def test_send_otp_expired_challenge_raises(self, tmp_path):
        """send_otp on an expired/nonexistent challenge should raise."""
        email_2fa = _get_email_2fa()
        with pytest.raises(KeyError):
            email_2fa.send_otp("nonexistent-challenge-id")


# ─── Auth.json 2FA Flag Tests ────────────────────────────────────

class TestAuthJson2FAFlag:
    """Test that auth.json supports the two_factor_enabled field."""

    def test_basic_auth_loads_2fa_flag(self, auth_file):
        """BasicAuth should read two_factor_enabled from user data."""
        phash = _scrypt_hash("testpass123", n=16384)
        auth_data = {
            "users": {
                "admin": {
                    "password_hash": phash,
                    "created_at": "2026-01-01",
                    "two_factor_enabled": True,
                }
            }
        }
        auth_file.write_text(json.dumps(auth_data))
        ba = BasicAuth(auth_file)
        user = ba._users.get("admin")
        assert user.get("two_factor_enabled") is True

    def test_basic_auth_defaults_2fa_false(self, basic_auth):
        """When two_factor_enabled is absent, it should default to False."""
        user = basic_auth._users.get("admin")
        assert user.get("two_factor_enabled", False) is False


# ─── 2FA Middleware Integration Tests ────────────────────────────

class Test2FAMiddleware:
    """Test that BasicAuth.check returns 2FA challenge for users with 2FA enabled."""

    @pytest.mark.asyncio
    async def test_2fa_user_gets_challenge(self, auth_file):
        """User with 2FA enabled should get a challenge_id instead of direct auth."""
        phash = _scrypt_hash("testpass123", n=16384)
        auth_data = {
            "users": {
                "admin": {
                    "password_hash": phash,
                    "created_at": "2026-01-01",
                    "two_factor_enabled": True,
                }
            }
        }
        auth_file.write_text(json.dumps(auth_data))
        ba = BasicAuth(auth_file)

        req = _make_request(_basic_auth("admin", "testpass123"), remote="10.0.0.1")

        # Mock the email_2fa functions so we don't need a real Gmail token
        with patch("email_2fa.generate_challenge", return_value="test-challenge-id"):
            with patch("email_2fa.send_otp"):
                result = await ba.check(req)

        # With 2FA enabled, check() should return a dict with requires_2fa
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("requires_2fa") is True
        assert "challenge_id" in result

    @pytest.mark.asyncio
    async def test_non_2fa_user_auth_directly(self, basic_auth):
        """User without 2FA should get normal boolean True on success."""
        req = _make_request(_basic_auth("admin", "testpass123"), remote="10.0.0.1")
        result = await basic_auth.check(req)
        assert result is True

    @pytest.mark.asyncio
    async def test_2fa_user_wrong_password_still_fails(self, auth_file):
        """Wrong password should still fail even for 2FA users."""
        phash = _scrypt_hash("testpass123", n=16384)
        auth_data = {
            "users": {
                "admin": {
                    "password_hash": phash,
                    "created_at": "2026-01-01",
                    "two_factor_enabled": True,
                }
            }
        }
        auth_file.write_text(json.dumps(auth_data))
        ba = BasicAuth(auth_file)

        req = _make_request(_basic_auth("admin", "wrongpass"), remote="10.0.0.1")
        result = await ba.check(req)
        assert result is False


# ─── Endpoint Contract Tests ─────────────────────────────────────

class Test2FAEndpoints:
    """Test the /api/auth/2fa/* endpoint handlers."""

    @pytest.mark.asyncio
    async def test_verify_correct_otp_returns_200(self, tmp_path):
        """POST /api/auth/2fa/verify with correct OTP should return 200."""
        email_2fa = _get_email_2fa()
        handle_2fa_verify = _get_2fa_handler("verify")

        challenge_id = email_2fa.generate_challenge("admin@local")
        code = email_2fa._pending_challenges[challenge_id]["code"]

        req = MagicMock()
        req.json = AsyncMock(return_value={
            "challenge_id": challenge_id,
            "code": code,
        })

        result = await handle_2fa_verify(req)
        assert result.status == 200

    @pytest.mark.asyncio
    async def test_verify_wrong_otp_returns_401(self, tmp_path):
        """POST /api/auth/2fa/verify with wrong OTP should return 401."""
        email_2fa = _get_email_2fa()
        handle_2fa_verify = _get_2fa_handler("verify")

        challenge_id = email_2fa.generate_challenge("admin@local")

        req = MagicMock()
        req.json = AsyncMock(return_value={
            "challenge_id": challenge_id,
            "code": "000000",
        })

        result = await handle_2fa_verify(req)
        assert result.status == 401

    @pytest.mark.asyncio
    async def test_verify_expired_otp_returns_401(self, tmp_path):
        """POST /api/auth/2fa/verify with expired OTP should return 401."""
        email_2fa = _get_email_2fa()
        handle_2fa_verify = _get_2fa_handler("verify")

        challenge_id = email_2fa.generate_challenge("admin@local")
        email_2fa._pending_challenges[challenge_id]["expires"] = time.time() - 1
        code = email_2fa._pending_challenges[challenge_id]["code"]

        req = MagicMock()
        req.json = AsyncMock(return_value={
            "challenge_id": challenge_id,
            "code": code,
        })

        result = await handle_2fa_verify(req)
        assert result.status == 401

    @pytest.mark.asyncio
    async def test_verify_missing_challenge_returns_401(self):
        """POST /api/auth/2fa/verify with unknown challenge_id returns 401."""
        handle_2fa_verify = _get_2fa_handler("verify")

        req = MagicMock()
        req.json = AsyncMock(return_value={
            "challenge_id": "nonexistent",
            "code": "123456",
        })

        result = await handle_2fa_verify(req)
        assert result.status == 401


# ─── Cleanup between tests ──────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_challenges():
    """Clear the pending challenges dict between tests."""
    try:
        import email_2fa
        email_2fa._pending_challenges.clear()
    except ImportError:
        pass  # email_2fa not yet created
    yield
    try:
        import email_2fa
        email_2fa._pending_challenges.clear()
    except ImportError:
        pass
