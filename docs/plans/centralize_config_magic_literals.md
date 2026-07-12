---
status: Ready
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1968
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-12T21:27:32Z
---

# Centralize Config Magic Literals + Audit settings/.env

## Problem

Timing/retry/TTL values are inlined at call sites across the codebase instead of
being sourced from the typed config catalog. The same semantic knob drifts to
different numbers in different files, and nothing is tunable without a code edit.
Meanwhile the catalog it should migrate *into* (`config/settings.py`) has its own
cruft: fields no code reads, an out-of-sync `.env.example`, a duplicated
`data_dir`, and at least one stale default.

**Current behavior:**
- ~179 inline subprocess/HTTP `timeout=` literals. One value, `timeout=10`, is
  copy-pasted ~40 times as a git/subprocess timeout with no shared constant;
  `timeout=5` ~35×; `timeout=30` ~25×. `config/settings.py:127` already defines
  `PerformanceSettings.timeout` that almost none of them reference.
- ~15 inline Redis TTLs where the same "1h lock" (`ex=3600`) and "2-min dedup"
  (`ex=120`) values are re-spelled in multiple files.
- ~10 inline sleeps/backoff; a handful of inline retry caps.
- 9 config fields with zero consumers, a stale `ServerSettings.port` default,
  a duplicated `data_dir`, and `.env.example` documenting only 6 of the nested
  `GROUP__` override keys.

**Desired outcome:**
- Every tunable timing/retry/TTL knob lives in `config/settings.py` as a typed,
  bounded, `.env`-overridable field; call sites read `settings.<group>.<field>`.
  Values that never need tuning become named module-level constants. No semantic
  knob has more than one source of truth.
- `config/settings.py` has no dead fields; `.env.example` documents the real
  override surface; no duplicate definitions; `ServerSettings.port` matches
  reality or is removed.
- A regression guard flags new inline `timeout=<int>` subprocess literals so the
  cleanup does not silently grow back (prevention at the creation site, not a
  recurring re-audit).

## Freshness Check

**Baseline at plan authoring:** `14d950c3` (2026-07-09)
**Re-verified against HEAD:** `1a23e1e8` (2026-07-12 re-critique)
**Issue filed at:** 2026-07-09T07:23:16Z
**Disposition:** DRIFTED — three major merges landed since authoring; inventory must be re-derived on current main before build.

**Config-anchor references re-verified on current main (all HOLD):**
- `config/settings.py:127` — `PerformanceSettings.timeout=30 ge=5 le=300` — holds.
- `config/settings.py:77` — `secret_key` — still present (audit target).
- `config/settings.py:69` — `ServerSettings.port default=8000` — still stale (UI runs on 8500; audit target).
- `config/settings.py:114` + `:476` — duplicated `data_dir` — both defs present (audit target; both have live consumers, see Concern in Critique Results).
- `model_config` `env_nested_delimiter="__"`, `VALOR_LAUNCHD` `.env` skip — holds.

**Merges on main since issue was filed (DRIFT — new literal sites this plan must cover):**
- **#2000 HarnessAdapter seam** (`347882f2`): new `agent/session_runner/harness/` — `harness/claude.py:1127` `asyncio.wait_for(proc.stdout.readline(), timeout=10.0)`. Added to subprocess-builder scope.
- **#1925 PydanticAI wrapper** (`443b5642`): new `agent/llm/wrapper.py` with a **double-timeout** pattern — `DEFAULT_SDK_TIMEOUT=30.0` / `DEFAULT_HARD_TIMEOUT=35.0` (wrapper.py:59-60), duplicated verbatim in `agent/memory_extraction.py:47-48` (`_EXTRACTION_SDK_TIMEOUT`/`_EXTRACTION_HARD_TIMEOUT`). The exact one-knob-two-files defect this plan targets. Both files added to http-ttl-builder scope; migration must preserve BOTH timers (see Solution).
- **#1927 AgentSession schema diet** (`1a23e1e8`): session-object TTL `models/agent_session.py:550` `ttl = 2592000` (30 days) exceeds the plan's original 604800s (7-day) bound; also `bridge/dedup.py:120` and `models/last_processed.py:40` at 2592000. Session-TTL upper bound raised to 2592000 (see Solution + Decision #3).

**Cited sibling issues/PRs re-checked:**
- #1693 (Ollama client consolidation) — closed; adjacent spirit (collapse duplicated call sites), different subsystem. Informs the "one source of truth" approach, no code conflict.

**Active plans in `docs/plans/` overlapping this area:** none touch `config/settings.py` structure or the timeout-literal surface.

## Prior Art

- **Issue/PR #1693**: "Consolidate three duplicated Ollama HTTP-client call sites into one internal client" — collapsed duplicated call sites behind one internal client. Same pattern this plan applies to timeout literals: replace N copies with one source of truth. Succeeded; no conflict.
- **Issue/PR #1111**: introduced `FeatureSettings.anthropic_concurrency` with the `FEATURES__` nested-delimiter env override — the canonical example of the migration target shape this plan follows.
- **`bridge_msg_claim_ttl_seconds` "GRAIN OF SALT" counter-precedent** (config/settings.py:357-380): a long TTL on a *claim lock* was a prior-critique BLOCKER because it orphaned a claim key on mid-window process death. This does NOT apply to session-lifetime TTLs (a session record living 30 days is fine; a claim lock held 30 days is not) — but the distinction must be honored: promote session-lifecycle TTLs freely to month-scale, keep short dedup/claim-lock TTLs bounded and named.
- No prior attempt to centralize the timeout/TTL literal surface specifically.

## Research

No relevant external findings — proceeding with codebase context and training data. `pydantic-settings` is an existing dependency and the nested-group + `env_nested_delimiter="__"` override pattern is already established in `config/settings.py`; no new library or ecosystem pattern is introduced.

## Data Flow

Config resolution is startup-time and single-directional:

1. **Entry point**: process start imports `config.settings`, constructs `settings = Settings()`.
2. **Env layer**: `pydantic-settings` reads `~/Desktop/Valor/.env` (via the repo symlink), applying `GROUP__FIELD` overrides through the `__` delimiter. Under `VALOR_LAUNCHD=1` the `.env` read is skipped — vars are pre-injected into the launchd plist.
3. **Validation**: each `Field` applies its typed default and `ge`/`le` bounds; invalid values raise at import.
4. **Consumption**: call sites read `settings.<group>.<field>` at use time (e.g. `subprocess.run(..., timeout=settings.timeouts.git_subprocess_s)`).

The migration adds new fields to step 3 and rewires step 4 consumers; it does not change the resolution mechanism.

## Architectural Impact

- **New dependencies**: none (pydantic-settings already present).
- **Interface changes**: new `Settings` sub-groups (e.g. `TimeoutSettings`, optionally `TtlSettings`/`RetrySettings`); call sites gain a `settings` import where they used a bare literal.
- **Coupling**: net decrease — N duplicated literals collapse to 1 typed field. A small increase in modules importing `settings`, which is already ubiquitous.
- **Data ownership**: `config/settings.py` becomes the single owner of tunable timing values.
- **Reversibility**: high. Defaults preserve behavior; reverting is a mechanical revert. The regression guard is additive and can be disabled independently.

## Appetite

**Size:** Large

**Team:** Solo dev + parallel builders (by subsystem) + code reviewer

**Interactions:**
- PM check-ins: 1-2 (taxonomy sign-off, normalization-vs-preserve decision)
- Review rounds: 1-2 (large mechanical diff; reviewer confirms defaults unchanged and no missed call sites)

The coding is mechanical; the cost is breadth (~200 sites) and the one real design decision (timeout normalization). Large appetite covers the fan-out and review, not algorithmic difficulty.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are to tracked source under `config/`, the audited subsystems, and `.claude/hooks/`.

## Solution

- **`TimeoutSettings` group** (env prefix `TIMEOUTS__`): one **general** system-timing config group — not a rigid taxonomy of exclusive sub-categories. Collapse the arbitrarily-drifted values into a small set of normalized, generously-commented fields (e.g. `git_subprocess_s`, `subprocess_default_s`, `http_request_s`, `smtp_s`, `redis_socket_s`, and the paired `anthropic_sdk_s` / `anthropic_hard_s` for the #1925 double-timeout sites). Every field carries an inline comment explaining what it's for and why the default is what it is, matching the existing `FeatureSettings` commenting style. Add fields only where they earn their keep; do not manufacture distinctions the code doesn't need.
- **Session-object TTLs** (`AgentSession` and things sessions use): these run **up to 30 days** (`2592000s`) in current main — `models/agent_session.py:550` `ttl = 2592000` (the `retain_for_resume` BUILD-session backstop) and `models/last_processed.py:40` `ttl = 2592000` are Popoto `Meta.ttl` class constants (the 7-day root-id mapping at `bridge/context.py:528` is a shorter precedent). Promote these session-lifecycle TTLs to `.env`-overridable settings fields with a `le=2592000` upper bound (defaulting to each site's current value); a 7-day bound would reject the live 30-day value. **Scope assignment (no overlap):** the two `models/` `Meta.ttl` sites (`models/agent_session.py:550`, `models/last_processed.py:40`) are owned by the **audit builder** (Step 5, serial — `models/` is in no parallel builder's scope, so no worktree race). The freeform observability TTL `bridge/dedup.py:120` (`_LAST_EVENT_TTL_SECONDS=2592000`) is owned by **http-ttl-builder** under its `bridge/` scope (it is not a Popoto model; leave it a named constant or promote at that builder's discretion). Non-session TTLs (short dedup/lock windows like `ex=120`) stay named module-level constants at builder discretion, reused across the sites that share them (extend the existing `OUTBOX_TTL` pattern).
- **Call-site rewiring**: replace inline literals with `settings.timeouts.<field>` / `settings.<group>.<field>` or the named constant. Where a hard timeout is genuinely runtime-dependent (its right value depends on how the process is running), the migration maps it to a **large-but-finite `settings` ceiling** (week-scale is sanctioned) rather than pinning a made-up short number — never remove the cap entirely (see Technical Approach; removing a cap on a worker-critical subprocess is a BLOCKER, resolved in Critique Results).
- **Double-timeout sites (#1925)**: the PydanticAI wrapper (`agent/llm/wrapper.py`) and `agent/memory_extraction.py` use a deliberate two-timer pattern — an inner SDK-level `timeout` plus an outer `asyncio.wait_for` hard cap (hotfix #1055). Promote BOTH timers to paired `settings` fields (e.g. `anthropic_sdk_s` / `anthropic_hard_s`) and preserve the two-timer structure; do NOT collapse them to one value.
- **Catalog audit + cleanup**: delete verified-dead fields, de-duplicate `data_dir`, fix/remove the stale `ServerSettings.port`, and regenerate/repair `.env.example` to document the real override surface.
- **Regression guard**: a validator under `.claude/hooks/validators/` that flags new inline `timeout=<int>` in `subprocess`/`requests` calls, with a test proving it fires.

### Flow

Call site with `timeout=10` → replace with `settings.timeouts.git_subprocess_s` (default 10) → same runtime behavior, now discoverable and `.env`-overridable → guard blocks reintroduction of a bare literal.

### Technical Approach

Four decisions are settled (supervisor sign-off, 2026-07-09):

- **Collapse and normalize — the drifted values are arbitrary.** The 5/10/30 spread across the ~150 git/subprocess sites reflects copy-paste drift, not deliberate per-site tuning. Collapse each into a single normalized field. Normalizing to the **longest** value in a category is safe by default (a longer timeout only delays failure detection on the hang path, never breaks a working call). This is an intended, documented normalization, not a "no behavior change" constraint — the arbitrary short values were the defect.
- **Runtime-dependent caps become a large finite ceiling, never `None`.** For a subprocess/HTTP call whose correct timeout genuinely depends on how the process is running (workload, machine, interactive vs. headless), map it to a **large-but-finite `settings.timeouts.*` ceiling** (week-scale is already sanctioned) rather than an invented short number. Removing the cap entirely is forbidden: the worker is the sole serial session-execution engine, so an uncapped `subprocess.run`/`Popen` (git/gh credential prompt, `.git/index.lock` contention, stalled fetch/push) wedges the worker indefinitely and "log a warning" cannot unblock it. Step 6 enforces a grep gate that no `subprocess.run`/`Popen` loses its `timeout=`. Genuine cap removal is deferred to the No-Gos re-tuning follow-up where a human justifies each one. Never loosen a watchdog/health-probe fast-fail cap either.
- **One general, well-commented config group — no exclusive taxonomy.** These are general system-timing settings; do not over-partition into narrow exclusive subgroups. A single `TimeoutSettings` group (plus session-TTL fields), each field generously inline-commented for what it controls and why its default was chosen.
- **Session-object TTLs may be month-scale.** `AgentSession` and session-used objects get `.env`-overridable TTL fields with upper bounds up to 30 days (`2592000s`); current main already runs a 30-day `retain_for_resume` TTL at `models/agent_session.py:550` and a 7-day precedent at `bridge/context.py:528`. Short non-session dedup/lock TTLs stay named constants at builder discretion.
- **Promote-vs-name-locally criterion**: promote to a `settings` field if duplicated across ≥2 modules, plausibly tuned per-machine, or a session-lifecycle TTL. Name-locally (module constant) for logic-coupled short one-offs.
- **Ship as ONE PR** (supervisor decision). The build fans out to parallel builders by non-overlapping file scope, but all batches land in a single reviewable PR. Internal order: scaffold → subprocess sweep + http/ttl sweep + guard (parallel) → audit → validation.
- **`VALOR_LAUNCHD` propagation**: any new `.env` key that a worker/bridge service reads must be added to the launchd plist injection path in `scripts/update/`, not only to `.env`.
- **Dead-field verification**: before deleting any zero-usage field, confirm nothing reads `settings` reflectively (grep for `getattr(settings`, `settings.dict(`, `model_dump`) AND that the field is not consumed via its env key directly. Some fields are read through `os.environ.get(...)` rather than `settings.<field>` (e.g. `notify_healthcheck_interval` at settings.py:582-593, `bridge_msg_claim_ttl_seconds`), so a zero-`settings.<field>` grep can hide a live override. For each of the 9 candidates derive its env key (group prefix + `__` + FIELD upper, e.g. `NOTIFY_HEALTHCHECK_INTERVAL`, `FEATURES__BRIDGE_MSG_CLAIM_TTL_SECONDS`) and run `git grep -nE 'environ\.get\(.?<KEY>|getenv\(.?<KEY>'`; a hit means retain the field. Grep-absence across both checks is necessary but still not sufficient.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The migration touches many `subprocess.run(..., timeout=...)` sites wrapped in `try/except subprocess.TimeoutExpired`. Confirm no such handler is silently changed; the timeout value swap must not alter which branch executes on success. No new `except Exception: pass` blocks are introduced.
- [ ] The regression-guard validator must log/print an actionable message (not swallow) when it rejects a file — asserted by its test.

### Empty/Invalid Input Handling
- [ ] New `Settings` fields carry `ge`/`le` bounds so an invalid `.env` override (`TIMEOUTS__GIT_SUBPROCESS_S=abc` or a negative) raises at import rather than silently defaulting. Add a test asserting an out-of-bounds override raises `ValidationError`.
- [ ] The guard must handle files with no timeout literals (empty match set) without error.

### Error State Rendering
- [ ] Guard rejection surfaces a clear message pointing at the offending file:line and the correct `settings.timeouts.*` field to use — tested against a deliberately-violating fixture.

## Test Impact

- [ ] the config test module (`tests/unit/test_config_consolidation.py` / `test_config_machine.py`; note `tests/unit/test_settings.py` does not exist — create it or extend an existing module) — UPDATE: add cases for the new `TimeoutSettings` group defaults, bounds, and `.env` override via `TIMEOUTS__*`; assert removed dead fields no longer exist on `Settings`.
- [ ] `tests/**/test_*doctor*.py`, watchdog/branch/worktree manager tests, if any assert on hardcoded timeout values — UPDATE: reference the new settings field instead of the literal.
- [ ] New test file for the regression guard — REPLACE/CREATE: `tests/unit/test_validate_no_inline_timeout.py` proving the guard fires on a violating fixture and passes on a compliant one.
- [ ] `.env.example` completeness check (existing) — must continue to pass after adding the new `TIMEOUTS__*` keys with comments.

Normalization is APPROVED (Decision #1): any test asserting an exact shorter timeout value at a normalized site is UPDATE'd to the category's longest value. Tests on non-normalized sites are unaffected.

## Rabbit Holes

- **Re-tuning while migrating.** This is a refactor, not a performance pass. Do not "improve" any timeout/TTL value beyond the agreed normalization. Changing values is a separate exercise.
- **Over-abstracting the config taxonomy.** Resist building a generic "duration registry" or reflection-driven config. Add plain typed fields matching the existing style.
- **Chasing every last literal.** A `time.sleep(0.1)` poll interval local to one function that no one would ever tune is fine as a named local constant. The goal is eliminating *duplicated/undiscoverable* knobs, not achieving zero integer literals.
- **Rewriting the retry constants that are already named.** `MAX_EMAIL_RELAY_RETRIES`, `SMTP_MAX_RETRIES`, etc. are already correct — leave them.

## Risks

### Risk 1: A missed call site keeps a hardcoded value while its siblings move
**Impact:** The "one source of truth" invariant is silently violated; the drift persists.
**Mitigation:** The regression-guard grep doubles as a completeness check — after each batch, `git grep -nE 'timeout\s*=\s*[0-9]'` over migrated dirs must return only `settings`/constant references. A Verification row asserts this.

### Risk 2: Normalization (or a dropped cap) changes behavior on a latency-sensitive path
**Impact:** A call that intentionally used a short timeout (fast-fail) now waits longer or has no cap, delaying a failover on a path where a hang is the guarded failure mode.
**Mitigation:** Normalize to the longest value per category (delays failure detection only, never breaks success). Runtime-dependent caps map to a large-but-finite `settings` ceiling — never removed (an uncapped worker subprocess wedges the sole serial engine; BLOCKER resolved in Critique Results). Step 6 enforces a grep gate that no `subprocess.run`/`Popen` loses its `timeout=`. Reviewer specifically audits `monitoring/*_watchdog.py` and health-probe sites to confirm no fast-fail cap was loosened or removed.

### Risk 3: Deleting a field that is read reflectively or by an external tool
**Impact:** Runtime `AttributeError` or a silently-broken integration.
**Mitigation:** Grep for reflective access (`getattr(settings`, `model_dump`, `.dict(`) before deleting; run the full import + test suite; delete in the audit batch only after the earlier batches are green.

### Risk 4: New `.env` key not propagated to launchd machines
**Impact:** A worker/bridge on a launchd machine ignores an override set in `.env`.
**Mitigation:** Update System section mandates adding service-relevant keys to the plist injection path; call it out in the audit batch. Defaults are safe regardless, so absence degrades to default, not breakage.

## Race Conditions

No race conditions identified. Config resolution is synchronous and happens once at process startup; the migration swaps literal values for equivalent field reads without introducing shared mutable state or new concurrency. The one exception field already documented as read via `os.environ.get(...)` for live-reload (`notify_healthcheck_interval`) is not modified by this plan.

## No-Gos (Out of Scope)

- [ORDERED] Re-tuning any timeout/TTL/retry value beyond the agreed per-category normalization — blocked until the migration lands and a human decides a value should actually change; that is a follow-up with its own justification.
- Nothing else deferred — the full literal migration, the catalog audit, dead-field removal, `.env.example` sync, and the regression guard are all in scope for this plan.

## Update System

- **New `.env` keys**: the `TIMEOUTS__*` overrides (and any promoted TTL/retry keys) are additive with safe defaults. Add them to `.env.example` with a comment above each `KEY=` (required by the completeness check).
- **launchd propagation**: for any new key a worker/bridge service actually reads at runtime, add it to the plist env-injection in `scripts/update/` so launchd machines honor overrides (defaults apply if absent). Most timeout knobs are read by processes that also read `.env`, so verify per-key.
- **No migration function** required — this is config/constants, not a Popoto schema change. Promoting the two `models/` `Meta.ttl` session-TTL constants (`models/agent_session.py:550`, `models/last_processed.py:40`) to a settings field keeps the **exact same default (`2592000`)**, so no field is added/renamed, no index changes, and no stored record is rewritten. It is a value-source change, not a schema change; `scripts/update/migrations.py` needs no new entry.

## Agent Integration

No agent integration required — this is an internal configuration refactor. No new CLI entry point, MCP tool, or `.mcp.json` change. The agent reaches nothing new; it continues to call the same tools, which now read timeouts from `settings`. The regression guard runs as a repo hook, not an agent-facing surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/config-timeout-catalog.md` documenting the `TimeoutSettings`/TTL/retry catalog: each field, its default, its env-override key, and the promote-vs-name-locally criterion for future additions.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Each new `Settings` field carries a description naming its env var (matching existing style).
- [ ] The regression-guard validator carries a module docstring explaining what it flags and how to satisfy it.

### CLAUDE.md
- [ ] Add a short note to the "Configuration Files" / secrets area pointing at the new timeout catalog as the home for tunable timing values.

## Success Criteria

- [ ] `TimeoutSettings` (and any TTL/retry additions) exist in `config/settings.py` with typed defaults, bounds, and env-override descriptions.
- [ ] The git/gh subprocess-timeout family reads from `settings`; `git grep -nE 'timeout\s*=\s*[0-9]'` over the shared `$MIGRATED_DIRS` path list (the single source of truth, defined once in the Verification section below — the union of the two builders' assigned-file lists) shows only `settings`/constant references. This criterion and the Step-8 Verification row MUST reference the same `$MIGRATED_DIRS` variable so a stray `timeout=30` left in http/ttl scope (e.g. `bridge/`, `reflections/`, `agent/llm/`) cannot slip past one gate while failing the other. Derive `$MIGRATED_DIRS` mechanically from the assigned-file lists; do NOT include `worker/` (no builder is assigned files there).
- [ ] The `ex=3600` and `ex=120` TTL duplicates collapse to one named constant each, reused at every site.
- [ ] Every zero-usage field is deleted (after reflective-access verification) or documented; `data_dir` is defined once; `ServerSettings.port` matches reality or is removed.
- [ ] `.env.example` documents every new override key with a comment; the completeness check passes.
- [ ] A regression-guard validator flags new inline subprocess `timeout=<int>`; a test proves it fires on a violating fixture and passes on a compliant one.
- [ ] No worker-critical subprocess lost its cap: `git grep -nE 'subprocess\.run\((?![^)]*timeout=)'` over migrated dirs returns 0.
- [ ] End-to-end tunability: a `TIMEOUTS__*` env override changes an observed runtime value (not merely that a bad value raises `ValidationError`).
- [ ] `python -c "from config.settings import settings"` imports clean; config tests pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead orchestrates; builders fan out by subsystem so the large mechanical diff parallelizes without call-site collisions. Assign explicit non-overlapping file scopes per builder, each in its **own worktree** (`.worktrees/sdlc-subproc` for subprocess-builder, `.worktrees/sdlc-http` for http-ttl-builder) — a shared checkout races the git index/HEAD even with disjoint file lists (documented failure mode in this repo). Both parallel builders branch from the commit that already contains the Step-1 `TimeoutSettings` field names (to avoid `AttributeError` on `settings.timeouts.<field>`); the lead merges each worktree into the single PR branch sequentially.

### Team Members

- **Builder (settings-scaffold)**
  - Name: settings-builder
  - Role: Add `TimeoutSettings` (+ any TTL/retry fields) to `config/settings.py`; update `.env.example`; own the catalog audit + dead-field removal; promote the two `models/` `Meta.ttl` session-TTL sites (`models/agent_session.py:550`, `models/last_processed.py:40`) to the new settings TTL field(s) in Step 5 (serial — no worktree needed).
  - Agent Type: builder
  - Resume: true

- **Builder (git-subprocess-sweep)**
  - Name: subprocess-builder
  - Role: Rewire the git/gh/subprocess timeout family in `agent/branch_manager.py`, `agent/worktree_manager.py`, `agent/session_logs.py`, `agent/completion.py`, `agent/session_revival.py`, `agent/session_runner/harness/` (new #2000 seam — `harness/claude.py:1127` `timeout=10.0` stdout read), `monitoring/*_watchdog.py`, `monitoring/crash_tracker.py`, `tools/doctor.py`.
  - Worktree: `.worktrees/sdlc-subproc`
  - Agent Type: builder
  - Resume: true

- **Builder (http-ttl-sweep)**
  - Name: http-ttl-builder
  - Role: Rewire HTTP/SMTP/Redis/Anthropic client timeouts, inline sleeps, and the `ex=3600`/`ex=120` TTL consolidation in `bridge/` (including `bridge/dedup.py:120` `_LAST_EVENT_TTL_SECONDS`), `reflections/`, `agent/session_completion.py`, `agent/session_health.py`, `agent/messenger.py`, and the new #1925 double-timeout sites `agent/llm/wrapper.py` + `agent/memory_extraction.py` (promote both the SDK and hard timers as a paired field set; preserve the two-timer structure). Does NOT touch `models/` — those session-TTLs belong to the audit builder (Step 5).
  - Worktree: `.worktrees/sdlc-http`
  - Agent Type: builder
  - Resume: true

- **Builder (regression-guard)**
  - Name: guard-builder
  - Role: Write `.claude/hooks/validators/validate_no_inline_timeout.py` + its test.
  - Agent Type: builder
  - Resume: true

- **Validator (normalization-correct)**
  - Name: migration-validator
  - Role: Verify each migrated field default equals the category's LONGEST pre-existing literal (a value change at short sites is EXPECTED under Decision #1, not a defect); confirm double-timeout sites keep BOTH timers; run grep-completeness per batch; audit `monitoring/*_watchdog.py` + health-probe sites to confirm none moved from a short bounded cap to a longer/removed cap; confirm no reflective- or env-key-accessed field was deleted.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: config-documentarian
  - Role: Feature doc + README index + CLAUDE.md note.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core agents (`builder`, `validator`, `documentarian`) cover this work; no domain specialist required beyond a `Domain: async/subprocess` framing note for the subprocess-builder (timeout semantics on hang/failure paths).

## Step by Step Tasks

### 1. Settings scaffolding
- **Task ID**: build-settings-scaffold
- **Depends On**: none
- **Validates**: `tests/unit/test_settings.py` (create/update), `python -c "from config.settings import settings"`
- **Assigned To**: settings-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `TimeoutSettings` group (`git_subprocess_s`, `gh_cli_s`, `subprocess_default_s`, `http_request_s`, `smtp_s`, `redis_socket_s`, and the paired #1925 double-timeout fields `anthropic_sdk_s` / `anthropic_hard_s`) with defaults equal to the current per-category canonical value, `ge`/`le` bounds, and env-var descriptions. The `anthropic_sdk_s` / `anthropic_hard_s` pair is mandatory in this scaffold: Step 1 is the shared commit both parallel builders branch from, and http-ttl-builder rewires the double-timeout sites (`agent/llm/wrapper.py`, `agent/memory_extraction.py`) against these exact field names. A singular `anthropic_client_s` would leave that builder hitting `AttributeError` on `settings.timeouts.anthropic_sdk_s`/`anthropic_hard_s`, or force it to add the missing fields to `config/settings.py` outside settings-builder's exclusive scope — a merge conflict at the single-PR merge (see the worktree-isolation invariant in Team Orchestration).
- Wire the group into `Settings` via `Field(default_factory=TimeoutSettings)`.
- Decide (per Open Question resolution) whether TTL/retry get promoted fields or named constants; scaffold accordingly.

### 2. Git/subprocess timeout sweep
- **Task ID**: build-subprocess-sweep
- **Depends On**: build-settings-scaffold
- **Validates**: `git grep -nE 'timeout\s*=\s*[0-9]'` over the assigned files returns only settings references; existing subsystem tests
- **Assigned To**: subprocess-builder
- **Agent Type**: builder
- **Domain**: async/subprocess
- **Parallel**: true
- Replace every inline git/gh/subprocess `timeout=` literal in the assigned files with `settings.timeouts.<field>`.
- Map each literal to its category's normalized field (longest value per Decision #1). Runtime-dependent caps go to a large finite ceiling, never removed.

### 3. HTTP/TTL/sleep sweep
- **Task ID**: build-http-ttl-sweep
- **Depends On**: build-settings-scaffold
- **Validates**: grep-completeness over assigned files; `bridge`/`reflections` tests
- **Assigned To**: http-ttl-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewire HTTP/SMTP/Redis/Anthropic client timeouts and inline sleeps to `settings`.
- Collapse `ex=3600` and `ex=120` duplicates to one named constant each, reused at every site.

### 4. Regression guard
- **Task ID**: build-guard
- **Depends On**: build-settings-scaffold
- **Validates**: `tests/unit/test_validate_no_inline_timeout.py`
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Write `.claude/hooks/validators/validate_no_inline_timeout.py` flagging new inline `timeout=<int>` in `subprocess`/`requests` calls; provide an allowlist mechanism for genuinely local one-offs.
- Write the test proving it fires on a violating fixture and passes on a compliant one (red-state proof).

### 5. Catalog audit + .env sync
- **Task ID**: build-audit
- **Depends On**: build-subprocess-sweep, build-http-ttl-sweep
- **Validates**: `.env.example` completeness check; `tests/unit/test_settings.py`
- **Assigned To**: settings-builder
- **Agent Type**: builder
- **Parallel**: false
- Verify reflective-access AND env-key absence (see Technical Approach) per field, then delete the confirmed-dead zero-usage fields; re-verify the "9 fields" count on current main first (#1925/#1927 may have added or removed consumers).
- Promote the two `models/` `Meta.ttl` session-TTL sites (`models/agent_session.py:550`, `models/last_processed.py:40`) to the new settings TTL field(s) (default `2592000`, `le=2592000`). Same default value ⇒ no stored-data change ⇒ no Popoto migration (see Update System). Runs serial here; no worktree race with the parallel sweeps.
- De-duplicate `data_dir` by resolving ownership, NOT by dropping a definition: both `WorkspaceSettings.data_dir` (settings.py:114, read by `create_directories` at :646) and `PathSettings.data_dir` (settings.py:476, derived in `model_post_init`) have live consumers. Specify which owner survives and rewire the other consumer to it. Gate each deletion behind `git grep -nE 'getattr\(settings|model_dump|\.dict\('` absence.
- Fix/remove `ServerSettings.port` (default 8000 is stale; UI runs on 8500).
- Regenerate/repair `.env.example`: add the new `TIMEOUTS__*` keys with comments; remove stale keys.
- Add launchd plist propagation in `scripts/update/` for any service-read key.

### 6. Migration validation
- **Task ID**: validate-migration
- **Depends On**: build-subprocess-sweep, build-http-ttl-sweep, build-guard, build-audit
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm each migrated field default equals its category's LONGEST pre-existing literal (Decision #1 normalization — value changes at short sites are expected, NOT defects). Do NOT diff-audit "default == original literal per site" — that gate is incompatible with normalization and would deadlock.
- Confirm the #1925 double-timeout sites (`agent/llm/wrapper.py`, `agent/memory_extraction.py`) retain both the inner SDK timer and the outer hard cap.
- Grep gate: no worker-critical `subprocess.run`/`Popen` lost its cap — `git grep -nE 'subprocess\.run\((?![^)]*timeout=)'` over migrated dirs returns 0.
- Audit `monitoring/*_watchdog.py` + health-probe sites: confirm no fast-fail cap was loosened or removed.
- Run the per-batch grep-completeness checks; confirm no reflective- or env-key-accessed field was deleted.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-migration
- **Assigned To**: config-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/config-timeout-catalog.md`; add README index entry; add the CLAUDE.md note.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table; confirm all success criteria (including docs).

## Verification

**Shared migrated-dirs path list (single source of truth).** Both the Success Criteria completeness check and the Step-8 Verification row below reference this one variable so the two gates cannot diverge. Define it once before running the table:

```bash
MIGRATED_DIRS="agent/branch_manager.py agent/worktree_manager.py agent/session_logs.py agent/completion.py agent/session_revival.py agent/session_runner/harness/ agent/session_completion.py agent/session_health.py agent/messenger.py agent/llm/ agent/memory_extraction.py bridge/ reflections/ monitoring/ tools/doctor.py"
```

This is the mechanical union of the two builders' assigned-file lists (`worker/` deliberately excluded — no builder is assigned files there). Update it only by editing this one block.

| Check | Command | Expected |
|-------|---------|----------|
| Settings import clean | `python -c "from config.settings import settings; settings.timeouts"` | exit code 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No inline git/subprocess timeout literals remain (migrated dirs) | `git grep -nE 'timeout\s*=\s*[0-9]' -- $MIGRATED_DIRS \| grep -v settings\.` | match count == 0 |
| Dead field removed (secret_key) | `git grep -n 'secret_key' -- config/settings.py` | exit code 1 |
| data_dir defined once | `git grep -cn 'data_dir: Path' -- config/settings.py` | output contains 1 |
| Guard test present and passing | `pytest tests/unit/test_validate_no_inline_timeout.py -q` | exit code 0 |
| .env.example has TIMEOUTS keys | `grep -c 'TIMEOUTS__' .env.example` | output > 0 |

## Resolved Decisions (supervisor, 2026-07-09)

1. **Normalize, don't preserve.** The drifted 5/10/30 values are arbitrary — collapse each category to one normalized field (default to the longest safe value). Where the right value is runtime-dependent, use a large-but-finite `settings` ceiling — never remove the cap (uncapped worker subprocess = indefinite wedge; BLOCKER resolved in Critique Results). Watchdog/health-probe fast-fail caps stay bounded and untouched.
2. **One general config group**, no exclusive taxonomy; generous inline comments on every field explaining purpose and default choice.
3. **Session-object TTLs may be month-scale** (`AgentSession` and session-used objects, up to `2592000s`/30 days, `.env`-overridable — current main runs a 30-day `retain_for_resume` TTL). Other short TTLs stay named constants at builder discretion.
4. **One big PR** — parallel builders by file scope, single reviewable PR.

## Critique Results

**Verdict: NEEDS REVISION** (recorded 2026-07-12, re-critique run `e83cbeeb`). 2 blockers + inventory drift since the 2026-07-09 baseline. Re-run against HEAD `1a23e1e8` (was `14d950c3`); merges #2000 (HarnessAdapter), #1925 (PydanticAI wrapper), #1927 (AgentSession schema diet) all landed and introduced new literal sites the original plan does not cover.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Validator gate "verify every migrated default equals the original literal" (role `no-behavior-change`) contradicts approved Decision #1 (normalize to LONGEST value — a `timeout=5` site deliberately becomes `30`). Deadlocks Step 6. | Rewrote Team Member `migration-validator` role + Step 6 to the correct two-part invariant. | Invariant: (a) migrated default == category's LONGEST pre-existing literal (value change at short sites is EXPECTED); (b) allowlist audit of `monitoring/*_watchdog.py` + health-probe sites confirming none moved from a short bounded cap to longer/no cap. Do NOT diff-audit "default == original literal per site." |
| BLOCKER | Risk (Adversary) + Scope (Simplifier) | "Drop-the-cap-and-warn" (Technical Approach) turns worker-critical `subprocess.run`/`Popen` into an unbounded hang (credential prompt, `.git/index.lock`, stalled fetch); worker is the sole serial engine — a hang wedges it and "log a warning" does nothing. New failure mode with zero incident evidence. | Replaced drop-the-cap with a large-but-finite ceiling; added a Step 6 grep gate forbidding `timeout=None`/omitted timeout. | Map "runtime-dependent" to a large finite `settings.timeouts.*` ceiling (week-scale sanctioned), never `None`. Step 6 gate: `git grep -nE 'subprocess\.run\((?![^)]*timeout=)'` over migrated dirs == 0. Genuine cap removal deferred to the No-Gos re-tuning follow-up. |
| BLOCKER | Inventory Drift (re-critique mandate) | Freshness Check (lines 46-62) claims baseline `14d950c3`, "HEAD unchanged," "Commits on main since issue filed: none" — now FALSE. HEAD is `1a23e1e8`; #2000/#1925/#1927 landed and touched timeout/TTL surfaces. | Rewrote Freshness Check to current main. | The inventory numbers and per-builder file lists must be re-derived on current main before build (see the four rows below). |
| CONCERN | Inventory Drift (#1925) | New duplicated timeout knob: `agent/llm/wrapper.py:59-60` (`DEFAULT_SDK_TIMEOUT=30.0`, `DEFAULT_HARD_TIMEOUT=35.0`) is duplicated verbatim in `agent/memory_extraction.py:47-48` (`_EXTRACTION_SDK_TIMEOUT`/`_EXTRACTION_HARD_TIMEOUT`) — the exact "one knob, two files" defect this plan targets — yet neither file is in any builder's scope. | Added both files to http-ttl-builder scope + a Solution note. | This is a deliberate **double-timeout** pattern (inner SDK timer + outer `asyncio.wait_for` hard cap, hotfix #1055). Migration must promote BOTH timers to paired `settings` fields and preserve the two-timer structure — do NOT collapse to a single value. |
| CONCERN | Inventory Drift (#2000) | New harness read timeout `agent/session_runner/harness/claude.py:1127` `asyncio.wait_for(proc.stdout.readline(), timeout=10.0)` — the normalized-turn-event seam — is not in any builder's file scope. | Added `agent/session_runner/harness/` to subprocess-builder scope. | This is a stdout-read liveness cap; treat like a health-probe fast-fail path (keep a bounded finite `settings` field, never drop). |
| CONCERN | Inventory Drift (#1927) | Session-object TTL `models/agent_session.py:550` `ttl = 2592000` (30 days) sits squarely in Decision #3's domain but EXCEEDS the plan's proposed `604800s` (7-day) upper bound; also `bridge/dedup.py:120` `_LAST_EVENT_TTL_SECONDS=2592000` and `models/last_processed.py:40`. None enumerated; a naive `le=604800` bound would reject the live 30-day value. | Raised the session-TTL upper bound to 30 days (`2592000`) and enumerated the sites. | Decision #3 said "up to a week"; reality is 30 days for `retain_for_resume` BUILD sessions. Set the field bound to `le=2592000`, default to the current per-site value, and list these `models/` sites for the audit builder. |
| CONCERN | Risk (Skeptic) | Dead-field detection greps only reflective `settings` access + `settings.<field>`; misses fields read via `os.environ.get(...)` (e.g. `notify_healthcheck_interval` at settings.py:582-593, `bridge_msg_claim_ttl_seconds`). A field with zero `settings.<field>` hits can be a live documented override. | Added env-key grep step to Technical Approach dead-field verification. | For each of the 9 candidates derive its env key (group prefix + `__` + FIELD upper) and run `git grep -nE 'environ\.get\(.?<KEY>|getenv\(.?<KEY>'`; a hit means retain. |
| CONCERN | History & Consistency | The two completeness greps cover different surfaces: Success Criteria greps `agent/ bridge/ worker/`; Verification table greps specific files + `monitoring/` + `tools/doctor.py`. Neither is a superset — `monitoring/`/`tools/doctor.py` outside Success dirs; `worker/` checked nowhere and assigned to nobody. | Unified the grep path list; dropped unassigned `worker/`. | Derive the grep paths mechanically from the union of both builders' assigned-file lists into one shared path var referenced by both the Success Criterion and Verification row. |
| CONCERN | Scope (Simplifier) | Catalog audit (`secret_key` delete, `ServerSettings.port` fix, `data_dir` de-dup) shares no code with the timeout sweep but carries the highest-risk failure (AttributeError from deleting a reflectively-read field). `data_dir` can't just be deleted: both defs have live consumers (`WorkspaceSettings.data_dir:114` read by `create_directories:646`; `PathSettings.data_dir:476` derived in `model_post_init`). | Step 5 already ordered last; specified which `data_dir` owner survives + per-field grep gate. | Gate each deletion behind `git grep -nE 'getattr\(settings|model_dump|\.dict\('` absence; "de-duplicate" must resolve ownership, not drop one def. |
| CONCERN | Risk (Operator) | Parallel builders `subprocess-builder` + `http-ttl-builder` fan into one branch with only "non-overlapping file scope" and no explicit per-builder worktree — a documented commit-race failure mode in this repo. | Added explicit per-builder worktree paths to Team Orchestration. | Each parallel builder gets its own worktree (`git worktree add .worktrees/sdlc-subproc session/<slug>` vs `.worktrees/sdlc-http ...`); lead merges each into the single PR branch sequentially. Both must start from the commit containing the `TimeoutSettings` field names (Step 1) to avoid `AttributeError`. |
| NIT | History & Consistency | Prior Art omits the `bridge_msg_claim_ttl_seconds` "GRAIN OF SALT" counter-precedent (settings.py:357-380) where a long TTL was a prior-critique BLOCKER. Session-lifetime vs dedup-claim distinction is sound — only the citation is missing. | Added the counter-precedent citation to Prior Art. | Cite the distinction so a reviewer sees week/month-scale session TTLs were vetted against the claim-lock mistake. |
| NIT | Scope (User) | All Success Criteria are grep/import/test-pass. None validates the operational benefit ("nothing tunable without a code edit"). | Added one end-to-end tunability criterion. | Assert a `TIMEOUTS__*` override actually changes an observed runtime value, not just that `ValidationError` fires on a bad value. |

**Structural check:** Required sections present; task numbering 1-8 gap-free; dependencies acyclic. Settings.py anchors re-verified on current main and all HOLD: `secret_key:77`, `data_dir:114/476`, `ServerSettings.port=8000:69`, `PerformanceSettings.timeout:127` — no dead config-field references to fix. `tests/unit/test_settings.py` does not exist (real config modules: `test_config_consolidation.py`, `test_config_machine.py`) — Step 1 must create it or target the real module.

**Inventory count corrections (current main):** raw non-test `timeout=` literals = 352 (was ~343 mid-critique; grew ~9 from the three merges) vs the plan's ~179 subprocess/HTTP-scoped estimate; TTL-ish (`ex=`/`setex`/`expire`) = 38 vs the plan's ~15. The plan's numbers are narrower subsets, but the raw counts confirm drift — re-derive the scoped inventory and the per-builder file lists on current main before dispatching build.
