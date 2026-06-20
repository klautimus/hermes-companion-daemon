# Plan 019: SSE Streaming Chat Proxy

> **Executor**: Use CodeGraph MCP tools for daemon at `/home/kevin/.hermes/companion`.

## Status
- **Priority**: P1 | **Effort**: M | **Risk**: MED | **Depends on**: 018 | **Category**: feature
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters
Chat is currently blocking (wait for full response, then display). Kevin wants a beautiful chat experience. Streaming (SSE) gives incremental text display like ChatGPT.

## Current state
- Daemon: `handle_chat_proxy` (server.py:423) forwards to Hermes `/v1/chat/completions` and returns the full response
- Android: `MainViewModel.sendMessage()` uses non-blocking POST, waits for complete response
- Hermes API supports SSE streaming via `"stream": true` in the request body

## Scope
**In scope**: `server.py` (add streaming endpoint), Android `MainViewModel.kt` (streaming consumer), Android `ChatScreen.kt` (incremental display)
**Out of scope**: Full streaming markdown rendering (deferred to v2)

## Steps

### Step 1 (daemon): Add SSE streaming endpoint
Add `POST /v1/chat/completions/stream` that:
1. Forwards to Hermes API with `"stream": true`
2. Pipes SSE chunks back to the client as they arrive
3. Uses `web.StreamResponse` with `text/event-stream` content type

### Step 2 (android): Add streaming consumer to ApiClient
Add `fun chatStream(messages, onChunk: (String) -> Unit)` that:
1. Opens an OkHttp connection to `/v1/chat/completions/stream`
2. Reads SSE chunks line by line
3. Calls `onChunk(text)` for each content delta

### Step 3 (android): Wire streaming in MainViewModel
Modify `sendMessage()` to:
1. Use streaming endpoint
2. Append chunks to the assistant message incrementally
3. Show a cursor while streaming

## Done criteria
- [ ] Sending a message shows incremental text appearing (not blocking)
- [ ] Streaming completes and finalizes the message
- [ ] `git status` clean; `git log -1` shows commit
