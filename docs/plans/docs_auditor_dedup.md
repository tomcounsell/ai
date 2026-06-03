---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-03
tracking: https://github.com/tomcounsell/ai/issues/1543
last_comment_id:
revision_applied: true
---

# Docs-Auditor Deleted-Target Dedup & False-Positive Suppression

## Problem

The *docs-auditor* deleted-target detector (`_detect_deleted_target_issues` in
`reflections/docs_auditor.py`) floods the GitHub issue tracker. In the two weeks
before 2026-06-01 it produced ~45 open `documentation`-labelled issues, almost
all exact duplicates or false positives. Valor bulk-closed all 45 manually on
2026-06-01, and a fresh duplicate (#1551) was filed on 2026-06-03 — confirming
the flood is still recurring against the unpatched auditor.

**Current behavior:** the detector files one issue per dead `.py` reference per
doc per machine per 30-day window, with no defense against illustrative example
paths and no defense against paths that docs deliberately name as deleted.

Three independent root-cause defects produce the noise:

1. **Cross-machine dedup failure.** Dedup lives only in local Redis
   (`docs_audit:issues_filed:<title_hash>`, 30-day TTL). The auditor runs on
   multiple machines, each with its own Redis. Machine A's dedup key is invisible
   to Machine B, so the identical finding is filed once per machine. Observed: the
   same title filed under both `tomcounsell` and `valorengels` gh identities —
   e.g. `agent/hooks/session_registry.py (...)` appears as #1435, #1502, #1522.

2. **No placeholder / example-path filtering.** The regex
   `` `((?:[\w.-]+/)+[\w.-]+\.py)` `` matches any inline-code path, including
   paths docs use deliberately as illustrations. Confirmed false positives:
   `agent/docs_handler/foo.py` (#1492, #1499, #1507, #1518 — an example *about
   path matching*) and `foo/bar.py` (#1517 — an example in `do-test.md`).

3. **Fires on intentionally-documented deleted modules.** The detector flags
   paths a doc *deliberately* names as removed under a Migration/Removed/Deleted
   heading whose entire purpose is to record the deletion. Confirmed:
   `intent/__init__.py` (#1551) under
   `docs/features/emoji-embedding-reactions.md`'s `## Migration from Ollama
   Intent Classification` heading. The file was genuinely deleted in #677, but
   documenting that deletion is the point of the section.

**Desired outcome:** the auditor never files a duplicate of an issue already
open on the tracker, never files on placeholder/example paths or paths inside
illustrative code, never files on paths under Migration/Removed/Deleted prose,
and (ideally) collapses many deleted-target findings into a single rolling
tracking issue rather than one issue per finding.

## Freshness Check

**Baseline commit:** `9dc6929b065f0df355707aa956e45e8d07e69e57`
**Issue filed at:** ~2026-06-01 (body draft-owner ts=1780332324 → 2026-06-01)
**Disposition:** Minor drift (line numbers exact; one cited path-list to re-verify at build)

**File:line references re-verified against baseline `9dc6929b`:**
- `reflections/docs_auditor.py:490` — `_detect_deleted_target_issues` defined here — **still holds** (exact match).
- `reflections/docs_auditor.py:493` — regex `` `((?:[\w.-]+/)+[\w.-]+\.py)` `` — **still holds** (exact match). Note an identical regex also appears at line 382 in `_detect_renamed_symbol_fixes`, which is an auto-fix detector, not an issue-filer; **out of scope** — only the issue-filing path floods the tracker.
- `reflections/docs_auditor.py:570` — `_file_issue_if_new` defined here — **still holds** (issue cited 570-620; function spans 570-620 exactly).
- `reflections/docs_auditor.py:64` — `REDIS_ISSUE_DEDUP_PREFIX = "docs_audit:issues_filed"` — confirmed present.
- Orchestration: `audit()` calls `_detect_deleted_target_issues` at line 738 and `_file_issue_if_new` at line 750 — confirmed.

**False-positive examples re-verified to still exist in docs (so the fix is still needed):**
- `docs/features/emoji-embedding-reactions.md:~138` — `intent/__init__.py` under `## Migration from Ollama Intent Classification` — **confirmed present**.
- `docs/features/do-test.md:57` — `foo/bar.py` illustrative — **confirmed present**.
- `docs/features/pm-dev-session-architecture.md:84` and `docs/features/teammate-session-permissions.md:65` — `agent/docs_handler/foo.py` illustrative — **confirmed present**.

**Cited sibling issues/PRs re-checked:**
- #1247 / PR #1253 — auditor origin (introduced the reflection). Context only; not re-opened.
- #677 — genuinely deleted `intent/__init__.py`. Confirms defect 3's path is a real deletion that is *correctly documented*, not a stale reference.
- The flood issue numbers (#1435, #1492, #1499, #1502, #1507, #1517, #1518, #1522, #1551) are duplicate/false-positive artifacts already closed; they are evidence, not work items. (GraphQL `gh issue view` was rate-limited at plan time; REST confirmed the issue body. Build should re-confirm any still-open duplicates and close them.)

**Commits on main since issue was filed (touching `reflections/docs_auditor.py`):** none observed affecting the two target functions; baseline `9dc6929b` matches the issue's cited line numbers exactly.

**Active plans in `docs/plans/` overlapping this area:** none. No existing plan references `docs_auditor` or `#1543`.

**Notes:** The duplicate regex at line 382 is deliberately left untouched — it feeds the auto-fix renamed-symbol path, not issue filing. Scoping the fix to the issue-filing path keeps the blast radius minimal.

## Prior Art

No prior fix attempts found for this specific flooding behavior. The auditor
itself was introduced in #1247 / PR #1253 (the substrate consolidation). The
flood is a first-order defect in that original implementation — dedup was
designed as local-Redis-only and the regex was never guarded against
illustrative paths. There is no prior PR that tried and failed to fix the
flooding, so this is the first corrective pass.

(GraphQL `gh issue list`/`gh pr list` search was rate-limited at plan time;
prior-art was reconstructed from the issue body's own citations, which are
unusually thorough. Build may run a confirming `gh pr list --state merged
--search "docs auditor dedup"` once the limit resets — no merged fix is
expected.)

## Research

No relevant external findings — proceeding with codebase context and training
data. This is a purely internal bug fix to a reflection: no new external
libraries, APIs, or ecosystem patterns are involved. The only external surface
is the `gh` CLI, already used by `_file_issue_if_new`.

## Data Flow

1. **Entry point**: `run_docs_auditor()` (rotation reflection) → `audit(scope_mode="rotation", apply_mode="apply")`.
2. **Scope resolution**: `audit()` resolves a neighborhood of docs (`_resolve_neighborhood`, cap 20).
3. **Detection** (per doc): `audit()` (line 738) calls `_detect_deleted_target_issues(path, content, root)`, which:
   - iterates regex matches over the doc `content`,
   - skips paths that still exist on disk,
   - skips paths with a git-rename history,
   - emits a finding dict `{title, body, category: "deleted-target"}` for each survivor.
   **← Defects 2 and 3 are fixed HERE: add filtering before emitting a finding.**
4. **Aggregation**: all per-doc findings accumulate in `issue_findings`.
5. **Filing** (line 748-751): for each finding, `_file_issue_if_new(finding, root)`:
   - hashes the title, checks local Redis dedup key, skips if present,
   - runs `gh issue create --label documentation`,
   - sets the Redis dedup key (30-day TTL) on success.
   **← Defect 1 is fixed HERE: add a live `gh issue list` tracker query before filing.**
   **← Batching (optional) restructures step 4→5: collapse N deleted-target findings into one rolling issue.**
6. **Output**: `issues_filed` count returned in the audit result; Telegram summary sent by the rotation caller.

The three fixes attach at three distinct, non-overlapping points in this flow,
so they can be built and tested independently.

## Why Previous Fixes Failed

No prior fixes exist — this is the first corrective pass. Section retained for
completeness; the root-cause pattern below explains why the *original* design
floods.

**Root cause pattern:** the original auditor treated issue filing as a
fire-and-forget local operation. Dedup state was local-Redis-only (invisible
across machines) and the detector regex was a pure syntactic match with no
semantic awareness of *why* a path appears in a doc (real reference vs.
illustration vs. documented deletion). The fix must move the dedup gate to a
shared source of truth (the live tracker) and make the detector context-aware.

## Architectural Impact

- **New dependencies**: none. Uses the existing `gh` CLI (already a dependency
  of `_file_issue_if_new`) and existing `re`/`subprocess` imports.
- **Interface changes**: `_detect_deleted_target_issues` and `_file_issue_if_new`
  signatures stay the same. New private helpers are added (`_is_placeholder_path`,
  `_is_under_deletion_heading` / illustrative-block check, `_open_issue_exists`).
  If batching is adopted, a new `_upsert_rolling_deleted_target_issue` helper
  replaces the per-finding filing for the `deleted-target` category only.
- **Coupling**: slightly increases coupling to the `gh` CLI's `issue list` and
  (for batching) `issue edit` surfaces, but this is the same tool already used.
- **Data ownership**: the live tracker becomes the authoritative dedup source;
  local Redis is demoted to a fast-path cache that must never be the only gate.
- **Reversibility**: high. All changes are inside one reflection module; reverting
  the commit restores prior behavior. No schema, no migration, no persisted state
  format change (Redis keys keep their existing shape).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the batching decision — Open Question 1 — is the main scope fork)
- Review rounds: 1

The coding is small (one module). The communication overhead is the
one-rolling-issue-vs-one-per-doc decision and getting the filtering heuristics
tuned so they suppress noise without masking a genuine dead reference.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Live tracker query + issue filing |
| Repo has `documentation` label | `gh label list --search documentation` | Filed issues carry this label |

Run all checks: `python scripts/check_prerequisites.py docs/plans/docs_auditor_dedup.md`

Note: the live-tracker dedup query (`gh issue list`) consumes GitHub API quota.
The auditor already rate-limits its `gh issue create` calls implicitly via the
rotation cadence; the new `gh issue list` query runs at most once per audit run
(see Risk 2), not once per finding.

## Solution

### Key Elements

- **Live-tracker dedup gate** (`_open_issue_exists`): before filing, query the
  live issue tracker for an already-open `documentation`-labelled issue matching
  this finding. Skip if found. This is the durable cross-machine fix. Local Redis
  remains as a fast-path cache but is no longer the only gate.
- **Placeholder/example-path suppression** (`_is_placeholder_path`): skip paths
  whose components are obvious stand-ins (`foo`, `bar`, `baz`, `qux`, `example`,
  `your-module`, single-letter dirs) before emitting a finding.
- **Illustrative & documented-deletion suppression**: skip paths that appear
  inside fenced code blocks used as illustration, and skip paths that appear
  under a Migration / Removed / Deleted section heading or in prose with deletion
  cues ("deleted module", "no longer in the codebase", "previously in").
- **Rolling tracking issue (optional, Open Question 1)**: collapse all
  deleted-target findings into one rolling issue updated in place, instead of one
  issue per finding.

### Flow

Rotation run → detect deleted-target paths in a doc → **filter: placeholder?
illustrative? under deletion heading?** → survivors become findings → **before
filing, query live tracker for an open match** → file (or upsert the rolling
issue) only if no open match exists → record local Redis fast-path key.

### Technical Approach

- **Defect 1 (cross-machine dedup) — `_file_issue_if_new`:** add a new step
  *before* `gh issue create`. Query
  `gh issue list --state open --label documentation --search "<title>" --json number,title`
  and parse the JSON. If an open issue's title matches (exact, normalized for
  whitespace), return `False` without filing and set the local Redis fast-path
  key so subsequent runs skip the API call. Keep local Redis as a *pre-check*
  fast path (if the local key exists, skip the tracker query entirely). The
  tracker query is the authoritative gate; Redis is the optimization. Handle
  `gh issue list` failure by falling back to the existing local-Redis-only
  behavior (fail-open on the dedup check is preferable to fail-closed silently
  swallowing a real finding — but log a warning).
  - **Open question on match strategy** (Open Question 2): exact-title match vs.
    fuzzy match on the `(target, doc)` pair. Default to exact-title match because
    the title already encodes both `path` and `doc` (`Doc references deleted
    target: {path} (in {doc_path})`), making it a natural composite key.

- **Defect 2 (placeholder/example paths) — `_detect_deleted_target_issues`:** add
  `_is_placeholder_path(path)` that returns `True` if any path component is in a
  stand-in set (`{foo, bar, baz, qux, quux, example, your-module, mymodule,
  sample}`) or is a single lowercase letter directory. Skip such matches before
  emitting a finding. This kills `agent/docs_handler/foo.py` and `foo/bar.py`.

- **Defect 3 (documented deletions & illustrative blocks) —
  `_detect_deleted_target_issues`:** two complementary cues:
  1. **Fenced-code awareness**: when a regex match falls inside a ```` ``` ```` /
     ```` ``` ```` fenced block, treat it as illustrative and skip. (Inline
     single-backtick code is the *normal* way real references are written, so do
     NOT blanket-skip inline code — only fenced blocks.)
  2. **Deletion-heading / deletion-prose awareness**: track the nearest preceding
     Markdown heading (`#`/`##`/`###`) for each match. If that heading contains
     any of `{migration, removed, deleted, deprecated}` (case-insensitive), skip.
     Additionally skip if the match's line or an immediately adjacent line
     contains a deletion cue (`deleted module`, `no longer in the codebase`,
     `no longer exists`, `previously in`, `formerly`). This kills
     `intent/__init__.py`.
  - Tune conservatively: the heading heuristic + prose cue must both be *cheap to
    compute from `content`* (no extra I/O) and must err toward keeping a genuine
    dead reference (Open Question 3 covers aggressiveness).

- **Batching (optional, Open Question 1) — new
  `_upsert_rolling_deleted_target_issue`:** if adopted, instead of filing one
  issue per `deleted-target` finding, collect all surviving findings for the run
  and either (a) update a single global rolling issue titled e.g. `Docs auditor:
  deleted-target references` in place (find the open issue, rewrite its body with
  the current full list), or (b) one rolling issue per doc. Stub-doc and
  orphan-plan detectors are unaffected — batching applies to the deleted-target
  category only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_file_issue_if_new` already wraps `gh` in `try/except Exception` (lines
      583, 618) returning `False` and logging a warning. The new `gh issue list`
      tracker query MUST be wrapped the same way: on `gh issue list` failure, log
      a warning and fall back to local-Redis-only dedup (do not silently swallow
      the finding). Test: patch `subprocess.run` for `issue list` to raise →
      assert a warning is logged AND filing still proceeds via the Redis path.
- [ ] The new filtering helpers (`_is_placeholder_path`,
      `_is_under_deletion_heading`) are pure functions over strings and raise on
      no input path — verify they handle empty/odd input (see below) rather than
      using broad `except`.

### Empty/Invalid Input Handling
- [ ] `_is_placeholder_path("")` and on a path with no `/` → returns `False`
      (or is never reached because the regex guarantees `dir/file.py` shape).
      Add a unit test for the empty-string and single-segment cases.
- [ ] `_detect_deleted_target_issues` on empty `content` → returns `[]` (no
      matches). Add a test.
- [ ] `_open_issue_exists` when `gh issue list` returns empty JSON `[]` → returns
      `False` (no open match → safe to file). Add a test.
- [ ] Tracker query returns malformed/non-JSON output → caught, logged, treated
      as "no match found" (fail-open) so a real finding is not lost. Add a test.

### Error State Rendering
- [ ] The auditor's user-visible output is the filed GitHub issue and the
      Telegram summary. Test that when all findings are suppressed by the new
      filters, `issues_filed == 0` and the Telegram summary reflects zero new
      issues (no crash, no empty-loop).
- [ ] Verify a warning propagates to the logger (not swallowed) when the tracker
      query fails, so operators can see the auditor degraded to Redis-only dedup.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py` — most existing tests patch
      `_file_issue_if_new` to `return_value=False` (lines 135, 152, 260, 298,
      383, 410, 433), so they are insulated from changes to that function's
      internals and **require no change** (UPDATE only if a signature changes —
      it does not).
- [ ] `tests/unit/test_docs_auditor_substrate.py` — ADD a new test class
      `TestDeletedTargetFiltering` covering: placeholder paths suppressed
      (`foo/bar.py`, `agent/docs_handler/foo.py`), deletion-heading paths
      suppressed (`intent/__init__.py` under `## Migration ...`), fenced-block
      paths suppressed, and a genuine dead reference NOT suppressed (regression
      guard against over-filtering).
- [ ] `tests/unit/test_docs_auditor_substrate.py` — ADD a new test class
      `TestCrossMachineDedup` covering: open-tracker match → skip filing,
      no open match → file proceeds, `gh issue list` failure → fall back to
      Redis-only + warning logged, Redis fast-path hit → tracker query skipped.
- [ ] If batching (Open Question 1) is adopted: ADD `TestRollingDeletedTargetIssue`
      covering upsert-into-existing vs. create-new, and that stub-doc/orphan-plan
      filing is unchanged.

No existing tests are DELETED or REPLACED — all changes are additive plus new
coverage. The existing suite's mocking of `_file_issue_if_new` means no
behavioral test currently asserts the un-fixed flooding, so nothing breaks.

## Rabbit Holes

- **Rewriting the regex into a full Markdown parser.** Tempting for perfect
  fenced-block / heading awareness, but a line-scan that tracks fenced-block
  state and the nearest preceding heading is sufficient and far cheaper. Do NOT
  pull in a Markdown AST library.
- **Fuzzy/semantic dedup against the whole tracker.** Querying every open issue
  and computing similarity is overkill and burns API quota. Exact-title search
  via `gh issue list --search` is the right granularity.
- **Touching the auto-fix regex at line 382.** That feeds renamed-symbol
  auto-fixing, not issue filing, and is not part of the flood. Leave it alone.
- **Retroactively closing the ~45 historical duplicates in code.** Those were
  already bulk-closed manually; build may close any *still-open* stragglers, but
  do not build an automated mass-close routine — that is a one-time cleanup, not
  a recurring auditor responsibility.
- **Generalizing the filtering to all three file-as-issue detectors.** The flood
  is specifically the deleted-target detector. Stub-doc and orphan-plan detectors
  are not implicated; do not refactor them.

## Risks

### Risk 1: Over-filtering masks a genuine dead reference
**Impact:** A real stale `.py` reference under a heading that happens to contain
the word "deprecated", or inside a fenced block that is actually a real
reference list, gets silently suppressed — the doc rots undetected.
**Mitigation:** Keep heuristics conservative and additive: only fenced blocks
(not inline code) are skipped, and the deletion-heading set is small and
specific. Add an explicit regression test asserting a genuine dead reference in
normal prose is still flagged. Log suppressed matches at DEBUG so an operator
can audit what the filter dropped.

### Risk 2: Live-tracker query adds GitHub API load / latency
**Impact:** Querying `gh issue list` per finding could be slow and consume quota,
especially when many docs each surface findings (and GraphQL is already rate-limit
sensitive — observed at plan time).
**Mitigation:** Gate the tracker query behind the local-Redis fast-path: if the
local dedup key exists, skip the query entirely. Use the REST-backed
`gh issue list --search` (not GraphQL) where possible. If batching (Open Question
1) is adopted, the query collapses to once-per-run. Wrap in try/except with
fail-open fallback to Redis-only so a rate-limit does not crash the auditor.

### Risk 3: Title-search false negatives create new duplicates
**Impact:** `gh issue list --search "<title>"` is a full-text search, not an exact
match; it may miss an open issue whose title differs by punctuation, re-filing a
duplicate.
**Mitigation:** Fetch candidates via `--search`, then confirm with an
exact normalized-title comparison in Python over the returned `title` fields.
Default to the title as the composite key (it already encodes path + doc).

## Race Conditions

### Race 1: Two machines file the same finding concurrently
**Location:** `_file_issue_if_new` (`reflections/docs_auditor.py:570-620`).
**Trigger:** Machine A and Machine B both run the auditor at nearly the same time,
both query the tracker, both see no open match, both `gh issue create` → two
duplicate issues.
**Data prerequisite:** An open issue with the matching title must be visible to
the tracker query before the second machine fires `issue create`.
**State prerequisite:** The first `issue create` must have committed (issue
visible in `gh issue list`) before the second machine's query runs.
**Mitigation:** This is the residual TOCTOU window the live-tracker query
*shrinks* but cannot fully close (cross-machine, no distributed lock). It is an
acceptable improvement: it turns "one duplicate per machine per 30 days" into "at
most one duplicate only when two machines query inside the same few-second
window" — rare, and dramatically better than today. A rolling-issue design
(Open Question 1) further reduces blast radius because the worst case is one
extra rolling issue, not N. Do NOT introduce a distributed lock for this — the
appetite does not justify it (documented in No-Gos).

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Automated mass-close of historical duplicate issues. The ~45
  were bulk-closed manually on 2026-06-01; build may close any *still-open*
  stragglers by hand-equivalent `gh issue close`, but a recurring auto-close
  routine is destructive (could close a legitimately re-opened issue) and is not
  built here.
- [SEPARATE-SLUG] A distributed cross-machine lock for issue filing. The
  live-tracker query shrinks the dup window to an acceptable size; a real
  distributed lock is a larger infrastructure change disproportionate to this
  appetite. Not filed as a separate issue because the residual risk is deemed
  acceptable, not deferred work — if it ever matters, a new issue can be opened.
- Filtering for the stub-doc and orphan-plan detectors — they are not part of the
  flood (architectural decision, in scope to *leave alone*).
- [DEFERRED] Batching deleted-target findings into a rolling tracking issue
  (resolved decision 1). The live-tracker dedup gate controls volume; a
  rolling-issue system is deferred to a possible follow-up issue if the gate
  proves insufficient in production. `_upsert_rolling_deleted_target_issue` and
  `TestRollingDeletedTargetIssue` are NOT built for this slug.

## Update System

No update system changes required — this feature is purely internal to the
`reflections/docs_auditor.py` reflection. No new dependencies, config files,
secrets, or services are introduced. The `gh` CLI is already installed and
authenticated on every machine that runs the auditor. Existing installations
pick up the fix on the next `git pull` + bridge/worker restart via the standard
`/update` flow.

## Agent Integration

No agent integration required — this is a reflection-internal change. The
docs-auditor runs as a scheduled rotation reflection in the worker
(`run_docs_auditor`), not as an agent-invokable tool. The agent does not call
`_detect_deleted_target_issues` or `_file_issue_if_new` directly, and no MCP
server or `.mcp.json` registration is affected. The bridge does not import this
code path. The only external surface (`gh` CLI) is already wired.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/docs-auditor.md` — revise the "Deleted target" row in
      the File-as-issue table and the dedup description (currently: "Issues are
      deduped by SHA-256 of the title via `docs_audit:issues_filed:{hash}` Redis
      keys (30-day TTL)") to describe the new two-tier dedup (live-tracker query
      as authoritative gate + Redis fast-path cache) and the placeholder /
      illustrative / deletion-heading suppression rules.
- [ ] If batching is adopted, document the rolling-tracking-issue behavior in
      `docs/features/docs-auditor.md` (replace "one issue per finding" with the
      rolling-issue model).
- [ ] No `docs/features/README.md` index change — the auditor already has an entry.

### Inline Documentation
- [ ] Docstrings on the new helpers (`_is_placeholder_path`,
      `_is_under_deletion_heading`/illustrative check, `_open_issue_exists`,
      and `_upsert_rolling_deleted_target_issue` if batching is adopted).
- [ ] Comment on the fail-open fallback in `_file_issue_if_new` explaining why a
      tracker-query failure degrades to Redis-only rather than dropping the finding.

## Success Criteria

- [ ] `_detect_deleted_target_issues` no longer emits findings for
      `agent/docs_handler/foo.py`, `foo/bar.py`, or `intent/__init__.py` (verified
      by unit tests using the real doc content as fixtures).
- [ ] `_file_issue_if_new` (or its batching replacement) queries the live tracker
      and does not file when an open `documentation`-labelled issue with a matching
      title already exists (verified by unit test patching `gh issue list`).
- [ ] A genuine dead `.py` reference in normal prose is still flagged (regression
      guard — over-filtering does not mask real rot).
- [ ] Tracker-query failure degrades to Redis-only dedup with a logged warning,
      and the finding is still filed (no silent drop).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — `docs/features/docs-auditor.md` reflects
      the new dedup + filtering behavior.
- [ ] `grep` confirms the auto-fix regex at `reflections/docs_auditor.py:382` is
      untouched (scope guard).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (auditor-fix)**
  - Name: auditor-builder
  - Role: Implement the three fixes (filtering + live-tracker dedup, optional batching) in `reflections/docs_auditor.py`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (auditor-tests)**
  - Name: auditor-tester
  - Role: Add `TestDeletedTargetFiltering`, `TestCrossMachineDedup` (and `TestRollingDeletedTargetIssue` if batching adopted) to `tests/unit/test_docs_auditor_substrate.py`
  - Agent Type: test-engineer
  - Resume: true

- **Validator (auditor-validate)**
  - Name: auditor-validator
  - Role: Verify all success criteria, run the suite, confirm the line-382 scope guard
  - Agent Type: validator
  - Resume: true

- **Documentarian (auditor-docs)**
  - Name: auditor-docs
  - Role: Update `docs/features/docs-auditor.md`
  - Agent Type: documentarian
  - Resume: true

### 1. Implement filtering + live-tracker dedup
- **Task ID**: build-auditor-fix
- **Depends On**: none
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: auditor-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_is_placeholder_path(path)` and wire it into `_detect_deleted_target_issues`.
- Add fenced-block + deletion-heading/prose awareness to `_detect_deleted_target_issues` (line-scan over `content`, no extra I/O).
- Add `_open_issue_exists(title, repo_root)` querying `gh issue list --state open --label documentation --search "<title>" --json number,title`, with exact normalized-title confirmation in Python.
- Wire `_open_issue_exists` into `_file_issue_if_new` as the authoritative gate behind the local-Redis fast-path; fail-open with a logged warning on `gh issue list` failure.
- Leave the auto-fix regex at line 382 untouched.

### 2. Add test coverage
- **Task ID**: build-auditor-tests
- **Depends On**: build-auditor-fix
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: auditor-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestDeletedTargetFiltering` (placeholder, deletion-heading, fenced-block, genuine-reference-not-suppressed).
- Add `TestCrossMachineDedup` (open match skips, no match files, query failure → Redis fallback + warning, Redis fast-path skips query).
- Cover empty/invalid inputs per Failure Path Test Strategy.

### 3. Documentation
- **Task ID**: document-auditor
- **Depends On**: build-auditor-fix
- **Assigned To**: auditor-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/docs-auditor.md` dedup + filtering description.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-auditor-tests, document-auditor
- **Assigned To**: auditor-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite for the auditor.
- Confirm the three false-positive paths are suppressed and a genuine reference is not.
- Confirm line-382 scope guard via grep.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Auditor unit tests pass | `pytest tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/docs_auditor.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/docs_auditor.py` | exit code 0 |
| Auto-fix regex untouched | `grep -n 'def _detect_renamed_symbol_fixes' reflections/docs_auditor.py` | output contains `_detect_renamed_symbol_fixes` |
| Filtering helper present | `grep -n '_is_placeholder_path' reflections/docs_auditor.py` | output contains `_is_placeholder_path` |
| Live-tracker gate present | `grep -n 'issue.*list' reflections/docs_auditor.py` | output contains `list` |

## Critique Results

Critique verdict: **READY TO BUILD (with concerns)** (recorded 2026-06-03). The
plan is structurally sound; the concerns are the three Open Questions, which the
critique flagged as needing resolution before build so the dev does not have to
guess the scope. All three are resolved in this revision pass (see the resolved
Open Questions section below). No structural rework was required.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Concern | Operator | Batching (OQ1) left as an open scope fork — a dev could over-build a rolling-issue system that the live-tracker gate makes unnecessary. | Resolve OQ1 below | Batching DEFERRED. Ship dedup + filtering only; live-tracker gate controls volume. Batching moves to No-Gos. |
| Concern | Skeptic | Dedup match strategy (OQ2) undecided — exact vs. fuzzy. | Resolve OQ2 below | Exact normalized-title match. Title already encodes (path, doc) as a composite key. |
| Concern | Adversary | Filter aggressiveness (OQ3) undecided — risk of masking a genuine dead reference. | Resolve OQ3 below | Conservative: small heading set + fenced-blocks-only + DEBUG-log every suppressed match for auditability. |

---

## Resolved Decisions (was Open Questions)

The three open questions are resolved as follows for the build. These resolutions
are the authoritative scope for `/do-build`.

1. **Batching — DEFERRED (option c).** Do NOT build a rolling-tracking-issue
   system. The live-tracker dedup gate + placeholder/illustrative/deletion-heading
   filtering directly kill the flood at its source; batching on top of that is
   speculative complexity disproportionate to the Medium appetite. Per-finding
   filing is retained, now gated by the live-tracker query. Batching is moved to
   No-Gos for this slug — if the dedup gate proves insufficient in production, a
   follow-up issue can revisit it. This removes `_upsert_rolling_deleted_target_issue`
   and `TestRollingDeletedTargetIssue` from build scope.

2. **Dedup match strategy — exact normalized-title match.** No fuzzy matching.
   The issue title (`Doc references deleted target: {path} (in {doc_path})`)
   already encodes both `path` and `doc`, making it a natural composite key.
   `gh issue list --search "<title>"` fetches candidates; a Python-side
   exact-comparison after whitespace normalization confirms the match. Fuzzy
   matching is rejected as over-engineering (Rabbit Holes).

3. **Filter aggressiveness — conservative.** Use the small, specific heading set
   (`migration`, `removed`, `deleted`, `deprecated`) plus the named prose cues,
   and skip *fenced* code blocks only (never blanket-skip inline single-backtick
   code, which is how real references are written). Every suppressed match is
   logged at DEBUG so an operator can audit exactly what the filter dropped. The
   regression test (genuine dead reference in normal prose still flagged) is the
   guardrail against over-filtering.
