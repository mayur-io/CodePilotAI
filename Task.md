# CodePilotAI — Master Task Log

> This file is the single source of truth for project state.
> **Update rule:** every session, update the checkboxes and `## Log` before ending work.
> Mark items `[ ]` pending, `[~]` in progress, `[x]` done, `[!]` blocked.

---

## 1. Product Definition

**One-liner:** A mobile-first AI coding assistant. You sign in with GitHub, pick or create a repo,
type a task in plain English, and the agent writes real code on a new branch — showing you every
diff and asking permission before anything is pushed.

**Positioning vs. Codex / Claude Code / OpenCode:** those are terminal-first, keyboard-first,
desktop-first. CodePilotAI is **thumb-first, streaming, approval-gated, and mobile-native**.

### Core user flow (the 9 steps)

1. Sign in with GitHub (OAuth; identity == GitHub account, no separate password).
2. Choose **Create Repo** or **Select Repo**.
3. Agent creates a dedicated **branch per feature/task**.
4. User types task in natural language, taps send.
5. Agent explores repo → drafts changes → **requests permission per change**
   (Approve / Reject / Request edit) — diff shown per file.
6. Approved changes are applied, formatted, linted, tested.
7. Commit + push branch, open **PR**, notify user.
8. App shows: push done, what was written, PR link, test results, token/cost used.
9. Follow-up messages continue on the same branch with full thread context.

---

## 2. Tech Stack (locked)

### Backend
| Concern | Choice |
|---|---|
| Framework | Django 5.2 LTS + Django REST Framework 3.18 |
| API schema | drf-spectacular (OpenAPI) |
| DB | PostgreSQL 16 (SQLite for local dev) |
| Agent orchestration | LangGraph 1.2 + LangChain 1.4 |
| LLM | Groq `openai/gpt-oss-120b` (OpenAI-compatible endpoint) |
| LLM SDK | `langchain-openai` pointed at `https://api.groq.com/openai/v1` |
| Async jobs | Celery 5 + Redis 7 |
| Git | `git` CLI via subprocess (no GitPython) |
| HTTP (GitHub API) | `httpx` |
| Token encryption | `cryptography` Fernet |
| Diff parsing | Hand-rolled in `apps/git/patch.py` (`unidiff` dropped: we only need header + hunk validation) |
| Logging | `structlog` |
| Testing | `pytest` + `pytest-django` + `pytest-httpx` (`responses` has no httpx support) |
| Lint/format/type | `ruff`, `mypy` |
| Docs | `drf-spectacular` / OpenAPI |

### Android App
| Concern | Choice |
|---|---|
| Language | Java 17 |
| UI | XML layouts + ViewBinding + Material 3 |
| Architecture | MVVM + Repository + use-case classes |
| DI | Dagger 2 (pure-Java friendly; see Open Question Q4) |
| Async | RxJava 3 (see Open Question Q5) |
| Networking | Retrofit 2 + OkHttp + Moshi (+ `okhttp-sse`) |
| Local cache | Room |
| Background work | WorkManager |
| Auth storage | EncryptedSharedPreferences |
| OAuth | Custom Tabs + deep link `codepilotai://` |
| Push | Firebase Cloud Messaging (Phase 2) |
| Build | Gradle KTS + AGP, `minSdk 24`, `targetSdk 36` |

### Repo layout
```
codepilotai/
├── Task.md
├── README.md
├── .env.example
├── backend/
│   ├── manage.py
│   ├── pyproject.toml
│   ├── config/               # settings, urls, wsgi, asgi, celery
│   ├── apps/
│   │   ├── accounts/          # User (GitHub identity), token refresh
│   │   ├── repositories/      # Repo, Branch, MirrorCache
│   │   ├── agent_tasks/       # AgentTask, ProposedChange, RunEvent, Commit
│   │   ├── git/               # GitService: mirror/worktree/branch/commit/push
│   │   ├── github/            # GitHubClient: REST API wrapper
│   │   ├── agent/             # LangGraph graph, prompts, tools, context, edits
│   │   └── api/               # DRF viewsets, serializers, urls, SSE views
│   └── tests/
├── android/
│   └── app/src/main/java/dev/codepilotai/...
└── infra/
    ├── docker-compose.yml     # postgres, redis
    └── nginx.conf
```

---

## 3. Verified External Constraints (checked 2026-10-01)

| Item | Finding | Design Consequence |
|---|---|---|
| `openai/gpt-oss-120b` status | **Production** on Groq, not deprecated. Recommended coding model. | Safe to hardcode as default. |
| Specs | 500 tok/s, 131,072 ctx, 65,536 max completion, $0.15 in / $0.60 out per 1M | Plan token budget accordingly. |
| `reasoning_format` | **Unsupported** on gpt-oss | Use `include_reasoning: true`; read `message.reasoning` |
| `reasoning_effort` | `low` \| `medium` \| `high` only | Set explicitly per phase (see §6) |
| Parallel tool calls | **Unsupported** on gpt-oss | LangGraph loop must handle exactly one tool call per step |
| Temperature | 0.5–0.7 recommended | `temperature=0.6`, `top_p=0.95` |
| `stop` sequences | Avoid with gpt-oss reasoning | Never set `stop` |
| Rate limits (dev tier) | 250K TPM, 1K RPM | Backoff + queue; budget cap per task |
| Prompt caching | Groq caches ≥1024-token prefixes; cached input bills at half rate | Repo context first, question last, to reuse the cache; cached tokens are captured, discounted in cost, and shown on the result screen |

---

## 4. Data Model

```
accounts.User            (OneToOne → auth.User)
  github_id, username, avatar_url, access_token_enc, refresh_token_enc,
  token_scope, token_expires_at

repositories.Repository  owner→User, github_id, full_name, default_branch,
                         is_private, language, cloned_at, mirror_path, mirror_synced_at

repositories.Branch      repository, name, head_sha, is_open, pr_url, pr_number,
                         pr_state, created_by_task

agent_tasks.AgentTask    user, repository, base_branch, feature_branch,
                         title, prompt, status, current_phase,
                         agent_reasoning, agent_summary, commit_sha, pr_url,
                         input_tokens, output_tokens, cost_usd, error_message
  status: queued|running|awaiting_approval|applying|committing|pushed|failed|cancelled|merged
  phase: plan|explore|propose|await_approval|apply|verify|commit|push|done

agent_tasks.ProposedChange  task, path, change_type(create|modify|delete|rename),
                            patch (unified diff), rationale, additions, deletions,
                            status(pending|approved|rejected|applied|failed),
                            order_index, user_feedback

agent_tasks.RunEvent     task, seq, kind, payload JSONB, created_at   (SSE source)
agent_tasks.Message      task, role(user|assistant|system), content, created_at  (thread memory)
git_svc.Mirror          repository, local_path, last_fetch_at
```

---

## 5. Agent Architecture (LangGraph)

### State
`GraphState`: task_id, repo, base_branch, feature_branch, prompt, repo_map, instructions,
`opened_files`, `proposals[]`, `messages[]`, `reasoning_trace[]`, phase, budget, iteration.

### Nodes
```
plan        → break task into concrete, repo-aware sub-goals
explore     → tool loop: repo_map / read_file / grep / list_dir / read_manifest
              (bounded: max 25 tool calls, max 3 self-corrections)
propose     → produce ProposedChange set (unified diffs) + rationale + summary
[PAUSE]     → task.status = awaiting_approval ; return control to app
apply       → apply approved diffs with fuzzy matcher to worktree
verify      → auto-detect & run repo formatter / linter / tests; capture output
repair      → if verify fails, one bounded self-repair attempt (max 2)
commit      → conventional commit message, single clean commit
push        → push branch, open PR with generated body
```

### Tools exposed to the model (local tool calling)
- `list_dir(path)` — directory listing, filtered
- `read_file(path, start_line?, end_line?)` — paginated read w/ line numbers
- `search_code(query, glob?)` — ripgrep-backed symbol/text search
- `read_repo_instructions()` — AGENTS.md / CLAUDE.md / CONTRIBUTING.md / .cursor/rules / .github/copilot-instructions.md

During `propose` the tool surface swaps to exactly two write-*proposals*:
- `propose_change(path, change_type, rationale, patch)` — one validated single-file unified diff
- `finish(summary)` — closes the proposal set; ends the run without another model call

Tools **never** include write/commit/push — writing is only possible via the approval gate.

### Loop control (implemented in `apps/agent/graph.py`)
- One shared `StateGraph`: `call_model` ⇄ `run_tool`, used by both explore and propose
- No parallel tool calls (gpt-oss limitation) → a completion carrying two raises `ModelOutputError`
- Round cap lives **in state** (`tool_calls_remaining`) — a non-converging loop stops at a
  known cost via `stopped_reason: "tool_calls_exhausted"` instead of a recursion exception
- `budget.check()` before every model call **and** every tool execution; per-call token cap
  (12,000) on top of the run budget; `finish()` short-circuits to END after the last tool
- Hard wall-clock + token budget per task; graceful `failed` state with partial work retained

---

## 6. Prompting Strategy (the "don't look AI-written" requirement)

Deterministic controls (do not rely on the model for these):
1. **Unified diffs only.** Model never returns whole files. Applied with fuzzy matcher.
2. **Auto-detect project tooling** from repo (`pyproject.toml`, `package.json`, `go.mod`,
   `Cargo.toml`, `pom.xml`, `build.gradle`) → run the repo's own formatter/linter/tests after edit.
3. **Secret path guard** — block read/write of `.env*`, `*.pem`, `*.key`, `id_rsa*`,
   `credentials*`, `*.p12`, `.aws/`, `.npmrc`.
4. **Context guard** — skip `.git`, `node_modules`, `vendor`, `dist`, `build`, `*.lock`,
   binaries; enforce a token budget on injected repo content.
5. **Repo instruction files** injected verbatim into the system context — repo conventions beat
   generic style rules.
6. **Stack detection** — inject detected language/framework/version so the model matches
   existing conventions rather than inventing new ones.

Prompt-level controls (the anti-slop contract). Explicitly forbid:
- reformatting / restyling code outside the change
- adding docstrings, comments, or type hints to untouched lines
- replacing working code "while I'm here"
- inventing new dependencies when an existing one suffices
- creating README/summary/test files unless requested
- changing unrelated imports, renaming, or "cleanup"

Also require: smallest viable diff, match surrounding style exactly, prefer editing over
rewriting, prefer new files over risky refactors, and always explain *why* in one line per file.

### `reasoning_effort` per phase (implemented in `apps/agent/prompts.py`)

| Phase | Effort | Why |
|---|---|---|
| queued / explore / apply / verify / commit / push | `low` | mechanical: reading, moving bytes, running tools |
| plan | `medium` | decomposition, but no code is written |
| propose / repair | `high` | writing code is where the model must think hardest |

---

## 7. Security & Guardrails

- [x] GitHub tokens encrypted at rest (Fernet, key from env). Never logged, never in responses. *(`apps/accounts/crypto.py`; stored only in the `access_token_enc` column.)*
- [x] Tokens excluded from all exception messages and git error output. *(token reaches `git` only through the askpass env, never argv; `run_git` `scrub_secrets()`es stdout/stderr before raising or logging.)*
- [x] Auth for our own API: short-lived access JWT + rotating refresh, issued after GitHub OAuth. *(`SIMPLE_JWT`: 30-min access, 30-day refresh, `ROTATE_REFRESH_TOKENS=True`, blacklist app.)*
- [x] Repo ownership enforced on every task/repo/branch lookup (`get_queryset` scoped to user). *(both viewsets filter `user=self.request.user`.)*
- [x] Path traversal blocked in all file tools (resolve + assert inside worktree root). *(`apps/agent/guards.py`, `context.py` via `resolve()` + `is_relative_to`.)*
- [x] Command execution allowlist for verify step (only detected formatters/linters/test runners). *(`apps/agent/verify.py` rejects any head token off the executable allowlist.)*
- [x] Per-user daily token/cost budget with hard stop.
- [x] Full audit log: every approval/rejection with actor + timestamp.
- [x] Rate limiting on API + Groq backoff on 429.
- [x] `.env.example` with placeholders only — **no real secrets committed**.

---

## 8. API Surface (implemented)

Auth notes: the OAuth callback never returns tokens in a redirect. It 302s to
`codepilotai://auth/callback?code=<one-time>`, and the app exchanges that code for
JWTs. The code is SHA-256 hashed with a 180s TTL and is destroyed on first use.

```
GET    /api/v1/auth/github/start          → { authorize_url, expires_in }
GET    /api/v1/auth/github/callback       → 302 → codepilotai://auth/callback?code=…
POST   /api/v1/auth/exchange              → { access, refresh, user }
# /api/v1/auth/refresh: PLANNED, not implemented — the app re-auths on 401 (Q29)
GET    /api/v1/auth/me
POST   /api/v1/auth/logout                → blacklists the refresh token
DELETE /api/v1/auth/github/disconnect

GET    /api/v1/repos                      → paged list (search, sort, filter)
POST   /api/v1/repos                      → create repo on GitHub
GET    /api/v1/repos/{id}
POST   /api/v1/repos/sync                 → import from GitHub
GET    /api/v1/repos/{id}/branches        → fetch from GitHub and mirror locally

POST   /api/v1/tasks                      → create on a new branch + enqueue
GET    /api/v1/tasks                      → history (search, filter, order)
GET    /api/v1/tasks/{id}                 → status, summary, cost
GET    /api/v1/tasks/{id}/changes         → proposed changes w/ diffs
POST   /api/v1/tasks/{id}/changes/{cid}/decide   → approve | reject | request_edit
GET    /api/v1/tasks/{id}/messages        → thread
POST   /api/v1/tasks/{id}/messages        → follow-up on the same branch
POST   /api/v1/tasks/{id}/cancel
GET    /api/v1/tasks/{id}/events          → JSON replay (?after_seq=)
GET    /api/v1/tasks/{id}/stream          → **SSE stream** of RunEvent
GET    /api/v1/schema, /api/v1/docs       → OpenAPI
GET    /api/v1/health
```

Cross-tenant access returns 404, never 403, so an ID probe cannot confirm that
another user's task or change exists.

Router-generated paths carry a trailing slash; the SSE stream is registered
outside the router and does not.

---

## 9. Mobile-First UX Requirements

- [x] Diff viewer built for small screens: file tabs → collapsible hunks → +/- highlighting
- [x] Approve/Reject anchored in thumb-reachable bottom bar
- [x] Streaming phase text ("Reading models.py", "Drafting change", "Running tests")
- [x] Expandable "thinking" trace (reasoning), collapsed by default
- [x] Test/lint result cards with actual output
- [x] Haptic feedback on apply success
- [x] Offline queue: task drafts survive app kill; WorkManager retries
- [x] Deep links: `codepilotai://auth/callback`, PR links open GitHub
- [x] Material 3 dynamic color, dark mode, accessibility labels, large touch targets
- [x] FCM push when a backgrounded task finishes (Phase 6) *— data-only messages from the
      backend (`apps/notifications`); the app registers its token, builds the notification,
      and deep-links to the task. Inert until a Firebase project is wired in
      (`app/google-services.json` + `FCM_*` credentials).*

---

## 10. Work Plan

### Phase 0 — Foundations
- [x] Python 3.13.15 installed (`.venv`, invoke as `..\.venv\Scripts\python.exe` from `backend/`)
- [x] Repo scaffold, `pyproject.toml`, `ruff`/`mypy` config
- [x] `.env.example` with placeholders: `GROQ_API_KEY`, `GITHUB_CLIENT_ID`,
      `GITHUB_CLIENT_SECRET`, `DJANGO_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`,
      `DATABASE_URL`, `REDIS_URL`, `ANDROID_DEEP_LINK_SCHEME`, `DEBUG`, `ALLOWED_HOSTS`
- [x] `config` settings split base/dev/prod/test
- [x] docker-compose for Postgres + Redis *(`docker-compose.yml`, infra only; local dev still
      runs SQLite + eager Celery by default; prod config for Postgres/Redis already written)*
- [x] Custom `User` model + migrations
- [x] Health endpoint, structlog config
- [x] `ruff check` clean, `mypy config apps` clean, 179 tests passing

### Phase 1 — Accounts & GitHub
- [x] GitHub OAuth web flow + signed state, one-time handoff code for the mobile redirect
- [x] Token exchange, Fernet encryption at rest
- [x] `GitHubClient` (httpx): list repos, create repo, list branches, create PR, merge
- [x] Retry/backoff with `Retry-After` + `X-RateLimit-Remaining` handling, 5xx typed as retryable
- [x] accounts + repositories APIs
- [x] agent task API (create, branch allocation, approve/reject/request-edit, messages, cancel, SSE)
- [ ] Token refresh on expiry *(stored, not yet exercised; OAuth Apps issue non-expiring tokens)*

### Phase 2 — Git service
- [x] `runner.py`: single choke point for every git subprocess — hardened allowlisted env, scratch `HOME`, `GIT_TERMINAL_PROMPT=0`, `safe.directory` scoped to the exact path, `executable=` so Windows never misreads argv[0]
- [x] `mirror.py`: per-repo **bare** clone (not `--mirror`), fetch refspec rewritten to `refs/remotes/origin/*`, fetch/prune/GC, token via `GIT_ASKPASS`
- [x] `credentials.py`: askpass helper script, token handed over stdin/env only — never in argv or on disk
- [x] `worktree.py`: add/remove per task, idempotent create, base resolution through remote-tracking refs, status / stage / commit / push / diff
- [x] branch slug allowlist + `codepilot/` prefix, orphan detection and workspace cleanup
- [x] `patch.py`: unified-diff validation, apply escalation (check → `--3way` → `--reject`), partial-application reporting, revert
- [x] Tests against local bare repos (`test_git_runner`, `test_git_mirror`, `test_git_worktree`, `test_git_branch`, `test_git_patch`)

### Phase 3 — Agent core
- [x] `LLMProvider` abstraction (Groq default) + message normalization for gpt-oss
- [x] Reasoning extraction (`include_reasoning`), phase-specific `reasoning_effort`
- [x] Repo map builder + instruction-file discovery + toolchain detection
- [x] Read-only tools (`list_dir`, `read_file`, `search_code`, `read_repo_instructions`) with path/secret guards
- [x] `propose` node + unified-diff parsing *(parser + validator shipped in Phase 2 as `apps/git/patch.py`; no `unidiff` dependency)*
- [x] Fuzzy diff applier with conflict reporting *(`apps/git/patch.py`: check → `--3way` → `--reject`, `.rej` surfaced per file)*
- [x] Prompt templates incl. anti-slop contract

### Phase 4 — Approval & execution
- [x] `ApplyChanges` service, idempotent
- [x] Verify service (formatter/linter/test autodetect + run)
- [x] Bounded repair loop
- [x] Commit message generation (conventional commits)
- [x] Push + PR creation with generated body
- [x] Celery tasks, task state machine, events + SSE

### Phase 5 — Android app
- [x] Gradle scaffold, Material 3 theme, ViewBinding
- [x] Dagger 2 graph + network stack (Retrofit/Moshi/OkHttp SSE)
- [x] Room cache
- [x] Auth: Custom Tab + deep link + token store
- [x] Repos screen (create + select + search)
- [x] Task input screen
- [x] Streaming run screen with phase text + reasoning
- [x] Approval screen: diff viewer + approve/reject/request-edit
- [x] Result screen: summary, PR link, test results, cost
- [x] History list + offline queue + WorkManager
- [x] FCM (Phase 6) *— device registration API + push dispatch on the backend; token
      registration, notification service, and permission/tap handling on Android*

### Phase 6 — Hardening
- [x] Unit/integration tests, coverage gate
- [x] Rate limiting, budget enforcement
- [x] Audit log UI
- [x] Performance: prompt caching tuning, mirror reuse
- [x] README + deployment guide

### Phase 7 — Nice to have
- [x] Voice input for task description *— trailing mic icon on the prompt field launches the
      system recognizer via `ACTION_RECOGNIZE_SPEECH` (no `RECORD_AUDIO` permission: the
      recognizer activity owns it); dictation is appended to what's typed by a unit-tested pure
      helper. Degrades to a toast when no recognizer is installed.*
- [x] i18n *— every user-visible string lives in `strings.xml`, including the previously hardcoded
      dynamic labels: `Labels` now returns `@StringRes` ids (phases/statuses), `EventAdapter`
      returns event-kind ids, and `AuditLog.summary` returns an `AuditLine` (resource id + args,
      nestable) that `AuditLog.render` resolves with a caller-supplied lookup — so `RunViewModel`
      passes ids through with no `Context` and `ResultFragment` renders via `getString`. Token
      counts use `<plurals>`; `supportsRtl` and a `localeConfig` give RTL + Android 13 per-app
      language. Adding a translation is now a `values-xx/` drop-in; server-supplied error text
      stays English by nature.*
- [x] On-device diff preview before push *— the approval screen is the gate. `ApprovalFragment`
      renders per-file tabs (`FileTabAdapter`) and a scrollable, colour-coded unified diff
      (`DiffParser` → `DiffLineAdapter`) for every proposed change, with approve / reject /
      request-edit per file. The backend only reaches `push_branch` +
      `create_pull_request` inside `runner._execute`, and `POST …/execute` returns 409 unless the
      task is `AWAITING_APPROVAL`, every change is decided, and ≥1 is approved — so no diff is
      ever pushed without having been on screen first.*
- [x] Optional web dashboard *— a read-only, staff-only operations console at `/dashboard/`
      (Django templates over the existing models, no SPA and no new dependency): platform-wide
      task counts by status, token+USD usage totals, a filterable/searchable task list, and a
      per-task page with the result/PR, usage, verification, proposed changes, and the run-event
      timeline. It reuses Django **session auth** and gates every view on `is_staff`; because
      CodePilotAI has no password login (a user *is* their GitHub identity), an operator is
      provisioned out-of-band and signs in at `/dashboard/login/`. Q38.*

---

## 11. Decisions (all resolved)

| # | Question | Decision |
|---|---|---|
| Q1 | GitHub **OAuth App** vs **GitHub App**? | **OAuth App** — matches "connect my account", no app review, full repo access. GitHub App only needed for multi-tenant distribution. |
| Q2 | Execution model: **Celery+Redis+SSE** vs synchronous HTTP? | **Celery+Redis+SSE**. Agent runs take 30s–3min; mobile apps get killed. |
| Q3 | Deliver as **branch + PR** or **direct push**? | **Branch + PR** with one-tap merge. Direct push is irreversible. |
| Q4 | Android DI: **Dagger 2** or **Hilt**? | **Dagger 2** — fully Java. Hilt's `@HiltAndroidApp` wants a Kotlin file. |
| Q5 | Android async: **RxJava 3** or **coroutines from Java**? | **RxJava 3** — idiomatic Java, no Kotlin dependency. |
| Q6 | Target `minSdk`? | **24** (Android 7.0) — ~99% of active devices. |

Additional decisions taken during build:

| # | Question | Decision |
|---|---|---|
| Q7 | Django 5.2 LTS or 6.1? | **5.2 LTS** — `drf-spectacular`, `cors-headers`, `environ`, `simplejwt`, `pytest-django` only declare support up to 6.0. |
| Q8 | How does the mobile app get its JWT? | **One-time handoff code.** The OAuth callback 302s to `codepilotai://auth/callback?code=…`; the code is SHA-256 hashed with a 180s TTL and exchanged once for JWTs. Tokens never appear in a redirect URL, browser history, or log. |
| Q9 | LangGraph checkpointer or Django as source of truth? | **Django.** An approval pause ends the run; resume seeds a new run seeded from the message history. No checkpointer to keep in sync. |
| Q10 | Local dev infrastructure? | **SQLite + eager Celery** for dev; Postgres/Redis config already written for prod. No Docker on this machine. |
| Q11 | `git clone --mirror` or `--bare`? | **`--bare`.** `--mirror` sets `remote.origin.mirror=true`, under which git refuses every refspec push with *"--mirror can't be combined with refspecs"* — the agent could clone but never deliver. The fetch refspec is rewritten to `+refs/heads/*:refs/remotes/origin/*` so fetches land in remote-tracking refs instead of colliding with branches a linked worktree has checked out. |
| Q12 | Where does a worktree's base commit come from? | **`refs/remotes/origin/<branch>` first**, falling back to `refs/heads/<branch>`. A bare clone copies `refs/heads/*` exactly once and fetch never touches them, so `refs/heads/main` is stale after the first fetch. |
| Q13 | Token in the clone/remote URL? | **No.** One URL template (`https://{username}@{host}/{full_name}.git`, username from settings) carries no secret; the token is supplied only by a `GIT_ASKPASS` helper. It never appears in argv, `.git/config`, or a reflog. An earlier two-template design (credentialed clone URL + clean remote URL) was removed for exactly that reason. |
| Q14 | How is the git subprocess hardened? | `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM`, a per-repo scratch `HOME`, `safe.directory` scoped to the exact path, an allowlisted env, and `git_executable()` passed as `executable=`. Windows `CreateProcess` reads argv[0] as the program name, so `git --no-pager …` used to fail with `WinError 2`. |
| Q15 | Force-push policy? | `push --force-with-lease`, never `--force`. The lease compares against the mirror's remote-tracking ref, so a human push made after the agent's last fetch is refused instead of silently deleted. |
| Q16 | How much of a model's patch is applied? | **Escalate, then accept deliberate partial success.** `--check` → `--3way` → `--reject`. Nine good hunks are delivered with a precise per-file `.rej` report rather than thrown away; `allow_partial=False` restores all-or-nothing for callers that need it. |
| Q17 | Can a model blank a file? | **Rejected before git runs.** A patch whose every hunk contributes zero lines to the new side of a file that is *not* being deleted is refused by `validate_patch`; whole-file deletion (`+++ /dev/null`) stays legal. |
| Q18 | How is the git layer tested without GitHub? | Local bare repos in `tmp_path` plus `GIT_ALLOW_LOCAL_REMOTE` (default `False`, `True` in test settings) controlling `protocol.file.allow`, so the real URL-template → clone → fetch → push path runs for real and never touches the network. |
| Q19 | How does the agent talk to Groq — `langchain-openai` or raw HTTP? | **Raw `httpx` (`GroqProvider`).** `langchain-openai` 1.6.7's `base.py` explicitly does **not** extract `reasoning_content`/`reasoning_details` ("use a provider-specific subclass"), which would throw away the whole `include_reasoning` requirement. The provider uses only `langchain-core` message types + prompt templates, and the `langgraph` loop. |
| Q20 | What happens to `langchain`, `langchain-openai`, `unidiff`? | **Removed from `pyproject.toml` and uninstalled.** Nothing imports them: the git layer hand-rolls its unified-diff parser, and the provider is raw httpx. `langchain-core` + `langgraph` remain. |
| Q21 | How is the single-tool-call rule enforced? | **In the loop, not the prompt.** `call_model` raises `ModelOutputError` on a parallel-tool-call completion; `run_tool` requires exactly one pending call. `finish()` ends the run via a conditional edge so no farewell model call is spent. |
| Q22 | How does a proposal become reviewable? | `propose_change` gates on the path sandbox + secret policy, `validate_patch` (structure, hunks, no silent blanking), and a strict one-file-one-patch rule (`patch_files` must name exactly the declared path). Failures return as **tool errors the model can repair**, with a bounded `max_repairs` budget (`apps/agent/propose.py`). |
| Q23 | Partial application inside the approval pipeline? | **Treated as a conflict, not a delivery.** `ApplyOutcome.needs_attention` (any hunk had to use `--reject`) reverts every touched file to HEAD via the new `revert_files` (`git apply --reverse` is all-or-nothing and refuses a partially-applied patch, so undo is per-file: `ls-files` + `checkout`, unlink for patch-created files), deletes the `.rej` files, and marks the row FAILED with the exact rejected hunk text for the repair phase. Direct callers who opt into partial delivery still get Q16 behaviour. |
| Q24 | How does a failed repair leave the tree? | **Snapshot-before-apply rollback.** `RepairController` copies each file before a repair patch lands and restores on failure — a HEAD reset would wipe the already-approved change on the same file. `git apply --reverse --check` idempotency is retained only for crash-retry (a worker that died mid-apply picks up cleanly on retry). |
| Q25 | What commands is verification allowed to run? | **Only what detection suggests.** `run_verification` derives commands from `suggest_commands` plus a first-token allowlist (`ALLOWED_COMMAND_BINARIES`); a suggestion outside it refuses before `subprocess` (GuardViolationError). Runs ordered format → lint → test; a missing binary reports 127, a timeout 124. The model can never invent a command (S603/S607 scoped per-file in `pyproject.toml`). |
| Q26 | How does execution start? | `POST /tasks/{pk}/execute` **409s** unless the task is `awaiting_decision` with ≥1 approved change and none pending; it then dispatches `run_agent_task.delay(pk)` when `AGENT_PIPELINE_ENABLED` (default `True`), else emits a LOG event only. Execution is a *new* run seeded from the message history (Q9 — no checkpointer), through apply → verify → bounded repair → conventional commit (deterministic subject, empty-model fallback, no-verify) → push + PR. |
| Q27 | Crash between row-write and tree-write? | **Recoverable by construction.** Row transitions are transactional while the tree is already patched; a worker death mid-apply leaves the row APPROVED and the tree patched, and the next apply's idempotency check (`--reverse --check`) detects the applied patch and marks the row APPLIED instead of raising a duplicate conflict. |
| Q28 | Moshi reflective or codegen? | **Reflective.** Codegen (KSP) would pull in a Kotlin toolchain; the Java-only build uses reflective mode with public-field POJOs, no-arg ctors and `@Json(name=…)`. `TaskDetailDto` merges the summary + detail wire shapes. |
| Q29 | What happens on a 401 (there is no `/auth/refresh` backend route)? | **Re-auth via Custom Tab.** The access token is short-lived (30 min); on expiry the app re-runs the GitHub OAuth flow. The "backend refresh endpoint" improvement stays unticked. |
| Q30 | How does the SSE stream survive disconnects? | **`after_seq` replay, not `sse-retry`.** The Room-cached event table is the durable buffer; on failure the client resubscribes from the last cached seq (bounded retries in `RunViewModel`), so reconnects never duplicate or lose events. |
| Q31 | Offline task send? | **Room `task_drafts` + `DraftSyncWorker`.** The prompt is persisted the instant "Start" is tapped; a periodic (15 min, `NetworkType.CONNECTED`) WorkManager worker pushes drafts in order. Workers resolve from the Dagger graph via `CodePilotWorkerFactory` + `Configuration.Provider`. |
| Q32 | Where do ViewModels come from? | **Dagger-exposed `ViewModel.Factory` classes** (no Hilt). Each `*ViewModel.Factory` is `@Module`-provided in `ViewModelModule` and consumed via `AppComponent.*ViewModelFactory()` accessors; the graph is one `AppComponent` with `@BindsInstance Application`. |
| Q33 | App icon? | **Single vector at `@drawable/ic_launcher`** referenced directly in the manifest — no PNG density sets or adaptive-icon XML needed at `minSdk 24`. |
| Q34 | Navigation & deep links? | **Single activity + Navigation Component.** `singleTask` `MainActivity` owns the `codepilotai://` handler (forwards the code to an activity-scoped `AuthViewModel`); `AuthFragment` owns auth→repos navigation so the exchange never double-navigates. |
| Q35 | Verification wire shape? | **Fixed in DTO:** `{passed, skipped, outcomes:[{kind, command, passed, returncode, duration_seconds, output}]}` — mirrors the Phase-4 `VerificationResult` serializer exactly. |
| Q36 | `String.isBlank()`/`strip()` are API 33, but `minSdk` is 24 — how is that reconciled? | **Avoid the API rather than add desugaring.** Android lint does not flag `java.*` core-library methods (`api-versions.xml` marks them `since="33"`), so the calls compiled and lint stayed green while risking `NoSuchMethodError` on API 24–32. Since `isBlank` was the *only* affected call (no `java.time`/`stream`/`Optional` anywhere), every `.isBlank()` became `.trim().isEmpty()` — both are ancient APIs, so no `desugar_jdk_libs` dependency and no enabling of core-library desugaring. Semantically equivalent for the (URLs, tokens, prompts) inputs involved. |
| Q37 | How is dynamic text made translatable when the ViewModel has no `Context`? | **Return `@StringRes` ids and resolve in the UI.** `Labels`/`EventAdapter` map wire enums → resource ids; `AuditLog.summary` returns a small `AuditLine` (id + args, where an arg may nest another `AuditLine`) and a pure `AuditLog.render(line, lookup)` resolves the tree. `RunViewModel` therefore emits `LiveData<Integer>` (default `R.string.phase_waiting`) and `ResultFragment` supplies `(id, args) -> getString(id, args)`. Keeps the formatters pure and unit-testable (tests assert ids + structure) with no `Context` leaking into the ViewModel layer. Per-app language uses a `res/xml/locales_config.xml` referenced by `android:localeConfig`. |
| Q38 | What is the "optional web dashboard", and how is it authenticated? | **A read-only, staff-only operations console rendered with Django templates — no SPA, no new dependency, no models/migrations.** CodePilotAI has no password login (users are GitHub identities with unusable passwords), so the console reuses `django.contrib.auth` session auth and gates every view on `is_staff`; an operator is provisioned out-of-band (`user.set_password(...)` + `is_staff=True`) and signs in at `/dashboard/login/`. It is deliberately **platform-wide** because it is an internal ops surface, not a second product UI: task counts by status, usage totals, a filterable task list, and a per-task page (result/PR, tokens+USD, verification, proposed changes, event timeline). Verification/diff payloads are printed verbatim (`prettyjson`) since their shape evolves. Lives in `apps/dashboard/` (+ `templates/dashboard/`), covered by `tests/test_dashboard.py`. |

---

## 12. Log

- **2026-10-01** — Session 1. Verified Groq model status/specs and gpt-oss API constraints
  (no `reasoning_format`, no parallel tool calls, explicit `reasoning_effort`).
  Confirmed tool env: Python **missing**, Git 2.55, Java 17, no Docker, no system Gradle.
  Wrote architecture + full work plan. Awaiting answers to Q1–Q6 and Python install.
- **2026-10-05** — Sessions 2–3. Built the backend through the end of Phase 1.

  **Toolchain.** Python 3.13.15 installed to `D:\downloads2` (invoke via `py -3.13`);
  `.venv` at repo root. Dev deps need `pip install --group dev` (PEP 735), not `.[dev]`.
  Removed `types-cryptography` — it does not exist; `cryptography` ships its own types.

  **Build order.** `config/` (settings split, structlog, throttling, pagination, error envelope)
  → models (`accounts.User` with Fernet-encrypted tokens, `OAuthHandoff`,
  `repositories.{Repository,Branch}`, `agent_tasks.{AgentTask,ProposedChange,RunEvent,Message}`)
  → `apps/github/` (httpx client, OAuth, typed exceptions)
  → accounts, repositories, and agent_tasks APIs including the SSE stream with
  `after_seq` replay and a 15s heartbeat.

  **Two API defects found and fixed while building.** The branch list was reachable
  at two different URLs (`repos/<id>/branches` and the router's
  `repos/<pk>/branches/`), serving two views; dropped the manual route and kept the
  viewset action. And `AgentTask.is_in_progress` included `QUEUED`, which made the
  follow-up endpoint reject instructions on a task no worker had picked up yet —
  split into `is_in_progress` (client should keep watching) and `is_busy` (a worker
  owns it, so a follow-up would be lost).

  **Test tooling bug worth recording.** The suite was originally written against
  `responses`, which only patches `requests.adapters.HTTPAdapter.send` and has **no
  httpx support**. Every "mocked" GitHub call was silently escaping to the live API.
  Replaced with `pytest-httpx`, which raises on any unregistered request — so a
  silent network escape can no longer pass. Two follow-on adjustments: `pytest-httpx`
  matches `url` literally, so a helper fixture matches the path and tolerates the
  `?per_page=…&page=…` query every paginated call carries; and retry backoff sleeps
  are neutralised by a `no_sleep` fixture.

  **Two production bugs the tests caught.** `_build_error` scrubbed the raw
  response text but then overwrote `detail` with the unscrubbed `payload["message"]`,
  so a token echoed inside a JSON error body would leak into logs and API responses —
  the structured field is now scrubbed too. And 5xx was mapped to a plain
  `GitHubError` and therefore never retried; added `GitHubServerError` so transient
  server failures back off and retry while 401/404/409 stay deterministic.

  **Three more production bugs, found by writing the accounts-API tests.** All three
  sat on the sign-in path, which had no tests until now — sign-in could not have
  worked in production.

  1. `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` listed `github` and `agent_task` but
     not `auth`, which the handoff-exchange view declared as its scope. A missing
     scope is not a startup error, it is a `KeyError` on the first request, so
     **every token exchange returned 500**. Fixed, and the settings block now
     documents why each scope exists.
  2. `UserSerializer` declared `username = SlugRelatedField(slug_field="username")`
     on what is a plain `CharField` on the model. `SlugRelatedField` applies
     `getattr` twice and died with `'str' object has no attribute 'username'`, so
     `/auth/me` **and** the exchange response **both** returned 500. DRF's
     `ModelSerializer` already handles `username` correctly; the declaration is gone.
  3. The deep-link `Location` header was built as `quote(url, safe=":/")` on a URL
     that `urlencode` had *already* escaped. That turned `?`, `&`, `=` into
     `%3F`, `%26`, `%3D`, so **both** the success and the failure redirects arrived
     at the app with no query string whatsoever — sign-in could never complete, and
     a failed sign-in gave the UI nothing to show. Both paths now go through one
     `_app_callback(**params)` helper.

  **Current state.** `ruff check` clean, `ruff format` clean, `mypy config apps` clean
  across 46 source files, `manage.py check` clean, `makemigrations --check` reports no
  changes, **214 tests passing** (added `tests/test_accounts_api.py` covering the
  full OAuth round trip, handoff replay/expiry, me, logout, and disconnect).
  `AGENT_PIPELINE_ENABLED` is `False` until `apps.agent` exists, so task creation
  queues cleanly and reports that in an event rather than dispatching a run that
  cannot succeed.

  **Next.** Phase 2 (`apps/git/`: mirror + worktree management via `git` subprocess
  with `GIT_ASKPASS`, tested against local bare repos), then Phase 3 (`apps.agent/`:
  gpt-oss message normalization, read-only tools with path/secret guards, unified-diff
  propose/apply) and flip `AGENT_PIPELINE_ENABLED=True`.
- **2026-10-06** — Session 4. Phase 2 complete: `apps/git/` (`runner`, `credentials`,
  `mirror`, `worktree`, `patch`, `errors`) with **363 tests passing**, `ruff check`
  clean, `ruff format` clean, `mypy config apps` clean across 54 source files,
  `manage.py check` clean, `makemigrations --check` reports no changes. The git app
  has no models — Django stays the source of truth for task state, git owns the tree.

  **Eight production bugs the tests caught**, in the order they surfaced:

  1. `git_executable()` had to be passed as `subprocess.run(executable=…)`.
     Windows `CreateProcess` uses argv[0] as the *program name*, so
     `["--no-pager", "-c", …]` raised `FileNotFoundError [WinError 2]` — every git
     call failed on Windows and no test had run one yet.
  2. The token was being placed in the remote URL, which lands in argv (visible in
     `ps`) and in `.git/config` on disk. Replaced by a single credential-less URL
     template plus a `GIT_ASKPASS` helper.
  3. `git clone --mirror` sets `remote.origin.mirror=true`, and git then refuses
     **every** refspec push with *"--mirror can't be combined with refspecs"*.
     The agent could clone but never deliver a branch. Now `--bare`.
  4. The mirror's fetch refspec originally targeted `refs/heads/*`, so
     `git remote update` failed with *"refusing to fetch into branch … checked out
     at …/worktrees/N"* whenever a task worktree existed. Rewritten to
     `refs/remotes/origin/*`.
  5. …which exposed the next one: a bare clone copies `refs/heads/*` **once**, and
     fetch never updates them, so `refs/heads/main` stayed at the clone-time commit
     forever. Every worktree would have branched from a stale base. Base resolution
     now prefers `refs/remotes/origin/<branch>`.
  6. `git remote update --quiet` is not a no-op — `remote update` accepts only
     `--prune`, and `--quiet` is a hard error.
  7. `status --porcelain -z` emits rename records as `newPath\0origPath` — the
     destination comes *first*. The parser read them in the opposite order, so a
     rename was reported as the file it left.
  8. `create_worktree` was not idempotent: a second call for the same task died
     with *"a branch named … already exists"* instead of returning the existing
     worktree.

  **Design corrections found while writing `test_git_patch.py`.** `patch_files`
  read only the `+++` side of each file header, so a deletion (whose `+++` is
  `/dev/null`) vanished from the change report the user approves against. And the
  original "blanking patch" heuristic — *are all `+` lines empty?* — was wrong in
  both directions: it passed patches that emptied a file and failed legitimate
  single-function removals. It is now a proper `---`/`+++`/`@@` parser
  (`file_sections`) that flags a file whose every hunk contributes zero lines to
  the new side while the file itself survives.

  **Two test-side misconceptions worth recording.** `push --force-with-lease`
  protects against the remote differing from what *you last fetched* — it does not
  protect against a human pushing to a branch you have already fetched. The
  clobber test had to stop fetching after the human's push to model the real
  race. And a hand-written hunk header must match the file exactly
  (`@@ -1,2 +1,2 @@` for a two-line file, `+` prefixes on added lines), otherwise
  `git apply` reports *"corrupt patch"* — several fixtures were silently
  malformed.

  **Next.** Phase 3 (`apps/agent/`): gpt-oss message normalization, read-only tools
  with path/secret guards, the `propose` node feeding `apps/git/patch.py`, prompt
  templates with the anti-slop contract — then flip `AGENT_PIPELINE_ENABLED=True`.
- **2026-10-06** — Session 5. Phase 3 core complete: `apps/agent/` now ships the whole
  agent brain behind the approval gate, **524 tests passing** (was 363), `ruff check`
  clean, `ruff format` clean, `mypy config apps` clean across 69 source files,
  `manage.py check` clean, `makemigrations --check` reports no changes (the agent app
  has no models — Django stays the source of truth).

  **What was built this session.** `errors.py` (renamed to N818 convention:
  `GuardViolationError`, `BudgetError`, `ProviderRateLimitError`), `guards.py`
  (containment + secret policy, symlink-safe, Windows separator normalisation),
  `context.py` (repo map with `--`/`|--` tree markers, instruction-file discovery
  limited to root + `.cursor/rules/` + `.github/copilot-instructions.md`, toolchain
  detect + suggested commands), `tools.py` (the four read-only tools, every path
  funnelling through `resolve_within` + guard checks; `list_dir` shows dirs with `/`,
  `read_file` paginates with numbered lines and refuses binaries, `search_code` is
  case-insensitive by default with glob/regex flags), `prompts.py` (anti-slop
  contract, `PromptContext`, per-phase briefs + the §6 effort map), `budget.py`
  (token + wall-clock budget with `clamp_max_tokens` per call), `providers/`
  (`base.py` types, `normalize.py` thread repair, `groq.py` raw-httpx client with
  retry/backoff mirroring `GitHubClient` and keys scrubbed from error text).

  **The LangGraph core.** `graph.py` compiles one `StateGraph` (`call_model` ⇄
  `run_tool`) shared by every phase. The single-tool-call rule is *enforced in the
  loop*: a two-tool-call completion raises `ModelOutputError`, the round cap lives
  in state (`tool_calls_remaining` → `stopped_reason: "tool_calls_exhausted"`),
  budgets are checked before every model call and every tool execution, and a
  12,000-token per-call cap sits on top of the run budget. `explore.py` is the
  read-only loop at low effort; `propose.py` is the write-*proposal* loop at high
  effort with two tools only — `propose_change(path, change_type, rationale, patch)`
  (sandbox + secret policy + `validate_patch` + strict one-file-one-patch, failures
  fed back as repairable tool errors with a bounded repair budget) and
  `finish(summary)` (sets the summary and short-circuits to END so no farewell
  model call is spent).

  **Bugs the tests caught.** `_line_matcher` lowercased the search pattern but not
  the line, so case-insensitive search only worked when the match was lowercase
  (fixed). `_tool_calls_from_ai` only understood wire-shape `{id, function:{name,
  arguments}}`, but langchain-core normalises `tool_calls` to `{id, name, args}` —
  assistant turns rebuilt locally lost their arguments (now handles both, plus
  pydantic `ToolCall` models). The `run_tool → call_model` edge was unconditional,
  so after `finish()` the graph spent one extra (unscripted) model call; it is now a
  conditional edge that ENDs when `final_summary` is set. And `suggest_commands`
  read `node_runner` which `detect_toolchain` never set — npm-degraded commands
  would be suggested for yarn/pnpm repos (fixed).

  **Dependency cleanup (Q19/Q20).** `langchain`, `langchain-openai`, and `unidiff`
  removed from `pyproject.toml` and uninstalled from the venv; only `langchain-core`
  message types + `langgraph` remain.

  **Test infra.** Shared `ScriptedProvider` + `tool_completion`/`answer_completion`
  fixtures in `conftest.py` drive the graph tests without a socket. 161 agent tests:
  guards (secret/ignored/containment, incl. symlink escape skipped on Windows),
  context (repo map, conventions, toolchain), tools (all four, error paths), prompts
  (contract ordering, deterministic rendering), budget, providers (pytest-httpx:
  request shape incl. `include_reasoning`/`reasoning_effort`/no-`stop`, 429/5xx
  retry, 401 never retried, key scrubbing), graph loop (tool round-trips, parallel
  violation, round cap, budget abort, reasoning trail), propose/explore end-to-end
  (validated proposals, repair loop, one-file rule, secret/traversal refusals).

  **Next.** Phase 4: `ApplyChanges` service (approval-gated), verify with bounded
  repair, conventional commits, push + PR — then Celery tasks + SSE wiring and flip
  `AGENT_PIPELINE_ENABLED=True` once the pipeline is real.
- **2026-10-10** — Session 6. Phase 4 complete: the approval-gated execution
  pipeline is real and `AGENT_PIPELINE_ENABLED` now defaults to `True`. **590 tests
  passing / 1 skipped** (was 524), `ruff check` clean, `ruff format` clean,
  `mypy config apps` clean across 75 source files, `manage.py check` clean,
  `makemigrations --check` reports no changes (the agent app still has no models).

  **What was built.** `apps/agent/apply.py` (idempotent approved-change apply:
  `--check` → `--3way` → `--reject` escalate, already-applied detection via
  `--reverse --check`, a partial application reverts every touched file and marks
  the change FAILED with exact rejected hunk text), `verify.py` (toolchain-derived
  commands only, first-token binary allowlist, ordered format → lint → test,
  127/124 outcomes), `repair.py` (bounded `RepairController` that snapshots each
  file before a repair patch and restores it on failure — never a HEAD reset),
  `commit.py` (conventional-commit `conventionalise` + `deterministic_subject`,
  empty-model fallback, `--no-verify` commit, `.codepilotai-home` excluded),
  `push_and_pr.py`, and `runner.py` (`run_planning_stage` / `run_execution_stage`
  with an error-code map) wired to Celery (`apps/agent/tasks.py`,
  `run_agent_task.delay`). The task API gained `POST /tasks/{pk}/execute`
  (`v1:agent-task-execute`): 409 unless `awaiting_decision` with ≥1 approved and
  none pending; dispatches the Celery run, or emits a LOG event when the pipeline
  is disabled.

  **Bugs the tests caught.**

  1. `git apply --reverse` is all-or-nothing and *refuses* a patch that only
     partially applied — undoing a partial apply had to be per-file
     (`revert_files`: `ls-files` + `checkout`, unlink for patch-created files).
  2. `PatchConflictError` subclasses `GitError`, **not** `GitCommandError`, and
     both are raised by the apply path — apply/repair originally caught only the
     latter and let a conflict escape as an unhandled error.
  3. A repair must never reset a file to HEAD: the already-approved change on the
     same file would be wiped. Snapshot-before-apply rollback solves it.
  4. git silently truncates a hunk beyond the line count its header claims, so a
     hand-written test patch with a wrong `+a,b` header applies *partially* — every
     fixture hunk header must match its payload exactly.
  5. Static type tightening: `revert_files` took `list[str]` but `ApplyOutcome`
     carries `tuple[str, ...]` (widened to `Sequence[str]`); `PromptContext`
     `instructions` is now `Sequence[InstructionFile]` for the same reason.

  **Test counts by theme.** apply 12, verify 12, repair 12, commit 16, runner 9
  (planning → AWAITING_APPROVAL with persisted proposals/transcript/events/reasoning
  collapse, and execution → PUSHED+PR via a monkeypatched
  `GitHubClient.create_pull_request`, repair fail-then-pass round, error mapping),
  tasks API `TestExecute` 5 (409 gates + Celery dispatch + LOG-when-disabled).
  `tests/test_agent_apply.py` crash-recovery test simulates a worker death between
  tree-write and row-commit and proves the retry marks the row APPLIED instead of
  raising a duplicate conflict (PT012/SIM117 conflict there resolved with a plain
  try/except instead of `pytest.raises`).

  **Current state.** Phase 4 checklist fully ticked, `.env.example` documents
  `AGENT_PIPELINE_ENABLED`, keys remain placeholders. Celery + Redis are still the
  only missing runtime piece on this machine (dev uses eager mode; config already
  written for real workers).

  **Next.** Phase 5 — the Android app: Gradle + Material 3 + ViewBinding, Dagger 2
  + Retrofit/Moshi/OkHttp SSE, Room, OAuth handoff deep link, repos/task/run/
  approval/result screens.
- **2026-10-10** — Session 7. Phase 5 (Android app) built end-to-end. First
  `assembleDebug` **and** `testDebugUnitTest`/`testReleaseUnitTest` are green:
  13 JVM tests (DiffParser 4, ApiException 6, SseClient 3 against a real
  MockWebServer). Toolchain: Java 17 (no Kotlin), AGP 8.13.0, Gradle 8.14.3
  wrapper, `minSdk 24` / `targetSdk 36`, KTS.

  **What was built.** Dagger graph (`AppModule`/`NetworkModule`/`DatabaseModule`/
  `RepositoryModule`/`ViewModelModule`, `@BindsInstance Application`, Dagger-
  constructed `CodePilotWorkerFactory`), full network stack (Retrofit 2 +
  Moshi reflective + OkHttp + `okhttp-sse`, `AuthInterceptor`, `AuthApi`/
  `ReposApi`/`TasksApi`), Room cache (repos, tasks, events, changes,
  `task_drafts`), `EncryptedSharedPreferences` `TokenStore` with a plain-prefs
  fallback, 7 ViewModels + 7 fragments + 4 adapters + 2 dialogs, the 7-
  destination nav graph with the `codepilotai://` deep-link handler, the small-
  screen diff viewer (file-tab chips → collapsible hunks → +/- colours), the
  thumb-reachable approve/reject/request-edit bottom bar, streaming phase text
  + collapsible reasoning trace, result cards (summary/verification/PR/stats),
  history with pull-to-refresh, and the offline queue (`DraftSyncWorker`,
  15-min periodic + network-gated).

  **First-build error hunt (all fixed).** AAPT rejects inline `-@dimen/…`
  negatives → a `screen_padding_neg` dimen. The repositories lived outside the
  graph → new `RepositoryModule`. `AgentTask` had to lose `final` for
  `AgentTaskDetail extends`. `android.app.Application` needs `@BindsInstance`.
  `Completable.count()` does not exist → `syncPending` restructured via
  `andThen(Single.just(count))`. `DynamicColors.applyToActivitiesIfAvailable`
  takes an `Application`, not an activity. A blank-final in `TokenStore`'s
  catch block fails definite-assignment → local-then-final. Flowable needs a
  `TestSubscriber` (not `TestObserver`), and RxJava 3's `BaseTestConsumer` has
  `awaitCount(int)` but no timeout variant and no `awaitTerminalEvent()`.

  **Current state.** Phase 5 checklist fully ticked (FCM remains Phase 6).
  API keys and `API_BASE_URL` stay placeholders injectable via BuildConfig
  (default `http://10.0.2.2:8000/api/v1` for the emulator). On-device E2E
  against the live local backend is not yet exercised — no emulator on this
  machine.

  **Next.** Phase 6 hardening: FCM, lint/CI, coverage, rate-limit + budget UI;
  or run the app against the Phase-4 backend once an emulator/device is
  available.
- **2026-10-10** — Session 8. Hardening pass on the Android app. First-ever
  `gradlew lint` run surfaced **1 error + 95 warnings**; now **0 errors,
  37 warnings** (all of them deliberate version-pin advisories from the locked
  stack).

  **Lint fixes.** The one error: the manifest shipped the default
  `androidx.startup.InitializationProvider` for WorkManager even though
  `CodePilotApp` implements `Configuration.Provider` (on-demand init) — the
  provider is now `tools:node="remove"` so WorkManager cannot double-initialize.
  Real warnings fixed, not suppressed: `DefaultLocale` ×3 (ReposFragment search
  now uses `Locale.ROOT`), `SetTextI18n` ×2 (RunFragment reasoning-toggle
  concatenation → new `reasoning_toggle_count` format string), `ObsoleteSdkInt`
  (dropped a `tools:targetApi="23"` that minSdk 24 makes pointless), and
  `PluralsCandidate` (`tokens_used` became a real `<plurals>` resource +
  `getQuantityString` in ResultFragment; `pr_created` suppressed as
  English-only). Dead weight removed: 22 unused strings, 3 unused dimens,
  3 unused colors, and the orphaned `item_change.xml` layout.

  **Dark mode actually works now.** The `md_dark_*` palette was defined in
  `colors.xml` but never wired — `Theme.Material3.DayNight` rendered the light
  palette in night mode. Added `values-night/themes.xml` (dark palette,
  light status-bar icons off), which both fixes night rendering and makes the
  dark colors used. The three streaming/small adapters keep
  `notifyDataSetChanged` on purpose, now documented with a justified
  `@SuppressLint("NotifyDataSetChanged")`.

  **Tests up 13 → 23.** Added `WireEnumsTest` (4: TaskStatus/TaskPhase wire
  round-trips + terminal/decided boundaries), `LabelsTest` (4: phase/status
  text for every enum + null fallbacks), `TaskRepositoryMappingTest` (2: the
  pure Room-entity → `ProposedChange` mapping).

  **Coverage gate.** JaCoCo 0.8.12 wired: `gradlew jacocoTestReport` → HTML +
  XML under `app/build/reports/jacoco/`. First baseline: **line 7.9%
  (228/2880), branch 11.6% (111/961), instruction 8.5%**. Low because the
  report measures every app class and the tests are deliberately pure-JVM
  (domain enums, Labels, DiffParser, ApiException, SseClient,
  TaskRepository.toDomain); Android/Room/Dagger/UI layers need Robolectric or
  device tests to move the number. AGP 8.13 gotchas recorded: the
  `unitTests.all { jacoco {} }` DSL was renamed/pulled (dropped the
  now-dead `includeNoLocationClasses` knob), exec data lands at
  `build/jacoco/testDebugUnitTest.exec`, and javac output nests at
  `intermediates/javac/debug/compileDebugJavaWithJavac/classes`.

  **Docs.** `android/README.md` written: prerequisites, build/test/lint
  commands, `ANDROID_API_BASE_URL` override, deep-link wiring, screen-by-screen
  flow, code layout, placeholder-keys note.

  **Current state.** `assembleDebug` + 23 JVM tests + `lint` + `jacocoTestReport`
  all green (debug APK ≈ 9.7 MB). Phase 6 "Unit/integration tests, coverage
  gate" ticked; the other Phase 6 items (FCM, rate-limit/budget UI, audit-log
  UI, performance tuning, deployment guide) remain open.

  **Next.** FCM (Phase 6), rate-limit + budget UI, audit-log UI; backend
  `/auth/refresh` still unticked; on-device E2E once an emulator/device exists.
- **2026-10-11** — Session 9. Phase 6 hardening: the two security items left
  open in §7 plus the audit-log UI.

  **Daily budget hard stop.** `apps/agent_tasks/usage.py` rolls up each user's
  token spend for the current UTC day from the tasks whose run *started* that
  day, and `assert_daily_budget()` compares it to `AGENT_DAILY_TOKEN_BUDGET`.
  `DailyBudgetExceededError` maps to a `429 daily_budget_exceeded` envelope in
  `config/api/exceptions.py`. The gate runs before any mutation in task
  creation and in follow-up messages, and again inside the single `_enqueue`
  funnel (so `execute` is covered too): a rejected request leaves no queued
  task and no half-written message behind. `DailyUsage.total_cost_usd` prices
  the same tokens at the locked $/1M rates.

  **Audit actor.** `ProposedChange.decided_by` (nullable FK, `SET_NULL`) plus
  migration `0002`; `approve()`/`reject()` take an optional actor; the decide
  endpoint attributes the decision, and the `change_decided` event now carries
  `actor_id`, `actor_username`, and `decided_at`. `ProposedChangeSerializer`
  exposes `decided_by` and `decided_by_username`.

  **Follow-up dispatch bug.** The messages endpoint flipped a task back to
  `QUEUED` but never handed it to a worker, so a follow-up never ran. It now
  calls the same `_enqueue` funnel as creation (which also routes it through
  the budget gate).

  **Android audit UI.** `util/AuditLog` (pure, unit-tested) filters the cached
  event stream to lifecycle/decision/verification events and formats each line
  with actor attribution; the result screen gains a collapsible "Activity log"
  card that backfills the event cache from `GET /tasks/{id}/events/` so it is
  complete even for tasks opened from history.

  **Verification.** Backend: 599 passed / 1 skipped; ruff + ruff-format and
  mypy clean (76 files). Android: `assembleDebug` + 32 JVM tests (9 new
  AuditLog cases) + `lint` (0 errors / 37 warnings) + `jacocoTestReport` all
  green; coverage rose to line 299/3023 (9.9%), branch 165/1079 (15.3%),
  instruction 1472/13740 (10.7%).

  **Next.** Performance tuning (prompt caching, mirror reuse), root README +
  deployment guide, FCM; backend `/auth/refresh` still unticked.

- **2026-10-11** — Session 10. Docs, deployment, and prompt-cache accounting.

  **README + deployment guide.** New root `README.md` (what it is, the
  end-to-end flow, repo layout, quick starts for backend + Android, the full
  config table, the API surface, quality gates, security posture) and
  `docs/DEPLOYMENT.md` (topology, prod env with the boot guards, the ASGI web
  tier because SSE must not be buffered, Celery workers, an nginx config with
  `proxy_buffering off` for the stream route, OAuth callback, static files,
  backup/scaling, a security checklist). `docker-compose.yml` brings up Postgres
  + Redis for local/self-hosted use and closes the deferred Phase 0 item.

  **Prompt-cache accounting.** Groq reports cache hits at
  `usage.prompt_tokens_details.cached_tokens` and bills cached input at half
  rate; both were being discarded. `Usage`/`Budget` now carry `cached_tokens`,
  the provider parses and logs them with a hit rate, and a new single-source
  `apps/agent_tasks/pricing.py` prices every usage record -- so
  `AgentTask.cost_usd` is populated for the first time (it was always zero) and
  `DailyUsage.total_cost_usd` applies the same discount. `cached_tokens` is on
  the task API and the Android result screen now shows "N tokens reused from the
  prompt cache".

  **Verification.** Backend: 607 passed / 1 skipped (new pricing/provider/budget/
  task-cost cases); ruff + ruff-format and mypy clean (77 files); migration
  `0003_agenttask_cached_tokens`. Android: `assembleDebug` + `test` + `lint` all
  green (0 errors / 37 warnings, unchanged).

  **Next.** FCM (Phase 6, needs a Firebase project); backend `/auth/refresh`
  still unticked; on-device E2E once an emulator/device exists.

- **2026-10-11** — Session 11. FCM push, backend and Android, end to end.

  **Backend (`apps/notifications`).** `DeviceToken` (token as natural key, FK to
  the user, `platform`, `last_seen_at`) with a `register` upsert; `POST /devices`
  (201 create / 200 refresh) and `POST /devices/unregister` (204) behind the
  normal auth. A transport abstraction keeps Firebase out of the core: the
  default `NullPushTransport` is a no-op, and `FcmPushTransport` speaks FCM HTTP
  v1 (`oauth2` refresh + `messages:send`) hand-rolling the RS256 service-account
  assertion with the `cryptography` already in the tree — no `google-auth`
  dependency. `build_transport()` picks the real one only when
  `FCM_PROJECT_ID` + credentials are set. Dispatch is hooked into the single
  status funnel `apps/agent_tasks/events.set_status` via `_dispatch_push`, which
  notifies `awaiting_approval` / `pushed` / `merged` / `failed` (not
  `cancelled`), is best-effort (never raises), and defers through
  `transaction.on_commit` when inside an atomic block. Messages are **data-only**
  (title/body/`channel_id` in `data`, `android.priority=high`, no `notification`
  block) so the client owns presentation and can deep-link to the task.

  **Android.** `firebase-messaging` (26.0.0) is a normal dependency, and
  `com.google.gms.google-services` (4.5.0) is declared at the root and applied in
  `app/build.gradle.kts` **only when `app/google-services.json` exists** — a
  clean checkout still builds. The new `data/api/DevicesApi` +
  `DeviceTokenRequest`/`DeviceTokenDto` and `data/repo/DevicesRepository`
  register the token (deduped against the last accepted one, remembered in a
  `codepilotai.push` prefs file that deliberately survives `TokenStore.clear()`).
  `notifications/` holds `FirebasePush` (null-safe token access; "not
  configured" is a quiet outcome), `PushCoordinator` (fire-and-forget
  register-once-signed-in), `TaskNotifications` (channel `codepilot_tasks`,
  `NotificationCompat` post, `POST_NOTIFICATIONS`-guarded), and
  `CodePilotMessagingService` (builds the notification from a data message).
  Wiring: DI providers + `AppComponent` exposure, `AuthRepository.logout()`
  unregisters the device before clearing the session, and `MainActivity`
  registers on sign-in, requests the runtime permission on API 33+, and routes a
  notification tap to `runFragment` (`taskId` argument) whether cold or warm.

  **Verification.** Backend: 640 passed / 1 skipped (33 new notifications/FCM
  cases); `ruff check` + `ruff format --check` clean; `mypy apps config` clean
  (85 files); `makemigrations --check` clean. Android: `assembleDebug` + 40 JVM
  tests (8 new `DevicesRepositoryTest` cases) + `lint` (0 errors / 37 warnings)
  green. `DevicesApi` routes resolve off the same `ANDROID_API_BASE_URL`.

  **Placeholders / limits.** Push stays inert until a Firebase project supplies
  `app/google-services.json` and the backend `FCM_PROJECT_ID` plus a service
  account (`FCM_SERVICE_ACCOUNT_JSON` inline or `FCM_SERVICE_ACCOUNT_FILE` on
  disk — both in `.env.example`). No emulator/device here, so FCM delivery is
  compile/test/lint-verified only, not on-device.

  **Next.** Real Firebase project + credentials, then on-device E2E; backend
  `/auth/refresh` still unticked; Phase 7 nice-to-haves remain.

- **2026-10-11** — Session 12. Security-checklist audit, an API-level correctness fix, voice input,
  and a diff-preview audit.

  **§7 audit.** The remaining §7 boxes were stale, not unimplemented; every one was verified
  against the code and ticked with evidence: Fernet at rest (`accounts/crypto.py`), tokens kept
  out of argv/errors (`git/credentials.py` + `run_git`'s `scrub_secrets`), 30-min/rotating-refresh
  JWTs with blacklisting, per-user `get_queryset` scoping, path-traversal guards
  (`agent/guards.py`), the verify-command allowlist (`agent/verify.py`), and `.env.example`
  placeholders.

  **`String.isBlank()` was a latent crash on API 24–32.** `isBlank()`/`strip()` are API 33
  (`api-versions.xml` `since="33"`) while `minSdk` is 24 and no core-library desugaring is
  configured; Android lint does not flag `java.*` core-library methods, so this compiled and
  linted green across ~23 pre-existing call sites but would have thrown `NoSuchMethodError` on
  real Android 7–12L devices. Since `isBlank` was the *only* affected API (no
  `java.time`/`stream`/`Optional` anywhere), every call became `.trim().isEmpty()` — ancient APIs,
  no new dependency (Q36).

  **Voice input (Phase 7).** The task-prompt field gained a trailing mic icon
  (`TextInputLayout` `endIconMode="custom"`); tapping it launches the system recognizer with
  `ACTION_RECOGNIZE_SPEECH`, so the app needs **no** `RECORD_AUDIO` permission and no
  `<queries>`. The recognizer's result is merged into the field by `util/VoicePrompt.append`
  (pure, unit-tested: trims, space-joins, ignores blank/null sides), cursor moved to the end;
  a missing recognizer falls back to a toast. `startVoiceInput`/`onVoiceResult` live in
  `NewTaskFragment`, with the launcher registered in `onCreate`.

  **Verification.** Android: `assembleDebug` + **46** JVM tests (6 new `VoicePromptTest` cases) +
  `lint` (0 errors / 37 warnings, unchanged) all green. Backend untouched this session.

  **Diff-preview audit (Phase 7).** The "On-device diff preview before push" box was verified and
  ticked rather than rebuilt: the approval screen already shows the complete per-file diff before
  anything is applied, and the push/PR code in `runner._execute` is unreachable until the user has
  decided every change through `POST …/execute` (which 409s otherwise). Repairs applied after a
  failing verify are the sole write outside the approval gate by design (Q22/Q24) and are bounded
  to make verification pass.

  **i18n (Phase 7).** The *static* strings were already externalized, but three *dynamic* formatters
  still hardcoded English prose. `Labels` now returns `@StringRes` ids (phases/statuses),
  `EventAdapter` returns event-kind ids, and `AuditLog.summary` returns a nestable `AuditLine`
  (resource id + args) resolved by a pure `AuditLog.render(line, lookup)`. That lets `RunViewModel`
  emit `LiveData<Integer>` (default `R.string.phase_waiting`) without a `Context`, while
  `ResultFragment` resolves via `getString`; the unit tests now assert resource ids + structure
  instead of English text (Q37). Added `res/xml/locales_config.xml` + `android:localeConfig` for
  Android 13 per-app language (`supportsRtl` was already set). Adding a translation is now a
  `values-xx/` drop-in; server-supplied error text stays English by nature. Verification:
  `assembleDebug` + **51** JVM tests + `lint` (**0 errors / 37 warnings**) all green.

  **Next.** Real Firebase project + credentials, then on-device E2E; the optional web dashboard is
  the only Phase 7 item left; backend `/auth/refresh` still unticked.

- **2026-10-11** — Session 13. Optional web dashboard (Phase 7) — **Phase 7 now complete.**

  **Decision (Q38).** The dashboard is a **read-only, staff-only operations console** built from
  Django templates over the existing models — no SPA, no new dependency, no models or migrations.
  CodePilotAI has no password login (a user *is* their GitHub identity; passwords are unusable), so
  the console reuses `django.contrib.auth` session auth and gates every view on `is_staff`; an
  operator is provisioned out-of-band (`set_password(...)` + `is_staff=True`) and signs in via
  `/dashboard/login/`. It is deliberately platform-wide: it is an internal "what happened on this
  deployment?" surface, not a second product UI.

  **Implementation.** New `apps/dashboard/` (registered in `INSTALLED_APPS`, mounted at
  `/dashboard/`): an overview page (task counts by status linking into a filtered list, plus token
  and USD totals), a paginated task list (status filter + free-text search over title/prompt/repo),
  and a per-task page (prompt, branch/PR/commit, error, agent summary; token/cost cards; the
  `verification` and `diff_stat` JSON; proposed changes with decisions; the message thread; and the
  run-event timeline, capped at 500 rows). `prettyjson` (a small template filter) prints the
  loose-shaped verification/diff payloads verbatim. `config/settings/base.py` gained
  `LOGIN_URL`/`LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL`.

  **Verification.** `manage.py check` + `makemigrations --check` clean (no models); `ruff` and
  `mypy apps config` (91 files) clean; `tests/test_dashboard.py` — **10** cases covering the
  anonymous/non-staff redirect, password sign-in, logout, platform-wide visibility, status filter,
  search, detail rendering (PR, verification, changes, events), and the unknown-task 404.

  **Next.** Real Firebase project + credentials, then on-device E2E; backend `/auth/refresh`
  still unticked. Every Phase 7 item is now ticked.
