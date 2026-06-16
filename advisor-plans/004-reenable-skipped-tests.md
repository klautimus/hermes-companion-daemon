# Plan 004: Re-enable 49 security-critical skipped tests by porting to the post-Plan-001 API

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- tests/ setup_wizard.py first_run.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: MED (porting tests may surface real bugs in the post-Plan-001 code that the old tests caught in the old code)
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

The v4 round's GATE claimed "56 tests pass" and the watchdog reported clean. **But 49 of those 105 collected tests are skipped via `pytest.mark.skipif`.** The 49 skipped tests cover exactly the security-critical code paths:

- **scrypt password hashing** (format correctness, salt uniqueness, custom params) — 6 tests
- **Password generation** (length, uniqueness) — 2 tests
- **QR code URI construction** (no plaintext password leak) — 4 tests
- **Auth file creation** (file permissions 0o600, parent dir creation, created_at field) — 6 tests
- **Config file creation** (valid YAML) — 4 tests
- **Hermes binary detection** — 2 tests
- **Connection URI generation** — 4 tests
- **QR code generation (no segno fallback)** — 2 tests
- **Interactive prompts** (`prompt`, `prompt_yes_no`) — 9 tests
- **Non-interactive setup flow** (config + auth + attachments dir) — 5 tests
- **CLI entry point** (setup subcommand, KeyboardInterrupt, EOFError) — 5 tests
- **First-run detection** (check_first_run, E2E wizard, YAML roundtrip) — 8 tests

These functions, if broken, lock users out of their own daemon or expose plaintext passwords. **They have zero active coverage.** The "56 pass" headline is technically correct but the verification surface is roughly 35% of what the repo contains.

## Current state (verified 2026-06-16 by Atlas)

```
$ cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1
======================== 56 passed, 49 skipped in 2.44s ========================
```

**Skipped test files**: `tests/test_setup_wizard.py` (entire file skipped) and `tests/test_first_run.py` (entire file skipped, with `pytestmark = pytest.skip(reason="STALE: pre-Plan-001 API")`).

**Why they're skipped**: Plan 001 (daemon-consolidate) restructured the setup wizard and first-run modules. The old function names like `create_auth_json`, `create_config_yaml`, `generate_connection_uri`, `run_wizard`, `main` no longer exist. The tests try to import them, get `ImportError`, and the `try/except ImportError` block at the top of `test_setup_wizard.py:38-61` sets `_LEGACY_IMPORTS_OK = False`, triggering the skipif.

**The actual current API** (post-Plan-001):
- `setup_wizard.py:107` `generate_password() -> str` — same name, but no length/charset params
- `setup_wizard.py:112` `generate_setup_token() -> str` — NEW
- `setup_wizard.py:121` `hash_password(password: str) -> str` — **signature changed**: no kwargs (n, r, p removed)
- `setup_wizard.py:134` `create_auth_file(config: CompanionConfig, username: str, password: str) -> Path` — **renamed from `create_auth_json`**, takes config object
- `setup_wizard.py:163` `generate_qr_code(config: CompanionConfig, username: str, token: str) -> str` — **takes token, not password**
- `setup_wizard.py:243` `register_setup_token_wizard(token, username, password, config)` — NEW
- `setup_wizard.py:run_setup_wizard() -> int` — **renamed from `run_wizard`**, no args
- `setup_wizard.py:prompt_with_default(prompt, default) -> str` — **renamed from `prompt`**
- `setup_wizard.py:prompt_yes_no(prompt, default) -> bool` — **renamed from `prompt_yes_no`**, same name but signature changed

The renamed/moved functions exist — they just don't match the old test imports.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Run all tests | `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ -v` | exit 0, count: 56 + (port) passed, 0 skipped |
| Run specific test file | `python3 -m pytest tests/test_setup_wizard.py -v` | 0 skipped, 0 errored |
| List collected tests | `python3 -m pytest tests/ --collect-only -q 2>&1 | head -40` | 105 collected |

## Scope

**In scope**:
- `~/.hermes/companion/tests/test_setup_wizard.py` — port all 49 tests to the new API
- `~/.hermes/companion/tests/test_first_run.py` — port all 8 tests to the new API
- `~/.hermes/companion/setup_wizard.py` — IF the porting reveals broken behavior, add missing functions; otherwise DO NOT touch

**Out of scope** (do NOT touch):
- The `setup_wizard.py` source — only touch if a test reveals a real bug
- The `first_run.py` source — same
- The `tests/test_server.py` (root-level) — separate plan
- The `tests/test_config.py` (root-level) — separate plan

## Git workflow

- Daemon repo branch: `advisor/004-reenable-skipped-tests`
- Commit per logical step (one commit per test file is fine)
- Message style: imperative, scoped (`test(setup): ...`, `test(first-run): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Read every test that needs porting

```bash
cd /home/kevin/.hermes/companion
wc -l tests/test_setup_wizard.py tests/test_first_run.py
# Expected: ~500 lines test_setup_wizard.py, ~200 lines test_first_run.py
```

Read both files. For each test method, note:
- The function being tested (old name + new name)
- The assertion structure (passes/fails, exact values vs. structural)
- Any setup/fixtures needed (e.g., tmp_path, monkeypatch)

### Step 2: Read the post-Plan-001 API surface

```bash
cd /home/kevin/.hermes/companion
grep -n "^def \|^async def \|^class " setup_wizard.py
# Map every public function
```

For each old test, find the new equivalent function and its new signature.

### Step 3: Port test_setup_wizard.py

**Edit** `~/.hermes/companion/tests/test_setup_wizard.py`:

1. Replace the top-of-file `_LEGACY_IMPORTS_OK` block (lines 38-67) with **real imports**:
   ```python
   from setup_wizard import (
       generate_password,
       generate_setup_token,
       hash_password,
       create_auth_file,
       generate_qr_code,
       render_qr_ascii,
       save_qr_png,
       register_setup_token_wizard,
       run_setup_wizard,
       prompt_with_default,
       prompt_yes_no,
   )
   from config_schema import CompanionConfig, load_config, save_config
   from pathlib import Path
   ```

2. Remove the `pytestmark = pytest.skipif(...)` line (line 55-67).

3. For each test method, update the function call signatures:
   - `create_auth_json(...)` → `create_auth_file(config, username, password)`
   - `create_config_yaml(...)` → `save_config(config, path)`
   - `generate_connection_uri(...)` → `generate_qr_code(config, username, token)` (or use the actual connection URI builder if it's separate)
   - `hash_password(password, n=..., r=..., p=...)` → `hash_password(password)` (the new API doesn't take params)
   - `prompt(prompt_text, default=...)` → `prompt_with_default(prompt_text, default)`
   - `run_wizard(...)` → `run_setup_wizard()` (no args)
   - `generate_qr_code_no_segno(...)` → `generate_qr_code(config, username, token)` (the no-segno fallback was removed; use the new function)

4. For each test, update the assertion to match the new return value:
   - `create_auth_json` returned a Path; `create_auth_file` returns a Path — same
   - `hash_password` old format: `scrypt$8192$4$2$...`; new format: `scrypt$131072$8$1$...` (N=131072 from Plan 002)
   - `prompt` returned user input; `prompt_with_default` returns the user input with default fallback — same semantics
   - `run_wizard` returned nothing; `run_setup_wizard` returns `int` (exit code) — update assertions

5. For each test that asserts on file permissions: `create_auth_file` creates `auth.json` with `0o600` perms (verified in `setup_wizard.py:135-149`). Tests should still pass.

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_setup_wizard.py -v 2>&1 | tail -30
# Expected: 49 passed, 0 skipped, 0 failed
```

If a test fails, **read the failure** — it's likely a real bug in the post-Plan-001 code. Do NOT loosen the assertion; either fix the test to match the new contract (if the new contract is correct) or report the bug back to the orchestrator.

### Step 4: Port test_first_run.py

**Edit** `~/.hermes/companion/tests/test_first_run.py`:

1. Remove the `pytestmark = pytest.skip(reason="STALE: pre-Plan-001 API")` line (line 30-33).
2. Replace old imports with new ones:
   ```python
   from first_run import check_first_run, ensure_configured_or_exit
   from config_schema import CompanionConfig, load_config, save_config
   ```
3. For each test:
   - `check_first_run` — same function name, same signature, same return
   - `create_config_yaml` → `save_config`
   - `create_auth_json` → `create_auth_file`
   - `run_wizard` → `run_setup_wizard`
   - E2E wizard flow test (lines 112-143) — needs to use the new function names and probably needs a tmp_path fixture

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/test_first_run.py -v 2>&1 | tail -15
# Expected: 8 passed, 0 skipped, 0 failed
```

### Step 5: Run the full test suite and confirm zero skips

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 105 passed (or whatever the new total is) in X.XXs
# There should be NO "skipped" in the summary
```

If there are still skips, investigate each — they may be other unrelated skipif markers that need attention.

### Step 6: Add a CI guard against re-skipping

Add to the README or a `tests/conftest.py` (or `pyproject.toml`):

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--strict-markers -ra"
```

The `-ra` flag shows reasons for all skipped tests. Any new skipif will be visible in CI output.

Or, more strictly, add to `tests/conftest.py`:
```python
import pytest

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    skipped = len(terminalreporter.stats.get("skipped", []))
    if skipped > 0:
        terminalreporter.write_sep("=", "WARNING: skipped tests detected", red=True)
        for report in terminalreporter.stats.get("skipped", []):
            terminalreporter.write_line(f"  {report.nodeid}: {report.longrepr}")
        # Don't fail on skips — just warn loudly
```

## Test plan

- All 49 tests in `test_setup_wizard.py` should pass after porting
- All 8 tests in `test_first_run.py` should pass after porting
- Total active tests: 56 + 49 + 8 = 113 (vs. current 56)
- The new tests catch:
  - scrypt hash format regressions (e.g., if someone reverts to weaker N parameter)
  - File permission regressions on auth.json
  - QR code plaintext leak regressions
  - First-run detection regressions (server silently exiting on unconfigured start)
  - CLI entry point regressions

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -3` shows ZERO skips
- [ ] `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | grep -c "skipped"` returns 0
- [ ] `grep -n "pytestmark = pytest.skip" tests/test_setup_wizard.py tests/test_first_run.py` returns no matches
- [ ] `grep -n "_LEGACY_IMPORTS_OK" tests/test_setup_wizard.py` returns no matches
- [ ] All previously-passing 56 tests still pass
- [ ] No files outside the in-scope list are modified (the `setup_wizard.py` source should only be touched IF a real bug is found)
- [ ] `git status` is clean in daemon repo (commit the test changes)

## STOP conditions

Stop and report back (do not improvise) if:
- More than 10 tests fail after porting (suggests a real bug in post-Plan-001 code, not just signature drift) — escalate to orchestrator
- A ported test fails because the new code does something the old test relied on (e.g., the new `hash_password` doesn't validate the format string the old test asserted on) — this is a real bug or a documented API change; report and stop
- The new function signatures don't match what the executor assumed from grep — open the file and verify

## Maintenance notes

- The old test file was a *characterization test* of the old API. Porting it provides a regression net for the new API.
- If future refactors rename functions, the next executor should update both this test file AND any callers. The renamed function pattern is fragile.
- Consider adding `pytest --strict-markers` to CI config so any future `pytest.mark.skip` is intentional.
