#!/usr/bin/env python3
"""Tests for attachment-to-message chat integration (Plan 018).

Covers:
- handle_chat_proxy stores attachment_ids keyed by session_id
- handle_session_messages injects attachment_url for sessions with attachments
- handle_session_messages passes through unchanged when no attachments
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aiohttp import web

from server import (
    handle_chat_proxy,
    handle_session_messages,
    _attachment_store,
)


def _make_request(method, path, data=None, query=None, match_info=None, body=None):
    """Create a mock aiohttp request."""
    req = MagicMock()
    req.method = method
    req.path = path
    req.match_info = match_info or {}
    req.query_string = ""
    req.headers = {"Content-Type": "application/json"}

    if body is not None:
        req.read = AsyncMock(return_value=body)
    else:
        req.read = AsyncMock(return_value=b"")

    if data is not None:
        req.json = AsyncMock(return_value=data)
    else:
        req.json = AsyncMock(side_effect=Exception("no body"))

    req.query = MagicMock()
    q = query or {}
    req.query.get = MagicMock(side_effect=lambda k, d="": q.get(k, d))

    return req


# ─── handle_chat_proxy: attachment storage ─────────────────────

class TestHandleChatProxyAttachmentStorage:
    def setup_method(self):
        """Clear attachment store before each test."""
        _attachment_store.clear()

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_stores_attachment_ids_with_session(self, mock_proxy):
        """Chat request with attachment_ids + session_id should store mapping."""
        # Mock the upstream Hermes response
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "application/json"}
        mock_upstream.read = AsyncMock(
            return_value=b'{"choices":[{"message":{"content":"hello"}}]}'
        )
        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "session_id": "sess_abc123",
            "attachment_ids": ["att_xyz789"],
        }).encode()

        req = _make_request("POST", "/v1/chat/completions", body=body)
        result = await handle_chat_proxy(req)

        # Attachment should be stored
        assert "sess_abc123" in _attachment_store
        assert _attachment_store["sess_abc123"] == ["att_xyz789"]
        # Response should be from upstream
        assert result.status == 200

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_no_attachment_ids_passthrough(self, mock_proxy):
        """Chat request without attachment_ids should not store anything."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "application/json"}
        mock_upstream.read = AsyncMock(
            return_value=b'{"choices":[{"message":{"content":"hello"}}]}'
        )
        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }).encode()

        req = _make_request("POST", "/v1/chat/completions", body=body)
        result = await handle_chat_proxy(req)

        # No attachments stored
        assert len(_attachment_store) == 0
        assert result.status == 200

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_multiple_attachments_stored(self, mock_proxy):
        """Chat request with multiple attachment_ids should store all."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "application/json"}
        mock_upstream.read = AsyncMock(
            return_value=b'{"choices":[{"message":{"content":"hello"}}]}'
        )
        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "session_id": "sess_multi",
            "attachment_ids": ["att_one", "att_two", "att_three"],
        }).encode()

        req = _make_request("POST", "/v1/chat/completions", body=body)
        await handle_chat_proxy(req)

        assert _attachment_store["sess_multi"] == ["att_one", "att_two", "att_three"]

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_strips_attachment_fields_from_forwarded_body(self, mock_proxy):
        """attachment_ids and session_id should NOT be forwarded to Hermes."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "application/json"}
        mock_upstream.read = AsyncMock(
            return_value=b'{"choices":[{"message":{"content":"hello"}}]}'
        )
        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "session_id": "sess_strip",
            "attachment_ids": ["att_strip"],
        }).encode()

        req = _make_request("POST", "/v1/chat/completions", body=body)
        await handle_chat_proxy(req)

        # Check what was forwarded to Hermes
        call_args = mock_session.request.call_args
        forwarded_body = call_args.kwargs.get("data") or call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("data")
        forwarded_data = json.loads(forwarded_body)
        assert "attachment_ids" not in forwarded_data
        assert "session_id" not in forwarded_data
        assert "model" in forwarded_data
        assert "messages" in forwarded_data


# ─── handle_session_messages: attachment URL injection ─────────

class TestHandleSessionMessagesAttachmentInjection:
    def setup_method(self):
        """Clear attachment store before each test."""
        _attachment_store.clear()

    @pytest.mark.asyncio
    async def test_injects_attachment_url_for_session_with_attachments(self):
        """Session messages should include attachment_url for sessions with attachments."""
        _attachment_store["sess_att"] = ["att_111"]

        # Mock the HermesProxy response
        upstream_data = {
            "data": [
                {"role": "user", "content": "see this image"},
                {"role": "assistant", "content": "I see it"},
            ]
        }

        with patch("server.HermesProxy.forward") as mock_forward:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.body = json.dumps(upstream_data).encode()
            mock_forward.return_value = mock_resp

            req = _make_request(
                "GET", "/api/sessions/sess_att/messages",
                match_info={"session_id": "sess_att"},
            )
            result = await handle_session_messages(req)

        assert result.status == 200
        body = json.loads(result.text)
        # Last user message should have attachment_url
        user_msg = body["data"][0]
        assert user_msg["role"] == "user"
        assert user_msg["attachment_url"] == "/api/attachments/att_111"
        # Assistant message should not have attachment_url
        asst_msg = body["data"][1]
        assert "attachment_url" not in asst_msg
        # Top-level attachments array
        assert "attachments" in body
        assert body["attachments"] == [{"id": "att_111", "url": "/api/attachments/att_111"}]

    @pytest.mark.asyncio
    async def test_no_attachment_url_for_session_without_attachments(self):
        """Session without attachments should pass through unchanged."""
        upstream_data = {
            "data": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        }

        with patch("server.HermesProxy.forward") as mock_forward:
            mock_resp = web.Response(
                body=json.dumps(upstream_data).encode(),
                status=200,
                content_type="application/json",
            )
            mock_forward.return_value = mock_resp

            req = _make_request(
                "GET", "/api/sessions/sess_noatt/messages",
                match_info={"session_id": "sess_noatt"},
            )
            result = await handle_session_messages(req)

        assert result.status == 200
        body = json.loads(result.text)
        # No attachment_url should be injected
        for msg in body["data"]:
            assert "attachment_url" not in msg
        assert "attachments" not in body

    @pytest.mark.asyncio
    async def test_multiple_attachments_injects_first_url(self):
        """When multiple attachments, first attachment URL is injected into user message."""
        _attachment_store["sess_multi_att"] = ["att_aaa", "att_bbb"]

        upstream_data = {
            "data": [
                {"role": "user", "content": "two images"},
                {"role": "assistant", "content": "got them"},
            ]
        }

        with patch("server.HermesProxy.forward") as mock_forward:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.body = json.dumps(upstream_data).encode()
            mock_forward.return_value = mock_resp

            req = _make_request(
                "GET", "/api/sessions/sess_multi_att/messages",
                match_info={"session_id": "sess_multi_att"},
            )
            result = await handle_session_messages(req)

        body = json.loads(result.text)
        # First attachment URL on user message
        assert body["data"][0]["attachment_url"] == "/api/attachments/att_aaa"
        # All attachments in top-level array
        assert len(body["attachments"]) == 2
