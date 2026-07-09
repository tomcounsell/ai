---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1968
last_comment_id:
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

**Baseline commit:** `14d950c3`
**Issue filed at:** 2026-07-09T07:23:16Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `config/settings.py:127` — `PerformanceSettings.timeout=30 ge=5 le=300` — still holds.
- `config/settings.py:483-493` — `model_config` with `env_nested_delimiter="__"`, `VALOR_LAUNCHD` `.env` skip — still holds.
- Zero-usage grep for the 9 dead fields — re-run at plan time, still 0 usages outside `config/settings.py`.

**Cited sibling issues/PRs re-checked:**
- #1693 (Ollama client consolidation) — closed; adjacent spirit (collapse duplicated call sites) but a different subsystem. Informs the "consolidate to one source of truth" approach, no code conflict.

**Commits on main since issue was filed (touching referenced files):** none — HEAD unchanged at `14d950c3`.

**Active plans in `docs/plans/` overlapping this area:** none. Keyword scan surfaced plans that mention `.env`/`timeout` in passing (`consolidate_delivery_paths`, `headless-runner-zombie-liveness`) but none touch `config/settings.py` structure or the timeout-literal surface.

## Prior Art

- **Issue/PR #1693**: "Consolidate three duplicated Ollama HTTP-client call sites into one internal client" — collapsed duplicated call sites behind one internal client. Same pattern this plan applies to timeout literals: replace N copies with one source of truth. Succeeded; no conflict.
- **Issue/PR #1111**: introduced `FeatureSettings.anthropic_concurrency` with the `FEATURES__` nested-delimiter env override — the canonical example of the migration target shape this plan follows.
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

### Key Elements

- **`TimeoutSettings` group** (env prefix `TIMEOUTS__`): typed, bounded fields for the recurring subprocess/HTTP timeout categories — `git_subprocess_s`, `gh_cli_s`, `subprocess_default_s`, `http_request_s`, `smtp_s`, `redis_socket_s`, `anthropic_client_s`. Each carries a description naming its env var, matching the existing `FeatureSettings` style.
- **Named module-level TTL/retry constants**: for lock/dedup TTLs that are logic-coupled and not per-machine-tuned (`ex=3600`, `ex=120`), a single named constant per semantic value, reused across the sites that share it (extend the existing `OUTBOX_TTL` pattern). Promote to a settings field only where per-machine tuning is plausible.
- **Call-site rewiring**: replace inline literals with `settings.timeouts.<field>` or the named constant.
- **Catalog audit + cleanup**: delete verified-dead fields, de-duplicate `data_dir`, fix/remove the stale `ServerSettings.port`, and regenerate/repair `.env.example` to document the real override surface.
- **Regression guard**: a validator under `.claude/hooks/validators/` that flags new inline `timeout=<int>` in `subprocess`/`requests` calls, with a test proving it fires.

### Flow

Call site with `timeout=10` → replace with `settings.timeouts.git_subprocess_s` (default 10) → same runtime behavior, now discoverable and `.env`-overridable → guard blocks reintroduction of a bare literal.

### Technical Approach

- **Promote-vs-name-locally criterion**: promote to a `settings` field if the value is duplicated across ≥2 modules OR is plausibly tuned per-machine (network/subprocess timeouts qualify). Name-locally (module constant) for logic-coupled one-offs (a dedup TTL sized to actor skew).
- **Timeout normalization decision (surfaced as Open Question)**: the ~150 git/subprocess sites use arbitrarily-drifted values (5/10/30). Collapsing each semantic category to a single canonical default is the point of the cleanup, but a call site previously at `timeout=5` that becomes `timeout=10` is a (benign) behavior change on the *failure/hang* path only — never the success path. The plan proposes normalizing per category to the **longest** current value in that category (a longer timeout only delays failure detection, never breaks a working call) and asks the human to confirm vs. preserving each value exactly via distinct fields.
- **Batching** (each independently reviewable; the build orchestrates as parallel tasks landing in one PR, or sequenced PRs if the diff is too large to review at once):
  1. `TimeoutSettings` scaffolding + the git/gh subprocess-timeout family (~150 sites).
  2. HTTP/SMTP/Redis/Anthropic client timeouts + inline sleeps/backoff.
  3. TTL/retry consolidation to named constants.
  4. Catalog audit, dead-field removal, `.env.example` sync, regression guard + test.
- **`VALOR_LAUNCHD` propagation**: any new `.env` key that a worker/bridge service reads must be added to the launchd plist injection path in `scripts/update/`, not only to `.env`.
- **Dead-field verification**: before deleting any zero-usage field, confirm nothing reads `settings` reflectively (grep for `getattr(settings`, `settings.dict(`, `model_dump`) — grep-absence is necessary but not sufficient.

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

- [ ] `tests/unit/test_settings.py` (or the existing config test module) — UPDATE: add cases for the new `TimeoutSettings` group defaults, bounds, and `.env` override via `TIMEOUTS__*`; assert removed dead fields no longer exist on `Settings`.
- [ ] `tests/**/test_*doctor*.py`, watchdog/branch/worktree manager tests, if any assert on hardcoded timeout values — UPDATE: reference the new settings field instead of the literal.
- [ ] New test file for the regression guard — REPLACE/CREATE: `tests/unit/test_validate_no_inline_timeout.py` proving the guard fires on a violating fixture and passes on a compliant one.
- [ ] `.env.example` completeness check (existing) — must continue to pass after adding the new `TIMEOUTS__*` keys with comments.

Existing behavioral tests should be unaffected because defaults preserve current values (pending the normalization decision — if normalization is approved, any test asserting an exact shorter timeout value is UPDATE'd).

## Rabbit Holes

- **Re-tuning while migrating.** This is a refactor, not a performance pass. Do not "improve" any timeout/TTL value beyond the agreed normalization. Changing values is a separate exercise.
- **Over-abstracting the config taxonomy.** Resist building a generic "duration registry" or reflection-driven config. Add plain typed fields matching the existing style.
- **Chasing every last literal.** A `time.sleep(0.1)` poll interval local to one function that no one would ever tune is fine as a named local constant. The goal is eliminating *duplicated/undiscoverable* knobs, not achieving zero integer literals.
- **Rewriting the retry constants that are already named.** `MAX_EMAIL_RELAY_RETRIES`, `SMTP_MAX_RETRIES`, etc. are already correct — leave them.

## Risks

### Risk 1: A missed call site keeps a hardcoded value while its siblings move
**Impact:** The "one source of truth" invariant is silently violated; the drift persists.
**Mitigation:** The regression-guard grep doubles as a completeness check — after each batch, `git grep -nE 'timeout\s*=\s*[0-9]'` over migrated dirs must return only `settings`/constant references. A Verification row asserts this.

### Risk 2: Timeout normalization changes behavior on a latency-sensitive path
**Impact:** A call that intentionally used a short timeout (fast-fail) now waits longer, delaying a failover.
**Mitigation:** Normalize to the longest value per category (delays failure detection only, never breaks success). Surface as an Open Question; if the human wants exact preservation, keep distinct fields per value. Reviewer specifically checks watchdog/health-probe sites where fast-fail matters.

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
- **No migration function** required — this is config/constants, not a Popoto schema change.

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
- [ ] The git/gh subprocess-timeout family (~150 sites) reads from `settings`; `git grep -nE 'timeout\s*=\s*[0-9]'` over `agent/ bridge/ worker/` migrated dirs shows only `settings`/constant references.
- [ ] The `ex=3600` and `ex=120` TTL duplicates collapse to one named constant each, reused at every site.
- [ ] Every zero-usage field is deleted (after reflective-access verification) or documented; `data_dir` is defined once; `ServerSettings.port` matches reality or is removed.
- [ ] `.env.example` documents every new override key with a comment; the completeness check passes.
- [ ] A regression-guard validator flags new inline subprocess `timeout=<int>`; a test proves it fires on a violating fixture and passes on a compliant one.
- [ ] `python -c "from config.settings import settings"` imports clean; config tests pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead orchestrates; builders fan out by subsystem so the large mechanical diff parallelizes without call-site collisions. Assign explicit non-overlapping file scopes per builder.

### Team Members

- **Builder (settings-scaffold)**
  - Name: settings-builder
  - Role: Add `TimeoutSettings` (+ any TTL/retry fields) to `config/settings.py`; update `.env.example`; own the catalog audit + dead-field removal.
  - Agent Type: builder
  - Resume: true

- **Builder (git-subprocess-sweep)**
  - Name: subprocess-builder
  - Role: Rewire the git/gh/subprocess timeout family in `agent/branch_manager.py`, `agent/worktree_manager.py`, `agent/session_logs.py`, `agent/completion.py`, `agent/session_revival.py`, `monitoring/*_watchdog.py`, `monitoring/crash_tracker.py`, `tools/doctor.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (http-ttl-sweep)**
  - Name: http-ttl-builder
  - Role: Rewire HTTP/SMTP/Redis/Anthropic client timeouts, inline sleeps, and the `ex=3600`/`ex=120` TTL consolidation in `bridge/`, `reflections/`, `agent/session_completion.py`, `agent/session_health.py`, `agent/messenger.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (regression-guard)**
  - Name: guard-builder
  - Role: Write `.claude/hooks/validators/validate_no_inline_timeout.py` + its test.
  - Agent Type: builder
  - Resume: true

- **Validator (no-behavior-change)**
  - Name: migration-validator
  - Role: Verify every migrated default equals the original literal; grep-completeness per batch; confirm no reflective-access field was deleted.
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
- Add `TimeoutSettings` group (`git_subprocess_s`, `gh_cli_s`, `subprocess_default_s`, `http_request_s`, `smtp_s`, `redis_socket_s`, `anthropic_client_s`) with defaults equal to the current per-category canonical value, `ge`/`le` bounds, and env-var descriptions.
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
- Preserve behavior: map each literal to the category default (or, if normalization declined, the exact-value field).

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
- Verify reflective-access absence, then delete the 9 zero-usage fields; de-duplicate `data_dir`; fix/remove `ServerSettings.port`.
- Regenerate/repair `.env.example`: add the new `TIMEOUTS__*` keys with comments; remove stale keys.
- Add launchd plist propagation in `scripts/update/` for any service-read key.

### 6. Migration validation
- **Task ID**: validate-migration
- **Depends On**: build-subprocess-sweep, build-http-ttl-sweep, build-guard, build-audit
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm each migrated default equals the original literal (diff-audit).
- Run the per-batch grep-completeness checks; confirm no reflective-access field was deleted.

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

| Check | Command | Expected |
|-------|---------|----------|
| Settings import clean | `python -c "from config.settings import settings; settings.timeouts"` | exit code 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No inline git/subprocess timeout literals remain (migrated dirs) | `git grep -nE 'timeout\s*=\s*[0-9]' -- agent/branch_manager.py agent/worktree_manager.py monitoring/ tools/doctor.py \| grep -v settings\.` | match count == 0 |
| Dead field removed (secret_key) | `git grep -n 'secret_key' -- config/settings.py` | exit code 1 |
| data_dir defined once | `git grep -cn 'data_dir: Path' -- config/settings.py` | output contains 1 |
| Guard test present and passing | `pytest tests/unit/test_validate_no_inline_timeout.py -q` | exit code 0 |
| .env.example has TIMEOUTS keys | `grep -c 'TIMEOUTS__' .env.example` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Timeout normalization vs. exact preservation.** The ~150 git/subprocess sites use drifted values (5/10/30). Proposal: normalize each semantic category to a single field defaulting to the **longest** current value in that category (safe — a longer timeout only delays failure detection, never breaks a working call). Acceptable, or must each distinct value be preserved exactly via separate fields (`git_quick_s=5`, `git_default_s=10`, `git_slow_s=30`)?
2. **Config taxonomy.** One flat `TimeoutSettings` group for all timeout categories, or fold subprocess timeouts into `PerformanceSettings` and add only the genuinely-new categories? I lean toward a dedicated `TimeoutSettings` group for discoverability.
3. **TTL/retry promotion.** Should the `ex=3600`/`ex=120` lock TTLs become `.env`-overridable settings fields, or stay named module-level constants (my default, since they're logic-coupled and sized to internal timing, not per-machine tuning)?
4. **One PR or sequenced PRs.** ~200 sites is a large diff. Land as one reviewable PR, or sequence the four batches as separate PRs (scaffold → subprocess → http/ttl → audit+guard)?
