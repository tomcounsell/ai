---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-08-18
tracking: https://github.com/tomcounsell/ai/issues/2867
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-18T13:48:53Z
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
stops existing, and all **57 referencing files** point at the new path in the same
change. `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` end with
zero harness imports, and a test in `tests/unit/test_architectural_constraints.py`
keeps them that way.

## Freshness Check

**Baseline commit:** `79b539535` (`git rev-parse HEAD` == `origin/main`, working tree clean)
**Original baseline:** `a2d13de73`. The only commits between the two are this plan
document's own three revisions (`git diff --name-only a2d13de73..79b539535` returns
`docs/plans/move-bridge-utc-to-utils.md` and nothing else), so every code
measurement below holds at both commits.
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

### Reference census — re-derived at `79b539535`, with units attached

The first draft of this plan collapsed three different quantities into one number
and called them all "57". They are not the same quantity and a builder measuring
against the wrong one will stop short of the completeness gate. The census below
is the authoritative decomposition; every later section quotes it rather than
restating a figure from memory.

| Quantity | Command | Value |
|---|---|---|
| **Files** with at least one reference of any kind | `git grep -lE 'bridge[./]utc\|from bridge import utc' -- '*.py' \| wc -l` | **57 files** |
| **Files** with at least one real import statement | `git grep -lE 'from bridge\.utc import\|from bridge import utc' -- '*.py' \| wc -l` | **51 files** |
| **Import statements** (grep lines) | `git grep -nE 'from bridge\.utc import\|from bridge import utc' -- '*.py' \| wc -l` | 60 raw lines |
| **Import statements**, real | 60 raw minus the one comment line that quotes an import verbatim (`tests/unit/test_session_stall_classifier.py:297`) | **59 import statements** |
| **Non-import reference lines** | `git grep -nE 'bridge[./]utc' -- '*.py' \| grep -vE ':[0-9]+: *from bridge\.utc import' \| grep -vE ':[0-9]+: *from bridge import utc'` | **19 lines across 15 files** |
| Files carrying **both** kinds | `comm -12` of the two file lists | 9 files |

The three file counts reconcile: 51 import-bearing + 15 prose-bearing − 9 overlap
= **57 files**. The earlier "54 import statements plus 3 string-literal paths =
57" was wrong on every term — 54 was invented, 3 undercounted the non-import
lines by a factor of six, and the 57 they summed to was a *file* count that never
decomposed that way in the first place.

**Two spellings exist and both must be rewritten.** A rule matching only the
dotted `bridge.utc` form leaves three sites behind and the completeness gate stays
red:

- dotted — `bridge.utc.to_unix_ts`, `from bridge.utc import …`, `"bridge.utc"` (16 of the 19 non-import lines)
- path — `bridge/utc.py::utc_now`, `bridge/utc.py::to_unix_ts` (3 sites: `models/agent_session.py:1022`, `models/agent_session.py:1135`, `tests/integration/test_updated_at_heal.py:51`)

The `bridge[./]utc` character class in the gate pattern covers both. Any narrower
per-site rewrite rule must be checked against both spellings explicitly.

**Directory breakdown of the 57 files:** tests 15, `reflections/` 13, `tools/` 9,
`agent/` 6, `bridge/` 5, `monitoring/` 4, `scripts/` 2, `models/` 2, `ui/` 1.

The issue's 57 is correct as a *file* count; its note about an independent pass
finding 56 is resolved — the earlier probe used `\s` in a POSIX-ERE `git grep -E`,
where `\s` is not a character class, so anchored patterns silently under-matched.

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

- **Assumption**: "Every reference outside an import statement is an executable string literal."
- **Method (first pass, too narrow)**: code-read — scan for string-literal module
  paths and dynamic import forms only. This found three sites and the plan then
  drew a *completeness* conclusion from it. The method never scanned comments or
  docstrings, so it could not support that conclusion. Two coverage gaps in the
  first draft trace directly to this mismatch.
- **Method (re-run, matching the claim)**: the scan is now as wide as the
  statement it supports — every non-import line, whatever its syntactic kind:

  ```bash
  git grep -nE 'bridge[./]utc' -- '*.py' \
    | grep -vE ':[0-9]+: *from bridge\.utc import' \
    | grep -vE ':[0-9]+: *from bridge import utc'
  ```

  This is the same command the task-6 validator runs, so builder and validator
  share one definition of done rather than two descriptions of it.
- **Finding**: **19 non-import reference lines across 15 files**, not three. By kind:

  | Kind | Count | Sites |
  |---|---|---|
  | Executable string literal | 3 | `tests/integration/test_reflections_redis.py:107`, `:129`; `tests/unit/test_session_stall_classifier.py:300` |
  | Executable dict key | 1 | `tests/unit/test_public_api_contract.py:57` |
  | Comment or docstring | 15 | the remainder — see the full list in task 4 |

  Only the first four change program behavior. The other 15 are prose inside
  source files, and they are exactly what the first draft's task list forgot:
  they are invisible to an import rewriter *and* invisible to the test suite, so
  nothing but the completeness gate catches them, and eleven of them had no task.

  The `patch` site is load-bearing. `agent/session_stall_classifier.py:233` does a
  function-local `from bridge.utc import to_unix_ts`, so the patch resolves
  against the live module attribute and genuinely takes effect. A stale target
  string would not raise — `unittest.mock.patch` would fail loudly on a
  nonexistent module, but the danger is the opposite direction: pointing it at a
  module that still exists while the code under test imports a different one
  turns a real assertion into a no-op that still reports green. Because the hard
  move deletes `bridge/utc.py` entirely, this particular site fails loudly rather
  than silently, which is the argument for the hard move over a shim.
- **Confidence**: high — the scan is now co-extensive with the completeness gate,
  so the two cannot disagree.
- **Impact on plan**: the four executable sites are named as explicit build tasks
  (task 3). The 15 prose sites get their own task (task 4) with a full file:line
  list, because a bulk import rewrite reaches none of them.

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
| `kernel/utc.py` | 2 modules / 1 ms | one-token edit to `pyproject.toml:115` | Rejected, narrowly |
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

**`kernel/` is rejected narrowly, and the packaging argument against it is
weaker than the first draft claimed.** A correction, because leaving a bad
argument in a plan is worse than leaving the decision open:

> The first draft argued that `kernel` "would not be packaged" and implied a
> latent wheel-import failure. That overstates the cost. `pyproject.toml:115`
> reads `packages = ["bridge", "tools", "scripts", "agent", "utils", "ui"]` —
> adding `"kernel"` is a one-token edit on a line this plan can see. It is a
> thing to remember, not a hazard.

With that corrected, `kernel/` matches `utils/` exactly on the only functional
axis that matters (an empty `__init__.py`, 2 modules, 1 ms) and costs one extra
token of packaging metadata. The remaining case against it is modest and honest:

- It creates a seventh top-level package to hold one 60-line module, against the repo's preference for minimal surface. A package named `kernel` also invites the immediate question of which of `utils/`'s six existing modules belong in it — a much larger reorganization the issue does not sanction and this plan will not start.
- The name has no precedent anywhere in this repo; `utils/` is where every other small stdlib-shaped helper already lives.

**The decision rests on the measured import cost, which is where the real
evidence is.** `config/` is disqualified by 214 modules against 2 — that is a
hundredfold difference and it decides the question. Between `utils/` and
`kernel/` there is no measurable difference at all, and `utils/` wins on
"changes nothing that does not need changing". A reviewer who prefers `kernel/`
is not wrong on evidence; they are making a naming judgment, and the cost of
honoring it is one token in `pyproject.toml` plus a new directory.

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

The work is mechanically large (57 files, 59 import statements, 19 prose lines)
and conceptually tiny (one path rewrite plus one guard test). The cost is in
verification breadth, not in design. The agent roster matches this: **one builder
for tasks 1–6, one validator for task 7**, consistent with "solo dev, code
reviewer". Six of the seven tasks are strictly sequential and share a single
ledger (the residual-reference count), so there is no concurrency to buy and each
extra agent would only cost a context handoff on the one dimension — completeness
— that handoffs lose.

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
- **57 rewritten files**, decomposing (per the Freshness Check census) into:
  - **59 import statements** across **51 files** — the bulk mechanical rewrite.
  - **19 non-import reference lines** across **15 files** — of which 4 are executable (3 string literals, 1 dict key) and 15 are comments or docstrings. An import rewriter reaches none of these.
  - 9 files carry both kinds; 51 + 15 − 9 = 57.
- **A boundary guard in `tests/unit/test_architectural_constraints.py`**: asserts the three freed `tools/` packages carry no harness imports, and that `utils/__init__.py` imports nothing. This is **added scope, not an issue acceptance criterion** — issue #2867 asserts a *state* ("zero remaining imports from…"), not a test enforcing it. The guard is justified by Risk 2 alone: without it the detachment is a fact of today with nothing defending it tomorrow. A reviewer approving this plan is approving that addition knowingly.
- **Docs and the generated graph**: `docs/features/utc-timestamps.md`, `docs/features/session-lifecycle.md`, and the path strings in `site/assets/graph.js`.

### Flow

Not a user-facing feature. The developer-facing flow:

Developer needs the current time → `from utils.utc import utc_now` → gets a tz-aware UTC datetime, having loaded two modules and touched no harness package.

### Technical Approach

- **`git mv bridge/utc.py utils/utc.py`**, so the move is recorded as a rename and review reads as a rename rather than a delete-plus-add.
- **Rewrite in three passes, not two.** (1) A bulk pass over the **59 import statements** in **51 files** (`from bridge.utc import` → `from utils.utc import`). (2) Four hand edits for the executable non-import sites — three string literals and one dict key. (3) A prose sweep over the **15 comment and docstring lines** that neither of the first two passes touches. No pass is trusted on its own; the completeness gate below is what closes the accounting.

- **The completeness gate, stated so it can actually fail.** The gate is:

  ```bash
  git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l   # must print 0
  ```

  or, for use inside a script, the fail-closed twin `! git grep -qE 'bridge[./]utc|from bridge import utc' -- '*.py'` (exit 0 means clean).

  **Do not use `grep -rc … --include='*.py' <many paths>`.** With more than one
  path argument `grep -c` prints a `path:count` line per file — 1294 lines in
  this repo, nearly all ending `:0` — and exits 0 whether or not there are
  matches. "Expected: match count == 0" is not an observable outcome of that
  command; it is a 1294-line wall a human skims past. `grep -c` yields a bare
  number only when given exactly one file argument. Every multi-path row in the
  Verification table has been converted to the `git grep -l … | wc -l` form.
  Where plain `grep` is still used, `--include='*.py'` is quoted, because zsh
  glob-expands it unquoted before grep ever sees it.

  Note the gate is deliberately wider than any rewrite rule: `bridge[./]utc`
  matches both the dotted `bridge.utc` and the path `bridge/utc.py` spellings.
  A rewrite rule written against the dotted form alone leaves three sites behind
  and this gate is what reports it.
- **Let ruff re-sort.** `python -m ruff check --fix` handles the isort reordering that follows from `utils` sorting after `tools` where `bridge` sorted first. Then `python -m ruff format`. No other linting.
- **Keep `tests/unit/test_utc.py` where it is.** The suite is flat under `tests/unit/` and does not mirror source paths, so the file name is already correct. Only its import line and its docstring change. Moving it would add churn for no signal.
- **Update the public API contract key, not its value.** `("bridge.utc", "utc_now")` becomes `("utils.utc", "utc_now")`; the signature string `"() -> datetime.datetime"` is unchanged because the function is unchanged.
- **Treat `site/assets/graph.js` as a path-keyed artifact, not as prose.** Its node ids are derived from file paths (`"id": "file:bridge/utc.py"`, `"filePath": "bridge/utc.py"`, and edge endpoints). A literal `bridge/utc.py` → `utils/utc.py` replacement in that file is exactly what a regeneration would produce for these nodes, and it keeps the committed graph honest without running the whole `/understand` pipeline. Commit `961d20eee` treated staleness in this file as a real defect (#2531), so leaving it stale is not the neutral option.
- **Commit in checkpoints**, not one lump: (1) the `git mv` plus the module docstring, (2) the bulk import rewrite plus ruff, (3) the four executable non-import sites, (4) the 15 prose sites, (5) the guard test, (6) docs and graph. Each is independently reviewable and each keeps the tree importable. **Each checkpoint commit message ends with the current gate reading** — `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` — so the ledger of remaining work is a measured number carried in git history rather than a claim in a handoff message. The number must fall monotonically: 57 → ~6 → ~2 → 0.

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
- [ ] `tests/integration/test_reflections_redis.py` lines 107 and 129 — UPDATE: change `__import__("bridge.utc", fromlist=["utc_now"])` to `__import__("utils.utc", fromlist=["utc_now"])` at both sites. **Must be executed, not merely collected** — see the execution note below.

**Test files whose only reference is an import statement** — UPDATE: plain
import-path rewrite, no assertion changes:

- [ ] `tests/performance/test_benchmarks.py:22`
- [ ] `tests/unit/test_hooks_audit.py:372`
- [ ] `tests/unit/test_job_model.py` — five sites (`:266`, `:280`, `:307`, `:322`, `:987`)
- [ ] `tests/unit/test_messenger.py:9`
- [ ] `tests/unit/test_migrations.py:25`
- [ ] `tests/unit/test_reconciler.py:1133`
- [ ] `tests/unit/test_session_archive.py:29`
- [ ] `tests/unit/test_valor_telegram.py:13`

**Test files whose only reference is prose, with NO import at all** — UPDATE:
comment or docstring edit only. The first draft listed these three as "plain
import-path rewrite", which is false: an import rewriter finds nothing to rewrite
in any of them and their references survive untouched:

- [ ] `tests/integration/test_updated_at_heal.py:51` — docstring, **path spelling** `bridge/utc.py::to_unix_ts`
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py:347` — comment, dotted spelling
- [ ] `tests/unit/session_runner/test_liveness.py:181` — docstring, dotted spelling

**Test files carrying both an import and a prose reference** — UPDATE: both must
be edited; fixing the import alone leaves the gate red:

- [ ] `tests/unit/test_reconciler.py` — import at `:1133`, docstring at `:1132`
- [ ] `tests/unit/test_utc.py` — import at `:7`, module docstring at `:1`
- [ ] `tests/unit/test_session_stall_classifier.py` — comment at `:297` and `:298`, `patch()` target at `:300`

**New test:**

- [ ] `tests/unit/test_architectural_constraints.py` — UPDATE: add a new `TestStandaloneToolPackageBoundaries` class. The file already has the AST helper this needs (`_get_imports`, which uses `ast.walk` and therefore catches function-local imports as well as top-level ones), so the guard extends an existing pattern instead of introducing one.

### Execution coverage for the modified non-unit tests

Three of the files this plan edits live outside `tests/unit/`:
`tests/integration/test_reflections_redis.py`,
`tests/integration/test_updated_at_heal.py`, and
`tests/performance/test_benchmarks.py`. The first draft's verification rows and
success criteria all stopped at `tests/unit`, so the plan edited three files it
never ran.

That gap is sharpest on the two `__import__("bridge.utc", …)` sites, which are
precisely the references the plan calls invisible to an import rewriter. They sit
inside test *bodies*, so **`--collect-only` is not a substitute**: collection
succeeds with a wrong module path and the failure only appears when the line
executes.

`tests/integration/` needs Redis, so this runs in the task-7 validator pass, not
in the builder's per-checkpoint loop:

```bash
scripts/pytest-clean.sh tests/integration/test_reflections_redis.py \
  tests/integration/test_updated_at_heal.py \
  tests/performance/test_benchmarks.py -q
```

The issue's own acceptance criteria require the **full** `scripts/pytest-clean.sh`
suite, not `tests/unit` alone. Success Criteria now says so.

## Rabbit Holes

- **Reorganizing `utils/` while you are in there.** The moment `utc.py` lands beside `api_keys.py` and `json_cache.py`, the question "should these be a kernel package?" becomes tempting. It is a different change with a different blast radius and it is not what issue #2867 asks for. Move the one module and stop.
- **Broadening the boundary guard to ban `config` imports.** The guard's forbidden set is exactly the issue's acceptance criterion — `bridge`, `agent`, `worker`, `models`, `monitoring`, `reflections` (plus `analytics` and `ui` for symmetry). Adding `config` would make the test stricter than anything agreed and would forbid `tools/image_gen`'s legitimate `config.models` dependency if the guard's package list ever widens.
- **Collapsing the three inline `to_unix_ts` duplicates.** `monitoring/session_watchdog._to_timestamp`, `agent/session_health._to_ts`, and `ui/data/sdlc._safe_float` each reimplement the same naive-datetime guard. `docs/features/utc-timestamps.md` records that #777 left them untouched on purpose. Touching them here would triple the review surface of a rename.
- **Regenerating the whole `site/assets/graph.js` knowledge graph.** The targeted path-string replacement is correct and cheap. Running the full `/understand` pipeline would rewrite thousands of unrelated lines and bury the actual change.
- **Hand-verifying 57 files.** The completeness proof is `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` printing `0`, plus the full test suite. Reading each file individually is slower and less reliable. This holds only because the gate is a single number that can be wrong out loud — the first draft's `grep -rc` form printed 1294 lines and exited 0 regardless, which is hand-verification wearing a command's clothes.

## Risks

### Risk 1: A string-literal reference is missed and a test silently stops asserting

**Impact:** `tests/unit/test_session_stall_classifier.py`'s patch target is the sharp case. In general, a `patch()` aimed at the wrong module either raises or quietly patches something the code under test never reads, and the second failure mode reports green.

**Mitigation:** The hard move deletes `bridge/utc.py`, so a missed `patch("bridge.utc...")` raises `ModuleNotFoundError` rather than passing. This is the strongest single argument for the no-shim constraint and it should be stated in the PR body. Backed by the completeness gate `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` printing `0`, which catches string literals that no import-aware tool sees. That gate is only load-bearing in the `git grep -l … | wc -l` form; the `grep -rc` form this plan originally specified could not fail. Additionally, mutate the patch's `return_value` and confirm the test flips to failing, proving the patch still reaches the code.

Note the `__import__("bridge.utc", …)` sites in `tests/integration/test_reflections_redis.py` fail the *opposite* way: they are inside test bodies, so they raise only when executed. They are covered by the task-7 integration run, not by collection.

### Risk 2: The detachment silently regresses after this ships

**Impact:** `utils/__init__.py` is empty today by accident, not by contract. One `from models import ...` added there later re-couples `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` to the harness with no test failing. The issue's whole payoff evaporates and nobody notices.

**Mitigation:** The boundary guard test asserts both halves — the three tool packages import no harness module, and `utils/__init__.py` contains zero import statements. Mutation-check both: add a harness import to a scratch copy of each guarded file and confirm the test goes red before trusting it.

### Risk 3: An importer lands on main during the build and is missed

**Impact:** A file added by a concurrently-merging PR imports `bridge.utc`, the branch merges, and main breaks at import time.

**Mitigation:** Zero overlap between the 57 files and all four open PRs is confirmed at plan time, and no commits have landed since the issue was filed. Re-run `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` immediately before opening the PR and again after any rebase; it must print `0` on the branch. The `ModuleNotFoundError` from the deleted module means a newly-landed top-level importer fails loudly at collection rather than degrading quietly.

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

Nothing deferred. Every one of the issue's seven acceptance criteria is in scope,
**including the last one — the full `scripts/pytest-clean.sh` suite, not a
`tests/unit` subset.** The first draft claimed full coverage while its Success
Criteria narrowed to `tests/unit`; that contradiction is resolved in favor of the
issue.

Two items in this plan are **additions beyond the issue**, and a reviewer should
approve them as such rather than as inherited requirements:

- **The boundary guard test** (`TestStandaloneToolPackageBoundaries`). Issue #2867 asserts a *state* — "`tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` have zero remaining imports from `bridge/`, `agent/`, `worker/`, `models/`, `monitoring/`, or `reflections/`" — and contains no criterion requiring a test to enforce it. The guard is justified by **Risk 2** alone: the property is incidental today and one import added to `utils/__init__.py` later would silently undo the whole change. That is a good reason, and it is this plan's reason, not the issue's.
- **The `site/assets/graph.js` path-string update.** The issue does not mention the generated graph. It is included because commit `961d20eee` (#2531) treated staleness in that file as a real defect.

Three things are **non-goals** of this change rather than postponed work. They
were never part of issue #2867 and this plan makes no promise about them:

- Detaching `tools/telegram_history` from the harness. It carries 17 `models.*`
  imports and moving one time helper does not touch that. It is named in the
  issue explicitly as *not* freed by this change.
- Detaching `tools/image_gen` from `config.models`. The issue's stated payoff for
  that package is "leaves only `config.models`", which this plan achieves exactly.
- Reorganizing `utils/` or introducing a `kernel/` package. The Architectural
  Impact section settles the destination; that is a decision made, not a decision
  postponed. Note the case against `kernel/` specifically is a judgment call on
  minimal surface, not a measured cost — `config/` is the candidate the
  measurements eliminate.

## Update System

No update system changes required. This change adds no dependency, no config
file, no secret, no launchd service, and no migration. `utils` is already one of
the six entries in `pyproject.toml:115`'s
`[tool.hatch.build.targets.wheel] packages`, so no packaging metadata moves. That
is a small convenience of the `utils/` choice, not the reason for it — the
alternative would have cost one token on that same line.

There is no Popoto model change, so `scripts/update/migrations.py` is untouched.
Note that `scripts/update/git.py:116` and `scripts/update/run.py:255` are both in
the 57-file list and get the plain import rewrite; neither changes behavior.

## Agent Integration

No agent integration required. This is an internal module relocation with no new
capability to expose. No `[project.scripts]` entry point changes, no MCP server
changes, no `.mcp.json` changes, and the bridge imports nothing new.

The bridge does import the moved module — `bridge/dedup.py`,
`bridge/escape_hatch.py`, `bridge/session_transcript.py`,
`bridge/telegram_bridge.py`, and `bridge/telegram_relay.py` are five of the 57
files and get the plain import rewrite. `bridge/telegram_bridge.py` additionally
carries a docstring reference at `:155` that the prose sweep handles.

**Restart obligation.** Because bridge code changes, the repo's restart rule
applies. The first draft left this as prose here and in no task, verification row,
or success criterion — and task 6 was scoped to "every row of the Verification
table", where it did not appear. It is now a **Post-Merge Checklist** item
(below Success Criteria) with an explicit command and verification:

```bash
./scripts/valor-service.sh restart
tail -5 logs/bridge.log        # must show "Connected to Telegram"
```

**Sequence this after the merge to `main`, never during the build.** Restarting
while the feature branch is checked out puts a half-migrated tree in front of live
Telegram traffic. It is deliberately not a build-stage verification row for that
reason.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/utc-timestamps.md` — retitle the `## The bridge/utc Module` heading, update the three code samples at lines 16, 60, and 85 to `from utils.utc import ...`, and update the `bridge.utc.to_unix_ts(val)` reference in the read-path guidance. Add a sentence recording that the module lives in `utils/` because it is dependency-free and consumed by standalone tools, so the next reader does not have to re-derive the reasoning.
- [ ] Update `docs/features/session-lifecycle.md:398` — `bridge.utc.utc_now()` becomes `utils.utc.utc_now()`.
- [ ] No new entry in `docs/features/README.md`. The existing UTC Timestamps row (line 258) describes behavior and names no path, so it stays correct as written.
- [ ] Update `site/assets/graph.js` — replace the **31** `bridge/utc.py` path strings in the node ids, `filePath` fields, and edge endpoints with `utils/utc.py`.

### External Documentation Site

- [ ] `site/` is the documentation site and its only affected content is the generated graph covered above. No page copy references the module path.

### Inline Documentation

- [ ] Update the moved module's docstring so it no longer reads as bridge-local, and state that it is dependency-free by contract because standalone `tools/` packages import it. (Task 1.)
- [ ] **All 15 comment and docstring references inside source files are owned by task 4, not by the documentation pass.** They are enumerated there with file:line and spelling. They live under the completeness gate — unlike `docs/` prose, a missed one keeps `git grep` red — so they are sequenced before the guard test and the docs pass rather than after. This includes `agent/session_stall_classifier.py:11` and the two-line comment above `tests/unit/test_session_stall_classifier.py:300`, both of which the first draft filed here.
- [ ] Leave `docs/plans/completed/*.md` untouched. Those are historical records of shipped work and describe the code as it was when they shipped. They are excluded by pathspec from every gate.

## Success Criteria

- [ ] `utils/utc.py` exists and exports `utc_now`, `to_local`, `utc_iso`, `to_unix_ts` with unchanged signatures and bodies.
- [ ] `bridge/utc.py` does not exist. No shim, no re-export, no alias anywhere in the repo.
- [ ] **`git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` prints `0`.** This single number covers all 59 import statements and all 19 non-import reference lines, in both the dotted and path spellings. It is the completeness proof for the whole change.
- [ ] `git grep -lE 'bridge[./]utc' -- 'docs/features/*.md' 'site/assets/graph.js' | wc -l` prints `0`. (`docs/plans/completed/*.md` is deliberately excluded — those are shipped history.)
- [ ] `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` have zero imports from `bridge`, `agent`, `worker`, `models`, `monitoring`, or `reflections` — including inside their `tests/` directories and including function-local imports.
- [ ] `tests/unit/test_public_api_contract.py` passes with the `("utils.utc", "utc_now")` key.
- [ ] `tests/unit/test_utc.py` passes with the same collected test count as before the move.
- [ ] The new boundary guard test fails when a harness import is injected into any guarded file (mutation-verified per guard, not once for the class).
- [ ] **The three modified non-unit test files actually execute and pass** — `scripts/pytest-clean.sh tests/integration/test_reflections_redis.py tests/integration/test_updated_at_heal.py tests/performance/test_benchmarks.py -q`. Collection alone does not count: the `__import__` sites are inside test bodies.
- [ ] **The full suite passes** — `scripts/pytest-clean.sh` with no path argument, per the issue's seventh acceptance criterion. A `tests/unit`-only run does not satisfy the issue and does not execute three of the files this change edits. Expect roughly 20 minutes for the unit portion; a long run is not a stuck run.
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.

## Post-Merge Checklist

Not part of the build and not a Verification-table row. These run against `main`
after the PR merges, because they act on live services.

- [ ] `./scripts/valor-service.sh restart` — five `bridge/` modules changed import paths, so the running bridge, watchdog, and worker are on stale code until cycled.
- [ ] `tail -5 logs/bridge.log` shows "Connected to Telegram".
- [ ] Re-run the completeness gate on `main` after the merge commit: `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` prints `0`. This is the only check that catches an importer merged from a concurrent PR between the branch's last rebase and the merge (Risk 3).

## Team Orchestration

The lead agent orchestrates and does not build directly.

**Two agents, not four.** The first draft assigned four named agents across six
tasks that were each `Parallel: false` and each depended on the one before it.
There was no concurrency to buy, so the roster purchased three context handoffs on
a change whose entire difficulty is *completeness across 57 files* — the one thing
a handoff loses. It also contradicted this plan's own Appetite line ("Solo dev,
code reviewer") on a `Small` change.

Tasks 1–6 share one ledger: the residual-reference count. They go to one builder,
who carries that number from checkpoint to checkpoint and re-emits it in every
commit message. Task 7 goes to a separate validator, because independent
verification is the one place a fresh context is worth its cost.

### Team Members

- **Builder**
  - Name: `utc-mover`
  - Role: tasks 1–6 — the rename, the bulk import rewrite, the four executable non-import sites, the 15 prose sites, the boundary guard test, and the docs/graph updates
  - Agent Type: builder
  - Resume: true
  - Handoff artifact: every checkpoint commit message ends with the gate reading `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l`, so progress is a measured number in git history rather than a claim

- **Validator**
  - Name: `utc-validator`
  - Role: task 7 — runs every Verification row, runs the full `scripts/pytest-clean.sh` suite, confirms zero residual references, and confirms the mutation checks in tasks 3 and 5 were performed and reported rather than asserted
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
- Record the pre-move baseline: `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l` (expect **57 files**) and the collected test count of `tests/unit/test_utc.py`.
- `git mv bridge/utc.py utils/utc.py`.
- Edit only the module docstring: drop the bridge-local framing, state that the module is dependency-free by contract because standalone `tools/` packages import it.
- Commit checkpoint: "Move the UTC helper from bridge/ to utils/ (#2867)", ending with the gate reading.

### 2. Rewrite the 59 import statements across 51 files

- **Task ID**: build-rewrite-imports
- **Depends On**: build-move-module
- **Validates**: tests/unit/test_utc.py, tests/unit/test_public_api_contract.py
- **Informed By**: the Freshness Check census (59 import statements in 51 files; 19 non-import lines in 15 files are explicitly NOT this task's job)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `from bridge.utc import` → `from utils.utc import` across all `*.py` files. The census counts **59 real import statements in 51 files**. A raw `git grep -nE 'from bridge\.utc import|from bridge import utc' -- '*.py'` returns **60 lines**; the extra one is `tests/unit/test_session_stall_classifier.py:297`, a comment that quotes an import verbatim. Leave it to task 4 — it is prose, not code.
- Include the function-local imports (`tools/telegram_history/__init__.py:333`, `agent/session_stall_classifier.py:233`, and the many indented `from bridge.utc import` lines inside function bodies across `reflections/`, `models/`, and `bridge/`), which a top-level-only pass would miss.
- Update the `tests/unit/test_public_api_contract.py:57` dict key to `("utils.utc", "utc_now")`, leaving the signature string untouched.
- Update the `tests/unit/test_utc.py:7` import line. Its module docstring at `:1` belongs to task 4.
- Run `python -m ruff check --fix` to re-sort the import blocks (`utils` sorts after `tools` where `bridge` sorted first), then `python -m ruff format`. No other linting.
- Confirm the tree still imports: `python -c "import utils.utc, bridge.telegram_bridge"`.
- Commit checkpoint with the gate reading in the message. **Expected: 14 files remain.** The 15 prose-bearing files, minus `tests/unit/test_public_api_contract.py` whose only non-import reference is the dict key this task fixes. A reading of 57 means the rewrite did not run; a reading below 14 means it rewrote prose it should have left to task 4; a reading of 0 here is a red flag, not a success.

### 3. Fix the four executable non-import references

- **Task ID**: build-executable-sites
- **Depends On**: build-rewrite-imports
- **Validates**: tests/unit/test_session_stall_classifier.py, tests/integration/test_reflections_redis.py
- **Informed By**: spike-3 (4 executable non-import sites: 3 string literals + 1 dict key)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- `tests/integration/test_reflections_redis.py:107` and `:129` — `__import__("bridge.utc", fromlist=["utc_now"])` → `__import__("utils.utc", ...)`. Both sites.
- `tests/unit/test_session_stall_classifier.py:300` — `patch("bridge.utc.to_unix_ts", ...)` → `patch("utils.utc.to_unix_ts", ...)`.
- (`tests/unit/test_public_api_contract.py:57` is the fourth executable site; it was handled in task 2 alongside the import rewrite because it lives in the same file's contract dict.)
- **Prove the patch still bites**: temporarily change its `return_value` from `None` to a float and confirm `test_unparseable_timestamp_returns_healthy_not_stalled` fails, then revert. A patch aimed at a module the code under test never reads passes green, and only this check distinguishes the two.
- Do **not** claim completeness here — 15 prose lines remain and task 4 owns them. Record the gate reading and move on.
- Commit checkpoint.

### 4. Sweep the 15 comment and docstring references

- **Task ID**: build-prose-sites
- **Depends On**: build-executable-sites
- **Validates**: the completeness gate (no test covers these lines)
- **Informed By**: spike-3 re-run (19 non-import lines total, 4 executable, 15 prose)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false

These are prose inside source files. No import rewriter reaches them and no test
fails because of them — the completeness gate is their only detector, which is
why the first draft lost eleven of them. **Two spellings appear and a rule
matching only the dotted form leaves three sites behind.**

Dotted spelling — `bridge.utc.…` → `utils.utc.…`:

- [ ] `agent/session_runner/liveness.py:68` — docstring, ``bridge.utc.to_unix_ts``
- [ ] `agent/session_stall_classifier.py:11` — module docstring, "Uses bridge.utc.to_unix_ts for all datetime → float conversions."
- [ ] `bridge/telegram_bridge.py:155` — docstring, ``bridge.utc.to_unix_ts``
- [ ] `models/job.py:695` — docstring, ``bridge.utc.to_unix_ts(job.last_active_at)``
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py:347` — comment, "per bridge.utc.to_unix_ts's"
- [ ] `tests/unit/session_runner/test_liveness.py:181` — docstring, "(mirrors bridge.utc.to_unix_ts)"
- [ ] `tests/unit/test_reconciler.py:1132` — docstring, "pinned via bridge.utc.to_unix_ts"
- [ ] `tests/unit/test_session_stall_classifier.py:297` and `:298` — the two-line comment explaining the patch target, naming `bridge.utc` once each
- [ ] `tests/unit/test_utc.py:1` — module docstring, "Tests for bridge.utc utility module."
- [ ] `tools/agent_session_scheduler.py:45` — docstring, ``bridge.utc.to_unix_ts``
- [ ] `tools/telegram_history/__init__.py:330` — docstring, ``bridge.utc.to_unix_ts``

Path spelling — `bridge/utc.py::` → `utils/utc.py::`. **These three are the ones a
dotted-only rule misses:**

- [ ] `models/agent_session.py:1022` — "(see bridge/utc.py::utc_now)"
- [ ] `models/agent_session.py:1135` — "(consistent with bridge/utc.py::to_unix_ts)"
- [ ] `tests/integration/test_updated_at_heal.py:51` — "the same logic used by bridge/utc.py::to_unix_ts"

- After the sweep, the gate must print `0`: `git grep -lE 'bridge[./]utc|from bridge import utc' -- '*.py' | wc -l`. This is the first checkpoint at which a completeness claim is legitimate.
- Commit checkpoint with the gate reading `0` in the message.

### 5. Add the boundary guard test

- **Task ID**: build-boundary-guard
- **Depends On**: build-prose-sites
- **Validates**: tests/unit/test_architectural_constraints.py
- **Informed By**: spike-2 (confirmed: the property holds today but nothing enforces it)
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- **Scope note**: this test is added scope, justified by Risk 2, not by an issue acceptance criterion (see No-Gos).
- Add `TestStandaloneToolPackageBoundaries` to `tests/unit/test_architectural_constraints.py`, reusing the file's existing `_get_imports` AST helper — it uses `ast.walk`, so it catches function-local imports, which a top-level-only check would miss on exactly the files that matter.
- `test_standalone_tool_packages_have_no_harness_imports`: walk every `*.py` under `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler` including their `tests/` directories; assert no import's first path segment is in `{bridge, agent, worker, models, monitoring, reflections, analytics, ui}`. Failure message must name the offending file, line, and module. Do not add `config` to the forbidden set (see Rabbit Holes).
- `test_utils_package_init_imports_nothing`: assert `utils/__init__.py` parses to zero `Import`/`ImportFrom` nodes, with a failure message explaining that a harness import here silently re-couples all three packages above.
- **Mutation-check each assertion separately**: inject a harness import into a scratch copy of `tools/selfie/__init__.py`, then of a file under `tools/sms_reader/tests/`, then into `utils/__init__.py`, and confirm the corresponding test goes red each time. A test that reaches no code passes for the wrong reason; per-guard measurement is the only thing that rules it out.
- Commit checkpoint.

### 6. Documentation and generated graph

- **Task ID**: document-feature
- **Depends On**: build-boundary-guard
- **Assigned To**: utc-mover
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/utc-timestamps.md` — the `## The bridge/utc Module` heading, the three code samples at `:16`, `:60`, and `:85`, and the `bridge.utc.to_unix_ts(val)` read-path guidance. Add the one-sentence rationale for the `utils/` location.
- Update `docs/features/session-lifecycle.md:398` — `bridge.utc.utc_now()` → `utils.utc.utc_now()`.
- Replace the **31** `bridge/utc.py` path strings in `site/assets/graph.js` (node ids, `filePath` fields, and edge endpoints) with `utils/utc.py`.
- (`agent/session_stall_classifier.py:11` moved to task 4 — it is a source-file docstring and belongs with the other 14 prose sites, under the completeness gate rather than in the docs pass.)
- Leave `docs/plans/completed/*.md` untouched — they record shipped history and are deliberately excluded from every gate in this plan.
- Verify: `git grep -lE 'bridge[./]utc' -- 'docs/features/*.md' 'site/assets/graph.js' | wc -l` prints `0`.
- Commit checkpoint.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-move-module, build-rewrite-imports, build-executable-sites, build-prose-sites, build-boundary-guard, document-feature
- **Assigned To**: utc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run **every row** of the Verification table and report the literal command output for each, not a pass/fail summary. Several rows print a number; the number is the evidence.
- Run the full suite: `scripts/pytest-clean.sh` with no path argument. This is the issue's seventh acceptance criterion and the only thing that executes `tests/integration/test_reflections_redis.py`, `tests/integration/test_updated_at_heal.py`, and `tests/performance/test_benchmarks.py` — three files this change edits. Never bare `pytest`; never a pattern kill to clean up. Roughly 20 minutes for the unit portion is normal.
- Re-run the completeness gate after any rebase, since a concurrently-merged importer surfaces only then (Risk 3).
- Confirm the mutation checks in tasks 3 and 5 were **performed and reported with their red output**, not asserted. A "mutation check passed" with no failing-test output is an unverified claim.
- Report pass/fail per criterion with evidence attached.
- The **Post-Merge Checklist** (service restart) is deliberately NOT this task's job. It runs on `main` after merge.

## Verification

Every row below prints either an exit code or a **bare number**. No row asks a
reader to scan a multi-line listing for the absence of something — that was the
defect that made the first draft's completeness proof unrunnable. `grep -c`
appears nowhere: it emits `path:count` lines rather than a total whenever it is
given more than one file argument, and it exits 0 regardless of matches. `git grep
-l … | wc -l` is used throughout because it yields one number under every
argument count and exits 0 whether or not it matched.

| Check | Command | Expected |
|-------|---------|----------|
| Module exists at new path | `test -f utils/utc.py` | exit code 0 |
| Old module is gone (anti-criterion: no shim) | `test -e bridge/utc.py` | exit code != 0 |
| **No residual references in Python (the completeness gate)** | `git grep -lE 'bridge[./]utc\|from bridge import utc' -- '*.py' \| wc -l` | prints `0` |
| Same gate, fail-closed form for scripts | `! git grep -qE 'bridge[./]utc\|from bridge import utc' -- '*.py'` | exit code 0 |
| Standalone tools carry no harness imports | `git grep -lE '^[[:space:]]*(from\|import)[[:space:]]+(bridge\|agent\|worker\|models\|monitoring\|reflections\|analytics\|ui)[.[:space:]]' -- 'tools/selfie/*.py' 'tools/sms_reader/*.py' 'tools/test_scheduler/*.py' \| wc -l` | prints `0` |
| Moved module imports no first-party package | `git grep -lE '^(from\|import) (bridge\|agent\|worker\|models\|config\|monitoring\|reflections\|tools\|ui\|utils)\b' -- utils/utc.py \| wc -l` | prints `0` |
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_utc.py tests/unit/test_public_api_contract.py tests/unit/test_architectural_constraints.py tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| **Modified non-unit tests actually execute** | `scripts/pytest-clean.sh tests/integration/test_reflections_redis.py tests/integration/test_updated_at_heal.py tests/performance/test_benchmarks.py -q` | exit code 0 |
| **Full suite passes** (issue acceptance criterion 7) | `scripts/pytest-clean.sh` | exit code 0 |
| `tests/unit/test_utc.py` collects the same number of tests as before the move | `scripts/pytest-clean.sh tests/unit/test_utc.py --collect-only -q \| tail -1` | same count as the task-1 baseline |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Docs and generated graph no longer cite the old path | `git grep -lE 'bridge[./]utc' -- 'docs/features/*.md' 'site/assets/graph.js' \| wc -l` | prints `0` |

**Why the non-unit row cannot be replaced by `--collect-only`.** The two
`__import__("utils.utc", fromlist=["utc_now"])` calls in
`tests/integration/test_reflections_redis.py` sit inside test *bodies*. Collection
imports the module and succeeds with a wrong path still in place; only execution
raises. `tests/integration/` needs Redis, which is why this row belongs to the
task-7 validator rather than the builder's checkpoint loop.

**Excluded from every gate on purpose:** `docs/plans/completed/*.md` (shipped
history) and this plan document itself, which quotes the old path throughout. All
gates are scoped by pathspec so neither can turn a row red.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, History & Consistency | The Verification row "Moved module imports no first-party package" is fail-open and can never go red. Its pattern ends in `\b`, a GNU extension POSIX ERE does not support, so `git grep -E` matches nothing at all. Measured: the pattern as written returns **0 files** against `bridge/dedup.py`, whose line 24 is literally `from models.dedup import DedupRecord`; drop the `\b` and the same pattern returns 1. Repo-wide, `git grep -lE 'import\b' -- '*.py'` returns **0** against **1310** without it. This is the same defect class as round 1's BLOCKER (`grep -rc` exiting 0 regardless of matches), and the plan's own Freshness Check diagnoses the identical trap one page earlier: "the earlier probe used `\s` in a POSIX-ERE `git grep -E`, where `\s` is not a character class, so anchored patterns silently under-matched." The revision fixed `\s` in the probe and reintroduced the shape as `\b` in a new row. Task 7 instructs the validator to "run every row and report the literal command output" -- it will report `0` and bank it as passing evidence. | pending | Replace `\b` with the bracket expression the sibling row already uses correctly: `git grep -lE '^(from\|import)[[:space:]]+(bridge\|agent\|worker\|models\|config\|monitoring\|reflections\|tools\|ui\|utils)[.[:space:]]' -- utils/utc.py \| wc -l`. Never use `\b`, `\s`, `\d` or `\w` in a `git grep -E` pattern anywhere in this plan -- POSIX ERE silently matches nothing and the row reports clean. Second, independent fail-open on the same row: `git grep` against a path that does not exist prints nothing and exits 0, so this row also reads `0` when `utils/utc.py` is absent entirely (verified today, before the move). It is only meaningful when paired with the `test -f utils/utc.py` row, and `git grep` sees tracked files only -- a plain `mv` without `git add` leaves the module invisible to it. Prove the fixed row can fail by running it against `bridge/dedup.py` and confirming it prints 1. |
| CONCERN | Risk & Robustness | Checkpoint 1 leaves the tree non-importable, contradicting the plan's own guarantee and its Race Conditions section. Technical Approach commits `git mv bridge/utc.py utils/utc.py` as checkpoint (1) and the bulk import rewrite as checkpoint (2), then claims "each keeps the tree importable". After checkpoint 1 all 50 files with a real `from bridge.utc import` raise `ModuleNotFoundError`. Race Conditions names this exact state as the one thing to avoid -- "the tree must not be left with `bridge/utc.py` deleted and importers unrewritten across a commit boundary" -- and then says the checkpoint sequence handles it "by keeping the rename and the bulk rewrite adjacent". Adjacent is not atomic; the boundary between commits 1 and 2 is precisely the forbidden state. Task 1 also declares `Validates: tests/unit/test_utc.py`, which cannot pass at its own checkpoint because `tests/unit/test_utc.py:7` still reads `from bridge.utc import ...` until task 2. | pending | Either squash tasks 1 and 2 into a single commit (the honest fix -- a rename plus its call-site rewrite is one atomic change, and `git mv` still records the rename because git detects it from content similarity across the whole commit), or state explicitly that checkpoint 1 is intentionally non-importable, drop the "each keeps the tree importable" claim, and remove `Validates: tests/unit/test_utc.py` from task 1. Do not leave both the guarantee and the two-commit split in place. Note task 1's baseline capture (pre-move gate reading and `test_utc.py` collected count) must still run BEFORE the `git mv` either way. |
| CONCERN | History & Consistency | The reference census, the table created specifically to fix round 1's wrong counts, carries an off-by-one in two of its own file-count rows. The "Files with at least one real import statement" row runs the **unanchored** `git grep -lE 'from bridge\.utc import\|from bridge import utc'`, which counts `tests/unit/test_session_stall_classifier.py` -- whose only match is the `:297` comment that the very next row subtracts as not-a-real-import. Measured: unanchored 51 files, anchored 50; "files carrying both kinds" is 8, not 9. The table applies its own 60-to-59 correction to the statement count but not to the file count derived from the same grep. | pending | Change the census to 50 import-bearing files and 8 files carrying both; the reconciliation still holds (50 + 15 - 8 = 57) because the two errors cancel. Retitle task 2 to "across 50 files". Nothing downstream breaks: the 57 total, the 59 statement count, the 19/15 non-import figures, and task 2's "Expected: 14 files remain" are all independently correct -- 14 follows from the 15 prose-bearing files minus `tests/unit/test_public_api_contract.py`, which task 2 fixes, and is unaffected by 50-vs-51. Use the anchored form `git grep -lE '^[[:space:]]*(from bridge\.utc import\|from bridge import utc)' -- '*.py' \| wc -l` so the file count and the statement count come from the same definition. |
| CONCERN | Scope & Value | The boundary guard's forbidden set omits `config`, the single package spike-1 measured as most expensive, and the stated reason for omitting it does not apply to the guard as scoped. Task 5 walks only `tools/selfie`, `tools/sms_reader`, and `tools/test_scheduler`, asserting no import's first segment is in `{bridge, agent, worker, models, monitoring, reflections, analytics, ui}`. Rabbit Holes justifies excluding `config` because it "would forbid `tools/image_gen`'s legitimate `config.models` dependency" -- but `tools/image_gen` is not one of the three packages the guard walks, so that dependency is never in scope. Meanwhile spike-1 measured `config` at **214 modules / 94 ms** against `utils`'s 2 / 1 ms and used exactly that number to disqualify `config` as the destination. A future `from config.paths import X` added to `tools/selfie` would blow the detachment payoff harder than any package in the forbidden set and the guard would pass it. | pending | Add `config` to the forbidden set. It is free today: all three packages currently import nothing but `bridge.utc` (verified -- `git grep -nE '^[[:space:]]*(from\|import)[[:space:]]+(bridge\|agent\|worker\|models\|monitoring\|reflections\|analytics\|ui\|config)' -- 'tools/selfie/*.py' 'tools/sms_reader/*.py' 'tools/test_scheduler/*.py'` returns exactly the three `from bridge.utc import` lines), so the guard goes green the moment task 2 lands. If `config` is deliberately kept out, rewrite the Rabbit Holes rationale to the real reason, because the `tools/image_gen` justification is a non-sequitur for a guard that never walks that package. |
| NIT | Risk & Robustness | The "Standalone tools carry no harness imports" verification row misses a bare `import models` at end of line. Its trailing bracket expression `[.[:space:]]` requires a following `.` or whitespace, and `git grep` strips the newline, so an unqualified single-segment import at EOL never matches. Verified against a scratch file: `from bridge import x` and `import models.foo` matched; `import models` did not. | pending | NIT -- no implementation note required. Backstopped by the task-5 AST guard, which compares first path segments and catches the bare form. The pathspec itself is fine: `git grep -- 'tools/selfie/*.py'` does recurse into `tools/selfie/tests/`, verified, so the "including inside their tests/ directories" criterion is genuinely covered. |
| NIT | History & Consistency | A prior-art reference names a function that does not exist. Prior Art and Rabbit Holes both cite `agent/session_health._to_ts` as one of three inline `to_unix_ts` duplicates #777 deliberately left alone. `agent/session_health.py` exists but contains no `_to_ts` (`git log -S'_to_ts'` on that file is empty, so it likely never did); its inline naive-datetime guards are at `:403`, `:730`, and `:5987`. The other two, `monitoring/session_watchdog._to_timestamp:52` and `ui/data/sdlc._safe_float:803`, are accurate. The claim is inherited verbatim from `docs/features/utc-timestamps.md:85` -- the stale reference is in the doc, and task 6 edits that exact line for the path rewrite. | pending | NIT -- no implementation note required. The substance holds (session_health does carry inline guards) and no task depends on the name, so this is only worth the one-word fix while task 6 is already editing `docs/features/utc-timestamps.md:85`. Do not expand scope to touch the helpers themselves -- that Rabbit Hole is correct. |

---

## Open Questions

None. The single design decision the issue left open — the destination package —
is settled in Architectural Impact on measured evidence: `config/` costs 214
modules against `utils/`'s 2, which decides that candidate outright.

Between `utils/` and `kernel/` the evidence is genuinely thin, and this plan now
says so rather than manufacturing a cost. `kernel/` matches `utils/` on every
functional axis; adopting it would cost one token on `pyproject.toml:115` plus a
new top-level directory. `utils/` is chosen because it changes nothing that does
not need changing and every comparable helper already lives there. A reviewer who
prefers `kernel/` is making a defensible naming judgment, not correcting an
error — that is the one thing worth arguing before the build starts, and it is a
cheap argument to settle either way.
