# AGENTS.md — Hermes Companion Daemon

## Agent skills

### Issue tracker

Local markdown issues under `.scratch/`. The daemon repo has no remote — issues are tracked locally. See `docs/agents/issue-tracker.md`.

### Triage labels

Standard label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` at repo root + `docs/adr/` for architectural decisions. See `docs/agents/domain.md`.

## Project overview

The Hermes Companion daemon (`server.py`) is an aiohttp web server that acts as a proxy between the Hermes Companion Android app and the Hermes Agent API. It provides:

- Basic Auth authentication (scrypt-hashed credentials)
- Session passthrough to Hermes API
- Kanban task management via `hermes kanban` CLI wrapper
- File attachment upload/serve
- First-time user registration via setup token flow

**Port**: 8777 (localhost)
**API Server**: 8642 (Hermes API)
**Tunnel**: android.kevlarscreations.com (Cloudflare)

### Key files

- `server.py` — main application (~900 lines), all route handlers
- `config_schema.py` — configuration dataclasses with validation
- `config.py` — runtime configuration loader
- `setup_wizard.py` — first-run setup wizard + auth file creation
- `tests/` — pytest test suite

### Build commands

```bash
# Run tests
python -m pytest tests/ -v

# Run daemon
python server.py

# Type check (no formal type checking — plain Python)
```
