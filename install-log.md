# CodePilotAI — Android install log

Session: install the app on a physical phone over USB (Windows, PowerShell).
Result: **SUCCESS** — app installed and launch-ready.

---

## 1. Environment checks

| Check | Result | Status |
|---|---|---|
| `adb devices -l` | `R9ZY50VZR9A  device product:m16xmins model:SM_M166P transport_id:1` | ✅ |
| `java -version` | `21.0.12.1 LTS` (project targets Java 17; JDK 21 compiles to 17) | ✅ |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | not set | ⚠️ → fixed via `local.properties` |
| SDK location | `C:\Users\user\AppData\Local\Android\Sdk` | ✅ |
| SDK platforms | `android-35`, `android-37.0` (needs `android-36`) | ⚠️ → auto-downloaded by AGP |
| SDK build-tools | `35.0.0`, `36.0.0` | ✅ |
| SDK license | `android-sdk-license` accepted | ✅ |
| `android/local.properties` | missing | ⚠️ → created |
| `cmdline-tools` / `sdkmanager` | not installed | ℹ️ (not needed; AGP auto-downloaded) |
| Gradle wrapper | `gradle-8.14.3-bin.zip` | ✅ |
| AGP version | `8.13.0` (libs.versions.toml) | ✅ |
| PC LAN IP | `10.122.202.215` (Wi-Fi 2) | (not used, see §3) |
| Backend `.env` | **missing** | ⚠️ → sign-in needs it, see §6 |
| Backend `.venv` | **missing** | ⚠️ → sign-in needs it, see §6 |
| Backend listening :8000 | **no** | ⚠️ → sign-in needs it, see §6 |

## 2. Fixes applied

1. **Created `android/local.properties`** (git-ignored) so Gradle can find the SDK:
   ```
   sdk.dir=C\:\\Users\\user\\AppData\\Local\\Android\\Sdk
   ```
2. **Installed missing platform `android-36`** — the build needs `compileSdk = 36`
   (app/build.gradle.kts). AGP downloaded it automatically because the SDK license
   was already accepted.

## 3. Networking decision (physical device vs. emulator)

- Default `BuildConfig.API_BASE_URL` is `http://10.0.2.2:8000/api/v1` — emulator-only,
  will NOT work on a physical phone.
- The app's `res/xml/network_security_config.xml` permits cleartext ONLY for
  `10.0.2.2`, `localhost`, `127.0.0.1` (everything else is cleartext-blocked).
- Chosen approach: **`adb reverse tcp:8000 tcp:8000`** — forwards the phone's
  `127.0.0.1:8000` to the PC's port 8000 over the USB cable. Works regardless of
  Wi-Fi, and `127.0.0.1` is already cleartext-allowed, so **no app code changed**.
- Built with: `ANDROID_API_BASE_URL=http://127.0.0.1:8000/api/v1`

```
$ adb reverse tcp:8000 tcp:8000
$ adb reverse --list
8000
UsbFfs tcp:8000 tcp:8000
```

Verified baked-in value:
```
BuildConfig.java: API_BASE_URL = "http://127.0.0.1:8000/api/v1"   ✅
BuildConfig.java: DEEP_LINK_SCHEME = "codepilotai"              ✅
```

## 4. Build

Command (from `android/`):
```powershell
$env:ANDROID_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
.\gradlew.bat assembleDebug --console=plain
```
Full log: `install-log-gradle.txt`

Result:
```
BUILD SUCCESSFUL in 7m
40 actionable tasks: 40 executed
EXITCODE=0
```
Artifact: `android/app/build/outputs/apk/debug/app-debug.apk`

Non-fatal notes:
- `Note: Some input files use or override a deprecated API.` (javac warning only)
- PowerShell surfaced stderr as a red `RemoteException` line — cosmetic; exit code 0.
- `[Incubating] Problems report` — Gradle informational notice only.

## 5. Install — errors encountered and resolutions

**Error 1 (real failure):**
```
adb.exe: failed to install ... app-debug.apk:
Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package dev.codepilotai
signatures do not match newer version; ignoring!]
```
Cause: an older `dev.codepilotai` build with a different signing key was already on
the phone.
Resolution: uninstalled the old package, then installed fresh.
```
$ adb -s R9ZY50VZR9A uninstall dev.codepilotai
Success   (UNINSTALL_EXIT=0)
$ adb -s R9ZY50VZR9A install app-debug.apk
Performing Streamed Install
Success   (INSTALL_EXIT=0)
```
Note: this wiped the old app's local data (saved tokens) — expected.

**Verification:**
```
pm list packages           → package:dev.codepilotai
dumpsys package dev.codepilotai → versionCode=1 versionName=0.1.0 minSdk=24 targetSdk=36
adb reverse --list         → UsbFfs tcp:8000 tcp:8000
```

## 6. GitHub sign-in — remaining requirement (not an app problem)

The app is installed, but "Sign in with GitHub" needs the **backend running**.
Currently: no `.env`, no `.venv`, nothing listening on :8000 — so the app's OAuth
call fails and shows `Sign-in failed: <message>` (string `sign_in_failed`).

Secrets the backend needs (`.env`, copied from `.env.example`):

| Variable | Source | Can I generate it? |
|---|---|---|
| `GITHUB_CLIENT_ID` | GitHub OAuth App | ❌ you create it |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App | ❌ you create it |
| `DJANGO_SECRET_KEY` | random ≥32 chars | ✅ yes |
| `TOKEN_ENCRYPTION_KEY` | `Fernet.generate_key()` | ✅ yes |
| `GROQ_API_KEY` | console.groq.com | ❌ you create it (needed to run tasks) |

GitHub OAuth App setup (github.com/settings/developers → OAuth Apps):
- Homepage URL: `http://localhost:8000`
- Authorization callback URL: `http://localhost:8000/api/v1/auth/github/callback`
- Scopes: `repo`, `read:user`, `user:email`

Because of `adb reverse`, a browser on the phone that lands on
`http://localhost:8000/...` is forwarded to the PC backend, so the default
`localhost` callback works with the USB setup.

## 6b. Launch verification

```
$ adb -s R9ZY50VZR9A shell monkey -p dev.codepilotai -c android.intent.category.LAUNCHER 1
Events injected: 1
$ adb ... dumpsys activity activities
topResumedActivity=ActivityRecord{... dev.codepilotai/.ui.MainActivity t3881}   ✅

$ adb -s R9ZY50VZR9A logcat -d -t 200 | grep -E "codepilotai|AndroidRuntime|FATAL"
(no output — no crash / no FATAL)                                               ✅
```

## 6c. Backend setup (for GitHub sign-in)

### .env created
- Path: `C:\Users\user\Desktop\CodePilotAI\.env` (repo root; backend reads `REPO_ROOT/.env`)
- Confirmed git-ignored: `git check-ignore -v .env` → `.gitignore:33:.env`
- Generated (real, cryptographic RNG):
  - `DJANGO_SECRET_KEY` (64 chars)
  - `TOKEN_ENCRYPTION_KEY` (44 chars, url-safe base64 of 32 bytes = valid Fernet key)
- Left as placeholders for the user to paste:
  - `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GROQ_API_KEY`
- Other notable choices: `DJANGO_SETTINGS_MODULE=config.settings.dev`,
  `CELERY_TASK_ALWAYS_EAGER=True` (no Redis needed), `FCM_ENABLED=False`,
  `ANDROID_API_BASE_URL=http://127.0.0.1:8000/api/v1`.

### RNG gotcha (logged)
- `[System.Security.Cryptography.RandomNumberGenerator]::Fill()` does not exist in
  this PowerShell/.NET → first attempt produced all-zero keys (discarded).
- Used `New-Object System.Security.Cryptography.RNGCryptoServiceProvider` +
  `.GetBytes()` instead.

### Python
- NOT installed (only the Microsoft Store alias stub at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`); `py` launcher absent.
- Action: `winget install --id Python.Python.3.12 -e --scope user` (log:
  `install-log-python.txt`).

## 6d. Backend setup progress (PAUSED — resume here tomorrow)

State at pause:

| Step | Status |
|---|---|
| Android app installed + running on phone | ✅ DONE |
| `android/local.properties` created | ✅ DONE |
| `adb reverse tcp:8000 tcp:8000` (USB port forward) | ✅ DONE (re-run after replugging) |
| `.env` created with generated keys | ✅ DONE (user still to paste 3 values) |
| Python 3.12.10 installed (winget, user scope) | ✅ DONE |
| `C:\Users\user\Desktop\CodePilotAI\.venv` created | ✅ DONE |
| Backend deps installed (`pip install -e .` + dev group) | ❌ NOT DONE — **interrupted** |
| Migrations run | ❌ NOT DONE |
| Backend server started | ❌ NOT DONE |

Verified at pause: `import django` → `ModuleNotFoundError: No module named 'django'`
(venv exists but packages were NOT installed; the pip command was interrupted).

Python path:
`C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe`
Venv python:
`C:\Users\user\Desktop\CodePilotAI\.venv\Scripts\python.exe`
Interrupted-install log (partial): `install-log-pip.txt`

### RESUMPTION (2026-10-21)
- User pasted credentials; `.env` verified real (no placeholders):
  - GITHUB_CLIENT_ID   length=20 prefix=Ov23  (valid format)
  - GITHUB_CLIENT_SECRET length=40 prefix=a08c (valid format)
  - GROQ_API_KEY       length=56 prefix=gsk_  (valid format)
- Note: an earlier "i have pasted" attempt did NOT save (file mtime unchanged);
  re-save fixed it. Backend reads only REPO_ROOT/.env.
- Rerunning pip install in background (log: install-log-pip.txt).

### RESUME COMMANDS (tomorrow)

```powershell
$root = "C:\Users\user\Desktop\CodePilotAI"
cd "$root\backend"

# 1. finish dependency install
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\.venv\Scripts\python.exe" -m pip install -e .
& "$root\.venv\Scripts\python.exe" -m pip install --group dev

# 2. migrate (SQLite, no Redis needed because CELERY_TASK_ALWAYS_EAGER=True)
& "$root\.venv\Scripts\python.exe" manage.py migrate

# 3. run the API for the phone (adb reverse forwards phone 127.0.0.1:8000 -> PC:8000)
& "$root\.venv\Scripts\python.exe" manage.py runserver 0.0.0.0:8000
```

Before signing in, the user must paste into `.env`:
`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GROQ_API_KEY`.

## 8. Backend RUNNING (2026-10-21)

| Step | Status |
|---|---|
| Backend deps (`pip install -e .` + `--group dev`) | ✅ `PIP_INSTALL_EXIT=0` (87+ pkgs, Django 5.2.18) |
| `manage.py check` | ✅ "System check identified no issues (0 silenced)" |
| `manage.py migrate` | ✅ `MIGRATE_EXIT=0`, all migrations applied to SQLite |
| `manage.py runserver 0.0.0.0:8000` | ✅ running (bg); log `install-log-server.txt` |
| `GET /api/v1/health` | ✅ `{"status":"ok","database":true}` |
| `GET /api/v1/auth/github/start` | ✅ returns `authorize_url` w/ client_id `Ov23li…` |

Authorize URL confirms the callback the backend sends to GitHub:
`http://localhost:8000/api/v1/auth/github/callback`
→ **The GitHub OAuth App's callback URL must match this exactly.**

Root cause of the original "Could not complete GitHub sign-in" error:
`apps/accounts/views.py` returns `exchange_failed` / "Could not complete GitHub
sign-in." when the GitHub token exchange fails — i.e. backend not running, or
client id/secret/callback mismatch.

## 9. LIVE TEST (2026-10-21)

- Phone re-authorized: `adb devices` went `unauthorized` → `device` after
  `adb kill-server` / `adb start-server` (re-triggered the USB-debugging prompt).
- `adb reverse tcp:8000 tcp:8000` → active (`UsbFfs tcp:8000 tcp:8000`).
- **PROOF the phone reached the backend** (server log):
  ```
  [21/Oct/2026 21:09:27] "GET /api/v1/auth/github/start HTTP/1.1" 200 347
  ```
- Chrome Custom Tab opened (`CustomTabActivity`) = the GitHub authorize page.
- Waiting on the user to tap **Authorize** on the phone → GitHub redirects to
  `/api/v1/auth/github/callback` → app receives `codepilotai://auth/callback`.

Note: `Not Found: /health` lines in the log are from an unrelated probe of
`/health` (correct path is `/api/v1/health`); they are harmless.

## 10. ROOT CAUSE FOUND + FIXED (2026-10-21)

Symptom: user tapped **Authorize** on GitHub, GitHub redirected back to the
backend, but the app said "could not complete the sign in".

Backend log showed:
```
github_oauth_exchange_failed  reason='GitHub token exchange failed (HTTP 400).'
GET /api/v1/auth/github/callback?code=... 302
```

### Cause
`backend/apps/github/oauth.py` `exchange_code_for_token()` sent form-encoded
body (`data=payload`) while also forcing `Content-Type: application/json`.
GitHub parsed the form body as JSON, failed, and returned a **bare HTTP 400**
with an empty body. Isolated proof (POST to
`https://github.com/login/oauth/access_token` with a dummy code):

| Request style | Result |
|---|---|
| `data=` + `Content-Type: application/json` (old code) | ❌ 400, empty body |
| `data=` + `Accept: application/json` (new code)       | ✅ 200, real JSON error |

### Fix
Removed `Content-Type: application/json` from the headers so httpx sends
`application/x-www-form-urlencoded` (what GitHub expects).

```python
# before
headers = {"Accept": "application/json", "Content-Type": "application/json"}
# after
headers = {"Accept": "application/json"}
```
Verified: `exchange_code_for_token('dummy')` now raises
`GitHubAuthError: GitHub rejected the authorization code (bad_verification_code: ...)`
instead of a bare 400.

Server restarted to apply the fix; `/api/v1/health` = ok; `adb reverse` still active.
User to retry sign-in.

## Next
- Reconnect phone (USB debugging + file transfer) → `adb reverse tcp:8000 tcp:8000`
- Launch app → Sign in with GitHub → verify `codepilotai://auth/callback` deep link

```powershell
# phone must stay plugged in with USB debugging on
adb reverse tcp:8000 tcp:8000          # re-run after replugging the cable
adb -s R9ZY50VZR9A shell monkey -p dev.codepilotai 1   # or tap the app icon

# reinstall after code changes (from android/)
$env:ANDROID_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
.\gradlew.bat installDebug
```
