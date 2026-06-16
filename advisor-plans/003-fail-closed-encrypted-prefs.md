# Plan 003: Fail closed on EncryptedSharedPreferences — no silent plaintext fallback

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/projects/HermesCompanion && git diff --stat 0a30d82..HEAD -- app/src/main/java/org/hermes/community/companion/data/SessionManager.kt`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P0
- **Effort**: M
- **Risk**: MED (could break setup on devices where Keystore is unavailable; mitigated by the explicit user consent step)
- **Depends on**: none
- **Category**: bug (security)
- **Planned at**: commit `0a30d82` (Android), 2026-06-16

## Why this matters

`SessionManager.kt:30-38` has a `try/catch` that falls back to **plaintext** `SharedPreferences` ("hermes_settings_fallback") if `EncryptedSharedPreferences.create()` throws. This is a security bug: credentials (password, token) are stored in plaintext on affected devices, with only a `Log.w` warning — no user notification, no audit log, no opt-in.

The user's explicit guidance (per memory): "if a missing library is critical to the app functioning as intended it should be installed, not worked around." Same principle applies to encryption: if the Android Keystore is unavailable, the app should fail closed and tell the user, not silently downgrade to plaintext.

The current code is silently insecure on:
- Emulators without Keystore hardware backing
- Test environments (CI, instrumentation tests)
- Devices with corrupted keystore
- Work-profile-restricted devices

## Current state (verified 2026-06-16 by Atlas)

**File**: `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/data/SessionManager.kt:30-38`

```kotlin
private val prefs: SharedPreferences by lazy {
    try {
        SessionMigration.encryptedPrefs(context.applicationContext)
    } catch (e: Exception) {
        // Fallback for test environments where Android Keystore is unavailable
        Log.w("SessionManager", "EncryptedSharedPreferences not available, falling back", e)
        context.getSharedPreferences("hermes_settings_fallback", Context.MODE_PRIVATE)
    }
}
```

**Why this is wrong**:
- `Log.w` doesn't surface to the user. The user has no idea their password is stored in plaintext.
- The fallback file name "hermes_settings_fallback" is a clear indicator it was the developer's "I'll get back to this" path.
- No telemetry, no opt-in, no audit log.

**Reproduction**:
1. Run the app in an Android emulator (most AVDs have limited Keystore support)
2. Try to save credentials
3. `adb shell run-as com.example.hermescompanion cat /data/data/com.example.hermescompanion/shared_prefs/hermes_settings_fallback.xml`
4. Observe plaintext password in the XML file

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Build | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` | `BUILD SUCCESSFUL` |
| Run unit tests | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` | `BUILD SUCCESSFUL` |
| Run instrumentation tests on a real device | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew connectedAndroidTest` | `BUILD SUCCESSFUL` |

## Scope

**In scope**:
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` — the `prefs` lazy initializer + add a `getStorageMode()` method that exposes whether the storage is encrypted
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt` — display a clear warning during setup if Keystore unavailable, require explicit user consent to use plaintext
- `~/.hermes/projects/HermesCompanion/app/src/main/java/org/hermes/community/companion/SettingsScreen.kt` — show current storage mode in the Settings screen
- `app/src/test/java/org/hermes/community/companion/data/SessionManagerTest.kt` — add a new test for the fail-closed behavior

**Out of scope** (do NOT touch):
- The actual `SessionMigration.encryptedPrefs()` implementation — that works correctly when Keystore is available
- The `companion_cli.py` Python side — this is an Android-only concern
- The Hermes API auth (server.py:BasicAuth) — orthogonal

## Git workflow

- Android repo branch: `advisor/003-fail-closed-encrypted-prefs`
- Commit per logical step
- Message style: imperative, scoped (`fix(security): ...`, `feat(settings): ...`)
- Do NOT push or open a PR unless the operator instructed it

## Steps

### Step 1: Replace the silent fallback with a visible state

**Edit** `app/src/main/java/org/hermes/community/companion/data/SessionManager.kt`:

Replace the `prefs` lazy initializer with a version that captures the failure state and exposes it:

```kotlin
sealed class StorageMode {
    object Encrypted : StorageMode()
    data class Plaintext(val reason: String) : StorageMode()
    data class Unavailable(val reason: String) : StorageMode()
}

private val storageMode: StorageMode by lazy {
    try {
        SessionMigration.encryptedPrefs(context.applicationContext)
        StorageMode.Encrypted
    } catch (e: Exception) {
        Log.e("SessionManager", "EncryptedSharedPreferences unavailable, security degraded", e)
        StorageMode.Plaintext(reason = e.message ?: "Unknown error")
    }
}

private val prefs: SharedPreferences by lazy {
    when (val mode = storageMode) {
        is StorageMode.Encrypted -> SessionMigration.encryptedPrefs(context.applicationContext)
        is StorageMode.Plaintext -> context.getSharedPreferences("hermes_settings_fallback", Context.MODE_PRIVATE)
        else -> error("unreachable")
    }
}

fun getStorageMode(): StorageMode = storageMode
```

Add a public function so the UI layer can query the storage mode.

**Verify**: `./gradlew assembleDebug`

### Step 2: Surface the storage mode in the Settings screen

**Edit** `app/src/main/java/org/hermes/community/companion/SettingsScreen.kt`:

Add a section at the top of the settings that shows the current storage mode:

```kotlin
val storageMode = sessionManager.getStorageMode()
when (storageMode) {
    is StorageMode.Encrypted -> {
        // No display — silent
    }
    is StorageMode.Plaintext -> {
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        ) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("⚠️ Credentials stored in PLAINTEXT", style = MaterialTheme.typography.titleMedium)
                Spacer(modifier = Modifier.height(8.dp))
                Text("Android Keystore is unavailable. Your password and token are stored without encryption.")
                Text("Reason: ${storageMode.reason}")
                Spacer(modifier = Modifier.height(8.dp))
                Text("This usually happens on emulators or devices with restricted Keystore access. On a real device, encryption should work automatically.")
            }
        }
    }
    is StorageMode.Unavailable -> {
        // similar
    }
}
```

**Verify**: `./gradlew assembleDebug` + visually inspect the screen on a phone (manual)

### Step 3: Block setup wizard completion if Keystore unavailable + no consent

**Edit** `app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt`:

Add an explicit consent dialog when the storage mode is `Plaintext`. The user must acknowledge the security risk before completing setup:

```kotlin
val storageMode = remember { sessionManager.getStorageMode() }
var acknowledgedInsecureStorage by remember { mutableStateOf(false) }

if (storageMode is StorageMode.Plaintext && !acknowledgedInsecureStorage) {
    AlertDialog(
        onDismissRequest = { /* do nothing — must explicitly choose */ },
        title = { Text("⚠️ Insecure storage detected") },
        text = {
            Text("""
                Your device's Android Keystore is unavailable. Credentials will be stored in plaintext.

                This is a security risk. Anyone with access to your device can read your password.

                Recommended actions:
                1. Use a real device instead of an emulator
                2. Or accept the risk and continue (NOT RECOMMENDED)

                Reason: ${storageMode.reason}
            """.trimIndent())
        },
        confirmButton = {
            TextButton(onClick = { acknowledgedInsecureStorage = true }) {
                Text("I understand, continue")
            }
        },
        dismissButton = {
            TextButton(onClick = { /* exit setup */ }) {
                Text("Cancel")
            }
        },
    )
    return  // block rest of wizard until acknowledged
}
```

**Verify**: `./gradlew assembleDebug` + manually run on an emulator (if available)

### Step 4: Add unit test for fail-closed behavior

**Create** `app/src/test/java/org/hermes/community/companion/data/SessionManagerTest.kt`:

```kotlin
@Test
fun getStorageMode_returnsPlaintext_whenKeystoreUnavailable() {
    // Mock context to throw on encryptedPrefs()
    val mockContext = mock<Context> {
        on { applicationContext } doReturn mock<Context> {
            on { getSharedPreferences("hermes_settings_fallback", Context.MODE_PRIVATE) } doReturn mock<SharedPreferences>()
        }
    }
    val sessionManager = SessionManager(mockContext)
    // First call triggers lazy init — exercise the catch path
    val mode = sessionManager.getStorageMode()
    // Should NOT be Encrypted; should be Plaintext or Unavailable
    assertTrue(mode is StorageMode.Plaintext || mode is StorageMode.Unavailable)
}
```

**Verify**: `./gradlew test` → the new test passes.

### Step 5: Log the storage mode for diagnostics

**Edit** `app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` — at app startup (in `init` block or first `getStorageMode()` call), log the mode at INFO level (not WARN, since this is expected on emulators):

```kotlin
init {
    when (val mode = storageMode) {
        is StorageMode.Encrypted -> Log.i("SessionManager", "Storage mode: Encrypted (Android Keystore)")
        is StorageMode.Plaintext -> Log.w("SessionManager", "Storage mode: Plaintext (Keystore unavailable: ${mode.reason})")
        is StorageMode.Unavailable -> Log.e("SessionManager", "Storage mode: Unavailable (${mode.reason})")
    }
}
```

**Verify**: `./gradlew test`

## Test plan

- New test in `app/src/test/java/org/hermes/community/companion/data/SessionManagerTest.kt` for the storage mode detection
- Manual test on a real device: install APK, observe no warning in Settings
- Manual test on an emulator (if available): install APK, observe warning in Settings + setup wizard consent dialog
- All existing tests in `app/src/test/java/` should still pass

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` exits 0
- [ ] `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` exits 0; new SessionManager test passes
- [ ] `grep -n "Log.e.*EncryptedSharedPreferences unavailable" app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` returns a match
- [ ] `grep -n "Insecure storage detected" app/src/main/java/org/hermes/community/companion/SetupWizardScreen.kt` returns a match
- [ ] `grep -n "PLAINTEXT" app/src/main/java/org/hermes/community/companion/SettingsScreen.kt` returns a match
- [ ] No files outside the in-scope list are modified
- [ ] `git status` is clean (commit the changes)

## STOP conditions

Stop and report back (do not improvise) if:
- The existing app's setup flow breaks on a real device (encrypted mode must still work — that's the primary path)
- The unit test for `getStorageMode` requires mocking that's more complex than the existing tests in the repo — escalate to the orchestrator
- The new consent dialog blocks setup on a real device (the dialog should only show on plaintext mode, not encrypted)

## Maintenance notes

- The plaintext fallback is **kept** in this plan (not removed) because the alternative — refusing to run on emulators — would break the test environment entirely. Instead, we surface the risk to the user.
- Future improvement: add a "Re-check encryption" button in Settings that re-initializes the storage mode after the user changes their Keystore settings.
- If a future Android version makes EncryptedSharedPreferences strictly required (no fallback possible), this code path becomes dead code. Acceptable — defensive programming.
