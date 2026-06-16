# Plan 005: Android cleartext traffic — add network_security_config.xml

> **Executor instructions**: Modifies Android manifest and adds a new XML resource. Run all verification.
>
> **Drift check**:
> ```bash
> cd /home/kevin/.hermes/projects/HermesCompanion
> git diff --stat 47041ea..HEAD -- app/src/main/AndroidManifest.xml
> ```

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001
- **Category**: security
- **Planned at**: commit `47041ea`, 2026-06-16

## Why this matters

`AndroidManifest.xml:22` declares `android:usesCleartextTraffic="true"` on the `<application>` element. This permits the app to send HTTP Basic Auth credentials over plaintext HTTP, which an attacker on the same network can intercept. Even with Cloudflare Tunnel (HTTPS), a misconfiguration or MITM can downgrade. There is no `network_security_config.xml` providing a safer default. Restricting cleartext to specific loopback/localhost domains (for emulator dev) while forcing HTTPS in production is the safe pattern.

## Current state

**File**: `app/src/main/AndroidManifest.xml`

Look for:
```xml
<application
    android:name=".TelosApplication"
    android:label="@string/app_name"
    android:icon="@mipmap/ic_launcher"
    android:usesCleartextTraffic="true"
    android:theme="@style/Theme.HermesCompanion">
```

**Repo conventions**:
- `app/src/main/res/xml/` directory for XML resources
- `network_security_config.xml` is the standard file name Android reads when `android:networkSecurityConfig` is set on `<application>`
- The app's production deployment is via Cloudflare Tunnel → `https://<subdomain>.kevlarscreations.com/...` (HTTPS terminated at edge)
- Local dev uses `http://127.0.0.1:8777` (cleartext)

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Build | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` | exit 0 |
| Test | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` | all pass |
| Verify config loads | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew :app:processDebugResources` | exit 0 |

## Scope

**In scope**:
- `app/src/main/AndroidManifest.xml` — replace `usesCleartextTraffic` with `networkSecurityConfig` reference
- `app/src/main/res/xml/network_security_config.xml` — create new file

**Out of scope**:
- Certificate pinning — separate plan, not in scope (would need to know the cert fingerprint)
- `MainViewModel.kt` — no code changes
- Build config / gradle — no changes

## Git workflow

- Branch: `advisor/005-network-security-config`
- Commit style: `fix(security):` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git diff --stat 47041ea..HEAD -- app/src/main/AndroidManifest.xml
```

If manifest changed, STOP and re-verify.

### Step 2: Create `network_security_config.xml`

Create `app/src/main/res/xml/network_security_config.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!-- Default: cleartext NOT permitted. Force HTTPS for all production traffic. -->
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>

    <!-- Local development: allow cleartext only for loopback and emulator-host. -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">localhost</domain>
        <domain includeSubdomains="true">127.0.0.1</domain>
        <domain includeSubdomains="true">10.0.2.2</domain> <!-- Android emulator host loopback -->
        <domain includeSubdomains="false">192.168.0.0</domain> <!-- LAN — restrict scope -->
    </domain-config>
</network-security-config>
```

Wait — `192.168.0.0` as a domain is wrong; that's a CIDR, not a domain. The `<domain>` element matches hostnames, not IP ranges. For LAN IPs, list individual IPs or use a wildcard. Better to drop the 192.168 line entirely; users with LAN deployments can configure manually.

Revised:
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <!-- Default: HTTPS only. -->
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>

    <!-- Local development: cleartext only for loopback/emulator. -->
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">localhost</domain>
        <domain includeSubdomains="true">127.0.0.1</domain>
        <domain includeSubdomains="true">10.0.2.2</domain>
    </domain-config>
</network-security-config>
```

**Verify**: file exists at the expected path
```bash
ls -la /home/kevin/.hermes/projects/HermesCompanion/app/src/main/res/xml/network_security_config.xml
```

### Step 3: Update `AndroidManifest.xml`

Read the current manifest:
```bash
cat /home/kevin/.hermes/projects/HermesCompanion/app/src/main/AndroidManifest.xml
```

Find the `<application>` element. Change:
- `android:usesCleartextTraffic="true"` → REMOVE
- Add: `android:networkSecurityConfig="@xml/network_security_config"`

**Verify**: `grep "networkSecurityConfig" AndroidManifest.xml` shows the new attribute.

### Step 4: Build and test

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
./gradlew assembleDebug
./gradlew test
```

If `assembleDebug` fails because Android doesn't recognize the resource, verify the file is at the right path and has the right root element.

### Step 5: Verify behavior

To manually verify, you can use `aapt dump xmltree`:
```bash
cd /home/kevin/.hermes/projects/HermesCompanion
$ANDROID_HOME/build-tools/34.0.0/aapt dump xmltree app/build/outputs/apk/debug/app-debug.apk AndroidManifest.xml 2>&1 | grep -i "network\|cleartext"
```

This is optional. The build success is the main check.

### Step 6: Commit

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git add -A
git status
git commit -m "$(cat <<'EOF'
fix(security): restrict cleartext traffic to loopback only via network_security_config

The AndroidManifest had android:usesCleartextTraffic="true", which permitted
HTTP Basic Auth credentials to be sent over plaintext HTTP. Even with
Cloudflare Tunnel, this is a downgrade risk.

Changes:
- Add res/xml/network_security_config.xml
- base-config: cleartextTrafficPermitted=false (HTTPS required by default)
- domain-config exception: cleartext allowed only for localhost, 127.0.0.1,
  and 10.0.2.2 (emulator host loopback)
- AndroidManifest: remove usesCleartextTraffic, add networkSecurityConfig
  reference to the new XML

Cloudflare-proxied production (https://<subdomain>.kevlarscreations.com)
continues to work. Local emulator dev (http://10.0.2.2:8777) continues
to work. Other cleartext is now blocked.
EOF
)"
```

## Test plan

No new unit tests required. Manual verification:
- Build a debug APK
- Install on emulator with `http://10.0.2.2:8777` — should work
- Try installing and pointing at `http://example.com:8777` — should fail with `CLEARTEXT communication not permitted`
- Production install with `https://<subdomain>.kevlarscreations.com` — should work

## Done criteria

- [ ] `./gradlew assembleDebug` exits 0
- [ ] `./gradlew test` exits 0
- [ ] `grep "usesCleartextTraffic" app/src/main/AndroidManifest.xml` shows no match
- [ ] `grep "networkSecurityConfig" app/src/main/AndroidManifest.xml` shows 1 match
- [ ] `test -f app/src/main/res/xml/network_security_config.xml` returns 0
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 005 row updated to `DONE`

## STOP conditions

- Plan 001 not DONE — STOP (this plan is Android-side, so 001 is required for the daemon, not directly for this plan, but the ASDD pattern requires sequential execution).
- Manifest drift — STOP.
- Build fails — STOP, surface the error.

## Maintenance notes

- When adding a new production domain (e.g., a new tunnel), add it to the `<domain-config>` block as a `<domain>` element if it should be HTTP-only. For HTTPS, no change needed (default config applies).
- The `10.0.2.2` address is the Android emulator's gateway to the host's loopback. Real devices cannot use it; they need a routable IP (which is a real-network IP, not loopback).
- For real devices on LAN pointing at `http://<lan-ip>:8777`, users will need to manually add that IP to the network_security_config. Or use Cloudflare Tunnel + HTTPS.
- Certificate pinning: for v2, add `<pin-set>` to the production `<base-config>` to pin Cloudflare's intermediate certs. Out of scope here.
