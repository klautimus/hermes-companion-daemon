#!/usr/bin/env python3
"""Tests for the 5 new kanban CRUD endpoints (Plan 015)."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from server import (
    handle_kanban_task_create,
    handle_kanban_task_edit,
    handle_kanban_task_delete,
    handle_kanban_task_bulk,
    handle_kanban_link,
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


# ─── handle_kanban_task_create ─────────────────────────────────

class TestHandleKanbanTaskCreate:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_create_success(self, mock_kanban):
        mock_kanban.return_value = (0, '{"task": {"id": "t_abc", "title": "test task"}}', "")
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={"title": "test task"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 201
        body = json.loads(result.text)
        assert body["task"]["id"] == "t_abc"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, mock_kanban):
        mock_kanban.return_value = (0, '{"task": {"id": "t_def", "title": "full task"}}', "")
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={"title": "full task", "body": "desc", "assignee": "ops", "priority": 1, "status": "todo"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 201
        call_args = mock_kanban.call_args
        cmd = call_args[0][0]
        assert "create" in cmd
        assert "full task" in cmd
        assert "--assignee" in cmd
        assert "ops" in cmd

    @pytest.mark.asyncio
    async def test_create_missing_board(self):
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={"title": "test"},
            query={},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 422
        body = json.loads(result.text)
        assert "board" in body["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_create_missing_title(self):
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={},
            query={"board": "default"},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 422
        body = json.loads(result.text)
        assert "title" in body["error"]["message"].lower()

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_create_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "board not found")
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={"title": "test"},
            query={"board": "nonexistent"},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 500

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_create_non_json_response(self, mock_kanban):
        mock_kanban.return_value = (0, "not json", "")
        req = _make_request(
            "POST", "/api/kanban/tasks",
            data={"title": "test"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_create(req)
        assert result.status == 201
        body = json.loads(result.text)
        assert body["ok"] is True


# ─── handle_kanban_task_edit ───────────────────────────────────

class TestHandleKanbanTaskEdit:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_edit_status_done(self, mock_kanban):
        mock_kanban.return_value = (0, '{"ok": true}', "")
        req = _make_request(
            "PATCH", "/api/kanban/tasks/t_abc",
            data={"status": "done"},
            query={"board": "default"},
            match_info={"task_id": "t_abc"},
        )
        result = await handle_kanban_task_edit(req)
        assert result.status == 200

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_edit_assignee(self, mock_kanban):
        mock_kanban.return_value = (0, '{"task": {"id": "t_abc", "assignee": "ops"}}', "")
        req = _make_request(
            "PATCH", "/api/kanban/tasks/t_abc",
            data={"assignee": "ops"},
            query={"board": "default"},
            match_info={"task_id": "t_abc"},
        )
        result = await handle_kanban_task_edit(req)
        assert result.status == 200

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_edit_title_only(self, mock_kanban):
        mock_kanban.return_value = (0, '{"task": {"id": "t_abc", "title": "new title"}}', "")
        req = _make_request(
            "PATCH", "/api/kanban/tasks/t_abc",
            data={"title": "new title"},
            query={"board": "default"},
            match_info={"task_id": "t_abc"},
        )
        result = await handle_kanban_task_edit(req)
        assert result.status == 200

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_edit_complete_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request(
            "PATCH", "/api/kanban/tasks/t_bad",
            data={"status": "done"},
            query={"board": "default"},
            match_info={"task_id": "t_bad"},
        )
        result = await handle_kanban_task_edit(req)
        assert result.status == 500


# ─── handle_kanban_task_delete ─────────────────────────────────

class TestHandleKanbanTaskDelete:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_delete_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request(
            "DELETE", "/api/kanban/tasks/t_abc",
            query={"board": "default"},
            match_info={"task_id": "t_abc"},
        )
        result = await handle_kanban_task_delete(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["task_id"] == "t_abc"
        assert body["status"] == "archived"

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_delete_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "task not found")
        req = _make_request(
            "DELETE", "/api/kanban/tasks/t_bad",
            query={"board": "default"},
            match_info={"task_id": "t_bad"},
        )
        result = await handle_kanban_task_delete(req)
        assert result.status == 500


# ─── handle_kanban_task_bulk ───────────────────────────────────

class TestHandleKanbanTaskBulk:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_bulk_set_status_done(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": ["t_a", "t_b"], "action": "set_status", "value": "done"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["affected"] == 2
        assert body["total"] == 2

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_bulk_set_assignee(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": ["t_a"], "action": "set_assignee", "value": "ops"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["affected"] == 1

    @pytest.mark.asyncio
    async def test_bulk_missing_board(self):
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": ["t_a"], "action": "set_status", "value": "done"},
            query={},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 422

    @pytest.mark.asyncio
    async def test_bulk_missing_task_ids(self):
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": [], "action": "set_status", "value": "done"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 422

    @pytest.mark.asyncio
    async def test_bulk_invalid_action(self):
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": ["t_a"], "action": "invalid", "value": "x"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 422

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_bulk_partial_failure(self, mock_kanban):
        mock_kanban.side_effect = [(0, "", ""), (1, "", "not found")]
        req = _make_request(
            "POST", "/api/kanban/tasks/bulk",
            data={"task_ids": ["t_a", "t_b"], "action": "set_status", "value": "done"},
            query={"board": "default"},
        )
        result = await handle_kanban_task_bulk(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["affected"] == 1
        assert len(body["failed"]) == 1


# ─── handle_kanban_link ────────────────────────────────────────

class TestHandleKanbanLink:
    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_link_success(self, mock_kanban):
        mock_kanban.return_value = (0, "", "")
        req = _make_request(
            "POST", "/api/kanban/links",
            data={"parent_id": "t_a", "child_id": "t_b"},
            query={"board": "default"},
        )
        result = await handle_kanban_link(req)
        assert result.status == 200
        body = json.loads(result.text)
        assert body["ok"] is True
        assert body["parent_id"] == "t_a"
        assert body["child_id"] == "t_b"

    @pytest.mark.asyncio
    async def test_link_missing_parent_id(self):
        req = _make_request(
            "POST", "/api/kanban/links",
            data={"child_id": "t_b"},
            query={"board": "default"},
        )
        result = await handle_kanban_link(req)
        assert result.status == 422

    @pytest.mark.asyncio
    async def test_link_missing_child_id(self):
        req = _make_request(
            "POST", "/api/kanban/links",
            data={"parent_id": "t_a"},
            query={"board": "default"},
        )
        result = await handle_kanban_link(req)
        assert result.status == 422

    @patch("server._kanban")
    @pytest.mark.asyncio
    async def test_link_failure(self, mock_kanban):
        mock_kanban.return_value = (1, "", "link failed")
        req = _make_request(
            "POST", "/api/kanban/links",
            data={"parent_id": "t_a", "child_id": "t_b"},
            query={"board": "default"},
        )
        result = await handle_kanban_link(req)
        assert result.status == 500
