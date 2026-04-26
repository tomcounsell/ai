---
status: Building
type: bug
appetite: Large
owner: Valor
created: 2026-04-26
tracking: https://github.com/tomcounsell/ai/issues/1172
last_comment_id:
---

# PM Session Liveness — See Progress or Stay Graceful

## Problem

`agent/session_health.py` infers whether a session is doing useful work from staleness signals (`last_stdout_at`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, psutil probes, Tier 1/Tier 2 reprieve gates). Each is a timestamp of a past observation — the detector never knows live state. Wall-clock deadlines (`FIRST_STDOUT_DEADLINE`, `STDOUT_FRESHNESS_WINDOW`, the per-session timeout cap) tune against variables that don't actually correlate with real progress. A false-kill loses real work; a 30-minute false-positive on a stuck session costs almost nothing (cost monitoring is the long-run backstop).

Concrete operational symptom: PM session output across the per-project Telegram groups (`PM: Valor`, `PM: PsyOptimal`, etc.) falls into spam mode (frequent short status updates), silent mode (≥1 hour quiet despite working), or — rarely — Goldilocks mode (one mid-work check-in, then "done"). The CEO can't tell if PM is alive; the system can't either.

**Current behavior:**
- `_has_progress()` returns False on stdout-stale (`>600s` since last stdout) even when both heartbeats are fresh — fires Tier 2 reprieve evaluation that may or may not save the session.
- `FIRST_STDOUT_DEADLINE = 300s` flags any session that hasn't produced stdout in 5 min, regardless of prompt size or warmup expectations.
- Per-session timeout (`_get_agent_session_timeout`) bypasses Tier 2 reprieves entirely (lines 826–831): a working session with active children IS killed at the wall-clock cap.
- PM sessions emit no self-report; their work is invisible until the final delivery message — or the harness times out and there's no message at all.
- Dashboard exposes `turn_count`, `tool_call_count`, and `watchdog_unhealthy` but no in-flight tool name, no recent thinking excerpt, no last-turn-boundary timestamp. The agent's own state signals are aggregated into one timestamp and discarded.

**Desired outcome:**
- The detector kills only on **evidence** of failure (dead subprocess via psutil, OOM exit, response already delivered, auth/credential error). It does NOT kill on **inference** from absence of expected activity.
- PM sessions emit at most one short self-report mid-work via `valor-telegram send`, then one final delivery — Goldilocks mode by default.
- The dashboard shows the agent's own state (current tool, last tool-use timestamp, last-turn-boundary timestamp, recent thinking excerpt) so operators can see what's happening without inferring from staleness.
- Cost/spend monitoring documented as the long-run backstop; no new wall-clock deadlines anywhere.

## Freshness Check

**Baseline commit:** `8f7cf6c6` (Delete migrated plan: design-md-integration-phase-1)
**Issue filed at:** 2026-04-25T16:05:04Z (≈18 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:140` (`STDOUT_FRESHNESS_WINDOW = 600`) — still holds.
- `agent/session_health.py:156` (`FIRST_STDOUT_DEADLINE = 300`) — still holds.
- `agent/session_health.py:461-566` (`_has_progress()`) — still holds.
- `agent/session_health.py:569-651` (`_tier2_reprieve_signal()`) — still holds.
- `agent/session_health.py:766` (`_get_agent_session_timeout(entry)`) — still holds.
- `agent/session_health.py:1017-1036` (Mode 4 OOM defer) — still holds.
- `models/agent_session.py:253-278` (heartbeat / stdout / compaction fields) — still holds. `current_tool_name` / `last_tool_use_at` / `last_turn_at` / `recent_thinking_excerpt` are absent (net-new fields needed).
- `ui/app.py:244-291` (`_session_to_json`) — still holds; renders no in-flight tool/thinking signal.
- `agent/sustainability.py:604-621` (`_send_telegram(message)` helper using `subprocess.run(["valor-telegram", "send", ...])`) — still holds; canonical pattern for harness-side sends.
- `agent/hooks/pre_tool_use.py:387` (`tool_name` / `tool_input` capture) — still holds; insertion point for `current_tool_name` writes.

**Cited sibling issues/PRs re-checked:**
- #1036 — CLOSED 2026-04-18; heartbeat-as-progress contract holds. **Must not regress.**
- #1046 — CLOSED 2026-04-18; this plan explicitly retires the stdout-stale Tier 1 extension introduced by #1046 while preserving #1036's dual-heartbeat OR check.
- #944 — CLOSED 2026-04-14; slugless dev-session sharing must not regress. The own-progress signals (`turn_count`, `log_path`, `claude_session_uuid`, child-progress) remain primary for slugless dev sessions.
- #1099 — CLOSED 2026-04-22; Mode 4 OOM defer at session_health.py:1017-1036 is evidence-based and stays intact.
- #918 — delivery guard at session_health.py:798-822 finalizes already-delivered sessions instead of recovering. Evidence-based; stays.
- #1159 — CLOSED 2026-04-25 (superseded). Tweaks 3 + 4 already shipped via `b39ba285`. Tweaks 1 + 2 retracted.

**Commits on main since issue was filed (touching referenced files):**
- None. The latest hotfix `b39ba285` (#1159) only touched log-line wording and the user_prompt_submit hook; no kill paths or dashboard surfaces moved.

**Active plans in `docs/plans/` overlapping this area:** None. `progress-detector-tweaks.md` (the #1159 plan) was migrated and removed; no live overlap.

**Notes:** All cited file:line references are unchanged. The plan's premises are valid against `main @ 8f7cf6c6`.

## Prior Art

- **Issue #1036**: 300s no-progress guard kills sessions before first turn despite live SDK heartbeat — established the dual-heartbeat OR check; this plan preserves that contract verbatim.
- **Issue #1046**: Promote `last_stdout_at` to tier-1 kill signal (catches alive-but-silent Claude) — added the stdout-stale Tier 1 extension. **This plan retires that path.** The premise — that stdout silence indicates failure — is precisely the inference the new issue rejects.
- **Issue #944**: Health check skips recovery for stuck dev sessions when a shared project-keyed worker is alive — added the `_has_progress()` own-progress field check. This plan keeps the own-progress fields (`turn_count`, `log_path`, `claude_session_uuid`, child-progress) since they are direct evidence of work, not staleness inference.
- **Issue #1099**: Harness failure hardening for four known modes — added Mode 4 OOM defer (evidence-based: real `returncode == -9`). This plan keeps Mode 4 intact.
- **Issue #918**: Delivery guard — finalizes sessions whose response was already delivered. Evidence-based (`response_delivered_at` is set by actual delivery, not inferred). Stays.
- **Issue #1058 / #1129**: PM final-delivery protocol at `agent/session_completion.py` — establishes the canonical PM "compose final summary then deliver" path. The self-report introduced by this plan is a NEW pre-completion message that is distinct from the final delivery, and must not interfere with it.
- **Issue #1159**: Progress-detector-tweaks (superseded). Tweaks 3 + 4 shipped as `b39ba285`. Tweaks 1 + 2 retracted as architecturally misguided — wall-clock kills on staleness inference, length-scaling against truncated `message_text`. The proper rethink is this issue.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1046 | Promoted `last_stdout_at` to Tier 1 kill signal so a "claude -p" emitting heartbeats but silent on stdout for ≥10 min would be flagged | The premise that stdout silence indicates failure is wrong. Long-thinking turns, large tool outputs being processed off-stream, and slow first-result tail-latencies all produce legitimate stdout silence. The fix optimized for one observed failure shape ("alive-but-silent") while introducing false-kill risk on healthy long-running PM work. Tier 2 reprieves partially mask this, but the architecture ("infer failure from absence") is the bug. |
| #1159 (Tweaks 1+2) | Tweak 1: combined-signal no-result deadline at 60 min. Tweak 2: prompt-length-scaled first-result deadline. | Both are wall-clock kills on inference. Tweak 1 layered another inference on top of the existing two; Tweak 2 used `message_text` length as a proxy for warmup tolerance, but `message_text` is truncated for storage and doesn't represent the full system-prompt + persona payload that actually drives latency. Retracted before shipping. |

**Root cause pattern:** The detector keeps trying to *infer* live state from past timestamps. Each new tweak adds another inference layer to the stack; none replace the asymmetric error model where false-kills (lose real work) are treated symmetrically with false-positives-on-stuck (cost almost nothing — cost monitoring catches the runaway case). The architecture this plan adopts: drop inference paths entirely, expand evidence collection (Pillar A), keep evidence-based kills only (Pillar B).

## Architectural Impact

- **New dependencies**: None. `valor-telegram` CLI is already installed; `subprocess.run(...)` is already used by `agent/sustainability.py`. PreToolUse / PostToolUse hooks already exist.
- **Interface changes**:
  - `models.AgentSession` gains four new fields: `current_tool_name: str | None`, `last_tool_use_at: datetime | None`, `last_turn_at: datetime | None`, `recent_thinking_excerpt: str | None` (nullable, default None — additive only).
  - `_has_progress()` simplified: heartbeat OR + own-progress fields + child-progress only. Removes `STDOUT_FRESHNESS_WINDOW` and `FIRST_STDOUT_DEADLINE` paths.
  - `_get_agent_session_timeout()` and the timeout recovery branch deleted (no per-session wall-clock cap).
  - `_tier2_reprieve_signal()` retained (psutil-based gates are evidence-based) but no longer invoked from inference paths since those paths are gone.
  - Dashboard `_session_to_json()` adds the four new fields and a derived `last_evidence_at` (max of heartbeat / tool / turn / stdout / compaction).
- **Coupling**: Pillar A increases coupling between hooks and the model (PreToolUse/PostToolUse now write to `AgentSession`), but the hook surface already writes other fields (`compaction_count`, etc.). No new abstractions.
- **Data ownership**: PreToolUse/PostToolUse hooks own `current_tool_name` / `last_tool_use_at` / `last_turn_at`. The SDK client owns `recent_thinking_excerpt` (extracted from extended-thinking deltas). Dashboard reads only.
- **Reversibility**: High. The plan deletes inference code; reverting would re-introduce dead code. The new fields are nullable and additive; reverting drops them with no migration concern. The self-report is a single subprocess call gated by a state flag; reverting is a one-line removal.

## Appetite

**Size:** Large (architectural change touching detector, model, hooks, dashboard, PM completion protocol)

**Team:** Solo dev with subagent delegation for distinct components (detector cleanup, hook plumbing, dashboard rendering, self-report integration, tests).

**Interactions:**
- PM check-ins: 2-3 (scope alignment per phase, mid-build sanity check, pre-merge review)
- Review rounds: 2+ (one per phase PR; war-room critique recommended on Phase 1)

**Phasing recommendation:** **Two PRs.** Phase 1 leads with Pillar B + self-report (subtractive + behavioral); Phase 2 follows with Pillar A (additive instrumentation). See **Step by Step Tasks** for the full split. Single PR rejected because: Pillar B is a deletion-driven cut over an established detector contract — must land cleanly with regression tests before Pillar A's new fields create new surfaces to defend.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `valor-telegram` CLI on PATH | `which valor-telegram` | Self-report channel |
| `psutil` installed | `python -c "import psutil; print(psutil.__version__)"` | Tier 2 reprieve gates (existing dependency) |
| Worker + bridge running for integration tests | `./scripts/valor-service.sh status` | E2E PM self-report verification |

Run all checks: `python scripts/check_prerequisites.py docs/plans/pm-session-liveness.md`

## Solution

### Key Elements

- **Pillar B — Graceful detector** (Phase 1): `_has_progress()` reduced to heartbeat OR + own-progress fields + child-progress. `STDOUT_FRESHNESS_WINDOW`, `FIRST_STDOUT_DEADLINE`, and the per-session timeout branch deleted. `_tier2_reprieve_signal()` retained (psutil-based) but invoked only from the heartbeat-stale recovery path, never from stdout/timeout inference paths (those paths cease to exist).
- **Self-report behavior** (Phase 1): PM session emits exactly one short status message via `valor-telegram send` mid-work. Triggered by the agent (not the detector) when (a) the PM has spawned ≥1 dev sub-session AND (b) no self-report has been sent yet on this session AND (c) the parent PM is not yet in the final-delivery turn. Content: 1-2 sentences. Channel: `PM: {project_name}` chat. Frequency cap: exactly 1 per session lifetime.
- **Pillar A — In-flight visibility** (Phase 2): Four new `AgentSession` fields. `current_tool_name` written by PreToolUse, cleared by PostToolUse. `last_tool_use_at` and `last_turn_at` bumped at tool boundaries. `recent_thinking_excerpt` (last 280 chars of extended-thinking content) updated by the SDK client's stream-event handler. Dashboard renders the four fields and a derived `last_evidence_at`.
- **Cost backstop documentation** (Phase 1): `docs/features/pm-session-liveness.md` documents that wall-clock kills are intentionally absent and runaway spend is bounded by the existing token/cost accounting (`AgentSession.total_cost_usd`, surfaced on the dashboard). No new code; just docs.

### Flow

**Phase 1 — Self-report (PM session perspective):**
PM session running a build → spawns Dev session → PM continues turning → PM detects "I have a Dev child but haven't told the chat yet" → PM invokes `subprocess.run(["valor-telegram", "send", "--chat", chat, "Working on X — Dev session running."])` → PM continues with no further status messages until final delivery → final delivery via existing `agent/session_completion.py` path.

**Phase 1 — Detector (operator perspective):**
Health-check tick → for each running session, check own-progress fields and heartbeat freshness → if both heartbeats fresh OR own-progress evident → continue (no kill). If both heartbeats stale → evaluate Tier 2 reprieve (psutil + compaction + recent stdout) → reprieve signal present → continue. → All evidence absent (heartbeats stale, no own-progress, no Tier 2) → recover. The `worker_dead`, OOM-defer, and `response_delivered_at` paths remain unchanged.

**Phase 2 — Pillar A (operator perspective):**
Operator opens dashboard → session card now shows: current tool ("Read", "Bash", etc.), last tool use 4s ago, last turn boundary 12s ago, recent thinking excerpt ("...checking the file structure before proposing a refactor..."), last evidence age (max of all). Operator can read what's happening live; no inference required.

### Technical Approach

**Phase 1 — Pillar B + self-report**

1. **Delete inference paths in `agent/session_health.py`:**
   - Remove the stdout-stale Tier 1 extension at lines 535-550 (the `if any_heartbeat_fresh:` clause's body keeps only the `return True` — the inner stdout/first-stdout checks are deleted).
   - Remove `STDOUT_FRESHNESS_WINDOW` and `FIRST_STDOUT_DEADLINE` module-level constants and their `os.environ.get(...)` reads (lines 139-156).
   - Remove the timeout recovery branch at lines 765-769; remove `_get_agent_session_timeout()` import and helper (audit for other call sites first — if it's only used here, delete it).
   - Remove `_reason_kind = "timeout"` classification at lines 781-782 (the branch ceases to exist).
   - Remove `STDOUT_FRESHNESS_WINDOW` references in `_tier2_reprieve_signal()` gate (e) — the gate fires when `last_stdout_at` is recent; without `STDOUT_FRESHNESS_WINDOW`, replace with a 600s literal local constant or delete the gate (the psutil "alive"/"children" gates already cover the active-subprocess case).
   - Remove `_last_progress_reason` references in the `tier1_flagged_stdout_stale` counter increment at lines 850-861 (the reason kind disappears).
   - Update log lines that mention "no progress signal observed" to drop the implication that this includes stdout/timeout staleness.

2. **Preserve evidence-based paths:**
   - `_has_progress()` keeps: dual-heartbeat OR check (lines 522-532), own-progress fields (`turn_count`, `log_path`, `claude_session_uuid` at lines 553-559), child-progress check (lines 560-565).
   - `_tier2_reprieve_signal()` keeps: `compacting`, `alive`, `children` gates. The `stdout` gate is dropped (consistent with removing `STDOUT_FRESHNESS_WINDOW`).
   - `worker_dead` recovery path unchanged.
   - `response_delivered_at` finalize-instead-of-recover guard (#918) unchanged.
   - Mode 4 OOM defer (#1099) unchanged.
   - Startup recovery (`_recover_interrupted_agent_sessions_startup`) unchanged.

3. **Add self-report to PM completion protocol:**
   - In `agent/session_completion.py`, add `_emit_pm_self_report(parent: AgentSession)` invoked from PM's first dev-session completion handler (`_handle_dev_session_completion`). Trigger condition: `parent.session_type == "pm"` AND `parent.self_report_sent_at is None` AND parent has at least one Dev child AND parent is not yet in final-delivery turn.
   - Add new `AgentSession` field `self_report_sent_at: datetime | None` (default None). Written by `_emit_pm_self_report` after successful `valor-telegram send`. Used as the frequency-cap state.
   - Self-report content: 1-2 sentence templated string composed from `parent.message_text[:200]` + active dev-child slug. Example: `"Working on issue #1172 — Dev session pm-session-liveness running."` Templated, not LLM-generated, to avoid drifting into spam-mode.
   - Channel: `PM: {parent.project_name}` resolved via existing `valor-telegram send --chat ...` pattern from `agent/sustainability.py:604-621`. Reuse the pattern verbatim; do not refactor.
   - Failure handling: subprocess failure logs at WARNING and proceeds; the field `self_report_sent_at` is set only on `returncode == 0`. Self-report failure must NOT block PM progress.
   - Frequency cap: exactly 1 per session lifetime. The `is None` check on `self_report_sent_at` enforces this.

4. **Document cost backstop:**
   - `docs/features/pm-session-liveness.md` (new) — sections: "Detector philosophy" (evidence > inference), "What the detector kills on" (worker_dead, OOM, delivered), "What the detector does NOT kill on" (stdout silence, wall-clock cap), "Cost backstop" (links `total_cost_usd` field + dashboard surfaces).

**Phase 2 — Pillar A**

5. **Add four `AgentSession` fields** in `models/agent_session.py`:
   - `current_tool_name: TextField(null=True, default=None)` — name of the tool currently being executed, or None if between tools.
   - `last_tool_use_at: DatetimeField(null=True, default=None)` — timestamp of last tool boundary (PreToolUse or PostToolUse, whichever fired last).
   - `last_turn_at: DatetimeField(null=True, default=None)` — timestamp of last result event (turn boundary).
   - `recent_thinking_excerpt: TextField(null=True, default=None)` — last 280 chars of extended-thinking content.

6. **Wire hook writers:**
   - `agent/hooks/pre_tool_use.py:387` — at the existing `tool_name = input_data.get("tool_name", "")` site, after the existing skill-tool dispatch, write `current_tool_name = tool_name` and `last_tool_use_at = datetime.now(tz=UTC)` to the AgentSession by resolving via `agent_session_id` from env. Save with `update_fields=["current_tool_name", "last_tool_use_at"]`.
   - `agent/hooks/post_tool_use.py` — clear `current_tool_name = None`, bump `last_tool_use_at` to now, save with `update_fields=["current_tool_name", "last_tool_use_at"]`.
   - `agent/sdk_client.py` (the `stream_event` handler around line 2338) — when receiving a `result` event, write `last_turn_at = datetime.now(tz=UTC)`. When receiving a `content_block_delta` with `type == "thinking_delta"` (or whatever the SDK calls extended-thinking deltas — verify in spike), accumulate into a small ring buffer and write the last 280 chars to `recent_thinking_excerpt`. Throttle saves to one per 5s to avoid Redis storm; coalesce tool-use writes likewise.

7. **Surface in dashboard:**
   - `ui/app.py` `_session_to_json()` — add the four fields plus a derived `last_evidence_at` computed as `max(last_heartbeat_at, last_sdk_heartbeat_at, last_stdout_at, last_tool_use_at, last_turn_at, last_compaction_ts)`. Render as ISO-8601 strings (None → null).
   - `ui/templates/` (HTML) — render new fields on session cards. Show "current tool: Read" prominently when `current_tool_name is not None`; show "last tool 4s ago" / "last turn 12s ago" age strings; show recent thinking excerpt in a collapsible section. If all four are None (e.g., older sessions), gracefully fall back to existing render.
   - `ui/data/sdlc.py` `PipelineProgress` — add the four fields so `_session_to_json` can read them.

8. **Throttle and bound writes:**
   - Per-session in-memory throttle (5s cooldown between Redis writes) to prevent storm under tight tool loops.
   - `recent_thinking_excerpt` capped at 280 chars (tweet length — small enough to render, large enough to be informative).
   - Hook writes wrapped in try/except — never crash the hook on Redis errors.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_health.py` — the `try/except` around the recovery counter increment (lines 788-796) and Mode 4 OOM defer (lines 1023-1029) remain. Add tests asserting the counter increment failure does NOT skip the kill path (existing behavior).
- [ ] `agent/session_completion.py::_emit_pm_self_report` — wrap the `subprocess.run(...)` in try/except. Assert WARNING is logged on failure AND `self_report_sent_at` remains None (so retry on next dev-completion is possible — but bounded by the dev-completion event firing, not by the detector).
- [ ] `agent/hooks/pre_tool_use.py` and `post_tool_use.py` — wrap the new AgentSession field writes in try/except. Assert the hook does NOT crash on Redis failure; the hook return value is unchanged.
- [ ] `agent/sdk_client.py` thinking-delta accumulator — wrap in try/except; on failure, log DEBUG and proceed. The session must not crash on a malformed delta.

### Empty/Invalid Input Handling
- [ ] `_emit_pm_self_report` with `parent.message_text == ""` — fallback to a generic template ("Working on PM session...").
- [ ] `_emit_pm_self_report` with `parent.project_name is None` — log WARNING and skip the send (no fallback to "Dev: Valor" — the wrong channel is worse than no message).
- [ ] PostToolUse hook with no AgentSession resolvable — silently no-op (matches existing hook patterns).
- [ ] Empty `recent_thinking_excerpt` (no thinking deltas observed) — field stays None; dashboard renders nothing.

### Error State Rendering
- [ ] Dashboard with all four Pillar A fields None — renders existing layout without the new tool/thinking surfaces (no broken UI).
- [ ] Dashboard with `last_evidence_at` older than 1h — explicit age string with the staleness, not a bare timestamp.
- [ ] PM session whose `_emit_pm_self_report` failed — final delivery still proceeds; no orphaned mid-work state.

## Test Impact

- [ ] `tests/unit/test_health_check.py` — REPLACE: remove all assertions that depend on `STDOUT_FRESHNESS_WINDOW`, `FIRST_STDOUT_DEADLINE`, or per-session timeout kills. Add tests asserting these paths are absent.
- [ ] `tests/unit/test_session_heartbeat_progress.py` — UPDATE: keep assertions that dual-heartbeat OR holds; remove assertions that fire on stdout-stale Tier 1 flag (`first_stdout_deadline`, `stdout_stale` reason kinds no longer exist).
- [ ] `tests/unit/test_session_health_compacting_reprieve.py` — UPDATE: the `compacting` Tier 2 reprieve still applies, but only from heartbeat-stale recovery (not stdout-stale, which is gone). Adjust trigger setup.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: remove timeout-recovery test cases; keep `worker_dead` and `response_delivered_at` finalize cases.
- [ ] `tests/unit/test_session_zombie_health_check.py` — UPDATE: remove timeout-related assertions; zombie detection is via `_tier2_reprieve_signal()` psutil gates which still exist.
- [ ] `tests/unit/test_worker_health_check.py` — UPDATE: drop test for FIRST_STDOUT_DEADLINE behavior; assert long-running sessions with fresh heartbeats survive indefinitely.
- [ ] `tests/unit/test_agent_session_health_monitor.py` — UPDATE: align assertions with the simplified `_has_progress()` logic.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: phantom guard logic unchanged but the recovery counter labels (`tier1_flagged_stdout_stale`) are gone — drop assertions on those labels.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py` — UPDATE: same as above.
- [ ] `tests/unit/test_transcript_liveness.py` — UPDATE: assertions about `last_stdout_at` driving liveness decisions need re-targeting (last_stdout_at is now informational only, not a kill signal).
- [ ] `tests/integration/test_pm_*.py` (any PM integration tests covering completion behavior) — UPDATE: assert exactly one self-report is sent before final delivery in the goldilocks scenario.
- [ ] `tests/unit/test_session_completion.py` (if exists, otherwise create) — REPLACE: add tests for `_emit_pm_self_report` covering trigger gates, frequency cap, subprocess failure handling.
- [ ] **Pillar A tests (Phase 2 only):**
  - `tests/unit/test_pre_tool_use_hook.py` — UPDATE: assert `current_tool_name` and `last_tool_use_at` are written when the hook runs.
  - `tests/unit/test_post_tool_use_hook.py` — UPDATE: assert `current_tool_name` is cleared and `last_tool_use_at` is bumped.
  - `tests/unit/test_dashboard_session_json.py` (or wherever `_session_to_json` is tested) — UPDATE: assert the four new fields appear in the JSON output and `last_evidence_at` is computed correctly.
  - `tests/unit/test_sdk_client_stream.py` (if exists, otherwise create) — REPLACE/CREATE: assert thinking deltas accumulate to `recent_thinking_excerpt` capped at 280 chars; assert `last_turn_at` bumps on result events.

**Regression tests required by issue acceptance criteria:**
- [ ] `tests/integration/test_pm_long_run_no_kill.py` — REPLACE/CREATE: a PM session running 4+ hours (test uses fixture/clock manipulation, not real wall time) with active tool use and no result event is NOT killed.
- [ ] `tests/integration/test_pm_goldilocks_messaging.py` — REPLACE/CREATE: a PM session that completes work emits ≤1 mid-work status message + 1 final "done" — not the spam-mode cadence.

## Rabbit Holes

- **LLM-generated self-report content.** Tempting to make the self-report "smart" by having the agent compose its own status string. Will swallow days; produces inconsistent voice; risks spam-mode resurfacing. Stay templated.
- **Generalizing self-report to all session types.** Dev sessions, Teammate sessions, etc. would all benefit from progress visibility — but each has different semantics (Dev session output is the build artifact; Teammate output is the conversation). Scope to PM in this plan; revisit per-type later if needed.
- **Replacing the entire health check with cost-based monitoring.** The cost ceiling exists (`total_cost_usd` per session) but is not currently a kill trigger. Adding cost-based kills is a separate plan; this plan only documents that cost monitoring is the long-run backstop and doesn't touch the cost ceiling logic.
- **Refactoring the SDK stream-event handler.** The thinking-delta accumulator is a small addition near `agent/sdk_client.py:2338`. Don't touch the broader stream handler; don't normalize the event taxonomy. One small write site per event type.
- **Clipping the dashboard's existing surfaces.** Pillar A is additive. Don't reorganize the existing session card layout; append the new fields below the existing ones. Visual polish is a separate design pass.
- **Backfilling `current_tool_name` for already-running sessions.** The new fields are nullable; sessions running across the deploy boundary will have None until their next tool boundary. No migration needed.
- **Generalizing the timeout removal to non-PM sessions.** All session types currently share `_get_agent_session_timeout()`. The plan removes the timeout for ALL session types because the asymmetric error model applies equally — but if a specific class of session (e.g., a known-hostile script) needs a hard cap, that's a future addition with explicit justification. Don't add per-type carve-outs preemptively.

## Risks

### Risk 1: Removing the per-session timeout uncovers a class of genuinely runaway session
**Impact:** A session enters a tool-loop that the dual-heartbeat OR check + Tier 2 reprieve all consider "alive" because the subprocess truly is alive and producing tool calls — but the work is not advancing toward a result. Without the wall-clock timeout, the session runs until cost or memory backstops trigger.
**Mitigation:** (a) Cost backstop is real — `total_cost_usd` exists per session and can be alarmed via the dashboard. (b) The `worker_dead` and Mode 4 OOM kill paths still exist. (c) If observed in production, add an explicit `cost_ceiling_usd` per session as a follow-up — that's evidence-based (real spend, not inferred staleness). Document the failure mode in `docs/features/pm-session-liveness.md`.

### Risk 2: Self-report misfires (sent twice, sent in wrong channel, sent from non-PM session)
**Impact:** CEO receives spam or cross-talk; defeats the goldilocks goal.
**Mitigation:** (a) `self_report_sent_at` field gated by `is None` check enforces single-send. (b) Channel resolution uses `parent.project_name` not a hardcoded chat name — a None project_name skips the send entirely. (c) Trigger gate explicitly checks `parent.session_type == "pm"`. (d) Integration test asserts exactly one self-report per session.

### Risk 3: Pillar A hook writes overload Redis under tight tool loops
**Impact:** Worker latency spikes; Redis storm; downstream queue effects.
**Mitigation:** (a) Per-session 5s write cooldown enforced in the hook. (b) `update_fields` parameter scopes the write to only the changed fields. (c) Load test in Phase 2 with a session running 100+ tool calls in 60s; assert Redis write rate stays bounded.

### Risk 4: Dashboard regression from added fields when `_session_to_json` is consumed by an external client
**Impact:** Existing callers that rely on a specific field set may break on extra fields.
**Mitigation:** (a) JSON additions are backwards-compatible (extra fields ignored by typical JSON consumers). (b) Audit `dashboard.json` consumers in the repo (search for `dashboard.json` references) before Phase 2 lands.

### Risk 5: Removing `STDOUT_FRESHNESS_WINDOW` in `_tier2_reprieve_signal()` gate (e) weakens reprieves for sessions whose pid is unknown
**Impact:** A session with a stale `handle.pid` (None) and no recent stdout could be killed even if it's actually alive.
**Mitigation:** (a) The dual-heartbeat OR check is the primary live signal — sessions writing heartbeats are not flagged at Tier 1 in the first place. (b) `handle.pid is None` is rare (a brief window during BackgroundTask startup); the existing `AGENT_SESSION_HEALTH_MIN_RUNNING` guard (300s) covers this window. (c) If false-kills emerge, restore gate (e) with a small literal window — this is a tactical knob, not a strategic deadline.

## Race Conditions

### Race 1: Self-report fires concurrently with PM final-delivery turn
**Location:** `agent/session_completion.py::_emit_pm_self_report` and `agent/session_completion.py::_complete_agent_session`
**Trigger:** A long-running PM session's first dev-child completion fires the self-report at the same tick that the PM begins its final-delivery turn.
**Data prerequisite:** `parent.self_report_sent_at` must be readable before the final-delivery turn writes its outputs.
**State prerequisite:** The PM is in a non-terminal status when the self-report is composed; a check-then-write race could send the self-report after the PM has already begun final delivery, producing duplicate user-facing output.
**Mitigation:** (a) Trigger gate explicitly checks `parent.status not in TERMINAL_STATUSES` AND `parent.completion_sent != True`. (b) The self-report `valor-telegram send` is fire-and-forget; if the final-delivery composition has already started in parallel, the self-report still arrives first chronologically (Telegram message ordering by send time is sufficient). (c) Empirically the dev-child completion handler runs synchronously within the PM's turn boundary, so the race is theoretical — still guard explicitly.

### Race 2: PreToolUse and PostToolUse hooks racing on `current_tool_name`
**Location:** `agent/hooks/pre_tool_use.py` and `agent/hooks/post_tool_use.py`
**Trigger:** Two tool calls in immediate succession; PostToolUse for tool A could land after PreToolUse for tool B, clearing `current_tool_name` set by tool B.
**Data prerequisite:** The hook must know which tool's PostToolUse it is processing.
**State prerequisite:** Tool calls are serial within a single agent turn (the SDK does not interleave tool calls), so PreToolUse(A) → PostToolUse(A) → PreToolUse(B) → PostToolUse(B) is the actual order.
**Mitigation:** (a) Tool calls are serial per-session — verified against SDK behavior (single in-flight tool per turn). (b) Hooks are single-threaded per session. (c) The `update_fields` save scopes writes to only the relevant fields; no cross-field interference.

### Race 3: Dashboard reads `last_evidence_at` mid-write
**Location:** `ui/app.py::_session_to_json` and the hook write sites
**Trigger:** Dashboard serializes a session at the same instant a hook writes one of the contributing fields.
**Data prerequisite:** Reading partially-written field state could produce a `last_evidence_at` that's older than reality.
**State prerequisite:** Popoto reads are atomic per field (Redis HGET); cross-field consistency is not guaranteed.
**Mitigation:** Acceptable inconsistency. The dashboard refreshes; the next read will pick up the new field. No remediation needed beyond accepting eventual consistency.

## No-Gos (Out of Scope)

- New wall-clock deadlines anywhere in the detector or anywhere else. **Hard rule from the issue.**
- Per-session-type kill policies (PM vs Dev vs Teammate). One detector behavior for all session types.
- Migrating the existing PM final-delivery protocol (`agent/session_completion.py` PM final-delivery sections). The self-report is *added*; the final delivery is unchanged.
- Slimming the PM persona / system-prompt size to address slow first turns. Tweak 2 from #1159 raised this; punt to a separate plan if pursued at all.
- Changing the bridge's nudge loop or output handler protocol. The self-report goes via `valor-telegram send` subprocess, not via the bridge's relay.
- Adding LLM-generated self-report content. Templated only.
- Backfilling Pillar A fields for sessions started before the deploy. Nullable fields suffice.
- Removing the `tool_call_count` field. It's still useful as a counter even though `current_tool_name` and `last_tool_use_at` are richer signals.
- Adding cost-based kill triggers. Out of scope; cost backstop is documentation-only in this plan.
- Reorganizing the dashboard's session card layout. Pillar A fields are appended; visual redesign is a separate design pass.
- Making the self-report a chat-routable command (e.g., user replies "more details" and PM elaborates). The self-report is one-way.

## Update System

The plan modifies in-process Python code only — no changes to deploy scripts, migrations, or update tooling.

- **`scripts/remote-update.sh`**: No changes. Standard `git pull` + restart picks up the new code.
- **`/update` skill**: No changes.
- **Migrations**: None. The four new `AgentSession` fields are nullable with `default=None`; Popoto materializes them lazily on first save. No backfill required.
- **Config files**: None added or removed. The deleted `STDOUT_FRESHNESS_WINDOW_SECS` and `FIRST_STDOUT_DEADLINE_SECS` env vars become no-ops; document this in the feature doc but do not write a env-var migration script — operators who set them in `.env` will see no effect after deploy, which is the intended behavior.
- **Existing installations**: After deploy, sessions started before the cutover may have `current_tool_name = None` etc. forever (until they end). New sessions get the full Pillar A treatment immediately. No operator action required.

## Agent Integration

- **MCP servers**: No new MCP tools needed. The agent (Claude Code via the worker harness) already has `Bash` access and can invoke `valor-telegram send` directly. The self-report from Phase 1 is invoked by the **harness** (`agent/session_completion.py`), not by the agent inside the SDK — so no MCP-layer change is required.
- **`.mcp.json`**: No changes.
- **`mcp_servers/`**: No changes.
- **`bridge/telegram_bridge.py`**: No changes. The self-report bypasses the bridge's relay (it goes directly via `valor-telegram send` CLI, which uses the Redis outbox). The bridge's nudge loop is untouched.
- **Agent visibility**: The PM session as the *agent* does not need to see the self-report status field — `self_report_sent_at` is a harness-side state flag, not an agent-readable signal. Pillar A fields ARE harness-written and dashboard-readable, but the agent itself doesn't read its own `current_tool_name` (the PreToolUse hook writes it for the operator's benefit).
- **Integration tests**: `tests/integration/test_pm_self_report_e2e.py` (new) — bring up a PM session in a test fixture, force a dev-child completion, assert the self-report subprocess was invoked with the correct args (`valor-telegram send --chat 'PM: TestProject' '<templated content>'`). Use a mock subprocess for hermeticity OR a real `valor-telegram send` against a known test chat — prefer real per the project's "no mocks" testing philosophy if a test chat is available.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pm-session-liveness.md` — sections: "Detector philosophy" (evidence vs inference), "What the detector kills on" (worker_dead, OOM, delivered, auth-error), "What the detector does NOT kill on" (stdout silence, wall-clock cap), "PM self-report behavior" (trigger, content, channel, cap), "Pillar A surfaces" (current tool, last tool use, last turn, recent thinking), "Cost backstop" (links `total_cost_usd` and dashboard).
- [ ] Add entry to `docs/features/README.md` index table under the session/health rows.
- [ ] Update `docs/features/session-management.md` if it cross-references the deleted `STDOUT_FRESHNESS_WINDOW` / `FIRST_STDOUT_DEADLINE` constants — replace with a pointer to the new feature doc.
- [ ] Update `docs/features/bridge-self-healing.md` if it discusses inference-based kills — adjust to reflect evidence-only model.

### Inline Documentation
- [ ] Update the module docstring at the top of `agent/session_health.py` to reflect the simplified detector model.
- [ ] Add a docstring on `_emit_pm_self_report` covering trigger conditions, frequency cap, and channel resolution.
- [ ] Update the `_has_progress()` docstring to remove references to `STDOUT_FRESHNESS_WINDOW` and `FIRST_STDOUT_DEADLINE`.
- [ ] Update the `_tier2_reprieve_signal()` docstring to remove the `stdout` gate documentation.
- [ ] Add docstrings on the four new `AgentSession` fields explaining write sites and read sites.

### CLAUDE.md
- [ ] No CLAUDE.md changes anticipated. The detector is invisible to the operator-facing CLI surface.

## Success Criteria

- [ ] `_has_progress()` no longer references `STDOUT_FRESHNESS_WINDOW` or `FIRST_STDOUT_DEADLINE` (grep confirms zero references).
- [ ] `agent/session_health.py` no longer references `_get_agent_session_timeout` (grep confirms zero references; the helper is deleted if it has no other call sites).
- [ ] `_tier2_reprieve_signal()` retains `compacting`, `alive`, `children` gates; `stdout` gate is removed.
- [ ] PM session emits exactly one `valor-telegram send` between first dev-child completion and final delivery, observed in integration test.
- [ ] PM session whose dev-child completion never fires emits zero self-reports — final delivery still proceeds normally.
- [ ] Dashboard `dashboard.json` exposes `current_tool_name`, `last_tool_use_at`, `last_turn_at`, `recent_thinking_excerpt`, `last_evidence_at` for every session in `/dashboard.json` output.
- [ ] Regression test: a PM session simulating 4+ hours of active tool use with no result event is NOT killed (asserted via fixture/clock manipulation).
- [ ] Regression test: a PM session that completes work emits exactly 1 mid-work message + 1 final delivery (asserted via integration test with test chat).
- [ ] `docs/features/pm-session-liveness.md` exists and is linked from `docs/features/README.md`.
- [ ] Tests pass (`/do-test`).
- [ ] Lint clean (`python -m ruff check .`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No new `xfail` markers introduced; any prior xfail tests touching the deleted detector paths are converted to hard assertions or deleted.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead never builds directly — they deploy team members and coordinate.

### Team Members

**Phase 1 (Pillar B + self-report):**

- **Builder (detector cleanup)**
  - Name: `detector-builder`
  - Role: Delete inference paths in `agent/session_health.py`; preserve evidence-based paths.
  - Agent Type: builder
  - Resume: true

- **Builder (self-report)**
  - Name: `self-report-builder`
  - Role: Add `self_report_sent_at` field; implement `_emit_pm_self_report` in `agent/session_completion.py`; wire into dev-child completion handler.
  - Agent Type: builder
  - Resume: true

- **Test-engineer (Phase 1 regression)**
  - Name: `phase1-test-engineer`
  - Role: Update existing health-check tests (REPLACE/UPDATE per Test Impact); write Phase 1 regression tests (long-running no-kill; goldilocks messaging).
  - Agent Type: test-engineer
  - Resume: true

- **Validator (Phase 1)**
  - Name: `phase1-validator`
  - Role: Verify all Phase 1 success criteria; run `pytest tests/`, `python -m ruff check .`; confirm grep assertions on deleted symbols.
  - Agent Type: validator
  - Resume: true

**Phase 2 (Pillar A):**

- **Builder (model + hooks)**
  - Name: `pillar-a-model-builder`
  - Role: Add four `AgentSession` fields; wire PreToolUse / PostToolUse / SDK stream-event writers with throttle.
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: `pillar-a-dashboard-builder`
  - Role: Surface new fields in `_session_to_json`; render in `ui/templates/`; update `ui/data/sdlc.py`.
  - Agent Type: builder
  - Resume: true

- **Test-engineer (Phase 2)**
  - Name: `phase2-test-engineer`
  - Role: Hook write tests; SDK stream-event tests; dashboard JSON tests; throttle/cooldown load test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (Phase 2)**
  - Name: `phase2-validator`
  - Role: Verify dashboard rendering; run integration tests; confirm `last_evidence_at` derivation.
  - Agent Type: validator
  - Resume: true

**Cross-phase:**

- **Documentarian**
  - Name: `liveness-documentarian`
  - Role: Author `docs/features/pm-session-liveness.md`; update `docs/features/README.md` and cross-references.
  - Agent Type: documentarian
  - Resume: true

- **Code-reviewer (final)**
  - Name: `liveness-reviewer`
  - Role: Pre-merge review across both phases; verify no new wall-clock deadlines; verify evidence-based contract holds.
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### Phase 1 — Pillar B + self-report (PR 1)

#### 1. Delete inference paths in session_health.py
- **Task ID**: build-detector-cleanup
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py, tests/unit/test_session_heartbeat_progress.py, tests/unit/test_session_health_compacting_reprieve.py, tests/unit/test_health_check_recovery_finalization.py, tests/unit/test_session_zombie_health_check.py, tests/unit/test_worker_health_check.py, tests/unit/test_agent_session_health_monitor.py, tests/unit/test_session_health_phantom_guard.py, tests/unit/test_session_health_sibling_phantom_safety.py, tests/unit/test_transcript_liveness.py
- **Informed By**: Recon Summary (kill path audit)
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `STDOUT_FRESHNESS_WINDOW` constant and env-var read at session_health.py:139-145.
- Remove `FIRST_STDOUT_DEADLINE` constant and env-var read at session_health.py:155-156.
- Simplify `_has_progress()` body — delete lines 535-550 (stdout-stale and first-stdout deadline checks); keep dual-heartbeat OR + own-progress + child-progress.
- Delete the timeout recovery branch at session_health.py:765-769.
- Remove the `timeout` reason kind classification at session_health.py:781-782.
- Audit call sites for `_get_agent_session_timeout()` — if exclusive to session_health.py, delete the helper entirely.
- Update `_tier2_reprieve_signal()` to drop the `stdout` gate (lines 644-650 in the current file). Update the docstring accordingly.
- Update log lines that imply live-state knowledge or reference deleted reason kinds.
- Remove `_last_progress_reason` references for `stdout_stale` / `first_stdout_deadline` at lines 850-861 (the reasons no longer exist).
- Update module docstring at top of session_health.py to reflect simplified model.

#### 2. Add self_report_sent_at field and helper
- **Task ID**: build-self-report
- **Depends On**: none (parallel with build-detector-cleanup)
- **Validates**: tests/unit/test_session_completion.py (new/replaced)
- **Informed By**: Recon Summary (sustainability.py:604-621 pattern)
- **Assigned To**: self-report-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `self_report_sent_at: DatetimeField(null=True, default=None)` to `models/agent_session.py`.
- Add to the `update_fields`-safe list if such a list exists in the model.
- Implement `_emit_pm_self_report(parent: AgentSession)` in `agent/session_completion.py` adjacent to existing PM final-delivery helpers.
- Wire `_emit_pm_self_report` into `_handle_dev_session_completion` (or whichever existing handler runs on dev-child completion). Trigger gates per Solution.
- Use the `agent/sustainability.py:604-621` subprocess pattern verbatim (do NOT refactor `_send_telegram` into a shared helper in this PR — that's a separate refactor).
- Failure handling: subprocess timeout 30s; rc != 0 logs WARNING and leaves `self_report_sent_at = None`.

#### 3. Update existing tests + add Phase 1 regression tests
- **Task ID**: build-phase1-tests
- **Depends On**: build-detector-cleanup, build-self-report
- **Validates**: All test files listed in Test Impact for Phase 1
- **Informed By**: Test Impact section
- **Assigned To**: phase1-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- UPDATE/REPLACE/DELETE per Test Impact list (Phase 1 entries only).
- Create `tests/integration/test_pm_long_run_no_kill.py` — fixture-driven 4+ hour simulated run, assert no kill.
- Create `tests/integration/test_pm_goldilocks_messaging.py` — assert exactly 1 self-report + 1 final delivery.
- Convert any related xfail markers to hard assertions or delete obsolete ones.

#### 4. Phase 1 documentation
- **Task ID**: build-phase1-docs
- **Depends On**: build-detector-cleanup, build-self-report
- **Assigned To**: liveness-documentarian
- **Agent Type**: documentarian
- **Parallel**: true (with build-phase1-tests)
- Create `docs/features/pm-session-liveness.md` with all sections from Documentation.
- Update `docs/features/README.md` index.
- Update `docs/features/session-management.md` cross-references.
- Update `docs/features/bridge-self-healing.md` if needed.

#### 5. Phase 1 validation
- **Task ID**: validate-phase1
- **Depends On**: build-detector-cleanup, build-self-report, build-phase1-tests, build-phase1-docs
- **Assigned To**: phase1-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ tests/integration/` and assert all pass.
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Grep assertions: `grep -rn "STDOUT_FRESHNESS_WINDOW\|FIRST_STDOUT_DEADLINE\|_get_agent_session_timeout" agent/` returns nothing.
- Verify `tests/integration/test_pm_long_run_no_kill.py` and `tests/integration/test_pm_goldilocks_messaging.py` exist and pass.
- Confirm `docs/features/pm-session-liveness.md` exists and is linked from `docs/features/README.md`.
- Generate Phase 1 PR-ready report.

### Phase 2 — Pillar A (PR 2, after Phase 1 merges)

#### 6. Add Pillar A fields and hook wiring
- **Task ID**: build-pillar-a-model
- **Depends On**: validate-phase1 (Phase 1 must merge first)
- **Validates**: tests/unit/test_pre_tool_use_hook.py, tests/unit/test_post_tool_use_hook.py, tests/unit/test_sdk_client_stream.py
- **Informed By**: Architectural Impact, Race Conditions
- **Assigned To**: pillar-a-model-builder
- **Agent Type**: builder
- **Parallel**: false
- Add four fields to `models/agent_session.py` per Technical Approach.
- Wire `agent/hooks/pre_tool_use.py` writer (set `current_tool_name`, bump `last_tool_use_at`).
- Wire `agent/hooks/post_tool_use.py` writer (clear `current_tool_name`, bump `last_tool_use_at`).
- Wire `agent/sdk_client.py` writer (bump `last_turn_at` on result event; accumulate thinking-delta into `recent_thinking_excerpt` capped at 280 chars).
- Implement per-session 5s write cooldown using a small in-memory dict keyed by `agent_session_id`.
- Wrap all writes in try/except — Redis failures must not crash the hook or stream handler.

#### 7. Surface Pillar A in dashboard
- **Task ID**: build-pillar-a-dashboard
- **Depends On**: build-pillar-a-model
- **Validates**: tests/unit/test_dashboard_session_json.py
- **Assigned To**: pillar-a-dashboard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add fields to `ui/data/sdlc.py` `PipelineProgress`.
- Add fields to `ui/app.py` `_session_to_json` plus derived `last_evidence_at` (max of all evidence timestamps).
- Render in `ui/templates/` session card with collapsible thinking excerpt.
- Verify `curl -s localhost:8500/dashboard.json` includes the new fields after the change.

#### 8. Phase 2 tests + load test
- **Task ID**: build-phase2-tests
- **Depends On**: build-pillar-a-model, build-pillar-a-dashboard
- **Validates**: All test files listed in Test Impact for Phase 2
- **Assigned To**: phase2-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- UPDATE/REPLACE/CREATE per Test Impact list (Phase 2 entries).
- Add load test asserting Redis write rate stays bounded under 100+ tool calls in 60s.
- Add JSON-shape test asserting `last_evidence_at` is computed correctly (max-of-all timestamps, None if all None).

#### 9. Phase 2 documentation update
- **Task ID**: build-phase2-docs
- **Depends On**: build-pillar-a-model, build-pillar-a-dashboard
- **Assigned To**: liveness-documentarian
- **Agent Type**: documentarian
- **Parallel**: true (with build-phase2-tests)
- Update `docs/features/pm-session-liveness.md` "Pillar A surfaces" section with concrete dashboard screenshots / field references.
- Add docstrings to the four new `AgentSession` fields.

#### 10. Phase 2 validation + final code review
- **Task ID**: validate-phase2
- **Depends On**: build-pillar-a-model, build-pillar-a-dashboard, build-phase2-tests, build-phase2-docs
- **Assigned To**: phase2-validator, liveness-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`pytest tests/`).
- Verify dashboard renders new fields end-to-end (start worker + bridge, observe a real session).
- Code-reviewer final pass: confirm no new wall-clock deadlines, evidence-based contract holds, hook throttling is correct.
- Generate Phase 2 PR-ready report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No deleted constants survive | `grep -rn "STDOUT_FRESHNESS_WINDOW\\|FIRST_STDOUT_DEADLINE" agent/` | exit code 1 |
| No deleted helper survives | `grep -rn "_get_agent_session_timeout" agent/` | exit code 1 |
| Self-report helper exists | `grep -n "_emit_pm_self_report" agent/session_completion.py` | output > 0 |
| New fields exist in model | `grep -E "current_tool_name\\|last_tool_use_at\\|last_turn_at\\|recent_thinking_excerpt\\|self_report_sent_at" models/agent_session.py` | output > 4 |
| Dashboard exposes new fields | `curl -s localhost:8500/dashboard.json \| python -c "import json,sys; d=json.load(sys.stdin); s=d['sessions'][0] if d['sessions'] else {}; assert 'current_tool_name' in s and 'last_evidence_at' in s"` | exit code 0 |
| Feature doc exists | `test -f docs/features/pm-session-liveness.md` | exit code 0 |
| Feature doc indexed | `grep -n "pm-session-liveness" docs/features/README.md` | output > 0 |
| Long-run regression test | `pytest tests/integration/test_pm_long_run_no_kill.py -q` | exit code 0 |
| Goldilocks regression test | `pytest tests/integration/test_pm_goldilocks_messaging.py -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Phase split granularity.** The plan recommends **two PRs** (Phase 1: Pillar B + self-report; Phase 2: Pillar A). Alternate split: three PRs (B / self-report / A) for smaller blast radius per PR. Current recommendation bundles B + self-report because they share the "graceful PM behavior" theme and the regression test for goldilocks messaging needs both. Confirm two-PR split is acceptable.
2. **Cost ceiling as a follow-up.** Risk 1 mentions adding `cost_ceiling_usd` per session if a runaway-tool-loop class of failure emerges. Should that be filed as a follow-up issue now, or wait for an actual incident?
3. **Self-report channel resolution failure mode.** If `parent.project_name is None`, the plan currently skips the send (logged WARNING). Alternative: fall back to `Dev: Valor` as a safety net. Current design rejects the fallback because the wrong channel is worse than no message — confirm.
4. **Thinking-delta SDK event taxonomy.** The plan references `content_block_delta` with `type == "thinking_delta"` but the SDK's exact event shape needs verification in Phase 2 before implementation. Should this be a Phase-2 spike, or accept a 1-day implementation discovery cost?
5. **Test chat for self-report integration test.** The plan suggests using a real `valor-telegram send` against a known test chat per the project's "no mocks" testing philosophy. Is there an existing test chat (e.g., `Dev: Test`) usable for this, or does this test fall back to a subprocess mock?
