---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2741
last_comment_id: 5311784123
---

# Delete the docs-auditor rename channel

## Problem

`reflections/docs_auditor.py` carries a rename-detection channel that cannot produce a
correct output for any input, in any repository. It has already contributed to one bad
commit class on `main`, and it currently degrades the one conservative, human-facing
detector the repo intends to rely on.

**Current behavior:**

`_git_log_follow_renames` runs `git log --follow --diff-filter=R --name-status --format= --
<old_path>`. `--follow` walks *backward* from HEAD, so the newest record it emits is the one
that **created** the queried path — `renames[0][1]` is always the queried path itself. All
four call sites take index `[0]`. Two reachable outcomes exist and neither is a rename fix:

| queried path was… | query returns | outcome |
|---|---|---|
| once a rename **destination** | a record whose destination is the queried path | degenerate self-substitution, permanently withheld by the #2728 existence invariant |
| only a rename **source** (the real case — a moved doc) | nothing | detector skips entirely |

Concretely, per detector:

- `_detect_renamed_symbol_fixes` — fully disabled. #2728 added `renames[0][1] != path`, which
  never holds.
- `_detect_renamed_link_fixes` / `_detect_readme_broken_entries` — propose replacing a
  doc-relative link with the repo-root-relative spelling of the *same nonexistent path*.
  Withheld on every run, permanently.
- `_detect_deleted_target_issues:1076` — **not a fix detector.** It calls the same query and
  `continue`s when it returns anything, i.e. it silently drops a broken `.py` reference from
  the human-facing report precisely when that path has a rename in its history. The
  suppression means "don't report it, the fix channel will handle it" — but the fix channel
  can never handle anything.

The net effect is a permanent `fixes_withheld > 0` generator plus a blind spot in the
conservative reporter. #2739 makes withheld fixes file a deduped GitHub issue; a permanent
generator makes that signal meaningless.

**Desired outcome:**

The rename channel is gone. `_detect_deleted_target_issues` reports every broken reference to
a human, including those whose path has a rename in its history. `fixes_withheld` becomes a
real signal. `_apply_fixes_to_file` carries exactly one fix channel — the regex channel — with
no vestigial parameter, loop, or sentinel left behind.

## Freshness Check

**Baseline commit:** `bc3051ee4`
**Issue filed at:** 2026-08-13T03:37:05Z
**Disposition:** Unchanged (with one Overlap note, below)

**File:line references re-verified against `bc3051ee4`:**

| Reference | Claim | Status |
|---|---|---|
| `reflections/docs_auditor.py:71` | `GIT_LOG_FOLLOW_CAP = 10` | holds |
| `reflections/docs_auditor.py:370` | `_RENAME_QUERY_COUNT = 0` module global | holds |
| `reflections/docs_auditor.py:373` | `_git_log_follow_renames` definition, `--follow` query unchanged | holds |
| `reflections/docs_auditor.py:441` | `_detect_renamed_link_fixes` call site, `renames[0][1]` | holds |
| `reflections/docs_auditor.py:465` | `_detect_renamed_symbol_fixes` call site with `renames[0][1] != path` guard | holds |
| `reflections/docs_auditor.py:495` | `_detect_readme_broken_entries` call site, else-branch emits `(line, "")` | holds |
| `reflections/docs_auditor.py:1076` | `_detect_deleted_target_issues` suppression `if renames: continue` | holds |
| `reflections/docs_auditor.py:1359-1360` | per-run `_RENAME_QUERY_COUNT` reset inside `audit()` | holds |
| `reflections/docs_auditor.py:1414/1416-1417` | detector registrations in the audit loop | holds (README arm at `:1414`, else-arm at `:1416-1417`) |

**Cited sibling issues/PRs re-checked:**
- **#2739** — still OPEN ("docs_auditor is its own committer: put a review gate in front of
  every write it makes"). This issue was split out of it as an explicit No-Go. Not a blocker;
  this work unblocks #2739's `fixes_withheld`-as-signal premise.
- **#2725** — CLOSED ("rename detectors pick the wrong target"). Its fix (PR #2728) added the
  `!= path` guard that fully disabled `_detect_renamed_symbol_fixes`. `docs/features/docs-auditor.md`
  still cites #2725 as the live route to the residual existence-invariant hole; deleting the
  channel removes that route and the citation must go with it.
- **#2711** — CLOSED. The bad commit `d7bf3ad99` came from the `STALE_TERMS` channel, not this
  one. The stale-term channel is out of scope here.
- **#2759** — CLOSED, merged as part of PR #2782. Widened `_PATH_REF_RE` to `*`; the detector
  patterns in `_detect_renamed_symbol_fixes` and `_detect_deleted_target_issues` were
  deliberately left narrow. The rationale comment at `_detect_deleted_target_issues:1052`
  cross-references `_detect_renamed_symbol_fixes` and will dangle once that function is gone.

**Commits on main since the issue was filed touching `reflections/docs_auditor.py`:**
- `ffbae5b1d` (2026-08-13 18:44) "fix(docs-auditor): migration-context hatch and bare-path
  existence invariant (#2782)" — **irrelevant to the premise.** It touched the stale-term hatch
  and `_PATH_REF_RE`, not the rename channel. Verified by the 2026-08-17 premise re-check
  comment on the issue, which reproduced `renames[0][1] == <queried path>` after this landed.
- No commits since. `reflections/docs_auditor.py` has not moved in four days.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/docs-auditor-review-gate.md` (`status: Planning`, tracking #2739) — **Overlap,
  deliberate.** #2741 was split out of #2739 precisely so the review-gate work stays scoped.
  The two touch the same file but disjoint regions: this plan deletes the rename channel,
  that plan wraps `audit()`'s commit path in a review gate. **Ordering matters:** this plan
  should land first, because #2739's design treats a nonzero `fixes_withheld` as a real
  signal, which is only true once the permanent generator is gone. Surfaced for coordination,
  not a blocker.

**Notes:** The issue body's original line numbers are stale by ~30 (it was written pre-#2728).
The table above supersedes them. The issue's own premise re-check comments already corrected
this; no plan-level premise changed.

## Prior Art

- **#2711** (closed) — "docs_auditor auto-fix invented a module rename and committed it."
  Produced `d7bf3ad99` on `main`. Root cause was the `STALE_TERMS` channel, not the rename
  channel, but it is the reason this repo now treats every auditor auto-write as guilty until
  proven safe. It is the standing argument against *rebuilding* rename auto-fix.
- **#2725 / PR #2728** (closed/merged) — "rename detectors pick the wrong target." Word-anchored
  stale terms and added the path-existence invariant, plus the `renames[0][1] != path` guard on
  `_detect_renamed_symbol_fixes`. **Outcome: partial.** It neutered the symbol detector without
  removing it, and left the other two permanently withholding. It did not touch the `:1076`
  reporter suppression at all.
- **#2759 / PR #2782** (closed/merged) — widened the existence invariant to bare filenames and
  fixed the migration-context hatch. Explicitly ruled the rename detector patterns unchanged.
- **#1247 / PR #1716** (closed/merged) — consolidated five disjointed docs-hygiene pieces into
  the unified auditor substrate. This is where the rename channel entered the codebase.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| PR #2728 (#2725) | Added `renames[0][1] != path` to `_detect_renamed_symbol_fixes`; added the existence invariant in `_apply_fixes_to_file` | Treated a **query defect** as a **target-selection defect**. Guarding index `[0]` cannot help when the query direction itself is wrong: `--follow` answers "what was this called before", the detectors need "what is this called now". The guard silently converted a broken detector into a dead one instead of removing it. |
| PR #2782 (#2759) | Widened `_PATH_REF_RE`, ruled the detector patterns unchanged | Correct scoping call, but it left the dead channel in place and added another doc surface citing `GIT_LOG_FOLLOW_CAP` as a live budget constraint. |

**Root cause pattern:** both fixes hardened the *write path* (`_apply_fixes_to_file`) around a
detector channel whose *read path* (`git log --follow`) is unsound. Hardening downstream of a
defective producer converts wrong output into withheld output, which reads as safety but is
actually an accumulating maintenance and signal-noise cost. The correct move is to delete the
producer, which is what this plan does.

## Data Flow

The channel being deleted, end to end:

1. **Entry point** — `audit(scope_mode=...)` resets `_RENAME_QUERY_COUNT = 0` at `:1359-1360`.
2. **Per-file detector fan-out** (`:1410-1417`) — for each file, either
   `_detect_readme_broken_entries` (README arm) or `_detect_renamed_link_fixes` +
   `_detect_renamed_symbol_fixes` (else arm) append literal `(old, new)` pairs to `fixes`.
   `_detect_stale_term_fixes` separately builds `regex_fixes` as `(re.Pattern, str)` pairs.
3. **Each rename detector** calls `_git_log_follow_renames`, which spends one unit of the
   `GIT_LOG_FOLLOW_CAP` budget and shells out to `git log --follow`.
4. **Apply** (`:1429`) — `_apply_fixes_to_file(path, root, fixes, regex_fixes=regex_fixes)`
   runs the literal loop first (including the `new == ""` whole-line-delete sentinel), then
   the regex loop over the already-mutated text. Both loops pass through
   `_absent_new_path_refs`; rejections land in `withheld`.
5. **Report** (`:1444`, rotation only) — `_detect_deleted_target_issues` calls
   `_git_log_follow_renames` a fourth time and drops a finding whenever it returns anything.
6. **Output** — `withheld` → `fixes_withheld` count → PR body marker, Telegram notification,
   liveness record.

After the deletion, step 2's `fixes` list has **no producer**, step 3 does not exist, step 4's
literal loop is unreachable, and step 5 reports unconditionally. That is the whole reason
`_apply_fixes_to_file` must collapse to regex-only rather than merely being passed an empty
list — see Technical Approach.

## Architectural Impact

- **New dependencies:** none. This is a net deletion.
- **Interface changes:** `_apply_fixes_to_file`'s signature loses its positional `fixes`
  parameter; `regex_fixes` becomes the single required fix argument. All callers are in-module
  or in-test. No public/CLI/MCP surface changes.
- **Coupling:** decreases. Removes the auditor's only dependency on `git log` rename history,
  and removes the ordering coupling between the literal and regex loops (the "literal runs
  first, so regex context must be derived at match time" constraint documented at
  `_make_stale_term_replacer` and in `docs/features/docs-auditor.md`).
  **The match-time context derivation stays** — it is correct on its own merits and is the
  cheaper invariant; only its *stated rationale* changes.
- **Data ownership:** unchanged.
- **Reversibility:** trivial — `git revert`. The deleted code is recoverable from history and
  the issue records exactly what a correct rebuild would require.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is pre-approved by the human, 2026-08-17)
- Review rounds: 1

The code deletion is an afternoon. The cost is concentrated in the test migration: eleven
`TestExistenceInvariant` cases drive `_apply_fixes_to_file` through the literal channel and
must be re-expressed on the regex channel, which has different suppression semantics (see
Rabbit Holes).

## Prerequisites

No prerequisites — this work has no external dependencies. It touches one module, one test
file, and four documentation surfaces, all in this repo.

## Solution

### Key Elements

- **Delete the rename query.** `_git_log_follow_renames`, `GIT_LOG_FOLLOW_CAP`,
  `_RENAME_QUERY_COUNT`, and the per-run reset in `audit()` all go.
- **Delete the three literal-fix detectors.** `_detect_renamed_link_fixes`,
  `_detect_renamed_symbol_fixes`, `_detect_readme_broken_entries`, and their registrations in
  the audit loop.
- **Un-blind the reporter.** Remove the `:1076` suppression so `_detect_deleted_target_issues`
  reports a broken `.py` reference regardless of whether its path has a rename in git history.
- **Collapse `_apply_fixes_to_file` to one channel.** With no literal producer left, the
  literal `fixes` parameter, its loop, and the `new == ""` whole-line-delete sentinel are dead
  code. NO LEGACY CODE TOLERANCE requires removing them, not passing `[]`.
- **Update every doc surface** so nothing describes a channel that no longer exists.

### Flow

`audit()` → per-file: **regex (stale-term) fixes only** → `_apply_fixes_to_file(path, root,
regex_fixes)` → existence invariant → write → (rotation only) `_detect_deleted_target_issues`
reports **every** broken reference to a human via a deduped GitHub issue.

### Technical Approach

**1. Module deletions (`reflections/docs_auditor.py`).**

| Target | Location | Action |
|---|---|---|
| `GIT_LOG_FOLLOW_CAP = 10` | `:71` | delete the constant |
| `_RENAME_QUERY_COUNT` global | `:370` | delete |
| `_git_log_follow_renames` | `:373-416` | delete the function |
| `_detect_renamed_link_fixes` | `:418-446` | delete |
| `_detect_renamed_symbol_fixes` | `:448-469` | delete |
| `_detect_readme_broken_entries` | `:471-503` | delete |
| `renames = ...; if renames: continue` | `:1076-1078` | delete the three lines only; the rest of `_detect_deleted_target_issues` is unchanged |
| `global _RENAME_QUERY_COUNT` + reset | `:1359-1360` | delete both lines; the adjacent `_BASENAME_INDEX_CACHE.clear()` **stays** (#2759) |
| README/else detector branches | `:1412-1417` | delete the `if path.name == "README.md"` branch entirely; `fixes` no longer exists |
| comment at `:1052` | `_detect_deleted_target_issues` | rewrite — it cross-references `_detect_renamed_symbol_fixes` for its narrow-pattern rationale. The rationale (narrow detector pattern, deliberate asymmetry with `_PATH_REF_RE`) is still correct and must be **preserved**, restated standalone. Do not delete the reasoning along with the reference. |

**2. Collapse the literal channel in `_apply_fixes_to_file` (`:825-...`).**

- Signature becomes `_apply_fixes_to_file(path, repo_root, regex_fixes)` — `regex_fixes` is now
  required and positional. Drop the `regex_fixes: ... | None = None` default and the
  `regex_fixes = regex_fixes or []` normalization; there is no longer a caller that omits it.
- Delete the `for old, new in fixes:` loop in its entirety, including the `new == ""`
  whole-line-delete branch.
- The early-out `if not full.exists() or (not fixes and not regex_fixes)` becomes
  `if not full.exists() or not regex_fixes`.
- Update the call site at `:1429` and the guard at `:1425` (`if (fixes or regex_fixes) and ...`
  → `if regex_fixes and ...`).
- `_reject`, `_absent_new_path_refs`, `original_refs`, and the regex loop are **unchanged**.
- Rewrite the docstring: the "literal `fixes` run first" framing is the current justification
  for match-time context derivation and is now false. Replace it with the standalone reason —
  the regex loop mutates `new_text` across iterations, so an index computed against the
  pre-loop `content` is stale for any fix after the first. **The behavior does not change;
  only the stated reason does.** Same for `_make_stale_term_replacer`'s docstring and
  `_detect_stale_term_fixes`'s.

**3. New regression coverage.**

The behavior change users can observe is exactly one: `_detect_deleted_target_issues` now
reports a broken reference whose path was once a rename destination. That needs a test built
on a **real git checkout** (the existing `git_repo` fixture in
`tests/unit/test_docs_auditor_substrate.py`), because the suppression it removes was driven by
real `git log` output. Shape: commit `a/old.py`, `git mv` it to `a/new.py`, commit, then delete
`a/new.py` and commit; a doc referencing `` `a/new.py` `` must now yield a finding. Assert
against the pre-change behavior explicitly so the test is a demonstrated-red guard, not a
tautology.

**4. Documentation surfaces.** See the `## Documentation` section.

## Failure Path Test Strategy

### Exception Handling Coverage
- The only `except Exception` in the deleted code is `_git_log_follow_renames`'s
  `git log` failure handler (`:413-415`), covered today by
  `TestGitLogFollowCap::test_subprocess_failure_returns_empty`. Both the handler and its test
  are deleted together — nothing is left uncovered.
- No exception handlers are added by this work. The surviving handlers in
  `_apply_fixes_to_file` (read failure `:833`, write failure `:920`) are untouched and keep
  their existing coverage.

### Empty/Invalid Input Handling
- [ ] `_apply_fixes_to_file` with an empty `regex_fixes` list must return `(0, [])` and write
      nothing — this is the new early-out condition and needs a direct test. The existing
      `test_empty_or_whitespace_doc_with_no_fixes_writes_nothing` covers the empty-document
      side; extend or mirror it for the empty-fixes side under the new signature.
- [ ] `_detect_deleted_target_issues` on empty content still returns `[]` — covered by the
      existing `test_empty_content_returns_empty`, which must keep passing unmodified.
- [ ] With the `:1076` suppression gone there is no `None`/empty branch left in that function
      to guard.

### Error State Rendering
- The user-visible output is the `withheld` list surfacing as `fixes_withheld` in the PR body,
  the Telegram notification, and the liveness record. `TestWithheldBlocksAutoMerge` and
  `TestWriteLivenessVaultParam` cover that path today and must keep passing — they are driven
  by the regex channel and the `audit()` result contract, neither of which changes shape.
- [ ] Verify the withheld-record `old` field still renders readably. `docs/features/docs-auditor.md`
      currently documents `old` as "a literal string for the three rename detectors, but the
      regex source for a stale-term rejection". Post-change it is *always* the regex source;
      the doc and any caller that echoes it (`.claude/skill-context/do-docs.md`) must say so.

## Test Impact

All in `tests/unit/test_docs_auditor_substrate.py` unless noted.

- [ ] `:55,57` — fixture resetting `docs_auditor._RENAME_QUERY_COUNT` — UPDATE: remove both
      lines (the symbol no longer exists; leaving them raises `AttributeError`).
- [ ] `TestGitLogFollowCap` (`:360-376`) — DELETE: both cases test the deleted function.
- [ ] `TestRenamedSymbolFixesDegenerate` (`:378-410`) — DELETE: both cases test the deleted
      detector.
- [ ] `TestStaleTermWordBoundary::test_suppression_survives_an_earlier_line_deletion`
      (`:778-828`) — REPLACE: it constructs a README fixture, calls
      `_detect_readme_broken_entries` to get a `new == ""` literal fix, and asserts the regex
      suppression survives that line deletion. Both the producer and the sentinel are gone.
      **Do not simply delete it** — the property it guards (regex context is derived at match
      time, not from a stale detection-time index) is still real: the regex loop mutates
      `new_text` across iterations. Rewrite it with two regex fixes where the first shortens
      the text ahead of the second's match.
- [ ] `TestExistenceInvariant` (`:830-1030`), eleven cases at `:841,856,861,876,892,917,938,961,985`
      — REPLACE: each drives `_apply_fixes_to_file` with a literal `fixes` list. Re-express on
      the regex channel. **This is not a mechanical `re.compile(re.escape(old))` swap** — see
      Rabbit Holes. `test_regex_channel_is_also_guarded` (`:994`) is already the correct shape
      and is the model to follow; it may become the base for the rewritten cases.
- [ ] `TestExistenceInvariant::test_empty_or_whitespace_doc_with_no_fixes_writes_nothing`
      (`:1013`) — UPDATE: passes `[]` as the literal arg; re-point at `regex_fixes=[]`.
- [ ] `TestDegradedBasenameIndex` case at `:1087` — UPDATE: same signature change.
- [ ] `TestWithheldRateNonRegression` (`:1419-1567`, call at `:1502`) — UPDATE: it already runs
      "under one regex arm" per its own docstring, but passes the literal arg positionally.
      Re-point at the new signature. Its corpus baseline must not change.
- [ ] `TestLineDeleteSentinel` (`:1152-1185`) — DELETE: the entire class tests the `new == ""`
      sentinel, which is being removed.
- [ ] `TestAuditSubstrate` patches at `:1120,1123` and `:1260,1263`
      (`patch.object(docs_auditor, "_detect_renamed_symbol_fixes"/"_detect_renamed_link_fixes")`)
      — UPDATE: remove the patches; the symbols no longer exist and `patch.object` will raise.
- [ ] `TestDeletedTargetFiltering` patches at `:1683,1703`
      (`patch.object(docs_auditor, "_git_log_follow_renames", return_value=[])`) — UPDATE:
      remove. These patches exist *because* of the suppression being deleted; removing them
      makes the surrounding assertions stronger, not weaker.
- [ ] `TestDeletedTargetFiltering` — ADD: the new rename-destination regression (Technical
      Approach step 3).
- [ ] `tests/README.md:272` — UPDATE: the row for `test_docs_auditor_substrate.py` records a
      case count of 62; recount after the deletions.
- [ ] `tests/unit/test_public_api_contract.py` — VERIFY: it asserts on renamed/reshaped symbols.
      Confirm none of the deleted `docs_auditor` names appear in its contract set before
      assuming it is unaffected.

## Rabbit Holes

- **Rebuilding the rename channel.** Explicitly ruled out by the human on 2026-08-17. A correct
  rebuild needs all three of a forward-resolving pathspec-free query, chain walking with cycle
  protection and a hop cap, and frame-correct re-relativization of doc-relative links. Do not
  build any one of them "while we're in here."
- **Assuming the `TestExistenceInvariant` migration is a mechanical swap.** It is not, and this
  is the single most likely place to burn a day. The regex channel applies path-token
  suppression (#2744, `_match_inside_path_token`) *before* the existence invariant. A regex fix
  matching `agent/real.py` — a path token — is suppressed and never reaches the invariant, so a
  naive `re.compile(re.escape("agent/real.py"))` rewrite of those tests produces green tests
  that assert nothing. The correct shape is `test_regex_channel_is_also_guarded`'s: match a
  **prose word** and let the *replacement* be the path-shaped string the invariant must reject.
  Its existing comment already warns against "simplifying" it back — heed it. Budget real time
  here and treat every rewritten case as demonstrated-red before green.
- **Re-litigating the narrow detector pattern in `_detect_deleted_target_issues`.** Ruled
  unchanged in #2759. The comment's cross-reference to `_detect_renamed_symbol_fixes` must be
  restated standalone, not used as an opening to widen the pattern.
- **Touching the `STALE_TERMS` channel.** It produced `d7bf3ad99` and is emotionally adjacent,
  but it is a different channel with its own live issues. Out of scope.
- **Pre-empting #2739's review gate.** Do not add a review gate, a dry-run default, or a
  human-approval step to `audit()`. That is #2739's plan.

## Risks

### Risk 1: The `TestExistenceInvariant` rewrite produces vacuous tests
**Impact:** The existence invariant — the guard standing between the auditor and a repeat of
`d7bf3ad99` — silently loses its coverage while the suite stays green. This is the worst
outcome available from this change.
**Mitigation:** Every rewritten case must be demonstrated-red before it is accepted: mutate
`_absent_new_path_refs` to return `[]`, confirm the case fails, revert. Path-token suppression
makes a vacuous pass the *default* failure mode here, so a green-only proof is not acceptable.
Record the red-state output in the PR description.

### Risk 2: Removing the `:1076` suppression floods the issue tracker
**Impact:** `_detect_deleted_target_issues` files deduped GitHub issues. Un-blinding it could
surface a backlog of genuinely-broken references all at once on the first rotation run.
**Mitigation:** Three existing controls already bound this and none is being touched: filing is
rotation-only (not per-PR), the neighborhood is capped at `NEIGHBORHOOD_CAP = 20` files per
run, and `_open_issue_exists` dedupes against open issues cross-machine. Measure before
shipping: run `audit(scope_mode="rotation", apply_mode="dry-run")` against a representative
primary path pre- and post-change and record the finding-count delta in the PR. A large delta
is a *correct* result (these are real broken references that were being hidden), but it should
be a known number, not a surprise.

### Risk 3: A doc surface is missed and describes a deleted function
**Impact:** `docs/features/docs-auditor.md` is 569 lines and references the rename channel in
at least eight places, several of them load-bearing rationale rather than passing mentions.
The two `/do-docs` surfaces are user-facing skill text. A miss leaves the repo documenting
functions that do not exist — the exact failure this auditor exists to catch.
**Mitigation:** A `## Verification` anti-criterion greps the whole repo (excluding
`docs/plans/`) for each deleted symbol name and requires zero matches. Note the
`feedback_grep_anticriterion_counts_comments` lesson: the plan's own text quotes these symbol
names, so the grep must exclude `docs/plans/` or it fails on this document.

### Risk 4: Coordination collision with `docs/plans/docs-auditor-review-gate.md` (#2739)
**Impact:** Both plans edit `reflections/docs_auditor.py`. If #2739 builds first or
concurrently, the merge conflicts in `audit()` and `_apply_fixes_to_file`.
**Mitigation:** #2739 is still in `status: Planning` — it has not built. Land this first; its
diff is a pure deletion and rebasing #2739 onto it is strictly easier than the reverse. Flag
in the PR body that #2739's premise (nonzero `fixes_withheld` is a real signal) becomes true
only after this merges.

## Race Conditions

No race conditions identified. Every touched code path is synchronous and single-threaded:
`_apply_fixes_to_file` and the detectors run inline within `audit()`'s per-file loop, and the
only concurrency control in the module — the Redis SETNX rotation lock (`LOCK_TTL_SECONDS`) —
is untouched.

One adjacent hazard is worth stating so it is not mistaken for one: the deletion removes the
`global _RENAME_QUERY_COUNT` reset from `audit()`, which sits directly above the
`_BASENAME_INDEX_CACHE.clear()` added by #2759. That `clear()` is a genuine per-run staleness
guard for long-lived processes and **must survive the deletion**. Removing the two lines above
it while leaving it intact is the required outcome.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2739] The review gate in front of the auditor's writes. This issue was split
  out of #2739 specifically to keep that work scoped. No review gate, dry-run default, or
  human-approval step is added to `audit()` here.
- Rebuilding rename detection correctly. Not deferred — **decided against**, by the human on
  2026-08-17 and on the evidence in the issue: the channel has no correct-output path for any
  input, and a rebuild reintroduces an automated-write class whose sibling already produced a
  bad commit on `main`. Nothing else is deferred; every remaining item is in scope.

## Update System

No update system changes required. This is a net deletion inside `reflections/`, with no new
dependency, config file, entry point, or migration. `/update` propagates it as an ordinary code
change on the next pull. No Popoto model is touched, so no `scripts/update/migrations.py` entry
is needed.

## Agent Integration

No new agent integration required — this is a deletion within an existing substrate.

The auditor is already reachable by the agent through two existing surfaces, both of which stay
wired and neither of which changes shape:

- `/do-docs` Step 2d calls `reflections.docs_auditor.audit(scope_mode="pr-changed-files")` via
  the inline `python -c` block in `.claude/skill-context/do-docs.md`. That block's **text**
  changes (it enumerates the four fix classes, one of which is being deleted), but its
  invocation and its result-key handling do not.
- The rotation caller `run_docs_auditor` is driven by the reflections scheduler.

The `audit()` return contract (`status`, `files_touched`, `fixes_applied`, `issues_filed`,
`fixes_withheld`, `withheld`, `pr_url`) is **unchanged** — no key is added or removed. Callers
that read `fixes_withheld` need no code change.

## Documentation

### Feature Documentation
- [ ] `docs/features/docs-auditor.md:54-56` — remove the three rename rows from the auto-fix
      detector table, leaving the stale-term row.
- [ ] `docs/features/docs-auditor.md:106-110` — the "literal `fixes` channel used by the three
      rename detectors is untouched, so the `new == ""` sentinel keeps its exact-line-equality
      semantics" passage. Rewrite: there is one channel now.
- [ ] `docs/features/docs-auditor.md:158-175` — "Gates 2 and 3 are computed at apply time".
      The load-bearing rationale ("`_apply_fixes_to_file` applies the literal `fixes` list
      first … any line index computed at detection time is stale") must be **restated, not
      deleted**: the conclusion still holds because the regex loop mutates `new_text` across
      iterations. Only the cause changes.
- [ ] `docs/features/docs-auditor.md:239-249` — "Why the sibling patterns stay narrower".
      `_detect_renamed_symbol_fixes` is gone and `GIT_LOG_FOLLOW_CAP` no longer exists. Rewrite
      for the one surviving detector (`_detect_deleted_target_issues`) and drop the
      `git log --follow` budget argument, keeping the per-run issue-cap argument.
- [ ] `docs/features/docs-auditor.md:262-266` — the residual-hole paragraph cites "#2725, and
      closing it requires span-level attribution" as the reachable route. That route is deleted
      with the channel; state that the residual hole now has no known reachable route.
- [ ] `docs/features/docs-auditor.md:278-282` — `old` is now *always* the regex source, never a
      literal string.
- [ ] `docs/features/docs-auditor.md:313` — the Deleted-target row says "references with no
      rename in history". Drop the rename qualifier; that is precisely the suppression removed.
- [ ] `docs/features/docs-auditor.md:543-548` — the test-coverage paragraph describes the
      `README.md` / `new == ""` sentinel fixture. Update to the rewritten test.
- [ ] `docs/features/README.md` — verify the docs-auditor index entry's one-line summary does
      not mention rename detection.

### Skill Surfaces
- [ ] `.claude/skills-global/do-docs/SKILL.md:216-219` — "renamed markdown links, renamed
      paths/symbols, index entries pointing at deleted files, and stale-term renames". This is
      a **global** skill body: keep it generic (it describes what such a substrate typically
      does, in any repo), but drop the specifics that are now false for the repo that actually
      declares one.
- [ ] `.claude/skill-context/do-docs.md:122-126` — "auto-handles four classes of mechanical
      fix" with the same enumeration. This is the repo-specific surface and must become one
      class: stale-term renames.

### Inline Documentation
- [ ] `_apply_fixes_to_file` docstring — rewrite the channel description and the
      literal-runs-first rationale.
- [ ] `_make_stale_term_replacer` docstring — same; it cites
      `_detect_readme_broken_entries`' sentinel by name.
- [ ] `_detect_stale_term_fixes` docstring — it says fixes are "never mixed into the literal
      `fixes` list (which carries the `new == ""` line-delete sentinel)". No such list exists.
- [ ] `_detect_deleted_target_issues` comment at `:1052` — restate the narrow-pattern rationale
      standalone, without the `_detect_renamed_symbol_fixes` cross-reference.

### Test Index
- [ ] `tests/README.md:272` — recount `test_docs_auditor_substrate.py` cases.

## Success Criteria

- [ ] `_git_log_follow_renames`, `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`,
      `_detect_readme_broken_entries`, `GIT_LOG_FOLLOW_CAP`, and `_RENAME_QUERY_COUNT` do not
      appear anywhere in the repo outside `docs/plans/`.
- [ ] `_apply_fixes_to_file` takes no literal `fixes` parameter and contains no `new == ""`
      branch.
- [ ] `_detect_deleted_target_issues` makes no subprocess call and reports a broken reference
      whose path was once a rename destination — proven by a new test on a real git checkout.
- [ ] Every rewritten `TestExistenceInvariant` case was demonstrated red (with
      `_absent_new_path_refs` neutered) before being accepted green, with the red output pasted
      into the PR description.
- [ ] The dry-run finding-count delta from removing the `:1076` suppression is measured and
      recorded in the PR description.
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Lint and format clean

## Team Orchestration

### Team Members

- **Builder (auditor-deletion)**
  - Name: `auditor-deletion-builder`
  - Role: Delete the rename channel from `reflections/docs_auditor.py` and collapse
    `_apply_fixes_to_file` to the regex channel
  - Agent Type: builder
  - Resume: true

- **Test engineer (test-migration)**
  - Name: `auditor-test-migrator`
  - Role: Migrate `TestExistenceInvariant` and the sentinel-dependent cases to the regex
    channel with demonstrated-red proof; add the rename-destination regression
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (doc-sweep)**
  - Name: `auditor-documentarian`
  - Role: Update all four documentation surfaces
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `auditor-validator`
  - Role: Verify no deleted symbol survives, red-state proofs exist, and the dry-run delta is
    recorded
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete the rename channel and collapse the apply path
- **Task ID**: build-delete-channel
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Assigned To**: `auditor-deletion-builder`
- **Agent Type**: builder
- **Parallel**: false
- Delete `GIT_LOG_FOLLOW_CAP`, `_RENAME_QUERY_COUNT`, `_git_log_follow_renames`,
  `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`, `_detect_readme_broken_entries`.
- Delete the `:1076` `renames`/`continue` suppression in `_detect_deleted_target_issues`;
  restate the `:1052` narrow-pattern comment standalone.
- Delete the `global _RENAME_QUERY_COUNT` reset in `audit()`. **Leave
  `_BASENAME_INDEX_CACHE.clear()` intact.**
- Delete the README/else detector branches in the audit loop; remove the `fixes` local.
- Collapse `_apply_fixes_to_file` to `(path, repo_root, regex_fixes)`: drop the literal loop,
  the `new == ""` branch, the `regex_fixes=None` default, and the `or []` normalization.
  Update the early-out and the `:1425` guard.
- Rewrite the three affected docstrings so the match-time-context rationale rests on the regex
  loop's own cross-iteration mutation, not on a literal loop that no longer runs.

### 2. Measure the un-blinding delta
- **Task ID**: measure-delta
- **Depends On**: build-delete-channel
- **Assigned To**: `auditor-deletion-builder`
- **Agent Type**: builder
- **Parallel**: false
- Run `audit(scope_mode="rotation", apply_mode="dry-run")` against a representative primary
  path on both `main` and the branch; record the `issues_filed` / finding-count delta.
- Write the numbers into the PR description. A large delta is expected and correct.

### 3. Migrate the tests
- **Task ID**: build-test-migration
- **Depends On**: build-delete-channel
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Informed By**: the Rabbit Holes entry on path-token suppression — a naive
  `re.compile(re.escape(path))` rewrite produces vacuous green tests
- **Assigned To**: `auditor-test-migrator`
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `TestGitLogFollowCap`, `TestRenamedSymbolFixesDegenerate`, `TestLineDeleteSentinel`.
- Remove the `_RENAME_QUERY_COUNT` fixture lines and every `patch.object` naming a deleted
  symbol (`:1120,1123,1260,1263,1683,1703`).
- Re-express the eleven `TestExistenceInvariant` cases on the regex channel using
  `test_regex_channel_is_also_guarded` as the model: match prose, let the **replacement** carry
  the path-shaped string. For each, neuter `_absent_new_path_refs` to prove red, then revert.
- Rewrite `test_suppression_survives_an_earlier_line_deletion` with two regex fixes where the
  first shortens text ahead of the second's match.
- Update the signature at every remaining `_apply_fixes_to_file` call site including
  `TestWithheldRateNonRegression`; confirm its corpus baseline is unchanged.
- Add the rename-destination regression to `TestDeletedTargetFiltering` on a real `git_repo`.

### 4. Validate the deletion
- **Task ID**: validate-deletion
- **Depends On**: build-test-migration, measure-delta
- **Assigned To**: `auditor-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every `## Verification` row.
- Confirm each rewritten existence-invariant case has a recorded red-state proof.
- Confirm `_BASENAME_INDEX_CACHE.clear()` survives in `audit()`.
- Confirm the dry-run delta is recorded.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-deletion
- **Assigned To**: `auditor-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Work the `## Documentation` checklist surface by surface.
- Preserve every rationale whose conclusion still holds; change only the stated cause.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `auditor-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run all `## Verification` rows including the repo-wide symbol-absence anti-criteria.
- Verify every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Rename query gone | `grep -rn "_git_log_follow_renames" --include=*.py --include=*.md . \| grep -vc "^./docs/plans/"` | match count == 0 |
| Link detector gone | `grep -rn "_detect_renamed_link_fixes" --include=*.py --include=*.md . \| grep -vc "^./docs/plans/"` | match count == 0 |
| Symbol detector gone | `grep -rn "_detect_renamed_symbol_fixes" --include=*.py --include=*.md . \| grep -vc "^./docs/plans/"` | match count == 0 |
| README detector gone | `grep -rn "_detect_readme_broken_entries" --include=*.py --include=*.md . \| grep -vc "^./docs/plans/"` | match count == 0 |
| Follow cap gone | `grep -rn "GIT_LOG_FOLLOW_CAP\|_RENAME_QUERY_COUNT" --include=*.py --include=*.md . \| grep -vc "^./docs/plans/"` | match count == 0 |
| Literal channel gone | `grep -c 'new == ""' reflections/docs_auditor.py` | match count == 0 |
| Reporter is subprocess-free | `sed -n '/def _detect_deleted_target_issues/,/^def /p' reflections/docs_auditor.py \| grep -c "subprocess\|_git_log"` | match count == 0 |
| Basename cache reset survives | `grep -c "_BASENAME_INDEX_CACHE.clear()" reflections/docs_auditor.py` | output > 0 |
| Rename regression exists | `grep -c "rename destination" tests/unit/test_docs_auditor_substrate.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness; History & Consistency | The plan's central premise — the channel "cannot produce a correct output for any input" — is false for `_detect_readme_broken_entries`. Its `else` arm (`reflections/docs_auditor.py:501-502`) never uses the rename query: when `_git_log_follow_renames` returns nothing it emits `(line, "")`, deleting a README index line whose target genuinely no longer exists. That arm is reachable, applies cleanly (`_absent_new_path_refs("")` yields no refs, so the existence invariant never rejects it), and has live coverage in `TestLineDeleteSentinel` (`tests/unit/test_docs_auditor_substrate.py:1152-1185`). The plan deletes working README self-healing with no mention in Risks or No-Gos. The Problem section, the Freshness Check row for `:495`, and the Test Impact entry for `TestLineDeleteSentinel` contradict each other on this point. | pending | Minimal preserving edit inside `_detect_readme_broken_entries`: delete `renames = _git_log_follow_renames(str(rel), repo_root)`, the `if renames:` test and its `fixes.append((target, new_target))`, then de-indent `fixes.append((line, ""))` out of the `else`. This keeps the literal `fixes` channel and the `new == ""` sentinel alive, which invalidates Technical Approach step 2 and the whole eleven-case `TestExistenceInvariant` migration — both are justified solely by "step 2's `fixes` list has no producer". |
| BLOCKER | Risk & Robustness | Task 2 ("Measure the un-blinding delta"), Risk 2's only quantitative mitigation, and Success Criterion 5 all rest on a measurement that cannot produce a number. `audit()` gates issue filing behind `if apply_mode == "apply" and scope_mode == "rotation":` (`reflections/docs_auditor.py:1456`), so under the prescribed `apply_mode="dry-run"` the returned `issues_filed` is always `0` on both sides. `_ok_result` (`:117,134`) exposes no findings list, so the `issue_findings` built at `:1444` are discarded before any caller sees them. The delta is structurally 0 vs 0 — a false green on the control meant to bound the issue-tracker flood. | pending | Respecify Task 2 to call `_detect_deleted_target_issues(doc_path, content, repo_root) -> list[dict]` directly over the same `NEIGHBORHOOD_CAP`-bounded file list `audit()` walks, summing `len(...)` per side. It is a pure function over already-read content. Do NOT switch to `apply_mode="apply"` to make `issues_filed` nonzero — that files real GitHub issues, which is exactly the flood Risk 2 exists to bound. |
| BLOCKER | History & Consistency | A second Ready plan for this same issue exists and the Freshness Check's "Active plans overlapping this area" does not mention it: `docs/plans/rename-detection-docs-auditor.md` (`appetite: Small`, same `tracking:` URL for #2741), committed as `92cd46ae9` 91 seconds before this plan's finalize commit `fe32898d2`. Two Ready plans claim one issue, and they disagree on scope — the sibling records "losing README broken-entry line deletion" as its Risk 1, the exact regression this plan omits. `find_plan_path(2741)` currently resolves to this document, but that resolution is order-dependent and `/do-build`, `/do-docs`, and the Step 5.5 findings-table write all key off it. | pending | Delete or retire one document on `main`, then re-verify with `.venv/bin/python -c "from tools.lane_identity import find_plan_path; print(find_plan_path(2741))"`. `find_plan_path` matches `tracking:` frontmatter only and does not read `status:`, so marking the loser `status: Superseded` does not remove the ambiguity — the `tracking:` line itself must go. |
| CONCERN | Risk & Robustness | Risk 3's only mitigation is the repo-wide symbol-absence anti-criteria, and none of those rows can run. Two independent defects: the shell is zsh, where the unquoted `--include=*.py` in five rows aborts with `no matches found` before grep executes; and `grep -r` against `.` emits bare paths (`docs/plans/foo.md`), so the exclusion regex `^./docs/plans/` never matches. Verified live — the "Follow cap gone" pipeline returns 25, not 0, on a tree where the deletion has not happened. The doc-sweep safety net fails permanently regardless of builder correctness. | pending | Quote the globs and anchor to grep's real output: `grep -rn "SYMBOL" --include="*.py" --include="*.md" . \| grep -vc "^docs/plans/"`. Note `grep -c` / `grep -vc` exit **1** when the count is 0, so an exit-code-driven harness reads every "expected 0" row as a failure — wrap as `[ "$(... \| grep -vc '^docs/plans/')" = 0 ]`. `docs/plans/completed/` and the sibling `docs/plans/rename-detection-docs-auditor.md` also carry these symbol names and must fall inside the exclusion. |
| CONCERN | Scope & Value | Nearly all of this plan's cost and its self-declared worst risk come from one optional scope decision resting on the falsified premise above. The issue asks for deletion of the `git log --follow` query and its rename arms — a few dozen lines. The `_apply_fixes_to_file` signature collapse is what drags in the eleven-case `TestExistenceInvariant` rewrite that Appetite calls the cost centre and Risk 1 calls "the worst outcome available from this change". | pending | Drop Technical Approach step 2 and Task 3 bullets 3-5; keep `_apply_fixes_to_file(path, repo_root, fixes, regex_fixes=None)` and `TestLineDeleteSentinel` untouched. Remaining test work becomes mechanical: delete `TestGitLogFollowCap` and `TestRenamedSymbolFixesDegenerate`, remove the `_RENAME_QUERY_COUNT` fixture lines at `tests/unit/test_docs_auditor_substrate.py:55,57`, and drop the `patch.object` calls naming deleted symbols — no vacuous-green failure mode. |
| CONCERN | History & Consistency | "Why Previous Fixes Failed" diagnoses the root cause as "hardening downstream of a defective producer" and concludes "delete the producer." The producer is not uniformly defective, so applying that lesson wholesale mirrors PR #2728's own error: #2728 generalized a query defect into a target-selection guard; this plan generalizes the same query defect into a whole-channel deletion. Both fail to localize the defect boundary. | pending | Narrow the stated root cause to the `git log --follow` query and enumerate per-arm dependence. The split is visible at the call sites: `:441` and `:465` consume `renames[0][1]` and are wholly rename-dependent; `:495` consumes `renames` only as a branch condition and its `else` arm is not; `:1076` consumes it as a suppression and is the arm the plan is already right to un-blind. |
| CONCERN | Scope & Value | The one user-facing change — `_detect_deleted_target_issues` filing issues it previously suppressed — ships with no measured bound, because the sizing measurement cannot run. None of Risk 2's three controls bounds steady-state volume: `NEIGHBORHOOD_CAP` bounds files per run, the per-run cap at `:1455` bounds filings to 5 per rotation, and `_open_issue_exists` dedupes against **open** issues only, so a finding closed without fixing the doc is re-filed later. Rotation repeats, so a backlog drains at 5 issues per run indefinitely rather than as a one-time surge. | pending | Make the corrected delta a gate, not a report line: if the counted finding delta exceeds a stated threshold, land the un-blinding behind #2739's review gate instead of ahead of it. The open-only dedup semantics are documented at `reflections/docs_auditor.py:1439-1441` and flow through `_open_issue_exists` (`:1169`) via `_file_issue_if_new` (`:1253`) on the rotation path too. |
| NIT | Scope & Value | Test Impact enumerates "eleven cases at `:841,856,861,876,892,917,938,961,985`" — nine line numbers for eleven cases. The remaining `_apply_fixes_to_file` call sites in `TestExistenceInvariant` are at `:1002` and `:1016`. | pending | n/a (NIT) |

---

## Open Questions

None. Scope was approved by the human on 2026-08-17 (delete the channel, extend to the `:1076`
suppression, collapse the literal `fixes` channel, add the rename-destination regression). The
one coordination point — that this should land before `docs/plans/docs-auditor-review-gate.md`
(#2739) — is recorded in the Freshness Check and Risk 4 rather than raised as a question,
because #2739 has not started building.
