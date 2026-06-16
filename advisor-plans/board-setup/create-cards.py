#!/usr/bin/env python3
"""Create the companion-audit-v5 board cards per the audit-board-playbook.

Sequencing: per advisor-plans/README.md "Runtime topology" section:
  Phase 1 (parallel): 003, 004, 008, 011  -- independent files
  Phase 2 (serial daemon metadata): 001 -> 005  -- both touch setup.py/pyproject.toml
  Phase 3 (serial server.py): 006 -> 007 -> 009 -> 010 -> 012  -- all touch server.py
  Phase 4 (Android redeem): 002  -- depends on 001 for end-to-end test

GATE depends on ALL 12 plan cards.

CRITICAL P0 pipeline fixes (from June 16 pipeline-audit-2026-06-16 research):
1. Every plan card body includes Autonomous Board Directive (not self-block) + MANDATORY SKILL LOADING
2. GATE body includes the Machine-Checkable Verification Checklist that RE-RUNS every Done-criteria command
3. Watchdog cross-checks GATE's claimed counts vs. actual pytest/build output
4. Pre-flight: clean test files referencing deleted modules (already done in plan bodies)
"""

import os
import re
import subprocess
import sys

BOARD = "companion-audit-v5"
PROFILE = "ops"
PLANS_DIR = "/home/kevin/.hermes/companion/advisor-plans"

os.environ["HERMES_KANBAN_BOARD"] = BOARD

# ─── Plan metadata (priority, depends on, file-conflict serial chain) ─────────
PLAN_ORDER = [
    # Phase 1: parallel (no shared files)
    "003-fail-closed-encrypted-prefs.md",     # Android
    "004-reenable-skipped-tests.md",          # daemon tests
    "008-stop-printing-password.md",          # daemon (1 line)
    "011-android-release-signing.md",         # Android
    # Phase 2: serial daemon package metadata
    "001-bring-daemon-up.md",                 # touches setup.py + pyproject.toml
    "005-fix-package-metadata.md",            # touches setup.py + pyproject.toml
    # Phase 3: serial daemon server.py
    "006-synchronize-setup-tokens.md",        # touches server.py
    "007-sanitize-subprocess-errors.md",      # touches server.py
    "009-fix-time-mixing.md",                 # touches server.py
    "010-stream-attachments.md",              # touches server.py
    "012-rate-limit-redeem.md",               # touches server.py (depends on 006)
    # Phase 4: Android redeem
    "002-fix-qr-token-android.md",            # depends on 001 for live daemon
]

# Manual dependency map (parent_id) — overrides the chain order above
# Key: plan filename. Value: list of plan filenames this depends on (must be in PLANS_DIR).
DEPS = {
    "001-bring-daemon-up.md":            [],                            # Phase 1, no deps
    "003-fail-closed-encrypted-prefs.md": [],                           # Phase 1, no deps
    "004-reenable-skipped-tests.md":      [],                            # Phase 1, no deps
    "008-stop-printing-password.md":      [],                            # Phase 1, no deps
    "011-android-release-signing.md":     [],                            # Phase 1, no deps
    "005-fix-package-metadata.md":        ["001-bring-daemon-up.md"],    # serial after 001
    "006-synchronize-setup-tokens.md":    ["005-fix-package-metadata.md"],
    "007-sanitize-subprocess-errors.md":  ["006-synchronize-setup-tokens.md"],
    "009-fix-time-mixing.md":             ["007-sanitize-subprocess-errors.md"],
    "010-stream-attachments.md":          ["009-fix-time-mixing.md"],
    "012-rate-limit-redeem.md":           ["010-stream-attachments.md"],
    "002-fix-qr-token-android.md":        ["001-bring-daemon-up.md"],    # for end-to-end test
}

# GATE depends on ALL plan cards
GATE_DEPS = PLAN_ORDER  # everything


# ─── Autonomous Board Directive (P0 pipeline fix) ──────────────────────────
ABD_BLOCK = """## Autonomous Board Directive
**This is a fully autonomous kanban board. You are a worker in a continuous loop.**
- Do NOT self-block for human review. Tests passing = task complete.
- Do NOT request review handoff. Dedicated review happens at a designated upstream task.
- `kanban_complete` when your acceptance criteria are met. Do not `kanban_block` unless the task is genuinely impossible.
- If you finish your work and tests pass, complete. The next task in the chain handles verification.
- **Before calling `kanban_complete`, run `git add -A && git commit` in the workspace.** The board is "done" only when the git tree reflects your work. Marking complete without committing leaves a dirty tree that breaks the next task.

## MANDATORY SKILL LOADING (execute IN ORDER, do not skip)
1. `skill_view(name="using-agent-skills")` — load the router FIRST
2. `skill_view(name="incremental-implementation")` — for code work (or `debugging-and-error-recovery` for fixes)
3. `skill_view(name="test-driven-development")` — verify each slice
4. `skill_view(name="code-review-and-quality")` — self-review before completing

`interview-me` SKIPS — intent locked in the plan body below. No deviations.

"""


def build_card_body(plan_text: str) -> str:
    """Wrap a plan file's body with the Autonomous Board Directive + MANDATORY SKILL LOADING."""
    return ABD_BLOCK + plan_text


def create_card(title: str, body: str, parents: list = None) -> str:
    """Create a card via hermes kanban CLI. Returns task_id."""
    cmd = ["hermes", "kanban", "create", title, "--body", body, "--assignee", PROFILE, "--json"]
    if parents:
        for p in parents:
            cmd.extend(["--parent", p])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"FAILED: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    # Parse JSON — output may have a leading non-JSON line
    stdout = result.stdout
    start = stdout.find("{")
    if start == -1:
        print(f"No JSON in output: {stdout}", file=sys.stderr)
        sys.exit(1)
    import json
    data = json.loads(stdout[start:])
    return data.get("task_id") or data.get("id")


def main():
    plan_ids = {}
    for plan_file in PLAN_ORDER:
        path = os.path.join(PLANS_DIR, plan_file)
        with open(path) as f:
            plan_text = f.read()
        title = plan_file.replace(".md", "").replace("-", " ", 1)
        title = "0" + title  # ensure 0-prefix for clean display: 001 bring daemon up
        # Strip leading 0
        title = re.sub(r"^0(\d)", r"\1", title)

        # Resolve parents to task IDs
        parent_ids = []
        for dep in DEPS[plan_file]:
            if dep not in plan_ids:
                print(f"ERROR: dep {dep} for {plan_file} not yet created", file=sys.stderr)
                sys.exit(1)
            parent_ids.append(plan_ids[dep])

        body = build_card_body(plan_text)
        print(f"Creating {title} (parents={len(parent_ids)})...", file=sys.stderr)
        tid = create_card(title, body, parents=parent_ids if parent_ids else None)
        plan_ids[plan_file] = tid
        print(f"  -> {tid}", file=sys.stderr)

    # Save plan_ids to a sidecar for the gate-creation step
    with open("/tmp/companion-audit-v5-plan-ids.json", "w") as f:
        import json
        json.dump(plan_ids, f, indent=2)

    print(f"\nCreated {len(plan_ids)} plan cards. IDs saved to /tmp/companion-audit-v5-plan-ids.json")
    return plan_ids


if __name__ == "__main__":
    main()
