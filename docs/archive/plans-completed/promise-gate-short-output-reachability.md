---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/2421
last_comment_id: 5089367903
---

# Promise Gate Reachability for Short-Output Replies

## Problem

The promise gate is a safety check that blocks the agent from telling a human "I'll
follow up" / "I'll report back" when nothing exists to fulfill that follow-up. It
classifies such text as a *forward deferral* and, unless the message cites a verifiable
scheduled delivery, refuses to send it, steering the agent to rewrite (the *self-draft*
path).

**Current behavior:**

The gate never runs on any reply under 200 characters. `draft_message` in
`bridge/message_drafter.py` has a short-output early return (lines 1015–1039) that fires
when *all* of these hold: `len(raw_response) < SHORT_OUTPUT_THRESHOLD` (200), not an SDLC
session, no artifacts, no `?`, and no fenced code block. That branch runs only the
wire-format validator `_validate_for_medium` and returns — the empty-promise check
`_detect_empty_promise` lives at line 1063, *after* the early return, so it is
structurally unreachable for short messages.

A real delivered message (172 chars) proved the hole:

```
On it — adding the "Dr. Chappelle's Answer to Overthinking" newsletter card
(March 23rd 2026) to the /resources Newsletters tab. I'll follow up with the
PR once it's ready.
```

The gate *would* have blocked it (`action=block`, `class_=forward_deferral`), but the
early return shipped it verbatim. The promised follow-up never happened. Short messages
are the highest-risk population for empty promises, and they are exactly the population
the gate cannot see.

Two adjacent gaps compound this:
1. **Terminal-flush bypass** — `flush_deferred_self_draft_sync` (`agent/session_health.py:2089`)
   delivers a deferred draft on terminal session paths applying only path-conversion and
   the narration gate; it never re-runs the promise gate.
2. **Drafter-path audit silence** — the drafter's promise check calls regex-only
   `_detect_empty_promise` and writes nothing to `logs/classification_audit.jsonl` (only
   the CLI path audits via `_write_promise_audit`), which is why this bug was invisible
   until a session was traced by hand.

**Desired outcome:** Every human-facing message the drafter produces is evaluated by the
promise gate regardless of length; drafter-path gate decisions are auditable; and the
documentation accurately states which paths are gated.

## Freshness Check

**Baseline commit:** `9e0e0ab5cf8ca450c927ff6d31a96679b4c51f43`
**Issue filed at:** 2026-07-27T09:01:27Z
**Disposition:** Unchanged (negligible line drift)

**File:line references re-verified:**
- `bridge/message_drafter.py:60` — `SHORT_OUTPUT_THRESHOLD = 200` — still holds (confirmed by grep).
- `bridge/message_drafter.py:1015–1039` — short-output early return with the 5-condition gate — still holds; `if` begins at line 1015, returns at 1039.
- `bridge/message_drafter.py:1063` — `_detect_empty_promise(stripped_text.lower())` after the early return — still holds at line 1063. Note: the full path evaluates the promise on `stripped_text` (post-narration-strip), while the short path only holds `raw_response`.
- `agent/session_health.py:2089` — `flush_deferred_self_draft_sync` applies `convert_local_paths_to_attachments` + `is_narration_only`, no promise gate — still holds.
- `docs/features/promise-gate.md:52` — claims the worker path is "Gated via `_detect_empty_promise` in the drafter" — still holds; the claim is inaccurate for <200-char messages.

**Cited sibling issues/PRs re-checked:**
- #1219, #2211, #2303, #2139, #1370 — all referenced as CLOSED origin/hardening issues in the same defect class; no re-open detected. #2211 and #2303 landed as the terminal-flush attachment fixes this plan builds beside (commits `520ac0ee1`, `e246ba29d`).

**Commits on main since issue was filed (touching referenced files):** none — the issue was filed today (2026-07-27) and no commit has since landed on `bridge/message_drafter.py`, `bridge/promise_gate.py`, or `agent/session_health.py`.

**Active plans in `docs/plans/` overlapping this area:** none touching the drafter promise path.

**Notes:** Bug reproduced live against current main — importing `_detect_empty_promise` /
`_evaluate_promise_heuristic` on the exact 172-char incident text yields `block` /
`forward_deferral`, and `extract_artifacts` + `_validate_for_medium` on that text confirm
every short-path condition holds. The defect is present and reproducible.

## Prior Art

- **#1219**: *Audit: agent should never make false promises — tighten gates across all delivery paths* (CLOSED) — the origin epic. Its gated-call-site table omitted both the short-output path and the terminal flush.
- **#2211**: *Terminal-turn self-draft steering always loses the race; fallback flush strips attachments and delivers validator-rejected text* (CLOSED) — same architectural shape (a bypass shipping validator-rejected text), fixed only for the local-file-path violation class. The promise class in the same flush is still unfixed (adjacent gap #1).
- **#2303**: *Attachment channel for deliver_system_notice* (CLOSED) — further hardening of the terminal-flush path, again scoped to attachments.
- **#2139**: *Promise gate can detect 'I'll report back' but nothing can fulfill it* (CLOSED) — companion feature giving the agent a legitimate way to make a keepable promise.
- **#1370**: *three send paths with divergent filters* (CLOSED) — ancestor audit of exactly this class of drift.
- **#1680**: *Reposition message drafter from rewriting summarizer to pass-through validation filter* (CLOSED/merged) — established the current verbatim-pass-through drafter that this fix extends.
- **Sibling defects from the same session trace** (`tg_psyoptimal_-1003743854645_413`, per the tracking-issue comment): **#2420** (PM fire-and-forget — why the promise in the incident message could never be kept) and **#2422** (merge-guard cross-repo blind spot). #2420 and this issue compound: one made the promise unkeepable, the other let it ship. This plan fixes only the "let it ship" half; keepability is #2420's scope.

## Research

No relevant external findings — this is a purely internal bridge change (regex heuristic
already implemented in `bridge/promise_gate.py`). Proceeding with codebase context.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| #1219 | Enumerated four gated call sites for the promise gate | The short-output fast path (added later for latency) and the terminal flush were never in the enumeration, so the gate's coverage silently regressed below "all delivery paths" |
| #2211 / #2303 | Hardened the terminal flush for attachment/local-path handling | Scoped only to the local-path violation class; the promise class in the same flush was out of scope and stayed unguarded |

**Root cause pattern:** The promise gate lives at a single point in `draft_message`
*after* an early-return fast path, and terminal delivery has its own chokepoint that never
imported the gate at all. Coverage is asserted at the epic level ("all paths") but
enforced per-call-site, so every new delivery path silently opts out until someone wires
it in. The fix must centralize the gate evaluation so short-path and full-path share one
call, and explicitly extend coverage to the terminal chokepoint (or file it).

## Data Flow

1. **Entry point**: Worker finishes a turn; `draft_message(raw_response, session, medium=...)` is called to normalize agent output before delivery.
2. **Short-output branch** (`< 200` chars, no artifacts, no `?`, no fence, non-SDLC): today runs only `_validate_for_medium`, returns `raw_response` verbatim. **← the hole.**
3. **Full path**: strips narration → composes → `_validate_for_medium` → `_detect_empty_promise(stripped_text.lower())` → on block, returns `needs_self_draft=True`.
4. **Output**: `MessageDraft` returned to `output_handler`; `needs_self_draft=True` routes to the self-draft steering path (`agent/output_handler.py`), where the agent is nudged to rewrite instead of the text shipping.

The fix inserts the promise evaluation into step 2 so both step 2 and step 3 funnel
through one shared gate call that also writes the drafter-path audit record.

## Architectural Impact

- **New dependencies**: none — reuses `bridge.promise_gate._detect_empty_promise` / `_write_promise_audit`, already imported in this module's neighborhood.
- **Interface changes**: none to `draft_message`'s public signature. Adds one private helper in `bridge/message_drafter.py`.
- **Coupling**: unchanged — `message_drafter` already depends on `promise_gate`.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — the change is a hoisted call plus an audit write; revert is a single-file diff.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is well-understood from the issue's recon)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (regex heuristic and audit
writer already exist in `bridge/promise_gate.py`).

## Solution

### Key Elements

- **Shared drafter promise gate** — one private helper `_gate_short_promise` (or equivalent) in `bridge/message_drafter.py` that evaluates `_detect_empty_promise` on the exact text about to ship AND writes the drafter-path audit record, returning whether the text is a blocked empty promise.
- **Short-output branch calls the gate** — the early-return branch invokes the helper before returning the verbatim text; on a block it returns `MessageDraft(text="", needs_self_draft=True, artifacts=artifacts)` exactly as the wire-format-violation arm already does.
- **Full path routes through the same helper** — line 1063's inline `_detect_empty_promise` is replaced by the shared helper so the drafter's *full* path also writes an audit record (closing gap #2 for both paths, not just the short one).
- **Documentation truth-up** — `docs/features/promise-gate.md` states exactly which paths the drafter gates (both length regimes) and notes the terminal-flush follow-up.

### Flow

Agent finishes turn → `draft_message` → **short-output branch** → `_gate_short_promise(text)`
→ (blocked?) → `needs_self_draft=True` → self-draft steering nudge → agent rewrites

### Technical Approach

- Gate the **exact bytes that would ship**. The short path deliberately delivers `raw_response` verbatim, so evaluate `_detect_empty_promise(raw_response.lower())` there — this gates precisely what the human would receive. (Confirmed live: the incident text blocks on both `raw_response` and narration-stripped forms, so parity with the full path holds for realistic short messages.)
- Order the short-branch checks so a wire-format violation still takes its existing self-draft arm; add the promise check alongside it. Either violation OR empty-promise promotes to `needs_self_draft=True`, mirroring the full path's `if is_empty_promise or violations:` logic.
- The shared helper writes the audit record via `bridge.promise_gate._write_promise_audit` with `source="drafter"` (new source tag distinguishing it from the CLI path's `source`), `transport=medium`, and `session_id` derived from `session`. The write is best-effort and never raises (the audit helper already swallows its own exceptions).
- Respect the `PROMISE_GATE_ENABLED` kill switch: `_detect_empty_promise` → `_evaluate_promise_heuristic` does not itself check `_gate_enabled()`, so the helper must consult `_gate_enabled()` and skip the block (still auditing, or not) when disabled — matching how the CLI path honors the kill switch. Verify the exact contract during build against `bridge/promise_gate._gate_enabled`.
- Keep the regex-only heuristic. No LLM call is added on any path — the short path's latency guarantee (Risk 1 / D5a in `docs/plans/message-drafter.md`) is preserved.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_write_promise_audit` already wraps its body in `try/except` and logs at debug — assert (via the existing `test_promise_gate_audit.py` patterns) that a drafter-path block writes an audit entry with `source="drafter"`, and that an audit-write failure does not break drafting.
- [ ] No new bare `except Exception: pass` blocks are introduced; the helper propagates nothing.

### Empty/Invalid Input Handling
- [ ] `draft_message("")` / whitespace-only already early-returns before the short-output branch — add/confirm a test that the empty-input path is unaffected (no promise evaluation, no audit write).
- [ ] Confirm a short non-promise message (e.g. `"Done. Committed abc1234."`) still passes through verbatim and is NOT promoted to self-draft (guard against false positives).

### Error State Rendering
- [ ] Assert the blocked short message yields `needs_self_draft=True` and `text=""` so the self-draft steering path (not a verbatim send) is taken — the user-visible failure path.

## Test Impact

- [ ] `tests/unit/test_message_drafter.py` — UPDATE: add `test_short_output_forward_deferral_triggers_self_draft` mirroring the existing `test_short_output_local_path_triggers_self_draft` (line 280), using the exact 172-char incident text as the fixture; assert `needs_self_draft is True`.
- [ ] `tests/unit/test_message_drafter.py::test_short_response_passes_through_verbatim` (line 172) — UPDATE only if needed: confirm a non-promise short reply is unchanged (regression guard against false positives). Likely no change required.
- [ ] `tests/unit/test_promise_gate_audit.py` — UPDATE: add a case asserting the drafter path writes an audit entry with `source="drafter"` on a blocked short message.
- [ ] `tests/unit/test_promise_gate.py` — no change expected (heuristic itself is untouched).

## Rabbit Holes

- **Broadening the forward-deferral regex.** The existing pattern matched the incident text correctly; the defect is reachability, not detection quality. Do NOT touch the heuristic — it only adds false-positive risk.
- **Redesigning the terminal-flush remediation.** What a terminal flush should *do* with a promise-flagged deferred draft (there is no live agent to self-draft) is a genuine product-behavior question, not a wiring fix. Keep it out of this plan — see No-Gos.
- **Reordering the short-path length gate around narration stripping.** Stripping before the `< 200` length check would change which branch fires for some inputs. Gate the verbatim `raw_response` instead; don't restructure the length gate.

## Risks

### Risk 1: False positives on benign short acknowledgments
**Impact:** A legitimate short reply ("On it — done, see abc1234.") gets promoted to self-draft, adding a round-trip.
**Mitigation:** The heuristic is unchanged and already blocks only forward-deferrals lacking a verifiable delivery reference; messages carrying an artifact (commit hash, PR, URL) never reach the short-output branch (the `has_any_artifacts` gate routes them to the full path). Add a regression test asserting a non-promise short reply passes through verbatim.

### Risk 2: Latency regression on the short path
**Impact:** The fast path exists to bound per-message latency; adding a check could erode it.
**Mitigation:** `_detect_empty_promise` is pure regex with no I/O or LLM call. Add a micro-benchmark task measuring the short path before/after; the expected delta is sub-millisecond. If a measurable regression appears, it is explicitly accepted as a safety cost (per the acceptance criteria) — but none is expected.

## Race Conditions

No race conditions identified — `draft_message` is a synchronous transformation over its
input text (the `async def` performs no concurrent I/O in the touched paths), and the
audit write is an append to a local JSONL file guarded by the existing best-effort
`try/except`. No shared mutable state is introduced.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2423] **Terminal-flush promise gate** (adjacent gap #1) — `flush_deferred_self_draft_sync` never re-runs the promise gate, so a promise-flagged draft that loses the self-draft race is delivered verbatim on terminal paths. This is a real second hole, but its correct remediation is a distinct design question: at terminal-flush time the session is already terminal, so there is no live agent to self-draft — the flush must instead *substitute* an honest fallback or suppress, a product-behavior decision unlike the pure wiring fix in this plan. Filed as #2423 so it is not silently dropped, satisfying AC bullet 5.

Everything else is in scope: the primary short-output fix, the drafter-path audit
(adjacent gap #2, folded in via the shared helper), the regression test, the latency
measurement, and the doc truth-up.

## Update System

No update system changes required — this is a purely internal bridge change with no new
dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change to the output-drafting
path. No new CLI entry point, MCP surface, or bridge import is needed; `draft_message` is
already called by the existing worker → output_handler flow.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/promise-gate.md` — correct the line (~52) that claims blanket drafter coverage; state that BOTH the short-output (<200 char) and full drafter paths are now gated, and that drafter-path decisions now write `source="drafter"` audit entries. Add a note that the terminal-flush path is tracked separately in #2423.

### Inline Documentation
- [ ] Update the `draft_message` docstring (currently claims at lines ~968–971 that violation promotion happens on BOTH return paths) to state that the *empty-promise* promotion now also happens on both paths.
- [ ] Comment on the shared helper explaining it gates the verbatim short-path bytes and writes the drafter-path audit.

## Success Criteria

- [ ] `_detect_empty_promise` (via the shared helper) is evaluated for human-facing messages under `SHORT_OUTPUT_THRESHOLD`.
- [ ] A regression test asserts the exact 172-char incident text (a <200-char forward-deferral ack, no artifacts, no `?`, no fence) is blocked and yields `needs_self_draft=True`.
- [ ] A test asserts a benign short non-promise reply still passes through verbatim (no false-positive promotion).
- [ ] Drafter-path blocks write an audit entry (`kind="promise_gate"`, `source="drafter"`) to `logs/classification_audit.jsonl`; a test asserts this.
- [ ] Short-output latency measured before/after; the fast path's latency guarantee is preserved (or a regression explicitly accepted).
- [ ] `docs/features/promise-gate.md` accurately states which paths are gated.
- [ ] Adjacent gap #1 (terminal-flush bypass) is filed as #2423 and linked in No-Gos.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (drafter-gate)**
  - Name: drafter-gate-builder
  - Role: Add the shared promise-gate helper, wire it into the short-output branch and the full path, and update docstrings/docs
  - Agent Type: builder
  - Domain: conversational-UX (bridge output path)
  - Resume: true

- **Validator (drafter-gate)**
  - Name: drafter-gate-validator
  - Role: Verify the incident text is blocked, the benign short reply passes verbatim, the audit entry is written, and latency is unregressed
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Wire the short-output promise gate
- **Task ID**: build-short-gate
- **Depends On**: none
- **Validates**: tests/unit/test_message_drafter.py, tests/unit/test_promise_gate_audit.py
- **Assigned To**: drafter-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a private helper in `bridge/message_drafter.py` that runs `_detect_empty_promise` on the given text, honors the `PROMISE_GATE_ENABLED` kill switch (via `bridge.promise_gate._gate_enabled`), writes a best-effort audit record (`_write_promise_audit`, `source="drafter"`, `transport=medium`, session-derived `session_id`), and returns whether the text is a blocked empty promise.
- In the short-output branch (lines 1015–1039), call the helper on `raw_response`; on a block return `MessageDraft(text="", needs_self_draft=True, artifacts=artifacts)`. Preserve the existing wire-format-violation arm; either condition promotes.
- Replace the inline `_detect_empty_promise(stripped_text.lower())` at line 1063 with the shared helper so the full path also audits.
- Update the `draft_message` docstring and the helper's inline comment.

### 2. Add regression + audit tests
- **Task ID**: build-tests
- **Depends On**: build-short-gate
- **Validates**: tests/unit/test_message_drafter.py, tests/unit/test_promise_gate_audit.py
- **Assigned To**: drafter-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `test_short_output_forward_deferral_triggers_self_draft` using the exact 172-char incident text; assert `needs_self_draft is True` and `text == ""`.
- Add a false-positive guard test: a benign short reply passes through verbatim.
- Add an audit test asserting a drafter-path block writes an entry with `source="drafter"`.

### 3. Measure short-path latency
- **Task ID**: build-latency-check
- **Depends On**: build-short-gate
- **Assigned To**: drafter-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Micro-benchmark `draft_message` on a short non-promise reply before/after the change (or benchmark the helper in isolation); record the delta in the PR description. Expected: sub-millisecond, no regression.

### 4. Terminal-flush follow-up issue (already filed as #2423)
- **Task ID**: build-followup-issue
- **Depends On**: none
- **Assigned To**: drafter-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Adjacent gap #1 (terminal-flush promise gate in `flush_deferred_self_draft_sync`) is already filed as #2423 (referencing #2421 and #2211) and tagged in the No-Gos `[SEPARATE-SLUG #2423]`. No action needed unless the scope note in #2423 needs refining during build.

### 5. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-short-gate, build-tests
- **Assigned To**: drafter-gate-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/promise-gate.md` to state both drafter length regimes are gated and drafter-path decisions audit with `source="drafter"`; note the terminal-flush follow-up.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-short-gate, build-tests, build-latency-check, build-followup-issue, document-feature
- **Assigned To**: drafter-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full verification table; confirm all success criteria including the audit entry and doc truth-up.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_message_drafter.py tests/unit/test_promise_gate_audit.py -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/message_drafter.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/message_drafter.py` | exit code 0 |
| Incident text blocked | `python -c "import asyncio; from bridge.message_drafter import draft_message; d=asyncio.run(draft_message('On it — adding the newsletter card to the /resources Newsletters tab. I\'ll follow up with the PR once it\'s ready.')); print(d.needs_self_draft)"` | output contains True |
| Regression test present | `grep -c "forward_deferral_triggers_self_draft\|forward.deferral" tests/unit/test_message_drafter.py` | output > 0 |
| Drafter audit source wired | `grep -c "drafter" bridge/message_drafter.py` | output > 0 |
| No LLM call on short path | `grep -c "openrouter\|haiku\|anthropic\|openai" bridge/message_drafter.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Resolved Decisions

1. **Short-path evaluation basis** — RESOLVED: gate the verbatim `raw_response.lower()` (what actually ships on the short path). The short path delivers `raw_response` verbatim, so gating those exact bytes gates precisely what the human receives; live testing confirmed the incident text blocks on both `raw_response` and narration-stripped forms, so full-path parity holds for realistic short messages. See Technical Approach.
2. **Terminal-flush scope** — RESOLVED: split adjacent gap #1 out as follow-up #2423 (filed, OPEN). Terminal-time remediation is a distinct product decision (no live agent to self-draft at flush time — the flush must substitute an honest fallback or suppress, not nudge a rewrite). This plan fixes only the short-output reachability hole. See No-Gos `[SEPARATE-SLUG #2423]`.
