"""First-run detection for Hermes Companion server.

If config.yaml doesn't exist, print a helpful message and exit with code 2.
"""

import sys
from pathlib import Path

try:
    from .config_schema import CONFIG_FILE, config_exists
except ImportError:
    from config_schema import CONFIG_FILE, config_exists


def check_first_run() -> bool:
    """Check if this is the first run (no config file).

    Returns:
        True if first run (config missing), False otherwise.
    """
    return not config_exists()


def print_first_run_message_and_exit() -> None:
    """Print first-run message and exit with code 2."""
    print("First run detected. Run 'hermes-companion setup' to configure.", file=sys.stderr)
    sys.exit(2)


def ensure_configured_or_exit() -> None:
    """Call at server startup. Exits with code 2 if not configured."""
    if check_first_run():
        print_first_run_message_and_exit()


if __name__ == "__main__":
    # Allow running as standalone check
    if check_first_run():
        print_first_run_message_and_exit()
    else:
        print("Already configured.")
        sys.exit(0)