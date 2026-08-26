---
status: Ready
type: bug
appetite: Medium
owner: valor
created: 2026-08-26
revision_applied: true
revision_applied_at: 2026-08-26T08:19:16Z
tracking: https://github.com/tomcounsell/ai/issues/3001
last_comment_id: 5420202999
---

# Run-boundary LLM-stack compatibility gate + coupled-set dependency bumping

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

Then it happened a *second* time, by a different route: `d0c02bde5` swept an
already-staged auto-bump re-application into a hand-authored commit and put the
bad pin back on `main` with no auto-bump involved at all. `7a30b88f7` reverted it
again.

That second event is the shape of the defect. **The incompatible pin does not
arrive through one path, so the check cannot live on one path.** Two of the three
times the bad pair reached a running process, `auto_bump_deps` was nowhere in the
picture — the pin arrived through `git add pyproject.toml`. A gate scoped inside
`auto_bump_deps` would have watched both of those go by.

**Current behavior:**

- **Nothing anywhere asserts that the installed `anthropic` and `pydantic-ai-slim`
  are a compatible pair.** Not `/update` verify, not bridge startup, not worker
  startup. A process can boot cleanly on a stack where every non-harness LLM call
  raises `TypeError` at argument binding, and does not find out until a caller
  tries — which on 2026-08-24 was six hours later.
- `scripts/update/deps.py::run_smoke_test` phase 1 is
  `import anthropic; import claude_agent_sdk`, phase 2 is one fast pytest file
  (`tests/unit/test_docs_auditor_substrate.py`). Neither makes an LLM call, so
  neither can catch an argument-binding break in the LLM layer.
- `scripts/update/deps.py::auto_bump_deps` iterates `AUTO_BUMP_PACKAGES`
  (`deps.py:329`, currently `["claude-agent-sdk"]`) one package at a time. There
  is no notion of packages that must move together, so a member of a coupled set
  can advance alone. `anthropic` is excluded by the `d0c02bde5` stopgap, which
  deliberately leaves us stale.
- `agent/llm/wrapper.py:48` has an **unguarded module-scope**
  `from pydantic_ai.models.openai import OpenAIChatModel`. Since
  `agent/llm/__init__.py` re-exports from `wrapper`, that import runs on every
  `import agent.llm`. It is a self-inflicted coupling — see Spike Results spike-5.
- The pin-editing helpers cannot even express a coupled set correctly:
  `get_pinned_version("openai")` returns a version scraped out of a *comment*,
  and `bump_pin_in_pyproject("pydantic-ai-slim", ...)` silently returns `False`
  because the pin carries an extras marker. Both verified in spike-2.

**Desired outcome:**

- A single standalone predicate, `check_llm_stack_compat()`, answers "is the
  installed LLM stack a pair we can actually call?" — and it is invoked at the
  **run boundary**: `/update` verify, bridge startup, worker startup, *and*
  auto-bump. Whatever route a bad pin takes, the next thing that starts sees it.
- On an incompatible stack, services **start degraded and alert loudly**.
  Telegram keeps receiving and queueing. LLM-dependent paths fail fast with a
  typed error instead of a provider `TypeError`. The condition is alarmed on
  channels that do not touch the broken LLM path.
- `anthropic` + `pydantic-ai-slim` are declared as one coupled set and are bumped
  atomically or not at all — a partial advance is structurally impossible.
  `openai` is **not** in the set (spike-5).
- `anthropic` is back in `AUTO_BUMP_PACKAGES` (as a member of its set), landing in
  the same change as the gate that makes it safe.
- The rollback behavior is verified by observation on a deliberately staged
  known-bad pair, not asserted in a mock.

This plan is **Step 1 of the sequencing agreed in
[issue comment 5420111955](https://github.com/tomcounsell/ai/issues/3001#issuecomment-5420111955)**:
gate first, upgrade second. The dependency upgrade itself (including the
`openai` 2.x → 3.x major bump) is explicitly **not** in this lane.

## Freshness Check

**Baseline commit:** `9a8df7039` (re-verified at revision time, 2026-08-26).
Original planning baseline was `d0c02bde54ddb187ea9a536767f257907f5258fc`.
**Issue filed at:** 2026-08-25T05:16:36Z
**Disposition:** **Minor drift** at revision time. The original **Major drift**
(main pinned to the known-bad pair) was surfaced to the supervisor and has since
been **resolved on main by hotfix `7a30b88f7`**. The narrative below is retained
because Prior Art and Risk 1 depend on it; it is superseded, not deleted.

### The original drift, and its resolution

*Recorded 2026-08-26, superseded the same day.* The issue and the stopgap commit
message both describe `7db5b82bb` as having reverted `anthropic` to `0.125.0`.
`d0c02bde5` ("stop auto-bumping anthropic — it is half of a coupled set") swept
the already-staged auto-bump re-application into its own commit, putting
`anthropic==1.0.0` back on `main`:

```
$ git show d0c02bde5 -- pyproject.toml
-    "anthropic==0.125.0",
+    "anthropic==1.0.0",
```

Verified live against the venv at that baseline:

```
anthropic 1.0.0 / pydantic-ai-slim 2.9.0 / openai 2.30.0
temperature in AsyncMessages.create signature: False
FAIL LLMCallError run_typed failed for model=claude-haiku-4-5-20251001:
     AsyncMessages.create() got an unexpected keyword argument 'temperature'
```

**Resolution:** `7a30b88f7 fix(deps): re-revert anthropic to 0.125.0 — bad pin
rode into d0c02bde5` landed on `main`. `pyproject.toml:12` now reads
`"anthropic==0.125.0"` and the venv resolves 0.125.0. Original Open Question 1
recommended exactly this hotfix; it is answered.

**Consequence for this plan:** Phase 0 is now a **verification step, not an
edit** — task 1 asserts the good pin and a passing probe, and makes no commit.
More importantly, this second bad-pin arrival is now first-class evidence in the
Problem statement: it reached `main` through a hand-staged `git add`, which is
why the compatibility check belongs at the run boundary rather than inside
`auto_bump_deps`.

### File:line references re-verified

| Reference | Issue's claim | Status |
|---|---|---|
| `scripts/update/deps.py:329` | `AUTO_BUMP_PACKAGES` excludes `anthropic` | **Holds** — `AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]` |
| `scripts/update/deps.py:414` | `run_smoke_test` is import-only + one pytest file | **Holds** |
| `scripts/update/deps.py:474` | `auto_bump_deps` iterates packages independently | **Holds** (`for package in AUTO_BUMP_PACKAGES:`) |
| `scripts/update/run.py:1304` | `/update` Step 3.5 calls `auto_bump_deps`, maintainer-only | **Holds** (gate at `run.py:1169`, `is_lockfile_maintainer`) |
| `agent/llm/wrapper.py::run_typed` | the single funnel for non-harness LLM calls | **Holds** |
| `agent/llm/wrapper.py:48` | — (found during revision) | **New** — unguarded `from pydantic_ai.models.openai import OpenAIChatModel` at module scope |
| `agent/llm/wrapper.py:213` | — (found during revision) | **New** — the single `OpenAIChatModel(...)` use site, inside `run_typed_local`, against `OllamaProvider` |
| `pyproject.toml:12` | `anthropic` pinned to the reverted 0.125.0 | **RESOLVED by `7a30b88f7`** (was DRIFTED at first planning pass) |

### Cited sibling issues/PRs re-checked

- **#2932** — closed, folded into #3001. Its scope lands in Work Item 3, deferred out of this lane.
- **#2960–#2999** — all closed as duplicates. Their shared-root-cause diagnosis of the worker-key failures is wrong; not relied on here.
- **#2949** / `69dc69568` — merged; owns Work Item 2. Deferred out of this lane.
- **#3016** — the independent `test_promise_gate_real_api` failure, separately filed. Not in scope.
- **#2334** — deliberately left open, separate scope.

### Commits on main since the issue was filed (touching referenced files)

- `7db5b82bb` — reverted anthropic to 0.125.0. Superseded by `d0c02bde5`, then re-established.
- `53cec47de` — removed the valor CLI wrapper; touched `pyproject.toml` scripts table only. Irrelevant.
- `d0c02bde5` — the stopgap. Also re-shipped the bad pin (the second arrival).
- `7a30b88f7` — the re-revert. Restores a working pair; retires original Open Question 1.

### Active plans in `docs/plans/` overlapping this area

None. `grep -l "auto_bump\|anthropic" docs/plans/*.md` matches only
`docs-auditor-review-gate.md` and `overclaim-guard-greps-whole-worktree.md`,
both incidental mentions with no overlap on `scripts/update/deps.py`.

## Prior Art

- **#3001 stopgap `d0c02bde5`** — removed `anthropic` from `AUTO_BUMP_PACKAGES`. Bought time; is the thing this plan replaces. It also *itself* re-shipped the bad pin, making it the strongest single argument for a run-boundary check.
- **`7db5b82bb`** and **`7a30b88f7`** — two separate emergency reverts of the same pin, eleven commits apart. Direct evidence that a one-off manual revert is not a durable fix and that a *standing* check is the real deliverable.
- **`9d1488ccb`** — `chore(deps): commit auto-bump anthropic 1.0.0`. The breaking bump. Landed through the existing smoke gate cleanly, which is the whole indictment.
- **`884302861`** — "Harden dependency management with tiered pinning and two-speed updates". Introduced `AUTO_BUMP_PACKAGES`, the `CRITICAL — pin exact` tier, and the smoke-test-then-rollback shape. The scaffolding is right; it only lacks coupling and a call-level gate.
- **PR #1696** — `deps(#1653): bump popoto >=1.7.1`. A hand-driven dependency bump through the SDLC pipeline. Confirms the repo's normal posture is exact pins moved deliberately, which is what coupled sets formalize.
- **`docs/archive/plans-completed/sdlc-1091.md`** — documents that `auto_bump_deps` commits and pushes *during* the cron `/update` run, and that the restart gate reads HEAD after `run.py --cron` returns. Any change to auto-bump's commit behavior must preserve that ordering.
- **`agent/index_drift.py:224`** — the repo's existing `sentry_sdk.capture_message` pattern, including the "capture failed" fallback log. The degraded-start alert follows this shape rather than inventing one.

No prior attempt at a run-boundary compatibility check, coupled-set bumping, or a
call-level gate exists. There is no "Why Previous Fixes Failed" section because
the only prior fixes were scoped as holding actions and are not claimed to have
addressed the shape of the defect.

## Research

**Queries used:**
- `pydantic-ai anthropic 1.0 temperature top_p removed Messages API compatibility fix release`

**Key findings:**

- **`pydantic-ai-slim>=2.33.0` is the first release that supports `anthropic>=1.0.0`.** Every release before it — including 2.32.2, cut hours earlier — allowed `anthropic 1.0.0` in its metadata without supporting it. The fix landed as "Use httpx2 for Anthropic clients". Sources: [pydantic-ai changelog](https://ai.pydantic.dev/changelog), [Anthropic Python SDK v1.0 migration](https://www.digitalapplied.com/blog/anthropic-python-sdk-v1-breaking-change-migration). **Informs the plan:** this is the exact version boundary the coupled set exists to enforce, and it is the number Step 2 will target. It also confirms the upper-bound hole is upstream metadata we do not control — a local check is the only remedy available to us.
- **anthropic 1.0.0 (2026-08-20) also moved its HTTP layer from `httpx` to `httpx2`**, and `AnthropicProvider(http_client=...)` now rejects legacy `httpx.AsyncClient`. **Informs the plan:** a future coupled bump can break `run_typed` through the transport as well as through argument binding — another failure mode an import check cannot see, and one a real call does catch. `agent/llm/wrapper.py` constructs `anthropic.AsyncAnthropic(...)` directly and does not pass its own `http_client`, so it is not exposed to that specific break today.
- **Anthropic additionally deprecated non-default `temperature`/`top_p`/`top_k` server-side on Opus 4.7+**, returning HTTP 400. **Informs the plan:** the compat predicate must treat a provider-side 400 as a genuine failure, not an environmental blip — but see Risk 3 on distinguishing that from network flakiness.

Saved to memory as `9716dcf2cf4a46eda06bd480554ea1ff`.

## Spike Results

### spike-1: Does the known-bad pair actually fail through `run_typed`?
- **Assumption**: "the break described in #3001 is reproducible"
- **Method**: prototype (live call in the repo venv, at baseline `d0c02bde5`)
- **Finding**: **Yes.** `run_typed` raises `LLMCallError` wrapping
  `TypeError: AsyncMessages.create() got an unexpected keyword argument 'temperature'`.
  Failure occurs at argument binding — no network I/O, no API cost, sub-second.
  (It was live on `main` at the time of the spike; `7a30b88f7` has since fixed it.)
- **Confidence**: high
- **Impact on plan**: proves the check's negative case is **cheap and
  deterministic** — the known-bad pair fails in under a second with no token
  spend. That is what makes a compat predicate viable at *bridge and worker
  startup*, not just in a maintenance script: it costs nothing to run at boot.

### spike-2: Can the existing pin helpers express a coupled set?
- **Assumption**: "adding `pydantic-ai-slim` to `AUTO_BUMP_PACKAGES` is a one-line change"
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
- **Impact on plan**: the coupled-set work is not a data-structure change; it requires making the pin reader/writer *declaration-aware* (extras-tolerant, comment-blind). Each of the three defects gets its own regression test. Note that defect 1 is a real bug independent of whether `openai` is in any set — see spike-5.

### spike-3: Is a real `run_typed` call viable from the update process?
- **Assumption**: "the lockfile-maintainer machine can make a live Anthropic call during `/update`"
- **Method**: code-read + prototype
- **Finding**: Yes. `utils/api_keys.py::get_anthropic_api_key` resolves a key on this machine (checked presence only, no value echoed), falling back to `~/Desktop/Valor/.env` when the launchd environment is thin — which is the relevant path, since `/update --cron` runs headless. `run_typed` defaults to `MODEL_FAST` (Haiku) with `settings.timeouts.anthropic_sdk_s` / `anthropic_hard_s` double timeouts already wired.
- **Confidence**: high
- **Impact on plan**: the predicate can call `run_typed` directly rather than re-implementing a raw client. From `auto_bump_deps` it must run **inside the target venv** (`{project_dir}/.venv/bin/python`), never in the update process's own interpreter, because the update process imported its modules before the sync. This mirrors the existing `_markitdown_importable` probe pattern already in `deps.py`. From bridge/worker startup the opposite is true — the process *is* the target venv, so it calls the predicate in-process.

### spike-4: What does `auto_bump_deps` rollback restore today?
- **Assumption**: "rollback is per-package"
- **Method**: code-read (`scripts/update/deps.py:463-547`)
- **Finding**: Rollback is **whole-file**: `original_content` is snapshotted once before the loop and rewritten wholesale on any sync or smoke failure. With one auto-bumped package that is indistinguishable from per-package. With sets it means one bad set reverts every other set's good bump in the same cycle. The restore's own `sync_dependencies` return value is discarded.
- **Confidence**: high
- **Impact on plan**: per-set snapshot/restore, sequential set evaluation, and an explicit `restore_failed` flag (see Concern-2 handling in Technical Approach).

### spike-5: Is `openai` actually coupled to `anthropic` + `pydantic-ai-slim`? (owner-verified during revision)
- **Assumption**: "openai is the third member of the coupled set" (the original plan's claim)
- **Method**: code-read of `uv.lock` locked dependencies + the real import graph
- **Finding**: **No packaging coupling, but a real self-inflicted import coupling.** Three separate facts, all verified:
  1. **No declared coupling.** `pydantic-ai-slim`'s locked dependencies in `uv.lock` are `genai-prices`, `griffe`, `httpx`, `opentelemetry-api`, `pydantic`, `pydantic-graph`, `typing-inspection`. **No `openai`.** `openai>=3.0.0` is declared only under `pydantic-ai`'s `[openai]` extra, which we do not install — the `pyproject.toml` comment on the `pydantic-ai-slim[anthropic]` pin says so explicitly.
  2. **The ImportError is nevertheless real and reproducible.** `agent/llm/wrapper.py:48` has an unguarded module-scope `from pydantic_ai.models.openai import OpenAIChatModel`. Under `pydantic-ai 2.34.0` that module requires an `openai` API our pinned `openai 2.30.0` does not provide, so the **entire wrapper fails at import** — taking down `run_typed` (the Anthropic path) along with it, because `agent/llm/__init__.py` re-exports from `wrapper`.
  3. **The decisive fact:** `OpenAIChatModel` is used at exactly **one** site, `wrapper.py:213`, inside `run_typed_local`, where it is constructed against `OllamaProvider(base_url=...)`. It talks to a **local Ollama server** over an OpenAI-compatible endpoint. It never talks to OpenAI. Nothing about that call path requires the `openai` package to be at any particular version relative to `anthropic`.
- **Confidence**: high (owner-verified both sides)
- **Impact on plan**: **Do not weld `openai` into the coupled set.** Doing so would enroll it in unattended auto-bump and auto-execute the `openai 2.x → 3.x` major bump that this plan's own No-Gos defer to Step 2 — all-or-nothing with `anthropic`, unattended, on a cron tick. The coupled set is **`anthropic` + `pydantic-ai-slim` only**. Fix the coupling where it actually lives: at the import in `wrapper.py`, so the Ollama path's dependency cannot take down the Anthropic path. `openai` still gets an exact pin (spike-2 defect 1 is genuine) but joins no set. And the compat predicate must still exercise the **real wrapper import graph**, because that graph is precisely what catches this class of break.

## Data Flow

### Today: how a bad pin reaches a running process (three routes, zero checks)

1. **Auto-bump route** — `scripts/update/run.py:1302` Step 3.5, gated on
   `config.do_auto_bump and is_lockfile_maintainer` (`run.py:1169`) →
   `deps.auto_bump_deps` → `get_pinned_version` → `get_pypi_latest` →
   `bump_pin_in_pyproject` → `sync_dependencies(frozen=False)` →
   `run_smoke_test` (import check + one pytest file — **the layer that failed to
   observe the break**) → commit + push (`run.py:1325-1345`).
2. **Hand-staged route** — a human or agent edits `pyproject.toml`, runs
   `uv sync`, and commits. **No check at all.** This is how `9d1488ccb`'s pin got
   back onto `main` via `d0c02bde5`.
3. **Follower route** — every non-maintainer machine runs `uv sync --frozen`
   against the pushed `uv.lock` on its next `/update`. **No check at all.**

All three converge on the same terminal state: a bridge or worker process boots
on the bad stack and every `run_typed` caller starts raising —
`bridge/routing.py`, `bridge/job_router.py`, `bridge/context_recall.py`,
`bridge/injection_inspection.py`, `bridge/agent_catchup.py`,
`agent/memory_extraction.py`, `agent/intent_classifier.py`,
`tools/classifier.py`, `tools/email_cs/triage.py`. On 2026-08-24 that state was
invisible for six hours.

### After this plan: one predicate, four call sites

```
utils/llm_stack_compat.py::check_llm_stack_compat() -> CompatResult
   |
   +-- /update verify (scripts/update/verify.py)  ... every run, bump or no bump
   +-- bridge startup (bridge/telegram_bridge.py::main)
   +-- worker startup (worker/__main__.py)
   +-- auto-bump gate (scripts/update/deps.py::run_smoke_test, "llm" phase)
```

Routes 2 and 3 above are now covered: the hand-staged pin is caught by the next
`/update` verify and by the next bridge/worker start; the follower machine is
caught at its own `/update` verify and at service start. Route 1 additionally
keeps its rollback, which is now *one caller of the predicate* rather than the
whole safety story.

## Failure Posture

This section is the owner's recorded decision on #3001 and is not a tradeoff the
build may re-open.

### At the run boundary: start degraded, alert loudly

`check_llm_stack_compat()` returning incompatible at bridge or worker startup
**does not exit the process.**

- The process **comes up**. Telegram intake continues: messages are received,
  AgentSessions are enqueued to Redis, nothing is dropped on the floor. Whatever
  can be done without an LLM keeps being done.
- A process-wide degraded flag is set. LLM-dependent entry points —
  `agent/llm/wrapper.py::run_typed` and, through it, every caller listed in Data
  Flow — **fail fast with a typed `LLMStackIncompatible`** (a subclass of the
  existing `LLMCallError`, so every existing call site's fail-safe posture keeps
  working unchanged) instead of surfacing a raw provider `TypeError` from deep in
  the SDK. Each site's own conservative default (respond / escalate / send / skip)
  still applies.
- The condition is **alarmed**.

Rationale, in the owner's terms: degraded-but-running is precisely the state that
hid this incident for six hours. Exiting would trade a six-hour silent LLM outage
for an immediate total outage plus a launchd crash-loop, which is worse. So the
alert is not a nicety — **the alert is the entire safety property.** If the alert
does not fire, this plan has shipped nothing.

### Alert independence constraint

Because the thing being alarmed *is the LLM stack*, the alert must not route
through it. Explicitly forbidden in the alert path:

- **No `run_typed` / `run_typed_local`.** They are exactly what is broken.
- **No message drafter, no LLM summarization, no persona pass.** The drafter is
  itself a `run_typed` caller.
- **No dynamic body composition of any kind.** The alert body is a **static
  string** plus the two resolved version numbers and the captured exception type
  and message. Nothing else is interpolated.

This is a deliberate, named exception to the repo's standing "never let raw text
speak to chat" convention (`feedback_drafter_comms_layer`). The convention exists
so chat output reads as a person; here the drafter is unavailable by
construction, and a silent alert is the failure being prevented. The feature doc
must state this exception explicitly so a future reader does not "fix" it by
routing the alert back through the drafter.

### Alert channels, and why each is unmissable

Three channels, fired unconditionally on a degraded start. All three, not one —
"unmissable" is a property of redundancy across independent transports, not an
adjective.

| Channel | Mechanism | Independence argument |
|---|---|---|
| Sentry | `sentry_sdk.capture_message(<static body>, level="fatal")`, following the existing `agent/index_drift.py:224` pattern including its capture-failed fallback log. `monitoring/sentry_config.py` already initializes Sentry in both bridge and worker. | Sentry's transport is `sentry-sdk`'s own HTTP client. Shares no code with `anthropic`, `pydantic-ai`, or `openai`. |
| Logs | `logger.critical` with a fixed, greppable sentinel token in the message. | No dependency beyond stdlib `logging`. Survives even a Sentry DSN outage. |
| Dashboard | A health field on `/dashboard.json` (`ui/app.py:906`) that `localhost:8500` renders red. | Read path; the FastAPI app does not import the LLM stack to serve it. |

A direct Telegram push was considered and **rejected**: there is no raw Bot-API
send path in tracked code (the bridge is Telethon-only), so it would mean
introducing a new outbound credential and transport inside a failure handler.
Telethon's own `send_message` is LLM-free and would satisfy independence, but it
lives inside the bridge process only — it cannot alarm a degraded *worker*, and
the whole point is that the check runs in both. If operational experience shows
the three channels above are missed in practice, adding a Telethon send in the
bridge is a small follow-up; it is not the load-bearing part.

### At the auto-bump boundary: fail-closed rollback (a separate axis)

`auto_bump_deps` keeps fail-closed semantics: if its `llm` gate phase fails **or
cannot run** (no key, no venv python, subprocess timeout), the set is rolled back
and a distinct warning is emitted. This does not contradict the degraded-start
decision above — they are different questions. "Should a running service refuse
to start?" is answered *no*. "Should an unattended script push a new pin to the
whole fleet without having verified it?" is answered *no* as well. Declining to
bump costs one stale cycle.

## Architectural Impact

- **New dependencies**: none. `sentry-sdk` is already a declared dependency.
- **New module**: `utils/llm_stack_compat.py`. **Named deviation from the
  critique's suggestion**, which proposed `scripts/update/compat_gate.py`: the
  predicate is imported by `bridge/` and `worker/`, and making runtime services
  import from `scripts/update/` inverts the dependency direction. `utils/` is the
  existing neutral home — `scripts/update/` already imports `utils.utc`
  (`git.py:116`, `run.py:288`) and `utils/api_keys.py` is already shared by both
  sides. `utils/llm_stack_compat.py` imports nothing from `scripts/`.
- **Interface changes**: `AUTO_BUMP_PACKAGES` (a `list[str]`) is replaced by
  `AUTO_BUMP_SETS` (a list of `CoupledSet`). `run_smoke_test` grows a `phases`
  argument and returns a phase marker. `AutoBumpResult` gains per-set bookkeeping
  and `restore_failed`. `LLMCallError` gains a subclass.
- **Behavior change at startup**: bridge and worker gain a boot-time check. It is
  a sub-second local operation on the happy path (see Technical Approach for why
  it does not add a network call at every boot).
- **Coupling**: deliberately **increases** coupling from `scripts/update/deps.py`
  to the LLM stack — but only across a subprocess boundary into the target venv,
  so the update process itself does not import it. That direction is correct: the
  gate must exercise the thing it protects.
- **Data ownership**: unchanged. `pyproject.toml` remains the single source of pin
  truth; `uv.lock` remains maintainer-authored and follower-consumed.
- **Reversibility**: high. The predicate is additive and its call sites are
  one-liners; removing them restores today's behavior exactly.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (alert-channel shape, if operational judgment is needed)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` resolvable | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | The auto-bump `llm` phase makes a real Haiku call |
| `uv` on PATH | `uv --version` | Coupled-set sync/rollback re-resolves the lockfile |
| Network reach to PyPI | `python -c "from scripts.update.deps import get_pypi_latest; assert get_pypi_latest('anthropic')"` | Latest-version discovery |
| Sentry configured | `python -c "from monitoring.sentry_config import *"` + a DSN present | One of the three alert channels |

## Solution

### Key Elements

- **`check_llm_stack_compat()` — a standalone predicate at the run boundary.**
  Lives in `utils/llm_stack_compat.py`, returns a `CompatResult`
  (`compatible: bool`, `anthropic_version: str`, `pydantic_ai_version: str`,
  `reason: str`, `exc_type: str | None`). It knows nothing about auto-bump,
  `pyproject.toml`, or `uv`. It answers exactly one question about the
  *installed* stack.
- **Four call sites.** `/update` verify (unconditional, every run — not only when
  a bump happened), bridge startup, worker startup, and the auto-bump gate.
  Auto-bump is demoted from "the gate" to "one caller of the gate".
- **Degraded start + loud alert** on the run-boundary sites, per Failure Posture.
- **`LLMStackIncompatible(LLMCallError)`** raised by `run_typed` when the degraded
  flag is set, so callers get a typed, legible error instead of a provider
  `TypeError` — and their existing `except LLMCallError` fail-safes keep working.
- **Fix the self-inflicted `openai` import coupling** at `wrapper.py:48` (spike-5).
  Two honest options; the builder picks one and records why:
  (a) declare the `[openai]` extra in `pyproject.toml` — we genuinely do import
  that module, so declaring it is truthful, but it enrolls us in openai's release
  cadence; or (b) make the import **lazy inside `run_typed_local`** and guarded,
  so an `openai`/`pydantic-ai` mismatch degrades the Ollama path alone and cannot
  take down the Anthropic path. **(b) is preferred** — it matches the actual
  dependency shape (one call site, local Ollama server, never OpenAI) and keeps
  the blast radius of a third-party break proportional to what actually uses it.
- **Coupled-set declaration** — `AUTO_BUMP_SETS = [CoupledSet(["anthropic",
  "pydantic-ai-slim"], reason=...), CoupledSet(["claude-agent-sdk"], reason=...)]`.
  A set is the atomic unit of bump, sync, gate, and rollback, and carries prose
  saying *why* its members are welded together.
- **`openai` is explicitly NOT a set member** (spike-5). It gets an exact pin
  because spike-2's defect 1 is real, but it joins no set, and an assertion plus
  a docstring note record why so a future reader does not re-add it.
- **Declaration-aware pin helpers** — the reader and writer stop substring-matching
  whole lines (comments included) and stop assuming a bare `name==version` shape.
  They locate a package's actual dependency declaration, tolerate extras
  (`pydantic-ai-slim[anthropic]`), and refuse rather than silently no-op.
- **Per-set gate phases** — `CoupledSet.gates` defaults to `("import", "pytest")`,
  so a newly added set never silently inherits a billed API call or a dependency
  on Anthropic being up. The LLM set opts into `("llm", "import", "pytest")`.
- **Atomic per-set rollback**, with the restore's own sync result captured
  (`restore_failed`) so a failed restore cannot leave `uv.lock` and
  `pyproject.toml` describing different worlds.
- **`anthropic` returns to auto-bump** — re-added as a member of the LLM set, in
  the same commit as the gate. Never before it.

### Flow

**Run boundary (every `/update`, every bridge start, every worker start):**

```
startup / verify
  → check_llm_stack_compat()
      → compatible?  → proceed normally
      → incompatible? → set degraded flag
                        → run_typed raises LLMStackIncompatible from here on
                        → ALERT: sentry fatal + logger.critical + dashboard red
                        → PROCESS CONTINUES (Telegram intake keeps queueing)
```

**Auto-bump (`/update --cron`, maintainer machine only):**

```
for each coupled set:
  → resolve latest for EVERY member         → any unresolvable? skip whole set
  → rewrite ALL member pins (set snapshot taken first)
  → uv sync --all-extras (unfrozen)         → fail? restore set snapshot, re-sync, next set
  → for each phase in set.gates:
       llm    → check_llm_stack_compat() in the target venv subprocess
       import → import anthropic; import claude_agent_sdk
       pytest → tests/unit/test_docs_auditor_substrate.py
  → any phase fails? restore set snapshot, re-sync, record rolled_back + phase
  → restore itself failed? record restore_failed, git checkout -- uv.lock
any set survived AND not restore_failed? commit + push pyproject.toml + uv.lock
```

### Technical Approach

- **The predicate's happy path must be cheap enough for boot.** At bridge/worker
  startup it performs a **local signature check only** — import the wrapper module
  (which is the real import graph, per spike-5) and verify that
  `anthropic.resources.messages.AsyncMessages.create` accepts the parameters
  `pydantic_ai`'s Anthropic model passes. That is the exact failure mode of the
  incident, it is sub-second, and it makes **no network call and spends no
  tokens**. Spike-1 confirms the bad pair fails at argument binding before any
  I/O, so a local check is sufficient for this class.
- **The auto-bump `llm` phase additionally makes a real call**, because a bump can
  break the transport (see Research: `httpx` → `httpx2`) in ways a signature check
  cannot see. Same predicate, an `allow_network=True` argument. Boot pays nothing;
  the once-a-day bump pays a handful of Haiku tokens only on cycles where
  something actually bumped.
- **The predicate exercises the real wrapper import graph.** `import agent.llm`
  (not a hand-rolled probe), because `agent/llm/__init__.py` re-exports from
  `wrapper` and that is precisely the graph spike-5 showed can break. An
  `ImportError` from that import is a compat failure with the exception text
  carried through verbatim.
- **From auto-bump, the predicate runs in the target venv, not in-process.**
  `{project_dir}/.venv/bin/python -m utils.llm_stack_compat --json`, following the
  existing `_markitdown_importable` pattern. In-process would exercise the
  *pre-sync* imports the update process already holds. Bounded by a subprocess
  timeout in addition to `run_typed`'s own double timeout.
- **Set semantics are all-or-nothing at every stage.** If any member's latest
  version cannot be resolved, the set does not move at all. Same for the rewrite:
  any failed member rewrite restores the snapshot and abandons the set
  *immediately* rather than continuing — today's code records an error and carries
  on, which spike-2 shows is exactly how the incident happened.
- **Capture the restore sync's result.** `restore = sync_dependencies(project_dir,
  frozen=False)`; on `not restore.success`, set `AutoBumpResult.restore_failed =
  True` and `run_cmd(["git", "checkout", "--", "uv.lock"], cwd=project_dir,
  check=False)`. Guard `run.py` Step 3.5's commit branch with
  `and not bump.restore_failed` — otherwise a later successful bump does
  `git add pyproject.toml uv.lock` and pushes a poisoned lockfile fleet-wide.
- **Skip a set's sync entirely when no member's pin actually changed**, so the
  per-set fan-out does not multiply `uv sync` calls on quiet cycles.
- **Distinguish gate phases in the result.** The operator reading a `/update`
  warning needs to tell "the LLM pair is incompatible" from "an unrelated unit
  test is flaky". Carry a phase marker (`llm` / `import` / `pytest`) alongside the
  output and surface it in the warning detail.
- **Do not touch the commit/push path in `run.py`.**
  `docs/archive/plans-completed/sdlc-1091.md` documents that the restart gate
  depends on auto-bump's commit landing in local HEAD synchronously before
  `auto_bump_deps` returns. That ordering stays exactly as-is; the only change is
  the added `restore_failed` guard on the commit branch.
- **Keep the `pyproject.toml` comment constraint, add the pointer.** With
  `7a30b88f7` landed, "Do NOT move to 1.x while pydantic-ai-slim is on 2.9.0" is a
  *correct, load-bearing* warning sitting above a correct pin — deleting it would
  strip the only in-file guard against a hand-edit repeating `9d1488ccb` while
  this lane is in flight. Replace the four-line block with two lines that keep the
  constraint and add the pointer:
  `# CRITICAL — coupled set: anthropic + pydantic-ai-slim move together.` /
  `# anthropic>=1.0.0 requires pydantic-ai-slim>=2.33.0. See AUTO_BUMP_SETS in scripts/update/deps.py.`

## Failure Path Test Strategy

### Exception Handling Coverage
- `deps.py::get_pypi_latest` has two bare `except Exception` blocks (method-1 fallthrough, method-2 return `None`). Both are pre-existing and stay; the set logic must treat a `None` latest as "skip the whole set", asserted directly.
- `deps.py::_markitdown_importable` swallows `TimeoutExpired`/`OSError` → `False`. Untouched.
- `check_llm_stack_compat` must **not** add a bare `except Exception: pass`. Every failure path returns a `CompatResult` with `compatible=False` and a non-empty `reason` carrying the exception type and message verbatim. Tested on each path.
- The **alert** path itself must be exception-tolerant in the other direction: a Sentry capture failure must not prevent the `logger.critical` or the dashboard field, and must not crash startup. Follow `agent/index_drift.py:229`'s "capture failed" fallback log. Tested by making `capture_message` raise and asserting the process still starts and the log line still appears.
- No exception handler introduced by this work may swallow a rollback failure — a failed restore-and-resync surfaces as `restore_failed` plus a warning, not silence.

### Empty/Invalid Input Handling
- `get_pinned_version` returning `None` (package absent from `pyproject.toml`) → set skipped, tested.
- `get_pypi_latest` returning `None` (network down) → set skipped, tested.
- A `pyproject.toml` with no dependency block at all → helpers refuse, no rewrite, no crash.
- `check_llm_stack_compat` on a venv where `agent.llm` cannot be imported at all → `compatible=False` with the `ImportError` text, not an unhandled raise.
- Empty/whitespace prompt is already rejected by `run_typed` with `ValueError`; the network-mode probe's prompt is a literal, so this is unreachable, and is covered generically by "any exception is a compat failure".

### Error State Rendering
- The degraded-start alert is the primary operator-visible artifact. Assert all three channels fire from one degraded start, and assert the body is the **static** string plus versions — a test that asserts no LLM call occurred during alert emission.
- Assert a rolled-back set produces the `"Auto-bump rolled back"` warning **and** that the phase marker appears in the logged detail — a rollback whose reason is not legible is the same failure as no rollback at all.
- Assert the success path still logs `"Smoke test passed after bump"` so the existing `extract_update_warnings` parsing is not disturbed.

## Test Impact

- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_no_bump_when_already_latest` — UPDATE: fixture `pyproject.toml` lists `anthropic` and `claude-agent-sdk` as flat siblings. Rewrite against `AUTO_BUMP_SETS`; keep the assertion that nothing bumps when all members are at latest.
- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_rollback_on_smoke_failure` — UPDATE: it patches `scripts.update.deps.run_smoke_test` to return a 2-tuple. Adjust to the new phase-carrying return shape and assert the **set's** snapshot is restored (all members), not just the file.
- [ ] `tests/integration/test_remote_update.py::TestGetPinnedVersion::test_reads_pinned_version` — UPDATE: extend with the extras form and the comment-collision case from spike-2. This test currently passes against a one-line fixture that cannot expose either defect.
- [ ] `tests/unit/test_agent_llm_wrapper.py` (or the nearest existing wrapper test module) — UPDATE: add coverage for `LLMStackIncompatible` being raised when the degraded flag is set, and assert it is a `LLMCallError` subclass so existing `except LLMCallError` sites are unaffected. If no such module exists, create it.
- [ ] `tests/integration/test_remote_update.py` — ADD: `test_partial_resolve_skips_whole_set`, `test_extras_pin_is_bumped`, `test_openai_pin_not_read_from_comment`, `test_openai_is_not_in_any_coupled_set`, `test_default_gates_exclude_llm_phase`, `test_llm_gate_failure_rolls_back_set`, `test_unrelated_set_survives_failed_set`, `test_gate_unavailable_is_fail_closed`, `test_restore_failure_blocks_commit`, `test_worktree_clean_after_every_rollback_path`.
- [ ] `tests/unit/test_llm_stack_compat.py` — ADD (new file): predicate returns compatible on the good pair; incompatible with verbatim reason on a simulated bad signature; incompatible on `ImportError`; local mode makes no network call.
- [ ] `tests/unit/test_llm_stack_degraded_start.py` — ADD (new file): all three alert channels fire on a degraded start; the alert fires **while `run_typed` raises** (the independence proof); a raising `capture_message` does not suppress the other two channels or crash startup; the process does not exit.
- [ ] No changes to `tests/unit/test_docs_auditor_substrate.py` — it stays the gate's pytest phase; this plan does not repurpose it.

## Rabbit Holes

- **Do not build a general dependency-compatibility solver.** The remedy for the missing upper bound in pydantic-ai's metadata is a local predicate, not a constraint engine or a vendored override file. Two packages, one declared set, one check.
- **Do not rewrite `pyproject.toml` parsing onto `tomlkit`/`tomllib`.** Tempting after spike-2, and genuinely more correct — but the writer must preserve the `CRITICAL — pin exact` comments verbatim, and round-tripping comments is where this swallows a day. Make the regex declaration-aware (anchored to the quoted dependency string, extras-tolerant) and move on. Revisit only if a fourth defect appears.
- **Do not make the boot-time check do network I/O.** A per-boot billed API call turns every worker restart into a token spend and makes service startup depend on provider availability. Signature check locally; real call only in auto-bump.
- **Do not make the gate run the full test suite.** A ~20-minute unit run inside `/update --cron` is not a gate, it is an outage.
- **Do not build a general degraded-mode framework.** One flag, one typed exception, one alert. The second subsystem that wants degraded start can justify the abstraction.
- **Do not attempt the upgrade "while we're in here."** `pydantic-ai-slim` 2.35.0 and `openai` 3.3.1 are sitting on PyPI and the temptation is real. That is Step 2, behind this gate. A three-way major bump with the gate landing in the same PR gives you no way to tell which half is at fault.
- **Do not route the alert through the drafter to make it read nicer.** See Failure Posture — that is the one thing the alert must never do.

## Risks

### Risk 1: The boot-time check is wrong and blocks a healthy fleet
**Impact:** A false negative in the signature check would put every bridge and worker on the fleet into degraded mode simultaneously, disabling all LLM paths on a stack that actually works.
**Mitigation:** Degraded start is *not* a hard failure — Telegram intake and queueing continue, so a false negative degrades capability rather than causing an outage, and the loud alert makes it immediately visible and one revert away. The check is also narrow by construction: it asserts the specific parameters `pydantic_ai`'s Anthropic model passes are accepted by the installed `anthropic` signature, plus that `import agent.llm` succeeds. Test the positive case against the real pinned pair in CI so a false negative fails the suite before it ships.

### Risk 2: The auto-bump gate makes `/update` depend on the Anthropic API being up
**Impact:** A provider outage or a lapsed key turns every maintainer-machine auto-bump cycle into a rollback + warning, and the fleet silently stops receiving dependency updates.
**Mitigation:** Accepted, with visibility, and now **scoped to one set**: `gates` defaults to `("import", "pytest")`, so only the LLM set carries the Anthropic dependency and `claude-agent-sdk` keeps advancing during an outage. Auto-bump is a convenience path restricted to one machine; a skipped cycle costs staleness. The distinct fail-closed warning makes a persistent outage legible in the `/update` summary. Boot-time checks are unaffected — they make no network call.

### Risk 3: A genuine provider-side error is misread as an incompatible pin
**Impact:** A 400 from the deprecated-sampling-params change (see Research) or a 529 overload rolls back a perfectly good bump, and the operator chases a dependency ghost.
**Mitigation:** `CompatResult.exc_type` carries the exception class verbatim, so `TypeError` (binding — a real incompatibility) is distinguishable from `APIStatusError` (provider) at a glance in the warning. Deliberately **not** adding retry logic or error classification in this lane: rolling back on a transient is the safe direction. Note this risk applies only to the auto-bump `llm` phase; the boot-time check is local and cannot see a provider error at all.

### Risk 4: The alert is emitted and still missed
**Impact:** This is the failure the whole plan is built to prevent. If the three channels are all ignored, degraded mode is indistinguishable from the six-hour silence of 2026-08-24.
**Mitigation:** Redundancy across three independent transports (Sentry fatal, `logger.critical` with a greppable sentinel, dashboard red) plus the typed `LLMStackIncompatible` that every failing call site now raises — so the symptom is legible at the point of failure too, not only at boot. The dashboard field in particular is a *standing* signal rather than a one-shot notification, so it survives a missed alert. Accepted residual: no channel here is a phone-buzzing page. Named as a follow-up candidate rather than silently assumed away.

### Risk 5: `openai` gaining an exact pin changes resolution for someone
**Impact:** `openai>=1.0.0` currently floats; pinning it exactly could conflict with a transitive requirement.
**Mitigation:** Pin at the currently-installed version (2.30.0 in this checkout) so the resolution is provably unchanged, and verify with a clean `uv sync --all-extras` producing no `uv.lock` diff beyond the pin line. This is deliberately **not** the 2.x → 3.x move, and `openai` joins **no** coupled set (spike-5), so nothing auto-bumps it.

### Risk 6: The lazy-import fix to `wrapper.py` hides a real break until runtime
**Impact:** Making `OpenAIChatModel` a lazy import inside `run_typed_local` (the preferred spike-5 option) means an `openai`/`pydantic-ai` mismatch stops being an import-time error and becomes a first-call error on the Ollama path.
**Mitigation:** That is the intended trade — it is precisely how the Anthropic path stops being collateral damage. The break does not become invisible: `run_typed_local` wraps it in `LLMCallError` with the exception text, and the import phase of the auto-bump gate still imports `agent.llm`. Add a test that `run_typed_local` surfaces an import failure as a legible `LLMCallError` rather than an opaque `AttributeError`.

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
**Mitigation:** Already structurally satisfied — `sync_with_uv` runs both commands synchronously via `run_cmd` and the gate is called after it returns a success result. `CompatResult` additionally carries the resolved `anthropic` and `pydantic-ai` versions, so a stale-venv read is visible in the output rather than silent.

### Race 3: A degraded-flag read races the boot-time check
**Location:** `utils/llm_stack_compat.py` module state, read by `agent/llm/wrapper.py::run_typed`
**Trigger:** A worker task begins before startup finishes evaluating the predicate, reads an unset flag, and reaches the provider directly — getting the raw `TypeError` the typed exception exists to replace.
**Data prerequisite:** The flag must be resolved before any `run_typed` call in the process.
**State prerequisite:** The check runs synchronously in startup, before the event loop accepts work.
**Mitigation:** Make the flag **lazily self-resolving** rather than write-once: `run_typed` reads it through an accessor that evaluates the predicate on first read if startup has not already set it, memoized thereafter. That makes the ordering irrelevant and keeps the local check cheap enough to afford. Assert it with a test that calls `run_typed` without any startup hook having run.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3001] **The dependency upgrade itself** — `pydantic-ai-slim` → 2.33.0+, `anthropic` → 1.x, `openai` 2.x → 3.x, and exercising the modules that import `openai` directly (`tools/impact_finder_core.py`, `tools/cross_vendor_judge.py`, `tools/selfie/__init__.py`). This is Step 2 of the sequencing agreed in issue comment 5420111955 and must run *behind* the gate this plan builds, in its own lane.
- [SEPARATE-SLUG #3001] **Work Item 2 — the dead `worker_key` regression guard.** Root-caused in the issue comments to `69dc69568`/#2949: `_make_session` passes `stage_states=` as a constructor kwarg that Popoto now silently drops. The human's sequencing note marks it explicitly not urgent and to be sequenced after Step 1. It shares no files with this lane (`tests/unit/test_agent_session.py`, `models/agent_session.py` vs. `scripts/update/deps.py`), so it is cleanly separable. The issue comments already carry the implementation gotchas (widen the `save` stub to `lambda self, **kwargs: None`; the blast radius is 11 tests not 9; do not restore the kwarg mapping — #2949 pinned its removal).
- [SEPARATE-SLUG #3001] **Work Item 3 — duplicate/noisy triage filing.** Idempotent issue filing, root-cause collapsing, environmental-failure classification. Also marked not urgent, also file-disjoint from this lane.
- [SEPARATE-SLUG #3016] **The `test_promise_gate_real_api` failure** — independent root cause, already filed.
- [SEPARATE-SLUG #3001] **Auditing the remaining `CRITICAL — pin exact` deps for staleness** (`telethon`, `claude-agent-sdk`). It is an acceptance criterion of #3001 but belongs with Step 2's upgrade work, where the findings can actually be acted on.
- **A paging alert channel** (PagerDuty, SMS, phone push). Risk 4 names this as accepted residual. The three channels shipped here are the mechanism; escalating one of them to a page is a follow-up if experience shows it is needed.
- **Extending the compat predicate to other subsystems.** One predicate for the LLM stack. See Rabbit Holes.
- *(Removed in revision: the previous `[ORDERED]` entry deferring "gating the non-auto-bump paths" is **no longer out of scope** — per the owner's decision it is this lane's primary deliverable. See Failure Posture and tasks 2-4.)*

## Update System

This work **is** an update-system change — it modifies `scripts/update/deps.py`
and `scripts/update/verify.py`, both of which `/update` drives.

- `utils/llm_stack_compat.py` — **new**, the standalone predicate. Shared by the update scripts and the runtime services.
- `scripts/update/verify.py` — **new call site**: `check_llm_stack_compat()` runs unconditionally on every `/update` (and `/update --cron`), reporting as a `ToolCheck` alongside the existing checks. Not gated on whether a bump happened.
- `scripts/update/deps.py` — the substantive change (coupled sets, declaration-aware pin helpers, per-set gate phases, per-set rollback with `restore_failed`, the `llm` gate phase calling the predicate in the target venv).
- `scripts/update/run.py` — minimal: surface the gate's phase marker in the rolled-back warning detail, and guard the commit branch with `not bump.restore_failed`. The commit/push/restart ordering documented in `sdlc-1091.md` is **not** touched.
- `.claude/skills/update/SKILL.md` lines 66-72 describe the auto-bump flow ("checks PyPI for newer `anthropic` and `claude-agent-sdk` versions... runs a smoke test (import check + pytest)"). That description becomes wrong on both counts and must be updated in the same change, along with a note that verify now includes an LLM-stack compatibility check.
- **No new config files or env keys.** `ANTHROPIC_API_KEY` and `SENTRY_DSN` are already declared.
- **No migration for existing installations.** Follower machines never run auto-bump (`is_lockfile_maintainer` gate at `run.py:1169`), but they **do** now get the verify-time check and the startup check — which is the point: the follower route (Data Flow route 3) was previously unguarded.
- `pyproject.toml` pin changes (the `openai` exact pin, and possibly the `[openai]` extra if the builder picks spike-5 option (a)) propagate to the fleet through the normal `uv sync --frozen` path on the next `/update`. Pin-only; no new packages.

## Agent Integration

**No new agent-facing surface** — no CLI entry point in `[project.scripts]`, no MCP
tool, no new bridge command. But this is **not** a purely internal change, because
it alters what the agent's runtime does at boot and how LLM failures present:

- `bridge/telegram_bridge.py::main` and `worker/__main__.py` both gain a startup
  call to `check_llm_stack_compat()`. Both are agent-runtime entry points.
- `agent/llm/wrapper.py::run_typed` raises `LLMStackIncompatible` under a degraded
  stack. Every non-harness caller — `bridge/routing.py`, `bridge/job_router.py`,
  `bridge/context_recall.py`, `bridge/injection_inspection.py`,
  `bridge/agent_catchup.py`, `agent/memory_extraction.py`,
  `agent/intent_classifier.py`, `tools/classifier.py`, `tools/email_cs/triage.py`
  — sees it. Because it subclasses `LLMCallError`, every existing
  `except LLMCallError` fail-safe keeps working with no edit; that inheritance is
  asserted in a test rather than assumed.
- **Telegram intake must keep working in degraded mode.** The integration test
  that matters: with the degraded flag set, an inbound message still enqueues an
  AgentSession. This is the acceptance property of the owner's "keep receiving
  and queueing" decision.
- `utils/llm_stack_compat.py` also exposes `python -m utils.llm_stack_compat
  --json`, used by `deps.py` across the subprocess boundary into the target venv.
  That is a tooling entry point, not an agent surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/llm-stack-compat-gate.md` — the run-boundary predicate and its four call sites; the degraded-start contract and why not fail-closed; the alert's three channels and the independence constraint (including the named exception to the drafter convention); the coupled-set model and why `anthropic`/`pydantic-ai-slim` are welded together (with the `pydantic-ai-slim>=2.33.0` boundary from Research); **why `openai` is deliberately not in the set** (spike-5); what the gate checks and what it cannot check, naming the uncovered ground explicitly; how to add a new coupled set and why `gates` defaults to `("import", "pytest")`.
- [ ] Add a row to `docs/features/README.md` index table.

### Existing Docs to Correct
- [ ] `.claude/skills/update/SKILL.md` lines 66-72 — the auto-bump description names only `anthropic` + `claude-agent-sdk` and describes the smoke test as "import check + pytest". Both become wrong; also note the new verify-time check.
- [ ] `pyproject.toml` — replace the four-line `anthropic` comment block with the two-line form that keeps the constraint and points at `AUTO_BUMP_SETS`.
- [ ] `docs/features/remote-update.md` — add a cross-reference to the new feature doc.
- [ ] `docs/features/nonharness-llm-wrapper.md` — document `LLMStackIncompatible`, the degraded flag, and the lazy `OpenAIChatModel` import (spike-5 fix).

### Inline Documentation
- [ ] Each `CoupledSet` carries a prose `reason`, replacing the stopgap comment block currently above `AUTO_BUMP_PACKAGES`.
- [ ] `AUTO_BUMP_SETS`' docstring records why `openai` is excluded, citing spike-5's three facts, so a future reader does not re-add it.
- [ ] Docstring on the auto-bump `llm` phase stating that it makes a real, billed API call, and on the boot-time path stating that it deliberately does not.
- [ ] The alert emitter carries a comment naming the independence constraint and the forbidden paths (`run_typed`, drafter, any LLM summarization).

## Success Criteria

- [ ] `check_llm_stack_compat()` exists as a standalone predicate in `utils/llm_stack_compat.py`, importing nothing from `scripts/`.
- [ ] It is called from all four sites: `/update` verify (unconditionally, bump or no bump), bridge startup, worker startup, and the auto-bump `llm` gate phase.
- [ ] On an incompatible stack, bridge and worker **start** — the process does not exit — and an inbound Telegram message still enqueues an AgentSession.
- [ ] A degraded start fires all three alert channels (Sentry `level="fatal"`, `logger.critical` with the sentinel token, dashboard health field), with a static body plus versions.
- [ ] **The alert fires in a test where `run_typed` raises.** This is the independence proof; without it the plan has shipped nothing.
- [ ] A raising `sentry_sdk.capture_message` neither suppresses the other two channels nor prevents startup.
- [ ] `run_typed` raises `LLMStackIncompatible`, a `LLMCallError` subclass, under a degraded stack — asserted, so existing `except LLMCallError` sites are provably unaffected.
- [ ] `agent/llm/wrapper.py` no longer takes down the Anthropic path on an `openai`/`pydantic-ai` mismatch (spike-5 fix applied, with the chosen option recorded).
- [ ] `AUTO_BUMP_SETS` contains `{"anthropic", "pydantic-ai-slim"}` as one set, and `anthropic` is back in the auto-bump path.
- [ ] `openai` appears in **no** coupled set, asserted in code, and carries an exact pin at its currently-resolved version with no other `uv.lock` change.
- [ ] `CoupledSet.gates` defaults to `("import", "pytest")` — asserted, so a new set never silently inherits a billed call.
- [ ] The three spike-2 pin-helper defects each have a regression test that fails against the current implementation.
- [ ] **Rollback verified in two named legs** (see task 8), with both transcripts in the PR description:
      - **Leg (a), unmocked:** a throwaway checkout staged with `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0`, synced for real, with the gate subprocess invoked directly — exits non-zero and reports `unexpected keyword argument 'temperature'`.
      - **Leg (b), resolution-stubbed:** `get_pypi_latest` monkeypatched to return the known-bad pair, then a full `auto_bump_deps` run asserting `rolled_back is True` and both pins restored. This leg **stubs version resolution**; that is stated rather than claimed as unmocked, because PyPI's real latest pair is currently compatible and would never roll back.
- [ ] A failed LLM set does not roll back a successful `claude-agent-sdk` bump in the same cycle.
- [ ] A gate that cannot run (no key / no venv / timeout) rolls back the set and emits a distinct warning.
- [ ] After every rollback path, `git status --porcelain pyproject.toml uv.lock` is empty; a failed restore sets `restore_failed` and blocks the commit.
- [ ] `/update`'s rolled-back warning names which gate phase failed.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (compat)**
  - Name: `compat-builder`
  - Role: the `check_llm_stack_compat` predicate, its four call sites, the degraded flag, `LLMStackIncompatible`, the alert emitter, the `wrapper.py` import fix
  - Agent Type: builder
  - Resume: true

- **Builder (deps)**
  - Name: `deps-builder`
  - Role: coupled-set declaration, declaration-aware pin helpers, per-set gate phases and rollback, `restore_failed`
  - Agent Type: builder
  - Resume: true

- **Test engineer (deps)**
  - Name: `deps-tester`
  - Role: alert-independence tests, degraded-intake test, regression tests for the three pin-helper defects, set-atomicity tests, and driving the two-leg rollback verification
  - Agent Type: test-engineer
  - Resume: true

- **Validator (deps)**
  - Name: `deps-validator`
  - Role: verifies the four call sites, degraded-start posture, alert independence, set atomicity, and that `openai` is in no set
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `deps-documentarian`
  - Role: feature doc, README index row, `update/SKILL.md` correction, wrapper doc, `pyproject.toml` comment repair
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Verify the working LLM stack on main (no edit)
- **Task ID**: `verify-phase0-pin`
- **Depends On**: none
- **Validates**: `grep -q '"anthropic==0.125.0"' pyproject.toml` plus the spike-1 probe printing `OK`
- **Informed By**: Freshness Check (drift resolved by `7a30b88f7`)
- **Assigned To**: `compat-builder`
- **Agent Type**: builder
- **Parallel**: false
- Confirm `pyproject.toml` reads `"anthropic==0.125.0"` and the venv resolves it.
- Re-run the spike-1 `run_typed` probe; it must print `OK`.
- **Make no edit, no commit, and no push.** `7a30b88f7` already landed this. If the probe fails, stop and report — the premise has moved again.

### 2. The compat predicate
- **Task ID**: `build-compat-predicate`
- **Depends On**: `verify-phase0-pin`
- **Validates**: `tests/unit/test_llm_stack_compat.py`
- **Informed By**: spike-1 (bad pair fails at binding, no I/O), spike-5 (must exercise the real `agent.llm` import graph)
- **Assigned To**: `compat-builder`
- **Agent Type**: builder
- **Parallel**: false
- Create `utils/llm_stack_compat.py` with `CompatResult` and `check_llm_stack_compat(allow_network: bool = False) -> CompatResult`.
- Local mode: `import agent.llm` (the real graph), then verify the installed `anthropic` message-create signature accepts what `pydantic_ai`'s Anthropic model passes. No network, no tokens.
- `allow_network=True`: additionally make one minimal `run_typed` call with a one-field output model.
- Carry both resolved versions, a verbatim `reason`, and `exc_type` on every failure path. No bare `except Exception: pass`.
- Add a `python -m utils.llm_stack_compat --json` entry point for the subprocess caller.
- Import nothing from `scripts/`.

### 3. Degraded-start posture and the alert
- **Task ID**: `build-degraded-posture`
- **Depends On**: `build-compat-predicate`
- **Validates**: `tests/unit/test_llm_stack_degraded_start.py`, and the degraded-intake integration test
- **Informed By**: Failure Posture (owner decision), `agent/index_drift.py:224` (existing capture pattern)
- **Assigned To**: `compat-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `LLMStackIncompatible(LLMCallError)` and the lazily-self-resolving degraded flag (Race 3); `run_typed` raises it when set.
- Wire the startup call into `bridge/telegram_bridge.py::main` and `worker/__main__.py`. **Neither may exit** on incompatibility.
- Implement the alert emitter: Sentry `capture_message(level="fatal")`, `logger.critical` with a fixed sentinel token, and the `/dashboard.json` health field. Static body plus the two versions and the exception text — no `run_typed`, no drafter, no summarization.
- A Sentry capture failure must not suppress the other two channels or crash startup.
- Prove independence: a test asserting the alert fires while `run_typed` raises.
- Prove intake survives: with the flag set, an inbound Telegram message still enqueues an AgentSession.

### 4. `/update` verify call site
- **Task ID**: `build-verify-callsite`
- **Depends On**: `build-compat-predicate`
- **Validates**: a verify-level test asserting the check runs on a `/update` with no bump
- **Informed By**: Data Flow (routes 2 and 3 were unguarded)
- **Assigned To**: `compat-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `check_llm_stack_compat()` to `scripts/update/verify.py` as a `ToolCheck`, run **unconditionally** on every `/update` and `/update --cron` — explicitly not gated on whether a bump happened.

### 5. Fix the self-inflicted `openai` import coupling
- **Task ID**: `build-wrapper-import-fix`
- **Depends On**: `build-compat-predicate`
- **Validates**: a test that an `OpenAIChatModel` import failure surfaces as a legible `LLMCallError` from `run_typed_local` and does **not** break `run_typed`
- **Informed By**: spike-5
- **Assigned To**: `compat-builder`
- **Agent Type**: builder
- **Parallel**: false
- Apply the preferred option (b): make `from pydantic_ai.models.openai import OpenAIChatModel` **lazy and guarded inside `run_typed_local`**, so the Ollama path's dependency cannot take down the Anthropic path.
- If option (a) is chosen instead (declare the `[openai]` extra), record the reason in the commit body and in the wrapper doc.
- Do **not** add `openai` to any coupled set — see task 6.

### 6. Coupled sets, per-set gates, and atomic rollback
- **Task ID**: `build-coupled-sets`
- **Depends On**: `build-wrapper-import-fix`
- **Validates**: the set-atomicity, default-gates, `openai`-exclusion, and rollback-cleanliness tests in `tests/integration/test_remote_update.py`
- **Informed By**: spike-2 (three helper defects), spike-4 (whole-file rollback), spike-5 (`openai` excluded)
- **Assigned To**: `deps-builder`
- **Agent Type**: builder
- **Parallel**: false
- Make the pin reader locate a package's actual dependency declaration rather than substring-matching whole lines including comments; make the writer tolerate extras markers; make both refuse loudly rather than silently no-op.
- Give `openai` an exact pin at its currently-resolved version; confirm `uv.lock` shows no change beyond that line.
- Replace `AUTO_BUMP_PACKAGES` with `AUTO_BUMP_SETS = [CoupledSet(["anthropic", "pydantic-ai-slim"], reason=...), CoupledSet(["claude-agent-sdk"], reason=...)]`. Assert in code that `openai` is in no set, with spike-5's reasoning in the docstring.
- `CoupledSet.gates` defaults to `("import", "pytest")`; the LLM set opts into `("llm", "import", "pytest")`.
- All-or-nothing resolve and all-or-nothing rewrite; abandon the set immediately on any member failure.
- Per-set snapshot/restore replacing the whole-run `original_content` snapshot; skip a set's sync when no pin actually changed.
- Capture the restore sync's result → `restore_failed` + `git checkout -- uv.lock`; guard `run.py`'s commit branch with `not bump.restore_failed`.
- Extend `AutoBumpResult` with per-set bookkeeping and the phase marker; surface the phase in `run.py`'s rolled-back warning. Do not touch the commit/push/restart ordering.
- Re-add `anthropic` to the auto-bump path **in this same task** — never as a separate earlier commit.

### 7. Tests
- **Task ID**: `build-tests`
- **Depends On**: `build-degraded-posture`, `build-verify-callsite`, `build-coupled-sets`
- **Validates**: `tests/integration/test_remote_update.py`, `tests/unit/test_llm_stack_compat.py`, `tests/unit/test_llm_stack_degraded_start.py`
- **Assigned To**: `deps-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Update the four existing tests per Test Impact; add the new files and cases named there.
- For each of the three spike-2 defects, confirm the new test **fails** against the pre-fix helper before it passes against the fixed one; record that red-state proof.
- Do not mock the LLM stack in leg (a) of task 8.

### 8. Two-leg rollback verification
- **Task ID**: `verify-known-bad-rollback`
- **Depends On**: `build-tests`
- **Validates**: the two captured transcripts — leg (a) unmocked, leg (b) resolution-stubbed
- **Assigned To**: `deps-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Leg (a), genuinely unmocked:** on a throwaway copy of the repo (never the shared checkout), write `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0`, run `uv sync --all-extras`, invoke the gate subprocess directly, and assert non-zero exit with `unexpected keyword argument 'temperature'` in the output.
- **Leg (b), resolution-stubbed:** `monkeypatch.setattr(deps, "get_pypi_latest", lambda p, **k: {"anthropic": "1.0.0", "pydantic-ai-slim": "2.9.0"}[p])`, run `auto_bump_deps`, assert `result.rolled_back is True` and both pins restored. **State in the transcript that this leg stubs version resolution** — PyPI's real latest pair (`anthropic 1.0.0` + `pydantic-ai-slim 2.35.0`) is compatible, so an unstubbed run would pass the gate and roll nothing back.
- Assert the converse on a good pair: the gate passes and the bump survives.
- Paste both transcripts into the PR description.

### 9. Documentation
- **Task ID**: `document-feature`
- **Depends On**: `verify-known-bad-rollback`
- **Validates**: `docs/features/llm-stack-compat-gate.md` exists and is indexed in `docs/features/README.md`
- **Assigned To**: `deps-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/llm-stack-compat-gate.md` and add the `docs/features/README.md` index row.
- Correct `.claude/skills/update/SKILL.md` lines 66-72.
- Cross-reference from `docs/features/remote-update.md`; update `docs/features/nonharness-llm-wrapper.md`.
- Note the expected `/update --cron` wall-clock delta from per-set syncing.

### 10. Final validation
- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Validates**: the Verification table
- **Assigned To**: `deps-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each Success Criterion, including that both task-8 transcripts are in the PR description.
- Confirm the PR body says `Refs #3001`, **not** `Closes #3001` — Work Items 2 and 3 keep the issue open.

## Verification

Rows assert *declarations and executed paths*, not file text. A `grep -c` that
counts mentions cannot distinguish an invocation from a comment — the round-1
critique caught exactly that, where `grep -c "anthropic" scripts/update/deps.py`
returned **9** on unmodified main (the stopgap comment block at `deps.py:313-328`)
and so could never fail.

| Check | Command | Expected |
|-------|---------|----------|
| Live LLM call works on the branch's pins | `.venv/bin/python -c "import asyncio;from pydantic import BaseModel;from agent.llm.wrapper import run_typed;O=type('O',(BaseModel,),{'__annotations__':{'answer':str}});print(asyncio.run(run_typed('Reply with answer=hi',O)))"` | exit code 0 |
| Predicate exists and reports compatible | `.venv/bin/python -m utils.llm_stack_compat --json` | exit 0, JSON `"compatible": true` |
| Predicate does not import from `scripts/` | `.venv/bin/python -c "import ast,sys;t=ast.parse(open('utils/llm_stack_compat.py').read());assert not [n for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom)) and 'scripts' in (getattr(n,'module','') or '')+''.join(a.name for a in getattr(n,'names',[]))]"` | exit code 0 |
| Coupled set membership is declared | `.venv/bin/python -c "from scripts.update.deps import AUTO_BUMP_SETS; ms={m for s in AUTO_BUMP_SETS for m in s.members}; assert {'anthropic','pydantic-ai-slim'} <= ms, ms"` | exit code 0 |
| `openai` is in no coupled set | `.venv/bin/python -c "from scripts.update.deps import AUTO_BUMP_SETS; assert 'openai' not in {m for s in AUTO_BUMP_SETS for m in s.members}"` | exit code 0 |
| New sets do not inherit the billed llm phase | `.venv/bin/python -c "from scripts.update.deps import CoupledSet; assert CoupledSet(['x'], reason='t').gates == ('import','pytest')"` | exit code 0 |
| Gate invocation is real, not a mention | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "llm_gate" -q` (monkeypatches `deps.run_cmd`, asserts the llm phase argv reaches the predicate and that failure returns phase `"llm"`) | exit code 0 |
| Import-only gate is gone | `grep -c "import anthropic; import claude_agent_sdk" scripts/update/deps.py` | match count == 0 |
| Verify runs the check unconditionally | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "verify_compat" -q` | exit code 0 |
| Startup is degraded, not fatal | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -q` | exit code 0 |
| Alert is independent of the LLM path | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "independence" -q` (alert fires while `run_typed` raises) | exit code 0 |
| Typed exception preserves existing fail-safes | `.venv/bin/python -c "from agent.llm.wrapper import LLMCallError, LLMStackIncompatible; assert issubclass(LLMStackIncompatible, LLMCallError)"` | exit code 0 |
| `openai` has an exact pin | `grep -cE '"openai==' pyproject.toml` | output > 0 |
| Pin comment keeps the constraint and adds the pointer | `grep -c "AUTO_BUMP_SETS" pyproject.toml` | output > 0 |
| Update-system tests pass | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Anti-criterion: no dependency upgrade smuggled in | `grep -cE '"pydantic-ai-slim\[anthropic\]==2\.9\.0"' pyproject.toml` | output > 0 |
| Anti-criterion: `anthropic` pin unchanged by this lane | `grep -c '"anthropic==0.125.0"' pyproject.toml` | output > 0 |
| Anti-criterion: Work Item 2 files untouched | `git diff --name-only origin/main...HEAD -- models/agent_session.py tests/unit/test_agent_session.py \| wc -l` | output is 0 |
| Update skill doc corrected | `grep -c "import check" .claude/skills/update/SKILL.md` | match count == 0 |
| Feature doc exists | `test -f docs/features/llm-stack-compat-gate.md` | exit code 0 |

## Critique Results

Round 1 — FULL depth (force-FULL: the plan edits `.claude/skills/update/SKILL.md`, a doctrine path).
Verdict: **NEEDS REVISION** — 5 blockers, 5 concerns, 3 nits.
**Revision applied 2026-08-26.** All 5 blockers, all 5 concerns, and all 3 nits addressed below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Owner anchor 1 (boundary) | The invariant was scoped inside `auto_bump_deps`, and the run-boundary check was deferred to Rabbit Holes / Open Question 3 — backwards from the owner's decision. The incident pin arrived via a hand-staged `git add`, which an auto-bump-scoped gate never sees. | **addressed** | Restructured around a standalone `utils/llm_stack_compat.py::check_llm_stack_compat()` with four call sites (`/update` verify unconditionally, bridge startup, worker startup, auto-bump). Auto-bump demoted in the prose to "one caller of the gate" (Data Flow, Solution). Run-boundary work is now tasks 2-4, not a rabbit hole. The `[ORDERED]` No-Go is removed with a note. Old Open Question 3 marked resolved and the Open Questions section retired. **Named deviation:** the module lives at `utils/llm_stack_compat.py`, not the suggested `scripts/update/compat_gate.py`, because bridge/worker importing from `scripts/update/` inverts the dependency direction; rationale recorded in Architectural Impact. |
| BLOCKER | Owner anchor 2 (failure posture) | No startup posture existed and no alert was designed; Open Question 2 still asked the operator to confirm fail-closed, which the owner had declined. | **addressed** | New **## Failure Posture** section states the start-degraded contract (process does not exit, Telegram intake keeps queueing, `LLMStackIncompatible(LLMCallError)` at LLM entry points) and designs the alert against the independence constraint: no `run_typed`, no drafter, no summarization, static body plus two versions. Three named channels with per-channel independence arguments (Sentry fatal, `logger.critical` sentinel, dashboard health field); a direct Telegram push is considered and rejected with reasons. Task 3 includes the independence proof — the alert fires in a test where `run_typed` raises — and it is a Success Criterion. Auto-bump's fail-closed rollback is retained and explicitly named a separate axis. Open Question 2 retired as answered. |
| BLOCKER | Risk & Robustness | The un-mockable acceptance criterion was unreachable: PyPI latest is `anthropic 1.0.0` + `pydantic-ai-slim 2.35.0`, a compatible pair, so an all-or-nothing coupled bump rolls nothing back. | **addressed** | Split into two named legs in Success Criteria and task 8. Leg (a) is genuinely unmocked — throwaway checkout with the bad pair staged, real `uv sync`, gate subprocess invoked directly, assert non-zero exit and the `temperature` message. Leg (b) monkeypatches `get_pypi_latest` at the single resolution seam and asserts `rolled_back is True`, and the plan **states** that it stubs resolution rather than claiming the whole path is unmocked. |
| BLOCKER | Scope & Value | `openai` was welded into the coupled set on a coupling that does not exist, and enrolling it in auto-bump would silently execute the `openai 2.x → 3.x` major bump the plan lists under No-Gos. | **addressed** | Owner verified both sides; recorded as **spike-5** with all three facts: (1) no declared coupling — `pydantic-ai-slim`'s locked deps carry no `openai`, which is only under `pydantic-ai`'s `[openai]` extra we do not install; (2) the ImportError is nevertheless real, from our own unguarded `wrapper.py:48` module-scope import; (3) `OpenAIChatModel` has exactly one use site, `wrapper.py:213` inside `run_typed_local`, against `OllamaProvider` — a **local Ollama server**, never OpenAI. Coupled set is now `["anthropic", "pydantic-ai-slim"]` only; `openai` is in no set, asserted in code and in Verification, with the reasoning in the `AUTO_BUMP_SETS` docstring. The coupling is fixed at the import instead (new task 5, preferring the lazy/guarded form), and the predicate still exercises the real `agent.llm` import graph because that graph is what catches this class of break. |
| BLOCKER | History & Consistency | The central verification `grep -c "anthropic" scripts/update/deps.py` returns 9 on unmodified main and can never fire; the companion `run_typed` grep has the same defect. | **addressed** | Verification table rewritten to assert declarations and executed paths. Membership is a Python import assertion over `AUTO_BUMP_SETS`; gate invocation is a monkeypatched unit test asserting the llm phase's argv and the `"llm"` failure phase; new rows assert `openai` exclusion, the default `gates` tuple, the `LLMStackIncompatible` subclass relation, and that the predicate imports nothing from `scripts/`. The one genuinely-failable negative grep (`import anthropic; import claude_agent_sdk` == 0) is kept. A preamble records why grep-count rows were removed. |
| CONCERN | Risk & Robustness | The gate ran for every set, so a lapsed key or provider outage would roll back `["claude-agent-sdk"]` too. | **addressed** | `CoupledSet.gates` defaults to `("import", "pytest")`; only the LLM set opts into `("llm", ...)`. Asserted in Verification and in a unit test. Risk 2 rewritten to scope the Anthropic dependency to one set. |
| CONCERN | Risk & Robustness | A failed restore sync leaves `uv.lock` and `pyproject.toml` describing different worlds, and a later successful bump pushes the poisoned lockfile. | **addressed** | Technical Approach captures the restore result → `AutoBumpResult.restore_failed` + `git checkout -- uv.lock`; `run.py`'s commit branch guarded with `not bump.restore_failed`. Test `test_worktree_clean_after_every_rollback_path` plus `test_restore_failure_blocks_commit`; a Success Criterion asserts `git status --porcelain` is empty after every rollback path. |
| CONCERN | Scope & Value | The gate's coverage was narrower than the set it claimed to protect; the `openai`-importing modules were exercised by no phase. | **addressed** | Resolved by the preferred route the critic named: `openai` is out of the set entirely (spike-5), so there is no uncovered member. The Documentation section still requires the feature doc to name what the gate checks and cannot check, explicitly. |
| CONCERN | History & Consistency | The Major-drift premise was already resolved by `7a30b88f7`, but the body still asserted present-tense breakage and task 1 was still an edit-then-push. | **addressed** | Freshness Check disposition changed to **Minor drift**, the `pyproject.toml:12` row reads `RESOLVED by 7a30b88f7`, and the drift narrative is date-stamped as superseded rather than deleted so Prior Art and Risk 1 stay legible. Task 1 is now pure verification — no edit, no commit, no push — and stops the lane if the probe fails. Old Open Question 1 retired. The second bad-pin arrival is promoted into the Problem statement as the argument for the run boundary. |
| CONCERN | History & Consistency | Deleting the `pyproject.toml` comment block would strip a now-correct, load-bearing warning while requiring `grep "Do NOT move to 1.x"` → 0. | **addressed** | Technical Approach and Documentation now specify replacing the four-line block with a two-line form that **keeps** the constraint and adds the `AUTO_BUMP_SETS` pointer. The `"Do NOT move to 1.x"` → 0 row is dropped; the row is now `grep -c "AUTO_BUMP_SETS" pyproject.toml` > 0. A new anti-criterion row asserts the `anthropic==0.125.0` pin is unchanged by this lane. |
| NIT | Scope & Value | Per-set sync fan-out multiplies `uv sync` calls without a stated budget. | **addressed** | Technical Approach skips a set's sync entirely when no member's pin changed; task 9 records the expected `/update --cron` wall-clock delta in the feature doc. |
| NIT | Structural check | `agent/routing.py` does not exist; the caller is `bridge/routing.py`. | **addressed** | Corrected everywhere, and the full non-harness caller set is now enumerated in Data Flow and Agent Integration. |
| NIT | Structural check | Tasks 6, 7, 8 carried no `Validates:` field. | **addressed** | All ten tasks now carry `Validates:`. |

**Structural check results** (re-run after revision)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation (with a `docs/features/` checkbox), Update System, Agent Integration, Test Impact all present and substantive |
| Popoto migration check | N/A | No Popoto model touched |
| Task numbering | PASS | Tasks 1-10, no gaps |
| Dependencies valid | PASS | Every `Depends On` resolves; task 7 fans in from 3, 4, and 6; no cycles |
| Task validation commands | PASS | All ten tasks carry `Validates:` |
| File paths exist | PASS | `bridge/routing.py` substituted for the nonexistent `agent/routing.py`; `utils/llm_stack_compat.py`, `docs/features/llm-stack-compat-gate.md`, and the three new test modules are intentionally new |
| Prerequisites met | PASS | `ANTHROPIC_API_KEY` resolves; `uv` on PATH; `get_pypi_latest('anthropic')` returns a version; Sentry initialized via `monitoring/sentry_config.py` |
| Cross-references | PASS | The rollback criterion is split into a reachable unmocked leg and a labelled resolution-stubbed leg; membership maps to an import assertion that fails on unmodified main |
| No-Go vs. Solution | PASS | `openai` is in no coupled set, so nothing in the Solution auto-executes the deferred 2.x → 3.x bump |
