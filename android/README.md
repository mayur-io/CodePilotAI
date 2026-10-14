# CodePilotAI — Android app

Mobile client for CodePilotAI: sign in with GitHub, pick or create a repository,
describe a task in natural language, watch the agent stream its progress, approve
or reject **every file change**, and get the finished pull request.

Locked stack: **Java 17** (no Kotlin), XML layouts + ViewBinding + Material 3,
MVVM + Repository, **Dagger 2**, **RxJava 3**, Retrofit 2 + OkHttp + Moshi +
`okhttp-sse`, **Room**, WorkManager, EncryptedSharedPreferences, Custom Tabs +
deep link, Gradle KTS, `minSdk 24` / `targetSdk 36`.

## Prerequisites

- JDK 17 (`sourceCompatibility`/`targetCompatibility` = 17)
- Android SDK (platform `android-36`, build-tools `36.0.0`)
  - The SDK path is picked up from `local.properties` (`sdk.dir=…`) or
    `ANDROID_HOME`.

## Build & run

```powershell
# from android/
.\gradlew.bat assembleDebug          # APK at app/build/outputs/apk/debug/app-debug.apk
.\gradlew.bat installDebug           # with a device/emulator attached
.\gradlew.bat test                   # JVM unit tests (pure-Java: diff parser, SSE, errors, enums)
.\gradlew.bat lint                   # Android lint report under app/build/reports/
```

No Kotlin plugin, no KSP, no Hilt — annotation processors are `dagger-compiler`
and `room-compiler` only. All ViewModels come from Dagger-provided
`ViewModel.Factory` classes; workers come from `CodePilotWorkerFactory`
registered as WorkManager's `Configuration.Provider`.

## Configuration

| Setting | Where | Default |
|---|---|---|
| Backend base URL | `ANDROID_API_BASE_URL` env var, Gradle property, or `local.properties` | `http://10.0.2.2:8000/api/v1` (emulator → host loopback) |
| Deep-link scheme | `BuildConfig.DEEP_LINK_SCHEME` | `codepilotai` |

The base URL is baked into `BuildConfig.API_BASE_URL` at build time, so a
physical device needs a reachable LAN address, e.g.:

```powershell
$env:ANDROID_API_BASE_URL = "http://192.168.1.20:8000/api/v1"
.\gradlew.bat assembleDebug
```

The OAuth callback URI `codepilotai://auth/callback` is declared in the manifest
(`scheme=codepilotai`, `host=auth`, `pathPrefix=/callback`) — no app-side setup.
Requires the backend (Django, `api/v1` router) running with your GitHub OAuth app
id/secret configured.

## Screens & flow

1. **Auth** — Sign in with GitHub opens the backend's authorize URL in a Custom
   Tab; the one-time handoff code returns via the deep link and is exchanged for
   JWTs (stored in `EncryptedSharedPreferences`). Existing sessions skip ahead.
2. **Repos** — search, sync from GitHub, create (name + description + private),
   sign out. Cached in Room for offline display.
3. **New task** — pick a repository, base branch, and prompt. Offline, the task
   is queued to `task_drafts` and pushed by `DraftSyncWorker` (15-min periodic +
   network-gated) when connectivity returns.
4. **Run** — live SSE stream (`GET /tasks/{id}/stream/?after_seq=N`); the phase
   header streams ("Reading your repository", "Drafting changes", …), the
   collapsible "thinking" trace shows reasoning, and the status row handles
   approvals and terminal states. Reconnects replay from the Room event cache.
5. **Approval** — file-tab chips, per-file diff with `+`/`-` highlighting and
   collapsible hunks, and a thumb-reachable bottom bar: Approve / Reject /
   Request edit. Executes once ≥1 change is approved and none pending.
6. **Result** — agent summary, verification/lint/test outcome cards, prompt,
   diff stat, tokens/cost (with the prompt-cache reuse count when the provider
   served any input from cache), the pull request link (system browser), and a
   collapsible **Activity log** — the audit trail of decisions (who approved or
   rejected which file, and when), verification runs, and the push.
7. **History** — task list with pull-to-refresh; open any task's run or result.

## Code layout

```
app/src/main/java/dev/codepilotai/
  data/api/         Retrofit interfaces, DTOs, AuthInterceptor, ApiException, SseClient
  data/db/          Room database, entities, DAOs (repos, tasks, events, changes, drafts)
  data/prefs/       TokenStore (EncryptedSharedPreferences + plain fallback)
  data/repo/        AuthRepository, ReposRepository, TaskRepository, DraftRepository, DevicesRepository
  domain/model/     Pure models + wire enums (TaskStatus, TaskPhase, ChangeStatus, …)
  notifications/    FirebasePush, PushCoordinator, TaskNotifications, CodePilotMessagingService
  ui/               MainActivity + 7 fragments + ViewModels + adapters
  util/             DiffParser, Event (single-shot LiveData), Labels, AuditLine/AuditLog, VoicePrompt
  work/             DraftSyncWorker, CodePilotWorkerFactory
  di/               AppModule, NetworkModule, DatabaseModule, RepositoryModule, ViewModelModule
```

## Notes

- **API keys are placeholders** (per `Task.md`): all GitHub/Django/Groq secrets
  live in the backend's `.env`, never in the app.
- On a backend **401** the app re-runs the OAuth flow (there is no
  `/auth/refresh` route yet — tracked in `Task.md`).
- **Push notifications (FCM)** are wired end to end, but inert until you drop a
  Firebase `app/google-services.json` into `android/app/`. The `google-services`
  plugin is applied only when that file exists, so a clean checkout still builds
  and runs; until then the app registers no token and the messaging service never
  fires. Messages are data-only, so the app builds the notification and opens the
  task on tap.
- **Voice input** on the new-task screen hands dictation to the system recognizer
  (`ACTION_RECOGNIZE_SPEECH`), so the app requires **no** `RECORD_AUDIO` permission;
  a device without a recognizer shows a toast instead.
- **Localization.** Every user-visible string lives in `res/values/strings.xml`,
  including the dynamic labels: `util/Labels` and `EventAdapter` return
  `@StringRes` ids, and `util/AuditLog.summary` returns an `AuditLine`
  (id + args, nestable) that the UI resolves with `getString`. `RunViewModel`
  therefore carries ids, not English. Token counts use `<plurals>`; `supportsRtl`
  and `res/xml/locales_config.xml` (`android:localeConfig`) enable RTL and the
  Android 13 per-app language picker. Add a translation by dropping in a
  `res/values-xx/` folder.
- On-device end-to-end verification still pending (no emulator/device attached
  to this workspace).