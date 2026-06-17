# Plan 010: Unify auth.json path — single source of truth for credentials

> **Executor instructions**: This is a P0 plan. The daemon and setup wizard currently use **two different auth.json files**, causing all auth to fail for users created via the setup wizard. This must be fixed first.

## Status
- **Priority**: P0 (blocks all authentication)
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness/bugs
- **Planned at**: commit `3c3b2bd`, 2026-06-17

## Why this matters

The daemon (`server.py`) loads credentials from `/home/kevin/.hermes/companion/auth.json` (via `config.py`), while the setup wizard (`setup_wizard.py`) writes to `/home/kevin/.config/hermes-companion/auth.json` (via `config_schema.py`). Users created via the wizard cannot log in because the daemon never sees their credentials.

**Current state**:
- Daemon auth file: `/home/kevin/.hermes/companion/auth.json` — user `kevin` (manually added)
- Wizard auth file: `/home/kevin/.config/hermes-companion/auth.json` — user `admin` (wizard default)

## Root cause

Two config modules with different defaults:
- `config.py:27` — `"file_path": "~/.hermes/companion/auth.json"`
- `config_schema.py:51` — `"file": "~/.config/hermes-companion/auth.json"`

Config file at `~/.config/hermes-companion/config.yaml` overrides with `auth.file: ~/.config/hermes-companion/auth.json`, cementing the split.

## Scope

**In scope**:
- `config_schema.py` — change default `AuthConfig.file` to `~/.hermes/companion/auth.json`
- `config.py` — verify default matches (already correct)
- `~/.config/hermes-companion/config.yaml` — update `auth.file` to `~/.hermes/companion/auth.json`
- `setup_wizard.py` — uses `config.get_expanded_paths()` which now resolves to unified path
- `server.py` — already uses `config.py` which resolves correctly

**Out of scope**:
- Android app credential storage (SessionManager)
- scrypt parameter changes (Plan 011)
- User management API (Plan 012)

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Verify current daemon path | `cd /home/kevin/.hermes/companion && python3 -c "from config import load_config; cfg=load_config(); print(cfg.get_expanded_paths()['auth_file'])"` | `/home/kevin/.hermes/companion/auth.json` |
| Verify current wizard path | `cd /home/kevin/.hermes/companion && python3 -c "from config_schema import load_config; cfg=load_config(); print(cfg.get_expanded_paths()['auth_file'])"` | should match above after fix |
| Run tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -x --tb=short` | all pass |

## Steps

### Step 1: Fix config_schema.py default path

Edit `/home/kevin/.hermes/companion/config_schema.py:51`:

```python
@dataclass
class AuthConfig:
    file: str = "~/.hermes/companion/auth.json"  # was: ~/.config/hermes-companion/auth.json
```

### Step 2: Update config.yaml to use unified path

Edit `/home/kevin/.config/hermes-companion/config.yaml`:

```yaml
auth:
  file: ~/.hermes/companion/auth.json  # was: ~/.config/hermes-companion/auth.json
```

### Step 3: Verify both config modules resolve to same path

```bash
cd /home/kevin/.hermes/companion
python3 -c "
from config import load_config as load1
from config_schema import load_config as load2
p1 = load1().get_expanded_paths()['auth_file']
p2 = load2().get_expanded_paths()['auth_file']
print('config.py:', p1)
print('config_schema.py:', p2)
print('MATCH:', str(p1) == str(p2))
"
```

Expected: both print `/home/kevin/.hermes/companion/auth.json` and `MATCH: True`

### Step 4: Copy existing daemon auth.json to unified location (if needed)

The daemon's auth.json already has the correct user (`kevin`). The wizard's auth.json has dummy data. Since we're unifying to the daemon's location, no copy needed — just ensure the wizard writes there.

### Step 5: Run full test suite

```bash
cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -x --tb=short
```

### Step 6: Commit

```bash
cd /home/kevin/.hermes/companion
git add config_schema.py
git add /home/kevin/.config/hermes-companion/config.yaml
git commit -m "fix(config): unify auth.json path to ~/.hermes/companion/auth.json

- config_schema.py: AuthConfig.file default now matches config.py
- config.yaml: auth.file updated to same unified path
- Ensures daemon and setup wizard use identical credentials file
"
```

## Test plan

- Existing auth tests pass
- `test_first_run.py::TestEndToEndSetup::test_auth_json_format_matches_server_expectations` should pass (verifies wizard auth.json readable by server)
- New test: verify `config.py` and `config_schema.py` resolve to identical `auth_file` path

## Done criteria

- [ ] `config_schema.py:51` shows `~/.hermes/companion/auth.json`
- [ ] `config.yaml` shows `auth.file: ~/.hermes/companion/auth.json`
- [ ] Both config modules resolve to identical absolute path
- [ ] `python3 -m pytest tests/ -x --tb=short` exits 0
- [ ] `git status` clean
- [ ] Plans/README.md updated

## STOP conditions

- Test suite fails after changes — STOP, revert and debug
- Paths still don't match — STOP, check config file loading order

## Maintenance notes

- Single auth.json location simplifies operations and debugging
- Future: consider making auth file path configurable via env var only, not two config systems
- If moving auth.json again, update BOTH config modules simultaneously