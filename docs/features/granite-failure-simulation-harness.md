# Granite Failure-Simulation Test Harness

Turns each silent granite production failure into a reproducible local test,
exercisable for free and at volume. Test-only: nothing under
`agent/granite_container/` changes. Tracking issue: #1837.

## Why

Granite (the production session runner) drives the real Claude Code TUI over
PTYs and detects turn boundaries by screen-scraping the painted terminal for
quiescence (`pty_driver.read_until_idle`). That scraping is coupled to specific
Claude Code UI — the `bypass permissions` bottom bar, the trust-folder dialog,
the `/login` screen — that Anthropic ships and revises. When the UI changes the
idle heuristic breaks and sessions **wedge silently**. Those failures were
neither reproducible locally nor covered by deterministic tests, and the only
real-loop test burned the Anthropic backend so it could not run at volume.

## Two substrates

Both live behind a shared support package, `tests/granite_faults/`.

### Substrate A — deterministic fault injection (always-on, no model)

`tests/granite_faults/scenarios.py` generalizes the piecemeal container-loop
mocks into named injectors, one per failure class, each targeting a real
granite seam and asserting the recovery/detection path fires. Assertions live
in `tests/unit/granite_container/test_fault_injection.py`. No ollama, no model,
no network, no real `claude` spawn — sub-second, in the default unit suite.

| Class | Seam | What it proves |
|-------|------|----------------|
| 1 turn-detection wedge | `read_until_idle` | idle bar removed/renamed → `saw_idle=False`, bounded wait |
| 2 startup/`/login` wedge | `parse_startup_frame` | drifted login wording → `UNKNOWN` (the `startup_unresolved` path) |
| 3 process hang / U-state | `read_until_idle` | silent hung child → deadline-bounded read, no unbounded block |
| 4 loop / non-convergence | `container.run` | always-`[/dev]` PM → max-turns + wrap-up guard deliver a user-facing message |
| 5 crash | `container.run` | corrupt-transcript read → fail-loud `exception` exit, not silent |
| 6 silent no-progress tail | `read_until_idle` | progress-then-quiet → silence observable via `IdleResult(saw_idle=False, elapsed_ms>N)` (detector wiring is out of scope, #1688) |

Each injector was demonstrated **red-first** (the recovery path temporarily
broken, the test observed to fail) before being asserted green — see the PR
description for the captured failing output.

### Substrate B — ollama-backed real Claude Code E2E (free, high-fidelity)

`tests/integration/test_granite_ollama_e2e.py` launches the **real** `claude`
binary against ollama's Anthropic-compatible endpoint and asserts a session
reaches a clean reply without wedging. It doubles as a canary for new `claude`
binary releases — the exact thing that breaks production granite.

It lives under `tests/integration/` (not the unit dir, so the
`tests/unit/granite_container/conftest.py` autouse spawn-guard does not cover
it) and self-gates inside its own module.

## The `GRANITE_OLLAMA_SMOKE` gate

Substrate B and the nightly canary self-skip unless **both**:

- `GRANITE_OLLAMA_SMOKE=1` is set, **and**
- ollama is reachable — a tool-capable model answers a `claude --print` ping
  over the OAuth-stripped env (`ollama_env.ollama_substrate_reachable`, mirrors
  `test_granite_container_loop._model_reachable`).

The reachability probe runs only when the smoke flag is set, so normal
collection stays fast. Run it directly:

```bash
GRANITE_OLLAMA_SMOKE=1 pytest tests/integration/test_granite_ollama_e2e.py -v
```

The `TestOllamaEnvContract` assertions (the OAuth-strip no-leak contract) are
**always-on** — they need no ollama and run in every integration pass.

## The ollama env-var inversion + OAuth strip

Per the ollama integration docs, pointing the real `claude` binary at ollama
means setting three env vars — the inversion of what production granite does:

```
ANTHROPIC_BASE_URL=http://localhost:11434
ANTHROPIC_AUTH_TOKEN=ollama
ANTHROPIC_API_KEY=""
```

Production `pty_driver._build_env` blanks all three `ANTHROPIC_*` vars to force
the Max-subscription OAuth endpoint, and **conditionally forwards**
`CLAUDE_CODE_OAUTH_TOKEN` when present. `PTYDriver.spawn` overlays the
per-session env with `env.update()`, which only adds/overwrites keys — it never
removes one. So overlaying just the three ollama vars is **not** a clean
inversion: a forwarded OAuth token survives, and on a machine already logged in
for production granite that reproduces the documented PR #1612 failure
("issue with the selected model": OAuth login present **and** `ANTHROPIC_BASE_URL`
pointed at ollama at once), silently invalidating the canary.

The fix lives in one place, `tests/granite_faults/ollama_env.py`:

- `build_ollama_child_env()` sets the three ollama vars **and**
  `env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)`.
- `assert_no_oauth_leak(env)` raises if a token survives. Both Substrate B and
  the golden-recorder call it on the assembled child env **immediately before
  `spawn()`**. The assertion is also covered deterministically in
  `tests/unit/granite_container/test_ollama_env.py`.

Only tool-capable models work: the `claude` binary always sends tool
definitions, so `gemma*` chat tags return HTTP 400 "does not support tools".
`pick_ollama_model()` drops those and prefers a qwen coding tag.

## Golden-recorder + re-record step

`tests/granite_faults/recorder.py` runs one real ollama-backed `claude` session
(same OAuth-stripped, no-leak env as Substrate B) in a scratch cwd and captures:

- `fixtures/recorded_session.frames` — the raw PTY frame stream the TUI paints.
- `fixtures/recorded_meta.json` — model tag, session id, elapsed, whether the
  idle bar and reply glyph were seen, and any hook-event paints.
- `fixtures/recorded_transcript.jsonl` — the JSONL transcript, when Claude Code
  persists a findable per-session-id file (the interactive TUI + SIGINT
  teardown often leaves none; the frame stream is the golden artifact).

The recorded frame fixture is consumable by the same Substrate A
replay-and-mutate path as the hand-authored seeds — proven by
`TestRecordedFixtureIsConsumable` in `test_fault_injection.py`.

Committed fixtures are the plan decision (reproducible, reviewable in PRs).
Re-record after a `claude` release:

```bash
python -m tests.granite_faults.recorder --model qwen3.6:35b-a3b-coding-nvfp4
```

Substrate A asserts against the *mutation*, never exact bytes, so cosmetic
drift (spacing reflow) does not break the tests — only a real seam regression
does.

## Nightly wiring + version canary

`scripts/nightly_regression_tests.py` runs the ollama-backed suite as an
isolated subprocess after the granite real-loop test:

- `ollama_reachable_for_nightly()` — the monkeypatchable reachability seam.
  When it returns `False`, `run_ollama_suite()` **self-skips with a logged
  reason and spawns no subprocess** (verified by
  `TestRunOllamaSuiteSelfSkip` — a monkeypatch to `False`, not `--dry-run`,
  which only suppresses Telegram and still runs full subprocesses).
- `PINNED_CLAUDE_VERSION` — the version the harness was validated against.
  `claude_canary_alert()` surfaces a Telegram alert on drift so the harness gets
  re-validated against the new release. Bump the constant after re-validation.

Placement is skills/dev-machine only; bridge machines do not spend cycles on it,
which keeps the `/update` change to zero (mirrors `install_nightly_tests.sh`).

## Dialog-fidelity check (Success Criterion 5)

Running Substrate B / the recorder on this machine captured the interactive TUI
under ollama. Observed result: **the startup dialogs render identically to the
Anthropic-backed binary.** The captured frames show the trust-folder dialog
("Quick safety check: Is this a project you created or one you trust?" with
`❯ 1. Yes, I trust this folder / 2. No, exit`), the `Claude Code v2.1.197`
welcome box, and the `⏵⏵ bypass permissions on` bottom bar — all byte-for-byte
the same as production, because those dialogs are **client-rendered by the
`claude` binary before any model call**, so the backend endpoint (ollama vs
Anthropic) does not affect them. The only divergence is model-response *content*
(reasoning verbosity, latency), which the harness explicitly does not assert on
(Substrate A asserts against recorded-frame mutations; Substrate B asserts
behavioral completion, not pixels). This confirms Substrate B is a faithful
canary for the startup-dialog scrape that breaks production granite.

## Files

| Path | Role |
|------|------|
| `tests/granite_faults/scenarios.py` | Substrate A injectors + scenario registry |
| `tests/granite_faults/mocks.py` | shared mock-driver builders (extracted from `test_container.py`) |
| `tests/granite_faults/ollama_env.py` | ollama env construction + OAuth-strip no-leak guard + model picker |
| `tests/granite_faults/recorder.py` | golden-recorder (real ollama session → committed fixtures) |
| `tests/granite_faults/fixtures/` | committed frame/meta fixtures (seed + recorded) |
| `tests/unit/granite_container/test_fault_injection.py` | Substrate A assertions |
| `tests/unit/granite_container/test_ollama_env.py` | deterministic env-contract + no-leak tests |
| `tests/integration/test_granite_ollama_e2e.py` | Substrate B ollama-backed E2E |
| `scripts/nightly_regression_tests.py` | nightly ollama suite + version canary |

## See also

- [Granite PTY Container: Production Path](granite-pty-production.md) — the
  system this harness exercises.
