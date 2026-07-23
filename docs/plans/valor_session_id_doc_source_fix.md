---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2208
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T02:49:43Z
---

# Fix VALOR_SESSION_ID Source Attribution in agent-session-model.md

## Problem

`docs/features/agent-session-model.md` tells a reader that the `VALOR_SESSION_ID`
environment variable is set by `agent/sdk_client.py` in a `_create_options()`
method, and shows a code snippet of that method. Neither the method nor the
assignment exists. `agent/sdk_client.py` has no `_create_options()` definition
(only two stale docstring mentions) and sets no `VALOR_SESSION_ID`. The SDK-era
env builder was deleted wholesale in the harness migration (#2000).

Anyone reading this doc to understand how the bridge session id reaches
subprocesses — or to debug a session-resolution problem — is pointed at dead code
and will waste time grepping for a method that no longer exists.

**Current behavior:**
Two locations in `docs/features/agent-session-model.md` misattribute the env var:
- Line 130 (Session Lookup Chain precedence table): "Bridge session_id, set by `sdk_client.py`"
- Lines 136-148 ("VALOR_SESSION_ID Environment Variable" section): attributes it to
  `sdk_client.py` `_create_options()` and shows a fictional code snippet.

**Desired outcome:**
Both spots correctly attribute `VALOR_SESSION_ID` to `agent/session_executor.py`'s
`_harness_env` (the headless-runner path), matching the actual code and the already-correct
`docs/features/harness-abstraction.md:90`. The surrounding guidance (three-tier lookup,
hook-invisibility, `task_list_id` fallback) is preserved because it remains accurate.

## Freshness Check

**Baseline commit:** 3c0fc7ee1
**Issue filed at:** 2026-07-22T07:53:28Z
**Disposition:** Unchanged

**File:line references re-verified (against baseline 3c0fc7ee1):**
- `agent/sdk_client.py` — issue claims no `_create_options()` and no `VALOR_SESSION_ID` assignment — CONFIRMED: `grep -n "def _create_options"` returns nothing; only stale docstring mentions at lines 100 and 144; `env[...]` assignments are all `SDLC_*`/`TELEGRAM_*` keys, none set `VALOR_SESSION_ID`.
- `agent/session_executor.py:1954` — issue claims `_harness_env` now sets it — CONFIRMED: `"VALOR_SESSION_ID": session.session_id or ""` inside `_harness_env`.
- `docs/features/agent-session-model.md:130` and `:136-148` — stale attributions — CONFIRMED present.
- `docs/features/harness-abstraction.md:90` — already documents the correct source (`_harness_env`, `session.session_id`, issue #2190) — CONFIRMED; this is the canonical reference to align to.

**Cited sibling issues/PRs re-checked:**
- #2000 (harness migration, deleted SDK path) — merged; established the removal of the SDK-era env builder.
- #2190 / PR #2206 — merged (commit `ed47cccb3`); this is the cascade that surfaced this issue and established `_harness_env` as the VALOR_SESSION_ID source.

**Commits on main since issue was filed (touching referenced files):**
- `ed47cccb3` Fix WS-F AGENT_SESSION_ID resolver identifier-type mismatch (#2190) (#2206) — this IS the PR whose docs cascade surfaced the issue; already reflected in current code and in my verification. No change to the plan's premise.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** One behavioral nuance worth capturing in the doc fix: current code always
adds the `VALOR_SESSION_ID` key (empty string when `session.session_id` is falsy),
whereas the stale prose says "only set when `session_id` is non-None". The revised prose
should describe the empty-string-when-absent behavior so the fallback explanation stays
accurate.

## Prior Art

No prior issues or PRs attempted to fix this specific doc conflict. The relevant
context is the harness migration lineage:
- **#2000**: Deleted the SDK path (`ValorAgent`, `get_agent_response_sdk()`) wholesale — the origin of the now-fictional `_create_options()` reference.
- **#2190 / PR #2206**: Established `_harness_env` as the VALOR_SESSION_ID source and updated `harness-abstraction.md` correctly, but did not touch `agent-session-model.md` (explicitly out of scope for that cascade, per the issue body) — which is why this doc was left stale.

## Data Flow

Documentation-only change — no runtime data flow. For reference, the mechanism the
doc describes: `agent/session_executor.py` builds `_harness_env` (with
`VALOR_SESSION_ID = session.session_id or ""`) → passed to the headless `claude -p`
subprocess → visible to in-subprocess tools (e.g. `tools/sdlc_session_ensure.py`) via
`os.environ` → NOT visible to hooks (which run in the parent bridge process).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Two-location text edit in one Markdown file. The bottleneck is confirming the
replacement prose matches the real code, which the Freshness Check already did.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Precedence table row (line 130)**: Change "set by `sdk_client.py`" to point at
  `agent/session_executor.py`'s `_harness_env`.
- **"VALOR_SESSION_ID Environment Variable" section (lines 136-148)**: Rewrite the
  source sentence and code snippet to reflect `_harness_env` in `session_executor.py`,
  including the current "always set, empty string when absent" behavior. This
  always-set / empty-string-when-absent value detail MUST be stated directly in
  `agent-session-model.md` prose — NOT deferred to the cross-link (see the CONCERN
  below: the `harness-abstraction.md` cell reads `session.session_id` and omits the
  `or ""` nuance). Preserve the hook-invisibility paragraph and the session-registry
  cross-reference verbatim (still accurate).
- **Cross-link**: Point readers at `docs/features/harness-abstraction.md` (the env-contract
  table) ONLY for the broader `_harness_env` key list — not as the authority for this
  value's empty-string behavior, which the cell does not capture. The cross-link exists
  to reduce env-key drift, not to carry the `or ""` detail.

### Flow

Reader opens `agent-session-model.md` → reaches Session Lookup Chain table → sees correct
source (`session_executor.py` `_harness_env`) → reads the VALOR_SESSION_ID section → sees a
code snippet that matches real code → (optionally) follows the cross-link to
`harness-abstraction.md` for the full env contract.

### Technical Approach

- Edit `docs/features/agent-session-model.md` only. No code changes.
- **Re-derive exact line numbers from the live file at edit time** — the Problem section
  cites "Lines 136-148" and the task steps cite "lines 138-146"; these are approximate and
  will drift. Locate the actual "VALOR_SESSION_ID Environment Variable" heading and the line-130
  table cell by content grep, not by trusting the plan's line ranges (NIT from critique).
- Replace the line-130 table cell attribution.
- Replace the section body with corrected source, corrected snippet, and corrected
  "always set / empty when absent" prose stated **directly in this doc** (not via the
  cross-link). Keep the "Important: not available to hooks" paragraph as-is.
- Verify no other file in `docs/` repeats the `sdk_client.py` + `_create_options` + `VALOR_SESSION_ID`
  misattribution (grep already shows the hits are confined to this one file).

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — documentation-only change, no code paths touched.

### Empty/Invalid Input Handling
- Not applicable — no functions added or modified. (The doc prose is being corrected to
  describe the existing empty-string-when-absent behavior, but no code implements new input handling.)

### Error State Rendering
- Not applicable — no user-visible runtime output.

## Test Impact

No existing tests affected — this is a documentation-only correction to a Markdown file;
no test asserts on the contents of `docs/features/agent-session-model.md`, and no code
behavior changes, so no unit or integration test exercises the edited lines.

## Rabbit Holes

- Do NOT audit or "fix" the entire `agent-session-model.md` doc for other possible staleness —
  scope is strictly the VALOR_SESSION_ID misattribution (two spots).
- Do NOT touch `agent/sdk_client.py` to remove the stale `_create_options()` docstring mentions —
  that is unrelated code cleanup, out of scope for a doc-conflict bug.
- Do NOT refactor or "improve" the three-tier lookup explanation — it is accurate; only the
  source attribution is wrong.

## Risks

### Risk 1: Replacement prose drifts from code again after a future harness change
**Impact:** The doc goes stale a third time.
**Mitigation:** Add a cross-link to `docs/features/harness-abstraction.md` as the single
source-of-truth for `_harness_env` keys, so future edits have one canonical place to update.

## Race Conditions

No race conditions identified — documentation-only change, no concurrent access or shared state.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG N/A] Removing the stale `_create_options()` docstring mentions in
  `agent/sdk_client.py` — this is code cleanup unrelated to the doc conflict; not filed
  because it is trivial and not requested. Documentation scope only.

Nothing else deferred — the entire relevant fix is in scope for this plan.

## Update System

No update system changes required — this is a documentation-only correction with no
new dependencies, config files, or deploy steps.

## Agent Integration

No agent integration required — this is a documentation-only change. No tool, MCP surface,
or bridge wiring is affected.

## Documentation

### Feature Documentation
- [ ] Correct `docs/features/agent-session-model.md` line 130 (Session Lookup Chain table)
      to attribute `VALOR_SESSION_ID` to `agent/session_executor.py`'s `_harness_env`.
- [ ] Rewrite the "VALOR_SESSION_ID Environment Variable" section (lines 136-148) with the
      correct source, code snippet, and "always set / empty string when absent" behavior;
      preserve the hook-invisibility paragraph.
- [ ] Add a cross-link to `docs/features/harness-abstraction.md` as the canonical `_harness_env`
      env-contract reference.

### External Documentation Site
- Not applicable — this repo does not publish `docs/features/` to an external docs site.

### Inline Documentation
- Not applicable — no code changed.

## Success Criteria

- [ ] `docs/features/agent-session-model.md` no longer references `sdk_client.py` or
      `_create_options()` as the source of `VALOR_SESSION_ID`.
- [ ] The doc correctly names `agent/session_executor.py` / `_harness_env` as the source.
- [ ] The three-tier lookup and hook-invisibility guidance is preserved and still reads coherently.
- [ ] Documentation updated (`/do-docs` cascade check passes).

## Team Orchestration

Single documentarian task; no builder/validator pairs needed for a two-spot doc edit.

### Team Members

- **Documentarian (doc-fix)**
  - Name: doc-fixer
  - Role: Correct the two VALOR_SESSION_ID misattributions in agent-session-model.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Correct the doc misattribution
- **Task ID**: fix-doc
- **Depends On**: none
- **Validates**: (doc-only; verified by grep in Verification table)
- **Assigned To**: doc-fixer
- **Agent Type**: documentarian
- **Parallel**: false
- **Re-derive line numbers at edit time.** The ranges below ("line 130", "lines 138-146",
  "line 148") are approximate from plan time — grep for the table cell and the
  "VALOR_SESSION_ID Environment Variable" heading in the live file and edit by content, not
  by trusting these numbers (critique NIT).
- Edit the line-130 table cell: change "set by `sdk_client.py`" to reference
  `session_executor.py`'s `_harness_env`.
- Rewrite the source sentence + code snippet to reflect `agent/session_executor.py`'s
  `_harness_env`, value `session.session_id or ""`, set for typed sessions routed to the
  headless runner.
- Update the "only set when session_id is non-None" prose to describe the current
  always-set / empty-string-when-absent behavior. **State this empty-string nuance directly
  in the prose of `agent-session-model.md`** — do NOT phrase it as "see harness-abstraction.md
  for the exact value", because that doc's env-contract cell reads `session.session_id`
  and omits the `or ""` (critique CONCERN).
- Preserve the hook-invisibility paragraph and session-registry cross-reference.
- Add a cross-link to `docs/features/harness-abstraction.md` as the canonical reference for
  the broader `_harness_env` key list only (env-key drift prevention) — not as the authority
  for this value's empty-string behavior.

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: fix-doc
- **Assigned To**: doc-fixer
- **Agent Type**: documentarian
- **Parallel**: false
- Run the Verification table grep checks.
- Confirm the section still reads coherently and no dead reference remains.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No sdk_client VALOR misattribution | `grep -c "set by \`sdk_client.py\`" docs/features/agent-session-model.md` | match count == 0 |
| No fictional _create_options in this doc | `grep -c "_create_options" docs/features/agent-session-model.md` | match count == 0 |
| Correct source named | `grep -c "session_executor" docs/features/agent-session-model.md` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), LITE depth (1 Consolidated Critic). Verdict: READY TO BUILD (with concerns). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Consolidated Critic | Plan calls `harness-abstraction.md:90` "already-correct" and cross-links it as the canonical `_harness_env` source, but that cell's Source column reads `session.session_id` (no `or ""`), while real code (`session_executor.py:1954`) is `session.session_id or ""` — the cross-link target omits the very empty-string nuance the fix must convey. | fix-doc task | When writing the new snippet/prose in agent-session-model.md, do NOT phrase it as "see harness-abstraction.md for the exact value" — that cell omits `or ""`. State the always-set / empty-string-when-absent behavior directly in agent-session-model.md; keep the cross-link only for the broader env-contract key list. |
| NIT | Consolidated Critic | Line-range references are inconsistent: Problem says "Lines 136-148"; Technical Approach/Tasks say replace "lines 138-146" preserving 148 — the two ranges aren't reconciled. | fix-doc task | N/A (NIT) — re-derive exact line numbers from the live file at edit time rather than trusting the plan's ranges verbatim. |

---

## Open Questions

None. Scope is a self-contained two-spot documentation correction with the correct
source already verified against current code.
