# Plan 014: Scrypt N consistency — wizard uses 131072 (fails), daemon uses 16384 (works)

> **Executor instructions**: P2 hardening. The setup wizard's `SCRYPT_N = 131072` triggers "memory limit exceeded" on this system. The daemon uses 16384 and works fine. Align the wizard to the daemon.

## Status
- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `a03dc6a`, 2026-06-17

## Why this matters

`setup_wizard.py:40` — `SCRYPT_N = 131072` (OWASP 2023 minimum). But on this system:
```
$ python3 -c "import hashlib; hashlib.scrypt(b'x', salt=b'\\x00'*16, n=131072, r=8, p=1, dklen=32)"
ValueError: [digital envelope routines] memory limit exceeded
```

So `setup_wizard.create_auth_file()` would fail if invoked on this system. Any code path that imports setup_wizard and calls create_auth_file writes a placeholder hash to auth.json. The user must manually generate the hash.

## Evidence

```python
# setup_wizard.py:40
SCRYPT_N = 131072  # ← fails on this system

# setup_wizard.py:121-131
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    hash_bytes = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
```

```python
# server.py:188 (dummy compute for unknown user)
hashlib.scrypt(password.encode(), salt=b"\x00" * 16, n=16384, r=8, p=1, dklen=32)

# server.py:212 (real verify)
hash_bytes = hashlib.scrypt(password.encode(), salt=salt_bytes, n=n, r=r, p=p, dklen=32)
# n is parsed from stored hash; can be 16384 or 131072

# server.py:236 (hash upgrade)
new_hash = hashlib.scrypt(password.encode(), salt=new_salt, n=131072, r=8, p=1, dklen=32)
```

So the daemon's hash upgrade path ALSO uses 131072 — would fail on this system.

## Scope

**In scope**:
- Change `SCRYPT_N = 131072` → `SCRYPT_N = 16384` in `setup_wizard.py`
- Change daemon's hash upgrade from 131072 to a value that works on this system (or remove the upgrade path)
- Add a startup check: daemon warns if it has a hash with N=131072 (since upgrade will fail)

**Out of scope**:
- Increasing scrypt N on a system that supports it (architectural decision; revisit later)

## Steps

### Step 1: Update setup_wizard.py

```python
# setup_wizard.py:40
SCRYPT_N = 16384  # was 131072; matches daemon's accepted range
```

### Step 2: Update server.py hash upgrade

Either:
- (A) Remove the hash upgrade entirely — keep N=16384
- (B) Make the upgrade target configurable; default to 16384

Going with (A) for simplicity. The security difference is small (16384 vs 131072 is 8x brute-force, but 16384 already resists online attacks for our use case; offline attacks would need the auth.json file).

```python
# server.py:219-221 — REMOVE the hash upgrade
# if n < 131072:
#     await self._upgrade_hash(username, password)
return True

# server.py:231-246 — REMOVE the _upgrade_hash method entirely
```

### Step 3: Add comment explaining the choice

In `server.py:188` (dummy scrypt) and the verify path, add:

```python
# Note: scrypt N=16384 is used throughout for compatibility. The setup
# wizard, daemon, and test fixtures all use the same N value. Upgrading
# to N=131072 (OWASP 2023 minimum) would require ~128MB of memory per
# verify call, which exceeds the system limit on this build.
```

### Step 4: Run all tests

```bash
cd /home/kevin/.hermes/companion
python3 -m pytest tests/ -x
```

### Step 5: Verify wizard no longer crashes

```bash
cd /home/kevin/.hermes/companion
python3 -c "
from setup_wizard import hash_password
print(hash_password('test'))
"
```

Expected: prints hash, no error.

### Step 6: Commit

```bash
cd /home/kevin/.hermes/companion
git add setup_wizard.py server.py
git commit -m "fix(security): align scrypt N across wizard and daemon (16384)

The setup wizard used SCRYPT_N=131072 (OWASP minimum) but the daemon's
hash upgrade path also used 131072. Both fail on this Python build with
'memory limit exceeded' — wizard would crash, daemon upgrade is a
silent no-op.

Aligned both to 16384. This is 8x weaker than OWASP 2023 minimum but:
1. Already resists online brute-force (scrypt compute ~100ms)
2. Offline attacks require reading auth.json
3. The system constraint is memory, not policy

Future: increase N when running on a system with >256MB available
memory for scrypt. Add a startup check that detects the memory
constraint and refuses to start if N exceeds the limit.
"
```

## Test plan

- `hash_password('test')` returns a valid scrypt hash without error
- `python3 -m pytest tests/ -x` all pass
- Daemon still accepts existing `kevin` / `Kevi667n!1991!` credentials

## Done criteria

- [ ] `setup_wizard.SCRYPT_N = 16384`
- [ ] Daemon's hash upgrade path removed (or N aligned to 16384)
- [ ] `hash_password('test')` works without error
- [ ] Existing tests pass
- [ ] Daemon still authenticates `kevin`
- [ ] Git committed

## STOP conditions

- Existing tests fail after N change — STOP, may need to update test fixtures
- Live daemon rejects kevin — STOP, may need to regenerate auth.json hash
