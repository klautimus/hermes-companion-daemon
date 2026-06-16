# Plan 004: Android credentials — migrate DataStore → EncryptedSharedPreferences

> **Executor instructions**: This plan modifies the Android app. The Android repo is at `/home/kevin/.hermes/projects/HermesCompanion/`, separate from the daemon. Run all verification commands.
>
> **Drift check (run first)**:
> ```bash
> cd /home/kevin/.hermes/projects/HermesCompanion
> git diff --stat 47041ea..HEAD -- app/src/main/java/org/hermes/community/companion/data/SessionManager.kt app/build.gradle
> ```

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 001, 002
- **Category**: security
- **Planned at**: commit `47041ea`, 2026-06-16

## Why this matters

`SessionManager.kt:10-34` stores the Companion server URL, username, password, and board slug in plaintext DataStore (`preferencesDataStore("hermes_settings")`). On a rooted device, via `adb backup`, or via filesystem extraction, these credentials are readable. The `androidx.security:security-crypto:1.0.0` dependency is declared in `app/build.gradle:57` but never used — it's a dead import. This is a cleartext-storage-of-sensitive-information finding (CWE-312). Migrating to `EncryptedSharedPreferences` with a `MasterKey` from `androidx.security.crypto` keeps the existing `SessionManager` API stable while protecting credentials at rest with AES-256 GCM under a key in the Android Keystore.

## Current state

**File**: `app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` (77 LOC)

```kotlin
package org.hermes.community.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "hermes_settings")

object SessionManager {
    private val KEY_BASE_URL = stringPreferencesKey("base_url")
    private val KEY_USERNAME = stringPreferencesKey("username")
    private val KEY_PASSWORD = stringPreferencesKey("password")
    private val KEY_BOARD = stringPreferencesKey("board")

    // ... getters/setters all use dataStore.edit { it[KEY_*] = value }
}
```

**File**: `app/build.gradle:54-58`
```gradle
implementation 'androidx.datastore:datastore-preferences:1.0.0'
implementation 'androidx.security:security-crypto:1.0.0'
```

**File**: `app/src/main/AndroidManifest.xml` — verify `MainActivity` is exported and uses the application context.

**Consumers of `SessionManager`**:
- `MainViewModel.kt` — read URL/username/password for API client init
- `SettingsScreen.kt` — write credentials
- `ChatScreen.kt`, `KanbanScreen.kt` — read board slug

**Repo conventions** (from recon):
- Compose UI in `app/src/main/java/.../`
- DataStore singleton via `Context.dataStore` extension
- `viewModelScope.launch { dataStore.edit { ... } }` for writes
- Tests use `runTest` from `kotlinx-coroutines-test`
- Module: `app/src/test/java/org/hermes/community/companion/data/`

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Build debug APK | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` | exit 0 |
| Run unit tests | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` | all pass |
| Lint | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew lint` | exit 0 |
| New test | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test --tests "org.hermes.community.companion.data.SessionManagerMigrationTest"` | new test passes |

## Scope

**In scope**:
- `app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` — switch from DataStore to EncryptedSharedPreferences
- `app/src/main/java/org/hermes/community/companion/data/SessionMigration.kt` — create new file, handles one-time migration from old DataStore
- `app/src/test/java/org/hermes/community/companion/data/SessionManagerTest.kt` — create or update
- `app/build.gradle` — verify security-crypto is at 1.1.0-alpha06 or later (1.0.0 is dated); bump if needed

**Out of scope**:
- `MainViewModel.kt` — its API is unchanged, no edits needed
- `SettingsScreen.kt` — no edits needed (uses SessionManager API)
- `ApiClient.kt` — receives credentials from SessionManager, no edits
- `AndroidManifest.xml` — no permission changes
- Daemon side credential rotation — separate plan; user should rotate Companion password after migration

## Git workflow

- Branch: `advisor/004-encrypted-shared-prefs`
- Commit style: `fix(security):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git diff --stat 47041ea..HEAD -- app/src/main/java/org/hermes/community/companion/data/SessionManager.kt app/build.gradle
```

If `SessionManager.kt` changed, STOP and re-verify.

### Step 2: Verify security-crypto version

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
grep "security-crypto" app/build.gradle
```

1.0.0 is fine but 1.1.0-alpha06 has bug fixes. Bump to 1.1.0-alpha06 if Gradle resolves cleanly. Otherwise stay on 1.0.0.

### Step 3: Write new `SessionMigration.kt`

Create `app/src/main/java/org/hermes/community/companion/data/SessionMigration.kt`:

```kotlin
package org.hermes.community.companion.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.datastore.preferences.core.edit
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Migrate credentials from plaintext DataStore to EncryptedSharedPreferences.
 *
 * Idempotent: only runs if the legacy datastore has values and encrypted store is empty.
 * After successful migration, the legacy datastore file is cleared.
 */
object SessionMigration {
    private const val TAG = "SessionMigration"
    private const val PREFS_NAME = "hermes_settings_secure"
    private const val MIGRATION_FLAG = "migration_complete_v1"

    suspend fun migrateIfNeeded(context: Context) {
        val securePrefs = encryptedPrefs(context)
        if (securePrefs.getBoolean(MIGRATION_FLAG, false)) return

        val appContext = context.applicationContext
        val legacy = appContext.dataStore
        val legacyPrefs = legacy.data.first()

        val url = legacyPrefs[SessionManager.LEGACY_KEY_BASE_URL] ?: return  // nothing to migrate
        val username = legacyPrefs[SessionManager.LEGACY_KEY_USERNAME] ?: ""
        val password = legacyPrefs[SessionManager.LEGACY_KEY_PASSWORD] ?: ""
        val board = legacyPrefs[SessionManager.LEGACY_KEY_BOARD] ?: "default"

        with(securePrefs.edit()) {
            putString("base_url", url)
            putString("username", username)
            putString("password", password)
            putString("board", board)
            putBoolean(MIGRATION_FLAG, true)
            apply()
        }

        // Clear legacy
        legacy.edit { it.clear() }
        Log.i(TAG, "Migrated credentials from DataStore to EncryptedSharedPreferences")
    }

    fun encryptedPrefs(context: Context): SharedPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            context,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }
}
```

**Note**: This requires `SessionManager.LEGACY_KEY_*` to be exposed (currently `private`). Step 4 handles that.

### Step 4: Update `SessionManager.kt`

Restructure to use EncryptedSharedPreferences. Expose LEGACY_KEY_* for the migration.

```kotlin
package org.hermes.community.companion.data

import android.content.Context
import android.content.SharedPreferences
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

internal val Context.legacyDataStore by preferencesDataStore(name = "hermes_settings")

object SessionManager {
    // Legacy keys — used only for one-time migration
    val LEGACY_KEY_BASE_URL: Preferences.Key<String> = stringPreferencesKey("base_url")
    val LEGACY_KEY_USERNAME: Preferences.Key<String> = stringPreferencesKey("username")
    val LEGACY_KEY_PASSWORD: Preferences.Key<String> = stringPreferencesKey("password")
    val LEGACY_KEY_BOARD: Preferences.Key<String> = stringPreferencesKey("board")

    private const val PREFS_NAME = "hermes_settings_secure"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_USERNAME = "username"
    private const val KEY_PASSWORD = "password"
    private const val KEY_BOARD = "board"

    private var prefs: SharedPreferences? = null
    private val _baseUrl = MutableStateFlow("")
    private val _username = MutableStateFlow("")
    private val _password = MutableStateFlow("")
    private val _board = MutableStateFlow("default")

    fun init(context: Context) {
        if (prefs != null) return
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        prefs = EncryptedSharedPreferences.create(
            context,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
        _baseUrl.value = prefs!!.getString(KEY_BASE_URL, "") ?: ""
        _username.value = prefs!!.getString(KEY_USERNAME, "") ?: ""
        _password.value = prefs!!.getString(KEY_PASSWORD, "") ?: ""
        _board.value = prefs!!.getString(KEY_BOARD, "default") ?: "default"
    }

    val baseUrl: Flow<String> = _baseUrl.asStateFlow()
    val username: Flow<String> = _username.asStateFlow()
    val password: Flow<String> = _password.asStateFlow()
    val board: Flow<String> = _board.asStateFlow()

    fun save(context: Context, baseUrl: String, username: String, password: String, board: String) {
        init(context)
        prefs!!.edit().putString(KEY_BASE_URL, baseUrl)
            .putString(KEY_USERNAME, username)
            .putString(KEY_PASSWORD, password)
            .putString(KEY_BOARD, board)
            .apply()
        _baseUrl.value = baseUrl
        _username.value = username
        _password.value = password
        _board.value = board
    }

    fun clear(context: Context) {
        init(context)
        prefs!!.edit().clear().apply()
        _baseUrl.value = ""
        _username.value = ""
        _password.value = ""
        _board.value = "default"
    }
}
```

**API change**: `save()` now takes `Context`. Update `SettingsScreen.kt` and any other callers.

### Step 5: Call `SessionMigration.migrateIfNeeded(context)` in `MainActivity.onCreate()`

Add at the top of `MainActivity.onCreate()` (after `super.onCreate`):

```kotlin
lifecycleScope.launch {
    SessionMigration.migrateIfNeeded(applicationContext)
    SessionManager.init(applicationContext)
}
```

This requires `androidx.lifecycle:lifecycle-runtime-ktx` (already a dep).

### Step 6: Update `MainViewModel.kt` to call `SessionManager.init(context)`

Wherever `MainViewModel` reads from `SessionManager`, ensure `init()` has been called. Either:
- Eager: `MainViewModel.init` calls `SessionManager.init(getApplication())`
- Lazy: `SessionManager.baseUrl.collect { ... }` triggers init on first read

Eager is simpler. Add to `MainViewModel.init` (the constructor block or `init {}` block):
```kotlin
SessionManager.init(getApplication<Application>())
```

### Step 7: Update `SettingsScreen.kt` to pass `context` to `SessionManager.save`

In the Save button onClick:
```kotlin
SessionManager.save(context, baseUrl, username, password, board)
```

`context` is already available via `LocalContext.current` or as a Composable parameter.

### Step 8: Add unit test

Create `app/src/test/java/org/hermes/community/companion/data/SessionManagerMigrationTest.kt`:

```kotlin
package org.hermes.community.companion.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.Assert.*

@RunWith(AndroidJUnit4::class)
class SessionManagerMigrationTest {
    private lateinit var context: Context

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
    }

    @Test
    fun testEncryptedPrefsStoresValues() = runBlocking {
        SessionManager.init(context)
        SessionManager.save(context, "https://example.com", "alice", "secret", "default")
        // Read directly from underlying prefs (which is EncryptedSharedPreferences)
        // and verify it's NOT plaintext
        val prefs = SessionMigration.encryptedPrefs(context)
        // EncryptedSharedPreferences returns decrypted values via getString;
        // verifying the value is round-tripped correctly:
        assertEquals("https://example.com", prefs.getString("base_url", null))
    }
}
```

### Step 9: Build and run all tests

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
./gradlew assembleDebug
./gradlew test
./gradlew lint
```

### Step 10: Commit

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(security): migrate credentials from plaintext DataStore to EncryptedSharedPreferences

The previous SessionManager stored URL/username/password/board in plaintext
DataStore (preferencesDataStore name 'hermes_settings'). On rooted devices,
adb backup, or filesystem extraction, credentials were readable.

The androidx.security:security-crypto:1.0.0 dep was declared but never used.

Changes:
- SessionManager now uses EncryptedSharedPreferences with AES256_SIV key
  encryption and AES256_GCM value encryption, master key in Android Keystore
- SessionMigration.migrateIfNeeded runs on app startup, copies legacy DataStore
  values into encrypted prefs, then clears the legacy file
- Legacy keys exposed as LEGACY_KEY_* (internal) for migration use only
- SessionManager.save() now takes Context (one-time API change)
- MainActivity.onCreate triggers migration before first SessionManager access
- SettingsScreen updated to pass context to save()

Users should rotate their Companion password after this update.
EOF
)"
```

## Test plan

- New `SessionManagerMigrationTest.kt` — verifies encrypted prefs store and retrieve values
- Existing tests should still pass (SessionManager API is mostly compatible; `save()` signature change requires call-site update)
- Verification: `./gradlew test` — all tests pass

## Done criteria

- [ ] `./gradlew assembleDebug` exits 0
- [ ] `./gradlew test` exits 0
- [ ] `grep "preferencesDataStore" app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` shows only the legacy `legacyDataStore` line, not active storage
- [ ] `grep "EncryptedSharedPreferences" app/src/main/java/org/hermes/community/companion/data/SessionManager.kt` shows 1+ matches
- [ ] `grep "SessionMigration" app/src/main/java/org/hermes/community/companion/MainActivity.kt` shows 1+ matches
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 004 row updated to `DONE`

## STOP conditions

- Plans 001 or 002 not DONE — STOP.
- Drift check shows SessionManager changed — STOP.
- `./gradlew assembleDebug` fails — STOP, surface the build error.
- EncryptedSharedPreferences.create() throws on init (KeyStore corruption, etc.) — STOP, the device may be incompatible; design fallback.

## Maintenance notes

- MasterKey AES256_GCM requires Android 6.0+ (API 23+). App is minSdk 26, so safe.
- If the user clears app data, EncryptedSharedPreferences is wiped. Migration runs again on next launch, finds empty legacy, no-op.
- The Keystore key is device-bound. After a backup-restore to a new device, the encrypted prefs are unreadable. User must re-enter credentials. This is correct behavior.
- For v2: consider using BiometricPrompt to gate access to the password field (extra protection against device-shoulder-surfing).
- Rotation: when this lands, force a password rotation on the server side. The `auth.json` plaintext password was previously recoverable from device, so it's compromised.
