# Plan 026: Add Missing /api/auth/2fa/check Endpoint

> **Executor instructions**: Follow this plan step by step.

## Status
- **Priority**: P1 | **Effort**: S | **Risk**: LOW | **Depends on**: nothing
- **Category**: bug
- **Planned at**: commit `cd9b59a`, 2026-06-19

## Why this matters

The Android app calls `POST /api/auth/2fa/check` during login to determine if 2FA
is required. This endpoint has NO handler — it works by accident: when 2FA is enabled,
the BasicAuth middleware intercepts the request and returns `{requires_2fa: true}`.
When 2FA is disabled, the middleware passes through and aiohttp returns 404. The Android
client catches the 404 and assumes "no 2FA needed." This is fragile — a 404 is not a
semantic "2FA disabled" response and could mask real errors.

## Current state

- `server.py:1504-1556` — full route table. No route for `/api/auth/2fa/check`.
- `server.py:276-289` — auth middleware exempt list includes `/api/auth/2fa/verify`,
  `/api/auth/2fa/setup`, `/api/auth/2fa/disable`, `/api/auth/2fa/resend` but NOT
  `/api/auth/2fa/check`. This is correct — check SHOULD go through auth so the
  middleware can detect 2FA-enabled users.
- Android `ApiClient.kt:293`: `suspend fun check2fa(): String = post("/api/auth/2fa/check")`
- Android `SetupWizardScreen.kt:299-307`: calls `c.check2fa()`, parses response for
  `requires_2fa`, catches failure as "proceed without 2FA."

## Scope

**In scope** (the only files you should modify):
- `server.py` (daemon repo) — add handler + route

## Steps

### Step 1: Add handle_2fa_check handler

Add after the existing 2FA handlers (around line 1451, after `handle_2fa_resend`):

```python
async def handle_2fa_check(request: web.Request) -> web.Response:
    """POST /api/auth/2fa/check — check if 2FA is required for the authenticated user.

    This endpoint is reached only when the auth middleware does NOT intercept
    (i.e., 2FA is not enabled for the user). If 2FA were enabled, the middleware
    would have returned {requires_2fa: true} before reaching this handler.
    """
    return web.json_response({"requires_2fa": False})
```

### Step 2: Register the route

In the route table section (around line 1549), add:

```python
    app.router.add_post("/api/auth/2fa/check", handle_2fa_check)
```

This must be added in the "2FA Auth" section alongside the other 2FA routes.

### Step 3: Verify

```bash
systemctl --user restart hermes-companion
sleep 2
curl -s -u kevin:Kevi667n!1991! -X POST http://127.0.0.1:8777/api/auth/2fa/check
# Expected: {"requires_2fa": false}
```

## Done criteria
- [ ] `curl -s -u kevin:Kevi667n!1991! -X POST http://127.0.0.1:8777/api/auth/2fa/check` returns `{"requires_2fa": false}`
- [ ] `/home/kevin/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/ -v --tb=short` passes with 0 new failures
- [ ] `git status` is CLEAN; `git log -1` shows commit
