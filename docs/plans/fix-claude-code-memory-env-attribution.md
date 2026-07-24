---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/2217
last_comment_id:
---

# Fix claude-code-memory.md env-var misattribution (SESSION_TYPE / VALOR_PARENT_SESSION_ID)

## Problem

`docs/features/claude-code-memory.md` tells a reader that the `SESSION_TYPE` and
`VALOR_PARENT_SESSION_ID` environment variables — the ones the UserPromptSubmit
hook uses to gate AgentSession creation — are set by `agent/sdk_client.py`. That
file no longer sets them. Anyone tracing how worker-spawned sessions get their
persona/parent linkage will open `sdk_client.py`, grep for those vars, find
nothing, and lose trust in the doc (or waste time hunting for the real setter).

**Current behavior:**
`docs/features/claude-code-memory.md:223` reads:

> The UserPromptSubmit hook gates creation on the presence of `SESSION_TYPE` or
> `VALOR_PARENT_SESSION_ID` environment variables, which are only set by
> `sdk_client.py` for worker-spawned sessions

The env-var builder that once lived in `sdk_client.py` was removed wholesale in
the harness migration (#2000). The vars are now set in
`agent/session_executor.py`'s `_harness_env` dict (`SESSION_TYPE` at line 1961,
`VALOR_PARENT_SESSION_ID` at line 1963).

**Desired outcome:**
The sentence attributes the two env vars to
`agent/session_executor.py`'s `_harness_env`, matching the code. No other prose
or behavior changes.

## Freshness Check

**Baseline commit:** `5940a311d66c7dd0f6bda8c28c23c2c5db7f3543`
**Issue filed at:** 2026-07-23T02:56:02Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `docs/features/claude-code-memory.md:221` (issue's cite) — the offending
  sentence now sits at **line 223** (drifted +2). Content unchanged: still
  attributes the vars to `sdk_client.py`. Still holds.
- `agent/session_executor.py:1957-1963` (issue's cite) — confirmed. `SESSION_TYPE`
  is set at line 1961, `VALOR_PARENT_SESSION_ID` at line 1963, both inside the
  `_harness_env` construction (dict opens ~line 1955). Still holds.
- `agent/sdk_client.py` — `grep -n "SESSION_TYPE\|VALOR_PARENT_SESSION_ID"`
  returns **zero** matches. The file exists but sets neither var. Confirms the
  misattribution.

**Cited sibling issues/PRs re-checked:**
- #2208 — CLOSED (the parallel `VALOR_SESSION_ID` misattribution in
  `agent-session-model.md`). Resolved by PR #2216.
- PR #2216 — MERGED 2026-07-23T03:06:45Z ("Fix VALOR_SESSION_ID doc
  misattribution to sdk_client.py"). Same root cause, different doc file and
  different env var. Did not touch `claude-code-memory.md`.

**Commits on main since issue was filed (touching referenced files):**
- `0e888123c` (Outcome-loop hardening, #2203) — touched
  `claude-code-memory.md` (honest deferred-fallback prose), NOT line 223.
  Irrelevant to this fix.
- `1090124da` (Distilled human ingest, #2299) — touched
  `claude-code-memory.md` (importance=6.0 diagram/prose), NOT line 223.
  Irrelevant to this fix.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Only drift is the +2 line shift (221 → 223). The Technical Approach
below targets the live sentence by content, not by line number, so the drift is
immaterial to the edit.

## Prior Art

- **Issue #2208 / PR #2216**: Fixed the exact same class of bug — a doc
  (`agent-session-model.md`) attributing `VALOR_SESSION_ID` to `sdk_client.py`
  after the harness migration (#2000) deleted the `sdk_client.py` env builder.
  Merged 2026-07-23. This issue (#2217) is the sibling that #2216 explicitly
  scoped out: different env vars (`SESSION_TYPE` / `VALOR_PARENT_SESSION_ID`),
  different doc file (`claude-code-memory.md`). The fix pattern is identical:
  repoint the prose to `agent/session_executor.py`'s `_harness_env`.

## Research

No relevant external findings — this is a purely internal documentation fix with
no external libraries, APIs, or ecosystem patterns involved.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

A one-sentence prose correction. The only overhead is confirming the corrected
attribution against the code (already done in the Freshness Check) and a light
review.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`docs/features/claude-code-memory.md`**: the single sentence at line ~223
  that misattributes `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID` to
  `sdk_client.py`.

### Flow

Reader opens `claude-code-memory.md` → reaches the AgentSession Lifecycle
Tracking note → reads which env vars gate creation and where they are set →
(after fix) is pointed to `agent/session_executor.py`'s `_harness_env`, greps,
finds the vars, trust preserved.

### Technical Approach

- Edit exactly one sentence in `docs/features/claude-code-memory.md`. Replace
  the clause "which are only set by `sdk_client.py` for worker-spawned sessions"
  with an attribution to `agent/session_executor.py`'s `_harness_env` (the dict
  that sets `SESSION_TYPE` and `VALOR_PARENT_SESSION_ID` for worker-spawned
  harness subprocesses).
- Target the sentence by its text content, not by line number (line drifted
  221 → 223 and could drift again before build).
- Keep the `#1001` issue link and the surrounding prose intact — only the
  setter attribution changes.
- No code changes. No changes to any other file.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. This is a documentation-only prose edit; no
  code paths are touched.

### Empty/Invalid Input Handling
- Not applicable. No functions are added or modified.

### Error State Rendering
- Not applicable. No user-visible runtime output is involved.

## Test Impact

No existing tests affected — this is a one-sentence documentation prose
correction that modifies no code, no interfaces, and no behavior, so no unit,
integration, or E2E test exercises the changed content.

## Rabbit Holes

- **Auditing the whole doc for other stale `sdk_client.py` references.** Tempting,
  but out of scope. This issue is scoped to the single known sentence. A
  broader doc audit is separate work.
- **Rewriting the surrounding paragraph for clarity.** The paragraph is correct
  apart from the one clause. Touching more invites review churn on a trivial fix.

## Risks

### Risk 1: The corrected attribution drifts again if `_harness_env` moves
**Impact:** Low. A future refactor could relocate the `_harness_env` dict,
re-staling the doc.
**Mitigation:** Attribute to the file + logical construct (`_harness_env`)
rather than a hard line number, which survives line drift. This matches how
PR #2216 fixed the sibling doc.

## Race Conditions

No race conditions identified — this is a static documentation edit with no
runtime, async, or concurrent behavior.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2208] The parallel `VALOR_SESSION_ID` misattribution in
  `agent-session-model.md` — already fixed by PR #2216; nothing to do here.

Nothing else deferred — the single-sentence correction is the entirety of the
in-scope work.

## Update System

No update system changes required — this is a documentation-only fix. Docs are
propagated to other machines by the existing `/update` git-pull path with no new
dependencies, config, or migration steps.

## Agent Integration

No agent integration required — this is a documentation prose edit. No tool, MCP
surface, or bridge wiring is added or changed.

## Documentation

The change *is* the documentation update; there is no separate feature doc to
create.

### Feature Documentation
- [ ] Edit the misattributing sentence in `docs/features/claude-code-memory.md`
      (the sole deliverable) to attribute `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID`
      to `agent/session_executor.py`'s `_harness_env`.
- [ ] Confirm the corrected sentence preserves the surrounding prose and the
      `#1001` issue link, changing only the setter attribution.

### External Documentation Site
- Not applicable — this repo publishes no external docs site for `docs/features/`.

### Inline Documentation
- Not applicable — no code changes, so no docstrings or code comments change.

## Success Criteria

- [ ] `docs/features/claude-code-memory.md` no longer contains the string
      "only set by `sdk_client.py`".
- [ ] The corrected sentence names `agent/session_executor.py` (and `_harness_env`)
      as the setter of `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID`.
- [ ] `grep -n "SESSION_TYPE" agent/session_executor.py` still confirms the code
      the doc now points to (line ~1961) — the attribution is accurate.
- [ ] No files other than `docs/features/claude-code-memory.md` changed in the PR.
- [ ] Documentation updated (`/do-docs`) — trivially satisfied; the doc edit is
      the whole change.

## Team Orchestration

Solo builder, single file, single sentence. No multi-agent orchestration needed.

### Team Members

- **Builder (doc-fix)**
  - Name: doc-fixer
  - Role: Edit the one misattributing sentence in `claude-code-memory.md`.
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Fix the misattributing sentence
- **Task ID**: build-doc-fix
- **Depends On**: none
- **Validates**: no test files (doc-only); success asserted via grep in Verification
- **Assigned To**: doc-fixer
- **Agent Type**: builder
- **Parallel**: false
- In `docs/features/claude-code-memory.md`, locate the sentence containing
  "only set by `sdk_client.py` for worker-spawned sessions".
- Replace the `sdk_client.py` attribution with `agent/session_executor.py`'s
  `_harness_env` (the dict that sets `SESSION_TYPE` and `VALOR_PARENT_SESSION_ID`).
- Preserve the `#1001` issue link and all surrounding prose.
- Verify no other file was touched.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Old misattribution gone | `grep -c "only set by \`sdk_client.py\`" docs/features/claude-code-memory.md` | match count == 0 |
| New attribution present | `grep -c "session_executor.py" docs/features/claude-code-memory.md` | output > 0 |
| Code still matches doc | `grep -c "SESSION_TYPE" agent/session_executor.py` | output > 0 |
| Only the doc changed | `git diff --name-only main -- . ':!docs/plans' \| grep -v '^docs/features/claude-code-memory.md$'` | exit code 1 |

## Open Questions

None. The fix is a single, code-verified sentence correction with no scope or
approach ambiguity.
