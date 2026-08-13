---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2744
last_comment_id: 5277752954
revision_applied: true
revision_applied_at: 2026-08-13T08:47:47Z
---

# Docs Auditor: Migration-Context Hatch and Bare-Path Existence Invariant

Combined lane for **#2744** (the `migration_context` escape hatch never fires on the
corpus's dominant phrasing) and **#2759** (`_PATH_REF_RE` cannot see bare filenames, so
the #2711 corruption class survives for unprefixed names). Both defects live in the same
~50 lines of `reflections/docs_auditor.py:497-547`, both are the "autonomous writer makes
a doc false" class, and fixing one without the other leaves the same PR half-safe.

## Problem

`reflections/docs_auditor.py` rewrites documentation automatically, on a daily rotation,
with no human between generation and `main`. Two independent holes in its guards let it
write false statements today.

**Current behavior:**

**Hole 1 — the migration-context hatch is dead on the dominant phrasing (#2744).**
`_detect_stale_term_fixes` (`:497-530`) rewrites every occurrence of a `STALE_TERMS` key
unless the document carries "migration context". That hatch (`:520-527`) is six bare
substring tests:

```python
migration_context = (
    f"renamed to {new_term}" in content
    or f"replaced by {new_term}" in content
    or f"now {new_term}" in content
    or f"formerly {old_term}" in content
    or f"Replaces {old_term}" in content
    or f"replaces {old_term}" in content
)
```

The corpus writes the terms backticked, so `formerly RedisJob` never matches the actual
text ``formerly `RedisJob` ``. Executed against `main` today, three live docs still queue
rewrites totalling 17 term occurrences:

| Doc | Terms it would rewrite |
|---|---|
| `docs/features/popoto-redis-expansion.md` | `SessionLog`, `RedisJob` |
| `docs/guides/agent-session-migration-audit.md` | `SessionLog`, `RedisJob`, `session_log` |
| `docs/guides/summarizer-output-audit.md` | `SessionLog`, `RedisJob` |

Each rewrite turns correct migration prose into a sentence saying the new name was
formerly itself. All of it ships with `fixes_withheld = 0`, so `_pr_is_auto_merge_eligible`
(`:1884`) clears it and `run_docs_branch_sweeper` can merge it unread.

The worst instance is not the one the issue named. Both `models/session_log.py` **and**
`models/agent_session.py` exist on disk, so `\bsession_log\b` → `agent_session` rewrites
`` `models/session_log.py` `` → `` `models/agent_session.py` `` in
`docs/guides/agent-session-migration-audit.md:22` and `:75`. The #2728 existence invariant
does **not** withhold it — the target exists — so a doc that documents the backward-compat
shim's location becomes false, silently, through a hole no existing guard covers.

**Hole 2 — the existence invariant cannot see bare filenames (#2759).**
`_PATH_REF_RE` (`:534`) is `(?:[\w.-]+/)+[\w.-]+\.(?:py|md)`. The `+` requires at least one
directory segment:

```
agent/session_logs.py   -> matched   (existence-checked)
docs/features/x.md      -> matched   (existence-checked)
agent_session.py        -> NOT matched  (invisible)
README.md               -> NOT matched  (invisible)
```

`_absent_new_path_refs` (`:537-547`) therefore never validates a substitution whose before
and after are both bare names. That is exactly the #2711 corruption shape
(`session_logs.py` → `agent_sessions.py`) minus a directory prefix, and #2711 was a real,
committed corruption (`d7bf3ad99`).

**Desired outcome:**

The auditor stops rewriting correct migration prose into false statements, and its
existence invariant covers bare filenames with a defined, tested meaning of "exists" —
without materially raising the withheld-fix rate, because a withheld fix today has no
escalation path (#2729) and gets stale-closed at day 14.

## Freshness Check

**Baseline commit:** `0f08cc48b13c0fd6c2529ec61bb85b0bc37671e7` (main)
**Issues filed at:** #2744 `2026-08-13T04:11:06Z`, #2759 `2026-08-13T05:34:07Z`
**Disposition:** **Minor drift + Overlap** — every structural claim holds and no line
numbers drifted, but #2744's evidence table is partially wrong and #2759 cites an issue
state that has since changed. One active plan overlaps the same file.

**File:line references re-verified** (read directly from `reflections/docs_auditor.py` at
the baseline commit):

| Reference | Issue claim | Status |
|---|---|---|
| `:497-530` | `_detect_stale_term_fixes` | Holds |
| `:520-527` | six-arm `migration_context` hatch, verbatim | **Holds, no drift** |
| `:534` | `_PATH_REF_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.(?:py|md)")` | **Holds, no drift** |
| `:537-547` | `_absent_new_path_refs`, additive-only | Holds |
| `:454` | `` r"`((?:[\w.-]+/)+[\w.-]+\.py)`" `` in `_detect_renamed_symbol_fixes` | Holds |
| `:761` | same pattern in `_detect_deleted_target_issues` | Holds |
| `:689-747` | `_build_line_context` / `_is_documented_deletion` | Holds — reusable, see Technical Approach |
| `:1884-1932` | `_pr_is_auto_merge_eligible`, `WITHHELD_PR_MARKER` disqualification | Holds |

**Bug reproduced against current main.** `_detect_stale_term_fixes` was executed on the
three named docs and produced the fix set in the table above. `_PATH_REF_RE` was executed
on the four sample paths and reproduced #2759's visibility table exactly.

**Corrections to #2744's evidence table** (recorded on the issue as Recon → Revised):

- `docs/features/popoto-redis-expansion.md:11` — the claim **holds**. But the same doc's
  `:7` (*"replacing both the earlier `SessionLog` and `RedisJob` models"*) matches **no
  arm even after backtick normalization**, so normalization alone does not clean this doc.
- `docs/guides/agent-session-migration-audit.md:17` — the claim **does not hold**. The
  `formerly` on that line refers to `` `bridge/summarizer.py` ``, which is not a
  `STALE_TERMS` key. No `formerly SessionLog` / `formerly RedisJob` exists anywhere in
  that doc. Backtick normalization changes nothing for it. Its real migration prose is
  *alias* form (`` `SessionLog = AgentSession` ``) and *"the old `RedisJob` + `SessionLog`
  split"*.
- `docs/guides/summarizer-output-audit.md` — its migration prose is *arrow* form
  (`SessionLog → AgentSession`); its `RedisJob` hits are **quoted historical transcript**,
  a class no migration phrasing will ever cover.

Consequence: **#2744's suggested fix is necessary but not sufficient.** The plan below
does more than backtick-normalize, and says why.

**Cited sibling issues/PRs re-checked:**

- **#2711** — CLOSED. The corruption both issues descend from.
- **#2728** — MERGED as `45d0961f9`. Added word-anchoring and the existence invariant.
  Everything this plan changes sits on top of it.
- **#2725** — OPEN. Rename detectors pick the wrong target. Adjacent, not in scope.
- **#2726** — **CLOSED `2026-08-13T08:03:18Z` as `NOT_PLANNED`, duplicate of #2739.**
  #2759's body says "#2726 is still open". The *premise* still holds (nothing has shipped;
  the auditor is still its own committer) but ownership moved to #2739.
- **#2729** — OPEN. Withheld PRs are auto-merge-ineligible forever and stale-close at day
  14. This is #2759's stated sequencing concern; see the ruling in Technical Approach.

**Commits on main since the issues were filed (touching referenced files):**
`45d0961f9` (PR #2728) is the only commit to `reflections/docs_auditor.py`, and both
issues were filed *after* it and are explicitly written against post-#2728 code. Nothing
has touched `reflections/` or `tests/` since. **No drift.**

**Active plans in `docs/plans/` overlapping this area:**

- **`docs/plans/docs-auditor-review-gate.md`** (#2739, `status: Planning`, not built) —
  **Overlap, not a dependency.** It edits the same file in disjoint regions
  (`_commit_current_branch` `:1192`, `_push_branch_and_pr` `:1338`,
  `_pr_is_auto_merge_eligible` `:1884`, sweeper `:1968+`); this plan is confined to
  `:497-547`. It closes #2725/#2726/**#2729** — i.e. it owns the withheld-fix escalation
  path this plan's #2759 half depends on. Whichever lands second rebases; the textual
  conflict surface is near zero.
- `docs/plans/agent_wiki.md` and `docs/plans/sdlc-1249.md` mention `docs_auditor` but do
  not modify the detector block.

## Prior Art

- **PR #2728** (`45d0961f9`, merged) — *word-anchor stale terms and enforce a
  path-existence invariant*. Word-anchored `STALE_TERMS` with `\b` and added
  `_absent_new_path_refs` + `WITHHELD_PR_MARKER`. **Succeeded at what it scoped**, and both
  issues in this lane are holes it explicitly did not claim to close: its docstring at
  `:497-513` says out loud that word-anchoring "stops short of *never rewrites a path*"
  and that only the existence invariant catches the remainder. #2744 shows the hatch never
  fires; #2759 shows the invariant has a blind spot. **This is the follow-through on
  #2728, not a repeat of it.**
- **#2711 / `d7bf3ad99`** — the live corruption: an invented rename applied five times and
  committed. The reference case for why an unreviewed writer needs fail-closed guards.
- **#2739 / `docs/plans/docs-auditor-review-gate.md`** — the *structural* answer (a review
  gate in front of every write). This lane is the *generator* answer. They are complements.
- **`tests/unit/test_docs_auditor_substrate.py`** (1483 lines) — the existing harness:
  `TestStaleTermDictionary` (`:597`), `TestStaleTermWordBoundary` (`:629`),
  `TestExistenceInvariant` (`:669`). Every fixture in the last uses `dir/file.py`-shaped
  paths, and `TestStaleTermDictionary::test_migration_context_skips_fix` (`:603`) asserts
  only the *un-backticked* form. **Both bugs shipped with green tests because the tests
  encode the same blind spots as the code.** That is the pattern to break.
- No prior attempt to fix either hole exists. No **Why Previous Fixes Failed** section.

## Research

No relevant external findings — purely internal work (Python `re`, `git ls-files`, no new
libraries, no external APIs). Phase 0.7 skipped per the skill's own skip condition.

## Spike Results

### spike-1: Does backtick normalization alone fix the three named docs?
- **Assumption**: "#2744's suggested fix (normalize backticks + case) eliminates the false
  rewrites in all three named docs."
- **Method**: prototype — ran candidate normalizers against the live corpus.
- **Finding**: **False.** Backtick normalization alone leaves roughly 9 of 17 occurrences
  still rewritten. It fixes `RedisJob` in `popoto-redis-expansion.md` and nothing in
  `agent-session-migration-audit.md`.
- **Confidence**: high (executed, not reasoned).
- **Impact on plan**: the hatch needs a consolidated *cue set*, not just normalization.

### spike-2: Document-scoped vs. line-scoped exemption
- **Assumption**: "Making the hatch occurrence-scoped is more precise than the current
  whole-document scope."
- **Method**: prototype — both scopes run over the three docs.
- **Finding**: **Line-scoped is strictly worse.** It re-exposes 8 occurrences that the
  document-scoped rule correctly exempts (e.g. `agent-session-migration-audit.md:75`,
  `summarizer-output-audit.md:68-74`), because migration context in real prose sits in a
  *different* sentence from the term.
- **Confidence**: high.
- **Impact on plan**: **keep the document scope.** Recorded as an explicit ruling so the
  build does not "improve" it into a regression.

### spike-3: Residual after each layer of the proposed fix
- **Assumption**: "A combination of normalization + cue set + existing context machinery +
  path-token suppression drives the false rewrites to (near) zero."
- **Method**: prototype — cumulative measurement over the three docs, 17 occurrences.
- **Finding**:

  | Layer | Occurrences still rewritten |
  |---|---|
  | Today (main) | 17 |
  | + normalized, case-insensitive, doc-scoped cue set | 7 |
  | + reuse of `_is_documented_deletion` (`:721`) for stale terms | 3 |
  | + suppress matches inside a path-shaped token | **1** |

  The final residual is `docs/guides/summarizer-output-audit.md:68`
  (*"Example 3 (10:52, fixing RedisJob refs)"*) — a quoted transcript line with no
  migration cue and no path token.
- **Confidence**: high.
- **Impact on plan**: all four layers are in scope; the single residual is accepted and
  recorded as a No-Go with a filed issue rather than chased.

### spike-4: Blast radius of widening `_PATH_REF_RE` from `+` to `*`
- **Assumption**: "Widening the invariant raises the withheld-fix rate, which #2729 makes
  costly."
- **Method**: prototype — widened pattern run over every `docs/**/*.md` except `docs/plans/`.
- **Finding**: **False, and measurably so.** 1557 bare refs become newly *visible*
  (1202 resolve to exactly one repo path, 218 are ambiguous, 137 resolve to nothing).
  But `_absent_new_path_refs` validates only `findall(candidate) - original_refs`, and
  `original_refs` is computed with the *same* pattern — so widening widens both sides
  symmetrically and **all 1557 are pre-existing and never re-validated.** The measured
  new-withhold count on today's corpus is **zero**. Withholds appear only when the auditor
  tries to *write* a bare name that exists nowhere, which is the #2711 class itself.
- **Confidence**: high.
- **Impact on plan**: **#2759 does not need to wait for #2729.** This is recorded as the
  ruling on the issue's sequencing question, and re-asserted as a Verification row.

### spike-5: What "exists" should mean for an unanchored bare name
- **Assumption**: "A repo-wide glob per candidate ref is affordable."
- **Method**: code-read + prototype.
- **Finding**: `Path.rglob` per ref is not affordable and would walk `.venv/`,
  `.worktrees/`, `node_modules/`. `git ls-files --cached --others --exclude-standard` is
  one subprocess, respects `.gitignore` for free, and covers untracked-but-not-ignored
  files (which matters because a doc may reference a file added in the same change). A
  basename index built once per `audit()` run is O(1) per lookup. The repo has 108
  basenames with more than one owner.
- **Confidence**: high.
- **Impact on plan**: existence oracle = doc-relative resolution first, then a cached
  `git ls-files` basename index.

## Data Flow

The rewrite path both bugs sit on, end to end:

1. **Entry**: `run_docs_auditor()` (`:1697`, daily reflection) or `audit(scope_mode="pr-changed-files")`
   (`:1041`, the `/do-docs` stage).
2. **Scope**: `_resolve_neighborhood` (`:259`) or `_resolve_pr_changed_files` (`:325`)
   yields up to `NEIGHBORHOOD_CAP = 20` doc paths.
3. **Detect**: per doc, `_detect_stale_term_fixes(content)` (`:497`) returns
   `(compiled_pattern, replacement)` pairs. **← Hole 1 lives here.** The
   `migration_context` hatch is the only thing standing between a stale-term key and a
   whole-document `re.subn`.
4. **Apply**: `_apply_fixes_to_file` (`:550`) computes `original_refs` with `_PATH_REF_RE`,
   builds a `candidate` string per fix, and calls `_absent_new_path_refs` (`:537`).
   **← Hole 2 lives here.** Bare names in `candidate` are invisible, so the guard passes
   them unconditionally.
5. **Withhold or write**: violating fixes go to the `withheld` list; survivors are written
   to disk with `full.write_text`.
6. **Publish**: rotation → `_push_branch_and_pr` (`:1338`) opens a PR, stamping
   `WITHHELD_PR_MARKER` iff `withheld` is non-empty; `/do-docs` → `_commit_current_branch`
   (`:1192`) commits directly.
7. **Merge**: `run_docs_branch_sweeper` (`:1968`) calls `_pr_is_auto_merge_eligible`
   (`:1884`), which auto-merges any docs-only PR with no withheld marker and no reviewer
   activity. **A false statement produced at step 3 with `fixes_withheld = 0` reaches
   `main` with no human in the loop at any step.**

The two fixes are placed at steps 3 and 4 respectively — the narrowest points that
dominate the whole path.

## Architectural Impact

- **New dependencies**: none. One new `subprocess` call to `git ls-files`, using the same
  `settings.timeouts.git_subprocess_s` pattern as the eight existing git calls in the file.
- **Interface changes**: `_absent_new_path_refs` gains a `doc_path` parameter (for
  doc-relative bare-name resolution). All call sites are inside this module. Nothing in the
  public surface (`audit`, `run_docs_auditor`, `run_docs_branch_sweeper`,
  `refresh_docs_in_memory`, `STALE_TERMS`) changes shape. The `_ok_result` contract
  (`fixes_withheld`, `withheld`) is untouched.
- **Coupling**: *decreases*. Reusing `_build_line_context` / `_is_documented_deletion` for
  stale terms removes a second, weaker, hand-rolled notion of "this text is documenting
  history" that currently duplicates what `_DELETION_PROSE_CUES` already expresses.
- **Data ownership**: unchanged. No Popoto model, no Redis key, no schema. **No migration
  required.**
- **Reversibility**: high. Every change is a pure function in one file with no persisted
  state; `git revert` of one commit restores prior behavior exactly.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus a validator pass

**Interactions:**
- PM check-ins: 1 (only if the Open Questions rulings are contested)
- Review rounds: 1

Two defects, one file, one test file, four measured layers with a prototype already
proving the residual. The cost is in the demonstrated-red discipline and the corpus
measurement, not the code.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| On-pin venv | `.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); assert '.'.join(map(str,sys.version_info[:2])) == pin, pin"` | `scripts/pytest-clean.sh` aborts on an off-pin venv; note the ambient `python` on PATH may not be the venv's |
| Git index readable from repo root | `git ls-files --cached --others --exclude-standard --error-unmatch reflections/docs_auditor.py` | Existence oracle for bare names is built from the git index |
| Test DB pinned to 9 | `python -c "import os; assert os.environ.get('POPOTO_TEST_DB') == '9'"` | Shared-test-DB contention (CLAUDE.md); this lane pins `9` |

## Solution

### Key Elements

- **`_normalize_prose(text)`** — one small helper that strips backticks, collapses
  whitespace, and lowercases. Used only for cue matching, never for producing output.
- **Consolidated migration cue set** — replaces the six asymmetric literal arms with a
  generated, case-insensitive list per `(old_term, new_term)` pair, covering the phrasings
  the corpus actually uses: `renamed to`, `replaced by`, `now`, `formerly`, `replaces`,
  `replacing`, `earlier`, `old`, and the `X = Y`, `X -> Y`, `X → Y`, `alias X` shapes.
  The `Replaces`/`replaces` case duplication disappears with it.
- **Context-aware suppression for stale terms** — `_detect_stale_term_fixes` gains the
  ability to exclude occurrences that `_is_documented_deletion` (`:721`) already knows how
  to recognize: fenced code blocks, deletion/migration headings, deletion prose on an
  adjacent line. This machinery exists and is tested; it is currently wired only to
  `_detect_deleted_target_issues`.
- **Path-token suppression** — a stale-term match that lies *inside* a file-path token is
  never rewritten. This closes the `models/session_log.py` → `models/agent_session.py`
  corruption that the existence invariant provably cannot catch (both files exist), and it
  completes the guarantee #2728's docstring explicitly stopped short of.
- **Widened `_PATH_REF_RE`** — `+` → `*`, so bare filenames enter the existence invariant.
- **Bare-name existence oracle** — doc-relative resolution first, then a once-per-run
  `git ls-files` basename index. `0 matches ⇒ withhold`; `≥1 match ⇒ pass` (ambiguity is
  logged at DEBUG, never withheld).

### Flow

Detector proposes a rewrite → **migration-context hatch** (normalized, doc-scoped, cue set)
→ **context suppression** (fence / heading / deletion prose) → **path-token suppression**
→ **existence invariant** (now including bare names) → write, or route to `withheld` →
PR body carries `WITHHELD_PR_MARKER` → auto-merge refused.

Four gates, each fail-closed, each independently tested.

### Technical Approach

All changes are confined to `reflections/docs_auditor.py:497-560` and
`tests/unit/test_docs_auditor_substrate.py`.

**1. `_normalize_prose` and the cue set (#2744).** Normalize both sides before testing.
Generate the cue list from `(old_term, new_term)` rather than hard-coding six arms, so
adding a `STALE_TERMS` entry needs no hatch edit. **Keep the hatch document-scoped** —
spike-2 measured line-scoping as strictly worse. State that ruling in the docstring so the
next reader does not "fix" it.

**2. Reuse `_build_line_context` / `_is_documented_deletion` (#2744) — suppression is
computed at APPLY time, never at detection time.** `_detect_stale_term_fixes` currently
takes only `content` and returns whole-document regex patterns applied via
`pattern.subn()` in `_apply_fixes_to_file` (`:619-628`).

**Detection-time line indices are unusable, and this is not a style preference.**
`_apply_fixes_to_file` applies the literal `fixes` list *first* (`:593-618`), including the
`new == ""` whole-line-delete sentinel that `_detect_readme_broken_entries` emits, and only
*then* runs `regex_fixes` over the already-mutated `new_text`. Any line index computed
against the on-disk `content` is stale the moment a line is deleted ahead of it. On a
`README.md` where both detectors fire, the stale-term loop would suppress the *wrong* line
— and it would do so **silently**, producing a plausible-looking wrong rewrite rather than
an error. That is precisely the corruption class this lane exists to close, so the design
must not reintroduce it.

**Correct mechanism.** Pass a *callable* replacement to `pattern.sub()` instead of a
string. Inside the callable, `match.string` **is** the text currently being rewritten and
`match.start()` is the live offset into it, so fence / heading / deletion-prose context is
re-derived from the actual apply-time text on every match. There is no index to go stale
and nothing to thread through from detection.

**Channel-shape consequences the build must honor:**

- The tuple becomes `(re.Pattern, str | Callable[[re.Match], str])`. This is a **widening,
  not a break**: `re.sub`/`re.subn` accept either, and
  `TestStaleTermDictionary::test_fixes_travel_on_the_regex_channel` (`:613`) asserts the
  *first* element is a `re.Pattern`, which is unchanged. That test must still pass
  unmodified. Update `_apply_fixes_to_file`'s docstring to state the widened type.
- **`subn`'s count becomes wrong and must be corrected.** A suppressed match returns
  `match.group(0)` unchanged, but `subn` still counts it as a substitution. Taking that
  count as-is would inflate `fixes_applied` with rewrites that never happened. The callable
  must tally its suppressions in a closure cell, and the effective count is
  `subn_count - suppressed_count`.
- **Guard the all-suppressed case.** If every match is suppressed, `candidate == new_text`
  while `count > 0`. Without a `if candidate == new_text: continue` guard, the loop runs
  `_absent_new_path_refs` pointlessly and adds a nonzero `applied` for a no-op. Add the
  guard.

**3. Path-token suppression (#2744).** A match whose surrounding token matches a file-path
shape (`[\w.-]*/?[\w.-]+\.(?:py|md)`) is not rewritten. This is the rule that saves
`agent-session-migration-audit.md:22` and `:75`.

**4. Widen `_PATH_REF_RE` (#2759).** `(?:[\w.-]+/)*[\w.-]+\.(?:py|md)`. Because
`_absent_new_path_refs` is additive-only and `original_refs` uses the same pattern, this is
symmetric and — per spike-4 — costs **zero** new withholds on the current corpus.

**5. Bare-name existence oracle (#2759).** `_absent_new_path_refs` gains `doc_path`.
Resolution order for a ref with no `/`:
   1. `(repo_root / doc_path.parent / ref).exists()` → exists.
   2. Cached basename index from `git ls-files --cached --others --exclude-standard`:
      ≥1 match → exists (DEBUG-log when >1); 0 matches → **absent, withhold**.
   Refs containing `/` keep today's exact behavior: `(repo_root / ref).exists()`.

**Ruling on #2759's open question — ambiguity is not a withhold.** `≥1 match ⇒ passes`.
The invariant's question is "does this name denote something real", not "is it
unambiguous". Ambiguity never produced the #2711 corruption; that was a name existing
nowhere. With 218 ambiguous refs in the corpus over 108 multi-owner basenames, a
withhold-on-ambiguous rule fires constantly for no safety gain, straight into #2729's
disposal problem. The ambiguous case still gets **defined, tested behavior** (pass + DEBUG
log), satisfying #2759's acceptance criterion 2.

**Ruling on #2759's open question — `:454` and `:761` are left unchanged, on the record.**
They are *detector* regexes governing what the auditor **proposes**, not the safety path.
Widening them enlarges the candidate set, spending the `GIT_LOG_FOLLOW_CAP = 10` per-run
`git log --follow` budget and the per-run issue cap of 5 on bare names that are not
resolvable to a single path anyway — a scope and volume change with its own failure modes,
not a safety fix. `_PATH_REF_RE` at `:534` is the only one of the three on the write path.
This reason is recorded in a code comment beside each, satisfying acceptance criterion 3.

**Ruling on #2759's open question — this lane does not block on #2729.** Spike-4 measured
the widening's new-withhold count at zero on the current corpus, and the #2744 work in the
same lane strictly *reduces* the number of proposed fixes. #2739's plan
(`docs/plans/docs-auditor-review-gate.md`) owns #2729's escalation path and can land in
either order. Verification row `withheld-rate-non-regression` asserts this mechanically.

**Ruling — the two issues land as ONE PR closing both.** This was previously posed as an
open question, but the plan had already foreclosed it and a late "two PRs" answer would
invalidate the task graph. The binding reason: #2759's AC4 is a *before/after corpus
measurement*, and it is only valid if #2744's reduction has already landed when #2759's
widening is measured — otherwise the two changes' effects on the withheld count are
confounded and the number proves nothing. Task 5 (`Depends On: build-hatch,
build-invariant`), Task 7's dual `Closes #2744` / `Closes #2759`, and the
`withheld-rate-non-regression` Verification row all encode this. If two PRs were ever
chosen instead, that row and Task 5's dependencies would have to split into two sequential
validation passes with separately-pinned baselines — a restructuring this plan does not
accommodate.

**Build-time constraints (operator-mandated, non-negotiable):**

- **FILE FENCE.** The build may touch **only** `reflections/docs_auditor.py` and
  `tests/unit/test_docs_auditor_substrate.py`. No other production file. If implementation
  concludes a file outside the fence must change, **stop and report it as a blocker** — do
  not silently widen scope.
- **`POPOTO_TEST_DB=9`** must be exported for every test invocation in this lane.
- **DEMONSTRATED-RED.** Every behavioral fix lands with a test proven failing against
  current `main` *first*, then passing after the fix. This is a per-fix checklist item
  below, not an aspiration.
- **Plan doc commits on `main`; code commits on `session/docs-auditor-migration-context-and-bare-paths`.**

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `git ls-files` call is the only new failure surface. It must be wrapped and,
      on any failure, **fail-closed toward permissiveness for bare names only** (empty
      index ⇒ bare names fall back to doc-relative check ⇒ likely absent ⇒ withhold). Test
      asserts the `logger.warning` fires and that dir-prefixed refs are unaffected.
- [ ] `_apply_fixes_to_file`'s existing `except Exception` on read (`:576`) and write
      (`:635`) are untouched; their existing coverage stands.
- [ ] No `except Exception: pass` is introduced. Verification row asserts this.

### Empty/Invalid Input Handling
- [ ] `_normalize_prose("")`, `_normalize_prose(None)`-guarded, and whitespace-only content
      must not raise and must produce no fixes.
- [ ] `_detect_stale_term_fixes("")` returns `[]` — already covered by
      `test_absent_key_emits_no_fix` (`:620`); extend to empty and whitespace-only.
- [ ] `_absent_new_path_refs` with an empty `candidate` and with `original_refs == set()`
      returns `[]`.
- [ ] A doc that is entirely a fenced code block produces zero stale-term fixes.

### Error State Rendering
- [ ] A withheld bare-name fix must reach every surface `run_docs_auditor` produces:
      `findings`, `summary`, the Telegram message, `WITHHELD_PR_MARKER` in the PR body, and
      the Redis liveness `fixes_withheld`. The existing `TestWithheldBlocksAutoMerge`
      (`:837`) covers this for dir-prefixed paths; extend it with a bare-name case so the
      new withhold class is proven to propagate, not just to be computed.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py::TestStaleTermDictionary::test_migration_context_skips_fix` (`:603`) — **UPDATE**: it asserts only the un-backticked form, which is why #2744 shipped green. Parametrize across backticked, cased, and alias/arrow forms.
- [ ] `::TestStaleTermDictionary::test_no_migration_context_queues_fix` (`:609`) — **UPDATE**: confirm the widened hatch does not over-exempt; the content must still queue a fix.
- [ ] `::TestStaleTermDictionary::test_fixes_travel_on_the_regex_channel` (`:613`) — **KEEP**: it pins the channel contract the line-filtering change must not break. Verify it still passes unmodified.
- [ ] `::TestStaleTermWordBoundary::test_apply_leaves_session_logs_path_untouched` (`:648`) — **UPDATE**: extend to assert `models/session_log.py` is also untouched (path-token suppression), the corruption the existence invariant cannot catch.
- [ ] `::TestExistenceInvariant` (`:669-800`) — **UPDATE**: every fixture uses `dir/file.py` paths. Add bare-name fixtures alongside; do not replace the existing ones (they pin dir-prefixed behavior as unchanged).
- [ ] `::TestExistenceInvariant::test_preexisting_absent_path_is_never_revalidated` (`:725`) — **KEEP and extend**: the additive-only property is what makes the widening free (spike-4). Add a bare-name twin.
- [ ] `::TestWithheldBlocksAutoMerge` (`:837`) — **UPDATE**: add a bare-name withhold case proving propagation to PR body / Telegram / liveness.
- [ ] `::TestNonMarkdownApplyGuard` (`:489`) — **KEEP**: unaffected; verify still green (the `.md`-only write guard at `:1130` is untouched).
- [ ] `::TestStaleTermWordBoundary` — **ADD** a `README.md` line-shift fixture: a broken index entry deleted via the `new == ""` sentinel on an earlier line plus a stale term with adjacent deletion prose on a later line. Proves apply-time suppression survives the literal loop's line deletions. Assert on resulting content — detection-time indices fail this silently, not loudly.
- [ ] `::TestWithheldRateNonRegression` — **NEW**: the `withheld-rate-non-regression` row. See "How `withheld-rate-non-regression` must be implemented" under Verification; it must drive `_apply_fixes_to_file` directly with snapshot/restore, not `audit(apply_mode="dry-run")`.
- [ ] No `xfail`/`xpass` markers exist anywhere in `tests/unit/test_docs_auditor_substrate.py` — verified by grep at plan time. **No xfail conversions required.**

## Rabbit Holes

- **Chasing the last residual occurrence.** `summarizer-output-audit.md:68` is a quoted
  transcript line. Recognizing "this is quoted history" in unstructured prose is an
  open-ended NLP problem. 17 → 1 is the win; take it. Filed separately (see No-Gos).
- **Rewriting the stale-term channel to be per-occurrence.** Tempting and *measurably
  worse* (spike-2). The document scope is the correct scope.
- **Widening `:454` and `:761` "for consistency".** Ruled against with a recorded reason.
  Consistency across three regexes with different jobs is not a goal.
- **Building a general "is this path real" resolver.** The oracle needs to answer one
  question for one caller. A `git ls-files` basename dict is the whole design.
- **Touching `_pr_is_auto_merge_eligible` or the sweeper.** That is #2739's file region and
  outside the file fence's spirit even though it is the same file. Do not.
- **Adding `STALE_TERMS` entries.** Out of scope; the hatch fix must be term-agnostic so
  additions are free later.

## Risks

### Risk 1: The widened hatch over-exempts and the detector stops doing its job
**Impact:** Genuinely stale terms survive in docs. The detector becomes decorative.
**Mitigation:** This is the deliberate direction of error, and it is asymmetric on purpose:
for an unreviewed autonomous committer, a surviving stale term is a cosmetic miss while a
falsified sentence is a corruption that a human later trusts. Guarded two ways —
`test_no_migration_context_queues_fix` must still queue a fix, and a Verification row runs
the detector over a synthetic doc with a stale term and no migration prose and asserts a
fix is still proposed.

### Risk 2: Line-filtered `subn` breaks the regex-channel contract
**Impact:** A compiled pattern leaks into the literal `fixes` list, where `new == ""` is the
line-delete sentinel — the exact confusion `:510-513` warns about. Worst case: silent line
deletion in a doc.
**Mitigation:** `test_fixes_travel_on_the_regex_channel` (`:613`) stays unmodified and must
pass. Plus a Verification row asserting `_detect_stale_term_fixes` returns only
`re.Pattern` first elements.

### Risk 3: The `git ls-files` index is stale or empty in a worktree
**Impact:** Bare names read as absent → over-withholding → straight into #2729's disposal
problem.
**Mitigation:** Index is built once per `audit()` run from the live index (`--cached
--others --exclude-standard` covers untracked, uncommitted files). On subprocess failure,
log a warning and fall back to doc-relative resolution only. Tested with a forced failure.

### Risk 4: Merge conflict with `docs/plans/docs-auditor-review-gate.md` (#2739)
**Impact:** Rework in whichever lane lands second.
**Mitigation:** Disjoint regions of the same file (`:497-560` here vs. `:1192+` there),
measured at plan time. Neither is built yet. Accepted; no sequencing constraint imposed.

### Risk 5: Path-token suppression is too broad and silences legitimate renames
**Impact:** A doc that genuinely should have `agent/session_log.py` corrected keeps a
broken path.
**Mitigation:** It keeps a *stale* path rather than acquiring a *false* one, and
`_detect_deleted_target_issues` (`:750`) still files an advisory issue for a path that no
longer exists. The failure mode degrades from "silent corruption" to "tracked finding" —
the right direction.

## Race Conditions

**No race conditions identified.** Every function this plan touches
(`_normalize_prose`, `_detect_stale_term_fixes`, `_absent_new_path_refs`,
`_apply_fixes_to_file`) is synchronous, single-threaded, and pure apart from a single file
read/write and one `git ls-files` subprocess. Cross-run concurrency for the auditor as a
whole is already handled upstream by the `REDIS_RUNNING_KEY` SETNX lock (`:1726`) and the
dirty-tree guard (`:1737`), neither of which this plan modifies. The per-run basename index
is built and consumed inside one `audit()` call and never shared across runs or processes.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #2739]` The review gate in front of every auditor write (self-commit,
  `git add -A`, withheld-PR escalation). Owned by #2739 and
  `docs/plans/docs-auditor-review-gate.md`. This lane fixes the generator; that one fixes
  the absence of review.
- `[SEPARATE-SLUG #2729]` Escalation for withheld PRs that are permanently auto-merge-
  ineligible and stale-close at day 14. Spike-4 shows this lane adds zero new withholds, so
  it is genuinely not a blocker here.
- `[SEPARATE-SLUG #2725]` Rename detectors picking the wrong target (newest-commit hop,
  doc-relative link resolved against the repo root). Adjacent code, different defect.
- `[SEPARATE-SLUG #2744]` The single residual occurrence at
  `docs/guides/summarizer-output-audit.md:68` — a quoted transcript line with no migration
  cue and no path token. Tracked as a follow-up comment on #2744 with the measured residual
  so it is not lost; recognizing quoted history is an open-ended problem and 17 → 1 is the
  shaped win.
- `[SEPARATE-SLUG #2739]` Any change to `_pr_is_auto_merge_eligible`, `_push_branch_and_pr`,
  `_commit_current_branch`, or `run_docs_branch_sweeper`. Same file, different region,
  different owner.

**Anti-criteria** for the code-level No-Gos are Verification rows
`no-sweeper-region-touched` and `regex-channel-purity` below.

## Update System

No update system changes required — this is a pure logic fix inside an existing reflection
module. No new dependency, no new config file, no new env key, no `pyproject.toml` entry,
no `scripts/update/migrations.py` migration (no Popoto model is touched). `/update` on
every machine picks the change up as an ordinary code sync.

## Agent Integration

No agent integration required. `reflections/docs_auditor.py` is already reachable through
its two existing surfaces — the reflection scheduler (`run_docs_auditor`,
`run_docs_branch_sweeper`) and the `/do-docs` stage's
`python -c "from reflections.docs_auditor import audit; ..."` call. Both public entry points
keep their exact signatures and their `_ok_result` return contract
(`status`, `files_touched`, `fixes_applied`, `issues_filed`, `pr_url`, `fixes_withheld`,
`withheld`). No new CLI entry point, no bridge import, no MCP surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/docs-auditor.md` — document the four-gate rewrite path
      (migration-context hatch → context suppression → path-token suppression → existence
      invariant), the **document-scoped** hatch ruling and why line-scoping was rejected,
      and the bare-name existence semantics including the ambiguity ruling.
- [ ] Record in the same doc the recorded reason `:454` and `:761` were left unchanged, so
      the decision survives the next reader who notices the asymmetry.
- [ ] `docs/features/README.md` already indexes `docs-auditor.md` — verify the one-line
      description still matches; update only if it does.

### Inline Documentation
- [ ] Rewrite the `_detect_stale_term_fixes` docstring (`:497-513`). Today it accurately
      says word-anchoring "stops short of never rewrites a path" — after path-token
      suppression that sentence is wrong and must describe the new guarantee.
- [ ] Docstring for `_absent_new_path_refs` covering the bare-name resolution order and the
      `≥1 match ⇒ pass` ambiguity ruling.
- [ ] Comment beside `:454` and `:761` recording why they are deliberately narrower than
      `_PATH_REF_RE` (#2759 acceptance criterion 3).

## Success Criteria

- [ ] Running `_detect_stale_term_fixes` + apply over `docs/features/popoto-redis-expansion.md`
      and `docs/guides/agent-session-migration-audit.md` produces **zero byte changes**.
- [ ] The same over `docs/guides/summarizer-output-audit.md` produces at most **one**
      changed occurrence (the accepted `:68` residual), down from five.
- [ ] `_PATH_REF_RE` matches `README.md` and `agent_session.py` as well as
      `agent/session_logs.py` and `docs/features/x.md`.
- [ ] A proposed substitution to a nonexistent **bare** filename is withheld, with a test
      (#2759 AC1).
- [ ] The ambiguous bare-name case has defined, tested behavior: passes, DEBUG-logged
      (#2759 AC2).
- [ ] All three patterns audited; `:454` and `:761` left unchanged **with the reason
      recorded in a code comment** (#2759 AC3).
- [ ] Withheld-fix count over `docs/features/*.md` in dry-run is **not greater** after the
      change than before (#2759 AC4).
- [ ] Every behavioral fix has a test demonstrated **red against `main`** before the fix and
      green after; the red output is pasted into the PR description as the paper trail.
- [ ] The diff touches **only** `reflections/docs_auditor.py`,
      `tests/unit/test_docs_auditor_substrate.py`, and `docs/**` (the last covers Task 6's
      `docs/features/docs-auditor.md` update). The `File fence honored` Verification row's
      grep allowlist is the single source of truth for this criterion; do not narrow the
      grep to exclude `docs/`, which would break Task 6.
- [ ] Tests pass (`/do-test`, with `POPOTO_TEST_DB=9`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (migration-context hatch)**
  - Name: `hatch-builder`
  - Role: #2744 — normalization, cue set, context suppression, path-token suppression
  - Agent Type: builder
  - Resume: true

- **Builder (bare-path invariant)**
  - Name: `invariant-builder`
  - Role: #2759 — widen `_PATH_REF_RE`, bare-name existence oracle, sibling-pattern rationale comments
  - Agent Type: builder
  - Resume: true

- **Test engineer (demonstrated-red)**
  - Name: `red-prover`
  - Role: Write and prove-red every regression test against `main` before either fix lands
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `fence-validator`
  - Role: Verify the file fence, the corpus success criteria, and the withheld-rate non-regression
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `auditor-documentarian`
  - Role: `docs/features/docs-auditor.md` and the inline docstrings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Prove red — migration-context tests
- **Task ID**: red-hatch
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py::TestStaleTermDictionary`, `::TestStaleTermWordBoundary`
- **Informed By**: spike-1 (backtick normalization alone leaves ~9 of 17), spike-3 (residual table)
- **Assigned To**: red-prover
- **Agent Type**: test-engineer
- **Parallel**: true
- On branch `session/docs-auditor-migration-context-and-bare-paths`, **before any production edit**, add tests asserting the hatch fires on: backticked ``formerly `RedisJob` ``; mixed case; alias form `` `SessionLog = AgentSession` ``; arrow form `SessionLog → AgentSession`; "replacing both the earlier `SessionLog`".
- Add a test asserting `` `models/session_log.py` `` is not rewritten to `` `models/agent_session.py` `` (both files exist — the existence invariant cannot catch this).
- Add a test asserting a stale term inside a fenced code block produces no fix.
- **Add the line-shift fixture (critique blocker-adjacent).** A `README.md` carrying *both* a broken index entry that `_detect_readme_broken_entries` deletes via the `new == ""` sentinel on an **earlier** line, *and* a stale term with adjacent deletion prose on a **later** line. Assert the later term is still correctly suppressed after the earlier line is deleted. Detection-time index suppression fails this by silently suppressing the wrong line rather than raising, so the assertion must be on the resulting file content, not on an exception.
- Run `POPOTO_TEST_DB=9 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -k "StaleTerm" -q` and **capture the FAILING output verbatim** into the PR description.
- Do not modify `reflections/docs_auditor.py` in this task.

### 2. Prove red — bare-path invariant tests
- **Task ID**: red-invariant
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant`, `::TestWithheldBlocksAutoMerge`
- **Informed By**: spike-4 (widening costs zero new withholds), spike-5 (`git ls-files` oracle)
- **Assigned To**: red-prover
- **Agent Type**: test-engineer
- **Parallel**: true
- Add tests, **before any production edit**: a fix introducing a bare `ghost_module.py` that exists nowhere is withheld; a fix introducing a bare name resolvable in the doc's own directory passes; a fix introducing an ambiguous bare name (≥2 owners) passes and DEBUG-logs; a pre-existing absent bare name is never re-validated.
- Add a bare-name case to `TestWithheldBlocksAutoMerge` proving the withhold reaches PR body, Telegram, and liveness.
- Run `POPOTO_TEST_DB=9 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -k "Existence or Withheld" -q` and **capture the FAILING output verbatim** into the PR description.
- Do not modify `reflections/docs_auditor.py` in this task.

### 3. Build — migration-context hatch (#2744)
- **Task ID**: build-hatch
- **Depends On**: red-hatch
- **Validates**: `tests/unit/test_docs_auditor_substrate.py::TestStaleTermDictionary`, `::TestStaleTermWordBoundary`, `::TestNonMarkdownApplyGuard`
- **Informed By**: spike-2 (**keep the document scope** — line-scoping is measurably worse), spike-3
- **Assigned To**: hatch-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_normalize_prose` (strip backticks, collapse whitespace, lowercase). Use it for cue matching only, never for output.
- Replace the six literal arms at `:520-527` with a generated, case-insensitive cue list per `(old_term, new_term)`. Keep the hatch **document-scoped**; record that ruling and spike-2's measurement in the docstring.
- Wire `_build_line_context` / `_is_documented_deletion` (`:689-747`) into stale-term suppression **at apply time**, via a callable replacement passed to `pattern.sub()`. Re-derive context from `match.string` and `match.start()` on every match. **Do not precompute line indices at detection time** — the literal `fixes` loop runs first and its `new == ""` line-delete sentinel invalidates them silently (see Technical Approach step 2).
- Add path-token suppression: a match inside a `[\w.-]*/?[\w.-]+\.(?:py|md)` token is never rewritten.
- Correct the substitution count: a suppressed match still increments `subn`'s counter, so tally suppressions in a closure cell and report `subn_count - suppressed_count` as `applied`. Add a `if candidate == new_text: continue` guard for the all-suppressed case.
- Widen the channel's second element to `str | Callable[[re.Match], str]` and say so in `_apply_fixes_to_file`'s docstring. This is a widening, not a break — `test_fixes_travel_on_the_regex_channel` asserts only that the first element is a `re.Pattern` and must pass unmodified.
- Rewrite the `_detect_stale_term_fixes` docstring: the "stops short of never rewrites a path" sentence is now false.
- **FILE FENCE**: touch only `reflections/docs_auditor.py`. If anything else seems required, stop and report a blocker.

### 4. Build — bare-path existence invariant (#2759)
- **Task ID**: build-invariant
- **Depends On**: red-invariant
- **Validates**: `tests/unit/test_docs_auditor_substrate.py::TestExistenceInvariant`, `::TestWithheldBlocksAutoMerge`
- **Informed By**: spike-4, spike-5
- **Assigned To**: invariant-builder
- **Agent Type**: builder
- **Parallel**: false
- Widen `_PATH_REF_RE` (`:534`) to `(?:[\w.-]+/)*[\w.-]+\.(?:py|md)`.
- Add a per-run basename index from `git ls-files --cached --others --exclude-standard`, using `settings.timeouts.git_subprocess_s`, wrapped so a failure logs a warning and degrades to doc-relative resolution only.
- Give `_absent_new_path_refs` a `doc_path` parameter; resolve bare refs doc-relative first, then via the index. `0 matches ⇒ withhold`; `≥1 ⇒ pass`, DEBUG-logging when `>1`.
- Leave `:454` and `:761` unchanged; add a comment at each recording the reason (detector-side, not write-path; widening spends the `GIT_LOG_FOLLOW_CAP` and issue-cap budgets).
- Docstring `_absent_new_path_refs` with the resolution order and the ambiguity ruling.
- **FILE FENCE**: touch only `reflections/docs_auditor.py`.

### 5. Validate — corpus and fence
- **Task ID**: validate-corpus
- **Depends On**: build-hatch, build-invariant
- **Assigned To**: fence-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `git diff --name-only main...HEAD` lists exactly `reflections/docs_auditor.py` and `tests/unit/test_docs_auditor_substrate.py`.
- Run the detector + apply over the three named docs in a scratch copy; assert zero byte changes for the first two and at most one occurrence for the third.
- Land `TestWithheldRateNonRegression` (the `withheld-rate-non-regression` Verification row). **`audit(apply_mode="dry-run")` cannot be used** — see the row's note; `_apply_fixes_to_file` is gated on `apply_mode == "apply"` (`:1130`), so dry-run reports `fixes_withheld == 0` unconditionally. Drive `_apply_fixes_to_file` directly, per the row.
- Measure the withheld count over `docs/features/*.md` at `main` first, pin it in the test as `MAIN_WITHHELD_BASELINE` with a comment recording the SHA it was measured at, then assert the post-change count is `<=` it.
- **Qualitative check — confirm the quieter detector is quiet, not dead.** The detector is deliberately made to propose fewer fixes, so the daily rotation's auto-fix volume visibly drops after this ships. Eyeball the diff the auditor would now produce over the three named corpus docs and confirm what *survives* is genuine stale-term modernization rather than the detector having gone decorative. Pair it with the `Detector still detects` Verification row (which proves a no-migration-context doc still queues a fix) so the drop is attributable to suppression working, not to detection breaking.
- Confirm each red test from tasks 1 and 2 now passes, and that its captured red output is in the PR description.
- Run the full `POPOTO_TEST_DB=9 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q`.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-corpus
- **Assigned To**: auditor-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/docs-auditor.md` with the four-gate path, the document-scope ruling, the bare-name existence semantics, the ambiguity ruling, and the `:454`/`:761` rationale.
- Verify the `docs/features/README.md` index line still describes the module accurately.
- Note: this task writes to `docs/`, which is outside the *code* fence by design — the fence constrains production code, and `/do-docs` output is expected.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: fence-validator
- **Agent Type**: validator
- **Parallel**: false
- Execute every row of the Verification table.
- Confirm every Success Criteria checkbox.
- Confirm the PR body carries the demonstrated-red paper trail and `Closes #2744` and `Closes #2759`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `POPOTO_TEST_DB=9 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check reflections/docs_auditor.py tests/unit/test_docs_auditor_substrate.py` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check reflections/docs_auditor.py tests/unit/test_docs_auditor_substrate.py` | exit code 0 |
| Bare names now visible | `.venv/bin/python -c "from reflections.docs_auditor import _PATH_REF_RE as R; print(int(bool(R.fullmatch('README.md')) and bool(R.fullmatch('agent_session.py')) and bool(R.fullmatch('agent/session_logs.py'))))"` | output contains 1 |
| popoto doc clean | `.venv/bin/python -c "from reflections.docs_auditor import _detect_stale_term_fixes as f; from pathlib import Path; print(len(f(Path('docs/features/popoto-redis-expansion.md').read_text())))"` | output contains 0 |
| migration-audit doc clean | `.venv/bin/python -c "from reflections.docs_auditor import _detect_stale_term_fixes as f; from pathlib import Path; print(len(f(Path('docs/guides/agent-session-migration-audit.md').read_text())))"` | output contains 0 |
| Detector still detects | `.venv/bin/python -c "from reflections.docs_auditor import _detect_stale_term_fixes as f; print(len(f('The SessionLog holds per-turn state and is queried directly.')))"` | output > 0 |
| Regex-channel purity (anti-criterion) | `.venv/bin/python -c "import re; from reflections.docs_auditor import _detect_stale_term_fixes as f; print(sum(0 if isinstance(p, re.Pattern) else 1 for p,_ in f('The SessionLog tracks state.')))"` | output contains 0 |
| No sweeper-region edits (anti-criterion) | `git diff main...HEAD -- reflections/docs_auditor.py \| grep -c '_pr_is_auto_merge_eligible\|_push_branch_and_pr\|_commit_current_branch\|run_docs_branch_sweeper'` | match count == 0 |
| File fence honored (anti-criterion) | `git diff --name-only main...HEAD \| grep -v -E '^(reflections/docs_auditor\.py\|tests/unit/test_docs_auditor_substrate\.py\|docs/)' \| wc -l \| tr -d ' '` | output contains 0 |
| No swallowed exceptions introduced (anti-criterion) | `git diff main...HEAD -- reflections/docs_auditor.py \| grep '^+' \| grep -c 'except Exception: *pass'` | match count == 0 |
| `withheld-rate-non-regression` (#2759 AC4, #2729 non-blocking ruling) | `POPOTO_TEST_DB=9 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -k WithheldRateNonRegression -q` | exit code 0 |
| Sibling-pattern reason recorded | `grep -c '2759' reflections/docs_auditor.py` | output > 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_docs_auditor_substrate.py` | exit code 1 |

### How `withheld-rate-non-regression` must be implemented

This row is the mechanical proof behind two of the plan's rulings (#2759 AC4, and "this
lane does not block on #2729"), so its measurement path has to actually reach the guard it
claims to measure. Two ways to get it wrong, both of which report a reassuring **zero**
regardless of the change's real effect:

1. **Calling `_detect_stale_term_fixes` alone** (the style the `popoto doc clean` and
   `migration-audit doc clean` rows use) never reaches `_absent_new_path_refs`.
   `fixes_withheld` is populated only by `_apply_fixes_to_file`'s `withheld` list threaded
   through `_ok_result`.
2. **Calling `audit(..., apply_mode="dry-run")`** — the obvious fix for (1) — is *also*
   wrong. `_apply_fixes_to_file` is gated on `apply_mode == "apply"` at `:1130`, so in
   dry-run it never runs and `withheld` stays empty. Verified against source during this
   revision.

**The mechanism that works.** Drive `_apply_fixes_to_file` directly, against the real
`repo_root` (the existence oracle and the `git ls-files` basename index both need a real
git checkout — a `tmp_path` mirror makes every reference read as absent and the measurement
becomes meaningless). Because that function writes, the test must snapshot each
`docs/features/*.md` before the call and restore it in a `finally`, then assert
`git diff --quiet -- docs/features/` to prove the corpus was left pristine.

Live as `tests/unit/test_docs_auditor_substrate.py::TestWithheldRateNonRegression`, which
keeps it inside the file fence. The `main` baseline is measured once during Task 5 and
pinned in the test as `MAIN_WITHHELD_BASELINE` with a comment naming the SHA it came from;
the assertion is `after <= MAIN_WITHHELD_BASELINE`.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | The plan twice promises an automated check named `withheld-rate-non-regression` (as the mechanism backing the "does not block on #2729" ruling, and as the proof for Success Criterion #2759 AC4), but no such row exists in the Verification table. The only description is prose inside Task 5 — a task narrative, not a reproducible command. | **ADDRESSED** — Verification row `withheld-rate-non-regression` added, plus a dedicated "How ... must be implemented" subsection under Verification and a new `::TestWithheldRateNonRegression` row in Test Impact. **Correction to the critic's prescription:** `audit(..., apply_mode="dry-run")` does NOT work either — `_apply_fixes_to_file` is gated on `apply_mode == "apply"` at `:1130`, so dry-run leaves `withheld` empty and reports zero for the same reason the detector-only style does. Verified against source. The row drives `_apply_fixes_to_file` directly against the real `repo_root` with snapshot/restore. | Add a Verification row named `withheld-rate-non-regression` running a two-checkout comparison. It MUST call `audit(..., apply_mode="dry-run")` per file, not `_detect_stale_term_fixes` alone: `fixes_withheld` is only populated by `_apply_fixes_to_file`'s `withheld` list threaded through `_ok_result`, so calling the detector directly never reaches `_absent_new_path_refs` and would silently report zero regardless of the widening's effect. |
| CONCERN | Risk & Robustness | The line-filtered `subn` design assumes suppression line indices computed at detection time (against `content` as read from disk) stay valid at apply time. They do not: `_apply_fixes_to_file` (`:593-618`) applies literal `fixes` first — including the `new == ""` whole-line-delete sentinel used by `_detect_readme_broken_entries` — and only then runs `regex_fixes` over the already-mutated `new_text` (`:619-628`). On a `README.md` where both detectors run, a line deletion shifts every subsequent line number before the stale-term loop runs. | **ADDRESSED** — Technical Approach step 2 rewritten: suppression is computed at APPLY time via a callable replacement passed to `pattern.sub()`, re-deriving context from `match.string` / `match.start()`. No index exists to go stale. Task 3 updated. Two consequences the critic did not name are now specified: `subn`'s count over-reports because suppressed matches still count as substitutions (tally in a closure cell, report `subn_count - suppressed_count`), and the all-suppressed case needs a `candidate == new_text` guard. The `README.md` line-shift fixture is added to Task 1 and Test Impact, asserting on content rather than an exception. | Derive fence/heading/deletion-prose context from the text actually being matched at apply time — e.g. `pattern.sub(callable, new_text)` where the callable re-derives context from `new_text` and `match.start()` — rather than threading precomputed indices from detection-time `content`. Add a `README.md` fixture with a broken index entry (deleted via the `new == ""` sentinel) on an earlier line and a stale term with adjacent deletion prose on a later line; index-based suppression fails this silently (wrong line suppressed) rather than raising, so it needs an explicit assertion. |
| CONCERN | History & Consistency, Scope & Value | Open Question 3 ("one PR or two? Confirm.") is posed as unresolved, but the plan has already foreclosed it: the Freshness Check states AC4's corpus measurement "depends on #2744's reduction landing first", Task 5 carries `Depends On: build-hatch, build-invariant`, and Task 7 hard-codes `Closes #2744` and `Closes #2759` in one PR body. A late "two PRs" answer would invalidate the task graph. | **ADDRESSED** — promoted to a fourth Ruling in Technical Approach with the binding reason (AC4's before/after measurement is confounded if the two changes land separately), and Open Question 3 replaced by a pointer to it. | Move the one-PR decision out of Open Questions into a fourth Ruling in Technical Approach, stating the reason: AC4's before/after corpus measurement is only valid if #2744's reduction has landed when #2759's widening is measured. If two PRs were ever chosen instead, Task 5's `Depends On` and the `withheld-rate-non-regression` row must split into two sequential validation passes — a restructuring the plan does not currently accommodate. |
| CONCERN | History & Consistency | Success Criteria asserts the diff "touches **only** `reflections/docs_auditor.py` and `tests/unit/test_docs_auditor_substrate.py`", but Task 6 mandates edits to `docs/features/docs-auditor.md` and the plan's own `File fence honored` Verification row already whitelists `docs/`. The criterion as written is falsified by the plan's own required deliverable, and Task 6 has to explain the discrepancy away in a parenthetical. | **ADDRESSED** — Success Criteria bullet reworded to `reflections/docs_auditor.py`, `tests/unit/test_docs_auditor_substrate.py`, and `docs/**`, naming the `File fence honored` grep as the single source of truth and explicitly forbidding narrowing the grep to match the old prose. | Keep the Verification row's grep allowlist as the single source of truth (`^(reflections/docs_auditor\.py`, `tests/unit/test_docs_auditor_substrate\.py`, `docs/)`) and reword the Success Criteria bullet to cite that same set: "touches only `reflections/docs_auditor.py`, `tests/unit/test_docs_auditor_substrate.py`, and `docs/**`". Do not narrow the grep to match the prose — that would break Task 6. |
| CONCERN | Scope & Value | Every Success Criterion is a technical assertion (byte diffs, regex `fullmatch`, withheld counts, exit codes). The detector is deliberately made quieter, so the daily rotation's auto-fix volume visibly drops after this ships, and nothing confirms that drop reads as intended rather than as the auditor having broken. | **ADDRESSED** — Task 5 gains a qualitative check: eyeball the diff the auditor would produce over the three named docs and confirm surviving fixes are genuine modernization, paired with the existing `Detector still detects` row so the volume drop is attributable to suppression working rather than detection breaking. | Extend Task 5 (`validate-corpus`, `fence-validator`) to build the `run_docs_auditor()` `findings`/`summary` output (or its dry-run equivalent) over the three named docs and assert the summary reads as a clean-corpus result rather than an error or empty-result string. Cheap: that task already runs the detector over the same three docs. Cross-reference the `docs/features/docs-auditor.md` note so the reduced fix rate is traceable to this change. |

---

## Open Questions

The three questions #2759 named as "the plan MUST resolve" are **resolved above**, with
measured evidence rather than judgement calls: ambiguity passes rather than withholds
(spike-5 + 218/108 corpus counts); `:454` and `:761` stay unchanged with the reason
recorded in code; and the lane does not block on #2729 because the widening's measured
new-withhold count is zero (spike-4).

**Question 3 (one PR or two) is resolved** — promoted to a Ruling in Technical Approach per
the critique. One PR closing both, because AC4's before/after corpus measurement is only
valid if #2744's reduction has landed when #2759's widening is measured.

The two remaining questions carry chosen defaults; the build proceeds on them unless the
supervisor overrides.

1. **Is the asymmetry ruling correct?** This plan deliberately errs toward *not*
   rewriting: a surviving stale term is accepted so that a falsified sentence becomes
   impossible. If the docs corpus is judged to need aggressive term modernization more than
   it needs prose safety, the hatch design inverts and this plan is wrong.
   **Default: yes, the asymmetry stands.** For an unreviewed autonomous committer, a
   surviving stale term is cosmetic while a falsified sentence is a corruption a human
   later trusts. Reversible — it is a cue-set width, not a structural choice.
2. **Is the single accepted residual acceptable?**
   `docs/guides/summarizer-output-audit.md:68` will still be rewritten
   (*"fixing RedisJob refs"* → *"fixing AgentSession refs"* in a quoted transcript). The
   alternative is a "quoted history" detector, which is open-ended.
   **Default: accept 17 → 1**, tracked as a follow-up comment on #2744 per the No-Gos.
