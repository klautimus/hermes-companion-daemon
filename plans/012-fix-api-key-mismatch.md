# Plan 012: Fix API key mismatch — daemon uses placeholder, real key in ~/.hermes/.env

> **Executor instructions**: P0 fix for end-to-end auth. The daemon's `config.yaml` has `api_key: test-key` (placeholder). The real key is `API_SERVER_KEY` in `~/.hermes/.env`. The daemon reads `config.yaml` first; only falls back to `~/.hermes/.env` if `api_key` is empty.

## Status
- **Priority**: P0 (blocks all `/api/sessions/*` calls)
- **Effort**: S
- **Risk**: LOW (just config change)
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `a03dc6a`, 2026-06-17

## Why this matters

When user authenticates successfully via Basic Auth, the daemon forwards the request to Hermes API at `http://127.0.0.1:8642` using its own `api_key`. If the api_key is wrong, every `/api/sessions` and `/v1/chat/completions` call returns 401 "Invalid API key" — the user sees "Invalid credentials" in the app even though Basic Auth succeeded.

## Evidence

`server.py:307` — `headers["Authorization"] = f"Bearer {API_KEY}"` where `API_KEY = config.hermes.api_key`.

`config.py:102-109`:
```python
if not env.get("HERMES_API_KEY") and not env.get("API_SERVER_KEY") and not config["hermes"].get("api_key"):
    # ... read from ~/.hermes/.env
```

If `config["hermes"].get("api_key")` is non-empty (it's "test-key"), the env-fallback NEVER runs.

## Current state

- `/home/kevin/.config/hermes-companion/config.yaml` has `api_key: test-key` (placeholder)
- `/home/kevin/.hermes/.env` has `API_SERVER_KEY=VfTDD7Kdw8RIwp4jVnEN7iQEfrynjDorZU3Jr73jZOk` (real)
- Direct curl with real key works: `curl -H "Authorization: Bearer VfTDD7Kdw8RIwp4jVnEN7iQEfrynjDorZU3Jr73jZOk" http://127.0.0.1:8642/api/sessions` → 200 with sessions list
- Through daemon with `test-key` returns 401 "Invalid API key"

## Scope

**In scope**:
- Update `~/.config/hermes-companion/config.yaml` with real API key
- Add a daemon restart
- Add a startup self-check that warns if `api_key` looks like a placeholder (`test-key`, `changeme`, etc.)

**Out of scope**:
- Wizard changes
- Android app

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify env var | `grep API_SERVER_KEY /home/kevin/.hermes/.env` | shows real key |
| Update config | edit `~/.config/hermes-companion/config.yaml` `api_key:` line | real key |
| Restart daemon | `systemctl --user restart hermes-companion` | clean restart |
| Verify | `curl -u kevin:Kevi667n!1991! https://android.kevlarscreations.com/api/sessions` | 200 with sessions |

## Steps

### Step 1: Update config.yaml with real key

```bash
# Read real key
REAL_KEY=$(grep '^API_SERVER_KEY=' /home/kevin/.hermes/.env | cut -d= -f2)
# Update config.yaml (single sed replacement)
sed -i "s|^  api_key: .*|  api_key: ${REAL_KEY}|" /home/kevin/.config/hermes-companion/config.yaml
# Verify
grep "api_key" /home/kevin/.config/hermes-companion/config.yaml
```

### Step 2: Restart daemon

```bash
systemctl --user restart hermes-companion
sleep 2
```

### Step 3: Verify end-to-end

```bash
curl -s -u "kevin:Kevi667n!1991!" https://android.kevlarscreations.com/api/sessions | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'OK - {len(d.get(\"data\",[]))} sessions')"
```

Expected: `OK - 50 sessions` (or similar)

### Step 4: Add placeholder-detection to daemon startup (defense in depth)

In `server.py:34-46`, after `config = load_config()`, add:

```python
PLACEHOLDER_KEYS = {"test-key", "changeme", "your-key-here", "TODO", "REPLACE_ME", ""}
if config["hermes"].get("api_key") in PLACEHOLDER_KEYS:
    logger.error(
        "FATAL: hermes.api_key in config.yaml looks like a placeholder ('%s'). "
        "Set it to the real API_SERVER_KEY from ~/.hermes/.env, or unset the "
        "config field to auto-load from the env var.",
        config["hermes"].get("api_key"),
    )
    sys.exit(2)
```

### Step 5: Commit any source changes

The config.yaml change is external to the repo (under `~/.config/`). The source change (Step 4) is in `server.py`:

```bash
cd /home/kevin/.hermes/companion
git add server.py
git commit -m "fix(daemon): fail-fast on placeholder hermes.api_key

The companion daemon was running with api_key: test-key while the
real API key was in ~/.hermes/.env. Forwarded requests to Hermes
API returned 401 'Invalid API key' but looked like auth failure
to the Android app.

- server.py: detect placeholder values on startup, exit with code 2
- Updated config.yaml to real API_SERVER_KEY (external, not in repo)
- End-to-end verified: tunnel -> daemon -> Hermes API returns 200
"
```

## Test plan

- After config update + restart, all `/api/*` endpoints return 200
- Direct curl with placeholder `test-key` fails the placeholder check
- App on phone can list sessions / create sessions

## Done criteria

- [ ] `~/.config/hermes-companion/config.yaml` has real `API_SERVER_KEY`
- [ ] `server.py` fails fast on placeholder values
- [ ] `python -m pytest test_server.py -v` passes (39 tests, verifies API endpoint code including placeholder detection)
- [ ] Daemon restart clean
- [ ] Git committed

## STOP conditions

- Real key cannot be read from `~/.hermes/.env` — STOP, check file permissions / existence
- Daemon restart fails — STOP, check systemd status

## Maintenance notes

- The `~/.config/hermes-companion/config.yaml` file is NOT in version control. Future daemons installed by setup wizard will regenerate this file. The placeholder detection in `server.py` will catch any regenerated placeholder.
- Consider: write the real key from `~/.hermes/.env` automatically during setup wizard (read env, write to config.yaml if present).
