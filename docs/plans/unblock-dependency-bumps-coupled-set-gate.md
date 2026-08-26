---
status: Planning
type: bug
appetite: Medium
owner: valor
created: 2026-08-26
tracking: https://github.com/tomcounsell/ai/issues/3001
last_comment_id: 5420202999
---

# Coupled-set dependency bumping + a real `run_typed` gate

## Problem

On 2026-08-24 a routine `/update` auto-bumped `anthropic` 0.125.0 → 1.0.0 on the
lockfile-maintainer machine. anthropic 1.0.0 removed `temperature`/`top_p`/`top_k`
from the Messages API; `pydantic-ai-slim==2.9.0` passes all three unconditionally
(as the `OMIT` sentinel, which still binds the keyword). Every `run_typed` call
therefore died at argument binding, before any network I/O. The auto-bump smoke
gate waved it through, because the gate is an **import** check and
`import anthropic` succeeds fine on a version whose call signature we cannot
satisfy. Six hours later it surfaced as 66 confusing nightly test failures and a
39-issue tracker flood.

`d0c02bde5` removed `anthropic` from `AUTO_BUMP_PACKAGES` to stop the bleeding.
That stopgap deliberately leaves us stale and does not address the shape of the
defect: `AUTO_BUMP_PACKAGES` is a flat list of independent packages, and the gate
that decides whether a bump survives cannot observe the thing that broke.

**Current behavior:**

- `scripts/update/deps.py::auto_bump_deps` iterates `AUTO_BUMP_PACKAGES` one
  package at a time. There is no notion of packages that must move together, so
  a member of a coupled set can advance alone.
- `scripts/update/deps.py::run_smoke_test` phase 1 is
  `import anthropic; import claude_agent_sdk`, phase 2 is one fast pytest file
  (`tests/unit/test_docs_auditor_substrate.py`). Neither makes an LLM call, so
  neither can catch an argument-binding break in the LLM layer.
- `main` is **currently pinned to the known-bad pair** (see Freshness Check
  below): `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0`. Every
  non-harness LLM call in the repo raises today.
- The pin-editing helpers cannot even express the coupled set correctly:
  `get_pinned_version("openai")` returns a version scraped out of a *comment*,
  and `bump_pin_in_pyproject("pydantic-ai-slim", ...)` silently returns `False`
  because the pin carries an extras marker. Both verified below.

**Desired outcome:**

- `anthropic` + `pydantic-ai-slim` + `openai` are declared as one coupled set and
  are bumped atomically or not at all — a partial advance is structurally
  impossible, not merely discouraged.
- The auto-bump gate makes a **real** call through `agent/llm/wrapper.py::run_typed`
  against the freshly synced venv. A bump that breaks the LLM layer rolls back at
  bump time, on the maintainer machine, before it reaches `origin/main`.
- `anthropic` is back in `AUTO_BUMP_PACKAGES` (as a member of its set), landing in
  the same change as the gate that makes it safe.
- Verified by staging the known-bad pair and *observing* the rollback, not by
  asserting it in a mock.

This plan is **Step 1 of the sequencing agreed in
[issue comment 5420111955](https://github.com/tomcounsell/ai/issues/3001#issuecomment-5420111955)**:
gate first, upgrade second. The dependency upgrade itself (including the
`openai` 2.x → 3.x major bump) is explicitly **not** in this lane.

## Freshness Check

**Baseline commit:** `d0c02bde54ddb187ea9a536767f257907f5258fc`
**Issue filed at:** 2026-08-25T05:16:36Z
**Disposition:** **Major drift** — surfaced to the supervisor, plan proceeds on a
revised premise (see below). The revision does not change this lane's scope; it
adds a one-line prerequisite ahead of it.

### The drift: the stopgap commit shipped the known-bad pin

The issue and the stopgap commit message both describe `7db5b82bb` as having
reverted `anthropic` to `0.125.0`. That revert is **no longer on main**.
`d0c02bde5` ("stop auto-bumping anthropic — it is half of a coupled set") swept
the already-staged auto-bump re-application into its own commit:

```
$ git show d0c02bde5 -- pyproject.toml
-    "anthropic==0.125.0",
+    "anthropic==1.0.0",
```

The comment block immediately above that pin still reads *"Do NOT move to 1.x
while pydantic-ai-slim is on 2.9.0"* — the file now contradicts itself, which is
strong evidence the pin change was accidental collateral of `git add
pyproject.toml uv.lock` while a staged re-bump was sitting in the index
(the session that produced this plan started with exactly that dirty state).

Verified live against the current venv at the baseline commit:

```
anthropic 1.0.0
pydantic-ai-slim 2.9.0
openai 2.30.0
temperature in AsyncMessages.create signature: False

FAIL LLMCallError run_typed failed for model=claude-haiku-4-5-20251001:
     AsyncMessages.create() got an unexpected keyword argument 'temperature'
CAUSE TypeError AsyncMessages.create() got an unexpected keyword argument 'temperature'
```

**Consequence for this plan:** every non-harness LLM call on `main` is dead right
now — routing classification, memory extraction, LLM judges, the drafter. It also
makes the gate work unverifiable as written: you cannot demonstrate "the gate
passes on a good pair and rolls back on the bad pair" from a baseline that is
already the bad pair. Restoring a working pair therefore becomes **Phase 0** of
this plan (a one-line pin revert to `anthropic==0.125.0`, re-landing `7db5b82bb`'s
intent). It is not the Step-2 upgrade and does not violate the sequencing
directive.

If a hotfix lands that revert on `main` before this lane builds, Phase 0
degrades to a verification step.

### File:line references re-verified

| Reference | Issue's claim | Status |
|---|---|---|
| `scripts/update/deps.py:329` | `AUTO_BUMP_PACKAGES` excludes `anthropic` | **Holds** — `AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]` |
| `scripts/update/deps.py:414` | `run_smoke_test` is import-only + one pytest file | **Holds** |
| `scripts/update/deps.py:463` | `auto_bump_deps` iterates packages independently | **Holds** |
| `scripts/update/run.py:1304` | `/update` Step 3.5 calls `auto_bump_deps`, maintainer-only | **Holds** (gate at `run.py:1169`, `is_lockfile_maintainer`) |
| `agent/llm/wrapper.py::run_typed` | the single funnel for non-harness LLM calls | **Holds** |
| `pyproject.toml:12` | `anthropic` pinned to the reverted 0.125.0 | **DRIFTED** — now `1.0.0` (see above) |

### Cited sibling issues/PRs re-checked

- **#2932** — closed, folded into #3001. Its scope lands in Work Item 3, deferred out of this lane.
- **#2960–#2999** — all closed as duplicates. Their shared-root-cause diagnosis of the worker-key failures is wrong; not relied on here.
- **#2949** / `69dc69568` — merged; owns Work Item 2. Deferred out of this lane.
- **#3016** — the independent `test_promise_gate_real_api` failure, separately filed. Not in scope.
- **#2334** — deliberately left open, separate scope.

### Commits on main since the issue was filed (touching referenced files)

- `7db5b82bb` — reverted anthropic to 0.125.0. **Superseded** — no longer effective on main.
- `53cec47de` — removed the valor CLI wrapper; touched `pyproject.toml` scripts table only. Irrelevant.
- `d0c02bde5` — the stopgap. **Changed the premise** (see above).

### Active plans in `docs/plans/` overlapping this area

None. `grep -l "auto_bump\|anthropic" docs/plans/*.md` matches only
`docs-auditor-review-gate.md` and `overclaim-guard-greps-whole-worktree.md`,
both incidental mentions with no overlap on `scripts/update/deps.py`.

### Notes

Three additional mechanical defects in the pin helpers were found during the
freshness pass and are load-bearing for the design — see Spike Results spike-2.

## Prior Art

- **#3001 stopgap `d0c02bde5`** — removed `anthropic` from `AUTO_BUMP_PACKAGES`. Bought time; is the thing this plan replaces. It is also the source of the Major drift above.
- **`7db5b82bb`** — `fix(deps): revert anthropic to 0.125.0`. The correct emergency action; got clobbered five commits later. Direct evidence that a one-off manual revert is not a durable fix and that the *gate* is the real deliverable.
- **`9d1488ccb`** — `chore(deps): commit auto-bump anthropic 1.0.0`. The breaking bump. Landed through the existing smoke gate cleanly, which is the whole indictment.
- **`884302861`** — "Harden dependency management with tiered pinning and two-speed updates". Introduced `AUTO_BUMP_PACKAGES`, the `CRITICAL — pin exact` tier, and the smoke-test-then-rollback shape. The scaffolding is right; it only lacks coupling and a call-level gate.
- **PR #1696** — `deps(#1653): bump popoto >=1.7.1`. A hand-driven dependency bump through the SDLC pipeline. Confirms the repo's normal posture is exact pins moved deliberately, which is what coupled sets formalize.
- **`docs/archive/plans-completed/sdlc-1091.md`** — documents that `auto_bump_deps` commits and pushes *during* the cron `/update` run, and that the restart gate reads HEAD after `run.py --cron` returns. Any change to auto-bump's commit behavior must preserve that ordering.

No prior attempt at coupled-set bumping or a call-level gate exists. There is no
"Why Previous Fixes Failed" section because there is exactly one prior fix (the
stopgap), and it did not fail — it was explicitly scoped as a holding action.

## Research

**Queries used:**
- `pydantic-ai anthropic 1.0 temperature top_p removed Messages API compatibility fix release`

**Key findings:**

- **`pydantic-ai-slim>=2.33.0` is the first release that supports `anthropic>=1.0.0`.** Every release before it — including 2.32.2, cut hours earlier — allowed `anthropic 1.0.0` in its metadata without supporting it. The fix landed as "Use httpx2 for Anthropic clients". Sources: [pydantic-ai changelog](https://ai.pydantic.dev/changelog), [Anthropic Python SDK v1.0 migration](https://www.digitalapplied.com/blog/anthropic-python-sdk-v1-breaking-change-migration). **Informs the plan:** this is the exact version boundary the coupled set exists to enforce, and it is the number Step 2 will target. It also confirms the upper-bound hole is upstream metadata we do not control — a local gate is the only remedy available to us.
- **anthropic 1.0.0 (2026-08-20) also moved its HTTP layer from `httpx` to `httpx2`**, and `AnthropicProvider(http_client=...)` now rejects legacy `httpx.AsyncClient`. **Informs the plan:** a future coupled bump can break `run_typed` through the transport as well as through argument binding — another failure mode an import check cannot see, and one a real call does catch. `agent/llm/wrapper.py` constructs `anthropic.AsyncAnthropic(...)` directly and does not pass its own `http_client`, so it is not exposed to that specific break today.
- **Anthropic additionally deprecated non-default `temperature`/`top_p`/`top_k` server-side on Opus 4.7+**, returning HTTP 400. **Informs the plan:** the gate must treat a provider-side 400 as a genuine failure, not an environmental blip — but see Risk 3 on distinguishing that from network flakiness.

Saved to memory as `9716dcf2cf4a46eda06bd480554ea1ff`.

## Spike Results

### spike-1: Does the known-bad pair actually fail through `run_typed` on the current main?
- **Assumption**: "the break described in #3001 is reproducible at the baseline commit"
- **Method**: prototype (live call in the repo venv)
- **Finding**: **Yes, and it is live on main right now.** `run_typed` raises `LLMCallError` wrapping `TypeError: AsyncMessages.create() got an unexpected keyword argument 'temperature'`. Failure occurs at argument binding — no network I/O, no API cost, sub-second.
- **Confidence**: high
- **Impact on plan**: (a) drove the Major-drift disposition and Phase 0; (b) proves the gate's negative case is **cheap and deterministic** — the known-bad pair fails in under a second with no token spend, so the "stage the bad pair and observe rollback" acceptance test is practical to run for real.

### spike-2: Can the existing pin helpers express the coupled set?
- **Assumption**: "adding `pydantic-ai-slim` and `openai` to `AUTO_BUMP_PACKAGES` is a one-line change"
- **Method**: prototype (called the helpers directly against the real `pyproject.toml` and a temp copy)
- **Finding**: **No — three defects, each of which would silently produce exactly the half-bump this plan exists to prevent.**

  ```
  get_pinned_version(repo, 'anthropic')        -> '1.0.0'   OK (by line order)
  get_pinned_version(repo, 'pydantic-ai-slim') -> '2.9.0'   OK (by luck)
  get_pinned_version(repo, 'openai')           -> '2.9.0'   WRONG
  bump_pin_in_pyproject(tmp, 'pydantic-ai-slim', '2.34.0')  -> False
  bump_pin_in_pyproject(tmp, 'openai', '3.0.0')             -> False
  bump_pin_in_pyproject(tmp, 'anthropic', '1.2.3')          -> True
  ```

  1. **`get_pinned_version` matches comment text.** It scans for `package in line and "==" in line`. `"openai"` appears inside the `pydantic-ai-slim` line's comment (*"avoids openai/google/mcp/logfire..."*), so it returns that line's `2.9.0`. The real declaration is `"openai>=1.0.0"` — a **floor, not an exact pin**, with no `==` anywhere.
  2. **The same substring matching makes `anthropic` correct only by line order** — the `pydantic-ai-slim[anthropic]==2.9.0` line also contains `anthropic` and `==`. Reordering `pyproject.toml` would silently change what `get_pinned_version("anthropic")` returns.
  3. **`bump_pin_in_pyproject` cannot match an extras pin.** Its regex is `"{package}==[^"]*"`; the real line is `"pydantic-ai-slim[anthropic]==2.9.0"`. It returns `False`, which `auto_bump_deps` records as a per-package `error` and then **continues** — bumping the other set members. That is the incident shape reproduced exactly.
- **Confidence**: high
- **Impact on plan**: the coupled-set work is not a data-structure change; it requires making the pin reader/writer *declaration-aware* (extras-tolerant, comment-blind) and giving `openai` an exact pin. Phase 1 is sized accordingly, and each of the three defects gets its own regression test.

### spike-3: Is a real `run_typed` call viable from the update process?
- **Assumption**: "the lockfile-maintainer machine can make a live Anthropic call during `/update`"
- **Method**: code-read + prototype
- **Finding**: Yes. `utils/api_keys.py::get_anthropic_api_key` resolves a key on this machine (checked presence only, no value echoed), falling back to `~/Desktop/Valor/.env` when the launchd environment is thin — which is the relevant path, since `/update --cron` runs headless. `run_typed` defaults to `MODEL_FAST` (Haiku) with `settings.timeouts.anthropic_sdk_s` / `anthropic_hard_s` double timeouts already wired.
- **Confidence**: high
- **Impact on plan**: the gate can call `run_typed` directly rather than re-implementing a raw client. It must run **inside the target venv** (`{project_dir}/.venv/bin/python`), never in the update process's own interpreter, because the update process imported its modules before the sync. This mirrors the existing `_markitdown_importable` probe pattern already in `deps.py`.

### spike-4: What does `auto_bump_deps` rollback restore today?
- **Assumption**: "rollback is per-package"
- **Method**: code-read (`scripts/update/deps.py:463-547`)
- **Finding**: Rollback is **whole-file**: `original_content` is snapshotted once before the loop and rewritten wholesale on any sync or smoke failure. With one auto-bumped package that is indistinguishable from per-package. With sets it means one bad set reverts every other set's good bump in the same cycle.
- **Confidence**: high
- **Impact on plan**: Phase 1 introduces **per-set** snapshot/restore. Sets are evaluated sequentially, each with its own sync + gate + rollback, so `claude-agent-sdk` still advances when the LLM set is held back.

## Data Flow

Auto-bump today (`/update --cron`, maintainer machine only):

1. **Entry point**: `scripts/update/run.py:1302` — Step 3.5, gated on `config.do_auto_bump and is_lockfile_maintainer` (`run.py:1169`).
2. **`deps.auto_bump_deps(project_dir)`** — for each package in `AUTO_BUMP_PACKAGES`: `get_pinned_version` (read `pyproject.toml`) → `get_pypi_latest` (`pip index versions`, PyPI JSON fallback) → `bump_pin_in_pyproject` (regex rewrite).
3. **`sync_dependencies(frozen=False)`** — `uv sync --all-extras` re-resolves and rewrites `uv.lock`, then `uv pip install -e .`.
4. **`run_smoke_test(project_dir)`** — `{venv}/bin/python -c "import anthropic; import claude_agent_sdk"`, then `pytest tests/unit/test_docs_auditor_substrate.py -x -q`. **← the layer that failed to observe the break.**
5. **Rollback or commit** — on failure, restore `original_content` + re-sync. On success, `run.py:1325-1345` stages `pyproject.toml` + `uv.lock`, commits, pushes (with a fetch/rebase-onto-named-ref retry).
6. **Fanout** — follower machines `uv sync --frozen` from the pushed lockfile on their next `/update`.

After this plan, step 2 becomes set-scoped (all members resolved and rewritten
together, or none), step 4 gains a phase 0 that runs `run_typed` against the
freshly synced venv, and step 5's rollback restores the **set's** snapshot.

The break's blast path, for reference: step 5 pushed → step 6 fanned the broken
lockfile out → every `run_typed` caller (`agent/routing.py`,
`agent/memory_extraction.py`, LLM judges, drafter) started raising → surfaced 6h
later via the nightly detector. The gate at step 4 is the only chokepoint that
sees the failure before step 5.

## Architectural Impact

- **New dependencies**: none. The gate reuses `agent/llm/wrapper.py` and the existing Anthropic key resolution.
- **Interface changes**: `AUTO_BUMP_PACKAGES` (a `list[str]`) is replaced by a set-aware declaration. `run_smoke_test`'s signature grows a way to express "the LLM phase failed" distinctly from "the pytest phase failed" (`AutoBumpResult.smoke_output` already carries free text; a structured phase marker is preferable). `AutoBumpResult` gains per-set bookkeeping.
- **Coupling**: deliberately **increases** coupling between `scripts/update/deps.py` and `agent/llm/wrapper.py` — but only across a subprocess boundary into the target venv, so the update process itself does not import the LLM stack. That direction is correct: the gate must exercise the thing it protects.
- **Data ownership**: unchanged. `pyproject.toml` remains the single source of pin truth; `uv.lock` remains maintainer-authored and follower-consumed.
- **Reversibility**: high. The whole change is confined to `scripts/update/deps.py` plus a pin declaration; reverting restores the flat list and the import check.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (Phase 0 disposition; the "no API key at bump time" policy call)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` resolvable | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | The gate makes a real Haiku call |
| `uv` on PATH | `uv --version` | Coupled-set sync/rollback re-resolves the lockfile |
| Network reach to PyPI | `python -c "from scripts.update.deps import get_pypi_latest; assert get_pypi_latest('anthropic')"` | Latest-version discovery |

## Solution

### Key Elements

- **Coupled-set declaration** — `AUTO_BUMP_PACKAGES` becomes a list of *sets*. `["anthropic", "pydantic-ai-slim", "openai"]` is one set; `["claude-agent-sdk"]` is another. A set is the atomic unit of bump, sync, gate, and rollback. The declaration carries a short prose reason so the next reader knows *why* these three are welded together.
- **Declaration-aware pin helpers** — the pin reader and writer stop doing substring matching against whole lines (comments included) and stop assuming a bare `name==version` shape. They locate a package's actual dependency declaration, tolerate extras (`pydantic-ai-slim[anthropic]`), and refuse rather than silently no-op when they cannot find one.
- **`openai` gets an exact pin** — it is currently `openai>=1.0.0`, a floor. A coupled-set member with no exact pin cannot be moved atomically. It is pinned at its currently-installed version as part of Phase 1 (a no-op for resolution; **not** the 2.x → 3.x upgrade).
- **Real-call LLM gate** — `run_smoke_test` grows a first phase that shells into the freshly synced venv and runs a minimal `run_typed` call with a trivial `output_type`. Success means argument binding, transport construction, the provider round-trip, and schema validation all work on the new pins. Failure fails the set.
- **Atomic per-set rollback** — each set snapshots `pyproject.toml` before its own edits and restores only that snapshot on failure, then re-syncs. An unrelated set's successful bump survives.
- **`anthropic` returns to auto-bump** — re-added as a member of the LLM set, in the same commit as the gate. Never before it.

### Flow

```
/update --cron (maintainer machine)
  → for each coupled set:
      → resolve latest for EVERY member          → any unresolvable? skip whole set
      → rewrite ALL member pins (set snapshot taken first)
      → uv sync --all-extras (unfrozen)          → fail? restore set snapshot, re-sync, next set
      → GATE phase 0: real run_typed call in the new venv
      → GATE phase 1: import check
      → GATE phase 2: fast pytest file
      → any gate phase fails? restore set snapshot, re-sync, record rolled_back
  → any set survived? commit + push pyproject.toml + uv.lock
```

### Technical Approach

- **Set semantics are all-or-nothing at every stage.** If any member's latest version cannot be resolved, the set does not move at all — a partial resolve is the failure mode we are eliminating, so it must not degrade into a partial bump. Same for the pin rewrite: if any member's rewrite fails, restore the snapshot and abandon the set immediately rather than continuing (today's code records an error and carries on — spike-2 shows that is exactly how the incident happened).

- **The gate runs in the target venv, not in-process.** `{project_dir}/.venv/bin/python -c <script>` (or a small dedicated module invoked with `-m`), following the existing `_markitdown_importable` pattern. In-process would exercise the *pre-sync* imports the update process already holds.

- **The gate script is deliberately minimal**: a one-field `pydantic.BaseModel`, a short prompt, `await run_typed(...)`, print a sentinel on success, non-zero exit on any exception. Bounded by an outer subprocess timeout in addition to `run_typed`'s own double timeout, so a hung socket cannot wedge `/update`.

- **Fail-closed on ambiguity.** If the gate cannot run at all — no API key resolvable, venv python missing, subprocess timeout — the set is rolled back and a distinct warning is emitted through the existing `_append_warning` channel. Rationale: auto-bump is an unattended, optional convenience that pushes to the whole fleet; declining to bump costs one stale cycle, while bumping unverified is what produced this issue. See Open Question 2.

- **Distinguish gate phases in the result.** `AutoBumpResult.smoke_output` is currently a bare string. The operator reading a `/update` warning needs to tell "the LLM pair is incompatible" from "an unrelated unit test is flaky" — those have different responses. Carry a phase marker (llm / import / pytest) alongside the output.

- **Do not touch the commit/push path in `run.py`.** `docs/archive/plans-completed/sdlc-1091.md` documents that the restart gate depends on auto-bump's commit landing in local HEAD synchronously before `auto_bump_deps` returns. That ordering stays exactly as-is.

- **Fix the stale comment block** above the `anthropic` pin in `pyproject.toml`. It currently forbids the very pin sitting under it. Replace it with a pointer to the coupled-set declaration so there is one place stating the constraint.

## Failure Path Test Strategy

### Exception Handling Coverage
- `deps.py::get_pypi_latest` has two bare `except Exception` blocks (method-1 fallthrough, method-2 return `None`). Both are pre-existing and stay; the set logic must treat a `None` latest as "skip the whole set", and that is asserted directly.
- `deps.py::_markitdown_importable` swallows `TimeoutExpired`/`OSError` → `False`. Untouched.
- The new gate must **not** add a bare `except Exception: pass`. Every failure path returns `(False, phase, message)` and the message reaches `AutoBumpResult.smoke_output`, which `run.py:1315` already logs. Test asserts the message is non-empty on each failure path.
- No exception handler introduced by this work may swallow a rollback failure — if the restore-and-resync itself fails, that surfaces as a warning, not silence.

### Empty/Invalid Input Handling
- `get_pinned_version` returning `None` (package absent from `pyproject.toml`) → set skipped, tested.
- `get_pypi_latest` returning `None` (network down) → set skipped, tested.
- Empty/whitespace prompt is already rejected by `run_typed` with `ValueError` before any network work; the gate script's prompt is a literal, so this is not reachable, but the gate's non-zero exit on `ValueError` is covered by the generic "any exception fails the gate" test.
- A `pyproject.toml` with no dependency block at all → helpers refuse, no rewrite, no crash.

### Error State Rendering
- The operator-visible path is the `/update` summary. Assert that a rolled-back set produces the `"Auto-bump rolled back"` warning **and** that the phase marker appears in the logged detail — a rollback whose reason is not legible is the same failure as no rollback at all.
- Assert the success path still logs `"Smoke test passed after bump"` so the existing `extract_update_warnings` parsing is not disturbed.

## Test Impact

- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_no_bump_when_already_latest` — UPDATE: fixture `pyproject.toml` lists `anthropic` and `claude-agent-sdk` as flat siblings. Rewrite against the set declaration; keep the assertion that nothing bumps when all members are at latest.
- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_rollback_on_smoke_failure` — UPDATE: it patches `scripts.update.deps.run_smoke_test` to return a 2-tuple. Adjust to the new return shape and assert the **set's** snapshot is restored (all members), not just the file.
- [ ] `tests/integration/test_remote_update.py::TestGetPinnedVersion::test_reads_pinned_version` — UPDATE: extend with the extras form and the comment-collision case from spike-2. This test currently passes against a one-line fixture that cannot expose either defect.
- [ ] `tests/integration/test_remote_update.py` — ADD: `test_partial_resolve_skips_whole_set`, `test_extras_pin_is_bumped`, `test_openai_pin_not_read_from_comment`, `test_llm_gate_failure_rolls_back_set`, `test_unrelated_set_survives_failed_set`, `test_gate_unavailable_is_fail_closed`.
- [ ] No changes to `tests/unit/test_docs_auditor_substrate.py` — it stays the gate's pytest phase; this plan does not repurpose it.

## Rabbit Holes

- **Do not build a general dependency-compatibility solver.** The remedy for the missing upper bound in pydantic-ai's metadata is a local gate, not a constraint engine or a vendored override file. Three packages, one declared set, one real call.
- **Do not rewrite `pyproject.toml` parsing onto `tomlkit`/`tomllib`.** Tempting after spike-2, and genuinely more correct — but the writer must preserve the `CRITICAL — pin exact` comments verbatim, and round-tripping comments is where this swallows a day. Make the regex declaration-aware (anchored to the quoted dependency string, extras-tolerant) and move on. Revisit only if a fourth defect appears.
- **Do not make the gate run the full test suite.** A ~20-minute unit run inside `/update --cron` is not a gate, it is an outage. One real LLM call plus the existing fast pytest file.
- **Do not attempt the upgrade "while we're in here."** `pydantic-ai-slim` 2.35.0 and `openai` 3.3.1 are sitting on PyPI and the temptation is real. That is Step 2, behind this gate, per the sequencing directive. A three-way major bump with the gate landing in the same PR gives you no way to tell which half is at fault.
- **Do not generalize the gate to "one smoke call per subsystem."** Every coupled set will want one eventually. Ship the LLM one; let the second requester justify the abstraction.

## Risks

### Risk 1: Phase 0 is a live-fire change to a broken main
**Impact:** The pin revert to `anthropic==0.125.0` requires a real `uv sync` and rewrites `uv.lock` for the whole fleet. If it is wrong, main goes from "LLM layer broken" to "nothing installs".
**Mitigation:** It is a straight re-application of `7db5b82bb`, which was verified working on 2026-08-25 (the issue's Recon Summary records `test_routing.py::TestNeedsResponseLlmClassification` going 10/10 after it). Verify by re-running the spike-1 probe — it must print `OK` instead of `FAIL` — before anything else in this lane proceeds. If a hotfix has already landed it, confirm and skip.

### Risk 2: The gate makes `/update` depend on the Anthropic API being up
**Impact:** A provider outage or a lapsed key turns every maintainer-machine auto-bump cycle into a rollback + warning, and the fleet silently stops receiving dependency updates.
**Mitigation:** Accepted, with visibility. Auto-bump is a convenience path, already restricted to one machine, and a skipped cycle costs nothing but staleness. The distinct fail-closed warning (see Technical Approach) makes a persistent outage legible in the `/update` summary rather than invisible. `run_typed`'s existing double timeout plus a subprocess timeout bound the cost of a hang. Note the gate spends a handful of Haiku tokens only on cycles where something actually bumped.

### Risk 3: A genuine provider-side error is misread as an incompatible pin
**Impact:** A 400 from the deprecated-sampling-params change (see Research) or a 529 overload rolls back a perfectly good bump, and the operator chases a dependency ghost.
**Mitigation:** The gate reports the exception type and message verbatim in `smoke_output` under the `llm` phase marker, so `TypeError` (binding — a real incompatibility) is distinguishable from `APIStatusError` (provider) at a glance. Deliberately **not** adding retry logic or error classification in this lane: rolling back on a transient is the safe direction, and a classifier here is a rabbit hole. Revisit if it fires spuriously.

### Risk 4: A future coupled break lands through a path auto-bump does not own
**Impact:** The gate only guards `auto_bump_deps`. A hand-edited pin, a `uv lock --upgrade`, or a merged PR bumping `pyproject.toml` reaches main ungated — which is exactly how `d0c02bde5` shipped the bad pin.
**Mitigation:** Out of scope here and named as such (No-Gos). Worth noting the shape: the durable fix is a check on the *lockfile-changed* path, not just the auto-bump path. Flagged in Open Question 3 for the supervisor to route.

### Risk 5: `openai` gaining an exact pin changes resolution for someone
**Impact:** `openai>=1.0.0` currently floats; pinning it exactly could conflict with a transitive requirement.
**Mitigation:** Pin at the currently-installed version (2.30.0 in this checkout) so the resolution is provably unchanged, and verify with a clean `uv sync --all-extras` producing no `uv.lock` diff beyond the pin line. This is deliberately **not** the 2.x → 3.x move.

## Race Conditions

### Race 1: Concurrent `/update` runs on the maintainer machine
**Location:** `scripts/update/run.py:1302-1345`, `scripts/update/deps.py::auto_bump_deps`
**Trigger:** A cron `/update` and a manually-triggered `/update` overlap. Both snapshot `pyproject.toml`, both edit it, and one's rollback restores a snapshot taken before the other's edit — resurrecting a pin that was deliberately moved.
**Data prerequisite:** The snapshot must reflect the state at the moment the set's edits begin.
**State prerequisite:** Single-writer on `pyproject.toml` for the duration of a set's bump-sync-gate-rollback cycle.
**Mitigation:** Pre-existing and **unchanged by this plan** — per-set snapshots narrow the window relative to today's single whole-run snapshot but do not close it. Not introduced here and not fixed here; `/update` runs are already serialized by the update lock in practice. Recorded so a reviewer does not mistake it for new.

### Race 2: The gate observes a half-synced venv
**Location:** `scripts/update/deps.py::sync_with_uv` → `run_smoke_test`
**Trigger:** `uv sync` returns before `uv pip install -e .` completes, or the gate's subprocess starts against a venv mid-rewrite.
**Data prerequisite:** The gate must import the *new* pins.
**State prerequisite:** `sync_dependencies` fully returned.
**Mitigation:** Already structurally satisfied — `sync_with_uv` runs both commands synchronously via `run_cmd` and the gate is called after it returns a success result. The gate additionally prints the resolved `anthropic.__version__` so a stale-venv read is visible in the output rather than silent.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3001] **The dependency upgrade itself** — `pydantic-ai-slim` → 2.33.0+, `anthropic` → 1.x, `openai` 2.x → 3.x, and exercising the modules that import `openai` directly (`tools/impact_finder_core.py`, `tools/cross_vendor_judge.py`, `tools/selfie/__init__.py`). This is Step 2 of the sequencing agreed in issue comment 5420111955 and must run *behind* the gate this plan builds, in its own lane. Doing it here would be a three-way major bump with no working gate underneath — the exact shape of the change that caused this incident.
- [SEPARATE-SLUG #3001] **Work Item 2 — the dead `worker_key` regression guard.** Root-caused in the issue comments to `69dc69568`/#2949: `_make_session` passes `stage_states=` as a constructor kwarg that Popoto now silently drops. The human's sequencing note marks it explicitly not urgent and to be sequenced after Step 1. It shares no files with this lane (`tests/unit/test_agent_session.py`, `models/agent_session.py` vs. `scripts/update/deps.py`), so it is cleanly separable. The issue comments already carry the implementation gotchas (widen the `save` stub to `lambda self, **kwargs: None`; the blast radius is 11 tests not 9; do not restore the kwarg mapping — #2949 pinned its removal).
- [SEPARATE-SLUG #3001] **Work Item 3 — duplicate/noisy triage filing.** Idempotent issue filing, root-cause collapsing, environmental-failure classification. Also marked not urgent, also file-disjoint from this lane.
- [SEPARATE-SLUG #3016] **The `test_promise_gate_real_api` failure** — independent root cause, already filed.
- [ORDERED] **Gating the non-auto-bump paths onto pin changes** (hand edits, merged PRs, `uv lock --upgrade`) — see Risk 4. Needs a supervisor routing decision on where such a check belongs (pre-push hook vs. `/update` verify step vs. CI) before it can be scoped; raised as Open Question 3.
- [SEPARATE-SLUG #3001] **Auditing the remaining `CRITICAL — pin exact` deps for staleness** (`telethon`, `claude-agent-sdk`). It is an acceptance criterion of #3001 but belongs with Step 2's upgrade work, where the findings can actually be acted on.

## Update System

This work **is** an update-system change — it modifies `scripts/update/deps.py`,
which `/update` Step 3.5 drives.

- `scripts/update/deps.py` — the substantive change (coupled sets, declaration-aware pin helpers, real-call gate, per-set rollback).
- `scripts/update/run.py` — minimal: surface the gate's phase marker in the rolled-back warning detail. The commit/push/restart ordering documented in `sdlc-1091.md` is **not** touched.
- `.claude/skills/update/SKILL.md` lines 66-72 describe the auto-bump flow ("checks PyPI for newer `anthropic` and `claude-agent-sdk` versions... runs a smoke test (import check + pytest)"). That description becomes wrong on both counts and must be updated in the same change.
- **No new config files or env keys.** `ANTHROPIC_API_KEY` is already declared and required.
- **No migration for existing installations.** Follower machines never run auto-bump (`is_lockfile_maintainer` gate at `run.py:1169`); they only consume the pushed lockfile. The change is inert on every machine but one.
- `pyproject.toml` pin changes (Phase 0's `anthropic` revert, Phase 1's `openai` exact pin) propagate to the fleet through the normal `uv sync --frozen` path on the next `/update`. Both are pin-only; no new packages.

## Agent Integration

**No agent integration required** — this is update-system-internal. No new CLI
entry point in `[project.scripts]`, no MCP surface, no bridge import. The gate is
invoked only by `auto_bump_deps` on the maintainer machine, and its sole
operator-facing output is the existing `/update` warning channel, which the agent
already reads via `extract_update_warnings`.

The one integration-shaped constraint: the gate calls `agent/llm/wrapper.py::run_typed`
across a subprocess boundary into the project venv. Assert that coupling
mechanically (grep in Verification) so a future refactor that stops exercising
`run_typed` — and quietly reverts the gate to an import check — is caught.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/coupled-dependency-bump-gate.md` — the coupled-set model, why `anthropic`/`pydantic-ai-slim`/`openai` are welded together (with the `pydantic-ai-slim>=2.33.0` boundary from Research), what the real-call gate checks and cannot check, the fail-closed policy, and how to add a new coupled set.
- [ ] Add a row to `docs/features/README.md` index table.

### Existing Docs to Correct
- [ ] `.claude/skills/update/SKILL.md` lines 66-72 — the auto-bump description names only `anthropic` + `claude-agent-sdk` and describes the smoke test as "import check + pytest". Both become wrong.
- [ ] `pyproject.toml` — replace the stale `anthropic` comment block (which forbids the pin directly beneath it) with a pointer to the coupled-set declaration in `deps.py`.
- [ ] `docs/features/remote-update.md` — check for an auto-bump description; add a cross-reference to the new feature doc.

### Inline Documentation
- [ ] The coupled-set declaration carries a prose reason per set, replacing the stopgap comment block currently sitting above `AUTO_BUMP_PACKAGES`.
- [ ] Docstring on the gate function stating explicitly that it makes a real, billed API call and why a mock cannot substitute (per the repo's testing philosophy and the issue's planner constraint).

## Success Criteria

- [ ] A real `run_typed` call succeeds on `main`'s pins — i.e. the spike-1 probe prints `OK` rather than `TypeError` (Phase 0).
- [ ] `anthropic`, `pydantic-ai-slim`, and `openai` are declared as one coupled set, and `anthropic` is back in the auto-bump path.
- [ ] `openai` carries an exact pin at its currently-resolved version, with no other `uv.lock` change.
- [ ] The three spike-2 pin-helper defects each have a regression test that fails against the current implementation.
- [ ] Staging the known-bad pair (`anthropic==1.0.0` + `pydantic-ai-slim==2.9.0`) through a real `auto_bump_deps` run produces an observed rollback, with the transcript captured in the PR description. Not a mock.
- [ ] A failed LLM set does not roll back a successful `claude-agent-sdk` bump in the same cycle.
- [ ] A gate that cannot run (no key / no venv / timeout) rolls back and emits a distinct warning.
- [ ] `/update`'s rolled-back warning names which gate phase failed.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `scripts/update/deps.py` references `run_typed` (grep-confirmed — the Agent Integration anti-regression).

## Team Orchestration

### Team Members

- **Builder (deps)**
  - Name: `deps-builder`
  - Role: coupled-set declaration, declaration-aware pin helpers, per-set rollback, the real-call gate
  - Agent Type: builder
  - Resume: true

- **Test engineer (deps)**
  - Name: `deps-tester`
  - Role: regression tests for the three pin-helper defects, set-atomicity tests, gate failure-path tests, and driving the real known-bad-pair rollback verification
  - Agent Type: test-engineer
  - Resume: true

- **Validator (deps)**
  - Name: `deps-validator`
  - Role: verifies set atomicity, fail-closed behavior, and that the gate genuinely exercises `run_typed`
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `deps-documentarian`
  - Role: feature doc, README index row, `update/SKILL.md` correction, `pyproject.toml` comment repair
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Restore a working LLM stack on main
- **Task ID**: `build-phase0-restore-pin`
- **Depends On**: none
- **Validates**: manual probe (spike-1 script must print `OK`)
- **Informed By**: Freshness Check (Major drift), spike-1
- **Assigned To**: `deps-builder`
- **Agent Type**: builder
- **Parallel**: false
- First re-check whether a hotfix already landed the revert; if `anthropic==0.125.0` is on `main`, verify and skip the rest of this task.
- Revert the `anthropic` pin in `pyproject.toml` to `0.125.0`, re-applying `7db5b82bb`'s intent.
- Repair the stale comment block above the pin so the file no longer contradicts itself.
- `uv sync --all-extras` (unfrozen) and commit the regenerated `uv.lock` alongside.
- Re-run the spike-1 `run_typed` probe and paste the `OK` output into the commit body.
- This is a checkpoint commit — push it before starting task 2.

### 2. Declaration-aware pin helpers
- **Task ID**: `build-pin-helpers`
- **Depends On**: `build-phase0-restore-pin`
- **Validates**: `tests/integration/test_remote_update.py::TestGetPinnedVersion`, new pin-helper tests
- **Informed By**: spike-2 (all three defects, with reproductions)
- **Assigned To**: `deps-builder`
- **Agent Type**: builder
- **Parallel**: false
- Make the pin reader locate a package's actual dependency declaration rather than substring-matching whole lines including comments.
- Make the pin writer tolerate extras markers (`pydantic-ai-slim[anthropic]==...`).
- Make both refuse loudly (return `None` / `False` with a distinguishable reason) rather than silently no-op when no declaration is found.
- Give `openai` an exact pin at its currently-resolved version; confirm `uv.lock` shows no change beyond that line.

### 3. Coupled sets and per-set atomic rollback
- **Task ID**: `build-coupled-sets`
- **Depends On**: `build-pin-helpers`
- **Validates**: new set-atomicity tests
- **Informed By**: spike-4 (rollback is whole-file today)
- **Assigned To**: `deps-builder`
- **Agent Type**: builder
- **Parallel**: false
- Replace the flat `AUTO_BUMP_PACKAGES` with a set-aware declaration; `["anthropic", "pydantic-ai-slim", "openai"]` and `["claude-agent-sdk"]`, each carrying a prose reason.
- All-or-nothing resolve: any member without a resolvable latest version skips the whole set.
- All-or-nothing rewrite: any failed member rewrite restores the set snapshot and abandons the set immediately.
- Per-set snapshot/restore replacing the single whole-run `original_content` snapshot.
- Extend `AutoBumpResult` with per-set bookkeeping so `run.py` can report which set rolled back.

### 4. The real-call LLM gate
- **Task ID**: `build-llm-gate`
- **Depends On**: `build-coupled-sets`
- **Validates**: new gate failure-path tests
- **Informed By**: spike-3 (key resolves; must run in the target venv), Research (transport breaks too)
- **Assigned To**: `deps-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add a gate phase ahead of the import check that shells into `{project_dir}/.venv/bin/python` and runs a minimal `run_typed` call with a one-field output model.
- Bound it with a subprocess timeout in addition to `run_typed`'s own double timeout.
- Fail closed: no key, no venv python, timeout, or any exception → gate fails → set rolls back, with a distinct message.
- Carry a phase marker (llm / import / pytest) in the result so the operator can tell the cases apart.
- Have the gate print the resolved `anthropic.__version__` so a stale-venv read is visible.
- Surface the phase marker in `run.py`'s rolled-back warning detail; do not touch the commit/push/restart ordering.
- Re-add `anthropic` to the auto-bump path in this same task — never as a separate earlier commit.

### 5. Tests
- **Task ID**: `build-tests`
- **Depends On**: `build-llm-gate`
- **Validates**: `tests/integration/test_remote_update.py`
- **Assigned To**: `deps-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Update the three existing tests per Test Impact.
- Add the six new tests named in Test Impact.
- For each of the three spike-2 defects, confirm the new test **fails** against the pre-fix helper before it passes against the fixed one; record that red-state proof.
- Do not mock the LLM gate in the test that proves rollback-on-incompatible-pair — that one is task 6.

### 6. Real known-bad-pair rollback verification
- **Task ID**: `verify-known-bad-rollback`
- **Depends On**: `build-tests`
- **Assigned To**: `deps-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- On a throwaway copy of the repo (never the shared checkout), stage `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0`, run `auto_bump_deps` for real with a real sync, and capture the full transcript.
- Assert the observed outcome: gate fails at the `llm` phase with the `TypeError` at argument binding, the set's pins are restored, and `rolled_back` is set.
- Assert the converse on a good pair: the gate passes and the bump survives.
- Paste both transcripts into the PR description. This is the acceptance criterion the issue calls out as un-mockable.

### 7. Documentation
- **Task ID**: `document-feature`
- **Depends On**: `verify-known-bad-rollback`
- **Assigned To**: `deps-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/coupled-dependency-bump-gate.md` and add the `docs/features/README.md` index row.
- Correct `.claude/skills/update/SKILL.md` lines 66-72.
- Cross-reference from `docs/features/remote-update.md`.

### 8. Final validation
- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Assigned To**: `deps-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each Success Criterion, including that the two transcripts from task 6 are in the PR description.
- Confirm the PR body says `Refs #3001`, **not** `Closes #3001` — Work Items 2 and 3 keep the issue open.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Live LLM call works on the branch's pins | `.venv/bin/python -c "import asyncio;from pydantic import BaseModel;from agent.llm.wrapper import run_typed;O=type('O',(BaseModel,),{'__annotations__':{'answer':str}});print(asyncio.run(run_typed('Reply with answer=hi',O)))"` | exit code 0 |
| Gate exercises `run_typed`, not just imports | `grep -c "run_typed" scripts/update/deps.py` | output > 0 |
| Import-only gate is gone | `grep -c "import anthropic; import claude_agent_sdk" scripts/update/deps.py` | match count == 0 |
| `anthropic` is back in auto-bump | `grep -c "anthropic" scripts/update/deps.py` | output > 0 |
| `openai` has an exact pin | `grep -cE '"openai==' pyproject.toml` | output > 0 |
| Stale self-contradicting pin comment removed | `grep -c "Do NOT move to 1.x" pyproject.toml` | match count == 0 |
| Update-system tests pass | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Anti-criterion: no dependency upgrade smuggled in | `grep -cE '"pydantic-ai-slim\[anthropic\]==2\.9\.0"' pyproject.toml` | output > 0 |
| Anti-criterion: Work Item 2 files untouched | `git diff --name-only origin/main...HEAD -- models/agent_session.py tests/unit/test_agent_session.py \| wc -l` | output contains 0 |
| Update skill doc corrected | `grep -c "import check" .claude/skills/update/SKILL.md` | match count == 0 |
| Feature doc exists | `test -f docs/features/coupled-dependency-bump-gate.md` | exit code 0 |

## Critique Results

Round 1 — FULL depth (force-FULL: the plan edits `.claude/skills/update/SKILL.md`, a doctrine path).
Verdict: **NEEDS REVISION** — 3 blockers, 5 concerns, 3 nits.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The flagship un-mockable acceptance criterion is unreachable, because coupling works. PyPI latest right now is `anthropic 1.0.0`, `pydantic-ai-slim 2.35.0`, `openai 3.3.1`. An all-or-nothing coupled bump resolves every member to latest, producing `anthropic 1.0.0` + `pydantic-ai-slim 2.35.0` — a pair the plan's own Research section calls compatible (`>=2.33.0` is the first release supporting `anthropic>=1.0.0`). Staging the bad pair and then running `auto_bump_deps` "for real with a real sync" bumps straight past it: gate passes, bump survives, no rollback observable. Task 6 and the matching Success Criterion cannot be satisfied as written. | pending | `get_pypi_latest(package, timeout=10)` (`scripts/update/deps.py:355-388`) is the single resolution seam. Split the criterion into two named legs. Leg (a), genuinely unmocked: write `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0` into a throwaway checkout, `uv sync --all-extras`, invoke the gate subprocess directly, assert exit != 0 and `unexpected keyword argument 'temperature'` in stderr. Leg (b), resolution-stubbed: `monkeypatch.setattr(deps, "get_pypi_latest", lambda p, **k: {"anthropic": "1.0.0", "pydantic-ai-slim": "2.9.0"}[p])`, assert `result.rolled_back is True` and both pins restored. Say explicitly that leg (b) stubs resolution instead of claiming the whole path is unmocked. |
| BLOCKER | Scope & Value | `openai` is welded into the coupled set on a coupling that does not exist here, and enrolling it in auto-bump silently executes the upgrade the plan declares out of scope. `uv.lock` shows `pydantic-ai-slim`'s `anthropic` optional-dependency group requires only `anthropic`; `openai` is a separate direct dependency for the embedding API, and the plan's own quoted `pyproject.toml` comment says the `pydantic-ai-slim[anthropic]` pin exists precisely to avoid the openai extra. Re-adding the set to auto-bump means the first cron `/update` after merge attempts `openai 2.30.0 → 3.3.1` unattended and all-or-nothing with anthropic — exactly the "openai 2.x → 3.x" move listed under No-Gos as Step 2's work. | pending | Drop `openai` from the set; the real set is `["anthropic", "pydantic-ai-slim"]`. Declare `AUTO_BUMP_SETS = [CoupledSet(["anthropic", "pydantic-ai-slim"], reason="anthropic>=1.0.0 dropped temperature/top_p/top_k; pydantic-ai-slim<2.33.0 passes them unconditionally"), CoupledSet(["claude-agent-sdk"], reason="...")]`. Keep the exact pin `"openai==2.30.0"` in `pyproject.toml` (spike-2's defect is real) but leave it out of every set, and add `assert "openai" not in {m for s in AUTO_BUMP_SETS for m in s.members}` with the reason in its docstring so a future reader does not re-add it. This also removes the hazard where an unresolvable `openai` version blocks a needed `anthropic` + `pydantic-ai-slim` bump. |
| BLOCKER | History & Consistency | The verification for this plan's central deliverable is a gate that cannot fire. `grep -c "anthropic" scripts/update/deps.py` returns **9** on current main — while `AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]` (`deps.py:329`) explicitly excludes anthropic; the nine matches are the stopgap comment block at `deps.py:313-328` that exists to explain the exclusion. The row passes today, in the exact state the plan exists to change, and would keep passing if the builder deleted the coupled-set work and left the comment. The companion row `grep -c "run_typed" scripts/update/deps.py > 0` has the same defect and is designated the Agent Integration anti-regression. | pending | Assert the declaration and the executed path, not file text. Membership row → `.venv/bin/python -c "from scripts.update.deps import AUTO_BUMP_SETS; ms={m for s in AUTO_BUMP_SETS for m in s.members}; assert {'anthropic','pydantic-ai-slim'} <= ms, ms"`, expected exit 0. Gate-invocation row → a unit test that monkeypatches `deps.run_cmd` and asserts the llm phase's argv contains `run_typed` and that `run_smoke_test` returns phase `"llm"` on failure; a grep count cannot distinguish an invocation from a mention. Keep the negative row (`grep -c "import anthropic; import claude_agent_sdk"` == 0) — that one can genuinely fail. |
| CONCERN | Risk & Robustness | The Flow places `GATE phase 0: real run_typed call` inside the per-set loop, so every set — including `["claude-agent-sdk"]`, which has nothing to do with the LLM wrapper — rolls back whenever the LLM gate cannot run. Risk 2 accepts fail-closed for the LLM set specifically, but the design widens that acceptance to every current and future set: a lapsed key or provider outage freezes all fleet dependency updates, undoing the plan's own stated benefit from the other direction. | pending | Make gate phases a property of the set declaration: `CoupledSet(members=[...], reason="...", gates=("llm", "import", "pytest"))`, with `run_smoke_test(project_dir, phases: tuple[str, ...])` skipping un-requested phases. Default `gates` to `("import", "pytest")` so a newly added set never silently inherits a billed API call or the Anthropic-availability dependency; assert that default in a unit test. |
| CONCERN | Risk & Robustness | The plan specifies per-set snapshot/restore of `pyproject.toml` but never states what leaves `uv.lock` consistent when the restore sync itself fails. `auto_bump_deps` (`deps.py:520-545`) rolls back by rewriting `pyproject.toml` and calling `sync_dependencies(project_dir, frozen=False)` while discarding that call's return value. If it fails, `uv.lock` describes the bumped pins while `pyproject.toml` describes the old ones; `run.py`'s rolled-back branch does not commit, so the divergence survives into the next cron cycle where a later successful bump does `git add pyproject.toml uv.lock` and pushes the poisoned lockfile fleet-wide. | pending | Capture the restore result — `restore = sync_dependencies(project_dir, frozen=False)` — and on `not restore.success` set a new `AutoBumpResult.restore_failed = True` and run `run_cmd(["git", "checkout", "--", "uv.lock"], cwd=project_dir, check=False)`. Guard `run.py` Step 3.5's commit branch with `and not bump.restore_failed`. Add a test asserting `git status --porcelain pyproject.toml uv.lock` is empty after every rollback path. |
| CONCERN | Scope & Value | The gate's coverage is narrower than the set it is described as protecting. `run_typed` (`agent/llm/wrapper.py:84-140`) constructs an `anthropic.AsyncAnthropic` client and nothing else. The three modules that import `openai` — `tools/impact_finder_core.py`, `tools/cross_vendor_judge.py`, `tools/selfie/__init__.py` — are exercised by none of the three gate phases. If `openai` stays in the set, the plan ships atomicity with zero verification for a third of it, and the feature doc would document protection that does not exist. | pending | Preferred: drop `openai` from the set (see the Scope & Value blocker). If it stays, extend the import phase to `import anthropic; import claude_agent_sdk; import openai; from openai import OpenAI; OpenAI(api_key="x").embeddings` — construct-only, no network, mirroring `tools/impact_finder_core.py`'s call shape. Either way, the "what the real-call gate checks and cannot check" bullet already promised in Documentation must name the uncovered members rather than describing the gate as covering "the coupled set". |
| CONCERN | History & Consistency | The Major-drift premise is already resolved on main and the plan body no longer describes reality. `7a30b88f7 fix(deps): re-revert anthropic to 0.125.0 — bad pin rode into d0c02bde5` has landed; `pyproject.toml:12` reads `"anthropic==0.125.0"` and the venv resolves anthropic 0.125.0. The plan anticipated this and Open Question 1 recommended it, so the disposition was right — but the body still asserts as present-tense fact that "main is currently pinned to the known-bad pair" and "every non-harness LLM call on main is dead right now", and task 1 is still written as edit-then-checkpoint-commit-then-push. A builder reading top-down will revert a pin that is already correct and push a no-op commit. | pending | Rewrite task 1 as pure verification: `grep -q '"anthropic==0.125.0"' pyproject.toml` and re-run the spike-1 probe expecting `OK` — no edit, no commit, no push, and no `Depends On` change for task 2. Update the Freshness Check row for `pyproject.toml:12` from **DRIFTED** to `RESOLVED by 7a30b88f7`, and mark Open Question 1 resolved. Date-stamp the drift narrative as superseded rather than deleting it, so Prior Art and Risk 1 stay legible. |
| CONCERN | History & Consistency | The stated rationale for deleting the `pyproject.toml` comment block no longer holds, but the Verification row still mandates the deletion. Lines 8-11 read "Do NOT move to 1.x while pydantic-ai-slim is on 2.9.0" directly above `"anthropic==0.125.0"` — with the hotfix landed that is a correct, load-bearing constraint, not a self-contradiction. The table nevertheless requires `grep -c "Do NOT move to 1.x" pyproject.toml` → 0. Combined with the anti-criterion pinning `pydantic-ai-slim[anthropic]==2.9.0` for this lane, deleting the warning strips the only in-file guard against a hand-edit repeating `9d1488ccb` while the lane is in flight — the very path Risk 4 says the new gate does not cover. | pending | Replace the four-line block with two lines that keep the constraint and add the pointer: `# CRITICAL — coupled set: anthropic + pydantic-ai-slim move together.` and `# anthropic>=1.0.0 requires pydantic-ai-slim>=2.33.0. See AUTO_BUMP_SETS in scripts/update/deps.py.` Change the Verification row to `grep -c "AUTO_BUMP_SETS" pyproject.toml` → output > 0, and drop the `"Do NOT move to 1.x"` → 0 row; it tests prose wording, not a constraint. |
| NIT | Scope & Value | Moving from one whole-run sync to per-set sync-gate-rollback multiplies `uv sync --all-extras` + `uv pip install -e .` by the number of sets, plus one restore sync per failed set — up to four full syncs per cron tick with two sets. The plan's own rabbit hole rejects a slow gate ("not a gate, it is an outage") but does not budget the sync fan-out it introduces. | pending | Note the expected `/update --cron` wall-clock delta in the feature doc, and skip a set's sync entirely when no member's pin actually changed. |
| NIT | Structural check | `agent/routing.py`, cited in Problem and in Data Flow as a `run_typed` caller, does not exist. The routing caller is `bridge/routing.py`. | pending | Substitute `bridge/routing.py`. The full non-harness caller set on main is `bridge/routing.py`, `bridge/job_router.py`, `bridge/context_recall.py`, `bridge/injection_inspection.py`, `bridge/agent_catchup.py`, `agent/memory_extraction.py`, `agent/intent_classifier.py`, `tools/classifier.py`, `tools/email_cs/triage.py`. |
| NIT | Structural check | Tasks 6, 7, and 8 carry no `Validates:` field, unlike tasks 1-5. Task 6 is the acceptance-critical one. | pending | Give task 6 `Validates: the two captured transcripts (leg a real, leg b resolution-stubbed)`, task 7 `Validates: docs/features/coupled-dependency-bump-gate.md exists and is indexed`, task 8 `Validates: the Verification table`. |

**Structural check results**

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation (with a `docs/features/` checkbox), Update System, Agent Integration, Test Impact all present and substantive |
| Popoto migration check | N/A | No Popoto model touched |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | Linear chain, every `Depends On` resolves; no cycles |
| Task validation commands | FAIL | Tasks 6, 7, 8 carry no `Validates:` field (nit above) |
| File paths exist | FAIL | 17 of 18 referenced existing-file paths resolve; `agent/routing.py` does not exist (nit above). `docs/features/coupled-dependency-bump-gate.md` is intentionally new. |
| Prerequisites met | PASS | `ANTHROPIC_API_KEY` resolves; `uv 0.6.10` on PATH; `get_pypi_latest('anthropic')` returns a version |
| Cross-references | FAIL | Success criterion "Staging the known-bad pair ... produces an observed rollback ... Not a mock" is unsatisfiable (blocker 1); "anthropic is back in auto-bump" maps to a verification that already passes (blocker 3) |
| No-Go vs. Solution | FAIL | No-Go "Do not attempt the upgrade while we're in here" is contradicted by the Solution enrolling `openai` (and `anthropic`) in unattended auto-bump (blocker 2) |


---

## Open Questions

1. **Phase 0 — hotfix now, or wait for this lane's PR?** Every non-harness LLM call on `main` is dead at `d0c02bde5` (verified live). The fix is a one-line pin revert plus `uv sync`. Waiting for this lane means the fleet runs with a broken LLM layer for the duration of the build. Recommendation: land it as a hotfix on `main` immediately (`Refs #3001`), and let task 1 degrade to a verification step.

2. **Fail-closed on an unavailable gate — confirm the policy.** This plan rolls back the bump when the gate *cannot run* (no API key, provider outage, timeout), not only when it fails. That means a persistent Anthropic outage silently freezes fleet dependency updates behind a `/update` warning. The alternative — skip the gate and let the bump through — is what produced this issue. Confirming fail-closed is the intended tradeoff.

3. **Should the non-auto-bump paths be gated too, and where?** (Risk 4.) The bad pin reached `main` through a hand-staged `git add pyproject.toml`, not through auto-bump — so this plan's gate would not have caught the drift documented in the Freshness Check. A durable fix keys on "the lockfile changed" rather than "auto-bump ran", which could live in a pre-push hook, an `/update` verify step, or CI. Out of scope here; needs a routing decision before it can be scoped as its own lane.

4. **Confirm Work Items 2 and 3 stay deferred.** The human's sequencing note marks both "not urgent, sequence after Step 1", and this plan defers both with `[SEPARATE-SLUG #3001]` tags rather than splitting them into new issues. Confirm that keeping them on #3001 (which therefore stays open after this lane merges, hence `Refs` not `Closes`) is preferred over filing two follow-up issues now.
