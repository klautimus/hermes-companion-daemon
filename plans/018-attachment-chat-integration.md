# Plan 018: Attachment-to-Message Chat Integration

> **Executor**: Use CodeGraph MCP tools for daemon at `/home/kevin/.hermes/companion`.

## Status
- **Priority**: P0 | **Effort**: M | **Risk**: LOW | **Depends on**: 017 | **Category**: feature
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters
The Composer (Android) has attachment UI (image picker, camera, file picker) and the daemon has upload/serve endpoints, but there's no way to associate an attachment with a chat message. Attachments are orphaned files with no message context.

## Current state
- Daemon: `POST /api/attachments` (upload) and `GET /api/attachments/{id}` (serve) exist and work
- Android Composer: has `onSendAttachment(filename, bytes, contentType)` callback
- Android ApiClient: has `uploadAttachment(filename, bytes, contentType)` method
- BUT: chat messages (`/v1/chat/completions`) don't support attachment references
- AND: MessageList.ChatBubble has `message.attachmentUrl` field but it's never populated from the chat flow

## Design
1. User selects attachment in Composer
2. App uploads attachment to daemon via existing `POST /api/attachments`
3. App includes attachment ID in the chat message metadata
4. When loading message history, attachment IDs are resolved to URLs
5. ChatBubble displays inline images for image attachments

## Scope
**In scope**: `server.py` (add attachment metadata to chat messages), Android `MainViewModel.kt` (wire upload before send), Android `MessageList.kt` (display attachments), Android `Composer.kt` (pass attachment reference)
**Out of scope**: Daemon upload/serve endpoints (already exist and work)

## Steps

### Step 1 (daemon): Add attachment metadata to chat proxy
Modify `handle_chat_proxy` to:
1. Parse attachment IDs from the request body (field: `attachment_ids`)
2. After forwarding to Hermes API, store the attachment→message association in a local mapping
3. When serving session messages via `handle_session_messages`, inject `attachment_url` field for messages with associated attachments

Create a simple JSON mapping file or in-memory dict: `{message_id: [attachment_id, ...]}`.

### Step 2 (android): Wire Composer → upload → send
In MainViewModel.sendMessage():
1. If there's a pending attachment, call `apiClient.uploadAttachment()` first
2. Get the returned attachment ID
3. Include `attachment_ids: [id]` in the chat request body
4. Clear the pending attachment

### Step 3 (android): Display attachments in MessageList
In ChatBubble:
1. Check `message.attachmentUrl` (already exists in the model)
2. If it's an image content type, render inline with `AsyncImage`
3. If it's another type, show a download button with filename

## Done criteria
- [ ] Upload an image from Composer → it appears inline in the chat bubble
- [ ] Attachment persists across session reload
- [ ] `python -m pytest tests/ -v` exits 0
- [ ] `git status` clean; `git log -1` shows commit
