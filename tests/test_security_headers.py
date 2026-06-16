#!/usr/bin/env python3
"""Tests for security headers middleware (Plan 009).

Covers: all security headers present on /healthz, handler can override
security headers (middleware respects existing values).
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Bootstrap: make companion importable as a package ──────────────
_companion_dir = Path(__file__).parent.parent

# Create a fake parent package
_companion_pkg = types.ModuleType("companion")
_companion_pkg.__path__ = [str(_companion_dir)]
_companion_pkg.__package__ = "companion"
sys.modules["companion"] = _companion_pkg

# Create stub sub-modules
_stub_config = types.ModuleType("companion.config_schema")
_stub_config.load_config = lambda: None
_stub_config.MODULE_DIR = Path("/tmp")

_stub_first_run = types.ModuleType("companion.first_run")
_stub_first_run.ensure_configured_or_exit = lambda: None

sys.modules["companion.config_schema"] = _stub_config
sys.modules["companion.first_run"] = _stub_first_run

# Now load server.py as companion.server
_server_spec = importlib.util.spec_from_file_location(
    "companion.server",
    str(_companion_dir / "server.py"),
    submodule_search_locations=[],
)
_server_mod = importlib.util.module_from_spec(_server_spec)
sys.modules["companion.server"] = _server_mod
_server_spec.loader.exec_module(_server_mod)

create_app = _server_mod.create_app
security_headers_middleware = _server_mod.security_headers_middleware


# ── Tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_security_headers_present_on_healthz():
    """Health endpoint should set security headers."""
    app = await create_app()
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "geolocation=()" in resp.headers.get("Permissions-Policy", "")


@pytest.mark.asyncio
async def test_handler_can_override_security_header():
    """If a handler sets its own CSP, the middleware respects it."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def custom_handler(request):
        return web.Response(
            text="ok",
            headers={"Content-Security-Policy": "default-src 'none'"},
        )

    app = web.Application(middlewares=[security_headers_middleware])
    app.router.add_get("/custom", custom_handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/custom")
        assert resp.headers.get("Content-Security-Policy") == "default-src 'none'"
