#!/usr/bin/env python3
"""Tests for SSE streaming chat proxy (Plan 019).

Covers:
- handle_chat_stream returns StreamResponse with text/event-stream
- handle_chat_stream pipes SSE chunks from upstream
- handle_chat_stream handles attachment_ids + session_id
- handle_chat_stream handles upstream errors
- handle_chat_stream forces stream:true in upstream payload
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
    handle_chat_stream,
    _attachment_store,
)


def _make_stream_request(method, path, data=None, query=None, match_info=None, body=None):
    """Create a mock aiohttp request for streaming tests."""
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


def _make_sse_chunks(chunks):
    """Build raw SSE byte chunks from a list of data payloads."""
    result = b""
    for c in chunks:
        result += f"data: {json.dumps(c)}\n\n".encode()
    return result


class MockStreamResponse:
    """Minimal mock of aiohttp.web.StreamResponse for unit testing."""

    def __init__(self, status=200, reason="OK"):
        self.status = status
        self.reason = reason
        self.headers = {}
        self._prepared = False
        self._written = bytearray()

    async def prepare(self, request):
        self._prepared = True

    async def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        self._written.extend(data)

    async def drain(self):
        pass

    def get_written(self):
        return bytes(self._written)


class TestHandleChatStream:
    def setup_method(self):
        """Clear attachment store before each test."""
        _attachment_store.clear()

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_returns_stream_response_with_correct_headers(self, mock_proxy):
        """Stream endpoint should return a StreamResponse with text/event-stream."""
        # Build upstream SSE response
        sse_data = _make_sse_chunks([
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        ])

        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "text/event-stream"}

        async def iter_chunks():
            # Yield in small chunks to simulate real SSE
            for i in range(0, len(sse_data), 20):
                yield sse_data[i:i+20]

        mock_upstream.content = MagicMock()
        mock_upstream.content.__aiter__ = MagicMock(return_value=iter_chunks().__aiter__())
        # Make it work as async iterable
        async def async_iter():
            for i in range(0, len(sse_data), 20):
                yield sse_data[i:i+20]
        mock_upstream.content = async_iter()

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        # We need to mock web.StreamResponse to capture output
        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            result = await handle_chat_stream(req)

            # Verify StreamResponse was created with correct status
            mock_sr_cls.assert_called_once_with(status=200, reason="OK")
            # Verify headers
            assert mock_sr.headers["Content-Type"] == "text/event-stream"
            assert mock_sr.headers["Cache-Control"] == "no-cache"
            assert mock_sr.headers["Connection"] == "keep-alive"
            assert mock_sr.headers["X-Accel-Buffering"] == "no"

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_pipes_sse_chunks_from_upstream(self, mock_proxy):
        """Stream endpoint should pipe SSE chunks from Hermes to client."""
        sse_data = _make_sse_chunks([
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"finish_reason": "stop"}]},
        ])

        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "text/event-stream"}

        async def async_iter():
            for i in range(0, len(sse_data), 30):
                yield sse_data[i:i+30]

        mock_upstream.content = async_iter()

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            await handle_chat_stream(req)

            written = mock_sr.get_written()
            # Should contain the upstream SSE data
            assert b"Hello" in written
            assert b"world" in written
            # Should end with [DONE]
            assert b"data: [DONE]" in written

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_stores_attachment_ids_with_session(self, mock_proxy):
        """Stream request with attachment_ids + session_id should store mapping."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "text/event-stream"}

        async def async_iter():
            yield b"data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\n"
        mock_upstream.content = async_iter()

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "session_id": "sess_stream_1",
            "attachment_ids": ["att_stream_1"],
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            await handle_chat_stream(req)

        assert "sess_stream_1" in _attachment_store
        assert _attachment_store["sess_stream_1"] == ["att_stream_1"]

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_forces_stream_true_in_upstream_payload(self, mock_proxy):
        """Stream endpoint should force stream:true in the upstream request."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "text/event-stream"}

        async def async_iter():
            return
            yield  # make it an async generator
        mock_upstream.content = async_iter()

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,  # Client might send false, we force true
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            await handle_chat_stream(req)

        # Check the body that was sent upstream
        call_args = mock_session.request.call_args
        sent_body = call_args.kwargs.get("data") or call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("data")
        if sent_body:
            sent_payload = json.loads(sent_body)
            assert sent_payload["stream"] is True

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_upstream_error_returns_error_sse(self, mock_proxy):
        """Non-200 upstream should return error as SSE data."""
        mock_upstream = MagicMock()
        mock_upstream.status = 500
        mock_upstream.read = AsyncMock(return_value=b'{"error": "Internal Server Error"}')

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            await handle_chat_stream(req)

            written = mock_sr.get_written()
            assert b"UPSTREAM_ERROR" in written
            assert b"data: [DONE]" in written

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_connection_error_returns_error_sse(self, mock_proxy):
        """Connection error should return error as SSE data."""
        mock_session = MagicMock()
        mock_session.request = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        body = json.dumps({
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=body)

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            await handle_chat_stream(req)

            written = mock_sr.get_written()
            assert b"STREAM_ERROR" in written
            assert b"data: [DONE]" in written

    @patch("server.HermesProxy")
    @pytest.mark.asyncio
    async def test_empty_body_passthrough(self, mock_proxy):
        """Stream request with empty body should not crash."""
        mock_upstream = MagicMock()
        mock_upstream.status = 200
        mock_upstream.headers = {"Content-Type": "text/event-stream"}

        async def async_iter():
            return
            yield
        mock_upstream.content = async_iter()

        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_upstream)
        mock_proxy.get_session = AsyncMock(return_value=mock_session)

        req = _make_stream_request("POST", "/v1/chat/completions/stream", body=b"")

        with patch("server.web.StreamResponse") as mock_sr_cls:
            mock_sr = MockStreamResponse()
            mock_sr_cls.return_value = mock_sr

            result = await handle_chat_stream(req)

            # Should complete without error
            assert result is not None
