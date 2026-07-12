---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/1927
last_comment_id: none
---

# AgentSession Schema Diet: Prune Accreted Telemetry, Rename Survivors for Precision

## Problem

`models/agent_session.py` grew a wide telemetry surface during the granite PTY era. #1924 (PTY teardown) removed the eight PTY fields; #2000 (HarnessAdapter) converged every role onto one `claude -p` transport. With both landed, the survivor field set is finally knowable — and it is still roughly 2x wider than its post-teardown meaning carries.

Concretely, the model retains:
- A **metered/total token-accounting split** (`metered_*` vs `total_*`) that existed only because a PTY transcript-tailer and the headless runner wrote disjoint field sets concurrently. Post-teardown the tailer is gone, so the split is obsolete.
- **Fields with no live writer** that persist only "to dodge a migration" (`self_report_sent_at`, `sdk_connection_torn_down_at`, `session_mode`, `pm_transcript_path`, `dev_transcript_path`) plus historical-only diagnostic pointers (`startup_failure_kind`, `startup_captured_frame`).
- **Write-only observability counters with no production reader** (`compaction_count`, `compaction_skipped_count`, `nudge_deferred_count`).

**Current behavior:** Every reader of `AgentSession` (dashboard two-hop, analytics, watchdog, CLI) carries dead weight; new contributors cannot tell which fields are load-bearing. Owner direction (2026-07-06): "100% agree. even naming of fields could be improved for clarity, precision."

**Desired outcome:** A field-by-field audited model where every surviving field has a live reader or writer and a name that says what it means, the token-accounting split is collapsed to one set, and every mirror stays consistent — shipped via one idempotent, ORM-safe migration.

## Freshness Check

**Baseline commit:** `b5105dfe8e60dfe7a36fb2460929ceb98c3d56f4`
**Issue filed at:** 2026-07-06T08:07:11Z
**Disposition:** Minor drift (line numbers moved; one claim corrected; premise intact)

**File:line references re-verified (against baseline):**
- Accounting split — issue said `models/agent_session.py:503-514`; now at **`:481-519`** (`total_*` 488-494, `metered_*` 516-519).
- "Delivery workaround `has_communicated` around `:286-296`" — **corrected**: `has_communicated` is a **method on `agent/messenger.py:176` (`BossMessenger`)**, never an AgentSession field. Lines `:286-296` are the `recent_sent_drafts` docstring. The actual delivery-tracking field the issue means is `user_facing_routed` at **`:337-345`** — and it is LIVE (read by the executor emoji branch, OR'd with `has_communicated()`), so it is a keep/rename candidate, not a delete.
- "Partitioned-save list `ADD_ONLY_LIVENESS_FIELDS` `:971-981`" — the real symbol is **`_UPDATED_AT_OMISSION_OK_FIELDS`** at `:969-985`.
- Dashboard mirrors — `ui/data/sdlc.py` still mirrors (via the intermediate `PipelineProgress` Pydantic model); `ui/app.py::_session_to_json()` now at `:662-757`.

**Cited sibling issues/PRs re-checked:**
- #1924 (PTY teardown) — CLOSED-COMPLETED 2026-07-07. Removed the PTY fields; shipped `scripts/migrate_strip_pty_fields.py` (the pattern this issue extends).
- #2000 (HarnessAdapter seam) — CLOSED-COMPLETED 2026-07-11. Single-transport convergence; deleted the idle-sweeper substrate that once wrote `sdk_connection_torn_down_at`.

**Commits on main since issue filed (touching `models/agent_session.py`):** `51473b9f` (#2043 ledger flag), `347882f2` (#2038 HarnessAdapter), `5ac64a8c` (#2030 scar-tissue removal), `1b1d1778`, `2f324bff`, `0f33567e`, `e8351e4c` (#1930 PTY runner cutover). All are prerequisite/adjacent landings that make the survivor set knowable — none change the root premise.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **#1924 / PR (`scripts/migrate_strip_pty_fields.py`)**: Removed eight PTY fields via an ORM-safe atomic delete+recreate on terminal records only. Succeeded and shipped the exact migration pattern this plan clones. This is the template.
- **#2000 (HarnessAdapter)**: Converged all roles onto one transport and deleted the idle-sweeper (`worker/idle_sweeper.py`), orphaning `sdk_connection_torn_down_at`. Establishes that the metered/total disjointness invariant no longer holds.
- **#1842 (metered-leg accounting)**: Introduced the `metered_*` fields as a DISJOINT set so the PTY tailer (`total_*`) and headless runner (`metered_*`) would not clobber each other. That justification is now void — this plan reverses it.
- **#1099 / #1172 (field-backcompat healing)**: Established that nullable/defaulted Popoto fields need no backfill; `_heal_descriptor_pollution` walks fields generically. Informs the "additive nullable" reasoning and its inverse (deletion needs the dead-field pop-list).

## Research

No relevant external findings — this is a purely internal Popoto-model refactor. Proceeding with codebase context and the #1924 migration precedent.

## Data Flow

The model is mirrored through four surfaces. The migration must keep all four consistent:

1. **Model** — `models/agent_session.py` field declarations plus three internal coupling lists: `_DATETIME_FIELDS` (663-675), `_INT_FIELDS_BACKCOMPAT` (683-691), `_UPDATED_AT_OMISSION_OK_FIELDS` (969-985), and the `_normalize_kwargs` "Remove dead fields silently" pop-list (901-908).
2. **Dashboard two-hop** — `ui/data/sdlc.py::_session_to_pipeline()` reads AgentSession fields into the intermediate **`PipelineProgress`** Pydantic model (declared fields ~294-352, read sites ~954-1097); then `ui/app.py::_session_to_json()` (662-757) serializes `PipelineProgress` (NOT the AgentSession) into `/dashboard.json`. A renamed/removed field must be updated at BOTH hops or it silently reads its default.
3. **Analytics** — `ui/data/analytics.py` sums `total_cost_usd` (42-50) and, separately, `_sum_metered_cost` (57-66) → `metered_cost_today_usd` / `metered_cost_7d` (97-98, 118).
4. **Other live readers** — `monitoring/session_watchdog.py:1038-1046` (token/cost alert), `agent/tool_budget.py:123` (`total_cost_usd` backstop), `reflections/pm_briefings/daily_log.py:401,749`, and the **queue serialization allowlist** `agent/agent_session_queue.py:201-202` (`recovery_attempts`, `reprieve_count`) — a fourth coupling the issue did not name.

Two surfaces the issue named need **no per-field edits**:
- **`valor-session` CLI** (`tools/valor_session.py`) — the flagged fields surface ONLY through `cmd_inspect`'s reflective `dir(session)` dump (1002-1009). Every command's output logic uses a fixed subset (`session_id`, `status`, `session_type`, `created_at`, `slug`, `pr_url`, …) — none on the hit-list. Deletes/renames flow through the reflection automatically.
- **SQLite session archive** (`agent/session_archive.py`) — field-agnostic: `_serialize_session` walks `session._meta.fields` (193-194) and dumps everything into one JSON `payload` column; only five fields are promoted to real columns (`id`, `session_id`, `project_key`, `status`, `updated_at`). No `CREATE TABLE` change for any flagged field.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the delete/keep/rename disposition table, especially the observability-counter cuts)
- Review rounds: 1 (migration correctness + mirror consistency)

Large because the change touches one central model plus four mirror surfaces, three internal coupling lists, an ORM-safe migration with delete + rename semantics, and ~15 test files — but it is well-understood and directly patterned on #1924.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Prereq #1924 merged | `gh issue view 1924 --json state -q .state` (expect `CLOSED`) | PTY fields already gone; migrations do not race |
| Prereq #2000 merged | `gh issue view 2000 --json state -q .state` (expect `CLOSED`) | Single transport; survivor set knowable |
| Shared venv intact | `test -x /Users/tomcounsell/src/ai/.venv/bin/python` | Migration + tests run under the repo venv |

## Solution

### Key Elements

- **Disposition table** — one keep / delete / rename decision for every AgentSession field, applied field-by-field. The table below is the audited starting point; the build must confirm it covers every declared field.
- **Deletion set** — fields with no live reader AND no live writer, removed from the model, their orphan read sites, and the relevant coupling lists.
- **Accounting collapse** — redirect the runner's `accumulate_session_tokens(metered=True)` writes to the `total_*` fields, then delete the four `metered_*` fields and their dashboard/analytics readers.
- **Rename set** — a conservative set of survivors whose names no longer say what they mean, each renamed with a `_normalize_kwargs` back-alias so archive-restore payloads still map.
- **One idempotent migration** — a clone of `scripts/migrate_strip_pty_fields.py` (atomic delete+recreate on terminal records, ORM-only) whose `STALE_FIELDS` set is the union of deleted names and old rename-source names; registered in `MIGRATIONS`.

### Field-by-Field Disposition (audited starting point)

**DELETE — dead (no live reader and no live writer):**

| Field | Evidence | Extra cleanup |
|-------|----------|---------------|
| `self_report_sent_at` | Retired 2026-05-06; docstring says no caller | — |
| `sdk_connection_torn_down_at` | Idle-sweeper deleted (#2000); no writer/reader | Remove from `_DATETIME_FIELDS` |
| `session_mode` | Deprecated no-op; superseded by `session_type`; well past 30-day TTL | — |
| `pm_transcript_path` | No live writer → always `None`; only dashboard reads it | Drop reads: `ui/app.py:738`, `ui/data/sdlc.py:339,1092` + `PipelineProgress` decl 338 |
| `dev_transcript_path` | No live writer (D1 runs Dev as PM subagent); dashboard-only reads | Drop reads: `ui/app.py:739`, `ui/data/sdlc.py:340,1093` + `PipelineProgress` decl 340 |
| `startup_failure_kind` | No live writer (historical); read by crash-sig `== "ceiling"` branch | Drop the branch at `agent/crash_signature.py:296,329` (guard `getattr` reader 235) |
| `startup_captured_frame` | No live writer; `getattr(...,None)` always None now | Update `reflections/crash_recovery.py:357` diagnostic call site |

**COLLAPSE — delete `metered_*`, redirect writes to `total_*`:**

| Field | Action |
|-------|--------|
| `metered_input_tokens`, `metered_output_tokens`, `metered_cache_read_tokens`, `metered_cost_usd` | Point `accumulate_session_tokens(metered=True)` (`agent/sdk_client.py:288-338`) at the `total_*` fields; delete the four `metered_*` fields; remove the `metered=` branch/param; remove dashboard emits + `total_cost_usd_combined` (`ui/app.py:703-708`) and analytics `_sum_metered_cost` + `metered_cost_today/7d` (`ui/data/analytics.py:57-66,97-98,118`). Accept loss of the metered/total breakdown (deliberate). |

**PRUNE — write-only observability, no production reader (confirm with PM — see Open Questions):**

| Field | Writer to remove | Recommendation |
|-------|------------------|----------------|
| `compaction_count` | `agent/hooks/pre_compact.py:165,169` | CUT (no consumer since introduction) |
| `compaction_skipped_count` | `agent/hooks/pre_compact.py:227-228` | CUT |
| `nudge_deferred_count` | `agent/session_executor.py:1387-1388` | CUT |
| `tool_timeout_count_{internal,mcp,default}` | `agent/session_health.py:4217-4221` (dynamic `setattr`) | KEEP (recent #1270, cheap, plausible near-term dashboard use); if cut, also drop the three `_INT_FIELDS_BACKCOMPAT` entries and the dynamic writer |

**KEEP (live) — do NOT rename the high-traffic accounting fields:**
`total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` (read by analytics, watchdog, tool_budget, pm_briefings — renaming is churn with no payoff); `reprieve_count`, `recovery_attempts` (live + queue allowlist); `exit_returncode` (OOM detector + `_INT_FIELDS_BACKCOMPAT`); `exit_reason`; `user_facing_routed`; all identity/queue/runner/liveness fields.

**RENAME — precision names, each with a `_normalize_kwargs` back-alias:**

| Current | Proposed | Rationale |
|---------|----------|-----------|
| `watchdog_unhealthy` | `unhealthy_reason` | Holds a reason string, not a bool; name implies a flag (confirm reader sites during build) |
| `user_facing_routed` | `user_delivery_confirmed` | "routed" understates it — the field means a user-facing payload was confirmed delivered |

The build must complete the per-field name audit for EVERY survivor and add any additional precision renames it justifies; the No-Gos below fence off the churn-heavy ones. Each rename: update model decl, add `_normalize_kwargs` alias, update every read/write site (grep-driven), update any coupling list membership, update the dashboard two-hop, and add the old name to the migration `STALE_FIELDS`.

### Flow

Model audit → apply deletions + coupling-list edits → collapse accounting → apply renames (+ aliases) → write & register migration → update dashboard two-hop + analytics → run migration dry-run then apply → tests → docs.

### Technical Approach

- **Rename value-preservation stance:** Popoto lazy-load reads raw hash keys and does NOT route through `_normalize_kwargs`, so a renamed field on an existing live Redis record cannot copy its old-key value forward ORM-safely (reading the raw scalar is banned by the #1038 binary-decode rule; only `HKEYS` for names is allowed). Therefore renames **accept loss of the pre-migration value on legacy records** (the field resets to default; the migration strips the orphaned old-name key). The `_normalize_kwargs` alias still preserves values on the **archive-restore path** (restore calls `AgentSession(**payload)` through `__init__`). In-flight sessions crossing the deploy boundary reset a renamed counter to its default once — transient and acceptable (sessions are short-lived; 30-day TTL).
- **Deletion migration:** clone `scripts/migrate_strip_pty_fields.py` to `scripts/migrate_schema_diet_fields.py` with `STALE_FIELDS = {deleted names} ∪ {old rename-source names}`. Terminal-record-only atomic delete+recreate on one Redis pipeline; non-terminal rows deferred (age out via TTL); `rebuild_indexes()` after. Register as a new `MIGRATIONS` entry in `scripts/update/migrations.py`.
- **Dead-field restore safety:** add every deleted field name and every old rename-source name to the `_normalize_kwargs` "Remove dead fields silently" pop-list (`:901-908`) so restoring a pre-migration archive payload does not raise on unexpected kwargs.
- **Coupling-list sync:** after edits, `_DATETIME_FIELDS`, `_INT_FIELDS_BACKCOMPAT`, `_UPDATED_AT_OMISSION_OK_FIELDS`, and the queue allowlist (`agent/agent_session_queue.py:201-202`) must reference only surviving field names.
- **No raw Redis** anywhere; ORM only (`instance.delete()`, `Model.save()`, `rebuild_indexes()`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The migration wraps per-record work in `try/except` with per-record isolation (mirrors `migrate_strip_pty_fields.py`); a test asserts a poison record increments the `errors` stat and does not abort the run.
- [ ] `accumulate_session_tokens` after collapse: assert a save failure path still logs rather than silently dropping token counts.

### Empty/Invalid Input Handling
- [ ] Migration on an empty keyspace returns zeroed stats (idempotent no-op) — add a test.
- [ ] Restoring an archive payload that still carries a deleted/renamed key does not raise (pop-list covers it) — add a test.
- [ ] Loading a legacy Redis record that has an old-name hash key returns the new field at its default (no crash) — add a test.

### Error State Rendering
- [ ] `/dashboard.json` renders with `total_cost_usd_combined` removed and `metered_*` gone — assert no `KeyError`/`AttributeError` and no lingering `metered_` keys in the payload.
- [ ] `valor-session inspect` still runs (reflective dump) with the reduced field set.

## Test Impact

- [ ] `tests/unit/test_agent_session_liveness_fields.py` — UPDATE: drop assertions on deleted `self_report_sent_at` / `sdk_connection_torn_down_at`.
- [ ] `tests/unit/test_session_token_accumulator.py` — REPLACE: rewrite for single-set accounting (no `metered=` branch).
- [ ] `tests/unit/test_harness_token_capture.py` — UPDATE: assert writes land on `total_*` only.
- [ ] `tests/unit/test_analytics_query_session_sums.py` — UPDATE: remove `_sum_metered_cost` expectations.
- [ ] `tests/integration/test_analytics_dashboard.py` — UPDATE: drop `metered_cost_today_usd` / `*_combined` assertions.
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: remove `pm_transcript_path` / `dev_transcript_path` set/read cases (or the renamed-field cases).
- [ ] `tests/unit/test_dashboard_pillar_a_fields.py` — UPDATE: reconcile emitted keys with the reduced payload.
- [ ] `tests/unit/test_ui_app.py` — UPDATE: `_session_to_json` key set changed.
- [ ] `tests/unit/test_watchdog_token_alert.py` — UPDATE: confirm still reads `total_*` (should be unaffected; verify).
- [ ] `tests/unit/hooks/test_pre_compact_hook.py` — UPDATE or DELETE the `compaction_count` / `compaction_skipped_count` assertions IF those counters are cut.
- [ ] `tests/unit/test_session_health_tool_timeout.py` / `tests/integration/test_session_health_tool_timeout.py` — UPDATE only if `tool_timeout_count_*` is cut (default: KEEP → no change).
- [ ] `tests/unit/test_session_archive.py`, `tests/integration/test_session_archive_cold_boot.py` — UPDATE: add a restore-of-legacy-payload-with-dead-keys case.
- [ ] `tests/unit/test_messenger.py` — no change (`has_communicated` is a messenger method, out of scope).
- [ ] `tests/unit/test_agent_session_updated_at_utc.py` — UPDATE only if `_UPDATED_AT_OMISSION_OK_FIELDS` membership changes (no deleted field is currently in it).

## Rabbit Holes

- **Renaming the `total_*` accounting fields for "consistency."** They are read by analytics, watchdog, tool_budget, pm_briefings, and the dashboard two-hop; the rename churn dwarfs any clarity gain. Leave them.
- **Copy-forward preservation of renamed field values on live Redis records.** Would require raw `HGET` (banned) or a transitional dual-field shim (violates no-legacy-code). Accept loss instead.
- **Preserving longitudinal comparability of pruned metrics.** Explicitly out of scope per owner direction — do not build a metrics-archival side-channel.
- **Deleting `total_cost_usd`/`total_input_tokens` because "metered replaced them."** Backwards — `metered_*` is deleted; `total_*` stays.
- **Auditing every non-AgentSession model** for similar diet opportunities. Scope is `AgentSession` only.

## Risks

### Risk 1: Renamed field silently reads default at one dashboard hop
**Impact:** A rename applied at `_session_to_json` but not at `_session_to_pipeline` (or vice versa) makes the dashboard show a default/blank value with no error.
**Mitigation:** The disposition table lists BOTH hops per field; a test asserts the renamed key carries a non-default value end-to-end through `/dashboard.json`.

### Risk 2: `tool_timeout_count_*` looks dead to a literal grep but is written via `f"tool_timeout_count_{tier}"`
**Impact:** A contributor deletes it as "dead," breaking tool-timeout recovery bookkeeping.
**Mitigation:** Default disposition is KEEP; the plan flags the dynamic-`setattr` writer at `session_health.py:4217` explicitly. If cut, remove the writer and `_INT_FIELDS_BACKCOMPAT` entries together.

### Risk 3: Archive restore of a pre-migration payload raises on a deleted/renamed key
**Impact:** Cold-boot restore from `session_archive.db` crashes on old rows.
**Mitigation:** Every deleted name and old rename-source name is added to the `_normalize_kwargs` dead-field pop-list; a restore-of-legacy-payload test covers it.

### Risk 4: Non-terminal (live) records skipped by the migration keep orphan keys
**Impact:** In-flight sessions retain stale hash keys until TTL.
**Mitigation:** Matches #1924's accepted behavior — Popoto ignores unknown hash keys on load; the migration runs once per machine and residuals age out via the 30-day TTL. Documented, not a defect.

## Race Conditions

### Race 1: Migration rewrites a record the worker is concurrently writing
**Location:** `scripts/migrate_schema_diet_fields.py` (terminal-record loop)
**Trigger:** Migration runs while a worker writes a session.
**Data prerequisite:** Only TERMINAL-status records are rewritten (the worker never writes terminal rows).
**State prerequisite:** Non-terminal rows are detected and deferred, never rewritten out from under the worker.
**Mitigation:** Copied verbatim from `migrate_strip_pty_fields.py` — terminal-only rewrite on one atomic MULTI/EXEC pipeline; base `popoto.Model.save` preserves the loaded `updated_at`. On this machine the worker/bridge/watchdog stay DOWN during build regardless.

## No-Gos (Out of Scope)

- `[DESTRUCTIVE]` Migrating or archiving the historical `metered_*` / pruned-counter values before deletion — the loss of longitudinal comparability is deliberately accepted per owner direction; no rescue side-channel.
- `[DESTRUCTIVE]` Renaming the `total_*` accounting fields — high read fan-out, no clarity payoff; the accounting collapse keeps their existing names.
- Auditing or dieting any Popoto model other than `AgentSession`.
- Changing the SQLite archive `CREATE TABLE` schema — it is field-agnostic; no change is needed or wanted.

Nothing deferred to a separate issue — every relevant item is in scope for this plan.

## Update System

Update-system changes ARE required:
- Add `_migrate_schema_diet_fields` to `scripts/update/migrations.py` and register it in the `MIGRATIONS` dict (idempotent, recorded once in `data/migrations_completed.json`). It shells out to `scripts/migrate_schema_diet_fields.py --apply` under the repo venv, mirroring `_migrate_strip_pty_session_fields`.
- No new dependencies, config files, or env vars. `scripts/update/run.py` needs no structural change — `run_pending_migrations()` already iterates `MIGRATIONS`.

## Agent Integration

No agent integration required — this is a bridge-internal data-model refactor. No new MCP surface, no `.mcp.json` change, and no `bridge/telegram_bridge.py` import change. The `valor-session` CLI's `inspect` command reflects the model generically, so its output tracks the reduced field set automatically with no code change.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` with the post-diet field inventory, the metered/total collapse rationale, and the delete/rename disposition table.
- [ ] Add a short "Schema diet (#1927)" note to `docs/features/agent-session-model.md` migration history and cross-reference `scripts/migrate_schema_diet_fields.py`.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for the model.

### Inline Documentation
- [ ] Update the model docstring block-comments for every renamed field to describe the new name's meaning.
- [ ] Docstring on `scripts/migrate_schema_diet_fields.py` listing the exact deleted + renamed field sets (mirrors the PTY strip script's docstring).

## Success Criteria

- [ ] Every surviving `AgentSession` field has a live reader or writer, or a documented keep-rationale; deleted fields (`self_report_sent_at`, `sdk_connection_torn_down_at`, `session_mode`, `pm_transcript_path`, `dev_transcript_path`, `startup_failure_kind`, `startup_captured_frame`, `metered_*`) are gone from the model.
- [ ] The `metered_*` fields are deleted and `accumulate_session_tokens` writes a single `total_*` set (no `metered=` branch).
- [ ] `grep -rn "metered_" --include=*.py agent/ ui/ models/ monitoring/ reflections/` returns no live field references.
- [ ] Renamed fields carry a `_normalize_kwargs` back-alias and update both dashboard hops.
- [ ] `_DATETIME_FIELDS`, `_INT_FIELDS_BACKCOMPAT`, `_UPDATED_AT_OMISSION_OK_FIELDS`, the `_normalize_kwargs` dead-field pop-list, and the queue allowlist reference only surviving field names.
- [ ] The migration is idempotent (second run reports zero stripped) and registered in `MIGRATIONS`.
- [ ] `/dashboard.json` and `valor-session inspect` render with the reduced field set (no `KeyError`/`AttributeError`).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Serial build — every change lands in `models/agent_session.py` plus tightly-coupled mirrors, so parallel builders would race on the same files. One builder works top-to-bottom in a dedicated worktree, then a validator, then a documentarian.

### Team Members

- **Builder (schema-diet)**
  - Name: `schema-diet-builder`
  - Role: Apply deletions, accounting collapse, renames, migration, and mirror updates in `models/agent_session.py`, `agent/sdk_client.py`, `ui/data/sdlc.py`, `ui/app.py`, `ui/data/analytics.py`, `scripts/`, and coupling sites.
  - Agent Type: builder
  - Domain: redis-popoto-data
  - Resume: true

- **Validator (schema-diet)**
  - Name: `schema-diet-validator`
  - Role: Verify migration idempotency, mirror consistency, no live `metered_` references, dashboard/CLI render, and success criteria.
  - Agent Type: validator
  - Resume: true

- **Documentarian (schema-diet)**
  - Name: `schema-diet-documenter`
  - Role: Update `docs/features/agent-session-model.md` and migration-script docstrings.
  - Agent Type: documentarian
  - Resume: true

### Machine & Build Guardrails (propagate to every agent)

- The worker, bridge, and watchdog on this machine are DOWN and MUST stay down for the entire build. Do not start them; do not run `valor-service.sh restart`.
- Work in ONE dedicated slug worktree (`.worktrees/agent-session-schema-diet/`). If any parallel helper is ever spawned, give it a non-overlapping path.
- NEVER run `uv sync` / `uv sync --frozen` from a worktree — it strips the shared `.venv`. If a dependency is missing, use scoped `uv pip install --python /Users/tomcounsell/src/ai/.venv/bin/python "<pkg>==<ver>"`.
- No raw Redis on Popoto-managed keys — ORM only (`instance.delete()`, `Model.save()`, `rebuild_indexes()`).
- No Claude co-author on commits; let git config drive the commit email (do not override `user.email`).
- Run the migration dry-run first (`python scripts/migrate_schema_diet_fields.py`), inspect stats, then `--apply`.

## Step by Step Tasks

### 1. Complete the field-by-field disposition audit
- **Task ID**: build-audit
- **Depends On**: none
- **Assigned To**: schema-diet-builder
- **Agent Type**: builder
- **Parallel**: false
- Walk every declared field in `models/agent_session.py`; confirm each against the disposition table; extend the rename set only where a name is genuinely misleading (fence off the No-Go churn fields).
- Produce the final delete / keep / rename lists as code comments in the migration script docstring.

### 2. Apply deletions + coupling-list edits
- **Task ID**: build-deletions
- **Depends On**: build-audit
- **Validates**: `tests/unit/test_agent_session_liveness_fields.py`, `tests/unit/test_ui_sdlc_data.py`
- **Assigned To**: schema-diet-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete the seven dead fields; remove orphan read sites (crash_signature `"ceiling"` branch, crash_recovery diagnostic, dashboard `pm_transcript_path`/`dev_transcript_path` at both hops).
- Remove `sdk_connection_torn_down_at` from `_DATETIME_FIELDS`; add every deleted name to the `_normalize_kwargs` dead-field pop-list.

### 3. Collapse the metered/total accounting split
- **Task ID**: build-collapse
- **Depends On**: build-audit
- **Validates**: `tests/unit/test_session_token_accumulator.py`, `tests/unit/test_harness_token_capture.py`, `tests/unit/test_analytics_query_session_sums.py`, `tests/integration/test_analytics_dashboard.py`
- **Assigned To**: schema-diet-builder
- **Agent Type**: builder
- **Domain**: redis-popoto-data
- **Parallel**: false
- Redirect `accumulate_session_tokens(metered=True)` to `total_*`; remove the `metered=` param/branch; delete the four `metered_*` fields.
- Remove `_sum_metered_cost`, `metered_cost_today/7d`, dashboard metered emits, and `total_cost_usd_combined`.

### 4. Apply renames with back-aliases + PM-confirmed counter prune
- **Task ID**: build-renames
- **Depends On**: build-deletions, build-collapse
- **Assigned To**: schema-diet-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename each survivor per the confirmed table; add `_normalize_kwargs` aliases; update every read/write site, coupling-list membership, dashboard two-hop, and queue allowlist.
- Apply the observability-counter cuts per the PM decision (Open Question 1).

### 5. Write + register the migration
- **Task ID**: build-migration
- **Depends On**: build-renames
- **Validates**: `tests/unit/test_session_archive.py`, `tests/integration/test_session_archive_cold_boot.py`
- **Assigned To**: schema-diet-builder
- **Agent Type**: builder
- **Domain**: redis-popoto-data
- **Parallel**: false
- Clone `migrate_strip_pty_fields.py` → `migrate_schema_diet_fields.py` with `STALE_FIELDS = {deleted} ∪ {old rename-source names}`; register `_migrate_schema_diet_fields` in `MIGRATIONS`.
- Run dry-run, then `--apply`; confirm a second run reports zero stripped (idempotent).

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-migration
- **Assigned To**: schema-diet-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm no live `metered_` references, mirror consistency, dashboard/CLI render, migration idempotency.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: schema-diet-documenter
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` and the migration-script docstring.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No live metered_* field refs | `grep -rn "metered_input_tokens\|metered_output_tokens\|metered_cache_read_tokens\|metered_cost_usd" --include=*.py agent/ ui/ models/ monitoring/ reflections/` | match count == 0 |
| Deleted fields gone from model | `grep -c "self_report_sent_at\|sdk_connection_torn_down_at\|pm_transcript_path\|dev_transcript_path\|startup_failure_kind\|startup_captured_frame" models/agent_session.py` | match count == 0 |
| No total_cost_usd_combined | `grep -c "total_cost_usd_combined" ui/app.py` | match count == 0 |
| Migration registered | `grep -c "_migrate_schema_diet_fields" scripts/update/migrations.py` | output > 0 |
| No raw Redis in migration | `grep -c "hdel\|hset\|\.delete(" scripts/migrate_schema_diet_fields.py` | exit code 0 |
| Migration idempotent (2nd run) | `python scripts/migrate_schema_diet_fields.py --apply` | output contains 'stripped': 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Observability-counter cut vs keep.** `compaction_count`, `compaction_skipped_count`, `nudge_deferred_count` are write-only with no production reader today — recommend CUT (with their writer increments). `tool_timeout_count_{internal,mcp,default}` are LIVE-written (#1270, via dynamic `setattr`) but also have no prod reader — recommend KEEP as near-term dashboard observability. Confirm both calls, since the issue's "wedge-recovery counters by tier" language implies pruning the `tool_timeout_count_*` trio.
2. **Rename aggressiveness.** The plan proposes only two high-payoff renames (`watchdog_unhealthy` → `unhealthy_reason`, `user_facing_routed` → `user_delivery_confirmed`) and fences off the accounting fields. Do you want a broader precision-rename pass across the liveness/runner fields, accepting the extra alias + mirror churn?
3. **`user_facing_routed` rename scope.** It is read alongside `messenger.has_communicated()` in the executor emoji branch. Renaming it touches the runner adapter, runner ExitSummary mirror, executor, and dashboard two-hop. Confirm the rename is worth that reach, or keep the current name.
