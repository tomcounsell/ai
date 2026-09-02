# LLM Stack Compat Gate

`anthropic` and `pydantic-ai-slim` are a coupled set. `pydantic_ai.models.anthropic` forwards a fixed keyword list into the `anthropic` client's `create` call, so moving one package without the other produces a pair where the forward no longer binds: a `TypeError` at argument binding, before any network I/O, at every non-harness LLM call on every machine that syncs the lockfile.

This feature is the check that catches that pair, plus the posture a process takes when it finds one.

Three things carry it:

- **An import-safety contract** on `agent/llm/`, so a broken stack can be *reported* instead of felling every importer.
- **A pure predicate** (`agent/llm/compat.py::check_llm_stack_compat`) run at `/update` verify time, at bridge and worker startup, and as an auto-bump gate phase.
- **A coupled-set model** in `scripts/update/deps.py`, so the auto-bump moves a set atomically or not at all.

## The Import-Safety Contract

Module scope in `agent/llm/wrapper.py`, `agent/llm/compat.py`, and `agent/anthropic_client.py` holds **stdlib and our own code only**. Every third-party LLM-stack symbol is reached through `agent/anthropic_client.py::_load_stack`, a `functools.cache`-memoized loader called only from inside function bodies:

```python
@functools.cache
def _load_stack() -> LLMStack:
    import anthropic
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    from pydantic_ai.providers.ollama import OllamaProvider
    ...
```

The direction is load-bearing. The predicate must be able to report an `ImportError`, so nothing on the predicate's own import path may raise one. Because `import agent.llm` cannot fail, `import bridge.telegram_bridge` cannot fail for stack reasons either, and every startup call site stays reachable in *every* failure class.

The loader is deliberately **whole-stack** with no per-symbol option menu: one `LLMStack` dataclass, one import block, one memo. An `anthropic` `ImportError` therefore also takes the local Ollama leg down, which is the pre-existing behaviour and a known residual, not a new fault. It is also the mechanism behind the open `openai` finding below.

Failures are not memoized (`functools.cache` caches returns, not exceptions), so a process that repairs its environment gets a fresh attempt on the next call.

`_load_stack` is re-imported into `agent/llm/wrapper.py`'s namespace, which makes `monkeypatch.setattr(wrapper_mod, "_load_stack", ...)` the network-isolation seam for tests.

**Enforcement:** `tests/unit/test_llm_import_safety.py`. It builds a shim directory whose `anthropic` and `pydantic_ai` modules raise on import, puts it ahead of the real stack on `sys.path`, and asserts out-of-process that `import agent.llm`, `import bridge.telegram_bridge`, and `import agent.anthropic_client` all still succeed while `_load_stack()` still raises. One test file owns the contract; no consumer is touched and no consumer can regress it.

## The Predicate

`check_llm_stack_compat(allow_network: bool = False) -> CompatResult`

### Two independent axes

`CompatResult` carries `loader_ok` and `compatible` separately:

| Axis | Question | Gates |
|---|---|---|
| `loader_ok` | Does the third-party stack import at all? | `run_typed` **and** `run_typed_local` |
| `compatible` | Does the installed `anthropic`'s `create` accept everything the installed `pydantic_ai` forwards? | `run_typed` only |

They stay separate all the way to the call sites through `stack_axes()`. `run_typed_local` is the granite-on-Ollama leg and never touches `anthropic`, so an Anthropic *signature* break must not fall the two hot-path classifiers (intake intent in `tools/classifier.py`, Job bind-or-mint in `bridge/job_router.py`) back to their conservative defaults fleet-wide.

### The forwarded-kwarg set comes from the call site

Not from `AnthropicModelSettings.__annotations__`. That TypedDict is a declared superset of provider-agnostic and `anthropic_`-namespaced settings the model translates, renames into headers, or drops: on the pinned, verified-working pair it has 31 keys of which 20 appear in no `create` signature. Deriving from it would report `compatible=False` on a healthy fleet.

So the predicate does `ast.parse(inspect.getsource(pydantic_ai.models.anthropic))`, collects every `.create(...)` call on a dotted path rooted at `self.client`, and reads the literal keyword names off that call.

The target resolves against `anthropic.AsyncAnthropic`, constructed constructor-only with `api_key="x"` — no network, no real key. The call site names only the attribute *path* (`self.client.beta.messages` on the pinned pair), never the client class, and `pydantic_ai` types `self.client` as a union that also admits `AsyncAnthropicBedrock`, `AsyncAnthropicBedrockMantle`, and `AsyncAnthropicVertex`, whose `create` signatures differ. `AsyncAnthropic` is correct because that is the class `agent/llm/wrapper.py` constructs and this repo uses none of the others. Anyone debugging a Bedrock-shaped false positive starts there.

Hardcoding `anthropic.resources.messages.AsyncMessages` is wrong in the other direction: the non-beta resource lacks `betas`, `context_management`, `mcp_servers`, and `speed`, four kwargs the call site does pass. The call site names its own target; read the target from the call site.

### Fail-closed, five ways

A silently-passing predicate whose target moved is the same failure as no gate. Every introspection failure returns `compatible=False` with the reason verbatim:

1. `getsource` unavailable for the module defining `AnthropicModel`.
2. **Zero** `self.client.*.create` sites found.
3. **More than one** such site.
4. The call site's attribute path unresolvable on `AsyncAnthropic`.
5. The single site forwards **no literal keywords** (splat-only).

Case 5 is not covered by the count gate and is the one worth stating out loud: on a site refactored to `create(**kwargs)` the count is still 1, the path still resolves, `forwarded` is empty, and the subset test would be vacuously true. A `SyntaxError` from `ast.parse` is caught on the same terms.

`compatible=True` also survives a `create` that declares `**kwargs`: a `VAR_KEYWORD` parameter means nothing can be missing.

### Purity

`check_llm_stack_compat` returns a `CompatResult` and does **nothing else**. It never touches the memoized degraded flag, never calls `capture_message`, never writes or clears a marker file. Only `_resolve_degraded_flag()` alerts.

This matters because the auto-bump `llm` gate deliberately runs the predicate against a stack it is *about to roll back*. An impure predicate would fire a fatal Sentry capture and strand a red dashboard marker on every **successful** rollback.

### The CLI

```bash
python -m agent.llm.compat --json                  # verdict as JSON, exit 0/1
python -m agent.llm.compat --json --allow-network  # + one minimal billed run_typed call
python -m agent.llm.compat                         # human-readable one-liner
```

A tooling entry point for subprocess callers, not an agent surface. It calls only the pure predicate.

`--allow-network` makes one real `run_typed` call against a single-field probe model, catching transport-class breaks (a moved HTTP layer, a changed auth header) that no signature comparison can see. It is billed, so only the auto-bump `llm` gate turns it on, and only on cycles where something actually bumped.

### The four call sites

| Site | How | When |
|---|---|---|
| `scripts/update/verify.py::check_llm_stack_compat` | subprocess `{venv}/bin/python -m agent.llm.compat --json`, mapped to a `ToolCheck` appended to `result.valor_tools` | every `/update` and `/update --cron`, unconditionally |
| `bridge/telegram_bridge.py::main` | in-process `_resolve_degraded_flag("bridge")` | bridge startup |
| `worker/__main__.py` | in-process `_resolve_degraded_flag("worker")` | worker startup |
| `scripts/update/deps.py` `llm` gate phase | subprocess via `llm_gate_argv(venv_python)`, with `--allow-network` | after a coupled set's pins are rewritten and synced |

The `ToolCheck`'s failure reason goes in `.error`, not `.detail`. `run.py`'s generic `valor_tools` loop is `if not tool.available and tool.error:` — with `error` unset, an incompatible stack would produce no log line, no `result.warnings` entry, and nothing for `bridge/update.py::extract_update_warnings` to surface. `detail` is not read by that loop at all. `run.py` separately prints the check's `detail` on pass, by name lookup in `result.verification.valor_tools`.

The update-script call sites are subprocess-only. `verify.py` and `deps.py` never import `agent.llm.compat` in-process, so no runtime→scripts or scripts→runtime import edge exists in either direction. The subprocess must be the **target** venv's interpreter: the update process's own interpreter imported its modules before the sync and would report on a stack that no longer exists on disk.

`llm_gate_argv` is the single construction of the `llm` phase argv, shared by the phase runner and by the manual rollback verification, so a hand-run invocation cannot drift from the production one while still appearing to prove the gate works.

## Degraded Posture: Start Degraded, Alert Loudly

An incompatible stack at bridge or worker startup **does not exit the process.**

- The process comes up. Telegram intake continues: messages are received, AgentSessions are enqueued to Redis, nothing is dropped.
- A degraded flag is set. `run_typed` and `run_typed_local` fail fast with `LLMStackIncompatible`.
- The condition is alarmed on the first transition, from inside the resolver.

Degraded-but-running is precisely the state that hid the 2026-08-24 incident for six hours. Exiting would trade a silent LLM outage for an immediate total outage plus a launchd crash-loop, which is worse. So **the alert is the entire safety property.** If the alert does not fire, this feature has shipped nothing.

### `LLMStackIncompatible`

```python
class LLMStackIncompatible(LLMCallError):
```

A subclass of `LLMCallError` on purpose: every existing `except LLMCallError` fail-safe keeps working with no edit, so a degraded stack degrades each call site to its own conservative default (routing → respond, email triage → escalate, memory extraction → skip, intake → default classification, router → NEW Job) instead of surfacing a raw provider `TypeError` from deep inside `pydantic_ai`.

`agent/llm/wrapper.py::_guard_stack(caller, *, signature_axis)` raises it. `run_typed` passes `signature_axis=True`; `run_typed_local` passes `False`.

### The alert is bound to flag resolution, not to startup

`_resolve_degraded_flag(proc=None)` is lazily self-resolving and memoized. The first read — from a startup hook, or from a `run_typed` call in a process that never ran one — evaluates the predicate, and the first transition to degraded emits the alert from inside the resolver. Subsequent reads are memo hits and alert nothing.

The startup hooks in `bridge/telegram_bridge.py::main` and `worker/__main__.py` do nothing but force resolution early, so the alert fires at boot rather than at first call. No entry path can reach the broken stack without alarming, and there is no ordering race between boot and first call to lose.

### Alert independence, and the named drafter exception

Because the thing being alarmed **is** the LLM stack, the alert must not route through it. Forbidden in the alert path:

- No `run_typed` / `run_typed_local`. They are exactly what is broken.
- No message drafter, no LLM summarization, no persona pass.
- No dynamic body composition. The body is a **static string** plus the two resolved version numbers, the failed axis, and the captured exception type and message.

**This is a deliberate, named exception to the standing "never let raw text speak to chat" convention.** The drafter is unavailable by construction — it is an LLM call, and the LLM stack is the thing that is broken — and a silent alert is the exact failure being prevented. Do not "fix" this by routing the degraded alert through the drafter, adding a persona pass, or composing the body from anything but static text plus resolved facts. Doing so re-creates the six-hour silent outage.

### Three channels

Fired unconditionally on the first transition to degraded, in `agent/llm/compat.py::_alert_degraded`. "Unmissable" is a property of redundancy across independent transports, so each later channel is independently guarded and a failure in one suppresses none of the others.

| Channel | Mechanism | Why it fires |
|---|---|---|
| Logs | `logger.critical` carrying the `LLM_STACK_COMPAT` sentinel | stdlib only, first in order, the signal of record. Survives a Sentry DSN outage. A single `grep LLM_STACK_COMPAT` over the logs finds this and both break-glass paths. |
| Sentry | `sentry_sdk.capture_message(body, level="fatal")`, with `agent/index_drift.py`'s capture-failed fallback log | `sentry-sdk`'s own HTTP transport shares no code with the LLM stack. |
| Dashboard | a per-process marker file `data/llm-stack-degraded.{proc}`, globbed by `ui/app.py::_get_llm_stack_health` into `/dashboard.json` | `/dashboard.json` is served by a **separate uvicorn process**; an in-process flag can never reach it. Every existing health field derives from a filesystem or Redis artifact, and this follows that pattern. |

**Hibernation exemption (Sentry).** `bridge/telegram_bridge.py::_sentry_before_send` drops every event while `is_hibernating()` — a persistent flag, not a brief window. It passes any event whose message carries the `LLM_STACK_COMPAT` sentinel. Hibernation means "we cannot reach Telegram", which is exactly when a broken LLM stack most needs to be visible somewhere else.

**A direct Telegram push was rejected.** No raw Bot-API send path exists in tracked code, and Telethon's send lives only in the bridge, so it could not alarm a degraded worker at all.

### The marker is per-process, and both directions of stuck are fatal

`_resolve_degraded_flag` writes a marker **only when `proc` is given**. `bridge/telegram_bridge.py::main` passes `"bridge"`, `worker/__main__.py` passes `"worker"`, and every other caller — one-shot scripts, cron helpers, pytest processes that touch `run_typed` — passes nothing and writes no marker, while still getting the Sentry capture and the sentinel log.

The rule is: **only a process that can clear its marker may write one.**

The clear leg is what makes this necessary, in both polarities:

- **Stuck red equals no dashboard channel.** A board that stays red after the fix lands is a board nobody reads. A pid-suffixed scheme has no clear leg for a process that exits while degraded (the clear only ever runs on a *healthy* resolution in the same process), and the read side has no liveness filter, so every one-shot on a degraded machine deposits another permanent red and the board stays red on the corpses. Two clearable writers is the whole marker population.
- **Stuck green equals no dashboard channel, and worse.** A single shared marker has no writer ownership. After a pin fix lands, the bridge takes the graceful restart (`agent/agent_session_queue.py::_check_restart_flag`), re-resolves healthy, and clears — while the worker, whose restart `_check_restart_flag` defers whenever jobs are running, still holds its memoized degraded flag and keeps raising `LLMStackIncompatible`. A shared marker would render green against a still-broken worker. Nobody investigates a green board.

So each resolver owns exactly one path and clears **only** its own, via `marker.unlink(missing_ok=True)` on the process's own filename. Never a glob, never another process's file. The read side globs and is red while **any** marker survives, naming every degraded process in `llm_stack_degraded_processes` and the marker payloads in `llm_stack_degraded_detail`.

Both write and clear fail quiet on `OSError` and log: losing the dashboard channel must not take the process with it. `ui/app.py`'s reader is fail-quiet on the same terms — an unreadable marker still names its process, and a health payload must never 500.

The marker directory is a module-level seam, `_MARKER_DIR = Path(__file__).resolve().parents[2] / "data"`, matching `ui/app.py`'s `Path(__file__).parent.parent / "data"` so both sides resolve to the same `<repo>/data` and neither is redirectable by cwd. Tests redirect it through an autouse fixture in `tests/unit/conftest.py` rather than writing into the live `data/` a running bridge, worker, and dashboard share.

## Break-Glass Environment Variables

Two, both read lazily inside function bodies (a module-scope `os.environ` read is blocked by `.claude/hooks/validators/validate_no_module_scope_env.py`), both announcing themselves at `logger.warning` with the `LLM_STACK_COMPAT` sentinel when active. Neither is a credential and neither is declared in `.env.example`.

| Variable | Read in | Effect |
|---|---|---|
| `LLM_STACK_COMPAT_OVERRIDE=healthy` | `_resolve_degraded_flag()` | Short-circuits this process's flag to not-degraded and clears this process's marker. Operator break-glass. |
| `LLM_STACK_MARKER_DIR` | `_marker_path()` | Relocates the marker directory. Exists so the subprocess CLI test can reach its child through the environment. |

**Neither is honoured by the pure predicate, by the `--json` CLI, or by the auto-bump gate.** An override must never let a bad pin ship. `check_llm_stack_compat` does not read either one, and `agent/llm/compat.py::main` calls only the predicate.

`LLM_STACK_COMPAT_OVERRIDE` clears the marker on its way out because the short-circuit happens *before* the predicate: without the clear, no future resolution could ever reach the healthy branch, and the board would stay red forever on a machine the operator declared healthy.

`LLM_STACK_MARKER_DIR` announces itself for the mirror-image reason. A stale value inherited from a launchd plist, an exported shell var, or a cron env would relocate the write path of the only *standing* degraded signal and leave the board green on a degraded bridge with nothing saying why. The warning latches once per process rather than once per marker touch.

## Coupled Sets in the Auto-Bump

`scripts/update/deps.py` declares `AUTO_BUMP_SETS: list[CoupledSet]`. A set is the atomic unit of every stage of a bump: resolve, rewrite, sync, gate, rollback.

```python
@dataclass(frozen=True)
class CoupledSet:
    members: Sequence[str]
    import_names: tuple[str, ...]
    reason: str
    gates: tuple[str, ...] = ("import", "pytest")
    hold: str | None = None
```

- `reason` is prose, on every set, saying why these packages move together.
- `import_names` are the set's own importable module names, so the `import` phase always probes what the set actually moved instead of a hardcoded package list.
- `gates` defaults to `("import", "pytest")`, so a newly declared set never silently inherits the billed `llm` phase. A set that needs a real API call asks for it by name.
- `hold` parks a set in the declaration without executing it: the bump is skipped and `held: <reason>` is recorded.

### The declared sets

| Set | Members | Gates | State |
|---|---|---|---|
| LLM stack | `anthropic`, `pydantic-ai-slim` | `llm`, `import`, `pytest` | **Held on `#3001 Step 2`** |
| Harness transport | `claude-agent-sdk` | `import`, `pytest` | Active |

The LLM set carries the `llm` phase because an import check cannot see its failure mode: `import anthropic` succeeds fine on a version whose call signature we cannot satisfy.

The version boundary is `anthropic>=1.0.0` requires `pydantic-ai-slim>=2.33.0`, recorded in the two-line comment above the `anthropic` pin in `pyproject.toml`. Current pins are `anthropic==0.125.0` and `pydantic-ai-slim[anthropic]==2.9.0`.

**Who removes the hold:** a human. `hold` exists so the coupled-set structure can land inert while the pin decision stays with a person. Removing it means editing `AUTO_BUMP_SETS` in `scripts/update/deps.py` and letting the next maintainer-machine `/update --cron` resolve, sync, and gate the pair — which today would roll back. See the open finding below.

### `openai` is in no set, and an assertion enforces that

There is no packaging coupling: `pydantic-ai-slim`'s locked dependencies contain no `openai` (it appears only under the `[openai]` extra, which this repo does not install). `openai` is also declared as a floor, `openai>=1.0.0`, not an exact pin, so `bump_pin_in_pyproject` would refuse to rewrite it rather than invent a pin the maintainer never chose. A module-scope assertion in `deps.py` fails the import if `openai` ever appears as a set member.

### The gate phases

| Phase | Command | Subprocess timeout |
|---|---|---|
| `llm` | `{venv}/bin/python -m agent.llm.compat --json --allow-network` | 120s |
| `import` | `{venv}/bin/python -c "import <each import_name>"` | 30s |
| `pytest` | `{venv}/bin/python -m pytest tests/unit/test_docs_auditor_substrate.py -x -q` | 60s |

`run_gate_phases` stops at the first failing phase and names it, so `run.py`'s rolled-back warning distinguishes an incompatible LLM pair from a flaky unrelated unit test. Fail-closed throughout: a phase that cannot run at all (no venv, timeout, `OSError`) is a **failed** phase, never a skipped one.

### Atomicity and rollback

Per set, in order: skip if held → resolve latest for **every** member (one unresolvable member skips the whole set) → skip if nothing actually changed (a quiet cycle must not re-resolve the lockfile) → snapshot `pyproject.toml` → rewrite all member pins → `uv sync` unfrozen → run the gates. Any failure restores that set's **own** snapshot and re-syncs, then moves to the next set.

The snapshot is per-set, taken immediately before that set's rewrite. A whole-file snapshot taken once before the loop would revert every other set's good bump on one bad set.

A member is reported `bumped=True` only once its whole set has survived every gate, so `run.py`'s commit list never names a pin that was rolled back underneath it.

If the rollback's own re-sync fails, `AutoBumpResult.restore_failed` is set and `uv.lock` is `git checkout`-ed back. The environment is not in its pre-bump state, so `run.py` must not commit anything that run — a later successful bump would otherwise push a poisoned lockfile fleet-wide.

### What the gate checks, and what it cannot

**Checks:** that the installed `anthropic` accepts every literal keyword the installed `pydantic_ai` forwards at its single `create` site; that the set's own modules import; that one fast test file passes; and, with `--allow-network`, that one real end-to-end typed call completes.

**Cannot check:**

- **A pin that is committed by hand.** The predicate reports on the *installed* stack. At push time a developer's venv reflects the last `uv sync`, not the pin being pushed, so a pre-push leg would introspect the good, still-installed pair and add a false all-clear on top of a bad pin. Making it sound needs a push-time `uv sync`. The accepted residual is that a hand-staged pin is caught at the next run boundary — the next `/update` verify or the next service start — after `origin/main` already carries it. No follower can *boot* on it silently, which is the property that matters. A declaration-level diff check against the `anthropic>=1.0.0 → pydantic-ai-slim>=2.33.0` boundary, needing no venv, is the named follow-up candidate.
- **Semantic breakage behind an unchanged signature.** A kwarg that keeps its name and changes its meaning passes the signature check. The `llm` phase's real call narrows this but does not close it.
- **Anything outside the declared sets.** The gate probes `import_names`; a transitive dependency that moves under a set is covered only insofar as the gates exercise it.

## `/update --cron` Wall-Clock Delta

Measured on the development machine.

**Every run, bumping or not:** the verify-time compat check adds one subprocess, ~4s cold (interpreter start, stack import, one `ast.parse` of `pydantic_ai.models.anthropic`). It is unconditional and covers the hand-staged and follower routes, which previously had no check at all.

**A quiet cycle** (nothing moved on PyPI) adds nothing else: no rewrite, no sync, no gate. A no-change `uv sync --all-extras` is ~0.05s, but the loop does not even reach it.

**Per-set syncing** is where the structural delta lives: each moving set gets its own unfrozen `uv sync`, so N moving sets cost N resolve-and-install passes instead of one batched pass. A rolled-back set costs a second sync for its restore.

**Today the delta is zero.** The LLM set is held, so it never rewrites and never syncs, and at most one set (`claude-agent-sdk`) can move on any given cycle — exactly one sync, as before.

**With the hold lifted and both sets moving on the same cycle**, the added cost is one extra `uv sync` pass plus the LLM set's own gates. Gate time is bounded by the subprocess timeouts at 120 + 30 + 60 = 210s worst case; typical is ~35s (`llm` a few seconds plus one billed Haiku call, `import` about a second, `pytest` ~26s measured for the 196-test gate file). The sync term is network- and wheel-install-dominated and is the variable part. Worst case with a rollback: two syncs plus the gates for that set.

This is bounded work on the maintainer machine only, on cycles where a pin actually moved. Followers never auto-bump and pay only the ~4s verify check.

## Open: the Held LLM Set Cannot Currently Pass Its Own Gate

**This is issue #3073's problem, not this feature's.** Recorded here because anyone who lifts the hold will hit it immediately.

With `openai` floored at `>=1.0.0` in `pyproject.toml` and pinned by `uv.lock` at **2.30.0**, a real bump of the LLM set to `anthropic` 1.3.0 + `pydantic-ai-slim` 2.37.0 **rolls back**. Verified live during this lane's rollback verification.

The chain:

1. `pydantic-ai-slim` ≥2.34 requires `openai` ≥3.x for `pydantic_ai.models.openai`.
2. `_load_stack` is deliberately whole-stack and imports `from pydantic_ai.models.openai import OpenAIChatModel` unconditionally, because `run_typed_local` needs `OpenAIChatModel` for the Ollama leg.
3. `uv sync` leaves `openai` at 2.30.0 (its floor permits it and no set moves it).
4. The `llm` gate phase returns `loader_ok=false`, reason ``"Please install `openai` to use the OpenAI model"``, and the set is correctly rolled back.

The gate is behaving exactly as designed: it caught a real, un-runnable stack before it shipped.

**Step 2 therefore requires moving `openai` in the same operation.** Do not read this as an argument for relaxing the whole-stack loader or for a per-symbol import menu; the whole-stack loader is what makes the failure loud instead of latent in `run_typed_local`.

For the record on the spike that scoped this: spike-5's finding that there is **no packaging coupling** between `openai` and the anthropic stack remains correct — `pydantic-ai-slim`'s locked dependencies still do not list `openai`. Its conclusion that "the fix is at the import, not by widening the set" does **not** survive contact with the whole-stack loader: the loader makes `openai` a runtime prerequisite of `pydantic_ai` at the version the LLM set is trying to reach, regardless of what the metadata declares. Widening the operation, not the packaging claim, is what Step 2 needs.

Do not change the `openai` guard in `deps.py` on the basis of this note. Removing the assertion would let an auto-bump try to rewrite a floor declaration, which `bump_pin_in_pyproject` refuses by design.

## Files

| Path | Role |
|---|---|
| `agent/llm/compat.py` | predicate, degraded flag, alert emitter, marker, `--json` CLI |
| `agent/anthropic_client.py` | `_load_stack`, the one memoized whole-stack loader |
| `agent/llm/wrapper.py` | `LLMStackIncompatible`, `_guard_stack`, `run_typed`, `run_typed_local` |
| `bridge/telegram_bridge.py` | startup resolution, `_sentry_before_send` sentinel exemption |
| `worker/__main__.py` | startup resolution |
| `ui/app.py` | `_get_llm_stack_health`, the marker glob into `/dashboard.json` |
| `scripts/update/deps.py` | `CoupledSet`, `AUTO_BUMP_SETS`, gate phases, per-set rollback |
| `scripts/update/verify.py` | `check_llm_stack_compat` → `ToolCheck` |
| `tests/unit/test_llm_import_safety.py` | the import-safety contract's single enforcement test |
| `tests/unit/test_llm_stack_compat.py` | predicate, fail-closed cases, CLI |
| `tests/unit/test_llm_stack_degraded_start.py` | degraded start, three channels, clear leg |
| `tests/unit/test_dashboard_llm_degraded.py` | dashboard read side |

## See Also

- [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md) — what `run_typed` / `run_typed_local` are and every call site that uses them.
- [Remote Update](remote-update.md) — the `/update` orchestrator this gate runs inside.
- [/update Warning Channel](update-warning-channel.md) — how a failed `ToolCheck` reaches chat.
- [Config Timeout Catalog](config-timeout-catalog.md) — `TIMEOUTS__*` fields, including `local_typed_hard_s`.
