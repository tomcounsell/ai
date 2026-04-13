---
status: Complete
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/841
last_comment_id:
---

# Sentry CLI Integration — Agent, /update, Opt-in Reflection

## Problem

Valor cannot triage Sentry errors autonomously. The `sentry` agent references MCP tool permissions (`sentry_list_*`, `sentry_retrieve_*`, etc.) that require a Sentry MCP server — which is not wired up in this deployment. `sentry-cli` is not installed or verified by `/update`. No reflection runs periodic issue triage.

**Current behavior:**
- `sentry` agent's `permissions` block references unavailable MCP tools
- `sentry-cli` is absent; `/update` does not install or verify it
- No automated Sentry triage runs for any project

**Desired outcome:**
- `sentry-cli` installed by `/update` and reported in the verify step
- `sentry` agent uses CLI commands only — no MCP dependency
- Opt-in `sentry-issue-triage` reflection in `config/reflections.yaml` (`enabled: false`)
- `_enqueue_agent_reflection()` injects `SENTRY_AUTH_TOKEN` for agent-type reflections

## Freshness Check

**Baseline commit:** `86ce1ff9620f3dc8367d6195fde1dfdbe9dd7179`
**Issue filed at:** 2026-04-09T04:05:53Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/agents/sentry.md` — MCP `permissions` block confirmed present (lines 11–22)
- `scripts/update/deps.py` — curl-install pattern (`install_uv()`) confirmed at line 50
- `scripts/update/run.py` — step 3.7/3.8/3.9 pattern (officecli/rodney/npm_tools) at lines 479–519
- `scripts/update/verify.py:175` — `check_system_tools()` list confirmed; no `sentry` entry
- `agent/reflection_scheduler.py:333` — `_enqueue_agent_reflection()` confirmed; no SENTRY_AUTH_TOKEN injection
- `agent/sdk_client.py:1026–1032` — SENTRY_PERSONAL_TOKEN → SENTRY_AUTH_TOKEN injection pattern confirmed
- `config/reflections.yaml` — `sustainability-digest` agent-type entry confirmed as pattern

**Commits on main since issue was filed (touching referenced files):**
- Multiple commits landed but none touched the files in scope above (verified via `git log --oneline --since=2026-04-09 -- .claude/agents/sentry.md scripts/update/ config/reflections.yaml agent/reflection_scheduler.py`)

**Active plans in `docs/plans/` overlapping this area:** none

## Prior Art

No prior issues found related to `sentry-cli` installation or the Sentry agent permissions block.

## Solution

Three independent changes in one PR:

### 1. Update `sentry` agent (`/.claude/agents/sentry.md`)
- Remove the `permissions:` block (lines 11–22) entirely — it references unavailable MCP tools
- Keep `tools: ['*']` (already present in frontmatter)
- Add a **CLI Workflow** section pointing agents to use `sentry issues list --json`, `sentry issues view <ID>`, etc. and referencing the triage classification from `.claude/skills/sentry/SKILL.md` (already on main)

### 2. Add `sentry-cli` to `/update`

**`scripts/update/sentry_cli.py`** (new file):
- `install_sentry_cli() -> InstallResult` — runs the official curl installer: `curl -sL https://sentry.io/get-cli/ | bash`
- `check_sentry_cli() -> InstallResult` — checks `shutil.which("sentry")` and runs `sentry --version`
- `install_or_update() -> InstallResult` — check first; if missing, install; return result with action `"installed"` / `"skipped"` / `"failed"`
- Follows the same `InstallResult` dataclass pattern as `officecli.py` and `rodney.py`

**`scripts/update/run.py`** changes:
- Import `sentry_cli` alongside the other modules
- Add `sentry_cli_result: sentry_cli.InstallResult | None = None` field to `UpdateResult`
- Add step 3.10 after npm_tools (step 3.9): `result.sentry_cli_result = sentry_cli.install_or_update()` with log output matching the officecli/rodney pattern

**`scripts/update/verify.py`** change:
- Add `("sentry", "--version")` to the `tools` list in `check_system_tools()` (line 177)

### 3. Add opt-in reflection + token injection

**`config/reflections.yaml`** — add new entry at end:
```yaml
  - name: sentry-issue-triage
    description: "Triage unresolved Sentry issues for all projects with SENTRY_DSN in their .env"
    interval: 86400  # daily
    priority: low
    execution_type: agent
    command: >
      Triage unresolved Sentry issues across all local projects.
      For each project returned by load_local_projects() that has SENTRY_DSN in its .env,
      run: sentry issues list --status unresolved --json
      Classify each issue using the A-E triage workflow from .claude/skills/sentry/SKILL.md.
      For Class C or D issues, create a GitHub issue in that project's repo.
      Send a summary to the 'Dev: Valor' Telegram chat.
      Skip projects without SENTRY_DSN silently.
    enabled: false
```

**`agent/reflection_scheduler.py`** — update `_enqueue_agent_reflection()`:
- After resolving `project_root` (line 352), add the same SENTRY_AUTH_TOKEN injection as `sdk_client.py` lines 1026–1032:
  - Read `~/Desktop/Valor/.env`, find `SENTRY_PERSONAL_TOKEN=...`, pass `extra_env={"SENTRY_AUTH_TOKEN": value}` into `_push_agent_session()`
- Check whether `_push_agent_session` accepts `extra_env`; if not, inject into the session's environment via a supported mechanism (e.g., prepend `SENTRY_AUTH_TOKEN=... ` to the command string, or use the `env` kwarg if available)

## Data Flow

```
/update skill
  → scripts/update/run.py step 3.10
  → sentry_cli.install_or_update()
  → curl https://sentry.io/get-cli/ | bash   (if not present)
  → verify.check_system_tools()              (reports "sentry vX.Y.Z")

ReflectionScheduler tick (daily)
  → config/reflections.yaml sentry-issue-triage entry
  → _enqueue_agent_reflection(entry)
      → reads ~/Desktop/Valor/.env for SENTRY_PERSONAL_TOKEN
      → injects SENTRY_AUTH_TOKEN into session env
  → PM session runs sentry command
      → iterates load_local_projects()
      → checks each project .env for SENTRY_DSN
      → runs sentry issues list --json per project
      → classifies A-E, creates GH issues for C/D
      → sends Telegram summary
```

## Race Conditions

No race conditions identified — all operations are sequential single-process installs and YAML config reads. The reflection runs as a single daily PM session.

## No-Gos (Out of Scope)

- No `sentry_org` / `sentry_project` fields in `projects.json` — per-project config lives in each project's `.env`
- No MCP server changes — sentry-cli replaces MCP entirely
- No changes to existing tests (this is greenfield installation/config code)
- No changes to `.mcp.json` — MCP Sentry server is not being wired up

## Update System

The update script itself (`scripts/update/run.py`) is the primary deliverable here — adding `sentry-cli` install/verify as step 3.10. The update skill (`scripts/remote-update.sh`) needs no changes; it invokes `run.py --full` which will automatically include the new step.

New machines pulling this update will have `sentry-cli` installed automatically on the first `/update` run.

## Agent Integration

No new MCP server changes needed. The `sentry` agent uses the Claude Code CLI subprocess model (`tools: ['*']`), which gives it full bash access including `sentry-cli`. The agent integration is the sentry.md file update itself — removing the MCP block so it doesn't confuse the agent into looking for unavailable tools.

Integration test: after build, trigger the sentry agent with `sentry issues list --json` and confirm it runs without tool-permission errors.

## Documentation

- [x] Update `.claude/skills/setup/SKILL.md` to mention `sentry auth login` as a post-install step under the authentication section
- [x] No new feature doc needed — the sentry skill doc (`.claude/skills/sentry/SKILL.md`) is already on main and covers the triage workflow

## Test Impact

No existing tests affected — this is a greenfield feature. The update module (`scripts/update/`) has no unit tests today. The reflection scheduler has integration tests but none cover agent-type reflections specifically.

- [x] Add `tests/unit/test_sentry_cli_update.py` — unit tests for `sentry_cli.install_or_update()` covering: already-installed (skipped), not-installed (mock curl succeeds), curl failure
- [x] Add assertion to `tests/unit/test_reflection_scheduler.py` (if it exists) that `_enqueue_agent_reflection()` injects `SENTRY_AUTH_TOKEN` when `SENTRY_PERSONAL_TOKEN` is set in `~/Desktop/Valor/.env`

## Failure Path Test Strategy

- `sentry-cli` install fails (curl not available, network down): `install_or_update()` returns `InstallResult(success=False, action="failed", error=...)` — `/update` logs WARN and continues; no hard failure
- `SENTRY_PERSONAL_TOKEN` absent from `~/Desktop/Valor/.env`: `_enqueue_agent_reflection()` skips injection silently (same guard as `sdk_client.py`) — session still enqueues without the token
- Project `.env` missing `SENTRY_DSN`: reflection command skips that project silently — verified by the command's own guard condition

## Success Criteria

- [x] `sentry-cli` installed by `/update` and appears in verify output (`sentry vX.Y.Z`)
- [x] `/update` exits cleanly on a machine where `sentry-cli` was not previously installed
- [x] `.claude/agents/sentry.md` has no `permissions:` block; contains CLI workflow section
- [x] `_enqueue_agent_reflection()` injects `SENTRY_AUTH_TOKEN` from `~/Desktop/Valor/.env`
- [x] `config/reflections.yaml` contains `sentry-issue-triage` entry with `enabled: false`
- [x] `.claude/skills/setup/SKILL.md` mentions `sentry auth login` as post-install step
- [x] Unit tests for `sentry_cli.py` pass
- [x] `python -m ruff check .` passes

## Team Orchestration

### Team Members

- **Builder (sentry-cli-update)**
  - Name: sentry-update-builder
  - Role: Implement `sentry_cli.py`, wire into `run.py` and `verify.py`, update `sentry.md`, update setup skill
  - Agent Type: builder
  - Resume: true

- **Builder (reflection)**
  - Name: reflection-builder
  - Role: Add `sentry-issue-triage` to `reflections.yaml`, update `_enqueue_agent_reflection()` to inject token
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Verify all acceptance criteria, ruff check, verify no MCP references remain in sentry.md
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build: sentry-cli update module + agent + setup skill
- **Task ID**: build-sentry-update
- **Depends On**: none
- **Validates**: `tests/unit/test_sentry_cli_update.py` (create)
- **Assigned To**: sentry-update-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/update/sentry_cli.py` with `InstallResult` dataclass, `install_sentry_cli()`, `check_sentry_cli()`, `install_or_update()` following `officecli.py` pattern
- Import `sentry_cli` in `scripts/update/run.py`; add `sentry_cli_result` field to `UpdateResult`; add step 3.10 log block after npm_tools step
- Add `("sentry", "--version")` to `check_system_tools()` tools list in `scripts/update/verify.py`
- Remove `permissions:` block from `.claude/agents/sentry.md`; add CLI Workflow section
- Add `sentry auth login` post-install note to `.claude/skills/setup/SKILL.md`
- Write `tests/unit/test_sentry_cli_update.py`

### 2. Build: reflection config + token injection
- **Task ID**: build-reflection
- **Depends On**: none
- **Validates**: manual review of reflections.yaml + reflection_scheduler.py diff
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `sentry-issue-triage` entry to `config/reflections.yaml` with `enabled: false` as specified in Solution section
- Update `_enqueue_agent_reflection()` in `agent/reflection_scheduler.py` to read `~/Desktop/Valor/.env` for `SENTRY_PERSONAL_TOKEN` and inject as `SENTRY_AUTH_TOKEN` — same logic as `sdk_client.py` lines 1026–1032; use `extra_env` param if `_push_agent_session` supports it, otherwise prepend to session metadata

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-sentry-update, build-reflection
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `scripts/update/sentry_cli.py` exists with correct `InstallResult` pattern
- Confirm `scripts/update/run.py` imports `sentry_cli` and has step 3.10
- Confirm `scripts/update/verify.py` `check_system_tools()` includes `sentry`
- Confirm `.claude/agents/sentry.md` has no `permissions:` block
- Confirm `config/reflections.yaml` has `sentry-issue-triage` with `enabled: false`
- Confirm `_enqueue_agent_reflection()` has SENTRY_AUTH_TOKEN injection
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Run `pytest tests/unit/test_sentry_cli_update.py` if created

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sentry_cli_update.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No MCP permissions in sentry agent | `grep -n "sentry_list\|sentry_retrieve\|sentry_update\|sentry_delete" .claude/agents/sentry.md` | exit code 1 |
| Reflection entry exists | `grep "sentry-issue-triage" config/reflections.yaml` | exit code 0 |
| Token injection present | `grep "SENTRY_AUTH_TOKEN" agent/reflection_scheduler.py` | exit code 0 |
| sentry-cli in verify | `grep '"sentry"' scripts/update/verify.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — all assumptions validated by recon and freshness check. Ready to build.
