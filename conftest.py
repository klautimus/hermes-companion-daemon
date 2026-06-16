"""Hermes Companion daemon test config.

This conftest is loaded by pytest before any test collection. It puts the
daemon repo root on sys.path so that relative imports inside the daemon
package (e.g. `from .config_schema import load_config` in server.py) work
correctly when tests import `from server import ...`.

Without this, `pytest tests/` from the daemon root fails collection with
`ImportError: attempted relative import with no known parent package`.
"""
import os
import sys

# Add the daemon repo root to sys.path so server.py's relative imports
# (`from .config_schema import ...`) resolve correctly during test runs.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
