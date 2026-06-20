#!/usr/bin/env python3
"""Tests for the 5 new kanban lifecycle endpoints (Plan 016)."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from server import (
    handle_kanban_task_block,
    handle_kanban_task_unblock,
    handle_kanban_task_archive,
    handle_kanban_task_reclaim,
    handle_kanban_task_decompose,
)


def _make_request(method, path, data=None, query=None, match_info=None):
    """Create a mock aiohttp request."""
    req = MagicMock()
    req.method = method
    req.path = path
    req.match_info = match_info or {}
    req.query_string = ""
    req.headers = {}

    if data is not None:
        req.json = AsyncMock(return_value=data)
    else:
        req.json = AsyncMock(side_effect=Exception("no body"))

    req.query = MagicMock()
    q = query or {}
    req.query.get = MagicMock(side_effect=lambda k, d="": q.get(k, d))

    return req


# ─── handle_kanban_task_block ───────────────────────────────────

class TestHandleKanbanTaskBlock:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_block_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/block", query={"board": "default"})
        req.match_info = {"task_id": "task-1"}
        result = await handle_kanban_task_block(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "blocked"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_block_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request("POST", "/api/kanban/tasks/task-99/block", query={"board": "default"})
        req.match_info = {"task_id": "task-99"}
        result = await handle_kanban_task_block(req)
        assert result.status == 500
        body = json.loads(result.text)
        assert body["error"]["code"] == "INTERNAL_ERROR"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_block_passes_board_query(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/block", query={"board": "custom-board"})
        req.match_info = {"task_id": "task-1"}
        await handle_kanban_task_block(req)
        call_args = mock_kanban.call_args
        board_env = call_args[1].get("board")
        assert board_env == "custom-board"


# ─── handle_kanban_task_unblock ─────────────────────────────────

class TestHandleKanbanTaskUnblock:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_unblock_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/unblock", query={"board": "default"})
        req.match_info = {"task_id": "task-1"}
        result = await handle_kanban_task_unblock(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "ready"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_unblock_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request("POST", "/api/kanban/tasks/task-99/unblock", query={"board": "default"})
        req.match_info = {"task_id": "task-99"}
        result = await handle_kanban_task_unblock(req)
        assert result.status == 500


# ─── handle_kanban_task_archive ─────────────────────────────────

class TestHandleKanbanTaskArchive:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_archive_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/archive", query={"board": "default"})
        req.match_info = {"task_id": "task-1"}
        result = await handle_kanban_task_archive(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "archived"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_archive_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request("POST", "/api/kanban/tasks/task-99/archive", query={"board": "default"})
        req.match_info = {"task_id": "task-99"}
        result = await handle_kanban_task_archive(req)
        assert result.status == 500


# ─── handle_kanban_task_reclaim ─────────────────────────────────

class TestHandleKanbanTaskReclaim:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_reclaim_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/reclaim", query={"board": "default"})
        req.match_info = {"task_id": "task-1"}
        result = await handle_kanban_task_reclaim(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "ready"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_reclaim_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request("POST", "/api/kanban/tasks/task-99/reclaim", query={"board": "default"})
        req.match_info = {"task_id": "task-99"}
        result = await handle_kanban_task_reclaim(req)
        assert result.status == 500


# ─── handle_kanban_task_decompose ───────────────────────────────

class TestHandleKanbanTaskDecompose:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_decompose_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request("POST", "/api/kanban/tasks/task-1/decompose", query={"board": "default"})
        req.match_info = {"task_id": "task-1"}
        result = await handle_kanban_task_decompose(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "decomposed"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_decompose_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request("POST", "/api/kanban/tasks/task-99/decompose", query={"board": "default"})
        req.match_info = {"task_id": "task-99"}
        result = await handle_kanban_task_decompose(req)
        assert result.status == 500
