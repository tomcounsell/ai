---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/629
last_comment_id:
---

# Move Hardcoded PII to Project Config

## Problem

A new user cloning this repo must grep through source files to find and replace personal identifiers before the system works for them. Usernames, credentials, service labels, and paths are scattered across Python source, shell scripts, plist templates, and config files.

**Current behavior:**
- `.env.example` exposes a real Sentry DSN with working auth tokens
- `bridge/routing.py:34` hardcodes `VALOR_USERNAMES = {"valor", "valorengels"}` for mention detection
- Service label prefix `com.valor.*` is hardcoded in plist templates and install scripts
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
| Sentry DSN (real credential) | `.env.example:69` | Replace with placeholder `https://your-key@your-org.ingest.sentry.io/your-project` | Credentials never belong in example files |
| `VALOR_USERNAMES` constant | `bridge/routing.py:34` | Derive entirely from `projects.json` `mention_triggers` + `defaults.telegram.mention_triggers` | Already partially redundant with config; remove the hardcoded constant |
| Service label prefix | `valor-service.sh`, plist templates, install scripts | `.env` as `SERVICE_LABEL_PREFIX` (default: `com.valor`) | Install-time concern; `.env` is the right home for machine-specific settings |
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

1. **Sentry DSN** — Simple text replacement in `.env.example`. One-line change.

2. **VALOR_USERNAMES removal** — In `bridge/routing.py`:
   - Remove the `VALOR_USERNAMES` constant at line 34
   - Modify `get_valor_usernames()` to build the set entirely from config `mention_triggers` (which are already loaded from `projects.json` defaults)
   - Add a `BOT_USERNAMES` field to `projects.json` `defaults.telegram` for the Telegram bot username(s) that should always trigger responses, separate from conversational mention triggers
   - Fallback: if no config is loaded (e.g., tests), use an empty set so mention detection is inert rather than crashing

3. **Service label prefix** — In `.env.example`, add `SERVICE_LABEL_PREFIX=com.valor`. In `valor-service.sh`, `install_reflections.sh`, `install_autoexperiment.sh`:
   - Source `.env` at the top (or read the specific var)
   - Use `${SERVICE_LABEL_PREFIX:-com.valor}` for plist names
   - Plist template labels also need the `__SERVICE_LABEL__` placeholder, sed-replaced at install time

4. **newsyslog.valor.conf** — Rename to `newsyslog.conf.template`, replace hardcoded paths with `__PROJECT_DIR__`, add install step to the setup docs and install scripts.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `get_valor_usernames()` with no config loaded — must return empty set, not crash
- [ ] Install scripts with missing `.env` — must use defaults, not fail silently

### Empty/Invalid Input Handling
- [ ] `get_valor_usernames()` with empty `mention_triggers` list — returns empty set
- [ ] `is_message_for_valor()` with empty text — returns False (existing behavior, verify preserved)

### Error State Rendering
- [ ] If `SERVICE_LABEL_PREFIX` is unset, install scripts print the default being used

## Test Impact

- [ ] `tests/e2e/test_message_pipeline.py` — UPDATE: if it references `VALOR_USERNAMES` import or hardcoded mention expectations, update to use config-driven values. (Grep confirmed no direct reference to `VALOR_USERNAMES` but may test mention behavior indirectly.)

No other existing tests directly reference `VALOR_USERNAMES`, `com.valor`, or the hardcoded paths being changed. The bridge routing tests that exist use config-driven fixtures already.

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

## Update System

The update script (`scripts/remote-update.sh`) and update skill need minor changes:
- After pulling, if `SERVICE_LABEL_PREFIX` is set in `.env`, the update script should use it when restarting services via `valor-service.sh`
- No new dependencies or config files need propagation — `.env` and `projects.json` are already in the deployment flow
- Migration: existing installations continue working with defaults; no manual intervention required

## Agent Integration

No agent integration required — this is a config/install infrastructure change. No new MCP servers, no changes to `.mcp.json`, no new tools. The bridge itself is modified (routing.py) but no new agent-callable functionality is added.

## Documentation

### Feature Documentation
- [ ] Update `docs/guides/setup.md` with new config fields (`SERVICE_LABEL_PREFIX`, `bot_usernames`)
- [ ] Update `docs/guides/valor-name-references.md` to reflect removed hardcoded references
- [ ] Update `docs/features/deployment.md` with newsyslog install step

### Inline Documentation
- [ ] Code comments on `get_valor_usernames()` explaining config-only source
- [ ] Updated docstrings for modified install scripts

## Success Criteria

- [ ] `.env.example` contains no real credentials (Sentry DSN replaced with placeholder)
- [ ] `VALOR_USERNAMES` constant removed from `bridge/routing.py`; mention detection loads entirely from config
- [ ] Service label prefix is configurable via `SERVICE_LABEL_PREFIX` env var; install scripts use it
- [ ] No hardcoded `/Users/valorengels` paths remain in config files (newsyslog uses template)
- [ ] Existing deployments continue working without config changes (backward compatible defaults)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

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

### 1. Sanitize Sentry DSN in .env.example
- **Task ID**: build-sentry-dsn
- **Depends On**: none
- **Validates**: `grep -c 'ingest.*sentry' .env.example` returns 0 for real DSNs
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace real Sentry DSN in `.env.example:69` with placeholder `https://your-key@your-org.ingest.sentry.io/your-project`

### 2. Remove VALOR_USERNAMES and make mention detection config-only
- **Task ID**: build-username-config
- **Depends On**: none
- **Validates**: `tests/unit/test_routing.py` (create), `grep -c VALOR_USERNAMES bridge/routing.py` returns 0
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `VALOR_USERNAMES` constant from `bridge/routing.py:34`
- Add `bot_usernames` field to `defaults.telegram` in `config/projects.example.json`
- Modify `get_valor_usernames()` to build set from config `mention_triggers` + `bot_usernames` only
- Update `telegram_bridge.py` config propagation to pass `bot_usernames` to routing module
- Add fallback empty set when no config is loaded
- Write unit test verifying config-only mention detection

### 3. Parametric service label prefix
- **Task ID**: build-service-labels
- **Depends On**: none
- **Validates**: `grep -c 'SERVICE_LABEL_PREFIX' scripts/valor-service.sh` returns >= 1
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `SERVICE_LABEL_PREFIX=com.valor` to `.env.example`
- Update `valor-service.sh` to read `SERVICE_LABEL_PREFIX` from env with `com.valor` default
- Update `install_reflections.sh` and `install_autoexperiment.sh` to use `SERVICE_LABEL_PREFIX`
- Add `__SERVICE_LABEL__` placeholder to plist templates and sed-replace it at install time
- Update `com.valor.reflections.plist` and `com.valor.autoexperiment.plist` Label fields to use placeholder

### 4. Convert newsyslog.valor.conf to template
- **Task ID**: build-newsyslog-template
- **Depends On**: none
- **Validates**: `grep -c '/Users/' config/newsyslog.conf.template` returns 0
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `config/newsyslog.valor.conf` to `config/newsyslog.conf.template`
- Replace hardcoded `/Users/valorengels/src/ai` paths with `__PROJECT_DIR__`
- Add install step to setup scripts that sed-replaces the template

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Bot username field naming**: The plan proposes `bot_usernames` in `projects.json` `defaults.telegram` to hold Telegram bot usernames (e.g., `["valor", "valorengels"]`). Is this the right field name, or should it merge with `mention_triggers`? The distinction is: `mention_triggers` includes casual phrases like "hey valor" while `bot_usernames` are strict Telegram @username handles.

2. **Plist template renaming**: Should the plist files be renamed from `com.valor.*.plist` to use a generic prefix (e.g., `service.*.plist.template`), or keep the `com.valor` in the filename and only parametrize the Label field inside? The current approach parametrizes the Label inside but keeps the template filename recognizable.
