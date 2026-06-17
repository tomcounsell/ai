---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-06-17
tracking: https://github.com/tomcounsell/ai/issues/1539
last_comment_id: 4716949840
revision_applied: true
---

# Crash-signature library + auto-resume policy from session telemetry (#1539 / epic #1536 Pillar 2)

## Problem

Sessions "crash for no reason and must be resumed." Today that resume is manual: an operator
notices a wedged or dead session, runs `valor-session resume --id <id> --message "continue"`
(or worse, `./scripts/valor-service.sh worker-restart`), and the system limps on. We keep no
record of *what the event stream looked like just before a crash*, nor *which resume strategy
recovered it and whether that worked*. So the same crash class recurs forever and always costs
a human.

The v1 telemetry recorder (#1536, shipped) now records a durable per-event JSONL trace per
session, including `status_transition` events that carry the subprocess kill outcome. The
liveness recovery path (#1537, shipped) reliably drives crashed `running` sessions to a
terminal state (`failed`/`abandoned`). The raw material to learn from crashes exists — nothing
consumes it.

**Current behavior:**
- A crashed session lands in a terminal state (`failed`/`killed`/`abandoned`). Its telemetry
  trace at `logs/session_telemetry/{session_id}.jsonl` records the terminal event sequence,
  but no system reads it for crash analysis (`cmd_telemetry` is display-only).
- Resume is operator-initiated and CLI-only (`cmd_resume`). No record links a crash to the
  resume strategy that recovered it or to the outcome of that resume.
- There is no notion of a "crash signature," so common, recoverable crashes wait for a human
  every single time.

**Desired outcome:**
- Each terminal session's trace is reduced to a **crash signature** — a stable, normalized key
  derived from the terminal event subsequence (e.g. `idle_gap → status_transition[to=failed,
  kill.confirmed_dead=false]`).
- A **crash-signature library** (Popoto-backed) aggregates signatures, counting occurrences and
  recording which resume strategy was attempted per occurrence and its outcome
  (recovered / failed / not-attempted).
- A **resume policy** maps high-confidence signatures to a recommended resume strategy. The
  policy is **proposed by default** (logged + surfaced); auto-apply is **opt-in and gated** by
  per-signature confidence and a global kill-switch.
- A clear **ownership boundary with #1537**: auto-resume acts only on already-terminal sessions
  *after* recovery has finalized them. It never touches `running` sessions, the recovery
  transition, or subprocess killing.
- A **determinism guardrail** (scope amendment from issue comment 2026-06-16): the policy
  distinguishes *transient* mid-stream crashes (resumable) from *deterministic "never-started"*
  startup failures (not resumable — escalate to human instead). Naively auto-resuming a
  deterministic startup failure creates an infinite loop: resume → same hang → terminal → resume …
  The guardrail uses `startup_failure_kind` (populated by the granite startup-failure diagnostic
  shipped in #1710) and a "did the session emit a first turn event?" check to classify and gate.

## Freshness Check

**Baseline commit:** `11ceb581` (`git rev-parse HEAD` at plan time)
**Issue filed at:** `2026-06-01T08:16:10Z`
**Revised at:** `2026-06-17` (two issue comments incorporated — see Scope Amendment below)
**Disposition:** Minor drift — all three cited dependencies shipped between filing and planning;
claims still hold, references updated below. Two scope-amending comments added 2026-06-16
incorporated: (1) determinism guardrail requirement and (2) new `startup_failure_kind` /
`startup_captured_frame` fields from #1710 available as crash-signature inputs.

**New from comments (2026-06-16):**
- `models/agent_session.py:332-336` — `startup_failure_kind` (nullable Field, `"plateau"` |
  `"ceiling"`) and `startup_captured_frame` (nullable Field, capped text snapshot) are live, set
  on every `startup_unresolved` exit. The comment in the source file (`# Coordination surface for
  #1539's auto-resume policy`) confirms this field is explicitly intended for this plan's use.
  `startup_failure_kind="plateau"` = deterministic hang (NOT safe to auto-resume);
  `"ceiling"` = slow/never-settled cold start (MAY be transient, use caution).
- #1710 (granite startup-failure fast diagnostic) CLOSED and shipped via `d2d5761b`; fields
  confirmed present in production.

**File:line references re-verified:**
- `docs/plans/session_telemetry_recorder.md` (cited in issue) — **GONE**: archived after PR #1699
  merged the v1 recorder. Canonical reference is now the implementation: `agent/session_telemetry.py`.
- `agent/session_telemetry.py` — v1 recorder live; `record_telemetry_event`, `read_session_timeline`,
  `finalize_session`; event types confirmed (`turn_start`, `turn_end`, `tool_use`, `token_usage`,
  `idle_gap`, `status_transition`, `telemetry_truncated`, `unknown`). Sink at
  `logs/session_telemetry/{session_id}.jsonl`.
- `agent/session_health.py:1362-1404` — recovery-path `status_transition` emission with `kill` dict
  (`confirmed_dead`, `signal_sent`, `pid`). **This is the crash-signature kill-outcome field.**
- `models/session_lifecycle.py:288-314` — lifecycle-path `status_transition` emission (`kill: None`).
- `models/session_lifecycle.py:61` — `TERMINAL_STATUSES = {completed, failed, killed, abandoned, cancelled}`.
- `tools/valor_session.py:619-726` — `cmd_resume`; **accepts only `{completed, killed, failed}`**
  (status guard at `:660`) with stored `claude_session_uuid`; appends steering message; transitions
  to `pending`. CLI-only. **Mismatch (Blocker 1): `abandoned` — the prime crash class #1537 drives
  crashed sessions to — is inside `TERMINAL_STATUSES` but rejected by this guard.** Resolved below by
  introducing `RESUMABLE_STATUSES`.
- `tools/valor_session.py:1273` — `cmd_telemetry`; display-only, no aggregation.

**Cited sibling issues/PRs re-checked:**
- #1536 (epic) — OPEN. V1 recorder MVP shipped via PR #1699 (commit `415e0e10`).
- #1537 (liveness recovery / ownership-boundary partner) — **CLOSED 2026-06-03, PR #1557**. Now
  fully shipped in `agent/session_health.py::_apply_recovery_transition`. This *clarifies* the
  boundary rather than complicating it (see Architectural Impact).
- #1061 (`valor-session resume` for killed/failed) — **CLOSED 2026-04-21**. Shipped; plan at
  `docs/plans/resume-killed-sessions.md`.
- #1271 (cross-process orphan reaper), #1311 (watchdog recovery) — shipped; out of scope but
  inform the boundary.

**Commits on main since issue was filed (touching referenced files):**
- `415e0e10` feat(#1536): session telemetry recorder v1 — created the substrate this plan consumes.
- `4ca97abe` Liveness recovery confirms subprocess death before requeue (#1537) — established the
  ownership boundary partner; the `kill` dict in `status_transition` originates here.
- `e702cf9c`, `dd926192` — touched health/session code but did not alter the telemetry schema or
  the resume guard.

**Active plans in `docs/plans/` overlapping this area:** `resume-killed-sessions.md` (#1061, the
resume machinery this builds on — complementary, not overlapping), `sdlc-1537-liveness-orphan-subprocess-kill.md`
(#1537, the recovery path — ownership boundary defined below, no conflict).

**Notes:** No drift changes the premise. The one substantive change since filing is that #1537
shipped — which is good news: the recovery path is now a fixed, well-defined boundary to coordinate
against rather than a moving target.

## Prior Art

- **#1536 / PR #1699** — v1 telemetry recorder. The substrate. This plan is its first learning
  consumer. Schema is `#1487`-compatible typed events.
- **#1537 / PR #1557** — liveness recovery confirms subprocess death; emits the kill-enriched
  `status_transition`. The ownership-boundary partner; auto-resume must not duplicate or fight it.
- **#1061 / PR #909** — `valor-session resume` + `retain_for_resume` + `claude_session_uuid` storage.
  The recovery mechanism auto-resume drives. Currently CLI-only.
- **#1271** — cross-process orphan reaper (PPID==1). Reaps dead-worker orphans; orthogonal.
- **#1311** — worker watchdog launchctl escalation. Process-level recovery; orthogonal.
- **#1710 / closed** — granite startup-failure fast diagnostic (capture unresolved frame, alert
  loudly). Shares the "don't silently burn time on a stuck session; capture + act" philosophy;
  no code overlap.

No prior attempt built a crash-signature library or an auto-resume policy. This is greenfield on
top of shipped substrate — no "Why Previous Fixes Failed" section needed.

## Research

External research confirms the design is a known, well-trodden pattern.

**Queries used:**
- "crash signature fingerprint clustering log event sequence automated recovery policy"

**Key findings:**
- **Event-sequence fingerprinting for crash categorization** is established practice. KabOOM
  ([arxiv 2110.10450](https://arxiv.org/pdf/2110.10450)) does unsupervised crash categorization
  via timeseries fingerprinting. Classic system-management work matches recurring terminal event
  sequences against learned rules to trigger automated recovery
  ([USPTO 8069374](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8069374)).
  → Informs the core design: normalize the *terminal event subsequence* (last N events before the
  terminal `status_transition`) into a stable signature key; group identical keys; attach
  resume-strategy outcomes per signature. We deliberately use **deterministic normalization +
  exact-key grouping**, not ML clustering — the event vocabulary is small and typed, so a hash of
  the normalized terminal subsequence is sufficient and explainable (no opaque model, no training
  data requirement). This matches the repo principle of explainable, evidence-based recovery.

## Data Flow

End-to-end, crash → signature → policy → (proposed/auto) resume:

1. **Entry point — terminal transition.** A session reaches a terminal state. The recovery path
   (#1537) or lifecycle path writes a `status_transition` telemetry event (with `kill` dict for
   recovery-path crashes) to `logs/session_telemetry/{session_id}.jsonl`, and
   `finalize_session()` reaps in-memory state.
2. **Signature extraction (new, `agent/crash_signature.py`).** A periodic reflection scans
   recently-terminal sessions (`AgentSession.query` filtered to terminal statuses with a recent
   `updated_at`), reads each trace via `read_session_timeline(session_id)`, and reduces the
   **terminal event subsequence** (the last N events up to and including the terminal
   `status_transition`) to a normalized signature key. Normalization drops volatile fields (pids,
   timestamps, durations, exact gap seconds bucketed) and keeps event types + categorical outcome
   fields (`to`, `kill.confirmed_dead`, `kill.signal_sent`, presence of `idle_gap`).
3. **Library upsert (new, `models/crash_signature.py` — Popoto).** For each session, upsert a
   `CrashSignature` record keyed by the signature hash: increment `occurrence_count`, append a
   compact occurrence record (session_id, terminal status, whether `claude_session_uuid` was
   present → resumable, project_key). All reads/writes go through the Popoto ORM (no raw Redis).
4. **Resume-outcome attribution.** When a session is resumed (via `valor-session resume` or the
   auto-resume path), it is stamped **write-once** with `crash_signature` = the signature of the
   session it recovered. On the resumed session's *next* terminal transition, the reflection — only if
   `crash_outcome_attributed` is falsy (checked via `_truthy()`) — records the outcome (recovered =
   reached `completed`; failed = reached a crash state again) back against the originating signature,
   then flips `crash_outcome_attributed = True` so a later tick cannot double-count. The session's own
   `crash_signature` is never overwritten by re-extraction (re-extraction writes only to the library).
   This is the learning loop.
5. **Policy derivation (new).** A signature with `occurrence_count >= MIN_OCCURRENCES` and a
   resume-success ratio `>= MIN_SUCCESS_RATIO` for a given strategy yields a **policy entry**:
   "signature X → strategy Y, confidence Z."
6. **Output — propose vs. auto-apply.**
   - **Propose (default):** the reflection logs the proposed policy and surfaces it (CLI:
     `valor-session crash-policy list`; optional Telegram digest line). No automatic action.
   - **Auto-apply (opt-in, gated):** if `CRASH_AUTORESUME_ENABLED` is set AND a freshly-terminal
     session's signature has an auto-eligible policy entry, the reflection programmatically resumes
     it (new programmatic resume entry point) with a synthetic "continue" steering message, tags the
     resume with the signature, and increments an attempt counter. A global kill-switch and a
     per-session max-auto-resume cap prevent loops.

## Architectural Impact

- **New dependencies:** none external. New internal modules: `agent/crash_signature.py` (extraction
  + library logic), `models/crash_signature.py` (Popoto model). New reflection callable in
  `reflections/maintenance.py` (or a new `reflections/crash_recovery.py`).
- **Interface changes:** additive. New CLI subcommands under `valor-session`
  (`crash-signatures`, `crash-policy`). A new **programmatic resume function** extracted from
  `cmd_resume`'s core so both the CLI and the auto-resume path call the same code (the CLI keeps
  its argparse wrapper; the shared core takes `(session, message) -> result`).
- **Coupling:** auto-resume reads telemetry (already public API: `read_session_timeline`) and the
  `AgentSession` model. It does **not** couple to `agent/session_health.py`'s recovery internals —
  it only consumes the telemetry the recovery path *emits*. This is the key decoupling.
- **Data ownership:** new `CrashSignature` Popoto model owns signature aggregates. `AgentSession`
  gains **two** small additive nullable fields (Blocker 2):
  - `crash_signature` (str, nullable, **write-once**): the signature of the crash this session is a
    resume *of*, stamped exactly once at resume time for outcome attribution. The reflection never
    re-extracts or overwrites it.
  - `crash_outcome_attributed` (bool, default `False`): the idempotency key. Set `True` after the
    reflection has credited/debited the originating signature for this session's terminal outcome,
    guarded with `_truthy()` (from `agent/session_pickup.py:36`) on read — Popoto stores bools as
    `"True"`/`"False"` strings, so naive `bool(value)` is always truthy (per memories
    `reference_popoto_bool_storage`, `feedback_field_backcompat_heal`).

  Both fields are covered by the generic descriptor-healing path (`_heal_descriptor_pollution`), no
  backcompat code needed.
- **Reversibility:** high. Propose-only mode changes nothing in the execution path. Auto-apply is
  behind an env flag; clearing it reverts to propose-only. Deleting the reflection entry from
  `reflections.yaml` stops all signature work. The `CrashSignature` model and the additive field
  are inert if unused.

## Ownership boundary with #1537 (critical)

| Concern | Owner | Action |
|---|---|---|
| Detect no-progress `running` session | **#1537** (`session_health.py`) | liveness loop |
| Kill the `claude -p` subprocess (SIGTERM/SIGKILL), confirm death | **#1537** | `_confirm_subprocess_dead` |
| Drive a crashed/hung session to a **terminal** state (`failed`/`abandoned`) | **#1537** | `_apply_recovery_transition` |
| Emit `status_transition` telemetry (with `kill` dict) | **#1537** + lifecycle | telemetry recorder |
| Read terminal telemetry → extract crash signature | **#1539** (this) | `crash_signature.py` reflection |
| Decide a terminal session is resumable + resume it | **#1539** | auto-resume policy |

**Invariant:** #1539 only ever acts on sessions whose status is already terminal **and** resumable —
i.e. in `RESUMABLE_STATUSES` (`{completed, killed, failed, abandoned}` = `TERMINAL_STATUSES` minus
`cancelled`, the intentional-stop status). It
performs **no kills**, touches no `running` session, and never writes the `pending` requeue that
#1537 owns *except* through the sanctioned `transition_status(..., reject_from_terminal=False)`
inside the shared programmatic-resume core — the same atomic path `cmd_resume` already uses. The two
systems are sequential, not concurrent: #1537 finalizes the corpse; #1539 later reads it and decides
whether to resurrect. They share no mutable state beyond the `AgentSession` record, and resume only
fires on terminal records #1537 has finished with.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm propose-vs-auto-apply default and gating thresholds)
- Review rounds: 1-2 (the auto-resume path touches a safety-sensitive area; reviewer scrutiny on
  loop guards and the #1537 boundary)

Medium because there are three cohesive but distinct deliverables (signature extraction, the
library/outcome loop, the gated policy) plus a programmatic-resume refactor. None individually is
large; the integration and the safety guards are where the cost lives.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| V1 telemetry recorder present | `python -c "from agent.session_telemetry import read_session_timeline"` | Source of crash traces |
| Resume machinery present | `python -c "import tools.valor_session as v; assert hasattr(v,'cmd_resume')"` | Recovery mechanism to drive |
| Popoto ORM available | `python -c "import popoto"` | Backing store for the signature library |

Run all checks: `python scripts/check_prerequisites.py docs/plans/crash_signature_auto_resume.md`

## Solution

### Key Elements

- **Signature extractor** (`agent/crash_signature.py`): reduces a terminal session's telemetry
  trace to a normalized, stable signature key. Pure function over a list of events — easily unit
  tested with fixture traces.
- **Crash-signature library** (`models/crash_signature.py`, Popoto): aggregates signatures with
  occurrence counts and per-strategy resume outcomes. Project-scoped.
- **Programmatic resume core** (refactored out of `cmd_resume` in `tools/valor_session.py`): a
  callable `resume_session(session, message, *, source) -> ResumeResult` shared by the CLI and the
  auto-resume path. Tags the resumed session with the originating crash signature.
- **Crash-recovery reflection** (`reflections/` callable, registered in `reflections.yaml`):
  periodically extracts signatures from recently-terminal sessions, updates the library, derives the
  policy, and — in propose mode — logs/surfaces proposals; in auto mode (gated) resumes eligible
  sessions.
- **CLI surfaces** (`valor-session crash-signatures` / `crash-policy`): inspect the library and the
  derived policy; show which signatures are auto-eligible.
- **Safety gates:** `CRASH_AUTORESUME_ENABLED` env kill-switch (default off → propose-only), a
  per-session `auto_resume_attempts` cap, and a global per-run resume budget so a storm of identical
  crashes can't trigger a resume storm.
- **Determinism guardrail:** a two-level check (1) `startup_failure_kind="plateau"` → flag
  `NON_RESUMABLE_DETERMINISTIC`, escalate; (2) no `turn_start` event in trace → same. Consumes
  the `startup_failure_kind` and `startup_captured_frame` fields shipped by #1710 — no new
  telemetry emission needed.

### Flow

Terminal session lands → crash-recovery reflection ticks → reads trace → extracts signature →
upserts library → derives policy → **propose mode:** logs "signature X seen N times, strategy Y
recovers Z% — would auto-resume" + surfaces in `crash-policy list`; **auto mode (gated):** resumes
the session via the programmatic core, tags it with signature X → on that session's next terminal
transition, outcome is attributed back to X (the learning loop closes).

### Technical Approach

- **Normalization is deterministic, not ML.** Signature = hash of the normalized terminal
  subsequence (last N events, default N configurable). Keep: event `type`, and for
  `status_transition` the categorical fields `to`, `kill.confirmed_dead`, `kill.signal_sent`;
  presence/absence of a preceding `idle_gap` (with gap bucketed into coarse bands, not raw seconds).
  Drop: pids, timestamps, exact durations, token counts. This yields a small, human-readable
  signature string before hashing (store both the human form and the hash).
- **Outcome attribution via two additive `AgentSession` fields, never one overloaded field (Blocker
  2).** When a session is resumed, stamp `crash_signature` **once** (write-once) with the signature of
  the crash it recovers. On its next terminal transition, the reflection — for any session whose
  `crash_signature` is set and whose `crash_outcome_attributed` is falsy (checked via `_truthy()`) —
  credits/debits the originating signature, then sets `crash_outcome_attributed = True`. This split
  prevents the two corruption modes the critique named: (a) a resumed session stamped X that re-crashes
  as Y cannot overwrite X before attribution (the stamp is write-once and the re-extraction writes to
  the *library*, never back to the session's `crash_signature`); (b) a later reflection tick cannot
  double-count, because `crash_outcome_attributed` is the idempotency key. No new event type needed.
- **Reuse `transition_status(..., reject_from_terminal=False)`** for the actual resume — the exact
  atomic path `cmd_resume` already uses. Do not invent a second resume mechanism.
- **Resolve the terminal-status / resumable-status mismatch (Blocker 1).** Today the resume guard at
  `tools/valor_session.py:660` accepts only `{completed, killed, failed}`, but the auto-resume
  Invariant filters on `TERMINAL_STATUSES = {completed, failed, killed, abandoned, cancelled}`. #1537
  drives crashed sessions to `failed`/`abandoned` — so `abandoned` (the prime crash class this feature
  recovers) is inside the auto-resume filter yet rejected by the resume core. **Fix:** define a single
  module-level constant `RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})`
  in `models/session_lifecycle.py` (next to `TERMINAL_STATUSES`). Both `cmd_resume` and the shared
  `resume_session` core check membership against `RESUMABLE_STATUSES` instead of the hardcoded tuple.
  `cancelled` is deliberately excluded — a cancelled session represents an intentional human stop, not
  a crash, and must never auto-resume; the auto-resume reflection therefore intersects its terminal
  query with `RESUMABLE_STATUSES`, narrowing `TERMINAL_STATUSES` down to the auto-eligible set. The
  resume core raises/returns a clear "not resumable" result for any status outside `RESUMABLE_STATUSES`.
- **Determinism guardrail — never auto-resume "never-started" failures** (scope amendment). Before
  any auto-resume decision, the reflection applies a two-level gating check:
  1. **`startup_failure_kind` check (fast path):** if the terminal session has
     `startup_failure_kind="plateau"` (deterministic hang — same binary, same config, guaranteed
     re-hang), mark the signature class as `NON_RESUMABLE_DETERMINISTIC` and route to escalation
     (Telegram alert / loud log), NOT resume. `"ceiling"` sessions are tentatively resumable
     (slow cold start is transient) but get a reduced confidence cap in the policy.
  2. **First-event check (general case):** if the session has no `startup_failure_kind` set (e.g.
     non-granite sessions or older sessions before #1710), check whether the telemetry trace
     contains at least one `turn_start` event. No `turn_start` = never produced output = treat as
     "never-started" = `NON_RESUMABLE_DETERMINISTIC`. At least one `turn_start` = mid-stream
     crash = tentatively resumable.
  The `CrashSignature` model records whether each signature class is `resumable` (bool) and —
  for non-resumable classes — whether it has `escalated` to human attention. The extractor emits
  this flag in the human-readable signature string (e.g. `never_started|plateau` vs.
  `mid_stream|idle_gap+failed`). The integration test asserts that a `startup_unresolved` session
  with `startup_failure_kind="plateau"` is never routed to `resume_session`.
- **Gate auto-apply behind env + thresholds + caps**, all read at reflection-run time so toggling
  requires no restart of the analysis logic.
- **Project scoping throughout** — the library, queries, and any bulk operations are scoped by
  `project_key` (per memory `feedback_test_redis_isolation`); tests never touch production data.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The signature extractor must never raise on a malformed/empty/truncated trace — a trace with
  zero events, only an `unknown` event, or a `telemetry_truncated` marker must yield a well-defined
  "unclassifiable" signature, not an exception. Test each.
- [ ] The reflection callable wraps its body so a single bad session never aborts the whole run
  (mirror the fail-silent posture of `record_telemetry_event`); assert a logged warning + continued
  processing of remaining sessions when one trace read fails.
- [ ] No new bare `except Exception: pass` — every swallow logs at WARNING with context.

### Empty/Invalid Input Handling
- [ ] `extract_signature([])` → returns the "unclassifiable" sentinel signature, not a crash.
- [ ] A terminal session with **no** trace file (`read_session_timeline` returns `[]`) →
  unclassifiable; never auto-resumed.
- [ ] A terminal session with `claude_session_uuid is None` → marked non-resumable; auto-resume must
  skip it (it cannot be resumed — same guard as `cmd_resume`).
- [ ] Whitespace/None `project_key` → session is processed under a sentinel project bucket, never
  cross-contaminates another project's library.

### Error State Rendering
- [ ] `valor-session crash-policy list` with an empty library prints a clear "no signatures recorded
  yet" message and exits 0, not a traceback.
- [ ] When auto-resume is requested but the kill-switch is off, the reflection logs a clear
  "auto-resume disabled (propose-only)" line rather than silently doing nothing.
- [ ] When the per-session auto-resume cap is hit, log a clear "max auto-resume attempts reached for
  session X (signature Y); leaving terminal for human" line.

## Test Impact

- [ ] `tests/unit/test_session_telemetry.py` (if present) — no change; this plan only *reads* via
  the public `read_session_timeline`. Confirm with `grep -rn "read_session_timeline" tests/`.
- [ ] `tests/unit/test_valor_session_resume_release.py` — UPDATE: refactoring `cmd_resume` to delegate
  to the new `resume_session(session, message, source)` core must not change CLI behavior; existing
  `cmd_resume` resume/release tests must still pass against the wrapper. Add cases for the extracted
  core, including the new `RESUMABLE_STATUSES` membership check (notably `abandoned` now resumable,
  `cancelled` still rejected).
- [ ] `tests/unit/test_resume_hydration.py` — UPDATE: confirm the `claude_session_uuid` hydration path
  is unaffected by the `cmd_resume` → `resume_session` core extraction (behavior-preserving refactor).
- [ ] `tests/unit/test_crash_signature_cli.py` — NEW: cases for the new `valor-session crash-signatures`
  / `crash-policy list` subcommands (empty-library handling, populated rendering, auto-eligible flag).
- [ ] `tests/unit/test_crash_signature.py` — NEW: extractor normalization (kill-confirmed-dead,
  idle-gap-then-fail, killed-by-operator, abandoned-local, empty/truncated/unknown traces);
  signature stability (same logical crash → same hash; different crash → different hash).
- [ ] `tests/unit/test_crash_signature_library.py` — NEW: Popoto model upsert/occurrence count,
  per-strategy outcome attribution, project scoping isolation, policy derivation thresholds.
- [ ] `tests/integration/test_crash_auto_resume.py` — NEW: end-to-end with a fixture terminal
  session — propose mode (no resume, policy surfaced) and gated auto mode (resume fires, signature
  tagged, cap enforced). **Add case:** `startup_failure_kind="plateau"` session → guardrail fires,
  escalation log produced, session NOT transitioned to `pending`. Project-scoped test data, cleaned
  up via Popoto per CLAUDE.md hygiene.

No existing tests are broken beyond the additive `cmd_resume` refactor (which preserves behavior).

## Rabbit Holes

- **ML clustering / embeddings over traces.** Tempting, but the event vocabulary is small and typed;
  deterministic normalization + exact-key grouping is sufficient, explainable, and needs no training
  data. Do NOT build a model.
- **A general "resume strategy" framework with pluggable strategies.** v1 has effectively one
  strategy (`--resume <uuid>` + "continue"). Model the strategy field for future extension but ship
  with the single concrete strategy; do not build a strategy registry/DSL now.
- **Reworking the #1537 recovery path to call auto-resume inline.** This collapses the ownership
  boundary and creates the exact concurrency hazard the boundary prevents. Auto-resume stays a
  *separate, later, terminal-only* pass. Do NOT touch `_apply_recovery_transition`.
- **Retroactive backfill of signatures over all historical traces.** Forward-looking from ship is
  enough to prove value; a one-shot backfill is a separate, optional chore.
- **Tuning idle-gap bucket bands to perfection.** Pick coarse, sane bands; do not over-fit.

## Risks

### Risk 1: Auto-resume loop — a session that crashes the same way every resume
**Impact:** A non-recoverable crash with a high historical success ratio (from earlier, different
conditions) gets auto-resumed repeatedly, burning compute and wedging slots.
**Mitigation:** Per-session `auto_resume_attempts` hard cap (default low, e.g. configurable small N);
once hit, the session stays terminal and a clear log line invites human action. A global per-run
resume budget caps a storm. The success-ratio that *demotes* a signature updates from real outcomes,
so a signature that starts failing auto-demotes itself out of auto-eligibility.

### Risk 2: Signature collision — two genuinely different crashes hash to the same signature
**Impact:** A recoverable signature's success ratio is polluted by an unrecoverable crash sharing the
key, leading to wrong auto-resume decisions.
**Mitigation:** Store the human-readable signature string alongside the hash so collisions are
inspectable via `crash-signatures`. Normalization keeps the categorical fields that distinguish the
crash *classes* the issue names (kill-confirmed-dead vs idle-gap-then-fail vs operator-kill). If a
collision is observed, widen the normalization (include one more categorical field), not narrow it to
raw values (which would fragment every crash into a unique signature and learn nothing).

### Risk 4: Auto-resume loop on a deterministic "never-started" failure
**Impact:** A granite session that exits `startup_unresolved` (e.g. `/granite:prime-pm-role` command
not found, same every time) gets auto-resumed repeatedly, burning PTYPool slots and masking the root
cause indefinitely (the 2026-06-16 cyndra outage scenario).
**Mitigation:** The determinism guardrail (Technical Approach) hard-blocks auto-resume for
`startup_failure_kind="plateau"` sessions and for any session with no `turn_start` events.
These sessions are routed to escalation (not resume). The `CrashSignature` model marks the entire
signature class as `non_resumable`; once a signature is classified `NON_RESUMABLE_DETERMINISTIC`,
the policy never promotes it to auto-eligible regardless of attempt count. The escalation path logs
at WARNING with the captured frame (`startup_captured_frame`) if available, so the human can
diagnose the root cause immediately.

### Risk 3: Fighting #1537 / acting on a non-terminal session
**Impact:** Auto-resume races the recovery path, double-handles a session, or requeues a `running`
session out from under a live subprocess.
**Mitigation:** Hard invariant enforced in code: the auto-resume path filters to
`TERMINAL_STATUSES` and re-reads the session status immediately before the atomic
`transition_status(..., reject_from_terminal=False)`; if the status is no longer terminal it aborts.
The resume uses the same atomic transition `cmd_resume` uses. Unit + integration tests assert
auto-resume is a no-op against a `running`/`pending` session.

## Race Conditions

### Race 1: Reflection reads a session that is mid-transition to terminal
**Location:** crash-recovery reflection query + `read_session_timeline`
**Trigger:** The reflection scans for terminal sessions while the lifecycle path is still writing the
final `status_transition` event.
**Data prerequisite:** The terminal `status_transition` event must be flushed to the trace before
signature extraction runs.
**State prerequisite:** The session's DB status must read terminal.
**Mitigation:** The recorder flushes each event on write (`fh.flush()` in `_write_event`), and the
DB status transition to terminal happens in `finalize_session` *after* (or atomically with) the event
emission. The reflection filters on DB terminal status; if the trace's last event isn't yet a
terminal `status_transition`, the extractor yields "incomplete" and the session is retried on the next
reflection tick rather than mis-signatured. Idempotent: re-running extraction on the same session is a
no-op upsert (keyed by session_id within the occurrence record).

### Race 2: Concurrent auto-resume + manual resume of the same session
**Location:** programmatic `resume_session` core
**Trigger:** Operator runs `valor-session resume` on a terminal session at the same tick the
reflection decides to auto-resume it.
**Data prerequisite:** Only one resume should append a steering message + transition to `pending`.
**State prerequisite:** The session must still be terminal when the transition fires.
**Mitigation:** `transition_status(..., reject_from_terminal=False)` is atomic at the Redis level and
flips the session out of terminal; the second caller re-reads, sees a non-terminal status, and aborts
(the auto-resume path's pre-transition status re-check from Risk 3). Worst case is one redundant
"continue" steering message, which is harmless.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1538] Pillar 1 — the healthy-vs-stalled classifier. This plan consumes only the
  *terminal* trace, not live health classification.
- [SEPARATE-SLUG #1540] Pillar 3 — human TUI behavior capture/emulation.
- Do NOT modify `agent/session_health.py::_apply_recovery_transition` or any subprocess-kill logic —
  that is #1537's owned territory (see Ownership boundary). This plan is read-only with respect to the
  recovery path.
- Do NOT add ML clustering or embeddings (Rabbit Holes).
- Do NOT build a one-shot historical backfill in this plan; forward-looking signature capture from
  ship is sufficient to prove value (a backfill, if wanted, is a separate chore).
- Resurrecting sessions with null `claude_session_uuid` (killed before first turn) — same hard limit
  as `cmd_resume`; such sessions are non-resumable and never auto-resumed.
- Auto-resuming `cancelled` sessions — `cancelled` is an intentional human stop, excluded from
  `RESUMABLE_STATUSES`; never auto-resumed.
- A global cross-project signature rollup (Resolved Q3) — deferred to a separate optional chore; v1 is
  project-scoped only.

## Update System

No update system changes required for the core. The new modules, CLI subcommands, and reflection
callable propagate via the normal `git pull && ./scripts/valor-service.sh restart` on each machine.
One soft touch: the new reflection must be added to the canonical `reflections.yaml`
(`~/Desktop/Valor/reflections.yaml`, iCloud-synced per memory `reference_reflections_config`) so it
schedules — document this as a one-line config addition in the feature doc, not a script change.

The `CRASH_AUTORESUME_ENABLED` flag is per-machine: **enabled on exactly one designated machine**
(the dedicated bot-identity machine, per Resolved Q1 / memory `project_sdlc_bot_token`) and **off
elsewhere** (propose-only). Add a placeholder to `.env.example` with a comment line documenting the
"on for the one designated auto-resume machine, off elsewhere" posture, and a field in
`config/settings.py` per the secrets convention (it is a feature flag, not a secret, but follows the
same wiring). The designated machine sets `CRASH_AUTORESUME_ENABLED=1` in its
`~/Desktop/Valor/.env`. No `/update` script change is required — the flag is read at reflection-run
time.

## Agent Integration

The auto-resume path runs inside the worker's reflection scheduler — **bridge-internal**, not an
agent-facing surface. No new MCP server or `.mcp.json` change is required for auto-resume itself.

The **inspection** surfaces (`valor-session crash-signatures`, `valor-session crash-policy`) are
operator-facing CLI subcommands on the existing `valor-session` entry point
(`pyproject.toml [project.scripts]` already declares `valor-session`) — invocable by the agent via its
Bash tool, like every other `valor-session` subcommand. No new `[project.scripts]` entry needed.

Integration test verifies (a) the reflection callable runs end-to-end against fixture terminal
sessions and (b) the new CLI subcommands return correctly-shaped output.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/crash-signature-auto-resume.md` describing the signature schema,
  normalization rules, the library, the policy thresholds, the propose-vs-auto-apply modes, the
  `CRASH_AUTORESUME_ENABLED` gate, the #1537 ownership boundary, and — prominently — the
  determinism guardrail (never-started / `startup_failure_kind="plateau"` escalation path).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update the epic-adjacent doc / link from the v1 recorder feature doc (if one exists) to this
  Pillar 2 consumer so the telemetry-to-learning story is traceable.

### External Documentation Site
- [ ] N/A — this repo does not publish a Sphinx/MkDocs/RtD site.

### Inline Documentation
- [ ] Docstrings on `extract_signature`, the `CrashSignature` model, and `resume_session` (the shared
  core) — especially the normalization contract and the terminal-only invariant.
- [ ] A CLAUDE.md Quick Commands row for `valor-session crash-signatures` / `crash-policy`.
- [ ] Add the new reflection to the reflections config documentation.

## Success Criteria

- [ ] A terminal session's trace is reduced to a stable, human-readable + hashed crash signature;
  the same logical crash class always yields the same signature, distinct classes yield distinct ones.
- [ ] The `CrashSignature` library aggregates occurrences and per-strategy resume outcomes,
  project-scoped, via Popoto ORM only (no raw Redis).
- [ ] `valor-session crash-signatures` and `valor-session crash-policy list` render the library and
  the derived policy, including which signatures are auto-eligible, and handle the empty case cleanly.
- [ ] A resumed session is tagged with the originating crash signature; on its next terminal
  transition the outcome is attributed back, updating the signature's success ratio (the learning loop
  closes — demonstrated in the integration test).
- [ ] In default (propose) mode, the execution path is unchanged: no session is auto-resumed; proposals
  are logged + surfaced only.
- [ ] With `CRASH_AUTORESUME_ENABLED` set, an eligible terminal session is auto-resumed via the shared
  programmatic core; the per-session attempt cap and global per-run budget are enforced.
- [ ] **(Operator-facing, Blocker 3) End-to-end auto-resume with ZERO human action.** Against a
  real-or-replayed crash trace (a fixture `.jsonl` reproducing the `abandoned`-after-idle-gap crash
  class #1537 produces), the reflection — with auto-apply enabled — extracts the signature, matches an
  eligible policy entry, and drives the terminal session back to `pending` with a synthetic "continue"
  steering message **without any operator command**. The integration test asserts: (a) the session
  reaches `pending` purely from the reflection tick, (b) no `valor-session resume` invocation occurred,
  (c) `crash_signature` is stamped and on the next terminal transition the outcome is attributed
  exactly once (`crash_outcome_attributed` flips, no double-count). This criterion is the concrete
  proof that the stated pain ("resume is manual, costs a human every time") is actually removed, not
  merely surfaced.
- [ ] Auto-resume is provably a no-op against any non-terminal session (unit + integration test) —
  the #1537 boundary holds.
- [ ] **Determinism guardrail holds:** a session with `startup_failure_kind="plateau"` (or no
  `turn_start` events in its trace) is never auto-resumed. The reflection routes it to the
  escalation path — producing a `[ESCALATE]` WARNING log with `startup_captured_frame` context —
  and the session status remains terminal. Demonstrated in `tests/integration/test_crash_auto_resume.py`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Lint clean (`python -m ruff check . && python -m ruff format --check .`).

## Team Orchestration

### Team Members

- **Builder (signature-extractor)**
  - Name: sig-builder
  - Role: `agent/crash_signature.py` extractor + `models/crash_signature.py` Popoto model + unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (resume-core + auto-resume)**
  - Name: resume-builder
  - Role: Refactor `cmd_resume` to a shared `resume_session` core; build the gated auto-resume path
    and the crash-recovery reflection callable; the safety guards
  - Agent Type: builder
  - Resume: true

- **Builder (cli-surfaces)**
  - Name: cli-builder
  - Role: `valor-session crash-signatures` / `crash-policy` subcommands
  - Agent Type: builder
  - Resume: true

- **Validator (crash-auto-resume)**
  - Name: car-validator
  - Role: Verify all success criteria; run targeted pytest + ruff; specifically prove the terminal-only
    invariant and the loop guards
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: car-doc
  - Role: Feature doc, README index, CLAUDE.md rows, reflections config note
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Signature extractor + library model
- **Task ID**: build-signature
- **Depends On**: none
- **Validates**: `tests/unit/test_crash_signature.py` (create), `tests/unit/test_crash_signature_library.py` (create)
- **Informed By**: Research (deterministic normalization, not ML); Recon (event types, `kill` dict fields); `models/agent_session.py:332-336` (`startup_failure_kind`, `startup_captured_frame`)
- **Assigned To**: sig-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `agent/crash_signature.py::extract_signature(events, *, session=None) -> CrashSignatureKey`
  — pure, fail-safe over empty/truncated/unknown traces; emits human form + hash. The optional
  `session` parameter allows the extractor to read `startup_failure_kind` and check for at least one
  `turn_start` event to apply the determinism guardrail: set `resumable=False` and
  `signature_class="NON_RESUMABLE_DETERMINISTIC"` if plateau or never-started.
- Implement `models/crash_signature.py` Popoto model (signature hash key, human form,
  `occurrence_count`, per-strategy outcome tallies, `project_key`, `resumable` bool,
  `escalated` bool); upsert + occurrence append. A `NON_RESUMABLE_DETERMINISTIC` signature's
  `resumable` field is permanently `False` — no policy entry can promote it.
- Unit tests per Test Impact (normalization cases, stability, project isolation); add cases:
  `startup_failure_kind="plateau"` → `NON_RESUMABLE_DETERMINISTIC`; no `turn_start` in trace
  → `NON_RESUMABLE_DETERMINISTIC`; `startup_failure_kind="ceiling"` + has `turn_start` → resumable.

### 2. Programmatic resume core + auto-resume reflection
- **Task ID**: build-resume
- **Depends On**: build-signature
- **Validates**: `tests/unit/test_valor_session_resume_release.py` + `tests/unit/test_resume_hydration.py` (update for refactor), `tests/integration/test_crash_auto_resume.py` (create)
- **Informed By**: Recon (`cmd_resume` at `tools/valor_session.py:619-726`; atomic `transition_status`); Ownership boundary
- **Assigned To**: resume-builder
- **Agent Type**: builder
- **Parallel**: false
- Define `RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})` in
  `models/session_lifecycle.py` (Blocker 1). Refactor `cmd_resume` to delegate to a new shared
  `resume_session(session, message, *, source)` core, both checking membership against
  `RESUMABLE_STATUSES` (replacing the hardcoded `("completed","killed","failed")` tuple at `:660`).
  CLI behavior unchanged for the existing three statuses; `abandoned` now resumable, `cancelled` still
  rejected. Existing resume/hydration tests still pass.
- Add **two** additive nullable `AgentSession` fields (Blocker 2): `crash_signature` (str, write-once)
  and `crash_outcome_attributed` (bool, default `False`), the latter read via `_truthy()`.
- Build the crash-recovery reflection callable: scan recently-terminal sessions (terminal query
  intersected with `RESUMABLE_STATUSES`), extract signatures, upsert library, derive policy, attribute
  outcomes for resumed sessions exactly once (guard on `crash_outcome_attributed`, then flip it).
- Implement propose mode (default) and gated auto-apply (`CRASH_AUTORESUME_ENABLED`, ENABLED on the one
  designated machine per Resolved Q1) with per-session attempt cap (`min()`-clamped against an absolute
  hard ceiling, Resolved Q2) + global per-run budget + terminal-only pre-transition re-check (Risk 3).
- **Apply determinism guardrail in reflection:** before any resume decision, call `extract_signature`
  with the session to get the signature. If `signature.resumable is False` (NON_RESUMABLE_DETERMINISTIC),
  skip resume entirely and call the escalation path: log at WARNING with signature, session id,
  `startup_captured_frame` (if set), and a `[ESCALATE]` tag so the operator can grep. In auto mode,
  also emit a Telegram alert (if bridge is reachable) with the captured frame. Never retry a
  `NON_RESUMABLE_DETERMINISTIC` session on subsequent reflection ticks.
- Register the reflection (document the `reflections.yaml` addition).

### 3. CLI inspection surfaces
- **Task ID**: build-cli
- **Depends On**: build-signature
- **Validates**: `tests/unit/test_crash_signature_cli.py` (create — subcommand cases)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `valor-session crash-signatures` and `valor-session crash-policy list` subcommands (empty-case
  handling per Failure Path Test Strategy).

### 4. Validate implementation
- **Task ID**: validate-impl
- **Depends On**: build-resume, build-cli
- **Assigned To**: car-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_crash_signature.py tests/unit/test_crash_signature_library.py tests/unit/test_crash_signature_cli.py tests/unit/test_valor_session_resume_release.py tests/unit/test_resume_hydration.py tests/integration/test_crash_auto_resume.py -v`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Prove the terminal-only invariant: auto-resume is a no-op against `running`/`pending` sessions.
- Prove loop guards: per-session cap and global budget enforced.
- Prove the determinism guardrail: a session with `startup_failure_kind="plateau"` NEVER gets
  auto-resumed — it produces an `[ESCALATE]` log and no `pending` transition.
- Verify no raw Redis on Popoto keys (`grep` for direct `r.hgetall`/`r.delete` in new code → none).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-impl
- **Assigned To**: car-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/crash-signature-auto-resume.md`; add README index row; add CLAUDE.md rows;
  document the reflections.yaml addition and the `CRASH_AUTORESUME_ENABLED` flag.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-signature, build-resume, build-cli, validate-impl, document-feature
- **Assigned To**: car-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run targeted pytest + ruff.
- Walk each Success Criterion and confirm.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted unit tests pass | `pytest tests/unit/test_crash_signature.py tests/unit/test_crash_signature_library.py -x -q` | exit code 0 |
| Resume refactor preserves CLI | `pytest tests/unit/test_valor_session_resume_release.py tests/unit/test_resume_hydration.py -x -q` | exit code 0 |
| Integration loop closes | `pytest tests/integration/test_crash_auto_resume.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw Redis in new code | `grep -rn "r\.\(hgetall\|delete\|srem\|sadd\|zrem\)" agent/crash_signature.py models/crash_signature.py` | exit code 1 |
| Shared resume core exists | `grep -n "def resume_session" tools/valor_session.py` | output > 0 |
| RESUMABLE_STATUSES defined | `grep -n "RESUMABLE_STATUSES" models/session_lifecycle.py` | output > 0 |
| Idempotency field present | `grep -n "crash_outcome_attributed" models/agent_session.py` | output > 0 |
| Terminal-only guard present | `grep -rn "RESUMABLE_STATUSES\|TERMINAL_STATUSES\|reject_from_terminal" agent/crash_signature.py reflections/` | output > 0 |
| Determinism guardrail present | `grep -n "NON_RESUMABLE_DETERMINISTIC\|startup_failure_kind\|ESCALATE" agent/crash_signature.py` | output > 0 |
| Escalation path present | `grep -rn "ESCALATE\|startup_captured_frame" reflections/` | output > 0 |

## Critique Results

FULL critique (3 critics) returned NEEDS REVISION. Blockers resolved in this revision pass:

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critic 1 | Terminal-status mismatch: prime recovery output (`abandoned`) is in the auto-resume filter but rejected by the resume core (`:660` accepts only `{completed,killed,failed}`). | Technical Approach + Ownership Invariant + build-resume task | New `RESUMABLE_STATUSES = {completed,killed,failed,abandoned}` in `session_lifecycle.py`; both `cmd_resume` and shared `resume_session` core check it; `cancelled` excluded (intentional stop); reflection query intersects terminal set with `RESUMABLE_STATUSES`. |
| Blocker | Critic 2 | Overloaded `crash_signature` field (immutable stamp AND re-extracted each tick) corrupts outcome attribution; no idempotency key. | Architectural Impact + Technical Approach + Data Flow step 4 + build-resume task | Split into two additive nullable fields: `crash_signature` (write-once stamp) + `crash_outcome_attributed` (bool idempotency key, read via `_truthy()`); re-extraction writes only to the library, never back to the session field. |
| Blocker | Critic 3 | Default propose-only mode delivers zero pain reduction; auto-apply default undecided (Open Q1). | Resolved Q1 (now a binding gate) + new operator-facing Success Criterion + Update System | Auto-apply ships ENABLED on one designated machine from the start (per `project_sdlc_bot_token`); new Success Criterion proves end-to-end zero-human-action auto-resume against a replayed `abandoned` trace. |
| Structural | Automated | Plan referenced non-existent `tests/unit/test_valor_session.py` in 4 places. | Test Impact + build-resume/build-cli Validates + Verification table | Repointed to real files `test_valor_session_resume_release.py` + `test_resume_hydration.py`; named new `test_crash_signature_cli.py` for the new subcommands. |

---

## Resolved Questions (formerly Open)

1. **RESOLVED — Propose-vs-auto-apply default (was a blocking gate; Blocker 3).** The critique
   correctly observed that propose-only delivers **zero** reduction in the stated pain ("resume is
   manual, costs a human every time") — only auto-apply removes the human from the loop. Per the
   single-trusted-machine posture already established for the SDLC bot identity (memory
   `project_sdlc_bot_token`), the resolution is:
   - The `CRASH_AUTORESUME_ENABLED` flag and the propose/auto split **remain** — they are the
     safety substrate and the per-machine opt-in.
   - **Auto-apply ships ENABLED on exactly one designated machine from the start** (the same dedicated
     machine that runs the bot identity), so the feature delivers real pain reduction at ship rather
     than waiting for a later flip. All other machines default to propose-only.
   - The wiring: `.env.example` carries the flag with a comment documenting "on for the one designated
     auto-resume machine, off elsewhere"; the designated machine sets `CRASH_AUTORESUME_ENABLED=1` in
     its `~/Desktop/Valor/.env`. This is a build-blocking decision: build proceeds with auto-apply as a
     first-class, shipped-on path, not a deferred toggle.

   This decision is now reflected as a binding Success Criterion (the end-to-end zero-human-action
   criterion below) and in **## Update System**.

2. **RESOLVED — Gating thresholds.** `MIN_OCCURRENCES`, `MIN_SUCCESS_RATIO`, per-session auto-resume
   cap, and global per-run budget remain named, configurable constants (per the "no specific numbers in
   prompts/specs" memory). An **absolute per-session auto-resume ceiling that config cannot exceed** is
   baked in as a hard backstop (a small constant the configurable cap is `min()`-clamped against), so a
   misconfiguration can never produce an unbounded resume loop.

3. **RESOLVED — Signature scope.** The library is **project-scoped** for v1 (per
   `feedback_test_redis_isolation`). A global cross-project rollup is explicitly deferred to a separate
   chore (noted under No-Gos); it is a small additive view that does not block this plan's value.
