#!/usr/bin/env python3
"""First-run setup wizard for Hermes Companion server.

Usage: hermes-companion setup

Interactively configures config.yaml, auth.json, and attachments directory.
Optionally generates a QR code for mobile app connection.
"""

import argparse
import base64
import hashlib
import json
import os
import pwd
import secrets
import sys
from pathlib import Path

# ── Password hashing ───────────────────────────────────────────

def generate_password(length: int = 32) -> str:
    """Generate a secure random password."""
    return secrets.token_urlsafe(length)


def hash_password(password: str, n: int = 16384, r: int = 8, p: int = 1) -> str:
    """Hash password with scrypt. Returns scrypt$N$r$p$salt_hex$hash_b64."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${base64.b64encode(dk).decode()}"


# ── Auth.json ──────────────────────────────────────────────────

def create_auth_json(dest: Path, username: str, password: str) -> Path:
    """Create auth.json with a scrypt-hashed password.

    Args:
        dest: Path to write auth.json
        username: Admin username
        password: Plaintext password (will be hashed)

    Returns:
        Path to created auth.json
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    auth_data = {
        "users": {
            username: {
                "password_hash": hash_password(password),
                "created_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
            }
        },
        "note": "Format: scrypt$N$r$p$<salt-hex>$<hash-b64>",
    }
    dest.write_text(json.dumps(auth_data, indent=2) + "\n")
    # Restrict permissions
    dest.chmod(0o600)
    return dest


# ── Hermes binary detection (reuse logic from config.py) ───────

def detect_hermes_binary() -> str:
    """Detect hermes binary path. Returns path string."""
    import shutil

    # 1) PATH lookup
    found = shutil.which("hermes")
    if found:
        return found

    # 2) Fallback paths
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    fallbacks = [
        Path(real_home) / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
        Path("/opt/hermes/bin/hermes"),
    ]
    for p in fallbacks:
        if p.is_file():
            return str(p)

    return "/usr/local/bin/hermes"


# ── Config.yaml generation ─────────────────────────────────────

def create_config_yaml(
    dest: Path,
    server_host: str,
    server_port: int,
    hermes_api_url: str,
    hermes_api_key: str,
    hermes_binary_path: str,
    auth_file_path: str,
    attachments_dir: str,
) -> Path:
    """Create config.yaml with the given values.

    Args:
        dest: Path to write config.yaml
        server_host: Server bind host
        server_port: Server bind port
        hermes_api_url: Hermes API URL
        hermes_api_key: Hermes API key (may be empty)
        hermes_binary_path: Path to hermes binary
        auth_file_path: Path to auth.json
        attachments_dir: Path to attachments directory

    Returns:
        Path to created config.yaml
    """
    import yaml

    dest.parent.mkdir(parents=True, exist_ok=True)

    config_data = {
        "server": {
            "host": server_host,
            "port": server_port,
        },
        "hermes": {
            "api_url": hermes_api_url,
            "api_key": hermes_api_key,
            "binary_path": hermes_binary_path,
        },
        "auth": {
            "file_path": auth_file_path,
        },
        "attachments": {
            "dir": attachments_dir,
            "max_upload_mb": 25,
        },
    }

    with open(dest, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    return dest


# ── QR Code generation ─────────────────────────────────────────

def generate_connection_uri(
    server_url: str,
    username: str,
    password: str,
    board: str = "default",
) -> str:
    """Encode connection parameters into a hermescompanion:// URI."""
    return (
        f"hermescompanion://configure"
        f"?url={server_url}"
        f"&user={username}"
        f"&pass={password}"
        f"&board={board}"
    )


def generate_qr_code(uri: str, dest_dir: Path) -> tuple[str, Path]:
    """Generate QR code as ASCII art and PNG file.

    Args:
        uri: Connection URI to encode
        dest_dir: Directory to save PNG file

    Returns:
        Tuple of (ascii_art_string, png_path)
    """
    import segno

    qr = segno.make(uri, error="M")

    # ASCII art
    ascii_art = qr.terminal()

    # PNG file
    dest_dir.mkdir(parents=True, exist_ok=True)
    png_path = dest_dir / "setup-qr.png"
    qr.save(str(png_path))

    return ascii_art, png_path


def generate_qr_code_no_segno(uri: str, dest_dir: Path) -> tuple[str, Path | None]:
    """Fallback QR generation without segno — ASCII only using unicode blocks.

    Returns:
        Tuple (ascii_repr, None) when segno is unavailable.
    """
    # Just return the URI as text if segno is not available
    lines = [
        "",
        "  Connection URI (scan with mobile app):",
        f"  {uri}",
        "",
        "  Install 'segno' pip package for QR code generation:",
        "    pip install segno",
        "",
    ]
    return "\n".join(lines), None


# ── Interactive prompt helpers ─────────────────────────────────

def prompt(text: str, default: str = "") -> str:
    """Prompt user with a default value. Returns user input or default."""
    if default:
        display = f"  [{default}]"
    else:
        display = ""
    result = input(f"  {text}{display}: ").strip()
    return result if result else default


def prompt_password() -> str:
    """Generate and display a random password, allow override."""
    pw = generate_password(32)
    print(f"\n  Generated admin password: {pw}")
    print("  (Press Enter to accept, or type a custom password)")
    custom = input("  Password: ").strip()
    return custom if custom else pw


def prompt_yes_no(text: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    result = input(f"  {text}{suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


# ── Main wizard ────────────────────────────────────────────────

def run_wizard(
    config_dir: Path | None = None,
    username: str = "admin",
    board: str = "default",
    skip_qr: bool = False,
) -> dict:
    """Run the interactive setup wizard.

    Args:
        config_dir: Directory for config.yaml (default: ~/.config/hermes-companion)
        username: Admin username (default: admin)
        board: Default kanban board name (default: default)
        skip_qr: Skip QR code generation

    Returns:
        dict with keys: config_path, auth_path, password, server_url, qr_png_path
    """
    if config_dir is None:
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        config_dir = Path(real_home) / ".config" / "hermes-companion"

    config_path = config_dir / "config.yaml"

    print()
    print("=" * 60)
    print("  Hermes Companion — First-Run Setup Wizard")
    print("=" * 60)
    print()
    print("  This wizard will create the configuration files needed")
    print("  to run the Hermes Companion server.")
    print()

    print("── Server Settings ──")
    print()
    server_host = prompt("Server bind host", "127.0.0.1")
    server_port_str = prompt("Server bind port", "8777")
    try:
        server_port = int(server_port_str)
    except ValueError:
        print(f"  WARNING: Invalid port '{server_port_str}', using 8777", file=sys.stderr)
        server_port = 8777

    print()
    print("── Hermes API Settings ──")
    print()
    hermes_api_url = prompt("Hermes API URL", "http://127.0.0.1:8642")
    hermes_api_key = prompt("Hermes API key (leave empty if not required)", "")

    print()
    print("── Hermes Binary Detection ──")
    print()
    detected_binary = detect_hermes_binary()
    print(f"  Detected Hermes binary: {detected_binary}")
    hermes_binary = prompt(f"Hermes binary path", detected_binary)

    print()
    print("── Authentication ──")
    print()
    username = prompt("Admin username", username)
    password = prompt_password()

    print()
    print("── File Locations ──")
    print()
    auth_file_default = str(config_dir / "auth.json").replace(
        pwd.getpwuid(os.getuid()).pw_dir, "~"
    )
    auth_file_path = prompt("Auth file path", auth_file_default)

    # Expand auth path for actual use
    auth_file_path_expanded = Path(auth_file_path.replace("~", pwd.getpwuid(os.getuid()).pw_dir))
    auth_path = auth_file_path_expanded

    attachments_default = str(config_dir / "attachments").replace(
        pwd.getpwuid(os.getuid()).pw_dir, "~"
    )
    attachments_dir = prompt("Attachments directory", attachments_default)

    print()
    print("── Creating files ──")
    print()

    # Create config.yaml
    create_config_yaml(
        dest=config_path,
        server_host=server_host,
        server_port=server_port,
        hermes_api_url=hermes_api_url,
        hermes_api_key=hermes_api_key,
        hermes_binary_path=hermes_binary,
        auth_file_path=auth_file_path,
        attachments_dir=attachments_dir,
    )
    print(f"  Config:     {config_path}")

    # Create auth.json
    create_auth_json(auth_path, username, password)
    print(f"  Auth:       {auth_path}")

    # Create attachments directory
    attachments_expanded = Path(attachments_dir.replace("~", pwd.getpwuid(os.getuid()).pw_dir))
    attachments_expanded.mkdir(parents=True, exist_ok=True)
    print(f"  Attachments: {attachments_expanded}")

    # Build server URL for mobile
    if server_host in ("0.0.0.0", "::"):
        # For display, use localhost
        display_host = "127.0.0.1"
    else:
        display_host = server_host
    server_url = f"http://{display_host}:{server_port}"

    # QR code
    qr_png_path = None
    if not skip_qr:
        print()
        print("── Mobile App Setup ──")
        print()
        uri = generate_connection_uri(server_url, username, password, board)
        print(f"  Connection URI: {uri}")
        print()

        try:
            import segno  # noqa: F401
            qr_ascii, qr_png_path = generate_qr_code(uri, config_dir)
            print("  QR Code:")
            print()
            # Indent QR art
            for line in qr_ascii.splitlines():
                print(f"  {line}")
            print()
            print(f"  QR code saved to: {qr_png_path}")
        except ImportError:
            qr_text, _ = generate_qr_code_no_segno(uri, config_dir)
            print(qr_text)

    else:
        uri = generate_connection_uri(server_url, username, password, board)

    # Summary
    print()
    print("=" * 60)
    print("  Setup Complete!")
    print("=" * 60)
    print()
    print(f"  Server URL:  {server_url}")
    print(f"  Username:    {username}")
    print(f"  Password:    {password}")
    print(f"  Board:       {board}")
    print()
    print(f"  Config file: {config_path}")
    print(f"  Auth file:   {auth_path}")
    print()
    print("  Start the server:")
    print("    hermes-companion serve")
    print()
    print("  Or with systemd:")
    print("    hermes-companion install-service")
    print("    systemctl --user enable --now hermes-companion")
    print()

    return {
        "config_path": config_path,
        "auth_path": auth_path,
        "password": password,
        "username": username,
        "server_url": server_url,
        "qr_png_path": qr_png_path,
    }


# ── CLI entry point ────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes-companion setup",
        description="First-run setup wizard for Hermes Companion server",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Configuration directory (default: ~/.config/hermes-companion)",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Admin username (default: admin)",
    )
    parser.add_argument(
        "--board",
        default="default",
        help="Default kanban board name (default: default)",
    )
    parser.add_argument(
        "--no-qr",
        action="store_true",
        help="Skip QR code generation",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accept all defaults without prompting (for automated setup)",
    )

    args = parser.parse_args(argv)

    if args.non_interactive:
        # Non-interactive mode: accept all defaults
        return _non_interactive_setup(
            config_dir=args.config_dir,
            username=args.username,
            board=args.board,
            skip_qr=args.no_qr,
        )

    try:
        run_wizard(
            config_dir=args.config_dir,
            username=args.username,
            board=args.board,
            skip_qr=args.no_qr,
        )
        return 0
    except KeyboardInterrupt:
        print("\n  Setup cancelled.")
        return 130
    except EOFError:
        print("\n  No input available. Use --non-interactive for automated setup.")
        return 1


def _non_interactive_setup(
    config_dir: Path | None,
    username: str,
    board: str,
    skip_qr: bool,
) -> int:
    """Run setup with all defaults, no prompts."""
    import yaml

    if config_dir is None:
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        config_dir = Path(real_home) / ".config" / "hermes-companion"

    config_path = config_dir / "config.yaml"
    auth_path = config_dir / "auth.json"
    attachments_dir = config_dir / "attachments"

    server_host = "127.0.0.1"
    server_port = 8777
    hermes_api_url = "http://127.0.0.1:8642"
    hermes_api_key = os.environ.get("HERMES_API_KEY", os.environ.get("API_SERVER_KEY", ""))
    hermes_binary = detect_hermes_binary()
    password = generate_password(32)

    config_dir.mkdir(parents=True, exist_ok=True)

    create_config_yaml(
        dest=config_path,
        server_host=server_host,
        server_port=server_port,
        hermes_api_url=hermes_api_url,
        hermes_api_key=hermes_api_key,
        hermes_binary_path=hermes_binary,
        auth_file_path=str(auth_path),
        attachments_dir=str(attachments_dir),
    )

    create_auth_json(auth_path, username, password)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {config_path}")
    print(f"Auth:   {auth_path}")
    print(f"User:   {username}")
    print(f"Pass:   {password}")
    print(f"URL:    http://{server_host}:{server_port}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
