# Hermes Companion Daemon

An HTTP proxy server that bridges the [Hermes Companion Android app](https://github.com/klautimus/hermes-companion) with the [Hermes Agent](https://github.com/nousresearch/hermes-agent) API. Provides authenticated access to chat sessions, kanban board management, file attachments, and email 2FA.

## Quick Start

### Option 1: One-line Install (systemd)

```bash
curl -fsSL https://raw.githubusercontent.com/klautimus/hermes-companion-daemon/main/install.sh | bash
```

### Option 2: pip

```bash
pip install hermes-companion-server
hermes-companion setup    # interactive first-run wizard
hermes-companion serve    # start the server
```

### Option 3: Docker

```bash
docker run -d \
  --name hermes-companion \
  --restart unless-stopped \
  -p 8777:8777 \
  -e HERMES_API_URL=http://host.docker.internal:8642 \
  -v companion-data:/data \
  ghcr.io/klautimus/hermes-companion-daemon
```

Or with docker-compose:

```yaml
# docker-compose.yml
services:
  companion:
    build: https://github.com/klautimus/hermes-companion-daemon.git#main
    ports: ["8777:8777"]
    environment:
      - HERMES_API_URL=http://host.docker.internal:8642
    volumes:
      - companion-data:/data
    restart: unless-stopped
```

## Prerequisites

- **Python 3.10+** (for pip/systemd install)
- **Hermes Agent** running on the same machine (provides the API on port 8642 and the `hermes` CLI for kanban operations)
- The `hermes` binary must be on your `PATH` (or set `HERMES_BIN` in config)

## Configuration

The daemon reads configuration from `~/.hermes/companion/config.yaml`:

```yaml
server:
  host: 127.0.0.1    # set to 0.0.0.0 for Docker/remote access
  port: 8777

hermes:
  api_url: http://127.0.0.1:8642
  api_key: ""          # your Hermes API server key
  cli_path: auto       # auto-detect hermes binary, or set explicit path
```

Environment variable overrides:
| Variable | Description |
|----------|-------------|
| `COMPANION_HOST` | Override server bind address |
| `COMPANION_PORT` | Override server port |
| `HERMES_API_URL` | Override Hermes API URL |
| `HERMES_API` | Same as above (alias) |

## Authentication

The daemon uses HTTP Basic Auth with scrypt-hashed credentials stored in `auth.json`.

**First-run setup:** Run `hermes-companion setup` to create credentials via the interactive wizard, or use the setup token flow from the Android app's QR scanner.

**Email 2FA:** Optional two-factor authentication via Gmail API. Configure the Gmail OAuth token path in the config to enable.

## API Reference

### Sessions (Chat)
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/sessions` | List chat sessions |
| `POST` | `/api/sessions` | Create a new session |
| `GET` | `/api/sessions/{id}` | Get session details |
| `GET` | `/api/sessions/{id}/messages` | Get messages in a session |
| `DELETE` | `/api/sessions/{id}` | Delete a session |
| `POST` | `/v1/chat/completions` | Send a chat message (proxied) |
| `POST` | `/v1/chat/completions/stream` | Send a chat message (SSE streaming) |

### Kanban
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/kanban/boards` | List all boards |
| `POST` | `/api/kanban/boards` | Create a board |
| `GET` | `/api/kanban/tasks?board=<slug>` | List tasks on a board |
| `GET` | `/api/kanban/tasks/{id}` | Show task details |
| `PATCH` | `/api/kanban/tasks/{id}` | Update task (status, title, priority, assignee) |
| `POST` | `/api/kanban/tasks` | Create a task |
| `DELETE` | `/api/kanban/tasks/{id}` | Delete a task |
| `POST` | `/api/kanban/tasks/{id}/complete` | Mark task done |
| `POST` | `/api/kanban/tasks/{id}/block` | Block a task |
| `POST` | `/api/kanban/tasks/{id}/unblock` | Unblock a task |
| `POST` | `/api/kanban/tasks/{id}/archive` | Archive a task |
| `POST` | `/api/kanban/tasks/{id}/reclaim` | Reclaim an archived task |
| `POST` | `/api/kanban/tasks/{id}/comment` | Add a comment |
| `POST` | `/api/kanban/tasks/{id}/assign` | Assign to a profile |
| `POST` | `/api/kanban/tasks/{id}/decompose` | Decompose into subtasks |
| `POST` | `/api/kanban/tasks/bulk` | Bulk update tasks |
| `POST` | `/api/kanban/links` | Link parent → child dependency |
| `GET` | `/api/kanban/profiles` | List available worker profiles |
| `GET` | `/api/kanban/stats?board=<slug>` | Get board statistics |

### Attachments
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/attachments` | Upload a file attachment |
| `GET` | `/api/attachments/{id}` | Download an attachment |

### Auth & 2FA
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/2fa/check` | Check if 2FA is required |
| `POST` | `/api/auth/2fa/verify` | Verify a 2FA code |
| `POST` | `/api/auth/2fa/setup` | Enable 2FA |
| `POST` | `/api/auth/2fa/disable` | Disable 2FA |
| `POST` | `/api/auth/2fa/resend` | Resend 2FA code |

### Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/healthz` | Health check (returns status + Hermes API reachability) |

## systemd Management

```bash
# Start/stop/restart
systemctl --user start hermes-companion
systemctl --user stop hermes-companion
systemctl --user restart hermes-companion

# View logs
journalctl --user -u hermes-companion -f

# Enable on boot
systemctl --user enable hermes-companion
```

## Development

```bash
# Clone
git clone https://github.com/klautimus/hermes-companion-daemon.git
cd hermes-companion-daemon

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run the server directly
python server.py
```

## Architecture

```
Android App (Kotlin)
       │
       ▼
Companion Daemon (this repo)      ──── HTTP proxy ────►  Hermes API (port 8642)
  • Basic Auth + 2FA                                        • Chat sessions
  • Kanban CLI wrapper                                      • Message history
  • Attachment storage                                      • Token generation
  • Port 8777
       │
       ▼
  hermes kanban CLI  ────►  Kanban DB (~/.hermes/kanban.db)
```

The daemon acts as a trusted intermediary: it holds credentials server-side, wraps the `hermes kanban` CLI for board operations, and proxies chat requests to the Hermes API server. The Android app never touches Hermes directly.

## License

MIT
