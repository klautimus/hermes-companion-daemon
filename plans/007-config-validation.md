# Plan 007: Config schema — type coercion/validation in from_dict

> **Executor instructions**: Modifies the canonical `config_schema.py` and `config.py` after Plan 001. Run all verification.
>
> **Drift check**:
> ```bash
> cd /home/kevin/.hermes/companion
> git diff --stat f78cd82..HEAD -- config_schema.py config.py
> ```

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED
- **Depends on**: 001
- **Category**: bug
- **Planned at**: commit `f78cd82`, 2026-06-16

## Why this matters

`config_schema.py:57-75` (the `from_dict` classmethod on `CompanionConfig`) uses `.get(key, default)` for every field without type coercion. A user-supplied `config.yaml` with `port: "8777"` (string) gets stored as a string; later `int()` calls or `web.run_app(host, port, ...)` may crash with `TypeError`. The failure mode is obscure — config errors surface as runtime crashes in handlers, not as clear "your config is wrong" errors. This plan adds explicit type coercion and a `validate()` method that raises a clear error before the server starts.

## Current state

**File**: `config_schema.py` (root, 127 LOC after Plan 001)

Lines 50-80 (approximate, read with `read_file` to verify):
```python
@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8777

@dataclass
class HermesConfig:
    api_url: str = "http://127.0.0.1:8642"
    api_key: str = ""
    cli_path: str = "auto"

@dataclass
class AuthConfig:
    file_path: str = "~/.config/hermes-companion/auth.json"
    lockout_seconds: int = 60
    max_failures: int = 5

@dataclass
class StorageConfig:
    attachments_dir: str = "~/.local/share/hermes-companion/attachments"
    max_upload_size: int = 25 * 1024 * 1024  # 25 MB

@dataclass
class CompanionConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    hermes: HermesConfig = field(default_factory=HermesConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "CompanionConfig":
        cfg = cls()
        if "server" in data:
            for k, v in data["server"].items():
                if hasattr(cfg.server, k):
                    setattr(cfg.server, k, v)   # <-- BUG: no type check
        # ... same for hermes, auth, storage
        return cfg
```

**File**: `config.py` (root, 223 LOC after Plan 001)

`config.py:load_config()` calls `CompanionConfig.from_dict(yaml.safe_load(open(...)))`. The result is the live config used by `server.py`.

**Repo conventions**:
- `@dataclass` from `dataclasses` (stdlib)
- Type hints on all fields
- Tests in `test_config.py` and `tests/test_first_run.py`

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Run tests | `cd /home/kevin/.hermes/companion && python -m pytest -xvs` | all pass |
| New test | `cd /home/kevin/.hermes/companion && python -m pytest tests/test_config_validation.py -v` | new tests pass |
| Smoke import | `cd /home/kevin/.hermes/companion && python -c "import config; print('ok')"` | `ok` |

## Scope

**In scope**:
- `config_schema.py` — add `validate()` method, type coercion in `from_dict`, helpers for int/str/bool coercion
- `config.py` — call `validate()` after `load_config()`, surface errors clearly
- `tests/test_config_validation.py` — create

**Out of scope**:
- Adding new config fields (separate concern)
- Default value changes (covered by 002 for SCRYPT_N)
- New config file format (YAML is the only supported format)

## Git workflow

- Branch: `advisor/007-config-validation`
- Commit style: `fix(config):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/companion
git diff --stat f78cd82..HEAD -- config_schema.py config.py
```

If either changed, STOP.

### Step 2: Add type coercion helpers in `config_schema.py`

At the top of `config_schema.py`, after the imports, add:

```python
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
```

### Step 3: Add type coercion in `from_dict`

Replace the existing `from_dict` method to use the coercion helpers:

```python
@classmethod
def from_dict(cls, data: dict) -> "CompanionConfig":
    cfg = cls()
    errors = []
    if "server" in data:
        for k, v in data["server"].items():
            if not hasattr(cfg.server, k):
                errors.append(f"unknown server field: {k}")
                continue
            if k == "port":
                try:
                    setattr(cfg.server, k, _coerce_int(v, f"server.{k}"))
                except ValueError as e:
                    errors.append(str(e))
            else:
                try:
                    setattr(cfg.server, k, _coerce_str(v, f"server.{k}"))
                except ValueError as e:
                    errors.append(str(e))
    if errors:
        raise ValueError("config validation errors:\n  " + "\n  ".join(errors))
    return cfg
```

Repeat for `hermes`, `auth`, `storage` sections. Each field has a type; coerce using the right helper.

### Step 4: Add `validate()` method

Add a method to `CompanionConfig`:

```python
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
    if self.auth.max_failures < 0:
        errors.append(f"auth.max_failures must be non-negative, got {self.auth.max_failures}")
    if self.auth.lockout_seconds < 0:
        errors.append(f"auth.lockout_seconds must be non-negative, got {self.auth.lockout_seconds}")
    return errors
```

### Step 5: Call `validate()` in `config.py:load_config()`

In `config.py:load_config()` (find the function, read with `read_file` to verify), after the `from_dict` call:

```python
errors = cfg.validate()
if errors:
    for e in errors:
        print(f"[FATAL] {e}", file=sys.stderr)
    sys.exit(1)
```

### Step 6: Write tests in `tests/test_config_validation.py`

```python
import pytest
from config_schema import CompanionConfig, ServerConfig, validate as config_validate

def test_from_dict_coerces_string_port_to_int():
    """YAML may parse '8777' as str; from_dict must coerce."""
    cfg = CompanionConfig.from_dict({"server": {"port": "8777"}})
    assert cfg.server.port == 8777
    assert isinstance(cfg.server.port, int)

def test_from_dict_rejects_non_coercible_port():
    with pytest.raises(ValueError, match="server.port must be int"):
        CompanionConfig.from_dict({"server": {"port": "eight"}})

def test_from_dict_warns_on_unknown_field():
    """Unknown fields raise an error (strict)."""
    with pytest.raises(ValueError, match="unknown server field"):
        CompanionConfig.from_dict({"server": {"nonexistent": "value"}})

def test_validate_rejects_out_of_range_port():
    cfg = CompanionConfig()
    cfg.server.port = 99999
    errors = cfg.validate()
    assert any("server.port" in e for e in errors)

def test_validate_rejects_bad_url():
    cfg = CompanionConfig()
    cfg.hermes.api_url = "ftp://example.com"
    errors = cfg.validate()
    assert any("api_url" in e for e in errors)

def test_validate_rejects_negative_max_upload():
    cfg = CompanionConfig()
    cfg.storage.max_upload_size = -1
    errors = cfg.validate()
    assert any("max_upload_size" in e for e in errors)
```

### Step 7: Run all tests

```bash
cd /home/kevin/.hermes/companion
python -m pytest -xvs 2>&1 | tail -30
```

### Step 8: Commit

```bash
cd /home/kevin/.hermes/companion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(config): add type coercion and validation to CompanionConfig.from_dict

Previously, from_dict used setattr(...) with no type checking. A user
config with port: "8777" (string from YAML) was stored as a string,
and runtime crashes surfaced obscurely when int was expected.

Changes:
- Add _coerce_int, _coerce_str, _coerce_bool helpers in config_schema.py
- from_dict now coerces types and raises ValueError on unknown fields
- Add CompanionConfig.validate() returning a list of error strings
- config.py:load_config() calls validate() and exits with clear
  [FATAL] messages on errors
- Add tests/test_config_validation.py covering coercion, unknown fields,
  port range, URL scheme, and max upload size

Errors that previously crashed the daemon mid-startup now produce a
clear list of problems before the server runs.
EOF
)"
```

## Test plan

- New `tests/test_config_validation.py` — 6+ tests
- Existing `test_config.py` and `tests/test_first_run.py` should still pass
- Verification: `python -m pytest tests/test_config_validation.py -v`

## Done criteria

- [ ] `python -m pytest -xvs` exits 0
- [ ] `python -m pytest tests/test_config_validation.py -v` — all new tests pass
- [ ] `grep "def validate" config_schema.py` shows the new method
- [ ] `grep "_coerce_int" config_schema.py` shows 2+ matches (definition + usage)
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 007 row updated to `DONE`

## STOP conditions

- Plan 001 not DONE — STOP.
- Drift check shows config files changed — STOP.
- A test that previously passed (in test_config.py) now fails — STOP, may indicate a behavior change that breaks existing configs.

## Maintenance notes

- Strict validation may break existing user configs with unknown fields (deprecated keys, custom additions). If this becomes a problem, change `errors` to `warnings` for the unknown-field case.
- The 1 GB max upload size cap is arbitrary. If users legitimately need larger files, make it configurable via env var.
- For v2: add a `migrate_config()` function that upgrades old config schemas to new ones (similar to Plan 002's hash upgrade).
- The error format `[FATAL] {message}` matches what `server.py` already uses for API key missing. Consistent UX.
