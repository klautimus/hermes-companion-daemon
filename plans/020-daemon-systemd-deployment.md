# Plan 020: Daemon Systemd Deployment

> **Executor**: This is a deployment task, not a code change.

## Status
- **Priority**: P0 | **Effort**: S | **Risk**: LOW | **Depends on**: nothing | **Category**: dx
- **Planned at**: commit `1b22699`, 2026-06-19

## Why this matters
The daemon is NOT running. Port 8777 is unbound. No systemd unit exists. The app cannot function at all without the daemon. This was the #1 finding of companion-audit-v5 and is STILL the case.

## Current state
- No systemd unit installed for the companion daemon
- No process running on port 8777
- Cloudflared tunnel status unknown
- Daemon code is complete and committed

## Steps

### Step 1: Create systemd user unit
Create `~/.config/systemd/user/hermes-companion.service`:
```ini
[Unit]
Description=Hermes Companion Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/kevin/.hermes/companion
ExecStart=/home/kevin/.hermes/hermes-agent/venv/bin/python3 /home/kevin/.hermes/companion/server.py
Restart=on-failure
RestartSec=5
Environment=HERMES_API=http://127.0.0.1:8642
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

### Step 2: Enable and start
```bash
systemctl --user daemon-reload
systemctl --user enable hermes-companion
systemctl --user start hermes-companion
```

### Step 3: Verify
```bash
systemctl --user status hermes-companion
ss -tlnp | grep 8777
curl -fsS http://127.0.0.1:8777/healthz
curl -fsS https://android.kevlarscreations.com/healthz
```

### Step 4: Verify cloudflared tunnel
If tunnel is down, restart cloudflared:
```bash
systemctl --user status cloudflared
# If down:
systemctl --user restart cloudflared
```

## Done criteria
- [ ] `systemctl --user status hermes-companion` shows active (running)
- [ ] `ss -tlnp | grep 8777` shows the port bound
- [ ] `curl http://127.0.0.1:8777/healthz` returns `{"status":"ok"}`
- [ ] `curl https://android.kevlarscreations.com/healthz` returns ok
- [ ] `git status` clean
