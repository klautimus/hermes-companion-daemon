# Plan 001: Consolidate daemon to single implementation (root → canonical)

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- server.py src/hermes_companion/server.py setup.py setup_wizard.py
> ```
> If any in-scope file changed since this plan was written, compare the "Current state" excerpts against the live code before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

The daemon repo currently ships **two divergent implementations of every module simultaneously** — `server.py` (572 LOC, root), `setup_wizard.py` (333 LOC, root), `config.py` (223 LOC, root), `cli.py` (root), at the daemon-repo root, AND identical-purpose modules in `src/hermes_companion/` (378 + 519 + 223 + 156 LOC). The Android repo's `hermes-companion.service:8` invokes the **root** copy (`/opt/hermes-companion/server/venv/bin/python server.py`), but the daemon repo's own `setup.py:37` declares an entry point `hermes-companion = server.cli:main` that points to a non-existent module. Tests target the root copy. The two implementations have drifted: root has 18 route handlers including chat proxy, attachments, board CRUD, task assign; package has 8 handlers (no chat, no attachments, no board CRUD, no task assign). The package copy's `handle_kanban_task_show` (L267) does NOT unwrap `{"task": {...}}` like root does (L280-281), causing `TaskShowResponse` deserialization to fail. `pyproject.toml` and `setup.py` both reference `server.cli:main`, broken. Consolidating to the root copy (which production actually runs) eliminates a maintenance trap and unblocks every other daemon-side plan (002, 003, 006, 007, 009).

## Current state

**Files in the daemon repo (root: `/home/kevin/.hermes/companion/`):**

| File | LOC | Role |
|------|-----|------|
| `server.py` | 572 | Root daemon (active in production) |
| `src/hermes_companion/server.py` | 378 | Package daemon (inactive; tests don't run against it) |
| `setup_wizard.py` | 333 | Root wizard (uses `config_schema.CompanionConfig` dataclass) |
| `src/hermes_companion/setup_wizard.py` | 519 | Package wizard (uses `config.load_config()` dict) |
| `config.py` | 223 | Root config (uses `CompanionConfig` dataclass) |
| `src/hermes_companion/config.py` | 223 | Package config (returns plain dict) |
| `cli.py` | root | Root CLI stub |
| `src/hermes_companion/cli.py` | 156 | Package CLI (with subcommands) |
| `config_schema.py` | 127 | Root config schema (dataclass) |
| `first_run.py` | 41 | Root first-run check |
| `setup.py` | 60 | Legacy setup.py — declares `console_scripts = "hermes-companion = server.cli:main"` — **broken** |
| `pyproject.toml` | 60 | Modern manifest — also references `server.cli:main` — **broken** |
| `src/hermes_companion/systemd/hermes-companion.service` | 12 | Systemd unit that imports `hermes_companion.server` (package copy) |
| `tests/test_first_run.py` | 174 | Tests root first_run |
| `tests/test_setup_wizard.py` | 344 | Tests root setup_wizard |
| `test_config.py` | 328 | Tests root config |
| `test_integration.py` | 76 | Tests config loading (no real server) |

**Test entrypoint** (`pyproject.toml` or `setup.cfg`): check via
```bash
grep -A5 "tool.pytest" /home/kevin/.hermes/companion/pyproject.toml
grep -A5 "test" /home/kevin/.hermes/companion/setup.py
```

**Production deployment target**: `/opt/hermes-companion/server/`, invoked by Android repo's `hermes-companion.service:8`:
```
ExecStart=/opt/hermes-companion/server/venv/bin/python server.py
```

**Repo conventions** (from recon):
- Python 3.10+, aiohttp 3.9+, pyyaml 6.0+
- Auth middleware pattern: `BasicAuth` class with `web.middleware` decorator
- Route handlers are module-level `async def` functions taking `web.Request`
- All responses are `web.json_response(...)` with `{"error": {"code": "...", "message": "..."}}` shape on failure
- Kanban calls go through `_kanban(args, board, timeout)` helper at L152-165
- Tests use pytest, async via `pytest-asyncio`, no real network

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run Python tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| Lint (if ruff installed) | `cd /home/kevin/.hermes/companion && python -m ruff check .` | exit 0 |
| Smoke import | `cd /home/kevin/.hermes/companion && python -c "from .server import main; print('ok')"` | prints `ok` |
| Authenticate admin (after tests) | `cd /home/kevin/.hermes/companion && python -c "from .config_schema import load_config; c = load_config(); print(c.server.port)"` | prints port number |

## Scope

**In scope** (modify only these):
- `setup.py` — fix entry_point
- `pyproject.toml` — fix [project.scripts]
- `src/hermes_companion/setup_wizard.py` — DELETE entirely (root copy supersedes)
- `src/hermes_companion/config.py` — DELETE entirely (root `config.py` + `config_schema.py` supersede)
- `src/hermes_companion/server.py` — DELETE entirely (root `server.py` supersedes)
- `src/hermes_companion/cli.py` — DELETE entirely (root `cli.py` supersedes; new root `cli.py` will be added)
- `src/hermes_companion/__init__.py` — KEEP (empty package marker)
- `src/hermes_companion/systemd/hermes-companion.service` — UPDATE to import root copy
- `tests/test_setup_wizard.py` — UPDATE import paths if needed
- `tests/test_first_run.py` — UPDATE import paths if needed
- `test_config.py` — UPDATE import paths if needed
- `src/hermes_companion_server.egg-info/` — DELETE (will regenerate)

**Out of scope** (do NOT touch):
- `server.py` (root) — this is the canonical version, only fix typos if drift check finds them
- `setup_wizard.py` (root), `config.py` (root), `config_schema.py` (root), `first_run.py` (root), `cli.py` (root)
- `API.md` — documentation, separate plan
- `auth.json` — secrets, never commit changes
- `attachments/` — runtime data
- Android repo at `/home/kevin/.hermes/projects/HermesCompanion/` — separate repo
- All `__pycache__/` directories

## Git workflow

- Branch: `advisor/001-daemon-consolidate`
- Commit style: conventional commits (`fix:`, `chore:`, `refactor:`)
- Match existing commit prefix style: `chore:`, `fix(`, `docs:` — look at `git log --oneline -5` before committing
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/companion
git diff --stat f78cd82..HEAD -- server.py src/hermes_companion/server.py setup.py setup_wizard.py
```

If any file changed, compare in-scope excerpts to the live code. Drift = STOP.

### Step 2: Run baseline tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -50
```

Confirm all tests pass BEFORE making changes. If any fail, that's pre-existing — note it but proceed.

### Step 3: Create branch

```bash
cd /home/kevin/.hermes/companion
git checkout -b advisor/001-daemon-consolidate
```

### Step 4: Fix `setup.py` entry_point

Current line 37 has `console_scripts = "hermes-companion = server.cli:main"` but `server/cli.py` does not exist.

Edit `setup.py` to:
- Change `console_scripts` to `hermes-companion = hermes_companion.cli:main`
- BUT: we are deleting `src/hermes_companion/cli.py` in Step 7. So this needs to point to root `cli.py`.
- New value: `hermes-companion = cli:main` (root cli.py, run as module via `python -m cli` or `pip install -e .`)
- Wait — `cli` is a stdlib module name. Pick a different name. Rename root `cli.py` to `companion_cli.py` and use `hermes-companion = companion_cli:main`.
- Update `setup.py:37` to use `companion_cli:main`.
- Update `setup.py:31` `find_packages` to include the new root module if needed (it should already).

**Verify**:
```bash
cd /home/kevin/.hermes/companion
mv cli.py companion_cli.py
python -c "import ast; ast.parse(open('setup.py').read()); print('setup.py parses')"
```

### Step 5: Fix `pyproject.toml` [project.scripts]

```bash
grep -n 'project.scripts\|console_scripts\|entry_points' /home/kevin/.hermes/companion/pyproject.toml
```

Update the `[project.scripts]` (or `[tool.setuptools.entry-points.console_scripts]`) to match `setup.py`:
```
[project.scripts]
hermes-companion = "companion_cli:main"
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python -c "import tomllib; print(tomllib.loads(open('pyproject.toml').read())['project']['scripts'])"
```
Should print `{'hermes-companion': 'companion_cli:main'}`.

### Step 6: Update systemd unit

The daemon repo's `src/hermes_companion/systemd/hermes-companion.service:7` currently has:
```
ExecStart={{PYTHON_PATH}} -c "from hermes_companion.server import main; main()"
```

Since we're deleting `src/hermes_companion/server.py`, this needs to point to root:
```
ExecStart={{PYTHON_PATH}} -m companion_server
```

OR if production deployment is via `hermes-companion serve` (matching `hermes-companion-user.service` in Android repo):
```
ExecStart={{PYTHON_PATH}} {{PYTHON_PATH}} -m companion_cli serve
```

The `{{PYTHON_PATH}}` is a templating artifact (likely filled by `pip install` post-install hook or config generator). For now, write a literal `/usr/bin/env python3` placeholder; production deployment will inject the venv path.

Update to:
```
ExecStart=/usr/bin/env python3 -m companion_cli serve
```

**Verify**:
```bash
cd /home/kevin/.hermes/companion
cat src/hermes_companion/systemd/hermes-companion.service
grep -c "companion_cli" src/hermes_companion/systemd/hermes-companion.service
```
Should print `1` or more.

### Step 7: Delete `src/hermes_companion/{server,setup_wizard,config,cli}.py`

```bash
cd /home/kevin/.hermes/companion
git rm src/hermes_companion/server.py src/hermes_companion/setup_wizard.py src/hermes_companion/config.py src/hermes_companion/cli.py
```

Keep `src/hermes_companion/__init__.py` (empty) for now. The package becomes a marker only.

### Step 8: Delete egg-info and regenerate

```bash
cd /home/kevin/.hermes/companion
git rm -r src/hermes_companion_server.egg-info
rm -rf src/hermes_companion_server.egg-info
```

### Step 9: Verify tests still pass

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -50
```

If `test_setup_wizard.py` or `test_first_run.py` import from `src.hermes_companion.*`, those imports will fail. They should import from root (e.g., `from .config_schema import ...` or relative imports from the same level).

**Verify imports in tests**:
```bash
cd /home/kevin/.hermes/companion
grep -n "^import\|^from" tests/test_setup_wizard.py tests/test_first_run.py test_config.py | head -20
```

If any import `from hermes_companion.X`, update to root-level import (e.g., `from config_schema import ...` — tests are run from the daemon repo root, so absolute imports work).

### Step 10: Smoke test the daemon can start

```bash
cd /home/kevin/.hermes/companion
# Don't actually start the server; just verify it imports
python -c "import server; print('server module loads')"
python -c "import companion_cli; print('cli module loads')"
python -c "import config_schema; print('config_schema loads')"
python -c "import setup_wizard; print('setup_wizard loads')"
python -c "import first_run; print('first_run loads')"
```

All five should print their respective `... loads` lines.

### Step 11: Update README and re-commit

If `README.md` references `hermes-companion` commands or paths that no longer exist, leave for a docs plan. This plan is structural only.

### Step 12: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
chore: consolidate daemon to root implementation

- Fix broken console_scripts entry point (server.cli:main -> companion_cli:main)
- Rename root cli.py -> companion_cli.py (avoid stdlib name collision)
- Remove divergent package copy: src/hermes_companion/{server,setup_wizard,config,cli}.py
- Update systemd unit to invoke -m companion_cli serve
- Regenerate egg-info (now empty; will be rebuilt on pip install -e .)
- Tests still pass (they target root already)
EOF
)"
```

## Test plan

- **Existing tests** must continue to pass: `python -m pytest -xvs`
- **New smoke tests** in `test_consolidation.py` (create):
  - `test_server_module_loads` — `import server` succeeds
  - `test_companion_cli_module_loads` — `import companion_cli` succeeds
  - `test_root_server_has_all_routes` — verify 18 route registrations present (count >= 18)
  - `test_package_copy_files_gone` — assert `not Path('src/hermes_companion/server.py').exists()` etc.
  - `test_entry_point_resolves` — parse `setup.py` and `pyproject.toml`, verify `companion_cli:main` declared

Pattern: follow `test_config.py:1-50` (module-level imports + simple assertions).

## Done criteria

- [ ] `python -m pytest -xvs` exits 0 (all existing tests pass)
- [ ] `python -c "import server"` exits 0
- [ ] `python -c "import companion_cli"` exits 0
- [ ] `ls src/hermes_companion/server.py src/hermes_companion/setup_wizard.py src/hermes_companion/config.py src/hermes_companion/cli.py` reports no such files
- [ ] `grep -rn "server.cli:main" setup.py pyproject.toml` returns no matches
- [ ] `git status` clean (commit done)
- [ ] `plans/README.md` Plan 001 row updated to `DONE`

## STOP conditions

- Drift check (Step 1) reveals changes in-scope — STOP, the plan is stale.
- Baseline tests fail BEFORE any changes — STOP, surface pre-existing failure to user.
- Tests fail AFTER deletions (Step 9) that don't resolve by import path fix — STOP, may need a deeper refactor.
- Smoke import fails for a root module — STOP, the root copy may also be broken.

## Maintenance notes

- After this plan lands, the daemon is a flat package. New contributors should look at `server.py` and `companion_cli.py` only.
- If `setup.py` is removed in favor of pure `pyproject.toml`, do that in a separate plan (it's not required for this consolidation).
- The `src/hermes_companion/` directory becomes a near-empty marker. If `__init__.py` is also removable, do that separately.
- Future daemon fixes (002, 003, 006, 007, 009) now have a single canonical codebase to modify.
