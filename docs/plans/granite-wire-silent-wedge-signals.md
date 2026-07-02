---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1843
last_comment_id:
---

# Granite silent wedges: wire existing signals (no new detector)

## Problem

Granite (the PTY-driven PM/Dev session runner, `agent/granite_container/`) wedges
silently in production. The session-health machinery built to protect these
sessions already computes the right signals, but three of them are not wired to
anything that acts. This is a deliberately narrow wiring bug, not a new detector.

**Current behavior:**

- **Gap 1 — `granite_wedged` is advisory-only.** The #1724 mid-run wedge detector
  computes a `granite_wedged` verdict (fresh `last_pty_read_loop_at` + stale
  `last_pty_activity_at`) in `agent/session_stall_classifier.py` (verdict string at
  line 298, block 285-306). That verdict is consumed ONLY by
  `reflections/stall_advisory.py:152` (a log/advisory). `agent/session_health.py:29-32`
  imports only the `NEVER_STARTED_*` constants — no kill/recovery path ever sees
  `granite_wedged`. The signal exists; nothing actuates on it.
- **Gap 2 — liveness fields are structurally dead for granite.** `current_tool_name` /
  `last_tool_use_at` are written only by the SDK in-process hooks
  (`agent/hooks/pre_tool_use.py:391`, `agent/hooks/post_tool_use.py:74` →
  `agent/hooks/liveness_writers.py:81::record_tool_boundary`). Granite's PM/Dev
  `claude` children run the **CLI hooks** (`.claude/settings.json:27,74`), whose
  `_update_agent_session` (`.claude/hooks/post_tool_use.py:443-494`, save at :494)
  writes only `updated_at` + `tool_call_count`. Consequence: the #1270 tool-timeout
  tier loop short-circuits on a null tool name (`agent/session_health.py:374-376`) and
  therefore **never fires for granite**.
- **Gap 3 — PTY activity samples too coarsely.** The `on_pty_read` liveness callback
  fires once per `_cycle_idle` return (`agent/granite_container/container.py:1099-1101`),
  not per inner read iteration inside `PTYDriver.read_until_idle`. The documented gap
  comment survives at `container.py:1092-1098`. A wedge *inside* a long idle-path turn
  refreshes `last_pty_read_loop_at` nothing until the cycle window elapses.

**Desired outcome:**

The three already-computed signals actuate:
1. `granite_wedged` routes into the recovery decision (`reason_kind="granite_wedged"`),
   with the PTY teardown owned by an existing kill path (no fourth ladder).
2. The CLI hooks populate `current_tool_name` / `last_tool_use_at` so the existing
   #1270 tool-timeout tiers arm for granite.
3. Mid-turn PTY reads refresh `last_pty_read_loop_at` before the cycle window elapses.

All three are red-first testable against the #1837 harness class-6
"silent no-progress tail" injector (`tests/granite_faults/scenarios.py:304-314`).

## Freshness Check

**Baseline commit:** `0297da0d` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-02T04:31:21Z
**Disposition:** Minor drift

The issue's claims all still hold, but **PR #1688 ("hook-driven turn returns", merge
`0297da0d`) landed AFTER the issue was filed** and reshaped granite's turn mechanism
and hook wiring. Every file:line reference in the issue was re-verified at HEAD; several
drifted. None of the three gaps was fixed by #1688 — all three problems persist.

**File:line references re-verified:**
- `session_stall_classifier.py:271-306` — `granite_wedged` verdict — **holds**; verdict string at line 298, block 285-306.
- `reflections/stall_advisory.py:152` — sole consumer — **holds** (line-exact).
- `session_health.py:29-32` — imports only `NEVER_STARTED_*` — **holds** (line-exact).
- `session_health.py:365` — #1270 tool-name null short-circuit — **drifted → 374-376** (`if not tool_name or not isinstance(tool_name, str): return None`); tier machinery ~292-387, `_check_tool_timeout` at 362-387.
- `agent/hooks/pre_tool_use.py:391` / `post_tool_use.py:74` → `record_tool_boundary` — **holds**; defined in `agent/hooks/liveness_writers.py:81`.
- `.claude/hooks/post_tool_use.py:443-494` — `_update_agent_session` writes only `updated_at`+`tool_call_count` (save at :494) — **holds**; `.claude/hooks/pre_tool_use.py` has no AgentSession write today.
- `.claude/settings.json:27,74` — CLI hook registration — **holds** (line-exact).
- `container.py:942-947` — `on_pty_read` once-per-cycle + gap comment — **drifted/changed by #1688**: gap comment now `1092-1098`; `_cycle_idle` fires callback at `1099-1101`; #1688 added a hook-driven `_await_turn_end` (1138-1271) that fires `_fire_pty_read` per poll-tick at `1200`. The true per-inner-read-iteration callback inside `read_until_idle` (`pty_driver.py:513`) still does NOT exist.
- `bridge_adapter.py:756-778` — #1724 freshness-field writer — **drifted → 849-876** (`last_pty_read_loop_at` unconditional at 862-863; `last_pty_activity_at` on buffer change at 874-876).
- `bridge_adapter.py:572` — `asyncio.to_thread(container.run)` — **drifted → 648**.
- `bridge_adapter.py:722-728` — `startup_unresolved` operator alert — **drifted → 789-798**.
- `session_executor.py:1393` — claimed `_apply_recovery_transition` kills via `claude_pid` — **misattributed**: `:1393` is `_on_sdk_started` stamping `claude_pid`; `_apply_recovery_transition` actually lives at **`agent/session_health.py:1848`**, kills by cancelling `handle.task` then SIGTERM→SIGKILL against `claude_pid` (~2016-2050). The claim's substance holds: `claude_pid` is stamped only on the SDK path, so a granite PTY session (via `to_thread(container.run)`) has no `claude_pid` and is unreachable by that kill.
- `scenarios.py:304-314` — class-6 injector — **holds**; asserts silence observable at the `read_until_idle`/`IdleResult` seam only, with a comment "No detector is wired here — out of scope (#1688 / No-Gos)". This issue is the actuation; the comment + assertion get updated.
- `granite_classifier.py:50` — `classify_pm_prefix` pure regex — **holds** (function at :162; :50 is a docstring line asserting the property).

**Cited sibling issues/PRs re-checked:**
- #1724 CLOSED (2026-06-18), #1270 CLOSED (2026-05-05), #1837/#1839 CLOSED/MERGED (2026-07-01), #1688 CLOSED/MERGED (2026-07-02), #1816 CLOSED.
- #1820 (`slot-lease-progress-deadline`, plan Ready) and #1821 (`out-of-domain-recovery-tool-budget`, plan Ready) — **OPEN, in flight**. These are the two coordination targets. See Deconfliction below.

**Commits on main since issue filed (touching referenced files):**
- `0297da0d` #1688 hook-driven turn returns — reshaped `container.py` turn methods + added `hook_edge.py`/`hook_forwarder.py` + per-session `--settings`. Changed line numbers, did NOT fix any gap. Full impact folded into Technical Approach.

**Active plans in `docs/plans/` overlapping this area:**
- `slot-lease-progress-deadline.md` (#1820) — owns the progress-deadline cancel scope + `killpg` PTY teardown. Gap 1 routes through it (deconflicted, not merged into it).
- `out-of-domain-recovery-tool-budget.md` (#1821) — its Fix #6 edits `.claude/hooks/pre_tool_use.py::main` for granite-PTY children and resolves the AgentSession sidecar there. Gap 2 shares that file; land as one coordinated edit.
- `granite_hook_driven_turn_returns.md` (#1688, now merged) — the source of the drift; consumed as context, no overlap in scope.

**Notes:** Disposition is Minor drift, not Major: #1688 did not fix or subsume any gap (verified: `granite_wedged` still unwired, liveness fields still unwritten, per-iteration callback still absent). All corrected line numbers are folded into Technical Approach and the tasks below.

## Prior Art

- **#1724 / PR #1728**: "recover stalled never_started and mid-run-wedge granite sessions" — built the `granite_wedged` verdict and the `last_pty_read_loop_at`/`last_pty_activity_at` freshness fields. Succeeded at *detecting*; left the verdict advisory-only. This issue completes the actuation.
- **#1270 / PR (merged 2026-05-05)**: per-tool timeout tiers with per-tier counters — the tier loop this issue arms for granite. It requires `current_tool_name` non-null, which is why granite never triggers it today.
- **#1789 / #1798**: gated `never_started` and default-tier `tool_timeout` kills on PTY liveness — established the pattern of consulting PTY liveness before killing granite. Gap 1's recovery must respect the same liveness gating (do not kill a session whose PTY is genuinely progressing).
- **#1815**: "liveness-vs-progress wedge survives because a parked loop never exits" — the exact production shape this issue's Gap 1 addresses.
- **#1816 / PR #1832**: `supervise()` + scoped process-group teardown (`os.killpg` in `container.py`) — provides the existing PTY-teardown API that #1820's cancel scope (and therefore Gap 1's kill) reuses. No fourth ladder needed.
- **#1688 / PR #1847**: hook-driven turn returns — introduced the per-session `--settings` + `hook_forwarder.py` architecture. Relevant to Gap 2 (confirms granite children run generated settings *in addition to* the repo `.claude/settings.json`).

No prior attempt failed at these three wiring fixes; they were simply never done (the detector work stopped at detection). No "Why Previous Fixes Failed" section needed.

## Research

External research covered Claude Code hook/settings resolution semantics, because
Gap 2's fix location depends on whether granite children (launched with
`claude --settings <generated>`) still fire the repo `.claude/settings.json` hooks.

**Queries / sources used:**
- Claude Code hooks doc (`docs.claude.com/en/docs/claude-code/hooks`)
- Claude Code settings doc (`docs.claude.com/en/docs/claude-code/settings`)

**Key findings:**
- **Hooks merge additively across settings sources.** "They don't override each other;
  they merge additively." Identical command hooks are deduplicated by command string + args.
  So a `--settings` file *adds* hooks; it does not replace the project's `.claude/settings.json`
  hooks — provided `--settings` participates in the same additive merge.
- **The `--settings` CLI flag's precedence is a documented blind spot** — the flag is not
  listed in either doc's precedence order. The docs alone cannot confirm that granite children
  still fire the repo PostToolUse hook. This is resolved by spike-1 below (cross-plan evidence)
  and a build-time empirical confirmation.

## Spike Results

### spike-1: Do granite `--settings` children still fire the repo `.claude/hooks/*`?
- **Assumption**: "Editing `.claude/hooks/post_tool_use.py` / `pre_tool_use.py` reaches granite PM/Dev PTY children (i.e. `claude --settings <generated>` merges rather than overrides the project `.claude/settings.json` hooks)."
- **Method**: code-read + cross-plan corroboration (docs are ambiguous on the flag).
- **Finding**: **CONFIRMED (high confidence).** Two independent lines of evidence: (a) the Claude Code hooks doc states hooks merge additively across sources with dedup; (b) #1821's already-critiqued, Ready plan (`out-of-domain-recovery-tool-budget.md`) explicitly treats `.claude/hooks/pre_tool_use.py::main` as "the interactive `claude` TUI / granite-PTY CLI hook" and wires per-tool budget enforcement there — that entire branch would be dead code if `--settings` suppressed the project hooks, and it passed critique. The generated settings (`hook_edge.generate_hook_settings`) register only forwarder hooks (Stop/SubagentStop/Notification/PermissionRequest/PreToolUse-matcher-`AskUserQuestion`/PreCompact/SessionStart) → they ADD to, not replace, the repo's `*`-matcher Pre/PostToolUse hooks.
- **Confidence**: high.
- **Impact on plan**: Gap 2's fix location is the repo `.claude/hooks/*` (as the issue states), coordinated with #1821. A **build-time empirical confirmation** (Task 2 red-first) is retained as belt-and-suspenders: assert `current_tool_name`/`last_tool_use_at` are populated on a granite session's AgentSession after a real tool call under the pinned `claude`. If the empirical check ever shows the repo PostToolUse hook does NOT fire for granite (override semantics), the fallback is to register a dedicated liveness hook in `hook_edge.generate_hook_settings` calling `record_tool_boundary` — documented in Rabbit Holes as the contingency, not the primary path.

## Data Flow

Gap 2 (tool-boundary liveness) end-to-end, the most cross-component of the three:

1. **Entry point**: A granite PM/Dev `claude` child invokes a tool inside its PTY.
2. **CLI hook fires**: Claude Code runs the PreToolUse then PostToolUse hooks registered in the merged settings (`.claude/settings.json:27,74` + generated `--settings`), i.e. `.claude/hooks/pre_tool_use.py::main` and `.claude/hooks/post_tool_use.py::main`.
3. **AgentSession resolution**: The CLI hook resolves the granite session's `AgentSession` from the sidecar / `AGENT_SESSION_ID` env (the `_load_agent_session_sidecar` / `AgentSession.get_by_id` path already used by `.claude/hooks/post_tool_use.py:464-476`).
4. **Liveness write (NEW)**: pre-hook sets `current_tool_name = <tool>` + `last_tool_use_at = now`; post-hook clears `current_tool_name = None` + refreshes `last_tool_use_at` (mirroring `record_tool_boundary`'s Pre=set / Post=clear contract).
5. **Health loop reads**: `agent/session_health.py::_check_tool_timeout` (362-387) no longer short-circuits at 374-376 (tool name now non-null) → the #1270 tier budget arms for the granite session.
6. **Output**: A granite session stuck inside a single tool call past its tier budget now transitions through recovery instead of being invisible.

Gap 1 flow: `classify_session_stall` → `granite_wedged` verdict → `session_health` health loop consults the verdict → `_apply_recovery_transition(reason_kind="granite_wedged")`. Gap 3 flow: `read_until_idle` inner poll → per-iteration callback → `bridge_adapter` freshness writer (849-876) refreshes `last_pty_read_loop_at` mid-turn.

## Architectural Impact

- **New dependencies**: none. All three fixes reuse existing symbols and files.
- **Interface changes**: `PTYDriver.read_until_idle` gains an optional per-iteration callback param (default `None` → identical pre-existing behavior). `_apply_recovery_transition` gains a new `reason_kind` value `"granite_wedged"` (additive; no signature change if `reason_kind` is already a free-form string — confirm at build).
- **Coupling**: Gap 1 adds a read-only import of the classifier's `granite_wedged` handling into `session_health.py` (it already imports `NEVER_STARTED_*` from the same module — same coupling direction, no new module dependency). Gap 2 adds AgentSession writes to the CLI hooks (coordinated with #1821, which is already adding AgentSession resolution there).
- **Data ownership**: unchanged. The freshness/liveness fields are already owned by `AgentSession`; this issue only writes fields that were dead for granite.
- **Reversibility**: high. Gap 3's callback is opt-in (param default None). Gap 2 is additive field writes. Gap 1 is a single new branch in the health loop, guarded by the verdict.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer (coordination-sensitive: two Ready sibling plans share files).

**Interactions:**
- PM check-ins: 1-2 (the #1820/#1821 landing-order coordination is the alignment cost, not the coding).
- Review rounds: 1 (shared-file edits with #1821 need a review that confirms the coordinated merge is clean).

The coding is small. The appetite is Medium because the communication overhead —
sequencing against two in-flight plans that touch the same kill path and the same
CLI hook file — is the real cost.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #1816 scoped PTY teardown exists | `grep -c "killpg" agent/granite_container/container.py` | Gap 1 reuses this existing kill path (no fourth ladder) |
| Class-6 harness injector present | `grep -c "silent_no_progress_tail" tests/granite_faults/scenarios.py` | Red-first test substrate for all three fixes |
| `granite_wedged` verdict present | `grep -c "granite_wedged" agent/session_stall_classifier.py` | Gap 1's source signal |

Run via `python scripts/check_prerequisites.py docs/plans/granite-wire-silent-wedge-signals.md`.

## Solution

### Key Elements

- **Gap 1 actuation**: `agent/session_health.py` consumes the `granite_wedged` verdict from `session_stall_classifier` and routes it into `_apply_recovery_transition(reason_kind="granite_wedged")`, gated on the same PTY-liveness respect as #1789/#1798. The PTY teardown itself is owned by an existing path (#1820's cancel scope / the #1816 `killpg` container teardown) — this issue adds NO new kill ladder.
- **Gap 2 liveness writes**: the CLI hooks (`.claude/hooks/pre_tool_use.py::main`, `.claude/hooks/post_tool_use.py::_update_agent_session`) also set `current_tool_name` / `last_tool_use_at`, mirroring `record_tool_boundary`'s Pre=set / Post=clear contract, reusing the AgentSession resolution #1821 is adding to the same file.
- **Gap 3 per-iteration callback**: `PTYDriver.read_until_idle` gains an optional per-read-iteration callback; the granite read path passes the existing bridge_adapter freshness writer so `last_pty_read_loop_at` refreshes mid-turn.

### Flow

Granite session wedges mid-tool → CLI PostToolUse hook has stamped `current_tool_name` →
#1270 tier loop arms → budget expiry → `_apply_recovery_transition(reason_kind="granite_wedged" | tool_timeout)` → existing #1820/#1816 PTY teardown → session recovers instead of hanging invisibly.

### Technical Approach

- **Gap 1 (priority 1):** In `session_health.py`, where the health loop classifies a
  session's stall, add a branch: when `classify_session_stall(...)` returns
  `reason == "granite_wedged"`, call `_apply_recovery_transition(session, reason_kind="granite_wedged", ...)`.
  Confirm `_apply_recovery_transition` (at `session_health.py:1848`) accepts a free-form
  `reason_kind`; if it enumerates reasons, add `granite_wedged`. **Document in the plan and
  in a code comment that `_apply_recovery_transition`'s `claude_pid` SIGTERM/SIGKILL is a
  no-op for granite** (no `claude_pid` on the PTY path), so the effective PTY teardown is
  owned by #1820's progress-deadline cancel scope / the #1816 `killpg` container teardown.
  The transition still lands the AgentSession row terminal / re-queueable; the PTY-child
  reap is the existing path's job.
- **Gap 2 (priority 2):** In `.claude/hooks/pre_tool_use.py::main`, after resolving the
  AgentSession (reuse #1821's resolution if landed; otherwise resolve via the
  `.claude/hooks/post_tool_use.py:464-476` sidecar path), set `current_tool_name` +
  `last_tool_use_at` and save those fields. In
  `.claude/hooks/post_tool_use.py::_update_agent_session`, add `current_tool_name = None`
  + `last_tool_use_at = now` to the existing `save(update_fields=[...])` at :494. **Land as
  one coordinated edit with #1821** — see Deconfliction. Mirror the `record_tool_boundary`
  contract (`agent/hooks/liveness_writers.py:81`): Pre sets the name, Post clears it.
- **Gap 3 (priority 3):** Add an optional `on_read_iteration` (or reuse the existing
  callback name) param to `PTYDriver.read_until_idle` (`pty_driver.py:513`), default `None`
  (preserves pre-#1688 behavior — the `pty_driver.py` diff in the working tree already
  touches this file, coordinate). Invoke it once per inner read poll. Thread the existing
  granite freshness callback (`bridge_adapter.py:849-876` region) through so mid-turn reads
  stamp `last_pty_read_loop_at`. Remove/replace the stale gap comment at
  `container.py:1092-1098` once the callback is wired. Note #1688's `_await_turn_end`
  already fires `_fire_pty_read` per poll-tick on the hook-driven path (1200); Gap 3 closes
  the remaining `_cycle_idle`/`read_until_idle`-inner-loop window for the idle-fallback path.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The CLI hooks are fail-silent by contract (they must never crash a `claude` child). Any new AgentSession write in `.claude/hooks/*` must be wrapped so a Redis/resolution failure logs and returns without raising — assert the hook still exits 0 when the AgentSession is unresolvable (empty/None sidecar). No new `except Exception: pass` may swallow silently without a logged warning.
- [ ] `_apply_recovery_transition` for granite: assert the granite branch logs a WARNING/CRITICAL with the stall age when it fires (mirrors #1820's deadline-cancel logging), never a silent transition.

### Empty/Invalid Input Handling
- [ ] CLI hook with missing/blank `AGENT_SESSION_ID` or absent sidecar → writes nothing, exits 0 (test).
- [ ] `read_until_idle` per-iteration callback: callback param `None` → behavior byte-identical to today (test). Callback raising → caught, does not break the read loop.

### Error State Rendering
- [ ] A `granite_wedged` recovery must surface a `session_event` / operator-visible signal (not swallowed) — assert the event is recorded.

## Test Impact

- [ ] `tests/granite_faults/scenarios.py::silent_no_progress_tail` (class-6, 304-314) — UPDATE: the injector comment "No detector is wired here — out of scope (#1688 / No-Gos)" is now stale; extend the class-6 assertion to verify the actuation (recovery transition fires with `reason_kind="granite_wedged"`), which is exactly what the harness was built to TDD.
- [ ] `tests/unit/test_pre_tool_use_liveness_writes.py` — UPDATE/EXTEND (this file is also touched by #1821): add a case asserting the CLI-hook path writes `current_tool_name`/`last_tool_use_at`, not only the SDK path. Coordinate with #1821's edits to the same file.
- [ ] `.claude/hooks/pre_tool_use.py` / `post_tool_use.py` — no dedicated CLI-hook liveness test today (per #1821 recon); ADD `tests/unit/granite_container/test_cli_hook_liveness_writes.py` (or extend an existing CLI-hook test) asserting the CLI hooks populate the fields and fail-silent on unresolvable session.
- [ ] Existing `session_health` tier tests — verify the new `granite_wedged` branch does not regress the `never_started` / `tool_timeout` PTY-liveness gating from #1789/#1798 (UPDATE if a test asserts the exhaustive set of recovery reasons).

No existing test is deleted — all three fixes are additive to existing behavior.

## Rabbit Holes

- **Do NOT build a new silent-failure detector.** The issue explicitly narrows scope to wiring the three existing signals. The verdict, the freshness fields, and the tier loop all already exist.
- **Do NOT add a fourth kill ladder.** The three existing ladders (container `_close_pair_and_reap`, orphan reaper in `session_health.py`, #1820's planned progress-deadline scope) are sufficient. Gap 1 routes through them.
- **Do NOT chase the live-but-useless loop** (frames keep painting, no real progress). That is #1821 Fix #6's job, explicitly out of scope here.
- **Gap 2 contingency (only if the build-time empirical check fails):** if `claude --settings` turns out to *override* the repo PostToolUse hook for granite children (spike-1 says it does not, high confidence), the fallback is a dedicated liveness hook registered in `hook_edge.generate_hook_settings` calling `record_tool_boundary` from a NON-stdlib-only hook script. Do not pre-build this; it is the documented contingency, not the plan.
- **Do NOT rewrite `_await_turn_end` / the #1688 hook-driven path.** Gap 3 only adds the per-iteration callback to the idle-fallback read loop.

## Risks

### Risk 1: Shared-file collision with #1821 on `.claude/hooks/pre_tool_use.py`
**Impact:** Two uncoordinated edits to `main()` produce a merge conflict or double-resolution of the AgentSession.
**Mitigation:** Land Gap 2 as ONE coordinated hook edit with #1821 (Deconfliction below). Whoever builds second reuses the AgentSession resolution the first added, appending only the liveness-field writes. If #1821 lands first, Gap 2 is a 3-line append.

### Risk 2: `granite_wedged` recovery kills a genuinely-progressing session
**Impact:** A session whose PTY is actually advancing gets torn down (false positive), regressing #1789/#1798's liveness gating.
**Mitigation:** The verdict already requires *stale* `last_pty_activity_at` (screen content unchanged) with a fresh read loop — that IS the liveness gate. Gap 3 makes `last_pty_read_loop_at` fresher mid-turn, which if anything reduces false positives. Add a test asserting a steadily-progressing granite session is NOT transitioned.

### Risk 3: `_apply_recovery_transition` no-op kill for granite masks the wedge as "handled"
**Impact:** The transition marks the row recovered but the PTY children survive (no `claude_pid`), so the wedge persists while the dashboard shows "recovered".
**Mitigation:** Document explicitly (AC #4) that the PTY teardown is owned by #1820's cancel scope / #1816 killpg, not by `_apply_recovery_transition`. Until #1820 lands, the granite branch must route the teardown through the existing container `killpg` seam (reachable via the session's container handle) OR the transition must leave the session in a re-queueable state that startup recovery reaps. Make the ownership explicit in code comment + plan.

## Race Conditions

### Race 1: CLI post-hook clears `current_tool_name` while the health loop reads it
**Location:** `.claude/hooks/post_tool_use.py::_update_agent_session` (write) vs `session_health.py::_check_tool_timeout:374-376` (read).
**Trigger:** Health loop samples `current_tool_name` at the instant the post-hook clears it to None.
**Data prerequisite:** `last_tool_use_at` must be written atomically alongside `current_tool_name` so a cleared name always carries a fresh timestamp.
**State prerequisite:** The tier loop must treat a null tool name as "no active tool" (already its behavior at 374-376 — it returns None). A momentary clear simply means "not currently in a tool", which is correct.
**Mitigation:** Save both fields in one `save(update_fields=[...])` call (Popoto persists them together). The read side already handles null gracefully; the worst case is one missed sample, self-corrected on the next tool call. No lock needed.

### Race 2: `granite_wedged` verdict fires concurrently with #1820's progress-deadline cancel
**Location:** `session_health.py` granite branch vs #1820's cancel scope (`agent_session_queue.py:1494`).
**Trigger:** Both decide to tear down the same session in the same window.
**Data prerequisite:** The recovery transition must be idempotent — a second transition on an already-terminal/recovering session is a no-op.
**State prerequisite:** `transition_status` must guard against double-transition.
**Mitigation:** Route Gap 1 through the SAME recovery path #1820 owns (no parallel ladder), so the two cannot issue competing kills. `_apply_recovery_transition` already guards via `transition_status`; confirm it declines cleanly (returns False) if the session is already being cancelled. Add a test for the double-fire.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1820] The progress-deadline cancel scope + fd-level PTY `killpg` teardown that actually terminates a wedged granite session. Gap 1 wires the *decision*; #1820 owns the *kill*.
- [SEPARATE-SLUG #1821] The synchronous per-tool budget backstop (live-but-useless loop) and the per-tool budget enforcement in the CLI hook. Gap 2 shares the same hook file but only adds liveness-field writes, not budget enforcement.
- [ORDERED] Building the Gap 2 contingency liveness hook inside `hook_edge.generate_hook_settings` — blocked on the build-time empirical `--settings` merge check FAILING (spike-1 says it will not). Only pursue if the empirical check contradicts spike-1.

## Update System

No update system changes required — all three fixes edit existing files
(`agent/session_health.py`, `.claude/hooks/*.py`, `agent/granite_container/pty_driver.py`
+ `container.py`/`bridge_adapter.py`). No new dependencies, config files, or Popoto models
are introduced (the liveness/freshness fields already exist on `AgentSession`, so no
`scripts/update/migrations.py` entry is needed). The `.claude/hooks/*` edits propagate via
the existing repo checkout on each machine; the generated `--settings` file is produced at
runtime by `hook_edge.generate_hook_settings` (unchanged).

## Agent Integration

No agent integration required — this is a bridge/worker-internal reliability fix. No new
CLI entry point, no `mcp_servers/` or `.mcp.json` change, and the bridge
(`bridge/telegram_bridge.py`) needs no new import. The only "hook" surface touched is the
CLI hooks that granite `claude` children already run; those are configuration the harness
exercises, not an agent-invokable tool. Integration coverage comes from the granite
failure-simulation harness (Substrate A unit + Substrate B ollama E2E), not from an
agent-invocation test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — add a subsection to the recovery/observability coverage describing the three now-wired signals (`granite_wedged` → recovery, CLI-hook liveness fields, per-iteration `read_until_idle` callback) and explicitly which kill path owns the PTY teardown (#1820 cancel scope / #1816 killpg).
- [ ] Update the `docs/features/README.md` index entry for granite-pty-production to mention `granite_wedged` recovery actuation and CLI-hook liveness fields.

### Inline Documentation
- [ ] Replace the stale per-iteration-gap comment at `container.py:1092-1098` once the callback lands.
- [ ] Add a code comment at the Gap 1 branch documenting the `claude_pid` no-op-for-granite teardown-ownership boundary.

## Success Criteria

- [ ] A session with fresh read-loop + stale screen (class-6 harness injection) transitions through recovery with `reason_kind="granite_wedged"` — red-first against `tests/granite_faults/scenarios.py:304-314`.
- [ ] After a granite tool call, `current_tool_name` / `last_tool_use_at` are populated on the AgentSession, and a unit test demonstrates the #1270 tier check no longer short-circuits on a null tool name for a granite session (`session_health.py:374-376`).
- [ ] Mid-turn PTY reads refresh `last_pty_read_loop_at` (unit against `read_until_idle` with a long single turn and the per-iteration callback).
- [ ] No new kill path added: `grep` confirms no new `killpg`/SIGKILL ladder; the plan + a code comment document that the PTY teardown is owned by #1820's scope / #1816 killpg.
- [ ] Deconfliction with #1820 (kill ownership) and #1821 (shared `.claude/hooks/pre_tool_use.py` edit) recorded in the plan and honored in the build (coordinated single hook edit).
- [ ] CLI hooks remain fail-silent (exit 0) when the AgentSession is unresolvable.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (recovery-wiring)**
  - Name: `gap1-builder`
  - Role: Gap 1 — wire `granite_wedged` into `_apply_recovery_transition`; document teardown ownership.
  - Agent Type: builder
  - Domain: async/concurrency (recovery transitions, `transition_status` idempotency)
  - Resume: true

- **Builder (cli-hook-liveness)**
  - Name: `gap2-builder`
  - Role: Gap 2 — CLI-hook `current_tool_name`/`last_tool_use_at` writes, coordinated with #1821.
  - Agent Type: builder
  - Resume: true

- **Builder (pty-callback)**
  - Name: `gap3-builder`
  - Role: Gap 3 — per-iteration `read_until_idle` callback + thread the freshness writer.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `wedge-validator`
  - Role: Verify all three fixes against the class-6 harness + success criteria; confirm no new kill ladder and coordinated #1821 edit.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 builder + validator per the template. Domain framing (`DOMAIN_FRAMING.md`)
for async/concurrency applies to Gap 1's recovery-transition idempotency.

## Step by Step Tasks

### 1. Wire `granite_wedged` into recovery (Gap 1)
- **Task ID**: build-gap1-recovery
- **Depends On**: none
- **Validates**: `tests/granite_faults/scenarios.py` class-6 assertion (update); new `tests/unit/granite_container/test_granite_wedged_recovery.py`
- **Informed By**: spike-1 (Gap 2 premise, not Gap 1); freshness-check corrected lines
- **Assigned To**: gap1-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/session_health.py`, consume the `granite_wedged` verdict from `session_stall_classifier` in the health loop and call `_apply_recovery_transition(reason_kind="granite_wedged")` (function at `:1848`).
- Add `granite_wedged` to `reason_kind` handling if it is enumerated; otherwise pass through.
- Add a code comment documenting the `claude_pid` no-op-for-granite teardown boundary; route the effective PTY kill through the existing #1816 `killpg` / #1820 scope (no new ladder).
- Respect #1789/#1798 PTY-liveness gating (do not kill a progressing session).

### 2. CLI-hook liveness writes (Gap 2) — coordinate with #1821
- **Task ID**: build-gap2-liveness
- **Depends On**: none (but MERGE-COORDINATE with #1821 if it is in flight on the same file)
- **Validates**: `tests/unit/test_pre_tool_use_liveness_writes.py` (extend); new `tests/unit/granite_container/test_cli_hook_liveness_writes.py`
- **Informed By**: spike-1 (confirmed: `.claude/hooks/*` reaches granite children)
- **Assigned To**: gap2-builder
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/hooks/pre_tool_use.py::main`, resolve the AgentSession (reuse #1821's resolution if present) and set `current_tool_name` + `last_tool_use_at`; fail-silent on unresolvable session.
- In `.claude/hooks/post_tool_use.py::_update_agent_session` (:443-494), add `current_tool_name=None` + `last_tool_use_at=now` to the `save(update_fields=[...])` at :494.
- Mirror `record_tool_boundary` (`agent/hooks/liveness_writers.py:81`) Pre=set / Post=clear contract.
- Build-time empirical check: assert the fields populate on a real granite session under the pinned `claude` (belt-and-suspenders for spike-1).

### 3. Per-iteration `read_until_idle` callback (Gap 3)
- **Task ID**: build-gap3-callback
- **Depends On**: none
- **Validates**: new `tests/unit/granite_container/test_read_until_idle_per_iteration.py`
- **Assigned To**: gap3-builder
- **Agent Type**: builder
- **Parallel**: true
- Add an optional per-read-iteration callback param to `PTYDriver.read_until_idle` (`pty_driver.py:513`), default `None` (preserves current behavior); coordinate with the in-flight working-tree edit to `pty_driver.py`.
- Thread the granite freshness writer (`bridge_adapter.py:849-876` region) so mid-turn reads stamp `last_pty_read_loop_at`.
- Replace the stale gap comment at `container.py:1092-1098`.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-gap1-recovery, build-gap2-liveness, build-gap3-callback, document-feature
- **Assigned To**: wedge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the class-6 harness red→green; run all new unit tests; run the Verification table.
- Confirm no new kill ladder (`grep`), coordinated #1821 edit, and fail-silent CLI hooks.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-gap1-recovery, build-gap2-liveness, build-gap3-callback
- **Assigned To**: wedge-validator (or documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` recovery/observability section + `docs/features/README.md` entry.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/granite_faults tests/unit/granite_container -q` | exit code 0 |
| granite_wedged wired into health | `grep -c "granite_wedged" agent/session_health.py` | output > 0 |
| granite_wedged still computed | `grep -c "granite_wedged" agent/session_stall_classifier.py` | output > 0 |
| CLI pre-hook writes tool name | `grep -c "current_tool_name" .claude/hooks/pre_tool_use.py` | output > 0 |
| CLI post-hook writes last_tool_use_at | `grep -c "last_tool_use_at" .claude/hooks/post_tool_use.py` | output > 0 |
| read_until_idle has per-iteration callback | `grep -cE "def read_until_idle" agent/granite_container/pty_driver.py` | output > 0 |
| No new kill ladder introduced | `grep -rn "killpg\|SIGKILL" agent/session_health.py` | match count == 0 |
| Class-6 injector assertion updated | `grep -c "granite_wedged" tests/granite_faults/scenarios.py` | output > 0 |
| Lint clean | `python -m ruff check agent/ .claude/hooks/pre_tool_use.py .claude/hooks/post_tool_use.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ .claude/hooks/` | exit code 0 |

The "No new kill ladder" row is an anti-criterion for the #1820 No-Go: `session_health.py`
must NOT gain its own `killpg`/`SIGKILL` teardown (the PTY kill is #1820's/#1816's). Demonstrate
it FAILS against a deliberately-added `os.killpg` line first (red-state proof), then paste the
FAIL output into the PR description.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Gap 1 kill ownership before #1820 lands.** #1820 (the progress-deadline cancel scope
   that owns the granite PTY `killpg` teardown) is Ready but not merged. If #1843 builds
   first, should Gap 1's `granite_wedged` recovery (a) route the PTY teardown through the
   existing #1816 `killpg` container seam directly (reachable via the session's container
   handle), landing a complete fix now, or (b) wire only the recovery *decision* and leave
   the PTY reap to startup recovery / #1820, accepting that until #1820 lands a `granite_wedged`
   transition may not immediately reap the PTY children? Option (a) is more complete but risks
   duplicating logic #1820 will own; option (b) is cleaner deconfliction but leaves a window.
2. **Build/merge ordering with #1821.** Should Gap 2 be built into #1821's PR (single
   coordinated hook edit) or as its own PR that rebases onto #1821 after it merges? The plan
   assumes coordination either way; the supervisor's call on which PR carries the CLI-hook edit
   avoids a merge conflict.
3. **`reason_kind` enumeration.** Is `_apply_recovery_transition`'s `reason_kind` a free-form
   string or an enum? (Determines whether Gap 1 adds an enum member vs. passes a literal — a
   30-second build-time check, flagged here only so critique can confirm.)
