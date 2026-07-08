---
status: docs_complete
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1934
last_comment_id:
---

# Merge-gate shape classifier: admit `.env.example` into the docs-only allowlist

## Problem

The merge gate (`/do-merge`) runs a PR-shape classifier (`scripts/pr_shape_classify.py`) over each PR's diff. "Safe shapes" (`docs-only`, `lockfile-only`, `small-patch`) get fast-path treatment: a post-approval diff that classifies as a safe shape re-admits the prior review approval instead of forcing a fresh review round, and `docs-only` additionally skips the lockfile check and the full test suite.

`.env.example` is a documentation-only artifact: it is never loaded at runtime (the repo `.env` is a symlink to the iCloud vault; `.env.example` only documents which keys exist, one placeholder plus a comment line per key). A diff touching only `.env.example` cannot change program behavior. Yet `DOCS_ONLY_GLOBS` (`scripts/pr_shape_classify.py:58`) does not list it, so a pure `.env.example` addition classifies as `feature`.

**Current behavior:** During the PR #1930 endgame, a 6-line `.env.example` documentation addition (commit c431b80c) landed post-approval. The classifier called it a non-safe shape, the post-approval check refused to re-admit the existing APPROVED verdict, and the pipeline paid a full extra review round for a change with zero runtime surface — the run's only formal gap (the final commit merged without a fresh structured verdict). Live trace on `main`: `classify(['.env.example'], ...)` → `feature`.

**Desired outcome:** a pure `.env.example` diff classifies as `docs-only`, so post-approval doc additions to it re-admit the prior approval. Mixed diffs (`.env.example` alongside real code) stay guarded and continue to require normal review.

## Freshness Check

**Baseline commit:** f9013f37f18ba4d24dc70d3d2d7d1dd9df5b71e2
**Issue filed at:** 2026-07-07T05:41:10Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/pr_shape_classify.py:58` — issue claims `DOCS_ONLY_GLOBS` lacks `.env.example` — **still holds.** Actual tuple is `("docs/**", "docs/**/*", "**/*.md", "*.md", "CHANGELOG*", "README*")`; `.env.example` matches none.
- `scripts/pr_shape_classify.py:117` (`_matches_docs`) — early-returns `False` for `*.py`/`*.toml`/`*.lock`, then falls through to the globs — **still holds.** `.env.example` is not in the exclude list but matches no glob, so it lands in `feature`.
- `scripts/update/verify.py:980` (`check_env_completeness`) + `:941` (`_parse_env_example`) — completeness check — **still holds** (see Research / consideration 2).

**Cited sibling issues/PRs re-checked:**
- #1924 / PR #1930 (granite-pty-teardown endgame) — merged; this issue is its follow-up 4. No bearing on the classifier code.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since=<createdAt> -- scripts/pr_shape_classify.py tests/unit/test_pr_shape_classify.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** `merge-gate-baseline-stale-refresh.md` touches the merge-gate baseline system but not `pr_shape_classify.py`'s allowlist. No conflict — different files, different concern.

**Notes:** No drift. All file:line references accurate at baseline SHA.

## Prior Art

No prior issues or merged PRs touched `DOCS_ONLY_GLOBS` for `.env.example`. Searches (`gh issue list --state closed --search "shape classifier env.example docs-only"`, `gh pr list --state merged --search "pr_shape_classify DOCS_ONLY_GLOBS"`) returned nothing. The classifier itself was introduced by the pr-shape-aware-merge-gates work (see `docs/features/pr-shape-aware-merge-gates.md`); this is the first refinement to its docs allowlist.

## Research

Purely internal change — no external libraries, APIs, or ecosystem patterns involved. No WebSearch performed. The relevant investigation was in-codebase (consideration 2 below).

**Consideration 2 — does anything else catch a malformed `.env.example`?** Answered by reading `scripts/update/verify.py`:

- The `.env.example` completeness check is `check_env_completeness()` (`scripts/update/verify.py:980`). It runs **only during `/update`** (`scripts/update/run.py:1724`), per-machine, **downstream of merge**. It compares the local `.env`'s keys against the keys declared in `.env.example` and reports missing keys; it is not part of the merge gate at all.
- The check is **tolerant of a missing comment line**: `_parse_env_example()` (`:941`) defaults a key's description to `""` when no comment precedes it and never raises. So a "malformed" `.env.example` (a `KEY=` with no comment above it) is not a hard failure even in the `/update` check — it only degrades the human-readable description.
- **No merge-gate test or CI validates `.env.example` structure.** Therefore admitting `.env.example` to `docs-only` (and thereby skipping the full suite for pure-doc PRs) removes **zero** existing pre-merge coverage — there was never any to lose.

**Conclusion:** The risk raised in the issue is acceptable. A malformed `.env.example` has no runtime surface; the only structural check that exists runs post-merge at `/update` time on each machine and is itself non-fatal. Nothing about the `docs-only` fast-path degrades detection of a malformed `.env.example`, because that detection never happened pre-merge to begin with.

## Data Flow

Single-file, synchronous classification path:

1. **Entry point**: `/do-merge` invokes `python -m scripts.pr_shape_classify --pr N` (or `--diff-from/--diff-to`).
2. **Diff read**: `_read_diff_from_pr` / `_read_diff_from_shas` produce `(changed_files, net_lines, has_new, has_deleted)`.
3. **`classify()`**: step 1 partitions `changed_files` via `_matches_docs`. With `.env.example` in `DOCS_ONLY_GLOBS`, a pure `.env.example` diff has empty `docs_unmatched` → returns `docs-only`. A mixed diff (`.env.example` + `*.py`) has the `.py` file in `docs_unmatched` → falls through to `detect_mixed` → `mixed`.
4. **Output**: JSON `{"shape": ...}` on stdout; `/do-merge` reads `shape` and applies fast-path or full-gate logic.

The change touches only step 3's allowlist constant. No new data crosses any boundary.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none — `DOCS_ONLY_GLOBS` is a module constant; its shape and consumers are unchanged.
- **Coupling**: unchanged. `.env.example` is a single literal glob entry.
- **Data ownership**: unchanged.
- **Reversibility**: trivially reversible — delete one tuple entry.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue)
- Review rounds: 1 (single code review; the change is a one-line constant edit plus tests)

## Prerequisites

No prerequisites — this work has no external dependencies. The classifier is a pure-Python module tested with `pytest` against `tmp_path` fixtures.

## Solution

### Key Elements

- **`DOCS_ONLY_GLOBS` allowlist**: add the literal glob `.env.example` so `_matches_docs('.env.example')` returns `True`.
- **Mixed-shape guard (unchanged)**: `_matches_docs` still returns `False` for `*.py`/`*.toml`/`*.lock`, so `.env.example` + `config/settings.py` keeps the `.py` file as a disqualifier and `detect_mixed` classifies it `mixed`.
- **Tests**: add unit tests pinning both the pure-`.env.example` → `docs-only` path and the mixed → non-safe-shape path.

### Flow

Pure doc PR touching only `.env.example` → `/do-merge` classifies → **`docs-only`** → post-approval re-admits prior APPROVED verdict → merge without an extra review round.

Mixed PR (`.env.example` + `config/settings.py`) → classifies → **`mixed`** (claimed `docs-only`, disqualifier `config/settings.py`) → not a safe shape → normal review round applies.

### Technical Approach

- Append `".env.example"` to the `DOCS_ONLY_GLOBS` tuple in `scripts/pr_shape_classify.py`. `fnmatch(".env.example", ".env.example")` is an exact literal match, so it admits the file at any invocation without touching `_matches_docs`'s `.py`/`.toml`/`.lock` exclusion (which continues to guard mixed diffs).
- Do NOT use a broader glob like `.env*` — that would risk admitting an actual `.env` secrets file or `.env.local` into `docs-only`. Pin the literal filename only.
- Add a short comment on the new entry explaining why `.env.example` is doc-like (never loaded at runtime), matching the file's existing defended-constant comment style.
- Cover with tests (see Test Impact / Success Criteria).

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. The change is a single constant-tuple entry; `classify()` and `_matches_docs()` are pure and side-effect-free (except the small-patch `glob`, which this change does not touch). The module's documented contract is that it never raises on malformed input and defaults to `feature`.

### Empty/Invalid Input Handling
- Empty/whitespace file lists are already covered by `test_empty_file_list_returns_feature` and `test_whitespace_files_filtered`; this change does not alter that path. No new function is introduced, so no new empty-input surface.
- This feature does not process agent output — no silent-loop risk.

### Error State Rendering
- No user-visible output surface changes. The classifier emits JSON consumed by `/do-merge`; the added shape value (`docs-only`) is an existing, already-handled shape.

## Test Impact

- [ ] `tests/unit/test_pr_shape_classify.py::test_docs_only_happy_path` — no change; still valid (existing docs globs untouched).
- [ ] `tests/unit/test_pr_shape_classify.py` — ADD `test_docs_only_env_example`: `classify(['.env.example'], net_lines=6, has_new=False, has_deleted=False)` → `shape == "docs-only"`, `allowlist_used == "docs-only"`.
- [ ] `tests/unit/test_pr_shape_classify.py` — ADD `test_env_example_plus_py_is_mixed_not_safe`: `classify(['.env.example', 'config/settings.py'], ...)` → `shape == "mixed"`, `claimed_shape == "docs-only"`, `'config/settings.py' in disqualifiers` (confirms consideration 1 — mixed diffs stay guarded).
- [ ] (Optional) `tests/unit/test_pr_shape_classify.py` — ADD `test_partition_env_example`: `partition_by_allowlist(['.env.example', 'agent/y.py'], 'docs-only')` → `matched == ['.env.example']`, `unmatched == ['agent/y.py']`.

No existing tests are modified or deleted — the change is purely additive to the docs allowlist and does not alter any currently-classified shape.

## Rabbit Holes

- **Do not broaden to `.env*` or add secrets-file heuristics.** Pin the literal `.env.example`. Any pattern that could match a real `.env` / `.env.local` is a security-adjacent trap.
- **Do not add an `.env.example` structural validator to the merge gate.** The issue's consideration 2 concludes no pre-merge structural check exists and none is warranted; adding one is scope creep for a separate issue if ever desired.
- **Do not refactor `_matches_docs` / `detect_mixed`.** The existing `.py`/`.toml`/`.lock` exclusion already produces the correct mixed-guard behavior; leave it.

## Risks

### Risk 1: A broader glob accidentally admits a real secrets file
**Impact:** If someone later widens the entry to `.env*`, a PR touching an actual `.env` or `.env.local` could be fast-pathed as `docs-only`, skipping the test suite on a runtime-affecting change.
**Mitigation:** Pin the exact literal `".env.example"`. Add an inline comment forbidding widening. The mixed-guard test also documents intent.

### Risk 2: Mixed diffs silently fast-pathed
**Impact:** If `.env.example` + code were classified `docs-only`, a real config change could skip review.
**Mitigation:** `_matches_docs` returns `False` for `*.py`; the added mixed test (`test_env_example_plus_py_is_mixed_not_safe`) asserts `mixed`. Verified live: `classify(['.env.example', 'config/settings.py'], ...)` → `mixed`.

## Race Conditions

No race conditions identified — the classifier is fully synchronous and single-threaded, operating on an in-memory file list. No shared mutable state, no async, no cross-process data flow.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing deferred — every relevant item is in scope for this plan. (No separate issue is warranted; the merge-gate structural-validator idea is explicitly declined in Rabbit Holes, not deferred.)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a change to a pure-Python classifier constant consumed by `/do-merge`. No new dependencies, config files, or migrations. `scripts/update/verify.py`'s `check_env_completeness` is unrelated and unchanged. No Popoto models are touched, so no `scripts/update/migrations.py` entry is needed.

## Agent Integration

No agent integration required — `scripts/pr_shape_classify.py` is invoked by the `/do-merge` skill via `python -m scripts.pr_shape_classify`, an existing surface. No new CLI entry point in `pyproject.toml [project.scripts]`, no `.mcp.json` change, and no bridge import. The change is internal to an already-wired merge-gate tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pr-shape-aware-merge-gates.md`: note that `.env.example` is admitted to the `docs-only` shape (add it to the DOCS_ONLY_GLOBS description / gate matrix), with the one-line rationale (never loaded at runtime) and the mixed-guard caveat.

### External Documentation Site
- Not applicable — this repo has no external docs site for the merge-gate internals.

### Inline Documentation
- [ ] Add an inline comment on the new `DOCS_ONLY_GLOBS` entry explaining why `.env.example` is doc-like and why the literal (not `.env*`) is used.

## Success Criteria

- [ ] `classify()` returns `docs-only` for a diff touching only `.env.example` (new test `test_docs_only_env_example` passes).
- [ ] A diff touching `.env.example` + any `.py` file does NOT classify as a safe shape — it returns `mixed` (new test `test_env_example_plus_py_is_mixed_not_safe` passes).
- [ ] Consideration 2 (completeness-check coverage) is answered in this plan's Research section: the check runs only at `/update` time, is non-fatal on missing comments, and no merge-gate coverage is lost — risk accepted.
- [ ] `DOCS_ONLY_GLOBS` contains the literal `.env.example` (not a `.env*` wildcard).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — `docs/features/pr-shape-aware-merge-gates.md`.

## Team Orchestration

Single small change; the lead deploys one builder and one validator.

### Team Members

- **Builder (classifier)**
  - Name: classifier-builder
  - Role: Add `.env.example` to `DOCS_ONLY_GLOBS` and the three unit tests; update the feature doc.
  - Agent Type: builder
  - Resume: true

- **Validator (classifier)**
  - Name: classifier-validator
  - Role: Verify the new tests pass, the mixed guard holds, and the literal (not wildcard) is used.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier 1 agents (`builder`, `validator`) suffice; no domain framing needed.

## Step by Step Tasks

### 1. Add `.env.example` to the docs allowlist and tests
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: `tests/unit/test_pr_shape_classify.py`
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: false
- Append `".env.example"` to `DOCS_ONLY_GLOBS` in `scripts/pr_shape_classify.py:58`, with an inline comment (doc-like, never loaded at runtime; literal not `.env*`).
- Add `test_docs_only_env_example` (pure `.env.example` → `docs-only`).
- Add `test_env_example_plus_py_is_mixed_not_safe` (`.env.example` + `config/settings.py` → `mixed`, disqualifier present).
- Add `test_partition_env_example` (optional partition-level assertion).

### 2. Update feature documentation
- **Task ID**: document-feature
- **Depends On**: build-classifier
- **Assigned To**: classifier-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pr-shape-aware-merge-gates.md` to list `.env.example` under the `docs-only` allowlist with rationale and the mixed-guard caveat.

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-classifier, document-feature
- **Assigned To**: classifier-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pr_shape_classify.py -q` — all pass.
- Confirm `DOCS_ONLY_GLOBS` contains the literal `.env.example` and NOT a `.env*` wildcard.
- Confirm the mixed test asserts `shape == "mixed"`.
- Run `python -m ruff check scripts/pr_shape_classify.py tests/unit/test_pr_shape_classify.py`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Classifier tests pass | `pytest tests/unit/test_pr_shape_classify.py -q` | exit code 0 |
| `.env.example` in docs allowlist | `grep -c '"\.env\.example"' scripts/pr_shape_classify.py` | output > 0 |
| No `.env*` wildcard leak | `grep -c '\.env\*' scripts/pr_shape_classify.py` | match count == 0 |
| Pure `.env.example` is docs-only | `python -c "from pathlib import Path; import scripts.pr_shape_classify as m; print(m.classify(['.env.example'],6,False,False,repo_root=Path('.')).shape)"` | output contains docs-only |
| Mixed `.env.example`+py is not safe | `python -c "from pathlib import Path; import scripts.pr_shape_classify as m; print(m.classify(['.env.example','config/settings.py'],20,False,False,repo_root=Path('.')).shape)"` | output contains mixed |
| Lint clean | `python -m ruff check scripts/pr_shape_classify.py tests/unit/test_pr_shape_classify.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

None. The issue fully specifies scope; consideration 1 (mixed guard) and consideration 2 (completeness-check coverage) are both resolved in this plan with live-verified evidence. Proceed to critique/build.
