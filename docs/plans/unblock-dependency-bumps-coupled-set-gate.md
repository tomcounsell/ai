---
status: Ready
type: bug
appetite: Large
owner: valor
created: 2026-08-26
revision_applied: true
revision_applied_at: 2026-09-02T07:42:42Z
tracking: https://github.com/tomcounsell/ai/issues/3001
last_comment_id: 5505317383
---

# Import-safe LLM stack + run-boundary compat gate + coupled-set bumping

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
picture — the pin arrived through `git add pyproject.toml`.

**Current behavior:**

- **Nothing anywhere asserts that the installed `anthropic` and `pydantic-ai-slim`
  are a compatible pair.** Not `/update` verify, not bridge startup, not worker
  startup. A process can boot cleanly on a stack where every non-harness LLM call
  raises `TypeError` at argument binding, and does not find out until a caller
  tries — which on 2026-08-24 was six hours later.
- Worse: the stack can fail in a way where the process **cannot boot at all**.
  `agent/llm/wrapper.py:44-50` imports the entire third-party stack
  (`anthropic` plus five `pydantic_ai` symbols) at module scope, and
  `agent/anthropic_client.py:44` (which wrapper imports) imports `anthropic` at
  module scope too. `agent.llm` has **six module-scope consumers**
  (`bridge/routing.py:21`, `bridge/job_router.py:46`, `bridge/agent_catchup.py:59`,
  `agent/memory_extraction.py:34`, `tools/email_cs/triage.py:21`, and
  `scripts/nightly_regression_tests.py:97` — the nightly detector itself, so
  under today's module-scope imports an ImportError kills the very job that is
  supposed to notice).
  `bridge/telegram_bridge.py:130` imports `bridge.routing` at module scope,
  ~1070 lines before `main()`. An ImportError anywhere in the stack (the exact
  mode spike-5 reproduced) therefore kills the bridge at import time and launchd
  crash-loops it. **No startup gate placed inside `main()` can ever observe this
  failure class** — this is round-2 critique blocker B-a, and it is the reason
  this plan's shape changed.
- `scripts/update/deps.py::run_smoke_test` phase 1 is
  `import anthropic; import claude_agent_sdk`, phase 2 is one fast pytest file.
  Neither makes an LLM call, so neither can catch an argument-binding break.
- `scripts/update/deps.py::auto_bump_deps` iterates `AUTO_BUMP_PACKAGES`
  (`deps.py:329`, currently `["claude-agent-sdk"]`) one package at a time. There
  is no notion of packages that must move together. `anthropic` is excluded by
  the `d0c02bde5` stopgap, which deliberately leaves us stale.
- The pin-editing helpers cannot express a coupled set correctly:
  `get_pinned_version("openai")` returns a version scraped out of a *comment*,
  and `bump_pin_in_pyproject("pydantic-ai-slim", ...)` silently returns `False`
  because the pin carries an extras marker. Both verified in spike-2.

**Desired outcome:**

- **`agent.llm` becomes import-safe by contract.** `import agent.llm` cannot
  fail on any stack state; every failure of the LLM stack — ImportError and
  signature break alike — becomes a call-time event, where degraded handling
  can actually live. This is one enforcement point replacing discipline across
  an unbounded consumer set, and it is what makes every other piece of this
  plan reachable.
- A single standalone predicate, `agent/llm/compat.py::check_llm_stack_compat()`,
  answers "is the installed LLM stack a pair we can actually call?" — invoked at
  the **run boundary**: `/update` verify, bridge startup, worker startup, *and*
  auto-bump. Whatever route a bad pin takes, the next thing that starts sees it.
- On an incompatible stack, services **start degraded and alert loudly**.
  Telegram keeps receiving and queueing. LLM-dependent paths fail fast with a
  typed error. The alert fires from the degraded-flag *resolution itself*, not
  from any process's startup sequence, so no entry path can reach the broken
  stack silently.
- `anthropic` + `pydantic-ai-slim` are declared as one coupled set and are bumped
  atomically or not at all. `openai` is **not** in the set (spike-5). The LLM set
  carries a **hold** so re-enrolling `anthropic` cannot auto-execute the Step 2
  upgrade on the next cron tick.
- The rollback behavior is verified by observation on a deliberately staged
  known-bad pair, not asserted in a mock.

This plan is **Step 1 of the sequencing agreed in
[issue comment 5420111955](https://github.com/tomcounsell/ai/issues/3001#issuecomment-5420111955)**:
gate first, upgrade second. The dependency upgrade itself (including the
`openai` 2.x → 3.x major bump) is explicitly **not** in this lane.

## Freshness Check

**Baseline commit:** `93fb790ef` — `main`'s tip at round 8 (2026-09-02). This is
the single baseline for every claim in this section; earlier rounds' SHAs are
deliberately not restated here. The round-8 critique re-verified every reference
below by execution at this commit, and every commit between it and the tip at
this revision touches only `docs/plans/*.md` (verified by `git log --name-only`),
so no tracked source file has moved since.
**Issue filed at:** 2026-08-25T05:16:36Z
**Disposition:** **Minor drift.** Every substantive premise holds: the
`agent/llm/` module-scope imports, the six module-scope consumers,
`AUTO_BUMP_PACKAGES`, the maintainer-only Step 3.5 shape, the `_sentry_before_send`
hibernation drop, and the filesystem-derived dashboard health fields are all
unchanged. `pyproject.toml:12` still reads `"anthropic==0.125.0"` (restored by
hotfix `7a30b88f7` after the second bad-pin arrival via `d0c02bde5`), and
`"pydantic-ai-slim[anthropic]==2.9.0"` and `"openai>=1.0.0"` are unchanged. What
moved is `scripts/update/run.py` line numbering (`97672207d` rewrote ~594 lines
of it) and `bridge/telegram_bridge.py`'s `_sentry_before_send` position;
corrected below. One **new arrival** materially changes task 2's execution — the
module-scope-env ratchet, recorded as its own subsection.

The two-arrivals history is retained in Problem and Prior Art because it is the
evidence for the run-boundary placement.

### File:line references re-verified at the baseline commit above

| Reference | Claim | Status |
|---|---|---|
| `agent/llm/wrapper.py:44-50` | full third-party stack imported at module scope | **Holds** |
| `agent/anthropic_client.py:44` | `import anthropic` at module scope, imported by wrapper | **Holds** |
| module-scope consumers of `agent.llm` | routing:21, job_router:46, agent_catchup:59, memory_extraction:34, email_cs/triage:21, nightly_regression_tests:97 | **Holds** — six, not five. Re-grepped at the baseline with `grep -rnE '^(from agent\.llm(\.[a-z_]+)? import\|import agent\.llm)'`, which catches the submodule form the earlier `from agent.llm import` grep missed |
| `bridge/telegram_bridge.py:130` | `from bridge.routing import ...` at module scope; `main()` at `:1203` | **Holds** |
| `scripts/update/deps.py:250` / `:277` / `:329` / `:391` / `:414` / `:463` / `:474` | `get_pinned_version`, `verify_critical_versions`, `AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]`, `bump_pin_in_pyproject`, `run_smoke_test`, `auto_bump_deps` (iterating packages independently) | **Holds** — `deps.py` was not touched by any commit since the reshape |
| ~~`scripts/update/run.py:1304` / `:1169`~~ → **`run.py:1493` / `:1358` and `:1485`** | Step 3.5 calls `auto_bump_deps`; `is_lockfile_maintainer` computed at `:1358` and gates Step 3.5 at `:1485`/`:1491` | **Drifted, claim holds.** `97672207d` (Wave-1 hotfix sweep) restructured `run.py`. The maintainer gate, the per-bump skip log, and the commit/push/restart ordering are structurally unchanged |
| ~~`run.py:1317`~~ → **`run.py:1514`** | `"Smoke test passed after bump"` log line whose text `extract_update_warnings` parses | **Drifted, claim holds** |
| `pyproject.toml:12` | `anthropic==0.125.0` | **Holds** |
| `tests/unit/test_llm_wrapper_local.py:52` | tests monkeypatch `wrapper_mod.OpenAIChatModel` as the network-isolation seam | **Holds** (round-2 B-b) |
| ~~`bridge/telegram_bridge.py:70-77`~~ → **`:55`** (`configure_sentry` wired at `:80`) | `_sentry_before_send` drops all events while hibernating | **Drifted, claim holds** (round-2 B-d) |
| `ui/app.py:373` (`_get_bridge_health`, stats `data/last_connected`) / `:511` (`_get_worker_health`) / ~~`:906`~~ → **`:907`** (`dashboard_json`) | dashboard health fields derive from filesystem artifacts, served by a separate process | **Holds** (round-2 blocker) |
| `agent/llm/` package contents | `__init__.py`, `wrapper.py` only — no `compat.py` yet | **Holds** (nothing pre-empted this lane) |

**Builders: these line numbers are a re-verification record, not addresses.**
Locate every site by symbol, not by line — `run.py` has now drifted twice.

### New arrival: the module-scope-env ratchet blocks a naive task 2

`2b926acae` (2026-09-01, five days after the reshape) **restored** the
module-scope-env ratchet: `.claude/hooks/validators/validate_no_module_scope_env.py`
blocks any commit that **adds or modifies** a line performing `os.environ.get` /
`os.getenv` / `os.environ.setdefault` / `os.environ.pop` at module top level.
Detection is AST-based and **diff-scoped** — pre-existing sites on untouched
lines are exempt, but a *moved* one is not.

`agent/llm/wrapper.py:167` is exactly such a site:

```python
LOCAL_TYPED_HARD_TIMEOUT = float(os.environ.get("LOCAL_TYPED_HARD_TIMEOUT", "20.0"))
```

Task 2 rewrites `wrapper.py`'s module scope wholesale. If that rewrite shifts,
reflows, or re-indents line 167, the hook fires and the commit is **blocked** —
a build-time surprise the round-3 plan could not have known about. The
disposition is not to work around the hook: migrate the read to a lazily-read
`config/settings.py` field (the `TimeoutSettings` pattern from #1968 that the
guard's own docstring prescribes) as part of task 2. It is one field, it is the
same direction the rest of the repo is draining toward, and it is strictly less
work than tiptoeing around a line in the middle of the module being rewritten.
Recorded as a task-2 bullet and a Test Impact row.

This is also **useful prior art for the import-safety contract itself**: the
ratchet is the repo's established shape for "an AST rule enforced at commit
time, diff-scoped so it never blocks its own migration." The contract in this
plan is enforced by a pytest architectural test rather than a hook, which is the
right call for an invariant that starts at zero violations (nothing to drain, so
nothing to scope) — but a reviewer asking "why not a hook?" now has an answer on
the record.

### Cited sibling issues/PRs — re-checked 2026-09-02

- **#2932** — CLOSED 2026-08-25, folded into #3001; its scope is Work Item 3, out of this lane.
- **#2960–#2999** — closed duplicates; their shared-root-cause diagnosis not relied on.
- **#2949** / `69dc69568` — MERGED 2026-08-24; owns Work Item 2, out of this lane.
- **#3016** — still **OPEN**. Independent `test_promise_gate_real_api` failure, separately filed; not a blocker for this lane.
- **#3073 / #3074 / #3075** — filed 2026-09-02 (issue comment `5505317383`),
  carrying the three chunks this lane defers: Step 2's upgrade (#3073, blocked
  on this lane), the `worker_key` residue (#3074), and nightly triage filing
  (#3075). Recorded in No-Gos with their own slugs.
- **#3001** — still OPEN. With all three chunks separately tracked, the owner
  may prefer to close it on this lane's merge; task 9 keeps `Refs #3001` as the
  default and defers the choice.

### Commits since the reshape that touched lane files

`97672207d`, `37e60af6f`, `b4daa7861`, `b152b7d3c` (all `scripts/update/run.py`);
`b152b7d3c` (`bridge/telegram_bridge.py`, import repointing off the
`agent_session_queue` re-export hub); `2b926acae` and `97672207d`
(`pyproject.toml`, `claude-agent-sdk` bumps only). **`scripts/update/deps.py`,
`agent/llm/`, `agent/anthropic_client.py`, `ui/app.py`,
`tests/integration/test_remote_update.py`, and `tests/unit/test_llm_wrapper_local.py`
were not touched at all.** No commit partially addresses, changes the root cause
of, or fixes this lane's problem.

### Active plans overlapping this area

None. `docs/plans/overclaim-guard-greps-whole-worktree.md` (#2807) names
`agent/llm/wrapper.py` twice — once inside a prose example, once as a pathspec
exclusion in a grep invocation. It modifies no file this lane touches. No other
plan matches on `auto_bump`, `AUTO_BUMP`, `agent/llm`, or `anthropic==`.

## Prior Art

- **#3001 stopgap `d0c02bde5`** — removed `anthropic` from `AUTO_BUMP_PACKAGES`.
  Bought time; is the thing this plan replaces. It also *itself* re-shipped the
  bad pin, making it the strongest single argument for a run-boundary check.
- **`7db5b82bb`** and **`7a30b88f7`** — two separate emergency reverts of the same
  pin, eleven commits apart. A one-off manual revert is not a durable fix.
- **`9d1488ccb`** — the breaking bump; landed through the existing smoke gate
  cleanly, which is the whole indictment.
- **`884302861`** — introduced `AUTO_BUMP_PACKAGES` and the smoke-then-rollback
  shape. The scaffolding is right; it lacks coupling and a call-level gate.
- **`docs/archive/plans-completed/sdlc-1091.md`** — auto-bump commits and pushes
  *during* the cron run; the restart gate reads HEAD after `run.py --cron`
  returns. That ordering must be preserved.
- **`agent/index_drift.py:224`** — the repo's existing `sentry_sdk.capture_message`
  pattern, including the "capture failed" fallback log. The degraded alert
  follows this shape.
- **`feedback_single_authoritative_liveness`** (repo doctrine) — the module that
  owns a lifecycle exposes the health API. This is why the compat predicate
  lives in `agent/llm/`, beside the stack it judges.

No prior attempt at import-safety, a run-boundary compat check, or coupled-set
bumping exists.

## Research

**Queries used:**
- `pydantic-ai anthropic 1.0 temperature top_p removed Messages API compatibility fix release`

**Key findings:**

- **`pydantic-ai-slim>=2.33.0` is the first release supporting `anthropic>=1.0.0`.**
  Every earlier release allowed `anthropic 1.0.0` in metadata without supporting
  it. Sources: [pydantic-ai changelog](https://ai.pydantic.dev/changelog),
  [Anthropic Python SDK v1.0 migration](https://www.digitalapplied.com/blog/anthropic-python-sdk-v1-breaking-change-migration).
  This is the exact boundary the coupled set enforces; the upper-bound hole is
  upstream metadata we do not control, so a local check is the only remedy.
- **anthropic 1.0.0 also moved its HTTP layer to `httpx2`**, and
  `AnthropicProvider(http_client=...)` now rejects legacy `httpx.AsyncClient`.
  A future bump can break `run_typed` through the transport as well as argument
  binding — which is why the auto-bump gate makes a real call, not just a
  signature check. (`wrapper.py` constructs `AsyncAnthropic` directly without a
  custom `http_client`, so it is not exposed to that specific break today.)
- **Anthropic deprecated non-default `temperature`/`top_p`/`top_k` server-side on
  Opus 4.7+** (HTTP 400). The gate must treat a provider-side 400 as a genuine
  failure; see Risk 3 on distinguishing it from flakiness.

Saved to memory as `9716dcf2cf4a46eda06bd480554ea1ff`.

## Spike Results

### spike-1: Does the known-bad pair actually fail through `run_typed`?
- **Method**: live call in the repo venv at the incident baseline
- **Finding**: **Yes.** `run_typed` raises `LLMCallError` wrapping
  `TypeError: AsyncMessages.create() got an unexpected keyword argument 'temperature'`.
  Failure at argument binding — no network I/O, no API cost, sub-second.
- **Impact**: the check's negative case is cheap and deterministic, which is what
  makes a compat predicate affordable at bridge/worker startup.

### spike-2: Can the existing pin helpers express a coupled set?
- **Finding**: **No — three defects, each of which silently produces exactly the
  half-bump this plan exists to prevent:**
  1. **`get_pinned_version` matches comment text.** `"openai"` appears inside the
     `pydantic-ai-slim` line's comment, so it returns that line's `2.9.0`. The
     real declaration is `"openai>=1.0.0"` — a floor, with no `==` anywhere.
  2. **`anthropic` resolves correctly only by line order** — the
     `pydantic-ai-slim[anthropic]==2.9.0` line also contains `anthropic` and `==`.
  3. **`bump_pin_in_pyproject` cannot match an extras pin** (`"{package}==[^"]*"`
     vs `"pydantic-ai-slim[anthropic]==2.9.0"`). It returns `False`, which
     `auto_bump_deps` records as an error and then **continues** — bumping the
     other members. The incident shape, reproduced exactly.
- **Impact**: the helpers must become declaration-aware (extras-tolerant,
  comment-blind, refuse-loudly). Each defect gets its own regression test.

### spike-3: Is a real `run_typed` call viable from the update process?
- **Finding**: Yes. `utils/api_keys.py::get_anthropic_api_key` resolves a key on
  the maintainer machine (presence checked, no value echoed), including on the
  headless cron path. From `auto_bump_deps` the call must run **inside the
  target venv** (`{project_dir}/.venv/bin/python`), never the update process's
  own interpreter — it imported its modules before the sync. Mirrors the
  existing `_markitdown_importable` subprocess pattern.

### spike-4: What does `auto_bump_deps` rollback restore today?
- **Finding**: whole-file — `original_content` snapshotted once before the loop,
  rewritten wholesale on any failure. With sets, one bad set would revert every
  other set's good bump. The restore's own `sync_dependencies` result is
  discarded.
- **Impact**: per-set snapshot/restore, sequential set evaluation, an explicit
  `restore_failed` flag.

### spike-5: Is `openai` coupled to `anthropic` + `pydantic-ai-slim`?
- **Finding**: **No packaging coupling; a real self-inflicted import coupling.**
  1. `pydantic-ai-slim`'s locked dependencies include no `openai`. `openai>=3.0.0`
     is declared only under the `[openai]` extra, which we do not install.
  2. The ImportError is nevertheless real: `wrapper.py:48`'s module-scope
     `from pydantic_ai.models.openai import OpenAIChatModel` fails under
     `pydantic-ai 2.34.0` + our pinned `openai 2.30.0`, taking down `run_typed`
     (the Anthropic path) with it via the `__init__` re-export.
  3. `OpenAIChatModel` is used at exactly one site (`wrapper.py:213`, inside
     `run_typed_local`), constructed against `OllamaProvider`. It talks to local
     Ollama, never OpenAI.
- **Impact**: `openai` joins **no** coupled set. The coupling is fixed where it
  lives: at the import. Under this plan's import-safety contract that fix is not
  a special case — the *entire* third-party stack goes lazy, `OpenAIChatModel`
  included. There is no per-symbol option menu.

## Data Flow

### Today: how a bad pin reaches a running process (three routes, zero checks)

1. **Auto-bump route** — `run.py`'s Step 3.5 `deps.auto_bump_deps(project_dir)`
   call (maintainer-only, gated on `is_lockfile_maintainer`)
   → `auto_bump_deps` → pin rewrite → sync → `run_smoke_test` (import check +
   one pytest file — **the layer that failed to observe the break**) → commit +
   push.
2. **Hand-staged route** — a human or agent edits `pyproject.toml`, syncs,
   commits. **No check at all.** This is how `d0c02bde5` re-shipped the pin.
3. **Follower route** — every non-maintainer machine runs `uv sync --frozen` on
   its next `/update`. **No check at all.**

All three converge on a bridge or worker process booting on the bad stack — or,
in the ImportError mode, failing to boot and crash-looping under launchd.

### After this plan: one import-safe module, one predicate, four call sites

```
agent/llm/  (import-safe by contract: module scope holds no third-party imports)
  wrapper.py   — lazy memoized stack loader; run_typed / run_typed_local call it
  compat.py    — check_llm_stack_compat() -> CompatResult; degraded flag;
                 alert emitter (bound to flag resolution, not startup);
                 `python -m agent.llm.compat --json` for subprocess callers
   |
   +-- /update verify (scripts/update/verify.py) ... subprocess, every run
   +-- bridge startup (bridge/telegram_bridge.py::main) ... force early resolution
   +-- worker startup (worker/__main__.py)             ... force early resolution
   +-- auto-bump gate (scripts/update/deps.py, "llm" phase) ... subprocess, target venv
```

Routes 2 and 3 are now covered at the next `/update` verify and the next service
start. Route 1 keeps its rollback, which becomes *one caller of the predicate*
rather than the whole safety story. And because `import agent.llm` can no longer
fail, the startup call sites are reachable in **every** failure class — the
round-2 B-a defect is dissolved structurally, not patched.

## Failure Posture

This section is the owner's recorded decision on #3001 and is not a tradeoff the
build may re-open.

### At the run boundary: start degraded, alert loudly

An incompatible stack at bridge or worker startup **does not exit the process.**

- The process **comes up**. Telegram intake continues: messages are received,
  AgentSessions are enqueued to Redis, nothing is dropped.
- A degraded flag is set. `run_typed` / `run_typed_local` **fail fast with a
  typed `LLMStackIncompatible`** (a subclass of the existing `LLMCallError`, so
  every existing call site's fail-safe posture keeps working unchanged) instead
  of a raw provider `TypeError`. Each site's own conservative default still
  applies.
- The condition is **alarmed** — from the flag resolution itself (see below).

Rationale, in the owner's terms: degraded-but-running is precisely the state that
hid this incident for six hours. Exiting would trade a six-hour silent LLM outage
for an immediate total outage plus a launchd crash-loop, which is worse. So
**the alert is the entire safety property.** If the alert does not fire, this
plan has shipped nothing.

### The alert is bound to flag resolution, not to startup

The degraded flag is **lazily self-resolving and memoized**: the first read —
whether from a startup hook or from a `run_typed` call in a process that never
ran one — evaluates the predicate, and **the first transition to degraded emits
the alert, from inside the resolver**. Startup hooks in bridge and worker do
nothing but force resolution early, so the alert fires at boot rather than at
first call. This closes both round-2 findings at once: no entry path can reach
the broken stack without the alert firing (B-e), and no ordering race between
boot and first call exists (Race 3 is designed away rather than mitigated).

### Alert independence constraint

Because the thing being alarmed *is the LLM stack*, the alert must not route
through it. Explicitly forbidden in the alert path:

- **No `run_typed` / `run_typed_local`.** They are exactly what is broken.
- **No message drafter, no LLM summarization, no persona pass.**
- **No dynamic body composition.** The alert body is a **static string** plus
  the two resolved version numbers and the captured exception type and message.

This is a deliberate, named exception to the standing "never let raw text speak
to chat" convention (`feedback_drafter_comms_layer`): the drafter is unavailable
by construction, and a silent alert is the failure being prevented. The feature
doc must state this so a future reader does not "fix" it.

### Alert channels, and why each actually fires

Three channels, fired unconditionally on the first transition to degraded.
"Unmissable" is a property of redundancy across independent transports.

| Channel | Mechanism | Why it actually fires |
|---|---|---|
| Sentry | `sentry_sdk.capture_message(<static body>, level="fatal")`, following `agent/index_drift.py:224` including its capture-failed fallback log. | `sentry-sdk`'s own HTTP transport; shares no code with the LLM stack. **Hibernation exemption required:** `bridge/telegram_bridge.py`'s `_sentry_before_send` (`:55`, wired into `configure_sentry` at `:80`) drops every event while `is_hibernating()` — a persistent flag, not a brief window. The hook must pass events whose message carries the degraded sentinel token. Hibernation means "we cannot reach Telegram", which is exactly when a broken LLM stack most needs to be visible elsewhere. |
| Logs | `logger.critical` with a fixed, greppable sentinel token. | stdlib only. Survives a Sentry DSN outage. |
| Dashboard | The resolver writes a **per-process** marker file at `<repo>/data/llm-stack-degraded.{proc}` (versions + `exc_type` + which axis failed) — **only for the two callers that pass a stable `proc`, `bridge` and `worker`**; every other caller writes none. The path comes from the module-level `_MARKER_DIR` seam (default `Path(__file__).resolve().parents[2] / "data"`) via `_marker_path(proc)` — never cwd-relative, mirroring `ui/app.py:376`'s `Path(__file__).parent.parent / "data" / ...`; `dashboard_json` (`ui/app.py:907`) reads `sorted((repo / "data").glob("llm-stack-degraded*"))` with the siblings' fail-quiet `try/except OSError` and renders red while **any** marker exists, naming the degraded processes. | `/dashboard.json` is served by a **separate uvicorn process** — an in-process flag can never reach it. Every existing health field derives from a filesystem/Redis artifact (`_get_bridge_health` stats `data/last_connected`, `ui/app.py:373`); this follows that pattern. **The marker must be cleared on a healthy resolution** — a stuck-red dashboard equals no dashboard channel; the clear leg gets its own test. |

### Why the dashboard marker is per-process, not shared

A single shared marker has no writer ownership, and the clear leg makes that
fatal in the *green* direction. After a pin fix lands, the bridge takes the
graceful restart (`agent/agent_session_queue.py::_check_restart_flag`),
re-resolves healthy, and clears the marker — while the worker, whose restart
`_check_restart_flag` defers whenever jobs are running
(`tests/integration/test_remote_update.py::test_check_restart_flag_defers_when_jobs_running`),
still holds its memoized degraded flag and keeps raising
`LLMStackIncompatible`. The dashboard would render green against a still-broken
worker: the same "stuck dashboard equals no dashboard channel" failure with the
polarity flipped, and worse, because nobody investigates a green board.

So each resolver owns exactly one path and clears **only** its own
(`marker.unlink(missing_ok=True)` on the process's own filename, never a glob
and never another process's file).

**Only a process that can clear its marker may write one.** `_resolve_degraded_flag(proc: str | None = None)`
writes a marker **only** when `proc` is given: `bridge/telegram_bridge.py::main`
passes `"bridge"`, `worker/__main__.py` passes `"worker"`, and every other
caller — one-shot scripts, cron helpers, pytest processes that touch
`run_typed` — passes nothing and writes **no** marker, while still getting the
Sentry capture and the `logger.critical` sentinel, which are the channels
appropriate to a process that will not be around to clear anything. This
closes the round-7 operator finding: a pid-suffixed marker scheme has no clear
leg for a process that exits while degraded (the clear only ever runs on a
*healthy* resolution in the same process) and the read side has no liveness
filter, so on a genuinely degraded machine every one-shot deposits another
permanent red and the board stays red on the corpses after the fix lands —
the plan's own "stuck-red dashboard equals no dashboard channel" mode, reached
through the mechanism introduced to prevent its inverse. Two clearable writers
is the whole marker population.

The read side globs and is red while any marker survives. Tested: clearing the
bridge marker leaves the worker marker and the red state intact.

**The marker directory is a module-level seam, not a hardcoded path.**
`_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"` at `compat.py`
module scope, with every write and clear going through a `_marker_path(proc)`
helper that reads it. The production default is unchanged — un-redirectable by
cwd, exactly as the dashboard requires — but tests do
`monkeypatch.setattr(compat, "_MARKER_DIR", tmp_path)` instead of writing into
the live `data/` that a running bridge, worker, and dashboard share on the
development machine. Without the seam, this lane's degraded-driving test files
write real markers into the real `data/`, xdist workers race on the same
filenames, and an interrupted test leaves the operator's actual dashboard red
with no underlying fault.

**The redirect is a mechanism, not a per-file convention** (round-8 concern).
Requiring each new test file to remember `monkeypatch.setattr(compat, "_MARKER_DIR", tmp_path)`
plus its own live-`data/` glob-unchanged assertion is exactly the
"consumer discipline" shape this plan rejects for `agent/llm/` — and the gap was
already visible: `tests/unit/test_llm_stack_compat.py` drives degraded
resolutions but was not one of the files carrying the guard. So the redirect
moves into `tests/unit/conftest.py` as an **`autouse=True`, function-scoped
fixture**:

```python
@pytest.fixture(autouse=True)
def _redirect_llm_marker_dir(monkeypatch, tmp_path_factory):
    try:
        from agent.llm import compat as _compat
    except ImportError:
        return
    monkeypatch.setattr(
        _compat, "_MARKER_DIR", tmp_path_factory.mktemp("llm-marker"), raising=False
    )
```

**The guarded import is what makes this inert, not `raising=False`** (round-9
blocker). The string-target form `monkeypatch.setattr("agent.llm.compat._MARKER_DIR", ..., raising=False)`
does **not** degrade gracefully: `raising` suppresses only the *attribute*
existence check, while the string form first calls `derive_importpath`, whose
`resolve(module)` performs a real import and raises before `raising` is
consulted. Verified on `main` today —
`.venv/bin/python -c "from _pytest.monkeypatch import derive_importpath; derive_importpath('agent.llm.compat._MARKER_DIR', False)"`
raises `ImportError: import error in agent.llm.compat: No module named 'agent.llm.compat'`,
and an autouse fixture in that form ERRORs every test in the repo at setup for
as long as `compat.py` is absent (the task-2 commit, a `git bisect`, a revert of
task 3, or a builder who lands the conftest change before task 3). The string
form is also never inert *after* `compat.py` exists: it force-imports
`agent.llm.compat` for every test in the suite. The module-object form skips
`derive_importpath` entirely; `raising=False` is retained only for the narrow
window where `compat.py` exists but `_MARKER_DIR` has not been added yet.

**The fixture must not bill the whole suite for an isolation four files need**
(round-9 concern). Two reductions, both in the block above. (1) It takes
session-scoped `tmp_path_factory` and calls `mktemp` **only on the branch where
`compat` imported**, so a test that returns early materializes no directory —
`tmp_path` would have created one unconditionally. Measured by the critic: 300
parametrized no-op tests under the unconditional shape produced 600 directories
under the session tmp root; the repo has ~14,216 test functions, so a full run
manufactured on the order of 28,000 unread temp directories, retained three
sessions deep, on a memory-constrained machine several agents share. (2) It
lives in `tests/unit/conftest.py`, not the root `tests/conftest.py`: all four
marker-driving files (`test_llm_import_safety.py`,
`test_llm_stack_degraded_start.py`, `test_dashboard_llm_degraded.py`,
`test_llm_stack_compat.py`) are under `tests/unit/`, so the mechanism property
is preserved — no file re-implements the redirect — while `tests/integration/`
pays nothing. `tests/unit/conftest.py` already exists.

The per-file glob-unchanged instruction is **deleted** from task 4 and from the
Test Impact rows — it was the hand-replicated half. One case still asserts the
**default**, and it must not read the patched global: resolve
`Path(agent.llm.compat.__file__).resolve().parents[2] / "data"` directly and
compare it to `Path(ui.app.__file__).parent.parent / "data"`.

An in-process fixture cannot reach the subprocess CLI case in
`test_llm_stack_compat.py`, so that case passes the redirect explicitly to the
child via the environment variable **`LLM_STACK_MARKER_DIR`** — which requires
`_marker_path()` to read it **lazily inside the function** (a module-scope
`os.environ.get` is blocked by `validate_no_module_scope_env.py`). The override
is **not silent** (round-9 concern): when it is set and differs from
`_MARKER_DIR`'s default, `_marker_path()` logs once at `logger.warning` with the
same sentinel token the `LLM_STACK_COMPAT_OVERRIDE` OVERRIDDEN warning carries,
so one sentinel grep finds both break-glass paths. Without the announce, a stale
value inherited from a launchd plist, an exported shell var, or a cron env would
relocate the write path of this plan's only *standing* signal and leave the
board green on a degraded bridge with nothing saying why — the false-green
polarity of the plan's own "stuck dashboard equals no dashboard channel" mode.

A direct Telegram push was considered and **rejected**: no raw Bot-API send path
exists in tracked code, and Telethon's send lives only in the bridge — it cannot
alarm a degraded worker. If experience shows the three channels are missed, a
Telethon send in the bridge is a small follow-up.

### At the auto-bump boundary: fail-closed rollback (a separate axis)

If the auto-bump `llm` gate phase fails **or cannot run** (no key, no venv
python, subprocess timeout), the set is rolled back and a distinct warning is
emitted. This does not contradict degraded-start — "should a running service
refuse to start?" is answered *no*; "should an unattended script push a new pin
fleet-wide without having verified it?" is also answered *no*. Declining to bump
costs one stale cycle.

## Architectural Impact

- **New dependencies**: none.
- **New module**: `agent/llm/compat.py`. **Named deviation from round-2's
  accepted placement** (`utils/llm_stack_compat.py`): the predicate is a
  statement about `agent.llm`'s own stack, and repo doctrine
  (`feedback_single_authoritative_liveness`) says the module that owns a
  lifecycle exposes its health API. Placing it inside the package also
  eliminates the bidirectional `utils/` ⇄ `agent/` coupling round 2 flagged as
  B-f (there is no `utils/` → `agent/` import edge anywhere today, and this
  plan no longer creates the first one), and `python -m agent.llm.compat --json`
  serves the subprocess callers identically — which works precisely *because*
  the package `__init__` becomes import-safe. `verify.py` and `deps.py` never
  import it in-process (subprocess only), so no runtime→scripts or
  scripts→runtime edge is created in either direction.
- **The import-safety contract is the load-bearing change**: `agent/llm/`
  (wrapper, compat, and `agent/anthropic_client.py` as its dependency) holds no
  third-party LLM-stack imports at module scope. A memoized loader inside the
  call paths owns them. One architectural test enforces it; no consumer is
  touched and no consumer can regress it.
- **Interface changes**: `AUTO_BUMP_PACKAGES` (a `list[str]`) is replaced by
  `AUTO_BUMP_SETS` (a list of `CoupledSet`, carrying `members`, `import_names`,
  `gates`, `reason`, `hold`). `run_smoke_test` grows a `phases` argument and
  returns a phase marker. `AutoBumpResult` gains per-set bookkeeping and
  `restore_failed`. `LLMCallError` gains a subclass.
- **Behavior change at startup**: bridge and worker force flag resolution at
  boot — a sub-second local operation, no network.
- **Data ownership**: unchanged. `pyproject.toml` remains the single source of
  pin truth; `uv.lock` remains maintainer-authored, follower-consumed.
- **Reversibility**: high. Laziness and the predicate are additive; call sites
  are one-liners.

## Appetite

**Size:** Large

Re-labelled from Medium at round 5, per the round-4 nit. Nine tasks, four agent
roles, three new test files, ~15 new integration tests, a wholesale `wrapper.py`
module-scope rewrite, a `TimeoutSettings` migration, and three interface
replacements in `deps.py` is not Medium-shaped work, and calling it Medium only
buys a false sense of the review budget. The Large label is the honest one; the
scope itself is unchanged.

**Team:** Solo dev pair (runtime + update-scripts), test engineer, documentarian

**Interactions:**
- PM check-ins: 1 (only if alert-channel judgment is needed)
- Review rounds: 1

### Why this lane is not split into two PRs

Round 4 proposed cutting a PR boundary at task 4 — half (A) import-safety +
predicate + degraded posture, half (B) the `deps.py` coupled-set rewrite —
noting the halves are file-disjoint and that half (B), being held, changes no
production behavior. **Rejected**, for the record:

- **The halves are not independent, only file-disjoint.** Task 6's `llm` gate
  phase invokes `python -m agent.llm.compat --json`, and `verify.py`'s new
  `ToolCheck` invokes the same entry point. Half (B) has no meaning without
  half (A)'s artifact, so splitting serializes two full SDLC pipelines: (B)
  cannot start until (A) merges, and its Verification rows cannot even be
  written against a branch that lacks `compat.py`.
- **The Rabbit Holes argument does not transfer.** That argument is about
  co-landing a gate with *the dependency bump it gates* — where a failure is
  genuinely ambiguous between "the gate is wrong" and "the bump is bad". Here
  both halves are gate machinery with disjoint test files
  (`test_llm_*.py` vs `test_remote_update.py`), so a red test names its own
  half. This lane already forbids the actual hazard: the anti-criteria assert
  no pin moves.
- **The held set is the point, not a reason to defer it.** `hold` exists
  precisely so the coupled-set structure can land inert. Deferring it to a
  second lane leaves `AUTO_BUMP_PACKAGES` and the three spike-2 pin-helper
  defects on `main` for another cycle — the defects that produced the
  half-bump in the first place.

The appetite re-label above is the concession taken instead.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` resolvable | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | The auto-bump `llm` phase makes a real Haiku call |
| `uv` on PATH | `uv --version` | Coupled-set sync/rollback re-resolves the lockfile |
| Network reach to PyPI | `python -c "from scripts.update.deps import get_pypi_latest; assert get_pypi_latest('anthropic')"` | Latest-version discovery |
| Sentry configured | `python -c "from monitoring.sentry_config import *"` + a DSN present | One of the three alert channels |

## Solution

### Key Elements

- **Import-safety contract on `agent/llm/`.** All third-party stack imports
  (`anthropic`, `pydantic_ai.*`) move out of module scope in `wrapper.py` and
  `agent/anthropic_client.py` into one memoized loader (`_load_stack()`),
  invoked by `run_typed` / `run_typed_local` / the predicate. Module scope keeps
  stdlib and our own code only. Enforced by an architectural test: with
  `anthropic` and `pydantic_ai` stubbed to raise on import,
  `import agent.llm` **and** `import bridge.telegram_bridge` both succeed,
  `run_typed` raises `LLMStackIncompatible`, and all three alert channels fire.
  That single test is the invariant; consumer discipline is not.

  **The import half MUST run in a fresh interpreter subprocess** (round-7
  blocker). In-process it cannot fail: existing test files already import
  `bridge.telegram_bridge`, whose module scope pulls `bridge.routing` →
  `agent.llm`, so under xdist both `import` statements are `sys.modules` cache
  hits that execute no stack import at all — a test that passes unconditionally
  and is counted as coverage. A `sys.modules`-purge variant is explicitly
  rejected: `tests/unit/test_bridge_api_id_parse.py:112` shows the trap (it pops
  only `bridge.telegram_bridge`, leaving a cached `bridge.routing` and
  `agent.llm` to short-circuit the chain), and the correct purge set is the full
  transitive closure (`bridge.telegram_bridge`, `bridge.routing`,
  `bridge.job_router`, `bridge.agent_catchup`, `agent.llm`, `agent.llm.wrapper`,
  `agent.llm.compat`, `agent.anthropic_client`, `agent.memory_extraction`,
  `tools.email_cs.triage`) whose completeness nothing enforces. The shape:
  write a shim dir into `tmp_path` holding `anthropic.py` and
  `pydantic_ai/__init__.py` whose bodies are `raise ImportError("stubbed")`,
  then assert
  `subprocess.run([sys.executable, "-c", "import agent.llm, bridge.telegram_bridge"], cwd=<repo root>, env={**os.environ, "PYTHONPATH": f"{shim}:{repo}"}, capture_output=True).returncode == 0`,
  with the captured stderr in the assertion message. The `run_typed`-raises and
  alert-channel halves stay in-process; only the import half needs the
  subprocess.
- **`check_llm_stack_compat()` in `agent/llm/compat.py`**, returning
  `CompatResult` (`compatible`, `loader_ok`, `anthropic_version`,
  `pydantic_ai_version`, `reason`, `exc_type`). Local mode: run the loader
  (catches the ImportError class — this is the `loader_ok` axis), then verify
  the installed `anthropic` create signature accepts what the installed
  `pydantic_ai` **actually forwards** (the `compatible` axis).

  **The derivation is from the call site, not from the settings TypedDict.**
  Round 5 established, by execution, that
  `AnthropicModelSettings.__annotations__` is the wrong source: on the pinned,
  verified-working pair it has 31 keys of which **20 are absent** from any
  `create` signature (`anthropic_betas`, `anthropic_thinking`,
  `frequency_penalty`, `logit_bias`, `parallel_tool_calls`, `seed`, …). It is a
  declared *superset* of provider-agnostic and anthropic-namespaced settings
  that `AnthropicModel` translates, renames into headers, or drops — not the
  kwargs that reach the client. Deriving from it makes the predicate report
  `compatible=False` on a healthy fleet.

  The algorithm (prototyped end to end during this revision; it returns
  `compatible=True` on `anthropic 0.125.0` + `pydantic-ai-slim 2.9.0`):

  1. `ast.parse(inspect.getsource(pydantic_ai.models.anthropic))`; collect every
     `ast.Call` whose `func` is an `ast.Attribute` named `create` on a dotted
     path rooted at `self.client`. On 2.9.0 there is exactly one such site,
     `self.client.beta.messages.create`, carrying **24 literal keyword names**
     (`temperature`, `top_p`, `top_k`, `stop_sequences`, `betas`,
     `context_management`, `mcp_servers`, `speed`, `max_tokens`, `system`,
     `messages`, `model`, `tools`, `tool_choice`, `output_config`, `stream`,
     `cache_control`, `thinking`, `container`, `metadata`, `service_tier`,
     `timeout`, `extra_headers`, `extra_body`).
  2. **Exactly one site or fail closed.** `if len(sites) != 1`, return
     `CompatResult(compatible=False, reason=f"expected exactly 1 self.client.*.create call site in pydantic_ai.models.anthropic, found {len(sites)}: {[s.path for s in sites]}")`.
     Falling through to `sites[0]` is the silently-passing-predicate-whose-
     target-moved failure this plan rejects, and a beta/non-beta branch split is
     the most natural shape Step 2's 25-minor-version move takes.
  3. Resolve the **same** attribute path against a real client
     (`client = anthropic.AsyncAnthropic(api_key="x")`, constructor-only, no
     network, no key needed), i.e. walk `beta.messages` off the instance and
     take `inspect.signature(target.create)`.

     **Why `AsyncAnthropic` specifically:** the call site names only the
     attribute *path*, not the client class — `pydantic_ai.models.anthropic`
     types `self.client` as the `AsyncAnthropicClient` union, which also admits
     `AsyncAnthropicBedrock`, `AsyncAnthropicBedrockMantle`, and
     `AsyncAnthropicVertex`. `AsyncAnthropic` is the correct resolution target
     **because that is the class `agent/llm/wrapper.py` constructs**, and this
     repo uses none of the others. Mirror this clause in `compat.py`'s
     docstring beside the "read the target from the call site" note, so a future
     reader debugging a Bedrock-shaped false positive has it on the record.
  4. **A splat-only call site fails closed.** Collect the site's literal keyword
     names — `forwarded = [k.arg for k in site.keywords if k.arg]` — and, *before*
     the subset test in step 5:

     ```python
     if not forwarded:
         return CompatResult(
             compatible=False,
             loader_ok=True,
             reason=(
                 "self.client.*.create call site in pydantic_ai.models.anthropic "
                 f"forwards no literal keywords (splat-only: "
                 f"{sum(1 for k in site.keywords if k.arg is None)} ** entries) "
                 "— derivation cannot verify the signature"
             ),
             exc_type=None,
         )
     ```

     This is the round-8 blocker and it is **distinct from step 2's count gate**:
     on a site refactored to `self.client.beta.messages.create(**kwargs)` there
     is still exactly one site, the path still resolves, and `getsource` is still
     available — so none of the other four fail-closed cases fires, `forwarded`
     is empty, and step 5's "every forwarded name is a parameter" is **vacuously
     true**, returning `compatible=True` against the known-bad pair. Collapsing
     an argument list into a splat is a routine refactor at the distance Step 2's
     lane moves `pydantic-ai-slim` (25 minor versions). The
     `temperature`/`top_p`/`top_k` shape assertions do **not** compensate: they
     live in `tests/unit/test_llm_stack_compat.py` and never run at `/update`
     verify or at bridge/worker startup, which is exactly Data Flow route 3 —
     the follower machine this lane exists to protect. Without this gate the
     predicate defeats the lane.
  5. `compatible` iff every forwarded name is a parameter of that signature, or
     the signature has a `VAR_KEYWORD` param. Missing names go into `reason`
     verbatim.

  **Resolving the path is load-bearing, not incidental.** Round 5's prescription
  also named the wrong *class*: `anthropic.resources.messages.AsyncMessages`
  (non-beta) is missing `betas`, `context_management`, `mcp_servers`, and
  `speed`, so even the correct kwarg set would report four false positives
  against it. The call site names its own target; read the target from the call
  site.

  Introspection failures — five cases, all returning `compatible=False` with the
  failure verbatim: `getsource` unavailable, zero `create` sites found, **more
  than one `create` site found**, the attribute path unresolvable on the client,
  and **the single site forwarding no literal keywords (splat-only)**. A silently-passing predicate whose target moved is
  the same failure as no gate. Guarding that fail-closed direction against
  false positives is what the break-glass override below is for.

  Two guards replace round 5's trivially-passing subset test:
  - **Positive self-test**: `check_llm_stack_compat().compatible is True` on the
    pinned pair, run in CI. This is the assertion whose absence let the
    round-5 blocker through.
  - **Shape assertions on the derived set**: it contains `temperature`,
    `top_p`, and `top_k` (the three the 2026-08-24 incident removed) and
    contains **no** `anthropic_`-prefixed name (the tell that the derivation
    slipped back onto the settings TypedDict). The module-level literal tuple
    is kept only as those three names' home — it is the *assertion* target, not
    a fallback the predicate silently degrades to.

  `allow_network=True` additionally makes one minimal `run_typed` call (catches
  the transport class). `python -m agent.llm.compat --json` is the subprocess
  entry point; no imports from `scripts/`.
- **Two axes, not one boolean — `run_typed_local` is not gated on the Anthropic
  signature.** `CompatResult` carries `loader_ok` (the third-party stack
  imports) beside `compatible` (the Anthropic create-signature check), and
  `_resolve_degraded_flag()` memoizes both. `run_typed` raises
  `LLMStackIncompatible` when `not loader_ok or not compatible`;
  `run_typed_local` raises **only** when `not loader_ok`. Rationale: the local
  leg constructs `OpenAIChatModel` against `OllamaProvider` and talks to
  localhost Ollama — it never touches `anthropic`, so an Anthropic signature
  break must not fall the two hot-path classifiers (intake intent in
  `tools/classifier.py`, Job bind-or-mint in `bridge/job_router.py`) back to
  their conservative defaults fleet-wide. Spike-5 already separated these
  domains; a single boolean would re-collapse them. The alert still fires on
  either axis, naming which.

  **Accepted residual: `loader_ok` is stack-wide, so only the *signature* axis
  is separated** (round-9 concern). One memoized `_load_stack()` imports the
  entire third-party set — deliberately, with no per-symbol option menu — so an
  `anthropic` **ImportError** sets `loader_ok=False` and `run_typed_local`
  raises `LLMStackIncompatible` too. That is exactly today's behavior (an
  `anthropic` import failure at `wrapper.py` module scope already fells the
  whole module), so it is a **no-regression residual, not a new fault**; the
  split's actual delivery is that an Anthropic *signature* break — the failure
  mode this lane was opened for, and the one the pinned pair exhibits — leaves
  the local classifiers running. Splitting the loader into `_load_anthropic_stack()`
  and `_load_local_stack()` legs would make the ImportError axis independent
  too, and is the honest upgrade if experience shows it matters; it is **out of
  scope for this lane** (it adds a second memo, a conjunction rule on
  `CompatResult.loader_ok`, and a separate `_resolve_degraded_flag` memoization
  path to a plan already re-labelled Large). Recorded beside Risk 5. This is not
  hypothetical for the lane immediately downstream — anthropic 1.0.0 moves its
  HTTP layer to `httpx2` (Research), an ImportError-class hazard, and #3073
  moves that pin — so the behavior is **asserted rather than incidental**:
  `test_anthropic_import_error_local_path` stubs **only** `anthropic` to raise
  on import and asserts `run_typed_local` raises `LLMStackIncompatible`.
- **Break-glass override.** `_resolve_degraded_flag()` reads
  `os.environ.get("LLM_STACK_COMPAT_OVERRIDE")` **inside the function** (a
  module-scope read is blocked by `validate_no_module_scope_env.py`); the value
  `"healthy"` short-circuits to not-degraded after a
  `logger.warning(<sentinel> + " OVERRIDDEN")`.

  **The override branch must run the same marker clear the healthy branch runs**
  (round-8 concern), gated on `proc` exactly as the write is:

  ```python
  if os.environ.get("LLM_STACK_COMPAT_OVERRIDE") == "healthy":
      logger.warning(SENTINEL + " OVERRIDDEN")
      if proc:
          _marker_path(proc).unlink(missing_ok=True)
      return False
  ```

  Without the clear, the override strands a permanent red marker in precisely
  the scenario it exists for: a false positive degrades the fleet, the bridge
  writes `data/llm-stack-degraded.bridge`, the operator sets the override and
  restarts — and because the short-circuit happens *before* the predicate, no
  future resolution can ever reach the healthy branch that clears. The board
  stays red forever on a machine the operator has declared healthy, recoverable
  only by a human `rm`. `missing_ok=True` keeps the clear a no-op for a process
  that never wrote a marker; the `if proc:` guard preserves the rule that only
  `bridge` and `worker` touch marker paths.

  The predicate introspects two
  third-party internals upstream is free to rename — including in Step 2's
  lane, which moves `pydantic-ai-slim` 25 minor versions — and the fail-closed
  direction means a false positive raises at every non-harness call site on all
  four machines until a code revert completes a full SDLC round trip. The
  override makes recovery an operator action. It is **not** honoured by
  `check_llm_stack_compat()` (which stays pure), by the `--json` CLI, or by the
  auto-bump `llm` gate: an override must never let a bad pin pass the bump gate.
  Documented as break-glass only, with the sentinel warning making its use
  visible in logs.
- **Two entry points, no shared state — the predicate is pure, the resolver
  alerts.** `check_llm_stack_compat(allow_network=...)` returns a `CompatResult`
  and does **nothing else**: it never touches the memoized degraded flag, never
  calls `capture_message`, never writes or clears the marker file. Only
  `_resolve_degraded_flag()` (the in-process, lazily-memoized reader used by
  `run_typed` and the startup hooks) alerts, and only on the first transition to
  degraded. `python -m agent.llm.compat --json` calls **only the pure
  predicate**. This is load-bearing, not tidiness: the CLI's callers are
  `verify.py` and the auto-bump `llm` gate phase, and the gate deliberately runs
  the predicate against a stack it is *about to roll back*. If the CLI resolved
  the flag, every **successful** rollback would fire a `level="fatal"` Sentry
  capture and leave the standing `data/llm-stack-degraded` marker behind for a
  failure the gate just prevented — reaching this plan's own named "stuck-red
  dashboard equals no dashboard channel" mode through its happy path, and
  alarming production twice during task 7.

  **How purity is asserted (round-8 concern: the marker clause was
  unfalsifiable).** Since the round-7 rule is that `_resolve_degraded_flag`
  writes a marker **only** when a `proc` is passed, and the CLI passes none, "the
  CLI creates no marker" passes whether the CLI is pure or routes through the
  resolver — a test that cannot fail, counted as coverage. The criterion is
  therefore split in two, and the marker clause is dropped:

  1. **In-process, discriminating:** call the CLI's JSON entry function directly
     with `capture_message` monkeypatched to a counting stub on an incompatible
     stack; assert the count is `0` **and** that `compat`'s memoized flag global
     is still unresolved afterwards (`compat._DEGRADED is None`). The memo
     assertion is the one that separates pure from impure, and it is only
     available in-process.
  2. **Subprocess, contract-only:** keep the `python -m agent.llm.compat --json`
     run, but prove only what a parent can see — the child's stdout is
     well-formed JSON carrying `compatible`, `loader_ok`, and both versions, and
     its exit status matches. That is the out-of-process CLI contract, not a
     purity claim.

  "Creates no marker" is deleted as a purity assertion here and in task 7 leg
  (a); where it is retained it is restated honestly as a consequence of the
  writer rule (no caller passes `proc`), never as evidence of purity.
- **Degraded flag + alert in the resolver** (see Failure Posture). Startup hooks
  in `bridge/telegram_bridge.py::main` and `worker/__main__.py` force early
  resolution; neither may exit on incompatibility.
- **`LLMStackIncompatible(LLMCallError)`** raised by `run_typed` /
  `run_typed_local` under a degraded stack; subclassing preserves every
  existing `except LLMCallError` fail-safe unchanged (asserted, not assumed).
- **Coupled-set declaration** —
  `AUTO_BUMP_SETS = [CoupledSet(members=["anthropic", "pydantic-ai-slim"], import_names=("anthropic", "pydantic_ai"), gates=("llm", "import", "pytest"), reason=..., hold="#3001 Step 2"), CoupledSet(members=["claude-agent-sdk"], import_names=("claude_agent_sdk",), reason=...)]`.
  A set is the atomic unit of bump, sync, gate, and rollback. `gates` defaults
  to `("import", "pytest")` so a new set never silently inherits a billed API
  call. The `import` phase imports the set's own `import_names` — the hardcoded
  `import anthropic; import claude_agent_sdk` string is gone.
- **`hold`** — a held set is skipped by `auto_bump_deps`, recording
  `BumpResult(bumped=False, error=f"held: {set.hold}")` so `run.py`'s Step 3.5
  per-bump log (the block containing `"Smoke test passed after bump"`,
  `run.py:1514` at the baseline commit) stays legible. This is how `anthropic` returns to the auto-bump *structure*
  without the first post-merge cron tick auto-executing the Step 2 upgrade
  (`anthropic 1.0.0` + `pydantic-ai-slim 2.35.0` would pass the gate and push
  fleet-wide, unattended). Step 2's lane removes the hold as its final act.
  Deliberately **not** an upper version bound — `get_pypi_latest` returns only
  the latest version, and a bound needs the resolver Rabbit Holes forbid.
- **`openai` is in no set** (spike-5), asserted in code with the reasoning in
  the docstring. Its declaration keeps the existing `openai>=1.0.0` floor —
  the round-2 concern is accepted: an exact pin was scope creep that also
  masked its own regression test's target. Defect 1 is fixed by the
  declaration-aware reader regardless, tested against a fixture that preserves
  the floor-plus-comment shape.
- **Declaration-aware pin helpers** — locate the actual dependency declaration
  (comment-blind), tolerate extras (`pydantic-ai-slim[anthropic]`), refuse
  loudly rather than silently no-op.
- **Atomic per-set rollback** with the restore's own sync result captured
  (`restore_failed`) and the `run.py` commit branch guarded by it.

### Flow

**Run boundary (every `/update`, every bridge start, every worker start):**

```
startup → force degraded-flag resolution
  resolver (first read, any entry path):
    → check_llm_stack_compat()  [local: loader + signature introspection]
        → compatible?   → memoize healthy; clear data/llm-stack-degraded
        → incompatible? → memoize degraded
                          → ALERT from resolver: sentry fatal (hibernation-exempt)
                                                + logger.critical sentinel
                                                + write data/llm-stack-degraded
                          → run_typed raises LLMStackIncompatible from here on
                          → PROCESS CONTINUES (Telegram intake keeps queueing)
```

**Auto-bump (`/update --cron`, maintainer machine only):**

```
for each coupled set:
  → held?                                   → skip, record "held: <reason>"
  → resolve latest for EVERY member         → any unresolvable? skip whole set
  → any pin actually changing? no → skip set (no sync on quiet cycles)
  → rewrite ALL member pins (set snapshot taken first; any rewrite failure
    restores snapshot and abandons the set immediately)
  → uv sync --all-extras (unfrozen)         → fail? restore snapshot, re-sync, next set
  → for each phase in set.gates:
       llm    → {venv}/bin/python -m agent.llm.compat --json --allow-network
       import → import each of set.import_names (in the target venv)
       pytest → tests/unit/test_docs_auditor_substrate.py
  → any phase fails? restore snapshot, re-sync, record rolled_back + phase
  → restore itself failed? record restore_failed, git checkout -- uv.lock
any set survived AND not restore_failed? commit + push pyproject.toml + uv.lock
```

### Technical Approach

- **Boot pays nothing.** The resolver's local check is loader + introspection —
  sub-second, no network, no tokens. Only the auto-bump `llm` phase makes a real
  call (transport-class breaks), and only on cycles where something bumped.
- **From auto-bump, the predicate runs in the target venv, not in-process**
  (`{project_dir}/.venv/bin/python -m agent.llm.compat --json`), following
  `_markitdown_importable`. In-process would exercise the pre-sync imports the
  update process already holds. Bounded by a subprocess timeout.
- **One argv construction, shared by the phase runner and the test.** The
  command `[str(venv_python), "-m", "agent.llm.compat", "--json", "--allow-network"]`
  is built by a single helper in `deps.py` (e.g. `llm_gate_argv(venv_python)`),
  called by the `llm` phase runner and by task 7's leg (a) alike, with
  `test_llm_phase_argv_matches_gate_helper` asserting the phase invokes exactly
  what the helper returns. Without this, the only executions of the `llm` phase
  this lane ever performs are hand-written invocations that could drift from the
  production one and still pass — see the Success Criteria note on why the phase
  is unreachable in production for this lane's whole life.
- **`verify.py` uses the same subprocess shape — and the result must be
  routed through `ToolCheck.error`, not `ToolCheck.detail`.** `check_python_import`
  (`verify.py:104`) and `check_venv_tool` (`verify.py:125`) already build
  `{venv}/bin/python` and shell out via `run_cmd`. Run the `--json` entry with
  `check=False` and return:

  ```python
  ToolCheck(
      name="llm-stack-compat",
      available=res["compatible"],
      version=f"anthropic {res['anthropic_version']} / pydantic-ai {res['pydantic_ai_version']}",
      error=None if res["compatible"] else res["reason"],
      detail=<both resolved versions>,
  )
  ```

  appended to `result.valor_tools` alongside its siblings (`verify.py:1225-1228`).
  **`error` must be non-empty on failure or the leg is silent.** `run.py`'s
  generic `valor_tools` loop (`run.py:2673-2684`) is literally
  `if not tool.available and tool.error:` — with `error` unset, an incompatible
  stack produces no log line, no `result.warnings` entry, and nothing for
  `bridge/update.py::extract_update_warnings` to surface, which would ship a
  dead check at the one call site covering Data Flow routes 2 and 3.
  Do **not** rely on `detail` to carry the failure: `ToolCheck.detail` is not
  read by that loop at all. Print-on-pass (#2541) is therefore a *second*
  deliverable, not a property of `detail`.

  **Print-on-pass is a lookup by `name` in `result.verification.valor_tools`,
  logged unconditionally — not a new `UpdateResult` field and not a second
  `verify` call site** (round-7 consistency finding). The `projects_json_check`
  block at `run.py:1907-1909` is the wrong structural model to copy literally:
  it reads a dedicated `UpdateResult` dataclass field (`run.py:152`) that
  `run.py` itself populates at `run.py:1904`. The compat `ToolCheck` is created
  inside `verify.verify_environment` and appended to `result.valor_tools`
  (`verify.py:1225-1228`); `run.py` only ever sees it as an element of
  `result.verification.valor_tools`, obtained once at `run.py:2636`. A builder
  copying the cited block literally either adds an `UpdateResult` field
  `verify.py` never sets, or adds a second `verify` call and runs the compat
  subprocess twice per update. The correct shape, immediately after
  `result.verification = verify.verify_environment(...)` (`run.py:2636`, inside
  `if config.do_verify:` — true in `UpdateConfig.full`, `.cron`, and
  `.verify_only`, so the leg is genuinely unconditional) and before the existing
  `valor_tools` warning loop at `run.py:2673`:

  ```python
  compat = next((t for t in result.verification.valor_tools if t.name == "llm-stack-compat"), None)
  if compat is not None:
      log(f"  llm-stack-compat: {compat.detail}", v, always=True)
  ```

  Same *style* as the `projects_json_check` detail block (report every run, pass
  or fail, so a silently stale venv is visible); different plumbing.
  `llm-stack-compat` is **not** added to `human_gated_tools`: it is
  agent-resolvable, so it should re-warn every run until fixed.
- **Import-cycle discipline inside the package**: `compat.py` reaches the stack
  only through the loader, inside function bodies. `wrapper.py` may import
  `compat`'s flag accessor at module scope (both are our own import-safe code).
  The direction matters: the predicate must be able to *report* an ImportError,
  so nothing on `compat.py`'s module-scope path may raise one.
- **Test seam preserved deliberately** (round-2 B-b): moving imports into the
  loader removes `wrapper_mod.OpenAIChatModel` as a module attribute, which is
  the monkeypatch seam `tests/unit/test_llm_wrapper_local.py:52` documents as
  its network isolation. The loader returns a namespace object that tests patch
  instead (`monkeypatch.setattr(wrapper_mod, "_load_stack", fake_loader)`); the
  module docstring's isolation claim is updated, and a test asserts no real
  network call is reachable from that file.
- **The `wrapper.py` rewrite trips a commit-time hook unless the env read moves
  with it** — `validate_no_module_scope_env.py` is diff-scoped, so relocating
  `LOCAL_TYPED_HARD_TIMEOUT` counts as introducing it. Migrating the read to
  `TimeoutSettings` is a task-2 deliverable, not an incidental cleanup, and it
  is a **public-surface change, not an internal rename**: the constant is
  re-exported from `agent/llm/__init__.py` (lines 14 and 26, `__all__`
  included) and documented by name in `docs/features/nonharness-llm-wrapper.md:56`
  as "env-overridable, default 20s". All three move together — new field
  `settings.timeouts.local_typed_hard_s` (env `TIMEOUTS__LOCAL_TYPED_HARD_S`)
  read inside `run_typed_local`, re-export deleted, doc corrected. **The reader
  set is already resolved** (round-9 nit): `git grep -n LOCAL_TYPED_HARD_TIMEOUT`
  at the current tip returns exactly five code hits, all inside `agent/llm/` —
  `__init__.py:14` and `:26` (the re-export and its `__all__` entry),
  `wrapper.py:167` (the module-scope read being migrated), `:192` (a docstring
  reference in `run_typed_local`, reworded to the new field name), and `:209`
  (the sole functional reader) — plus the one prose hit at
  `docs/features/nonharness-llm-wrapper.md:56`, already a task-8 item. **Zero**
  readers exist outside the package. Re-run the grep to confirm nothing arrived;
  a non-empty result outside `agent/llm/` is a premise change that stops the
  task.
- **Set semantics are all-or-nothing at every stage** — resolve, rewrite, sync,
  gate, rollback. Today's record-error-and-continue is exactly how the incident
  happened (spike-2 defect 3).
- **Capture the restore sync's result** → `restore_failed = True` +
  `git checkout -- uv.lock`; guard `run.py` Step 3.5's commit branch with
  `not bump.restore_failed` — otherwise a later successful bump pushes a
  poisoned lockfile fleet-wide. The commit/push/restart ordering documented in
  `sdlc-1091.md` is otherwise untouched.
- **Distinguish gate phases in the result** (`llm` / `import` / `pytest`),
  surfaced in the `/update` warning detail, so "the LLM pair is incompatible"
  is legible against "an unrelated unit test is flaky".
- **`verify_critical_versions` (`deps.py:277`) is the helper rewrite's silent
  blast radius** — it calls `get_pinned_version` for
  `["telethon", "anthropic", "claude-agent-sdk"]`. Assert it returns the same
  three `VersionInfo` results across the rewrite.
- **Keep the `pyproject.toml` comment constraint, add the pointer.** Replace the
  four-line stopgap block above the `anthropic` pin with two lines:
  `# CRITICAL — coupled set: anthropic + pydantic-ai-slim move together.` /
  `# anthropic>=1.0.0 requires pydantic-ai-slim>=2.33.0. See AUTO_BUMP_SETS in scripts/update/deps.py.`

## Failure Path Test Strategy

### Exception Handling Coverage
- `check_llm_stack_compat` must **not** contain a bare `except Exception: pass`.
  Every failure path returns `CompatResult(compatible=False, ...)` with the
  exception type and message verbatim — including introspection failures and
  loader ImportErrors. Tested per path.
- The **alert** path is exception-tolerant the other way: a Sentry capture
  failure must not suppress the sentinel log or the marker file, and must not
  crash the resolver. Follow `agent/index_drift.py:229`'s fallback-log shape.
  Tested by making `capture_message` raise.
- The marker-file write and clear are wrapped in fail-quiet `OSError` handling
  on the dashboard's read side only; the resolver's write failure logs, never
  raises.
- No handler introduced by this work may swallow a rollback failure — a failed
  restore surfaces as `restore_failed` plus a warning, never silence.

### Empty/Invalid Input Handling
- `get_pinned_version` returning `None` (member absent) → set skipped, tested.
- `get_pypi_latest` returning `None` (network down) → set skipped, tested.
- A `pyproject.toml` with no dependency block → helpers refuse, no rewrite, no crash.
- The predicate on a venv where the loader raises → `compatible=False` with the
  `ImportError` text, not an unhandled raise (this **is** the spike-5 class).

### Error State Rendering
- Assert all three channels fire from one degraded resolution, that the body is
  the static string plus versions, and that no LLM call occurred during
  emission.
- Assert a healthy resolution clears `data/llm-stack-degraded` (the stuck-red
  leg).
- Assert a rolled-back set's warning names the failed phase.
- Assert the success path still logs `"Smoke test passed after bump"` so
  `extract_update_warnings` parsing is undisturbed.

## Test Impact

- [ ] `tests/unit/test_llm_wrapper_local.py` — UPDATE (round-2 B-b): the
  `wrapper_mod.OpenAIChatModel` monkeypatch seam at `:52` is replaced by the
  loader seam; update the module docstring's isolation claim; add an assertion
  that no real network call is reachable from the file.
- [ ] `tests/unit/test_llm_wrapper.py` — UPDATE: add `LLMStackIncompatible`
  raised under the degraded flag, and the `issubclass(..., LLMCallError)`
  assertion.
- [ ] `tests/unit/test_llm_import_safety.py` — ADD (new file): the contract
  test — with `anthropic`/`pydantic_ai` stubbed to raise on import,
  `import agent.llm` and `import bridge.telegram_bridge` succeed, `run_typed`
  raises `LLMStackIncompatible`, all three channels fire; plus the
  no-startup-hook path (first `run_typed` call resolves, alerts, raises typed).
  **The import half runs in a fresh interpreter subprocess with a
  raise-on-import shim dir on `PYTHONPATH`** — in-process (and under any
  `sys.modules` purge) it is a cache hit that passes unconditionally, which is
  the round-7 blocker. The alert/typed halves stay in-process. The marker
  redirect is supplied by the autouse `tests/unit/conftest.py` fixture (see Failure
  Posture) — no per-file monkeypatch and no per-file glob assertion.
- [ ] `tests/unit/test_llm_stack_compat.py` — ADD (new file): **the positive
  self-test — `check_llm_stack_compat().compatible is True` on the pinned pair**
  (the assertion whose absence produced the round-5 blocker); the derived kwarg
  set contains `temperature`/`top_p`/`top_k` and no `anthropic_`-prefixed name;
  the resolved target is the callable named by the call site's own attribute
  path; incompatible with verbatim reason on a simulated bad signature;
  `loader_ok is False` on loader ImportError; introspection-failure (zero
  `create` sites, **two `create` sites** fed from a synthetic module source,
  unresolvable path) → incompatible; **`test_splat_only_call_site_fails_closed`**
  — a synthetic module source whose sole `create` call is
  `self.client.beta.messages.create(**kwargs)` yields `compatible is False` with
  the splat reason (round-8 blocker; same synthetic-source mechanism the two-site
  case uses); local mode makes no network call; **the
  predicate is pure** — `check_llm_stack_compat` on an incompatible stack emits
  zero `capture_message` calls and leaves the memoized degraded flag unresolved;
  **the CLI is pure, asserted in-process** — call the `--json` entry function
  directly with a counting `capture_message` stub, assert zero calls **and**
  `compat._DEGRADED is None` afterwards; **the CLI contract out-of-process** —
  the subprocess run's stdout is well-formed JSON carrying `compatible`,
  `loader_ok`, and both versions, with a matching exit status (no marker
  assertion: it cannot fail, per the round-8 concern). The subprocess case
  receives the marker-directory redirect explicitly through the child's
  `LLM_STACK_MARKER_DIR`, since the autouse in-process fixture cannot reach it.
- [ ] `tests/unit/test_llm_stack_degraded_start.py` — ADD (new file): alert
  fires **while `run_typed` raises** (independence proof); raising
  `capture_message` suppresses nothing else; per-process marker written on
  degraded and cleared on healthy, with **clearing the bridge marker leaving the
  worker marker and the red state intact**; **a resolver called with no `proc`
  writes no marker at all while still emitting Sentry + the sentinel log**;
  hibernation `before_send` passes the
  sentinel event while dropping others; process does not exit; degraded intake
  (inbound message still enqueues an AgentSession); **two-axis split** —
  `loader_ok` true + `compatible` false makes `run_typed` raise while
  `run_typed_local` completes against a stubbed `FunctionModel`;
  `test_anthropic_import_error_local_path` — with **only** `anthropic` stubbed
  to raise on import, assert `run_typed_local` raises `LLMStackIncompatible`
  explicitly (the stack-wide `loader_ok` residual, below — asserted, not left to
  fall out of a shared boolean); `test_marker_dir_override_warns` — with
  `LLM_STACK_MARKER_DIR` set to a non-default value, `_marker_path()` emits one
  sentinel `logger.warning`; `test_marker_redirect_is_autouse` — import
  `agent.llm.compat`, resolve degraded with `proc="bridge"` carrying **no**
  per-file monkeypatch, assert the marker landed under the fixture's tmp dir and
  **not** under `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"`;
  **override** —
  `LLM_STACK_COMPAT_OVERRIDE=healthy` short-circuits the resolver, emits the
  OVERRIDDEN warning, and is ignored by the pure predicate and the `--json` CLI;
  **override clears the marker** (round-8 concern) — resolve degraded with
  `proc="bridge"` so the marker is written, reset the memo, set
  `LLM_STACK_COMPAT_OVERRIDE=healthy`, resolve again with `proc="bridge"`, and
  assert the marker is gone and the OVERRIDDEN warning was emitted.
  Marker redirection comes from the autouse `tests/unit/conftest.py` fixture, so no
  case monkeypatches `_MARKER_DIR` by hand and none carries a glob-unchanged
  assertion. One case still asserts the *default* — resolving
  `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"` directly (never
  the patched global) and comparing it to `Path(ui.app.__file__).parent.parent / "data"`.
- [ ] `tests/unit/test_dashboard_llm_degraded.py` — ADD (new file): the
  dashboard read side. With a marker written (versions + `exc_type`),
  `dashboard_json()` returns a truthy degraded field carrying both versions and
  naming the process; with no marker it returns the healthy value; with two
  markers it names both; with a marker present but unreadable the call still
  returns 200 and does not raise, proving the `except OSError` leg. Without
  this, the lane can go fully green with `dashboard_json` never wired. Markers
  are written under the directory the autouse `tests/unit/conftest.py` fixture
  redirects to, never the live `data/`.
- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_no_bump_when_already_latest`
  — UPDATE: rewrite against `AUTO_BUMP_SETS`; keep the nothing-bumps assertion.
- [ ] `tests/integration/test_remote_update.py::test_verify_runs_compat_check_without_bump`
  — ADD, and it must assert the leg is **loud**, not merely present: the
  returned `ToolCheck` on an incompatible stack has a non-empty `.error`, the
  check appears in `result.valor_tools`, and the emitted status lines produce a
  matching entry from `bridge/update.py::extract_update_warnings`. A version
  asserting only `available is False` would pass against the silent-check
  implementation the round-4 blocker identified.
- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_rollback_on_smoke_failure`
  — UPDATE: new phase-carrying return shape; assert the **set's** snapshot is
  restored (all members).
- [ ] `tests/integration/test_remote_update.py::TestGetPinnedVersion::test_reads_pinned_version`
  — UPDATE: extend with the extras form and the comment-collision case, against
  a fixture preserving the **floor-plus-comment** shape (`"openai>=1.0.0"` with
  `openai` inside the `pydantic-ai-slim` comment) — not the repo's live file.
- [ ] `tests/integration/test_remote_update.py` — ADD:
  `test_partial_resolve_skips_whole_set`, `test_extras_pin_is_bumped`,
  `test_openai_pin_not_read_from_comment`, `test_openai_is_not_in_any_coupled_set`,
  `test_default_gates_exclude_llm_phase`, `test_held_set_is_skipped_and_legible`,
  `test_llm_phase_argv_matches_gate_helper`,
  `test_llm_gate_failure_rolls_back_set`, `test_unrelated_set_survives_failed_set`,
  `test_gate_unavailable_is_fail_closed`, `test_restore_failure_blocks_commit`,
  `test_worktree_clean_after_every_rollback_path`,
  `test_verify_critical_versions_unchanged_by_helper_rewrite`,
  `test_verify_runs_compat_check_without_bump`.
- [ ] Any reader of `LOCAL_TYPED_HARD_TIMEOUT` — UPDATE: the constant becomes
  `settings.timeouts.local_typed_hard_s` and its `agent/llm/__init__.py`
  re-export (lines 14, 26, `__all__`) is deleted (see task 2 and the Freshness
  Check's module-scope-env ratchet subsection). **Resolved, not open**
  (round-9 nit): the five code hits are `agent/llm/__init__.py:14`/`:26` and
  `agent/llm/wrapper.py:167`/`:192`/`:209`, with zero readers outside
  `agent/llm/`; `docs/features/nonharness-llm-wrapper.md:56` is the one prose
  hit and is a task-8 item. Re-run `git grep -n LOCAL_TYPED_HARD_TIMEOUT` to
  confirm and stop the task if anything new appears.
- [ ] `tests/unit/test_settings.py` — UPDATE: cover the new `local_typed_hard_s` field and its
  `TIMEOUTS__LOCAL_TYPED_HARD_S` override, matching how its siblings are tested.
- [ ] `tests/unit/conftest.py` — UPDATE: add the `autouse=True`, function-scoped
  `_redirect_llm_marker_dir` fixture in the **guarded module-object** form given
  in Failure Posture — `try: from agent.llm import compat as _compat / except
  ImportError: return`, then
  `monkeypatch.setattr(_compat, "_MARKER_DIR", tmp_path_factory.mktemp("llm-marker"), raising=False)`.
  This replaces the per-file redirect convention across all four
  degraded-driving test files (round-8 concern). **Do not use the string-target
  form**: `raising=False` does not make it inert — `derive_importpath` imports
  the module first and raises, erroring every test in the repo while `compat.py`
  is absent (round-9 blocker). The guarded import is what delivers inertness.
  `tmp_path_factory` (not `tmp_path`) keeps the early-return branch from
  materializing a temp directory for every test in the suite, and
  `tests/unit/` (not root `tests/`) scopes the cost to where the four
  marker-driving files live.
- [ ] No changes to `tests/unit/test_docs_auditor_substrate.py` — it stays the
  gate's pytest phase.

## Rabbit Holes

- **Do not build a general dependency-compatibility solver.** Two packages, one
  declared set, one check.
- **Do not rewrite `pyproject.toml` parsing onto `tomlkit`/`tomllib`.** The
  writer must preserve the `CRITICAL` comments verbatim, and round-tripping
  comments swallows a day. Declaration-aware regex, move on.
- **Do not make the boot-time check do network I/O.** Signature check locally;
  real call only in auto-bump.
- **Do not make the gate run the full test suite.** ~20 minutes inside
  `/update --cron` is an outage, not a gate.
- **Do not build a general degraded-mode framework.** One flag, one typed
  exception, one alert. The second subsystem that wants degraded start can
  justify the abstraction.
- **Do not implement `hold` as a version upper bound.** It needs a resolver;
  a skip-with-reason is one `if`.
- **Do not attempt the upgrade "while we're in here."** That is Step 2, behind
  this gate, in its own lane. A gate landing in the same PR as the bump it
  gates gives you no way to tell which half is at fault.
- **Do not route the alert through the drafter.** See Failure Posture.

## Risks

### Risk 1: The compat check is wrong and degrades a healthy fleet
**Impact:** A false negative puts every bridge and worker into degraded mode
simultaneously.
**Mitigation:** Degraded is not down — intake and queueing continue, the alert
makes it immediately visible, and it is one revert away. The introspection
derivation makes the check track the installed pair rather than the 2026-08-24
incident specifically; the positive case runs against the real pinned pair in
CI so a false negative fails the suite before shipping — round 5 caught exactly
this failure in the prescribed derivation, and the positive self-test is the
guard that would have caught it automatically. Recovery is no longer a code
revert: `LLM_STACK_COMPAT_OVERRIDE=healthy` is an operator action on the
affected machine (see the break-glass bullet in Solution).

### Risk 2: The auto-bump gate makes `/update` depend on Anthropic being up
**Impact:** A provider outage turns maintainer auto-bump cycles into rollbacks.
**Mitigation:** Accepted, scoped to one set (gates default excludes `llm`), and
while the LLM set is held the exposure is zero. A skipped cycle costs
staleness; the distinct fail-closed warning keeps a persistent outage legible.
Boot-time checks are unaffected — no network.

### Risk 3: A genuine provider-side error is misread as an incompatible pin
**Impact:** A transient 400/529 rolls back a good bump.
**Mitigation:** `CompatResult.exc_type` distinguishes `TypeError` (binding) from
`APIStatusError` (provider) at a glance. No retry logic in this lane — rolling
back on a transient is the safe direction. Applies only to the auto-bump `llm`
phase; the local check cannot see a provider error.

### Risk 4: The alert is emitted and still missed
**Impact:** The failure the plan exists to prevent.
**Mitigation:** Three independent transports, one of them (the dashboard marker)
a *standing* signal rather than a one-shot; plus the typed exception makes the
symptom legible at every failing call site. Accepted residual: none of these is
a phone-buzzing page — named as a follow-up candidate, not assumed away.

### Risk 5: Laziness moves our own import-time bugs to first call
**Impact:** A typo-grade regression in wrapper code surfaces at first call
instead of at boot.
**Mitigation:** Every bridge/worker boot force-resolves the flag within seconds
of start, running the full loader — so detection latency stays boot-time in
practice. Only a process that never runs a startup hook learns at first call,
and the resolver-bound alert covers that path too.

### Risk 6 (accepted residual): `loader_ok` is stack-wide, so the two-axis split protects only the signature axis
**Impact:** An `anthropic` **ImportError** — a live hazard, since anthropic
1.0.0 moves its HTTP layer to `httpx2` (Research) and #3073 moves that pin —
sets `loader_ok=False` from the single memoized `_load_stack()`, so
`run_typed_local` raises `LLMStackIncompatible` and both hot-path classifiers
(`tools/classifier.py`, `bridge/job_router.py`) fall back to their conservative
defaults fleet-wide, even though the local leg never touches `anthropic`.
**Why accepted:** this is not a regression. Today `anthropic` is imported at
`agent/llm/wrapper.py` module scope, so an ImportError already fells the whole
module including `run_typed_local`; the lane leaves that behavior exactly where
it found it while fixing the *signature* axis, which is the failure the pinned
pair actually exhibits. Splitting `_load_stack()` into `_load_anthropic_stack()`
and `_load_local_stack()` legs is the correct upgrade and is deliberately
deferred — it adds a second memo, a conjunction rule on `CompatResult.loader_ok`,
and a separate `_resolve_degraded_flag` memoization path to a plan already
re-labelled Large.
**Mitigation:** the behavior is pinned by an explicit test rather than left
implicit — `test_anthropic_import_error_local_path` stubs only `anthropic` to
raise on import and asserts `run_typed_local` raises. If #3073 makes the
fleet-wide classifier fallback bite in practice, the loader split is a small,
well-scoped follow-up against a contract the test already states.

## Race Conditions

### Race 1: Concurrent `/update` runs on the maintainer machine
**Location:** `run.py`'s Step 3.5 block (the `auto_bump_deps` call and its
result handling, `run.py:1491-1534` at `b87fb26de`), `deps.py::auto_bump_deps`
**Trigger:** Overlapping runs snapshot/edit `pyproject.toml`; one's rollback
resurrects a pin the other moved.
**Mitigation:** Pre-existing and unchanged — per-set snapshots narrow the window
but do not close it; `/update` runs are already serialized by the update lock in
practice. Recorded so a reviewer does not mistake it for new.

### Race 2: The gate observes a half-synced venv
**Location:** `deps.py::sync_with_uv` → gate subprocess
**Mitigation:** Structurally satisfied — `sync_with_uv` runs synchronously via
`run_cmd` and the gate runs after it returns success. `CompatResult` carries
both resolved versions, so a stale-venv read is visible, not silent.

### Race 3: A `run_typed` call races the boot-time check
**Status:** **Designed away**, not mitigated. The flag is lazily self-resolving
and the alert is bound to resolution (Failure Posture), so ordering between
startup and first call is irrelevant by construction. The contract test covers
the no-startup-hook path explicitly.

### Race 4: Two processes race on the dashboard marker
**Location:** `agent/llm/compat.py::_resolve_degraded_flag` write/clear,
`ui/app.py::dashboard_json` read
**Trigger:** Bridge and worker resolve the flag at different times (the worker's
graceful restart defers while jobs run), so one process's healthy clear would
erase another's live degraded state.
**Status:** **Designed away**, not mitigated — per-process marker filenames give
each writer sole ownership of its own path, and the reader is red while any
marker exists. See "Why the dashboard marker is per-process" in Failure Posture.
The residual is the opposite and benign direction: a process killed while
degraded leaves a stale red marker until it next resolves healthy, which is the
safe way to be wrong.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3073] **The dependency upgrade itself** — `pydantic-ai-slim`
  → 2.33.0+, `anthropic` → 1.x, the `openai` 2.x → 3.x decision, the
  `CRITICAL — pin exact` staleness audit, and exercising the modules that import
  `openai` directly. Step 2, behind this gate, in its own lane; #3073 is
  **blocked on this lane merging**, and removing the LLM set's `hold` is its
  final act.
- [SEPARATE-SLUG #3074] **Work Item 2 — the `worker_key` regression guard.** The
  headline fix already landed in `56e80d843` (2026-08-31, `Refs #2852`);
  `_make_session` sets `stage_states` through the setter and carries a fixture
  self-check. #3074 owns the residue: the `save` stub masking a `TypeError` on
  stage-setting construction, the #2949 kwarg-drop audit, and the
  reject-unknown-kwargs decision. File-disjoint from this lane, and this lane's
  anti-criterion (`models/agent_session.py` and `tests/unit/test_agent_session.py`
  untouched) still holds.
- [SEPARATE-SLUG #3075] **Work Item 3 — duplicate/noisy nightly triage filing.**
- [SEPARATE-SLUG #3016] **The `test_promise_gate_real_api` failure.**
- [SEPARATE-SLUG #3001] **Auditing remaining `CRITICAL — pin exact` deps for
  staleness** — belongs with Step 2, where findings can be acted on.
- **An exact `openai` pin.** Dropped at round-2's recommendation: scope creep, a
  resolution-semantics change deferred to Step 2, and it masked its own
  regression test's target. The floor stays.
- **A paging alert channel.** Risk 4 names this as accepted residual.
- **Extending the compat predicate to other subsystems.**
- **A commit-time or push-time leg for route 2 — declined, with the residual
  named.** Round 5 asked why the plan applies the diff-scoped-AST-hook lesson to
  the import-safety *contract* but not to the *pin*, whose route (hand-staged
  commit) has two demonstrated occurrences. The proposed form was a
  `.githooks/pre-push` leg running `python -m agent.llm.compat --json` when the
  pushed diff touches an `anthropic` / `pydantic-ai-slim` pin. **It would not
  have caught either occurrence.** The predicate reports on the *installed*
  stack, and at push time the venv reflects the last `uv sync`, not the pin
  being pushed — `d0c02bde5` swept in an already-staged bump the author had not
  synced, so the hook would have introspected the good, still-installed pair and
  passed, adding a false all-clear on top of the bad pin. Making it sound
  requires syncing at push time: minutes of wall clock and a mutation of the
  pusher's venv on every push, well outside this appetite.
  **Accepted residual:** route 2 stays detected at the *next* run boundary —
  the next `/update` verify (every machine, unconditional) or the next bridge or
  worker start — which is after `origin/main` carries the bad pin. Followers
  cannot boot on it silently, which is the property this lane buys. A sound
  commit-time leg would be a **declaration-level** check (compare the pinned
  versions in the diff against the `anthropic>=1.0.0 → pydantic-ai-slim>=2.33.0`
  boundary, needing no venv at all); that is a named follow-up candidate, not
  this lane's work, and it needs the version-boundary table Rabbit Holes
  currently forbid.

## Update System

This work **is** an update-system change.

- `agent/llm/compat.py` — **new**, the predicate + degraded flag + alert; shared
  by runtime services (in-process) and update scripts (subprocess only).
- `scripts/update/verify.py` — **new call site**: subprocess `--json` run mapped
  to a `ToolCheck` with the failure reason in `.error` (see Technical Approach —
  `detail` alone yields a silent check), appended to `result.valor_tools`,
  unconditional on every `/update` and `/update --cron`.
- `scripts/update/deps.py` — the substantive change: `CoupledSet` (+`hold`),
  declaration-aware pin helpers, per-set gate phases (set-derived imports),
  per-set rollback with `restore_failed`, the `llm` phase subprocess.
- `scripts/update/run.py` — surface the failed phase in the rolled-back
  warning; guard the commit branch with `not bump.restore_failed`; add the
  print-on-pass block for the compat check's `detail` as a lookup by `name` in
  `result.verification.valor_tools` (no new `UpdateResult` field, no second
  `verify` call). The
  commit/push/restart ordering per `sdlc-1091.md` is untouched.
- `.claude/skills/update/SKILL.md`, section `### Auto-Bump Critical Dependencies`
  (cited by heading, not line range — the previously cited `66-72` spilled into
  `### Critical Dependency Handling`) — the auto-bump description
  ("anthropic and claude-agent-sdk... import check + pytest") becomes wrong on
  both counts; correct it and note the new verify-time check.
- `ui/app.py` — globs `data/llm-stack-degraded*` into `/dashboard.json`, red
  while any marker exists, with its own read-side test.
- `config/settings.py` — **one new `TimeoutSettings` field**,
  `local_typed_hard_s: float = Field(default=20.0, ge=1.0, le=300.0)`, env key
  `TIMEOUTS__LOCAL_TYPED_HARD_S`, read **inside** `run_typed_local` as
  `settings.timeouts.local_typed_hard_s` (a module-scope read would defeat the
  migration). It **retires** the documented `LOCAL_TYPED_HARD_TIMEOUT` env knob,
  so `agent/llm/__init__.py`'s re-export (lines 14 and 26, including its
  `__all__` entry) is deleted and
  `docs/features/nonharness-llm-wrapper.md:56` is corrected.
- **No new config files.** One new `TIMEOUTS__` field (above) and **two**
  lazily-read env reads inside `agent/llm/compat.py` —
  `LLM_STACK_COMPAT_OVERRIDE` (operator break-glass, read inside
  `_resolve_degraded_flag()`) and `LLM_STACK_MARKER_DIR` (test-only marker
  redirect for the subprocess CLI case, read inside `_marker_path()`). Both stay
  inside function bodies; `validate_no_module_scope_env.py` blocks the
  module-scope form in a new file. Both announce themselves at
  `logger.warning` with the sentinel token when active, so one sentinel grep
  finds every break-glass path. Neither is a credential and neither is added to
  `.env.example` — declaring either would make `check_env_completeness` require
  an operator-only or test-only variable on every machine forever, and
  `tests/unit/test_env_declaration_readers.py` has a determinate expectation for
  each because both are now named. `ANTHROPIC_API_KEY` and `SENTRY_DSN` are
  already declared. **This bullet is the env inventory** — it has gone stale
  twice (round 5 on `LOCAL_TYPED_HARD_TIMEOUT`, round 8 on the then-unnamed
  marker override); any task that adds an env read updates it in the same
  commit.
- **No migration.** Followers never auto-bump but now get the verify-time and
  startup checks — which is the point: route 3 was previously unguarded.

## Agent Integration

**No new agent-facing surface** — no `[project.scripts]` entry, no MCP tool, no
bridge command. But the runtime's boot and failure presentation change:

- `bridge/telegram_bridge.py::main` and `worker/__main__.py` force degraded-flag
  resolution at startup. Neither may exit on incompatibility.
- `run_typed` / `run_typed_local` raise `LLMStackIncompatible` under a degraded
  stack. Every non-harness caller sees it; because it subclasses `LLMCallError`,
  every existing `except LLMCallError` fail-safe works with no edit (asserted).
- **Telegram intake must keep working in degraded mode**: with the flag set, an
  inbound message still enqueues an AgentSession. This is the acceptance
  property of the owner's "keep receiving and queueing" decision.
- `python -m agent.llm.compat --json` is a tooling entry point (subprocess
  callers), not an agent surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/llm-stack-compat-gate.md` — the import-safety
  contract and its single enforcement test; the predicate, its four call sites,
  and its **five fail-closed cases** (including the splat-only call site);
  the resolver-bound degraded/alert contract and why not fail-closed;
  the three channels, the hibernation exemption, the marker-file clear leg
  (including the break-glass override's own clear), and
  the named drafter-convention exception; the coupled-set model, the
  `pydantic-ai-slim>=2.33.0` boundary, the `hold` and who removes it; why
  `openai` is in no set; what the gate checks and cannot check.
- [ ] Add a row to `docs/features/README.md`.

### Existing Docs to Correct
- [ ] `.claude/skills/update/SKILL.md`, section `### Auto-Bump Critical Dependencies` (see Update System).
- [ ] `pyproject.toml` — the two-line comment above the `anthropic` pin.
- [ ] `docs/features/remote-update.md` — cross-reference.
- [ ] `docs/features/nonharness-llm-wrapper.md` — the import-safety contract,
  the lazy loader and its test seam, `LLMStackIncompatible`, the degraded flag,
  the two-axis split (`run_typed_local` is **not** gated on the Anthropic
  signature), and **line 56's env knob rename**: `LOCAL_TYPED_HARD_TIMEOUT` →
  `TIMEOUTS__LOCAL_TYPED_HARD_S`. The old name is gone from
  `agent/llm/__init__.py`'s `__all__`, so leaving the paragraph documents a
  variable that no longer exists.

### Inline Documentation
- [ ] Each `CoupledSet` carries a prose `reason`; the LLM set's `hold` names
  the blocking issue. The `AUTO_BUMP_SETS` docstring records why `openai` is
  excluded (spike-5's three facts).
- [ ] Docstrings: the `llm` phase makes a real billed call; the local path
  deliberately does not.
- [ ] The alert emitter names the independence constraint and the forbidden
  paths.
- [ ] `wrapper.py` module docstring states the import-safety contract and
  points at the enforcement test.

## Success Criteria

- [ ] **Import-safety contract holds**: with `anthropic`/`pydantic_ai` broken at
  import, `import agent.llm` and `import bridge.telegram_bridge` succeed,
  `run_typed` raises `LLMStackIncompatible`, and all three alert channels fire
  — one test proves all four. **The import half executes in a fresh interpreter
  subprocess against a raise-on-import shim dir**, so the assertion is capable
  of failing in a full xdist run; an in-process or `sys.modules`-purge form does
  not satisfy this criterion.
- [ ] `check_llm_stack_compat()` exists in `agent/llm/compat.py`, importing
  nothing from `scripts/`, with `python -m agent.llm.compat --json` working.
- [ ] The predicate is exercised from three sites **in production on this
  lane's merge**: `/update` verify (unconditional, subprocess), bridge startup,
  worker startup. The fourth site — the auto-bump `llm` phase — is wired and
  covered by tests plus task 7's transcripts, but is **unreachable in
  production for this lane's entire life** by design: the only set declaring
  `gates=("llm", ...)` carries `hold="#3001 Step 2"`, and `CoupledSet.gates`
  defaults exclude `llm`. Its first unattended production execution belongs to
  Step 2's lane, which removes the hold. What this lane asserts instead is that
  the wiring is real: `test_llm_phase_argv_matches_gate_helper` proves the
  phase invokes exactly the argv the shared helper builds, and task 7 leg (a)
  executes that same helper's argv unmocked.
- [ ] `/update` verify's compat leg is **loud, not silent**: on an incompatible
  stack the `ToolCheck` has a non-empty `.error`, `run.py`'s `valor_tools` loop
  logs and appends a warning, and `extract_update_warnings` surfaces it. Both
  resolved versions print on every run, pass or fail, from a dedicated
  call-site block (not from `detail` in the generic loop, which never reads it).
- [ ] The `--json` CLI calls the **pure** predicate, asserted where the assertion
  can fail: **in-process**, calling the CLI's JSON entry function on an
  incompatible stack with a counting `capture_message` stub emits zero calls
  **and** leaves `compat._DEGRADED` unresolved (`None`). The "creates no marker"
  clause is deleted — under the round-7 writer rule no caller passes `proc`, so
  it holds whether the CLI is pure or not. **Out-of-process**, the subprocess run
  proves only the CLI contract: well-formed JSON on stdout carrying
  `compatible`/`loader_ok`/both versions, with a matching exit status. Only
  `_resolve_degraded_flag()` alerts.
- [ ] On an incompatible stack, bridge and worker **start**, and an inbound
  Telegram message still enqueues an AgentSession.
- [ ] **The alert fires in a test where `run_typed` raises** (independence
  proof) and fires on the no-startup-hook path (resolver-bound proof).
- [ ] A raising `capture_message` suppresses nothing else; the hibernation
  `before_send` passes the sentinel event; `data/llm-stack-degraded.{proc}` is
  written on degraded and **cleared on healthy** resolution — only the writing
  process's own path — under the module-level `_MARKER_DIR` seam whose
  **default** equals the directory `ui/app.py` reads. Clearing one process's
  marker leaves another's, and the board stays red.
- [ ] **Only `bridge` and `worker` write markers**: a resolver called without a
  `proc` emits Sentry and the sentinel log but creates no marker file, so no
  one-shot can strand a permanent red. No pid-suffixed markers exist.
- [ ] **The suite does not pollute the operator's board, by mechanism**: the
  redirect is an `autouse=True` fixture in `tests/unit/conftest.py` using the
  **guarded module-object** form (`try: from agent.llm import compat` /
  `except ImportError: return`, then
  `monkeypatch.setattr(_compat, "_MARKER_DIR", tmp_path_factory.mktemp("llm-marker"), raising=False)`),
  not a convention each file re-implements; no test file carries a
  hand-written `_MARKER_DIR` monkeypatch or a live-glob assertion, and the
  subprocess CLI case receives the redirect through the child's
  `LLM_STACK_MARKER_DIR`.
- [ ] **The fixture is inert when `agent/llm/compat.py` is absent, and free for
  tests that never touch it**: on a checkout with task 3 not yet applied (or
  reverted, or mid-`git bisect`) the suite still collects and runs green —
  no setup errors — and no temp directory is created on the early-return
  branch. This is a property of the guarded import, not of `raising=False`.
- [ ] **The marker-dir override announces itself**: with
  `LLM_STACK_MARKER_DIR` set to a value differing from `_MARKER_DIR`'s default,
  `_marker_path()` emits one `logger.warning` carrying the sentinel token, so a
  single sentinel grep surfaces both break-glass paths.
- [ ] **The predicate fails closed on an ambiguous call site**: `len(sites) != 1`
  (not just zero) returns `compatible=False` naming the count and the found
  paths; covered by a synthetic two-site test.
- [ ] **The predicate fails closed on a splat-only call site**: a single
  `create` site forwarding no literal keywords (e.g.
  `self.client.beta.messages.create(**kwargs)`) returns `compatible=False`
  naming the splat-entry count — never `True` by a vacuous subset test. Covered
  by `test_splat_only_call_site_fails_closed` against a synthetic module source.
- [ ] **`/dashboard.json` actually renders it**: with a marker present,
  `dashboard_json()` returns a truthy degraded field carrying both versions and
  the degraded process; with none, the healthy value; with an unreadable marker,
  a 200 and no raise.
- [ ] `LLMStackIncompatible` is a `LLMCallError` subclass — asserted.
- [ ] **`check_llm_stack_compat().compatible is True` on the branch's pinned
  pair** — the positive self-test, run in CI. The derived kwarg set is taken
  from `pydantic_ai.models.anthropic`'s `.create(` call site (not from
  `AnthropicModelSettings.__annotations__`) and the target callable from that
  call's own attribute path; the set contains `temperature`/`top_p`/`top_k` and
  no `anthropic_`-prefixed name.
- [ ] **Two axes, not one**: with the loader healthy and the signature check
  failing, `run_typed` raises `LLMStackIncompatible` and `run_typed_local`
  completes. Both raise when the loader fails.
- [ ] `LLM_STACK_COMPAT_OVERRIDE=healthy` short-circuits `_resolve_degraded_flag()`
  with a sentinel OVERRIDDEN warning, and is ignored by the pure predicate, the
  `--json` CLI, and the auto-bump `llm` gate. **The override branch also clears
  the caller's own marker** when a `proc` was passed, so it cannot strand a
  permanent red on a machine the operator has declared healthy — asserted by
  writing a bridge marker, resetting the memo, then resolving under the override.
- [ ] `LOCAL_TYPED_HARD_TIMEOUT` is gone from `agent/llm/__init__.py`'s
  `__all__`, replaced by `TIMEOUTS__LOCAL_TYPED_HARD_S`, with
  `docs/features/nonharness-llm-wrapper.md:56` corrected.
- [ ] `AUTO_BUMP_SETS` declares `{anthropic, pydantic-ai-slim}` as one set with
  a truthy `hold`; `claude-agent-sdk` as its own unheld set; `openai` in no set
  (asserted in code); `CoupledSet.gates` defaults exclude `llm`.
- [ ] The three spike-2 defects each have a regression test that fails against
  the pre-fix helper (red-state proof recorded); `verify_critical_versions`
  returns identical results across the rewrite.
- [ ] **Rollback verified in two named legs** (task 7), both transcripts in the
  PR description — leg (a) unmocked on a throwaway checkout, leg (b)
  resolution-stubbed (stated as such: PyPI's real latest pair is compatible and
  would never roll back).
- [ ] A failed LLM set does not roll back a successful `claude-agent-sdk` bump;
  a gate that cannot run rolls back with a distinct warning; after every
  rollback path `git status --porcelain pyproject.toml uv.lock` is empty; a
  failed restore sets `restore_failed` and blocks the commit.
- [ ] `openai` keeps its `>=1.0.0` floor — no resolution-semantics change ships
  in this lane.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (runtime)**
  - Name: `compat-builder`
  - Role: import-safety contract (lazy loader, `anthropic_client`), `compat.py`
    (predicate, flag, alert, `--json`), `LLMStackIncompatible`, startup hooks,
    hibernation exemption, dashboard marker read side
  - Agent Type: builder
  - Resume: true

- **Builder (update scripts)**
  - Name: `deps-builder`
  - Role: declaration-aware pin helpers, `CoupledSet`/`AUTO_BUMP_SETS` (+hold),
    per-set gates and rollback, `restore_failed`, `verify.py` call site
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `deps-tester`
  - Role: contract/independence/degraded-intake tests, spike-2 regression tests
    with red-state proofs, set-atomicity tests, the two-leg rollback
    verification, and running the Verification table as final validation
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `deps-documentarian`
  - Role: feature doc, README row, SKILL.md correction, wrapper doc,
    `pyproject.toml` comment
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Verify the working LLM stack on main (no edit)
- **Task ID**: `verify-phase0-pin`
- **Depends On**: none
- **Validates**: `grep -q '"anthropic==0.125.0"' pyproject.toml` plus the spike-1 probe printing `OK`
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Confirm the pin and that the venv resolves it; re-run the spike-1 probe.
- **No edit, no commit.** If the probe fails, stop and report — the premise moved.

### 2. Import-safety contract on `agent/llm/`
- **Task ID**: `build-import-safety`
- **Depends On**: `verify-phase0-pin`
- **Validates**: `tests/unit/test_llm_import_safety.py` (contract test),
  `tests/unit/test_llm_wrapper_local.py` (seam preserved)
- **Informed By**: round-2 B-a/B-b/B-c; spike-5
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Move all third-party stack imports in `wrapper.py` and
  `agent/anthropic_client.py` into a memoized `_load_stack()` invoked from the
  call paths. Module scope: stdlib + our own code only. No per-symbol
  exceptions — `OpenAIChatModel` is not a special case, it is one of the
  symbols.
- Replace the `wrapper_mod.OpenAIChatModel` test seam with the loader seam;
  update `test_llm_wrapper_local.py` and its docstring; assert no real network
  call is reachable.
- Write the contract test's import half now, **in a fresh interpreter
  subprocess with a raise-on-import shim dir on `PYTHONPATH`** — never
  in-process, and never via a `sys.modules` purge. In-process the assertion
  cannot fail: existing test files already import `bridge.telegram_bridge` (→
  `bridge.routing` → `agent.llm`), so under xdist both imports are cache hits.
  See the Solution bullet for the exact subprocess/shim shape and why the purge
  variant is rejected. The alert/typed-exception half lands with task 4 and may
  stay in-process.
- **Migrate `LOCAL_TYPED_HARD_TIMEOUT` (`wrapper.py:167`) to a lazily-read
  `config/settings.py` `TimeoutSettings` field in this same commit.** The
  module-scope-env ratchet (`validate_no_module_scope_env.py`, restored
  `2b926acae`) blocks any commit that adds *or modifies* a module-scope
  `os.environ.get` line, and this rewrite necessarily moves that line. Do not
  attempt to preserve line 167 byte-identically to dodge the hook — migrate it,
  per the pattern the guard's own docstring prescribes. Add
  `local_typed_hard_s: float = Field(default=20.0, ge=1.0, le=300.0, ...)` to
  `TimeoutSettings` (env `TIMEOUTS__LOCAL_TYPED_HARD_S`) and read it **inside**
  `run_typed_local`, never at module scope.
- **Delete the `LOCAL_TYPED_HARD_TIMEOUT` re-export from
  `agent/llm/__init__.py` (lines 14 and 26, `__all__` included)** — it is a
  public attribute, not an internal constant, so the migration is a surface
  change. **The reader set is already resolved and stable** (round-9 nit):
  `git grep -n LOCAL_TYPED_HARD_TIMEOUT` at the current tip returns five code
  hits, all inside `agent/llm/` — `__init__.py:14`/`:26`, `wrapper.py:167`
  (module-scope read being migrated), `:192` (docstring reference in
  `run_typed_local`; reword to `settings.timeouts.local_typed_hard_s`), `:209`
  (the sole functional reader) — and zero readers anywhere else in tracked code.
  Re-run the grep as a confirmation, not a discovery step; treat any hit outside
  `agent/llm/` as a premise change that stops the task rather than something to
  narrate in the build report. `docs/features/nonharness-llm-wrapper.md:56`
  documents the old env knob by name and is a task-8 correction.

### 3. The compat predicate
- **Task ID**: `build-compat-predicate`
- **Depends On**: `build-import-safety`
- **Validates**: `tests/unit/test_llm_stack_compat.py`
- **Informed By**: spike-1, spike-3; round-2 introspection concern
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Create `agent/llm/compat.py`: `CompatResult` (with **both** `loader_ok` and
  `compatible`), `check_llm_stack_compat(allow_network: bool = False)`. Local
  mode: run the loader (`loader_ok`), then the **call-site-derived** signature
  check (`compatible`). `allow_network=True` adds one minimal `run_typed` call.
- **Derive the forwarded kwargs from `pydantic_ai.models.anthropic`'s `.create(`
  call site via `ast` over `inspect.getsource`, and resolve the target callable
  from that same call's attribute path** (`self.client.beta.messages` on 2.9.0)
  against `anthropic.AsyncAnthropic(api_key="x")` — constructor only, no
  network. **Do not** use `AnthropicModelSettings.__annotations__` (20 of its 31
  keys are absent from `create` on the pinned pair — the round-5 blocker) and
  **do not** hardcode `anthropic.resources.messages.AsyncMessages` (non-beta;
  missing four kwargs the call site passes). See the Solution bullet for the
  five-step algorithm and the executed evidence.
- **Fail closed on `len(sites) != 1`**, not just on zero sites — never fall
  through to `sites[0]`. The reason names the count and the found paths
  verbatim. Test the multi-site case beside the zero-site case, feeding a
  synthetic module source with two `create` calls.
- **Fail closed on a splat-only site** (round-8 blocker). After collecting the
  single site's literal keywords (`forwarded = [k.arg for k in site.keywords if k.arg]`)
  and **before** the subset test, `if not forwarded:` return
  `compatible=False` with a reason naming the splat-entry count
  (`sum(1 for k in site.keywords if k.arg is None)`). This is a *separate* gate
  from the count gate: with one splat-only site the count is 1, the path
  resolves, `getsource` works, `forwarded` is empty, and the subset test is
  vacuously true — `compatible=True` against the known-bad pair, on the exact
  Data Flow route 3 this lane exists to close. Add
  `test_splat_only_call_site_fails_closed` fed a synthetic module source whose
  sole `create` call is `self.client.beta.messages.create(**kwargs)`. Do **not**
  treat the `temperature`/`top_p`/`top_k` shape assertions as the control here:
  they are suite-only and never run at `/update` verify or service startup.
- Record in the module docstring **why the target resolves against
  `anthropic.AsyncAnthropic`**: the call site names only the attribute path, and
  `pydantic_ai` types `self.client` as the `AsyncAnthropicClient` union
  (Bedrock / BedrockMantle / Vertex included); `AsyncAnthropic` is right because
  it is the class `wrapper.py` constructs.
- **Write the positive self-test first**: `check_llm_stack_compat().compatible
  is True` on the pinned pair. Plus the shape assertions — the derived set
  contains `temperature`/`top_p`/`top_k` and no `anthropic_`-prefixed name.
  Round 5's subset-only test passed trivially against a predicate that would
  have degraded the whole fleet; this pair of tests is what makes that
  impossible.
- Every failure path returns `compatible=False` with verbatim `reason` and
  `exc_type`. No bare `except Exception: pass`.
- **Keep the predicate pure.** `check_llm_stack_compat` never touches the
  memoized degraded flag, never calls `capture_message`, never writes or clears
  the marker file. Alerting belongs solely to `_resolve_degraded_flag()` in
  task 4.
- Add the `python -m agent.llm.compat --json` entry point, calling **only the
  pure predicate** — its callers (`verify.py`, the auto-bump `llm` gate) must
  not alarm production for a stack the gate is about to roll back. Import
  nothing from `scripts/`; reach the stack only through the loader, inside
  function bodies.
- Test purity where the test can actually fail: **in-process**, call the
  function and the CLI's JSON entry directly on an incompatible stack with a
  counting `capture_message` stub — zero calls **and** `compat._DEGRADED is None`
  afterwards. Do **not** assert "no marker file" as purity evidence: no caller
  passes `proc`, so that assertion holds either way (round-8 concern). Keep a
  subprocess run only to prove the out-of-process CLI contract (well-formed JSON
  with `compatible`/`loader_ok`/both versions, matching exit status), passing the
  marker-directory redirect through the child's environment — which requires
  `_marker_path()` to read that override lazily inside the function.

### 4. Degraded flag, resolver-bound alert, startup hooks
- **Task ID**: `build-degraded-posture`
- **Depends On**: `build-compat-predicate`
- **Validates**: `tests/unit/test_llm_stack_degraded_start.py`, the completed
  contract test, the degraded-intake integration test
- **Informed By**: Failure Posture (owner decision); round-2 B-d/B-e and the
  dashboard-transport blocker
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Lazily self-resolving memoized flag in `compat.py` (memoizing **both** axes);
  **the alert fires from the first transition to degraded inside the resolver**
  — Sentry fatal, `logger.critical` sentinel, and a **per-process**
  `data/llm-stack-degraded.{proc}` marker (versions + `exc_type` + which axis
  failed). Healthy resolution clears **only this process's own** path
  (`marker.unlink(missing_ok=True)`), never a glob.
- **Signature: `_resolve_degraded_flag(proc: str | None = None)`; a marker is
  written only when `proc` is given.** `bridge/telegram_bridge.py::main` passes
  `"bridge"`, `worker/__main__.py` passes `"worker"`; every other caller passes
  nothing and writes no marker, getting Sentry + the sentinel log only. No
  pid-suffixed markers — a process that exits while degraded has no clear leg,
  and the read side has no liveness filter, so pid markers strand permanent red.
- **Marker directory is a module-level seam**: `_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"`
  at module scope, with all writes/clears through `_marker_path(proc)`. Never
  cwd-relative. **The redirect is enforced by mechanism, not per-file
  convention** (round-8 concern): add an `autouse=True`, function-scoped fixture
  to `tests/unit/conftest.py` in the **guarded module-object** form —
  `try: from agent.llm import compat as _compat` / `except ImportError: return`,
  then `monkeypatch.setattr(_compat, "_MARKER_DIR", tmp_path_factory.mktemp("llm-marker"), raising=False)`
  — so no degraded-driving test can write into the live `data/` a running
  bridge, worker, and dashboard share, including
  `tests/unit/test_llm_stack_compat.py`, which the per-file convention had
  missed. **Do not use the string-target form**
  (`monkeypatch.setattr("agent.llm.compat._MARKER_DIR", tmp_path, raising=False)`):
  `raising=False` suppresses only the attribute check, and the string form's
  `derive_importpath` → `resolve(module)` performs a real import that raises
  first, erroring every test in the repo whenever `compat.py` is absent
  (round-9 blocker; reproducible on `main` today). Use `tmp_path_factory`, not
  `tmp_path`, and put the `mktemp` call on the success branch so the early
  return allocates nothing; place the fixture in `tests/unit/conftest.py`, not
  the root `tests/conftest.py`, since all four marker-driving files live under
  `tests/unit/`. Do **not** add per-file `_MARKER_DIR` monkeypatches or per-file
  live-glob assertions. Keep one case asserting the *default*, resolving
  `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"` directly (not
  the patched global) and comparing it to `Path(ui.app.__file__).parent.parent / "data"`.
  The subprocess CLI case, which an in-process fixture cannot reach, gets the
  redirect through the child's **`LLM_STACK_MARKER_DIR`**, read lazily inside
  `_marker_path()`. When that variable is set and differs from `_MARKER_DIR`'s
  default, `_marker_path()` logs once at `logger.warning` with the sentinel
  token — the same visibility contract as the `LLM_STACK_COMPAT_OVERRIDE`
  OVERRIDDEN warning, so a single sentinel grep finds both break-glass paths and
  a stale inherited value cannot silently relocate the only standing signal.
- The resolver is the **only** alerting path; `check_llm_stack_compat` stays
  pure (task 3).
- **Break-glass override**: read `LLM_STACK_COMPAT_OVERRIDE` **inside**
  `_resolve_degraded_flag()` (module scope is blocked by
  `validate_no_module_scope_env.py`); `"healthy"` short-circuits to
  not-degraded after a `logger.warning(<sentinel> + " OVERRIDDEN")`. Not
  honoured by the pure predicate, the `--json` CLI, or the auto-bump gate.
  **The override branch must clear this process's own marker before returning**
  (`if proc: _marker_path(proc).unlink(missing_ok=True)`) — the short-circuit
  happens before the predicate, so without it no future resolution can ever
  reach the healthy branch's clear and the board stays red forever on a machine
  the operator declared healthy (round-8 concern). Test: write the bridge marker
  via a degraded resolution, reset the memo, set the override, resolve again
  with `proc="bridge"`, assert the marker is gone and the OVERRIDDEN warning
  fired.
- `LLMStackIncompatible(LLMCallError)`. `run_typed` raises it when
  `not loader_ok or not compatible`; `run_typed_local` raises **only** when
  `not loader_ok` — an Anthropic signature break must not fall the Ollama
  classifiers over. Test: loader OK + signature check failing → `run_typed`
  raises, `run_typed_local` completes against a stubbed `FunctionModel`.
  **`loader_ok` is stack-wide** (accepted residual, round-9 concern): a single
  `_load_stack()` imports the whole set, so an `anthropic` **ImportError** still
  raises from `run_typed_local` — matching today's module-scope behavior, no
  regression — and only the *signature* axis is separated. Assert it rather than
  letting it fall out of the shared boolean: `test_anthropic_import_error_local_path`
  stubs only `anthropic` to raise on import and asserts `run_typed_local` raises
  `LLMStackIncompatible`. Do **not** split the loader into per-domain legs in
  this lane.
- Startup calls in `bridge/telegram_bridge.py::main` and `worker/__main__.py`
  force resolution; neither exits on incompatibility.
- Exempt the sentinel from `_sentry_before_send`'s hibernation drop.
- Dashboard read side in `ui/app.py::dashboard_json`: glob
  `data/llm-stack-degraded*`, red while any marker exists, naming the degraded
  processes; fail-quiet `except OSError` like its siblings. **This leg gets its
  own test file** (`tests/unit/test_dashboard_llm_degraded.py`) — round 5
  caught the same defect class on the verify leg, and a channel this plan calls
  its only *standing* signal cannot ship untested.
- Tests: independence (alert while `run_typed` raises), no-startup-hook path,
  Sentry-failure tolerance, per-process marker clear leg (clearing the bridge
  marker leaves the worker's, and the board stays red), hibernation exemption,
  intake survives degraded, the two-axis split, and the override short-circuit.

### 5. Declaration-aware pin helpers
- **Task ID**: `build-pin-helpers`
- **Depends On**: `verify-phase0-pin`
- **Validates**: the three spike-2 regression tests (red-state proofs recorded)
  and `test_verify_critical_versions_unchanged_by_helper_rewrite`
- **Assigned To**: `deps-builder`
- **Parallel**: true (with tasks 2-4 — no shared files)
- Reader locates the actual declaration (comment-blind, extras-tolerant);
  writer handles extras; both refuse loudly. Fixture preserves the
  floor-plus-comment shape. `openai` keeps its floor — no pin change.

### 6. Coupled sets, per-set gates, hold, atomic rollback
- **Task ID**: `build-coupled-sets`
- **Depends On**: `build-pin-helpers`, `build-compat-predicate`
- **Validates**: the set-atomicity, default-gates, hold, `openai`-exclusion,
  rollback-cleanliness, and verify-callsite tests in
  `tests/integration/test_remote_update.py`
- **Informed By**: spike-2, spike-4, spike-5; round-2 hold blocker and
  verify-subprocess concern
- **Assigned To**: `deps-builder`
- **Parallel**: false
- `CoupledSet` (members, `import_names`, `gates` defaulting to
  `("import", "pytest")`, `reason`, `hold`); `AUTO_BUMP_SETS` replacing
  `AUTO_BUMP_PACKAGES`, with the LLM set held on `#3001 Step 2` and the
  `openai`-in-no-set assertion.
- All-or-nothing resolve/rewrite/sync/gate per set; skip on hold (legible
  `BumpResult`); skip sync when no pin changed; per-set snapshot/restore;
  `restore_failed` + `git checkout -- uv.lock`; guard `run.py`'s commit branch.
- Gate phases: `llm` → subprocess `--json --allow-network` in the target venv,
  with the argv built by the shared `llm_gate_argv(venv_python)` helper (task 7
  leg (a) calls the same helper; `test_llm_phase_argv_matches_gate_helper`
  pins them together); `import` → the set's own `import_names`; `pytest`
  unchanged. Surface the failed phase in the warning. Commit/push/restart
  ordering untouched.
- `verify.py`: the unconditional subprocess `ToolCheck` appended to
  `result.valor_tools`, **with the failure reason in `.error`** (non-empty on
  failure, or `run.py:2673-2684`'s `if not tool.available and tool.error:`
  makes the whole leg silent) and both versions in `version`/`detail`. Add the
  print-on-pass block in `run.py` as a **lookup by `name` in
  `result.verification.valor_tools`** right after `run.py:2636`'s
  `verify_environment` call and before the warning loop at `run.py:2673` — the
  generic loop never reads `detail`. **No new `UpdateResult` field and no second
  `verify` call site**; see Technical Approach for the exact three lines and why
  copying `run.py:1907-1909`'s plumbing literally is wrong. Do not add
  `llm-stack-compat` to `human_gated_tools`.
- Replace the `pyproject.toml` stopgap comment with the two-line form.
- Re-add `anthropic` (as a held-set member) **in this same task** — never as a
  separate earlier commit.

### 7. Two-leg rollback verification
- **Task ID**: `verify-known-bad-rollback`
- **Depends On**: `build-degraded-posture`, `build-coupled-sets`
- **Validates**: the two captured transcripts
- **Assigned To**: `deps-tester`
- **Parallel**: false
- **Leg (a), unmocked:** on a throwaway copy of the repo (never the shared
  checkout), write `anthropic==1.0.0` + `pydantic-ai-slim[anthropic]==2.9.0`,
  `uv sync --all-extras`, invoke the gate subprocess **through
  `llm_gate_argv(venv_python)`** — the same helper the `llm` phase runner calls,
  never a hand-written command line; assert non-zero exit with
  `unexpected keyword argument 'temperature'`, and that stdout is well-formed
  JSON carrying `compatible`/`loader_ok`/both versions. The "left no marker
  behind" clause is **dropped** as purity evidence (round-8 concern): the CLI
  passes no `proc`, so under the writer rule it writes none whether it is pure
  or not. CLI purity is asserted in-process in task 3.
- **Leg (b), resolution-stubbed:** monkeypatch `get_pypi_latest` to the
  known-bad pair, temporarily clear the hold in the fixture, run
  `auto_bump_deps`, assert `rolled_back is True` and both pins restored.
  **State in the transcript that this leg stubs resolution and clears the
  hold** — PyPI's real latest pair is compatible and a held set never bumps.
- Assert the converse on a good pair (hold cleared): gate passes, bump survives.
- Paste both transcripts into the PR description.

### 8. Documentation
- **Task ID**: `document-feature`
- **Depends On**: `verify-known-bad-rollback`
- **Validates**: `docs/features/llm-stack-compat-gate.md` exists and is indexed
- **Assigned To**: `deps-documentarian`
- **Parallel**: false
- Everything under Documentation above, including the expected `/update --cron`
  wall-clock delta from per-set syncing.

### 9. Final validation
- **Task ID**: `validate-all`
- **Depends On**: `document-feature`
- **Validates**: the Verification table
- **Assigned To**: `deps-tester`
- **Parallel**: false
- Run every Verification row; confirm each Success Criterion, both transcripts
  in the PR description, and that the PR body says `Refs #3001` by default.
  Work Items 2 and 3 now live in #3074 and #3075 and Step 2 in #3073, so
  `Closes #3001` is a defensible alternative — but it is the **owner's** call
  (issue comment `5505317383`), not the builder's. Do not switch it unilaterally.

## Verification

Rows assert *declarations and executed paths*, not file text.

| Check | Command | Expected |
|-------|---------|----------|
| Live LLM call works on the branch's pins | `.venv/bin/python -c "import asyncio;from pydantic import BaseModel;from agent.llm.wrapper import run_typed;O=type('O',(BaseModel,),{'__annotations__':{'answer':str}});print(asyncio.run(run_typed('Reply with answer=hi',O)))"` | exit code 0 |
| Predicate exists and reports compatible | `.venv/bin/python -m agent.llm.compat --json` | exit 0, JSON `"compatible": true` |
| Predicate does not import from `scripts/` | `.venv/bin/python -c "import ast;t=ast.parse(open('agent/llm/compat.py').read());assert not [n for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom)) and 'scripts' in (getattr(n,'module','') or '')+''.join(a.name for a in getattr(n,'names',[]))]"` | exit code 0 |
| Import-safety contract | `./scripts/pytest-clean.sh tests/unit/test_llm_import_safety.py -q` | exit code 0 |
| No module-scope third-party stack imports in `agent/llm/` | `.venv/bin/python -c "import ast,sys;bad={'anthropic','pydantic_ai'};files=['agent/llm/wrapper.py','agent/llm/compat.py','agent/anthropic_client.py'];hits=[(f,n.lineno) for f in files for n in ast.walk(ast.parse(open(f).read())) if isinstance(n,(ast.Import,ast.ImportFrom)) and n.col_offset==0 and any((getattr(n,'module','') or a.name).split('.')[0] in bad for a in n.names)];assert not hits,hits"` | exit code 0 |
| Module-scope-env ratchet satisfied in `agent/llm/` | `.venv/bin/python -c "from scripts.scan_module_scope_env import find_module_scope_env_calls as f;import pathlib;p='agent/llm/wrapper.py';assert not f(pathlib.Path(p).read_text(),p)"` | exit code 0 (verified 2026-09-02 to return exactly one `EnvCall` today, at `wrapper.py:167` — this row is red on main and must go green) |
| Coupled set declared with hold | `.venv/bin/python -c "from scripts.update.deps import AUTO_BUMP_SETS as S; s=[x for x in S if 'anthropic' in x.members][0]; assert set(s.members)=={'anthropic','pydantic-ai-slim'} and s.hold"` | exit code 0 |
| `openai` is in no coupled set | `.venv/bin/python -c "from scripts.update.deps import AUTO_BUMP_SETS; assert 'openai' not in {m for s in AUTO_BUMP_SETS for m in s.members}"` | exit code 0 |
| New sets do not inherit the billed llm phase | `.venv/bin/python -c "from scripts.update.deps import CoupledSet; assert CoupledSet(members=['x'], import_names=('x',), reason='t').gates == ('import','pytest')"` | exit code 0 |
| Import phase is set-derived, not hardcoded | `! grep -q "import anthropic; import claude_agent_sdk" scripts/update/deps.py` | exit code 0 (the literal is replaced by `set.import_names`) |
| Gate invocation is real, not a mention | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "llm_gate" -q` | exit code 0 |
| Held set is skipped and legible | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "held_set" -q` | exit code 0 |
| Verify runs the check unconditionally **and loudly** (non-empty `.error`, surfaced by `extract_update_warnings`) | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "verify_runs_compat" -q` | exit code 0 |
| The `llm` phase and the manual gate invocation share one argv construction | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "llm_phase_argv" -q` | exit code 0 |
| The `--json` CLI is pure — zero `capture_message` calls **and** the memoized flag left unresolved, asserted in-process | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_compat.py -k "pure or cli" -q` | exit code 0 |
| The marker redirect is a mechanism, not a per-file convention (executes; fails if the fixture is absent, misnamed, non-autouse, or erroring) | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "marker_redirect_is_autouse" -q` | exit code 0 |
| The autouse fixture is inert without `compat.py` (guarded-import form, not `raising=False`) | `./scripts/pytest-clean.sh tests/unit/test_settings.py -q` run on a tree with `agent/llm/compat.py` temporarily renamed away | exit code 0, zero setup errors |
| The marker-dir override announces itself | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "marker_dir_override_warns" -q` | exit code 0 |
| `run_typed_local` behavior under an `anthropic`-only ImportError is asserted, not incidental | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "anthropic_import_error_local_path" -q` | exit code 0 |
| The predicate reports the pinned pair **compatible** (the round-5 blocker's red state) | `.venv/bin/python -c "from agent.llm.compat import check_llm_stack_compat as c;r=c();assert r.compatible and r.loader_ok, r.reason"` | exit code 0 |
| The derived kwarg set comes from the call site, not the settings TypedDict | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_compat.py -k "derived or forwarded" -q` | exit code 0 (asserts `temperature`/`top_p`/`top_k` present, no `anthropic_`-prefixed name) |
| `run_typed_local` is not gated on the Anthropic signature axis | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "two_axis or loader_ok" -q` | exit code 0 |
| Break-glass override works and is scoped to the resolver | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "override" -q` | exit code 0 |
| The dashboard read side actually renders the degraded state | `./scripts/pytest-clean.sh tests/unit/test_dashboard_llm_degraded.py -q` | exit code 0 |
| Marker path is a module-level seam, per-process, and pid-free | `.venv/bin/python -c "s=open('agent/llm/compat.py').read();assert 'llm-stack-degraded' in s and '_MARKER_DIR' in s and 'parents[2]' in s and 'missing_ok' in s and 'getpid' not in s"` | exit code 0 |
| The import-safety contract runs out-of-process (cannot pass on a `sys.modules` cache hit) | `.venv/bin/python -c "s=open('tests/unit/test_llm_import_safety.py').read();assert 'subprocess' in s and 'PYTHONPATH' in s"` | exit code 0 |
| Only `bridge`/`worker` write markers (no-`proc` caller writes none) | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "no_proc or marker_writer" -q` | exit code 0 |
| Ambiguous `create` call site fails closed (`len(sites) != 1`) | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_compat.py -k "multi_site or introspection" -q` | exit code 0 |
| Splat-only `create` call site fails closed (no vacuous pass) | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_compat.py -k "splat_only" -q` | exit code 0 |
| Break-glass override clears its own marker | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "override_clears_marker" -q` | exit code 0 |
| The old env knob is retired from the package surface | `.venv/bin/python -c "import agent.llm as m;assert 'LOCAL_TYPED_HARD_TIMEOUT' not in m.__all__ and not hasattr(m,'LOCAL_TYPED_HARD_TIMEOUT');from config.settings import settings;assert settings.timeouts.local_typed_hard_s"` | exit code 0 |
| Startup is degraded, not fatal; alert independence; marker clear leg | `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -q` | exit code 0 |
| Typed exception preserves existing fail-safes | `.venv/bin/python -c "from agent.llm.wrapper import LLMCallError, LLMStackIncompatible; assert issubclass(LLMStackIncompatible, LLMCallError)"` | exit code 0 |
| Pin comment keeps the constraint and adds the pointer | `grep -c "AUTO_BUMP_SETS" pyproject.toml` | output > 0 |
| Update-system tests pass | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Anti-criterion: no dependency upgrade smuggled in | `grep -cE '"pydantic-ai-slim\[anthropic\]==2\.9\.0"' pyproject.toml` | output > 0 |
| Anti-criterion: `anthropic` pin unchanged by this lane | `grep -c '"anthropic==0.125.0"' pyproject.toml` | output > 0 |
| Anti-criterion: `openai` floor unchanged | `grep -c '"openai>=1.0.0"' pyproject.toml` | output > 0 |
| Anti-criterion: Work Item 2 files untouched | `git diff --name-only origin/main...HEAD -- models/agent_session.py tests/unit/test_agent_session.py \| wc -l` | output is 0 |
| Update skill doc corrected | `! grep -q "import check" .claude/skills/update/SKILL.md` | exit code 0 |
| Feature doc exists | `test -f docs/features/llm-stack-compat-gate.md` | exit code 0 |

## Critique Results

### Round-9 war room (2026-09-02) — FULL roster, verdict NEEDS REVISION

**Dispatch deviation, recorded (unchanged from rounds 7-8):** the critique agent had no
Agent/Task spawn tool in its context, so the three FULL lenses were executed inline by a
single agent against the real files, the live venv, and a real pytest run rather than by
three parallel sub-agents. Result files were still written to the run-dir barrier and the
`critique-roster-check --plan-path` membership + grounding gate passed 3/3 with an empty
`ungrounded` list. Every finding below was verified by execution at `b90e99892`, not
inferred from a bundle. Attribution names the lens, not a separate agent.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness (Skeptic) | The round-8 fix errors **every test in the suite**, and the plan's stated reason it is safe is factually wrong. The prescribed root-`tests/conftest.py` fixture does `monkeypatch.setattr("agent.llm.compat._MARKER_DIR", tmp_path, raising=False)`, justified three times over as "`raising=False` keeps it inert until `compat.py` exists and for every test that never imports it". `raising=False` suppresses only the *attribute* existence check: the string-target form first calls `derive_importpath`, whose `resolve(module)` performs a real import and raises **before** `raising` is consulted. Reproduced: `derive_importpath("agent.llm.compat._MARKER_DIR", False)` raises `ImportError: import error in agent.llm.compat: No module named 'agent.llm.compat'`, and a real pytest run of an unrelated no-op test under exactly the prescribed fixture ERRORs at setup. The second half of the claim is wrong too — the string form force-imports `agent.llm.compat` for every test in the repo, so it is never inert for tests that do not use it. Task 4 depends on task 3, so the happy path survives; every intermediate state does not — the task-2 commit, a `git bisect`, a revert of task 3, or a builder who trusts the plan's "inert until `compat.py` exists" and lands the conftest change early all convert a green suite into ~14,216 setup errors. This is the plan's own named defect class landing on the mechanism introduced to replace consumer discipline. | **Fixed (round 9)** — adopted as prescribed. The fixture becomes the guarded module-object form (`try: from agent.llm import compat as _compat` / `except ImportError: return`, then `monkeypatch.setattr(_compat, "_MARKER_DIR", ..., raising=False)`), which skips `derive_importpath` entirely; `raising=False` is retained only for the window where `compat.py` exists but `_MARKER_DIR` does not. The false rationale is corrected at all three prose sites — Failure Posture (which now records the reproduction and the reason the string form is also never inert *after* `compat.py` exists), task 4, and the Test Impact conftest row — each carrying an explicit "do not use the string-target form" instruction. Reproduced independently at the current tip: `derive_importpath('agent.llm.compat._MARKER_DIR', False)` raises `ImportError: import error in agent.llm.compat: No module named 'agent.llm.compat'`. New Success Criterion ("inert when `compat.py` is absent") and a Verification row running an unrelated unit file with `compat.py` renamed away. | Use a defensive module-object form, which skips `derive_importpath` entirely: `@pytest.fixture(autouse=True)` / `def _redirect_llm_marker_dir(monkeypatch, tmp_path):` / `    try:` / `        from agent.llm import compat as _compat` / `    except ImportError:` / `        return` / `    monkeypatch.setattr(_compat, "_MARKER_DIR", tmp_path, raising=False)`. The guarded import is what delivers the inertness the plan currently attributes to `raising=False`; keep `raising=False` only for the attribute, covering the window where `compat.py` exists but `_MARKER_DIR` has not been added. Then correct the three prose sites repeating the false property — Failure Posture, task 4, and the Test Impact `tests/conftest.py` row. Red-state proof for the build report: `.venv/bin/python -c "from _pytest.monkeypatch import derive_importpath; derive_importpath('agent.llm.compat._MARKER_DIR', False)"` raises `ImportError` on `main` today. |
| CONCERN | Scope & Value | The same fixture bills the entire repository's suite for an isolation need four files have. It is `autouse=True`, function-scoped, in the **root** `tests/conftest.py`, and it requests `tmp_path`, so pytest materializes a temp directory for every test whether or not it touches `agent.llm.compat`. Measured: 300 parametrized no-op tests under exactly this fixture shape produced **600** directories under the session tmp root; the repo has **14,216** test functions, so a full run manufactures on the order of 28,000 unread temp directories, and pytest's default retention keeps three sessions of them. A full `tests/unit/` run already takes about 20 minutes on a memory-constrained machine several agents share. The mechanism-over-convention goal is right and does not require the unconditional cost. | **Fixed (round 9)** — both reductions adopted. The fixture takes session-scoped `tmp_path_factory` and calls `mktemp("llm-marker")` **only on the success branch**, so the early return allocates nothing; and it moves from the root `tests/conftest.py` to `tests/unit/conftest.py` (which already exists), since all four marker-driving files — `test_llm_import_safety.py`, `test_llm_stack_degraded_start.py`, `test_dashboard_llm_degraded.py`, `test_llm_stack_compat.py` — live under `tests/unit/`. The mechanism property is preserved (no file re-implements the redirect) while `tests/integration/` pays nothing. The measured numbers (600 dirs from 300 no-op tests; ~14,216 test functions; ~28,000 dirs per full run, three sessions retained) are recorded in Failure Posture in place of the qualitative claim, and every `tests/conftest.py` reference across Solution, Test Impact, Success Criteria, task 4, and the decision log is retargeted. | Two independent reductions, both compatible with the blocker fix. (1) Do not request `tmp_path`; take session-scoped `tmp_path_factory` and call `tmp_path_factory.mktemp("llm-marker")` only on the branch where `compat` imported successfully, so pytest creates nothing for tests that return early. (2) Move the fixture from the root `tests/conftest.py` to `tests/unit/conftest.py` — all four marker-driving files listed in Test Impact live under `tests/unit/`, so this preserves the mechanism property (no file re-implements it) while excluding `tests/integration/`. If the root location is kept deliberately, record why. Put the measured numbers in the plan rather than the qualitative claim. |
| CONCERN | Risk & Robustness (Adversary), also flagged by History & Consistency | Round 8's subprocess-redirect fix introduces a **second** new env read — an override relocating the marker directory — that the plan never names, never requires to announce itself, and never adds to the inventory that exists to catch exactly this. Update System still reads "One new `TIMEOUTS__` field (above) and one break-glass env read, `LLM_STACK_COMPAT_OVERRIDE`", while Failure Posture, task 3, and task 4 each require `_marker_path()` to read an unnamed override lazily inside the function. The new read is worse-behaved than the one beside it: `LLM_STACK_COMPAT_OVERRIDE` emits a sentinel OVERRIDDEN warning, so its use is visible in logs, whereas the marker-dir override silently relocates the write path of the channel this plan calls its **only standing signal**. Any process inheriting a stale value — a launchd plist, an exported shell var, a cron env — writes `llm-stack-degraded.bridge` somewhere `ui/app.py` never globs, and the board stays green on a degraded bridge with nothing saying why: the plan's own "stuck dashboard equals no dashboard channel" mode in its false-green polarity, introduced by a test-only affordance. This is also the second time in two rounds that this inventory bullet has gone stale (round 5 caught it on `LOCAL_TYPED_HARD_TIMEOUT`), and `tests/unit/test_env_declaration_readers.py` gives an unnamed variable no determinate expectation. | **Fixed (round 9)** — adopted as prescribed. The override is named **`LLM_STACK_MARKER_DIR`** at all three sites that previously said only "the override" (Failure Posture, task 3's Test Impact bullet, task 4), and `_marker_path()` logs once at `logger.warning` with the sentinel token when it is set and differs from `_MARKER_DIR`'s default — the same visibility contract as the OVERRIDDEN warning, so one sentinel grep finds both break-glass paths. The Update System bullet is rewritten to the prescribed two-read inventory (`LLM_STACK_COMPAT_OVERRIDE`, `LLM_STACK_MARKER_DIR`), both lazily read inside function bodies, neither declared in `.env.example`, with the reason; it also now carries a standing instruction that any task adding an env read updates the inventory in the same commit, since this bullet has gone stale twice. New `test_marker_dir_override_warns` case, a Success Criterion, and a Verification row. | Name it (e.g. `LLM_STACK_MARKER_DIR`) at the three sites that currently say only "the override", and have `_marker_path(proc)` log once at `logger.warning` with the sentinel token when the override is active and differs from `_MARKER_DIR`'s default — the same visibility contract as the OVERRIDDEN warning, so one sentinel grep finds both break-glass paths. Change Update System to: "**No new config files.** One new `TIMEOUTS__` field (above) and **two** lazily-read env reads inside `agent/llm/compat.py` — `LLM_STACK_COMPAT_OVERRIDE` (operator break-glass, read inside `_resolve_degraded_flag()`) and `LLM_STACK_MARKER_DIR` (test-only marker redirect, read inside `_marker_path()`). Neither is a credential and neither is added to `.env.example` — declaring either would make `check_env_completeness` require an operator/test-only variable on every machine forever." Both stay inside function bodies (`validate_no_module_scope_env.py` blocks the module-scope form in a new file). Add the announce-on-override case to `tests/unit/test_llm_stack_degraded_start.py`. |
| CONCERN | Risk & Robustness (Skeptic) | The two-axis split delivers its stated protection on only one of the two axes, and misses the one the next lane makes likely. Its rationale is that an Anthropic-domain fault "must not fall the two hot-path classifiers (intake intent in `tools/classifier.py`, Job bind-or-mint in `bridge/job_router.py`) back to their conservative defaults fleet-wide" — verified: `run_typed_local` genuinely never touches `anthropic` (`wrapper.py:170-209`, its docstring's first deliberate difference being "No Anthropic client and no shared Anthropic semaphore"). But `loader_ok` is a **stack-wide** boolean produced by one memoized `_load_stack()` that the plan requires to import the entire third-party set with "no per-symbol option menu". An `anthropic` **ImportError** therefore sets `loader_ok=False` and `run_typed_local` raises anyway — the exact fleet-wide classifier fallback the bullet says it prevents. Not hypothetical for the lane immediately downstream: the plan's own Research section records that anthropic 1.0.0 moved its HTTP layer to `httpx2`, an ImportError-class hazard on the anthropic axis, and #3073 moves that pin. It is also the precise mirror of spike-5's finding (an `openai`-side ImportError felling the Anthropic path through a shared module scope), relocated from module scope into the loader rather than dissolved. | **Fixed (round 9)** — cheapest honest option adopted, plus the discriminating test. The two-axis Solution bullet now states that `loader_ok` is **stack-wide**, so an `anthropic` ImportError still raises `LLMStackIncompatible` from `run_typed_local`; that this matches today's module-scope behavior and is therefore a no-regression residual; and that only the *signature* axis is separated. Recorded as **Risk 6 (accepted residual)** beside Risk 5, naming the #3073 / httpx2 exposure and why the per-domain loader split is deferred rather than denied. Task 4's restatement carries the same sentence plus an explicit "do not split the loader in this lane". The discriminating case `test_anthropic_import_error_local_path` (stub **only** `anthropic` to raise on import; assert `run_typed_local` raises) is added to Test Impact with its own Verification row, so the behavior is asserted rather than falling out of a shared boolean. | Cheapest honest option: keep one `_load_stack()` and add a sentence to the two-axis bullet stating `loader_ok` is stack-wide, so an `anthropic` **ImportError** still raises `LLMStackIncompatible` from `run_typed_local` — matching today's module-scope behavior, no regression — and that only the *signature* axis is separated; record it as an accepted residual beside Risk 5. Sound option, if the protection is meant to be real: give the loader two memoized legs sharing one cache, `_load_anthropic_stack()` (`anthropic`, `pydantic_ai.models.anthropic`, `AnthropicProvider`) and `_load_local_stack()` (`pydantic_ai`, `pydantic_ai.models.openai.OpenAIChatModel`, `OllamaProvider`); make `CompatResult.loader_ok` the conjunction while `_resolve_degraded_flag` memoizes the legs separately, and gate `run_typed_local` on the local leg only. Either way, add the discriminating case to the two-axis row in `tests/unit/test_llm_stack_degraded_start.py`: with **only** `anthropic` stubbed to raise on import, assert the chosen `run_typed_local` behavior explicitly rather than letting it fall out of a shared boolean. |
| NIT | Scope & Value | The Verification row "The marker redirect is a mechanism, not a per-file convention" runs `assert '_MARKER_DIR' in s and 'autouse=True' in s` against `tests/conftest.py`. The second conjunct is already satisfied on `main` — `autouse=True` appears at `tests/conftest.py:138`, `:366`, `:441`, `:469`, `:638`, `:730`, `:737`, `:840`, `:914` and more — so the row reduces to a bare substring check for `_MARKER_DIR` that a comment or docstring would satisfy. Given this round's blocker is that the fixture does not run at all, a row passing on mere string presence is the weakest possible evidence for the criterion it is named after. | **Fixed (round 9)** — adopted as prescribed. The text-grep row is replaced with `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "marker_redirect_is_autouse" -q`, backed by a new `test_marker_redirect_is_autouse` case that imports `agent.llm.compat`, resolves degraded with `proc="bridge"` carrying **no** per-file monkeypatch, and asserts the marker landed under the fixture's tmp dir and not under `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"`. That fails if the fixture is absent, misnamed, non-autouse, or erroring. | Replace the text-grep row with an execution row that can fail: `./scripts/pytest-clean.sh tests/unit/test_llm_stack_degraded_start.py -k "marker_redirect_is_autouse" -q`, backed by a case that imports `agent.llm.compat`, resolves degraded with `proc="bridge"` **without** any per-file monkeypatch, and asserts the marker landed under the test's tmp dir and **not** under `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"`. That fails if the fixture is absent, misnamed, non-autouse, or erroring — none of which the substring check detects. |
| NIT | History & Consistency (Archaeologist) | Task 2's final bullet defers to build time a question already answered and stable: "Grep the whole tracked tree ... if there are none outside these files, say so in the build report rather than assuming." Executed at the current tip, `git grep -n LOCAL_TYPED_HARD_TIMEOUT` returns exactly five hits, all inside the two files the task already names — `agent/llm/__init__.py:14` and `:26`, and `agent/llm/wrapper.py:167`, `:192` (a docstring reference), `:209` (the only functional reader). There are **zero** readers outside `agent/llm/`, and `agent/llm/` has been untouched by every commit since the reshape, so this cannot drift before the build starts. Leaving it open costs a discovery step and leaves a reviewer chasing a build-report line rather than confirming one. | **Fixed (round 9)** — adopted as prescribed, and re-executed at the current tip to confirm nothing drifted: `git grep -n LOCAL_TYPED_HARD_TIMEOUT` returns five code hits — `agent/llm/__init__.py:14`/`:26`, `agent/llm/wrapper.py:167`/`:192`/`:209` — all inside `agent/llm/`, with zero readers elsewhere in tracked code, plus the single prose hit at `docs/features/nonharness-llm-wrapper.md:56` that task 8 already owns. Both the task-2 bullet and the Test Impact row now state the executed result and recast the grep as a confirmation step whose non-empty result outside `agent/llm/` stops the task, rather than an open discovery deferred to the build report. | In task 2's bullet and the Test Impact row, replace the open instruction with the executed result: five hits, all inside `agent/llm/` — `__init__.py:14`/`:26` (the re-export and its `__all__` entry), `wrapper.py:167` (the module-scope read being migrated), `:192` (a docstring reference in `run_typed_local`; update the prose to the new field name), `:209` (the sole functional reader, becomes `settings.timeouts.local_typed_hard_s`). No readers exist outside the package; re-run the grep to confirm nothing arrived and treat a non-empty result as a premise change that stops the task. |

Structural checks all pass. Required sections present and substantive (Documentation carries `docs/features/llm-stack-compat-gate.md` as a checkbox path; Update System addresses `scripts/update/` and records that no Popoto migration applies; Agent Integration addresses the runtime surface and the absence of an MCP entry; Test Impact lists UPDATE/ADD dispositions). Task numbering 1-9 is contiguous, every `Depends On` resolves to a declared Task ID, the graph is acyclic, and every task carries a `Validates`. Every cited file path exists except the six intentionally-new ones. **Freshness baseline re-checked and still valid:** `origin/main` is `b90e99892`, twelve commits past the stated `93fb790ef`, and `git log --name-only 93fb790ef..origin/main` shows every one touching only `docs/plans/*.md`, so no tracked source file has moved and the baseline is not a finding this round. Re-verified by execution at the current tip: `agent/llm/wrapper.py:44-50` still imports the full third-party stack at module scope (`anthropic` at `:44`, `OpenAIChatModel` at `:48`); `scripts.scan_module_scope_env.find_module_scope_env_calls` still returns exactly one `EnvCall` for that file, at `:167`, so the ratchet Verification row is correctly red on `main`; `run.py:2636` is the sole `verify_environment` call and sits inside `if config.do_verify:`; `run.py:2673-2684` is literally the `valor_tools` loop with `if not tool.available and tool.error:` and a non-gated branch that logs `WARN` and calls `_append_warning`, with `human_gated_tools` at `:2672` being `{"google-token", "sms_reader", "env-completeness"}` — so the "do not add `llm-stack-compat` to `human_gated_tools`" instruction lands on the right set; `docs/features/nonharness-llm-wrapper.md:56` still documents the knob as "env-overridable, default 20s"; `.claude/skills/update/SKILL.md:70` is the sole line matching the `! grep -q "import check"` anti-row. The round-8 blocker and concern fixes were re-read and are correctly carried into Solution, tasks 3-4, Test Impact, Success Criteria, and the Verification table; the splat-only gate, the override marker clear, and the split CLI-purity criterion are all present and internally consistent. The one exception is the autouse-fixture mechanism, which is this round's blocker.

### Round-8 war room (2026-09-02) — FULL roster, verdict NEEDS REVISION

**Dispatch deviation, recorded (unchanged from round 7):** the critique agent had no
Agent/Task spawn tool in its context, so the three FULL lenses were executed inline by a
single agent against the real files and the live venv rather than by three parallel
sub-agents. Result files were still written to the run-dir barrier and the
`critique-roster-check --plan-path` membership + grounding gate passed 3/3. Every finding
below was verified by execution at `93fb790ef`, not inferred from a bundle. Attribution
names the lens, not a separate agent.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness (Skeptic) | The four-step derivation fails **open**, not closed, when the `create` call site forwards by splat instead of literal keywords. Executed on the pinned pair: the AST walk finds exactly one `self.client.beta.messages.create` site carrying 24 literal kwarg names, no `**` splat, and step 4 yields `missing: []` → `compatible: True`. But step 4's test is "every forwarded name is a parameter of the signature" — on a site refactored to `self.client.beta.messages.create(**kwargs)` the forwarded set is empty and that test is **vacuously true**, returning `compatible=True` against any stack including the known-bad pair. One site, resolvable path, `getsource` available: none of the four enumerated fail-closed cases fires. The plan's own break-glass rationale says the predicate "introspects two third-party internals upstream is free to rename — including in Step 2's lane, which moves `pydantic-ai-slim` 25 minor versions", and collapsing an argument list into a splat is a routine refactor at that distance. The only compensating control — the shape assertions that the derived set contains `temperature`/`top_p`/`top_k` — lives solely in `tests/unit/test_llm_stack_compat.py`, so it covers the CI/dev path and **not** Data Flow route 3: a follower machine that pulls a bad pin runs `/update` verify and bridge/worker startup, which call the predicate and not the suite, and boots green on a broken stack. That is the exact route this lane exists to close, defeated by the lane's own predicate. | **Fixed (round 8)** — adopted as prescribed. The derivation is now five steps, with a new step 4 that collects the site's literal keywords and returns `compatible=False` naming the splat-entry count when the set is empty — placed before the subset test and explicitly distinct from the `len(sites) != 1` gate. Solution's failure enumeration goes from four cases to five, task 3 carries the gate as its own bullet with the reason the shape assertions cannot compensate (suite-only, absent from `/update` verify and service startup), Test Impact adds `test_splat_only_call_site_fails_closed` on a synthetic splat-only module source, and there is a Success Criterion plus a Verification row. | Add the empty-forwarded-set case as a fifth explicit fail-closed gate. After collecting the single site's keywords, before the subset test: `forwarded = [k.arg for k in site.keywords if k.arg]` then `if not forwarded: return CompatResult(compatible=False, loader_ok=True, reason=f"self.client.*.create call site in pydantic_ai.models.anthropic forwards no literal keywords (splat-only: {sum(1 for k in site.keywords if k.arg is None)} ** entries) — derivation cannot verify the signature", exc_type=None)`. This is distinct from the `len(sites) != 1` gate: the count is 1 and the path resolves, so that gate does not fire. Extend the Solution bullet's failure list from four cases to five, add a Success Criterion, and add `test_splat_only_call_site_fails_closed` in `tests/unit/test_llm_stack_compat.py` fed a synthetic module source whose sole `create` call is `self.client.beta.messages.create(**kwargs)` — the same synthetic-source mechanism the two-site case already uses. Do **not** rely on the `temperature`/`top_p`/`top_k` shape assertions for this: they are suite-only and never run at `/update` verify or at bridge/worker startup on a follower machine. |
| CONCERN | Risk & Robustness (Operator) | The break-glass override strands a permanent red marker, in precisely the scenario it exists for. `_resolve_degraded_flag()` reads `LLM_STACK_COMPAT_OVERRIDE` and `"healthy"` short-circuits to not-degraded — short-circuiting **before** the predicate is evaluated, therefore before the "healthy resolution clears the marker" leg the plan makes load-bearing ("The marker must be cleared on a healthy resolution — a stuck-red dashboard equals no dashboard channel"). Sequence: a false positive degrades the fleet, the bridge writes `data/llm-stack-degraded.bridge`, the operator sets the override and restarts. The new process resolves not-degraded by override and never touches the marker; every subsequent resolution short-circuits the same way, so no future healthy resolution can ever clear it. The board stays red forever on a machine the operator has explicitly declared healthy, and the only recovery is a human `rm`. The plan names this failure mode and gives the clear leg its own test, but the override path bypasses that test's subject entirely. | **Fixed (round 8)** — adopted as prescribed. The override branch now runs `if proc: _marker_path(proc).unlink(missing_ok=True)` before returning, recorded in the Solution break-glass bullet (with the code block and the strand-forever sequence), task 4, Test Impact, a Success Criterion, and a Verification row (`-k override_clears_marker`). The test writes a bridge marker by a degraded resolution, resets the memo, sets the override, resolves again with `proc="bridge"`, and asserts the marker is gone and the OVERRIDDEN warning fired. | The override branch must run the same clear the healthy branch runs, gated on `proc` exactly as the write is: `if os.environ.get("LLM_STACK_COMPAT_OVERRIDE") == "healthy": logger.warning(SENTINEL + " OVERRIDDEN"); if proc: _marker_path(proc).unlink(missing_ok=True); return False`. `missing_ok=True` keeps it a no-op for a process that never wrote one, and the `if proc:` guard preserves the round-7 rule that only `bridge` and `worker` touch marker paths. Add a case to `tests/unit/test_llm_stack_degraded_start.py`: resolve degraded with `proc="bridge"` (marker written), reset the memo, set `LLM_STACK_COMPAT_OVERRIDE=healthy`, resolve again with `proc="bridge"`, assert the marker is gone and the OVERRIDDEN warning was emitted — under the `_MARKER_DIR` redirect, not the live `data/`. |
| CONCERN | Risk & Robustness (Adversary) | Round 7's marker-writer restriction silently made the marker half of the CLI-purity guard **unfalsifiable**. Task 4 now specifies that `_resolve_degraded_flag(proc: str \| None = None)` "writes a marker **only** when `proc` is given", and the `--json` CLI is a one-shot that passes no `proc`. So a CLI that is fully **impure** — one routing through `_resolve_degraded_flag()` instead of the pure predicate — still writes no marker, and the assertion "creates no `data/llm-stack-degraded` marker" passes either way. The prescribed subprocess form makes it worse: from a parent process you cannot count `capture_message` calls inside a child, so in the subprocess variant the *only* checkable assertion is the vacuous one. Task 7 leg (a) carries the identical assertion with the identical parenthetical justification and is vacuous for the same reason. This is the round-7 blocker's own named defect class — "a test that cannot fail is worse than no test, because it is counted as coverage" — landing on the purity property the round-4 concern was raised to protect. | **Fixed (round 8)** — adopted as prescribed, all three parts. The purity criterion is split: in-process, the CLI's JSON entry is called directly with a counting `capture_message` stub and asserts zero calls **and** `compat._DEGRADED is None`; the subprocess run is retained but proves only the out-of-process CLI contract (well-formed JSON with `compatible`/`loader_ok`/both versions, matching exit status). The "creates no marker" clause is deleted from the Success Criterion, from task 3, from Test Impact, and from task 7 leg (a), with the reason recorded (no caller passes `proc`, so it holds either way under the round-7 writer rule). The Verification row is reworded to name the discriminating assertion. | Split the criterion. (1) In-process: call the CLI's JSON entry directly with `capture_message` monkeypatched to a counting stub and assert the count is 0 **and** that `compat`'s memoized flag global is still unresolved (e.g. `compat._DEGRADED is None`) after the call — this is the assertion that discriminates pure from impure, and it is unavailable in a subprocess. (2) Keep the subprocess run but change what it proves: the child's stdout is well-formed JSON carrying `compatible`/`loader_ok`/both versions and its exit status matches, i.e. the CLI contract works out-of-process at all. (3) Delete the "creates no marker" clause from the Success Criterion and from task 7 leg (a), or restate it honestly as "no marker is written because no caller passes `proc`" — a property of the round-7 writer rule, not evidence of CLI purity. |
| CONCERN | Scope & Value | The suite-pollution guard is specified as a hand-replicated per-file convention, which is the shape this plan's own central argument rejects. `agent/llm/` gets an *enforcement mechanism* — one architectural test — precisely because "That single test is the invariant; consumer discipline is not". The marker seam gets the opposite: three named test files must each remember to `monkeypatch.setattr(compat, "_MARKER_DIR", tmp_path)` and each must additionally carry its own live-`data/` glob-unchanged assertion, with nothing enforcing either. The gap is already visible in this plan: `tests/unit/test_llm_stack_compat.py` drives degraded resolutions (loader-ImportError cases, simulated-bad-signature cases, and a subprocess CLI run) but is **not** one of the three files required to redirect the seam or carry the glob assertion — so the one file whose subprocess cannot see a parent monkeypatch at all is also the one with no live-`data/` guard. | **Fixed (round 8)** — adopted as prescribed. The redirect moves into `tests/conftest.py` as an `autouse=True`, function-scoped fixture (`monkeypatch.setattr("agent.llm.compat._MARKER_DIR", tmp_path, raising=False)`), which also covers `tests/unit/test_llm_stack_compat.py` — the file the per-file convention had missed. The per-file monkeypatch instruction and every per-file glob-unchanged assertion are deleted from task 4 and the Test Impact rows; the default-path case remains and now resolves `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"` directly rather than reading the patched global. The subprocess CLI case receives the redirect through the child's environment via a lazily-read override inside `_marker_path()`. New Test Impact row for `tests/conftest.py`, a reworded Success Criterion, and a Verification row asserting the fixture exists. | Add to `tests/conftest.py` an `autouse=True`, function-scoped fixture doing `monkeypatch.setattr("agent.llm.compat._MARKER_DIR", tmp_path, raising=False)` — `raising=False` so it is inert until `compat.py` exists and for any test that never imports it. Then delete the per-file glob-unchanged instruction from task 4 and the three Test Impact rows, and keep the single case asserting the *default* — which must not read the patched global: resolve `Path(agent.llm.compat.__file__).resolve().parents[2] / "data"` directly and compare to `Path(ui.app.__file__).parent.parent / "data"`. An in-process fixture cannot help the subprocess CLI case in `test_llm_stack_compat.py`; pass the redirect explicitly to the child, which requires `_marker_path()` to read an override **lazily inside the function** (a module-scope `os.environ.get` is blocked by `validate_no_module_scope_env.py`). |
| NIT | History & Consistency (Archaeologist) | Both grep-derived counts the plan cites as evidence are wrong, and one omits a materially interesting site. There are **six** module-scope consumers of `agent.llm`, not five: `scripts/nightly_regression_tests.py:97` does `from agent.llm.wrapper import run_typed` at column 0, missed because the plan's grep matched only `from agent.llm import`. That sixth site is the nightly regression detector — the launchd job that surfaced the 2026-08-24 incident — so under today's module-scope imports an ImportError kills the thing that is supposed to notice, which strengthens the plan's argument rather than weakening it. Separately, "31 test files already import `bridge.telegram_bridge`" is not reproducible: 6 files import it at module scope, 21 reference it in any import form. The xdist cache-hit argument holds at any count above zero, so the number is decorative and only creates a maintenance liability. | **Fixed (round 8)** — both counts corrected, re-grepped at the baseline. Problem and the Freshness table now read **six** module-scope consumers including `scripts/nightly_regression_tests.py:97`, with the point the finding makes (an ImportError kills the nightly detector that is supposed to notice) carried in the Problem bullet, and the Freshness row records the corrected grep that catches the submodule import form. The "31 existing test files" count is replaced by "existing test files" in both the Solution import-safety bullet and task 2 — the xdist cache-hit argument holds at any count above zero. | Reword Problem to "`agent.llm` has module-scope consumers in `bridge/routing.py:21`, `bridge/job_router.py:46`, `bridge/agent_catchup.py:59`, `agent/memory_extraction.py:34`, `tools/email_cs/triage.py:21`, and `scripts/nightly_regression_tests.py:97` (the nightly detector itself)" and update the Freshness table row to match. In the Solution import-safety bullet and task 2, replace "31 existing test files already import `bridge.telegram_bridge`" with "existing test files already import `bridge.telegram_bridge`" — the argument is that *any* prior import in the same xdist worker makes both statements cache hits, and it does not depend on a count. Reproducing greps: `grep -rnE '^(from agent\.llm(\.[a-z_]+)? import\|import agent\.llm)' --include='*.py' agent bridge tools scripts worker ui models config utils monitoring` and `grep -rlE '^(from bridge\.telegram_bridge import\|import bridge\.telegram_bridge)' tests/ \| wc -l`. |
| NIT | History & Consistency (Consistency Auditor) | The stated Freshness baseline `ce54eb3d6` is two commits behind `main`'s tip. `main` is now at `93fb790ef` (this plan's own round-7 revision), with `5b35d5212` between. Independently re-checked with `git log --name-only ce54eb3d6..93fb790ef`: both intervening commits touch only `docs/plans/*.md`, so every premise in the Freshness table carries forward unchanged and this is a labelling defect, not a stale-claim defect. Same class as the round-5 baseline nit that round 6 fixed by committing to a single stated baseline — the discipline needs re-applying each round, not only once. | **Fixed (round 8)** — the Freshness header now reads `93fb790ef` (`main`'s tip at round 8) as the single baseline, with the same sentence pattern: every commit between it and the tip at this revision touches only `docs/plans/*.md` (verified by `git log --name-only`), so no tracked source file has moved. `ce54eb3d6` is not restated alongside it. One further body site carrying a stale inline SHA (`run.py:1514` at `b87fb26de`) is reworded to "at the baseline commit", so the one-baseline-per-round discipline holds across the whole document, not just the header. | Change the header to "**Baseline commit:** `93fb790ef` — `main`'s tip at round 8 (2026-09-02)" and keep the existing sentence pattern: every commit between `ce54eb3d6` and `93fb790ef` touches only `docs/plans/*.md` (verified by `git log --name-only`), so no tracked source file has moved. Do not restate `ce54eb3d6` alongside it — one baseline per round, per the round-6 fix. |

Structural checks all pass. Required sections present and substantive (Documentation carries `docs/features/llm-stack-compat-gate.md` as a checkbox path; Update System addresses `scripts/update/` and records that no Popoto migration applies; Agent Integration addresses the runtime surface and the absence of an MCP entry; Test Impact lists UPDATE/ADD dispositions). Task numbering 1-9 is contiguous, every `Depends On` resolves to a declared Task ID, the graph is acyclic, and every task carries a `Validates`. Every cited file path exists except the six intentionally-new ones. Every `file:line` reference in the Freshness table was re-verified by execution at `93fb790ef` and **all hold exactly**: `wrapper.py:44-50`, `anthropic_client.py:44`, `telegram_bridge.py:55`/`:80`/`:130`/`:1203`, `deps.py:250`/`:277`/`:329`/`:391`/`:414`/`:463` (`AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]`), `run.py:1358`/`:1485`/`:1491`/`:1493`/`:1514`/`:1907-1909`/`:2636`/`:2673-2684` (the `valor_tools` loop's non-gated branch at `:2683-2684` logs and calls `_append_warning`; `:2647` is the *system_tools* loop's identical guard, a near-miss a builder should not confuse), `verify.py:1225-1228`, `ui/app.py:373`/`:376`/`:511`/`:907`, `pyproject.toml:12`, `agent/llm/__init__.py:14`/`:26`, `docs/features/nonharness-llm-wrapper.md:56`. `scripts.scan_module_scope_env.find_module_scope_env_calls` returns exactly one `EnvCall` for `agent/llm/wrapper.py`, at `:167`, so that Verification row is correctly red on main. The `! grep -q "import check" .claude/skills/update/SKILL.md` anti-row targets exactly one line, `SKILL.md:70`, inside `### Auto-Bump Critical Dependencies`. The round-6/7 predicate algorithm was re-executed end to end: one `self.client.beta.messages.create` site carrying exactly the 24 literal kwargs enumerated, attribute path `client.beta.messages` resolving off `anthropic.AsyncAnthropic(api_key="x")`, `missing == []`, `compatible: True` on `anthropic 0.125.0` + `pydantic-ai-slim 2.9.0`; measured cost 0.553s total of which 0.526s is the stack import and 26ms the `getsource` + `ast.parse` + signature work, so "sub-second, no network" holds. The round-7 subprocess shim mechanism was executed and reproduces its own red state: baseline `import agent.llm, bridge.telegram_bridge` succeeds, and with a raise-on-import shim dir on `PYTHONPATH` it fails at `agent/llm/wrapper.py:44`. The degraded-intake acceptance property was checked against production code rather than assumed: `bridge/routing.py::classify_work_request` wraps `_classify_work_request_llm` in `except Exception` and defaults to `ClassificationType.QUESTION` (`:1052-1060`), so an `LLMStackIncompatible` on the intake path does not drop the message.

### Round-7 war room (2026-09-02) — FULL roster, verdict NEEDS REVISION (all findings addressed)

**Dispatch deviation, recorded:** the critique agent had no Agent/Task spawn tool in
its context, so the three FULL lenses (Risk & Robustness, Scope & Value, History &
Consistency) were executed inline by a single agent against the real files rather
than by three parallel sub-agents writing result files. Every finding below was
verified by execution against the live venv and tracked tree at `3714bd96f`, not
inferred from a bundle. Attribution names the lens, not a separate agent.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness (Skeptic) | The import-safety contract test — the plan's single named enforcement mechanism ("That single test is the invariant; consumer discipline is not") — is specified in a way that passes unconditionally in a full-suite run. `tests/unit/test_llm_import_safety.py` is prescribed as "with `anthropic`/`pydantic_ai` stubbed to raise on import, `import agent.llm` and `import bridge.telegram_bridge` succeed", with no isolation prescription anywhere in Solution, Test Impact, task 2, or the Verification row. Thirty-one test files already import `bridge.telegram_bridge` (grep-verified), and its module scope pulls `bridge.routing` which pulls `agent.llm`; under xdist, file distribution is nondeterministic, so in the run that matters those modules are already in `sys.modules` and both `import` statements are cache hits that execute no stack import at all. The existing in-process precedent makes the trap concrete rather than theoretical: `tests/unit/test_bridge_api_id_parse.py:112` pops only `bridge.telegram_bridge` from `sys.modules` and reimports, which for this contract would leave a cached `bridge.routing` and `agent.llm` short-circuiting the very chain under test. This is the round-6 pass's own named defect class — "a test that cannot fail is worse than no test, because it is counted as coverage" — landing on the invariant the whole reshape exists to enforce. | **Fixed (round 7)** — adopted as prescribed. The Solution import-safety bullet, task 2, Test Impact, Success Criteria, and a new Verification row now require the import half to run in a **fresh interpreter subprocess** with a raise-on-import shim dir on `PYTHONPATH`, and explicitly reject both the in-process form and the `sys.modules`-purge variant (the transitive-closure argument and the `test_bridge_api_id_parse.py:112` precedent are recorded in the plan body). The `run_typed`-raises and alert-channel halves stay in-process. | Run the import half in a **fresh interpreter subprocess**, never in-process. Write a stub shim dir into `tmp_path` containing `anthropic.py` and `pydantic_ai/__init__.py` whose bodies are `raise ImportError("stubbed")`, then assert `subprocess.run([sys.executable, "-c", "import agent.llm, bridge.telegram_bridge"], cwd=<repo root>, env={**os.environ, "PYTHONPATH": f"{shim}:{repo}"}, capture_output=True).returncode == 0`, with the stderr in the assertion message. Do **not** attempt a `sys.modules`-purge variant: purging `bridge.telegram_bridge` alone is insufficient, and the correct purge set is the full transitive closure (`bridge.telegram_bridge`, `bridge.routing`, `bridge.job_router`, `bridge.agent_catchup`, `agent.llm`, `agent.llm.wrapper`, `agent.llm.compat`, `agent.anthropic_client`, `agent.memory_extraction`, `tools.email_cs.triage`) whose completeness nothing enforces. The `run_typed`-raises and alert-channel halves can stay in-process; only the import half needs the subprocess. |
| CONCERN | Risk & Robustness (Operator) | The per-process marker scheme strands permanent red markers, and the plan asserts the opposite. Failure Posture says `proc` is "a pid-suffixed name for one-shot callers, **so a crashed one-shot cannot strand a permanent red**" — but nothing in the plan delivers that property. A one-shot process that resolves degraded writes `data/llm-stack-degraded.{pid}` and then exits; the clear leg only ever runs on a *healthy* resolution inside the same process, so the marker outlives it forever. The read side is specified as `sorted((repo/"data").glob("llm-stack-degraded*"))`, red while **any** marker exists, with no liveness filter. On a genuinely degraded machine every one-shot script, cron helper, and pytest process that touches `run_typed` deposits another permanent marker; after the pin fix lands the bridge and worker clear theirs and the board stays red on the corpses. That is precisely the plan's own named "stuck-red dashboard equals no dashboard channel" mode, reached through the mechanism introduced to prevent its inverse. | **Fixed (round 7)** — adopted as prescribed, first option. `_resolve_degraded_flag(proc: str \| None = None)` writes a marker **only** when `proc` is given; `bridge` and `worker` are the only two callers that pass one, and every other caller gets Sentry + the sentinel log and writes nothing. Pid-suffixed markers are gone, and the sentence claiming they prevent stranded red is deleted rather than restated. Recorded in Failure Posture (the per-process subsection and the channel table row), task 4, Test Impact, a Success Criterion, and a Verification row that greps `getpid` **out** of `compat.py`. | Restrict marker writing to processes that declare a stable, clearable `proc` name. Give the resolver an explicit opt-in (e.g. `_resolve_degraded_flag(proc: str \| None = None)`); `bridge/telegram_bridge.py::main` passes `"bridge"`, `worker/__main__.py` passes `"worker"`, and every other caller passes nothing and writes **no** marker — it still gets the Sentry capture and the `logger.critical` sentinel, which are the one-shot-appropriate channels. Then delete the pid-suffix sentence from Failure Posture rather than restating it. If pid-suffixed markers are kept instead, the read side must filter by liveness (`os.kill(pid, 0)` in a `try/except (OSError, ProcessLookupError)`) and that filter needs its own row in `tests/unit/test_dashboard_llm_degraded.py`. |
| CONCERN | Risk & Robustness (Adversary) | The marker path is deliberately un-redirectable, so the lane's own tests write into the live `data/` directory that the running bridge, worker, and dashboard share. Task 4 pins the write to `Path(__file__).resolve().parents[2] / "data" / f"llm-stack-degraded.{proc}"` — "never cwd-relative" — which is right for production and leaves tests no seam: `monkeypatch.chdir(tmp_path)` cannot move it. Three new test files (`test_llm_import_safety.py`, `test_llm_stack_degraded_start.py`, `test_dashboard_llm_degraded.py`) each drive degraded resolutions, so on the development machine they write real markers into the real `data/` while a real bridge and worker are running against the same checkout, and parallel xdist workers race on the same filenames. A test that fails or is interrupted before its cleanup leaves the operator's actual dashboard red with no underlying fault — the failure this plan is built to make legible, manufactured by its own suite. | **Fixed (round 7)** — adopted as prescribed. `_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"` at `compat.py` module scope, all writes and clears through `_marker_path(proc)`; production default unchanged and still un-redirectable by cwd, tests monkeypatch the seam to `tmp_path`. Each of the three new test files asserts the live `data/llm-stack-degraded*` glob is unchanged across the test, and one case asserts the **default** `_MARKER_DIR` equals the directory `ui/app.py` reads. Recorded in Failure Posture, task 4, all three Test Impact rows, two Success Criteria, and the amended marker Verification row. | Keep the module-resolved path as the **default**, not the only option: `_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"` at module scope in `agent/llm/compat.py`, with every write and clear going through a `_marker_path(proc)` helper that reads it. Tests then do `monkeypatch.setattr(compat, "_MARKER_DIR", tmp_path)`. The existing Success Criterion still holds unchanged — assert the *default* `_MARKER_DIR` equals `Path(ui.app.__file__).parent.parent / "data"`. Add one assertion to each of the three new files that `list((repo/"data").glob("llm-stack-degraded*"))` is unchanged across the test, so a future test that forgets the monkeypatch fails loudly instead of polluting the operator's board. |
| CONCERN | Risk & Robustness (Skeptic) | The fail-closed enumeration for introspection failures is explicit about three cases and silent about the one that Step 2's lane makes likely. Solution names "`getsource` unavailable, zero `create` sites found, the attribute path unresolvable on the client" as `compatible=False` paths, but says nothing about **more than one** `create` site. Verified on the pinned pair: `ast` over `inspect.getsource(pydantic_ai.models.anthropic)` yields exactly one `self.client.beta.messages.create` call carrying exactly the 24 literal kwargs the plan lists, and resolving `beta.messages` off `anthropic.AsyncAnthropic(api_key="x")` gives `missing == []`, so the round-6 algorithm is confirmed correct **today**. But the plan's own break-glass rationale states the predicate "introspects two third-party internals upstream is free to rename — including in Step 2's lane, which moves `pydantic-ai-slim` 25 minor versions", and a beta/non-beta branch split is the most natural shape that change takes. Falling through to `sites[0]` there is the silently-passing-predicate-whose-target-moved failure the plan explicitly rejects, and reaching it via an unenumerated branch means no test names it. | **Fixed (round 7)** — adopted as prescribed. The derivation is now four steps, with step 2 an explicit `len(sites) != 1` fail-closed gate returning the count and the found paths verbatim — never a fall-through to `sites[0]`. The Solution failure enumeration is extended from three cases to four, task 3 carries the gate as its own bullet, Test Impact adds the synthetic two-site case beside the zero-site case, and there is a Success Criterion and a Verification row. | Make the count an explicit gate, not an implicit index: after collecting sites, `if len(sites) != 1: return CompatResult(compatible=False, reason=f"expected exactly 1 self.client.*.create call site in pydantic_ai.models.anthropic, found {len(sites)}: {[s.path for s in sites]}", exc_type=None)`. Add the multi-site case beside the existing zero-site case in `tests/unit/test_llm_stack_compat.py`'s introspection-failure row (feed a synthetic module source with two `create` calls), and extend the Solution bullet's failure list from three cases to four. |
| CONCERN | History & Consistency (Consistency Auditor) | The print-on-pass prescription cites a model that is structurally the wrong shape, in the exact leg a round-4 blocker already caught once. Technical Approach and task 6 say to "add a dedicated call-site block in `run.py` modeled on `run.py:1907-1909`" — but that block reads `result.projects_json_check.detail`, a **dedicated `UpdateResult` dataclass field** (`run.py:152`) populated by its own run.py call site at `run.py:1904`. The compat `ToolCheck` is created inside `verify.verify_environment` and appended to `result.valor_tools` (`verify.py:1225-1228`); run.py only ever sees it as an element of `result.verification.valor_tools`, which it obtains once at `run.py:2636`. A builder following the cited model literally either adds an `UpdateResult` field that verify.py never sets, or adds a second `verify` call site and runs the compat subprocess twice per update. | **Fixed (round 7)** — adopted as prescribed. Technical Approach and task 6 now specify a **lookup by `name` in `result.verification.valor_tools`** placed immediately after `run.py:2636` and before the warning loop at `run.py:2673`, with the three-line form written out, and state explicitly that there is **no new `UpdateResult` field and no second `verify` call site**. The `projects_json_check` block is cited as the *style* (report every run, pass or fail) and named as the wrong *plumbing* to copy, with the reason (it reads a dedicated `UpdateResult` field `run.py` itself populates). Update System's `run.py` bullet is reworded to match. Verified in the tree: `run.py:2636` is the sole `verify_environment` call and sits inside `if config.do_verify:`. | No new `UpdateResult` field and no second call site. Immediately after `result.verification = verify.verify_environment(...)` (`run.py:2636`, inside `if config.do_verify:` — true in all three `UpdateConfig` presets, so the leg is genuinely unconditional), do `compat = next((t for t in result.verification.valor_tools if t.name == "llm-stack-compat"), None)` and, when it is not None, `log(f"  llm-stack-compat: {compat.detail}", v, always=True)` before the existing `valor_tools` warning loop at `run.py:2673`. Reword Technical Approach and task 6 to say "a lookup by `name` in `result.verification.valor_tools`, logged unconditionally, in the same style as the `projects_json_check` detail block" rather than "modeled on `run.py:1907-1909`". |
| NIT | History & Consistency (Archaeologist) | Step 2 of the derivation resolves the target callable against `anthropic.AsyncAnthropic(api_key="x")`, but `pydantic_ai.models.anthropic` types `self.client` as `AsyncAnthropicClient` — a union that also admits `AsyncAnthropicBedrock`, `AsyncAnthropicBedrockMantle`, and `AsyncAnthropicVertex` (imported at that module's lines 106-109, grouped at `_NON_AUTOMATIC_CACHING_CLIENTS` and the tuple below it). The choice is correct for this repo, because `wrapper.py` constructs `AsyncAnthropic` directly, but the plan states the resolution rule as if the class were implied by the call site, which it is not — the call site names only the attribute *path*, not the client class. A future reader debugging a Bedrock-shaped false positive has nothing on the record. | **Fixed (round 7)** — adopted as prescribed. The derivation's client-resolution step now carries the clause: the call site names only the attribute *path*, `pydantic_ai` types `self.client` as the `AsyncAnthropicClient` union (Bedrock / BedrockMantle / Vertex included), and `AsyncAnthropic` is the right target **because it is the class `agent/llm/wrapper.py` constructs** — this repo uses none of the others. Task 3 requires the same clause in `compat.py`'s docstring beside the "read the target from the call site" note. | One clause in the Solution bullet: resolve the path against `anthropic.AsyncAnthropic` **because that is the class `agent/llm/wrapper.py` constructs**; `pydantic_ai` types `self.client` as the `AsyncAnthropicClient` union (Bedrock / BedrockMantle / Vertex included) and this repo uses none of the others. Mirror the clause in `agent/llm/compat.py`'s docstring beside the existing "read the target from the call site" note. |

Structural checks all pass. Required sections present and substantive (Documentation carries `docs/features/llm-stack-compat-gate.md` as a checkbox path; Update System addresses `scripts/update/` and records that no Popoto migration applies; Agent Integration addresses the runtime surface and the absence of an MCP entry; Test Impact lists UPDATE/ADD dispositions). Task numbering 1-9 is contiguous, every `Depends On` resolves to a declared Task ID, the graph is acyclic, and every task carries a `Validates`. Every cited file path exists except the six intentionally-new ones (`agent/llm/compat.py`, `docs/features/llm-stack-compat-gate.md`, and the four new test files). All four Prerequisite check commands were executed and pass. The round-6 blocker fix was independently re-derived by execution on the live venv: the AST walk over `inspect.getsource(pydantic_ai.models.anthropic)` returns exactly one `self.client.beta.messages.create` site carrying the 24 literal kwargs the plan enumerates, and resolving `beta.messages` off `anthropic.AsyncAnthropic(api_key="x")` yields `missing == []` on `anthropic 0.125.0` + `pydantic-ai-slim 2.9.0`, so the corrected predicate does return `compatible=True` on the pinned pair. The round-5 verify-leg prescriptions re-verified: `ToolCheck` carries `error` and `detail` (`verify.py:35-44`), `run.py:2647` and `run.py:2674` are both literally `if not tool.available and tool.error:`, the non-gated branch at `run.py:2683-2684` logs and calls `_append_warning`, and `bridge/update.py::extract_update_warnings` parses those bullets. `do_verify=True` in `UpdateConfig.full`, `.cron`, and `.verify_only`, so "unconditional on every `/update`" holds. `agent/llm/wrapper.py:44-50` still imports the full third-party stack at module scope, `agent/anthropic_client.py:44` still imports `anthropic` at module scope, `wrapper.py:167` is still the single `EnvCall` `scripts/scan_module_scope_env.find_module_scope_env_calls` reports for that file, and the `LOCAL_TYPED_HARD_TIMEOUT` re-export is at `agent/llm/__init__.py:14` and `:26` exactly as claimed, with two further in-module readers at `wrapper.py:192` and `:209` and the doc paragraph at `docs/features/nonharness-llm-wrapper.md:56`. `AUTO_BUMP_PACKAGES = ["claude-agent-sdk"]` at `deps.py:329`; `get_pinned_version`/`verify_critical_versions`/`bump_pin_in_pyproject`/`run_smoke_test`/`auto_bump_deps` at `:250`/`:277`/`:391`/`:414`/`:463`. `_sentry_before_send` at `telegram_bridge.py:55` wired at `:80`; `main()` at `:1203`; `bridge.routing` imported at module scope well above it. `ui/app.py:376` reads `Path(__file__).parent.parent / "data" / "last_connected"` and `dashboard_json` is at `:907`. The `! grep -q "import check" .claude/skills/update/SKILL.md` anti-row targets exactly one line, `SKILL.md:70`, inside the `### Auto-Bump Critical Dependencies` block the plan corrects. The Freshness baseline `63e6c2299` is six commits behind `origin/main` (`3714bd96f`) and all six touch only `docs/plans/*.md` (`git log --name-only`), so every premise carries forward unchanged and the baseline is not restated as a finding.

### Round-5 war room (2026-09-02) — FULL roster, verdict NEEDS REVISION

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | The prescribed introspection derivation returns `compatible=False` on the currently pinned, verified-working pair. Executed against the live venv (anthropic 0.125.0 + pydantic-ai-slim 2.9.0), `AnthropicModelSettings.__annotations__` has 31 keys, `inspect.signature(AsyncMessages.create).parameters` has 24, and **20 annotation keys are absent** from `create` with no `**kwargs` to absorb them (`anthropic_betas`, `anthropic_cache`, `anthropic_thinking`, `frequency_penalty`, `logit_bias`, `parallel_tool_calls`, `presence_penalty`, `seed`, and 12 more). `AnthropicModelSettings` is a declared *superset* of provider-agnostic and anthropic-namespaced settings that `AnthropicModel` translates or drops; it is not the set of kwargs pydantic-ai forwards to `create()`. Followed literally, this lane merges and every bridge and worker boots degraded, all three alert channels fire fleet-wide, `run_typed` raises `LLMStackIncompatible` at every non-harness call site, and `/update` verify warns on all four machines — Risk 1's named false negative, realized by the plan's own algorithm. The plan's guard does not catch it: the "fallback tuple ⊆ introspected keys" test passes trivially because `{temperature, top_p, top_k}` *is* a subset of the 31 keys. | **Fixed (round 6)** — the blocker was reproduced (20 missing keys confirmed on the live venv) and the corrected algorithm was **prototyped end to end during the revision**, returning `compatible=True` on the pinned pair. The Solution bullet now derives the kwarg set from `pydantic_ai.models.anthropic`'s `.create(` call site by `ast` over `inspect.getsource`, and resolves the target callable from that call's own attribute path. A second defect the finding did not name was found and fixed: 2.9.0 calls `self.client.beta.messages.create`, not `anthropic.resources.messages.AsyncMessages.create` — the non-beta class is missing `betas`, `context_management`, `mcp_servers`, `speed`, so the prescribed target would have produced four false positives even with the right kwargs. The trivially-passing subset test is replaced by the positive self-test plus shape assertions; task 3 and Test Impact require the self-test to be written first. | Red-state proof is one command: `.venv/bin/python -c "import inspect,anthropic;from pydantic_ai.models.anthropic import AnthropicModelSettings as S;p=set(inspect.signature(anthropic.resources.messages.AsyncMessages.create).parameters);print(sorted(set(S.__annotations__)-p))"` prints 20 keys today on the pair the anti-criteria pin. Derive from what pydantic-ai **forwards**, not what it declares: intersect the annotation keys with the kwargs literally passed at the `.create(` call in `pydantic_ai.models.anthropic.AnthropicModel` (reachable via `inspect.getsource`), or invert — start from the module-level literal tuple and use introspection only to confirm each member is still a declared setting. Add the inverse regression test beside the subset test: `assert check_llm_stack_compat().compatible is True` on the pinned pair, and assert the derived candidate set contains no `anthropic_`-prefixed key. |
| CONCERN | Risk & Robustness (component also flagged by Scope & Value) | One `data/llm-stack-degraded` marker is shared by every process that resolves the flag, with no writer ownership, so one process's healthy clear erases another's live degraded state. After a pin fix lands, the bridge takes the graceful restart (`agent/agent_session_queue.py::_check_restart_flag`), re-resolves healthy and clears the marker — while the worker, whose restart `_check_restart_flag` **defers whenever jobs are running** (`tests/integration/test_remote_update.py::test_check_restart_flag_defers_when_jobs_running`), still holds its memoized degraded flag and keeps raising `LLMStackIncompatible`. The dashboard renders green against a still-broken worker: the plan's own "stuck-red dashboard equals no dashboard channel" failure with the polarity flipped, and worse, because a false green is not investigated. No Race Condition covers a multi-writer marker. | **Fixed (round 6)** — adopted as prescribed. Per-process `data/llm-stack-degraded.{proc}`; each resolver clears only its own path (`missing_ok=True`, never a glob); `ui/app.py` globs and is red while any marker survives, naming the degraded processes. New Failure Posture subsection "Why the dashboard marker is per-process", new **Race 4** (designed away, with the benign stale-red residual named), plus task 4, Test Impact, Success Criteria, and a Verification row. | Write `data/llm-stack-degraded.{proc}` where `proc` is `bridge` / `worker` / a pid-suffixed name for one-shot callers; the resolver clears **only its own** path (`marker.unlink(missing_ok=True)`), never a glob. `ui/app.py::dashboard_json` reads `sorted((repo/"data").glob("llm-stack-degraded*"))` and renders red while any marker exists, naming the degraded processes. Test that clearing the bridge marker leaves the worker marker and the red state intact. |
| CONCERN | Risk & Robustness | No break-glass override for a false-positive degradation. The predicate is deliberately fail-closed on introspection failure, and it introspects two third-party internals (`AnthropicModelSettings.__annotations__`, `AsyncMessages.create`) upstream is free to rename — including in the very next lane, which moves `pydantic-ai-slim` 25 minor versions. When that fires, every non-harness LLM call on all four machines raises until a code revert is authored, merged, pulled and each service restarted. Risk 1's "one revert away" understates recovery: a revert is a full SDLC round trip, not an operator action, and Emergency recovery is empty. | **Fixed (round 6)** — adopted as prescribed. `LLM_STACK_COMPAT_OVERRIDE=healthy` read lazily **inside** `_resolve_degraded_flag()`, short-circuiting after a sentinel OVERRIDDEN warning; not honoured by the pure predicate, the `--json` CLI, or the auto-bump gate. New Solution bullet, task-4 bullet, Test Impact case, Success Criterion, Verification row. Update System records it as an env read deliberately **not** declared in `.env.example` (declaring it would make `check_env_completeness` require an operator-only break-glass var on every machine). | In `_resolve_degraded_flag()`, before evaluating the predicate: `if os.environ.get("LLM_STACK_COMPAT_OVERRIDE") == "healthy": logger.warning(<sentinel> " OVERRIDDEN"); return False`. Read it lazily **inside** the function — a module-scope `os.environ.get` in a new `agent/llm/compat.py` is blocked by `validate_no_module_scope_env.py`. Do not wire the override into `check_llm_stack_compat()` (must stay pure per the round-5 split), and do not honour it in the `--json` CLI or the auto-bump `llm` gate: an override must never let a bad pin pass the bump gate. Document as break-glass only. |
| CONCERN | Scope & Value (component also flagged by Risk & Robustness) | The dashboard channel — the one the plan singles out as its only *standing* signal and defends at most length — ships with no test and no Verification row on the read side. Test Impact names five test files, none touching `ui/`. Success Criteria assert only that the marker is written, cleared, and path-equal to what `ui/app.py` reads; nothing asserts `dashboard_json` surfaces the state. The Verification table's 24 rows include none under `ui/`. The lane can go fully green with `dashboard_json` never wired, or wired and raising into its own fail-quiet `except OSError`. This is the identical defect class the round-4 blocker caught on the verify leg: a channel present but silent. | **Fixed (round 6)** — adopted as prescribed. `tests/unit/test_dashboard_llm_degraded.py` added to Test Impact (marker present → truthy degraded field with both versions and the process name; absent → healthy; two markers → both named; unreadable → 200 and no raise, exercising the `except OSError` leg), with a Verification row and a Success Criterion that names `/dashboard.json` rendering rather than marker path equality. Task 4's dashboard bullet now requires the test file by name. | Add Test Impact row `tests/unit/test_dashboard_llm_degraded.py` — ADD: with the marker written (versions + `exc_type`), `dashboard_json()` returns a truthy degraded field carrying both versions; with the marker absent it returns the healthy value; with the marker present but unreadable the call still returns 200 and does not raise, proving the `except OSError` leg. Add Verification row `./scripts/pytest-clean.sh tests/unit/test_dashboard_llm_degraded.py -q` → exit 0, and a Success Criterion naming `/dashboard.json` rendering rather than marker path equality alone. |
| CONCERN | Scope & Value | A single boolean gates two unrelated fault domains, so an Anthropic-only signature break needlessly kills the local granite path. `run_typed_local` constructs `OpenAIChatModel` against `OllamaProvider` and talks to localhost Ollama (`agent/llm/wrapper.py:213`); it never touches `anthropic`. The signature check is entirely about `AsyncMessages.create`, yet the plan has `run_typed_local` raise `LLMStackIncompatible` on the same flag. The two hot-path classifiers routing through it (intake intent in `tools/classifier.py`, Job bind-or-mint in `bridge/job_router.py`) would fall back to conservative defaults (`new_work` / NEW Job) fleet-wide for a fault that provably does not affect them. Spike-5 already distinguishes the domains — ImportError breaks local, a signature break does not — but the design collapses them. | **Fixed (round 6)** — adopted as prescribed. `CompatResult` carries `loader_ok` beside `compatible` and `_resolve_degraded_flag()` memoizes both; `run_typed` raises on `not loader_ok or not compatible`, `run_typed_local` **only** on `not loader_ok`. New Solution bullet, task-3 and task-4 bullets, the stubbed-`FunctionModel` test in Test Impact, a Success Criterion, and a Verification row. The alert still fires on either axis and names which. | Add `loader_ok: bool` alongside `compatible: bool` on `CompatResult` (loader success is the import-class axis; `compatible` stays the anthropic-signature axis); `_resolve_degraded_flag()` memoizes both. `run_typed` raises when `not loader_ok or not compatible`; `run_typed_local` raises **only** when `not loader_ok`. Test: loader succeeding and signature check failing → `run_typed` raises, `run_typed_local` completes against a stubbed FunctionModel. |
| CONCERN | History & Consistency | Update System asserts "**No new config files or env keys.**" while task 2 requires migrating `LOCAL_TYPED_HARD_TIMEOUT` onto a `TimeoutSettings` field — which by that group's contract creates a new `TIMEOUTS__*` key and retires the documented one. Two consequences the plan does not carry: `docs/features/nonharness-llm-wrapper.md:56` documents the knob by name as "env-overridable, default 20s" and is **not** in Existing Docs to Correct for that change; and `agent/llm/__init__.py` re-exports `LOCAL_TYPED_HARD_TIMEOUT` at lines 14 and 26 (it is in `__all__`), a public attribute the task-2 "update every reader" bullet never names and that Test Impact's grep instruction scopes to `tests/` only. Followed literally the builder leaves a dangling `__all__` entry and a doc paragraph describing an env var that no longer exists. | **Fixed (round 6)** — adopted as prescribed. Update System now reads "**No new config files.** One new `TIMEOUTS__` field" and spells out `local_typed_hard_s` / `TIMEOUTS__LOCAL_TYPED_HARD_S`, read inside `run_typed_local`. Task 2 gains an explicit bullet deleting the `agent/llm/__init__.py` re-export (lines 14, 26, `__all__`) and orders a whole-tracked-tree grep, not a `tests/`-scoped one; `docs/features/nonharness-llm-wrapper.md` is in Existing Docs to Correct with the env-key rename called out at line 56; Test Impact adds `tests/unit/test_settings.py`; a Verification row asserts the old name is gone from the package surface and the new field resolves. | Add `local_typed_hard_s: float = Field(default=20.0, ge=1.0, le=300.0, ...)` to `TimeoutSettings` (env `TIMEOUTS__LOCAL_TYPED_HARD_S`). Read it **inside** `run_typed_local` (`settings.timeouts.local_typed_hard_s`), not at module scope — the module-scope form defeats the migration's purpose. Delete `LOCAL_TYPED_HARD_TIMEOUT` from `agent/llm/__init__.py` lines 14 and 26. Add `docs/features/nonharness-llm-wrapper.md` to Existing Docs to Correct with the env-key rename, and change Update System to "one new `TIMEOUTS__` field; no new config files." |
| CONCERN | History & Consistency | The plan's central thesis is that the bad pin arrives by three routes so the check cannot live on one, yet route 2 — the hand-staged commit, with **two demonstrated occurrences** (`9d1488ccb`'s re-application swept into `d0c02bde5`, reverted by `7a30b88f7`) — is still detected only *after* the pin is committed and pushed. Every remedy is a run boundary: the next `/update` verify or the next service start, both after `origin/main` already carries the bad pin and followers have pulled it. The plan cites `2b926acae`'s `validate_no_module_scope_env.py` as "the repo's established shape for an AST rule enforced at commit time, diff-scoped", applying that lesson to the *contract* but never to the *pin* — the thing with the recurrence record. No No-Go or Rabbit Hole excludes a commit-time leg, so its absence reads as oversight rather than decision. | **Declined (round 6), with the residual named** — taking the finding's own stated fallback. The proposed `pre-push` leg **would not have caught either occurrence**: the predicate reports on the *installed* stack, and at push time the venv reflects the last `uv sync`, not the pin being pushed. `d0c02bde5` swept in an already-staged bump that was not synced, so the hook would have introspected the good, still-installed pair and passed — adding a false all-clear on top of the bad pin. Making it sound requires a push-time `uv sync`: minutes of wall clock plus a mutation of the pusher's venv on every push, outside this appetite. New No-Go entry records the decision, the reasoning, the accepted residual (route 2 detected at the next run boundary, after `origin/main` carries the pin, though no follower can *boot* on it silently), and names the sound alternative — a **declaration-level** diff check against the `anthropic>=1.0.0 → pydantic-ai-slim>=2.33.0` boundary, needing no venv — as a follow-up candidate. | Cheapest sufficient form is a `.githooks/pre-push` leg (the repo already ships `.githooks/pre-push` for the hotfix-issue-disposition gate): if `git diff --name-only @{push}..HEAD` contains `pyproject.toml` **and** `git diff @{push}..HEAD -- pyproject.toml` touches a line matching `^\+.*"(anthropic\|pydantic-ai-slim)`, run `.venv/bin/python -m agent.llm.compat --json` and refuse the push on `"compatible": false`. Reuses the pure CLI, adds no new predicate, and must stay `--no-verify`-skippable so it cannot wedge an emergency revert. If declined, add an accepted-residual line to No-Gos naming route 2's post-push detection window. |
| NIT | Scope & Value | Two Verification rows are `grep -c` invocations whose Expected value is "match count == 0" (the `import anthropic; import claude_agent_sdk` row and the `import check` SKILL.md row). `grep -c` exits 1 when it matches nothing, so the passing state of both rows is a non-zero exit — the opposite convention from the other 22 rows, all of which expect exit 0. A runner keying on exit status marks the success case as a failure. | **Fixed (round 6)** — both rows are now `! grep -q ...` with Expected "exit code 0". The remaining `grep -c` rows all expect a match (exit 0 on success), so the whole table is exit-0-on-pass. | Replace with `! grep -q "import anthropic; import claude_agent_sdk" scripts/update/deps.py` and `! grep -q "import check" .claude/skills/update/SKILL.md`, Expected "exit code 0" — matching the `grep -q` form task 1's Validates field already uses. |
| NIT | History & Consistency | The Freshness Check names three different commits as the baseline for one set of claims: the prose says "**Baseline commit:** `b87fb26de`", the table below is headed "re-verified 2026-09-02 at `3b6eb651b`", and the round-4 disposition row says "re-verified at `5021a40aa`". All three exist, but `b87fb26de` and `5021a40aa` are this lane's own round-4 critique and revision doc commits and `3b6eb651b` belongs to a different lane; none is `main`'s tip (`b37d67d98`). Independently re-checked at `b37d67d98`: every claim in the table still holds, so this is a labelling defect, not a stale-claim defect. | **Fixed (round 6)** — the Freshness Check now states one baseline, `63e6c2299` (`main`'s tip at round 6), and the table header reads "re-verified at the baseline commit above". Confirmed by `git log --name-only b37d67d98..63e6c2299` that all six intervening commits touch only `docs/plans/*.md`, so the round-5 re-verification at `b37d67d98` carries forward unchanged. Earlier rounds' SHAs survive only in the historical change-log tables at the bottom, where they describe what a past pass did rather than asserting a current baseline. | State one baseline SHA in the section header and have the table header read "re-verified at the baseline commit above"; drop the second and third SHAs rather than reconciling them. |

Structural checks all pass. Required sections present and substantive (Documentation carries a `docs/features/` checkbox path; Update System addresses `scripts/update/` and records that no Popoto migration applies; Agent Integration addresses the runtime surface and the absence of an MCP entry; Test Impact lists dispositions). Task numbering 1-9 is contiguous with valid, acyclic `Depends On` references and every task carries a `Validates`. Every cited file path exists except the five intentionally-new ones (`agent/llm/compat.py`, `docs/features/llm-stack-compat-gate.md`, and the three new test files). All sixteen cited commit SHAs were confirmed to exist with the described content. Every `file:line` reference in the Freshness table plus the five round-5-corrected body sites was independently re-verified at `b37d67d98` and holds, including `run.py:2673-2684`'s `if not tool.available and tool.error:` guard, `run.py:1907-1909`'s `projects_json_check` detail block, `_sentry_before_send` at `telegram_bridge.py:55` wired at `:80`, `ui/app.py:376`/`:907`, and `wrapper.py:167`'s single `EnvCall`. All four Prerequisite check commands were executed and pass. The module-scope import closure of `bridge.telegram_bridge`, `worker.__main__`, and `agent.llm` was computed: outside `agent/llm/wrapper.py` and `agent/anthropic_client.py` there are **zero** module-scope `anthropic`/`pydantic_ai` imports on those graphs, so the import-safety contract's stated file scope is sufficient for the contract test.

### Round-4 war room (2026-09-02) — FULL roster, verdict NEEDS REVISION

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | The prescribed `verify.py` call signature `ToolCheck(name=..., available=result["compatible"], detail=result["reason"])` produces a **silent** verify leg. `run.py`'s `valor_tools` loop warns only under `if not tool.available and tool.error:`, so with `error` unset an incompatible stack emits no log line, no `result.warnings` entry, and nothing for `extract_update_warnings` to surface. The "(printed even on pass, #2541)" claim is also wrong: `detail` is rendered by one bespoke block for `projects_json_check` only; the generic loop never reads it. Followed literally, the build ships a dead check at the one call site covering Data Flow routes 2 and 3. | **Fixed (round 5)** — Technical Approach verify.py bullet rewritten with the full `ToolCheck(..., error=...)` shape and the `run.py:2673-2684` guard quoted; the false '(printed even on pass)' claim replaced by a separate print-on-pass block modeled on `run.py:1907-1909`; Update System, task 6, Test Impact, Success Criteria, and a Verification row all updated to require a non-empty `.error` and an `extract_update_warnings` hit. | Return `ToolCheck(name="llm-stack-compat", available=res["compatible"], version=<both versions>, error=None if res["compatible"] else res["reason"], detail=<both versions>)` and append to `result.valor_tools` (verify.py:1225-1228). `run.py`'s guard is literally `if not tool.available and tool.error:` — `error` must be non-empty or the leg is silent. For print-on-pass, add a dedicated call-site block modeled on run.py:1907-1909. Extend `test_verify_runs_compat_check_without_bump` to assert a non-empty `.error` AND presence in `extract_update_warnings(status_lines)`. |
| CONCERN | Risk & Robustness | The plan never says whether `python -m agent.llm.compat --json` calls the pure predicate or the alerting resolver, and the two callers want opposite behavior. If the CLI resolves the degraded flag, every *successful* auto-bump rollback fires a `level="fatal"` Sentry capture and writes the standing `data/llm-stack-degraded` marker for a failure the gate just prevented — reaching the plan's own named "stuck-red dashboard equals no dashboard channel" mode via its happy path, and alarming production twice during task 7. | **Fixed (round 5)** — new Solution bullet: `check_llm_stack_compat` is pure (no flag, no `capture_message`, no marker), `_resolve_degraded_flag()` alone alerts, `--json` calls only the pure predicate. Tasks 3, 4, and 7 leg (a), Test Impact, Success Criteria, and a Verification row carry the purity assertions. | Two entry points, no shared state: `check_llm_stack_compat(allow_network=False) -> CompatResult` is pure (never touches the memoized flag, never calls `capture_message`, never writes the marker); `_resolve_degraded_flag()` alerts on first transition. The `--json` CLI calls only the former. Add a test asserting the CLI path emits zero `capture_message` calls and creates no marker file on an incompatible stack. |
| CONCERN | Scope & Value | The lane bundles two independently shippable, explicitly file-disjoint halves: (A) import-safety contract + predicate + degraded posture, and (B) the update-script coupled-set rewrite. The plan itself marks task 5 `**Parallel**: true (with tasks 2-4 — no shared files)` and argues in Rabbit Holes that co-landing a gate with what it gates "gives you no way to tell which half is at fault" — the same argument applies here. Half (A) alone closes routes 2 and 3; half (B), being held, changes no production behavior this lane. | **Rejected, with reason recorded (round 5)** — see the new Appetite subsection 'Why this lane is not split into two PRs': the halves are file-disjoint but not independent (task 6 and `verify.py` both invoke `agent.llm.compat`), the Rabbit Holes argument is about gate-vs-bump not gate-vs-caller, and splitting leaves the three spike-2 pin-helper defects on `main` another cycle. The appetite re-label was taken as the concession instead. | The split is latent in the task graph: `build-pin-helpers` depends only on `verify-phase0-pin`. Cut PR 1 at task 4; `build-coupled-sets` then re-acquires `agent/llm/compat.py` from merged `main` rather than a sibling branch. Nothing in tasks 5-6 touches a file tasks 2-4 touch. If the split is rejected, record the reason — the plan currently argues both sides. |
| CONCERN | History & Consistency | The round-4 revision corrected drifted addresses only inside the Freshness Check table and did not propagate them into the body. Five body sites still carry references the same document declares stale: `run.py:1302`/`run.py:1169` (Data Flow), `bridge/telegram_bridge.py:70-77` and `ui/app.py:906` (Failure Posture channels table), `run.py:1317` (Solution `hold` bullet), `run.py:1302-1345` (Race 1). | **Fixed (round 5)** — all five body sites re-verified at `b87fb26de` and rewritten to lead with symbols: Data Flow (`auto_bump_deps` call / `is_lockfile_maintainer`), Failure Posture channels table (`_sentry_before_send` `:55` wired at `:80`; `ui/app.py:907`), the `hold` bullet (`:1514`), and Race 1 (`run.py:1491-1534`). | Substitutions re-verified at `5021a40aa`: `run.py:1302` → `:1493`; `run.py:1169` → `:1358` (gate at `:1485`/`:1491`); `run.py:1317` → `:1514`; `telegram_bridge.py:70-77` → `:55` (wired at `:80`); `ui/app.py:906` → `:907`. Prefer symbol names over any of these, per the plan's own "locate every site by symbol, not by line." |
| CONCERN | History & Consistency | The Success Criterion "The predicate is exercised from all four sites: ... auto-bump `llm` phase" asserts coverage the design forecloses: the only set declaring `gates=("llm", ...)` carries `hold="#3001 Step 2"`, and `CoupledSet.gates` defaults exclude `llm`, so the phase is unreachable in production for this lane's whole life. It can only be satisfied by a test double and by task-7 transcripts that themselves stub resolution and clear the hold. | **Fixed (round 5)** — the criterion now names three production sites plus the `llm` phase as test-and-transcript coverage, states the hold makes production execution Step 2's, and requires the shared `llm_gate_argv(venv_python)` helper plus `test_llm_phase_argv_matches_gate_helper`. Task 6, task 7 leg (a), Technical Approach, Test Impact, and a Verification row all wire it. | Reword the criterion to name test + transcript coverage and record that first unattended production execution is Step 2's lane. Make task 7 leg (a) invoke the gate through the **same argv construction** `auto_bump_deps` uses — factor `[str(venv_python), "-m", "agent.llm.compat", "--json", "--allow-network"]` into one helper called by both the phase runner and the test, and add `test_llm_phase_argv_matches_gate_helper`. |
| NIT | Risk & Robustness | The degraded marker path is given only as the bare relative string `data/llm-stack-degraded`; the reader is pinned (`ui/app.py:376`) but the writer's resolution rule is unstated, so a cwd-relative or worktree-rooted write lands a marker the dashboard never reads. | **Fixed (round 5)** — the marker path is prescribed as `Path(__file__).resolve().parents[2] / "data" / "llm-stack-degraded"` in the channels table and task 4, with a Success Criterion and Verification row asserting equality with the path `ui/app.py:376` reads. | Resolve from the module: `Path(__file__).resolve().parents[2] / "data" / "llm-stack-degraded"` in `agent/llm/compat.py`, mirroring `ui/app.py:376`. Assert in the marker test that the written path equals the path `ui/app.py` reads. |
| NIT | History & Consistency | The auto-bump block in `.claude/skills/update/SKILL.md` is at lines 64-70, not the cited 66-72; the cited range spills into `### Critical Dependency Handling`. | **Fixed (round 5)** — Update System and Documentation now cite the heading `### Auto-Bump Critical Dependencies` instead of a line range. | Cite the section heading `### Auto-Bump Critical Dependencies` instead of a line range. |
| NIT | Scope & Value | Nine tasks, four agent roles, three new test files, ~13 new integration tests, a wholesale `wrapper.py` module-scope rewrite, a `TimeoutSettings` migration, and three interface replacements in `deps.py` is not Medium-shaped work. | **Adopted (round 5)** — appetite re-labelled Large in frontmatter and the Appetite section, with the split alternative explicitly rejected and reasoned above. | Re-label the appetite Large, or take the task-4/5 split, which makes each half genuinely Medium. |

Structural checks all pass: required sections present and substantive; task numbering 1-9 contiguous with valid, acyclic `Depends On` references; every cited file path exists except the four intentionally-new ones (`agent/llm/compat.py`, `docs/features/llm-stack-compat-gate.md`, and the three new test files); every cited commit SHA verified to exist with the described content; every `file:line` reference in the Freshness table re-verified as holding.

### Prior rounds

Round 3 is a **reshape**, not a wording revision. Round 2 (7 blockers, 6
concerns, 3 nits, verdict NEEDS REVISION) established that the plan's shape was
wrong: a startup gate inside `main()` structurally cannot observe the
ImportError failure class, because the bridge dies ~1070 lines earlier at
`from bridge.routing import ...`. Six of round 2's findings collapse into one
architectural decision — **`agent.llm` is import-safe by contract** — and the
remaining four keep their round-2 prescribed fixes verbatim.

### Round-2 finding dispositions

| Finding | Disposition in this reshape |
|---|---|
| B-a (bridge can never reach its gate on ImportError) | **Dissolved structurally.** The failure class no longer exists: `import agent.llm` cannot fail, so `main()` is always reached and the startup hook merely forces early flag resolution. Neither of B-a's two patch options (early guard / function-scope import in routing) was taken — both were consumer-side fixes covering one of five module-scope consumers, an invariant maintained by convention. The supplier-side contract has one enforcement test. |
| B-b (lazy import deletes the test seam) | **Addressed.** The loader is the new seam; `tests/unit/test_llm_wrapper_local.py` is a named UPDATE with the docstring fix and a no-real-network assertion. |
| B-c (option (a) was an escape hatch that auto-executes the openai major bump) | **Dissolved.** There is no option menu: the entire stack goes lazy, `OpenAIChatModel` included. |
| B-d (hibernation `before_send` silently drops the Sentry channel) | **Adopted as prescribed:** sentinel exemption in `_sentry_before_send`, with a test. |
| B-e (lazily-resolving flag degrades silently off the startup path) | **Adopted as prescribed, promoted to the design:** the alert is bound to flag resolution; startup hooks only force early resolution. Race 3 is designed away by the same move. |
| Deviation (utils/ placement: accepted, rationale false) | **Superseded by a new named deviation:** the predicate moves into `agent/llm/compat.py`, per the single-authoritative-liveness doctrine; this also eliminates the B-f cycle and avoids creating the first `utils/` → `agent/` edge. `verify.py`/`deps.py` reach it by subprocess only, so no import edge is created in either direction. |
| Dashboard channel has no cross-process transport | **Adopted as prescribed:** `data/llm-stack-degraded` marker written by the resolver, read fail-quiet by `ui/app.py`, with the clear-on-healthy leg and its own test. |
| Re-adding `anthropic` auto-executes Step 2 on the next cron tick | **Adopted as prescribed:** `CoupledSet.hold`, set on the LLM set, skip-with-legible-reason; explicitly not a version bound. Step 2's lane removes it. |
| B-f (utils ⇄ agent import cycle) | **Dissolved** by the compat module living inside `agent/llm/`. |
| Hardcoded-params concern | **Adopted as prescribed:** introspection-derived set, guarded, with the fallback-subset test. |
| verify.py in-process concern | **Adopted as prescribed:** subprocess `--json` → `ToolCheck`, versions in `detail`. |
| openai exact-pin concern | **Adopted (the drop):** the floor stays; the fixture keeps the floor-plus-comment shape so the regression test targets the real defect. |
| Task-6 bundling concern | **Adopted:** split into tasks 5 (helpers) and 6 (sets), with `verify_critical_versions` named as the helper rewrite's blast radius. |
| "Import-only gate is gone" row contradiction | **Fixed:** the import phase is set-derived (`import_names`); the hardcoded literal genuinely disappears, so Flow and the Verification row now agree. |
| NIT: five agents / twenty-two criteria | **Adopted:** validator role removed (the tester runs the Verification table); criteria consolidated. |
| NIT: `test_agent_llm_wrapper.py` does not exist | **Fixed:** Test Impact names the real files (`test_llm_wrapper.py`, `test_llm_wrapper_local.py`). |
| NIT: TCC hazard on the `~/Desktop/Valor/.env` fallback under launchd | **Carried as a caution** in spike-3's wording; the maintainer-machine `llm` phase reads `repo/.env` first via `utils/api_keys.py`. |

### Round-4 revision pass (2026-09-02) — freshness, not reshape

Round 3's reshape was never critiqued: the lane sat for seven days between the
reshape commit (`88f572d3a`, 2026-08-26) and this dispatch. The router routed
back to `/do-plan` under the revision guard with the round-2 verdict still the
latest recorded one, so this pass is **a re-verification of the reshaped plan's
premises against current `main`**, not a further reshape. No architectural
decision changed; the import-safety contract, the resolver-bound alert, the
coupled-set model, and the hold all stand exactly as round 3 left them.

What this pass changed:

| Change | Why |
|---|---|
| Freshness Check re-baselined to `3b6eb651b`, disposition Minor drift | Seven days and 30+ commits elapsed; the old baseline `d5d08615a` and its "no drift" claim were stale as claims about *today*. |
| `run.py:1304`/`:1169`/`:1317` corrected to `:1493`/`:1358`/`:1485`/`:1514`; `_sentry_before_send` `:70-77` → `:55`; `dashboard_json` `:906` → `:907` | `97672207d` rewrote ~594 lines of `run.py`; `b152b7d3c` shifted `telegram_bridge.py`. Every underlying claim survived; only addresses moved. Builders are now told explicitly to locate by symbol. |
| **New**: module-scope-env ratchet subsection + task-2 bullet + Technical Approach note + Test Impact row + Verification row | `2b926acae` (2026-09-01) restored a commit-blocking AST hook that a wholesale `wrapper.py` module-scope rewrite will trip via `LOCAL_TYPED_HARD_TIMEOUT` at `wrapper.py:167`. This is a genuine new build blocker that did not exist at reshape time; disposition is to migrate the read to `TimeoutSettings`, not to dodge the hook. |
| Commits-since-reshape subsection added | Records that `deps.py`, `agent/llm/`, `anthropic_client.py`, `ui/app.py`, and both named test files were untouched — the evidence for "no commit partially fixes this". |
| Sibling issues re-checked with current states; overlap re-checked | #3016 confirmed still open; #2807's plan confirmed to touch no shared file. |
| `last_comment_id` advanced `5420202999` → `5421299483` | The owner's design-decisions comment was already folded into Failure Posture at round 3 but never recorded in frontmatter. No unincorporated comment remains. |

Round-4 critique verdict: **NEEDS REVISION** (1 blocker, 4 concerns, 3 nits) —
recorded in the table at the top of this section.

### Round-5 revision pass (2026-09-02) — round-4 findings, no reshape

Every round-4 finding is dispositioned in the table above. No architectural
decision changed: the import-safety contract, the resolver-bound alert, the
coupled-set model, and the hold all stand as round 3 left them.

What this pass changed:

| Change | Why |
|---|---|
| `verify.py` prescription rewritten to route the failure through `ToolCheck.error`, plus a separate print-on-pass block in `run.py` | The round-4 blocker, re-verified at `b87fb26de`: `run.py:2673-2684` warns only under `if not tool.available and tool.error:`, and `ToolCheck.detail` is read by exactly one bespoke block (`run.py:1907-1909`) that valor_tools never reaches. The prescribed signature shipped a dead check at the one call site covering Data Flow routes 2 and 3. |
| Pure predicate / alerting resolver split made explicit, with purity tests | The `--json` CLI's callers include the auto-bump gate, which runs the predicate against a stack it is about to roll back. A resolving CLI would alarm production and leave a standing red marker on the gate's *success* path. |
| Five stale body references corrected, symbol-first | Round 4's revision fixed only the Freshness table. Re-verified at `b87fb26de`: `is_lockfile_maintainer` `:1358` (gate `:1485`/`:1491`), `auto_bump_deps` call `:1493`, `"Smoke test passed after bump"` `:1514`, `_sentry_before_send` `:55` (wired `:80`), `dashboard_json` `:907`. |
| The four-site Success Criterion reworded; `llm_gate_argv` helper introduced | The `llm` phase is unreachable in production while the LLM set is held, so the old criterion could only be satisfied by doubles. The helper makes the wiring itself assertable and pins task 7 leg (a) to the production argv. |
| Marker path pinned to a module-resolved absolute path | A cwd-relative write lands a marker `ui/app.py` never reads. |
| `SKILL.md` cited by heading | The cited `66-72` spilled into the next section; verified the block is `64-72` today and will drift again. |
| Appetite Medium → Large; the two-PR split explicitly rejected with reasons | Honest sizing. The split was rejected because the halves are file-disjoint but not independent, and deferring half (B) leaves the spike-2 pin-helper defects on `main`. |

### Round-6 revision pass (2026-09-02) — the compat derivation, no reshape

Every round-5 finding is dispositioned in the table above: seven fixed, one
declined with the residual named. No architectural decision changed — the
import-safety contract, the resolver-bound alert, the coupled-set model, and the
hold all stand as round 3 left them. Scope did not expand: nothing was added
beyond what the findings required.

| Change | Why |
|---|---|
| **The compat derivation is rewritten to read the `.create(` call site instead of `AnthropicModelSettings.__annotations__`** | The round-5 blocker, reproduced on the live venv: 20 of the settings TypedDict's 31 keys are absent from any `create` signature, so the prescribed predicate would have booted every bridge and worker degraded on the pinned, working pair. The corrected algorithm was prototyped end to end during this revision and returns `compatible=True` today. |
| A second, unnamed defect fixed alongside it: the target callable is resolved from the call site's own attribute path | pydantic-ai 2.9.0 calls `self.client.beta.messages.create`. The round-5 prescription named `anthropic.resources.messages.AsyncMessages` (non-beta), which is missing `betas`, `context_management`, `mcp_servers`, and `speed` — four false positives even with the correct kwarg set. Hardcoding the target is the same class of mistake as hardcoding the kwargs. |
| Positive self-test + shape assertions replace the subset test | `{temperature, top_p, top_k} ⊆ <31 keys>` passed against a predicate that would have degraded the fleet. A test that cannot fail is worse than no test, because it is counted as coverage. |
| `CompatResult` gains `loader_ok`; `run_typed_local` gated only on it | One boolean was gating two unrelated fault domains; an Anthropic signature break would have fallen both hot-path Ollama classifiers back to conservative defaults for a fault that cannot affect them. |
| Per-process dashboard markers, with ownership and Race 4 | A shared marker with no writer ownership fails **green**: the bridge's restart clears it while the worker, whose restart defers under load, is still degraded. A false green is worse than a stuck red because nobody investigates it. |
| Break-glass `LLM_STACK_COMPAT_OVERRIDE`, resolver-only | The predicate is fail-closed on introspection failure over two third-party internals that Step 2's lane moves 25 minor versions. Without an override, a false positive costs a full SDLC round trip on every machine. |
| Dashboard read side gets a test file, a Verification row, and a rendering criterion | The channel the plan defends at most length shipped with no read-side coverage — the round-4 blocker's defect class (present but silent) repeated on a different leg. |
| `TimeoutSettings` migration carried through the public surface and the docs | The migration retires a documented env knob and a name in `agent/llm/__init__.py`'s `__all__`; "no new env keys" was false and the `tests/`-scoped grep would have missed the re-export. |
| Route 2's commit-time leg declined, residual recorded in No-Gos | A pre-push predicate run reports on the *installed* stack, which at push time is not the stack the pin declares — it would have passed `d0c02bde5` and added a false all-clear. The sound form (a venv-free declaration check against the compat boundary) is named as a follow-up. |
| Two `grep -c` rows → `! grep -q`; one baseline SHA in Freshness Check | The two nits. The whole Verification table is now exit-0-on-pass. |
| `last_comment_id` advanced `5421299483` → `5505317383`; No-Gos re-slugged to #3073 / #3074 / #3075; task 9's `Refs`-vs-`Closes` note reworded | Phase 2.7 sync. The owner filed the three deferred chunks as their own issues on 2026-09-02 and corrected the record on Work Item 2 (its headline fix landed in `56e80d843`; #3074 owns only the residue). This lane's scope is unchanged and its Work-Item-2 anti-criterion still holds, now owned by #3074. |

### Round-8 revision pass (2026-09-02) — one fail-closed gate, three guard fixes

All six round-8 findings are dispositioned in the round-8 table above: six
fixed, none declined. No architectural decision changed and no scope was added
beyond what the findings required — the import-safety contract, the
resolver-bound alert, the two-axis split, the coupled-set model, and the hold
all stand.

| Change | Why |
|---|---|
| **A fifth fail-closed gate: a `create` site forwarding no literal keywords returns `compatible=False`** | The round-8 blocker. With one splat-only site the count gate does not fire, the path resolves, `getsource` works, and the subset test is *vacuously true* — the predicate returns `compatible=True` against the known-bad pair. The only compensating control lived in the test suite, which never runs at `/update` verify or service startup, so Data Flow route 3 — the follower machine this lane exists to protect — was defeated by the lane's own predicate. |
| The override branch clears the caller's own marker | The break-glass short-circuit runs *before* the predicate, so it can never reach the healthy branch's clear. Setting the override on a falsely-degraded machine stranded a permanent red board recoverable only by a human `rm` — the plan's own "stuck-red equals no dashboard channel" mode, reached through the escape hatch. |
| The CLI-purity criterion split: in-process memo assertion, subprocess contract-only, marker clause deleted | Round 7's writer restriction (`proc`-gated marker writes) made "the CLI creates no marker" hold whether the CLI is pure or not, and a parent cannot count `capture_message` calls inside a child. The purity property was being asserted by a test that could not fail — the defect class the round-7 blocker named, landed on the property round 4 raised the concern to protect. |
| The `_MARKER_DIR` redirect becomes an autouse `tests/unit/conftest.py` fixture | A hand-replicated per-file convention is the shape this plan rejects for `agent/llm/`, and the gap was already real: `test_llm_stack_compat.py` drives degraded resolutions and was not one of the files carrying the guard. One fixture covers every present and future test; the per-file glob assertions are deleted as the redundant half. |
| The fixture uses a **guarded module import**, not `monkeypatch.setattr("agent.llm.compat._MARKER_DIR", ...)` | `raising=False` suppresses only the attribute check; the string form's `derive_importpath` imports the module first and raises, which errors every test in the repo while `compat.py` is absent and force-imports `compat` for every test once it exists. Verified by execution on `main` (round-9 blocker). The `try/except ImportError: return` is what delivers inertness. |
| The fixture takes `tmp_path_factory` and lives under `tests/unit/`, not root `tests/` | An unconditional `tmp_path` bills ~14,216 test functions for an isolation four files need — on the order of 28,000 unread temp dirs per full run, retained three sessions. `mktemp` on the success branch plus `tests/unit/` scoping keeps the mechanism property while charging only the suite that uses it. |
| The marker-dir override is **named** `LLM_STACK_MARKER_DIR` and announces itself | An unnamed, silent override that relocates the write path of the plan's only *standing* signal turns a stale inherited value into a false-green board. Naming it gives `test_env_declaration_readers.py` a determinate expectation; the sentinel warning gives it the same log visibility as `LLM_STACK_COMPAT_OVERRIDE`. |
| `loader_ok` stays stack-wide; the loader is **not** split into per-domain legs | Splitting is the honest upgrade but is out of scope for a plan already re-labelled Large. Today's module-scope `anthropic` import already fells `run_typed_local` on ImportError, so keeping one `_load_stack()` is a no-regression residual (Risk 6) — pinned by an explicit test rather than left implicit. |
| Consumer count corrected to six; the "31 test files" count removed | `scripts/nightly_regression_tests.py:97` imports `agent.llm.wrapper` at module scope — the nightly detector itself, so today an ImportError kills the job that is supposed to notice. The xdist cache-hit argument holds at any count above zero, so the test-file number was decorative and a maintenance liability. |
| Freshness baseline restated as `93fb790ef`; one stale inline SHA reworded | One baseline per round, applied to the whole document rather than only the header. Both intervening commits touch only `docs/plans/*.md`, so every premise carries forward unchanged — a labelling defect, not a stale-claim defect. |
