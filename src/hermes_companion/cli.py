#!/usr/bin/env python3
"""CLI entry point for hermes-companion.

Subcommands:
  setup    — First-run setup wizard
  serve    — Start the companion server
  qr       — Generate connection QR code
  install-service — Install systemd user service file
"""

import argparse
import sys
from pathlib import Path


def cmd_setup(argv: list[str]) -> int:
    from hermes_companion.setup_wizard import main as wizard_main
    return wizard_main(argv)


def cmd_serve(_argv: list[str]) -> int:
    from hermes_companion.server import main as server_main
    server_main()
    return 0


def cmd_qr(argv: list[str]) -> int:
    import os
    from pathlib import Path
    from hermes_companion.setup_wizard import (
        generate_connection_uri,
        generate_qr_code,
        generate_qr_code_no_segno,
    )
    from hermes_companion.config import load_config

    parser = argparse.ArgumentParser(prog="hermes-companion qr")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--board", default="default")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    config = load_config()
    host = config["server"]["host"]
    port = config["server"]["port"]
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = f"http://{host}:{port}"

    uri = generate_connection_uri(url, args.username, "", args.board)
    print(f"URI: {uri}")

    try:
        import segno  # noqa: F401
        qr_ascii, qr_png = generate_qr_code(uri, args.output or Path("."))
        for line in qr_ascii.splitlines():
            print(line)
        if qr_png:
            print(f"\nQR saved to: {qr_png}")
    except ImportError:
        text, _ = generate_qr_code_no_segno(uri, Path("."))
        print(text)

    return 0


def cmd_install_service(_argv: list[str]) -> int:
    import os
    import shutil
    from pathlib import Path

    service_source = Path(__file__).parent / "systemd" / "hermes-companion.service"
    if not service_source.exists():
        # Generate inline if template not found
        service_content = _generate_systemd_service()
    else:
        service_content = service_source.read_text()

    # Determine service dir
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    service_dir = Path(xdg_data) / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_dest = service_dir / "hermes-companion.service"

    # Fill in the user's path
    import shutil as _shutil
    python_path = _shutil.which("python3") or "/usr/bin/python3"
    service_content = service_content.replace("{{PYTHON_PATH}}", python_path)

    service_dest.write_text(service_content)
    print(f"Systemd user service installed: {service_dest}")
    print()
    print("Enable and start:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now hermes-companion")
    print()
    return 0


def _generate_systemd_service() -> str:
    return """[Unit]
Description=Hermes Companion Server
After=network.target

[Service]
Type=simple
ExecStart={{PYTHON_PATH}} -c "from hermes_companion.server import main; main()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="hermes-companion",
        description="Hermes Companion Server",
    )
    subparsers = parser.add_subparsers(dest="command")

    # setup — args are forwarded to setup_wizard.main() for parsing
    subparsers.add_parser("setup", help="First-run setup wizard")

    # serve
    subparsers.add_parser("serve", help="Start the companion server")

    # qr — args forwarded to qr handler
    subparsers.add_parser("qr", help="Generate connection QR code")

    # install-service
    subparsers.add_parser("install-service", help="Install systemd user service")

    args, rest = parser.parse_known_args(argv)

    if args.command == "setup":
        return cmd_setup(rest)
    elif args.command == "serve":
        return cmd_serve(rest)
    elif args.command == "qr":
        return cmd_qr(rest)
    elif args.command == "install-service":
        return cmd_install_service(rest)
    elif args.command is None:
        parser.print_help()
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
