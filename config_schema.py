"""Configuration schema and defaults for Hermes Companion server."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import yaml
import os


def _coerce_int(value, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"config field '{field_name}' must be int, got {type(value).__name__}: {value!r}")


def _coerce_str(value, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"config field '{field_name}' must be str, got {type(value).__name__}: {value!r}")
    return value


def _coerce_bool(value, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "yes", "1"):
            return True
        if value.lower() in ("false", "no", "0"):
            return False
    if isinstance(value, int):
        return bool(value)
    raise ValueError(f"config field '{field_name}' must be bool, got {type(value).__name__}: {value!r}")


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8777


@dataclass
class HermesConfig:
    api_url: str = "http://127.0.0.1:8642"
    api_key: str = ""
    cli_path: str = "auto"  # "auto" or explicit path


@dataclass
class AuthConfig:
    file: str = "~/.hermes/companion/auth.json"


@dataclass
class StorageConfig:
    attachments_dir: str = "~/.config/hermes-companion/attachments"
    max_upload_size: int = 10485760  # 10 MB


@dataclass
class CompanionConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    hermes: HermesConfig = field(default_factory=HermesConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    def to_dict(self) -> dict:
        return {
            "server": {"host": self.server.host, "port": self.server.port},
            "hermes": {
                "api_url": self.hermes.api_url,
                "api_key": self.hermes.api_key,
                "cli_path": self.hermes.cli_path,
            },
            "auth": {"file": self.auth.file},
            "storage": {
                "attachments_dir": self.storage.attachments_dir,
                "max_upload_size": self.storage.max_upload_size,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompanionConfig":
        cfg = cls()
        errors = []
        if "server" in data:
            s = data["server"]
            if "host" in s:
                try:
                    cfg.server.host = _coerce_str(s["host"], "server.host")
                except ValueError as e:
                    errors.append(str(e))
            if "port" in s:
                try:
                    cfg.server.port = _coerce_int(s["port"], "server.port")
                except ValueError as e:
                    errors.append(str(e))
        if "hermes" in data:
            h = data["hermes"]
            if "api_url" in h:
                try:
                    cfg.hermes.api_url = _coerce_str(h["api_url"], "hermes.api_url")
                except ValueError as e:
                    errors.append(str(e))
            if "api_key" in h:
                try:
                    cfg.hermes.api_key = _coerce_str(h["api_key"], "hermes.api_key")
                except ValueError as e:
                    errors.append(str(e))
            if "cli_path" in h:
                try:
                    cfg.hermes.cli_path = _coerce_str(h["cli_path"], "hermes.cli_path")
                except ValueError as e:
                    errors.append(str(e))
        if "auth" in data:
            a = data["auth"]
            if "file" in a:
                try:
                    cfg.auth.file = _coerce_str(a["file"], "auth.file")
                except ValueError as e:
                    errors.append(str(e))
        if "storage" in data:
            st = data["storage"]
            if "attachments_dir" in st:
                try:
                    cfg.storage.attachments_dir = _coerce_str(st["attachments_dir"], "storage.attachments_dir")
                except ValueError as e:
                    errors.append(str(e))
            if "max_upload_size" in st:
                try:
                    cfg.storage.max_upload_size = _coerce_int(st["max_upload_size"], "storage.max_upload_size")
                except ValueError as e:
                    errors.append(str(e))
        if errors:
            raise ValueError("config validation errors:\n  " + "\n  ".join(errors))
        return cfg

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty if valid)."""
        errors = []
        if not (1 <= self.server.port <= 65535):
            errors.append(f"server.port must be 1-65535, got {self.server.port}")
        if not self.hermes.api_url.startswith(("http://", "https://")):
            errors.append(f"hermes.api_url must start with http:// or https://, got {self.hermes.api_url!r}")
        if self.storage.max_upload_size <= 0:
            errors.append(f"storage.max_upload_size must be positive, got {self.storage.max_upload_size}")
        if self.storage.max_upload_size > 1024 * 1024 * 1024:  # 1 GB
            errors.append(f"storage.max_upload_size exceeds 1 GB, got {self.storage.max_upload_size}")
        return errors

    def get_expanded_paths(self) -> dict[str, Path]:
        """Return all paths expanded to absolute Path objects."""
        return {
            "auth_file": Path(self.auth.file).expanduser().resolve(),
            "attachments_dir": Path(self.storage.attachments_dir).expanduser().resolve(),
            "config_dir": Path(self.auth.file).expanduser().parent.resolve(),
        }

    def resolve_env_overrides(self) -> "CompanionConfig":
        """Apply environment variable overrides (env vars take precedence)."""
        import os

        cfg = CompanionConfig.from_dict(self.to_dict())

        if host := os.getenv("COMPANION_HOST"):
            cfg.server.host = host
        if port := os.getenv("COMPANION_PORT"):
            cfg.server.port = int(port)
        if api_url := os.getenv("HERMES_API_URL"):
            cfg.hermes.api_url = api_url
        if api_key := os.getenv("API_SERVER_KEY"):
            cfg.hermes.api_key = api_key

        return cfg


CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "hermes-companion"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
DEFAULT_CONFIG = CompanionConfig()


def load_config() -> CompanionConfig:
    """Load config from YAML file, apply env overrides, return resolved config."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = yaml.safe_load(f) or {}
        cfg = CompanionConfig.from_dict(data)
    else:
        cfg = DEFAULT_CONFIG
    errors = cfg.validate()
    if errors:
        raise SystemExit(
            "[FATAL] config validation errors:\n  " + "\n  ".join(errors)
        )
    return cfg.resolve_env_overrides()


def save_config(cfg: CompanionConfig) -> None:
    """Save config to YAML file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg.to_dict(), f, default_flow_style=False, sort_keys=False)


def config_exists() -> bool:
    """Check if config file exists."""
    return CONFIG_FILE.exists()
