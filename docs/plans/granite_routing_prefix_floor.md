---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1719
last_comment_id:
---

# Granite Bridge Canned-Fallback Fix — Per-Turn Prefix Contract + Relaxed Wrap-Up Floor

## Problem

Every incoming Telegram message handled by the granite Eng session comes back as the canned string:

> "I wasn't able to produce a response to this — please rephrase or follow up."

(`OPERATOR_TERMINAL_MESSAGE`, `container.py:255`). The bridge is effectively unusable. Sessions run ~10 minutes (exhausting `max_turns=10` PM↔Dev cycles) and then deliver this fallback. Production worker logs since ~2026-06-15 17:14 UTC: **24× `exit_reason=pm_no_user_message`** + **6× `pm_hang`**, and **0× `pm_complete`/`pm_user`**.

**Desired outcome:** A real Telegram message round-trips a genuine, non-canned response. The canned fallback fires only on a genuinely empty turn (no assistant output at all).

## Freshness Check

*Baseline: `main` @ `c79262e8` (2026-06-19). Re-verified during the pre-plan validity review — see issue #1719 `## Recon Summary`.*

| Reference | Disposition | Notes |
|---|---|---|
| `container.py:1219` `_successful_exits = {pm_complete, pm_user, pm_max_turns}` | **Unchanged** | Still present; `pm_max_turns` still in the set. |
| `container.py:255` `OPERATOR_TERMINAL_MESSAGE`; `:1555-1559` direct delivery | **Minor drift** | Line numbers shifted from issue's `:231` to `:255`. Claim holds. |
| `#1694` (`971b77d6`) deleted `--append-system-prompt` persona path | **Unchanged** | Confirmed in container.py git log within the regression window. |
| Solution-sketch #2 ("add a Stop-hook floor") | **Major drift (reframed)** | A last-assistant-message floor **already partially exists**: `_run_wrapup_guard` (`container.py:1475`, introduced #1651/`d005aaa2`, *predates* the regression). It reads `last_assistant_text` and re-drives PM once, but re-gates through `classify_pm_prefix` + `user_facing_routed`, so a no-prefix wrap-up response still falls to canned. → relax the existing floor, do not build new. |
| `#1739` (deferred delivery fallback, shipped 2026-06-19) | **Dropped (orthogonal)** | Acts at the message-drafter/health-checker layer on session *failure*. Granite delivers canned with `user_facing_routed=True` and exits *cleanly*, so #1739's path never fires. Does not fix this. |

No active plan in `docs/plans/` overlaps this surface (the granite_lossless_checkpoint_resume plan touches resume, not routing). No closure of the bug — reproduced in code against current main.

## Prior Art

- **#1651 / `d005aaa2`** — introduced `_run_wrapup_guard` + `PM_WRAPUP_PROMPT` (the mandatory user-facing wrap-up). The floor this plan relaxes.
- **#1694 / `971b77d6`** — Persona-as-Priming refactor. Root-cause commit: deleted the per-turn `--append-system-prompt` contract injection, moving the prefix contract into one-shot `/prime-*` slash commands.
- **#1713 / #1708** — canned-fallback *diagnostic* + catchup/reconciler persona resolution. Added transcript-read diagnostics (`_log_transcript_read_diagnostic`) we reuse; did not address the routing gate.
- **#1732** — Omnigent reference map. Source of the sticky-`failed` constraint (Practice 9).

## Research

No relevant external findings — this is a purely internal change to the granite PTY container and its priming slash commands. No external libraries, APIs, or ecosystem patterns involved. Proceeding with codebase context.

## Root Cause

Two compounding failures, both downstream of #1694:

1. **Prefix contract decays over multi-turn cycles.** `/prime-pm-role` (`.claude/commands/granite/prime-pm-role.md`) *clearly* instructs the `[/user]`/`[/complete]`/`[/dev]` contract — but priming is **one-shot at session start**. The deleted `--append-system-prompt` path re-asserted the contract on **every turn**. The steady-state loop (`container.py` `_route_pm_classification`) writes the Dev report back to the PM PTY each cycle but never re-asserts the prefix contract. Over ~10 PM↔Dev cycles the PM drifts and stops line-leading with a prefix → classifier returns `unknown`/no routing → run exits `pm_no_user_message`.

2. **The wrap-up floor re-gates on the prefix.** `_run_wrapup_guard` (`container.py:1533-1551`) reads `last_assistant_text(pm_transcript)` but pipes it through `classify_pm_prefix`; only a detected prefix sets `user_facing_routed`. A non-empty but prefix-less wrap-up response is discarded and the canned `OPERATOR_TERMINAL_MESSAGE` is delivered instead (`:1555-1559`).

## Spike Results

### spike-1: priming is one-shot, no per-turn re-assertion (code-read) — RESOLVED
- **Assumption**: "The container never re-asserts the routing-prefix contract per turn after the initial prime."
- **Method**: code-read (`container.py` steady-state loop + `_route_pm_classification`).
- **Result**: Confirmed. The only per-turn writes to the PM PTY are (a) the Dev-report handoff (no contract text) and (b) `PM_COMPLIANCE_NUDGE` on an `unknown` classification (`:1286`). There is no standing per-turn contract reminder. `PM_WRAPUP_PROMPT` (`:226`) does restate the contract, but only at exit.
- **Confidence**: high.
- **Impact if false**: would weaken Change 1's premise; it does not.

### spike-2 (DEFERRED TO BUILD — first task, bridge machine only): real PM transcript confirms decay vs. load-failure
- **Assumption**: "The regression is contract *decay over turns*, not the prime command *failing to load*."
- **Method**: code-read of a real production PM PTY transcript (`logs/sessions/tg_valor_*/`) on the bridge machine.
- **Why deferred**: This is the skills/tools-only machine — it has no production bridge sessions exhibiting the regression. Local `logs/sessions/` entries are stale dev runs, not the affected production sessions. The build agent runs on the bridge machine where the live transcripts and the running PM exist.
- **Impact if false (prime fails to load entirely)**: Change 1's mechanism shifts from "per-turn reminder" to "fix the prime-load path"; Change 2 (relaxed floor) is unaffected and still correct. The build's first task resolves this before touching Change 1.

## Data Flow

```
Telegram msg → bridge → worker → BridgeAdapter → Container.run()
  → prime PM (one-shot /prime-pm-role)  ← [contract asserted ONCE]
  → steady-state loop (≤10 turns):
       PM idle → classify_pm_prefix(pm_tail)
         ├─ [/dev]      → forward to Dev, Dev idle, report back to PM   ← [no contract re-assert]  ← CHANGE 1 HERE
         ├─ [/user]     → on_user_payload(payload); user_facing_routed=True; break
         ├─ [/complete] → on_complete_payload; user_facing_routed=True; break
         └─ unknown     → PM_COMPLIANCE_NUDGE; continue
  → exit successful-shaped but user_facing_routed=False
  → _run_wrapup_guard():
       read last_assistant_text → classify_pm_prefix
         ├─ prefix found → route, user_facing_routed=True              ← current
         └─ no prefix    → OPERATOR_TERMINAL_MESSAGE (canned)          ← CHANGE 2 HERE
```

## Architectural Impact

Changes are confined to the granite container layer (`agent/granite_container/container.py`) and one priming slash command (`.claude/commands/granite/prime-pm-role.md`). No change to BridgeAdapter, session_executor, the worker, or the message-drafter/health-checker layer. The `ContainerResult` contract gains one new `exit_reason` value (`pm_floor_delivered`); `user_facing_routed` semantics are unchanged (still "the human got a real message").

## Appetite

**Medium.** Two complementary targeted changes plus a real-loop regression test. Explicitly NOT a revert of #1694 (26 files, three later commits build on it). Bounded to the container + one prime file.

## Prerequisites

- Build runs on the **bridge machine** (production granite sessions + live PM transcripts required for spike-2 and the real-loop test). The skills-only machine cannot validate end-to-end.

## Solution

### Key Elements

1. **Change 1 — per-turn prefix-contract re-assertion (primary).** Restore the load-bearing instruction the deleted `--append-system-prompt` path guaranteed: append a one-line contract reminder to the PM PTY on each steady-state Dev-report handoff, so the contract cannot decay across turns. Exact wording confirmed by spike-2.
2. **Change 2 — relax the wrap-up floor (defense in depth).** In `_run_wrapup_guard`, when `last_assistant_text` is **non-empty** but classification yields no routing prefix, deliver that text directly via `_on_user_payload`, set `user_facing_routed=True`, and set `exit_reason="pm_floor_delivered"`. Reserve `OPERATOR_TERMINAL_MESSAGE` for a **genuinely empty** transcript only.
3. **Sticky-`failed` guard.** The floor must not deliver (and must not flip status) when the result already reflects a `StopFailure`/failed terminal state — a failure status must remain terminal and not be overwritten by trailing PTY idle (per #1732/Omnigent Practice 9).

### Flow

After Change 1, the PM keeps emitting prefixes across all 10 turns → normal exit `pm_complete`/`pm_user` with `user_facing_routed=True`. If the PM still drifts (rare), Change 2's relaxed floor delivers its real last message instead of the canned string. Canned fires only when the PM produced literally nothing.

### Technical Approach

- **Change 1**: Add a `PM_TURN_CONTRACT_REMINDER` constant (one line: *"Begin your reply with `[/user]`, `[/complete]`, or `[/dev]` on its own line."*) and append it to the Dev-report text written to the PM PTY in the steady-state loop's `dev` branch (`container.py` ~`:1261-1263`, where `self._last_dev_report` is written back to PM). Keep it short — it is a reminder, not a re-prime. Optionally tighten `prime-pm-role.md` wording, but the per-turn reminder is the load-bearing fix.
- **Change 2**: Rewrite `_run_wrapup_guard`'s tail (`container.py:1538-1562`). When `pm_text` is truthy after `last_assistant_text`, deliver `pm_text` (stripped of any partial leading prefix) via `_on_user_payload`, set `user_facing_routed=True`, `exit_reason="pm_floor_delivered"`. Only when `pm_text` is empty/whitespace fall to `OPERATOR_TERMINAL_MESSAGE`. Guard the whole delivery behind `if not <result indicates StopFailure/failed>`.
- **`_successful_exits`**: add `pm_floor_delivered` to the set (`:1219`) so the new clean-delivery exit is not treated as an anomaly. Dropping `pm_max_turns` from the set is `[SEPARATE-SLUG #1740]`.

## Failure Path Test Strategy

### Exception Handling Coverage
- `_on_user_payload` raising during floor delivery: already wrapped in try/except (`:1563`); preserve. On exception, do NOT mark `user_facing_routed`; fall through to logging.
- `last_assistant_text` returning None/raising on a truncated transcript: treat as empty → canned path.

### Empty/Invalid Input Handling
- Genuinely empty PM transcript (`pm_text == ""`): canned `OPERATOR_TERMINAL_MESSAGE` — the one legitimate case. Asserted by test.
- Whitespace-only `pm_text`: treated as empty (`.strip()`), canned path.

### Error State Rendering
- StopFailure terminal state: floor must not overwrite. Test asserts a `failed`-flagged result is not flipped to `pm_floor_delivered` by trailing idle.

## Test Impact
- [ ] `tests/integration/test_granite_pty_production.py::test_simulated_bridge_session_completes_via_container` — UPDATE: the mock PTY emulator emits compliant prefixes by construction; add a variant whose mock PM emits a **prefix-less** final message and assert the relaxed floor delivers it (not the canned string).
- [ ] `tests/integration/test_granite_container_loop.py::test_cli_short_run_produces_results_json` — UPDATE: extend the real-loop assertion (when `_model_reachable`) to require a **non-empty, non-canned** user-facing message regardless of `exit_reason`. (Note: this test SKIPS when `claude --print ping` is unreachable — the scheduling/alert-on-skip hardening is `[SEPARATE-SLUG #1740]`.)
- [ ] New unit coverage for `_run_wrapup_guard` floor branches: non-empty→delivered, empty→canned, StopFailure→not-overwritten.

## Rabbit Holes

- **Do NOT revert #1694.** 26 files; #1710, #1708, #1663 build on it.
- **Do NOT rebuild the wrap-up guard from scratch.** It exists and predates the regression; relax it.
- **Do NOT migrate routing off the prefix mechanism.** The prefix stays the primary routing signal; the floor is a safety net, not a replacement.
- **Do NOT widen scope to the nightly-test scheduling / `_model_reachable` gating.** `[SEPARATE-SLUG #1740]`

## Risks

### Risk 1: Per-turn reminder pollutes the PM context or annoys the model
The reminder is one short line appended to an existing handoff write — minimal token cost, no new turn. Mitigation: keep it to a single sentence; spike-2 confirms the exact wording against a real transcript.

### Risk 2: Relaxed floor delivers a partial/internal PM thought as a user message
The floor only fires when the PM produced a non-empty final assistant message but omitted the prefix — that text is the PM's intended reply. Mitigation: strip any partial leading prefix token; rely on Change 1 making this path rare.

### Risk 3: spike-2 reveals prime fails to *load* (not decay)
Then Change 1's mechanism changes (fix prime-load path) but Change 2 is unaffected. Mitigation: spike-2 is the build's first task, before Change 1 code.

## Race Conditions

### Race 1: Trailing PTY idle overwrites a StopFailure with a floor delivery
A `StopFailure` (in-turn error, clean hook exit) is followed by PTY quiescence; the wrap-up guard could misread the quiet PTY and deliver a floor message over a failed status. Mitigation (Change 2's sticky-`failed` guard): once `failed`/StopFailure is set on the result, the floor short-circuits and does not deliver or flip status. Treat `failed` as terminal once set.

## No-Gos (Out of Scope)

- Reverting #1694 or any system-prompt-path restoration beyond the minimal per-turn reminder.
- Nightly-test scheduling, `_model_reachable` alert-on-skip, dropping `pm_max_turns` from `_successful_exits` — all `[SEPARATE-SLUG #1740]`.
- Any change to BridgeAdapter, session_executor, or the #1739 health-checker layer.

## Update System

No update system changes required — this feature is purely internal to the granite container and a priming slash command. The prime command (`.claude/commands/granite/prime-pm-role.md`) is a project-only file already synced with the repo checkout; no `/update` wiring or new dependency is introduced.

## Agent Integration

No new agent integration required — this is a bridge-internal change. The granite container is already invoked by the worker via `BridgeAdapter`; no new CLI entry point in `pyproject.toml` and no new bridge import. The fix changes behavior on the existing bridge→worker→container path. Integration coverage is the updated `test_granite_pty_production.py` (mocked PTY) plus the env-gated real-loop `test_granite_container_loop.py`.

## Documentation

- [ ] Update `docs/features/granite-pty-production.md` — document the per-turn prefix-contract reminder and the relaxed wrap-up floor (`pm_floor_delivered` exit reason; canned only on empty transcript).
- [ ] Add the `pm_floor_delivered` exit reason to any exit-reason reference in `docs/features/granite-pty-production.md` (and `ContainerResult` docstring at `container.py:279`).

## Success Criteria

- A real Telegram message to the Eng session round-trips a genuine, non-canned response (verified on the bridge machine).
- Granite exits `pm_complete`/`pm_user` with `user_facing_routed=True` for normal turns (production logs show `pm_no_user_message` rate returning to baseline).
- `OPERATOR_TERMINAL_MESSAGE` fires only on a genuinely empty turn (asserted by test).
- `_run_wrapup_guard` delivers `last_assistant_text` (not canned) when the PM produced a non-empty prefix-less final message (unit + mocked-PTY integration test).
- A StopFailure terminal status is not overwritten by the floor (unit test).

## Step by Step Tasks

### 1. spike-2: read a real production PM transcript (bridge machine)
Confirm decay-over-turns vs. prime-load-failure. Pick Change 1's exact mechanism + reminder wording from the evidence. Time cap: 10 min.

### 2. Change 1 — per-turn prefix-contract reminder
Add `PM_TURN_CONTRACT_REMINDER`; append to the Dev-report handoff write in the steady-state loop. Keep to one sentence.

### 3. Change 2 — relax `_run_wrapup_guard` floor
Deliver non-empty `last_assistant_text` directly; reserve canned for empty transcript; add `pm_floor_delivered` exit reason; add sticky-`failed` guard. Add `pm_floor_delivered` to `_successful_exits`.

### 4. Tests
Unit tests for the three floor branches (non-empty→delivered, empty→canned, StopFailure→not-overwritten). Mocked-PTY integration variant with a prefix-less PM. Extend the real-loop test's assertion (env-gated).

### 5. Documentation
Update `docs/features/granite-pty-production.md` and the `ContainerResult` exit-reason docstring.

### 6. Final Validation
Run `tests/integration/test_granite_pty_production.py` + `tests/unit` granite tests green. On the bridge machine, send a real Telegram message and confirm a genuine round-trip + baseline `pm_no_user_message` rate.

## Verification

- `scripts/pytest-clean.sh tests/integration/test_granite_pty_production.py tests/unit -k granite -v` → green.
- Bridge-machine manual: send a Telegram message to the Eng session; confirm a non-canned reply; tail `logs/worker/*.log` for `pm_complete`/`pm_user`/`pm_floor_delivered` (not `pm_no_user_message`).

## Open Questions

1. **spike-2 (build-time, bridge machine):** Does the real PM transcript show contract *decay over turns* (expected) or prime *load failure*? This selects Change 1's exact mechanism. Cannot be answered on the skills-only machine; resolved as the build's first task.
2. Should the per-turn reminder also be appended after `PM_COMPLIANCE_NUDGE`, or is the Dev-report handoff sufficient? (Lean: handoff is the high-frequency path; revisit if spike-2 shows misses cluster elsewhere.)
