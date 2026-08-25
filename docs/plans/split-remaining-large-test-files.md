---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-08-25
tracking: https://github.com/tomcounsell/ai/issues/2879
last_comment_id:
---

# Split the remaining four large test files (#2879, part 2 of 2)

## Problem

`tests/unit/` still contains four oversized test modules that mirror the size of
the production monoliths they cover. PR #2941 split the first two of the six
files named in #2879 and **deliberately deferred these four**, each for a named
reason — shared module state, an autouse fixture, or a filesystem-depth
expression that a move would break.

**Current behavior:**

| Lines | File | Collected tests | Marker |
|---|---|---|---|
| 3171 | `tests/unit/test_sdlc_session_ensure.py` | 114 | `sdlc` |
| 2096 | `tests/unit/test_sdlc_router_decision.py` | 107 | `sdlc` |
| 2016 | `tests/unit/test_valor_telegram.py` | 97 | `messaging` |
| 1610 | `tests/unit/test_worktree_manager.py` | 107 | `git` |

Localizing a failure means paging through thousands of lines, and
`--dist=loadfile` pins each whole file to one xdist worker.

**Desired outcome:**
All four are packages of sub-800-line modules. Collection count, per-test
markers, and test bodies are unchanged.

## Freshness Check

**Baseline commit:** `483c7cd14`
**Issue filed at:** 2026-08-17 (pre-#2941)
**Disposition:** Minor drift — partial completion, no premise change.

**Issue claims re-verified against the working tree:**

- `test_output_handler.py` (3,253 lines) — **gone**, now `tests/unit/output_handler/` (PR #2941).
- `test_memory_extraction.py` (2,638 lines) — **gone**, now `tests/unit/memory_extraction/` (PR #2941).
- The four line counts in the table above — all four re-measured with `wc -l`, **exact match** to the issue's figures. No drift.
- `tests/unit/test_worktree_manager.py:1268` `Path(__file__).resolve().parents[2]` — still present.
- `tests/unit/test_valor_telegram.py:12` `sys.path.insert(...parent.parent.parent)` — still present.
- `tests/unit/test_sdlc_session_ensure.py:28` `REPO_ROOT = dirname(dirname(dirname(...)))` — still present.
- `tests/unit/test_sdlc_session_ensure.py:33` autouse `_stub_lane_identity` — still present, still class-name-conditional on `TestLaneSlugMintedAtLaneStart`.

**Cited sibling issues/PRs re-checked:**

- **#2941** — MERGED. Split the other two files and wrote the convention this plan follows
  (`tests/README.md` §"Splitting or Renaming a Test File"). Its PR body enumerates the exact
  deferral reason for each of my four files; those reasons are the risk register below.
- **#2946** — the scoped child issue #2941 closed. #2879 stayed open for these four, by design.
- **#2805 / PR #2958** (`d59f6509c`) — deleted the line-number-keyed `ALLOWLIST` guard that used
  to break whenever line counts shifted. **Confirmed absent**: `grep -rn "ALLOWLIST" tests/ tools/`
  returns nothing line-keyed. This is what makes the split safe to attempt now; the PM's
  standing instruction is to stop and report if anything line-number-keyed reappears.

**Commits on main since #2941 touching the four target files:** none.

**Active overlapping plans:** Lane #2875 is in flight and owns `agent/`, `reflections/`,
`config/`, `scripts/`. This plan owns `tests/unit/` only. Overlap risk is confined to that lane
needing to edit assertions inside one of my four files; see Risks.

## Prior Art

- **PR #2941** — "test: split test_output_handler and test_memory_extraction by theme". Merged.
  Established: theme-grouping over strict per-class; original basename kept as a **prefix**;
  AST source-segment equality as the "no body changed" proof; per-marker histogram parity as
  the verification that a total-count check cannot provide. **This plan reuses all four.**
- **`tests/README.md` §"Splitting or Renaming a Test File"** — the durable writeup #2941 left
  behind. It is a procedure, not just prose, and this plan follows it step for step.

## Research

No relevant external findings — this is a purely internal reorganization of a
first-party test suite. Proceeding on codebase context.

## Spike Results

### spike-1: Does the basename→marker coupling actually bite these four files?
- **Assumption**: "Keeping the original basename as a prefix is sufficient to preserve markers."
- **Method**: code-read + executable check against the parsed `FEATURE_MAP`.
- **Finding**: **Sufficient, but not automatically — it depends on suffix choice.** `FEATURE_MAP`
  is iterated in dict-insertion order and **breaks on first hit**. `worktree_manager` sits at
  position ~68, *after* `config`, `lifecycle`, `checkpoint`, `reflection`, `routing`. So:
  - `test_worktree_manager_config.py` → tagged **`config`**, not `git`.
  - `test_worktree_manager_lifecycle.py` → tagged **`sessions`**, not `git`.
  Both silently, with the collection total unchanged. The five suffixes chosen below were all
  run through the checker and confirmed to resolve to `git`.
- **Confidence**: high (mechanically verified, not reasoned).
- **Impact on plan**: adds a mandatory naming gate — every candidate basename is run through
  the checker *before* the file is written, not after.

### spike-2: Does splitting break `--dist=loadfile` safety for `test_sdlc_session_ensure.py`?
- **Assumption**: "Real `PipelineLedger` Redis writes at a fixed key make this file unsafe to split
  across workers" (#2941's stated deferral reason).
- **Method**: code-read of every ledger reference in the file.
- **Finding**: **The hazard is real but confined to one class.** The autouse `_stub_lane_identity`
  patches `resolve_lane_slug` to `None` for *every* class except `TestLaneSlugMintedAtLaneStart`,
  which is the sole class that performs real ledger writes (at the fixed key
  `{_TEST_REPO}:{_ISSUE}`). Every other class is fully mocked and writes nothing.
  Therefore: keeping `TestLaneSlugMintedAtLaneStart` **intact inside a single file** means no two
  files ever contend for that key, and `--dist=loadfile` continues to provide the same guarantee
  it does today. No `xdist_group` marker is needed.
- **Confidence**: high.
- **Impact on plan**: `TestLaneSlugMintedAtLaneStart` is pinned to one file and must not be
  divided; the autouse fixture moves to a package `conftest.py` so its class-name condition keeps
  working identically.

### spike-3: Is the `_git` helper a real collision with `tests/unit/conftest.py`?
- **Assumption**: "`_git` in `test_worktree_manager.py:1503` collides with `tests/unit/conftest.py:10`"
  (#2941's stated deferral reason).
- **Method**: code-read.
- **Finding**: **Not a collision.** `tests/unit/conftest.py:10` `_git` is a plain module-level
  function, not a fixture, and is referenced only within that conftest. Python module namespacing
  keeps the two entirely separate; there is no fixture-resolution path between them.
- **Confidence**: high.
- **Impact on plan**: removes a blocker #2941 listed. The helper simply travels with its class.

### spike-4: How many depth-sensitive expressions actually break on a move into a subdirectory?
- **Assumption**: "Depth breakage is pervasive and makes these files unsafe to relocate."
- **Method**: `grep` for `__file__`, `parents[`, `parent.parent`, `dirname(dirname(` across all four.
- **Finding**: **Exactly three sites, only one of them inside a test body.**
  - `test_sdlc_session_ensure.py:28` `REPO_ROOT` — module header.
  - `test_valor_telegram.py:12` `sys.path.insert` — module header.
  - `test_worktree_manager.py:1268` `parents[2]` — **inside `TestInterpreterPinResolution.test_this_repo_ships_a_committed_pin`**.
  `test_sdlc_router_decision.py` has none.
- **Confidence**: high.
- **Impact on plan**: the two module-header sites are import-header edits, which the issue's
  "move imports and fixtures only" constraint already permits. The single in-body site is the
  one and only deliberate byte-level deviation in the whole change, and is called out as such
  in Verification and in the PR body.

## Data Flow

Not applicable — no runtime data flow changes. The only flow that moves is
pytest's collection path: `rootdir → testpaths → tests/unit/**` → per-item
nodeid → `pytest_collection_modifyitems` → basename-derived marker. That last
hop is the one this change perturbs, and it is the subject of the verification.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. No production code is touched at all.
- **Coupling**: reduced. Four monoliths become 20 focused modules.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — `git revert`. No migration, no state.

## Appetite

**Size:** Medium

**Team:** Solo dev + 4 parallel builder subagents + 1 code reviewer

**Interactions:**
- PM check-ins: 1 (final report)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Provisioned worktree venv on the repo pin | `.venv/bin/python --version` matches `.python-version` | `scripts/pytest-clean.sh` aborts on an off-pin venv |
| Baseline marker dump captured | `wc -l < baseline.tsv` == 14065 | Parity proof requires a pre-split snapshot |

## Solution

### Key Elements

- **Four packages**, one per target file, following #2941's shape:
  `tests/unit/sdlc_session_ensure/`, `sdlc_router_decision/`, `valor_telegram/`, `worktree_manager/`.
- **Basename-prefix naming**, mechanically gated against `FEATURE_MAP` before any file is written.
- **Package-local `conftest.py`** for each package that had a module-level autouse fixture,
  so the fixture keeps applying to exactly the tests it applied to before — and no others.
- **Byte-identical bodies**, proven by AST source-segment comparison, with exactly one
  documented exception (`parents[2]` → `parents[3]`).

### Flow

`tests/unit/test_X.py` (monolith) → group classes by theme → write
`tests/unit/X/test_X_{theme}.py` + `__init__.py` + `conftest.py` → delete the
monolith → re-collect → diff nodeid+marker multiset against baseline → zero drift.

### Technical Approach

**1. `test_sdlc_session_ensure.py` (3171 → 6 files) → `tests/unit/sdlc_session_ensure/`**

| New file | Classes | ~lines |
|---|---|---|
| `test_sdlc_session_ensure_core.py` | `TestEnsureSession`, `TestCLI`, `TestCreateLocalMessageText` | ~340 |
| `test_sdlc_session_ensure_short_circuit.py` | `TestBridgeShortCircuit`, `TestEnvShortCircuitIdentifierMismatch` | ~575 |
| `test_sdlc_session_ensure_adoption.py` | `TestOwnerlessAdoption`, `TestLaneSlugMintedAtLaneStart`, `_make_orphan_session`, `TestKillOrphans` | ~750 |
| `test_sdlc_session_ensure_issue_lock.py` | `TestIssueLockWiring`, `TestBindDoesNotRestampUpdatedAt` | ~540 |
| `test_sdlc_session_ensure_run_identity.py` | `TestVerifiedRunIdReuse`, `TestSupervisedRunSignal`, `TestSupervisedRunModule`, `TestOwnedRunIdsSelfRecognition` | ~570 |
| `test_sdlc_session_ensure_lease_identity.py` | `TestLeaseHeartbeatSpawnIdentity`, `TestDurableRunIdentityFourthProof`, `TestRunIdentityAnchorWriteOnLeaseConfirmation` | ~350 |

- **Naming trap**: do NOT name the short-circuit file `..._bridge_short_circuit.py`. `bridge`
  is the very first key in `FEATURE_MAP` and maps to `messaging` — it would strip `sdlc` off
  those tests. The class is `TestBridgeShortCircuit`; the file must not echo it.
- `_stub_lane_identity` + `_LANE_IDENTITY_CLASS` move verbatim into the package `conftest.py`.
  Its `request.cls.__name__ == "TestLaneSlugMintedAtLaneStart"` check is name-based, so it keeps
  discriminating correctly no matter which file the class lands in.
- `REPO_ROOT` gains one `os.path.dirname()` level in each file that uses it.
- `TestLaneSlugMintedAtLaneStart` stays whole and in one file (spike-2).

**2. `test_sdlc_router_decision.py` (2096 → 4 files) → `tests/unit/sdlc_router_decision/`**

| New file | Contents | ~lines |
|---|---|---|
| `test_sdlc_router_decision_dispatch_rows.py` | `_states_all_pending`, `TestDispatchRulesTable`, rows 1–10, `TestStageStatesUnavailable…`, `TestNoMatchingRule`, `TestVerdictNormalizationUnderscore`, `TestPlanExistenceGate` | ~505 |
| `test_sdlc_router_decision_verdict_staleness.py` | `_iso`, `TestReviewVerdictStaleness`, `TestCritiqueVerdictStaleness` | ~275 |
| `test_sdlc_router_decision_convergence.py` | `TestConvergenceLatchRevisionAppliedAt` through `TestNeedsRevisionInvalidatedByRevision`, plus the `_VERDICT_AT`/`_post_patch_states` block and its row-8b classes | ~965 |
| `test_sdlc_router_decision_with_concerns.py` | `_WC`/`_PLAN_HASH`/`_wc_states`/`_wc_meta` and `TestG5AliveOnWithConcerns` → `TestForeverWithConcernsTerminates` | ~350 |

- The three module-level constant blocks (`_VERDICT_AT`, `_WC`, …) each travel with the classes
  that consume them. Verify no cross-group consumer before splitting — if one exists, the group
  boundary moves rather than the constant being duplicated.
- Easiest of the four: no autouse fixtures, no depth expressions, no Redis.
- If `_convergence` lands over 800 lines, split it on the `_post_patch_states` boundary into
  `..._convergence.py` and `..._post_patch.py` (both verified `sdlc`).

**3. `test_valor_telegram.py` (2016 → 5 files) → `tests/unit/valor_telegram/`**

| New file | Classes | ~lines |
|---|---|---|
| `test_valor_telegram_parsing.py` | `TestParseSince`, `TestResolveChat`, `TestFormatTimestamp`, `TestCLIParsing`, `TestFormatRelativeAge` | ~215 |
| `test_valor_telegram_cli_send.py` | `TestCmdSend` | ~340 |
| `test_valor_telegram_cli_read.py` | `_CandidateStub`, `TestCmdReadFlags`, `TestCmdReadArgparseMutex`, `TestCmdReadProject` | ~660 |
| `test_valor_telegram_cli_chats.py` | `TestCmdChatsSearch`, `TestCmdChatsProject` | ~280 |
| `test_valor_telegram_rtr.py` | `TestShouldRunRTRSecondaryConsumer`, `TestCmdSendRTR`, `TestValorTelegramPromiseGate` | ~550 |

- **The three existing siblings move into the package too** — `test_valor_telegram_await.py`,
  `test_valor_telegram_chat_log.py`, `test_valor_telegram_voice_flag.py`. They already carry the
  basename prefix so their markers are unaffected by the move (directory contributes nothing),
  and leaving them stranded outside a package named for the same module is the kind of
  half-migration this repo forbids. **They are moved unmodified** — pure `git mv`.
- `_bypass_promise_gate` moves verbatim to the package `conftest.py`. It is unconditional autouse,
  so hoisting it to a package conftest reproduces its current scope exactly — provided the three
  moved siblings do not break under it. **Check before moving**: if any sibling currently relies on
  the *real* promise gate, the fixture cannot be hoisted package-wide and must instead be
  duplicated into the five new files only. This is the single highest-risk judgment call in the
  plan and must be resolved by reading the siblings, not assumed.
- `_CandidateStub` travels with `TestCmdReadFlags`, its only consumer.
- The `sys.path.insert` at L12 is a module header; it gains one `.parent`.

**4. `test_worktree_manager.py` (1610 → 5 files) → `tests/unit/worktree_manager/`**

| New file | Classes | ~lines |
|---|---|---|
| `test_worktree_manager_cleanup.py` | `TestValidateSlug`, `TestCleanupAfterMerge`, `TestFindWorktreeForBranch`, `TestCleanupStaleWorktree` | ~305 |
| `test_worktree_manager_creation.py` | `TestCreateWorktreeStaleRecovery`, `TestGetOrCreateWorktree`, `_make_session` | ~185 |
| `test_worktree_manager_busy_guards.py` | `TestWorktreeBusyCheck` → `TestCleanupAfterMergeBusyBlock` (7 classes) | ~385 |
| `test_worktree_manager_venv_provisioning.py` | `_init_git_worktree`, `TestVerifyWorktreeBranch`, `TestInterpreterPinResolution`, `TestProvisionWorktreeVenv`, `TestCreateWorktreeProvisioningWiring` | ~440 |
| `test_worktree_manager_uncommitted.py` | `_init_git_repo`, `_add_linked_worktree`, `_dirty`, `_git`, `TestPreserveUncommittedChanges` | ~150 |

- **The one in-body edit in the entire change** lives here: `parents[2]` → `parents[3]` at what is
  currently L1268, inside `TestInterpreterPinResolution.test_this_repo_ships_a_committed_pin`.
  Without it the test resolves `repo_root` to `tests/` and fails looking for `.python-version`.
- Every suffix above was run through the `FEATURE_MAP` checker and resolves to `git`.
  `_config`, `_lifecycle`, `_checkpoint`, `_routing` are **forbidden** here (spike-1).
- The shared helpers are consumed only by adjacent classes; each travels with its consumers.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this change adds no code. The moved test bodies retain
whatever handlers they already had, byte for byte.

### Empty/Invalid Input Handling
Not applicable — no functions are added or modified.

### Error State Rendering
Not applicable — no user-visible output.

The real "failure path" for this change is **silent verification failure**: a split that passes
`--collect-only` totals while quietly re-tagging tests. That is precisely what the marker-parity
diff in Verification exists to catch, and it is treated as the primary correctness gate rather
than an afterthought.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py` — REPLACE: becomes `tests/unit/sdlc_session_ensure/` (6 modules + `conftest.py` + `__init__.py`). All 114 tests preserved.
- [ ] `tests/unit/test_sdlc_router_decision.py` — REPLACE: becomes `tests/unit/sdlc_router_decision/` (4 modules + `__init__.py`). All 107 tests preserved.
- [ ] `tests/unit/test_valor_telegram.py` — REPLACE: becomes `tests/unit/valor_telegram/` (5 modules + `conftest.py` + `__init__.py`). All 97 tests preserved.
- [ ] `tests/unit/test_valor_telegram_await.py` — UPDATE: `git mv` into the package, contents unmodified.
- [ ] `tests/unit/test_valor_telegram_chat_log.py` — UPDATE: `git mv` into the package, contents unmodified.
- [ ] `tests/unit/test_valor_telegram_voice_flag.py` — UPDATE: `git mv` into the package, contents unmodified.
- [ ] `tests/unit/test_worktree_manager.py` — REPLACE: becomes `tests/unit/worktree_manager/` (5 modules + `__init__.py`). All 107 tests preserved.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py::TestInterpreterPinResolution::test_this_repo_ships_a_committed_pin` — UPDATE: `parents[2]` → `parents[3]`. The only body edit in the change.
- [ ] `tests/README.md` — UPDATE: replace the four index rows with the twenty new ones.

No test **logic** is added, removed, or altered. No assertions change.

## Rabbit Holes

- **Strict one-file-per-class.** The issue's original phrasing. #2941 already rejected it and
  shipped theme-grouping; `test_sdlc_router_decision.py` alone would yield 37 files averaging
  56 lines. Follow the landed convention.
- **Chasing a parallelism speedup.** #2941 explicitly made no timing claim, and neither does this.
  Wall time is bounded by out-of-scope integration files. Do not take timing baselines, do not
  argue about worker counts.
- **"Improving" the tests while moving them.** Dead imports, duplicated setup, and awkward names
  will be visible and tempting. Removing genuinely-unused imports via `ruff --select F401` is in
  scope (it never touches a body); anything else is not.
- **Editing the `--dist=loadfile` comment in `pyproject.toml`.** It names three *integration*
  files that share global resources. None of my four are among them, and spike-2 shows the split
  introduces no new cross-file contention. Touching it would be a manufactured change. Leave it.
- **Hoisting the autouse fixtures higher than a package conftest.** A `tests/unit/conftest.py`
  addition would silently apply to all 14,065 unit tests.

## Risks

### Risk 1: A file is silently re-tagged by `FEATURE_MAP`
**Impact:** Tests still run in a full sweep but vanish from `pytest -m sdlc` / `-m git` /
`-m messaging`. Invisible to a collection-count check. This is the single most likely way this
change goes wrong, and spike-1 proved two natural-looking names would trigger it.
**Mitigation:** Every candidate basename goes through the `FEATURE_MAP` checker before the file
is written. Then the full per-marker histogram is diffed before/after; `sdlc` must stay 2577,
`git` 179, `messaging` 990, and the nodeid+marker multiset must be identical.

### Risk 2: An autouse fixture changes scope when hoisted to a package conftest
**Impact:** Either tests start hitting the real promise gate / real lane resolution (slow, or
live LLM calls), or the three moved `valor_telegram` siblings get a fixture they never had.
**Mitigation:** Read the three siblings before moving them. If any depends on the unmocked gate,
duplicate the fixture into the five new files instead of hoisting. The `_stub_lane_identity`
condition is class-name-based, so it is inherently move-safe.

### Risk 3: Lane #2875 edits one of my four files concurrently
**Impact:** A conflict against a file I have deleted and replaced with a directory is
unpleasant to resolve and easy to resolve *wrongly* — losing that lane's assertion change.
**Mitigation:** These four files are `tests/unit/` only and #2875 owns `agent/`, `reflections/`,
`config/`, `scripts/`. Per the PM's instruction, a conflict on anything outside my four targets
is **flagged, not resolved unilaterally**. Rebase before opening the PR to surface it early.

### Risk 4: A moved module-level constant loses a consumer
**Impact:** `NameError` at import, caught immediately by collection.
**Mitigation:** Collection itself is the gate — a missing name fails `--collect-only` loudly,
long before the test run. Low severity precisely because it cannot be silent.

## Race Conditions

No race conditions identified in the change itself — it is a static file reorganization with no
runtime component.

One adjacent concurrency property is *preserved rather than introduced*, and is worth stating
because it was #2941's stated reason for deferring the largest file: `--dist=loadfile` pins a
file's tests to one worker, so splitting a file can expose intra-file shared state to
cross-worker concurrency. spike-2 established that the only real shared state among these four
(the fixed-key `PipelineLedger` writes in `test_sdlc_session_ensure.py`) is confined to a single
class, which stays intact in a single file. No new cross-worker contention is created and no
`xdist_group` marker is required.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2879] The other 16 files with >80 test functions each. #2879 names six
  specifically; this plan finishes those six and closes it. The broader "20 files" observation
  in the issue body is context, not a requirement, and is not attempted here.
- [SEPARATE-SLUG #2879] A permanent regression test asserting that every `tests/unit/**` basename
  resolves to its intended `FEATURE_MAP` marker. spike-1 shows this guard has real value and the
  checker already exists as a throwaway script — but it is **new test logic**, which the issue's
  mechanical-reorganization constraint forbids. Called out in the PR body so it can be picked up
  deliberately rather than smuggled in here.
- No production code is touched. No `pyproject.toml` change. No new fixtures beyond relocating
  the two that already exist.

## Update System

No update system changes required — this is a test-suite-internal reorganization. No new
dependencies, no config files, no migration. `/update` propagates it as an ordinary commit.

## Agent Integration

No agent integration required — no CLI entry point, no MCP surface, no bridge import. This
change is invisible to the running system; it only moves test files on disk.

## Documentation

- [ ] Update `tests/README.md` — replace the four index rows for the split files with the twenty
      new rows, each carrying its real collected-test count.
- [ ] Confirm `tests/README.md` §"Splitting or Renaming a Test File" (added by #2941) still reads
      correctly after a second application; extend it only if this split surfaced a step it
      omits — specifically the **ordering-dependence** of `FEATURE_MAP` that spike-1 found, which
      the current text does not mention.
- [ ] No `docs/features/` change — no feature behavior changes.

## Success Criteria

- [ ] All four target files are gone, replaced by four packages.
- [ ] Every resulting file is under 800 lines.
- [ ] `pytest tests/unit --collect-only` reports exactly **14065**, unchanged.
- [ ] The nodeid+marker multiset is **identical** before and after (not just the total).
- [ ] Per-marker counts unchanged: `sdlc` 2577, `git` 179, `messaging` 990, and all 20 others.
- [ ] AST source-segment comparison shows every moved definition byte-identical, with exactly
      one documented exception (`parents[3]`).
- [ ] The four packages pass under `scripts/pytest-clean.sh`: 425 tests (114+107+97+107).
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] `tests/README.md` index updated.

## Team Orchestration

Four independent builders, one per target file, with **disjoint file sets** — no two builders
touch the same path, so their commits cannot interleave. All four run in the single lane
worktree `.worktrees/split-remaining-test-files/`.

### Team Members

- **Builder (session-ensure)** — Name: `split-session-ensure`, Agent Type: `builder`, Resume: true
- **Builder (router-decision)** — Name: `split-router-decision`, Agent Type: `builder`, Resume: true
- **Builder (valor-telegram)** — Name: `split-valor-telegram`, Agent Type: `builder`, Resume: true
- **Builder (worktree-manager)** — Name: `split-worktree-manager`, Agent Type: `builder`, Resume: true
- **Reviewer** — Name: `split-reviewer`, Agent Type: `code-reviewer`, Resume: true

## Step by Step Tasks

### 1. Split `test_sdlc_session_ensure.py`
- **Task ID**: build-session-ensure
- **Depends On**: none
- **Validates**: tests/unit/sdlc_session_ensure/
- **Informed By**: spike-1 (no `_bridge`/`_config`/`_lifecycle` suffixes), spike-2 (keep `TestLaneSlugMintedAtLaneStart` whole), spike-4 (`REPO_ROOT` +1 level)
- **Assigned To**: split-session-ensure
- **Agent Type**: builder
- **Parallel**: true
- Create the package, `__init__.py`, and `conftest.py` holding `_stub_lane_identity` verbatim.
- Move classes into the 6 files per the table; adjust `REPO_ROOT` depth; delete the monolith.

### 2. Split `test_sdlc_router_decision.py`
- **Task ID**: build-router-decision
- **Depends On**: none
- **Validates**: tests/unit/sdlc_router_decision/
- **Informed By**: spike-4 (no depth expressions in this file)
- **Assigned To**: split-router-decision
- **Agent Type**: builder
- **Parallel**: true
- Create the package and 4 files per the table; verify each module-level constant block travels
  with all of its consumers.

### 3. Split `test_valor_telegram.py`
- **Task ID**: build-valor-telegram
- **Depends On**: none
- **Validates**: tests/unit/valor_telegram/
- **Informed By**: spike-4 (`sys.path.insert` +1 level), Risk 2 (verify siblings before hoisting)
- **Assigned To**: split-valor-telegram
- **Agent Type**: builder
- **Parallel**: true
- **First** read the three existing siblings and decide hoist-vs-duplicate for `_bypass_promise_gate`.
- Create the package and 5 files; `git mv` the three siblings in unmodified.

### 4. Split `test_worktree_manager.py`
- **Task ID**: build-worktree-manager
- **Depends On**: none
- **Validates**: tests/unit/worktree_manager/
- **Informed By**: spike-1 (`_config`/`_lifecycle` forbidden), spike-3 (`_git` is not a collision), spike-4 (`parents[2]`→`parents[3]`)
- **Assigned To**: split-worktree-manager
- **Agent Type**: builder
- **Parallel**: true
- Create the package and 5 files per the table; make the single in-body depth edit.

### 5. Marker + collection parity verification
- **Task ID**: validate-parity
- **Depends On**: build-session-ensure, build-router-decision, build-valor-telegram, build-worktree-manager
- **Assigned To**: split-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Re-run the collect-only marker dump; diff the nodeid+marker multiset against the baseline.
- Run the AST source-segment equality check across all four original files.

### 6. Documentation
- **Task ID**: document-split
- **Depends On**: validate-parity
- **Assigned To**: split-reviewer
- **Agent Type**: documentarian
- **Parallel**: false
- Update the `tests/README.md` index; extend the split procedure with the `FEATURE_MAP`
  ordering-dependence finding.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-split
- **Assigned To**: split-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run the four packages under `scripts/pytest-clean.sh`; confirm 425 passed.
- Confirm every success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Collection count unchanged | `.venv/bin/python -m pytest tests/unit --collect-only -q 2>/dev/null \| tail -1 \| grep -oE '^[0-9]+'` | output contains 14065 |
| Monoliths are gone | `ls tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_router_decision.py tests/unit/test_valor_telegram.py tests/unit/test_worktree_manager.py 2>&1` | exit code != 0 |
| No file over 800 lines | `find tests/unit/sdlc_session_ensure tests/unit/sdlc_router_decision tests/unit/valor_telegram tests/unit/worktree_manager -name '*.py' -exec wc -l {} + \| awk '$1>800 && $2!="total"' \| wc -l \| tr -d ' '` | output contains 0 |
| `sdlc` marker count held | `.venv/bin/python -m pytest tests/unit -m sdlc --collect-only -q 2>/dev/null \| tail -1 \| grep -oE '^[0-9]+'` | output contains 2577 |
| `git` marker count held | `.venv/bin/python -m pytest tests/unit -m git --collect-only -q 2>/dev/null \| tail -1 \| grep -oE '^[0-9]+'` | output contains 179 |
| `messaging` marker count held | `.venv/bin/python -m pytest tests/unit -m messaging --collect-only -q 2>/dev/null \| tail -1 \| grep -oE '^[0-9]+'` | output contains 990 |
| No forbidden `worktree_manager` suffix | `ls tests/unit/worktree_manager/ \| grep -cE '_(config\|lifecycle\|checkpoint\|routing\|search)\.py$'` | match count == 0 |
| No `bridge` in a session-ensure filename | `ls tests/unit/sdlc_session_ensure/ \| grep -c bridge` | match count == 0 |
| Lint clean | `python -m ruff check tests/unit` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit` | exit code 0 |
| No line-number-keyed exemption reintroduced | `grep -rn 'ALLOWLIST' tests/ tools/ \| wc -l \| tr -d ' '` | output contains 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

None blocking. Two judgment calls are resolved in-plan and flagged for the critique to challenge:

1. **Moving the three existing `test_valor_telegram_*` siblings into the new package.** The
   alternative is leaving them at `tests/unit/` level, which produces a package and three
   stragglers for the same module. Chosen: move them, unmodified.
2. **Hoisting `_bypass_promise_gate` to a package `conftest.py`** versus duplicating it into the
   five new files. Chosen: hoist, *conditional on* the three siblings not depending on the real
   gate — which the builder verifies before committing to it.
