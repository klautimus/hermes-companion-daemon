# API Reference

Complete REST API reference for the Hermes Companion server.

**Base URL:** `http://<host>:8777`
**Auth:** HTTP Basic (RFC 7617) on all endpoints except `/health`
**Content-Type:** `application/json; charset=utf-8`
**Error shape:** `{ "error": { "code": "...", "message": "..." } }`

---

## Health

### GET /health

No auth required.

**Response 200:**
```json
{ "status": "ok", "uptime": 120, "hermes_api_reachable": true }
```

**Response 503:**
```json
{ "status": "degraded", "uptime": 120, "hermes_api_reachable": false }
```

---

## Sessions

All session endpoints proxy directly to the Hermes API at `http://127.0.0.1:8642`.

### GET /api/sessions

List all sessions.

**Response 200:**
```json
{
  "data": [
    { "id": "sess-abc", "title": "My Session", "model": "hermes-agent", "message_count": 5 }
  ],
  "has_more": false
}
```

### POST /api/sessions

Create a new session.

**Response 201:**
```json
{
  "data": [
    { "id": "sess-new", "title": "New Session" }
  ]
}
```

### GET /api/sessions/{session_id}

Get session metadata.

**Response 200:**
```json
{ "id": "sess-abc", "title": "My Session", "model": "hermes-agent" }
```

### DELETE /api/sessions/{session_id}

Delete a session. Returns `{ "ok": true }` even if Hermes doesn't support DELETE (405/404 fallback).

**Response 200:**
```json
{ "ok": true }
```

### GET /api/sessions/{session_id}/messages

Get message history for a session.

**Response 200:**
```json
{
  "data": [
    { "role": "user", "content": "Hello" },
    { "role": "assistant", "content": "Hi there!" }
  ]
}
```

---

## Chat

### POST /v1/chat/completions

Proxy to Hermes API. Supports both streaming and non-streaming.

**Request:**
```json
{
  "model": "hermes-agent",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "stream": false
}
```

**Response (non-streaming):** Standard OpenAI Chat Completions format.

**Response (streaming):** SSE stream.

---

## Kanban

All kanban endpoints require the `?board=<slug>` query parameter.

### GET /api/kanban/boards

List all kanban boards.

**Response 200:**
```json
{
  "boards": [
    { "slug": "default", "title": "Default Board" }
  ]
}
```

### POST /api/kanban/boards

Create a new board.

**Request:**
```json
{ "slug": "my-board", "name": "My Board" }
```

**Response 201:**
```json
{ "ok": true, "slug": "my-board", "name": "My Board" }
```

### POST /api/kanban/boards/{slug}/rename

Rename a board.

**Request:**
```json
{ "name": "New Name" }
```

**Response 200:**
```json
{ "ok": true }
```

### POST /api/kanban/boards/{slug}/archive

Archive a board.

**Response 200:**
```json
{ "ok": true }
```

### DELETE /api/kanban/boards/{slug}

Delete a board. The `default` board cannot be deleted (403).

**Response 200:**
```json
{ "ok": true }
```

### GET /api/kanban/tasks?board={slug}

List tasks on a board.

**Query parameters:**
- `board` (required) — Board slug
- `status` (optional) — Filter by status: `triage`, `todo`, `ready`, `running`, `blocked`, `done`, `archived`
- `assignee` (optional) — Filter by assignee profile name

**Response 200:**
```json
[
  {
    "id": "t_abc123",
    "title": "Implement feature",
    "status": "ready",
    "assignee": "ops",
    "priority": 1,
    "created": "2026-01-01T00:00:00Z"
  }
]
```

### GET /api/kanban/tasks/{task_id}?board={slug}

Get full task details including comments and events.

**Response 200:**
```json
{
  "id": "t_abc123",
  "title": "Implement feature",
  "status": "running",
  "assignee": "ops",
  "priority": 1,
  "body": "Full task description...",
  "comments": [
    { "author": "ops", "body": "Started work", "created_at": 1704067200 }
  ],
  "events": [
    { "kind": "created", "created_at": 1704067200 },
    { "kind": "claimed", "profile": "ops", "created_at": 1704067300 }
  ]
}
```

### POST /api/kanban/tasks/{task_id}/complete?board={slug}

Mark a task as done.

**Response 200:**
```json
{ "ok": true, "task_id": "t_abc123", "status": "done" }
```

### POST /api/kanban/tasks/{task_id}/comment?board={slug}

Add a comment to a task.

**Request:**
```json
{ "text": "Reviewed — looks good" }
```

**Query parameters:**
- `author` (optional) — Comment author (alphanumeric + hyphen/underscore only)

**Response 200:**
```json
{ "ok": true }
```

### POST /api/kanban/tasks/{task_id}/assign?board={slug}

Assign a task to a profile.

**Request:**
```json
{ "assignee": "ops" }
```

**Response 200:**
```json
{ "ok": true, "task_id": "t_abc123", "assignee": "ops" }
```

---

## Attachments

### POST /api/attachments

Upload a file attachment (multipart form data).

**Limits:** 10 MB max file size.

**Response 201:**
```json
{
  "id": "att_abcdef1234567890",
  "url": "/api/attachments/att_abcdef1234567890",
  "filename": "photo.jpg",
  "mime_type": "image/jpeg",
  "size": 12345
}
```

### GET /api/attachments/{att_id}

Serve an uploaded attachment.

**Response 200:** File content with appropriate Content-Type.

---

## Error Codes

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed JSON or missing required fields |
| 401 | `UNAUTHORIZED` | Missing or invalid Basic auth credentials |
| 403 | `FORBIDDEN` | Action not allowed (e.g., deleting default board) |
| 404 | `NOT_FOUND` | Task or resource does not exist |
| 422 | `VALIDATION_ERROR` | Semantically invalid input |
| 500 | `INTERNAL_ERROR` | Server or subprocess failure |
| 503 | `HERMES_DOWN` | Upstream Hermes API unreachable |

All errors return:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid credentials"
  }
}
```
