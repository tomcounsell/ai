---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-17
tracking: https://github.com/tomcounsell/ai/issues/1538
last_comment_id: 4715101146
revision_applied: false
---

# Stalled-Session Advisory Classifier (Pillar 1 of epic #1536)

## Problem

A human watching a Claude Code TUI reads "stuck vs still working" at a glance — tokens ticking, tools scrolling, turns landing. Our headless worker now *records* that stream (v1 telemetry recorder, PR #1699) and Pillar 2 (PR #1718) already reduces **terminal** traces to crash signatures for auto-resume. But nothing reads the *live* stream to answer the human's glance-question for a session that is **still running**: "does this one look healthy, or has it quietly wedged?"

The closest existing signal is the two-tier liveness check (`agent/session_health.py`), but that is deliberately conservative: it only acts when evidence of progress is *absent* past a 30-minute budget, and it exists to decide kills, not to inform a watching human. There is a wide band — a granite session burning toward its 600s startup ceiling with zero `turn_start`, a session that has gone idle for several minutes mid-task, a session racking up tool-timeout recoveries — where the liveness check has not yet (and may never) act, but a human reading the dashboard would already say "that one looks stalled, go look."

**Current behavior:**
- The recorded telemetry stream (`logs/session_telemetry/{session_id}.jsonl`) is consumed only by the read-back CLI (`valor-session telemetry`) and by Pillar 2's *terminal* crash-signature extractor. No process derives a live healthy-vs-stalled read for a running session.
- The dashboard surfaces raw liveness fields (`last_tool_use_at`, `recovery_attempts`, `reprieve_count`, `process_alive`) but no synthesized "looks stalled" verdict; an operator must interpret the raw fields themselves.
- A granite session that never emits a first event (hangs at startup) is structurally invisible to any *stream* classifier while it is still running — the only signal is the eventual terminal `startup_unresolved` status, which Pillar 2 catches only after the session is terminal.

**Desired outcome:**
A pure, advisory classifier that, given a running session's recent telemetry window plus its per-project health counters, returns a `healthy | suspect | stalled` verdict with a short human-readable reason — surfaced two ways: (1) a per-session indicator on the dashboard, and (2) a periodic reflection that annotates suspect/stalled running sessions (error-only Telegram surfacing). The verdict is **advisory only**: it never feeds the kill path, never resurrects stdout-silence as a kill signal (#1172), and never duplicates Pillar 2's terminal/auto-resume logic.

## Freshness Check

**Baseline commit:** `514b2cf5`
**Issue filed at:** 2026-06-01T08:16:07Z (scope-amendment comment 2026-06-16T04:52:45Z, comment id 4715101146)
**Disposition:** Overlap (resolved by scoping)

**File:line references re-verified (against `514b2cf5`):**
- `agent/session_telemetry.py:256` — `read_session_timeline(session_id, limit=None) -> list[dict]` — still holds (the reader this pillar consumes).
- `agent/session_telemetry.py:171` — `record_telemetry_event` — still holds; event vocabulary `turn_start/turn_end/tool_use/token_usage/idle_gap/status_transition/telemetry_truncated/unknown` confirmed.
- `agent/session_health.py:678` `_has_progress` (Tier 1), `:884` `_tier2_reprieve_signal`, `:1132` `_apply_recovery_transition`, `:1038` `_confirm_subprocess_dead` — all present. This is the kill path; the classifier never touches it.
- Per-project counters confirmed at `agent/session_health.py`: `recoveries:{reason_kind}` `:1173`, `tier1_flagged_total` `:1250`, `tier2_reprieve_total:{gate}` `:1259`, `kill_total` `:1357`, `tool_timeouts:{tier}` `:2275`.
- `ui/data/sdlc.py:957` `get_all_sessions()`, `:746` `_session_to_pipeline()`, `PipelineProgress` Pydantic `:234-356` — confirmed; advisory field attaches here.
- `agent/reflection_scheduler.py:270` `_resolve_callable`, `:391-394` sync/async function-callable invocation — confirmed; callable returns a `{status, findings, summary}` dict (pattern from `reflections/crash_recovery.py:65`).
- `agent/crash_signature.py` `extract_signature` / `CrashSignatureKey` — confirmed shipped (Pillar 2); `_has_turn_start` never-started detection and `startup_failure_kind` ("plateau"/"ceiling") consumption present.
- `agent/granite_container/bridge_adapter.py:532` sets `session.startup_failure_kind`; `tools/granite_loop/cli.py:172` finalizes `startup_unresolved` via `finalize_session(... "failed", reason="startup_unresolved")`.

**Cited sibling issues/PRs re-checked:**
- #1536 (epic) — OPEN.
- #1539 (Pillar 2) — **MERGED 2026-06-17 (PR #1718, `7c139247`)**. Ships `agent/crash_signature.py`, `models/crash_signature.py`, `reflections/crash_recovery.py`. Directly overlaps the trace-reduction surface — scope narrowed accordingly (see Spike Results / No-Gos).
- #1487 — MERGED 2026-06-01 as granite PTY PoC; the `decode_error`/`broken_pipe`/`timeout` synthetic events **never landed** (zero hits in `agent/`). Do not depend on them.
- #1226/#1356/#1172/#1537 — all closed; the liveness/silence-not-kill constraints they established still apply.
- #1313 (`docs/plans/stalled-session-user-visible-alert.md`, Critique-Resolved) — adjacent watchdog `pending`-stall Telegram alert; coordinate the user-visible surface to avoid double-alerting.

**Commits on main since issue filed (touching consumer files):** `415e0e10` (v1 recorder — the substrate), `7c139247` (Pillar 2 — the overlap), `d8fe0452`/`3b4bbe61` (dashboard granite telemetry parity), `e702cf9c` (zombie-recovery heartbeat gate). All read; none invalidate the live-advisory premise — they tighten the boundary against Pillar 2 and confirm the dashboard attach point.

**Active plans in `docs/plans/` overlapping this area:** `stalled-session-user-visible-alert.md` (#1313) — adjacent surface, not the same mechanism. No active plan builds a live telemetry classifier.

**Notes:** The scope-amendment comment asked Pillar 1 to flag never-started hangs. Pillar 2 already does this **for terminal sessions** (`no_turn_start` → `NON_RESUMABLE_DETERMINISTIC`). The genuinely new, non-overlapping contribution this pillar adds is the **live** never-started detection: a *running* granite session with zero `turn_start` that is burning toward its ceiling, flagged advisory *before* it goes terminal.

## Prior Art

- **#1536 v1 recorder (PR #1699, `415e0e10`)** — built the durable per-event JSONL trace + `read_session_timeline` reader + `valor-session telemetry` CLI. This pillar's sole input substrate. Succeeded; in production.
- **#1539 Pillar 2 (PR #1718, `7c139247`)** — built `agent/crash_signature.py` (terminal-trace → normalized signature), `models/crash_signature.py` (occurrence/outcome library), `reflections/crash_recovery.py` (periodic terminal-trace reflection + gated auto-resume). Succeeded; in production. **This pillar reuses its signature vocabulary and never re-derives terminal signatures.**
- **#1226/#1356 two-tier liveness (closed)** — evidence-based Tier 1 + reprieve-gated Tier 2 kill logic. The system this pillar augments (never replaces).
- **#1172 (closed)** — retired stdout-silence-as-kill. **Constraint:** the classifier records and reads idle gaps as facts; an idle/silent window may raise the *advisory* verdict but is never a kill input.
- **#1313 `stalled-session-user-visible-alert.md` (Critique-Resolved)** — Telegram alert when `monitoring/session_watchdog.py` detects a `pending`-stall. Different trigger (watchdog state, not learned telemetry verdict); coordinate so the two surfaces don't double-notify.

## Research

No relevant external findings — proceeding with codebase context. The classifier consumes internal JSONL traces and internal Redis counters; it introduces no external library, API, or ecosystem dependency. (Phase 0.7 skipped per the "purely internal" rule.)

## Spike Results

### spike-1 (code-read, resolved during recon): What is the non-overlapping boundary against shipped Pillar 2?
- **Assumption**: "Pillar 1 (advisory classifier) and Pillar 2 (crash-signature auto-resume) are distinct enough that Pillar 1 has genuine independent value after Pillar 2 shipped."
- **Method**: code-read of `agent/crash_signature.py`, `models/crash_signature.py`, `reflections/crash_recovery.py`, `agent/session_health.py`, `ui/data/sdlc.py` at `514b2cf5`.
- **Finding**: **Confirmed, with a sharpened boundary.** Pillar 2 operates on **terminal** sessions: `extract_signature` is documented "reduce a *terminal* session's telemetry trace," and `reflections/crash_recovery.py` scans recently-terminal sessions. It owns never-started *terminal* detection (`_has_turn_start` → `NON_RESUMABLE_DETERMINISTIC`) and auto-resume policy. Pillar 1's non-overlapping surface is the **live (non-terminal) advisory read**: classify a *running* session's recent window and surface a verdict for a human, with no resume/kill action. The two share only the trace stream and the signature *vocabulary* — which Pillar 1 imports rather than re-implements.
- **Confidence**: high (file:line evidence at current main).
- **Impact on plan**: (1) The classifier lives in a new module `agent/session_stall_classifier.py` and operates on **running** sessions only; terminal sessions are Pillar 2's domain and are skipped. (2) It imports/reuses `agent.crash_signature` helpers (`_bucket_idle_gap`, the idle/kill normalization vocabulary) where they apply, rather than duplicating them — but it does NOT call `extract_signature` (that produces a *terminal* resume key, the wrong abstraction for a live read). (3) The "live never-started" detection (running granite session, zero `turn_start`, elapsed-time pressure toward the 600s ceiling) is the one new piece of detection logic this pillar adds. (4) No new event types are recorded; the classifier is a pure *reader*.

### spike-2 (code-read, resolved during recon): Can the advisory verdict be computed without a kill-path coupling?
- **Assumption**: "A `healthy | suspect | stalled` verdict can be derived purely from `read_session_timeline` + per-project Redis counters + a few AgentSession fields, with zero call into `agent/session_health.py`'s kill logic."
- **Method**: code-read of the recorder reader, the counter writes, and `PipelineProgress` field set.
- **Finding**: **Confirmed.** `read_session_timeline(session_id)` returns the full ordered event list (idle_gap, status_transition with kill, tool_use, token_usage, turn_*). Per-project counters are plain integer reads. AgentSession exposes `startup_failure_kind`, turn/heartbeat timestamps, and `recovery_attempts`/`reprieve_count` (already surfaced on `PipelineProgress`). All inputs are read-only; nothing requires invoking `_has_progress`, `_apply_recovery_transition`, or any transition function.
- **Confidence**: high.
- **Impact on plan**: The classifier is a pure function `classify_session_stall(events, *, session, project_counters=None) -> StallVerdict`. It is import-safe from both the dashboard (`ui/data/sdlc.py`, sync, per-request) and a reflection (`reflections/`, sync). No async, no kill-path import, no Redis writes.

## Data Flow

1. **Entry point A — dashboard (live, per-request):** `ui/data/sdlc.py::_session_to_pipeline(session)` (`:746`) runs for each session being rendered. **New:** for a *non-terminal* session, call `classify_session_stall(read_session_timeline(session.session_id), session=session)` and attach the resulting verdict + reason to a new `PipelineProgress` advisory field. Terminal sessions skip the call (verdict stays `None`).
2. **Entry point B — reflection (periodic):** a new `reflections/stall_advisory.py::run_stall_advisory()` callable, registered in `config/reflections.yaml` (`execution_type: function`, every ~5 min, project-scoped or global). It queries running sessions, classifies each, and collects `suspect`/`stalled` verdicts into `findings`. Returns the `{status, findings, summary}` dict the scheduler expects. Error/anomaly-only Telegram surfacing (no all-clear spam, #1292).
3. **Classifier core (`agent/session_stall_classifier.py`, new, pure):** `classify_session_stall(events, *, session, project_counters=None)` → `StallVerdict(level: "healthy"|"suspect"|"stalled", reason: str, signals: dict)`. It (a) handles the **live never-started** case (running session, no `turn_start`, elapsed pressure), (b) reads the recent event window for idle-gap / tool-timeout / kill-bearing status_transition density, (c) optionally folds in per-project counter pressure (`tool_timeouts`, `recoveries`) as a weak corroborating signal, and (d) returns a verdict. Fail-soft: any exception yields `StallVerdict("healthy", reason="unclassifiable", ...)` so the advisory never blocks rendering or a reflection.
4. **Counters reader (`agent/session_stall_classifier.py` helper, optional):** read-only `POPOTO_REDIS_DB.get` of the `{project_key}:session-health:{metric}` integers (never `.incr`/`.delete`). These are *corroborating* signals only.
5. **Output A:** dashboard renders the advisory badge (e.g. a colored dot + reason tooltip) on the session row/modal — purely informational.
6. **Output B:** the reflection logs the suspect/stalled list and, only when something looks wrong, sends one concise Telegram note via the reflection's send path. Never kills, never resumes.

## Architectural Impact

- **New dependencies**: none (stdlib + existing `read_session_timeline` + existing `POPOTO_REDIS_DB` read).
- **Interface changes**: one new pure module `agent/session_stall_classifier.py` (`classify_session_stall`, `StallVerdict`); one new reflection module `reflections/stall_advisory.py`; one new nullable field on `PipelineProgress` in `ui/data/sdlc.py`; one new entry in `config/reflections.yaml`. No signature change to any existing function.
- **Coupling**: low and one-directional — the classifier *reads* the telemetry stream and counters; nothing in the kill/recovery path depends on it. It imports vocabulary helpers from `agent.crash_signature` (read-only reuse).
- **Data ownership**: no new persisted data. The verdict is computed on demand (dashboard) or transiently (reflection). Nothing written to Popoto/Redis/disk.
- **Reversibility**: high — delete the two new modules, the `PipelineProgress` field, and the reflections.yaml entry. No schema migration, no backfill.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus validator and documentarian (orchestrated).

**Interactions:**
- PM check-ins: 1-2 (verdict thresholds sign-off; dashboard-vs-reflection surface confirmation)
- Review rounds: 1

## Prerequisites

No prerequisites beyond the shipped v1 recorder (in production) — stdlib + existing read paths only.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| v1 recorder present | `python -c "from agent.session_telemetry import read_session_timeline"` | The input substrate |
| Pillar 2 signature vocab present | `python -c "from agent.crash_signature import _bucket_idle_gap"` | Reused normalization helpers |

Run all checks: `python scripts/check_prerequisites.py docs/plans/stalled_session_advisory_classifier.md`

## Solution

### Key Elements

- **`agent/session_stall_classifier.py` (new, pure)**: `classify_session_stall(events, *, session, project_counters=None) -> StallVerdict`. A fail-soft, side-effect-free function returning a 3-level advisory verdict (`healthy | suspect | stalled`) plus a short reason and a `signals` dict for debuggability. Reuses idle/kill normalization vocabulary from `agent.crash_signature` rather than re-deriving it. Includes the **live never-started** rule: a running session with zero `turn_start` and elapsed-time pressure toward its ceiling is `stalled` with reason `"never_started"`.
- **`reflections/stall_advisory.py` (new)**: `run_stall_advisory() -> dict` — queries running sessions, classifies each via the core, returns `{status, findings, summary}`. Error/anomaly-only Telegram surfacing. Read-only; never kills or resumes.
- **`config/reflections.yaml` entry**: `stall-advisory`, `execution_type: function`, `callable: reflections.stall_advisory.run_stall_advisory`, schedule ~`every: 300s`, `group: agents`, `enabled: true`.
- **Dashboard advisory field**: a new nullable `stall_advisory: str | None` (or a small struct) on `PipelineProgress`, populated inline in `_session_to_pipeline` for non-terminal sessions, rendered as an unobtrusive per-session badge with the reason in a tooltip.
- **Read-only counter helper**: optional `read_project_health_counters(project_key) -> dict[str,int]` doing `POPOTO_REDIS_DB.get` on the `session-health:*` keys — corroborating signals only, never a sole verdict driver.

### Flow

`worker runs a session` → `recorder writes JSONL trace (already shipped)` → **Dashboard:** `operator loads dashboard` → `_session_to_pipeline reads trace + classifies` → `advisory badge renders on the session row` → **operator glances and decides whether to investigate** ‖ **Reflection:** `scheduler fires stall-advisory every ~5 min` → `classify each running session` → `if any suspect/stalled, log + one Telegram note` → **human gets a nudge, never an automated kill**

### Technical Approach

- **Pure reader, never a writer or actor.** The classifier only reads `read_session_timeline`, AgentSession fields, and (optionally) counter integers. It imports nothing from the kill/recovery path. This structurally guarantees the hard constraint: the advisory can never be a kill trigger.
- **3-level verdict, not a binary.** `healthy` (recent turn/tool evidence, no concerning pattern), `suspect` (e.g. a single long idle gap mid-task, rising tool-timeout count, one recovery attempt), `stalled` (live never-started; or sustained idle past a generous window with no offsetting evidence; or repeated kill-bearing transitions). Thresholds are constants with placeholder defaults, documented and PM-tunable (do not hardcode magic numbers in prompts/descriptions per repo convention).
- **Reuse Pillar 2 vocabulary, not its abstraction.** Import `_bucket_idle_gap` and the kill/idle token shapes from `agent.crash_signature` so the advisory's reason strings line up with crash signatures operators already see. Do NOT call `extract_signature` — that yields a *terminal* resume key, the wrong abstraction for a live read.
- **Live never-started rule.** For a *running* session: if `read_session_timeline` has zero `turn_start` events AND the session has been running longer than a generous startup grace, return `stalled` with reason `"never_started"`. This is the new detection the scope amendment asked for, on the live side (Pillar 2 covers the terminal side).
- **Idle gaps are facts, not kill signals (#1172).** A long idle window can raise the *advisory* level but is explicitly documented as never feeding a kill decision; the docstring and feature doc state this.
- **Fail-soft everywhere.** Any exception in classification (malformed trace, missing field, Redis read error) yields `StallVerdict("healthy", reason="unclassifiable")` and logs at debug — the dashboard render and the reflection must never break because of the advisory.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `classify_session_stall` wraps its body in `try/except Exception` that logs at debug and returns `StallVerdict("healthy", reason="unclassifiable", ...)`. Test: feed a malformed event list (non-dict entries, missing `type`) and assert no raise, a debug log, and a `healthy/unclassifiable` verdict.
- [ ] `read_project_health_counters` swallows a Redis read error and returns `{}` (logged at debug); test by forcing the read to raise.
- [ ] `run_stall_advisory` never propagates an exception from any single session's classification — a bad session is skipped, the run continues; test with one session whose trace read raises.

### Empty/Invalid Input Handling
- [ ] `classify_session_stall([], session=<running>)` → if zero `turn_start` and past startup grace → `stalled/never_started`; otherwise `healthy` (no events yet, just started). Both branches tested.
- [ ] `classify_session_stall(events, session=None)` → no raise; falls back to event-only signals (no never-started elapsed check). Tested.
- [ ] A terminal session passed to the dashboard path is skipped (verdict `None`), not classified. Tested.

### Error State Rendering
- [ ] Dashboard: a session with a `stalled` advisory renders a visible badge + reason; a session with `None` advisory renders nothing (no empty badge). Both asserted (data-level test on `PipelineProgress`).
- [ ] Reflection: an all-`healthy` run sends NO Telegram message (no all-clear spam, #1292); a run with a `stalled` session produces exactly one concise note. Both asserted.

## Test Impact

- [ ] `tests/unit/` coverage of `ui/data/sdlc.py::_session_to_pipeline` — UPDATE: assert the new `stall_advisory` field is populated for a non-terminal session and `None` for a terminal one (mock `read_session_timeline`/`classify_session_stall`; don't re-test the classifier here).
- [ ] New `tests/unit/test_session_stall_classifier.py` — CREATE: full coverage of `classify_session_stall` — healthy/suspect/stalled verdicts, live never-started rule, idle-gap and kill-transition signals, fail-soft on malformed input, `session=None` fallback, counter corroboration.
- [ ] New `tests/unit/test_stall_advisory_reflection.py` — CREATE: `run_stall_advisory` return shape `{status, findings, summary}`, per-session exception isolation, error-only Telegram surfacing, skips terminal sessions.
- [ ] New `tests/integration/test_stall_advisory_e2e.py` — CREATE: write a synthetic running-session trace to `logs/session_telemetry/`, run the classifier + reflection, assert the verdict and that no kill/recovery function is invoked (assert `agent.session_health` transition functions are never called — guards the hard constraint).

No existing tests in `agent/session_health.py` or `agent/crash_signature.py` coverage are affected — this work is purely additive (new modules, one new nullable dashboard field, one new reflection entry) and changes no existing behavior or signature.

## Rabbit Holes

- **Re-deriving terminal crash signatures.** Pillar 2 owns terminal-trace reduction and auto-resume. Do NOT call `extract_signature` or touch `models/crash_signature.py` / `reflections/crash_recovery.py`. Reuse only the small normalization *vocabulary* helpers.
- **A trained ML model.** "Learn" here means a transparent, threshold-based heuristic over the recorded signals — not a fitted classifier. A real model is disproportionate to the appetite and unauditable as an advisory. The plan derives the verdict heuristically; the event stream is the substrate a future model *could* use, but that is out of scope.
- **Feeding the verdict into the kill path.** Structurally forbidden by the hard constraint. The classifier imports nothing from the recovery path; the e2e test asserts no transition function is called.
- **A bespoke dashboard widget redesign.** A single unobtrusive badge + tooltip on the existing session row satisfies "dashboard indicator." A new panel/visualization is front-end scope creep.
- **Double-alerting with #1313's watchdog alert.** Coordinate the user-visible surface; don't ship a second independent Telegram alerter for the same symptom.
- **Recording new event types.** This pillar is a pure reader. Adding synthetic events (e.g. a live `startup_unresolved` telemetry event) is recorder scope, not classifier scope — the live never-started case is derived from the *absence* of `turn_start`, no new event needed.

## Risks

### Risk 1: Advisory false positives erode trust ("it cried stalled, session was fine")
**Impact:** A noisy advisory trains operators to ignore it, defeating the purpose.
**Mitigation:** 3-level verdict with a conservative `stalled` threshold; `suspect` absorbs ambiguous cases without alarming. Reflection surfaces only `stalled` (or repeated `suspect`) to Telegram, and only on change (no per-tick spam). Thresholds are tunable constants with documented defaults; the e2e/unit tests pin the verdict boundaries so tuning is deliberate.

### Risk 2: Per-request dashboard classification adds latency
**Impact:** Calling `read_session_timeline` per session per dashboard load could slow rendering for many sessions.
**Mitigation:** `read_session_timeline` is a bounded local file read (per-session cap 10k events); the classifier inspects only the recent window (tail), not the whole trace. Classify only non-terminal sessions (a small set). If measurable, cap the read with the reader's `limit` arg and read only the tail. Benchmark in the e2e test.

### Risk 3: Boundary drift against Pillar 2 (the two reflections overlap or contradict)
**Impact:** `stall-advisory` (live) and `crash-recovery` (terminal) could both speak about the same session near its terminal moment, producing confusing or duplicate signals.
**Mitigation:** Strict terminal/non-terminal partition — `stall-advisory` classifies running sessions only; the moment a session goes terminal it leaves Pillar 1's scope and enters Pillar 2's. The e2e test asserts a terminal session is skipped by the classifier. The feature doc states the partition explicitly.

## Race Conditions

### Race 1: Trace read while the recorder is still appending
**Location:** `agent/session_stall_classifier.py` (via `read_session_timeline`) reading a JSONL trace that the executor task is concurrently appending to.
**Trigger:** Dashboard or reflection reads `{session_id}.jsonl` while the session is live and the recorder writes a new line.
**Data prerequisite:** A partially-written last line could be present.
**State prerequisite:** None beyond the file existing.
**Mitigation:** `read_session_timeline` already skips malformed/partial lines with a logged warning (verified at `agent/session_telemetry.py`). The classifier therefore tolerates a torn final line by design — it reads a best-effort snapshot, which is correct for an advisory. No lock is taken (a read-only advisory must never contend with the hot write path); a missed last line at most delays a verdict by one tick.

### Race 2: Counter read racing counter writes
**Location:** `read_project_health_counters` reading `{project_key}:session-health:*` while `session_health.py` `.incr()`s them.
**Trigger:** Concurrent counter increment during the advisory read.
**Data prerequisite:** Shared Redis integer keys.
**State prerequisite:** None.
**Mitigation:** Reads are plain `GET` of monotonic counters used only as weak corroboration; an off-by-one from a concurrent `incr` is immaterial to a 3-level advisory. No transaction needed. (Read-only via `POPOTO_REDIS_DB.get`; never `.incr`/`.delete`, honoring the no-raw-Redis-mutation rule on counters owned by `session_health`.)

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1539] Terminal crash-signature extraction and gated auto-resume — shipped by Pillar 2 (PR #1718). This pillar reuses its vocabulary but never re-derives terminal signatures or resumes sessions.
- [SEPARATE-SLUG #1540] Human-TUI behavior capture (Pillar 3) — not started; out of scope.
- [SEPARATE-SLUG #1313] The watchdog-driven `pending`-stall Telegram alert (`stalled-session-user-visible-alert.md`) is a different trigger mechanism; this pillar coordinates with it but does not subsume or re-implement it.
- [DESTRUCTIVE] Any change to kill/recovery/transition logic in `agent/session_health.py` — the advisory is structurally read-only; modifying the kill path is forbidden by the hard constraint and would require review-before-execute it deliberately avoids.

## Update System

No update system changes required — this feature is purely internal. It adds two Python modules, one nullable dashboard field, and one `config/reflections.yaml` entry. The reflections registry is loaded from `~/Desktop/Valor/reflections.yaml` (vault, iCloud-synced) with `config/reflections.yaml` as the in-repo fallback; the new `stall-advisory` entry rides the existing reflection-deploy path already present on every machine. No new dependency, secret, or migration to propagate via `/update`.

## Agent Integration

The advisory is consumed by the **dashboard** and the **reflection scheduler**, not by the conversational agent directly. Per this repo's Agent Integration convention:
- [ ] No new CLI entry point required. The existing `python -m tools.valor_session telemetry --id <ID>` already lets the agent read a session's raw trace; the advisory verdict is an internal dashboard/reflection derivation, not a new agent-invoked command. (Optional, nice-to-have: surface the verdict in `valor-session status` output — folded in only if trivial; otherwise out of scope for v1.)
- [ ] No `.mcp.json` change and no new MCP server — the classifier runs inside the dashboard request path and the reflection scheduler the worker already drives.
- [ ] No bridge import — the bridge does not call the classifier; the reflection scheduler invokes the reflection callable, and `ui/data/sdlc.py` invokes the classifier inline.
- [ ] Integration test asserts the reflection callable is resolvable via its dotted path (`reflections.stall_advisory.run_stall_advisory`) and returns the expected `{status, findings, summary}` shape — the same contract `agent/reflection_scheduler.py:391` invokes.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/stall-advisory-classifier.md` — the 3-level verdict model, the signals it reads (idle gaps, tool-timeouts, kill-bearing transitions, live never-started), the explicit non-goal that the advisory NEVER feeds the kill path (#1172), the terminal/non-terminal partition against Pillar 2 (#1539), the dashboard badge, and the reflection's error-only surfacing.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/session-telemetry.md` (v1) and from Pillar 2's `docs/features/crash-signature-auto-resume.md` so the three-pillar boundary is discoverable.

### Inline Documentation
- [ ] Docstring on `classify_session_stall` documenting the fail-soft contract, the read-only/never-kill invariant, and the verdict thresholds.
- [ ] Comment at the `_session_to_pipeline` attach point and the reflection callable noting the advisory-only, terminal-skip behavior.

## Success Criteria

- [ ] `classify_session_stall(events, *, session)` returns a `healthy | suspect | stalled` verdict + reason; pinned thresholds covered by unit tests.
- [ ] A *running* session with zero `turn_start` past startup grace classifies `stalled/never_started` (the live never-started detection the scope amendment asked for); a terminal never-started session is left to Pillar 2 (skipped here).
- [ ] The dashboard shows an advisory badge for non-terminal suspect/stalled sessions and nothing for healthy/terminal ones (`PipelineProgress.stall_advisory` populated correctly).
- [ ] The `stall-advisory` reflection is resolvable, returns `{status, findings, summary}`, and surfaces only on anomaly (no all-clear Telegram spam).
- [ ] The classifier imports nothing from `agent/session_health.py`'s kill/recovery path, and the e2e test asserts no transition/recovery function is invoked during classification (hard-constraint guard).
- [ ] Fail-soft: malformed trace / Redis error / `session=None` never raises and yields a safe `healthy/unclassifiable` verdict (asserted).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `ui/data/sdlc.py` references `classify_session_stall`, `config/reflections.yaml` contains `stall-advisory`, and `agent/session_stall_classifier.py` contains no `import` from `agent.session_health`.

## Team Orchestration

### Team Members

- **Builder (classifier core)**
  - Name: `classifier-builder`
  - Role: `agent/session_stall_classifier.py` — `classify_session_stall`, `StallVerdict`, live never-started rule, counter-read helper, fail-soft wrapper; reuse `agent.crash_signature` vocabulary.
  - Agent Type: builder
  - Resume: true

- **Builder (surfaces)**
  - Name: `surface-builder`
  - Role: `reflections/stall_advisory.py` + `config/reflections.yaml` entry + `PipelineProgress.stall_advisory` field and `_session_to_pipeline` attach point + dashboard badge render.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (advisory)**
  - Name: `advisory-tester`
  - Role: unit (classifier, reflection) + integration (e2e with synthetic trace + no-kill-path assertion).
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `advisory-validator`
  - Role: verify success criteria + verification table, especially the hard-constraint grep/e2e guard.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `advisory-docs`
  - Role: feature doc + index + cross-links.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Classifier core
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: tests/unit/test_session_stall_classifier.py (create)
- **Informed By**: spike-1 (live/non-terminal scope; reuse vocab not `extract_signature`), spike-2 (pure, read-only, no kill-path import)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/session_stall_classifier.py`: `StallVerdict` dataclass, `classify_session_stall(events, *, session, project_counters=None)`, the live never-started rule, the recent-window idle/tool-timeout/kill-transition signal logic, optional `read_project_health_counters(project_key)` (read-only `POPOTO_REDIS_DB.get`), and the fail-soft wrapper.
- Reuse `_bucket_idle_gap` and idle/kill token vocabulary from `agent.crash_signature`. Import NOTHING from `agent.session_health`.

### 2. Surfaces (reflection + dashboard)
- **Task ID**: build-surfaces
- **Depends On**: build-classifier
- **Validates**: tests/unit/test_stall_advisory_reflection.py (create), tests/unit/ sdlc `_session_to_pipeline` coverage (update)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/stall_advisory.py::run_stall_advisory()` returning `{status, findings, summary}`; query running sessions only; per-session exception isolation; error-only Telegram surfacing.
- Add the `stall-advisory` entry to `config/reflections.yaml` (`execution_type: function`, `callable: reflections.stall_advisory.run_stall_advisory`, `every: 300s`, `group: agents`, `enabled: true`).
- Add nullable `stall_advisory` field to `PipelineProgress` and populate it in `_session_to_pipeline` for non-terminal sessions only; render an unobtrusive badge + reason tooltip in the dashboard template.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-classifier, build-surfaces
- **Assigned To**: advisory-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: verdict levels, live never-started, idle/kill signals, fail-soft (malformed input, Redis error), `session=None` fallback, counter corroboration; reflection return shape, per-session isolation, error-only surfacing, terminal skip.
- Integration: synthetic running-session trace in `logs/session_telemetry/` → classify + run reflection → assert verdict AND assert no `agent.session_health` transition/recovery function is called during classification (hard-constraint guard).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: advisory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/stall-advisory-classifier.md` + README index + cross-links to session-telemetry and crash-signature-auto-resume docs.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: advisory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the verification table; confirm all success criteria, especially the no-kill-path grep and the e2e no-transition-call guard.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_session_stall_classifier.py tests/unit/test_stall_advisory_reflection.py tests/integration/test_stall_advisory_e2e.py -x -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Classifier wired (dashboard) | `grep -n classify_session_stall ui/data/sdlc.py` | output > 0 |
| Reflection registered | `grep -n 'stall-advisory' config/reflections.yaml` | output contains stall-advisory |
| Hard constraint: no kill-path import | `grep -c 'session_health' agent/session_stall_classifier.py` | output contains 0 |
| Reflection resolvable | `python -c "from reflections.stall_advisory import run_stall_advisory"` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Surface emphasis:** Both a dashboard badge AND a reflection are planned. Is the dashboard badge sufficient for v1 (reflection deferred), or are both required from the start? (Plan currently ships both; the reflection is the lower-effort half.)
2. **Verdict thresholds:** Proposed defaults — `stalled` on live never-started past a ~120s grace, or sustained idle past ~10 min with no offsetting evidence; `suspect` on a single multi-minute idle gap or rising tool-timeout count. Right balance of sensitivity vs noise, given the false-positive trust risk?
3. **Telegram coordination with #1313:** The watchdog `pending`-stall alert (#1313) and this advisory could both notify about an overlapping symptom. Should the advisory reflection suppress Telegram entirely when #1313's watchdog already covers `pending`, surfacing only the cases #1313 misses (running-but-stalled, live never-started)?
