# CodePilotAI

A mobile-first agentic coding assistant. Sign in with GitHub, pick a repository,
describe a change in plain language, watch the agent stream its progress, approve
or reject **every file it proposes to touch**, and get a finished pull request.

The phone is the product; the backend is the agent runtime. Nothing is pushed
without an explicit, per-file human decision.

```
  Android app                     Backend (Django + DRF)             GitHub
  -----------                     ----------------------             ------
  GitHub sign-in  ──OAuth──▶  /api/v1/auth/github/start|callback
                              exchange one-time code ──▶ JWT
  pick repo       ─────────▶  /api/v1/repos, /repos/{id}/branches ──▶ mirror
  describe task   ─────────▶  /api/v1/tasks  ──▶ Celery worker ──▶ LangGraph agent
                                    │                                    │
       SSE progress  ◀──────  /api/v1/tasks/{id}/stream  ◀── explore/propose
  approve / reject ────────▶  /tasks/{id}/changes/{cid}/decide
                              /tasks/{id}/execute ──▶ apply → verify → repair
                                                        → commit → push ──▶ PR
  result + audit  ◀───────  /tasks/{id}  +  run-event trail
```

## Repository layout

```
codepilotai/
  Task.md            Canonical plan, decision log (Q1-Q38), session history
  .env.example       Backend environment template - placeholders only
  docker-compose.yml Postgres + Redis for local/self-hosted use
  backend/           Django 5.2 + DRF + Celery + LangGraph agent
    config/          settings split (base/dev/prod/test), urls, celery, observability
    apps/
      accounts/      User model (Fernet-encrypted GitHub tokens), OAuth, JWT
      repositories/  Repository/Branch models, GitHub sync, branches API
      agent_tasks/   AgentTask/ProposedChange/RunEvent/Message, approvals, SSE
      git/           Hardened git runner, bare mirrors, worktrees, diff parsing
      agent/         LangGraph pipeline: explore -> propose -> apply -> verify -> push
      notifications/ Device-token registry + FCM push
      dashboard/     Read-only staff operations console (Django templates)
    templates/       dashboard/*.html served by the operations console
    tests/           650 pytest tests (unit + API + real-git integration)
  android/           Java 17 client (see android/README.md)
    app/             MVVM + Repository, Dagger 2, RxJava 3, Retrofit/Moshi, Room
```

## How it works

1. **Sign in** - the app opens the backend's GitHub authorize URL in a Custom
   Tab. The OAuth callback never returns tokens in a redirect: it 302s to
   `codepilotai://auth/callback?code=<one-time>`, and the app exchanges that
   short-lived, single-use code for an access/refresh JWT pair.
2. **Pick a repository** - the backend keeps a bare *mirror* clone per repo and
   creates an isolated worktree per task.
3. **Describe a task** - a new `codepilot/<slug>` branch is allocated and a
   Celery worker runs the agent: it explores the code, then proposes **per-file
   unified diffs** (never whole files).
4. **Watch it work** - phases, log lines, reasoning, and proposed changes stream
   over Server-Sent Events. The event table is the durable buffer, so a client
   that reconnects with `?after_seq=N` loses nothing.
5. **Decide** - each change is approved, rejected, or sent back with an edit
   request. Execution only starts once every change is decided and at least one
   is approved; rejected changes are skipped.
6. **Ship** - the agent applies, runs the repo's detected formatter/linter/test
   commands, repairs failures within a bounded budget, commits, pushes, and opens
   a pull request. Every decision is attributed to an actor with a timestamp in
   the run-event audit trail.

## Quick start

### Prerequisites

- **Python 3.12+** (developed on 3.13)
- A **GitHub OAuth App** (client id + secret) - see below
- A **Groq API key** (the LLM provider)
- For the app: **JDK 17** and the **Android SDK** (platform 36)

### 1. Backend

```powershell
# from the repository root
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
cd backend
..\.venv\Scripts\python.exe -m pip install -e .
..\.venv\Scripts\python.exe -m pip install --group dev   # PEP 735 dev group
```

Create your `.env` from the template and fill in real values (see
**Configuration** below):

```powershell
Copy-Item ..\.env.example ..\.env
# edit ..\.env: DJANGO_SECRET_KEY, GROQ_API_KEY, GITHUB_CLIENT_ID/SECRET,
#                TOKEN_ENCRYPTION_KEY
```

Generate the encryption key and the secret:

```powershell
..\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
..\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))"
```

Run it (SQLite + eager Celery out of the box - no Redis required for dev):

```powershell
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

- API root: `http://localhost:8000/api/v1/` - docs at `/api/docs/`.
- Health: `http://localhost:8000/api/v1/health`.

### 2. GitHub OAuth App

Create one at <https://github.com/settings/developers> -> OAuth Apps:

| Field | Value |
|---|---|
| Homepage URL | `http://localhost:8000` |
| Authorization callback URL | `http://localhost:8000/api/v1/auth/github/callback` |
| Scopes | `repo`, `read:user`, `user:email` |

Put the client id/secret in `.env`. The callback must match `GITHUB_REDIRECT_URI`
exactly.

### 3. Android app

```powershell
cd android
.\gradlew.bat assembleDebug          # APK: app/build/outputs/apk/debug/app-debug.apk
.\gradlew.bat installDebug           # with a device/emulator attached
```

The app talks to `http://10.0.2.2:8000/api/v1` by default (emulator -> host
loopback). For a physical device, point it at a reachable address:

```powershell
$env:ANDROID_API_BASE_URL = "http://192.168.1.20:8000/api/v1"
.\gradlew.bat assembleDebug
```

See `android/README.md` for the screen-by-screen flow and code layout.

### 4. Operations dashboard (optional)

The backend ships a read-only, staff-only console at `/dashboard/` for
inspecting tasks, proposed diffs, verification outcomes, and token/cost usage.
There is no password login for ordinary users (a user *is* their GitHub
identity), so provision an operator once — from `backend/`:

```powershell
..\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model; u, _ = get_user_model().objects.get_or_create(username='ops'); u.is_staff=True; u.set_password('change-me'); u.save()"
```

Then sign in at `http://localhost:8000/dashboard/login/`. Nothing under
`/dashboard/` writes data; see `docs/DEPLOYMENT.md` section 10.

## Configuration

All backend configuration comes from environment variables (or a `.env` file at
the repository root). `.env.example` documents every one; the important ones:

| Variable | Required | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | yes | `config.settings.dev` locally, `config.settings.prod` deployed |
| `DJANGO_SECRET_KEY` | yes | >= 32 chars in production (enforced at boot) |
| `GROQ_API_KEY` | yes | LLM provider key (never leaves the backend) |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | yes | GitHub OAuth App |
| `GITHUB_REDIRECT_URI` | yes | must match the OAuth app callback exactly |
| `TOKEN_ENCRYPTION_KEY` | yes | Fernet key; encrypts GitHub tokens at rest |
| `DATABASE_URL` | prod | `postgres://...`; defaults to SQLite |
| `REDIS_URL` | prod | Celery broker + result backend + cache-backed throttling |
| `DJANGO_ALLOWED_HOSTS` | prod | comma-separated hostnames |
| `ANDROID_API_BASE_URL` | no | default `http://10.0.2.2:8000/api/v1` |
| `AGENT_*` | no | pipeline switch + guardrails (tool calls, iterations, token/wall-clock budgets) |
| `AGENT_DAILY_TOKEN_BUDGET` | no | per-user daily token cap; hard-stops new runs |
| `FCM_PROJECT_ID` / `FCM_SERVICE_ACCOUNT_JSON` | no | Firebase Cloud Messaging push; blank disables it |

> **API keys are placeholders.** Per `Task.md`, no real secret is committed;
> GitHub/Django/Groq credentials live only in the untracked `.env`.

## API surface

Base: `/api/v1`. Router paths carry a trailing slash; the SSE stream does not.

```
Auth      GET  /auth/github/start          GET  /auth/github/callback
          POST /auth/exchange              GET  /auth/me
          POST /auth/logout                DEL  /auth/github/disconnect
Repos     GET/POST /repos                  POST /repos/sync
          GET  /repos/{id}                 GET  /repos/{id}/branches
Tasks     POST /tasks                      GET  /tasks            GET /tasks/{id}
          GET  /tasks/{id}/changes         POST /tasks/{id}/changes/{cid}/decide
          GET/POST /tasks/{id}/messages    POST /tasks/{id}/cancel
          GET  /tasks/{id}/events          GET  /tasks/{id}/stream   (SSE)
Devices   POST /devices                    POST /devices/unregister
Meta      GET  /health                     GET  /docs  GET /schema
```

Cross-tenant access returns **404**, never 403, so an ID probe cannot confirm
that another user's task exists. See `Task.md` section 8 for the full contract.

> There is **no** `/auth/refresh` route yet. Access JWTs last 30 minutes; on a 401
> the app re-runs the OAuth flow. This is tracked in `Task.md` (Security section).

## Testing & quality gates

```powershell
# backend (from backend/)
..\.venv\Scripts\python.exe -m pytest                    # 650 passed, 1 skipped
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check apps config tests
..\.venv\Scripts\python.exe -m mypy apps config

# android (from android/)
.\gradlew.bat test                                       # pure-JVM unit tests
.\gradlew.bat lint                                       # 0 errors
.\gradlew.bat jacocoTestReport                           # coverage report
```

## Security posture

- GitHub tokens are Fernet-encrypted at rest, never logged, and stripped from
  git error output.
- Every query is scoped to the requesting user; cross-tenant lookups 404.
- Path traversal is blocked in all file tools; commands run only from the
  repo's own detected toolchain, allowlisted by first token, with no shell.
- Per-run token + wall-clock budgets and a **per-user daily token budget with a
  hard stop**.
- API rate limiting (scoped) plus Groq 429 backoff with `Retry-After`.
- Full audit trail: every approval/rejection carries an actor and timestamp.

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the production topology,
required environment, web/ASGI + Celery services, reverse proxy (including the
SSE no-buffering settings), static files, and a security checklist.

## Status

Phases 0-6 are built and tested; **Phase 7 (nice-to-haves) is complete** — voice
input for the task description, the approval-screen diff preview (verified
against the pre-push gate), full i18n (all text in string resources, RTL and
per-app language configured), and the optional **operations dashboard**
(`/dashboard/`, read-only and staff-only) have all landed. `Task.md` is the
source of truth for the checklist, the full decision log (Q1-Q38), and a
per-session build history.
