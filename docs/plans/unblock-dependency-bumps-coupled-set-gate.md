---
status: Ready
type: bug
appetite: Medium
owner: valor
created: 2026-08-26
revision_applied: true
revision_applied_at: 2026-09-02T05:55:00Z
tracking: https://github.com/tomcounsell/ai/issues/3001
last_comment_id: 5421299483
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
  module scope too. `agent.llm` has **five module-scope consumers**
  (`bridge/routing.py:21`, `bridge/job_router.py:46`, `bridge/agent_catchup.py:59`,
  `agent/memory_extraction.py:34`, `tools/email_cs/triage.py:21`).
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

**Baseline commit:** `3b6eb651b` (re-verified 2026-09-02, seven days after the
round-3 reshape; the prior baseline was `d5d08615a`).
**Issue filed at:** 2026-08-25T05:16:36Z
**Disposition:** **Minor drift.** Every substantive premise holds: the
`agent/llm/` module-scope imports, the five module-scope consumers,
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

### File:line references re-verified 2026-09-02 at `3b6eb651b`

| Reference | Claim | Status |
|---|---|---|
| `agent/llm/wrapper.py:44-50` | full third-party stack imported at module scope | **Holds** |
| `agent/anthropic_client.py:44` | `import anthropic` at module scope, imported by wrapper | **Holds** |
| module-scope consumers of `agent.llm` | routing:21, job_router:46, agent_catchup:59, memory_extraction:34, email_cs/triage:21 | **Holds** (grep-verified; six more import function-scope) |
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
- **#3001** — still OPEN, as required: Work Items 2 and 3 keep it open past this lane's merge.

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

1. **Auto-bump route** — `run.py:1302` Step 3.5 (maintainer-only, `run.py:1169`)
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
| Sentry | `sentry_sdk.capture_message(<static body>, level="fatal")`, following `agent/index_drift.py:224` including its capture-failed fallback log. | `sentry-sdk`'s own HTTP transport; shares no code with the LLM stack. **Hibernation exemption required:** `bridge/telegram_bridge.py:70-77`'s `_sentry_before_send` drops every event while `is_hibernating()` — a persistent flag, not a brief window. The hook must pass events whose message carries the degraded sentinel token. Hibernation means "we cannot reach Telegram", which is exactly when a broken LLM stack most needs to be visible elsewhere. |
| Logs | `logger.critical` with a fixed, greppable sentinel token. | stdlib only. Survives a Sentry DSN outage. |
| Dashboard | The resolver writes a marker file `data/llm-stack-degraded` (versions + `exc_type`); `dashboard_json` (`ui/app.py:906`) reads it with the siblings' fail-quiet `try/except OSError` and renders red. | `/dashboard.json` is served by a **separate uvicorn process** — an in-process flag can never reach it. Every existing health field derives from a filesystem/Redis artifact (`_get_bridge_health` stats `data/last_connected`, `ui/app.py:373`); this follows that pattern. **The marker must be cleared on a healthy resolution** — a stuck-red dashboard equals no dashboard channel; the clear leg gets its own test. |

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

**Size:** Medium

**Team:** Solo dev pair (runtime + update-scripts), test engineer, documentarian

**Interactions:**
- PM check-ins: 1 (only if alert-channel judgment is needed)
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

- **Import-safety contract on `agent/llm/`.** All third-party stack imports
  (`anthropic`, `pydantic_ai.*`) move out of module scope in `wrapper.py` and
  `agent/anthropic_client.py` into one memoized loader (`_load_stack()`),
  invoked by `run_typed` / `run_typed_local` / the predicate. Module scope keeps
  stdlib and our own code only. Enforced by an architectural test: with
  `anthropic` and `pydantic_ai` stubbed to raise on import,
  `import agent.llm` **and** `import bridge.telegram_bridge` both succeed,
  `run_typed` raises `LLMStackIncompatible`, and all three alert channels fire.
  That single test is the invariant; consumer discipline is not.
- **`check_llm_stack_compat()` in `agent/llm/compat.py`**, returning
  `CompatResult` (`compatible`, `anthropic_version`, `pydantic_ai_version`,
  `reason`, `exc_type`). Local mode: run the loader (catches the ImportError
  class), then verify the installed `anthropic` message-create signature
  accepts what the installed `pydantic_ai` passes — **derived by
  introspection**, not hardcoded: bind
  `pydantic_ai.models.anthropic.AnthropicModelSettings.__annotations__` keys
  against `inspect.signature(anthropic.resources.messages.AsyncMessages.create).parameters`,
  flagging any key absent and not absorbed by `**kwargs`. Introspection
  failures (an internals rename) return `compatible=False` — a silently-passing
  predicate whose target moved is the same failure as no gate. A module-level
  literal fallback tuple carries the pydantic-ai version in a comment plus a
  test asserting it is a subset of the introspected keys.
  `allow_network=True` additionally makes one minimal `run_typed` call (catches
  the transport class). `python -m agent.llm.compat --json` is the subprocess
  entry point; no imports from `scripts/`.
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
  `BumpResult(bumped=False, error=f"held: {set.hold}")` so `run.py:1317`'s log
  stays legible. This is how `anthropic` returns to the auto-bump *structure*
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
- **`verify.py` uses the same subprocess shape.** `check_python_import`
  (`verify.py:104`) and `check_venv_tool` (`verify.py:125`) already build
  `{venv}/bin/python` and shell out via `run_cmd`. Run the `--json` entry with
  `check=False`, map onto
  `ToolCheck(name=..., available=result["compatible"], detail=result["reason"])`,
  carrying both resolved versions in `detail` (printed even on pass, #2541) so
  a silently stale venv is visible. Unconditional — every `/update`, bump or no
  bump.
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
  `TimeoutSettings` is a task-2 deliverable, not an incidental cleanup.
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
- [ ] `tests/unit/test_llm_stack_compat.py` — ADD (new file): compatible on the
  good pair; incompatible with verbatim reason on a simulated bad signature;
  incompatible on loader ImportError; introspection-failure → incompatible;
  fallback tuple ⊆ introspected keys; local mode makes no network call.
- [ ] `tests/unit/test_llm_stack_degraded_start.py` — ADD (new file): alert
  fires **while `run_typed` raises** (independence proof); raising
  `capture_message` suppresses nothing else; marker file written on degraded,
  cleared on healthy; hibernation `before_send` passes the sentinel event while
  dropping others; process does not exit; degraded intake (inbound message
  still enqueues an AgentSession).
- [ ] `tests/integration/test_remote_update.py::TestAutoBumpDeps::test_no_bump_when_already_latest`
  — UPDATE: rewrite against `AUTO_BUMP_SETS`; keep the nothing-bumps assertion.
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
  `test_llm_gate_failure_rolls_back_set`, `test_unrelated_set_survives_failed_set`,
  `test_gate_unavailable_is_fail_closed`, `test_restore_failure_blocks_commit`,
  `test_worktree_clean_after_every_rollback_path`,
  `test_verify_critical_versions_unchanged_by_helper_rewrite`,
  `test_verify_runs_compat_check_without_bump`.
- [ ] Any test asserting on `wrapper.LOCAL_TYPED_HARD_TIMEOUT` — UPDATE: the
  constant becomes a lazily-read `TimeoutSettings` field (see task 2 and the
  Freshness Check's module-scope-env ratchet subsection). Grep for the name
  across `tests/` before editing; if there are no readers, state that in the
  build report rather than assuming.
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
CI so a false negative fails the suite before shipping.

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

## Race Conditions

### Race 1: Concurrent `/update` runs on the maintainer machine
**Location:** `run.py:1302-1345`, `deps.py::auto_bump_deps`
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

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3001] **The dependency upgrade itself** — `pydantic-ai-slim`
  → 2.33.0+, `anthropic` → 1.x, `openai` 2.x → 3.x, and exercising the modules
  that import `openai` directly. Step 2, behind this gate, in its own lane;
  removing the LLM set's `hold` is that lane's final act.
- [SEPARATE-SLUG #3001] **Work Item 2 — the dead `worker_key` regression guard**
  (`69dc69568`/#2949). File-disjoint from this lane; implementation gotchas are
  in the issue comments.
- [SEPARATE-SLUG #3001] **Work Item 3 — duplicate/noisy triage filing.**
- [SEPARATE-SLUG #3016] **The `test_promise_gate_real_api` failure.**
- [SEPARATE-SLUG #3001] **Auditing remaining `CRITICAL — pin exact` deps for
  staleness** — belongs with Step 2, where findings can be acted on.
- **An exact `openai` pin.** Dropped at round-2's recommendation: scope creep, a
  resolution-semantics change deferred to Step 2, and it masked its own
  regression test's target. The floor stays.
- **A paging alert channel.** Risk 4 names this as accepted residual.
- **Extending the compat predicate to other subsystems.**

## Update System

This work **is** an update-system change.

- `agent/llm/compat.py` — **new**, the predicate + degraded flag + alert; shared
  by runtime services (in-process) and update scripts (subprocess only).
- `scripts/update/verify.py` — **new call site**: subprocess `--json` run mapped
  to a `ToolCheck`, unconditional on every `/update` and `/update --cron`.
- `scripts/update/deps.py` — the substantive change: `CoupledSet` (+`hold`),
  declaration-aware pin helpers, per-set gate phases (set-derived imports),
  per-set rollback with `restore_failed`, the `llm` phase subprocess.
- `scripts/update/run.py` — minimal: surface the failed phase in the rolled-back
  warning; guard the commit branch with `not bump.restore_failed`. The
  commit/push/restart ordering per `sdlc-1091.md` is untouched.
- `.claude/skills/update/SKILL.md` lines 66-72 — the auto-bump description
  ("anthropic and claude-agent-sdk... import check + pytest") becomes wrong on
  both counts; correct it and note the new verify-time check.
- `ui/app.py` — reads `data/llm-stack-degraded` into `/dashboard.json`.
- **No new config files or env keys.** `ANTHROPIC_API_KEY` and `SENTRY_DSN` are
  already declared.
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
  contract and its single enforcement test; the predicate and its four call
  sites; the resolver-bound degraded/alert contract and why not fail-closed;
  the three channels, the hibernation exemption, the marker-file clear leg, and
  the named drafter-convention exception; the coupled-set model, the
  `pydantic-ai-slim>=2.33.0` boundary, the `hold` and who removes it; why
  `openai` is in no set; what the gate checks and cannot check.
- [ ] Add a row to `docs/features/README.md`.

### Existing Docs to Correct
- [ ] `.claude/skills/update/SKILL.md` lines 66-72 (see Update System).
- [ ] `pyproject.toml` — the two-line comment above the `anthropic` pin.
- [ ] `docs/features/remote-update.md` — cross-reference.
- [ ] `docs/features/nonharness-llm-wrapper.md` — the import-safety contract,
  the lazy loader and its test seam, `LLMStackIncompatible`, the degraded flag.

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
  — one test proves all four.
- [ ] `check_llm_stack_compat()` exists in `agent/llm/compat.py`, importing
  nothing from `scripts/`, with `python -m agent.llm.compat --json` working.
- [ ] The predicate is exercised from all four sites: `/update` verify
  (unconditional, subprocess), bridge startup, worker startup, auto-bump `llm`
  phase (subprocess, target venv).
- [ ] On an incompatible stack, bridge and worker **start**, and an inbound
  Telegram message still enqueues an AgentSession.
- [ ] **The alert fires in a test where `run_typed` raises** (independence
  proof) and fires on the no-startup-hook path (resolver-bound proof).
- [ ] A raising `capture_message` suppresses nothing else; the hibernation
  `before_send` passes the sentinel event; `data/llm-stack-degraded` is written
  on degraded and **cleared on healthy** resolution.
- [ ] `LLMStackIncompatible` is a `LLMCallError` subclass — asserted.
- [ ] The signature check derives its parameter set by introspection, with the
  fallback-tuple-subset test.
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
- Write the contract test's import half now (broken-stack stubs →
  `import agent.llm` and `import bridge.telegram_bridge` succeed); the
  alert/typed-exception half lands with task 4.
- **Migrate `LOCAL_TYPED_HARD_TIMEOUT` (`wrapper.py:167`) to a lazily-read
  `config/settings.py` `TimeoutSettings` field in this same commit.** The
  module-scope-env ratchet (`validate_no_module_scope_env.py`, restored
  `2b926acae`) blocks any commit that adds *or modifies* a module-scope
  `os.environ.get` line, and this rewrite necessarily moves that line. Do not
  attempt to preserve line 167 byte-identically to dodge the hook — migrate it,
  per the pattern the guard's own docstring prescribes. Update every reader of
  the constant.

### 3. The compat predicate
- **Task ID**: `build-compat-predicate`
- **Depends On**: `build-import-safety`
- **Validates**: `tests/unit/test_llm_stack_compat.py`
- **Informed By**: spike-1, spike-3; round-2 introspection concern
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Create `agent/llm/compat.py`: `CompatResult`,
  `check_llm_stack_compat(allow_network: bool = False)`. Local mode: run the
  loader, then the introspection-derived signature check (with the guarded
  fallback tuple + subset test). `allow_network=True` adds one minimal
  `run_typed` call.
- Every failure path returns `compatible=False` with verbatim `reason` and
  `exc_type`. No bare `except Exception: pass`.
- Add the `python -m agent.llm.compat --json` entry point. Import nothing from
  `scripts/`; reach the stack only through the loader, inside function bodies.

### 4. Degraded flag, resolver-bound alert, startup hooks
- **Task ID**: `build-degraded-posture`
- **Depends On**: `build-compat-predicate`
- **Validates**: `tests/unit/test_llm_stack_degraded_start.py`, the completed
  contract test, the degraded-intake integration test
- **Informed By**: Failure Posture (owner decision); round-2 B-d/B-e and the
  dashboard-transport blocker
- **Assigned To**: `compat-builder`
- **Parallel**: false
- Lazily self-resolving memoized flag in `compat.py`; **the alert fires from
  the first transition to degraded inside the resolver** — Sentry fatal,
  `logger.critical` sentinel, `data/llm-stack-degraded` marker (versions +
  `exc_type`). Healthy resolution clears the marker.
- `LLMStackIncompatible(LLMCallError)`; `run_typed`/`run_typed_local` raise it
  when degraded.
- Startup calls in `bridge/telegram_bridge.py::main` and `worker/__main__.py`
  force resolution; neither exits on incompatibility.
- Exempt the sentinel from `_sentry_before_send`'s hibernation drop.
- Dashboard read side in `ui/app.py::dashboard_json`, fail-quiet like its
  siblings.
- Tests: independence (alert while `run_typed` raises), no-startup-hook path,
  Sentry-failure tolerance, marker clear leg, hibernation exemption, intake
  survives degraded.

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
- Gate phases: `llm` → subprocess `--json --allow-network` in the target venv;
  `import` → the set's own `import_names`; `pytest` unchanged. Surface the
  failed phase in the warning. Commit/push/restart ordering untouched.
- `verify.py`: the unconditional subprocess `ToolCheck` (both versions in
  `detail`).
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
  `uv sync --all-extras`, invoke the gate subprocess directly; assert non-zero
  exit with `unexpected keyword argument 'temperature'`.
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
  in the PR description, and that the PR body says `Refs #3001`, **not**
  `Closes #3001` — Work Items 2 and 3 keep the issue open.

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
| Import phase is set-derived, not hardcoded | `grep -c "import anthropic; import claude_agent_sdk" scripts/update/deps.py` | match count == 0 (the literal is replaced by `set.import_names`) |
| Gate invocation is real, not a mention | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "llm_gate" -q` | exit code 0 |
| Held set is skipped and legible | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "held_set" -q` | exit code 0 |
| Verify runs the check unconditionally | `./scripts/pytest-clean.sh tests/integration/test_remote_update.py -k "verify_runs_compat" -q` | exit code 0 |
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
| Update skill doc corrected | `grep -c "import check" .claude/skills/update/SKILL.md` | match count == 0 |
| Feature doc exists | `test -f docs/features/llm-stack-compat-gate.md` | exit code 0 |

## Critique Results

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

Awaiting round-4 critique.
