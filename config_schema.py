"""Configuration schema and defaults for Hermes Companion server."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import yaml
import os


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
    file: str = "~/.config/hermes-companion/auth.json"


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
        if "server" in data:
            cfg.server.host = data["server"].get("host", cfg.server.host)
            cfg.server.port = data["server"].get("port", cfg.server.port)
        if "hermes" in data:
            cfg.hermes.api_url = data["hermes"].get("api_url", cfg.hermes.api_url)
            cfg.hermes.api_key = data["hermes"].get("api_key", cfg.hermes.api_key)
            cfg.hermes.cli_path = data["hermes"].get("cli_path", cfg.hermes.cli_path)
        if "auth" in data:
            cfg.auth.file = data["auth"].get("file", cfg.auth.file)
        if "storage" in data:
            cfg.storage.attachments_dir = data["storage"].get(
                "attachments_dir", cfg.storage.attachments_dir
            )
            cfg.storage.max_upload_size = data["storage"].get(
                "max_upload_size", cfg.storage.max_upload_size
            )
        return cfg

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
    return cfg.resolve_env_overrides()


def save_config(cfg: CompanionConfig) -> None:
    """Save config to YAML file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg.to_dict(), f, default_flow_style=False, sort_keys=False)


def config_exists() -> bool:
    """Check if config file exists."""
    return CONFIG_FILE.exists()