---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2692
last_comment_id: none
---

# Fast-path importtime test false-positives on the redis-flush-guard .pth shim

## Problem

`tests/unit/test_post_tool_use_fast_path.py::test_counter_only_call_does_not_import_popoto` pins a real optimization: the PostToolUse hook fires on *every* tool call, so the common counter-only path must early-return without dragging in the popoto-heavy `hook_utils.memory_bridge`. The test spawns the hook under `python -X importtime` and inspects the resulting import graph.

It inspects that graph with plain substring containment over the whole stderr dump:

```python
heavy = ("popoto", "redis", "config.settings", "models.memory", "models.agent_session")
offenders = [m for m in heavy if m in proc.stderr]
```

`"redis"` is a substring of `tools.redis_flush_guard` and of `_redis_flush_guard_boot`. Neither is the `redis` package. When the redis-flush-guard `.pth` shim is installed into the venv, the interpreter attempts that import at startup, CPython emits an `importtime` row for the attempt (even when it raises and is swallowed), and the test reports `AssertionError: counter-only path imported heavy modules: ['redis']` for a hook that imported nothing of the sort.

**Current behavior:** a module whose *name* merely contains a heavy module's name as a substring fails the assertion. The hook is blamed for an import it never performed.

**Desired outcome:** the assertion matches on module *identity* — exact name, or dotted descendant — so `tools.redis_flush_guard` is ignored while a genuine top-level `redis` or `popoto.models` import is still caught.

## Freshness Check

**Baseline commit:** `0dd8e70f2` ("Sync the db-derivation-guard plan doc on main to the branch head")
**Issue filed at:** 2026-08-07T20:21:57Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tests/unit/test_post_tool_use_fast_path.py:77-79` — the issue claimed the substring scan lives here — **still holds, verbatim, at the same lines.**
- `.claude/hooks/post_tool_use.py:384-398` — the issue claimed the fast path is unchanged and still early-returns before `from hook_utils.memory_bridge import recall` — **still holds.** `git log --since="2026-08-07T20:21:57Z" -- .claude/hooks/post_tool_use.py tests/unit/test_post_tool_use_fast_path.py` returns **no commits**. Neither file has been touched since the issue was filed.
- `.venv/lib/python3.14/site-packages/zzz_redis_flush_guard.pth` and `_redis_flush_guard_boot.py` — the issue claimed these are installed in the shared main venv — **gone.** Both have been removed.
- `tools/redis_flush_guard.py` — the issue claimed it does not exist on `main` — **still holds, still absent.**

**Cited sibling issues/PRs re-checked:**
- #2645 — still **open**. The redis-flush-hardening work.
- PR #2680 (`session/redis-flush-hardening`) — still **open, unmerged**. `gh pr view 2680 --json files` confirms it adds both `tools/redis_flush_guard.py` and `scripts/update/redis_flush_guard_pth.py`. Merging it reintroduces the shim *with the module present*, converting today's environment-dependent false positive into a deterministic failure. This is the plan's urgency.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/redis-flush-hardening.md` (status `Ready`, tracking #2645). It uses `-X importtime` for its own startup-budget assertion in `tests/unit/test_redis_flush_guard_prod.py` but does **not** touch `tests/unit/test_post_tool_use_fast_path.py`. No file-level conflict. The only coupling is ordering: this must land before #2680 merges.

**Notes — the load-bearing drift.** The issue's reproduction **no longer reproduces**. `python -X importtime -c "pass"` emits no `redis`/`guard` lines, and `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q` reports **4 passed**. The *defect* is unchanged and still in the code; only its environmental trigger has been cleaned away. The direct consequence for this plan: **red-first cannot be observed from environment state and must be synthesized** from a crafted `importtime` fixture. The Verification and Step-by-Step sections below specify exactly how.

## Prior Art

- `gh issue list --state closed --search "importtime false positive substring heavy module"` — **no results.** This defect has not been attempted before.
- `gh pr list --state merged --search "importtime"` — #1833 (calendar work-logging redesign + hook hardening) and #1255 (memory progressive disclosure + memory MCP server). Neither touches this test or its assertion; #1255 is the PR whose optimization this test was written to pin.
- No prior fix exists for this problem, so there is no **Why Previous Fixes Failed** section.

## Research

**Queries used:**
- `CPython -X importtime output format columns "imported package" parse module name stderr`

Supplemented by direct empirical verification against the interpreter this repo pins in `.python-version` (CPython 3.14.5), which is stronger evidence than the search for the format question.

**Key findings:**

- The `-X importtime` row shape is stable and documented since Python 3.7: `import time: {self_us} | {cumulative_us} | {indent}{module_name}`, written to **stderr**, introduced by a header row `import time: self [us] | cumulative | imported package`. Confirmed empirically on 3.14.5. Source: <https://github.com/dominikwalk/importtime-output-wrapper>
- **The module name is the final pipe-delimited column.** Its leading whitespace encodes tree depth and is safe to strip. This is exactly the parse the fix needs.
- **Dotted submodule names appear in full** (`encodings.aliases`, `json.scanner`) rather than as bare leaves. This is what makes `name.startswith(heavy + ".")` a correct and sufficient descendant test — verified against real output showing `json.scanner`, `json.decoder`, `json.encoder`.
- The header row's final column is `imported package`, which contains an **interior space**. No real module name can. That gives a one-line, generic way to skip the header without pattern-matching its exact text.
- `-X importtime=2` emits additional rows of the form `import time: cached    | cached     | {name}` — same three-column layout, so the same parser handles them. Verified on 3.14.5.
- **No tree-drawing characters** (`│ ├ └ ─`) are emitted in either mode on 3.14.5. The issue's suggestion to strip them is therefore defensive rather than currently necessary; the plan keeps the strip as cheap forward-insurance but does not treat it as a behavior under test.
- CPython documents that importtime output "may be broken in multi-threaded applications." Not a concern here (the probed subprocess is single-threaded during import), but it is why the parser must skip malformed rows rather than assume every `import time:` line splits into three fields. Captured as Risk 2.

Finding saved to memory (`47a9dd4e07a644feabb3a608e94193a9`) for reuse by future plans that parse importtime output.

## Data Flow

1. **Entry point**: `test_counter_only_call_does_not_import_popoto` builds a synthetic PostToolUse event dict for an ignored tool (`Read`).
2. **Subprocess**: `subprocess.run([sys.executable, "-X", "importtime", str(HOOK)], input=json.dumps(event), ...)`. The child interpreter processes `site` (executing any installed `.pth`), then runs `.claude/hooks/post_tool_use.py`.
3. **CPython importtime writer**: emits one row per import *attempt* — including attempts that raise and are swallowed — to the child's **stderr**, captured into `proc.stderr`.
4. **Detection (the defect)**: `proc.stderr` is treated as one opaque string and scanned for heavy-module names as substrings. Module boundaries, the `|` column structure, and the distinction between "the interpreter preloaded this" and "the hook imported this" are all invisible at this layer.
5. **Output**: `offenders` list feeds the assertion message.

The fix is applied at layer 4 and only at layer 4: `proc.stderr` is parsed into a list of module names first, and heavy-module membership is decided over that list.

## Architectural Impact

- **New dependencies**: none. Standard library string operations only.
- **Interface changes**: two new module-level helpers in the test file. No production code, no public API, no importable surface outside `tests/`.
- **Coupling**: reduced. The assertion currently couples to the *textual accident* of the whole stderr dump; after the change it couples to the documented three-column importtime row format.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — a single test file, revertable with `git revert`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified and constrained by the human; no ambiguity to resolve)
- Review rounds: 1 (standard `/do-pr-review`)

## Prerequisites

No prerequisites — this work has no external dependencies. It touches one test file, adds no imports beyond the standard library, and requires no secrets, services, or environment setup beyond the repo's pinned interpreter.

## Solution

### Key Elements

- **`_importtime_module_names(stderr)`**: turns a `-X importtime` stderr dump into a list of module names. Skips non-`import time:` noise, skips malformed rows, skips the header, strips depth indentation.
- **`_heavy_import_offenders(stderr)`**: decides which heavy modules are genuinely in the graph, by *identity* — `name == heavy` or `name.startswith(heavy + ".")` — over the parsed names.
- **`_HEAVY_MODULES`**: the existing heavy tuple, lifted to module scope so both the live probe and the isolated unit tests share exactly one definition of "heavy". Two copies would let the tested logic and the asserted logic drift apart, which is the failure mode that makes a green test meaningless.
- **Two isolated detection tests**: crafted stderr fixtures that exercise the detector without spawning a subprocess, so the behavior is provable independent of what any machine's venv happens to have installed.

### Flow

`test_counter_only_call_does_not_import_popoto` → spawn hook under `-X importtime` → **`_heavy_import_offenders(proc.stderr)`** → assert empty

`test_heavy_detection_ignores_lookalike_module_names` → **`_heavy_import_offenders(_GUARD_SHIM_DUMP)`** → assert empty (this is the synthesized red)

`test_heavy_detection_still_catches_real_imports` → **`_heavy_import_offenders(_REAL_HEAVY_DUMP)`** → assert all three found (this is the over-loosening guard)

### Technical Approach

Everything below lands in `tests/unit/test_post_tool_use_fast_path.py`. No other file is modified.

**Parsing.** Per row: require the `import time:` prefix; `split("|")`; require at least 3 fields; take `parts[-1]`; `.strip()` then `.lstrip("│├└─ \t")` to shed depth indentation and any future tree-drawing characters; drop the result if it is empty **or contains a space** (the header's `imported package` is the only such row, and no legal module name has an interior space).

**Matching.** For each heavy module `h` and each parsed name `n`, flag `h` when `n == h or n.startswith(h + ".")`. Return `sorted(set(...))` so the assertion message is deterministic across runs.

Corrected/confirmed anchors for the builder:
- Substring scan to replace: `tests/unit/test_post_tool_use_fast_path.py:77-79` (verified current at `0dd8e70f2`).
- Hook fast path under test, unchanged and **not to be modified**: `.claude/hooks/post_tool_use.py:384-398`.

**Why not exclude startup imports.** Matching on identity removes the reported false positive completely, because the offending names (`tools.redis_flush_guard`, `_redis_flush_guard_boot`) are not `redis` and not descendants of `redis`. Separating interpreter-startup imports from hook imports is a different and larger change to the probe's contract; it is deferred to #2751.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers are introduced. The two new helpers are pure string functions with no `try`/`except`; malformed input is handled by explicit `continue` guards (non-`import time:` prefix, fewer than 3 pipe-delimited fields, empty or space-containing name), each of which is a normal branch rather than a swallowed error. The existing `except Exception: return None` in `.claude/hooks/post_tool_use.py` is **not in scope** — that file is not modified.

### Empty/Invalid Input Handling
- [ ] `_importtime_module_names("")` returns `[]` and `_heavy_import_offenders("")` returns `[]` — no exception, no `IndexError` from `parts[-1]`, because the loop body never executes.
- [ ] A stderr dump containing only the header row yields `[]` — covered by both crafted fixtures, which each begin with the real header line.
- [ ] A truncated or interleaved row (`import time:` prefix but fewer than 3 fields) is skipped rather than raising — this is the multi-threaded-interleaving case CPython warns about.
- [ ] Not agent-output processing; no silent-loop risk.

### Error State Rendering
- [ ] The assertion's failure message is the user-visible output. It must still name the offending modules: `f"counter-only path imported heavy modules: {offenders}"`. `sorted(set(...))` makes that message deterministic, which the current tuple-order list is not once the detector changes.
- [ ] The synthesized red run (Task 1) is itself the proof that the failure path renders correctly — the captured FAIL output goes into the PR body.

## Test Impact

- [ ] `tests/unit/test_post_tool_use_fast_path.py::test_counter_only_call_does_not_import_popoto` — UPDATE: replace the inline substring comprehension at lines 77-79 with a call to the new `_heavy_import_offenders(proc.stderr)` helper. The subprocess spawn, the event payload, and the `returncode == 0` assertion are unchanged.
- [ ] `tests/unit/test_post_tool_use_fast_path.py::test_heavy_detection_ignores_lookalike_module_names` — ADD: crafted-fixture test asserting the detector rejects `tools.redis_flush_guard` and `_redis_flush_guard_boot`. This is the red-first case.
- [ ] `tests/unit/test_post_tool_use_fast_path.py::test_heavy_detection_still_catches_real_imports` — ADD: crafted-fixture test asserting the detector still catches a genuine top-level `redis`, a `popoto.models` descendant, and an exact `config.settings`.
- [ ] `tests/unit/test_post_tool_use_fast_path.py::test_ignored_tool_event_exits_zero`, `::test_counter_only_call_bumps_sidecar_counter`, `::test_fast_path_state_matches_real_recall` — NO CHANGE: they do not inspect importtime output.
- [ ] No other test file is affected. `grep -rn importtime tests/ .claude/ scripts/` finds only this file's two live uses plus a prose comment at `tests/integration/test_memory_mcp_server.py:122`, which does not scan stderr.
- [ ] No xfail markers exist for this bug — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` surfaces nothing related, so there is nothing to convert to a hard assertion.

## Rabbit Holes

- **Rebuilding the probe to exclude interpreter-startup imports.** `-S` isolation, `site` filtering, or diffing against a `python -X importtime -c pass` baseline. Tempting because it is the "real" fix for the general class, and genuinely larger: `-S` may break the hook's own `sys.path` resolution, and baseline diffing needs the two legs to be captured under provably identical conditions. Deferred to #2751.
- **Touching `scripts/update/redis_flush_guard_pth.py`.** That file does not exist on `main`; it is created by PR #2680. Editing it from this branch means either inventing it here or cross-branch surgery, and it would collide at merge.
- **Cleaning the shared venv.** Already clean (see Freshness Check). Any temptation to add a cleanup step is work against a condition that no longer holds.
- **Generalizing the parser into `tools/` or a shared test helper.** One caller. A module-level function in the test file is the right altitude; promoting it invites an import surface and a second consumer that does not exist.
- **Widening `_HEAVY_MODULES`.** The tuple's membership is a separate judgment from how membership is *tested*. Changing both at once makes the red signal unattributable.

## Risks

### Risk 1: Over-tightening silently disarms the test
**Impact:** A parser that returns `[]` for everything — a wrong prefix check, a bad column index, an overzealous filter — makes the test pass unconditionally. The optimization it pins would then be free to regress, undetected, and the failure would be invisible precisely because it looks like success.
**Mitigation:** `test_heavy_detection_still_catches_real_imports` is the guard, and it is mandatory, not optional. It asserts three *positive* detections (top-level `redis`, descendant `popoto.models` → `popoto`, exact `config.settings`) against a crafted dump. A disarmed parser fails it. It is written in the same commit as the red test, before the parser exists.

### Risk 2: importtime rows are not always well-formed
**Impact:** CPython documents that importtime output "may be broken in multi-threaded applications." A parser that assumes every `import time:` line splits cleanly into three fields could raise `IndexError` and turn a passing test into an error.
**Mitigation:** Explicit `len(parts) < 3: continue` guard, plus the empty-name guard. Malformed rows are skipped, never fatal. Called out in the Failure Path Test Strategy above.

### Risk 3: The fix lands after PR #2680 merges
**Impact:** #2680 reintroduces `tools/redis_flush_guard.py` and the `.pth` installer. Once merged, the shim's import *succeeds*, the row is emitted, and `test_counter_only_call_does_not_import_popoto` fails deterministically on every machine that has run `/update` — a red suite for a non-bug, on `main`.
**Mitigation:** Small appetite, one file, no dependencies — this is deliberately sized to land first. It has no dependency on #2680 and can merge at any time. Noted as an ordering No-Go so the sequencing is explicit rather than assumed.

### Risk 4: Red-first is synthesized, not observed
**Impact:** The test currently passes on `main` (verified: 4 passed). A reviewer cannot reproduce a failing baseline from the issue's instructions, so "the fix works" risks resting on assertion rather than evidence.
**Mitigation:** Task 1 makes the red observable by construction: the new crafted-fixture tests are written *first*, against the **existing substring logic**, and `test_heavy_detection_ignores_lookalike_module_names` fails with `['redis'] != []`. That FAIL output is captured verbatim and pasted into the PR body. The red is real, reproducible from the repo alone, and independent of any machine's venv contents.

## Race Conditions

No race conditions identified. The two new helpers are pure synchronous string functions over an already-captured string, with no shared state, no I/O, and no concurrency. `subprocess.run` in the existing test is fully blocking and its stderr is read to completion before parsing begins. The `clean_session` fixture already isolates sidecar state per test, and the two new tests touch no filesystem state at all.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2751] Excluding interpreter-startup / `.pth` / `site` imports from the measured graph (the issue's suggested step 2) — `-S`-equivalent isolation or baseline diffing against `python -X importtime -c pass`. A redesign of the probe's contract, not an assertion tightening. Filed as #2751.
- [SEPARATE-SLUG #2645] Making the `.pth` installer refuse to install, and self-clean, when `tools/redis_flush_guard.py` is not importable from the target checkout (the issue's suggested step 3). The installer is `scripts/update/redis_flush_guard_pth.py`, which exists only on `session/redis-flush-hardening` (PR #2680) and not on `main`. It belongs to that branch.
- [ORDERED] Coordinating the merge order with PR #2680 — #2680 is human-gated review and merge on a separate lane. This plan cannot merge it or block it; it can only be sized to land first.
- [SEPARATE-SLUG #2645] Any change to `tools/redis_flush_guard.py`, `_redis_flush_guard_boot.py`, or the venv's `zzz_redis_flush_guard.pth`.

## Update System

No update system changes required. This is a test-only change: no new dependency, no config file, no `.pth`, no entry point, no migration. `/update` propagates it as an ordinary `git pull` with nothing to run.

Note the inverse relationship worth stating plainly: `/update` is what *installs* the `.pth` that triggers this false positive once #2680 lands. This plan does not change `/update`; it makes the test immune to what `/update` installs.

## Agent Integration

No agent integration required. This changes a file under `tests/`, which is invoked by pytest only. No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP surface, no bridge import, no new reachable capability. The agent's existing route to this code is `./scripts/pytest-clean.sh`, which is unchanged.

## Documentation

No documentation changes needed. The change is confined to one test file and introduces no new capability, configuration, workflow, or public interface — nothing in `docs/features/` describes the assertion internals of an individual test, and adding a feature doc for a two-helper test-file fix would violate the repo's no-historical-artifacts principle by documenting a bug's remediation rather than a status quo.

The rationale that must survive lives at the point of use instead:

- [ ] Inline docstring on `_importtime_module_names` recording the three-column `-X importtime` row contract (`self | cumulative | indented name`) verified against CPython 3.14.5, so a future reader does not have to rediscover the format.
- [ ] Inline docstring on `_heavy_import_offenders` stating why matching is on module identity and never substring, naming `tools.redis_flush_guard` / `_redis_flush_guard_boot` as the concrete false positive and citing #2692.

`tests/README.md` needs no entry: it indexes feature markers and suite-level blind spots, and this adds neither.

## Success Criteria

- [ ] `test_heavy_detection_ignores_lookalike_module_names` exists, and its FAIL output against the pre-fix substring logic is pasted verbatim in the PR body (red-first paper trail)
- [ ] `test_heavy_detection_still_catches_real_imports` passes both before and after the parser change, proving the fix did not disarm the detector
- [ ] `test_counter_only_call_does_not_import_popoto` routes through `_heavy_import_offenders` and no longer contains an inline substring comprehension
- [ ] `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q` reports 6 passed
- [ ] `git diff --name-only main...HEAD` lists exactly one file: `tests/unit/test_post_tool_use_fast_path.py`
- [ ] `.claude/hooks/post_tool_use.py` is untouched
- [ ] Tests pass (`/do-test`) — with the four known pre-existing `main` baseline failures excluded: `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_all_entries_have_required_fields`, two nodes in `tests/unit/test_session_modal_liveness_render.py`, and `tests/unit/test_update_hardlinks.py::test_no_husk_directories_in_skill_roots`
- [ ] Documentation updated (`/do-docs`) — inline docstrings only, per the Documentation section
- [ ] No agent integration to grep for; the Agent Integration section asserts none is required

## Team Orchestration

### Team Members

- **Builder (test-detection)**
  - Name: `importtime-detection-builder`
  - Role: Implement the red-first crafted-fixture tests, then the identity-matching parser, then rewire the live probe. Single file.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (test-detection)**
  - Name: `importtime-detection-validator`
  - Role: Verify the red-first paper trail is real, the detector is not disarmed, and the diff touches exactly one file.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Red-first: add crafted-fixture detection tests against the EXISTING substring logic

- **Task ID**: build-red-fixtures
- **Depends On**: none
- **Validates**: `tests/unit/test_post_tool_use_fast_path.py`
- **Informed By**: Freshness Check (the test passes on `main`, so red must be synthesized); Research (verified 3.14.5 row format, so the fixtures are faithful)
- **Assigned To**: `importtime-detection-builder`
- **Agent Type**: test-engineer
- **Parallel**: false

- Lift the heavy tuple from inside `test_counter_only_call_does_not_import_popoto` to a module-level `_HEAVY_MODULES` constant with the same five entries, unchanged: `("popoto", "redis", "config.settings", "models.memory", "models.agent_session")`.
- Add a module-level `_heavy_import_offenders(stderr, heavy=_HEAVY_MODULES)` whose body is, for now, **the existing substring logic verbatim**: `return [m for m in heavy if m in stderr]`. Do not implement the parser yet — the point of this task is to produce an observable failure.
- Add two module-level crafted stderr fixtures, each beginning with the real header row `import time: self [us] | cumulative | imported package`:
  - `_GUARD_SHIM_DUMP` — contains `tools.redis_flush_guard`, `_redis_flush_guard_boot` (with realistic leading indentation), a benign `site` row, and one `-X importtime=2`-shaped cached row `import time: cached    | cached     | _redis_flush_guard_boot`. Contains **no** genuine heavy module.
  - `_REAL_HEAVY_DUMP` — contains an indented top-level `redis` row, a `popoto.models` row, and a `config.settings` row.
- Add `test_heavy_detection_ignores_lookalike_module_names`: `assert _heavy_import_offenders(_GUARD_SHIM_DUMP) == []`.
- Add `test_heavy_detection_still_catches_real_imports`: compare as a **set** — `assert set(_heavy_import_offenders(_REAL_HEAVY_DUMP)) == {"redis", "popoto", "config.settings"}`. Set comparison is deliberate: it passes under both the old tuple-ordered logic and the new sorted logic, so the *only* red in this task is the false positive, making the signal unambiguously attributable.
- Run `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q` and confirm exactly one failure: `test_heavy_detection_ignores_lookalike_module_names`, reporting `['redis'] != []`.
- **Capture that FAIL output verbatim** into the commit message and set it aside for the PR body. This is the red-first paper trail and the only evidence a reviewer can reproduce from the repo alone.
- Commit this checkpoint to `session/fastpath-importtime-false-positives` before proceeding.

### 2. Green: replace the substring body with module-identity parsing

- **Task ID**: build-identity-parser
- **Depends On**: build-red-fixtures
- **Validates**: `tests/unit/test_post_tool_use_fast_path.py`
- **Informed By**: Research (module name is the final `|`-delimited column; dotted names appear in full; header's final column has an interior space; `-X importtime=2` shares the layout)
- **Assigned To**: `importtime-detection-builder`
- **Agent Type**: test-engineer
- **Parallel**: false

- Add `_importtime_module_names(stderr) -> list[str]`. Per line of `stderr.splitlines()`: skip unless the line starts with `import time:`; `split("|")`; skip if fewer than 3 fields; take `parts[-1]`, `.strip()`, then `.lstrip("│├└─ \t")`; skip if the result is empty or contains a space. Collect the rest.
- Replace the body of `_heavy_import_offenders` with identity matching over those names: flag heavy module `h` when some parsed `n` satisfies `n == h or n.startswith(h + ".")`. Return `sorted(set(...))`.
- Write the two docstrings required by the Documentation section: the row-format contract on `_importtime_module_names`, and the identity-not-substring rationale naming `tools.redis_flush_guard` / `_redis_flush_guard_boot` and citing #2692 on `_heavy_import_offenders`.
- Rewire `test_counter_only_call_does_not_import_popoto`: delete the inline `heavy = (...)` tuple and the `offenders = [m for m in heavy if m in proc.stderr]` comprehension at lines 77-79; call `offenders = _heavy_import_offenders(proc.stderr)`. Keep the subprocess spawn, the event payload, the `returncode == 0` assertion, and the failure message text unchanged.
- Run `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q` — expect **6 passed**.
- Run `python -m ruff check tests/unit/test_post_tool_use_fast_path.py` and `python -m ruff format tests/unit/test_post_tool_use_fast_path.py`.
- Commit.

### 3. Validation

- **Task ID**: validate-detection
- **Depends On**: build-identity-parser
- **Assigned To**: `importtime-detection-validator`
- **Agent Type**: validator
- **Parallel**: false

- Confirm the red-first FAIL output from Task 1 is present in the commit history and the PR body, and that it names `['redis'] != []`.
- Independently re-derive the red: `git stash` the parser body, or check out the Task 1 commit, and confirm `test_heavy_detection_ignores_lookalike_module_names` fails there and passes at HEAD.
- Confirm the detector is not disarmed: `test_heavy_detection_still_catches_real_imports` passes at **both** the Task 1 commit and HEAD.
- Confirm `git diff --name-only main...HEAD` lists exactly `tests/unit/test_post_tool_use_fast_path.py` and nothing else — in particular no `.claude/hooks/post_tool_use.py`, no `scripts/update/`, no `tools/`.
- Confirm no `-S`, no `-c pass` baseline leg, and no `site` filtering crept in (deferred to #2751).
- Run every row of the Verification table and report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Fast-path suite green | `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q` | exit code 0 |
| Six cases present | `./scripts/pytest-clean.sh tests/unit/test_post_tool_use_fast_path.py -q 2>&1 \| grep -c "6 passed"` | output contains 1 |
| Red-first case exists | `grep -c "def test_heavy_detection_ignores_lookalike_module_names" tests/unit/test_post_tool_use_fast_path.py` | output contains 1 |
| Over-loosening guard exists | `grep -c "def test_heavy_detection_still_catches_real_imports" tests/unit/test_post_tool_use_fast_path.py` | output contains 1 |
| Identity matching present | `grep -c 'startswith(h + "\.")\|startswith(heavy + "\.")' tests/unit/test_post_tool_use_fast_path.py` | output contains 1 |
| Substring scan gone (anti-criterion) | `grep -c "m in proc.stderr" tests/unit/test_post_tool_use_fast_path.py` | match count == 0 |
| Hook untouched (anti-criterion) | `git diff --name-only main...HEAD -- .claude/hooks/post_tool_use.py \| wc -l` | match count == 0 |
| Diff is one file (anti-criterion) | `git diff --name-only main...HEAD \| grep -cv '^tests/unit/test_post_tool_use_fast_path.py$'` | match count == 0 |
| No `-S`/baseline-diff creep, #2751 stays deferred (anti-criterion) | `grep -c '"-S"\|-c pass\|isolated_baseline' tests/unit/test_post_tool_use_fast_path.py` | match count == 0 |
| No `.pth` installer edits, #2645 stays deferred (anti-criterion) | `git diff --name-only main...HEAD \| grep -c 'redis_flush_guard'` | match count == 0 |
| Lint clean | `python -m ruff check tests/unit/test_post_tool_use_fast_path.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_post_tool_use_fast_path.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. Scope was fully specified by the human at dispatch (minimum fix only; suggested steps 2 and 3 explicitly excluded and now filed as #2751 and tracked under #2645 respectively), every technical assumption was resolved by direct verification against `main` at `0dd8e70f2` rather than left open, and the red-first caveat was answered with a concrete synthesis strategy in Task 1.
