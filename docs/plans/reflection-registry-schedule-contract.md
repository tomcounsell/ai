---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-14
tracking: https://github.com/yudame/ai/issues/2734
last_comment_id: 5286094986
revision_applied: false
---

# Reflection registry: schedule contract and worktree-safe resolution

## Problem

The reflections registry is a private, gitignored YAML file. Two independent defects sit on top of it, and the second hides the first.

**Defect 1 — the required-fields test encodes a narrower contract than the loader.**
`tests/unit/test_reflection_scheduler.py:667` asserts that *every* registry entry carries an `every` key. The loader does not require that: `ReflectionEntry.validate()` (`agent/reflection_scheduler.py:172`) requires only a `schedule`, and the normalizer at `agent/reflection_scheduler.py:266-283` accepts `every:` / `cron:` (+`cron_tz:`) / `at:` and folds all three into one `schedule` string. The registry's `sdlc-upvote-pickup` entry — added with `f1d86255a` / PR #2721 — is the first to use `cron:`, so it is the first entry the stale assertion rejects. The entry is valid and the reflection runs correctly; the test is wrong.

**Defect 2 — the registry is unresolvable from a worktree under launchd.**
`_resolve_registry_path` (`agent/reflection_scheduler.py:69-95`) deliberately skips the `~/Desktop` vault whenever `VALOR_LAUNCHD` is set (a macOS TCC hang workaround that this repo already paid for once, in the June 2026 worker wedge). It then falls through to `Path(__file__).parent.parent / "config" / "reflections.yaml"` — which its docstring at line 75 calls *"in-repo fallback, always present"*. That is false. `config/reflections.yaml` is gitignored (`.gitignore:8`) and is materialized only in the primary checkout, by `install_reflection_worker.sh` / `install_email_bridge.sh` / `scripts/update/env_sync.py::sync_reflections_yaml`. **No worktree ever has it.** The final `return` is the one branch with no `exists()` check, so the resolver hands back a path that is not there.

The two stack: SDLC lane work and the nightly regression detector both run in `.worktrees/{slug}/` with `VALOR_LAUNCHD=1` inherited from the launchd-managed worker. There, ten registry-reading assertions raise `FileNotFoundError` on a correctly-absent path — which reads like infrastructure noise, and buries Defect 1 underneath it.

**Current behavior:**

- In a worktree under `VALOR_LAUNCHD=1`: 9 call sites in `tests/unit/test_reflection_scheduler.py` and 1 in `tests/unit/test_plan_migration_invariant.py:78` resolve to a nonexistent path and error. The nightly detector reports a path error instead of the contract error it was written to catch.
- `load_registry()` compounds this by failing open — `return []` at both `agent/reflection_scheduler.py:230-241` (the `~/Desktop` realpath guard) and `243-245` (missing path). So `TestRegistryLoading::test_load_registry_returns_only_enabled` iterates an empty list and **passes while asserting nothing**, and any production process inheriting `VALOR_LAUNCHD=1` from a worktree silently schedules **zero reflections** behind a single WARNING.
- In the primary checkout, Defect 1 is plainly red: `TestRegistryIntegrity` → `2 failed, 3 passed`.

**Desired outcome:**

- The registry resolves from *any* checkout — worktree or primary — without weakening the launchd TCC guard.
- The required-fields test accepts every schedule shape the loader accepts, and still fails loudly on an entry with no schedule at all.
- When the resolver genuinely cannot find a registry anywhere, that surfaces as one legible error rather than ten `FileNotFoundError`s.

## Freshness Check

**Baseline commit:** `a9016cbdc`
**Issue filed at:** 2026-08-12T20:19:05Z
**Disposition:** Minor drift — one finding materially revises an acceptance criterion.

**File:line references re-verified:**

| Reference | Issue's claim | Status |
|---|---|---|
| `agent/reflection_scheduler.py:69-95` | `_resolve_registry_path`, skips vault under `VALOR_LAUNCHD` | Still holds, exact lines |
| `agent/reflection_scheduler.py:75` | Docstring claims "always present" | Still holds, exact line |
| `agent/reflection_scheduler.py:172` | `validate()` requires only `schedule` | Still holds, exact line |
| `agent/reflection_scheduler.py:266-280` | Unified-grammar normalization | Drifted slightly — now `266-283` |
| `agent/reflection_scheduler.py:230-241` | `~/Desktop` realpath guard returns `[]` | Still holds, exact lines |
| `agent/reflection_scheduler.py:243-245` | Missing-path fail-open | Still holds, exact lines |
| `tests/unit/test_reflection_scheduler.py:667` | `assert "every" in entry` | Still holds, exact line |
| `tests/unit/test_reflection_scheduler.py:51-58` | `_entry_interval_seconds` is `every:`-only | Still holds; single call site at line 100, scoped to `pm-briefings` |
| `.gitignore:8` | Excludes `config/reflections.yaml` | Still holds |

**Cited sibling issues/PRs re-checked:**

- **#2810** — OPEN. Registry schedules `expectation-reconciler` against `reflections.expectation_reconciler`, a module that does not exist. **This is the one material drift.** At `a9016cbdc`, `TestRegistryIntegrity::test_all_callables_resolve` fails on the *primary* checkout with `ModuleNotFoundError`, independent of anything in this plan. The issue comment claiming that node "was not independently red" is now stale. Consequence: this work cannot turn `TestRegistryIntegrity` fully green on its own, and the acceptance criteria are written around that.
- **#2708** — OPEN, plan `docs/plans/job-expectations-obligation-primitive.md` is `status: Ready` with no PR. That is the work that would create the missing module. Not a blocker for this plan; tracked under #2810.
- **PR #2721** (merged 2026-08-12) — introduced `sdlc-upvote-pickup`, the `cron:` entry that triggers Defect 1. Confirmed present in both registry copies.

**Commits on main since the issue was filed (touching referenced files):** none touched `agent/reflection_scheduler.py` or `tests/unit/test_reflection_scheduler.py`. Both defects reproduce unchanged.

**Registry state re-measured:** 34 entries (was 33 when filed). Still exactly one uses `cron:` — `sdlc-upvote-pickup`. Vault (`~/Desktop/Valor/reflections.yaml`, 19335 bytes) and primary-checkout config copy (10724 bytes) differ in size but agree on the entry set; the config copy is a **real file**, not a symlink, so the lines 230-241 realpath guard does not block reading it.

**Active plans in `docs/plans/` overlapping this area:** none touching the resolver or this test module. `job-expectations-obligation-primitive.md` is adjacent via #2810 but disjoint in code.

## Prior Art

- **PR #1776** — *refactor(reflections): one file per reflection under `reflections/{group}/` (#1028)* — created the re-export shims that `test_all_callables_resolve` exists to guard. Establishes why that test must read the *live* registry rather than a fixture: it is the only thing standing between a hand-edited, gitignored, unreviewed registry and a reflection that errors in production.
- **June 2026 worker wedge** (documented inline at `agent/reflection_scheduler.py:222-241`) — `config/reflections.yaml` was a symlink into `~/Desktop`, silently defeating the `VALOR_LAUNCHD` guard in `_resolve_registry_path` and freezing the worker's event loop. The response was the defense-in-depth realpath check at 230-241 and a switch from symlink to real-file copy (`scripts/update/env_sync.py::sync_reflections_yaml`). **This is the direct reason open-question option 1 is rejected below.**
- **Issue #2810** — the `expectation-reconciler` unresolvable callable. Same test class, different root cause, filed separately. Explicitly out of scope here.
- **Issue #2439** — prior surgery on this same test class (removed `session-liveness-check` assertions). Confirms the class is actively maintained and the right place to fix the contract.

No prior attempt has been made to fix either defect, so there is no "Why Previous Fixes Failed" section.

## Research

No relevant external findings — this is entirely internal: a git worktree layout question and a test contract, both resolved by direct measurement against this repo rather than by documentation lookup. The one externally-defined fact used (git's worktree `.git`-file / `commondir` layout) was verified empirically in spike-1 rather than taken from docs.

## Spike Results

### spike-1: Can a worktree recover its primary checkout root without a subprocess?
- **Assumption**: "A worktree can deterministically locate the primary checkout that owns it."
- **Method**: code-read + direct measurement in `.worktrees/ledger-integrity`
- **Finding**: Two independent routes, both confirmed:
  - `git rev-parse --git-common-dir` → `/Users/valorengels/src/ai/.git`; parent is the primary root. Canonical, handles every layout, but costs a subprocess.
  - The worktree's `.git` is a **file** containing `gitdir: /Users/valorengels/src/ai/.git/worktrees/ledger-integrity`, and that directory holds a `commondir` file containing `../..`. Resolving `gitdir/commondir` yields the primary `.git`; its parent is the primary root. Pure filesystem, no subprocess, no `PATH` dependency.
  - In the primary checkout, `.git` is a directory, so the worktree branch is trivially skipped.
- **Confidence**: high
- **Impact on plan**: Makes option 2 concretely implementable. The plan specifies the filesystem route as primary because `_resolve_registry_path` runs at module import (`REGISTRY_PATH` at line 98) inside a latency-sensitive launchd worker, and a subprocess on that path is exactly the class of hazard this module has already been burned by.

### spike-2: Is Defect 1 confined to one assertion?
- **Assumption**: "Sibling tests carry the same `every:`-only assumption, so a one-line fix is insufficient."
- **Method**: code-read + full-module run against the live registry
- **Finding**: **Assumption is false.** `_entry_interval_seconds` (lines 51-58) is `every:`-only but has exactly one call site (line 100), scoped by name to `entries["pm-briefings"]` (`every: 300s`). It never iterates the registry, so a `cron:` entry cannot reach it. Defect 1 is confined to the assertion at line 667.
- **Confidence**: high
- **Impact on plan**: The issue's "audit other `every:`-only assumptions" acceptance criterion is closed as *audited, none found*. No task is needed for it.

### spike-3: Does the `~/Desktop` realpath guard block reading the primary checkout's copy?
- **Assumption**: "Resolving worktrees to the primary `config/reflections.yaml` will just trip the other launchd guard."
- **Method**: code-read + `os.path.realpath`
- **Finding**: **No.** `/Users/valorengels/src/ai/config/reflections.yaml` is a real file whose `realpath` is itself — it does not resolve under `~/Desktop`. The guard at lines 230-241 passes it. (Had it still been the pre-June-2026 symlink, option 2 would have been dead on arrival; `sync_reflections_yaml` guarantees the real-file form.)
- **Confidence**: high
- **Impact on plan**: Confirms option 2 works end-to-end under `VALOR_LAUNCHD=1`, and adds a Risk entry — the fix is silently coupled to the copy staying a real file.

### spike-4: Is `test_all_callables_resolve` red for reasons outside this plan?
- **Assumption**: "Fixing both defects makes `TestRegistryIntegrity` green."
- **Method**: prototype — ran the class on the primary checkout at `a9016cbdc`
- **Finding**: **Assumption is false.** `2 failed, 3 passed`. The second failure is #2810's `ModuleNotFoundError: No module named 'reflections.expectation_reconciler'`, unrelated to either defect here.
- **Confidence**: high
- **Impact on plan**: Rewrites the success criteria. This plan targets `4 passed, 1 failed` with the sole remaining failure being #2810's, and adds a Verification row that pins the failure to that exact cause so a *different* regression cannot hide behind the carve-out.

## Data Flow

1. **Entry point**: any process importing `agent.reflection_scheduler` — the reflection worker (`python -m reflections`), the dashboard, `scripts/update/reflection_register.py`, or pytest.
2. **`_resolve_registry_path()`** (lines 69-95): `REFLECTIONS_YAML` env override → vault (skipped iff `VALOR_LAUNCHD`) → in-repo `config/reflections.yaml` derived from `__file__`. **This is where a worktree derails**: `__file__` points into the worktree, so the derived path names a file that only ever exists in the primary checkout.
3. **`REGISTRY_PATH`** (line 98): the result is frozen at import time into a module-level constant.
4. **`load_registry()`** (lines 220-245): re-resolves, applies the `~/Desktop` realpath guard, then fails open with `[]` if the path is missing.
5. **Output, production**: `ReflectionScheduler` ticks over an empty entry list. Zero reflections scheduled, one WARNING logged, no crash.
6. **Output, tests**: the 10 test call sites bypass `load_registry()` and `open()` the resolved path directly — so they get a raw `FileNotFoundError` rather than the fail-open `[]`.

The fix lands at step 2, which is the single point every downstream consumer funnels through.

## Architectural Impact

- **New dependencies**: none. Standard-library `pathlib` reads of git's on-disk worktree metadata; no `git` subprocess, no new package.
- **Interface changes**: `_resolve_registry_path()` keeps its signature and return type. Its contract tightens in one way — it logs an error when it exhausts every candidate, instead of returning a nonexistent path silently.
- **Coupling**: adds a narrow, read-only coupling from the scheduler to git's worktree metadata layout. Contained in one helper, and degrades to today's behavior on any parse failure.
- **Data ownership**: unchanged. The vault remains canonical; `config/reflections.yaml` remains the launchd-safe install-time copy. Worktrees gain **read** access to the primary checkout's copy and nothing more.
- **Reversibility**: high — one helper function and one added candidate step, revertible in a single commit.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the design decision is made in this plan, with evidence)
- Review rounds: 1

Two files change. The cost here is in getting the decision right and in proving the fix under an environment that is awkward to reproduce, not in volume of code.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Reflections registry present on this machine | `test -f ~/Desktop/Valor/reflections.yaml -o -f config/reflections.yaml` | The tests read the live registry; without either copy there is nothing to assert against |
| A registered git worktree to verify against | `python3 -c "import subprocess,sys; out=subprocess.run(['git','worktree','list'],capture_output=True,text=True).stdout; sys.exit(0 if '.worktrees/' in out else 1)"` | Defect 2 only reproduces in a worktree |

## Solution

### Key Elements

- **Primary-checkout fallback in the resolver** — when this checkout's `config/reflections.yaml` is absent, locate the checkout that owns this one and read its copy. Fixes production and all ten test call sites at a single point.
- **Exhausted-candidates diagnostic, emitted once** — the resolver stops returning a nonexistent path in silence. It logs one legible error naming every candidate it tried, de-duplicated per candidate set so ten call sites do not produce ten copies.
- **A single test-side failure for a missing registry** — `_registry_path()` becomes a module-scoped fixture that `pytest.fail`s once with the candidate list instead of letting ten call sites raise `FileNotFoundError`.
- **Schedule-shape assertion covering every shape the loader accepts** — the required-fields test requires *exactly one* of `every` / `cron` / `at` / `schedule`. Accepting all four shapes fixes Defect 1; rejecting two-or-more is an intentional lint that is **stricter** than `agent/reflection_scheduler.py:266-283`, which resolves multi-key entries by precedence rather than rejecting them.
- **De-vacuuming the enabled-entries test** — `test_load_registry_returns_only_enabled` asserts a non-empty registry before iterating, so it can never again pass by asserting nothing.
- **Truthful docstring** — line 75 stops claiming the in-repo copy is "always present".

### Flow

Process imports `agent.reflection_scheduler` → `_resolve_registry_path()` → `REFLECTIONS_YAML` set and exists? → **use it** → else `VALOR_LAUNCHD` unset and vault exists? → **use vault** → else this checkout's `config/reflections.yaml` exists? → **use it** → else this checkout is a git worktree? → owning checkout's `config/reflections.yaml` exists? → **use it** → else **log one error naming every candidate tried (once per candidate set, via `_exhausted_warned`), return the local in-repo path** (unchanged legacy return, so no new exception type escapes into production). On the test side the `_registry_path` fixture converts that missing path into a single `pytest.fail` naming the same candidates, instead of ten `FileNotFoundError`s.

### Technical Approach

**Decision on Defect 2: option 2 — resolve the in-repo path against the primary checkout.** The issue left this open with three candidates. Taking them in turn:

- **Option 1 (fall back to the vault even under `VALOR_LAUNCHD`) — rejected.** It cannot be done in `_resolve_registry_path` alone: `load_registry()` carries an independent realpath guard at lines 230-241 that refuses *any* path resolving under `~/Desktop` while `VALOR_LAUNCHD` is set, so the resolver change would be neutralized one function later and both would have to be unwound together. Unwinding them means betting that the macOS TCC `open()` hang does not reproduce for short-lived non-daemon children — a bet this repo already lost once, at the cost of a wedged worker event loop. The prize for winning it is a fallback we do not need, because option 2 reaches a real file without going near `~/Desktop`.
- **Option 3 (set `REFLECTIONS_YAML` in the tests) — rejected as the primary fix.** It is the established pattern for tests that construct their *own* fixture registry (`tests/unit/test_reflection_arm.py`, `test_reflection_register.py`), but that is a different job from these ten sites, which must read the **live** registry. It also leaves the production hole entirely open: a worktree process under launchd still schedules zero reflections silently. And it scales badly — ten edits now, plus one more for every future site, each of which fails open if forgotten. Comment 5 on the issue puts the decisive argument well: turning `FileNotFoundError` into a skip or a fixture preserves the detection outage, and `test_all_callables_resolve` is the only guard between an unreviewed registry and a reflection that errors every 30 minutes in production.
- **Option 2 — selected.** One change at the single point every consumer funnels through. It fixes the production hole and all ten test sites together, it keeps the tests reading the live registry (preserving the guard's whole purpose), and it never touches `~/Desktop`, so the TCC hazard is sidestepped rather than gambled against.

**Mechanism.** Add a helper that answers "which checkout owns this one?" using git's on-disk worktree metadata, per spike-1:

- If `<repo_root>/.git` is a **directory**, this is the primary checkout — there is nothing to do.
- If it is a **file**, read its single `gitdir: <path>` line. **Both branches below must converge on one variable, `root`, holding the owning checkout's *root* — not its `.git`.** The two branches are asymmetric and applying a uniform `.parent` to both is a silent wrong answer (it lands on `/Users/valorengels/src`, whose `config/reflections.yaml` does not exist, so the resolver falls through to the legacy path with no exception):
  - **`commondir` present**: `common = (gitdir / Path(commondir_text.strip())).resolve()` yields the primary `.git`; `root = common.parent`.
  - **`commondir` missing**: `root = Path(gitdir).parents[2]` — this already *is* the checkout root for git's fixed `<primary>/.git/worktrees/<name>` layout (verified verbatim against `.worktrees/ledger-integrity/.git` → `/Users/valorengels/src/ai/.git/worktrees/ledger-integrity`, whose `parents[2]` is `/Users/valorengels/src/ai`). **No `.parent` in this branch.**
  - Guard the fallback with `len(Path(gitdir).parents) > 2` before indexing. A malformed short `gitdir:` otherwise raises `IndexError`, which the broad `except` would swallow into the same silent fallback the guard exists to make legible.
  - Do the `exists()` check once, on `root / "config" / "reflections.yaml"`, after the branches converge.
- Wrap the whole helper in a broad `except Exception` that returns `None`. A malformed or unfamiliar git layout must degrade to today's behavior, never raise out of a module-level import.

Deliberately **not** shelling out to the git CLI: `REGISTRY_PATH` is computed at import time (line 98) in a launchd worker, and putting a subprocess on that path reintroduces exactly the shape of hazard — a blocking call during scheduler startup — that the surrounding code exists to prevent. The rationale comment in the code must state this **without reproducing the `git rev-parse` token verbatim**, so the anti-criterion that guards against a subprocess is not tripped by prose about not using one (see Verification, and the Documentation task).

**Exhausted-candidates diagnostic must not multiply.** Each of the ten call sites calls `_resolve_registry_path()` afresh, so a naive `logger.error` on the exhausted path fires ten times per run — strictly noisier than today's silence, which is the opposite of desired outcome 3. De-duplicate with a module-level `_exhausted_warned: set[str]` keyed on the **candidate tuple** (joined paths), not `lru_cache` and not a bare bool:
- `lru_cache` on `_resolve_registry_path` is wrong: `load_registry()` re-resolves per call and tests mutate `REFLECTIONS_YAML`, so the function must stay uncached.
- A bare bool is wrong: a second, *different* bad candidate set would find the flag already set and silently suppress its own diagnostic.

**Delivering "one legible error" on the test side.** The dedup above fixes the log. The ten `FileNotFoundError`s are a separate surface: they come from call sites that `open()` the resolved path directly. Convert `_registry_path()` (`tests/unit/test_reflection_scheduler.py:40`) into a module-scoped fixture that, when the resolved path is absent, calls `pytest.fail(...)` once with the candidate list. **`fail`, not `skip`** — the entire argument against Option 3 turns on not preserving the detection outage, and a skip would preserve it.

**Answering the issue's second open question — "should the nightly detector run with `VALOR_LAUNCHD` unset?" No.** That env var is inherited from the launchd worker and is a faithful part of the environment the code must work in. Unsetting it for the detector would make the test environment diverge from production and would paper over the resolver bug rather than fix it. The detector keeps inheriting it; the resolver stops being broken under it.

**Defect 1.** Replace the single `assert "every" in entry` at line 667 with an exactly-one-of check over `every` / `cron` / `at` / `schedule`. Zero keys must fail (missing schedule). Two or more must also fail — but **be precise about what that is**: it is a *lint, deliberately stricter than the loader*, not a mirror of it. The normalizer at lines 266-283 is not ambiguous about multi-key entries; it applies a deterministic precedence (`schedule` > `every` > `cron` > `at`) and would load such an entry by silently picking the winner. Adopting the strict rule anyway is safe and cheap: verified against both live copies at `a9016cbdc`, zero entries carry more than one of the four keys and zero carry `cron_tz` without `cron`, so the rule is green today. Encode the divergence where a future reader will hit it — in the failure text, not only a comment:

```python
f"Entry {name} declares {keys!r}; the loader (agent/reflection_scheduler.py:266-283) "
f"would silently pick by precedence schedule>every>cron>at. Declare exactly one."
```

The inline comment must say the check is **stricter than the loader by policy**, never that it "mirrors the loader" — so a future loader change that formalizes multi-key support reads as intended divergence rather than as a test bug. Also assert that `cron_tz` appears only alongside `cron`, since it is meaningless otherwise. Do **not** add an `every:` to the `sdlc-upvote-pickup` registry entry — the entry is valid and the test is what is wrong.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new primary-checkout helper's `except Exception: return None` must have a test asserting observable fallback behavior — feed it a malformed `.git` file (garbage content, no `gitdir:` line) and assert the resolver returns the legacy in-repo path rather than raising.
- [ ] `load_registry()`'s two existing fail-open `return []` paths (lines 230-241, 243-245) are already covered by `test_load_registry_handles_missing_file` (line 148); no change needed, but confirm they still log at their current levels.

### Empty/Invalid Input Handling
- [ ] `.git` file present but empty → helper returns `None`, resolver falls back. Test it.
- [ ] `.git` file with a `gitdir:` pointing at a nonexistent directory → helper returns `None` (no `commondir`, `parents[2]` yields a path whose `config/reflections.yaml` does not exist), resolver falls back. Test it.
- [ ] `.git` file with a **short** `gitdir:` (fewer than three parents, e.g. `gitdir: /a`) → the `len(parents) > 2` guard returns `None` rather than letting `IndexError` reach the broad `except`. Test it.
- [ ] Helper return-shape test: for a synthesized worktree layout, assert the helper returns a directory that **contains** a `.git` entry — i.e. the checkout root, not the `.git` directory itself. This is the anti-regression for the two branches diverging on `.parent`.
- [ ] No registry anywhere (no `REFLECTIONS_YAML`, no vault, no local copy, no primary copy) → resolver logs one error naming every candidate and returns the legacy path. Test the log, not just the return.
- [ ] Calling `_resolve_registry_path()` **twice** with the same exhausted candidate set logs exactly one error (`_exhausted_warned` dedup), while a *different* exhausted candidate set still logs its own. Assert both via `caplog` record counts.
- [ ] Registry entry with **no** schedule key at all → required-fields test still fails. This is the anti-regression for Defect 1's fix; without it, "widen the assertion" could degrade to "delete the assertion".

### Error State Rendering
- [ ] The exhausted-candidates error must name each path tried, so an operator reading `logs/` can tell "no vault, no local copy, no primary copy" apart from "wrong path". Assert on the message content via `caplog`, not merely that something was logged.

## Test Impact

- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_all_entries_have_required_fields` — UPDATE: replace `assert "every" in entry` (line 667) with an exactly-one-of `every`/`cron`/`at`/`schedule` check plus the `cron_tz`-implies-`cron` check. This is the node named in the issue.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading::test_load_registry_returns_only_enabled` — UPDATE: assert `entries` is non-empty before the loop. Today it passes vacuously in a worktree; after the Defect 2 fix it would pass legitimately, and the added assertion is what prevents it from silently regressing to vacuous if resolution breaks again.
- [ ] `tests/unit/test_reflection_scheduler.py::_registry_path` (line 40) — REPLACE: convert the module-level helper into a module-scoped fixture that `pytest.fail`s once, naming every candidate, when the resolved path is absent. Not `skip` — a skip would preserve exactly the detection outage the Option 3 rejection turns on. All nine in-module call sites take the fixture instead of calling the helper.
- [ ] `tests/unit/test_reflection_scheduler.py` — NEW: unit tests for the primary-checkout helper (worktree hit, primary-checkout no-op, malformed `.git`, empty `.git`, dangling `gitdir:`, short `gitdir:` guard, return-shape is the root not the `.git`) and for the exhausted-candidates log including its per-candidate-set dedup.
- [ ] `tests/unit/test_reflection_scheduler.py` — NEW: a required-fields negative test — an entry dict with no schedule key must be rejected — asserted against the extracted predicate, not the live registry (the live registry has no such entry and must not gain one).
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::{test_registry_yaml_valid,test_no_duplicate_names,test_expected_reflections_present}` — NO CHANGE, but they are the observable proof of the Defect 2 fix: they go from `FileNotFoundError` to passing in a worktree with no edit to their bodies.
- [ ] `tests/unit/test_plan_migration_invariant.py::test_reflections_yaml_registers_merged_branch_cleanup` (line 78) — NO CHANGE: it calls `_resolve_registry_path()` directly and is fixed for free. It is the tenth call site and lives outside the fixture's module, so in the genuinely-exhausted case it still raises `FileNotFoundError` — one, in one file, which is the legible-error bar. Cross-module fixture sharing via `conftest.py` is deliberately not done: it would put a reflections-specific fixture in the unit-test root for one caller. Must be included in the worktree verification run.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_all_callables_resolve` — NO CHANGE: red for #2810, not for anything here. Explicitly excluded from the green target and pinned by a Verification row.
- [ ] `tests/unit/test_reflection_arm.py`, `tests/unit/test_reflection_register.py`, `tests/unit/test_reflections_main.py` — NO CHANGE: they set `REFLECTIONS_YAML` explicitly, which short-circuits ahead of every branch this plan touches. Listed because they are the largest block of registry-adjacent tests and their non-involvement should be stated rather than assumed.

## Rabbit Holes

- **Re-litigating the launchd TCC guard.** Option 1 makes it tempting to "just test whether the hang still reproduces". Proving a negative about macOS TCC behavior across OS versions is open-ended, and option 2 makes the answer irrelevant. Leave lines 86-90 and 230-241 exactly as they are.
- **Making `config/reflections.yaml` available in worktrees.** Copying or symlinking the registry into each worktree at creation time (`agent/worktree_manager.py`) looks like a tidier fix. It is not: it multiplies copies of a hand-edited private file, they go stale independently, and a symlink into the vault is precisely the June 2026 wedge. Read the one authoritative copy instead.
- **Fixing #2810 while in here.** `test_all_callables_resolve` will be red at the end of this work. It is a registry-content bug awaiting #2708's module and touches nothing in this plan. Resist.
- **Un-failing-open `load_registry()`.** Making a missing registry raise instead of returning `[]` is defensible and is *not* this issue. It would change worker startup behavior on every machine, deserves its own blast-radius analysis, and would obscure whether the resolver fix worked.
- **Generalizing to a repo-wide "find my primary checkout" utility.** One caller needs this. A shared helper in `tools/` invites callers whose failure modes have not been thought through. Keep it private to the scheduler until a second real caller exists.
- **Rewriting `_entry_interval_seconds` to handle `cron:`.** Spike-2 settled this: one call site, scoped to a named `every:` entry. Generalizing it is speculative work with no caller.

## Risks

### Risk 1: Worktree tests now depend on the primary checkout's install-time copy
**Impact:** The worktree fix reads `<primary>/config/reflections.yaml`. On a machine that has never run `/update` or an installer, that file does not exist and worktree tests stay red — with a better error, but still red.
**Mitigation:** The vault branch still covers every non-launchd context, which is the common developer case. The exhausted-candidates error names all three candidates so the remedy (`/update`) is obvious from the log line. The Prerequisites table makes the dependency explicit rather than implicit.

### Risk 2: The primary copy could revert to a symlink and silently re-trip the realpath guard
**Impact:** If `config/reflections.yaml` ever becomes a symlink into `~/Desktop` again, `load_registry()`'s guard at lines 230-241 rejects it under launchd and reflections go silent — the June 2026 wedge, one layer removed.
**Mitigation:** `scripts/update/env_sync.py::sync_reflections_yaml` already enforces the real-file form and reports `was symlink or stale`. Note the coupling in the docstring so the next reader knows the resolver depends on it. Out of scope to re-verify the sync itself.

### Risk 3: `scripts/update/reflection_register.py` reuses this resolver as a **write** target
**Impact:** `_resolve_target()` (line 184-192) delegates to `_resolve_registry_path()` to decide where to *append* a registration. After this change, that call from a worktree under launchd would target the primary checkout's `config/reflections.yaml` — an install-time copy that the next `/update` overwrites from the vault, so the registration would be silently lost.
**Mitigation:** Real-world exposure is nil today: `reflection_register` runs as a step in `scripts/update/run.py`, which runs in the primary checkout, and `_this_machine_owns_valor` already fails closed. The change does not create the hazard — today the same call targets a *nonexistent* worktree path, which is equally lost.

The docstring must be **rewritten, not appended to**. It currently asserts the opposite invariant as a recorded prior decision: *"so the entry lands where the scheduler will actually look, not in the soon-clobbered config copy (critique C6)"* — and this plan makes that shared resolver able to return precisely that copy. Appending would leave a stale claim sitting beside its own contradiction. The rewrite must name the exact reachability condition: **the new fourth level is reached only when this checkout's `config/reflections.yaml` is absent, which never holds in the primary checkout where `/update` runs** — so `_resolve_target` still lands on the vault there and C6 holds for every real caller today. It must then state that a worktree caller under `VALOR_LAUNCHD=1` would write to the primary checkout's install-time copy and lose the registration at the next `/update`, and cross-reference the `[ORDERED]` No-Go. Leave `_this_machine_owns_valor`'s fail-closed behavior untouched — it is the only thing preventing that path from being exercised.

Splitting the resolver into distinct read and write functions is a real improvement and a separate change (`[ORDERED]` No-Go).

### Risk 4: The #2810 carve-out could mask a genuine new regression
**Impact:** "`test_all_callables_resolve` is expected to fail" is exactly the shape of statement under which a second, unrelated failure hides.
**Mitigation:** The carve-out is pinned, not blanket. A Verification row asserts the failure output contains `expectation_reconciler` — any *other* unresolvable callable fails the check.

## Race Conditions

No race conditions identified. `_resolve_registry_path` is a pure synchronous function over filesystem reads with no shared mutable state; the helper it gains reads two immutable-after-worktree-creation git metadata files. The one ordering property worth naming is pre-existing and unchanged: `REGISTRY_PATH` (line 98) is bound once at import time, so a registry that appears *after* import is not picked up by that constant — every hot path already calls `_resolve_registry_path()` afresh via `load_registry()`, so this affects only the module-level constant, exactly as it does today.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2810] Fixing the `expectation-reconciler` unresolvable callable. It is registry content awaiting #2708's module, not a test contract or a path-resolution defect, and shares no code with this plan. `test_all_callables_resolve` therefore remains red after this work.
- [SEPARATE-SLUG #2810] Removing or disabling the `expectation-reconciler` registry entry to force the class green. Registry content is owned by #2810; editing the shared vault file to make a test pass here would hide a live production error that fires every 30 minutes.
- [ORDERED] Splitting `_resolve_registry_path` into separate read and write resolvers (Risk 3). The write path's only caller runs inside `/update` in the primary checkout, so the change must be sequenced against an `/update` cycle on every machine to be verified; doing it here would put an unverifiable change on the critical path of a test fix. This plan documents the asymmetry instead.

## Update System

No update system changes required. The fix is a pure code change in `agent/reflection_scheduler.py` plus test edits — no new dependency, no new config file, no new env var, nothing to propagate. `/update` continues to materialize `config/reflections.yaml` in the primary checkout exactly as it does today; this change makes worktrees *read* that existing artifact rather than requiring a new one.

Worth noting for whoever runs `/update` after this merges: `scripts/update/reflection_register.py` imports the modified resolver (`_resolve_target`, line 190). Its behavior in the primary checkout — where `/update` runs — is unchanged, because the new worktree branch is only reached when the local `config/reflections.yaml` is absent, and in the primary checkout it is present.

## Agent Integration

No agent integration required. This is an internal fix to a module the reflection worker subprocess and the test suite already import; it adds no capability the agent needs to reach. No new CLI entry point in `pyproject.toml [project.scripts]`, no new bridge import, no MCP surface.

The one agent-visible consequence is indirect and is the point of the work: SDLC lane sessions running in `.worktrees/{slug}/` will stop seeing ten spurious `FileNotFoundError`s when they run this test module, so `/do-test` results from a lane become trustworthy for reflections tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` lines 98-110 ("The scheduler resolves `config/reflections.yaml` via a three-level fallback"): document the new fourth level (owning checkout's copy), state explicitly that worktrees have no local copy and why, and correct the stale claim at lines 62 and 102 that `config/reflections.yaml` is a **symlink** — since the June 2026 wedge fix it is a real file copy written by `scripts/update/env_sync.py::sync_reflections_yaml`, which the new fallback depends on (Risk 2).
- [ ] Update `docs/features/worktree-manager.md` with a short note that gitignored install-time artifacts (`config/reflections.yaml` being the worked example) are absent in worktrees, and that the resolution strategy is to read the owning checkout's copy rather than to duplicate the file per worktree.
- [ ] No new entry in `docs/features/README.md` — this modifies existing documented behavior rather than adding a feature.

### Inline Documentation
- [ ] Rewrite the `_resolve_registry_path` docstring (`agent/reflection_scheduler.py:71-77`): remove the false "always present" claim at line 75, enumerate all four resolution levels, and state that a worktree has no local copy.
- [ ] Comment the new primary-checkout helper with the git layout it relies on (`.git` file → `gitdir:` → `commondir`), the asymmetry between the two branches (only the `commondir` branch takes `.parent`), and why the helper reads those files rather than invoking the git CLI: `REGISTRY_PATH` is computed at import time inside a launchd worker, and a blocking subprocess there is the hazard the surrounding code exists to prevent. **Phrase the rationale without reproducing the literal `git rev-parse` token** — say "resolved from git's on-disk worktree metadata rather than by invoking the git CLI". The Verification anti-criterion detects the actual hazard (a `subprocess` import), so the paraphrase is defence in depth, not the mechanism.
- [ ] **Rewrite** (do not append to) `scripts/update/reflection_register.py::_resolve_target`'s docstring per Risk 3: state the reachability condition that keeps critique decision C6 true for every real caller, the worktree-under-launchd hazard, and the `[ORDERED]` No-Go cross-reference. The existing "not in the soon-clobbered config copy (critique C6)" sentence must not survive unqualified.
- [ ] Comment the widened required-fields assertion with a pointer to `agent/reflection_scheduler.py:266-283`, stating that the exactly-one-of rule is **stricter than the loader by policy** (the loader resolves multi-key entries by precedence rather than rejecting them), so the next grammar change updates both with the divergence understood. Do not write that it "mirrors the loader".

## Success Criteria

- [ ] From a `.worktrees/{slug}` checkout with `VALOR_LAUNCHD=1` set, `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity` reports **4 passed, 1 failed**, the sole failure being `test_all_callables_resolve` with `expectation_reconciler` in its message (#2810).
- [ ] The same class, same worktree, with `VALOR_LAUNCHD` unset: identical result — 4 passed, 1 failed on the same node for the same reason. **Note that the two runs read different files** (config copy vs vault). They are expected to agree only on the projection these tests read — entry names plus `every`/`cron`/`at`/`schedule`/`callable` — which is asserted by its own Verification row. Content outside that projection legitimately differs (measured at `a9016cbdc`: `tech-debt-scan`, `skills-audit`, `hooks-audit`, `docs-auditor`), so a `DRIFT` verdict on that row is the explanation for a criterion-2 failure, not a regression in this work.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading` passes fully from a worktree with `VALOR_LAUNCHD=1`, including `test_load_registry_returns_only_enabled` now asserting a non-empty registry.
- [ ] `tests/unit/test_plan_migration_invariant.py` passes from a worktree with `VALOR_LAUNCHD=1` (fixed for free by the resolver change).
- [ ] The live `cron:` entry `sdlc-upvote-pickup` passes `test_all_entries_have_required_fields`, and the registry is **unmodified** — no `every:` bolted onto it.
- [ ] A synthetic entry with no schedule key is rejected by the required-fields predicate, and a synthetic entry with both `every:` and `cron:` is rejected as ambiguous.
- [ ] `_resolve_registry_path` no longer claims "always present" and logs **exactly one** error naming every candidate when it exhausts them, however many call sites re-resolve within the process.
- [ ] With no registry reachable anywhere, `tests/unit/test_reflection_scheduler.py` reports a single `pytest.fail` from the `_registry_path` fixture naming the candidates, not nine `FileNotFoundError`s. (`test_plan_migration_invariant.py` contributes at most one more, in a different file — see Test Impact.)
- [ ] Full `tests/unit/test_reflection_scheduler.py` module passes from the primary checkout except the #2810 node.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (resolver)**
  - Name: `resolver-builder`
  - Role: The Defect 2 fix in `agent/reflection_scheduler.py` — primary-checkout helper, fourth resolution level, exhausted-candidates diagnostic, docstring correction.
  - Agent Type: builder
  - Resume: true

- **Builder (test contract)**
  - Name: `contract-builder`
  - Role: Sole owner of `tests/unit/test_reflection_scheduler.py` — the Defect 1 contract fix, the `_registry_path` fixture conversion, and every resolver failure-path test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (worktree environment)**
  - Name: `worktree-validator`
  - Role: Reproduce the original failures and verify the fix under the real nightly environment — worktree cwd, `VALOR_LAUNCHD=1`.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `reflections-documentarian`
  - Role: The Documentation section tasks.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Capture the red baseline
- **Task ID**: capture-baseline
- **Depends On**: none
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- From a `.worktrees/{slug}` checkout with `VALOR_LAUNCHD=1`, run `tests/unit/test_reflection_scheduler.py` and `tests/unit/test_plan_migration_invariant.py`; record the exact `FileNotFoundError` set.
- From the primary checkout, run `TestRegistryIntegrity`; record the 2-failed/3-passed split and both failure messages verbatim.
- **Capture the registry fingerprint** so the "registry untouched" anti-criterion has something to compare against (a `git diff` cannot serve: the file is gitignored and untracked): `shasum -a 256 config/reflections.yaml ~/Desktop/Valor/reflections.yaml > /tmp/2734-registry-baseline.txt`. Run from the primary checkout — `config/reflections.yaml` is absent in a worktree by premise.
- This is the red-state proof the PR description must carry. Demonstrated-red before green: a passing suite afterwards proves nothing without it.

### 2. Fix the resolver (Defect 2)
- **Task ID**: build-resolver
- **Depends On**: capture-baseline
- **Validates**: tests/unit/test_reflection_scheduler.py, tests/unit/test_plan_migration_invariant.py
- **Informed By**: spike-1 (`.git` file → `gitdir:` → `commondir`, no subprocess), spike-3 (primary copy is a real file, realpath guard passes it)
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add the private owning-checkout helper described in Technical Approach, returning `None` on any failure.
- Insert the fourth resolution level after the existing in-repo candidate; do not reorder or weaken the `REFLECTIONS_YAML`, vault, or `VALOR_LAUNCHD` branches.
- Log one error naming every candidate when all are exhausted; keep returning the legacy in-repo path so no new exception escapes at import.
- Rewrite the docstring: four levels, no "always present", worktrees have no local copy.
- Leave `agent/reflection_scheduler.py:230-241` and `86-90` untouched.

### 3. Fix the test contract (Defect 1) and add the resolver failure-path tests
- **Task ID**: build-contract
- **Depends On**: capture-baseline, build-resolver
- **Validates**: tests/unit/test_reflection_scheduler.py
- **Informed By**: spike-2 (blast radius is exactly one assertion; `_entry_interval_seconds` is not implicated)
- **Assigned To**: `contract-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- One task, not two: every bullet here writes `tests/unit/test_reflection_scheduler.py`, and splitting it across two scheduling nodes gained no isolation while inviting a same-file conflict.
- Extract the required-fields check into a module-level predicate so it can be unit-tested against synthetic entries without touching the live registry.
- Require exactly one of `every` / `cron` / `at` / `schedule`; reject zero (missing) and reject two or more. The two-or-more rule is an intentional lint stricter than the loader — the failure text must name the loader's precedence and the inline comment must say "stricter than the loader by policy". Require `cron_tz` only alongside `cron`.
- Add negative tests: no-schedule entry rejected; `every`+`cron` entry rejected; a `cron`-only entry with `cron_tz` accepted.
- Add the non-empty assertion to `test_load_registry_returns_only_enabled`.
- Convert `_registry_path()` (line 40) into a module-scoped fixture that `pytest.fail`s once with the candidate list when the resolved path is absent; repoint all nine in-module call sites at it. `fail`, never `skip`.
- Cover every bullet in Failure Path Test Strategy: malformed `.git`, empty `.git`, dangling `gitdir:`, short `gitdir:` guard, helper-returns-the-root-not-the-`.git`, primary-checkout no-op, worktree hit, the exhausted-candidates log asserted via `caplog` on message content, and its per-candidate-set dedup.
- Use `tmp_path` to synthesize the git layouts. Do not create real worktrees in tests.
- Do not modify `config/reflections.yaml` or the vault registry.

### 4. Verify under the real nightly environment
- **Task ID**: validate-worktree
- **Depends On**: build-resolver, build-contract
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run every command from `capture-baseline` and diff against the recorded red state.
- Confirm both `VALOR_LAUNCHD=1` and unset produce identical results (Success Criteria 1 and 2).
- Confirm the sole remaining failure is `test_all_callables_resolve` and that its message names `expectation_reconciler` — any other cause is a regression, not the carve-out.
- Confirm both registry copies are unmodified with `shasum -a 256 -c /tmp/2734-registry-baseline.txt` (two `OK` lines). `git status` / `git diff` cannot serve here — `config/reflections.yaml` is gitignored and untracked.
- Run the copies-agree-on-projection check from the primary checkout, so a `MATCH`/`DRIFT` verdict accompanies the Success Criteria 1-vs-2 comparison.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-worktree
- **Assigned To**: `reflections-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section tasks, including the stale-symlink correction in `docs/features/reflections.md`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification table row and confirm each expected result.
- Confirm all Success Criteria boxes.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Registry integrity green except #2810 | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity" -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `1 failed, 4 passed` |
| The one failure is #2810, not something else | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity" -p no:randomly -n0 -q 2>&1 \| grep -c "expectation_reconciler"` | output > 0 |
| No FileNotFoundError anywhere in the module | `./scripts/pytest-clean.sh tests/unit/test_reflection_scheduler.py -p no:randomly -n0 -q 2>&1 \| grep -c "FileNotFoundError"` | match count == 0 |
| Registry loading tests fully green | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryLoading" -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `passed` |
| Plan-migration invariant fixed for free | `./scripts/pytest-clean.sh tests/unit/test_plan_migration_invariant.py -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `passed` |
| Docstring no longer claims "always present" | `grep -c "always present" agent/reflection_scheduler.py` | match count == 0 |
| Anti-criterion: registry not edited to add `every:` to the cron entry (resolver-routed so it works from either checkout; run post-fix, since resolution is the thing under repair) | `python3 -c "import yaml;from agent.reflection_scheduler import _resolve_registry_path as p;d=yaml.safe_load(open(p()));print(sum(1 for r in d['reflections'] if r.get('name')=='sdlc-upvote-pickup' and 'every' in r))"` | output contains `0` |
| Anti-criterion: neither registry copy modified at all (replaces the `git diff` row — `config/reflections.yaml` is gitignored and untracked, so `git diff` prints `0` whether or not it was edited, asserting nothing). Baseline captured in task 1. | `shasum -a 256 -c /tmp/2734-registry-baseline.txt` | two lines, both ending `OK` |
| The two registry copies agree on the projection the tests read (guards Success Criteria 2 against sync drift; **not** a byte or full-YAML diff — the copies legitimately differ elsewhere). Primary checkout only. | `python3 -c "import yaml;p=lambda f:sorted((r['name'],r.get('every'),r.get('cron'),r.get('at'),r.get('schedule'),r.get('callable')) for r in yaml.safe_load(open(f))['reflections']);print('MATCH' if p('config/reflections.yaml')==p('/Users/valorengels/Desktop/Valor/reflections.yaml') else 'DRIFT')"` | output contains `MATCH` |
| Anti-criterion: launchd TCC guards not weakened | `grep -c "VALOR_LAUNCHD" agent/reflection_scheduler.py` | output > 2 |
| Anti-criterion: no import-time git subprocess in the resolver (detects the hazard itself, not prose about it — a string match on `git rev-parse` would be tripped by the mandated rationale comment) | `grep -cE "^\s*(import subprocess\|from subprocess)" agent/reflection_scheduler.py` | match count == 0 |
| Exhausted-candidates diagnostic does not multiply across call sites | `./scripts/pytest-clean.sh tests/unit/test_reflection_scheduler.py -p no:randomly -n0 -q -k "exhausted" 2>&1 \| tail -3` | output contains `passed` |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Success Criterion 8 promises that with no registry reachable the module 'reports a single `pytest.fail` from the `_registry_path` fixture naming the candidates, not nine `FileNotFoundError`s'. The chosen mechanism cannot produce that. pytest caches a module-scoped fixture's exception and re-raises it per dependent test: measured with `.venv/bin/python -m pytest`, a 3-test module against one failing module-scoped fixture reports `3 errors`, each rendering the full message. With 7 dependent tests the validator sees 7 errors, reported as ERROR (setup), not FAILED. Task 6 mandates 'Confirm all Success Criteria boxes', so this box is unconfirmable and `validate-all` cannot go green — the same shape as the round-1 BLOCKER. | pending | Reword the criterion to describe one distinct *cause*, not one report line: 'every registry-reading test in the module errors at `_registry_path` fixture setup with the same message naming all four candidates, and `grep -c FileNotFoundError` over the run output is 0.' Note in Test Impact that pytest reports fixture-setup failures as ERROR, not FAILED, so the validator's expected string changes. If a literally-single line is required, the only mechanism that delivers it is a session-scoped `pytest_collection_modifyitems` / `pytest.exit` hook — heavier than this appetite, so make it an explicit No-Go rather than an implied promise. |
| BLOCKER | History & Consistency | Two Verification rows cannot execute as written, one of them recreating the round-1 BLOCKER inside its own replacement. (1) `grep -cE "^\s*(import subprocess\|from subprocess)"` returns **0** against a probe file containing both `import subprocess` and `from subprocess import run`; unescaped it returns **2**. ERE treats `\|` as a literal pipe, so the alternation never fires — and the escaping is mandatory inside a markdown table, so a copy-pasted row is an anti-criterion that can never fail. (2) `python3 -c "import yaml;from agent.reflection_scheduler import ..."` dies with `ModuleNotFoundError: No module named 'popoto'` — `python3` here is homebrew 3.12, not the repo venv. The row errors instead of printing `0`; `.venv/bin/python` prints `0` correctly. | pending | (1) Split the subprocess anti-criterion into two pipe-free rows: `grep -c "^import subprocess" agent/reflection_scheduler.py` expecting 0, and `grep -c "^from subprocess" agent/reflection_scheduler.py` expecting 0. Do NOT fix it by unescaping the pipe — the findings-table parser splits on `(?<!\\)\|` and an unescaped pipe shifts columns. (2) Change `python3 -c` to `.venv/bin/python -c` (verified working) in the row importing `agent.reflection_scheduler`. (3) The plan protects the `git rev-parse` token from the grep-trips-on-prose trap but leaves the identical trap on `grep -c "always present"` == 0: the mandated docstring rewrite must say the in-repo copy is *not* always present. Add an explicit instruction to the Documentation task — 'say "absent in worktrees", never the phrase "always present"' — mirroring the `git rev-parse` treatment. |
| CONCERN | Risk & Robustness | `_exhausted_warned: set[str]` is module-level mutable state with no specified reset hook, while the plan's dedup test asserts `caplog` record counts across two calls. Any earlier test that exhausts the same candidate set primes the set, so the dedup test observes zero records and fails — or passes vacuously in the reverse order. This repo runs `pytest-randomly` (every Verification row already carries `-p no:randomly` to work around it), and the 'No FileNotFoundError anywhere in the module' row runs the priming tests and the dedup test in one process. | pending | Add an autouse reset fixture to `tests/unit/test_reflection_scheduler.py` and name it in task 3 so the test-engineer does not meet it as a flake: `@pytest.fixture(autouse=True)` yielding around `from agent import reflection_scheduler as rs; rs._exhausted_warned.clear()` on both entry and exit, so the test neither inherits nor leaks state. Do not rely on the dedup row's `-k exhausted` isolation to hide the coupling — the full-module row exercises it. |
| CONCERN | Risk & Robustness | The registry-untouched anti-criterion is cwd-fragile. Task 1 captures `shasum -a 256 config/reflections.yaml ~/Desktop/Valor/reflections.yaml`, recording the **relative** path `config/reflections.yaml`, but tasks 4 and 6 run in a worktree and the Verification row states no cwd. Measured: running `shasum -a 256 -c` on that baseline from a non-primary cwd prints `config/reflections.yaml: FAILED open or read` plus `WARNING: 1 listed file could not be read`. The row then fails for a reason unrelated to the registry being edited — the false-alarm mode Risk 4 warns about, on the very row that replaced the round-1 unfalsifiable `git diff`. | pending | Capture absolute paths so the check is cwd-independent: from the primary checkout, `shasum -a 256 "$(git rev-parse --show-toplevel)/config/reflections.yaml" ~/Desktop/Valor/reflections.yaml > /tmp/2734-registry-baseline.txt`. Keep that bullet explicitly pinned to the primary checkout — `git rev-parse --show-toplevel` in a worktree returns the worktree root. The tilde is shell-expanded at capture time so the vault line is already absolute; only the first path needs fixing. Expected output stays 'two lines, both ending `OK`'. |
| CONCERN | Scope & Value | Data Flow names 'the dashboard' as an entry point importing `agent.reflection_scheduler` and closes by asserting step 2 is 'the single point every downstream consumer funnels through'. Neither holds. `ui/data/reflections.py:17` defines its own `REGISTRY_PATH = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"` and `_load_registry()` at line 80 opens that literal path inside a `try/except` returning `[]`. It never imports the resolver, so the dashboard silently renders an empty registry in exactly the situation this plan fixes, and is untouched by the fix. The 'single funnel point' claim is the argument that makes option 2 a one-point fix rather than ten edits, and it is overstated by one live consumer. | pending | Correct Data Flow ('every downstream consumer' → 'every consumer that uses the shared resolver') and add a No-Go: `[SEPARATE-SLUG] ui/data/reflections.py::_load_registry hardcodes config/reflections.yaml and fails open with []; the dashboard is served from the primary checkout where that file exists, so it is not on this issue's failure path.` Verify that premise before writing it. Do NOT quietly widen task 2 to also edit `ui/data/reflections.py` — its fail-open `except Exception: return []` is a different contract from the scheduler's, and changing resolution without changing the failure mode moves the silent-empty behavior rather than removing it. |
| CONCERN | Scope & Value | The Documentation bullet mandates correcting 'the stale claim at lines 62 and 102' that `config/reflections.yaml` is a symlink. Measured, `docs/features/reflections.md` carries **10** symlink assertions: lines 62, 94, 102, 104, 105, 143, 144, 795, 813, 831. Lines 104-105 state it outright ('On live machines, `config/reflections.yaml` is a symlink … The symlink is created by `sync_reflections_yaml()`'), line 831's table row says sync 'creates vault symlink on update', and 795/813 repeat it in reference tables. Fixing 2 of 10 leaves the document asserting both forms, while Risk 2 depends on the real-file invariant being the documented one. | pending | Replace the two line numbers with file-wide scope: 'correct every symlink assertion in `docs/features/reflections.md` — measured 10 occurrences at lines 62, 94, 102, 104, 105, 143, 144, 795, 813, 831 — to the real-file copy written by `scripts/update/env_sync.py::sync_reflections_yaml`.' Add a falsifiable pipe-free Verification row, `grep -ci symlink docs/features/reflections.md` against a stated target count, and record that lines 143-144 describe `reflection_machine_filter` refusing to write *through* a symlink (a defensive check, not a claim the file is one) and may legitimately survive. Naming which occurrences may remain is what makes the count checkable. |
| CONCERN | History & Consistency | The round-1 finding was that `_resolve_target`'s docstring records critique decision C6 as an invariant this plan invalidates. The revision mandates rewriting `scripts/update/reflection_register.py::_resolve_target` (lines 181-192) — but the same C6 claim, stated more strongly, lives in that file's **module docstring** at lines 30-38, which the plan never names: 'The target is resolved via ``agent.reflection_scheduler._resolve_registry_path()`` (critique C6), which prioritizes the vault over the config copy'. After this plan the shared resolver can return precisely that copy, so the plan's own instruction that the C6 sentence 'must not survive unqualified' is satisfied for the function and violated for the module. | pending | Name both sites in the Documentation inline bullet: the module docstring (lines 30-38) and `_resolve_target` (lines 181-192). The module docstring needs a narrower edit — its point ('do not hardcode the config copy') stays true; what becomes false is the unqualified 'prioritizes the vault over the config copy'. Qualify it with the reachability condition the plan already worked out: the fourth level is reached only when this checkout's `config/reflections.yaml` is absent, which never holds in the primary checkout where `/update` runs. Checkable gate: `grep -c "prioritizes the vault over the config copy" scripts/update/reflection_register.py` expecting 0. |
| CONCERN | History & Consistency | The call-site census is wrong and is stated six times as a measured fact (Problem, Solution, Test Impact, task 3, Success Criterion 8, and a round-1 Critique Results row asserting 'verified: 9 sites'). `_registry_path()` has **7** call sites in `tests/unit/test_reflection_scheduler.py` — lines 71, 90, 653, 661, 682, 709, 717 — not 9. `grep -c "_registry_path()"` returns 9 because it also counts the `def _registry_path()` line at 40 and the `_resolve_registry_path()` substring at line 48 inside the helper body. Total across both modules is 8, not 10. Separately, lines 71 and 653 assert `registry_path.exists()` before opening, so they raise `AssertionError`, not `FileNotFoundError`; only 5 in-module sites raise `FileNotFoundError`. | pending | Replace 'nine'/'ten' with 7 in-module + 1 at `tests/unit/test_plan_migration_invariant.py:78` = 8 total, and cite the line numbers (71, 90, 653, 661, 682, 709, 717) so the builder gets a checklist rather than a count. Re-measure with `grep -n "= _registry_path()" tests/unit/test_reflection_scheduler.py` — anchoring on the assignment excludes the `def` line and the helper body that inflated the count. Also correct 'ten `FileNotFoundError`s' to '5 `FileNotFoundError`s and 2 `AssertionError`s in-module, plus 1 `FileNotFoundError` cross-module': the fixture conversion subsumes both shapes, but `grep -c "FileNotFoundError"` == 0 would pass today for the two `AssertionError` sites and so is not evidence they were converted. |
| NIT | Scope & Value | `agent/reflection_scheduler.py:98`'s `REGISTRY_PATH` constant has zero readers repo-wide — the only other `REGISTRY_PATH` is `ui/data/reflections.py`'s independent definition. The plan reasons about this constant in three sections (Data Flow step 3, the no-subprocess rationale, Race Conditions). The import-time argument still holds, since line 98 executes the resolver at import, so the no-subprocess decision is unaffected; but a reader will look for consumers of a value nothing consumes. | pending | N/A (NIT). |

---

## Open Questions

Both open questions from the issue are resolved in this plan with evidence rather than deferred:

1. **Defect 2 fix strategy** — decided: **option 2**. Option 1 is blocked by the independent `~/Desktop` realpath guard at `agent/reflection_scheduler.py:230-241` and would require betting against a TCC hang this repo already lost once. Option 3 leaves the production hole open and needs ten edits that each fail open if forgotten. Rationale in full under Technical Approach.
2. **Should the nightly detector run with `VALOR_LAUNCHD` unset?** — decided: **no**. The var is a faithful part of the production environment; unsetting it would make tests diverge from production and hide the resolver bug rather than fix it.

One item for the reviewer's judgment, not a blocker:

3. **The #2810 carve-out.** This work deliberately lands with `TestRegistryIntegrity::test_all_callables_resolve` still red, because its cause is registry content awaiting #2708's module. The alternative — deleting or disabling the `expectation-reconciler` entry to force the class green — would hide a live production error that fires every 30 minutes, so it is listed as a No-Go. If a fully green class is required before merge, #2810 must land first and this becomes an [ORDERED] dependency.
