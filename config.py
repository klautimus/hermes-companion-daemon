"""Config loading and Hermes binary auto-detection for companion daemon."""

import copy
import os
import pwd
import shutil
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("companion.config")

# ── Default config values ────────────────────────────────────
DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8777,
    },
    "hermes": {
        "api_url": "http://127.0.0.1:8642",
        "api_key": "",
        "binary_path": "auto",
    },
    "auth": {
        "file_path": "~/.hermes/companion/auth.json",
    },
    "attachments": {
        "dir": "~/.hermes/companion/attachments",
        "max_upload_mb": 25,
    },
}

# Config file search paths (first found wins, before env override)
_real_home = pwd.getpwuid(os.getuid()).pw_dir
CONFIG_SEARCH_PATHS = [
    Path("./config.yaml"),
    Path(_real_home) / ".config" / "hermes-companion" / "config.yaml",
    Path("/etc/hermes-companion/config.yaml"),
]

# Hermes binary fallback locations (when not in PATH)
HERMES_FALLBACK_PATHS = [
    Path(_real_home) / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
    Path("/usr/local/bin/hermes"),
    Path("/opt/hermes/bin/hermes"),
]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base; returns new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand(path: str) -> Path:
    """Expand ~ using the real user home and return absolute Path."""
    if path.startswith("~"):
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        path = path.replace("~", real_home, 1)
    return Path(path).resolve()


def find_config_path() -> Path | None:
    """Return the first config file found in search paths, or None."""
    for p in CONFIG_SEARCH_PATHS:
        expanded = _expand(str(p)) if not p.is_absolute() else p
        # For ./config.yaml, resolve relative to cwd
        if p == Path("./config.yaml"):
            expanded = Path.cwd() / p
        if expanded.is_file():
            return expanded
    return None


def load_config() -> dict[str, Any]:
    """Load config from file + env overrides. Returns fully resolved dict."""
    config = copy.deepcopy(DEFAULT_CONFIG)

    # 1) File
    config_path = find_config_path()
    if config_path:
        logger.info("Loading config from %s", config_path)
        try:
            raw = yaml.safe_load(config_path.read_text()) or {}
            config = _deep_merge(config, raw)
        except Exception as e:
            logger.error("Failed to parse config file %s: %s", config_path, e)
    else:
        logger.info("No config file found; using defaults")

    # 2) Env overrides
    env = os.environ

    # Load API key from ~/.hermes/.env if not set in env (backward compat)
    if not env.get("HERMES_API_KEY") and not env.get("API_SERVER_KEY") and not config["hermes"].get("api_key"):
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        env_file = Path(real_home) / ".hermes" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("API_SERVER_KEY=") or line.startswith("HERMES_API_KEY="):
                    config["hermes"]["api_key"] = line.split("=", 1)[1].strip()
                    break

    if env.get("COMPANION_HOST"):
        config["server"]["host"] = env["COMPANION_HOST"]
    if env.get("COMPANION_PORT"):
        config["server"]["port"] = int(env["COMPANION_PORT"])
    if env.get("HERMES_API_URL"):
        config["hermes"]["api_url"] = env["HERMES_API_URL"]
    if env.get("HERMES_API_KEY"):
        config["hermes"]["api_key"] = env["HERMES_API_KEY"]
    # Backward compat: API_SERVER_KEY env var
    if env.get("API_SERVER_KEY"):
        config["hermes"]["api_key"] = env["API_SERVER_KEY"]
    if env.get("HERMES_BINARY_PATH"):
        config["hermes"]["binary_path"] = env["HERMES_BINARY_PATH"]

    # 3) Expand paths
    config["auth"]["file_path"] = _expand(config["auth"]["file_path"])
    config["attachments"]["dir"] = _expand(config["attachments"]["dir"])

    return config


def detect_hermes_binary(config_path: str = "auto") -> str:
    """Detect hermes binary path using priority-ordered strategy.

    Priority:
      1. Explicit config path (if not "auto")
      2. `which hermes` (PATH lookup)
      3. Fallback hardcoded paths
    """
    # 1) Explicit config
    if config_path and config_path != "auto":
        p = Path(config_path)
        if p.is_file():
            logger.info("Using Hermes binary from config: %s", p)
            return str(p)
        logger.warning("Configured hermes binary not found at %s", config_path)

    # 2) PATH lookup
    found = shutil.which("hermes")
    if found:
        logger.info("Found Hermes binary on PATH: %s", found)
        return found

    # 3) Fallback paths
    for p in HERMES_FALLBACK_PATHS:
        if p.is_file():
            logger.info("Using Hermes binary fallback: %s", p)
            return str(p)

    logger.warning("No Hermes binary found; defaulting to /usr/local/bin/hermes")
    return "/usr/local/bin/hermes"


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate required fields; returns list of error messages."""
    errors: list[str] = []
    server = config.get("server", {})
    if not server.get("host"):
        errors.append("server.host is required")
    port = server.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append(f"server.port must be 1-65535, got {port!r}")

    hermes = config.get("hermes", {})
    if not hermes.get("api_url"):
        errors.append("hermes.api_url is required")

    auth = config.get("auth", {})
    if not auth.get("file_path"):
        errors.append("auth.file_path is required")

    return errors


def generate_default_config(dest: Path | None = None) -> Path:
    """Generate a default config.yaml with auto-detected values.

    Writes to dest if provided, otherwise to ~/.config/hermes-companion/config.yaml.
    Returns the path written.
    """
    if dest is None:
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        dest = Path(real_home) / ".config" / "hermes-companion" / "config.yaml"

    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Build a clean dict with only strings (not Path objects) for YAML output
    auto_binary = detect_hermes_binary("auto")
    yaml_config = {
        "server": {
            "host": DEFAULT_CONFIG["server"]["host"],
            "port": DEFAULT_CONFIG["server"]["port"],
        },
        "hermes": {
            "api_url": DEFAULT_CONFIG["hermes"]["api_url"],
            "api_key": DEFAULT_CONFIG["hermes"]["api_key"],
            "binary_path": auto_binary,
        },
        "auth": {
            "file_path": DEFAULT_CONFIG["auth"]["file_path"],
        },
        "attachments": {
            "dir": DEFAULT_CONFIG["attachments"]["dir"],
            "max_upload_mb": DEFAULT_CONFIG["attachments"]["max_upload_mb"],
        },
    }

    with open(dest, "w") as f:
        yaml.dump(yaml_config, f, default_flow_style=False, sort_keys=False)

    logger.info("Generated default config at %s", dest)
    return dest
