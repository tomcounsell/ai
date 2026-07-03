---
status: Complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1843
last_comment_id:
revision_applied: true
---

# Granite silent wedges: wire the two still-dead signals (liveness fields + per-iteration PTY read)

## Problem

Granite (the PTY-driven PM/Dev session runner, `agent/granite_container/`) wedges
silently in production. The session-health machinery built to protect these
sessions already computes the right signals. The original issue named three
un-actuated signals; **revision-3 freshness re-verification against HEAD
(`331f251c`) proved one of them (Gap 1) is already wired** by #1768/#1773 (an
ancestor of the plan's own baseline). This plan ships the two that are genuinely
still dead, and records the Gap 1 finding so it is not re-built as a no-op.

**Gaps that remain dead at HEAD (this plan's scope):**

- **Gap A (was Gap 2) — CLI-hook liveness fields are structurally dead for granite.**
  `current_tool_name` / `last_tool_use_at` are written only by the SDK in-process
  hooks (`agent/hooks/pre_tool_use.py`, `post_tool_use.py` →
  `agent/hooks/liveness_writers.py:81::record_tool_boundary`). Granite's PM/Dev
  `claude` children run the **CLI hooks** (`.claude/settings.json:27,74`), whose
  `_update_agent_session` (`.claude/hooks/post_tool_use.py:443-494`, save at :494)
  writes only `updated_at` + `tool_call_count`, and `pre_tool_use.py::main` (:61)
  writes no AgentSession liveness field at all. Consequence: the #1270
  tool-timeout tier loop short-circuits on a null tool name
  (`agent/session_health.py:374-376`) and therefore **never fires for granite**.
- **Gap B (was Gap 3) — PTY activity samples too coarsely.** The `on_pty_read`
  liveness callback fires once per `_cycle_idle` return
  (`agent/granite_container/container.py:1099-1101`), not per inner read iteration
  inside `PTYDriver.read_until_idle` (`pty_driver.py:513`, no callback param at
  HEAD). The stale gap comment survives at `container.py:1092-1098`. A wedge
  *inside* a long idle-path turn refreshes `last_pty_read_loop_at` nothing until
  the cycle window elapses.

**Gap 1 (granite_wedged → recovery) is already shipped — deferred residual only.**
The `granite_wedged` verdict (`agent/session_stall_classifier.py:296-306`) is
**already consumed by an actor**: `reflections/stall_advisory.py` builds the
timeline (`read_session_timeline`, :151), calls `classify_session_stall` (:152),
and `_maybe_recover` (:240) runs a full kill+recover ladder — consec-observation
counter, per-run and per-session kill budgets, Race-1 re-read guard, kill via
`tools.agent_session_scheduler._kill_agent_session`, `valor-catchup` re-enqueue,
and a `stall_recovery_action` dashboard event. `granite_wedged` is already in
`_ACTIONABLE_STALL_REASONS` (`stall_advisory.py:45`). The ladder is gated by
`stall_recovery_enabled` (`config/settings.py:294`, **default `False` = dry-run**,
reversible per-machine `.env`). So the *decision* actuates today. The only
residual is that `_kill_agent_session` reaps by **single PID**
(`_find_process_by_session_id` → `pgrep -f session_id` → `_kill_process`
SIGTERM→SIGKILL), **not** the granite PTY **process group** (`os.killpg`). That
process-group teardown is owned by #1820's progress-deadline cancel scope / the
#1816 `container._close_pair_and_reap` `killpg` seam. Gap 1 is therefore a No-Go
here (see No-Gos), deferred to #1820. This plan does not touch
`session_health.py` and adds no new actuation path.

**Desired outcome:**

The two still-dead signals actuate:
1. The CLI hooks populate `current_tool_name` / `last_tool_use_at` so the existing
   #1270 tool-timeout tiers arm for granite.
2. Mid-turn PTY reads refresh `last_pty_read_loop_at` before the cycle window elapses.

Both are red-first testable: Gap A against a granite-session AgentSession under the
CLI hooks; Gap B against the #1837 harness class-6 "silent no-progress tail"
injector (`tests/granite_faults/scenarios.py:304-314`), whose seam is
`pty_driver.read_until_idle` — exactly Gap B's territory.

## Freshness Check

**Baseline commit:** `331f251c` (`git rev-parse HEAD` at revision-3 time; the
previous baseline `0297da0d` is an ancestor of this commit).
**Issue filed at:** 2026-07-02T04:31:21Z
**Disposition:** **Major drift** on Gap 1 (already implemented); **Unchanged** on Gap A/Gap B.

**Gap 1 is already wired (Major drift — the original plan missed a merged commit).**
`git log a7f86331` — "feat(#1768): stall-advisory actor + granite_wedged signal
(auto-recover wedged sessions) (#1773)" — landed the full `_maybe_recover` kill+recover
ladder and added `granite_wedged` to `_ACTIONABLE_STALL_REASONS`. That commit is an
**ancestor of the plan's original baseline `0297da0d`**, so the "advisory-only, nothing
actuates" premise was already false when the plan was first written. Re-verified at HEAD:

- `stall_advisory.py:45` — `_ACTIONABLE_STALL_REASONS = {"never_started", "granite_wedged", "idle_gap_exceeded_stall"}` — `granite_wedged` present.
- `stall_advisory.py:152` — `classify_session_stall(events, session=session)` after `read_session_timeline(session_id)` at :151.
- `stall_advisory.py:240-426` — `_maybe_recover` full ladder ending in `_kill_agent_session` (:364-367) + `valor-catchup` + `_emit_recovery_event`.
- `config/settings.py:294` — `stall_recovery_enabled: bool = Field(default=False, ...)` — dry-run by default; enabling is a reversible per-machine `.env` edit (`FEATURES__STALL_RECOVERY_ENABLED`).
- `agent/session_health.py:29-31` — imports **only** `NEVER_STARTED_*`; **never** calls `classify_session_stall`. (BLOCKER 2 confirmed: session_health was never the actuation site.)

**BLOCKER 1 premise was inverted (recorded for future Gap-1 work / #1820).** The critique
said `_apply_recovery_transition` (`session_health.py:1848`) special-cases
`no_progress`/`worker_dead`/`tool_timeout` and any other value "falls through to the
DEFAULT full Tier-2 reprieve". The actual code at `session_health.py:1962` is
`if reason_kind == "no_progress":` (comment at :1955 reads "Tier 2 reprieve
(no_progress only)") — an **opt-in**, not a skip-set with a default reprieve. Any
non-`no_progress` `reason_kind` skips the reprieve and proceeds to recover. So a
hypothetical `granite_wedged` reason would **not** be reprieved by the alive gate.
This is moot for #1843 (Gap 1 is deferred and does not touch `session_health`), but is
recorded here so #1820 does not repeat the inverted assumption.

**CONCERN 3 verified UNFOUNDED — the granite_wedged probe is reachable post-#1688.**
The probe (`session_stall_classifier.py:268-306`) fires only when
`has_turn_start = any(e.get("type") == "turn_start" for e in events)` is `False`.
Verified: `turn_start` telemetry events are written **only** by
`agent/sdk_client.py`'s in-process SDK turn loop — which granite (running via
`BridgeAdapter → Container.run` on `to_thread`) never enters. #1688's `TURN_END`
(`hook_edge.py:76`) is a hook-edge **EdgeType**, not a `session_telemetry` event.
`bridge_adapter.py` updates the `turn_count` **field** (:1301) but never calls
`record_telemetry_event`. `tui_interaction_capture.py` records only `slash_command` /
`human_steering`, and is not wired to the granite container. The classifier's own
docstring (`session_stall_classifier.py:217-220`) documents this: granite sessions show
zero `turn_start` events. So `has_turn_start` stays `False` for granite post-#1688 and
the probe is reachable.

**Gap A / Gap B file:line references re-verified at HEAD (Unchanged):**
- `.claude/hooks/pre_tool_use.py:61` (`def main`) — no `current_tool_name` / `last_tool_use_at` / `record_tool_boundary` / AgentSession write today. **holds** (still dead).
- `.claude/hooks/post_tool_use.py:443-494` — `_update_agent_session` writes only `updated_at = time.time()` (float) + `tool_call_count` (save at :494). **holds**.
- `models/agent_session.py:500,505` — `current_tool_name = Field(null=True)`, `last_tool_use_at = DatetimeField(null=True)`. **holds** (DatetimeField — see CONCERN 4).
- `agent/session_health.py:374-379` — `_check_tool_timeout` returns `None` on null tool name (375) **and** on `not isinstance(last_at, datetime)` (378). **holds**.
- `agent/hooks/liveness_writers.py:102,136,174` — `record_tool_boundary` resolves via `os.environ.get("AGENT_SESSION_ID")`. **holds** (CONCERN 4: unset in granite child env).
- `pty_driver.py:513` — `def read_until_idle(` has no per-iteration callback param. **holds** (still dead).
- `container.py:1092-1098` gap comment + `1099-1101` `_cycle_idle` callback; `1200` `_await_turn_end` fires `_fire_pty_read` per poll-tick (hook-driven path only). **holds**.
- `tests/granite_faults/scenarios.py:304-314` — class-6 `silent_no_progress_tail`, seam `pty_driver.read_until_idle`, comment "No detector is wired here". **holds**; this is Gap B's substrate (not Gap 1's — corrected from the prior plan).

**Cited sibling issues/PRs re-checked:**
- #1724 CLOSED, #1270 CLOSED, #1837/#1839 CLOSED/MERGED, #1688 CLOSED/MERGED, #1816 CLOSED, #1768/#1773 MERGED (Gap 1 actor).
- **#1820** (`slot-lease-progress-deadline`) — **OPEN**. Owns the progress-deadline cancel scope + `killpg` PTY process-group teardown. Gap 1's residual is deferred to it.
- **#1821** (`out-of-domain-recovery-tool-budget`) — **OPEN**. Its Fix #6 edits `.claude/hooks/pre_tool_use.py::main` for granite-PTY children and resolves the AgentSession sidecar there. Gap A shares that file; land as one coordinated edit.

## Prior Art

- **#1768 / PR #1773**: "stall-advisory actor + granite_wedged signal (auto-recover wedged sessions)" — **already wired Gap 1's decision path.** Built `_maybe_recover` and added `granite_wedged` to `_ACTIONABLE_STALL_REASONS`, gated by `stall_recovery_enabled` (dry-run default). This is why Gap 1 is out of scope here.
- **#1724 / PR #1728**: built the `granite_wedged` verdict and the `last_pty_read_loop_at` / `last_pty_activity_at` freshness fields. Gap B makes `last_pty_read_loop_at` fresher mid-turn.
- **#1270 / PR (merged 2026-05-05)**: per-tool timeout tiers with per-tier counters — the tier loop Gap A arms for granite. It requires `current_tool_name` non-null, which is why granite never triggers it today.
- **#1789 / #1798**: gated `never_started` and default-tier `tool_timeout` kills on PTY liveness — established consulting PTY liveness before killing granite. Gap A's fields feed those existing gates; it adds no new kill decision.
- **#1816 / PR #1832**: `supervise()` + scoped process-group teardown (`os.killpg` in `container.py`) — the existing PTY process-group teardown that Gap 1's deferred residual (via #1820) reuses. `_kill_agent_session` does **not** call this.
- **#1688 / PR #1847**: hook-driven turn returns — introduced the per-session `--settings` + `hook_forwarder.py` architecture; confirms granite children run generated settings *in addition to* the repo `.claude/settings.json` (relevant to Gap A's fix location).

No prior attempt failed at these two wiring fixes; they were simply never done. No
"Why Previous Fixes Failed" section needed.

## Research

External research covered Claude Code hook/settings resolution semantics, because
Gap A's fix location depends on whether granite children (launched with
`claude --settings <generated>`) still fire the repo `.claude/settings.json` hooks.

**Queries / sources used:**
- Claude Code hooks doc (`docs.claude.com/en/docs/claude-code/hooks`)
- Claude Code settings doc (`docs.claude.com/en/docs/claude-code/settings`)

**Key findings:**
- **Hooks merge additively across settings sources.** "They don't override each other;
  they merge additively." Identical command hooks are deduplicated by command string + args.
  A `--settings` file *adds* hooks; it does not replace the project's `.claude/settings.json`
  hooks — provided `--settings` participates in the same additive merge.
- **The `--settings` CLI flag's precedence is a documented blind spot.** The docs alone
  cannot confirm granite children still fire the repo PostToolUse hook. Resolved by spike-1
  (cross-plan evidence) and a build-time empirical confirmation.

## Spike Results

### spike-1: Do granite `--settings` children still fire the repo `.claude/hooks/*`?
- **Assumption**: "Editing `.claude/hooks/post_tool_use.py` / `pre_tool_use.py` reaches granite PM/Dev PTY children (i.e. `claude --settings <generated>` merges rather than overrides the project `.claude/settings.json` hooks)."
- **Method**: code-read + cross-plan corroboration (docs are ambiguous on the flag).
- **Finding**: **CONFIRMED (high confidence).** Two independent lines of evidence: (a) the Claude Code hooks doc states hooks merge additively across sources with dedup; (b) #1821's Ready-and-critiqued plan (`out-of-domain-recovery-tool-budget.md`) treats `.claude/hooks/pre_tool_use.py::main` as "the interactive `claude` TUI / granite-PTY CLI hook" and wires per-tool budget enforcement there — dead code if `--settings` suppressed the project hooks, and it passed critique. The generated settings (`hook_edge.generate_hook_settings`) register only forwarder hooks (Stop/SubagentStop/Notification/PermissionRequest/PreToolUse-matcher-`AskUserQuestion`/PreCompact/SessionStart) → they ADD to, not replace, the repo's `*`-matcher Pre/PostToolUse hooks.
- **Confidence**: high.
- **Impact on plan**: Gap A's fix location is the repo `.claude/hooks/*`, coordinated with #1821. A **build-time empirical confirmation** (Task 1 red-first) is retained: assert `current_tool_name`/`last_tool_use_at` populate on a granite session's AgentSession after a real tool call under the pinned `claude`. If the empirical check ever shows the repo PostToolUse hook does NOT fire for granite, the fallback is a dedicated liveness hook in `hook_edge.generate_hook_settings` calling into a sidecar-resolving writer — documented in Rabbit Holes as the contingency, not the primary path.

## Data Flow

Gap A (tool-boundary liveness) end-to-end — the most cross-component fix:

1. **Entry point**: A granite PM/Dev `claude` child invokes a tool inside its PTY.
2. **CLI hook fires**: Claude Code runs the PreToolUse then PostToolUse hooks registered in the merged settings (`.claude/settings.json:27,74` + generated `--settings`), i.e. `.claude/hooks/pre_tool_use.py::main` and `.claude/hooks/post_tool_use.py::main`.
3. **AgentSession resolution**: The CLI hook resolves the granite session's `AgentSession` from the **sidecar** via `_load_agent_session_sidecar(session_id)` → `AgentSession.get_by_id(agent_session_id)` — the path already used by `_update_agent_session` (`.claude/hooks/post_tool_use.py:464-476`). **Not** `record_tool_boundary` (which resolves via `os.environ["AGENT_SESSION_ID"]` — unset in the granite child env; see CONCERN 4).
4. **Liveness write (NEW)**: pre-hook sets `current_tool_name = <tool>` + `last_tool_use_at = datetime.now(tz=UTC)`; post-hook clears `current_tool_name = None` + refreshes `last_tool_use_at = datetime.now(tz=UTC)` (mirroring `record_tool_boundary`'s Pre=set / Post=clear contract). `last_tool_use_at` MUST be a `datetime`, not `time.time()` — see CONCERN 4.
5. **Health loop reads**: `agent/session_health.py::_check_tool_timeout` (362-387) no longer short-circuits at 374-379 (tool name non-null AND `last_tool_use_at` is a `datetime`) → the #1270 tier budget arms for the granite session.
6. **Output**: A granite session stuck inside a single tool call past its tier budget now transitions through the existing #1270 recovery instead of being invisible.

Gap B flow: `read_until_idle` inner poll → per-iteration callback → the existing
`bridge_adapter` freshness writer refreshes `last_pty_read_loop_at` mid-turn.

## Architectural Impact

- **New dependencies**: none. Both fixes reuse existing symbols and files.
- **Interface changes**: `PTYDriver.read_until_idle` gains an optional per-iteration callback param (default `None` → byte-identical pre-existing behavior). No `session_health.py` change (Gap 1 deferred).
- **Coupling**: Gap A adds AgentSession field writes to the CLI hooks, resolving the session via the **existing** sidecar path already present in `.claude/hooks/post_tool_use.py::_update_agent_session` — no new module dependency, coordinated with #1821 which is adding AgentSession resolution to the same `main()`. (The prior plan's "read-only import of the classifier into `session_health.py`, no new module dependency" line was **wrong** and is removed: session_health never calls the classifier, and Gap 1 is deferred.)
- **Data ownership**: unchanged. The liveness/freshness fields are already owned by `AgentSession`; this plan only writes fields that were dead for granite.
- **Reversibility**: high. Gap B's callback is opt-in (param default `None`). Gap A is additive field writes.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer (coordination-sensitive: #1821 shares the CLI-hook file).

**Interactions:**
- PM check-ins: 1 (the #1821 landing-order coordination on `.claude/hooks/pre_tool_use.py`).
- Review rounds: 1 (shared-file edit with #1821 needs a review confirming the coordinated merge is clean).

Dropping Gap 1 (already shipped) removes the #1820 kill-ownership coordination that
made the original plan Medium. What remains is two additive, low-risk wiring fixes
sharing one file with one in-flight plan — Small.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Class-6 harness injector present | `grep -c "silent_no_progress_tail" tests/granite_faults/scenarios.py` | Red-first substrate for Gap B |
| `last_tool_use_at` is a DatetimeField | `grep -c "last_tool_use_at = DatetimeField" models/agent_session.py` | Gap A must write `datetime`, not float |
| `read_until_idle` present | `grep -c "def read_until_idle" agent/granite_container/pty_driver.py` | Gap B's callback insertion point |

Run via `python scripts/check_prerequisites.py docs/plans/granite-wire-silent-wedge-signals.md`.

## Solution

### Key Elements

- **Gap A liveness writes**: the CLI hooks (`.claude/hooks/pre_tool_use.py::main`,
  `.claude/hooks/post_tool_use.py::_update_agent_session`) set `current_tool_name` /
  `last_tool_use_at` on the **sidecar-resolved** AgentSession, mirroring
  `record_tool_boundary`'s Pre=set / Post=clear contract. `last_tool_use_at` is written
  as `datetime.now(tz=UTC)` (a `datetime`, never `time.time()`) so `_check_tool_timeout`'s
  `isinstance(last_at, datetime)` gate (`session_health.py:378`) passes. Fields are set
  directly on the session object resolved via `_load_agent_session_sidecar` — **not** via
  `record_tool_boundary`, which no-ops when `AGENT_SESSION_ID` is unset in the granite
  child env.
- **Gap B per-iteration callback**: `PTYDriver.read_until_idle` gains an optional
  per-read-iteration callback (default `None`); the granite read path threads the existing
  `bridge_adapter` freshness writer so `last_pty_read_loop_at` refreshes mid-turn.

### Flow

Granite session wedges mid-tool → CLI PreToolUse hook has stamped `current_tool_name`
+ a `datetime` `last_tool_use_at` on the sidecar-resolved AgentSession → #1270 tier loop
arms → budget expiry → existing #1270 recovery. Independently, Gap B keeps
`last_pty_read_loop_at` fresh mid-turn so the existing `granite_wedged` probe's
read-loop-fresh signal reflects reality inside long idle-path turns.

### Technical Approach

- **Gap A (priority 1):** In `.claude/hooks/pre_tool_use.py::main` (:61), after resolving
  the AgentSession via the sidecar path (reuse #1821's resolution if it lands first;
  otherwise resolve via `_load_agent_session_sidecar(session_id)` →
  `AgentSession.get_by_id(...)`, mirroring `post_tool_use.py:464-476`), set
  `current_tool_name = <tool_name>` and `last_tool_use_at = datetime.now(tz=UTC)`, then
  `save(update_fields=["current_tool_name", "last_tool_use_at"])`. Add
  `from datetime import UTC, datetime` (neither hook imports it today). In
  `.claude/hooks/post_tool_use.py::_update_agent_session` (:443-494), add
  `current_tool_name = None` + `last_tool_use_at = datetime.now(tz=UTC)` to the existing
  `save(update_fields=[...])` at :494. Wrap all new writes fail-silent (exit 0 on an
  unresolvable session). **Land as one coordinated edit with #1821** (see Risks). Mirror
  the `record_tool_boundary` contract (`liveness_writers.py:81`): Pre sets the name, Post
  clears it — but set the fields **directly on the sidecar-resolved session**, not by
  calling `record_tool_boundary` (which resolves via `os.environ["AGENT_SESSION_ID"]`,
  unset in the granite child env → silent no-op).
- **Gap B (priority 2):** Add an optional `on_read_iteration` param to
  `PTYDriver.read_until_idle` (`pty_driver.py:513`), default `None` (preserves current
  behavior; note the working-tree already has an in-flight edit to `pty_driver.py` —
  coordinate). Invoke it once per inner read poll, wrapped so a raising callback never
  breaks the read loop. Thread the existing granite freshness callback (the
  `bridge_adapter` `last_pty_read_loop_at` writer) through the granite read path so
  mid-turn reads stamp `last_pty_read_loop_at`. Replace the stale gap comment at
  `container.py:1092-1098` once the callback is wired. Note #1688's `_await_turn_end`
  already fires `_fire_pty_read` per poll-tick on the hook-driven path (`container.py:1200`);
  Gap B closes the remaining `_cycle_idle` / `read_until_idle`-inner-loop window for the
  idle-fallback path.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The CLI hooks are fail-silent by contract (they must never crash a `claude` child). Every new AgentSession write in `.claude/hooks/*` is wrapped so a Redis/resolution failure logs and returns without raising — assert the hook still exits 0 when the AgentSession is unresolvable (empty/None sidecar). No new bare `except: pass` may swallow silently without a logged debug/warning.
- [ ] `read_until_idle` per-iteration callback raising → caught, does not break the read loop (test).

### Empty/Invalid Input Handling
- [ ] CLI hook with missing/blank `session_id` or absent sidecar → writes nothing, exits 0 (test).
- [ ] `read_until_idle` per-iteration callback param `None` → behavior byte-identical to today (test).

### Error State Rendering
- [ ] Gap A: a unit test proves `_check_tool_timeout` no longer short-circuits for a granite session once `current_tool_name` is set and `last_tool_use_at` is a `datetime` — and DOES still short-circuit if `last_tool_use_at` is written as a float (regression guard against the CONCERN 4 type trap).

## Test Impact

- [ ] `tests/unit/test_pre_tool_use_liveness_writes.py` — UPDATE/EXTEND (also touched by #1821): add a case asserting the CLI-hook path writes `current_tool_name` / `last_tool_use_at` (as a `datetime`), not only the SDK path. Coordinate with #1821's edits to the same file.
- [ ] ADD `tests/unit/granite_container/test_cli_hook_liveness_writes.py` — assert the CLI hooks populate the fields on a sidecar-resolved session, that `last_tool_use_at` is a `datetime` (not a float), and that the hooks fail-silent (exit 0) on an unresolvable session.
- [ ] ADD `tests/unit/granite_container/test_read_until_idle_per_iteration.py` — assert the per-iteration callback fires per inner poll, is a no-op when `None`, and a raising callback does not break the loop.
- [ ] `tests/granite_faults/scenarios.py::silent_no_progress_tail` (class-6, 304-314) — UPDATE: the comment "No detector is wired here — out of scope (#1688 / No-Gos)" is stale for the read-loop-freshness dimension; extend the class-6 assertion to verify the per-iteration callback stamps `last_pty_read_loop_at` mid-turn (Gap B). Do NOT assert a `granite_wedged` recovery transition here (that is #1820's kill scope, out of scope).

No existing test is deleted — both fixes are additive to existing behavior.

## Rabbit Holes

- **Do NOT re-wire Gap 1 (`granite_wedged` → recovery).** It already actuates via `stall_advisory._maybe_recover` (#1768/#1773), gated by `stall_recovery_enabled` (dry-run default). Re-wiring it — especially into `session_health.py`, which never calls the classifier — ships a no-op or a duplicate ladder. The residual (PTY process-group reap) is #1820's/#1816's scope.
- **Do NOT call `record_tool_boundary` from the CLI hooks for Gap A.** It resolves the session via `os.environ["AGENT_SESSION_ID"]`, which is unset in the granite child env; it would silently no-op. Set fields on the sidecar-resolved session directly.
- **Do NOT write `last_tool_use_at` with `time.time()`.** It is a `DatetimeField`; `_check_tool_timeout` requires `isinstance(last_at, datetime)` and short-circuits on a float, leaving the tier loop unarmed. Use `datetime.now(tz=UTC)`.
- **Do NOT add a kill ladder.** This plan touches no kill path. The PTY teardown for a wedged granite session is #1820's/#1816's job.
- **Do NOT rewrite `_await_turn_end` / the #1688 hook-driven path.** Gap B only adds the per-iteration callback to the idle-fallback read loop.
- **Gap A contingency (only if the build-time empirical check fails):** if `claude --settings` turns out to *override* the repo PostToolUse hook for granite children (spike-1 says it does not, high confidence), the fallback is a dedicated liveness hook registered in `hook_edge.generate_hook_settings`. Do not pre-build this.

## Risks

### Risk 1: Shared-file collision with #1821 on `.claude/hooks/pre_tool_use.py`
**Impact:** Two uncoordinated edits to `main()` produce a merge conflict or double-resolution of the AgentSession.
**Mitigation:** Land Gap A as ONE coordinated hook edit with #1821. Whoever builds second reuses the AgentSession resolution the first added, appending only the liveness-field writes. If #1821 lands first, Gap A is a ~3-line append.

### Risk 2: CONCERN 4 type trap silently disarms the tier loop
**Impact:** If Gap A writes `last_tool_use_at` as a float (mirroring `_update_agent_session`'s `updated_at = time.time()` idiom), `_check_tool_timeout`'s `isinstance(last_at, datetime)` gate (`session_health.py:378`) short-circuits and the tier loop never arms — a silent no-op that passes a naive "field is populated" test.
**Mitigation:** Write `datetime.now(tz=UTC)`. Add a regression test asserting `_check_tool_timeout` arms with a `datetime` value and short-circuits with a float value (Failure Path Test Strategy).

### Risk 3: `record_tool_boundary` reuse silently no-ops
**Impact:** "Just call `record_tool_boundary`" no-ops for granite because it resolves via `os.environ["AGENT_SESSION_ID"]`, unset in the granite child env.
**Mitigation:** Set fields directly on the sidecar-resolved session (the `_load_agent_session_sidecar` → `get_by_id` path). Add a test asserting the fields populate when `AGENT_SESSION_ID` is absent but the sidecar resolves.

## Race Conditions

### Race 1: CLI post-hook clears `current_tool_name` while the health loop reads it
**Location:** `.claude/hooks/post_tool_use.py::_update_agent_session` (write) vs `session_health.py::_check_tool_timeout:374-379` (read).
**Trigger:** Health loop samples `current_tool_name` at the instant the post-hook clears it to None.
**Data prerequisite:** `last_tool_use_at` must be written atomically alongside `current_tool_name` so a cleared name always carries a fresh timestamp.
**State prerequisite:** The tier loop treats a null tool name as "no active tool" (its behavior at 374-376 — returns None).
**Mitigation:** Save both fields in one `save(update_fields=[...])` call (Popoto persists them together). The read side handles null gracefully; the worst case is one missed sample, self-corrected on the next tool call. No lock needed.

## No-Gos (Out of Scope)

- [ALREADY-SHIPPED #1768/#1773 · DEFERRED-RESIDUAL #1820] **Gap 1 — `granite_wedged` → recovery.** The *decision* already actuates via `stall_advisory._maybe_recover` (gated by `stall_recovery_enabled`, dry-run default). The only residual — reaping the granite PTY **process group** (`_kill_agent_session` does a single-PID kill, not `os.killpg`) — is #1820's progress-deadline cancel scope / the #1816 `container` `killpg` seam. This plan adds no actuation and does not touch `session_health.py`.
- [SEPARATE-SLUG #1820] The progress-deadline cancel scope + fd-level PTY `killpg` teardown that actually terminates a wedged granite session's process group.
- [SEPARATE-SLUG #1821] The synchronous per-tool budget backstop (live-but-useless loop) and per-tool budget enforcement in the CLI hook. Gap A shares the same hook file but only adds liveness-field writes, not budget enforcement.
- [ORDERED] Building the Gap A contingency liveness hook inside `hook_edge.generate_hook_settings` — blocked on the build-time empirical `--settings` merge check FAILING (spike-1 says it will not).

## Update System

No update system changes required — both fixes edit existing files
(`.claude/hooks/pre_tool_use.py`, `.claude/hooks/post_tool_use.py`,
`agent/granite_container/pty_driver.py` + `container.py`/`bridge_adapter.py`). No new
dependencies, config files, or Popoto models are introduced (the liveness/freshness
fields already exist on `AgentSession`, so no `scripts/update/migrations.py` entry is
needed). The `.claude/hooks/*` edits propagate via the existing repo checkout on each
machine; the generated `--settings` file is produced at runtime by
`hook_edge.generate_hook_settings` (unchanged).

## Agent Integration

No agent integration required — this is a bridge/worker-internal reliability fix. No new
CLI entry point, no `mcp_servers/` or `.mcp.json` change, and the bridge
(`bridge/telegram_bridge.py`) needs no new import. The only "hook" surface touched is the
CLI hooks that granite `claude` children already run; those are configuration the harness
exercises, not an agent-invokable tool. Integration coverage comes from the granite
failure-simulation harness (Substrate A unit + Substrate B ollama E2E).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — add a subsection to the recovery/observability coverage describing the two now-wired signals (CLI-hook liveness fields arming the #1270 tier loop; per-iteration `read_until_idle` callback refreshing `last_pty_read_loop_at`), and state that `granite_wedged` → recovery already actuates via `stall_advisory` (#1768/#1773, gated by `stall_recovery_enabled`) with PTY process-group teardown owned by #1820/#1816.
- [ ] Update the `docs/features/README.md` index entry for granite-pty-production to mention the CLI-hook liveness fields and the per-iteration PTY-read callback.

### Inline Documentation
- [ ] Replace the stale per-iteration-gap comment at `container.py:1092-1098` once the callback lands.

## Success Criteria

- [ ] After a granite tool call, `current_tool_name` / `last_tool_use_at` are populated on the AgentSession (resolved via the sidecar path, not `AGENT_SESSION_ID`), `last_tool_use_at` is a `datetime`, and a unit test demonstrates `_check_tool_timeout` (`session_health.py:374-379`) no longer short-circuits for a granite session — and DOES short-circuit if the field is a float (CONCERN 4 regression guard).
- [ ] Mid-turn PTY reads refresh `last_pty_read_loop_at` (unit against `read_until_idle` with a long single turn and the per-iteration callback; class-6 harness assertion extended for the freshness dimension).
- [ ] CLI hooks remain fail-silent (exit 0) when the AgentSession is unresolvable.
- [ ] No new kill path added: `grep` confirms no `killpg`/`SIGKILL` added to `session_health.py` or the CLI hooks; the plan documents Gap 1's actuation is already shipped and its PTY-reap residual belongs to #1820/#1816.
- [ ] Coordinated single edit with #1821 on `.claude/hooks/pre_tool_use.py` recorded and honored in the build.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (cli-hook-liveness)**
  - Name: `gapA-builder`
  - Role: Gap A — CLI-hook `current_tool_name`/`last_tool_use_at` writes (datetime, sidecar-resolved), coordinated with #1821.
  - Agent Type: builder
  - Resume: true

- **Builder (pty-callback)**
  - Name: `gapB-builder`
  - Role: Gap B — per-iteration `read_until_idle` callback + thread the freshness writer.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `wedge-validator`
  - Role: Verify both fixes against the class-6 harness + success criteria; confirm no new kill ladder, the CONCERN 4 datetime guard, and the coordinated #1821 edit.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 builder + validator per the template. The two builds are independent files
(`.claude/hooks/*` vs `pty_driver.py`/`container.py`) and can run in parallel worktrees.

## Step by Step Tasks

### 1. CLI-hook liveness writes (Gap A) — coordinate with #1821
- **Task ID**: build-gapA-liveness
- **Depends On**: none (MERGE-COORDINATE with #1821 if in flight on the same file)
- **Validates**: `tests/unit/test_pre_tool_use_liveness_writes.py` (extend); new `tests/unit/granite_container/test_cli_hook_liveness_writes.py`
- **Informed By**: spike-1 (`.claude/hooks/*` reaches granite children); CONCERN 4 (datetime + sidecar resolution)
- **Assigned To**: gapA-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `from datetime import UTC, datetime` to `.claude/hooks/pre_tool_use.py` and `.claude/hooks/post_tool_use.py`.
- In `pre_tool_use.py::main` (:61), resolve the AgentSession via the sidecar path (reuse #1821's resolution if present); set `current_tool_name = <tool>` + `last_tool_use_at = datetime.now(tz=UTC)`; `save(update_fields=["current_tool_name","last_tool_use_at"])`; fail-silent on unresolvable session.
- In `post_tool_use.py::_update_agent_session` (:443-494), add `current_tool_name=None` + `last_tool_use_at=datetime.now(tz=UTC)` to the `save(update_fields=[...])` at :494.
- Do NOT call `record_tool_boundary` (it no-ops on unset `AGENT_SESSION_ID`); set fields on the sidecar-resolved session directly.
- Build-time empirical check: assert the fields populate (as a `datetime`) on a real granite session under the pinned `claude`.

### 2. Per-iteration `read_until_idle` callback (Gap B)
- **Task ID**: build-gapB-callback
- **Depends On**: none
- **Validates**: new `tests/unit/granite_container/test_read_until_idle_per_iteration.py`; class-6 assertion (freshness dimension)
- **Assigned To**: gapB-builder
- **Agent Type**: builder
- **Parallel**: true
- Add an optional per-read-iteration callback param to `PTYDriver.read_until_idle` (`pty_driver.py:513`), default `None` (preserves current behavior); coordinate with the in-flight working-tree edit to `pty_driver.py`. Wrap the callback so a raising callback never breaks the read loop.
- Thread the granite freshness writer (the `bridge_adapter` `last_pty_read_loop_at` writer) so mid-turn reads stamp `last_pty_read_loop_at`.
- Replace the stale gap comment at `container.py:1092-1098`.

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-gapA-liveness, build-gapB-callback, document-feature
- **Assigned To**: wedge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the class-6 harness red→green; run all new unit tests; run the Verification table.
- Confirm no new kill ladder (`grep`), the CONCERN 4 datetime guard, coordinated #1821 edit, and fail-silent CLI hooks.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-gapA-liveness, build-gapB-callback
- **Assigned To**: wedge-validator (or documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` recovery/observability section + `docs/features/README.md` entry.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/granite_faults tests/unit/granite_container -q` | exit code 0 |
| CLI pre-hook writes tool name | `grep -c "current_tool_name" .claude/hooks/pre_tool_use.py` | output > 0 |
| CLI post-hook writes last_tool_use_at | `grep -c "last_tool_use_at" .claude/hooks/post_tool_use.py` | output > 0 |
| last_tool_use_at written as datetime (not time.time) | `grep -c "datetime.now" .claude/hooks/pre_tool_use.py` | output > 0 |
| read_until_idle has per-iteration callback | `grep -cE "def read_until_idle" agent/granite_container/pty_driver.py` | output > 0 |
| No new kill ladder introduced | `grep -rn "killpg\|SIGKILL" agent/session_health.py .claude/hooks/pre_tool_use.py .claude/hooks/post_tool_use.py` | no ADDED match |
| Gap 1 not re-wired into session_health | `grep -c "classify_session_stall" agent/session_health.py` | output == 0 |
| Class-6 injector freshness assertion updated | `grep -c "last_pty_read_loop_at" tests/granite_faults/scenarios.py` | output > 0 |
| Lint clean | `python -m ruff check .claude/hooks/pre_tool_use.py .claude/hooks/post_tool_use.py agent/granite_container/pty_driver.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/ agent/granite_container/pty_driver.py` | exit code 0 |

The "Gap 1 not re-wired into session_health" row is an anti-criterion for the #1768/#1820
No-Go: `session_health.py` must NOT gain a `classify_session_stall` call (the actuation
already lives in `stall_advisory`, and the PTY reap is #1820's).

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER 1 | war-room | `granite_wedged` reprieved by alive gate in `_apply_recovery_transition` | Freshness Check | Premise inverted: `session_health.py:1962` reprieve is `no_progress`-only (opt-in), not a skip-set. Moot — Gap 1 deferred, session_health untouched. |
| BLOCKER 2 | war-room | Gap 1 mis-sized: `session_health` never calls `classify_session_stall` | Problem / No-Gos | Confirmed. Actuation already lives in `stall_advisory` (#1768/#1773) and already handles `granite_wedged`. Gap 1 dropped; no health-loop wiring, no new per-tick I/O. |
| CONCERN 3 | war-room | `granite_wedged` may be unreachable post-#1688 if granite emits `turn_start` | Freshness Check | Verified unfounded: granite emits zero `turn_start` telemetry; probe reachable. Recorded for the deferred Gap 1. |
| CONCERN 4 | war-room | `last_tool_use_at` datetime-vs-float trap; `record_tool_boundary` env no-op | Technical Approach / Risks / Success Criteria | Write `datetime.now(tz=UTC)`; set fields on the sidecar-resolved session, not via `record_tool_boundary`. Regression guard added. |
| CONCERN 5 | war-room | Gap 1 no standalone value; open question shipped | No-Gos / Problem | Resolved via option (b): defer Gap 1 to #1820, ship Gap A + Gap B standalone. Open Questions removed. |
