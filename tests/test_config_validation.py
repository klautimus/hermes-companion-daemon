"""Tests for config_schema.py type coercion and validation."""

import pytest
from config_schema import CompanionConfig, ServerConfig, HermesConfig, AuthConfig, StorageConfig


# ── from_dict: type coercion ──────────────────────────────────

class TestFromDictCoercion:
    def test_coerces_string_port_to_int(self):
        """YAML may parse '8777' as str; from_dict must coerce."""
        cfg = CompanionConfig.from_dict({"server": {"port": "8777"}})
        assert cfg.server.port == 8777
        assert isinstance(cfg.server.port, int)

    def test_coerces_int_port_stays_int(self):
        cfg = CompanionConfig.from_dict({"server": {"port": 9090}})
        assert cfg.server.port == 9090
        assert isinstance(cfg.server.port, int)

    def test_rejects_non_coercible_port(self):
        with pytest.raises(ValueError, match=r"'server\.port' must be int"):
            CompanionConfig.from_dict({"server": {"port": "eight"}})

    def test_coerces_string_max_upload_size_to_int(self):
        cfg = CompanionConfig.from_dict({"storage": {"max_upload_size": "20971520"}})
        assert cfg.storage.max_upload_size == 20971520
        assert isinstance(cfg.storage.max_upload_size, int)

    def test_rejects_non_coercible_max_upload_size(self):
        with pytest.raises(ValueError, match=r"'storage\.max_upload_size' must be int"):
            CompanionConfig.from_dict({"storage": {"max_upload_size": "ten"}})

    def test_coerces_str_fields(self):
        cfg = CompanionConfig.from_dict({
            "server": {"host": "0.0.0.0"},
            "hermes": {"api_url": "http://10.0.0.1:8642", "api_key": "key123", "cli_path": "/usr/bin/hermes"},
            "auth": {"file": "/tmp/auth.json"},
            "storage": {"attachments_dir": "/tmp/attachments"},
        })
        assert cfg.server.host == "0.0.0.0"
        assert cfg.hermes.api_url == "http://10.0.0.1:8642"
        assert cfg.hermes.api_key == "key123"
        assert cfg.hermes.cli_path == "/usr/bin/hermes"
        assert cfg.auth.file == "/tmp/auth.json"
        assert cfg.storage.attachments_dir == "/tmp/attachments"

    def test_rejects_non_str_for_str_field(self):
        with pytest.raises(ValueError, match=r"'server\.host' must be str"):
            CompanionConfig.from_dict({"server": {"host": 12345}})

    def test_empty_dict_returns_defaults(self):
        cfg = CompanionConfig.from_dict({})
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 8777
        assert cfg.hermes.api_url == "http://127.0.0.1:8642"

    def test_multiple_errors_collected(self):
        with pytest.raises(ValueError, match="config validation errors"):
            CompanionConfig.from_dict({
                "server": {"port": "abc"},
                "storage": {"max_upload_size": "xyz"},
            })


# ── validate() ────────────────────────────────────────────────

class TestValidate:
    def test_valid_config_no_errors(self):
        cfg = CompanionConfig()
        errors = cfg.validate()
        assert errors == []

    def test_rejects_port_zero(self):
        cfg = CompanionConfig()
        cfg.server.port = 0
        errors = cfg.validate()
        assert any("server.port" in e for e in errors)

    def test_rejects_port_too_high(self):
        cfg = CompanionConfig()
        cfg.server.port = 99999
        errors = cfg.validate()
        assert any("server.port" in e for e in errors)

    def test_rejects_port_negative(self):
        cfg = CompanionConfig()
        cfg.server.port = -1
        errors = cfg.validate()
        assert any("server.port" in e for e in errors)

    def test_rejects_bad_url_scheme(self):
        cfg = CompanionConfig()
        cfg.hermes.api_url = "ftp://example.com"
        errors = cfg.validate()
        assert any("api_url" in e for e in errors)

    def test_rejects_empty_url(self):
        cfg = CompanionConfig()
        cfg.hermes.api_url = ""
        errors = cfg.validate()
        assert any("api_url" in e for e in errors)

    def test_rejects_negative_max_upload(self):
        cfg = CompanionConfig()
        cfg.storage.max_upload_size = -1
        errors = cfg.validate()
        assert any("max_upload_size" in e for e in errors)

    def test_rejects_zero_max_upload(self):
        cfg = CompanionConfig()
        cfg.storage.max_upload_size = 0
        errors = cfg.validate()
        assert any("max_upload_size" in e for e in errors)

    def test_rejects_max_upload_over_1gb(self):
        cfg = CompanionConfig()
        cfg.storage.max_upload_size = 2 * 1024 * 1024 * 1024  # 2 GB
        errors = cfg.validate()
        assert any("max_upload_size" in e for e in errors)

    def test_accepts_1gb_max_upload(self):
        cfg = CompanionConfig()
        cfg.storage.max_upload_size = 1024 * 1024 * 1024  # exactly 1 GB
        errors = cfg.validate()
        assert not any("max_upload_size" in e for e in errors)

    def test_accepts_valid_http_url(self):
        cfg = CompanionConfig()
        cfg.hermes.api_url = "http://127.0.0.1:8642"
        errors = cfg.validate()
        assert not any("api_url" in e for e in errors)

    def test_accepts_valid_https_url(self):
        cfg = CompanionConfig()
        cfg.hermes.api_url = "https://example.com"
        errors = cfg.validate()
        assert not any("api_url" in e for e in errors)

    def test_boundary_port_1_valid(self):
        cfg = CompanionConfig()
        cfg.server.port = 1
        errors = cfg.validate()
        assert not any("server.port" in e for e in errors)

    def test_boundary_port_65535_valid(self):
        cfg = CompanionConfig()
        cfg.server.port = 65535
        errors = cfg.validate()
        assert not any("server.port" in e for e in errors)


# ── from_dict + validate integration ──────────────────────────

class TestFromDictValidateIntegration:
    def test_from_dict_then_validate_clean(self):
        cfg = CompanionConfig.from_dict({
            "server": {"host": "0.0.0.0", "port": "9090"},
            "hermes": {"api_url": "http://10.0.0.1:8642"},
            "storage": {"max_upload_size": "52428800"},
        })
        errors = cfg.validate()
        assert errors == []
        assert cfg.server.port == 9090
        assert isinstance(cfg.server.port, int)
        assert cfg.storage.max_upload_size == 52428800
