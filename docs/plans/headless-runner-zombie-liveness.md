---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1935
last_comment_id: 4901789240
revision_applied: true
---

# Headless runner zombie wedge: toolless-but-streaming turns misclassified as no-output

## Critique Revision (2026-07-07)

### Fourth revision — CRITIQUE pass 3, FULL depth (2026-07-08, 1 BLOCKER + 4 concerns, converged
across two independent critique runs)

- **BLOCKER — Element 1's cooldown citation was factually false.** Element 1 claimed
  `_stamp_stdout_liveness()` "mirrors `session_executor.py:1506-1509`, same fail-silent +
  5s-cooldown discipline." **Verified false:** that closure (`_on_stdout_event`,
  `session_executor.py:1509-1518`) has **zero** cooldown — it calls
  `session.save(update_fields=["last_stdout_at"])` unconditionally on every stdout line, and
  `on_stdout_event` fires on every non-empty stdout line (`sdk_client.py:2898-2904`). The real
  cooldown precedent (`COOLDOWN_WINDOW_SEC = 5.0`) lives only in `agent/hooks/liveness_writers.py`,
  which the plan's own Technical Approach section already cites correctly elsewhere — an internal
  contradiction. Fixed below (Element 1, Technical Approach): the citation now points solely at
  `liveness_writers.py`'s cooldown pattern, and cooldown state is explicitly required to be
  **per-session-keyed** (a bare module/class-level timestamp would let one session's stdout
  suppress another's stamp under concurrent `SessionRunner`s — reintroducing the exact false
  positive this plan fixes).
- **Concern — a dead, uncoordinated duplicate writer already exists and must be removed.** Both
  critique runs independently found `agent/session_executor.py:1509-1518`'s `_on_stdout_event`
  closure, wired into `BossMessenger(on_stdout_event=...)` at `:1520-1528` — a **prior, unlanded
  attempt at this exact signal**. It is dead in production (`grep -rn "notify_stdout_event" agent/
  tests/` shows zero call sites outside one unit test; `messenger` is never passed to
  `SessionRunner(...)` at `:1868-1876`). Leaving it in place after this plan lands would create two
  independent, uncoordinated `last_stdout_at` writers — a latent violation of both the
  single-authoritative-module directive (third revision) and this repo's no-legacy-code norm. Added
  as a new Prior Art bullet and a new cleanup task (Step 3.5) deleting the dead closure and its
  `BossMessenger` wiring.
- **Concern — no production-validation gate ties back to the reported incident.** Every Success
  Criterion was unit/grep-level; the one criterion that would demonstrate the actual fix — the
  motivating `sdlc-local-1933`/`1934` wedge class no longer recurring — was pushed to No-Gos as
  purely optional. Added a non-blocking Final Validation checklist item: grep the
  `zombie_uuid_no_output` Redis counter (`session_health.py:2154`, key pattern
  `{project_key}:session-health:recoveries:zombie_uuid_no_output`) post-deploy and note the result
  in the PR description.
- **Concern — widened post-init-hang detection window has no compensating observability.** Risk 1
  accepts detection latency growing from ~150s to up to `turn_timeout_s` (7200s for PM/eng) with no
  side-channel signal in between. Added an observability note to Risk 1: a debug-level log line
  inside `_stamp_stdout_liveness()`'s success branch (distinct from its fail-silent path) so
  operators can positively confirm the fix is firing post-deploy, distinguishing "wired and live"
  from "present but silently inert" (the exact failure mode the dead `BossMessenger` writer above
  demonstrates is possible in this codebase).
- **Concern (acknowledged, not actioned) — Element 3 bundles an independently-dead signal repair.**
  Both critique runs flagged `record_turn_boundary`'s fix as orthogonal to the reported wedge (it
  only fires at end-of-turn, after the 150s grace has already elapsed, so it cannot by itself
  prevent a toolless-turn zombie verdict — Elements 1+2 are independently sufficient for that).
  **Decision: kept in scope**, per CRITIQUE-pass-2's original reframing (`last_turn_at` is one of
  the three OR-inputs `derive_sdk_ever_output` reads, and is currently ~100% dead — leaving it
  unfixed means the single-authoritative function has a permanently-inert third input). To bound
  the risk both critiques raised, Element 3 is now called out as an **independently revertible
  commit** with its own Test Impact entries, so it can be reverted alone post-merge without
  reopening the primary wedge fix if it causes trouble.

### Third revision — owner design directive (2026-07-07T08:35:19Z, issue comment 4901774113)

Tom's directive, issued after CRITIQUE pass 2 landed: *"One authoritative liveness signal makes
the most sense. As much as we can strengthen a single module, let's do that instead of
manipulating the worker."* This changes **where** the derivation lives, not **what** it derives.

- **Ownership moves to `agent/session_runner/`.** Element 2's `_derive_sdk_ever_output` helper —
  previously module-level in `agent/session_health.py` (the worker file) — is relocated to a new
  module, **`agent/session_runner/liveness.py`**, exporting `derive_sdk_ever_output(entry) -> bool`.
  `session_health.py`'s four call sites now `import` and call it rather than defining it locally.
  The runner package — which already owns subprocess spawn/kill (`runner.py`) — becomes the single
  home for both the liveness **write** (Element 1's `_stamp_stdout_liveness`, still a `SessionRunner`
  method, calling into the same new module for the timestamp save) and the liveness **read**
  (Element 2's derivation). The worker's job shrinks to "call the one function four times," not
  "compute the signal."
- **Field count is unchanged, by design.** The directive is about *ownership location*, not about
  collapsing `last_tool_use_at` / `last_turn_at` / `last_stdout_at` into one field. Sub-check A of
  `_has_progress` still needs per-field *freshness* (not just presence) for its own, separate
  mid-turn cadence check — the No-Gos section already forbids touching that. Merging the fields
  would break sub-check A and expand scope well past Medium appetite. "Single authoritative signal"
  is satisfied by a single **function**, in a single **module**, called from four **sites** — not by
  a single **field**.
- **Element 3 (`record_turn_boundary`) re-evaluated, unchanged in substance.** The directive asks
  whether it's still load-bearing for liveness under the single-signal design, or telemetry-only.
  `last_turn_at` remains one of the three OR-inputs `derive_sdk_ever_output` reads — fixing its
  ~100%-dead id-resolution bug is still necessary for the signal to work on sessions that reach a
  turn boundary before any tool call. It is reframed as: a co-equal regression fix that feeds the
  now-centralized function, not an independent "third liveness field" being bolted on.
- **Cross-reference, not scope creep: issue comment 4901789240 (08:37:03Z).** The same issue
  thread surfaced an additive finding — `_confirm_subprocess_dead(None)` vacuously returns
  `confirmed_dead=True` when `claude_pid` is unset, which is exactly the population the
  never-started gate (D0) kills, so the kill-confirmation step never actually verifies anything for
  that population. This is real, but it is **`_confirm_subprocess_dead` / recovery-transition kill
  authority**, not the `sdk_ever_output` derivation — already tracked and actively planned under
  **#1938** ("Session recovery/failure leaks the live claude -p subprocess..."), whose recon
  independently confirms the same `claude_pid=None` root cause with file:line evidence
  (`agent/session_health.py:2292-2299`/`:1533`, `agent/session_runner/runner.py:522`). #1938's
  "owner-preferred direction" section already cites this same single-authoritative-module principle
  for subprocess *kill* ownership. No changes made to this plan's scope; noted here so a reader of
  either issue sees the other.

### Second revision — CRITIQUE pass 2 (1 NEW BLOCKER + 3 concerns + 1 nit)

- **BLOCKER — Risk 1 mitigation was factually false; post-`init`-then-hang subprocess.** Broadening
  `sdk_ever_output` to include `last_stdout_at` presence means a subprocess that streams `init` once
  and then hangs derives `sdk_ever_output=True` and escapes the never-started gate. The plan claimed
  "idle-gap / turn-deadline detectors key on `last_stdout_at`/`last_activity` freshness and DO fire" —
  **verified FALSE**: `grep -n "last_stdout_at\|last_activity" agent/session_health.py` returns ZERO
  hits. Resolution: adopted **option (a)** — the real backstop for a post-`init` hang is the
  **whole-turn deadline** (runner preempt watcher `runner.py:764` firing `_kill_turn(cause="timeout")`,
  with the driver's `asyncio.wait_for(..., timeout=turn_timeout_s)` at `role_driver.py:404` as the
  backstop). Risk 1, the Success Criterion, and Step 5 rewritten to cite that mechanism honestly,
  including the **detection-latency tradeoff** (a genuine post-`init` hang is now caught at the
  whole-turn deadline — `ENG_TURN_TIMEOUT_S`=7200s / `TEAMMATE`=900s — not at the ~150s never-started
  gate). The finer idle-gap-on-`last_stdout_at`-freshness detector remains the deferred follow-up
  (Open Question 1). Option (b) (build the idle-gap detector now) was rejected as a scope expansion
  with no correctness justification: the whole-turn deadline already recovers the hung turn.
- **Concern — Element 1 hard constraint.** The composing 1-arg `on_init` adapter is now **mandatory**;
  the inline-inside-`_on_harness_init` alternative is **forbidden** because `_on_harness_init`'s
  early-return (`runner.py:540`, `if not sid: return`) and its wrapping try/except (`:538`/`:560`)
  would skip the stamp on a `session_id`-less or persist-failing init event.
- **Concern — Element 3 reframed as co-equal regression fix.** Verified `record_turn_boundary` has a
  single caller (`sdk_client.py:2936`, worker-side result handler) and reads `AGENT_SESSION_ID`, which
  is set ONLY in the subprocess env overlay (`session_executor.py:1783`, value = `agent_session_id`).
  In the worker process it is unset → `last_turn_at` is **~100% dead today**, and even where set the
  `agt_xxx` value can never match `filter(session_id=...)`. Element 3 + Appetite relabeled from
  "defense-in-depth" to a co-equal regression fix.
- **Concern — Element 2 scope guard corrected.** The guard omitted site 2 (`_has_progress` sub-check
  B). Verified `_has_progress` feeds `should_recover` (`session_health.py:3136`) — a recovery-path
  consumer, not a mid-turn cadence detector. Guard reworded to enumerate all four recovery-path sites.
- **Nit — line citation.** `runner.py:431` verified CORRECT at HEAD (`session_id = str(getattr(...) or
  "")`); the critique's "~426" reading was stale. Citation anchored to `_build_driver` and the quoted
  code corrected to include the `or ""` guard.

### First revision — CRITIQUE pass 1 (1 BLOCKER + 4 concerns)

- **BLOCKER — fourth derivation site.** `sdk_ever_output` is derived at FOUR sites, not three; the
  missed one is `_tier2_reprieve_signal` (`session_health.py:1310`), a *second* wedge route via the
  reprieve cap. Element 2 now converts all four through a module-level `_derive_sdk_ever_output`
  helper, gated by two zero-hit greps. Root cause, Data Flow (new step 7), Success Criteria,
  Test Impact, and Step 2 updated.
- **Concern — callback arity.** `on_stdout_event` (0-arg) and `on_init` (1-arg) cannot share one
  closure; Element 1 now specifies two adapters, and the `on_init` adapter composes with (never
  replaces) `_on_harness_init`'s `claude_session_uuid` persistence.
- **Concern — `record_turn_boundary` id.** Element 3 now plumbs the true `AgentSession.session_id`
  from `runner.py:431`, explicitly rejecting the env `agent_session_id` (agt_xxx) and the Claude UUID
  (`data.get("session_id")`) as filter keys.
- **Concern — driver-seam test.** Added a deterministic fake-harness test proving the real stream
  fires `on_stdout_event` during a toolless window (Failure Path Test Strategy + Step 1c).
- **Concern — Test Impact site-4.** Added `test_session_health_compacting_reprieve.py`
  (`_tier2_reprieve_signal` / `reprieve_count` / `MAX_NO_OUTPUT_REPRIEVES`) and the driver-seam file.

## Problem

Since the granite PTY teardown (PR #1930, commit `e8351e4c`, merged 2026-07-07) cut every
session role over to the headless `claude -p` runner, `/do-sdlc` sessions can wedge: the worker
spawns the subprocess, the stream-json `init` event lands and a `claude_session_uuid` is
persisted, but no progress signal that session-health recognizes is written for the full ~150s
never-started grace. The health machinery classifies this as `zombie_uuid_no_output`
(`kind=no_progress`), recovers/retries once, hits the identical condition, and finalizes the
session `failed` after 2 recovery attempts. Two co-occurring instances (`sdlc-local-1933`,
`sdlc-local-1934`) wedged this way in the first batch after the cutover, both stalled at the
PLAN/CRITIQUE transition. Nine sessions total were confirmed wedged this way through 2026-07-07
14:38 UTC (dashboard sweep), spanning both bridge-originated Telegram sessions and local
`sdlc-local-*` pipeline runs — confirming the defect is in the spawn/streaming layer, not specific
to one session type or SDLC stage.

**Root cause.** `sdk_ever_output` — the flag whose being-False drives the zombie verdict — is
derived as `bool(last_tool_use_at or last_turn_at)` at **four** independent sites in
`agent/session_health.py` (re-verified at current HEAD `68c56004`, third revision): `:985-987`
(`_never_started_past_grace`), `:1127-1129` (`_has_progress`), `:1310-1312`
(`_tier2_reprieve_signal`), and `:2149-2151` (recovery-classification / `zombie_uuid_no_output`
counter — drifted from `:2057` since plan baseline due to unrelated commit `a77ae27a`, which added
~92 lines earlier in the file for interrupt-messaging work, #1937). On a headless turn those two
fields are written only by tool-boundary hooks (on a tool call) and by end-of-turn
`record_turn_boundary`. A turn that streams the `init` event and then produces assistant output
*without calling a tool within 150s* (e.g. PM prime resolution + reasoning before its first tool)
therefore has **no recognized progress signal even though the subprocess is demonstrably alive and
streaming** — the persisted `claude_session_uuid` is itself proof the SDK produced output. The
PTY→headless cutover dropped the per-stream-activity liveness write that previously covered this
case: `SessionRunner._build_driver` (`agent/session_runner/runner.py:423-446`) never wires
`on_stdout_event`, so `last_stdout_at` is never refreshed during a headless turn — and
`last_stdout_at` was never part of the `sdk_ever_output` derivation to begin with.

**Two wedge routes, not one.** The never-started gate (`_never_started_past_grace`, site 1) is the
first-observed route. But the reprieve path is a *second* independent route to the same failure: a
toolless-streaming session that survives the grace window still computes `sdk_ever_output=False` at
site 3 (`_tier2_reprieve_signal`), and once `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` (= 20) its
Tier-2 reprieves are suppressed and it is escalated to recovery — the identical wedge, reached via
the reprieve-cap guard instead of the never-started gate. **All four sites must convert together**;
converting only sites 1/2/4 leaves the reprieve route open (critique BLOCKER).

## Freshness Check

**Original baseline commit:** `8485db99` (`git rev-parse HEAD` at plan time).
**Issue filed at:** 2026-07-07T06:14:28Z. **Cutover merged:** 2026-07-07T04:54:35Z (`e8351e4c`).
**Disposition (original pass):** **Unchanged.**

- All cited file:line references (`session_health.py:985/1127/1310/2057`, `runner.py:423-446`,
  `role_driver.py:175/194/400`, `adapter.py:362-382`, `session_executor.py:1506-1524/1783`,
  `sdk_client.py:2936`, `liveness_writers.py:136`, `session_stall_classifier.py:53/60`) were read
  at HEAD `8485db99` and match the issue's description.
- Cited sibling issues re-checked: #1843 CLOSED (granite Gap A/Gap B fix), #1792/#1724/#1356/#1614/#1905
  all CLOSED. #1843's Gap B substrate (`agent/granite_container/` PTY driver) was deleted by the
  cutover — its mid-turn liveness refresh has no headless equivalent, which is the carried-over gap.
- Bug still present: the code path is unchanged and the reproduction (toolless streaming turn past
  grace → zombie) is deterministic from the derivation, not environmental.

### Re-verification (2026-07-08, comment-sync pass — third revision)

**New baseline commit:** `68c56004`. **Disposition:** **Minor drift** — one site's line number
moved; the derivation logic itself is untouched.

- `git log --oneline --since="2026-07-07T06:14:28Z" -- agent/session_runner/ agent/session_health.py
  agent/hooks/liveness_writers.py` returns one commit: `a77ae27a` ("Remove the
  interrupted-will-resume announcement entirely (#1937)"). Read its diff on `session_health.py`
  (207 lines changed): it is entirely about interrupt/cancel-reason messaging
  (`_deliver_terminal_interrupt_notice`, `_deliver_oneshot_dedup_notice`) — it does not touch the
  `sdk_ever_output` derivation. Net effect: site 4's line number drifted from `:2057` to `:2149`
  (content unchanged); sites 1-3 (`:985`, `:1127`, `:1310`) are unaffected (the inserted code is
  later in the file). Citations in this plan updated accordingly throughout.
- `agent/session_runner/runner.py` unchanged since baseline (`_build_driver`, `_on_harness_init`
  confirmed at the same lines cited originally; no `_stamp_stdout_liveness` exists yet, confirming
  Element 1 is still unbuilt as the plan assumes).
- `docs/plans/` overlap check re-run: `session-recovery-subprocess-leak.md` and
  `recovery-subprocess-leak-worktree-race.md` (both for sibling issue #1938) touch
  `agent/session_health.py` and `agent/session_runner/runner.py` too, but a **different** concern
  (subprocess kill/confirm-exit ordering around recovery/failure transitions, not the
  `sdk_ever_output` derivation). No direct file-region conflict expected — #1938's changes are in
  `_confirm_subprocess_dead`/recovery-transition kill sequencing; this plan's are in the
  never-started/reprieve/zombie-classification derivation sites. Flagged as coordination context in
  the third-revision section above, not a blocker.
- Owner comment 4901774113 (08:35:19Z) incorporated as the third revision (single-authoritative-
  module relocation). Owner comment 4901789240 (08:37:03Z, kill-confirmation gap) cross-referenced
  to #1938, not incorporated into this plan's scope (see third-revision section).

## Prior Art

- **`session_executor.py:1509-1528` — a prior, unlanded attempt at this exact signal (found during
  CRITIQUE pass 3).** An `_on_stdout_event` closure already stamps `last_stdout_at` unconditionally
  (no cooldown) on every stdout line, wired into `BossMessenger(on_stdout_event=...)`. It is dead in
  production: `grep -rn "notify_stdout_event" agent/ tests/` returns zero call sites outside
  `tests/unit/test_messenger_callbacks.py`, and `messenger` is never passed into
  `SessionRunner(...)` (`session_executor.py:1868-1876`). This plan's Element 1 re-solves the same
  problem at the correct layer (the runner's driver callbacks, which the live stream actually
  drives) and Step 3.5 deletes the dead closure so only one `last_stdout_at` writer exists
  post-merge.
- **#1843 / `granite-wire-silent-wedge-signals` (CLOSED).** Fixed this exact "silent no-progress"
  class for the PTY runner. **Gap A** wired CLI-hook liveness writes so tool boundaries populate
  `current_tool_name`/`last_tool_use_at` via sidecar resolution
  (`.claude/hooks/pre_tool_use.py:24-102`, `.claude/hooks/post_tool_use.py:455-515`) — this SURVIVES
  the cutover (CLI hooks merge additively with the per-session `--settings` file). **Gap B** made
  PTY reads refresh `last_pty_read_loop_at` mid-turn — this DIED with the PTY substrate and has no
  headless replacement. The current bug is the headless-shaped reincarnation of Gap B.
- **#1724 / PR #1728.** Built `last_pty_read_loop_at` / `last_pty_activity_at` freshness fields and
  the `granite_wedged` verdict — the PTY-era per-stream-activity liveness this plan re-establishes
  for the headless runner via `last_stdout_at`.
- **#1270 (CLOSED).** Per-tool timeout tiers requiring non-null `current_tool_name`. Confirms
  `last_tool_use_at` is a tool-boundary-only signal and cannot cover a toolless turn.
- **#1688 / PR #1847.** Introduced the per-session `--settings` + `hook_forwarder.py` architecture
  and established that generated-settings hooks merge *additively* with repo `.claude/settings.json`.
- **#1905 (CLOSED).** D0 never-started gate makes `no_output_budget_exceeded` unreachable — context
  for how the never-started gate short-circuits before other stall detectors.
- **#1938 (OPEN, sibling).** "Session recovery/failure leaks the live claude -p subprocess and
  deletes its worktree while it runs." Same incident, complementary defect: this plan stops the
  FALSE `no_progress` triggers; #1938 stops what happens to the subprocess when a recovery/failure
  fires (false or genuine). #1938's recon independently confirms the `claude_pid=None`
  kill-confirmation gap raised in issue comment 4901789240.

## Research

No relevant external findings needed — this is an internal regression in the session-runner
liveness plumbing. The one external fact this plan depends on (Claude Code hooks from `--settings`
merge additively with project `.claude/settings.json` rather than replacing them) was already
researched and recorded in #1843's Research section against the Claude Code hooks/settings docs, and
is corroborated here by worker-log evidence that the per-session settings file coexists with the
repo hooks. Proceeding with codebase context.

## Data Flow

Headless turn, from spawn to zombie verdict:

1. Worker picks up the session, allocates a synthetic slug + worktree (`session_executor.py`
   synthetic-slug path), injects `AGENT_SESSION_ID` into the **subprocess** env overlay
   (`_harness_env`, `session_executor.py:1783`), and constructs `SessionRunner`.
2. `SessionRunner._build_driver` (`runner.py:423-446`) builds `HeadlessRoleDriver` with `on_spawn`
   and `on_init` **but not `on_stdout_event`**.
3. `claude -p` spawns and streams the `init` event → `role_driver._handle_init` → `runner._on_harness_init`
   → `adapter.persist_resume_scalars` writes `claude_session_uuid` (`adapter.py:362-382`). **t ≈ few seconds.**
4. The subprocess produces assistant output (prime resolution, reasoning). No tool call yet → no
   PreToolUse/PostToolUse hook → `last_tool_use_at` unwritten. `on_stdout_event` is None → `last_stdout_at`
   unwritten. `record_turn_boundary` only fires at end-of-turn (`sdk_client.py:2936`) and even then
   reads the worker's unset `AGENT_SESSION_ID` (`liveness_writers.py:136`).
5. The 60s heartbeat keeps `last_heartbeat_at` fresh but that is not a progress signal.
6. At running-seconds > 150s (`NEVER_STARTED_GRACE_SECS`+`CONFIRM_MARGIN`), `_never_started_past_grace`
   returns True (`session_health.py:985-987`, `sdk_ever_output=False`, via the imported
   `agent.session_runner.liveness.derive_sdk_ever_output`), `_has_progress` denies the heartbeat
   fast-path, the zombie branch fires (`:2149-2158`), and the session is recovered → retried →
   identical wedge → `failed`.
7. **Reprieve route (second path to the same wedge).** Even for a session that clears the grace
   window, `_tier2_reprieve_signal` (`:1310-1312`, `sdk_ever_output=False`) suppresses all Tier-2
   reprieves once `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` (`:1314-1317`), escalating a
   still-streaming session to recovery. Because this site derives `sdk_ever_output` via the same
   imported function, it must be converted in lockstep with the other three.

The fix inserts a progress write at step 4 (stream activity → `last_stdout_at`) and makes both the
step-6 never-started derivation and the step-7 reprieve derivation recognize it, via one function
owned by `agent/session_runner/liveness.py`.

## Why Previous Fixes Failed

The relevant prior fix (#1843) did not fail — it was **substrate-specific and only partially
portable**. Gap A (tool-boundary liveness via CLI hooks) was implemented in the repo `.claude/hooks/`
layer that both PTY and headless children share, so it carried over. Gap B (per-stream-read liveness)
was implemented inside `agent/granite_container/`'s PTY driver, which the cutover deleted wholesale —
so the signal that covered *toolless streaming* turns silently vanished. No code "regressed" in the
sense of a broken edit; the cutover removed a whole liveness source without re-providing an
equivalent on the new transport. This plan re-provides it via `last_stdout_at` on the headless
stream.

## Architectural Impact

Touches the session-runner liveness seam and the session-health never-started/zombie derivation.
No schema changes (both `last_stdout_at` and the tool/turn fields already exist on `AgentSession`).
The change is additive: it introduces a third, transport-native progress signal
(`last_stdout_at`, fed by the headless stream) into an OR-derivation that already tolerates any one
signal being present. No behavior change for sessions that already emit tool/turn boundaries.

**Ownership relocation (owner directive, third revision).** The derivation itself moves from
`agent/session_health.py` (worker-owned, inline) to a new `agent/session_runner/liveness.py`
(runner-owned, single exported function). `session_health.py` becomes a pure consumer — it imports
`derive_sdk_ever_output` and calls it at all four sites; it no longer defines or inlines the OR
expression. This is the single-authoritative-module structure the directive asked for.

## Appetite

**Medium.** Three focused code edits (wire `on_stdout_event` in the runner; relocate and extend the
`sdk_ever_output` derivation into a new runner-owned module, consumed at all four
`session_health.py` sites; fix the `record_turn_boundary` id resolution — a co-equal regression fix,
since `last_turn_at` is a dead writer today) plus deterministic unit reproductions, a driver-seam
test, and a docs update. The session-health derivation change is small but load-bearing and spans
four sites (one of them a second wedge route via the reprieve cap), so it warrants careful
red-first testing rather than a Small-appetite drive-by.

## Prerequisites

None. `last_stdout_at`, `last_tool_use_at`, `last_turn_at` all already exist on `AgentSession`
(`models/agent_session.py`). `HeadlessRoleDriver` already accepts and threads `on_stdout_event`
(`role_driver.py:175,194,400`) — only the runner's wiring is missing.

## Solution

### Key Elements

1. **Wire `on_stdout_event` in the headless runner — two distinct adapters.** In
   `SessionRunner._build_driver` (`runner.py:423-446`), the driver's two observer slots have
   **different arities**: `on_stdout_event: Callable[[], None]` (0-arg, `role_driver.py:175`) and
   `on_init: Callable[[dict], None]` (1-arg, `role_driver.py:178`). A single closure cannot serve
   both. Provide two adapters over one shared `_stamp_stdout_liveness()` helper:
   - `on_stdout_event` → a **0-arg** adapter that calls `_stamp_stdout_liveness()`.
   - `on_init` must **compose** with (not replace) the existing `self._on_harness_init` — that
     callback persists `claude_session_uuid` + `runner_cwd` + `claude_version` via
     `persist_resume_scalars` (`runner.py:529-561`) and MUST keep doing so. **HARD CONSTRAINT:** use a
     separate **1-arg** composing adapter —
     `def _on_init(data): self._on_harness_init(data); self._stamp_stdout_liveness()` — passed as the
     driver's `on_init`. **The inline-inside-`_on_harness_init` alternative is FORBIDDEN.** Reason
     (verified): `_on_harness_init` early-returns at `runner.py:540` (`sid = data.get("session_id");
     if not sid: return`) and wraps its whole body in a try/except (`:538` try, `:560` except), so a
     stamp placed inside it would be **skipped** on any `session_id`-less init event or if
     `persist_resume_scalars` raises. The composing adapter stamps *after* `_on_harness_init` returns,
     unconditionally — the `init` event is the first proof of output and must count immediately,
     before any assistant token, and independently of whether the resume-scalar persistence
     succeeded. `_on_harness_init` itself is left byte-for-byte unchanged (it keeps owning
     resume-scalar persistence).

   `_stamp_stdout_liveness()` stamps `last_stdout_at = datetime.now(tz=UTC)` on the AgentSession,
   fail-silent, with a **per-session-keyed** cooldown mirroring `agent/hooks/liveness_writers.py`'s
   `COOLDOWN_WINDOW_SEC = 5.0` discipline (CRITIQUE pass 3 BLOCKER fix — the cooldown state must be
   keyed by `session_id`, e.g. an instance attribute on `SessionRunner` or a dict keyed by
   `session_id`, NOT a bare module/class-level timestamp, otherwise one session's stdout stream
   could suppress a concurrently-running session's stamp within the same worker process,
   reintroducing the exact false positive this plan fixes). **Do NOT** mirror the pre-existing
   `session_executor.py:1509-1518` `_on_stdout_event` closure — that closure has zero cooldown
   (unconditional save on every stdout line) and is itself dead code being removed by Step 3.5; it
   is not a cooldown precedent. This restores the per-stream-activity liveness signal the PTY
   teardown dropped.

2. **Recognize stream activity in the derivation — all FOUR sites, relocated to
   `agent/session_runner/` (owner directive, third revision).** Create
   **`agent/session_runner/liveness.py`** exporting a single function
   `derive_sdk_ever_output(entry) -> bool` returning
   `bool(last_tool_use_at or last_turn_at or last_stdout_at)`. `session_health.py` **imports** this
   function and calls it at **all four** sites — it does not define or inline the expression itself:
   `session_health.py:985-987` (`_never_started_past_grace`), `:1127-1129` (`_has_progress`),
   `:1310-1312` (`_tier2_reprieve_signal`), and `:2149-2151` (recovery classification — line drifted
   from `:2057` to `:2149` per the Freshness Check re-verification above; unrelated commit
   `a77ae27a` added ~92 lines earlier in the file). The function lives at module scope in the new
   file (no ordering constraint from `_tier2_reprieve_signal` calling `_never_started_past_grace`,
   since both now call the same imported function rather than each other's local helper).
   Semantically correct: `sdk_ever_output` means "has the SDK ever produced output," and the
   `init`/stdout stream IS output. **Post-edit assertion (critique BLOCKER gate, updated for the
   relocation):** `grep -n "sdk_ever_output = bool(" agent/session_health.py` and
   `grep -n "_sdk_ever_output = bool(" agent/session_health.py` must BOTH return zero hits (no
   inline derivation left in the worker file), AND
   `grep -n "from agent.session_runner.liveness import derive_sdk_ever_output" agent/session_health.py`
   must return exactly one hit (single import, four call sites). **Scope guard:** this broadens the
   "no output ever" input at all FOUR recovery-path sites and nowhere else — site 1
   `_never_started_past_grace` (never-started gate), site 2 `_has_progress` sub-check B (verified a
   recovery-path consumer: its result gates `should_recover` at `session_health.py:3136`, not any
   live turn), site 3 `_tier2_reprieve_signal` (reprieve-cap guard), site 4 the
   `zombie_uuid_no_output` recovery classifier (`:2149`). None of the four is a mid-turn cadence
   detector. Detectors that legitimately need a tool/turn cadence (per-tool timeout tiers, and
   `_has_progress` **sub-check A**'s freshness comparison — which is untouched) are NOT loosened; only
   the presence-based "has the SDK EVER produced output" input is broadened — see No-Gos. The
   relocation does not change *what* is derived, only *where* — session_health.py's behavior at all
   four sites is byte-for-byte equivalent to CRITIQUE-pass-2's version, just via an imported call
   instead of an inlined expression.

3. **Fix the `record_turn_boundary` id resolution (co-equal regression fix, NOT defense-in-depth) —
   plumb the true `AgentSession.session_id`.** `record_turn_boundary`
   (`agent/hooks/liveness_writers.py:129`, called once from `sdk_client.py:2936`) reads
   `os.environ.get("AGENT_SESSION_ID")` and filters `AgentSession.query.filter(session_id=...)`.
   **Verified: `last_turn_at` is ~100% dead today.** Two stacked reasons:
   (i) `record_turn_boundary` has a *single* caller — `sdk_client.py:2936`, the harness `result`-event
   handler, which runs in the **worker** process; `AGENT_SESSION_ID` is injected only into the
   *subprocess* env overlay (`session_executor.py:1783`), so in the worker it is unset →
   `record_turn_boundary` returns False before any write. (ii) Even where the env var *is* set (the
   in-subprocess CLI-hook path / child-spawn overlay), its value is `agent_session_id` (`agt_xxx`,
   `session_executor.py:1783: "AGENT_SESSION_ID": session.agent_session_id`), which can **never** match
   `filter(session_id=...)` — `session_id` is the distinct Telegram-derived key
   (`models/agent_session.py:141`). The harness result event's `data.get("session_id")` is the
   **Claude UUID** — also wrong for the filter. The ONLY correct value for `filter(session_id=...)` is
   the true `AgentSession.session_id`, which the runner already has in hand in `_build_driver`
   (`runner.py:431`: `session_id = str(getattr(self._agent_session, "session_id", "") or "")` — the
   same value it passes to `HeadlessRoleDriver(session_id=...)`). **Plumbing path:** add an optional
   `session_id: str | None = None` param to `record_turn_boundary`; thread the runner's true
   `AgentSession.session_id` through the harness stream call (`sdk_client.py` stream fn) down to the
   `result`-event handler at `:2936`, which passes it explicitly. When `session_id` is None, fall
   back to `os.environ` (preserves the in-subprocess CLI-hook call sites unchanged). Because
   `last_turn_at` is one of the three OR-signals feeding `sdk_ever_output` and is currently a dead
   writer, restoring it is a genuine regression fix — not merely belt-and-suspenders. It does not by
   itself close the toolless-streaming in-grace wedge (Elements 1+2 do); the two fixes are
   complementary.

### Flow

Post-fix: init event → `last_stdout_at` stamped (t≈few seconds) → `sdk_ever_output` derives True →
`_never_started_past_grace` returns False → no zombie verdict. Subsequent stdout activity keeps
`last_stdout_at` fresh. Tool calls and end-of-turn continue to write their own fields as before.

### Technical Approach

- Add a `_stamp_stdout_liveness()` helper in `SessionRunner` (alongside the existing turn-spawn/init
  observers) that resolves the AgentSession by the true `session_id` and saves `last_stdout_at` with
  `update_fields=["last_stdout_at"]`, fail-silent, with a short in-memory cooldown to bound Redis
  write rate (mirror `liveness_writers.COOLDOWN_WINDOW_SEC = 5.0` — **per-session-keyed**, e.g. a
  `SessionRunner` instance attribute or a dict keyed by `session_id`; NOT a bare module/class-level
  timestamp, which would let one session's stdout suppress another's stamp under concurrent
  `SessionRunner`s — CRITIQUE pass 3 BLOCKER fix). Expose it through **two** driver adapters
  (Element 1): a 0-arg `on_stdout_event` adapter, and a 1-arg `on_init` adapter that first
  delegates to `_on_harness_init` (preserving `persist_resume_scalars`) and then stamps.
- Create **`agent/session_runner/liveness.py`** exporting `derive_sdk_ever_output(entry) -> bool`
  (owner directive, third revision — relocated from a `session_health.py`-local helper). In
  `agent/session_health.py`, add `from agent.session_runner.liveness import derive_sdk_ever_output`
  and replace the **four** inline `bool(last_tool_use_at or last_turn_at)` /
  `_sdk_ever_output = bool(...)` expressions (`:985`, `:1127`, `:1310`, `:2149`) with a call to the
  imported function, adding `last_stdout_at` to its OR. Keep the docstrings' "either per-turn field"
  language updated to "any stream or turn signal, derived by `agent.session_runner.liveness`."
  Confirm with the three greps in Element 2 (two zero-hit, one single-hit-import).
- `record_turn_boundary(session_id: str | None = None)`: if `session_id` is None fall back to
  `os.environ` (preserves the in-subprocess CLI-hook call sites); the harness worker-side call site
  in `sdk_client.py:2936` passes the true `AgentSession.session_id` plumbed from the runner
  (`runner.py:431`), never the Claude UUID (`data.get("session_id")`) and never the env
  `agent_session_id`.

## Failure Path Test Strategy

### Exception Handling Coverage
- `last_stdout_at` stamp save failure (Redis down / no matching session): the closure must swallow
  the exception and log at debug — a liveness-write failure must never crash or wedge the turn.
  Test by monkeypatching the save to raise and asserting the turn proceeds.

### Empty/Invalid Input Handling
- `on_stdout_event` firing before `claude_session_uuid` is persisted: `last_stdout_at` resolves the
  AgentSession by `session_id` (the stable AgentSession key), not the claude uuid, so ordering vs.
  uuid persistence is irrelevant. Test: stamp with only `session_id` present.
- `record_turn_boundary(session_id=None)` with unset env: returns False, no write (unchanged
  behavior). Test preserves the existing no-op contract.

### Error State Rendering
- Not applicable — no user-facing surface. The observable is the session NOT transitioning
  `running→failed` with `context="never progressed (kind=no_progress)"` when it is actually streaming.

### Driver-Seam Coverage (Concern 4)
- **Deterministic proof that the real `claude -p` stream fires `on_stdout_event` during a toolless
  window.** Element 1's unit test can pass by stamping `last_stdout_at` directly, without ever
  proving the driver actually invokes `on_stdout_event` on stdout lines. Close that gap with a
  driver-seam test in `tests/unit/session_runner/test_headless_role_driver.py` (the existing fake-
  harness seam — see `test_claude_session_id_capture` / `_make_harness`): construct a
  `HeadlessRoleDriver` with a fake `harness_fn` that emits an `init` event followed by assistant
  stdout lines and **no tool-call event** (a toolless window), pass a counting `on_stdout_event`
  callback, run one turn, and assert the counter incremented (≥1) — i.e. the stdout stream drives
  the liveness signal even when no tool boundary ever fires. This is deterministic (no real
  subprocess; the harness fake feeds stdout lines through the same `sdk_client.py:2900-2902`
  `on_stdout_event` dispatch the real stream uses).

## Test Impact

- [ ] **`tests/unit/session_runner/test_liveness.py`** (NEW) — the `derive_sdk_ever_output` unit
      tests over all 2^3 combinations of `last_tool_use_at`/`last_turn_at`/`last_stdout_at` live
      here, since the function now lives in `agent/session_runner/liveness.py` (owner directive).
- [ ] `tests/unit/test_never_started_recovery.py` (never-started + `sdk_ever_output` +
      `zombie_uuid_no_output`) — UPDATE: add a case where `last_stdout_at` is fresh and assert
      `sdk_ever_output` derives True / no zombie verdict via the imported
      `agent.session_runner.liveness.derive_sdk_ever_output`; verify existing cases that set
      `last_tool_use_at`/`last_turn_at` still pass through unchanged.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` and
      `tests/unit/test_session_health_inference_removed.py` (both reference `sdk_ever_output` /
      recovery finalization) — UPDATE: point derivation assertions at the imported
      `derive_sdk_ever_output` and the 3-field OR; keep recovery-finalization semantics.
- [ ] **`tests/unit/test_session_health_compacting_reprieve.py`** (Concern 5 — site-4 reprieve cap:
      references `_tier2_reprieve_signal`, `reprieve_count`, `MAX_NO_OUTPUT_REPRIEVES`) — UPDATE: add
      a case where a session past the reprieve cap but with fresh `last_stdout_at` is NOT suppressed
      (reprieve still granted), and confirm the existing "no output ever → suppress at cap" cases
      still hold when all three fields are unset.
- [ ] `tests/unit/test_session_health_trusted_clock.py` (references `sdk_ever_output`) — UPDATE:
      re-point any inline-derivation assertions at the imported function.
- [ ] `tests/unit/session_runner/test_runner_liveness.py` — UPDATE: assert `on_stdout_event` is now
      wired and stamps `last_stdout_at`; assert the `on_init` adapter still persists resume scalars
      (`_on_harness_init` not shadowed).
- [ ] `tests/unit/session_runner/test_headless_role_driver.py` — UPDATE/ADD: the Concern-4
      driver-seam test (toolless window fires `on_stdout_event`); existing
      `test_claude_session_id_capture` must still pass (init still persists the uuid).
- [ ] `record_turn_boundary` unit tests (`agent/hooks/liveness_writers`) — UPDATE: add the
      explicit-`session_id` path; keep the env-fallback no-op case.
- [ ] `tests/unit/session_runner/headless_hook_probe.py` — no change expected (it exercises turn-end
      hook firing, not liveness); confirm it still passes.
- [ ] **`tests/unit/test_messenger_callbacks.py`** (CRITIQUE pass 3, Step 3.5) — UPDATE:
      `test_notify_stdout_event_invokes_callback` exercises `BossMessenger.notify_stdout_event()` in
      isolation and is unaffected by removing the dead `on_stdout_event=_on_stdout_event` wiring in
      `session_executor.py` (the messenger class itself is untouched, only its unused call site is
      removed) — confirm it still passes; no test deletion needed since the test targets the
      messenger unit, not the dead wiring.
- [ ] **New: cooldown-keying test (CRITIQUE pass 3 BLOCKER fix)** — assert two concurrently
      instantiated `SessionRunner`/driver pairs (distinct `session_id`s) each get an independent
      `last_stdout_at` stamp within the same 5s window, proving the cooldown state is per-session-
      keyed and not a shared module/class-level timestamp. Lives alongside the Step 3 runner tests.

No existing tests are DELETED or REPLACED — the changes are additive to the derivation (four sites,
one function, relocated), the runner wiring, and removal of one dead, already-unreferenced call site
in `session_executor.py`.

## Rabbit Holes

- **Do NOT** rework the whole session-health stall taxonomy or the `granite_wedged`/stall-advisory
  actuation ladder — this plan touches only the never-started/zombie derivation and one liveness
  write.
- **Do NOT** try to resurrect the deleted PTY liveness path or reason about `last_pty_read_loop_at` —
  that field is dead with the substrate; `last_stdout_at` is its headless replacement.
- **Do NOT** attempt to distinguish "streaming useful tokens" from "streaming a spinner" — the
  never-started gate only asks "did the SDK EVER produce output," and mid-turn hang detection is a
  separate concern owned by other detectors (out of scope here).
- **Do NOT** collapse `last_tool_use_at`/`last_turn_at`/`last_stdout_at` into a single field, and do
  NOT touch `_confirm_subprocess_dead` or any recovery-transition kill logic — that is #1938's scope
  (owner directive, third revision).

## Risks

### Risk 1: Masking a genuinely wedged subprocess that emits `init` then truly hangs
Counting `last_stdout_at` *presence* as progress means a subprocess that streamed `init` and then
hung with no further output is marked as "produced output" and escapes the never-started gate
permanently (the derivation is presence-based, not freshness-based).
**What actually catches it (verified).** There is **no** idle-gap / freshness detector on
`last_stdout_at` — `grep -n "last_stdout_at\|last_activity" agent/session_health.py` returns ZERO
hits, so the earlier claim that session-health idle-gap detectors "key on last_stdout_at freshness"
was false. The real backstop for a post-`init` hang is the **whole-turn deadline** enforced by the
runner, not by session-health:
- The runner's preempt watcher (`_run_preempt_watcher`, `runner.py:764`) checks
  `(loop.time() - started_at) >= self._turn_timeout_s` every poll and calls
  `_kill_turn(cause="timeout")` — this fires FIRST.
- The driver's `asyncio.wait_for(harness_fn(...), timeout=self.turn_timeout_s)` (`role_driver.py:404`)
  is the backstop; on expiry it sets `outcome.hung=True` / `exit_reason="headless_turn_timeout"`.
- `turn_timeout_s = turn_timeout_for(session_type)` (`runner.py:334/183`): `ENG_TURN_TIMEOUT_S`=7200s
  for PM/eng, `TEAMMATE_TURN_TIMEOUT_S`=900s (both env-overridable). The driver's own `wait_for` is
  set slightly higher (`_turn_timeout_s + _term_grace_s + DRIVER_BACKSTOP_MARGIN_S`, `runner.py:442`).

**Accepted tradeoff (honest).** Because the whole-turn deadline is a wall-clock timer measured from
turn start — NOT an idle-gap on stdout freshness — a genuine post-`init` hang is now caught at the
turn deadline (up to 7200s for a PM/eng turn) rather than at the ~150s never-started gate. This is a
detection-latency regression *for the rare init-then-immediate-hang case only*, and it is the correct
tradeoff: a subprocess that streamed `init` genuinely produced output, so the never-started gate
(whose sole question is "did the SDK EVER produce output") SHOULD NOT fire on it; the hung turn is
still recovered, just via the coarser existing backstop. Tightening this to a fast idle-gap detector
keyed on `last_stdout_at` *freshness* is the deferred follow-up (Open Question 1) — a scope expansion
with no correctness justification here, since the turn deadline already recovers the turn.
**Build verification:** add a deterministic test (small injected `turn_timeout_s`) asserting a
fake-harness turn that emits `init` then hangs is preempted/killed via the turn-deadline path
(`outcome.hung=True` / `exit_reason="headless_turn_timeout"`), NOT via the never-started gate.
**Observability (CRITIQUE pass 3 concern):** widening the detection window from ~150s to up to
`turn_timeout_s` means a genuine post-`init` hang goes silent for longer with no compensating
signal. `_stamp_stdout_liveness()`'s success branch (after the `update_fields=["last_stdout_at"]`
save) must emit a distinct debug-level log line (e.g. `logger.debug("stdout_liveness_stamped",
session_id=session_id)`) so a post-deploy `grep stdout_liveness_stamped logs/worker.log` can
positively confirm the fix is firing, rather than only inferring it from the absence of zombie
verdicts — this is the same "looks fixed but is silently inert" failure mode the dead
`session_executor.py` `_on_stdout_event`/`BossMessenger` writer (Prior Art) demonstrates is
possible in this codebase. Pure observability — it does not feed the recovery/reprieve state
machine.

### Risk 2: Redis write amplification from per-stdout-chunk stamping
Stamping `last_stdout_at` on every stdout event could hammer Redis on a chatty turn.
**Mitigation:** apply the same 5s cooldown the CLI-hook liveness writer uses; the stamp is a single
`update_fields=["last_stdout_at"]` save, coalesced to at most once per 5s per session.

## Race Conditions

### Race 1: `on_stdout_event` fires before the AgentSession row is queryable
The AgentSession record is created and saved by the worker before the subprocess is spawned, so the
row exists before any stdout event. The stamp resolves by `session_id` (stable key). If a resolve
miss ever occurs it fail-silently no-ops (no crash), and the next stdout event (or the init stamp)
retries. No ordering dependency on `claude_session_uuid` persistence.

## No-Gos (Out of Scope)

- Loosening mid-turn stall detectors (per-tool timeout tiers `#1270`, idle-gap). Only the
  `sdk_ever_output` "no output ever" input is broadened — at all four sites that derive it, including
  the `_tier2_reprieve_signal` reprieve-cap guard (site 4). The reprieve *cadence*, the recovery
  ladder, and the per-tool/idle-gap detectors are untouched. Justification: those detectors correctly
  require tool/turn cadence and must keep firing on real mid-turn hangs; broadening them would
  re-introduce the very silent-wedge blindness #1843 closed. Broadening `sdk_ever_output` is safe
  because a session that streamed output genuinely produced output — the exact question that flag
  asks.
- Changing the never-started grace duration or the recovery-attempt cap. The 150s grace is not the
  bug; the missing progress signal is.
- Reviving `agent/granite_container/` or any PTY liveness field.
- `_confirm_subprocess_dead`, recovery-transition kill sequencing, or worktree-cleanup-vs-live-process
  ordering — that is #1938's scope, not this plan's, even though issue comment 4901789240 surfaced a
  related finding on the same thread (owner directive, third revision).
- [EXTERNAL] A live `/do-sdlc` re-run of #1933/#1934 as the acceptance gate — requires the live
  worker/batch environment and is inherently non-deterministic, so it cannot serve as an in-plan
  automated gate. The deterministic unit repro is the acceptance gate; the live re-run is optional
  manual post-merge confirmation.

## Update System

No update-system changes required. This is a purely internal fix within `agent/session_runner/` and
`agent/session_health.py`; no new dependencies, config keys, migrations, or `scripts/update/` wiring.
No Popoto schema change (all fields already exist), so no `scripts/update/migrations.py` entry.

## Agent Integration

No agent integration required. No new CLI entry point, MCP server, or `.mcp.json` change. The fix is
invisible to the agent surface — it changes only how the worker's liveness plumbing feeds
session-health. The observable effect is that legitimate headless turns stop being killed; existing
tools (`valor-session status/telemetry`, dashboard) surface `last_stdout_at` unchanged.

## Documentation

- [ ] Update `docs/features/headless-session-runner.md` — add a "Liveness signals" subsection
      documenting that the headless runner stamps `last_stdout_at` on `init`/stdout events and that
      `sdk_ever_output` (the never-started/zombie gate AND the `_tier2_reprieve_signal` reprieve-cap
      guard) derives from `last_tool_use_at OR last_turn_at OR last_stdout_at` via
      `agent.session_runner.liveness.derive_sdk_ever_output` — the single authoritative function,
      owned by the runner package, that `session_health.py` imports rather than re-deriving inline
      (owner directive, 2026-07-07). Cross-reference #1843 Gap B as the PTY-era predecessor, and
      #1938 as the sibling issue covering subprocess *kill* ownership under the same principle.
- [ ] Update `docs/features/session-lifecycle.md` (or the session-health reference it links) where
      `zombie_uuid_no_output` / never-started is described, to reflect the 3-signal derivation.

### Feature Documentation
Primary target: `docs/features/headless-session-runner.md`.

### External Documentation Site
Not applicable.

### Inline Documentation
Update the `sdk_ever_output` derivation docstrings in `session_health.py` (all four sites now call
the imported `agent.session_runner.liveness.derive_sdk_ever_output`, including
`_tier2_reprieve_signal`) and the `record_turn_boundary` docstring for the new `session_id` param.

## Success Criteria

- A headless session whose turn streams `init` and then produces stdout with NO tool call for >150s
  is NOT classified `zombie_uuid_no_output` and NOT transitioned `running→failed` with
  `kind=no_progress` — verified by a deterministic unit test over the session-health derivation.
- **A toolless-streaming session past the reprieve cap (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES`)
  but with fresh `last_stdout_at` is NOT suppressed by `_tier2_reprieve_signal` (site 4) — verified
  by a unit test; this closes the second wedge route (critique BLOCKER).**
- `sdk_ever_output` derives True when any of `last_tool_use_at`, `last_turn_at`, or `last_stdout_at`
  is set — verified by `agent.session_runner.liveness.derive_sdk_ever_output` unit tests over all
  combinations, living in `tests/unit/session_runner/test_liveness.py`.
- **All four derivation sites route through the single imported function, owned by
  `agent/session_runner/` (owner directive): `grep -n "sdk_ever_output = bool("
  agent/session_health.py` and `grep -n "_sdk_ever_output = bool(" agent/session_health.py` both
  return zero hits, and `grep -c "from agent.session_runner.liveness import derive_sdk_ever_output"
  agent/session_health.py` returns exactly `1`.**
- `SessionRunner._build_driver` wires `on_stdout_event` (0-arg adapter) and an `on_init` adapter that
  still persists resume scalars; a runner unit test asserts `last_stdout_at` is stamped on
  stdout/init AND that `claude_session_uuid` persistence is unaffected.
- **The real driver stream fires `on_stdout_event` during a toolless window — verified by the
  fake-harness driver-seam test in `test_headless_role_driver.py` (Concern 4).**
- `record_turn_boundary(session_id=...)` writes `last_turn_at` keyed on the true
  `AgentSession.session_id` (not the env `agent_session_id`, not the Claude UUID) without depending on
  worker `os.environ` — verified by unit test.
- A post-`init` stall (stdout goes stale after the init stamp) is still recovered — NOT via the
  never-started gate (it correctly no longer fires, since `init` is real output) but via the
  **whole-turn deadline**: with a small injected `turn_timeout_s`, a fake-harness turn that emits
  `init` then hangs is preempted/killed through the turn-deadline path (`role_driver.py:404`
  `asyncio.wait_for` → `outcome.hung=True` / `exit_reason="headless_turn_timeout"`, and/or the
  `runner.py:764` watcher `_kill_turn(cause="timeout")`). Verified by a deterministic regression test
  (Risk 1 guard). The detection-latency tradeoff (turn deadline, not ~150s never-started gate) is
  documented and accepted.
- `python -m ruff format . && python -m ruff check .` clean; the named unit tests pass.

## Step by Step Tasks

### 1. Red-first repro tests
Write failing unit tests: (a) the (not-yet-existing) `agent.session_runner.liveness.derive_sdk_ever_output`
returns False today when only `last_stdout_at` is set (documents the bug) — assert it feeds BOTH the
never-started site and the `_tier2_reprieve_signal` site so the reprieve route is covered; (b) a
runner test asserting `on_stdout_event` is wired (fails today); (c) a driver-seam test asserting a
toolless fake-harness window fires `on_stdout_event` (Concern 4, fails today). Add the Risk-1 guard
test skeleton.

### 2. Extract and relocate the derivation — all four sites, owned by `agent/session_runner/`
Create `agent/session_runner/liveness.py` with module-level `derive_sdk_ever_output(entry)`. In
`session_health.py`, import it and replace ALL FOUR inline expressions (`:985`, `:1127`, `:1310`,
`:2149`) with calls to the import, adding `last_stdout_at`. Update docstrings to point at the new
module. Run the three-grep BLOCKER gate (two zero-hit inline-expression checks, one single-hit
import check). Flip test (a) green at both sites; add
`tests/unit/session_runner/test_liveness.py` for the relocated function's own unit coverage.

### 3. Wire `on_stdout_event`/`on_init` liveness in the runner (two adapters)
Add the `_stamp_stdout_liveness` helper to `SessionRunner`. Pass a 0-arg adapter as
`on_stdout_event`, and a 1-arg `on_init` adapter that delegates to `_on_harness_init` (preserving
`persist_resume_scalars`) then stamps. Fail-silent + **per-session-keyed** 5s cooldown (CRITIQUE
pass 3 BLOCKER fix). Emit the debug-level `stdout_liveness_stamped` log on the success branch (Risk
1 observability note). Flip tests (b) and (c) green; confirm the resume-scalar persistence test
still passes.

### 3.5. Delete the dead duplicate liveness writer (CRITIQUE pass 3, Prior Art)
Remove `agent/session_executor.py:1509-1518`'s `_on_stdout_event` closure and its
`on_stdout_event=_on_stdout_event` kwarg in the `BossMessenger(...)` construction (`:1520-1528`) —
a prior, unlanded, uncoordinated attempt at the same signal, dead in production (`messenger` is
never passed to `SessionRunner`). Run `grep -rn "notify_stdout_event\|on_stdout_event"
agent/session_executor.py` and confirm zero remaining references. This is an independently
revertible commit, separate from Step 3's new runner-owned writer — a build that only did Step 3
would (harmlessly) leave two writers; this step is what makes the single-authoritative-module
directive actually hold post-merge.

### 4. Fix `record_turn_boundary` id resolution (independently revertible — see fourth-revision note)
Add the optional `session_id` param. Plumb the true `AgentSession.session_id` from the runner
(`runner.py:431`) through the `sdk_client.py` stream fn to the result-event call site (`:2936`),
which passes it explicitly — NOT `data.get("session_id")` (Claude UUID). Keep the `os.environ`
fallback for the in-subprocess CLI-hook call sites. Land as its own commit within the PR so it can
be reverted alone post-merge without reopening the Elements-1+2 wedge fix, per the fourth-revision
CRITIQUE note (this element does not by itself close the toolless-streaming wedge).

### 5. Risk-1 regression guard
Confirm a post-`init`-then-hang turn is still recovered by the **whole-turn deadline** (NOT the
never-started gate, which correctly no longer fires once `init` stamped `last_stdout_at`). Write a
deterministic driver/runner test with a small injected `turn_timeout_s` and a fake harness that emits
`init` then hangs; assert the turn is preempted/killed via the turn-deadline path (`role_driver.py:404`
`asyncio.wait_for` → `outcome.hung=True` / `exit_reason="headless_turn_timeout"`, and/or the
`runner.py:764` watcher `_kill_turn(cause="timeout")`). Do NOT assert any session-health idle-gap
detector on `last_stdout_at` freshness — none exists (verified zero grep hits).

### N-1. Documentation
Update `docs/features/headless-session-runner.md` and the session-lifecycle/session-health reference
per the Documentation section.

### N. Final Validation
Run the named unit tests + `ruff`. Run the three BLOCKER-gate greps (two zero-hit, one single-hit
import), plus `grep -rn "notify_stdout_event\|on_stdout_event" agent/session_executor.py` (Step 3.5,
must be zero hits). Confirm no mid-turn stall detector was loosened: the diff in `session_health.py`
must touch ONLY the four `sdk_ever_output` derivation sites (now a single-line call to the imported
`agent.session_runner.liveness.derive_sdk_ever_output`) plus the new import — no derivation logic
remains inline in `session_health.py`. The reprieve *cadence* logic and the per-tool/idle-gap
detectors are unchanged; only the "no output ever" input to the reprieve-cap guard is broadened.
**Post-deploy, non-blocking (CRITIQUE pass 3 concern):** after the fix ships, grep
`logs/worker.log` for `stdout_liveness_stamped` to confirm the write path is actually firing, and
check the `{project_key}:session-health:recoveries:zombie_uuid_no_output` Redis counter
(`session_health.py:2154`) for a drop in new increments; note the result in the PR description.

## Verification

- Deterministic: unit tests over `agent.session_runner.liveness.derive_sdk_ever_output`, the runner
  `on_stdout_event` wiring, and `record_turn_boundary(session_id=...)`.
- Behavioral (manual, post-merge, optional): re-run `/do-sdlc` for #1933/#1934 and confirm the PLAN/
  CRITIQUE transition no longer wedges; watch `logs/worker.log` for `last_stdout_at` freshness and
  absence of `zombie_uuid_no_output` recovery.

## Open Questions

1. **Should `last_stdout_at` freshness (not just presence) gate a mid-turn stall detector too?**
   This plan uses `last_stdout_at` only to satisfy the "SDK ever produced output" never-started gate.
   **Verified during this revision:** `session_health.py` has NO detector keyed on `last_stdout_at`
   or `last_activity` freshness (zero grep hits) — so the only backstop for a post-`init` hang is the
   coarse whole-turn deadline (`turn_timeout_s`, up to 7200s for PM/eng; see Risk 1). A follow-up
   could add a first-class mid-turn idle-gap detector on `last_stdout_at` *freshness* (the true
   headless analogue of PTY Gap B's `last_pty_read_loop_at`) to catch a post-`init` hang closer to the
   ~150s mark instead of at the turn deadline. Deferred as a scope expansion — the whole-turn deadline
   already recovers the hung turn, so this is a latency optimization, not a correctness gap.
2. **Was the observed wedge a toolless turn, or did a tool call fire but the sidecar liveness write
   no-op?** Recon strongly indicates toolless (no tool/liveness log lines at all), and the fix is
   robust either way. If Build's repro shows the sidecar path also failing under the worktree cwd,
   add a sidecar-resolution assertion; otherwise leave the CLI-hook path untouched.
