# Deployment guide

CodePilotAI's backend is a standard Django + DRF app with one non-standard
requirement: it serves **Server-Sent Events**, so the web tier must not buffer
responses and should run an async or thread-based worker. The agent runs happen
in **Celery** workers, not in the web process.

```
                 ┌─────────────────────────┐
  Android ──HTTPS──▶  nginx / reverse proxy  │
                 └───────────┬─────────────┘
                             │
                 ┌───────────▼─────────────┐        ┌──────────────┐
                 │  ASGI web (Django/DRF)   │◀──────▶│  PostgreSQL  │
                 │  REST + SSE, stateless   │        └──────────────┘
                 └───────────┬─────────────┘
                             │ broker / results / cache
                 ┌───────────▼─────────────┐
                 │        Redis            │
                 └───────────┬─────────────┘
                             │
                 ┌───────────▼─────────────┐        ┌──────────────┐
                 │  Celery worker (agent)   │───────▶│  Workspaces  │
                 │  LangGraph run + git     │        │  (disk)      │
                 └─────────────────────────┘        └──────────────┘
```

Only the backend holds secrets. The Android app ships with none.

## 1. Requirements

- Python 3.12+ (developed on 3.13)
- PostgreSQL 14+ and Redis 6+
- A process manager (systemd, supervisord, or a container orchestrator)
- A reverse proxy that terminates TLS (nginx, Caddy, a cloud LB)
- Persistent disk for `WORKSPACE_ROOT` (git mirrors + per-task worktrees)
- **git** on `PATH` and **JDK/Node/Python toolchains** as needed by the repos the
  agent is asked to build (the verify step runs the repo's own commands)

## 2. Environment

Set `DJANGO_SETTINGS_MODULE=config.settings.prod`. Production boot **fails fast**
if any of these are missing or weak (see `config/settings/prod.py`):

| Variable | Notes |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_SECRET_KEY` | unique, >= 32 chars (boot-guarded) |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | comma-separated public hostnames |
| `DATABASE_URL` | `postgres://user:pass@host:5432/codepilotai` |
| `POSTGRES_SSLMODE` | defaults to `require` |
| `REDIS_URL` | `redis://host:6379/0` - broker, results, and throttling cache |
| `GROQ_API_KEY` | required (boot-guarded) |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | required (boot-guarded) |
| `GITHUB_REDIRECT_URI` | public `https://<host>/api/v1/auth/github/callback` |
| `TOKEN_ENCRYPTION_KEY` | required (boot-guarded); Fernet key |
| `WORKSPACE_ROOT` | e.g. `/var/lib/codepilotai/workspaces` |
| `CORS_ALLOWED_ORIGINS` | only if a browser client calls the API |
| `SENTRY_DSN`, `LOG_LEVEL` | optional observability |
| `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_JSON` | optional push; blank disables it (see `apps/notifications`) |

Agent guardrails (defaults are sane; tune per capacity):

```
AGENT_PIPELINE_ENABLED=True
AGENT_MAX_TOOL_CALLS=25
AGENT_MAX_ITERATIONS=8
AGENT_MAX_REPAIR_ATTEMPTS=2
AGENT_TOKEN_BUDGET=120000        # per run
AGENT_WALL_CLOCK_SECONDS=600     # per run
AGENT_DAILY_TOKEN_BUDGET=2000000 # per user, per UTC day (hard stop)
```

> `prod.py` enables `SECURE_SSL_REDIRECT`, HSTS (1 year, preload), secure cookies,
> `X-Frame-Options: DENY`, and `nosniff`. Serve over HTTPS only; the proxy must
> send `X-Forwarded-Proto` (already wired via `SECURE_PROXY_SSL_HEADER`).

## 3. Install & build

```bash
# runtime dependencies only (skip the dev group)
python -m venv /opt/codepilotai/.venv
/opt/codepilotai/.venv/bin/pip install -e /opt/codepilotai/backend

export DJANGO_SETTINGS_MODULE=config.settings.prod
source /path/to/.env            # or use your process manager's EnvironmentFile

cd /opt/codepilotai/backend
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
```

## 4. Processes

Run each as its own supervised service.

### Web (ASGI - required for SSE)

```bash
# preferred: uvicorn ASGI workers
uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --workers 4

# or gunicorn + uvicorn worker
gunicorn config.asgi:application \
  -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 --timeout 120
```

The web tier is stateless - scale replicas horizontally. Any replica can serve
the SSE stream because the event table (Postgres) is the replay buffer.

### Celery worker (agent runs)

```bash
celery -A config worker -l info -c 4
```

- `-c` = concurrency. Each task can hold a slot for up to
  `CELERY_TASK_TIME_LIMIT` (30 min); size workers against CPU, disk, and the
  repo test suites they run.
- `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` is set so long tasks dispatch fairly.
- Scale workers independently of the web tier; they are the expensive tier.
- No Celery Beat schedule is required.

## 5. Reverse proxy (nginx)

SSE breaks if the proxy buffers the response. Turn buffering off (at least for
the stream route) and allow long-lived reads.

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    client_max_body_size 5m;

    # Static files (from collectstatic)
    location /static/ {
        alias /opt/codepilotai/backend/staticfiles/;
        expires 30d;
    }

    # SSE stream: no buffering, long read timeout
    location ~ ^/api/v1/tasks/\d+/stream$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        chunked_transfer_encoding off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

The stream emits a heartbeat every 15s and caps a single connection at 30
minutes; the client reconnects with `?after_seq=N` and loses nothing.

## 6. GitHub OAuth in production

Update the OAuth App's **Authorization callback URL** to the public URL, and set
`GITHUB_REDIRECT_URI` to match exactly:

```
https://api.example.com/api/v1/auth/github/callback
```

The callback 302s to the app's deep link `codepilotai://auth/callback?code=...`.
No app-side configuration is needed beyond the manifest already in the client.

## 7. Local infrastructure with Docker Compose

`docker-compose.yml` at the repository root brings up the two stateful
dependencies for a self-hosted or local run (Postgres + Redis). It does **not**
include the app; run Django/Celery natively or in your own images.

```bash
docker compose up -d
# then point .env at the defaults:
#   DATABASE_URL=postgres://codepilotai:codepilotai@localhost:5432/codepilotai
#   REDIS_URL=redis://localhost:6379/0
```

The bundled credentials are for local development only - change them before
exposing the services.

## 8. Data, backups, and housekeeping

- **Postgres** is the source of truth for users, tasks, changes, and the event
  log. Back it up normally.
- **`WORKSPACE_ROOT`** holds bare mirrors and per-task worktrees. It is
  regenerable from GitHub, but it can be large; put it on a volume with room for
  the repos you ask the agent to build. A failed run keeps its partial worktree
  so a worker can resume; prune old worktrees periodically if disk grows.
- **Redis** holds ephemeral broker data, task results, and the throttling cache.
  Losing it drops in-flight queue messages; tasks remain visible in `QUEUED`
  state and can be re-enqueued.

## 9. Scaling notes

- Web replicas scale horizontally; Redis must be shared across them (the prod
  settings already use Redis for the cache, so throttling is consistent).
- Agent workers scale with the number of concurrent tasks; the daily token
  budget and per-run budgets cap spend per user.
- Keep `AGENT_WALL_CLOCK_SECONDS` and `CELERY_TASK_SOFT_TIME_LIMIT` aligned so a
  stuck run is reaped by the agent budget before Celery force-kills it.

## 10. Operations dashboard (optional)

The backend ships a read-only console at `/dashboard/` for inspecting tasks,
proposed changes, verification outcomes, and token/cost usage. It is
deliberately **staff-only** and platform-wide: it is an internal ops surface,
not a second product UI.

Because CodePilotAI has no password login (a user *is* their GitHub identity;
passwords are unusable), the console reuses Django **session auth**. Provision
an operator out-of-band:

```bash
python manage.py shell -c "
from django.contrib.auth import get_user_model
u, _ = get_user_model().objects.get_or_create(username='ops')
u.is_staff = True
u.set_password('a-strong-unique-password')
u.save()
"
```

Then sign in at `https://<host>/dashboard/login/`. No extra environment variable
is required. The console is read-only, so no routes under `/dashboard/` mutate
data.

## 11. Security checklist

- [ ] HTTPS terminated with a valid certificate; `SECURE_SSL_REDIRECT` on.
- [ ] `DJANGO_SECRET_KEY` is unique and >= 32 chars (boot guard enforces).
- [ ] `TOKEN_ENCRYPTION_KEY` set; GitHub tokens encrypted at rest.
- [ ] `.env` is not world-readable and never committed.
- [ ] Postgres/Redis not exposed to the public internet.
- [ ] OAuth callback URL updated to the public HTTPS host.
- [ ] `python manage.py check --deploy` reports no issues.
- [ ] Log shipping enabled (`LOG_LEVEL`, optional `SENTRY_DSN`); logs are
      structured JSON and never contain tokens.
- [ ] If the operations dashboard is exposed, its staff accounts use strong,
      unique passwords and are limited to operators who should see all tasks.
- [ ] Backups for Postgres (and, optionally, workspaces).
