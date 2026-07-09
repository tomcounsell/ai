---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1917
last_comment_id:
---

# Crash Auto-Resume Revival: Schedule the Reflection, Harden Classification, Add a Deterministic Floor

## Problem

The worker kills a session whose tool call wedges (e.g. a `gh` network stall), marks it `failed`, and sends the operator a "please try again" message. The operator's requirement is explicit: the system must self-heal. A session killed for a transient tool wedge should be re-run automatically, not parked behind a human apology.

Issue #1539 built exactly this layer — a crash-recovery reflection that fingerprints each crash, accumulates a signature library, and auto-resumes sessions whose crash pattern has a good recovery track record. It is structurally dead: it never runs, and even if it ran the posture blocks every early failure.

**Current behavior:** two live gaps (plus one gap that has been overtaken by a merged PR — see Freshness Check) leave auto-resume unreachable.

1. **The crash-recovery reflection is not scheduled.** It ships as a callable but registration was left as a manual vault edit that was never done. `python -m reflections --dry-run` does not list `crash-recovery`. `valor-session crash-signatures` reports an empty library after months of crashes.
2. **Policy posture blocks the first N failures.** `FEATURES__CRASH_AUTORESUME_ENABLED` defaults off, and the warm-up thresholds (min 3 occurrences, 0.7 success ratio) mean a cold library cannot act on the first several tool-wedge deaths. With no deterministic floor, the exact current failure mode stays in place during warm-up.

**Desired outcome:** a session finalized `failed` for a transient failure kind (a tool wedge with a confirmed-dead clean kill) is automatically resumed within one reflection cadence, subject to the existing per-session attempt cap, with no human in the loop. The signature library warms from real crashes so the statistical policy eventually takes over from the deterministic floor.

## Freshness Check

**Baseline commit:** `2fb1f8ef` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-06T06:04:56Z
**Disposition:** **Major drift** (one of three gaps overtaken by a merged PR; the other two remain fully live; core problem still real)

**File:line references re-verified:**
- `agent/crash_signature.py` `_extract_signature_inner` "turn_start check" — the guardrail returning `NON_RESUMABLE_DETERMINISTIC[no_turn_start]` on timelines without `turn_start` — **still present** (lines 207-219). Claim holds at the code level.
- `agent/session_stall_classifier.py` `_has_demonstrable_progress` — the ground-truth `turn_count` / `last_tool_use_at` pattern to port — **still present** (lines 189-217). Claim holds.
- `reflections/crash_recovery.py` `run_crash_recovery` — reads posture from `settings.features.*` at run time; escalates `NON_RESUMABLE_DETERMINISTIC`; gates on `is_auto_eligible` — **still present** (lines 65-421). Claim holds.
- `config/settings.py` crash auto-resume fields — `crash_autoresume_enabled` default `False`, warm-up thresholds `min_occurrences=3` / `min_success_ratio=0.7` — **still present** (lines 239-291). Claim holds.
- `config/reflections.yaml` — gitignored per-machine copy synced from the vault; `crash-recovery` absent; `python -m reflections --dry-run` on this machine loads 19 reflections, none crash-related. Gap 1 confirmed live.

**Cited sibling issues/PRs re-checked:**
- #1539 — CLOSED. Built the whole layer; registration deferred to a vault edit that never happened. Premise holds.
- #1724 — CLOSED (closed by #1930). Introduced `_has_demonstrable_progress`. Pattern still available to port.
- #1721 — OPEN at filing; **superseded by #1930** ("Supersedes #1721"). Lossless-checkpoint-resume is folded into the headless runner's four-scalar resume (`claude_session_uuid` + `dev_agent_id` + `runner_cwd` + `claude_version`).
- #1536 — CLOSED (epic). Context only.

**Commits on main since issue was filed (touching referenced files):**
- `e8351e4c` **"Granite PTY teardown: headless `claude -p` session runner cutover (#1930)"** — **CHANGED THE ROOT CAUSE of gap #2.** The PTY substrate is deleted. All session roles now run through `agent/session_runner/` (headless `claude -p`, one subprocess per turn). Crucially, `agent/session_runner/runner.py:808-812` emits a `turn_start` telemetry event at the start of every turn, with a comment naming this issue directly: *"turn_start telemetry unblocks the #1917 class: crash-signature extraction treats a trace with no turn_start as deterministically non-resumable — PTY sessions never emitted these events... Runner sessions emit them."* So the issue's gap #2 ("every granite PTY session classifies non-resumable because it never emits turn_start") no longer describes the current transport: a runner session that reaches turn 1 now has `turn_start` in its timeline and classifies resumable. Granite PTY sessions no longer exist.

**Active plans in `docs/plans/` overlapping this area:** `granite-pty-teardown.md` (shipped as #1930, status Complete) is the source of the drift. No open/active plan overlaps the crash-recovery layer.

**Notes — how the drift reshapes scope (revised premise):**
- **Gap #1 (unscheduled reflection): fully live, unchanged. This is the dominant blocker** — nothing extracts signatures or resumes because the reflection never runs. Primary fix.
- **Gap #2 (no `turn_start` → non-resumable): overtaken by #1930 for the current transport, but NOT dead as a hardening target.** Two residual cases keep the `_has_demonstrable_progress` port worthwhile as defense-in-depth: (a) a runner session killed while its `turn_start` telemetry write lagged or was lost (the exact failure class `_has_demonstrable_progress` was built to cover in the stall classifier), and (b) historical PTY-era session rows still in Redis with `turn_count > 0` and no `turn_start` that the warming library would otherwise stamp non-resumable. The issue's acceptance-criteria unit test (a session with `turn_count > 0` killed for a tool wedge extracts a resumable signature over a synthetic timeline with no `turn_start`) is still exactly right as a regression guard, and is satisfiable by the port. We keep this work, reframed from "structural death" to "classification hardening."
- **Gap #3 (posture blocks warm-up): fully live, unchanged.** Deterministic floor + machine-ownership posture are the second substantive piece.
- **Bottom line:** the issue's *headline mechanism* (granite PTY) is stale, but its *operator requirement* (self-heal a failed transient session) is unmet and every acceptance criterion remains valid and testable. Proceeding on the revised premise above rather than closing. Flagged for confirmation at critique.

## Prior Art

- **PR #1718 (#1539)**: "crash-signature library + auto-resume policy from session telemetry" — built the entire layer this plan revives: `reflections/crash_recovery.py`, `agent/crash_signature.py`, `models/crash_signature.py`, settings fields, docs. Complete and correct except registration + posture. This plan does not re-implement; it schedules and hardens.
- **PR #1722 (#1538)**: "Stalled-Session Advisory Classifier" — introduced `agent/session_stall_classifier.py` and, via #1724, `_has_demonstrable_progress`. This is the ground-truth pattern we port into the signature extractor.
- **PR #1930 (#1924)**: "Granite PTY teardown / headless session runner cutover" — deleted the PTY substrate and added `turn_start` telemetry emission in the new runner. Source of the Freshness Check drift; makes gap #2 a hardening target rather than a structural fix.
- No prior attempt tried to schedule the reflection or add a deterministic floor — those are net-new.

## Research

No relevant external findings — this is purely internal (reflection scheduling, telemetry classification, per-machine posture). Proceeding with codebase context.

## Data Flow

1. **Entry point — a session dies.** `agent/session_health.py` detects a tool wedge (`_check_tool_timeout`), kills the subprocess (`SubprocessKillResult(confirmed_dead, signal_sent)`), records a kill-enriched `status_transition` telemetry event (`running -> failed`, reason `"tool-wedge: Bash ..."`, `kill.confirmed_dead`, `kill.signal_sent`), and finalizes the session `failed`.
2. **Reflection tick (every 300s, once registered).** `agent/reflection_scheduler.py` reads the registry (`REFLECTIONS_YAML` env → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`) and dispatches `reflections.crash_recovery.run_crash_recovery`.
3. **Signature extraction.** The reflection reads `read_session_timeline(session_id)`, calls `extract_signature(events, session=session)` in `agent/crash_signature.py`. Currently: no `turn_start` → `NON_RESUMABLE_DETERMINISTIC[no_turn_start]` → escalate, never resume. After the port: session's own `turn_count`/`last_tool_use_at` prove progress → proceed to the resumable path.
4. **Policy gate.** `CrashSignature.is_auto_eligible(strategy="auto_resume", min_occurrences, min_success_ratio)`. Currently blocks a cold signature. After this plan: a deterministic-floor predicate (confirmed-dead clean kill to `failed`) permits one first retry ahead of statistical eligibility.
5. **Machine-ownership gate (new).** Auto-resume only proceeds if this machine owns the session's project (`projects.<project_key>.machine == computer_name()`), preserving the single-machine invariant.
6. **Resume.** `tools.valor_session.resume_session(session, "continue", source="auto-resume")` pushes a steering message and transitions the session `pending`; the worker's headless runner resumes it via `--resume` on the persisted `claude_session_uuid`. Attempt count on `AgentSession.auto_resume_attempts` bounds retries.
7. **Output — outcome attribution.** On the next terminal transition, the reflection's Phase-1 attribution records recovered/crashed_again into the library, warming the statistical policy.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1718 (#1539) | Built the crash-signature library + auto-resume reflection + settings + docs | Registration was documented as a manual vault edit ("Scheduling / Registration" section) and never performed; posture shipped propose-only with no deterministic floor, so a cold library never acts. |
| PR #1930 (#1924) | Added `turn_start` telemetry to the new runner (partial cover of gap #2) | Fixes the dominant classification mode but relies on the telemetry write landing before the kill; does not harden against lagged/lost `turn_start` writes or historical PTY rows. Does not touch gap #1 (scheduling) or gap #3 (posture). |

**Root cause pattern:** the layer was built but never *activated*. Every gap is an activation gap (registration, classification robustness, posture) rather than a missing capability. The fix set is small and additive.

## Architectural Impact

- **New dependencies:** none. Reuses `tools/machine_identity.py::computer_name()`, `config.settings`, existing reflection scheduler and CrashSignature model.
- **Interface changes:** `extract_signature` / `_extract_signature_inner` gain reliance on the passed `session` object's progress fields (already an accepted `session=` kwarg — no signature change). `CrashSignatureKey` may gain an additive `transient_kind: str | None` field (default `None`) — backward compatible.
- **Coupling:** the extractor gains a soft dependency on AgentSession progress fields, mirroring the stall classifier's existing pattern (read via `getattr`, fail-soft). No new import of the kill/recovery machinery.
- **Data ownership:** unchanged. Auto-resume decisions stay owned by the reflection; machine ownership stays owned by `projects.json`.
- **Reversibility:** high. Registration is a vault entry (`enabled: false` reverts). Deterministic floor is a settings field (set to 0 to disable). Posture is the existing env flag. The classifier port is fail-soft and additive.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the revised premise from Freshness Check; confirm posture resolution)
- Review rounds: 1

The code changes are small and additive; the alignment cost is confirming the drift-driven rescope and the three posture decisions.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Reflections registry resolvable | `python -c "from agent.reflection_scheduler import _resolve_registry_path; print(_resolve_registry_path())"` | Confirms which registry the update-flow assertion must edit |
| Settings importable | `python -c "from config.settings import settings; print(settings.features.crash_autoresume_enabled)"` | Confirms the posture surface exists |
| Machine identity available | `python -c "from tools.machine_identity import computer_name; print(computer_name())"` | Confirms ownership derivation for the posture gate |

## Solution

### Key Elements

- **Reflection registration (Gap 1):** an idempotent update-flow assertion that ensures a `crash-recovery` entry exists in the machine's vault `reflections.yaml`, replacing the manual operator step so registration survives vault resets.
- **Classification hardening (Gap 2, reframed):** port the `_has_demonstrable_progress` ground-truth pattern into `_extract_signature_inner` so a session with `turn_count > 0` (or a fresh `last_tool_use_at`) is not stamped `NON_RESUMABLE_DETERMINISTIC[no_turn_start]` when its telemetry lacks `turn_start`.
- **Deterministic floor + machine posture (Gap 3):** a first-retry floor for confirmed-dead clean-kill-to-`failed` signatures that bypasses statistical warm-up (bounded by the existing attempt cap and run budget), plus a per-project machine-ownership gate on auto mode so exactly one machine resumes a given session.
- **Docs truthful to the post-#1930 world:** `docs/features/crash-signature-auto-resume.md` describes actual registration, the ground-truth classification, and the chosen posture — no "previously broken" narration and no stale granite-PTY framing.

### Flow

Session dies (`failed`, tool wedge, confirmed-dead kill) → crash-recovery reflection tick → extract signature (progress-fields ground truth → resumable) → deterministic-floor predicate matches (confirmed-dead clean kill) → machine owns project + auto flag on → `resume_session("continue")` → worker resumes via `--resume` on stored UUID → outcome attributed next tick → library warms.

### Technical Approach

**Gap 1 — registration (Update System is the seam).** `config/reflections.yaml` is gitignored (the vault is source of truth, copied per-machine by `scripts/update/env_sync.py::sync_reflections_yaml`; `tools/reflection_machine_filter.py` gates project-scoped entries by ownership at update time). Add an idempotent assertion to the update flow (a new function invoked from `scripts/update/run.py`, or an extension of `scripts/update/reflections_yaml.py`) that: parses the vault `reflections.yaml`, and if no entry named `crash-recovery` exists, appends the documented entry (300s cadence, `execution_type: function`, `callable: reflections.crash_recovery.run_crash_recovery`, `enabled: true`, unscoped so every machine runs it in propose mode). Idempotent: a no-op when the entry is already present. Runs per-machine, editing that machine's vault copy, so it survives resets.

**Gap 2 — classifier hardening.** In `_extract_signature_inner`, before the `if not has_turn` early return, add a progress-fields probe mirroring `agent/session_stall_classifier.py::_has_demonstrable_progress`: read `getattr(session, "turn_count", None)` and `getattr(session, "last_tool_use_at", None)` (converted via `bridge.utc.to_unix_ts`), fail-soft. When the timeline lacks `turn_start` but the session's own fields prove progress, do NOT return `NON_RESUMABLE_DETERMINISTIC[no_turn_start]`; fall through to the normal resumable-signature path (terminal subsequence tokens + `_derive_signature_class`). Sessions with no `turn_start` AND no demonstrable progress keep the existing deterministic non-resumable classification (genuine never-started). Extract the shared helper or inline a fail-soft copy — do not import the stall/kill machinery into the extractor (the extractor must stay dependency-light).

**Gap 3a — deterministic floor.** Add a settings field `crash_autoresume_deterministic_floor_attempts: int = Field(default=1, ge=0, le=5, ...)` (`FEATURES__CRASH_AUTORESUME_DETERMINISTIC_FLOOR_ATTEMPTS`). Expose from the extractor whether the terminal `status_transition` was a **confirmed-dead clean kill to `failed`** (i.e. `kill.confirmed_dead == true` and `to == "failed"`) — the known-transient tool-wedge shape — as an additive `CrashSignatureKey.transient_kind` (`"tool_wedge"` or `None`). In `run_crash_recovery`, before the `is_auto_eligible` gate: if `sig.transient_kind` is set AND `settings.features.crash_autoresume_deterministic_floor_attempts > 0` AND the session's `auto_resume_attempts < deterministic_floor_attempts`, permit the resume path even when the signature is not yet statistically eligible. The floor is still bounded by `crash_autoresume_max_attempts` (per-session) and `crash_autoresume_run_budget` (per-run). Setting the field to 0 disables the floor and restores pure statistical gating.

**Gap 3b — machine posture (resolves open question 2).** Retain `FEATURES__CRASH_AUTORESUME_ENABLED` as the master auto-mode enable (still default off; the update/setup flow sets it in the designated worker machine's vault `.env`). Add a per-project ownership gate in the resume branch: resolve the session's `project_key` to its owner via `projects.json` (`projects.<key>.machine`) and compare to `computer_name()`; skip auto-resume (fall to propose) when this machine is not the owner. This makes the single-machine invariant structural rather than relying on the operator setting the flag on exactly one box, and scales to N machines. Unowned / unknown `project_key` → treat as not-owned (safe: propose only).

**Gap 3c — resume path (resolves open question 3).** Keep `resume_session` (the hard-PATCH path that preserves context via `claude_session_uuid`). #1930's four-scalar resume persists `claude_session_uuid` at stream-json init, and `resume_session` already requires a UUID and refuses (`"session was killed before first turn completed"`) when absent. Do not add a context-losing fresh re-enqueue: a session with no UUID cannot be safely resumed and should escalate, not restart blind. This behaves correctly for headless runner sessions (UUID-based `--resume`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/crash_signature.py` — the new progress-fields probe must be fail-soft (any exception → treat as no-progress, fall through to the existing `no_turn_start` deterministic path), mirroring `_has_demonstrable_progress`'s `except Exception: return False`. Add a test passing a session stub whose `turn_count` getattr raises, asserting the extractor returns the deterministic non-resumable key rather than propagating.
- [ ] `reflections/crash_recovery.py` — the ownership-gate resolution (reading `projects.json`) must fail-soft to not-owned (propose) on any lookup error. Test with an unresolvable `project_key`, asserting no resume and a propose finding.

### Empty/Invalid Input Handling
- [ ] Extractor with empty `events` list → still `unclassifiable` (unchanged). Test retained.
- [ ] Extractor with `session=None` and no `turn_start` → deterministic `no_turn_start` (no progress fields to consult). Test added.
- [ ] Deterministic-floor field set to 0 → floor disabled, statistical gating unchanged. Test asserts a cold transient signature is proposed, not resumed.

### Error State Rendering
- [ ] Reflection `summary` string reports `auto_resumed`/`proposed`/`escalated` counts truthfully after the floor and ownership gate; assert a floor-resume increments `auto_resumed` and a non-owner tick increments `proposed`.

## Test Impact

- [ ] `tests/unit/test_crash_signature.py` (or the existing signature-extractor test module) — UPDATE: existing `no_turn_start → NON_RESUMABLE_DETERMINISTIC` cases must now pass a session WITHOUT progress fields to keep asserting the deterministic path; add new cases for `turn_count > 0` → resumable and `transient_kind` detection.
- [ ] `tests/unit/test_crash_recovery*.py` / reflection tests — UPDATE: add deterministic-floor and machine-ownership-gate cases; existing propose-mode assertions stay valid when the floor field is 0 or the machine is not the owner.
- [ ] `tests/**` reflection-registry/dry-run tests, if any assert a fixed reflection count — UPDATE: account for `crash-recovery` when the vault entry is present (guard on the assertion machine).
- [ ] Any test asserting `crash_autoresume_*` settings defaults — UPDATE: add the new `crash_autoresume_deterministic_floor_attempts` default (1).

If a grep of `tests/` for `extract_signature`, `crash_recovery`, and `crash_autoresume` returns no existing coverage for a given branch, the builder adds new tests rather than updating — but the four modules above are the expected touch set.

## Rabbit Holes

- **Rewriting the whole granite/PTY narrative in every doc.** Scope the doc work to `docs/features/crash-signature-auto-resume.md` only. Do not sweep the repo for stale PTY references — #1930 already did that.
- **Adding a new `failure_kind` field to AgentSession.** Not needed — the confirmed-dead clean-kill shape is already in the terminal `status_transition` telemetry the extractor reads. Deriving `transient_kind` from telemetry avoids a Popoto migration.
- **Reusing `session-recovery-drip` to drip `failed` sessions.** Explicitly rejected in the issue's recon — it is scoped to circuit-recovery stampede control and would bypass the signature library and attempt caps.
- **Building lossless checkpoint resume.** That is #1721, superseded by #1930's four-scalar resume. This plan is about *whether* a dead session gets re-run, not resuming at the exact stopped point.
- **Making the deterministic floor pattern-match arbitrary failure kinds.** Restrict to the one confirmed-dead clean-kill-to-`failed` shape. Broadening the transient set is a follow-up once the library warms.

## Risks

### Risk 1: Deterministic floor causes a resume loop on a genuinely unrecoverable transient-shaped crash
**Impact:** A session that always wedges the same tool gets retried up to the floor+cap without ever succeeding, burning worker time.
**Mitigation:** The floor is bounded by `crash_autoresume_deterministic_floor_attempts` (default 1) AND the existing per-session `crash_autoresume_max_attempts` (default 3) AND the per-run `crash_autoresume_run_budget` (default 5). After the floor, statistical gating takes over and a low success ratio demotes the signature. Attempt count is persisted on `AgentSession.auto_resume_attempts` and re-read before each resume.

### Risk 2: Two machines both resume the same session (double-resume)
**Impact:** Duplicate work, competing harnesses at the same `claude_session_uuid`.
**Mitigation:** The per-project ownership gate (`projects.<key>.machine == computer_name()`) makes single-machine action structural, independent of the env flag. The existing pre-resume status re-read (`fresh_session.status not in RESUMABLE_STATUSES → skip`) closes the residual race.

### Risk 3: The classifier port false-positives an unstarted session as resumable
**Impact:** A never-started session gets a resumable signature and wastes a resume.
**Mitigation:** The port only overrides `no_turn_start` when the session's own fields prove progress (`turn_count > 0` or fresh `last_tool_use_at`). No progress fields → the deterministic non-resumable path is unchanged. This mirrors the stall classifier, which has run this exact predicate in production since #1724.

### Risk 4: The update-flow assertion corrupts or reorders the vault reflections.yaml
**Impact:** Reflection scheduling breaks fleet-wide.
**Mitigation:** Append-only, idempotent, no-op when present; validate by re-loading via `load_registry()` after the edit (same pattern as `migrate_reflections_yaml.py` phase 3). Fail-soft: on any parse error, log and leave the file untouched.

## Race Conditions

### Race 1: Signature extracted before the session's terminal transition is fully recorded
**Location:** `reflections/crash_recovery.py` Phase 2 (lines ~223-238)
**Trigger:** Reflection ticks while `finalize_session` is mid-write.
**Data prerequisite:** The terminal `status_transition` event must be in the timeline before extraction.
**State prerequisite:** Session status is terminal and stable.
**Mitigation:** Existing incomplete-retry guard: skip when `not _has_terminal_status_transition(events)` or the signature is `unclassifiable`; retry next tick. Unchanged by this plan.

### Race 2: Auto-resume races the worker's own recovery mechanisms
**Location:** `reflections/crash_recovery.py` resume branch (lines ~345-364)
**Trigger:** A recovery path transitions the session between eligibility check and resume.
**Data prerequisite:** Session still in `RESUMABLE_STATUSES` at resume time.
**State prerequisite:** No other process has already re-enqueued it.
**Mitigation:** Existing re-read (`AgentSession.query.filter(session_id=...)`, pick newest, skip if not resumable) before `resume_session`. The new ownership gate runs before this and further narrows the actor set. Unchanged mechanism.

### Race 3: `turn_start` telemetry write lags the kill (the reframed gap 2)
**Location:** `agent/crash_signature.py` `_extract_signature_inner`
**Trigger:** A runner session is killed after `turn_count` increments but before/without its `turn_start` event landing in the timeline.
**Data prerequisite:** The AgentSession's `turn_count`/`last_tool_use_at` reflect real progress.
**State prerequisite:** N/A — read-only classification.
**Mitigation:** The progress-fields probe consults the session's own fields as ground truth, exactly to survive telemetry lag/loss.

## No-Gos (Out of Scope)

- [EXTERNAL] Setting `FEATURES__CRASH_AUTORESUME_ENABLED=1` in the designated worker machine's vault `.env` — this is a per-machine secret edit the agent cannot perform on the target box; the update/setup flow asserts the reflection registration, but flipping auto mode on is a human `.env` action on the owning machine. The ownership gate ensures leaving it off elsewhere is safe.
- [ORDERED] Fleet-wide activation of auto mode — must follow this PR's merge and a `/do-deploy` so every machine has the ownership gate before any machine acts; enabling auto mode before the gate ships risks double-resume.
- Broadening the deterministic-floor transient set beyond confirmed-dead clean-kill-to-`failed` — deliberately out of scope until the library warms; revisit as a data-driven follow-up, not a speculative expansion now.

## Update System

**Changes required.** This is the primary fix for Gap 1.

- Add an idempotent "ensure `crash-recovery` registered" assertion to the update flow (new function invoked from `scripts/update/run.py`, or an extension of `scripts/update/reflections_yaml.py`). It appends the `crash-recovery` entry to the machine's vault `reflections.yaml` when absent, validates by re-loading the registry, and no-ops when present. Replaces the manual operator step the design doc documented.
- The entry is **unscoped** (`enabled: true`, no `project_key`) so every machine runs it in propose mode; auto mode is gated separately by the env flag + ownership check, so `tools/reflection_machine_filter.py` needs no change.
- New settings field `crash_autoresume_deterministic_floor_attempts` propagates via the existing pydantic settings surface; add a placeholder line to `.env.example` with the required comment above it (`FEATURES__CRASH_AUTORESUME_DETERMINISTIC_FLOOR_ATTEMPTS`). No secret.
- No Popoto model change → no `scripts/update/migrations.py` entry. (Confirmed: `transient_kind` is a dataclass field on `CrashSignatureKey`, not a persisted model; the floor reuses `AgentSession.auto_resume_attempts`.)

## Agent Integration

No agent integration required — this is a worker/reflection-internal change. The reflection is dispatched by `agent/reflection_scheduler.py`, not invoked by the agent's tool surface. `valor-session crash-signatures` / `crash-policy list` (existing CLIs) surface the warming library for humans; no new MCP tool or `.mcp.json` change. The bridge imports nothing new.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/crash-signature-auto-resume.md` to describe: (a) the update-flow registration assertion (replacing the manual "Scheduling / Registration" step), (b) the progress-fields ground-truth classification (replacing the PTY-specific `no_turn_start` narration), (c) the deterministic first-retry floor and its settings field, and (d) the machine-ownership posture. Remove any "previously broken" / granite-PTY framing — describe the current headless-runner behavior in the present tense.
- [ ] Verify `docs/features/README.md` index entry for crash-signature-auto-resume is accurate (update the one-line description if the posture changed).

### External Documentation Site
- [ ] Not applicable — no external docs site.

### Inline Documentation
- [ ] Docstring on the new progress-fields probe in `agent/crash_signature.py` (cite #1724's `_has_demonstrable_progress` as the ported pattern).
- [ ] Update the `_extract_signature_inner` "Determinism guardrail" docstring block to reflect the new ground-truth override.
- [ ] Comment the deterministic-floor and ownership-gate branches in `reflections/crash_recovery.py`.

## Success Criteria

- [ ] `python -m reflections --dry-run` lists `crash-recovery` as a loaded reflection on a machine whose vault registry has been asserted by the update flow.
- [ ] A session with `turn_count > 0` killed for a tool wedge extracts a resumable crash signature (not `NON_RESUMABLE_DETERMINISTIC[no_turn_start]`) over a synthetic timeline with no `turn_start` plus session progress fields — unit test.
- [ ] A session finalized `failed` with a confirmed-dead clean kill to `failed` is auto-resumed within one reflection cadence under the deterministic floor (with the floor field > 0, auto flag on, and this machine owning the project), respecting `crash_autoresume_max_attempts` — integration test.
- [ ] With the deterministic-floor field set to 0, a cold transient signature is proposed (not resumed) — statistical-gating regression test.
- [ ] A non-owning machine proposes (does not resume) a resume-eligible session — ownership-gate test.
- [ ] `valor-session crash-signatures` shows records accumulating from real terminal sessions after registration (manual/soak verification noted in the PR).
- [ ] `docs/features/crash-signature-auto-resume.md` describes actual post-fix behavior with no "previously broken" or granite-PTY narration.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (classifier)**
  - Name: `classifier-builder`
  - Role: Port the progress-fields ground-truth probe into `agent/crash_signature.py`; add `transient_kind` detection.
  - Agent Type: builder
  - Domain: async/telemetry (fail-soft classification)
  - Resume: true

- **Builder (reflection + posture)**
  - Name: `reflection-builder`
  - Role: Deterministic floor, machine-ownership gate, settings field in `reflections/crash_recovery.py` + `config/settings.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (update-system)**
  - Name: `update-builder`
  - Role: Idempotent reflection-registration assertion in the update flow + `.env.example` line.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `test-eng`
  - Role: Unit + integration tests per Test Impact and Failure Path Test Strategy.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Rewrite `docs/features/crash-signature-auto-resume.md` truthful to the post-#1930 world.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `final-validator`
  - Role: Verify every Success Criterion and Verification row.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden the signature classifier
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: `tests/unit/test_crash_signature*.py`
- **Informed By**: Freshness Check (gap 2 reframed), `_has_demonstrable_progress` pattern
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a fail-soft progress-fields probe to `_extract_signature_inner`; override `no_turn_start` only when `turn_count > 0` or fresh `last_tool_use_at`.
- Add additive `CrashSignatureKey.transient_kind` set to `"tool_wedge"` on a confirmed-dead clean kill to `failed`; `None` otherwise.
- Update guardrail docstring; do not import stall/kill machinery.

### 2. Deterministic floor + machine posture in the reflection
- **Task ID**: build-reflection
- **Depends On**: build-classifier (uses `transient_kind`)
- **Validates**: reflection/crash_recovery tests, settings default test
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `crash_autoresume_deterministic_floor_attempts` settings field (default 1).
- Insert the deterministic-floor branch before `is_auto_eligible`; bound by attempt cap + run budget.
- Add the per-project ownership gate (`computer_name()` vs `projects.json`), fail-soft to propose.
- Keep `resume_session` (hard-PATCH) as the resume path.

### 3. Reflection registration assertion (update system)
- **Task ID**: build-update
- **Depends On**: none
- **Validates**: update-flow tests / manual `python -m reflections --dry-run`
- **Assigned To**: update-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the idempotent "ensure crash-recovery registered" assertion; validate by re-loading the registry; no-op when present.
- Add the `.env.example` placeholder line (with comment) for the new settings field.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-classifier, build-reflection, build-update
- **Assigned To**: test-eng
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: classifier ground-truth override, `transient_kind`, fail-soft, empty/None inputs.
- Integration: floor-resume respecting caps; floor=0 propose; non-owner propose.
- Update any registry-count assertions.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-classifier, build-reflection, build-update
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite `docs/features/crash-signature-auto-resume.md` (registration, ground-truth classification, floor, posture); present tense, no "previously broken".
- Verify the `docs/features/README.md` index entry.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every Success Criterion; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k "crash"` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Classifier port present | `grep -c "turn_count" agent/crash_signature.py` | output > 0 |
| Deterministic floor field present | `python -c "from config.settings import settings; print(settings.features.crash_autoresume_deterministic_floor_attempts)"` | output contains 1 |
| Ownership gate present | `grep -c "computer_name" reflections/crash_recovery.py` | output > 0 |
| Registration assertion wired | `grep -rc "crash-recovery" scripts/update/` | output > 0 |
| No stale "previously broken" narration | `grep -ci "previously.*broken\|structurally dead\|granite pty" docs/features/crash-signature-auto-resume.md` | match count == 0 |

## Critique Results

**Verdict: READY TO BUILD (with concerns)** — FULL war room (Risk & Robustness, Scope & Value, History & Consistency). 0 blockers, 6 concerns, 1 nit. Concerns are embeddable refinements, not re-planning items; a revision pass folds the Implementation Notes below into the plan text before build.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | `auto_resume_attempts` is only advanced inside `if result.success:`; the deterministic floor fires the resume branch on the FIRST occurrence, so a persistently-failing `resume_session` (e.g. missing UUID → refuses; `transition_status` raises) re-satisfies `attempt_count < floor` (`0 < 1`) every 300s tick forever. Risk 1 claims boundedness the failure path does not deliver. | build-reflection | In the `else:` after `if result.success:` (`reflections/crash_recovery.py` ~384-390) add `fresh_session.auto_resume_attempts = str(attempt_count + 1); fresh_session.save()`, mirroring lines 370-371, so a failed resume still consumes an attempt and converges to the `attempt_count >= max_auto_attempts` guard at line 334. |
| CONCERN | Risk & Robustness | The ported `last_tool_use_at` freshness check (`now - ts < IDLE_SUSPECT_SECS`) is wall-clock-relative; `_extract_signature_inner` runs over already-terminal sessions inside a 2h-lookback reflection, so "now" is minutes-to-hours after death and the check reads stale/False. `turn_count` only increments on turn completion, so a mid-first-turn wedge has `turn_count == 0` — leaving the broken freshness check as the sole signal for the exact residual case (lagged/lost `turn_start`) the port cites. That path ships dead and untested (the AC test only exercises `turn_count > 0`). | build-classifier, build-tests | In the new probe, replace `(time.time() - ts) < IDLE_SUSPECT_SECS` with `ts is not None` (no wall-clock window) or compare against the terminal `status_transition` event's own timestamp. Add a unit test with `turn_count == 0` and a non-fresh `last_tool_use_at`. |
| CONCERN | Scope & Value | Gap-2 justification case (b) "historical PTY-era rows still in Redis" is unreachable: `run_crash_recovery` gates on `updated_at > now - CRASH_AUTORESUME_LOOKBACK_HOURS` (default 2.0h, filter ~lines 131-160/215-220), so months-old rows never reach `extract_signature`. The dual-justification for keeping the port is half wrong. | document-feature, build-reflection | Drop case (b) and rely on case (a) alone, OR scope an explicit one-time backfill sweep that bypasses the lookback. A test for "historical row reclassified" would have to bypass `run_crash_recovery`'s lookback entirely — a sign the scenario does not flow through the real path. |
| CONCERN | Scope & Value | `CrashSignatureKey` gains a `transient_kind` field solely so the reflection's floor branch can know the terminal transition was a confirmed-dead clean kill to `failed` — but `run_crash_recovery` already holds the raw `events` list at that point. Widening the shared extractor dataclass (also consumed by `get_or_create_by_hash` and others) for one caller's predicate is unnecessary coupling. | build-classifier, build-reflection | Derive the confirmed-dead-clean-kill-to-`failed` shape inline in `reflections/crash_recovery.py` from the last `status_transition` in `events` (already in scope at the floor branch ~line 303), reusing the `kill.confirmed_dead` / `to` read shown in `agent/crash_signature.py:90-99` (`_normalize_status_transition`). Do not thread `transient_kind` through the dataclass. |
| CONCERN | Scope & Value | Post-merge and even post-`/do-deploy`, `FEATURES__CRASH_AUTORESUME_ENABLED` stays off everywhere until a human edits the owning machine's vault `.env` ([EXTERNAL] No-Go), with no committed timeline or tracked follow-up — so the operator's "auto-heal, no human in the loop" requirement is unmet on day one and no Success Criterion verifies activation. This reproduces #1539's "built but never activated" shape that Gap 1 exists to prevent. | Success Criteria / follow-up | Add an owned follow-up (tracked issue linked in the PR, or explicit Success Criterion) requiring a human to set the flag on the designated machine within N days of `/do-deploy`, plus a post-activation `valor-session crash-policy list` check confirming propose-mode findings convert to auto-resumes. |
| CONCERN | History & Consistency | Registration location is left as "a new function in `scripts/update/run.py`, OR an extension of `scripts/update/reflections_yaml.py`" while the assertion is said to "parse the vault `reflections.yaml`". But `reflections_yaml.py` operates on `config/reflections.yaml` (the per-machine copy); a builder following that module's convention writes to the config copy, which `_resolve_registry_path` deprioritizes below the vault. `grep crash-recovery scripts/update/` passes either way, so this silently reproduces #1539's "looks wired, never lands" failure. (Verified: `_resolve_registry_path()` resolves the vault on this machine.) | build-update | Pin the assertion to write the vault by calling `_resolve_registry_path()`'s vault candidate directly (not a hardcoded path, not the config copy); add a unit test asserting the entry lands in the vault file specifically; make the Verification row grep the vault file, not `scripts/update/` source. |
| CONCERN | History & Consistency | The resume path (`resume_session` → runner `--resume` on `claude_session_uuid`) is exactly the path patched by the most recent main commit `662d5b50` (#1980/#1985, "preserve valid completion when resumed turn exits non-zero after a result event"). Auto-resume drives `--resume` far more often, specifically against sessions that just died mid-tool-call (the conditions that produce partial/no result events), yet neither Prior Art nor Risks/Race Conditions mentions it. | build-tests / Prior Art | Add #1980/#1985 to Prior Art; add a failure-path test exercising a floor-triggered `resume_session` whose `--resume` subprocess exits non-zero after a fired result event, reusing the branch fixtures in `tests/unit/test_harness_stale_uuid_result_preservation.py`, asserting Phase-1 attribution records the correct outcome. |
| NIT | History & Consistency | The plan does not state whether the registration assertion runs before or after Step 1.66's vault→config copy; if after, machines relying on the `config/reflections.yaml` fallback won't see the entry until the next update cycle. | build-update | State explicitly that the assertion runs before Step 1.66's copy (or triggers a re-copy afterward). |

**Cross-validation note:** the two gap-2 findings (Risk & Robustness on the dead `last_tool_use_at` fallback + `turn_count==0`, and Scope & Value on the unreachable historical-rows case) converge: together they undermine BOTH residual justifications for keeping the classifier port. Not elevated — the port retains independent value as the acceptance-criteria regression guard (`turn_count > 0 → resumable`), and both fixes are embeddable. The "One confirmation needed from the supervisor" (Freshness Check rescope of gap 2) is confirmed reasonable: proceed on the revised premise, trimming the justification per the two findings.

---

## Open Questions

The three questions the issue left to the planner are **resolved in-plan** (see Technical Approach); recorded here so critique can challenge the resolutions:

1. **Deterministic first-retry floor?** RESOLVED: yes. A settings-gated (`crash_autoresume_deterministic_floor_attempts`, default 1) first retry for confirmed-dead clean-kill-to-`failed` signatures, bypassing statistical warm-up, bounded by the existing per-session cap and per-run budget. Set to 0 to disable.
2. **Which machine gets `FEATURES__CRASH_AUTORESUME_ENABLED=1`?** RESOLVED: derive eligibility from per-project machine ownership (`projects.<key>.machine == computer_name()`) as a structural gate, with the env flag retained as the master enable and set on the designated worker machine's vault `.env` by a human (No-Go [EXTERNAL]).
3. **Fresh run vs `resume_session` hard-PATCH?** RESOLVED: keep `resume_session` (preserves context via `claude_session_uuid`; behaves correctly for headless runner sessions). No context-losing fresh re-enqueue; UUID-less sessions escalate rather than restart blind.

**One confirmation needed from the supervisor (not a scoping unknown):** the Freshness Check flags **Major drift** — #1930 deleted the granite PTY substrate and the new runner emits `turn_start`, so the issue's headline gap #2 is overtaken. This plan proceeds on the revised premise (gap #1 scheduling + gap #3 posture are the substantive work; gap #2 becomes classification hardening / regression guard). Confirm this rescope rather than closing the issue.
