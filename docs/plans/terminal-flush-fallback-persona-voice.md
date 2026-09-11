---
status: Ready
revision_applied: true
revision_applied_at: 2026-09-11T09:01:37Z
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
I didn't send my last message here. It didn't meet the bar I hold myself to.
If you were waiting on something from me, just ask again.
```

Why each clause is defensible given **only** "the gate withheld the message":

| Clause | Justification |
|---|---|
| "I didn't send my last message here." | Exactly what happened. First person, because Valor's own outbound check is Valor's, not a third party's. |
| "It didn't meet the bar I hold myself to." | True for **every** block class without asserting what the offending text said. See the note below on why the more specific phrasing was rejected. |
| "If you were waiting on something from me, just ask again." | Conditional. Invents no pending request and promises no future delivery. |

**Rejected phrasing, and why (critique finding, History & Consistency).** The first draft
read "It made a commitment I couldn't back up." That is *not* entailed by a block verdict.
The gate has two block classes, and `_BEHAVIORAL_CHANGE_PATTERNS` (`bridge/promise_gate.py:279-287`)
matches bare acknowledgments of the *human's* statement: `you're right`, `good point`,
`makes sense`, `point taken`, `fair point`. A message blocked for one of those made no
commitment at all. Shipping "I made a commitment" in that case asserts something false about
text the human never saw — which is precisely the overreach class #3135 was filed to remove.
"It didn't meet the bar I hold myself to" is true under both block classes because the bar
*is* the gate, so the clause is verdict-shaped rather than content-shaped.

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
imported by name in `tests/unit/test_deferred_self_draft_completed.py` at **three** sites, verified
by grep at revision time: **L1153**, **L1217**, and **L1226** (the last is a parenthesized
multi-name import). An earlier draft of this plan said "two sites" and its Test Impact table
omitted L1153; two critics caught it independently. Because Step 2 deletes the constant with **no**
compatibility alias, a missed import site is an `ImportError` at collection time that takes down
the entire test module, not just one test. Step 3's grep is therefore a gate, not a formality.

**Import placement (critique finding, Risk & Robustness).** Import the constant at **module level**
in `agent/session_health.py`, not function-locally inside `_gate_terminal_promise`. That function
wraps its whole body in `try: ... except Exception:` and **fail-opens by returning the original,
un-gated message**. A function-local import that failed would therefore be swallowed into the
fail-open path and silently deliver the very promise text the gate exists to withhold. A
module-level import fails loudly at import time instead. There is no cycle risk:
`agent/notification_copy.py` imports nothing but `__future__`, so it cannot import
`session_health` back. This deliberately diverges from the function-local
`from agent.notification_copy import INTERRUPT_NO_RESUME` at `_deliver_terminal_interrupt_notice`;
that site is not inside a fail-open handler, so the same hazard does not apply to it.

**Heuristic verification is deterministic.** The terminal-flush route always uses the regex
`_evaluate_promise_heuristic`, never the LLM layer, so the allow/block property of the new wording
is fully testable offline with no API key. Confirmed by direct execution during planning: the
chosen wording returns `action="allow"`, `reason="no_promise_detected"`.

**Test design.** Extend `test_substitute_message_passes_the_heuristic` into two assertions, or add
a sibling test. The second property is a *negative vocabulary* assertion over the banned terms
`filter`, `session`, `gate`.

Match on **word boundaries**, not raw substrings (critique finding, Scope & Value):
`re.search(rf"\b{term}", text, re.IGNORECASE)`. A raw `in` check makes the guard fire on innocent
words that merely contain a banned term — `possession` and `obsession` both contain `session` —
so a perfectly on-persona future rewrite could fail the guard for no jargon reason. Use a leading
`\b` only, so that inflections (`filtered`, `sessions`, `gated`) are still caught.

This is the assertion that prevents a future edit silently regressing to narration; the heuristic
assertion alone would not catch it.

## Step by Step Tasks

1. Add `TERMINAL_PROMISE_FALLBACK_MESSAGE` to `agent/notification_copy.py` with the new wording,
   a module-docstring bullet, and the #3135 constraint comment.
2. Delete the constant and its comment block from `agent/session_health.py`; import the name from
   `agent.notification_copy` inside `_gate_terminal_promise`.
3. `grep -rn "TERMINAL_PROMISE_FALLBACK_MESSAGE"` and update **all three** test import sites
   (L1153, L1217, L1226) to the new location. The grep must return zero references to
   `agent.session_health` for this name before the step is done.
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
- **Human-facing validation (critique finding, Scope & Value):** every other criterion here is
  mechanical, and the defect Tom reported is a subjective one — a regex-clean string can still read
  as stilted. Before the PR is opened, the exact final text is read cold, as a chat message, and
  judged on one question: does this sound like Valor talking to a teammate? The reviewer in
  `/do-pr-review` is asked the same question explicitly. This criterion is a judgment gate, not an
  assertion, and it is recorded here so it cannot be quietly skipped.

## Documentation

- [ ] `docs/features/promise-gate.md` — Terminal flush section (~line 79): replace the quoted
      string with the new wording, and state the persona-voice constraint next to the existing
      truth constraint so a future editor sees both. This is the only doc in `docs/features/` that
      quotes the constant.
- [ ] `agent/notification_copy.py` — module docstring gains a bullet for the new constant, matching
      the format of the existing `INTERRUPT_NO_RESUME` / `FAILURE_NOTICE` bullets.

No new `docs/infra/` doc: this plan adds no dependency, service, external API call, or deployment
change.

## Update System

**No migration required.** This plan touches no Popoto model, no model field, and no Redis key
shape. It changes one module-level string constant and its defining module. `scripts/update/migrations.py`
needs no new entry and `MIGRATIONS` is unmodified.

No raw Redis operations are introduced; the plan does not touch persistence at all.

**Deploy note (not a migration):** the constant is read in-process by the worker, so the new
wording takes effect for running services only after `./scripts/valor-service.sh restart`, which
`/update` performs on the standard post-merge path. No special deploy step beyond that.

## Agent Integration

**No MCP exposure required.** This plan adds no Python tool, no CLI entrypoint, and no callable
surface. It relocates one string constant between two existing modules. There is nothing for an
agent to invoke, so no MCP server registration, no `tools/` addition, and no
`docs/tools-reference.md` entry.

The only agent-visible effect is indirect and intended: when the promise gate blocks a terminal
flush, the text the human receives changes.

## Test Impact

| Test | File | Disposition | Why |
|---|---|---|---|
| `test_promise_flagged_deferred_draft_substituted_not_delivered` | `tests/unit/test_deferred_self_draft_completed.py` (L1153) | **UPDATE** (import only) | Third import site, missed by the first draft of this plan and caught by two critics. Asserts the delivered payload equals the constant by identity, so the wording change does not affect it; only its import location moves. Missing it is an `ImportError` at collection time for the whole module. |
| `test_substitute_message_passes_the_heuristic` | `tests/unit/test_deferred_self_draft_completed.py` (L1217) | **UPDATE** | Repoint its import of `TERMINAL_PROMISE_FALLBACK_MESSAGE` to `agent.notification_copy`, and extend it with the new negative-vocabulary assertion (or add a sibling test for that property). The existing heuristic-allow assertion is kept verbatim: it is the #3135 guarantee. |
| `test_async_email_fallback_promise_substituted` | `tests/unit/test_deferred_self_draft_completed.py` (L1226) | **UPDATE** (import only) | Asserts the delivered text equals the constant *by identity*, not by literal string, so the wording change does not affect it. Only its import location changes. |
| `test_promise_reply_substituted_at_terminal_flush` / kill-switch / benign-text tests | `tests/unit/test_deferred_self_draft_completed.py` | **KEEP** | Exercise gate behavior, not the fallback wording. Must stay green as the regression guard that this PR changed copy and nothing else. |
| (new) vocabulary-guard assertion | `tests/unit/test_deferred_self_draft_completed.py` | **ADD** | Asserts the constant contains none of `filter`, `session`, `gate` (case-insensitive substring check). This is the assertion that makes the persona property durable rather than a one-time edit. |

**No DELETE / REPLACE dispositions.** No existing test becomes obsolete; #3135's coverage is
preserved in full and extended.

**Narrow run command:** `scripts/pytest-clean.sh tests/unit/test_deferred_self_draft_completed.py`.
No full-suite run — per `CLAUDE.md`, parallel worktrees collide on Redis state.

**Expected-failure scan:** `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` found no xfail
markers related to the promise gate or terminal flush, so there is no xfail to convert.

## Critique Results

Critique run 2026-09-11 (FULL depth, appetite=Small): 1 blocker, 4 concerns, 1 nit. Verdict
NEEDS REVISION. All findings addressed in this revision pass. The blocker was independently
reported by two critics and re-verified by hand against the test file before being accepted.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency, Risk & Robustness | Plan said the constant is imported at "two sites" in the test module; there are three (L1153, L1217, L1226). Step 2 deletes the constant with no alias, so the missed site is an `ImportError` at collection time that fails the whole module. | Technical Approach ("Re-export for compatibility" rewritten), Step 3, Test Impact (new row for L1153) | Verified by hand: `grep -n "from agent.session_health import TERMINAL_PROMISE_FALLBACK_MESSAGE"` returns L1153 and L1217; L1226 is a parenthesized multi-name import that grep for the bare name also catches. Grep for the name, not for the import line shape. |
| CONCERN | History & Consistency | Chosen wording "It made a commitment I couldn't back up" is not entailed by a block verdict: `_BEHAVIORAL_CHANGE_PATTERNS` matches bare acknowledgments (`you're right`, `good point`, `makes sense`) that contain no commitment. Same overreach class #3135 was filed to remove. | Solution → Chosen wording (text changed to "It didn't meet the bar I hold myself to"), plus a "Rejected phrasing, and why" note | The replacement clause is verdict-shaped, not content-shaped: the bar *is* the gate, so it is true under both block classes without asserting what the withheld text said. Re-verified `action == "allow"` on the new string. |
| CONCERN | Risk & Robustness | Plan did not say whether the new `agent.notification_copy` import goes inside `_gate_terminal_promise`'s `try` block, which catches `Exception` broadly and fail-opens by delivering the original un-gated text. | Technical Approach (new "Import placement" paragraph) | Put the import at module level in `agent/session_health.py`. Function-local inside the `try` means an ImportError is swallowed into the fail-open path and ships the promise text the gate exists to withhold. No cycle risk: `notification_copy.py` imports only `__future__`. |
| CONCERN | Scope & Value | The negative-vocabulary guard bans `session` as a raw substring, but `possession`/`obsession` contain it, so an on-persona future wording could fail the guard for no jargon reason. | Technical Approach → Test design | Use `re.search(rf"\b{term}", text, re.IGNORECASE)`. Leading `\b` only, so inflections (`filtered`, `sessions`, `gated`) are still caught. |
| CONCERN | Scope & Value | All Success Criteria are mechanical/regex-based; none validate the actual subjective defect Tom reported ("reads as off-persona"). The plan could satisfy every criterion and still ship stilted copy. | Success Criteria (new human-facing validation bullet) | Judgment gate, not an assertion: the final text is read cold as a chat message before the PR opens, and `/do-pr-review` is asked the same question explicitly. |
| NIT | Scope & Value | "I didn't send my last message here" may momentarily confuse a cold reader, since the substitute is the only message they actually see. | Accepted, not changed | Any alternative that removes the ambiguity ("I wrote something and held it") asserts more about the withheld text than the verdict supports. The mild ambiguity is the cheaper cost. Recorded so a future editor does not re-litigate it blind. |

**Scope ruling.** The Scope & Value critic was asked to rule on the Open Question below and
judged the relocation to `agent/notification_copy.py` **justified scope, not creep** — it matches
the existing #1877 convention, it is a one-constant move, and the plan gave explicit reasoning. No
finding was raised against it. The Open Question is therefore resolved in favor of keeping the
relocation in this PR.

---

## Open Questions

**Resolved.** The one judgment call in this plan — whether relocating the constant to
`agent/notification_copy.py` belongs in this PR or should be deferred — was put to the Scope &
Value critic, which ruled it justified scope rather than creep. No open questions block the build.
