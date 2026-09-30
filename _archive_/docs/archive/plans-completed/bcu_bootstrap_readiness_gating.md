---
status: planned
type: feature
appetite: Small
owner: Valor Engels
created: 2026-07-20
tracking: https://github.com/tomcounsell/ai/issues/2130
last_comment_id: none
---

# Wire /v1/bootstrap readiness gating into valor-computer

## Problem

bcu v0.1.0 exposes `GET /v1/bootstrap`, whose `instructions.ready` field gates
whether action routes (click/type/screenshot/etc.) will succeed. When
Accessibility or Screen Recording permission is missing, bcu returns
`instructions.ready == false` plus user-facing recovery text, and action routes
fail. The v0.1.0 contract migration (#2114 / PR #2132) deliberately dropped a
`bootstrap()` wrapper because it had no consumer at the time. This follow-up adds
the wrapper, a `valor-computer bootstrap` subcommand, and skill-context guidance
so callers check readiness once per session before the first action — turning a
silent, confusing action failure into an actionable "grant permissions" message.

## Freshness Check

- `tools/computer/__init__.py` is on the v0.1.0 contract (merged PR #2132): all
  routes are POST with JSON bodies except `/health`, `/v1/bootstrap`, `/v1/routes`
  which are GET. There is currently **no** `_get` helper and no `bootstrap()`.
- `config/bcu_pin.json` pins `release_tag: v0.1.0`.
- `.claude/skill-context/computer-use.md` documents the CLI commands and error
  handling but has no readiness-gating step.
- Contract source (pinned tag v0.1.0, `Sources/BackgroundComputerUse/Contracts/BootstrapContracts.swift`):
  `BootstrapResponse` = `{ contractVersion, baseURL?, startedAt?, permissions,
  instructions, guide, routes }`; `BootstrapInstructionsDTO` = `{ ready: Bool,
  summary: String, agent: [String], user: [String] }`; `RuntimePermissionsDTO` =
  `{ accessibility: {granted, promptable}, screenRecording: {granted, promptable},
  checkedAt, checkMs }`. Upstream `runtime.md`: "Always trust the GET /v1/bootstrap
  response: `instructions.ready == true` → action routes available;
  `instructions.ready == false` → report `instructions.user` and recovery guidance."

## Prior Art

- The v0.1.0 migration plan (`docs/plans/completed/bcu_v010_contract_migration.md`)
  explicitly deferred readiness-gating via `/v1/bootstrap` `instructions.ready` to
  this issue (#2130), which this plan now resolves in full.
- Contract-level test harness already exists in
  `tools/computer/tests/test_computer_use.py` (a real loopback `http.server` fake
  bcu that records method/path/body). The fake handler already implements `do_GET`.

## Solution

**Module (`tools/computer/__init__.py`):**
- Add a `_get(path, *, timeout)` helper mirroring `_post` (same error-dict and
  `ComputerUseUnavailableError` semantics) but issuing an HTTP GET with no body.
- Add `bootstrap() -> dict` → `_get("/v1/bootstrap")`. Returns the parsed
  `BootstrapResponse` dict, or an error dict / raises `ComputerUseUnavailableError`
  exactly like the other wrappers.
- Add `is_ready(bootstrap_result: dict) -> bool` — a pure helper that returns
  `True` only when the payload has no `error` key and `instructions.ready` is
  truthy. Centralizes the gating predicate so the CLI and any future caller agree.

**CLI (`tools/computer/cli.py`):**
- Add a `bootstrap` subcommand (no positional args) that calls `bootstrap()` and
  prints the JSON payload like every other command.
- Exit-code semantics encode the gate so a script can do
  `valor-computer bootstrap && valor-computer click ...`:
  - bcu unavailable (`computer_use_unavailable`) → exit 78 (existing path).
  - other error dict → exit 1 (existing path).
  - HTTP-200 payload with `instructions.ready == false` → exit 78 (`EX_CONFIG`):
    bcu is running but not ready (permissions ungranted). This is the new gate.
  - `instructions.ready == true` → exit 0.

**Skill-context (`.claude/skill-context/computer-use.md`):**
- Add a "Readiness gating" section: run `valor-computer bootstrap` **once per
  session before the first action**; on exit 78 with `instructions.ready == false`,
  surface `instructions.user` to the user and stop rather than issuing blind
  actions.

No auto-gating is injected into every action wrapper — that would double every
request and duplicate the skill-level check. The gate is one explicit, cheap,
once-per-session call, exactly as the issue scopes it.

## Step by Step Tasks

1. Add `_get` + `bootstrap()` + `is_ready()` to `tools/computer/__init__.py`.
2. Add the `bootstrap` subcommand and readiness exit-code logic to
   `tools/computer/cli.py`.
3. Add contract + CLI tests to `tools/computer/tests/test_computer_use.py`.
4. Update `.claude/skill-context/computer-use.md` with the readiness-gating step.
5. Update `docs/features/computer-use.md` with the bootstrap command + gating flow.
6. `ruff format` + `ruff check`, run the targeted test module, commit, open PR.

## Success Criteria

- `valor-computer bootstrap` issues `GET /v1/bootstrap` (asserted against the fake
  bcu), prints the payload, and exits 0 when ready, 78 when `ready == false`, 78
  when bcu is unavailable, 1 on other errors.
- `bootstrap()` and `is_ready()` are importable and unit-tested.
- Skill-context and feature doc describe the once-per-session gate.
- macOS-only OS gate and all existing behavior unchanged.

## Failure Path Test Strategy

- `ready == false` payload → CLI exits 78 and prints the payload (test asserts exit
  code + `instructions.ready == false` in output).
- bcu unavailable (no manifest) → `bootstrap()` raises `ComputerUseUnavailableError`;
  CLI exits 78 with `computer_use_unavailable` (reuse existing assertions pattern).
- HTTP 500 from `/v1/bootstrap` → `bootstrap()` returns `http_500` error dict; CLI
  exits 1.
- `is_ready()` returns `False` for error dicts and missing/false `instructions.ready`.

## Test Impact

- [ ] `tools/computer/tests/test_computer_use.py` — UPDATE: add `bootstrap` contract
  test (GET method + `/v1/bootstrap` path + empty body), `is_ready` unit tests, and
  CLI exit-code tests (ready=0, not-ready=78, unavailable=78, http-500=1). No existing
  cases change behavior; this is purely additive.

## Rabbit Holes

- Do **not** add auto-gating inside every action wrapper (doubles requests, couples
  the module to session state). One explicit subcommand + skill-context step only.
- Do **not** parse/normalize the `guide`/`routes` sub-objects — pass the payload
  through verbatim; only `instructions.ready` drives the exit code.
- Do **not** cache bootstrap results in the module — "once per session" is the
  caller's (skill's) responsibility, not the CLI's.

## No-Gos

- No live bcu on this host (macOS opt-in, installed only on Tom's MacBook Air).
  All tests are contract-level against the loopback fake server; live verification
  deferred to the opted-in machine.
- No new dependencies.

## Update System

No update-system changes required. `config/bcu_pin.json` is unchanged (still
v0.1.0), no new config files or dependencies, and the `/update` flow is unaffected.

## Agent Integration

The `bootstrap` subcommand is reached through the existing `valor-computer` CLI
entry point (`pyproject.toml [project.scripts] → tools.computer.cli:main`), which
the agent already invokes via Bash — no new entry point needed. The skill-context
update wires the agent's behavior (check readiness once before acting). CLI dispatch
tests verify the agent-visible surface (exit codes + JSON payload).

## Documentation

- [ ] Update `docs/features/computer-use.md` with the `valor-computer bootstrap`
  command and the once-per-session readiness-gating flow.
- [ ] Update `.claude/skill-context/computer-use.md` with the readiness-gating step
  and exit-code contract.
