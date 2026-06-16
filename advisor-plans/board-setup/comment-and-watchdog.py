#!/usr/bin/env python3
"""Pre-comment all 12 ops cards with anti-self-block directive + create watchdog.

Per kanban-orchestrator skill v4.4.0:
1. Pre-comment is a belt-and-suspenders measure: body-embedded directives propagate
   to spawned children, comments ensure workers see the directive even if card
   bodies were edited post-creation.
2. Watchdog cron job is MANDATORY for every ASDD board.
3. Watchdog MUST cross-check GATE's claimed counts vs. actual pytest/build output
   (P0 fix from June 16 pipeline-audit-2026-06-16).
"""

import json
import os
import subprocess
import sys

BOARD = "companion-audit-v5"
os.environ["HERMES_KANBAN_BOARD"] = BOARD

with open("/tmp/companion-audit-v5-gate-id.json") as f:
    data = json.load(f)
plan_ids = data["plan_ids"]
gate_id = data["gate_id"]

# Pre-comment directive text
DIRECTIVE = """AUTONOMOUS BOARD DIRECTIVE: Do NOT self-block for human review. Tests passing = task complete. kanban_complete when acceptance met. No kanban_block unless genuinely impossible. Before kanban_complete, run `git add -A && git commit` in BOTH repos (daemon at ~/.hermes/companion, Android at ~/.hermes/projects/HermesCompanion). Body-embedded Autonomous Board Directive is authoritative."""


def comment_card(task_id: str, text: str):
    cmd = ["hermes", "kanban", "--board", BOARD, "comment", task_id, "--author", "Atlas (orchestrator)", text]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print(f"FAILED: {result.stderr}", file=sys.stderr)
        return False
    return True


# 1. Pre-comment all 12 plan cards + the gate
print("Pre-commenting all 13 cards with anti-self-block directive...", file=sys.stderr)
for plan_file, task_id in plan_ids.items():
    ok = comment_card(task_id, DIRECTIVE)
    print(f"  {'OK' if ok else 'FAIL'}: {plan_file} ({task_id})", file=sys.stderr)

ok = comment_card(gate_id, DIRECTIVE)
print(f"  {'OK' if ok else 'FAIL'}: GATE ({gate_id})", file=sys.stderr)


# 2. Create the watchdog cron job
print("\nCreating watchdog cron job...", file=sys.stderr)

WATCHDOG_PROMPT = """You are Atlas, orchestrator of the companion-audit-v5 autonomous kanban board.

## Task: Monitor board every 30 minutes (with GATE cross-check)

1. Run: `hermes kanban --board companion-audit-v5 list --json`
2. Also check for sub-boards: `hermes kanban boards list` (look for `companion-audit-v5-fix-*`)
3. For each BLOCKED task:
   a. `hermes kanban --board companion-audit-v5 show <blocked_id>`
   b. Diagnose: review-required? (unblock + comment). Genuinely stuck? (comment fix + unblock). Fabricated results? (comment what's actually needed + unblock)
   c. `hermes kanban --board companion-audit-v5 unblock <blocked_id>`
4. For each RUNNING task that has been running >30 min with no output:
   a. Check workspace: `ls /home/kevin/.hermes/kanban/boards/companion-audit-v5/workspaces/<task_id>/`
   b. Check git: `git -C /home/kevin/.hermes/companion diff HEAD` and `git -C /home/kevin/.hermes/projects/HermesCompanion diff HEAD`
   c. If no output: `hermes kanban reclaim <task_id>` then comment "Reclaimed — no output detected. Worker was stuck. Fresh worker dispatched."
5. **GATE CROSS-CHECK (P0 fix from June 16 pipeline-audit)**:
   - The GATE worker may fabricate pass counts. Independently re-run:
     - `cd /home/kevin/.hermes/companion && python3 -m pytest tests/ 2>&1 | tail -1` (expect: 0 failures, ideally 0 skips)
     - `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug --no-daemon 2>&1 | tail -3` (expect: BUILD SUCCESSFUL)
     - `curl -fsS http://127.0.0.1:8777/healthz` (expect: {"status":"ok"})
     - `curl -fsS https://android.kevlarscreations.com/healthz` (expect: {"status":"ok"})
   - If any fail, comment discrepancy on the GATE card and reclaim.
6. When ALL boards (main + all fix sub-boards) show all tasks done AND the deliverable exists at /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk:
   a. Send Discord completion message to discord:klauts_ via send_message with full summary
   b. Delete this cron job: `hermes cron list` to find the ID, then `hermes cron remove <id>`
7. Always end by sending status to discord:klauts_ (progress, blocked count, boards active)

## Safety
- Do NOT execute workers' tasks. Only diagnose + unblock.
- Do NOT modify task bodies or acceptance criteria.
- Do NOT touch git (no commits/pushes).
- Monitor ALL sub-boards spawned by recursive gate (companion-audit-v5-fix-1, companion-audit-v5-fix-2, ...)
"""

# Use hermes cron create via terminal
result = subprocess.run(
    ["hermes", "cron", "create", "every 30m",
     "--name", "companion-audit-v5-watchdog",
     "--deliver", "origin",
     "--prompt", WATCHDOG_PROMPT,
     "--no-agent", "false"],
    capture_output=True, text=True, timeout=30
)
if result.returncode != 0:
    print(f"FAILED: {result.stderr}", file=sys.stderr)
else:
    print(f"Watchdog created: {result.stdout}", file=sys.stderr)

# Verify
print("\n=== Verification ===", file=sys.stderr)
result = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=15)
print(result.stdout, file=sys.stderr)

result = subprocess.run(["hermes", "kanban", "--board", BOARD, "list"], capture_output=True, text=True, timeout=15)
print(result.stdout, file=sys.stderr)
