---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1208
last_comment_id:
revision_applied: true
---

# Kill Is Terminal — Stop Killed Sessions From Resurrecting

## Problem

An operator can kill an AgentSession via `valor-session kill --id <id>` (or `--all`), expecting that to be a hard stop. **It is not.** The killed session continues to drive Telegram traffic, dispatch new pipeline stages, and survive a computer restart.

Witnessed live in PM: Valor (chat_id `-1003449100931`, parent session `tg_valor_-1003449100931_754`):

1. Operator killed all running sessions. The parent PM (`tg_valor_-1003449100931_754`, agent_session_id `04a1b7ba207449a98169171c5e44513a`) was set to `status=killed` on disk.
2. Worker `python -m worker` (PID 750, run by `com.valor.worker` LaunchAgent) kept invoking `_agent_session_hierarchy_health_check()` every 5 minutes.
3. The check's query `AgentSession.query.filter(status="waiting_for_children")` kept matching the killed parent, even though its hash status was `killed`. (Likely Popoto IndexedField staleness or lazy-load skew — root-cause investigation deferred to a follow-up issue per the Follow-up Issues section below; operational mitigation lives in Fix B.)
4. For each match, the check called `schedule_pipeline_completion()` → drafted a final summary → **queued to `telegram:outbox:tg_valor_-1003449100931_754`** → relay shipped the message to Tom's chat.
5. *Only after* shipping the message did the runner call `finalize_session(parent, "completed", ...)`, which raised `StatusConflictError` (`expected 'killed' on disk, found 'completed'`). The error was logged but harmless — the spam had already gone out.
6. Tom received 9+ near-identical "All 5 child pipelines completed..." status messages over 90 minutes. Operator restart of the computer did not stop it because launchd auto-respawned the worker.

**Current behavior:**
- Operator kill of a parent PM is silently ignored by the periodic hierarchy health check; the runner ships Telegram messages every 5 min.
- Operator kill of a child is treated by parent progression logic as a successful completion → next pipeline stage dispatches → continuation chain keeps moving.
- Restart does not help: the surviving disk state (sometimes `killed`, sometimes overwritten to `completed` by an earlier runner pass) keeps matching the health check's query.
- The only working operator stop is `launchctl disable gui/501/com.valor.worker && launchctl bootout gui/501/com.valor.worker` — `bootout` alone does not stick because the LaunchAgent has `KeepAlive=true`. This is undocumented runbook knowledge.

**Desired outcome:**
- `valor-session kill` is a hard guarantee: no further work — Telegram messages, pipeline dispatch, finalize calls — happens on a killed session or its descendants.
- A `killed` session cannot be flipped to any other status without an explicit, documented opt-in.
- The hierarchy health check skips terminal parents at iteration time, regardless of what any index says.
- The `valor-service.sh worker-stop` operator command actually stops the worker (and disables launchd respawn) without requiring `launchctl` knowledge.

## Freshness Check

**Baseline commit:** `036686a7cc73188bd6702e0b159773e9d1130b62`
**Issue filed at:** 2026-04-29T07:50:54Z (≈3.5 hours before plan time — within the same day; live debugging captured in the issue body itself)
**Disposition:** **Unchanged.** Issue claims were verified live during planning by tailing `logs/worker.log` over a 30-minute window; the chain is fully reproducible against the current main commit.

**File:line references re-verified:**
- `models/session_lifecycle.py:217` — `finalize_session()` definition — verified, no `reject_from_terminal` parameter present.
- `models/session_lifecycle.py:415` — `transition_status()` definition — verified, has `reject_from_terminal: bool = True`.
- `agent/session_completion.py:738` — completion-runner "always-finalize" call — verified at the labeled "D6(c) always-finalize" block.
- `agent/session_health.py:1096` — `waiting_parents = list(AgentSession.query.filter(status="waiting_for_children"))` — verified.
- `agent/session_health.py:1156` — `[session-health] Fan-out complete for parent ...` log line + `schedule_pipeline_completion()` call at line 1160 — verified.

**Cited sibling issues/PRs re-checked:**
- **#1006** (Killed sessions resurrect in running index) — closed 2026-04-16. Read its solution sketch: addressed Popoto IndexedField corruption via defensive `srem` and consumer-side terminal-status guards. **Did NOT touch the `waiting_for_children` index path** observed in this bug. So #1006's fix is in scope only for the `running` index, leaving `waiting_for_children` exposed to the same class of bug.
- **#804** (kill uses transition_status() for terminal 'killed') — closed. Confirms kill-side path uses `finalize_session()` correctly.
- **#867** (Race: nudge re-enqueue stomped by finally-block finalize_session) — closed. Different code path, same family of "concurrent finalize stomps prior state" bugs.
- **#822** (Worker restart marking interrupted sessions as completed) — closed. Adjacent class.
- **#898** (always-finalize introduced) — context for the "always-finalize" comment block at session_completion.py:738. We must preserve the always-finalize intent on the happy path; only add a terminal-status guard on the entry side.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --oneline --since=2026-04-29T07:50:54Z -- agent/session_health.py agent/session_completion.py models/session_lifecycle.py` returns empty (verified at plan time).

**Active plans in `docs/plans/` overlapping this area:** None. `agent_session_*` and `*lifecycle*` plans are all completed/landed.

**Notes:** A pre-existing merge conflict on `docs/plans/sdlc-1193.md` was present in the repo at plan time. Owner unknown — left untouched per dirty-state-ownership policy. This plan is being committed in a worktree to avoid that conflict.

## Prior Art

Closely related closed work (focused on adjacent failure modes):

- **#1006** — *Killed sessions resurrect in running index after worker restart or health check* (closed 2026-04-16). Addressed the `running` index. Out of scope for the `waiting_for_children` index path.
- **#804** — *valor-session kill uses transition_status() for terminal 'killed' status* (closed). Fixed the kill path to use `finalize_session()`.
- **#867** — *Race: nudge re-enqueue stomped by worker finally-block finalize_session()*. Mitigated by CAS — but CAS only enforces "in-memory == on-disk", not "on-disk is non-terminal."
- **#822** — *Worker restart incorrectly kills pending sessions and marks interrupted sessions as completed*. Adjacent timing class.
- **#1205** — *Drafter loop spams Telegram with redundant status updates* (open). Compounds user-visible damage; orthogonal cause.

No prior attempt has fixed the receiver side of `finalize_session()`, nor added a terminal-status guard at the `_agent_session_hierarchy_health_check()` iteration site. This is genuinely new ground.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| #1006 | Defensive `srem` in `finalize_session()` + consumer-side terminal guards in startup recovery and health-check pickup | Only covered the `running` index. The `waiting_for_children` index path used by `_agent_session_hierarchy_health_check` was not protected. |
| #804 | Routed `valor-session kill` through `finalize_session()` | Correctly writes `killed`. But left the receiving end (`finalize_session()`'s acceptance of any terminal target, and the runner-entry sites) without a guard. |
| #867 | CAS check in `finalize_session()` (in-memory vs on-disk status) | CAS only catches "I think it's X but it's actually Y" — it does NOT catch "I think it's killed and it really is killed but I want to overwrite it with completed anyway." |

**Root cause pattern:** Every prior fix patched a specific symptom (kill path, `running` index, race window) without enforcing the underlying invariant: **"once terminal, always terminal — unless the caller has explicitly documented why they need to re-classify."** The asymmetry between `transition_status()` (which has `reject_from_terminal=True`) and `finalize_session()` (which doesn't) is the load-bearing gap.

## Architectural Impact

- **New parameter:** `finalize_session(reject_from_terminal=True)` matches the existing `transition_status` parameter — no new concept, just symmetry.
- **Interface changes:** All ~15 `finalize_session()` callers must be reviewed; most should accept the new default behavior, the `_deliver_pipeline_completion` and similar pipeline-progression callers must additionally `try: finalize_session(...) except StatusConflictError: log_and_skip`.
- **Coupling:** Reduces coupling between the lifecycle module and the kill semantic — anyone who knows "this session is killed" no longer has to manually check before every finalize call.
- **Data ownership:** No change. AgentSession remains owned by Popoto.
- **Reversibility:** Trivial — the new parameter has a default-true value that preserves the old permissive behavior at any individual call site by passing `reject_from_terminal=False`.

## Appetite

**Size:** Medium

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 1-2 (confirmation on Fix C scope, depending on root-cause finding)
- Review rounds: 1 (focused on the audit-pass changes)

Medium because the **fix surface is three-layered** (runner-entry guard, lifecycle-API guard, index-staleness investigation) and the audit pass over ~15 `finalize_session()` callers requires careful per-site judgment. Not Small (more than a single targeted patch). Not Large (no architectural redesign).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker stopped during testing | `pgrep -af 'python.*-m worker' \| grep -v grep \| wc -l` returns 0 | Avoid live-spam during integration test of kill behavior |
| Redis available locally | `redis-cli ping` returns `PONG` | Popoto-backed AgentSession tests need Redis |
| `tests/unit/test_session_lifecycle.py` runs green at HEAD | `pytest tests/unit/test_session_lifecycle.py -q` exit 0 | Baseline for regression detection |

## Solution

### Key Elements

- **`reject_from_terminal` parameter on `finalize_session()`** — mirrors the existing parameter on `transition_status()`. Default `True` means terminal→terminal raises `StatusConflictError`. Callers with legitimate re-classification needs (rare) opt out explicitly.
- **Terminal-status guard at hierarchy health check** — `_agent_session_hierarchy_health_check()` re-reads each parent's hash status before invoking the completion runner; skips any parent whose status is in `TERMINAL_STATUSES`. This is defense against any source of stale-index lookups.
- **Terminal-status guard at runner entry** — `schedule_pipeline_completion()` (or `_deliver_pipeline_completion()`) checks the parent's status as its first action; bails early without queuing any Telegram message if the parent is terminal-and-not-`completed`.
- **Caller audit pass** — every `finalize_session()` call site is reviewed; pipeline-progression callers (completion-runner, transcript-finalizer, executor-guard) wrap the call in `try/except StatusConflictError` and log at INFO; any caller with a documented re-classification need passes `reject_from_terminal=False`.
- **Index-staleness investigation** — figure out why a `killed` parent matches `query.filter(status="waiting_for_children")`. If it is index corruption (analogous to #1006), fix at the Popoto layer. If it is a lazy-load timing artifact, fix at the query-call site by re-reading the hash.
- **Operator runbook fix** — `./scripts/valor-service.sh worker-stop` should `launchctl disable` + `launchctl bootout` so a single command actually stops the worker. Add `worker-disable` / `worker-enable` commands to make the disabled state explicit.

### Flow

**Operator runs `valor-session kill <id>`** → kill writes `status=killed` via `finalize_session()` (existing path, unchanged).

**5 minutes later, hierarchy health check fires** → loops over `query.filter(status="waiting_for_children")` candidates → for each, **re-reads the candidate's status** → if status is in `TERMINAL_STATUSES`, log `[session-health] skipping terminal parent {id} (status={status})` and continue → no runner invocation → no Telegram message → no overwrite of `killed`.

**Defensive belt-and-suspenders** → if a runner *does* somehow get invoked on a terminal parent → `schedule_pipeline_completion()` first-line check rejects → if even that slips → `finalize_session(completed)` raises `StatusConflictError` because parent is `killed` and `reject_from_terminal=True` → the caller's `try/except StatusConflictError` logs at INFO and skips.

### Technical Approach

**Fix A — Lifecycle API guard (`models/session_lifecycle.py`):**
Add `reject_from_terminal: bool = True` to `finalize_session()`. Insert the check *after* the existing idempotency check (which short-circuits target-equals-current). New behavior:
```python
# After the existing idempotency check:
if reject_from_terminal and current_status in TERMINAL_STATUSES and current_status != status:
    raise StatusConflictError(
        session_id=session_id,
        expected_status=current_status,
        actual_status=status,  # what the caller wanted
        reason=(
            f"finalize_session({status!r}) blocked: session already terminal "
            f"({current_status!r}). Pass reject_from_terminal=False if intentional."
        ),
    )
```

**Fix B — Hierarchy health check guard (`agent/session_health.py:1096`):**
Inside the `for parent in waiting_parents:` loop, add as first line:
```python
# Re-read the hash status: index entries can be stale (see #1006-class bugs).
fresh = get_authoritative_session(getattr(parent, "session_id", None))
if fresh is not None and getattr(fresh, "status", None) in _TERMINAL_STATUSES:
    logger.info(
        "[session-health] Skipping terminal parent %s (status=%s) — index entry stale",
        parent.agent_session_id,
        fresh.status,
    )
    continue
```
This re-uses `get_authoritative_session` (already imported and used elsewhere in `session_lifecycle.py`).

**Fix C — Runner entry guard (`agent/session_completion.py`):**
At the top of `_deliver_pipeline_completion()` (and any sibling entry points such as `schedule_pipeline_completion()`), add a parent-status check before any drafting or queuing happens:
```python
parent_status = getattr(parent, "status", None)
if parent_status in TERMINAL_STATUSES and parent_status != "completed":
    logger.info(
        "[completion-runner] Skipping pipeline completion for %s — parent terminal (status=%s)",
        getattr(parent, "agent_session_id", "?"),
        parent_status,
    )
    return
```

**Implementation Note (B1 — ordering, blocker resolution):** The runner-entry terminal-status guard MUST run **before** the `pipeline_complete_pending` CAS lock acquisition at `agent/session_completion.py:499` (`POPOTO_REDIS_DB.set(_pipeline_complete_lock_key(parent_id), "1", nx=True, ex=60)`). The correct insertion site is between the existing `parent_id` validation (current line 488–490) and the CAS lock block (current lines 492–513). Concretely, the guard goes immediately after:

```python
if not parent_id:
    logger.warning("[completion-runner] Missing parent_id; skipping")
    return
```

and *before* the `# CAS lock — Race 1 ...` comment block. This ordering is load-bearing: a terminal parent must bail out *before* taking the lock so that (a) a killed parent never blocks lock acquisition for healthy work on an unrelated session, and (b) no `pipeline_complete_pending:{killed_parent_id}` Redis key is ever written for a dead session, leaving the lock keyspace clean. Place this *before* any `send_cb()` call, any drafting, any outbox queuing, and any lock acquisition.

**Fix D — Caller audit pass (cross-module):**
Enumerate every `finalize_session()` call site:
```bash
grep -rn "finalize_session(" agent/ models/ bridge/ 2>&1 | grep -v "^Binary\|test_\|/tests/"
```
For each site, classify into one of:
- **No change** — caller is on a path that only operates on non-terminal sessions.
- **Catch-and-log** — wrap in `try/except StatusConflictError` and log at INFO. Pipeline-progression callers belong here: `agent/session_completion.py:738`, `agent/session_health.py:355,723,860,881,1010`, `bridge/session_transcript.py:312`, `bridge/telegram_bridge.py:1684`, `agent/session_executor.py:658`.
- **Opt out** — pass `reject_from_terminal=False` with a docstring/comment explaining the legitimate need (e.g., escalating `abandoned`→`failed` on timeout, if any such site exists). Expected count: 0–1.

**Implementation Note (Q1 — log level decision):** Catch-and-log sites use **`logger.info(...)`** (NOT `WARNING` or `ERROR`). Once the new guards in Fix B (health-check) and Fix C (runner entry) are in place, a `StatusConflictError` raised from `finalize_session(reject_from_terminal=True)` represents the *expected, correct, defense-in-depth outcome* of a kill racing a routine pipeline-progression call — not an alarm condition. Reserving `WARNING`/`ERROR` for genuine concurrency anomalies keeps signal-to-noise high in `worker.log`. Standard message form:
```python
except StatusConflictError as e:
    logger.info("[%s] Skipping finalize: %s", caller_name, e)
```

**Fix E — DEFERRED to follow-up issue (Q2 decision).** The original plan included an in-scope index-staleness investigation for the `waiting_for_children` index. **Decision:** split this out as a separate follow-up issue, OUT OF SCOPE for this plan. Rationale: Fix B (the re-read guard at the hierarchy health check) operationally masks the staleness — a stale index entry can no longer cause spam because the re-read sees the actual hash status. The underlying corruption (if real) is a Popoto-layer concern in the same class as #1006 and deserves its own dedicated investigation with proper instrumentation. Tracking note in the plan's "Follow-up Issues" section below.

**Fix F — Operator runbook (`scripts/valor-service.sh`) — Q3 decision:**

**Decision:** Keep `worker-stop` semantics unchanged (one-shot stop only — does NOT touch launchd's enabled/disabled state, so launchd's `KeepAlive=true` may auto-respawn the worker as today). Add an EXPLICIT new subcommand `worker-disable` for the stay-down behavior. This preserves backward compatibility for existing callers (scripts, cron jobs, human muscle memory) that rely on `worker-stop` being a transient stop, while giving operators a clearly-named opt-in for "I want this worker to stay down until I say otherwise."

Concretely, the four operator commands are:

| Command | Behavior | When to use |
|---------|----------|-------------|
| `worker-stop` | One-shot `bootout`. Does NOT call `launchctl disable`. launchd's `KeepAlive=true` may relaunch. | Transient stop — most common case; quick restart, debugging, etc. |
| `worker-start` | `launchctl enable` (idempotent), then `bootstrap`. Always re-enables in case `worker-disable` was called previously. | Restart after stop or disable. |
| `worker-disable` | `launchctl disable gui/$UID/com.valor.worker` then `launchctl bootout gui/$UID/com.valor.worker 2>/dev/null`. Worker stays down across launchd respawn until `worker-enable` or `worker-start`. | "I'm killing all the sessions and I do NOT want the worker to come back" — the live-debug scenario from this issue. |
| `worker-enable` | `launchctl enable gui/$UID/com.valor.worker` only. Does not start the worker; pairs with a follow-up `worker-start`. | Re-enable after `worker-disable` without starting immediately. |

Update the CLAUDE.md "Quick Commands" table to add `worker-disable` and `worker-enable` rows. The runbook entry for the kill-is-terminal scenario should reference `worker-disable`, not `worker-stop`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_completion.py:746` already has `except Exception as finalize_err: logger.error(...)` — convert to `except StatusConflictError: logger.info(...)` (specific) plus generic catch fallback.
- [ ] `agent/session_health.py` finalize call sites — wrap each in `try/except StatusConflictError: log_and_continue`.
- [ ] `bridge/session_transcript.py:312` and `bridge/telegram_bridge.py:1684` — same wrapping pattern.

### Empty/Invalid Input Handling
- [ ] `_TERMINAL_STATUSES` containment check must handle `None` status (treat as non-terminal — proceed normally).
- [ ] Hierarchy health check `fresh = get_authoritative_session(...)` returning `None` (session deleted) — current behavior should continue (no guard fires; the legacy code path handles it).

### Error State Rendering
- [ ] When the runner-entry guard fires, NO Telegram message is queued — verified by integration test asserting `r.llen("telegram:outbox:{session_id}") == 0` after the guard logs.
- [ ] When `finalize_session(reject_from_terminal=True)` raises, the caller logs at INFO (not ERROR) — distinguishes "expected guard fire" from "real concurrency bug." Verified by log-level assertion in the catch-and-log sites.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: add `test_finalize_session_rejects_terminal_to_terminal_by_default`, `test_finalize_session_allows_terminal_to_terminal_with_opt_out`, `test_finalize_session_idempotent_on_same_terminal_state` (already covered, verify still green).
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — REVIEW: any test that relies on terminal-overwrite must declare intent via `reject_from_terminal=False` or be updated to expect `StatusConflictError`.
- [ ] `tests/unit/test_kill_cascades_to_children.py` — UPDATE: add `test_killed_parent_survives_child_completion` — kill parent, simulate child completion firing completion-runner, assert parent stays `killed` and no message reaches outbox.
- [ ] `tests/unit/test_valor_session_kill.py` — UPDATE: add `test_kill_prevents_replacement_dispatch` — kill a child mid-pipeline, advance the parent's progression, assert no new dev session enters the queue.
- [ ] `tests/unit/test_agent_session_scheduler_kill.py` — REVIEW: ensure scheduler-side kill exhibits identical behavior.
- [ ] **NEW** `tests/integration/test_kill_is_terminal.py` — CREATE: end-to-end repro. Kill parent → trigger hierarchy health check → assert `_TERMINAL_STATUSES` guard logs "Skipping terminal parent" → assert outbox is empty → assert parent's hash status remains `killed`.

## Rabbit Holes

- **A "force kill that bypasses everything" CLI flag.** Tempting because the current kill is broken, but adding a stronger kill admits defeat. Fix the basic kill instead.
- **Re-litigating issue #898's "always-finalize" intent.** The always-finalize call in `session_completion.py:738` was added intentionally to ensure routine pipeline progression reaches a terminal state. Don't remove it — make it gracefully respect prior terminal states.
- **Refactoring the entire status-transition module.** A single new parameter and a few caller updates is the minimum-viable fix. A full lifecycle redesign is a separate project; this plan does not attempt it.
- **Trying to make `query.filter(status=X)` perfectly index-consistent.** Index corruption is real and #1006 already addressed one path. Adding the re-read guard at Fix B is defense-in-depth — the underlying corruption is a separate investigation.
- **Adding bridge-side dedup to suppress the spam visually.** That is #1205's job (the drafter's "read the room" pre-send pass). Don't double-dip.

## Risks

### Risk 1: A legitimate caller relies on terminal→terminal overwrite

**Impact:** A code path that intentionally re-classifies a terminal session (e.g., escalating `abandoned`→`failed` after a timeout) starts raising `StatusConflictError`, breaking some background reclassification job.
**Mitigation:** The audit pass (Fix D) is the front-line mitigation — every existing caller is reviewed and explicitly classified. Any caller with a documented re-classification need passes `reject_from_terminal=False`. The new error message includes the opt-out instruction (`Pass reject_from_terminal=False if intentional`).

### Risk 2: `get_authoritative_session()` adds latency to the hierarchy health check

**Impact:** The hierarchy check runs every 5 min over potentially many parents; an extra Redis round-trip per parent is non-zero overhead.
**Mitigation:** The hierarchy check already does `parent.get_children()` (a Redis query) per parent — adding one more is in the same order of magnitude. Measure on a project with 100+ historical parents; if measurable, batch the status reads via `redis.pipeline()`.

### Risk 3: The runner-entry guard accidentally suppresses legitimate completions

**Impact:** A parent that legitimately reached `completed` should still go through the runner once to deliver its summary. If the guard's logic is too aggressive (e.g., `parent_status in TERMINAL_STATUSES`), it suppresses everything including `completed`.
**Mitigation:** Guard is `parent_status in TERMINAL_STATUSES and parent_status != "completed"` — `completed` parents are explicitly allowed to pass through (idempotency at `finalize_session` level handles re-finalize). Add a unit test asserting a `completed` parent does NOT trip the guard.

### Risk 4: Operator runbook change breaks an unrelated `valor-service.sh worker-stop` caller

**Impact:** Other scripts or human operators who run `worker-stop` expect the worker to come back via `worker-start` and rely on launchd's `KeepAlive=true` for transient stops. If we change `worker-stop` semantics, those callers break.
**Mitigation (Q3 resolution):** **`worker-stop` semantics are unchanged** — it remains a one-shot `bootout` that does not touch launchd's enabled/disabled state. The new "stay down across respawn" behavior lives in a *new* `worker-disable` subcommand, with a paired `worker-enable`. Existing callers see no behavior change. As a defensive measure, `worker-start` is updated to call `launchctl enable` (idempotent) before `bootstrap` so it correctly recovers from a prior `worker-disable`. Smoke tests cover both paths (see Step 5 verification).

## Race Conditions

### Race 1: Kill writes `killed` while runner is mid-flight

**Location:** `agent/session_completion.py` `schedule_pipeline_completion` ↔ `models/session_lifecycle.py` `finalize_session`.
**Trigger:** Operator runs `valor-session kill` while the hierarchy health check is mid-iteration — the runner has read the parent into memory (status `waiting_for_children`) and is about to draft + queue the message. Kill writes `killed` to disk concurrently.
**Data prerequisite:** The runner's in-memory snapshot is from before the kill; the on-disk status is `killed`.
**State prerequisite:** The runner has not yet called `finalize_session()`; the message has not yet been queued.
**Mitigation:** Fix C (runner entry guard) re-reads parent status at the top of `schedule_pipeline_completion`. Even if the parent was loaded as `waiting_for_children`, the re-read sees `killed` and bails. Belt-and-suspenders: Fix A (`finalize_session(reject_from_terminal=True)`) catches it as a last line of defense.

### Race 2: Multiple workers (none today, but plan for the future)

**Location:** Same path as above; the assumption breaks if more than one worker is running.
**Trigger:** Two workers both pick up the same parent in their hierarchy checks (theoretically possible if the scan is not exclusive).
**Data prerequisite:** Both workers see the parent as `waiting_for_children`.
**State prerequisite:** Multi-worker deployment.
**Mitigation:** Existing CAS in `finalize_session` already prevents both writers from succeeding. Plus the message queuing is idempotent at the relay level (deduped via `msg_id`). No additional work in this plan; flag for follow-up if multi-worker becomes real.

## No-Gos (Out of Scope)

- **Drafter-side dedup of redundant Telegram messages.** That is `#1205`'s scope.
- **Redesign of the AgentSession lifecycle state machine.** The existing terminal/non-terminal split is sound; we're just enforcing it consistently.
- **A new "force-kill" CLI flag.** Listed in Rabbit Holes.
- **Investigation of cross-machine session ownership and what happens if the killing operator is on a different machine than the running worker.** Single-machine ownership is enforced elsewhere (`docs/features/single-machine-ownership.md`).
- **Auto-cleanup of historical killed sessions.** This plan changes future behavior; it does not retroactively transition sessions that were misfiled in the past.

## Update System

- **`scripts/valor-service.sh`** worker-stop / worker-start / worker-disable / worker-enable updates need to propagate via `/update`.
- The launchd plist (`~/Library/LaunchAgents/com.valor.worker.plist`) is per-machine; if the plist is regenerated by an installer (`./scripts/install_*.sh`), confirm the regeneration preserves the disabled state until explicitly re-enabled. If not, document in CLAUDE.md.
- No new env vars, secrets, or external dependencies introduced.
- New machines pick up the lifecycle-module fix automatically on next worker restart.

## Agent Integration

No agent integration changes required — `valor-session` CLI is unchanged from the operator's perspective. The behavior change is internal to the lifecycle module and the worker's hierarchy health check. The agent itself never directly calls `finalize_session()`; it goes through the worker.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` — document the new `reject_from_terminal` parameter and the "killed is strictly terminal" invariant.
- [ ] Update `docs/features/bridge-self-healing.md` — note that the hierarchy health check skips terminal parents.
- [ ] Update `docs/features/bridge-worker-architecture.md` — note the new operator-stop semantics (`worker-stop` disables launchd auto-respawn).

### External Documentation Site
- N/A (no Sphinx/MkDocs site for this repo).

### Inline Documentation
- [ ] Update `models/session_lifecycle.py` `finalize_session()` docstring with the new parameter, plus a note explaining why `transition_status()` and `finalize_session()` now have symmetric guards.
- [ ] Update `agent/session_health.py:1043` `_agent_session_hierarchy_health_check()` docstring to reflect the new skip-terminal-parents behavior.
- [ ] Update CLAUDE.md "Quick Commands" entry for `worker-stop` to mention launchd disable behavior.

## Success Criteria

- [ ] `finalize_session(killed_session, "completed")` raises `StatusConflictError` by default.
- [ ] `finalize_session(killed_session, "completed", reject_from_terminal=False)` succeeds (escape-hatch verified).
- [ ] After `valor-session kill <id>` on a parent, the next `_agent_session_hierarchy_health_check()` cycle logs `[session-health] Skipping terminal parent` and queues no Telegram messages.
- [ ] Audit-pass log: every `finalize_session()` call site reviewed and either no-changed, catch-and-log-wrapped, or opted out with an inline comment.
- [ ] Integration test `test_kill_is_terminal.py` passes: kill parent, drive hierarchy check, assert outbox empty, assert parent stays `killed`.
- [ ] `./scripts/valor-service.sh worker-stop` actually stops the worker without manual `launchctl disable` follow-up; `worker-start` re-enables and starts.
- [ ] 30-minute observation window post-fix on a real `PM:` chat: zero unexpected status updates after kill.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No stale xfails introduced.

## Team Orchestration

### Team Members

- **Builder (lifecycle)**
  - Name: `lifecycle-builder`
  - Role: Add `reject_from_terminal` to `finalize_session()` + tests in `test_session_lifecycle.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (health-check + runner)**
  - Name: `runner-guard-builder`
  - Role: Terminal-status guards in `agent/session_health.py` (Fix B) and `agent/session_completion.py` (Fix C).
  - Agent Type: builder
  - Resume: true

- **Builder (caller audit)**
  - Name: `audit-builder`
  - Role: Per-site review of `finalize_session()` callers; wrap with `try/except StatusConflictError` or pass opt-out.
  - Agent Type: builder
  - Resume: true

- **Builder (operator runbook)**
  - Name: `runbook-builder`
  - Role: Update `scripts/valor-service.sh` worker-stop/start/disable/enable; update CLAUDE.md.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `test-author`
  - Role: Write `tests/integration/test_kill_is_terminal.py`; extend existing kill-related unit tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `lead-validator`
  - Role: Verify all success criteria on the merged feature branch; run the 30-min observation window.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Update three feature docs + CLAUDE.md + docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Lifecycle API guard (Fix A)
- **Task ID**: build-lifecycle
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py` (extend), `tests/unit/test_session_lifecycle_consolidation.py` (review)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `reject_from_terminal: bool = True` param to `finalize_session()` in `models/session_lifecycle.py`.
- Insert guard after the existing idempotency check; reuse `StatusConflictError` with the prescribed message.
- Update docstring with the new parameter and rationale.

### 2. Health-check + runner entry guards (Fix B + Fix C)
- **Task ID**: build-runner-guard
- **Depends On**: build-lifecycle
- **Validates**: `tests/unit/test_kill_cascades_to_children.py` (extend), new `tests/integration/test_kill_is_terminal.py`.
- **Assigned To**: runner-guard-builder
- **Agent Type**: builder
- **Parallel**: false (touches files that the audit task also touches)
- In `agent/session_health.py:1096`, add `get_authoritative_session()` re-read with terminal-status skip + INFO log.
- In `agent/session_completion.py`, add first-line guard in `schedule_pipeline_completion()` (and `_deliver_pipeline_completion()` if separate) — bail before any drafting or `send_cb()` call when parent is terminal-not-completed.

### 3. Caller audit pass (Fix D)
- **Task ID**: build-audit
- **Depends On**: build-lifecycle
- **Validates**: existing tests stay green; no `finalize_session()` call site emits an unhandled `StatusConflictError`.
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true (with build-runner-guard if no file overlap)
- Enumerate every `finalize_session()` call site via `grep -rn`.
- For each: classify (no-change | catch-and-log | opt-out) and apply the right transformation.
- Each catch-and-log site: `except StatusConflictError as e: logger.info("[caller-name] Skipping finalize: %s", e)` — use INFO not ERROR to distinguish expected from unexpected.

### 4. Test authoring
- **Task ID**: build-tests
- **Depends On**: build-lifecycle, build-runner-guard
- **Validates**: itself — pytest runs.
- **Assigned To**: test-author
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_kill_is_terminal.py` using **direct function invocation** of `_agent_session_hierarchy_health_check()` and `_deliver_pipeline_completion()` — see "Implementation Note (Q4 — integration test strategy)" below.
- Extend `tests/unit/test_session_lifecycle.py` with the three new lifecycle tests listed in Test Impact.
- Extend `tests/unit/test_kill_cascades_to_children.py` with `test_killed_parent_survives_child_completion`.

**Implementation Note (Q4 — integration test strategy decision):** The integration test uses **direct function invocation only** — no worker-loop simulation, no launchd respawn dance, no real 5-minute scheduler ticks. Rationale: simulating the full worker loop is gold-plating for a defense-in-depth fix; the guards live at well-defined function boundaries (`_agent_session_hierarchy_health_check` iteration, `_deliver_pipeline_completion` entry, `finalize_session` body), so calling those functions directly with crafted AgentSession state covers the regression contract deterministically. The launchd-respawn-class bugs that worker-loop simulation would catch are orthogonal to this fix and would belong in a separate operational-resilience test suite. **The live verification target is the haunted parent `tg_valor_-1003449100931_754`** in PM: Valor (chat_id `-1003449100931`, agent_session_id `04a1b7ba207449a98169171c5e44513a`), captured in this issue's "Live Debugging Update" section — the validator step (Step 6) reproduces the kill-then-wait scenario against that real session to confirm the fix lands as intended in production conditions.

### 5. Operator runbook (Fix F)
- **Task ID**: build-runbook
- **Depends On**: none (independent of the lifecycle work)
- **Validates**: smoke tests:
  - `worker-stop && worker-start && pgrep -af 'python.*-m worker' | wc -l` returns ≥ 1 (one-shot stop + restart).
  - `worker-disable && launchctl print-disabled gui/$UID | grep -q 'com.valor.worker.*=> disabled'` (disable sticks).
  - `worker-enable && worker-start && pgrep -af 'python.*-m worker' | wc -l` returns ≥ 1 (re-enable + start).
- **Assigned To**: runbook-builder
- **Agent Type**: builder
- **Parallel**: true
- Keep `worker-stop` behavior unchanged (one-shot `bootout`, no launchd disable) — preserves backward compatibility.
- Update `worker-start` to call `launchctl enable` (idempotent) before `bootstrap` so it works correctly after a prior `worker-disable`.
- Add a NEW `worker-disable` subcommand (`launchctl disable` + `bootout`) for the stay-down case.
- Add a NEW `worker-enable` subcommand (`launchctl enable` only; does not start).
- Update CLAUDE.md Quick Commands table to add `worker-disable` and `worker-enable` rows.

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-lifecycle, build-runner-guard, build-audit, build-tests, build-runbook, document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows.
- **Live verification (Q4 target):** Reproduce the live-debug scenario against the haunted parent `tg_valor_-1003449100931_754` (PM: Valor chat, agent_session_id `04a1b7ba207449a98169171c5e44513a`) — see this plan's Problem section and the issue's "Live Debugging Update". Procedure: ensure the parent is in `status=killed`, restart the worker, wait for two hierarchy-check cycles (10 minutes), confirm zero Telegram messages reach the chat and the parent's status remains `killed`.
- Confirm CAS error rate in worker.log is zero post-fix.
- Confirm the audit pass left no `finalize_session()` call site without a try/except for `StatusConflictError` (unless explicitly classified no-change).

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-lifecycle, build-runner-guard, build-audit, build-runbook
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false (after the code lands)
- Update `docs/features/session-lifecycle.md`, `docs/features/bridge-self-healing.md`, `docs/features/bridge-worker-architecture.md`.
- Update CLAUDE.md Quick Commands.
- Update inline docstrings on `finalize_session()` and `_agent_session_hierarchy_health_check()`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lifecycle test | `pytest tests/unit/test_session_lifecycle.py -q -k reject_from_terminal` | exit code 0 |
| Kill cascade test | `pytest tests/unit/test_kill_cascades_to_children.py -q -k killed_parent_survives` | exit code 0 |
| Integration test | `pytest tests/integration/test_kill_is_terminal.py -q` | exit code 0 |
| No unhandled finalize raises | `grep -rn "finalize_session(" agent/ models/ bridge/ \| grep -v 'try\|except\|#'` matches the audit checklist | manual review pass |
| Worker-stop is one-shot (semantics preserved) | `./scripts/valor-service.sh worker-stop && launchctl print-disabled gui/$UID \| grep 'com.valor.worker'` | NOT marked `=> disabled` (transient stop only) |
| Worker-disable sticks | `./scripts/valor-service.sh worker-disable && launchctl print-disabled gui/$UID \| grep -q 'com.valor.worker.*=> disabled'` | exit code 0 |
| Worker-start re-enables and starts | `./scripts/valor-service.sh worker-disable && ./scripts/valor-service.sh worker-start && pgrep -af 'python.*-m worker' \| wc -l` | output > 0 |
| Worker-enable alone does not start | `./scripts/valor-service.sh worker-disable && ./scripts/valor-service.sh worker-enable && pgrep -af 'python.*-m worker' \| wc -l` | output == 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Follow-up Issues

The following work is intentionally **out of scope** for this plan but should be filed as a separate GitHub issue once this plan ships:

- **Index-staleness investigation for `waiting_for_children` (Q2 deferral, originally Fix E in earlier drafts of this plan).** Determine why a `killed` parent matches `AgentSession.query.filter(status="waiting_for_children")` after kill. Likely candidates: Popoto IndexedField corruption (analogous to #1006, which only fixed the `running` index path), or a lazy-load timing artifact where the query returns a cached object whose in-memory status disagrees with the hash. Operational mitigation already in place via Fix B (the re-read guard at the hierarchy health check); this follow-up addresses the underlying corruption itself. Suggested instrumentation: snapshot `r.smembers("agent_session:status:waiting_for_children")` before and after kill, diff the membership, and compare to each session's `r.hget(key, "status")`. Title suggestion: "Investigate `waiting_for_children` index staleness for killed parents (follow-up to #1208)". This issue will be filed *after* the kill-is-terminal plan ships, not as part of this build.
