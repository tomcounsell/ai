---
status: docs_complete
type: chore
appetite: Medium
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/629
last_comment_id:
revision_applied: true
---

# Move Hardcoded PII to Project Config

## Problem

A new user cloning this repo must grep through source files to find and replace personal identifiers before the system works for them. Usernames, credentials, service labels, and paths are scattered across Python source, shell scripts, plist templates, and config files.

**Current behavior:**
- `.env.example:74` exposes a real Sentry DSN with working auth tokens
- `bridge/routing.py:35` hardcodes `VALOR_USERNAMES = {"valor", "valorengels"}` for mention detection
- Service label prefix `com.valor.*` is hardcoded across **every** service install path: `scripts/valor-service.sh` (bridge, update, bridge-watchdog, worker PLIST_NAMEs), `scripts/install_worker.sh`, `scripts/install_reflections.sh`, `scripts/install_autoexperiment.sh`, `scripts/remote-update.sh` (reflections/worker labels + legacy daydream cleanup), `scripts/update/service.py` (bridge, reflections, worker, update, caffeinate — inline-generated caffeinate plist via heredoc), and `scripts/update/run.py`. Committed plist templates at repo root: `com.valor.reflections.plist`, `com.valor.autoexperiment.plist` (bridge/worker/bridge-watchdog/caffeinate plists are generated inline by `service.py`, not committed)
- `config/newsyslog.valor.conf` contains raw `/Users/valorengels/src/ai` paths
- Manifest files in `tools/*/manifest.json` contain `github.com/tomcounsell/ai` URLs

**Desired outcome:**
A new user edits only `.env` and `projects.json`, runs the install scripts, and has a working system with their own identity. No source file edits required.

## Prior Art

- **PR #438**: Config consolidation: eliminate hardcoded paths, unify settings — Successfully removed hardcoded paths from production Python code; introduced `__PROJECT_DIR__` and `__HOME_DIR__` sed placeholders in plist templates. Did NOT address: VALOR_USERNAMES constant, Sentry DSN credential, service label prefix, or newsyslog paths.
- **PR #382**: Patch tech debt: hardcoded paths and deprecated APIs — Earlier round of path cleanup, partial.
- **PR #448**: Make persona name configurable via layered soul files — Made the "Valor" persona name configurable through `projects.json`. Demonstrates the pattern for this work.
- **PR #559**: Config-driven chat mode resolution — Moved chat mode logic to config. Another precedent for config-driven behavior.

## Data Flow

This change touches the config loading and service installation paths:

1. **Entry point (runtime)**: `bridge/telegram_bridge.py` loads `projects.json` via `routing.load_config()`
2. **Routing module**: `routing.py` populates module globals (`CONFIG`, `DEFAULTS`, `GROUP_TO_PROJECT`) and sets `DEFAULT_MENTIONS` from config
3. **Mention detection**: `get_valor_usernames()` merges hardcoded `VALOR_USERNAMES` set with config `mention_triggers` — the hardcoded set is redundant when config is loaded
4. **Entry point (install-time)**: Install scripts (`install_reflections.sh`, `install_autoexperiment.sh`) sed-replace `__PROJECT_DIR__` and `__HOME_DIR__` placeholders in plist templates before copying to `~/Library/LaunchAgents/`
5. **Service management**: `valor-service.sh` constructs plist names using hardcoded `com.valor.*` prefix

## Config Allocation Table

| PII Item | Current Location | Target Home | Rationale |
|----------|-----------------|-------------|-----------|
| Sentry DSN (real credential) | `.env.example:74` | Replace with placeholder `https://your-key@your-org.ingest.sentry.io/your-project` | Credentials never belong in example files |
| `VALOR_USERNAMES` constant | `bridge/routing.py:35` | Derive entirely from `projects.json` `mention_triggers` + `defaults.telegram.mention_triggers` | Already partially redundant with config; remove the hardcoded constant |
| Service label prefix (ALL services) | `scripts/valor-service.sh`, `scripts/install_worker.sh`, `scripts/install_reflections.sh`, `scripts/install_autoexperiment.sh`, `scripts/remote-update.sh`, `scripts/update/service.py`, `scripts/update/run.py`, committed plist templates (`com.valor.reflections.plist`, `com.valor.autoexperiment.plist`), and inline-generated plists in `service.py` (bridge, worker, bridge-watchdog, caffeinate, update) | `.env` as `SERVICE_LABEL_PREFIX` (default: `com.valor`) | Install-time concern; `.env` is the right home for machine-specific settings. ALL `com.valor.*` literals must be parameterized — half-migration violates Dev Principle #1 |
| `/Users/valorengels` paths | `config/newsyslog.valor.conf` | Convert to template with `__PROJECT_DIR__` placeholder; sed-replace at install time | Same pattern already used by plist templates |
| Manifest GitHub URLs | `tools/*/manifest.json` (4 files) | Low priority — leave as-is with a note in setup docs | Non-functional metadata; not fetched at runtime |

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on service label approach)
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies existing config loading and install scripts with no new external dependencies.

## Solution

### Key Elements

- **Sentry DSN sanitization**: Replace real credential in `.env.example` with a clearly-fake placeholder
- **Config-driven username detection**: Remove `VALOR_USERNAMES` constant; `get_valor_usernames()` reads entirely from config `mention_triggers`
- **Parametric service labels**: Add `SERVICE_LABEL_PREFIX` to `.env.example`; install scripts and `valor-service.sh` read it from `.env` or default to `com.valor`
- **newsyslog template**: Convert `newsyslog.valor.conf` to use `__PROJECT_DIR__` placeholders with a companion install step

### Flow

**Clone repo** → Edit `.env` (secrets + service prefix) → Edit `projects.json` (identity + mention triggers) → Run install scripts → Scripts sed-replace templates → **Working system**

### Technical Approach

1. **Sentry DSN** — This is a **live credential**, not just a text-cleanup. Required sequence:
   a. Rotate/revoke the exposed DSN in the Sentry dashboard (`o4508986235682816` org, project `4511091961888768`).
   b. **Verify rejection with HTTP status check** — the test event must return `HTTP 401` or `HTTP 403`, not `200 OK` with a server-side drop. Required curl: `curl -i -X POST "https://o4508986235682816.ingest.us.sentry.io/api/4511091961888768/store/" -H "X-Sentry-Auth: Sentry sentry_version=7,sentry_key=6c263d512a49826d9f4d63578d86d3d1" -H "Content-Type: application/json" -d '{"message":"rotation-test","level":"info"}'`. Paste the full curl response (status line + headers) into the PR description.
   c. Replace `.env.example:74` with placeholder `https://your-key@your-org.ingest.sentry.io/your-project`. Add a comment above the placeholder explaining that historical commits still contain an old, revoked DSN.
   d. Git history scrubbing (`git filter-repo`) is **explicitly out of scope** — see No-Gos. The credential will remain in prior commits; rotation (verified by 401/403) is the sole remediation.

2. **VALOR_USERNAMES removal** — In `bridge/routing.py`:
   - Remove the `VALOR_USERNAMES` constant at line 35
   - Modify `get_valor_usernames()` to build the set entirely from config `mention_triggers` (already loaded from `projects.json` defaults)
   - **No new field** — fold the existing hardcoded handles (`"valor"`, `"valorengels"`) into `defaults.telegram.mention_triggers` in `config/projects.example.json`. The bridge runs as a Telethon userbot (user account, not a Telegram bot), so there is no separate "bot username" concept; `mention_triggers` is already the single source of truth for self-mention detection.
   - **Three-way fallback behavior** (fail-loud on production misconfig, inert in tests):
     - `project is None` → return `set()` (test ergonomics — keeps unit tests from needing config fixtures)
     - `project` is a dict but `mention_triggers` is missing/empty → return `set()` but this is a production misconfig signal
     - **Startup assertion**: `bridge/telegram_bridge.py` must, immediately after `routing.load_config()`, assert that `routing.DEFAULT_MENTIONS` is truthy — raise `RuntimeError("mention_triggers must be configured in projects.json defaults.telegram")` otherwise. This makes production misconfig loud while preserving test inertness.
   - Unit tests must cover all three states: `project=None`, `project={"telegram":{"mention_triggers":[]}}`, `project={"telegram":{"mention_triggers":["x"]}}`.

3. **Service label prefix (ALL services)** — `SERVICE_LABEL_PREFIX` is an **install-time-only** concern for launchd. Scope covers **every** `com.valor.*` literal across shell and Python install/management code.

   **a. Add `SERVICE_LABEL_PREFIX=com.valor` to `.env.example`.**

   **b. Shell scripts** — Every shell script that references `com.valor.*` must source `.env` and compute labels from the prefix:
   - Pattern: after sourcing `.env`, guard with `: "${SERVICE_LABEL_PREFIX:=com.valor}"` (POSIX default).
   - Source line: `set -a; source "$(cd "$(dirname "$0")/.." && pwd)/.env" 2>/dev/null || true; set +a`
   - Files to update:
     - `scripts/valor-service.sh` — lines 11/13/15/17 (bridge, update, bridge-watchdog, worker PLIST_NAMEs)
     - `scripts/install_worker.sh` — lines 12–14
     - `scripts/install_reflections.sh` — reflections label + plist filename
     - `scripts/install_autoexperiment.sh` — autoexperiment label + plist filename
     - `scripts/remote-update.sh` — lines 54–78 (reflections/worker labels). **Legacy `com.valor.daydream` cleanup** at lines 58–60 must be handled explicitly: hard-pin to `com.valor.daydream` (not `${SERVICE_LABEL_PREFIX}.daydream`) because that legacy name only ever existed under `com.valor` and must be cleaned up regardless of the fork's current prefix.

   **c. Python scripts** — `scripts/update/service.py` and `scripts/update/run.py` must load the prefix at module level, after the project `.env` is sourced:
   ```python
   import os
   SERVICE_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")
   ```
   Rebuild every `"com.valor.<x>"` literal as an f-string. Specific call sites to fix:
   - `scripts/update/service.py`: lines 93, 138–142, 175, 229–240, 297, 304, 366, 373, 390 — covers bridge, reflections, worker, update, and caffeinate labels and plist paths.
   - **Inline-generated caffeinate plist**: `scripts/update/service.py:366–390` generates `caffeinate.plist` inline via Python heredoc — the Label `<string>com.valor.caffeinate</string>` inside the heredoc must use an f-string with `{SERVICE_PREFIX}` (both the `Label` and the output filename).
   - `scripts/update/run.py`: lines 662, 666.

   **d. Committed plist templates** — Add `__SERVICE_LABEL__` placeholder to `Label` field in:
   - `com.valor.reflections.plist` (repo root)
   - `com.valor.autoexperiment.plist` (repo root)
   - Template files remain named `com.valor.*.plist` as source-of-truth (recognizability); only installed copies are renamed to `${SERVICE_LABEL_PREFIX}.*.plist` in `~/Library/LaunchAgents/`.
   - **Pre-task enumeration**: run `find /Users/tomcounsell/src/ai -maxdepth 2 -name "com.valor.*.plist"` to confirm committed templates before starting. Bridge/worker/bridge-watchdog plists are **not** committed — they're generated inline by `service.py` (covered in item c).

   **e. Install-time sed/f-string rename**: install scripts and `service.py` write rendered plists to `~/Library/LaunchAgents/` using `${SERVICE_LABEL_PREFIX}.<service>.plist` so the on-disk filename matches the internal `Label` (keeps `launchctl list/unload` sane for forks).

   **f. `valor-service.sh` runtime usage**: `valor-service.sh` sources `.env`, then uses `${SERVICE_LABEL_PREFIX:-com.valor}` **only** to compute the installed plist filename for `launchctl load/unload`. It does not re-render plists at runtime.

   **g. Install/runtime prefix drift guard (C1)**: `SERVICE_LABEL_PREFIX` cannot change post-install without reinstall — launchd is bound to the label baked at install time. `valor-service.sh` must detect drift: after sourcing `.env`, scan `~/Library/LaunchAgents/` for any installed `*.bridge.plist` / `*.worker.plist` / `*.reflections.plist` and extract the actual prefix. If the installed prefix differs from `$SERVICE_LABEL_PREFIX`, print a loud warning (`WARN: installed service prefix '$INSTALLED_PREFIX' differs from .env SERVICE_LABEL_PREFIX='$SERVICE_LABEL_PREFIX'; using installed prefix for launchctl ops. Reinstall to change.`) and use `$INSTALLED_PREFIX` for all `launchctl` commands. Detection pattern:
   ```bash
   INSTALLED_PREFIX=$(ls ~/Library/LaunchAgents/ 2>/dev/null \
     | grep -oE '^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.(bridge|worker|reflections|autoexperiment|bridge-watchdog)\.plist$' \
     | head -1 | sed -E 's/\.(bridge|worker|reflections|autoexperiment|bridge-watchdog)\.plist$//')
   ```

4. **newsyslog.valor.conf** — Rename to `config/newsyslog.conf.template`, replace hardcoded paths with `__PROJECT_DIR__`. macOS newsyslog **only** reads `/etc/newsyslog.conf` and `/etc/newsyslog.d/*.conf` — there is no user-level `~/Library/Logs/newsyslog.d/` path. The install script (`scripts/install_reflections.sh`) renders the template to `config/newsyslog.rendered.conf` and prints an explicit instruction for the user to run `sudo cp config/newsyslog.rendered.conf /etc/newsyslog.d/valor.conf` (root required).

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `get_valor_usernames()` with no config loaded — must return empty set, not crash
- [x] Install scripts with missing `.env` — must use defaults, not fail silently

### Empty/Invalid Input Handling
- [x] `get_valor_usernames()` with empty `mention_triggers` list — returns empty set
- [x] `is_message_for_valor()` with empty text — returns False (existing behavior, verify preserved)

### Error State Rendering
- [x] If `SERVICE_LABEL_PREFIX` is unset, install scripts print the default being used

## Test Impact

Audit command used: `grep -rn 'com\.valor\|VALOR_USERNAMES\|valorengels\|newsyslog\.valor\|is_message_for_valor\|get_valor_usernames' tests/`

- [x] `tests/unit/test_reflections_scheduling.py` — **UPDATE**: asserts `data["Label"] == "com.valor.reflections"` and reads the plist file by exact filename `com.valor.reflections.plist` (7+ references). After Task 3 the installed filename and Label are derived from `SERVICE_LABEL_PREFIX`. Rewrite assertions to load the source-of-truth template (still `com.valor.reflections.plist` in repo) and validate the placeholder form `__SERVICE_LABEL__` or the rendered form when checking an installed copy. Add a parametric test that runs the install script against a temp `.env` with `SERVICE_LABEL_PREFIX=com.example` and verifies the rendered plist has matching filename and Label.
- [x] `tests/e2e/test_message_pipeline.py` — **UPDATE**: calls `is_message_for_valor()` / `get_valor_usernames()`. Ensure every call passes a real project dict (not `None`), and add a regression assertion that with a loaded project the mention detection still resolves `@valor` correctly.
- [x] `tests/unit/test_routing.py` — **CREATE**: new file. Tests (a) `get_valor_usernames(project_with_triggers)` returns config-derived set, (b) `get_valor_usernames(None)` returns empty set without crashing, (c) `is_message_for_valor` behavior preserved end-to-end for loaded-config case.
- [x] `tests/integration/test_agent_session_lifecycle.py` — **REVIEW**: grep hit on `com.valor` (context unknown; confirm whether assertion or comment). UPDATE only if asserting service label literals.
- [x] `tests/integration/test_remote_update.py` — **REVIEW**: grep hit on `com.valor`. UPDATE only if asserting service label literals.
- [x] `tests/unit/test_memory_hook.py` — **REVIEW**: grep hit on `valorengels` (likely a fixture username, not PII wiring). UPDATE only if it references removed code paths.

Callsite audit for `is_message_for_valor` / `get_valor_usernames` (production code, excluding worktrees):
- `bridge/routing.py` (definition)
- `bridge/telegram_bridge.py` (callers) — verify every call passes a resolved project, not `None`
- `docs/guides/valor-name-references.md` — doc-only reference
- `tests/e2e/test_message_pipeline.py` — see above

## Rabbit Holes

- **Renaming all `com.valor.*` references in docs** — The docs reference service labels for human readability; changing every occurrence is churn with no functional impact. Update only the setup guide.
- **Making manifest.json URLs dynamic** — These are non-functional metadata fields. Not worth the complexity of config injection into static JSON files.
- **Removing "Valor" persona name everywhere** — Explicitly out of scope per issue. The persona name is already configurable via `projects.json`.

## Risks

### Risk 1: Breaking existing deployments on config upgrade
**Impact:** Running bridges fail after git pull if they lack new config fields
**Mitigation:** All new config fields have sensible defaults matching current behavior (`com.valor` prefix, existing mention triggers). The change is backward-compatible: old `.env` and `projects.json` files continue to work.

### Risk 2: Mention detection regression
**Impact:** Bot stops responding to @mentions in groups
**Mitigation:** The `mention_triggers` config path is already the primary source in most code paths. We're removing the redundant fallback. Unit test to verify config-only detection works correctly.

## Race Conditions

No race conditions identified — all changes are to startup-time config loading (synchronous, single-threaded) and install-time shell scripts (sequential execution).

## No-Gos (Out of Scope)

- "Valor" persona name removal — stays configurable via `projects.json` personas
- GitHub URLs in `docs/` files — harmless metadata, updated organically
- Telegram group names in test fixtures — test data, not PII exposure
- Manifest URL changes — non-functional metadata, deferred to fork authors
- Social handles (`@valorengels`) in docs/plans — not in Python source
- **Git history scrubbing of the leaked Sentry DSN** — `git filter-repo` would rewrite every commit and break every open PR, worktree, and clone. Rotation of the credential in Sentry is the sole remediation; the old DSN will remain valid-looking in git history but will reject events after rotation.

## Update System

The update script (`scripts/remote-update.sh`) and update skill need minor changes:
- After pulling, if `SERVICE_LABEL_PREFIX` is set in `.env`, the update script should use it when restarting services via `valor-service.sh`
- No new dependencies or config files need propagation — `.env` and `projects.json` are already in the deployment flow
- Migration: existing installations continue working with defaults; no manual intervention required

## Agent Integration

No agent integration required — this is a config/install infrastructure change. No new MCP servers, no changes to `.mcp.json`, no new tools. The bridge itself is modified (routing.py) but no new agent-callable functionality is added.

## Documentation

### Feature Documentation
- [x] Update `docs/guides/setup.md` with new config fields (`SERVICE_LABEL_PREFIX`) and note that self-mention handles live in `mention_triggers`
- [x] Update `docs/guides/valor-name-references.md` to reflect removed hardcoded references
- [x] Update `docs/features/deployment.md` with newsyslog install step

### Inline Documentation
- [x] Code comments on `get_valor_usernames()` explaining config-only source
- [x] Updated docstrings for modified install scripts

## Success Criteria

- [x] `.env.example` contains no real credentials (Sentry DSN replaced with placeholder)
- [x] `VALOR_USERNAMES` constant removed from `bridge/routing.py`; mention detection loads entirely from config
- [x] Service label prefix is configurable via `SERVICE_LABEL_PREFIX` env var; install scripts use it
- [x] No hardcoded `/Users/valorengels` paths remain in config files (newsyslog uses template)
- [x] Existing deployments continue working without config changes (backward compatible defaults)
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (config-cleanup)**
  - Name: config-builder
  - Role: Implement all PII removal changes across routing, scripts, and config files
  - Agent Type: builder
  - Resume: true

- **Validator (config-cleanup)**
  - Name: config-validator
  - Role: Verify no hardcoded PII remains, backward compatibility preserved
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update setup guide, deployment docs, and name references guide
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Rotate + sanitize Sentry DSN
- **Task ID**: build-sentry-dsn
- **Depends On**: none
- **Validates**: (a) old DSN rejects events (manual verification logged in PR description); (b) `grep -c '6c263d512a49' .env.example` returns 0
- **Assigned To**: config-builder (requires human-in-the-loop for Sentry dashboard access)
- **Agent Type**: builder
- **Parallel**: true
- **Step 1 (HUMAN REQUIRED)**: Log into Sentry, rotate/revoke the exposed DSN (`o4508986235682816` / project `4511091961888768`).
- **Step 2 (HUMAN REQUIRED — verify rejection with HTTP status)**: Run `curl -i -X POST "https://o4508986235682816.ingest.us.sentry.io/api/4511091961888768/store/" -H "X-Sentry-Auth: Sentry sentry_version=7,sentry_key=6c263d512a49826d9f4d63578d86d3d1" -H "Content-Type: application/json" -d '{"message":"rotation-test","level":"info"}'`. **Must return HTTP 401 or 403** — a `200 OK` with silent server-side drop is NOT sufficient. Paste the full response (status line + headers) into the PR description.
- **Step 3**: Replace real Sentry DSN in `.env.example:74` with placeholder `https://your-key@your-org.ingest.sentry.io/your-project`. Add a comment above the placeholder noting that historical commits still contain an old, revoked DSN.
- **Step 4**: Record in the PR description: "Old Sentry DSN rotated on {YYYY-MM-DD}; test event confirmed rejected with HTTP {401|403}. Curl output: {paste}."
- Do NOT attempt `git filter-repo` history scrubbing — see No-Gos.

### 2. Remove VALOR_USERNAMES and make mention detection config-only
- **Task ID**: build-username-config
- **Depends On**: none
- **Validates**: create `tests/unit/test_routing.py` and ensure it passes; `grep -c VALOR_USERNAMES bridge/routing.py` returns 0; audit `bridge/telegram_bridge.py` callsites of `is_message_for_valor`/`get_valor_usernames` to ensure none pass `project=None`
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `VALOR_USERNAMES` constant from `bridge/routing.py:35`
- Fold `"valor"` and `"valorengels"` into `defaults.telegram.mention_triggers` in `config/projects.example.json` (no new field)
- **Implementation Note (C1) — `bridge/routing.py:250`**: the current body of `get_valor_usernames()` opens with `usernames = VALOR_USERNAMES.copy()` at line 250 and then layers config triggers on top. Replace that initializer with `usernames: set[str] = set()` and pull triggers from `DEFAULT_MENTIONS` when `project` has no `mention_triggers` of its own (not from the removed constant). Final shape:
  ```python
  def get_valor_usernames(project: dict | None) -> set[str]:
      if project is None:
          return set()
      mentions = project.get("telegram", {}).get("mention_triggers", DEFAULT_MENTIONS)
      return {t.lstrip("@").lower() for t in mentions}
  ```
- Modify `get_valor_usernames()` to build set from config `mention_triggers` only; `project=None` returns `set()` for test ergonomics
- **Add fail-loud startup assertion** in `bridge/telegram_bridge.py`: immediately after `routing.load_config()`, assert `routing.DEFAULT_MENTIONS` is truthy; raise `RuntimeError("mention_triggers must be configured in projects.json defaults.telegram")` if empty. This preserves test inertness (tests that never load config stay silent) while making production misconfig fail loudly at bridge startup.
- Write `tests/unit/test_routing.py` covering **all three states**: `project=None` → `set()`, `project={"telegram":{"mention_triggers":[]}}` → `set()`, `project={"telegram":{"mention_triggers":["x"]}}` → `{"x"}`. Also add a bridge startup test that verifies the assertion fires when `DEFAULT_MENTIONS` is empty.

### 3. Parametric service label prefix (ALL services)
- **Task ID**: build-service-labels
- **Depends On**: none
- **Validates**:
  - `grep -rn 'com\.valor\.' scripts/ com.valor.*.plist` returns ONLY template files or legacy-cleanup pins (e.g., `com.valor.daydream` cleanup in `remote-update.sh`); no live service labels.
  - `grep -c 'SERVICE_LABEL_PREFIX\|SERVICE_PREFIX' scripts/valor-service.sh scripts/install_worker.sh scripts/install_reflections.sh scripts/install_autoexperiment.sh scripts/remote-update.sh scripts/update/service.py scripts/update/run.py` — all files > 0.
  - Parametric test: install with `SERVICE_LABEL_PREFIX=com.example` produces `~/Library/LaunchAgents/com.example.{bridge,worker,reflections,autoexperiment,bridge-watchdog,caffeinate,update}.plist` with matching `Label` fields inside.
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- **Step 0 — Enumerate templates**: run `find /Users/tomcounsell/src/ai -maxdepth 2 -name "com.valor.*.plist"` to list committed plist templates (expected: `com.valor.reflections.plist`, `com.valor.autoexperiment.plist`). Bridge/worker/bridge-watchdog/caffeinate/update plists are generated inline by `scripts/update/service.py` — covered in Step 3.
- **Step 1 — `.env.example`**: Add `SERVICE_LABEL_PREFIX=com.valor`.
- **Step 2 — Shell scripts**: In each of `scripts/valor-service.sh`, `scripts/install_worker.sh`, `scripts/install_reflections.sh`, `scripts/install_autoexperiment.sh`, `scripts/remote-update.sh`, source `.env` (`set -a; source "$(cd "$(dirname "$0")/.." && pwd)/.env" 2>/dev/null || true; set +a`) then `: "${SERVICE_LABEL_PREFIX:=com.valor}"`. Replace every `com.valor.<svc>` literal with `${SERVICE_LABEL_PREFIX}.<svc>`.
  - `scripts/valor-service.sh`: lines 11/13/15/17 PLIST_NAMEs (bridge, update, bridge-watchdog, worker).
  - `scripts/install_worker.sh`: lines 12–14.
  - `scripts/remote-update.sh`: lines 54–78 (reflections/worker labels). **Hard-pin** `com.valor.daydream` cleanup at lines 58–60 — this is a legacy name that only ever existed under `com.valor`, so it does NOT use the prefix variable.
- **Step 3 — Python scripts** (`scripts/update/service.py`, `scripts/update/run.py`): add module-level `SERVICE_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")` after `.env` is sourced. Convert every `"com.valor.<x>"` literal to an f-string `f"{SERVICE_PREFIX}.<x>"`. Specific sites:
  - `scripts/update/service.py`: lines 93, 138–142, 175, 229–240, 297, 304, 366, 373, 390 (bridge, reflections, worker, update, caffeinate labels + plist paths).
  - **Inline-generated caffeinate plist** (`service.py:366–390`): the Python heredoc that writes `caffeinate.plist` must use an f-string with `{SERVICE_PREFIX}` inside the `<string>…</string>` `Label` element AND in the output filename path.
  - `scripts/update/run.py`: lines 662, 666.
- **Step 4 — Committed plist templates**: add `__SERVICE_LABEL__` placeholder to `Label` field in `com.valor.reflections.plist` and `com.valor.autoexperiment.plist` (repo root). Install scripts sed-replace `__SERVICE_LABEL__` with `${SERVICE_LABEL_PREFIX}.reflections` / `.autoexperiment`. Template filenames remain `com.valor.*.plist` in repo; installed copies are renamed to `${SERVICE_LABEL_PREFIX}.*.plist`.
- **Step 5 — Install-time filename rename**: all install scripts write rendered plists to `~/Library/LaunchAgents/${SERVICE_LABEL_PREFIX}.<service>.plist` so filename and internal `Label` stay in sync.
- **Step 6 — Prefix drift guard in `valor-service.sh`** (C1): after sourcing `.env`, scan `~/Library/LaunchAgents/` for installed plists matching `*.{bridge,worker,reflections,autoexperiment,bridge-watchdog}.plist` and extract the actual prefix. If `$INSTALLED_PREFIX` differs from `$SERVICE_LABEL_PREFIX`, print a loud `WARN:` line and use `$INSTALLED_PREFIX` for `launchctl load/unload` operations. Detection pattern:
  ```bash
  INSTALLED_PREFIX=$(ls ~/Library/LaunchAgents/ 2>/dev/null \
    | grep -oE '^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.(bridge|worker|reflections|autoexperiment|bridge-watchdog)\.plist$' \
    | head -1 | sed -E 's/\.(bridge|worker|reflections|autoexperiment|bridge-watchdog)\.plist$//')
  if [ -n "$INSTALLED_PREFIX" ] && [ "$INSTALLED_PREFIX" != "$SERVICE_LABEL_PREFIX" ]; then
    echo "WARN: installed service prefix '$INSTALLED_PREFIX' differs from .env SERVICE_LABEL_PREFIX='$SERVICE_LABEL_PREFIX'; using installed prefix. Reinstall to change."
    SERVICE_LABEL_PREFIX="$INSTALLED_PREFIX"
  fi
  ```
- **Step 7 — Tests**: update `tests/unit/test_reflections_scheduling.py` to validate both template (placeholder) and rendered (substituted) forms. Add a parametric test that runs install with `SERVICE_LABEL_PREFIX=com.example` and verifies rendered filename + internal `Label` both match. Add a test for the drift-guard warning path in `valor-service.sh`.

### 4. Convert newsyslog.valor.conf to template
- **Task ID**: build-newsyslog-template
- **Depends On**: none
- **Validates**: `grep -c '/Users/' config/newsyslog.conf.template` returns 0
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `config/newsyslog.valor.conf` to `config/newsyslog.conf.template`
- Replace hardcoded `/Users/valorengels/src/ai` paths with `__PROJECT_DIR__`
- Add install step to **`scripts/install_reflections.sh`** that sed-replaces `__PROJECT_DIR__` and writes the rendered config to `config/newsyslog.rendered.conf`. The script then prints an explicit instruction: `echo "Run: sudo cp config/newsyslog.rendered.conf /etc/newsyslog.d/valor.conf"`. macOS newsyslog only reads `/etc/newsyslog.conf` and `/etc/newsyslog.d/*.conf` — there is no user-level alternative.

### 5. Validate all changes
- **Task ID**: validate-all-pii
- **Depends On**: build-sentry-dsn, build-username-config, build-service-labels, build-newsyslog-template
- **Assigned To**: config-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `.env.example` contains no real credentials
- Verify `VALOR_USERNAMES` constant is gone from `bridge/routing.py`
- Verify `config/newsyslog.valor.conf` no longer exists (renamed to template)
- Verify install scripts use `SERVICE_LABEL_PREFIX` variable
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Run `pytest tests/unit/ -x -q`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all-pii
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/guides/setup.md` with new config fields
- Update `docs/guides/valor-name-references.md` to reflect removals
- Update `docs/features/deployment.md` with newsyslog install step

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: config-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No real Sentry DSN | `grep -c '6c263d512a49' .env.example` | exit code 1 |
| No VALOR_USERNAMES | `grep -c 'VALOR_USERNAMES' bridge/routing.py` | exit code 1 |
| No hardcoded user path in newsyslog | `grep -c '/Users/' config/newsyslog.conf.template` | exit code 1 |
| Service prefix in scripts | `grep -c 'SERVICE_LABEL_PREFIX' scripts/valor-service.sh` | output > 0 |

## Critique Results

| Severity | Critic | Concern | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Skeptic/Archaeologist/Adversary | B1: `SERVICE_LABEL_PREFIX` scope missed bridge/worker/bridge-watchdog/update/caffeinate labels across `valor-service.sh`, `install_worker.sh`, `remote-update.sh`, `scripts/update/service.py` (inc. inline-generated caffeinate plist), `scripts/update/run.py`. Half-migration would violate Dev Principle #1. | Technical Approach §3 rewritten to enumerate ALL affected files with specific line numbers. Task 3 expanded from 8 bullets to 7 steps covering shell, Python, inline-generated plists, committed templates, drift guard, and parametric tests. Config Allocation Table updated to list all file paths. |
| BLOCKER | Operator/Adversary | B2: empty-set fallback silently broke mention detection on production misconfig. | Three-way fallback clarified: `project=None` → `set()` (test ergonomics); bridge startup now asserts `DEFAULT_MENTIONS` is non-empty after `routing.load_config()` and raises `RuntimeError` on production misconfig. Task 2 extended to include the assertion and three-state unit tests. |
| CONCERN | Operator/Archaeologist | C1: prefix drift between install and runtime — `.env` changes post-install silently break `launchctl` ops. | Task 3 Step 6 adds prefix-drift guard in `valor-service.sh`: scans installed plists, detects drift, prints warning, and uses `$INSTALLED_PREFIX` for launchctl ops. |
| CONCERN | Archaeologist | C2: wrong line numbers (`.env.example:69` should be `:74`; `routing.py:34` should be `:35`); plist templates not enumerated. | Line numbers corrected throughout plan. Task 3 Step 0 adds explicit `find` enumeration; Technical Approach §3d clarifies committed vs inline-generated plists. |
| CONCERN | Skeptic/User | C3: Sentry rotation remediation didn't verify HTTP 401/403 (vs 200-with-silent-drop). | Technical Approach §1 and Task 1 now require curl-based HTTP status verification; exact curl command embedded in the plan; PR description must paste curl response. |
| NIT | — | N1: bogus `~/Library/Logs/newsyslog.d/` path; macOS newsyslog is root-only at `/etc/newsyslog.d/`. | Technical Approach §4 and Task 4 corrected to render to `config/newsyslog.rendered.conf` and print explicit `sudo cp` instruction to `/etc/newsyslog.d/valor.conf`. |

---

## Open Questions

_All open questions resolved._

> **Resolved — Self-mention field naming** (formerly OQ1): The bridge is a Telethon userbot, not a Telegram bot. There is no separate "bot username" concept. Fold existing hardcoded handles into `defaults.telegram.mention_triggers` — single source of truth, no new field.

> **Resolved — Plist filename/Label drift** (formerly OQ2): Source-of-truth templates in the repo remain `com.valor.*.plist` for recognizability; install scripts rename the installed copy to `${SERVICE_LABEL_PREFIX}.*.plist` so on-disk filename and internal `Label` stay in sync. See Task 3.
