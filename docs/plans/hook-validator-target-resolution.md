---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2738
last_comment_id:
revision_applied: true
revision_applied_at: 2026-08-13T03:39:48Z
---

# Hook Validator Target Resolution

## Problem

Hook validators under `.claude/hooks/validators/` are small Python scripts the Claude Code harness runs after a tool call. A `PostToolUse` validator receives a JSON payload on stdin describing the tool call that just ran, and may exit 2 to block the agent.

Four of them decide **which file to validate** by guessing rather than by reading the payload that told them.

**Current behavior.** `validate_documentation_section.py`, `validate_test_impact_section.py`, and `validate_verification_section.py` each carry a copy of `find_newest_plan_file`: they read stdin, throw the parsed result away (`json.load(sys.stdin)` with the result unassigned), then shell out to `git status --porcelain docs/plans/` and validate whichever untracked-or-added `.md` has the newest mtime. A `Write` to `docs/features/anything.md` therefore gets judged against some other lane's in-progress plan. The `git status` query is not worktree-scoped, so the collision crosses checkouts.

`validate_file_contains.py` is the same defect wearing a partial fix. It *does* read `tool_input.file_path`, but only as an early-exit relevance filter: if the write was not under `docs/plans/*.md` it passes through. When the write **is** a plan doc — the exact case the hook exists to police — it discards the resolved target and calls `validate(args.directory, ...)`, which reduces to `find_newest_file()`, a union of the same git-status scan and an mtime glob. Two lanes concurrently writing distinct plan docs will have one lane's write validated against the other lane's file. This is the worst of the four, because plan writes are precisely what concurrent lanes do at the same moment.

**Desired outcome.** A validator validates the file the hook payload named, or it validates nothing. `find_newest_plan_file` exists in zero files. Target resolution has one implementation, in one place, with tests.

## Freshness Check

**Baseline commit:** `7cd1d4a5e9d71fe7ab7048922632b97564bd29e0`
**Issue filed at:** 2026-08-13T03:21:34Z (minutes before planning)
**Disposition:** Minor drift — one referenced sibling PR is live and moving, which reshapes scope.

**File:line references re-verified (all against the baseline SHA):**
- `.claude/hooks/validators/validate_documentation_section.py:78` — `find_newest_plan_file` def — still holds; call site at `:226`; stdin discarded at `:219`.
- `.claude/hooks/validators/validate_test_impact_section.py:64` — same helper — still holds; call at `:223`; stdin discarded at `:216`.
- `.claude/hooks/validators/validate_verification_section.py:63` — same helper — still holds; call at `:200`; stdin discarded at `:194`.
- `.claude/hooks/validators/validate_no_gos_justification.py:119` — `target_from_hook_input` reference implementation — still holds.
- `.claude/hooks/validators/validate_file_contains.py:150` — `find_newest_file` — still holds; git-status scan at `:53`, mtime glob at `:78`, partial payload read at `:266`.

**Cited sibling issues/PRs re-checked:**
- #2682 — closed, fixed by PR #2688, which is the reference diff.
- #2689 — open. This plan closes it.
- PR #2686 (`session/redis-validator-scope`, covering #2638 and #2641) — **OPEN and actively moving**: commit `7e124c3` landed 2026-08-13 03:02Z and a re-review comment 03:15Z, both within the hour before planning. `mergeStateStatus=CLEAN`, review decision currently Changes Requested on tech debt.
- #2736 — open, and its fix edits the one file PR #2686 is rewriting.

**Commits on main since issue was filed (touching referenced files):** none. `7cd1d4a5e` is unchanged.

**Active plans in `docs/plans/` overlapping this area:** none touching `.claude/hooks/validators/`. PR #2686's lane is the only concurrent worker in hook-validator territory, and it is confined to `validate_no_raw_redis_delete.py`, `tests/unit/test_validate_no_raw_redis_delete.py`, and `.claude/hooks/dispatch/pre_tool_use_bash.py` — disjoint from every file this plan touches except the dispatcher, which this plan does not modify.

**Notes:** The drift is in ownership, not in code. The tracking issue was written assuming this lane might absorb #2638/#2641/#2736; the freshness check confirms #2686 is a live lane holding that file. Scope narrows accordingly (see No-Gos), and widens to take in `validate_file_contains.py`, which the issue did not know about.

## Prior Art

- **PR #2688** (merged): *Resolve the No-Gos validator's target from the hook payload* — deleted `find_newest_plan_file` from `validate_no_gos_justification.py` and replaced it with `target_from_hook_input`, plus 65 tests including `test_newest_plan_fallback_is_gone`, which asserts the helper never comes back. This is the reference implementation and the test template. Its own PR body explicitly reproduced the identical defect in the three siblings and named #2689 to carry it.
- **Issue #2682** (closed): the originating defect. Established the failure mode and the tracked-`docs/plans/` detail that makes the reproducer work.
- **PR #2686** (open, live): scoping the raw-Redis validator to this repo, plus two stale-entry fixes. Same *theme* — a validator judging text instead of a resolved target — but a different mechanism: it matches Bash command strings and resolves no file target at all. Not merged into this plan's scope.
- **`hook-registration-manifest-dispatcher`** (completed plan, `docs/plans/completed/`): recorded that `validate_verification_section.py` is unregistered but deliberately kept ("Do NOT delete" — it has a passing unit test and is documented in `docs/features/machine-readable-dod.md:188`). This settles the port-vs-delete open question in the issue: port it.

## Research

No relevant external findings — proceeding with codebase context. This is a change to this repo's own hook scripts, involving no external libraries, APIs, or ecosystem patterns.

## Spike Results

### spike-1: Can a validator import a shared module from `hook_utils`?
- **Assumption**: "A `PostToolUse` validator script, invoked standalone by the harness, can import `hook_utils` without a fragile CWD dependency."
- **Method**: code-read
- **Finding**: **Yes, with proven precedent.** The manifest generator `scripts/update/hardlinks.py::_build_hook_command` (`:1006`, interpreter constant at `:23`) emits `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python "$CLAUDE_PROJECT_DIR"/.claude/hooks/validators/<script>.py`. The `hook_python` shim resolves the main checkout's `.venv/bin/python` via `git rev-parse --git-common-dir` and execs it with an **absolute** script path, so `Path(__file__).resolve()` is reliable regardless of CWD. Three validators already do exactly this: `validate_no_destructive_git_in_worktree.py:74-75`, `validate_no_destructive_git_in_shared_checkout.py:79-80`, `validate_sdlc_on_stop.py:24-28`, all using the one-liner `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` followed by `from hook_utils.… import …`.
- **Confidence**: high
- **Impact on plan**: The shared module goes in `hook_utils/`. The issue's open question is resolved. Note the consequence for tests: `test_validate_no_gos_justification.py` inserts only `.claude/hooks/validators` on `sys.path`, which is **not** sufficient once a validator imports `hook_utils`. New and updated tests must follow `test_validate_sdlc_on_stop.py:12-17`, which inserts both `.claude/hooks` and `.claude/hooks/validators`.

### spike-2: Does the bug class survive anywhere else under `.claude/hooks/`?
- **Assumption**: "The three validators named in #2689 are the complete remaining population."
- **Method**: code-read (exhaustive sweep of `.claude/hooks/`)
- **Finding**: **False — there is a fourth, and it is live.** `validate_file_contains.py` (registered `PostToolUse` / `Write`, manifest.toml:149, invoked with `-d docs/plans -e .md --contains …`) resolves the payload target at `:266` but uses it only as an early-exit filter, then calls `find_newest_file()` (`:150`) — a union of `get_git_new_files()` (git-status scan, `:53`) and `get_recent_files()` (mtime glob, `:78`). Verified by reading the code directly, not taken on the subagent's word. Separately cleared: `validate_knowledge_base_section.py` also discards stdin (`:138`) but is unregistered, warn-only (`sys.exit(0)` on every path), and defaults to a fixed `CLAUDE.md` target — it does no target guessing and is **not** part of this class.
- **Confidence**: high
- **Impact on plan**: Scope widens from three validators to four. `validate_file_contains.py` is arguably the highest-severity of the set, since it fires on exactly the concurrent plan writes that collide.

### spike-3: What test surface exists today?
- **Assumption**: "Each of the three validators has a test file we can extend."
- **Method**: code-read
- **Finding**: Partly false. `test_validate_verification_section.py` (9 tests) and `test_validate_test_impact.py` (20 tests) exist but exercise **only** the pure `extract_*` / `is_section_complete` / `validate_*` functions — zero coverage of `main()`, stdin, or `find_newest_plan_file`. There is **no test file at all** for `validate_documentation_section.py`, and none for `validate_file_contains.py`. `test_validate_no_gos_justification.py` holds the template: a `run_hook(payload, cwd)` subprocess helper that feeds JSON on stdin with no argv, plus the `TestTargetIsTheFileTheWriteNamed` class.
- **Confidence**: high
- **Impact on plan**: Two new test files must be created from scratch, and the two existing files gain a new targeting class each. The `run_hook` helper is duplicated four times or shared; see Technical Approach.

### spike-4: Is the plan-doc write path itself safe to work in?
- **Assumption**: "Editing these validators will not wedge the build lane, because they gate the agent's own Write calls."
- **Method**: code-read
- **Finding**: The four validators are `PostToolUse`, not `PreToolUse` — they run *after* the write lands, so a block reports a finding about a file that already exists on disk. `exit_policy = "propagate"` means exit 2 surfaces to the agent. A builder editing these files will be gated by the **installed** copies in `.claude/hooks/`, which are the same files being edited, so a mid-edit broken state blocks subsequent writes until fixed.
- **Confidence**: high
- **Impact on plan**: Builders must edit each validator in a single coherent Write, not a sequence of partial edits, and must never disable a hook to make progress. Because the hooks are `PostToolUse` on `Write`, an edit that breaks a validator surfaces immediately on the *next* write — fast feedback, not a silent trap.

## Data Flow

1. **Entry point**: The agent calls `Write` with `file_path`.
2. **Harness**: emits a `PostToolUse` JSON payload on stdin — `{"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": "…", "tool_input": {"file_path": "…"}}` — and invokes each registered validator via `hook_python` with an absolute script path.
3. **Validator (today)**: parses stdin, discards it, shells to `git status --porcelain docs/plans/` in the process CWD, picks newest-by-mtime, reads *that* file, and may exit 2. The file read has no relationship to the file written.
4. **Validator (after)**: parses stdin, resolves the target via the shared helper, exits 0 if the target is absent or out of the watched scope, otherwise reads exactly that file and judges it.
5. **Output**: exit 0 with a `{"result": "continue"}` line, or exit 2 with a human-readable finding on stderr that the harness shows the agent.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2688 | Replaced `find_newest_plan_file` with `target_from_hook_input` in `validate_no_gos_justification.py` | Did not fail — it succeeded, and deliberately fixed one file to keep the diff reviewable. It filed #2689 for the siblings. The incompleteness was scoped and declared, not accidental. |
| `validate_file_contains.py`'s payload read (`:266`) | Added a `tool_input.file_path` early-exit so the hook passes through on non-plan writes | A partial adoption that treats the payload as a *relevance filter* rather than a *target*. It fixed the cheap half (irrelevant writes pass) and left the expensive half (relevant writes are judged against the wrong file). Reading the payload is necessary but not sufficient; the target must also be *used*. |

**Root cause pattern:** the payload is treated as advisory context rather than as the authoritative statement of what the tool call did. Every partial fix in this family reads the payload and then falls back to a search. The durable fix is a helper whose contract makes the fallback unrepresentable: `None` means "nothing to validate", never "go find something to validate".

## Architectural Impact

- **New dependencies**: none. One new stdlib-only module under `.claude/hooks/hook_utils/`.
- **Interface changes**: four validators lose their private target-selection helpers and gain a shared import. `validate_file_contains.py`'s `validate()` signature changes from directory-based to target-based; its CLI flags stay compatible except `--max-age`, which is removed since target selection no longer depends on mtime.
- **Coupling**: decreases. Four copies of a subtle predicate collapse to one tested implementation.
- **Data ownership**: the hook payload becomes the single source of truth for "what file is being judged". Git working-tree state stops being an input to that decision entirely.
- **Reversibility**: high. Each validator is an independent script; a revert is file-local.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer, plus a test-engineer and a documentarian (see Team Orchestration — the roster is three agents, matching this line)

**Interactions:**
- PM check-ins: 1-2 (the scope change — dropping #2638/#2641/#2736, adding `validate_file_contains.py` — needs acknowledgement)
- Review rounds: 1-2

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv on the pinned interpreter | `python -m tools.doctor` | `scripts/pytest-clean.sh` aborts on an off-pin venv |
| `gh` authenticated | `gh auth status` | `validate_no_gos_justification` shells `gh issue view` for `[SEPARATE-SLUG #N]` tags |

## Solution

### Key Elements

- **`hook_utils/hook_target.py`** — one module owning target resolution for path-targeting validators. Exposes `target_from_hook_input(hook_input)` (payload → path or `None`) and `read_hook_input()` (stdin → dict, never raising). The contract is that `None` means "nothing to validate", never "go find something".
- **Four ported validators** — `validate_documentation_section.py`, `validate_test_impact_section.py`, `validate_verification_section.py`, `validate_file_contains.py` all resolve their target from the payload and from nowhere else.
- **Four deleted guessers** — `find_newest_plan_file` (×3), plus `find_newest_file` / `get_git_new_files` / `get_recent_files` / `get_git_committed_files` / `get_committed_file_content` in `validate_file_contains.py`.
- **A shared test harness** — the `run_hook(payload, cwd)` subprocess pattern from `test_validate_no_gos_justification.py`, applied to all four validators, including the #2689 cross-lane reproducer as an explicit regression test.

### Flow

Agent writes a file → harness emits PostToolUse payload → validator resolves target from payload → target absent or out of watched scope → **exit 0, nothing judged** → agent continues.

Agent writes a plan doc → harness emits payload → validator resolves target from payload → target is in the watched scope → validator reads **that exact file** → section missing or incomplete → **exit 2 with a finding naming the file the agent actually wrote**.

### Technical Approach

The port is not a one-line substitution. PR #2688 shipped three coupled behaviors, and dropping any one turns a fail-open guess into a fail-closed block:

1. **Resolve from the payload.** `tool_input.file_path`, falling back to `tool_input.notebook_path`. Non-dict `tool_input`, non-string path, and empty string all collapse to `None`.

   **Both functions must guard a syntactically-valid non-dict payload.** Swallowing `json.JSONDecodeError` is not enough: stdin of `null`, `[1,2]`, or `"str"` parses cleanly and yields a non-dict, and `target_from_hook_input`'s verbatim source does `hook_input.get("tool_input")` as its first act — an `AttributeError`, i.e. a traceback out of a hook that gates every write. The 65 inherited tests do not cover this, so the gap would survive the "65 pass unchanged" gate untouched. Therefore `read_hook_input()` is `parsed = json.loads(raw); return parsed if isinstance(parsed, dict) else {}`, never a bare `return json.loads(raw)`; and `target_from_hook_input` opens with `if not isinstance(hook_input, dict): return None`, because the plan documents the two as separately callable.
2. **Filter to the watched scope before touching the filesystem.** A write to a non-plan path must exit 0 *before* any `Path.exists()` call. Today all three siblings stat first and exit 2 on an unresolvable path — a relative path resolved against a different worktree's CWD would block a write they have no business judging.
3. **Distinguish an explicit CLI argument from a hook-derived path.** A CLI path that names nothing is a user error worth reporting (exit 2). A hook-derived path that cannot be resolved is not a finding at all (exit 0).

Module placement and import, following the three existing precedents exactly:

```python
# Standalone script — sys.path mutation is safe (never imported as library).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hook_utils.hook_target import read_hook_input, target_from_hook_input
```

`validate_no_gos_justification.py` is ported onto the shared module too. Leaving its private copy in place would mean the class is centralized in four files and duplicated in a fifth — the same trap one refactor later. Its behavior must not change; its 65 existing tests are the proof.

`validate_file_contains.py` needs more than a substitution because its whole design is directory-based. The change: use the resolved target as the file to check, keep the `--directory` / `--extension` flags as the *scope filter* they already effectively are, and delete the git-history fallback (`get_git_committed_files` / `get_committed_file_content`). That fallback existed to answer "the plan was committed and then migrated away, so nothing is dirty" — a question that only arises when you are guessing. Once the payload names the file, and the hook is `PostToolUse` so the file exists on disk, the fallback is unreachable. When no payload target resolves (bare CLI invocation with no positional argument), the validator exits 0 rather than guessing.

The `--max-age` flag becomes meaningless once target selection stops depending on mtime. Remove the flag and its plumbing rather than leaving a dead knob; it is not passed by the manifest registration.

The `run_hook` subprocess helper is duplicated per test file rather than shared. Test helpers that import across `tests/unit/` files create a second coupling problem, and the helper is eight lines. This is a deliberate exception to the de-duplication goal, which targets *production* predicate logic.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `read_hook_input()` swallows `json.JSONDecodeError` / `OSError` / `EOFError` by design and returns `{}`. Assert the observable behavior: malformed stdin, empty stdin, and closed stdin each produce exit 0 from every ported validator, not a traceback.
- [ ] The deleted helpers' `except (subprocess.TimeoutExpired, subprocess.SubprocessError)` blocks go away with them — no orphaned handlers remain.
- [ ] No `except Exception: pass` is introduced.

### Empty/Invalid Input Handling
- [ ] `target_from_hook_input` is tested against: missing `tool_input`, `tool_input: None`, `tool_input: "string"`, `tool_input: {}`, `file_path: ""`, and non-string paths (`int`, `dict`, `list`, `bool`). All return `None`.
- [ ] **Syntactically-valid non-dict top-level payloads** — stdin of `null`, `[1,2]`, `"str"`, and `42` — return `None` from `target_from_hook_input` and `{}` from `read_hook_input`, and produce exit 0 (not a traceback) from every ported validator. These cases are absent from the 65 inherited tests and must be added to `test_hook_target.py` and to each validator's targeting class as `run_hook("null", cwd)` / `run_hook("[1,2]", cwd)`.
- [ ] Every ported validator exits 0 on `None`, `""`, `"not json"`, and `{"tool_input": {}}` as stdin.
- [ ] Not applicable: agent-output processing / silent loops. These validators produce a single exit code per invocation.

### Error State Rendering
- [ ] A genuine finding must still name the file: assert the exit-2 stderr contains the path from the payload, not a path discovered by scanning.
- [ ] Assert the failure is not swallowed: writing an actually-deficient plan doc still exits 2 for each of the four validators.

## Test Impact

- [ ] `tests/unit/test_validate_verification_section.py` — UPDATE: add `sys.path` insert for `.claude/hooks` (per `test_validate_sdlc_on_stop.py:12-17`); add a `run_hook` helper and a `TestTargetIsTheFileTheHookNamed` class. The 9 existing pure-function tests are unaffected and must keep passing.
- [ ] `tests/unit/test_validate_test_impact.py` — UPDATE: same two additions. The 20 existing tests are unaffected and must keep passing.
- [ ] `tests/unit/test_validate_no_gos_justification.py` — UPDATE: add the `.claude/hooks` `sys.path` insert (currently inserts only `validators/`, which breaks once the module imports `hook_utils`). All 65 tests must keep passing unchanged otherwise — they are the regression proof that the shared extraction preserved behavior. `test_newest_plan_fallback_is_gone` stays as-is.
- [ ] `tests/unit/test_validate_documentation_section.py` — CREATE: no test file exists today. Pure-function coverage for `extract_documentation_section` / `is_section_complete`, plus the targeting class.
- [ ] `tests/unit/test_validate_file_contains.py` — CREATE: no test file exists today. Cover the payload-target path, the out-of-scope early exit, and the removal of mtime/git-status selection.
- [ ] `tests/unit/test_hook_target.py` — CREATE: direct unit tests for the shared module.
- [ ] `tests/unit/test_architectural_constraints.py` — CHECK, no change expected: confirm it has no rule that a `hook_utils` module would violate.

## Rabbit Holes

- **Rewriting the section-parsing regexes.** The `extract_*` and `is_section_complete` functions have their own accumulated bug history and passing tests. This plan changes *which file* gets parsed, not *how*. Touching the parsers makes the diff unreviewable and puts the 29 existing pure-function tests at risk for no gain.
- **Generalizing the helper into a validator base class.** Tempting once four scripts share an import. Resist: these scripts must stay import-light and fast under a 10s hook timeout, and a base class would pull the whole family into one blast radius. A function with a clear contract is the right unit.
- **Building Bash-command-shape inspection into the shared module** so #2736 can adopt it later. No caller, no tests, and no way to know the right shape until PR #2686 lands. Speculative surface in a module that gates every write is exactly the wrong bet.
- **Chasing every `git status` call under `.claude/hooks/`.** The sweep found several, but only target *selection* is this bug. Dirty-tree checks in `validate_no_destructive_git_in_worktree.py` and friends are legitimate and out of scope. The anti-criterion row must therefore match `porcelain` **scoped to a directory variable**, not bare `porcelain` — a bare match flags `validate_no_destructive_git_in_worktree.py:143`, which is correct code, and turns a green build red. Caught by running the row against the finished branch.
- **Fixing `validate_knowledge_base_section.py`.** It discards stdin, which looks like the same signature, but it is unregistered, warn-only, and targets a fixed path. Changing it is motion, not progress.

## Risks

### Risk 1: The shared module breaks the already-fixed validator
**Impact:** `validate_no_gos_justification.py` is the one validator in this family that currently works. Porting it onto the shared module could regress the #2682 fix, re-opening a defect that already cost two rounds.
**Mitigation:** Its 65 tests, including `test_newest_plan_fallback_is_gone` and the seven `run_hook` end-to-end cases, run unchanged as the acceptance gate. The extracted function is moved verbatim, not rewritten. If any of the 65 fail, the extraction is wrong.

### Risk 2: A mid-edit broken validator wedges the builder
**Impact:** These hooks gate the agent's own `Write` calls with `exit_policy = "propagate"`. A validator left in a broken state between two edits will block every subsequent write, including the write that would fix it.
**Mitigation:** Each validator is rewritten in a single coherent `Write`, never a sequence of partial edits. Because they are `PostToolUse`, the first bad write surfaces the error immediately rather than silently. Disabling a hook to make progress is prohibited; if it becomes unavoidable, it is recorded in the PR and re-enabled before review.

### Risk 3: `validate_file_contains.py`'s behavior change is wider than the others
**Impact:** It is the only one whose `validate()` signature and CLI surface change. A mistake there stops the `## Success Criteria` / `## Update System` / `## Agent Integration` / `## Test Impact` presence checks from firing at all — a fail-open regression in a fail-closed guard, and this very plan document depends on those checks.
**Mitigation:** Its new test file asserts both directions: a plan doc missing a required string still exits 2, and a compliant one exits 0. The red-state proof runs against a deliberately-deficient fixture before the fix is called done.

### Risk 4: Collision with the live PR #2686 lane
**Impact:** Two branches editing hook validators concurrently. A conflict costs a rebase; worse, a duplicate fix wastes a review cycle.
**Mitigation:** File-level disjointness is verified and recorded in the Freshness Check. This plan touches none of `validate_no_raw_redis_delete.py`, `tests/unit/test_validate_no_raw_redis_delete.py`, or `.claude/hooks/dispatch/pre_tool_use_bash.py`. #2638, #2641, and #2736 are explicitly out of scope, tagged in No-Gos.

## Race Conditions

### Race 1: Cross-lane target selection (the bug itself)
**Location:** `validate_documentation_section.py:78-112`, `validate_test_impact_section.py:64-96`, `validate_verification_section.py:63-94`, `validate_file_contains.py:53-96,150-174`
**Trigger:** Two agent lanes in separate worktrees each `Write` a file while `docs/plans/` holds an untracked `.md` from the other lane. The validator's `git status` runs in whichever checkout the hook process started in, and mtime ordering between lanes is arbitrary.
**Data prerequisite:** `docs/plans/` must contain at least one **tracked** file. Otherwise `git status --porcelain docs/plans/` collapses the directory to a single `?? docs/plans/` line whose path does not end in `.md`, the suffix filter drops it, the helper returns `None`, and the validator exits 0 looking innocent. That false clear is what produced the original "no evidence" claim, and any test fixture that omits it will silently pass against the unfixed code.
**State prerequisite:** the hook process CWD, which the validator does not control and which is not guaranteed to be the lane's own worktree.
**Mitigation:** eliminated by construction — target selection stops reading working-tree state, so there is no shared mutable input left to race on. The regression test builds the tracked-anchor fixture explicitly.

### Race 2: Concurrent test runs against a shared Redis DB
**Location:** test execution, not product code
**Trigger:** Three SDLC lanes running tests at once against the shared `[1..15]` claim pool (#2628).
**Data prerequisite:** none for these tests — they are filesystem-and-subprocess only.
**State prerequisite:** a test DB no other lane holds.
**Mitigation:** export `POPOTO_TEST_DB=12` for every test invocation in this lane, and never DB 15 (popoto's pytest11 plugin flushes it). Use `scripts/pytest-clean.sh`, never bare `pytest`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2638] Scoping `validate_no_raw_redis_delete.py` to this repo. Implemented and under active review on PR #2686 at `session/redis-validator-scope`; re-implementing it here produces a conflicting duplicate diff in a file another lane holds.
- [SEPARATE-SLUG #2641] The `$SortF` prefix correction and the missing `Job` model entry in that same validator. Same file, same live PR, same conflict.
- [SEPARATE-SLUG #2736] Dropping heredoc bodies whose consuming command is not an interpreter. Its fix builds on the `_EXECUTABLE_CONTEXT` gate that exists only on PR #2686's branch, so it is not implementable from `main` today. [ORDERED] It is additionally sequenced behind PR #2686 merging, which is a human-gated review event.
- [SEPARATE-SLUG #2689] is **not** listed here — it is the whole point of this plan and is fully in scope.

Everything else the sweep surfaced is in scope and gets done in this plan, including `validate_file_contains.py`, which the tracking issue did not know about.

## Update System

No update system changes required. The four validators are hardlinked into place by the existing `scripts/update/hardlinks.py` machinery, which globs the directory rather than enumerating files, and the new `hook_utils/hook_target.py` is picked up the same way. No manifest registration changes: the two registered section validators keep their existing `PostToolUse` / `Write` entries, `validate_file_contains.py` keeps its args minus `--max-age`, and no new hook is added.

One generated artifact must be refreshed: `.opencode/SYNC_MANIFEST.json` records a SHA-256 per `.py` under `.claude/hooks/` via `scripts/sync_claude_to_opencode.py::write_plugin`'s recursive glob. It is generated, never hand-edited — rerun `python scripts/sync_claude_to_opencode.py` after the code lands so the new module and the four changed files are hashed.

## Agent Integration

No agent integration required. These are hook scripts the Claude Code harness invokes directly via generated `settings.json` entries; they are not reachable through `pyproject.toml [project.scripts]` and the bridge does not import them. The `hook_utils` package is internal to `.claude/hooks/` and is imported only by sibling hook scripts.

The one integration surface that matters is the reverse direction: these hooks gate the *agent's* `Write` tool, so the acceptance tests exercise them exactly as the harness does — JSON payload on stdin, no argv, via subprocess.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/hook-target-resolution.md` describing the contract: validators resolve their target from the hook payload, `None` means nothing-to-validate, and working-tree state is never an input to target selection. Include the tracked-`docs/plans/` reproducer as the canonical regression.
- [ ] Add an entry to `docs/features/README.md` index table (respect the sort order — `validate_features_readme_sort.py` enforces it).
- [ ] Update `docs/features/hook-manifest.md` to note that `hook_utils/` is the sanctioned home for shared validator logic and to record the `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` bootstrap as the standard import pattern.

### External Documentation Site
No external documentation site in this repo — nothing to update.

### Inline Documentation
- [ ] `hook_target.py`'s module docstring states the contract and cites #2682 / #2689 as the defects it exists to prevent.
- [ ] Each ported validator's `main()` keeps a short comment on why an explicit CLI path and a hook-derived path get different missing-file treatment.

### Test Suite Index
- [ ] Update the validation table in `tests/README.md`: bump the counts for `test_validate_verification_section.py` (9 → new total) and `test_validate_test_impact.py` (20 → new total), and add rows for `test_validate_documentation_section.py`, `test_validate_file_contains.py`, and `test_hook_target.py`.

## Success Criteria

- [ ] `grep -rn "find_newest_plan_file" .claude/` returns zero matches.
- [ ] `grep -rn "find_newest_file" .claude/hooks/validators/` returns zero matches.
- [ ] `hook_utils/hook_target.py` exists and is imported by all five validators in the family (the four ported plus `validate_no_gos_justification.py`).
- [ ] The #2689 reproducer passes: with `docs/plans/` containing a tracked anchor and another lane's untracked plan, a payload naming `docs/features/mine.md` exits 0 for all four ported validators. This currently exits 2 for three of them.
- [ ] A payload naming a genuinely deficient plan doc still exits 2, for each of the four.
- [x] All tests in `test_validate_no_gos_justification.py` pass, with exactly one deliberate change. **Amended during the patch round.** The criterion originally read "all 65 pass unchanged", which review round 1 made unsatisfiable: Tech Debt 2 required unifying explicit-argv semantics across the family, and `test_non_plan_path_passes_even_when_it_does_not_exist` pinned the *old* behavior (an operator-supplied path outside `docs/plans` silently ignored). It was split into `test_hook_derived_non_plan_path_passes_even_when_it_does_not_exist`, which preserves the real intent — scope filter before existence check, on the payload path — and `test_explicit_cli_path_bypasses_the_scope_filter`, which pins the new unified rule. The regression-proof value of the file is intact; only the one assertion that encoded the inconsistency changed.
- [ ] All 29 existing pure-function tests in the verification and test-impact files pass unchanged.
- [ ] Tests pass (`/do-test`) with `POPOTO_TEST_DB=12` via `scripts/pytest-clean.sh`.
- [ ] Documentation updated (`/do-docs`).
- [ ] No hook was disabled to complete the work; if one was, the PR records it and it is re-enabled.
- [ ] The PR body states that #2638 and #2641 belong to PR #2686 and that #2736 is sequenced behind it.

## Team Orchestration

**There is exactly one builder, and Tasks 1-3 run sequentially.** An earlier draft split the port across three parallel builders. That was wrong twice over: the three ports are the same mechanical change across files that differ only in a section name and a regex, so a split buys no wall-clock; and this session owns a single worktree, so concurrent `git add` / `git commit` from parallel agents race on `.git/index.lock`. "Commits never interleave" is not an outcome a plan can assert — it needs either a serialization mechanism or no concurrency. This plan chooses no concurrency. The only genuinely independent work is test authoring and documentation, which follow the build.

### Team Members

- **Builder**
  - Name: `hook-builder`
  - Role: Tasks 1-3 in order — create `hook_utils/hook_target.py`, port `validate_no_gos_justification.py` onto it without behavior change, port the three section validators, then port `validate_file_contains.py`
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `hook-test-engineer`
  - Role: Create the three new test files and add targeting classes to the two existing ones
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `hook-doc-writer`
  - Role: Feature doc, features README index row, hook-manifest convention note, `tests/README.md` table
  - Agent Type: documentarian
  - Resume: true

Final validation (Task 6) is performed by the code reviewer named in the Appetite line, not by a separate roster member — it runs the Verification table and reports, which needs no standing agent.

## Step by Step Tasks

### 1. Shared module and reference port
- **Task ID**: build-hook-target
- **Depends On**: none
- **Validates**: tests/unit/test_hook_target.py (create), tests/unit/test_validate_no_gos_justification.py
- **Informed By**: spike-1 (confirmed: `sys.path.insert(0, parent.parent)` + `from hook_utils.…` is the proven precedent in three sibling validators)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/hook_utils/hook_target.py` with `target_from_hook_input(hook_input) -> str | None` moved from `validate_no_gos_justification.py:119-135` — verbatim except for a new leading `if not isinstance(hook_input, dict): return None` — plus `read_hook_input() -> dict` that parses stdin, returns `{}` on any exception, and **also** returns `{}` when the parsed value is not a dict.
- Port `validate_no_gos_justification.py` to import both and delete its private copy. Behavior must not change.
- Add the `.claude/hooks` `sys.path` insert to `tests/unit/test_validate_no_gos_justification.py` per `test_validate_sdlc_on_stop.py:12-17`.
- Run `POPOTO_TEST_DB=12 scripts/pytest-clean.sh tests/unit/test_validate_no_gos_justification.py` — all 65 must pass before anything else starts.

### 2. Port the three section validators
- **Task ID**: build-section-validators
- **Depends On**: build-hook-target
- **Validates**: tests/unit/test_validate_verification_section.py, tests/unit/test_validate_test_impact.py, tests/unit/test_validate_documentation_section.py (create)
- **Informed By**: spike-2 (three verbatim copies), spike-4 (single coherent Write per file, never partial edits)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite each of the three validators' `main()` to use `read_hook_input` + `target_from_hook_input`.
- Delete all three `find_newest_plan_file` definitions and their `subprocess` imports if now unused.
- Apply the plan-path scope filter **before** any `Path.exists()` call.
- Preserve the CLI-vs-hook missing-path distinction: explicit argv path missing → exit 2; hook-derived path missing → exit 0.
- Touch no `extract_*` or `is_section_complete` function.

### 3. Port the file-contains validator
- **Task ID**: build-file-contains
- **Depends On**: build-section-validators
- **Validates**: tests/unit/test_validate_file_contains.py (create)
- **Informed By**: spike-2 (payload read at `:266` is an early-exit only; `find_newest_file` at `:150` still selects the target)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Use the resolved payload target as the file to check; keep `--directory` / `--extension` as the scope filter.
- Delete `find_newest_file`, `get_git_new_files`, `get_recent_files`, `get_git_committed_files`, `get_committed_file_content`.
- Remove the `--max-age` flag and its plumbing; it is not passed by the manifest registration and is meaningless once mtime stops selecting the target.
- Exit 0 when no payload target resolves and no positional file was given.
- Do not change the manifest entry's `--contains` arguments.

### 4. Test suite
- **Task ID**: build-tests
- **Depends On**: build-section-validators, build-file-contains
- **Validates**: all five test files
- **Informed By**: spike-3 (no test file exists for documentation_section or file_contains; existing files cover only pure functions; `run_hook` is the template), Race 1 (the fixture MUST include a tracked anchor in `docs/plans/`)
- **Assigned To**: hook-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_hook_target.py`: `file_path`, `notebook_path`, empty string, missing/non-dict `tool_input`, non-string path types, and malformed stdin.
- Cover the syntactically-valid non-dict payloads explicitly — `null`, `[1,2]`, `"str"`, `42` — at both levels: `read_hook_input` returns `{}`, `target_from_hook_input` returns `None`, and `run_hook("null", cwd)` / `run_hook("[1,2]", cwd)` exit 0 rather than raising `AttributeError`. This is the gap the 65 inherited tests do not close.
- Create `tests/unit/test_validate_documentation_section.py` and `tests/unit/test_validate_file_contains.py` from scratch, each with its own `run_hook` helper.
- Add a targeting class plus the `.claude/hooks` `sys.path` insert to the verification and test-impact test files.
- Every new test file builds the reproducer fixture with a **tracked** anchor file in `docs/plans/` — without it the fixture false-passes against unfixed code and proves nothing.
- Add a `test_newest_plan_fallback_is_gone`-style assertion to each ported validator's tests.
- Run with `POPOTO_TEST_DB=12` via `scripts/pytest-clean.sh`, scoped to the five files only.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Validates**: `validate_features_readme_sort.py` must pass on the edited `docs/features/README.md`; no test file owns this task's output, so the Verification table's lint/format rows and the sort validator are its proof.
- **Assigned To**: hook-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/hook-target-resolution.md`; add the sorted index row in `docs/features/README.md`.
- Update `docs/features/hook-manifest.md` with the shared-module convention.
- Update the validation table in `tests/README.md` with new counts and new rows.
- Do **not** regenerate `.opencode/SYNC_MANIFEST.json` here — Task 6 owns it, after the last code edit lands.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-hook-target, build-section-validators, build-file-contains, build-tests, document-feature
- **Validates**: the Verification table itself — this task authors no code, so the table is its acceptance criterion rather than a test file.
- **Assigned To**: code reviewer (per the Appetite line; no standing roster member)
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the Verification table and report pass/fail per row.
- Rerun `python scripts/sync_claude_to_opencode.py` **after the last code edit has landed**, then assert `git diff --exit-code .opencode/SYNC_MANIFEST.json` is clean. Ordering is load-bearing: regenerating before the final edit leaves silent hash drift, which is why this lives here and not in Task 5.
- Confirm no hook was disabled and none is left disabled.
- Confirm the diff touches none of PR #2686's three files.
- Confirm the PR body states that #2638 and #2641 belong to PR #2686 and that #2736 is sequenced behind it. This criterion has no other owner — it is authored here, not left to the builder's memory at PR time.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guesser fully deleted | `grep -rl "find_newest_plan_file" .claude/hooks/ \| wc -l` | match count == 0 |
| Contains-guesser deleted | `grep -rl "find_newest_file" .claude/hooks/validators/ \| wc -l` | match count == 0 |
| Mtime selection gone | `grep -rl "newest_mtime" .claude/hooks/validators/ \| wc -l` | match count == 0 |
| Shared module exists | `test -f .claude/hooks/hook_utils/hook_target.py` | exit code 0 |
| All five import the helper | `grep -rl "from hook_utils.hook_target import" .claude/hooks/validators/ \| wc -l` | output contains 5 |
| No stdin discarded | `grep -ln "^\s*json.load(sys.stdin)$" .claude/hooks/validators/validate_documentation_section.py .claude/hooks/validators/validate_test_impact_section.py .claude/hooks/validators/validate_verification_section.py .claude/hooks/validators/validate_file_contains.py .claude/hooks/validators/validate_no_gos_justification.py \| wc -l` | match count == 0 |
| No-Gos regression intact | `POPOTO_TEST_DB=12 scripts/pytest-clean.sh tests/unit/test_validate_no_gos_justification.py -q` | exit code 0 |
| Family tests pass | `POPOTO_TEST_DB=12 scripts/pytest-clean.sh tests/unit/test_hook_target.py tests/unit/test_validate_documentation_section.py tests/unit/test_validate_test_impact.py tests/unit/test_validate_verification_section.py tests/unit/test_validate_file_contains.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/ tests/unit/` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/ tests/unit/` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_hook_target.py tests/unit/test_validate_documentation_section.py tests/unit/test_validate_file_contains.py` | exit code 1 |
| PR #2686 files untouched | `git diff --name-only main... -- .claude/hooks/validators/validate_no_raw_redis_delete.py .claude/hooks/dispatch/pre_tool_use_bash.py tests/unit/test_validate_no_raw_redis_delete.py` | output does not contain .py |
| Anti-criterion: no git target selection | `grep -rl 'porcelain.*directory' .claude/hooks/validators/ \| wc -l` | match count == 0 |
| Non-dict payload never raises | `printf 'null' \| .venv/bin/python .claude/hooks/validators/validate_documentation_section.py` | exit code 0 |
| Sync manifest regenerated and clean | `python scripts/sync_claude_to_opencode.py && git diff --exit-code .opencode/SYNC_MANIFEST.json` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | **`read_hook_input()`'s contract guards exceptions but not a syntactically-valid non-dict payload.** The plan specifies it swallows `json.JSONDecodeError` / `OSError` / `EOFError`, but stdin of `null` or `[1,2]` parses cleanly and yields a non-dict. `target_from_hook_input`'s verbatim source (`validate_no_gos_justification.py:129`, `tool_input = hook_input.get("tool_input")`) then raises `AttributeError`. None of the 65 acceptance tests or the plan's Failure Path Test Strategy cases feed a non-dict top-level JSON value through `main()`, so the gap survives the "65 tests pass unchanged" gate untouched. | Applied in the post-critique revision | `read_hook_input()` must be `parsed = json.loads(raw); return parsed if isinstance(parsed, dict) else {}` — not a bare `return json.loads(raw)`. Independently, `target_from_hook_input` needs `if not isinstance(hook_input, dict): return None` before the first `.get`, because the plan documents the two functions as separately callable. Add `run_hook("null", cwd)` and `run_hook("[1,2]", cwd)` cases to `test_hook_target.py` and to each ported validator's targeting class. |
| CONCERN | Risk & Robustness | **The `.opencode/SYNC_MANIFEST.json` refresh is a manual step with no Verification row.** Task 5 says to rerun `python scripts/sync_claude_to_opencode.py`, but nothing in Success Criteria or the Verification table asserts the manifest hashes actually match the landed files. If the step is skipped, interrupted, or run before the last edit lands, the drift is silent. | Applied in the post-critique revision | Add a Verification row `python scripts/sync_claude_to_opencode.py && git diff --exit-code .opencode/SYNC_MANIFEST.json` with expected "exit code 0", and add it to Task 6's checklist for `hook-validator-check`. Ordering is load-bearing: the regeneration must run after the last code edit, so the row belongs in Task 6, not Task 5. |
| CONCERN | Scope & Value, Risk & Robustness | **The Team Orchestration roster contradicts the declared team and is heavier than the work.** Appetite says "Team: Solo dev, PM, code reviewer" (`:120`) while Team Orchestration names five agents (`:298-326`); three of them (`helper-builder`, `section-builder`, `contains-builder`) perform the same mechanical port across files that are near-identical outside their section name and regex. Separately, Tasks 2 and 3 are both `Parallel: true` inside the single shared worktree, and "commits never interleave" is asserted as an outcome with no serialization mechanism named — concurrent `git add`/`git commit` in one working tree races on `.git/index.lock`. | Applied in the post-critique revision | Collapse `helper-builder` / `section-builder` / `contains-builder` into a single `builder` running Tasks 1-3 sequentially (`Parallel: false`); there is one worktree, so parallelism buys no wall-clock and costs an index-lock race. Keep `hook-test-engineer`; fold Task 6's checklist into it or into the Appetite line's code reviewer. If parallelism is kept instead, the plan must name the serialization mechanism (a retryable commit with lock backoff, or an existing SDLC lease step) rather than asserting the outcome. |
| CONCERN | History & Consistency | **Task 5 names an assignee whose declared type contradicts the task's own `Agent Type`.** `document-feature` sets `Assigned To: hook-test-engineer` but `Agent Type: documentarian`; Team Members declares `hook-test-engineer` as `Agent Type: test-engineer` with a single test-authoring role, and no `documentarian` member exists in the roster. | Applied in the post-critique revision | Dispatch resolves `Assigned To` against the Team Members table, so Task 5's `Agent Type: documentarian` is either dead text or is silently overridden to `test-engineer` at dispatch time. Pick one: add a `hook-doc-writer` member with `Agent Type: documentarian` and reassign Task 5 to it, or change Task 5's `Agent Type` to `test-engineer`. Do not leave the two fields disagreeing. |
| CONCERN | History & Consistency | **Three Verification rows state an expectation the command cannot produce.** "Guesser fully deleted", "Contains-guesser deleted", and "Mtime selection gone" use `grep -rc "<pattern>" <dir>` with Expected "match count == 0", but `grep -rc` over a directory prints one `path:count` line per scanned file (including zero-match files), never a bare aggregate. A validator reading the row verbatim gets multi-line `path:0` output with no single number to compare. | Applied in the post-critique revision | Rewrite each of the three rows as `grep -rl "<pattern>" <dir> \| wc -l` with Expected `0` — the aggregate form the "All five import the helper" row two lines below already uses correctly. The exit-code reading happens to be correct today (`grep -rc` exits 1 only when no file matches), so this is a row-legibility defect, not a false pass — but a checker asserting on stdout will misread it. |
| NIT | Scope & Value | **Architectural Impact overstates CLI compatibility.** Line 111 says `validate_file_contains.py`'s "CLI flags stay compatible", which contradicts Technical Approach `:168` and Task 3 `:367`, both of which delete `--max-age` and its plumbing. | Applied in the post-critique revision | Edit `:111` to read "its CLI flags stay compatible except `--max-age`, which is removed since target selection no longer depends on mtime." |
| NIT | Structural check | **Tasks 5 and 6 carry no `Validates:` field.** Every other task names the tests that prove it. `document-feature` and `validate-all` produce docs and a report respectively, so the omission may be deliberate, but it is undeclared. | Applied in the post-critique revision | Either add a `Validates:` line to each (Task 5 can point at `validate_features_readme_sort.py` and the docs checks; Task 6 at the Verification table itself) or state inline that these two tasks are validated by the Verification table rather than by a test file. |
| NIT | Structural check | **One Success Criterion maps to no task.** "The PR body states that #2638 and #2641 belong to PR #2686 and that #2736 is sequenced behind it" has no owning step — Task 6 validates the diff and the criteria but does not author the PR body. | Applied in the post-critique revision | Add the PR-body statement to Task 6's checklist, or to whichever step opens the PR, so the criterion has an owner rather than depending on the builder remembering it at PR time. |

### Note on the grep-row finding

One critique finding is recorded as applied but was overstated, and the record should say so. The claim was that `grep -rc <pattern> <dir>` with Expected `match count == 0` cannot produce a comparable value. The template's own spec for that expectation explicitly supports the shape — "passes when every non-blank stdout line is `0` **or ends with `:0`** (supports `grep -c`, `grep -rc`, and `grep -r … | wc -l` shapes)" — and an empirical check confirms `grep -rc` emits exactly the `path:0` lines the spec accommodates. The critique itself conceded this was "a row-legibility defect, not a false pass." The rows were rewritten to the `grep -rl … | wc -l` form regardless, because a single aggregate number is easier for a human reviewer to read and the change costs nothing. No behavior depended on it.

## Resolved Questions

Both questions raised at draft time are settled; neither blocks build.

1. **Scope narrowing to #2689 only — confirmed.** The Freshness Check establishes that PR #2686 is a live lane holding `validate_no_raw_redis_delete.py` (a commit and a re-review comment both landed within the hour before planning), and that #2736's fix is not implementable from `main` at all because it builds on an `_EXECUTABLE_CONTEXT` gate that exists only on that branch. Duplicating #2638/#2641 here would produce a conflicting diff in a file another lane owns. The routing brief for this lane explicitly permitted resolving fewer than four issues provided the PR explains which and why, and the PR will. #2736 and the other two are tagged `[SEPARATE-SLUG]` in No-Gos.
2. **Folding in `validate_file_contains.py` — confirmed.** It is the same bug class, in the same directory, in the same review blast radius, and it is the highest-severity instance: proven by direct reproduction to block a *compliant* `docs/plans/lane-a.md` write with an error naming a different lane's `docs/plans/zzz-other-lane.md`. Splitting it out would leave the class alive in a registered, live hook while shipping a PR that claims to have eliminated it. It goes in.
