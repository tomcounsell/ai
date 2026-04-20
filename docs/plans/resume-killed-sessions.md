---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1061
last_comment_id:
revision_applied: true
---

# valor-session resume: support killed/failed sessions and dual agent_session_id lookup

## Problem

When an AgentSession is killed (by the operator) or fails (crash during execution), the operator has no recovery path. `valor-session resume --id <id>` rejects every non-`completed` session with a hard error; the session's transcript is still on disk but unreachable. The operator must create a fresh session, losing all prior context.

Additionally, the Claude Code CLI displays `agent_session_id` (a UUID like `c00fd40d7a10432ba38b52bead17061f`) as "Session ID" in its session header. When an operator copies that UUID from the terminal and passes it to `valor-session status/inspect/resume/steer/kill --id <uuid>`, the CLI returns "not found" because every subcommand looks up by `session_id` only.

**Current behavior:**

- `tools/valor_session.py:299-305` — `cmd_resume` rejects any status other than `"completed"`.
- `agent/sdk_client.py:170-172` — `_get_prior_session_uuid()` filters to `{completed, running, active, dormant}`. A killed/failed session's `claude_session_uuid` is invisible, so even if the session is re-enqueued the worker starts a fresh conversation.
- `tools/valor_session.py` subcommands (`cmd_status`, `cmd_inspect`, `cmd_resume`, `cmd_steer`, `cmd_kill`) all call `AgentSession.query.filter(session_id=args.id)` — no fallback to `agent_session_id`.

**Desired outcome:**

- `valor-session resume --id <id> --message "..."` works for killed and failed sessions that have a stored `claude_session_uuid`; the worker picks up the resumed session and calls `claude -p --resume <uuid>`. Output routes back through the same `TelegramRelayOutputHandler` via the persisted `chat_id` field.
- Clear error when `claude_session_uuid` is None: `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"`.
- `valor-session status/inspect/resume/steer/kill --id <id>` accepts both `session_id` and `agent_session_id` (UUID) as lookup keys.
- `CLAUDE.md` quick-reference row updated to reflect expanded support.

## Freshness Check

**Baseline commit:** `ccdc10ac048c613dd9376a8fed1f32b2b0018fb4`
**Issue filed at:** `2026-04-20T03:24:20Z`
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/valor_session.py:299-305` — "Only completed sessions can be resumed" guard — still holds verbatim.
- `agent/sdk_client.py:170-172` — `_get_prior_session_uuid()` status filter `("completed", "running", "active", "dormant")` — still holds verbatim at lines 171-172.
- `tools/valor_session.py` — `cmd_status` at line 386, `cmd_inspect` at line 477, `cmd_resume` at line 277, `cmd_steer` via `steer_session()` helper at line 362, `cmd_kill` at line 708 — all still query `session_id` only.

**Cited sibling issues/PRs re-checked:**
- #900 / PR #909 — merged 2026-04-13, introduced `retain_for_resume` + hard-PATCH resume. Resolution intact.
- PR #981 — cited in issue; did not change the status guard. Still a separate concern.
- PR #922 — cited in issue; deterministic reply-to cache. Unrelated to the guard.

**Commits on main since issue was filed (touching referenced files):**
- `7c19c9a7` chore: remove parent_session_id aliases (issue #1025) (#1062) — touched `tools/valor_session.py` but only removed the deprecated `parent_session_id` alias path; did not modify any `cmd_*` lookup or the `cmd_resume` status guard.

**Active plans in `docs/plans/` overlapping this area:** none (scan of `ls -lt docs/plans/*.md` showed no active plans touching `tools/valor_session.py` resume path or `agent/sdk_client.py` UUID lookup).

**Notes:** Line numbers from the issue body remain accurate on the baseline commit. No drift — proceed.

## Prior Art

- **Issue #900 / PR #909**: "SDLC stage model selection and hard-PATCH builder session resume" — introduced the `retain_for_resume` flag, `claude_session_uuid` storage, and the initial `valor-session resume` command. This is the foundation; the current issue extends its coverage from `completed` to include `killed`/`failed`.
- **Issue #374 / related PR**: "Observer returns early on continuation sessions due to session cross-wire" — motivated `_get_prior_session_uuid()`'s existence. The current status filter was chosen to prevent reusing UUIDs from abandoned/failed sessions; we're revising that assumption now that `retain_for_resume` + TTL control session longevity.
- **Issue #730**: "Session re-enqueue loop: intake path missing terminal-status guard" — the inverse problem (cycling completed sessions). That fix guards the intake path; our fix expands eligibility for explicit operator-initiated resume. No conflict.
- **PR #981**: Fixed session continuity via `--resume` with unconditional context budget — unrelated to the status guard.
- **PR #922**: Deterministic reply-to root cache + completed session resume — unrelated.

## Research

No relevant external findings — the work is purely internal (Popoto ORM queries, argparse CLI, Claude Code SDK wiring). Training data and codebase context are sufficient.

## Data Flow

Killed-session resume, end-to-end:

1. **Entry point**: Operator runs `valor-session resume --id <id> --message "..."`.
2. **`cmd_resume`** (`tools/valor_session.py`): Resolves the session via `_find_session(id_arg)` (new helper — tries `session_id` first, then `agent_session_id`). Checks status is in `{completed, killed, failed}`. Checks `claude_session_uuid` is non-null. Appends message to `queued_steering_messages`. Transitions status `pending` via `transition_status(..., reject_from_terminal=False)`. Saves.
3. **Worker** (`worker/__main__.py`): Picks up the pending session from the queue. Calls `_execute_agent_session()`.
4. **`_create_options`** (`agent/sdk_client.py`): Calls `_get_prior_session_uuid(session_id)`. That function's status filter now includes `killed` and `failed`, so the stored UUID is returned. Sets `continue_conversation=True`, `resume=<uuid>`.
5. **Claude Code SDK**: Runs `claude -p --resume <uuid>`, replaying the prior transcript, injecting the new steering message.
6. **Output routing**: `TelegramRelayOutputHandler` reads `session.chat_id` (unchanged on resume) and writes output to the Redis outbox for Telegram delivery to the correct thread.

Dual-id lookup, end-to-end:

1. Operator copies `agent_session_id` (UUID) from Claude Code CLI header.
2. Runs `valor-session status --id <uuid>`.
3. `_find_session(uuid)` calls `AgentSession.query.filter(session_id=uuid)` → empty. Falls back to `AgentSession.get_by_id(uuid)` (the canonical UUID lookup helper that already exists at `models/agent_session.py:563`). Returns the session.
4. `cmd_status` proceeds with normal rendering.

## Why Previous Fixes Failed

The current issue is not a "previous fix failed" scenario — PR #909 shipped the intended behavior for `completed` sessions, and that behavior is still correct. The current work *extends* the supported statuses. There's no failure pattern to analyze; the original author simply did not anticipate operator-initiated resume of killed sessions (a workflow that only became important as PM sessions became longer-running and more likely to be manually killed mid-work).

## Architectural Impact

- **New dependencies**: None. Uses existing `AgentSession.get_by_id()` helper for the dual-id path.
- **Interface changes**: `valor-session resume` now accepts `killed`/`failed` sessions (additive — no existing invocation pattern breaks). All five subcommands (`status`, `inspect`, `resume`, `steer`, `kill`) accept an additional lookup form (additive — existing `session_id` invocations unchanged).
- **Coupling**: No change. `sdk_client.py` and `valor_session.py` already know about the same `AgentSession` model and the same `claude_session_uuid` field.
- **Data ownership**: No change. `AgentSession` still owns all session state.
- **Reversibility**: Fully reversible — revert the status-filter tuple change and the `_find_session` helper.

**Revision note (from CRITIQUE, Archaeologist):** The `_get_prior_session_uuid` status-filter relaxation is the one change that touches the defensive perimeter of issue #374 (session cross-wire). On revert, revert the filter-tuple change first; `_find_session` can stay in place harmlessly (the primary `session_id` lookup returns before the fallback ever fires on legacy inputs). Record this two-step rollback order in the PR description so an on-call reviewer doesn't have to reconstruct it: *"If regression spotted, revert the one-line filter change in `agent/sdk_client.py` first. `_find_session` is additive — it only alters behavior on previously-failing lookups (UUID form), so it can remain."*

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Four surgical edits across two files plus a CLAUDE.md row update. No new abstractions, no schema changes, no new tests of existing behavior. The bottleneck is code review, not implementation time.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Relaxed status guard in `cmd_resume`**: accept `completed | killed | failed` instead of `completed` only. Add explicit pre-flight check that `claude_session_uuid` is non-null, with a clear error if it isn't.
- **Expanded status filter in `_get_prior_session_uuid`**: include `killed` and `failed` so the worker can find the stored UUID after resume.
- **Dual-id lookup helper `_find_session`**: try `session_id` first, fall back to `agent_session_id` via `AgentSession.get_by_id()`. Call from all five subcommands.
- **CLAUDE.md quick-reference update**: document that `resume` now supports killed/failed sessions.

### Flow

**Operator sees killed session** → `valor-session status --id <uuid>` (works via dual-id) → inspects session → `valor-session resume --id <uuid> --message "..."` → session transitions to pending → worker picks up → `claude -p --resume <uuid>` → output delivered via persisted `chat_id` → operator sees result in Telegram thread.

### Technical Approach

**Revision note (from CRITIQUE, Adversary — line drift):** Every file:line anchor cited below and in the Test Impact / Freshness Check sections is a **hint, not a target**. Between plan-write time and build time, other PRs may land that shift line numbers. Before editing each region, re-locate it by content pattern. The builder MUST run this pre-edit grep set in Step 1 and edit whatever the greps find, regardless of whether the current line numbers match this plan's citations:

```bash
grep -n "Only completed sessions can be resumed\|def cmd_resume\|def cmd_status\|def cmd_inspect\|def cmd_kill\|def cmd_steer" tools/valor_session.py
grep -n "def _get_prior_session_uuid\|\"completed\", \"running\", \"active\", \"dormant\"" agent/sdk_client.py
grep -n "def get_by_id" models/agent_session.py
```

At plan-write verification time the relevant anchors were: `cmd_resume:268`, error string `:311`, `cmd_steer:365`, `cmd_status:389`, `cmd_inspect:478`, `cmd_kill:685`, `_get_prior_session_uuid:152`, status filter `:171`, `get_by_id:516`. If the builder's grep returns different numbers, trust the grep.

1. **`tools/valor_session.py` — add `_find_session` helper** near the other private helpers (before `cmd_resume`):
   ```python
   def _find_session(id_arg: str) -> "AgentSession | None":
       """Resolve a session by session_id first, then agent_session_id (UUID)."""
       from models.agent_session import AgentSession
       sessions = list(AgentSession.query.filter(session_id=id_arg))
       if sessions:
           sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
           return sessions[0]
       return AgentSession.get_by_id(id_arg)
   ```
   Return the newest record by `created_at` to match the existing `cmd_resume` convention (handles session_id reuse across runs).

   **Revision note (from CRITIQUE, Skeptic):** Every UUID-form lookup pays the cost of one empty `AgentSession.query.filter(session_id=<uuid>)` before the `get_by_id` fallback fires. At operator-initiated CLI rates (a handful of `valor-session` invocations per day) this cost is imperceptible. An alternative — sniffing the id shape (32-char hex → UUID path, anything else → session_id path) — would shave the extra query but introduce a format-guessing heuristic that breaks if session_id conventions ever change. Accept the overhead; name it in the docstring: *"We try session_id first because it is the canonical routing key. UUID lookups pay one empty query before the fallback — at CLI invocation rates this is imperceptible."*

   **Revision note (from CRITIQUE, Simplifier):** `_find_session` is a five-line helper with five call sites in one file. Keep it inline in `tools/valor_session.py`; do NOT promote to a shared module (`agent/agent_session_queue.py`, a new `tools/_session_lookup.py`, or similar). Promotion adds surface area and cross-file cognitive load for zero gain. The docstring should describe (a) the `session_id`-first ordering, (b) the `created_at desc` tiebreaker for session_id collisions, and (c) that UUID lookups go through the canonical `AgentSession.get_by_id()` helper — nothing more.

   **Revision note (from CRITIQUE, Adversary):** The plan asserts "no collision possible" between `session_id` and `agent_session_id` values because session_ids are human-readable (e.g., `0_1776653716603`, `tg_valor_-1003449100931_686`) while agent_session_ids are 32-char hex UUIDs. This holds under **current** naming conventions but is not a hard invariant. Document the assumption in the helper's docstring: *"Assumption: session_id values never collide with 32-char hex agent_session_id values. Current session_id formats (`{chat_id}_{message_id}`, `tg_{project}_{chat_id}_{message_id}`, `sdlc-local-{issue}`) satisfy this trivially. If a future session_id scheme produces 32-char hex values, the session_id-first ordering will still return the correct session — but callers passing an agent_session_id that coincidentally matches a session_id would receive the wrong record."* No runtime validation; docstring-only guard.

2. **`tools/valor_session.py` — `cmd_resume`**:
   - Replace the direct `AgentSession.query.filter(session_id=session_id)` lookup with `_find_session(session_id)`.
   - Relax the status guard: change `if current_status != "completed":` to `if current_status not in ("completed", "killed", "failed"):`. Update the error message to: `"has status '{current_status}'. Only completed/killed/failed sessions can be resumed."`.
   - Keep the existing `pending` and `running` guard branches — those still apply.
   - Add a pre-flight check immediately before the steering-message block: `if getattr(session, "claude_session_uuid", None) is None: print("Error: cannot resume: no transcript UUID stored (session was killed before first turn completed)", file=sys.stderr); return 1`.

3. **`tools/valor_session.py` — `cmd_status`, `cmd_inspect`, `cmd_kill`**:
   - Replace the `AgentSession.query.filter(session_id=args.id)` block with `session = _find_session(args.id)` + `if not session: print(f"Session not found: {args.id}", file=sys.stderr); return 1`.
   - `cmd_kill` `--all` path is unchanged (no id argument).

4. **`tools/valor_session.py` — `cmd_steer`**:
   - `cmd_steer` delegates to `agent.agent_session_queue.steer_session(args.id, args.message)`. The right layering is to fix it at the CLI boundary: resolve the id in `cmd_steer`, then call `steer_session(session.session_id, args.message)` so the queue helper continues to operate on canonical `session_id`. This keeps the queue helper's contract unchanged.

   **Revision note (from CRITIQUE, Operator):** The dual-id lookup must land in **all five** subcommands (`cmd_resume`, `cmd_status`, `cmd_inspect`, `cmd_steer`, `cmd_kill`) in the **same commit**. Forgetting one leaves latent operator confusion: `valor-session status --id <uuid>` would work but `valor-session kill --id <uuid>` wouldn't. Step 1's task list enumerates all five explicitly; Step 2 (validate) must smoke-test each path (at minimum: `status --id <agent_session_id>`, then `kill` by the same id on the same test session) before declaring done. Validator's `grep -n "query.filter(session_id=args.id)" tools/valor_session.py` must return zero matches after the change — any remaining use of the raw filter indicates a missed subcommand.

5. **`agent/sdk_client.py` — `_get_prior_session_uuid`**:
   - Change the status filter tuple from `("completed", "running", "active", "dormant")` to `("completed", "running", "active", "dormant", "killed", "failed")`.
   - No other changes — the sort-by-`created_at`-desc logic already picks the right record.

   **Revision note (from CRITIQUE, Archaeologist):** This filter was originally narrow to prevent the cross-wire class of bug traced in issue #374. Relaxing it must be paired with an explicit rationale for why the remaining defenses are sufficient. Encode that rationale in the updated docstring so the next maintainer can find it without git archaeology: *"killed/failed included since #1061. The original narrow filter defended against fresh-session UUID reuse (#374). Today the primary defense is keying the lookup on `session_id` (so only this thread's records are considered) and `created_at desc` sort (so an ancient killed record cannot shadow a newer completed one). Killed/failed sessions are included because operator-initiated resume (`valor-session resume`) is explicitly asking for this specific transcript to continue; the UUID it needs is valid until the transcript file is cleaned up on disk."*

6. **`CLAUDE.md` — quick-reference table**:
   - Update the row `| \`python -m tools.valor_session resume --id <ID> --message "..."\` | Resume a completed BUILD session (hard-PATCH path) |` to `| \`python -m tools.valor_session resume --id <ID> --message "..."\` | Resume a completed, killed, or failed session (hard-PATCH path; accepts session_id or agent_session_id) |`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_find_session` does not introduce new `except Exception: pass` blocks. `AgentSession.get_by_id()` already logs via `logger.warning` on lookup failure (see `models/agent_session.py:585-591`) — no change needed.
- [ ] `_get_prior_session_uuid`'s existing `except Exception:` block (logs via `logger.warning` with `exc_info=True`) is untouched; behavior validated by the existing path.

### Empty/Invalid Input Handling

- [ ] New test: `cmd_resume` with a session whose `claude_session_uuid` is `None` → exits with `"cannot resume: no transcript UUID stored"` on stderr, returns 1.
- [ ] New test: `_find_session("")` returns `None` (via `get_by_id`'s existing empty-string guard at `models/agent_session.py:581`).
- [ ] New test: `_find_session("nonexistent-id")` returns `None`.

### Error State Rendering

- [ ] `cmd_resume` error messages already print to `sys.stderr` with return code 1 — pattern preserved.
- [ ] New test asserts the exact error string for the "no transcript UUID" path so future refactors don't silently change operator-facing output.

**Revision note (from CRITIQUE, User/PM):** The two new error strings — `"has status '{current_status}'. Only completed/killed/failed sessions can be resumed."` and `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"` — are operator-facing. Once shipped, operators will grep for these strings in docs, runbooks, and their own shell history. The unit tests MUST assert the exact strings (not regex-match substrings) so a future refactor that "cleans up" the wording fails loudly rather than silently fragmenting the operator vocabulary. Keep the assertion helper tight: `assert captured.err.strip() == expected_message` rather than `expected_message in captured.err`.

## Test Impact

- [ ] `tests/unit/test_valor_session.py` (or nearest existing test file for the CLI) — ADD: unit tests for `_find_session` (dual-id resolution), `cmd_resume` with killed/failed status, `cmd_resume` with missing UUID.
- [ ] `tests/unit/test_sdk_client.py` (or nearest existing test for `_get_prior_session_uuid`) — ADD: unit test confirming `killed` and `failed` statuses return the stored UUID.
- [ ] Verify `grep -rn "Only completed sessions can be resumed" tests/` returns no matches — the old error string is not hard-coded in any existing test.

No existing tests are broken by this change. The CLI's existing `session_id` lookup path stays identical on the happy path — the new fallback only fires when the primary lookup returns empty.

## Rabbit Holes

- **Reworking `_get_prior_session_uuid` to use `agent_session_id` as the primary key**: Tempting because `agent_session_id` is unambiguous, but `session_id` is the canonical routing key the worker and bridge use everywhere. Changing the primary lookup would ripple into PM/child linkage, cross-wire detection, and the Claude Code UUID-to-transcript mapping. Out of scope.
- **Generalizing `_find_session` into a shared module**: With only five call sites in one file, inlining the helper in `tools/valor_session.py` is the right size. Promoting to `agent/agent_session_queue.py` or a new `tools/_session_lookup.py` adds surface area for no gain.
- **Dashboard/UI changes to expose `agent_session_id`**: The CLI is the primary consumer; the dashboard already shows both IDs. No UI change needed.
- **Resurrecting sessions with null `claude_session_uuid`** (i.e., killed-before-first-turn): Would require spawning a fresh Claude Code session while preserving AgentSession metadata. This is a different feature; ship the hard failure message and revisit later if operators request it.

## Risks

### Risk 1: Stale UUIDs resurface from abandoned sessions
**Impact:** A killed session whose `claude_session_uuid` points to a transcript that has since been deleted by filesystem cleanup would cause `claude -p --resume <uuid>` to fail.
**Mitigation:** The Claude Code SDK already handles missing transcript files by returning a clear error. Operator sees the failure and falls back to creating a fresh session. No silent corruption. Documented in the `no transcript UUID stored` error message — operators learn that missing UUID = fresh session required.

### Risk 2: Dual-id lookup creates ambiguity if a session_id coincidentally matches an agent_session_id
**Impact:** None in practice — `session_id` values are human-readable (e.g., `0_1776653716603`, `tg_valor_-1003449100931_686`) while `agent_session_id` values are 32-char hex UUIDs. No collision possible.
**Mitigation:** `_find_session` tries `session_id` first so the more specific match always wins. Document this ordering in the helper docstring.

### Risk 3: Expanding `_get_prior_session_uuid` to killed/failed reintroduces issue #374 (session cross-wire)
**Impact:** Issue #374 was about fresh sessions reusing stale session files on disk. The status filter was one defense.
**Mitigation:** The primary defense in #374 was using the stored `claude_session_uuid` (not the most-recent-file-on-disk fallback). That defense is unchanged. The status filter was a secondary belt-and-braces check; relaxing it is safe because:
   1. The lookup is still keyed by `session_id`, so only records for this specific conversation thread are considered.
   2. The `created_at desc` sort picks the newest record, preventing an ancient killed session from shadowing a newer completed one.
   3. Operators invoking `valor-session resume --id <killed-id>` are *explicitly* asking for this exact transcript to continue.

## Race Conditions

### Race 1: Concurrent resume + worker cleanup
**Location:** `tools/valor_session.py:307-325` (cmd_resume steering + transition)
**Trigger:** Operator runs `valor-session resume` on a killed session while a background process (e.g., cleanup job) is inspecting or modifying the same record.
**Data prerequisite:** The session's `queued_steering_messages` list must include the new message before the `pending` status transition.
**State prerequisite:** No other process concurrently transitions the same session.
**Mitigation:** The existing `cmd_resume` code already stages the steering message before the status transition (see comment at lines 307-315, "Stage steering message BEFORE transitioning to pending"). `transition_status(..., reject_from_terminal=False)` is atomic at the Redis level. No new race introduced.

### Race 2: Dual-id lookup returns different sessions on retry
**Location:** `_find_session` helper
**Trigger:** A session's `session_id` is created while the helper is resolving; the second call returns a newer match.
**Data prerequisite:** None.
**State prerequisite:** None.
**Mitigation:** Each CLI invocation calls `_find_session` exactly once. No retry loop. Idempotent from the caller's perspective.

## No-Gos (Out of Scope)

- Resurrecting sessions with null `claude_session_uuid` (killed before first turn).
- Resume for sessions in non-terminal statuses other than `completed` (i.e., don't try to revive `running`, `active`, `paused_circuit`, etc.). The existing guards for `pending`/`running` stay. `dormant` and `paused*` states are out of scope — they're expected-to-resume-by-themselves states, not operator-revival states.
- Changes to `TelegramRelayOutputHandler` or bridge routing — `chat_id` is already persisted and the routing path is unchanged.
- Dashboard UI changes.
- Any rename/refactor of `session_id` vs `agent_session_id` semantics.

## Update System

No update system changes required — this feature is purely internal to the CLI and worker, no new deps, no config, no migration. Deployment is a normal `git pull && ./scripts/valor-service.sh restart`.

## Agent Integration

No agent integration required — `valor-session` is an operator-facing CLI, not an agent-facing tool. The agent does not invoke `valor-session resume`. Bridge changes: none. `.mcp.json` changes: none.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/pm-dev-session-architecture.md` — add a short note in the "Resume Semantics" section (if present) or append a paragraph clarifying that killed/failed sessions are now resumable with a stored `claude_session_uuid`.
- [ ] If a `docs/features/session-lifecycle.md` or similar exists, update the row for `killed`/`failed` states to mention operator-initiated resume.
- [ ] No new feature doc needed — this is a CLI enhancement, not a new feature.

### External Documentation Site

- [ ] N/A — this repo does not publish a Sphinx/MkDocs/RtD site.

### Inline Documentation

- [ ] Docstring on new `_find_session` helper documents the session_id-first ordering and the `agent_session_id` fallback.
- [ ] Updated docstring on `cmd_resume` mentions `killed`/`failed` support.
- [ ] Updated docstring on `_get_prior_session_uuid` explains why killed/failed are now included (cross-reference this plan + #374).

## Success Criteria

- [ ] `valor-session resume --id <killed-session-id> --message "..."` succeeds when `claude_session_uuid` is non-null; worker picks up session and calls `claude -p --resume <uuid>`.
- [ ] `valor-session resume --id <killed-session-id>` exits 1 with `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"` on stderr when `claude_session_uuid` is None.
- [ ] `valor-session resume --id <failed-session-id>` succeeds when `claude_session_uuid` is non-null.
- [ ] `valor-session status --id <agent_session_id>` (UUID form) returns the session correctly.
- [ ] `valor-session inspect/steer/kill --id <agent_session_id>` (UUID form) work correctly.
- [ ] Completed-session resume (the existing happy path) is unchanged — no regression.
- [ ] A resumed killed bridge session (with `chat_id` set) delivers its final output to the correct Telegram thread. Verify by running a smoke test: create a PM session, kill it mid-work, resume it, confirm the resume message and output appear in the right Telegram thread.
- [ ] `CLAUDE.md` quick-reference table updated.
- [ ] Tests pass (`pytest tests/unit/test_valor_session.py tests/unit/test_sdk_client.py`, or the nearest test file).
- [ ] Lint clean (`python -m ruff check . && python -m ruff format --check .`).

## Team Orchestration

Small plan — single builder, no parallel components.

### Team Members

- **Builder (cli-resume)**
  - Name: resume-builder
  - Role: Implement the four surgical edits per Technical Approach, add the unit tests per Test Impact
  - Agent Type: builder
  - Resume: true

- **Validator (cli-resume)**
  - Name: resume-validator
  - Role: Verify all success criteria, run targeted pytest + ruff, sanity-check the CLAUDE.md edit
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `_find_session` helper and update `cmd_resume`
- **Task ID**: build-cli-resume
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session.py` (ADD cases for dual-id + killed/failed + null UUID), `tests/unit/test_sdk_client.py` (ADD case for killed/failed UUID retrieval)
- **Informed By**: none
- **Assigned To**: resume-builder
- **Agent Type**: builder
- **Parallel**: false
- **Revision note (from CRITIQUE, Adversary):** Before editing any file, run the pre-edit grep set from Technical Approach to re-locate every region by content pattern. Line-number citations in this task list are hints; trust what the grep finds:
  - `grep -n "Only completed sessions can be resumed\|def cmd_resume\|def cmd_status\|def cmd_inspect\|def cmd_kill\|def cmd_steer" tools/valor_session.py`
  - `grep -n "def _get_prior_session_uuid\|\"completed\", \"running\", \"active\", \"dormant\"" agent/sdk_client.py`
  - `grep -n "def get_by_id" models/agent_session.py`
- Add `_find_session` helper in `tools/valor_session.py` per Technical Approach section.
- Update `cmd_resume`: use `_find_session`, relax status guard to `{completed, killed, failed}`, add null-UUID pre-flight with the exact error message `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"`.
- Update `cmd_status`, `cmd_inspect`, `cmd_kill` to use `_find_session`.
- Update `cmd_steer` to resolve via `_find_session` then delegate to `steer_session` with canonical `session_id`.
- Update `_get_prior_session_uuid` in `agent/sdk_client.py`: expand status filter tuple to include `"killed"` and `"failed"`.
- Update the `valor-session resume` row in `CLAUDE.md` to describe the expanded support.
- Update docstrings per the Documentation section.
- Add unit tests covering all Success Criteria bullets.

### 2. Validate implementation
- **Task ID**: validate-cli-resume
- **Depends On**: build-cli-resume
- **Assigned To**: resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_session.py tests/unit/test_sdk_client.py -v`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Verify `grep -n "Only completed sessions can be resumed" tools/valor_session.py` returns no matches (old string gone).
- Verify `grep -n "killed.*failed" agent/sdk_client.py` returns the expanded filter.
- Verify the CLAUDE.md row was updated.
- **Revision note (from CRITIQUE, Operator — symmetry check):** Verify `grep -n "AgentSession.query.filter(session_id=args.id)" tools/valor_session.py` returns **zero matches** after the change. Any remaining direct-filter-on-args.id indicates a subcommand that was not migrated to `_find_session`. If matches remain, the builder missed one of the five subcommands — fail validation and send back to build.
- Smoke test the dual-id lookup across **all five** subcommands using a single test AgentSession (create, then run `status`, `inspect`, `steer`, `resume`, `kill` by `--id <agent_session_id>`). Each must resolve the session. Clean up the test record per the manual-testing hygiene rules in CLAUDE.md.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cli-resume
- **Assigned To**: resume-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md` (or nearest applicable) with a short paragraph on killed/failed resume.
- Confirm CLAUDE.md quick-reference table entry is present and accurate.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-cli-resume, validate-cli-resume, document-feature
- **Assigned To**: resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the targeted pytest + ruff again.
- Walk through each Success Criterion and confirm.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted unit tests pass | `pytest tests/unit/test_valor_session.py tests/unit/test_sdk_client.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Old error string removed | `grep -n "Only completed sessions can be resumed" tools/valor_session.py` | exit code 1 |
| Status filter expanded | `grep -n '"killed"' agent/sdk_client.py` | output contains killed |
| CLAUDE.md updated | `grep -n "killed, or failed session" CLAUDE.md` | output > 0 |

## Critique Results

Verdict: **READY TO BUILD (with concerns)**. Critique was run on 2026-04-20 by `/do-plan-critique`; the structured findings table was not persisted by the critic run, so the findings below are a revision-pass self-critique distilled from the verdict and the plan's surface area. All concerns are acknowledged risks and clarifications — none are blockers. The table stays in-document for build-time reference.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | `_find_session` pays one empty `AgentSession.query.filter(session_id=<uuid>)` before the `get_by_id` fallback fires. A reviewer could reasonably ask why we don't sniff the id shape (32-char hex → UUID path, anything else → session_id path) to skip the empty query. | Technical Approach (Step 1) | Accept the overhead; name it in the helper docstring. Format-sniffing would introduce a guessing heuristic that breaks if session_id conventions change. At CLI invocation rates (a handful per day) the cost is imperceptible (one indexed lookup against local Redis). |
| CONCERN | Operator | The dual-id lookup must land in **all five** subcommands (`cmd_resume`, `cmd_status`, `cmd_inspect`, `cmd_steer`, `cmd_kill`) in the **same commit**. Forgetting one leaves latent operator confusion — e.g., `valor-session status --id <uuid>` works but `valor-session kill --id <uuid>` doesn't. | Step 1 (task list enumerates all five); Step 2 (symmetry grep) | Validator's `grep -n "AgentSession.query.filter(session_id=args.id)" tools/valor_session.py` must return zero matches after the change. Smoke test must exercise all five subcommands by UUID on a single test session. If any match remains, fail validation. |
| CONCERN | Archaeologist | `_get_prior_session_uuid`'s status filter was originally narrow to defend against issue #374 (session cross-wire). Relaxing it must be paired with an explicit rationale for why the remaining defenses are sufficient, and a clear rollback order. | Technical Approach (Step 5); Architectural Impact (Reversibility) | Update the `_get_prior_session_uuid` docstring to document why the narrower filter was chosen originally and why keying on `session_id` + `created_at desc` is a sufficient secondary defense. Record a two-step rollback order in the PR description: revert the filter-tuple change first (one line in `agent/sdk_client.py`); `_find_session` is additive and can remain in place. |
| CONCERN | Adversary (line drift) | Plan cites specific line numbers (`tools/valor_session.py:268`, `:311`, `:365`, `:389`, `:478`, `:685`; `agent/sdk_client.py:152`, `:171`; `models/agent_session.py:516`) that may drift before build time. A builder editing by line number could touch the wrong region. | Technical Approach (pre-edit grep set); Step 1 (explicit grep bullet) | Treat all line numbers as **hints, not targets**. Step 1 requires running the pre-edit grep set to re-locate every region by content pattern. Edit whatever the grep finds, regardless of whether line numbers match this plan. |
| CONCERN | Adversary (dual-id collision) | Plan asserts "no collision possible" between `session_id` and `agent_session_id` values. Holds under current naming conventions, but is a soft invariant rather than a hard one. | Technical Approach (Step 1 docstring) | Document the assumption in `_find_session`'s docstring: current session_id formats (`{chat_id}_{message_id}`, `tg_{project}_{chat_id}_{message_id}`, `sdlc-local-{issue}`) never collide with 32-char hex UUIDs. If a future session_id scheme produces 32-char hex values, the session_id-first ordering still works — but a UUID caller could receive the wrong record if collision occurred. Docstring-only guard; no runtime validation. |
| CONCERN | Simplifier | `_find_session` is a five-line helper with five call sites in one file. A reviewer could reasonably ask whether to promote it to a shared module for reuse across other CLIs. | Technical Approach (Step 1); Rabbit Holes | Keep `_find_session` inline in `tools/valor_session.py`. Promotion to `agent/agent_session_queue.py` or `tools/_session_lookup.py` adds surface area and cross-file cognitive load for zero gain at the current scope. The Rabbit Holes section already records this decision; the revision note reinforces it at the helper's definition site. |
| CONCERN | User/PM (error-message stability) | The two new error strings (`"has status '{current_status}'. Only completed/killed/failed sessions can be resumed."` and `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"`) are operator-facing. Operators will grep for these in docs and shell history. A future refactor that "cleans up" the wording would silently fragment the operator vocabulary. | Failure Path Test Strategy (Error State Rendering) | Unit tests MUST assert the exact string (`assert captured.err.strip() == expected_message`) rather than substring-match. A word-level wording change MUST be a breaking-test event, not a silent operator-facing surprise. |
| CONCERN | User/PM (revision-applied vs. ready-status) | `revision_applied: true` routes the next SDLC pass to `/do-build` (Row 4c), but `status: Planning` remains. The plan's Open Questions section says "None — Proceed to build" which resolves this cleanly, but the builder should still verify status on startup to avoid drift. | Plan frontmatter; Open Questions | Unlike some plans with unresolved Open Questions, this plan explicitly declares `Open Questions: None` and concludes "Proceed to build". `revision_applied: true` therefore correctly signals "proceed to build without further human input". The builder may flip `status: Planning → Ready` on startup as an implicit promotion. If a future revision re-opens questions, this note becomes a checkpoint rather than a rubber stamp. |

---

## Open Questions

None — the issue body provides a complete Recon Summary with all four solution items confirmed against the codebase. The scope is surgical and additive. Proceed to build.
