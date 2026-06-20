# Plan 016: Add Task Lifecycle Endpoints

> **Executor**: Use CodeGraph MCP tools for daemon at `/home/kevin/.hermes/companion`.

## Status
- **Priority**: P0 | **Effort**: M | **Risk**: LOW | **Depends on**: 015 | **Category**: feature
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters
The Hermes dashboard supports task block/unblock/archive/reclaim/decompose but the daemon has no endpoints for these. The Android KanbanScreen needs them for full parity.

## Current state
Existing lifecycle handlers follow the `_kanban()` pattern (server.py:353). The hermes CLI supports: `hermes kanban block <id>`, `unblock <id>`, `archive <id>`, `reclaim <id>`, `decompose <id>`.

## Scope
**In scope**: `server.py` (add handlers + routes), `tests/test_kanban_lifecycle.py` (create)
**Out of scope**: Android UI (Plan 021)

## Steps

### Step 1: Add 5 lifecycle handlers
Each follows the `handle_kanban_task_complete` pattern (read task_id from match_info, read board from query, call _kanban, return JSON):

```python
async def handle_kanban_task_block(request):
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, out, err = _kanban(["block", task_id, "--json"], board)
    # ... standard error handling ...

async def handle_kanban_task_unblock(request): ...  # same pattern with "unblock"
async def handle_kanban_task_archive(request): ...  # "archive"
async def handle_kanban_task_reclaim(request): ...  # "reclaim"
```

For decompose, the handler reads a JSON body for `subtasks` (optional):
```python
async def handle_kanban_task_decompose(request):
    task_id = request.match_info["task_id"]
    board = request.query.get("board", "")
    code, out, err = _kanban(["decompose", task_id, "--json"], board)
```

### Step 2: Register routes (after line 891 in server.py)
```python
app.router.add_post("/api/kanban/tasks/{task_id}/block", handle_kanban_task_block)
app.router.add_post("/api/kanban/tasks/{task_id}/unblock", handle_kanban_task_unblock)
app.router.add_post("/api/kanban/tasks/{task_id}/archive", handle_kanban_task_archive)
app.router.add_post("/api/kanban/tasks/{task_id}/reclaim", handle_kanban_task_reclaim)
app.router.add_post("/api/kanban/tasks/{task_id}/decompose", handle_kanban_task_decompose)
```

### Step 3: Write tests
Create `tests/test_kanban_lifecycle.py` — mock `_kanban()` and verify each handler returns correct status codes.

**Verify**: `python -m pytest tests/test_kanban_lifecycle.py -v` → all pass

## Done criteria
- [ ] `python -m pytest tests/ -v` exits 0
- [ ] 5 new handlers exist in server.py
- [ ] 5 new routes registered
- [ ] `git status` clean; `git log -1` shows commit
