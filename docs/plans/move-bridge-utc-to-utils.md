---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-08-18
tracking: https://github.com/tomcounsell/ai/issues/2867
last_comment_id: none
---

# Move the UTC time helper out of `bridge/` into `utils/`

## Problem

`bridge/utc.py` holds four pure functions — `utc_now()`, `to_local()`, `utc_iso()`,
`to_unix_ts()` — over a single stdlib import (`datetime`). Nothing in it knows
anything about Telegram, Telethon, or the I/O layer it lives in. It sits in
`bridge/` for historical reasons.

**Current behavior:**

Every consumer that needs the current time has to reach into the harness's
Telegram I/O package to get it. That includes `tools/` packages that are meant
to stand alone. `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler`
each have exactly **one** cross-package import in their entire source tree, tests
included, and it is `from bridge.utc import ...`. Three otherwise self-contained
CLI utilities are chained to the Telegram package by a call to `datetime.now()`.

**Desired outcome:**

The time helper lives in a package whose name matches what it is, `bridge/utc.py`
stops existing, and all 57 referencing files point at the new path in the same
change. `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` end with
zero harness imports, and a test in `tests/unit/test_architectural_constraints.py`
keeps them that way.

## Freshness Check

**Baseline commit:** `a2d13de73` (`git rev-parse HEAD` == `origin/main`, working tree clean)
**Issue filed at:** 2026-08-18T13:10:14Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `bridge/utc.py` — claimed pure, stdlib-only, four exports. Still holds. Sole import is `from datetime import UTC, datetime`.
- `tools/image_gen/__init__.py:21` — `from bridge.utc import utc_now`. Still holds, exact line.
- `tools/selfie/__init__.py:15` — `from bridge.utc import utc_now`. Still holds, exact line.
- `tools/sms_reader/__init__.py:34` — `from bridge.utc import utc_now`. Still holds, exact line.
- `tools/test_scheduler/__init__.py:15` — `from bridge.utc import utc_iso`. Still holds, exact line.
- `tools/telegram_history/__init__.py:333` — `from bridge.utc import to_unix_ts`, function-local. Still holds (the issue cited "330/333"; the import statement itself is at 333).
- `tests/unit/test_public_api_contract.py:57` — `("bridge.utc", "utc_now"): "() -> datetime.datetime"`. Still holds, exact line.
- `tests/unit/test_utc.py` — exists, imports all four symbols from `bridge.utc`, docstring names the old path.

**Importer count re-derived, not inherited:** `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py'` returns **57** files at `a2d13de73`. Breakdown: tests 15, `reflections/` 13, `tools/` 9, `agent/` 6, `bridge/` 5, `monitoring/` 4, `scripts/` 2, `models/` 2, `ui/` 1. The issue's 57 is correct; its note about an independent pass finding 56 is resolved — the earlier probe used `\s` in a POSIX-ERE `git grep -E`, where `\s` is not a character class, so anchored patterns silently under-matched. This plan's scans use `-P`.

**Cited sibling issues/PRs re-checked:**

- #2859, #2856, #2797, #2746 — all four still open, all four still touch none of the 57 files. The zero-overlap claim holds at plan time.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since="2026-08-18T13:10:14Z"` is empty; `HEAD` is the same `a2d13de73` the issue recorded.

**Active plans in `docs/plans/` overlapping this area:** none. Every `docs/plans/*.md` hit for `bridge.utc` is under `docs/plans/completed/`.

**Notes:** The recon that grounds this plan was added to issue #2867 as a `## Recon Summary` section during this stage, after re-measuring each figure independently.

## Prior Art

- **#542 / `docs/plans/completed/542-utc-timestamp-normalization.md`** — established the tz-aware-UTC convention and created `bridge/utc.py` in the first place. It settled the *semantics* of these four functions and never revisited the module's location. Nothing there argues for `bridge/` as the home; the placement is incidental to that plan's scope.
- **#777 / hotfix `9e3a64f5`** — added `to_unix_ts` as the single source of truth for naive-datetime coercion, and deliberately left three older inline copies (`monitoring/session_watchdog._to_timestamp`, `agent/session_health._to_ts`, `ui/data/sdlc._safe_float`) untouched. That decision stands and this plan does not disturb it.
- **PR #2610** (merged 2026-08-07) — datetime age coercion fixes in the same helper. Changed behavior inside `to_unix_ts`, not its location.
- No closed issue or merged PR has previously proposed relocating this module, and no `kernel/` package has ever existed in this repo. There is no failed prior attempt to learn from, so this plan has no **Why Previous Fixes Failed** section.

## Research

Skipped by the Phase 0.7 rule for purely internal work: this change introduces no
library, no API, no service, and no ecosystem pattern. It relocates one
stdlib-only module and rewrites import statements. The one mechanical question it
raises — whether import ordering stays correct after the rewrite — is answered
inside the repo: `[tool.ruff.lint] select` includes `I`, so `ruff check --fix`
re-sorts the affected import blocks, and `utils` sorts after `tools` where
`bridge` sorted first.

No relevant external findings — proceeding with codebase context.

## Spike Results

The two assumptions that could sink this plan were resolved by direct measurement
during Phase 1 rather than by dispatched agents, because both were single-command
checks.

### spike-1: Does the destination package drag harness dependencies into the freed tools?

- **Assumption**: "A destination package's `__init__.py` runs on every submodule import, so the choice of package determines what `tools/selfie` actually loads."
- **Method**: prototype — subprocess import probe counting `sys.modules` growth and wall time for each candidate.
- **Finding**:

  | Import | New modules | Time |
  |---|---|---|
  | `bridge.utc` (today) | 4 | 1 ms |
  | `utils.api_keys` | 2 | 1 ms |
  | `config.paths` | 214 | 94 ms |
  | `config.models` | 215 | 70 ms |

  `bridge/__init__.py` is a one-line comment and `utils/__init__.py` is empty, so
  both are free. `config/__init__.py` eagerly re-exports `loader`, `paths`, and
  `settings`, pulling pydantic, pydantic-settings, dotenv, asyncio, ssl, socket,
  and subprocess.
- **Confidence**: high — measured, reproducible.
- **Impact on plan**: eliminates `config/` outright. It would have taken
  `tools/selfie` from 2 loaded modules to 214 and made a selfie CLI depend on
  settings and env loading. That inverts the goal of the issue.

### spike-2: Is `utils/` itself free of harness dependencies?

- **Assumption**: "`utils/` is a grab-bag and may itself carry harness deps, which would silently defeat the move."
- **Method**: code-read — AST-visible import scan of `utils/__init__.py` and every `utils/*.py`.
- **Finding**: `utils/__init__.py` is **0 bytes**, so importing `utils.utc` executes nothing. No module in `utils/` imports `bridge`, `agent`, `worker`, `models`, `monitoring`, `reflections`, `analytics`, or `ui`. The single cross-package import anywhere in the package is `utils/keyword_extraction.py` → `config.memory_defaults`, which is a sibling module and is never on the import path of `utils.utc`.
- **Confidence**: high.
- **Impact on plan**: `utils/` clears the detachment bar. It also motivates a guard test — the property is real today but nothing currently protects it, and one harness import added to `utils/__init__.py` later would silently re-couple all three freed packages.

### spike-3: Are there references a mechanical import rewrite would miss?

- **Assumption**: "All 57 references are import statements."
- **Method**: code-read — scan for string-literal module paths and dynamic import forms.
- **Finding**: three string-literal sites, none of which any import-rewriting tool would catch:
  - `tests/integration/test_reflections_redis.py:107` and `:129` — `__import__("bridge.utc", fromlist=["utc_now"])`
  - `tests/unit/test_session_stall_classifier.py:300` — `patch("bridge.utc.to_unix_ts", return_value=None)`

  The `patch` site is load-bearing. `agent/session_stall_classifier.py:233` does a
  function-local `from bridge.utc import to_unix_ts`, so the patch resolves
  against the live module attribute and genuinely takes effect. A stale target
  string would not raise — `unittest.mock.patch` would fail loudly on a
  nonexistent module, but the danger is the opposite direction: pointing it at a
  module that still exists while the code under test imports a different one
  turns a real assertion into a no-op that still reports green. Because the hard
  move deletes `bridge/utc.py` entirely, this particular site fails loudly rather
  than silently, which is the argument for the hard move over a shim.
- **Confidence**: high.
- **Impact on plan**: these three sites are named explicitly as tasks rather than
  left to a bulk rewrite.

## Data Flow

Not applicable in the runtime sense — this change moves no data and alters no
runtime behavior. The flow that matters is the *import* graph:

1. **Entry point**: a `tools/selfie` CLI invocation.
2. **Today**: `tools/selfie/__init__.py` → `bridge.utc` → executes `bridge/__init__.py` → `datetime`. The standalone tool has a live edge into the Telegram I/O package.
3. **After**: `tools/selfie/__init__.py` → `utils.utc` → executes an empty `utils/__init__.py` → `datetime`. No edge into `bridge/` remains anywhere in the package.
4. **Output**: identical. Every function body, signature, and return value is byte-for-byte unchanged; only the module's path changes.

## Architectural Impact

- **New dependencies**: none. No package, service, or import is added. `utils` is already a first-party package and already appears in `[tool.hatch.build.targets.wheel] packages`.
- **Interface changes**: the module path only. `utc_now`, `to_local`, `utc_iso`, and `to_unix_ts` keep identical signatures and bodies. The `tests/unit/test_public_api_contract.py` snapshot entry changes its module key, not its signature string.
- **Coupling**: strictly decreased. Three `tools/` packages drop from one harness import to zero; `tools/image_gen` drops from two cross-package imports to one. Nothing gains an import.
- **Data ownership**: unchanged.
- **Reversibility**: high. The inverse change is the same mechanical rewrite in the opposite direction, and it is fully expressed in one commit.

### The destination decision

The issue left the destination open and named three candidates. The choice is `utils/`.

| Candidate | Import cost to a freed tool | Packaging change | Verdict |
|---|---|---|---|
| `utils/utc.py` | 2 modules / 1 ms | none — `utils` already ships in the wheel | **Chosen** |
| `kernel/utc.py` | 2 modules / 1 ms | must add `kernel` to `[tool.hatch.build.targets.wheel] packages` | Rejected |
| `config/utc.py` | 214 modules / 94 ms | none | Rejected |

**`config/` is disqualified on measured evidence.** The issue's argument for it
was that `tools/image_gen` already imports `config.models`, so consolidating
would reduce that one package's cross-package *count* from two to one. That
optimizes the wrong quantity. `config/__init__.py` executes on any
`config.<submodule>` import and pulls 214 modules including pydantic-settings and
dotenv. `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` import
nothing from `config` today; routing them through it would replace a 4-module
dependency on `bridge` with a 214-module dependency on the settings layer. The
issue asks to detach these tools, and this option attaches them harder.

**`kernel/` is rejected on cost, not on taste.** It matches `utils/` exactly on
the only functional axis that matters (an empty `__init__.py`, 2 modules, 1 ms),
so it buys nothing measurable. What it costs is concrete:

- `utils` is one of six packages listed in `[tool.hatch.build.targets.wheel]` (`bridge`, `tools`, `scripts`, `agent`, `utils`, `ui`). `kernel` would not be, and a new top-level package that ships `tools/selfie` while omitting the module `tools/selfie` imports produces a wheel that fails at import time. That registration is one line and easy to omit, and the failure surfaces only in an installed environment, not in the source checkout where every test runs.
- It creates an eighth top-level package to hold one 60-line module, against the repo's preference for minimal surface. A package named `kernel` also invites the immediate question of which of `utils/`'s six existing modules belong in it — a much larger reorganization the issue does not sanction and this plan will not start.
- The name has no precedent anywhere in this repo.

The honest cost of `utils/` is that the name is vague and the package is a
grab-bag. That is an aesthetic objection, and `utc.py` sits comfortably beside
`api_keys.py`, `github_patterns.py`, and `json_cache.py` — small stdlib-shaped
helpers with no harness ties. The one real risk `utils/` carries is that its
dependency-free property is currently incidental rather than enforced: nothing
stops someone adding `from models import ...` to `utils/__init__.py` and
silently re-coupling all three freed packages. That risk is answered by a guard
test in this plan rather than by a new package name, which would not have
prevented it either.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the one open design decision is settled above, on measured evidence)
- Review rounds: 1

The work is mechanically large (57 files) and conceptually tiny (one path
rewrite plus one guard test). The cost is in verification breadth, not in
design.

## Prerequisites

No prerequisites — this work has no external dependencies, no secrets, and no
services. It runs entirely against the source checkout.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Venv on the pinned interpreter | `python -c "import sys,pathlib;pin=pathlib.Path('.python-version').read_text().strip();v='.'.join(map(str,sys.version_info[:2]));assert v==pin,f'venv {v} != pinned {pin}'"` | `scripts/pytest-clean.sh` aborts on an off-pin venv; catching that up front avoids misreading an environment failure as a regression from the rewrite |
| Destination package is still dependency-free | `python -c "import pathlib;assert pathlib.Path('utils/__init__.py').read_text().strip()==''"` | The entire case for `utils/` rests on an empty `__init__.py`. If someone added an import to it between plan and build, the detachment payoff is gone and the destination decision needs revisiting before any file moves |

## Solution

### Key Elements

- **`utils/utc.py`**: the relocated module, byte-identical in body to today's `bridge/utc.py` apart from its docstring, which stops implying a bridge-local scope.
- **`bridge/utc.py`**: deleted. No shim, no re-export, no alias, no deprecation window.
- **57 rewritten references**: 54 import statements plus 3 string-literal module paths, all in one change.
- **A boundary guard in `tests/unit/test_architectural_constraints.py`**: asserts the three freed `tools/` packages carry no harness imports, and that `utils/__init__.py` imports nothing. Without it the detachment is a fact of today with nothing defending it tomorrow.
- **Docs and the generated graph**: `docs/features/utc-timestamps.md`, `docs/features/session-lifecycle.md`, and the path strings in `site/assets/graph.js`.

### Flow

Not a user-facing feature. The developer-facing flow:

Developer needs the current time → `from utils.utc import utc_now` → gets a tz-aware UTC datetime, having loaded two modules and touched no harness package.

### Technical Approach

- **`git mv bridge/utc.py utils/utc.py`**, so the move is recorded as a rename and review reads as a rename rather than a delete-plus-add.
- **Rewrite in two passes.** A bulk pass over the 54 import-statement sites (`from bridge.utc import` → `from utils.utc import`), then three hand edits for the string-literal sites named in spike-3. Neither pass is trusted on its own; the completeness check is a repo-wide grep for `bridge[./]utc` over `*.py` returning zero.
- **Let ruff re-sort.** `python -m ruff check --fix` handles the isort reordering that follows from `utils` sorting after `tools` where `bridge` sorted first. Then `python -m ruff format`. No other linting.
- **Keep `tests/unit/test_utc.py` where it is.** The suite is flat under `tests/unit/` and does not mirror source paths, so the file name is already correct. Only its import line and its docstring change. Moving it would add churn for no signal.
- **Update the public API contract key, not its value.** `("bridge.utc", "utc_now")` becomes `("utils.utc", "utc_now")`; the signature string `"() -> datetime.datetime"` is unchanged because the function is unchanged.
- **Treat `site/assets/graph.js` as a path-keyed artifact, not as prose.** Its node ids are derived from file paths (`"id": "file:bridge/utc.py"`, `"filePath": "bridge/utc.py"`, and edge endpoints). A literal `bridge/utc.py` → `utils/utc.py` replacement in that file is exactly what a regeneration would produce for these nodes, and it keeps the committed graph honest without running the whole `/understand` pipeline. Commit `961d20eee` treated staleness in this file as a real defect (#2531), so leaving it stale is not the neutral option.
- **Commit in checkpoints**, not one lump: (1) the `git mv` plus the module docstring, (2) the bulk import rewrite plus ruff, (3) the three string-literal sites, (4) the guard test, (5) docs and graph. Each is independently reviewable and each keeps the tree importable.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] No exception handlers are added, removed, or modified by this change. The only `try/except` in the moved module is `to_unix_ts`'s `except (ValueError, TypeError): return None` on ISO-string parsing, which moves verbatim and is already covered by `tests/unit/test_utc.py`. Confirm that coverage survives the move rather than assuming it.

### Empty/Invalid Input Handling

- [ ] `to_unix_ts(None)`, `to_unix_ts("not-a-date")`, and `to_local(<naive datetime>)` are the three degenerate inputs in the module. All three have existing assertions in `tests/unit/test_utc.py`; verify each still runs against the new import path rather than silently collecting zero tests.
- [ ] Guard against the pytest failure mode where a renamed module makes a whole file error at collection and the run reports "0 failed" — assert the collected test count for `tests/unit/test_utc.py` is unchanged from the pre-move baseline.

### Error State Rendering

- [ ] No user-visible output changes. The one behavior a reviewer could mistake for a rendering path is `to_local()`, used at the Telegram presentation boundary; its output is unchanged because its body is unchanged.

## Test Impact

- [ ] `tests/unit/test_utc.py` — UPDATE: change `from bridge.utc import ...` to `from utils.utc import ...` and the module docstring "Tests for bridge.utc utility module." to name the new path. File stays at its current location.
- [ ] `tests/unit/test_public_api_contract.py::test_public_api_signatures_are_stable` — UPDATE: change the dict key `("bridge.utc", "utc_now")` to `("utils.utc", "utc_now")`. The signature value is unchanged. This test is designed to fail first and loudest on exactly this kind of rename, so it is the canary, not a nuisance.
- [ ] `tests/unit/test_session_stall_classifier.py::test_unparseable_timestamp_returns_healthy_not_stalled` — UPDATE: change the `patch("bridge.utc.to_unix_ts", ...)` target to `patch("utils.utc.to_unix_ts", ...)` and correct the three-line explanatory comment above it that names `bridge.utc`. Verify the patch still bites by confirming the assertion fails when `return_value` is changed.
- [ ] `tests/integration/test_reflections_redis.py` lines 107 and 129 — UPDATE: change `__import__("bridge.utc", fromlist=["utc_now"])` to `__import__("utils.utc", fromlist=["utc_now"])` at both sites.
- [ ] `tests/integration/test_updated_at_heal.py`, `tests/performance/test_benchmarks.py`, `tests/unit/reflections/test_sdlc_upvote_lanes.py`, `tests/unit/session_runner/test_liveness.py`, `tests/unit/test_hooks_audit.py`, `tests/unit/test_job_model.py`, `tests/unit/test_messenger.py`, `tests/unit/test_migrations.py`, `tests/unit/test_reconciler.py`, `tests/unit/test_session_archive.py`, `tests/unit/test_valor_telegram.py` — UPDATE: plain import-path rewrite, no assertion changes.
- [ ] `tests/unit/test_architectural_constraints.py` — UPDATE: add a new `TestStandaloneToolPackageBoundaries` class. The file already has the AST helper this needs (`_get_imports`, which uses `ast.walk` and therefore catches function-local imports as well as top-level ones), so the guard extends an existing pattern instead of introducing one.

## Rabbit Holes

- **Reorganizing `utils/` while you are in there.** The moment `utc.py` lands beside `api_keys.py` and `json_cache.py`, the question "should these be a kernel package?" becomes tempting. It is a different change with a different blast radius and it is not what issue #2867 asks for. Move the one module and stop.
- **Broadening the boundary guard to ban `config` imports.** The guard's forbidden set is exactly the issue's acceptance criterion — `bridge`, `agent`, `worker`, `models`, `monitoring`, `reflections` (plus `analytics` and `ui` for symmetry). Adding `config` would make the test stricter than anything agreed and would forbid `tools/image_gen`'s legitimate `config.models` dependency if the guard's package list ever widens.
- **Collapsing the three inline `to_unix_ts` duplicates.** `monitoring/session_watchdog._to_timestamp`, `agent/session_health._to_ts`, and `ui/data/sdlc._safe_float` each reimplement the same naive-datetime guard. `docs/features/utc-timestamps.md` records that #777 left them untouched on purpose. Touching them here would triple the review surface of a rename.
- **Regenerating the whole `site/assets/graph.js` knowledge graph.** The targeted path-string replacement is correct and cheap. Running the full `/understand` pipeline would rewrite thousands of unrelated lines and bury the actual change.
- **Hand-verifying 57 files.** The completeness proof is a repo-wide grep returning zero, plus the test suite. Reading each file individually is slower and less reliable.

## Risks

### Risk 1: A string-literal reference is missed and a test silently stops asserting

**Impact:** `tests/unit/test_session_stall_classifier.py`'s patch target is the sharp case. In general, a `patch()` aimed at the wrong module either raises or quietly patches something the code under test never reads, and the second failure mode reports green.

**Mitigation:** The hard move deletes `bridge/utc.py`, so a missed `patch("bridge.utc...")` raises `ModuleNotFoundError` rather than passing. This is the strongest single argument for the no-shim constraint and it should be stated in the PR body. Backed by a repo-wide `grep` for `bridge[./]utc` over `*.py` expected to return zero matches, which catches string literals that no import-aware tool sees. Additionally, mutate the patch's `return_value` and confirm the test flips to failing, proving the patch still reaches the code.

### Risk 2: The detachment silently regresses after this ships

**Impact:** `utils/__init__.py` is empty today by accident, not by contract. One `from models import ...` added there later re-couples `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` to the harness with no test failing. The issue's whole payoff evaporates and nobody notices.

**Mitigation:** The boundary guard test asserts both halves — the three tool packages import no harness module, and `utils/__init__.py` contains zero import statements. Mutation-check both: add a harness import to a scratch copy of each guarded file and confirm the test goes red before trusting it.

### Risk 3: An importer lands on main during the build and is missed

**Impact:** A file added by a concurrently-merging PR imports `bridge.utc`, the branch merges, and main breaks at import time.

**Mitigation:** Zero overlap between the 57 files and all four open PRs is confirmed at plan time, and no commits have landed since the issue was filed. Re-run the `git grep` count immediately before opening the PR and again after any rebase; the number must be zero on the branch. The `ModuleNotFoundError` from the deleted module means this fails loudly at collection rather than degrading quietly.

### Risk 4: The rename reads as a delete-plus-add and review misses that the body is unchanged

**Impact:** A reviewer spends the review budget re-reading four functions that did not change, and the actual risk surface — the 57 call sites and the guard test — gets less attention.

**Mitigation:** Use `git mv` so git records a rename, and keep the body edit to the docstring only in that first commit. State in the PR body that `git diff --find-renames` shows the module body unchanged, and give the reviewer the command.

## Race Conditions

No race conditions identified. This change is a compile-time relocation: it adds
no concurrency, no shared mutable state, no async operation, and no cross-process
data flow. The module's four functions are pure and stateless before and after
the move. The only ordering that matters is internal to the build — the tree must
not be left with `bridge/utc.py` deleted and importers unrewritten across a
commit boundary — which the checkpoint sequence in Technical Approach handles by
keeping the rename and the bulk rewrite adjacent and verifying importability at
each checkpoint.

## No-Gos (Out of Scope)

Nothing deferred — every item in the issue's acceptance criteria is in scope for
this plan, including the guard test, the string-literal sites, the docs, and the
generated graph artifact.

Three things are **non-goals** of this change rather than postponed work. They
were never part of issue #2867 and this plan makes no promise about them:

- Detaching `tools/telegram_history` from the harness. It carries 17 `models.*`
  imports and moving one time helper does not touch that. It is named in the
  issue explicitly as *not* freed by this change.
- Detaching `tools/image_gen` from `config.models`. The issue's stated payoff for
  that package is "leaves only `config.models`", which this plan achieves exactly.
- Reorganizing `utils/` or introducing a `kernel/` package. The Architectural
  Impact section argues against it on measured grounds; that is a decision made,
  not a decision postponed.

## Update System

No update system changes required. This change adds no dependency, no config
file, no secret, no launchd service, and no migration. `utils` is already listed
in `[tool.hatch.build.targets.wheel] packages`, so no packaging metadata moves
either — which is one of the reasons `utils/` was chosen over a new top-level
package.

There is no Popoto model change, so `scripts/update/migrations.py` is untouched.
Note that `scripts/update/git.py` and `scripts/update/run.py` are both in the
57-file importer list and get the plain import rewrite; neither changes behavior.

## Agent Integration

No agent integration required. This is an internal module relocation with no new
capability to expose. No `[project.scripts]` entry point changes, no MCP server
changes, no `.mcp.json` changes, and the bridge imports nothing new.

The bridge does import the moved module — `bridge/dedup.py`,
`bridge/escape_hatch.py`, `bridge/session_transcript.py`,
`bridge/telegram_bridge.py`, and `bridge/telegram_relay.py` are five of the 57
and get the plain import rewrite. Because bridge and worker code changes, the
repo's restart rule applies after merge: `./scripts/valor-service.sh restart`,
verified by `tail -5 logs/bridge.log` showing "Connected to Telegram".

## Documentation

### Feature Documentation

- [ ] Update `docs/features/utc-timestamps.md` — retitle the `## The bridge/utc Module` heading, update the three code samples at lines 16, 60, and 85 to `from utils.utc import ...`, and update the `bridge.utc.to_unix_ts(val)` reference in the read-path guidance. Add a sentence recording that the module lives in `utils/` because it is dependency-free and consumed by standalone tools, so the next reader does not have to re-derive the reasoning.
- [ ] Update `docs/features/session-lifecycle.md:398` — `bridge.utc.utc_now()` becomes `utils.utc.utc_now()`.
- [ ] No new entry in `docs/features/README.md`. The existing UTC Timestamps row (line 258) describes behavior and names no path, so it stays correct as written.
- [ ] Update `site/assets/graph.js` — replace the `bridge/utc.py` path strings in the node ids, `filePath` fields, and edge endpoints with `utils/utc.py`.

### External Documentation Site

- [ ] `site/` is the documentation site and its only affected content is the generated graph covered above. No page copy references the module path.

### Inline Documentation

- [ ] Update the moved module's docstring so it no longer reads as bridge-local, and state that it is dependency-free by contract because standalone `tools/` packages import it.
- [ ] Update the `agent/session_stall_classifier.py:11` docstring line "Uses bridge.utc.to_unix_ts for all datetime → float conversions."
- [ ] Update the explanatory comment above `tests/unit/test_session_stall_classifier.py:300`, which names `bridge.utc` twice while explaining why the patch target is what it is.
- [ ] Leave `docs/plans/completed/*.md` untouched. Those are historical records of shipped work and describe the code as it was when they shipped.

## Success Criteria

- [ ] `utils/utc.py` exists and exports `utc_now`, `to_local`, `utc_iso`, `to_unix_ts` with unchanged signatures and bodies.
- [ ] `bridge/utc.py` does not exist. No shim, no re-export, no alias anywhere in the repo.
- [ ] A repo-wide grep for `bridge[./]utc` over `*.py` returns zero matches.
- [ ] `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` have zero imports from `bridge`, `agent`, `worker`, `models`, `monitoring`, or `reflections` — including inside their `tests/` directories and including function-local imports.
- [ ] `tests/unit/test_public_api_contract.py` passes with the `("utils.utc", "utc_now")` key.
- [ ] `tests/unit/test_utc.py` passes with the same collected test count as before the move.
- [ ] The new boundary guard test fails when a harness import is injected into any guarded file (mutation-verified per guard, not once for the class).
- [ ] Tests pass (`/do-test`), full `tests/unit` run.
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.

## Team Orchestration

The lead agent orchestrates and does not build directly.

### Team Members

- **Builder (move)**
  - Name: `utc-mover`
  - Role: performs the rename, the bulk import rewrite, and the three string-literal edits
  - Agent Type: builder
  - Resume: true

- **Builder (guard)**
  - Name: `boundary-guard-builder`
  - Role: writes and mutation-verifies the architectural boundary test
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `utc-documentarian`
  - Role: docs, docstrings, comments, and the generated graph artifact
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `utc-validator`
  - Role: runs the Verification table, confirms zero residual references, confirms the mutation checks were actually performed
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Relocate the module

- **Task ID**: build-move-module
- **Depends On**: none
- **Validates**: tests/unit/test_utc.py
- **Informed By**: spike-1 (confirmed: `utils/__init__.py` is empty, 2 modules / 1 ms), spike-2 (confirmed: no `utils/*.py` imports a harness package)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- Record the pre-move baseline: `git grep -clE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` (expect 57) and the collected test count of `tests/unit/test_utc.py`.
- `git mv bridge/utc.py utils/utc.py`.
- Edit only the module docstring: drop the bridge-local framing, state that the module is dependency-free by contract because standalone `tools/` packages import it.
- Commit checkpoint: "Move the UTC helper from bridge/ to utils/ (#2867)".

### 2. Rewrite the 54 import statements

- **Task ID**: build-rewrite-imports
- **Depends On**: build-move-module
- **Validates**: tests/unit/test_utc.py, tests/unit/test_public_api_contract.py
- **Informed By**: spike-3 (confirmed: exactly 3 references are string literals, not import statements)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `from bridge.utc import` → `from utils.utc import` across all remaining `*.py` files. Include the function-local imports in `tools/telegram_history/__init__.py:333` and `agent/session_stall_classifier.py:233`, which a top-level-only pass would miss.
- Update the `tests/unit/test_public_api_contract.py` dict key to `("utils.utc", "utc_now")`, leaving the signature string untouched.
- Update the `tests/unit/test_utc.py` import line and docstring.
- Run `python -m ruff check --fix` to re-sort the import blocks (`utils` sorts after `tools` where `bridge` sorted first), then `python -m ruff format`. No other linting.
- Confirm the tree still imports: `python -c "import utils.utc, bridge.telegram_bridge"`.
- Commit checkpoint.

### 3. Fix the three string-literal references

- **Task ID**: build-string-sites
- **Depends On**: build-rewrite-imports
- **Validates**: tests/unit/test_session_stall_classifier.py, tests/integration/test_reflections_redis.py
- **Informed By**: spike-3
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- `tests/integration/test_reflections_redis.py:107` and `:129` — `__import__("bridge.utc", ...)` → `__import__("utils.utc", ...)`.
- `tests/unit/test_session_stall_classifier.py:300` — `patch("bridge.utc.to_unix_ts", ...)` → `patch("utils.utc.to_unix_ts", ...)`, and correct the explanatory comment above it that names `bridge.utc` twice.
- **Prove the patch still bites**: temporarily change its `return_value` from `None` to a float and confirm `test_unparseable_timestamp_returns_healthy_not_stalled` fails, then revert. A patch aimed at a module the code under test never reads passes green, and only this check distinguishes the two.
- Verify completeness: `grep -rn "bridge[./]utc" --include=*.py .` returns nothing.
- Commit checkpoint.

### 4. Add the boundary guard test

- **Task ID**: build-boundary-guard
- **Depends On**: build-string-sites
- **Validates**: tests/unit/test_architectural_constraints.py
- **Informed By**: spike-2 (confirmed: the property holds today but nothing enforces it)
- **Assigned To**: boundary-guard-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestStandaloneToolPackageBoundaries` to `tests/unit/test_architectural_constraints.py`, reusing the file's existing `_get_imports` AST helper — it uses `ast.walk`, so it catches function-local imports, which a top-level-only check would miss on exactly the files that matter.
- `test_standalone_tool_packages_have_no_harness_imports`: walk every `*.py` under `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` including their `tests/` directories; assert no import's first path segment is in `{bridge, agent, worker, models, monitoring, reflections, analytics, ui}`. Failure message must name the offending file, line, and module. Do not add `config` to the forbidden set (see Rabbit Holes).
- `test_utils_package_init_imports_nothing`: assert `utils/__init__.py` parses to zero `Import`/`ImportFrom` nodes, with a failure message explaining that a harness import here silently re-couples all three packages above.
- **Mutation-check each assertion separately**: inject a harness import into a scratch copy of `tools/selfie/__init__.py`, then of a file under `tools/sms_reader/tests/`, then into `utils/__init__.py`, and confirm the corresponding test goes red each time. A test that reaches no code passes for the wrong reason; per-guard measurement is the only thing that rules it out.
- Commit checkpoint.

### 5. Documentation and generated graph

- **Task ID**: document-feature
- **Depends On**: build-boundary-guard
- **Assigned To**: utc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/utc-timestamps.md` (heading, three code samples, the `to_unix_ts` read-path guidance) and add the one-sentence rationale for the `utils/` location.
- Update `docs/features/session-lifecycle.md:398`.
- Update the `agent/session_stall_classifier.py:11` docstring line.
- Replace `bridge/utc.py` path strings in `site/assets/graph.js` node ids, `filePath` fields, and edge endpoints.
- Leave `docs/plans/completed/*.md` untouched — they record shipped history.
- Commit checkpoint.

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: build-move-module, build-rewrite-imports, build-string-sites, build-boundary-guard, document-feature
- **Assigned To**: utc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Re-run the residual-reference grep after any rebase, since a concurrently-merged importer would only surface then.
- Confirm the mutation checks in tasks 3 and 4 were actually performed and reported, not asserted.
- Report pass/fail per criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Module exists at new path | `test -f utils/utc.py` | exit code 0 |
| Old module is gone (anti-criterion: no shim) | `test -e bridge/utc.py` | exit code != 0 |
| No residual `bridge.utc` references in Python | `grep -rc "bridge[./]utc" --include='*.py' agent bridge models monitoring reflections scripts tools ui utils tests` | match count == 0 |
| Standalone tools carry no harness imports | `grep -rc -E "(from\|import)[[:space:]]+(bridge\|agent\|worker\|models\|monitoring\|reflections)[. ]" --include='*.py' tools/selfie tools/sms_reader tools/test_scheduler` | match count == 0 |
| Moved module imports no first-party package | `grep -c -E "^(from\|import) (bridge\|agent\|worker\|models\|config\|monitoring\|reflections\|tools\|ui\|utils)" utils/utc.py` | match count == 0 |
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_utc.py tests/unit/test_public_api_contract.py tests/unit/test_architectural_constraints.py tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| Full unit suite passes | `scripts/pytest-clean.sh tests/unit -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Docs no longer cite the old path | `grep -c "bridge[./]utc" docs/features/utc-timestamps.md docs/features/session-lifecycle.md` | match count == 0 |
| Generated graph no longer cites the old path | `grep -c "bridge/utc.py" site/assets/graph.js` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The single design decision the issue left open — the destination package —
is settled in Architectural Impact on measured evidence: `config/` costs 214
modules against `utils/`'s 2, and `kernel/` matches `utils/` on every functional
axis while adding a wheel-packaging step and a new top-level package for one
file. If a reviewer disagrees with choosing `utils/` over a new `kernel/`, that
is the one thing worth arguing before the build starts, and the table in that
section is the argument to attack.
