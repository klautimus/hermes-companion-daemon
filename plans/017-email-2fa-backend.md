# Plan 017: Email 2FA Backend (Gmail API OTP)

> **Executor**: Use CodeGraph MCP tools for daemon at `/home/kevin/.hermes/companion`.

## Status
- **Priority**: P0 | **Effort**: L | **Risk**: MED | **Depends on**: 016 | **Category**: feature
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters
Kevin explicitly requested email 2FA. The app currently has only Basic Auth — no second factor. Gmail OAuth is already configured at `~/.hermes/google_token.json`.

## Current state
- Gmail OAuth token: `~/.hermes/google_token.json` (owned by kevin.douglas.disher@gmail.com)
- Re-auth script: `~/.hermes/prompt-library/scripts/reauth-gmail.py`
- Daemon auth: `BasicAuth` class in server.py reads scrypt-hashed credentials from `~/.hermes/companion/auth.json`
- No 2FA infrastructure exists

## Design
Email-based OTP (not TOTP/authenticator app). Flow:
1. User logs in with username+password (existing Basic Auth)
2. If 2FA is enabled for the user, daemon returns `{"requires_2fa": true, "challenge_id": "..."}`
3. App prompts for 6-digit code
4. User enters code → daemon verifies → returns session token
5. Codes sent to kevin.douglas.disher@gmail.com via Gmail API

## Scope
**In scope**: `server.py` (new endpoints + 2FA middleware), `email_2fa.py` (new — Gmail API sender), `auth.json` schema update (add `two_factor_enabled` field), `tests/test_2fa.py` (create)
**Out of scope**: Android UI (Plan 022)

## Steps

### Step 1: Create email_2fa.py module
A new module that:
- Generates 6-digit OTP codes
- Stores pending challenges in memory (dict with 5-minute TTL)
- Sends emails via Gmail API using the existing OAuth token
- Pattern: `send_otp(email, challenge_id) -> code`, `verify_otp(challenge_id, code) -> bool`

Use `google-auth` and `google-api-python-client` for Gmail. If not installed: `pip install google-auth google-auth-oauthlib google-api-python-client`.

```python
import random, time, base64
from email.mime.text import MIMEText

_pending_challenges = {}  # challenge_id -> {code, email, expires}

def generate_challenge(email: str) -> str:
    challenge_id = os.urandom(8).hex()
    code = f"{random.randint(0, 999999):06d}"
    _pending_challenges[challenge_id] = {
        "code": code, "email": email, "expires": time.time() + 300
    }
    return challenge_id

def send_otp(challenge_id: str) -> None:
    challenge = _pending_challenges[challenge_id]
    # Use Gmail API to send email with the code
    # Load OAuth token from ~/.hermes/google_token.json
    # Send to challenge["email"]
    ...

def verify_otp(challenge_id: str, code: str) -> bool:
    challenge = _pending_challenges.get(challenge_id)
    if not challenge or time.time() > challenge["expires"]:
        return False
    if challenge["code"] != code:
        return False
    del _pending_challenges[challenge_id]
    return True
```

### Step 2: Add 2FA endpoints to server.py

```python
# POST /api/auth/2fa/verify — verify OTP code
async def handle_2fa_verify(request):
    body = await request.json()
    challenge_id = body.get("challenge_id", "")
    code = body.get("code", "")
    if email_2fa.verify_otp(challenge_id, code):
        return web.json_response({"status": "ok", "authenticated": True})
    return web.json_response({"error": {"code": "INVALID_OTP"}}, status=401)

# POST /api/auth/2fa/setup — enable 2FA for current user
async def handle_2fa_setup(request):
    # Read current user from Basic Auth
    # Enable 2FA flag in auth.json
    # Send a test OTP
    ...

# POST /api/auth/2fa/disable — disable 2FA
async def handle_2fa_disable(request):
    # Require OTP verification before disabling
    ...
```

### Step 3: Modify auth middleware for 2FA challenge
When a user with 2FA enabled authenticates via Basic Auth, return 200 with `{"requires_2fa": true, "challenge_id": "..."}` instead of proceeding to the requested endpoint. The Android app then prompts for the code and calls `/api/auth/2fa/verify`.

### Step 4: Register routes
```python
app.router.add_post("/api/auth/2fa/verify", handle_2fa_verify)
app.router.add_post("/api/auth/2fa/setup", handle_2fa_setup)
app.router.add_post("/api/auth/2fa/disable", handle_2fa_disable)
app.router.add_post("/api/auth/2fa/resend", handle_2fa_resend)
```

### Step 5: Write tests
`tests/test_2fa.py` — test challenge generation, OTP verification, expired challenges, wrong codes.

## Done criteria
- [ ] `python -m pytest tests/test_2fa.py -v` exits 0
- [ ] `curl -X POST /api/auth/2fa/verify` with correct code returns 200
- [ ] `curl -X POST /api/auth/2fa/verify` with wrong code returns 401
- [ ] Email is sent via Gmail API (verify with test)
- [ ] `git status` clean; `git log -1` shows commit
