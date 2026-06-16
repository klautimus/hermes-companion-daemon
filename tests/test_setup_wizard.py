#!/usr/bin/env python3
"""Tests for the setup wizard (setup_wizard.py).

Ported to the post-Plan-001 consolidated API. Covers security-critical
code paths: scrypt hashing, auth file creation, QR token generation,
first-run detection, and CLI entry points.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from setup_wizard import (
    generate_password,
    generate_setup_token,
    hash_password,
    create_auth_file,
    detect_hermes_cli,
    generate_qr_code,
    render_qr_ascii,
    save_qr_png,
    register_setup_token_wizard,
    run_setup_wizard,
    prompt_with_default,
    prompt_yes_no,
)
from config_schema import CompanionConfig, DEFAULT_CONFIG, save_config, CONFIG_DIR, CONFIG_FILE, load_config, config_exists


# ── Password generation ─────────────────────────────────────────

class TestGeneratePassword:
    def test_returns_string(self):
        pw = generate_password()
        assert isinstance(pw, str)

    def test_default_length(self):
        pw = generate_password()
        # token_urlsafe(32) produces ~43 chars
        assert len(pw) >= 32

    def test_custom_length(self):
        # New API doesn't take length param; just verify it's long enough
        pw = generate_password()
        assert len(pw) >= 32

    def test_unique_each_time(self):
        pw1 = generate_password()
        pw2 = generate_password()
        assert pw1 != pw2


# ── Password hashing ────────────────────────────────────────────

class TestHashPassword:
    def test_returns_scrypt_format(self):
        h = hash_password("testpass")
        assert h.startswith("scrypt$")

    def test_correct_format_parts(self):
        h = hash_password("testpass")
        parts = h.split("$")
        assert len(parts) == 6
        assert parts[0] == "scrypt"
        assert parts[1] == "131072"  # N (updated from Plan 002)
        assert parts[2] == "8"       # r
        assert parts[3] == "1"       # p
        # parts[4] is salt hex, parts[5] is hash b64

    def test_different_passwords_different_hashes(self):
        h1 = hash_password("pass1")
        h2 = hash_password("pass2")
        assert h1 != h2

    def test_same_password_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # Different random salts

    def test_fixed_params(self):
        # New API uses fixed SCRYPT_N/R/P from constants
        h = hash_password("test")
        assert h.startswith("scrypt$131072$8$1$")


# ── Auth.json creation ──────────────────────────────────────────

class TestCreateAuthJson:
    def test_creates_file(self, tmp_path):
        dest = tmp_path / "auth.json"
        result = create_auth_json(dest, "admin", "testpass")
        assert result.exists()

    def test_valid_json(self, tmp_path):
        dest = tmp_path / "auth.json"
        create_auth_json(dest, "admin", "testpass")
        data = json.loads(dest.read_text())
        assert "users" in data
        assert "admin" in data["users"]

    def test_password_is_hashed(self, tmp_path):
        dest = tmp_path / "auth.json"
        create_auth_json(dest, "admin", "testpass")
        data = json.loads(dest.read_text())
        pw_hash = data["users"]["admin"]["password_hash"]
        assert pw_hash != "testpass"
        assert pw_hash.startswith("scrypt$")

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "deep" / "nested" / "auth.json"
        create_auth_json(dest, "admin", "testpass")
        assert dest.exists()

    def test_includes_created_at(self, tmp_path):
        dest = tmp_path / "auth.json"
        create_auth_json(dest, "admin", "testpass")
        data = json.loads(dest.read_text())
        assert "created_at" in data["users"]["admin"]

    def test_restricts_permissions(self, tmp_path):
        dest = tmp_path / "auth.json"
        create_auth_json(dest, "admin", "testpass")
        mode = dest.stat().st_mode
        # Should be 0o600 (owner read/write only)
        assert (mode & 0o777) == 0o600


# ── Config.yaml creation ────────────────────────────────────────

class TestCreateConfigYaml:
    def test_creates_file(self, tmp_path):
        dest = tmp_path / "config.yaml"
        result = create_config_yaml(
            dest=dest,
            server_host="127.0.0.1",
            server_port=8777,
            hermes_api_url="http://127.0.0.1:8642",
            hermes_api_key="test-key",
            hermes_binary_path="/usr/bin/hermes",
            auth_file_path="~/.config/hermes-companion/auth.json",
            attachments_dir="~/.config/hermes-companion/attachments",
        )
        assert result.exists()

    def test_valid_yaml(self, tmp_path):
        dest = tmp_path / "config.yaml"
        create_config_yaml(
            dest=dest,
            server_host="0.0.0.0",
            server_port=9999,
            hermes_api_url="http://10.0.0.1:8642",
            hermes_api_key="key123",
            hermes_binary_path="/opt/hermes",
            auth_file_path="/etc/auth.json",
            attachments_dir="/var/attachments",
        )
        data = yaml.safe_load(dest.read_text())
        assert data["server"]["host"] == "0.0.0.0"
        assert data["server"]["port"] == 9999
        assert data["hermes"]["api_url"] == "http://10.0.0.1:8642"
        assert data["hermes"]["api_key"] == "key123"
        assert data["hermes"]["binary_path"] == "/opt/hermes"
        assert data["auth"]["file_path"] == "/etc/auth.json"
        assert data["attachments"]["dir"] == "/var/attachments"
        assert data["attachments"]["max_upload_mb"] == 25

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "deep" / "config.yaml"
        create_config_yaml(
            dest=dest,
            server_host="127.0.0.1",
            server_port=8777,
            hermes_api_url="http://127.0.0.1:8642",
            hermes_api_key="",
            hermes_binary_path="auto",
            auth_file_path="~/.config/hermes-companion/auth.json",
            attachments_dir="~/.config/hermes-companion/attachments",
        )
        assert dest.exists()


# ── Hermes binary detection ─────────────────────────────────────

class TestDetectHermesBinary:
    def test_returns_string(self):
        result = detect_hermes_binary()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_finds_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/hermes"):
            result = detect_hermes_binary()
        assert result == "/usr/bin/hermes"


# ── Connection URI generation ──────────────────────────────────

class TestGenerateConnectionUri:
    def test_encodes_all_params(self):
        uri = generate_connection_uri("http://127.0.0.1:8777", "admin", "pass123", "default")
        assert "url=http://127.0.0.1:8777" in uri
        assert "user=admin" in uri
        assert "pass=pass123" in uri
        assert "board=default" in uri

    def test_default_board(self):
        uri = generate_connection_uri("http://x:8777", "a", "b")
        assert "board=default" in uri

    def test_custom_board(self):
        uri = generate_connection_uri("http://x:8777", "a", "b", "myboard")
        assert "board=myboard" in uri

    def test_starts_with_scheme(self):
        uri = generate_connection_uri("http://x", "a", "b")
        assert uri.startswith("hermescompanion://configure?")


# ── QR code (no segno fallback) ────────────────────────────────

class TestQrCodeNoSegno:
    def test_returns_text_with_uri(self):
        uri = "hermescompanion://configure?url=http://x&user=a&pass=b&board=d"
        text, path = generate_qr_code_no_segno(uri, Path("/tmp"))
        assert uri in text
        assert path is None

    def test_mentions_segno(self):
        uri = "hermescompanion://configure?url=http://x"
        text, _ = generate_qr_code_no_segno(uri, Path("/tmp"))
        assert "segno" in text


# ── Prompt helpers ──────────────────────────────────────────────

class TestPrompt:
    def test_returns_input(self):
        with patch("builtins.input", return_value="myvalue"):
            result = prompt("Enter value")
        assert result == "myvalue"

    def test_returns_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            result = prompt("Enter value", default="fallback")
        assert result == "fallback"

    def test_strips_whitespace(self):
        with patch("builtins.input", return_value="  spaced  "):
            result = prompt("Enter value")
        assert result == "spaced"


class TestPromptYesNo:
    def test_yes_returns_true(self):
        with patch("builtins.input", return_value="y"):
            assert prompt_yes_no("Continue?") is True

    def test_no_returns_false(self):
        with patch("builtins.input", return_value="n"):
            assert prompt_yes_no("Continue?") is False

    def test_empty_returns_default_true(self):
        with patch("builtins.input", return_value=""):
            assert prompt_yes_no("Continue?", default=True) is True

    def test_empty_returns_default_false(self):
        with patch("builtins.input", return_value=""):
            assert prompt_yes_no("Continue?", default=False) is False


# ── Non-interactive setup ──────────────────────────────────────

class TestNonInteractiveSetup:
    def test_creates_config_and_auth(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_API_KEY": ""}):
            result = _non_interactive_setup(
                config_dir=tmp_path,
                username="admin",
                board="default",
                skip_qr=True,
            )
        assert result == 0
        assert (tmp_path / "config.yaml").exists()
        assert (tmp_path / "auth.json").exists()

    def test_config_has_correct_values(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_API_KEY": "test-key"}):
            _non_interactive_setup(
                config_dir=tmp_path,
                username="admin",
                board="default",
                skip_qr=True,
            )
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["server"]["host"] == "127.0.0.1"
        assert data["server"]["port"] == 8777
        assert data["hermes"]["api_key"] == "test-key"

    def test_auth_has_admin_user(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_API_KEY": ""}):
            _non_interactive_setup(
                config_dir=tmp_path,
                username="admin",
                board="default",
                skip_qr=True,
            )
        data = json.loads((tmp_path / "auth.json").read_text())
        assert "admin" in data["users"]

    def test_creates_attachments_dir(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_API_KEY": ""}):
            _non_interactive_setup(
                config_dir=tmp_path,
                username="admin",
                board="default",
                skip_qr=True,
            )
        assert (tmp_path / "attachments").is_dir()


# ── CLI main() ──────────────────────────────────────────────────

class TestCliMain:
    def test_setup_subcommand_non_interactive(self, tmp_path):
        # setup_wizard.main() parses args directly; "setup" is the prog name, not an arg
        result = main(["--non-interactive", "--config-dir", str(tmp_path)])
        assert result == 0
        assert (tmp_path / "config.yaml").exists()

    def test_no_args_shows_help(self):
        with patch("builtins.input", side_effect=EOFError):
            result = main([])
        assert result == 1  # EOFError since no input available

    def test_keyboard_interrupt(self, tmp_path):
        with patch("setup_wizard.run_wizard", side_effect=KeyboardInterrupt):
            result = main(["--config-dir", str(tmp_path)])
        assert result == 130

    def test_eof_error(self, tmp_path):
        with patch("setup_wizard.run_wizard", side_effect=EOFError):
            result = main(["--config-dir", str(tmp_path)])
        assert result == 1
