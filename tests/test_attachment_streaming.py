#!/usr/bin/env python3
"""Tests for attachment streaming upload (Plan 006).

Covers: early size abort via chunked read (413), small file success (201).
"""

import asyncio
import importlib.util
import io
import os
import sys
import types
from pathlib import Path

import aiohttp
import aiohttp.test_utils
import pytest

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

handle_attachment_upload = _server_mod.handle_attachment_upload
MAX_UPLOAD_SIZE = _server_mod.MAX_UPLOAD_SIZE
ATTACHMENTS_DIR = _server_mod.ATTACHMENTS_DIR


@pytest.fixture(autouse=True)
def reset_attachments_dir(tmp_path, monkeypatch):
    """Use a temp attachments dir for each test."""
    monkeypatch.setattr(_server_mod, "ATTACHMENTS_DIR", tmp_path)
    yield


@pytest.mark.asyncio
async def test_streaming_aborts_at_size_limit():
    """If the upload exceeds MAX_UPLOAD_SIZE, abort with 413 BEFORE reading all of it."""
    oversize = MAX_UPLOAD_SIZE + 1024
    payload = b"x" * oversize

    app = aiohttp.web.Application()
    app.router.add_post("/api/attachments", handle_attachment_upload)

    async with aiohttp.test_utils.TestClient(
        aiohttp.test_utils.TestServer(app)
    ) as client:
        data = aiohttp.FormData()
        data.add_field(
            "file",
            io.BytesIO(payload),
            filename="big.bin",
            content_type="application/octet-stream",
        )
        resp = await client.post("/api/attachments", data=data)
        assert resp.status == 413
        body = await resp.json()
        assert "exceeds" in body["error"]["message"]


@pytest.mark.asyncio
async def test_streaming_saves_small_file():
    """A small upload should succeed."""
    app = aiohttp.web.Application()
    app.router.add_post("/api/attachments", handle_attachment_upload)

    payload = b"hello world"
    async with aiohttp.test_utils.TestClient(
        aiohttp.test_utils.TestServer(app)
    ) as client:
        data = aiohttp.FormData()
        data.add_field(
            "file",
            io.BytesIO(payload),
            filename="hello.txt",
            content_type="text/plain",
        )
        resp = await client.post("/api/attachments", data=data)
        assert resp.status == 201
        body = await resp.json()
        assert body["filename"] == "hello.txt"
        assert body["size"] == len(payload)
