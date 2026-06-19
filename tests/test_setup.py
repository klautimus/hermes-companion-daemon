#!/usr/bin/env python3
"""Tests for the /api/setup/register endpoint (Plan 009+)."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent dir for direct imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import handle_setup_register


def _make_register_request(username="admin", password="testpass123"):
    """Create a mock request for /api/setup/register."""
    req = MagicMock()
    req.json = AsyncMock(return_value={"username": username, "password": password})
    req.match_info = {}
    req.query = MagicMock()
    req.query.get = MagicMock(return_value="")
    req.app = {"config": MagicMock()}
    req.app["config"].get_expanded_paths.return_value = {
        "auth_file": Path(tempfile.mkdtemp()) / "auth.json",
    }
    return req


@pytest.mark.asyncio
async def test_register_first_user():
    """First user registration should succeed with 201."""
    req = _make_register_request("admin", "testpass123")
    result = await handle_setup_register(req)
    assert result.status == 201
    body = json.loads(result.text)
    assert body["status"] == "ok"
    assert "admin" in body["message"]


@pytest.mark.asyncio
async def test_register_rejects_short_username():
    """Username < 3 chars should return 400."""
    req = _make_register_request("ab", "testpass123")
    result = await handle_setup_register(req)
    assert result.status == 400


@pytest.mark.asyncio
async def test_register_rejects_short_password():
    """Password < 8 chars should return 400."""
    req = _make_register_request("admin", "short")
    result = await handle_setup_register(req)
    assert result.status == 400


@pytest.mark.asyncio
async def test_register_rejects_missing_username():
    """Missing username should return 400."""
    req = MagicMock()
    req.json = AsyncMock(return_value={"password": "testpass123"})
    req.match_info = {}
    req.query = MagicMock()
    req.query.get = MagicMock(return_value="")
    req.app = {"config": MagicMock()}
    req.app["config"].get_expanded_paths.return_value = {
        "auth_file": Path(tempfile.mkdtemp()) / "auth.json",
    }
    result = await handle_setup_register(req)
    assert result.status == 400


@pytest.mark.asyncio
async def test_register_rejects_missing_password():
    """Missing password should return 400."""
    req = MagicMock()
    req.json = AsyncMock(return_value={"username": "admin"})
    req.match_info = {}
    req.query = MagicMock()
    req.query.get = MagicMock(return_value="")
    req.app = {"config": MagicMock()}
    req.app["config"].get_expanded_paths.return_value = {
        "auth_file": Path(tempfile.mkdtemp()) / "auth.json",
    }
    result = await handle_setup_register(req)
    assert result.status == 400


@pytest.mark.asyncio
async def test_register_blocked_when_users_exist():
    """Registration should return 403 when users already exist."""
    tmp_dir = Path(tempfile.mkdtemp())
    auth_file = tmp_dir / "auth.json"
    # Pre-populate auth.json with an existing user
    auth_file.write_text(json.dumps({
        "users": {
            "existing": {
                "password_hash": "scrypt$16384$8$1$abcd$efgh",
                "created_at": "2026-01-01",
            }
        }
    }))

    req = _make_register_request("admin", "testpass123")
    req.app["config"].get_expanded_paths.return_value = {
        "auth_file": auth_file,
    }
    result = await handle_setup_register(req)
    assert result.status == 403


@pytest.mark.asyncio
async def test_register_rejects_invalid_json():
    """Invalid JSON body should return 400."""
    req = MagicMock()
    req.json = AsyncMock(side_effect=Exception("invalid json"))
    req.match_info = {}
    req.query = MagicMock()
    req.query.get = MagicMock(return_value="")
    req.app = {"config": MagicMock()}
    req.app["config"].get_expanded_paths.return_value = {
        "auth_file": Path(tempfile.mkdtemp()) / "auth.json",
    }
    result = await handle_setup_register(req)
    assert result.status == 400


@pytest.mark.asyncio
async def test_register_creates_valid_auth_file():
    """Successful registration should create a valid auth.json."""
    req = _make_register_request("admin", "testpass123")
    auth_path = req.app["config"].get_expanded_paths.return_value["auth_file"]
    result = await handle_setup_register(req)
    assert result.status == 201
    # Verify auth.json was created
    assert auth_path.exists()
    auth_data = json.loads(auth_path.read_text())
    assert "admin" in auth_data["users"]
    # Verify password is hashed (not plaintext)
    pw = auth_data["users"]["admin"]["password_hash"]
    assert pw.startswith("scrypt$")
    assert "testpass123" not in pw
