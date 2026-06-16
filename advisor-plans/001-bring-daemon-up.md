# Plan 001: Bring the companion daemon up — install systemd unit, fix package metadata, deploy

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/companion && git diff --stat d378902..HEAD -- server.py setup.py pyproject.toml systemd/`
> If any in-scope file changed since this plan was written, compare the excerpts below against live code before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: MED (changes production deploy; verify on a real phone)
- **Depends on**: none
- **Category**: bug (deployment)
- **Planned at**: commit `d378902` (daemon), `0a30d82` (Android), 2026-06-16
- **Recurrence note**: This exact failure (daemon not running, package uninstallable) was missed by 5 prior companion boards. The deploy audit that caught it (`DEPLOY-1`, `DEPLOY-2`) must be re-run after this plan lands to confirm the daemon stays up.

## Why this matters

The companion daemon is **not running** on the production system. Port 8777 is unbound, no systemd unit is installed, cloudflared tunnel is down, and `pip install -e .` fails on broken package metadata. The Android app — even with all 9 prior plan fixes applied — cannot reach Hermes through the companion because the companion itself is down. Every prior "the app works" claim was untested at runtime. The user asked for a "fresh end-to-end review to ensure it is full up to spec" — this plan is the literal floor of "full up to spec."

## Current state (verified 2026-06-16 by Atlas)

**Daemon process**: not running.
```
$ ps auxf | grep -iE "hermes|companion" | grep -v grep
(empty)
$ ss -tlnp | grep -E "8642|8777"
LISTEN 127.0.0.1:8642 (hermes, pid 1950232)   # Hermes API up
(no 8777 — companion down)
```

**Systemd units**: not installed in `/etc/systemd/system/`. Only `hermes-gateway.service` (user-level) is running.

**Two unit templates exist in repos, neither deployed**:
- `/home/kevin/.hermes/companion/src/hermes_companion/systemd/hermes-companion.service` (lines 1-15) — `ExecStart=/usr/bin/env python3 -m companion_cli serve` (assumes package install; broken because package doesn't install)
- `/home/kevin/.hermes/projects/HermesCompanion/hermes-companion.service` (lines 1-15) — `ExecStart=/opt/hermes-companion/server/venv/bin/python server.py` (assumes root script in `/opt/hermes-companion/server/`; that path doesn't exist on this host)

**Package install broken**:
```
$ cd /home/kevin/.hermes/companion && python3 -c "from setuptools import find_packages; print(find_packages(include=['server*']))"
[]
$ cd /home/kevin/.hermes/companion && pip install -e . --dry-run 2>&1 | tail -5
error: ... 'tool.setuptools.package-data' keys must be python-module-name
       (key "server" is invalid)
$ cd /home/kevin/.hermes/companion && python3 -c "import hermes_companion" 2>&1
ModuleNotFoundError: No module named 'hermes_companion'
```

**Cloudflared tunnel**: not running.
```
$ ps auxf | grep cloudflared | grep -v grep
(empty)
$ systemctl status cloudflared
Unit cloudflared.service could not be found.
```

Config at `~/.cloudflared/config.yml` is valid and routes `android.kevlarscreations.com → http://127.0.0.1:8777`, but no process is consuming it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Daemon repo dir | `cd /home/kevin/.hermes/companion` | shell prompt |
| Android repo dir | `cd /home/kevin/.hermes/projects/HermesCompanion` | shell prompt |
| Run daemon in foreground | `python3 server.py` | aiohttp serve log on :8777 |
| Health check | `curl -fsS http://127.0.0.1:8777/healthz` | `{"status":"ok"}` |
| Systemd status | `systemctl --user status hermes-companion` | `active (running)` |
| Cloudflared status | `systemctl --user status cloudflared` | `active (running)` |
| Tunnel reachability | `curl -fsS https://android.kevlarscreations.com/healthz` | `{"status":"ok"}` (after both above) |

## Scope

**In scope**:
- `~/.hermes/companion/setup.py` (line 19: `find_packages(include=["server*"])`)
- `~/.hermes/companion/pyproject.toml` (lines 28-29: `[tool.setuptools.packages.find] include = ["server*"]`, and `[tool.setuptools.package-data]` "server" key)
- `~/.hermes/companion/src/hermes_companion/systemd/hermes-companion.service` (modify ExecStart to match chosen deployment model)
- `~/.hermes/projects/HermesCompanion/hermes-companion.service` (modify ExecStart to match chosen deployment model — choose one canonical template)
- `~/.cloudflared/config.yml` (verify, do not edit; existing config is correct)

**Out of scope** (do NOT touch, even though they look related):
- The Android app's network_security_config — no change needed
- Hermes API config (port 8642) — running, no change
- The auth.json file at `~/.hermes/companion/auth.json` — has real credentials, do not regenerate
- The setup_wizard.py / config_schema.py modules — they work, just the package metadata and deploy unit are broken

## Git workflow

- Daemon repo branch: `advisor/001-bring-daemon-up`
- Commit per logical step
- Message style: imperative, scoped (`fix(setup): ...`, `feat(systemd): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Fix the package metadata in daemon repo

**Problem**: `setup.py:19` and `pyproject.toml:28-29` use `find_packages(include=["server*"])`, but the package directory is named `src/hermes_companion/`. The `server*` glob matches nothing. Additionally, `[tool.setuptools.package-data]` has `"server" = ["*.py"]` which is invalid (no `server` package).

**Fix**: Use `src/`-layout properly. Edit `~/.hermes/companion/pyproject.toml`:
- Replace `[tool.setuptools.packages.find]` block with:
  ```toml
  [tool.setuptools.packages.find]
  where = ["src"]
  include = ["hermes_companion*"]
  ```
- Replace `[tool.setuptools.package-data]` block with:
  ```toml
  [tool.setuptools.package-data]
  "hermes_companion" = ["*.py", "systemd/*.service", "templates/*"]
  ```

**Alternative (simpler, recommended)**: keep the root-layout and switch to `py_modules`. Edit `pyproject.toml`:
- Remove `[tool.setuptools.packages.find]` entirely
- Add `packages = ["hermes_companion"]` and `package-dir = {"" = "src"}` somewhere — but this requires moving modules under `src/hermes_companion/`. Not recommended.

**Recommended approach** (minimal change): in `pyproject.toml`, declare `src/hermes_companion` as a package explicitly and move the actual code in. If that's too invasive, just **delete the broken `[tool.setuptools.packages.find]` and `[tool.setuptools.package-data]` blocks** and add a single `packages = ["hermes_companion"]` with `package-dir = {"hermes_companion" = "src/hermes_companion"}`. Then `pip install -e .` creates an importable `hermes_companion` package from the empty `src/hermes_companion/__init__.py` (which is all that's there), and the root-level modules (`server.py`, etc.) are importable as a flat module via `py_modules` if needed. But the **primary** invocation path is `python3 server.py` (not `python3 -m hermes_companion.server`), so the package layout doesn't actually need to be importable for the daemon to run.

**Simplest correct fix**: 
1. In `setup.py`, change line 19 to `packages=[]` and add `py_modules=["server", "companion_cli", "config", "config_schema", "first_run", "setup_wizard"]`. This makes the root modules importable when installed.
2. In `pyproject.toml`, remove the broken `[tool.setuptools.packages.find]` and `[tool.setuptools.package-data]` sections entirely (or comment them out). Replace with `[tool.setuptools]` block that has just `py-modules = ["server", "companion_cli", "config", "config_schema", "first_run", "setup_wizard"]`.

**Verify**:
```bash
cd /home/kevin/.hermes/companion
python3 -c "import setuptools; print('OK')"   # parser doesn't reject
python3 setup.py sdist --dry-run 2>&1 | tail -5   # no error
```

### Step 2: Choose one canonical systemd unit template

The two templates conflict. Pick the daemon-repo one (since it has the canonical path) and fix its ExecStart.

**Edit** `~/.hermes/companion/src/hermes_companion/systemd/hermes-companion.service`:
- Replace `ExecStart=/usr/bin/env python3 -m companion_cli serve` with `ExecStart=/usr/bin/env python3 /home/kevin/.hermes/companion/companion_cli.py serve` (explicit path to the CLI entry, no module resolution needed)

**Edit** `~/.hermes/projects/HermesCompanion/hermes-companion.service`:
- Replace `ExecStart=/opt/hermes-companion/server/venv/bin/python server.py` with `ExecStart=/usr/bin/env python3 /home/kevin/.hermes/companion/server.py` (match the daemon repo's working dir)
- Update `WorkingDirectory=` to `/home/kevin/.hermes/companion`
- Update `EnvironmentFile=` to `/home/kevin/.hermes/companion/.env` if one exists, or remove

**Keep both templates in sync** — both point to the same daemon installation. Mark the Android repo's template as deprecated by adding `# DEPRECATED: use the daemon repo template at src/hermes_companion/systemd/hermes-companion.service` at the top of the Android-repo file.

### Step 3: Install the unit as a user-level systemd service

```bash
mkdir -p ~/.config/systemd/user/
cp ~/.hermes/companion/src/hermes_companion/systemd/hermes-companion.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable hermes-companion.service
systemctl --user start hermes-companion.service
sleep 2
systemctl --user status hermes-companion.service
```

**Expected**: `active (running)`.

**Verify daemon is listening**:
```bash
ss -tlnp | grep 8777
# Expected: LISTEN 0 128 127.0.0.1:8777 ...
curl -fsS http://127.0.0.1:8777/healthz
# Expected: {"status":"ok"}
```

### Step 4: Start cloudflared tunnel

```bash
# Cloudflared is already installed (per memory). Verify:
which cloudflared

# Run via the existing user-level systemd pattern (matches hermes-gateway)
# Create the service file if not present:
cat > ~/.config/systemd/user/cloudflared.service <<'EOF'
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/env cloudflared tunnel run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable cloudflared.service
systemctl --user start cloudflared.service
sleep 5
systemctl --user status cloudflared.service
```

**Verify tunnel is up**:
```bash
curl -fsS https://android.kevlarscreations.com/healthz
# Expected: {"status":"ok"} — same response as 127.0.0.1:8777/healthz
```

### Step 5: Verify end-to-end from outside

```bash
# Test with the auth the Android app uses
curl -fsS -u "kevin:Kevi667n!1991!" https://android.kevlarscreations.com/api/kanban/boards | head -3
# Expected: JSON list of boards (or auth-error JSON if password differs)
```

## Test plan

No new automated tests. The verification is: the existing `pytest tests/` in daemon repo (56 passed, 49 skipped) + manual end-to-end `curl` from a remote client through the tunnel. The Android app on the phone is the ultimate smoke test.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/companion && python3 setup.py sdist --dry-run 2>&1` exits 0
- [ ] `systemctl --user status hermes-companion.service` shows `active (running)`
- [ ] `ss -tlnp | grep 8777` shows `LISTEN` on 127.0.0.1:8777
- [ ] `curl -fsS http://127.0.0.1:8777/healthz` returns `{"status":"ok"}`
- [ ] `systemctl --user status cloudflared.service` shows `active (running)`
- [ ] `curl -fsS https://android.kevlarscreations.com/healthz` returns the same `{"status":"ok"}` (proves tunnel + daemon + upstream all wired)
- [ ] No files outside the in-scope list are modified (besides the canonical unit template in daemon repo)
- [ ] `git status` is clean in both repos (commit the fixes)
- [ ] `plans/README.md` (this file) status row updated to DONE

## STOP conditions

Stop and report back (do not improvise) if:
- The setup.py/pyproject.toml changes break `pytest tests/` (run that BEFORE installing the unit)
- The systemd unit fails to start with a Python ImportError — the package install fix from Step 1 may not be enough; report the exact error
- The cloudflared config at `~/.cloudflared/config.yml` is missing the companion tunnel — stop and ask the user before creating a new tunnel
- The phone cannot reach the daemon even after all four services are up — that's a network/firewall issue, not a code issue; report and stop

## Maintenance notes

- After this plan lands, **the daemon must be brought up on every reboot**. Since we're using `systemctl --user`, that requires `loginctl enable-linger kevin` to keep the user systemd active without an active session. Add this to the systemd unit as `WantedBy=default.target` (already there) AND enable lingering with `sudo loginctl enable-linger kevin`.
- Future daemon updates: rebuild and `systemctl --user restart hermes-companion.service`. No rebuild of systemd unit needed unless ExecStart path changes.
- The package metadata fix is forward-looking: if someone later wants to `pip install -e .` to develop, the metadata will work. The current deployment doesn't use pip install — it just `python3 server.py`s directly. So this fix is a hygiene fix, not the blocker for the daemon coming up. The real blocker is Step 3 (install the unit) and Step 4 (start cloudflared).
