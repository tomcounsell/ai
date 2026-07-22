---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2197
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-22T02:44:15Z
---

# Terminal-turn self-draft deferral: suppress the context-blind continuation

## Problem

When the message drafter defers a session's **final-turn** reply for self-draft
rewrite, two independent terminal-path handlers fire, uncoordinated. The user
gets a raw flush of the deferred (empty-promise) text **plus** a redundant,
context-blind continuation that emits a misleading "No substantive results to
report" — even though the prior turn produced real, correct output.

**Current behavior:**

On a terminal-turn deferral (sender `drafter-fallback`):

1. **Terminal-path flush** (`agent/session_health.py`, `flush_deferred_self_draft_sync`,
   ~L2135-2230, added by #1794) reads `deferred_self_draft_text` and delivers the
   raw deferred text. It works, but does **not** pop the steering queue.
2. **Steering re-enqueue** (`agent/session_executor.py:2247-2304`) then pops the
   still-present `drafter-fallback` steering and re-enqueues it as a continuation
   via `enqueue_agent_session(...)` — which has **no `claude_session_uuid`
   parameter** (`agent/agent_session_queue.py:1483`), so the continuation spawns
   a **brand-new, context-blind Claude session**. The steering payload is only the
   rewrite *instruction* (`SELF_DRAFT_INSTRUCTION`), not the text to rewrite. Told
   to "rewrite it" with no "it," the agent takes the instruction's escape hatch —
   *"If your work produced no substantive results, say so plainly"*
   (`bridge/message_drafter.py:630`) — and confidently emits "No substantive
   results to report."

Real incident (Cuttlefish thread, 2026-07-22): a full correct root-cause diagnosis
was produced, then never delivered; Tom got an empty promise followed by a
misleading "nothing to report."

**Desired outcome:**

A final-turn self-draft deferral results in **exactly one** coherent terminal
handling. The terminal delivery path — the **telegram** sync flush
(`flush_deferred_self_draft_sync`) or the **email** async fallback
(`_deliver_deferred_self_draft_fallback`) — is the sole handler for `drafter-fallback`
steering; that steering is **never** re-enqueued as a context-blind continuation on
either transport. A session that produced real output must never emit "no
substantive results."

Note on transports: `flush_deferred_self_draft_sync` (`agent/session_health.py:2089`)
early-returns for email and non-`completed` statuses — it owns only the telegram
completed terminal path. The async `_deliver_deferred_self_draft_fallback`
(`agent/session_health.py:2239`) owns the email `failed`/`abandoned` path. The
re-enqueue suppression in `session_executor.py` is transport-agnostic (it drops
`drafter-fallback` from `pop_all_steering_messages()` regardless of transport), so it
correctly protects BOTH terminal delivery paths — but the plan must not claim a
single flush handles every case.

## Freshness Check

**Baseline commit:** 89eac425c
**Issue filed at:** 2026-07-22T02:25:15Z (same day as planning)
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/message_drafter.py:622-630` — `SELF_DRAFT_INSTRUCTION` with the escape-hatch line "If your work produced no substantive results, say so plainly." — still holds.
- `agent/session_health.py:~2135-2230` (`flush_deferred_self_draft_sync`) — reads `deferred_self_draft_text`, dedups on `self_draft_completed_flush_sent:{session_id}`, delivers raw text, does NOT pop the steering queue — still holds.
- `agent/session_executor.py:2247-2304` — `pop_all_steering_messages(...)` then `enqueue_agent_session(...)` for ALL leftover incl. `drafter-fallback` — still holds.
- `agent/agent_session_queue.py:1483` (`enqueue_agent_session`) — confirmed NO `claude_session_uuid` parameter in the signature; continuation is inherently context-blind — still holds.
- `agent/session_executor.py:1932-1949` (`ResumeContext`) — only built when the AgentSession already carries `claude_session_uuid` — still holds.

**Cited sibling issues/PRs re-checked:**
- #1794 (CLOSED, PR #1796 merged 2026-06-25) — added the completed-path flush; did NOT guard the re-enqueue. This issue closes that gap.
- #1730 (PR #1739 merged 2026-06-18) — original failed/abandoned fallback; predecessor, unaffected.
- #1797 (PR #1807 merged 2026-06-26) — email-completed-path flush; parallel path, unaffected.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none (`deferred_self_draft_completed_path_flush.md` is #1794, already completed/migrated).

**Notes:** No drift. All references accurate on `main`.

## Prior Art

- **#1794 / PR #1796**: Added `flush_deferred_self_draft_sync` on the completed terminal path so a deferred reply isn't lost. Fixed *loss*; explicitly did NOT guard the redundant re-enqueue (its own recon flagged the continuation path as "unreliable for self-draft and should be superseded by the terminal-path flush"). **This plan closes that remainder.**
- **#1730 / PR #1739**: Original `failed`/`abandoned` deferred-delivery fallback (`_deliver_deferred_self_draft_fallback`), now EMAIL-only. Predecessor pattern; not modified here.
- **#1797 / PR #1807**: Email-completed-path flush. Parallel transport path; not modified.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1796 (#1794) | Added the completed-path terminal flush of `deferred_self_draft_text`. | Superseded the continuation path **additively** — it added the flush but never removed or guarded the re-enqueue at `session_executor.py:2247-2304`. Both handlers now fire on a terminal-turn deferral. |

**Root cause pattern:** The fix was applied at the delivery layer (flush) without
retiring the now-redundant recovery layer (re-enqueue) for the same trigger. The
two handlers share no claim over `drafter-fallback` steering, so both act on it.

## Data Flow

1. **Entry point**: Agent's final-turn message reaches the message drafter
   (`bridge/message_drafter.py`). Empty-promise / wire-format violation detected.
2. **Defer**: Delivery is deferred; `deferred_self_draft_pending` +
   `deferred_self_draft_text` are persisted to `extra_context`, and a
   `SELF_DRAFT_INSTRUCTION` steering message (sender `drafter-fallback`) is pushed
   to the Redis steering queue.
3. **Session goes terminal** (no next turn to drain steering).
4. **Handler A — flush** (`session_health.py: flush_deferred_self_draft_sync`, via
   `finalize_session`): reads `deferred_self_draft_text`, dedups on
   `self_draft_completed_flush_sent:{session_id}`, writes the raw text to the
   telegram/email outbox. Steering queue untouched.
5. **Handler B — re-enqueue** (`session_executor.py:2247-2304`):
   `pop_all_steering_messages()` returns the `drafter-fallback` message;
   `enqueue_agent_session()` spawns a context-blind continuation → emits
   "no substantive results."
6. **Output**: user receives raw flush **and** the contradictory "nothing to report."

The fix targets **step 5**: partition leftover steering by sender; do not
re-enqueue `drafter-fallback` messages (the flush at step 4 owns them).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the suppress-vs-resume policy — see Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies existing in-repo control flow and has no external dependencies.

## Solution

### Key Elements

- **Sender-partitioned re-enqueue**: In the steering-cleanup block, split
  `pop_all_steering_messages()` results into `drafter-fallback` messages and
  everything else. Only the non-`drafter-fallback` messages are eligible for
  re-enqueue as a continuation. `drafter-fallback` steering is dropped here
  (already popped, so not leaked) because the terminal delivery path owns it —
  the telegram sync flush (`flush_deferred_self_draft_sync`) on the completed path,
  or the email async fallback (`_deliver_deferred_self_draft_fallback`) on the
  failed/abandoned path. The suppression is transport-agnostic, so it protects both.
- **Preserve legitimate continuations**: Genuine steering (e.g. a human message
  that arrived mid-session) still re-enqueues exactly as today. Only the
  self-draft rewrite instruction is suppressed.
- **Observability**: log at INFO when `drafter-fallback` steering is suppressed
  on a terminal path, naming the session, so the coordination between flush and
  re-enqueue is auditable.

### Flow

Terminal-turn deferral → flush delivers deferred text (Handler A) → steering
cleanup pops leftover → **partition by sender** → `drafter-fallback` dropped
(flush owns it), other senders re-enqueued as before → user receives one coherent
message (the flush), no blind continuation.

### Technical Approach

- Modify the re-enqueue block at `agent/session_executor.py:2247-2304`:
  - After `leftover = pop_all_steering_messages(...)`, partition:
    `fallback = [m for m in leftover if m.get("sender") == "drafter-fallback"]`
    and `carry = [m for m in leftover if m.get("sender") != "drafter-fallback"]`.
  - If `fallback` is non-empty, log an INFO line that self-draft steering was
    suppressed on the terminal path (flush is the sole handler).
  - Only build `combined_text` / call `enqueue_agent_session(...)` when `carry`
    is non-empty. If `carry` is empty, skip the re-enqueue entirely.
  - No shared `"drafter-fallback"` constant exists — the sender is a bare literal
    at both existing sites: the **push** side (`agent/output_handler.py:1050`,
    peeked at `:992` via `peek_steering_sender(...) == "drafter-fallback"`) and now
    the **suppression** side in `session_executor.py`. (Note: the `SELF_DRAFT_INSTRUCTION`
    *body* lives in `bridge/message_drafter.py:622-629`, but the steering **sender**
    literal is pushed from `output_handler.py`, not from `message_drafter.py`.)
    Introduce a shared module-level constant (e.g. `DRAFTER_FALLBACK_SENDER` in
    `agent/output_handler.py`) referenced at both the push and suppression sites,
    OR — if that widens the surface beyond the Small appetite — keep the literal at
    both sites with a cross-reference comment. Prefer the shared symbol.
- No change to the flush (`session_health.py`) — it already works and remains the
  sole handler. No shared Redis claim key is needed because the suppression is
  structural (sender-based), not timing-based.
- `enqueue_agent_session` is **not** extended with `claude_session_uuid` in this
  plan (that is the heavier "resume the transcript" alternative — see Open
  Questions). The Small-appetite fix is suppression, which fully satisfies the
  acceptance criteria.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The re-enqueue block is already wrapped in `try/except` that logs at WARNING and drops on failure. The new partition logic runs before `enqueue_agent_session`; assert that a `drafter-fallback`-only leftover produces **no** `enqueue_agent_session` call and logs the suppression at INFO (observable behavior, not a silent swallow).

### Empty/Invalid Input Handling
- [ ] Leftover message with missing/`None` `sender` key → treated as non-`drafter-fallback` (carries through to re-enqueue), matching today's behavior. Add a test asserting a `sender`-less message still re-enqueues.
- [ ] Empty `carry` after partition → re-enqueue is skipped, no exception.

### Error State Rendering
- [ ] The user-visible outcome (the flush message) is delivered by the untouched flush path; assert the regression test observes exactly the flush delivery and **no** "no substantive results" continuation output.

## Test Impact

- [ ] `tests/unit/test_deferred_self_draft_completed.py` — UPDATE: add a regression case for the terminal-turn re-enqueue suppression (this is the #1794 home file; keep existing flush tests intact).
- [ ] `tests/unit/test_deferred_self_draft_completed.py` — UPDATE: add an **email-path** regression case. Because `flush_deferred_self_draft_sync` early-returns for email and `_deliver_deferred_self_draft_fallback` owns the email `failed`/`abandoned` terminal path, assert that an email session's terminal `drafter-fallback` steering is likewise suppressed from re-enqueue (no context-blind email continuation) — verifying the suppression is genuinely transport-agnostic, not telegram-only.
- [ ] `tests/unit/test_steering.py` — no change expected; `pop_all_steering_messages` semantics are unchanged (partition happens at the call site, not in the steering API).

No other existing tests are affected — the change is confined to the re-enqueue call site and is additive (a filter before an existing branch), leaving all non-`drafter-fallback` re-enqueue behavior byte-for-byte identical.

## Rabbit Holes

- **Extending `enqueue_agent_session` to carry `claude_session_uuid` / a full
  `ResumeContext`** so the continuation resumes the prior transcript. This is the
  "make the rewrite actually work" alternative — larger surface, threads a new
  param through the queue, and is not required to satisfy the acceptance criteria.
  Deferred (see No-Gos / Open Questions).
- **Embedding the flagged message body into the steering payload / `extra_context`**
  so a rewrite agent could see what it's rewriting. Same rationale — only needed if
  the resume-continuation policy is chosen over suppression.
- **Rewording `SELF_DRAFT_INSTRUCTION`'s escape hatch** globally. Tempting, but it
  affects every self-draft flow (not just the terminal path) and risks regressing
  the legitimate "genuinely nothing to report" case. Kept as an Open Question, not
  bundled into this fix.

## Risks

### Risk 1: A legitimate continuation is accidentally suppressed
**Impact:** If a terminal session carried genuine non-`drafter-fallback` steering plus a `drafter-fallback` message, over-broad suppression could drop the genuine continuation.
**Mitigation:** Partition by sender and re-enqueue the `carry` (non-fallback) subset intact. Only `drafter-fallback` messages are dropped. A test asserts a mixed leftover still re-enqueues the genuine messages.

### Risk 2: `drafter-fallback` sender string drifts
**Impact:** No shared constant exists today — the literal appears at the push site (`agent/output_handler.py:1050`, plus the peek at `:992`) and will now appear at the suppression check in `session_executor.py`. If one changes but not the other, the guard silently stops matching.
**Mitigation:** Introduce a single shared `DRAFTER_FALLBACK_SENDER` constant referenced at the push, peek, and suppression sites so the string is defined once. If the shared constant is deferred, add a cross-reference comment at every site naming the others.

## Race Conditions

No new race conditions introduced. The flush (Handler A) and re-enqueue (Handler B)
already run in sequence within the same terminal-finalization path; the fix removes
Handler B's action on `drafter-fallback` rather than adding concurrent access. The
flush retains its own SETNX dedup (`self_draft_completed_flush_sent:{session_id}`),
which is unchanged.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2197] Resume-the-transcript continuation (threading `claude_session_uuid` / `ResumeContext` through `enqueue_agent_session` and embedding the flagged body in the payload). Only pursued if the PM chooses the "rewrite continuation" policy over suppression in Open Questions; tracked by this same issue until that decision, then split if chosen.
- Rewording `SELF_DRAFT_INSTRUCTION`'s escape-hatch line — kept as an Open Question; not a code change in this plan unless the PM directs it.

## Update System

No update system changes required — this feature is purely internal control-flow within the worker's session-finalization path.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to how deferred self-draft steering is handled on terminal sessions. No new tool, MCP surface, or CLI entry point.

## Documentation

### Feature Documentation
- [ ] Update `docs/plans/deferred_self_draft_completed_path_flush.md`'s successor note OR add a short section to the relevant delivery feature doc noting that `drafter-fallback` steering is suppressed from re-enqueue on terminal paths (flush is the sole handler). Target: `docs/features/` — locate the message-delivery/self-draft feature doc and add the terminal-handling coordination note.
- [ ] If no dedicated delivery feature doc exists, add inline documentation (see below) and note the behavior in the PR body.

### Inline Documentation
- [ ] Comment at the partition point in `session_executor.py` explaining WHY `drafter-fallback` is dropped here (the terminal delivery — the telegram sync flush in `session_health.py` or the email async fallback in `_deliver_deferred_self_draft_fallback` — owns it; ref #1794 and #2197).
- [ ] Comment at the sender **push** site (`agent/output_handler.py:1050`, plus the peek at `:992`) cross-referencing the suppression, if a shared `DRAFTER_FALLBACK_SENDER` constant is not introduced.

## Success Criteria

- [ ] A self-draft deferral on a session's terminal turn never spawns a context-blind continuation that emits "no substantive results" when the prior turn produced output.
- [ ] The user receives one coherent terminal message (the flush), not a raw flush plus a contradictory "nothing to report."
- [ ] Non-`drafter-fallback` steering on a terminal session still re-enqueues as a continuation (no regression).
- [ ] Regression test added to `tests/unit/test_deferred_self_draft_completed.py` covering terminal-turn suppression, the mixed-sender carry case, and the **email-path** suppression case.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

**Known limitation (in scope by design):** Suppression removes the *contradiction*
(the false "no substantive results" continuation), but it does **not** improve the
*content* the user receives — on a terminal deferral the user still gets only the
originally-deferred draft (which may be an empty-promise text), never the real
diagnosis the prior turn produced. Delivering the actual diagnosis requires the
resume-the-transcript continuation (thread `claude_session_uuid` + the flagged body
into the rewrite), which is **Open Question 1 / the No-Go**. This Small-appetite fix
deliberately stops the false negative; content quality is a separate, larger decision.

## Team Orchestration

This is a single-call-site change (one partition in `session_executor.py` plus
its regression tests and an inline/feature-doc note). Two roles suffice — a
builder that ships code, tests, and docs together, and an independent validator.
A separate test-engineer and documentarian would add sequential hand-offs without
proportional value at this appetite.

### Team Members

- **Builder (executor-guard)**
  - Name: executor-guard-builder
  - Role: Implement sender-partitioned re-enqueue suppression in `session_executor.py`, add the regression tests (terminal telegram, mixed-sender, sender-less, and email-path), and add the inline comments + feature-doc note.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Validator**
  - Name: selfdraft-validator
  - Role: Verify acceptance criteria and no-regression on genuine continuations
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement suppression, tests, and docs
- **Task ID**: build-executor-guard
- **Depends On**: none
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: executor-guard-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data
- **Parallel**: false
- Partition `leftover` from `pop_all_steering_messages()` at `agent/session_executor.py:2247-2304` into `drafter-fallback` vs `carry`.
- Skip `enqueue_agent_session` when `carry` is empty; re-enqueue only `carry` when non-empty.
- Log INFO on `drafter-fallback` suppression. Introduce a shared `DRAFTER_FALLBACK_SENDER` constant referenced at the push site (`agent/output_handler.py:1050`/`:992`) and the new suppression site; if the shared constant is deferred, add a cross-reference comment at both sites (the literal is bare at both today — no constant exists).
- Add regression tests to `tests/unit/test_deferred_self_draft_completed.py`:
  - terminal telegram session with only a `drafter-fallback` leftover → no `enqueue_agent_session` call, suppression logged, no "no substantive results" output.
  - mixed leftover (`drafter-fallback` + a genuine sender) → continuation re-enqueued with only the genuine message(s).
  - `sender`-less leftover message → still re-enqueues (carries through).
  - **email-path** terminal session with a `drafter-fallback` leftover → likewise suppressed (transport-agnostic; email delivery is owned by `_deliver_deferred_self_draft_fallback`, not the sync flush).
- Add the inline comment at the partition point and the terminal-handling coordination note to the relevant delivery feature doc.

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-executor-guard
- **Assigned To**: selfdraft-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new regression tests and lint/format.
- Confirm all success criteria met, including the email-path suppression case.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Regression tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_executor.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_executor.py` | exit code 0 |
| Suppression guard present | `grep -c "drafter-fallback" agent/session_executor.py` | output > 0 |

## Critique Results

**Verdict:** READY TO BUILD (WITH CONCERNS) — revision pass applied 2026-07-22.

Concerns raised by critique and how the revision addressed each:

1. **Push-site misattribution (fixed).** The `"drafter-fallback"` sender literal is
   NOT in `bridge/message_drafter.py` — it is pushed at `agent/output_handler.py:1050`
   (peeked at `:992`). Only the `SELF_DRAFT_INSTRUCTION` *body* lives in
   `message_drafter.py:622-629`. No shared constant exists (bare literal at both
   sites). Corrected in Technical Approach, Risk 2, Inline Documentation, and Task 1;
   the plan now recommends a shared `DRAFTER_FALLBACK_SENDER` constant.
2. **"Flush is sole handler" was imprecise (fixed).** `flush_deferred_self_draft_sync`
   (`session_health.py:2089`) early-returns for email/non-completed; the async
   `_deliver_deferred_self_draft_fallback` (`:2239`) owns the email failed/abandoned
   path. Desired Outcome, Solution, and the transport note now name both handlers, and
   an **email-path regression test** was added to Test Impact, Success Criteria, and Task 1.
3. **Content-quality limitation surfaced (fixed).** Added a "Known limitation" line to
   Success Criteria: suppression removes the contradiction but the user still receives
   only the deferred draft — delivering the real diagnosis requires the resume policy in
   Open Question 1.
4. **Agent-role heaviness (fixed, nit).** Trimmed four sequential roles (builder,
   test-engineer, documentarian, validator) to two (builder + validator) for this
   single-call-site change.

---

## Open Questions

1. **Policy: suppress vs. resume.** This plan implements **suppression** (drop
   `drafter-fallback` from re-enqueue; flush is the sole handler), which fully
   satisfies the acceptance criteria and matches #1794's stated intent. The
   alternative — make the rewrite continuation *actually* work by resuming the
   prior transcript (`claude_session_uuid` + flagged body) — is larger and
   deferred. **Confirm suppression is the desired policy.**
2. **Escape-hatch wording.** Should `SELF_DRAFT_INSTRUCTION`'s line "If your work
   produced no substantive results, say so plainly" be reworded/conditioned so it
   can't convert a context-loss into a confident false negative in other flows?
   Kept out of scope here unless you want it bundled.
