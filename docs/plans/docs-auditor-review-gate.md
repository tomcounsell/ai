---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2739
also_tracks: https://github.com/tomcounsell/ai/issues/2834
closes: [2739, 2834]
last_comment_id: none
also_tracks_last_comment_id: 5324492042
revision_applied: true
revision_applied_at: 2026-08-19T00:00:00Z
---

# Docs Auditor Review Gate

**Issues closed by this lane: #2739 and #2834.** The implementation PR carries
`Closes #2739` and `Closes #2834`.

## Problem

Two defects in one module, both about the docs auditor acting on its own judgment
without a human in the path.

### Defect 1 (#2739) — the auditor is its own committer

An automated writer generates documentation rewrites and commits them itself, in the
same function call, with nothing between generation and the permanent record.

In #2711 the auditor invented a module rename that never happened
(`agent/session_logs.py` → `agent/agent_sessions.py`), applied it in five places
across two docs, and committed it as `d7bf3ad99`. A human running `ls` caught it.
PR #2728 fixes that particular generator bug. It does not touch the reason a
generator bug reached `main` at all: there is no review gate in front of any write
the auditor makes, so the next generator bug takes exactly the same path.

**Current behavior:**

- **Cascade (Caller B).** `audit(scope_mode="pr-changed-files")` calls
  `_commit_current_branch`, which runs `git add <touched>` + `git commit`, both
  `check=False` with errors swallowed to a log line. The `/do-docs` skill is
  explicitly instructed not to review it: *"Do not re-commit the substrate's
  changes — it commits them itself"*
  (`.claude/skill-context/do-docs.md:166`, `.claude/skills-global/do-docs/SKILL.md:220-221`).
- **Rotation (Caller A).** `_push_branch_and_pr` runs `git add -A`, staging the
  **entire working tree** of the shared main checkout into a docs PR, which
  `run_docs_branch_sweeper` can then auto-merge with no human involvement.
- **Withheld fixes rot.** PR #2728's existence invariant stamps
  `WITHHELD_PR_MARKER` into the PR body. `_pr_is_auto_merge_eligible` (`:2005`)
  refuses that PR forever — nothing rewrites the body — so the sweeper stale-closes
  it at `STALE_PR_AGE_DAYS = 14` with `--delete-branch` (`:2263`), discarding the
  fixes that *did* pass the invariant. #2782 added a Telegram line naming the
  withheld count at creation time, so this is no longer *silent at run time* — but
  the discard at day 14 still is, and nothing durable survives the close.
- **Rotation can wedge the shared checkout and report success.** The daily-cap and
  open-PR guards in `_push_branch_and_pr` fire *after* the substrate has already
  written to disk, and rotation never commits (that path is gated to
  `pr-changed-files`). The edits sit uncommitted in `${AI_REPO_ROOT:-$HOME/src/ai}`.
  `run_docs_auditor` then stamps the rotation hash, sends a "N files, M fixes"
  Telegram, and writes liveness `status="ok"`. Every subsequent run trips the
  dirty-tree guard and skips. The auditor is permanently disabled until a human
  notices the dirt by hand.

### Defect 2 (#2834) — the auditor cannot tell a live reference from an obituary

The same module's *reporting* channel gets the reference question wrong in both
directions at once, and the two errors share one root cause: nothing in
`_detect_deleted_target_issues` (`:876`) asks whether a reference is a claim about the
present or a record of the past.

- **`.md` breaks reach nobody.** The detector matches only backticked `.py` paths
  (`` re.finditer(r"`((?:[\w.-]+/)+[\w.-]+\.py)`") ``, `:893`). Markdown-link `.md`
  targets were handled by `_detect_readme_broken_entries`, which *auto-repaired* by
  deleting the offending index line and never reported. #2741 / PR #2842 deleted that
  detector — correctly, because an auditor deleting lines from a human's index file on
  its own judgment is precisely the unreviewed-write class #2739 exists to gate. The
  consequence is a silence: a broken `.md` link is now neither repaired nor reported.
  Measured on the live corpus at plan time, **19 distinct broken `.md` link targets**
  sit in `docs/` outside the archived plan directories and reach nobody (spike-6).
- **`.py` deletion narrative files false issues.** The detector *does* carry a
  deletion-narrative hatch (`_is_documented_deletion`, `:847`, from #1555), but its cue
  lists are too literal to fire on real prose. On 2026-08-17 it filed three issues in one
  run and two were false:

  | Issue | Site | Why the hatch missed it |
  |---|---|---|
  | #2840 (closed, not-a-bug) | `docs/features/harness-abstraction.md:189` | heading is `## Hook Cleanup (Phase 5)`; `_DELETION_HEADING_KEYWORDS` has `deleted`, not `cleanup`. Line prose is "deleted (250 lines)" and "no longer needed"; `_DELETION_PROSE_CUES` wants the exact phrases "deleted module" / "no longer exists" |
  | #2841 (closed, not-a-bug) | `docs/features/harness-adapter.md:19`, `:115` | `:115`'s heading is `## Dead SDK Path Deletion` — `"deleted" in "…deletion"` is `False`, so an inflection gap defeats it. `:19`'s cue sits two lines above the match and the window is ±1 |
  | #2839 (**true positive**) | `docs/features/standardized-enums.md:19` | a live present-tense table row for `SessionType.GRANITE` citing `tools/granite_interactive_tui_poc/cli.py` |

  Two of three filings in one night cost a human a triage pass each. The detector is not
  merely noisy: it cannot distinguish a live pointer from an obituary.

- **Neither channel converges.** `_open_issue_exists` (`:1003`) queries
  `gh issue list --state open`, and `_file_issue_if_new`'s Redis dedup key expires at 30
  days (`:1091` and `:1133`, `ex=86400 * 30`). A finding a human closes *without* editing the doc is
  therefore re-filed a month later, forever. `audit()`'s own comment at `:1259-1265`
  already names this and its only mitigation is scope: filing is gated to rotation so the
  flood is metered, not stopped. Widening the detector without fixing dedup widens a
  source that meters but never converges.

**Desired outcome:**

Every write the docs auditor makes passes a named review gate before it becomes a
permanent record; the auditor never leaves the repository in a state it did not
intend; when it declines to do something, a human finds out through a channel
somebody actually reads; and every reference it reports is a claim about the present,
reported once, in the frame the document is actually read in.

## Freshness Check

**Baseline commit:** `f491306c5491c93b1481094ff602552c010521c7` (`origin/main`, 2026-08-19)
**Issues filed at:** #2739 — 2026-08-13T03:27:43Z; #2834 — 2026-08-17, scope-widening
comment `5324492042` 2026-08-18T06:31:48Z
**Re-verified at:** 2026-08-19 (the #2834 fold-in pass; earlier passes ran 2026-08-13
against `48feedf31` and 2026-08-18 against `15e66d931`)
**Disposition:** Minor drift for Q1-Q5, plus a **scope addition**. `reflections/docs_auditor.py`
is byte-identical between `15e66d931` and `f491306c5` (`git diff` over the file returns
empty), so every anchor in the tables below still holds and the Q1-Q5 work is untouched.
The addition is #2834, folded into this lane as **Q7**. Q6 remains a landed-fact record:
the rename channel was deleted upstream by #2741 / PR #2842 (`a9205b065`, 2026-08-18).

**#2834's own `file:line` citations are stale and are corrected here.** Both were written
against a pre-#2842 tree:

| #2834 says | Reality on `f491306c5` |
|---|---|
| `_detect_deleted_target_issues` at `:1056` | `:876`. The regex is at `:893` |
| `_detect_readme_broken_entries` at `:478` | **Gone.** `git grep -c '_detect_readme_broken_entries' origin/main` returns nothing; PR #2842 deleted it. `:478` on today's file is inside `_detect_stale_term_fixes` |

That second correction changes nothing about the issue's substance — the `.md`
reporting gap is *caused* by the deletion — but any plan text or build step that expects
to find and edit `_detect_readme_broken_entries` would be looking for a function that
does not exist. There is nothing to modify; there is only a branch to add.

**Anchors #2834's fold-in relies on, all read on `f491306c5`:**

| Symbol | Line | Confirmed |
|---|---|---|
| `NEIGHBORHOOD_CAP = 20` | `:69` | Holds |
| `_PLACEHOLDER_PATH_COMPONENTS` | `:773-775` | Holds; 9 stand-in names, `.py`-stem aware only |
| `_DELETION_HEADING_KEYWORDS` | `:778` | Holds; `("migration", "removed", "deleted", "deprecated")` |
| `_DELETION_PROSE_CUES` | `:781-789` | Holds; 5 exact phrases |
| `_is_placeholder_path` | `:790` | Holds; strips a `.py` suffix on the final component only |
| `_build_line_context` | `:815` | Holds; returns `(in_fence, heading_for_line)`, pure string scan |
| `_is_documented_deletion` | `:847` | Holds; fence / heading-keyword / ±1-line prose cue |
| `_detect_deleted_target_issues` | `:876`, regex `:893` | Holds |
| `_open_issue_exists` `--state open` | `:1003`, state literal `:1025-1026` | Holds |
| `_file_issue_if_new` 30-day dedup TTL | `:1064`, `:1091`, `:1133` | Holds |
| `_resolve_neighborhood` doc-relative link resolution | `:259`, outbound loop `:286-298` | Holds — **this is the in-module precedent for frame-correct resolution** (`(full.parent / target).resolve()` then `relative_to(repo_root)`) |
| Advisory detector call site | `:1267` | Holds; `scope_mode == "rotation"` only |
| Non-convergence already documented in code | `:1259-1265` | Holds — the comment names the closed-without-fixing re-file explicitly |

**File:line references re-anchored to current `main`.** The original table mapped issue
claims onto the `origin/session/docs-auditor-rename-guard` branch (PR #2728). That branch
is merged, so the mapping is obsolete; the table below is anchored directly to
`reflections/docs_auditor.py` at the baseline commit and every line was read to confirm
the claim.

| Claim | Current main line | Status |
|---|---|---|
| `_apply_fixes_to_file` def | `:690` (write at `:760`) | Holds; still the module's only filesystem write |
| `_file_issue_if_new` def | `:1064` | Holds; 30-day Redis dedup + `_open_issue_exists` (`:1003`) |
| `_send_telegram_notification` def | `:1147` | Holds; two rotation call sites, `:1915` (zero-diff+withheld) and `:1947` (success) |
| `audit()` def | `:1170` | Holds |
| `_commit_current_branch` call site | `:1297` | Holds; still `pr-changed-files` + `apply` + `touched` |
| `_commit_current_branch` def | `:1313` | Holds; `git add <touched>` + `git commit`, both `check=False` |
| `_git_dirty` def | `:1386` | Holds |
| `_has_open_pr_for_slug` def | `:1416` | Holds |
| `_daily_pr_cap_reached` def | `:1436` | Holds; 1 PR per calendar day |
| `_push_branch_and_pr` def | `:1459` | Holds; signature is still `(slug, repo_root, withheld=None)` — **no `files_touched`** |
| Late guards inside `_push_branch_and_pr` | `:1476-1483` | Holds; both return before `checkout -b` at `:1486` |
| `git add -A` | `:1494` | Holds |
| Unguarded `finally` restore | `:1557-1563` | Holds; plain `git checkout main`, no `check=`, return code discarded |
| `_write_liveness` def | `:1567` | Holds; two Redis keys set at `:1592` / `:1603` |
| `run_docs_auditor` def | `:1818` | Holds; dirty-tree guard `:1858`, rotation `audit()` call `:1884`, liveness `"ok"` `:1955`, blanket `except` `:1988` |
| `_pr_is_auto_merge_eligible` def | `:2005` | Holds; any review/reviewRequest/comment still disqualifies (`:2056-2057`) |
| `run_docs_branch_sweeper` def | `:2089` | Holds; auto-merge branch `:2236-2240`, stale-close `gh pr close --delete-branch` `:2263` |
| `__main__` CLI caller | `:2289-2295` | Holds; no `repo_root`, no git of its own |
| Scheduler discards `summary` | `agent/reflection_scheduler.py:639-640` | **Confirmed** — only `result.get("projects")` is read; `findings` and `summary` are dropped |
| Old contract text (descriptive) | `.claude/skill-context/do-docs.md:166` | Holds; moved from `:152` |
| Old contract text (imperative) | `.claude/skills-global/do-docs/SKILL.md:220-221` | Holds; same lines |
| Rename channel | absent | **Deleted upstream.** `_git_log_follow_renames`, `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`, `_detect_readme_broken_entries`, `GIT_LOG_FOLLOW_CAP`, `_RENAME_QUERY_COUNT` all grep to 0 |
| Sole surviving fix producer | `_detect_stale_term_fixes:473`, called once at `:1244` | New fact — see the premise note below |

**The premise of item 3 (Q5) is now true, where before it was not.** With the rename
channel gone, `_detect_stale_term_fixes` (`:473`) is the *only* producer of fixes, called
from the one detector site at `:1244`. A nonzero `fixes_withheld` therefore means
something genuinely surprising happened. Before #2842 it also meant "the auditor ran"
(spike-2), which would have made Q5's escalation permanently untrustworthy. That
precondition is satisfied upstream rather than by this lane.

**Cited sibling issues/PRs re-checked:**

- **PR #2728** — **MERGED** 2026-08-13T05:24:24Z (`45d0961f9`). The hard dependency is
  satisfied: `fixes_withheld`, the `withheld` result list, `WITHHELD_PR_MARKER`,
  `_absent_new_path_refs`, `_file_issue_if_new` and `_open_issue_exists` are all on main.
- **#2741 / PR #2842** — **CLOSED / MERGED** 2026-08-18T03:05:15Z (`a9205b065`),
  "chore(#2741): delete the docs-auditor rename channel". #2741 resolved as *delete*, not
  *rebuild*. This is the drift that triggered this refresh.
- **#2726, #2725, #2729** — all **CLOSED** as `NOT_PLANNED` duplicates consolidated into
  #2739 on 2026-08-13. Their content is still this plan's work; only the tracking moved.
  #2725 (wrong rename target) is additionally moot on the code, since the detectors it
  describes no longer exist.
- **#2711, #2713** — **CLOSED**; fixed by PR #2728, not by this work.
- **#2743** (delete `_write_liveness`) — still **OPEN**. See the caveat under Q5.
- **#2834** — **OPEN**, folded into this lane as Q7. Its `Refs #2741, #2739` footer is
  what ties it here. Its two `file:line` citations are corrected in the table above.
- **#2839** — **OPEN**, and it is a *true positive* the auditor filed:
  `docs/features/standardized-enums.md:19` cites `tools/granite_interactive_tui_poc/cli.py`
  in a live present-tense table row. Q7 must not silence it, and spike-7 asserts it does
  not. Fixing that doc is not this lane's work.
- **#2840, #2841** — **CLOSED as not-a-bug**. Both are the false-positive class Q7's
  hatch-widening removes; both sites were re-read on `f491306c5` and the defective
  classification reproduces exactly (spike-7).

**Commits on main since the issue was filed** (touching `reflections/docs_auditor.py`,
`agent/reflection_scheduler.py`, `.claude/skill-context/do-docs.md`,
`.claude/skills-global/do-docs/SKILL.md`, `tests/unit/test_docs_auditor_substrate.py`):

| Commit | What it did | Effect on this plan |
|---|---|---|
| `45d0961f9` (#2728) | word-anchored stale terms; added the path-existence invariant | Hard dependency, now satisfied |
| `ffbae5b1d` (#2782) | migration-context hatch (#2744) + bare-name existence invariant (#2759) | Widens the withhold classes and threads `fixes_withheld` into the PR body, the Telegram message, and `_write_liveness`. Q5's framing is corrected accordingly |
| `a9205b065` (#2842) | deleted the rename channel | Q6 is landed fact, not a decision this plan makes |

**Nothing has touched `reflections/docs_auditor.py` since `a9205b065`.** `git log
--oneline origin/main -- reflections/docs_auditor.py` still tops out at that commit, and
`git diff 15e66d931..f491306c5 -- reflections/docs_auditor.py` is empty.

**Active plans overlapping this area:** none. `docs/plans/docs-auditor-rename-guard.md`
shipped as PR #2728 and the rename-channel deletion shipped as PR #2842; no open plan
touches `reflections/docs_auditor.py`.

**Bug reproduction:** items 1, 3 and 4 were re-read on current `main` at the baseline
commit and the defective code paths are all present and unchanged — the self-commit at
`:1297`, the `git add -A` at `:1494`, the unchecked `finally` restore at `:1557`, the late
guards at `:1476-1483`, and the auto-merge predicate at `:2005`. Item 2 is gone: the code
it described no longer exists. **#2834's two defects both reproduce** on the baseline:
the `.md` gap is structural (the detector's regex admits only `.py`), and the
false-positive class was reproduced by executing the real
`_is_documented_deletion` / `_detect_deleted_target_issues` pair against the three cited
sites — #2840's and #2841's still classify as findings, #2839's still classifies as a
finding and should (spike-7).

**Portability correction applied in this pass.** Several Prerequisites and Verification
rows hardcoded `${AI_REPO_ROOT:-$HOME/src/ai}` as the shared main checkout. The lane can be
built on any fleet machine and the home directory differs per host, so a hardcoded row
fails for a reason that has nothing to do with the condition it tests. Every such row now
uses `${AI_REPO_ROOT:-$HOME/src/ai}`, the idiom `docs/sdlc/do-plan.md` already uses. Still
pipe-free and still POSIX `/bin/sh`. The substitution was applied to the quoted commands
inside **Critique Results** as well, so a builder copying a command out of a finding row
gets a runnable one; no finding's substance was touched.

**Lane identity re-anchored.** The recorded lane slug is **`sdlc-2739`** — worktree
`.worktrees/sdlc-2739`, branch `session/sdlc-2739`. The plan document's filename stem
(`docs-auditor-review-gate`) is the plan-doc slug and is deliberately different; per
[`docs/features/sdlc-lane-identity.md`](../features/sdlc-lane-identity.md) the two are
linked by `tracking:` frontmatter, not by name. Earlier revisions of this plan told the
builder to work in `.worktrees/docs-auditor-review-gate`, which does not exist. Every such
reference now names the recorded lane.

## Prior Art

- **PR #2728** (merged `45d0961f9`): word-anchors stale terms and adds the
  path-existence invariant. Closed #2711/#2713. Fixed the *generator* bug that
  produced `d7bf3ad99`; deliberately left the *structural* absence of a review gate
  to this slug.
- **PR #2782** (merged `ffbae5b1d`, issues #2744/#2759): added the migration-context
  hatch to the stale-term channel and extended the existence invariant to bare
  filenames, and threaded `fixes_withheld` through the PR body, the Telegram
  message, and `_write_liveness`. Relevant twice over: it is why the surviving
  channel's withhold classes are `target-absent` and bare-name rather than
  frame-mismatch noise, and it is why `_write_liveness` is no longer as inert as
  spike-4 found it.
- **PR #2842** (merged `a9205b065`, issue #2741): deleted the rename channel
  outright. Q6 below is the record of the reasoning; the work is done. It is also the
  direct cause of #2834's first half — deleting `_detect_readme_broken_entries` removed
  the only code that looked at `.md` link targets at all.
- **`eb340e23d`** ("Fix docs-auditor deleted-target flood: filtering + live-tracker
  dedup", #1555): the origin of `_is_placeholder_path`, `_build_line_context`, and
  `_is_documented_deletion`. Directly relevant to Q7: the deletion-narrative hatch
  #2834's comment asks for **already exists** — this lane widens it rather than inventing
  it, and the prior attempt's cue lists are the evidence for *how* it under-fires.
- **`208f08a69`** ("add per-run issue-filing cap to prevent flood"): the `per_run_cap = 5`
  bound Q5 hoists to `ISSUE_FILING_PER_RUN_CAP` and Q7 reuses unchanged.
- **#2839 / #2840 / #2841**: one true positive and two false positives filed by
  `_detect_deleted_target_issues` in a single run on 2026-08-17. The measured evidence for
  Q7's second half, and the control set spike-7 tests against.
- **#2726 / #2725 / #2729**: the three consolidated issues, all closed as duplicates
  of #2739. Filed separately during #2728's scoping precisely because each needed an
  owner ruling rather than a mechanical fix. #2726 and #2729 are this plan's
  substance; #2725 was answered by the deletion in #2842.
- **`docs/plans/docs-auditor-rename-guard.md`**: records the reasoning for deferring
  — *"a workflow decision about the `/do-docs` contract, not a bug fix, and it would
  change how every cascade behaves."* This plan makes that decision.
- **`reflections/housekeeping/merged_branch_cleanup.py` + `tests/unit/reflections/test_merged_branch_cleanup.py`**:
  the closest in-repo *structural* precedent for the test strategy this plan needs — a
  real git repo on disk, `PROJECT_ROOT` monkeypatched, and `git status --porcelain`
  asserted. Note its dispatcher patches `asyncio.create_subprocess_exec` because that
  module is async, so it is not directly reusable here (see **Test Impact**).
- No prior attempt to remove the self-commit exists. This is the first pass, so
  there is no **Why Previous Fixes Failed** section.

## Research

Purely internal work: no new libraries, no external APIs, no ecosystem patterns.
The one external-behavior question (git's `--follow` semantics) was settled by
direct experiment against real git rather than by search, because the answer is
version- and repo-specific and the experiment is cheap and authoritative.

No relevant external findings — proceeding with codebase context and the spike
results below.

## Spike Results

> **Status of spikes 1-3.** They investigated the rename channel and concluded it had
> to be deleted. That deletion **landed upstream** as #2741 / PR #2842 (`a9205b065`,
> 2026-08-18). The findings are preserved verbatim because they are the evidence record
> for a deletion that is now permanent, and because Risk 2 still needs them if anyone
> reads the removal as a regression. Only each spike's "Impact on plan" line is
> restated, to say what is landed versus what this lane still owes. Spikes 4 and 5
> describe live code and are current.

### spike-1: What do the three rename detectors actually produce?

> **Correction.** An earlier revision of this plan claimed `git log --follow` only
> follows a path that exists at HEAD, and concluded the detectors were unreachable
> dead code. **That claim was false** and has been removed. It was produced by
> sampling only paths that were absent at HEAD *and had never been a rename
> destination*, plus a positive control that was present at HEAD — which misses the
> reachable case entirely. The corrected finding below is stronger, and it is the
> finding #2741 acted on.

- **Assumption being tested:** issue #2739 item 2 asserts the detectors "pick the
  wrong target" — `renames[0][1]`, the newest rename hop.
- **Method:** prototype — real git repos, both a purpose-built temp repo and the
  actual `ai` repo history. git 2.50.1 (Apple Git-155).
- **Finding: the detectors do execute, and `renames[0][1]` is always the query path
  itself. Their output is therefore always frame-mismatched and never a rename fix.**

  `git log --follow` walks **backwards** from HEAD and emits the rename records that
  created the queried path. A path absent at HEAD still returns records, provided it
  was at some point a rename **destination**. Temp repo,
  `pkg/alpha.py → pkg/beta.py → pkg/gamma.py`:

  ```
  $ git log --follow --diff-filter=R --name-status --format= -- pkg/beta.py
  R100  pkg/alpha.py  pkg/beta.py        # beta.py is ABSENT at HEAD, and it returns
  $ git log --follow --diff-filter=R --name-status --format= -- pkg/alpha.py
  (empty)                                 # absent AND never a destination
  ```

  Confirmed on real repo history:

  ```
  $ git log --follow --diff-filter=R --name-status --format= -- docs/cursor-lessons.md
  R100  docs/improvements/cursor-lessons.md  docs/cursor-lessons.md
  ```

  **The decisive structural consequence:** because the walk goes backward *from the
  query path*, the newest record it emits is the one that **created** the query path.
  So `renames[0][1] == rel` — the destination is always the queried path itself,
  never a later hop. And every detector queries `rel` only after confirming
  `(repo_root / rel).exists()` is `False`.

  So each detector's "fix" is: *replace this reference with the repo-root-relative
  spelling of the same nonexistent path.* Never a rename correction. Concretely:

  | Detector | Post-#2728 outcome |
  |---|---|
  | `_detect_renamed_symbol_fixes` | Fully disabled. #2728 added `if renames and renames[0][1] != path`, and `renames[0][1] == path` always, so the guard rejects **every** candidate. |
  | `_detect_renamed_link_fixes` | Emits `(target, str(rel))` — a doc-relative link replaced by a repo-root-relative path. `str(rel)` does not exist, so `_absent_new_path_refs` withholds it. **Always withheld, on every run.** |
  | `_detect_readme_broken_entries` | Same substitution, same permanent withhold. |

  Pre-#2728 this wrote a broken link. Post-#2728 the existence invariant catches it
  — but as a **permanent** `fixes_withheld > 0` generator.

  Corollary, unchanged from the earlier revision and still true: the bad rename in
  #2711 came from the `STALE_TERMS` channel (`_detect_stale_term_fixes`), not from
  these detectors.

- **Confidence:** high. Both branches of the absent-path case tested (was-a-destination
  and never-a-destination), plus real repo history.
- **Impact on plan (restated 2026-08-18):** this finding is what #2741 acted on, and PR
  #2842 deleted the channel on that basis. Nothing in this lane's task list follows from
  it any more. It survives here as the reasoning behind an already-landed deletion, and
  as the argument Risk 2 needs.

### spike-2: The operational cost of leaving the channel in place

- **Assumption:** deleting the rename detectors is an isolated dead-code removal that
  can be sequenced independently of the rest of the plan.
- **Method:** trace spike-1's "always withheld" result through this plan's own Q5.
- **Finding: false, and the two interact badly.** Q5 makes `fixes_withheld > 0` file
  a deduped GitHub issue. spike-1 shows `_detect_renamed_link_fixes` and
  `_detect_readme_broken_entries` withhold on **every** run against any doc holding a
  reference to a path that is absent-but-once-a-rename-destination. That is a
  permanent withheld generator, so Q5 would file an escalation issue on essentially
  every rotation touching such a doc — the dedup only damps the volume, it does not
  stop the signal being permanently untrustworthy.

  Deleting the channel is therefore **load-bearing for Q5's signal quality**, not an
  optional tidy-up. Withheld must mean "something surprising happened", not "the
  auditor ran".
- **Confidence:** high.
- **Impact on plan (restated 2026-08-18):** this precondition is **satisfied upstream**.
  PR #2842 removed the permanent withheld generator, so `_detect_stale_term_fixes`
  (`:473`, called once at `:1244`) is the sole fix producer and a nonzero
  `fixes_withheld` is a real signal. Q5 can be built directly; there is no longer a
  prerequisite deletion task in this lane. The spike is the reason this lane was
  *gated* behind #2741 rather than racing it.

### spike-3: Does PR #2728's existence invariant catch the doc-relative frame mismatch?

- **Assumption (issue Q6):** the existence invariant may already reduce the rename
  defects to acceptable "declines to write" behavior.
- **Method:** code-read of `_absent_new_path_refs` (then `:537-547`; on the current
  baseline it is `:580` and still present) against `_detect_renamed_link_fixes` (then
  `:417-444`; deleted by #2842), informed by spike-1's corrected result.
- **Finding: the invariant does withhold, but for the wrong reason, and it is a
  partial answer at best.** Given spike-1 (`renames[0][1] == rel`, and `rel` is known
  absent), the substituted string is a path that does not exist at the repo root, so
  `_absent_new_path_refs` returns it and the fix is withheld. So on today's code the
  invariant *does* stop the bad write.

  But it stops it as a *nonexistent-path* violation, not as a *frame* violation, and
  that distinction matters for any future repair: the moment a repaired detector
  proposes a target that genuinely exists at the repo root, the invariant passes it
  and the frame mismatch is written unchecked. A doc at `docs/features/x.md` linking
  `(./old.md)` rewritten to `docs/features/new.md` resolves to
  `docs/features/docs/features/new.md`, while `_absent_new_path_refs` validates the
  raw string against the repo root, where it exists. The invariant is structurally
  blind to the frame; it only happens to catch today's output because today's output
  is also a nonexistent path.
- **Confidence:** high.
- **Impact on plan (restated 2026-08-18):** this was prerequisite 3 of 3 in #2741, and
  #2741 resolved as *delete*, so no repaired detector will ever meet it. The finding
  still matters as a constraint on any future rebuild: the existence invariant is
  structurally blind to the resolution frame, so a rebuild cannot lean on it. Nothing
  in this lane depends on it.

### spike-4: Which reporting channel does a function reflection actually have?

- **Assumption (issue Q5):** `findings` and `summary` reach no human, so any
  escalation built on them is a no-op.
- **Method:** code-read of `agent/reflection_scheduler.py`, `models/reflection.py`,
  `ui/data/reflections.py`, and every `reflections/*.py` escalation site.
- **Finding: confirmed, plus a cheap repair nobody noticed.**
  - `agent/reflection_scheduler.py:639-640` reads only `result.get("projects")`;
    `findings` and `summary` are discarded. Still true on the baseline commit.
    (Anchor corrected in the round-3 settle pass, R3-4 — this was cited as `:514-515`
    through rounds 1-3, which is an unrelated `every:`-schedule burst-fire guard inside
    `is_reflection_due`. The claim was always right; only the line numbers were rotten.)
  - **But `mark_completed` already accepts `output_summary`**
    (`models/reflection.py:186`, stored `:221`, passed through at `:254`), and the
    dashboard already renders it (`ui/data/reflections.py:286` and `:139` as
    `last_run_summary`). The scheduler simply never passes it. Wiring
    `summary` → `output_summary` is a one-line change that makes every function
    reflection's summary visible.
  - `_write_liveness`'s two Redis keys (`docs_audit:last_completed_run_ts`,
    `docs_audit:last_completed_run_summary`, set at `:1592` and `:1603`) have **zero
    programmatic readers** repo-wide — no `r.get` for either key anywhere, nothing in
    `ui/`. **Amended 2026-08-18:** they are not reader-free in the operator sense.
    `docs/features/docs-auditor.md:546-547` and
    `docs/features/vault-drift-audit.md:178` document a manual `redis-cli GET`, and
    #2782 threaded `fixes_withheld` into the summary payload with a docstring calling
    it "the only durable, queryable surface the rotation produces." See the caveat
    under Q5.
  - `_send_telegram_notification` (`:1147`) is hardcoded to chat `"Eng: Valor"` and is
    silent on failure (`check=False`, output dropped). **Amended:** it now has *two*
    rotation call sites, not one — `:1947` on the success path and `:1915` on the
    zero-diff-with-withheld path, the latter added by #2782.
  - `run_docs_auditor` (`:1818`) catches its own exceptions (`:1988`) and returns
    `{"status": "error", ...}`, so it **never raises to the scheduler** and never
    trips the consecutive-failure counter. Every run records as `success`.
  - The durable, most-used escalation across `reflections/` is `gh issue create`
    (six modules). The docs auditor already owns one — `_file_issue_if_new` (`:1064`),
    with 30-day Redis dedup and a cross-machine `_open_issue_exists` (`:1003`)
    pre-check.
- **Confidence:** high.
- **Impact on plan:** Q5 resolves to "reuse `_file_issue_if_new`" rather than
  inventing a channel, plus the one-line scheduler wiring as a bonus fix. The two
  amendments do not change that conclusion — a Telegram line is ephemeral and a Redis
  key nobody polls is not an escalation; only the issue is durable.

### spike-5: Is `files_touched` the complete set of the auditor's writes?

- **Assumption (issue Q3):** `git add -A` → stage only `files_touched` is
  unconditionally correct; confirm nothing depends on the sweep.
- **Method:** code-read — grep for every filesystem write in the module.
- **Finding: confirmed, and re-confirmed on the 2026-08-18 baseline.**
  `reflections/docs_auditor.py` contains exactly **one** filesystem write:
  `full.write_text(new_text, encoding="utf-8")` at `:760`, inside
  `_apply_fixes_to_file` (`:690`), whose path is precisely what lands in
  `files_touched`. `_run_vault_drift_detection` (`:1784`) only files GitHub issues; it
  writes no files. Nothing in the module depends on the `add -A` sweep at `:1494`, and
  the sweep can only ever capture *other* processes' work in the shared checkout.
- **Confidence:** high.
- **Impact on plan:** Q3 is a mechanical, zero-risk narrowing.

### spike-6: How much volume does the `.md` widening actually add? (#2834)

- **Assumption (#2834's own constraint):** *"Volume must be sized before it ships."*
  Widening `_detect_deleted_target_issues` to markdown-link `.md` targets might flood the
  5-per-run issue cap.
- **Method:** prototype, **read-only and issue-free by construction**. Called
  `_resolve_neighborhood`, `_build_line_context`, `_is_documented_deletion`,
  `_is_placeholder_path` and `_detect_deleted_target_issues` **directly** against the live
  checkout at `f491306c5`. `audit()` was never invoked, so `apply_mode` never existed in
  the run and no `gh issue create` could fire. Two measurements: a repo-wide census over
  all 1,076 `.md` files under `docs/` and `.claude/`, and a per-run distribution over 35
  rotation primaries sampled from the 274 candidates in `docs/features/`, each expanded
  through `_resolve_neighborhood(..., cap=NEIGHBORHOOD_CAP)`.
- **Finding: the widening is small, and the naive version has two distinct
  false-positive classes that the strict rule set removes.**

  | Rule set | Distinct findings repo-wide | Per-run mean | Per-run max |
  |---|---|---|---|
  | Naive (link regex + doc-relative resolution only) | 50 | 0.29 | 3 |
  | Strict (adds inline-code, URI-scheme, placeholder, archived-dir rules) | 41 | — | — |
  | Strict **and** scoped to `docs/` minus archived plan dirs | **19** | **0.29** | **3** |

  In 28 of 35 sampled runs the widening finds **nothing**, and it never approaches the
  per-run cap of 5 on its own. The 19 in-scope findings are a one-time backlog that drains
  at ≤5 per run and then stays drained, provided the dedup fix in Q7c lands.

  The two false-positive classes, both real and both cheap to exclude:

  - **Links displayed as syntax, not followed.** `docs/features/features-readme-sort-check.md:26`
    explains the index format with `` `[Feature Name](filename.md)` `` — inside a
    single-backtick code span. `.claude/skills-global/new-skill/SKILL_TEMPLATE.md`
    contributes three more (`SUB_FILE_A.md`…). A markdown link inside inline code is being
    *shown*, not followed.
  - **Non-path targets.** `docs/plans/completed/sdlc-1136.md` links a
    `file:/Users/…` URL; others carry mangled `blob/main/…` GitHub URLs. Any target
    carrying a URI scheme is out of the filesystem frame entirely.

  A representative sample of what survives, and it is real: `docs/features/observer-agent.md`
  is linked from three live feature docs and does not exist;
  `docs/features/compaction-hardening.md` links `docs/plans/compaction-hardening.md`,
  archived long ago.
- **Confidence:** high — executed against the real corpus with the real helpers, both
  measurements reproducible from the rule set stated in Q7a.
- **Impact on plan:** Q7a ships. The strict rule set is mandated in Q7a rather than left
  to the builder, because the naive version's 50 findings include a class
  (`SKILL_TEMPLATE.md`) that would file issues against files whose whole purpose is to
  contain placeholder links.

### spike-7: Does widening the deletion-narrative hatch fix the false positives without silencing the true one? (#2834)

- **Assumption:** the `.py` over-report is fixable by widening `_is_documented_deletion`,
  and the widening will not suppress genuine live references.
- **Method:** prototype — reimplemented `_detect_deleted_target_issues`'s filter chain
  with three widenings (heading **stems** rather than exact inflections, **word-anchored**
  prose cues rather than exact phrases, window ±2 rather than ±1), plus a **live-claim
  veto**, and ran it over `docs/features/` and the whole `docs/`+`.claude/` corpus.
  Controls: the three issues the detector filed on 2026-08-17.
- **Finding: it fixes both false positives, keeps the true positive, and the residual
  cost is measured rather than assumed.**

  All three controls behave correctly:

  | Control | Site | Wanted | Result |
  |---|---|---|---|
  | #2840 | `harness-abstraction.md:189` | suppress | **suppressed** (heading stem `cleanup`) |
  | #2841 | `harness-adapter.md:19` | suppress | **suppressed** (prose cue `deleted` at −2) |
  | #2841 | `harness-adapter.md:115` | suppress | **suppressed** (heading stem `delet` matches "Deletion") |
  | #2839 | `standardized-enums.md:19` | **keep** | **kept** — no cue within ±2, heading `### SessionType` |

  Volume, over `docs/features/` (rotation's primary corpus), findings not already
  suppressed by today's hatch:

  | Variant | Newly suppressed | Still reported |
  |---|---|---|
  | Widened cues, no veto | 20 | 37 |
  | Widened cues **+ live-claim veto** | **15** | **42** |

  Per rotation run over the same 35 sampled neighborhoods, the widened hatch drops the
  `.py` channel from mean 0.86 to 0.57 findings — almost exactly offsetting the 0.29 the
  `.md` branch adds. **Net per-run volume is unchanged** (0.86 → 0.86, max 8 in both,
  driven by one outlier neighborhood that already exceeds the cap today).

  **The residual, stated rather than claimed away.** Without the veto, five suppressions
  were wrong: `sdlc-stage-tracking.md:48` says two symbols *"remain defined in"* a file
  that no longer exists, and `structured-logging-telemetry.md:89` and
  `enforce-review-docs-stages.md:13` are similar. All five are recovered by the veto — a
  cue on the match's own line asserting presence (`remain`, `still`, `defined in`,
  `lives in`, `currently`, `implemented in`) cancels the suppression. What the veto does
  *not* recover is `test-coverage-standards.md:59/63/123/124`, whose "Tests:" rows cite
  deleted test files on lines that also carry the word "removed". Those four stay silent.
  That is the accepted direction of error and it is deliberate: a false positive costs a
  human a triage pass, a false negative costs a stale line in a doc that is already
  narrating its own history.
- **Confidence:** high for the controls (executed against the real sites); medium for the
  corpus-wide counts, which depend on the exact cue lists and must be re-measured at build
  time if the builder changes them.
- **Impact on plan:** Q7b ships **with the live-claim veto included**, not as an optional
  refinement. Without it the widening trades two known false positives for five new false
  negatives, which is not obviously a win; with it the trade is 15-for-4 in the direction
  the issue asks for.

### spike-8 (not run): does the `.md` branch need its own frame study?

- **Assumption:** the doc-relative vs repo-root frame question (#2725 / #2741) needs
  fresh investigation for the `.md` branch.
- **Method:** none — **deliberately reused spike-3's finding instead of re-running it.**
- **Finding:** spike-3 already settled the frame question and its conclusion transfers
  directly: `_absent_new_path_refs` is *structurally* blind to the resolution frame, so
  nothing in the existing invariant machinery can be leaned on for `.md` targets. What
  spike-3 did not have, and what closes the question, is that **the module already
  contains a frame-correct resolver**: `_resolve_neighborhood:286-298` resolves outbound
  `.md` links as `(full.parent / target).resolve()` and then `relative_to(repo_root)`.
  Q7a follows that idiom rather than inventing one, which is why Q7a's resolution rule is
  three lines and not a design problem.
- **Confidence:** high.
- **Impact on plan:** no spike budget spent; Q7a's resolution rule cites the in-module
  precedent.

## Data Flow

**Caller B (cascade) — after this change:**

1. **Entry point:** `/do-docs` skill Step 2d runs the substrate via the bash block
   in `.claude/skill-context/do-docs.md`.
2. **`audit(scope_mode="pr-changed-files")`:** detectors run, `_apply_fixes_to_file`
   writes to the working tree, `withheld` accumulates invariant rejections.
3. **Substrate returns** `files_touched` + `fixes_withheld` + `withheld`. **No git.**
   The working tree is dirty and stays dirty.
4. **`refresh_docs_in_memory(touched)`** fires on the applied (not committed) set.
5. **`/do-docs` Step 4 — the review gate:** the agent runs `git diff --name-only`,
   reconciles it against the Step 2 task list **plus the substrate's returned
   `files_touched`**, reads `git diff`, and reverts anything it cannot justify.
6. **Output:** `git add -A && git commit` by the skill, on the feature branch, with
   a human-reviewable diff that an agent has actually read.

**Caller A (rotation) — after this change:**

1. **Entry point:** scheduler invokes `run_docs_auditor` daily.
2. **Preflight (new position):** auth → lock → dirty-tree guard → **vault-drift
   detection** → rotation pick → `slug` → **daily-PR cap guard** →
   **open-PR-for-slug guard**. Both hoisted guards fire **before** any write, and both
   sit *after* vault-drift detection so a capped day still reports vault drift (B2).
   A capped run does skip the substrate's advisory issue loop; that narrowing is
   accepted and bounded in Q4 item 1. A fired guard **still stamps the rotation hash
   for the picked doc** before returning, so the pointer advances past the blocked slug
   (NEW-1).
3. **`audit(scope_mode="rotation")`** writes fixes to the shared main checkout.
4. **Zero-diff gate** unchanged.
5. **`_push_branch_and_pr(slug, root, files_touched, withheld)`:** records the
   starting ref, `checkout -b`, stages **only `files_touched`**, commits, pushes,
   opens the PR.
6. **Restore, verified and scoped:** `finally` returns to the recorded starting ref,
   runs `git checkout HEAD -- <files_touched>`, and asserts that no `files_touched` path
   is dirty in either porcelain column. It does **not** assert the whole tree is clean —
   foreign dirt outside `files_touched` is preserved by design. A failed restore is a
   hard error, not a discarded result.
7. **Outcome routing:** PR created → `status="ok"`. Guard fired pre-write →
   `status="skipped"`: **no working-tree write and no git operation**, but the rotation
   hash **is** stamped for the picked doc (NEW-1, below). Anything else →
   `status="error"`, escalated.

   **The invariant is "no working-tree writes and no git operations", not "no Redis
   writes" (NEW-1).** These are different claims and the plan needs only the first one.
   A guard fires precisely to keep the substrate from touching the shared main checkout;
   stamping `REDIS_LAST_RUN_HASH` touches Redis and nothing else, so it cannot wedge the
   checkout, cannot enter a PR, and cannot reach `main`. Wherever this plan says a
   guard-fired run performs "zero writes", read it as **zero writes to the working tree**
   — the rotation-pointer stamp is a deliberate, required exception, and Q4 item 1
   explains why omitting it produces a permanent silent shutdown.
8. **The review gate:** the PR. Nothing merges it automatically.
9. **Output:** `summary` → scheduler → `output_summary` → dashboard; withheld →
   deduped GitHub issue.

**The advisory reporting channel (Q7) — after this change:**

Unchanged in shape and position: it still runs inside `audit()` at `:1267`, still only
under `scope_mode == "rotation"`, still feeds the same `issue_findings` list and the same
`ISSUE_FILING_PER_RUN_CAP`. Three things change inside it:

1. `_detect_deleted_target_issues` emits findings for **two** reference shapes —
   backticked `.py` paths as today, and markdown-link `.md` targets resolved
   **doc-relative** (Q7a).
2. Both shapes pass through the **same widened** `_is_documented_deletion`, so a reference
   that a document is narrating rather than asserting is suppressed for either shape
   (Q7b).
3. `_file_issue_if_new` → `_issue_exists` now dedups against **all** issues, open and
   closed, so a human's ruling is durable and the channel converges (Q7c).

No write path is added or touched. A Q7 finding's only effect is a GitHub issue.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - `_commit_current_branch` — **deleted**. `audit()`'s return contract is unchanged;
    only its side effects shrink.
  - `_push_branch_and_pr` gains a required `files_touched: list[str]` parameter.
  - `_pr_is_auto_merge_eligible` — **deleted**.
  - `agent/reflection_scheduler.py` passes `output_summary` to `mark_completed`.
    This is a generic change affecting **every** function reflection, all
    beneficially (a summary that was discarded now renders).
  - `_open_issue_exists` — **renamed** `_issue_exists` and widened to `--state all`
    (Q7c). Signature unchanged. This changes dedup semantics for *every* finding the
    module files, deliberately: see Q7c.
  - `_is_placeholder_path` — extended to strip a `.md` suffix as well as `.py`.
  - `_is_documented_deletion` — widened cue matching plus a live-claim veto (Q7b).
    Internal to the module; both call sites are inside `_detect_deleted_target_issues`.
  - `_detect_deleted_target_issues` — gains a second match branch. Return shape
    unchanged (a list of `{title, body, category}` dicts); the new `category` value is
    `broken-md-link`.
- **Coupling:** decreases. The substrate stops owning git for Caller B; the skill
  that already owns git for the rest of the docs stage owns it for all of it.
- **Data ownership:** the commit decision moves from the substrate to each caller.
- **Reversibility:** high. Every change is a deletion or a narrowing, and the
  deployed code only changes on the next `/update` service restart.

## Appetite

**Size:** Large

Resized from Medium in the critique round 1 revision (C1). The inventory the critic
counted is real: a deleted helper, a changed signature, an ~80-line predicate deletion,
a guard hoist plus a restore rewrite inside a live writer against the *shared* main
checkout, a sweeper branch deletion, a cross-cutting scheduler change that affects every
function reflection, two skill bodies plus one skill-context, roughly eight doc passages,
fourteen existing test dispositions (two of which break at import), and a synchronous
`gh` dispatcher this plan itself says has no in-repo precedent.

**Still Large after folding in #2834 (Q7).** Q7 adds one function branch, three helper
widenings, one rename, and a test class — real work, but it lands entirely inside the
advisory reporting path, which no other Q-group touches. It does not move the plan out of
Large and it does not justify a second lane: #2834's two halves are the same question
about the same function, and its `.py` half depends on `_is_documented_deletion` being
widened, which is the same edit its `.md` half consumes. Splitting them would put two
lanes in one function.

**The lane stays single.** The critique proposed splitting into three lanes
(A = Q1, B = Q3+Q4, C = Q2+Q5). That is declined: the lane assignment is held at fleet
level, and splitting would fragment a change set whose parts share one test file, one
feature doc, and one set of anti-criteria. The isolation the split was reaching for is
preserved a cheaper way: **Step by Step Tasks is sequenced so each Q-group lands as its
own independently reviewable commit** (task 1 = Q1, task 2 = Q3+Q4, task 3 = Q2+Q5,
task 4 = Q7), so a reviewer can read them one at a time and a bad group can be reverted
alone. The three-way split remains available to the coordinator as an override if the lane
stalls.

**Two of the four build commits are already on the lane branch.** See the status block at
the top of **Step by Step Tasks** — BUILD verifies tasks 1 and 2 rather than
reimplementing them, and starts real work at task 3.

**Team:** substrate-builder, git-test-engineer, contract-documentarian, gate-validator
(the four agents named in **Team Orchestration**), plus PM and a code reviewer.

**Interactions:**
- PM check-ins: 2. One to confirm Q2 (unreviewed auto-merge dies), one to confirm Open
  Question 3 (whether the rotation reflection is re-enabled at all). OQ3 is explicitly a
  post-merge owner call and does not block the build; OQ2 is settled in this plan with a
  stated choice and needs no check-in.
- Review rounds: 2 (this touches a live scheduled reflection that writes to the
  shared main checkout)

## Prerequisites

**Every row is an exit-code assertion (B1).** `scripts/check_prerequisites.py:80-106` runs
each command with `shell=True` and judges **purely on the return code** — it never reads
stdout. A row phrased as *"run this and look at the output"* therefore passes no matter
what the output says. Five of the seven rows were that shape before the critique round 1
revision, including the two that matter most (no rotation in flight, clean shared
checkout), which made Risk 3's stated mitigation inert. Each row below now exits non-zero
in the state it is meant to catch, and every one was executed in both states while writing
this revision.

Two constraints on the shape of these commands, both structural:

- **No bare `|` in any cell.** `check_prerequisites.py:62` splits table rows on the pipe
  character, so a pipeline inside a cell truncates the command. No alternation in greps
  either.
- **POSIX `/bin/sh` only.** `shell=True` is `/bin/sh`, not bash: `test "$X" = Y`, never
  `[[ ]]`.

**Recorded finding on `gh --search "head:"` (NEW-7).** The critic flagged the old
docs-audit row as possibly fail-open because GitHub does not document `head:` as a prefix
match. Tested directly at revision time against this repo's open PRs: it **is** a prefix
match — `head:session` and `head:sess` both returned `session/sdlc-2494` and
`session/hook-validator-target-resolution`, while the mid-string probes `head:hook` and
`head:router` returned nothing. So `head:docs-audit` would in fact have matched
`docs-audit/{slug}-{ts}` and the row was not fail-open. It was still replaced, for a
different and stronger reason: the behavior is undocumented, and the critic's proposed
`jq` replacement is unusable under this table's own pipe constraint. The row now tests
the prefix explicitly in Python and depends on no undocumented search semantics.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #2728 merged to main — **satisfied 2026-08-13** | `test "$(gh pr view 2728 --json state -q .state)" = MERGED` | Build depends on `fixes_withheld`, `withheld`, `WITHHELD_PR_MARKER`, `_absent_new_path_refs`, `_file_issue_if_new`, `_open_issue_exists` — all confirmed present on main. Fails closed: an unmerged PR yields `OPEN`, and a `gh` failure yields an empty string; both compare unequal and exit 1 |
| PR #2842 merged to main — **satisfied 2026-08-18** | `test "$(gh pr view 2842 --json state -q .state)" = MERGED` | Q5's escalation is only trustworthy once the permanent withheld generator is gone (spike-2). Merged as `a9205b065`. Same fail-closed argument |
| Rename channel actually absent | `! grep -q _git_log_follow_renames reflections/docs_auditor.py` | Guards against building Q5 on a tree where #2842 was reverted. Shape is deliberate: both `grep -c` (exits 1 on a zero count) and `grep -L` (BSD grep still exits 1 when no line was selected) report *absence* as failure, so the negation is the only form that reads correctly. Exits 1 exactly when the symbol is back |
| No rotation in flight | `.venv/bin/python -c "import sys; from reflections.docs_auditor import _get_redis, REDIS_RUNNING_KEY; sys.exit(1 if _get_redis().exists(REDIS_RUNNING_KEY) else 0)"` | Changing commit behavior mid-rotation could interleave with a live `checkout -b` in the shared checkout. `sys.exit` carries the answer, so a live rotation is a FAIL rather than a printed `True` nobody reads. Must be the venv interpreter — a bare `python` has no `popoto`, and that too now fails closed, since `ModuleNotFoundError` exits 1 |
| Shared main checkout clean **on the auditor's own write surface** | `test -z "$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" status --porcelain -- docs .claude)"` | Scoped rather than whole-tree in critique round 2 (NEW-3), and **widened from `docs/features` to `docs .claude` in the round-3 settle pass (R3-1)**. The whole-tree form was practically unsatisfiable and contradicted this plan's own model: other lanes routinely hold uncommitted work in the shared checkout, and No-Gos forbids the builder from clearing it, so a whole-tree row handed the builder a FAIL they were not permitted to fix — it failed for exactly that reason during the round-1 revision. The narrower `docs/features` form was satisfiable but rested on a **false** justification: `docs/features/` is where rotation picks its *primary*, not where the module writes. The write surface is the primary doc's neighborhood, `_resolve_neighborhood:259` (called at `:1220`), spanning `docs/` and outbound-linked `.md` paths — `_apply_fixes_to_file` gates only on `.endswith(".md")` (`:1251`), never on a directory, so real targets today include `docs/plans/*.md`, `docs/sdlc/*.md`, `docs/tools-reference.md`, and `.claude/skills-global/do-plan/DOMAIN_FRAMING.md`. `docs .claude` covers that surface, so leftover dirt from a wedged pre-change rotation is actually detected. Foreign dirt outside those two trees does **not** block and is preserved by design (Race 1). **Known cost of the widening:** the scope now also catches other lanes' uncommitted work under `docs/` — most commonly in-flight `docs/plans/*.md` edits. If this row FAILs on foreign non-auditor dirt, the builder must inspect it and report rather than clear it (No-Gos, [EXTERNAL]); it is a stop-and-ask, not a licence to `checkout` another lane's file |
| No open docs-audit PR mid-flight | `.venv/bin/python -c "import json,subprocess,sys; o=subprocess.run(['gh','pr','list','--state','open','--limit','200','--json','headRefName'],capture_output=True,text=True); sys.exit(0 if o.returncode==0 and not [x for x in json.loads(o.stdout or '[]') if x['headRefName'].startswith('docs-audit/')] else 1)"` | An in-flight PR opened by the old code carries old-format staging. Rewritten in critique round 2 (NEW-7) to an explicit `startswith("docs-audit/")` test rather than `--search "head:docs-audit"`. **`--limit 200` added in the round-3 settle pass:** `gh pr list` defaults to `--limit 30`, newest-first, so with more than 30 open PRs an older `docs-audit/*` PR falls off the page and the row silently passes — a narrow fail-open, and exactly the PR this row exists to catch, since a stuck docs-audit PR is by definition an old one. The critic's own suggested `gh … -q` replacement cannot be used: `check_prerequisites.py:62` runs a naive `row.strip("|").split("|")`, which is blind to quoting, so the pipes inside the jq program truncate the cell. This form is pipe-free, and matches the `.venv/bin/python -c` shape the rotation-probe row above already uses. Fails closed twice over: a non-zero `gh` exit and an unparseable payload both exit 1. Both branches executed at revision time — exit 0 with no `docs-audit/` PR open, exit 1 against a `session/` control |
| `gh` authenticated | `gh auth status` | Sweeper and PR tests reason about real `gh` JSON shapes. Already exit-code correct — `gh auth status` exits non-zero when no account is logged in |
| Q7's filter chain is present to widen | `.venv/bin/python -c "import reflections.docs_auditor as d; assert d._is_documented_deletion and d._build_line_context and d._is_placeholder_path and d._detect_deleted_target_issues"` | Q7b **widens** an existing hatch rather than inventing one (spike-7). If any of the four helpers is absent, the tree is not the one this plan was written against and Q7's edits have no anchor. An `AttributeError` or `ImportError` exits 1, so it fails closed both ways |
| Q7's `.md` reporting gap is real on this tree | `! grep -q _detect_readme_broken_entries reflections/docs_auditor.py` | The gap #2834 records exists precisely because #2842 deleted this function. If it is back, the tree is a revert and Q7a would add a **second** `.md` channel beside a live auto-repairing one — the parallel path Principle 1 forbids. Same negation shape and same reasoning as the rename-channel row above |

## Solution

### Key Elements

- **A named gate per caller.** Cascade's gate is the `/do-docs` agent reading the
  diff. Rotation's gate is the pull request, reviewed by a human or `/do-pr-review`.
  Neither caller can produce a commit that no reviewer saw.
- **The substrate stops being a committer.** `audit()` writes files and returns what
  it wrote. It never runs `git commit`.
- **Rotation stages an explicit list.** The only files that enter a docs PR are the
  files the auditor wrote.
- **Rotation is transactional against the shared checkout.** Guards run before
  writes; the restore is verified; a failed restore is reported as an error.
- **Declining is loud, and durably so.** Withheld fixes become a deduped GitHub issue,
  and every function reflection's `summary` reaches the dashboard. The Telegram line
  #2782 added is the ephemeral half of this; the issue is the durable half.
- **Dead rename detection is already gone.** #2741 / PR #2842 deleted it, which is what
  makes `fixes_withheld > 0` a signal worth escalating rather than a per-run constant.

### Flow

**Cascade:** `/do-docs` Step 2d → substrate applies fixes, returns `files_touched`,
commits nothing → **Step 4 review gate**: agent reads `git diff`, reconciles against
task list + `files_touched`, reverts the unjustifiable → agent commits → feature-PR
review sees the docs diff alongside the code diff.

**Rotation:** scheduler → preflight guards (all pre-write) → substrate applies fixes
→ stage exactly `files_touched` → commit → push → **PR opened = review gate** →
human or `/do-pr-review` approves → `/do-merge`. No auto-merge. Withheld → deduped
issue → human.

### Technical Approach

The six open questions from #2739's Solution sketch, resolved, plus **Q7 for #2834**.
**Q6 is a record of work that landed upstream; Q1-Q5 and Q7 are this lane's work.**

---

**Q1 — Cascade: who commits? → The `/do-docs` skill. Delete `_commit_current_branch`.**

Delete `_commit_current_branch` (`:1313`) and its call site at `audit():1297`; keep the
`refresh_docs_in_memory` hook firing there (it operates on applied paths and does
not need a commit to be meaningful). `audit()` in `pr-changed-files` mode leaves a
dirty tree and returns `files_touched`.

The skill can carry the contract: `.claude/skills-global/do-docs/SKILL.md` Step 4
**already** contains the full sequence — `git diff --name-only`, "every file in this
list should appear in the Step 2 task list", `git diff`, "each change should be a
targeted update, not a rewrite", then `git add -A && git commit`. Only three edits
are needed:

1. Step 2d (`:220-221`): replace *"do not re-commit changes the substrate commits
   itself"* with an instruction to carry the substrate's `files_touched` into Step 4.
2. Step 4 (`:250-252`): reconcile a latent conflict the recon surfaced —
   *"If there are unexpected files, revert them"* (`:252`) would today condemn every
   substrate-touched file, since they are not in the Step 2 task list. The expected
   set becomes **Step 2 task list ∪ substrate `files_touched`**.
3. `.claude/skill-context/do-docs.md` — **three** stale-contract passages, not two
   (corrected in critique round 1, C6):
   - `:142-144` (the anchor previously given as `:144-146`, twice, and wrong both
     times): rewrite the descriptive claim ("commits them to the **current branch**...
     fires the memory-refresh hook after the commit").
   - `:152`: "Before trusting the substrate's **self-committed** output:" — a third
     stale string that the anti-criterion grep (`commits itself`) does not match, so it
     would survive a green validator. Rewrite it to say "Before trusting the substrate's
     applied-but-uncommitted output:". The Verification anti-criterion is widened to
     match `self-committed` for exactly this reason.
   - `:166-168`: delete the imperative ("Do not re-commit the substrate's changes — it
     commits them itself").

No other Caller B site is left writing to a tree nobody commits. Recon enumerated
all four `audit()` callers:

| Caller | Location | Passes `repo_root`? | Own git? | Disposition |
|---|---|---|---|---|
| Rotation | `docs_auditor.py:1884` | yes | yes | Handled by Q2/Q3/Q4 |
| `__main__` CLI | `docs_auditor.py:2289-2295` | no | no | Correct after the change — it prints JSON and leaves the tree for its caller. Its comment block is updated to say so. |
| Skill-context bash block | `.claude/skill-context/do-docs.md:127-140` | no | no | Contract rewritten (above) |
| Cheatsheet | `docs/features/docs-auditor.md:27`, `:40-46` | no | no | Documentation only; updated |

There is **no** MCP tool and **no** `pyproject.toml [project.scripts]` entry for the
auditor, so no other surface exists.

---

**Q2 — Rotation's review gate → the PR itself. Conservative unreviewed auto-merge does not survive.**

Delete `_pr_is_auto_merge_eligible` (`:2005-2086`) entirely and remove the auto-merge
branch from `run_docs_branch_sweeper` (`:2235-2258`).

Justification. Rotation is headless, so it cannot stop committing — the question is
who reviews, and the only available reviewer is whoever reads the PR. That makes the
PR the gate, and auto-merge is precisely the mechanism that bypasses it: it is how
an unreviewed auditor write reaches `main` without any human, which is the whole
defect this issue names.

The issue observes the predicate is inverted, and it is: `:2056-2057` returns `False` when
`reviews`, `reviewRequests`, **or** `comments` are present. So today it is strictly a
*"nobody has looked at this"* detector — it merges exactly the PRs that had no
review, and refuses exactly the ones that did. Repairing the inversion means
requiring a positive approval, at which point the predicate reduces to "merge
approved PRs", which is what `/do-merge` and the human already do. So the repaired
predicate is either redundant or unsafe; there is no version of it worth keeping.
Deleting it is the Principle-1 outcome and removes ~80 lines.

The sweeper keeps its two legitimate jobs: deleting stale `docs-audit/*` branches
whose PRs are already closed, and closing stale PRs (modified per Q5).

**Who actually gets told — the mechanism already exists.** Removing auto-merge only
works if a human learns the PR is there. Nothing automated merges a `docs-audit/*` PR.
The evidence for that was re-cited in critique round 1 (C4): the earlier citation,
`sdlc_progress.py:116`'s `_SDLC_BRANCH_RE = ^session/sdlc-\d+$`, names a symbol that no
longer exists — it was deleted by
`docs/plans/completed/sdlc-progress-lane-discovery-branch-shape.md`, which *widened* the
discovery shape. The conclusion survives the widening, but only because the boundary is
a namespace rather than a regex: `reflections/sdlc_progress.py:248`
(`_list_open_lane_prs`) restricts the corpus to the `session/` namespace and `:315`
(`_slug_from_branch`) reads the slug out of a `session/<slug>` branch. `docs-audit/*` is
not in the `session/` namespace, so lane discovery never sees it at all, and
`pr_review_audit` only inspects already-merged PRs. But the notification route is
already wired and was overlooked in the first revision of this plan:
`run_docs_auditor` calls `_send_telegram_notification` (`:1147`) with the PR URL to chat
`Eng: Valor` on **every** rotation PR (`:1947`). That is Q2's named gate mechanism — a
human is notified at creation and merges via `/do-merge`.

Two changes to make it carry the weight now placed on it:

1. Strengthen the message to state that **review is required** and that the PR will be
   **closed unmerged** if nobody acts. Note the message body at `:1936-1946` currently
   ends the withheld branch with *"PR is not auto-merge eligible"*, and the zero-diff
   branch at `:1915-1919` says *"no PR was opened to review them"*. Both strings name a
   mechanism this plan deletes and must be rewritten in the same task, not left as
   dangling references to auto-merge.
2. State plainly, in this plan and in `docs/features/docs-auditor.md`, that a rotation
   PR nobody reviews is closed unmerged at `STALE_PR_AGE_DAYS = 14`. That is a
   legitimate "nobody cared" outcome, not a silent loss — *provided* the withheld case
   still files its deduped issue per Q5, which is what distinguishes "nobody thought
   this was worth merging" from "the auditor tried to write something wrong".

Volume objection, addressed: without auto-merge, docs PRs accumulate. They do not —
`_daily_pr_cap_reached` bounds rotation to **1 PR per calendar day**, and the sweeper
still closes non-withheld PRs at 14 days.

Whether the rotation reflection should stay `enabled: true` at all, given that its
output now requires human attention it may never get, is a question for the owner and
is recorded in **Open Questions**. It is not a blocker for this plan.

---

**Q3 — Staging → `git add --` with the explicit `files_touched` list.**

`_push_branch_and_pr` (`:1459`, today `(slug, repo_root, withheld=None)`) gains a
required `files_touched: list[str]` parameter and stages
`["git", "add", "--", *files_touched]`. The `git add -A` at `:1494` is removed from the
module.

Confirmed nothing depends on the sweep (spike-5): the module contains exactly one
filesystem write, at `:760` inside `_apply_fixes_to_file` (`:690`), and its path is
exactly what lands in `files_touched`. `_run_vault_drift_detection` (`:1784`) files
issues and writes no files.
The sweep could therefore only ever capture *other* processes' work in the shared
checkout, which is the bug.

---

**Q4 — Rotation must not corrupt the shared checkout → both hoist the guards *and* verify the restore.**

The issue offers these as alternatives. They are not: hoisting fixes the common case,
verification fixes the rest.

1. **Hoist both guards to immediately before the `audit()` call — not into the top of
   the preflight block.** They live at `:1476-1483` today, inside `_push_branch_and_pr`
   and after the write. The obvious move is to put them beside the dirty-tree guard at
   `:1858`, and critique round 1 (B2) showed that is wrong: a guard that returns from
   `:1858` returns **before** `_run_vault_drift_detection` at `:1866-1869`, whose own
   comment declares it runs *"unconditionally, NOT gated behind the `_select_primary_doc`
   pick"*. A plan whose thesis is "declining must be loud" cannot start its build by
   silently switching off an unrelated reporting channel.

   **Correct placement:** after the dirty-tree guard, after
   `_run_vault_drift_detection` (`:1866-1869`), after the rotation pick, and after
   `slug = _path_to_slug(primary)` (`:1881`) — i.e. in the few lines immediately
   preceding the `audit()` call at `:1884`. That is still strictly **pre-write**, which
   is the whole point of the hoist, and it also solves the ordering problem for free:
   `_has_open_pr_for_slug(slug, PROJECT_ROOT)` (`:1416`) needs `slug`, which does not
   exist before `:1881`. `_daily_pr_cap_reached(PROJECT_ROOT)` (`:1436`) needs nothing
   but Redis, but it goes in the same place so both guards read as one block. A fired
   guard returns `status="skipped"` with **zero writes to the working tree and no git
   operations** — and with the rotation hash stamped, per the ruling immediately below.

   **Ruling: a guard-fired `skipped` run MUST stamp the rotation hash (NEW-1).** Before
   this change `_update_rotation_hash` (`:1950`) fired unconditionally, so even a blocked
   rotation advanced the pointer. This plan makes it fire only on `ok` (Task 2) and makes
   a fired guard write nothing. Those two changes are individually sound and jointly
   catastrophic: `_select_primary_doc` (`:1344-1383`) always picks the
   least-recently-audited doc out of `REDIS_LAST_RUN_HASH`, so an unstamped slug is
   re-picked on **every** subsequent run for as long as the guard keeps firing. For
   `_daily_pr_cap_reached` that self-clears at the next calendar day. For
   `_has_open_pr_for_slug` it lasts the PR's whole lifetime — and **forever** for a
   withheld PR, because Q5 item 2 mandates the sweeper never close a
   `WITHHELD_PR_MARKER` PR and Open Question 2 chooses to leave it open. One immortal
   withheld PR would therefore become a permanent, silent, whole-rotation shutdown
   reporting `skipped` rather than `error` — the exact failure class this plan exists to
   eliminate.

   So the guard path calls `_update_rotation_hash(project_key, [str(primary)])` for the
   picked doc before returning. **This is not a new mechanism: the zero-diff path at
   `:1912` already does precisely this**, stamping the single picked doc on a run that
   produced no PR, and the guard path is the same shape — a run that legitimately
   produced no PR and must not thereby freeze the rotation. Follow that precedent
   literally, including the `[str(primary)]` single-element list form.

   **The bound, stated accurately (corrected in the round-3 settle pass).** The deferral a
   fired guard costs is: **≤1 day for the daily cap**; and for the open-PR guard, **this
   doc is deferred until its next turn in the rotation**, while **rotation continues on
   other docs because the hash is stamped**. The guard skips one document, not the
   rotation.

   Read the open-PR bound that way rather than as "the PR's lifetime" — the earlier
   phrasing was optimistic in the builder's favour and understated the deferral. Stamping
   a doc the run never actually audited gives it a maximal timestamp, which pushes it to
   the **back** of the queue of every `docs/features/*.md`; it therefore waits a full
   rotation cycle, not merely until the blocking PR closes. That is still the correct
   design choice — a bounded, self-clearing deferral of one document beats a permanent
   silent shutdown of the whole rotation — but the cost is one cycle, not one PR.

   **Ruling on the substrate's advisory issue channel (B2, second half).** Skipping
   `audit()` also skips the advisory `_file_issue_if_new` loop at `:1290`
   (deleted-target, stub-doc, orphan-plan findings). **This narrowing is accepted in
   writing; the plan does not add a dry-run pass on the capped path.** Two reasons, the
   first decisive:

   - The critic's proposed alternative, `audit(..., apply_mode="dry-run")`, **does not
     work**. The advisory filing loop is gated at `:1279` on
     `apply_mode == "apply" and scope_mode == "rotation"`, so a dry-run pass files
     nothing. It would run every detector, produce no writes and no issues, and cost a
     full rotation's I/O for no output. Adopting it would have been a second silent
     no-op in a plan about silent no-ops.
   - The residual loss is bounded and self-healing. Both guards fire only in states that
     imply the channel already ran recently for the same content: the daily cap fires
     only when a rotation **already opened a PR today**, and that earlier run executed
     the advisory loop; the open-PR guard fires only when a PR for **this same slug** is
     already open, meaning this doc's advisory findings were filed on the run that
     opened it. On top of that, rotation runs daily and `_file_issue_if_new` dedups by
     title for 30 days, so a finding skipped on a capped day is filed on the next
     uncapped day and lands as the same issue it would have. The exposure is a deferral
     of at most one day per capped day, not a lost report.

   A Verification row asserts the placement holds structurally: vault-drift detection
   must remain reachable when the daily cap is set.
2. **A verified restore still matters,** because `checkout -b`, `add`, `commit`,
   `push`, and `gh pr create` can all fail after the substrate has written.
   `_push_branch_and_pr` records the starting ref via
   `git rev-parse --abbrev-ref HEAD` at entry, and on every exit path:
   - `git checkout <starting ref>` with the return code **checked**;
   - **`git checkout HEAD -- <files_touched>`** to discard the auditor's own edits;
   - delete the created branch if it exists.

   **`HEAD` is load-bearing and is not decoration (B3).** The bare form
   `git checkout -- <paths>` restores the worktree **from the index**, not from HEAD.
   There is a reachable path where the index holds the auditor's content at restore
   time: `git add -- <files_touched>` succeeds, `git commit` then fails, and
   `git checkout <starting ref>` carries the staged content across. A bare
   `checkout --` at that point rewrites the worktree *from* the auditor's own staged
   content and leaves it both staged and applied on `main` in the shared checkout —
   precisely the wedge this plan exists to remove, and the plan has already ruled out
   `reset --hard` and `checkout -f` as the remedy. `git checkout HEAD -- <paths>`
   (equivalently `git restore --source=HEAD --staged --worktree -- <paths>`) resets the
   index **and** the worktree for those paths only, so the "foreign dirt outside
   `files_touched` survives" property is untouched. This exact spelling appears in
   three places — here, Race 1's mitigation, and the Task 2 bullet — and they must be
   changed together; the drift between them is what let the defect live.

   **The restore is scoped to `files_touched` and must never be a whole-tree
   operation.** No `git checkout -f`, no `git reset --hard`, no `git clean`. The
   auditor runs in the shared main checkout, where other lanes routinely hold
   uncommitted work — at the time of writing, three files belonging to a different
   lane. A whole-tree force-restore would destroy them. This is the same constraint
   as Race 1, and the two must not drift apart again.
3. **A failed restore is a hard error, judged on scoped state.** The postcondition is
   **(a)** HEAD is back on the starting ref, and **(b)** no path in `files_touched` is
   dirty in **either** column of `git status --porcelain` (staged or worktree — the
   two-column check is what catches the B3 case above). It is explicitly **not**
   `not _git_dirty(repo_root)` — foreign dirt **outside `files_touched`** is expected to
   survive by design, so a whole-tree dirtiness check would fire a
   spurious `status="error"` on every run that coincides with another lane's work.

   **Accepted residual: the `files_touched` overlap case (C5).** The safety claim is
   *"foreign dirt outside `files_touched` is preserved"*, not the flat "foreign dirt is
   preserved" this plan asserted before critique round 1. If a concurrent lane holds an
   uncommitted edit to a file the auditor also rewrites, the scoped restore discards
   that lane's edit along with the auditor's. This is accepted rather than fixed, and
   the bound is: (a) the dirty-tree guard at `:1858` aborts the whole rotation if
   *anything* is uncommitted when it starts, so the overlap requires a foreign write
   landing inside the window between that guard and the failure, a window of one
   substrate pass; (b) `REDIS_RUNNING_KEY` serializes rotation globally, so at most one
   auditor is ever in that window; (c) the auditor only ever writes `.md` files it
   selected by rotation, so the collision requires a concurrent lane editing that same
   doc in that window. The residual is real but small, and every alternative is worse:
   leaving the auditor's own dirt behind is the wedge, and a whole-tree restore destroys
   strictly more. An optional hardening, not required for this build: before discarding,
   skip and report any `files_touched` path whose on-disk content no longer matches what
   `_apply_fixes_to_file` wrote, which converts the overlap from a silent discard into a
   named finding.
   If either scoped condition fails, `_push_branch_and_pr` signals failure and
   `run_docs_auditor` returns `status="error"` and escalates via Q5's channel. It must
   not write liveness `"ok"`, must not stamp the rotation hash, and must not send a
   success Telegram.
4. **Outcome routing in `run_docs_auditor`.** Because the guards moved,
   `pr_url is None` after `audit()` now unambiguously means failure, not "a guard
   fired". The three outcomes become distinct: `ok` (PR created), `skipped`
   (guard fired; nothing written to the working tree, no git operation, **rotation hash
   stamped**), `error` (write happened, PR did not).

   **What fires on which outcome, exhaustively (NEW-1).**

   | Side effect | `ok` (PR created) | `skipped` (guard fired) | `error` |
   |---|---|---|---|
   | Working-tree write / any git operation | yes | **no** | yes (the write already happened) |
   | `_update_rotation_hash` (`:1950`) | yes, with `files_touched` | **yes**, with `[str(primary)]` | **no** |
   | `_write_liveness` (`:1955`) | yes, `status="ok"` | **yes**, `status="skipped"` — see the ruling below | no |
   | Success Telegram (`:1947`) | yes | no | no |
   | Q5 withheld issue filing | when `fixes_withheld > 0` | no (`audit()` never ran) | no |

   `_update_rotation_hash` does **not** fire on `error`: a run that wrote and failed to
   produce a PR has not audited the doc, and re-picking it on the next run is correct
   there.

   **Ruling: the guard-fired `skipped` path DOES call `_write_liveness` (round-3 settle
   pass).** The terminal critique left this explicitly unspecified — either choice
   satisfies the plan's invariants — so it is decided here rather than left to the
   builder. Call
   `_write_liveness(slug, "skipped", None, 0, fixes_withheld=0)`, copying the zero-diff
   path at `:1913` verbatim in shape, with `fixes_withheld=0` because `audit()` never ran
   and there is nothing to report. Three reasons:

   - **Precedent, and the same precedent.** The guard path is already mandated to copy the
     zero-diff path's `_update_rotation_hash` call. The zero-diff path stamps liveness on
     the very next line for a run that also legitimately produced no PR. Splitting the two
     halves of that precedent would leave the module with two near-identical no-PR exits
     that behave differently for no stated reason — the kind of drift this plan spends
     its Q4 fixing.
   - **A frozen liveness timestamp is a false alarm, not a signal.** The keys are the
     operator's documented manual surface (`docs/features/docs-auditor.md:546-547`). If a
     guard fires daily, omitting the stamp makes `last_completed_run_ts` age
     indefinitely and reads exactly like a crashed or wedged auditor — indistinguishable
     from the failure mode this plan exists to make visible. Writing `"skipped"` says the
     true thing: the auditor ran, decided not to act, and is healthy.
   - **It costs nothing this plan has to defend.** The invariant Q4 protects is "no
     working-tree write and no git operation" on the guard path; a Redis write is
     explicitly outside it (see the NEW-1 note in Data Flow step 7). And it does not
     conflict with the `_write_liveness` deprecation in No-Gos: this adds one more call
     site to a channel #2743 will delete wholesale, which is one more line in that
     deletion, not a new reader or a competing surface.

   The only thing that stays forbidden is `_write_liveness(..., "ok", ...)` on any path
   that did not create a PR — the Failure Path Test Strategy row asserts exactly that, and
   `"skipped"` satisfies it.

On the failure mode this replaces: today's plain `git checkout main` in the `finally`
block (`:1557-1563`, no `check=`, return code discarded) fails when local
edits conflict, and the current code discards that failure. The fix is **not** to add
`-f` — that trades a wedged checkout for destroyed foreign work. The fix is to discard
the auditor's own paths first (`git checkout HEAD -- <files_touched>`, index and
worktree, per item 2), which removes the conflict without touching anything else, and
then to **check** the return code.

---

**Q5 — Withheld must escalate → a deduped GitHub issue, plus never deleting the branch that carries the fixes. Also wire `summary` to the dashboard.**

The issue is right that any escalation built on `findings`/`summary` is a no-op
today — but spike-4 found the repair is one line, so this plan does both.

**Precondition, now met.** Q5 is only worth building if `fixes_withheld > 0` is rare and
meaningful. spike-2 showed that was false while the rename channel lived, because the
channel withheld on every run. #2741 / PR #2842 deleted it, leaving
`_detect_stale_term_fixes` (`:473`, sole call site `:1244`) as the only fix producer, so
a withhold is now a genuine event. This lane does not have to do that deletion; it has to
not build Q5 without it, which the Prerequisites table now checks.

1. **Escalate withheld via the module's existing `_file_issue_if_new`.** Do not
   invent a channel. `gh issue create` is the most-used escalation across
   `reflections/` (six modules), it is durable rather than ephemeral, and the docs
   auditor already owns a deduped wrapper for it at `:1064` with 30-day Redis dedup and
   a cross-machine `_open_issue_exists` (`:1003`) pre-check.

   **The title template is mandated, not left to the builder (B4).** The title *is* the
   dedup key — `_file_issue_if_new:1075-1076` hashes it, and `_open_issue_exists` matches
   on it — so choosing it is the single most load-bearing decision in Q5. A per-run title
   ("docs-auditor withheld fixes") would swallow every distinct withhold for 30 days.
   Use a **per-defect** title:

   ```
   docs-auditor: withheld fix in {doc} ({old} -> {new})
   ```

   with `{doc}` the repo-relative path and `{old}`/`{new}` the withheld substitution, and
   file **one issue per withheld entry**, not one per run. The body names the reason
   (`target-absent` or bare-name), the run, and the PR URL when there is one.

   **No volatile component may appear in any title this module files** — no age, no
   date, no count, no run id. A title that changes between runs defeats dedup in the
   worst direction: it files a fresh issue every single run instead of suppressing one.

   **Cap the per-run volume, reusing the module's own bound (NEW-4).** "One issue per
   withheld entry" is unbounded per run, while the module's advisory loop already caps
   itself at 5 with the comment *"Hard per-run cap prevents flood"* (`:1277-1289`). A
   single pathological rotation pass could otherwise mint dozens of issues at once.
   Reuse the **same cap of 5** and the same suppression shape: file at most 5 withheld
   issues per run, and on hitting the cap emit one `logger.warning` naming how many were
   suppressed, exactly as `:1281-1288` does. Nothing is lost — rotation runs daily and
   the per-defect titles are stable, so a suppressed entry is filed on the next run under
   the title it would have had.

   **How the cap is shared, stated executably (R3-2).** "Reuse the existing
   `per_run_cap`" is not literally executable: `per_run_cap` is a **function-local** in
   `audit()` (`:1278`, `per_run_cap = 5 if scope_mode == "rotation" else 3`), and the
   withheld filing loop lives in `run_docs_auditor` — a different function that cannot see
   it. **Hoist it to a single module-level constant** and have both loops read that one
   name:

   ```python
   ISSUE_FILING_PER_RUN_CAP = 5          # rotation; advisory loop uses 3 for other scopes
   ```

   Concretely: define the constant beside the module's other tunables (near
   `STALE_PR_AGE_DAYS`, `:75`), rewrite `audit()`'s local to derive from it
   (`per_run_cap = ISSUE_FILING_PER_RUN_CAP if scope_mode == "rotation" else 3`), and have
   the new withheld loop in `run_docs_auditor` break on `ISSUE_FILING_PER_RUN_CAP`
   directly. That is the reuse this plan intends — one source of truth, so the two
   channels cannot drift — and it is still not the "separate new constant" forbidden
   above, which meant a second, independently-valued literal.
   Note also that `_file_issue_if_new` hardcodes `--label documentation`
   (`:1115-1116`), so every issue this channel files carries that label and no other;
   nothing here needs a new label.
2. **The sweeper never closes a PR carrying `WITHHELD_PR_MARKER`**, and it files its own
   escalation under a **distinct** title.

   **Deleting the predicate removes the sweeper's only access to a PR body — restore it
   (NEW-2).** The sweeper's own PR query at `:2147-2148` requests
   `number,state,createdAt` and nothing else; the `body` field is fetched at
   `:2023-2024`, inside `_pr_is_auto_merge_eligible`, which Q2 deletes. So after Q2 the
   marker check this item depends on has no data to read. The fix is one field: add
   `body` to the sweeper's `gh pr list --json` field set at `:2147`, making it
   `number,state,createdAt,body`. No second `gh pr view` round-trip is needed, and the
   test `gh` dispatcher must return `body` in its canned `pr list` payload or the marker
   test passes vacuously.

   On encountering such a PR at stale age it
   leaves both PR and branch untouched. This is what stops the
   propose → withhold → close → re-propose loop: the loop today is silent, and an
   open issue is not. Non-marker stale PRs keep their existing
   `gh pr close --delete-branch` behavior (`:2263`) — no withheld fixes are at risk there.

   **Ruling: the sweeper-side filing survives, with its own title (B4).** As written
   before critique round 1 this was a guaranteed no-op: item 1 files its issue on day 0
   and `_file_issue_if_new` returns `False` on a title it has already seen — it never
   comments, refreshes, or bumps — so a day-14 filing under the same title could never
   fire. The fix is a title that describes a *different fact*, because it is a different
   fact: item 1 says "the auditor declined to write something", the sweeper says
   "a PR carrying declined fixes has been sitting unreviewed past the stale bar". Use:

   ```
   docs-auditor: withheld PR #{n} still unreviewed
   ```

   Keyed on the PR number and nothing else. Dedup is then naturally per-PR: one issue
   per stuck PR, filed on the first sweeper pass that finds it past `STALE_PR_AGE_DAYS`
   and suppressed on every pass after. The age deliberately does **not** appear in the
   title — including it would mint a new issue every day, which is the failure mode
   the no-volatile-fields rule in item 1 exists to prevent. Put the age in the body.

   Correction of the phrasing this plan used before: `_file_issue_if_new` does not
   "refresh, via dedup". On a dedup hit it **is suppressed** and returns `False`. Any
   design that needs a second signal for the same subject needs a second title.
3. **Wire `summary` → `output_summary`** in `agent/reflection_scheduler.py:639-640`
   (anchor corrected from `:514-515` in the round-3 settle pass, R3-4):
   `state.mark_completed(duration, projects=projects_list, output_summary=summary)`
   where `summary = result.get("summary") if isinstance(result, dict) else None`.
   The field already exists on the model (`models/reflection.py:186`, stored into
   `last_run_summary` at `:221`, and onto the `ReflectionRun` row at `:254`), and
   `ui/data/reflections.py` already carries it out to the API — `:139` as the
   `last_run_summary` dict and `:286` on each run row.

   **Correction from critique round 1 (C3): it reaches `dashboard.json`, it is not
   rendered.** The earlier claim "the dashboard already renders it" was false. The value
   travels as far as `get_all_reflections()` → `ui/app.py:931` → the `dashboard.json`
   payload, and **no template under `ui/templates/` references `output_summary` or
   `last_run_summary`** (verified by grep at revision time). Leaving it there would have
   left this plan arguing that `_write_liveness` deserves no reader while promoting a
   JSON key with no rendered reader — the same defect, one layer up.

   **Ruling: render it, in this lane.** Add a one-line render of the last run's
   `output_summary` to `ui/templates/reflections/_partials/modal_content.html`, beside
   the existing `{% if r.last_error %}` block at `:54-56` and in the same shape:

   ```jinja
   {% if r.last_run_summary and r.last_run_summary.output_summary %}
   <h3>Last summary</h3>
   <div style="font-size: 12px; white-space: pre-wrap; word-break: break-word;">{{ r.last_run_summary.output_summary }}</div>
   {% endif %}
   ```

   That is the whole change — the data is already on `r` via `ui/data/reflections.py:139`
   and `models/reflection.py:221`. It carries a Documentation checkbox and a Verification
   row of its own. With it, "on the dashboard" in the Success Criteria is true as written;
   without it the criterion would have to be weakened to "in `dashboard.json`", and the
   argument against `_write_liveness` weakens with it. This makes every function
   reflection's summary visible, not just the auditor's.

**Deliberately not doing:** repairing `_write_liveness` (`:1567`). Its two Redis keys
(`:1592`, `:1603`) have no programmatic readers, and this plan gives the same information
a real surface (the dashboard, via `output_summary`). Adding a reader for a redundant
channel is the parallel-run migration Principle 1 forbids.

**Caveat, and a live tension with #2743.** Since #2782 (`ffbae5b1d`), `_write_liveness`
carries `fixes_withheld` and its docstring claims to be *"the only durable, queryable
surface the rotation produces"*, with a documented manual `redis-cli GET` at
`docs/features/docs-auditor.md:546-547` and `docs/features/vault-drift-audit.md:178`.
That claim stops being true once Q5 lands, because the GitHub issue is strictly more
durable and more visible. So this plan does not *depend* on the keys, but it does
invalidate #2743's premise in the opposite direction from how #2743 states it: the
justification for deletion becomes "Q5 superseded it", not "nobody ever read it". #2743
is out of scope here and should be re-argued on that basis rather than on the
zero-readers claim. Recorded in No-Gos.

---

**Q6 — Rename targets → LANDED UPSTREAM. The rename channel was deleted by #2741 / PR #2842. Not this lane's work.**

This is a statement of fact, not a decision this plan makes. PR #2842
(`chore(#2741): delete the docs-auditor rename channel`) merged to `main` as
`a9205b065` on 2026-08-18 and removed the entire channel:
`_git_log_follow_renames`, `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`,
`_detect_readme_broken_entries`, `GIT_LOG_FOLLOW_CAP`, and the `_RENAME_QUERY_COUNT`
global. All six grep to zero occurrences in `reflections/docs_auditor.py`.
`_detect_stale_term_fixes` (`:473`) is now the sole fix producer, called from the one
detector site at `:1244`.

**The reason, preserved.** From spike-1: `git log --follow` walks backward from the
query path, so the newest record it emits is the one that *created* that path, and
`renames[0][1] == rel` always. Every detector reached that line only when
`(repo_root / rel).exists()` was `False`. So the entire channel's output was a single
degenerate transform — *replace this reference with the repo-root-relative spelling of
the same nonexistent path*. Never a rename correction, in any repository, for any input.
Pre-#2728 that wrote a broken link; post-#2728 the existence invariant withheld it
permanently, which is the permanent-withheld-generator problem spike-2 describes.

Repair was rejected in #2741 because it needs three independent fixes together — a query
that resolves forward rather than backward, chain-walking with cycle protection, and
frame-correct re-relativization — which is a feature, not a bug fix. The capability was
not silently lost: `_detect_deleted_target_issues` (`:876`) still reports broken
references to a human as an issue.

**What this means for this lane.** Three things:

- Q5's escalation signal is trustworthy, because the permanent withheld generator is
  gone. That is spike-2's conclusion, satisfied upstream instead of by a task here.
- There is **no deletion task** in the Step by Step Tasks list. The build starts at the
  substrate git surface. The only obligation Q6 leaves behind is the Prerequisites row
  asserting the channel really is absent on the tree the build runs against, so this
  lane cannot be built on a revert.
- The deletion left a hole in the *reporting* surface. Q6's closing line — *"the
  capability was not silently lost: `_detect_deleted_target_issues` still reports broken
  references to a human"* — was true only for `.py` paths. **Q7 is the repair of that
  overstatement**, and it is why #2834 belongs in this lane rather than a fresh one.

---

**Q7 — Reference-shape parity (#2834). One question, asked once, for both path shapes:
is this reference a claim about the present or a record of the past?**

`_detect_deleted_target_issues` (`:876`) is the module's only surviving reference
reporter, and it gets the question wrong in both directions: it never asks it of `.md`
targets (so a broken link reaches nobody) and it asks it too literally of `.py` targets
(so deletion narrative files issues). Both are fixed inside that one function and its
filter chain. **This is a reporting change only — no new automated write, no auto-repair,
no resurrection of `_detect_readme_broken_entries`.** The whole point of #2739 is that an
auditor rewriting a human's document on its own judgment needs a review gate; adding a
`.md` *repair* here would reintroduce exactly what #2842 removed.

**Q7a — `.md` targets get a reporting path, resolved in the frame the document is read
in.**

Add a second `finditer` branch to `_detect_deleted_target_issues` over markdown links.
The rule set is mandated, because spike-6 showed the naive version files issues against
template files whose purpose is to hold placeholder links:

1. **Match** `[label](target.md)`, tolerating an optional `<…>` wrapper, surrounding
   whitespace, and a trailing `#anchor` or `?query` which is stripped before resolution.
2. **Skip** a target that begins with `#` (same-document anchor) or matches a URI scheme
   `^[A-Za-z][A-Za-z0-9+.-]*:` (covers `http:`, `https:`, `mailto:`, and the real
   `file:` case spike-6 found). A scheme means the target is not a repository path.
3. **Skip** a match that sits inside an **inline single-backtick code span** — count the
   backticks between the start of the match's line and the match; an odd count means the
   match is inside a span. This is a deliberate asymmetry with the `.py` branch, whose
   docstring says *"Inline single-backtick code is NOT suppressed — that is how genuine
   references are written."* That is true for a bare path and false for a link: a
   markdown link written inside a code span is displaying syntax, not linking. State the
   asymmetry in the code comment so nobody "harmonizes" the two branches later.
4. **Resolve doc-relative**, following the in-module precedent at
   `_resolve_neighborhood:286-298`: a target beginning with `/` resolves against the repo
   root, everything else against `doc_path.parent`. Normalize `..` segments, then require
   the result to stay inside the repo root; a target that escapes is out of scope and is
   skipped, not reported.

   **This is the #2725 / #2741 frame bug and spike-3's finding is the reason the rule is
   stated this way.** A target that exists at the repo root but not doc-relative is
   **still broken**, because doc-relative is the frame GitHub and every markdown renderer
   actually use — so a repo-root-spelled link in a nested doc is a true finding, not a
   false positive. Reuse spike-3's conclusion rather than re-deriving it: the existence
   invariant `_absent_new_path_refs` is structurally blind to the frame and must not be
   borrowed for this check.
5. **Apply the same suppressions as the `.py` branch** — fenced code blocks, deletion
   headings, deletion prose — via the shared `_build_line_context` / `_is_documented_deletion`
   pair, using the Q7b-widened versions.
6. **Skip placeholder targets.** `_is_placeholder_path` (`:790`) strips only a `.py`
   suffix on the final component; extend it to strip `.md` as well, and add the
   link-specific stand-ins spike-6 surfaced (`filename`, `path`, `name`) to
   `_PLACEHOLDER_PATH_COMPONENTS`. Do **not** add `sub_file_a`-style names: those are
   handled structurally by rule 7.
7. **Report only for containing docs under `docs/`, excluding the archived plan
   directories `docs/plans/completed/` and `docs/plans/done/`.** This is what takes the
   census from 41 to 19 (spike-6). Two justifications, both structural: `.claude/` holds
   skill *templates* whose links are deliberately unresolvable, and an archived plan is a
   historical record whose links describe the repo as it was. The scope applies to the
   `.md` branch only — the `.py` branch's scope is unchanged, so this is not a silent
   narrowing of existing behavior.

Finding shape, following the existing `.py` branch exactly:

```
title: "Doc references missing link target: {target} (in {doc})"
body:  "`{doc}` links to `{target}`, which does not exist. The link was resolved
        relative to `{doc.parent}`, the frame markdown renderers use."
category: "broken-md-link"
```

`{target}` is the **resolved repo-relative path**, not the raw link text, so the title is
stable under equivalent spellings of the same link. The title carries no age, date, count,
or run id — the same no-volatile-fields rule Q5 states, for the same reason: the title is
the dedup key.

**Q7b — the `.py` branch gets a deletion-narrative hatch that fires on real prose.**

The hatch already exists (`_is_documented_deletion`, `:847`, from #1555). It under-fires
for three independent reasons, each measured in spike-7. Fix all three plus add a veto:

| Today | Fails on | Change |
|---|---|---|
| `_DELETION_HEADING_KEYWORDS` are exact inflections: `migration`, `removed`, `deleted`, `deprecated` | `## Dead SDK Path Deletion` (`"deleted" in "deletion"` is `False`), `## Hook Cleanup (Phase 5)` | Match **stems**: `delet`, `remov`, `deprecat`, `migrat`, `cleanup`, `obsolete`, `retire` |
| `_DELETION_PROSE_CUES` are exact phrases: `deleted module`, `no longer exists`, … | "deleted (250 lines)", "no longer needed", "was removed as part of" | **Word-anchored** alternation over `deleted\|deletes\|deletion\|removed\|removes\|removal\|obsolete\|retired\|dropped\|gone\|no longer\|superseded\|formerly\|previously`, compiled once at module level like `_MIGRATION_CUE_WORD_RE` (`:408`). Word anchoring is load-bearing for the same reason #2782 gives at `:403-407`: a docs corpus is dense with snake_case identifiers, so an unanchored test fires inside `removed_at`, `dropped_frames`, and `deletion_marker`, which turns the tier into "the page mentions code" |
| Adjacent-line window is ±1 | `harness-adapter.md:19`, whose cue sits two lines above a wrapped sentence | Widen to **±2** |

Then add the guard that keeps the widening from over-suppressing:

**The live-claim veto.** A cue on the match's **own line** asserting that the target is
present — `remain`, `remains`, `still`, `defined in`, `lives in`, `currently`,
`implemented in`, word-anchored — **cancels** the suppression. Checked before the heading
and prose tiers, after the fence tier (a fenced block is illustrative regardless of what
it says). spike-7 measured this as the difference between trading two false positives for
five false negatives and trading fifteen for four. Concretely it is what keeps
`docs/features/sdlc-stage-tracking.md:48` — *"`classify_outcome()` and `fail_stage()`
remain defined in `agent/hooks/subagent_stop.py`"* — reported, which is correct: that
sentence is a claim about the present and it is false.

**Do not reach for `_has_migration_context` (`:439`).** #2834's comment describes the fix
as "give `_detect_deleted_target_issues` the migration-context hatch that #2782 gave the
other detectors", and that is right in spirit and wrong in mechanism.
`_has_migration_context` is parameterized on an `(old_term, new_term)` pair drawn from
`STALE_TERMS`; a deleted path has no successor term, so both of its tiers are undefined
here. The correct reading is that `_detect_deleted_target_issues` needs *its own* hatch to
work as well as the stale-term one does, and it already has one — this widens it.

**Q7c — the convergence answer: file once, ever.**

Widening a source that never converges makes the non-convergence worse, so it is fixed in
the same lane. Today `_open_issue_exists` (`:1003`) queries `--state open` (`:1025-1026`)
and the Redis fast-path expires at 30 days (`:1091`, `:1133`). A human who reads a finding,
decides the doc is fine, and closes the issue gets the same issue back a month later,
forever. `audit()`'s comment at `:1259-1265` already documents this and its only answer
is to meter the flood by gating filing to rotation.

**Change `_open_issue_exists` to query `--state all` with `--limit 100`, and rename it
`_issue_exists`.** No compatibility alias — Principle 1.

- **Why `all` is right for every title this module files, not just Q7's.** Each title is a
  stable per-defect key: the `.py` and `.md` findings key on (doc, target); the stub-doc
  and orphan-plan findings key on the doc; Q5's withheld findings key on (doc, old, new);
  Q5's sweeper finding keys on the PR number. A **closed** issue on any of those keys is a
  human's ruling on that exact defect. Re-asking is not diligence, it is not listening.
- **Why `--limit 100` is not decoration.** `gh issue list` defaults to `--limit 30`.
  Under `--state open` the candidate set is small; under `--state all` a full-text search
  can easily return more than 30 issues, and the exact-title match this function needs
  could fall off page one — a silent fail-open that would file a duplicate of an issue
  that already exists. This is the same defect class as the `gh pr list --limit 200`
  correction the round-3 settle pass applied to Prerequisites row 6.
- **The cost, stated plainly.** If a doc is fixed, its issue closed, and the doc later
  regresses in exactly the same way, the auditor stays silent about it. That is accepted.
  The auditor is not the only surface a regression has, and the alternative — a channel
  that re-litigates every human ruling on a 30-day timer — is the thing that made
  `documentation`-labelled issues a flood twice already (#1555, #1716).
- The fail-open-on-`gh`-error behavior is **unchanged** and must stay: a `gh` failure
  still returns `False` so a genuine finding is never silently dropped.
- Update the `audit()` comment at `:1259-1265`: the parenthetical *"(and re-files any that
  were closed without fixing the doc, since the dedup gate only sees open issues)"* is no
  longer true and must be deleted, not annotated.

**What Q7 explicitly does not do.**

- No repair of any `.md` link. Report only.
- No change to the `.py` branch's regex. #2759 ruled that `_PATH_REF_RE`'s `*` widening
  stays out of the detector (`:887-892`), and that ruling stands — Q7a adds a *link*
  branch, not a wider bare-path branch.
- No change to the per-run cap. Q7's findings enter the same `issue_findings` list and are
  bounded by the same `ISSUE_FILING_PER_RUN_CAP` Q5 hoists. spike-6 and spike-7 together
  show net per-run volume is flat, so no new bound is needed.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_push_branch_and_pr`'s `except subprocess.CalledProcessError` / `except Exception`
      (`:1551-1556`) — test that each leaves the checkout on the starting ref with a
      clean tree, and that the failure is observable in the return value (not only a
      `logger.warning`).
- [ ] The `finally` restore (`:1557-1563`) — test the case where `checkout` itself
      fails; assert the function reports failure rather than discarding the result.
- [ ] `run_docs_auditor`'s blanket `except Exception` (`:1988`) — assert it
      returns `status="error"` **and** that the tree was restored, so a mid-run
      exception cannot wedge the checkout.
- [ ] `_file_issue_if_new` failure during withheld escalation must not crash the
      rotation; assert the run still completes and the failure is in `findings`.
- [ ] `_send_telegram_notification` remains best-effort; assert a send failure does
      not change the returned `status`.

### Empty/Invalid Input Handling

- [ ] `_push_branch_and_pr` with `files_touched == []` — must not create a branch and
      must not run `git commit`. (Today `add -A` would happily commit unrelated work.)
- [ ] `_push_branch_and_pr` with a `files_touched` entry that no longer exists on
      disk — `git add --` fails; assert the restore runs and the error is reported.
- [ ] `audit(scope_mode="pr-changed-files")` with an empty changed-file set — returns
      `ok`, writes nothing, commits nothing.
- [ ] Sweeper with an empty `gh pr list` response and with malformed JSON — neither
      may delete a branch.

### Error State Rendering

- [ ] A rotation run that fails after writing must surface `status="error"` in the
      returned dict **and** through `output_summary` on the dashboard — assert the
      scheduler passes it through.
- [ ] A run with `fixes_withheld > 0` must produce a GitHub issue; assert the issue
      body names the doc and the attempted rewrite.
- [ ] Assert `_write_liveness` is **not** called with `status="ok"` on any path where
      the PR was not created.

## Test Impact

Real-git tests in a temp repo are **required** for the git surface. Blanket
`unittest.mock.patch` over `subprocess.run` is explicitly not an acceptable strategy
for `_push_branch_and_pr`, the staging set, the restore path, or the sweeper's close
path — every `git` command in those paths must actually run.

**Sanctioned pattern, since no in-repo precedent exists for it.** Use
`monkeypatch.setattr(docs_auditor.subprocess, "run", dispatcher)` — module-scoped, not
a global `patch("subprocess.run")` — where `dispatcher` intercepts **only**
`cmd[0] == "gh"` and returns a canned `CompletedProcess`, and delegates **everything
else to the real `subprocess.run`**. That is what satisfies both the "real git"
requirement and the Verification row forbidding a blanket mock; the two are consistent
only under this shape, so do not substitute a broader patch.

Precedent corrections, so nobody loses time hunting:

- `tests/unit/reflections/test_merged_branch_cleanup.py` is the nearest *structural*
  model (real repo on disk, `monkeypatch.setattr(module, "PROJECT_ROOT", repo)`,
  `git status --porcelain` asserted), **but it patches
  `asyncio.create_subprocess_exec`, not `subprocess.run`** — its module is async. Its
  dispatcher is not reusable here.
- That module lives at `reflections/housekeeping/merged_branch_cleanup.py`, **not**
  `reflections/merged_branch_cleanup.py`.
- There is therefore **no in-repo precedent for a synchronous `gh` dispatcher**. It
  must be written from scratch. Budget for that.
- `tests/unit/test_validate_docs_changed.py` supplies the `git_repo` fixture shape and
  the `_git` helper convention. There is no shared git fixture in `tests/conftest.py`;
  every file rolls its own.

Existing tests in `tests/unit/test_docs_auditor_substrate.py` — **130 collected**
(113 `def test_` functions; the gap is parametrization). Re-derived on the 2026-08-18
baseline; the count grew from 81 via #2782's demonstrated-red batch. Re-run
`--collect-only` at build time rather than trusting this number.

`TestGitLogFollowCap` no longer exists — #2842 deleted it along with
`_git_log_follow_renames`. There is no rename-detector test disposition left to apply;
grep for `_detect_renamed`, `_detect_readme_broken`, and `GitLogFollow` in the test file
returns nothing.

`_commit_current_branch` is patched at **five** sites (re-derived, not copied):

- [ ] `TestDoDocsContract::test_hook_invocation_under_pr_mode` (`:404`) —
      REPLACE: it patches `_commit_current_branch` and asserts on the mock. Rewrite to
      assert the hook fires **and** that no commit occurred (`git status --porcelain`
      non-empty in a real repo).
- [ ] `TestNonMarkdownApplyGuard::test_html_with_stale_term_in_attribute_left_untouched`
      (`:478`) — UPDATE: drop the `_commit_current_branch` patch and any assertion on
      it; keep the untouched-content assertion.
- [ ] `TestNonMarkdownApplyGuard::test_markdown_sibling_still_rewritten` (`:508`) —
      UPDATE: drop the now-nonexistent patch target.
- [ ] `TestExistenceInvariant::test_audit_surfaces_withheld_without_writing` (`:1086`) —
      UPDATE: drop the patch target; the withheld assertion is unaffected.
- [ ] `TestWithheldBlocksAutoMerge::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
      (`:1191`) — UPDATE: drop the patch target. The withhold→PR-body→Telegram→liveness
      propagation is still worth asserting; only the auto-merge framing at `:1224`
      ("that marker is what makes the PR auto-merge-ineligible") has to go.

The auto-merge assertions Q2 invalidates, enumerated so none is missed:

- [ ] `TestWithheldBlocksAutoMerge` (`:1109`) — RENAME and rescope. Its docstring
      (`:1110`) reads "must not produce an auto-mergeable PR" and its first test calls
      `docs_auditor._pr_is_auto_merge_eligible(123)` directly at `:1128`, which will not
      import once Q2 lands. Its valid subject afterwards is "withheld blocks the
      sweeper's stale-close", which is Q5's behavior and worth keeping.
- [ ] The comment at `:1224` and the docstring at `:1171` — UPDATE: both explain the
      marker in terms of auto-merge eligibility.
- [ ] `:585` ("`fixes_withheld == 0` and auto-merges unread") and `:1345` (the #2729
      block comment) — UPDATE: prose describing a deleted mechanism.

`_push_branch_and_pr` has **nine** references in that file, not six (re-derived):

- [ ] Seven patch sites — `TestZeroDiffGate::test_zero_diff_skips_pr_creation` (`:236`),
      `TestRefreshDocsInMemoryHook` (`:267`, `:287`, `:305`),
      `TestDoDocsContract::test_pr_mode_does_not_create_branch` (`:377`),
      `TestWithheldBlocksAutoMerge::test_rotation_result_surfaces_withheld_count`
      (`:1262`), `TestPRCreationFailure::test_push_failure_returns_finding_no_raise`
      (`:1517`) — UPDATE: the signature gains `files_touched`, so the `return_value`
      mocks stay but call assertions must accept the new arg. These stay mocked (they
      test `run_docs_auditor`'s orchestration, not git); real-git coverage is added
      separately below.
- [ ] Two **direct calls** — `:1158` and `:1218`, both in `TestWithheldBlocksAutoMerge`,
      invoke `docs_auditor._push_branch_and_pr("slug", repo, withheld=...)` positionally.
      These break outright on the new required parameter and must be updated, not just
      re-asserted. They are the reason the count is nine and not seven.
- [ ] `tests/README.md:272` — UPDATE: the index row reads `130` today (correct as of
      #2842). This plan changes the count again, so the row must be recomputed from
      `--collect-only` after the test work lands, not left at 130.

New coverage required (new file `tests/unit/reflections/test_docs_auditor_git_surface.py`,
real git throughout — the filename keeps the `docs_auditor` keyword so
`tests/conftest.py` `FEATURE_MAP` auto-tags it `validation`):

- [ ] Staging set: after `_push_branch_and_pr`, the commit contains **exactly**
      `files_touched`. Seed an unrelated dirty file in the temp repo and assert it is
      **not** in `git show --name-only HEAD` and is still dirty afterward. This is the
      direct anti-regression for `git add -A`.
- [ ] Early-return restore: force each failure (`git push` to a nonexistent remote,
      `gh pr create` returning non-zero, `git add --` on a missing path) and assert
      HEAD is back on the starting ref, the created branch is gone, and
      `git status --porcelain` matches the pre-call state byte for byte.
- [ ] **Foreign dirt survives (Race 1).** Seed an unrelated modified file that the
      auditor never touches, force a failure, and assert the file is **still modified**
      after the restore and that the run did **not** report `status="error"` merely
      because the tree was dirty. This is the regression test for the scoped-restore
      postcondition; a whole-tree `checkout -f` or a `not _git_dirty(repo_root)`
      assertion both fail it. Scope note: the assertion is about a file **outside**
      `files_touched`; the overlap case is an accepted residual (Q4 item 3) and is
      deliberately not asserted either way.
- [ ] **Staged-then-commit-failed restore (B3).** The direct regression test for the
      index-vs-HEAD defect. In a real repo: let `git add -- <files_touched>` succeed,
      force `git commit` to fail, run the restore, then assert **neither column** of
      `git status --porcelain` mentions any `files_touched` path — not the staged column,
      not the worktree column — and that HEAD is back on the starting ref. A restore
      written as bare `git checkout -- <paths>` leaves the auditor's content staged and
      applied and fails this test; `git checkout HEAD -- <paths>` passes it.
- [ ] Failed-restore reporting: make `checkout` fail and assert the function reports
      failure and `run_docs_auditor` returns `status="error"`.
- [ ] Guard hoisting: with the daily cap set, assert `audit()` is never called and
      the tree is untouched.
- [ ] **Guard-fired run still advances the rotation (NEW-1).** The direct regression test
      for the permanent-shutdown defect. Force `_has_open_pr_for_slug` true for the
      picked slug, run `run_docs_auditor`, and assert: the result is `status="skipped"`;
      `REDIS_LAST_RUN_HASH` carries a fresh timestamp for that slug; and a second
      immediately-following run selects a **different** doc. A build that fires
      `_update_rotation_hash` only on `ok` fails this test by re-picking the same slug
      forever.
- [ ] **Guard-fired run performs no working-tree write (NEW-1).** Same setup, asserting
      `git status --porcelain` is byte-identical across the run. Paired with the row
      above so the two halves of the invariant are pinned together: Redis is written,
      the tree is not.
- [ ] **Sweeper reads the marker from its own query (NEW-2).** Have the `gh` dispatcher
      return a `pr list` payload **without** a `body` field and assert the marker path
      fails loudly rather than silently treating the PR as unmarked and closing it. Then
      assert the built code requests `body` at `:2147`. Without this, the
      "withheld PR is not closed" test passes vacuously against a payload that never
      carried a body.
- [ ] **Withheld filing respects the per-run cap (NEW-4 / R3-3).** Feed more than 5
      withheld entries and assert **exactly 5** issues are filed and that the suppression
      warning names the remainder. This is the Verification row for NEW-4 — it is
      behavioral on purpose, because a `grep -c` on the cap symbol cannot tell a shared
      constant from a second one the withheld loop declared for itself.
- [ ] `files_touched == []` creates no branch and no commit.
- [ ] Sweeper close path: real repo + `gh` dispatcher. Assert a PR whose body
      contains `WITHHELD_PR_MARKER` is **not** closed and **no** `--delete-branch`
      is issued for it, and that an escalation issue is filed. Assert a non-marker
      stale PR is still closed.
- [ ] Sweeper auto-merge absence (anti-criterion): assert no `gh pr merge` is ever
      dispatched for any input.
- [ ] `agent/reflection_scheduler.py`: assert `mark_completed` receives
      `output_summary` equal to the reflection's returned `summary`. The exact home is
      `tests/unit/test_scheduler_result_forwarding.py` — it already asserts the
      `projects` half of the same call with the `state.mark_completed.call_args` kwargs
      idiom (`:164-166`), so the new case is a sibling, not a new file. Cover the
      non-dict and missing-key paths too, mirroring
      `test_run_reflection_passes_none_for_legacy_none_result` (`:170`).

Test hygiene for this lane — every invocation:

```bash
cd ${AI_REPO_ROOT:-$HOME/src/ai}/.worktrees/sdlc-2739
POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q
POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q
```

`POPOTO_TEST_DB=13` is mandatory and **DB 15 is forbidden** — popoto's pytest11
plugin flushes DB 15 before every test, and two other lanes are running
concurrently. Never bare `pytest`; `scripts/pytest-clean.sh` reaps xdist workers.

## Rabbit Holes

- **Rebuilding rename detection.** Three coupled fixes (query, chain-walk with cycle
  protection, frame-correct re-relativization) for a capability that has never once
  worked, at the moment we are trying to *reduce* unreviewed automated writes. #2741
  ruled on this and PR #2842 deleted the channel. Do not resurrect it inside this lane;
  a rebuild would be a new issue on a clean slate.
- **Designing a general review-gate framework for all reflections.** Other reflections
  also write. This plan fixes one auditor. A framework is a different project.
- **Making `_write_liveness` useful, or deleting it.** Tempting because the code is
  right there, but the dashboard `output_summary` path already carries the same
  information through a surface with a real reader. Building a second one is the
  parallel-run migration Principle 1 forbids. Deleting it is #2743's job, not this
  lane's — and note that #2782 made it carry `fixes_withheld`, so #2743's
  "zero readers" premise needs restating before that deletion is safe.
- **Adding a `do-not-auto-merge` GitHub label.** PR #2728's own comment explains why
  it did not: the label does not exist in this repo and `gh pr create --label` fails
  outright when it is missing. Moot anyway once auto-merge is deleted.
- **Rewriting the PR body to lift the withheld disqualification.** The issue floats
  "making the disqualification liftable once reviewed". With auto-merge gone there is
  no disqualification to lift — a reviewed PR is merged by a human. Do not build a
  body-rewriting mechanism.
- **Fixing the `/do-docs` skill's Step 4 push-ancestry guard, the memory-refresh hook
  body (#1249), or the skill's `status: "ok"` handling.** All adjacent, all separate.
- **Auditing every other `git add -A` in the repo.** In scope: `reflections/docs_auditor.py`.

## Risks

### Risk 1: The cascade loses its commit and the `/do-docs` agent does not pick it up

**Impact:** docs fixes are applied to the working tree and never committed. On a
feature branch that means the fixes silently vanish at the next checkout, or worse,
get swept into an unrelated commit.
**Mitigation:** the skill's Step 4 already ends in `git add -A && git commit` — the
commit is not new machinery, only the review in front of it. The three doc edits are
explicit acceptance criteria. Add a Verification anti-criterion asserting the stale
contract phrases appear nowhere **under `.claude/` or `docs/features/`** (NEW-5 — that
scope is deliberate and matches the Verification row exactly; a repo-wide reading would
fire on this plan document and on `docs/plans/completed/docs-auditor-rename-guard.md`,
both of which quote the phrase to describe history), plus a positive check that the
skill-context declares the new ownership.

### Risk 2: A reviewer reads this plan's docs as still owning the rename deletion

**Impact:** the plan was written against a pre-#2842 tree and its spike evidence is
still here. A reviewer who skims it may expect a deletion diff that this lane does not
produce, or may re-litigate a decision already closed as #2741.
**Mitigation:** Q6 is now a landed-fact statement naming the merge commit
(`a9205b065`), spikes 1-3 carry a status banner, and there is no deletion task in the
task list. If the deletion is nonetheless questioned during review, spike-1 is
reproducible in a few lines against real repo history: lead with the structural argument
(`renames[0][1] == rel` always, because `git log --follow` walks backward from the query
path), not with an empirical "it returns nothing" claim — an earlier revision of this
plan made exactly that claim and it was false. Show both absent-path branches
(was-a-destination and never-a-destination) so the finding cannot be mistaken for a
sampling error.

### Risk 3: Landing while a rotation is in flight

**Impact:** the daily reflection is mid-`checkout -b` in the shared main checkout
when the code changes underneath it, leaving a wedged tree — exactly the failure this
plan fixes.
**Mitigation:** the Prerequisites table gates on `REDIS_RUNNING_KEY` being unset, a
clean shared checkout, and no open `docs-audit/*` PR. Note that the **deployed** code
does not change at merge — the worker keeps running the old module until the next
`/update` service restart. Run `/update` deliberately, after confirming no rotation
is in flight, rather than letting the change land silently at an arbitrary time.

### Risk 4: Removing auto-merge lets docs PRs accumulate

**Impact:** a growing pile of open `docs-audit/*` PRs.
**Mitigation:** rotation is capped at 1 PR per calendar day by
`_daily_pr_cap_reached`, and the sweeper still closes non-withheld PRs at 14 days.
Steady state is bounded at ~14 open PRs worst case, and in practice far fewer. If
this becomes real, the answer is routing them to `/do-pr-review`, not resurrecting
unreviewed merges.

### Risk 5: The scheduler `output_summary` change affects every reflection

**Impact:** a reflection returning a huge or malformed `summary` now writes it to the
model and renders it on the dashboard.
**Mitigation:** guard with the same `isinstance(result, dict)` check that already
protects `projects`, coerce to `str`, and truncate. The field is already nullable and
already rendered for other producers, so the surface is not new.

## Race Conditions

### Race 1: A concurrent lane dirties the shared main checkout mid-rotation

**Location:** `reflections/docs_auditor.py` `run_docs_auditor:1858` (dirty-tree guard)
through `_push_branch_and_pr:1557-1563` (restore).
**Trigger:** rotation passes the dirty-tree guard, then another process writes to
`${AI_REPO_ROOT:-$HOME/src/ai}` before `git add`. With `git add -A` that foreign work is
committed into a docs PR; with the narrowed staging it is not, but a whole-tree
force-restore would still destroy it.
**Data prerequisite:** `files_touched` must be resolved before staging.
**State prerequisite:** the restore must not touch paths outside `files_touched`.
**Mitigation:** stage an explicit path list (Q3), and scope the restore exactly as Q4
specifies — `git checkout <starting ref>` (no `-f`) plus
**`git checkout HEAD -- <files_touched>`**, with the postcondition asserted only over
`files_touched`, never `_git_dirty(repo_root)`. The `HEAD` is required, not stylistic:
the bare form restores from the index, which on the staged-then-commit-failed path still
holds the auditor's own content (B3). This spelling must stay identical to Q4 item 2 and
to the Task 2 bullet.

**The safety claim, stated precisely (C5):** foreign dirt **outside `files_touched`** is
preserved by design. Dirt *inside* `files_touched` — a concurrent lane editing the very
doc the auditor rewrote, inside the window between the dirty-tree guard at `:1858` and
the failure — is discarded along with the auditor's edit. That overlap is an accepted
residual with its bounding argument recorded in Q4 item 3; it is not covered by the
regression test below and is not silently claimed away.

Assert in a real-git test that an unrelated dirty file (outside `files_touched`) survives
the commit **and** the restore, and that its presence does **not** produce
`status="error"`.

### Race 2: Two machines run the sweeper simultaneously

**Location:** `run_docs_branch_sweeper:2089`, lock acquired at `:2098`
(`REDIS_SWEEPER_RUNNING_KEY`, defined `:100`).
**Trigger:** `do-docs-branch-sweeper` has **no `project_key`** in
`config/reflections.yaml:145-152`, so unlike `docs-auditor` (`:196-205`, which has
`project_key: valor`) it runs on **every** machine. Two machines could both evaluate the
same PR. Note `config/reflections.yaml` is gitignored and per-machine, so this is a
property of the deployed copy, not of the repo.
**Data prerequisite:** the Redis lock is per-machine only if the machines share a
Redis instance.
**State prerequisite:** close and issue-filing must be idempotent.
**Mitigation:** pre-existing, not introduced here, and the operations are naturally
idempotent (`gh pr close` on a closed PR is a no-op; `_file_issue_if_new` dedups via
`_open_issue_exists` which is explicitly a cross-machine check). Note it in the docs
so the next reader does not rediscover it.

### Race 3: Guard hoisting widens the check-to-use window

**Location:** `run_docs_auditor` preflight vs. `_push_branch_and_pr`.
**Trigger:** `_has_open_pr_for_slug` now runs before `audit()` instead of immediately
before the branch creation, so a PR opened during the substrate run is missed and a
duplicate PR is created.
**Data prerequisite:** none.
**State prerequisite:** at most one open PR per slug.
**Mitigation:** accepted and bounded. `REDIS_RUNNING_KEY` already serializes rotation
globally, the daily cap allows only one PR per day regardless, and the failure mode
is a duplicate PR (visible, closeable) rather than a wedged checkout (invisible,
blocking). Do **not** re-check inside `_push_branch_and_pr` — a late guard return is
the exact structure this plan is removing.

## No-Gos (Out of Scope)

- [DONE UPSTREAM #2741 / PR #2842] Deleting the rename channel. This is **not**
  deferred work and **not** this lane's work — it merged to `main` as `a9205b065` on
  2026-08-18. #2741 is closed. Rebuilding rename detection correctly (a
  forward-resolving query, chain-walking with cycle protection, and frame-correct
  re-relativization) remains out of scope, but no open issue carries it; if the
  capability is ever wanted, it needs a fresh issue arguing the case from scratch.
- [SEPARATE-SLUG #2743] Deleting `_write_liveness` and its two Redis keys. This plan
  routes the same information to the dashboard via `output_summary`; removing the dead
  channel is a clean follow-up and keeping both would be the parallel-run migration
  Principle 1 forbids. Caveat for whoever picks it up: #2743's stated premise
  ("zero readers") was overtaken by #2782, which threaded `fixes_withheld` into the
  liveness summary and documented a manual `redis-cli GET`. The correct argument after
  this plan lands is that Q5's GitHub issue supersedes the keys, not that nobody read
  them.
- [ORDERED] Running `/update` to restart the worker so the new module is actually
  deployed. Must wait until PR merge, and must be run deliberately at a moment when
  no rotation is in flight — the code on disk changes at merge, but the running
  worker keeps the old module until the restart.
- [EXTERNAL] Manually clearing the shared main checkout if it is already wedged from a
  pre-change rotation. A human must inspect the dirt and decide whether it is
  auditor output or another lane's in-flight work before anything is discarded.

## Update System

No `/update` **script or skill changes** are required — this touches no dependencies,
no config files, and no new binaries.

One deployment note that is not a code change: `.claude/skills/update/SKILL.md:68`
runs `pytest tests/unit/test_docs_auditor_substrate.py -x -q` as a smoke test. That file
is modified by this plan (five `_commit_current_branch` patch sites, nine
`_push_branch_and_pr` references including two direct calls that break on the new
required parameter, and one class rename), so the smoke test must be green before merge.
**Corrected in critique round 1 (N2):** a red suite does *not* "fail `/update` on every
machine". The implementation is `scripts/update/deps.py:426-438`, which runs that smoke
test only inside the `anthropic` / `claude-agent-sdk` auto-bump path — a path only the
lockfile-maintainer machine takes. The real consequence is that a red suite **blocks the
dependency auto-bump on the lockfile-maintainer machine**. Still worth keeping green
before merge, but it is one machine and one code path, not a fleet-wide breakage. The new
real-git file is
deliberately **not** added to the update smoke test — it shells out to git and `gh`
and is too slow for that path.

Deployment behavior to state plainly: merging changes the code on disk; it does not
change the running worker. The docs auditor keeps executing the old module until the
next `./scripts/valor-service.sh restart` that `/update` performs.

## Agent Integration

No agent integration required — this is a reflection-internal change. There is no MCP
tool and no `pyproject.toml [project.scripts]` entry for the docs auditor, and none is
added. Both callers are unchanged in *how* they are reached:

- Caller A remains `config/reflections.yaml:196-205` →
  `reflections.docs_auditor.run_docs_auditor`, invoked by the scheduler. Note the
  deployed per-machine copy on this machine currently carries `enabled: false` for
  `docs-auditor` (`:205`) — see Open Question 3.
- Caller B remains the `/do-docs` skill's bash block, invoked by the agent as part of
  the SDLC docs stage. Its **contract** changes (the skill now commits), but its
  invocation surface does not.

The one cross-cutting change, `agent/reflection_scheduler.py` passing
`output_summary`, uses a model field and a dashboard renderer that both already exist.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/docs-auditor.md` to describe the new commit and review
      contract as the **only** status quo — no migration notes, no "previously"
      sections. Specific passages: the ASCII diagram (`:27`, currently
      "commit on current branch"), the Caller B section (`:40-46`), and the
      memory-refresh-hook description (`:433-434`, currently "after fixes are applied
      and committed" — the hook now fires after apply, before any commit).
- [ ] In the same file, fix the stale path `.claude/skills/do-docs/SKILL.md` at `:42`
      and `:599` — the real path is `.claude/skills-global/do-docs/SKILL.md`.
- [ ] Update the `## Branch Sweeper` section (`:443-450`) to remove auto-merge and
      describe the withheld-PR exemption.
- [ ] State plainly that a rotation PR is announced by Telegram to `Eng: Valor` at
      creation, is merged only by a human via `/do-merge`, and is **closed unmerged at
      14 days** if nobody acts. Frame that as the intended "nobody cared" outcome,
      not a failure mode.
- [ ] Update the `## Tests` section (`:558-566`) to name the new real-git test file.
- [ ] State the rotation-pointer rule plainly (NEW-1): a run that a guard skips still
      stamps the rotation hash for the doc it picked, so a blocked slug defers **that
      doc** rather than the whole rotation. Name the consequence of the alternative —
      an unreviewed withheld PR that is never closed would otherwise pin rotation on one
      document indefinitely — so nobody "simplifies" the stamp back onto the `ok` path.
- [ ] `docs/features/reflections.md:147-152` (the Caller B filing note) and the registry
      rows at `:205` (`docs-auditor`) and `:206` (`do-docs-branch-sweeper`, which still
      describes the pre-Q5 close behavior) — update both.
- [ ] `docs/features/README.md:67` — verify the Docs Auditor row still describes the
      feature accurately. (There is exactly one such row; the second one this plan
      previously cited no longer exists.)
- [ ] `docs/features/docs-auditor.md:546-547` and `docs/features/vault-drift-audit.md:178`
      — both document a manual `redis-cli GET` of the liveness keys as the operator
      surface. State that the durable surface is now the GitHub issue (Q5) and the
      dashboard `output_summary`, so #2743 is not later argued from a doc this plan
      left stale.

### Skill Documentation (the review gate itself)

- [ ] `.claude/skills-global/do-docs/SKILL.md` Step 2d (`:220-221`) — replace the
      "do not re-commit" instruction with the carry-`files_touched`-into-Step-4
      contract. **Note this file is a hardlink** to `~/.claude/skills/`
      (`scripts/update/hardlinks.py`); the edit propagates on `/update`.
- [ ] `.claude/skills-global/do-docs/SKILL.md` Step 4 (`:250-252`) — the expected file
      set is the Step 2 task list **∪** the substrate's `files_touched`.
- [ ] `.claude/skill-context/do-docs.md` — all **three** stale-contract passages:
      `:142-144` (descriptive; the anchor was wrong as `:144-146` before critique round
      1), `:152` ("Before trusting the substrate's **self-committed** output:"), and
      `:166-168` (imperative). Rewrite the first two, delete the third. `:152` is the one
      the old anti-criterion grep missed, so it is called out separately here on purpose.
- [ ] `.claude/skills-global/new-audit-skill/BEST_PRACTICES.md:32` — cites the docs
      auditor as the canonical "Skill commits results" full-apply pattern to copy.
      Leaving it is a historical artifact that will propagate the deleted contract into
      new skills. (The companion citation in that skill's `SKILL.md` that this plan
      previously named is already gone; re-grep at build time rather than assuming.)

### Dashboard Rendering (Q5 item 3)

- [ ] `ui/templates/reflections/_partials/modal_content.html` — add the one-line
      `r.last_run_summary.output_summary` block beside the existing `{% if r.last_error %}`
      block at `:54-56`, so the summary the scheduler now forwards has an actual rendered
      reader and not just a `dashboard.json` key. Exact snippet in Q5 item 3.

### Inline Documentation

- [ ] `audit()` docstring — state that it never commits and that the caller owns the
      commit.
- [ ] `_push_branch_and_pr` docstring — document the explicit staging set and the
      verified-restore postcondition.
- [ ] `__main__` block comment (`:2289-2295`) — state that it leaves a dirty tree.
- [ ] `config/settings.py:205` — verify the `git_subprocess_s` field description is
      still accurate after `_commit_current_branch` is deleted.

## Success Criteria

- [ ] No code path in `reflections/docs_auditor.py` creates a git commit that has not
      passed a review gate; the gate is named and documented for each caller.
- [ ] `_commit_current_branch` is gone; `.claude/skill-context/do-docs.md` and
      `.claude/skills-global/do-docs/SKILL.md` state the new commit ownership, and no
      "do not re-commit, it commits them itself" instruction survives **under `.claude/`
      or `docs/features/`** (NEW-5 — the same scope the Verification row uses; plan
      documents that quote the phrase historically are deliberately out of scope).
- [ ] `git add -A` does not appear in `reflections/docs_auditor.py`; rotation stages
      only `files_touched`.
- [ ] `_pr_is_auto_merge_eligible` is gone and the sweeper never runs `gh pr merge`.
- [ ] A rotation run that returns early leaves the shared main checkout exactly as it
      found it, and does not report `status="ok"` for a pass whose output it discarded.
- [ ] A rotation run that a guard skips still advances the rotation pointer for the doc
      it picked, so no blocked slug — including one behind a withheld PR that is never
      closed — can pin the rotation on a single document.
- [ ] A run that withholds fixes files a deduped GitHub issue, and the sweeper never
      closes or deletes the branch of a PR carrying `WITHHELD_PR_MARKER`.
- [ ] `agent/reflection_scheduler.py` passes `output_summary` to `mark_completed`, and
      the value is **rendered** on the reflections dashboard modal, not merely present in
      `dashboard.json`.
- [ ] No surviving string in the module, the skills, or the feature docs promises
      auto-merge — including the rotation Telegram message, which says
      "PR is not auto-merge eligible" today.
- [ ] Real-git tests in a temp repo cover: the staging set, the early-return restore,
      the failed-restore error path, and the sweeper's close path. No new
      `unittest.mock.patch` over `subprocess.run` for the git surface.
- [ ] `docs/features/docs-auditor.md` describes the new contract as the only status
      quo — no migration notes, no "previously" sections.
- [ ] Tests pass (`/do-test`), run with `POPOTO_TEST_DB=13` via
      `scripts/pytest-clean.sh`.
- [ ] Documentation updated (`/do-docs`).
- [ ] No xfail tests relate to this bug (verified at plan time: none exist).

## Team Orchestration

### Team Members

- **Builder (substrate git surface)**
  - Name: `substrate-builder`
  - Role: the `reflections/docs_auditor.py` changes for Q1-Q5
  - Agent Type: builder
  - Resume: true

- **Test engineer (real git surface)**
  - Name: `git-test-engineer`
  - Role: the new real-git test file and the existing-test dispositions
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `contract-documentarian`
  - Role: skill bodies, skill-context, feature docs
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `gate-validator`
  - Role: verify every acceptance criterion, especially the anti-criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

**Sequencing rule (C1).** The lane is not split, so the isolation comes from the commit
boundaries: **each build task lands as its own commit** — task 1 = Q1, task 2 = Q3+Q4,
task 3 = Q2+Q5 — so a reviewer reads one Q-group at a time and a bad group can be
reverted without unpicking the others. Do not squash the build tasks together.

**On N3 (parallelism).** Partially applied. `document-contract` is moved to depend on
`preflight` and run in parallel, because every documentation decision is already fixed in
this plan's text and none of it needs to see the code. The three **build** tasks stay
sequential, and the critique's premise that Q2/Q5 are disjoint from Q1/Q3/Q4 does not
hold: Q2 rewrites the Telegram messages at `:1915-1919` and `:1936-1946`, and Q5 files
issues, all inside `run_docs_auditor` — the same function whose outcome routing task 2
restructures. Running them in parallel would produce conflicting edits to one function
and destroy exactly the per-group reviewability the sequencing rule exists for.

### 0. Preflight

- **Task ID**: preflight
- **Depends On**: none
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm PR #2728 and PR #2842 are both `MERGED`, and that
  `grep -c '_git_log_follow_renames\|_detect_renamed' reflections/docs_auditor.py`
  returns `0`. If any of the three fails, stop and report — Q5 is unsound on a tree
  where the rename channel is present (spike-2). Rebase this lane onto current `main`.
- Run every row of the Prerequisites table. In particular confirm
  `REDIS_RUNNING_KEY` is unset and `${AI_REPO_ROOT:-$HOME/src/ai}` is clean.
- Re-run `--collect-only` on `tests/unit/test_docs_auditor_substrate.py` and re-grep
  every `file:line` in this plan before relying on it. The anchors are current as of
  the baseline commit and nothing more.
- Do not enter any worktree other than `.worktrees/sdlc-2739`.

### 1. Cascade commit ownership (Q1) — commit 1 of 3

- **Task ID**: build-cascade
- **Depends On**: preflight
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: substrate / caller contract
- Q1: delete `_commit_current_branch` (`:1313`) and its call site (`:1297`); keep
  `refresh_docs_in_memory` firing on the applied set.
- Update the `audit()` docstring and the `__main__` comment block (`:2289-2295`) in the
  same commit — the contract change and its inline documentation belong together.
- Land this as its own commit before touching the rotation path.

### 2. Rotation git surface (Q3, Q4) — commit 2 of 3

- **Task ID**: build-rotation
- **Depends On**: build-cascade
- **Validates**: tests/unit/test_docs_auditor_substrate.py, tests/unit/reflections/test_docs_auditor_git_surface.py (create)
- **Informed By**: spike-5 (files_touched is the complete write set)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: git / shared-checkout safety
- Q3: add the required `files_touched` parameter to `_push_branch_and_pr` (`:1459`);
  replace the `git add -A` at `:1494` with `git add -- <files_touched>`; return early
  without creating a branch when the list is empty.
- Q4 (guard hoist, B2): move `_daily_pr_cap_reached` (`:1436`) and
  `_has_open_pr_for_slug` (`:1416`) out of `_push_branch_and_pr` (`:1476-1483`) into
  `run_docs_auditor` — **immediately before the `audit()` call at `:1884`, after
  `_run_vault_drift_detection` (`:1866-1869`) and after `slug` is computed (`:1881`)**.
  Do **not** put them beside the dirty-tree guard at `:1858`: that placement returns
  before vault-drift detection, which its own comment says runs unconditionally. The
  advisory `_file_issue_if_new` loop inside `audit()` (`:1290`) is knowingly skipped on a
  capped run; that narrowing is ruled on and accepted in Q4 item 1 — do **not** "fix" it
  with a `apply_mode="dry-run"` pass, which files nothing (`:1279` gates filing on
  `apply_mode == "apply"`; `:1278` is the `per_run_cap` assignment).
- Q4 (restore, B3): record the starting ref on entry to `_push_branch_and_pr`; make the
  `finally` restore (`:1557-1563`) verified — check the `checkout` return code, run
  **`git checkout HEAD -- <files_touched>`** (the `HEAD` is required; the bare form
  restores from the index and re-applies the auditor's own staged content on the
  commit-failed path), delete the created branch — and **scoped**, so foreign dirt
  outside `files_touched` survives (Race 1). No `checkout -f`, no `reset --hard`, no
  `clean`. Make a failed restore a reported failure, judged on both porcelain columns for
  `files_touched` only.
- Q4: route outcomes as `ok` / `skipped` / `error`. `_write_liveness(..., "ok", ...)`
  (`:1955`) and the success Telegram (`:1947`) fire **only** on `ok`. On the guard-fired
  `skipped` path call `_write_liveness(slug, "skipped", None, 0, fixes_withheld=0)`,
  copying the zero-diff path at `:1913` — ruled on in Q4 item 4's outcome table, so do not
  re-decide it. Consult that table for the exhaustive per-outcome side-effect list.
- Q4 (NEW-1): `_update_rotation_hash` (`:1950`) is the **exception** — it must fire on
  `ok` *and* on the guard-fired `skipped` path, never on `error`. On the `skipped` path
  call `_update_rotation_hash(project_key, [str(primary)])` for the picked doc, copying
  the zero-diff path at `:1912` verbatim in shape. Do **not** simplify this to "fires
  only on `ok`": `_select_primary_doc` (`:1344-1383`) re-picks the least-recently-audited
  doc, so an unstamped slug is re-picked every run, and an immortal withheld PR (Q5 item
  2 forbids the sweeper from closing one) would then pin the rotation on that one doc
  permanently while reporting `skipped`. The `skipped` path stamps Redis and nothing
  else — it still performs no working-tree write and no git operation.

### 3. Auto-merge deletion and escalation (Q2, Q5) — commit 3 of 3

- **Task ID**: build-escalation
- **Depends On**: build-rotation
- **Validates**: tests/unit/reflections/test_docs_auditor_git_surface.py
- **Informed By**: spike-4 (gh issue is the durable channel; output_summary is one line)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- Q2: delete `_pr_is_auto_merge_eligible` (`:2005-2086`) and the auto-merge branch of
  `run_docs_branch_sweeper` (`:2235-2258`).
- Q2 (C2): **the two ranges above are not the whole job.** The Verification row demands
  `grep -c 'auto-merge' reflections/docs_auditor.py` → `0` against **16** hits, and eight
  of them are outside both ranges. Every anchor, re-derived at revision time:
  `:79`, `:90`, `:91` (module header comments), `:1470` (`_push_branch_and_pr`
  docstring), `:1527` (PR body text), `:1894`, `:1896` (`run_docs_auditor` comments),
  `:1941` (Telegram), `:1970` (findings string), `:2006`, `:2051`, `:2085` (the
  predicate), `:2095` (sweeper docstring), `:2255`, `:2258` (merge-branch logging),
  `:2276` (summary string). Sweep all of them, not just the two ranges.
- Q2 (C2): deleting the merge branch also requires removing the `prs_merged` counter —
  initialised `:2108`, incremented `:2248`, reported `:2276` — and its clause in the
  sweeper summary string. Left in place, the sweeper permanently reports
  "0 PRs auto-merged", which is both a dangling reference to a deleted mechanism and a
  `grep -c 'auto-merge'` hit that keeps the anti-criterion red.
- Q2: rewrite the two rotation Telegram messages (`:1915-1919` zero-diff-with-withheld,
  `:1936-1946` success) so neither promises auto-merge, and so the success message says
  review is required and that an unreviewed PR is closed at
  `STALE_PR_AGE_DAYS = 14` (`:75`).
- Q5 (B4): when `fixes_withheld > 0`, file **one issue per withheld entry** via
  `_file_issue_if_new` (`:1064`), titled
  `docs-auditor: withheld fix in {doc} ({old} -> {new})`. The title is the dedup key
  (`:1075-1076`), so it must be per-defect and must contain **no** volatile component —
  no age, date, count, or run id.
- Q5 (NEW-4 / R3-2): bound the withheld filing at the module's existing per-run cap of 5
  (`:1277-1289`), and log a suppression warning naming the remainder exactly as
  `:1281-1288` does. **The cap must be hoisted, not read in place.** `per_run_cap` is a
  function-local in `audit()` (`:1278`) and the withheld loop lives in `run_docs_auditor`,
  so "reuse the existing local" is not executable across the two functions. Introduce a
  single module-level `ISSUE_FILING_PER_RUN_CAP = 5` beside `STALE_PR_AGE_DAYS` (`:75`),
  derive `audit()`'s local from it
  (`per_run_cap = ISSUE_FILING_PER_RUN_CAP if scope_mode == "rotation" else 3`), and break
  the withheld loop on the constant. One source of truth; no second literal.
- Q5 (NEW-2): add `body` to the sweeper's `gh pr list --json` field set at `:2147`
  (`number,state,createdAt` → `number,state,createdAt,body`). Deleting
  `_pr_is_auto_merge_eligible` removes the only place the sweeper ever fetched a PR body
  (`:2023-2024`), and the `WITHHELD_PR_MARKER` check below has nothing to read without
  it. The test `gh` dispatcher must return `body` in its canned `pr list` payload, or the
  marker test passes vacuously.
- Q5 (B4): the sweeper must skip close **and** branch deletion (`:2263`) for any PR whose
  body contains `WITHHELD_PR_MARKER`, and file its own escalation under the **distinct**
  title `docs-auditor: withheld PR #{n} still unreviewed`. A same-title filing is a
  guaranteed no-op — `_file_issue_if_new` returns `False` on a dedup hit and never
  comments or refreshes. Keep the age out of the title and put it in the body.
- Q5 (C3): wire `output_summary` in `agent/reflection_scheduler.py:639-640` — the
  `projects_list = result.get("projects") …` / `state.mark_completed(duration,
  projects=projects_list)` pair, **not** `:514-515`, which this plan cited in error
  through round 3 (R3-4) and which is an unrelated schedule guard — guarded by
  the existing `isinstance(result, dict)` check, coerced to `str` and truncated; **and**
  add the one-line render to `ui/templates/reflections/_partials/modal_content.html`
  beside the `{% if r.last_error %}` block at `:54-56`. Without the render the value
  reaches `dashboard.json` and nothing else.

### 4. Real-git test surface

- **Task ID**: build-tests
- **Depends On**: build-escalation
- **Validates**: tests/unit/reflections/test_docs_auditor_git_surface.py (create), tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: git-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Domain**: testing — real integrations, no mocks over the subject under test
- Create `tests/unit/reflections/test_docs_auditor_git_surface.py`: real `git init`
  repo, `PROJECT_ROOT` monkeypatched, and a **synchronous** `gh`-only dispatcher via
  `monkeypatch.setattr(docs_auditor.subprocess, "run", ...)` that delegates every
  non-`gh` command to the real `subprocess.run`. Read the precedent corrections in
  **Test Impact** first — `test_merged_branch_cleanup.py` patches
  `asyncio.create_subprocess_exec` and is **not** reusable; this dispatcher has no
  in-repo precedent and must be written from scratch.
- Cover every bullet in the "New coverage required" list under **Test Impact**.
- Apply every disposition in the existing-test list under **Test Impact**. Re-derive the
  patch sites yourself — the anchors there are current as of the baseline commit only,
  and the two **direct** `_push_branch_and_pr` calls at `:1158` and `:1218` will fail
  outright rather than silently, so do not treat a green `--collect-only` as proof they
  were found.
- Recompute the `tests/README.md:272` index row from `--collect-only` after the test
  work lands. It reads `130` today; do not leave it at 130 if the count changed.
- Every run: `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh <file> -q`. Never DB 15,
  never bare pytest.

### 5. Contract documentation

- **Task ID**: document-contract
- **Depends On**: preflight
- **Assigned To**: contract-documentarian
- **Agent Type**: documentarian
- **Parallel**: true
- Runs alongside the build tasks (N3): every documentation decision is already fixed in
  this plan's text, so nothing here needs to read the finished code.
- Apply every checkbox in the **Documentation** section.
- Describe only the new status quo. No "previously", no migration notes.
- Remember `.claude/skills-global/do-docs/SKILL.md` is a hardlink; edit it in the repo.

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: build-tests, document-contract
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the **Verification** table, including the anti-criteria.
- Confirm each **Success Criteria** checkbox.
- Confirm the shared main checkout at `${AI_REPO_ROOT:-$HOME/src/ai}` is still clean and
  that no worktree other than this slug's was touched.

## Verification

All commands run from the lane worktree root unless an absolute path is given. Every
row was executed against the baseline commit while writing this refresh; the
**When** column records whether the row already holds today (a regression guard) or is a
post-build expectation that legitimately fails now.

| Check | Command | Expected | When |
|-------|---------|----------|------|
| Tests pass (substrate) | `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 | post-build |
| Tests pass (real git surface) | `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q` | exit code 0 | post-build (file does not exist yet) |
| Lint clean | `python -m ruff check .` | exit code 0 | holds now |
| Format clean | `python -m ruff format --check .` | exit code 0 | holds now |
| No `git add -A` in the auditor | `grep -c '"-A"' reflections/docs_auditor.py` | `0` | post-build (currently `1`, at `:1494`) |
| Self-commit helper gone | `grep -rh -c '_commit_current_branch' reflections/ .claude/ docs/features/ \| paste -sd+ - \| bc` | `0` | post-build (currently `2`) |
| Auto-merge predicate gone | `grep -c '_pr_is_auto_merge_eligible' reflections/docs_auditor.py` | `0` | post-build (currently `3`) |
| Sweeper never merges | `grep -c '"merge"' reflections/docs_auditor.py` | `0` | post-build (currently `1`, at `:2240`) |
| No auto-merge concept survives anywhere in the module | `grep -c 'auto-merge' reflections/docs_auditor.py` | `0` | post-build (currently **16**: module comments `:79`, `:90-91`; `_push_branch_and_pr` docstring `:1470` and PR body text `:1527`; `run_docs_auditor` comments `:1894-1896`, Telegram `:1941`, findings `:1970`; the predicate `:2006`ff. All of them go with Q2 — this row is the reason Q2 is not just a function deletion) |
| Old contract instruction gone | `grep -rn 'commits itself\|commits them itself\|self-committed' .claude/ docs/features/` | exit code 1 | post-build (currently exit 0: `SKILL.md:221`, `skill-context/do-docs.md:166`, and `skill-context/do-docs.md:152` via the third alternative). **Scope is deliberately `.claude/` and `docs/features/` only** — this plan lives in `docs/plans/` and quotes the phrase, so widening the *path* scope makes the row fail on the plan itself. Do not widen the paths. The `self-committed` alternative was added in critique round 1 (C6): without it, `skill-context/do-docs.md:152` survives a green validator |
| Rename channel stays gone | `grep -c '_git_log_follow_renames\|_detect_renamed' reflections/docs_auditor.py` | `0` | **holds now** (#2842) — regression guard only |
| No whole-tree force restore | `grep -c 'checkout", "-f\|reset", "--hard\|"clean"' reflections/docs_auditor.py` | `0` | holds now — must still hold after Q4 |
| Restore is index-safe (B3) | `grep -c 'checkout", "HEAD"' reflections/docs_auditor.py` | > 0 | post-build (currently `0`). The bare `git checkout -- <paths>` form restores from the index and re-applies the auditor's staged content on the commit-failed path; this row is the grep-level guard that `HEAD` did not get dropped |
| Vault drift stays reachable when the cap fires (B2) | Read `run_docs_auditor` and confirm the hoisted `_daily_pr_cap_reached` / `_has_open_pr_for_slug` guards appear **after** the `_run_vault_drift_detection` call (today `:1869`) and after `slug` is computed, and before the `audit()` call. Structural check: `grep -n '_run_vault_drift_detection\|_daily_pr_cap_reached\|_has_open_pr_for_slug\|slug = _path_to_slug\|result = audit' reflections/docs_auditor.py` | the line numbers appear in that order: vault-drift, `slug =`, the two guards, `audit(` | post-build (today the two guards are not in `run_docs_auditor` at all) |
| Guard-fired run still advances the rotation (NEW-1) | Real-git/Redis test: force `_has_open_pr_for_slug` true for the picked slug, run `run_docs_auditor`, assert the run returns `status="skipped"`, that `REDIS_LAST_RUN_HASH` now holds a fresh timestamp for that slug, and that a second immediately-following run picks a **different** doc. Grep-level guard that the call was not dropped: `grep -c '_update_rotation_hash' reflections/docs_auditor.py` | `status="skipped"`, the slug is stamped, the next pick differs; grep `> 2` (zero-diff path, guard path, success path) | post-build (today the guards are not in `run_docs_auditor` at all and the grep reads `3` for unrelated reasons — assert the behavior, not the count alone) |
| Guard-fired run touches no working tree (NEW-1) | Real-git test: with the daily cap set, assert `git status --porcelain` in the temp repo is byte-identical before and after the run, and that `audit()` was never called. Redis writes are expected and are **not** covered by this row | porcelain unchanged; `audit()` not called | post-build |
| Sweeper can still read a PR body (NEW-2) | `grep -c 'number,state,createdAt,body' reflections/docs_auditor.py` | > 0 | post-build (currently `0` — the sweeper's `pr list` at `:2147` omits `body`, and the only `body` fetch is inside the predicate Q2 deletes) |
| Withheld filing is flood-capped (NEW-4 / R3-3) | **Behavioral**, in `tests/unit/reflections/test_docs_auditor_git_surface.py`: drive a rotation run whose result carries **more than 5** withheld entries and assert (a) `_file_issue_if_new` is invoked exactly 5 times, (b) a `logger.warning` fires naming the number suppressed, and (c) the run's status is unaffected by the suppression | exactly 5 issues filed, one suppression warning naming the remainder | post-build. The grep-count form this row used before is retired: it claimed `grep -c 'per_run_cap'` was "currently 2" and expected `> 2`, but the real current count is **3** (`:1278`, `:1281`, `:1285`), so the row was already green today and proved nothing. Counting a symbol also cannot distinguish "both loops share one cap" from "the withheld loop declared its own"; only behavior can. Companion structural check, if a grep is still wanted: `grep -c 'ISSUE_FILING_PER_RUN_CAP' reflections/docs_auditor.py` → `> 2` (definition, `audit()` derivation, withheld loop), currently `0` |
| Dashboard renders the summary (C3) | `grep -c 'output_summary' ui/templates/reflections/_partials/modal_content.html` | > 0 | post-build (currently `0` — the value reaches `dashboard.json` and no template) |
| Issue titles carry no volatile field (B4) | Read the two title templates in the built code and confirm neither interpolates an age, date, count, or run id — only `{doc}`/`{old}`/`{new}` for the per-defect title and `#{n}` for the sweeper title | no volatile interpolation | post-build |
| New commit ownership declared | `grep -c 'files_touched' .claude/skill-context/do-docs.md` | > 0 | post-build (currently `0`) |
| Scheduler wires the summary | `grep -c 'output_summary' agent/reflection_scheduler.py` | > 0 | post-build (currently `0`) |
| Git surface is never blanket-mocked | `grep -c 'patch("subprocess.run"\|patch.object(subprocess' tests/unit/reflections/test_docs_auditor_git_surface.py` | `0` | post-build |
| Real git in the new test file | `grep -c '"init"' tests/unit/reflections/test_docs_auditor_git_surface.py` | > 0 | post-build |
| Foreign dirt survives (Race 1) | `grep -c 'foreign\|unrelated_dirty' tests/unit/reflections/test_docs_auditor_git_surface.py` | > 0 | post-build |
| No stale xfails | `grep -rn 'xfail' tests/unit/reflections/test_docs_auditor_git_surface.py` | exit code 1 | post-build |
| Test count row is recomputed | `POPOTO_TEST_DB=13 .venv/bin/python -m pytest tests/unit/test_docs_auditor_substrate.py --collect-only -q \| tail -1` and compare against `grep -n 'test_docs_auditor_substrate' tests/README.md` | the number in `tests/README.md:272` equals the collected count | post-build (both read `130` today, and the count will move) |
| Shared checkout clean on the auditor's write surface after run (R3-1) | `test -z "$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" status --porcelain -- docs .claude)"` | exit code 0 | holds now — must still hold at the end. Scope matches the Prerequisites row exactly and is deliberate: the auditor's write surface is the primary doc's neighborhood (`_resolve_neighborhood:259`), which spans `docs/` and outbound-linked `.md` paths, **not** `docs/features/` alone — a `docs/features`-only check would pass over a wedge whose leftover dirt landed in `docs/plans/`, `docs/sdlc/`, or `.claude/`. Foreign dirt outside `docs/` and `.claude/` is preserved by design and is not asserted here |

## Critique Results

**Round 1 — 2026-08-18, against commit `81a0c6cf1` (the post-#2842 refresh). Verdict: NEEDS REVISION, 4 blockers / 7 concerns.**

The critic re-derived every re-anchored `file:line` independently and found no anchor error in either the module or the test file; the 16 auto-merge hits, the 130/113 test counts, and the `enabled: false` reading were all confirmed exact. Three of the four blockers are latent defects in the *original* plan that the refresh faithfully preserved, not refresh errors.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Prerequisites auditor | B1: 5 of 7 Prerequisites rows are exit-code-vacuous. `scripts/check_prerequisites.py:94-106` judges purely on return code, so `gh pr view … -q .state`, the `print(bool(...))` rotation probe, `git status --porcelain`, and the PR-count row all exit 0 regardless of result. The two rows that matter most (no rotation in flight, clean shared checkout) are the fail-open ones, which makes Risk 3's stated mitigation inert. | Q4 / Prerequisites | Rewrite each as an exit-code assertion, pipe-free (`check_prerequisites.py:62` splits cells on a bare `\|`): `test "$(gh pr view 2728 --json state -q .state)" = MERGED`; `sys.exit(1 if _get_redis().exists(REDIS_RUNNING_KEY) else 0)`; `test -z "$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" status --porcelain)"`. `/bin/sh` POSIX only — no `[[ ]]`. |
| BLOCKER | Control-flow critic | B2: hoisting `_daily_pr_cap_reached` to the preflight block at `:1858` returns before `_run_vault_drift_detection` at `:1866-1869`, whose own comment declares it runs "unconditionally, NOT gated behind the `_select_primary_doc` pick". Skipping `audit()` also skips the substrate's advisory `_file_issue_if_new` loop at `:1290`. A plan whose thesis is "declining must be loud" would silently narrow two reporting channels. | Q4 item 1 / Data Flow / Task 1 | Place both hoisted guards after vault-drift detection and after `slug` is computed (`:1881`), immediately before `audit()` at `:1884` — still pre-write. Then rule explicitly on the advisory channel: accept the narrowing in writing, or run `audit(..., apply_mode="dry-run")` on the capped path. Add a Verification row asserting vault-drift stays reachable when the cap is set. |
| BLOCKER | Shared-checkout safety critic | B3: the prescribed `git checkout -- <files_touched>` restores from the **index**, not HEAD. On the reachable staged-then-commit-failed path (`add` succeeds, `commit` raises, `checkout main` carries the staged content over), the restore rewrites the worktree from the index that still holds the auditor's content — leaving staged auditor edits on `main` in the shared checkout. That is the exact wedge this plan exists to remove, and the plan forbids `reset --hard` / `checkout -f` as the remedy. | Q4 item 2 / Race 1 / Task 1 | Prescribe `git checkout HEAD -- <files_touched>` (or `git restore --source=HEAD --staged --worktree --`). Both reset index and worktree for those paths only, so "foreign dirt survives" is unaffected. Update Q4 item 2, Race 1's mitigation, and the Task 1 bullet in one pass so they cannot drift. Add a real-git test that stages `files_touched`, forces `commit` to fail, and asserts neither column of `git status --porcelain` shows those paths. |
| BLOCKER | Escalation critic | B4: Q5's sweeper-side escalation is a guaranteed no-op. `_file_issue_if_new` (`:1072-1096`) dedups by title with a 30-day Redis key plus `_open_issue_exists`, and returns `False` on a hit — it never refreshes, comments, or bumps. Q5 item 1 claims the same title on day 0, so the day-14 sweeper filing can never fire. Separately, the issue-title template is unspecified, which is the single most load-bearing decision in Q5: a generic title swallows every distinct withhold for 30 days. | Q5 items 1-2 / Task 2 | Mandate a per-defect title for item 1 (e.g. `docs-auditor: withheld fix in {doc} ({old} → {new})`) so dedup is per-defect, not per-run. Give the sweeper a distinct title (e.g. `docs-auditor: withheld PR #{n} unreviewed at {age}d`) or drop the sweeper filing and say so. Correct the phrase "or refreshes, via dedup" to "or is suppressed by the 30-day dedup". Note `_file_issue_if_new` hardcodes `--label documentation` (`:1115-1116`). |
| CONCERN | Appetite critic | C1: Medium is the wrong appetite. Inventory: a deleted helper, a changed signature, an ~80-line predicate deletion, a guard hoist plus restore rewrite in a live shared-checkout writer, a sweeper branch deletion, a cross-cutting scheduler change affecting every function reflection, 2 skill bodies + 1 skill-context, ~8 doc passages, 14 existing test dispositions (two of which break at import), and a from-scratch synchronous `gh` dispatcher the plan itself says has no in-repo precedent. Critic proposed a three-lane split: Lane A = Q1, Lane B = Q3+Q4, Lane C = Q2+Q5. | Appetite / Step by Step Tasks | Lane stays single per the fleet assignment; resize the appetite to Large and sequence the tasks so each Q-group lands as an independently reviewable commit, preserving the critic's isolation intent without fragmenting the lane. The three-way split remains available to the coordinator as an override. |
| CONCERN | Completeness critic | C2: Task 2 names only the predicate (`:2005-2086`) and the sweeper branch (`:2235-2258`), but its own Verification row demands `grep -c 'auto-merge'` → 0 against 16 hits. Outside both ranges: module comments `:79`, `:90-91`; `_push_branch_and_pr` docstring `:1470`; PR body text `:1527`; `run_docs_auditor` comments `:1894-1896`; findings string `:1970`; sweeper docstring `:2095`; summary string `:2276`. The builder would hit a red validator with no instruction where to look. | Task 2 | List all 16 anchors in Task 2. State that deleting the merge branch also requires removing the `prs_merged` counter and its clause at `:2274-2277`, or the sweeper permanently reports "0 PRs auto-merged". |
| CONCERN | Evidence critic | C3: "the dashboard already renders it" is false. `models/reflection.py` and `ui/data/reflections.py` are exactly as cited, but no template in `ui/templates/` references `output_summary` or `last_run_summary`. The value reaches `dashboard.json` (`ui/app.py:931`) and nothing else — which weakens the argument that dismisses `_write_liveness` for having no reader while promoting a JSON key with no rendered reader. | Q5 item 3 / spike-4 / Success Criteria | Restate as "reaches `dashboard.json` via `get_all_reflections()`". Either add a one-line render of `last_run_summary.output_summary` to `modal_content.html` as part of Q5, or drop the "on the dashboard" phrasing from the success criterion. |
| CONCERN | Citation-rot critic | C4: Q2 cites `sdlc_progress.py:116`'s `_SDLC_BRANCH_RE` as the reason nothing auto-merges a `docs-audit/*` PR. That symbol was deleted by `docs/plans/completed/sdlc-progress-lane-discovery-branch-shape.md` and appears nowhere today. The conclusion still holds, but via a deliberately *widened* discovery shape, so the narrow-regex citation is exactly the kind of evidence that rots. | Q2 | Cite `reflections/sdlc_progress.py:248` (`_list_open_lane_prs`) and `:315` (`_slug_from_branch`), and state the boundary as "the `session/` namespace, which `docs-audit/*` is not in". |
| CONCERN | Safety-claim critic | C5: Race 1 asserts flatly "Foreign dirt is preserved by design", but the scoped restore discards `files_touched`. If a concurrent lane writes to a file the auditor also touched — inside the window between the dirty-tree guard at `:1858` and the failure — that lane's uncommitted edit is destroyed. The regression test seeds a file "the auditor never touches", so the overlap case is neither covered nor acknowledged. | Race 1 / Q4 item 3 | Narrow the claim to "foreign dirt **outside `files_touched`** is preserved" and record the overlap as an accepted residual with its bounding argument (dirty-tree preflight + `REDIS_RUNNING_KEY` serialization). Optionally skip-and-report any `files_touched` path whose on-disk content no longer matches what `_apply_fixes_to_file` wrote. |
| CONCERN | Anchor critic | C6: the skill-context descriptive claim is at `.claude/skill-context/do-docs.md:142-144`, not `:144-146` as the plan states twice. Separately `:152` reads "Before trusting the substrate's **self-committed** output:" — a third stale-contract string that is in neither Q1's edit list nor the Documentation checkboxes, and that the anti-criterion grep (`commits itself\|commits them itself`) does not catch, so it survives a green validator. | Q1 item 3 / Documentation / Verification | Correct the anchors to `:142-144`; add `:152` to Q1 item 3 and to the Documentation checkbox; widen the anti-criterion to also match `self-committed`, keeping the deliberate `.claude/` + `docs/features/` scope so the row never fires on this plan document. |
| CONCERN | Cross-reference critic | C7: `:870` ("Race 1 / B4") and `:1114` ("(B3)") reference B-numbered findings that exist nowhere in the document, against a then-empty Critique Results table. Residue from a prior round the refresh did not sweep; a builder reading "the regression test for B4" has nothing to look up. | Test Impact / Risks | `:870` → "(Race 1)". `:1114` → drop the "(B3)" marker; the sentence stands alone. |
| NIT | Consistency critic | N1: `## Appetite` says "Team: Solo dev, PM, code reviewer" while `## Team Orchestration` names four agents, and "PM check-ins: 1" against three Open Questions, one of which explicitly says "Owner call". | Appetite | Align the roster line with the four agents and say 2 check-ins, or state that OQ3 defers to post-merge. |
| NIT | Evidence critic | N2: `## Update System` says a red substrate suite means "`/update` will fail on every machine". The implementation (`scripts/update/deps.py:426-438`) runs only inside the `anthropic`/`claude-agent-sdk` auto-bump path, which only the lockfile-maintainer machine runs. | Update System | Cite `scripts/update/deps.py:426-438` and soften to "blocks the dependency auto-bump on the lockfile-maintainer machine". |
| NIT | Parallelism critic | N3: `build-escalation` depends on `build-substrate` though Q2/Q5 touch `run_docs_branch_sweeper` and `agent/reflection_scheduler.py`, disjoint from Q1/Q3/Q4; `document-contract` depends on `build-escalation` though every documentation decision is already fixed in the plan text. | Step by Step Tasks | Make `build-escalation` depend on `preflight` with `Parallel: true`, and `document-contract` depend on `preflight`. |

**Round 2 — 2026-08-18, against commit `95a12aff1`. Verdict: NEEDS REVISION, 1 blocker / 3 concerns / 3 nits.**

Round 1's dispositions, re-verified independently against the source: **B1, B3, B4, C1-C7, N1, N2 all CLOSED. B2 partially closed** (the vault-drift half is genuinely fixed; its bounding argument is not). **N3's pushback was verified correct** — `run_docs_auditor` spans `:1818-1997` and the Q2 Telegram strings at `:1915-1919`/`:1936-1946` sit inside the same function task 2 restructures, so Q2/Q5 are not disjoint from Q1/Q3/Q4 and parallelizing would have produced conflicting edits to one function. **B4's and B2's dry-run pushbacks were also verified correct**: `reflections/docs_auditor.py:1279` gates the advisory loop on `apply_mode == "apply" and scope_mode == "rotation"`, so a dry-run pass would indeed file nothing.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Liveness critic | NEW-1: a guard-skipped rotation never stamps the rotation hash, so the hoisted open-PR guard pins the rotation on one slug indefinitely. `_select_primary_doc` (`:1344-1383`) picks the least-recently-audited doc from `REDIS_LAST_RUN_HASH`. Today `_update_rotation_hash` at `:1950` is unconditional, so rotation advances past a blocked slug. This plan makes it fire only on `ok` (Task 2) and makes a fired guard return `skipped` with zero writes (Data Flow step 7), so the same slug is re-picked every day for as long as the guard fires. For the daily cap that self-clears next day; for `_has_open_pr_for_slug` it lasts the PR's lifetime — and **forever** for a withheld PR, since Q5 item 2 mandates the sweeper never close a `WITHHELD_PR_MARKER` PR and OQ2 chooses to leave it open. Net: one immortal withheld PR becomes a permanent, silent rotation shutdown that reports `skipped`, not `error` — the exact failure class this plan exists to eliminate. | Q4 item 1 / Data Flow step 7 / Task 2 | A guard-fired `skipped` run MUST still call `_update_rotation_hash(project_key, [str(primary)])` for the picked doc, so rotation advances past a blocked slug. The zero-diff path at `:1912` already does exactly this — follow it. Correct Q4 item 1's bound to: "≤1 day for the daily cap; the open-PR guard defers *this doc* for the PR's lifetime, and rotation continues on other docs because the hash is stamped." Add a Verification row: with `_has_open_pr_for_slug` true, assert the rotation hash for the picked slug is written and the next run picks a different doc. |
| CONCERN | Interface critic | NEW-2: deleting `_pr_is_auto_merge_eligible` removes the sweeper's only access to a PR body, which Q5 item 2 then requires. The sweeper's PR query at `:2147-2148` requests only `number,state,createdAt`; the `body` fetch lives at `:2023-2024`, inside the predicate Q2 deletes. Q5 item 2 and Task 3 both assume the `WITHHELD_PR_MARKER` check is available. | Task 3 / Q5 item 2 | State in Task 3 that `body` must be added to the sweeper's `gh pr list --json` field set at `:2147`, and that the test `gh` dispatcher must return it. |
| CONCERN | Prerequisites critic | NEW-3: the "shared main checkout clean" row (`test -z "$(git status --porcelain)"`) contradicts the plan's own foreign-dirt model and is practically unsatisfiable. The plan states elsewhere that other lanes routinely hold uncommitted work in this checkout, and No-Gos forbids the builder from clearing it — so the builder gets a FAIL they are not permitted to fix. It already failed for exactly this reason during the round-1 revision. | Prerequisites | Scope the row to the auditor's own write surface (`git -C … status --porcelain -- docs/features`) or mark it advisory with an explicit "foreign dirt outside `docs/features/` does not block" note. |
| CONCERN | Flood critic | NEW-4: Q5 item 1 files one issue per withheld entry with no per-run bound, while the module's own advisory loop deliberately caps at 5 with the comment "Hard per-run cap prevents flood" (`:1277-1289`). | Q5 item 1 | Reuse the same per-run cap, or state an explicit bound and why none is needed. |
| NIT | Consistency critic | NEW-5: the anti-criterion's scope is stated three ways. Risk 1 says the phrase must appear "nowhere in the repo" and Success Criteria says "survives anywhere", but the Verification row deliberately scopes to `.claude/` + `docs/features/`. A literal repo-wide reading hits this plan and `docs/plans/completed/docs-auditor-rename-guard.md:47,408`. | Risk 1 / Success Criteria | Align both prose sites to the scoped wording the Verification row uses. |
| NIT | Anchor critic | NEW-6: the advisory-filing gate is cited as `:1278` in two places; `:1278` is `per_run_cap = …` and the gate is `:1279`. | Q4 / Task 2 | Correct both anchors to `:1279`. |
| NIT | Prerequisites critic | NEW-7 (unverified): `--search "head:docs-audit"` may not match branches named `docs-audit/{slug}-{ts}` — GitHub's `head:` qualifier is not documented as a prefix match. The row is fail-closed on `gh` errors but potentially fail-open on the condition it exists to catch. | Prerequisites | Confirm at preflight, or switch to `gh pr list --state open --json headRefName -q '[.[]\|select(.headRefName\|startswith("docs-audit/"))]\|length'` (pipe-free at the cell level; the pipes are inside the jq string). |

**Round 3 — 2026-08-18, against commit `e9471f286`. Verdict: READY TO BUILD (WITH CONCERNS), 0 blockers / 4 concerns. TERMINAL ROUND.**

NEW-1 is **closed and the fix was traced end to end**: `_update_rotation_hash` (`:1608-1620`) keys entries by `_path_to_slug(p)` with `str(time.time())`, and `_select_primary_doc` (`:1379-1382`) sorts ascending on `last_run.get(_path_to_slug(path), 0.0)` — so a guard-path stamp writes exactly the field the selector reads, with a maximal timestamp, pushing that doc to the end of the sort. The next run genuinely picks a different doc. Both `project_key` and `primary` are in scope at the prescribed guard site. All five "zero writes" sites were checked for mutual consistency and no unqualified occurrence survives. NEW-2, NEW-4, NEW-5, NEW-6, NEW-7 all closed; NEW-7's pushback was independently upheld and strengthened (round 2's jq remedy was not merely parser-breaking but also exit-code-vacuous, reintroducing round 1's B1 defect). NEW-3 is partial.

**The four concerns below are ACCEPTED RESIDUALS carried into BUILD, not blockers.** The builder must read this block and apply the inline corrections; none requires re-planning.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Write-surface critic | R3-1 (NEW-3 partial): the rescoped prerequisite is satisfiable but its *justification* is wrong. `docs/features/` is where rotation picks its **primary**, not the module's write surface. `audit(scope_mode="rotation")` audits `_resolve_neighborhood(primary, root, …)` (`:1220`), which (a) follows outbound `[..](*.md)` links with **no `docs/plans/` exclusion** on that branch and (b) pulls inbound refs from `grep -rln <name> docs/`; `_apply_fixes_to_file` gates only on `.endswith(".md")` (`:1251`), not on a directory. Real targets today include `docs/plans/*.md`, `docs/sdlc/*.md`, `docs/tools-reference.md`, and `.claude/skills-global/do-plan/DOMAIN_FRAMING.md`. So a wedge whose leftover dirt sits outside `docs/features/` passes both the prerequisite and the final "shared checkout clean after run" row. | Prerequisites / Verification | Blast radius is contained — nothing in Q3/Q4 depends on the claim, because staging and restore are both `files_touched`-scoped and path-agnostic. At build time either widen to `test -z "$(git -C "${AI_REPO_ROOT:-$HOME/src/ai}" status --porcelain -- docs .claude)"`, or knowingly accept partial detection. Replace the "docs/features is the auditor's only write surface" sentence with "the write surface is the primary doc's neighborhood (`_resolve_neighborhood:259`), spanning `docs/` and outbound-linked `.md` paths". |
| CONCERN | Executability critic | R3-2: "reuse the module's existing `per_run_cap`" is not literally executable as written. `per_run_cap` is a **function-local** in `audit()` (`:1278`); the withheld filing Q5 item 1 adds lives in `run_docs_auditor`, a different function. | Q5 item 1 / Task 3 | Hoist it to a single module-level constant consumed by both loops. That is the reuse the plan intends and is still not the "separate new constant" it forbids. |
| CONCERN | Verification-quality critic | R3-3: the NEW-4 Verification row states `grep -c 'per_run_cap'` is "currently 2" and expects `> 2`. The real current count is **3** (`:1278`, `:1281`, `:1285`), so the row is already green today and proves nothing post-build. | Verification | Change the expectation to `> 3`, or better, assert the behavior (a run with >5 withheld entries files exactly 5 issues and logs the suppression warning) rather than a grep count. |
| CONCERN | Anchor-rot critic | R3-4: `agent/reflection_scheduler.py:514-515` is wrong in four live citations (Freshness Check, spike-4, Q5 item 3, Task 3). That range is docstring text inside `is_reflection_due`; the real site is `:639-640` (`projects_list = result.get("projects") if isinstance(result, dict) else None` / `state.mark_completed(duration, projects=projects_list)`). The *claim* is correct — only `projects` is read — and preflight mandates re-grepping, so this is anchor rot rather than misdirection. The round-1 C3 row keeps the old anchor as history and must not be edited. | Q5 item 3 / Task 3 | Correct the four live citations to `:639-640` at build time. |

Two further build-time notes from the terminal round, neither a finding:
- **Prerequisite row 6 needs `--limit 200`.** `gh pr list` defaults to `--limit 30`, newest-first, so with more than 30 open PRs an older `docs-audit/*` PR is invisible — a narrow fail-open.
- **NEW-1's stated bound is optimistic in the builder's favour.** Stamping a doc that was never audited pushes it to the back of a queue of every `docs/features/*.md`, so the deferral is "until its next turn in the rotation", not "the PR's lifetime". The design choice is still correct; read the bound that way rather than restating it.
- **Unspecified but harmless:** whether the guard-fired `skipped` path also calls `_write_liveness(slug, "skipped", …)` as the zero-diff path does at `:1913`. Either choice satisfies the plan's invariants — pick one at build time and note it.

---

## Open Questions

> **Resolved and removed in the 2026-08-18 refresh.** The former Q1 ("confirm the
> rename-channel deletion") is answered: #2741 ruled *delete* and PR #2842 landed it as
> `a9205b065`. The former Q5 ("accept README index line deletion?") is moot:
> `_detect_readme_broken_entries` was deleted entirely by #2842 rather than kept with
> its rename branch removed, so the behavior change it asked about cannot occur.

1. **Q2 — confirm unreviewed auto-merge dies.** The predicate is inverted today
   (any review disqualifies), and repairing it yields "merge approved PRs", which
   `/do-merge` already does. This plan deletes it. The cost is that docs PRs now
   need a human or `/do-pr-review`, bounded to 1 new PR per calendar day. Acceptable?
2. **Q5 — is "leave the withheld PR open forever plus a deduped issue" the right
   stopping condition?** The alternative is closing it without `--delete-branch` so
   the branch survives for recovery. Leaving it open keeps the fixes one click from
   merging; closing it keeps the PR list tidy. This plan chooses leaving it open,
   because an open PR with an open issue is the loudest available signal and the
   silent loop is the actual defect.
3. **Should the `docs-auditor` rotation reflection be re-enabled at all?** The question
   has sharpened since this plan was written: on this machine the deployed
   `config/reflections.yaml:205` already carries `enabled: false` for `docs-auditor`,
   so Caller A is **not currently running**. That does not invalidate any of Q2-Q5 —
   Caller B (the `/do-docs` cascade, Q1) runs on every PR and is the larger blast
   radius, and the rotation code must be correct before it is switched back on — but it
   does mean this lane's rotation work is a prerequisite for re-enabling rather than a
   fix to something live. With auto-merge gone, every rotation PR would need a human to
   act on a Telegram notification or it is closed unmerged at 14 days. Owner call on
   whether to re-enable after merge; not a blocker for the build. Note that
   `config/reflections.yaml` is gitignored and per-machine, so this cannot be settled by
   a repo change.
