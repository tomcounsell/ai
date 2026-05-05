---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-05-05
tracking: https://github.com/tomcounsell/ai/issues/1283
last_comment_id:
revision_applied: true
revision_cycle: 1
revision_addresses:
  - "BLOCKER: APPROVAL_COMMIT_SHA extraction from REVIEW_CONTEXT trailer"
  - "CONCERN-1: mixed-shape detection algorithm underspecified"
  - "CONCERN-2: data/ gitignore verification not enforced"
  - "CONCERN-3: cache atomic-write read-modify-write race"
  - "CONCERN-4: head_sha vs approval-commit SHA semantics"
  - "CONCERN-5: classifier needs local SHAs fetched for git diff"
  - "CONCERN-6: test-mapping glob over-matches unrelated tests"
---

# PR-Shape-Aware Merge Gates

Closes #1283.

## Problem

**Current behavior:**

Every PR routed through `/do-merge` runs the **same heavyweight gate stack** regardless of diff shape:

- Full `pytest tests/` suite (categorised against `data/main_test_baseline.json` via `scripts/baseline_gate.py`)
- `python -m ruff check .` and `ruff format --check .`
- `uv lock --locked` (read-only lockfile drift check)
- `## Review:` comment freshness filter — any commit after the latest `## Review: Approved` invalidates the approval
- Documentation gate — `docs/features/{slug}.md` must exist if the plan declared one

The gate stack is invariant. There is no path for a docs-only typo fix or a `uv lock` regen to skip the full pytest suite. This produces two specific bottlenecks:

1. **PATCH loop tax.** When `/do-pr-review` flags a small finding (a doc nit, a missing `# noqa`, a one-line patch), `/do-patch` rewrites the targeted file, then the *full* gate stack re-runs. Empirically, the PATCH→TEST→REVIEW cycle on small fixes consumes more wall-clock time than the original BUILD stage.
2. **Stale-review whip.** Per `do-merge.md:144-175` (shipped via #1155), any commit after the latest `## Review: Approved` invalidates the approval. Correct for substantive code changes, but it forces a full re-review after a docs-only follow-up or a lockfile regen even though the approved logic is unchanged.

**Desired outcome:**

A **PR-shape classifier** routes each PR through a gate set proportional to its blast radius:

- Identifies a small set of "safe shapes" (`docs-only`, `lockfile-only`, `small-patch`) whose failure modes the cheap gates already catch.
- Each safe shape runs a **lighter gate set** that always retains ruff lint, ruff format, and syntax checks; skips the expensive ones the shape provably cannot break.
- A safe-shape follow-up commit on a previously-approved PR preserves the prior approval (the stale-review filter is *narrowed*, not removed).
- A per-SHA verdict cache lets an unchanged tree skip the full pytest re-run.
- Any PR whose claimed safe shape touches files outside its allowlist is reclassified as `mixed` and bumped back to full gates with a logged disqualifier list.

This is a **relief valve, not a bypass.** The classifier defaults to `feature` (full gates) on any ambiguity. The gate contract for genuine feature work is unchanged.

## Freshness Check

**Baseline commit:** `2dd9dde2`
**Issue filed at:** 2026-05-05T02:52:24Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/commands/do-merge.md:91-304` — gate stack still uniform across shapes — holds
- `data/main_test_baseline.json` (gitignored, per-machine) — schema-v2 categorised baseline, unchanged
- `scripts/baseline_gate.py` — `compute_gate_verdict()` shape unchanged from #1084
- `.githooks/pre-commit:49-67` — `uv lock --locked` already runs at commit time when `pyproject.toml` or `uv.lock` is staged (pre-existing from #1155). Issue's solution sketch incorrectly implies this is the merge-gate's only check; recon corrected this in the issue's Recon Summary.
- `docs/sdlc/merge-troubleshooting.md` — exists from #1155 (the issue body did not anticipate this; recon noted it)

**Cited sibling issues/PRs re-checked:**
- #1155 — closed 2026-04-24, all seven hardenings shipped (PR #1160)
- #1084 — closed, schema-v2 baseline shipped (PR #1154)
- #1207 — closed, plan-completion gate dropped
- #1267 — open, complementary track (outcome verification of *claimed completions*); no overlap with PR-shape gate routing

**Commits on main since issue was filed (touching referenced files):**
- All commits since `2dd9dde2` are plan-revision commits (`Plan(#1267)`, `Plan(#1268)`, `Plan(#1271)`) — none touch `do-merge.md`, `baseline_gate.py`, `.githooks/pre-commit`, or any file this plan modifies.

**Active plans in `docs/plans/` overlapping this area:** none. Plans for #1267 (outcome verification), #1268 (composed persona), #1269 (dashboard rows), #1270, #1271 (orphan reaper), #1272 (parallel-session contamination), #1273 (unified loops), #1274 (BYOB) all touch unrelated surfaces. No plan touches `do-merge.md`, `baseline_gate.py`, or introduces a PR classifier.

**Notes:** The issue's solution sketch §3 (stale-review whitelist) is more accurately framed as "safe-shape exemption to the existing #1155 commit-SHA filter," not as a new filter. Plan reflects this.

## Prior Art

- **Issue #1155 / PR #1160 — Self-healing SDLC merge gate.** Shipped seven gate hardenings: cold-Redis durable-signal fallback, commit-SHA-aware review filter, `uv lock --locked` in pre-commit, flake retry + baseline decay + quarantine hints, PM gate-recovery rule, `merge-troubleshooting.md`, merge-guard regex exemption. **This plan extends #1155** — the cache, classifier, and shape-aware routing build on top of those hardenings. Specifically: the per-SHA cache reuses the verdict shape from `compute_gate_verdict()`; the safe-shape exemption narrows (not replaces) the commit-SHA filter from item 2 of #1155.
- **Issue #1084 / PR #1154 — Categorised merge-gate baseline (schema v2).** Shipped `data/main_test_baseline.json` schema v2 with `category` field (real/flaky/hung/import_error). The per-SHA cache stores the *gate verdict* (downstream of the baseline), not the baseline itself — no schema changes needed.
- **Issue #1207 / PR #1209 — Drop plan-completion gate.** Recent precedent for selectively dropping gates that no longer pull their weight. Validates the "lighter routing per shape" principle.
- **Issue #1267 — AgentSession outcome verification.** Open, complementary. Verifies *claimed completions* before SDLC advances. This plan routes *gate intensity by shape*. No overlap; both can ship independently.

## Research

**Queries used:**
- "PR shape aware CI test selection docs-only changes skip pytest 2026"
- "pytest-testmon impacted tests git diff selection"

**Key findings:**
- `pytest-testmon` exists as a PyPI plugin that selects tests affected by changed files via Coverage.py-tracked dependencies. Stores state in a per-repo `.testmondata` file. **Rejected for this plan** because (a) `.testmondata` is per-machine state that conflicts with the per-machine baseline philosophy already documented in `docs/features/merge-gate-baseline.md` (Data ownership section), (b) it adds a runtime dep with cross-PR state semantics, (c) it requires building a database via "run all tests with --testmon" before it works. The hand-rolled glob mapping (issue's open question 4) is sufficient and avoids the new failure mode of "testmondata cache is stale."
- `pytest-git-selector` exists with similar semantics. Same objections as `pytest-testmon`.
- pytest's own skip/xfail markers do not address shape-aware routing — they're per-test, not per-PR. Not applicable.

**Conclusion:** No external dependency is warranted. The implementation is a pure-Python classifier (`scripts/pr_shape_classify.py`) plus shape-conditional logic in `do-merge.md`.

## Spike Results

### spike-1: Verify per-shape gate matrix is sound — would the cheap gates actually catch a regression in each shape?
- **Assumption**: "For docs-only, ruff format/lint and a syntax check are sufficient — no Python file changes means no runtime regression possible."
- **Method**: code-read
- **Finding**: Confirmed for `docs-only` (no `*.py` files in allowlist). For `lockfile-only`, the cheap gates do NOT cover dependency-resolution regressions — `uv.lock` changes can install a different version of a transitive dep. Therefore `lockfile-only` MUST run the full pytest suite (the original issue agreed with this). For `small-patch`, the cheap gates catch syntax/lint, but a behavioral regression in the patched function requires test coverage — hand-rolled touched-file → test mapping must be reliable.
- **Confidence**: high
- **Impact on plan**: Reflected in §Solution gate matrix. `lockfile-only` skips review-staleness only; `small-patch` runs targeted tests via glob mapping with a "no test mapped → fall through to full suite" safety property.

### spike-2: Verify per-SHA cache key uniqueness across baseline changes
- **Assumption**: "Cache key `{pr_number}:{commit_sha}` is sufficient — same SHA implies same verdict."
- **Method**: code-read of `scripts/baseline_gate.py::compute_gate_verdict`
- **Finding**: Insufficient. The verdict depends on BOTH the PR's failing tests AND the baseline's classification. If the baseline file changes between two `/do-merge` invocations on the same PR-SHA (e.g., a refresh on main happens), the cached verdict would be stale. Key MUST include a baseline content-hash component.
- **Confidence**: high
- **Impact on plan**: Cache key revised to `{pr_number}:{commit_sha}:{baseline_content_hash}`. On baseline change, all entries for that baseline silently miss and re-compute. LRU eviction handles cleanup.

### spike-3: Verify hand-rolled touched-file → test mapping coverage on a sample PR
- **Assumption**: "Glob `tests/**/test_{stem}.py` matches enough tests to be useful."
- **Method**: code-read of `tests/unit/` directory naming conventions
- **Finding**: 9 of 9 sampled `*.py` files under `tools/`, `agent/`, `scripts/`, `bridge/` have a corresponding `tests/unit/test_{stem}.py`. The glob also matches `tests/integration/test_{stem}.py` and `tests/integration/test_*{stem}*.py`. Files without a direct match (e.g., `__init__.py`, internal helpers like `agent/_constants.py`) MUST trigger fallback to full suite. Decision rule: if any touched file has zero matched tests, classify as `feature` (no `small-patch` shape).
- **Confidence**: high
- **Impact on plan**: Reflected in §Solution. `small-patch` requires *every* touched file to map to at least one existing test; otherwise classifier returns `feature`.

## Data Flow

1. **Entry point**: `/do-merge {pr_number}` invoked by PM session
2. **Pre-Merge Pipeline Check** (existing, unchanged): query `PipelineStateMachine.get_display_progress()` → confirm TEST/REVIEW/DOCS completed
3. **NEW: Shape classifier**: invoke `python -m scripts.pr_shape_classify --pr {pr_number}` → returns JSON `{shape, allowlist_used, disqualifiers, log_line}`
4. **NEW: Per-SHA cache lookup**: read `data/pr_shape_verdict_cache.json`, key = `{pr}:{sha}:{baseline_hash}`. On hit → use cached verdict; on miss → continue
5. **Shape-aware gate dispatch** (NEW routing layer over existing gates):
   - `docs-only` → ruff lint + ruff format only; preserve prior `## Review: Approved`; skip docs gate (it IS the docs)
   - `lockfile-only` → ruff lint + ruff format + lockfile drift check + full pytest; preserve prior approval
   - `small-patch` → ruff lint + ruff format + lockfile drift check + targeted pytest (touched-file → test glob); preserve prior approval IF the diff between approval-commit and HEAD also classifies as a safe shape
   - `mixed` → log disqualifier list; full gate stack
   - `feature` → full gate stack (status quo)
6. **NEW: Cache write** (on cache miss): write `{pr, sha, baseline_hash, verdict, classified_at, last_used_at}` to cache
7. **Gate verdict aggregation** (existing): same `GATES_FAILED` / `ALL_GATES_PASS` decision logic
8. **Output**: merge proceeds (existing `gh pr merge --squash --delete-branch`) or report blockers (existing)

## Architectural Impact

- **New dependencies**: none. Pure-Python classifier; no PyPI installs.
- **Interface changes**: `scripts/baseline_gate.py` is unchanged. `do-merge.md` gains a "Shape Classification" section before the gate stack. The cache file is a new artifact under `data/` (gitignored).
- **Coupling**: Adds a thin coupling between the classifier (`scripts/pr_shape_classify.py`) and `do-merge.md` (consumer). Both are command-surface code; the classifier is reusable by future skills (e.g., `/do-pr-review` could read the shape to scope its review depth — out of scope for this plan).
- **Data ownership**: New owner is `data/pr_shape_verdict_cache.json` — gitignored, per-machine, same philosophy as `main_test_baseline.json`. No new shared state.
- **Reversibility**: Trivial. Delete the classifier script, revert the `do-merge.md` shape-routing block, delete the cache file. No data migration required.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-ins for the gate matrix decisions

**Interactions:**
- PM check-ins: 1 (confirm gate matrix per shape during build)
- Review rounds: 1

The classifier is small (~200 LOC). The shape-routing in `do-merge.md` is ~80 lines of new bash. The cache module is ~100 LOC. Test coverage (adversarial cases for `mixed` defect detection) is the main investment. No external services, no migrations, no schema changes — Medium not Large.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Required for `gh pr diff` to read PR file lists |
| `uv` available | `command -v uv` | Lockfile drift check on `lockfile-only` shape |
| `data/` directory writable | `test -w data/` | Cache file location |
| `python` 3.10+ | `python --version` | Classifier runs as a Python module |

Run all checks: `python scripts/check_prerequisites.py docs/plans/pr-shape-aware-merge-gates.md`

## Solution

### Key Elements

- **`scripts/pr_shape_classify.py`**: pure-function classifier over `gh pr diff --name-only` + per-shape line-count budget. Returns `{shape, allowlist_used, disqualifiers, log_line}`. Defaults to `feature` on any ambiguity.
- **`scripts/pr_shape_cache.py`**: module exposing `get_cached_verdict(pr, sha, baseline_hash)` and `write_verdict(pr, sha, baseline_hash, verdict)`. Backed by flat JSON file at `data/pr_shape_verdict_cache.json` with LRU cap (100 entries), keyed on `{pr}:{sha}:{baseline_hash[:12]}`.
- **`do-merge.md` shape-routing block**: a new section before "Lockfile Sync Check" that invokes the classifier, the cache, and routes the gate stack accordingly. Always-on cheap gates (ruff lint, ruff format) run unconditionally.
- **Stale-review safe-shape exemption**: in the existing Structured Review Comment Check (`do-merge.md:134-175`), when the most-recent commit's diff classifies as a safe shape AND the prior `## Review: Approved` exists, re-admit the prior approval.
- **`mixed` defect detection**: the classifier's `mixed` shape ALWAYS runs the full stack and emits a deterministic stderr line: `SHAPE: mixed — claimed safe shape touched non-allowlisted paths: <list>` so the disqualification is greppable.

### Flow

PM dispatches `/do-merge {pr}` → Pipeline state check (existing) → **Shape classifier** (`pr_shape_classify --pr N`) → **Cache lookup** (`pr_shape_cache get`) → On hit: use cached verdict, skip pytest re-run, route remaining gates per shape → On miss: route per shape, run gates, **write verdict to cache** → Aggregate verdicts → On pass: `gh pr merge --squash --delete-branch` (existing) → On fail: report blockers (existing).

### Technical Approach

**Classifier (`scripts/pr_shape_classify.py`):**

- CLI modes:
  - `python -m scripts.pr_shape_classify --pr N` — read PR diff via `gh pr diff --name-only N` and `gh pr view N --json additions,deletions,headRefOid` → JSON to stdout
  - `python -m scripts.pr_shape_classify --diff-from <sha> --diff-to <sha>` — read diff via `git diff --name-only <from>..<to>` and `git diff --shortstat <from>..<to>` → JSON to stdout. **Both SHAs MUST be present in the local objects database**; if either `git cat-file -e <sha>` fails, the CLI exits 2 with a clear stderr message ("SHA <sha> not in local objects; run `git fetch origin <sha>` first"). The caller (`do-merge.md` safe-shape exemption block) is responsible for the fetch — see the bash snippet for the safe-shape exemption above.
- Pure function: `classify(changed_files: list[str], net_lines: int, has_new: bool, has_deleted: bool) -> ClassifierResult`
- Allowlist constants:
  - `DOCS_ONLY_GLOBS = ("docs/**", "*.md", "CHANGELOG*", "README*")`
  - `LOCKFILE_ONLY_FILES = frozenset({"uv.lock"})` — strictly literal `uv.lock`, never `pyproject.toml` (settled by Open Question 2)
  - `SMALL_PATCH_LINE_BUDGET = 20` (settled by Open Question 3)
- Decision order (first match wins, ambiguous → `feature`):
  1. All files match `DOCS_ONLY_GLOBS` AND no `*.py`/`*.toml`/`*.lock` → `docs-only`
  2. Files == `LOCKFILE_ONLY_FILES` exactly → `lockfile-only`
  3. No new files, no deletions, all touched files exist on `origin/main` HEAD, net_lines ≤ 20, every touched file maps to ≥1 existing test → `small-patch`
  4. Mentions a safe-shape claim (e.g., diff is *mostly* docs but includes one `*.py`) → `mixed` with disqualifier list
  5. Default → `feature`
- The `mixed` bucket is detected via a deterministic per-shape "majority-match" algorithm. Pseudocode:

  ```python
  SAFE_SHAPES_ORDER = ("docs-only", "lockfile-only", "small-patch")

  def detect_mixed(changed_files: list[str]) -> tuple[str, list[str]] | None:
      """Return (claimed_shape, disqualifying_files) if PR looks like a partial safe shape, else None.

      A shape is "claimed" when ≥50% of changed files match its allowlist AND
      ≥1 file violates it. The disqualifiers are the files that fall outside
      the matched shape's allowlist.

      The 50% threshold prevents trivial single-file feature PRs from being
      classified as "mixed" (e.g., a 1-file Python change isn't a "claimed
      docs-only PR" just because the file isn't a doc). It also prevents
      a single doc edit attached to a 50-file refactor from looking like a
      "claimed docs-only" — the docs are the minority, so it stays `feature`.
      """
      for shape in SAFE_SHAPES_ORDER:
          matched, unmatched = partition_by_allowlist(changed_files, shape)
          if len(matched) >= len(changed_files) / 2 and unmatched:
              return (shape, sorted(unmatched))
      return None  # No partial-safe-shape match → caller defaults to "feature"
  ```

  Concrete examples:
  - PR touches `docs/foo.md` + `agent/bar.py` (1 doc, 1 py). `docs-only` matches `docs/foo.md` (50% = ≥50%), unmatched=[`agent/bar.py`] → `mixed`, claimed=`docs-only`, disqualifiers=[`agent/bar.py`].
  - PR touches 1 doc + 5 py files (1 doc, 5 py). `docs-only` matches 1/6 = 17%, fails the ≥50% gate → `feature` (not `mixed`). The `docs-only` claim is too thin to call.
  - PR touches `uv.lock` + `pyproject.toml`. `lockfile-only` matches 1/2 = 50%, unmatched=[`pyproject.toml`] → `mixed`, claimed=`lockfile-only`, disqualifiers=[`pyproject.toml`]. (This is the canonical "lockfile-only with pyproject leak" case the issue explicitly calls out.)
  - PR touches a new file `agent/feature.py` only (1 py, no docs). No safe shape matches ≥50% AND has unmatched. → `feature` (no `mixed`).

  The algorithm is deterministic, side-effect-free, and unit-testable independently of the rest of the classifier. The 50% threshold is a defended constant — its purpose is documented in the docstring.

**Cache (`scripts/pr_shape_cache.py`):**

- File: `data/pr_shape_verdict_cache.json` (gitignored — covered by `data/` rule at `.gitignore:181`, verified at plan time and re-verified in Step 6 via `git check-ignore data/pr_shape_verdict_cache.json`)
- Schema:
  ```json
  {
    "schema_version": 1,
    "entries": {
      "1283:abc123def:9f8e7d6c5b4a": {
        "pr": 1283,
        "sha": "abc123def...",
        "baseline_hash": "9f8e7d6c5b4a",
        "shape": "small-patch",
        "verdict": { "...JSON from compute_gate_verdict() ..." },
        "classified_at": "2026-05-05T...",
        "last_used_at": "2026-05-05T..."
      }
    }
  }
  ```
- Eviction: when `len(entries) > 100`, drop the entry with the oldest `last_used_at`. Single-pass, no background process.
- `baseline_hash` = first 12 chars of `sha256(pathlib.Path("data/main_test_baseline.json").read_bytes())`. Cache miss when baseline content changes (the most common cache-staleness vector).
- Atomic writes with merge-on-write to bound the read-modify-write race window. The naive "read full dict → mutate → atomic-rename" pattern loses ALL of writer A's prior in-memory entries when writer B's `os.rename` lands first. Mitigation:
  1. Acquire an exclusive `fcntl.flock(LOCK_EX)` on a sidecar file `data/pr_shape_verdict_cache.lock` for the entire read-modify-write critical section. The lock file is created on demand and is gitignored.
  2. Inside the lock: re-read the cache file fresh, mutate the dict, write to `.tmp`, `os.rename` to the canonical path, release the lock.
  3. The lock makes the RMW sequence serial — the `os.rename` itself is then atomic only as a tail step inside the already-serial section.
  4. `flock` is advisory but every cache writer goes through `pr_shape_cache.write_verdict()` which acquires it; no other process touches this file. The advisory contract is sufficient because the *only* other writer is another `/do-merge` invocation calling the same function.
  5. Lock timeout: 10 seconds. On timeout, log a warning and skip the write (cache miss next time is acceptable — this is an optimization, not correctness state).

**`do-merge.md` shape-routing block (new, between Pre-Merge Pipeline Check and Lockfile Sync Check):**

```bash
# Classify PR shape
SHAPE_JSON=$(python -m scripts.pr_shape_classify --pr "$ARGUMENTS")
SHAPE=$(echo "$SHAPE_JSON" | python -c "import json, sys; print(json.load(sys.stdin)['shape'])")
echo "SHAPE: $SHAPE"
if [ "$SHAPE" = "mixed" ]; then
  echo "$SHAPE_JSON" | python -c "import json, sys; d=json.load(sys.stdin); print(f\"  Claimed: {d['claimed_shape']} — disqualifiers: {d['disqualifiers']}\", file=sys.stderr)"
fi

# Cache lookup
SHA=$(gh pr view "$ARGUMENTS" --json headRefOid -q .headRefOid)
CACHED_VERDICT=$(python -m scripts.pr_shape_cache get --pr "$ARGUMENTS" --sha "$SHA" 2>/dev/null || echo "")
```

Then, between Pre-Merge and the existing gate sequence, insert shape-conditional skips:

- `docs-only`: skip Lockfile Sync Check, skip Full Suite Gate, skip docs gate; run ruff lint+format only
- `lockfile-only`: run Lockfile Sync Check, run Full Suite Gate; skip docs gate
- `small-patch`: run all gates BUT replace `pytest tests/` with `pytest <touched-test-files>` and use the cached verdict on cache hit
- `mixed` / `feature`: run the full stack as today

**Stale-review safe-shape exemption (new, in `do-merge.md:134-175`):**

The exemption hinges on extracting the `head_sha` from the `<!-- REVIEW_CONTEXT head_sha=<SHA> pr_body_hash=<HASH> -->` HTML comment that `/do-pr-review` already emits at the end of every review body (see `.claude/skills/do-pr-review/sub-skills/post-review.md:69, 107, 146, 156` and `.claude/skills/do-pr-review/sub-skills/code-review.md:172-176`). The trailer is the canonical anchor for "what code state was approved" — it records the SHA that the reviewer actually evaluated, which is exactly what we need to compute the post-approval diff against. **Do NOT use the comment's `created_at` timestamp or PR commit history to infer the approved SHA — both are subject to force-push and idempotent replay.**

```bash
# Existing: drop comments older than the latest commit
LATEST_COMMIT_DATE=$(gh api repos/$REPO/pulls/$ARGUMENTS/commits --jq '.[-1].commit.committer.date')
LAST_REVIEW=$(gh api repos/$REPO/issues/$ARGUMENTS/comments \
  --jq "[.[] | select(.created_at >= \"$LATEST_COMMIT_DATE\")] | last")

# NEW: if no current review BUT a prior approval exists AND the diff between
# the approval's recorded head_sha and HEAD classifies as a safe shape,
# re-admit the prior approval.
if [ -z "$LAST_REVIEW" ]; then
  PRIOR_APPROVAL=$(gh api repos/$REPO/issues/$ARGUMENTS/comments \
    --jq '[.[] | select(.body | startswith("## Review: Approved"))] | last')
  if [ -n "$PRIOR_APPROVAL" ]; then
    PRIOR_BODY=$(echo "$PRIOR_APPROVAL" | python -c "import json,sys; print(json.load(sys.stdin)['body'])")

    # Extract head_sha from the REVIEW_CONTEXT HTML-comment trailer.
    # Trailer format: <!-- REVIEW_CONTEXT head_sha=<40-char-sha> pr_body_hash=<12-char-hash> -->
    APPROVAL_COMMIT_SHA=$(echo "$PRIOR_BODY" | grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}' | sed 's/REVIEW_CONTEXT head_sha=//' | tail -1)

    if [ -z "$APPROVAL_COMMIT_SHA" ]; then
      # Trailer missing or malformed — pre-trailer reviews (before this feature) lack the marker.
      # Fail closed: do NOT re-admit the prior approval. Surface the reason for the dev.
      echo "REVIEW_COMMENT: SKIP — prior approval has no REVIEW_CONTEXT trailer; cannot anchor safe-shape diff. Falling through to require fresh review." >&2
    else
      # Ensure the approval SHA is fetched locally so git diff can resolve it.
      # `gh pr checkout` only fetches the PR's HEAD ref; older SHAs may not be in
      # the local objects database when /do-merge runs from a fresh clone or
      # after a worktree switch.
      git -C "${SDLC_TARGET_REPO:-.}" cat-file -e "$APPROVAL_COMMIT_SHA" 2>/dev/null || \
        git -C "${SDLC_TARGET_REPO:-.}" fetch origin "$APPROVAL_COMMIT_SHA" 2>/dev/null || {
          echo "REVIEW_COMMENT: SKIP — approval SHA $APPROVAL_COMMIT_SHA not fetchable; falling through to require fresh review." >&2
          APPROVAL_COMMIT_SHA=""
        }
    fi

    if [ -n "$APPROVAL_COMMIT_SHA" ]; then
      HEAD_SHA=$(git -C "${SDLC_TARGET_REPO:-.}" rev-parse HEAD)
      DIFF_SHAPE=$(python -m scripts.pr_shape_classify --diff-from "$APPROVAL_COMMIT_SHA" --diff-to "$HEAD_SHA" \
                   | python -c "import json,sys; print(json.load(sys.stdin)['shape'])")
      SAFE_SHAPES="docs-only lockfile-only small-patch"
      if echo "$SAFE_SHAPES" | grep -wq "$DIFF_SHAPE"; then
        echo "REVIEW_COMMENT: PASS — Prior approval at ${APPROVAL_COMMIT_SHA:0:7} preserved (post-approval diff is $DIFF_SHAPE)"
        LAST_REVIEW="$PRIOR_APPROVAL"
      else
        echo "REVIEW_COMMENT: SKIP — post-approval diff is $DIFF_SHAPE (not a safe shape); fresh review required." >&2
      fi
    fi
  fi
fi
```

**Why `head_sha` from the trailer, not the comment timestamp:** The PR's commit history can be force-pushed; reviews can be replayed idempotently (per `.claude/skills/do-pr-review/sub-skills/post-review.md:149-156`, an idempotent replay re-emits the prior verdict on the *same* HEAD SHA + body hash). The trailer's `head_sha` is the SHA the reviewer actually evaluated — the only stable anchor. The `created_at` timestamp moves on every replay; the commit history can vanish under force-push.

**Why fail closed when trailer is missing:** Reviews authored before the REVIEW_CONTEXT trailer existed (`do-pr-review` pre-this-feature, or human-authored reviews that bypassed the skill) have no anchor SHA. Granting them safe-shape exemption would require *guessing* the approved SHA from comment metadata — a guess that opens the gate against an unverified state. The conservative behavior is to require a fresh review; this matches the original #1155 stale-review filter's safe direction.

This narrows the existing #1155 commit-SHA filter; it does not replace it. A `feature`-shape follow-up still invalidates the prior approval (status quo). A safe-shape follow-up on a pre-trailer approval also still invalidates (defensive).

**Targeted-test mapping for `small-patch`:**

The glob strategy uses two tiers, exact-match-first to bound over-matching:

```python
# Inside scripts/pr_shape_classify.py
SHORT_STEM_THRESHOLD = 4  # stems shorter than this are treated as ambiguous

def map_to_tests(touched_files: list[str], repo_root: Path) -> list[str] | None:
    """Return list of test files for the touched files, or None if any touched file has no mapping.

    Two-tier strategy:
      Tier 1 — exact-name match: `tests/**/test_{stem}.py`. Always tried first.
      Tier 2 — substring match: `tests/**/test_*{stem}*.py`. Only used as fallback
               when Tier 1 finds nothing AND the stem is long enough to be
               reasonably specific (≥ SHORT_STEM_THRESHOLD characters).

    Short stems (e.g. "io", "db", "fs") are NOT eligible for substring matching —
    they would over-match wildly (test_db.py, test_db_loader.py, test_app_db.py,
    test_db_pool.py, ... all match for stem "db"). Short-stem files with no
    Tier 1 match return None → classifier falls back to `feature` shape.

    Per-file substring-match safety cap: if Tier 2 returns more than
    SUBSTRING_MATCH_CAP test files for one source file, the classifier
    treats it as "ambiguous mapping" and returns None — the cap rejects
    the small-patch shape rather than running an unbounded test set.
    """
    SUBSTRING_MATCH_CAP = 8  # max tests per single source file before we bail
    tests: list[str] = []
    for f in touched_files:
        stem = Path(f).stem
        if stem.startswith("_") or stem == "__init__":
            return None  # private helpers and package markers — fall back to feature

        # Tier 1: exact-name match
        exact = list(repo_root.glob(f"tests/**/test_{stem}.py"))
        if exact:
            tests.extend(str(c.relative_to(repo_root)) for c in exact)
            continue

        # Tier 2: substring match (long stems only)
        if len(stem) < SHORT_STEM_THRESHOLD:
            return None  # short stem with no exact match → ambiguous → fall back

        substring = list(repo_root.glob(f"tests/**/test_*{stem}*.py"))
        if not substring:
            return None
        if len(substring) > SUBSTRING_MATCH_CAP:
            # Over-match — running 9+ tests for one file source means our glob
            # is no longer "targeted." Fall back to feature shape and let the
            # full suite run.
            return None
        tests.extend(str(c.relative_to(repo_root)) for c in substring)
    return sorted(set(tests))
```

If `map_to_tests` returns `None` for any touched file, the classifier returns `feature`, not `small-patch`. This is the safety property: silent test-mapping failure (or ambiguous over-matching) cannot wave a regression through. The unit test suite for `test_pr_shape_classify.py` MUST cover both the short-stem fallback and the over-match cap.

**Why the constants:**
- `SHORT_STEM_THRESHOLD = 4` — stems of length ≥4 are specific enough that substring matches are meaningful (e.g., "config" → `test_config_loader.py`, `test_app_config.py` is a manageable set; but "io" → 30+ matches is not). The threshold is a defended constant, documented in the docstring, and exercised in tests.
- `SUBSTRING_MATCH_CAP = 8` — a single source file mapping to 8 tests is the upper bound of "targeted" before the dispatch cost approaches the full suite. Empirically, 8 covers the typical case of a module with one main test file plus a few related integration tests.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `scripts/pr_shape_classify.py` MUST NOT have any `except Exception: pass` blocks. Every catch must log and re-raise OR return `feature` (default-to-safe). Test asserts: a malformed `gh pr diff` response → returns `feature` AND logs a warning.
- [ ] `scripts/pr_shape_cache.py`: corrupt cache file → reset to empty + log warning. Test: write garbage to cache file, call `get_cached_verdict`, assert returns `None` and log message contains "corrupt cache".
- [ ] `do-merge.md` shape-routing block: classifier non-zero exit → fall through to `feature` shape (full gate stack). Test via shell: rename `pr_shape_classify.py`, run gate, assert full pytest still runs.

### Empty/Invalid Input Handling
- [ ] Classifier with empty file list (zero-file PR is impossible but defensively handled) → returns `feature`
- [ ] Classifier with whitespace-only `gh pr diff` output → returns `feature`
- [ ] Cache with `pr_number = ""` or `sha = ""` → returns `None` (cache miss)

### Error State Rendering
- [ ] When `mixed` shape is classified, the disqualifier list appears in stderr AND in the gate's PR comment so the developer sees WHY the shape was rejected
- [ ] When the cache hits, the merge gate prints a clear log line: `SHAPE_CACHE: HIT — verdict reused from {classified_at}` so the dev knows pytest was skipped

## Test Impact

- [ ] `tests/unit/test_do_merge_baseline.py` — UPDATE: add a test for the cache layer's interaction with `compute_gate_verdict()` — specifically: a cached verdict for `pr=N, sha=X` MUST still pass through `format_staleness_warning` (the warning is not cached; it's recomputed on every call so a freshly-stale baseline still warns even on cache hit).
- [ ] `tests/unit/test_validate_merge_guard.py` — UPDATE: add a test that `python -m scripts.pr_shape_classify` is allowed by the guard (it's a read-only command, not a `gh pr merge` call). Should pass without modification, but assert explicitly to prevent regression.
- [ ] `tests/unit/test_do_merge_review_filter.py` — UPDATE: add a test for the safe-shape exemption — a prior `## Review: Approved` followed by a docs-only commit MUST re-admit the prior approval. Adversarial: a prior approval followed by a `feature`-shape commit MUST NOT re-admit.
- [ ] `tests/unit/test_pr_shape_classify.py` (NEW) — REPLACE: greenfield. Cover:
  - Each shape's happy path
  - Every `mixed` defect path (claims docs-only but edits .py; claims lockfile-only but edits pyproject.toml; claims small-patch but creates new file; claims small-patch but exceeds line budget; claims small-patch but has untestable file)
  - `mixed` 50%-threshold algorithm: 1 doc + 1 py → mixed (docs-only claimed); 1 doc + 5 py → feature (docs claim too thin); `uv.lock` + `pyproject.toml` → mixed (lockfile-only claimed)
  - Test-mapping safety: short stem (`io`, `db`, `fs`) with no Tier 1 match → returns None → classifier returns feature
  - Test-mapping safety: substring over-match (>8 hits) → returns None → classifier returns feature
  - Test-mapping safety: `__init__.py` and `_helper.py` always return None
  - `--diff-from`/`--diff-to` mode: missing local SHA → exits 2 with stderr message; valid SHAs → returns shape JSON
  - Ambiguity → `feature`, empty diff → `feature`, malformed diff → `feature`
- [ ] `tests/unit/test_pr_shape_cache.py` (NEW) — REPLACE: greenfield. Cover:
  - Hit, miss, cache key includes baseline hash (baseline change → miss)
  - LRU eviction at 100 entries
  - Atomic write doesn't corrupt on interrupt (kill mid-write, assert canonical file remains valid)
  - Corrupt file resets to empty (write garbage, assert get_cached_verdict returns None and logs warning)
  - **Concurrent write serialization**: two threads each call `write_verdict` with different PR/SHA inputs; assert final cache contains BOTH entries (not one). This is the regression test for the `fcntl.flock` mitigation of Race 1.
  - **Lock timeout**: hold the lock manually for >10s, call `write_verdict`; assert it returns without raising and logs a "lock timeout" warning, no partial write.
- [ ] `tests/integration/test_do_merge_shape_routing.sh` (NEW) — REPLACE: greenfield. End-to-end: synthesize a fake PR diff for each shape, invoke the shape-routing block from `do-merge.md`, assert the correct gates ran (e.g., for `docs-only`, `pytest` was NOT invoked). Include the safe-shape exemption fail-closed cases:
  - Prior approval body without REVIEW_CONTEXT trailer → exemption SKIPS, fresh review required
  - Prior approval body with REVIEW_CONTEXT trailer but the SHA is not fetched → exemption SKIPS after fetch fails
  - Prior approval body with valid trailer + safe-shape post-approval diff → exemption PASSES, prior approval re-admitted

## Rabbit Holes

- **Adopting `pytest-testmon` instead of hand-rolled glob mapping.** Tempting (it's well-maintained, more accurate). But `.testmondata` is per-machine state with cross-PR semantics that conflicts with the per-machine baseline philosophy (`docs/features/merge-gate-baseline.md` Data ownership). New failure mode of "testmondata cache is stale" introduces complexity disproportionate to the relief. Stay with hand-rolled glob; default-to-feature fallback is the safety property.
- **Caching beyond per-SHA verdicts.** The cache could grow to memoize classifier output, intermediate baseline parsing, etc. Don't. The verdict cache is the only one that matters — pytest is the dominant cost. Other operations are millisecond-scale.
- **Per-shape *configuration*** (allowing developers to declare a PR's shape via PR description). Defeats the defect-detection property — a PR could claim `docs-only` and be granted it without the classifier checking the diff. Always derive shape from the diff.
- **Shape inheritance on merge.** Tempting to extend the shape concept past merge (e.g., "this commit on main was a docs-only commit, future PRs benefit"). Out of scope; baseline already handles main-state.
- **Generalising `mixed` to a configurable allowlist policy** (e.g., "in repo X, allow `pyproject.toml + uv.lock` as `lockfile-only`"). Don't. Keep allowlists hard-coded in the classifier; configurability adds complexity for no measurable benefit on a single-repo system.

## Risks

### Risk 1: `mixed`-shape false positive locks an honest PR into full gates
**Impact:** Annoyance, not a correctness issue. A developer expecting `docs-only` routing sees full pytest run anyway because the classifier flagged a typo or a misnamed file (e.g., `docs/foo.py.md`).
**Mitigation:** Adversarial unit tests cover edge cases. The disqualifier list is logged so the developer can see exactly why their PR was bumped. Worst case: full gates run (the safe direction). No correctness risk.

### Risk 2: Cache returns stale verdict after a baseline refresh that the dev didn't notice
**Impact:** Stale verdict could mask a regression that a fresh baseline would catch.
**Mitigation:** Cache key includes `baseline_hash` (sha256 prefix of baseline file). Any baseline change invalidates all cached entries for that baseline. Verified by spike-2.

### Risk 3: Touched-file → test glob misses a test that DOES cover the touched function
**Impact:** A `small-patch` shape runs targeted tests only, skipping a test in a different file that would have caught the regression. Falsely passes.
**Mitigation:** Two-tier glob: exact-name `test_{stem}.py` first, then substring `test_*{stem}*.py` for stems ≥4 chars only. Per-file cap of 8 substring matches before falling back to `feature`. Any touched file with zero matched tests downgrades to `feature`. Documented limitation: deeply indirect coupling (e.g., a function imported transitively by `tests/unit/test_other.py`) is not caught — but neither is it caught by the full suite when only the unit test for the touched stem runs in isolation. The relevant comparison is "targeted tests vs. nothing," not "targeted tests vs. omniscient test selection."

### Risk 6: Test-mapping glob over-matches and runs an unintended test set
**Impact:** A `small-patch` for `tools/db.py` (stem "db") would substring-match `test_db.py`, `test_db_loader.py`, `test_app_db.py`, `test_db_pool.py`, and dozens more if uncapped — defeating the targeted-test goal and possibly running tests that fail for unrelated reasons.
**Mitigation:** `SHORT_STEM_THRESHOLD = 4` rejects substring matching for stems shorter than 4 chars (the most over-match-prone bucket). `SUBSTRING_MATCH_CAP = 8` rejects substring sets larger than 8 per source file. Both constants are documented and unit-tested. When either rejects a file, the classifier returns `feature` — the safe direction.

### Risk 4: Safe-shape exemption admits an approval whose context has changed
**Impact:** A prior approval might have been given assuming a specific code state; a docs-only follow-up that reframes the docs could imply a different intent.
**Mitigation:** Safe shapes are *narrow* — `docs-only` only matches docs, `lockfile-only` only matches `uv.lock`, `small-patch` only matches ≤20-line patches in already-existing files. None of these can change runtime semantics meaningfully. Reviewer can always force a re-review via a fresh `## Review:` comment.

### Risk 5: PR diff size grows during the gate run (e.g., a force-push during gate execution)
**Impact:** Classifier reads diff at gate-start; cache key uses HEAD SHA. If HEAD moves mid-gate, cached verdict is for a different SHA than what's being merged.
**Mitigation:** `gh pr merge` already detects this — the merge fails with "PR head has moved." The gate's cache write uses the SHA at gate-start; the actual merge command will use the SHA at merge-time. No new failure mode.

## Race Conditions

### Race 1: Concurrent `/do-merge` invocations cause read-modify-write entry loss
**Location:** `scripts/pr_shape_cache.py::write_verdict`
**Trigger:** Two `/do-merge` invocations (e.g., a PM session and a manual run) compute verdicts for two *different* PRs simultaneously and try to write to the cache file.
**Data prerequisite:** Both processes read the cache file (each sees the same set of N entries), each adds its own new entry, each writes back N+1 entries.
**State prerequisite:** Both `os.rename` calls land in sequence. The naive atomic-write pattern means writer A's N+1 entries get overwritten by writer B's N+1 entries — A's new entry is lost (but the existing N entries survive because B also had them).
**Real failure mode:** When the writers' input *also* differs in which old entry the LRU eviction drops (e.g., A and B both compute a new entry, the cache is at exactly 100 entries, both run LRU eviction independently and pick different victims), the final state has TWO new entries missing — A's new entry and B's evicted entry that A would have kept.
**Mitigation:** `fcntl.flock(LOCK_EX)` on `data/pr_shape_verdict_cache.lock` wrapping the entire read-modify-write critical section. The lock serialises writers; the `os.rename` tail is then an atomic step inside an already-serial section. Verdicts themselves are deterministic (same input → same output) so a lost cache entry is at most one extra pytest run on the next invocation — not a correctness issue. Lock timeout 10s; on timeout the writer logs a warning and skips the write.

### Race 2: Baseline file rewritten mid-gate by a concurrent merge
**Location:** `data/main_test_baseline.json` is rewritten by `do-merge.md`'s post-merge reset block
**Trigger:** Gate A reads baseline, computes hash, writes cache entry. Gate B's post-merge reset rewrites baseline. Gate A's cache entry is now keyed against an obsolete baseline.
**Data prerequisite:** Concurrent `/do-merge` succeeds during another `/do-merge`'s gate run.
**State prerequisite:** Two PRs being merged simultaneously is the only path.
**Mitigation:** The cache entry is *for the old baseline* and remains valid for re-runs against the old baseline. On the next `/do-merge` invocation, the new baseline yields a different `baseline_hash` → cache miss → fresh compute. No correctness issue; cache simply has one more stale entry that LRU evicts.

## No-Gos (Out of Scope)

- **Removing any existing gate from the `feature` shape.** Status quo for everything that classifies as `feature` or `mixed`.
- **Custom per-shape gate matrices configurable via CLI flags or env vars.** Allowlists are hard-coded constants in the classifier source.
- **Shape routing for non-`/do-merge` skills** (e.g., `/do-pr-review` could shorten its review for `docs-only`). Future work.
- **Cross-PR shape memoization** (e.g., "this branch's last commit was docs-only, assume the next is too"). Always re-classify.
- **Parallelising the gates within a shape.** Existing gate sequence is sequential; this plan keeps it sequential. Parallelisation is a separate optimisation.
- **Replacing `pytest-testmon` adoption.** Hand-rolled glob is the chosen approach. Re-evaluate if the glob proves insufficient in practice.
- **`pyproject.toml` as `lockfile-only`.** Settled by Open Question 2: any `pyproject.toml` change is `feature`-shape. The lockfile shape is strictly the literal file `uv.lock`.

## Update System

No update system changes required — this feature is purely internal to the `/do-merge` skill flow on each developer's machine. The classifier and cache run only when `/do-merge` is invoked, which only happens after the PM session has dispatched it. No new dependencies, no new config files, no new processes. The `data/pr_shape_verdict_cache.json` file is created on first run and gitignored under the existing `data/` rule. The update script does not need to know about this feature.

## Agent Integration

No agent integration required — this is a `/do-merge`-internal change. The agent (PM session) already invokes `/do-merge {pr_number}` via the existing skill dispatch mechanism (`sdlc-tool next-skill` returns `/do-merge` from `agent/sdlc_router.py`). The shape routing happens inside the `/do-merge` skill execution and is invisible to the agent — the agent receives the same `MERGE_AUTHORIZED` / `GATES_FAILED` outcome as today, just faster on safe shapes.

The classifier is invoked as a Bash command from inside `do-merge.md`. It does not need to be wrapped in an MCP tool. No `pyproject.toml [project.scripts]` entry needed (the classifier runs as `python -m scripts.pr_shape_classify`, matching the existing `python -m scripts.baseline_gate` pattern).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pr-shape-aware-merge-gates.md` describing the shape taxonomy, the gate matrix, the defect-detection path, the cache eviction policy, and the relationship to `docs/features/merge-gate-baseline.md`
- [ ] Add entry to `docs/features/README.md` index table

### Repo SDLC Addenda
- [ ] Update `docs/sdlc/do-merge.md` — add a section "Shape-Aware Routing" that briefly describes the new behavior and points to the feature doc
- [ ] Update `docs/sdlc/merge-troubleshooting.md` — add a "Why was my PR classified as `mixed`?" section with the disqualifier-list grep pattern

### Inline Documentation
- [ ] Module docstrings on `scripts/pr_shape_classify.py` and `scripts/pr_shape_cache.py` linking to the feature doc
- [ ] `do-merge.md` comment block above the new shape-routing section explaining what runs when

## Success Criteria

- [ ] `scripts/pr_shape_classify.py` exists with pure-function classifier returning `{shape, allowlist_used, disqualifiers, log_line}` on stdout as JSON
- [ ] `scripts/pr_shape_cache.py` exists with `get_cached_verdict` / `write_verdict` API; cache file at `data/pr_shape_verdict_cache.json` (gitignored)
- [ ] `.claude/commands/do-merge.md` invokes the classifier before the gate stack and routes per shape using the matrix in §Solution
- [ ] Always-on cheap gates (ruff lint, ruff format) run on every PR regardless of shape
- [ ] A safe-shape follow-up commit on a previously-approved PR preserves the prior `## Review: Approved`; an unsafe-shape follow-up still invalidates it
- [ ] Per-SHA verdict cache hits avoid re-running the full pytest suite when the tree hash and baseline hash are unchanged
- [ ] `mixed` shape PRs get the full gate stack AND emit `SHAPE: mixed — ...` to stderr with the disqualifier list
- [ ] `tests/unit/test_pr_shape_classify.py` passes with adversarial inputs for every `mixed` defect path
- [ ] `tests/unit/test_pr_shape_cache.py` passes including LRU eviction, atomic write, baseline-change invalidation
- [ ] Documentation: `docs/features/pr-shape-aware-merge-gates.md` exists; `docs/sdlc/do-merge.md` and `docs/sdlc/merge-troubleshooting.md` updated; entry added to `docs/features/README.md`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No existing test in `tests/unit/test_do_merge_*.py` regresses

## Team Orchestration

### Team Members

- **Builder (classifier)**
  - Name: shape-classifier-builder
  - Role: Implement `scripts/pr_shape_classify.py` with pure-function classifier and CLI
  - Agent Type: builder
  - Resume: true

- **Builder (cache)**
  - Name: shape-cache-builder
  - Role: Implement `scripts/pr_shape_cache.py` with LRU + atomic writes
  - Agent Type: builder
  - Resume: true

- **Builder (gate routing)**
  - Name: gate-routing-builder
  - Role: Update `.claude/commands/do-merge.md` with shape-routing block + safe-shape review exemption
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: shape-test-engineer
  - Role: Write `tests/unit/test_pr_shape_classify.py` and `tests/unit/test_pr_shape_cache.py` including adversarial cases
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: shape-documentarian
  - Role: Create `docs/features/pr-shape-aware-merge-gates.md`, update `docs/sdlc/do-merge.md`, `docs/sdlc/merge-troubleshooting.md`, `docs/features/README.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: shape-validator
  - Role: Verify all success criteria, including end-to-end shape routing on synthetic PR diffs
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the classifier
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: tests/unit/test_pr_shape_classify.py (created by build-tests)
- **Informed By**: spike-3 (touched-file → test glob coverage)
- **Assigned To**: shape-classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `scripts/pr_shape_classify.py` with `classify()` pure function and `__main__` CLI
- Allowlist constants for `docs-only`, `lockfile-only`, `small-patch`
- Mixed-shape detection via "relaxed allowlist" pattern
- Touched-file → test mapping with default-to-feature fallback
- Module docstring linking to `docs/features/pr-shape-aware-merge-gates.md`

### 2. Build the cache layer
- **Task ID**: build-cache
- **Depends On**: none
- **Validates**: tests/unit/test_pr_shape_cache.py (created by build-tests)
- **Informed By**: spike-2 (cache key needs baseline hash)
- **Assigned To**: shape-cache-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `scripts/pr_shape_cache.py` with `get_cached_verdict`, `write_verdict`, internal `_evict_lru`, `_baseline_hash`
- Atomic write via `os.rename`
- Schema-versioned JSON file at `data/pr_shape_verdict_cache.json`
- LRU cap at 100 entries
- Module docstring linking to feature doc

### 3. Wire up the gate routing
- **Task ID**: build-gate-routing
- **Depends On**: build-classifier, build-cache
- **Validates**: tests/integration/test_do_merge_shape_routing.sh (created by build-tests)
- **Assigned To**: gate-routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert shape classification block in `.claude/commands/do-merge.md` before "Lockfile Sync Check"
- Add cache-lookup block before the Full Suite Gate
- Add cache-write block after the Full Suite Gate (cache-miss path)
- Update Structured Review Comment Check with safe-shape exemption logic
- Add per-shape skip blocks (docs-only skips Full Suite, etc.)
- Add cache-write call on miss
- Comment block above each new section explaining what runs when

### 4. Build the tests
- **Task ID**: build-tests
- **Depends On**: build-classifier, build-cache, build-gate-routing
- **Validates**: pytest tests/unit/test_pr_shape_*.py
- **Assigned To**: shape-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- `tests/unit/test_pr_shape_classify.py` — every shape happy path + every `mixed` disqualifier path + 50%-threshold algorithm cases + short-stem fallback + substring over-match cap + `__init__.py` rejection + `--diff-from`/`--diff-to` mode (missing-SHA exit 2; valid SHAs return JSON) + ambiguity → feature + empty/malformed input
- `tests/unit/test_pr_shape_cache.py` — hit, miss, baseline-change invalidation, LRU eviction, atomic write, corrupt-file recovery, concurrent-write serialization (the `fcntl.flock` regression test for Race 1), lock-timeout no-write
- `tests/integration/test_do_merge_shape_routing.sh` — synthesize a fake PR diff for each shape, invoke routing, assert correct gates ran. Include the safe-shape exemption fail-closed cases (missing trailer SKIPS; unfetchable SHA SKIPS; valid trailer + safe-shape diff PASSES)
- Update `tests/unit/test_do_merge_review_filter.py` for safe-shape exemption (positive + adversarial cases including the trailer-missing SKIP regression)

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: shape-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pr-shape-aware-merge-gates.md` covering: shape taxonomy, gate matrix table, defect-detection contract, cache eviction policy, relationship to `merge-gate-baseline.md`
- Add entry to `docs/features/README.md` index table
- Add "Shape-Aware Routing" section to `docs/sdlc/do-merge.md`
- Add "Why was my PR classified as `mixed`?" section to `docs/sdlc/merge-troubleshooting.md`

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: shape-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pr_shape_classify.py tests/unit/test_pr_shape_cache.py -v`
- Run `pytest tests/unit/test_do_merge_review_filter.py tests/unit/test_do_merge_baseline.py -v` (regression check)
- Run `bash tests/integration/test_do_merge_shape_routing.sh`
- Run `python -m ruff check scripts/pr_shape_classify.py scripts/pr_shape_cache.py`
- Verify `docs/features/pr-shape-aware-merge-gates.md` exists and is referenced from `docs/features/README.md`
- Verify `git check-ignore data/pr_shape_verdict_cache.json` exits 0 (gitignored under `data/` rule at `.gitignore:181`)
- Verify `git check-ignore data/pr_shape_verdict_cache.lock` exits 0 (lock sidecar also under `data/`)
- Verify the safe-shape exemption fail-closed paths via the integration script: missing trailer SKIPS, unfetchable SHA SKIPS, valid trailer + safe-shape diff PASSES
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Classifier tests pass | `pytest tests/unit/test_pr_shape_classify.py -x -q` | exit code 0 |
| Cache tests pass | `pytest tests/unit/test_pr_shape_cache.py -x -q` | exit code 0 |
| Review filter regression | `pytest tests/unit/test_do_merge_review_filter.py -x -q` | exit code 0 |
| Baseline gate regression | `pytest tests/unit/test_do_merge_baseline.py -x -q` | exit code 0 |
| Shape routing E2E | `bash tests/integration/test_do_merge_shape_routing.sh` | exit code 0 |
| Lint clean | `python -m ruff check scripts/pr_shape_classify.py scripts/pr_shape_cache.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/pr_shape_classify.py scripts/pr_shape_cache.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/pr-shape-aware-merge-gates.md` | exit code 0 |
| Feature doc indexed | `grep -q pr-shape-aware-merge-gates docs/features/README.md` | exit code 0 |
| Cache file gitignored | `git check-ignore data/pr_shape_verdict_cache.json` | exit code 0 |
| Cache lock gitignored | `git check-ignore data/pr_shape_verdict_cache.lock` | exit code 0 |
| Trailer extraction regex | `echo '## Review: Approved\n<!-- REVIEW_CONTEXT head_sha=abc123def456abc123def456abc123def4567890 pr_body_hash=deadbeefcafe -->' \| grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}'` | matches the trailer line |
| Concurrent-write race regression | `pytest tests/unit/test_pr_shape_cache.py::test_concurrent_writers_serialize -v` | exit code 0, both entries present in final cache |
| Short-stem fallback | `pytest tests/unit/test_pr_shape_classify.py::test_short_stem_falls_back_to_feature -v` | exit code 0 |
| Substring over-match cap | `pytest tests/unit/test_pr_shape_classify.py::test_substring_overmatch_cap -v` | exit code 0 |
| Trailer-missing SKIP | `pytest tests/unit/test_do_merge_review_filter.py::test_safe_shape_exemption_skips_when_trailer_missing -v` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary | `APPROVAL_COMMIT_SHA=$(... extract from PRIOR_APPROVAL ...)` was a placeholder; no concrete extraction logic. The whole safe-shape exemption is unimplementable without an anchor SHA. | Stale-review safe-shape exemption block | Replaced with `grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}'` extraction from the `<!-- REVIEW_CONTEXT head_sha=<SHA> ... -->` trailer that `/do-pr-review` already emits. Falls closed (skips exemption) when trailer is missing or SHA is unfetchable. |
| CONCERN | Skeptic | `mixed`-shape detection ("relaxed allowlist" pattern) was hand-waved; no concrete pseudocode. | Solution → Classifier `mixed` detection | Replaced with deterministic 50%-threshold majority-match algorithm. Pseudocode + 4 worked examples + defended threshold constant. Unit-tested. |
| CONCERN | Operator | `data/` gitignore claim was unverified. If the rule were absent, the cache file would be committed. | Solution → Cache file location + Step 6 Verification | Confirmed `data/` is gitignored at `.gitignore:181`. Added `git check-ignore` verification to Step 6 and Verification table. |
| CONCERN | Adversary | Cache atomic-write race was understated. RMW pattern loses entries beyond just the writer's own; LRU-eviction-under-contention can lose two entries per race. | Cache section + Race 1 mitigation | Added `fcntl.flock(LOCK_EX)` on `data/pr_shape_verdict_cache.lock` wrapping the entire RMW critical section. 10s timeout. New unit test for concurrent-write serialization. |
| CONCERN | Skeptic | `head_sha` vs comment-timestamp vs commit-history semantics for the approval-anchor SHA were not spelled out. Multiple ambiguous candidates. | Stale-review safe-shape exemption block (rationale paragraph) | Documented why `head_sha` from the trailer is the only stable anchor (force-push tolerance + idempotent-replay tolerance). Other candidates explicitly rejected. |
| CONCERN | Operator | Classifier's `--diff-from`/`--diff-to` mode requires both SHAs to be in the local objects DB, but `gh pr checkout` only fetches HEAD. No fetch step was specified. | Classifier CLI mode 2 + safe-shape exemption block | Added `git cat-file -e` precondition check + `git fetch origin <sha>` fallback in the safe-shape exemption block. Classifier exits 2 with clear stderr message if SHA missing. |
| CONCERN | User | Test-mapping glob `test_*{stem}*.py` over-matches for short stems (e.g., "db" matches 30+ files), defeating the targeted-test goal. | Solution → `map_to_tests` two-tier strategy + Risk 6 | Two-tier glob: exact `test_{stem}.py` first, substring fallback only for stems ≥4 chars and ≤8 hits. New unit test cases for short-stem fallback and over-match cap. |

---

## Open Questions

All six open questions from the issue body have been resolved by the recon and spike phases:

1. **Classifier location** — Settled: standalone `scripts/pr_shape_classify.py`. `scripts/baseline_gate.py` is comparison logic for one specific gate, not a general PR-classification surface.
2. **`pyproject.toml` paired with `uv.lock`** — Settled: `feature` shape. A `pyproject.toml` change can swap a runtime dep. `lockfile-only` is strictly the literal file `uv.lock`.
3. **Small-patch line budget** — Settled: N=20 net lines, no new files, no deletions. Conservative based on sample of recent merged PRs.
4. **Touched-file → test mapping** — Settled: hand-rolled `git diff --name-only` → `tests/**/test_{stem}.py` and `tests/**/test_*{stem}*.py` glob, with default-to-feature fallback when any touched file has zero matched tests. Not `pytest-testmon` (per-machine state conflicts with baseline philosophy).
5. **Cache storage** — Settled: flat JSON file at `data/pr_shape_verdict_cache.json`, LRU cap 100. No Popoto model.
6. **Baseline interaction** — Settled: cache stores `compute_gate_verdict()` output, keyed on `{pr}:{sha}:{baseline_hash[:12]}`. Baseline file unchanged. Baseline changes invalidate cache via key mismatch.

If the critic identifies new questions during war-room critique, they will be added here in a revision cycle.
