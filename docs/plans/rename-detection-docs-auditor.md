---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2741
last_comment_id: 5311784123
---

# Rename Detection in docs_auditor — Leave It Deleted

## Problem

The docs auditor's rename channel runs on every audit and cannot produce a correct
result for any input.

**Current behavior:**

`_git_log_follow_renames` asks `git log --follow --diff-filter=R` about a path. `--follow`
walks *backward* from the query path, so the newest record it returns is the one that
*created* that path — `renames[0][1]` is always the queried path itself. Every caller
reaches that line only after confirming the path does **not** exist. So the two reachable
outcomes are:

| Queried path was… | Query returns | Detector outcome |
|---|---|---|
| once a rename **destination** | a record whose destination is the queried path | degenerate self-substitution, permanently withheld by the #2728 existence invariant |
| only a rename **source** (the real case) | nothing | detector skips entirely |

The case these detectors exist for — a doc referencing a file that has since moved — is the
second row, and it returns no records at all. Meanwhile the fourth call site is not a fix
detector but a *suppression*: `_detect_deleted_target_issues` drops a broken reference from
its human-facing report whenever the path has a rename in its history, deferring to a fix
channel that can never fix anything. That blind spot lands precisely on the paths most
likely to have moved.

Post-#2728 the channel is a permanent `fixes_withheld > 0` generator, which is why #2739
(make withheld fixes file a deduped issue) needs it gone: a permanent generator makes that
signal meaningless.

**Desired outcome:**

The rename channel is deleted, not rebuilt. A broken reference with a rename in its history
is reported to a human instead of silently deferred, and `fixes_withheld` becomes a real
signal.

The full analysis is the 2026-08-17 premise-check comment on issue #2741 and the
`## Recon Summary` in that issue's body; this plan does not restate it.

## Freshness Check

**Baseline commit:** `bc3051ee4`
**Issue filed at:** 2026-08-13T03:37:05Z
**Disposition:** Unchanged (with a noted, non-blocking overlap)

**File:line references re-verified — all exact, zero drift:**

- `reflections/docs_auditor.py:373` — `_git_log_follow_renames` — still holds
- `:441`, `:465`, `:495`, `:1076` — the four call sites — still hold
- `:418`, `:448`, `:471` — the three fix detectors — still hold
- `:1413`-`:1417` — detector registrations in the audit loop — still hold
- `:71`, `:370`, `:1359`-`:1360` — `GIT_LOG_FOLLOW_CAP`, `_RENAME_QUERY_COUNT`, per-run reset — still hold

**Both empirical premise checks re-run on `bc3051ee4` and reproduce exactly:**

```
$ git log --follow --diff-filter=R --name-status --format= -- docs/cursor-lessons.md
R100    docs/improvements/cursor-lessons.md     docs/cursor-lessons.md

$ git log --follow --diff-filter=R --name-status --format= -- docs/plans/with-concerns-revision-recritique.md
(no output)
```

**Cited sibling issues/PRs re-checked:**

- #2739 — still OPEN. This work unblocks it; it does not depend on it.
- #2725 — CLOSED `NOT_PLANNED` 2026-08-13, explicitly superseded by #2741's stronger analysis. Its defect 2 (doc-relative link resolved against the repo root) is carried forward as rebuild-requirement 3 and is moot under deletion.
- #2728 — MERGED. Added the existence invariant that renders the channel permanently withheld rather than corrupting.
- #2759, #2744 — CLOSED. Both narrowed adjacent detector/regex behavior; neither touches the rename query.

**Commits on main since the issue was filed (touching referenced files):**

- `45d0961f9` (#2728) — added the path-existence invariant. Partially addresses: converts the channel from *corrupting* to *permanently withheld*. Already reflected in the issue's corrected analysis.
- `ffbae5b1d` (#2782) — migration-context hatch and bare-path invariant. Irrelevant to the rename query; touches the stale-term regex channel, which this plan preserves.

**Active plans overlapping this area:** `docs/plans/docs-auditor-review-gate.md` (#2739),
`status: Planning` — not in build, so there is no concurrent-edit contention. Relationship is
sequencing, not conflict: deleting this channel is what makes #2739's `fixes_withheld` signal
meaningful. No coordination action needed beyond landing this first.

**Notes:** No drift. No line-number corrections needed in Technical Approach.

## Prior Art

- **#2711 / `d7bf3ad99`**: the docs auditor wrote a bad commit to `main`. Came from the `STALE_TERMS` regex channel, not this one, but it is why automated doc writes are under scrutiny and why rebuilding a write class is unattractive.
- **#2728 (MERGED)**: added the path-existence invariant. Did not diagnose the backward-walk defect — it made the symptom safe (withheld instead of written) without removing the cause, which is exactly the state this plan resolves.
- **#2725 (CLOSED, NOT_PLANNED)**: first flagged the rename detectors picking the wrong target, framed as a "newest-commit hop" bug. That framing was wrong; the closure comment records that #2741 supersedes it.
- **#2739 (OPEN)**: review gate in front of every docs_auditor write. Downstream consumer of this deletion.

**Why previous fixes were incomplete:** both prior touches (#2728, #2725's framing) treated the
rename channel as *mis-targeting* — picking the wrong hop from a valid result set. The actual
defect is that the query direction is backward, so the result set never contains a correct
answer to walk to. Every fix aimed at hop selection was aimed at the wrong layer.

## Research

No relevant external findings — proceeding with codebase context. This is a pure deletion
inside one module, with no external libraries, APIs, or ecosystem patterns involved. The one
external-behavior question (what `git log --follow` returns for a path absent at HEAD) was
settled empirically against this repo rather than by documentation, and is recorded in the
Freshness Check above.

## Data Flow

The channel being removed, end to end:

1. **Entry point**: `audit()` iterates the resolved doc neighborhood (`:1402`).
2. **Detector dispatch** (`:1413`-`:1417`): `README.md` → `_detect_readme_broken_entries`; every other doc → `_detect_renamed_link_fixes` + `_detect_renamed_symbol_fixes`. All three append to the literal `fixes` list.
3. **Rename query**: each detector, having confirmed the referenced path is absent, calls `_git_log_follow_renames`, which shells out to `git log --follow` under a per-run cap.
4. **Apply** (`:1429`): `_apply_fixes_to_file` runs the literal `fixes` loop first, then the regex loop; the existence invariant rejects any fix introducing an absent path, and rejects land in `withheld`.
5. **Output**: `withheld` becomes `fixes_withheld` in the result dict (`:1485`), surfaced in the run summary and liveness record.

After this change, step 2 produces nothing, step 3 does not exist, and step 4 is regex-only.
Step 5 is unchanged in shape — `fixes_withheld` still exists and is still populated by the
regex channel, it simply stops being permanently nonzero.

Separately, `_detect_deleted_target_issues` (`:1040`) loses its `:1076` early-`continue`, so a
broken `.py` reference whose path has a rename in its history now reaches the findings list
instead of being dropped.

## Architectural Impact

- **New dependencies**: none. Deletion only.
- **Interface changes**: `_apply_fixes_to_file` loses its `fixes` positional parameter, becoming regex-only. It is module-private with one production call site (`:1429`) and is driven directly by tests.
- **Coupling**: decreases. The module stops shelling out to `git log` per referenced path, removing a subprocess dependency from the detector path and the per-run global counter that bounded it.
- **Data ownership**: unchanged.
- **Reversibility**: high — a single revert restores the channel. Nothing is migrated and no state is destroyed.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully settled by the issue's Decision section and recon)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It is deletion within one module,
its test file, and three doc surfaces.

## Solution

### Key Elements

- **Rename query removal**: `_git_log_follow_renames` and the per-run cap state that exists only to bound it.
- **Fix-detector removal**: the three detectors that consume the query, and their registrations in the audit loop.
- **Suppression removal**: the `:1076` early-`continue` in `_detect_deleted_target_issues`, so broken references with rename history are reported.
- **Literal-channel collapse**: `_apply_fixes_to_file` becomes regex-only once its only producers are gone.

### Flow

Audit run → doc with a broken reference → (previously: rename query → degenerate fix →
withheld) → **now**: reference reaches `_detect_deleted_target_issues` → finding filed for a
human.

### Technical Approach

Delete, in `reflections/docs_auditor.py`:

- `GIT_LOG_FOLLOW_CAP` (`:71`), `_RENAME_QUERY_COUNT` (`:370`), and the per-run reset in `audit()` (`:1359`-`:1360`).
- `_git_log_follow_renames` (`:373`).
- `_detect_renamed_link_fixes` (`:418`), `_detect_renamed_symbol_fixes` (`:448`), `_detect_readme_broken_entries` (`:471`).
- The registration block at `:1413`-`:1417`, along with the now-empty `fixes` local and its use in the `:1428` apply condition.
- The `:1076` rename suppression inside `_detect_deleted_target_issues`.

Then collapse the literal channel, which is the part the issue does not name and the main
size driver. The three deleted detectors are the only producers of `_apply_fixes_to_file`'s
literal `fixes` list, and its sole production call site is `:1429`. With no producer, the
`fixes` parameter, the literal loop (`:880`-`:904`), and the `new == ""` whole-line-delete
sentinel are dead code that NO LEGACY CODE TOLERANCE requires removing rather than leaving
as an always-empty argument.

Two pieces of prose must be rewritten rather than deleted, because the behavior they describe
survives while the reason they cite does not. `_make_stale_term_replacer`'s docstring (`:788`)
and `_apply_fixes_to_file`'s (`:841`) both justify deriving match context from the live,
already-mutated text on the grounds that the literal loop runs first and its whole-line
deletions invalidate any index computed at detection time. The live derivation stays correct
and stays — a regex fix can still shift offsets ahead of a later match within its own
`subn` pass — but the justification must be restated in terms of what remains. This is a
rewrite of the *reason*, not a tombstone: the new text describes only the new status quo.

Retain, untouched: the `_detect_stale_term_fixes` regex channel, `_absent_new_path_refs` and
the existence invariant (still applied to regex fixes), `_reject` / `withheld` /
`fixes_withheld`, and all apply-time suppression behavior.

**Anti-criterion-grep trap:** the Verification rows below assert that the deleted symbol names
no longer appear in `reflections/` and `tests/`. Any comment or test name written during the
build must therefore *paraphrase* those names rather than quote them, or the build trips its
own check. Verification greps are scoped to exclude `docs/plans/`, so this plan document is
free to name them.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The only `except Exception` in the deleted scope is `_git_log_follow_renames`' subprocess guard, which goes away with the function. No new handler is introduced. The retained handlers in `_apply_fixes_to_file` (read/write failures, each logging a warning) already have coverage and are not modified.

### Empty/Invalid Input Handling
- [ ] Confirm `_apply_fixes_to_file` still returns `(0, [])` early when handed no regex fixes — the `not fixes and not regex_fixes` guard at `:857` must be restated for the single remaining channel, not silently dropped, or a file with nothing to do gets read and rewritten needlessly.
- [ ] Confirm `audit()` handles a doc that now yields no fixes at all (the common case after this change) without touching the file.

### Error State Rendering
- [ ] `fixes_withheld` must still surface in the run summary and liveness record when the regex channel withholds a fix. This is the user-visible output path #2739 depends on; assert it survives the collapse.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py` — rename-query tests at `:363`-`:369` (cap behavior) — DELETE: the cap and the query no longer exist.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — symbol-detector tests at `:390`-`:403` — DELETE: detector removed.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — README broken-entry tests at `:782`-`:805` and the whole-line-delete sentinel test at `:1153` — DELETE: detector and sentinel removed.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — detector-patch fixtures at `:1120`-`:1123` and `:1260`-`:1263` — UPDATE: stop patching detectors that no longer exist.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — query-patch guards at `:804`, `:1683`, `:1703` — UPDATE: remove the patch, which becomes a patch of a missing attribute (and would raise).
- [ ] `tests/unit/test_docs_auditor_substrate.py` — the ~15 sites driving `_apply_fixes_to_file` with literal fixes (`:744`, `:771`, `:841`-`:1016`, `:1087`, `:1167`-`:1175`) — REPLACE: re-express against the regex channel where the assertion is about invariant/withholding behavior that still exists; DELETE where the assertion is specifically about literal-fix mechanics.
- [ ] `tests/unit/test_docs_auditor_substrate.py` — the fixture resetting the query counter at `:55`-`:57` — UPDATE: drop the reset.
- [ ] **NEW regression**: `_detect_deleted_target_issues` reports a broken `.py` reference whose path was once a rename destination. This case is suppressed on `main` and must be demonstrated red against the pre-change code, then green after — it is the behavior change the deletion buys.

## Rabbit Holes

- **Rebuilding the channel correctly.** Requires a forward query, chain walking with cycle protection and a hop cap, and frame-correct re-relativization — all three together, to restore an automated write class that already produced one bad commit on `main`. The issue's Decision section answers this: no.
- **Broadening the existence invariant or the detector regexes.** `_PATH_REF_RE` vs. the narrower detector patterns was ruled unchanged in #2759. Out of scope; do not relitigate while deleting.
- **Absorbing #2739's review gate.** Tempting because this deletion is what makes that gate's signal meaningful, but it is a separate, larger design with its own plan.
- **Rewriting the retained regex channel's suppression logic** because its docstring is being edited. Only the cited rationale changes; the behavior must not.

## Risks

### Risk 1: Deleting `_detect_readme_broken_entries` also removes the whole-line-delete capability
**Impact:** That detector had a second, non-rename branch: when a README index entry pointed at a
missing file and no rename was found, it deleted the line outright. Removing the detector removes
that capability, not just its rename branch. A broken README index entry will no longer be
auto-deleted.
**Mitigation:** This is the intended direction, not collateral. Auto-deleting a line from a human's
index file on the auditor's own judgment is exactly the unreviewed-write class #2739 exists to
gate, and the issue's Decision section prefers reporting to a human over automated correction. The
broken entry is still surfaced: `_detect_deleted_target_issues` reports broken references, and this
change *widens* what it reports by removing the `:1076` suppression. Call this out explicitly in
the PR body so the reviewer rules on it deliberately rather than discovering it in the diff.

### Risk 2: The literal-channel collapse touches more test surface than the deletion itself
**Impact:** ~15 test sites drive `_apply_fixes_to_file` with literal fixes. Mechanically deleting
them could silently drop coverage of the existence invariant and the withheld-fix path, which are
retained behavior that #2739 depends on.
**Mitigation:** Triage each site by what it actually asserts, not by which channel it uses. Anything
asserting invariant or withholding behavior gets re-expressed against the regex channel; only
assertions about literal-fix mechanics are deleted. Verify afterward that the invariant and
`fixes_withheld` still have direct coverage.

### Risk 3: A doc surface keeps describing the channel
**Impact:** Docs that still document rename auto-fixing would misrepresent the auditor and could
lead a future contributor to rebuild it.
**Mitigation:** A Verification row greps `docs/features/` and `docs/sdlc/` for the deleted symbol
names and detector vocabulary; it must return zero matches.

## Race Conditions

No race conditions identified. The audit path is synchronous and single-threaded; the one piece
of shared mutable state involved (`_RENAME_QUERY_COUNT`, a module-level per-run counter) is being
deleted, which removes a global rather than adding one.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2739] The review gate in front of every docs_auditor write. This deletion is a prerequisite for it — with the permanent generator gone, a nonzero `fixes_withheld` becomes a real signal — but the gate itself is separately planned in `docs/plans/docs-auditor-review-gate.md`.

## Update System

No update system changes required — this is a deletion inside a single reflection module. No
new dependencies, config files, or migration steps, and nothing to propagate beyond the merge
itself.

## Agent Integration

No agent integration required. `reflections/docs_auditor.py` is invoked by the existing
reflection scheduler and by `/do-docs`; both call `audit()`, whose signature and result-dict
shape are unchanged. No new CLI entry point and no bridge import.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/docs-auditor.md` — remove the rename-channel description at `:164`, `:239`, and `:246` (the `new == ""` sentinel, the detector rationale, and the `GIT_LOG_FOLLOW_CAP` budget note). Describe only the remaining regex channel and the widened deleted-target reporting. No tombstones, no "removed because" markers.
- [ ] `docs/features/README.md` — verify the docs-auditor index entry still describes the feature accurately after the channel is gone; update if it mentions rename fixing.

### SDLC Stage Docs
- [ ] Update `docs/sdlc/do-docs.md` and `docs/sdlc/do-plan-critique.md` — remove rename-channel references so neither stage instructs an agent to expect rename auto-fixes.

### Inline Documentation
- [ ] Rewrite the `_make_stale_term_replacer` (`:788`) and `_apply_fixes_to_file` (`:841`) docstrings so the live-context-derivation rationale stands on what remains rather than on the deleted literal loop.

## Success Criteria

- [ ] The rename query, its per-run cap state, the three fix detectors, their registrations, and the `_detect_deleted_target_issues` suppression are gone from `reflections/`.
- [ ] `_apply_fixes_to_file` is regex-only; no always-empty literal parameter survives.
- [ ] A broken `.py` reference whose path was once a rename destination is reported by `_detect_deleted_target_issues` — demonstrated red before the change, green after.
- [ ] The retained regex channel, existence invariant, and `fixes_withheld` plumbing still have direct test coverage.
- [ ] No doc surface describes rename auto-fixing.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (deletion)**
  - Name: `rename-channel-deleter`
  - Role: Execute the deletion in `reflections/docs_auditor.py` and re-triage the affected tests
  - Agent Type: builder
  - Resume: true

- **Validator (deletion)**
  - Name: `rename-channel-validator`
  - Role: Verify the deletion is complete, retained behavior still has coverage, and the new regression genuinely goes red on pre-change code
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Demonstrate the regression red

- **Task ID**: build-regression-red
- **Depends On**: none
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: rename-channel-deleter
- **Agent Type**: builder
- **Parallel**: false
- Write the new test asserting `_detect_deleted_target_issues` reports a broken `.py` reference whose path was once a rename destination.
- Run it against unmodified code and capture the FAIL output — this is the red-state proof that the `:1076` suppression is real. Paste it into the PR body.

### 2. Delete the rename channel

- **Task ID**: build-delete-channel
- **Depends On**: build-regression-red
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: rename-channel-deleter
- **Agent Type**: builder
- **Parallel**: false
- Delete the query, the per-run cap state and its reset, the three detectors, the registration block, and the `:1076` suppression.
- Collapse `_apply_fixes_to_file` to regex-only, including its early-return guard.
- Rewrite the two docstrings whose cited rationale no longer exists.
- Paraphrase deleted symbol names in any new comment or test name (see the anti-criterion-grep trap note).

### 3. Re-triage the test surface

- **Task ID**: build-tests
- **Depends On**: build-delete-channel
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: rename-channel-deleter
- **Agent Type**: builder
- **Parallel**: false
- Apply the dispositions in Test Impact, triaging each `_apply_fixes_to_file` site by what it asserts rather than which channel it uses.
- Confirm the existence invariant and `fixes_withheld` retain direct coverage.

### 4. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: rename-channel-deleter
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/docs-auditor.md`, `docs/features/README.md`, `docs/sdlc/do-docs.md`, `docs/sdlc/do-plan-critique.md` to describe only the new status quo.

### 5. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: rename-channel-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm the new regression passes and that its red-state proof was captured.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Rename query and detectors gone from source and tests | `grep -rn "_git_log_follow_renames\|_detect_renamed_link_fixes\|_detect_renamed_symbol_fixes\|_detect_readme_broken_entries" reflections/ tests/ \| wc -l` | match count == 0 |
| Per-run cap state gone | `grep -rn "GIT_LOG_FOLLOW_CAP\|_RENAME_QUERY_COUNT" reflections/ tests/ docs/features/ docs/sdlc/ \| wc -l` | match count == 0 |
| Literal fixes loop collapsed | `grep -c "for old, new in fixes" reflections/docs_auditor.py` | match count == 0 |
| No rename query rebuilt | `grep -c "diff-filter=R" reflections/docs_auditor.py` | match count == 0 |
| No review-gate machinery added (anti-criterion for the #2739 No-Go) | `grep -c "review_gate\|create_review_pr" reflections/docs_auditor.py` | match count == 0 |
| Docs describe only the new status quo | `grep -rn "_detect_renamed\|_git_log_follow\|rename detection" docs/features/ docs/sdlc/ \| wc -l` | match count == 0 |
| Retained regex channel intact | `grep -c "_detect_stale_term_fixes" reflections/docs_auditor.py` | output > 0 |
| Withheld-fix plumbing intact | `grep -c "fixes_withheld" reflections/docs_auditor.py` | output > 0 |
| Targeted tests pass | `./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ tests/unit/test_docs_auditor_substrate.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ tests/unit/test_docs_auditor_substrate.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The issue's Decision section ("is auto-fixing renamed references worth rebuilding at
all?") is answered by the recon recommendation — leave it deleted — and the one scope
extension beyond the issue's framing (collapsing the literal fixes channel, which loses all
producers) follows mechanically from the deletion rather than requiring a judgment call. The
one consequential side effect, losing README broken-entry line deletion, is recorded as Risk 1
for the reviewer to rule on.
