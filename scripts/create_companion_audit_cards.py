#!/usr/bin/env python3
"""
Create the companion-audit-v4 board cards.

Strict serial chain (Kevin's "only one task at a time" rule):
  001 -> 002 -> 003 -> 006 -> 007 -> 009 -> 004 -> 005 -> 008 -> GATE

This is a deliberate override of the plans/README.md logical dep graph
(where 002-009 all had parent=001).  The logical graph would dispatch
all 8 children simultaneously after 001, but they all touch the same
server.py / setup_wizard.py / Android Kotlin source files — concurrent
workers would create merge conflicts and lost edits.

Each plan body is INLINED into its card (improve rule: workers see
zero audit context).  Autonomous Board Directive + MANDATORY SKILL
LOADING + Pipeline Discipline are prepended to every card body.

Card body construction rules (kanban-orchestrator skill):
- Use subprocess.run with list args (no shell escaping)
- Use Python .format() or %, NOT f-strings (curly braces in code blocks)
- Double backslashes in Windows paths (none here, all WSL/Linux)
- Avoid the § character in card bodies
- Inline the plan text using .read() of plans/NNN-*.md

Profile assignment: ops (all implementation cards).
GATE: ops, goal_mode=True, goal_max_turns=1000, priority=10.
"""

import json
import os
import re
import subprocess
import sys
import time

BOARD = "companion-audit-v4"
PROFILE = "ops"
PLANS_DIR = "/home/kevin/.hermes/companion/plans"

# Strict serial order. Each plan is a child of the previous plan's
# implementation (not the plan's logical dep — see comment at top).
SERIAL_ORDER = [
    "001-daemon-consolidate.md",
    "002-auth-hardening.md",
    "003-qr-setup-token.md",
    "006-attachment-streaming.md",
    "007-config-validation.md",
    "009-security-headers.md",
    "004-android-encrypted-prefs.md",
    "005-network-security-config.md",
    "008-android-test-rewrite.md",
]

# Autonomous Board Directive (prepended to every card body, FIRST section)
AUTONOMOUS_DIRECTIVE = """## Autonomous Board Directive
**This is a fully autonomous kanban board. You are a worker in a continuous loop.**
- Do NOT self-block for human review. Tests passing = task complete.
- Do NOT request review handoff. Dedicated review happens at the GATE task.
- `kanban_complete` when your acceptance criteria are met. Do not `kanban_block` unless the task is genuinely impossible.
- If you finish your work and all verifications pass, complete. The next card in the serial chain handles the next phase.
- This board is a STRICT SERIAL CHAIN — only one card runs at a time. The previous card MUST be done before you start. Do not race siblings.
"""

# MANDATORY SKILL LOADING block (SECOND section in every card body).
# Workers MUST call these skill_view() calls in order before any other work.
SKILL_LOADING = """## MANDATORY SKILL LOADING (execute IN ORDER, do not skip)
1. skill_view(name="using-agent-skills") — load the router FIRST
2. skill_view(name="incremental-implementation") — for code changes
3. skill_view(name="test-driven-development") — for tests
4. skill_view(name="code-review-and-quality") — for self-review before completing
interview-me SKIPS — intent is locked in the plan body below. No deviations from the plan.

If the plan involves a broken feature, ALSO load: skill_view(name="debugging-and-error-recovery") between steps 2 and 3.
"""

# Pipeline Discipline (THIRD section, human-readable form of the skill chain)
PIPELINE_DISCIPLINE = """## Pipeline Discipline
Follow this chain in order:
1. Load using-agent-skills (router) — done in MANDATORY SKILL LOADING above
2. Load incremental-implementation — make the code change
3. (If broken) Load debugging-and-error-recovery — reproduce, localize, reduce, fix, guard
4. Load test-driven-development — write/run tests per the plan's "Test plan" section
5. Load code-review-and-quality — self-review against the plan's "Done criteria"
6. Run every command in "Verification" below. If all pass, `kanban_complete`. If any fail, fix and re-run. Do not improvise STOP conditions.
"""


def make_card_body(plan_filename: str) -> str:
    """Build a card body by prepending directive + skill loading + pipeline
    discipline to the inlined plan text."""
    plan_path = os.path.join(PLANS_DIR, plan_filename)
    with open(plan_path) as f:
        plan_text = f.read()

    # Extract the plan's Verification / Done criteria section for emphasis
    # (the worker will read the full plan anyway, but this makes the
    # gate-style acceptance checklist easy to find).
    body = (
        AUTONOMOUS_DIRECTIVE
        + "\n---\n\n"
        + SKILL_LOADING
        + "\n---\n\n"
        + PIPELINE_DISCIPLINE
        + "\n---\n\n"
        + "## INLINED PLAN (full text — workers see no audit context)\n\n"
        + plan_text
    )
    return body


def make_title(plan_filename: str) -> str:
    """001-daemon-consolidate.md -> 'implement: 001 daemon consolidate'"""
    stem = plan_filename.replace(".md", "")
    # Replace hyphens with spaces after the number
    m = re.match(r"^(\d+)-(.+)$", stem)
    if not m:
        return f"implement: {stem}"
    num, slug = m.group(1), m.group(2)
    slug = slug.replace("-", " ")
    return f"implement: {num} {slug}"


def create_card(
    title: str,
    body: str,
    parent_id: str | None,
) -> str:
    """Run `hermes kanban create` and return the new task_id."""
    env = os.environ.copy()
    env["HERMES_KANBAN_BOARD"] = BOARD
    env["HERMES_PROFILE"] = "default"  # dispatcher uses default for ops, fine

    cmd = [
        "hermes", "kanban", "create", title,
        "--body", body,
        "--assignee", PROFILE,
        "--json",
    ]
    if parent_id:
        cmd.extend(["--parent", parent_id])

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, env=env,
    )
    if result.returncode != 0:
        print(f"FAILED to create card '{title}'", file=sys.stderr)
        print(f"  stdout: {result.stdout}", file=sys.stderr)
        print(f"  stderr: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"card creation failed: {title}")

    # --json output.  Strip any leading non-JSON line(s) per the
    # skill's documented quirk (output starts with a request ID line).
    out = result.stdout.strip()
    # Find the first '{'
    idx = out.index("{")
    data = json.loads(out[idx:])

    # Field name varies: try 'task_id' then 'id'
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        print(f"NO task_id in response: {data}", file=sys.stderr)
        raise RuntimeError("no task_id in create response")
    return task_id


def main() -> None:
    print(f"Creating cards on board '{BOARD}' (serial chain)")
    print(f"  Profile: {PROFILE}")
    print(f"  Plans: {len(SERIAL_ORDER)} cards + 1 GATE")
    print()

    created: list[tuple[str, str]] = []  # (plan_filename, task_id)
    prev_id: str | None = None

    for pf in SERIAL_ORDER:
        if not os.path.exists(os.path.join(PLANS_DIR, pf)):
            raise FileNotFoundError(f"plan file missing: {pf}")
        title = make_title(pf)
        body = make_card_body(pf)
        print(f"  Creating: {title}")
        print(f"    parent: {prev_id or '(none)'}")
        print(f"    body length: {len(body)} chars")
        tid = create_card(title, body, prev_id)
        print(f"    -> {tid}")
        created.append((pf, tid))
        prev_id = tid
        # Brief pause to avoid hammering the dispatcher
        time.sleep(0.5)

    # Create the terminal GATE card.  GATE depends on ALL plan cards.
    gate_title = "GATE: companion-audit-v4 recursive verification"
    gate_body = make_gate_body([tid for _, tid in created])
    all_parent_ids = [tid for _, tid in created]
    print()
    print(f"  Creating: {gate_title}")
    print(f"    parents: {len(all_parent_ids)} cards")

    env = os.environ.copy()
    env["HERMES_KANBAN_BOARD"] = BOARD
    cmd = [
        "hermes", "kanban", "create", gate_title,
        "--body", gate_body,
        "--assignee", PROFILE,
        "--goal", "--goal-max-turns", "1000",
        "--priority", "10",
        "--json",
    ]
    for pid in all_parent_ids:
        cmd.extend(["--parent", pid])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    if result.returncode != 0:
        print(f"FAILED to create GATE: {result.stderr}", file=sys.stderr)
        raise RuntimeError("gate creation failed")
    out = result.stdout.strip()
    idx = out.index("{")
    data = json.loads(out[idx:])
    gate_id = data.get("task_id") or data.get("id")
    print(f"    -> {gate_id}")

    # Summary
    print()
    print("=" * 60)
    print("CARD CREATION SUMMARY")
    print("=" * 60)
    for pf, tid in created:
        print(f"  {tid}  {make_title(pf)}")
    print(f"  {gate_id}  {gate_title}")
    print()
    print(f"Total: {len(created)} plan cards + 1 GATE = {len(created) + 1} cards")
    print()
    print("All task IDs (for comment + cron steps):")
    for pf, tid in created:
        print(f"  {tid}  # {pf}")
    print(f"  {gate_id}  # GATE")


def make_gate_body(plan_task_ids: list[str]) -> str:
    """Build the recursive GATE card body.

    The GATE runs every plan's Done criteria commands. If any fail, it
    spawns a sub-board (companion-audit-v4-fix-N) with the full chain
    re-run. Loops until zero failures.
    """
    return (
        AUTONOMOUS_DIRECTIVE
        + "\n---\n\n"
        + """## MANDATORY SKILL LOADING (execute IN ORDER)
1. skill_view(name="using-agent-skills") — load the router FIRST
2. skill_view(name="improve", file_path="references/closing-the-loop.md") — load the execute/reconcile playbook
3. skill_view(name="kanban-orchestrator") — load the orchestrator playbook
4. Read references/audit-board-playbook.md (audit-specific decomposition patterns)
interview-me SKIPS — intent is locked: verify all plans and spawn fix sub-boards as needed.

## Pipeline Discipline
This is a recursive verification gate. Follow the loop algorithm below EXACTLY.
"""
        + "\n---\n\n"
        + """## Task: RECURSIVE GATE (goal-mode, 1000 turns)

You are the terminal gate for the companion-audit-v4 board. Your job is to verify that every plan in `~/.hermes/companion/plans/` was executed correctly, and to spawn fix sub-boards if any verification fails.

### Algorithm

```
LOOP:
  1. Run the verification commands from EVERY plan's "Done criteria" section.
     For each plan (001 through 009), run every command listed.
  2. If ALL verifications pass for ALL 9 plans:
     a. Run the daemon repo tests: cd /home/kevin/.hermes/companion && python -m pytest -xvs
     b. Run the Android build: cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug
     c. If both pass: mark verified, write a GATE_REPORT.md summary, kanban_complete (DONE)
  3. If ANY verification fails:
     a. Collect the full failure list (which plan, which command, what output)
     b. Spawn a sub-board: hermes kanban boards create companion-audit-v4-fix-<N>
        (where N = 1, 2, 3, ...)
     c. Populate the sub-board with the FULL chain: each failing plan becomes a card
        on the sub-board, with the Autonomous Board Directive + MANDATORY SKILL LOADING
        prepended. The sub-board gate is a clone of THIS card body.
     d. kanban_complete (handoff — the sub-board takes over)
  4. SOFA gating: if the same test fails across 2 fix iterations, search SOFA
     (https://agents.stackoverflow.com) before iteration 3:
     POST /api/posts with title/body/tags describing the error
```

### Sub-board spawn details

When creating companion-audit-v4-fix-N:
- Create the board: `hermes kanban boards create companion-audit-v4-fix-N`
- For each failing plan from the original 9, create a card on the sub-board
  with the SAME body that was on the original card (re-inlined plan + directive + skill loading)
- Wire STRICT serial chain: each card parent = previous card (matches the original chain)
- The sub-board's GATE is a clone of THIS card body
- If sub-board gate fails -> spawn companion-audit-v4-fix-N+1
- Loop until a gate produces ZERO failures

### Why this pattern
Each sub-board gets FRESH context (no bloat from previous iterations).
Each sub-board runs the FULL pipeline (review -> fix -> gate), not just a patch.
The gate body is self-cloning. Clean exit: a gate finds zero failures -> deliverable verified -> board done.

## Verification (machine-checkable)
- [ ] All 9 plans' "Done criteria" commands run; all pass
- [ ] `python -m pytest -xvs` in daemon repo exits 0
- [ ] `./gradlew assembleDebug` in Android repo exits 0
- [ ] GATE_REPORT.md written at /home/kevin/.hermes/companion/GATE_REPORT.md summarizing the verification pass
- [ ] plans/README.md status column updated to DONE for all 9 plans
- [ ] No sub-board still running (if applicable, all -fix-N sub-boards are themselves done)

## STOP conditions
- A plan file is missing from plans/ — STOP, surface to user
- A plan's verification command is unrunnable (env issue) — STOP, surface
- A sub-board creation fails (board name conflict) — STOP, archive old -fix-N boards first
- You cannot find the plan files referenced — STOP, the daemon repo may have been moved

## Maintenance notes
- After this board completes with all verifications green, the companion-audit-v4
  audit is officially closed. The plans/ directory becomes the historical record.
- Future audits should create a new board (companion-audit-v5) and write new plans/,
  not amend this one.
- The watchdog cron (companion-audit-v4-watchdog) will self-delete when all
  boards (main + sub-boards) are done.
"""
    )


if __name__ == "__main__":
    main()
