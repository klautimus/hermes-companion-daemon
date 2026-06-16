# Plan 011: Add Android release signing + data extraction rules

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in the "STOP conditions" section occurs, stop and report — do not improvise.
>
> **Drift check (run first)**: `cd /home/kevin/.hermes/projects/HermesCompanion && git diff --stat 0a30d82..HEAD -- app/`
> If any in-scope file changed since this plan was written, treat the drift as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (changes the release artifact; can't be undone if wrong keys used)
- **Depends on**: none
- **Category**: bug (deployment)
- **Planned at**: commit `0a30d82`, 2026-06-16

## Why this matters

The Android app:
- Builds debug APK (39MB) — works for development
- Has no `signingConfig` for release — release build can't be installed
- Missing `data_extraction_rules.xml` + `backup_rules.xml` — Android 12+ expects these for backup/data transfer control

**The user has a debug APK in their hand. They need a release APK to test distribution.**

## Steps

### Step 1: Generate a release keystore (LOCAL ONLY — not committed)

The keystore must NOT be committed to the repo. Use a gitignored path:

```bash
mkdir -p ~/.android/keystores
keytool -genkey -v \
  -keystore ~/.android/keystores/hermes-companion-release.jks \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -alias hermes-companion \
  -storepass <generate-and-store-securely> \
  -keypass <generate-and-store-securely> \
  -dname "CN=Kevin,O=Hermes,C=CA"
```

**Stop and ask the user** for the keystore passwords. Do NOT hardcode in the build.gradle.kts.

### Step 2: Add signing config to build.gradle.kts

**Edit** `~/.hermes/projects/HermesCompanion/app/build.gradle.kts`:

Add (at the top of the file, before `android {`):
```kotlin
import java.util.Properties
import java.io.FileInputStream

// Load signing config from gitignored file
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("keystore.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}
```

Inside `android {`, add:
```kotlin
signingConfigs {
    create("release") {
        if (keystorePropertiesFile.exists()) {
            storeFile = file(keystoreProperties["storeFile"] as String)
            storePassword = keystoreProperties["storePassword"] as String
            keyAlias = keystoreProperties["keyAlias"] as String
            keyPassword = keystoreProperties["keyPassword"] as String
        }
    }
}

buildTypes {
    getByName("release") {
        isMinifyEnabled = true
        proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        signingConfig = signingConfigs.getByName("release")
    }
}
```

### Step 3: Create keystore.properties (LOCAL ONLY)

**Create** `~/.hermes/projects/HermesCompanion/keystore.properties`:
```properties
storeFile=/home/kevin/.android/keystores/hermes-companion-release.jks
storePassword=<from step 1>
keyAlias=hermes-companion
keyPassword=<from step 1>
```

Add to `.gitignore`:
```
keystore.properties
```

### Step 4: Create data extraction rules

**Create** `~/.hermes/projects/HermesCompanion/app/src/main/res/xml/data_extraction_rules.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<data-extraction-rules>
    <cloud-backup>
        <exclude domain="sharedpref" path="hermes_secure_prefs.xml"/>
        <exclude domain="sharedpref" path="hermes_settings_fallback.xml"/>
    </cloud-backup>
    <device-transfer>
        <exclude domain="sharedpref" path="hermes_secure_prefs.xml"/>
        <exclude domain="sharedpref" path="hermes_settings_fallback.xml"/>
    </device-transfer>
</data-extraction-rules>
```

**Create** `~/.hermes/projects/HermesCompanion/app/src/main/res/xml/backup_rules.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<full-backup-content>
    <exclude domain="sharedpref" path="hermes_secure_prefs.xml"/>
    <exclude domain="sharedpref" path="hermes_settings_fallback.xml"/>
</full-backup-content>
```

### Step 5: Reference in AndroidManifest.xml

**Edit** `~/.hermes/projects/HermesCompanion/app/src/main/AndroidManifest.xml`:

Find the `<application>` tag and add:
```xml
<application
    android:dataExtractionRules="@xml/data_extraction_rules"
    android:fullBackupContent="@xml/backup_rules"
    ... (existing attributes)
>
```

### Step 6: Build the release APK

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
./gradlew assembleRelease
```

**Expected**: `app/build/outputs/apk/release/app-release.apk` produced, signed with the release key.

**Verify**:
```bash
ls -la app/build/outputs/apk/release/
# Expected: app-release.apk, ~5-10MB (much smaller than debug due to proguard)
jarsigner -verify app/build/outputs/apk/release/app-release.apk
# Expected: "jar verified"
```

### Step 7: Copy APK to a discoverable location

```bash
cp app/build/outputs/apk/release/app-release.apk /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk
ls -la /home/kevin/Desktop/HermesCompanion-v1.0.0-release.apk
```

## Done criteria

- [ ] `./gradlew assembleRelease` exits 0
- [ ] `app/build/outputs/apk/release/app-release.apk` exists and is <15MB
- [ ] `jarsigner -verify` succeeds
- [ ] `keystore.properties` is in .gitignore
- [ ] `~/.android/keystores/hermes-companion-release.jks` is NOT in the repo
- [ ] `data_extraction_rules.xml` and `backup_rules.xml` exist and exclude credential prefs
- [ ] `git status` is clean (commit the gradle and manifest changes; do NOT commit keystore.properties or .jks)

## STOP conditions

- The user does not have `keytool` available — install OpenJDK
- The release build fails on ProGuard rules — the existing proguard-rules.pro may need updates for the new dependencies
- The signed APK is rejected by the device — key mismatch, need to uninstall the debug APK first
