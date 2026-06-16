# Plan 006: Attachment upload — stream read with early size abort

> **Executor instructions**: Modifies the canonical `server.py` after Plan 001. Run all verification.
>
> **Drift check**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- server.py
> ```

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001
- **Category**: security
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

`server.py:454-494` (root copy, in `handle_attachment_upload`) reads the entire uploaded file into memory (`data = await file_field.read()` at L470) BEFORE checking size (L473-477). A malicious or accidental 1GB upload consumes 1GB of RAM before the size check rejects it. The current code at least enforces the size limit, but the size check happens too late. Streaming with early abort is a 5-line change that closes the DoS vector.

## Current state

**File**: `server.py` (root, 572 LOC after Plan 001)

Lines 454-494:
```python
async def handle_attachment_upload(request: web.Request) -> web.Response:
    """POST /api/attachments — upload a file attachment."""
    reader = await request.multipart()
    file_field = await reader.next()
    if file_field is None:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "file required"}},
            status=422,
        )
    # F-10 FIX: Sanitize filename — strip directory separators and ".."
    filename = file_field.filename or "upload"
    filename = os.path.basename(filename)
    filename = filename.replace("..", "_")
    if not filename:
        filename = "upload"
    content_type = file_field.headers.get("Content-Type", "application/octet-stream")
    data = await file_field.read()                                     # <-- BUG: full read first

    # F-08 FIX: Upload size limit
    if len(data) > MAX_UPLOAD_SIZE:                                    # <-- BUG: too late
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": f"file exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit"}},
            status=422,
        )

    # Save to attachments directory
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    att_id = f"att_{os.urandom(8).hex()}"
    att_path = ATTACHMENTS_DIR / f"{att_id}_{filename}"
    att_path.write_bytes(data)
    # ... rest
```

**Repo conventions**:
- `aiohttp` `BodyPartReader.read_chunk(size=...)` for streaming reads
- Module-level constants for limits
- Error response shape `{"error": {"code": "...", "message": "..."}}`

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| New test | `cd /home/kevin/.hermes/companion && python -m pytest tests/test_attachment_streaming.py -v` | new tests pass |
| Smoke import | `cd /home/kevin/.hermes/companion && python -c "import server; print('ok')"` | `ok` |

## Scope

**In scope**:
- `server.py` — replace `data = await file_field.read()` with chunked streaming in `handle_attachment_upload`
- `tests/test_attachment_streaming.py` — create

**Out of scope**:
- Other file-handling paths (e.g., `_kanban` subprocess doesn't read arbitrary user content)
- Auth changes (covered by Plan 002)

## Git workflow

- Branch: `advisor/006-attachment-streaming`
- Commit style: `fix(upload):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/companion
git diff --stat f78cd82..HEAD -- server.py
```

If `server.py` changed, STOP.

### Step 2: Replace `read()` with chunked streaming

In `server.py`, find `handle_attachment_upload` (L454-494). Replace the section from `data = await file_field.read()` (L470) to `att_path.write_bytes(data)` (L483) with:

```python
# Stream read with early size enforcement
CHUNK_SIZE = 64 * 1024  # 64 KB
data_chunks: list[bytes] = []
total = 0
while True:
    chunk = await file_field.read_chunk(size=CHUNK_SIZE)
    if not chunk:
        break
    total += len(chunk)
    if total > MAX_UPLOAD_SIZE:
        return web.json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": f"file exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit"}},
            status=413,  # Payload Too Large
        )
    data_chunks.append(chunk)
data = b"".join(data_chunks)
```

Also change the size-exceeded response status from `422` to `413` (more semantically correct; this is `Payload Too Large`, not `Unprocessable Entity`).

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python -c "import server; print('ok')"
```

### Step 3: Add unit test

Create `tests/test_attachment_streaming.py`:

```python
import io
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from server import handle_attachment_upload, MAX_UPLOAD_SIZE, ATTACHMENTS_DIR
import server  # for module globals

@pytest.fixture(autouse=True)
def reset_attachments_dir(tmp_path, monkeypatch):
    """Use a temp attachments dir for each test."""
    monkeypatch.setattr(server, "ATTACHMENTS_DIR", tmp_path)
    yield

@pytest.mark.asyncio
async def test_streaming_aborts_at_size_limit():
    """If the upload exceeds MAX_UPLOAD_SIZE, abort with 413 BEFORE reading all of it."""
    # Build a multipart payload larger than the limit
    oversize = MAX_UPLOAD_SIZE + 1024
    payload = b"x" * oversize

    # Use aiohttp test client
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    app.router.add_post("/api/attachments", handle_attachment_upload)

    async with TestClient(TestServer(app)) as client:
        data = aiohttp.FormData()
        data.add_field("file", io.BytesIO(payload), filename="big.bin", content_type="application/octet-stream")
        resp = await client.post("/api/attachments", data=data)
        assert resp.status == 413
        body = await resp.json()
        assert "exceeds" in body["error"]["message"]

@pytest.mark.asyncio
async def test_streaming_saves_small_file():
    """A small upload should succeed."""
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    app.router.add_post("/api/attachments", handle_attachment_upload)

    payload = b"hello world"
    async with TestClient(TestServer(app)) as client:
        data = aiohttp.FormData()
        data.add_field("file", io.BytesIO(payload), filename="hello.txt", content_type="text/plain")
        resp = await client.post("/api/attachments", data=data)
        assert resp.status == 201
        body = await resp.json()
        assert body["filename"] == "hello.txt"
        assert body["size"] == len(payload)
```

This requires `pytest-aiohttp` plugin. If not installed, add to dev deps in `pyproject.toml`.

### Step 4: Run all tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -30
```

### Step 5: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(upload): stream attachment upload with early size abort

The previous handle_attachment_upload read the entire file into memory
(data = await file_field.read()) before checking size. A 1GB upload
consumed 1GB of RAM before being rejected.

Changes:
- Read in 64 KB chunks via file_field.read_chunk(size=64*1024)
- Track running total; abort with 413 (Payload Too Large) when total
  exceeds MAX_UPLOAD_SIZE, before the next chunk is read
- Change size-exceeded response from 422 to 413 (semantic correctness)
- Add tests/test_attachment_streaming.py covering both abort and success
EOF
)"
```

## Test plan

- New `tests/test_attachment_streaming.py` — 2+ tests:
  - Oversize upload returns 413 without writing file
  - Small upload succeeds
- Existing tests still pass
- Verification: `python -m pytest tests/test_attachment_streaming.py -v`

## Done criteria

- [ ] `python -m pytest -xvs` exits 0
- [ ] `python -m pytest tests/test_attachment_streaming.py -v` — new tests pass
- [ ] `grep "read_chunk" server.py` shows 1+ matches
- [ ] `grep "status=413" server.py` shows the new status code
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 006 row updated to `DONE`

## STOP conditions

- Plan 001 not DONE — STOP.
- Drift check shows server.py changed — STOP.
- New test fails consistently — STOP, the streaming logic may have an off-by-one or chunk-boundary bug.

## Maintenance notes

- 64 KB chunk size is a balance between memory pressure and syscall overhead. Tune if needed.
- The 413 status is more semantically correct than 422. Existing clients should handle 413 (most HTTP libraries do by default).
- For very large legitimate uploads, consider increasing MAX_UPLOAD_SIZE in config_schema.py (covered by Plan 007).
- The `att_path.write_bytes(data)` still buffers the full file before writing. For really large files, write chunks directly: open the file once, write each chunk, close at the end. Memory savings is more dramatic. Out of scope for this plan.
