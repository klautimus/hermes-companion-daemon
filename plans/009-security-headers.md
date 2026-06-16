# Plan 009: Security headers middleware + CORS docs

> **Executor instructions**: Modifies the canonical `server.py` after Plan 001. Run all verification.
>
> **Drift check**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- server.py API.md
> ```

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001
- **Category**: security
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

`server.py:528-562` (the `create_app()` function) registers only the auth middleware. No security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) are set on responses. If the daemon is ever reached directly (bypassing Cloudflare) or via a misconfigured tunnel, responses lack browser hardening. While the daemon is server-to-server (Android client uses OkHttp, not a browser), future web UIs (Hermes Web companion) would benefit from these headers. This plan adds a lightweight middleware that sets the headers, plus documents CORS posture in `API.md`.

## Current state

**File**: `server.py` (root, 572 LOC after Plan 001)

Lines 528-562 (approximate, read with `read_file` to verify):
```python
async def create_app() -> web.Application:
    auth = BasicAuth(AUTH_FILE)
    app = web.Application(middlewares=[auth.middleware])
    # ... route registrations
    return app
```

**File**: `API.md` (6.4 KB)

Documents the daemon's HTTP contract. Currently does not mention security headers or CORS.

**Repo conventions**:
- aiohttp middleware pattern: `async def middleware(request, handler)` with `web.middleware` decorator
- Header names: standard HTTP, no custom names
- `API.md` is the source of truth for the contract

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| New test | `cd /home/kevin/.hermes/companion && python -m pytest tests/test_security_headers.py -v` | new tests pass |
| Smoke import | `cd /home/kevin/.hermes/companion && python -c "import server; print('ok')"` | `ok` |

## Scope

**In scope**:
- `server.py` — add `security_headers_middleware` function, register in `create_app()`
- `tests/test_security_headers.py` — create
- `API.md` — add a "Security Headers" section

**Out of scope**:
- HSTS (daemon is HTTP behind Cloudflare; HSTS only matters if Cloudflare terminates TLS AND the daemon's response travels cleartext to the browser, which doesn't happen here)
- CORS implementation (current architecture is server-to-server, no browser origin)
- CSP report-uri (no policy violations expected)
- Reverse-proxy headers (Cloudflare sets these, daemon doesn't need to)

## Git workflow

- Branch: `advisor/009-security-headers`
- Commit style: `feat(security):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/companion
git diff --stat f78cd82..HEAD -- server.py API.md
```

If either changed, STOP.

### Step 2: Add `security_headers_middleware` in `server.py`

In `server.py`, after the `BasicAuth` class (around L110), add:

```python
@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response: web.StreamResponse = await handler(request)
    # Skip headers for streaming/SSE responses (already set by handler)
    if not isinstance(response, web.Response):
        return response
    # Set headers only if not already set by the handler
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
    if "X-Frame-Options" not in response.headers:
        response.headers["X-Frame-Options"] = "DENY"
    if "X-Content-Type-Options" not in response.headers:
        response.headers["X-Content-Type-Options"] = "nosniff"
    if "Referrer-Policy" not in response.headers:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "Permissions-Policy" not in response.headers:
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response
```

### Step 3: Register the middleware in `create_app()`

In `server.py:528-562`, update the middleware list:

```python
async def create_app() -> web.Application:
    auth = BasicAuth(AUTH_FILE)
    app = web.Application(middlewares=[auth.middleware, security_headers_middleware])
    # ... rest unchanged
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python -c "import server; print('ok')"
```

### Step 4: Write tests in `tests/test_security_headers.py`

```python
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from server import create_app

@pytest.mark.asyncio
async def test_security_headers_present_on_healthz():
    """Health endpoint should set security headers."""
    app = create_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "geolocation=()" in resp.headers.get("Permissions-Policy", "")

@pytest.mark.asyncio
async def test_handler_can_override_security_header():
    """If a handler sets its own CSP, the middleware respects it."""
    async def custom_handler(request):
        return web.Response(
            text="ok",
            headers={"Content-Security-Policy": "default-src 'none'"},
        )
    app = web.Application(middlewares=[security_headers_middleware])
    app.router.add_get("/custom", custom_handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/custom")
        assert resp.headers.get("Content-Security-Policy") == "default-src 'none'"
```

To test the second case, import the middleware function. It may need to be exposed at module level (which it is, since `create_app` is a module-level function).

### Step 5: Update `API.md`

In `API.md`, add a section:

```markdown
## Security Headers

All responses include the following HTTP security headers:

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | `default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |

Handlers may override these by setting the header in their response. HSTS is
not emitted because the daemon speaks HTTP behind a TLS-terminating proxy
(Cloudflare). The proxy emits HSTS in production.

## CORS

The daemon does NOT implement CORS. It is designed for server-to-server
use (Android client via OkHttp + Hermes API). If a web UI is added in the
future, CORS must be configured with EXPLICIT allowed origins (never
`Access-Control-Allow-Origin: *` with `Access-Control-Allow-Credentials: true`).
The recommended pattern is `aiohttp_cors` with an allow-list of origins,
each with `allow_credentials=True` only if the origin is trusted.
```

### Step 6: Run all tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -30
```

### Step 7: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
feat(security): add security headers middleware + CORS docs

Previously no security headers were set on daemon responses. If the
daemon is ever reached directly (bypassing Cloudflare) or via a
misconfigured tunnel, responses lack browser hardening.

Changes:
- Add security_headers_middleware: sets CSP, X-Frame-Options,
  X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- Register middleware in create_app() after auth middleware
- Handlers can override any header (middleware respects existing values)
- API.md: add Security Headers section and CORS posture note
- CORS: documented as not-implemented, with safe-pattern guidance for
  any future web UI

HSTS intentionally omitted: daemon speaks HTTP behind TLS-terminating
proxy (Cloudflare), so HSTS would be ignored.
EOF
)"
```

## Test plan

- New `tests/test_security_headers.py` — 2+ tests:
  - All security headers present on `/healthz`
  - Handler can override (middleware respects existing values)
- Existing tests still pass
- Verification: `python -m pytest tests/test_security_headers.py -v`

## Done criteria

- [ ] `python -m pytest -xvs` exits 0
- [ ] `python -m pytest tests/test_security_headers.py -v` — new tests pass
- [ ] `grep "security_headers_middleware" server.py` shows 2+ matches (definition + registration)
- [ ] `grep "Content-Security-Policy" server.py` shows 1+ matches
- [ ] `grep "CORS" API.md` shows 1+ matches
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 009 row updated to `DONE`

## STOP conditions

- Plan 001 not DONE — STOP.
- Drift check shows server.py or API.md changed — STOP.
- Test fails because middleware doesn't apply (e.g., create_app not updated) — STOP, verify Step 3 was actually applied.

## Maintenance notes

- The CSP `default-src 'self'` blocks inline scripts. The daemon doesn't serve any HTML, so this is fine. If a web UI is added that needs inline scripts, the CSP must be loosened — but that's the right time to think about it.
- `frame-ancestors 'none'` and `X-Frame-Options: DENY` are belt-and-suspenders. The `frame-ancestors` directive is the modern CSP replacement for `X-Frame-Options`; both are emitted for older browser support.
- `Permissions-Policy` is a relatively new header. Older browsers ignore it; modern browsers respect it.
- For v2: when a web UI is added, implement CORS with explicit origin allow-list. Do NOT use wildcard with credentials.
