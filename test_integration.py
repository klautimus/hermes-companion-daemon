"""Integration tests: server starts with generated config."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config import generate_default_config, load_config, detect_hermes_binary


class TestServerIntegration:
    def test_server_imports_with_defaults(self):
        """Server module loads with default config (no config file)."""
        with patch("config.find_config_path", return_value=None):
            with patch.dict(os.environ, {"API_SERVER_KEY": "test-key-for-import"}):
                import importlib
                import server
                importlib.reload(server)
                assert server.HOST == "127.0.0.1"
                assert server.PORT == 8777

    def test_server_loads_generated_config(self, tmp_path):
        """Server loads a generated config file."""
        cfg = tmp_path / "config.yaml"
        generate_default_config(cfg)

        with patch("config.find_config_path", return_value=cfg):
            with patch.dict(os.environ, {"API_SERVER_KEY": "test-key-for-import"}):
                import importlib
                import server
                importlib.reload(server)
                assert server.HOST == "127.0.0.1"
                assert server.HERMES_BIN is not None

    def test_hermes_binary_auto_detection(self):
        """Auto-detection finds a binary (or returns default)."""
        result = detect_hermes_binary("auto")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_generated_config_is_valid_yaml(self, tmp_path):
        """Generated config parses as valid YAML with all expected keys."""
        cfg = tmp_path / "config.yaml"
        generate_default_config(cfg)
        data = yaml.safe_load(cfg.read_text())

        assert "server" in data
        assert "hermes" in data
        assert "auth" in data
        assert "attachments" in data
        assert data["server"]["host"] == "127.0.0.1"
        assert data["server"]["port"] == 8777
        assert data["hermes"]["binary_path"] != "auto"  # should be resolved

    def test_config_precedence_env_overrides_file(self, tmp_path):
        """Env vars override file values."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("server:\n  port: 9999\n")

        import os
        with patch.dict(os.environ, {"COMPANION_PORT": "1234"}):
            with patch("config.find_config_path", return_value=cfg):
                config = load_config()
            assert config["server"]["port"] == 1234

    def test_no_hardcoded_paths_in_server(self):
        """Verify server.py doesn't contain hardcoded /home/kevin paths."""
        import inspect
        import server
        source = inspect.getsource(server)
        # Should not contain hardcoded paths
        assert "/home/kevin/.hermes/hermes-agent/venv/bin/hermes" not in source
        assert 'Path("/home/kevin' not in source
