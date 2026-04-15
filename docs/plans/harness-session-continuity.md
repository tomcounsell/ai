---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/976
last_comment_id:
revision_applied: true
---

# Harness Session Continuity (--resume / --continue)

## Problem

Dev sessions executed via the CLI harness (`claude -p` subprocess) crash with
`"Separator is found, but chunk is longer than limit"` on long-running Telegram
threads. Production has already reproduced this on session
`tg_valor_-5051653062_8939` (2026-04-15).

**Current behavior:**

The harness is *stateless per call*. Every turn rebuilds the entire reply
chain, project header, scope text, and steering message into a single string
and passes it as a positional argv to `claude`. As threads grow, that
positional argument crosses the binary's internal chunk limit and the
subprocess dies. The Claude Code session UUID is captured from the
stream-json `result` event but only logged at DEBUG; nothing stores it. So
every harness turn looks like a fresh first turn, and `--resume` / `--continue`
are never used.

```
claude -p --verbose --output-format stream-json ... [entire_reconstructed_context]
                                                    ^^ overflows on long threads
```

**Desired outcome:**

After the first turn, the harness uses the CLI's native session continuity:

```
claude -p --resume <prior_uuid> --verbose --output-format stream-json ... [just_the_new_user_message]
```

The binary then loads prior context from its own session file on disk, the
positional argument stays bounded by the size of the new user message, and
context overflow becomes structurally impossible for resumed turns.

## Freshness Check

**Baseline commit:** `92811099` (`Bump deps: anthropic 0.94.1->0.95.0`)
**Issue filed at:** 2026-04-15T03:13:45Z
**Disposition:** Unchanged

**File:line references re-verified (2026-04-15):**
- `agent/sdk_client.py:150` — `_get_prior_session_uuid()` definition — still holds
- `agent/sdk_client.py:201` — `_store_claude_session_uuid()` definition — still holds
- `agent/sdk_client.py:1202` — SDK-path call site of `_store_claude_session_uuid()` — still holds
- `agent/sdk_client.py:1489` — `_apply_context_budget()` definition — still holds
- `agent/sdk_client.py:1543` — `get_response_via_harness()` entry point — still holds
- `agent/sdk_client.py:1574` — `_apply_context_budget()` is applied to `message` before subprocess exec — still holds
- `agent/sdk_client.py:1596` — `session_id_from_harness = None` — still holds
- `agent/sdk_client.py:1613` — `session_id_from_harness = data.get("session_id")` from `result` event — still holds
- `agent/sdk_client.py:1725` — `build_harness_turn_input()` definition (referenced as the prefix builder) — still holds
- `agent/agent_session_queue.py:3727` — call site importing `build_harness_turn_input` and `get_response_via_harness` — still holds
- `agent/agent_session_queue.py:3732` — `_harness_input = await build_harness_turn_input(...)` — still holds
- `agent/agent_session_queue.py:3762` — `await get_response_via_harness(message=_harness_input, ...)` — still holds

**Cited sibling issues/PRs re-checked:**
- #958 (closed 2026-04-14) — `_apply_context_budget()` band-aid landed; this issue is the proper fix layer
- #961 (closed 2026-04-14) — Redis hiredis chunk limit; orthogonal root cause; nothing to merge with
- #780 / #838 (open) — BaseHarness / Pi harness; downstream consumers of this pattern; no work to coordinate
- PR #909 (merged 2026-04-13) — established the SDK-path UUID storage pattern this issue extends

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since="2026-04-15T03:13:45Z" -- agent/sdk_client.py agent/agent_session_queue.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent
plans (`agent_wiki.md`, `pm-dev-session-briefing.md`, `nudge-stomp-append-event-bypass.md`)
do not touch the harness path.

**Notes:** Issue is fresh and accurate. No drift. Proceed.

## Prior Art

- **#958** (closed 2026-04-14) — *PM session crashes with 'Separator is not found' on context overflow during multi-turn resume*. Added `_apply_context_budget()` (`sdk_client.py:1489`) which trims the reconstructed input string to ≤100K chars. **Outcome:** band-aid. Reduced crash frequency but cannot prevent intra-turn tool-output accumulation from overflowing inside the binary. This issue (#976) is the proper fix at the source.
- **#961** (closed 2026-04-14) — *bug(session): hiredis 'Separator is not found' crash when session_events payload exceeds chunk limit*. Same error string, different root cause (Redis transport, not subprocess argv). **Outcome:** orthogonal, already fixed at the Redis layer.
- **PR #909** (merged 2026-04-13) — *feat: SDLC stage model selection and hard-PATCH builder session resume*. Established the SDK-path pattern of storing `claude_session_uuid` on the `AgentSession` Popoto record after each turn and reusing it for the next turn's `--resume` argument. **Outcome:** working. This issue extends the same pattern from the SDK path to the harness path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| #958 (`_apply_context_budget`) | Trims the reconstructed context string to ≤100K chars before passing to the CLI binary | Treats the symptom (a too-large argv string) instead of the cause (rebuilding the entire context every turn). Cannot shrink context the binary itself accumulates during a turn from tool outputs. Long threads with many tool calls still overflow. |

**Root cause pattern:** Every prior fix has assumed the harness must reconstruct
context per call. The actual fix is to stop reconstructing — let the binary
manage its own session state via `--resume`.

## Data Flow

End-to-end trace of a single harness turn, with the proposed change:

1. **Entry point**: New user message arrives in the bridge / steering inbox; `_execute_agent_session()` in `agent/agent_session_queue.py` is invoked.
2. **Prior-UUID lookup (NEW)**: Before building the turn input, `_execute_agent_session()` calls `_get_prior_session_uuid(session.session_id)`. Returns a UUID string if a prior harness turn ran for this `session_id`, else `None`.
3. **Turn-input construction**: `build_harness_turn_input(...)` is called **twice when resuming**.
   - **Always**: build the full-context message (`skip_prefix=False`) — `PROJECT / FROM / SESSION_ID / TASK_SCOPE / SCOPE / [WORK REQUEST] / MESSAGE` prefix as today. This is kept as `full_context_message` for fallback.
   - **If `prior_uuid` set (NEW)**: also build the minimal message (`skip_prefix=True`) — just the raw user message body. This is used as the primary `message` arg.
   - **First turn (`prior_uuid is None`)**: only the full-context message is built (current behavior).
4. **Subprocess invocation**: `get_response_via_harness(message, working_dir, env, prior_uuid=..., full_context_message=...)` is called.
   - **Validates `prior_uuid` format** (UUID regex check). If invalid, logs warning and treats as `None`.
   - Constructs `cmd = ["claude", "-p", ...base_flags...]`.
   - **If `prior_uuid` set and valid (NEW)**: appends `["--resume", prior_uuid]` to `cmd` and skips `_apply_context_budget()` (the message is already small — just the new user input).
   - **If `prior_uuid is None`**: applies `_apply_context_budget()` as today (safety net for first turns).
   - Appends `[message]` as final positional argv.
   - Spawns `asyncio.create_subprocess_exec(*cmd, ...)`.
   - **Mandatory stale-UUID fallback (NEW)**: if the subprocess exits non-zero with "requires a valid session" in stderr, retries once using `full_context_message` (with `_apply_context_budget()` applied), without `--resume`. This handles deleted session files.
5. **Stream parsing**: As today — reads stream-json line-by-line, captures `session_id_from_harness` from the `result` event.
6. **UUID storage (NEW)**: After the `result` event is parsed (whether or not `--resume` was used), if `session_id_from_harness` is set, call `_store_claude_session_uuid(session_id, session_id_from_harness)`. This persists the UUID on the AgentSession Popoto record so the next turn finds it via step 2.
7. **Output**: `result_text` returned to caller, BackgroundTask delivers it.

**The new piece:** step 2 (lookup) and step 6 (store) close the loop. Step 3 and 4 use the lookup to pick the right command shape.

## Architectural Impact

- **New dependencies**: none. All required helpers (`_get_prior_session_uuid`, `_store_claude_session_uuid`) already exist and are reusable from the SDK path.
- **Interface changes**: `get_response_via_harness()` gains three optional keyword-only parameters: `prior_uuid: str | None = None`, `session_id: str | None = None`, and `full_context_message: str | None = None` (all additive, default-None — backward-compatible). `build_harness_turn_input()` gains an optional `skip_prefix: bool = False` keyword-only parameter (additive, default-False — backward-compatible). No return-type changes. The `session_id` param introduces a Popoto write side effect (see Change 1 side-effect note); tests must mock `_store_claude_session_uuid` when providing it.
- **Coupling**: unchanged. The harness path now uses the same Popoto field (`claude_session_uuid`) the SDK path already uses. No new cross-component wiring.
- **Data ownership**: unchanged. `claude_session_uuid` lives on `AgentSession` (where it already lives for the SDK path).
- **Reversibility**: high. If `--resume` proves problematic, removing the `--resume` injection and the prefix-skip restores the prior behavior. `_apply_context_budget()` stays as the safety net throughout.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue)
- Review rounds: 1 (standard PR review)

Three surgical edits in two files with clear before/after diffs and pre-existing helpers. No new abstractions, no new dependencies, no infra. Bottleneck is review and test verification, not implementation time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on PATH | `command -v claude` | Harness binary required for runtime and integration test |
| `claude -p --resume` supported | `claude -p --resume nonexistent-uuid hi 2>&1 \| head -5` | Verify the CLI version supports the flag (any non-fatal output is acceptable; we just need the flag to be recognized) |
| Popoto / Redis available | `python -c "from models.agent_session import AgentSession; AgentSession.query.filter(session_id='__plan_check__')"` | UUID storage path requires Popoto |

Run all checks: `python scripts/check_prerequisites.py docs/plans/harness-session-continuity.md`

## Solution

### Key Elements

- **Harness UUID persistence**: After every successful harness turn, store the `session_id_from_harness` value (already captured) on the `AgentSession` record via the existing `_store_claude_session_uuid()` helper.
- **Harness UUID reuse**: Before each harness turn, look up the prior UUID via the existing `_get_prior_session_uuid()` helper. When found, inject `--resume <uuid>` into the subprocess argv.
- **Resumed-turn message minimization**: When resuming, skip the context-prefix construction in `build_harness_turn_input()` and pass only the raw new user message as the positional argv. The binary already has all prior context in its session file.
- **Retain the safety net**: `_apply_context_budget()` continues to run on first-turn (no-prior-UUID) calls. It is bypassed on resumed turns where the message is already minimal.

### Flow

**Telegram message** → `_execute_agent_session` looks up prior UUID → if found: build raw message + spawn `claude -p --resume <uuid> [raw_msg]` ; if not: build full-context message + spawn `claude -p [full_ctx]` (current behavior) → harness runs → store returned UUID for next turn → deliver result via BackgroundTask.

### Technical Approach

Three surgical changes, all confined to `agent/sdk_client.py` and `agent/agent_session_queue.py`:

**Change 1 — Persist UUID after every harness turn.**

In `get_response_via_harness()` (`sdk_client.py:1543`), after the `result` event is parsed and the function is about to return, persist the captured UUID. The function does not have `session_id` in scope today, so add a new optional keyword-only parameter `session_id: str | None = None`. When the caller provides it and `session_id_from_harness` is non-null, call `_store_claude_session_uuid(session_id, session_id_from_harness)` before returning.

The store call must succeed-or-fail-silent (the helper already does this internally — see `sdk_client.py:228`).

**Side-effect note (from critique CONCERN-3):** This adds a Popoto/Redis write as a side effect inside `get_response_via_harness()`, which was previously a pure subprocess-calling function. Tests that call `get_response_via_harness` with a `session_id` argument MUST mock `_store_claude_session_uuid` to avoid hitting Redis. The function remains pure when `session_id` is not provided (backward-compatible default).

**Change 2 — Inject `--resume` on subsequent turns with mandatory fallback.**

Add optional keyword-only parameters `prior_uuid: str | None = None` and `full_context_message: str | None = None` to `get_response_via_harness()`. When `prior_uuid` is set:
- **Validate UUID format** before injection: check `prior_uuid` matches the pattern `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` (standard UUID v4). If validation fails, treat as `None` (log warning, fall through to first-turn path). This prevents corrupted Popoto records from injecting garbage into subprocess argv.
- Insert `["--resume", prior_uuid]` into `cmd` *after* the base flags but *before* the final positional message arg.
- Skip the `_apply_context_budget()` call. The message at this point is just the new user input — it cannot overflow.
- **Mandatory stale-UUID fallback**: if the subprocess exits with non-zero return code AND stderr contains "requires a valid session" (the empirically confirmed error from `claude -p --resume <bad-uuid>`), retry **once** without `--resume` using `full_context_message` (the full-context first-turn message). This retry applies `_apply_context_budget()` to `full_context_message` before passing it. The fallback is not conditional — empirical testing (2026-04-15) confirms the claude binary **always** errors with a non-existent UUID.

When `prior_uuid` is unset or empty: current behavior — apply the budget, no `--resume`, full reconstructed context as the positional arg. Treat empty-string `prior_uuid` as `None`.

In `_execute_agent_session()` (`agent_session_queue.py:3727`), before calling `build_harness_turn_input`, look up the prior UUID:

```python
from agent.sdk_client import _get_prior_session_uuid
_prior_uuid = _get_prior_session_uuid(session.session_id)
```

**Always build BOTH message forms at the call site.** Build the full-context message via `build_harness_turn_input(skip_prefix=False)` first. If `_prior_uuid` is set, also build the minimal message via `build_harness_turn_input(skip_prefix=True)`. Pass both to `get_response_via_harness(prior_uuid=_prior_uuid, session_id=session.session_id, full_context_message=_full_context_input)` so the stale-UUID fallback has access to the full context without needing to reconstruct it.

**Change 3 — Skip context prefix on resumed turns.**

In `build_harness_turn_input()` (`sdk_client.py:1725`), add a keyword-only parameter `skip_prefix: bool = False`. When True, return the raw `message` argument unchanged — no `PROJECT` / `FROM` / `SESSION_ID` / `TASK_SCOPE` / `SCOPE` / `WORK REQUEST` / `MESSAGE:` headers. The binary already has all of that context from its session file, plus the global `CLAUDE.md` is auto-loaded by the binary on every invocation.

When False (first turn or UUID lookup miss): current behavior — full prefix.

**Why keyword-only and default values:** all three signature additions are
backward-compatible. Existing tests (`test_cross_repo_gh_resolution.py` calls
`build_harness_turn_input` six times) continue to pass without change.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_get_prior_session_uuid()` already wraps its body in `except Exception` (`sdk_client.py:179`); the existing test path covers Redis-down behavior. No new test required for that handler.
- [ ] `_store_claude_session_uuid()` already wraps its body in `except Exception` (`sdk_client.py:228`); add a test asserting that a Popoto write failure does not propagate out of `get_response_via_harness()` and the function still returns the result text.
- [ ] No new `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] If `prior_uuid` is the empty string, treat it as `None` (don't pass `--resume ""` to the subprocess). Add a unit test for this.
- [ ] If `prior_uuid` fails UUID format validation (not matching `^[0-9a-f]{8}-...`), treat it as `None`, log a warning, and fall through to first-turn path. Add a unit test with a corrupted UUID string.
- [ ] If `session_id_from_harness` is empty/missing on the `result` event, do not call `_store_claude_session_uuid()` (current code already guards with `if session_id_from_harness:` — preserve that). Add a unit test confirming no store call happens on a `result` event without a `session_id` field.
- [ ] If `build_harness_turn_input(skip_prefix=True, message="")` is called with empty message, return `""` unchanged (do not crash). Add a unit test.

### Error State Rendering
- [ ] When the harness exits with a non-zero return code on a resumed turn AND stderr contains "requires a valid session", the mandatory fallback retries once without `--resume` using `full_context_message`. Add a unit test that mocks this scenario and asserts the fallback fires and returns a valid result.
- [ ] When the harness exits with a non-zero return code on a resumed turn for a DIFFERENT error (not stale UUID), the existing error path (`sdk_client.py:1635`) logs and returns `""`; this behavior must be preserved. Add a unit test confirming no retry for non-stale-UUID errors.
- [ ] When `full_context_message` is `None` and the stale-UUID fallback would fire, log an error and return `""` (cannot retry without the full context). Add a unit test for this defensive path.

## Test Impact

- [ ] `tests/unit/test_cross_repo_gh_resolution.py` (6 calls to `build_harness_turn_input`) — UPDATE: no behavioral change required, but add one new test case asserting `skip_prefix=True` returns the raw message unchanged across these scenarios. Existing assertions continue to hold (default-False keeps current behavior).
- [ ] `tests/unit/test_harness_streaming.py` — UPDATE: add new test cases:
  - `test_get_response_via_harness_includes_resume_when_prior_uuid_set` — assert `--resume <uuid>` appears in the spawned subprocess argv when `prior_uuid` is provided.
  - `test_get_response_via_harness_omits_resume_when_prior_uuid_none` — assert `--resume` does NOT appear in argv when `prior_uuid is None` (regression guard).
  - `test_get_response_via_harness_skips_context_budget_on_resume` — assert `_apply_context_budget` is not invoked when `prior_uuid` is set.
  - `test_get_response_via_harness_applies_context_budget_on_first_turn` — regression guard for #958's fix.
  - `test_get_response_via_harness_stores_uuid_after_result` — assert `_store_claude_session_uuid` is called with the captured UUID after a successful turn.
  - `test_get_response_via_harness_no_store_when_uuid_missing` — assert no store call when `result` event lacks `session_id`.
  - `test_get_response_via_harness_treats_empty_prior_uuid_as_none` — assert `--resume ""` is not emitted.
  - `test_get_response_via_harness_rejects_invalid_uuid_format` — assert corrupted UUID (e.g. `not-a-uuid`) is treated as None, no `--resume` emitted.
  - `test_get_response_via_harness_stale_uuid_fallback` — mock subprocess returning non-zero with "requires a valid session" in stderr; assert retry fires with `full_context_message` and `_apply_context_budget`.
  - `test_get_response_via_harness_no_retry_on_other_errors` — mock subprocess returning non-zero with different stderr; assert no retry, returns `""`.
  - `test_get_response_via_harness_fallback_without_full_context` — assert that when `full_context_message` is None and stale-UUID fallback triggers, returns `""` with logged error.
- [ ] `tests/unit/test_cross_wire_fixes.py` — REVIEW (no change expected): the SDK-path UUID-storage tests should still pass; the harness path changes do not touch SDK code paths.
- [ ] New file `tests/integration/test_harness_resume.py` (CREATE) — REPLACE the absence of integration coverage. Two sequential `get_response_via_harness` calls on the same `session_id`: assert the second call's argv contains `--resume <uuid_from_first_call>` and the body argv is small (just the new message, not a reconstructed prefix). Mark with `@pytest.mark.integration` and gate on `claude` binary availability.

## Rabbit Holes

- **Designing for Pi harness or BaseHarness now.** #780 / #838 are downstream consumers. Adding an abstraction layer on top of the harness path before #780 lands inverts the dependency. Ship the concrete fix; let #780 generalize later.
- **Refactoring `build_harness_turn_input()` for clean separation of "context builder" vs "wrapper".** Tempting because the function does several things. Out of scope — the additive `skip_prefix` flag is the smallest change that achieves the goal. A cleanup pass can come later as a chore.
- **Removing `_apply_context_budget()` because "we don't need it anymore".** Wrong. First turns and UUID-lookup misses still need the safety net. The plan explicitly retains it.
- **Handling cross-process UUID race conditions.** Popoto's per-`session_id` storage is already serialized through Redis; two parallel turns on the same `session_id` is itself a bug (and #887 covers the parallel-session-creation race separately). Out of scope for this issue.
- **Re-architecting the SDK path to also use `--resume` instead of `continue_conversation=True`.** The SDK path already works correctly via PR #909. Don't touch it.

## Risks

### Risk 1: Stale UUID points to a deleted session file
**Impact:** `claude -p --resume <stale_uuid>` errors with "Error: --resume requires a valid session ID or session title" (empirically confirmed 2026-04-15). The turn fails if not caught.
**Mitigation:** The implementation includes a **mandatory** retry-without-`--resume` fallback. When the subprocess exits non-zero and stderr contains "requires a valid session", `get_response_via_harness()` retries once using `full_context_message` (the full-context first-turn message, passed by the caller). This fallback is not conditional — it is always wired in. The integration test exercises this path explicitly with a known-bad UUID.

### Risk 2: First-turn classification fails (UUID lookup returns a value when it shouldn't)
**Impact:** A truly fresh session would skip the context prefix and the binary would have no project / scope / sender info on the first turn.
**Mitigation:** `_get_prior_session_uuid()` only returns a value if an `AgentSession` record with the matching `session_id` already exists in Popoto AND has a non-empty `claude_session_uuid` field (`sdk_client.py:175`). For a fresh session, no prior record exists, so it returns `None` and the full prefix is built. This is the same guard the SDK path uses today (verified in PR #909 tests).

### Risk 3: `--resume` interacts unexpectedly with `--include-partial-messages` or `--permission-mode bypassPermissions`
**Impact:** The harness command template (`_HARNESS_COMMANDS["claude-cli"]` at `sdk_client.py:1528`) carries flags that may or may not be honored on resumed sessions.
**Mitigation:** The integration test exercises a full two-turn cycle with the actual binary. If any flag is silently ignored on resume, the test will surface it via behavioral assertion (e.g., permission prompts appearing where they shouldn't). This is cheap and definitive.

### Risk 4: Storing the UUID on every turn writes to Popoto under load
**Impact:** Long-running sessions could generate hundreds of writes; if Popoto throttles, latency increases.
**Mitigation:** `_store_claude_session_uuid()` is the same helper PR #909 uses on the SDK path, which has been in production since 2026-04-13 without throughput issues. Plus, the value being written is the same UUID 99% of the time (the binary keeps the same `session_id` across `--resume` calls), so an idempotent write is fine. If profiling later shows the write to be hot, a "skip if unchanged" check is trivial to add. Not blocking the initial fix.

## Race Conditions

### Race 1: Concurrent turns on the same `session_id`
**Location:** `agent/agent_session_queue.py` around `_execute_agent_session` (the dispatch path).
**Trigger:** Two messages arrive for the same `session_id` while the worker is still mid-turn for an earlier message.
**Data prerequisite:** The `claude_session_uuid` written at the end of turn N must be visible before turn N+1's lookup.
**State prerequisite:** The worker must serialize turns per `session_id` (or accept that the second turn may not see the first turn's UUID).
**Mitigation:** The standalone worker already serializes session execution per `session_id` via `AgentSessionQueue` (the same lock that prevents concurrent SDK calls). The `--resume` write happens before the function returns, which is before the queue releases the next turn for the same `session_id`. So by the time turn N+1 reads, turn N's write is durably stored. No additional locking required. (This is the same invariant PR #909 relies on for the SDK path.)

### Race 2: UUID lookup vs. UUID store on the *first* turn of a new session
**Location:** `agent/sdk_client.py:_get_prior_session_uuid` and `_store_claude_session_uuid`.
**Trigger:** A brand-new `session_id` arrives; the lookup finds no prior record; the store happens at the end of the turn.
**Data prerequisite:** No prior `AgentSession` record exists.
**State prerequisite:** The first turn must not attempt `--resume` (since there is nothing to resume).
**Mitigation:** `_get_prior_session_uuid()` returns `None` when no record exists; the dispatcher passes `None` to `get_response_via_harness`, which then takes the full-context path. No race — the lookup is read-only and the store happens strictly after. Already-tested invariant from PR #909.

## No-Gos (Out of Scope)

- BaseHarness abstraction (#780)
- Pi harness adoption of the same pattern (#838) — that's its own work item
- Removing `_apply_context_budget()` — keep as the first-turn safety net
- Refactoring `build_harness_turn_input()` beyond adding `skip_prefix`
- Touching the SDK path (`get_agent_response_sdk` / `_create_options`)
- Cross-process UUID coordination (Popoto handles it)
- Telemetry / metrics for resume hit-rate (worth tracking but not on the critical path; can be added in a follow-up chore)
- Migration of existing in-flight sessions — they will simply use the new path on their next turn after deploy; no backfill needed

## Update System

No update system changes required. The fix is purely internal to `agent/sdk_client.py` and `agent/agent_session_queue.py`. No new dependencies, no new config files, no new secrets. Existing deployments pick up the change on next `git pull && service restart`. The `/update` skill needs no changes.

## Agent Integration

No agent integration required. The change is internal to the dev-session execution path (the harness invocation itself), not the surface the bridge or MCP servers expose. The agent's tool list, `.mcp.json`, and `bridge/telegram_bridge.py` are untouched. The bridge will keep dispatching dev sessions exactly as today; only the subprocess argv shape changes downstream of dispatch.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` if it documents the harness invocation shape (verify and update — current text may describe the always-full-context flow).
- [ ] Add a new section "Harness Session Continuity" to `docs/features/pm-dev-session-architecture.md` (or create `docs/features/harness-session-continuity.md` and add it to `docs/features/README.md`) describing: (a) the two harness turn shapes (first-turn full-context vs. resumed with `--resume`), (b) the role of `claude_session_uuid` on `AgentSession`, (c) the fallback to full-context when UUID lookup misses, (d) why `_apply_context_budget()` is retained.

### External Documentation Site
No external doc site changes — repo doesn't ship Sphinx/MkDocs.

### Inline Documentation
- [ ] Docstring on `get_response_via_harness()` updated to document the `prior_uuid` and `session_id` keyword args, the `--resume` injection behavior, and the context-budget bypass on resumed turns.
- [ ] Docstring on `build_harness_turn_input()` updated to document the `skip_prefix` keyword arg.
- [ ] Inline comment at the new `_get_prior_session_uuid` call site in `_execute_agent_session` referencing this issue (#976) and PR #909 for the parallel SDK pattern.

## Success Criteria

- [ ] `get_response_via_harness()` stores the captured `session_id_from_harness` via `_store_claude_session_uuid()` after every successful turn (when called with `session_id`). This side effect is documented in the docstring and tests mock it.
- [ ] `_execute_agent_session()` passes `prior_uuid = _get_prior_session_uuid(session.session_id)`, `session_id`, and `full_context_message` to the harness. Both full-context and minimal messages are built at the call site.
- [ ] When `prior_uuid` is set and valid, the spawned argv contains `--resume <prior_uuid>` and the message arg is just the new user message (no reconstructed prefix)
- [ ] When `prior_uuid` is unset, empty, or fails UUID format validation, the spawned argv does not contain `--resume` and the message arg is the full reconstructed context (current behavior preserved)
- [ ] `_apply_context_budget()` is retained, applied on first-turn calls and on stale-UUID fallback retries, and bypassed on resumed turns
- [ ] Stale-UUID fallback is mandatory: when `--resume` fails with "requires a valid session", the function retries once using `full_context_message`
- [ ] A previously-overflowing session (long reply chain) successfully completes after this fix (manual verification with a reproducer thread)
- [ ] All new and updated unit tests pass (see Test Impact)
- [ ] New integration test (`tests/integration/test_harness_resume.py`) passes with the actual `claude` binary available
- [ ] All pre-existing tests continue to pass (`pytest tests/` exits 0)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No new lint or format violations (`python -m ruff check . && python -m ruff format --check .`)

## Team Orchestration

The build is small enough that one builder + one validator handles it cleanly.

### Team Members

- **Builder (harness-resume)**
  - Name: harness-resume-builder
  - Role: Implement the three surgical changes in `agent/sdk_client.py` and `agent/agent_session_queue.py`, plus the new and updated tests.
  - Agent Type: builder
  - Resume: true

- **Validator (harness-resume)**
  - Name: harness-resume-validator
  - Role: Verify all success criteria, run the full test suite, confirm no regression in `_apply_context_budget()` first-turn behavior, and verify the integration test exercises a real two-turn cycle.
  - Agent Type: validator
  - Resume: true

- **Documentarian (harness-resume)**
  - Name: harness-resume-doc
  - Role: Update the feature docs and inline docstrings as enumerated in the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement the three changes and unit tests
- **Task ID**: build-harness-resume
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py`, `tests/unit/test_cross_repo_gh_resolution.py`
- **Informed By**: Issue #976 Solution Sketch, PR #909 SDK-path pattern
- **Assigned To**: harness-resume-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `prior_uuid`, `session_id`, and `full_context_message` keyword-only params to `get_response_via_harness()` (`agent/sdk_client.py:1543`). Validate `prior_uuid` against UUID regex before injection. Inject `--resume <uuid>` into `cmd` when valid; bypass `_apply_context_budget()` on resumed turns; call `_store_claude_session_uuid()` after the `result` event when `session_id` is provided and the captured UUID is non-empty (document this as a side effect in the docstring). Implement mandatory stale-UUID fallback: if subprocess exits non-zero with "requires a valid session" in stderr, retry once using `full_context_message` with `_apply_context_budget()` applied.
- Add `skip_prefix` keyword-only param to `build_harness_turn_input()` (`agent/sdk_client.py:1725`); when True, return the raw message unchanged.
- In `_execute_agent_session()` (`agent/agent_session_queue.py:3727`), look up `_prior_uuid = _get_prior_session_uuid(session.session_id)` before constructing the turn input. **Always build the full-context message** via `build_harness_turn_input(skip_prefix=False)`. When `_prior_uuid` is set, also build the minimal message via `build_harness_turn_input(skip_prefix=True)`. Pass both to `get_response_via_harness(prior_uuid=_prior_uuid, session_id=session.session_id, full_context_message=_full_context_input)`.
- Treat empty-string `prior_uuid` as `None` inside `get_response_via_harness` (don't emit `--resume ""`). Treat UUID-format-invalid `prior_uuid` as `None` (log warning).
- Add the new unit tests enumerated in **Test Impact** to `tests/unit/test_harness_streaming.py`.
- Add the one new `skip_prefix` unit test to `tests/unit/test_cross_repo_gh_resolution.py`.

### 2. Create integration test
- **Task ID**: build-integration-test
- **Depends On**: build-harness-resume
- **Validates**: `tests/integration/test_harness_resume.py` (create)
- **Assigned To**: harness-resume-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/integration/test_harness_resume.py`.
- Test: two sequential `get_response_via_harness` calls on the same `session_id`. Assert the second call's argv contains `--resume <uuid>` from the first call's stored UUID, the message arg is short (just the new user message), and the second call returns a non-empty result.
- Gate on `shutil.which("claude")`; mark with `@pytest.mark.integration`.
- Verify the stale-UUID behavior empirically: pass a known-bad UUID and observe whether the binary errors or starts a new session. If it errors, add the fallback (catch and retry without `--resume`) to `get_response_via_harness` and add a regression test.

### 3. Validate
- **Task ID**: validate-harness-resume
- **Depends On**: build-harness-resume, build-integration-test
- **Assigned To**: harness-resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -k "harness or cross_repo_gh" -x -q`; assert all pass.
- Run `pytest tests/integration/test_harness_resume.py -x -q` (skip cleanly if `claude` binary not on PATH).
- Run the full suite `pytest tests/ -x -q`; assert no regressions.
- Read the diff in `agent/sdk_client.py` and `agent/agent_session_queue.py`; confirm no other files were touched.
- Verify `_apply_context_budget()` is still defined and still called on first-turn paths via grep.
- Report pass/fail with evidence (test counts, files touched).

### 4. Document
- **Task ID**: document-harness-resume
- **Depends On**: validate-harness-resume
- **Assigned To**: harness-resume-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` and/or `docs/features/pm-dev-session-architecture.md` (or create a new `docs/features/harness-session-continuity.md` and link it in `docs/features/README.md`) per the Documentation section.
- Update docstrings on `get_response_via_harness()` and `build_harness_turn_input()`.
- Add inline comment at the new `_get_prior_session_uuid` call site referencing #976 and PR #909.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-harness-resume
- **Assigned To**: harness-resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands below.
- Confirm every Success Criteria checkbox is satisfied.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Harness unit tests pass | `pytest tests/unit/test_harness_streaming.py -x -q` | exit code 0 |
| Cross-repo unit tests pass | `pytest tests/unit/test_cross_repo_gh_resolution.py -x -q` | exit code 0 |
| Integration test passes (when binary available) | `pytest tests/integration/test_harness_resume.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `--resume` injected when prior_uuid set | `grep -n '"--resume"' agent/sdk_client.py` | output contains `--resume` |
| Context budget retained | `grep -n 'def _apply_context_budget' agent/sdk_client.py` | output contains def |
| UUID storage call added in harness | `grep -n '_store_claude_session_uuid' agent/sdk_client.py` | output > 4 (2 existing + new) |
| UUID validation present | `grep -n 'uuid.UUID\|UUID_PATTERN' agent/sdk_client.py` | output > 0 |
| Stale-UUID fallback present | `grep -n 'requires a valid session' agent/sdk_client.py` | output > 0 |
| Prior UUID lookup added in dispatcher | `grep -n '_get_prior_session_uuid' agent/agent_session_queue.py` | output > 0 |
| Files touched (count) | `git diff --name-only main \| wc -l` | output ≤ 6 (the 2 source files, up to 3 test files, up to 1 docs file) |

## Critique Results

**Critique run:** 2026-04-15
**Critics:** Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings:** 5 total (1 blocker, 3 concerns, 1 nit)
**Revision applied:** 2026-04-15 — All findings addressed in plan text (see Change 1 side-effect note, Change 2 mandatory fallback + UUID validation, Data Flow step 3/4, Risk 1, Failure Path Test Strategy, Test Impact, Step by Step Tasks, Success Criteria).

### Blockers

#### Stale UUID causes hard error, not silent fallback
- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Risk 1 / Failure Path Test Strategy / "Error State Rendering"
- **Finding**: The plan states in Risk 1 that `claude -p --resume <stale_uuid>` "may either silently start a new session (best case) or error out (worst case)" and defers the determination to the integration test. Empirical testing (2026-04-15) confirms the binary **errors**: `Error: --resume requires a valid session ID or session title when used with --print. Provided value "nonexistent-uuid" is not a UUID and does not match any session title.` This is not a "may" -- it is the actual behavior. The plan's conditional fallback ("if the binary errors, add the fallback") must be unconditional.
- **Suggestion**: Promote the retry-without-`--resume` fallback from conditional to mandatory in the implementation. In `get_response_via_harness()`, when `prior_uuid` is set and the process exits with non-zero return code AND stderr contains "resume" or "session", retry the call once without `--resume` (full first-turn path). Add this as an explicit sub-step in Task 1 rather than leaving it to discovery during the integration test.
- **Implementation Note**: After `proc.communicate()`, if `proc.returncode != 0` and `prior_uuid` is set, check `stderr_text` for the substring `"--resume"`. If found, log a warning (`"Stale UUID {prior_uuid}, falling back to first-turn path"`), then re-enter the function recursively (or inline) with `prior_uuid=None` — which means re-applying `_apply_context_budget()` to the original full message. The caller must pass the original un-skipped message for this fallback, so `get_response_via_harness` needs access to both the full message and the minimal message, or the retry must reconstruct the full-context path. Simplest approach: accept both `message` (always the full context) and `prior_uuid`; when `prior_uuid` is set, ignore `message` and use a separately-passed `resume_message` (just the new user text). On fallback, use `message` with `_apply_context_budget()`.

### Concerns

#### UUID validity not checked before subprocess spawn
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Solution / Change 2
- **Finding**: The plan injects `--resume <prior_uuid>` directly into the subprocess argv without validating that `prior_uuid` is a well-formed UUID. If the Popoto record contains a corrupted or non-UUID string (e.g., from a bug in a future code path), this could cause unexpected CLI behavior or argument injection.
- **Suggestion**: Add a UUID format check (regex or `uuid.UUID()` parse) before injecting `--resume`. If the value is not a valid UUID, treat it as `None` and take the first-turn path.
- **Implementation Note**: Guard in `get_response_via_harness()` right after the empty-string check: `try: uuid.UUID(prior_uuid); except ValueError: prior_uuid = None`. Import `uuid` from stdlib. This is a 3-line defensive check that prevents argument injection and corrupted-data issues.

#### Dual message paths require careful orchestration at call site
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Solution / Change 3 / `_execute_agent_session` call site
- **Finding**: When `prior_uuid` is set, `build_harness_turn_input(skip_prefix=True)` returns just the raw message, and this is what gets passed to `get_response_via_harness()`. But if the stale-UUID fallback fires (see Blocker above), the function needs the full-context message to retry. The plan does not specify how the full-context message is preserved for the fallback path.
- **Suggestion**: Always call `build_harness_turn_input()` with `skip_prefix=False` to get the full context message. Pass both the full message and the minimal message (just `_turn_input`) to `get_response_via_harness()`. On first attempt with `--resume`, use the minimal message. On fallback, use the full message with `_apply_context_budget()`.
- **Implementation Note**: In `_execute_agent_session()`, always build `_harness_input_full = await build_harness_turn_input(skip_prefix=False, ...)`. When `_prior_uuid` is set, also prepare `_harness_input_minimal = _turn_input` (the raw steering/user message). Pass both to `get_response_via_harness(message=_harness_input_full, resume_message=_harness_input_minimal, prior_uuid=_prior_uuid, ...)`. This ensures the fallback path has the full context without needing to call `build_harness_turn_input` again.

#### `get_response_via_harness` return type is `str` but UUID must be persisted
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Solution / Change 1
- **Finding**: The plan adds `session_id` as a parameter to `get_response_via_harness()` and calls `_store_claude_session_uuid()` inside the function. This couples a side-effecting Popoto write into a function whose current contract is "run CLI, return text." The function currently returns `str`; callers (including tests) expect pure string return. Embedding the store call inside means tests of `get_response_via_harness` now need Popoto/Redis mocked or available.
- **Suggestion**: This is acceptable given the existing pattern (the SDK path does the same inside `get_agent_response_sdk`), but the docstring and test setup must explicitly note the side effect. Ensure the new unit tests mock `_store_claude_session_uuid` to verify it is called without requiring Redis.
- **Implementation Note**: In each new test case in `test_harness_streaming.py`, patch `agent.sdk_client._store_claude_session_uuid` as a `MagicMock` and assert `mock.assert_called_once_with(session_id, expected_uuid)`. The mock prevents Redis dependency in unit tests. The side effect is already fail-silent (line 228 `except Exception`), so even if the mock is misconfigured the test won't hang.

### Nits

#### Redundant success criteria
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Success Criteria
- **Finding**: "Tests pass (`/do-test`)" and "All pre-existing tests continue to pass (`pytest tests/` exits 0)" are redundant -- `/do-test` runs `pytest tests/`. Similarly "No new lint or format violations" is covered by `/do-test` which runs ruff.
- **Suggestion**: Consolidate into a single "All tests, lint, and format checks pass" criterion to reduce noise.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All 4 required sections present and non-empty |
| Task numbering | PASS | Tasks 1-5, sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references resolve to defined Task IDs |
| File paths exist | PASS | 10 of 10 referenced source files exist; `tests/integration/test_harness_resume.py` correctly marked as CREATE |
| Prerequisites met | PASS/WARN | `claude` binary on PATH (PASS); `--resume` flag recognized (PASS but errors on invalid UUID -- see Blocker); Popoto not tested |
| Cross-references | PASS | All success criteria map to tasks; no No-Gos appear in Solution as planned work |

### Verdict

**READY TO BUILD (with concerns)** -- No BLOCKERs after the stale-UUID fallback is promoted from conditional to mandatory. The 1 BLOCKER finding identifies behavior that the plan already anticipated as a possibility but must now be treated as certain (empirically confirmed). A revision pass should embed the Implementation Notes from the 1 blocker and 3 concerns into the plan text before build proceeds.

---

## Open Questions

None. The issue body fully specifies scope, files, acceptance criteria, and constraints. The plan's only judgment call is the empirical stale-UUID behavior (Risk 1), which is resolved during build via the integration test and conditionally adds the fallback if needed — no human input required up front.
