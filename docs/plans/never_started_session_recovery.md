---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/valorengels/ai/issues/1724
last_comment_id:
revision_applied: true
---

# Recover hung granite sessions promptly: progress-based liveness for the priming wedge AND the mid-run wedge

## Problem

As of commit `ef53a88f`, the in-container 120s `dev_hang`/`pm_hang` deadline became a 12h *sanity ceiling* (`CYCLE_IDLE_TIMEOUT_S = 12 * 60 * 60.0`, `agent/granite_container/container.py:138`), with the code comment explicitly delegating hang detection to "the heartbeat / liveness-recovery layer (agent/session_health.py + issue #1724)." **The session-health/liveness layer is now the sole hang detector for granite sessions.** This issue therefore owns the whole problem, in two distinct wedge shapes:

**(a) Priming wedge (`never_started`).** A granite session is `running` but produced no turn (`last_turn_at = None`, `sdk_ever_output = False`), while its watchdog writes fresh `last_heartbeat_at`/`last_sdk_heartbeat_at` every ~60s. Two detectors disagree:
- `reflections/stall_advisory.py::run_stall_advisory` classifies it `STALLED reason=never_started` at a 120s grace (`agent/session_stall_classifier.py::NEVER_STARTED_GRACE_SECS = 120`), but is **advisory-only** — logs/optionally-alerts, never recovers.
- `agent/session_health.py::_has_progress` sub-check B treats the fresh heartbeat as "alive" across `300s <= running_seconds <= NO_OUTPUT_BUDGET_SECONDS (1800s)` (`session_health.py:784-824`), and even past 1800s the granite PTY pair's live children let `_tier2_reprieve_signal`'s `children`/`alive` gates grant up to `MAX_NO_OUTPUT_REPRIEVES (20)` reprieves (`:943`). Net: ~100 min before recovery, not ~300s.

**(b) Mid-run wedge.** A session that produced turns (`sdk_ever_output = True`) then genuinely hangs during a long Dev turn — the TUI froze/crashed but the `claude` process is still alive. The container used to catch this at 120s; that deadline is now 12h. Worse, the session-health layer **cannot** catch it today: `_check_tool_timeout` would flag the stuck in-flight tool, but `_tier2_reprieve_signal`'s `"alive"` gate reprieves any session whose process exists — and a hung-but-alive process passes `"alive"` forever. So a mid-run wedge is unrecoverable for up to 12h.

### Why the obvious signals don't work
- **Heartbeats lie.** The watchdog ticks on a schedule regardless of real work (the original bug). Not progress evidence.
- **Process-alive lies.** A hung TUI keeps an alive `claude` process; `"alive"` reprieve fires forever. Not progress evidence.
- **Transcript silence lies (the critical one).** The transcript tailer (`agent/granite_container/transcript_tailer.py`) folds an **append-only JSONL**: `byte_offset`, `total_*_tokens`, `turn_count`, and `last_tool_use_at` only advance when an assistant/user event lands. During a legitimate long `Task` subagent or long tool, **no event lands in the parent transcript for the entire run** (observed: subagents run 25+ min). All those fields freeze on a perfectly healthy session. A naive "transcript unchanged for N minutes → recover" rule would false-kill exactly the long-subagent case — the same mistake the old 120s `dev_hang` made.

### The reliable oracle
The signal that actually distinguishes alive from hung is the one the operator uses by eye: **the TUI screen repainting** — the spinner and token/elapsed counters tick continuously while work is happening, and freeze on a crash. `agent/granite_container/pty_driver.py` already computes byte-quiescence (C5 idle: a hung PTY goes byte-silent within ~seconds; a working PTY emits continuously), but this liveness is transient inside the container read loop and **is never persisted onto the `AgentSession`**. Capturing it is the core of the mid-run fix.

But "screen frozen" read from a *single* persisted timestamp is itself a trap: if the writer that stamps that timestamp dies or throttles while `claude` lingers, the timestamp goes stale exactly during the wedge, and a stale-but-present value is indistinguishable from genuine quiescence — the detector would then key off its own dead writer. The fix is to persist **two** signals, not one: a **loop-alive marker** stamped every read-loop iteration unconditionally (proves the writer is still cycling), and a **screen-changed marker** stamped only when the normalized buffer differs (proves the screen is repainting). Genuine quiescence is "loop marker fresh AND screen marker stale"; a dead writer is "loop marker stale" and forces the detector to abstain. See Architectural Impact for the three-state breakdown.

But "the screen is frozen" is necessary, not sufficient — it splits into two jobs that must NOT be conflated:

- **Job 1 — is the screen moving? (mechanical, always-on, free.)** A normalized hash/diff of the screen buffer between reads answers this deterministically in microseconds, immune to local-model load. This is the cheap gate. An LLM here would only add latency, nondeterminism, and a contention failure mode — the bottom-bar token/elapsed counter literally repaints every second while working, so byte-activity already *is* the liveness bit.
- **Job 2 — the screen is frozen; WHY? (interpretive, triggered, rare.)** A frozen screen can mean `CRASHED` (recover), `WAITING` at an idle prompt (turn genuinely done — do not recover), `DIALOG` (trust-folder / update notice — do not recover, needs a keystroke not a kill), or — critically, given flaky Claude API connections — **`RETRYING` / `API_ERROR`**, where the session is alive and waiting out a transient network/API hiccup and will self-heal. Recovering a `RETRYING` session is a false-kill of healthy work. Regex/glyph heuristics for this disambiguation are brittle across ANSI noise and Claude Code version changes; this fuzzy classification is exactly where the **local granite classifier** earns its keep — but only as a second stage fired on the handful of suspects the cheap gate flags, never as a polling loop over every session (which would load the same local model that does live message classification, and degrade precisely under the memory/CPU pressure that correlates with hangs).

### Desired outcome
- **Priming wedge:** a `running` + `last_turn_at = None` + fresh-heartbeat session is recovered on a grace reconciled with the advisory's 120s detection grace — and the fix closes **both** sub-check B's band and the reprieve-cap path.
- **Mid-run wedge:** a session whose **PTY screen has gone quiescent** for a generous window (no spinner/counter repaint) while the transcript has not advanced, **and which a triggered classifier confirms is `CRASHED`** (not waiting, not on a dialog, not retrying an API hiccup), is recovered well below the 12h ceiling — **without** killing a legitimately long actively-repainting turn, an idle-at-prompt session, or a session waiting out a flaky Claude API connection.
- Grace/window values derive from single shared sources of truth so detection and action cannot drift.
- The detector is **resilient by construction**: it errs toward inaction, requires multiple independent confirmations before killing, and degrades safely (does nothing this pass) whenever any input is missing, slow, or low-confidence — because every layer here (PTY rendering, local inference, the Claude API) is non-deterministic and flaky.

### Real observation (2026-06-18)
Session `tg_valor_-1003449100931_993` (`agent_session_id 7f819c9a7efa46f1b2b8b10ef1d34dfc`) was `running` + heartbeating with `last_turn_at = None` for ~5 min (priming wedge). `stall-advisory` logged `STALLED reason=never_started elapsed_secs=247 grace_secs=120` at 02:48:53; nothing recovered it until a manual `valor-service.sh restart`.

## Freshness Check

**Baseline commit at original plan:** b414eed1 (recon for the narrow scope). **Re-baselined to:** ef53a88f + the transcript-tailer fixes (995bc453, 91289fc3, b414eed1).
**Disposition:** **CHANGED.** The original plan assumed (1) scope = priming wedge only, and (2) granite exposes no usable progress fields. Both are now false: `ef53a88f` removed the container's 120s hang detector (mid-run wedge now in scope), and the tailer fixes populate real progress fields. This plan supersedes the narrow version.

**File:line references re-verified against current main:**
- `agent/granite_container/container.py:121-138` — `CYCLE_IDLE_TIMEOUT_S` now 12h, comment delegates to session_health + #1724. ✓
- `reflections/stall_advisory.py:38-161` — advisory-only, zero writes. ✓
- `agent/session_stall_classifier.py:52` — `NEVER_STARTED_GRACE_SECS = 120` (the single source of truth; advisory consumes it at `:206`/`:212`). ✓
- `agent/session_health.py:207-269` — `AGENT_SESSION_HEALTH_MIN_RUNNING` / `STARTUP_GRACE_SECONDS = 300` (race-guard floor, env-tunable). ✓
- `agent/session_health.py:284` — `NO_OUTPUT_BUDGET_SECONDS = MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW = 1800`. ✓
- `agent/session_health.py:345-369` — `_check_tool_timeout`, tiers 30/120/300s. ✓
- `agent/session_health.py:784-824` — sub-check B fresh-heartbeat band. ✓
- `agent/session_health.py:884-981` — `_tier2_reprieve_signal` (`compacting`/`children`/`alive`; cap at `:943`). ✓
- `agent/granite_container/bridge_adapter.py:626-648` — `_bump_last_turn_at` (flips `sdk_ever_output`). ✓
- `agent/granite_container/bridge_adapter.py:837-934` — diff-gated tailer save of `turn_count`/`current_tool_name`/`last_tool_use_at`/`byte_offset`/token counts. ✓
- `agent/granite_container/transcript_tailer.py:105-174` — append-only fold; confirms transcript freezes during in-flight tool with no following event. ✓
- `agent/granite_container/pty_driver.py:36, 212, 353, 491` — byte-quiescence (C5 idle) computed but not persisted. ✓

**Cited sibling issues/PRs re-checked:** #1356 (CLOSED, introduced 1800s gate), #1614 (CLOSED, gated sticky own-progress on heartbeat freshness), #1172 (CLOSED, "kill only on positive no-progress evidence, never on staleness"), #1226 (CLOSED, the 20-tick reprieve cap). All resolutions unchanged.

**Test-path correction (critique B2):** the original plan cited `tests/unit/test_agent_session_health_monitor.py` 4×. That file lives in **`tests/integration/`**. Corrected throughout this plan.

## Prior Art

- **#1356** (closed): bounded sub-check B's previously-infinite fast-path at `NO_OUTPUT_BUDGET_SECONDS = 1800`. **Partial** — 30 min is still long, the value is internally-derived (`20 * 90`) with no tie to the advisory's 120s, and it left the reprieve-cap path untouched. Direct parent of this issue. **Do not repeat its partial-fix shape** (touching only one of the two gates).
- **#1614** (closed): gated sticky own-progress fields (`turn_count`/`log_path`/`claude_session_uuid`) on heartbeat freshness so a stale `running` session can't evade recovery. **Success.** Reuse `tests/unit/test_session_health_inference_removed.py` as the regression-test model.
- **#1172** (closed): retired wall-clock caps and the "stdout" reprieve gate; established "kill only on positive no-progress evidence, never infer death from staleness." **Constrains this fix**: the mid-run oracle must be positive evidence of a frozen screen, not mere transcript staleness.
- **#1226** (closed): added `MAX_NO_OUTPUT_REPRIEVES` cap (only for `sdk_ever_output=False`). The priming wedge survives it because the granite pair keeps live children → `children`/`alive` reprieves keep firing until the cap.
- **`ef53a88f`** (this week): demoted the container's 120s deadline to a 12h ceiling and handed hang detection to this layer. Read its `container.py:121-138` comment — it states the intent this plan fulfills.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1356 | Bounded sub-check B at 1800s | Value untied to the advisory's 120s; ignored the reprieve-cap path; 30 min still long for a zero-progress session. |
| #1226 | 20-tick reprieve cap (no-output only) | Same 1800s-class timescale; the priming pair's live children keep `children`/`alive` reprieves firing up to the cap. |
| container 120s `dev_hang` | Killed any PTY not idle within 120s | Killed legit multi-minute turns (false positive) → removed in `ef53a88f`, leaving a 12h hole for the mid-run wedge. |

**Root-cause pattern:** every prior signal (heartbeat, process-alive, transcript-staleness, wall-clock) is either a false-positive (kills long-but-working turns) or a false-negative (reprieves hung-but-alive sessions forever). None measures the one thing that is ground truth: **is the TUI screen still doing anything.**

## Data Flow

**Priming wedge (path A):** bridge enqueues Eng `AgentSession` → worker `status=running`, spawns granite PTY pair → `_heartbeat_loop` ticks → priming runs, no `turn_start`, `last_turn_at=None`, `sdk_ever_output=False`. Advisory classifies `never_started` at 120s (read-only, dead end). **Today** the only recovery actor `elif` (`:1708`, in the 300s main loop) is hard-gated by `running_seconds > 300s` before it even calls `_has_progress` AND is only visited every 300s; sub-check B returns True up to 1800s; past that, `_tier2_reprieve_signal` `children`/`alive` reprieves the live pair up to the 20-cap (~100 min). **After this fix:** a NEW dedicated never-started check hosted in the **30s** `_agent_session_tool_timeout_check` tick (D0) fires at grace+margin rounded to the next 30s tick (~125-155s) — independent of the 300s floor for the no-turn case only — and the reprieve guard at `:943` suppresses `children`/`alive` for the same predicate, so `_apply_recovery_transition(reason_kind="no_progress")` → `running -> pending` happens at ~125-155s instead of ~100 min. Sub-check B is also intercepted so any `_has_progress` caller (the 30s tool-timeout loop, the 300s main loop) agrees on the never-started verdict.

**Mid-run wedge (path B):** session produces turns (`sdk_ever_output=True`) → enters a long Dev turn / `Task` subagent → parent transcript stops emitting events (byte_offset/tokens/turn_count/last_tool_use_at all freeze) → if the turn genuinely hangs, the TUI screen freezes but the `claude` process stays alive. `_check_tool_timeout` flags the stuck `current_tool_name`, but `_tier2_reprieve_signal` `"alive"` reprieves it forever (process exists). No recovery until the 12h ceiling.

The fix lands in: (path A) the new never-started branch in the 30s loop, sub-check B's never-started leg, **and** the reprieve evaluation; (path B) a new PTY-activity signal persisted from the container read loop, consumed by a two-stage mid-run leg **also hosted in the 30s `_agent_session_tool_timeout_check` tick** (D0 — the 30s cadence is what makes the 180s/K=2 quiescence gate observable at all) — a free byte-diff quiescence gate, then a triggered granite classifier (fail-safe) that must confirm `CRASHED` before any recovery.

## Architectural Impact

- **New persisted signals — TWO fields, not one (resolves BLOCKER).** The mid-run leg cannot rest on a single timestamp, because a single `last_pty_activity_at` overloads `None`/stale across three different physical states and goes silently stale in exactly the wedge it must detect. We therefore persist two distinct fields on `AgentSession`, written from the granite container:
  - **`last_pty_read_loop_at` (datetime) — the monotonic loop-alive marker.** Stamped on *every* PTY read-loop iteration, unconditionally, regardless of whether the screen changed. This proves the read loop *itself* is still cycling. It advances even when the screen is frozen (a working-but-idle prompt or a hung TUI still cycles the read loop), and only goes stale when the read loop is dead/blocked.
  - **`last_pty_activity_at` (datetime) — the screen-changed marker.** Stamped only when the normalized screen buffer *differs* from the prior read (the C5-style byte-activity `pty_driver` already detects). Fresh = the screen is repainting (spinner/counters ticking = live work). Stale-but-with-a-fresh-loop-marker = the screen is genuinely frozen.
  - These two fields together distinguish **three granite states** the single-field design conflated: (1) **loop alive + screen changing** → healthy, never a suspect; (2) **loop alive + screen frozen** (`last_pty_read_loop_at` fresh, `last_pty_activity_at` quiescent > window) → the mid-run wedge candidate; (3) **loop marker itself stale** → the writer is dead/throttled/blocked; the PTY signal is untrustworthy this pass, so the mid-run leg **abstains** and falls back to the existing process-alive behavior (D4 bias-to-inaction). Cross-checked against `last_heartbeat_at` (see Cross-check below).
  - Per `feedback_field_backcompat_heal`, nullable AgentSession fields need no extra back-compat code (`_heal_descriptor_pollution` walks fields generically).
- **Granite-ness is detected structurally, NEVER via a timestamp (resolves BLOCKER).** The mid-run leg's "is this a granite session?" decision is made from `session_type` / container attachment (the same way the executor already routes granite sessions), NOT from `last_pty_activity_at is None`. A granite session whose PTY writer never initialized reads both fields `None` — under the old design that read as "non-granite, skip" and the session was never evaluated. Under the revised design it is recognized as granite-with-no-PTY-signal-yet and handled by state (3) above (abstain this pass, re-evaluate next tick) rather than silently excluded forever. Non-granite sessions are excluded by `session_type`, leaving both fields `None` and unaffected.
- **PTY freshness is cross-checked against the heartbeat (resolves BLOCKER).** Before the mid-run leg trusts a "screen frozen" reading, it requires `last_pty_read_loop_at` to be fresh relative to `last_heartbeat_at` — i.e. the loop marker must be no more stale than `HEARTBEAT_FRESHNESS_WINDOW` behind the heartbeat. If the loop marker has fallen behind the heartbeat (the writer died while the watchdog kept ticking), the PTY signal is treated as untrustworthy (state 3, abstain). This makes a dead/throttled writer fail toward *no recovery*, never toward a kill on a stale-but-present timestamp.
- **Import direction (unchanged constraint):** `session_health` → `session_stall_classifier` only (shared grace constant); the classifier must never import `session_health` (guarded by `tests/integration/test_stall_advisory_e2e.py`). The new PTY-activity field is plain data on the model — no new module coupling.
- **Single writer for recovery:** only `session_health` ever writes recovery transitions; `stall_advisory` stays read-only. No double-action.
- **Classifier dependency (stage 2):** reuse the existing local granite/ollama classifier path already used for message classification (no new model). The dependency is *soft* by design — stage 2 is best-effort and fails toward "leave alone" (D4/Risk 6), so a classifier outage degrades to no-recovery, never to a crash or a wrong kill.
- **Reversibility:** Medium-high. Path-A change is a tighter branch + reprieve guard + one shared predicate + one constant. Path-B adds two fields + a writer + a recovery leg; revertible by ignoring the fields and restoring the prior reprieve behavior.

## Appetite

**Size:** Large (was Medium — mid-run scope + the new persisted PTY signal raise it).

**Team:** debugging-specialist (recovery logic + reprieve guard), an agent for the container/tailer PTY-activity writer, test-engineer (both wedge regressions), validator, documentarian.

**Interactions:** PM check-ins 1-2 (the PTY-activity-signal approach and the mid-run window are the load-bearing decisions); review rounds 1-2 (this is now the sole hang detector — careful pass on both false-kill and false-negative).

**Scope decision (rebuts concern #2 — "ship priming leg alone, defer the classifier"):** the critic is right that this is one reported bug with two fixes of unequal complexity, and that the priming leg (tasks 1-2) is the simpler, higher-confidence win. We deliberately keep both legs in one issue, but **stage the work so the simple leg never blocks on the complex one and the complex leg cannot ship half-validated**:
- Path A (tasks 1-2) is independent and lands first; it alone fully fixes the *reported incident* (the priming wedge in "Real observation"). If path B's spike fails or the classifier proves unworkable, path A still ships as #1724's fix.
- Path B is built behind the spike-1 gate (concern #1) and stage-2 is built strictly after stage-1 (3a → 3b), so the read-only/advisory-shaped cheap gate is proven before any classifier-driven kill is wired. This is the critic's "build stage-1 first, defer the classifier" sequencing, kept inside one issue rather than split into two.
We reject *splitting into two issues* (rather than two staged legs) because the container's `ef53a88f` change handed *both* wedge shapes to this layer simultaneously; tracking them apart would leave the mid-run 12h hole owned by no open issue. The staging above captures the critic's risk-reduction intent without that gap.

## Prerequisites

None external. The transcript-tailer progress fields and PTY byte-quiescence detection already exist on main; this work consumes and persists them.

## Solution

### Decisions locked (from PM, 2026-06-18)
- **D0 (HOST LOOP — both recovery branches live in the 30s tool-timeout loop, NOT the 300s health loop) (resolves the cadence BLOCKER).** The actor body `_agent_session_health_check()` (`agent/session_health.py:1532`) is invoked only by `_agent_session_health_loop()` (`:2168`), whose `asyncio.sleep` is `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300s` (`:206`, `:2187`). A predicate that becomes true at the ~125s grace but is only *visited* every 300s recovers somewhere in ~125-425s depending on loop phase — so the headline goal and the first Success Criterion ("recovered within `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` of start", ~120s+margin) are provably false on a bad phase. Worse, the mid-run stage-1 gate (180s continuous quiescence confirmed across K consecutive ticks) is structurally **impossible** to observe with a 300s sampler. **Therefore BOTH the path-A (never-started) and path-B (mid-run) recovery branches are hosted in the existing 30s sub-loop `_agent_session_tool_timeout_loop` (`:2298`, `TOOL_TIMEOUT_LOOP_INTERVAL = 30`, `:303`), inside its per-tick body `_agent_session_tool_timeout_check` (`:2190`).** That loop already (a) scans every `running` `AgentSession` each 30s (`AgentSession.query.filter(status="running")`, `:2217`), (b) carries the terminal-status guard + fresh-re-read race mitigation, and (c) routes recoveries through the shared `_apply_recovery_transition` (`:2284`) — the same transition the main loop uses. The new branches reuse that scan + transition; they do **not** add a third loop. The main 300s loop and its existing `:1708` no_progress `elif` are left exactly as-is (they remain a slower backstop for the output-producing path and stay correct because their cadence has always matched their 300s-floor predicate). **Cadence reconciliation:** with a 30s host tick, path-A recovers at `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` rounded up to the next 30s tick boundary (~125-155s, within the headline goal — never 425s); and path-B's `MID_RUN_QUIESCENCE_SECS = 180s` window confirmed across `K = 2` consecutive ticks is `K * TOOL_TIMEOUT_LOOP_INTERVAL = 60s` of confirmation sampling layered over the 180s quiescence floor, which is only derivable because the sampler period (30s) is far below both 180s and the grace. The 30s host loop is the pin that makes the entire K / 180s / cadence triple consistent.
- **D1 (priming recovery timescale): a DEDICATED never-started recovery branch in the 30s host loop (D0), keyed off ONE shared predicate, that also intercepts sub-check B and the reprieve path (resolves the BLOCKER and concern #4).** The 300s race guard cannot honor the advisory's 120s, and (per D0) the 300s loop cannot even *visit* the predicate often enough. For `never_started` sessions specifically, the recovery actor must evaluate/act on the 30s cadence, reconciled to the shared `NEVER_STARTED_GRACE_SECS (120s)` plus a small confirmation margin. **Critical mechanic prior drafts got wrong:** there are *two* independent 300s-anchored gates on the never-started path, and relaxing only one is dead code.
  - **Gate (1) — a NEW dedicated recovery branch hosted in `_agent_session_tool_timeout_check` (`:2190`), the 30s loop's body (D0).** The existing main-loop actor `elif` at `agent/session_health.py:1708` reads `elif running_seconds is not None and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING (300s) and not _has_progress(entry)` — hard-gated by the 300s floor *and* only visited every 300s. Relaxing the predicate *inside* that elif would be doubly dead (never entered below 300s, never sampled below 300s). **The fix instead adds a dedicated never-started check to the 30s tool-timeout tick**, sitting alongside the existing `_check_tool_timeout` hit-path and routing through the same `_apply_recovery_transition`. Conceptually, for each `running` session the 30s tick already iterates, before/beside the tool-timeout check:**
    ```python
    if _never_started_past_grace(entry, now):
        # 30s-cadence recovery; reason carries "no progress signal"
        # so _reason_kind resolves to "no_progress" (NOT "worker_dead").
        await _apply_recovery_transition(
            fresh, reason="no progress signal observed (never_started past grace)",
            reason_kind="no_progress", handle=handle, worker_key=fresh.worker_key,
        )
    ```
    The reason string **must contain the substring `"no progress signal"`** so `_reason_kind` resolves to `"no_progress"` — NOT `"worker_dead"`. The branch must reuse the loop's existing fresh-re-read race mitigation (re-read `fresh`, re-confirm the predicate on the fresh row before transitioning) exactly as the tool-timeout path does (`:2233-2254`). This fires at `grace + margin` rounded up to the next 30s tick (~125-155s). The **main loop's `:1708` elif and the global 300s floor are left exactly as-is** — output-producing sessions never satisfy `_never_started_past_grace` (the predicate requires no turn ever) and so keep the full 300s race guard on that slower backstop path. Do **not** loosen the global 300s floor.
  - **Gate (2) — sub-check B inside `_has_progress` at `:811/818`** returns `True` whenever `running_seconds < STARTUP_GRACE_SECONDS (300s)`, holding a never-started session "alive" via its fresh heartbeat. The same `_never_started_past_grace` predicate is consulted here so that when it is true sub-check B does **not** grant the sub-300s heartbeat fast-path. (This gate is reached by the tool-timeout loop and any other `_has_progress` caller even though the new actor branch above bypasses it for the priming-wedge actor path; intercepting both is what makes the relaxation complete rather than half — exactly the partial-fix shape #1356 fell into.)
  - **One shared predicate.** `_never_started_past_grace(entry, now) -> bool` is true when the session has never produced output (one canonical `sdk_ever_output` definition reused everywhere) AND `running_seconds > NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`. Both the new actor branch and sub-check B call it. This is a deliberate, scoped relaxation of the race guard for the no-turn case only.
- **D2 (mid-run oracle): cheap-gate-then-judge, not transcript silence.** Stage 1 (free, always-on): a normalized PTY-buffer diff → is the screen quiescent beyond the window across K consecutive ticks, with the loop-alive marker fresh? Stage 2 (triggered, only on stage-1 suspects): the local granite classifier reads the frozen buffer and labels it `CRASHED / WAITING / DIALOG / RETRYING`. Recover **only** on `CRASHED`. Never recover on transcript-staleness alone (false-kills long subagents) and never on a frozen screen alone (false-kills idle-at-prompt, dialogs, and API retries).
  - **`byte_offset`-not-advanced is an OPTIONAL corroborator, NOT a gating conjunct (resolves concern #3).** The prior draft required `byte_offset` unchanged as a required AND-term of the stage-1 gate. This term has near-zero discriminating power for the case we care about: during a long `Task` subagent the parent transcript emits no events *whether the session is healthy or hung*, so `byte_offset` is frozen in both branches — it cannot separate them, and adding it as a required conjunct only creates a way for the gate to mis-fire if the offset *does* tick (e.g. a stray event) and wrongly clears a genuinely-frozen suspect. **The sole gating condition is PTY screen quiescence (`last_pty_activity_at` stale) sustained across K consecutive ticks with `last_pty_read_loop_at` fresh.** `byte_offset`-not-advanced is recorded and logged as a corroborating observation only (it raises confidence when present, never required, never able to veto). A regression test pins that a suspect with quiescent PTY is flagged even if `byte_offset` happens to have advanced once.
- **D3 (reprieve cap, educated guess to validate in build): bypass the `children`/`alive` reprieve for `never_started` — ownership and ordering specified (resolves concern #1).** Live PTY children are *expected* during priming and are not progress evidence, so a no-turn session past its grace must not be reprieved by `children`/`alive`. **Owner: `_tier2_reprieve_signal` (`agent/session_health.py:884-981`) is the sole place this is decided; the predicate must not be re-evaluated anywhere else that could independently re-grant the reprieve.** **Ordering: the never-started bypass is inserted as the FIRST check inside `_tier2_reprieve_signal`, immediately reusing/extending the existing `#1226` escalation guard at `:943` (`if not sdk_ever_output and reprieve_count >= MAX_NO_OUTPUT_REPRIEVES: return None`), which already sits *before* the `compacting`/`children`/`alive` gates (`:950-974`).** Concretely: replace the cap-only condition with `if not sdk_ever_output and (reprieve_count >= MAX_NO_OUTPUT_REPRIEVES or _never_started_past_grace(entry, now)): return None`. Because this `return None` fires before any of the three reprieve gates are reached, a never-started-past-grace session can never have `children`/`alive` independently re-grant the wedge — the cap is effectively *ignored* (its `>= MAX_NO_OUTPUT_REPRIEVES` term becomes moot) for never-started sessions, collapsing the ~20-tick (~30-100 min) reprieve window to the grace+margin. The `compacting` gate is deliberately NOT bypassed (a session mid-compaction has a legitimate reason to be quiet), but compaction cannot occur before the first turn so this is a no-op for true never-started sessions. Validate via tests that this does not regress (a) a genuinely-still-priming session about to emit its first turn, and (b) the existing cap/`children`/`alive`/`compacting` behavior for output-producing sessions (`sdk_ever_output=True` never enters the bypass).
- **D4 (resilience posture — overrides ambiguity everywhere): bias to inaction, confirm before killing, degrade safely.** Concretely: (a) recovery requires the stage-1 gate to hold across **K consecutive health ticks** (not a single reading) so one flaky read/render can't trigger a kill; (b) the stage-2 classifier runs with a hard timeout, structured output, a fixed label set, low temperature, and **defaults to a non-`CRASHED` label on any failure, timeout, unavailability, or low confidence** — a granite outage must never cause recoveries, only suppress them; (c) `RETRYING`/`API_ERROR` is treated as alive (Claude API flakiness self-heals); (d) the whole stage-2 path is best-effort and never raises into or blocks the health loop. When in doubt, leave the session alone — a wedge that persists one more cycle is cheaper than killing live work (#1172's principle).
- **D4-observability (resolves concern #6): the fail-safe default must be observable, because it silently re-creates the pre-fix "never recovers" state.** Defaulting stage 2 to non-`CRASHED` on every failure is correct for safety, but a *persistently* unavailable/timing-out classifier means genuine mid-run wedges silently go unrecovered forever — the exact bug this issue exists to fix, now hidden behind a safe-looking default. So every fall-through to the non-`CRASHED` default increments a dedicated **`tier2_classifier_fallback`** counter (distinct from the per-label and per-recovery counters), and `dashboard.json` surfaces a **fallback rate** (fallbacks ÷ stage-2 invocations over a trailing window) with a **threshold alert** when that rate exceeds a provisional ceiling (env-tunable, grain-of-salt). A high fallback rate is the signal that the mid-run leg has quietly degraded to no-op and the classifier dependency needs attention — it converts a silent regression into a visible one.

### Key Elements
1. **Shared never-started grace — ONE source of truth across all THREE consumers (resolves concern #3).** `agent/session_stall_classifier.py:52` `NEVER_STARTED_GRACE_SECS = 120` is the single canonical definition. There are **three** independent grace consumers that must not drift:
   - **(i) the advisory** — `session_stall_classifier.py` itself reads it at `:206`/`:212` to classify `STALLED reason=never_started`;
   - **(ii) the recovery actor** — `session_health` imports `NEVER_STARTED_GRACE_SECS` from `session_stall_classifier` (the existing one-way import edge: `session_health → session_stall_classifier`, never the reverse) and uses it inside `_never_started_past_grace`;
   - **(iii) any other caller** — none today, but the import edge is the only sanctioned access path.

   `session_health` adds **only** the small `NEVER_STARTED_CONFIRM_MARGIN_SECS` (its own confirmation margin), and never re-declares the grace. No second `120` literal exists anywhere. A drift-pin unit test (`test_session_stall_classifier.py`, Risk 4) asserts the actor's effective grace equals `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` and that `session_health` does not define its own grace constant — so a divergent edit to any of the three consumers fails CI. No second magic number.
2. **Never-started recovery leg (path A)** — one shared `_never_started_past_grace(entry, now)` predicate (D1) consulted by (i) a NEW dedicated recovery check hosted in the **30s** `_agent_session_tool_timeout_check` tick (`:2190`, per D0 — so the never-started recovery actually fires at ~125-155s instead of being unreachable/unsampled below the 300s floor), and (ii) sub-check B's grace floor (`:811/818`). When `not sdk_ever_output` and no turn ever and past grace: (a) the new 30s-loop branch calls `_apply_recovery_transition` with a reason string containing `"no progress signal"` (so `_reason_kind` resolves to `no_progress`, not `worker_dead`), reusing the loop's fresh-re-read race mitigation; (b) sub-check B does NOT grant the heartbeat fast-path; (c) `_tier2_reprieve_signal` does NOT grant `children`/`alive` for this session (D3). Output-producing sessions never satisfy the predicate and keep the 300s floor on the main loop's `:1708` backstop and both gates.
3. **Two PTY signals + two-stage mid-run leg (path B, new)** — persist `last_pty_read_loop_at` (loop-alive, stamped every read-loop iteration) AND `last_pty_activity_at` (screen-changed, stamped on non-quiescent normalized screen bytes) from the container read loop. Both stages are consumed in the **30s** `_agent_session_tool_timeout_check` tick (D0); "K consecutive ticks" throughout means K consecutive 30s tool-timeout-loop ticks (`K = 2` → 60s of confirmation over the 180s quiescence floor). Granite-ness is detected via `session_type`/container attachment, never via these timestamps.

   **Three-state consumption decision (resolves concern #2) — evaluated at the single point of consumption inside the stage-1 gate, keyed off the `last_pty_read_loop_at` loop marker first, THEN `last_pty_activity_at`:** for a session already confirmed granite (by `session_type`/container attachment) and `sdk_ever_output=True` with a tool/turn in flight, the leg branches on the loop marker before it ever looks at the screen-changed marker:

   | `last_pty_read_loop_at` (loop marker) | `last_pty_activity_at` (screen marker) | Physical meaning | Decision |
   |---|---|---|---|
   | **`None`** (writer never initialized) | `None` | Granite session, PTY writer hasn't started a single loop yet (state 3a) | **ABSTAIN** this pass; re-evaluate next tick. NOT excluded forever, NOT a kill. |
   | **Stale** (older than `HEARTBEAT_FRESHNESS_WINDOW` behind `last_heartbeat_at`) | any value (fresh, stale, or `None`) | Writer died/throttled/blocked while the watchdog kept heartbeating (state 3b) | **ABSTAIN** this pass; PTY signal untrusted; fall back to existing process-alive behavior. A stale screen marker here is NEVER read as quiescence. |
   | **Fresh** (within `HEARTBEAT_FRESHNESS_WINDOW` of `last_heartbeat_at`) | **Fresh** | Loop cycling AND screen repainting → live work (state 1) | **NOT a suspect.** Never a stage-1 candidate regardless of transcript age. |
   | **Fresh** | **Stale** > `MID_RUN_QUIESCENCE_SECS` across K ticks | Loop cycling but screen frozen → mid-run wedge candidate (state 2) | **Stage-1 suspect** → snapshot + proceed to stage 2. |

   The loop marker is checked FIRST and is the trust gate: only when it is fresh (state 1 or 2) is `last_pty_activity_at` consulted as a liveness signal at all. `None` and stale loop markers (states 3a/3b) both abstain — the leg never keys a kill off a screen marker whose writer it cannot prove is alive. This is the explicit handling for the writer-never-initialized (`None`) vs. fresh vs. stale ambiguity the BLOCKER and concern #2 flagged. Stage 1 (free): if the session is granite AND `sdk_ever_output=True` AND a tool/turn is in flight AND `last_pty_read_loop_at` is fresh relative to `last_heartbeat_at` (writer alive — else abstain) AND `last_pty_activity_at` has been quiescent > `MID_RUN_QUIESCENCE_SECS` across K consecutive ticks → mark as a suspect and **snapshot `(last_pty_activity_at, byte_offset)`** (concern #5). `byte_offset`-not-advanced is logged as a corroborator only, never a gating conjunct (concern #3). Stage 2 (triggered, per D4): classify the frozen buffer; recover only on `CRASHED`, and only if the snapshot tuple is still unchanged (CAS precondition). The `"alive"` reprieve must NOT override a `CRASHED` verdict, and conversely a non-`CRASHED`/failed/absent verdict must NOT recover.
4. **Preserved long-subagent safety** — a session whose screen is still repainting (`last_pty_activity_at` fresh) never even becomes a stage-1 suspect, regardless of how long the transcript or `last_tool_use_at` has been frozen. This is the primary regression guard for 25+ min subagents; the stage-2 classifier is the backstop for the frozen-but-not-crashed cases.
5. **Resilient by construction (D4)** — K-of-N confirmation on the cheap gate, a bounded best-effort classifier that fails toward "leave alone," and explicit `RETRYING`/`WAITING`/`DIALOG` handling so non-determinism in rendering, local inference, or the Claude API can only ever *suppress* a recovery, never cause a wrong one.

### Mid-run window (D2/D4) — value rationale
A live TUI repaints its spinner/counters on a sub-second-to-seconds cadence; a crash freezes it immediately. So `MID_RUN_QUIESCENCE_SECS` is a *confirmation* window over an already-strong signal, not a tolerance for legit silence. Proposed default **180s** of continuous screen quiescence (env-tunable `MID_RUN_QUIESCENCE_SECS`), required to hold across `K = 2` consecutive **30s tool-timeout-loop ticks** (D0 — `K * TOOL_TIMEOUT_LOOP_INTERVAL = 60s` of confirmation sampling layered on the 180s floor; this is only observable because the 30s host tick is far below 180s, which the 300s main loop could never satisfy) before stage 2 even runs — comfortably above transient repaint gaps and API-retry backoffs yet far below the 12h ceiling. The 25+ min subagent is protected by the *nature* of the signal (its screen keeps repainting), not by the window size; the API-retry case is protected by both the window and the stage-2 `RETRYING` label. **Open for confirmation** — see Open Questions Q-A.

## Spike Results

### spike-1 (BLOCKING, run at start of build — gates Step 3a): Does a >5-min `Task` subagent keep the PARENT PTY repainting?
- **Assumption**: "During a healthy multi-minute `Task` subagent, the parent granite TUI keeps repainting its spinner/elapsed/token counters, so `last_pty_activity_at` stays fresh and the long-subagent case never becomes a stage-1 suspect."
- **Method**: prototype (real PTY pair, worktree-isolated) — drive a parent PTY through a Task subagent running > 5 min; sample `last_pty_activity_at` and `last_pty_read_loop_at` throughout; then kill the child and confirm `last_pty_activity_at` goes quiescent within seconds while `last_pty_read_loop_at` keeps advancing.
- **Result**: _unresolved at plan time — this is a build-time gate, not a plan-time spike._ The premise is plausible (the bottom-bar counter is a parent-process render, not a child render) but **must be empirically confirmed before the stage-1 gate is wired into recovery**.
- **Confidence**: medium (plausible; unverified).
- **Impact if false**: path B switches from PTY-activity to the **subagent-transcript fallback** (discover the child JSONL, use its growth as liveness). Do not build both. Step 3a does not proceed to recovery wiring until this resolves.

> This spike is intentionally deferred to build start rather than run during planning, because it requires a real granite PTY pair and a multi-minute subagent — a >5-min live exercise that belongs in a worktree, not in the planning loop. It is recorded here as a **hard gate** on Step 3a (see Risk 2 and task `build-mid-run-gate`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Redis counter increments on the new fall-through legs are best-effort (log + continue); a counter failure must not block recovery (patch `incr` to raise, assert recovery still fires).
- [ ] PTY-activity writer in the container must never raise into the read loop; a write failure leaves `last_pty_activity_at` stale and falls back to the existing (process-alive) behavior — assert the writer swallows + logs.
- [ ] Stage-2 classifier (D4): on timeout, exception, ollama-unavailable, malformed/unparseable output, or low confidence → returns a non-`CRASHED` label and the session is NOT recovered. Tests patch the classifier to raise / time out / return garbage and assert no recovery fires and the loop continues.
- [ ] `RETRYING`/`API_ERROR`/`WAITING`/`DIALOG` verdicts → NOT recovered (only `CRASHED` recovers). Explicit test per label.
- [ ] `classify_session_stall` keeps its swallow-all → `healthy/unclassifiable` contract; unchanged.

### Empty/Invalid Input Handling
- [ ] **Non-granite session** (determined by `session_type`, NOT by a `None` timestamp — BLOCKER): the mid-run leg never applies; both PTY fields stay `None`; behavior reverts to current. Test pins this.
- [ ] **Granite session, both PTY fields `None`** (writer never initialized — the granite-state-3 case the BLOCKER flagged): recognized as granite-with-no-PTY-signal-yet via `session_type`; the mid-run leg **abstains this pass** (no recovery) and re-evaluates next tick — it is NOT silently excluded forever. Test pins this distinct from the non-granite case.
- [ ] **Granite session, `last_pty_read_loop_at` stale relative to `last_heartbeat_at`** (writer died/throttled — granite state 3): PTY signal untrusted; mid-run leg abstains (no recovery), falls back to existing process-alive behavior. Test pins that a stale loop-marker never produces a kill even with a stale `last_pty_activity_at`.
- [ ] `started_at=None AND created_at=None` (legacy/phantom) preserves the fast-path. Keep the existing test.
- [ ] Negative `running_seconds` (clock skew) preserves the fast-path. Keep the existing guard + test.

### Error State Rendering
- [ ] No user-visible output; observable via the `[session-health] Recovering session ...` log and project-scoped Redis counters (new: `tier1_falloff:never_started_grace_exceeded`, `mid_run_pty_quiescent_recovery`, `tier2_classifier_fallback`, per-label `mid_run_classifier_*`) plus the `dashboard.json` classifier-fallback rate + threshold alert (concern #6). Asserted in tests.

## Test Impact
- [ ] `tests/integration/test_agent_session_health_monitor.py` — UPDATE (note: integration, not unit): add never-started recovery cases (recover at the never-started grace, not 1800s) and the mid-run PTY-quiescence recovery case.
- [ ] `tests/unit/test_session_health_inference_removed.py` — UPDATE: add never-started-grace assertions following its sub-check-B pattern; re-verify no assumption of the old 1800s boundary for the never-started case.
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: extend to assert (a) never-started + fresh heartbeat recovers on the new grace, (b) a session that produced a turn is not recovered by the never-started leg.
- [ ] `tests/unit/test_session_stall_classifier.py` — UPDATE: pin `NEVER_STARTED_GRACE_SECS` and the new `NEVER_STARTED_CONFIRM_MARGIN_SECS` relationship.
- [ ] `tests/unit/test_session_health_compacting_reprieve.py` — UPDATE/VERIFY: the D3 reprieve-bypass for never-started must not break the existing `compacting`/`children`/`alive` reprieve behavior for output-producing sessions.
- [ ] `tests/integration/test_stall_advisory_e2e.py` — VERIFY (no change expected): the `sys.modules` import-direction guard still holds.
- [ ] NEW `tests/integration/` case — mid-run wedge: `last_pty_read_loop_at` fresh + `last_pty_activity_at` stale > window across K ticks + process alive + `CRASHED` → recovered; inverse: `last_pty_activity_at` fresh (screen repainting) + transcript frozen 25+ min → NOT recovered.
- [ ] NEW `tests/integration/` case — BLOCKER three-state: (a) non-granite → leg never applies; (b) granite both fields `None` → abstain, not excluded; (c) granite `last_pty_read_loop_at` stale vs heartbeat → abstain (no kill on stale-but-present `last_pty_activity_at`).
- [ ] NEW `tests/integration/` case — concern #5 CAS race: snapshot taken at suspect time; `byte_offset`/`last_pty_activity_at` advances during the classifier window → recovery aborts even though `status` is still `running`.
- [ ] NEW `tests/integration/` case — concern #3: a suspect with quiescent PTY is flagged even when `byte_offset` advanced once (corroborator, not gate).
- [ ] NEW unit/integration case — concern #6: persistent classifier unavailability increments `tier2_classifier_fallback` and the dashboard fallback rate crosses the alert threshold.

No tests DELETEd.

## Rabbit Holes
- **Lowering the global 300s race guard.** D1 relaxes it for `never_started` ONLY. Do not touch the floor for sessions that have produced output — it protects genuinely-fresh sessions loop-wide.
- **Using transcript staleness as the mid-run trigger.** Explicitly rejected (false-kills long subagents). The mid-run trigger is PTY quiescence; transcript-not-advanced is a corroborating condition, never the sole one.
- **Regex/glyph-parsing the TUI screen to decide crashed-vs-waiting.** Brittle across ANSI noise and Claude Code version changes — this is exactly what the stage-2 classifier replaces. For stage 1 (liveness), byte-activity is sufficient and deterministic; do not parse exact counter values.
- **Polling the granite classifier on every session every tick.** Loads the shared local model continuously and degrades under the load that correlates with hangs. The classifier runs ONLY on stage-1 suspects (D2/D4).
- **Giving `stall_advisory` kill powers.** Breaks the classifier's zero-writes / no-session_health-import contract; reintroduces competing recoverers. Rejected.
- **Storing `sdk_ever_output`.** It stays derived from `last_tool_use_at`/`last_turn_at`.
- **Discovering subagent transcript files as the liveness signal.** A plausible alternative to PTY-activity (a Task subagent writes its own growing JSONL), but it requires mapping parent→child transcripts and handling nested/Bash tools that have no transcript. PTY-activity is uniform across all tool types. Note as the fallback if PTY-activity proves unreliable in build; do not build both.

## Risks

### Risk 1: False-kill of a long, actively-working subagent/turn (the #1 regression)
**Impact:** A 25+ min subagent is recovered mid-flight — wasted work, looks flaky, repeats the old `dev_hang` mistake.
**Mitigation:** The mid-run trigger requires `last_pty_activity_at` quiescent for the full window. A working turn repaints continuously, so its activity timestamp stays fresh. Regression test pins "fresh PTY activity + frozen transcript 25 min → NOT recovered."

### Risk 2: The repaint assumption is unproven — does a long `Task` subagent actually keep the PARENT PTY repainting? (BLOCKING SPIKE — resolves concern #1)
**Impact:** The entire mid-run leg rests on the premise that a >5-min `Task` subagent keeps the parent TUI repainting its spinner/counter (so `last_pty_activity_at` stays fresh and the long-subagent case never becomes a suspect). If, instead, the parent screen goes quiescent while a subagent runs in a child context, then a healthy long subagent looks identical to a crash — and the leg would false-kill exactly the case it most needs to protect, repeating the old `dev_hang` mistake. This premise has NOT been empirically verified.
**Mitigation — make it a build gate, not just a test:** Step 3a (build-mid-run-gate) is **gated** on a real-PTY spike that MUST pass before the stage-1 gate is wired into recovery: drive a real parent PTY pair through a `Task` subagent that runs **> 5 minutes** and assert `last_pty_activity_at` stays fresh (screen keeps repainting) for the whole subagent duration, AND that it goes quiescent within seconds when the child is killed. **If the parent screen is quiescent during a healthy subagent, the PTY-activity premise is false → STOP and switch path B to the subagent-transcript fallback** (Rabbit Holes: discover the child JSONL and use *its* growth as the liveness signal) before proceeding. The result of this spike is recorded in `## Spike Results`. The corresponding regression test ("fresh PTY + 25 min frozen transcript → NOT recovered") is the permanent guard once the spike confirms the premise.

### Risk 3: D3 reprieve-bypass kills a session about to emit its first turn
**Impact:** A slow-but-genuinely-priming session is recovered just before its first turn lands.
**Mitigation:** The never-started grace + confirmation margin gives priming headroom; the moment a turn lands, `sdk_ever_output` flips True and the never-started legs no longer apply. Regression test: a session that emits a turn within the grace+margin is not recovered.

### Risk 4: Constant/grace drift reintroduced later
**Mitigation:** Single shared `NEVER_STARTED_GRACE_SECS`; unit test pins the detection/action relationship so a divergent edit fails CI.

### Risk 5: False-kill of a session waiting out a flaky Claude API connection (resilience #1)
**Impact:** A transient API/network hiccup freezes the screen; a naive frozen-screen kill recovers healthy work that would have self-healed on reconnect.
**Mitigation (D4):** the stage-2 classifier explicitly distinguishes `RETRYING`/`API_ERROR` from `CRASHED` and only `CRASHED` recovers; the K-consecutive-tick confirmation outlasts typical retry backoffs; on any classifier doubt the default is non-`CRASHED` (no recovery). Test drives a `RETRYING` buffer and asserts no recovery.

### Risk 6: Local classifier unavailable / slow / degraded under load
**Impact:** ollama down or thrashing (often correlated with the very incidents we care about) could either block the health loop or make wrong calls.
**Mitigation (D4):** stage-2 is best-effort with a hard timeout, never raises into the loop, and **fails toward "leave alone"** — a granite outage suppresses recoveries rather than causing them. The stage-1 cheap gate (which needs no inference) keeps running; suspects simply wait for a later tick when the classifier is reachable. Test patches the classifier to raise/time-out and asserts the loop continues and nothing is recovered.

## Race Conditions

### Race 1: Recovery vs. first turn arriving (path A)
**Mitigation:** `_has_progress` checks `sdk_ever_output` first; `_apply_recovery_transition` uses CAS (`expected_status="running"`). Worst case: one benign re-queue; the resumed session continues from its transcript.

### Race 2: Recovery vs. subagent finishing (path B) — stale-snapshot race the status CAS won't catch (resolves concern #5)
**Trigger:** between the stage-1 suspect decision and the post-classifier recovery write (the classifier call itself takes ~5-10s), the subagent returns — the screen repaints and `last_pty_activity_at`/`byte_offset` advance. The session is now alive, but the recovery write still fires because the only CAS is on `status="running"`, which is *still true* (a re-animated session is still `running`). A status-only CAS cannot see that the liveness evidence changed under it.
**Mitigation (concern #5):** **snapshot the liveness tuple `(last_pty_activity_at, byte_offset)` at stage-1 suspect time**, carry it through the (slow) classifier call, and make "these values are unchanged since the snapshot" a **CAS precondition** of the recovery transition, in addition to the `status="running"` CAS. If either value advanced during the classifier window, the session repainted = it is alive → abort the recovery (no kill), the stage-1 suspect status is cleared, and the K-counter resets. So the recovery write requires: status still `running` AND `last_pty_activity_at` still quiescent AND `byte_offset` still at its snapshot value. A turn landing flips any of these; worst case is a no-op pass, never a kill of re-animated work. (Note: `byte_offset` is a *required* member of the CAS precondition tuple here even though it is only an *optional corroborator* of the stage-1 gate per concern #3 — its role at gate-time, "raise confidence," and its role at CAS-time, "abort if it moved," are different and both correct.)

### Race 3: Advisory and actor on the same session
**Mitigation:** advisory is read-only; only `session_health` writes. No double-action.

## No-Gos (Out of Scope)
Not changing: the global 300s race guard for output-producing sessions, the 1800s output-then-idle path for the non-granite case, the advisory's 120s detection cadence, the derived nature of `sdk_ever_output`. Not building the subagent-transcript discovery path unless PTY-activity proves unreliable.

## Update System
No update-system changes required — purely internal worker/container logic. New env knobs (`NEVER_STARTED_CONFIRM_MARGIN_SECS`, `MID_RUN_QUIESCENCE_SECS`) have safe defaults and need no propagation; document them in `.env.example` only if we want operator-tunability. The change ships with the next `/update` pull-and-restart (`./scripts/valor-service.sh restart` to cycle worker + the granite container code).

## Agent Integration
No agent integration required — worker/container-internal recovery logic. No new CLI entry point, MCP tool, or bridge import. Observable via the `[session-health] Recovering session ...` log, project-scoped Redis counters (`tier1_falloff:never_started_grace_exceeded`, `mid_run_pty_quiescent_recovery`, `tier2_classifier_fallback`, per-label `mid_run_classifier_*`), the new `dashboard.json` classifier-fallback rate + threshold alert (concern #6), and the unchanged stall-advisory alert. The dashboard surface change is a read-only addition to the existing `dashboard.json` health block — no new endpoint.

## Documentation
### Feature Documentation
- [ ] Update the canonical recovery doc (locate during build: `grep -rl "NO_OUTPUT_BUDGET\|_has_progress\|_tier2_reprieve" docs/`; likely `docs/features/session-lifecycle.md` and/or `docs/features/granite-pty-production.md`) to describe both recovery legs, the shared `_never_started_past_grace` predicate, and the two PTY signals (`last_pty_read_loop_at` loop-alive + `last_pty_activity_at` screen-changed) with the three-state model.
- [ ] Document that the container's 12h `CYCLE_IDLE_TIMEOUT_S` is a ceiling and the session-health layer is the real hang detector (cross-link `ef53a88f`).

### Inline Documentation
- [ ] Update `_has_progress` and `_tier2_reprieve_signal` docstrings for the never-started legs.
- [ ] Comment the new shared grace usage and the `last_pty_activity_at` writer site.
- [ ] **Grain-of-salt comment on every new numeric constant.** Each of `MID_RUN_QUIESCENCE_SECS`, the `K` confirmation count, the classifier timeout, `NEVER_STARTED_CONFIRM_MARGIN_SECS`, and the classifier-fallback-rate alert threshold (concern #6) carries an inline comment stating it is a provisional, safety-chosen starting value — tune via env / adjust / refactor (e.g. derive from a neighbor) freely; no structural change should be needed to change it. Keep all of them as named, env-overridable constants, never inline literals.

## Success Criteria
- [ ] **End-to-end acceptance (matches the reported incident, NIT):** a session matching the 2026-06-18 signature — `status=running`, `last_turn_at=None`, `sdk_ever_output=False`, `last_heartbeat_at` fresh, `running_seconds ≈ 247s` (as in `tg_valor_-1003449100931_993`) — is recovered by the session-health actor (`running -> pending`, reason "no progress signal") within `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` of start, with NO manual `valor-service.sh restart` required. Asserted in `tests/integration/test_agent_session_health_monitor.py`.
- [ ] Priming wedge: `running` + `last_turn_at=None` + fresh heartbeats is recovered on the never-started grace (reconciled to 120s + margin via D1), NOT after ~30-100 min — closing both sub-check B and the reprieve path via the shared `_never_started_past_grace` predicate (both the 1708 actor floor and the 818 sub-check-B floor).
- [ ] Mid-run wedge: a session with frozen PTY screen (quiescent > window across K ticks) + non-advancing transcript + alive process + stage-2 `CRASHED` verdict is recovered well below the 12h ceiling.
- [ ] No false-kill: a long subagent whose screen keeps repainting (fresh `last_pty_activity_at`) is NOT recovered even with a 25+ min frozen transcript — regression test green.
- [ ] No false-kill: a session that emits its first turn within the never-started grace+margin is NOT recovered.
- [ ] Resilience: a frozen-screen session classified `WAITING`/`DIALOG`/`RETRYING`, or for which the classifier times out / errors / is unavailable / low-confidence, is NOT recovered — and a granite outage suppresses recoveries without crashing the loop. Tests green for each case.
- [ ] Resilience: a single flaky quiescent reading does not trigger recovery — the gate must hold across K consecutive ticks.
- [ ] Detection and action graces derive from a single shared constant; a drift test fails CI.
- [ ] `last_pty_activity_at` advances during real PTY work and freezes on a killed child — integration test green.
- [ ] Advisory stays read-only; only `session_health` writes (import-guard test green).
- [ ] New Redis counters distinguish the two recovery legs on dashboards.
- [ ] Tests pass (`/do-test`); docs updated (`/do-docs`); ruff clean.

## Team Orchestration
The lead agent orchestrates via Task tools and NEVER builds directly.

### Team Members
- **recovery-builder** (debugging-specialist) — path-A never-started leg, reprieve bypass (D3), shared grace, counters. Resume: true.
- **pty-signal-builder** (agent-architect) — run spike-1; persist BOTH `last_pty_read_loop_at` and `last_pty_activity_at`; build the stage-1 cheap quiescence gate (with loop-marker-vs-heartbeat cross-check + snapshot) and the stage-2 triggered granite classifier (fail-safe, CAS precondition, fallback counter). Resume: true.
- **regression-tester** (test-engineer) — both wedge recover/do-not-recover suites + drift pin + PTY-activity integration test. Resume: true.
- **recovery-validator** (validator) — verify long-subagent not killed, slow-priming not killed, import direction, advisory read-only. Resume: true.
- **docs-writer** (documentarian) — recovery feature doc + docstrings.

## Step by Step Tasks

### 1. Shared never-started grace + confirmation margin + shared predicate
- **Task ID**: build-shared-grace
- **Depends On**: none
- **Validates**: tests/unit/test_session_stall_classifier.py
- **Assigned To**: recovery-builder · debugging-specialist · Parallel: false
- Add `NEVER_STARTED_CONFIRM_MARGIN_SECS` next to `NEVER_STARTED_GRACE_SECS` in `session_stall_classifier.py`; `session_health` imports both. Add the single shared predicate `_never_started_past_grace(entry, now) -> bool` in `session_health` (concern #4) with one canonical `sdk_ever_output` definition, to be consulted by BOTH the actor floor and sub-check B in task 2. Document the detection-vs-action relationship.

### 2. Path-A never-started recovery (sub-check B leg + reprieve bypass + floor relaxation)
- **Task ID**: build-never-started
- **Depends On**: build-shared-grace
- **Validates**: tests/integration/test_agent_session_health_monitor.py, tests/unit/test_session_health_inference_removed.py, tests/unit/test_session_health_compacting_reprieve.py
- **Informed By**: D1 (relax 300s floor for no-turn case only), D3 (bypass children/alive reprieve for never-started)
- **Assigned To**: recovery-builder · debugging-specialist · Parallel: false
- Wire the shared `_never_started_past_grace` predicate into BOTH gates (BLOCKER + concern #4), hosting the actor branch in the **30s** loop (D0): (a) **add a NEW dedicated never-started recovery check to `_agent_session_tool_timeout_check` (`agent/session_health.py:2190`, the 30s `_agent_session_tool_timeout_loop` body)** — for each `running` session it already iterates, when `_never_started_past_grace(fresh, now)` holds, call `_apply_recovery_transition(fresh, reason="no progress signal observed (never_started past grace)", reason_kind="no_progress", handle=handle, worker_key=fresh.worker_key)`, reusing the loop's existing fresh-re-read race mitigation (`:2233-2254`) and terminal-status guard. The reason MUST contain the literal substring `"no progress signal"`. Do NOT relocate this into the 300s main loop's `:1708` elif — at 300s cadence it is neither sampled below 300s nor (with the 300s floor) entered, so the ~125s goal and Success Criterion are unreachable there (the cadence BLOCKER). Leave the main loop's `:1708` elif and the global 300s floor exactly as-is (a slower output-producing backstop; never satisfied by `_never_started_past_grace`). (b) in sub-check B at `:811/818`, deny the sub-300s heartbeat fast-path when the predicate is true (so every `_has_progress` caller — the 30s loop, the 300s loop — agrees). In `_tier2_reprieve_signal` (D3, concern #1): extend the existing `#1226` escalation guard at `:943` to `if not sdk_ever_output and (reprieve_count >= MAX_NO_OUTPUT_REPRIEVES or _never_started_past_grace(entry, now)): return None`, so the `return None` fires BEFORE the `compacting`/`children`/`alive` gates at `:950-974` and `children`/`alive` can never independently re-grant the wedge. This is the SOLE site deciding the bypass. Add `tier1_falloff:never_started_grace_exceeded` counter. Update docstrings.

### 3a. Path-B stage 1 — PTY-activity signal + cheap quiescence gate
- **Task ID**: build-mid-run-gate
- **Depends On**: build-shared-grace
- **Gate**: spike-1 (Risk 2 / Spike Results) MUST pass before the stage-1 gate is wired toward recovery. If the parent screen is quiescent during a healthy >5-min subagent, STOP and switch to the subagent-transcript fallback.
- **Validates**: tests/integration/test_agent_session_health_monitor.py
- **Informed By**: D2 stage 1, the BLOCKER (two-field design), concerns #1/#3/#5, pty_driver C5 quiescence
- **Assigned To**: pty-signal-builder · agent-architect · Parallel: true (independent of task 2; different code paths)
- First run spike-1 (real PTY pair, worktree). On pass: add BOTH `last_pty_read_loop_at` (stamped every read-loop iteration, unconditional — the loop-alive marker) and `last_pty_activity_at` (stamped on non-quiescent *normalized* screen bytes — strip known cursor/blink noise) to `AgentSession`, written from the container read loop (swallow+log on failure; never raise into the loop). Detect granite-ness via `session_type`/container attachment, never via the timestamps (BLOCKER). Add the stage-1 suspect gate **inside the 30s `_agent_session_tool_timeout_check` tick (D0)** — the per-suspect K-consecutive-tick counter advances on the 30s cadence (`K = 2` → 60s confirmation; this gate is unobservable at the 300s main-loop cadence, which is the cadence BLOCKER): granite + `sdk_ever_output=True` + tool/turn in flight + `last_pty_read_loop_at` fresh relative to `last_heartbeat_at` (else abstain — writer dead) + `last_pty_activity_at` quiescent > `MID_RUN_QUIESCENCE_SECS` held across K consecutive 30s ticks → mark a suspect AND snapshot `(last_pty_activity_at, byte_offset)` for the CAS precondition (concern #5). `byte_offset`-not-advanced is logged as a corroborator only, never a gating conjunct (concern #3). Stage 1 alone does NOT recover — it only marks a suspect.

### 3b. Path-B stage 2 — triggered classifier + recovery (D4 resilience)
- **Task ID**: build-mid-run-judge
- **Depends On**: build-mid-run-gate
- **Validates**: tests/integration/test_agent_session_health_monitor.py
- **Informed By**: D4 (fail-safe), flaky-API false-kill risk (Risk 5), classifier-unavailable (Risk 6)
- **Assigned To**: pty-signal-builder · agent-architect · Parallel: false
- For stage-1 suspects only (evaluated in the same 30s `_agent_session_tool_timeout_check` tick, D0; recovery routes through the loop's shared `_apply_recovery_transition`), call the local granite classifier on the frozen buffer with a hard timeout, low temperature, structured output, fixed label set `{CRASHED, WAITING, DIALOG, RETRYING}`. Recover ONLY on `CRASHED` **and** only if the snapshot tuple `(last_pty_activity_at, byte_offset)` from stage 1 is still unchanged (CAS precondition in addition to `status="running"`, concern #5) — if either advanced during the classifier window, abort (session re-animated), clear the suspect, reset the K-counter. Any other label, timeout, exception, unavailability, or low confidence → no recovery (default-safe) AND increment the dedicated `tier2_classifier_fallback` counter (concern #6). `"alive"` reprieve must not override `CRASHED`; a non-`CRASHED` verdict must not be overridden into a kill. Best-effort, never raises into the loop. Add `mid_run_pty_quiescent_recovery`, `mid_run_classifier_*` (per-label), and `tier2_classifier_fallback` counters; wire the fallback-rate + threshold alert into `dashboard.json` (concern #6).

### 4. Regression + integration tests
- **Task ID**: build-tests
- **Depends On**: build-never-started, build-mid-run-gate, build-mid-run-judge
- **Validates**: all UPDATEd test files + the new mid-run integration cases
- **Assigned To**: regression-tester · test-engineer · Parallel: false
- Never-started recover at grace (not 1800s); turn-producing + slow-priming-about-to-turn NOT recovered. Stage 1: fresh PTY + 25min frozen transcript → never a suspect (NOT recovered); single flaky quiescent read → not enough (K-of-N). Stage 2: suspect + `CRASHED` → recover; suspect + `WAITING`/`DIALOG`/`RETRYING` → NOT recovered; classifier timeout/raise/garbage/unavailable → NOT recovered and loop continues. `last_pty_activity_at` advances on work / freezes on killed child. Legacy + clock-skew preserve fast-path. Counter-failure does not block recovery. Drift pin.

### 5. Validate safety + import direction
- **Task ID**: validate-recovery
- **Depends On**: build-tests
- **Assigned To**: recovery-validator · validator · Parallel: false
- Confirm both false-kill cases safe; `test_stall_advisory_e2e.py` import guard green; import direction via grep; run targeted tests.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-recovery
- **Assigned To**: docs-writer · documentarian · Parallel: false
- Update recovery feature doc(s) + docstrings + constant/field comments + docs index entry.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recovery-validator · validator · Parallel: false
- Full targeted test set + ruff; verify every Success Criterion incl. docs and import-direction grep. Final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Stall classifier tests | `pytest tests/unit/test_session_stall_classifier.py -q` | exit 0 |
| Health monitor (integration) | `pytest tests/integration/test_agent_session_health_monitor.py -q` | exit 0 |
| Inference-removed unit | `pytest tests/unit/test_session_health_inference_removed.py -q` | exit 0 |
| Reprieve unit | `pytest tests/unit/test_session_health_compacting_reprieve.py -q` | exit 0 |
| Heartbeat-progress integration | `pytest tests/integration/test_session_heartbeat_progress.py -q` | exit 0 |
| Advisory import guard | `pytest tests/integration/test_stall_advisory_e2e.py -q` | exit 0 |
| Import direction (no reverse import) | `grep -n "import session_health\|from agent.session_health" agent/session_stall_classifier.py` | exit 1 (no match) |
| Shared grace imported | `grep -n "NEVER_STARTED_GRACE_SECS\|NEVER_STARTED_CONFIRM_MARGIN_SECS" agent/session_health.py` | match |
| Both PTY fields present | `grep -n "last_pty_activity_at\|last_pty_read_loop_at" models/agent_session.py` | both match |
| PTY writer wires both | `grep -n "last_pty_activity_at\|last_pty_read_loop_at" agent/granite_container/bridge_adapter.py agent/granite_container/pty_driver.py` | match |
| Shared never-started predicate | `grep -n "_never_started_past_grace" agent/session_health.py` | ≥3 matches (def + new 30s-loop branch + sub-check B + reprieve guard) |
| Never-started branch hosted in 30s loop (cadence BLOCKER) | `awk '/async def _agent_session_tool_timeout_check/,/async def _agent_session_tool_timeout_loop/' agent/session_health.py \| grep -c "_never_started_past_grace"` | ≥1 (the never-started recovery check lives inside the 30s tool-timeout tick, NOT only the 300s loop) |
| Dedicated branch reason string | `grep -n "no progress signal observed" agent/session_health.py` | match (so `_reason_kind` resolves to no_progress) |
| Grace defined once (no drift) | `grep -rn "NEVER_STARTED_GRACE_SECS *[:=]" agent/` | exactly one assignment, in `session_stall_classifier.py` |
| Classifier-fallback counter | `grep -n "tier2_classifier_fallback" agent/session_health.py` | match |
| Dashboard fallback-rate surface | `grep -rn "classifier_fallback\|fallback_rate" ui/` | match |
| Lint clean | `python -m ruff check agent/session_health.py agent/session_stall_classifier.py agent/granite_container/bridge_adapter.py` | exit 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/session_stall_classifier.py agent/granite_container/bridge_adapter.py` | exit 0 |

## Critique Results

**Re-critique verdict (3rd pass, 2026-06-18): NEEDS REVISION** (1 source-verified blocker). Resolved in this third revision pass:

| Severity | Finding | Disposition | Addressed By |
|----------|---------|-------------|--------------|
| BLOCKER (cadence) | Loop-cadence placement bug: the new never-started branch and the mid-run stage-1 gate were placed in `_agent_session_health_check` (`:1532`), invoked only by `_agent_session_health_loop` whose sleep is `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300s` (`:206`/`:2187`). A predicate true at ~125s but visited every 300s recovers in ~125-425s (headline goal + first Success Criterion provably false on a bad phase), and the mid-run 180s/K-tick gate is structurally impossible to observe with a 300s sampler. | FIXED | **D0 added:** BOTH path-A and path-B branches are hosted in the existing **30s** `_agent_session_tool_timeout_loop` (`:2298`, `TOOL_TIMEOUT_LOOP_INTERVAL = 30`, `:303`) via its body `_agent_session_tool_timeout_check` (`:2190`) — which already scans all `running` sessions each 30s and routes through the shared `_apply_recovery_transition`. K/180s reconciled to 30s ticks: `K = 2 → K * 30s = 60s` confirmation over the 180s quiescence floor; path-A fires at grace+margin rounded to the next 30s tick (~125-155s). The 300s main loop and its `:1708` elif are untouched (slower output-producing backstop). 300s floor not loosened; reason string still contains `"no progress signal"` → `_reason_kind = no_progress`. See D0, D1 Gate (1), Key Element 2/3, Data Flow, tasks 2/3a/3b, Verification (new "hosted in 30s loop" row), Mid-run window. |

---

**Re-critique verdict (2nd pass, 2026-06-18): NEEDS REVISION** (1 blocker + 3 specificity concerns). All resolved in the second revision pass:

| Severity | Finding | Disposition | Addressed By |
|----------|---------|-------------|--------------|
| BLOCKER | Actor's 300s floor makes prompt never_started recovery unreachable: the only recovery `elif` (`:1708`) is hard-gated by `running_seconds > 300s` *before* it calls `_has_progress`/any predicate, so consulting `_never_started_past_grace` inside it is dead code below 300s and "recover at ~120s+" is unreachable. | FIXED | New **dedicated `elif _never_started_past_grace(entry, now):` branch inserted BEFORE the `:1708` elif** (between `:1704` and `:1708`), setting `should_recover = True` and a reason containing `"no progress signal"` so `_reason_kind = "no_progress"`. Global 300s floor on the existing elif left untouched (output-producing sessions keep it). See D1, Key Element 2, task 2, Verification. |
| Specificity #1 | D3 reprieve-cap bypass: who clears/ignores `MAX_NO_OUTPUT_REPRIEVES` for never_started, and where in the evaluation order, so it can't independently re-grant the wedge. | FIXED | Owner = `_tier2_reprieve_signal` only; ordering = extend the `#1226` escalation guard at `:943` to `if not sdk_ever_output and (reprieve_count >= MAX_NO_OUTPUT_REPRIEVES or _never_started_past_grace(...)): return None`, which fires BEFORE the `compacting`/`children`/`alive` gates (`:950-974`). See D3, Key Element 2, task 2. |
| Specificity #2 | PTY null-vs-stale ambiguity: make explicit the three-state handling (writer-never-initialized `None` vs fresh vs stale) at the point of consumption, tied to `last_pty_read_loop_at`. | FIXED | Three-state decision table in Key Element 3, evaluated at the single consumption point, loop marker checked FIRST (None→abstain, stale-vs-heartbeat→abstain, fresh→consult screen marker). See Key Element 3, Architectural Impact, Empty/Invalid Input. |
| Specificity #3 | Third grace floor (`session_stall_classifier.py:52` `NEVER_STARTED_GRACE_SECS`) must derive from the SAME single source as actor/advisory so all three cannot drift. | FIXED | `:52` named as the single source of truth; three consumers (advisory `:206`/`:212`, actor via the one-way import, future callers) enumerated; drift-pin test asserts equality and that `session_health` declares no own grace. See Key Element 1, Risk 4, Freshness Check. |

---

**First-pass critique verdict: NEEDS REVISION** (1 blocker + 6 concerns + 1 nit). All resolved in the first revision pass (2026-06-18).

| Severity | Finding | Disposition | Addressed By |
|----------|---------|-------------|--------------|
| BLOCKER | `last_pty_activity_at` writer can go silently stale and `None` is overloaded across three states; granite-ness keyed off the timestamp excludes a no-writer granite session forever. | FIXED | Two persisted fields (`last_pty_read_loop_at` loop-alive + `last_pty_activity_at` screen-changed); granite-ness detected via `session_type`/container attachment; PTY freshness cross-checked vs `last_heartbeat_at`; three explicit granite states. See Architectural Impact, reliable-oracle, task 3a, Empty/Invalid Input. |
| Concern #1 | Mid-run leg rests on an unproven repaint assumption. | FIXED | spike-1 added as a **blocking gate** on Step 3a (Risk 2 + Spike Results): >5-min real-PTY Task-subagent test; if quiescent, switch to subagent-transcript fallback. |
| Concern #2 | One bug, two fixes of unequal complexity — consider shipping priming leg alone / deferring classifier. | REBUTTED + MITIGATED | Both legs kept in one issue but staged: path A lands first and alone fixes the reported incident; path B gated behind spike-1 and 3a→3b sequencing builds the cheap gate before any classifier kill. Splitting into two issues rejected (would orphan the mid-run 12h hole). See Appetite scope decision. |
| Concern #3 | `byte_offset`-not-advanced is a required conjunct with zero discriminating power. | FIXED | Demoted to optional corroborator (logged, never gates, never vetoes); sole gating condition is PTY quiescence across K ticks. Regression test pins flagging even if byte_offset advanced. See D2, Key Element 3, task 3a, Test Impact. |
| Concern #4 | D1 floor relaxation is dead code unless sub-check B is also intercepted (121s-300s hits `:825`/`:818` before the relaxed branch at `:1708`). | FIXED | One shared `_never_started_past_grace(entry, now)` predicate with one canonical `sdk_ever_output` def, consulted by BOTH the actor floor (`:1708`) and sub-check B (`:811/818`). See D1, Key Element 2, tasks 1 & 2, Verification. |
| Concern #5 | 5-10s classifier creates a stale-snapshot race the status-CAS won't catch. | FIXED | Snapshot `(last_pty_activity_at, byte_offset)` at stage-1 suspect time; require unchanged as a CAS precondition after the classifier returns. See Race 2, Key Element 3, tasks 3a/3b. |
| Concern #6 | Fail-silent-to-NON-CRASHED silently reverts to pre-fix "never recovers". | FIXED | Dedicated `tier2_classifier_fallback` counter + `dashboard.json` fallback-rate surface with threshold alert. See D4-observability, Agent Integration, Verification. |
| NIT | No end-to-end acceptance line for the priming leg matching the reported incident. | FIXED | Added Success Criteria E2E line matching `tg_valor_-1003449100931_993` (running, last_turn_at=None, sdk_ever_output=False, fresh heartbeat, ~247s → recovered within grace+margin, no manual restart). |

---

### All resolved (PM, 2026-06-18)
- **Mid-run oracle = cheap-gate-then-judge** (was Q-B): stage-1 PTY byte-diff gate (free, always-on) → stage-2 granite classifier on suspects only. Subagent-transcript discovery is the build-time fallback if PTY-activity proves unreliable; do not build both. Captured as D2.
- **Resilience over brittleness** (D4): bias to inaction, K-of-N confirmation, classifier fails toward "leave alone," explicit `RETRYING` handling for flaky Claude API connections.
- **Q-A — go with defaults:** `MID_RUN_QUIESCENCE_SECS = 180s`, `K = 2` consecutive ticks. Env-tunable.
- **Q-C — go with defaults:** relax the 300s race guard for the `never_started` case only (D1 as written).
- **Q-D — go with defaults:** reuse the existing local granite/ollama classifier path (no new model); per-call timeout **~5-10s** (suspect-only, so a short budget is fine).

> ⚠️ **All numeric constants in this plan are provisional — take them with a grain of salt.** `MID_RUN_QUIESCENCE_SECS (180)`, `K (2)`, the classifier timeout (5-10s), `NEVER_STARTED_GRACE_SECS`/`NEVER_STARTED_CONFIRM_MARGIN_SECS`, the classifier-fallback-rate alert threshold, and the tool-timeout tiers are starting points chosen for safety, not tuned values. They are expected to be adjusted from real-world behavior, made env-tunable, or refactored away (e.g. derived from one another) at any time. The build MUST (a) keep them as named, env-overridable constants — never inline literals — and (b) annotate each definition site with an inline comment saying so (see Inline Documentation). Tuning a constant later must require zero structural change.
