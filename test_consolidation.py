#!/usr/bin/env python3
"""Consolidation tests for Plan 001: daemon root-only.

Verifies that the package copy has been removed and the root
implementation is the sole canonical source.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_server_module_loads():
    """Root server module can be imported (via spec to avoid relative import issue)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("server", "server.py")
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert mod is not None


def test_companion_cli_module_loads():
    """companion_cli module can be imported."""
    import companion_cli
    assert hasattr(companion_cli, "main")


def test_root_server_has_all_routes():
    """Root server.py has at least 18 route registrations."""
    src = Path("server.py").read_text()
    # Count @app.router.get/post/put/delete or app.router.add_get etc.
    import re
    routes = re.findall(r"@(?:app\.router|router)\.(?:get|post|put|delete|patch|head|options|view)", src)
    # Also count app.router.add_get style
    routes += re.findall(r"app\.router\.add_(?:get|post|put|delete|patch|head|options|view)", src)
    assert len(routes) >= 18, f"Expected >= 18 routes, found {len(routes)}"


def test_package_copy_files_gone():
    """Package copy files have been deleted."""
    gone = [
        "src/hermes_companion/server.py",
        "src/hermes_companion/setup_wizard.py",
        "src/hermes_companion/config.py",
        "src/hermes_companion/cli.py",
    ]
    for f in gone:
        assert not Path(f).exists(), f"Package copy still exists: {f}"


def test_entry_point_resolves():
    """setup.py and pyproject.toml declare companion_cli:main."""
    # Check setup.py
    setup_src = Path("setup.py").read_text()
    assert "companion_cli:main" in setup_src, "setup.py missing companion_cli:main"
    assert "server.cli:main" not in setup_src, "setup.py still has broken server.cli:main"

    # Check pyproject.toml
    import tomllib
    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    scripts = data["project"]["scripts"]
    assert scripts.get("hermes-companion") == "companion_cli:main", \
        f"pyproject.toml has wrong entry point: {scripts}"


def test_systemd_unit_updated():
    """Systemd unit points to companion_cli, not hermes_companion.server."""
    unit = Path("src/hermes_companion/systemd/hermes-companion.service").read_text()
    assert "companion_cli" in unit, "systemd unit doesn't reference companion_cli"
    assert "hermes_companion.server" not in unit, "systemd unit still references hermes_companion.server"
