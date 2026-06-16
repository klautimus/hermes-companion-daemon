# Plan 002: Fix the QR setup-token end-to-end — Android must parse `token=`, not just `pass=`

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/projects/HermesCompanion && git diff --stat 0a30d82..HEAD -- app/src/main/java/org/hermes/community/companion/`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P0
- **Effort**: S
- **Risk**: LOW (additive change to deep link parsing)
- **Depends on**: 001 (daemon must be up for end-to-end test)
- **Category**: bug (security + UX integration)
- **Planned at**: commit `0a30d82` (Android), `d378902` (daemon), 2026-06-16

## Why this matters

Plan 003 (the v4 round) added a 256-bit setup token to QR codes so the plaintext password never appears on screen. **The daemon side was fixed**: `setup_wizard.py:176` writes `token=<token>` into the QR URI. But the **Android side was never updated** to read it. The app's `MainActivity.kt:94` still extracts `pass=<password>` and ignores `token=<token>` entirely.

**Net effect**: The QR-token security improvement is non-functional. Either:
- The QR code never gets used (user has to type the password manually — same as before), OR
- The app's deep-link parser silently drops the token, shows a setup error, and the user is stuck.

This is a paired change that was only half-implemented. It must be finished.

## Current state (verified 2026-06-16 by Atlas)

**Daemon QR URI format** (`~/.hermes/companion/setup_wizard.py:163-180`):
```python
def generate_qr_code(config: CompanionConfig, username: str, token: str) -> str:
    params = {
        "host": config.host,
        "port": str(config.port),
        "user": username,
        "token": token,           # <-- token, not password
    }
    query = urllib.parse.urlencode(params)
    return f"hermescompanion://configure?{query}"
```

**Android deep-link parser** (`~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/MainActivity.kt:80-100`):
```kotlin
fun parseDeepLinkIntent(intent: Intent): DeepLinkConfig? {
    val data = intent.data ?: return null
    if (data.scheme != "hermescompanion") return null
    val host = data.host ?: return null
    if (host != "configure") return null
    return DeepLinkConfig(
        host = data.getQueryParameter("host") ?: "",
        port = data.getQueryParameter("port")?.toIntOrNull() ?: 8777,
        username = data.getQueryParameter("user") ?: "",
        password = data.getQueryParameter("pass") ?: "",  // <-- looking for "pass", but daemon sends "token"
        board = data.getQueryParameter("board") ?: "default",
    )
}
```

**Token-redemption endpoint** (daemon): `POST /api/setup/redeem` (see `server.py:669-699`):
```python
# Takes a token, returns the actual credentials
{
    "token": "...",
    "client_id": "..."   # optional
}
# Returns:
{
    "username": "kevin",
    "password": "<the actual password>",
    "host": "...",
    "port": 8777,
    "board": "default"
}
```

The endpoint exists and works (per the v4 round's `advisor/003-qr-token` plan). The Android app just doesn't call it because it doesn't know about tokens.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Build Android debug APK | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` | `BUILD SUCCESSFUL` |
| Run Android unit tests | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` | `BUILD SUCCESSFUL` |
| Install on phone (if connected) | `./gradlew installDebug` | `BUILD SUCCESSFUL` |
| Check daemon health (after 001) | `curl -fsS http://127.0.0.1:8777/healthz` | `{"status":"ok"}` |

## Scope

**In scope**:
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/MainActivity.kt` — update `parseDeepLinkIntent` to also extract `token=`
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/QrScannerScreen.kt` — same update for the QR scanner path
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt` — the wizard flow that calls redeem
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/MainViewModel.kt` — add a `redeemSetupToken(token)` method that calls `POST /api/setup/redeem`
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/atlas/hermescompanion/data/ApiClient.kt` — add `redeemSetupToken(token: String): Result<RedeemResponse>` method

**Out of scope** (do NOT touch):
- The daemon's QR generation code — already correct (`setup_wizard.py:163-180`)
- The daemon's `/api/setup/redeem` endpoint — already correct (`server.py:669-699`)
- The daemon's QR token storage (`_SETUP_TOKENS`) — already correct
- The Android EncryptedSharedPreferences work from Plan 004 — orthogonal to this fix

## Git workflow

- Android repo branch: `advisor/002-qr-token-android`
- Commit per logical step
- Message style: imperative, scoped (`feat(deeplink): ...`, `feat(setup): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Update Android deep-link parser to extract `token=`

**Edit** `app/src/main/java/org/hermes/community/companion/MainActivity.kt`:

Add a `token` field to the `DeepLinkConfig` data class. Update `parseDeepLinkIntent` to extract it:

```kotlin
data class DeepLinkConfig(
    val host: String,
    val port: Int,
    val username: String,
    val password: String,   // kept for backward compat
    val token: String? = null,  // NEW: setup token, takes precedence
    val board: String,
)

fun parseDeepLinkIntent(intent: Intent): DeepLinkConfig? {
    val data = intent.data ?: return null
    if (data.scheme != "hermescompanion") return null
    val host = data.host ?: return null
    if (host != "configure") return null
    return DeepLinkConfig(
        host = data.getQueryParameter("host") ?: "",
        port = data.getQueryParameter("port")?.toIntOrNull() ?: 8777,
        username = data.getQueryParameter("user") ?: "",
        password = data.getQueryParameter("pass") ?: "",
        token = data.getQueryParameter("token"),  // NEW
        board = data.getQueryParameter("board") ?: "default",
    )
}
```

**Verify** (code compiles): `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug`

### Step 2: Update QrScannerScreen to extract `token=`

**Edit** `app/src/main/java/org/hermes/community/companion/QrScannerScreen.kt` similarly. Search for the line that extracts `pass=` and add `token=` extraction in the same place.

**Verify**: `./gradlew assembleDebug`

### Step 3: Add `redeemSetupToken()` to ApiClient

**Edit** `app/src/main/java/org/hermes/community/companion/data/ApiClient.kt`:

Add a new method that calls `POST /api/setup/redeem` and returns the credentials. The endpoint is **unauthenticated** (per `server.py:669-699`):

```kotlin
suspend fun redeemSetupToken(token: String): Result<RedeemResponse> = runCatching {
    val baseUrl = baseUrl.removeSuffix("/")
    val url = "$baseUrl/api/setup/redeem"
    val body = JSONObject().apply {
        put("token", token)
    }
    val request = Request.Builder()
        .url(url)
        .post(body.toString().toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).execute().use { response ->
        if (!response.isSuccessful) {
            throw ApiException("Setup redeem failed: ${response.code}")
        }
        val respBody = response.body?.string() ?: throw ApiException("Empty response")
        val json = JSONObject(respBody)
        RedeemResponse(
            username = json.optString("username"),
            password = json.optString("password"),
            host = json.optString("host", baseUrl),
            port = json.optInt("port", 8777),
            board = json.optString("board", "default"),
        )
    }
}

data class RedeemResponse(
    val username: String,
    val password: String,
    val host: String,
    val port: Int,
    val board: String,
)
```

**Verify**: `./gradlew assembleDebug`

### Step 4: Wire the redeem flow in MainViewModel

**Edit** `app/src/main/java/org/hermes/community/companion/MainViewModel.kt`:

Add a method that takes a token, calls `redeemSetupToken`, and on success populates the SessionManager with the returned credentials:

```kotlin
suspend fun redeemSetupToken(token: String): Result<Unit> {
    val response = apiClient.redeemSetupToken(token).getOrElse {
        return Result.failure(it)
    }
    sessionManager.setCredentials(
        url = "https://${response.host}:${response.port}",
        username = response.username,
        password = response.password,
        board = response.board,
    )
    return Result.success(Unit)
}
```

**Verify**: `./gradlew assembleDebug`

### Step 5: Update SetupWizardScreen to call redeem when token is present

**Edit** `app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt`:

When the deep-link parser returns a non-null `token`, the wizard should NOT show a "enter password" form. Instead, it should:
1. Call `viewModel.redeemSetupToken(token)` automatically
2. On success: mark setup complete, navigate to the chat screen
3. On failure: show error, allow manual entry fallback

**Pseudocode**:
```kotlin
LaunchedEffect(deepLink.token) {
    val token = deepLink.token
    if (token != null) {
        val result = viewModel.redeemSetupToken(token)
        if (result.isSuccess) {
            // Setup done, navigate to chat
        } else {
            // Show error, allow manual entry
        }
    }
}
```

**Verify**: `./gradlew assembleDebug` + `./gradlew test`

### Step 6: End-to-end test against the live daemon

After Plan 001 has brought the daemon up:

```bash
# 1. Generate a test setup token
cd /home/kevin/.hermes/companion
python3 -c "
from setup_wizard import generate_setup_token, register_setup_token_wizard
from config_schema import load_config
token = generate_setup_token()
config = load_config()
register_setup_token_wizard(token, 'kevin', 'Kevi667n!1991!', config)
print('Token:', token)
"
# (register_setup_token_wizard also writes setup_token.json which the daemon loads on next start)

# 2. Restart daemon so it picks up the token
systemctl --user restart hermes-companion.service

# 3. Test the redeem endpoint
curl -fsS -X POST http://127.0.0.1:8777/api/setup/redeem \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"<the token from step 1>\"}"
# Expected: JSON with username, password, host, port, board
```

If step 3 returns 200, the daemon side works. The Android-side verification is the `assembleDebug` succeeding + the new test passing.

## Test plan

**New test**: Add a unit test in `app/src/test/java/org/hermes/community/companion/MainActivityTest.kt`:

```kotlin
@Test
fun parseDeepLinkIntent_extractsToken() {
    val intent = mock<Intent> {
        on { data } doReturn Uri.parse("hermescompanion://configure?host=192.168.1.1&port=8777&user=kevin&token=abc123&board=default")
    }
    val config = parseDeepLinkIntent(intent)
    assertNotNull(config)
    assertEquals("abc123", config?.token)
}

@Test
fun parseDeepLinkIntent_keepsPasswordForBackcompat() {
    val intent = mock<Intent> {
        on { data } doReturn Uri.parse("hermescompanion://configure?host=192.168.1.1&port=8777&user=kevin&pass=plaintextpw&board=default")
    }
    val config = parseDeepLinkIntent(intent)
    assertNotNull(config)
    assertEquals("plaintextpw", config?.password)
    assertNull(config?.token)
}
```

**Verify**: `./gradlew test` → both new tests pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` exits 0
- [ ] `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` exits 0; new tests for token parsing pass
- [ ] `grep -n 'data.getQueryParameter("token")' app/src/main/java/org/hermes/community/companion/MainActivity.kt` returns at least one match
- [ ] `grep -n 'data.getQueryParameter("token")' app/src/main/java/org/hermes/community/companion/QrScannerScreen.kt` returns at least one match
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `git diff` shows only the new `token` field and the redeem flow
- [ ] End-to-end: with the daemon up (Plan 001), `curl -X POST .../api/setup/redeem` returns credentials; with the new APK installed, scanning a QR code with `token=` completes setup without prompting for a password

## STOP conditions

Stop and report back (do not improvise) if:
- The daemon's `/api/setup/redeem` endpoint rejects the new test token with 404 or 500 — that's a daemon-side bug not covered by this plan; report and stop
- The Android build fails for reasons unrelated to the in-scope files — that's a separate issue
- The new unit test passes but the actual `assembleDebug` produces a 17MB+ APK that crashes on launch — that's a Compose/Hilt issue, not a deep-link issue
- The user's phone isn't connected via ADB to verify the end-to-end test — the unit tests + daemon `curl` are the verification, no phone required

## Maintenance notes

- This plan fixes the **paired** nature of the QR token feature. Future changes to either side (daemon's QR format OR Android's deep-link parser) should update both. Add a comment in both files pointing at the other:
  - In `setup_wizard.py:generate_qr_code`: `# Paired with Android MainActivity.kt:parseDeepLinkIntent — keep in sync`
  - In `MainActivity.kt:parseDeepLinkIntent`: `# Paired with daemon setup_wizard.py:generate_qr_code — keep in sync`
- If a future feature adds `board=` overrides, the deep-link parser already extracts it (line 99). No change needed.
- The `password` field is kept for backward compatibility with the OLD QR format (pre-Plan-003) — the app may still encounter old QR codes during the transition period. New QR codes use `token=`.
