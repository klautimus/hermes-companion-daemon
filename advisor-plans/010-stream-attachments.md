# Plan 010: Stream attachment downloads + close HermesProxy session

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1 (attachment streaming), P2 (session cleanup)
- **Effort**: S
- **Risk**: LOW (both are localized fixes)
- **Depends on**: none
- **Category**: bug (performance + resource leak)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

Two related issues in `server.py`:

1. **`handle_attachment_serve` (lines 638-665)** uses `file_path.read_bytes()` then `web.Response(body=data, ...)`. This loads the entire file into memory before sending. With `MAX_UPLOAD_SIZE = 10MB` and concurrent downloads, the daemon can OOM.

2. **`HermesProxy._session`** is a class-level `ClientSession` singleton. It's never closed — no `atexit`, no signal handler, no `web.run_app` cleanup hook. File descriptors leak on every restart.

## Steps

### Step 1: Stream attachment downloads

**Edit** `~/.hermes/companion/server.py:638-665`:

Replace `data = file_path.read_bytes()` + `web.Response(body=data, ...)` with `web.FileResponse(file_path)`:

```python
async def handle_attachment_serve(request: web.Request) -> web.Response:
    # ... (existing auth/validation code)
    file_path = Path(ATTACHMENTS_DIR) / filename
    # Path traversal check (existing)
    if not str(file_path.resolve()).startswith(str(Path(ATTACHMENTS_DIR).resolve())):
        return web.json_response({"error": "Invalid path"}, status=400)
    if not file_path.exists():
        return web.json_response({"error": "Not found"}, status=404)

    # Use FileResponse for streaming
    return web.FileResponse(file_path)
```

`web.FileResponse` streams the file in chunks (default 256KB), uses sendfile() on systems that support it, and never loads the full file into memory.

**Verify**:
```bash
cd /home/kevin/.hermes/companion
grep -n "read_bytes" server.py
# Expected: only in setup_wizard.py or auth loading, not in handle_attachment_serve
```

### Step 2: Close HermesProxy._session on shutdown

**Edit** `~/.hermes/companion/server.py`:

Add at the top of the file (after the `import` block):
```python
import atexit
```

Add a function to close the session:
```python
@atexit.register
def _close_hermes_proxy_session():
    """Close the HermesProxy session on daemon shutdown."""
    if HermesProxy._session is not None and not HermesProxy._session.closed:
        # aiohttp ClientSession.close() is a coroutine, but it can be called
        # from sync context with run_until_complete on a new loop
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(HermesProxy._session.close())
            loop.close()
        except Exception as e:
            logger.warning("Failed to close HermesProxy session: %s", e)
```

Add a similar cleanup in the daemon's `web.run_app` shutdown hook (if `create_app` is the entry):

```python
def create_app() -> web.Application:
    # ... existing code
    app = web.Application(middlewares=[auth_middleware, security_headers_middleware])

    async def cleanup_handler(app):
        if HermesProxy._session is not None and not HermesProxy._session.closed:
            await HermesProxy._session.close()

    app.on_cleanup.append(cleanup_handler)
    return app
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
grep -n "_session.close" server.py
# Expected: matches in both the atexit handler and the on_cleanup handler
```

### Step 3: Add a streaming-download test

**Edit** `~/.hermes/companion/tests/test_attachment_streaming.py` (or a new test file):

```python
@pytest.mark.asyncio
async def test_attachment_serve_streams(tmp_path, monkeypatch):
    """Verify handle_attachment_serve uses streaming (not read_bytes)."""
    # Create a large test file (5MB)
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(b"x" * (5 * 1024 * 1024))
    # ... mock the ATTACHMENTS_DIR to tmp_path
    # ... make a request to /api/attachments/test.bin
    # ... assert response.status == 200
    # ... assert response.headers.get("Content-Length") == str(5 * 1024 * 1024)
```

**Verify**: `python3 -m pytest tests/test_attachment_streaming.py -v 2>&1 | tail -5`

### Step 4: Run full test suite

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 0 failures
```

## Done criteria

- [ ] `grep -n "web.FileResponse" server.py` shows at least 1 match in `handle_attachment_serve`
- [ ] `grep -n "on_cleanup" server.py` shows at least 1 match
- [ ] `python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] `git status` is clean (commit)

## STOP conditions

- `web.FileResponse` is not available in the installed aiohttp version (need 3.5+); update aiohttp or use a chunked `web.StreamResponse` instead
- A test asserts on the response body being bytes (it is, but streamed); update the test
