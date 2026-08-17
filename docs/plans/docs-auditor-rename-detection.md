---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2741
last_comment_id: 5311784123
revision_applied: true
revision_applied_at: 2026-08-17T06:23:07Z
---

# Delete the docs-auditor rename channel

## Problem

`reflections/docs_auditor.py` carries a rename-detection channel whose **query is
unsound in one direction**: `git log --follow` answers "what was this path called
before", while every consumer needs "what is this path called now". No rename *fix* the
channel proposes can be correct, in any repository. It has already contributed to one bad
commit class on `main`, and it currently degrades the one conservative, human-facing
detector the repo intends to rely on.

**Precision on the defect boundary** (the thing PR #2728 got wrong, and which this plan
must not repeat in the opposite direction): the defect is the *query*, not the whole
channel uniformly. Enumerated per call site:

| Call site | How it consumes `renames` | Rename-dependent? |
|---|---|---|
| `:441` `_detect_renamed_link_fixes` | `renames[0][1]` as the replacement | **fully** — no correct output exists |
| `:465` `_detect_renamed_symbol_fixes` | `renames[0][1]` behind the `!= path` guard | **fully** — guard never holds, detector is dead |
| `:495` `_detect_readme_broken_entries` | both: `renames` is the `if`/`else` branch condition, and inside the `if` arm (`:496-498`) it consumes `renames[0][1]` as the replacement exactly as `:441` and `:465` do | **partially** — the `if` arm is dead for the same reason as the other two; the `else` arm at `:499-500` emits `(line, "")` and is **correct working code** that never reads the query result |
| `:1076` `_detect_deleted_target_issues` | `renames` as a *suppression* condition | **inverted** — the suppression defers to a fix channel that can never fix anything |

The `else` arm at `:499-500` is the one piece of this channel that works: when a README
index entry points at a genuinely-deleted `.md` file and the rename query returns nothing,
it deletes that index line. It applies cleanly (`_absent_new_path_refs("")` yields no
refs, so the existence invariant never rejects it) and has live coverage in
`TestLineDeleteSentinel`. **Deleting it is a deliberate, human-approved loss of working
behavior, not a consequence of the query defect** — see Risk 1 and the No-Gos.

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
- `_detect_renamed_link_fixes` — proposes replacing a doc-relative link with the
  repo-root-relative spelling of the *same nonexistent path*. Withheld on every run,
  permanently.
- `_detect_readme_broken_entries` — its rename arm has the same defect and is permanently
  withheld. Its `else` arm works (above).
- `_detect_deleted_target_issues:1076` — **not a fix detector.** It calls the same query and
  `continue`s when it returns anything, i.e. it silently drops a broken `.py` reference from
  the human-facing report precisely when that path has a rename in its history. The
  suppression means "don't report it, the fix channel will handle it" — but the fix channel
  can never handle anything.

The net effect is a permanent `fixes_withheld > 0` generator plus a blind spot in the
conservative reporter. #2739 makes withheld fixes file a deduped GitHub issue; a permanent
generator makes that signal meaningless.

**Desired outcome:**

The rename channel is gone, **including `_detect_readme_broken_entries`' working `else`
arm** — the human approved deleting the whole detector on 2026-08-17, accepting the loss of
README index-line self-healing as a trade (Risk 1). `_detect_deleted_target_issues` reports
every broken reference to a human, including those whose path has a rename in its history.
`fixes_withheld` becomes a real signal. `_apply_fixes_to_file` carries exactly one fix
channel — the regex channel — with no vestigial parameter, loop, or sentinel left behind.

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
| `reflections/docs_auditor.py:495` | `_detect_readme_broken_entries` rename call site. Re-verified 2026-08-17: `renames` here is a **branch condition**, not a replacement source. The `else` arm at `:499-500` emits `(line, "")` — a working README index-line delete that does **not** depend on the query. Deleting it is an approved scope decision (Risk 1), not a dead-code removal. | holds |
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
- `docs/plans/rename-detection-docs-auditor.md` — **was** a second `status: Ready` plan
  carrying this same `tracking:` URL for #2741, written by a concurrent unsupervised writer
  (commits `cc4cc1f4d`, `92cd46ae9`) roughly one minute before this plan's finalize commit.
  Two plans claimed one issue and `find_plan_path(2741)` resolution was order-dependent.
  **Resolved by deletion** on `main` (commit `1f15d3756`); its one substantive contribution —
  that deleting `_detect_readme_broken_entries` loses working README index-line deletion — is
  salvaged into this plan's Problem section, Risk 1, and No-Gos, strengthened with the
  disjoint-pattern evidence showing `_detect_deleted_target_issues` does **not** backstop it.
  Re-verified after deletion: `find_plan_path(2741)` returns exactly
  `docs/plans/docs-auditor-rename-detection.md`.

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

**Root cause, stated narrowly:** the defect is the `git log --follow` **query direction**,
not the detector channel as a whole. Both prior fixes hardened the *write path*
(`_apply_fixes_to_file`) around that unsound read, converting wrong output into withheld
output — safety-shaped, but an accumulating maintenance and signal-noise cost. The correct
move is to delete the *query* and every consumer that depends on its result value.

**Where this plan goes beyond the root cause, and why that is a scope decision rather than a
deduction.** Applying "delete the producer" wholesale would mirror PR #2728's own error in the
opposite direction: #2728 generalized a query defect into a target-selection guard; a
whole-channel deletion generalizes the same query defect into removing code that never touched
the query. The per-arm table in Problem is the honest boundary. Two arms (`:441`, `:465`) are
query-dependent and their deletion follows from the root cause. `:1076` is a suppression whose
un-blinding also follows. `_detect_readme_broken_entries`' `else` arm does **not** follow — it
is deleted because the human decided on 2026-08-17 that an auditor auto-deleting a line from a
human's index file is a write class this repo no longer wants, which is a policy call recorded
in Risk 1 and No-Gos, argued on its own merits.

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
literal loop is unreachable, and step 5 reports unconditionally.

The collapse of `_apply_fixes_to_file` rests on exactly this producer count, and on nothing
else. All three literal-fix detectors are deleted (two because the query is unsound, one by
the human's scope decision), and `:1429` is the only production call site. Once all three are
gone the literal `fixes` channel has **zero producers in the repo** — the parameter, the loop,
and the `new == ""` sentinel become code no execution path can reach. That is a plain NO
LEGACY CODE TOLERANCE removal, and it holds regardless of *why* each detector was deleted.
It would not hold if `_detect_readme_broken_entries` were retained in reduced form; the
approved scope is that it is not.

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

The code deletion is an afternoon. The cost is concentrated in the test migration: **twelve**
`TestExistenceInvariant` cases drive `_apply_fixes_to_file` through the literal channel and
must be re-expressed on the regex channel, which has different suppression semantics (see
Rabbit Holes).

## Prerequisites

No prerequisites — this work has no external dependencies. It touches one module, one test
file, and three documentation surfaces (`docs/features/docs-auditor.md`,
`docs/features/README.md`, `.claude/skill-context/do-docs.md`) plus `tests/README.md`, all in
this repo. The global skill body `.claude/skills-global/do-docs/SKILL.md` is deliberately **not**
touched — see `## Documentation`.

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
- Update the call site at `:1429` and the guard at `:1428` (`if (fixes or regex_fixes) and ...`
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
  the Telegram notification, and the liveness record. Two tests cover that end-to-end chain
  today — `TestExistenceInvariant::test_audit_surfaces_withheld_without_writing` (`:1111-1144`)
  and `TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
  (`:1243-1319`) — and **both are driven by the literal channel this plan deletes**, not by the
  regex channel. Each patches `_detect_renamed_symbol_fixes` to return a literal fix and then
  asserts `fixes_withheld == 1` out of a real `audit()` run. They must be REPLACED (re-pointed at
  the surviving regex producer), not merely stripped of their patches — see Test Impact. The
  `audit()` result contract itself does not change shape.
  **This chain — `fixes_withheld` → `WITHHELD_PR_MARKER` in the PR body →
  auto-merge-ineligibility → Telegram notification → liveness record — must NOT be deleted.**
  It is the exact signal this plan claims to make meaningful; losing its only end-to-end
  coverage while deleting its permanent noise generator would be a strictly worse outcome than
  not doing the work.
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
- [ ] `TestStaleTermWordBoundary::test_apply_leaves_session_logs_path_untouched` (`:744`) and
      `::test_stale_term_inside_fenced_block_is_not_rewritten` (`:771`) — UPDATE: both call
      `_apply_fixes_to_file(Path(...), repo, [], regex_fixes=regex_fixes)`. Under the new
      three-positional signature the `[]` binds to `regex_fixes` and the keyword collides —
      `TypeError: got multiple values for argument 'regex_fixes'`. Mechanical fix, and **not**
      subject to the Rabbit Holes prose-match treatment, because both already drive the regex
      channel exclusively: drop the `[]` and pass `regex_fixes` positionally
      (`_apply_fixes_to_file(Path("docs/features/snap.md"), repo, regex_fixes)`, and the
      identical edit for `fence.md` at `:771`). Their assertions (`applied == 0`,
      `withheld == []`, content unchanged) are unaffected. Both are #2744 path-token-suppression
      regressions — the coverage class Risk 2 exists to protect.
- [ ] `TestStaleTermWordBoundary::test_suppression_survives_an_earlier_line_deletion`
      (`:778-828`) — REPLACE: it constructs a README fixture, calls
      `_detect_readme_broken_entries` to get a `new == ""` literal fix, and asserts the regex
      suppression survives that line deletion. Both the producer and the sentinel are gone.
      **Do not simply delete it** — the property it guards (regex context is derived at match
      time, not from a stale detection-time index) is still real: the regex loop mutates
      `new_text` across iterations. Rewrite it with two regex fixes where the first shortens
      the text ahead of the second's match.
- [ ] `TestExistenceInvariant` (`:830-1151`), **twelve** `_apply_fixes_to_file` call sites at
      `:841, 856, 861, 876, 892, 917, 938, 961, 985, 1002, 1016, 1087`
      — REPLACE: each drives `_apply_fixes_to_file` with a literal `fixes` list. Re-express on
      the regex channel. **This is not a mechanical `re.compile(re.escape(old))` swap** — see
      Rabbit Holes. `test_regex_channel_is_also_guarded` (`:994`) is already the correct shape
      and is the model to follow; it may become the base for the rewritten cases. The twelfth
      site (`:1087`) gets its own entry directly below, because its rewrite has a shape the
      other eleven do not.
- [ ] `TestExistenceInvariant::test_empty_or_whitespace_doc_with_no_fixes_writes_nothing`
      (`:1013`) — UPDATE: passes `[]` as the literal arg; re-point at `regex_fixes=[]`.
- [ ] `TestExistenceInvariant::test_dir_prefixed_decisions_unaffected_by_degraded_index`
      (def `:1076`, call at `:1087`) — **REPLACE, same treatment as the other eleven.** This
      is the twelfth call site and it is *not* a signature-only edit. (Earlier drafts
      mis-attributed `:1087` to `test_ls_files_failure_warns_and_yields_empty_index`, which is
      defined at `:1044` and calls `_repo_basename_index`, never `_apply_fixes_to_file`. There
      is likewise no class named `TestDegradedBasenameIndex`; `:1087` sits inside
      `TestExistenceInvariant`.) The case drives the **literal** channel with two path-shaped
      pairs — `[("agent/real.py", "agent/ghost.py"), ("agent/other.py", "agent/renamed.py")]` —
      and asserts `applied == 1` plus one `target-absent` withheld record (`:1093-1101`).
      A mechanical `re.compile(re.escape("agent/real.py"))` swap is the exact Risk 2 failure
      mode: the pattern matches a **path token**, so `_make_stale_term_replacer`'s
      `_match_inside_path_token` suppression (#2744) refuses the rewrite *before*
      `_absent_new_path_refs` runs, yielding `applied == 0` and `withheld == []` — the case goes
      red and the cheap repair is to weaken its assertions into vacuity. Use the
      `test_regex_channel_is_also_guarded` (`:994`) shape: two **prose-anchored** patterns whose
      *replacements* carry the path-shaped strings — e.g.
      `(re.compile(r"\bghost\b"), "agent/ghost.py")` (must withhold) and
      `(re.compile(r"\brenamed\b"), "agent/renamed.py")` (must apply, with
      `(repo / "agent" / "renamed.py")` still created at `:1085`) — and a fixture doc carrying
      those two words in ordinary prose, never as path tokens. Keep the `subprocess.run` failure
      patch (`:1086`) and **both** `failure` parametrisations (`:1063-1075`): the property under
      test — dir-prefixed targets never consult the degraded basename index — is
      channel-independent and must survive the migration intact.
- [ ] `TestWithheldRateNonRegression` (`:1419-1567`, call at `:1502`) — UPDATE: it already runs
      "under one regex arm" per its own docstring, but passes the literal arg positionally.
      Re-point at the new signature. Its corpus baseline must not change.
- [ ] `TestLineDeleteSentinel` (`:1152-1185`) — DELETE: the entire class tests the `new == ""`
      sentinel. **Note what this deletion means:** these are passing tests covering working
      behavior, not tests of a defect. They go because the behavior goes, by the human's scope
      decision (Risk 1 / No-Gos), not because they were ever wrong. Deleting them is the
      correct move under NO LEGACY CODE TOLERANCE once the sentinel has no producer; do not
      preserve them in adapted form.
- [ ] `TestExistenceInvariant::test_audit_surfaces_withheld_without_writing` (`:1111-1144`,
      patches at `:1120,1123`) and
      `TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
      (`:1243-1319`, patches at `:1260,1263`) — **REPLACE, not UPDATE.** Both patch
      `_detect_renamed_symbol_fixes` to *return* a literal fix
      (`[("agent/real.py", "agent/ghost.py")]` and `[("session_runner.py", "ghost_module.py")]`
      respectively) and then assert `result["fixes_withheld"] == 1` and `withheld[0]["new"]`
      out of a **real `audit()` run**. Simply "removing the patches" leaves no producer of a
      withheld fix at all: `fixes_withheld` becomes 0 and both tests go red. **Do not delete
      them** — together they are the only end-to-end coverage of
      `fixes_withheld` → `WITHHELD_PR_MARKER` → auto-merge-ineligibility → Telegram → liveness,
      the signal chain this whole plan exists to make meaningful.
      **Mandatory rewrite, not a suggestion:** patch the *surviving* producer instead —
      `patch.object(docs_auditor, "_detect_stale_term_fixes", return_value=[(re.compile(r"\bSessionRunner\b"), "ghost_module.py")])`
      — and write the fixture doc so it contains the **prose word** `SessionRunner`, never a
      path token, so `_make_stale_term_replacer`'s `_match_inside_path_token` suppression
      (#2744) does not eat the match. This is exactly the shape
      `test_regex_channel_is_also_guarded` (`:994`) already uses. `_absent_new_path_refs` still
      rejects the bare `ghost_module.py` replacement (#2759 bare-name widening), so `audit()`
      yields `fixes_withheld == 1` and `withheld[0]["new"] == "ghost_module.py"`, and every
      downstream assertion (`WITHHELD_PR_MARKER` in the PR body, `_eligible(body) is False`,
      `"1 fix(es) withheld"` in the Telegram call,
      `liveness.call_args.kwargs["fixes_withheld"] == 1`) holds unchanged. Adjust the
      first test's `withheld[0]["new"]` / `"agent/ghost.py" not in p.read_text()` assertions to
      the new replacement string. The sibling
      `patch.object(docs_auditor, "_detect_renamed_link_fixes", return_value=[])` in each `with`
      block is a plain deletion — it only existed to silence the other detector.
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

### Risk 1: Deleting `_detect_readme_broken_entries` removes working README self-healing, and nothing else covers that path shape
**Impact:** The detector's `else` arm (`:499-500`) is not dead code. When a README index entry
links a `.md` file that is genuinely gone and the rename query returns nothing, it deletes that
index line — correctly, and cleanly past the existence invariant. After this change a broken
README index entry is **neither auto-repaired nor reported**. The two detectors match disjoint
path shapes and one does not backstop the other:

| Detector | Pattern | Matches |
|---|---|---|
| `_detect_readme_broken_entries` (`:478`) | `re.search(r"\(([^)]+\.md)\)", line)` | markdown-link `.md` targets |
| `_detect_deleted_target_issues` (`:1056`) | `` re.finditer(r"`((?:[\w.-]+/)+[\w.-]+\.py)`") `` | backticked `.py` paths |

A broken `.md` index entry matches neither the surviving reporter nor anything else in the
module. It goes fully silent. **Do not claim in the PR body that un-blinding
`_detect_deleted_target_issues` compensates for this — it does not, and asserting so would put a
false statement in front of the reviewer.**

**Mitigation:** None available within this scope, and none is sought. This is an accepted,
human-approved trade-off (2026-08-17): an auditor that deletes lines from a human's index file
on its own judgment is precisely the unreviewed-write class that produced `d7bf3ad99` and that
#2739 exists to gate. The repo prefers losing the capability to keeping an ungated one. What
this plan owes the reviewer is disclosure, not repair:

- [ ] State plainly in the PR body that broken README `.md` index entries lose **both**
      automated repair and automated reporting, with the disjoint-pattern table above as
      evidence, so the reviewer rules on it deliberately rather than discovering it in the diff.
- [x] Filed as **#2834** — records that `.md`-shaped broken references have no reporting path,
      so a future widening of `_detect_deleted_target_issues` (or #2739's gated rebuild) has
      somewhere to attach. Do **not** widen the reporter in this PR — that is ruled out in
      Rabbit Holes and No-Gos. Cite #2834 in the PR body alongside the disclosure above.

### Risk 2: The `TestExistenceInvariant` rewrite produces vacuous tests
**Impact:** The existence invariant — the guard standing between the auditor and a repeat of
`d7bf3ad99` — silently loses its coverage while the suite stays green. This is the worst
outcome available from this change.
**Mitigation:** Every rewritten case must be demonstrated-red before it is accepted: mutate
`_absent_new_path_refs` to return `[]`, confirm the case fails, revert. Path-token suppression
makes a vacuous pass the *default* failure mode here, so a green-only proof is not acceptable.
Record the red-state output in the PR description.

**The demonstrated-red proof is the only real guard here; the `target-absent > 3` Success
Criterion is not a second one.** That criterion is implemented as
`grep -c "target-absent" tests/unit/test_docs_auditor_substrate.py` → `output > 3`, which is a
**textual presence check, not a liveness check**. It counts the string, so it cannot tell a live
assertion from a vacuous one: a builder who rewrites every migrated case into a tautology still
passes the row at its pre-change count of 5. The five occurrences on `main` are `:850, 926, 1010,
1099, 1227`; `:1227` is a hand-written dict inside
`TestWithheldBlocksAutoMerge::test_pr_body_carries_marker_when_fixes_withheld` (`:1215`) that
never calls `_apply_fixes_to_file` at all. The row is kept as a **floor** — it catches wholesale
deletion of the coverage — and nothing more. It cannot be strengthened by grep, and no attempt
should be made to do so.

**Therefore the demonstrated-red mandate is scoped by assertion, not by class membership.** It
covers **every** case that both asserts `target-absent` **and** calls `_apply_fixes_to_file` —
on `main` that is `:850, 926, 1010, 1099`. Note explicitly that `:1099` belongs to
`test_dir_prefixed_decisions_unaffected_by_degraded_index` (call at `:1087`), the twelfth
`TestExistenceInvariant` site, which earlier drafts filed as a mechanical signature update and
which falls outside any "the eleven cases" phrasing. A red-state transcript for `:1099` is
mandatory on exactly the same terms as for the other three. Task 3's validator enforces this.

### Risk 3: Removing the `:1076` suppression un-blinds an unknown number of findings
**Impact:** `_detect_deleted_target_issues` currently drops any broken `.py` reference whose
path has a rename anywhere in its git history. Removing that suppression makes those references
visible for the first time. **How many there are is not known, and this plan does not attempt to
find out** — it is a property of the whole doc corpus against the whole rename history, and every
bounded proxy for it that has been proposed (a dry-run `issues_filed` delta, a rotation-window
sum) has turned out to measure either structurally zero or one arbitrary slice. Stated plainly:
the change may raise `_detect_deleted_target_issues` finding volume, by an amount discovered at
run time rather than at build time. That is the honest disclosure, and it is deliberately not
dressed up as a number.

**What the existing controls actually bound — and what they do not.** Filing is rotation-only
(not per-PR), the neighborhood is capped at `NEIGHBORHOOD_CAP = 20` files per run, and the
per-run cap at `:1455` allows at most 5 filings per rotation. Those bound the *rate*: no run can
file more than five issues, so there is no single-surge failure mode. They do **not** bound
steady-state volume — rotation repeats, so a backlog drains at 5 issues per run indefinitely.
`_open_issue_exists` (`:1169`, reached via `_file_issue_if_new` `:1253`) dedupes against **open**
issues only (documented at `:1439-1441`), so a finding closed without fixing the doc is re-filed
on a later rotation.

**Mitigation:** ordinary observation. The per-run cap of 5 means the worst case is a slow trickle,
not a flood, and open-issue dedupe means the same finding is not re-filed while it is open. Watch
the next few scheduled rotation runs after this merges; if the trickle turns out to be a standing
source rather than a draining backlog, that is a real finding about the doc corpus and belongs on
its own issue. **No gate, threshold, or pre-merge measurement is required by this plan** — two
previous attempts to specify one produced controls that could not execute, and a fail-branch that
contradicted this plan's own Success Criteria. Bounding the un-blinding quantitatively is a
follow-up if it is ever wanted, not a precondition for this deletion.

### Risk 4: A doc surface is missed and describes a deleted function
**Impact:** `docs/features/docs-auditor.md` is 569 lines and references the rename channel in
at least eight places, several of them load-bearing rationale rather than passing mentions.
The repo-specific `/do-docs` surface (`.claude/skill-context/do-docs.md`) is user-facing skill
text. A miss leaves the repo documenting
functions that do not exist — the exact failure this auditor exists to catch.
**Mitigation, and the honest limit of it:** a `## Verification` anti-criterion per deleted
symbol, each requiring zero matches. **These rows guard only the surfaces that spell a deleted
identifier, which is a minority of the doc edits this plan requires.** Measured on `main`:
`docs/features/docs-auditor.md` carries exactly three deleted-symbol mentions (`:164`, `:239`,
`:246`) against nine planned edits to it, and `.claude/skill-context/do-docs.md` carries
**zero**. So roughly two thirds of the feature-doc edits and the entire repo-specific skill
surface have **no mechanical guard at all** — they are prose describing behavior, not
identifiers. Those are covered only by the `## Documentation` checklist plus the Task 5
human/validator pass. This plan deliberately does not add prose anti-criteria: a prose row is
brittle against ordinary rewording and would trade a known gap for a false sense of coverage.
State the gap; do not paper over it.

Four drafts of these rows have now been broken by four different mechanisms, so the
requirements below are stated as rules rather than as commentary.

0. **Enumerate source roots; never grep `.`.** This is the rule that subsumes the two that used
   to sit here (an exclusion regex for `docs/plans/`, and an anchor for it). Grepping `.`
   pulls in two contaminants that no anchor can remove: this plan document, whose prose quotes
   every deleted symbol (the `feedback_grep_anticriterion_counts_comments` lesson), and the
   **29 sibling worktrees under `.worktrees/`**, each carrying its own full copy of
   `reflections/docs_auditor.py` and the test file. Both survive the deletion, so a `.`-rooted
   row can never reach zero — it is **permanently red**, which fails the build's validation just
   as uselessly as a vacuous row passes it. The rows above therefore name real source roots
   (`reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/`). Verified under the
   harness on 2026-08-17: survivor sets are exactly the two source files plus
   `docs/features/docs-auditor.md`, with empty stderr.
   **One of those roots has a worktree sibling of its own.** `.claude/worktrees/` is Claude
   Code's `EnterWorktree` root, and each agent worktree there is a *full* repo checkout carrying
   `reflections/docs_auditor.py` and the test file. It is empty today, which is why the numbers
   reproduce exactly; the moment any agent worktree exists, all six rows would go permanently
   red for precisely the reason this rule dropped `.`. Every symbol row therefore carries
   `--exclude-dir=worktrees` (portable across BSD and GNU `grep`, and it hardens the rows
   against `.worktrees/` too if a `.`-adjacent root is ever re-added). Demonstrated on
   2026-08-17 against a synthetic tree: a planted
   `.claude/worktrees/agentA/reflections/docs_auditor.py` counts **1** without the flag and
   **0** with it. Re-validated per rule 5 through `parse_verification_table()` and execution of
   the resolved `command`, never by typing the row — 16 checks, 0 malformed, and the six
   pre-change counts unchanged at 12, 4, 8, 7, 7, 9 with empty stderr.
1. **Quote the globs.** A bare `--include=*.py` is glob-expanded by the shell and can abort with
   `no matches found` before `grep` ever runs. Write `--include="*.py"`.
2. **Validate under the harness's shell, not your own — they are different programs.** This is
   the trap that produced the previous draft's confidently-wrong claim that `grep -r .` emits
   bare relative paths. An interactive agent shell here has `grep` shadowed by a **shell
   function** wrapping `ugrep` with `--ignore-files`, which honors `.gitignore` and so silently
   hides `.worktrees/`, and which emits bare paths. `run_checks` executes rows via
   `subprocess.run(..., shell=True)` → `/bin/sh` → the **system** `grep`, which honors no
   ignore file and emits `./`-prefixed paths. The same row that counts `12` when typed into the
   agent's Bash tool counts **230** under the harness. Never validate a row by typing it; drive
   it through `parse_verification_table()` + `run_checks()` (rule 5) and read *that* number.
3. **Do not read these rows by exit code alone.** `grep -c` exits **1** when the count is zero,
   so an exit-code-driven reading marks every "expected 0" row as failed. The rows above end in
   `| wc -l` and use `match count == 0`, which reads stdout and is insensitive to grep's exit
   code. If a root is ever mistyped or collapsed behind a variable, `grep` writes to stderr and
   `wc -l` still prints `0` on stdout — a **false pass**. Keep the roots as literal arguments in
   the row, and treat a previously-red row that turns `0` as a reason to inspect stderr.
4. **One pattern per row — never pattern-internal alternation.** The table is executed through
   `agent/verification_parser.py`, whose `split_row_cells` unescapes `\|` to a bare `|` after
   splitting. A single-escaped `\|` written *inside* a grep pattern therefore reaches the shell
   as a literal pipe character, and a basic-regex `grep -c` searches for a string containing
   that pipe — which matches nothing, so the row passes vacuously. Getting BRE alternation
   through the table needs a *double* escape (`\\|`), while the shell-pipe separator before
   `grep -vc` needs a *single* one; two escape levels in one cell is a trap this plan already
   fell into once. The rows above avoid it entirely: every anti-criterion carries exactly one
   pattern, so no cell needs a second escape level. Keep it that way — if a new symbol needs
   guarding, add a row, do not extend a pattern.
5. **Validate rewrites through the parser, never in a shell.** Run
   `parse_verification_table()` on this document, print each resolved `command`, and execute
   *that* string. A shell-typed approximation is what made the previous two drafts' rows inert.

### Risk 5: Coordination collision with `docs/plans/docs-auditor-review-gate.md` (#2739)
**Impact:** Both plans edit `reflections/docs_auditor.py`. If #2739 builds first or
concurrently, the merge conflicts in `audit()` and `_apply_fixes_to_file`.
**Mitigation:** #2739 is still in `status: Planning` — it has not built. Land this first; its
diff is a pure deletion and rebasing #2739 onto it is strictly easier than the reverse. Flag
in the PR body that #2739's premise (nonzero `fixes_withheld` is a real signal) becomes true
only after this merges.

**Known and expected:** `docs/plans/docs-auditor-review-gate.md` carries 24 lines referencing
the six symbols this plan deletes, and the `## Verification` anti-criteria scan enumerated source
roots that deliberately omit `docs/plans/` entirely (Risk 4, rule 0), so those references survive
the deletion without failing a check. That is the
correct outcome given the #2741-before-#2739 ordering — #2739 is still `status: Planning` and its
plan text is a draft against today's `main`, not a doc surface describing the shipped system.
Refreshing it is #2739's own work when it replans onto the post-deletion tree; nothing in this
PR should edit it.

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
  2026-08-17 and on the evidence in the issue: no rename *fix* the channel proposes can be
  correct, and a rebuild reintroduces an automated-write class whose sibling already produced a
  bad commit on `main`.
- **Preserving README index-line self-healing in any reduced form.** `_detect_readme_broken_entries`'
  `else` arm works today. Keeping it — by stripping only the rename branch and de-indenting the
  `fixes.append((line, ""))` out of the `else` — is a real, cheap option and is **ruled out** by
  the human on 2026-08-17. The auditor is not to delete lines from a human's index file
  unreviewed. Accepted consequence: broken README `.md` index entries lose both repair and
  reporting (Risk 1). Do not reintroduce it under any name.
- **Widening `_detect_deleted_target_issues` to cover `.md` link targets** so it would backstop
  the lost README arm. Tempting once Risk 1's disjoint-pattern table is read, but it is a
  detector-pattern widening ruled unchanged in #2759 and it spends the per-run issue cap.
- [SEPARATE-SLUG #2834] Giving `.md`-shaped broken references a reporting path. Filed, with the
  report-don't-repair constraint, the volume-sizing requirement, and the doc-relative
  re-relativization trap recorded on it. Out of scope here for the reason directly above.
- Nothing else is deferred; every remaining item is in scope.

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
- [ ] `.claude/skill-context/do-docs.md:122-126` — "It auto-handles four classes of mechanical
      fix — renamed markdown links, renamed paths/symbols, README index entries pointing at
      deleted files, and stale-term renames". This is the repo-specific surface and becomes
      **one class: stale-term renames**. The `python -c` invocation block below it and its
      result-key handling stay **byte-identical** (see `## Agent Integration`).
- **OUT OF SCOPE — do not edit `.claude/skills-global/do-docs/SKILL.md`.** Its `:216-219` text
  ("Such a substrate **typically** handles mechanical fixes — …") is explicitly hypothetical and
  asserts nothing about this repo's substrate, so nothing in it becomes false when these three
  detectors are deleted. CLAUDE.md requires global skill bodies to stay generic; repo-specific
  behavior layers in via `.claude/skill-context/` or `docs/sdlc/`. Narrowing the generic
  enumeration would make a body hardlinked onto every machine less correct for every other repo
  while fixing nothing. Supervisor ruling, 2026-08-17. No `## Verification` row covers it.

### Inline Documentation
- [ ] `_apply_fixes_to_file` docstring — rewrite the channel description and the
      literal-runs-first rationale.
- [ ] `_make_stale_term_replacer` docstring — same; it cites
      `_detect_readme_broken_entries`' sentinel by name.
- [ ] `_detect_stale_term_fixes` docstring — it says fixes are "never mixed into the literal
      `fixes` list (which carries the `new == ""` line-delete sentinel)". No such list exists.
- [ ] `_detect_deleted_target_issues` comment at `:1052` — restate the narrow-pattern rationale
      standalone, without the `_detect_renamed_symbol_fixes` cross-reference.
- [ ] `_detect_deleted_target_issues` docstring at `:1041` — currently reads "File issues for
      references to deleted **(non-renamed)** targets". That qualifier *is* the `:1076`
      suppression this plan removes. Drop it. No pre-existing Verification row covered in-source
      docstrings; the "Reporter docstring un-blinded" row was added for exactly this.

### Test Index
- [ ] `tests/README.md:272` — recount `test_docs_auditor_substrate.py` cases.

## Success Criteria

- [ ] `_git_log_follow_renames`, `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`,
      `_detect_readme_broken_entries`, `GIT_LOG_FOLLOW_CAP`, and `_RENAME_QUERY_COUNT` do not
      appear in any live source root — `reflections/`, `tests/`, `docs/features/`, `docs/sdlc/`,
      `.claude/`, `tools/`, `agent/`. Plan documents under `docs/plans/` are out of scope by
      design (Risk 4, rule 0; Risk 5), as are sibling checkouts under `.worktrees/`.
- [ ] `_apply_fixes_to_file` takes no literal `fixes` parameter and contains no `new == ""`
      branch.
- [ ] `_detect_deleted_target_issues` makes no subprocess call and reports a broken reference
      whose path was once a rename destination — proven by a new test on a real git checkout.
- [ ] Every rewritten `TestExistenceInvariant` case — **all twelve**, including
      `test_dir_prefixed_decisions_unaffected_by_degraded_index` (call at `:1087`) — was
      demonstrated red (with `_absent_new_path_refs` neutered) before being accepted green, with
      the red output pasted into the PR description. In particular, every case that asserts
      `target-absent` **and** calls `_apply_fixes_to_file` (`:850, 926, 1010, 1099` on `main`)
      has a transcript; `:1099` is the one that falls outside any "the eleven" phrasing and is
      explicitly in scope.
- [ ] The PR body carries Risk 3's disclosure: removing the `:1076` suppression un-blinds an
      unknown number of previously-suppressed broken references and may raise
      `_detect_deleted_target_issues` finding volume; the per-run filing cap of 5 and the
      open-issue dedupe are the only bounds, and the mitigation is observing the next scheduled
      rotation runs. No pre-merge measurement and no threshold gate.
- [ ] The PR body states plainly that broken README `.md` index entries lose both automated
      repair and automated reporting (Risk 1), and cites #2834, which records the `.md`
      reporting gap.
- [ ] `_detect_deleted_target_issues`' docstring no longer carries the `(non-renamed)`
      qualifier, which encoded the removed suppression.
- [ ] The end-to-end withheld chain still has coverage: both
      `test_audit_surfaces_withheld_without_writing` and
      `test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness` still exist, still
      drive a real `audit()`, and still assert `fixes_withheld == 1` — now produced through the
      regex channel. Neither was deleted.
- [ ] The existence invariant still has direct test coverage after the channel collapse,
      checked mechanically against the test file rather than production source — the
      `target-absent` rejection path must still be asserted more than three times. **This is a
      textual presence check and a floor only: it is blind to vacuity and cannot substitute for
      the demonstrated-red criterion above** (Risk 2). It catches wholesale deletion of the
      coverage, nothing more.
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
  - Role: Update every surface in the `## Documentation` checklist. Do **not** touch
    `.claude/skills-global/do-docs/SKILL.md` — explicitly out of scope.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `auditor-validator`
  - Role: Verify no deleted symbol survives, red-state proofs exist, and the PR body carries
    the Risk 1 and Risk 3 disclosures
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
  restate the `:1052` narrow-pattern comment standalone; drop the `(non-renamed)` qualifier
  from its `:1041` docstring.
- Deleting `_detect_readme_broken_entries` includes its working `else` arm (`:499-500`).
  **Do not "preserve" it** by de-indenting `fixes.append((line, ""))` out of the `else` — that
  is explicitly ruled out in No-Gos and would keep the literal channel alive, invalidating the
  collapse below.
- Delete the `global _RENAME_QUERY_COUNT` reset in `audit()`. **Leave
  `_BASENAME_INDEX_CACHE.clear()` intact.**
- Delete the README/else detector branches in the audit loop; remove the `fixes` local.
- Collapse `_apply_fixes_to_file` to `(path, repo_root, regex_fixes)`: drop the literal loop,
  the `new == ""` branch, the `regex_fixes=None` default, and the `or []` normalization.
  Update the early-out and the `:1428` guard.
- Rewrite the three affected docstrings so the match-time-context rationale rests on the regex
  loop's own cross-iteration mutation, not on a literal loop that no longer runs.

### 2. Migrate the tests
- **Task ID**: build-test-migration
- **Depends On**: build-delete-channel
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Informed By**: the Rabbit Holes entry on path-token suppression — a naive
  `re.compile(re.escape(path))` rewrite produces vacuous green tests. **Twelve** call sites, at
  `:841, 856, 861, 876, 892, 917, 938, 961, 985, 1002, 1016, 1087`.
- **Assigned To**: `auditor-test-migrator`
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `TestGitLogFollowCap`, `TestRenamedSymbolFixesDegenerate`, `TestLineDeleteSentinel`.
- Remove the `_RENAME_QUERY_COUNT` fixture lines, the `_git_log_follow_renames` patches
  (`:804, 1683, 1703`), and the `_detect_renamed_link_fixes` silencer patches (`:1123, 1263`).
- **Re-point, do not delete, the two `audit()`-driven withheld tests**
  (`TestExistenceInvariant::test_audit_surfaces_withheld_without_writing` at `:1111-1144` and
  `TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
  at `:1243-1319`). Replace their `_detect_renamed_symbol_fixes` patch (`:1120`, `:1260`) with a
  `_detect_stale_term_fixes` patch returning a prose-anchored regex whose replacement is a bare
  absent path — see the Test Impact entry for the exact shape. These are the only end-to-end
  coverage of `fixes_withheld` → PR marker → auto-merge-ineligibility → Telegram → liveness;
  a red suite is not licence to remove them.
- Re-express **all twelve** `TestExistenceInvariant` cases on the regex channel using
  `test_regex_channel_is_also_guarded` as the model: match prose, let the **replacement** carry
  the path-shaped string. For each, neuter `_absent_new_path_refs` to prove red, then revert.
  The twelfth is `test_dir_prefixed_decisions_unaffected_by_degraded_index` (call at `:1087`);
  it gets the same prose-anchored treatment as the other eleven, keeping its `subprocess.run`
  failure patch and both `failure` parametrisations. See its Test Impact entry for the exact
  pattern/replacement pair.
- Rewrite `test_suppression_survives_an_earlier_line_deletion` with two regex fixes where the
  first shortens text ahead of the second's match.
- Update the signature at **every** remaining `_apply_fixes_to_file` call site. Derive the list
  mechanically — `grep -n "_apply_fixes_to_file" tests/unit/test_docs_auditor_substrate.py`
  yields 744, 771, 812, 841, 856, 861, 876, 892, 917, 938, 961, 985, 1002, 1016, 1087, 1167,
  1175, 1502 — rather than from the enumerated twelve `TestExistenceInvariant` sites. `:744` and
  `:771` (`TestStaleTermWordBoundary`) pass `[]` positionally *and* `regex_fixes=` by keyword and
  will raise `TypeError` under the new signature; drop the `[]` at both. Confirm
  `TestWithheldRateNonRegression`'s corpus baseline is unchanged.
- Add the rename-destination regression to `TestDeletedTargetFiltering` on a real `git_repo`.

### 3. Validate the deletion
- **Task ID**: validate-deletion
- **Depends On**: build-test-migration
- **Assigned To**: `auditor-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every `## Verification` row **except the three symbol-absence rows that are still blocked
  by the not-yet-done documentation sweep.** Task 4 edits `docs/features/docs-auditor.md`, and at
  this point in the chain it has not run, so three symbols still have one doc-surface match each:
  `_detect_renamed_symbol_fixes` (`docs/features/docs-auditor.md:239`),
  `_detect_readme_broken_entries` (`:164`), and `GIT_LOG_FOLLOW_CAP` (`:246`). Those three rows
  are **expected red here and are not defects** — do not "fix" them by re-rooting the rows away
  from `docs/features/`, which would destroy the coverage Risk 4 depends on, and do not report
  them as a pipeline failure. Expected Task 3 counts, so an expected red is distinguishable from
  a real one:

  - `_git_log_follow_renames` → **0**, `_detect_renamed_link_fixes` → **0**, `_RENAME_QUERY_COUNT` → **0** (must be clean already)
  - `_detect_renamed_symbol_fixes` → **1**, `_detect_readme_broken_entries` → **1**, `GIT_LOG_FOLLOW_CAP` → **1** (doc-blocked until Task 4)

  All six must reach 0 at Task 5, which re-runs the full table after the documentation sweep.
  Any deviation from the counts above is a real failure.
- Confirm each rewritten existence-invariant case has a recorded red-state proof. This is
  scoped by assertion, not by class membership: **every** case that both asserts `target-absent`
  and calls `_apply_fixes_to_file` needs a transcript — on `main` those are `:850, 926, 1010,
  1099`. `:1099` belongs to `test_dir_prefixed_decisions_unaffected_by_degraded_index` (call at
  `:1087`), so a validator that checks only "the eleven" misses it. A missing transcript for any
  of the four is a hard fail: the `target-absent` count row is a presence check and cannot
  substitute for it (Risk 2).
- Confirm `_BASENAME_INDEX_CACHE.clear()` survives in `audit()`.
- Confirm the PR body carries Risk 3's plain disclosure (the `:1076` removal un-blinds an
  unknown number of previously-suppressed broken references; the per-run cap of 5 and the
  open-issue dedupe are the only bounds, and observation of the next rotation runs is the
  mitigation). No number and no gate decision are required.
- Confirm the PR body carries Risk 1's disclosure (README `.md` entries lose repair *and*
  reporting) and that it cites #2834 for the `.md` reporting gap.
- Confirm no reduced-form README line-delete survives: `_apply_fixes_to_file` must have no
  literal `fixes` parameter at all.
- Confirm both `audit()`-driven withheld tests survive and still assert `fixes_withheld == 1`
  from a real `audit()` run — `test_audit_surfaces_withheld_without_writing` and
  `test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`. If either was deleted
  rather than re-pointed at `_detect_stale_term_fixes`, that is a hard fail, not a cleanup.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-deletion
- **Assigned To**: `auditor-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Work the `## Documentation` checklist surface by surface.
- Preserve every rationale whose conclusion still holds; change only the stated cause.

### 5. Final Validation
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
| Rename query gone | `grep -rn "_git_log_follow_renames" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Link detector gone | `grep -rn "_detect_renamed_link_fixes" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Symbol detector gone | `grep -rn "_detect_renamed_symbol_fixes" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| README detector gone | `grep -rn "_detect_readme_broken_entries" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Follow cap constant gone | `grep -rn "GIT_LOG_FOLLOW_CAP" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Rename query counter gone | `grep -rn "_RENAME_QUERY_COUNT" --include="*.py" --include="*.md" --exclude-dir=worktrees reflections/ tests/ docs/features/ docs/sdlc/ .claude/ tools/ agent/ \| wc -l` | match count == 0 |
| Reporter docstring un-blinded | `grep -c "(non-renamed)" reflections/docs_auditor.py` | match count == 0 |
| Literal channel gone | `grep -c 'new == ""' reflections/docs_auditor.py` | match count == 0 |
| Reporter makes no rename query | `sed -n '/def _detect_deleted_target_issues/,/^def /p' reflections/docs_auditor.py \| grep -c "_git_log"` | match count == 0 |
| Reporter stays subprocess-free (non-regression guard — already green pre-change) | `sed -n '/def _detect_deleted_target_issues/,/^def /p' reflections/docs_auditor.py \| grep -c "subprocess"` | match count == 0 |
| Basename cache reset survives | `grep -c "_BASENAME_INDEX_CACHE.clear()" reflections/docs_auditor.py` | output > 0 |
| Rename regression exists | `grep -c "rename destination" tests/unit/test_docs_auditor_substrate.py` | output > 0 |
| Existence-invariant coverage survives the channel collapse | `grep -c "target-absent" tests/unit/test_docs_auditor_substrate.py` | output > 3 |

**Every row above was executed against unmodified `main` on 2026-08-17** — validated by running,
not by reasoning about grep's output format. Three rounds of critique broke these rows via three
different mechanisms (unquoted `--include` globs aborting under zsh; a `^./docs/plans/` anchor
that matched nothing; then a `^docs/plans/` anchor that was equally inert because `grep -r .`
emits `./`-prefixed paths, while that same `.` descended into 29 sibling worktrees). The durable
fix is to stop grepping `.` at all: the rows enumerate real source roots, which removes the
`docs/plans/` exclusion, the anchor, and the worktree contamination in one move. Confirmed by
execution — the survivor set for every symbol is exactly `reflections/docs_auditor.py` and
`tests/unit/test_docs_auditor_substrate.py`, with zero lines from this plan document or any
worktree.

Red/green state on unmodified `main`, so a reviewer can tell which rows prove the work happened.
**This list is deliberately not a markdown table.** `parse_verification_table` collects *every*
pipe-prefixed line in the `## Verification` section and treats rows past the first two as checks,
so a second table here is executed as twelve nonsense commands (`/bin/sh: 12,: command not found`)
that fail the run. Any future annotation in this section must stay in prose or bullets.

- **The six symbol-absence rows** — pre-change `12, 4, 8, 7, 7, 9` → demonstrated red, must reach 0
- **Reporter docstring un-blinded** — pre-change `1` → demonstrated red, must reach 0
- **Literal channel gone** — pre-change `5` → demonstrated red, must reach 0
- **Reporter makes no rename query** — pre-change `1` → demonstrated red, must reach 0
- **Rename regression exists** — pre-change `0` → demonstrated red, must become > 0
- **Reporter stays subprocess-free** — pre-change `0` → **already green**; a non-regression guard,
  not evidence of completion
- **Basename cache reset survives** — pre-change `1` → already green, non-regression guard
- **Existence-invariant coverage** — pre-change `5` → already green, non-regression guard, floor of 3
- **Lint / format clean** — pre-change pass → already green; `ruff` honors `.gitignore` and so does
  not descend into `.worktrees/`

**Two execution hazards found while validating these rows, both recorded so the builder does not
rediscover them.**

First, **the `grep` you type is not the `grep` that runs these rows.** An interactive agent shell
here has `grep` shadowed by a shell function wrapping **ugrep 7.5.0** with `--ignore-files`; the
harness runs each row through `subprocess.run(..., shell=True)`, i.e. `/bin/sh` and the **system**
`grep`, which honors no ignore file. The two disagree on both things these rows depend on: path
prefixes (`docs/plans/…` vs `./docs/plans/…`) and whether `.worktrees/` is descended. A row that
counts `12` when typed counts `230` under the harness. Every number in the table above is the
**harness** number. See Risk 4, rule 2.

Second, and more dangerous: if the root list is ever collapsed into a single shell word (for
example by putting it in an unquoted variable), `grep` emits `No such file or directory` on
**stderr** and `wc -l` prints `0` on **stdout**. A `match count == 0` row reads stdout, so a
completely broken command **false-passes**. Keep the roots as literal arguments in the row, never
behind a variable, and treat an unexpected `0` on a row that was previously red as a reason to
inspect stderr rather than as success.

## Critique Results

Round 9 (re-critique of the round-8 revision), FULL depth. Rounds 1-8 findings remain RESOLVED and
their remedies are embedded in the plan body above; round 8's table is recoverable at
`994c723f3:docs/plans/docs-auditor-rename-detection.md`, round 7's at `9a93d6559:`, round 6's at
`c08af526d:`, round 5's at `61717ccb2:`, rounds 1-4 at `97e1ac80c:`.

**No BLOCKERs.** All four round-8 items are closed and were re-verified by execution, not by
reading the revision note: the `:1087` site is now dispositioned as REPLACE under its real case
name, the twelve-site count is consistent across Test Impact / Appetite / Task 2, Risk 2 carries
the presence-check-not-liveness-check disclosure with the demonstrated-red mandate scoped to
`:850, 926, 1010, 1099`, Task 3 excludes the three doc-blocked symbol rows with expected counts
recorded, and the patch coordinates read `:1120` / `:1260`.

This round found two CONCERNs. Both are plan-text corrections with no code consequence, and
neither touches the deletion argument, the test-migration shape, or the `## Verification` rows.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk \& Robustness; Scope \& Value | The plan makes four PR-description artifacts load-bearing — Risk 1's README-loses-repair-and-reporting disclosure, Risk 3's un-blinding disclosure, the #2834 citation, and the demonstrated-red transcripts that Risk 2 calls "the only real guard" — and three of them are Success Criteria. **No task authors any of them, and no task can verify them where they sit.** `/do-build` opens the PR at its own step 17, *after* every task in `## Step by Step Tasks` has run, so at Task 3 (`validate-deletion`) and at Task 5 (`validate-all`) the PR body does not exist yet. Task 3's two "Confirm the PR body carries…" bullets and Task 5's "Verify every Success Criterion" therefore check an artifact that cannot be present — structurally the same premature-validation defect round 8 caught in Task 3's `## Verification` rows, one layer up. Task 2 compounds it: it says "neuter `_absent_new_path_refs` to prove red, then revert" but never says to *capture* the transcript, while Task 3 says to confirm a "recorded red-state proof" and Success Criteria say it must be "pasted into the PR description" — so the transcripts have no durable holding place between the moment they are produced (Task 2) and the moment a PR exists to paste them into. A validator that takes Task 3 literally reports a spurious failure or loops. | pending | Do **not** weaken the Success Criteria — the disclosure content is correct and fully specified in Risks 1/2/3. Three edits: (1) Add to Task 2 an explicit bullet to write each red-state transcript into a durable artifact as it is produced (e.g. append to a `docs/plans/` scratch note or the lane's task-list notes) so it survives to PR-authoring time. (2) Move Task 3's two `Confirm the PR body carries…` bullets out of Task 3 — Task 3 keeps only the checks it can actually run against the worktree (verification rows, transcript existence, `_BASENAME_INDEX_CACHE.clear()`, no literal `fixes` param, both `audit()`-driven withheld tests surviving). (3) Add a final PR-body authoring step *after* Task 5, owned by whoever runs `/do-build` step 17, enumerating the four required contents (Risk 1 disclosure + #2834 citation, Risk 3 disclosure, red-state transcripts, Risk 5's #2739-ordering note), and re-point the three PR-body Success Criteria at that step so they are verified where the artifact exists. |
| CONCERN | Risk \& Robustness | Test Impact dispositions `tests/unit/test_docs_auditor_substrate.py:55,57` as "remove both lines (the symbol no longer exists; leaving them raises `AttributeError`)". Those two lines are the *entire body* of the `reset_global_state` autouse fixture at `:52-57`; removing them leaves `def reset_global_state(): """Reset module-level counters between tests.""" yield` — an autouse fixture that resets nothing, carrying a docstring that is now false. `grep -n reset_global_state` returns exactly one hit (the definition), so no test requests it by name and nothing else depends on it. The `_RENAME_QUERY_COUNT` anti-criterion still reaches 0, so **no `## Verification` row catches this**: the plan's own guard passes over a vestigial autouse fixture with a lying docstring. That is precisely the residue class this plan refuses to tolerate three sections earlier, where it deletes the `fixes` parameter outright rather than passing `[]`, and the false docstring is the same defect the auditor exists to catch. | pending | Change the Test Impact entry from "remove both lines" to DELETE the whole `reset_global_state` fixture (`:52-57`, including the `@pytest.fixture(autouse=True)` decorator and the blank line after it). Verify nothing else references it first — `grep -n "reset_global_state" tests/unit/test_docs_auditor_substrate.py` must return only `:53`, the def, as it does at `bc3051ee4`. Do **not** confuse it with `clear_basename_cache`, a different fixture that is requested by name (`test_ls_files_failure_warns_and_yields_empty_index`, `test_dir_prefixed_decisions_unaffected_by_degraded_index`) and must survive. Add the deletion to Task 2's bullet list alongside the class deletions. |
| NIT | History \& Consistency | Task 2 says ":744 and :771 (`TestStaleTermWordBoundary`) pass `[]` positionally *and* `regex_fixes=` by keyword and will raise `TypeError` under the new signature; drop the `[]` at **both**." A third site has the identical shape: `:1502` in `TestWithheldRateNonRegression` reads `_apply_fixes_to_file(full.relative_to(worktree), worktree, [], regex_fixes=fixes)`. Its Test Impact entry says only "passes the literal arg positionally. Re-point at the new signature" and does not name the keyword collision. Low impact — `:1502` is in Task 2's mechanically-derived call-site list, and the failure mode is a loud `TypeError`, never a vacuous green — but the count is wrong. | pending | n/a (NIT) |
| NIT | History \& Consistency | Risk 4 states `docs/features/docs-auditor.md` carries "exactly three deleted-symbol mentions (`:164`, `:239`, `:246`) against **nine** planned edits to it". The `### Feature Documentation` checklist has nine bullets, but only **eight** target `docs-auditor.md`; the ninth targets `docs/features/README.md`. The round-8 verified-sound list has the same slip, calling its own enumeration of eight ranges "all nine". The three-mentions claim and the "roughly two thirds unguarded" conclusion both survive either reading (5/8 unguarded). | pending | n/a (NIT) |

**Verified this round and found sound (so round 10, if any, does not re-litigate):**

- `parse_verification_table()` yields **16 checks and 0 malformed rows**.
- All 13 executable `## Verification` rows were re-run through `subprocess.run(..., shell=True)`
  (the harness `/bin/sh` + system `grep`, not the agent's `ugrep`-shadowed one) and reproduced the
  plan's stated pre-change numbers **exactly**: `12, 4, 8, 7, 7, 9` for the six symbols and
  `1, 5, 1, 0, 1, 0, 5` for the rest, every one with empty stderr. `--exclude-dir=worktrees` is
  present on all six symbol rows.
- Task 3's expected-count table reproduces: `_git_log_follow_renames`, `_detect_renamed_link_fixes`
  and `_RENAME_QUERY_COUNT` have **zero** doc-surface survivors, while `_detect_renamed_symbol_fixes`
  (`docs-auditor.md:239`), `_detect_readme_broken_entries` (`:164`) and `GIT_LOG_FOLLOW_CAP` (`:246`)
  have exactly one each — the 0/0/0 and 1/1/1 split the task records.
- Every `reflections/docs_auditor.py` coordinate re-verified at HEAD: `:71`, `:370`, `:373`, `:418`,
  `:441`, `:448`, `:465`, `:471`, `:495-500`, `:825`, `:1040-1041`, `:1052`, `:1076-1078`,
  `:1359-1360`, `:1363`, `:1428-1429`. The `else` arm at `:499-500` is `fixes.append((line, ""))`
  exactly as Problem describes, and `_BASENAME_INDEX_CACHE.clear()` sits at `:1363`, below the two
  lines being deleted.
- `_apply_fixes_to_file` has exactly one production call site repo-wide (`:1429`); a scan of
  `reflections/ tools/ agent/ bridge/ worker/ scripts/` finds only that call, the definition
  (`:825`), and four docstring mentions. The Data Flow zero-producer collapse argument holds.
- The test file's `_apply_fixes_to_file` call sites are exactly the 18 Task 2 enumerates
  (`744, 771, 812, 841, 856, 861, 876, 892, 917, 938, 961, 985, 1002, 1016, 1087, 1167, 1175, 1502`),
  and `target-absent` occurs at exactly `:850, 926, 1010, 1099, 1227`.
- Round-8's coordinate corrections all landed: `TestExistenceInvariant` opens at `:830` and
  `TestLineDeleteSentinel` at `:1152` (so the twelve sites are correctly bounded);
  `test_dir_prefixed_decisions_unaffected_by_degraded_index` is defined at `:1076` with its call at
  `:1087`; `test_ls_files_failure_warns_and_yields_empty_index` is defined at `:1044` and never calls
  `_apply_fixes_to_file`; the `_detect_renamed_symbol_fixes` patch targets are at `:1120` / `:1260`
  and the `_detect_renamed_link_fixes` silencers at `:1123` / `:1263`.
- `_git_log_follow_renames` test-side patches are at `:804`, `:1683`, `:1703` as Task 2 states
  (`:364`, `:369`, `:390`, `:400` are inside the two classes being deleted outright).
- All nine `## Documentation` targets resolve to the text the plan describes: `docs-auditor.md:54-56`
  is the three rename rows of the detector table, `:313` is the Deleted-target row carrying the
  "with no rename in history" qualifier, `:543-548` is the `new == ""` sentinel fixture paragraph;
  `.claude/skill-context/do-docs.md:122-126` carries the "four classes of mechanical fix" sentence;
  `tests/README.md:272` records 62; `docs/features/README.md`'s summary does not mention renames.
- `find_plan_path(2741)` resolves uniquely to this document;
  `docs/plans/rename-detection-docs-auditor.md` is gone.
- Cited sibling issues re-checked: #2739 OPEN (`docs/plans/docs-auditor-review-gate.md` still
  `status: Planning`, so Risk 5's land-this-first ordering holds), #2834 OPEN, #2725/#2711/#2759
  CLOSED.
- Task graph sound: five tasks, no numbering gaps, one linear chain
  (`build-delete-channel` → `build-test-migration` → `validate-deletion` → `document-feature` →
  `validate-all`), no cycles. All four addendum-required sections (`## Documentation`,
  `## Update System`, `## Agent Integration`, `## Test Impact`) are present and substantive;
  `## Documentation` carries `docs/features/` checkbox tasks and `## Test Impact` carries explicit
  UPDATE/DELETE/REPLACE/ADD/VERIFY dispositions on every entry. No Popoto model is touched, so the
  migration check does not apply.

**Dispatch note.** The driving session had **no Agent tool available**, so the three FULL-roster
lenses (Risk & Robustness, Scope & Value, History & Consistency) were applied directly by the
critique driver against the real source files rather than by three separate subagents. The
artifact-based roster barrier was therefore not exercised — there were no subagent result files to
fence or gate, and none were fabricated to simulate one. Every citation above was produced by
execution against HEAD (`994c723f3`), which is the grounding the barrier exists to guarantee.
---

## Open Questions

None. Scope was approved by the human on 2026-08-17 (delete the channel, extend to the `:1076`
suppression, collapse the literal `fixes` channel, add the rename-destination regression). The
one coordination point — that this should land before `docs/plans/docs-auditor-review-gate.md`
(#2739) — is recorded in the Freshness Check and Risk 5 rather than raised as a question,
because #2739 has not started building.
