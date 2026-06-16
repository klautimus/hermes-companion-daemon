#!/usr/bin/env python3
"""Create the recursive GATE card for companion-audit-v5.

The GATE body includes the P0 fix from June 16 pipeline-audit-2026-06-16:
  - Machine-Checkable Verification Checklist
  - Re-run every Done-criteria command from every plan
  - Refuse kanban_complete if ANY exit code is non-zero
  - Spawn fix sub-boards on failure (not single fix cards)
"""

import json
import os
import subprocess
import sys

BOARD = "companion-audit-v5"

os.environ["HERMES_KANBAN_BOARD"] = BOARD

# Load plan_ids from the previous step
with open("/tmp/companion-audit-v5-plan-ids.json") as f:
    plan_ids = json.load(f)

# All plan task IDs become parents of the GATE
parent_ids = list(plan_ids.values())

GATE_BODY = """## Autonomous Board Directive
**This is a fully autonomous kanban board. You are a worker in a continuous loop.**
- Do NOT self-block for human review. Tests passing = task complete.
- Do NOT request review handoff. Dedicated review happens at a designated upstream task.
- `kanban_complete` when your acceptance criteria are met. Do not `kanban_block` unless the task is genuinely impossible.
- If you finish your work and all verifications pass, complete. The next task in the chain handles verification.
- **Before calling `kanban_complete`, run `git add -A && git commit` in both repos.** The board is "done" only when the git trees reflect all the work.

## MANDATORY SKILL LOADING (execute IN ORDER, do not skip)
1. `skill_view(name="using-agent-skills")` — load the router FIRST
2. `skill_view(name="improve", file_path="references/closing-the-loop.md")` — gate protocol
3. `skill_view(name="kanban-orchestrator")` — board creation + watchdog

## Task: RECURSIVE GATE (goal-mode, 1000 turns)

### Machine-Checkable Verification Checklist (MANDATORY — DO NOT SKIP)

**YOU MUST RUN EVERY VERIFICATION COMMAND YOURSELF. DO NOT TRUST plans/README.md STATUS.**

For each plan in `~/.hermes/companion/advisor-plans/`:

1. Run EVERY command from the plan's "Done criteria" section.
2. Capture the exit code AND the relevant output (last 50 lines minimum).
3. If exit code != 0 → record FAILURE with: plan number, command, exit code, stderr summary.
4. Count: TOTAL commands, PASSED (exit 0), FAILED (exit != 0).

**REQUIRED OUTPUT FORMAT (must appear at top of your final report):**
```
VERIFICATION SUMMARY:
  Total commands: N
  Passed: N
  Failed: N
  Failed commands:
    - plan 007: `python -m pytest tests/test_subprocess_errors.py` -> exit 1 (collection error)
    - plan 001: `curl -fsS http://127.0.0.1:8777/healthz` -> exit 7 (connection refused)
```

**COMPLETION RULE: You may ONLY `kanban_complete` if Failed: 0.**
If ANY failure → collect full list → spawn sub-board → `kanban_complete` (handoff).

### Verification commands by plan

**Plan 001 (bring daemon up)**:
- `systemctl --user status hermes-companion.service` (expect: active (running))
- `ss -tlnp | grep 8777` (expect: LISTEN on 127.0.0.1:8777)
- `curl -fsS http://127.0.0.1:8777/healthz` (expect: `{"status":"ok"}`)
- `systemctl --user status cloudflared.service` (expect: active (running))
- `curl -fsS https://android.kevlarscreations.com/healthz` (expect: `{"status":"ok"}`)

**Plan 002 (QR token Android)**:
- `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug --no-daemon` (expect: BUILD SUCCESSFUL)
- `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test --no-daemon` (expect: BUILD SUCCESSFUL, all tests pass)
- `grep -n 'data.getQueryParameter("token")' app/src/main/java/org/hermes/community/companion/MainActivity.kt` (expect: match)
- `grep -n 'data.getQueryParameter("token")' app/src/main/java/org/hermes/community/companion/QrScannerScreen.kt` (expect: match)
- `cd /home/kevin/.hermes/companion && curl -fsS -X POST http://127.0.0.1:8777/api/setup/redeem -H "Content-Type: application/json" -d '{"token":"<test_token>"}'` (expect: 200 with credentials, OR 404 with `{"error": "Invalid token"}` — both prove the endpoint is wired)

**Plan 003 (fail-closed encrypted prefs)**:
- `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug --no-daemon` (expect: BUILD SUCCESSFUL)
- `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test --no-daemon` (expect: BUILD SUCCESSFUL)
- `grep -n "Insecure storage detected" app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt` (expect: match)
- `grep -n "PLAINTEXT" app/src/main/java/org/hermes/community/companion/SettingsScreen.kt` (expect: match)

**Plan 004 (re-enable skipped tests)**:
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: zero skips, e.g. `113 passed in X.XXs`)
- `cd /home/kevin/.hermes/companion && grep -c "skipped" <(python3 -m pytest tests/ 2>&1)` (expect: 0)
- `cd /home/kevin/.hermes/companion && grep -n "pytestmark = pytest.skip" tests/test_setup_wizard.py tests/test_first_run.py` (expect: no matches)

**Plan 005 (fix package metadata)**:
- `cd /home/kevin/.hermes/companion && python3 -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"` (expect: OK)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)
- `cd /home/kevin/.hermes/companion && grep -n "include.*server\\*" setup.py pyproject.toml` (expect: no matches)
- `cd /home/kevin/.hermes/companion && grep -n "py-modules" pyproject.toml` (expect: match)
- `cd /home/kevin/.hermes/companion && python3 -m pip install --break-system-packages -e . --user 2>&1 | tail -3` (expect: Successfully installed hermes-companion-server-0.2.0)

**Plan 006 (synchronize setup tokens)**:
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/test_setup_token.py -v 2>&1 | grep -c "PASSED"` (expect: >= 14 — at least the new concurrent test passes)
- `cd /home/kevin/.hermes/companion && grep -n "_setup_tokens_lock" server.py` (expect: 3+ matches)
- `cd /home/kevin/.hermes/companion && grep -n "_SETUP_TOKENS.pop" server.py` (expect: 1+ match)

**Plan 007 (sanitize subprocess errors)**:
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)
- `cd /home/kevin/.hermes/companion && grep -n "err or " server.py` (expect: 0 matches)
- `cd /home/kevin/.hermes/companion && grep -c "_sanitized_error_response" server.py` (expect: >= 14)

**Plan 008 (stop printing password)**:
- `cd /home/kevin/.hermes/companion && grep -n "Generated secure password" setup_wizard.py` (expect: 0 matches)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)

**Plan 009 (fix time-mixing)**:
- `cd /home/kevin/.hermes/companion && grep -n "time.monotonic" server.py` (expect: no matches in _SETUP_TOKENS / register_setup_token / handle_setup_redeem context)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)

**Plan 010 (stream attachments)**:
- `cd /home/kevin/.hermes/companion && grep -n "web.FileResponse" server.py` (expect: 1+ match in handle_attachment_serve)
- `cd /home/kevin/.hermes/companion && grep -n "on_cleanup" server.py` (expect: 1+ match)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)

**Plan 011 (Android release signing)**:
- `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleRelease --no-daemon 2>&1 | tail -5` (expect: BUILD SUCCESSFUL)
- `ls -la /home/kevin/.hermes/projects/HermesCompanion/app/build/outputs/apk/release/app-release.apk` (expect: file exists, <15MB)
- `cd /home/kevin/.hermes/projects/HermesCompanion && jarsigner -verify app/build/outputs/apk/release/app-release.apk 2>&1 | tail -1` (expect: jar verified)
- `cd /home/kevin/.hermes/projects/HermesCompanion && grep -n "data_extraction_rules" app/src/main/AndroidManifest.xml` (expect: match)
- `ls -la /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk` (expect: file exists, deliverable on Desktop)

**Plan 012 (rate limit redeem)**:
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/test_auth_hardening.py -v 2>&1 | tail -5` (expect: 0 failures, new rate-limit test passes)
- `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures)

### Sub-board Spawn (Recursive)

On failure:
1. Create: `hermes kanban boards create companion-audit-v5-fix-<N>` (increment N each iteration)
2. Populate with full chain per audit-board-playbook.md (parallel Phase 1 → serial implementation → gate)
3. Gate of sub-board = EXACT clone of THIS card body (including this checklist)
4. Loop until zero failures

### Final deliverable check (after all verifications pass)

1. Confirm daemon is up: `curl -fsS http://127.0.0.1:8777/healthz` returns `{"status":"ok"}`
2. Confirm tunnel is up: `curl -fsS https://android.kevlarscreations.com/healthz` returns `{"status":"ok"}`
3. Confirm release APK is on Desktop: `ls -la /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk` succeeds
4. Confirm pytest has zero skips: `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` shows `113 passed` or similar with no skips
5. Confirm Android build: `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug --no-daemon` succeeds
6. Send completion summary to discord:klauts_ via `send_message`

## Done Criteria (MACHINE-CHECKABLE)

- [ ] All 12 plans verified (every "Done criteria" command exit 0)
- [ ] VERIFICATION SUMMARY block present with Failed: 0
- [ ] No sub-boards still running
- [ ] advisor-plans/README.md status column = DONE for all 12 plans
- [ ] Final deliverable check (6 items) all pass
- [ ] Git trees clean in both repos
- [ ] Discord message sent to klauts_ with completion summary
- [ ] Release APK at /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk
"""

# Create the GATE card
cmd = ["hermes", "kanban", "create", "GATE: recursive audit verification (companion-audit-v5)",
       "--body", GATE_BODY,
       "--assignee", "ops",
       "--goal", "--goal-max-turns", "1000",
       "--priority", "10",
       "--json"]

for pid in parent_ids:
    cmd.extend(["--parent", pid])

print(f"Creating GATE with {len(parent_ids)} parents...", file=sys.stderr)
result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
if result.returncode != 0:
    print(f"FAILED: {result.stderr}", file=sys.stderr)
    sys.exit(1)

# Parse JSON
stdout = result.stdout
start = stdout.find("{")
data = json.loads(stdout[start:])
gate_id = data.get("task_id") or data.get("id")
print(f"GATE created: {gate_id}", file=sys.stderr)

# Save GATE ID
with open("/tmp/companion-audit-v5-gate-id.json", "w") as f:
    json.dump({"gate_id": gate_id, "plan_ids": plan_ids}, f, indent=2)

print(f"\nAll set. Gate ID saved to /tmp/companion-audit-v5-gate-id.json")
