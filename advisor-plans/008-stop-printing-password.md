# Plan 008: Stop printing generated password to stdout in setup wizard

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- setup_wizard.py`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P0
- **Effort**: S
- **Risk**: LOW (one-line delete)
- **Depends on**: none
- **Category**: bug (security - credential hygiene)
- **Planned at**: commit `d378902`, 2026-06-16

## Why this matters

`setup_wizard.py:322` does `print(f"   Generated secure password: {password}")` — a 32-character token printed to the user's terminal. This ends up in:
- Shell history (if the user copies the command from a tutorial)
- Terminal scrollback
- Any screen recording
- CI logs if the wizard is run in a test environment

**The whole point of the QR token flow (Plan 003) was to avoid this.** But the password is STILL printed, defeating the security improvement.

## Current state (verified 2026-06-16 by Atlas)

**File**: `~/.hermes/companion/setup_wizard.py:322`

```python
print(f"   Generated secure password: {password}")
```

The connection info section (around line 235) ALREADY says "Password: (transferred via secure QR token — check your mobile app)". The print at line 322 is redundant and leaks the password.

## Scope

**In scope**:
- `~/.hermes/companion/setup_wizard.py` line 322 — delete the `print(...)` call

**Out of scope**:
- The connection info block at line 235 (which is correct)
- The QR code generation (which transfers the token securely)

## Steps

### Step 1: Delete the print

**Edit** `~/.hermes/companion/setup_wizard.py`:

Find the line:
```python
    print(f"   Generated secure password: {password}")
```

Delete it (or replace with a non-secret message like `print("   Password generated (transferred via QR token)")`).

**Verify**:
```bash
cd /home/kevin/.hermes/companion
grep -n "Generated secure password" setup_wizard.py
# Expected: no output
```

### Step 2: Run tests

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ 2>&1 | tail -3
# Expected: 0 failures
```

## Done criteria

- [ ] `grep -n "Generated secure password" setup_wizard.py` returns 0 matches
- [ ] `python3 -m pytest tests/ 2>&1 | tail -1` shows 0 failures
- [ ] `git status` is clean (commit)

## STOP conditions

- The deleted print was being used by some test that asserts on stdout output — find the test and update it to expect the new (no-print) behavior
