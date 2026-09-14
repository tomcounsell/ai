---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1881
last_comment_id: 4877533846
revision_applied: true
---

# Granite startup: deliver PM's terminal turn when PM completes before Dev primes

## Problem

A granite container coordinates two `claude` PTYs — the PM (routing / user-relationship layer) and the Dev (SDLC pipeline). During startup, the container primes the PM (with the user's message as context), then primes the Dev, then enters a startup loop that watches both PTYs. Delivery of the PM's first (prime-turn) reply is gated behind `startup_settled`, which only becomes true when `pm_saw_idle AND dev_saw_idle` are observed **in the same cycle** (anchor `if pm_saw_idle and dev_saw_idle:`, currently `container.py:~1977`).

For a fast PM-only request (status check, board update, Q&A), the PM primes, immediately dives into minutes of substantive subagent work, emits a clean `[/complete]` reply, and quiesces — all *before* the Dev PTY has finished (or even begun) priming. The two PTYs' idle observations never coincide in one cycle, so `startup_settled` never flips, the prime-turn relay (anchor comment `# Prime-turn relay (issue #1644)`, currently `container.py:~2009`, the only path that delivers the PM's `[/complete]`) never runs, and the container burns to `STARTUP_HARD_CEILING_S` (600s) and exits `startup_unresolved`.

**Current behavior:**
The user's request is actually fulfilled (e.g., a Notion card gets moved), the PM drafts a perfect confirmation ending in `[/complete]`, but the reply is silently dropped. The Telegram thread shows no Valor response, and the session records as a startup failure (`exit_reason=startup_unresolved`, `startup_failure_kind=ceiling`). Reproduced in production: session `c220a40996b74cad9da696fb18afc042` (thread `tg_cyndra_-1003900483201_172`, 2026-07-03).

**Desired outcome:**
- A PM turn that reaches `[/complete]` or `[/user]` is delivered regardless of whether the Dev PTY has finished priming. PM→user delivery must not depend on Dev reaching idle.
- Startup does not classify a session as `startup_unresolved` when the PM has already produced a routable terminal turn.
- When Dev priming *is* genuinely needed (PM routes `[/dev]`), the PM's idle/completion is latched so a later Dev-idle cycle still settles startup — the PM need not *still* be idle at the exact moment Dev settles.

## Freshness Check

**Baseline commit:** d9cb76b1 (`git rev-parse HEAD` at revision time; `git diff bc8ae4d5..d9cb76b1 -- container.py transcript_tailer.py` is empty, so the earlier bc8ae4d5 anchors still hold verbatim; original plan baseline was 06fca807)
**Issue filed at:** 2026-07-03T10:50:53Z
**Disposition:** Minor drift (line numbers re-anchored; claims unchanged)

**Anchor-text references re-verified** (line numbers drift under refactors — prefer the anchor text / symbol name; approximate line numbers are given against `bc8ae4d5` as a convenience only):
- Both-idle settle gate — anchor `if pm_saw_idle and dev_saw_idle:` (currently `container.py:~1977`, was cited as `:1958`). Body: `startup_settled = True; break`. Claim still holds.
- Prime-turn relay (issue #1644) — anchor comment `# Prime-turn relay (issue #1644)` (currently `container.py:~2009`, block runs to `~2080`; was cited as `:1990-2052`). Runs strictly after the settle break; delivers via `_route_pm_classification` (call site currently `~:2059`). Claim still holds.
- **Relay baseline snapshot (the BLOCKER site)** — `pm_prime_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0` (currently `container.py:~2019`), snapshotted AFTER the startup loop breaks and BEFORE `_cycle_turn`. Its intent (per `text_bearing_count` docstring, `transcript_tailer.py:~402`) is "the cheap baseline a caller captures BEFORE an idle read, so `last_assistant_text` can tell whether a genuinely new text-bearing entry flushed this cycle." **This intent is violated in the fast-PM/slow-Dev case:** PM's terminal `[/complete]` text flushed cycles earlier, so the post-loop baseline already counts it.
- **The freshness guard that drops the reply** — `last_assistant_text(prime_read_path, baseline_text_count=pm_prime_baseline)` (currently `container.py:~2042`) returns `""` when `len(texts) <= baseline_text_count` (`transcript_tailer.py:~452`). With the post-loop baseline already counting the terminal turn, this returns `""` → the relay classifies `unknown` → `PM_COMPLIANCE_NUDGE`, `should_break=False` (`_route_pm_classification`, currently `container.py:~2324`) → `user_facing_routed` stays `False`, exit is NOT `pm_complete`. This is the delivered-nothing failure the BLOCKER identified.
- Ceiling exit — sets `result.exit_reason = "startup_unresolved"` and `result.startup_failure_kind = "ceiling"` (currently `container.py:~2000-2005`; was cited as `:1976-1988`), before the relay. Claim still holds.
- `_startup_cycle_idle` — returns a 5-tuple `(saw_idle, edge_buffer, level_tail, idle_marker, elapsed_ms)`; edge-triggered per call. Claim still holds (locate by symbol name).
- `agent/granite_container/pty_driver.py` — `idle_marker` is a UI slice of the bypass/overlay bar, not a `[/complete]` marker (locate by symbol name). The fix cannot detect "PM completed" from the idle read alone.
- `_route_pm_classification` — `def _route_pm_classification` (currently `container.py:~2278`; was cited as `:2308-2337`). Its `complete` branch delivers non-empty payload via `on_complete_payload` and sets `user_facing_routed=True`. Claim still holds.

**Citation-drift note (critique fix):** The original plan's Freshness Check asserted these citations as "exact line" against `06fca807`; each was off by ~19 lines from the real code. This revision re-anchors every reference to its anchor text / symbol name and re-verifies against the current baseline `bc8ae4d5`. Prefer the anchors over the approximate line numbers, which will continue to drift.

**Cited sibling issues/PRs re-checked:**
- #1647 (PM never routes `[/complete]`), #1644 (PM's `[/dev]` never relayed), #1710 (loud startup diagnostics), #1842/#1848 (per-role transport hedge, headless Dev) — all distinct prior work; the prime-turn relay this bug depends on was introduced by #1644. #1882 (mean 👎 reaction) is the compounding policy bug, out of scope.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=<issue createdAt> -- agent/granite_container/container.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** None touching the granite startup handshake.

**Notes:** Reproduction against current `main` is via the code path, not a live PTY — the defect is structurally present in the startup loop as read. A production instance (`c220a40996b74cad9da696fb18afc042`) already demonstrates it end-to-end.

## Prior Art

- **#1644 (prime-turn relay)** — Introduced the prime-turn relay so PM can decide `[/user]`/`[/complete]`/`[/dev]` during its prime response rather than waiting for the first steady-state idle. Succeeded for its case, but placed the relay *after* the `startup_settled` gate — which is exactly what strands a fast-PM turn when Dev is slow. This bug is the unhandled ordering.
- **#1647** — PM never *routes* `[/complete]`. Distinct: here the PM did route a clean completion; the failure is in the settle/delivery gating, not classification.
- **#1710 (startup diagnostics / plateau detector)** — Added `startup_failure_kind` (`plateau`/`ceiling`) and diagnostic frames, and an early plateau bail. Relevant because the ceiling exit path this bug hits was hardened by #1710, but #1710 treats the startup "failure" as real; here it is spurious (the session succeeded).
- **#1842 / #1848 (per-role transport hedge, headless Dev)** — Added `_dev_is_headless()`; the startup loop already treats a headless Dev as `dev_idle = (True, ...)` so the both-idle gate reduces to PM alone (`container.py:1841-1842`). The fix must preserve this headless-Dev path byte-for-byte.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR for #1644 | Added the prime-turn relay so PM's terminal decision during prime is routed. | Gated the relay behind `startup_settled` (both PTYs idle same cycle). A fast PM that completes before Dev primes never satisfies the gate, so the relay it introduced never fires. Root cause was the *ordering assumption* (both PTYs settle together), not the relay logic itself. |

**Root cause pattern:** Delivery of an already-terminal PM turn is coupled to a *Dev-liveness* handshake condition. The PM's user-facing output should be an independent concern from whether Dev finished priming.

## Data Flow

1. **Entry point:** Bridge enqueues an `AgentSession`; worker runs it through `BridgeAdapter` → `Container.run()` (`agent/granite_container/container.py`).
2. **Prime PM:** `_prime_session(pm_pty, pm_prime_cmd, include_user_message=True)` (`container.py:1793`) — PM receives the user message and begins work.
3. **Prime Dev:** `_prime_session(dev_pty, DEV_PRIME_SLASH_CMD, ...)` (`container.py:1800`), skipped when Dev is headless.
4. **Startup loop** (anchor `while time.monotonic() < startup_deadline:`, currently `container.py:~1854` through the ceiling exit `~:2005`): each cycle reads `pm_idle = _startup_cycle_idle(pm)` then `dev_idle`, handles startup events, checks plateau, and at the settle gate (anchor `if pm_saw_idle and dev_saw_idle:`, currently `~:1977`) settles iff `pm_saw_idle and dev_saw_idle` in this cycle. Ceiling exit (anchor `result.startup_failure_kind = "ceiling"`, currently `~:2005`) if never settled.
5. **Prime-turn relay** (anchor comment `# Prime-turn relay (issue #1644)`, currently `container.py:~2009-2080`): reads PM's last assistant text from its JSONL transcript, `classify_pm_prefix`, then `_route_pm_classification`.
6. **Delivery** (`_route_pm_classification`, currently `container.py:~2278+`): `complete`/`user` destinations invoke `on_complete_payload`/`on_user_payload` (the Telegram send path) and set `user_facing_routed=True`. `dev` destination forwards to the Dev PTY (this is the ONLY branch that genuinely needs Dev).

The bug severs the flow between step 4 (never settles) and step 5 (never runs), so step 6 (delivery) never happens.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** None to public signatures. Internal startup-loop local state gains a latch flag (`pm_ever_idle`) and possibly an early terminal-settle branch. **The prime-turn relay changes in exactly one way:** its `pm_prime_baseline` snapshot (currently `container.py:~2019`) MOVES from *after* the startup loop to *before* PM emits any task-response output (before `_prime_session(pm)`, `~:1812`). Everything else in the relay is unchanged. `ContainerResult` may gain one observability field (e.g., `startup_settle_reason`) — additive, optional, defaulted.
- **Coupling:** *Decreases* coupling — PM→user delivery is decoupled from the Dev handshake.
- **Data ownership:** Unchanged.
- **Reversibility:** High. The change is localized to the startup loop; revert is a single-file revert.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the fast-path settle semantics don't regress the `[/dev]` handshake or headless-Dev path)
- Review rounds: 1 (async-timing correctness is the risk surface)

The coding is small (one function), but the correctness surface — reordering when startup settles and when the relay fires, without double-delivery or regressing four adjacent paths (headless Dev, `[/dev]` routing, plateau bail, ceiling diagnostics) — warrants a careful review round.

## Prerequisites

No prerequisites — this work modifies existing in-repo code with no external dependencies. The granite PTY harness runs under the existing test venv.

## Solution

### Key Elements

- **PM-idle latch:** A sticky `pm_ever_idle` flag in the startup loop. Once the PM is observed idle in any cycle, remember it. The settle condition becomes `pm_ever_idle AND dev_saw_idle` instead of requiring both in the same cycle. This fixes the *settle* for the reported production case (Dev *did* reach idle ~21s after the PM).
- **Relay baseline relocation (REQUIRED, not optional):** The latch fixes when the loop breaks but NOT what the relay delivers. In the reported case PM's `[/complete]` flushed cycles before the break, so the relay's post-loop `pm_prime_baseline` (`~:2019`) already counts it and `last_assistant_text` returns `""` → the PM reply is dropped and the exit is a compliance nudge, not `pm_complete` (the BLOCKER). **The fix is to snapshot `pm_prime_baseline` BEFORE PM emits any task-response output** — before `_prime_session(pm)` (`~:1812`), using the already-populated `result.pm_transcript_path` (set by `_capture_pty_identity`, `~:1798`). Then PM's terminal turn always counts as a NEW text-bearing entry relative to the baseline, so the relay re-derives and delivers it for ANY break reason (both-idle, latch, or fast settle). This is a genuine relay change; the plan's earlier "relay stays byte-for-byte unchanged" invariant was false and is retired.
- **Terminal-turn fast settle (decouple from Dev):** When the PM has gone idle *this cycle* during startup, read PM's transcript and classify it. If the PM produced a terminal user-facing turn (`[/complete]` or `[/user]`), settle startup **successfully and immediately** and fall through to the (relay-baseline-relocated) prime-turn relay to deliver it — without waiting for Dev at all. Only a `[/dev]` classification (or an unknown/empty PM turn that still needs the Dev) waits on the Dev handshake.
- **Preserve the Dev-needed path:** If the PM's terminal decision routes to Dev, keep the current behavior: latch PM idle and settle on the next Dev-idle cycle, so the `[/dev]` relay still hands PM's instruction to a primed Dev.
- **Preserve failure diagnostics:** The plateau bail (anchor `_plateau_count >= STARTUP_PLATEAU_CYCLES`, currently `container.py:~1948`) and the genuine ceiling exit (never-idle-PM, e.g. broken `--permission-mode`) must still fire with their existing `startup_failure_kind` and diagnostic frames. The fast-settle path must not mask a truly stuck PM.

### Flow

Container start → prime PM → prime Dev (or skip if headless) → **startup loop**:
- PM reaches idle with a `[/complete]`/`[/user]` terminal turn → **settle immediately, run prime-turn relay, deliver to user** → done (even if Dev is still priming).
- PM reaches idle but routes `[/dev]` → latch PM idle → wait for Dev idle → settle → relay hands `[/dev]` to Dev.
- PM never reaches idle (genuinely stuck) → plateau bail or ceiling → `startup_unresolved` (unchanged).

### Technical Approach

- **Relocate the relay baseline snapshot (the BLOCKER fix — do this in Task 2a).** Move `pm_prime_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0` from its current post-loop site (`container.py:~2019`) to BEFORE PM emits any task-response output — immediately before `_prime_session(self._pm_pty, ...)` (`~:1812`), reading `result.pm_transcript_path` (already populated by `_capture_pty_identity` at `~:1798`; tolerate `None` → 0, matching current behavior). Hoist the local so the relay's `last_assistant_text(prime_read_path, baseline_text_count=pm_prime_baseline)` call (`~:2042`) reads the pre-loop value. **Why this is required and sufficient:** with the baseline captured before PM's response, PM's terminal `[/complete]`/`[/user]` turn — flushed during the loop, whether cycles before the break (fast PM) or in the settle cycle (both-idle) — always satisfies `len(texts) > baseline_text_count`, so `last_assistant_text` returns it and the relay delivers. Delete the old `~:2019` assignment. This is the ONLY change to the relay block; the relay remains the single payload-delivery authority. **Retire the false invariant:** the relay is NOT byte-for-byte unchanged, and the plan no longer relies on it re-deriving the turn "identically for any break reason" from an unchanged post-loop baseline — that assumption is what stranded the reply.
- **Latch the PM idle bool.** In the startup loop, add `pm_ever_idle = pm_ever_idle or pm_saw_idle` each cycle. Change the settle test at the anchor `if pm_saw_idle and dev_saw_idle:` (currently `container.py:~1977`) to `if pm_ever_idle and dev_saw_idle:`. (Headless Dev already forces `dev_saw_idle=True`, so that path is unaffected; verify the byte-identical claim in review.) The latch alone settles the reported case; the relay-baseline relocation above is what makes it actually deliver.
- **Add a terminal-turn fast settle (read-only classification).** When `pm_saw_idle` is true **this cycle**, classify PM's prime transcript (reuse `text_bearing_count`/`last_assistant_text`/`classify_pm_prefix`, the exact primitives the relay uses, and the SAME relocated pre-loop `pm_prime_baseline`). This classification is **read-only**: on a positive terminal result (`complete` non-empty, or `user`) it does exactly one thing — set `startup_settled = True` and `break`. It MUST NOT snapshot, mutate, or forward any *additional* per-settle state (no second baseline, no payload, no `_prime_relayed`) into the relay beyond the single pre-loop baseline both paths already share. Gate the transcript read on the current cycle's `pm_saw_idle` (never the latch) so the read reflects a completed turn; do it at most once per idle observation (avoid a per-cycle transcript read while PM is still busy).
- **Single delivery site; relay is sole payload authority.** Do NOT deliver inside the loop. Set `startup_settled = True` and fall through to the prime-turn relay (anchor comment `# Prime-turn relay (issue #1644)`, currently `container.py:~2009`) as the single delivery site. With the baseline relocated once before the loop, the relay re-reads and re-classifies via `_route_pm_classification` (with its `user_facing_routed` / `should_break` bookkeeping) identically for a both-idle break, a latch break, or a fast-settle break. Because `_cycle_turn` / `_await_turn_end` is level-triggered (it re-detects PM's already-emitted Stop edge), the relay independently re-derives the terminal turn — the pre-loop baseline is the shared, sufficient guard. Fast-settle only *decides when to break*; the relay remains the sole authority on payload extraction and delivery.
- **Observability.** Add an optional `ContainerResult.startup_settle_reason` (`"both_idle"` | `"pm_terminal_fast"` | `"pm_latched_dev_idle"`), defaulted, appended to `startup_events`, so production can distinguish the new fast-path settles from the classic both-idle settle. This makes the fix's effect visible in `dashboard.json` / session records.
- **Keep the ceiling meaningful.** The genuine-stuck case (PM never idle) still reaches the ceiling; nothing about the fast-path weakens Risk 6's broken-`--permission-mode` detection, because that case never produces a PM idle at all.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The prime-turn relay already wraps `on_complete_payload`/`on_turn` in `try/except` with `logger.warning` (`container.py:2035-2039`, `2325-2333`). No new bare `except: pass` is introduced. Add a test asserting that when `on_complete_payload` raises during a fast-path settle, the container logs a warning and still exits cleanly (does not fall back to `startup_unresolved`).
- [ ] The transcript read in fast-settle detection must swallow read errors the same way the relay does (`_log_transcript_read_diagnostic` + `transcript_fallback_count`), never crashing the loop. Test: transcript missing/empty at fast-settle → falls through to the latched `pm_ever_idle AND dev_saw_idle` path rather than raising.

### Empty/Invalid Input Handling
- [ ] Empty `[/complete]` body: must NOT trigger the user-facing fast settle (mirrors `container.py:2334-2337` — empty complete is not user-facing). Test that an empty-body `[/complete]` during startup does not fast-settle-and-deliver; it falls through to the wrap-up guard path exactly as today.
- [ ] PM idle with `unknown`/no-marker text: must NOT fast-settle; falls through to the latched both-idle wait (needs Dev or a compliance nudge). Test: PM idle with non-terminal chatter + Dev never idle → still reaches ceiling (no spurious success).
- [ ] Whitespace-only PM transcript at fast-settle → treated as non-terminal, no fast settle.

### Error State Rendering
- [ ] The user-visible output is the delivered `[/complete]` payload. Test asserts `result.user_facing_routed is True` and `on_complete_payload` was called with the PM's payload on the fast path — i.e., the confirmation actually reaches the send path (the exact thing that was silently dropped).
- [ ] Test asserts that on the fast path `result.exit_reason == "pm_complete"` and NOT `startup_unresolved`, and `startup_failure_kind is None`.

## Test Impact

- [ ] `tests/unit/granite_container/test_container.py::TestContainerStartup::test_late_settle_proceeds_to_steady_state` (line 533) — UPDATE: this test already simulates a slow cold start settling at cycle 2 with a same-cycle both-idle. Keep it green (the classic both-idle settle must still work) and confirm neither the latch nor the relocated pre-loop `pm_prime_baseline` changes its outcome (the both-idle relay read must still deliver identically).
- [ ] `tests/unit/granite_container/test_container.py::test_never_idle_exits_startup_unresolved_at_ceiling` (line 497) — UPDATE/KEEP: PM never idle must still hit the ceiling. Verify the latch (`pm_ever_idle` stays False) preserves this exact behavior; add an assertion that `startup_settle_reason` is unset on this path.
- [ ] `tests/unit/granite_container/test_container.py::test_empty_complete_body_not_user_facing` (line 1395) — UPDATE/KEEP: ensure an empty `[/complete]` still does not become user-facing, now also exercised through the fast-settle branch.
- [ ] `tests/unit/granite_container/test_container.py::test_classify_complete_exits_loop` (line 79) — KEEP: steady-state complete classification unaffected.
- [ ] `tests/unit/test_granite_startup_diagnostic.py` (ceiling/plateau frame tests) — KEEP: the diagnostic frame and `startup_failure_kind` on genuine failures must be unchanged; add no regressions.
- [ ] NEW: `tests/unit/granite_container/test_container.py::test_pm_complete_before_dev_primes_delivers` — REPLACE/ADD: the core reproduction — PM idle+`[/complete]` at an early cycle (with `[/complete]` written to a REAL JSONL transcript fixture on disk) while Dev is NOT idle, Dev reaching idle only later (2a) or never (2b, gated); drives the genuine `last_assistant_text` baseline guard (NO stub); asserts delivery + `pm_complete` exit, not `startup_unresolved`.

## Rabbit Holes

- **Rewriting idle detection to be level-triggered.** Do not change `PTYDriver.read_until_idle` / `_startup_cycle_idle` semantics to make PM idle "stick" at the driver layer. The latch belongs in the startup loop's local state, not in the shared PTY driver (which many other paths depend on).
- **Making `idle_marker` carry the `[/complete]` marker.** `idle_marker` is a bypass-bar UI slice by design; do not repurpose it to detect terminal markers. Terminal detection goes through transcript classification, which already exists.
- **Reordering PM/Dev priming to prime Dev first or in parallel.** Tempting ("if Dev primed sooner the windows would overlap"), but it changes the whole startup contract, risks the `[/dev]` self-start guard (#1644/#1692), and doesn't fix the fundamental coupling. Out of scope.
- **A second delivery code path.** Delivering the payload inline in the loop instead of via the existing relay would duplicate `_route_pm_classification` bookkeeping and risk double-sends. Keep one delivery site.
- **Touching the #1882 reaction policy.** The 👎-on-`startup_unresolved` behavior is a separate issue; fixing delivery here removes most of its triggers, but the policy fix itself is out of scope.

## Risks

### Risk 1: Double delivery of the PM's reply
**Impact:** The user receives the confirmation twice (fast-settle delivers, then the relay or steady-state re-delivers).
**Mitigation:** Do not deliver in the loop. The fast path only *decides to break*; the single existing prime-turn relay (anchor comment `# Prime-turn relay (issue #1644)`, currently `container.py:~2009`) performs the one delivery. Reuse `self._prime_relayed` / `user_facing_routed` guards already present. Add a test asserting `on_complete_payload` is called exactly once.

### Risk 2: The relay re-reads and gets `unknown` because its baseline already counts PM's turn (the BLOCKER)
**Impact:** The loop breaks (via latch or fast settle) expecting delivery, but the relay's post-loop `pm_prime_baseline` (`~:2019`) already counts PM's `[/complete]` — which flushed cycles earlier — so `last_assistant_text(..., baseline_text_count=pm_prime_baseline)` returns `""` (`transcript_tailer.py:~452`, `len(texts) <= baseline_text_count`). The relay classifies `unknown` → `PM_COMPLIANCE_NUDGE`, `should_break=False`, `user_facing_routed` stays `False`. The session settles but delivers NOTHING and exits non-`pm_complete`. **This is the confirmed BLOCKER, and it fires on the latch path (2a) too — the latch alone fixes the settle but not the delivery.**
**Mitigation (relocate the baseline, once, before the loop):** Snapshot `pm_prime_baseline` BEFORE PM emits any task-response output — before `_prime_session(pm)` (`~:1812`), reading the already-populated `result.pm_transcript_path` — and delete the post-loop `~:2019` snapshot, hoisting the local into the relay. Then PM's terminal turn is always a NEW text-bearing entry relative to the baseline, so `last_assistant_text` returns it for a both-idle break, a latch break, or a fast-settle break alike. This is a relay change (the "byte-for-byte unchanged" invariant is retired), but it is the single, shared, sufficient guard: fast-settle introduces NO second baseline and forwards no payload/`_prime_relayed` state. Delivery is safe because `_cycle_turn` / `_await_turn_end` is level-triggered — it re-detects PM's already-emitted Stop edge and re-reads the flush-safe transcript. **Test (drives the REAL reader, no stub):** write a JSONL transcript fixture whose last text-bearing assistant entry is `[/complete]\nDone.`, seed the pre-loop baseline as the code would (count of text-bearing entries present before PM's response), break via latch AND via fast settle, and assert the real `last_assistant_text` + relay deliver `complete` and set `user_facing_routed=True` / `exit_reason="pm_complete"`. A stubbed `last_assistant_text` would bypass the exact `baseline_text_count` guard that fails and must NOT be used.

### Risk 3: Masking a genuinely stuck PM
**Impact:** A PM that paints an idle bar but never produces a real turn could be mistaken for "terminal" and skip the ceiling/plateau safety.
**Mitigation:** Fast-settle requires a *non-empty terminal classification* (`complete` with payload, or `user`), not mere idle. Mere idle only feeds the latch, which still requires Dev idle to settle. The never-idle-PM case is untouched (latch stays False → ceiling). Keep `test_never_idle_exits_startup_unresolved_at_ceiling` green.

### Risk 4: Regressing the headless-Dev path (#1842/#1848)
**Impact:** Headless Dev sessions could settle differently or double-fire.
**Mitigation:** Headless Dev already forces `dev_saw_idle=True`, so `pm_ever_idle AND dev_saw_idle` reduces to PM alone exactly as `pm_saw_idle AND dev_saw_idle` did once PM is idle. Verify byte-identical behavior with an explicit headless-Dev test.

## Race Conditions

### Race 1: PM idle window and Dev idle window never coincide (the bug itself)
**Location:** `agent/granite_container/container.py` startup loop (anchor `while time.monotonic() < startup_deadline:`, currently `~:1854`), specifically the settle gate (anchor `if pm_saw_idle and dev_saw_idle:`, currently `~:1977`).
**Trigger:** PM primes, does substantive work, emits `[/complete]`, and quiesces before the Dev PTY reaches idle (Dev primes late / slowly / never). Idle detection is edge-triggered per cycle, so PM's idle is observed in an early cycle and Dev's idle in a later cycle, never together.
**Data prerequisite:** PM's terminal turn must be present in PM's JSONL transcript before the relay reads it — it is, because PM has already emitted `[/complete]` and gone idle (that is precisely the observed idle).
**State prerequisite:** `startup_settled` must become true for the relay to run; today it requires simultaneous same-cycle idle.
**Mitigation:** Latch `pm_ever_idle` so PM idle need not recur; add a terminal-turn fast settle so a `[/complete]`/`[/user]` PM turn settles startup immediately without any Dev idle. This removes the same-cycle-overlap requirement entirely for user-facing PM turns.

### Race 2: Fast-settle transcript read races an in-flight PM turn
**Location:** New fast-settle classification read, in the startup loop body near the settle gate (`container.py:~1977`).
**Trigger:** The loop reads PM's transcript for a terminal marker while PM is mid-turn (transcript has partial text).
**Data prerequisite:** Only classify when `pm_saw_idle` is true **this cycle** (PM has quiesced) — never on the strength of the `pm_ever_idle` latch, which can be true while PM is mid-turn on a later cycle. This mirrors the relay's own precondition and keeps the read reflecting a completed turn.
**State prerequisite:** The single `pm_prime_baseline` is snapshotted once BEFORE the loop (before `_prime_session(pm)`); fast-settle reuses that same baseline, so `last_assistant_text` requires a genuinely new text-bearing entry and cannot be fooled by a partial transcript that predates PM's response.
**Mitigation:** Gate the classification read on the current cycle's `pm_saw_idle` (drop any "or the latch" allowance); reuse the shared pre-loop baseline (`text_bearing_count`). Do at most one classification per idle observation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1882] The `startup_unresolved` → 👎 reaction policy bug. Filed and tracked separately; fixing delivery here removes most of its triggers but the reaction-classification change belongs to #1882.
- Reordering or parallelizing PM/Dev priming — a structural change to the startup contract, not required to fix this bug, and risks the `[/dev]` self-start guard.
- Changing `PTYDriver.read_until_idle` / `_startup_cycle_idle` idle semantics at the driver layer — the latch is loop-local by design.

## Update System

No update system changes required — this is a purely internal change to `agent/granite_container/container.py` (and optionally one additive `ContainerResult` field). No new dependencies, config files, migrations, or `scripts/update/` changes. No Popoto model changes (`ContainerResult` is a plain dataclass, not a Popoto model).

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to the granite container's startup loop. The agent already reaches this code via `BridgeAdapter → Container.run()`; no new CLI entry point (`pyproject.toml [project.scripts]`), no MCP surface (`mcp_servers/` / `.mcp.json`), and no new bridge import. The fix's effect is exercised end-to-end by the existing granite integration tests and the new unit reproduction.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — add a "Startup settle conditions" note documenting the PM-idle latch and the terminal-turn fast settle (PM→user delivery decoupled from the Dev handshake), and the new `startup_settle_reason` values.
- [ ] Verify `docs/features/README.md` index still points correctly (no new file, so likely no index change; confirm).

### Inline Documentation
- [ ] Comment the settle gate change (anchor `if pm_ever_idle and dev_saw_idle:`, currently `container.py:~1977`) explaining why the latch + fast settle exist (reference #1881 and the fast-PM/slow-Dev race).
- [ ] Comment the relocated `pm_prime_baseline` snapshot (now before `_prime_session(pm)`, `~:1812`) explaining that it MUST precede PM's task-response output so the relay's `last_assistant_text` freshness guard delivers PM's already-flushed `[/complete]` for a latch/fast-settle break (reference #1881; note the moved-from `~:2019` site).
- [ ] Docstring/comment for the new `startup_settle_reason` field on `ContainerResult`.

## Success Criteria

- [ ] **SC#1a (satisfied by Task 2a — latch + relay-baseline relocation):** A granite startup where the PM emits a non-empty `[/complete]` and goes idle while the Dev PTY is NOT idle but reaches idle **on a later cycle** delivers the PM payload to `on_complete_payload` and exits `pm_complete` — NOT `startup_unresolved`. This is the reported production incident and MUST be met by 2a alone (the latch settles it and the relocated baseline delivers it).
- [ ] **SC#1b (gated on Task 2b — terminal-turn fast settle):** A granite startup where the PM emits a non-empty `[/complete]` and goes idle while the Dev PTY **never reaches idle** likewise delivers the payload and exits `pm_complete`. This "or never" branch is satisfiable ONLY by the fast settle (2b); if 2b is deferred, this criterion is explicitly deferred with it and the "never" case falls through to the ceiling (unchanged-from-today behavior). Definition-of-done for a 2a-only merge covers SC#1a; the full definition-of-done requires 2b for SC#1b.
- [ ] `result.user_facing_routed is True` and `on_complete_payload` is invoked exactly once on the delivering path (no double delivery).
- [ ] The classic same-cycle both-idle settle still works (`test_late_settle_proceeds_to_steady_state` green).
- [ ] A genuinely never-idle PM still exits `startup_unresolved` with `startup_failure_kind` set (`test_never_idle_exits_startup_unresolved_at_ceiling` green).
- [ ] Empty-body `[/complete]` during startup is still non-user-facing (`test_empty_complete_body_not_user_facing` green through the new path).
- [ ] Headless-Dev startup is byte-identical in behavior (explicit test).
- [ ] (Optional/deferrable — NIT) `startup_settle_reason` distinguishes `both_idle` / `pm_terminal_fast` / `pm_latched_dev_idle` in `ContainerResult`, if the observability field is included with Task 2b.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (startup-settle)**
  - Name: startup-settle-builder
  - Role: Implement the PM-idle latch + terminal-turn fast settle in the startup loop, plus the `startup_settle_reason` observability field.
  - Agent Type: builder
  - Domain: async/concurrency (edge-triggered idle detection, latch state, no double-delivery)
  - Resume: true

- **Validator (startup-settle)**
  - Name: startup-settle-validator
  - Role: Verify the reproduction test fails on `main` and passes on the branch; verify the four adjacent paths (both-idle settle, never-idle ceiling, empty-complete, headless-Dev) are unregressed; confirm single delivery.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: granite-startup-doc
  - Role: Update `docs/features/granite-pty-production.md` and inline comments.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Reproduce the race as a failing unit test
- **Task ID**: build-repro-test
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_container.py::test_pm_complete_before_dev_primes_delivers` (create)
- **Informed By**: Recon (edge-triggered idle; `_mock_pm`/`_mock_dev`/`_idle_result` harness in `tests/unit/granite_container/`)
- **Assigned To**: startup-settle-builder
- **Agent Type**: builder
- **Parallel**: false
- Write a unit test using the existing mock-driver harness: PM `read_until_idle` side-effect yields `_idle_result("[/complete]\nDone.", saw_idle=True)` on an early startup cycle, then non-idle/idle-with-no-new-text on later cycles; Dev `read_until_idle` yields `saw_idle=False` for the first two cycles then `saw_idle=True` (the reported-incident timing).
- **Drive the REAL transcript reader — do NOT stub `last_assistant_text`.** Write an actual JSONL transcript fixture at `result.pm_transcript_path` whose last text-bearing assistant entry is `[/complete]\nDone.` (flushed to disk *before* the loop breaks, matching the fast-PM ordering), plus the level-triggered Stop edge the relay's `_await_turn_end` re-detects. The test must exercise the genuine `text_bearing_count` / `last_assistant_text(baseline_text_count=...)` path so the `len(texts) <= baseline_text_count` guard is actually hit — a stub would go green while the bug persists (the guard is exactly where the reply is dropped).
- Assert that on **current `main`** this test FAILS: the post-loop baseline at `~:2019` counts the already-flushed `[/complete]`, `last_assistant_text` returns `""`, the relay classifies `unknown`, and the session exits `startup_unresolved` (never delivers). Capture the red-state output for the PR.

Task 2 is deliberately **split into two independently-mergeable diffs** (critique: Scope & Value). Diff 2a (the latch **plus the relay-baseline relocation**) fixes the *reported* production incident (Dev reached idle ~21s after the PM) end-to-end — settle AND delivery — and carries the lower correctness risk; it must stay shippable on its own. Diff 2b (the terminal-turn fast settle) additionally covers the unevidenced "Dev never primes" case and adds its own decision branch (Race 2 / Risk 2); it can be deferred to a follow-up merge without blocking 2a.

### 2a. Implement PM-idle latch + relay-baseline relocation (independently mergeable — fixes the reported incident end-to-end)
- **Task ID**: build-fix-latch
- **Depends On**: build-repro-test
- **Validates**: `test_late_settle_proceeds_to_steady_state`, `test_never_idle_exits_startup_unresolved_at_ceiling`, and the reported-incident variant of the new repro (PM idle early with `[/complete]` on disk, Dev idle ~later cycle → **delivers** + `pm_complete`)
- **Assigned To**: startup-settle-builder
- **Agent Type**: builder
- **Parallel**: false
- **Relay-baseline relocation (the BLOCKER fix).** Move `pm_prime_baseline = text_bearing_count(pm_transcript) if pm_transcript else 0` from the post-loop relay site (`container.py:~2019`) to BEFORE `_prime_session(self._pm_pty, ...)` (`~:1812`), reading `result.pm_transcript_path` (populated by `_capture_pty_identity` at `~:1798`; `None` → 0). Hoist the local into the enclosing scope so the relay's `last_assistant_text(prime_read_path, baseline_text_count=pm_prime_baseline)` (`~:2042`) reads the pre-loop value; delete the old `~:2019` assignment. Without this, the latch settles but the relay drops PM's already-flushed `[/complete]` (the BLOCKER).
- **Latch.** Add `pm_ever_idle = pm_ever_idle or pm_saw_idle` in the startup loop; change the settle gate (anchor `if pm_saw_idle and dev_saw_idle:`, currently `container.py:~1977`) to `if pm_ever_idle and dev_saw_idle:`.
- Verify the headless-Dev path is byte-identical: headless Dev already forces `dev_saw_idle=True`, so `pm_ever_idle and dev_saw_idle` reduces to PM alone exactly as before once PM is idle.
- Preserve plateau bail, ceiling exit, and headless-Dev handling unchanged. The relay's baseline snapshot MOVES (documented above) but no other relay logic changes; the relay remains the single delivery site.
- This diff is self-contained and mergeable without 2b. If 2b is deferred, the "Dev never primes" case still hits the ceiling (acceptable, unchanged-from-today behavior); SC#1b is deferred with 2b.

### 2b. Implement terminal-turn fast settle (independently mergeable — decouples PM→user from Dev; deferrable)
- **Task ID**: build-fix-fastsettle
- **Depends On**: build-fix-latch
- **Validates**: the "Dev never primes" variant of `test_pm_complete_before_dev_primes_delivers`, plus `test_empty_complete_body_not_user_facing`
- **Assigned To**: startup-settle-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the terminal-turn fast settle as a **read-only classification**: when `pm_saw_idle` is true **this cycle** (never on the strength of the latch), classify PM's transcript (reuse `text_bearing_count`/`last_assistant_text`/`classify_pm_prefix` against the SAME pre-loop `pm_prime_baseline` relocated in 2a); if `complete` (non-empty) or `user`, set `startup_settled = True` and `break` immediately. It MUST NOT snapshot a second baseline or forward payload/`_prime_relayed` into the relay.
- Fall through to the single prime-turn relay for delivery. The relay's ONLY change (the baseline relocation) landed in 2a; 2b adds no further relay change and introduces no baseline of its own.
- Guard the transcript read to at most once per idle observation, gated on the current cycle's `pm_saw_idle`.
- Preserve plateau bail, ceiling exit, and headless-Dev handling unchanged.
- **Optional (NIT, deferrable):** Add `ContainerResult.startup_settle_reason` (additive, defaulted: `"both_idle"` | `"pm_terminal_fast"` | `"pm_latched_dev_idle"`) and record it at each settle site + in `startup_events`. Not required by any Success Criterion — include only if a reviewer wants fast-path visibility before merge; otherwise defer to a follow-up.

### 3. Validate fix + adjacent paths
- **Task ID**: validate-fix
- **Depends On**: build-fix-latch, build-fix-fastsettle (validate after each diff if merged separately)
- **Assigned To**: startup-settle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new test (green) and confirm it was red on `main`. Confirm it drives the REAL `last_assistant_text` (JSONL fixture), not a stub.
- Run `tests/unit/granite_container/test_container.py` and `tests/unit/test_granite_startup_diagnostic.py` in full; confirm no regressions.
- Grep-confirm a single delivery site (no in-loop `on_complete_payload` call) AND that the `pm_prime_baseline` snapshot now appears BEFORE the startup-loop start (relocated), with no second baseline introduced by fast-settle. There must be exactly one `pm_prime_baseline =` assignment and it must precede `while time.monotonic() < startup_deadline`.
- Confirm `startup_settle_reason` values are set correctly per path (only if the optional field was included).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fix
- **Assigned To**: granite-startup-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` with the settle-condition changes and `startup_settle_reason` values.
- Add inline comments referencing #1881 at the settle gate.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: startup-settle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands below.
- Confirm every Success Criterion.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New reproduction test passes | `pytest tests/unit/granite_container/test_container.py -k pm_complete_before_dev_primes -q` | exit code 0 |
| Startup container tests pass | `pytest tests/unit/granite_container/test_container.py -q` | exit code 0 |
| Startup diagnostics unregressed | `pytest tests/unit/test_granite_startup_diagnostic.py -q` | exit code 0 |
| Single delivery site (no in-loop deliver) | `S=$(grep -n 'while time.monotonic() < startup_deadline' agent/granite_container/container.py \| head -1 \| cut -d: -f1); E=$(grep -n '# Prime-turn relay (issue #1644)' agent/granite_container/container.py \| head -1 \| cut -d: -f1); awk -v s=$S -v e=$E 'NR>=s && NR<e' agent/granite_container/container.py \| grep -c 'on_complete_payload'` | match count == 0 |
| Exactly one baseline snapshot | `grep -c 'pm_prime_baseline =' agent/granite_container/container.py` | output == 1 |
| Baseline relocated before the loop | `B=$(grep -n 'pm_prime_baseline =' agent/granite_container/container.py \| head -1 \| cut -d: -f1); L=$(grep -n 'while time.monotonic() < startup_deadline' agent/granite_container/container.py \| head -1 \| cut -d: -f1); [ "$B" -lt "$L" ] && echo ok` | prints `ok` (snapshot precedes loop start) |
| Latch present | `grep -c 'pm_ever_idle' agent/granite_container/container.py` | output > 0 |
| Settle-reason field present (only if optional NIT field included with Task 2b) | `grep -c 'startup_settle_reason' agent/granite_container/container.py` | output > 0 (skip if deferred) |
| Format clean | `python -m ruff format --check agent/granite_container/container.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). FULL depth (3 critics). Re-critique verdict: NEEDS REVISION (1 BLOCKER + 3 concerns + 1 nit). Revision pass applied 2026-07-03: BLOCKER resolved by relocating the relay baseline (retiring the false "relay unchanged" invariant); the 3 supporting concerns resolved; NIT deferrable. Prior-round Implementation Notes retained below. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER (re-critique) | Risk & Robustness | The invariant "leave the prime-turn relay byte-for-byte unchanged and trust it to re-derive the terminal turn identically for any break reason" is FALSE. Delivered text comes from `last_assistant_text(prime_read_path, baseline_text_count=pm_prime_baseline)` (`container.py:~2042`), which returns `""` when `len(texts) <= baseline_text_count` (`transcript_tailer.py:~452`). `pm_prime_baseline` is snapshotted at `~:2019` AFTER the loop breaks; in the fast-PM/slow-Dev case PM's `[/complete]` flushed cycles earlier so the baseline already counts it → `""` → relay classifies `unknown` → `PM_COMPLIANCE_NUDGE`, `should_break=False` → settles but delivers NOTHING, exit not `pm_complete`. | **RESOLVED (revision pass)** — relay baseline relocated to before the loop; false invariant retired | Move `pm_prime_baseline` from `~:2019` to before `_prime_session(pm)` (`~:1812`) using `result.pm_transcript_path` (populated at `~:1798`); hoist the local into the relay. PM's terminal turn then always counts as NEW relative to the pre-loop baseline, so the relay delivers for a both-idle, latch, OR fast-settle break. **Moved into Task 2a** (the latch alone did not deliver). Embedded in Solution/Key Elements, Technical Approach, Risk 2, Race 2, Architectural Impact, Task 2a, Verification. |
| CONCERN (re-critique) | Test rigor | Task 1's reproduction test stubbed `last_assistant_text`, bypassing the freshness guard — it would go green while the bug persists. | **RESOLVED (revision pass)** — test drives the REAL reader | Task 1 now writes an on-disk JSONL transcript fixture (last text-bearing entry `[/complete]\nDone.`) and exercises the genuine `text_bearing_count` / `last_assistant_text(baseline_text_count=...)` path so the `len(texts) <= baseline_text_count` guard is hit; stubbing is explicitly forbidden. Embedded in Task 1, Risk 2 test, Test Impact NEW entry. |
| CONCERN (re-critique) | Scope & Value | Success Criterion #1's "or never" branch is only satisfiable by Task 2b, but 2b is deferrable — shipping 2a alone left definition-of-done unmet. | **RESOLVED (revision pass)** — SC#1 split into SC#1a / SC#1b | SC#1a (Dev idle later) is met by 2a; SC#1b (Dev never idle) is gated on 2b and explicitly deferred with it. Definition-of-done for a 2a-only merge covers SC#1a. |
| CONCERN (re-critique) | Consistency | Technical Approach permitted a latch-triggered transcript read, contradicting Race 2's mandate to gate the read on the current cycle's `pm_saw_idle`. | **RESOLVED (revision pass)** — "(or the latch)" dropped | Fast-settle read is now gated strictly on the current cycle's `pm_saw_idle` in Technical Approach, Task 2b, and Race 2 (the latch only settles; it never triggers the transcript read). |
| CONCERN (prior round — partially SUPERSEDED) | Risk & Robustness (verified-down from BLOCKER) | Risk 2's mitigation wording ("fast-settle uses the same transcript path + baseline the relay uses") is in tension with the Technical Approach's "fast-settle only decides when to break; the relay remains the authority on payload extraction." A builder who wires fast-settle's classification into the relay's `pm_prime_baseline` could regress a fast-PM turn to `pm_hang`. | **RESOLVED, then partially SUPERSEDED by the re-critique BLOCKER** | The "fast-settle stays read-only; relay is sole payload authority" conclusion still holds. **SUPERSEDED:** this prior note claimed the relay's `pm_prime_baseline` at `~:2019` is left unchanged and "recomputed fresh identically for any break reason" — the re-critique proved that FALSE (the post-loop baseline already counts a fast PM's `[/complete]`, so `last_assistant_text` returns `""`). The relay baseline is now RELOCATED to before the loop (see the BLOCKER row above); the "unchanged relay / baseline at 2019" wording here is retired. |
| CONCERN | Scope & Value | Two mechanisms of different evidentiary weight ship together: the `pm_ever_idle` latch alone fixes the reported incident (Dev idle ~21s later); the terminal-turn fast settle additionally targets an unevidenced "Dev never primes" case and adds its own decision branch (Race 2 / Risk 2). Open Question #3 asks the same. | **RESOLVED (revision pass)** — Task 2 split into 2a/2b | Split Task 2 into two independently-mergeable diffs — Task 2a (`build-fix-latch`) first (settle test `pm_ever_idle and dev_saw_idle` at the gate; headless-Dev path byte-identical), Task 2b (`build-fix-fastsettle`) second — so the lower-risk fix stays shippable if the fast settle is deferred. |
| CONCERN | History & Consistency + Structural check | The Freshness Check asserted three file:line citations "still hold, exact line" against baseline `06fca807`, but each was off by ~19 lines: settle gate `if pm_saw_idle and dev_saw_idle:` at `:1977` (not `:1958`); prime-turn relay at `:2009-2071` (not `:1990-2052`); `_route_pm_classification` at `:2278+` (not `:2308-2337`). | **RESOLVED (revision pass)** — all citations re-anchored | Freshness Check re-verified against real baseline `bc8ae4d5`; every reference now leads with anchor text / symbol name (approximate line numbers marked "currently `~:NNNN`"). Technical Approach's "change the settle test at :1958" replaced with the anchor condition `if pm_saw_idle and dev_saw_idle:`. Verification awk command re-anchored to grep the loop-start and relay anchors (drift-proof). |
| NIT | Scope & Value | `ContainerResult.startup_settle_reason` is additive debugging scope not required by any Success Criterion; the plan itself calls it "strictly optional" (Open Question #2). | **DEFERRABLE (revision pass)** — moved into Task 2b as optional | Marked explicitly optional/deferrable in Task 2b: include only if a reviewer wants fast-path visibility before merge; otherwise defer to a follow-up. Not gating any Success Criterion. |

---

## Open Questions

1. **Fast-settle scope for `[/user]`.** The plan treats both `[/complete]` and `[/user]` as terminal fast-settle triggers (both are user-facing and Dev-independent). Confirm `[/user]` should also skip the Dev handshake during startup, or whether only `[/complete]` should fast-settle and `[/user]` should keep waiting for Dev. *(Still open — genuine design judgement; does not block the latch diff 2a, only the fast-settle diff 2b.)*
2. **`startup_settle_reason` field.** ~~Is the additive observability field wanted?~~ **Resolved by revision pass (NIT):** made explicitly optional/deferrable inside Task 2b — include only if a reviewer wants fast-path visibility before merge; otherwise defer to a follow-up. Not gating any Success Criterion.
3. **Latch vs. full decouple as the shippable minimum.** ~~Confirm both ship together or latch-only first.~~ **Resolved by revision pass (Scope & Value concern):** Task 2 is now split into two independently-mergeable diffs — 2a (latch, fixes the reported incident) and 2b (fast settle, covers "Dev never primes"). Both are planned to ship, but 2a stays shippable on its own if 2b is deferred.
