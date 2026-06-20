# Context — Hermes Companion

## Domain vocabulary

- **Companion** — the mobile app + daemon system that lets users interact with Hermes Agent from their phone.
- **Daemon** — the Python aiohttp server (`server.py`) running on the host machine. Proxies requests between the Android app and the Hermes Agent API.
- **Session** — a Hermes Agent conversation thread. Has an ID, title, and message history. Sessions are created/managed via the Hermes API.
- **Kanban Task** — a work item on a Hermes Kanban board. Has status (triage, todo, ready, running, blocked, review, done), priority, assignee, title, body, comments, dependencies.
- **Board** — a named kanban board (slug-based). Contains tasks organized by status.
- **Attachment** — a file uploaded through the daemon's `/api/attachments` endpoint. Has an ID, filename, content type, size.
- **Setup Token** — a one-time-use token for first-time device pairing via QR code. 5-minute TTL. Redeemed at `/api/setup/redeem`.
- **HermesProxy** — the daemon's forwarding class that injects Bearer auth and proxies requests to the Hermes API at `http://127.0.0.1:8642`.
- **BasicAuth** — the daemon's authentication middleware. Reads scrypt-hashed credentials from `~/.hermes/companion/auth.json`.
- **EncryptedSharedPreferences** — Android's secure credential storage. Fail-closed: if unavailable, credentials are NOT stored in plaintext.
