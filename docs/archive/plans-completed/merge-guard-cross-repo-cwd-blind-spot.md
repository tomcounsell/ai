---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2422
last_comment_id: IC_kwDOEYGa088AAAABL1mYXA
---

# Merge-guard cross-repo blind spot: `cd` into a foreign repo evades the -R/--repo check

## Problem

The merge-guard hook (`.claude/hooks/validators/validate_merge_guard.py`) blocks a
direct `gh pr merge` unless the live merge predicate passes against **this**
repository. Because the predicate is inherently local, the hook carries a
cross-repo guard that refuses to judge a PR belonging to a different repository —
but that guard only recognizes an explicit `-R`/`--repo` flag. A command that
reaches another repository by changing directory slips past it.

**Current behavior:**

From a session rooted at `~/src/ai`, merging a PR in a different checkout:

```bash
cd ~/src/psyoptimal && gh pr merge 670 --squash --delete-branch
```

The guard does not recognize this as cross-repo. It evaluates the predicate
against the local repo (`tomcounsell/ai`), where PR #670 does not resolve, and
fails closed with a message that misrepresents a healthy foreign PR as a broken
local one:

```
Merge blocked — failed predicate check(s): PR state unavailable
(gh pr view failed: ... Could not resolve to a PullRequest with the number of 670.)
```

The message then points the operator at `data/merge_authorized_670` — an override
file namespaced to **this** repo's (nonexistent-or-unrelated) PR #670. Following
that advice leaves a live, silent merge authorization for a local PR the operator
never inspected. A misleading block routes people to a dangerous remedy.

**Desired outcome:**

Any command targeting a foreign repository — by flag **or** by directory change
(`cd`, `pushd`) — is classified cross-repo, blocked with the existing clear
cross-repo message, and never evaluates the local predicate or offers the
break-glass override. A same-repo `cd` (changing directory within the local
checkout) still merges normally — no false cross-repo blocks.

## Freshness Check

**Baseline commit:** `2e75b1af4` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-27T09:02:15Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/validators/validate_merge_guard.py:596` — issue claims the guard
  matches only `-R/--repo` via `_REPO_FLAG_RE.search(command)`. Confirmed: line
  596 today reads exactly `repo_flag = _REPO_FLAG_RE.search(command)`. The flag
  regex is at line 74; the cross-repo block body is lines 596–609.
- `tests/unit/test_validate_merge_guard.py` — the three cited flag-only tests
  (`test_cross_repo_flag_blocks_without_evaluating`,
  `test_cross_repo_flag_before_pr_number_blocks`,
  `test_cross_repo_unresolvable_local_fails_closed`) all present and flag-scoped.

**Cited sibling issues/PRs re-checked:**
- #2003 — CLOSED; introduced the flag-only guard (comment scopes it to the flag).
- #2010 — MERGED; implemented #2003 / added `_REPO_FLAG_RE`.
- #2394, #1642 — CLOSED; same class of repo-resolution confusion in `sdlc-tool` /
  `stage-query`. Relevant to Open Question 3 (shared-helper question) only.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-07-27T09:02:15Z -- validate_merge_guard.py
  test_validate_merge_guard.py` is empty. Root cause is still present exactly as
  described.

**Active plans in `docs/plans/` overlapping this area:** none. Sibling issues
#2420 (PM fire-and-forget) and #2421 (promise gate) touch different components.

**Notes:** No drift. Line numbers cited in the issue are exact against `2e75b1af4`.

## Prior Art

- **Issue #2003 / PR #2010**: *SDLC pipeline substrate* — introduced the cross-repo
  guard (`_REPO_FLAG_RE`, `_normalize_repo_slug`, `_local_repo_slug`) in the
  cycle-3 patch (`56d9aa1c1`). The in-code comment (lines 68–74) scopes it
  explicitly to the flag: *"a `-R/--repo` flag means the PR number belongs to
  ANOTHER repository."* The `cd` case was never in scope — this is a design gap,
  not a regression. **This plan reuses `_normalize_repo_slug` and generalizes
  `_local_repo_slug`; it does not re-solve slug normalization.**
- **Issue #2394**: *sdlc-tool: gh calls resolve against the ai repo* — same class
  of repo-resolution confusion, resolved by threading explicit repo context.
  Precedent that "resolve the real target repo" is the durable fix shape.
- **Issue #1642**: *sdlc stage-query: pr_merge_state resolves UNKNOWN without
  GH_REPO* — related repo-context failure in the opposite mode (silent no-fire).
  Together with #2394, evidence that repo-resolution bugs recur across guards
  (feeds Open Question 3).

No merged PR has previously attempted the `cd`/`pushd` detection — this is a
first fix for this specific blind spot.

## Why Previous Fixes Failed

The original guard (PR #2010) did not *fail* — it was **scoped narrowly by
design**. Its author deliberately handled only the `-R/--repo` flag because that
was the concrete case in front of them, and documented the scope in the code
comment. The failure mode is enumeration: the guard recognizes one of several
ways a command can retarget its repo. This plan must avoid repeating that mistake
in a new form — see Rabbit Holes (do not chase every exotic shell construct;
resolve the effective directory instead of enumerating shell syntax).

## Data Flow

1. **Entry point**: Claude Code fires the PreToolUse hook with a JSON payload on
   stdin: `{tool_name: "Bash", tool_input: {command: "<full compound command>"}}`.
   The full compound command string (including any leading `cd`/`pushd`) is
   available — the hook does not see only the `gh` fragment.
2. **`main()`** (line 569): parses stdin → confirms `tool_name == "Bash"` → fast
   path skips `echo`/`printf` → `_merge_cmd_in_command()` confirms a real merge
   invocation exists at an actual command position → `_command_has_help_flag()`.
3. **Cross-repo classification** (lines 593–609, the change site): today only
   `_REPO_FLAG_RE.search(command)` runs. **New:** before (or alongside) the flag
   check, determine the *effective working directory* of the merge invocation and
   resolve its origin slug; compare to the local slug.
4. **Predicate evaluation** (lines 611–658): only reached when the command is
   NOT classified cross-repo. Extracts PR number → override check → predicate.
5. **Output**: either silent allow, or a `{"decision": "block", "reason": ...}`
   JSON object on stdout. The cross-repo branch must emit the existing "Cross-repo
   merge not evaluable here" message and NOT the override remediation.

The load-bearing fact: because the hook receives the *entire* compound command,
`cd /foreign && gh pr merge N` is fully inspectable at classification time. The
`cd` and the merge are in one string.

## Architectural Impact

- **New dependencies**: none. Reuses the existing `subprocess` + `git remote
  get-url` pattern already in `_local_repo_slug` and the `_normalize_repo_slug`
  helper.
- **Interface changes**: internal helpers only. Generalize `_local_repo_slug()`
  to delegate to a new `_slug_for_dir(path)`; add an `_effective_merge_dir(command)`
  parser. No public/CLI surface changes.
- **Coupling**: unchanged. The hook remains self-contained; no new imports from
  the app.
- **Reversibility**: trivial — the change is additive classification logic in one
  file plus tests. Revert = delete the new helpers and the new branch.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (to resolve Open Questions 1 and 3 — detect-vs-resolve default
  and the `git -C` semantics call)
- Review rounds: 1 (this hook gates every merge in the repo; the false-positive
  case must be reviewed as carefully as the fix)

## Prerequisites

No prerequisites — this work modifies a self-contained hook with no external
service dependencies. The hook's git calls target local checkouts already present
on the machine.

## Solution

### Key Elements

- **`_slug_for_dir(path)`**: resolves an arbitrary directory to its lowercase
  `OWNER/REPO` origin slug via `git -C <path> remote get-url origin` +
  `_normalize_repo_slug`. `_local_repo_slug()` becomes a thin wrapper:
  `_slug_for_dir(_REPO_ROOT)`. Returns `None` on any failure (not a git repo,
  no origin, timeout).
- **`_effective_merge_dir(command)`**: parses the compound command to determine
  the working directory in effect when `gh pr merge` runs. Walks the ordered
  command segments (reusing `_extract_executed_commands`) up to the merge segment,
  tracking `cd <dir>` and `pushd <dir>` directory changes. Returns the effective
  directory path (absolute, or resolved against the hook's cwd for relative
  paths), or `None` when no directory change is detected or the target is a shell
  variable / substitution the parser cannot resolve literally.
- **Cross-repo classification (extended)**: if `_effective_merge_dir` yields a
  directory whose slug is resolvable AND differs from the local slug → block with
  the cross-repo message. The existing `-R/--repo` branch is unchanged and runs in
  addition.

### Flow

Merge command detected → determine effective dir (cd/pushd walk) → resolve its
slug → **slug ≠ local** → cross-repo block (existing message, no override advice)
→ **slug == local OR unresolvable OR no dir change** → fall through to existing
`-R` check → fall through to local predicate evaluation → allow/block.

### Technical Approach

- **Recommended default: resolve, don't enumerate (Open Question 1).** Rather than
  adding brittle regexes for every shell form, detect the small set of constructs
  that actually change `gh`'s repo context (`cd`, `pushd`), extract the target
  directory, and *resolve* it to a real repo slug with `git remote get-url`.
  Comparison is slug-vs-slug, exactly as the flag path already does. This is
  robust to symlinks, `.git` suffixes, and URL/SSH remote forms (all handled by
  the existing `_normalize_repo_slug`).
- **Reuse the tokenizer.** `_extract_executed_commands` already segments a compound
  command into ordered, real command positions (skipping heredocs, quoted bodies,
  and substitutions). Walk those segments in order; the last `cd`/`pushd` before
  the segment containing the merge determines the effective directory. This
  inherits the tokenizer's fail-closed contract for free.
- **Ambiguity posture — fall through, do not over-block (Acceptance Criterion 4).**
  When the effective directory is unresolvable (a shell variable, `cd "$DIR"`, a
  command substitution, or a path that is not a git checkout), do **not** classify
  cross-repo — fall through to the existing predicate path. Rationale: a `cd` to
  an unresolvable target is far more likely to be a benign local navigation than
  deliberate foreign intent, and AC4 (no false cross-repo blocks on local merges)
  weighs at least as heavily as the fix. This is strictly no worse than today's
  behavior for the unresolvable case, and it protects every legitimate local
  merge. (Contrast: the `-R` flag path fails *closed* on unresolvable local slug,
  because an explicit `-R` is an explicit foreign signal. A bare `cd` is not.)
- **Same-repo `cd` is explicitly allowed.** If the effective dir resolves to the
  same slug as local, the command merges normally. Covered by a dedicated test.
- **Message reuse.** Route dir-based detections to the existing "Cross-repo merge
  not evaluable here … Run the merge from a checkout of the target repository so
  its own merge gate applies." message. That message already omits the
  `data/merge_authorized_{pr}` advice (AC5 is satisfied by routing to it). Adjust
  the wording so it does not hard-code "via -R/--repo" (state the detected target
  directory instead) while preserving the `"Cross-repo merge not evaluable here"`
  prefix that existing tests assert.
- **Relative-path resolution.** Resolve relative `cd ../foo` against the hook
  process cwd (`os.getcwd()`), which in practice matches the Bash tool's working
  directory. Document this as a known approximation (see Risks).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_slug_for_dir` wraps its `subprocess.run` in `try/except Exception` and
  returns `None` (mirroring `_local_repo_slug`). Add a test asserting a
  nonexistent / non-git directory yields `None` and the command falls through to
  the predicate path (observable: predicate seam is reached, not a cross-repo
  block).
- [ ] `_effective_merge_dir` must not raise on malformed input; on any tokenizer
  exception it returns `None` (fall-through). Add a test monkeypatching
  `_extract_executed_commands` to raise, asserting `_effective_merge_dir` returns
  `None` and a bare `cd X && gh pr merge N` still reaches the predicate rather than
  crashing the hook.

### Empty/Invalid Input Handling
- [ ] `_effective_merge_dir("")` and a command with no `cd`/`pushd` return `None`
  (no directory change) — covered by asserting a plain `gh pr merge N` evaluates
  the predicate unchanged.
- [ ] `cd "$HOME/foo" && gh pr merge N` (shell variable) is treated as unresolvable
  → falls through, does not block. Test asserts no cross-repo block.

### Error State Rendering
- [ ] The cross-repo block for a dir-based detection renders the "Cross-repo merge
  not evaluable here" message AND does **not** contain `merge_authorized` or
  `override:` — asserted directly in the block-message test (AC5).
- [ ] The block names the detected target directory / slug so the operator
  understands why (not a bare predicate failure).

## Test Impact

All existing tests in `tests/unit/test_validate_merge_guard.py` must continue to
pass unchanged — the change is additive classification that only fires when a
`cd`/`pushd` retargets to a foreign slug.

- [ ] `tests/unit/test_validate_merge_guard.py::test_predicate_pass_allows_silently`
  — no change expected; a `gh pr merge` with no directory change still allows. Run
  to confirm no regression.
- [ ] `tests/unit/test_validate_merge_guard.py::test_cross_repo_flag_*` (three
  tests) — no change; the `-R` branch is untouched. Run to confirm.
- [ ] `tests/unit/test_validate_merge_guard.py::test_repo_flag_matching_local_repo_evaluates_normally`
  — no change; confirms the local-slug-equal path still evaluates. The new
  same-repo-`cd` test mirrors this contract for the dir path.

New tests are added (see Success Criteria), not modifications to existing ones. No
DELETE or REPLACE dispositions.

## Rabbit Holes

- **Enumerating every shell construct.** Do NOT try to handle subshells `( cd x;
  ... )`, `env -C`, `pushd`/`popd` stacks, `cd -`, background jobs, `&&`/`||`
  short-circuit semantics, or `cd $(...)`-computed directories. That is the exact
  enumeration trap that produced the original `-R`-only gap. Handle literal
  `cd <path>` and `pushd <path>`; treat everything else as unresolvable →
  fall-through. Robustness comes from *resolving the slug*, not from parsing more
  syntax.
- **Replaying the command to discover cwd.** Do not attempt to actually execute or
  dry-run the compound command to learn gh's effective repo. Parse for the literal
  dir and resolve it with a read-only `git remote get-url`.
- **A shared cross-guard resolution helper.** Refactoring `sdlc-tool` / `stage-query`
  (#2394, #1642) to share one repo-resolution module is a real idea but a separate,
  larger project. Out of scope here (Open Question 3).
- **`git -C` semantics.** `git -C <dir>` does NOT change `gh pr merge`'s repo
  context — `gh` resolves its base repo from the process cwd, which `git -C`
  leaves untouched. Chasing `git -C` as a cross-repo signal risks false-blocking a
  genuinely-local merge. See Open Question 1.

## Risks

### Risk 1: Over-eager blocking of legitimate local merges
**Impact:** This hook gates *every* merge in the repo. If dir-detection
misclassifies a local merge as cross-repo, all such merges break — worse than the
original bug.
**Mitigation:** Fall-through-on-ambiguity posture (only block when the effective
dir *positively resolves* to a *different* slug). Dedicated test for same-repo `cd`
and for unresolvable-dir fall-through. Review round treats AC4 as a first-class
gate.

### Risk 2: Relative-path resolution against the wrong base
**Impact:** `cd ../foo` resolved against a hook cwd that differs from the Bash
tool's cwd could resolve to the wrong directory, either missing a foreign target
or (rarely) mis-resolving. In practice the hook and tool share the working dir.
**Mitigation:** Resolve relatives against `os.getcwd()`; if the resolved path does
not exist or is not a git repo, `_slug_for_dir` returns `None` → fall-through (no
false block). Document the approximation in the code comment. Absolute-path cases
(the reported scenario used an absolute path) are unaffected.

### Risk 3: Latency from an extra `git remote get-url`
**Impact:** One more subprocess (~tens of ms) on the merge path, only when a
`cd`/`pushd` is present.
**Mitigation:** 10s timeout (matching `_local_repo_slug`); only invoked when a
directory change is actually detected, so the common no-`cd` merge path adds zero
subprocesses.

## Race Conditions

No race conditions identified — the hook is a synchronous, single-invocation
stdin→stdout process. All git resolution is read-only (`git remote get-url`) with
no shared mutable state.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2394] Unifying repo-resolution across `sdlc-tool`, `stage-query`,
  and this hook into one shared helper. #2394 and #1642 track the sibling
  resolution bugs; a shared module is a larger refactor. This plan reuses the
  existing local helpers (`_normalize_repo_slug`) but does not extract a new
  cross-component module.
- Nothing else deferred — `cd`, `pushd`, the message-safety fix, and the
  same-repo/unresolvable fall-through cases are all in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to a Claude
Code hook that already ships with the repo. No new dependencies, config files, or
migration steps. The hook is registered in `.claude/settings*.json` (unchanged).

## Agent Integration

No agent integration required — this is a hook-internal change. The merge guard is
a PreToolUse hook the Claude Code harness invokes on `Bash` tool calls; it has no
MCP surface, no CLI entry point, and no bridge wiring. Its "integration test" is
the unit test suite driving `guard.main()` with synthetic Bash payloads (the
existing `_run_main` harness), which exercises the exact code path the harness
uses.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/self-healing-merge-gate.md` — document that the
  cross-repo guard now recognizes directory-change constructs (`cd`, `pushd`) in
  addition to the `-R/--repo` flag, and the fall-through-on-ambiguity posture.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for hook internals.

### Inline Documentation
- [ ] Update the cross-repo guard comment block (lines 68–74) to reflect that
  detection now covers effective-cwd changes, not just the flag.
- [ ] Docstrings on the new `_slug_for_dir` and `_effective_merge_dir` helpers,
  including the fall-through-on-ambiguity contract and the relative-path
  approximation.

## Success Criteria

- [ ] A `gh pr merge N` reaching a foreign repo via `cd /foreign && ...` is
  classified cross-repo and blocked with the "Cross-repo merge not evaluable here"
  message, not a predicate-evaluation failure. (New test:
  `test_cross_repo_cd_blocks_without_evaluating`.)
- [ ] The same via `pushd /foreign && ...` behaves identically. (New test:
  `test_cross_repo_pushd_blocks_without_evaluating`.)
- [ ] The local predicate is never evaluated when the effective dir resolves to a
  foreign slug — the predicate seam raises if called. (Asserted in the two tests
  above via a `must_not_run` predicate seam.)
- [ ] A same-repo `cd` (resolving to the local slug) still merges normally — no
  false cross-repo block. (New test: `test_same_repo_cd_evaluates_normally`.)
- [ ] An unresolvable/shell-variable dir (`cd "$X" && ...`) falls through to the
  predicate rather than blocking. (New test:
  `test_unresolvable_cd_falls_through_to_predicate`.)
- [ ] The cross-repo block message does NOT contain `merge_authorized` or
  `override:` for the dir-based detection. (Asserted in the block-message tests.)
- [ ] `git -C` handling matches whatever Open Question 1 resolves to, with a test
  documenting the chosen behavior.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No related xfail/xpass tests exist (none found — nothing to convert).

## Team Orchestration

Small, single-file change — a builder/validator pair suffices.

### Team Members

- **Builder (merge-guard)**
  - Name: `guard-builder`
  - Role: Implement `_slug_for_dir`, `_effective_merge_dir`, and the extended
    cross-repo classification branch; add the new unit tests. Domain: untrusted
    shell-string parsing / fail-closed security guard.
  - Agent Type: builder
  - Resume: true

- **Validator (merge-guard)**
  - Name: `guard-validator`
  - Role: Verify all Success Criteria; specifically stress the false-positive
    (same-repo `cd`, unresolvable dir) cases and confirm existing tests unchanged.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier-1 agents. Add `Domain: security/untrusted-input` framing from
`DOMAIN_FRAMING.md` to the builder task (the guard parses attacker-shaped shell
strings and must fail safe).

## Step by Step Tasks

### 1. Implement directory-aware cross-repo detection
- **Task ID**: build-cd-detection
- **Depends On**: none
- **Validates**: `tests/unit/test_validate_merge_guard.py`
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: security/untrusted-input
- **Parallel**: false
- Add `_slug_for_dir(path)` and refactor `_local_repo_slug()` to delegate to it.
- Add `_effective_merge_dir(command)` that walks `_extract_executed_commands`
  segments and returns the last literal `cd`/`pushd` target before the merge
  segment (or `None`).
- Extend the classification block in `main()` (lines 593–609): before/alongside
  the `-R` check, resolve the effective dir's slug; block cross-repo when it
  positively differs from local; fall through on same-slug or unresolvable.
- Adjust the cross-repo message wording to name the detected target while keeping
  the `"Cross-repo merge not evaluable here"` prefix; ensure it never mentions the
  override file.
- Update the guard comment block (lines 68–74) and add docstrings.

### 2. Add regression tests
- **Task ID**: build-tests
- **Depends On**: build-cd-detection (same builder, same file)
- **Validates**: `tests/unit/test_validate_merge_guard.py`
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add: `test_cross_repo_cd_blocks_without_evaluating`,
  `test_cross_repo_pushd_blocks_without_evaluating`,
  `test_same_repo_cd_evaluates_normally`,
  `test_unresolvable_cd_falls_through_to_predicate`, a `git -C` test matching the
  resolved Open Question 1 decision, and a fall-through-on-tokenizer-failure test.
- Use the existing `enforcement` fixture + `_run_main` harness; monkeypatch
  `_slug_for_dir` / `_local_repo_slug` to avoid real git calls, mirroring the
  existing `_local_repo_slug` monkeypatch pattern.

### 3. Validation
- **Task ID**: validate-guard
- **Depends On**: build-cd-detection, build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_validate_merge_guard.py -q` — all pass, existing
  tests unchanged.
- Confirm each Success Criterion, with special attention to AC4 (no false
  cross-repo block on same-repo `cd` and on unresolvable dir).
- Report pass/fail.

### 4. Documentation
- **Task ID**: document-guard
- **Depends On**: validate-guard
- **Assigned To**: guard-validator (doc pass) or documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/self-healing-merge-gate.md` per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-guard, document-guard
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit suite + lint + format checks (Verification table).
- Confirm all criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Merge-guard tests pass | `pytest tests/unit/test_validate_merge_guard.py -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/validators/validate_merge_guard.py tests/unit/test_validate_merge_guard.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/validators/validate_merge_guard.py tests/unit/test_validate_merge_guard.py` | exit code 0 |
| cd detection covered | `grep -c "def test_cross_repo_cd" tests/unit/test_validate_merge_guard.py` | output > 0 |
| pushd detection covered | `grep -c "def test_cross_repo_pushd" tests/unit/test_validate_merge_guard.py` | output > 0 |
| same-repo cd allowed test present | `grep -c "def test_same_repo_cd" tests/unit/test_validate_merge_guard.py` | output > 0 |
| cross-repo message omits override advice | `grep -n "merge_authorized" .claude/hooks/validators/validate_merge_guard.py \| grep -i "cross-repo"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`git -C` semantics — detect it or not?** Acceptance Criterion 1 lists
   `git -C` alongside `cd`/`pushd`, but mechanically `git -C <dir>` does NOT change
   `gh pr merge`'s repo context (`gh` resolves its base repo from the process cwd,
   which `git -C` leaves untouched). Treating `git -C /foreign status && gh pr
   merge <local-N>` as cross-repo would *false-block a genuinely-local merge*,
   violating AC4. **Recommended default:** implement `cd`/`pushd` (which truly
   retarget gh) and do NOT treat `git -C` as a cross-repo signal; add a test
   documenting that a `git -C` command still evaluates the local predicate. Do you
   want `git -C` detected anyway (accepting the rare false-positive because the
   block message is now safe), or is the mechanically-correct "cd/pushd only"
   scope acceptable?

2. **Unresolvable-dir posture: fall-through vs. fail-closed.** For a `cd "$VAR"`
   or `cd $(...)` whose target the parser cannot resolve literally, the plan
   recommends *falling through* to the local predicate (protecting local merges,
   AC4) rather than fail-closed blocking (as the `-R` path does). This is strictly
   no worse than today for that case. Confirm this is the right trade-off, or do
   you want an unresolvable `cd` to fail closed like the flag path?

3. **Sibling-guard blind spots (#2394, #1642).** A shared repo-resolution helper
   across `sdlc-tool`, `stage-query`, and this hook is deferred to a separate slug
   (No-Gos). Confirm that deferral, or should this plan grow to include a shared
   module now?
