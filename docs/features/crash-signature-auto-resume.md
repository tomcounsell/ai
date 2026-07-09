# Crash-Signature Auto-Resume

Periodic reflection that builds a crash pattern library from session telemetry and gates automatic session resumption behind statistical confidence (#1539).

## Problem Solved

Before this feature, every crashed session required a human operator to notice, diagnose, and manually resume it. Common patterns repeated across sessions with no accumulation of knowledge. The operator burden scaled linearly with crash frequency, and low-severity recurring crashes (idle-gap + SIGTERM, for instance) occupied the same operator attention as novel, truly broken patterns.

This reflection closes that loop: it watches terminal sessions, extracts normalized crash signatures, stores them in a library, and — once a pattern has been seen enough times with a sufficient recovery success rate — either proposes auto-resume or performs it automatically.

## Pillar Boundary: Running vs. Terminal Sessions

This feature (Pillar 2, #1539) operates on **terminal** sessions only — completed, failed, killed, abandoned. It extracts crash signatures from the closed telemetry trace and gates automatic resumption behind statistical confidence.

Live advisory classification for **running** sessions is handled by the [Stall Advisory Classifier](stall-advisory-classifier.md) (Pillar 1, #1538). The moment a session transitions to a terminal status it leaves Pillar 1's scope and enters Pillar 2's.

## Ownership Boundary with #1537

Issue #1537 (`session_health.py`) drives crashed sessions to terminal state. Issue #1539 (this feature) begins after that transition.

| Concern | Owner | Action |
|---|---|---|
| Detect no-progress `running` session | #1537 (`session_health.py`) | liveness loop |
| Drive crashed session to terminal state | #1537 | `_apply_recovery_transition` |
| Read terminal telemetry and extract crash signature | #1539 (this) | `reflections/crash_recovery.py` |
| Decide a terminal session is resumable and resume it | #1539 | auto-resume policy |

## Solution Architecture

```
AgentSession (terminal)
    |
    v
reflections/crash_recovery.py   <-- periodic reflection
    |
    +-- Phase 1: Attribute outcomes for already-resumed sessions
    |       (idempotency via crash_outcome_attributed flag)
    |
    +-- Phase 2: Extract signatures for freshly-terminal sessions
            |
            v
        agent/crash_signature.py::extract_signature()
            |
            v
        models/crash_signature.py::CrashSignature (Popoto, Redis)
            |
            v
        Policy check: is_auto_eligible()?
            |
            +-- propose mode (default): log only
            +-- auto mode (FEATURES__CRASH_AUTORESUME_ENABLED=1): call resume_session()
```

## Signature Extraction

The extractor (`agent/crash_signature.py`) reduces a session's telemetry event trace to a stable, normalized key. It examines the last `TERMINAL_SUBSEQUENCE_LENGTH` (default 10) events.

### Normalization Rules

Kept in the signature:
- Event type (e.g. `idle_gap`, `status_transition`, `turn_start`, `tool_use`)
- For `status_transition`: the `to` status, `kill.confirmed_dead`, `kill.signal_sent`
- Presence of an idle gap, bucketed coarsely (see below)

Dropped from the signature:
- PIDs
- Timestamps
- Exact durations
- Token counts

### Idle Gap Buckets

Idle gaps are normalized to three bucket labels:

| Duration | Bucket |
|---|---|
| less than 5 minutes | `short` |
| 5 to 30 minutes | `medium` |
| more than 30 minutes | `long` |

### Human Form Examples

```
idle_gap[medium]+status_transition[to=failed,dead=false,sig=SIGTERM]
status_transition[to=killed,dead=true,sig=SIGKILL]
ceiling+turn_start+status_transition[to=failed]
truncated+idle_gap[short]+status_transition[to=abandoned]
```

### CrashSignatureKey

`extract_signature()` returns a `CrashSignatureKey` dataclass:

| Field | Type | Description |
|---|---|---|
| `human_form` | str | Short human-readable crash pattern description |
| `hash` | str | sha256[:16] of `human_form` — stable key across runs |
| `signature_class` | str | Broad category; `NON_RESUMABLE_DETERMINISTIC` for never-started patterns |
| `resumable` | bool | False when `NON_RESUMABLE_DETERMINISTIC`, True otherwise |
| `escalated` | bool | Mutable flag set by the reflection when an alert is sent |

## Determinism Guardrail

Sessions that could never recover are detected before any resume attempt:

1. `session.startup_failure_kind == "plateau"`: the startup loop stalled without progress. Classified as `NON_RESUMABLE_DETERMINISTIC`. Never resumed.
2. No `turn_start` event in the full trace **and** no demonstrable progress on the session's own fields: the session never started a turn. Classified as `NON_RESUMABLE_DETERMINISTIC`. Never resumed.
3. `session.startup_failure_kind == "ceiling"` with a `turn_start` present: the session reached the startup timeout but did start. Classified as resumable; the `"ceiling"` prefix is added to the human form so it stays distinct in the library.

### Progress-fields ground truth

A `turn_start` telemetry event is the primary signal that a session started a turn, but that write can lag or be lost when the subprocess is killed mid-turn. So the extractor consults the AgentSession's own progress fields as ground truth before stamping the deterministic never-started key. `_has_demonstrable_progress(session)` (ported from #1724's stall-classifier probe) returns `True` when either holds:

- `turn_count > 0` (the session completed at least one turn), or
- `last_tool_use_at is not None` (a tool fired at some point).

When the trace lacks `turn_start` but the session's fields prove progress, classification falls through to the normal resumable-signature path instead of `NON_RESUMABLE_DETERMINISTIC[no_turn_start]`. This covers the mid-first-turn wedge (`turn_count == 0` with a recorded `last_tool_use_at`). Presence alone counts as progress: no wall-clock freshness window is applied, because the extractor runs over already-terminal sessions inside the lookback reflection where "now" is minutes-to-hours after death. The probe is fail-soft (any field-access error counts as no-progress) and reads the fields via `getattr` without importing the stall or kill machinery, keeping the extractor dependency-light. A session with no `turn_start` and no progress fields keeps the deterministic never-started classification.

`NON_RESUMABLE_DETERMINISTIC` sessions are escalated (a warning is logged with `[ESCALATE]` prefix) and the `escalated` flag on the library record is set to `True`. They are never proposed for resume.

## CrashSignature Popoto Model

`models/crash_signature.py` stores one aggregation record per unique crash pattern.

Primary key: `signature_hash` (sha256[:16] of `human_form`).

| Field | Type | Notes |
|---|---|---|
| `signature_hash` | KeyField | Primary key |
| `human_form` | Field | Human-readable pattern description |
| `signature_class` | Field | Broad class (e.g. `idle_gap\|kill_sigterm\|terminal_failed`) |
| `resumable` | Field | Stored as `"True"`/`"False"` string by Popoto; use `is_resumable` property |
| `escalated` | Field | `True` after an escalation alert; use `is_escalated` property |
| `occurrence_count` | Field | Total observations; use `occurrence_count_int` property |
| `project_key` | IndexedField | Project partition key for filtered queries |
| `outcome_tallies_json` | Field | JSON: `{"strategy": {"attempts": N, "recovered": N, "failed": N}}` |

Records are upserted on every terminal session scan: `get_or_create_by_hash()` followed by `upsert_occurrence()`.

## Policy Thresholds (Demotion-Gate Model)

Auto-eligibility follows a **demotion-gate** model, not a promotion gate. The success ratio only *demotes* a signature once it has earned real attempt data. A signature with zero recorded attempts is "not yet demoted" and remains eligible (provided the structural gates pass). A promotion gate would deadlock the cold-start case: zero attempts produces a 0.0 ratio, which would never clear a minimum ratio, so the signature would never be resumed and never accrue attempts.

`is_auto_eligible` evaluates, in order:

1. `NON_RESUMABLE_DETERMINISTIC` -> never eligible (determinism guardrail wins unconditionally).
2. Not `is_resumable` -> never eligible.
3. `occurrence_count < MIN_OCCURRENCES` (default 3, settings: `crash_autoresume_min_occurrences`) -> not eligible.
4. Zero recorded attempts for the strategy -> **eligible** (bootstrap; not yet demoted).
5. Attempts > 0 -> eligible iff `policy_confidence("auto_resume") >= MIN_SUCCESS_RATIO` (default 0.7, settings: `crash_autoresume_min_success_ratio`). A signature that starts failing auto-demotes itself out of eligibility.

`policy_confidence` is `recovered / attempts` from the `outcome_tallies_json` for the `"auto_resume"` strategy. Returns 0.0 when no attempts exist.

The library is cold at first ship. The INFO log line "crash-signature library is cold" will appear on each run until the first signatures are recorded. Observable via `valor-session crash-signatures --project <key>`.

## Scheduling / Registration

The reflection is registered by the update flow, so every machine schedules it after a `/update` run. `config/reflections.yaml` is a gitignored per-machine copy of the iCloud-synced vault registry (`~/Desktop/Valor/reflections.yaml`), refreshed from the vault by `env_sync.sync_reflections_yaml()` on every update. Because the config copy is overwritten from the vault each cycle, registration has to land in the vault file to persist.

`scripts/update/reflection_register.py::register_crash_recovery()` performs that registration as an idempotent update step:

- It resolves the target registry via `agent.reflection_scheduler._resolve_registry_path()`, the same vault-first resolver the scheduler reads at run time, so the entry lands where the scheduler will actually look rather than in the soon-overwritten config copy.
- It appends the `crash-recovery` entry when absent and no-ops when present. The append is line-scoped and atomic (temp file plus `os.replace`), validated by re-parsing before it replaces the original, so the hand-authored registry keeps its comments and a sync race never leaves a truncated file.
- It is guarded on the vault existing and this machine owning the `valor` project (`projects.json`), mirroring `reflection_arm.py`. The owning machine writes the vault; every other machine receives the entry through the vault's iCloud sync and the update copy step.
- It runs before the vault-to-config copy step so the appended entry propagates into the per-machine `config/reflections.yaml` on the same update cycle.

The registered entry:

```yaml
  - name: crash-recovery
    description: "Fingerprint crashes, warm signatures, auto-resume tool-wedge deaths (#1917)"
    every: 300s # 5 minutes
    priority: normal
    execution_type: function
    callable: "reflections.crash_recovery.run_crash_recovery"
    enabled: true
```

The entry is unscoped, so every machine runs the reflection in **propose-only mode**. Auto-apply (actually resuming sessions) additionally requires `FEATURES__CRASH_AUTORESUME_ENABLED=1` in the environment and this machine owning the session's project (see [Machine-ownership gate](#machine-ownership-gate)). Verify registration with `python -m reflections --dry-run`, which lists `crash-recovery` once the vault registry has the entry.

## Propose vs Auto-Apply Modes

By default the reflection runs in **propose mode**: it logs which sessions it would resume but does not act.

Set `FEATURES__CRASH_AUTORESUME_ENABLED=1` to enable **auto mode**. The enable flag and all four thresholds are read from the pydantic settings object (`config.settings.settings.features`) at run time inside `run_crash_recovery()`, so the documented `FEATURES__` env prefix is the single source of truth. Auto mode should only be activated on one designated machine. Running it on multiple machines concurrently risks double-resume.

In auto mode the reflection calls `resume_session(session, "continue", source="auto-resume")` and tags the session with `crash_signature = sig.hash` and `auto_resume_attempts = N` for outcome attribution on the next run.

## Safety Gates

Two caps prevent runaway auto-resume:

**Per-session cap:** A session that has been auto-resumed `crash_autoresume_max_attempts` times (default 3, env: `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS`) is left in its terminal state for human review. The attempt count is stored on `AgentSession.auto_resume_attempts`.

**Per-run budget:** Each reflection run will auto-resume at most `crash_autoresume_run_budget` sessions (default 5, env: `FEATURES__CRASH_AUTORESUME_RUN_BUDGET`). This limits blast radius from a single noisy reflection tick.

A failed resume still consumes an attempt: when `resume_session` returns unsuccessfully (for example a session with no stored transcript UUID, which refuses), the reflection advances `auto_resume_attempts` on the failure path just as it does on success. A persistently-failing resume therefore converges to the per-session cap instead of retrying every tick forever.

## Deterministic First-Retry Floor

Statistical eligibility needs a warm library (`MIN_OCCURRENCES` observations). A cold library cannot act on the first several tool-wedge deaths, which is exactly the failure mode this feature targets. The deterministic floor grants one bounded first retry to the known-transient shape ahead of statistical warm-up.

The transient shape is a **confirmed-dead clean kill to `failed`**: the worker detected a wedged tool call, killed the subprocess with a confirmed death, and finalized the session `failed`. `reflections/crash_recovery.py::_is_transient_clean_kill_to_failed(events)` derives this inline from the last `status_transition` in the timeline (`to == "failed"` and `kill.confirmed_dead == true`). It is derived per-run rather than stored, so the shared `CrashSignatureKey` dataclass stays unchanged. The check is fail-soft: any malformed event yields no floor.

When the floor field is greater than 0, the session's `auto_resume_attempts` is below the floor, and the transient shape matches, the reflection permits the resume even when the signature is not yet statistically eligible. The floor stays bounded by the per-session cap (`crash_autoresume_max_attempts`) and the per-run budget (`crash_autoresume_run_budget`). Setting `crash_autoresume_deterministic_floor_attempts` to 0 disables the floor and restores pure statistical gating. Broadening the floor beyond this one shape is deliberately left for a data-driven follow-up once the library warms.

## Machine-ownership gate

Auto mode resumes a session only when this machine owns the session's project: `projects.<project_key>.machine == computer_name()` in `projects.json`. `reflections/crash_recovery.py::_machine_owns_project(project_key)` resolves ownership and falls soft to not-owned on an unknown or unresolvable project key, so a non-owner proposes rather than resumes.

This makes the single-machine invariant structural. Even with `FEATURES__CRASH_AUTORESUME_ENABLED=1` set fleet-wide, exactly one machine acts on a given session, which removes the reliance on an operator setting the enable flag on exactly one box and scales to any number of machines. Combined with the pre-resume status re-read, it closes the double-resume race.

## Resumable Statuses

The reflection scans sessions in `RESUMABLE_STATUSES`:

```python
RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})
```

`cancelled` is excluded. A cancelled session represents an intentional human stop and must never be auto-resumed.

## Outcome Attribution Loop

After a resumed session reaches a terminal state again, the reflection attributes the outcome back to the library on its next run:

1. Phase 1 finds sessions with `crash_signature` set but `crash_outcome_attributed` not yet set.
2. Outcome is `"recovered"` if the session `completed`; `"crashed_again"` otherwise.
3. Write ordering (flag-first to avoid double-count): set `crash_outcome_attributed = True` on the session first, then call `sig_record.record_outcome("auto_resume", recovered=...)`.

This gives safe under-count rather than dangerous over-count in the event of a crash between the two writes.

## Per-Run Observability

Every reflection run emits a single INFO summary line:

```
crash-recovery run complete: processed=N, signatures_extracted=N, proposed=N, auto_resumed=N, escalated=N, re_crashed=N
```

| Counter | Meaning |
|---|---|
| `processed` | Sessions touched this run (attribution + extraction) |
| `signatures_extracted` | New signatures upserted into the library |
| `proposed` | Sessions that would have been resumed in auto mode |
| `auto_resumed` | Sessions actually resumed (auto mode only) |
| `escalated` | `NON_RESUMABLE_DETERMINISTIC` patterns detected and flagged |
| `re_crashed` | Resumed sessions that crashed again (outcome attributed as failed) |

This line also appears in the `findings` list returned by `run_crash_recovery()` for the reflections dashboard.

## Race Condition Handling

The reflection re-reads each session's status immediately before calling `resume_session()` to avoid racing with #1537's recovery mechanisms. If the status changed since the initial scan, the session is skipped silently.

For incomplete telemetry (no terminal `status_transition` event yet, or `unclassifiable` signature), the session is skipped and retried on the next reflection tick. The reflection never claims `finalize_session` ordering as mitigation: that function does not touch DB status.

## Configuration Reference

The enable flag and four thresholds are pydantic settings fields on `settings.features`, read at run time. Their env prefix is `FEATURES__`:

| Env var | Settings field | Default | Description |
|---|---|---|---|
| `FEATURES__CRASH_AUTORESUME_ENABLED` | `crash_autoresume_enabled` | `0` | Set to `1` to enable auto mode on this machine |
| `FEATURES__CRASH_AUTORESUME_MIN_OCCURRENCES` | `crash_autoresume_min_occurrences` | `3` | Min observations before a signature is auto-eligible |
| `FEATURES__CRASH_AUTORESUME_MIN_SUCCESS_RATIO` | `crash_autoresume_min_success_ratio` | `0.7` | Min success ratio (recovered / attempts) once attempts exist (demotion threshold) |
| `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS` | `crash_autoresume_max_attempts` | `3` | Per-session auto-resume attempt cap |
| `FEATURES__CRASH_AUTORESUME_RUN_BUDGET` | `crash_autoresume_run_budget` | `5` | Max auto-resumes per reflection run |
| `FEATURES__CRASH_AUTORESUME_DETERMINISTIC_FLOOR_ATTEMPTS` | `crash_autoresume_deterministic_floor_attempts` | `1` | First-retry floor for the confirmed-dead clean-kill-to-`failed` shape ahead of statistical warm-up (0 disables) |

The lookback window has no settings field and is read from a bare env var at run time:

| Env var | Default | Description |
|---|---|---|
| `CRASH_AUTORESUME_LOOKBACK_HOURS` | `2.0` | How far back to scan for recently-terminal sessions |

## CLI Reference

```bash
# Show all crash signatures in the library (project-scoped)
valor-session crash-signatures

# Show signatures with at least 5 occurrences
valor-session crash-signatures --min-occurrences 5

# Show signatures as JSON
valor-session crash-signatures --json

# Show derived auto-resume policy entries (which signatures are auto-eligible)
valor-session crash-policy list

# Show policy with custom thresholds
valor-session crash-policy list --min-occurrences 2 --min-success-ratio 0.6

# Show policy as JSON
valor-session crash-policy list --json
```

## Source Files

| File | Role |
|---|---|
| `agent/crash_signature.py` | Normalization and extraction logic |
| `models/crash_signature.py` | Popoto model and outcome tally management |
| `reflections/crash_recovery.py` | Periodic reflection: scan, extract, propose, auto-resume; deterministic floor and machine-ownership gate |
| `scripts/update/reflection_register.py` | Idempotent update step that registers the reflection in the vault registry |
| `models/session_lifecycle.py` | `RESUMABLE_STATUSES` definition |
| `tools/valor_session.py` | `crash-signatures` and `crash-policy` CLI commands |
