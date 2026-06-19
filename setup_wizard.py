#!/usr/bin/env python3
"""Interactive setup wizard for Hermes Companion server.

Run with: hermes-companion setup
"""

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

# Add server directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    # When run as module (python -m server.setup_wizard)
    from .config_schema import (
        CompanionConfig,
        DEFAULT_CONFIG,
        save_config,
        CONFIG_DIR,
        CONFIG_FILE,
    )
    from .first_run import check_first_run
except ImportError:
    # When run as script (python setup_wizard.py)
    from config_schema import (
        CompanionConfig,
        DEFAULT_CONFIG,
        save_config,
        CONFIG_DIR,
        CONFIG_FILE,
    )
    from first_run import check_first_run


# ── Constants ──────────────────────────────────────────────────
SCRYPT_N = 16384  # was 131072; matches daemon's working value
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


# ── Helpers ────────────────────────────────────────────────────

def detect_hermes_cli() -> Path | None:
    """Auto-detect Hermes CLI binary.

    Checks common locations in order:
    1. ~/.hermes/hermes-agent/venv/bin/hermes
    2. ~/.local/bin/hermes
    3. /usr/local/bin/hermes
    4. Any hermes in PATH
    """
    candidates = [
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    # Check PATH
    hermes_in_path = shutil.which("hermes")
    if hermes_in_path:
        return Path(hermes_in_path)

    return None


def prompt_with_default(prompt: str, default: str, required: bool = False) -> str:
    """Prompt user for input with a default value."""
    while True:
        if default:
            user_input = input(f"{prompt} [{default}]: ").strip()
        else:
            user_input = input(f"{prompt}: ").strip()

        if user_input:
            return user_input
        if default:
            return default
        if required:
            print("  This field is required.")
            continue
        return ""


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt user for yes/no."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        response = input(f"{prompt}{suffix}").strip().lower()
        if not response:
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("  Please answer y or n.")


def generate_password() -> str:
    """Generate a secure random password."""
    return secrets.token_urlsafe(32)


def generate_setup_token() -> str:
    """Generate a single-use, 5-minute-TTL setup token.

    Returned in the QR code URI instead of the plaintext password. The mobile
    app redeems it via POST /api/setup/redeem to fetch the actual credentials.
    """
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    """Hash password with scrypt (same format as server.py)."""
    import base64
    import hashlib

    salt = secrets.token_bytes(16)
    hash_bytes = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    b64hash = base64.b64encode(hash_bytes).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${b64hash}"


def create_auth_file(config: CompanionConfig, username: str, password: str) -> Path:
    """Create auth.json with scrypt-hashed password."""
    import json

    auth_data = {
        "users": {
            username: {
                "password_hash": hash_password(password),
                "created_at": "2026-01-01",
            }
        },
    }

    paths = config.get_expanded_paths()
    auth_file = paths["auth_file"]
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(auth_data, indent=2))
    auth_file.chmod(0o600)
    return auth_file


def create_attachments_dir(config: CompanionConfig) -> Path:
    """Create attachments directory."""
    paths = config.get_expanded_paths()
    attachments_dir = paths["attachments_dir"]
    attachments_dir.mkdir(parents=True, exist_ok=True)
    return attachments_dir


def generate_qr_code(config: CompanionConfig, username: str, token: str) -> str:
    """Generate QR code data URI for mobile app config.

    Uses a one-time setup token instead of plaintext password. The mobile
    app redeems the token via POST /api/setup/redeem to fetch credentials.
    """
    server_url = f"http://{config.server.host}:{config.server.port}"
    board = "default"

    import urllib.parse
    params = {
        "url": server_url,
        "user": username,
        "token": token,
        "board": board,
    }
    query = urllib.parse.urlencode(params)
    return f"hermescompanion://configure?{query}"


def render_qr_ascii(qr_data: str) -> str:
    """Render QR code as ASCII art for terminal display."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)

        # Get matrix and render as ASCII
        matrix = qr.get_matrix()
        lines = []
        for row in matrix:
            line = "".join("██" if cell else "  " for cell in row)
            lines.append(line)
        return "\n".join(lines)
    except ImportError:
        return "[QR code generation requires 'qrcode' package]"


def save_qr_png(qr_data: str, config: CompanionConfig) -> Path | None:
    """Save QR code as PNG to config directory."""
    try:
        import qrcode
        from qrcode.image.pil import PilImage

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        img = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")

        paths = config.get_expanded_paths()
        config_dir = paths["config_dir"]
        qr_path = config_dir / "setup_qr.png"
        img.save(qr_path)
        return qr_path
    except ImportError:
        return None


def print_connection_info(config: CompanionConfig, username: str, password: str) -> None:
    """Print connection info for mobile app setup."""
    server_url = f"http://{config.server.host}:{config.server.port}"
    print("\n" + "=" * 60)
    print("SETUP COMPLETE — Mobile App Configuration")
    print("=" * 60)
    print(f"\nServer URL:  {server_url}")
    print(f"Username:    {username}")
    print("Password:    (transferred via secure QR token — check your mobile app)")
    print(f"Board:       default")
    print("\nOpen the Hermes Companion app on your Android device")
    print("and enter these credentials.")
    print("=" * 60)


def register_setup_token_wizard(token: str, username: str, password: str, config: CompanionConfig):
    """Write the setup token to a file the daemon will read on startup."""
    import json
    from datetime import datetime, timezone

    paths = config.get_expanded_paths()
    config_dir = paths["config_dir"]
    config_dir.mkdir(parents=True, exist_ok=True)
    token_file = config_dir / "setup_token.json"
    token_file.write_text(json.dumps({
        "tokens": [{
            "token": token,
            "username": username,
            "password": password,
            "board": "default",
            "host": config.server.host,
            "port": config.server.port,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    }, indent=2))
    token_file.chmod(0o600)


def run_setup_wizard() -> int:
    """Run the interactive setup wizard. Returns exit code."""
    try:
        return _run_setup_wizard()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        return 130
    except EOFError:
        print("\nEOF received, aborting.")
        return 1


def _run_setup_wizard() -> int:
    """Internal implementation of the setup wizard."""
    print("\n" + "=" * 60)
    print("Hermes Companion — First-Run Setup Wizard")
    print("=" * 60)

    # Check if already configured
    if not check_first_run():
        print(f"\nConfig already exists at {CONFIG_FILE}")
        if not prompt_yes_no("Re-run setup and overwrite?", default=False):
            print("Setup cancelled.")
            return 0
        print()

    # 1. Detect Hermes CLI
    print("🔍 Detecting Hermes CLI...")
    hermes_cli = detect_hermes_cli()
    if hermes_cli:
        print(f"   Found: {hermes_cli}")
        cli_path = str(hermes_cli)
    else:
        print("   Not found automatically.")
        cli_path = prompt_with_default(
            "Path to hermes binary",
            "auto",
            required=False,
        )
        if cli_path.lower() == "auto":
            cli_path = "auto"

    # 2. Server host/port
    print("\n🌐 Server Configuration")
    host = prompt_with_default("Bind address", DEFAULT_CONFIG.server.host)
    port = prompt_with_default("Port", str(DEFAULT_CONFIG.server.port))
    try:
        port = int(port)
    except ValueError:
        print(f"  Invalid port, using default {DEFAULT_CONFIG.server.port}")
        port = DEFAULT_CONFIG.server.port

    # 3. Hermes API
    print("\n🤖 Hermes API Configuration")
    api_url = prompt_with_default("Hermes API URL", DEFAULT_CONFIG.hermes.api_url)

    # API key - check env first
    env_api_key = os.getenv("API_SERVER_KEY", "")
    if env_api_key:
        print(f"   Using API_SERVER_KEY from environment")
        api_key = env_api_key
    else:
        api_key = prompt_with_default("Hermes API Key", "", required=True)

    # 4. Admin user
    print("\n👤 Admin User")
    username = prompt_with_default("Username", "admin")

    # Generate random password
    password = generate_password()
    print("   Password generated (transferred via QR token)")

    # 5. Build config
    config = CompanionConfig()
    config.server.host = host
    config.server.port = port
    config.hermes.api_url = api_url
    config.hermes.api_key = api_key
    config.hermes.cli_path = cli_path

    # 6. Save config
    print("\n💾 Saving configuration...")
    save_config(config)
    print(f"   Config saved to: {CONFIG_FILE}")

    # 7. Create auth.json
    print("🔐 Creating auth.json...")
    auth_file = create_auth_file(config, username, password)
    print(f"   Auth saved to: {auth_file}")

    # 8. Create attachments directory
    print("📁 Creating attachments directory...")
    attachments_dir = create_attachments_dir(config)
    print(f"   Directory: {attachments_dir}")

    # 9. Generate QR code with one-time setup token
    print("\n📱 Generating QR code for mobile app...")
    token = generate_setup_token()
    register_setup_token_wizard(token, username, password, config)
    qr_data = generate_qr_code(config, username, token)
    qr_ascii = render_qr_ascii(qr_data)
    print("\nScan with Hermes Companion app:")
    print(qr_ascii)

    qr_png = save_qr_png(qr_data, config)
    if qr_png:
        print(f"\nQR code saved as PNG: {qr_png}")

    # 10. Print connection info
    print_connection_info(config, username, password)

    print("\n✅ Setup complete! Start the server with:")
    print("   hermes-companion serve")
    return 0


if __name__ == "__main__":
    sys.exit(run_setup_wizard())