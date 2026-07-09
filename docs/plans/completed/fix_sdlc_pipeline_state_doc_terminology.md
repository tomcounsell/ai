---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1970
last_comment_id:
---

# Fix stale PM-era terminology in sdlc-pipeline-state.md

## Problem

`docs/features/sdlc-pipeline-state.md` still describes the SDLC pipeline session with retired PM-era vocabulary and invocation examples. A reader following this doc would run commands that do not match the codebase and would form a wrong mental model of how session resolution works.

**Current behavior:**

The doc claims:
- Pipeline state lives on "the PM session's `stage_states` field" (line 3, 22, 114, 116).
- `sdlc_session_ensure` creates an `AgentSession` with `session_type="pm"` (line 39).
- The bridge short-circuit "Confirms `session_type == "pm"`" (line 47) and falls through when `session_type != "pm"` (line 54).
- Stage markers and queries are invoked as `python -m tools.sdlc_stage_marker`, `python -m tools.sdlc_stage_query`, `python -m tools.sdlc_session_ensure` (lines 10-11, 17, 36, 65, 68, 81).

None of this matches current code:
- `tools/_sdlc_utils.py` filters on `session_type="eng"` at lines 168, 197, 284, 309. `"pm"` was retired by the PM/Dev role-merge (#1691/#1900).
- The canonical CLI is the unified `sdlc-tool` wrapper (`scripts/sdlc-tool`, hardlinked to `~/.local/bin/sdlc-tool`), documented in the project `CLAUDE.md` Quick Commands table and `docs/features/sdlc-tool-resolver.md`. Subcommands are kebab-case: `stage-marker`, `stage-query`, `session-ensure`, `verdict`, `dispatch`, `next-skill`, `meta-set`.

**Desired outcome:**

`docs/features/sdlc-pipeline-state.md` reads accurately against current code: "Eng session" / `session_type="eng"` throughout, and invocation examples use `sdlc-tool <subcommand>` instead of `python -m tools.sdlc_*`.

## Freshness Check

**Baseline commit:** 01214eaceb2d12d1d5ba9825ab2447bb9308c5bc
**Issue filed at:** 2026-07-09T09:33:25Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/_sdlc_utils.py:168,197,284,309` — issue claimed these filter on `session_type="eng"` — still holds (verified via grep).
- `docs/features/sdlc-pipeline-state.md` — all 14 stale references (lines 3, 10-11, 17, 22, 36, 39, 47, 54, 65, 68, 81, 114, 116) confirmed present at their cited locations.
- `sdlc-tool --help` — confirms the seven kebab-case subcommands including `stage-marker`, `stage-query`, `session-ensure`.

**Cited sibling issues/PRs re-checked:**
- #1691 / #1900 (PM/Dev role-merge) — referenced as the origin of the `pm`→`eng` retirement; not re-opened.
- #1958 / PR #1969 — the DOCS-stage run that auto-filed this issue; unrelated diff, out of scope here.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since` on the doc returns empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The doc's last commit was #1676/#1677, predating the PM→Eng merge — confirming pre-existing drift. No drift since the issue was filed today.

## Prior Art

Skipped per Small-appetite / no-code guidance. This is a single-file documentation correction; there is no prior implementation to mine. The origin of the terminology change (PM/Dev role-merge, #1691/#1900) is already captured in the Freshness Check.

## Research

No relevant external findings — this is a purely internal documentation correction with no external libraries, APIs, or ecosystem patterns involved.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a mechanical, evidence-backed documentation edit to one file. The only judgment call is the accurate replacement wording for the "cross-role debugging" example (see Technical Approach).

## Prerequisites

No prerequisites — this work has no external dependencies. `sdlc-tool` is already on PATH (`~/.local/bin/sdlc-tool`) for verifying subcommand names.

## Solution

### Key Elements

- **Terminology pass**: Replace every "PM session" / `session_type="pm"` reference in `docs/features/sdlc-pipeline-state.md` with "Eng session" / `session_type="eng"`.
- **Invocation pass**: Replace every `python -m tools.sdlc_*` example with the equivalent `sdlc-tool <subcommand>` call (kebab-case module name).
- **Accuracy nuance**: Correct the fall-through example on line 54 so it names a currently-valid non-eng session type rather than the retired "Dev session".

### Flow

Reader opens `docs/features/sdlc-pipeline-state.md` → every session reference reads "Eng session" → every command example is copy-pasteable as `sdlc-tool ...` → the doc matches `tools/_sdlc_utils.py` and the CLAUDE.md convention.

### Technical Approach

Edit exactly one file: `docs/features/sdlc-pipeline-state.md`. The complete set of edits:

| Line(s) | Current | Replacement |
|---------|---------|-------------|
| 3 | "the PM session's `stage_states`" | "the Eng session's `stage_states`" |
| 10-11 | `python -m tools.sdlc_stage_marker --stage PLAN ...` | `sdlc-tool stage-marker --stage PLAN ...` |
| 17 | `python -m tools.sdlc_stage_query --issue-number 941` | `sdlc-tool stage-query --issue-number 941` |
| 22 | "resolves the PM session in this order" | "resolves the Eng session in this order" |
| 36 | `python -m tools.sdlc_session_ensure --issue-number 941 ...` | `sdlc-tool session-ensure --issue-number 941 ...` |
| 39 | `session_type="pm"` | `session_type="eng"` |
| 47 | `session_type == "pm"` | `session_type == "eng"` |
| 54 | `session_type != "pm"` (e.g., a Dev session during cross-role debugging) | `session_type != "eng"` (e.g., a Teammate session) |
| 65, 68 | `python -m tools.sdlc_session_ensure --kill-orphans ...` | `sdlc-tool session-ensure --kill-orphans ...` |
| 81 | `python -m tools.sdlc_stage_query --issue-number N` | `sdlc-tool stage-query --issue-number N` |
| 114 | "scanning PM sessions by `issue_url`" | "scanning Eng sessions by `issue_url`" |
| 116 | "the same `stage_states` field on the PM session" | "the same `stage_states` field on the Eng session" |

Notes on non-mechanical decisions:
- **Line 54 wording.** Since the PM/Dev merge collapsed both into `eng`, the "Dev session during cross-role debugging" example is obsolete. The remaining non-eng session type is `teammate` (conversational; see `docs/features/eng-session-architecture.md`). Replace the parenthetical with "e.g., a Teammate session" so the fall-through example stays accurate.
- **"Key Files" table (lines 100-109).** Leave the `tools/sdlc_stage_marker.py`, `sdlc_stage_query.py`, `sdlc_session_ensure.py` file paths unchanged — those Python modules still exist and back the `sdlc-tool` subcommands. The table documents implementation files, not the invocation surface, so it is already accurate.
- **Env var / `find_session` mechanics** (lines 43-57) describe resolution logic that is still current apart from the `pm`→`eng` string; only the session-type literals change.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a documentation-only edit with no executable code paths.

### Empty/Invalid Input Handling
Not applicable — no functions are added or modified.

### Error State Rendering
Not applicable — no user-visible runtime output is changed.

## Test Impact

No existing tests affected — this change edits only `docs/features/sdlc-pipeline-state.md`, a Markdown documentation file with no code behavior. No test asserts on the prose or command examples in this doc, so no unit, integration, or E2E test references it.

## Rabbit Holes

- **Do not** sweep the same PM→Eng terminology fix across sibling docs (`docs/features/sdlc-tool-resolver.md`, `docs/features/eng-session-architecture.md`, etc.). Those are separate documents with their own review context; broadening scope turns a one-file chore into an open-ended docs audit. Filed as a No-Go below.
- **Do not** touch `tools/_sdlc_utils.py` or any code. The code is already correct; the doc is what drifted.
- **Do not** rewrite the "Key Files" table paths — the Python modules are the real implementation and their names are accurate.

## Risks

### Risk 1: Blind find/replace corrupts the "Key Files" table or an accurate `Dev`/`Teammate` reference
**Impact:** A naive `s/PM/Eng/` or `s/pm/eng/` could alter file paths or introduce an inaccurate session-type name on line 54.
**Mitigation:** Use the explicit line-by-line edit table in Technical Approach. Verify the "Key Files" table paths are unchanged after editing (Verification row).

## Race Conditions

No race conditions identified — this is a single-file, synchronous documentation edit with no concurrent access, async operations, or shared mutable state.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1970] The PM→Eng terminology also appears in `docs/features/sdlc-tool-resolver.md` (e.g., "PM `AgentSession`'s `stage_states`", "scanning PM sessions"). Correcting sibling docs is deliberately excluded from this issue, whose scope is the single file `docs/features/sdlc-pipeline-state.md`. If a broader docs sweep is wanted, file a follow-up issue.

## Update System

No update system changes required — this is a documentation-only edit to an existing file. No new dependencies, config, or migration steps; nothing propagates through `/update`.

## Agent Integration

No agent integration required — this edits a Markdown doc. No MCP surface, `.mcp.json` entry, or bridge import is involved. The `sdlc-tool` CLI referenced in the corrected examples already exists and is already on PATH.

## Documentation

This plan *is* the documentation change. The deliverable is the corrected `docs/features/sdlc-pipeline-state.md`.

### Feature Documentation
- [ ] Edit `docs/features/sdlc-pipeline-state.md` per the Technical Approach edit table (PM→Eng terminology + `python -m tools.sdlc_*`→`sdlc-tool` invocations).
- [ ] Confirm the `docs/features/README.md` index entry for `sdlc-pipeline-state.md` still describes the file accurately (title/summary unchanged by this edit; no index update expected).

### External Documentation Site
Not applicable — this repo publishes docs as in-repo Markdown; no Sphinx/MkDocs build.

### Inline Documentation
Not applicable — no code changes.

## Success Criteria

- [ ] `docs/features/sdlc-pipeline-state.md` contains no occurrence of `session_type="pm"`, `session_type == "pm"`, `session_type != "pm"`, or "PM session".
- [ ] `docs/features/sdlc-pipeline-state.md` contains no `python -m tools.sdlc_` invocation examples.
- [ ] Session references read "Eng session" and command examples read `sdlc-tool <subcommand>`.
- [ ] The "Key Files" table still lists the `tools/sdlc_*.py` module paths unchanged.
- [ ] Only `docs/features/sdlc-pipeline-state.md` is modified (git diff touches exactly one file).

## Team Orchestration

Single builder, single file. No parallel fan-out needed.

### Team Members

- **Builder (docs-edit)**
  - Name: docs-editor
  - Role: Apply the Technical Approach edit table to `docs/features/sdlc-pipeline-state.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Apply terminology and invocation edits
- **Task ID**: build-doc-edit
- **Depends On**: none
- **Validates**: Verification table below
- **Assigned To**: docs-editor
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every row of the Technical Approach edit table to `docs/features/sdlc-pipeline-state.md`.
- Replace line 54's "Dev session during cross-role debugging" with "Teammate session".
- Leave the "Key Files" table (`tools/sdlc_*.py` paths) unchanged.

### 2. Verify
- **Task ID**: validate-doc-edit
- **Depends On**: build-doc-edit
- **Assigned To**: docs-editor
- **Agent Type**: documentarian
- **Parallel**: false
- Run every command in the Verification table and confirm expected results.
- Confirm `git diff --name-only` lists only `docs/features/sdlc-pipeline-state.md`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No `pm` session_type literals remain | `grep -c 'session_type == "pm"\|session_type != "pm"\|session_type="pm"' docs/features/sdlc-pipeline-state.md` | match count == 0 |
| No "PM session" phrase remains | `grep -c 'PM session' docs/features/sdlc-pipeline-state.md` | match count == 0 |
| No `python -m tools.sdlc_` examples remain | `grep -c 'python -m tools.sdlc_' docs/features/sdlc-pipeline-state.md` | match count == 0 |
| Eng terminology present | `grep -c 'Eng session\|session_type="eng"' docs/features/sdlc-pipeline-state.md` | output > 0 |
| `sdlc-tool` invocations present | `grep -c 'sdlc-tool ' docs/features/sdlc-pipeline-state.md` | output > 0 |
| Key Files table paths preserved | `grep -c 'tools/sdlc_stage_marker.py' docs/features/sdlc-pipeline-state.md` | output > 0 |
| Only one file changed | `git diff --name-only main -- docs/features/ \| grep -vc 'sdlc-pipeline-state.md'` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

None. The change is fully specified by the edit table; the one judgment call (line 54 wording) is resolved to "Teammate session" per current architecture.
