---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1741
last_comment_id:
---

# Granite messageless sdlc-local session fail-loud + /goal-driven PM/Dev prime

## Problem

`/do-sdlc 1460` ran for ~8 minutes, reported success (👏), but issue #1460 was
untouched: no PR, no SDLC stages recorded, `git` clean on `main`. The pipeline
silently no-op'd while masquerading as a successful completion.

**Current behavior:**

A local SDLC pipeline run creates an issue-scoped tracking record
`sdlc-local-{N}` via `tools/sdlc_session_ensure.py` `ensure_session()`, which
calls `AgentSession.create_local(...)` with **no `message_text`**. When that
record is executed by the worker's granite-container path
(`agent/session_executor.py`), the missing message propagates straight through:

- `enriched_text = session.message_text` → `None` (`session_executor.py:1392`)
- `_turn_input = enriched_text` (no steering messages queued) → `None` (`:1519`)
- `build_harness_turn_input(message=None, …)` (param typed `message: str` but
  receives `None`) wraps `None` into the `SCOPE:` header block, rendering the
  literal task text `"None"` (`:1557`)
- `_container_message = _harness_input` → passed to the container (`:1659`)

`Container.__init__` guards `if not user_message.strip(): raise`
(`agent/granite_container/container.py:569`) — but the message wasn't empty. It
was a full SCOPE block *containing* the word "None", so the empty-guard passed.
The PM TUI was primed with `MESSAGE: None`, correctly observed "no task
provided," emitted `[/complete]` three times, and the run reported a clean 👏
success despite doing zero work.

**Desired outcome:**

1. The `sdlc-local-{N}` tracking record carries the originating intent, so any
   executor that runs it always has a genuine first message (Fix A).
2. If a container message is ever empty / `None` / strips to `"None"`, the
   session fails loudly — finalized `failed` with a clear logged reason —
   instead of dispatching a no-op container and reporting success (Fix B).
3. The PM and Dev primes anchor a Claude Code `/goal` to the originating
   message so a PM that mis-reads a thin first message has a durable goal
   pulling it back toward the real intent (Fix C), with turn-loop ownership
   between `/goal` and the granite operator resolved so a PM waiting on the Dev
   is never re-spun.

## Freshness Check

**Baseline commit:** c649dc412f6e6cab7a5381898ddea3ea27d3a515
**Issue filed at:** 2026-06-19T08:23:02Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold at the baseline commit):**
- `tools/sdlc_session_ensure.py:174-180` — `AgentSession.create_local(...)`
  invoked with `session_id`, `project_key`, `working_dir`, `session_type`, and
  `**kwargs` (only `issue_url` ever populated) — **no `message_text`**. Confirmed.
- `agent/session_executor.py:1392` — `enriched_text = session.message_text`. Confirmed.
- `agent/session_executor.py:1519` — `_turn_input = enriched_text`. Confirmed.
- `agent/session_executor.py:1557-1568` — `await build_harness_turn_input(message=_turn_input, …)`. Confirmed.
- `agent/session_executor.py:1659` — `_container_message = _harness_input`; the
  finalized message is consumed by `do_work()` → `_bridge_adapter.run(user_message=_container_message, …)` at `:1663`. Confirmed.
- `agent/granite_container/container.py:569` — `if not user_message.strip():` guard. Confirmed.
- `models/agent_session.py:1567-1591` — `create_local(...)` accepts `**kwargs`
  and forwards them to the constructor, so `message_text=...` flows through
  cleanly. Confirmed (no signature change required for Fix A).
- `.claude/commands/granite/prime-pm-role.md`, `prime-dev-role.md`,
  `_prime-rails.md` — present and structured as the spec describes. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1486 (granite dual-session PoC) — prior art, referenced for context only.
- #1692 — persona is delivered entirely via the prime commands now (the
  `--append-system-prompt` path was removed); confirmed by the comment block at
  `session_executor.py:1608-1626`. This is why Fix C lives in the prime files.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-06-19T08:23:02Z` over the three target paths is empty.

**Active plans in `docs/plans/` overlapping this area:** `granite_routing_prefix_floor.md`,
`granite_lossless_checkpoint_resume.md`, `granite_pty_production_cutover.md`,
`granite_root_session_runner.md`, `granite-tui-pty-spike.md`. These touch the
granite container loop but **not** `sdlc_session_ensure.py`'s `create_local`
call, the executor's `_container_message` finalization, or the `/goal` prime
design. No overlap that requires merging plans — coordination signal only:
`granite_lossless_checkpoint_resume.md` touches `--resume` wiring, which is
explicitly out of scope here (Fix C re-primes every run precisely because there
is no resume).

**Notes:** No drift. The issue body's root-cause trace is current and exact.

## Prior Art

- **#1486**: Granite dual-session PoC — established the PM/Dev cooperating-TUI
  model the primes implement. Relevant as the architectural foundation; does not
  attempt the goal-anchoring or the messageless-session guard.
- **#1692**: Removed `compose_system_prompt` / `--append-system-prompt`; persona
  now arrives only via the prime commands. This is why Fix C is a prime-file
  change, not a system-prompt change.
- **#1195 / #1272**: The two existing executor-entry guards
  (`None working_dir/session_id`, slugless eng session) — Fix B reuses their
  exact `finalize_session(session, "failed", reason=…)` + `[executor-guard]`
  structured-log pattern. No new failure-recording mechanism is invented.
- **#1676**: `sdlc-local` orphan reaping keys on `updated_at` liveness — unrelated
  to the message-text gap but confirms `create_local` records are first-class,
  long-lived tracking records that must carry real intent.

No prior attempt addressed the messageless-session silent-success path. This is
the first fix for it.

## Research

No relevant external findings — `/goal` is a Claude Code harness feature whose
semantics are fully specified in the issue/spec (session-scoped Stop hook;
Haiku evaluator reads only the conversation, runs no tools; v2.1.139+ required,
present substrate is 2.1.183). Everything else is internal codebase work.

## Data Flow

The bug is a missing-value propagation across four layers. Fix A plugs the hole
at the source; Fix B is a backstop at the sink.

1. **Entry point**: `/do-sdlc {N}` (local) → `tools/sdlc_session_ensure.py`
   `ensure_session(N)` → `AgentSession.create_local(...)`. **Today**: no
   `message_text` → record carries `message_text=None`. **After Fix A**: record
   carries a real originating message derived from issue #N.
2. **Executor read**: worker runs the record → `enriched_text = session.message_text`
   (`session_executor.py:1392`). `None` today; real string after Fix A.
3. **Turn input**: `_turn_input = enriched_text`, then `pop_steering_messages()`
   *may* override it (`:1519-1537`). **Critical**: the goal must anchor to the
   originating `message_text`, never to a popped steering message — steering is
   course-correction toward the goal, never redefinition of it.
4. **Harness build**: `build_harness_turn_input(message=_turn_input, …)`
   (`:1557`) wraps the message in SCOPE headers → `_harness_input`.
5. **Finalize**: `_container_message = _harness_input` (`:1659`). **Fix B guard
   lands here**: if `_container_message` is empty / `None` / strips to `"None"`,
   finalize `failed` and return BEFORE building the BridgeAdapter / running the
   container.
6. **Sink**: `do_work()` → `_bridge_adapter.run(user_message=_container_message)`
   → `Container.__init__` (`container.py:569`). The PM TUI is primed; the `/goal`
   (Fix C) anchors it to the originating message.

## Architectural Impact

- **New dependencies**: none. `/goal` is a harness-native feature already present
  in the substrate; the primes invoke it as a slash command in their authored
  text. No Python imports, no MCP servers, no config files.
- **Interface changes**: none. `create_local` already accepts `**kwargs`;
  Fix A passes `message_text=` through that existing channel. Fix B adds a guard
  block inside `_execute_agent_session` with no signature change.
- **Coupling**: Fix A slightly tightens `sdlc_session_ensure` → the executor's
  message contract (the tracking record now guarantees a non-empty first
  message). Fix B makes the executor's empty-message contract explicit and
  enforced rather than implicit and silently violated.
- **Data ownership**: unchanged. The `AgentSession` record still owns its
  `message_text`; Fix A just stops leaving it null.
- **Reversibility**: high. Each fix is a small, isolated, independently
  revertible change. The prime changes are authored-text only.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the turn-loop ownership decision is the one design call worth confirming)
- Review rounds: 1 (Fix A/B are mechanical and well-traced; Fix C is prompt design that benefits from one review pass)

The code changes (Fix A, Fix B) are small and surgical. The appetite is Medium
because Fix C is prompt-engineering against a turn-driving harness feature whose
interaction with the granite operator's own turn driver must be reasoned through
carefully — that reasoning, not LOC, is the cost.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` ≥ 2.1.139 (for `/goal`) | `claude --version` | `/goal` Stop-hook support; substrate is 2.1.183 ✓ |
| Hooks enabled (no `disableAllHooks` / `allowManagedHooksOnly`) | `python -c "import json,os; s=json.load(open(os.path.expanduser('~/.claude/settings.json'))) if os.path.exists(os.path.expanduser('~/.claude/settings.json')) else {}; assert not s.get('disableAllHooks'), 'hooks disabled'"` | `/goal` is a Stop hook; must be allowed to fire |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_messageless_session_goal_prime.md`

## Solution

### Key Elements

- **Fix A — persist originating intent** (`tools/sdlc_session_ensure.py`):
  `ensure_session()` sets a real `message_text` on the `create_local(...)` call,
  derived from the originating issue so the prime can resolve the goal from issue
  #N.
- **Fix B — fail loud on empty task** (`agent/session_executor.py`): a single
  guard just before the granite dispatch finalizes the session `failed` if the
  container message is empty / `None` / strips to `"None"`, reusing the existing
  `finalize_session(..., "failed", reason=…)` + `[executor-guard]` pattern.
- **Fix C — `/goal`-anchored primes** (`prime-pm-role.md`, `prime-dev-role.md`,
  `_prime-rails.md`): the PM prime sets a `/goal` anchored to the originating
  `<prompt>` with a completion condition demonstrable in the PM transcript; the
  Dev prime accepts a PM-set `/goal` in its first relay slot. Turn-loop ownership
  is resolved (see Technical Approach).

### Flow

`/do-sdlc {N}` → `ensure_session` creates `sdlc-local-{N}` **with** a real
originating message → worker reads `message_text` → executor finalizes
`_container_message` → **(Fix B guard: empty/"None"/None → fail loud, stop)** →
container runs PM TUI → **PM prime sets `/goal` anchored to the originating
message** → PM relays `[/dev]` carrying a PM-decided `/goal` for the Dev →
Dev drives the SDLC pipeline → PM drafts a FINAL `[/complete]` reply to the
supervisor (goal condition met, demonstrable in PM transcript).

### Technical Approach

**Fix A — `tools/sdlc_session_ensure.py`.** In `ensure_session()`, before the
`create_local` call (around line 156-180), add `message_text` to `kwargs`. The
text must be a genuine instruction the PM prime can act on, anchored to the
issue. Use a phrasing that references the issue so the PM can read issue #N for
the goal, e.g.:

```
kwargs["message_text"] = (
    f"Run the full SDLC pipeline for issue #{issue_number}. "
    f"Read the issue body for the work to be done"
    + (f" ({issue_url})." if issue_url else ".")
)
```

This is the minimum that satisfies the originating-intent requirement and gives
the `/goal` prime a real anchor. It must NOT be the bare string `"None"` or a
SCOPE block — a plain natural-language instruction. (The exact wording is a
build-time detail; the load-bearing requirement is non-empty, issue-anchored,
PM-actionable text.)

**Fix B — `agent/session_executor.py` (~line 1659).** Immediately after
`_container_message = _harness_input`, before `do_work()` / `BridgeAdapter`
construction, add a guard:

```
_stripped = (_container_message or "").strip()
if not _stripped or _stripped == "None":
    # ... [executor-guard] structured error log with reason ...
    # finalize_session(session, "failed", reason="empty_container_message: ...")
    # mirror the StatusConflictError / last-resort save handling from the
    # existing guards at :684-717 and :733-769
    return
```

Reuse the **exact** error-handling shape of the two existing guards: import
`StatusConflictError, finalize_session` locally; call
`finalize_session(session, "failed", reason="empty_container_message: container message stripped to <repr>")`;
catch `StatusConflictError` (already-terminal → log at INFO, no fallback save);
catch broad `Exception` (alarm-log + last-resort `session.status = "failed";
session.save(update_fields=["status","updated_at"])`). The structured
`[executor-guard]` log line is the durable failure record — there is no
`failure_reason` column. Emit the offending repr (e.g. the SCOPE block) so the
log shows WHY it was judged empty.

Note: this guard catches the `"None"`-rendered SCOPE block that
`Container.__init__`'s `strip()` check misses, AND any future regression that
produces a null/empty message. It is defense-in-depth — Fix A removes the only
known producer, Fix B ensures no producer can ever silently succeed again.

**Fix C — the primes.**

*PM prime (`prime-pm-role.md`)*: add a "Set your goal" step at the top of "What
you DO" instructing the PM, on its first turn, to set a `/goal` anchored to the
literal `$ARGUMENTS` (the originating message). The completion condition must be
**demonstrable in the PM transcript** because the `/goal` evaluator reads only
the conversation and runs no tools. Concretely the condition is satisfied when
the PM's transcript shows BOTH: (a) the Dev has reported the routed work
complete (e.g. "PR for #N merged"), AND (b) the PM has authored a FINAL
`[/complete]` reply to the supervisor (not a progress report). The prime states
explicitly: anchor the goal to `$ARGUMENTS`, never to a later steering/relay
message; steering is course-correction toward the goal, never redefinition.

*Dev prime (`prime-dev-role.md`)*: the Dev already waits for the operator to
relay the PM's first `[/dev]` instruction (item 5, lines 30/53-55). Add one
sentence: if the PM's first relay includes a `/goal …` directive, set it as your
session goal; the goal is PM-decided and may be a decomposed sub-goal. Dev goal
conditions may reference tool output the Dev surfaces (e.g. "`npm test` exits 0",
"PR opened and review passed").

**Turn-loop ownership resolution (the primary design question).** Two
turn-drivers coexist in one PTY session: (1) the granite operator, which drives
cross-role turns via PTY relay of `[/dev]`/`[/user]`/`[/complete]` plus
classifier completion detection, and (2) the `/goal` Stop-hook loop inside each
TUI, which re-drives a turn after the agent stops if its condition is unmet. The
hazard: a PM blocked WAITING on the Dev gets re-turned (burning turns/tokens) by
`/goal` because, from the PM transcript's point of view, the goal is not yet met.

**Chosen mitigation: (a) — the PM completion/quiescence condition tolerates an
explicit, in-transcript "WAITING on Dev" state.** The PM prime instructs the PM
to end any turn where it has handed off to the Dev with a recognized waiting
sentinel surfaced in the transcript (e.g. a final line `WAITING: Dev is
executing <task>; will resume on Dev report`). The `/goal` condition is authored
so it is satisfied (loop quiesces) when the PM's last turn ends in EITHER a
final `[/complete]` reply to the supervisor OR a `WAITING:` sentinel. Because
the `/goal` evaluator reads only the transcript, the `WAITING:` line is
sufficient evidence for it to NOT re-drive a turn. The granite operator remains
the **sole** driver of the next cross-role turn: when the Dev's report arrives,
the operator relays it into the PM, which starts a fresh PM turn naturally. The
goal then re-evaluates against the new transcript content and, if the work is
done, the PM produces its final reply.

This keeps a single source of turn authority (the operator) for cross-role
progression, while `/goal` serves only its intended purpose: preventing the PM
from prematurely declaring done. It does not let `/goal` spin a PM that is
legitimately waiting. We deliberately reject mitigation (b) ("operator holds
turns while Dev runs") for this plan: it would require the operator to suppress
the Stop hook, coupling the operator to `/goal` internals it does not own —
higher risk, more code, and it fights the harness instead of cooperating with
it. (a) is authored-text only and keeps the operator and `/goal` decoupled.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Fix B's guard wraps `finalize_session` in the same try/except shape as the
  existing guards (`session_executor.py:684-717`, `:733-769`): a `StatusConflictError`
  branch (already-terminal → INFO log, no save) and a broad `Exception` branch
  (alarm-log + last-resort status save). Add a unit test asserting the
  `[executor-guard]` ERROR log fires with the reason on the empty-message path.
- [ ] `tools/sdlc_session_ensure.py` `ensure_session` already wraps everything in
  a broad `except Exception` returning `{}` — Fix A adds only a kwarg, no new
  handler. State: "no new exception handlers in `sdlc_session_ensure`."

### Empty/Invalid Input Handling
- [ ] Fix B is itself the empty/invalid-input handler. Unit-test all three
  trigger forms against the guard: `_container_message = None`, `""`,
  whitespace-only, and a SCOPE block that strips to `"None"` → each must finalize
  `failed` and must NOT construct the BridgeAdapter / run the container.
- [ ] Assert the non-trigger case: a real non-empty message passes the guard and
  proceeds to dispatch (guard must not be over-eager — e.g. a message that merely
  *contains* "None" mid-text but does not strip to exactly "None" must pass).
- [ ] Verify a messageless `sdlc-local-{N}` can no longer reach a 👏 success: the
  session ends `failed`, never `completed`.

### Error State Rendering
- [ ] Fix B's failure is operator-visible via the `[executor-guard]` structured
  log and the `failed` session status (surfaced on the dashboard and to
  reflections). Assert the log line carries the offending message repr so the
  failure is diagnosable, not silent.
- [ ] The PM-facing surface: a `failed` session does not emit a 👏 — confirm no
  success delivery path is reachable from the guard's `return`.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: existing tests that
  assert a created `sdlc-local-{N}` record's fields must now also assert
  `message_text` is non-empty and issue-anchored. Any test asserting
  `message_text is None` (if present) must flip to assert the new value.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — UPDATE:
  end-to-end creation assertions extend to cover the populated `message_text`.
- [ ] `tests/unit/test_session_executor_guards.py` — UPDATE (extend): add a new
  test class `TestExecutorGuardEmptyContainerMessage` mirroring the existing
  `TestExecutorGuardWorkingDirNone` pattern (same fixtures, same caplog
  assertions on the `[executor-guard]` reason), covering the empty/"None"/None
  container-message cases and the non-trigger pass-through case.
- [ ] `tests/unit/test_session_executor_granite.py` — UPDATE if it asserts the
  dispatch always proceeds; ensure no existing granite-path test regresses now
  that an empty message short-circuits before BridgeAdapter construction.
- [ ] No prime-file unit tests exist today (the primes are authored markdown).
  Fix C verification is via a structural test asserting the prime files contain
  the required `/goal` and `WAITING:` affordances (see Verification table) — a
  new lightweight test, not a modification of existing coverage.

## Rabbit Holes

- **Re-architecting the turn-driver.** Do NOT attempt to make the operator and
  `/goal` share a formal turn-token protocol, suppress the Stop hook from the
  operator, or build a turn-arbitration layer. The chosen mitigation (a) is
  authored-text only and keeps them decoupled. Anything more is a separate
  project.
- **`--resume` / checkpoint wiring.** The container re-primes every run by
  design; do not try to make the goal persist across runs via session resume.
  That is `granite_lossless_checkpoint_resume.md`'s territory.
- **Reworking `build_harness_turn_input` to reject `None`.** Tempting (it's the
  function that renders `None` → `"None"`), but the typed contract change ripples
  across every caller and every session type. Fix B at the finalization point is
  the surgical backstop; Fix A removes the only producer. Leave the harness
  builder alone.
- **Perfecting the PM goal-condition wording.** Iterate once in review, then
  stop. The condition only needs to be transcript-demonstrable and to recognize
  the `WAITING:` sentinel; chasing the "perfect" Haiku-evaluator phrasing is
  diminishing returns.
- **Generalizing the fix to all `create_*` factories.** Only `create_local` on
  the `sdlc-local` path is implicated. `create_child` already requires
  `message_text`. Do not audit/retrofit every factory.

## Risks

### Risk 1: The PM `/goal` condition is too strict and the PM never completes
**Impact:** A PM whose goal condition can't be satisfied loops via the Stop hook,
burning turns/tokens until a turn cap or operator timeout fires.
**Mitigation:** The condition is authored to quiesce on EITHER a final
`[/complete]` reply OR a `WAITING:` sentinel — both are states the PM can always
reach. Review the condition wording in the single review round. The existing
two-tier no-progress detector and turn caps in the executor are the backstop.

### Risk 2: Fix B over-triggers and fails legitimate sessions
**Impact:** A real message that happens to strip to something the guard
mis-judges (e.g. a message whose entire content is the literal word "None")
would be failed incorrectly.
**Mitigation:** The guard triggers only on exact-match `strip() == "None"` or
empty/whitespace — not substring containment. The "real message contains 'None'
mid-text" non-trigger case is an explicit unit test. The realistic blast radius
is near-zero: a legitimate first message is never the bare token "None".

### Risk 3: `/goal` and the operator's classifier both react to the same turn
**Impact:** Double-driving — the operator relays a turn AND `/goal` re-drives it,
producing duplicate/contending PM turns.
**Mitigation:** This is exactly what the turn-loop ownership resolution prevents.
The operator is the sole cross-role driver; `/goal` only guards premature
completion and quiesces on the `WAITING:` sentinel. Documented in the prime and
in `docs/features/granite-pty-production.md`.

## Race Conditions

### Race 1: steering message popped before goal anchors to originating message
**Location:** `agent/session_executor.py:1519-1537` (`pop_steering_messages`)
**Trigger:** A steering message is queued onto `sdlc-local-{N}` before/at the
first turn; `_turn_input` becomes the steering message rather than the
originating `message_text`, and the PM could anchor its `/goal` to the wrong text.
**Data prerequisite:** The goal must derive from `session.message_text` (the
originating intent), not from `_turn_input` after a steering pop.
**State prerequisite:** The PM prime receives `$ARGUMENTS` = the originating
message on first prime, distinct from later relays.
**Mitigation:** Fix A guarantees `message_text` is populated. The PM prime
explicitly anchors `/goal` to `$ARGUMENTS` (the prime's argument is the
originating message), and states that steering is course-correction toward the
goal, never redefinition. The goal-anchoring is therefore independent of what
`pop_steering_messages()` returns on any given turn.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #N/A] Lossless `--resume`/checkpoint so the goal persists across
  container runs — owned by `granite_lossless_checkpoint_resume.md`; this plan
  deliberately re-primes (and re-sets the goal) every run. *(Not filed as a new
  issue because it is an existing in-flight plan, not deferred new work.)*
- [DESTRUCTIVE] Changing `build_harness_turn_input`'s `message: str` signature to
  reject `None` at the type boundary — ripples across every caller and session
  type; the surgical backstop (Fix B) plus source fix (Fix A) fully cover the
  bug without a cross-cutting contract change.

Everything else the issue asks for (Fix A, Fix B, Fix C, turn-loop ownership) is
in scope and resolved in this plan.

## Update System

No update system changes required. All three fixes are internal: Fix A and Fix B
are Python edits shipped with the repo; Fix C edits prime-command markdown under
`.claude/commands/granite/` which is already part of the repo checkout the
container runs against. No new dependencies, config files, or migration steps.
The `/goal` feature is already present on every machine's `claude` substrate
(2.1.183 ≥ 2.1.139) — the update skill already keeps `claude` current.

## Agent Integration

No new agent integration surface required — this is a bridge/worker-internal
change plus prime-prompt design.

- No new CLI entry point in `pyproject.toml [project.scripts]`:
  `tools/sdlc_session_ensure.py` is already invoked via
  `python -m tools.sdlc_session_ensure`; Fix A only changes a kwarg it passes.
- No new MCP server / `.mcp.json` change.
- The bridge does not import the changed code directly; the worker's
  `session_executor` is the consumer, already on the execution path.
- Fix C reaches the agent through the existing prime-command mechanism the
  granite container already loads (`.claude/commands/granite/prime-*.md`).
- Integration coverage: `tests/integration/test_sdlc_session_ensure_integration.py`
  (extended for Fix A) verifies the created record carries `message_text`. The
  granite container loop tests (`tests/integration/test_granite_container_loop.py`,
  `test_granite_pty_production.py`) exercise the prime path; if practical, add an
  assertion that an empty container message fails loudly end-to-end (else the
  unit test in `test_session_executor_guards.py` is the authoritative coverage).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md`: add a "Goal anchoring &
  turn-loop ownership" subsection describing the PM `/goal` (anchored to the
  originating message, transcript-demonstrable completion condition), the Dev
  PM-set goal, and the chosen turn-loop ownership mitigation (operator is sole
  cross-role driver; `/goal` quiesces on `[/complete]` or a `WAITING:` sentinel).
- [ ] Update `docs/features/eng-session-architecture.md` (or
  `docs/features/sdlc-tool-resolver.md` if more apt): note that `sdlc-local-{N}`
  records now carry an issue-anchored `message_text` and that the executor fails
  loud on an empty container message (no silent 👏).
- [ ] Add/confirm an entry in `docs/features/README.md` index if the granite
  goal-anchoring warrants its own discoverable line.

### External Documentation Site
- [ ] No external docs site in this repo — N/A.

### Inline Documentation
- [ ] Comment the Fix B guard explaining it catches the `"None"`-rendered SCOPE
  block that `Container.__init__`'s `strip()` check misses, and cites issue #1741.
- [ ] Comment the Fix A kwarg explaining the originating-intent requirement and
  the goal-anchoring contract (anchor to originating message, not steering).

## Success Criteria

- [ ] `AgentSession.create_local(...)` for an `sdlc-local-{N}` record sets a
  non-empty, issue-anchored `message_text` (Fix A).
- [ ] `agent/session_executor.py` refuses to dispatch a container whose message
  is empty / `None` / strips to `"None"`, finalizing the session `failed` with a
  clear `[executor-guard]` logged reason (Fix B).
- [ ] A messageless tracking session can no longer report a 👏 success — it ends
  `failed`, never `completed`.
- [ ] The PM prime sets a `/goal` anchored to the originating message with a
  completion condition demonstrable in the PM transcript; the Dev prime accepts a
  PM-set `/goal` in its first relay slot (Fix C).
- [ ] Turn-loop ownership is resolved: a PM waiting on the Dev surfaces a
  `WAITING:` sentinel and is NOT re-spun by `/goal`; the operator remains the
  sole cross-role turn driver (documented in the prime + feature doc).
- [ ] Unit coverage for Fix A (`message_text` set) and Fix B (empty/"None"/None →
  failed, never success; real message passes).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms the PM prime references `/goal` and the `WAITING:` sentinel,
  and the Dev prime references accepting a PM-set goal.

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (fix-a-source)**
  - Name: source-builder
  - Role: Fix A — populate `message_text` on `create_local` in `sdlc_session_ensure.py`
  - Agent Type: builder
  - Resume: true

- **Builder (fix-b-guard)**
  - Name: guard-builder
  - Role: Fix B — empty-container-message guard in `session_executor.py`
  - Agent Type: builder
  - Resume: true

- **Builder (fix-c-primes)**
  - Name: prime-builder
  - Role: Fix C — `/goal`-anchored PM/Dev primes + turn-loop ownership wording
  - Agent Type: builder
  - Resume: true

- **Test Engineer (coverage)**
  - Name: test-builder
  - Role: Unit + integration tests for Fix A, Fix B, and the prime structural checks
  - Agent Type: test-engineer
  - Resume: true

- **Validator (all)**
  - Name: all-validator
  - Role: Verify all success criteria, run verification commands
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs)**
  - Name: docs-builder
  - Role: Update granite + eng-session feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix A — persist originating intent
- **Task ID**: build-fix-a
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_session_ensure.py, tests/integration/test_sdlc_session_ensure_integration.py
- **Assigned To**: source-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_session_ensure.py` `ensure_session()`, add a non-empty,
  issue-anchored `message_text` to `kwargs` before the `create_local(...)` call.
- Keep it plain natural-language instruction text referencing issue #N (and the
  URL if present); never the bare token "None" or a SCOPE block.
- Add an inline comment citing #1741 and the goal-anchoring contract.

### 2. Fix B — fail loud on empty container message
- **Task ID**: build-fix-b
- **Depends On**: none
- **Validates**: tests/unit/test_session_executor_guards.py, tests/unit/test_session_executor_granite.py
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- After `_container_message = _harness_input` (~`session_executor.py:1659`), add a
  guard: if `(_container_message or "").strip()` is empty or `== "None"`,
  finalize `failed` and `return` before BridgeAdapter construction / `do_work()`.
- Mirror the existing guards' error handling exactly (local import of
  `StatusConflictError, finalize_session`; `StatusConflictError` → INFO; broad
  `Exception` → alarm-log + last-resort status save). Emit the offending repr in
  the `[executor-guard]` log. Cite #1741.

### 3. Fix C — /goal-anchored primes + turn-loop ownership
- **Task ID**: build-fix-c
- **Depends On**: none
- **Validates**: prime structural checks (new lightweight test)
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: true
- `prime-pm-role.md`: add a first-turn "set `/goal` anchored to `$ARGUMENTS`"
  step with a transcript-demonstrable completion condition (Dev reported done AND
  final `[/complete]` reply delivered); state steering ≠ goal redefinition;
  instruct the PM to surface a `WAITING:` sentinel when handing off to the Dev so
  `/goal` does not re-spin it.
- `prime-dev-role.md`: one sentence — accept a PM-set `/goal` in the first relay
  slot; goal may be a decomposed sub-goal; conditions may reference Dev tool
  output.
- `_prime-rails.md`: if a shared note on turn-loop ownership belongs here (both
  roles), add it once; otherwise keep role-specific.

### 4. Tests — Fix A, Fix B, prime structure
- **Task ID**: build-tests
- **Depends On**: build-fix-a, build-fix-b, build-fix-c
- **Validates**: tests/unit/test_sdlc_session_ensure.py, tests/unit/test_session_executor_guards.py, tests/integration/test_sdlc_session_ensure_integration.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Extend `test_sdlc_session_ensure.py` / integration to assert `message_text` is
  non-empty and issue-anchored.
- Add `TestExecutorGuardEmptyContainerMessage` (mirror `TestExecutorGuardWorkingDirNone`):
  None / "" / whitespace / strips-to-"None" → `failed` + `[executor-guard]` log;
  real message passes; "None" mid-text non-trigger passes.
- Add a structural test asserting the PM prime contains `/goal` + `WAITING:` and
  the Dev prime references accepting a PM-set goal.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: all-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands; confirm every Success Criterion.
- Confirm no messageless session can reach `completed`/👏.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and
  `docs/features/eng-session-architecture.md` per the Documentation section.

### 7. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: all-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run all verification; confirm docs landed; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Fix A unit tests pass | `pytest tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Fix B guard tests pass | `pytest tests/unit/test_session_executor_guards.py -q` | exit code 0 |
| Fix A integration passes | `pytest tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| `create_local` call sets message_text | `grep -n "message_text" tools/sdlc_session_ensure.py` | output contains message_text |
| Fix B guard present | `grep -n 'strip() == "None"' agent/session_executor.py` | output > 0 |
| PM prime sets /goal | `grep -n "/goal" .claude/commands/granite/prime-pm-role.md` | output > 0 |
| PM prime has WAITING sentinel | `grep -n "WAITING" .claude/commands/granite/prime-pm-role.md` | output > 0 |
| Dev prime accepts PM goal | `grep -ni "goal" .claude/commands/granite/prime-dev-role.md` | output > 0 |
| Format clean | `python -m ruff format --check tools/sdlc_session_ensure.py agent/session_executor.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **PM `/goal` completion-condition phrasing.** The plan picks "Dev reported the
   routed work complete AND PM delivered a final `[/complete]` reply" as the
   transcript-demonstrable condition, with a `WAITING:` sentinel for quiescence.
   Is that the right completion bar, or should the PM's goal also require an
   explicit human acknowledgement (👍) before it considers itself done?
2. **Fix A message wording.** Minimum is `f"/do-sdlc {issue_number}"`; the plan
   proposes a richer "Run the full SDLC pipeline for issue #{N}; read the issue
   body…" so the PM can resolve the goal from the issue. Confirm the richer
   phrasing is preferred (it is what makes the `/goal` anchor useful).
3. **Turn-loop ownership mitigation.** The plan commits to mitigation (a) —
   in-transcript `WAITING:` sentinel, operator stays sole cross-role driver — and
   explicitly rejects (b) (operator suppresses the Stop hook). Confirm (a) is
   acceptable before build; it is authored-text only and keeps `/goal` and the
   operator decoupled.
