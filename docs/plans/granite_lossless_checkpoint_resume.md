---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-06-17
tracking: https://github.com/tomcounsell/ai/issues/1721
last_comment_id:
revision_applied: true
---

# Granite Lossless Checkpoint Resume

## Problem

Granite is this repo's interactive-TUI session runner: it drives two real Claude Code TUIs (a PM persona and a Dev persona) over PTYs and relays between them in a loop (`agent/granite_container/container.py`, `Container.run()`). When a granite session dies (stalled stream, in-loop hang watchdog, crash, worker restart) **or** when a user replies to a finished session, granite cannot continue where it stopped. It always starts a brand-new `Container` from turn 0 with two fresh TUIs and zero prior transcript.

**Current behavior:**
- **Stall / hang / crash:** the watchdog (`_cycle_idle`, ~120s, `container.py:1098`) exits `pm_hang`/`dev_hang`, or an exception ends the run. Crash-recovery (#1539 / PR #1718) may re-enqueue the session to `pending` with a steering message, but the worker spawns a **fresh** Container from turn 0. The "resume" re-does work blind.
- **User reply to a finished session:** the bridge synthesizes prior context into the message *text* (`bridge/telegram_bridge.py:1788-1853`, `_build_completed_resume_text`) and dispatches a brand-new session. The two TUIs start cold; context is approximated in the prompt, not restored.

Root cause: the orchestration loop is entirely ephemeral in-memory. Three pieces of state hold a granite conversation and only the first two survive a death — (1) PM transcript on disk, (2) Dev transcript on disk, (3) the `Container` loop position (turn index, current actor, mid-relay, last classification), which lives only in `Container.run()` locals. Two specific misses make resume impossible: the PM/Dev Claude Code session UUIDs are generated fresh per run and discarded (`bridge_adapter.py:412-413`), and there is no durable loop cursor.

**Desired outcome:**
A granite session that stalled, was stopped, crashed, or is continued by a user reply resumes exactly where it stopped: the same PM and Dev TUIs reattach to their full prior transcripts via `claude --resume`, and the loop re-enters at the precise turn/actor/relay position. The reply (or auto-resume steering message) becomes the next input on a restored loop, not the seed of a fresh one. The dashboard shows live loop position (e.g. "turn N, waiting on Dev").

## Freshness Check

**Baseline commit:** `ab9c0faf`
**Issue filed at:** 2026-06-17T10:49:22Z (same day as planning)
**Disposition:** Overlap (complementary, not blocking)

**File:line references re-verified (all from same-day exploration, still hold):**
- `agent/granite_container/pty_driver.py:382-384` — flag assembler emits `--model`, `--permission-mode bypassPermissions`, conditional `--session-id`; no `--resume` — holds.
- `agent/granite_container/bridge_adapter.py:412-413` — PM/Dev UUIDs generated fresh, not persisted — holds.
- `agent/granite_container/container.py:850-1175` — loop state in locals — holds.
- `bridge/telegram_bridge.py:1788-1853` — reply-to synthesizes context, spawns fresh — holds.
- `reflections/crash_recovery.py`, `agent/crash_signature.py:207-228` — auto-resume + determinism guardrail — holds (shipped PR #1718).

**Cited sibling issues/PRs re-checked:**
- #1539 — CLOSED, PR #1718 merged. Auto-resume decides *whether*; this issue makes it *lossless*.
- #1538 — OPEN, plan in progress (`docs/plans/stalled_session_advisory_classifier.md`). Detection side; complementary.
- #1648 / PR #1658 — dashboard telemetry parity for granite; this extends it with loop cursor.

**Active plans overlapping this area:** `stalled_session_advisory_classifier.md` (#1538). Overlap is complementary: #1538 *detects* stalled running sessions (advisory verdict); this plan *resumes* them losslessly. **No field collision exists** (critique-verified): #1538 adds its only field (`stall_advisory`) to `PipelineProgress` in `ui/data/sdlc.py`, NOT to `AgentSession`. This plan's new fields are all on `AgentSession`. The earlier "coordinate on AgentSession field additions" concern is therefore void.

**Notes:** No drift. Same-day filing; the only landed commits on main are the #1538 plan revision and a `last_comment_id` fix, neither touching granite container code.

## Prior Art

- **#1539 / PR #1718** — Crash-signature library + auto-resume policy. Re-enqueues resumable terminal sessions but spawns fresh. This plan closes the lossless half.
- **#1546 / PR #1570** — PoC: granite drives a real interactive Claude Code TUI via PTY. Established the PTY substrate this builds on.
- **PR #1612** — Granite PTY production cutover + bounded slot pool. Established `PTYPool` reserve/release semantics resume must respect.
- **#1648 / PR #1658** — Dashboard telemetry parity for granite (turn_count, tokens, exit_reason). The loop cursor extends this.
- **#1710 / PR #1717** — Granite startup fast diagnostic (plateau detection, `startup_failure_kind`). Feeds the determinism guardrail that gates which signatures may resume.

No prior attempt at lossless granite resume exists. `docs/features/granite-pty-production.md` documents the absence as a known limitation.

## Research

**Queries used:**
- `claude --help` flag inspection (CLI semantics, run directly).
- Earlier claude-code-guide sweep on Claude Code stream-stall behavior and resume guarantees.

**Key findings:**
- `claude --resume <uuid>` resumes a conversation by session ID and **preserves the same session ID by default**; `--fork-session` (opt-in) is what creates a new ID. So the resume handle stays stable across repeated resumes — the loop cursor's UUID remains valid. (Source: `claude --help`.)
- `--resume` and `--session-id` are distinct: `--session-id` forces a specific (new) ID at creation; `--resume` reattaches an existing one. They are mutually exclusive in intent — the assembler must branch, not pass both.
- `claude --resume` restores full message history + tool results, but does NOT restore a custom `--system-prompt`. Granite does not pass `--system-prompt` (personas are installed via `/granite:prime-*` slash commands recorded in the transcript), so resume restores the primed personas naturally — which is exactly why re-priming must be skipped on resume.
- Claude Code's upstream SSE stall (silent mid-stream hang) is the same failure mode resume recovers from; a resumed run is subject to it too, so the resume path should fail fast (tight idle timeout) rather than re-hang.

## Spike Results

### spike-1: `--resume`/`--session-id` CLI semantics
- **Assumption**: "`claude --resume <uuid>` preserves the same session ID so the cursor handle stays valid across multiple resumes."
- **Method**: code-read (`claude --help`)
- **Finding**: Confirmed. `--resume [value]` resumes by session ID; `--fork-session` is the opt-in flag to create a new ID on resume. Default keeps the ID. `--session-id <uuid>` is a separate creation-time flag.
- **Confidence**: high
- **Impact on plan**: The assembler branch is `if resume_uuid: --resume <uuid>` mutually exclusive with `--session-id`, and we must NOT pass `--fork-session`. Persisted UUIDs remain the durable handles indefinitely.

### spike-2 (DEFERRED TO BUILD — first task): two-transcript lockstep resume reliability
- **Assumption**: "`claude --resume` on *both* PM and Dev re-establishes the relay handshake cleanly, including when a transcript was truncated mid-write at crash time."
- **Method**: prototype (worktree isolation) — spawn a real PM+Dev pair, kill mid-relay, resume both, verify the handshake re-enters.
- **Result**: NOT YET RUN. This is the load-bearing unknown; it is the **first build task (build-spike)** and gates the rest of the implementation. If lockstep resume is unreliable, the plan pivots to single-side resume (resume Dev only, re-prime PM with a continuation summary) — documented in Risks.
- **Confidence**: unknown (to be resolved)
- **Impact if false**: Pivot to single-side resume; loop cursor still required, but reconstruction strategy changes.

## Data Flow

1. **Entry point**: a granite session terminates (watchdog/crash/exception) OR a user reply arrives in the bridge for a session with resume handles.
2. **Crash-recovery / reply path**: `reflections/crash_recovery.py` (auto) or `telegram_bridge.py` reply handler (manual) decides the session is resumable and re-enqueues to `pending`, carrying a `resume=True` intent + the persisted handles/cursor.
3. **Worker dispatch**: `agent/session_executor.py` reads the session; when resume handles are present, builds a `PairSpawnSpec` with `pm_resume_uuid`/`dev_resume_uuid` and the loop cursor instead of fresh UUIDs.
4. **PTY acquire**: `PTYPool.acquire_pair(spawn_spec)` reserves a slot (gated on terminal status of the prior session to avoid double-acquire) and spawns the pair.
5. **Flag assembly**: `pty_driver.py` emits `--resume <uuid>` (not `--session-id`, not `--fork-session`), inheriting all other flags + env from the same assembler.
6. **Container resume**: `Container.run()` detects the resume cursor, skips priming + startup-settle, and re-enters the steady-state loop at `(turn_index, current_actor, mid_relay)`.
7. **Output**: PM/Dev continue their restored transcripts; the reply/steering message is delivered as the next input; per-turn cursor writes resume; user-facing output routes as normal.

## Architectural Impact

- **New dependencies**: none external. Reuses `claude --resume`, existing `PTYPool`, `AgentSession`, crash-recovery.
- **Interface changes**: `PairSpawnSpec` gains `pm_resume_uuid`/`dev_resume_uuid` + cursor fields; `PTYDriver` gains a resume UUID param; `Container.__init__`/`run()` gain a resume-cursor entry path; `AgentSession` gains nullable fields.
- **Coupling**: slightly increases coupling between crash-recovery/reply paths and the granite container (they now pass resume intent through). Mitigated by keeping the spawn-spec the single hand-off contract.
- **Data ownership**: `AgentSession` becomes the durable owner of granite loop position (it already owns granite forensics). On-disk JSONL transcripts remain the conversation source of truth.
- **Reversibility**: high. All fields are additive/nullable; if `resume_uuid` is absent the system behaves exactly as today (fresh spawn). Feature can be gated behind a settings flag.

## Appetite

**Size:** Large

**Team:** Solo dev (lead), async-specialist (PTY/loop reconstruction), test-engineer, documentarian, validator

**Interactions:**
- PM check-ins: 2-3 (spike outcome gates design; pivot decision if lockstep fails)
- Review rounds: 2+ (concurrency-sensitive; PTY + loop reconstruction)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on PATH | `command -v claude` | Resume flag target |
| Granite PTY infra present | `python -c "import agent.granite_container.pty_driver"` | Module under change exists |
| Redis up (Popoto) | `python -c "from models.agent_session import AgentSession; AgentSession.query.all()"` | Field persistence |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_lossless_checkpoint_resume.md`

## Solution

### Key Elements

- **Resume handles**: persist the PM/Dev Claude Code session UUIDs already generated at spawn, so they can be reused as `--resume` targets.
- **Loop cursor**: a minimal durable position marker (turn index, current actor, mid-relay flag, last classification) written per turn-boundary.
- **Resume-aware flag assembler**: branch the single PTY spinup assembler to emit `--resume` mutually exclusively with `--session-id`, inheriting every other flag/env.
- **Resume-aware container startup**: skip `/granite:prime-*` and startup-settle on resume; re-enter the steady-state loop at the cursor.
- **Reply-to-as-resume**: when a reply targets a session with handles, thread them into the spawn spec instead of synthesizing context into the message text.
- **Guardrail honor**: only resumable signatures resume; determinism-guardrail (never-started / startup-plateau) stays escalate-only.

### Flow

Session dies / user replies → recovery-or-reply path detects resume handles → re-enqueue `pending` with resume intent → worker builds resume `PairSpawnSpec` → `PTYPool` acquires slot (terminal-gated) → assembler emits `--resume` → `Container` skips prime, re-enters loop at cursor → PM/Dev continue restored transcripts → next input delivered → output routes normally.

### Technical Approach

- **Persist UUIDs**: add `pm_session_uuid`/`dev_session_uuid` nullable Fields to `AgentSession`; save the values generated at `bridge_adapter.py:412-413` on first spawn (write-once). Additive nullable fields need no backcompat (`_heal_descriptor_pollution` is generic — see #1099/#1172).
- **Persist cursor**: add `turn_index` (IntField), `current_actor` (Field: "pm"|"dev"), `mid_relay` (bool via `_truthy()` helper — Popoto stores bools as strings), `last_classification` (Field/JSON), plus `resume_intent` (bool) as the explicit branch signal (see below).
- **Cursor write hand-off (B1)**: there is NO `AgentSession.save()` inside `container.py` — the container holds no AgentSession handle, and `container.run()` is consumed once via `asyncio.to_thread(container.run)` returning a single `ContainerResult` only after the loop exits (`bridge_adapter.py:457`). A post-run write checkpoints zero in-flight turns for the dominant death modes (worker restart, crash, ~120s hang-kill), where `run()` never returns. The per-turn save that DOES exist is in `BridgeAdapter._bump_last_turn_at` (`bridge_adapter.py:617`), driven by the container's `on_turn` hook — but that hook is a zero-arg `Callable[[], None]` (`container.py:531`, called at `container.py:1053,1130`), so loop position is invisible to it. **Fix: widen the `on_turn` signature to `on_turn(turn_index, current_actor, mid_relay)`** and have `_bump_last_turn_at` write the cursor in the same per-turn `save(update_fields=[...])`. Persistence stays in BridgeAdapter (which owns the AgentSession handle); the container only reports position. This checkpoints every turn boundary, so resume re-enters at the last committed turn even when `run()` never returns.
- **Assembler branch** (`pty_driver.py:382`): `args = ["--model", model, "--permission-mode", "bypassPermissions"]`; then `if resume_uuid: args += ["--resume", resume_uuid]` `elif session_id: args += ["--session-id", session_id]`. Never add `--fork-session`. Carry `resume_uuid` via new `PairSpawnSpec` fields and `PTYDriver.__init__`.
- **cwd validation**: before resume, confirm the transcript path (`pm_transcript_path`/`dev_transcript_path`) still exists on disk; if the worktree was GC'd, fall back to a fresh run (clear resume intent, spawn with fresh `--session-id`).
- **Container resume entry**: `Container.run()` accepts a resume cursor; when present, skip `_prime_session` (personas already in transcript) and the startup-settle loop, and re-enter steady-state. If `mid_relay` and `current_actor == "dev"`, first re-read Dev's restored transcript and relay to PM (re-enter `dev_wait`); else re-classify PM's last text.
- **Reply path (C4)** (`telegram_bridge.py`): when the replied-to session has resume handles and a resumable status, set `resume_intent=True` and let the user's message ride as the next steering input. Do NOT delete `_build_completed_resume_text` wholesale — `claude --resume` restores the granite transcripts but NOT the bridge's reply-chain framing (quoted/threaded reply context). Route that `reply_chain_context` into the next-input/steering message so threaded replies keep their framing on top of the restored transcript. Only the prior-context *synthesis* (which the transcript now supplies) is dropped.
- **Auto-resume path (B2)**: `resume_session()` lives at `tools/valor_session.py:631` (NOT `reflections/crash_recovery.py` — that reflection *calls* it). It hard-rejects any session where `claude_session_uuid is None` (`valor_session.py:670`). Granite sessions never set `claude_session_uuid`; they use `pm_session_uuid`/`dev_session_uuid`. **Fix: make the precondition granite-aware** — accept `claude_session_uuid` OR (both granite UUIDs present). This function cross-cuts the `valor-session resume` CLI and the `/do-build` path, so the amendment must preserve existing non-granite behavior. The reflection (`reflections/crash_recovery.py`) then sets `resume_intent=True` + handles/cursor on the re-enqueued record. Confirm worker dispatch reads the granite UUIDs, not `result.claude_session_uuid`. Guardrail unchanged (escalate-only for `NON_RESUMABLE_DETERMINISTIC`).
- **Resume-intent signal (C2)**: UUIDs are write-once and present on *every* re-enqueue (including ordinary fresh ones), so field-presence cannot distinguish a lossless cursor-resume from a fresh re-enqueue. The worker branches on the explicit `resume_intent` bool: `True` → take the `--resume` + skip-prime + cursor-re-entry path; `False`/None → fresh spawn as today. `resume_intent` is set only by the auto-resume reflection and the reply-to-resume path, never on first dispatch.
- **Attempt-cap carry-forward (C1)**: lossless resume creates a *new* pending record, but #1539 tracks `auto_resume_attempts` on the prior record. **Carry the attempt count forward** onto the new record so a resume→re-stall chain still hits `max_auto_attempts` and escalates instead of resetting to 0 (unbounded resume storm on a bounded PTY slot). Add **mutual gating** so the auto-resume reflection and any reply-path resume do not both fire on a single terminal event (idempotency key on the terminal transition).
- **Fail-fast on resumed stall**: set a tighter `API_FORCE_IDLE_TIMEOUT` for resumed PTYs so a re-stalled stream aborts quickly into another resume attempt (bounded by the existing per-session attempt cap) rather than hanging.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except Exception` blocks in `bridge_adapter.py`, `container.py`, `pty_driver.py` touched by resume; each must assert observable behavior (log + fallback-to-fresh), not silent swallow.
- [ ] Resume with a missing/GC'd transcript must log and fall back to fresh spawn (assert the log + fresh `--session-id` path taken).

### Empty/Invalid Input Handling
- [ ] Resume with `None`/empty `resume_uuid` → behaves exactly as today (fresh spawn). Test asserts no `--resume` flag emitted.
- [ ] Resume with a non-existent UUID (transcript gone) → fall back to fresh, no crash.
- [ ] Cursor with out-of-range `turn_index` → clamp/fall back to fresh, logged.

### Error State Rendering
- [ ] If resume fails and falls back to fresh, the user still gets a coherent response (no raw error reaches Telegram — see persona-always rule).
- [ ] Dashboard renders live loop position fields without error when cursor is null (pre-resume sessions).

## Test Impact

- [ ] `tests/` granite PTY driver tests (flag assembly) — UPDATE: assert the new `--resume`/`--session-id` exclusivity branch and that `--fork-session` is never added.
- [ ] `tests/` granite bridge_adapter tests — UPDATE: assert UUIDs are persisted on first spawn and cursor is written per turn.
- [ ] `tests/` AgentSession model tests — UPDATE: cover new nullable fields default to None and round-trip (esp. `mid_relay` via `_truthy()`).
- [ ] reply-to resume tests (`telegram_bridge`) — REPLACE: a reply to a session with handles must continue the same transcripts, not synthesize-and-fresh.
- [ ] crash-recovery tests (`reflections/crash_recovery.py`) — UPDATE: resumable signature now sets resume handles on the re-enqueued record; guardrail path unchanged.

If areas lack coverage, add new tests rather than skipping — granite PTY has thin unit coverage historically.

## Rabbit Holes

- **Reattaching to dead PTY PIDs.** Do NOT try to keep or re-attach the original PTY processes. Resume spawns *fresh* processes via `--resume`; dead PIDs are irrelevant.
- **Re-serializing transcript content into Redis.** Unnecessary — the JSONL transcripts on disk are the source of truth. Persist only handles + cursor.
- **Generalized checkpoint/restore framework.** Resist building a generic session-snapshot system. Scope is granite loop position only.
- **Perfect mid-tool-call resume.** If Dev died mid-tool-call, `claude --resume` handles transcript-level restoration; do not attempt to replay partial tool invocations ourselves.
- **Fixing upstream SSE stalls.** Out of scope — that's a Claude Code client bug. We make resume cheap and fast, not the stream stall-proof.

## Risks

### Risk 1: Two-transcript lockstep resume is unreliable
**Impact:** Resuming both PM and Dev produces a desynchronized handshake (e.g. PM re-asks something Dev already answered), corrupting the conversation.
**Mitigation:** build-spike runs first and gates the design. If unreliable, pivot to single-side resume: resume Dev (the work-holder) via `--resume`, re-prime PM fresh with a continuation summary derived from the cursor + Dev transcript. Cursor is required either way.

### Risk 2: Truncated transcript at crash time
**Impact:** A transcript half-written when the process died may be unparseable by `claude --resume`, failing the resume.
**Mitigation:** spike tests the truncated case explicitly; on resume failure, fall back to fresh spawn (never crash). Detect via resume process exiting non-zero / not reaching idle.

### Risk 3: Double-acquire of a PTY slot
**Impact:** A resume acquires a slot while the dying session still holds one → semaphore deadlock or duplicate spawn.
**Mitigation:** gate resume on terminal status of the prior session (the Race-1 terminal-only re-check pattern from #1539, `reflections/crash_recovery.py:346-364`); confirm the gate also covers the reply-to path.

### Risk 4: Resumed run re-stalls (same upstream SSE bug)
**Impact:** Resume hangs the same way the original did, burning a slot for the full timeout.
**Mitigation:** tighter `API_FORCE_IDLE_TIMEOUT` on resumed PTYs + the existing per-session attempt cap; escalate after N failed resumes.

## Race Conditions

### Race 1: Resume acquires slot before prior session is terminal
**Location:** `agent/session_executor.py` dispatch → `agent/granite_container/pty_pool.py` `acquire_pair`
**Trigger:** crash-recovery/reply enqueues a resume while the original PTY pair is still being torn down.
**Data prerequisite:** prior session status == terminal AND its slot released before the resume acquires.
**State prerequisite:** the resume handles/cursor are fully persisted before re-enqueue.
**Mitigation:** terminal-status gate before re-enqueue (reuse #1539 pattern); persist handles/cursor in the same `save()` that drives the terminal transition.

### Race 2: Cursor write vs. read on rapid resume
**Location:** per-turn `save()` in `bridge_adapter.py` vs. resume read in worker dispatch
**Trigger:** a session dies immediately after a turn; resume reads a cursor mid-write.
**Data prerequisite:** cursor write is atomic per turn (single `save(update_fields=[...])`).
**State prerequisite:** worker reads the persisted record, not in-memory Container state (which is gone).
**Mitigation:** write the full cursor in one `save()`; reads are post-terminal so the last committed cursor is authoritative.

### Race 3: Auto-resume and reply-resume both fire on one terminal event
**Location:** `reflections/crash_recovery.py` (auto path) vs. `bridge/telegram_bridge.py` reply handler
**Trigger:** a session goes terminal and the user replies at nearly the same moment the crash-recovery reflection picks it up — two resume re-enqueues for one death.
**Data prerequisite:** exactly one re-enqueue per terminal event; `auto_resume_attempts` carried forward, not reset.
**State prerequisite:** an idempotency marker on the terminal transition consumed by whichever path fires first.
**Mitigation:** mutual gating via an idempotency key written at terminal transition; the second path observes it and no-ops. Attempt count carries forward onto the new pending record (C1).

## Omnigent Deltas (from #1732 reference map)

The following two deltas from issue #1732's reference map home to this issue and plan. They are additive — they do not change the plan's scope or contradict its design:

### Delta 1: Fork-on-resume guard

**Source:** Omnigent `claude_native_hook.py:123-167`, `claude_native_bridge.py:1346-1351` (pinned to omnigent HEAD 2026-06-18; re-verify on revisit).

When `claude --resume <uuid>` runs, if the resumed session surfaces a *new* Claude session ID (different from the one passed to `--resume`), Omnigent forks the session record rather than silently rebinding to an unexpected session. The `seen_claude_session_ids` set detects this: a `SessionStart source=resume` event with an unfamiliar session ID means the resume spawned a different underlying session than expected.

**Our-side acceptance criterion addition:** The `--resume` path in `pty_driver.py` (step 3 of this plan) SHOULD log a warning when the post-resume session ID differs from the one we passed to `--resume`. This guards the case where `--resume` creates a divergent session (e.g., due to transcript corruption or missing checkpoint). The fork-detection does not need to be a hard abort — a warning + continuing on the new session is acceptable — but it must be **detectable** so operators can diagnose unexpected forks.

**Reconciliation against determinism guardrail:** This guard is complementary to the existing `NON_RESUMABLE_DETERMINISTIC` guardrail (`agent/crash_signature.py:207-228`, `reflections/crash_recovery.py:280-293`). The guardrail gates *whether* to attempt resume (never-started → escalate-only). The fork guard applies *during* a permitted resume to detect unexpected session divergence. No conflict.

### Delta 2: Dead-vs-stalled disambiguation rationale

**Source:** Issue #1732 crash/resume section; reconciled against `reflections/crash_recovery.py:280-293` and `agent/crash_signature.py:207-228`.

The hook-edge architecture (once #1688 ships) changes the meaning of PTY silence:

| State | Before hook edge (C5 heuristic) | After hook edge |
|-------|--------------------------------|-----------------|
| Process dead (`pexpect.EOF`, `isalive()==False`) | Detected — process gone, crash path | Same — resume the session |
| Stalled-but-alive + no Stop within watchdog | *Ambiguous* — could be thinking or wedged | Unambiguous — still running (hook would have fired if done) |
| Never-started / startup plateau | `NON_RESUMABLE_DETERMINISTIC` — escalate | Same — guardrail unchanged |

**Implication for this plan:** The lossless resume path (#1721) handles the "process dead" case (crash or stall that killed the process). The hook-edge architecture (#1688) handles the "stalled-but-alive + no Stop" case by making it unambiguous. These two issues are complementary: #1721 resumes dead processes losslessly; #1688 eliminates false completions from alive-but-quiet processes. No conflict with the determinism guardrail — never-started stays escalate-only regardless.

**No Open Question raised:** Omnigent's model confirms the two-layer architecture (hook edge for happy path, PTY for crash/liveness detection) is sound for our case. The reconciliation finds no contradictory resume triggers between #1688 and #1721.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1538] Live stalled-session *detection* (advisory classifier) — owned by Pillar 1; this plan consumes its verdicts but does not build detection.
- [SEPARATE-SLUG #1712] Bridge stale-update-stream detector — separate reliability concern, not granite loop resume.
- [EXTERNAL] Fixing the upstream Claude Code SSE stream-stall bug — outside this repo; we mitigate via fast-fail resume, not a client fix.

## Update System

No update system changes required — this feature is purely internal to the granite container + worker on each machine. No new dependencies, config files, or migration steps (additive nullable `AgentSession` fields self-heal). If a settings flag gates the feature, it lives in `config/settings.py` and is read at runtime, requiring no update-script change.

## Agent Integration

No new agent-facing tool surface is required — this is a bridge/worker-internal change to how granite sessions spawn and resume. The agent already invokes granite via the normal bridge path; resume is transparent. Existing CLI surfaces extend naturally:
- `valor-session telemetry`/`inspect` will display the new cursor fields (read-only, no new command).
- `dashboard.json` exposes the new fields via the existing `_session_to_json` serializer (`ui/app.py`).
Integration tests verify a reply through the bridge resumes the same transcripts.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — remove the "Resume is a fresh TUI session / no `claude --resume` wiring" limitation; document the resume handles, loop cursor, resume-aware startup, and reply-to-as-resume.
- [ ] Add/refresh entry in `docs/features/README.md` index.

### Inline Documentation
- [ ] Docstrings on the new `PairSpawnSpec` fields, `PTYDriver` resume param, and `Container` resume entry path.
- [ ] Comment the `--resume`/`--session-id` exclusivity and the "never `--fork-session`" invariant at the assembler.

## Success Criteria

- [ ] `pm_session_uuid`/`dev_session_uuid` persisted on `AgentSession` and visible on `dashboard.json`.
- [ ] Loop cursor (`turn_index`, `current_actor`, `mid_relay`, `last_classification`) persisted per turn, survives session death.
- [ ] Assembler emits `--resume <uuid>` mutually exclusive with `--session-id`, never `--fork-session`; cwd-existence validation with fresh-run fallback.
- [ ] On resume, the loop re-enters steady-state at the cursor. **(C3 conditional on build-spike):** if lockstep resume is reliable, both PM and Dev skip re-priming and continue the *same* transcripts; if the spike forces the single-side pivot, Dev continues its transcript and PM is re-primed with a continuation summary — the acceptance bar follows the spike outcome, not an unconditional "skips re-priming."
- [ ] A user reply to a session with handles continues at least the Dev work transcript (full lockstep continuity if the spike passes), not a fully fresh pair, AND preserves reply-chain framing (C4).
- [ ] Crash-recovery auto-resume drives a lossless resume for resumable signatures; determinism-guardrail signatures remain escalate-only.
- [ ] build-spike documents two-transcript lockstep reliability (incl. truncated case) with the chosen strategy.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead orchestrates; it never builds directly.

### Team Members

- **Builder (spike)** — Name: `spike-builder` — Role: prototype two-transcript lockstep resume in a worktree, return reliability finding — Agent Type: builder — Resume: true
- **Builder (persistence)** — Name: `persist-builder` — Role: AgentSession fields + UUID/cursor writes — Agent Type: builder — Resume: true
- **Builder (assembler)** — Name: `pty-builder` — Role: PairSpawnSpec/PTYDriver `--resume` branch + cwd validation — Agent Type: async-specialist — Resume: true
- **Builder (container)** — Name: `container-builder` — Role: resume-aware startup + loop re-entry — Agent Type: async-specialist — Resume: true
- **Builder (paths)** — Name: `paths-builder` — Role: reply-to-as-resume + crash-recovery handle threading — Agent Type: builder — Resume: true
- **Validator** — Name: `resume-validator` — Role: verify criteria, transcript continuity, fallback paths — Agent Type: validator — Resume: true
- **Documentarian** — Name: `resume-docs` — Role: update granite docs — Agent Type: documentarian — Resume: true

### Available Agent Types
See template — async-specialist recruited for PTY/loop concurrency.

## Step by Step Tasks

### 1. Spike: two-transcript lockstep resume
- **Task ID**: build-spike
- **Depends On**: none
- **Validates**: spike result documented; go/no-go on lockstep vs single-side resume
- **Informed By**: spike-1 (CLI semantics confirmed)
- **Assigned To**: spike-builder
- **Agent Type**: builder
- **Parallel**: false (gates the rest)
- In a worktree, spawn a real PM+Dev granite pair, kill mid-relay, resume both via `--resume`, verify the handshake re-enters cleanly.
- Test the truncated-transcript case (kill mid-write).
- Return: reliable? if not, recommend single-side resume strategy.

### 2. Persist resume handles + loop cursor
- **Task ID**: build-persist
- **Depends On**: build-spike
- **Validates**: AgentSession field tests; dashboard serializer test
- **Assigned To**: persist-builder
- **Agent Type**: builder
- **Parallel**: true
- Add nullable fields to `AgentSession` (`pm_session_uuid`, `dev_session_uuid`, `turn_index`, `current_actor`, `mid_relay`, `last_classification`, `resume_intent`); save UUIDs at first spawn (write-once).
- Widen the container `on_turn` hook to `(turn_index, current_actor, mid_relay)` and write the cursor in `BridgeAdapter._bump_last_turn_at` per turn (B1).
- Expose fields in `ui/app.py` serializer. Note: dashboard rendering is observability polish — it must NOT gate core resume (nit).

### 3. Resume-aware PTY assembler
- **Task ID**: build-assembler
- **Depends On**: build-spike
- **Validates**: pty_driver flag-assembly tests
- **Assigned To**: pty-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add resume fields to `PairSpawnSpec`/`PTYDriver`; branch assembler (`--resume` xor `--session-id`, never `--fork-session`); cwd-existence validation + fresh fallback.

### 4. Resume-aware container startup + loop re-entry
- **Task ID**: build-container
- **Depends On**: build-persist, build-assembler
- **Validates**: container resume-entry tests (skip-prime, cursor re-entry, mid-relay)
- **Assigned To**: container-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- `Container.run()` resume cursor entry: skip prime + startup-settle, re-enter steady-state at `(turn_index, current_actor, mid_relay)`.

### 5. Reply-to-as-resume + crash-recovery threading
- **Task ID**: build-paths
- **Depends On**: build-container
- **Validates**: reply-to continuity test; crash-recovery handle-threading test
- **Assigned To**: paths-builder
- **Agent Type**: builder
- **Parallel**: false
- Amend `resume_session()` (`tools/valor_session.py:631`) precondition to be granite-aware (B2), preserving non-granite behavior.
- Reply path sets `resume_intent=True`, threads handles into spawn spec, routes `reply_chain_context` into the next input (C4); auto path carries `auto_resume_attempts` forward (C1).
- Terminal-status gate + mutual-gating idempotency key confirmed on both paths (Race 1, Race 3).

### 6. Validation
- **Task ID**: validate-resume
- **Depends On**: build-paths
- **Assigned To**: resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria, fallback paths, no raw errors to Telegram.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-resume
- **Assigned To**: resume-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` + README index.

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks; confirm docs created; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resume flag branch present | `grep -n "\-\-resume" agent/granite_container/pty_driver.py` | output contains `--resume` |
| No fork-session | `grep -rn "fork-session" agent/granite_container/` | exit code 1 |
| Cursor fields on model | `grep -nE "pm_session_uuid\|turn_index\|current_actor\|mid_relay" models/agent_session.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | B1: "fold cursor into existing per-turn save" — no AgentSession.save() exists in container.py; run() never returns on the dominant death modes | Technical Approach "Cursor write hand-off (B1)" | Widen `on_turn` to `(turn_index, current_actor, mid_relay)`; write cursor in `_bump_last_turn_at` per turn. Container reports, BridgeAdapter persists. |
| BLOCKER | Risk & Robustness | B2: `resume_session()` (at `tools/valor_session.py:631`, not crash_recovery) rejects sessions with `claude_session_uuid is None` — every granite resume fails before new logic | Technical Approach "Auto-resume path (B2)" | Make precondition granite-aware (claude_session_uuid OR both granite UUIDs); preserve non-granite behavior. |
| CONCERN | Risk | C1: attempt-cap resets across the new pending record; no mutual gating | Technical Approach "Attempt-cap carry-forward (C1)" + Race 3 | Carry `auto_resume_attempts` forward; idempotency key on terminal transition. |
| CONCERN | History | C2: resume-intent signal hand-waved (UUIDs write-once, present on fresh re-enqueue too) | Technical Approach "Resume-intent signal (C2)" | Explicit `resume_intent` bool; worker branches on it. |
| CONCERN | History | C3: success criteria presuppose unrun lockstep spike | Success Criteria (made conditional on build-spike) | Criteria follow spike outcome (lockstep vs single-side). |
| CONCERN | Scope | C4: deleting `_build_completed_resume_text` drops reply-chain framing | Technical Approach "Reply path (C4)" | Route `reply_chain_context` into the next-input message. |
| NIT | Scope | Dashboard "live loop position" framed as co-equal deliverable | build-persist task note | Keep fields (near-free); do not gate core resume on rendering. |

---

## Open Questions

Resolved with documented defaults (override at build time if desired):

1. **Lockstep vs single-side resume** — prefer lockstep; fall back to single-side per-session on resume failure. (Gated by build-spike.)
2. **Feature gating** — ship behind `GRANITE__RESUME_ENABLED`, default on after the spike passes.
3. **Reply-to scope** — start with `completed` + `killed`; expand to `failed`/`abandoned` after soak.
