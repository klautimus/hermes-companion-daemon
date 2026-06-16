#!/usr/bin/env python3
"""Watchdog script for companion-audit-v5 — runs every 30m, reports board status.

Classic watchdog pattern (per cron job docs): no_agent=True, script=path.
Script stdout is delivered verbatim. Empty stdout = silent.

This script checks:
- Board state (any blocked, any running >30min, all done)
- Deploy state (daemon up, tunnel up, tests pass)
- Deliverable state (release APK on Desktop)
And produces a human-readable status report.
"""
import json
import os
import subprocess
import sys

BOARD = "companion-audit-v5"
DISCORD = "discord:klauts_"

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

def main():
    out = []

    # 1. Board status
    out.append("=== companion-audit-v5 board status ===")
    result = run(["hermes", "kanban", "--board", BOARD, "list"])
    out.append(result.stdout)

    # 2. Sub-boards
    result = run(["hermes", "kanban", "boards", "list"])
    fix_boards = [l for l in result.stdout.split("\n") if "companion-audit-v5-fix" in l]
    if fix_boards:
        out.append("\n=== Fix sub-boards ===")
        out.extend(fix_boards)

    # 3. Deploy state (P0 cross-check)
    out.append("\n=== Deploy state (P0 cross-check) ===")
    daemon = run(["curl", "-fsS", "--max-time", "3", "http://127.0.0.1:8777/healthz"])
    out.append(f"Daemon /healthz: {daemon.stdout.strip() if daemon.returncode == 0 else f'DOWN (exit {daemon.returncode})'}")
    tunnel = run(["curl", "-fsS", "--max-time", "3", "https://android.kevlarscreations.com/healthz"])
    out.append(f"Tunnel /healthz: {tunnel.stdout.strip() if tunnel.returncode == 0 else f'DOWN (exit {tunnel.returncode})'}")
    hermes_api = run(["curl", "-fsS", "--max-time", "3", "http://127.0.0.1:8642/health"])
    out.append(f"Hermes API: {hermes_api.stdout.strip()[:100] if hermes_api.returncode == 0 else f'DOWN (exit {hermes_api.returncode})'}")

    # 4. Test counts
    out.append("\n=== Test counts ===")
    os.chdir("/home/kevin/.hermes/companion")
    pytest = run(["python3", "-m", "pytest", "tests/", "--tb=no", "-q"], timeout=60)
    last_line = pytest.stdout.strip().split("\n")[-1] if pytest.stdout else "(empty)"
    out.append(f"Daemon pytest: {last_line}")

    # 5. Deliverable
    apk = "/home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk"
    if os.path.exists(apk):
        out.append(f"\n=== Deliverable: {apk} EXISTS ({os.path.getsize(apk)} bytes) ===")
    else:
        out.append(f"\n=== Deliverable: {apk} NOT YET PRODUCED ===")

    # 6. Send to Discord
    report = "\n".join(out)
    print(report)

    # If all done + deliverable exists, send completion + remove self
    # Otherwise just deliver the status update
    result = run(["hermes", "kanban", "--board", BOARD, "list", "--json"])
    if result.returncode == 0:
        try:
            # Strip leading non-JSON line
            start = result.stdout.find("[")
            if start >= 0:
                tasks = json.loads(result.stdout[start:])
                all_done = all(t.get("status") == "done" for t in tasks)
                if all_done and os.path.exists(apk):
                    # Send completion, then self-delete
                    completion = f"✅ **companion-audit-v5 COMPLETE**\n\n{report}\n\nRemoving watchdog..."
                    run(["hermes", "send", "--to", DISCORD, "-q", completion])
                    # Self-delete this cron job
                    cron_list = run(["hermes", "cron", "list"])
                    for line in cron_list.stdout.split("\n"):
                        if "companion-audit-v5-watchdog" in line:
                            # Extract ID
                            import re
                            m = re.search(r"\[([a-f0-9]+)\]", line)
                            if m:
                                cron_id = m.group(1)
                                run(["hermes", "cron", "remove", cron_id])
                                print(f"\nWatchdog self-deleted: {cron_id}")
                    return
        except (json.JSONDecodeError, KeyError) as e:
            print(f"JSON parse error: {e}", file=sys.stderr)

    # Otherwise: send status update to Discord
    run(["hermes", "send", "--to", DISCORD, "-q", f"⏰ **companion-audit-v5 watchdog tick**\n\n{report}"])


if __name__ == "__main__":
    main()
