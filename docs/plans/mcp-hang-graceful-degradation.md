---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-16
refreshed: 2026-06-19
refreshed_against: 0ddcd3f4a6e911aef5b92f6b30459cd81cf4dc69
tracking: https://github.com/tomcounsell/ai/issues/1711
last_comment_id:
revision_applied: true
---

# MCP Hang Graceful Degradation (steering on tool_timeout recovery)

## Problem

When an MCP tool call (e.g. `mcp__claude_ai_Notion__notion-fetch`) hangs past its 120 s
budget, the per-tool timeout sub-loop (`_agent_session_tool_timeout_loop` in
`agent/session_health.py`, from issue #1270) kills the session and re-queues it as
`pending` with `recovery_attempts += 1`. The re-queued session is handed the **same**
turn input it had before, so the model re-issues the same hanging tool call, wedges a
second time, hits `MAX_RECOVERY_ATTEMPTS=2`, and is finalized as `failed`. The user's
original message is never answered.

**Current behavior:**
The `tool_timeout` recovery path (`_apply_recovery_transition` → requeue branch at
`agent/session_health.py:1558-1590`) re-queues with no signal about *why* the session
was killed. The model has no way to know the tool is unavailable, so it repeats the call.

Real example (session `tg_cyndra_8762685703_10818`, 2026-06-16 05:29–05:30 UTC):
- First run: `mcp__claude_ai_Notion__notion-fetch` hung at 120 s → killed → re-queued
- Second run: same tool hung again → `failed` permanently
- Original message: never answered

**Desired outcome (two layers):**

1. **Advisory steering (attempt-1 requeue).** On the first `tool_timeout` recovery, before
   re-queuing, **prepend** a steering message (to the FRONT of the steering queue) naming the
   timed-out tool and instructing the model to skip it and degrade gracefully. The session
   delivers a degraded-but-real response ("I couldn't reach Notion; here's what I can answer
   without it") on the re-pickup turn.
2. **Deterministic floor (terminal/second wedge).** Advisory steering may be ignored — the
   model can re-issue the same tool call and wedge again. On the **terminal** `tool_timeout`
   recovery (recovery_attempts ≥ MAX → would otherwise finalize `failed`), the worker delivers
   a **canned, user-facing degraded message** to the originating chat ("I couldn't complete
   that because the {tool} service didn't respond — try again shortly") **before** finalizing
   `failed`. Delivery routes through the session's **actual output handler** (the
   `OutputHandler` protocol — telegram relay, email relay, or file), NOT a hard-coded Telegram
   outbox key, so the guarantee holds for telegram-, email-, and local/file-originated
   sessions alike. The user is never left with silent failure.

This two-layer design follows the precedent of PR #892 (see Prior Art): on a closely-related
recovery path, advisory-only steering proved insufficient and required a deterministic
last-resort gate to guarantee a user-visible outcome. We adopt the *principle* — advisory
plus a deterministic floor — without claiming the two mechanisms are identical.

## Freshness Check

**Original baseline commit:** `fc1f73c88a5120496a5f26972b9085c2870c818e` (2026-06-16)
**Re-verification baseline commit:** `0ddcd3f4a6e911aef5b92f6b30459cd81cf4dc69` (HEAD, 2026-06-19)
**Issue filed at:** 2026-06-16T05:40:08Z
**Disposition:** Minor drift — line numbers shifted; one stated rationale corrected. All structural claims hold; the fix is unchanged.

**Why re-verified:** PR #1728 (commit `2efb58ce`, "feat(#1724): recover stalled never_started and mid-run-wedge granite sessions") merged to main on 2026-06-19, **after** this plan's original freshness check. #1724 added **+323 lines** to `agent/session_health.py` (and +24 to `models/agent_session.py`), shifting every line anchor in this plan. The re-verification below re-pins all anchors against HEAD `0ddcd3f4` and confirms #1724 did **not** touch the `tool_timeout` recovery path this plan modifies.

**Did #1724 change the recovery path this plan builds on?** No. `git blame` confirms the `_apply_recovery_transition` requeue/`failed` branches are owned by `e0fd94414` (and the #1537 not-confirmed-dead branch by its own prior commit), **not** `2efb58ce`. #1724 added new *sibling* recovery actors (never-started + mid-run-wedge) and shared predicates elsewhere in the file; it left the `tool_timeout` → `_apply_recovery_transition` flow structurally untouched. This plan stacks cleanly on the merged #1724 — no re-litigation of the #1724 single-source-of-truth recovery-decision design.

**File:line references re-pinned against HEAD `0ddcd3f4`:**
- `_apply_recovery_transition` — now at **`agent/session_health.py:1222`** (was `:1132`, +90).
- The `response_delivered_at` early-return (C1 guard) — now at **`agent/session_health.py:1297`** (was `:1207`, +90). Still `finalize_session(entry, "completed", …)`. Confirmed structurally present.
- The requeue `else:` block — now starts at **`agent/session_health.py:1558`** (`entry.priority = "high"` / `entry.started_at = None` at 1559–1560; was `:1468–1470`, ~+90).
- The OOM-defer sub-path (`exit_returncode == -9` + `_is_memory_tight()`) — now at **`agent/session_health.py:1561–1580`**.
- The shared `transition_status(entry, "pending", …)` — now at **`agent/session_health.py:1589`**.
- The MAX-attempts `failed` branch (`elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:`) — now at **`agent/session_health.py:1514`** (was `:1424`).
- The not-confirmed-dead `failed` branch (`elif not _subprocess_confirmed_dead:`, #1537) — now at **`agent/session_health.py:1529`** (was `:1439`). Still finalizes `failed` on attempt 1 with neither advisory nor notice — the silent-failure path this plan closes. Confirmed present and unchanged.
- `MAX_RECOVERY_ATTEMPTS = 2` — now at **`agent/session_health.py:236`** (was `:232`). Value unchanged.
- The `tool_timeout` recovery call site `_apply_recovery_transition(fresh, …, reason_kind="tool_timeout", …)` — now at **`agent/session_health.py:2608`** (was `:2284`).
- `_resolve_callbacks` is still called within `session_health.py` (e.g. **`:2183`**) — the floor's delivery path reuses an existing import/pattern, as the plan assumes.
- `push_steering_message()` — now at **`models/agent_session.py:1999`** (was `:1918`); `STEERING_QUEUE_MAX = 10` at `:62`; the current append-only body trims `current[-STEERING_QUEUE_MAX:]` at **`:2015`**. `pop_steering_messages()` at `:2022`. The `front=` keyword this plan adds does **not** yet exist — confirmed (the method takes only `text`).
- `current_tool_name` field — now at **`models/agent_session.py:500`** (was `:444`); null-default, populated by the PreToolUse hook, NOT cleared on requeue.
- Steering pop / turn-input replacement — `pop_steering_messages()` at **`agent/session_executor.py:1522`**, `_turn_input = steering_msgs[0]` at **`:1524`**. The popped message still **replaces** the turn input.
- Canonical transport read — `_extra.get("transport")` at **`agent/session_executor.py:1025`** (the D1-corrected pattern the floor must copy; `getattr(entry, "transport", None)` remains a constant-None antipattern).

**Corrected rationale (the one substantive drift):** Several sections (Data Flow §4, Solution "B-B", Technical Approach, Step 1) previously justified placing the advisory injection at the **TOP** of the requeue `else:` block by claiming the OOM-defer sub-path *"saves and defers 120s via `scheduled_at` **without** ever calling `transition_status`."* **Against current main (and per blame, since `e0fd94414` — this predates #1724) that claim is FALSE.** The OOM-defer `if` block (1561–1580) and the normal-save `else` (1581–1588) both **fall through** to a single **unconditional** `transition_status(entry, "pending", …)` at `1589` (at `else:`-block indentation). So injecting *just before* `transition_status` would in fact cover both sub-paths too. **The plan's recommendation (inject at the TOP of the block) is still correct and the safest placement** — it guarantees coverage regardless of any future restructuring of the OOM sub-path and keeps the injection adjacent to the `priority`/`started_at` setup — but the *reason* has been corrected throughout: inject at the top because it is the single point that dominates every requeue sub-path (current and future), **not** because the OOM path skips `transition_status` (it does not). This correction is propagated to Data Flow §4, Solution, Technical Approach, and Step 1 below.

**Cited sibling issues/PRs re-checked:**
- #1270 (per-tool timeout tiers) — the feature this plan extends. Still the live mechanism; `_check_tool_timeout` / `_classify_tool_tier` / `current_tool_name` tracking all present.
- #1724 / PR #1728 (`2efb58ce`) — **merged 2026-06-19.** Reviewed: added never-started + mid-run-wedge recovery actors and shared predicates; did **not** modify the `tool_timeout` requeue/`failed` branches. No conflict; this plan builds on it.
- #1537 (not-confirmed-dead escalation) — the second `failed` branch the floor must cover; present at `:1529`.
- Session-steering design — `docs/features/session-steering.md`; mechanism unchanged.

**Commits on main since the ORIGINAL freshness check (touching referenced files):** `2efb58ce` (#1724/#1728) and `0cd03fdf` (dep bump). `2efb58ce` touched `agent/session_health.py` and `models/agent_session.py` but only shifted line numbers (re-pinned above); the dep bump is irrelevant. No commit altered the recovery branches this plan edits.

**Active plans in `docs/plans/` overlapping this area:** none new. The #1724 plan migrated to `docs/plans/completed/` (`0ddcd3f4`) — no longer an active-plan overlap. Steering-subsystem plans (`parent-child-steering.md`, `summarizer-fallback-steering.md`, `consolidate-steering-docs.md`) touch different call sites; no overlap with the `tool_timeout` recovery path.

**Notes:** Bug confirmed still present by code read against HEAD `0ddcd3f4` (reproduction in a live worker is infeasible — requires a real upstream MCP hang). Both `failed` branches (`:1514`, `:1529`) still finalize without delivering any user-facing notice; the requeue branch still injects no steering. The defect is real and unaddressed on current main.

## Prior Art

- **Issue/PR #1270**: Per-tool timeout tiers — added `_agent_session_tool_timeout_loop`, `_classify_tool_tier`, `current_tool_name` tracking, and the `reason_kind="tool_timeout"` recovery path. This plan adds the missing "tell the model what timed out" step to that path.
- **Session steering (`docs/features/session-steering.md`)**: `push_steering_message()` / `pop_steering_messages()` already exist and are exercised by `tests/integration/test_steering.py`. The worker pops steering at the turn boundary (`session_executor.py:1522`). The steering queue is **FIFO** — `push_steering_message` *appends* (`models/agent_session.py:2011`) and the executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`), re-queuing the remainder at the back. A plain append is therefore **not** sufficient for tool-skip injection (see Risk 4 / Solution): if any steering message is already queued, an append would run that older message first and let the model re-call the hung tool. This plan adds a **prepend** path.
- **PR #892 — "Summarizer fallback: agent self-summary via session steering"** (`docs/plans/summarizer-fallback-steering.md`, merged 2026-04-10, closed #891). This is **precedent for the principle, not a mechanism match.** PR #892 addressed a *different* failure surface — a summarizer/self-draft fallback — and its deterministic gate (`is_narration_only()`, applied to the agent's own already-produced text) is a different mechanism than this plan's tool-timeout floor (a canned notice delivered through the session's output handler on the terminal `failed` branch). What carries over is the **architectural lesson, not the implementation**: on that path, advisory steering alone proved insufficient and a deterministic last-resort gate was required to guarantee a user-visible outcome. **Principle adopted here:** advisory steering ships PLUS a deterministic floor. We do NOT claim equivalence between `is_narration_only()` and the outbox-delivered degraded notice — only that #892 establishes the precedent that advisory-only is not enough.
- No prior closed issue attempted to inject steering on `tool_timeout` recovery specifically. This is the first fix of this specific defect; #892 is cited only as precedent that advisory-only is insufficient, not as a reusable mechanism.

## Research

No relevant external findings — this is a purely internal change to the worker's session-health recovery path and the steering subsystem. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

1. **Entry point**: PreToolUse hook fires when the model invokes `mcp__claude_ai_Notion__notion-fetch`, writing `current_tool_name` and `last_tool_use_at` on the `AgentSession`.
2. **Wedge**: PostToolUse never returns (upstream MCP hang). After 120 s, `_agent_session_tool_timeout_check` (`session_health.py:~2458`) re-reads the session, `_check_tool_timeout(fresh)` returns `(tier="mcp", reason=...)`.
3. **Counter + recovery**: the check bumps `tool_timeout_count_mcp`, INCRs `…:tool_timeouts:mcp`, then calls `_apply_recovery_transition(fresh, reason_kind="tool_timeout", handle, worker_key)` at `session_health.py:2608`.
4. **Recovery transition (attempt 1 → requeue)**: `_apply_recovery_transition` cancels the task, confirms the subprocess is dead, and enters the `else:` requeue branch (`:1558`), which sets `entry.priority="high"` and `entry.started_at=None` (`:1559-1560`), then forks into two sub-paths that **both fall through to a single shared `transition_status(entry, "pending")` at `:1589`**: an **OOM-defer** sub-path (`exit_returncode == -9` + memory-tight, `:1561-1580`) that additionally `save()`s `scheduled_at` to defer 120 s, and the **normal-requeue** sub-path (`:1581-1588`) that just saves `recovery_attempts`. **(NEW injection point A)**: place the advisory prepend at the **TOP of the `else:` block** (`:~1559`, right after `entry.priority`/`started_at` are set and BEFORE the OOM check at `:1561`), guarded by `if reason_kind == "tool_timeout" and tool_name:`. Rationale: the top of the block is the single statement that **dominates every requeue sub-path** (OOM-defer and normal alike, plus any future sub-path), so the tool-skip steering is carried regardless of which sub-path runs. (NOTE: on current main both sub-paths *do* reach the shared `transition_status` at `:1589`, so injecting just before it would also work today — but top-of-block placement is more robust to future restructuring and keeps the injection adjacent to the `priority`/`started_at` setup.) **Prepend** to the FRONT of the queue (NOT a plain append — see B1 / Risk 4) so the tool-skip instruction is the `steering_msgs[0]` the executor consumes on the very next turn.
5. **Re-pickup**: the worker picks the pending session back up. At the turn boundary (`session_executor.py:1522`), `pop_steering_messages()` returns the queue with the tool-skip message first; `_turn_input = steering_msgs[0]` (`session_executor.py:1524`) **replaces** the original message text.
6. **Output**: `build_harness_turn_input(_turn_input, …)` wraps the steering text with context headers and sends it to the brand-new TUI container run (no `--resume`). The model reads "skip tool X, answer without it", produces a degraded response, and delivers it to the user.
7. **Terminal/early wedge → `failed`**: the `failed` outcome is reached from **two** branches, not one:
   - the MAX-attempts branch (`recovery_attempts >= MAX_RECOVERY_ATTEMPTS`, `session_health.py:1514`) when the model ignored the advisory and the tool wedged again; and
   - the **subprocess-not-confirmed-dead branch** (`elif not _subprocess_confirmed_dead:`, `session_health.py:1529`), which finalizes `failed` on **attempt 1** (recovery_attempts already bumped to 1, and `1 < MAX_RECOVERY_ATTEMPTS=2`) when the killed subprocess could not be confirmed dead — this path requeue-escalates straight to `failed` (issue #1537) and today delivers **neither** the advisory steering nor any notice, reproducing the exact silent-failure #1711 closes.
   **(NEW injection point B — deterministic floor, shared by BOTH `failed` branches)**: a single helper `_deliver_tool_timeout_degraded_notice(entry, tool_name)` is invoked from **every** `tool_timeout`→`failed` exit (the `:1514` MAX branch AND the `:1529` not-confirmed-dead branch), BEFORE each `finalize_session(entry, "failed", …)` call. It delivers a canned user-facing degraded message through the session's **actual output handler**. Resolve the transport with the canonical `(getattr(entry, "extra_context", None) or {}).get("transport")` (AgentSession has **no** `transport` field — transport lives in `extra_context`; the `getattr(parent, "transport", None)` at `session_health.py:2182` is a constant-None antipattern this plan must NOT copy; the correct read is the one at `session_executor.py:1024-1025` / `output_handler.py:181-182`), then resolve the handler via `_resolve_callbacks(entry.project_key, transport)` (the same resolver used by `_deliver_pipeline_completion`), which returns the registered telegram-relay or email-relay send callback; if no callback is registered, fall back to `FileOutputHandler` (mirrors `session_executor.py:1031-1040`). The handler's `send()` routes to `telegram:outbox:{session_id}`, `email:outbox:{session_id}`, or the file log as appropriate. Only then does the session finalize `failed`. The user gets a real reply on whatever channel they used, not silence.

**Load-bearing fact**: the container has no `claude --resume` wiring (`session_executor.py:1653-1659`) — every run is a fresh TUI session. The steering message becomes the *entire* turn input, so it must be **self-contained**: it must reference the original request, not merely say "skip the tool". The original request is preserved on the session as `entry.message_text` and must be woven into the steering text.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: one small additive method on `AgentSession` — `push_steering_message(text, front: bool = False)` gains a `front` keyword (default `False` preserves all existing append callers). When `front=True` the message is inserted at index 0 so the executor's `steering_msgs[0]` consumption picks it up first. **Overflow trim is direction-dependent and MUST invert for the front path** (B1): the existing append path trims `current[-STEERING_QUEUE_MAX:]` (keeps the tail, drops from the HEAD) — applying that to a front-insert would discard the just-prepended tool-skip message at index 0, defeating the fix. For `front=True` the trim is `current[:STEERING_QUEUE_MAX]` (keeps the HEAD/index-0 prepended message, drops from the BACK/tail). The branch is on the `front` flag: append → trim tail-keep (`[-MAX:]`), prepend → trim head-keep (`[:MAX]`).
- **Schema:** no new model fields. AgentSession has **no** `transport` field and this plan does **not** add one — transport is read from `extra_context` (`(getattr(entry, "extra_context", None) or {}).get("transport")`). The C2 idempotency guard uses a **Redis SETNX one-shot marker** (`tool_timeout:degraded_sent:{session_id}`), **not** a model field — there is no `degraded_notice_sent_at` field and none is added (zero-schema).
- **Coupling**: `_apply_recovery_transition` gains a read of `entry.current_tool_name` and `entry.message_text` (both already on the model), a `push_steering_message(..., front=True)` call, and (on **both** `failed` branches — `:1514` MAX and `:1529` not-confirmed-dead) a degraded-notice delivery routed through `_resolve_callbacks(entry.project_key, <transport-from-extra_context>)` → registered handler (with `FileOutputHandler` fallback). `session_health.py` already imports `AgentSession` and already calls `_resolve_callbacks` for fan-out completion, so the delivery path reuses an existing import and pattern — no new coupling to a specific channel.
- **Data ownership**: unchanged. The steering queue is owned by `AgentSession`. The degraded notice is owned by whichever `OutputHandler` the session's transport resolves to (telegram relay → `telegram:outbox:{session_id}`, email relay → `email:outbox:{session_id}`, or file log) — the plan does not introduce a new key contract; it reuses the existing per-transport outbox contracts each handler already owns.
- **Reversibility**: trivial — both injections are single guarded blocks; remove them to revert. The `front=` keyword defaults to the prior append behavior.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0 (design committed — advisory steering + deterministic floor; suppression is out of scope)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Runs entirely against the worker's in-process session-health loop and Redis-backed `AgentSession`.

## Solution

### Key Elements

- **Prepend-capable steering** (`push_steering_message(text, front=False)`): add a `front` keyword to the existing `AgentSession.push_steering_message`. `front=True` inserts at index 0 **and inverts the overflow trim to `current[:STEERING_QUEUE_MAX]`** (keeps the index-0 prepended message, drops from the BACK/tail). The existing append path keeps its current `current[-STEERING_QUEUE_MAX:]` trim (drops from the head). **The trim direction MUST follow the insert direction** — using the append-path tail-keep trim for a front insert would drop the just-prepended tool-skip message at index 0 and silently defeat the fix. Default `front=False` is the existing append behavior — no existing caller changes. **This is the B1 fix**: the executor consumes `steering_msgs[0]`, so the tool-skip instruction MUST be at the front to be the next turn's input; a plain append would let any already-queued message run first and re-call the hung tool.
- **Steering composer** (`_compose_tool_timeout_steering`): a small pure helper in `agent/session_health.py` that takes the tool name and the original request text and returns a self-contained steering string. Pure and unit-testable.
- **Advisory injection in the requeue path**: in `_apply_recovery_transition`, when `reason_kind == "tool_timeout"` AND the transition is taking the **requeue (`pending`) branch** (not `failed`/`abandoned`), call `entry.push_steering_message(_compose_tool_timeout_steering(...), front=True)` before `transition_status(entry, "pending", …)`.
- **Deterministic floor on EVERY `tool_timeout`→`failed` exit (B2)**: a single helper `_deliver_tool_timeout_degraded_notice(entry, tool_name)` invoked from **both** `failed` branches when `reason_kind == "tool_timeout"` — the MAX-attempts branch (`recovery_attempts >= MAX_RECOVERY_ATTEMPTS`, `:1514`) **and** the subprocess-not-confirmed-dead branch (`elif not _subprocess_confirmed_dead:`, `:1529`, which finalizes `failed` on attempt 1 and is itself a silent-failure reproduction of #1711). It delivers a canned user-facing degraded message through the session's **actual output handler** — resolved by first reading the transport with the canonical `(getattr(entry, "extra_context", None) or {}).get("transport")` (NOT `getattr(entry, "transport", None)`, which is constant-None — AgentSession has no `transport` field) and then calling `_resolve_callbacks(entry.project_key, transport)` with a `FileOutputHandler` fallback when no callback is registered — **before** each `finalize_session(entry, "failed", …)`. The handler's `send()` routes to the telegram outbox, email outbox, or file log per the session's transport; the floor is channel-agnostic and never hard-codes `telegram:outbox:`. Guarded so a delivery failure logs WARNING and still lets the finalize proceed. **Idempotency (C2):** the helper writes a Redis `SETNX tool_timeout:degraded_sent:{session_id}` one-shot marker (with a short TTL) as its first step and returns early if already set, so a crash-and-retry of the health loop — or the two `failed` branches both firing across passes — cannot double-deliver. No model field is used (there is no `degraded_notice_sent_at` field; the SETNX marker is the sole guard — see C2 / Risk 5).
- **Tool-name capture**: read `current_tool_name` from `entry` at recovery time (it is not cleared on requeue, but capture it into a local before any save to be safe against concurrent clears). Used by both the advisory composer and the deterministic notice.

### Flow

**Attempt 1 (advisory):** MCP tool hangs → tool-timeout sub-loop fires → `_apply_recovery_transition(reason_kind="tool_timeout")` → **compose + PREPEND steering naming the tool (`front=True`)** → requeue to `pending` → worker re-picks → `pop_steering_messages()[0]` is the tool-skip message → model skips the tool, answers degraded → response delivered to user.

**Any `tool_timeout`→`failed` exit (deterministic floor):** the floor fires on **every** path that finalizes `failed` for a `tool_timeout`, not just the MAX-attempts case:
- **Attempt 2 / MAX branch** (`:1514`): model ignored the advisory, tool wedges again → recovery_attempts hits MAX → `failed`.
- **Attempt 1 / not-confirmed-dead branch** (`:1529`): the killed subprocess could not be confirmed dead → escalate straight to `failed` (issue #1537) on attempt 1, before the advisory ever gets a second turn.
In **both** cases → **`_deliver_tool_timeout_degraded_notice(entry, tool_name)` writes the canned degraded notice through the resolved handler (user sees it)** → then `finalize_session("failed")`. The user always gets a reply; `failed` is now a clean terminal state on every tool_timeout path, not a silent one. The Redis SETNX marker ensures only one notice is delivered even if both branches are reached across health-loop passes.

### Technical Approach

- **PREPEND, do not append (B1) — and invert the trim direction.** The executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`) and re-queues the rest at the back. `push_steering_message` appends today (`models/agent_session.py:2011`) and, on overflow, trims `current[-STEERING_QUEUE_MAX:]` (`models/agent_session.py:2015`) — which keeps the tail and **drops from the head**. If any steering message is already queued when the tool wedges, a plain append would let that older message run first while the tool-skip waits — and on a fresh-TUI re-pickup the model would just re-call the hung tool. The fix adds `front: bool = False` to `push_steering_message`; the advisory injection calls it with `front=True` so the tool-skip instruction is index 0. **The overflow trim MUST branch on `front`:** for `front=True`, insert at index 0 and trim `current[:STEERING_QUEUE_MAX]` (keep the HEAD/index-0 prepended message, drop from the BACK). Reusing the existing tail-keep trim (`[-MAX:]`) on a front insert would discard the very tool-skip message just prepended at index 0 — the inverted bug this fix exists to prevent. The append path (`front=False`) keeps its existing head-drop trim unchanged.
- **Advisory injection only on the requeue branch, at the TOP of the block (B-B).** Do NOT push steering when the transition resolves to `failed` or `abandoned` — a steering message on a terminal record is dead weight (the deterministic floor handles `failed`). Place the injection at the **top of the `else:` requeue block** (`session_health.py:~1559`, right after `entry.priority="high"` / `entry.started_at=None`, BEFORE the OOM-defer check at `:1561`), guarded by `if reason_kind == "tool_timeout" and tool_name:`. **Why the top, not immediately-before `transition_status` (`:1589`):** the top of the block is the single statement that **dominates every requeue sub-path**. The `else:` block forks into an OOM-defer sub-path (`:1561-1580`, `exit_returncode == -9` + memory-tight) and a normal-save sub-path (`:1581-1588`); on current main **both** fall through to the shared `transition_status` at `:1589`, so injecting just before it would also work today — but top-of-block placement is robust to any future restructuring of the sub-paths (e.g. if an OOM-defer path were ever made to skip `transition_status`) and keeps the steering adjacent to the `priority`/`started_at` setup. (Earlier revisions of this plan asserted the OOM sub-path *already* skips `transition_status`; that is false against current main — corrected in the Freshness Check. The top-of-block recommendation is unchanged; only its rationale is.)
- **Deterministic floor on EVERY `tool_timeout`→`failed` exit (B2).** The `failed` outcome is reached from **two** branches and the floor must fire from **both**:
  - the `elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:` branch (`session_health.py:1514`); and
  - the `elif not _subprocess_confirmed_dead:` branch (`session_health.py:1529`), which finalizes `failed` on **attempt 1** (recovery_attempts already bumped to 1, `1 < MAX_RECOVERY_ATTEMPTS=2`) when the killed subprocess survived cancel+SIGTERM+SIGKILL or could not be confirmed dead (issue #1537 escalation). This second branch currently delivers neither advisory steering nor any notice — it is itself a silent-failure reproduction of #1711 and MUST be covered.
  In **each** branch, when `reason_kind == "tool_timeout"`, call `_deliver_tool_timeout_degraded_notice(entry, tool_name)` BEFORE that branch's `finalize_session(entry, "failed", …)`. Because the floor is a single helper invoked from every `tool_timeout`→`failed` exit (not inlined into one branch), adding a future `failed` path only needs the one-line helper call. The helper delivers a canned user-facing message ("I couldn't finish that — the {tool} service didn't respond after two tries. Please try again shortly; everything else is working.") through the session's resolved output handler: read transport via `(getattr(entry, "extra_context", None) or {}).get("transport")`, then `_resolve_callbacks(entry.project_key, transport)` with `FileOutputHandler` fallback — routes to the telegram outbox, email outbox, or file log per transport, never a hard-coded `telegram:outbox:` write. **On `response_delivered_at` (C1 — defense-only, NOT a reachable gate):** `_apply_recovery_transition` already early-returns at `session_health.py:1297` for any session with `response_delivered_at is not None` (finalizing it as `completed`), so by the time control reaches either `failed` branch that field is *structurally always None*. We therefore do NOT rely on a `response_delivered_at` re-check as a load-bearing skip condition, and there is no test asserting "floor skipped when already delivered" (it would assert unreachable behavior). The real double-delivery protection is the idempotency marker (C2). This is the prior-art *principle* from PR #892: advisory steering needs a deterministic backstop (mechanism differs — see Prior Art / C4).
- **Idempotency against the crash-and-retry window AND the two-branch overlap (C2).** The deterministic floor delivers the notice *before* `finalize_session("failed")`. If the worker crashes between the handler write and the finalize — or if the not-confirmed-dead branch fires on one pass and the MAX branch on a later pass for the same session — the floor could deliver a second notice. To prevent this, `_deliver_tool_timeout_degraded_notice` performs a **Redis `SETNX tool_timeout:degraded_sent:{session_id}`** (short TTL) as its first step and returns early if the key already existed. There is **no** `degraded_notice_sent_at` model field — the SETNX marker is the sole guard (zero-schema). The SETNX-before-send ordering means a crash after-set-before-send at worst suppresses the notice (acceptable — silent is the pre-existing failure, and the advisory layer already had a turn) rather than double-delivering. A test drives the floor twice for the same session and asserts only one outbox payload is written.
- **Capture the tool name early.** Read `tool_name = getattr(entry, "current_tool_name", None)` just before the transition branches into a local so a concurrent PostToolUse-driven clear cannot null it between read and use. If `tool_name` is None, the floor still delivers a generic notice (no `{tool}` substitution); the advisory injection is skipped (the `and tool_name` guard).
- **Compose a self-contained advisory steering message** because the container has no `--resume` and the steering text replaces the turn input. Include: (a) the timed-out tool name, (b) a "do not retry it" instruction, (c) the original user request (`entry.message_text`, **inlined truncated to 1500 chars** — committed decision, see Risk 1) so the model has something to answer, (d) an instruction to note what was unavailable. Example:
  > `The tool {tool_name} timed out and is temporarily unavailable — do not call it again this turn. Answer the user's original request as best you can without it, and note which information was unavailable. Original request: {original_request_truncated_1500}`
- **One advisory injection per wedge, naturally.** Since `MAX_RECOVERY_ATTEMPTS=2`, the requeue branch only fires on attempt 1 (attempt 2 goes to `failed` → deterministic floor). A single advisory injection per wedge is the natural outcome — no de-dup logic needed. Confirm by reading the branch order: `failed` (attempts ≥ 2) is checked before the `else` requeue.
- **Best-effort, never fatal.** Wrap both the `push_steering_message(..., front=True)` call and the outbox write so a failure logs at WARNING and does not block the requeue or the finalize (matches the existing observability-counter pattern in this file).
- **Telemetry (optional, low-cost):** INCR `{project_key}:session-health:tool_timeout_steering_injected` on advisory injection and `{project_key}:session-health:tool_timeout_degraded_delivered` on deterministic floor delivery so dashboards can confirm both paths fire. Best-effort.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The advisory `push_steering_message(..., front=True)` call is wrapped in try/except that logs at WARNING on failure (mirrors `recoveries:{kind}` counter pattern at `session_health.py:1170-1178`). Add a unit test asserting that a `push_steering_message` raising does NOT prevent the `transition_status(entry, "pending")` call.
- [ ] The deterministic-floor outbox write is wrapped in try/except that logs WARNING on failure and still calls `finalize_session(entry, "failed")`. Add a unit test asserting an outbox-write failure does NOT prevent finalize.
- [ ] `_compose_tool_timeout_steering` must not raise on `None`/empty `message_text` — assert it returns a valid (if generic) string.

### Empty/Invalid Input Handling
- [ ] `_compose_tool_timeout_steering(tool_name=None, ...)` — guarded by the `and tool_name` injection condition; add a test that no advisory steering is pushed when `current_tool_name` is None.
- [ ] `_compose_tool_timeout_steering(tool_name="x", original_request="")` — returns a string that still instructs the model to skip the tool (no crash, no empty turn input).
- [ ] Inline `original_request` truncated to **1500 chars** (committed) so a very long original message cannot bloat the steering queue entry; assert a 5000-char input is truncated to ≤1500 in the composed string.
- [ ] Deterministic floor with `tool_name=None` — `_deliver_tool_timeout_degraded_notice` still produces a generic (no `{tool}`) notice and does not raise.

### Prepend Ordering (B1)
- [ ] With a pre-existing steering message already queued (e.g. `["older instruction"]`), the advisory injection prepends so `queued_steering_messages[0]` is the tool-skip message and the older one is at index 1. Assert ordering explicitly — this is the regression guard for the FIFO bug.
- [ ] `push_steering_message(text, front=True)` overflow: queue already at `STEERING_QUEUE_MAX`, prepend one more → the prepended message survives the trim at index `[0]` and an entry is dropped from the **BACK/tail** (`current[:STEERING_QUEUE_MAX]`). The mirror test for `front=False` asserts the append path still drops from the head (`current[-STEERING_QUEUE_MAX:]`) — guarding the trim-direction inversion.

### Error State Rendering / Delivered Outcome (B3)
- [ ] **Advisory path:** Integration/log test asserts a simulated mcp-tier wedge produces a `pending` requeue carrying a steering message at index 0 containing the tool name AND the original-request substring — the surrogate for "model has what it needs to answer degraded".
- [ ] **Deterministic floor (delivered, not just queued):** A test drives a session to the terminal `tool_timeout` `failed` branch and asserts the resolved output handler's `send()` was invoked (telegram-transport session → payload on `telegram:outbox:{session_id}`; assert via a registered fake/relay handler so the test is channel-agnostic) with a coherent user-facing degraded notice (tool name when known, an apology, and a "try again shortly" instruction) BEFORE `finalize_session("failed")` ran. This validates that a real degraded answer is **delivered to the user** — not merely that a steering message was queued.
- [ ] **Deterministic floor on the not-confirmed-dead branch (:1529):** A test drives a `tool_timeout` session into the `elif not _subprocess_confirmed_dead:` branch on **attempt 1** (recovery_attempts bumps to 1, subprocess NOT confirmed dead) and asserts the degraded notice is delivered through the resolved handler BEFORE that branch's `finalize_session("failed")`. This is the regression guard for the silent-failure path that branch reproduces today.
- [ ] **Deterministic floor (channel-agnostic, B-A):** A test with an **email-transport** session (`extra_context["transport"] == "email"`, read via `(getattr(entry, "extra_context", None) or {}).get("transport")`) asserts the degraded notice routes to the email handler / `email:outbox:{session_id}`, not `telegram:outbox:`. A second variant with no registered callback asserts the `FileOutputHandler` fallback fires (no crash, notice logged) — proving local/file-originated sessions are covered.
- [ ] **Idempotency (C2):** Driving the floor twice for the same session (including the both-branches-fire-across-passes case) writes exactly one degraded-notice payload (the second call short-circuits on the Redis `SETNX tool_timeout:degraded_sent:{session_id}` marker).

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_recovers_wedged_session_default_tier` — UPDATE: this test patches `_apply_recovery_transition` to a fake and asserts `reason_kind == "tool_timeout"`. It does NOT exercise the real transition, so it stays green. Add a sibling test that calls the **real** `_apply_recovery_transition` with `reason_kind="tool_timeout"` and a wedged-but-recoverable session (recovery_attempts=0, subprocess confirmed dead) and asserts a steering message was prepended naming the tool.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD (advisory path): `test_tool_timeout_requeue_prepends_steering_with_tool_name`, `test_tool_timeout_prepend_when_queue_already_has_message` (B1 ordering: tool-skip is index 0, pre-existing message at index 1), `test_no_steering_when_current_tool_name_none`, `test_steering_push_failure_does_not_block_requeue`.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD (deterministic floor / B2): `test_tool_timeout_failed_branch_delivers_degraded_notice` (attempts ≥ MAX → handler `send()` invoked / outbox payload written before `finalize_session("failed")`), `test_tool_timeout_not_confirmed_dead_branch_delivers_degraded_notice` (the `:1529` attempt-1 not-confirmed-dead branch also delivers the floor notice before `finalize_session("failed")` — regression guard for the silent path), `test_failed_branch_does_not_inject_advisory_steering` (no steering on the terminal branch), `test_degraded_notice_idempotent_no_double_delivery` (C2 — driving the floor twice, including both-branches-across-passes, writes exactly one payload via Redis SETNX marker), `test_outbox_write_failure_does_not_block_finalize`, `test_degraded_notice_generic_when_tool_name_none`, `test_degraded_notice_routes_email_transport` (B-A — `extra_context["transport"]=="email"` session routes to email handler, not telegram), `test_degraded_notice_file_fallback_when_no_callback` (B-A — `FileOutputHandler` fallback). NOTE: there is intentionally NO `test_..._skipped_when_response_already_delivered` test — the `:1297` early-return makes `response_delivered_at` structurally None at the floor, so such a test would assert unreachable behavior (C1).
- [ ] `tests/unit/test_agent_session.py` (or the model's steering test file) — ADD: `test_push_steering_message_front_inserts_at_head`, `test_push_steering_message_front_overflow_drops_from_tail` (B1 — front insert keeps index 0, drops from the back via `current[:STEERING_QUEUE_MAX]`), `test_push_steering_message_append_overflow_drops_from_head` (mirror — confirms the append path's tail-keep `[-MAX:]` trim is unchanged, guarding the inversion).
- [ ] `tests/unit/` (new or existing health test file) — ADD: pure unit tests for `_compose_tool_timeout_steering` (tool name present, empty original_request, None original_request, truncation to 1500 chars).
- [ ] `tests/integration/test_session_health_tool_timeout.py` — ADD: (a) an end-to-end-ish test that drives a real mcp-tier wedge through `_agent_session_tool_timeout_check` and asserts the requeued session carries a prepended steering message; (b) a test driving the session to the terminal `failed` branch and asserting a degraded notice was written to `telegram:outbox:{session_id}` (delivered-outcome surrogate, B3). REPLACE only if an existing test asserts "no steering on requeue"; none does today.

No existing assertions are invalidated — the advisory change is additive to the requeue branch, the `front=` keyword defaults to the prior append behavior (existing callers unaffected), and the existing tool-timeout tests patch the transition function rather than asserting its internals.

## Rabbit Holes

- **Active tool suppression via a `disabled_tools` SDK list.** Tempting (more reliable than advice), but requires SDK-client / container changes, a per-session disabled-tools field, and schema work. Explicitly out of scope for this plan (see Scope / No-Gos); the advisory-steering-plus-deterministic-floor approach ships in Small appetite and guarantees a user-visible outcome regardless of whether the advice is honored.
- **Distinguishing "transient hang" from "permanently broken MCP server".** Don't build retry/backoff intelligence per MCP server. One wedge → one skip-instruction. Anything smarter is a separate project.
- **Reworking the `MAX_RECOVERY_ATTEMPTS` budget.** The acceptance criteria explicitly forbid raising it. Do not touch.
- **Generalizing steering injection to `no_progress` / `worker_dead` recoveries.** Those reasons have no single offending tool to name; the steering text would be meaningless. Keep the injection `tool_timeout`-only.

## Risks

### Risk 1: Steering message replaces turn input but lacks original context
**Impact:** Because the container has no `--resume`, the steering string becomes the entire turn input. If it only says "skip tool X" without the original request, the model answers nothing useful.
**Mitigation:** `_compose_tool_timeout_steering` **inlines** `entry.message_text` truncated to **1500 chars** (committed decision — Q2) into the steering text. A reference like "your previous message" is NOT used, because the fresh-TUI run has no `--resume` and would have nothing to dereference. Test asserts the original-request substring is present and that a 5000-char input is truncated to ≤1500.

### Risk 2: The model ignores the advisory and re-issues the tool call
**Impact:** Second wedge → terminal recovery. Without a backstop the user would be left with silent failure (the exact #1711 defect).
**Mitigation:** **The deterministic floor (B2) eliminates the silent-failure outcome.** On the terminal `failed` branch the worker delivers a canned user-facing degraded notice to the outbox before finalizing — so even when the model ignores the advisory, the user gets a real reply. The advisory text is also imperative ("do not call it again this turn") to maximize the chance the model honors it on attempt 1. Telemetry (`tool_timeout_steering_injected` vs. `tool_timeout_degraded_delivered`) measures how often the advisory is honored vs. the floor fires; active SDK `disabled_tools` suppression remains explicitly out of scope (see Scope / No-Gos) and would only be revisited if telemetry shows the floor firing frequently.

### Risk 3: `current_tool_name` cleared between read and push
**Impact:** Steering message names no tool, or the `and tool_name` guard skips injection.
**Mitigation:** Capture `tool_name` into a local before any save. The sub-loop already re-reads a `fresh` entry just before calling `_apply_recovery_transition`, so `current_tool_name` is as fresh as possible. If it is genuinely None, skip advisory injection silently (the requeue still happens); the deterministic floor still delivers a generic notice.

### Risk 4: FIFO steering queue runs an older message before the tool-skip (B1)
**Impact:** The steering queue is FIFO — `push_steering_message` appends (`models/agent_session.py:2011`) and the executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`), re-queuing the rest at the back. If a steering message was already queued when the tool wedged, a plain append would run that older message first on the re-pickup turn; the tool-skip instruction would wait, and on a fresh-TUI run the model would simply re-call the hung tool — defeating the fix.
**Mitigation:** Inject via the new `push_steering_message(..., front=True)` PREPEND path so the tool-skip instruction is `steering_msgs[0]` and is consumed first. Overflow trim for `front=True` keeps the prepended entry. A dedicated unit test (`test_tool_timeout_prepend_when_queue_already_has_message`) asserts the ordering as a regression guard.

### Risk 5: Crash-window / two-branch double delivery of the degraded notice (C2)
**Impact:** The deterministic floor delivers the notice **before** `finalize_session("failed")`, and it now fires from **two** branches (the `:1514` MAX branch and the `:1529` not-confirmed-dead branch). If the worker crashes in that window, the session is still non-terminal; the health loop re-picks it, re-enters a `failed` branch, and could deliver the canned notice a second time — the user sees a duplicate apology.
**Mitigation:** `_deliver_tool_timeout_degraded_notice` performs a Redis `SETNX tool_timeout:degraded_sent:{session_id}` (short TTL) as its **first** step and returns early if the key already existed. There is **no** `degraded_notice_sent_at` model field — the SETNX marker is the sole, zero-schema guard. SETNX-before-send ordering means a crash after-set-before-send suppresses the (already-attempted) notice rather than duplicating it; silent-on-rare-crash is strictly better than the double-send, and the advisory layer already gave the user a turn. The `response_delivered_at` guard at `:1297` is NOT the mitigation here — it is unreachable-as-false at the floor (see Solution / C1); the SETNX marker is. A unit test drives the floor twice for one session and asserts exactly one outbox write.

## Race Conditions

### Race 1: PostToolUse clears tool state between recovery read and steering push
**Location:** `agent/session_health.py` — `_apply_recovery_transition` requeue branch (`:1558`), reading `entry.current_tool_name`.
**Trigger:** The hung MCP tool returns at the exact moment recovery runs; PostToolUse writes `current_tool_name=None`.
**Data prerequisite:** `tool_name` must be captured before the steering composition reads it.
**State prerequisite:** The session is still being requeued (not finalized by a concurrent transition).
**Mitigation:** Read `tool_name` into a local at function entry / just before the branch; guard injection on `if reason_kind == "tool_timeout" and tool_name`. The sub-loop's own re-read + `_check_tool_timeout(fresh)` race guard (`session_health.py:~2567`) already aborts recovery if PostToolUse fired before the transition, so by the time we reach the requeue branch the wedge was still live at re-read time.

### Race 2: Concurrent steering writers overflow the queue
**Location:** `models/agent_session.py:1999` `push_steering_message`, `STEERING_QUEUE_MAX=10`.
**Trigger:** Another process steers the same session simultaneously.
**Data prerequisite:** none.
**State prerequisite:** none.
**Mitigation:** `push_steering_message` already trims to the last `STEERING_QUEUE_MAX` and uses a partial save (`update_fields`). One extra message cannot break the invariant. No new locking needed.

## No-Gos (Out of Scope)

**Scope statement.** This plan's scope is the two-layer graceful-degradation fix for the
`tool_timeout` recovery path: (1) advisory prepend-steering on the attempt-1 requeue, and
(2) the deterministic outbox-delivered degraded notice on the terminal `failed` branch.
Both layers are fully built, tested, and documented within this plan — nothing in scope is
left undone.

**Active SDK tool suppression (`disabled_tools`) is architecturally out of scope.** Passing
a per-session disabled-tools list to the SDK/container so the hung tool is *suppressed at the
harness level* (rather than advised against) is a genuinely separate piece of work: it
requires SDK-client and granite-container changes plus a new per-session schema field, which
is a different architectural surface than the in-process session-health recovery path this
plan touches. It is **not** a smaller-but-skipped part of this fix — it is a different
mechanism. The advisory-plus-deterministic-floor design here already guarantees a user-visible
outcome (the floor delivers regardless of whether the advisory is honored), so suppression is
not required to close #1711. We are not filing a tracking promise for it; if production
telemetry (`tool_timeout_degraded_delivered`) later shows the floor firing often enough to
justify harness-level suppression, that becomes its own independently-scoped issue at that
time, decided on its own merits.

## Update System

No update system changes required — this feature is purely internal to the worker's session-health loop. No new dependencies, config files, CLI entry points, or cross-machine propagation. The worker restart that `/update` already performs picks up the code change.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent (model) does not call any new tool; it *receives* a steering message at the next turn boundary via the existing `pop_steering_messages()` path in `session_executor.py`. No `.mcp.json` change, no new CLI entry point, no bridge import. The integration surface is the existing steering mechanism, already covered by `tests/integration/test_steering.py`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-steering.md` — add a subsection "Automatic steering on tool_timeout recovery" documenting that `_apply_recovery_transition` injects a skip-the-tool steering message on mcp/default-tier wedges (advisory layer) and delivers a channel-agnostic degraded notice on the terminal `failed` branch (deterministic floor).
- [ ] Update `docs/features/session-recovery-mechanisms.md` — this is the existing catalogue of recovery mechanisms that documents the `tool_timeout` recovery path (the doc named `docs/features/per-tool-timeout-tiers.md` does NOT exist; the #1270 mechanism lives here and in `docs/features/agent-session-health-monitor.md`). Add the graceful-degradation behavior (advisory prepend-steering + deterministic floor) to the `tool_timeout` entry, and cross-link `session-steering.md`.

### External Documentation Site
- [ ] No external docs site changes — internal feature.

### Inline Documentation
- [ ] Docstring on `_compose_tool_timeout_steering` explaining the self-contained-message requirement (no `--resume`).
- [ ] Comment at the injection point in `_apply_recovery_transition` explaining why injection is requeue-branch-only and `tool_timeout`-only.

## Success Criteria

- [ ] **(B1)** A session that wedges on `mcp__claude_ai_Notion__notion-fetch` (or any `mcp__`/default-tier tool) receives a steering message **prepended** (at index 0) on re-queue naming the timed-out tool and the inlined original request — even when another steering message is already queued.
- [ ] **(B1)** `push_steering_message(text, front=True)` inserts at the head and the prepended message survives overflow trim by dropping from the **BACK** (`current[:STEERING_QUEUE_MAX]`); the `front=False` append path still drops from the head (`current[-STEERING_QUEUE_MAX:]`) — default callers unchanged.
- [ ] The advisory `tool_timeout` requeue branch injects steering; the `failed` and `abandoned` branches do NOT inject advisory steering.
- [ ] **(B2)** **Every** `tool_timeout`→`failed` exit delivers a canned degraded notice through the session's **resolved output handler** (`_resolve_callbacks(project_key, transport)` → telegram/email handler, `FileOutputHandler` fallback) BEFORE `finalize_session("failed")` — covering BOTH the `:1514` MAX-attempts branch AND the `:1529` not-confirmed-dead attempt-1 branch (the latter is a current silent-failure path). The floor is a single helper invoked from each. A delivery failure does not block finalize.
- [ ] **(B-A — channel-agnostic)** The degraded notice routes by the session's transport, read via `(getattr(entry, "extra_context", None) or {}).get("transport")` (NOT `getattr(entry, "transport", None)` — no such field): telegram-transport → telegram outbox, email-transport → email outbox, no-callback → `FileOutputHandler`. No code path hard-codes `telegram:outbox:` for the floor.
- [ ] **(C1 — defense-only, no unreachable assertion)** The plan does NOT assert a reachable "floor skipped when `response_delivered_at` set" behavior; the `:1297` early-return makes that field structurally None at the floor. Double-delivery is instead prevented by the idempotency marker (C2).
- [ ] **(C2 — idempotency)** Driving the floor twice for one session writes exactly one degraded-notice payload (Redis `SETNX tool_timeout:degraded_sent:{session_id}` short-circuits the second call), covering both the crash-and-retry window and the two-branch overlap. No `degraded_notice_sent_at` model field is added.
- [ ] **(B3 — delivered, not just queued)** A test confirms that on the terminal wedge a coherent user-facing degraded answer (apology + tool name when known + "try again shortly") is actually delivered via the resolved handler — validating the user receives a real reply, not merely that a steering message was queued.
- [ ] `MAX_RECOVERY_ATTEMPTS` is unchanged (still 2).
- [ ] **(Q2)** Unit test: the advisory steering message inlines the original request truncated to 1500 chars and contains the tool name.
- [ ] Unit test: steering-push failure does not block the `pending` requeue.
- [ ] Integration test: a simulated mcp-tier wedge produces a `pending` requeue carrying the prepended steering message (advisory path), and a terminal wedge writes the degraded notice to the outbox (deterministic-floor path).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_apply_recovery_transition` references `push_steering_message`, `_compose_tool_timeout_steering`, and `_deliver_tool_timeout_degraded_notice`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (steering-injection)**
  - Name: steering-builder
  - Role: Add `_compose_tool_timeout_steering` and the requeue-branch injection in `_apply_recovery_transition`; write unit + integration tests.
  - Agent Type: builder
  - Resume: true

- **Validator (steering-injection)**
  - Name: steering-validator
  - Role: Verify injection fires only on the `tool_timeout` requeue branch, never on `failed`/`abandoned`; verify failure-path coverage and that `MAX_RECOVERY_ATTEMPTS` is untouched.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: steering-doc
  - Role: Update `docs/features/session-steering.md` and `docs/features/session-recovery-mechanisms.md` (the existing recovery-mechanism catalogue; cross-link the two).
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — builder, validator, documentarian sufficient for this Small-appetite fix.)

## Step by Step Tasks

### 1. Implement steering composer + injection
- **Task ID**: build-steering-injection
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_tool_timeout.py, tests/integration/test_session_health_tool_timeout.py
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `front: bool = False` keyword to `AgentSession.push_steering_message` (`models/agent_session.py`): when `front`, insert at index 0 and **branch the overflow trim** — `front=True` trims `current[:STEERING_QUEUE_MAX]` (keep index-0 prepended message, drop from BACK); `front=False` keeps the existing `current[-STEERING_QUEUE_MAX:]` (drop from head). Default path unchanged.
- Add `_compose_tool_timeout_steering(tool_name: str, original_request: str | None) -> str` to `agent/session_health.py` (pure, inlines original_request truncated to 1500 chars, never raises).
- Add `_deliver_tool_timeout_degraded_notice(entry, tool_name: str | None) -> None` to `agent/session_health.py`: reads transport via `(getattr(entry, "extra_context", None) or {}).get("transport")` (AgentSession has NO `transport` field — do NOT use `getattr(entry, "transport", None)`), resolves the handler via `_resolve_callbacks(entry.project_key, transport)` (with `FileOutputHandler` fallback when no callback is registered), and delivers a canned degraded message through it — channel-agnostic, NOT a hard-coded `telegram:outbox:` write. Best-effort (WARNING on failure). Performs a Redis `SETNX tool_timeout:degraded_sent:{session_id}` (short TTL) as the first step and returns early if the key already existed (C2 — sole guard, no model field). Do NOT gate on `response_delivered_at` — it is unreachable-as-set at the floor (C1).
- In `_apply_recovery_transition`, capture `tool_name = getattr(entry, "current_tool_name", None)` before the transition branches.
- In the `else:` requeue branch, place the advisory injection at the **TOP of the block** (`session_health.py:~1559`, immediately after `entry.priority="high"` / `entry.started_at=None` are set and BEFORE the OOM-defer check at `:1561`) — NOT just before `transition_status` — because the top of the block **dominates every requeue sub-path** (the OOM-defer sub-path `:1561-1580` and the normal-save sub-path `:1581-1588`, both of which fall through to the shared `transition_status` at `:1589`), so the steering is carried whichever sub-path runs and survives any future restructuring (B-B). Guard `if reason_kind == "tool_timeout" and tool_name:` and call `entry.push_steering_message(..., front=True)` inside try/except (WARNING on failure).
- Invoke the floor from **EVERY** `tool_timeout`→`failed` exit (single helper call per branch): (a) in the `elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:` branch (`session_health.py:~1514`); AND (b) in the `elif not _subprocess_confirmed_dead:` branch (`session_health.py:~1529`, the attempt-1 #1537 escalation that currently fails silently). In each, when `reason_kind == "tool_timeout"`, call `_deliver_tool_timeout_degraded_notice(entry, tool_name)` BEFORE that branch's `finalize_session(entry, "failed", …)`.
- Best-effort INCR `{project_key}:session-health:tool_timeout_steering_injected` (advisory) and `…:tool_timeout_degraded_delivered` (floor).

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-steering-injection
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit: composer (tool name present, empty/None original_request, truncation to 1500 chars).
- Unit: `push_steering_message(front=True)` prepends at head + survives overflow trim (drops from BACK via `current[:STEERING_QUEUE_MAX]`); mirror test confirms `front=False` append path still drops from the head (`[-MAX:]`) — guards the trim-direction inversion.
- Unit (advisory): requeue PREPENDS steering with tool name + original-request substring; ordering correct when a message is already queued (B1); `failed` branch does not inject advisory; None tool name → no advisory injection; push failure → requeue still happens.
- Unit (deterministic floor / B2): BOTH `failed` branches deliver the degraded notice via the resolved handler before finalize — the `:1514` MAX branch AND the `:1529` not-confirmed-dead attempt-1 branch; generic notice when tool name None; delivery failure does not block finalize. Idempotency (C2): floor driven twice (incl. both-branches-across-passes) → exactly one payload via Redis SETNX. Channel-agnostic (B-A): email-transport session (`extra_context["transport"]=="email"`) routes to email handler; no-callback session falls back to `FileOutputHandler`. Do NOT add a "skipped when `response_delivered_at` set" test — unreachable (C1).
- Integration: simulated mcp-tier wedge → `pending` requeue carries prepended steering message; terminal wedge → degraded notice delivered via the resolved handler (telegram-transport → `telegram:outbox:{session_id}`) (B3 delivered-outcome).

### 3. Validate
- **Task ID**: validate-steering
- **Depends On**: build-steering-injection, build-tests
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm injection is requeue-branch-only and `tool_timeout`-only.
- Confirm `MAX_RECOVERY_ATTEMPTS` unchanged.
- Run `pytest tests/unit/test_session_health_tool_timeout.py tests/integration/test_session_health_tool_timeout.py -q`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-steering
- **Assigned To**: steering-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-steering.md` with the automatic-steering subsection (advisory + floor).
- Update `docs/features/session-recovery-mechanisms.md` `tool_timeout` entry with the graceful-degradation behavior and cross-link `session-steering.md`. (Do NOT reference `docs/features/per-tool-timeout-tiers.md` — it does not exist.)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks below; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tool-timeout tests pass | `pytest tests/unit/test_session_health_tool_timeout.py tests/integration/test_session_health_tool_timeout.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Advisory injection wired | `grep -n "push_steering_message" agent/session_health.py` | output contains push_steering_message with front=True |
| Composer wired | `grep -n "_compose_tool_timeout_steering" agent/session_health.py` | output > 1 |
| Deterministic floor wired on both failed exits | `grep -n "_deliver_tool_timeout_degraded_notice" agent/session_health.py` | output ≥ 3 (helper def + MAX-branch call + not-confirmed-dead-branch call) |
| Floor reads transport from extra_context (not the constant-None field) | `grep -n "extra_context" agent/session_health.py` | floor helper resolves transport via `(getattr(entry, "extra_context", None) or {}).get("transport")`; floor does NOT use `getattr(entry, "transport"` |
| Floor is channel-agnostic (B-A) | `grep -n "_resolve_callbacks" agent/session_health.py` | floor uses `_resolve_callbacks` (no hard-coded `telegram:outbox:` in the floor helper) |
| Idempotency via SETNX, no new field | `grep -n "tool_timeout:degraded_sent" agent/session_health.py` | floor performs Redis SETNX; `grep -c degraded_notice_sent_at models/agent_session.py` → 0 |
| Prepend method wired with branched trim | `grep -n "front" models/agent_session.py` | push_steering_message has front kwarg; front=True trims `[:STEERING_QUEUE_MAX]`, front=False trims `[-STEERING_QUEUE_MAX:]` |
| MAX unchanged | `grep -n "MAX_RECOVERY_ATTEMPTS = 2" agent/session_health.py` | output contains MAX_RECOVERY_ATTEMPTS = 2 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique (B1) | FIFO queue runs an older steering message before tool-skip | Prepend path (`push_steering_message(front=True)`) | Solution / Risk 4 / B1 tests |
| Blocker | Critique (B2) | Advisory-only repeats PR #892's mistake; no deterministic floor | Deterministic outbox notice on terminal `failed` branch | Prior Art (#892), Solution, B2 tests |
| Blocker | Critique (B3) | Success criteria only assert plumbing, not a delivered answer | Delivered-outcome criterion + outbox-write test | Success Criteria / Failure Path B3 |
| Blocker | Critique (B4) | Open Question 2 contradicted committed inlining decision | Committed to inlining at 1500 chars; OQ removed | Solution / Risk 1 / Q2 |
| Blocker | Re-critique (B-A) | Floor hard-coded `telegram:outbox:` — silent failure for email/file sessions | Route through resolved `OutputHandler` via `_resolve_callbacks(project_key, transport)` + `FileOutputHandler` fallback | Problem, Data Flow §7, Solution (B2), Arch Impact, Success Criteria, Tests |
| Blocker | Re-critique (B-B) | Injection before `transition_status` skips the OOM-defer requeue sub-path | Move injection to TOP of `else:` block (`:~1469`, before OOM check `:1471`) so both sub-paths carry it | Data Flow §4, Solution (Technical Approach), Step 1 |
| Concern | Re-critique (C1) | `response_delivered_at` guard/test unreachable (early-return at `:1207`) | Reframed as defense-only; dropped the unreachable guard/test; idempotency marker is the real protection | Solution (B2), Success Criteria, Test Impact |
| Concern | Re-critique (C2) | Crash window between handler write and finalize could double-deliver | One-shot idempotency marker (`degraded_notice_sent_at` / Redis SETNX) set before send | Solution, Risk 5, Tests, Success Criteria |
| Concern | Re-critique (C3) | Documentation pointed at non-existent `per-tool-timeout-tiers.md` | Retargeted to existing `docs/features/session-recovery-mechanisms.md` | Documentation, Step 4, Team Orchestration |
| Concern | Re-critique (C4) | PR #892 analogy overclaimed mechanism equivalence | Softened to "precedent that advisory-only is insufficient"; mechanism explicitly differs | Problem, Prior Art |
| Blocker | 3rd-revision (D1) | Floor resolved transport via `getattr(entry, "transport", None)` — constant-None (no such field; the `:2092` antipattern) — breaking email/file routing | Resolve transport via `(getattr(entry, "extra_context", None) or {}).get("transport")` (canonical: `session_executor.py:1024-1025`, `output_handler.py:181-182`) everywhere the floor calls `_resolve_callbacks` | Data Flow §7, Arch Impact, Solution (B2), Step 1, Success Criteria, Verification |
| Blocker | 3rd-revision (D2) | The `:1439` `not _subprocess_confirmed_dead` branch finalizes `failed` on attempt 1 with NO advisory and NO floor notice — a live silent-failure reproduction of #1711 | Floor is a single helper invoked from EVERY `tool_timeout`→`failed` exit, including the `:1439` not-confirmed-dead branch, before each `finalize_session("failed")` | Data Flow §7, Flow, Solution (B2), Step 1/2, Test Impact, Success Criteria, Verification |
| Blocker | 3rd-revision (D3) | C2 guard referenced `degraded_notice_sent_at`, a field AgentSession does not have | Dropped the nonexistent field; idempotency is the Redis `SETNX tool_timeout:degraded_sent:{session_id}` marker alone (zero-schema) | Arch Impact (Schema), Solution (C2), Risk 5, Step 1, Success Criteria, Verification |
| Blocker | 3rd-revision (D4) | `front=True` prepend used the append-path trim (`[-MAX:]`), dropping the just-prepended tool-skip message at the WRONG end | `push_steering_message` branches the trim on `front`: prepend keeps the head and drops from the BACK (`[:STEERING_QUEUE_MAX]`); append unchanged | Arch Impact, Solution (B1 / Technical Approach), Failure Path tests, Test Impact, Step 1/2, Success Criteria, Verification |

---

## Open Questions

None — all prior open questions resolved during the revision pass:
- **Advisory vs. active suppression:** committed to advisory steering PLUS a deterministic floor (the canned degraded notice delivered through the session's resolved output handler — channel-agnostic). Active SDK `disabled_tools` suppression is explicitly out of scope (see No-Gos) — a separate architectural surface, not required to close #1711.
- **Inline vs. reference the original request:** committed to **inlining the original request truncated to 1500 chars** (see Solution / Risk 1).
