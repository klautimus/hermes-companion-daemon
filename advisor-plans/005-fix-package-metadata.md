# Plan 005: Fix `pip install` package metadata — make the daemon installable

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- setup.py pyproject.toml`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (only affects installability, not runtime behavior)
- **Depends on**: none
- **Category**: bug (deployment)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

The daemon's packaging is broken:
- `setup.py:19` declares `packages=find_packages(include=["server*"])` — but no `server/` directory exists
- `pyproject.toml:28-29` declares the same broken glob
- `pyproject.toml:43` has `[tool.setuptools.package-data] "server" = ["*.py"]` — invalid key (no `server` package)

**Net effect**: `pip install -e .` and `pip install .` both fail. The `hermes-companion` console script (declared in `pyproject.toml:38-39`) cannot be installed. If anyone tries to set up the daemon on a new machine, they'll hit the broken metadata.

This is a hygiene fix — the actual production deployment uses `python3 server.py` directly, not `pip install`. But broken metadata means:
- New contributors can't install dev dependencies
- The README's "pip install" instructions (if any) don't work
- The package shows up as broken in any PyPI upload attempt

## Current state (verified 2026-06-16 by Atlas)

```
$ cd /home/kevin/.hermes/companion && python3 -c "from setuptools import find_packages; print(find_packages(include=['server*']))"
[]

$ pip install -e . --dry-run 2>&1 | tail -5
error: ... 'tool.setuptools.package-data' keys must be python-module-name
       (key "server" is invalid)
```

**Actual repo layout**:
```
~/.hermes/companion/
├── setup.py                  # declares broken `server*` package
├── pyproject.toml            # same broken config
├── server.py                 # root module
├── companion_cli.py          # root module
├── config.py                 # root module
├── config_schema.py          # root module
├── first_run.py              # root module
├── setup_wizard.py           # root module
├── src/
│   └── hermes_companion/     # only contains __init__.py, systemd/, templates/
│       └── (mostly empty)
├── tests/                    # pytest tests
├── conftest.py
└── ...
```

The actual production modules are at repo root. The `src/hermes_companion/` package contains only scaffolding files.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Validate setup.py parses | `cd /home/kevin/.hermes/companion && python3 -c "import setup; print('OK')"` | `OK` |
| Validate pyproject.toml parses | `cd /home/kevin/.hermes/companion && python3 -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"` | `OK` |
| Build sdist (dry run) | `cd /home/kevin/.hermes/companion && python3 -m build --sdist --dry-run 2>&1 | tail -5` | no error |
| Try pip install (may fail in PEP 668 env) | `cd /home/kevin/.hermes/companion && pip install -e . 2>&1 | tail -5` | see Step 4 |
| Run tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -3` | 56 + 49 + 8 = 113 passed (after Plan 004) |

## Scope

**In scope**:
- `~/.hermes/companion/setup.py` — change `packages=find_packages(include=["server*"])` to use root modules
- `~/.hermes/companion/pyproject.toml` — remove broken `[tool.setuptools.packages.find]` and `[tool.setuptools.package-data]` sections; add a `[tool.setuptools]` block with `py-modules` listing the root modules

**Out of scope** (do NOT touch):
- The actual module files (server.py, companion_cli.py, etc.) — they don't change
- The src/hermes_companion/ directory — it stays as-is (used for systemd unit template and Jinja2 templates)
- The tests/ directory — orthogonal

## Git workflow

- Daemon repo branch: `advisor/005-fix-package-metadata`
- Single commit is fine (this is a small fix)
- Message style: imperative, scoped (`fix(packaging): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Update setup.py

**Edit** `~/.hermes/companion/setup.py`:

Replace the `packages=find_packages(include=["server*"])` line with `py_modules` listing the root modules:

```python
from setuptools import setup

# Modules live at the repo root, not in a package. py_modules makes them
# importable when installed via pip.
ROOT_MODULES = [
    "server",
    "companion_cli",
    "config",
    "config_schema",
    "first_run",
    "setup_wizard",
]

setup(
    name="hermes-companion-server",
    version="0.2.0",
    description="Hermes Companion Server — HTTP shim for Hermes API + Kanban CLI",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Hermes Community",
    author_email="community@hermes-agent.nousresearch.com",
    license="MIT",
    python_requires=">=3.10",
    py_modules=ROOT_MODULES,           # <-- changed from broken find_packages
    install_requires=[
        "aiohttp>=3.9",
        "pyyaml>=6.0",
        "qrcode[pil]>=7.4",
    ],
    entry_points={
        "console_scripts": [
            "hermes-companion=companion_cli:main",
        ],
    },
    # Include systemd unit and templates from the hermes_companion package
    package_data={},
)
```

**Verify**: `cd /home/kevin/.hermes/companion && python3 setup.py --classifiers 2>&1 | head -5` → shows classifiers without error.

### Step 2: Update pyproject.toml

**Edit** `~/.hermes/companion/pyproject.toml`:

Replace the broken `[tool.setuptools.packages.find]` and `[tool.setuptools.package-data]` sections with a single `[tool.setuptools]` block:

```toml
# REMOVE these:
# [tool.setuptools.packages.find]
# where = ["."]
# include = ["server*"]
#
# [tool.setuptools.package-data]
# "server" = ["*.py"]
# "" = ["hermes-companion-user.service"]

# ADD this:
[tool.setuptools]
py-modules = [
    "server",
    "companion_cli",
    "config",
    "config_schema",
    "first_run",
    "setup_wizard",
]
```

Keep the rest of `pyproject.toml` unchanged. The `name`, `version`, `dependencies`, `optional-dependencies`, `project.scripts`, `tool.pytest.ini_options` sections all stay as-is.

**Verify**: `cd /home/kevin/.hermes/companion && python3 -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"` → `OK`

### Step 3: Validate the package builds

```bash
cd /home/kevin/.hermes/companion
python3 -m pip install --break-system-packages -e . --user 2>&1 | tail -5
```

Note: `--break-system-packages` is needed on PEP 668 systems. If the install succeeds, the modules should be importable from anywhere:

```bash
cd /tmp
python3 -c "import server; print(server.__file__)"
# Expected: .../site-packages/server.py or similar
```

If the install fails for an unrelated reason (e.g., dependency resolution), report the exact error and STOP — that's a different problem.

### Step 4: Confirm tests still pass

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: same as before the change (56 pass + 49 skipped, or 113 pass after Plan 004).

### Step 5: Update the README if it has install instructions

If `~/.hermes/companion/README.md` has a "How to install" section that references `pip install -e .`, verify it now works. If not, add a one-line section:

```markdown
## Installation (optional — production runs `python3 server.py` directly)

```bash
cd ~/.hermes/companion
pip install -e . --break-system-packages --user
# Now `hermes-companion setup` and `hermes-companion serve` are on PATH
```
```

## Test plan

No new automated tests. Verification is:
1. `python3 setup.py --classifiers` doesn't error
2. `pip install -e .` succeeds (or fails for unrelated reasons, in which case report)
3. `python3 -m pytest tests/` passes (same as before)
4. After install, `python3 -c "import server; print(server.__file__)"` works from any directory

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/companion && python3 -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"` exits 0
- [ ] `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` shows 56 pass (before Plan 004) or 113 pass (after Plan 004), 0 failures
- [ ] `grep -n "include.*server\*" setup.py pyproject.toml` returns no matches
- [ ] `grep -n "py-modules" pyproject.toml` returns a match
- [ ] `pip install -e . --break-system-packages --user` succeeds (or fails only for unrelated dependency reasons)
- [ ] `git status` is clean in daemon repo (commit the change)

## STOP conditions

Stop and report back (do not improvise) if:
- The pip install fails for a reason unrelated to the broken metadata (e.g., dependency conflict, network error)
- After the fix, `pytest tests/` starts failing (the fix shouldn't affect tests, but verify)
- The `hermes-companion` console script doesn't appear on PATH after install — the entry point config may need additional work

## Maintenance notes

- The `py_modules` approach is the simplest fix for a flat-layout Python project. If the project later moves to a `src/`-layout (with all modules under `src/hermes_companion/`), the `py_modules` block can be replaced with a proper `packages` declaration.
- The systemd unit template at `src/hermes_companion/systemd/hermes-companion.service` references `python3 -m companion_cli serve` which assumes the module is importable. After this fix, that command works (when installed). For development without install, use `python3 companion_cli.py serve` instead.
