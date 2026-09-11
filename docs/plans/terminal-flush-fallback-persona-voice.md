---
status: Ready
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/3290
---

# Terminal-flush promise fallback in Valor's voice

## Problem

When the promise gate blocks a session's held final reply at terminal-flush time, there is no
live agent left to redraft it, so `agent/session_health._gate_terminal_promise` substitutes a
fixed constant, `TERMINAL_PROMISE_FALLBACK_MESSAGE`. That constant is delivered verbatim to the
human in Telegram and reads as a system bulletin, not as Valor:

> An outbound safety filter held back this session's final message. The work may have finished
> normally; if something you expected is missing, ask again in a new message.

Tom received this in the **Eng: Valor** group (thread `tg_valor_-1003449100931_1457`) and flagged
it as off-persona. It violates the standing `CLAUDE.md` rule that no raw error or internal
narration reaches the human: it names an internal mechanism ("outbound safety filter"), uses
internal vocabulary ("this session's final message"), and speaks about Valor in the third person
instead of as Valor.

**Current behavior:** gate blocks a terminal-flush reply, human gets machine narration.

**Desired outcome:** human gets a short first-person message that reads as Valor talking to a
teammate, names no filter / session / gate, asserts nothing the system cannot verify, and still
returns `allow` from `_evaluate_promise_heuristic`.

## Freshness Check

**Disposition: Unchanged.** Baseline `e75a7aebb` (main). Issue #3290 was filed minutes before this
plan, and its recon was performed by direct read of the code at that same baseline, so there is no
drift window. Re-verified at plan time:

- `agent/session_health.py:2537` — constant present with the exact quoted text.
- `docs/features/promise-gate.md:79` — the only other occurrence of the literal.
- `#3135` confirmed CLOSED; its two constraints are carried forward as a code comment at
  `agent/session_health.py:2530-2536`, so they are live constraints and not merely issue history.
- No commits have landed on `agent/session_health.py`, `agent/notification_copy.py`,
  `bridge/promise_gate.py`, or `tests/unit/test_deferred_self_draft_completed.py` since the issue
  was filed.
- No active plan in `docs/plans/` touches the promise gate or the terminal-flush path. No overlap.

Bug still reproducible by inspection: the constant is unconditionally returned on a `block`
verdict, so any blocked terminal flush delivers exactly this text.

## Research

No external research performed. This is purely internal copy and a constant relocation inside this
repo. No libraries, APIs, or ecosystem patterns are involved. Proceeding on codebase context.

## Prior Art

- **#3135 (closed)** — fixed the *truth* defect in this same constant. The prior wording asserted
  the session's work had failed, which a block verdict cannot know; a cleanly-completed session was
  telling the human its work didn't finish. The fix corrected the claim and left the register
  untouched. Its two constraints must survive this change and are restated under No-Gos.
- **#2423** — established the substitution-over-suppression design for this route. Suppression was
  rejected because it reintroduces the #1796 swallowed-reply class. This plan does not reopen that.
- **#1877** — established `agent/notification_copy.py` as the single source of truth for
  user-facing session-lifecycle copy, with the contract "send sites import these names; they never
  inline the literal string". This is the convention `TERMINAL_PROMISE_FALLBACK_MESSAGE` is
  currently outside of.

**Why the previous fix was incomplete:** #3135 correctly scoped itself to truth, treating the text
as a payload to be made accurate rather than as human-facing copy subject to the persona rule. The
mechanical reason the register drifted is that the constant lives in `session_health.py` among
control-flow helpers, not in `notification_copy.py` beside the two constants that set the voice.
Fixing only the words without moving the constant leaves that drift pressure in place.

## Appetite

**Small.** One constant reworded and relocated, its import sites updated, one test extended, one
doc section updated. No behavior change, no new control flow.

## Solution

1. Rewrite the constant in first-person Valor voice.
2. Relocate it from `agent/session_health.py` to `agent/notification_copy.py`, joining
   `INTERRUPT_NO_RESUME` and `FAILURE_NOTICE` under the #1877 contract. Import it at its use site.
3. Extend the existing #3135 test with a second property: the text is free of internal vocabulary.
4. Update `docs/features/promise-gate.md` where it quotes the old string.

### Chosen wording

```
I didn't send my last message here. It made a commitment I couldn't back up.
If you were waiting on something from me, just ask again.
```

Why each clause is defensible given **only** "the gate withheld the message":

| Clause | Justification |
|---|---|
| "I didn't send my last message here." | Exactly what happened. First person, because Valor's own outbound check is Valor's, not a third party's. |
| "It made a commitment I couldn't back up." | Restates the block verdict itself. Both block classes (`forward_deferral`, `behavioral_change`) are precisely "a commitment with no evidence behind it". Claims nothing about the work. |
| "If you were waiting on something from me, just ask again." | Conditional. Invents no pending request and promises no future delivery. |

Deliberately absent: any claim about whether work completed (the #3135 trap), the words
filter/session/gate/message-gate, em-dashes (repo rule for published text), and any
forward-deferral phrasing the heuristic blocks.

## Data Flow

Unchanged by this plan; recorded so the reviewer can confirm the blast radius is one constant.

```
held self-draft reply
  -> finalize_session (models/session_lifecycle.py)
     -> agent/session_health.flush_deferred_self_draft_sync      [telegram, sync, no event loop]
     -> agent/session_health._deliver_deferred_self_draft_fallback [email, async]
        both call:
        -> _gate_terminal_promise(text, transport=..., session_id=...)
           -> bridge.promise_gate._evaluate_promise_heuristic(text)   [regex only, never LLM]
              verdict.action == "block"
                -> return TERMINAL_PROMISE_FALLBACK_MESSAGE   <-- the only thing changing
              verdict.action == "allow"
                -> return text unchanged
  -> rpush to outbox -> bridge -> Telegram / email
```

The constant is read at exactly one site (`_gate_terminal_promise`'s block branch) and asserted by
identity in tests. Moving its definition changes no call graph.

## Technical Approach

**Relocation.** Add the constant to `agent/notification_copy.py` with a docstring bullet matching
the existing two, carrying the #3135 constraints forward into the comment so they remain
discoverable at edit time. Delete the definition from `agent/session_health.py` and import it
where `_gate_terminal_promise` needs it. Use a function-local import (`# noqa: PLC0415`) matching
the existing `INTERRUPT_NO_RESUME` import style at `_deliver_terminal_interrupt_notice`, to stay
consistent with the file and avoid any import-cycle risk.

**Re-export for compatibility.** `agent.session_health.TERMINAL_PROMISE_FALLBACK_MESSAGE` is
imported by name in `tests/unit/test_deferred_self_draft_completed.py` (two sites). Per the repo's
NO LEGACY CODE rule, do **not** leave a compatibility alias: update the test imports to the new
canonical location instead. Verify by grep that no other module imports the name.

**Heuristic verification is deterministic.** The terminal-flush route always uses the regex
`_evaluate_promise_heuristic`, never the LLM layer, so the allow/block property of the new wording
is fully testable offline with no API key. Confirmed by direct execution during planning: the
chosen wording returns `action="allow"`, `reason="no_promise_detected"`.

**Test design.** Extend `test_substitute_message_passes_the_heuristic` into two assertions, or add
a sibling test. The second property is a *negative vocabulary* assertion over a list of banned
substrings (`filter`, `session`, `gate`) checked case-insensitively. This is the assertion that
prevents a future edit silently regressing to narration; the heuristic assertion alone would not
catch it.

## Step by Step Tasks

1. Add `TERMINAL_PROMISE_FALLBACK_MESSAGE` to `agent/notification_copy.py` with the new wording,
   a module-docstring bullet, and the #3135 constraint comment.
2. Delete the constant and its comment block from `agent/session_health.py`; import the name from
   `agent.notification_copy` inside `_gate_terminal_promise`.
3. `grep -rn "TERMINAL_PROMISE_FALLBACK_MESSAGE"` and update every import site to the new location.
4. Extend the #3135 coverage in `tests/unit/test_deferred_self_draft_completed.py`: assert
   heuristic-allow AND absence of the banned vocabulary. Keep
   `test_async_email_fallback_promise_substituted` green (it asserts by identity, so it should be
   unaffected once its import is repointed).
5. Update `docs/features/promise-gate.md` Terminal flush section (~line 79) to quote the new
   string and describe the voice constraint alongside the truth constraint.
6. `grep -rn "outbound safety filter"` returns nothing.
7. `python -m ruff check` and `python -m ruff format`.
8. Narrow test run: `scripts/pytest-clean.sh tests/unit/test_deferred_self_draft_completed.py`.

## Rabbit Holes

- **Rewriting the promise heuristic.** Tempting, since the gate's pattern list is what constrains
  natural phrasing. Out of scope; the chosen wording already passes.
- **Auditing every other outbound string for persona voice.** A real concern but a different piece
  of work. If this PR's grep surfaces other off-persona human-facing constants, note them for a
  follow-up issue rather than fixing them here.
- **Making the email and Telegram routes say different things.** They share one constant today and
  should keep sharing it.

## No-Gos

Carried forward from #3135 and non-negotiable:

- The fallback must NOT claim the session's work failed, succeeded, or is incomplete. A block
  verdict carries no information about work completion.
- The fallback must NOT invent a pending request or imply the human asked for something specific.
- The fallback must NOT itself be an empty promise: `_evaluate_promise_heuristic(...)` must return
  `action == "allow"`.

New for this issue:

- No mention of filters, sessions, gates, or any internal mechanism.
- No em-dashes (repo rule for published text).
- No compatibility alias left behind at the old location.
- No change to gate behavior: substitution stays substitution, the kill switch and fail-open paths
  are untouched.

## Risks

| Risk | Mitigation |
|---|---|
| New wording trips the heuristic it is the fallback for (infinite-narrowing failure). | Verified by direct execution at plan time; locked by a test assertion. |
| A hidden import site of the constant breaks on relocation. | Step 3 is an explicit repo-wide grep before the move is considered done. |
| The vocabulary assertion is too blunt and blocks a legitimate future word. | Keep the banned list to the three words that caused this report, checked as substrings, with a comment explaining why each is banned. |
| Reviewer reads this as reopening #3135's truth question. | Plan states explicitly that truth constraints are preserved, not revisited; the test proves it. |

## Success Criteria

- `_evaluate_promise_heuristic(TERMINAL_PROMISE_FALLBACK_MESSAGE).action == "allow"`.
- A test asserts the text contains none of `filter`, `session`, `gate` (case-insensitive).
- All pre-existing tests in `tests/unit/test_deferred_self_draft_completed.py` pass, including both
  #3135 tests.
- `grep -rn "outbound safety filter"` returns no hits.
- The constant is defined in `agent/notification_copy.py` and nowhere else.
- Ruff check and format clean.

## Documentation

- `docs/features/promise-gate.md` — Terminal flush section: replace the quoted string, and state
  the persona-voice constraint next to the existing truth constraint so a future editor sees both.
- `agent/notification_copy.py` — module docstring gains a bullet for the new constant.

No new `docs/infra/` doc: this plan adds no dependency, service, external API call, or deployment
change.

## Open Questions

None blocking. One judgment call already made and recorded here for the critique to challenge:
relocating the constant to `agent/notification_copy.py` is included in this PR rather than
deferred, on the grounds that it is a one-constant move and that leaving it in place preserves the
drift pressure that produced the bug. If the critique judges the relocation to be scope creep, the
wording change alone satisfies every acceptance criterion in #3290 and the move can be dropped.
