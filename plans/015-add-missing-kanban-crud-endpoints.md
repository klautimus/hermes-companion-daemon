# Plan 015: Add Missing Kanban CRUD Endpoints

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. Use CodeGraph MCP tools (mcp_codegraph_codegraph_*) as primary exploration tools for the daemon repo at `/home/kevin/.hermes/companion`.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: LOW
- **Depends on**: Plan 020 (daemon must be running)
- **Category**: bug
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters

The Android app calls 5 kanban endpoints that **do not exist** on the daemon. Every kanban create/edit/delete/bulk/dependency operation silently fails with 404. The KanbanScreen UI has 1800+ lines implementing these features, but none of them work. This is the #1 blocker for a functional app.

## Current state

### Daemon route table (server.py:881-891)

The daemon registers these kanban routes:
```python
app.router.add_get("/api/kanban/boards", handle_kanban_boards)
app.router.add_post("/api/kanban/boards", handle_kanban_boards_create)
app.router.add_post("/api/kanban/boards/{slug}/rename", handle_kanban_board_rename)
app.router.add_post("/api/kanban/boards/{slug}/archive", handle_kanban_board_archive)
app.router.add_delete("/api/kanban/boards/{slug}", handle_kanban_board_delete)
app.router.add_get("/api/kanban/tasks", handle_kanban_tasks_list)
app.router.add_get("/api/kanban/tasks/{task_id}", handle_kanban_task_show)
app.router.add_post("/api/kanban/tasks/{task_id}/complete", handle_kanban_task_complete)
app.router.add_post("/api/kanban/tasks/{task_id}/comment", handle_kanban_task_comment)
app.router.add_post("/api/kanban/tasks/{task_id}/assign", handle_kanban_task_assign)
```

### Android endpoints called but MISSING from daemon

| Android call | Method | Path | MainViewModel.kt line |
|---|---|---|---|
| Create task | POST | `/api/kanban/tasks?board=X` | :600 |
| Update title/body/status/priority | PATCH | `/api/kanban/tasks/{id}?board=X` | :452,464,476 |
| Delete task | DELETE | `/api/kanban/tasks/{id}?board=X` | :500 |
| Bulk status/reassign | POST | `/api/kanban/tasks/bulk?board=X` | :618,636 |
| Link/dependency | POST | `/api/kanban/links?board=X` | :516 |

### Existing _kanban() wrapper pattern

The daemon already has a `_kanban(args, board, timeout)` function (server.py:353) that wraps the `hermes kanban` CLI. Existing handlers like `handle_kanban_task_complete` follow this pattern:

```python
async def handle_kanban_task_complete(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, out, err = _kanban(["complete", task_id, "--json"], board)
    if code != 0:
        return web.json_response({"error": {"code": "INTERNAL_ERROR", "message": err or out}}, status=500)
    try:
        return web.json_response(json.loads(out))
    except json.JSONDecodeError:
        return web.json_response({"status": "ok"})
```

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd ~/.hermes/companion && python -m pytest tests/ -v --tb=short` | all pass |
| Start daemon | `cd ~/.hermes/companion && python server.py` | listening on 8777 |
| Health check | `curl -fsS http://127.0.0.1:8777/healthz` | `{"status":"ok"}` |

## CodeGraph-first codebase exploration

Use `mcp_codegraph_codegraph_context(task="...", projectPath="/home/kevin/.hermes/companion")` and `mcp_codegraph_codegraph_explore(query="...", projectPath="/home/kevin/.hermes/companion")` as primary tools. Use `read_file` only for specific line excerpts.

## Scope

**In scope** (the only files you should modify):
- `server.py` — add new route handlers + register them
- `tests/test_kanban_crud.py` (create) — tests for new endpoints

**Out of scope**:
- Android app code (covered by separate plans)
- Kanban block/unblock/archive/reclaim (Plan 016)
- 2FA (Plan 017)
- Attachment integration (Plan 018)

## Git workflow

- Branch: `advisor/015-kanban-crud-endpoints`
- Commit per logical unit
- Do NOT push unless told to

## Steps

### Step 1: Add handle_kanban_task_create

Add a new handler for `POST /api/kanban/tasks` that:
1. Reads JSON body for `title`, `status`, `assignee`, `priority`
2. Reads `board` from query string
3. Calls `_kanban(["create", title, "--body", body_json, "--assignee", assignee, "--json"], board)`
4. Returns the created task JSON

Follow the pattern of `handle_kanban_task_complete` (server.py ~line 430-440).

**Verify**: `curl -X POST -u user:pass -H "Content-Type: application/json" -d '{"title":"test","status":"todo"}' "http://127.0.0.1:8777/api/kanban/tasks?board=default"` → 200/201 with task JSON

### Step 2: Add handle_kanban_task_edit (PATCH)

Add a handler for `PATCH /api/kanban/tasks/{task_id}` that:
1. Reads JSON body for fields to update: `title`, `body`, `status`, `priority`, `assignee`
2. Calls `_kanban(["edit", task_id, "--title", title, ...], board)` for each provided field
3. Returns the updated task

The hermes CLI supports `hermes kanban edit <task_id> --title "..." --body "..." --status "..." --priority N --assignee "..."`.

**Verify**: `curl -X PATCH -u user:pass -H "Content-Type: application/json" -d '{"status":"done"}' "http://127.0.0.1:8777/api/kanban/tasks/<task_id>?board=default"` → 200 with updated task

### Step 3: Add handle_kanban_task_delete

Add a handler for `DELETE /api/kanban/tasks/{task_id}` that:
1. Reads `board` from query string
2. Calls `_kanban(["archive", task_id], board)` (archive, not hard-delete)
3. Returns `{"status": "ok"}`

**Verify**: `curl -X DELETE -u user:pass "http://127.0.0.1:8777/api/kanban/tasks/<task_id>?board=default"` → 200

### Step 4: Add handle_kanban_task_bulk

Add a handler for `POST /api/kanban/tasks/bulk` that:
1. Reads JSON body: `task_ids` (array), `action` ("set_status" or "set_assignee"), `value`
2. Loops through task_ids applying the action via `_kanban()`
3. Returns count of affected tasks

**Verify**: `curl -X POST -u user:pass -H "Content-Type: application/json" -d '{"task_ids":["t_abc"],"action":"set_status","value":"done"}' "http://127.0.0.1:8777/api/kanban/tasks/bulk?board=default"` → 200

### Step 5: Add handle_kanban_link

Add a handler for `POST /api/kanban/links` that:
1. Reads JSON body: `parent_id`, `child_id`
2. Calls `_kanban(["link", parent_id, child_id], board)`
3. Returns `{"status": "ok"}`

**Verify**: `curl -X POST -u user:pass -H "Content-Type: application/json" -d '{"parent_id":"t_a","child_id":"t_b"}' "http://127.0.0.1:8777/api/kanban/links?board=default"` → 200

### Step 6: Register all new routes

Add to the route table in server.py (after line 891):
```python
app.router.add_post("/api/kanban/tasks", handle_kanban_task_create)
app.router.add_patch("/api/kanban/tasks/{task_id}", handle_kanban_task_edit)
app.router.add_delete("/api/kanban/tasks/{task_id}", handle_kanban_task_delete)
app.router.add_post("/api/kanban/tasks/bulk", handle_kanban_task_bulk)
app.router.add_post("/api/kanban/links", handle_kanban_link)
```

### Step 7: Write tests

Create `tests/test_kanban_crud.py` with tests for each new endpoint following the pattern in `tests/test_setup.py` (mock requests, verify status codes and response shapes).

**Verify**: `python -m pytest tests/test_kanban_crud.py -v` → all pass

## Done criteria

- [ ] `python -m pytest tests/ -v` exits 0 (all pass including new tests)
- [ ] `curl -X POST ... /api/kanban/tasks` returns 200/201
- [ ] `curl -X PATCH ... /api/kanban/tasks/{id}` returns 200
- [ ] `curl -X DELETE ... /api/kanban/tasks/{id}` returns 200
- [ ] `curl -X POST ... /api/kanban/tasks/bulk` returns 200
- [ ] `curl -X POST ... /api/kanban/links` returns 200
- [ ] `grep "handle_kanban_task_create\|handle_kanban_task_edit\|handle_kanban_task_delete\|handle_kanban_task_bulk\|handle_kanban_link" server.py` shows all 5 handlers
- [ ] `git status` clean; `git log -1` shows commit
