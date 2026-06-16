#!/usr/bin/env python3
"""CLI entry point for Hermes Companion.

Provides:
- hermes-companion setup    : Interactive first-run setup wizard
- hermes-companion serve    : Start the companion server
"""

import sys
from pathlib import Path

# Add server directory to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from .setup_wizard import run_setup_wizard
    from .first_run import ensure_configured_or_exit
except ImportError:
    from setup_wizard import run_setup_wizard
    from first_run import ensure_configured_or_exit


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: hermes-companion <command>", file=sys.stderr)
        print("\nCommands:", file=sys.stderr)
        print("  setup    Run interactive first-run setup wizard", file=sys.stderr)
        print("  serve    Start the companion server", file=sys.stderr)
        return 1

    command = sys.argv[1]

    if command == "setup":
        return run_setup_wizard()

    elif command == "serve":
        # Check first-run before starting server
        ensure_configured_or_exit()

        # Import and run the server
        try:
            from .server import main as server_main
        except ImportError:
            from server import main as server_main
        return server_main()

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Available commands: setup, serve", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())